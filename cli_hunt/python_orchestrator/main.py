import requests
import json
import subprocess
import time
import os

CONFIG_FILE = "config.json"
DB_FILE = "challenges.json"
RUST_SOLVER_PATH = (
    "../rust_solver/target/release/ashmaize-solver"  # Assuming it's built
)


def load_config():
    if not os.path.exists(CONFIG_FILE):
        print(
            "Config file not found. Please create config.json with your Cardano addresses."
        )
        return {"addresses": []}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=4)


def load_db():
    if not os.path.exists(DB_FILE):
        return {"challenges": []}
    with open(DB_FILE, "r") as f:
        return json.load(f)


def fetch_challenges(addresses):
    print("Fetching challenges...")
    db = load_db()
    for address in addresses:
        try:
            response = requests.get(
                "https://sm.midnight.gd/api/challenge"
            )  # Assuming API runs on localhost:8080
            response.raise_for_status()
            challenge_response = response.json()
            challenge = challenge_response["challenge"]
            challenge["address"] = address
            challenge["solved"] = False
            # Check if challenge already exists for this address
            if not any(
                c["challenge_id"] == challenge["challenge_id"]
                and c["address"] == address
                for c in db["challenges"]
            ):
                db["challenges"].append(challenge)
                print(
                    f"New challenge fetched for {address}: {challenge['challenge_id']}"
                )
            else:
                print(
                    f"Challenge {challenge['challenge_id']} already exists for {address}"
                )
        except requests.exceptions.RequestException as e:
            print(f"Error fetching challenge for {address}: {e}")
    save_db(db)


def solve_challenges():
    print("Solving challenges...")
    db = load_db()
    for challenge in db["challenges"]:
        if not challenge["solved"]:
            print(
                f"Attempting to solve challenge {challenge['challenge_id']} for {challenge['address']}"
            )
            try:
                command = [
                    RUST_SOLVER_PATH,
                    "--address",
                    challenge["address"],
                    "--challenge-id",
                    challenge["challenge_id"],
                    "--difficulty",
                    challenge["difficulty"],
                    "--no-pre-mine",
                    challenge["no_pre_mine"],
                    "--latest-submission",
                    challenge["latest_submission"],
                    "--no-pre-mine-hour",
                    challenge["no_pre_mine_hour"],
                ]
                result = subprocess.run(
                    command, capture_output=True, text=True, check=True
                )
                nonce = result.stdout.strip()
                print(f"Found nonce: {nonce}")

                # Submit solution
                submit_url = f"https://sm.midnight.gd/api/solution/{challenge['address']}/{challenge['challenge_id']}/{nonce}"
                submit_response = requests.post(submit_url)
                submit_response.raise_for_status()
                print(
                    f"Solution submitted successfully for {challenge['challenge_id']}"
                )
                challenge["solved"] = True
            except subprocess.CalledProcessError as e:
                print(f"Rust solver error: {e.stderr}")
            except requests.exceptions.RequestException as e:
                print(f"Error submitting solution: {e}")
            except Exception as e:
                print(f"An unexpected error occurred: {e}")
    save_db(db)


def main():
    config = load_config()
    addresses = config.get("addresses", [])

    if not addresses:
        print("No addresses found in config.json. Please add your Cardano addresses.")
        return

    # Simple CLI for now
    while True:
        print("\nChoose an action:")
        print("1. Fetch challenges")
        print("2. Solve challenges")
        print("3. Exit")
        choice = input("Enter your choice: ")

        if choice == "1":
            fetch_challenges(addresses)
        elif choice == "2":
            solve_challenges()
        elif choice == "3":
            break
        else:
            print("Invalid choice. Please try again.")


if __name__ == "__main__":
    main()
