"""Run all automatable testbench_dist / plugin smokes (no Plugin Manager UI)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_DIST = Path(__file__).resolve().parents[1]
_PROJECT = _DIST.parents[1]
_SCRIPTS = _DIST / "scripts"

_STEPS: list[tuple[str, list[str]]] = [
    ("p00_isolation_gate", [sys.executable, str(_DIST / "smoke" / "p00_isolation_gate.py")]),
    ("plugin_unit_smoke", [sys.executable, "-m", "pytest", str(_DIST / "plugin" / "testbench" / "tests" / "test_smoke.py"), "-q"]),
    ("build_plugin", [sys.executable, str(_SCRIPTS / "build_plugin.py")]),
    ("p05_plugin_package", [sys.executable, str(_DIST / "smoke" / "p05_plugin_package_smoke.py")]),
    ("p03_plugin_shell", [sys.executable, str(_DIST / "smoke" / "p03_plugin_shell_smoke.py")]),
    ("p04_plugin_mode_b", [sys.executable, str(_DIST / "smoke" / "p04_plugin_mode_b_embed_smoke.py")]),
    (
        "neko_plugin_check_release",
        ["uv", "run", "neko-plugin", "check", "-r", str(_DIST / "plugin" / "testbench")],
    ),
]


def main() -> int:
    failed = 0
    for name, cmd in _STEPS:
        print("=" * 66)
        print(f"[run] {name}: {' '.join(cmd)}")
        rc = subprocess.call(cmd, cwd=str(_PROJECT))
        if rc != 0:
            print(f"[FAIL] {name} rc={rc}")
            failed += 1
        else:
            print(f"[OK] {name}")
    print("=" * 66)
    if failed:
        print(f"[DONE] failed={failed}")
        return 1
    print("[DONE] all automated plugin/dist smokes passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
