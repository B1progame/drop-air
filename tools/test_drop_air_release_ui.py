import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


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
        self.assertIn("startViewTransition", source)
        self.assertIn("heartbeatConnection", source)
        self.assertIn("pasteClipboard", source)
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
