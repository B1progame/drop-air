import os
import io
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from PIL import Image


os.environ.setdefault("DROP_AIR_DATA_DIR", tempfile.mkdtemp(prefix="drop-air-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402
import desktop_app  # noqa: E402


class DropAirReleaseUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.app.test_client()
        cls.template_source = (Path(__file__).resolve().parents[1] / "templates" / "index.html").read_text(encoding="utf-8")
        cls.update_template_source = (Path(__file__).resolve().parents[1] / "templates" / "update.html").read_text(encoding="utf-8")
        cls.installer_source = (Path(__file__).resolve().parents[1] / "installer" / "DropAir.iss").read_text(encoding="utf-8")

    def test_desktop_icon_uses_the_current_brand_icon(self):
        with patch.dict(os.environ, {"DROP_AIR_ICON": ""}):
            self.assertEqual(app.app_icon_path().name, "drop_air_brand.ico")
        self.assertIn('IconFilename: "{app}\\assets\\icon\\drop_air_brand.ico"', self.installer_source)

    def test_pyinstaller_build_uses_the_current_icon_and_desktop_entry(self):
        root = Path(__file__).resolve().parents[1]
        build_script = (root / "build_exe.ps1").read_text(encoding="utf-8")
        self.assertIn('IconPath = "assets\\icon\\drop_air_brand.ico"', build_script)
        self.assertIn("--icon $iconAbs", build_script)
        self.assertIn("--onedir", build_script)
        self.assertNotIn("--onefile", build_script)
        self.assertIn("--hidden-import app", build_script)
        self.assertIn("--hidden-import werkzeug.serving", build_script)
        self.assertIn('"${AppName}Core"', build_script)
        self.assertIn("$launcherCompiler.Source", build_script)
        self.assertIn('launcher\\native_launcher.c', build_script)
        self.assertIn('Resolve-Path "desktop_app.py"', build_script)
        self.assertNotIn('Get-ChildItem -Path "dist" -File', build_script)
        self.assertIn('IconPath = "assets\\icon\\drop_air_brand.ico"', (root / "build_installer.ps1").read_text(encoding="utf-8"))

    def test_desktop_downloads_use_the_system_downloads_folder_without_a_dialog(self):
        root = Path(__file__).resolve().parents[1]
        desktop_source = (root / "desktop_app.py").read_text(encoding="utf-8")
        self.assertIn("StandardLocation.DownloadLocation", desktop_source)
        self.assertIn("download.setDownloadDirectory", desktop_source)
        self.assertIn("profile.setDownloadPath", desktop_source)
        self.assertIn("download.accept()", desktop_source)
        self.assertIn("download.cancel()", desktop_source)
        self.assertNotIn("QFileDialog.getSaveFileName", desktop_source)

    def test_desktop_download_is_accepted_to_downloads_and_slot_errors_do_not_escape(self):
        class Download:
            def __init__(self):
                self.calls = []

            def downloadFileName(self):
                return "folder\\report.txt"

            def setDownloadDirectory(self, path):
                self.calls.append(("directory", path))

            def setDownloadFileName(self, name):
                self.calls.append(("name", name))

            def accept(self):
                self.calls.append(("accept",))

            def cancel(self):
                self.calls.append(("cancel",))

        with tempfile.TemporaryDirectory() as folder:
            download = Download()
            window = type("Window", (), {"_download_directory": lambda _self: folder})()
            desktop_app.DropAirWindow._save_download(window, download)
            self.assertEqual([call[0] for call in download.calls], ["directory", "name", "accept"])
            self.assertEqual(download.calls[1][1], "report.txt")

            class BrokenDownload(Download):
                def accept(self):
                    raise RuntimeError("simulated browser error")

            broken = BrokenDownload()
            desktop_app.DropAirWindow._save_download(window, broken)
            self.assertEqual(broken.calls[-1], ("cancel",))

    def test_reload_action_is_safe_before_web_view_exists(self):
        called = []
        desktop_app.DropAirWindow._reload_page(type("Window", (), {"web": None})())
        fake_web = type("Web", (), {"reload": lambda self: called.append(True)})()
        desktop_app.DropAirWindow._reload_page(type("Window", (), {"web": fake_web})())
        self.assertEqual(called, [True])

    def test_clicking_a_file_name_uses_the_browser_download_directly(self):
        source = self.template_source
        start = source.index("    function fileRow(")
        end = source.index("    const mobileTaskQuery", start)
        row_builder = source[start:end]
        self.assertNotIn("anchor.addEventListener(\"click\"", row_builder)
        self.assertNotIn("event.preventDefault()", row_builder)
        self.assertIn('link.download = file.name', row_builder)
        self.assertIn('link.click()', row_builder)
        self.assertIn('Downloading to your Downloads folder', row_builder)
        self.assertNotIn("XMLHttpRequest", row_builder)
        self.assertNotIn("Save File", row_builder)

    def test_copy_file_link_uses_secure_and_fallback_clipboard_paths(self):
        source = self.template_source
        start = source.index("    async function copyText(")
        end = source.index("\n    function commitTheme", start)
        copy_function = source[start:end]
        self.assertIn("navigator.clipboard.writeText(text)", copy_function)
        self.assertIn('document.execCommand("copy")', copy_function)
        self.assertIn('showToast("Copied to clipboard")', copy_function)
        self.assertIn('showToast("Could not copy. Select and copy the link manually.")', copy_function)
        self.assertIn('copyText(new URL(copyButton.dataset.url, window.location.href).toString(), copyButton)', source)
        self.assertIn("copy.dataset.url = absolute", source)

    def test_startup_defers_upload_cleanup_until_after_the_window_is_shown(self):
        root = Path(__file__).resolve().parents[1]
        desktop_source = (root / "desktop_app.py").read_text(encoding="utf-8")
        start = desktop_source.index("def start_backend():")
        end = desktop_source.index("\ndef schedule_startup_maintenance()", start)
        self.assertNotIn("cleanup_uploads(force=True)", desktop_source[start:end])
        self.assertIn("QTimer.singleShot", desktop_source)
        self.assertIn("schedule_startup_maintenance", desktop_source)
        main_source = desktop_source[desktop_source.index("def main() -> int:"):]
        self.assertLess(main_source.index("window.show()"), main_source.index("startup_thread.start()"))
        self.assertIn('name="DropAirBackendStartup"', main_source)
        self.assertIn("backend_ready.connect(window.set_backend)", main_source)
        start = desktop_source.index("def start_backend():")
        end = desktop_source.index("\ndef schedule_startup_maintenance()", start)
        self.assertIn('importlib.import_module("app")', desktop_source[start:end])
        self.assertIn("backend.register_shutdown_cleanup()", desktop_source[start:end])
        self.assertLess(desktop_source.index('if "--version" in sys.argv[1:]:', 0), desktop_source.index("from PySide6.QtCore"))

    def test_shutdown_signal_handlers_are_only_registered_on_main_thread(self):
        worker = threading.Thread()
        with (
            patch.object(app.threading, "current_thread", return_value=worker),
            patch.object(app.threading, "main_thread", return_value=threading.main_thread()),
            patch.object(app, "install_windows_console_close_handler"),
            patch.object(app.atexit, "register"),
            patch.object(app.signal, "signal") as signal_handler,
        ):
            app.register_shutdown_cleanup()
        signal_handler.assert_not_called()

    def test_desktop_paints_a_native_skeleton_before_importing_webengine(self):
        root = Path(__file__).resolve().parents[1]
        desktop_source = (root / "desktop_app.py").read_text(encoding="utf-8")
        self.assertIn("class StartupSkeleton(QWidget)", desktop_source)
        self.assertIn("self.setCentralWidget(StartupSkeleton(self))", desktop_source)
        self.assertLess(desktop_source.index("self.setCentralWidget(StartupSkeleton(self))"), desktop_source.index("from PySide6.QtWebEngineCore"))
        self.assertLess(desktop_source.index("window.show()"), desktop_source.index("QTimer.singleShot(80, start_backend_thread)"))
        self.assertNotIn("import app as backend", desktop_source)
        self.assertNotIn("from werkzeug.serving import make_server", desktop_source.split("def start_backend():", 1)[0])
        self.assertIn("loadFinished.connect(self._page_load_finished)", desktop_source)
        self.assertIn("mark_startup_healthy()", desktop_source)
        self.assertIn('self.setAccessibleName("Loading Drop Air")', desktop_source)

    def test_native_launcher_paints_before_spawning_the_full_desktop_app(self):
        root = Path(__file__).resolve().parents[1]
        launcher = (root / "launcher" / "native_launcher.c").read_text(encoding="utf-8")
        self.assertIn('CORE_EXE[] = L"DropAirCore.exe"', launcher)
        self.assertLess(launcher.index("UpdateWindow(startup_window)"), launcher.index('launch_core(L"", FALSE)'))
        self.assertIn("draw_skeleton", launcher.lower())
        self.assertIn("has_core_window()", launcher)
        self.assertIn("if self._tray_available and not self._allow_close:", (root / "desktop_app.py").read_text(encoding="utf-8"))

    def test_startup_failure_has_a_previous_version_fallback(self):
        root = Path(__file__).resolve().parents[1]
        installer = (root / "installer" / "DropAir.iss").read_text(encoding="utf-8")
        self.assertIn('DestDir: "{localappdata}\\DropAir\\rollback"', installer)
        self.assertIn("ShouldSaveRollbackBackup", installer)
        self.assertIn("startup-failed.txt", installer)
        self.assertIn("DROP_AIR_ROLLBACK_ACTIVE", (root / "desktop_app.py").read_text(encoding="utf-8"))
        self.assertIn("should_rollback_after_interrupted_startup", (root / "desktop_app.py").read_text(encoding="utf-8"))

    def test_startup_failure_launches_the_previous_version_once(self):
        with tempfile.TemporaryDirectory(prefix="drop-air-rollback-test-") as temp:
            data_dir = Path(temp)
            rollback_exe, pending, failed = (
                data_dir / "rollback" / "DropAir.exe",
                data_dir / "rollback" / "startup-pending.txt",
                data_dir / "rollback" / "startup-failed.txt",
            )
            rollback_exe.parent.mkdir(parents=True)
            rollback_exe.write_bytes(b"previous release")
            pending.write_text(desktop_app.APP_VERSION, encoding="utf-8")
            with patch.object(desktop_app, "backend", app), patch.object(app, "DATA_DIR", data_dir), patch.dict(
                os.environ, {desktop_app.ROLLBACK_ACTIVE_ENV: ""}
            ), patch("desktop_app.subprocess.Popen") as spawn:
                self.assertTrue(desktop_app.launch_previous_version("simulated startup failure"))
                self.assertEqual(failed.read_text(encoding="utf-8").splitlines()[0], desktop_app.APP_VERSION)
                self.assertFalse(pending.exists())
                child_env = spawn.call_args.kwargs["env"]
                self.assertEqual(child_env[desktop_app.ROLLBACK_ACTIVE_ENV], "1")
                self.assertEqual(child_env[desktop_app.PYINSTALLER_RESET_ENV], "1")
                self.assertTrue(desktop_app.should_rollback_after_interrupted_startup())

    def test_primary_and_danger_button_text_use_contrasting_theme_tokens(self):
        self.assertIn("--accent-ink:", self.template_source)
        self.assertIn("--danger-ink:", self.template_source)
        self.assertIn("color: var(--accent-ink)", self.template_source)
        self.assertIn("color: var(--danger-ink)", self.template_source)

    def test_index_contains_gui_hooks(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("data-qr-image", body)
        self.assertIn("qrModal", body)
        self.assertIn("openAdminAlert", body)
        self.assertIn("updateServerBtn", body)
        self.assertIn("connectionCount", body)
        self.assertIn("pasteBtn", body)
        self.assertIn("uploadLimitBadge", body)
        self.assertIn("setMaxUploadGb", body)
        self.assertIn("/api/connections", body)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertIn("delete-file", body)

    def test_responsive_connect_screen_uses_branded_link_and_qr_transfer_view(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('class="qr-connect-screen"', body)
        self.assertIn('data-copy-public', body)
        self.assertIn('data-transfer-view-link', body)
        self.assertIn('el.href = publicUrl', body)
        self.assertIn('view=transfers', body)
        self.assertIn("qrConnectQuery", body)
        self.assertIn(".qr-stage.generating .qr-build-canvas", body)
        self.assertIn("display: none;", body)
        self.assertNotIn("fetchQrModules", body)
        self.assertNotIn("drawQrModules", body)
        qr_src = unescape(re.search(r'<img data-qr-image[^>]+src="([^"]+)"', body).group(1))
        qr_params = parse_qs(urlparse(qr_src).query)
        self.assertIn("view=transfers", qr_params["url"][0])
        self.assertEqual(qr_params["url"][0], unescape(re.search(r'data-public-url>([^<]+)<', body).group(1)))
        logo = self.client.get("/static/brand/drop-air-logo.png")
        self.assertEqual(logo.status_code, 200)
        self.assertTrue(logo.mimetype.startswith("image/"))
        logo.close()
        key = app.session_snapshot()["key"]
        qr = self.client.get(f"/qr.png?url=http://192.168.1.4:8000/?k={key}&view=transfers")
        self.assertEqual(qr.status_code, 200)
        self.assertEqual(qr.mimetype, "image/png")
        self.assertTrue(qr.data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn("no-store", qr.headers.get("Cache-Control", ""))
        with Image.open(io.BytesIO(qr.data)) as qr_image:
            self.assertEqual(qr_image.getpixel((31, 31)), (255, 255, 255))
            self.assertEqual(qr_image.getpixel((32, 32)), (0, 0, 0))
        qr.close()
        svg = self.client.get("/qr.svg?url=https%3A%2F%2Fexample.test%2Fdrop%3Fk%3Dabc%26view%3Dtransfers")
        self.assertEqual(svg.status_code, 200)
        self.assertIn("no-store", svg.headers.get("Cache-Control", ""))
        self.assertIn('viewBox=', svg.get_data(as_text=True))
        svg.close()
        self.assertEqual(app.app_icon_path().name, "drop_air_brand.ico")

    def test_template_contains_text_viewer_and_animation_hooks(self):
        source = self.template_source
        self.assertIn("text-viewer", source)
        self.assertIn("Show previous text", source)
        self.assertIn("Show next text", source)
        self.assertIn("Collapse", source)
        self.assertIn("qr-refresh", source)
        self.assertIn("qr-spin", source)
        self.assertIn("qr-star", source)
        self.assertIn("qr-build-canvas", source)
        self.assertIn("playQrBuildAnimation", source)
        self.assertIn("drop-ripple", source)
        self.assertIn("drop-scan-line", source)
        self.assertIn("drop-scan", source)
        self.assertIn("drop-border-trace", source)
        self.assertIn("border-trace-run", source)
        self.assertIn("stroke-dasharray", source)
        self.assertIn("stroke-dashoffset", source)
        self.assertIn("stroke-dasharray: 2200 2200", source)
        self.assertIn("trace-top", source)
        self.assertIn("trace-right", source)
        self.assertIn("trace-bottom", source)
        self.assertIn("trace-left", source)
        self.assertNotIn(".dropzone.uploading .drop-scan", source)
        self.assertNotIn(".dropzone.active .drop-scan", source)
        self.assertNotIn("drop-cross", source)
        self.assertNotIn("drop-line-x", source)
        self.assertNotIn("drop-line-y", source)
        self.assertIn("flashDropTrace", source)
        self.assertIn("trashIcon", source)
        self.assertIn("deleteFile", source)
        self.assertIn("deleteTextItem", source)
        self.assertIn("Delete shared text", source)
        self.assertIn("upload-state", source)
        self.assertIn("heartbeatConnection", source)
        self.assertIn("pasteClipboard", source)
        self.assertIn("scheduleFilePoll", source)
        self.assertIn("scheduleTextPoll", source)
        self.assertIn("visibilitychange", source)
        self.assertIn("Math.min(2, files.length)", source)
        self.assertIn("lastTextState", source)
        self.assertIn("Clipboard blocked. Use Choose Files", source)
        self.assertIn("max_upload_gb", source)
        self.assertIn("postTextItem", source)
        self.assertIn("window.location.reload", source)
        self.assertIn("sessionSecondsRemaining + 1", source)
        self.assertNotIn("sessionSecondsRemaining - 20", source)

    def test_session_endpoint_returns_rotating_key_payload(self):
        key = app.session_snapshot()["key"]
        response = self.client.get(
            f"/api/session?k={key}",
            environ_overrides={"REMOTE_ADDR": "192.168.1.55", "HTTP_HOST": "192.168.1.2:8000"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload["key"]), 32)
        self.assertIn("qr_url", payload)
        self.assertIn(".png?", payload["qr_url"])
        self.assertIn("qr_svg_url", payload)
        self.assertIn("seconds_remaining", payload)

    def test_session_endpoint_syncs_old_key_after_rotation(self):
        old_key = app.session_snapshot()["key"]
        previous_expires = app.SESSION_EXPIRES_AT
        try:
            app.SESSION_EXPIRES_AT = time.time() - 1
            response = self.client.get(
                f"/api/session?k={old_key}",
                environ_overrides={"REMOTE_ADDR": "192.168.1.56", "HTTP_HOST": "192.168.1.2:8000"},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertNotEqual(payload["key"], old_key)
            self.assertIn(f"k={payload['key']}", payload["public_url"])
            self.assertIn("view=transfers", payload["public_url"])
        finally:
            app.SESSION_EXPIRES_AT = max(previous_expires, time.time() + app.SESSION_TTL_SECONDS)

    def test_file_api_accepts_previous_session_key_during_grace_period(self):
        old_key = app.session_snapshot()["key"]
        previous_expires = app.SESSION_EXPIRES_AT
        env = {"REMOTE_ADDR": "192.168.1.62", "HTTP_HOST": "192.168.1.2:8000"}
        try:
            app.SESSION_EXPIRES_AT = time.time() - 1
            response = self.client.get(f"/api/files?k={old_key}", environ_overrides=env)
            self.assertEqual(response.status_code, 200)
            self.assertNotEqual(app.session_snapshot()["key"], old_key)
        finally:
            app.SESSION_EXPIRES_AT = max(previous_expires, time.time() + app.SESSION_TTL_SECONDS)

    def test_text_api_round_trip(self):
        key = app.session_snapshot()["key"]
        env = {"REMOTE_ADDR": "192.168.1.55", "HTTP_HOST": "192.168.1.2:8000"}
        post_response = self.client.post(
            f"/api/text?k={key}",
            json={"text": "line 1\nline 2\nline 3\nline 4\nline 5\nline 6"},
            environ_overrides=env,
        )
        self.assertEqual(post_response.status_code, 200)
        get_response = self.client.get(f"/api/text?k={key}", environ_overrides=env)
        self.assertEqual(get_response.status_code, 200)
        items = get_response.get_json()["items"]
        self.assertGreaterEqual(len(items), 1)
        self.assertIn("line 6", items[0]["text"])

    def test_delete_specific_text_item(self):
        key = app.session_snapshot()["key"]
        env = {"REMOTE_ADDR": "192.168.1.57", "HTTP_HOST": "192.168.1.2:8000"}
        post_response = self.client.post(f"/api/text?k={key}", json={"text": "delete me"}, environ_overrides=env)
        self.assertEqual(post_response.status_code, 200)
        item_id = post_response.get_json()["item"]["id"]
        delete_response = self.client.delete(f"/api/text/{item_id}?k={key}", environ_overrides=env)
        self.assertEqual(delete_response.status_code, 200)
        get_response = self.client.get(f"/api/text?k={key}", environ_overrides=env)
        ids = [item["id"] for item in get_response.get_json()["items"]]
        self.assertNotIn(item_id, ids)

    def test_delete_specific_file(self):
        key = app.session_snapshot()["key"]
        env = {"REMOTE_ADDR": "192.168.1.58", "HTTP_HOST": "192.168.1.2:8000"}
        app.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        target = app.UPLOAD_DIR / "delete-me.txt"
        target.write_text("bye", encoding="utf-8")
        delete_response = self.client.delete(f"/api/files/delete-me.txt?k={key}", environ_overrides=env)
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(target.exists())

    def test_authenticated_download_returns_the_original_file(self):
        key = app.session_snapshot()["key"]
        env = {"REMOTE_ADDR": "192.168.1.64", "HTTP_HOST": "192.168.1.2:8000"}
        app.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        target = app.UPLOAD_DIR / "download-smoke.txt"
        target.write_bytes(b"authenticated download smoke test")
        try:
            response = self.client.get(f"/files/download-smoke.txt?k={key}", environ_overrides=env)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b"authenticated download smoke test")
            self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
            response.close()
        finally:
            target.unlink(missing_ok=True)

    def test_missing_authenticated_download_returns_404(self):
        key = app.session_snapshot()["key"]
        response = self.client.get(
            f"/files/does-not-exist-iab-test.txt?k={key}",
            environ_overrides={"REMOTE_ADDR": "192.168.1.65", "HTTP_HOST": "192.168.1.2:8000"},
        )
        self.assertEqual(response.status_code, 404)

    def test_template_includes_mobile_tasks_and_automatic_downloads(self):
        source = self.template_source
        for marker in (
            "mobileTaskTabs", "aria-controls=\"sendFilesPanel\"", "aria-controls=\"receivePanel\"",
            "function requestDownload", 'link.download = file.name', 'link.click()',
            'Downloading to your Downloads folder', "matchMedia();", "prefers-reduced-motion: reduce"
        ):
            self.assertIn(marker, source)
        for removed in ("MAX_TRACKED_DOWNLOAD_BYTES", "ready-to-save", "function saveTrackedDownload", "xhr.responseType = \"blob\""):
            self.assertNotIn(removed, source)

    def test_local_gsap_bundle_is_served(self):
        response = self.client.get("/static/vendor/gsap/gsap.min.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"GSAP 3.15.0", response.data[:200])
        response.close()

    def test_same_name_uploads_publish_unique_completed_files(self):
        key = app.session_snapshot()["key"]
        env = {"REMOTE_ADDR": "192.168.1.59", "HTTP_HOST": "192.168.1.2:8000"}
        names = {"parallel-same-name.bin", "parallel-same-name_1.bin"}
        try:
            def upload(payload):
                client = app.app.test_client()
                with io.BytesIO(payload) as stream:
                    response = client.post(
                        f"/api/upload?k={key}",
                        data={"file": (stream, "parallel-same-name.bin")},
                        environ_overrides=env,
                    )
                return response.status_code, response.get_json()

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(upload, (b"first", b"second")))
            self.assertEqual([status for status, _ in results], [200, 200], results)
            self.assertEqual({body["filename"] for _, body in results}, names)
            self.assertEqual({(app.UPLOAD_DIR / name).read_bytes() for name in names}, {b"first", b"second"})
        finally:
            for name in names:
                (app.UPLOAD_DIR / name).unlink(missing_ok=True)
            app.invalidate_upload_snapshot()

    def test_failed_upload_staging_is_hidden_and_removed(self):
        key = app.session_snapshot()["key"]
        env = {"REMOTE_ADDR": "192.168.1.60", "HTTP_HOST": "192.168.1.2:8000"}
        app.invalidate_upload_snapshot()

        def fail_after_partial_write(_storage, dst, buffer_size=16384):
            Path(dst).write_bytes(b"partial")
            self.assertFalse(any(item["name"].startswith(".dropair-staging-") for item in app.upload_snapshot()))
            raise OSError("simulated write failure")

        with patch("werkzeug.datastructures.FileStorage.save", fail_after_partial_write):
            response = self.client.post(
                f"/api/upload?k={key}",
                data={"file": (io.BytesIO(b"complete"), "failure-cleanup.bin")},
                environ_overrides=env,
            )
        self.assertEqual(response.status_code, 500)
        self.assertFalse((app.UPLOAD_DIR / "failure-cleanup.bin").exists())
        self.assertFalse(any(path.name.startswith(".dropair-staging-") for path in app.UPLOAD_DIR.iterdir()))

    def test_upload_snapshot_can_be_invalidated_after_external_edit(self):
        previous_ttl = app.UPLOAD_SNAPSHOT_TTL_SECONDS
        path = app.UPLOAD_DIR / "external-edit-cache-test.bin"
        try:
            app.UPLOAD_SNAPSHOT_TTL_SECONDS = 60
            app.invalidate_upload_snapshot()
            self.assertNotIn(path.name, {item["name"] for item in app.upload_snapshot()})
            path.write_bytes(b"external")
            self.assertNotIn(path.name, {item["name"] for item in app.upload_snapshot()})
            app.invalidate_upload_snapshot()
            self.assertIn(path.name, {item["name"] for item in app.upload_snapshot()})
        finally:
            path.unlink(missing_ok=True)
            app.UPLOAD_SNAPSHOT_TTL_SECONDS = previous_ttl
            app.invalidate_upload_snapshot()

    def test_cleanup_enforces_file_count_and_preserves_newest(self):
        previous = app.get_settings()
        paths = [app.UPLOAD_DIR / f"retention-{i}.bin" for i in range(4)]
        try:
            app.set_settings({**previous, "auto_cleanup_minutes": 1, "auto_cleanup_days": 0, "auto_cleanup_max_files": 2})
            for index, path in enumerate(paths):
                path.write_bytes(bytes([index]))
                age = 1000 if index == 0 else 4 - index
                os.utime(path, (time.time() - age,) * 2)
            app.invalidate_upload_snapshot()
            app.cleanup_uploads(force=True)
            self.assertEqual({path.name for path in paths if path.exists()}, {"retention-2.bin", "retention-3.bin"})
        finally:
            for path in paths:
                path.unlink(missing_ok=True)
            app.set_settings(previous)
            app.invalidate_upload_snapshot()

    def test_unauthenticated_file_list_does_not_run_cleanup(self):
        with patch.object(app, "cleanup_uploads") as cleanup:
            response = self.client.get(
                "/api/files?k=invalid-session-key",
                environ_overrides={"REMOTE_ADDR": "192.168.1.61", "HTTP_HOST": "192.168.1.2:8000"},
            )
        self.assertEqual(response.status_code, 404)
        cleanup.assert_not_called()

    def test_connection_heartbeat_counts_active_clients(self):
        app.ACTIVE_CONNECTIONS.clear()
        key = app.session_snapshot()["key"]
        env = {"REMOTE_ADDR": "192.168.1.55", "HTTP_HOST": "192.168.1.2:8000"}
        first = self.client.post(f"/api/connections?k={key}", json={"client_id": "phone-a"}, environ_overrides=env)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["count"], 1)
        second = self.client.post(f"/api/connections?k={key}", json={"client_id": "phone-b"}, environ_overrides=env)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()["count"], 2)

    def test_server_port_skips_occupied_default(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            occupied_port = blocker.getsockname()[1]

            chosen_port = app.select_server_port(occupied_port)

        self.assertGreater(chosen_port, occupied_port)

    def test_urls_use_active_port(self):
        previous_port = app.ACTIVE_PORT
        try:
            app.ACTIVE_PORT = 8123
            self.assertIn("http://127.0.0.1:8123/", app.build_local_url(""))
            self.assertIn(":8123/", app.build_public_url(""))
        finally:
            app.ACTIVE_PORT = previous_port

    def test_admin_update_get_uses_release_info(self):
        expected = {
            "configured": True,
            "repo": "B1progame/drop-air",
            "current_version": "1.0.0",
            "latest_version": "1.0.1",
            "update_available": True,
            "release_url": "https://github.com/B1progame/drop-air/releases/tag/1.0.1",
            "message": "Update available.",
        }
        with patch.object(app, "latest_release_info", return_value=expected):
            response = self.client.get("/api/admin/update", environ_overrides={"REMOTE_ADDR": "127.0.0.1", "HTTP_HOST": "127.0.0.1:8000"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["update_available"])

    def test_update_page_contains_live_progress_ui(self):
        response = self.client.get("/update", environ_overrides={"REMOTE_ADDR": "127.0.0.1", "HTTP_HOST": "127.0.0.1:8000"})
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Drop Air Updater", body)
        self.assertIn("updateProgressBar", body)
        self.assertIn("updatePercent", body)
        self.assertIn("/api/admin/update/status", body)
        self.assertIn("role=\"progressbar\"", body)

    def test_update_status_endpoint_returns_progress_shape(self):
        response = self.client.get(
            "/api/admin/update/status",
            environ_overrides={"REMOTE_ADDR": "127.0.0.1", "HTTP_HOST": "127.0.0.1:8000"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("percent", data)
        self.assertIn("eta_seconds", data)
        self.assertIn("speed_bps", data)
        self.assertIn("running", data)

    def test_update_template_formats_eta_and_percentage(self):
        source = self.update_template_source
        self.assertIn("formatEta", source)
        self.assertIn("human(data.speed_bps)", source)
        self.assertIn("aria-valuenow", source)
        self.assertIn("Waiting for Drop Air to restart", source)

    def test_runtime_upload_limit_setting_updates(self):
        response = self.client.post(
            "/api/settings",
            json={"max_upload_gb": 1.5},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1", "HTTP_HOST": "127.0.0.1:8000"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["settings"]["max_upload_gb"], 1.5)

    def test_runtime_upload_limit_is_enforced_before_saving(self):
        previous = app.get_settings()
        path = app.UPLOAD_DIR / "over-runtime-limit.bin"
        try:
            app.set_settings({**previous, "max_upload_gb": 0.01})
            app.app.config["MAX_CONTENT_LENGTH"] = app.upload_limit_bytes()
            payload = b"x" * (app.upload_limit_bytes() + 1)
            response = self.client.post(
                f"/api/upload?k={app.session_snapshot()['key']}",
                data={"file": (io.BytesIO(payload), path.name)},
                environ_overrides={"REMOTE_ADDR": "192.168.1.63", "HTTP_HOST": "192.168.1.2:8000"},
            )
            self.assertEqual(response.status_code, 413)
            self.assertFalse(path.exists())
        finally:
            path.unlink(missing_ok=True)
            app.set_settings(previous)
            app.app.config["MAX_CONTENT_LENGTH"] = app.upload_limit_bytes()

    def test_update_prefers_setup_installer_asset(self):
        info = {
            "assets": [
                {"name": "DropAir.exe", "browser_download_url": "portable"},
                {"name": "Drop-Air-Setup-1.1.0.exe", "browser_download_url": "setup"},
            ]
        }
        asset = app.find_release_setup_asset(info)
        self.assertIsNotNone(asset)
        self.assertEqual(asset["browser_download_url"], "setup")

    def test_updater_restart_resets_pyinstaller_environment(self):
        source = Path(app.__file__).read_text(encoding="utf-8")
        self.assertIn('env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"', source)
        self.assertIn("$env:PYINSTALLER_RESET_ENVIRONMENT = '1'", source)

    def test_packaged_update_restart_uses_the_native_startup_launcher(self):
        with tempfile.TemporaryDirectory(prefix="drop-air-restart-test-") as temp:
            app_dir = Path(temp)
            launcher = app_dir / "DropAir.exe"
            launcher.write_bytes(b"launcher")
            with (
                patch.object(app, "APP_DIR", app_dir),
                patch.object(app.sys, "frozen", True, create=True),
                patch.object(app.sys, "executable", str(app_dir / "DropAirCore.exe")),
                patch.object(app.sys, "argv", [str(app_dir / "DropAirCore.exe"), "--resume"]),
                patch.object(app.subprocess, "Popen") as spawn,
                patch.object(app.os, "_exit") as exit_process,
            ):
                app.restart_current_app()
            self.assertEqual(spawn.call_args.args[0], [str(launcher), "--resume"])
            self.assertEqual(spawn.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
            exit_process.assert_called_once_with(0)

    def test_admin_update_post_starts_install(self):
        expected = {"ok": True, "message": "Update started.", "status_url": "/update"}
        with patch.object(app, "start_update_install", return_value=expected):
            response = self.client.post("/api/admin/update", json={}, environ_overrides={"REMOTE_ADDR": "127.0.0.1", "HTTP_HOST": "127.0.0.1:8000"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ok"], True)
        self.assertIn("status_url", response.get_json())


if __name__ == "__main__":
    unittest.main()
