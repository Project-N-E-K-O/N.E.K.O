#!/usr/bin/env bash
# Linux packaging stub — prefer AppImage tooling in CI.
# Expects PyInstaller one-dir at output/pyinstaller/Testbench/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/output/pyinstaller/Testbench"
OUT="${ROOT}/output/installer"
mkdir -p "$OUT"
if [[ ! -d "$SRC" ]]; then
  echo "Missing $SRC — run build_pyinstaller.py first" >&2
  exit 1
fi
ARCHIVE="${OUT}/Testbench-linux-x64.tar.gz"
tar -C "$(dirname "$SRC")" -czf "$ARCHIVE" "$(basename "$SRC")"
echo "Wrote $ARCHIVE (AppImage wrapping can be added in CI)"
