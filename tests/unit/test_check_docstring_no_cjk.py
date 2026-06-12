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
