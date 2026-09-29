from __future__ import annotations

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def _nuitka_blocks() -> dict[str, str]:
    desktop = (WORKFLOWS / "build-desktop.yml").read_text(encoding="utf-8")
    unix_block, windows_block = desktop.split("- name: Build with Nuitka (Windows)", maxsplit=1)
    linux = (WORKFLOWS / "build-desktop-linux.yml").read_text(encoding="utf-8")
    return {
        "build-desktop.yml (unix)": unix_block,
        "build-desktop.yml (windows)": windows_block,
        "build-desktop-linux.yml": linux,
    }


@pytest.mark.parametrize("name", list(_nuitka_blocks()))
def test_pyav_is_frozen_from_extension_modules(name: str) -> None:
    # PyAV wheels ship Cython pure-python-mode .py files next to some compiled
    # extension modules. Compiling those sources instead of using the .so/.pyd
    # breaks the frozen import (`No module named 'cython'`), and only the
    # nightly Nuitka build would notice.
    block = _nuitka_blocks()[name]

    assert "--include-module=av" in block
    # --include-package walks the package directory and compiles the shadow .py files.
    assert not re.search(r"--include-package=av(?![\w-])", block)
    # Nuitka marks "extension module vs source" as an undecided default; pin it.
    assert "--no-prefer-source-code" in block
    assert "--prefer-source-code" not in block
