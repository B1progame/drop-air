# Drop Air

Fast local file sharing from your laptop to iPhone/iPad (and back) using a browser on the same Wi-Fi network.

## Features

- AirDrop-like local transfer (no cloud)
- Drag-and-drop upload from any device
- Download files from any connected device
- Text relay for pasting text on one device and copying it on another
- Host-only admin panel with QR code, share link, cleanup, and runtime settings
- Per-launch connection key in QR/share links so old or manual LAN links cannot join the wrong session
- Shared text keeps line breaks and expires after 10 minutes by default
- Host-only quit button to stop Drop Air from the admin panel
- Light, dark, and system theme modes
- Optional auto-open of the local admin page in the default browser
- Optional one-time passcode (`DROP_AIR_CODE`)

## Requirements

- Python 3.10+
- Same local network (laptop + iPhone/iPad)

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open the shown URL on your iPhone/iPad, or scan the QR code in terminal. The Wi-Fi URL includes a temporary `k=...` connection key generated on each launch.
The host machine also opens the local admin page automatically by default.

Disable browser auto-open:

```powershell
$env:DROP_AIR_OPEN_BROWSER="0"
python app.py
```

## Optional security

Set a code before starting server:

```powershell
$env:DROP_AIR_CODE="123456"
python app.py
```

Then open `http://<server-ip>:8000/?code=123456`.

## Optional auto-cleanup

By default, uploads expire after 10 minutes.

You can enable cleanup with env vars:

```powershell
$env:DROP_AIR_AUTO_CLEANUP_MINUTES="10"
$env:DROP_AIR_AUTO_CLEANUP_DAYS="7"
$env:DROP_AIR_AUTO_CLEANUP_MAX_FILES="300"
python app.py
```

- `DROP_AIR_AUTO_CLEANUP_MINUTES`: delete files older than N minutes (default `10`, `0` disables)
- `DROP_AIR_AUTO_CLEANUP_DAYS`: delete files older than N days (`0` disables)
- `DROP_AIR_AUTO_CLEANUP_MAX_FILES`: keep only newest N files (`0` disables)
- `DROP_AIR_TEXT_TTL_MINUTES`: delete shared text older than N minutes (default `10`, `0` disables)

## Build Windows EXE

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
.\build_exe.ps1
```

Output:

- `dist\DropAir.exe`

## Build Windows Installer EXE (Inno Setup)

1. Install Inno Setup 6 (compiler `ISCC.exe`).
2. Build installer:

```powershell
.\build_installer.ps1
```

Output:

- `dist\DropAirSetup.exe`

You can customize metadata/version:

```powershell
.\build_installer.ps1 -AppVersion "1.0.0" -Publisher "Drop Air"
```

### Icon styles

Create icon before build:

```powershell
.\.venv\Scripts\python.exe tools\make_icon.py --style neon --output assets\icon\drop_air.ico
```

Available styles:

- `neon`
- `minimal`
- `retro`
## Notes

- Files are stored in `uploads/`.
- This is LAN-only by default.
- If Windows Firewall asks, allow for Private networks.

