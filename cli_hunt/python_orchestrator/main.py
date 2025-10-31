import argparse
import json
import logging
import os
import subprocess
import threading
from copy import deepcopy
from datetime import datetime, timezone

import requests
from tui import ChallengeUpdate, LogMessage, OrchestratorTUI, RefreshTable

# --- Constants ---
DB_FILE = "challenges.json"
JOURNAL_FILE = "challenges.json.journal"
LOG_FILE = "orchestrator.log"
RUST_SOLVER_PATH = (
    "../rust_solver/target/release/ashmaize-solver"  # Assuming it's built
)
FETCH_INTERVAL = 15 * 60  # 15 minutes
DEFAULT_SOLVE_INTERVAL = 30 * 60  # 30 minutes
DEFAULT_SAVE_INTERVAL = 2 * 60  # 2 minutes


# --- Logging Setup ---
def setup_logging():
    """Sets up logging to a file."""
    # Configure logging to write to a file, overwriting it each time
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        filename=LOG_FILE,
        filemode="w",  # 'w' to overwrite the log on each run
    )
    # Silence noisy libraries
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# --- DatabaseManager for Thread-Safe Operations ---
class DatabaseManager:
    """Manages the in-memory database with thread-safe operations and journaling."""

    def __init__(self):
        self._db = {}
        # A lock is still good practice for data consistency between background workers.
        self._lock = threading.Lock()
        self._load_from_disk()
        self._replay_journal()

    def _load_from_disk(self):
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, "r") as f:
                    self._db = json.load(f)
                logging.info("Loaded main database from challenges.json.")
            except json.JSONDecodeError:
                logging.error(
                    f"Error reading {DB_FILE}, starting with an empty database."
                )
                self._db = {}

    def _replay_journal(self):
        if not os.path.exists(JOURNAL_FILE):
            return

        logging.info("Replaying journal...")
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
                    logging.warning(f"Skipping malformed journal entry: {line.strip()}")
        if replayed_count > 0:
            logging.info(f"Replayed {replayed_count} journal entries.")

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
            logging.critical(f"CRITICAL: Could not write to journal file: {e}")

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
            queue = self._db.get(address, {}).get("challenge_queue", [])
            if any(c["challengeId"] == challenge["challengeId"] for c in queue):
                return False

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
            # Return the updated status if it exists
            return update.get("status")

    def get_addresses(self):
        with self._lock:
            return list(self._db.keys())

    def get_challenge_queue(self, address):
        with self._lock:
            return deepcopy(self._db.get(address, {}).get("challenge_queue", []))

    def save_to_disk(self):
        logging.info("Saving database to disk...")
        with self._lock:
            try:
                with open(DB_FILE, "w") as f:
                    json.dump(self._db, f, indent=4)
                if os.path.exists(JOURNAL_FILE):
                    open(JOURNAL_FILE, "w").close()
                logging.info("Database saved successfully.")
            except IOError as e:
                logging.error(f"Error saving database: {e}")


# --- Worker Functions ---
# Note: These are now designed to be run by a Textual @work decorator.
# They accept a `tui_app` object to post messages back to the UI thread.


def fetcher_worker(db_manager, stop_event, tui_app):
    tui_app.post_message(LogMessage("Fetcher thread started."))
    while not stop_event.is_set():
        tui_app.post_message(LogMessage("Fetching new challenges..."))
        addresses = db_manager.get_addresses()
        if not addresses:
            tui_app.post_message(
                LogMessage("No addresses in database, fetcher is idle.")
            )
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

                added = False
                for address in addresses:
                    if db_manager.add_challenge(address, new_challenge):
                        tui_app.post_message(
                            LogMessage(
                                f"New challenge {new_challenge['challengeId']} added for {address[:10]}..."
                            )
                        )
                        added = True

                if added:
                    # Signal to the UI that a full refresh is needed to show the new column
                    tui_app.post_message(RefreshTable())

            except requests.exceptions.RequestException as e:  # ty: ignore
                tui_app.post_message(LogMessage(f"Error fetching challenge: {e}"))
            except json.JSONDecodeError:
                tui_app.post_message(
                    LogMessage("Error decoding challenge API response.")
                )

        stop_event.wait(FETCH_INTERVAL)
    logging.info("Fetcher thread stopped.")


