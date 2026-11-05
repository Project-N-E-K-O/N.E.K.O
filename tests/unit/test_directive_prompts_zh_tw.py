# -*- coding: utf-8 -*-
"""Traditional Chinese users get the zh-TW templates of prompts_directives.

Every prompt table in ``config/prompts/prompts_directives.py`` carries a
``'zh-TW'`` row, yet none of them was ever rendered: ``_norm_lang`` went through
``normalize_language_code(format='short')``, which collapses zh-TW / zh-Hant /
zh-HK / tchinese to ``zh``, and ``_build_initial_prompt`` handed the directive
and topic blocks its own short ``_lang`` on top of that. Traditional users read
Simplified prompts.

Both halves are pinned here: the render functions must resolve Traditional on
their own, and the notify call sites must still be carrying the script when
they call them. Everything that is not Traditional has to come out exactly as
it did before.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import config._runtime as runtime_module
import memory.anti_repeat as anti_repeat_module
import memory.anti_repeat_effects as anti_repeat_effects_module
import memory.user_directives as user_directives_module
from config.prompts import prompts_directives as D
from main_logic.core.notify import NotifyMixin
from utils.language_utils import normalize_language_code

TRADITIONAL_INPUTS = ["zh-TW", "zh-Hant", "zh-HK", "zh_TW", "zh-Hant-TW", "tchinese"]

# (input, template key) for everything that is not Traditional. These are the
# keys the old short-code path produced; the fix must not move any of them.
# One exception: get_negative_target_check_prompt used to skip _norm_lang, so a
# region-tagged ja-JP / ko-KR / pt-BR fell to English there. Its only caller
# passes get_global_language_full(), which never carries those regions.
UNCHANGED_INPUTS = [
    ("zh", "zh"),
    ("zh-CN", "zh"),
    ("zh_CN", "zh"),
    ("zh-Hans", "zh"),
    ("en", "en"),
    ("en-US", "en"),
    ("ja", "ja"),
    ("ja-JP", "ja"),
    ("ko", "ko"),
    ("ko-KR", "ko"),
    ("ru", "ru"),
    ("es", "es"),
    ("pt", "pt"),
    ("pt-BR", "pt"),
    ("fr", "en"),
    ("", "en"),
    (None, "en"),
]

TERMS = ["topic-a", "topic-b"]


def _render_directives(lang):
    return D.render_directives_block(TERMS, lang)


def _expect_directives(key):
    return D.USER_DIRECTIVES_PROMPT_BLOCK[key].format(
        items="- topic-a\n- topic-b", n=2,
    )


def _render_topics(lang):
    return D.render_recent_topics_block(TERMS, lang)


def _expect_topics(key):
    return D.RECENT_TOPIC_HINT_PROMPT_BLOCK[key].format(
        items="- topic-a\n- topic-b", n=2,
    )


def _render_regen(lang):
    return D.render_regen_avoid_instruction(TERMS, lang)


def _expect_regen(key):
    if key in ("zh", "zh-TW", "ja"):
        terms = "「topic-a」、「topic-b」"
    else:
        terms = '"topic-a", "topic-b"'
    return D.PROACTIVE_REGEN_AVOID_INSTRUCTION[key].format(
        terms=terms, master_name=D._DEFAULT_ADDRESSEE[key],
    )


def _render_format_fix(lang):
    return D.render_format_fix_instruction(lang)


def _expect_format_fix(key):
    return D.PROACTIVE_FORMAT_FIX_INSTRUCTION[key].format(
        master_name=D._DEFAULT_ADDRESSEE[key],
    )


def _render_target_check(lang):
    return D.get_negative_target_check_prompt(lang)


def _expect_target_check(key):
    return D.NEGATIVE_TARGET_CHECK_PROMPT[key]


RENDERERS = [
    pytest.param(_render_directives, _expect_directives, id="directives"),
    pytest.param(_render_topics, _expect_topics, id="recent_topics"),
    pytest.param(_render_regen, _expect_regen, id="regen_avoid"),
    pytest.param(_render_format_fix, _expect_format_fix, id="format_fix"),
    pytest.param(_render_target_check, _expect_target_check, id="target_check"),
]


@pytest.fixture(params=["bound", "unbound"])
def language_normalizer(request, monkeypatch):
    """Run each case with and without the app's runtime normalizer.

    The app registers ``utils.language_utils.normalize_language_code`` at
    startup; partial entry points and bare test imports leave it unbound, in
    which case ``_norm_lang`` falls back to splitting the region suffix. Both
    paths collapsed Traditional, so both are covered.
    """
    monkeypatch.setattr(
        runtime_module,
        "_language_normalizer",
        normalize_language_code if request.param == "bound" else None,
    )
    return request.param


@pytest.mark.parametrize("render, expect", RENDERERS)
def test_traditional_and_simplified_templates_differ(render, expect):
    """Guards the tests below against passing vacuously on identical rows."""
    assert expect("zh-TW") != expect("zh")


@pytest.mark.parametrize("lang", TRADITIONAL_INPUTS)
@pytest.mark.parametrize("render, expect", RENDERERS)
def test_traditional_input_renders_the_zh_tw_template(
    language_normalizer, render, expect, lang,
):
    assert render(lang) == expect("zh-TW")


@pytest.mark.parametrize("lang, key", UNCHANGED_INPUTS)
@pytest.mark.parametrize("render, expect", RENDERERS)
def test_other_languages_render_the_same_template_as_before(
    language_normalizer, render, expect, lang, key,
):
    assert render(lang) == expect(key)


@pytest.mark.parametrize("lang", TRADITIONAL_INPUTS)
def test_norm_lang_keeps_traditional(language_normalizer, lang):
    assert D._norm_lang(lang) == "zh-TW"


# ---------------------------------------------------------------------------
# Call sites in main_logic/core/notify.py
# ---------------------------------------------------------------------------


class _Manager(NotifyMixin):
    """Just what ``_build_initial_prompt`` and the mid-session injection touch."""

    def __init__(self, user_language):
        self.lanlan_name = "Neko"
        self.lanlan_prompt = ""
        self.user_language = user_language
        self.session = SimpleNamespace(_conversation_history=[])
        self.append_context = AsyncMock(
            return_value=SimpleNamespace(appended=True, targets=("active_history",))
        )

    def _is_agent_enabled(self):
        return False


def _install_directives(monkeypatch, *, pending=False):
    stub = MagicMock()
    stub.render_prompt_block.side_effect = (
        lambda _key, lang: D.render_directives_block(TERMS, lang)
    )
    stub.get_active_terms.return_value = list(TERMS)
    stub.take_pending_injection.return_value = pending
    monkeypatch.setattr(
        user_directives_module, "get_user_directives_manager", lambda: stub,
    )
    return stub


def _install_topics(monkeypatch):
    corpus = SimpleNamespace(
        apreload=AsyncMock(),
        top_recent_topics=lambda _key: list(TERMS),
    )
    monkeypatch.setattr(anti_repeat_module, "get_anti_repeat_corpus", lambda: corpus)
    monkeypatch.setattr(
        anti_repeat_effects_module, "record_anti_repeat_soft_hint", lambda _key: None,
    )


@pytest.mark.parametrize(
    "user_language, key",
    [("zh-TW", "zh-TW"), ("zh-CN", "zh"), ("en", "en"), ("ja", "ja"), (None, "en")],
)
@pytest.mark.asyncio
async def test_initial_prompt_blocks_follow_the_user_script(
    monkeypatch, user_language, key,
):
    _install_directives(monkeypatch)
    _install_topics(monkeypatch)

    prompt = await _Manager(user_language)._build_initial_prompt()

    assert _expect_directives(key) in prompt
    assert _expect_topics(key) in prompt


@pytest.mark.parametrize(
    "user_language, key", [("zh-TW", "zh-TW"), ("zh-CN", "zh"), ("en", "en")],
)
@pytest.mark.asyncio
async def test_midsession_directive_block_follows_the_user_script(
    monkeypatch, user_language, key,
):
    _install_directives(monkeypatch, pending=True)
    mgr = _Manager(user_language)

    await mgr._inject_pending_user_directives()

    mgr.append_context.assert_awaited_once()
    assert mgr.append_context.await_args.kwargs["text"] == _expect_directives(key).strip()
