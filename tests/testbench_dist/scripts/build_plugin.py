"""Snapshot testbench sources into plugin/bundled and build .neko-plugin.

Does **not** require PyInstaller / runtime/*.exe.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_DIST = Path(__file__).resolve().parents[1]
_PROJECT = _DIST.parents[1]
_PLUGIN = _DIST / "plugin" / "testbench"
_SRC_TESTBENCH = _PROJECT / "tests" / "testbench"
_BUNDLED_ROOT = _PLUGIN / "bundled"
_BUNDLED_TB = _BUNDLED_ROOT / "tests" / "testbench"
_OUT = _DIST / "output"

_SKIP_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    "smoke",
    ".git",
    "node_modules",
    ".mypy_cache",
    ".ruff_cache",
}


def _ignore(directory: str, names: list[str]) -> set[str]:
    skipped: set[str] = set()
    for name in names:
        if name in _SKIP_DIR_NAMES:
            skipped.add(name)
            continue
        if name.endswith((".pyc", ".pyo")):
            skipped.add(name)
    return skipped


def snapshot_testbench(*, force: bool = True) -> Path:
    if not _SRC_TESTBENCH.is_dir():
        raise FileNotFoundError(f"missing source testbench: {_SRC_TESTBENCH}")
    if _BUNDLED_ROOT.exists() and force:
        shutil.rmtree(_BUNDLED_ROOT)
    _BUNDLED_TB.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_SRC_TESTBENCH, _BUNDLED_TB, ignore=_ignore)
    init_tests = _BUNDLED_ROOT / "tests" / "__init__.py"
    if not init_tests.exists():
        init_tests.write_text("# namespace for bundled tests.testbench\n", encoding="utf-8")
    print(f"[build_plugin] snapshot -> {_BUNDLED_TB}")
    return _BUNDLED_TB


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-snapshot", action="store_true")
    parser.add_argument(
        "-o",
        "--out",
        default=str(_OUT / "testbench.neko-plugin"),
        help="Output .neko-plugin path",
    )
    args = parser.parse_args()

    if not args.skip_snapshot:
        snapshot_testbench()

    # Drop legacy exe runtime tree if present (no longer packaged).
    legacy_runtime = _PLUGIN / "runtime"
    if legacy_runtime.is_dir():
        print(f"[build_plugin] removing legacy {legacy_runtime}")
        shutil.rmtree(legacy_runtime)

    _OUT.mkdir(parents=True, exist_ok=True)
    out = Path(args.out).resolve()
    cmd = ["uv", "run", "neko-plugin", "build", str(_PLUGIN), "-o", str(out)]
    print("[build_plugin]", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(_PROJECT))


if __name__ == "__main__":
    raise SystemExit(main())
