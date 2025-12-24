#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
FRONTEND_DIR="$PROJECT_ROOT/frontend/vue-project"
DIST_DIR="$FRONTEND_DIR/dist"
EXPORT_DIR="$PROJECT_ROOT/frontend/exported"

if [ ! -d "$FRONTEND_DIR" ]; then
  echo "[export_frontend] frontend dir not found: $FRONTEND_DIR" >&2
  exit 1
fi

echo "[export_frontend] building frontend in: $FRONTEND_DIR"
(
  cd "$FRONTEND_DIR"
  npm run build-only
)

if [ ! -d "$DIST_DIR" ]; then
  echo "[export_frontend] build output not found: $DIST_DIR" >&2
  exit 1
fi

echo "[export_frontend] exporting dist -> $EXPORT_DIR"
rm -rf "$EXPORT_DIR"
mkdir -p "$EXPORT_DIR"

# Copy dist contents into exported/
cp -R "$DIST_DIR"/. "$EXPORT_DIR"/

echo "[export_frontend] done. exported at: $EXPORT_DIR"
