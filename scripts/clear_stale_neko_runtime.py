# -*- coding: utf-8 -*-
"""Clear stale N.E.K.O single-instance locks / orphan launcher processes."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STILL_ACTIVE = 259


def _runtime_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "N.E.K.O.runtime"
    return Path.home() / ".neko_runtime"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    code = wintypes.DWORD()
    ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
    k32.CloseHandle(handle)
    return bool(ok) and int(code.value) == STILL_ACTIVE


def _taskkill(image: str) -> None:
    if sys.platform != "win32":
        return
    subprocess.run(
        ["taskkill", "/IM", image, "/F", "/T"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _kill_launcher_pythons(repo: Path) -> int:
    """Kill python processes whose command line contains this repo's launcher.py."""
    if sys.platform != "win32":
        return 0
    needle = str((repo / "launcher.py").resolve()).lower()
    killed = 0
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine", "/FORMAT:CSV"],
            text=True,
            errors="replace",
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return 0
    for line in out.splitlines():
        low = line.lower()
        if "launcher.py" not in low:
            continue
        if needle not in low and "n.e.k.o" not in low and "neko" not in low:
            continue
        parts = [p.strip() for p in line.split(",")]
        if not parts:
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        killed += 1
        print(f"  killed orphan launcher pid={pid}")
    return killed


def clear_runtime(*, kill_app: bool, kill_orphans: bool, repo: Path) -> int:
    runtime = _runtime_dir()
    lock = runtime / "launcher.lock"
    record = runtime / "launcher.json"

    print(f"runtime dir: {runtime}")
    owner_pid = 0
    if record.is_file():
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
            owner_pid = int((data or {}).get("pid") or 0)
            print(
                f"record: state={data.get('state')} pid={owner_pid} "
                f"alive={_pid_alive(owner_pid)}"
            )
        except Exception as exc:
            print(f"record unreadable: {exc}")
    elif lock.is_file():
        try:
            owner_pid = int(lock.read_text(encoding="utf-8").strip().splitlines()[0])
            print(f"lock pid={owner_pid} alive={_pid_alive(owner_pid)}")
        except Exception:
            pass

    if kill_app:
        print("stopping N.E.K.O.exe ...")
        _taskkill("N.E.K.O.exe")
        time.sleep(0.5)

    if kill_orphans:
        print("stopping orphan launcher.py processes ...")
        _kill_launcher_pythons(repo)
        try:
            helpers = Path(__file__).resolve().with_name("stop_desktop_helpers.py")
            if helpers.is_file():
                import importlib.util

                spec = importlib.util.spec_from_file_location("stop_desktop_helpers", helpers)
                mod = importlib.util.module_from_spec(spec)
                assert spec and spec.loader
                spec.loader.exec_module(mod)
                print("stopping desktop helpers (Edge TTS / leftover cmd) ...")
                mod.stop_helpers(repo=repo, verbose=True)
        except Exception as exc:
            print(f"WARN stop_desktop_helpers: {exc}")

    # After kills, drop stale lock/record if owner is gone.
    owner_alive = _pid_alive(owner_pid) if owner_pid else False
    if owner_alive and not kill_app and not kill_orphans:
        print("LIVE owner still holds the lock; not deleting.")
        print("Re-run with --kill-app to force restart.")
        return 2

    for path in (lock, record):
        try:
            if path.is_file():
                path.unlink()
                print(f"removed {path.name}")
        except OSError as exc:
            print(f"WARN cannot remove {path}: {exc}")
            return 1
    print("stale runtime cleared")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear stale N.E.K.O runtime lock")
    parser.add_argument("--kill-app", action="store_true", help="taskkill N.E.K.O.exe")
    parser.add_argument(
        "--kill-orphans",
        action="store_true",
        help="kill orphan python launcher.py for this repo",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="shortcut for --kill-app --kill-orphans",
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    kill_app = args.kill_app or args.force
    kill_orphans = args.kill_orphans or args.force
    return clear_runtime(kill_app=kill_app, kill_orphans=kill_orphans, repo=repo)


if __name__ == "__main__":
    raise SystemExit(main())
