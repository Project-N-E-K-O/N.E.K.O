#!/usr/bin/env bash
# tests/testbench/smoke/_run_all.sh
#
# POSIX companion to _run_all.cmd. Same contract: hand off args to _run_all.py.
#
# Usage (from anywhere):
#     ./tests/testbench/smoke/_run_all.sh              # all smokes
#     ./tests/testbench/smoke/_run_all.sh p25_*        # subset
#     ./tests/testbench/smoke/_run_all.sh --list
#     ./tests/testbench/smoke/_run_all.sh --fail-fast

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/../../.." && pwd )"

VENV_PY="$PROJECT_ROOT/.venv/bin/python"
if [[ -x "$VENV_PY" ]]; then
    PY="$VENV_PY"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    PY="python"
fi

exec "$PY" "$SCRIPT_DIR/_run_all.py" "$@"
