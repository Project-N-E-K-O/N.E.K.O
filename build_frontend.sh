#!/usr/bin/env bash
# Build frontend projects shipped with production distributions.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v npm &> /dev/null; then
  echo "[build_frontend] npm not found, please install Node.js" >&2
  exit 1
fi

if ! command -v uv &> /dev/null; then
  echo "[build_frontend] uv not found, please install uv" >&2
  exit 1
fi

# --- 0. Built-in PNGTuber models ---
uv run --no-sync python "$SCRIPT_DIR/scripts/unpack_builtin_pngtuber.py"

# --- 1. Built-in Live2D models (unpack from assets/) ---
# 每个模型都是 assets/<name>.tar.gz，解到 static/<name>/，marker 是 <name>.moc3。
unpack_live2d_model() {
  local model="$1"
  local archive="$SCRIPT_DIR/assets/$model.tar.gz"
  local dir="$SCRIPT_DIR/static/$model"
  local marker="$dir/$model.moc3"

  if [ ! -f "$archive" ]; then
    echo "[build_frontend] $model archive missing: $archive" >&2
    exit 1
  fi

  if [ ! -f "$marker" ] || [ "$archive" -nt "$marker" ]; then
    echo "[build_frontend] unpacking $model..."
    rm -rf "$dir"
    tar -xzmf "$archive" -C "$SCRIPT_DIR/static"
    if [ ! -f "$marker" ]; then
      echo "[build_frontend] $model marker missing after unpack: $marker" >&2
      exit 1
    fi
    echo "[build_frontend] $model done: $dir"
  else
    echo "[build_frontend] $model up to date, skip"
  fi
}

unpack_live2d_model yui-origin
unpack_live2d_model yui-lolita

# --- 2. Plugin Manager (Vue) ---
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

# --- 3. React Neko Chat ---
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
echo "[build_frontend] all production frontend projects built successfully."
