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

Two layers: which dict shapes count as a localized prompt table
(``find_violations``), and what the signature ratchet does to a given
base -> head transition (``signature_counter``). The ratchet layer needs no git
because it compares two {path: source} mappings, so each scenario — added key,
pure rename, copy edit — is expressed directly. The git plumbing is smoke-tested
through ``--base HEAD`` (empty diff -> exit 0).
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


def _violations(source: str):
    source = textwrap.dedent(source)
    tree = ast.parse(source)
    return MOD.find_violations(tree, source.splitlines())


def _added(base: dict[str, str], head: dict[str, str]):
    """Signatures HEAD has more of than base — what the ratchet reports."""
    base = {k: textwrap.dedent(v) for k, v in base.items()}
    head = {k: textwrap.dedent(v) for k, v in head.items()}
    return MOD.signature_counter(head) - MOD.signature_counter(base)


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
    assert out[0][1] == frozenset({"zh", "en"})


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


# ---------------------------------------------------------------------------
# The signature ratchet
# ---------------------------------------------------------------------------


def test_ratchet_flags_a_brand_new_table():
    added = _added({"a.py": ""}, {"a.py": 'T = {"en": "x", "zh": "y"}'})
    assert sum(added.values()) == 1


def test_ratchet_flags_table_that_becomes_localized_via_added_key():
    """A pre-existing en/ja table gaining a 'zh' key must be caught.

    This is the case a line-based ratchet misses: the dict's definition line is
    unchanged, only a member line is added. It is also the likeliest way for the
    backlog to grow, so missing it would defeat the gate.
    """
    base = {"a.py": 'T = {"en": "x", "ja": "y"}'}
    head = {"a.py": 'T = {"en": "x", "ja": "y", "zh": "z"}'}
    added = _added(base, head)
    assert sum(added.values()) == 1
    assert frozenset({"en", "ja", "zh"}) in added


def test_ratchet_ignores_a_pure_rename():
    """Renaming a prompt module with no content change reports nothing.

    A line-based ratchet counts every line of the new path as added and would
    report the file's whole existing backlog.
    """
    src = '''
    T = {
        "zh": "简体",
        "en": "english",
    }
    '''
    assert not _added({"old_name.py": src}, {"new_name.py": src})


def test_ratchet_ignores_a_copy_edit():
    """Editing an existing table's text must not trip the gate."""
    base = {"a.py": 'T = {"en": "old copy", "zh": "旧文案"}'}
    head = {"a.py": 'T = {"en": "new copy", "zh": "新文案"}'}
    assert not _added(base, head)


def test_ratchet_ignores_adding_traditional_to_an_existing_table():
    """Backfilling zh-TW removes an offender; nothing is reported as added."""
    base = {"a.py": 'T = {"en": "x", "zh": "y"}'}
    head = {"a.py": 'T = {"en": "x", "zh": "y", "zh-TW": "z"}'}
    assert not _added(base, head)


def test_ratchet_counts_multiplicity_not_just_presence():
    """Two new tables sharing one key set still count as two."""
    base = {"a.py": 'A = {"en": "x", "zh": "y"}'}
    head = {
        "a.py": 'A = {"en": "x", "zh": "y"}',
        "b.py": 'B = {"en": "p", "zh": "q"}\nC = {"en": "r", "zh": "s"}',
    }
    added = _added(base, head)
    assert sum(added.values()) == 2
    assert added[frozenset({"en", "zh"})] == 2


def test_ratchet_documented_blind_spot_nets_to_zero():
    """Removing one offender while adding another with the same key set passes.

    Pinned deliberately: the script's docstring calls this out as an accepted
    tradeoff, because the alternative is matching dicts across revisions by
    position, which breaks on any reformat. Closing it would need a different
    identity scheme, not a tweak.
    """
    base = {"a.py": 'A = {"en": "x", "zh": "y"}'}
    head = {"b.py": 'B = {"en": "p", "zh": "q"}'}
    assert not _added(base, head)


# ---------------------------------------------------------------------------
# Locating the offender for the error message
# ---------------------------------------------------------------------------


_TWO_TABLES = {
    "new.py": 'NEW = {\n    "en": "a",\n    "zh": "b",\n}',
    "old.py": 'OLD = {\n    "en": "c",\n    "zh": "d",\n}',
}
_EN_ZH = frozenset({"en", "zh"})


def test_locate_narrows_to_tables_the_diff_touched():
    """Common key sets match many tables, so the message needs the touched one.

    Without this, a failure on the {en, zh} signature lists every pre-existing
    table sharing it and the developer has to guess which one is theirs.
    """
    likely, other = MOD.locate(_TWO_TABLES, _EN_ZH, {"new.py": {2}})
    assert likely == ["new.py:1"]
    assert other == 1


def test_locate_matches_anywhere_in_the_dict_body():
    """A key added on the dict's last line still identifies that dict."""
    likely, _ = MOD.locate(_TWO_TABLES, _EN_ZH, {"new.py": {3}})
    assert likely == ["new.py:1"]


