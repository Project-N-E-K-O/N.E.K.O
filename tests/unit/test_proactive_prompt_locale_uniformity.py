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


def _module_tree(source: str | None = None):
    return ast.parse(source if source is not None else inspect.getsource(P))


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


def _raw_language_lookups(source: str | None = None):
    """Every place a raw language parameter is used directly as a lookup key.

    A parameter stops counting as raw once the function reassigns that same name
    from a normalizer, which is the ``lang = _normalize_prompt_language(lang)``
    shape the module used before this change.
    """
    offenders = []
    for fn in (n for n in ast.walk(_module_tree(source)) if isinstance(n, ast.FunctionDef)):
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

    Calls ``_raw_language_lookups`` itself. The first version re-implemented the
    inner walk inline, so it validated a COPY of the detector rather than the
    detector -- an empty sweep from the real one would still have read green.
    """
    before = "def get_meme_topic_line(lang):\n    return _loc(D, lang)\n"
    assert _raw_language_lookups(before), "detector missed the pre-change shape"

    # 归一化之后必须不再报，否则这条守卫会把修好的代码也判红。
    after = (
        "def get_meme_topic_line(lang):\n"
        "    lang_key = _normalize_prompt_language(lang)\n"
        "    return _loc(D, lang_key)\n"
    )
    assert _raw_language_lookups(after) == []

    # 另外两种查表形状也要认得，否则改写成 .get / [] 就能绕过守卫。
    assert _raw_language_lookups("def f(lang):\n    return D.get(lang, D['en'])\n")
    assert _raw_language_lookups("def f(language):\n    return D[language]\n")


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


# 每个 locale 在 source_instruction 里独有的字面片段。手写而不是从模块里取：
# 这些片段住在 get_proactive_format_sections 的函数体内部（局部 dict），拿不到，
# 而"从被测对象自己推导期望值"恰恰是下面这条测试要避开的东西。
_SOURCE_INSTRUCTION_MARKERS = {
    "zh": "你可以结合",
    "en": "You may combine",
    "ja": "組み合わせて",
    "ko": "결합하여",
    "ru": "комбинировать",
    "es": "Puedes combinar",
    "pt": "Você pode combinar",
}

# 素材名走的是另一张表（_material_labels），必须单独钉：只钉上面那张的话，把
# 素材名表整个换成英文仍然全绿 —— 这是变异跑出来的，不是推理出来的。
_MATERIAL_LABEL_MARKERS = {
    "zh": "屏幕内容",
    "en": "screen content",
    "ja": "画面の内容",
    "ko": "화면 내용",
    "ru": "содержимое экрана",
    "es": "contenido de pantalla",
    "pt": "conteúdo da tela",
}


@pytest.mark.parametrize("lang", CALLER_REACHABLE_LOCALES)
def test_format_sections_select_that_locales_own_text(lang):
    """An independent oracle, not the function compared against itself.

    The first version asserted ``f(lang) == f(_normalize_prompt_language(lang))``.
    Since the sibling test pins the normalizer as the identity on exactly these
    seven codes, that was ``f(x) == f(x)`` -- green even if the function returned
    a constant, or English for everyone. Assert the locale's own wording instead.
    """
    marker = _SOURCE_INSTRUCTION_MARKERS[lang]
    seen = set()
    for flags in itertools.product([False, True], repeat=4):
        kwargs = dict(zip(("has_screen", "has_web", "has_music", "has_meme"), flags))
        source_instruction, output_format_section = P.get_proactive_format_sections(lang=lang, **kwargs)
        assert source_instruction and output_format_section
        if any(flags):
            assert marker in source_instruction, (
                f"{lang} did not select its own source_instruction wording"
            )
        if kwargs["has_screen"]:
            assert _MATERIAL_LABEL_MARKERS[lang] in source_instruction, (
                f"{lang} did not select its own material label"
            )
        seen.add((source_instruction, output_format_section))
    # 16 种素材组合不能全塌成同一段文案，否则 has_* 分支根本没起作用。
    assert len(seen) > 1


def test_every_locale_gets_distinct_format_sections():
    """No locale silently rides another's text -- the failure a self-comparison hides."""
    rendered = {
        lang: P.get_proactive_format_sections(
            has_screen=True, has_web=True, has_music=False, has_meme=False, lang=lang
        )
        for lang in CALLER_REACHABLE_LOCALES
    }
    assert len(set(rendered.values())) == len(rendered), "有 locale 拿到了别人的文案"


def test_the_whole_module_answers_one_script_for_a_traditional_locale():
    """The point of the refactor: no function disagrees with the others.

    C2 flipped ``keep_traditional`` to ``True`` together with every call site, so
    the whole module now answers Traditional for ``zh-TW`` -- and, still, as one
    unit. That uniformity is the invariant, not the script: before the collapse
    ``get_meme_topic_line`` was the single function answering Traditional while the
    rest answered Simplified, which is exactly the mixed-script turn this pins shut.
    """
    meme_line = P.get_meme_topic_line("zh-TW", keyword="", title="T", source="S")
    assert meme_line != P.get_meme_topic_line("zh", keyword="", title="T", source="S")
    assert meme_line == P._loc(P.MEME_TOPIC_NO_KEYWORD, "zh-TW").format(
        title="T", source="S"
    )

    source_instruction, _ = P.get_proactive_format_sections(
        has_screen=True, has_web=False, has_music=False, has_meme=False, lang="zh-TW"
    )
    simplified, _ = P.get_proactive_format_sections(
        has_screen=True, has_web=False, has_music=False, has_meme=False, lang="zh"
    )
    assert source_instruction != simplified
    # 钉住内容确实是繁体那一行，且不是简体、不是英文——单看"两边不等"是空的，
    # "所有人都拿英文"也能让它不等。
    assert "螢幕內容" in source_instruction                          # 繁体写法
    assert _MATERIAL_LABEL_MARKERS["zh"] not in source_instruction  # 屏幕内容
    assert _MATERIAL_LABEL_MARKERS["en"] not in source_instruction  # screen content


@pytest.mark.parametrize(
    "full_locale, expected_key", [("zh-CN", "zh"), ("zh-TW", "zh-TW")]
)
def test_full_locales_resolve_without_falling_through_loc(full_locale, expected_key, capsys):
    """After step 2's caller flip, Simplified arrives as ``zh-CN``, not ``zh``.

    ``_loc`` would answer that with a missing-key warning and a fallback. Going
    through the normalizer first means the key is always one the dicts carry —
    and, since C2, ``zh-TW`` resolves to its own row rather than the Simplified one.
    """
    line = P.get_meme_topic_line(full_locale, keyword="", title="T", source="S")
    assert line == P.get_meme_topic_line(expected_key, keyword="", title="T", source="S")
    assert "Unexpected lang code" not in capsys.readouterr().out
