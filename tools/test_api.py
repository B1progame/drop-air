"""End-to-end API check for either edition.

    python tools/test_api.py rust  path/to/DropAir.exe
    python tools/test_api.py python path/to/python.exe

Starts a throwaway server in a temporary data folder and talks to it as the host
(loopback) and as a phone (this PC's LAN address).
"""

import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from discovery import route_ipv4  # noqa: E402

edition, program = sys.argv[1], sys.argv[2]
work = Path(tempfile.mkdtemp(prefix="dropair-test-"))
data, downloads = work / "data", work / "downloads"
PORT = 8765 if edition == "rust" else 8766
env = {**os.environ, "DROP_AIR_DATA_DIR": str(data), "DROP_AIR_DOWNLOAD_DIR": str(downloads), "PORT": str(PORT)}
command = [program, "--headless"] if edition == "rust" else [program, str(ROOT / "app.py")]
server = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
LAN = route_ipv4()
failures = []


def call(method, path, body=None, headers=None, host="127.0.0.1"):
    connection = http.client.HTTPConnection(host, PORT, timeout=20)
    if isinstance(body, dict):
        body = json.dumps(body).encode()
        headers = {"Content-Type": "application/json", **(headers or {})}
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    content = response.read()
    result = response.status, dict((k.lower(), v) for k, v in response.getheaders()), content
    connection.close()
    return result


def check(label, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + label + ("" if condition else f"  -> {detail}"))
    if not condition:
        failures.append(label)


