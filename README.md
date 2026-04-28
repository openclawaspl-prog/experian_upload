# Experian Dispute Automation

Automates Experian dispute form submissions by fetching data from a CRM, uploading PDFs, and filling out the form using a Chrome Extension and a Python backend.

## Architecture

1.  **Chrome Extension**: Handles the web interaction, PDF fetching, and form filling on Experian's site.
2.  **Python Backend (`auto_run.py`)**: A Flask-based server that uses `PyAutoGUI` to automate browser actions (like opening tabs) and manages an `ngrok` tunnel for remote triggering.

## Prerequisites

- Python 3.7+
- Google Chrome
- An ngrok account (for remote access)

## Installation

### 1. Python Backend
Install the required Python packages:

```bash
pip install -r requirements.txt
```

### 2. Chrome Extension
1.  Open Chrome and go to `chrome://extensions/`.
2.  Enable **Developer mode**.
3.  Click **Load unpacked** and select this repository folder.

## Configuration

1.  Create a `.env` file based on `.env.example`.
2.  Set your `NGROK_AUTH_TOKEN` and a `AUTO_RUN_SECRET` for security.

## Usage

1.  Run the Python backend:
    ```bash
    python auto_run.py
    ```
2.  Note the public ngrok URL provided in the terminal.
3.  Use the Chrome Extension popup to trigger automation or check status.

## Security Warning
This project uses `PyAutoGUI`, which takes control of your mouse and keyboard. Ensure your Chrome window is visible and do not interact with the computer while the script is running.

## License
MIT
