"""Native Windows desktop shell for the Drop Air web interface."""

from __future__ import annotations

import ctypes
import base64
import importlib
import os
import subprocess
import sys
import threading
import time
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

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
PROCESS_START_TIME = time.perf_counter()


def read_app_version() -> str:
    override = os.environ.get("DROP_AIR_VERSION", "").strip()
    if override:
        return override.lstrip("v")
    for candidate in (BUNDLE_DIR / "VERSION", APP_DIR / "VERSION", Path(__file__).resolve().parent / "VERSION"):
        try:
            version = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if version:
            return version.lstrip("v")
    return "1.0.0"


APP_VERSION = read_app_version()
backend = None

# Keep the version check ahead of every Qt/backend import. PyInstaller's
# bootstrap still starts, but diagnostics no longer load Chromium, Flask, or PIL.
if "--version" in sys.argv[1:]:
    print(f"Drop Air {APP_VERSION}")
    raise SystemExit(0)

from PySide6.QtCore import QObject, QBuffer, QIODevice, QStandardPaths, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QMessageBox, QStackedWidget, QSystemTrayIcon, QWidget

QT_IMPORT_FINISHED_AT = time.perf_counter()


APP_ID = "B1progame.DropAir.Desktop"
ROLLBACK_ACTIVE_ENV = "DROP_AIR_ROLLBACK_ACTIVE"
PYINSTALLER_RESET_ENV = "PYINSTALLER_RESET_ENVIRONMENT"

def set_windows_app_id() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass


def icon_for_app() -> QIcon:
    for root in (BUNDLE_DIR, APP_DIR):
        path = root / "assets" / "icon" / "drop_air_brand.ico"
        if path.is_file():
            return QIcon(str(path))
    return QIcon()


