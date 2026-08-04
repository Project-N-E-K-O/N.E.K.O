# -*- coding: utf-8 -*-
"""Stop helper processes started alongside the desktop shell.

Kills:
  - python/pythonw running scripts/edge_tts_bridge.py
  - cmd windows titled \"Neko Edge TTS\" (legacy start_desktop.bat)
  - leftover launcher cmd windows for this repo's start_desktop*.bat

Safe to call on app quit; never touches D:\\RVC.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _taskkill_pid(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/F", "/T"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _taskkill_window_title(title_glob: str) -> None:
    subprocess.run(
        ["taskkill", "/F", "/T", "/FI", f"WINDOWTITLE eq {title_glob}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _pids_matching(needles: tuple[str, ...]) -> list[int]:
    """Find PIDs whose CommandLine contains any needle (case-insensitive)."""
    if sys.platform != "win32":
        return []
    # Escape for PowerShell single-quoted -like patterns
    patterns = [n.replace("'", "''") for n in needles]
    # Build: ($_.CommandLine -like '*a*') -or ...
    clauses = " -or ".join(
        f"($_.CommandLine -like '*{p}*')" for p in patterns
    )
    ps = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -and ({clauses}) }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            text=True,
            errors="replace",
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except Exception:
        return []
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def stop_helpers(
    *,
    repo: Path | None = None,
    verbose: bool = True,
    kill_launchers: bool = False,
) -> int:
    if sys.platform != "win32":
        if verbose:
            print("stop_desktop_helpers: non-Windows, skip")
        return 0

    repo = (repo or _repo_root()).resolve()
    killed: set[int] = set()

    # 1) Legacy visible Edge TTS console
    _taskkill_window_title("Neko Edge TTS*")
    _taskkill_window_title("Neko Edge TTS")

    # 2) PID file written by edge_tts_bridge.py
    pid_file = repo / "logs" / "edge_tts_bridge.pid"
    if pid_file.is_file():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip().splitlines()[0])
            if pid not in killed:
                _taskkill_pid(pid)
                killed.add(pid)
                if verbose:
                    print(f"  killed pid-file pid={pid}")
        except Exception:
            pass
        try:
            pid_file.unlink()
        except OSError:
            pass

    # 3) Command-line match for Edge TTS.
    # Skip start_desktop*.bat unless kill_launchers (Electron quit only).
    # clear_stale runs inside that cmd on launch — must not kill it then.
    needles: list[str] = ["edge_tts_bridge.py"]
    if kill_launchers:
        repo_s = str(repo)
        needles.extend(
            [
                f"{repo_s}\\start_desktop_silent.bat",
                f"{repo_s}\\start_desktop.bat",
                f"{repo_s}/start_desktop_silent.bat",
                f"{repo_s}/start_desktop.bat",
            ]
        )
    for pid in _pids_matching(tuple(needles)):
        if pid in killed or pid == os.getpid():
            continue
        _taskkill_pid(pid)
        killed.add(pid)
        if verbose:
            print(f"  killed helper pid={pid}")

    if verbose:
        print(f"stop_desktop_helpers done (killed={len(killed)})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop N.E.K.O desktop helper processes")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--kill-launchers",
        action="store_true",
        help="also kill leftover start_desktop*.bat cmd windows (use on app quit)",
    )
    args = parser.parse_args()
    return stop_helpers(verbose=not args.quiet, kill_launchers=args.kill_launchers)


if __name__ == "__main__":
    raise SystemExit(main())
