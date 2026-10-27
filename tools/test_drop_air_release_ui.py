import os
import io
import json
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
from discovery import PairingRequests, PeerDiscovery  # noqa: E402


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
        self.assertNotIn('Source: "..\\settings.json"', self.installer_source)

    def test_pyinstaller_build_uses_the_current_icon_and_desktop_entry(self):
        root = Path(__file__).resolve().parents[1]
        build_script = (root / "build_exe.ps1").read_text(encoding="utf-8")
        self.assertIn('IconPath = "assets\\icon\\drop_air_brand.ico"', build_script)
        self.assertIn("--icon $iconAbs", build_script)
        self.assertIn("--onedir", build_script)
        self.assertNotIn("--onefile", build_script)
        self.assertIn("--hidden-import app", build_script)
        self.assertIn("--hidden-import winrt.windows.devices.bluetooth.advertisement", build_script)
        self.assertIn("--hidden-import werkzeug.serving", build_script)
        self.assertIn('Copy-Item -LiteralPath $versionAbs -Destination (Join-Path $exeDir "VERSION") -Force', build_script)
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

    def test_desktop_webengine_allows_user_initiated_clipboard_copy(self):
        desktop_source = (Path(__file__).resolve().parents[1] / "desktop_app.py").read_text(encoding="utf-8")
        self.assertIn("QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard", desktop_source)
        self.assertIn("self.web.settings().setAttribute(", desktop_source)

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
        start = source.index("    function legacyCopyText(")
        end = source.index("\n    function commitTheme", start)
        copy_function = source[start:end]
        self.assertIn("navigator.clipboard.writeText(text)", copy_function)
        self.assertIn('document.execCommand("copy")', copy_function)
        self.assertIn('showToast("Copied to clipboard")', copy_function)
        self.assertIn('showToast("Could not copy. Select and copy the link manually.")', copy_function)
        self.assertIn('copyText(new URL(copyButton.dataset.url, window.location.href).toString(), copyButton)', source)
        self.assertIn("copy.dataset.url = absolute", source)
        self.assertIn("function legacyCopyText(text)", copy_function)
        self.assertLess(copy_function.index('document.execCommand("copy")'), copy_function.index("await navigator.clipboard.writeText(text)"))
        self.assertIn("temp.setSelectionRange(0, temp.value.length)", copy_function)

    def test_expired_file_session_attempts_renewal_and_shows_reconnect_help(self):
        source = self.template_source
        sync_start = source.index("    async function syncSession()")
        sync_end = source.index("\n    function scheduleSessionSync", sync_start)
        sync_function = source[sync_start:sync_end]
        self.assertIn("scheduleSessionSync(Math.min(30000", sync_function)
        self.assertIn("return true", sync_function)
        self.assertIn("return false", sync_function)
        self.assertIn("async function refreshFiles(options = {}, retryAfterSessionSync = true)", source)
        self.assertIn("if (await syncSession()) return refreshFiles(options, false)", source)
        self.assertIn("This transfer link expired. Scan the current QR code or copy a fresh link from Drop Air.", source)
        self.assertIn("syncSession().finally(() =>", source)
        self.assertIn("uploadFile(file, ctx, false).then(resolve, reject)", source)

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
        self.assertIn("self._startup_skeleton = StartupSkeleton(self)", desktop_source)
        self.assertIn("self._startup_stack = QStackedWidget(self)", desktop_source)
        self.assertIn("self._startup_stack.setCurrentWidget(self.web)", desktop_source)
        self.assertLess(desktop_source.index("self._startup_stack.addWidget(self._startup_skeleton)"), desktop_source.index("from PySide6.QtWebEngineCore"))
        self.assertNotIn("_loading_timer", desktop_source)
        self.assertIn("QColor(\"#303437\")", desktop_source)
        self.assertIn("card_width = min(560, max(280, width - 40))", desktop_source)
        self.assertIn("rounded(left + 22, row_y + 30, 56, 56, bone_light, 28)", desktop_source)
        self.assertLess(desktop_source.index("window.show()"), desktop_source.index("QTimer.singleShot(80, start_backend_thread)"))
        self.assertNotIn("import app as backend", desktop_source)
        self.assertNotIn("from werkzeug.serving import make_server", desktop_source.split("def start_backend():", 1)[0])
        self.assertIn("loadFinished.connect(self._page_load_finished)", desktop_source)
        self.assertIn("mark_startup_healthy()", desktop_source)
        self.assertIn('self.setAccessibleName("Loading Drop Air")', desktop_source)
        self.assertIn("title_widths = (218, 204, 232)", desktop_source)
        self.assertIn("line_widths = ((330, 394, 286)", desktop_source)
        self.assertIn("Text Relay", self.template_source)

    def test_native_launcher_paints_before_spawning_the_full_desktop_app(self):
        root = Path(__file__).resolve().parents[1]
        launcher = (root / "launcher" / "native_launcher.c").read_text(encoding="utf-8")
        self.assertIn('CORE_EXE[] = L"DropAirCore.exe"', launcher)
        self.assertLess(launcher.index("UpdateWindow(startup_window)"), launcher.index('launch_core(L"", FALSE)'))
        self.assertIn("draw_skeleton", launcher.lower())
        self.assertIn("int card_width = width - 40", launcher)
        self.assertIn("rounded(dc, left + 22, row_y + 30, 56, 56, bone_light, 56)", launcher)
        self.assertNotIn("InvalidateRect(window, NULL, FALSE)", launcher)
        self.assertIn("line_widths[3][3]", launcher)
        self.assertIn("has_core_window()", launcher)
        self.assertIn("if self._tray_available and not self._allow_close:", (root / "desktop_app.py").read_text(encoding="utf-8"))

    def test_native_launcher_reuses_and_restores_the_existing_instance(self):
        root = Path(__file__).resolve().parents[1]
        launcher = (root / "launcher" / "native_launcher.c").read_text(encoding="utf-8")
        self.assertIn('L"Local\\\\B1progame.DropAir.Desktop.Instance"', launcher)
        self.assertIn("CreateMutexW", launcher)
        self.assertIn("WaitForSingleObject(instance_mutex, 5000)", launcher)
        self.assertIn("EnumWindows(find_existing_dropair_window", launcher)
        self.assertIn('wcsncmp(title, L"Drop Air", 8)', launcher)
        self.assertIn('name, L"DropAir.exe"', launcher)
        self.assertIn('name, CORE_EXE', launcher)
        self.assertIn("focus_dropair_window(existing.window, existing.process_id)", launcher)
        self.assertIn("ShowWindow(window, SW_SHOW)", launcher)
        self.assertNotIn("IsWindowVisible(window)", launcher[launcher.index("static BOOL CALLBACK find_existing_dropair_window"):launcher.index("static BOOL CALLBACK find_dropair_window")])

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
        self.assertIn("sessionSyncFailures", source)
        self.assertIn("scheduleSessionSync(Math.min(30000", source)
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

    def test_session_key_state_persists_current_expiry_and_grace_keys(self):
        with tempfile.TemporaryDirectory(prefix=".drop-air-session-state-test-", dir=Path(__file__).resolve().parents[1]) as folder:
            state_path = Path(folder) / "session-state.json"
            key = "a" * 32
            previous = {"b" * 32: time.time() + 30}
            expires_at = time.time() + 90
            with (
                patch.object(app, "SESSION_STATE_FILE", state_path),
                patch.object(app, "SESSION_KEY", key),
                patch.object(app, "SESSION_EXPIRES_AT", expires_at),
                patch.object(app, "SESSION_PREVIOUS_KEYS", previous),
            ):
                app.persist_session_state()
                restored = app.read_session_state()
            self.assertEqual(restored["key"], key)
            self.assertEqual(restored["expires_at"], expires_at)
            self.assertEqual(restored["previous"], previous)

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

    def test_file_api_reports_an_expired_session_as_json(self):
        key = "0" * 32
        response = self.client.get(
            f"/api/files?k={key}",
            environ_overrides={"REMOTE_ADDR": "192.168.1.62", "HTTP_HOST": "192.168.1.2:8000"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"error": "invalid session link"})

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

    def test_upload_progress_shows_percent_speed_and_eta_in_accessible_bar(self):
        for marker in (
            'id="progressShell" role="progressbar"',
            'id="progressDetails"',
            'textContent = `${roundedTotalPct}%  ·  ${human(speed)}/s  ·  ${eta} left`',
            'progressShell?.setAttribute("aria-valuenow"',
        ):
            self.assertIn(marker, self.template_source)

    def test_desktop_clipboard_bridge_supports_image_and_text_paste(self):
        root = Path(__file__).resolve().parents[1]
        desktop_source = (root / "desktop_app.py").read_text(encoding="utf-8")
        self.assertIn("class ClipboardBridge(QObject)", desktop_source)
        self.assertIn("mime.hasImage()", desktop_source)
        self.assertIn('registerObject("clipboardBridge"', desktop_source)
        self.assertIn("qrc:///qtwebchannel/qwebchannel.js", self.template_source)
        self.assertIn("readNativeClipboard()", self.template_source)
        self.assertIn('new File([blob], clipboardFileName("image/png")', self.template_source)

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

    def test_upload_publishes_completed_staging_file_without_copy_when_hardlinks_work(self):
        key = app.session_snapshot()["key"]
        name = "hardlink-publish-test.bin"
        try:
            with patch.object(app.shutil, "copyfileobj", side_effect=AssertionError("unexpected second disk copy")):
                response = self.client.post(
                    f"/api/upload?k={key}",
                    data={"file": (io.BytesIO(b"complete payload"), name)},
                    environ_overrides={"REMOTE_ADDR": "192.168.1.59", "HTTP_HOST": "192.168.1.2:8000"},
                )
            # On common local filesystems hard-link publication succeeds and
            # never enters the compatibility copy path.
            if response.status_code == 200:
                self.assertEqual((app.UPLOAD_DIR / name).read_bytes(), b"complete payload")
            else:
                self.assertEqual(response.status_code, 500, response.get_json())
        finally:
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

    def test_device_name_and_discoverability_are_saved_and_validated(self):
        previous = app.get_settings()
        try:
            response = self.client.post(
                "/api/settings",
                json={"device_name": "Benji's Desk", "discoverable": True},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1", "HTTP_HOST": "localhost:8000"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["settings"]["device_name"], "Benji's Desk")
            self.assertTrue(response.get_json()["settings"]["discoverable"])
            persisted = json.loads(app.SETTINGS_FILE.read_text(encoding="utf-8"))
            self.assertEqual(persisted["device_name"], "Benji's Desk")
            self.assertTrue(persisted["discoverable"])
            invalid = self.client.post(
                "/api/settings",
                json={"device_name": "x" * 33},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1", "HTTP_HOST": "localhost:8000"},
            )
            self.assertEqual(invalid.status_code, 400)
        finally:
            app.set_settings(previous)

    def test_settings_write_failure_does_not_report_success_or_change_runtime(self):
        previous = app.get_settings()
        with patch.object(app, "save_settings", side_effect=OSError("read only")):
            with self.assertRaises(OSError):
                app.set_settings({**previous, "device_name": "Unsaved name"})
            self.assertEqual(app.get_settings(), previous)
            response = self.client.post(
                "/api/settings",
                json={"device_name": "Unsaved name"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1", "HTTP_HOST": "localhost:8000"},
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn("could not be saved", response.get_json()["error"].lower())
        self.assertEqual(app.get_settings(), previous)

    def test_public_discovery_endpoint_requires_a_valid_transfer_session(self):
        guest = {"REMOTE_ADDR": "192.168.1.40", "HTTP_HOST": "192.168.1.2:8000"}
        denied = self.client.get("/api/discovery", environ_overrides=guest)
        self.assertEqual(denied.status_code, 401)
        allowed = self.client.get(
            f"/api/discovery?k={app.session_snapshot()['key']}", environ_overrides=guest
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertIn("peers", allowed.get_json())

    def test_phone_pairing_is_relayed_only_to_a_discovered_peer(self):
        peer = {
            "device_id": "b" * 32,
            "device_name": "Living Room",
            "address": "192.168.1.21",
            "port": 8000,
            "transports": ["Wi-Fi"],
        }

        class RelayResponse:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read():
                return b'{"request":{"id":"abc"}}'

        with patch.object(app.PEER_DISCOVERY, "get_peer", return_value=peer), patch.object(
            app, "urlopen", return_value=RelayResponse()
        ) as relay:
            response = self.client.post(
                f"/api/discovery/{peer['device_id']}/pair",
                json={"device_id": "c" * 24, "device_name": "iPhone"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1", "HTTP_HOST": "localhost:8000"},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json(), {"request": {"id": "abc"}})
        self.assertTrue(relay.call_args.args[0].full_url.startswith("http://192.168.1.21:8000/api/pair/requests"))

    def test_discovery_peer_cache_does_not_expire_after_quiet_mdns_period(self):
        discovery = PeerDiscovery("a" * 32, lambda: {"discoverable": False})
        discovery._peers["b" * 32] = {
            "device_id": "b" * 32,
            "device_name": "Quiet peer",
            "address": "192.168.1.21",
            "port": 8000,
            "transports": ["Wi-Fi"],
            "last_seen": time.time() - 15,
        }
        self.assertEqual(len(discovery.snapshot()["peers"]), 1)
        discovery._peers["b" * 32]["last_seen"] = time.time() - 121
        self.assertEqual(discovery.snapshot()["peers"], [])

    def test_spoofed_localhost_host_header_cannot_access_admin(self):
        response = self.client.get(
            "/api/admin",
            environ_overrides={"REMOTE_ADDR": "192.168.1.44", "HTTP_HOST": "localhost:8000"},
        )
        self.assertEqual(response.status_code, 403)

    def test_wifi_mdns_discovery_contains_no_transfer_credentials_and_updates_peer(self):
        settings = {"device_name": "Office PC", "discoverable": True}
        discovery = PeerDiscovery("a" * 32, lambda: dict(settings))

        class AdvertisedDevice:
            properties = {b"protocol": b"1", b"id": b"b" * 32, b"name": b"Living Room"}
            addresses = [b"\xc0\xa8\x01\x15"]
            port = 8000

        announced_values = b" ".join(AdvertisedDevice.properties.values())
        self.assertNotIn(b"session", announced_values.lower())
        self.assertNotIn(b'"k"', announced_values.lower())
        self.assertNotIn(b"access_code", announced_values.lower())
        self.assertTrue(discovery._accept_service(AdvertisedDevice()))
        peer = discovery.snapshot()["peers"][0]
        self.assertEqual(peer["device_name"], "Living Room")
        self.assertEqual(peer["transports"], ["Wi-Fi"])
        discovery._mark_bluetooth_peer("b" * 8)
        self.assertEqual(discovery.snapshot()["peers"][0]["transports"], ["Wi-Fi", "Bluetooth"])
        self.assertFalse(discovery._accept_service(type("OtherProtocol", (AdvertisedDevice,), {
            "properties": {**AdvertisedDevice.properties, b"protocol": b"other"}
        })()))

    def test_pair_request_only_reveals_link_after_host_approval(self):
        now = [1000.0]
        requests = PairingRequests(clock=lambda: now[0])
        item, error = requests.create(
            "192.168.1.21",
            {"device_id": "b" * 32, "device_name": "Phone"},
        )
        self.assertIsNone(error)
        self.assertEqual(requests.status("192.168.1.21", item["id"]), {"decision": "pending"})
        self.assertIsNone(requests.status("192.168.1.22", item["id"]))
        self.assertTrue(requests.decide(item["id"], True, "http://192.168.1.2:8000/?k=approved"))
        self.assertEqual(requests.status("192.168.1.21", item["id"])["decision"], "accepted")
        self.assertGreater(item["expires_at"], now[0])
        now[0] = item["expires_at"] + 1
        self.assertIsNone(requests.status("192.168.1.21", item["id"]))

    def test_pairing_http_flow_requires_visibility_and_host_approval(self):
        previous = app.get_settings()
        try:
            app.set_settings({**previous, "discoverable": True})
            guest = {"REMOTE_ADDR": "192.168.1.21", "HTTP_HOST": "192.168.1.2:8000"}
            requested = self.client.post(
                "/api/pair/requests",
                json={"device_id": "c" * 32, "device_name": "Guest Laptop"},
                environ_overrides=guest,
            )
            self.assertEqual(requested.status_code, 202)
            request_id = requested.get_json()["request"]["id"]
            pending = self.client.get(
                f"/api/pair/requests/{request_id}", environ_overrides=guest
            )
            self.assertEqual(pending.get_json(), {"decision": "pending"})
            approved = self.client.post(
                f"/api/admin/discovery/requests/{request_id}",
                json={"decision": "accept"},
                environ_overrides={"REMOTE_ADDR": "127.0.0.1", "HTTP_HOST": "localhost:8000"},
            )
            self.assertEqual(approved.status_code, 200)
            connected = self.client.get(
                f"/api/pair/requests/{request_id}", environ_overrides=guest
            )
            self.assertEqual(connected.get_json()["decision"], "accepted")
            self.assertIn("k=", connected.get_json()["share_url"])
        finally:
            app.set_settings(previous)

    def test_nearby_device_controls_require_both_sides_to_approve(self):
        source = self.template_source
        self.assertIn('id="setDeviceName"', source)
        self.assertIn('id="setDiscoverable"', source)
        self.assertIn('id="nearbyDevices"', source)
        self.assertIn('id="findDevicesBtn"', source)
        self.assertIn('id="nearbyModal"', source)
        self.assertIn('step="0.001"', source)
        self.assertIn('id="retryDiscoveryBtn"', source)
        self.assertIn("confirm(`Connect to ${displayName}", source)
        self.assertIn("Allow ${incoming.device_name}", source)

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

    def test_frozen_updater_targets_current_folder_logs_and_verifies_install(self):
        source = Path(app.__file__).read_text(encoding="utf-8")
        for marker in (
            "-Verb RunAs -PassThru",
            "/DIR=",
            "update-install.log",
            "update-helper.log",
            "installedVersion -ne $expectedVersion",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("[Math]::Min(95, $percent + 2)", source)

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
