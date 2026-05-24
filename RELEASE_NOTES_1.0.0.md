# Drop Air 1.0.0

Drop Air is a fast local file and text sharing tool for Windows and iPhone/iPad on the same Wi-Fi network.

## Highlights

- Browser-based local transfer with no cloud step
- QR code connect flow for fast phone and tablet pairing
- Text Relay for copying text between devices without creating a file
- Host-only admin panel with cleanup tools, runtime settings, status, and quit control
- Per-launch session key in shared links and QR codes
- Light, dark, and system themes
- Built-in GitHub release updater in the host admin panel

## Included in this release

- Windows standalone `DropAir.exe`
- Optional Windows installer build
- Release-ready updater flow for future versions

## Notes

- Files and shared text are meant for local network handoff, not long-term storage
- Shared text keeps line breaks and expires after 10 minutes by default
- The updater expects the standalone `DropAir.exe` asset to stay attached to future GitHub releases

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Or run the packaged `DropAir.exe` build from the release assets.

