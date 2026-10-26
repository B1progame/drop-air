"""Native Windows desktop shell for the Drop Air web interface."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

# In frozen builds QtCore.pyd must resolve Qt and Shiboken DLLs from this
# application's bundle, even when another Qt installation is on the host PATH.
_DLL_SEARCH_HANDLES = []
if getattr(sys, "frozen", False) and hasattr(os, "add_dll_directory"):
    bundle_root = Path(sys._MEIPASS)
    for directory in (bundle_root / "PySide6", bundle_root / "shiboken6"):
        if directory.is_dir():
            _DLL_SEARCH_HANDLES.append(os.add_dll_directory(str(directory)))

from PySide6.QtCore import QObject, QStandardPaths, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QMessageBox, QSystemTrayIcon
from werkzeug.serving import make_server

import app as backend


APP_ID = "B1progame.DropAir.Desktop"
ROLLBACK_ACTIVE_ENV = "DROP_AIR_ROLLBACK_ACTIVE"
PYINSTALLER_RESET_ENV = "PYINSTALLER_RESET_ENVIRONMENT"

STARTUP_SKELETON_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Loading Drop Air</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #171a1d; color: #edf2f4; font: 14px/1.45 "Segoe UI", sans-serif; }
    .shell { width: min(1180px, 100%); margin: auto; padding: 30px 24px; }
    .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
    .top { display: flex; align-items: center; gap: 12px; margin-bottom: 30px; }
    .mark { width: 48px; height: 48px; border-radius: 12px; }
    .title { width: 190px; height: 28px; border-radius: 6px; }
    .status { width: 110px; height: 24px; border-radius: 999px; margin-left: auto; }
    .layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; align-items: start; }
    .column { display: grid; gap: 16px; }
    .panel { overflow: hidden; border: 1px solid #373f43; border-radius: 8px; background: #202428; }
    .panel-head { display: flex; justify-content: space-between; align-items: center; min-height: 66px; padding: 14px 16px; border-bottom: 1px solid #373f43; }
    .heading { width: 112px; height: 18px; border-radius: 5px; }
    .detail { width: 184px; height: 12px; margin-top: 8px; border-radius: 4px; }
    .count { width: 58px; height: 24px; border-radius: 999px; }
    .drop { min-height: 250px; margin: 14px; border: 1px dashed #485257; border-radius: 8px; display: grid; align-content: center; justify-items: center; gap: 14px; }
    .drop-mark { width: 44px; height: 44px; border-radius: 10px; }
    .drop-copy { width: min(66%, 260px); height: 15px; border-radius: 5px; }
    .drop-action { width: 120px; height: 42px; border-radius: 8px; }
    .text-area { height: 124px; margin: 14px; border: 1px solid #373f43; border-radius: 8px; }
    .row { display: grid; grid-template-columns: 38px minmax(0, 1fr) 64px; align-items: center; gap: 10px; min-height: 66px; padding: 10px 14px; border-bottom: 1px solid #30373b; }
    .file-mark { width: 36px; height: 36px; border-radius: 8px; }
    .filename { width: min(78%, 230px); height: 15px; border-radius: 5px; }
    .metadata { width: 86px; height: 10px; margin-top: 7px; border-radius: 4px; }
    .action { width: 64px; height: 38px; border-radius: 8px; }
    .block { background: linear-gradient(105deg, #2b3034 30%, #3a4145 46%, #2b3034 62%); background-size: 240% 100%; animation: load-sheen 1.45s ease-in-out infinite; }
    @keyframes load-sheen { to { background-position: -140% 0; } }
    @media (max-width: 760px) { .shell { padding: 20px 16px; } .layout { grid-template-columns: minmax(0, 1fr); } .panel-head { min-height: 58px; } }
    @media (prefers-color-scheme: light) {
      body { background: #f5f7fb; color: #172033; }
      .panel { background: #fff; border-color: #dce5ee; }
      .panel-head, .row { border-color: #dce5ee; }
      .text-area { border-color: #dce5ee; }
      .drop { border-color: #c6d2da; }
      .block { background: linear-gradient(105deg, #e2e8ec 30%, #f1f4f6 46%, #e2e8ec 62%); background-size: 240% 100%; }
    }
    @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; } }
  </style>
</head>
<body>
  <main class="shell" aria-busy="true" aria-label="Loading Drop Air">
    <span class="sr-only" role="status">Loading Drop Air</span>
    <header class="top"><div class="block mark"></div><div class="block title"></div><div class="block status"></div></header>
    <div class="layout">
      <div class="column">
        <section class="panel"><header class="panel-head"><div><div class="block heading"></div><div class="block detail"></div></div><div class="block count"></div></header><div class="drop"><div class="block drop-mark"></div><div class="block drop-copy"></div><div class="block drop-action"></div></div></section>
        <section class="panel"><header class="panel-head"><div class="block heading"></div><div class="block count"></div></header><div class="text-area"></div></section>
      </div>
      <section class="panel"><header class="panel-head"><div><div class="block heading"></div><div class="block detail"></div></div><div class="block count"></div></header><div class="row"><div class="block file-mark"></div><div><div class="block filename"></div><div class="block metadata"></div></div><div class="block action"></div></div><div class="row"><div class="block file-mark"></div><div><div class="block filename"></div><div class="block metadata"></div></div><div class="block action"></div></div><div class="row"><div class="block file-mark"></div><div><div class="block filename"></div><div class="block metadata"></div></div><div class="block action"></div></div></section>
    </div>
  </main>
</body>
</html>"""


