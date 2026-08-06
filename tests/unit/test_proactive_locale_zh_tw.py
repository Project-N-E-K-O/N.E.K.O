# -*- coding: utf-8 -*-
"""Traditional-Chinese reachability for the whole proactive family (issue #2500 step 2 C2).

Step 1 backfilled a ``zh-TW`` row into every dictionary in ``prompts_proactive``.
Those rows were unreachable data until now: ``_normalize_prompt_language`` ran with
``keep_traditional=False``, and every caller resolved its locale with
``format="short"``, so Traditional was already gone twice over before a lookup
happened. This file pins the flip and — more importantly — the call sites, because
the flip alone is worthless if a caller still hands over ``zh``.

Three things are asserted, and each is useless without the others:

1. the normalizer keeps the script (``keep_traditional=True``),
2. every proactive entry point hands it a locale that still has one, and
3. **one turn renders one locale** — the invariant that made the flip have to be
   atomic. A later patch that adds a lookup bypassing the normalizer, or "cleans
   up" one call site back to the short code, would leave Traditional users reading
   half-Simplified copy. Test 3 is the one that catches it.

⚠️ There is a fourth assertion that looks redundant and is not: the Simplified
guard. The obvious way to keep the script is ``format="full"``, which yields
``zh-CN`` — and ``zh-CN`` is not a key in *any* of these tables. That mistake keeps
every Traditional test green while quietly degrading the majority Simplified path
to English on the plain ``dict.get`` lookups. See
``test_simplified_never_degrades_to_english``.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import ast
import inspect
import pathlib
from types import SimpleNamespace

import pytest

from config.prompts.prompts_activity import (
    WORK_BREAK_GAME_INVITE_PROMPTS_BY_GAME,
    WORK_BREAK_GENERIC_WORK_LABEL,
)
from config.prompts.prompts_proactive import (
    CAT_GREETING_REASON_AUTO,
    MEME_SECTION_HEADER,
    MUSIC_SEARCH_RESULT_TEXTS,
    NEW_CHARACTER_GREETING_PROMPT,
    PROACTIVE_MUSIC_UNKNOWN_TRACK,
    RECENT_PROACTIVE_CHANNEL_LABELS,
    RECENT_PROACTIVE_TIME_LABELS,
    SCREEN_SECTION_HEADER,
    _normalize_prompt_language,
    get_cat_greeting_reason_hint,
    get_new_character_greeting_prompt,
    get_proactive_music_unknown_track_name,
    get_screen_section_header,
    normalize_proactive_prompt_locale,
)
from config.prompts.prompts_sys import _loc
from main_logic.proactive_chat import service

TRADITIONAL_SPELLINGS = ("zh-TW", "zh-tw", "zh_TW", "zh-Hant", "zh-Hant-TW", "tchinese")
SIMPLIFIED_SPELLINGS = ("zh", "zh-CN", "zh_CN", "zh-Hans", "schinese")
OTHER_LOCALES = ("en", "ja", "ko", "ru", "es", "pt")


def _mgr(user_language):
    return SimpleNamespace(user_language=user_language)


# ── 1. 归一化器留住字形 ──────────────────────────────────────
@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_every_traditional_spelling_survives_the_normalizer(spelling):
    assert _normalize_prompt_language(spelling) == "zh-TW"


@pytest.mark.parametrize("spelling", SIMPLIFIED_SPELLINGS)
def test_simplified_spellings_still_collapse_to_the_simplified_key(spelling):
    """``zh-CN`` must come out ``zh``: it is the *simplified* key of these tables,
    and ``zh-CN`` itself is not a key anywhere in ``config/prompts``."""
    assert _normalize_prompt_language(spelling) == "zh"


@pytest.mark.parametrize("spelling", OTHER_LOCALES)
def test_other_locales_are_untouched_by_the_flip(spelling):
    assert _normalize_prompt_language(spelling) == spelling


@pytest.mark.parametrize("spelling", ("estonian", "undefined", "", None))
def test_garbage_degrades_to_english_not_to_a_crash(spelling):
    assert _normalize_prompt_language(spelling) == "en"


def test_public_normalizer_is_the_same_function():
    """The public face must not drift from the private one — consumers that index
    the tables directly have to land on exactly the getters' key scheme."""
    for spelling in TRADITIONAL_SPELLINGS + SIMPLIFIED_SPELLINGS + OTHER_LOCALES:
        assert normalize_proactive_prompt_locale(spelling) == _normalize_prompt_language(
            spelling
        )


