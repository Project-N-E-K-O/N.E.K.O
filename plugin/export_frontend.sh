#!/usr/bin/env bash
# Build plugin manager UI into plugin/frontend/vue-project/dist (single artifact path).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/frontend/vue-project"
DIST_DIR="$FRONTEND_DIR/dist"

if [ ! -d "$FRONTEND_DIR" ]; then
  echo "[export_frontend] frontend dir not found: $FRONTEND_DIR" >&2
  exit 1
fi

if ! command -v npm &> /dev/null; then
  echo "[export_frontend] npm not found, please install Node.js" >&2
  exit 1
fi

echo "[export_frontend] building in: $FRONTEND_DIR"
(
  cd "$FRONTEND_DIR"
  npm run build-only
)

if [ ! -f "$DIST_DIR/index.html" ]; then
  echo "[export_frontend] build output missing: $DIST_DIR/index.html" >&2
  exit 1
fi

echo "[export_frontend] done: $DIST_DIR"
