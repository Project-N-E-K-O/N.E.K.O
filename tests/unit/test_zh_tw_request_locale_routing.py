# -*- coding: utf-8 -*-
"""Traditional Chinese reaches the request-scoped prompt routers (issue #2500 step 2).

Issue #2500 step 1 backfilled a ``zh-TW`` row into every prompt dict. That alone
changes nothing: these three call sites derived their locale through a SHORT code,
which collapses Traditional into Simplified before the template lookup ever happens,
so the new rows stayed unreachable data. This file pins the flipped behaviour.

Each test drives the real resolver and then asserts on the template it selects,
not just on the code it returns: a locale key that no dict is keyed by would pass
a code-only assertion while still handing the user the Simplified template.

The Simplified half of every test is not filler — it is the regression guard.
Widening a resolver to emit ``zh-TW`` is only correct if ``zh-CN`` / ``zh`` still
land on the Simplified row rather than following Traditional through the new branch.
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest

from config.prompts.prompts_card_assist import (
    CARD_ASSIST_CLARIFY_PROMPT,
    get_card_assist_chat_empty_reply_fallback,
    get_card_assist_clarify_prompt,
)
from config.prompts.prompts_galgame import (
    GALGAME_FALLBACK_OPTIONS,
    GALGAME_OPTION_GENERATION_PROMPT,
    get_galgame_fallback_options,
)
from config.prompts.prompts_response import (
    LONG_RESPONSE_TAIL_SUMMARY_PROMPT,
)
from main_logic.omni_offline_client import _streaming
from main_routers import card_assist_router, galgame_router
from utils.language_utils import language_context

# 繁体独有写法，用来证明拿到的确实是 zh-TW 那一行而不是简体行。
TRADITIONAL_TAIL = "我剛剛講的那個計畫，其實還有一些細節沒有講完，你要不要聽聽看？"
SIMPLIFIED_TAIL = "我刚刚讲的那个计划，其实还有一些细节没有讲完，你要不要听听看？"


# ── card-assist：请求体没带 locale 时的全局兜底 ─────────────────────────────
#
# 带 locale 的路径本来就分得清繁简（payload 里就是 'zh-TW'）。塌掉的是兜底那一支：
# 它读的是短码 getter，繁中用户在那里被当成简中。

def test_card_assist_falls_back_to_traditional_when_payload_has_no_locale():
    with language_context("zh-TW"):
        assert card_assist_router._resolve_language(None) == "zh-TW"
        # 角色卡模板文件名同样要跟着走，否则字段 key 还是简中那套。
        assert card_assist_router._resolve_locale_code(None) == "zh-TW"

    prompt = get_card_assist_clarify_prompt("zh-TW")
    assert prompt == CARD_ASSIST_CLARIFY_PROMPT["zh-TW"]
    assert prompt != CARD_ASSIST_CLARIFY_PROMPT["zh"]


def test_card_assist_simplified_fallback_is_unchanged():
    with language_context("zh-CN"):
        assert card_assist_router._resolve_language(None) == "zh"
        assert card_assist_router._resolve_locale_code(None) == "zh-CN"
    with language_context("en"):
        assert card_assist_router._resolve_language(None) == "en"
        assert card_assist_router._resolve_locale_code(None) == "en"


@pytest.mark.parametrize(
    "payload_locale, expected",
    [
        ("zh-TW", "zh-TW"),
        ("zh-Hant", "zh-TW"),
        ("zh-HK", "zh-TW"),
        ("zh-CN", "zh"),
        ("zh", "zh"),
        ("en-US", "en"),
    ],
)
def test_card_assist_payload_locale_keeps_the_script(payload_locale, expected):
    # 全局设成英文，确保结果只可能来自 payload 而不是兜底。
    with language_context("en"):
        assert card_assist_router._resolve_language(payload_locale) == expected


def test_card_assist_empty_reply_fallback_has_a_traditional_line():
    """`lang` can now be 'zh-TW', and the fallback line must not fall to English.

    This guards the shape that leaves ``lang == "zh"`` in place: a Traditional
    user would take the else branch and get English rather than any Chinese.
    """
    traditional = get_card_assist_chat_empty_reply_fallback("zh-TW")
    simplified = get_card_assist_chat_empty_reply_fallback("zh")
    english = get_card_assist_chat_empty_reply_fallback("en")
    assert traditional != simplified
    assert traditional != english
    assert "喵" in traditional

    # 光测 accessor 不够：上面三条在 router 仍写 `lang == "zh"` 时也全绿，因为它们
    # 根本没碰 router。先把 resolver→accessor 那一段链钉住：
    with language_context("zh-TW"):
        lang = card_assist_router._resolve_language(None)
    assert get_card_assist_chat_empty_reply_fallback(lang) == traditional
    with language_context("zh-CN"):
        lang = card_assist_router._resolve_language(None)
    assert get_card_assist_chat_empty_reply_fallback(lang) == simplified


def test_chat_endpoint_picks_the_fallback_through_the_locale_table():
    """The last link: ``chat()`` must select that line via the table, not a ternary.

    STRUCTURAL on purpose, and the limit is worth stating. Driving ``chat()`` end
    to end needs the CSRF guard, a Request, and an LLM stub, and the branch under
    test is one assignment deep inside it. The behavioural half above pins
    resolver -> accessor; this half pins that the router actually reaches for the
    accessor. Verified by mutation: restoring the ``lang == "zh"`` ternary passed
    every behavioural assertion in this file and is caught only here.
    """
    src = (
        pathlib.Path(card_assist_router.__file__).read_text(encoding="utf-8")
    )
    assert "get_card_assist_chat_empty_reply_fallback(lang)" in src, (
        "chat() no longer takes the empty-reply line from the locale table"
    )
    assert 'if lang == "zh"' not in src, (
        "a `lang == \"zh\"` equality test drops 'zh-TW' into the non-Chinese branch; "
        "compare the script family or go through the locale table"
    )


def test_card_assist_action_recovery_prompt_stays_chinese_for_traditional():
    """The recovery prompt is internal machinery, so both scripts sharing the
    Simplified version is deliberate.

    What this guards is "do not fall to English": an ``lang == "zh"`` equality
    check drops 'zh-TW' into the English branch.
    """
    kwargs = dict(
        locale_code="zh-TW",
        user_instruction="把性格改得更活泼一点",
        current_card_text="{}",
        target_keys_text="性格",
        assistant_reply="好呀",
    )
    traditional = card_assist_router._build_action_recovery_prompt(lang="zh-TW", **kwargs)
    simplified = card_assist_router._build_action_recovery_prompt(lang="zh", **kwargs)
    english = card_assist_router._build_action_recovery_prompt(lang="en", **kwargs)
    assert traditional == simplified
    assert traditional != english


# ── galgame：请求语言 + 文本检测 ────────────────────────────────────────────

@pytest.mark.parametrize(
    "request_lang, expected",
    [
        ("zh-TW", "zh-TW"),
        ("tchinese", "zh-TW"),
        ("zh-CN", "zh"),
        ("schinese", "zh"),
        ("ja", "ja"),
        ("en", "en"),
    ],
)
def test_galgame_request_language_keeps_the_script(request_lang, expected):
    with language_context("en"):
        assert galgame_router._resolve_language("", request_lang) == expected


def test_galgame_detection_refines_chinese_with_the_session_locale():
    """``detect_language`` cannot separate the scripts; both report 'zh'.

    Flipping ``format`` to full is therefore not enough on its own -- the
    detection branch could never reach zh-TW. The session's own locale is what
    actually decides.
    """
    with language_context("zh-TW"):
        assert galgame_router._resolve_language(TRADITIONAL_TAIL, None) == "zh-TW"
    with language_context("zh-CN"):
        # 同一段繁体文本，简中会话仍然拿简体模板 —— 检测器本来就分不出，
        # 会话 locale 才是判据。
        assert galgame_router._resolve_language(TRADITIONAL_TAIL, None) == "zh"
    with language_context("zh-TW"):
        # 非中文检测结果不看会话 locale。
        assert galgame_router._resolve_language("hello there my friend", None) == "en"


def test_galgame_traditional_locale_selects_the_traditional_templates():
    with language_context("zh-TW"):
        lang = galgame_router._resolve_language("", "zh-TW")
    assert lang == "zh-TW"
    assert get_galgame_fallback_options(lang) == GALGAME_FALLBACK_OPTIONS["zh-TW"]
    assert get_galgame_fallback_options(lang) != GALGAME_FALLBACK_OPTIONS["zh"]
    assert GALGAME_OPTION_GENERATION_PROMPT[lang] != GALGAME_OPTION_GENERATION_PROMPT["zh"]


# ── 长回复收尾摘要（TTS）：_summarize_tail_for_tts ──────────────────────────


class _FakeResult:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, sink):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def ainvoke(self, messages):
        self._sink.append(messages)
        return _FakeResult("好的。")


class _StreamingStub(_streaming._StreamingMixin):
    """Carries only the one attribute ``_summarize_tail_for_tts`` actually reads."""

    def __init__(self, ui_language):
        self._user_language_provider = (lambda: ui_language) if ui_language else None


class _FakeConfigManager:
    async def aget_model_api_config(self, model_type, *, core_config=None):
        assert model_type == "emotion"
        return {
            "api_key": "k",
            "model": "m",
            "base_url": "http://localhost",
            "provider_type": "openai",
        }


def _run_tail_summary(monkeypatch, *, ui_language, tail):
    """Run the real ``_summarize_tail_for_tts`` and capture the system prompt it built."""
    import utils.config_manager as config_manager_module

    sink: list = []
    monkeypatch.setattr(
        config_manager_module, "get_config_manager", lambda: _FakeConfigManager()
    )

    async def _fake_create(*args, **kwargs):
        return _FakeLLM(sink)

    monkeypatch.setattr(_streaming, "create_chat_llm_async", _fake_create)
    monkeypatch.setattr(_streaming, "set_call_type", lambda *_a, **_kw: None)

    stub = _StreamingStub(ui_language)
    asyncio.run(stub._summarize_tail_for_tts("前面已经播过的部分。", tail))
    assert sink, "LLM 没被调用，说明前置配置分支提前返回了"
    return sink[0][0].content


def test_tail_summary_uses_the_traditional_prompt_for_a_traditional_session(monkeypatch):
    system_text = _run_tail_summary(
        monkeypatch, ui_language="zh-TW", tail=TRADITIONAL_TAIL
    )
    assert system_text == LONG_RESPONSE_TAIL_SUMMARY_PROMPT["zh-TW"]["system"]
    assert system_text != LONG_RESPONSE_TAIL_SUMMARY_PROMPT["zh"]["system"]


def test_tail_summary_keeps_simplified_sessions_on_the_simplified_prompt(monkeypatch):
    system_text = _run_tail_summary(
        monkeypatch, ui_language="zh-CN", tail=SIMPLIFIED_TAIL
    )
    assert system_text == LONG_RESPONSE_TAIL_SUMMARY_PROMPT["zh"]["system"]


def test_tail_summary_without_a_session_locale_follows_the_global_one(monkeypatch):
    """With no ``_user_language_provider``, fall back to the process-wide FULL
    locale rather than the short code."""
    with language_context("zh-TW"):
        system_text = _run_tail_summary(
            monkeypatch, ui_language=None, tail=TRADITIONAL_TAIL
        )
    assert system_text == LONG_RESPONSE_TAIL_SUMMARY_PROMPT["zh-TW"]["system"]