# ── 2. 解析器把字形送到位 ────────────────────────────────────
@pytest.mark.parametrize("source", ("request", "session", "global"))
@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_prompt_format_preserves_traditional_from_every_source(source, spelling, monkeypatch):
    if source == "request":
        data, mgr = {"language": spelling}, _mgr(None)
    elif source == "session":
        data, mgr = {}, _mgr(spelling)
    else:
        data, mgr = {}, _mgr(None)
        monkeypatch.setattr(service, "get_global_language_full", lambda: spelling)

    assert service._resolve_proactive_locale(data, mgr, fmt="prompt") == "zh-TW"


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_short_format_still_collapses_it(spelling):
    """前提守卫：短码方案确实会毁掉字形。这条一旦变绿，上面那些测试就
    不再证明任何东西了——它们会在没修调用点的情况下照样通过。"""  # noqa: DOCSTRING_CJK
    resolved = service._resolve_proactive_locale({}, _mgr(spelling))
    assert resolved == "zh", resolved


@pytest.mark.parametrize("spelling", SIMPLIFIED_SPELLINGS)
def test_prompt_format_yields_the_simplified_key_not_a_full_locale(spelling):
    """⚠️ ``fmt="full"`` 会给出 ``zh-CN``，那不是任何 prompt 表的键。"""  # noqa: DOCSTRING_CJK
    assert service._resolve_proactive_locale({}, _mgr(spelling), fmt="prompt") == "zh"


@pytest.mark.parametrize("spelling", OTHER_LOCALES)
def test_prompt_format_leaves_other_locales_alone(spelling):
    assert service._resolve_proactive_locale({}, _mgr(spelling), fmt="prompt") == spelling


def test_request_language_still_wins_over_the_session():
    resolved = service._resolve_proactive_locale({"language": "ja"}, _mgr("zh-TW"), fmt="prompt")
    assert resolved == "ja"


def test_garbage_request_language_still_falls_through_to_the_session():
    """白名单守卫早于本次改动，必须活下来：否则 localStorage 一坏，
    文案就被短路成英文，而 session 里明明有真值。"""  # noqa: DOCSTRING_CJK
    resolved = service._resolve_proactive_locale(
        {"language": "estonian"}, _mgr("zh-TW"), fmt="prompt"
    )
    assert resolved == "zh-TW", resolved


# ── 3. 四条路径各自拿到 zh-TW 模板 ───────────────────────────
@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_proactive_chat_copy_renders_traditional(spelling):
    """主动搭话文案：屏幕分节 header 走 {master} 占位符展开，是整条
    Phase 2 prompt 里最容易被漏掉字形的一段。"""  # noqa: DOCSTRING_CJK
    lang = service._resolve_proactive_locale({}, _mgr(spelling), fmt="prompt")
    assert get_screen_section_header("博士", lang) == SCREEN_SECTION_HEADER["zh-TW"].format(
        master="博士"
    )


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_break_reminder_copy_renders_traditional(spelling):
    """休息邀请：这两张表在 prompts_activity，用的是裸 ``dict.get``——
    键错了不会报错，只会静默变英文。"""  # noqa: DOCSTRING_CJK
    from main_logic.proactive_chat.break_reminders import _resolve_break_reminder_label

    lang = service._resolve_proactive_locale({}, _mgr(spelling), fmt="prompt")
    assert _resolve_break_reminder_label(None, lang, WORK_BREAK_GENERIC_WORK_LABEL) == (
        WORK_BREAK_GENERIC_WORK_LABEL["zh-TW"]
    )
    for game_type, per_lang in WORK_BREAK_GAME_INVITE_PROMPTS_BY_GAME.items():
        assert per_lang.get(lang) == per_lang["zh-TW"], game_type


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_new_character_greeting_renders_traditional(spelling):
    """破冰问候。"""  # noqa: DOCSTRING_CJK
    assert get_new_character_greeting_prompt(
        normalize_proactive_prompt_locale(spelling)
    ) == NEW_CHARACTER_GREETING_PROMPT["zh-TW"]


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_cat_greeting_renders_traditional(spelling):
    """猫咪问候：与破冰问候同族，同一个 mixin、同样的短码来源。"""  # noqa: DOCSTRING_CJK
    assert get_cat_greeting_reason_hint(
        True, normalize_proactive_prompt_locale(spelling)
    ) == CAT_GREETING_REASON_AUTO["zh-TW"]


