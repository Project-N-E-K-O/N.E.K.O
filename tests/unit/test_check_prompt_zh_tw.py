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


def _grew(base: dict[str, str], head: dict[str, str]) -> int:
    """How many more offenders HEAD has than base — the ratchet decision."""
    base = {k: textwrap.dedent(v) for k, v in base.items()}
    head = {k: textwrap.dedent(v) for k, v in head.items()}
    return MOD.count_offenders(head) - MOD.count_offenders(base)


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
        return "config/prompts/a.py\0config/prompts/中文.py\0config/prompts/notes.txt\0"

    monkeypatch.setattr(MOD, "_git", fake_git)
    files = MOD._prompt_files_at("REV")
    assert files == ["config/prompts/a.py", "config/prompts/中文.py"]
    assert "-z" in captured[0], captured[0]


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
