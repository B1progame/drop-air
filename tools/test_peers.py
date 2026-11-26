"""Two hosts on one PC: discover, pair and send from one edition to the other.

    python tools/test_peers.py path/to/rust/DropAir.exe path/to/python.exe
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
rust, python = sys.argv[1], sys.argv[2]
work = Path(tempfile.mkdtemp(prefix="dropair-peers-"))


def start(command, name, port):
    env = {**os.environ, "DROP_AIR_DATA_DIR": str(work / name), "PORT": str(port)}
    return subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def call(url, body=None, raw=None):
    headers = {"Content-Type": "application/json" if body is not None else "application/octet-stream"}
    data = json.dumps(body).encode() if body is not None else raw
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=10) as response:
        return json.load(response)


def wait_for(probe, seconds=20):
    for _ in range(seconds * 4):
        try:
            if value := probe():
                return value
        except OSError:
            pass
        time.sleep(0.25)
    raise AssertionError("timed out")


def run(sender, sender_port, receiver, receiver_port):
    a, b = f"http://127.0.0.1:{sender_port}", f"http://127.0.0.1:{receiver_port}"
    wait_for(lambda: call(a + "/api/state"))
    wait_for(lambda: call(b + "/api/state"))
    name = f"{receiver} test host"
    call(b + "/api/settings", {"discoverable": True, "device_name": name})
    me = call(a + "/api/state")
    peer = wait_for(lambda: next((p for p in call(a + "/api/state")["peers"] if p["device_name"] == name), None))
    print(f"ok   {sender} discovered {receiver}")
    base = f"{a}/api/discovery/{peer['device_id']}/pair"
    request = call(base, {"device_id": me["device_id"], "device_name": f"{sender} sender"})["request"]
    pending = wait_for(lambda: call(b + "/api/state")["requests"])
    call(f"{b}/api/admin/pair/{pending[0]['id']}", {"decision": "accept"})
    result = call(f"{base}/{request['id']}")
    assert result["decision"] == "accepted", result
    print(f"ok   {receiver} approved the request")
    share = urllib.parse.urlsplit(result["share_url"])
    key = urllib.parse.parse_qs(share.query)["k"][0]
    sent = call(f"{share.scheme}://{share.netloc}/api/upload?name=peer.txt&k={key}", raw=b"over the network")
    assert (work / receiver / "uploads" / sent["filename"]).read_bytes() == b"over the network"
    print(f"ok   file arrived on {receiver}")


import urllib.parse  # noqa: E402

servers = [start([rust, "--headless"], "rust", 8781), start([python, str(ROOT / "app.py")], "python", 8782)]
try:
    run("rust", 8781, "python", 8782)
    run("python", 8782, "rust", 8781)
    print("\npeers: ALL PASSED")
finally:
    for server in servers:
        server.kill()
