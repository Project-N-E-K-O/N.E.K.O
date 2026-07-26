# -*- coding: utf-8 -*-
"""Integration contract for silence-aware proactive repetition intervention."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import memory.anti_repeat as anti_repeat_module
from main_logic.proactive_chat.contracts import PROACTIVE_REASON_PASS_DUPLICATE
from main_logic.proactive_chat.generation import (
    _guard_phase2_output,
    _proactive_silence_since,
)
from memory.anti_repeat import UnansweredProactiveRepeatSignal
from utils.llm_client import HumanMessage, SystemMessage


class _NeverPreemptedState:
    @staticmethod
    def is_proactive_preempted(*_args):
        return False


class _FakeRegenLlm:
    def __init__(self, content: str):
        self.content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def ainvoke(self, _messages):
        return SimpleNamespace(content=self.content)


def test_proactive_silence_since_prefers_last_real_user_message():
    mgr = SimpleNamespace(
        last_user_message_time=200.0,
        proactive_engagement_observation_started_at=100.0,
    )
    assert _proactive_silence_since(mgr) == 200.0

    mgr.last_user_message_time = None
    assert _proactive_silence_since(mgr) == 100.0


@pytest.mark.asyncio
async def test_guard_regenerates_then_drops_still_unanswered_repeat(monkeypatch):
    """The third ignored repeat gets one rewrite before a still-repetitive drop."""
    initial_signal = UnansweredProactiveRepeatSignal(
        triggered=True,
        match_count=2,
        considered_count=8,
        best_similarity=0.72,
        repeated_terms=("屏幕", "按钮", "快点"),
    )
    regen_signal = UnansweredProactiveRepeatSignal(
        triggered=True,
        match_count=2,
        considered_count=8,
        best_similarity=0.68,
        repeated_terms=("屏幕", "按钮"),
    )
    corpus = MagicMock()
    corpus.score_unanswered_proactive_draft.side_effect = [
        initial_signal,
        regen_signal,
    ]
    corpus.score_draft.return_value = (0.0, {})
    monkeypatch.setattr(
        anti_repeat_module,
        "get_anti_repeat_corpus",
        lambda: corpus,
    )

    mgr = SimpleNamespace(
        state=_NeverPreemptedState(),
        last_user_message_time=None,
        proactive_engagement_observation_started_at=100.0,
        handle_new_message=AsyncMock(),
    )
    make_llm_calls = 0

    async def make_llm(**_kwargs):
        nonlocal make_llm_calls
        make_llm_calls += 1
        return _FakeRegenLlm("屏幕上的这个新按钮也很好看，还是快点点一下看看吧。")

    output = await _guard_phase2_output(
        mgr=mgr,
        proactive_sid="sid",
        lanlan_name="unanswered-repeat-test",
        response_text="屏幕上这个小猫按钮好好看啊，快点点一下看看吧。",
        full_text="屏幕上这个小猫按钮好好看啊，快点点一下看看吧。",
        source_tag="CHAT",
        active_channels=["vision"],
        selected_music_link=None,
        selected_meme_link=None,
        music_content=None,
        meme_content=None,
        is_playing_music=False,
        music_cooldown=False,
        expects_source_tag=False,
        make_llm=make_llm,
        messages=[
            SystemMessage(content="system"),
            HumanMessage(content="begin"),
        ],
        human_text="begin",
        screenshot_b64=None,
        phase2_use_vision=False,
        phase2_disable_thinking=True,
        proactive_lang="zh",
        master_name="博士",
    )

    assert make_llm_calls == 1
    assert corpus.score_unanswered_proactive_draft.call_count == 2
    mgr.handle_new_message.assert_awaited_once()
    assert output.result is not None
    assert output.result.body["action"] == "pass"
    assert output.result.body["reason_code"] == PROACTIVE_REASON_PASS_DUPLICATE
    assert output.result.body["unanswered_repeat_matches"] == 2
    assert output.result.body["unanswered_repeat_similarity"] == pytest.approx(0.68)
