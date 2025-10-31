import requests
import json
import subprocess
import os
import argparse
import threading
import time
import signal
import sys
from datetime import datetime, timezone
from copy import deepcopy

# --- Constants ---
DB_FILE = "challenges.json"
JOURNAL_FILE = "challenges.json.journal"
RUST_SOLVER_PATH = (
    "../rust_solver/target/release/ashmaize-solver"  # Assuming it's built
)
FETCH_INTERVAL = 15 * 60  # 15 minutes
DEFAULT_SOLVE_INTERVAL = 30 * 60  # 30 minutes
DEFAULT_SAVE_INTERVAL = 10 * 60  # 10 minutes


# --- DatabaseManager for Thread-Safe Operations ---
class DatabaseManager:
    """Manages the in-memory database with thread-safe operations and journaling."""

    def __init__(self):
        self._db = {}
        self._lock = threading.Lock()
        self._load_from_disk()
        self._replay_journal()

    def _load_from_disk(self):
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, "r") as f:
                    self._db = json.load(f)
                print("Loaded main database from challenges.json.")
            except json.JSONDecodeError:
                print(f"Error reading {DB_FILE}, starting with an empty database.")
                self._db = {}

    def _replay_journal(self):
        if not os.path.exists(JOURNAL_FILE):
            return

        print("Replaying journal...")
        replayed_count = 0
        with open(JOURNAL_FILE, "r") as f:
            for line in f:
                try:
                    log_entry = json.loads(line)
                    action = log_entry.get("action")
                    payload = log_entry.get("payload")
                    address = payload.get("address")

                    if action == "add_challenge":
                        self._apply_add_challenge(address, payload["challenge"])
                    elif action == "update_challenge":
                        self._apply_update_challenge(
                            address, payload["challengeId"], payload["update"]
                        )
                    replayed_count += 1
                except (json.JSONDecodeError, KeyError):
                    print(f"Skipping malformed journal entry: {line.strip()}")
        if replayed_count > 0:
            print(f"Replayed {replayed_count} journal entries.")

    def _log_to_journal(self, action, payload):
        try:
            with open(JOURNAL_FILE, "a") as f:
                log_entry = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "action": action,
                    "payload": payload,
                }
                f.write(json.dumps(log_entry) + "\n")
        except IOError as e:
            print(f"CRITICAL: Could not write to journal file: {e}")

    def _apply_add_challenge(self, address, challenge):
        if address in self._db:
            queue = self._db[address].get("challenge_queue", [])
            if not any(c["challengeId"] == challenge["challengeId"] for c in queue):
                queue.append(challenge)
                queue.sort(key=lambda c: c["challengeId"])

    def _apply_update_challenge(self, address, challenge_id, update):
        if address in self._db:
            queue = self._db[address].get("challenge_queue", [])
            for c in queue:
                if c["challengeId"] == challenge_id:
                    c.update(update)
                    break

    def add_challenge(self, address, challenge):
        with self._lock:
            # Check if challenge already exists before logging to journal
            queue = self._db.get(address, {}).get("challenge_queue", [])
            if any(c["challengeId"] == challenge["challengeId"] for c in queue):
                return False  # Indicate no change was made

            self._log_to_journal(
                "add_challenge", {"address": address, "challenge": challenge}
            )
            self._apply_add_challenge(address, challenge)
            return True

    def update_challenge(self, address, challenge_id, update):
        with self._lock:
            self._log_to_journal(
                "update_challenge",
                {"address": address, "challengeId": challenge_id, "update": update},
            )
            self._apply_update_challenge(address, challenge_id, update)

    def get_addresses(self):
        with self._lock:
            return list(self._db.keys())

    def get_challenge_queue(self, address):
        with self._lock:
            return deepcopy(self._db.get(address, {}).get("challenge_queue", []))

    def save_to_disk(self):
        print("Saving database to disk...")
        with self._lock:
            try:
                with open(DB_FILE, "w") as f:
                    json.dump(self._db, f, indent=4)
                # Clear the journal after a successful save
                open(JOURNAL_FILE, "w").close()
                print("Database saved successfully.")
            except IOError as e:
                print(f"Error saving database: {e}")


# --- Worker Functions ---
def fetcher_worker(db_manager, stop_event):
    print("Fetcher thread started.")
    while not stop_event.is_set():
        print(f"[{datetime.now(timezone.utc).isoformat()}] Fetching new challenges...")
        addresses = db_manager.get_addresses()
        if not addresses:
            print("No addresses in database, fetcher is idle.")
        else:
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

                for address in addresses:
                    if db_manager.add_challenge(address, new_challenge):
                        print(
                            f"New challenge {new_challenge['challengeId']} added for {address}."
                        )

            except requests.exceptions.RequestException as e:
                print(f"Error fetching challenge: {e}")
            except json.JSONDecodeError:
                print("Error decoding challenge API response.")

        stop_event.wait(FETCH_INTERVAL)
    print("Fetcher thread stopped.")