def solver_worker(db_manager, stop_event, interval, tui_app):
    tui_app.post_message(
        LogMessage(f"Solver thread started. Polling every {interval / 60:.1f} minutes.")
    )
    while not stop_event.is_set():
        tui_app.post_message(LogMessage("Checking for challenges to solve..."))
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
                        msg = f"Challenge {c['challengeId']} for {address[:10]}... has expired."
                        tui_app.post_message(LogMessage(msg))
                        updated_status = db_manager.update_challenge(
                            address, c["challengeId"], {"status": "expired"}
                        )
                        if updated_status:
                            tui_app.post_message(
                                ChallengeUpdate(
                                    address, c["challengeId"], updated_status
                                )
                            )
                        continue

                    # Update UI to show we're trying to solve it
                    tui_app.post_message(
                        ChallengeUpdate(address, c["challengeId"], "solving")
                    )
                    msg = f"Attempting to solve challenge {c['challengeId']} for {address[:10]}..."
                    tui_app.post_message(LogMessage(msg))

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
                        process = subprocess.Popen(
                            command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )

                        while process.poll() is None:
                            if stop_event.is_set():
                                process.terminate()
                                tui_app.post_message(
                                    LogMessage(
                                        f"Solver for {c['challengeId']} terminated by shutdown."
                                    )
                                )
                                return  # Exit worker thread
                            stop_event.wait(0.2)  # Non-blocking wait

                        stdout, stderr = process.communicate()

                        if process.returncode != 0:
                            raise subprocess.CalledProcessError(
                                process.returncode,
                                command,
                                output=stdout,
                                stderr=stderr,
                            )

                        nonce = stdout.strip()
                        solved_time = datetime.now(timezone.utc)
                        tui_app.post_message(
                            LogMessage(f"Found nonce: {nonce} for {c['challengeId']}")
                        )

                        submit_url = f"https://sm.midnight.gd/api/solution/{address}/{c['challengeId']}/{nonce}"
                        submit_response = requests.post(submit_url)
                        submit_response.raise_for_status()
                        validated_time = datetime.now(timezone.utc)
                        tui_app.post_message(
                            LogMessage(
                                f"Solution submitted successfully for {c['challengeId']}"
                            )
                        )

                        try:
                            submission_data = submit_response.json()
                            crypto_receipt = submission_data.get("crypto_receipt")

                            update = {}
                            if crypto_receipt:
                                update = {
                                    "status": "validated",
                                    "solvedAt": solved_time.isoformat(
                                        timespec="milliseconds"
                                    ).replace("+00:00", "Z"),
                                    "submittedAt": solved_time.isoformat(
                                        timespec="milliseconds"
                                    ).replace("+00:00", "Z"),
                                    "validatedAt": validated_time.isoformat(
                                        timespec="milliseconds"
                                    ).replace("+00:00", "Z"),
                                    "salt": nonce,
                                    "cryptoReceipt": crypto_receipt,
                                }
                                tui_app.post_message(
                                    LogMessage(
                                        f"Successfully validated challenge {c['challengeId']}"
                                    )
                                )
                            else:
                                update = {
                                    "status": "solved",  # Submitted but not validated with receipt
                                    "solvedAt": solved_time.isoformat(
                                        timespec="milliseconds"
                                    ).replace("+00:00", "Z"),
                                    "salt": nonce,
                                }
                                tui_app.post_message(
                                    LogMessage(
                                        f"Submission for {c['challengeId']} OK but no crypto_receipt."
                                    )
                                )

                            updated_status = db_manager.update_challenge(
                                address, c["challengeId"], update
                            )
                            if updated_status:
                                tui_app.post_message(
                                    ChallengeUpdate(
                                        address, c["challengeId"], updated_status
                                    )
                                )

                        except json.JSONDecodeError:
                            msg = f"Failed to decode submission response for {c['challengeId']}."
                            tui_app.post_message(LogMessage(msg))
                            update = {"status": "submission_error", "salt": nonce}
                            updated_status = db_manager.update_challenge(
                                address, c["challengeId"], update
                            )
                            if updated_status:
                                tui_app.post_message(
                                    ChallengeUpdate(
                                        address, c["challengeId"], updated_status
                                    )
                                )

                    except subprocess.CalledProcessError as e:
                        msg = f"Rust solver error for {c['challengeId']}: {e.stderr.strip()}"
                        tui_app.post_message(LogMessage(msg))
                        # Revert status to available if solver fails
                        tui_app.post_message(
                            ChallengeUpdate(address, c["challengeId"], "available")
                        )
                    except requests.exceptions.RequestException as e:  # ty: ignore
                        msg = f"Error submitting solution for {c['challengeId']}: {e}"
                        tui_app.post_message(LogMessage(msg))
                        tui_app.post_message(
                            ChallengeUpdate(address, c["challengeId"], "available")
                        )
                    except Exception as e:
                        msg = f"An unexpected error occurred during solving: {e}"
                        tui_app.post_message(LogMessage(msg))
                        tui_app.post_message(
                            ChallengeUpdate(address, c["challengeId"], "available")
                        )

        stop_event.wait(interval)
    logging.info("Solver thread stopped.")


