from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "check_api_trailing_slash.py"


def _load_mod():
    spec = importlib.util.spec_from_file_location("check_api_trailing_slash_sut", SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load_mod()


def _check_src(src: str) -> list[tuple[int, int, str]]:
    import ast

    return MOD.check_file(Path("router.py"), ast.parse(src))


@pytest.mark.unit
def test_flags_trailing_slash_and_allows_valid_exemptions():
    bad = (
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/api/items')\n"
        "@router.get('/list/')\n"
        "def items(): pass\n"
    )
    assert len(_check_src(bad)) == 1

    bad_prefixed_root = (
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/api/items')\n"
        "@router.get('/')\n"
        "def root(): pass\n"
    )
    assert len(_check_src(bad_prefixed_root)) == 1

    good_alias_pair = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/ui')\n"
        "@router.get('/ui/')\n"
        "def ui(): pass\n"
    )
    assert _check_src(good_alias_pair) == []


@pytest.mark.unit
@pytest.mark.parametrize("ancestor", ["build", "dist", ".venv", "frontend"])
def test_repo_under_excluded_ancestor_still_scans_and_excludes_internal_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ancestor: str
):
    """F0324: ancestor directory names matching EXCLUDE_DIRS must not exclude repo files."""
    fake_root = tmp_path / ancestor / "NEKO"
    bad_file = fake_root / "main_routers" / "bad.py"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/api')\n"
        "@router.get('/oops/')\n"
        "def oops(): pass\n",
        encoding="utf-8",
    )
    internal_excluded = fake_root / "dist" / "ignored.py"
    internal_excluded.parent.mkdir(parents=True)
    internal_excluded.write_text(
        "@router.get('/ignored/')\ndef ignored(): pass\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(MOD, "REPO_ROOT", fake_root)
    assert MOD._is_excluded(bad_file) is False
    assert MOD._is_excluded(internal_excluded) is True
    assert MOD.main(["main_routers"]) == 1


@pytest.mark.unit
def test_external_path_under_build_ancestor_still_scans(tmp_path: Path):
    """F0324: explicit external path under /build/ is scanned relative to scan_root."""
    ext_dir = tmp_path / "build" / "ext"
    ext_dir.mkdir(parents=True)
    ext_file = ext_dir / "bad.py"
    ext_file.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/api')\n"
        "@router.get('/oops/')\n"
        "def oops(): pass\n",
        encoding="utf-8",
    )
    assert MOD.main([str(ext_dir)]) == 1
    assert MOD.main([str(ext_file)]) == 1


@pytest.mark.unit
def test_parent_dotdot_build_path_and_cyclic_symlink_do_not_bypass_or_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    """Lexical '..' through /build/ must still scan, and cyclic symlinks must not abort the scan."""
    fake_root = tmp_path / "NEKO"
    fake_root.mkdir(parents=True)
    ext_dir = tmp_path / "build" / "ext"
    ext_dir.mkdir(parents=True)
    ext_file = ext_dir / "bad.py"
    ext_file.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/api')\n"
        "@router.get('/oops/')\n"
        "def oops(): pass\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(MOD, "REPO_ROOT", fake_root)
    assert MOD.main(["../build/ext/bad.py"]) == 1

    loop_dir = tmp_path / "loop_dir"
    loop_dir.mkdir()
    loop_file = loop_dir / "a.py"
    try:
        loop_file.symlink_to(loop_file)
    except OSError:
        pytest.skip("symlinks unavailable in this environment")
    z_file = loop_dir / "z.py"
    z_file.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/api')\n"
        "@router.get('/late/')\n"
        "def late(): pass\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    assert MOD.main([str(loop_dir)]) == 1
    assert "/late/" in capsys.readouterr().out


@pytest.mark.unit
def test_live_repo_passes_check_api_trailing_slash():
    targets = [MOD.REPO_ROOT / p for p in MOD.DEFAULT_PATHS]
    files = list(MOD._iter_python_files(targets))
    assert len(files) > 5
    assert MOD.main([]) == 0
