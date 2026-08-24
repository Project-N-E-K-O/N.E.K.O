"""Validate plugin package contents: snapshot layout, no legacy runtime exe."""
from __future__ import annotations

import zipfile
from pathlib import Path

_DIST = Path(__file__).resolve().parents[1]
_PLUGIN = _DIST / "plugin" / "testbench"
_PKG = _DIST / "output" / "testbench.neko-plugin"
_BUNDLED = _PLUGIN / "bundled" / "tests" / "testbench"


def main() -> int:
    failures: list[str] = []

    if not (_PLUGIN / "shell_main.py").is_file():
        failures.append("missing shell_main.py")
    if not (_PLUGIN / "path_bootstrap.py").is_file():
        failures.append("missing path_bootstrap.py")
    if not (_PLUGIN / "__init__.py").is_file():
        failures.append("missing __init__.py")

    if not _BUNDLED.is_dir():
        failures.append("bundled/tests/testbench missing — run build_plugin.py")
    else:
        if (_BUNDLED / "smoke").is_dir():
            failures.append("bundled snapshot should exclude smoke/")
        if not (_BUNDLED / "server.py").is_file() and not (_BUNDLED / "run_testbench.py").is_file():
            failures.append("bundled testbench missing server/run entry")

    legacy = _PLUGIN / "runtime"
    if legacy.is_dir() and any(legacy.rglob("*.exe")):
        failures.append("legacy runtime/*.exe still present")

    if not _PKG.is_file():
        failures.append(f"missing package {_PKG}")
    else:
        with zipfile.ZipFile(_PKG) as zf:
            names = zf.namelist()
            norm = [n.replace("\\", "/") for n in names]
        driver_files = (
            "shell_main.py",
            "path_bootstrap.py",
            "__init__.py",
        )
        for fname in driver_files:
            hits = [n for n in norm if n.endswith(f"/{fname}") or n == fname]
            if not hits:
                failures.append(f"package missing driver file {fname}")
            elif any("/bundled/" in hit for hit in hits):
                failures.append(f"package driver file {fname} only found under bundled/")
        if any(n.lower().endswith(".exe") and "/runtime/" in n.replace("\\", "/").lower() for n in names):
            failures.append("package still contains runtime exe")
        bundled_hits = [n for n in names if "bundled/tests/testbench/" in n.replace("\\", "/")]
        if not bundled_hits:
            failures.append("package missing bundled/tests/testbench payload")
        # Soft size gate: driver package should stay far below frozen standalone.
        size_mb = _PKG.stat().st_size / (1024 * 1024)
        if size_mb > 80:
            failures.append(f"package unexpectedly large: {size_mb:.1f} MB")

    if failures:
        for item in failures:
            print(f"[FAIL] {item}")
        return 1
    print(f"[OK] plugin package layout clean size={_PKG.stat().st_size}B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
