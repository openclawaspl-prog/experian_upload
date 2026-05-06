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
import json
import websocket
import requests as _http

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
PORT = 5232
SLEEP_BETWEEN_BROWSERS = 3 * 60  # 3 minutes in seconds

# Browser definitions: (display_name, title_keyword, exe_path, cdp_port)
#
# cdp_port — Chrome DevTools Protocol port for this browser instance.
# This allows Python to call runDisputePipeline() directly in the extension's
# service worker without needing to click anything on screen.
#
# HOW TO ENABLE CDP (one-time setup per browser):
#   Right-click the browser shortcut → Properties → Target → append:
#   --remote-debugging-port=<cdp_port>   e.g. chrome.exe --remote-debugging-port=9223
#
# If a browser is launched by this script (not pre-running), the port is
# added automatically. If it was already running WITHOUT the flag, CDP will
# fail gracefully and fall through to Tab+Enter keyboard trigger.
BROWSERS = [
    # ("Ulaa",       "Ulaa",               r"C:\Program Files\Zoho\Ulaa\Application\ulaa.exe",                       9222),
    ("Chrome",     "Google Chrome",      r"C:\Program Files\Google\Chrome\Application\chrome.exe",                  9223),
    ("AVG Secure", "AVG Secure Browser", r"C:\Program Files\AVG\Browser\Application\avg_browser.exe",               9224),
    ("Edge",       "Microsoft Edge",     r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",           9225),
    ("Brave",      "Brave",              r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",     9226),
    ("JioSphere",  "JioSphere",          r"C:\Program Files\JioSphere\Application\jiosphere.exe",                   9227),
    # Per-user JioSphere fallback:
    # ("JioSphere", "JioSphere", r"C:\Users\<YourUser>\AppData\Local\JioSphere\Application\jiosphere.exe", 9227),
]

# Surfshark VPN window handling.
# Set the click coordinates to the button you want to press after Surfshark is restored.
SURFSHARK_WINDOW_TITLE = "Surfshark"
SURFSHARK_EXE_PATH = r"C:\Program Files\Surfshark\Surfshark.exe"
SURFSHARK_CLICK_X = 1400
SURFSHARK_CLICK_Y = 800
SURFSHARK_RESTORE_DELAY = 1.0
SURFSHARK_POST_CLICK_DELAY = 15.0
SURFSHARK_AFTER_CLOSE_DELAY = 5.0

# Disable PyAutoGUI fail-safe (prevents crash if mouse is at corner on RDP/VMs)
pyautogui.FAILSAFE = False



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


def sleep_with_stop(seconds, label):
    """Sleep in 1-second chunks so stop requests can interrupt long waits."""
    for _ in range(int(seconds)):
        if stop_requested:
            log(f"Stop requested during {label} - exiting")
            return False
        time.sleep(1)
    return True


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
                            if length > 0:
                                hwnd_target.append(hwnd)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass
                return True

            WNDENUMPROC_EXE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(WNDENUMPROC_EXE(enum_cb_exe), 0)

            if hwnd_target:
                hwnd = hwnd_target[0]
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
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
            user32.ShowWindow(hwnd, 9)
            user32.ShowWindow(hwnd, 3)
            user32.SetForegroundWindow(hwnd)
            log(f"Browser activation OK: '{title_keyword}' (via title match)")
            return True
        else:
            log(f"Browser NOT found: '{title_keyword}'")
            return False

    except Exception as e:
        log(f"Browser activation error for '{title_keyword}': {e}")
        return False


def find_and_restore_window(title_keyword, exe_path=None):
    """
    Bring a non-browser window to the foreground and restore it if minimized.
    Returns the restored window handle, or None if no match was found.
    """
    try:
        user32 = ctypes.windll.user32

        # Primary: match by executable path
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
                            if length > 0:
                                hwnd_target.append(hwnd)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        pass
                return True

            WNDENUMPROC_EXE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(WNDENUMPROC_EXE(enum_cb_exe), 0)

            if hwnd_target:
                hwnd = hwnd_target[0]
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                log(f"Window restore OK: '{title_keyword}' (via exe match -> {exe_name})")
                return hwnd
            else:
                log(f"Exe match failed for '{exe_name}' - falling back to title match")

        # Fallback: match by window title keyword
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
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            log(f"Window restore OK: '{title_keyword}' (via title match)")
            return hwnd
        else:
            log(f"Window NOT found: '{title_keyword}'")
            return None

    except Exception as e:
        log(f"Window restore error for '{title_keyword}': {e}")
        return None


def restore_surfshark_and_click(click_x, click_y, title_keyword=SURFSHARK_WINDOW_TITLE, exe_path=SURFSHARK_EXE_PATH):
    """
    Restore Surfshark from a minimized state, move the mouse, and click.
    """
    hwnd = find_and_restore_window(title_keyword, exe_path)
    if not hwnd:
        return False

    time.sleep(SURFSHARK_RESTORE_DELAY)

    if click_x is None or click_y is None:
        user32 = ctypes.windll.user32
        client_rect = wintypes.RECT()
        client_center = wintypes.POINT()

        if user32.GetClientRect(hwnd, ctypes.byref(client_rect)):
            client_center.x = (client_rect.right - client_rect.left) // 2
            client_center.y = (client_rect.bottom - client_rect.top) // 2
            if user32.ClientToScreen(hwnd, ctypes.byref(client_center)):
                click_x = client_center.x
                click_y = client_center.y
                log(f"Surfshark click coordinates not configured; using client center ({click_x}, {click_y}).")

        if click_x is None or click_y is None:
            window_rect = wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
                click_x = (window_rect.left + window_rect.right) // 2
                click_y = (window_rect.top + window_rect.bottom) // 2
                log(f"Surfshark client center unavailable; using window center ({click_x}, {click_y}).")

    if click_x is None or click_y is None:
        log("Surfshark restored, but click coordinates are not configured and could not be derived.")
        return False
    
    pyautogui.moveTo(click_x, click_y, duration=0.25)
    pyautogui.click()
    log(f"Surfshark click sent at ({click_x}, {click_y})")
    time.sleep(SURFSHARK_POST_CLICK_DELAY)
    return True


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


# ============================================
# CDP — CHROME DEVTOOLS PROTOCOL TRIGGER
# ============================================
# Calls runDisputePipeline() directly inside the extension's service worker
# via a WebSocket connection to Chrome's remote debugging port.
# This completely eliminates coordinate-based clicking — works on any screen size.
#
# Requires the browser to be running with --remote-debugging-port=<cdp_port>.
# Browsers launched by this script get the flag automatically via launch_browser_if_needed().
# Browsers that were already running without the flag: CDP fails → falls back to Tab+Enter.
# ============================================

def find_extension_service_worker(cdp_port):
    """
    Query CDP for the extension's background service worker.
    Returns the webSocketDebuggerUrl, or None if not found.
    """
    try:
        res = _http.get(f"http://localhost:{cdp_port}/json/list", timeout=5)
        targets = res.json()
        log(f"CDP port {cdp_port}: found {len(targets)} targets")
        for t in targets:
            url = t.get("url", "")
            if t.get("type") == "service_worker" and "chrome-extension://" in url:
                log(f"CDP: Extension service worker found → {url[:80]}")
                return t.get("webSocketDebuggerUrl")
        log(f"CDP port {cdp_port}: no extension service worker in target list")
    except Exception as e:
        log(f"CDP port {cdp_port}: target query failed — {e}")
    return None


def trigger_extension_cdp(cdp_port):
    """
    Directly invoke runDisputePipeline() in the extension's service worker via CDP.
    Returns True on success, False on any failure.
    """
    if not cdp_port:
        return False

    ws_url = find_extension_service_worker(cdp_port)
    if not ws_url:
        return False

    try:
        ws = websocket.create_connection(ws_url, timeout=10)
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": "runDisputePipeline()",
                "awaitPromise": False,
            }
        }))
        raw = ws.recv()
        ws.close()

        result = json.loads(raw)
        if result.get("error"):
            log(f"CDP: Runtime error — {result['error']}")
            return False

        log(f"CDP: Extension triggered successfully via port {cdp_port}")
        return True

    except Exception as e:
        log(f"CDP: WebSocket error on port {cdp_port} — {e}")
        return False