try:
    for _ in range(100):
        try:
            call("GET", "/api/state")
            break
        except OSError:
            time.sleep(0.1)

    status, _, body = call("GET", "/api/state")
    state = json.loads(body)
    check("host sees admin state", status == 200 and state["admin"] and "settings" in state, body[:200])
    check("edition reported", state["edition"] == edition)
    key = state["session_key"]

    for path, kind in [("/", "text/html"), ("/assets/app.js", "text/javascript"), ("/assets/app.css", "text/css"),
                       ("/assets/logo.png", "image/png"), ("/qr.svg", "image/svg+xml")]:
        status, headers, content = call("GET", path)
        check(f"serves {path}", status == 200 and headers["content-type"].startswith(kind) and content, (status, headers))

    status, _, _ = call("GET", "/api/state", host=LAN)
    check("phone without key is refused", status == 404, status)
    status, _, body = call("GET", f"/api/state?k={key}&client_id=phone1&name=iPhone", host=LAN)
    phone = json.loads(body)
    check("phone with key connects, not admin", status == 200 and not phone["admin"] and "settings" not in phone, body[:200])
    check("phone appears as a device", any(c["id"] == "phone1" for c in phone["clients"]), phone["clients"])
    status, _, _ = call("POST", f"/api/admin/quit?k={key}", host=LAN)
    check("phone cannot use admin calls", status == 403, status)
    status, _, _ = call("POST", "/api/admin/quit", headers={"Origin": "https://evil.example"})
    check("another website cannot use admin calls", status == 403, status)
    status, _, _ = call("GET", "/api/state", headers={"Origin": "https://evil.example"})
    check("another website cannot read host state", status == 404, status)

    payload = os.urandom(3 * 1024 * 1024 + 17)
    name = "Übersicht 2026.bin"
    status, _, body = call("POST", f"/api/upload?name={quote(name)}&k={key}", payload, host=LAN)
    check("phone upload keeps unicode name", status == 200 and json.loads(body)["filename"] == name, body)
    status, _, body = call("POST", f"/api/upload?name={quote(name)}&k={key}", payload, host=LAN)
    check("duplicate gets a number", json.loads(body)["filename"] == "Übersicht 2026_1.bin", body)
    status, headers, content = call("GET", f"/files/{quote(name)}?k={key}", host=LAN)
    check("download is byte-identical", status == 200 and hashlib.sha256(content).digest() == hashlib.sha256(payload).digest())
    status, headers, content = call("GET", f"/files/{quote(name)}?k={key}", headers={"Range": "bytes=100-199"}, host=LAN)
    check("download resumes by range", status == 206 and content == payload[100:200], (status, len(content)))
    status, _, _ = call("GET", f"/files/{quote(name)}", host=LAN)
    check("download needs the key", status == 404, status)

    for bad in ["..%2Fsettings.json", "..%5Csettings.json", "%2E%2E"]:
        status, _, _ = call("GET", f"/files/{bad}?k={key}", host=LAN)
        check(f"traversal blocked: {bad}", status == 404, status)
    status, _, body = call("POST", f"/api/upload?name={quote('..%/..' + chr(92) + 'evil.txt')}&k={key}", b"x", host=LAN)
    check("upload path is flattened", json.loads(body)["filename"] == "evil.txt", body)

    call("POST", f"/api/upload?name=private.txt&to=phone2&from=pc&k={key}", b"secret", host=LAN)
    seen = lambda client: [f["name"] for f in json.loads(call("GET", f"/api/state?k={key}&client_id={client}&name={client}", host=LAN)[2])["files"]]
    check("private send reaches its device", "private.txt" in seen("phone2"))
    check("private send hidden from others", "private.txt" not in seen("phone1"))

    # One phone with two pages (second tab storage, second browser) is one device.
    call("GET", f"/api/state?k={key}&client_id=tabA&name=Pixel", host=LAN)
    time.sleep(1.1)
    call("GET", f"/api/state?k={key}&client_id=tabB&name=Pixel&os=Android+15", host=LAN)
    pixels = [c for c in json.loads(call("GET", "/api/state")[2])["clients"] if c["name"] == "Pixel"]
    check("one device is listed once", len(pixels) == 1, pixels)
    check("the entry with details wins", pixels[0]["id"] == "tabB" and pixels[0]["os"] == "Android 15", pixels)
    check("it knows all its pages", sorted(pixels[0].get("ids", [])) == ["tabA", "tabB"], pixels)
    call("POST", f"/api/upload?name=forpixel.txt&to=tabB&from=pc&k={key}", b"x", host=LAN)
    files = json.loads(call("GET", f"/api/state?k={key}&client_id=tabA&name=Pixel", host=LAN)[2])["files"]
    check("a private file reaches either page", "forpixel.txt" in [f["name"] for f in files], files)

    # "Send to" must cover text and files alike: iPhone gets it, iPad does not.
    state_of = lambda who: json.loads(call("GET", f"/api/state?k={key}&client_id={who}&name={who}", host=LAN)[2])
    state_of("iphone1"), state_of("ipad1")
    call("POST", "/api/text", {"text": "only iphone", "to": "iphone1", "from": "hostpage"})
    call("POST", "/api/upload?name=only-iphone.txt&to=iphone1&from=hostpage", b"x")
    got = lambda who: ([t["text"] for t in state_of(who)["texts"]], [f["name"] for f in state_of(who)["files"]])
    texts, files = got("iphone1")
    check("chosen device gets the text and file", "only iphone" in texts and "only-iphone.txt" in files, (texts, files))
    texts, files = got("ipad1")
    check("other device gets neither", "only iphone" not in texts and "only-iphone.txt" not in files, (texts, files))
    check("host sees what it sent", "only iphone" in [t["text"] for t in json.loads(call("GET", "/api/state")[2])["texts"]])
    call("POST", f"/api/text?k={key}", {"text": "to the host", "to": "host", "from": "iphone1"}, host=LAN)
    check("phone to host: other phones do not see it", "to the host" not in got("ipad1")[0] and "to the host" in got("iphone1")[0])

    # Several chosen devices: both get it, a third does not.
    state_of("macA"), state_of("macB"), state_of("macC")
    call("POST", "/api/text", {"text": "for two", "to": "macA,macB", "from": "hostpage"})
    call("POST", "/api/upload?name=for-two.txt&to=macA%2CmacB&from=hostpage", b"x")
    for who, expected in [("macA", True), ("macB", True), ("macC", False)]:
        texts, files = got(who)
        check(f"{who} {'gets' if expected else 'does not get'} the two-device send",
              ("for two" in texts) == expected and ("for-two.txt" in files) == expected, (texts, files))

    # A sender that disconnects halfway must leave nothing behind.
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    connection.putrequest("POST", "/api/upload?name=half.bin")
    connection.putheader("Content-Length", str(10 * 1024 * 1024))
    connection.endheaders()
    connection.send(b"x" * 1024 * 1024)
    connection.close()
    time.sleep(1)
    leftovers = [p.name for p in (data / "uploads").iterdir() if p.name == "half.bin" or p.name.startswith(".dropair-")]
    check("aborted upload leaves nothing", not leftovers, leftovers)

    status, _, body = call("POST", f"/api/text?k={key}", {"text": "line one\nline two"}, host=LAN)
    item = json.loads(body)["item"]
    check("share text", status == 200 and item["text"] == "line one\nline two", body)
    status, _, _ = call("DELETE", f"/api/text/{item['id']}?k={key}", host=LAN)
    check("delete text", status == 200, status)

    status, _, body = call("POST", "/api/admin/save/" + quote(name))
    check("host saves to Downloads", status == 200 and (downloads / name).read_bytes() == payload, body)

    status, _, body = call("POST", "/api/settings", {"max_upload_gb": 0.01, "access_code": "pä ss", "device_name": "Test PC"})
    check("save settings", status == 200 and json.loads(body)["settings"]["device_name"] == "Test PC", body)
    status, _, body = call("GET", f"/api/state?k={key}", host=LAN)
    check("access code now required", status == 404 and json.loads(body)["error"] == "code not found", body)
    status, _, _ = call("GET", "/api/state", headers={"X-Drop-Air-Key": key, "X-Drop-Air-Code": quote("pä ss", safe="")}, host=LAN)
    check("access code accepted from header", status == 200, status)
    try:
        status, _, _ = call("POST", f"/api/upload?name=big.bin&k={key}&code={quote('pä ss')}", b"x" * (11 * 1024 * 1024), host=LAN)
    except OSError:
        status = 413  # server closed the socket while we were still sending
    check("upload limit enforced", status == 413, status)

    # Device details and removing a device.
    key = json.loads(call("GET", "/api/state")[2])["session_key"]
    code = quote("pä ss")
    info = "client_id=kickme&name=iPhone&os=iOS+18.1&browser=Safari&screen=390+x+844"
    call("GET", f"/api/state?k={key}&code={code}&{info}", host=LAN)
    device = next(c for c in json.loads(call("GET", "/api/state")[2])["clients"] if c["id"] == "kickme")
    check("host sees device details", device["os"] == "iOS 18.1" and device["browser"] == "Safari" and device["ip"] == LAN, device)
    others = json.loads(call("GET", f"/api/state?k={key}&code={code}&client_id=other&name=x", host=LAN)[2])["clients"]
    check("other devices see no addresses", all("ip" not in c and "screen" not in c for c in others), others)
    check("phones cannot remove devices", call("POST", f"/api/admin/kick/kickme?k={key}&code={code}", host=LAN)[0] == 403)
    page = call("GET", f"/?k={key}&code={code}", host=LAN)
    cookie = page[1].get("set-cookie", "").split(";")[0]
    check("host removes the device", call("POST", "/api/admin/kick/kickme")[0] == 200)
    status, _, body = call("GET", f"/api/state?k={key}&code={code}&{info}", host=LAN)
    check("removed device is refused", status == 403 and json.loads(body)["error"] == "removed by host", body)
    check("removed device cannot download", call("GET", f"/files/{quote(name)}?k={key}&code={code}", host=LAN)[0] == 403)
    page = call("GET", f"/?k={key}", headers={"Cookie": cookie}, host=LAN)
    check("removed device gets no new cookie", "set-cookie" not in page[1], page[1])
    after = json.loads(call("GET", "/api/state")[2])
    check("host still works and key changed", after["admin"] and after["session_key"] != key)
    check("host lists the removed device", [b["ip"] for b in after["blocked"]] == [LAN], after["blocked"])
    check("host allows it again", call("POST", "/api/admin/unblock", {"ip": LAN})[0] == 200)
    check("allowed device needs the new link", call("GET", f"/api/state?k={after['session_key']}&code={code}", host=LAN)[0] == 200)
    check("old cookie no longer works", call("GET", f"/api/state?code={code}", headers={"Cookie": cookie}, host=LAN)[0] == 404)

    status, _, body = call("GET", "/api/admin/update/status")
    check("update status", status == 200 and json.loads(body)["running"] is False, body)
    status, _, _ = call("DELETE", f"/api/files/{quote(name)}")
    check("delete file", status == 200 and not (data / "uploads" / name).exists(), status)
    status, _, _ = call("POST", "/api/admin/cleanup", {"target": "all"})
    check("clear everything", status == 200 and not list((data / "uploads").iterdir()), status)
    status, _, _ = call("POST", "/api/admin/quit")
    check("quit accepted", status == 200)
    try:
        server.wait(timeout=5)
        check("server stops after quit", True)
    except subprocess.TimeoutExpired:
        check("server stops after quit", False)
