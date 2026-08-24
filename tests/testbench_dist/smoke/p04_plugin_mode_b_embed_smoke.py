"""Mode B style embed smoke: uvicorn thread in-process + healthz + stop.

Mirrors plugin driver Mode B HTTP path without Plugin Manager / ZMQ.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

_DIST = Path(__file__).resolve().parents[1]
_PROJECT = _DIST.parents[1]
_TESTBENCH = _PROJECT / "tests" / "testbench"
_PLUGIN = _DIST / "plugin" / "testbench"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            return 200 <= int(getattr(resp, "status", 200)) < 500
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    if str(_PROJECT) not in sys.path:
        sys.path.insert(0, str(_PROJECT))
    if str(_PLUGIN) not in sys.path:
        sys.path.insert(0, str(_PLUGIN))
    sys.path[:] = [p for p in sys.path if Path(p).resolve() != _TESTBENCH.resolve()]

    from path_bootstrap import apply_plugin_patches

    data = _DIST / "staging" / "plugin_mode_b_smoke_data"
    if data.exists():
        import shutil

        shutil.rmtree(data)
    apply_plugin_patches(
        neko_root=_PROJECT,
        code_dir=_TESTBENCH,
        user_data_dir=data,
    )

    port = _free_port()
    host = "127.0.0.1"
    url = f"http://{host}:{port}/healthz"

    import uvicorn
    from tests.testbench.pipeline import live_runtime_log
    from tests.testbench.server import app

    live_runtime_log.rotate_for_boot()
    live_runtime_log.install()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", reload=False)
    server = uvicorn.Server(config)

    def _run() -> None:
        try:
            server.run()
        finally:
            try:
                live_runtime_log.close()
            except Exception:  # noqa: BLE001
                pass

    thread = threading.Thread(target=_run, name="p04-embed", daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            if _health(url):
                break
            if not thread.is_alive():
                print("[FAIL] embed thread died")
                return 1
            time.sleep(0.25)
        else:
            print("[FAIL] healthz timeout")
            return 1

        keys = data / "api_keys.json"
        if not keys.is_file():
            print(f"[FAIL] api_keys not created at {keys}")
            return 1
        print(f"[OK] Mode B embed healthz port={port}")
        return 0
    finally:
        server.should_exit = True  # type: ignore[attr-defined]
        thread.join(timeout=15)
        if thread.is_alive():
            print("[FAIL] embed thread still alive after stop")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
