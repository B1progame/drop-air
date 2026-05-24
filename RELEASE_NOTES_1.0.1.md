# Drop Air 1.0.1

This release focuses on usability, session safety, updater readiness, and release tooling improvements on top of `1.0.0`.

## Highlights

- Rotating 32-character session keys with QR and link sync
- Larger QR preview modal with close button and keyboard support
- Stronger admin update signals, including the `!` badge on the main Admin button
- Cleaner text relay viewer with one-item navigation and expandable preview
- Better release tooling for GitHub-based updates

## Included in this update

- Live `/api/session` sync for QR refresh
- Animated QR refresh, drag/drop, upload, and theme transitions
- Admin update flow improvements for GitHub Releases
- Release-ready installer workflow with bundled release assets and draft release support
- Test helper for generating large local files

## Notes

- The text relay now shows one shared text item at a time, with left/right navigation and a collapsed 5-line preview
- The admin `Clear Files` action now clears shared text too
- The updater expects the standalone `DropAir.exe` asset to remain attached to future GitHub releases
- The new smoke test script checks the updater endpoints and key GUI hooks

## Upgrade notes

- Keep using the standalone `DropAir.exe` asset for in-app auto-updates
- Rebuild release assets with the updated `build_installer.ps1` flow before publishing future versions

## Thanks

Thanks to everyone stress-testing the UI, updater flow, and admin tools and helping turn the rough edges into something steadier.
