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
(``find_violations``), and what the count ratchet does to a given
base -> head transition (``count_offenders``). The ratchet layer needs no git
because it compares two {path: source} mappings, so each scenario — added key,
pure rename, copy edit — is expressed directly. The git plumbing is smoke-tested
through ``--base HEAD`` (empty diff -> exit 0).
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

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
    """Run the real parse path so noqa comes from comments, not raw lines."""
    source = textwrap.dedent(source)
    tree, comments = MOD._parse_source(source, 'test.py')
    assert tree is not None
    return MOD.find_violations(tree, comments)


def _grew(base: dict[str, str], head: dict[str, str]) -> int:
    """How many more offenders HEAD has than base — the ratchet decision."""
    base = {k: textwrap.dedent(v) for k, v in base.items()}
    head = {k: textwrap.dedent(v) for k, v in head.items()}
    return MOD.count_offenders(head) - MOD.count_offenders(base)


def _touched_from(diff: str) -> dict[str, set[int]]:
    """Parse a diff into touched lines without invoking git."""
    original = MOD._git
    MOD._git = lambda *_a: diff
    try:
        return MOD._touched_lines("BASE_SHA")
    finally:
        MOD._git = original


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
    assert out == [2]


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


def test_flags_dict_constructor_call():
    """`dict(en=..., zh=...)` has the same runtime shape and the same problem."""
    assert _violations('TABLE = dict(en="english", zh="简体")') == [1]


def test_ignores_dict_constructor_with_unpacking():
    """`**` is where such a table would have to put 'zh-TW', so judging is unsafe.

    `dict()` cannot name zh-TW as a keyword (not an identifier), so unpacking is
    the only way to write one — reporting it would be a false positive, and a
    gate that cries wolf gets worked around instead of satisfied.
    """
    assert _violations('T = dict(en="e", zh="s", **{"zh-TW": "t"})') == []


def test_ignores_dict_literal_with_unpacking():
    assert _violations('T = {"en": "e", "zh": "s", **OTHER_TABLE}') == []


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
    assert out == [3, 7]


# ---------------------------------------------------------------------------
# The count ratchet
# ---------------------------------------------------------------------------


def test_ratchet_flags_a_brand_new_table():
    assert _grew({"a.py": ""}, {"a.py": 'T = {"en": "x", "zh": "y"}'}) == 1


def test_ratchet_flags_table_that_becomes_localized_via_added_key():
    """A pre-existing en/ja table gaining a 'zh' key must be caught.

    This is the case a line-based ratchet misses: the dict's definition line is
    unchanged, only a member line is added. It is also the likeliest way for the
    backlog to grow, so missing it would defeat the gate.
    """
    base = {"a.py": 'T = {"en": "x", "ja": "y"}'}
    head = {"a.py": 'T = {"en": "x", "ja": "y", "zh": "z"}'}
    assert _grew(base, head) == 1


def test_ratchet_ignores_adding_an_unrelated_locale():
    """Adding a 'fr' template to a pre-existing offender did not grow the backlog.

    This is why the signature is the Simplified key rather than the whole key
    set: counting whole sets made {en, zh} -> {en, zh, fr} look like a new table
    and failed PRs that only added a language.
    """
    base = {"a.py": 'T = {"en": "x", "zh": "y"}'}
    head = {"a.py": 'T = {"en": "x", "zh": "y", "fr": "z"}'}
    assert _grew(base, head) <= 0


def test_ratchet_flags_a_new_table_under_either_scheme():
    """A new zh-CN-scheme offender counts, same as a zh-scheme one."""
    base = {"a.py": 'A = {"en": "x", "zh": "y"}'}
    head = {
        "a.py": 'A = {"en": "x", "zh": "y"}',
        "b.py": 'B = {"en": "p", "zh-CN": "q"}',
    }
    assert _grew(base, head) == 1


def test_ratchet_ignores_a_scheme_migration():
    """Renaming an offender's key from 'zh' to 'zh-CN' did not grow the backlog.

    This is why the ratchet counts a plain total. Counting the two schemes
    separately made a migration read as one scheme losing a table and the other
    gaining one, and Counter subtraction keeps only the positive side — reporting
    growth that never happened.
    """
    base = {"a.py": 'T = {"en": "x", "zh": "y"}'}
    head = {"a.py": 'T = {"en": "x", "zh-CN": "y"}'}
    assert _grew(base, head) == 0


def test_ratchet_ignores_a_bulk_scheme_migration():
    """issue #2500's endgame renames 'zh' to 'zh-CN' across every table.

    A gate that failed on that would be blocking the migration it exists to
    serve, so this pins the whole-file case, not just one table.
    """
    base = {"a.py": "\n".join(
        f'T{i} = {{"en": "x", "zh": "y{i}"}}' for i in range(5)
    )}
    head = {"a.py": "\n".join(
        f'T{i} = {{"en": "x", "zh-CN": "y{i}"}}' for i in range(5)
    )}
    assert _grew(base, head) == 0


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
    assert _grew({"old_name.py": src}, {"new_name.py": src}) == 0


def test_ratchet_ignores_a_copy_edit():
    """Editing an existing table's text must not trip the gate."""
    base = {"a.py": 'T = {"en": "old copy", "zh": "旧文案"}'}
    head = {"a.py": 'T = {"en": "new copy", "zh": "新文案"}'}
    assert _grew(base, head) <= 0


def test_ratchet_ignores_adding_traditional_to_an_existing_table():
    """Backfilling zh-TW removes an offender; nothing is reported as added."""
    base = {"a.py": 'T = {"en": "x", "zh": "y"}'}
    head = {"a.py": 'T = {"en": "x", "zh": "y", "zh-TW": "z"}'}
    assert _grew(base, head) <= 0


def test_ratchet_counts_multiplicity_not_just_presence():
    """Two new offenders raise the total by two, not by one."""
    base = {"a.py": 'A = {"en": "x", "zh": "y"}'}
    head = {
        "a.py": 'A = {"en": "x", "zh": "y"}',
        "b.py": 'B = {"en": "p", "zh": "q"}\nC = {"en": "r", "zh": "s"}',
    }
    assert _grew(base, head) == 2


def test_ratchet_documented_blind_spot_nets_to_zero():
    """Removing one offender while adding another nets to zero and passes.

    Pinned deliberately: the script's docstring calls this out as an accepted
    tradeoff, because the alternative is matching dicts across revisions by
    position, which breaks on any reformat. Closing it would need a different
    identity scheme, not a tweak.
    """
    base = {"a.py": 'A = {"en": "x", "zh": "y"}'}
    head = {"b.py": 'B = {"en": "p", "zh": "q"}'}
    assert _grew(base, head) <= 0


# ---------------------------------------------------------------------------
# Locating the offender for the error message
# ---------------------------------------------------------------------------


_TWO_TABLES = {
    "new.py": 'NEW = {\n    "en": "a",\n    "zh": "b",\n}',
    "old.py": 'OLD = {\n    "en": "c",\n    "zh": "d",\n}',
}


_NESTED_SHARING_A_LINE = 'T = {"en": {\n    "en": "a",\n    "zh": "b",\n  },\n  "zh": "s",\n  "other": "x",\n}'


def test_nested_table_sharing_its_parents_opening_line():
    """Both tables count when a nested one opens on its parent's line.

    Outer resolves to {en, other, zh} and inner to {en, zh}; neither has zh-TW, so
    both are offenders and both report line 1.
    """
    src = _NESTED_SHARING_A_LINE
    assert MOD.find_violations(ast.parse(src), src.splitlines()) == [1, 1]


def test_locate_uses_each_nodes_own_span_not_the_survivors():
    """A nested table must not shadow its parent's span.

    Keying nodes by start line let the inner table (ending line 4) evict the outer
    one (ending line 7). Touching line 5 — inside the parent, past the child — then
    matched against the child's shorter span and classified both as pre-existing,
    degrading the message to "run --full".
    """
    src = _NESTED_SHARING_A_LINE
    likely, other = MOD.locate_touched({"p.py": src}, {"p.py": {5}})
    assert likely == ["p.py:1"]
    assert other == 1


def test_locate_deduplicates_labels_for_tables_on_one_line():
    """Two offenders on one line render to one label, not the same line twice."""
    src = _NESTED_SHARING_A_LINE
    likely, other = MOD.locate_touched({"p.py": src}, {"p.py": {1}})
    assert likely == ["p.py:1"]
    assert other == 0


def test_locate_narrows_to_tables_the_diff_touched():
    """A bare total says nothing about where, so the message needs the touched one.

    Without this, a failure on the {en, zh} signature lists every pre-existing
    table sharing it and the developer has to guess which one is theirs.
    """
    likely, other = MOD.locate_touched(_TWO_TABLES, {"new.py": {2}})
    assert likely == ["new.py:1"]
    assert other == 1


def test_locate_matches_anywhere_in_the_dict_body():
    """A key added on the dict's last line still identifies that dict."""
    likely, _ = MOD.locate_touched(_TWO_TABLES, {"new.py": {3}})
    assert likely == ["new.py:1"]


def test_locate_without_hints_reports_everything_as_pre_existing():
    likely, other = MOD.locate_touched(_TWO_TABLES, None)
    assert likely == []
    assert other == 2


def test_locate_hint_outside_any_table_body_is_ignored():
    """Touching an unrelated line must not mislabel a table as the new one."""
    sources = {"a.py": 'X = 1\n\nT = {\n    "en": "a",\n    "zh": "b",\n}'}
    likely, other = MOD.locate_touched(sources, {"a.py": {1}})
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


def _has_noqa_under_test(line: str) -> bool:
    return MOD._has_noqa(line)