# ============================================
# POPUP KEYBOARD TRIGGER
# ============================================
# After Ctrl+Shift+Q opens the extension popup, the popup window has focus.
# The "▶ Run Next Dispute" button (id=runBtn) is the FIRST focusable element
# in the popup HTML, so Tab always lands on it.
# This requires no coordinates and works on any screen resolution.
# ============================================

def trigger_extension_keyboard():
    """
    Open the extension popup via keyboard shortcut, then Tab+Enter to click Run.
    No screen coordinates needed — works on any resolution/screen size.
    """
    time.sleep(2.5)

    log("Keyboard trigger: opening popup (Ctrl+Shift+Q)...")
    pyautogui.hotkey('ctrl', 'shift', 'e')
    time.sleep(2.5)

    # Tab focuses runBtn (first focusable element in popup.html)
    # Enter clicks it — same as pressing the button
   
    pyautogui.press('tab')
    time.sleep(1.5)
    pyautogui.press('enter')
    log("Keyboard trigger: done")


# ============================================
# CORE SCRIPT (runs once per browser)
# ============================================

def wait_for_browser_foreground(browser_name, title_keyword, exe_path=None, retries=10, delay=1.0):
    """
    After calling find_and_activate_browser(), wait until the browser
    is actually the foreground window before proceeding.
    """
    exe_name = os.path.basename(exe_path).lower() if exe_path else None

    for attempt in range(1, retries + 1):
        time.sleep(delay)
        fg_title = get_foreground_window_title()
        fg_exe   = get_foreground_window_exe()

        log(f"Waiting for {browser_name} foreground... attempt {attempt}/{retries} | "
            f"title='{fg_title}' | exe='{fg_exe}'")

        if exe_name and fg_exe.lower().endswith(exe_name):
            log(f"{browser_name} is now foreground (exe match) — proceeding")
            return True

        if title_keyword.lower() in fg_title.lower():
            log(f"{browser_name} is now foreground (title match) — proceeding")
            return True

        find_and_activate_browser(title_keyword, exe_path)

    log(f"ERROR: {browser_name} never came to foreground after {retries} attempts — skipping")
    return False


