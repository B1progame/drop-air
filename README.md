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
- Optional GitHub Releases updater in the host-only admin panel
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

## Optional updates

Drop Air can check a public GitHub repository release feed from the host admin panel.
If you run from a git checkout, it auto-detects the GitHub `origin` remote. You only need `DROP_AIR_UPDATE_REPO` if you want a different repository:

```powershell
$env:DROP_AIR_UPDATE_REPO="your-github-name/your-repo"
python app.py
```

Create GitHub releases with tags like `1.0.0`, `1.0.1`, etc. The admin panel compares the latest release tag with `VERSION`.
For packaged builds, attach the standalone `DropAir.exe` release asset; the updater skips the Inno installer.

Release flow:

1. Update `VERSION` in the repo.
2. Build the standalone EXE with `.\build_exe.ps1`.
3. Push the commit and create a matching Git tag such as `1.0.1`.
4. Create a GitHub Release from that tag and attach `dist\DropAir.exe`.
5. On the host machine, the admin panel will show a red `!` and a rainbow update button when a newer release exists.

What the update button does:

- Checks the latest GitHub Release tag against local `VERSION`
- Warns before install
- Clears live files and shared text so connections do not survive a version swap
- Downloads the release in the terminal with in-place progress
- Replaces the running app and restarts it
- Skips the Inno installer path when `DropAir.exe` is attached to the release

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

- `dist\DropAir.exe`
- `dist\DropAir-Setup-<version>.exe`
- `dist\release\<version>\` with:
- `DropAir-<version>.exe`
- `DropAir-Setup-<version>.exe`
- `DropAir-<version>-portable.zip`
- `SHA256SUMS.txt`
- `RELEASE-STEPS.txt`
- `publish-github-release.ps1`

You can customize metadata/version:

```powershell
.\build_installer.ps1 -AppVersion "1.0.0" -Publisher "Drop Air"
```

Create a ready-to-publish GitHub draft release in the current repo:

```powershell
.\build_installer.ps1 -CreateDraftRelease
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