finally:
    if server.poll() is None:
        server.kill()

# The QR code's key must rotate, and the old one must stop working after the grace time.
quick = {**env, "DROP_AIR_DATA_DIR": str(work / "rotate"), "DROP_AIR_SESSION_TTL_SECONDS": "3", "DROP_AIR_SESSION_GRACE_SECONDS": "2"}
run = subprocess.Popen(command, env=quick, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
try:
    for _ in range(100):
        try:
            first = json.loads(call("GET", "/api/state")[2])
            break
        except OSError:
            time.sleep(0.1)
    time.sleep(3.5)
    second = json.loads(call("GET", "/api/state")[2])
    check("link key rotates", first["session_key"] != second["session_key"])
    check("QR link carries the new key", second["session_key"] in second["public_url"], second["public_url"])
    check("old key works during the grace time", call("GET", f"/api/state?k={first['session_key']}", host=LAN)[0] == 200)
    time.sleep(3)
    check("old key is refused afterwards", call("GET", f"/api/state?k={first['session_key']}", host=LAN)[0] == 404)
    idle = json.loads(call("GET", "/api/state")[2])["session_key"]
    time.sleep(6)  # nobody asks while the key expires, as in the tray
    check("a long-expired key gets no grace", call("GET", f"/api/state?k={idle}", host=LAN)[0] == 404)
finally:
    run.kill()
    run.wait()

# Quitting and starting again must end every old link and old cookie.
again = {**env, "DROP_AIR_DATA_DIR": str(work / "restart")}
keys, cookie = [], ""
for attempt in (1, 2):
    run = subprocess.Popen(command, env=again, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                keys.append(json.loads(call("GET", "/api/state")[2])["session_key"])
                break
            except OSError:
                time.sleep(0.1)
        if attempt == 1:
            cookie = call("GET", f"/?k={keys[0]}", host=LAN)[1].get("set-cookie", "").split(";")[0]
            check("cookie works while running", call("GET", "/api/state", headers={"Cookie": cookie}, host=LAN)[0] == 200)
        else:
            check("new key after restart", keys[0] != keys[1], keys)
            check("old link refused after restart", call("GET", f"/api/state?k={keys[0]}", host=LAN)[0] == 404)
            check("old cookie refused after restart", call("GET", "/api/state", headers={"Cookie": cookie}, host=LAN)[0] == 404)
    finally:
        run.kill()
        run.wait()

# Without PORT, each data folder gets its own random port and keeps it.
chosen = []
for _ in range(2):
    run = subprocess.Popen(command, env={k: v for k, v in env.items() if k != "PORT"}, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    (data / "port").unlink(missing_ok=True)
    for _ in range(100):
        if (data / "port").is_file() and (data / "port").read_text().strip():
            break
        time.sleep(0.1)
    chosen.append(int((data / "port").read_text()))
    run.kill()
    run.wait()
check("random port in the Drop Air range", 20_000 <= chosen[0] < 49_000, chosen)
check("same port after restart", chosen[0] == chosen[1], chosen)

print(f"\n{edition}: {'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
sys.exit(1 if failures else 0)