def set_windows_app_id() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass


def icon_for_app() -> QIcon:
    path = backend.app_icon_path()
    return QIcon(str(path)) if path else QIcon()


class DropAirPage(QWebEnginePage):
    """Keep Drop Air routes in the app and open unrelated links in a browser."""

    def acceptNavigationRequest(self, url: QUrl, _navigation_type, is_main_frame: bool) -> bool:
        host = (url.host() or "").lower()
        if url.scheme() in {"http", "https"} and host not in {"127.0.0.1", "localhost", "::1"}:
            webbrowser.open(url.toString())
            return False
        return super().acceptNavigationRequest(url, _navigation_type, is_main_frame)


class StartupSignals(QObject):
    backend_ready = Signal(object, object, str)
    backend_failed = Signal(str)


class DropAirWindow(QMainWindow):
    def __init__(self, icon: QIcon):
        super().__init__()
        self._server = None
        self._server_thread = None
        self._shutting_down = False
        self._allow_close = False
        self._skeleton_loaded = False
        self._dashboard_url = ""
        self._dashboard_loading = False
        self._dashboard_healthy = False
        self.setWindowTitle(f"Drop Air {backend.APP_VERSION}")
        self.setWindowIcon(icon)
        self.resize(1092, 1255)

        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip(f"Drop Air {backend.APP_VERSION}")
        tray_menu = QMenu(self)
        open_action = tray_menu.addAction("Open Drop Air")
        open_action.triggered.connect(self._show_from_tray)
        tray_menu.addSeparator()
        quit_action = tray_menu.addAction("Quit Drop Air")
        quit_action.triggered.connect(self._quit_from_tray)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self._tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        if self._tray_available:
            self.tray_icon.show()

        self.web = QWebEngineView(self)
        self.web.setPage(DropAirPage(self.web))
        self.setCentralWidget(self.web)
        self.web.loadFinished.connect(self._page_load_finished)
        self.web.setHtml(STARTUP_SKELETON_HTML)

        reload_action = QAction("Reload", self)
        reload_action.setShortcut(QKeySequence.Refresh)
        reload_action.triggered.connect(self.web.reload)
        self.addAction(reload_action)

        profile = QWebEngineProfile.defaultProfile()
        profile.downloadRequested.connect(self._save_download)

    def _page_load_finished(self, success: bool) -> None:
        if not self._dashboard_loading:
            self._skeleton_loaded = True
            self._maybe_show_dashboard()
            return

        if success:
            self._dashboard_healthy = True
            mark_startup_healthy()
        elif launch_previous_version("The dashboard failed to load after startup."):
            self._allow_close = True
            self.close()
        else:
            QMessageBox.critical(self, "Drop Air could not load", "The dashboard failed to load.")

    def set_backend(self, server, server_thread: threading.Thread, url: str) -> None:
        self._server = server
        self._server_thread = server_thread
        self._dashboard_url = url
        schedule_startup_maintenance()
        self._maybe_show_dashboard()

    def _maybe_show_dashboard(self) -> None:
        if self._skeleton_loaded and self._dashboard_url and not self._dashboard_loading:
            self._dashboard_loading = True
            QTimer.singleShot(80, lambda: self.web.setUrl(QUrl(self._dashboard_url)))

    def show_startup_error(self, message: str) -> None:
        if launch_previous_version(message):
            if self._tray_available:
                self.tray_icon.showMessage(
                    "Drop Air recovered",
                    "Startup failed. Opening the last working version.",
                    QSystemTrayIcon.MessageIcon.Warning,
                )
            self._allow_close = True
            self.close()
            return
        QMessageBox.critical(self, "Drop Air could not start", message)

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_from_tray()

    def _quit_from_tray(self) -> None:
        self._allow_close = True
        self.close()

    def _save_download(self, download) -> None:
        suggested_name = download.downloadFileName() or "download"
        download_directory = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        if not download_directory:
            download_directory = str(Path.home() / "Downloads")
        Path(download_directory).mkdir(parents=True, exist_ok=True)
        download.setDownloadDirectory(download_directory)
        download.setDownloadFileName(suggested_name)
        download.accept()

    def closeEvent(self, event) -> None:
        if self._tray_available and not self._allow_close:
            self.hide()
            event.ignore()
            return

        if not self._shutting_down:
            self._shutting_down = True
            self.tray_icon.hide()
            if not self._dashboard_healthy:
                clear_startup_pending()
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
                backend.shutdown_drop_air()
                if self._server_thread is not None:
                    self._server_thread.join(timeout=2)
        event.accept()
        QApplication.instance().quit()


