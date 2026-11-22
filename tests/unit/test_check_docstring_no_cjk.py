"""Unit tests for ``scripts/check_docstring_no_cjk.py``.

Synthetic-source coverage of ``find_violations`` (the diff-aware core):
docstring kinds (module / class / def / async def), the changed-lines
gate, noqa suppression, the docstring-vs-other-strings boundary, and the
plugin/ + local_server/ policy exemptions. The git plumbing is exercised
separately with ``--base HEAD`` (empty diff → exit 0) so CI smoke-tests
the CLI path without depending on the branch state.
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_docstring_no_cjk.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "check_docstring_no_cjk", SCRIPT_PATH,
    )
    assert spec and spec.loader, f"failed to load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load_script_module()


def _violations(source: str, changed_lines: set[int] | None):
    source = textwrap.dedent(source)
    tree = ast.parse(source)
    return MOD.find_violations(tree, source.splitlines(), changed_lines)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_flags_cjk_in_function_docstring_full_scan():
    src = '''
    def f():
        """中文 docstring."""
        return 1
    '''
    out = _violations(src, None)
    assert len(out) == 1
    assert "docstring of f" in out[0][2]


def test_flags_module_class_and_async_docstrings():
    src = '''
    """モジュール docstring."""

    class C:
        """클래스 docstring."""

        async def g(self):
            """中文 docstring."""
    '''
    out = _violations(src, None)
    owners = {msg.split("docstring of ")[1].split(" ")[0] for _, _, msg in out}
    assert owners == {"<module>", "C", "g"}


def test_english_docstring_and_non_docstring_cjk_pass():
    src = '''
    def f():
        """English docstring."""
        x = "中文字符串字面量不是 docstring"  # 中文注释也不管
        return x
    '''
    assert _violations(src, None) == []


def test_string_statement_not_in_first_position_is_not_a_docstring():
    src = '''
    def f():
        x = 1
        "中文裸字符串，不是 docstring"
        return x
    '''
    assert _violations(src, None) == []


# ---------------------------------------------------------------------------
# Diff gate
# ---------------------------------------------------------------------------


def test_diff_gate_flags_only_overlapping_docstrings():
    src = '''
    def touched():
        """中文 A."""

    def untouched():
        """中文 B."""
    '''
    # Docstring of `touched` sits on line 3 of the dedented source.
    out = _violations(src, {3})
    assert len(out) == 1
    assert "docstring of touched" in out[0][2]


def test_diff_gate_multiline_span_overlap():
    src = '''
    def f():
        """First line is English.

        中文在第三行。
        """
    '''
    # Span is lines 3-6; touching any line of it (here: 5) flags it.
    assert len(_violations(src, {5})) == 1
    # Touching lines outside the span does not.
    assert _violations(src, {2}) == []


# ---------------------------------------------------------------------------
# noqa
# ---------------------------------------------------------------------------


def test_noqa_on_docstring_line_suppresses():
    src = '''
    def f():
        """中文 docstring."""  # noqa: DOCSTRING_CJK  # fixture content
    '''
    assert _violations(src, None) == []


def test_noqa_with_other_code_does_not_suppress():
    src = '''
    def f():
        """中文 docstring."""  # noqa: SOME_OTHER_CODE
    '''
    assert len(_violations(src, None)) == 1


# ---------------------------------------------------------------------------
# Policy exemptions
# ---------------------------------------------------------------------------


def test_plugin_and_local_server_are_exempt():
    assert MOD._is_excluded(MOD.REPO_ROOT / "plugin" / "host.py")
    assert MOD._is_excluded(
        MOD.REPO_ROOT / "plugin" / "plugins" / "x" / "main.py"
    )
    assert MOD._is_excluded(MOD.REPO_ROOT / "local_server" / "tts.py")


def test_main_program_dirs_are_covered():
    for top in ("utils", "memory", "main_routers", "config", "tests", "scripts"):
        assert not MOD._is_excluded(MOD.REPO_ROOT / top / "x.py"), top


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_diff_mode_empty_diff_exits_zero():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--base", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_missing_base_ref_skips_with_warning():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--base", "no-such-ref-xyz"],
        cwd=PROJECT_ROOT,
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "skipping" in result.stderr


# ---------------------------------------------------------------------------
# F0325: git rename handling
# ---------------------------------------------------------------------------


def _init_git_repo(repo: Path, monkeypatch) -> None:
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_pure_rename_with_preexisting_cjk_docstring_passes(tmp_path: Path, monkeypatch):
    """F0325: renaming a file without editing its CJK docstring must exit 0."""
    _init_git_repo(tmp_path, monkeypatch)
    old_file = tmp_path / "pkg" / "old_mod.py"
    old_file.parent.mkdir(parents=True)
    old_file.write_text(
        'def helper():\n    """旧中文说明。"""\n    return 1\n',
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feat")

    new_file = tmp_path / "pkg" / "new_mod.py"
    old_file.rename(new_file)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "rename")

    monkeypatch.setattr(MOD, "REPO_ROOT", tmp_path)
    assert MOD.main(["--base", "main"]) == 0


def test_rename_with_edit_flags_only_newly_modified_cjk_docstring(
    tmp_path: Path, monkeypatch, capsys
):
    """F0325: rename + edit only flags touched docstrings, not untouched moved ones."""
    _init_git_repo(tmp_path, monkeypatch)
    old_file = tmp_path / "pkg" / "mod_a.py"
    old_file.parent.mkdir(parents=True)
    old_file.write_text(
        "def untouched():\n"
        '    """保留的旧中文文档。"""\n'
        "    return 1\n\n"
        "def touched():\n"
        '    """Original English docstring."""\n'
        "    return 2\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feat")

    new_file = tmp_path / "pkg" / "mod_b.py"
    old_file.unlink()
    new_file.write_text(
        "def untouched():\n"
        '    """保留的旧中文文档。"""\n'
        "    return 1\n\n"
        "def touched():\n"
        '    """新改出来的中文文档。"""\n'
        "    return 2\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "rename and edit")

    monkeypatch.setattr(MOD, "REPO_ROOT", tmp_path)
    assert MOD.main(["--base", "main"]) == 1
    out = capsys.readouterr().out
    assert "docstring of touched" in out
    assert "docstring of untouched" not in out


def test_rename_with_recreated_old_path_and_spaces_uses_blob_diff(
    tmp_path: Path, monkeypatch
):
    """F0325: blob-to-blob diff handles recreated old paths and filenames with spaces."""
    _init_git_repo(tmp_path, monkeypatch)
    old_file = tmp_path / "pkg" / "my file.py"
    old_file.parent.mkdir(parents=True)
    body_lines = [f"def fn_{i}():\n    return {i}\n" for i in range(20)]
    old_file.write_text(
        "".join(body_lines) + 'def moved():\n    """旧中文。"""\n    return 1\n',
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feat")

    new_file = tmp_path / "pkg" / "new name.py"
    old_file.rename(new_file)
    old_file.write_text('def replacement():\n    """English."""\n    return 2\n', encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "rename and recreate")

    monkeypatch.setattr(MOD, "REPO_ROOT", tmp_path)
    assert MOD.main(["--base", "main"]) == 0


def test_move_from_excluded_dir_into_enforced_dir_still_flags_cjk(
    tmp_path: Path, monkeypatch, capsys
):
    """F0325: moving a file out of an excluded dir (plugin/) must not bypass docstring checks."""
    _init_git_repo(tmp_path, monkeypatch)
    old_file = tmp_path / "plugin" / "helper.py"
    old_file.parent.mkdir(parents=True)
    old_file.write_text(
        'def p():\n    """插件里的中文。"""\n    return 1\n',
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feat")

    new_file = tmp_path / "utils" / "helper.py"
    new_file.parent.mkdir(parents=True)
    old_file.rename(new_file)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "promote to utils")

    monkeypatch.setattr(MOD, "REPO_ROOT", tmp_path)
    assert MOD.main(["--base", "main"]) == 1
    out = capsys.readouterr().out
    assert "docstring of p" in out
