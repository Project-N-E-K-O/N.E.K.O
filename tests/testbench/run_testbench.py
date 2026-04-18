"""CLI entry point for the testbench server.

Usage:
    uv run python tests/testbench/run_testbench.py [--port 48920] [--host 127.0.0.1]

The server binds to ``127.0.0.1`` by default to avoid exposing the
testbench on the local network. Use ``--host 0.0.0.0`` at your own risk.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is on ``sys.path`` so ``tests.testbench.*`` imports work
# when this script is launched directly (``uv run python <file>``).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Python auto-injects the script's directory as ``sys.path[0]`` at launch.
# For us that directory is ``tests/testbench``, which contains a local
# ``config.py`` that would **shadow** the top-level ``config`` package
# (e.g. ``from config import APP_NAME`` in ``utils/config_manager``). Drop
# it so absolute imports resolve unambiguously against project root.
sys.path[:] = [p for p in sys.path if Path(p).resolve() != _SCRIPT_DIR]


def _parse_args() -> argparse.Namespace:
    from tests.testbench import config as tb_config

    parser = argparse.ArgumentParser(
        description="N.E.K.O. Testbench web UI server.",
    )
    parser.add_argument(
        "--host",
        default=tb_config.DEFAULT_HOST,
        help=(
            f"Host to bind. Default: {tb_config.DEFAULT_HOST} (loopback only). "
            "Pass 0.0.0.0 to expose on the local network (not recommended)."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=tb_config.DEFAULT_PORT,
        help=f"Port to listen on. Default: {tb_config.DEFAULT_PORT}",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload (dev only).",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        help="uvicorn log level.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Import after sys.path adjustment.
    import uvicorn

    from tests.testbench import config as tb_config

    tb_config.ensure_code_support_dirs()
    tb_config.ensure_data_dirs()

    print("=" * 66)
    print(" N.E.K.O. Testbench")
    print(f"  URL       : http://{args.host}:{args.port}")
    print(f"  Code dir  : {tb_config.CODE_DIR}")
    print(f"  Data dir  : {tb_config.DATA_DIR}")
    print(f"  Logs dir  : {tb_config.LOGS_DIR}")
    print(f"  Saved     : {tb_config.SAVED_SESSIONS_DIR}")
    print("=" * 66)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "[WARN] Binding to non-loopback host. The testbench has no auth; "
            "do not expose it on untrusted networks.",
        )
        print("=" * 66)

    uvicorn.run(
        "tests.testbench.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
