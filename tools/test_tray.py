"""Real tray apps: close the window, wait, check sharing is off, reopen, check it is back.

    python tools/test_tray.py        (needs the builds in dist\\; takes about 3 minutes)
"""

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from discovery import route_ipv4  # noqa: E402

user32 = ctypes.windll.user32
PORT = 23600
failures = 0


def check(label, ok, detail=""):
    global failures
    failures += not ok
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"  -> {detail}"), flush=True)


def request(url, body=None):
    headers = {"Content-Type": "application/json"} if body is not None else {}
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def windows():
    """Visible top-level windows titled exactly 'Drop Air' that belong to the test copy."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(handle, _):
        title = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(handle, title, 64)
        if title.value == "Drop Air" and user32.IsWindowVisible(handle) and handle not in existing:
            found.append(handle)
        return True

    user32.EnumWindows(visit, 0)
    return found


for name, program in [("rust", ROOT / "dist/rust/DropAir.exe"), ("python", ROOT / "dist/python/DropAir/DropAir.exe")]:
    existing = set()
    existing = set(windows())  # never touch a Drop Air window that was already open
    data = Path(tempfile.mkdtemp(prefix=f"dropair-tray-{name}-"))
    app = subprocess.Popen([str(program)], env={**os.environ, "DROP_AIR_DATA_DIR": str(data), "PORT": str(PORT)})
    host, phone = f"http://127.0.0.1:{PORT}", f"http://{route_ipv4()}:{PORT}"
    try:
        for _ in range(200):
            try:
                state = request(host + "/api/state")[1]
                if windows():
                    break
            except OSError:
                pass
            time.sleep(0.1)
        request(host + "/api/settings", {"tray_sleep_minutes": 1})
        key = state["session_key"]
        for handle in windows():
            user32.PostMessageW(handle, 0x10, 0, 0)  # close button
        time.sleep(5)
        check(f"{name}: window closed to tray", not windows() and app.poll() is None)
        check(f"{name}: link still works at first", request(f"{phone}/api/state?k={key}")[0] == 200)
        time.sleep(65)
        status, body = request(f"{phone}/api/state?k={key}")
        check(f"{name}: sharing off after 1 minute", status == 503 and body["error"] == "sharing is off", (status, body))
        check(f"{name}: app stays in the tray", app.poll() is None)
        started = time.perf_counter()
        request(host + "/api/admin/show", {})  # what opening from the tray or the shortcut does
        for _ in range(100):
            new = request(host + "/api/state")[1]
            if request(f"{phone}/api/state?k={new['session_key']}")[0] == 200:
                break
            time.sleep(0.05)
        took = time.perf_counter() - started
        check(f"{name}: back on after opening ({took:.2f}s)", took < 3, took)
        check(f"{name}: new QR code with full time", new["session_key"] != key and new["seconds_remaining"] > 290, new["seconds_remaining"])
        check(f"{name}: old link stays dead", request(f"{phone}/api/state?k={key}")[0] == 404)
        for _ in range(100):
            if windows():
                break
            time.sleep(0.1)
        check(f"{name}: window is open again", bool(windows()))
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)
        time.sleep(2)

sys.exit(1 if failures else 0)
