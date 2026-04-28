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
import psutil

# Path to extension icon image for visual matching
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "ext_icon.png")

app = Flask(__name__)

# Prevent multiple runs at same time
is_running = False
stop_requested = False
last_error = None
run_log = []  # Track execution history

# ============================================
# CONFIG
# ============================================
SECRET_KEY = "mysecret123"
PORT = 5231
SLEEP_BETWEEN_BROWSERS = 3 * 60  # 5 minutes in seconds

# Browser definitions: (display_name, title_keyword, exe_path)
BROWSERS = [
    # ("Ulaa",         "Ulaa",               r"C:\Program Files\Zoho\Ulaa\Application\ulaa.exe"),
    # ("Chrome",       "Google Chrome",      r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    # ("Edge",         "Microsoft Edge",     r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ("Brave",        "Brave",              r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"),
    # ("AVG Secure",   "AVG Secure Browser", r"C:\Program Files\AVG\Browser\Application\avg_browser.exe"),
    # ("JioSphere",    "JioSphere",          r"C:\Program Files\JioSphere\Application\jiosphere.exe"),
    # If JioSphere is installed per-user, try this path instead:
    # ("JioSphere",  "JioSphere",          r"C:\Users\<YourUser>\AppData\Local\JioSphere\Application\jiosphere.exe"),
]

# Disable PyAutoGUI fail-safe (prevents crash if mouse is at corner on RDP/VMs)
pyautogui.FAILSAFE = False
# ============================================


def log(msg):
    """Print with flush so thread output is visible"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    sys.stdout.flush()
    run_log.append(line)
    # Keep only last 50 log entries
    if len(run_log) > 50:
        run_log.pop(0)


# ============================================
# BROWSER HELPERS
# ============================================

def find_and_activate_browser(title_keyword, exe_path=None):
    """
    Bring a browser window to foreground and MAXIMIZE it.
    If exe_path is provided, match windows by process executable first (more reliable).
    Falls back to title keyword matching if exe match fails.
    """
    try:
        user32 = ctypes.windll.user32

        # ── Primary: match by executable path ──────────────────────────────────
        if exe_path:
            exe_name = os.path.basename(exe_path).lower()
            hwnd_target = []

            def enum_cb_exe(hwnd, lParam):
                if user32.IsWindowVisible(hwnd):
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    try:
                        proc = psutil.Process(pid.value)
                        proc_exe = proc.exe().lower()
                        if proc_exe.endswith(exe_name):
                            length = user32.GetWindowTextLengthW(hwnd)
                            if length > 0:  # Only windows with visible titles
                                hwnd_target.append(hwnd)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass
                return True

            WNDENUMPROC_EXE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(WNDENUMPROC_EXE(enum_cb_exe), 0)

            if hwnd_target:
                hwnd = hwnd_target[0]
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE first (in case minimized)
                user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE — full window ✅
                user32.SetForegroundWindow(hwnd)
                log(f"Browser activation OK: '{title_keyword}' (via exe match → {exe_name})")
                return True
            else:
                log(f"Exe match failed for '{exe_name}' — falling back to title match")

        # ── Fallback: match by window title keyword ─────────────────────────────
        hwnd_target = []

        def enum_cb_title(hwnd, lParam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    if title_keyword.lower() in buf.value.lower():
                        hwnd_target.append(hwnd)
            return True

        WNDENUMPROC_TITLE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC_TITLE(enum_cb_title), 0)

        if hwnd_target:
            hwnd = hwnd_target[0]
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE first (in case minimized)
            user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE — full window ✅
            user32.SetForegroundWindow(hwnd)
            log(f"Browser activation OK: '{title_keyword}' (via title match)")
            return True
        else:
            log(f"Browser NOT found: '{title_keyword}'")
            return False

    except Exception as e:
        log(f"Browser activation error for '{title_keyword}': {e}")
        return False


def get_all_window_titles():
    """Returns all visible window titles."""
    try:
        user32 = ctypes.windll.user32
        titles = []

        def enum_cb(hwnd, lParam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    titles.append(buf.value)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        return titles

    except Exception as e:
        log(f"Error reading window titles: {e}")
        return []


def get_foreground_window_title():
    """Returns the title of the currently focused window."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value
        return ""
    except Exception as e:
        log(f"Error reading foreground window title: {e}")
        return ""


