import requests
import json
import subprocess
import os
import argparse
from datetime import datetime, timezone

DB_FILE = "challenges.json"
RUST_SOLVER_PATH = (
    "../rust_solver/target/release/ashmaize-solver"  # Assuming it's built
)


def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=4)


def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(
            f"Warning: Could not decode JSON from {DB_FILE}. Starting with an empty DB."
        )
        return {}


def init_db(json_files):
    print("Updating database from registration receipts...")
    db = load_db()
    for file_path in json_files:
        if not os.path.exists(file_path):
            print(f"File not found: {file_path}")
            continue
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
                address = data.get("registration_receipt", {}).get("walletAddress")
                if address:
                    if address not in db:
                        db[address] = {
                            "registration_receipt": data.get("registration_receipt"),
                            "challenge_queue": data.get("challenge_queue", []),
                        }
                        print(f"Added new address: {address}")
                    else:
                        print(f"Updating existing address: {address}")
                        existing_queue = db[address].get("challenge_queue", [])
                        existing_ids = {c["challengeId"] for c in existing_queue}
                        new_challenges_count = 0
                        for challenge in data.get("challenge_queue", []):
                            if challenge["challengeId"] not in existing_ids:
                                existing_queue.append(challenge)
                                new_challenges_count += 1

                        if new_challenges_count > 0:
                            existing_queue.sort(key=lambda c: c["challengeId"])
                            db[address]["challenge_queue"] = existing_queue
                            print(f"  Added {new_challenges_count} new challenges.")
                        else:
                            print("  No new challenges to add.")
                else:
                    print(f"Could not find address in {file_path}")
        except json.JSONDecodeError:
            print(f"Error decoding JSON from {file_path}. Skipping.")
        except Exception as e:
            print(f"An unexpected error occurred with {file_path}: {e}")

    save_db(db)
    print("Database update complete.")


def fetch_challenges(addresses):
    print("Fetching challenges...")
    db = load_db()
    for address in addresses:
        try:
            response = requests.get("https://sm.midnight.gd/api/challenge")
            response.raise_for_status()
            challenge_data = response.json()["challenge"]

            new_challenge = {
                "challengeId": challenge_data["challenge_id"],
                "challengeNumber": challenge_data["challenge_number"],
                "campaignDay": challenge_data["day"],
                "difficulty": challenge_data["difficulty"],
                "status": "available",
                "noPreMine": challenge_data["no_pre_mine"],
                "noPreMineHour": challenge_data["no_pre_mine_hour"],
                "latestSubmission": challenge_data["latest_submission"],
                "availableAt": challenge_data["issued_at"],
            }

            if address in db:
                challenge_queue = db[address].get("challenge_queue", [])
                # Check if challenge already exists for this address
                if not any(
                    c["challengeId"] == new_challenge["challengeId"]
                    for c in challenge_queue
                ):
                    challenge_queue.append(new_challenge)
                    challenge_queue.sort(key=lambda c: c["challengeId"])
                    db[address]["challenge_queue"] = challenge_queue
                    print(
                        f"New challenge fetched for {address}: {new_challenge['challengeId']}"
                    )
                else:
                    print(
                        f"Challenge {new_challenge['challengeId']} already exists for {address}"
                    )
            else:
                print(f"Address {address} not in database, skipping.")

        except requests.exceptions.RequestException as e:
            print(f"Error fetching challenge for {address}: {e}")
    save_db(db)


def solve_challenges():
    print("Solving challenges...")
    db = load_db()
    now = datetime.now(timezone.utc)
    for address, data in db.items():
        challenges = data.get("challenge_queue", [])
        for challenge in challenges:
            if challenge["status"] == "available":
                latest_submission = datetime.fromisoformat(
                    challenge["latestSubmission"].replace("Z", "+00:00")
                )
                if now > latest_submission:
                    challenge["status"] = "expired"
                    print(
                        f"Challenge {challenge['challengeId']} for {address} has expired."
                    )
                    continue

                print(
                    f"Attempting to solve challenge {challenge['challengeId']} for {address}"
                )
                try:
                    command = [
                        RUST_SOLVER_PATH,
                        "--address",
                        address,
                        "--challenge-id",
                        challenge["challengeId"],
                        "--difficulty",
                        challenge["difficulty"],
                        "--no-pre-mine",
                        challenge["noPreMine"],
                        "--latest-submission",
                        challenge["latestSubmission"],
                        "--no-pre-mine-hour",
                        challenge["noPreMineHour"],
                    ]
                    result = subprocess.run(
                        command, capture_output=True, text=True, check=True
                    )
                    nonce = result.stdout.strip()
                    print(f"Found nonce: {nonce}")

                    # Submit solution
                    submit_url = f"https://sm.midnight.gd/api/solution/{address}/{challenge['challengeId']}/{nonce}"
                    submit_response = requests.post(submit_url, data={})
                    submit_response.raise_for_status()
                    print(
                        f"Solution submitted successfully for {challenge['challengeId']}"
                    )
                    challenge["status"] = "solved"
                    challenge["solvedAt"] = (
                        datetime.now(timezone.utc)
                        .isoformat(timespec="milliseconds")
                        .replace("+00:00", "Z")
                    )
                    challenge["salt"] = nonce
                    try:
                        submission_data = submit_response.json()
                        if "hash" in submission_data:
                            challenge["hash"] = submission_data["hash"]
                    except json.JSONDecodeError:
                        pass

                except subprocess.CalledProcessError as e:
                    print(f"Rust solver error: {e.stderr}")
                except requests.exceptions.RequestException as e:
                    print(f"Error submitting solution: {e}")
                except Exception as e:
                    print(f"An unexpected error occurred: {e}")
    save_db(db)


def main():
    parser = argparse.ArgumentParser(
        description="Challenge orchestrator for Midnight scavenger hunt."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init command
    init_parser = subparsers.add_parser(
        "init", help="Initialize or update the database from JSON files."
    )
    init_parser.add_argument("files", nargs="+", help="List of JSON files to import.")

    # fetch command
    subparsers.add_parser("fetch", help="Fetch new challenges.")

    # solve command
    subparsers.add_parser("solve", help="Solve available challenges.")

    args = parser.parse_args()

    if args.command == "init":
        init_db(args.files)
    elif args.command == "fetch":
        db = load_db()
        if not db:
            print("Database is not initialized. Please run 'init' first.")
            return
        addresses = list(db.keys())
        fetch_challenges(addresses)
    elif args.command == "solve":
        db = load_db()
        if not db:
            print("Database is not initialized. Please run 'init' first.")
            return
        solve_challenges()


if __name__ == "__main__":
    main()
