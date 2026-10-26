"""Native Windows desktop shell for the Drop Air web interface."""

from __future__ import annotations

import ctypes
import importlib
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

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))


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

from PySide6.QtCore import QObject, QStandardPaths, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QMessageBox, QStackedWidget, QSystemTrayIcon, QWidget


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

    def __init__(self, parent=None, icon: QIcon | None = None):
        super().__init__(parent)
        self.setAccessibleName("Loading Drop Air")
        self.setMinimumSize(560, 520)
        self.setAutoFillBackground(True)
        self._brand_icon = icon.pixmap(42, 42) if icon and not icon.isNull() else None
        self._loading_phase = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(50)
        self._loading_timer.timeout.connect(self._advance_loading)
        self._loading_timer.start()

    def _advance_loading(self) -> None:
        self._loading_phase = (self._loading_phase + 1) % 36
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#171a1d"))
        width = self.width()
        margin = 24 if width > 760 else 16
        gap = 16
        usable = max(1, width - margin * 2)
        left_width = max(240, (usable - gap) // 2)
        right_x = margin + left_width + gap
        right_width = max(160, usable - left_width - gap)
        bone = QColor("#30363a")
        bone_light = QColor("#3b4246")
        panel = QColor("#202428")
        edge = QColor("#373f43")
        accent = QColor("#21bac8")

        def rounded(x, y, w, h, color, radius=6):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(int(x), int(y), int(w), int(h), radius, radius)

        def panel_box(x, y, w, h):
            painter.setPen(QPen(edge, 1))
            painter.setBrush(panel)
            painter.drawRoundedRect(int(x), int(y), int(w), int(h), 8, 8)

        rounded(margin, 24, 42, 42, bone_light, 10)
        if self._brand_icon is not None:
            painter.drawPixmap(margin, 24, self._brand_icon)
        painter.setPen(QColor("#edf2f4"))
        title_font = QFont(painter.font())
        title_font.setPointSize(17)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.drawText(margin + 56, 44, "Drop Air")
        subtitle_font = QFont(painter.font())
        subtitle_font.setPointSize(9)
        subtitle_font.setWeight(QFont.Weight.Normal)
        painter.setFont(subtitle_font)
        painter.setPen(QColor("#9ba8ad"))
        painter.drawText(margin + 56, 62, "Getting things ready...")
        rounded(max(margin, width - margin - 110), 34, 110, 24, bone, 12)
        # A single moving accent makes the whole skeleton feel active without
        # adding motion to every placeholder or delaying the first paint.
        track_x, track_y, track_width = margin + 56, 72, min(190, max(80, width - margin * 2 - 220))
        rounded(track_x, track_y, track_width, 2, QColor("#293135"), 1)
        segment_width = max(28, track_width // 4)
        segment_x = track_x + int((track_width + segment_width) * self._loading_phase / 36) - segment_width
        rounded(segment_x, track_y, segment_width, 2, accent, 1)
        # Mirror the dashboard: the connection strip spans both columns, then
        # Send / Incoming sit side-by-side with Text Relay beneath Send.
        connect_y = 96
        connect_height = 100
        panel_box(margin, connect_y, usable, connect_height)
        qr_x, qr_y, qr_size = margin + 16, connect_y + 16, 68
        rounded(qr_x, qr_y, qr_size, qr_size, QColor("#dce3e5"), 6)
        for finder_x, finder_y in ((qr_x + 7, qr_y + 7), (qr_x + 43, qr_y + 7), (qr_x + 7, qr_y + 43)):
            rounded(finder_x, finder_y, 18, 18, QColor("#30383c"), 2)
            rounded(finder_x + 4, finder_y + 4, 10, 10, QColor("#dce3e5"), 1)
        rounded(margin + 100, connect_y + 24, min(212, usable - 260), 18, bone, 5)
        rounded(margin + 100, connect_y + 52, min(310, usable - 260), 12, bone, 4)
        rounded(margin + 100, connect_y + 74, min(235, usable - 260), 10, bone, 4)
        rounded(margin + usable - 136, connect_y + 32, 112, 36, QColor("#23464a"), 9)

        panel_y = connect_y + connect_height + 14
        upload_height = max(300, int(self.height() * .29))
        panel_box(margin, panel_y, left_width, upload_height)
        rounded(margin + 16, panel_y + 16, 112, 18, bone, 5)
        rounded(margin + 16, panel_y + 42, min(184, left_width - 32), 12, bone, 4)
        rounded(margin + left_width - 74, panel_y + 22, 58, 24, bone, 12)
        drop_y = panel_y + 82
        drop_h = max(120, upload_height - 96)
        painter.setPen(QPen(QColor("#28565b"), 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(int(margin + 8), int(drop_y), int(left_width - 16), int(drop_h), 8, 8)
        center_x = margin + left_width / 2
        rounded(center_x - 22, drop_y + drop_h * .28, 44, 44, bone_light, 10)
        # Upload-arrow silhouette, matching the actual dropzone action.
        painter.setPen(QPen(accent, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        arrow_x, arrow_y = int(center_x), int(drop_y + drop_h * .28)
        painter.drawLine(arrow_x, arrow_y + 30, arrow_x, arrow_y + 14)
        painter.drawLine(arrow_x, arrow_y + 14, arrow_x - 6, arrow_y + 20)
        painter.drawLine(arrow_x, arrow_y + 14, arrow_x + 6, arrow_y + 20)
        painter.drawLine(arrow_x - 9, arrow_y + 29, arrow_x - 9, arrow_y + 34)
        painter.drawLine(arrow_x - 9, arrow_y + 34, arrow_x + 9, arrow_y + 34)
        rounded(center_x - min(130, left_width * .33), drop_y + drop_h * .28 + 58, min(260, left_width * .66), 15, bone, 5)
        rounded(center_x - 60, drop_y + drop_h * .28 + 86, 120, 42, bone, 8)

        text_y = panel_y + upload_height + gap
        panel_box(margin, text_y, left_width, 244)
        rounded(margin + 16, text_y + 18, 112, 18, bone, 5)
        rounded(margin + left_width - 74, text_y + 15, 58, 24, bone, 12)
        painter.setPen(QPen(edge, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(int(margin + 8), int(text_y + 56), int(left_width - 16), 112, 8, 8)
        rounded(margin + 20, text_y + 72, min(280, left_width - 56), 10, bone, 4)
        rounded(margin + 20, text_y + 92, min(190, left_width - 56), 10, bone, 4)
        rounded(margin + 20, text_y + 126, min(240, left_width - 56), 10, bone, 4)
        rounded(margin + 20, text_y + 190, 104, 34, QColor("#23464a"), 8)

        list_height = 278
        panel_box(right_x, panel_y, right_width, list_height)
        rounded(right_x + 16, panel_y + 16, 112, 18, bone, 5)
        rounded(right_x + 16, panel_y + 42, min(184, right_width - 32), 12, bone, 4)
        for index in range(3):
            row_y = panel_y + 68 + index * 66
            if row_y + 64 > panel_y + list_height:
                break
            if index:
                painter.setPen(QPen(edge, 1))
                painter.drawLine(int(right_x + 1), int(row_y), int(right_x + right_width - 1), int(row_y))
            rounded(right_x + 14, row_y + 12, 36, 36, bone_light, 8)
            rounded(right_x + 62, row_y + 14, min(230, right_width - 120), 15, bone, 5)
            rounded(right_x + 62, row_y + 36, min(86, right_width - 120), 10, bone, 4)
        painter.end()


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
        self._startup_skeleton = StartupSkeleton(self, icon)
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
        if success:
            self._dashboard_healthy = True
            if self.web is not None:
                self._startup_stack.setCurrentWidget(self.web)
                self._startup_skeleton._loading_timer.stop()
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
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView

        class DropAirPage(QWebEnginePage):
            def acceptNavigationRequest(self, target_url: QUrl, navigation_type, is_main_frame: bool) -> bool:
                host = (target_url.host() or "").lower()
                if target_url.scheme() in {"http", "https"} and host not in {"127.0.0.1", "localhost", "::1"}:
                    webbrowser.open(target_url.toString())
                    return False
                return super().acceptNavigationRequest(target_url, navigation_type, is_main_frame)

        self.web = QWebEngineView(self)
        self.web.setPage(DropAirPage(self.web))
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
    backend = importlib.import_module("app")
    from werkzeug.serving import make_server

    backend.initialize_log_file()
    backend.redirect_stdio_to_log_if_windowless()
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