def get_foreground_window_exe():
    """Returns the executable path of the currently focused window's process."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = psutil.Process(pid.value)
        return proc.exe()
    except Exception as e:
        log(f"Error reading foreground window exe: {e}")
        return ""


def is_target_tab_open(title_keyword):
    """Check if the active browser tab is an Experian page."""
    title = get_foreground_window_title().lower()
    log(f"Foreground window title: {title}")
    return "experian" in title


# ============================================
# CORE SCRIPT (runs once per browser)
# ============================================

def wait_for_browser_foreground(browser_name, title_keyword, exe_path=None, retries=10, delay=1.0):
    """
    After calling find_and_activate_browser(), wait until the browser
    is actually the foreground window before proceeding.

    Matches foreground window by:
      1. exe_path (if provided) — immune to misleading window titles
      2. title_keyword fallback if exe check fails

    Retries up to `retries` times, re-activating each attempt.
    Returns True if browser came to foreground, False if it never did.
    """
    exe_name = os.path.basename(exe_path).lower() if exe_path else None

    for attempt in range(1, retries + 1):
        time.sleep(delay)
        fg_title = get_foreground_window_title()
        fg_exe   = get_foreground_window_exe()

        log(f"Waiting for {browser_name} foreground... attempt {attempt}/{retries} | "
            f"title='{fg_title}' | exe='{fg_exe}'")

        # Primary check: exe match
        if exe_name and fg_exe.lower().endswith(exe_name):
            log(f"{browser_name} is now foreground (exe match) — proceeding")
            return True

        # Fallback check: title keyword
        if title_keyword.lower() in fg_title.lower():
            log(f"{browser_name} is now foreground (title match) — proceeding")
            return True

        # Re-trigger activation in case Windows didn't honour it
        find_and_activate_browser(title_keyword, exe_path)

    log(f"ERROR: {browser_name} never came to foreground after {retries} attempts — skipping")
    return False


def launch_browser_if_needed(browser_name, title_keyword, exe_path):
    """
    Try to activate the browser. If not found, launch it from exe_path,
    wait for it to load, then try activating again.
    Returns True if browser is successfully activated, False otherwise.
    """
    # First try: maybe it's already open
    found = find_and_activate_browser(title_keyword, exe_path)

    if not found:
        log(f"{browser_name} window not found — attempting to launch...")

        if not exe_path:
            log(f"ERROR: No exe_path defined for {browser_name} — cannot launch")
            return False

        if not os.path.exists(exe_path):
            log(f"ERROR: Executable not found at path: {exe_path}")
            log(f"  Hint: Check BROWSERS config or run 'Get-Process | Select-Object Path' in PowerShell")
            return False

        try:
            subprocess.Popen([exe_path])
            log(f"Launched {browser_name}, waiting 5s for it to load...")
            time.sleep(5)
        except Exception as e:
            log(f"ERROR: Failed to launch {browser_name}: {e}")
            return False

        # Second try after launch — find_and_activate_browser now maximizes automatically
        found = find_and_activate_browser(title_keyword, exe_path)
        if not found:
            log(f"ERROR: {browser_name} still not found after launch — skipping")
            return False

    return True


def run_script_for_browser(browser_name, title_keyword, exe_path=None):
    """Run the automation script for a single browser."""
    log(f"--- Starting script for: {browser_name} ---")

    # Step 0: Bring browser to foreground (launch if needed)
    log(f"Step 0: Activating {browser_name}...")
    if not launch_browser_if_needed(browser_name, title_keyword, exe_path):
        log(f"Skipping {browser_name} — could not activate or launch")
        return

    # Wait until browser is actually the foreground window before sending any keystrokes
    if not wait_for_browser_foreground(browser_name, title_keyword, exe_path):
        return

    time.sleep(0.5)  # small extra buffer after confirmed foreground

    # Step 1: Open incognito/private window + new tab
    time.sleep(1.5)
    pyautogui.hotkey('ctrl', 'shift', 'n')
    time.sleep(1.5)
    pyautogui.hotkey('ctrl', 't')
    time.sleep(1.5)

    # Step 2: Open Extension via Keyboard Shortcut
    # log("Step 2: Opening extension (Ctrl+Shift+E)...")
    pyautogui.hotkey('ctrl', 'shift', 'q')
    time.sleep(1.5)

    pyautogui.hotkey('win', 'up')
    time.sleep(2)

    # Step 3: Click the bottom button (fixed position)
    log("Step 3: Click (1250, 310)")
    pyautogui.moveTo(1250, 280)
    pyautogui.click()
    time.sleep(1.5)
    log("sleeping for 300 sec or 5 mins")
    time.sleep(160)

    pyautogui.hotkey('alt', 'f4')
    log(f"Script completed for: {browser_name}")


# ============================================
# MAIN LOOP (all browsers, with sleep between)
# ============================================

def run_all_browsers_loop():
    """
    Loops through all browsers forever:
      Ulaa → 5 min → Chrome → 5 min → Edge → 5 min → Brave → 5 min → AVG Secure → 5 min → JioSphere → 5 min → repeat
    Respects stop_requested flag — checks before each browser and every second during sleep.
    """
    global is_running, stop_requested, last_error

    is_running = True
    stop_requested = False
    last_error = None
    cycle = 0

    try:
        while not stop_requested:
            cycle += 1
            log(f"=== Cycle {cycle} starting ===")

            for browser_name, title_keyword, exe_path in BROWSERS:

                # Check stop before starting each browser
                if stop_requested:
                    log("Stop requested — breaking browser loop")
                    break

                # Run the script for this browser
                try:
                    run_script_for_browser(browser_name, title_keyword, exe_path)
                except Exception as e:
                    last_error = traceback.format_exc()
                    log(f"ERROR in {browser_name}: {e}")
                    log(last_error)

                # Sleep between browsers
                log(f"Sleeping {SLEEP_BETWEEN_BROWSERS // 60} min after {browser_name}...")
                for _ in range(SLEEP_BETWEEN_BROWSERS):
                    if stop_requested:
                        log("Stop requested during sleep — exiting")
                        break
                    time.sleep(1)

                if stop_requested:
                    break

            if stop_requested:
                break

            log(f"=== Cycle {cycle} complete — restarting from Ulaa ===")

    except Exception as e:
        last_error = traceback.format_exc()
        log(f"FATAL ERROR in browser loop: {e}")
        log(last_error)
    finally:
        is_running = False
        log("Run loop finished.")


# ============================================
# FLASK ROUTES
# ============================================

@app.route('/run', methods=['GET', 'POST', 'HEAD', 'OPTIONS'])
def run_api():
    global is_running, stop_requested

    if request.method in ['HEAD', 'OPTIONS']:
        return '', 200

    key = request.args.get("key")
    if key != SECRET_KEY:
        return "Unauthorized", 401

    if is_running:
        return jsonify({
            "status": "already_running",
            "time": str(datetime.datetime.now()),
            "message": "Loop is already running. Call /stop?key=... to stop it first."
        })

    stop_requested = False
    threading.Thread(target=run_all_browsers_loop, daemon=True).start()

    return jsonify({
        "status": "triggered",
        "time": str(datetime.datetime.now()),
        "message": "Browser loop started: Ulaa → Chrome → Edge → Brave → AVG Secure → JioSphere"
    })


@app.route('/stop', methods=['GET', 'POST', 'HEAD', 'OPTIONS'])
def stop_api():
    global stop_requested

    if request.method in ['HEAD', 'OPTIONS']:
        return '', 200

    key = request.args.get("key")
    if key != SECRET_KEY:
        return "Unauthorized", 401

    if not is_running:
        return jsonify({
            "status": "not_running",
            "time": str(datetime.datetime.now()),
            "message": "Nothing is running right now."
        })

    stop_requested = True
    log("Stop requested via /stop endpoint")

    return jsonify({
        "status": "stop_requested",
        "time": str(datetime.datetime.now()),
        "message": "Stop signal sent. Loop will exit at the next checkpoint (within ~1 sec)."
    })


@app.route('/status')
def status():
    return jsonify({
        "running": is_running,
        "stop_requested": stop_requested,
        "time": str(datetime.datetime.now()),
        "message": "API is online",
        "last_error": last_error,
        "recent_logs": run_log[-10:]
    })


@app.route('/')
def home():
    return "Auto Run API is online ✅"


@app.route('/windows')
def list_windows():
    key = request.args.get("key")
    if key != SECRET_KEY:
        return "Unauthorized", 401

    titles = get_all_window_titles()

    browser_exes = ["chrome.exe", "msedge.exe", "brave.exe", "avg_browser.exe", "ulaa.exe", "jiosphere.exe"]
    running_browsers = []
    for proc in psutil.process_iter(['pid', 'name', 'exe']):
        try:
            if proc.info['name'] and proc.info['name'].lower() in browser_exes:
                running_browsers.append({
                    "pid":  proc.info['pid'],
                    "name": proc.info['name'],
                    "exe":  proc.info['exe']
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return jsonify({
        "window_titles": titles,
        "window_count": len(titles),
        "running_browser_processes": running_browsers,
        "tip": "Use 'exe' field to verify exe paths in BROWSERS config"
    })


# ============================================
# ENTRY POINT
# ============================================

if __name__ == '__main__':
    ngrok.set_auth_token("3BnVWB1jfPfeq8ePywe9Q223AjZ_5me1wfTeFX6mqG1LXTHVH")

    try:
        ngrok.kill()
    except:
        pass

    public_url = ngrok.connect(PORT).public_url

    print("=" * 60)
    print("  AUTO RUN SCRIPT - ONLINE MODE (via ngrok)")
    print("=" * 60)
    print(f"  Local:   http://127.0.0.1:{PORT}")
    print(f"  Public:  {public_url}")
    print(f"")
    print(f"  ENDPOINTS:")
    print(f"  Start:   {public_url}/run?key={SECRET_KEY}")
    print(f"  Stop:    {public_url}/stop?key={SECRET_KEY}")
    print(f"  Status:  {public_url}/status")
    print(f"  Windows: {public_url}/windows?key={SECRET_KEY}  ← use to verify exe paths")
    print(f"")
    print(f"  BROWSER ORDER:")
    for name, _, exe in BROWSERS:
        exists = "✅" if os.path.exists(exe) else "❌ NOT FOUND"
        print(f"    → {name:<15}  {exists}")
        if not os.path.exists(exe):
            print(f"       Path: {exe}")
    print(f"  ({SLEEP_BETWEEN_BROWSERS // 60} min sleep between each)")
    print("=" * 60)

    with open("ngrok_url.txt", "w") as f:
        f.write(f"Public URL: {public_url}\n")
        f.write(f"Start:   {public_url}/run?key={SECRET_KEY}\n")
        f.write(f"Stop:    {public_url}/stop?key={SECRET_KEY}\n")
        f.write(f"Status:  {public_url}/status\n")
        f.write(f"Windows: {public_url}/windows?key={SECRET_KEY}\n")
    print("  URLs saved to ngrok_url.txt")

    app.run(host='127.0.0.1', port=PORT)