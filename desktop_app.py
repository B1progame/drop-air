"""Native Windows desktop shell for the Drop Air web interface."""

from __future__ import annotations

import ctypes
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox
from werkzeug.serving import make_server

import app as backend


APP_ID = "B1progame.DropAir.Desktop"


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

    def acceptNavigationRequest(self, url: QUrl, navigation_type, is_main_frame: bool) -> bool:
        host = (url.host() or "").lower()
        if url.scheme() in {"http", "https"} and host not in {"127.0.0.1", "localhost", "::1"}:
            webbrowser.open(url.toString())
            return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)


class DropAirWindow(QMainWindow):
    def __init__(self, server, server_thread: threading.Thread, url: str, icon: QIcon):
        super().__init__()
        self._server = server
        self._server_thread = server_thread
        self._shutting_down = False
        self.setWindowTitle(f"Drop Air {backend.APP_VERSION}")
        self.setWindowIcon(icon)
        self.resize(1280, 900)

        self.web = QWebEngineView(self)
        self.web.setPage(DropAirPage(self.web))
        self.setCentralWidget(self.web)
        self.web.setUrl(QUrl(url))

        reload_action = QAction("Reload", self)
        reload_action.setShortcut(QKeySequence.Refresh)
        reload_action.triggered.connect(self.web.reload)
        self.addAction(reload_action)

        profile = QWebEngineProfile.defaultProfile()
        profile.downloadRequested.connect(self._save_download)

    def _save_download(self, download) -> None:
        suggested_name = download.downloadFileName() or "download"
        path, _ = QFileDialog.getSaveFileName(self, "Save file", suggested_name)
        if not path:
            download.cancel()
            return
        destination = Path(path)
        download.setDownloadDirectory(str(destination.parent))
        download.setDownloadFileName(destination.name)
        download.accept()

    def closeEvent(self, event) -> None:
        if not self._shutting_down:
            self._shutting_down = True
            self._server.shutdown()
            self._server.server_close()
            backend.shutdown_drop_air()
            self._server_thread.join(timeout=2)
        event.accept()


def start_backend():
    backend.initialize_log_file()
    backend.redirect_stdio_to_log_if_windowless()
    host = "0.0.0.0"
    port = backend.select_server_port(backend.DEFAULT_PORT)
    backend.ACTIVE_PORT = port
    settings = backend.get_settings()
    backend.register_shutdown_cleanup()
    backend.cleanup_uploads(force=True)
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
    desktop_url = f"{local_url}{separator}view=transfers"
    print(f"Drop Air running on {backend.build_public_url(settings['access_code'])}")
    print(f"Desktop dashboard on {desktop_url}")
    return server, thread, desktop_url


def main() -> int:
    set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("Drop Air")
    app.setOrganizationName("Drop Air")
    icon = icon_for_app()
    app.setWindowIcon(icon)

    try:
        server, thread, url = start_backend()
    except Exception as exc:
        QMessageBox.critical(None, "Drop Air could not start", str(exc))
        return 1

    window = DropAirWindow(server, thread, url, icon)
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