def launch_browser_if_needed(browser_name, title_keyword, exe_path, cdp_port=None):
    """
    Try to activate the browser. If not found, launch it.
    When launching, adds --remote-debugging-port so CDP works from the start.
    """
    found = find_and_activate_browser(title_keyword, exe_path)

    if not found:
        log(f"{browser_name} window not found — attempting to launch...")

        if not exe_path:
            log(f"ERROR: No exe_path defined for {browser_name} — cannot launch")
            return False

        if not os.path.exists(exe_path):
            log(f"ERROR: Executable not found at path: {exe_path}")
            return False

        try:
            cmd = [exe_path]
            if cdp_port:
                cmd.append(f"--remote-debugging-port={cdp_port}")
                log(f"Launching {browser_name} with CDP port {cdp_port}...")
            else:
                log(f"Launching {browser_name}...")
            subprocess.Popen(cmd)
            time.sleep(5)
        except Exception as e:
            log(f"ERROR: Failed to launch {browser_name}: {e}")
            return False

        found = find_and_activate_browser(title_keyword, exe_path)
        if not found:
            log(f"ERROR: {browser_name} still not found after launch — skipping")
            return False

    return True


def run_script_for_browser(browser_name, title_keyword, exe_path=None, cdp_port=None):
    """Run the automation script for a single browser."""
    log(f"--- Starting script for: {browser_name} ---")

    # Step 0: Restore Surfshark before opening the browser.
    log("Surfshark setup: restoring the VPN window before opening the browser...")
    restore_surfshark_and_click(SURFSHARK_CLICK_X, SURFSHARK_CLICK_Y)

    # Step 0: Activate browser (launch if needed)
    if not launch_browser_if_needed(browser_name, title_keyword, exe_path, cdp_port):
        log(f"Skipping {browser_name} — could not activate or launch")
        return

    if not wait_for_browser_foreground(browser_name, title_keyword, exe_path):
        return

    time.sleep(0.5)

    # Step 1: Open incognito window + new tab
    pyautogui.hotkey('ctrl', 'shift', 'n')
    time.sleep(1.5)
    pyautogui.hotkey('ctrl', 't')
    time.sleep(1.5)

    # ── Trigger strategy (most reliable → least reliable) ────────────────────
    #
    # 1. CDP  — calls runDisputePipeline() directly in the service worker.
    #           Zero UI dependency. Works on any screen size.
    #           Requires browser launched with --remote-debugging-port=<cdp_port>.
    #
    # 2. Tab+Enter — opens the popup via Ctrl+Shift+Q, then uses keyboard
    #           navigation to click runBtn (first focusable element in popup.html).
    #           No coordinates. Works on any screen size.
    #
    # 3. Coordinate click — last resort. Screen-size dependent; kept as safety net.
    # ─────────────────────────────────────────────────────────────────────────

    triggered = False

    # Try CDP first
    if cdp_port:
        log(f"Step 2: Trying CDP trigger on port {cdp_port}...")
        triggered = trigger_extension_cdp(cdp_port)
        if triggered:
            log(f"CDP succeeded — job running in extension for {browser_name}")

    # Fallback: keyboard Tab+Enter (no coordinates)
    if not triggered:
        log("Step 2: CDP unavailable — using keyboard trigger (Tab+Enter)...")
        trigger_extension_keyboard()
        triggered = True  # Keyboard trigger is fire-and-forget; assume it worked

    log(f"Waiting 160s for job to complete in {browser_name}...")
    time.sleep(160)

    pyautogui.hotkey('alt', 'f4')
    log(f"--- Script completed for: {browser_name} ---")

    log(f"Waiting {int(SURFSHARK_AFTER_CLOSE_DELAY)}s after Alt+F4 before refreshing Surfshark...")
    if not sleep_with_stop(SURFSHARK_AFTER_CLOSE_DELAY, "post-close Surfshark delay"):
        return

    log("Surfshark setup: restoring the VPN window after browser close...")
    restore_surfshark_and_click(SURFSHARK_CLICK_X, SURFSHARK_CLICK_Y)


