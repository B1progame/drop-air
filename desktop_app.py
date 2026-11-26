"""Windows tray app. The window runs in its own process, so closing it frees all its memory."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

TRAY_IDLE_EXIT_SECONDS = 600


def dark_title_bar() -> None:
    handle = ctypes.windll.user32.FindWindowW(None, "Drop Air")
    if handle:
        enabled = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))


def run_window(url: str, storage: str) -> None:
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window("Drop Air", url, width=1080, height=860, min_size=(420, 560), background_color="#121316")
    window.events.shown += dark_title_bar
    webview.start(private_mode=False, storage_path=storage)


def activate_running(data_dir: Path) -> bool:
    """Ask an already running Drop Air to show its window."""
    try:
        port = int((data_dir / "port").read_text(encoding="ascii"))
        with urlopen(Request(f"http://127.0.0.1:{port}/api/admin/show", data=b""), timeout=0.7):
            return True
    except (OSError, ValueError):
        return False


class Shell:
    def __init__(self, backend):
        self.backend = backend
        self.window: subprocess.Popen | None = None
        self.icon = None
        self.lock = threading.Lock()
        self.hidden_since: float | None = None

    def window_open(self) -> bool:
        return self.window is not None and self.window.poll() is None

    def show(self, *_args) -> None:
        with self.lock:
            self.hidden_since = None
            if self.window_open():
                user32 = ctypes.windll.user32
                handle = user32.FindWindowW(None, "Drop Air")
                if handle:
                    user32.ShowWindow(handle, 9)  # SW_RESTORE
                    user32.SetForegroundWindow(handle)
                return
            command = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve())]
            command += ["--window", self.backend.local_url(), str(self.backend.DATA_DIR / "webview")]
            self.window = subprocess.Popen(command, env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})

    def quit(self, *_args) -> None:
        if self.window_open():
            self.window.terminate()
        self.backend.shutdown()
        if self.icon:
            self.icon.visible = False
        os._exit(0)

    def watch(self) -> None:
        """Exit after ten minutes hidden, unless phones are still connected."""
        while True:
            time.sleep(5)
            if self.window_open():
                continue
            self.hidden_since = self.hidden_since or time.monotonic()
            if time.monotonic() - self.hidden_since > TRAY_IDLE_EXIT_SECONDS and not self.backend.CLIENTS:
                self.quit()


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--window":
        run_window(sys.argv[2], sys.argv[3])
        return 0

    import app as backend

    if "--version" in sys.argv[1:]:
        print(f"Drop Air {backend.APP_VERSION}")
        return 0
    if activate_running(backend.DATA_DIR):
        return 0

    import pystray
    from PIL import Image

    shell = Shell(backend)
    backend.on_show = shell.show
    backend.on_quit = shell.quit
    backend.start_server()
    shell.icon = pystray.Icon(
        "DropAir",
        Image.open(backend.WEB_DIR / "logo.png"),
        f"Drop Air {backend.APP_VERSION}",
        pystray.Menu(
            pystray.MenuItem("Open Drop Air", shell.show, default=True),
            pystray.MenuItem("Quit Drop Air", shell.quit),
        ),
    )
    shell.show()
    threading.Thread(target=shell.watch, daemon=True).start()
    shell.icon.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
