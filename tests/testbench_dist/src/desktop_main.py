"""Desktop entry for standalone Testbench (PyInstaller + pywebview).

Development:
    uv run python tests/testbench_dist/src/desktop_main.py --dev

Frozen:
    Testbench.exe  (no browser; native WebView window)
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

_FROZEN = bool(getattr(sys, "frozen", False) or "__compiled__" in globals())

if not _FROZEN:
    # Ensure project root is on sys.path when launched from source.
    _SRC_DIR = Path(__file__).resolve().parent
    _DIST_ROOT = _SRC_DIR.parent
    _PROJECT_ROOT = _DIST_ROOT.parents[1]
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))

    # Mirror run_testbench.py: drop tests/testbench from path[0] so local
    # config.py cannot shadow the top-level config package. Also drop this
    # src/ directory if Python injected it.
    _TESTBENCH_DIR = _PROJECT_ROOT / "tests" / "testbench"
    sys.path[:] = [
        p
        for p in sys.path
        if Path(p).resolve() not in {_TESTBENCH_DIR.resolve(), _SRC_DIR.resolve()}
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="N.E.K.O. Testbench desktop shell")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Dev mode: use source tree paths; open webview against local server.",
    )
    parser.add_argument(
        "--standalone-paths",
        action="store_true",
        help="Even in source, apply user-data path patches (smoke / dry-run).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 = auto-pick free port")
    parser.add_argument("--console", action="store_true", help="Keep console attached (Windows)")
    parser.add_argument(
        "--no-webview",
        action="store_true",
        help="Skip pywebview window (serve HTTP only; useful for automated smoke).",
    )
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=900)
    return parser.parse_args()


def _pick_port(preferred: int) -> int:
    if preferred > 0:
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_health(url: str, *, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if 200 <= getattr(resp, "status", 200) < 500:
                    return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.25)
    raise RuntimeError(f"Testbench server did not become ready at {url}: {last_err}")


def _prepare_paths(*, frozen_like: bool) -> None:
    from tests.testbench_dist.src import frozen_runtime
    from tests.testbench_dist.src.bootstrap import apply_standalone_patches

    if frozen_like or frozen_runtime.is_frozen():
        apply_standalone_patches(
            bundle_dir=frozen_runtime.bundle_dir(),
            user_data_dir=frozen_runtime.user_data_dir(),
        )
    else:
        from tests.testbench import config as tb_config

        tb_config.ensure_code_support_dirs()
        tb_config.ensure_data_dirs()


def _start_uvicorn(host: str, port: int) -> tuple[threading.Thread, object]:
    import uvicorn

    from tests.testbench.pipeline import live_runtime_log

    live_runtime_log.rotate_for_boot()
    live_runtime_log.install()

    # Import app object after path patches.
    from tests.testbench.server import app

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        reload=False,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, name="testbench-uvicorn", daemon=True)
    thread.start()
    return thread, server


def main() -> int:
    args = _parse_args()
    from tests.testbench_dist.src import frozen_runtime

    frozen_like = frozen_runtime.is_frozen() or args.standalone_paths
    _prepare_paths(frozen_like=frozen_like)

    host = args.host
    if args.port > 0:
        port = args.port
    elif frozen_like:
        port = _pick_port(0)
    else:
        # Prefer stable 48920 in dev; fall back if occupied.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind((host, 48920))
            port = 48920
        except OSError:
            port = _pick_port(0)

    thread, server = _start_uvicorn(host, port)
    base = f"http://{host}:{port}"
    try:
        _wait_health(f"{base}/healthz")
    except Exception as exc:
        print(f"[desktop_main] FATAL: {exc}", file=sys.stderr)
        server.should_exit = True  # type: ignore[attr-defined]
        return 1

    print("=" * 66)
    print(" N.E.K.O. Testbench (desktop)")
    print(f"  URL       : {base}")
    if frozen_like:
        from tests.testbench_dist.src.bootstrap import describe_paths

        for key, value in describe_paths().items():
            print(f"  {key:12}: {value}")
    print("=" * 66)

    if args.no_webview:
        print("[desktop_main] --no-webview: serving until terminated")
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.should_exit = True  # type: ignore[attr-defined]
            try:
                from tests.testbench.pipeline import live_runtime_log

                live_runtime_log.close()
            except Exception:  # noqa: BLE001
                pass
            thread.join(timeout=15)
        return 0

    try:
        import webview
    except ImportError:
        print(
            "[desktop_main] pywebview not installed. "
            "Install with: uv pip install pywebview  "
            f"Or open {base} in a browser.",
            file=sys.stderr,
        )
        # Keep server alive until Ctrl+C in console-only fallback.
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.should_exit = True  # type: ignore[attr-defined]
            thread.join(timeout=10)
        return 0

    window = webview.create_window(
        "N.E.K.O. Testbench",
        url=base,
        width=args.width,
        height=args.height,
    )
    try:
        webview.start()
    finally:
        server.should_exit = True  # type: ignore[attr-defined]
        try:
            from tests.testbench.pipeline import live_runtime_log

            live_runtime_log.close()
        except Exception:  # noqa: BLE001
            pass
        thread.join(timeout=15)
        _ = window
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