# ============================================
# MAIN LOOP (all browsers, with sleep between)
# ============================================

def run_all_browsers_loop():
    """
    Loops through all browsers:
      Ulaa → 3 min → Chrome → 3 min → AVG Secure → 3 min → Edge → 3 min → Brave → 3 min → JioSphere → repeat
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

            for browser_name, title_keyword, exe_path, cdp_port in BROWSERS:

                if stop_requested:
                    log("Stop requested — breaking browser loop")
                    break

                try:
                    run_script_for_browser(browser_name, title_keyword, exe_path, cdp_port)
                except Exception as e:
                    last_error = traceback.format_exc()
                    log(f"ERROR in {browser_name}: {e}")
                    log(last_error)

                log(f"Sleeping {SLEEP_BETWEEN_BROWSERS // 60} min after {browser_name}...")
                if not sleep_with_stop(SLEEP_BETWEEN_BROWSERS, f"sleep after {browser_name}"):
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

@app.route('/compress-pdf', methods=['POST', 'OPTIONS'])
def compress_pdf():
    if request.method == 'OPTIONS':
        return '', 200

    key = request.args.get("key")
    if key != SECRET_KEY:
        return "Unauthorized", 401

    pdf_data = request.get_data()
    if not pdf_data:
        return jsonify({"error": "No PDF data received"}), 400

    original_kb = len(pdf_data) // 1024

    # Try Ghostscript — best compression (compresses embedded images too)
    gs_candidates = [
        r"C:\Program Files\gs\gs10.04.0\bin\gswin64c.exe",
        r"C:\Program Files\gs\gs10.03.1\bin\gswin64c.exe",
        r"C:\Program Files\gs\gs10.02.1\bin\gswin64c.exe",
        "gswin64c", "gswin32c", "gs",
    ]
    import tempfile, os

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f_in:
        f_in.write(pdf_data)
        in_path = f_in.name
    out_path = in_path + "_out.pdf"

    try:
        for gs_exe in gs_candidates:
            try:
                result = subprocess.run(
                    [gs_exe, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
                     "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dQUIET", "-dBATCH",
                     f"-sOutputFile={out_path}", in_path],
                    timeout=120, capture_output=True
                )
                if result.returncode == 0 and os.path.exists(out_path):
                    with open(out_path, "rb") as f:
                        compressed = f.read()
                    log(f"/compress-pdf: Ghostscript {original_kb}KB → {len(compressed)//1024}KB")
                    return compressed, 200, {"Content-Type": "application/pdf"}
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

        # Fallback — pypdf (compresses content streams; less effective on image-heavy PDFs)
        try:
            import pypdf, io
            reader = pypdf.PdfReader(io.BytesIO(pdf_data))
            writer = pypdf.PdfWriter()
            for page in reader.pages:
                page.compress_content_streams()
                writer.add_page(page)
            buf = io.BytesIO()
            writer.write(buf)
            compressed = buf.getvalue()
            log(f"/compress-pdf: pypdf {original_kb}KB → {len(compressed)//1024}KB")
            return compressed, 200, {"Content-Type": "application/pdf"}
        except Exception as e:
            log(f"/compress-pdf: pypdf failed — {e}")
            return jsonify({"error": f"No compression method available: {e}"}), 500
    finally:
        try: os.unlink(in_path)
        except: pass
        try: os.unlink(out_path)
        except: pass


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
        "message": "Browser loop started: Ulaa → Chrome → AVG Secure → Edge → Brave → JioSphere"
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
        "message": "Stop signal sent. Loop will exit at the next checkpoint."
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
    print(f"  BROWSER ORDER  (trigger method shown):")
    for name, _, exe, cdp_port in BROWSERS:
        exists = "✅" if os.path.exists(exe) else "❌ NOT FOUND"
        trigger = f"CDP port {cdp_port}" if cdp_port else "Tab+Enter keyboard"
        print(f"    → {name:<15}  {exists}  [{trigger}]")
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
