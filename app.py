"""Drop Air local server, Python edition (final release).

Serves the same web/ interface and the same HTTP API as the Rust edition.
Run ``python desktop_app.py`` for the tray app or ``python app.py`` for the server only.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from socketserver import TCPServer
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen

import qrcode
import qrcode.image.svg
from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.serving import ThreadedWSGIServer, WSGIRequestHandler

from discovery import PairingRequests, PeerDiscovery, clean_device_name, route_ipv4

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
WEB_DIR = BUNDLE_DIR / "web"


def read_version() -> str:
    for candidate in (BUNDLE_DIR / "VERSION", APP_DIR / "VERSION"):
        try:
            return candidate.read_text(encoding="utf-8").strip().lstrip("v")
        except OSError:
            continue
    return "1.7.4"


APP_VERSION = read_version()


def data_dir() -> Path:
    override = os.getenv("DROP_AIR_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if (APP_DIR / "portable.mode").is_file():
        return APP_DIR / "data"
    base = os.getenv("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "DropAir"


DATA_DIR = data_dir()
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

STAGING_PREFIX = ".dropair-"
BUFFER = 1024 * 1024
CLIENT_TTL = 30
MAX_TEXT_ITEMS = 40
MAX_TEXT_CHARS = 20_000
COOKIE = "dropair_session_recovery"
RELEASES_URL = "https://api.github.com/repos/B1progame/drop-air/releases?per_page=30"
ASSET_PREFIX = "https://github.com/B1progame/drop-air/"


def env_number(name: str, fallback: float) -> float:
    try:
        return float(os.getenv(name, "") or fallback)
    except ValueError:
        return fallback


# The link key changes every 5 minutes; the old one keeps working 2 more.
SESSION_TTL = int(env_number("DROP_AIR_SESSION_TTL_SECONDS", 300))
SESSION_GRACE = int(env_number("DROP_AIR_SESSION_GRACE_SECONDS", 120))
TEXT_TTL_MINUTES = int(env_number("DROP_AIR_TEXT_TTL_MINUTES", 10))
DEFAULT_SETTINGS = {
    "access_code": os.getenv("DROP_AIR_CODE", "").strip(),
    "auto_cleanup_minutes": int(env_number("DROP_AIR_AUTO_CLEANUP_MINUTES", 10)),
    "auto_cleanup_days": int(env_number("DROP_AIR_AUTO_CLEANUP_DAYS", 0)),
    "auto_cleanup_max_files": int(env_number("DROP_AIR_AUTO_CLEANUP_MAX_FILES", 0)),
    "max_upload_gb": env_number("DROP_AIR_MAX_UPLOAD_GB", 10.0),
    "launch_browser_on_start": True,
    "device_name": "Drop Air device",
    "discoverable": False,
    "tray_sleep_minutes": 10,  # hidden this long: sharing turns off; 0 = never
}


def write_atomic(path: Path, text: str) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path, fallback):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, type(fallback)) else fallback
    except (OSError, ValueError):
        return fallback


def read_or_create(path: Path, pattern: str, make) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
        if re.fullmatch(pattern, value):
            return value
    except OSError:
        pass
    value = make()
    path.write_text(value, encoding="utf-8")
    return value


LOCK = threading.RLock()
DEVICE_ID = read_or_create(DATA_DIR / "device-id", r"[0-9a-f]{32}", lambda: secrets.token_hex(16))
# A new key and cookie secret every start: links from before a restart stop working.
RECOVERY_SECRET = secrets.token_hex(32)
SETTINGS = {**DEFAULT_SETTINGS, **{k: v for k, v in read_json(DATA_DIR / "settings.json", {}).items() if k in DEFAULT_SETTINGS}}
SETTINGS["device_name"] = clean_device_name(SETTINGS["device_name"])
TEXTS = [
    {"id": str(item["id"]), "text": str(item.get("text", "")), "created": int(item.get("created", 0)),
     "to": str(item.get("to", "")), "from": str(item.get("from", ""))}
    for item in read_json(DATA_DIR / "text_items.json", [])
    if isinstance(item, dict) and "id" in item
]
SESSION = {"key": secrets.token_hex(16), "expires_at": time.time() + SESSION_TTL, "previous": {}}
CLIENTS: dict[str, dict] = {}
BLOCKED: dict[str, str] = {}  # devices the host removed, until Drop Air quits: address -> name
ROUTES: dict[str, tuple[str, str]] = {}  # file name -> (recipient, sender) for private sends
PUBLISHING: set[str] = set()
ASLEEP = threading.Event()  # sharing is off while the app has been in the tray too long
UPDATE = {"running": False, "percent": 0, "message": "", "error": ""}

PORT = 0  # chosen when the server starts
PEERS = PeerDiscovery(DEVICE_ID, lambda: dict(SETTINGS))
PAIRS = PairingRequests()
# The desktop shell replaces these.
on_show = lambda: None
on_quit = lambda: os._exit(0)


def session() -> tuple[str, int]:
    with LOCK:
        now = time.time()
        SESSION["previous"] = {k: e for k, e in SESSION.get("previous", {}).items() if e > now}
        if now >= SESSION.get("expires_at", 0):
            # Grace counts from when the key expired, not from when somebody next asked.
            expired_at = SESSION.get("expires_at", 0)
            if expired_at + SESSION_GRACE > now:
                SESSION["previous"][SESSION["key"]] = expired_at + SESSION_GRACE
            SESSION["key"] = secrets.token_hex(16)
            SESSION["expires_at"] = now + SESSION_TTL
        return SESSION["key"], int(SESSION["expires_at"] - now)


def sleep() -> None:
    """Turn sharing off. Every link handed out so far stops working."""
    ASLEEP.set()
    with LOCK:
        SESSION.update(key=secrets.token_hex(16), previous={}, expires_at=time.time() + SESSION_TTL)
        CLIENTS.clear()


def wake() -> None:
    """Sharing is back at once, with a QR code that has its full time left."""
    if ASLEEP.is_set():
        ASLEEP.clear()
        with LOCK:
            SESSION["expires_at"] = time.time() + SESSION_TTL


def public_url() -> str:
    address = route_ipv4()
    url = f"http://{'127.0.0.1' if address == '0.0.0.0' else address}:{PORT}/?k={session()[0]}"
    if SETTINGS["access_code"]:
        url += f"&code={quote(SETTINGS['access_code'], safe='')}"
    return url + "&view=transfers"


def local_url() -> str:
    return f"http://127.0.0.1:{PORT}/?desktop=1"


def safe_file_name(raw: str) -> str:
    """Plain Windows-safe name that keeps Unicode."""
    last = re.split(r"[/\\]", raw)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f\x7f]', "_", last).lstrip(". ").rstrip(". ")[:180]
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM\d|LPT\d)", name.split(".")[0]):
        name = "_" + name
    return name or f"file_{int(time.time())}"


def numbered_name(name: str, index: int) -> str:
    if not index:
        return name
    dot = name.rfind(".")
    return f"{name[:dot]}_{index}{name[dot:]}" if dot > 0 else f"{name}_{index}"


def file_path(name: str) -> Path | None:
    if not name or re.search(r"[/\\:]", name) or name == ".." or name.startswith(STAGING_PREFIX):
        return None
    path = UPLOAD_DIR / name
    return path if path.is_file() and not path.is_symlink() else None


def clean_ids(value: str) -> str:
    """A "to" value: one page id or several separated by commas."""
    ids = (re.sub(r"[^A-Za-z0-9]", "", part)[:64] for part in value.split(","))
    return ",".join([part for part in ids if part][:16])


def addressed_to(item: dict, ids: set[str]) -> bool:
    return not item["to"] or item["from"] in ids or not ids.isdisjoint(item["to"].split(","))


def list_files() -> list[dict]:
    files = []
    with os.scandir(UPLOAD_DIR) as entries:
        for entry in entries:
            if entry.name.startswith(STAGING_PREFIX) or entry.name in PUBLISHING:
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                to, sender = ROUTES.get(entry.name, ("", ""))
                files.append(
                    {"name": entry.name, "size": info.st_size, "modified": int(info.st_mtime), "to": to, "from": sender}
                )
    files.sort(key=lambda item: item["modified"], reverse=True)
    return files


def delete_file(name: str) -> bool:
    path = file_path(name)
    if not path:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    ROUTES.pop(name, None)
    return True


def clear_uploads() -> None:
    ROUTES.clear()
    for entry in UPLOAD_DIR.iterdir():
        try:
            entry.unlink()
        except OSError:
            pass


def retain_texts(keep) -> bool:
    with LOCK:
        kept = [item for item in TEXTS if keep(item)]
        if len(kept) == len(TEXTS):
            return False
        TEXTS[:] = kept
        write_atomic(DATA_DIR / "text_items.json", json.dumps(TEXTS))
        return True


def cleanup() -> None:
    now = time.time()
    ages = [SETTINGS["auto_cleanup_minutes"] * 60, SETTINGS["auto_cleanup_days"] * 86400]
    max_age = min((age for age in ages if age > 0), default=0)
    kept = 0
    for item in list_files():
        expired = max_age and now - item["modified"] > max_age
        overflow = 0 < SETTINGS["auto_cleanup_max_files"] <= kept
        if expired or overflow:
            delete_file(item["name"])
        else:
            kept += 1
    if TEXT_TTL_MINUTES > 0:
        retain_texts(lambda item: item["created"] >= now - TEXT_TTL_MINUTES * 60)
    with LOCK:
        for key, client in list(CLIENTS.items()):
            if now - client["seen"] > CLIENT_TTL:
                del CLIENTS[key]


def shutdown() -> None:
    PEERS.stop()
    clear_uploads()
    retain_texts(lambda _item: False)
    (DATA_DIR / "port").unlink(missing_ok=True)


# --- HTTP -------------------------------------------------------------------

app = Flask(__name__, static_folder=None)


class ApiError(HTTPException):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.code = status
        self.message = message


@app.errorhandler(ApiError)
def api_error(error: ApiError):
    return jsonify({"error": error.message}), error.code


def is_loopback_authority(value: str) -> bool:
    return re.fullmatch(r"(127\.0\.0\.1|localhost|\[::1\])(:\d+)?", value) is not None


def is_admin() -> bool:
    """Loopback socket AND loopback page, so other websites cannot drive the host."""
    origin = request.headers.get("Origin", "")
    return (
        request.remote_addr in {"127.0.0.1", "::1"}
        and is_loopback_authority(request.headers.get("Host", ""))
        and (not origin or (origin.startswith("http://") and is_loopback_authority(origin[7:])))
    )


def require_admin() -> None:
    if not is_admin():
        raise ApiError(403, "admin access is only available on the host machine")


def authorize() -> None:
    if is_admin():
        return
    if request.remote_addr in BLOCKED:
        raise ApiError(403, "removed by host")
    if ASLEEP.is_set():
        raise ApiError(503, "sharing is off")
    key = request.args.get("k") or request.headers.get("X-Drop-Air-Key", "")
    current, _ = session()
    cookie_ok = secrets.compare_digest(request.cookies.get(COOKIE, "").encode(), RECOVERY_SECRET.encode())
    if not (key == current or SESSION["previous"].get(key, 0) > time.time() or cookie_ok):
        raise ApiError(404, "invalid session link")
    code = SETTINGS["access_code"]
    supplied = request.args.get("code")
    if supplied is None:
        supplied = unquote(request.headers.get("X-Drop-Air-Code", ""))
    if code and supplied != code:
        raise ApiError(404, "code not found")


def payload() -> dict:
    if (request.content_length or 0) > 256 * 1024:
        raise ApiError(413, "request too large")
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ApiError(400, "invalid payload")
    return value


@app.after_request
def common_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Drop-Air-Code, X-Drop-Air-Key"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/")
def index():
    # A removed device gets the page without a cookie, so it can say what happened.
    if (ASLEEP.is_set() or request.remote_addr in BLOCKED) and not is_admin():
        return send_from_directory(WEB_DIR, "index.html")
    try:
        authorize()
    except ApiError as error:
        if error.message != "code not found":
            return Response(
                "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>Link not found</title>"
                "<body style='font:16px system-ui;display:grid;place-items:center;min-height:90vh;text-align:center'>"
                "<div><h1>Link not found</h1><p>Scan the current QR code from the host PC.</p></div>",
                404,
                mimetype="text/html",
            )
    response = send_from_directory(WEB_DIR, "index.html")
    response.set_cookie(COOKIE, RECOVERY_SECRET, max_age=30 * 86400, httponly=True, samesite="Lax")
    return response


@app.get("/assets/<name>")
def asset(name: str):
    types = {"app.css": "text/css", "app.js": "text/javascript", "logo.png": "image/png"}
    if name not in types:
        return "", 404
    response = send_from_directory(WEB_DIR, name, mimetype=types[name])
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(WEB_DIR, "logo.png")


@app.get("/qr.svg")
def qr_svg():
    url = request.args.get("url") or public_url()
    buffer = io.BytesIO()
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathFillImage, border=4).save(buffer)
    return Response(buffer.getvalue(), mimetype="image/svg+xml")


def same_device(client_id: str) -> set[str]:
    """Every page id that belongs to the same device as ``client_id``."""
    clients = CLIENTS.copy()
    me = clients.get(client_id)
    ids = {client_id}
    if me:
        ids.update(k for k, c in clients.items() if (c["ip"], c["name"]) == (me["ip"], me["name"]))
    return ids


def clients_list(details: bool) -> list[dict]:
    """One entry per device: pages with the same name and address (a second browser or
    private tab) are merged. Addresses and details are only for the host."""
    devices: dict[tuple, list] = {}
    for key, client in CLIENTS.copy().items():
        devices.setdefault((client["ip"], client["name"]), []).append((key, client))
    result = []
    for pages in devices.values():
        # The newest page that told us about itself stands for the device.
        key, client = max(pages, key=lambda page: (bool(page[1]["os"]), page[1]["since"], page[0]))
        entry = {"id": key, "ids": [k for k, _ in pages], "name": client["name"], "os": client["os"]}
        if details:
            since = min(c["since"] for _, c in pages)
            entry.update(ip=client["ip"], browser=client["browser"], screen=client["screen"],
                         connected_seconds=int(time.time() - since))
        result.append(entry)
    return sorted(result, key=lambda entry: entry["id"])


@app.get("/api/state")
def api_state():
    authorize()
    admin = is_admin()
    client_id = re.sub(r"[^A-Za-z0-9]", "", request.args.get("client_id", ""))[:64]
    if not admin and client_id:
        def text(field: str) -> str:
            return "".join(c for c in request.args.get(field, "") if c.isprintable())[:48]

        with LOCK:
            if len(CLIENTS) < 64 or client_id in CLIENTS:
                CLIENTS[client_id] = {
                    "name": clean_device_name(request.args.get("name", "Browser")),
                    "seen": time.time(),
                    "since": CLIENTS.get(client_id, {}).get("since", time.time()),
                    "ip": request.remote_addr,
                    "os": text("os"),
                    "browser": text("browser"),
                    "screen": text("screen"),
                }
    cleanup()
    mine = same_device(client_id)
    key, remaining = session()
    gb = float(SETTINGS["max_upload_gb"])
    discovery = PEERS.snapshot()
    body = {
        "version": APP_VERSION,
        "edition": "python",
        "admin": admin,
        "device_id": DEVICE_ID,
        "device_name": SETTINGS["device_name"],
        "session_key": key,
        "seconds_remaining": remaining,
        "public_url": public_url(),
        "limit_bytes": int(gb * 1024**3),
        "limit_label": f"{gb:g} GB" if gb >= 1 else f"{gb * 1024:.0f} MB",
        "cleanup_minutes": SETTINGS["auto_cleanup_minutes"],
        "text_ttl_minutes": TEXT_TTL_MINUTES,
        "files": [f for f in list_files() if admin or addressed_to(f, mine)],
        "texts": [t for t in list(TEXTS) if admin or addressed_to(t, mine)],
        "clients": clients_list(admin),
        "peers": discovery["peers"],
        "discovery_status": discovery["status"],
    }
    if admin:
        body["settings"] = SETTINGS
        body["requests"] = PAIRS.pending()
        body["blocked"] = [{"ip": ip, "name": name} for ip, name in list(BLOCKED.items())]
    return jsonify(body)


@app.post("/api/upload")
def upload():
    authorize()
    limit = int(float(SETTINGS["max_upload_gb"]) * 1024**3)
    expected = request.content_length
    if expected is None or not request.args.get("name"):
        raise ApiError(400, "missing file")
    if expected > limit:
        raise ApiError(413, f"Upload is larger than {float(SETTINGS['max_upload_gb']):g} GB.")
    name = safe_file_name(request.args["name"])
    staging = None
    try:
        # The raw body goes straight to disk once: no multipart parsing, no second copy.
        handle, staging_name = tempfile.mkstemp(prefix=STAGING_PREFIX + "staging-", dir=UPLOAD_DIR)
        staging = Path(staging_name)
        size = 0
        with os.fdopen(handle, "wb") as out:
            while chunk := request.stream.read(BUFFER):
                size += len(chunk)
                out.write(chunk)
        if size != expected:
            raise ApiError(400, "Upload was interrupted.")
        index = 0
        while True:
            candidate = numbered_name(name, index)
            target = UPLOAD_DIR / candidate
            try:
                os.link(staging, target)
                break
            except FileExistsError:
                index += 1
            except OSError:
                if target.exists():
                    index += 1
                    continue
                staging.rename(target)
                break
    except OSError as error:
        raise ApiError(500, f"Could not save upload: {error.strerror or error}") from error
    finally:
        if staging:
            staging.unlink(missing_ok=True)

    def clean(field: str) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", request.args.get(field, ""))[:64]

    to = clean_ids(request.args.get("to", ""))
    if to:
        ROUTES[candidate] = (to, clean("from"))
    else:
        ROUTES.pop(candidate, None)
    cleanup()
    return jsonify({"ok": True, "filename": candidate, "size": size})


@app.get("/files/<name>")
def download(name: str):
    authorize()
    path = file_path(name)
    if not path:
        raise ApiError(404, "file not found")
    size = path.stat().st_size
    start, end, status = 0, size - 1, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", request.headers.get("Range", ""))
    if match and any(match.groups()):
        first, last = match.groups()
        if first:
            start, end = int(first), min(int(last or size - 1), size - 1)
        else:
            start = max(size - int(last), 0)
        if start >= size or start > end:
            return Response(status=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206
    length = end - start + 1

    def chunks():
        with path.open("rb") as source:
            source.seek(start)
            remaining = length
            while remaining > 0 and (chunk := source.read(min(BUFFER, remaining))):
                remaining -= len(chunk)
                yield chunk

    response = Response(chunks(), status, mimetype="application/octet-stream", direct_passthrough=True)
    response.headers["Content-Length"] = str(length)
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name, safe='')}"
    if status == 206:
        response.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return response


@app.delete("/api/files/<name>")
def api_delete_file(name: str):
    authorize()
    if not delete_file(name):
        raise ApiError(404, "file not found")
    return jsonify({"ok": True})


@app.post("/api/text")
def add_text():
    authorize()
    data = payload()
    text = str(data.get("text", ""))[:MAX_TEXT_CHARS]
    if not text.strip():
        raise ApiError(400, "Paste text before sharing.")
    to = clean_ids(str(data.get("to", "")))
    sender = re.sub(r"[^A-Za-z0-9]", "", str(data.get("from", "")))[:64]
    item = {"id": secrets.token_hex(8), "text": text, "created": int(time.time()), "to": to, "from": sender if to else ""}
    with LOCK:
        TEXTS.insert(0, item)
        del TEXTS[MAX_TEXT_ITEMS:]
        write_atomic(DATA_DIR / "text_items.json", json.dumps(TEXTS))
    return jsonify({"ok": True, "item": item})


@app.delete("/api/text/<item_id>")
def delete_text(item_id: str):
    authorize()
    if not retain_texts(lambda item: item["id"] != item_id):
        raise ApiError(404, "text item not found")
    return jsonify({"ok": True})


@app.post("/api/settings")
def save_settings():
    require_admin()
    data = payload()
    updated = dict(SETTINGS)
    if "access_code" in data:
        updated["access_code"] = str(data["access_code"]).strip()
    for field in ("auto_cleanup_minutes", "auto_cleanup_days", "auto_cleanup_max_files", "tray_sleep_minutes"):
        if field in data:
            value = data[field]
            if type(value) is not int or not 0 <= value <= 1_000_000:
                raise ApiError(400, f"{field} must be a non-negative integer")
            updated[field] = value
    if "max_upload_gb" in data:
        value = data["max_upload_gb"]
        if type(value) not in (int, float) or not 0.01 <= value <= 1024:
            raise ApiError(400, "max_upload_gb must be between 0.01 and 1024")
        updated["max_upload_gb"] = round(float(value), 3)
    if "device_name" in data:
        name = data["device_name"]
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 32:
            raise ApiError(400, "device_name must contain 1 to 32 characters")
        updated["device_name"] = clean_device_name(name)
    for field in ("launch_browser_on_start", "discoverable"):
        if field in data:
            updated[field] = data[field] is True
    try:
        write_atomic(DATA_DIR / "settings.json", json.dumps(updated, indent=2))
    except OSError as error:
        raise ApiError(500, "Settings could not be saved to disk.") from error
    SETTINGS.update(updated)
    cleanup()
    return jsonify({"ok": True, "settings": SETTINGS})


@app.post("/api/pair/requests")
def pair_create():
    if request.remote_addr in BLOCKED:
        raise ApiError(403, "removed by host")
    if ASLEEP.is_set():
        raise ApiError(503, "sharing is off")
    if not SETTINGS["discoverable"]:
        raise ApiError(404, "this Drop Air device is not discoverable")
    item, error = PAIRS.create(request.remote_addr or "", payload())
    if error:
        raise ApiError(400 if error.startswith("invalid") else 429, error)
    on_show()
    return jsonify({"request": item}), 202


@app.get("/api/pair/requests/<request_id>")
def pair_status(request_id: str):
    result = PAIRS.status(request.remote_addr or "", request_id)
    if result is None:
        raise ApiError(404, "request expired or not found")
    if result["decision"] == "accepted":
        result["share_url"] = public_url()
    return jsonify(result)


@app.post("/api/admin/pair/<request_id>")
def pair_decide(request_id: str):
    require_admin()
    decision = payload().get("decision")
    if decision not in ("accept", "decline"):
        raise ApiError(400, "decision must be accept or decline")
    accepted = decision == "accept"
    if not PAIRS.decide(request_id, accepted, public_url() if accepted else ""):
        raise ApiError(404, "This request expired or was already handled.")
    return jsonify({"ok": True})


@app.post("/api/admin/kick/<client_id>")
def kick(client_id: str):
    """Block the device's address and replace the secrets it has seen."""
    global RECOVERY_SECRET
    require_admin()
    with LOCK:
        client = CLIENTS.get(client_id)
        if not client:
            raise ApiError(404, "That device is no longer connected.")
        BLOCKED[client["ip"]] = client["name"]
        for key, other in list(CLIENTS.items()):
            if other["ip"] == client["ip"]:
                del CLIENTS[key]
        RECOVERY_SECRET = secrets.token_hex(32)
        SESSION["expires_at"] = time.time()  # rotate now; others get the grace time
    session()
    return jsonify({"ok": True})


