import os
import io
import re
import socket
import sys
import tempfile
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


class DropAirReleaseUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.app.test_client()
        cls.template_source = (Path(__file__).resolve().parents[1] / "templates" / "index.html").read_text(encoding="utf-8")
        cls.update_template_source = (Path(__file__).resolve().parents[1] / "templates" / "update.html").read_text(encoding="utf-8")

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

    def test_template_includes_mobile_tasks_and_tracked_download_states(self):
        source = self.template_source
        for marker in (
            "mobileTaskTabs", "aria-controls=\"sendFilesPanel\"", "aria-controls=\"receivePanel\"",
            "MAX_TRACKED_DOWNLOAD_BYTES", "ready-to-save", "handed-to-browser", "Download cancelled.",
            "xhr.responseType = \"blob\"", "text/html", "function saveTrackedDownload", "function dismissDownload",
            "matchMedia();", "prefers-reduced-motion: reduce"
        ):
            self.assertIn(marker, source)

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
            self.assertEqual([status for status, _ in results], [200, 200])
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

    def test_admin_update_post_starts_install(self):
        expected = {"ok": True, "message": "Update started.", "status_url": "/update"}
        with patch.object(app, "start_update_install", return_value=expected):
            response = self.client.post("/api/admin/update", json={}, environ_overrides={"REMOTE_ADDR": "127.0.0.1", "HTTP_HOST": "127.0.0.1:8000"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ok"], True)
        self.assertIn("status_url", response.get_json())


if __name__ == "__main__":
    unittest.main()
