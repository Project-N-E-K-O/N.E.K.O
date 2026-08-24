"""Alias entry for docs/PLAN: delegates to plugin package shell_main."""
from __future__ import annotations

import runpy
from pathlib import Path

_SHELL = Path(__file__).resolve().parents[1] / "plugin" / "testbench" / "shell_main.py"

if __name__ == "__main__":
    runpy.run_path(str(_SHELL), run_name="__main__")