# ── 4. 同一轮里所有文案同一个 locale ─────────────────────────
# ⚠️ 这条是 C1 收口的存在理由。上面每条路径单独测都可能是绿的，而用户看到的是
# 一整轮混排。以后谁再加一处绕过归一化器的查表，这里会红。
def _one_turn_renderings(lang: str) -> dict[str, str]:
    """一轮主动搭话里真实会拼进 prompt 的若干片段，覆盖三种查表风格：
    getter / ``_loc`` / 裸 ``dict.get``。"""  # noqa: DOCSTRING_CJK
    return {
        "screen_header": get_screen_section_header("博士", lang),
        "meme_header": _loc(MEME_SECTION_HEADER, lang),
        "music_unknown_track": get_proactive_music_unknown_track_name(lang),
        "music_search_title": MUSIC_SEARCH_RESULT_TEXTS.get(
            lang, MUSIC_SEARCH_RESULT_TEXTS["en"]
        )["title"],
        "recent_time_label": RECENT_PROACTIVE_TIME_LABELS.get(
            lang, RECENT_PROACTIVE_TIME_LABELS["en"]
        )["m"],
        "recent_channel_label": RECENT_PROACTIVE_CHANNEL_LABELS.get(
            lang, RECENT_PROACTIVE_CHANNEL_LABELS["en"]
        )["vision"],
    }


def _expected_for_key(key: str, row: str) -> str:
    return {
        "screen_header": SCREEN_SECTION_HEADER[row].format(master="博士"),
        "meme_header": MEME_SECTION_HEADER[row],
        "music_unknown_track": PROACTIVE_MUSIC_UNKNOWN_TRACK[row],
        "music_search_title": MUSIC_SEARCH_RESULT_TEXTS[row]["title"],
        "recent_time_label": RECENT_PROACTIVE_TIME_LABELS[row]["m"],
        "recent_channel_label": RECENT_PROACTIVE_CHANNEL_LABELS[row]["vision"],
    }[key]


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_one_turn_is_entirely_traditional(spelling):
    lang = service._resolve_proactive_locale({}, _mgr(spelling), fmt="prompt")
    rendered = _one_turn_renderings(lang)
    # 断言取值相等而不是"含某个繁体字"：包含式断言挡不住一半模板回落简体。
    assert rendered == {k: _expected_for_key(k, "zh-TW") for k in rendered}


@pytest.mark.parametrize("spelling", SIMPLIFIED_SPELLINGS)
def test_one_turn_is_entirely_simplified(spelling):
    lang = service._resolve_proactive_locale({}, _mgr(spelling), fmt="prompt")
    rendered = _one_turn_renderings(lang)
    assert rendered == {k: _expected_for_key(k, "zh") for k in rendered}


def test_simplified_never_degrades_to_english():
    """⚠️ 这条专门挡 ``fmt="full"``。

    ``full`` 给出的是 ``zh-CN``，而这些表一个 ``zh-CN`` 键都没有——裸 ``dict.get``
    会直接落到 ``en``。繁中那一侧的测试全绿，简中用户却看到英文。
    """  # noqa: DOCSTRING_CJK
    lang = service._resolve_proactive_locale({}, _mgr("zh-CN"), fmt="prompt")
    english = {k: _expected_for_key(k, "en") for k in _one_turn_renderings(lang)}
    rendered = _one_turn_renderings(lang)
    offenders = [k for k, v in rendered.items() if v == english[k]]
    assert not offenders, f"简中被降级成英文的片段：{offenders}"


