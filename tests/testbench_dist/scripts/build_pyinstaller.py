"""Run PyInstaller for Testbench standalone (one-dir)."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_DIST = Path(__file__).resolve().parents[1]
_PROJECT = _DIST.parents[1]
_SPEC = _DIST / "specs" / "testbench_standalone.spec"
_OUT = _DIST / "output" / "pyinstaller"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    if args.clean and _OUT.exists():
        shutil.rmtree(_OUT)
    _OUT.mkdir(parents=True, exist_ok=True)
    work = _DIST / "staging" / "pyinstaller_work"
    work.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        f"--distpath={_OUT}",
        f"--workpath={work}",
        str(_SPEC),
    ]
    print("[build_pyinstaller]", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(_PROJECT))


if __name__ == "__main__":
    raise SystemExit(main())
