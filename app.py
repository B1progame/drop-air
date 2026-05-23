import atexit
import json
import logging
import os
import secrets
import signal
import socket
import sys
import time
import webbrowser
from pathlib import Path
from threading import Lock, Timer
from urllib.parse import urlencode

import qrcode
from flask import Flask, Response, jsonify, redirect, render_template, request, send_from_directory, url_for
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
LOG_FILE = DATA_DIR / "drop-air.log"

MAX_CONTENT_LENGTH = 10 * 1024 * 1024 * 1024  # 10 GB
ALLOWED_ORIGINS = "*"
SESSION_KEY = (os.getenv("DROP_AIR_SESSION_KEY", "").strip() or secrets.token_hex(16))[:32]
SESSION_PARAM = "k"
DEFAULT_SETTINGS = {
    "access_code": os.getenv("DROP_AIR_CODE", "").strip(),
    "auto_cleanup_minutes": int(os.getenv("DROP_AIR_AUTO_CLEANUP_MINUTES", "10") or "10"),
    "auto_cleanup_days": int(os.getenv("DROP_AIR_AUTO_CLEANUP_DAYS", "0") or "0"),
    "auto_cleanup_max_files": int(os.getenv("DROP_AIR_AUTO_CLEANUP_MAX_FILES", "0") or "0"),
    "launch_browser_on_start": os.getenv("DROP_AIR_OPEN_BROWSER", "1").strip().lower()
    not in {"0", "false", "no", "off"},
}
SETTINGS_LOCK = Lock()
SETTINGS = {}
TEXT_ITEMS_FILE = DATA_DIR / "text_items.json"
TEXT_ITEMS_LOCK = Lock()
TEXT_ITEMS = []
MAX_TEXT_ITEMS = 40
MAX_TEXT_CHARS = 20000
TEXT_TTL_MINUTES = int(os.getenv("DROP_AIR_TEXT_TTL_MINUTES", "10") or "10")

app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH


