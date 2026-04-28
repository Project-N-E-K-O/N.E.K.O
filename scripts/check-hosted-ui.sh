#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR/frontend/plugin-manager"
npm run type-check
npm run check-hosted-tsx -- plugin/plugins/mcp_adapter
npm run test:hosted
npm run test:hosted:e2e

cd "$ROOT_DIR"
python -m py_compile plugin/plugins/mcp_adapter/__init__.py
python -m pytest plugin/tests/unit/server/test_plugin_ui_manifest.py plugin/tests/unit/plugins/test_mcp_adapter_tasks.py
