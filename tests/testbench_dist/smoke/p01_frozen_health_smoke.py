"""Launch frozen Testbench.exe briefly and probe /healthz + path layout."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_DIST = Path(__file__).resolve().parents[1]
_EXE = _DIST / "output" / "pyinstaller" / "Testbench" / "Testbench.exe"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    if not _EXE.is_file():
        print(f"[FAIL] missing {_EXE}")
        return 1
    port = _free_port()
    data = _DIST / "staging" / "smoke_user_data"
    if data.exists():
        import shutil

        shutil.rmtree(data)
    data.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["NEKO_TESTBENCH_DATA_DIR"] = str(data)
    # desktop_main currently auto-picks port when frozen; we need a way to pass --port.
    # Launch with explicit args (desktop_main supports --port).
    proc = subprocess.Popen(
        [str(_EXE), "--port", str(port), "--no-webview"],
        cwd=str(_EXE.parent),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}/healthz"
    ok = False
    last = None
    try:
        for _ in range(80):
            if proc.poll() is not None:
                err = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
                print(f"[FAIL] process exited early rc={proc.returncode}\n{err[-4000:]}")
                return 1
            try:
                with urllib.request.urlopen(url, timeout=1.5) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    print(f"[OK] healthz -> {body}")
                    ok = True
                    break
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(0.5)
        if not ok:
            print(f"[FAIL] healthz not ready: {last}")
            return 1
        # Confirm user data dirs created
        expected = [
            data / "sandboxes",
            data / "logs",
            data / "api_keys.json",
            data / "live_runtime",
        ]
        for path in expected:
            if not path.exists():
                print(f"[FAIL] expected path missing: {path}")
                return 1
            print(f"[OK] path {path.relative_to(data)}")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