@app.post("/api/admin/unblock")
def unblock():
    require_admin()
    BLOCKED.pop(str(payload().get("ip", "")), None)
    return jsonify({"ok": True})


def relay(device: str, path: str, body: dict | None = None):
    """Forward a pairing call to another Drop Air host that discovery found."""
    peer = PEERS.get_peer(device)
    if not peer:
        raise ApiError(404, "That device is no longer nearby.")
    outgoing = Request(
        f"http://{peer['address']}:{peer['port']}{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urlopen(outgoing, timeout=4) as response:
            return jsonify(json.load(response)), response.status
    except HTTPError as error:
        try:
            return jsonify(json.load(error)), error.code
        except ValueError:
            raise ApiError(502, f"The nearby device returned HTTP {error.code}.") from error
    except (OSError, URLError, ValueError) as error:
        raise ApiError(502, "Could not reach that device. Check Wi-Fi and Windows Firewall.") from error


@app.post("/api/discovery/<device>/pair")
def relay_pair(device: str):
    authorize()
    return relay(device, "/api/pair/requests", payload())


@app.get("/api/discovery/<device>/pair/<request_id>")
def relay_status(device: str, request_id: str):
    authorize()
    if not re.fullmatch(r"[0-9a-f]{48}", request_id):
        raise ApiError(400, "Invalid connection request.")
    return relay(device, f"/api/pair/requests/{request_id}")


def downloads_folder() -> str:
    """The real Downloads folder, even when the user moved it."""
    try:
        import winreg

        shell_folders = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, shell_folders) as key:
            value, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
        return os.path.expandvars(value)
    except (ImportError, OSError):
        return str(Path.home() / "Downloads")


