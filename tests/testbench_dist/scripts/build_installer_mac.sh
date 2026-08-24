#!/usr/bin/env bash
# macOS DMG helper (run on macOS after PyInstaller .app exists).
# Requires: brew install create-dmg
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${ROOT}/output/pyinstaller/Testbench.app"
DMG_OUT="${ROOT}/output/installer/Testbench.dmg"
BG="${ROOT}/assets/installer/mac/dmg-background.png"
mkdir -p "$(dirname "$DMG_OUT")"
if [[ ! -d "$APP" ]]; then
  echo "Missing $APP — build macOS .app first" >&2
  exit 1
fi
create-dmg \
  --volname "N.E.K.O. Testbench" \
  --background "$BG" \
  --window-pos 200 120 \
  --window-size 660 400 \
  --icon-size 100 \
  --app-drop-link 480 200 \
  "$DMG_OUT" \
  "$APP"
echo "Wrote $DMG_OUT"
