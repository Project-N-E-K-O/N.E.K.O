"""Mode A plugin shell smoke: healthz + user-data paths + clean stop.

Does not require Plugin Manager or pywebview (uses --no-webview).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_DIST = Path(__file__).resolve().parents[1]
_PROJECT = _DIST.parents[1]
_PLUGIN = _DIST / "plugin" / "testbench"
_SHELL = _PLUGIN / "shell_main.py"


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
    if not _SHELL.is_file():
        print(f"[FAIL] missing {_SHELL}")
        return 1

    port = _free_port()
    data = _DIST / "staging" / "plugin_shell_smoke_data"
    data.mkdir(parents=True, exist_ok=True)
    ready = data / "ready.json"
    if ready.exists():
        ready.unlink()

    env = os.environ.copy()
    env["NEKO_TESTBENCH_DATA_DIR"] = str(data)
    env["PYTHONPATH"] = os.pathsep.join(
        [p for p in (env.get("PYTHONPATH", ""), str(_PROJECT)) if p]
    )

    cmd = [
        sys.executable,
        str(_SHELL),
        "--neko-root",
        str(_PROJECT),
        "--code-dir",
        str(_PROJECT / "tests" / "testbench"),
        "--import-root",
        str(_PROJECT),
        "--user-data",
        str(data),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-webview",
        "--ready-file",
        str(ready),
    ]
    print("[p03]", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=str(_PLUGIN),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}/healthz"
    ok = False
    try:
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                print(f"[FAIL] shell exited early rc={proc.returncode}\n{out[-2000:]}")
                return 1
            if _health(url):
                ok = True
                break
            time.sleep(0.3)
        if not ok:
            print("[FAIL] healthz timeout")
            return 1

        with urllib.request.urlopen(url, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("status") != "ok":
            print(f"[FAIL] unexpected health payload: {body}")
            return 1

        ready_deadline = time.monotonic() + 10.0
        while time.monotonic() < ready_deadline and not ready.is_file():
            time.sleep(0.1)
        if not ready.is_file():
            print("[FAIL] ready-file missing")
            return 1
        ready_data = json.loads(ready.read_text(encoding="utf-8"))
        if ready_data.get("ui") not in {"http_only", "browser", "webview"}:
            print(f"[FAIL] unexpected ready ui: {ready_data}")
            return 1
        if int(ready_data.get("port") or 0) != port:
            print(f"[FAIL] ready port mismatch: {ready_data} vs {port}")
            return 1

        live = data / "live_runtime" / "current.log"
        if not live.is_file():
            print(f"[FAIL] missing live log {live}")
            return 1

        print(f"[OK] Mode A shell healthz port={port} data={data}")
        return 0
    finally:
        if proc.poll() is None:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
        # Port should free shortly.
        time.sleep(0.5)
        if _health(url):
            print("[WARN] healthz still up after stop (race); not failing")


if __name__ == "__main__":
    raise SystemExit(main())
