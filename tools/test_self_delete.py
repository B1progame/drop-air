"""Drop Air installed inside its own data\\rollback folder must not delete itself.

    python tools/test_self_delete.py

Needs the builds in dist\\ (run build.ps1 first). The 1.6.x updater can install
into that folder, because some old installs ran from their rollback copy.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
failures = 0


def files(folder: Path) -> set:
    return {p.relative_to(folder) for p in folder.rglob("*") if p.is_file()}


for name, build, arguments in [("python", ROOT / "dist/python/DropAir", []), ("rust", ROOT / "dist/rust", ["--headless"])]:
    data = Path(tempfile.mkdtemp(prefix=f"dropair-self-{name}-"))
    app = data / "rollback"
    shutil.copytree(build, app)
    # Sorts before DropAir.exe, so a delete that stops at the locked program still shows.
    (app / "0-canary.dat").write_text("x")
    before = files(app)
    env = {**os.environ, "DROP_AIR_DATA_DIR": str(data), "PORT": "23400"}
    process = subprocess.Popen([str(app / "DropAir.exe"), *arguments], env=env)
    time.sleep(10)
    alive = process.poll() is None
    missing = before - files(app)
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
    ok = alive and not missing
    failures += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {len(missing)} of {len(before)} program files deleted, running={alive}")
    time.sleep(2)

sys.exit(1 if failures else 0)