def test_full_format_would_have_broken_simplified():
    """前提守卫：证明上一条测的不是空气。``fmt="full"`` 确实会打穿简中。

    如果哪天 ``full`` 也开始返回 ``zh``（或这些表补了 ``zh-CN`` 行），这条会红，
    提醒把上一条的理由重写——而不是让它退化成一条永远为真的断言。
    """  # noqa: DOCSTRING_CJK
    full = service._resolve_proactive_locale({}, _mgr("zh-CN"), fmt="full")
    assert full == "zh-CN"
    assert MUSIC_SEARCH_RESULT_TEXTS.get(full) is None
    assert RECENT_PROACTIVE_TIME_LABELS.get(full) is None


# ── 5. 调用点：自动发现，不是列清单 ──────────────────────────
# ⚠️ 上面全部是"归一化器和表各自没问题"。真正让 zh-TW 到达用户的是调用点：
# 把 ``fmt="prompt"`` 删掉，第 1/3/4 节里很多条还是绿的（表本身没变）。所以这里
# walk 一遍 service.py 的 AST，检查**每一个**调用点都显式给了 fmt。
def _service_tree() -> ast.Module:
    path = pathlib.Path(inspect.getsourcefile(service))
    return ast.parse(path.read_text(encoding="utf-8"))


def _resolver_calls() -> list[ast.Call]:
    return [
        node
        for node in ast.walk(_service_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_resolve_proactive_locale"
    ]


def test_the_call_site_walk_finds_something():
    """空断言陷阱：AST 匹配写错时下面那条会 vacuously 通过。"""  # noqa: DOCSTRING_CJK
    assert len(_resolver_calls()) >= 4


def test_no_call_site_relies_on_the_short_default():
    """短码默认值留着是函数契约的一部分，但没有调用点该再用它——
    proactive 一族的消费点如今全是 prompt dict。"""  # noqa: DOCSTRING_CJK
    offenders = [
        node.lineno
        for node in _resolver_calls()
        if not any(kw.arg == "fmt" for kw in node.keywords)
    ]
    assert not offenders, f"这些调用点还在吃短码默认值：service.py:{offenders}"


GREETING_ENTRYPOINTS = ("trigger_cat_greeting", "trigger_new_character_greeting")


def _greeting_function(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    from main_logic.core import greeting as greeting_mod

    tree = ast.parse(
        pathlib.Path(inspect.getsourcefile(greeting_mod)).read_text(encoding="utf-8")
    )
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"greeting.py 里找不到 {name}——入口改名了，这条守卫已失效")


@pytest.mark.parametrize("name", GREETING_ENTRYPOINTS)
def test_greeting_entrypoints_do_not_shorten_the_locale(name):
    """⚠️ 上面两条问候测试喂的是已归一化的 locale，测的是 getter 不是调用点：
    把 greeting.py 改回 ``format='short'``，那两条照样绿。这里盯调用点。"""  # noqa: DOCSTRING_CJK
    offenders = [
        node.lineno
        for node in ast.walk(_greeting_function(name))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "normalize_language_code"
        and any(
            kw.arg == "format" and getattr(kw.value, "value", None) == "short"
            for kw in node.keywords
        )
    ]
    assert not offenders, f"{name} 还在把 locale 砍成短码：greeting.py:{offenders}"


@pytest.mark.parametrize("name", GREETING_ENTRYPOINTS)
def test_greeting_entrypoints_normalize_through_the_prompt_normalizer(name):
    callees = {
        node.func.id
        for node in ast.walk(_greeting_function(name))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "normalize_proactive_prompt_locale" in callees, (
        f"{name} 没走 prompt key 归一化器，zh-TW 行取不到"
    )


def test_prompt_dict_consumers_ask_for_the_prompt_format():
    """``_break_lang`` / ``proactive_lang`` 是喂 prompt 表的两条主干。"""  # noqa: DOCSTRING_CJK
    wanted = {"_break_lang", "proactive_lang"}
    seen: dict[str, str] = {}
    for node in ast.walk(_service_tree()):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (isinstance(call.func, ast.Name) and call.func.id == "_resolve_proactive_locale"):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in wanted:
                fmt = next((kw.value for kw in call.keywords if kw.arg == "fmt"), None)
                seen[target.id] = getattr(fmt, "value", None)
    assert seen == {name: "prompt" for name in wanted}, seen
