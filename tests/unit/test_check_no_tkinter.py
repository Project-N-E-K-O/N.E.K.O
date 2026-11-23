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
def test_dotdot_paths_and_cyclic_symlink_do_not_bypass_or_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    """Relative '..' paths through build/ still scan and cyclic symlinks do not abort."""
    fake_root = tmp_path / "NEKO"
    build_dir = fake_root / "build"
    build_dir.mkdir(parents=True)
    utils_dir = fake_root / "utils"
    utils_dir.mkdir(parents=True)
    (utils_dir / "bad.py").write_text("import tkinter\n", encoding="utf-8")

    ext_dir = tmp_path / "build" / "ext"
    ext_dir.mkdir(parents=True)
    (ext_dir / "bad.py").write_text("from tkinter import Tk\n", encoding="utf-8")

    monkeypatch.setattr(MOD, "REPO_ROOT", fake_root)
    assert MOD.main(["../build/ext/bad.py"]) == 1
    assert MOD.main(["build/../utils"]) == 1

    loop_dir = tmp_path / "loop_dir"
    loop_dir.mkdir()
    loop_file = loop_dir / "a.py"
    try:
        loop_file.symlink_to(loop_file)
    except OSError:
        pytest.skip("symlinks unavailable in this environment")
    (loop_dir / "z.py").write_text("import tkinter\n", encoding="utf-8")
    capsys.readouterr()
    assert MOD.main([str(loop_dir)]) == 1
    assert "z.py" in capsys.readouterr().out


@pytest.mark.unit
def test_live_repo_passes_check_no_tkinter():
    files = list(MOD._iter_python_files([MOD.REPO_ROOT]))
    assert len(files) > 50
    assert MOD.main([]) == 0