def solver_worker(db_manager, stop_event, interval):
    print(f"Solver thread started. Polling every {interval / 60} minutes.")
    while not stop_event.is_set():
        print(
            f"[{datetime.now(timezone.utc).isoformat()}] Checking for challenges to solve..."
        )
        now = datetime.now(timezone.utc)
        addresses = db_manager.get_addresses()

        for address in addresses:
            challenges = db_manager.get_challenge_queue(address)
            for c in challenges:
                if c["status"] == "available":
                    latest_submission = datetime.fromisoformat(
                        c["latestSubmission"].replace("Z", "+00:00")
                    )
                    if now > latest_submission:
                        print(
                            f"Challenge {c['challengeId']} for {address} has expired."
                        )
                        db_manager.update_challenge(
                            address, c["challengeId"], {"status": "expired"}
                        )
                        continue

                    print(
                        f"Attempting to solve challenge {c['challengeId']} for {address}..."
                    )
                    try:
                        command = [
                            RUST_SOLVER_PATH,
                            "--address",
                            address,
                            "--challenge-id",
                            c["challengeId"],
                            "--difficulty",
                            c["difficulty"],
                            "--no-pre-mine",
                            c["noPreMine"],
                            "--latest-submission",
                            c["latestSubmission"],
                            "--no-pre-mine-hour",
                            c["noPreMineHour"],
                        ]
                        result = subprocess.run(
                            command, capture_output=True, text=True, check=True
                        )
                        nonce = result.stdout.strip()
                        print(f"Found nonce: {nonce}")

                        submit_url = f"https://sm.midnight.gd/api/solution/{address}/{c['challengeId']}/{nonce}"
                        submit_response = requests.post(submit_url, data={})
                        submit_response.raise_for_status()
                        print(f"Solution submitted successfully for {c['challengeId']}")

                        update = {
                            "status": "solved",
                            "solvedAt": datetime.now(timezone.utc)
                            .isoformat(timespec="milliseconds")
                            .replace("+00:00", "Z"),
                            "salt": nonce,
                        }
                        try:
                            submission_data = submit_response.json()
                            if "hash" in submission_data:
                                update["hash"] = submission_data["hash"]
                        except json.JSONDecodeError:
                            pass
                        db_manager.update_challenge(address, c["challengeId"], update)

                    except subprocess.CalledProcessError as e:
                        print(
                            f"Rust solver error for {c['challengeId']}: {e.stderr.strip()}"
                        )
                    except requests.exceptions.RequestException as e:
                        print(f"Error submitting solution for {c['challengeId']}: {e}")
                    except Exception as e:
                        print(f"An unexpected error occurred during solving: {e}")

        stop_event.wait(interval)
    print("Solver thread stopped.")


def saver_worker(db_manager, stop_event, interval):
    print(f"Saver thread started. Saving to disk every {interval / 60} minutes.")
    while not stop_event.is_set():
        db_manager.save_to_disk()
        stop_event.wait(interval)
    print("Saver thread stopped.")


# --- Main Application Logic ---
def init_db(json_files):
    """Initializes or updates the main database file from JSON inputs."""
    print("Initializing or updating database file...")
    db = {}
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                db = json.load(f)
        except json.JSONDecodeError:
            print(f"Could not read existing {DB_FILE}, starting fresh.")

    for file_path in json_files:
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
                address = data.get("registration_receipt", {}).get("walletAddress")
                if not address:
                    print(f"Could not find address in {file_path}, skipping.")
                    continue

                if address not in db:
                    db[address] = {
                        "registration_receipt": data.get("registration_receipt"),
                        "challenge_queue": data.get("challenge_queue", []),
                    }
                    print(f"Initialized new address: {address}")
                else:
                    print(f"Updating existing address: {address}")
                    existing_ids = {
                        c["challengeId"] for c in db[address].get("challenge_queue", [])
                    }
                    new_challenges = [
                        c
                        for c in data.get("challenge_queue", [])
                        if c["challengeId"] not in existing_ids
                    ]
                    if new_challenges:
                        db[address]["challenge_queue"].extend(new_challenges)
                        db[address]["challenge_queue"].sort(
                            key=lambda c: c["challengeId"]
                        )
                        print(f"  Added {len(new_challenges)} new challenges.")
        except FileNotFoundError:
            print(f"File not found: {file_path}")
        except json.JSONDecodeError:
            print(f"Error decoding JSON from {file_path}")

    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=4)

    if os.path.exists(JOURNAL_FILE):
        os.remove(JOURNAL_FILE)
        print("Cleared existing journal file.")
    print("Database file initialization complete.")


def run_orchestrator(args):
    """Starts and manages all worker threads."""
    print("Starting orchestrator...")
    db_manager = DatabaseManager()
    stop_event = threading.Event()

    def signal_handler(sig, frame):
        print("\nShutdown signal received. Stopping threads gracefully...")
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    threads = [
        threading.Thread(target=fetcher_worker, args=(db_manager, stop_event)),
        threading.Thread(
            target=solver_worker, args=(db_manager, stop_event, args.solve_interval)
        ),
        threading.Thread(
            target=saver_worker, args=(db_manager, stop_event, args.save_interval)
        ),
    ]

    for t in threads:
        t.start()

    # Wait for the stop event to be set
    stop_event.wait()

    # Wait for all threads to terminate
    for t in threads:
        t.join()

    print("All threads have stopped. Performing final save.")
    db_manager.save_to_disk()
    print("Orchestrator shut down.")


def main():
    parser = argparse.ArgumentParser(
        description="Challenge orchestrator for Midnight scavenger hunt."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init", help="Initialize or update the database from JSON files."
    )
    init_parser.add_argument("files", nargs="+", help="List of JSON files to import.")

    run_parser = subparsers.add_parser(
        "run", help="Run the orchestrator with fetch, solve, and save threads."
    )
    run_parser.add_argument(
        "--solve-interval",
        type=int,
        default=DEFAULT_SOLVE_INTERVAL,
        help="Interval in seconds for the solver to check for challenges.",
    )
    run_parser.add_argument(
        "--save-interval",
        type=int,
        default=DEFAULT_SAVE_INTERVAL,
        help="Interval in seconds for saving the database to disk.",
    )

    args = parser.parse_args()

    if args.command == "init":
        init_db(args.files)
    elif args.command == "run":
        if not os.path.exists(DB_FILE):
            print("Database file not found. Please run the 'init' command first.")
            sys.exit(1)
        run_orchestrator(args)


if __name__ == "__main__":
    main()
