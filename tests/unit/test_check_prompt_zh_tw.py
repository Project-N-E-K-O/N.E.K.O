# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for ``scripts/check_prompt_zh_tw.py``.

Synthetic-source coverage of ``find_violations`` (the diff-aware core): which
dict shapes count as a localized prompt table, the ratchet on the dict's own
definition line, and noqa suppression. The git plumbing is exercised through
``--base HEAD`` (empty diff -> exit 0) so CI smoke-tests the CLI path without
depending on branch state.
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_prompt_zh_tw.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("check_prompt_zh_tw", SCRIPT_PATH)
    assert spec and spec.loader, f"failed to load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load_script_module()


def _violations(source: str, changed_lines: set[int] | None = None):
    source = textwrap.dedent(source)
    tree = ast.parse(source)
    return MOD.find_violations(tree, source.splitlines(), changed_lines)


# ---------------------------------------------------------------------------
# What counts as a localized prompt table
# ---------------------------------------------------------------------------


def test_flags_en_plus_short_zh_without_traditional():
    src = '''
    TABLE = {
        "zh": "简体",
        "en": "english",
    }
    '''
    out = _violations(src)
    assert len(out) == 1
    assert out[0][0] == 2


def test_flags_en_plus_full_zh_cn_without_traditional():
    """The full-locale scheme (zh-CN keys) is covered too, not just short zh."""
    src = '''
    TABLE = {
        "zh-CN": "简体",
        "en": "english",
    }
    '''
    assert len(_violations(src)) == 1


def test_accepts_table_with_traditional():
    src = '''
    TABLE = {
        "zh": "简体",
        "zh-TW": "繁體",
        "en": "english",
    }
    '''
    assert _violations(src) == []


def test_ignores_dict_without_en_anchor():
    """Without an 'en' key it is not a localized prompt table."""
    src = '''
    NOT_A_TABLE = {
        "zh": "简体",
        "ja": "日本語",
    }
    '''
    assert _violations(src) == []


def test_ignores_dict_without_any_chinese_key():
    src = '''
    NOT_A_TABLE = {
        "en": "english",
        "ja": "japanese",
    }
    '''
    assert _violations(src) == []


def test_ignores_non_string_keys():
    src = '''
    LOOKUP = {
        1: "one",
        2: "two",
    }
    '''
    assert _violations(src) == []


def test_flags_nested_and_multiple_tables():
    """Nested dicts are walked, and each offending table is reported once."""
    src = '''
    OUTER = {
        "greeting": {
            "zh": "你好",
            "en": "hello",
        },
        "farewell": {
            "zh": "再见",
            "en": "bye",
        },
    }
    '''
    out = _violations(src)
    assert len(out) == 2, out
    assert [lineno for lineno, _ in out] == [3, 7]


def test_reports_present_keys_in_message():
    src = '''
    TABLE = {"zh": "a", "en": "b", "ja": "c"}
    '''
    out = _violations(src)
    assert out[0][1] == "en, ja, zh"


# ---------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------


def test_ratchet_reports_dict_whose_definition_line_changed():
    src = '''
    TABLE = {
        "zh": "简体",
        "en": "english",
    }
    '''
    assert len(_violations(src, changed_lines={2})) == 1


def test_ratchet_exempts_dict_whose_definition_line_is_untouched():
    """Editing a table's copy must not trip the gate — only new tables do.

    Line 3 is inside the dict but is not its definition line, which is what
    keeps a copy edit on an existing table from being reported.
    """
    src = '''
    TABLE = {
        "zh": "简体",
        "en": "english",
    }
    '''
    assert _violations(src, changed_lines={3, 4}) == []


def test_ratchet_empty_changed_set_reports_nothing():
    src = '''
    TABLE = {"zh": "a", "en": "b"}
    '''
    assert _violations(src, changed_lines=set()) == []


def test_none_changed_lines_scans_everything():
    """None means full scan, which is how --full and --count behave."""
    src = '''
    TABLE = {"zh": "a", "en": "b"}
    '''
    assert len(_violations(src, changed_lines=None)) == 1


# ---------------------------------------------------------------------------
# noqa
# ---------------------------------------------------------------------------


def test_noqa_on_opening_line_suppresses():
    src = '''
    TABLE = {  # noqa: PROMPT_ZH_TW
        "zh": "简体",
        "en": "english",
    }
    '''
    assert _violations(src) == []


def test_noqa_for_a_different_code_does_not_suppress():
    src = '''
    TABLE = {  # noqa: DOCSTRING_CJK
        "zh": "简体",
        "en": "english",
    }
    '''
    assert len(_violations(src)) == 1


def test_noqa_on_an_inner_line_does_not_suppress():
    """Suppression is anchored to the dict's opening line, not any member."""
    src = '''
    TABLE = {
        "zh": "简体",  # noqa: PROMPT_ZH_TW
        "en": "english",
    }
    '''
    assert len(_violations(src)) == 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
    )


def test_cli_against_head_is_clean():
    """--base HEAD is an empty diff, so the ratchet has nothing to report."""
    result = _run("--base", "HEAD")
    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_count_reports_backlog_and_exits_zero():
    result = _run("--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "missing 'zh-TW'" in result.stdout


def test_repo_backlog_is_nonempty_and_shrinking_is_the_goal():
    """Guards the scanner itself: if this hits 0, the scan silently broke.

    The issue #2500 backlog is large. A zero here means the detector stopped
    matching, not that the work finished — when the backfill genuinely lands,
    replace this with the full-scan gate.
    """
    result = _run("--count")
    count = int(result.stdout.strip().rsplit(":", 1)[1])
    assert count > 0, "scanner found nothing; detection likely broke"
