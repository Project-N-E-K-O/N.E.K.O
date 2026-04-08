#!/usr/bin/env bash
# Build all frontend projects.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v npm &> /dev/null; then
  echo "[build_frontend] npm not found, please install Node.js" >&2
  exit 1
fi

# --- 1. Plugin Manager (Vue) ---
PM_DIR="$SCRIPT_DIR/frontend/plugin-manager"
PM_DIST="$PM_DIR/dist"

if [ ! -d "$PM_DIR" ]; then
  echo "[build_frontend] plugin-manager dir not found: $PM_DIR" >&2
  exit 1
fi

echo "[build_frontend] building plugin-manager..."
(
  cd "$PM_DIR"
  npm ci
  npm run build-only
)

if [ ! -f "$PM_DIST/index.html" ]; then
  echo "[build_frontend] plugin-manager build output missing: $PM_DIST/index.html" >&2
  exit 1
fi
echo "[build_frontend] plugin-manager done: $PM_DIST"

# --- 2. React Neko Chat ---
RC_DIR="$SCRIPT_DIR/frontend/react-neko-chat"
RC_DIST="$SCRIPT_DIR/static/react/neko-chat"

if [ ! -d "$RC_DIR" ]; then
  echo "[build_frontend] react-neko-chat dir not found: $RC_DIR" >&2
  exit 1
fi

echo "[build_frontend] building react-neko-chat..."
(
  cd "$RC_DIR"
  npm ci
  npm run build
)

if [ ! -f "$RC_DIST/neko-chat-window.iife.js" ]; then
  echo "[build_frontend] react-neko-chat build output missing: $RC_DIST/neko-chat-window.iife.js" >&2
  exit 1
fi
echo "[build_frontend] react-neko-chat done: $RC_DIST"

echo ""
echo "[build_frontend] all frontend projects built successfully."
