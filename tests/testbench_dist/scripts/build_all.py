"""Unified build entry: optional embedding → PyInstaller → plugin package."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_DIST = _SCRIPTS.parent
_PROJECT = _DIST.parents[1]


def _run(script: str, extra: list[str] | None = None) -> int:
    cmd = [sys.executable, str(_SCRIPTS / script), *(extra or [])]
    print("[build_all]", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(_PROJECT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-embedding", action="store_true")
    parser.add_argument("--skip-pyinstaller", action="store_true")
    parser.add_argument("--skip-plugin", action="store_true")
    parser.add_argument("--plugin-only", action="store_true", help="Only build .neko-plugin")
    parser.add_argument("--platform", default="auto", help="Informational label only")
    args = parser.parse_args()

    gate = _DIST / "smoke" / "p00_isolation_gate.py"
    rc = subprocess.call([sys.executable, str(gate)], cwd=str(_PROJECT))
    if rc != 0:
        return rc

    if args.plugin_only:
        return _run("build_plugin.py")

    if not args.skip_embedding:
        rc = _run("prepare_embedding.py", ["--skip-if-present"])
        if rc != 0:
            return rc

    if not args.skip_pyinstaller:
        rc = _run("build_pyinstaller.py")
        if rc != 0:
            return rc

    if not args.skip_plugin:
        rc = _run("build_plugin.py")
        if rc != 0:
            return rc

    print("[build_all] done. Artifacts under", _DIST / "output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