def saver_worker(db_manager, stop_event, interval, tui_app):
    tui_app.post_message(
        LogMessage(
            f"Saver thread started. Saving to disk every {interval / 60:.1f} minutes."
        )
    )
    while not stop_event.is_set():
        stop_event.wait(interval)
        if stop_event.is_set():
            break
        tui_app.post_message(LogMessage("Performing periodic save..."))
        db_manager.save_to_disk()
    logging.info("Saver thread stopped.")


# --- Main Application Logic ---
def init_db(json_files):
    """Initializes or updates the main database file from JSON inputs."""
    logging.info("Initializing or updating database file...")
    db = {}
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                db = json.load(f)
        except json.JSONDecodeError:
            logging.warning(f"Could not read existing {DB_FILE}, starting fresh.")

    for file_path in json_files:
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
                address = data.get("registration_receipt", {}).get("walletAddress")
                if not address:
                    logging.warning(f"Could not find address in {file_path}, skipping.")
                    continue

                if address not in db:
                    db[address] = {
                        "registration_receipt": data.get("registration_receipt"),
                        "challenge_queue": data.get("challenge_queue", []),
                    }
                    logging.info(f"Initialized new address: {address}")
                else:
                    logging.info(f"Updating existing address: {address}")
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
                        logging.info(f"  Added {len(new_challenges)} new challenges.")
        except FileNotFoundError:
            logging.error(f"File not found: {file_path}")
        except json.JSONDecodeError:
            logging.error(f"Error decoding JSON from {file_path}")

    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=4)

    if os.path.exists(JOURNAL_FILE):
        os.remove(JOURNAL_FILE)
        logging.info("Cleared existing journal file.")
    logging.info("Database file initialization complete.")


def run_orchestrator(args):
    """Starts and manages the TUI and all worker threads."""
    logging.info("Starting orchestrator TUI...")
    db_manager = DatabaseManager()

    worker_functions = {
        "fetcher": fetcher_worker,
        "solver": solver_worker,
        "saver": saver_worker,
    }

    worker_args = {
        "solve_interval": args.solve_interval,
        "save_interval": args.save_interval,
    }

    app = OrchestratorTUI(
        db_manager=db_manager,
        worker_functions=worker_functions,
        worker_args=worker_args,
    )
    app.run()
    logging.info("Orchestrator shut down.")


def main():
    parser = argparse.ArgumentParser(
        description="Challenge orchestrator for Midnight scavenger hunt."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init", help="Initialize or update the database from JSON files."
    )
    init_parser.add_argument("files", nargs="+", help="List of JSON files to import.")

    run_parser = subparsers.add_parser("run", help="Run the orchestrator with TUI.")
    run_parser.add_argument(
        "--solve-interval",
        type=int,
        default=DEFAULT_SOLVE_INTERVAL,
        help=f"Interval in seconds for the solver to check for challenges (default: {DEFAULT_SOLVE_INTERVAL}).",
    )
    run_parser.add_argument(
        "--save-interval",
        type=int,
        default=DEFAULT_SAVE_INTERVAL,
        help=f"Interval in seconds for saving the database to disk (default: {DEFAULT_SAVE_INTERVAL}).",
    )

    args = parser.parse_args()

    setup_logging()

    if args.command == "init":
        init_db(args.files)
    elif args.command == "run":
        if not os.path.exists(DB_FILE):
            print("Database file not found. Please run the 'init' command first.")
            logging.critical("Database file not found. Aborting run.")
            os._exit(1)  # Exit immediately without traceback
        run_orchestrator(args)


if __name__ == "__main__":
    main()
