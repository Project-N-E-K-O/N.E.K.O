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

    # P24 hotfix #105 (2026-04-21): install the stdout/stderr tee BEFORE
    # any print so the banner + uvicorn startup + access logs + library
    # logs all land in ``DATA_DIR/live_runtime/current.log``. Rotate
    # first so the previous boot's capture moves aside (current.log →
    # previous.log, old previous.log removed — matches user requirement:
    # "每次开启服务自检的时候可以清理上一次留下的实时转存日志").
    # Background: §4.27 #105 — we had two browser-freeze-then-hard-
    # power-off incidents where the only evidence (uvicorn's '200 OK'
    # flood) went nowhere durable. Now it does.
    from tests.testbench.pipeline import live_runtime_log

    boot_rotate_stats = live_runtime_log.rotate_for_boot()
    live_runtime_log.install()

    print("=" * 66)
    print(" N.E.K.O. Testbench")
    print(f"  URL       : http://{args.host}:{args.port}")
    print(f"  Code dir  : {tb_config.CODE_DIR}")
    print(f"  Data dir  : {tb_config.DATA_DIR}")
    print(f"  Logs dir  : {tb_config.LOGS_DIR}")
    print(f"  Saved     : {tb_config.SAVED_SESSIONS_DIR}")
    print(f"  Live log  : {live_runtime_log.CURRENT_FILE}")
    if boot_rotate_stats.get("rotated"):
        print(
            f"              (rotated previous boot's {boot_rotate_stats['current_bytes']} bytes "
            f"→ previous.log; dropped older previous.log "
            f"{boot_rotate_stats['previous_bytes']} bytes)"
        )
    print("=" * 66)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "[WARN] Binding to non-loopback host. The testbench has no auth; "
            "do not expose it on untrusted networks.",
        )
        print("=" * 66)
        # Also surface to in-app diagnostics so the event is visible on
        # the Diagnostics → Errors subpage (not just stderr). P24 §4.3 I.
        try:
            from tests.testbench.pipeline import diagnostics_store
            from tests.testbench.pipeline.diagnostics_ops import DiagnosticsOp
            diagnostics_store.record_internal(
                DiagnosticsOp.INSECURE_HOST_BINDING,
                f"服务器绑定到非 loopback 主机 (--host={args.host}). testbench 无鉴权, "
                f"局域网任何人都可以访问. 仅在可信网络使用.",
                level="warning",
                detail={"host": args.host, "port": args.port},
            )
        except Exception:  # noqa: BLE001 — never block startup on audit
            pass

    uvicorn.run(
        "tests.testbench.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