class StartupSkeleton(QWidget):
    """Paint the branded loading frame with Qt widgets, before Chromium is imported."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Loading Drop Air")
        self.setMinimumSize(560, 520)
        self.setAutoFillBackground(True)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#171a1d"))
        width = self.width()
        height = self.height()
        card_width = min(560, max(280, width - 40))
        card_height = 116
        gap = 20
        total_height = card_height * 3 + gap * 2
        left = (width - card_width) / 2
        top = (height - total_height) / 2
        bone = QColor("#303437")
        bone_light = QColor("#373c40")
        panel = QColor("#202428")
        edge = QColor("#30373b")

        def rounded(x, y, w, h, color, radius=6):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(int(x), int(y), int(w), int(h), radius, radius)

        def panel_box(x, y, w, h):
            painter.setPen(QPen(edge, 1))
            painter.setBrush(panel)
            painter.drawRoundedRect(int(x), int(y), int(w), int(h), 8, 8)

        title_widths = (218, 204, 232)
        line_widths = ((330, 394, 286), (352, 410, 318), (310, 420, 292))
        text_x = left + 94
        for index in range(3):
            row_y = top + index * (card_height + gap)
            panel_box(left, row_y, card_width, card_height)
            rounded(left + 22, row_y + 30, 56, 56, bone_light, 28)
            rounded(text_x, row_y + 18, min(title_widths[index], card_width - 122), 15, bone, 5)
            for line_index, line_width in enumerate(line_widths[index]):
                rounded(text_x, row_y + 48 + line_index * 13, min(line_width, card_width - 122), 4, bone_light, 2)
        painter.end()


class StartupSignals(QObject):
    backend_ready = Signal(object, object, str)
    backend_failed = Signal(str)


class ClipboardBridge(QObject):
    """Read clipboard data after an explicit click in the desktop dashboard."""

    @Slot(result="QVariantMap")
    def readClipboard(self) -> dict:
        mime = QApplication.clipboard().mimeData()
        result = {"text": mime.text() if mime.hasText() else "", "image": ""}
        if mime.hasImage():
            image = mime.imageData()
            if hasattr(image, "toImage"):
                image = image.toImage()
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if image.save(buffer, "PNG"):
                result["image"] = base64.b64encode(bytes(buffer.data())).decode("ascii")
            buffer.close()
        return result


class DropAirWindow(QMainWindow):
    def __init__(self, icon: QIcon):
        super().__init__()
        self._server = None
        self._server_thread = None
        self._shutting_down = False
        self._allow_close = False
        self._dashboard_url = ""
        self._dashboard_healthy = False
        self.setWindowTitle(f"Drop Air {APP_VERSION}")
        self.setWindowIcon(icon)
        self.resize(1092, 1255)

        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip(f"Drop Air {APP_VERSION}")
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

        self.web = None
        self._startup_skeleton = StartupSkeleton(self)
        self._startup_stack = QStackedWidget(self)
        self._startup_stack.addWidget(self._startup_skeleton)
        self.setCentralWidget(self._startup_stack)

        reload_action = QAction("Reload", self)
        reload_action.setShortcut(QKeySequence.Refresh)
        reload_action.triggered.connect(self._reload_page)
        self.addAction(reload_action)

    def _reload_page(self) -> None:
        if self.web is not None:
            self.web.reload()

    def _page_load_finished(self, success: bool) -> None:
        if backend is not None:
            print(f"Startup profile: dashboard load finished={success} at {time.perf_counter() - PROCESS_START_TIME:.2f}s")
        if success:
            self._dashboard_healthy = True
            if self.web is not None:
                self._startup_stack.setCurrentWidget(self.web)
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

        # This slot runs only after the first skeleton paint and backend startup.
        # Importing WebEngine here keeps Chromium's initialization off the first frame.
        webengine_import_started = time.perf_counter()
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
        print(f"Startup profile: Qt WebEngine imports took {time.perf_counter() - webengine_import_started:.2f}s")

        class DropAirPage(QWebEnginePage):
            def acceptNavigationRequest(self, target_url: QUrl, navigation_type, is_main_frame: bool) -> bool:
                host = (target_url.host() or "").lower()
                if target_url.scheme() in {"http", "https"} and host not in {"127.0.0.1", "localhost", "::1"}:
                    webbrowser.open(target_url.toString())
                    return False
                return super().acceptNavigationRequest(target_url, navigation_type, is_main_frame)

        self.web = QWebEngineView(self)
        self.web.setPage(DropAirPage(self.web))
        from PySide6.QtWebChannel import QWebChannel

        self._clipboard_bridge = ClipboardBridge(self)
        self._web_channel = QWebChannel(self.web.page())
        self._web_channel.registerObject("clipboardBridge", self._clipboard_bridge)
        self.web.page().setWebChannel(self._web_channel)
        # The dashboard runs on a local HTTP origin. Clipboard API is unavailable
        # there, and Chromium also disables execCommand clipboard writes by default.
        self.web.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard,
            True,
        )
        self.web.loadFinished.connect(self._page_load_finished)
        self._startup_stack.addWidget(self.web)
        profile = QWebEngineProfile.defaultProfile()
        try:
            Path(self._download_directory()).mkdir(parents=True, exist_ok=True)
            profile.setDownloadPath(self._download_directory())
        except (OSError, AttributeError):
            pass
        profile.downloadRequested.connect(self._save_download)
        self.web.setUrl(QUrl(self._dashboard_url))

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

    @staticmethod
    def _download_directory() -> str:
        download_directory = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        if not download_directory:
            download_directory = str(Path.home() / "Downloads")
        return download_directory

    def _save_download(self, download) -> None:
        try:
            download_directory = self._download_directory()
            Path(download_directory).mkdir(parents=True, exist_ok=True)
            suggested_name = (download.downloadFileName() or "download").replace("\\", "/").rsplit("/", 1)[-1] or "download"
            download.setDownloadDirectory(download_directory)
            download.setDownloadFileName(suggested_name)
            download.accept()
        except Exception:
            # Exceptions escaping a Qt signal slot can terminate the desktop app.
            try:
                download.cancel()
            except Exception:
                pass

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
                if backend is not None:
                    backend.shutdown_drop_air()
                if self._server_thread is not None:
                    self._server_thread.join(timeout=2)
        event.accept()
        QApplication.instance().quit()


def start_backend():
    global backend
    backend_import_started = time.perf_counter()
    backend = importlib.import_module("app")
    from werkzeug.serving import make_server

    backend.initialize_log_file()
    backend.redirect_stdio_to_log_if_windowless()
    print(f"Startup profile: Python and Qt imported by {QT_IMPORT_FINISHED_AT - PROCESS_START_TIME:.2f}s")
    print(f"Startup profile: backend module import took {time.perf_counter() - backend_import_started:.2f}s")
    backend.register_shutdown_cleanup()
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
    backend.start_peer_discovery(port)
    print(f"Startup profile: local server ready at {time.perf_counter() - PROCESS_START_TIME:.2f}s")

    local_url = backend.build_local_url(settings["access_code"])
    separator = "&" if "?" in local_url else "?"
    desktop_url = f"{local_url}{separator}view=transfers&desktop=1"
    print(f"Drop Air running on {backend.build_public_url(settings['access_code'])}")
    print(f"Desktop dashboard on {desktop_url}")
    return server, thread, desktop_url


def schedule_startup_maintenance() -> None:
    if backend is None:
        return
    threading.Thread(
        target=lambda: backend.cleanup_uploads(force=True),
        name="DropAirStartupCleanup",
        daemon=True,
    ).start()


def rollback_paths() -> tuple[Path, Path, Path]:
    data_dir_override = os.environ.get("DROP_AIR_DATA_DIR", "").strip()
    if backend is not None:
        data_dir = Path(backend.DATA_DIR)
    elif data_dir_override:
        data_dir = Path(data_dir_override).expanduser()
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        data_dir = Path(os.environ["LOCALAPPDATA"]) / "DropAir"
    else:
        data_dir = Path.home() / "AppData" / "Local" / "DropAir" if os.name == "nt" else Path.home() / ".local" / "share" / "drop-air"
    rollback_dir = data_dir / "rollback"
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
        pending_path.write_text(APP_VERSION, encoding="utf-8")
    except OSError:
        pass


def mark_startup_healthy() -> None:
    if _is_rollback_process():
        return
    _rollback_executable, pending_path, failed_path = rollback_paths()
    pending_path.unlink(missing_ok=True)
    failed_version = _read_marker(failed_path)
    if failed_version and _version_parts(APP_VERSION) > _version_parts(failed_version):
        failed_path.unlink(missing_ok=True)


def launch_previous_version(reason: str) -> bool:
    if _is_rollback_process():
        return False
    rollback_executable, pending_path, failed_path = rollback_paths()
    if not rollback_executable.is_file():
        return False

    try:
        failed_path.parent.mkdir(parents=True, exist_ok=True)
        failed_path.write_text(f"{APP_VERSION}\n{reason[:500]}", encoding="utf-8")
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
    return APP_VERSION in {failed_version, pending_version}


def main() -> int:
    if should_rollback_after_interrupted_startup():
        if launch_previous_version("The previous startup did not finish."):
            return 0
        clear_startup_pending()

    set_windows_app_id()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Drop Air")
    app.setOrganizationName("Drop Air")
    icon = icon_for_app()
    app.setWindowIcon(icon)

    window = DropAirWindow(icon)
    mark_startup_pending()
    startup_signals = StartupSignals()
    startup_results = []
    startup_signals.backend_ready.connect(window.set_backend)
    startup_signals.backend_failed.connect(window.show_startup_error)
    window.show()
    startup_threads = []

    def initialize_backend() -> None:
        try:
            result = start_backend()
            startup_results.append(result)
            startup_signals.backend_ready.emit(*result)
        except Exception as exc:
            startup_signals.backend_failed.emit(str(exc))

    def start_backend_thread() -> None:
        startup_thread = threading.Thread(
            target=initialize_backend,
            name="DropAirBackendStartup",
            daemon=True,
        )
        startup_thread.start()
        startup_threads.append(startup_thread)

    # Let Qt paint the native skeleton once before importing Flask/Pillow and
    # spinning up the local server in the background.
    QTimer.singleShot(80, start_backend_thread)
    exit_code = app.exec()
    for startup_thread in startup_threads:
        startup_thread.join()

    # If the user quit while the skeleton was still showing, clean up a backend
    # that finished after the Qt event loop had stopped dispatching signals.
    if startup_results and window._server is None:
        server, server_thread, _url = startup_results[0]
        server.shutdown()
        server.server_close()
        if backend is not None:
            backend.shutdown_drop_air()
        server_thread.join(timeout=2)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
