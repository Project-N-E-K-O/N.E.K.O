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


def snapshot_testbench(*, force: bool = True, plugin_dir: Path | None = None) -> Path:
    if not _SRC_TESTBENCH.is_dir():
        raise FileNotFoundError(f"missing source testbench: {_SRC_TESTBENCH}")
    target_plugin = (plugin_dir or _PLUGIN).resolve()
    bundled_root = target_plugin / "bundled"
    bundled_tb = bundled_root / "tests" / "testbench"
    if bundled_root.exists() and force:
        shutil.rmtree(bundled_root)
    bundled_tb.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_SRC_TESTBENCH, bundled_tb, ignore=_ignore)
    init_tests = bundled_root / "tests" / "__init__.py"
    if not init_tests.exists():
        init_tests.write_text("# namespace for bundled tests.testbench\n", encoding="utf-8")
    print(f"[build_plugin] snapshot -> {bundled_tb}")
    return bundled_tb


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-snapshot", action="store_true")
    parser.add_argument(
        "--target-plugin-dir",
        default=None,
        help="Snapshot bundled/ into this plugin directory (for market CI mount path)",
    )
    parser.add_argument(
        "--snapshot-only",
        action="store_true",
        help="Only snapshot bundled sources; do not build .neko-plugin",
    )
    parser.add_argument(
        "-o",
        "--out",
        default=str(_OUT / "testbench.neko-plugin"),
        help="Output .neko-plugin path",
    )
    args = parser.parse_args()

    target_plugin = Path(args.target_plugin_dir).resolve() if args.target_plugin_dir else _PLUGIN

    if not args.skip_snapshot:
        snapshot_testbench(plugin_dir=target_plugin)

    if args.snapshot_only:
        return 0

    # Drop legacy exe runtime tree if present (no longer packaged).
    legacy_runtime = target_plugin / "runtime"
    if legacy_runtime.is_dir():
        print(f"[build_plugin] removing legacy {legacy_runtime}")
        shutil.rmtree(legacy_runtime)

    _OUT.mkdir(parents=True, exist_ok=True)
    out = Path(args.out).resolve()
    cmd = ["uv", "run", "neko-plugin", "build", str(target_plugin), "-o", str(out)]
    print("[build_plugin]", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(_PROJECT))


if __name__ == "__main__":
    raise SystemExit(main())
