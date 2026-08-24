"""Mode A single-shell: uvicorn thread + optional pywebview (plugin package entry).

Env / CLI supply NEKO root, testbench code dir, user data, and optional vendor path.
Does not talk to plugin ZMQ/IPC.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Testbench plugin shell (Mode A)")
    p.add_argument("--neko-root", type=Path, required=True)
    p.add_argument("--code-dir", type=Path, required=True, help="…/tests/testbench directory")
    p.add_argument("--user-data", type=Path, required=True)
    p.add_argument("--import-root", type=Path, default=None, help="Parent of tests/ for imports")
    p.add_argument("--vendor", type=Path, default=None)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--no-webview", action="store_true")
    p.add_argument("--width", type=int, default=1400)
    p.add_argument("--height", type=int, default=900)
    p.add_argument("--ready-file", type=Path, default=None)
    return p.parse_args()


def _setup_sys_path(*, neko_root: Path, import_root: Path, code_dir: Path, vendor: Path | None) -> None:
    # Order: vendor → import_root (bundled) → neko_root
    for path in (vendor, import_root, neko_root):
        if path is None:
            continue
        value = str(path.resolve())
        if value not in sys.path:
            sys.path.insert(0, value)
    # Drop bare code_dir so local config.py cannot shadow top-level config.
    code_res = code_dir.resolve()
    sys.path[:] = [p for p in sys.path if Path(p).resolve() != code_res]


def _pick_port(preferred: int) -> int:
    if preferred > 0:
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_health(url: str, *, timeout_s: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_s
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if 200 <= int(getattr(resp, "status", 200)) < 500:
                    return
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.25)
    raise RuntimeError(f"healthz not ready at {url}: {last}")


def _write_ready(path: Path | None, *, url: str, port: int, ui: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'{{"url":"{url}","port":{port},"ui":"{ui}","pid":{os.getpid()}}}\n',
        encoding="utf-8",
    )


def main() -> int:
    args = _parse_args()
    neko_root = args.neko_root.resolve()
    code_dir = args.code_dir.resolve()
    user_data = args.user_data.resolve()
    import_root = (args.import_root or code_dir.parent.parent).resolve()
    vendor = args.vendor.resolve() if args.vendor else None

    _setup_sys_path(neko_root=neko_root, import_root=import_root, code_dir=code_dir, vendor=vendor)

    # Local imports after path setup.
    from path_bootstrap import apply_plugin_patches  # type: ignore  # noqa: E402

    apply_plugin_patches(neko_root=neko_root, code_dir=code_dir, user_data_dir=user_data)

    host = args.host
    port = _pick_port(args.port)

    import uvicorn

    from tests.testbench.pipeline import live_runtime_log
    from tests.testbench.server import app

    live_runtime_log.rotate_for_boot()
    live_runtime_log.install()

    config = uvicorn.Config(app, host=host, port=port, log_level="info", reload=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="testbench-uvicorn", daemon=True)
    thread.start()

    base = f"http://{host}:{port}"
    try:
        _wait_health(f"{base}/healthz")
    except Exception as exc:
        print(f"[plugin_shell] FATAL: {exc}", file=sys.stderr)
        server.should_exit = True  # type: ignore[attr-defined]
        return 1

    print(f"[plugin_shell] ready {base}", flush=True)
    # Write ready ASAP so drivers/smokes racing on healthz can observe URL/port.
    _write_ready(args.ready_file, url=base, port=port, ui="http_only")

    if args.no_webview:
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.should_exit = True  # type: ignore[attr-defined]
            try:
                live_runtime_log.close()
            except Exception:  # noqa: BLE001
                pass
            thread.join(timeout=15)
        return 0

    try:
        import webview
    except ImportError:
        print("[plugin_shell] pywebview missing; HTTP only", file=sys.stderr)
        _write_ready(args.ready_file, url=base, port=port, ui="browser")
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.should_exit = True  # type: ignore[attr-defined]
            thread.join(timeout=10)
        return 0

    _write_ready(args.ready_file, url=base, port=port, ui="webview")
    webview.create_window("N.E.K.O. Testbench", url=base, width=args.width, height=args.height)
    try:
        webview.start()
    finally:
        server.should_exit = True  # type: ignore[attr-defined]
        try:
            live_runtime_log.close()
        except Exception:  # noqa: BLE001
            pass
        thread.join(timeout=15)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