@app.post("/api/admin/save/<name>")
def save_to_downloads(name: str):
    require_admin()
    source = file_path(name)
    if not source:
        raise ApiError(404, "file not found")
    folder = Path(os.getenv("DROP_AIR_DOWNLOAD_DIR") or downloads_folder())
    try:
        folder.mkdir(parents=True, exist_ok=True)
        index = 0
        while True:
            candidate = numbered_name(name, index)
            try:
                with (folder / candidate).open("xb") as out, source.open("rb") as incoming:
                    shutil.copyfileobj(incoming, out, BUFFER)
                return jsonify({"ok": True, "filename": candidate})
            except FileExistsError:
                index += 1
    except OSError as error:
        raise ApiError(500, f"Could not save the file: {error.strerror or error}") from error


@app.post("/api/admin/reveal")
def reveal():
    require_admin()
    subprocess.Popen(["explorer", str(UPLOAD_DIR)])
    return jsonify({"ok": True})


@app.post("/api/admin/firewall")
def firewall():
    require_admin()
    subprocess.Popen(["control.exe", "firewall.cpl"])
    return jsonify({"ok": True})


@app.post("/api/admin/cleanup")
def admin_cleanup():
    require_admin()
    target = payload().get("target")
    if target not in ("uploads", "text", "all"):
        raise ApiError(400, "target must be uploads, text, or all")
    if target != "text":
        clear_uploads()
    if target != "uploads":
        retain_texts(lambda _item: False)
    return jsonify({"ok": True})


