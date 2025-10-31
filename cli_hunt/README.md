# Midnight Scavenger CLI Miner

This project provides a command-line interface (CLI) tool for managing and automating the Midnight Scavenger mining process, offering an alternative to the official web application. While initial registration and potentially solving the first challenge for an address still require the web interface, this tool streamlines the continuous process of requesting, solving, and submitting new challenges.

The tool is designed to manage multiple mining addresses and keep track of their challenge progress locally.

<img width="2712" height="1904" alt="image" src="https://github.com/user-attachments/assets/552ae061-46fa-4b62-9fd7-b18b136bb33a" />

## Features

*   **Automated Challenge Management**: Automatically fetches new challenges and submits solutions.
*   **Local Data Storage**: All challenge data is stored locally in `challenges.json`, allowing for easy export and reuse with other tools.
*   **Robust Data Handling**: Utilizes an append-only journal (`challenges.json.journal`) to prevent race conditions and ensure data integrity.
*   **Efficient Solver**: Leverages a high-performance Rust binary for solving mining challenges.
*   **TUI**: Provides a Text User Interface (TUI) written in Python for real-time visual feedback on mining progress.

## Getting Started

To get this tool up and running, follow these steps:

### Prerequisites

You will need the following installed on your system:

*   **Rust and Cargo**: The Rust programming language and its package manager are required to compile the solver. You can install them from [rustup.rs](https://rustup.rs/).
*   **uv**: A fast Python package installer and dependency resolver. You can install it from [astral.sh](https://docs.astral.sh/uv/).

### Cloning the Repository

First, clone the project repository to your local machine, and switch to the `piz` branch.
This is a fork of the original repository from IOHK implementing the ashmaize mining function.
Then move into the `cli_hunt` subfolder where I added this tool.

```bash
git clone https://github.com/mpizenberg/ce-ashmaize.git
git checkout piz
cd cli_hunt
```

### Building the Rust Solver

Navigate to the `rust_solver` directory and compile the optimized release binary:

```bash
cd rust_solver
cargo build --release
cd .. # Go back to the project root
```

This will create an executable solver in `cli_hunt/rust_solver/target/release/`.

### Python Orchestrator Setup

Navigate to the `python_orchestrator` directory and install the Python dependencies using `uv`:

```bash
cd python_orchestrator
uv sync
```

## Usage

### Initializing the Database

Before you can run the orchestrator, you need to initialize its local database with information from your mining addresses. This information is obtained by exporting JSON metadata files from the Midnight Scavenger web application for each address you wish to manage. It's likely you'll need to register and solve at least one challenge on the website for each address to enable this export.

Place all your exported JSON files into the `cli_hunt/python_orchestrator/web/` directory.

Then, from the `cli_hunt/python_orchestrator` directory, run the initialization command:

```bash
uv run main.py init web/*.json
```

This command will read the provided JSON files and populate your `challenges.json` database.

### Running the Orchestrator

Once the database is initialized, you can start the main mining loop and TUI. From the `cli_hunt/python_orchestrator` directory, execute:

```bash
uv run main.py run
```

This will launch the TUI, providing visual feedback on the progress of fetching new challenges, solving them with the Rust binary, and submitting solutions. The orchestrator will continuously manage the mining process.

## Project Structure

The project is divided into two main components:

*   `cli_hunt/python_orchestrator`: Contains the Python application responsible for orchestrating the entire mining process. This includes fetching challenges, managing the local database, and submitting solutions. It also hosts the interactive TUI.
*   `cli_hunt/rust_solver`: Houses the high-performance Rust binary that performs the actual cryptographic challenge-solving computation.

## Data Storage

All persistent data for your mining addresses and their challenges are stored in two files within the `cli_hunt/python_orchestrator/` directory:

*   `challenges.json`: The main database file containing the current state of all managed challenges.
*   `challenges.json.journal`: An append-only journal file that records all operations. This journal is regularly consolidated into `challenges.json` and helps prevent data corruption due to race conditions or unexpected shutdowns.

## Contributing

If you find things missing and want to contribute, simply create the code you need and push it on your own fork of this.
I most likely wont integrate others code by lack of time to review this.

## Disclaimer

This tool is provided "as is," without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose, and non-infringement. In no event shall the authors or copyright holders be liable for any claim, damages, or other liability, whether in an action of contract, tort, or otherwise, arising from, out of, or in connection with the software or the use or other dealings in the software.

Users are solely responsible for their actions and for complying with all applicable laws and terms of service when using this tool. The developer of this tool is not responsible for any financial losses, account issues, or any other consequences that may arise from its use.

## License

For the full license text, please see the LICENSE file in the root of the repository.
