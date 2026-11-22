from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "check_no_tkinter.py"


def _load_mod():
    spec = importlib.util.spec_from_file_location("check_no_tkinter_sut", SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load_mod()


@pytest.mark.unit
@pytest.mark.parametrize(
    "snippet",
    [
        "import tkinter\n",
        "import tkinter as tk\n",
        "from tkinter.ttk import Frame\n",
    ],
)
def test_flags_tkinter_imports_and_ignores_strings(snippet: str):
    violations = MOD.check_file(Path("x.py"), ast.parse(snippet))
    assert len(violations) == 1

    safe = '"""tkinter"""\nmsg = "import tkinter"\n'
    assert MOD.check_file(Path("x.py"), ast.parse(safe)) == []


@pytest.mark.unit
@pytest.mark.parametrize("ancestor", ["build", "dist", "frontend", ".venv"])
def test_repo_under_excluded_ancestor_still_scans_and_flags_tkinter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ancestor: str
):
    """F0327: ancestor directory matching EXCLUDE_DIRS must not skip repo scan."""
    fake_root = tmp_path / ancestor / "NEKO"
    bad_file = fake_root / "utils" / "bad.py"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text("import tkinter as tk\n", encoding="utf-8")

    excluded = fake_root / "build" / "ignored.py"
    excluded.parent.mkdir(parents=True)
    excluded.write_text("import tkinter\n", encoding="utf-8")

    monkeypatch.setattr(MOD, "REPO_ROOT", fake_root)
    assert MOD._is_excluded(bad_file) is False
    assert MOD._is_excluded(excluded) is True
    assert MOD.main(["."]) == 1


@pytest.mark.unit
def test_external_path_under_build_ancestor_flags_tkinter(tmp_path: Path):
    """F0327: explicit external path under /build/ is still checked."""
    ext_dir = tmp_path / "build" / "ext"
    ext_dir.mkdir(parents=True)
    ext_file = ext_dir / "bad.py"
    ext_file.write_text("from tkinter import Tk\n", encoding="utf-8")

    assert MOD.main([str(ext_dir)]) == 1
    assert MOD.main([str(ext_file)]) == 1


@pytest.mark.unit
def test_live_repo_passes_check_no_tkinter():
    files = list(MOD._iter_python_files([MOD.REPO_ROOT]))
    assert len(files) > 50
    assert MOD.main([]) == 0
