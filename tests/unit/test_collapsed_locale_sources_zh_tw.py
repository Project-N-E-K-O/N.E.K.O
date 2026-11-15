# -*- coding: utf-8 -*-
"""Locale *sources* that had already collapsed before the lookup (issue #2500 step 2).

Three places looked correct on inspection and were not. Each ran a
``format='full'`` normalization — the right call for its consumer — over a source
that was **already** a short code, so Traditional had been destroyed one line
earlier and ``full`` merely re-expanded ``zh`` into ``zh-CN``:

* ``_shared._get_chat_locale_text`` — reads ``static/locales/*.json`` (``zh-CN`` /
  ``zh-TW`` keys), fell back to ``get_global_language()``.
* ``conversation_turns.normalize_turn_language`` — same shape.
* ``callback_render._build_callback_instruction`` — five call sites, all shortening
  to ``zh`` before handing the locale over.

⚠️ These are *not* fixed by changing the ``format`` argument. ``format='full'``
cannot recover a script that is already gone; the fix is at the source. A test
that only pins the ``format`` argument would stay green through the bug, which is
why every case below drives the real function and compares rendered output.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import ast
import inspect
import json
import pathlib

import pytest

from config.prompts.prompts_sys import (
    SYSTEM_NOTIFICATION_TASK_ACTIVE,
    normalize_sys_prompt_locale,
)
from main_logic import conversation_turns
from main_logic.core import _shared
from main_logic.core.callback_render import _build_callback_instruction

TRADITIONAL_SPELLINGS = ("zh-TW", "zh-tw", "zh_TW", "zh-Hant", "zh-Hant-TW", "tchinese")
SIMPLIFIED_SPELLINGS = ("zh", "zh-CN", "zh_CN", "zh-Hans", "schinese")
LOCALES_DIR = pathlib.Path(_shared.__file__).resolve().parents[2] / "static" / "locales"


def _locale_chat_value(locale_code: str, key: str) -> str:
    data = json.loads((LOCALES_DIR / f"{locale_code}.json").read_text(encoding="utf-8"))
    return data["chat"][key]


# -- 1. _get_chat_locale_text fallback source. --
def test_chat_locale_text_precondition_the_two_rows_differ():
    """Vacuous-assertion trap: if both JSON files ever contain identical text, the following assertion is always true."""  # noqa: DOCSTRING_CJK
    assert _locale_chat_value("zh-TW", "title") != _locale_chat_value("zh-CN", "title")


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_chat_locale_text_falls_back_to_the_traditional_global(spelling, monkeypatch):
    monkeypatch.setattr(_shared, "get_global_language_full", lambda: spelling)
    assert _shared._get_chat_locale_text(None, "title", "FB") == _locale_chat_value(
        "zh-TW", "title"
    )


@pytest.mark.parametrize("spelling", SIMPLIFIED_SPELLINGS)
def test_chat_locale_text_still_falls_back_to_simplified(spelling, monkeypatch):
    monkeypatch.setattr(_shared, "get_global_language_full", lambda: spelling)
    assert _shared._get_chat_locale_text(None, "title", "FB") == _locale_chat_value(
        "zh-CN", "title"
    )


def test_chat_locale_text_still_prefers_an_explicit_language(monkeypatch):
    """Changing fallback sources must not change explicit argument precedence."""  # noqa: DOCSTRING_CJK
    monkeypatch.setattr(_shared, "get_global_language_full", lambda: "zh-TW")
    assert _shared._get_chat_locale_text("ja", "title", "FB") == _locale_chat_value(
        "ja", "title"
    )


def test_chat_locale_text_unknown_key_still_returns_the_fallback(monkeypatch):
    monkeypatch.setattr(_shared, "get_global_language_full", lambda: "zh-TW")
    assert _shared._get_chat_locale_text(None, "no_such_key_here", "FB") == "FB"


# -- 2. normalize_turn_language fallback source. --
@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_turn_language_falls_back_to_the_traditional_global(spelling, monkeypatch):
    import utils.language_utils as lu

    monkeypatch.setattr(lu, "get_global_language_full", lambda: spelling)
    assert conversation_turns.normalize_turn_language() == "zh-TW"


@pytest.mark.parametrize("spelling", SIMPLIFIED_SPELLINGS)
def test_turn_language_still_falls_back_to_simplified(spelling, monkeypatch):
    import utils.language_utils as lu

    monkeypatch.setattr(lu, "get_global_language_full", lambda: spelling)
    assert conversation_turns.normalize_turn_language() == "zh-CN"


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_turn_language_still_prefers_an_explicit_language(spelling, monkeypatch):
    import utils.language_utils as lu

    monkeypatch.setattr(lu, "get_global_language_full", lambda: "ja")
    assert conversation_turns.normalize_turn_language(spelling) == "zh-TW"


# -- 3. Callback instructions: renderer normalizes locally. --
CALLBACKS = [
    {
        "origin": "task_result",
        "status": "completed",
        "source_kind": "plugin",
        "source_name": "x",
        "result": "ok",
    }
]


def _rendered(lang: str) -> str:
    return _build_callback_instruction(
        CALLBACKS, lang=lang, lanlan_name="N", master_name="M"
    )


def test_callback_precondition_the_two_rows_differ():
    assert SYSTEM_NOTIFICATION_TASK_ACTIVE["zh-TW"] != SYSTEM_NOTIFICATION_TASK_ACTIVE["zh"]


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_callback_instruction_renders_traditional(spelling):
    """Renderer-local normalization accepts spellings such as ``zh_TW`` / ``zh-Hant`` too."""  # noqa: DOCSTRING_CJK
    assert _rendered(spelling) == _rendered("zh-TW")
    assert _rendered(spelling) != _rendered("zh")


@pytest.mark.parametrize("spelling", SIMPLIFIED_SPELLINGS)
def test_callback_instruction_still_renders_simplified(spelling):
    """``zh-CN`` is included too: ``format='full'`` produces it,
    but prompts_sys has no zh-CN row."""  # noqa: DOCSTRING_CJK
    assert _rendered(spelling) == _rendered("zh")


@pytest.mark.parametrize("spelling", ("en", "ja", "ko", "ru", "es", "pt"))
def test_callback_instruction_leaves_other_locales_alone(spelling):
    assert normalize_sys_prompt_locale(spelling) == spelling
    assert _rendered(spelling) != _rendered("en") or spelling == "en"


# -- 4. Automatically discover call sites rather than enumerating them. --
# Section 3 tests renderers that already normalize locally. Reverting callers to format='short'
# still leaves section 3 green: script information lost at call sites cannot be restored inside renderers. Walk
# both mixins' ASTs, find variables supplied to _build_callback_instruction, and trace their assignments.
CALLBACK_MODULES = ("main_logic.core.proactive", "main_logic.core.lifecycle")


def _callee_name(node: ast.Call) -> str:
    """Return the call name, treating attribute calls and bare names equally.

    Recognizing only ast.Name misses attribute-style calls such as callback_render._build_callback_instruction(...),
    a perfectly valid refactor that would otherwise hide the call site from the guard.
    """  # noqa: DOCSTRING_CJK
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _tree(module_name: str) -> ast.Module:
    import importlib

    mod = importlib.import_module(module_name)
    return ast.parse(pathlib.Path(inspect.getsourcefile(mod)).read_text(encoding="utf-8"))


def _functions_rendering_callbacks(tree: ast.Module) -> list:
    """Functions containing _build_callback_instruction calls.

    Bound discovery by function: _lang is shared across seven or eight unrelated consumers in both modules
    (prompt_ephemeral, context summaries, pending extra replies, etc.);
    module-wide variable-name matching would include paths not yet migrated.
    """  # noqa: DOCSTRING_CJK
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            isinstance(call, ast.Call)
            and _callee_name(call) == "_build_callback_instruction"
            for call in ast.walk(node)
        ):
            out.append(node)
    return out


def _lang_arg_names(tree: ast.Module) -> set[str]:
    """Names of ``lang=`` variables supplied to _build_callback_instruction."""  # noqa: DOCSTRING_CJK
    names = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and _callee_name(node) == "_build_callback_instruction"
        ):
            continue
        for kw in node.keywords:
            if kw.arg == "lang" and isinstance(kw.value, ast.Name):
                names.add(kw.value.id)
    return names


def _short_format_assignments(tree: ast.Module) -> list[int]:
    """Within callback-rendering functions, identify ``lang=`` variables assigned using ``format='short'``."""  # noqa: DOCSTRING_CJK
    offenders = []
    for func in _functions_rendering_callbacks(tree):
        names = _lang_arg_names(func)
        for node in ast.walk(func):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
                continue
            for call in ast.walk(node.value):
                if not (
                    isinstance(call, ast.Call)
                    and _callee_name(call) == "normalize_language_code"
                ):
                    continue
                if any(
                    kw.arg == "format" and getattr(kw.value, "value", None) == "short"
                    for kw in call.keywords
                ):
                    offenders.append(node.lineno)
    return offenders


@pytest.mark.parametrize("module_name", CALLBACK_MODULES)
def test_the_callback_call_site_walk_finds_something(module_name):
    """Vacuous-assertion trap: broken AST matching would make the following assertion pass vacuously."""  # noqa: DOCSTRING_CJK
    tree = _tree(module_name)
    assert _lang_arg_names(tree), f"{module_name} 里没找到 lang= 调用点"
    assert _functions_rendering_callbacks(tree), f"{module_name} 里没定位到渲染函数"


def test_all_five_callback_call_sites_are_accounted_for():
    total = sum(
        len(
            [
                node
                for node in ast.walk(_tree(m))
                if isinstance(node, ast.Call)
                and _callee_name(node) == "_build_callback_instruction"
            ]
        )
        for m in CALLBACK_MODULES
    )
    assert total == 5, f"调用点数量变了（{total}），复核一遍是不是有新路径漏了归一化"


@pytest.mark.parametrize("module_name", CALLBACK_MODULES)
def test_no_callback_call_site_shortens_the_locale(module_name):
    offenders = _short_format_assignments(_tree(module_name))
    assert not offenders, (
        f"{module_name} 这些行还在把 locale 砍成短码，字形到不了渲染函数：{offenders}"
    )