@app.post("/api/admin/rediscover")
def rediscover():
    require_admin()
    PEERS.restart(PORT)
    return jsonify({"ok": True})


@app.post("/api/admin/show")
def show():
    require_admin()
    on_show()
    return jsonify({"ok": True})


@app.post("/api/admin/quit")
def quit_app():
    require_admin()
    threading.Timer(0.5, lambda: on_quit()).start()
    return jsonify({"ok": True})


# --- Switching to the Rust edition ------------------------------------------


def find_rust_release() -> dict | None:
    """Newest 2.x+ installer. The Python edition itself gets no more updates."""
    outgoing = Request(RELEASES_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": "DropAir"})
    with urlopen(outgoing, timeout=15) as response:
        releases = json.load(response)
    found = []
    for release in releases:
        version = tuple(int(n) for n in re.findall(r"\d+", release.get("tag_name", ""))[:3])
        if release.get("draft") or release.get("prerelease") or not version or version[0] < 2:
            continue
        for item in release.get("assets", []):
            name = item.get("name", "").lower()
            url = item.get("browser_download_url", "")
            if name.endswith(".exe") and "setup" in name and url.startswith(ASSET_PREFIX):
                found.append((version, item))
                break
    return max(found, key=lambda pair: pair[0])[1] if found else None


@app.get("/api/admin/update")
def update_info():
    require_admin()
    try:
        item = find_rust_release()
    except (OSError, ValueError) as error:
        raise ApiError(502, f"Could not check GitHub: {error}") from error
    message = "The Rust version is available." if item else "No Rust release found yet."
    return jsonify({"update_available": bool(item), "message": message, "current_version": APP_VERSION})


def switch_to_rust() -> None:
    try:
        item = find_rust_release()
        if not item:
            raise RuntimeError("No Rust release found yet.")
        target = Path(tempfile.mkdtemp(prefix="drop-air-rust-")) / Path(item["name"]).name
        outgoing = Request(item["browser_download_url"], headers={"User-Agent": "DropAir"})
        with urlopen(outgoing, timeout=30) as response, target.open("wb") as out:
            done = 0
            while chunk := response.read(256 * 1024):
                out.write(chunk)
                done += len(chunk)
                UPDATE["percent"] = min(100, done * 100 // max(item.get("size", 0), 1))
        if item.get("size") and done != item["size"]:
            raise RuntimeError("The download was incomplete.")
        os.startfile(target)  # Windows asks for permission to install.
        UPDATE.update(running=False, percent=100, message="Starting the installer…")
        on_quit()
    except Exception as error:  # shown to the user in Settings
        UPDATE.update(running=False, percent=0, message=f"Switch failed: {error}", error=str(error))


@app.post("/api/admin/update")
def update_start():
    require_admin()
    if not UPDATE["running"]:
        UPDATE.update(running=True, percent=0, message="Downloading the Rust version…", error="")
        threading.Thread(target=switch_to_rust, daemon=True).start()
    return jsonify({"ok": True})


@app.get("/api/admin/update/status")
def update_status():
    require_admin()
    return jsonify(UPDATE)


# --- Server -----------------------------------------------------------------


class QuietHandler(WSGIRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_request(self, *args, **kwargs):
        pass


class LocalServer(ThreadedWSGIServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind calls getfqdn(), which can stall on bad DNS.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]


def port_is_free(port: int) -> bool:
    with socket.socket() as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def port_candidates() -> list[int]:
    """Each PC gets its own random port and keeps it, so links survive restarts. ``PORT`` overrides it."""
    forced = int(env_number("PORT", 0))
    if forced:
        return list(range(forced, forced + 50))
    try:
        saved = [int((DATA_DIR / "preferred-port").read_text(encoding="ascii"))]
    except (OSError, ValueError):
        saved = []
    return saved + [20_000 + secrets.randbelow(29_000) for _ in range(50)]


def remove_leftovers() -> None:
    """Delete what older releases left behind, but never the folder we are running from.

    Some old installs ran from their rollback copy, so their updater installed into it.
    """
    program = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    for name in ("rollback", "browser-profile"):
        folder = (DATA_DIR / name).resolve()
        if folder not in program.parents:
            shutil.rmtree(folder, ignore_errors=True)


def start_server():
    """Bind the first free port and serve in a background thread."""
    global PORT
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    port = next((p for p in port_candidates() if port_is_free(p)), None)
    if port is None:
        raise OSError("No free port found.")
    server = LocalServer("0.0.0.0", port, app, handler=QuietHandler)
    PORT = server.server_port
    (DATA_DIR / "port").write_text(str(PORT), encoding="ascii")
    if not os.getenv("PORT"):
        (DATA_DIR / "preferred-port").write_text(str(PORT), encoding="ascii")
    threading.Thread(target=server.serve_forever, name="DropAirWeb", daemon=True).start()

    def maintain():
        remove_leftovers()
        while True:
            try:
                cleanup()
            except OSError:
                pass
            time.sleep(15)

    threading.Thread(target=maintain, name="DropAirCleanup", daemon=True).start()
    PEERS.start(PORT)
    return server


if __name__ == "__main__":
    start_server()
    print(f"Drop Air {APP_VERSION} on {local_url()}", flush=True)
    print(public_url(), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        shutdown()