def start_backend():
    backend.initialize_log_file()
    backend.redirect_stdio_to_log_if_windowless()
    host = "0.0.0.0"
    port = backend.select_server_port(backend.DEFAULT_PORT)
    backend.ACTIVE_PORT = port
    settings = backend.get_settings()
    backend.configure_request_logging()

    server = make_server(host, port, backend.app, threaded=True)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.1},
        name="DropAirWebServer",
        daemon=True,
    )
    thread.start()

    local_url = backend.build_local_url(settings["access_code"])
    separator = "&" if "?" in local_url else "?"
    desktop_url = f"{local_url}{separator}view=transfers&desktop=1"
    print(f"Drop Air running on {backend.build_public_url(settings['access_code'])}")
    print(f"Desktop dashboard on {desktop_url}")
    return server, thread, desktop_url


def schedule_startup_maintenance() -> None:
    threading.Thread(
        target=lambda: backend.cleanup_uploads(force=True),
        name="DropAirStartupCleanup",
        daemon=True,
    ).start()


def rollback_paths() -> tuple[Path, Path, Path]:
    rollback_dir = backend.DATA_DIR / "rollback"
    return (
        rollback_dir / "DropAir.exe",
        rollback_dir / "startup-pending.txt",
        rollback_dir / "startup-failed.txt",
    )


def _version_parts(value: str) -> tuple[int, ...]:
    parts = []
    for part in value.lstrip("v").split("."):
        digits = "".join(character for character in part if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _is_rollback_process() -> bool:
    if os.environ.get(ROLLBACK_ACTIVE_ENV) == "1":
        return True
    try:
        return Path(sys.executable).resolve().parent == rollback_paths()[0].parent.resolve()
    except OSError:
        return False


def _read_marker(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def clear_startup_pending() -> None:
    _rollback_executable, pending_path, _failed_path = rollback_paths()
    pending_path.unlink(missing_ok=True)


def mark_startup_pending() -> None:
    if _is_rollback_process():
        return
    rollback_executable, pending_path, _failed_path = rollback_paths()
    if not rollback_executable.is_file():
        return
    try:
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text(backend.APP_VERSION, encoding="utf-8")
    except OSError:
        pass


def mark_startup_healthy() -> None:
    if _is_rollback_process():
        return
    _rollback_executable, pending_path, failed_path = rollback_paths()
    pending_path.unlink(missing_ok=True)
    failed_version = _read_marker(failed_path)
    if failed_version and _version_parts(backend.APP_VERSION) > _version_parts(failed_version):
        failed_path.unlink(missing_ok=True)


def launch_previous_version(reason: str) -> bool:
    if _is_rollback_process():
        return False
    rollback_executable, pending_path, failed_path = rollback_paths()
    if not rollback_executable.is_file():
        return False

    try:
        failed_path.parent.mkdir(parents=True, exist_ok=True)
        failed_path.write_text(f"{backend.APP_VERSION}\n{reason[:500]}", encoding="utf-8")
        pending_path.unlink(missing_ok=True)
        environment = os.environ.copy()
        environment[ROLLBACK_ACTIVE_ENV] = "1"
        environment[PYINSTALLER_RESET_ENV] = "1"
        subprocess.Popen(
            [str(rollback_executable)],
            cwd=str(rollback_executable.parent),
            close_fds=True,
            env=environment,
        )
        return True
    except OSError:
        failed_path.unlink(missing_ok=True)
        return False


def should_rollback_after_interrupted_startup() -> bool:
    if _is_rollback_process():
        return False
    rollback_executable, pending_path, failed_path = rollback_paths()
    if not rollback_executable.is_file():
        return False
    failed_version = _read_marker(failed_path)
    pending_version = _read_marker(pending_path)
    return backend.APP_VERSION in {failed_version, pending_version}


def main() -> int:
    if "--version" in sys.argv[1:]:
        print(f"Drop Air {backend.APP_VERSION}")
        return 0

    if should_rollback_after_interrupted_startup():
        if launch_previous_version("The previous startup did not finish."):
            return 0
        clear_startup_pending()

    set_windows_app_id()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Drop Air")
    app.setOrganizationName("Drop Air")
    backend.register_shutdown_cleanup()
    icon = icon_for_app()
    app.setWindowIcon(icon)

    window = DropAirWindow(icon)
    mark_startup_pending()
    startup_signals = StartupSignals()
    startup_results = []
    startup_signals.backend_ready.connect(window.set_backend)
    startup_signals.backend_failed.connect(window.show_startup_error)
    window.show()

    def initialize_backend() -> None:
        try:
            result = start_backend()
            startup_results.append(result)
            startup_signals.backend_ready.emit(*result)
        except Exception as exc:
            startup_signals.backend_failed.emit(str(exc))

    startup_thread = threading.Thread(
        target=initialize_backend,
        name="DropAirBackendStartup",
        daemon=True,
    )
    startup_thread.start()
    exit_code = app.exec()
    startup_thread.join()

    # If the user quit while the skeleton was still showing, clean up a backend
    # that finished after the Qt event loop had stopped dispatching signals.
    if startup_results and window._server is None:
        server, server_thread, _url = startup_results[0]
        server.shutdown()
        server.server_close()
        backend.shutdown_drop_air()
        server_thread.join(timeout=2)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
