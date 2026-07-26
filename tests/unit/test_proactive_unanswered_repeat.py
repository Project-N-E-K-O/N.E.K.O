# -*- coding: utf-8 -*-
"""Integration contract for silence-aware proactive repetition intervention."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import memory.anti_repeat as anti_repeat_module
from config import ANTI_REPEAT_INJECT_TOP_K
from main_logic.proactive_chat.contracts import PROACTIVE_REASON_PASS_DUPLICATE
from main_logic.proactive_chat.generation import (
    _guard_phase2_output,
    _merge_regen_avoid_terms,
    _proactive_silence_since,
)
from utils.llm_client import HumanMessage, SystemMessage


class _NeverPreemptedState:
    @staticmethod
    def is_proactive_preempted(*_args):
        return False


class _FakeRegenLlm:
    def __init__(self, content: str, on_invoke=None):
        self.content = content
        self.on_invoke = on_invoke

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def ainvoke(self, _messages):
        if self.on_invoke is not None:
            self.on_invoke()
        return SimpleNamespace(content=self.content)


class _FakeStreamingLlm:
    def __init__(self, *chunks: str):
        self.chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def astream(self, _messages):
        for chunk in self.chunks:
            yield SimpleNamespace(content=chunk)


def test_proactive_silence_since_uses_latest_engagement_signal():
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


def test_merge_regen_avoid_terms_preserves_both_repeat_signals():
    """BM25 and long-window shape terms share the bounded rewrite instruction."""
    merged = _merge_regen_avoid_terms(
        ("rare-topic-a", "rare-topic-b", "rare-topic-c", "rare-topic-d"),
        ("screen-shape", "button-shape", "click-shape", "prompt-shape"),
    )
    expected = [
        "rare-topic-a",
        "screen-shape",
        "rare-topic-b",
        "button-shape",
        "rare-topic-c",
        "click-shape",
        "rare-topic-d",
        "prompt-shape",
    ]
    assert merged == expected[:ANTI_REPEAT_INJECT_TOP_K]
    assert len(merged) == min(ANTI_REPEAT_INJECT_TOP_K, len(expected))


@pytest.mark.asyncio
@pytest.mark.parametrize("regen_still_repeats", [False, True])
async def test_break_reminder_applies_unanswered_repeat_regen_before_delivery(
    monkeypatch,
    regen_still_repeats,
):
    from main_logic.proactive_chat import break_reminders

    initial_text = "记得起来喝水休息一下。"
    regenerated_text = "先望望远处，让眼睛放松一会儿吧。"
    make_llm = AsyncMock(
        side_effect=[
            _FakeStreamingLlm(initial_text),
            _FakeRegenLlm(regenerated_text),
        ]
    )
    monkeypatch.setattr(break_reminders, "create_chat_llm_async", make_llm)
    monkeypatch.setattr(
        break_reminders,
        "_record_proactive_chat",
        MagicMock(),
    )

    first_signal = anti_repeat_module.UnansweredProactiveRepeatSignal(
        triggered=True,
        match_count=2,
        considered_count=2,
        best_similarity=0.92,
        repeated_terms=("喝水休息",),
    )
    regen_signal = anti_repeat_module.UnansweredProactiveRepeatSignal(
        triggered=regen_still_repeats,
        match_count=2 if regen_still_repeats else 0,
        considered_count=2,
        best_similarity=0.91 if regen_still_repeats else 0.1,
    )
    corpus = MagicMock()
    corpus.score_unanswered_proactive_draft.side_effect = [
        first_signal,
        regen_signal,
    ]
    monkeypatch.setattr(
        break_reminders,
        "get_anti_repeat_corpus",
        lambda: corpus,
    )

    state = SimpleNamespace(
        fire=AsyncMock(),
        is_proactive_preempted=MagicMock(return_value=False),
    )
    mgr = SimpleNamespace(
        prepare_proactive_delivery=AsyncMock(return_value=True),
        current_speech_id="break-sid",
        state=state,
        feed_tts_chunk=AsyncMock(),
        finish_proactive_delivery=AsyncMock(return_value=True),
        handle_new_message=AsyncMock(),
        proactive_engagement_observation_started_at=100.0,
        last_user_message_time=None,
        last_user_engagement_time=None,
    )
    config_manager = SimpleNamespace(
        aget_model_api_config=AsyncMock(
            return_value={
                "model": "fake-model",
                "base_url": "http://127.0.0.1:9/v1",
                "api_key": "fake-key",
                "provider_type": "openai_compatible",
            }
        )
    )

    result = await break_reminders._deliver_break_reminder_via_llm(
        lanlan_name="Neko",
        mgr=mgr,
        config_manager=config_manager,
        system_prompt="Generate one concise water-break reminder.",
        channel="work_break",
        lang="en",
    )

    assert corpus.score_unanswered_proactive_draft.call_count == 2
    if regen_still_repeats:
        assert result == (None, None)
        mgr.feed_tts_chunk.assert_not_awaited()
        mgr.finish_proactive_delivery.assert_not_awaited()
        mgr.handle_new_message.assert_awaited_once_with()
    else:
        assert result == (regenerated_text, "break-sid")
        mgr.feed_tts_chunk.assert_awaited_once_with(
            regenerated_text,
            expected_speech_id="break-sid",
        )
        mgr.finish_proactive_delivery.assert_awaited_once_with(
            regenerated_text,
            expected_speech_id="break-sid",
        )


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
async def test_unanswered_score_failure_keeps_bm25_guard_active(monkeypatch):
    """A new-signal failure must not disable the established BM25 fallback."""
    corpus = MagicMock()
    corpus.score_unanswered_proactive_draft.side_effect = RuntimeError(
        "synthetic unanswered scorer failure"
    )
    corpus.score_draft.side_effect = [
        (100.0, {"legacy-bm25-topic": 100.0}),
        (0.0, {}),
    ]
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
        return _FakeRegenLlm("这是改写后的全新主动话题。")

    output = await _guard_phase2_output(
        mgr=mgr,
        proactive_sid="sid",
        lanlan_name="unanswered-failure-bm25-test",
        response_text="这是触发既有 BM25 防线的主动话题。",
        full_text="这是触发既有 BM25 防线的主动话题。",
        source_tag="CHAT",
        active_channels=[],
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
    assert corpus.score_draft.call_count == 2
    mgr.handle_new_message.assert_not_awaited()
    assert output.result is None
    assert output.response_text == "这是改写后的全新主动话题。"


@pytest.mark.asyncio
async def test_regenerated_fresh_music_recomputes_text_exemption(monkeypatch):
    """A rewrite that selects fresh material skips every textual recheck."""
    corpus = MagicMock()
    corpus.score_unanswered_proactive_draft.return_value = (
        anti_repeat_module.UnansweredProactiveRepeatSignal(
            triggered=True,
            match_count=2,
            considered_count=8,
            best_similarity=0.8,
            repeated_terms=("旧话题",),
        )
    )
    corpus.score_draft.return_value = (0.0, {})
    monkeypatch.setattr(
        anti_repeat_module,
        "get_anti_repeat_corpus",
        lambda: corpus,
    )
    literal_guard = MagicMock(return_value=(False, 0.0))
    monkeypatch.setattr(
        "main_logic.proactive_chat.generation._is_similar_to_recent_proactive_chat",
        literal_guard,
    )

    mgr = SimpleNamespace(
        state=_NeverPreemptedState(),
        last_user_message_time=None,
        last_user_engagement_time=None,
        proactive_engagement_observation_started_at=100.0,
        handle_new_message=AsyncMock(),
    )

    async def make_llm(**_kwargs):
        return _FakeRegenLlm("[MUSIC] 这首新歌很适合现在听。")

    output = await _guard_phase2_output(
        mgr=mgr,
        proactive_sid="sid",
        lanlan_name="regen-fresh-music-exemption-test",
        response_text="先聊一个会触发长窗口改写的旧话题。",
        full_text="先聊一个会触发长窗口改写的旧话题。",
        source_tag="CHAT",
        active_channels=["music", "vision"],
        selected_music_link={"title": "Fresh Regen Song", "artist": "Neko"},
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

    corpus.score_unanswered_proactive_draft.assert_called_once()
    corpus.score_draft.assert_called_once()
    literal_guard.assert_called_once()
    mgr.handle_new_message.assert_not_awaited()
    assert output.result is None
    assert output.source_tag == "MUSIC"
    assert output.is_music_used is True


@pytest.mark.asyncio
async def test_regenerated_score_refreshes_silence_cutoff(monkeypatch):
    """Engagement during the rewrite invalidates the old unanswered window."""
    initial_signal = anti_repeat_module.UnansweredProactiveRepeatSignal(
        triggered=True,
        match_count=2,
        considered_count=8,
        best_similarity=0.8,
        repeated_terms=("旧话题",),
    )
    regenerated_signal = anti_repeat_module.UnansweredProactiveRepeatSignal(
        triggered=False,
        match_count=0,
        considered_count=0,
        best_similarity=0.0,
    )
    corpus = MagicMock()
    corpus.score_unanswered_proactive_draft.side_effect = [
        initial_signal,
        regenerated_signal,
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
        last_user_engagement_time=None,
        proactive_engagement_observation_started_at=100.0,
        handle_new_message=AsyncMock(),
    )

    async def make_llm(**_kwargs):
        return _FakeRegenLlm(
            "这是改写后的全新主动话题。",
            on_invoke=lambda: setattr(mgr, "last_user_engagement_time", 250.0),
        )

    output = await _guard_phase2_output(
        mgr=mgr,
        proactive_sid="sid",
        lanlan_name="regen-silence-refresh-test",
        response_text="这是用户一直没有回应的旧主动话题。",
        full_text="这是用户一直没有回应的旧主动话题。",
        source_tag="CHAT",
        active_channels=[],
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

    silence_cutoffs = [
        call.kwargs["silence_since"]
        for call in corpus.score_unanswered_proactive_draft.call_args_list
    ]
    assert silence_cutoffs == [100.0, 250.0]
    mgr.handle_new_message.assert_not_awaited()
    assert output.result is None


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pending_session_id", "choice_result"),
    [
        ("newer-invite-session", {"action": "later"}),
        ("invite-session", {"action": "ignored", "reason": "no pending invite"}),
    ],
)
async def test_rejected_mini_game_button_response_records_user_engagement(
    monkeypatch,
    pending_session_id,
    choice_result,
):
    """A valid stale or ignored button click still resets silence evidence."""
    import importlib

    router_module = importlib.import_module(
        "main_routers.system_router.mini_game_invite"
    )
    monkeypatch.setattr(
        router_module,
        "_read_json_object",
        AsyncMock(
            return_value={
                "lanlan_name": "button-engagement-test",
                "choice": "later",
                "session_id": "invite-session",
            }
        ),
    )
    monkeypatch.setattr(
        router_module,
        "_validate_local_mutation_request",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        router_module,
        "get_config_manager",
        lambda: SimpleNamespace(
            aget_character_data=AsyncMock(
                return_value=(
                    None,
                    "fallback",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            )
        ),
    )
    monkeypatch.setitem(
        router_module._mini_game_invite_state,
        "button-engagement-test",
        {"pending_session_id": pending_session_id},
    )
    apply_choice = MagicMock(return_value=choice_result)
    monkeypatch.setattr(
        router_module,
        "_apply_mini_game_invite_choice",
        apply_choice,
    )
    mgr = SimpleNamespace(note_user_engagement=MagicMock())
    monkeypatch.setattr(
        router_module,
        "get_session_manager",
        lambda: SimpleNamespace(get=lambda _name: mgr),
    )

    response = await router_module.mini_game_invite_respond(object())

    assert response.status_code == 200
    mgr.note_user_engagement.assert_called_once_with()
    if pending_session_id == "invite-session":
        apply_choice.assert_called_once()
    else:
        apply_choice.assert_not_called()