def test_locate_without_hints_reports_everything_as_pre_existing():
    likely, other = MOD.locate(_TWO_TABLES, _EN_ZH, None)
    assert likely == []
    assert other == 2


def test_locate_hint_outside_any_table_body_is_ignored():
    """Touching an unrelated line must not mislabel a table as the new one."""
    sources = {"a.py": 'X = 1\n\nT = {\n    "en": "a",\n    "zh": "b",\n}'}
    likely, other = MOD.locate(sources, _EN_ZH, {"a.py": {1}})
    assert likely == []
    assert other == 1


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
# File discovery
# ---------------------------------------------------------------------------


def test_sources_on_disk_includes_subpackages(tmp_path, monkeypatch):
    """--full / --count must not stop at the top level of config/prompts.

    Diff mode selects files with `git ls-tree -r`, so a top-level-only glob here
    would make the two modes disagree about what a prompt module is.
    """
    (tmp_path / "sub").mkdir()
    (tmp_path / "top.py").write_text('T = {"en": "a", "zh": "b"}', encoding="utf-8")
    (tmp_path / "sub" / "nested.py").write_text(
        'N = {"en": "c", "zh": "d"}', encoding="utf-8"
    )
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    monkeypatch.setattr(MOD, "PROMPTS_DIR", tmp_path)
    monkeypatch.setattr(MOD, "REPO_ROOT", tmp_path)

    sources = MOD._sources_on_disk()
    assert set(sources) == {"top.py", "sub/nested.py"}
    assert sum(MOD.signature_counter(sources).values()) == 2


# ---------------------------------------------------------------------------
# main() — the ratchet's actual call site
# ---------------------------------------------------------------------------


def _stub_revisions(monkeypatch, base_src: dict[str, str], head_src: dict[str, str],
                    touched: dict[str, set[int]] | None = None):
    """Point main() at two synthetic revisions instead of git."""
    monkeypatch.setattr(MOD, "_merge_base", lambda base: "BASE_SHA")
    monkeypatch.setattr(MOD, "_sources_at", lambda rev: base_src)
    monkeypatch.setattr(MOD, "_sources_on_disk", lambda: head_src)
    monkeypatch.setattr(MOD, "_touched_lines", lambda rev: touched or {})


def test_main_fails_when_head_gained_an_offender(monkeypatch, capsys):
    """Covers the subtraction direction in main(), not just the helper.

    Asserting on signature_counter alone lets `head - base` silently become
    `base - head`, which never reports anything and disables the gate.
    """
    _stub_revisions(
        monkeypatch,
        {"a.py": 'T = {"en": "x", "ja": "y"}'},
        {"a.py": 'T = {"en": "x", "ja": "y", "zh": "z"}'},
        {"a.py": {1}},
    )
    assert MOD.main(["--base", "irrelevant"]) == 1
    out = capsys.readouterr().out
    assert "a.py:1" in out


def test_main_passes_when_head_backfilled_an_offender(monkeypatch):
    """The reverse transition must pass — removing an offender is the goal."""
    _stub_revisions(
        monkeypatch,
        {"a.py": 'T = {"en": "x", "zh": "y"}'},
        {"a.py": 'T = {"en": "x", "zh": "y", "zh-TW": "z"}'},
    )
    assert MOD.main(["--base", "irrelevant"]) == 0


def test_main_passes_on_an_unchanged_tree(monkeypatch):
    src = {"a.py": 'T = {"en": "x", "zh": "y"}'}
    _stub_revisions(monkeypatch, src, src)
    assert MOD.main(["--base", "irrelevant"]) == 0


def test_touched_lines_asks_git_for_rename_detection(monkeypatch):
    """`-M` is load-bearing: without it a rename marks every line as added.

    The pass/fail decision would still be right (signatures are unaffected), but
    the reported location would point at the whole renamed file.
    """
    seen: list[tuple[str, ...]] = []

    def fake_git(*args: str) -> str:
        seen.append(args)
        return ""

    monkeypatch.setattr(MOD, "_git", fake_git)
    MOD._touched_lines("BASE_SHA")
    assert seen, "no git invocation recorded"
    assert "-M" in seen[0], seen[0]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
    )


def test_cli_against_head_is_clean():
    """--base HEAD compares HEAD to itself, so nothing is newly added."""
    result = _run("--base", "HEAD")
    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_count_reports_backlog_and_exits_zero():
    result = _run("--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "missing 'zh-TW'" in result.stdout


def test_repo_backlog_is_nonempty():
    """Guards the scanner itself: if this hits 0, detection silently broke.

    The issue #2500 backlog is large. A zero here means the detector stopped
    matching, not that the work finished — when the backfill genuinely lands,
    replace this with the full-scan gate.
    """
    result = _run("--count")
    count = int(result.stdout.strip().rsplit(":", 1)[1])
    assert count > 0, "scanner found nothing; detection likely broke"
