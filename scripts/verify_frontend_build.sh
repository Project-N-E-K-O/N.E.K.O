#!/usr/bin/env bash
# Verify frontend build outputs that are required by packaged desktop builds.
set -euo pipefail

REQUIRED_MODEL_FILE="static/yui-origin/yui-origin.model3.json"

if [ ! -f "$REQUIRED_MODEL_FILE" ]; then
  echo "ERROR: missing $REQUIRED_MODEL_FILE after build_frontend.sh" >&2
  exit 1
fi
