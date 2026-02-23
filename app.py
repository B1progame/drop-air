import atexit
import json
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path
from threading import Lock

import qrcode
from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    TEMPLATE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR)) / "templates"
else:
    APP_DIR = Path(__file__).resolve().parent
    TEMPLATE_DIR = APP_DIR / "templates"

def get_data_dir() -> Path:
    # Use a per-user writable folder for runtime data so packaged installs work from Program Files.
    env_override = (os.getenv("DROP_AIR_DATA_DIR") or "").strip()
    if env_override:
        return Path(env_override).expanduser()

    if os.name == "nt":
        local_appdata = (os.getenv("LOCALAPPDATA") or "").strip()
        if local_appdata:
            return Path(local_appdata) / "DropAir"
        return Path.home() / "AppData" / "Local" / "DropAir"

    xdg_data_home = (os.getenv("XDG_DATA_HOME") or "").strip()
    if xdg_data_home:
        return Path(xdg_data_home) / "drop-air"
    return Path.home() / ".local" / "share" / "drop-air"


DATA_DIR = get_data_dir()
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"

MAX_CONTENT_LENGTH = 10 * 1024 * 1024 * 1024  # 10 GB
ALLOWED_ORIGINS = "*"
DEFAULT_SETTINGS = {
    "access_code": os.getenv("DROP_AIR_CODE", "").strip(),
    "auto_cleanup_minutes": int(os.getenv("DROP_AIR_AUTO_CLEANUP_MINUTES", "10") or "10"),
    "auto_cleanup_days": int(os.getenv("DROP_AIR_AUTO_CLEANUP_DAYS", "0") or "0"),
    "auto_cleanup_max_files": int(os.getenv("DROP_AIR_AUTO_CLEANUP_MAX_FILES", "0") or "0"),
}
SETTINGS_LOCK = Lock()
SETTINGS = {}

app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH


class WerkzeugRequestFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if '"GET /api/files HTTP/1.1" 200' in message:
            return False
        return True


def configure_request_logging() -> None:
    logging.getLogger("werkzeug").addFilter(WerkzeugRequestFilter())


def _coerce_non_negative_int(value, fallback: int) -> int:
    try:
        val = int(value)
        if val < 0:
            return fallback
        return val
    except (TypeError, ValueError):
        return fallback


def _normalize_settings(raw: dict) -> dict:
    return {
        "access_code": str(raw.get("access_code", DEFAULT_SETTINGS["access_code"])).strip(),
        "auto_cleanup_minutes": _coerce_non_negative_int(
            raw.get("auto_cleanup_minutes", DEFAULT_SETTINGS["auto_cleanup_minutes"]),
            DEFAULT_SETTINGS["auto_cleanup_minutes"],
        ),
        "auto_cleanup_days": _coerce_non_negative_int(
            raw.get("auto_cleanup_days", DEFAULT_SETTINGS["auto_cleanup_days"]),
            DEFAULT_SETTINGS["auto_cleanup_days"],
        ),
        "auto_cleanup_max_files": _coerce_non_negative_int(
            raw.get("auto_cleanup_max_files", DEFAULT_SETTINGS["auto_cleanup_max_files"]),
            DEFAULT_SETTINGS["auto_cleanup_max_files"],
        ),
    }


def load_settings() -> dict:
    merged = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                merged.update(stored)
        except (OSError, json.JSONDecodeError):
            pass
    return _normalize_settings(merged)


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"Could not save settings: {exc}")


def get_settings() -> dict:
    with SETTINGS_LOCK:
        return dict(SETTINGS)


def set_settings(new_values: dict) -> dict:
    with SETTINGS_LOCK:
        SETTINGS.update(new_values)
        current = dict(SETTINGS)
    save_settings(current)
    return current


SETTINGS = load_settings()


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def check_code() -> bool:
    access_code = get_settings()["access_code"]
    if not access_code:
        return True
    supplied = request.args.get("code", "") or request.headers.get("X-Drop-Air-Code", "")
    return supplied == access_code


def list_files():
    items = []
    for p in sorted(UPLOAD_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file():
            items.append(
                {
                    "name": p.name,
                    "size": p.stat().st_size,
                    "modified": int(p.stat().st_mtime),
                    "url": url_for("download_file", filename=p.name),
                }
            )
    return items


def cleanup_uploads() -> None:
    settings = get_settings()
    auto_cleanup_minutes = settings["auto_cleanup_minutes"]
    auto_cleanup_days = settings["auto_cleanup_days"]
    auto_cleanup_max_files = settings["auto_cleanup_max_files"]

    if auto_cleanup_minutes <= 0 and auto_cleanup_days <= 0 and auto_cleanup_max_files <= 0:
        return

    files = [p for p in UPLOAD_DIR.iterdir() if p.is_file()]
    if not files:
        return

    now = time.time()
    removed = 0

    if auto_cleanup_minutes > 0:
        cutoff = now - (auto_cleanup_minutes * 60)
        for p in files:
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue

    if auto_cleanup_days > 0:
        cutoff = now - (auto_cleanup_days * 24 * 60 * 60)
        for p in files:
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue

    if auto_cleanup_max_files > 0:
        files = [p for p in UPLOAD_DIR.iterdir() if p.is_file()]
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        for p in files[auto_cleanup_max_files:]:
            try:
                p.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue

    if removed:
        print(f"Auto-cleanup removed {removed} upload file(s).")


def cleanup_all_uploads_on_shutdown() -> None:
    removed = 0
    for p in UPLOAD_DIR.iterdir():
        if not p.is_file():
            continue
        try:
            p.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    if removed:
        print(f"Shutdown cleanup removed {removed} upload file(s).")


def _handle_shutdown_signal(signum, _frame):
    cleanup_all_uploads_on_shutdown()
    raise SystemExit(0)


def register_shutdown_cleanup() -> None:
    atexit.register(cleanup_all_uploads_on_shutdown)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _handle_shutdown_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_shutdown_signal)


