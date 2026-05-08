#!/usr/bin/env bash
# tests/run_testbench.sh — Testbench quick-start for Linux / macOS
#
# Usage:
#   ./tests/run_testbench.sh               # port 48920 (default)
#   ./tests/run_testbench.sh -p 48921      # custom port
#   ./tests/run_testbench.sh -f            # kill any process on target port first
#
# What it does:
#   1. Check if target port is occupied; offer to kill, change, or abort
#   2. Activate .venv/bin/activate
#   3. uv run python tests/testbench/run_testbench.py --port <port>
#
# See P24_BLUEPRINT.md §12.6 / dev_note L24.

set -euo pipefail

PORT=48920
BIND_HOST="127.0.0.1"
FORCE=0

while getopts "p:h:f" opt; do
    case "$opt" in
        p) PORT="$OPTARG" ;;
        h) BIND_HOST="$OPTARG" ;;
        f) FORCE=1 ;;
        *) echo "Usage: $0 [-p PORT] [-h HOST] [-f]" >&2; exit 1 ;;
    esac
done

# Resolve project root (script at <root>/tests/run_testbench.sh)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"
echo "[run_testbench] project root: $PROJECT_ROOT"

# 1. Check port
port_owner() {
    # Try lsof first (macOS / most Linux), fallback to ss (Linux without lsof)
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1
    elif command -v ss >/dev/null 2>&1; then
        ss -tlnp "sport = :$1" 2>/dev/null | awk 'NR>1 {split($6, a, ","); for (i in a) if (a[i] ~ /^pid=/) { gsub("pid=", "", a[i]); print a[i]; exit }}'
    else
        echo ""
    fi
}

pid=$(port_owner "$PORT")
if [[ -n "$pid" ]]; then
    pname=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
    echo ""
    echo "[WARN] Port $PORT already in use:"
    echo "  PID $pid · $pname"
    echo ""
    if [[ "$FORCE" -eq 1 ]]; then
        echo "  -f passed, killing PID $pid..."
        kill -9 "$pid" || true
        sleep 2
        if [[ -n "$(port_owner "$PORT")" ]]; then
            echo "[ERR] Port $PORT still held. Aborting." >&2
            exit 1
        fi
        echo "  Port $PORT released."
    else
        read -rp "  [k]ill it / [c]hange port / [a]bort (k/c/a): " choice
        case "$choice" in
            k|K)
                kill -9 "$pid" || true
                sleep 2
                if [[ -n "$(port_owner "$PORT")" ]]; then
                    echo "[ERR] Port $PORT still held. Aborting." >&2
                    exit 1
                fi
                echo "  Port $PORT released."
                ;;
            c|C)
                read -rp "  New port (1024-65535): " PORT
                if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )); then
                    echo "[ERR] Invalid port: $PORT" >&2
                    exit 1
                fi
                if [[ -n "$(port_owner "$PORT")" ]]; then
                    echo "[ERR] Port $PORT also in use. Aborting." >&2
                    exit 1
                fi
                ;;
            *)
                echo "  Aborting."
                exit 0
                ;;
        esac
    fi
fi

# 2. Activate venv
VENV_ACTIVATE="$PROJECT_ROOT/.venv/bin/activate"
if [[ ! -f "$VENV_ACTIVATE" ]]; then
    echo "[ERR] .venv not found at $VENV_ACTIVATE" >&2
    echo "      Run 'uv venv' first." >&2
    exit 1
fi
# shellcheck disable=SC1090
source "$VENV_ACTIVATE"

# 3. Launch
echo ""
echo "[run_testbench] starting on $BIND_HOST:$PORT ..."
echo ""
uv run python tests/testbench/run_testbench.py --host "$BIND_HOST" --port "$PORT"