def _sibling_has_noqa(line: str) -> bool:
    """The shared noqa implementation, loaded from a sibling gate."""
    spec = importlib.util.spec_from_file_location(
        "check_docstring_no_cjk", PROJECT_ROOT / "scripts" / "check_docstring_no_cjk.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._has_noqa(line, "PROMPT_ZH_TW")


@pytest.mark.parametrize("line", [
    "# noqa: PROMPT_ZH_TW",
    "# noqa: PROMPT_ZH_TW, DOCSTRING_CJK",
    # This code not first in the list, and a bare noqa: both were silently
    # rejected by an implementation that anchored the code right after `noqa:`.
    "# noqa: DOCSTRING_CJK, PROMPT_ZH_TW",
    "# noqa: E501,PROMPT_ZH_TW",
    "# noqa",
    "# noqa: PROMPT_ZH_TW  # rationale",
    "# noqa: PROMPT_ZH_TW_EXTRA",
    "# noqa: OTHER",
    "no comment at all",
])
def test_noqa_matches_the_sibling_gates(line):
    """Suppression must behave exactly like the other four gates (and ruff).

    An author who writes the form the rest of the repo uses would otherwise find
    their suppression ignored here and nowhere else.
    """
    assert _has_noqa_under_test(line) == _sibling_has_noqa(line), line


def test_noqa_on_the_closing_line_suppresses():
    """`}  # noqa: PROMPT_ZH_TW` is a natural place to put it.

    It also matters for merge expressions, whose node lineno is the left operand's
    line rather than what an author would call the table's start.
    """
    assert _violations('T = {\n    "en": "e",\n    "zh": "s",\n}  # noqa: PROMPT_ZH_TW') == []
    assert _violations('T = {\n    "en": "e",\n    "zh": "s",\n}') == [1]


@pytest.mark.parametrize("marker", [" ", " ", "\x0c", "\x0b", ""])
def test_line_split_matches_ast_line_numbers(marker):
    """`str.splitlines()` breaks on characters CPython does not count as newlines.

    One inside a string literal shifted every later line by one, so a noqa stopped
    matching its own table and started matching a neighbour — here the suppressed
    table would be reported and the real offender missed.
    """
    src = (
        'A = {"en": "x' + marker + 'y", "ja": "z"}\n'
        'B = {"en": 1, "zh": 2}  # noqa: PROMPT_ZH_TW\n'
        'C = {"en": 1, "zh": 3}'
    )
    tree, lines = MOD._parse_source(src, "probe.py")
    assert MOD.find_violations(tree, lines) == [3]


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_line_split_handles_every_python_line_ending(newline):
    """Including a lone CR — `split("\\n")` would collapse that to one line."""
    src = newline.join([
        'A = {"en": 1, "zh": 2}  # noqa: PROMPT_ZH_TW',
        'B = {"en": 1, "zh": 3}',
    ])
    tree, lines = MOD._parse_source(src, "probe.py")
    assert MOD.find_violations(tree, lines) == [2]


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
    assert MOD.count_offenders(sources) == 2


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

    Asserting on count_offenders alone lets `head - base` silently become
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


def test_main_forces_utf8_on_its_own_streams(monkeypatch):
    """The gate must not encode its output with the locale encoding.

    Asserting the reconfigure call is asserting the mechanism, because the failure
    it prevents is not reproducible in-process: when stdout is a pipe, Python picks
    the locale encoding — cp1252 on the Windows CI runner — and printing a
    non-ASCII path or a SyntaxError carrying CJK source raises UnicodeEncodeError
    from inside the gate. That is exactly how this gate first went red on CI.
    """
    calls: list[dict[str, object]] = []

    class _Stream:
        def reconfigure(self, **kwargs):
            calls.append(kwargs)

        def write(self, _text):
            return None

        def flush(self):
            return None

    monkeypatch.setattr(sys, "stdout", _Stream())
    monkeypatch.setattr(sys, "stderr", _Stream())
    _stub_revisions(monkeypatch, {}, {})
    MOD.main(["--base", "irrelevant"])

    assert len(calls) == 2, calls
    assert all(c.get("encoding") == "utf-8" for c in calls), calls
    assert all(c.get("errors") == "replace" for c in calls), calls


def test_main_tolerates_streams_without_reconfigure(monkeypatch):
    """Older/wrapped streams lack reconfigure; the gate must not crash on them."""

    class _Bare:
        def write(self, _text):
            return None

        def flush(self):
            return None

    monkeypatch.setattr(sys, "stdout", _Bare())
    monkeypatch.setattr(sys, "stderr", _Bare())
    _stub_revisions(monkeypatch, {}, {})
    assert MOD.main(["--base", "irrelevant"]) == 0


def test_main_passes_on_an_unchanged_tree(monkeypatch):
    src = {"a.py": 'T = {"en": "x", "zh": "y"}'}
    _stub_revisions(monkeypatch, src, src)
    assert MOD.main(["--base", "irrelevant"]) == 0


def test_sources_on_disk_skips_undecodable_file(tmp_path, monkeypatch, capsys):
    """A non-UTF-8 prompt module is skipped, not fatal.

    UnicodeDecodeError is a ValueError, not an OSError, so catching only OSError
    would take the whole gate down with a traceback over one bad file.
    """
    (tmp_path / "good.py").write_text('T = {"en": "a", "zh": "b"}', encoding="utf-8")
    (tmp_path / "bad.py").write_bytes(b'T = {"en": "\xff\xfe not utf-8"}')
    monkeypatch.setattr(MOD, "PROMPTS_DIR", tmp_path)
    monkeypatch.setattr(MOD, "REPO_ROOT", tmp_path)

    sources = MOD._sources_on_disk()
    assert set(sources) == {"good.py"}
    assert "bad.py" in capsys.readouterr().err
    assert MOD.count_offenders(sources) == 1


def _stub_git_stdout(monkeypatch, stdout: bytes):
    class _Result:
        returncode = 0
        stderr = b""

    _Result.stdout = stdout
    monkeypatch.setattr(MOD.subprocess, "run", lambda cmd, **kw: _Result())


def test_git_text_decoding_survives_bad_bytes(monkeypatch):
    """`_git` decodes git's own reporting without crashing on a bad byte.

    Asserts the behavior rather than the subprocess kwargs: diff headers and path
    lists must come back as text even when a byte is not valid UTF-8, because the
    alternative is the gate dying mid-decode on unrelated repo content.
    """
    _stub_git_stdout(monkeypatch, b"ok\xff\xfe")
    out = MOD._git("diff", "--name-only")
    assert out.startswith("ok")
    assert "�" in out, out


def test_git_bytes_hands_back_raw_stdout(monkeypatch):
    """Source blobs stay bytes so `_decode_source` can honour PEP 263.

    If this decoded eagerly, a `# coding: latin-1` module would already be
    mangled before its declaration was ever read.
    """
    _stub_git_stdout(monkeypatch, b"# coding: latin-1\nT = {'en': 'caf\xe9'}\n")
    raw = MOD._git_bytes("show", "X:y.py")
    assert isinstance(raw, bytes)
    assert raw.endswith(b"\n")


def test_dict_call_with_positional_base_is_not_judged():
    """`dict(BASE, zh=...)` must be left alone: BASE may hold the zh-TW entry.

    Same unknowable-keys problem as `**`, and a gate that cries wolf gets worked
    around rather than satisfied.
    """
    assert _violations("T = dict(BASE, en='e', zh='s')") == []
    assert _violations("T = dict({'zh-TW': 't'}, en='e', zh='s')") == []
    # Keyword-only is still fully knowable, so it is still judged.
    assert len(_violations("T = dict(en='e', zh='s')")) == 1


@pytest.mark.parametrize("src", [
    'T = dict({"en": "e", "zh": "s"}, **{"zh-TW": "t"})',
    'T = {**{"en": "e", "zh": "s"}, **{"zh-TW": "t"}}',
    'T = {"en": "e", "zh": "s"} | {"zh-TW": "t"}',
    'T = dict({"en": "e", "zh": "s"}, {"zh-TW": "t"})',
])
def test_merge_resolving_to_a_compliant_table_is_silent(src):
    """A merge whose *result* carries zh-TW must not report its fragments.

    Plain ast.walk judges each half on its own and flags the `{en, zh}` literal,
    even though the assembled mapping is compliant.
    """
    assert _violations(src) == []


@pytest.mark.parametrize("src", [
    # Neither half is a localized table; only the union is, and it lacks zh-TW.
    'T = {"en": "e"} | {"zh": "s"}',
    # A complete offender unioned with an empty dict.
    'T = {"en": "e", "zh": "s"} | {}',
    # Positional base carrying en/zh, merged with an unrelated keyword.
    'T = dict({"en": "e", "zh": "s"}, note="x")',
    'T = dict({"en": "e", "zh": "s"}, **{"note": "x"})',
])
def test_merge_resolving_to_an_offender_is_reported(src):
    """A merge whose *result* lacks zh-TW must be caught.

    Suppressing the operands without resolving the merge let these through
    entirely: both halves were suppressed as fragments, and the enclosing BinOp is
    not a dict node, so nothing got judged at all.
    """
    assert len(_violations(src)) == 1


@pytest.mark.parametrize("src", [
    # One template per locale off a literal locale list — a realistic way to
    # write a localized table, so leaving it unresolved was a real blind spot.
    'T = {loc: f"hi {loc}" for loc in ("en", "zh", "ja")}',
    'T = {loc: 1 for loc in ["en", "zh"]}',
    'T = {k: v for k, v in (("en", "hello"), ("zh", "你好"))}',
])
def test_resolvable_comprehension_is_judged(src):
    """A comprehension over an inline literal has statically known keys."""
    assert len(_violations(src)) == 1


@pytest.mark.parametrize("src", [
    'T = dict([("en", "english"), ("zh", "s")])',
    'T = dict((("en", "e"), ("zh", "s")))',
])
def test_iterable_of_pairs_constructor_is_judged(src):
    """`dict([(k, v), ...])` is as statically known as a literal.

    It reaches resolve_keys as a list, not a mapping, so without a pair-sequence
    path it resolved to None and the table escaped entirely.
    """
    assert len(_violations(src)) == 1


def test_iterable_of_pairs_with_traditional_is_silent():
    assert _violations('T = dict([("en", "e"), ("zh", "s"), ("zh-TW", "t")])') == []


@pytest.mark.parametrize("src", [
    'T = dict(PAIRS)',
    'T = dict([("en", "e"), BAD])',
    'T = dict([("en", "e"), (VAR, "s")])',
    'T = dict([("en", "e", "extra"), ("zh", "s", "extra")])',
])
def test_unresolvable_pair_sequence_is_not_judged(src):
    """Anything but a literal sequence of two-item string-keyed pairs stays unknown."""
    assert _violations(src) == []


@pytest.mark.parametrize("src", [
    'T = {loc: 1 for loc in ("en", "zh", "zh-TW")}',
    'T = {k: v for k, v in (("en", "a"), ("zh", "b"), ("zh-TW", "c"))}',
])
def test_resolvable_comprehension_with_traditional_is_silent(src):
    assert _violations(src) == []


@pytest.mark.parametrize("src", [
    # Following a name to its definition is deliberately out of scope: all three
    # comprehensions under config/prompts today are of this shape, and their keys
    # are a language list rather than templates.
    'T = {lang: build(lang) for lang in _L10N}',
    'T = {k: v for k, v in D.items()}',
    # Filters, extra generators, a computed key, or a key bound to the value slot
    # all make the key set something other than the literals in the iterable.
    'T = {loc: 1 for loc in ("en", "zh") if loc}',
    'T = {loc: 1 for loc in ("en", "zh") for x in y}',
    'T = {loc.lower(): 1 for loc in ("en", "zh")}',
    'T = {v: k for k, v in (("en", "a"), ("zh", "b"))}',
    # The key is a free variable, not the loop target, so the literals in the
    # iterable say nothing about the resulting key set.
    'T = {other: 1 for loc in ("en", "zh")}',
    'T = {other: v for k, v in (("en", "a"), ("zh", "b"))}',
])
def test_unresolvable_comprehension_is_not_judged(src):
    assert _violations(src) == []


def test_comprehension_over_a_non_sequence_iterable_does_not_crash():
    """Restricting the iterable to literal sequences is what keeps .elts safe.

    Without the type check, a name or call in the iterable position would reach
    `gen.iter.elts` and raise AttributeError, taking the whole gate down.
    """
    for src in ('T = {loc: 1 for loc in SOME_NAME}',
                'T = {loc: 1 for loc in get_locales()}',
                'T = {loc: 1 for loc in "enzh"}'):
        assert _violations(src) == []


@pytest.mark.parametrize("src", [
    'T = dict(BASE, en="e", zh="s")',
    'T = {**BASE, "en": "e", "zh": "s"}',
    'T = BASE | {"en": "e", "zh": "s"}',
    'T = {"en": "e", "zh": "s", DYNAMIC_KEY: "x"}',
])
def test_unresolvable_mapping_is_not_judged(src):
    """When any part of the key set is unknowable, stay silent.

    The unknown part is exactly where a zh-TW entry could be hiding, and a gate
    that cries wolf gets worked around rather than satisfied.
    """
    assert _violations(src) == []


@pytest.mark.parametrize("src", [
    'P = {**COMMON, "new": {"en": "hello", "zh": "hi"}}',
    'P = dict(BASE, extra={"en": "a", "zh": "b"})',
    'P = {**A, "k": {"en": "x", "zh": "y"}}',
])
def test_independent_tables_inside_a_merged_container_are_still_judged(src):
    """Suppression must cover merge *operands*, not the container's whole subtree.

    A value keyed normally alongside a spread is an independent table that merely
    sits in a merged container. Pruning the subtree — the first attempt at fixing
    the fragment false-positive — silently stopped checking these entirely.
    """
    assert len(_violations(src)) == 1


def test_merge_fragment_and_independent_table_side_by_side():
    """The container and the independent table are each judged; the fragment isn't.

    The container resolves to `{en, zh, ind}` — a localized table in its own right
    that lacks zh-TW — so line 2 is a real offender, not the fragment on line 3
    being double-counted. Line 4 is the independently keyed table.
    """
    src = '''
    P = {
        **{"en": "f", "zh": "g"},
        "ind": {"en": "x", "zh": "y"},
    }
    '''
    assert _violations(src) == [2, 4]


def test_ordinary_nesting_is_still_judged_in_source_order():
    """Pruning merged constructions must not stop ordinary nesting being checked.

    `{"a": {...}}` is not a merge — the inner dict is a table in its own right.
    Order is asserted because the pruning walk uses a stack, which reverses it.
    """
    src = '''
    OUTER = {
        "a": {"en": "x", "zh": "y"},
        "b": {"en": "p", "zh": "q"},
    }
    '''
    assert _violations(src) == [3, 4]


def test_touched_lines_records_a_location_for_deletion_only_hunks():
    """Removing a table's only zh-TW entry must stay locatable.

    `@@ -4 +3,0 @@` has a zero-length new side, so a plain range() is empty — and
    that transition (a compliant table losing zh-TW) is a real way for the total
    to grow, so having no location would send the author through all 339 entries.
    """
    diff = (
        "diff --git a/p.py b/p.py\n"
        "--- a/p.py\n"
        "+++ b/p.py\n"
        "@@ -4 +3,0 @@\n"
        '-    "zh-TW": "c",\n'
    )
    touched = _touched_from(diff)
    assert touched == {"p.py": {3, 4}}


def test_deletion_at_file_start_does_not_emit_line_zero():
    """`@@ -1 +0,0 @@` must not produce line 0 — line numbers are 1-based."""
    diff = (
        "diff --git a/p.py b/p.py\n"
        "--- a/p.py\n"
        "+++ b/p.py\n"
        "@@ -1 +0,0 @@\n"
        "-x\n"
    )
    assert _touched_from(diff) == {"p.py": {1}}


def test_losing_zh_tw_is_located_end_to_end(monkeypatch):
    """The deletion-hunk location actually names the table that lost zh-TW."""
    diff = (
        "diff --git a/p.py b/p.py\n"
        "--- a/p.py\n"
        "+++ b/p.py\n"
        "@@ -4 +3,0 @@\n"
        '-    "zh-TW": "c",\n'
    )
    head = {"p.py": 'T = {\n    "en": "a",\n    "zh": "b",\n}\n'}
    monkeypatch.setattr(MOD, "_git", lambda *a: diff)
    likely, other = MOD.locate_touched(head, MOD._touched_lines("BASE_SHA"))
    assert likely == ["p.py:1"]
    assert other == 0


def test_touched_lines_disables_git_path_quoting(monkeypatch):
    """Non-ASCII paths must not come back C-quoted in the `+++` header.

    Git's default output is `+++ "b/config/prompts/\\344\\270\\255.py"`, which
    matches no real path and would silently drop that file's location hints.
    """
    seen: list[tuple[str, ...]] = []

    def fake_git(*args: str) -> str:
        seen.append(args)
        return ""

    monkeypatch.setattr(MOD, "_git", fake_git)
    MOD._touched_lines("BASE_SHA")
    assert seen, "no git invocation recorded"
    args = seen[0]
    assert "core.quotePath=false" in args, args
    assert args.index("-c") < args.index("core.quotePath=false")


def test_touched_lines_parses_a_non_ascii_path(monkeypatch):
    """End of the same story: an unquoted CJK path is keyed by its real name."""
    monkeypatch.setattr(MOD, "_git", lambda *a: (
        "diff --git a/config/prompts/中文表.py b/config/prompts/中文表.py\n"
        "--- a/config/prompts/中文表.py\n"
        "+++ b/config/prompts/中文表.py\n"
        "@@ -3,0 +4 @@\n"
        '+    "ja": "c",\n'
    ))
    touched = MOD._touched_lines("BASE_SHA")
    assert touched == {"config/prompts/中文表.py": {4}}


def test_decode_source_honours_a_coding_declaration():
    """A `# coding: latin-1` module is valid Python and must be checked, not skipped."""
    raw = "# coding: latin-1\nT = {'en': 'caf\xe9', 'zh': 'x'}\n".encode("latin-1")
    text = MOD._decode_source(raw, "legacy.py")
    assert text is not None
    assert "café" in text
    assert len(_violations(text)) == 1


def test_decode_source_defaults_to_utf8_without_a_declaration():
    raw = "T = {'en': 'a', 'zh': '简体'}\n".encode("utf-8")
    assert "简体" in (MOD._decode_source(raw, "plain.py") or "")


def test_decode_source_reports_undecodable_bytes(capsys):
    """Only genuinely broken bytes are skipped, and never silently."""
    assert MOD._decode_source(b"# coding: utf-8\nT = '\xff\xfe'\n", "bad.py") is None
    assert "bad.py" in capsys.readouterr().err


def test_prompt_files_at_reads_nul_separated_paths(monkeypatch):
    """`-z` output is NUL-separated and unquoted.

    Without it git quotes non-ASCII paths and octal-escapes their bytes
    (`"config/prompts/\\344\\270\\255.py"`), which would not resolve as a path.
    This repo already carries such paths under tests/testbench.
    """
    captured: list[tuple[str, ...]] = []

    def fake_git(*args: str) -> str:
        captured.append(args)
        return (
            "100644 blob aaa\tconfig/prompts/a.py\0"
            "100644 blob bbb\tconfig/prompts/中文.py\0"
            "100644 blob ccc\tconfig/prompts/notes.txt\0"
        )

    monkeypatch.setattr(MOD, "_git", fake_git)
    files = MOD._prompt_files_at("REV")
    assert files == ["config/prompts/a.py", "config/prompts/中文.py"]
    assert "-z" in captured[0], captured[0]
    # Modes are needed to spot symlinks, so --name-only must not come back.
    assert "--name-only" not in captured[0], captured[0]


def test_prompt_files_at_skips_symlinks(monkeypatch, capsys):
    """Symlinked modules are excluded on the base side.

    `git show` on a symlink yields the link's *target path* as blob content, while
    the disk side would follow the link and scan real source. Counting one and not
    the other makes the two sides disagree about the same path forever, so every
    later PR fails on a difference no PR introduced.
    """
    monkeypatch.setattr(MOD, "_git", lambda *_a: (
        "100644 blob aaa\tconfig/prompts/real.py\0"
        "120000 blob bbb\tconfig/prompts/linked.py\0"
    ))
    files = MOD._prompt_files_at("REV")
    assert files == ["config/prompts/real.py"]
    assert "linked.py" in capsys.readouterr().err


def test_sources_on_disk_skips_symlinks(tmp_path, monkeypatch, capsys):
    """The disk side must skip symlinks too — the dual of the base side."""
    (tmp_path / "real.py").write_text('T = {"en": "a", "zh": "b"}', encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_text('T = {"en": "c", "zh": "d"}', encoding="utf-8")
    link = tmp_path / "linked.py"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    monkeypatch.setattr(MOD, "PROMPTS_DIR", tmp_path)
    monkeypatch.setattr(MOD, "REPO_ROOT", tmp_path)
    sources = MOD._sources_on_disk()
    assert set(sources) == {"real.py"}
    assert "linked.py" in capsys.readouterr().err


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
    """Run the gate as a subprocess, decoding its output as UTF-8.

    ``text=True`` alone decodes with the *locale* encoding, which is cp1252 on
    the Windows CI runner — any non-ASCII byte in the gate's output then raises
    UnicodeDecodeError inside subprocess's reader thread, and the failure surfaces
    as an unrelated-looking assertion on returncode. The gate forces UTF-8 on its
    own streams; this is the matching half.
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=PROJECT_ROOT, capture_output=True, check=False,
        text=True, encoding="utf-8", errors="replace",
    )


def test_cli_against_head_is_clean():
    """--base HEAD compares HEAD to itself, so nothing is newly added."""
    result = _run("--base", "HEAD")
    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_count_reports_a_nonempty_backlog():
    """--count exits 0 and reports a real number.

    The non-zero assertion guards the scanner itself: the issue #2500 backlog is
    large, so a zero means detection silently broke, not that the work finished.
    When the backfill genuinely lands, replace this with the full-scan gate.

    Deliberately one subprocess, not two: --count re-parses every prompt module
    (prompts_proactive.py alone is ~5k lines) and the Windows CI runner shares
    this suite with thread-timing tests on one-second budgets.
    """
    result = _run("--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "missing 'zh-TW'" in result.stdout
    count = int(result.stdout.strip().rsplit(":", 1)[1])
    assert count > 0, "scanner found nothing; detection likely broke"


def test_disk_side_excludes_files_git_does_not_know(tmp_path, monkeypatch):
    """Both sides must draw from the same file set.

    The base side comes from `git ls-tree`, so a gitignored scratch module would
    exist only on the disk side and count as pure growth — failing the gate over
    something no PR introduced.
    """
    (tmp_path / "tracked.py").write_text('T = {"en": "a", "zh": "b"}', encoding="utf-8")
    (tmp_path / "ignored.py").write_text('I = {"en": "c", "zh": "d"}', encoding="utf-8")
    monkeypatch.setattr(MOD, "PROMPTS_DIR", tmp_path)
    monkeypatch.setattr(MOD, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(MOD, "_git_visible_prompt_files", lambda: {"tracked.py"})

    sources = MOD._sources_on_disk()
    assert set(sources) == {"tracked.py"}
    assert MOD.count_offenders(sources) == 1


def test_disk_side_unfiltered_when_git_cannot_answer(tmp_path, monkeypatch):
    """Outside a checkout the scan still works — --count/--full are useful there."""
    (tmp_path / "a.py").write_text('T = {"en": "a", "zh": "b"}', encoding="utf-8")
    monkeypatch.setattr(MOD, "PROMPTS_DIR", tmp_path)
    monkeypatch.setattr(MOD, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(MOD, "_git_visible_prompt_files", lambda: None)
    assert set(MOD._sources_on_disk()) == {"a.py"}


def test_git_visible_prompt_files_asks_for_untracked_but_not_ignored(monkeypatch):
    """A work-in-progress module must stay visible; an ignored one must not.

    `--others --exclude-standard` is what draws that line, so losing either flag
    changes which files the gate checks locally.
    """
    seen: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = b"config/prompts/a.py\0"
        stderr = b""

    def fake_run(cmd, **_kwargs):
        seen.append(cmd)
        return _Result()

    monkeypatch.setattr(MOD.subprocess, "run", fake_run)
    assert MOD._git_visible_prompt_files() == {"config/prompts/a.py"}
    argv = seen[0]
    assert "ls-files" in argv
    assert "--cached" in argv
    assert "--others" in argv
    assert "--exclude-standard" in argv


def test_git_visible_prompt_files_returns_none_on_git_failure(monkeypatch):
    class _Result:
        returncode = 128
        stdout = b""
        stderr = b"not a git repository"

    monkeypatch.setattr(MOD.subprocess, "run", lambda cmd, **kw: _Result())
    assert MOD._git_visible_prompt_files() is None


@pytest.mark.parametrize("src", [
    'T = dict.fromkeys(("en", "zh"), "tpl")',
    'T = dict.fromkeys(["en", "zh"], "tpl")',
])
def test_dict_fromkeys_over_a_literal_is_judged(src):
    """`dict.fromkeys(("en", "zh"), tpl)` — one template across a literal list.

    `node.func` is an Attribute, so the `dict(...)` branch never sees it, and
    there is no child mapping node for the walker to fall back on. The constructor
    itself is already used under config/prompts.
    """
    assert _violations(src) == [1]


def test_dict_fromkeys_with_traditional_is_silent():
    assert _violations('T = dict.fromkeys(("en", "zh", "zh-TW"), "t")') == []


@pytest.mark.parametrize("src", [
    'T = dict.fromkeys(LOCALES, "t")',
    'T = dict.fromkeys()',
    'T = other.fromkeys(("en", "zh"), "t")',
])
def test_dict_fromkeys_unresolvable_is_not_judged(src):
    assert _violations(src) == []


@pytest.mark.parametrize("src", [
    'T = {None: "d", "en": "e", "zh": "s"}',
    'T = {1: "d", "en": "e", "zh": "s"}',
    'T = dict([(None, "d"), ("en", "e"), ("zh", "s")])',
])
def test_constant_non_string_key_does_not_abandon_the_table(src):
    """A None sentinel or int key cannot be 'zh-TW', so it hides nothing.

    Abandoning the whole mapping over it let a real offender through; only a
    *non-constant* key justifies giving up, because that one could be anything.
    """
    assert _violations(src) == [1]


def test_constant_non_string_key_alongside_traditional_stays_silent():
    assert _violations('T = {None: "d", "en": "e", "zh": "s", "zh-TW": "t"}') == []


@pytest.mark.parametrize("src", [
    'T = {DYNAMIC: "d", "en": "e", "zh": "s"}',
    'T = dict([(VAR, "d"), ("en", "e"), ("zh", "s")])',
])
def test_non_constant_key_still_abandons_the_table(src):
    """The unknown key could be 'zh-TW' itself, so stay silent."""
    assert _violations(src) == []


@pytest.mark.parametrize("src", [
    'T = dict.fromkeys(("en", "zh", MAYBE_ZH_TW), "t")',
    'T = {loc: 1 for loc in ("en", "zh", MAYBE_ZH_TW)}',
])
def test_non_constant_element_in_a_key_sequence_abandons_the_table(src):
    """One unknown element makes the whole key set unknown.

    Skipping it instead would resolve to {en, zh} and report an offender, when the
    unknown element may well be the 'zh-TW' entry that makes the table compliant —
    a false positive on code that is actually fine.
    """
    assert _violations(src) == []


def test_git_visible_prompt_files_returns_none_when_git_is_missing(monkeypatch):
    """git absent from PATH raises rather than returning nonzero.

    A missing git is one of the two cases this fallback exists for, so letting
    FileNotFoundError escape would crash --count/--full in exactly the environment
    the fallback was written for.
    """
    def boom(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(MOD.subprocess, "run", boom)
    assert MOD._git_visible_prompt_files() is None


def test_cli_description_matches_the_implemented_ratchet():
    """--help must not advertise a judgement the gate no longer uses.

    The ratchet went through signature-based and scheme-based forms before landing
    on a plain offender count; stale help text sends a reader looking for logic
    that is not there.
    """
    result = _run("--help")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "signature ratchet" not in result.stdout
    assert "offender-count ratchet" in result.stdout


def test_failure_message_mentions_both_noqa_positions():
    """The message must describe the suppression the gate actually accepts.

    Suppression works on the opening *or* closing line; telling authors only about
    the opening line makes a correct `}  # noqa: PROMPT_ZH_TW` look like a mistake.
    """
    source = pathlib.Path(SCRIPT_PATH).read_text(encoding="utf-8")
    assert "opening or closing line if it genuinely does not need one" in source


@pytest.mark.parametrize("src", [
    'T = {"zh-TW": "t"}\nT.update({"en": "e", "zh": "s"})',
    'T = {"zh-TW": "t"}\nT |= {"en": "e", "zh": "s"}',
    'OTHER.update({"en": "e", "zh": "s"})',
])
def test_mutation_payload_is_a_fragment(src):
    """An `update()` / `|=` payload alone says nothing about the assembled table.

    Whether the result has zh-TW depends on the target, which the payload
    expression does not show — so judging it standalone reports tables that are
    compliant at runtime.
    """
    assert _violations(src) == []


@pytest.mark.parametrize("src", [
    'T = {"en": "e", "zh": "s"}\nT.update({"zh-TW": "t"})',
    'T = {"en": "e", "zh": "s"}\nT["zh-TW"] = "t"',
    'T = {"en": "e", "zh": "s"}\nT |= {"zh-TW": "t"}',
])
def test_table_backfilled_by_a_later_mutation_is_exempt(src):
    """The other direction: a mutation that supplies zh-TW makes the table fine."""
    assert _violations(src) == []


@pytest.mark.parametrize("src", [
    'T = {"en": "e", "zh": "s"}\nT["other"] = "x"',
    'T = {"en": "e", "zh": "s"}\nT.update({"ja": "j"})',
    'T = {"en": "e", "zh": "s"}\nT |= {"ja": "j"}',
])
def test_unrelated_mutation_does_not_exempt_the_table(src):
    """Exemption is keyed on zh-TW actually being supplied.

    Exempting on any mutation would let `T["other"] = x` excuse a real offender —
    trading one rare false positive for a broad blind spot.
    """
    assert _violations(src) == [1]


def test_backfill_exemption_is_per_name():
    """Only the mutated name is exempt; a sibling table is still judged."""
    src = (
        'A = {"en": "e", "zh": "s"}\n'
        'B = {"en": "x", "zh": "y"}\n'
        'A["zh-TW"] = "t"'
    )
    assert MOD.find_violations(ast.parse(src), src.splitlines()) == [2]


@pytest.mark.parametrize("src", [
    '_FRAGMENT = {"en": "e", "zh": "s"}\nT = {**_FRAGMENT, "zh-TW": "t"}',
    '_F = {"en": "e", "zh": "s"}\nT = _F | {"zh-TW": "t"}',
    '_F = {"en": "e", "zh": "s"}\nT = dict(_F, **{"zh-TW": "t"})',
])
def test_named_merge_input_is_a_fragment(src):
    """A name used as a merge operand is a fragment, same as an inline literal.

    The assembled table is compliant, so reporting the named half is a false
    positive — and false positives are what get a gate worked around.
    """
    assert _violations(src) == []


def test_named_fragment_exemption_is_per_name():
    """Only the name actually merged is exempt."""
    src = (
        'A = {"en": "e", "zh": "s"}\n'
        'B = {"en": "x", "zh": "y"}\n'
        'T = {**A, "zh-TW": "t"}'
    )
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [2]


def test_noqa_inside_a_string_literal_is_not_a_directive():
    """Template text that happens to read like a directive must not suppress.

    A multiline value whose first line ends with `# noqa: PROMPT_ZH_TW` sits on
    the dict's opening line, so a raw-line scan exempted the table it belongs to.
    """
    src = 'T = {"en": """# noqa: PROMPT_ZH_TW\nmore text""", "zh": "s"}'
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


def test_real_comment_still_suppresses_after_the_token_switch():
    """The token-based lookup must not break ordinary suppression."""
    for src in (
        'T = {"en": "e", "zh": "s"}  # noqa: PROMPT_ZH_TW',
        'T = {\n    "en": "e",\n    "zh": "s",\n}  # noqa: PROMPT_ZH_TW',
        'T = {  # noqa: PROMPT_ZH_TW\n    "en": "e",\n    "zh": "s",\n}',
    ):
        tree, comments = MOD._parse_source(src, "t.py")
        assert MOD.find_violations(tree, comments) == [], src


def test_comment_lines_align_with_source_lines():
    """The comment list is indexed by line, so lookups cannot drift."""
    src = 'a = 1\nT = {"en": "e", "zh": "s"}  # noqa: PROMPT_ZH_TW\nb = 2  # other'
    comments = MOD._comment_lines(src)
    assert len(comments) == 3
    assert comments[0] == ""
    assert "PROMPT_ZH_TW" in comments[1]
    assert comments[2] == "# other"


def test_comment_lines_on_unparseable_source_suppresses_nothing():
    """A tokenize failure must not hand back stale or wrong suppression."""
    comments = MOD._comment_lines('T = {"en": "e"  # noqa: PROMPT_ZH_TW\n')
    assert all(c == "" for c in comments), comments


def test_exemption_lands_on_the_binding_before_the_mutation():
    """A name reassigned after a backfill must not inherit the exemption.

    The later literal is a real offender; exempting every same-named assignment
    let it through.
    """
    src = (
        'T = {"en": "e", "zh": "s"}\n'
        'T["zh-TW"] = "t"\n'
        'T = {"en": "e2", "zh": "s2"}'
    )
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [3]


def test_exemption_does_not_reach_a_different_name():
    src = (
        'T = {"en": "e2", "zh": "s2"}\n'
        'T2 = {"en": "e", "zh": "s"}\n'
        'T2["zh-TW"] = "t"'
    )
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


@pytest.mark.parametrize("src", [
    'T = {"en": "e", "zh": "s"}\nT.update([("zh-TW", "t")])',
    'T = {"en": "e", "zh": "s"}\nT.update((("zh-TW", "t"),))',
])
def test_iterable_of_pairs_update_payload_counts_as_backfill(src):
    """`update([("zh-TW", ...)])` supplies zh-TW as knowably as a dict literal.

    resolve_keys says nothing about a list, so without the pair-sequence fallback
    the target went unexempted and its compliant literal was reported.
    """
    assert _violations(src) == []


def test_exemption_picks_the_nearest_preceding_binding_not_the_first():
    """With two bindings before the mutation, only the nearest one is exempt.

    The earlier literal was live until it was reassigned, and it lacks zh-TW, so it
    is a genuine offender. Exempting the first binding instead would clear the
    wrong one and report the compliant table.
    """
    src = (
        'T = {"en": "a", "zh": "b"}\n'
        'T = {"en": "c", "zh": "d"}\n'
        'T["zh-TW"] = "t"'
    )
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


def test_exemption_covers_a_binding_mutated_on_the_same_line():
    """`T = {...}; T["zh-TW"] = "t"` on one line still exempts the table.

    The binding and the mutation share a line number, so the comparison has to be
    inclusive.
    """
    src = 'T = {"en": "e", "zh": "s"}; T["zh-TW"] = "t"'
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == []


@pytest.mark.parametrize("src", [
    'T: dict[str, str] = {"en": "e", "zh": "s"}\nT["zh-TW"] = "t"',
    'T: dict = {"en": "e", "zh": "s"}\nT.update({"zh-TW": "t"})',
    '_F: dict[str, str] = {"en": "e", "zh": "s"}\nT = {**_F, "zh-TW": "t"}',
])
def test_annotated_binding_is_exempt_too(src):
    """An annotated assignment is still a binding.

    Typed prompt constants are ordinary style; collecting only ast.Assign left
    their tables unexempted and reported despite being compliant at runtime.
    """
    assert _violations(src) == []


def test_annotated_binding_without_a_value_is_ignored():
    """A bare `T: dict` declares nothing to exempt and must not crash."""
    src = 'T: dict\nT2 = {"en": "e", "zh": "s"}'
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [2]


def test_annotated_offender_is_still_reported():
    """The exemption is about mutations, not about annotations."""
    assert _violations('T: dict[str, str] = {"en": "e", "zh": "s"}') == [1]


@pytest.mark.parametrize("src", [
    'T = {"en": "e", "zh": "s"}\nU = dict(T)',
    'T = {"en": "e", "zh": "s"}\nU = {**T}',
    'T = {"en": "e", "zh": "s"}\nU = {**T, "ja": "j"}',
])
def test_copying_an_offender_does_not_exempt_it(src):
    """Being merged somewhere is not enough — the merge must supply zh-TW.

    Exempting every named operand meant an offender plus a copy of it counted as
    zero: the original was excused as an "operand" while the copy was unresolvable,
    because resolve_keys does not follow names.
    """
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


@pytest.mark.parametrize("src", [
    '_F = {"en": "e", "zh": "s"}\nT = {**_F, "zh-TW": "t"}',
    '_F = {"en": "e", "zh": "s"}\nT = _F | {"zh-TW": "t"}',
    '_F = {"en": "e", "zh": "s"}\nT = dict(_F, **{"zh-TW": "t"})',
    '_F = {"en": "e", "zh": "s"}\nT = dict(_F, **dict([("zh-TW", "t")]))',
])
def test_named_fragment_exempt_only_when_zh_tw_is_supplied(src):
    assert _violations(src) == []


def test_directly_visible_keys_ignores_unknowable_parts():
    """Unlike resolve_keys, an unknowable operand contributes nothing, not None."""
    import ast as _ast
    visible = MOD._directly_visible_keys(
        _ast.parse('{**UNKNOWN, "zh-TW": "t"}', mode="eval").body
    )
    assert visible == {"zh-TW"}
    assert MOD._directly_visible_keys(
        _ast.parse('dict(UNKNOWN)', mode="eval").body
    ) == set()


def test_zh_tw_supplied_through_a_nested_spread_still_exempts():
    """The supplying key can itself sit inside a spread, not just be a direct key.

    `{**_F, **{"zh-TW": ...}}` completes _F as surely as `{**_F, "zh-TW": ...}`
    does, so the visibility scan has to recurse into spread values.
    """
    assert _violations('_F = {"en": "e", "zh": "s"}\nT = {**_F, **{"zh-TW": "t"}}') == []


def test_directly_visible_keys_recurses_into_spreads():
    import ast as _ast
    visible = MOD._directly_visible_keys(
        _ast.parse('{**OTHER, **{"zh-TW": "t"}}', mode="eval").body
    )
    assert visible == {"zh-TW"}


@pytest.mark.parametrize("src", [
    'T = U = {"en": "e", "zh": "s"}\nT["zh-TW"] = "t"',
    'T = U = {"en": "e", "zh": "s"}\nU["zh-TW"] = "t"',
])
def test_chained_binding_is_exempt_through_either_name(src):
    """`T = U = {...}` names one object; a mutation through either completes it.

    Requiring exactly one target kept chained bindings out of the lookup, so the
    literal was reported despite being compliant at runtime.
    """
    assert _violations(src) == []


def test_chained_binding_without_a_backfill_is_still_reported():
    src = 'T = U = {"en": "e", "zh": "s"}'
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


@pytest.mark.parametrize("src", [
    '_F = {"en": "e", "zh": "s"}\nT = _F | dict.fromkeys(("zh-TW",), "t")',
    '_F = {"en": "e", "zh": "s"}\nT = {**_F, **dict.fromkeys(("zh-TW",), "t")}',
])
def test_fromkeys_counts_as_a_visible_supplier(src):
    """resolve_keys handles dict.fromkeys, so the visibility scan must too.

    Otherwise the union looks like it supplies nothing and the named fragment is
    reported even though the assembled table is compliant.
    """
    assert _violations(src) == []


@pytest.mark.parametrize("src", [
    '_F = {"en": "e", "zh": "s"}\nT = _F | dict.fromkeys(("ja",), "t")',
    '_F = {"en": "e", "zh": "s"}\nT = _F | dict.fromkeys(LOCALES, "t")',
])
def test_fromkeys_without_zh_tw_does_not_exempt(src):
    """Supplying some other locale, or an unresolvable list, exempts nothing."""
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


@pytest.mark.parametrize("src", [
    'T = {"en": "e", "zh": "s"}\nT.update(**{"zh-TW": "t"})',
    'T = {"en": "e", "zh": "s"}\nT.setdefault("zh-TW", "t")',
])
def test_other_explicit_backfill_forms_exempt(src):
    """`update(**{...})` and `setdefault(key, ...)` supply zh-TW just as plainly.

    The detector only accepted `update()` with a positional argument, so both of
    these left the target unexempted and its compliant literal was reported.
    """
    assert _violations(src) == []


@pytest.mark.parametrize("src", [
    'T = {"en": "e", "zh": "s"}\nT.update(**{"ja": "j"})',
    'T = {"en": "e", "zh": "s"}\nT.setdefault("ja", "j")',
])
def test_other_backfill_forms_supplying_something_else_do_not_exempt(src):
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


def test_named_traditional_supplier_exempts_the_named_fragment():
    """Both halves of a merge can be names: `{**_F, **_TW}`.

    Visibility has to follow a name to its preceding binding, or a merge whose
    zh-TW comes from `_TW` looks like it supplies nothing.
    """
    src = (
        '_F = {"en": "e", "zh": "s"}\n'
        '_TW = {"zh-TW": "t"}\n'
        'T = {**_F, **_TW}'
    )
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == []


def test_named_supplier_without_zh_tw_still_reports():
    """Following names only adds visible keys; it must not excuse a plain copy."""
    src = (
        '_F = {"en": "e", "zh": "s"}\n'
        '_X = {"ja": "j"}\n'
        'T = {**_F, **_X}'
    )
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


@pytest.mark.parametrize("src", [
    'T = {"en": "e", "zh": "s"}\nT = T | {"zh-TW": "t"}',
    'T = {"en": "e", "zh": "s"}\nT = {**T, "zh-TW": "t"}',
    'T = {"en": "e", "zh": "s"}\nT = dict(T, **{"zh-TW": "t"})',
])
def test_self_rebinding_exempts_the_previous_binding(src):
    """`T = T | {...}` — the RHS reads the *earlier* binding.

    The new assignment registers on the merge's own line, so an inclusive
    line search picked the merge's own result and left the first literal reported.
    """
    assert _violations(src) == []


@pytest.mark.parametrize("src", [
    'T = {"zh-TW": "t"}\nT.update({"en": "e", "zh": "s"})',
    'T = {"zh-TW": "t"}\nT.update(**{"en": "e", "zh": "s"})',
])
def test_both_update_argument_forms_are_fragments(src):
    """`update({...})` and `update(**{...})` are the same merge.

    The operand lookup required a positional argument, so the keyword-spread
    payload was judged standalone and reported despite the target carrying zh-TW.
    """
    assert _violations(src) == []


def test_update_with_no_arguments_does_not_crash():
    assert _violations("T.update()") == []


@pytest.mark.parametrize("src,expected", [
    ('T = {"en": "e", "zh": "s"}\nT["zh-TW"]: str = "t"', []),
    ('T = {"en": "e", "zh": "s"}\nT["ja"]: str = "j"', [1]),
])
def test_annotated_subscript_backfill(src, expected):
    """`T["zh-TW"]: str = t` is the same mutation with an annotation.

    AnnAssign carries a single `target` instead of a `targets` list, so matching
    the subscript shape needs its own unpacking.
    """
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == expected


def test_lone_cr_module_keeps_comments_on_their_own_lines():
    """A CR-only module must not shift noqa onto a neighbouring table.

    io.StringIO does not treat a lone \r as a newline, so tokenize saw one line
    while the split saw two — the comment landed on index 0, suppressing the table
    that has no noqa and reporting the one that does. Exactly backwards.
    """
    src = 'A = {"en": 1, "zh": 2}\rB = {"en": 1, "zh": 3}  # noqa: PROMPT_ZH_TW'
    tree, comments = MOD._parse_source(src, "t.py")
    assert comments[0] == ""
    assert "PROMPT_ZH_TW" in comments[1]
    assert MOD.find_violations(tree, comments) == [1]


@pytest.mark.parametrize("src", [
    'T = {"en": "e", "zh": "s"}\nT.update({"ja": "j"}, **{"zh-TW": "t"})',
    'T = {"en": "e", "zh": "s"}\nT.update(**{"ja": "j"}, **{"zh-TW": "t"})',
])
def test_every_update_payload_is_inspected(src):
    """`update()` takes a positional mapping and any number of `**` spreads.

    Looking at only the first payload missed zh-TW whenever it arrived in a later
    one, and reported the compliant target's literal.
    """
    assert _violations(src) == []


def test_mixed_update_payloads_without_zh_tw_still_report():
    src = 'T = {"en": "e", "zh": "s"}\nT.update({"ja": "j"}, **{"ko": "k"})'
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


@pytest.mark.parametrize("src", [
    'TW = {"zh-TW": "t"}\nT = {"en": "e", "zh": "s"}\nT.update(TW)',
    'TW = {"zh-TW": "t"}\nT = {"en": "e", "zh": "s"}\nT |= TW',
])
def test_named_mutation_payload_is_resolved(src):
    """A payload can be a name bound to a mapping earlier.

    Merge operands already used the preceding-binding lookup; mutation payloads
    did not, so `T.update(TW)` left T reported. `prompts_emotion.py:573` shows the
    `update(<name>)` shape does occur in this repo.
    """
    assert _violations(src) == []


@pytest.mark.parametrize("src,expected", [
    ('X = {"ja": "j"}\nT = {"en": "e", "zh": "s"}\nT.update(X)', [2]),
    ('T = {"en": "e", "zh": "s"}\nT.update(UNKNOWN)', [1]),
])
def test_named_payload_without_zh_tw_or_unresolvable_still_reports(src, expected):
    """Following the name only helps when it demonstrably supplies zh-TW."""
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == expected


@pytest.mark.parametrize("src", [
    # A copy taken before the backfill: U lacks zh-TW at runtime but is not judged.
    'T = {"en": "e", "zh": "s"}\nU = dict(T)\nT["zh-TW"] = "t"',
    # dict(zip(...)) — every other static constructor resolves, this one does not.
    'T = dict(zip(("en", "zh"), (english, simplified)))',
])
def test_documented_miss_side_blind_spots(src):
    """Pinned so a change here is deliberate, not accidental.

    Both are on the miss side and both are called out in the module docstring:
    ordering the exemption against each use needs statement-level data flow, and
    `dict(zip(...))` has zero occurrences under config/prompts because splitting a
    localized table into parallel sequences makes the template bodies unreadable.
    """
    assert _violations(src) == []


@pytest.mark.parametrize("supplier", [
    '{loc: tpl for loc in ("zh-TW",)}',
    'dict.fromkeys(("zh-TW",), "t")',
    '{"zh-TW": "t"}',
])
def test_visible_keys_covers_every_constructor_resolve_keys_does(supplier):
    """`_directly_visible_keys` must not lag behind `resolve_keys`.

    It answers "does this merge demonstrably supply zh-TW?", so a constructor it
    fails to read makes the union look like it supplies nothing and the fragment
    gets reported. It re-listed constructors by hand and fell behind twice; it now
    delegates, which is what this pins.
    """
    assert _violations(f'F = {{"en": "e", "zh": "s"}}\nT = F | {supplier}') == []


def test_visible_keys_delegation_does_not_blind_the_gate():
    src = 'F = {"en": "e", "zh": "s"}\nT = F | {loc: tpl for loc in ("ja",)}'
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


@pytest.mark.parametrize("src", [
    # payload named, bound to an iterable of pairs rather than a mapping
    'P = [("zh-TW", "t")]\nT = {"en": "e", "zh": "s"}\nT.update(P)',
    # the supplier reached through a second name
    'TW = {"zh-TW": "t"}\nTW2 = dict(TW)\nF = {"en": "e", "zh": "s"}\nT = {**F, **TW2}',
    'TW = {"zh-TW": "t"}\nTW2 = TW | {}\nT = {"en": "e", "zh": "s"}\nT |= TW2',
])
def test_name_resolution_is_uniform_across_paths(src):
    """Mutation payloads, merge operands and aliases share one resolver.

    Each grew its own partial version first — `resolve_keys` only, no
    iterable-of-pairs, no second hop — and every gap read as "no zh-TW here".
    """
    assert _violations(src) == []


@pytest.mark.parametrize("src,expected", [
    ('X = {"ja": "j"}\nX2 = dict(X)\nF = {"en": "e", "zh": "s"}\nT = {**F, **X2}', [3]),
    ('P = [("ja", "j")]\nT = {"en": "e", "zh": "s"}\nT.update(P)', [2]),
])
def test_following_names_only_ever_adds_keys(src, expected):
    """Chasing a name must not become a way to excuse an offender."""
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == expected


@pytest.mark.parametrize("src,expected", [
    ("T = T\nT.update(T)\nU = {'en': 'e', 'zh': 's'}", [3]),
    ("A = B\nB = A\nT = {'en': 'e', 'zh': 's'}\nT.update(A)", [3]),
])
def test_self_and_mutual_bindings_terminate(src, expected):
    """Name hops are recursive, so they need a cycle guard.

    Without one these hang the gate rather than failing it, which in CI reads as
    an infrastructure problem rather than a bug here.
    """
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == expected


def test_conditional_operand_branches_are_fragments():
    """`{**(A if flag else B), "zh-TW": t}` is one compliant table, not two.

    Suppressing only the IfExp left traversal free to reach both branch dicts and
    report each on its own — the count grew by two for a compliant table.
    """
    src = (
        'T = {**({"en": "e", "zh": "s"} if flag else {"en": "e2", "zh": "s2"}),'
        ' "zh-TW": "t"}'
    )
    assert _violations(src) == []


@pytest.mark.parametrize("src", [
    # supplied through a merge operand — in either branch, so neither may be the
    # only one read
    'F = {"en": "e", "zh": "s"}\nT = F | (TW if flag else {"zh-TW": "t"})',
    'F = {"en": "e", "zh": "s"}\nT = F | ({"zh-TW": "t"} if flag else TW)',
    # a conditional nested in a conditional is still just branches — the fragment
    # sits in the inner one, so expanding only the outer level leaves it reported
    'T = {**(X if f else ({"en": "e", "zh": "s"} if g else Y)), "zh-TW": "t"}',
    # supplied through a mutation payload — the same shape on the other path
    'TW = {"zh-TW": "t"}\nT = {"en": "e", "zh": "s"}\nT |= TW if flag else {}',
    'T = {"en": "e", "zh": "s"}\nT.update({"ja": "j"} if flag else {"zh-TW": "t"})',
])
def test_conditional_operand_may_be_the_zh_tw_supplier(src):
    """A conditional supply counts, same call as `if enabled: T["zh-TW"] = t`.

    Both paths that ask "is zh-TW supplied here?" have to agree — supporting it on
    merge operands only would report the mutation form of the same table.
    """
    assert _violations(src) == []


def test_direct_keys_count_even_when_the_rest_of_the_payload_is_unknowable():
    """`TW = {**BASE, "zh-TW": t}` supplies zh-TW whatever BASE holds.

    `_mapping_keys` gives up on the table as a whole (BASE is unresolvable), so
    without folding in the keys it states outright the payload looks empty and the
    compliant target gets reported.
    """
    src = 'TW = {**BASE, "zh-TW": "t"}\nT = {"en": "e", "zh": "s"}\nT.update(TW)'
    assert _violations(src) == []


def test_unknowable_payload_without_direct_zh_tw_still_reports():
    src = 'TW = {**BASE, "ja": "j"}\nT = {"en": "e", "zh": "s"}\nT.update(TW)'
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [2]


def test_independent_table_inside_a_conditional_operand_is_still_judged():
    """Branches are fragments; tables merely *held* by a branch are not.

    The same distinction `_merge_operands` already draws for spreads — otherwise
    expanding branches would prune real offenders out of the walk.
    """
    src = 'T = {**(A if flag else {"new": {"en": "e", "zh": "s"}}), "zh-TW": "t"}'
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


@pytest.mark.parametrize("src", [
    'F = {"en": "e", "zh": "s"}\nT = {**dict(F), "zh-TW": "t"}',
    'F = {"en": "e", "zh": "s"}\nT = {**(dict(F) | {}), "zh-TW": "t"}',
])
def test_named_fragment_is_exempt_through_a_nested_merge(src):
    """`{**dict(_F), "zh-TW": t}` wraps the fragment `{**_F, "zh-TW": t}` names.

    Stopping at the outer Call reported _F for a table that is compliant either way
    it is written.
    """
    assert _violations(src) == []


def test_nested_merge_without_zh_tw_still_reports_the_fragment():
    src = 'F = {"en": "e", "zh": "s"}\nT = {**dict(F), "ja": "j"}'
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


def test_alias_resolves_against_the_binding_it_captured():
    """An alias holds the object bound *then*, not whatever the name means later.

    Carrying the use site's line through every hop read `ALIAS` against a later
    rebinding of its source and exempted a table that really is missing zh-TW —
    a miss, and the one direction this exemption must never produce.
    """
    src = (
        "P = {}\n"
        "ALIAS = P\n"
        'P = {"zh-TW": "t"}\n'
        'T = {"en": "e", "zh": "s"}\n'
        "T.update(ALIAS)"
    )
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [4]


def test_alias_to_a_traditional_binding_still_exempts():
    src = (
        'P = {"zh-TW": "t"}\n'
        "ALIAS = P\n"
        'T = {"en": "e", "zh": "s"}\n'
        "T.update(ALIAS)"
    )
    assert _violations(src) == []


@pytest.mark.parametrize("src,expected", [
    ('T = {k: v for k, v in ((None, d), ("en", e), ("zh", s))}', [1]),
    ('T = {k: v for k, v in ((None, d), ("en", e), ("zh", s), ("zh-TW", t))}', []),
])
def test_pair_comprehension_skips_constant_non_string_keys(src, expected):
    """A sentinel like `(None, default)` cannot be hiding zh-TW.

    Abandoning the whole comprehension over one made the table unknowable and let
    a real offender through — while the dict-literal and iterable-of-pairs paths
    had skipped the same kind of item all along.
    """
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == expected


@pytest.mark.parametrize("src,expected", [
    ('T = dict((loc, build(loc)) for loc in ("en", "zh"))', [1]),
    ('T = dict((loc, build(loc)) for loc in ("en", "zh", "zh-TW"))', []),
    ('T = dict((k, v) for k, v in (("en", e), ("zh", s)))', [1]),
])
def test_generator_constructor_resolves_like_the_comprehension(src, expected):
    """`dict((loc, v) for loc in ...)` is a dict comprehension in other syntax.

    Resolving one spelling and not the other is the kind of near-miss a gate gets
    worked around by, so both go through `_comprehension_keys`.
    """
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == expected


def test_documented_blind_spot_individually_innocent_fragments():
    """Pinned so a change here is deliberate — see the module docstring.

    Judging the result would mean following names from `_table_nodes`, the reverse
    of the union-style resolution used for the supply question: over-approximating
    is safe there and unsafe here. Zero `NAME | NAME` merges exist under
    config/prompts, and the shape is strictly more verbose than one dict.
    """
    src = 'EN = {"en": "e"}\nZH = {"zh": "s"}\nT = EN | ZH'
    assert _violations(src) == []


def test_generator_of_non_pairs_is_not_treated_as_a_mapping():
    """`dict(<3-tuples>)` is a TypeError, not a table with knowable keys.

    Reading the first two slots of a wider tuple would have the gate claim to know
    the keys of something that is not a mapping at all.
    """
    src = 'T = dict((loc, x, y) for loc, x, y in (("en", 1, 2), ("zh", 3, 4)))'
    assert _violations(src) == []


@pytest.mark.parametrize("src", [
    'F = {"en": "e", "zh": "s"}\nF2 = F\nT = {**F2, "zh-TW": "t"}',
    'F = {"en": "e", "zh": "s"}\nF2 = F\nF3 = F2\nT = {**F3, "zh-TW": "t"}',
    'F = {"en": "e", "zh": "s"}\nF2 = dict(F)\nT = {**F2, "zh-TW": "t"}',
])
def test_exemption_follows_aliases_to_the_table_it_lands_on(src):
    """Exempting the binding's own value node exempts the wrong thing.

    For `F2 = F` that value is a bare Name; the literal bound to F is the node
    actually judged, so it stayed subject to the rule and a compliant table still
    grew the count.
    """
    assert _violations(src) == []


def test_alias_chain_is_not_a_back_door_for_exemption():
    """Following names must still only exempt what the merge demonstrably completes."""
    src = 'X = {"ja": "j"}\nX2 = X\nF = {"en": "e", "zh": "s"}\nT = {**F, **X2}'
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [3]


@pytest.mark.parametrize("src", [
    'T = {"en": "e", "zh": "s"}\nT = T | {"zh-TW": "t"}',
    'T = {"en": "e", "zh": "s"}\nT = (T | {"zh-TW": "t"}) if flag else (T | {"zh-TW": "u"})',
    'T = {"en": "e", "zh": "s"}\nT = dict(T | {"zh-TW": "t"})',
])
def test_self_rebinding_is_recognized_through_any_wrapper(src):
    """The registered binding is the whole right-hand side, not the merge inside it.

    Excluding the merge node by identity worked only when it *was* the right-hand
    side; wrap it in anything and each `T` resolved to the assignment being
    evaluated instead of the table it reads.
    """
    assert _violations(src) == []


def test_rebinding_that_does_not_supply_zh_tw_still_reports():
    src = 'T = {"en": "e", "zh": "s"}\nT = T | {"ja": "j"}'
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


@pytest.mark.parametrize("src,expected", [
    # the rebinding runs AFTER the backfill, so the final table really lacks zh-TW
    ('T = {}; T["zh-TW"] = "t"; T = {"en": "e", "zh": "s"}', [1]),
    # …and the same three statements in the compliant order must stay silent
    ('T = {"en": "e", "zh": "s"}; T["zh-TW"] = "t"', []),
    ('T = {}; T = {"en": "e", "zh": "s"}; T["zh-TW"] = "t"', []),
])
def test_bindings_are_ordered_by_column_not_only_by_line(src, expected):
    """Semicolons put several statements on one line, so a line is not an order.

    Searching by line alone picked the *last* binding on the line for a mutation
    that runs before it, exempting a table that really does end up missing zh-TW.
    """
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == expected


@pytest.mark.parametrize("src", [
    'TW = {"zh-TW": "t"}\nTW = dict(TW)\nT = {"en": "e", "zh": "s"}\nT.update(TW)',
    'TW = {"zh-TW": "t"}\nTW = TW | {}\nT = {"en": "e", "zh": "s"}\nT |= TW',
    'TW = {"zh-TW": "t"}\nTW = dict(TW)\nF = {"en": "e", "zh": "s"}\nT = {**F, **TW}',
])
def test_a_supplier_may_rebind_itself_before_being_used(src):
    """The `TW` inside `TW = dict(TW)` reads the binding *before* that one.

    Resolving it against the binding being descended into found nothing, so a
    supplier that had merely been rebound looked empty and its target was reported.
    """
    assert _violations(src) == []


def test_self_rebinding_supplier_that_drops_zh_tw_still_reports():
    src = (
        'TW = {"zh-TW": "t"}\n'
        'TW = {"ja": "j"}\n'
        'T = {"en": "e", "zh": "s"}\n'
        "T.update(TW)"
    )
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [3]


@pytest.mark.parametrize("src", [
    'T = {"en": "e", "zh": "s"}\nT["zh-TW"], marker = "t", True',
    'T = {"en": "e", "zh": "s"}\n(a, (T["zh-TW"], b)) = (1, ("t", 2))',
    'T = {"en": "e", "zh": "s"}\nT["zh-TW"], *rest = "t", 1, 2',
    # the starred slot may itself be the subscript — the key still ends up set
    # (to a list), which is what verifying this against CPython showed
    'T = {"en": "e", "zh": "s"}\n*T["zh-TW"], b = "t", 1',
])
def test_backfill_through_an_unpacking_target(src):
    """`T["zh-TW"], flag = t, True` puts the subscript inside an ast.Tuple.

    Scanning only top-level targets missed the backfill and reported a table that
    is compliant at runtime.
    """
    assert _violations(src) == []


def test_unpacking_target_for_another_key_still_reports():
    src = 'T = {"en": "e", "zh": "s"}\nT["ja"], marker = "j", True'
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


@pytest.mark.parametrize("src", [
    'T, marker = ({"en": "e", "zh": "s"}, True)\nT["zh-TW"] = "t"',
    'a, (T, b) = (1, ({"en": "e", "zh": "s"}, 2))\nT["zh-TW"] = "t"',
])
def test_unpacking_also_binds_a_name_to_a_table(src):
    """`T, flag = ({...}, True)` binds T as much as `T = {...}` does.

    Registering only top-level Name targets left the later backfill with no
    binding to exempt, so the compliant table was still reported.
    """
    assert _violations(src) == []


@pytest.mark.parametrize("src,expected", [
    ('T, marker = ({"en": "e", "zh": "s"}, True)', [1]),
    # not a literal sequence on both sides: the pairing is unknowable, and
    # guessing would exempt whichever table happened to be nearby
    ('T, m = f()\nT["zh-TW"] = "t"\nU = {"en": "e", "zh": "s"}', [3]),
])
def test_unpacking_binding_stays_narrow(src, expected):
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == expected


@pytest.mark.parametrize("src,expected", [
    ('T = dict([loc, build(loc)] for loc in ("en", "zh"))', [1]),
    ('T = dict([loc, build(loc)] for loc in ("en", "zh", "zh-TW"))', []),
])
def test_generator_pairs_may_be_lists(src, expected):
    """`dict` only asks for a two-item iterable, and the pair-sequence path
    already accepted both — so the generator one has to as well."""
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == expected


@pytest.mark.parametrize("src", [
    'TW = {}\nTW["zh-TW"] = "t"\nT = {"en": "e", "zh": "s"}\nT.update(TW)',
    'TW = {}\nTW.update({"zh-TW": "t"})\nT = {"en": "e", "zh": "s"}\nT.update(TW)',
    'TW = {}\nTW.setdefault("zh-TW", "t")\nT = {"en": "e", "zh": "s"}\nT |= TW',
])
def test_a_supplier_may_be_assembled_in_two_steps(src):
    """A name is not only what it was bound to.

    `TW = {}` / `TW["zh-TW"] = t` is an ordinary way to build a table, and reading
    only the literal saw an empty one — so the compliant target got reported.
    """
    assert _violations(src) == []


@pytest.mark.parametrize("src,expected", [
    ('TW = {}\nTW["ja"] = "j"\nT = {"en": "e", "zh": "s"}\nT.update(TW)', [3]),
    # the assembly happens after the use, so it cannot have supplied anything
    ('TW = {}\nT = {"en": "e", "zh": "s"}\nT.update(TW)\nTW["zh-TW"] = "t"', [2]),
])
def test_assembled_supplier_is_read_in_source_order(src, expected):
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == expected


def test_deleting_the_key_revokes_the_backfill_exemption():
    """The exemption claims the literal is compliant by the time it runs.

    A later removal makes that false again, and a deletion creates no mapping node
    of its own, so nothing else would have noticed.
    """
    src = 'T = {"en": "e", "zh": "s"}\nT["zh-TW"] = "t"\ndel T["zh-TW"]'
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [1]


@pytest.mark.parametrize("src", [
    'T = {"en": "e", "zh": "s"}\nT["zh-TW"] = "t"\ndel T["ja"]',
    # the deletion applies to the table bound on line 3, not to the one the
    # backfill completed — the first literal really was compliant while it lived
    'T = {"en": "e", "zh": "s"}\nT["zh-TW"] = "t"\n'
    'T = {"en": "e2", "zh": "s2", "zh-TW": "t2"}\ndel T["zh-TW"]',
])
def test_revocation_is_scoped_to_the_binding_it_applies_to(src):
    assert _violations(src) == []


@pytest.mark.parametrize("src", [
    # names before and after a starred slot pair from their own end, as Python does
    'T, *rest = ({"en": "e", "zh": "s"}, 1, 2)\nT["zh-TW"] = "t"',
    '*rest, T = (1, 2, {"en": "e", "zh": "s"})\nT["zh-TW"] = "t"',
    'a, *rest, T = (1, 2, 3, {"en": "e", "zh": "s"})\nT["zh-TW"] = "t"',
])
def test_starred_unpacking_pairs_from_both_ends(src):
    """Refusing to pair anything with a star present is a false positive.

    These bindings are perfectly ordinary at runtime, so the backfill really does
    complete the table.
    """
    assert _violations(src) == []


@pytest.mark.parametrize("src,expected", [
    # a ValueError at runtime — the gate must not guess past broken code
    ('T, m = ({"en": "e", "zh": "s"},)\nT["zh-TW"] = "t"', [1]),
    ('T, m, x = ({"en": "e", "zh": "s"}, 1)\nT["zh-TW"] = "t"', [1]),
    # the starred name receives a list, never a table
    ('*T, b = ({"en": "e", "zh": "s"}, 1)\nT["zh-TW"] = "t"', [1]),
    # too few values for the slots around the star — also a runtime ValueError
    ('a, *rest, T = ({"en": "e", "zh": "s"},)\nT["zh-TW"] = "t"', [1]),
    # two starred slots parse fine but fail at compile time, so the module could
    # never be imported; the gate still must not invent a pairing for it
    ('*a, T, *b = (1, {"en": "e", "zh": "s"}, 2)\nT["zh-TW"] = "t"', [1]),
])
def test_unknowable_unpacking_binds_nothing(src, expected):
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == expected


def test_mutations_before_the_binding_do_not_count():
    """A rebinding wipes whatever earlier mutations had put there.

    Reading the whole file's mutations for a name would carry a key across a
    rebinding that discarded it, and exempt a target that really is missing zh-TW.
    """
    src = (
        "TW = {}\n"
        'TW["zh-TW"] = "t"\n'
        "TW = {}\n"
        'T = {"en": "e", "zh": "s"}\n'
        "T.update(TW)"
    )
    tree, comments = MOD._parse_source(src, "t.py")
    assert MOD.find_violations(tree, comments) == [4]
