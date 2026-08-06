# -*- coding: utf-8 -*-
"""Every template lookup in prompts_proactive goes through one normalizer (issue #2500).

The module resolves ~45 template lookups. All but one pair already ran the caller's
language through ``_normalize_prompt_language`` (or one of its two siblings) before
indexing a dict; ``get_meme_topic_line`` handed ``_loc`` the caller's raw value.

That inconsistency is invisible today because the callers pass SHORT codes, which
the normalizer leaves alone. It stops being invisible the moment step 2 flips those
callers to full locales: the raw-value function would answer Traditional while every
normalized one still answered Simplified (``keep_traditional=False``), mixing both
scripts inside a single turn. Folding the last pair onto the shared path is what
makes the later ``keep_traditional`` flip atomic.

The guard below is derived from the module's own AST rather than from a list of
function names — a list would silently stop covering whatever gets added next,
which is how this pair survived in the first place.
"""
from __future__ import annotations

import ast
import inspect
import itertools

import pytest

from config.prompts import prompts_proactive as P

# ``_resolve_proactive_locale(fmt="short")``'s full value range: everything a caller
# can hand these functions today.
CALLER_REACHABLE_LOCALES = ("zh", "en", "ja", "ko", "ru", "es", "pt")

# The names that turn a caller's language into a prompt-dict key in this module.
NORMALIZER_NAMES = frozenset({
    "_normalize_prompt_language",
    "_normalize_startup_greeting_language",
    "normalize_mini_game_invite_locale",
    "normalize_prompt_locale",
})

# Parameter names that carry a caller-supplied, not-yet-normalized language.
RAW_LANGUAGE_PARAMS = frozenset({"lang", "language"})


def _module_tree():
    return ast.parse(inspect.getsource(P))


def _lookup_key_nodes(node):
    """Key expressions of the three lookup shapes this module uses."""
    if isinstance(node, ast.Subscript):
        yield node.slice
    elif isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "get" and node.args:
            yield node.args[0]
        elif isinstance(func, ast.Name) and func.id == "_loc" and len(node.args) >= 2:
            yield node.args[1]


def _raw_language_lookups():
    """Every place a raw language parameter is used directly as a lookup key.

    A parameter stops counting as raw once the function reassigns that same name
    from a normalizer, which is the ``lang = _normalize_prompt_language(lang)``
    shape the module used before this change.
    """
    offenders = []
    for fn in (n for n in ast.walk(_module_tree()) if isinstance(n, ast.FunctionDef)):
        params = {a.arg for a in fn.args.args + fn.args.kwonlyargs} & RAW_LANGUAGE_PARAMS
        if not params:
            continue
        laundered = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func = node.value.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if called in NORMALIZER_NAMES:
                    laundered |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        still_raw = params - laundered
        if not still_raw:
            continue
        for node in ast.walk(fn):
            for key in _lookup_key_nodes(node):
                if isinstance(key, ast.Name) and key.id in still_raw:
                    offenders.append((fn.name, node.lineno, key.id))
    return sorted(offenders)


def test_no_template_lookup_uses_a_raw_language_parameter():
    offenders = _raw_language_lookups()
    assert offenders == [], (
        "these lookups index a prompt dict with the caller's raw language, so they "
        "will disagree with the rest of the module once the callers move to full "
        "locales: " + "; ".join(f"{fn}() L{ln} key={key}" for fn, ln, key in offenders)
    )


def test_the_guard_can_actually_fail():
    """The AST guard is worth nothing if its shapes do not match real code.

    Runs the same detector over a snippet written in the shape the module used
    before this change, and requires it to be flagged.
    """
    tree = ast.parse(
        "def f(lang):\n"
        "    return _loc(D, lang)\n"
    )
    fn = tree.body[0]
    keys = [
        key.id
        for node in ast.walk(fn)
        for key in _lookup_key_nodes(node)
        if isinstance(key, ast.Name)
    ]
    assert "lang" in keys


# ── 行为面：今天零变化，C2 之后才会动 ────────────────────────────────────────


@pytest.mark.parametrize("lang", CALLER_REACHABLE_LOCALES)
@pytest.mark.parametrize("keyword", ["", "   ", "猫咪"])
def test_meme_topic_line_is_unchanged_for_every_locale_callers_can_pass(lang, keyword):
    """Normalizing is a no-op on the short codes, which is why this commit ships alone.

    ``_normalize_prompt_language`` is the identity on all seven, so routing the
    lookup through it cannot move the output.
    """
    assert P._normalize_prompt_language(lang) == lang
    direct = P._loc(P.MEME_TOPIC_NO_KEYWORD if not keyword.strip() else P.MEME_TOPIC_WITH_KEYWORD, lang)
    built = P.get_meme_topic_line(lang, keyword=keyword, title="T", source="S")
    assert built == direct.format(
        **({"keyword": keyword.strip(), "title": "T", "source": "S"} if keyword.strip()
           else {"title": "T", "source": "S"})
    )


@pytest.mark.parametrize("lang", CALLER_REACHABLE_LOCALES)
def test_format_sections_are_unchanged_for_every_locale_callers_can_pass(lang):
    for flags in itertools.product([False, True], repeat=4):
        kwargs = dict(zip(("has_screen", "has_web", "has_music", "has_meme"), flags))
        assert (
            P.get_proactive_format_sections(lang=lang, **kwargs)
            == P.get_proactive_format_sections(lang=P._normalize_prompt_language(lang), **kwargs)
        )


def test_the_whole_module_answers_one_script_for_a_traditional_locale():
    """The point of the refactor: no function disagrees with the others.

    ``keep_traditional`` is still ``False``, so the whole module answers Simplified
    for ``zh-TW`` -- deliberately, and as one unit. Before, ``get_meme_topic_line``
    was the single function answering Traditional, which is exactly the mixed-script
    turn a later flip has to avoid.
    """
    meme_line = P.get_meme_topic_line("zh-TW", keyword="", title="T", source="S")
    assert meme_line == P.get_meme_topic_line("zh", keyword="", title="T", source="S")
    assert meme_line == P._loc(P.MEME_TOPIC_NO_KEYWORD, "zh").format(title="T", source="S")

    source_instruction, _ = P.get_proactive_format_sections(
        has_screen=True, has_web=False, has_music=False, has_meme=False, lang="zh-TW"
    )
    simplified, _ = P.get_proactive_format_sections(
        has_screen=True, has_web=False, has_music=False, has_meme=False, lang="zh"
    )
    assert source_instruction == simplified


@pytest.mark.parametrize("full_locale, short_key", [("zh-CN", "zh"), ("zh-TW", "zh")])
def test_full_locales_resolve_without_falling_through_loc(full_locale, short_key, capsys):
    """After step 2's caller flip, Simplified arrives as ``zh-CN``, not ``zh``.

    ``_loc`` would answer that with a missing-key warning and a fallback. Going
    through the normalizer first means the key is always one the dicts carry.
    """
    line = P.get_meme_topic_line(full_locale, keyword="", title="T", source="S")
    assert line == P.get_meme_topic_line(short_key, keyword="", title="T", source="S")
    assert "Unexpected lang code" not in capsys.readouterr().out