def app_icon_path() -> Path | None:
    env_icon = (os.getenv("DROP_AIR_ICON") or "").strip()
    bundle_dir = Path(getattr(sys, "_MEIPASS", APP_DIR))
    candidates = [
        Path(env_icon).expanduser() if env_icon else None,
        APP_DIR / "assets" / "icon" / "drop_air_minimal.ico",
        bundle_dir / "assets" / "icon" / "drop_air_minimal.ico",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def set_console_window_icon() -> None:
    if os.name != "nt":
        return
    icon_path = app_icon_path()
    if not icon_path:
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetConsoleWindow()
        if not hwnd:
            return

        image_icon = 1
        lr_load_from_file = 0x00000010
        wm_seticon = 0x0080
        icon_small = 0
        icon_big = 1

        for icon_type, size in ((icon_small, 16), (icon_big, 32)):
            icon_handle = user32.LoadImageW(None, str(icon_path), image_icon, size, size, lr_load_from_file)
            if icon_handle:
                user32.SendMessageW(hwnd, wm_seticon, icon_type, icon_handle)
    except Exception:
        pass


class WerkzeugRequestFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        quiet_paths = ("GET /api/files ", "GET /api/text ", "GET /favicon.ico ")
        if any(path in message for path in quiet_paths):
            return False
        return True


def configure_request_logging() -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text("", encoding="utf-8")
    except OSError as exc:
        print(f"Could not initialize log file: {exc}")

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(WerkzeugRequestFilter())

    request_logger = logging.getLogger("werkzeug")
    request_logger.addFilter(WerkzeugRequestFilter())
    request_logger.addHandler(file_handler)

    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info("Drop Air log started.")


def _coerce_non_negative_int(value, fallback: int) -> int:
    try:
        val = int(value)
        if val < 0:
            return fallback
        return val
    except (TypeError, ValueError):
        return fallback


def _coerce_bool(value, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, int):
        return bool(value)
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
        "launch_browser_on_start": _coerce_bool(
            raw.get("launch_browser_on_start", DEFAULT_SETTINGS["launch_browser_on_start"]),
            DEFAULT_SETTINGS["launch_browser_on_start"],
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


def _normalize_text_item(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text", ""))[:MAX_TEXT_CHARS]
    translated_text = str(raw.get("translated_text", ""))[:MAX_TEXT_CHARS]
    if not text.strip() and not translated_text.strip():
        return None
    return {
        "id": str(raw.get("id", int(time.time() * 1000))),
        "text": text,
        "translated_text": translated_text,
        "source_language": str(raw.get("source_language", "auto"))[:16],
        "target_language": str(raw.get("target_language", "en"))[:16],
        "created": int(raw.get("created", time.time())),
    }


def load_text_items() -> list[dict]:
    if not TEXT_ITEMS_FILE.exists():
        return []
    try:
        stored = json.loads(TEXT_ITEMS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(stored, list):
        return []
    items = []
    for raw in stored[:MAX_TEXT_ITEMS]:
        item = _normalize_text_item(raw)
        if item:
            items.append(item)
    return items


def save_text_items(items: list[dict]) -> None:
    try:
        TEXT_ITEMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TEXT_ITEMS_FILE.write_text(json.dumps(items[:MAX_TEXT_ITEMS], indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"Could not save text items: {exc}")


def cleanup_text_items() -> int:
    if TEXT_TTL_MINUTES <= 0:
        return 0
    cutoff = int(time.time() - (TEXT_TTL_MINUTES * 60))
    removed = 0
    with TEXT_ITEMS_LOCK:
        kept = [item for item in TEXT_ITEMS if int(item.get("created", 0)) >= cutoff]
        removed = len(TEXT_ITEMS) - len(kept)
        if removed:
            TEXT_ITEMS[:] = kept
            current = list(TEXT_ITEMS)
        else:
            current = []
    if removed:
        save_text_items(current)
    return removed


def list_text_items() -> list[dict]:
    cleanup_text_items()
    with TEXT_ITEMS_LOCK:
        return list(TEXT_ITEMS)


def add_text_item(payload: dict) -> dict:
    item = _normalize_text_item(
        {
            "id": int(time.time() * 1000),
            "text": str(payload.get("text", "")),
            "translated_text": str(payload.get("translated_text", "")),
            "source_language": str(payload.get("source_language", "auto")).strip() or "auto",
            "target_language": str(payload.get("target_language", "en")).strip() or "en",
            "created": int(time.time()),
        }
    )
    if not item:
        raise ValueError("Paste text before sharing.")
    with TEXT_ITEMS_LOCK:
        TEXT_ITEMS.insert(0, item)
        del TEXT_ITEMS[MAX_TEXT_ITEMS:]
        current = list(TEXT_ITEMS)
    save_text_items(current)
    return item


TEXT_ITEMS = load_text_items()
cleanup_text_items()


def is_admin_request() -> bool:
    remote = (request.remote_addr or "").strip()
    host = (request.host.split(":", 1)[0] or "").lower()
    return remote in {"127.0.0.1", "::1", "localhost"} or host in {"127.0.0.1", "localhost"}


def require_admin():
    if not is_admin_request():
        return jsonify({"error": "admin access is only available on the host machine"}), 403
    return None


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def build_public_url(access_code: str | None = None) -> str:
    port = int(os.getenv("PORT", "8000"))
    base_url = f"http://{get_local_ip()}:{port}/"
    return f"{base_url}{build_auth_query(access_code)}"


def build_auth_query(access_code: str | None = None) -> str:
    code = access_code if access_code is not None else get_settings()["access_code"]
    params = [(SESSION_PARAM, SESSION_KEY)]
    if code:
        params.append(("code", code))
    return f"?{urlencode(params)}"


def build_local_url(access_code: str | None = None) -> str:
    port = int(os.getenv("PORT", "8000"))
    base_url = f"http://127.0.0.1:{port}/"
    code = access_code if access_code is not None else get_settings()["access_code"]
    return f"{base_url}?code={code}" if code else base_url


def make_qr_svg(url: str) -> str:
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    size = len(matrix)
    rects = []
    for y, row in enumerate(matrix):
        for x, cell in enumerate(row):
            if cell:
                rects.append(f'<rect x="{x}" y="{y}" width="1" height="1"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        'shape-rendering="crispEdges" role="img" aria-label="Drop Air connection QR code">'
        '<rect width="100%" height="100%" fill="#fff"/>'
        '<g fill="#111827">'
        f'{"".join(rects)}'
        "</g></svg>"
    )


def upload_stats() -> dict:
    files = [p for p in UPLOAD_DIR.iterdir() if p.is_file()]
    total_size = 0
    for p in files:
        try:
            total_size += p.stat().st_size
        except OSError:
            continue
    return {
        "file_count": len(files),
        "total_size": total_size,
        "text_count": len(list_text_items()),
        "data_dir": str(DATA_DIR),
        "uploads_dir": str(UPLOAD_DIR),
    }


def check_code() -> bool:
    access_code = get_settings()["access_code"]
    if not access_code:
        return True
    supplied = request.args.get("code", "") or request.headers.get("X-Drop-Air-Code", "")
    return supplied == access_code


def check_session_key() -> bool:
    if is_admin_request():
        return True
    supplied = request.args.get(SESSION_PARAM, "") or request.headers.get("X-Drop-Air-Key", "")
    return supplied == SESSION_KEY


def session_error():
    return render_template(
        "not_found.html",
        title="Link Not Found",
        message="This Drop Air link is not valid for the current session.",
        hint="Scan the current QR code from the host admin panel.",
    ), 404


def code_error():
    return render_template(
        "not_found.html",
        title="Code Not Found",
        message="This Drop Air access code does not exist.",
        hint="Check the code or ask the host to generate a new guest link.",
    ), 404


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


def clear_text_items() -> None:
    with TEXT_ITEMS_LOCK:
        TEXT_ITEMS.clear()
    save_text_items([])


def clear_uploads() -> int:
    removed = 0
    for p in UPLOAD_DIR.iterdir():
        if not p.is_file():
            continue
        try:
            p.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed


def stop_server_process() -> None:
    cleanup_all_uploads_on_shutdown()
    logging.shutdown()
    os._exit(0)


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
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Drop-Air-Code, X-Drop-Air-Key"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.route("/", methods=["GET"])
def index():
    cleanup_uploads()
    settings = get_settings()
    theme = (request.args.get("theme", "") or "").lower()
    index_tpl = "saved/index_v1.html" if theme == "classic" else "index.html"
    gate_tpl = "saved/code_gate_v1.html" if theme == "classic" else "code_gate.html"

    if not check_session_key():
        return session_error()
    if settings["access_code"] and request.args.get("code", "") == "":
        return render_template(gate_tpl, session_key=SESSION_KEY, session_param=SESSION_PARAM)
    if not check_code():
        return code_error()
    public_url = build_public_url(settings["access_code"])
    return render_template(
        index_tpl,
        files=list_files(),
        access_code=settings["access_code"],
        auth_query=build_auth_query(settings["access_code"]),
        session_key=SESSION_KEY,
        session_param=SESSION_PARAM,
        settings=settings,
        is_admin=is_admin_request(),
        public_url=public_url,
        local_url=build_local_url(settings["access_code"]),
        qr_svg=make_qr_svg(public_url),
        stats=upload_stats(),
        log_file=str(LOG_FILE),
        text_ttl_minutes=TEXT_TTL_MINUTES,
    )


@app.route("/enter", methods=["POST"])
def enter():
    settings = get_settings()
    code = request.form.get("code", "").strip()
    supplied_session = request.form.get(SESSION_PARAM, "").strip()
    theme = (request.args.get("theme", "") or request.form.get("theme", "")).lower()
    if not theme and "theme=classic" in (request.referrer or ""):
        theme = "classic"
    if not is_admin_request() and supplied_session != SESSION_KEY:
        return session_error()
    if not settings["access_code"] or code == settings["access_code"]:
        if settings["access_code"]:
            args = {SESSION_PARAM: supplied_session or SESSION_KEY, "code": code}
            if theme:
                args["theme"] = theme
            return redirect(url_for("index", **args))
        args = {SESSION_PARAM: supplied_session or SESSION_KEY}
        if theme:
            args["theme"] = theme
        return redirect(url_for("index", **args))
    return code_error()


@app.route("/api/files", methods=["GET", "OPTIONS"])
def api_files():
    cleanup_uploads()
    if request.method == "OPTIONS":
        return ("", 204)
    if not check_session_key():
        return jsonify({"error": "invalid session link"}), 404
    if not check_code():
        return jsonify({"error": "code not found"}), 404
    return jsonify({"files": list_files()})


@app.route("/api/upload", methods=["POST", "OPTIONS"])
def api_upload():
    cleanup_uploads()
    if request.method == "OPTIONS":
        return ("", 204)
    if not check_session_key():
        return jsonify({"error": "invalid session link"}), 404
    if not check_code():
        return jsonify({"error": "code not found"}), 404

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
    app.logger.info("Uploaded file %s (%s bytes).", target.name, target.stat().st_size)
    return jsonify({"ok": True, "filename": target.name, "size": target.stat().st_size})


@app.route("/api/text", methods=["GET", "POST", "OPTIONS"])
def api_text():
    if request.method == "OPTIONS":
        return ("", 204)
    if not check_session_key():
        return jsonify({"error": "invalid session link"}), 404
    if not check_code():
        return jsonify({"error": "code not found"}), 404

    if request.method == "GET":
        return jsonify({"items": list_text_items()})

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid payload"}), 400

    try:
        item = add_text_item(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    app.logger.info("Shared text item %s (%s chars).", item["id"], len(item["text"]))
    return jsonify({"ok": True, "item": item})


@app.route("/api/settings", methods=["GET", "POST", "OPTIONS"])
def api_settings():
    if request.method == "OPTIONS":
        return ("", 204)
    admin_error = require_admin()
    if admin_error:
        return admin_error
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

    if "launch_browser_on_start" in payload:
        updates["launch_browser_on_start"] = _coerce_bool(
            payload.get("launch_browser_on_start"),
            DEFAULT_SETTINGS["launch_browser_on_start"],
        )

    merged = dict(current)
    merged.update(updates)
    saved = set_settings(_normalize_settings(merged))
    cleanup_uploads()
    app.logger.info("Updated runtime settings.")
    return jsonify({"ok": True, "settings": saved})


@app.route("/api/admin", methods=["GET", "OPTIONS"])
def api_admin():
    if request.method == "OPTIONS":
        return ("", 204)
    admin_error = require_admin()
    if admin_error:
        return admin_error
    if not check_code():
        return jsonify({"error": "unauthorized"}), 401
    settings = get_settings()
    public_url = build_public_url(settings["access_code"])
    return jsonify(
        {
            "is_admin": True,
            "public_url": public_url,
            "local_url": build_local_url(settings["access_code"]),
            "qr_url": url_for("qr_code", url=public_url),
            "stats": upload_stats(),
            "settings": settings,
            "log_file": str(LOG_FILE),
            "text_ttl_minutes": TEXT_TTL_MINUTES,
        }
    )


@app.route("/api/admin/cleanup", methods=["POST", "OPTIONS"])
def api_admin_cleanup():
    if request.method == "OPTIONS":
        return ("", 204)
    admin_error = require_admin()
    if admin_error:
        return admin_error
    if not check_code():
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    target = str(payload.get("target", "")).strip().lower()
    removed_files = 0
    cleared_text = False
    if target in {"uploads", "all"}:
        removed_files = clear_uploads()
    if target in {"text", "all"}:
        clear_text_items()
        cleared_text = True
    if target not in {"uploads", "text", "all"}:
        return jsonify({"error": "target must be uploads, text, or all"}), 400
    app.logger.info("Admin cleanup target=%s removed_files=%s cleared_text=%s.", target, removed_files, cleared_text)
    return jsonify({"ok": True, "removed_files": removed_files, "cleared_text": cleared_text, "stats": upload_stats()})


@app.route("/api/admin/quit", methods=["POST", "OPTIONS"])
def api_admin_quit():
    if request.method == "OPTIONS":
        return ("", 204)
    admin_error = require_admin()
    if admin_error:
        return admin_error
    if not check_code():
        return jsonify({"error": "unauthorized"}), 401
    app.logger.info("Admin requested server quit.")
    Timer(0.35, stop_server_process).start()
    return jsonify({"ok": True, "message": "Drop Air is shutting down."})


@app.route("/qr.svg", methods=["GET"])
def qr_code():
    url = request.args.get("url", build_public_url())
    return Response(make_qr_svg(url), mimetype="image/svg+xml")


@app.route("/favicon.ico", methods=["GET"])
def favicon():
    return Response(status=204)


@app.route("/files/<path:filename>", methods=["GET"])
def download_file(filename: str):
    if not check_session_key():
        return session_error()
    if not check_code():
        return code_error()
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
    set_console_window_icon()
    host = "0.0.0.0"
    port = int(os.getenv("PORT", "8000"))
    settings = get_settings()
    access_code = settings["access_code"]
    url = build_public_url(access_code)
    local_url = build_local_url(access_code)

    print(f"Drop Air running on {url}")
    print(f"Admin dashboard on {local_url}")
    print("Data folder:", DATA_DIR)
    print("Uploads folder:", UPLOAD_DIR)
    print("Log file:", LOG_FILE)
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
    if settings["launch_browser_on_start"]:
        Timer(1.0, lambda: webbrowser.open(local_url)).start()
    configure_request_logging()
    app.run(host=host, port=port, debug=False)
