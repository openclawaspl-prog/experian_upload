from flask import Flask, request, jsonify
from pyngrok import ngrok
import pyautogui
import time
import threading
import datetime
import traceback
import sys
import os
import ctypes
from ctypes import wintypes
import subprocess
from dotenv import load_dotenv

# Load variables from .env if it exists
load_dotenv()

# Path to extension icon image for visual matching
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "icon128.png")

app = Flask(__name__)

# Prevent multiple runs at same time
is_running = False
last_error = None
run_log = []  # Track execution history

# ============================================
# CONFIG
# ============================================
SECRET_KEY = os.getenv("AUTO_RUN_SECRET", "mysecret1256")
PORT = int(os.getenv("PORT", 5232))
# Disable PyAutoGUI fail-safe (prevents crash if mouse is at corner of screen on RDP/VMs)
pyautogui.FAILSAFE = False
# ============================================


def log(msg):
    """Print with flush so thread output is visible"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    sys.stdout.flush()
    run_log.append(line)
    # Keep only last 20 log entries
    if len(run_log) > 20:
        run_log.pop(0)


# ============================================
# Chrome HELPERS
# ============================================

def find_and_activate_Chrome():
    """Bring Chrome window to the foreground and maximize it."""
    try:
        user32 = ctypes.windll.user32
        hwnd_target = []

        def enum_cb(hwnd, lParam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    if "Chrome" in buf.value:
                        hwnd_target.append(hwnd)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

        if hwnd_target:
            hwnd = hwnd_target[0]
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            user32.SetForegroundWindow(hwnd)
            log("Chrome activation: OK (via ctypes)")
            return True

    except Exception as e:
        log(f"Chrome activation error (ctypes): {e}")

    # Fallback to PowerShell if ctypes fails
    try:
        log("Trying PowerShell fallback for Chrome activation...")
        ps_cmd = '''
        Add-Type -TypeDefinition @"
        using System;
        using System.Runtime.InteropServices;
        public class Win {
            [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
            [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        }
"@
        $Chrome = Get-Process Chrome -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
        if ($Chrome) {
            [Win]::ShowWindow($Chrome.MainWindowHandle, 9)
            [Win]::ShowWindow($Chrome.MainWindowHandle, 3)
            [Win]::SetForegroundWindow($Chrome.MainWindowHandle)
            Write-Output "OK"
        } else {
            Write-Output "NOTFOUND"
        }
        '''
        result = subprocess.run(
            ["powershell", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout.strip()
        log(f"Chrome activation: {output}")
        return "OK" in output

    except Exception as e:
        log(f"Chrome activation error (PowerShell): {e}")
        return False


def get_active_Chrome_title():
    """Returns the window title of the currently focused Chrome window."""
    try:
        user32 = ctypes.windll.user32
        hwnd_list = []

        def enum_cb(hwnd, lParam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
                    # Chrome windows end with "- Chrome"
                    if "Chrome" in title:
                        hwnd_list.append(title)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

        return hwnd_list[0] if hwnd_list else ""

    except Exception as e:
        log(f"Error reading Chrome title: {e}")
        return ""


def is_experian_tab_open():
    """Check if the active Chrome tab is an Experian page by reading the window title."""
    title = get_active_Chrome_title().lower()
    log(f"Chrome window title: {title}")
    return "experian" in title


# ============================================
# MAIN AUTOMATION SCRIPT
# ============================================

def run_script():
    """Main automation script - keyboard + mouse actions"""
    log("Running script...")

    # Step 0: Bring Chrome to foreground
    log("Step 0: Activating Chrome...")
    if not find_and_activate_Chrome():
        log("ERROR: Chrome not found or couldn't activate!")
        return
    time.sleep(1)

    time.sleep(2.5)
    pyautogui.hotkey('ctrl','shift','n')
    time.sleep(2.5)

    pyautogui.hotkey('ctrl','t')
    time.sleep(1.5)

    log("Step 2.1: Opening proxy (Ctrl+Shift+A)...")
    pyautogui.hotkey('ctrl', 'shift', 'A')
    time.sleep(2)

    log("Step 2.2: Click proxy")
    pyautogui.moveTo(1250, 250)
    pyautogui.click()
    time.sleep(5.5)

    # Step 2: Open Extension via Keyboard Shortcut
    log("Step 2: Opening extension (Ctrl+Shift+Q)...")
    pyautogui.hotkey('ctrl', 'shift', 'q')
    time.sleep(2)

    # Step 3: Click the bottom button (fixed position - always there)
    log("Step 3: Click (1300, 300)")
    pyautogui.moveTo(1250, 290)
    pyautogui.click()
    time.sleep(270)
    pyautogui.hotkey('alt', 'f4')
    log("step 4: alt")
   
    log("Script completed!")


def run_script_safe():
    """Thread-safe wrapper to prevent overlapping runs"""
    global is_running, last_error

    if is_running:
        log("Already running, skipping...")
        return

    is_running = True
    last_error = None
    try:
        run_script()
    except Exception as e:
        last_error = traceback.format_exc()
        log(f"ERROR: {e}")
        log(last_error)
    finally:
        is_running = False


# ============================================
# FLASK ROUTES
# ============================================

@app.route('/run', methods=['GET', 'POST', 'HEAD'])
def run_api():
    # Handle cron HEAD/OPTIONS requests (cron-job.org sends these)
    if request.method in ['HEAD', 'OPTIONS']:
        return '', 200

    # 🔐 Security check
    key = request.args.get("key")
    if key != SECRET_KEY:
        return "Unauthorized", 401

    threading.Thread(target=run_script_safe).start()
    return jsonify({
        "status": "triggered",
        "time": str(datetime.datetime.now())
    })


@app.route('/status')
def status():
    return jsonify({
        "running": is_running,
        "time": str(datetime.datetime.now()),
        "message": "API is online",
        "last_error": last_error,
        "recent_logs": run_log[-10:]
    })


@app.route('/')
def home():
    return "Auto Run API is online ✅"


# ============================================
# ENTRY POINT
# ============================================

if __name__ == '__main__':
    # Set ngrok auth token from environment variable
    auth_token = os.getenv("NGROK_AUTH_TOKEN")
    if auth_token:
        ngrok.set_auth_token(auth_token)
    else:
        print("WARNING: NGROK_AUTH_TOKEN not found in environment variables.")

    # Kill any existing ngrok processes
    try:
        ngrok.kill()
    except:
        pass

    # Start ngrok tunnel
    public_url = ngrok.connect(PORT).public_url

    print("=" * 60)
    print("  AUTO RUN SCRIPT - ONLINE MODE (via ngrok)")
    print("=" * 60)
    print(f"  Local:   http://127.0.0.1:{PORT}")
    print(f"  Public:  {public_url}")
    print(f"")
    print(f"  CRON JOB URLs (use on cron-job.org):")
    print(f"  Trigger: {public_url}/run?key={SECRET_KEY}")
    print(f"  Status:  {public_url}/status")
    print(f"")
    # print(f"  Local IP: 122.179.139.244 (no port exposed)")
    print("=" * 60)

    # Save URL to file for easy reference
    with open("ngrok_url.txt", "w") as f:
        f.write(f"Public URL: {public_url}\n")
        f.write(f"Trigger:    {public_url}/run?key={SECRET_KEY}\n")
        f.write(f"Status:     {public_url}/status\n")
    print("  URLs saved to ngrok_url.txt")

    # Flask runs on localhost only - ngrok handles public access
    app.run(host='127.0.0.1', port=PORT)
