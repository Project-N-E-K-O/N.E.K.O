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
        last_user_engagement_time=250.0,
        proactive_engagement_observation_started_at=100.0,
    )
    assert _proactive_silence_since(mgr) == 250.0

    mgr.last_user_message_time = None
    assert _proactive_silence_since(mgr) == 250.0

    mgr.last_user_engagement_time = None
    assert _proactive_silence_since(mgr) == 100.0


@pytest.mark.asyncio
async def test_guard_regenerates_then_drops_still_unanswered_repeat(monkeypatch):
    """The third ignored repeat gets one rewrite before a still-repetitive drop."""
    initial_signal = anti_repeat_module.UnansweredProactiveRepeatSignal(
        triggered=True,
        match_count=2,
        considered_count=8,
        best_similarity=0.72,
        repeated_terms=("屏幕", "按钮", "快点"),
    )
    regen_signal = anti_repeat_module.UnansweredProactiveRepeatSignal(
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


@pytest.mark.asyncio
async def test_fresh_music_material_skips_unanswered_text_scoring(monkeypatch):
    """Fresh material keeps its established exemption from every text repeat guard."""
    corpus = MagicMock()
    corpus.score_unanswered_proactive_draft.return_value = (
        anti_repeat_module.UnansweredProactiveRepeatSignal(
            triggered=True,
            match_count=2,
            considered_count=8,
            best_similarity=0.9,
        )
    )
    monkeypatch.setattr(
        anti_repeat_module,
        "get_anti_repeat_corpus",
        lambda: corpus,
    )
    mgr = SimpleNamespace(
        state=_NeverPreemptedState(),
        last_user_message_time=None,
        last_user_engagement_time=None,
        proactive_engagement_observation_started_at=100.0,
        handle_new_message=AsyncMock(),
    )
    make_llm = AsyncMock()

    output = await _guard_phase2_output(
        mgr=mgr,
        proactive_sid="sid",
        lanlan_name="fresh-music-unanswered-repeat-test",
        response_text="这首歌听起来很舒服，快点开来听听吧。",
        full_text="这首歌听起来很舒服，快点开来听听吧。",
        source_tag="MUSIC",
        active_channels=["music"],
        selected_music_link={"title": "Fresh Song", "artist": "Neko"},
        selected_meme_link=None,
        music_content=None,
        meme_content=None,
        is_playing_music=False,
        music_cooldown=False,
        expects_source_tag=True,
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

    corpus.score_unanswered_proactive_draft.assert_not_called()
    corpus.score_draft.assert_not_called()
    make_llm.assert_not_awaited()
    mgr.handle_new_message.assert_not_awaited()
    assert output.result is None
    assert output.is_music_used is True


@pytest.mark.asyncio
async def test_mini_game_button_response_records_user_engagement(monkeypatch):
    """An explicit invite button response resets silence evidence without a message."""
    import importlib

    router_module = importlib.import_module(
        "main_routers.system_router.mini_game_invite"
    )
    request_data = {
        "lanlan_name": "button-engagement-test",
        "choice": "later",
        "session_id": "invite-session",
    }
    monkeypatch.setattr(
        router_module,
        "_read_json_object",
        AsyncMock(return_value=request_data),
    )
    monkeypatch.setattr(
        router_module,
        "_validate_local_mutation_request",
        lambda *_args, **_kwargs: None,
    )
    config_manager = SimpleNamespace(
        aget_character_data=AsyncMock(
            return_value=(None, "fallback", None, None, None, None, None, None, None)
        )
    )
    monkeypatch.setattr(
        router_module,
        "get_config_manager",
        lambda: config_manager,
    )
    monkeypatch.setitem(
        router_module._mini_game_invite_state,
        "button-engagement-test",
        {"pending_session_id": "invite-session"},
    )
    monkeypatch.setattr(
        router_module,
        "_apply_mini_game_invite_choice",
        MagicMock(return_value={"action": "later"}),
    )
    push_resolved = AsyncMock()
    monkeypatch.setattr(
        router_module,
        "_push_mini_game_invite_resolved",
        push_resolved,
    )
    mgr = SimpleNamespace(note_user_engagement=MagicMock())
    manager_registry = SimpleNamespace(
        get=lambda lanlan_name: (
            mgr if lanlan_name == "button-engagement-test" else None
        )
    )
    monkeypatch.setattr(
        router_module,
        "get_session_manager",
        lambda: manager_registry,
    )

    response = await router_module.mini_game_invite_respond(object())

    assert response.status_code == 200
    mgr.note_user_engagement.assert_called_once_with()
    push_resolved.assert_awaited_once()