@app.after_request
def add_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGINS
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Drop-Air-Code"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.route("/", methods=["GET"])
def index():
    cleanup_uploads()
    settings = get_settings()
    theme = (request.args.get("theme", "") or "").lower()
    index_tpl = "saved/index_v1.html" if theme == "classic" else "index.html"
    gate_tpl = "saved/code_gate_v1.html" if theme == "classic" else "code_gate.html"

    if settings["access_code"] and request.args.get("code", "") == "":
        return render_template(gate_tpl)
    if not check_code():
        return render_template(gate_tpl, error="Wrong code"), 401
    return render_template(index_tpl, files=list_files(), access_code=settings["access_code"], settings=settings)


@app.route("/enter", methods=["POST"])
def enter():
    settings = get_settings()
    code = request.form.get("code", "").strip()
    theme = (request.args.get("theme", "") or request.form.get("theme", "")).lower()
    if not theme and "theme=classic" in (request.referrer or ""):
        theme = "classic"
    if not settings["access_code"] or code == settings["access_code"]:
        if settings["access_code"]:
            return redirect(url_for("index", code=code, theme=theme) if theme else url_for("index", code=code))
        return redirect(url_for("index", theme=theme) if theme else url_for("index"))
    gate_tpl = "saved/code_gate_v1.html" if theme == "classic" else "code_gate.html"
    return render_template(gate_tpl, error="Wrong code"), 401


@app.route("/api/files", methods=["GET", "OPTIONS"])
def api_files():
    cleanup_uploads()
    if request.method == "OPTIONS":
        return ("", 204)
    if not check_code():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"files": list_files()})


@app.route("/api/upload", methods=["POST", "OPTIONS"])
def api_upload():
    cleanup_uploads()
    if request.method == "OPTIONS":
        return ("", 204)
    if not check_code():
        return jsonify({"error": "unauthorized"}), 401

    if "file" not in request.files:
        return jsonify({"error": "missing file"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "empty filename"}), 400

    safe_name = secure_filename(f.filename)
    if not safe_name:
        safe_name = f"file_{int(time.time())}"

    target = UPLOAD_DIR / safe_name
    stem, suffix = target.stem, target.suffix
    i = 1
    while target.exists():
        target = UPLOAD_DIR / f"{stem}_{i}{suffix}"
        i += 1

    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        f.save(target)
    except OSError as exc:
        return jsonify({"error": f"Could not save upload: {exc.strerror or str(exc)}"}), 500
    cleanup_uploads()
    return jsonify({"ok": True, "filename": target.name, "size": target.stat().st_size})


@app.route("/api/settings", methods=["GET", "POST", "OPTIONS"])
def api_settings():
    if request.method == "OPTIONS":
        return ("", 204)
    if not check_code():
        return jsonify({"error": "unauthorized"}), 401

    if request.method == "GET":
        return jsonify({"settings": get_settings()})

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid payload"}), 400

    current = get_settings()
    updates = {}

    if "access_code" in payload:
        updates["access_code"] = str(payload.get("access_code", "")).strip()

    int_fields = ("auto_cleanup_minutes", "auto_cleanup_days", "auto_cleanup_max_files")
    for field in int_fields:
        if field not in payload:
            continue
        try:
            value = int(payload[field])
            if value < 0:
                raise ValueError
            updates[field] = value
        except (TypeError, ValueError):
            return jsonify({"error": f"{field} must be a non-negative integer"}), 400

    merged = dict(current)
    merged.update(updates)
    saved = set_settings(_normalize_settings(merged))
    cleanup_uploads()
    return jsonify({"ok": True, "settings": saved})


@app.route("/files/<path:filename>", methods=["GET"])
def download_file(filename: str):
    if not check_code():
        return jsonify({"error": "unauthorized"}), 401
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=True)


def print_qr(url: str):
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    black = "##"
    white = "  "
    print()
    print("Scan this QR on iPhone/iPad:")
    for row in matrix:
        print("".join(black if cell else white for cell in row))
    print()
    print(url)
    print()


if __name__ == "__main__":
    host = "0.0.0.0"
    port = int(os.getenv("PORT", "8000"))
    ip = get_local_ip()
    base_url = f"http://{ip}:{port}/"
    settings = get_settings()
    access_code = settings["access_code"]
    url = f"{base_url}?code={access_code}" if access_code else base_url

    print(f"Drop Air running on {url}")
    print("Data folder:", DATA_DIR)
    print("Uploads folder:", UPLOAD_DIR)
    if (
        settings["auto_cleanup_minutes"] > 0
        or settings["auto_cleanup_days"] > 0
        or settings["auto_cleanup_max_files"] > 0
    ):
        print(
            "Auto-cleanup:",
            "minutes="
            f"{settings['auto_cleanup_minutes']}, days={settings['auto_cleanup_days']}, "
            f"max_files={settings['auto_cleanup_max_files']}",
        )
    register_shutdown_cleanup()
    cleanup_uploads()
    print_qr(url)
    configure_request_logging()
    app.run(host=host, port=port, debug=False)
