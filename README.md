# Drop Air

Fast local file and text sharing between a Windows PC and any phone, tablet or computer on the same Wi-Fi. No cloud, no account.

> **The Python edition is no longer updated.** 1.7.0 is its final release.
> New features and fixes only go into the **Rust edition (2.x)**.
> To switch: open Drop Air → **Switch to Rust**, or run the latest `DropAir-Setup-2.x.exe` from [Releases](https://github.com/B1progame/drop-air/releases). Your settings stay.

## Download

| Edition | Installer | Installed size | Updates |
|---|---|---|---|
| **Rust 2.x** (recommended) | `DropAir-Setup-2.x.exe` | about 9 MB | yes |
| Python 1.7.0 (final) | `DropAir-Setup-1.7.0.exe` | about 36 MB | none |

Both need Windows 10 or 11 (64-bit) with Microsoft Edge WebView2, which Windows 11 includes. Older releases were about 450 MB.

## What it does

- Drop, pick or paste files and text; everyone connected can get them
- Drop files onto a device in the **Nearby** area to send only to it
- Scan the QR code to connect a phone; no app needed on the phone
- Find other Drop Air PCs on your Wi-Fi and send to them after they approve
- Live speed and time left, resumable downloads, up to 4 uploads at once
- Dark and light theme, optional access code, automatic deletion of old files
- Closing the window keeps sharing from the tray and frees the window's memory; it exits after 10 minutes if no device is connected

Each PC picks its own random port (20000–48999) on first start and keeps it. Set the `PORT` environment variable to choose one yourself. If Windows Firewall asks, allow Drop Air on **private** networks.

## Security

Drop Air is meant for networks you trust: traffic is plain HTTP. Links carry a rotating session key, and an access code can be added in Settings. Host controls only work from this PC. A "private send" is hidden from other devices' lists but is not encrypted.

Shared files and texts are deleted when Drop Air quits, and by default after 10 minutes. Data lives in `%LOCALAPPDATA%\DropAir` (or `data\` next to the program in the portable ZIP).

## Build from source

```powershell
# Rust edition (needs Rust and Inno Setup)
.\build.ps1 -Edition rust

# Python edition
python -m venv .venv
.\build.ps1 -Edition python
```

Run without building: `cargo run --release` in `rust\`, or `python desktop_app.py` after `pip install -r requirements.txt`.

```
web\          the interface, shared by both editions
rust\         Rust server, tray app and window
app.py        Python server (same HTTP API)
desktop_app.py  Python tray app and window
tools\test_api.py, tools\test_peers.py   end-to-end tests
```

Environment variables: `PORT`, `DROP_AIR_DATA_DIR`, `DROP_AIR_CODE`, `DROP_AIR_AUTO_CLEANUP_MINUTES`, `DROP_AIR_AUTO_CLEANUP_DAYS`, `DROP_AIR_AUTO_CLEANUP_MAX_FILES`, `DROP_AIR_MAX_UPLOAD_GB`, `DROP_AIR_TEXT_TTL_MINUTES`.

## Changes in 2.0.0 / 1.7.0

- New interface with a start-up animation, nearby-device view, redesigned settings and phone layout
- Files stream straight to disk
- Much smaller install: the bundled Qt browser engine is gone
- Random per-PC port instead of 8000
- Fixed: other websites open on the host PC could call Drop Air's host controls
- Removed: Bluetooth hint (Rust edition), the "classic" theme, and the portable choice inside the installer (use the portable ZIP)
