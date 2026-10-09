"""Real proactive orchestration, recommendation owner and publication boundary.

External model/network and unrelated memory persistence are isolated. Candidate
selection, source gates, Phase 2 parsing, delivery guards and queue publication
run through production functions.
"""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.core import LLMSessionManager
from main_logic.proactive_chat import contracts, generation, service
from main_logic.topic.recommendation import registry
from tests.unit.test_proactive_sid_guard import _make_mgr
from tests.unit.test_topic_recommendation_runtime import CAT, setup, turn


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    yield


@pytest.fixture
async def orchestration(tmp_path, monkeypatch):
    owner, sink, analyzer, _ = await setup(tmp_path)
    sink.note_turn(turn())
    await owner.process_pending(CAT)
    monkeypatch.setattr(registry, "get_recommendation_service", lambda: owner)

    # No real HTTP, user preferences, legacy memory roots, or provider client.
    from utils import preferences, internal_http_client
    from memory import anti_repeat, anti_repeat_effects
    monkeypatch.setattr(preferences, "ais_privacy_mode_enabled", AsyncMock(return_value=True))
    response = SimpleNamespace(status_code=200, text="", raise_for_status=lambda: None,
                               json=lambda: {"topics": []})
    monkeypatch.setattr(internal_http_client, "get_internal_http_client",
                        lambda: SimpleNamespace(get=AsyncMock(return_value=response)))
    monkeypatch.setattr(anti_repeat, "get_anti_repeat_corpus", lambda: None)
    monkeypatch.setattr(anti_repeat_effects, "mark_anti_repeat_response_delivered", lambda *a, **kw: None)
    monkeypatch.setattr(service, "_ensure_source_history_loaded", AsyncMock())
    monkeypatch.setattr(service, "_format_recent_proactive_chats", lambda *a: "")
    monkeypatch.setattr(service, "_append_directives_section", lambda text, *a: text)
    monkeypatch.setattr(service, "_advance_mini_game_invite_entry", lambda *a: None)
    monkeypatch.setattr(service, "_mini_game_invite_get_state", lambda *a, **kw: {})
    monkeypatch.setattr(generation, "_proactive_directive_hits", lambda *a: [])
    monkeypatch.setattr(generation, "count_tokens", lambda text: len(text), raising=False)
    monkeypatch.setattr(generation, "leaks_thinking_in_content", lambda model: False, raising=False)
    monkeypatch.setattr(generation, "set_call_type", lambda *a: None, raising=False)

    mgr = _make_mgr()
    mgr.lanlan_name = "Yui"
    mgr.is_active = True
    mgr.session = SimpleNamespace(_conversation_history=[], _is_responding=False)
    mgr.websocket = SimpleNamespace(send_json=AsyncMock())
    mgr.use_tts = False
    mgr.user_language = "en"
    mgr._conversation_render_language = None
    mgr._user_language_explicit = True
    mgr.last_user_activity_time = None
    mgr.last_user_message_time = None
    mgr.proactive_engagement_observation_started_at = time.time() - 100
    mgr.is_goodbye_silent = lambda: False
    mgr._recommendation_character_id = CAT
    mgr._conversation_observer_id = "s1"
    mgr._active_text_request_id = None
    mgr.websocket_lock = None
    mgr._push_focus_thinking = AsyncMock()
    mgr._focus_idle_thinking = lambda: False
    mgr._focus_idle_cooldown = AsyncMock()
    mgr.handle_new_message = AsyncMock()
    mgr.send_lanlan_response = LLMSessionManager.send_lanlan_response.__get__(mgr)
    # Flush metadata is tested separately against the real turn mixin. Avoid
    # coupling this publication test to its unrelated activity plugin lifecycle.
    mgr._flush_ai_turn_text_to_tracker = lambda **kw: None

    fetch = AsyncMock(return_value={})
    monkeypatch.setattr(service, "collect_proactive_sources", fetch)
    recorded = AsyncMock(return_value=contracts.ProactiveChatResult(
        body=contracts._proactive_chat_body(contracts.PROACTIVE_REASON_CHAT_DELIVERED)))
    monkeypatch.setattr(service, "_record_committed_delivery", recorded)
    model_calls = []
    model_started = asyncio.Event()
    model_resume = asyncio.Event()
    model_resume.set()
    chunks = ["[CHAT][REC:R1]How is your painting going?"]

    class Model:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def astream(self, messages):
            model_calls.append(messages)
            model_started.set()
            await model_resume.wait()
            for chunk in chunks:
                yield SimpleNamespace(content=chunk)

    async def make_model(*a, **kw):
        return Model()
    monkeypatch.setattr(generation, "_make_proactive_llm", make_model)
    config = SimpleNamespace(memory_dir=tmp_path,
        aget_core_config=AsyncMock(return_value={}),
        aget_model_api_config=AsyncMock(side_effect=lambda tier, **kw:
            {"model": "test", "api_key": "test", "base_url": "http://invalid"} if tier == "conversation" else {}))

    async def run(modes=None):
        return await service.handle_proactive_chat(
            contracts.ProactiveChatCommand(lanlan_name="Yui", mini_game_invite_enabled=False,
                enabled_modes=["topic_recommendation"] if modes is None else modes,
                enabled_modes_provided=True, language="en"),
            config_manager=config, session_manager=SimpleNamespace(get=lambda name: mgr),
            character_data=("User", "Yui", None, None, None, {"Yui": "You are Yui."}, None, None, None),
            game_route_active_for=lambda name: False, break_config_manager_provider=lambda: config,
            run_mini_game_invite_short_circuit=AsyncMock(return_value=None),
            push_mini_game_invite_options=AsyncMock(), push_mini_game_invite_resolved=AsyncMock())

    yield SimpleNamespace(owner=owner, sink=sink, analyzer=analyzer, mgr=mgr, run=run,
        fetch=fetch, recorded=recorded, calls=model_calls, chunks=chunks,
        started=model_started, resume=model_resume)
    await owner.close()


@pytest.mark.asyncio
async def test_only_candidate_runs_actual_generation_and_captures_visible_publication(orchestration):
    env = orchestration
    result = await env.run()
    assert result.body["action"] == "chat", result.body
    assert len(env.calls) == 1
    assert "the blue painting" in env.calls[0][0].content
    assert env.fetch.await_args.kwargs["enabled_modes"] == []
    visible = [item["data"]["text"] for item in list(env.mgr.sync_message_queue.queue)
               if item["type"] == "json" and "text" in item["data"]]
    assert visible == ["How is your painting going?"]
    assert "[REC:" not in env.mgr.session._conversation_history[0].content
    captures = env.owner._characters[CAT].captures
    assert len(captures) == 1
    assert next(iter(captures.values()))["text"] == visible[0]
    env.recorded.assert_awaited_once()


@pytest.mark.asyncio
async def test_adoption_model_rejection_is_legal_pass_without_publication(orchestration):
    env = orchestration
    env.analyzer.choice_matches = AsyncMock(return_value=False)
    result = await env.run()
    assert result.status_code == 200, result.body
    assert result.body["reason_code"] == contracts.PROACTIVE_REASON_PASS_MODEL_PASS
    assert env.mgr.sync_message_queue.empty()
    assert not env.owner._characters[CAT].captures
    env.recorded.assert_not_awaited()


@pytest.mark.asyncio
async def test_none_choice_without_any_other_source_is_legal_pass(orchestration):
    env = orchestration
    env.chunks[:] = ["[CHAT][REC:NONE]Let us chat about something else."]
    result = await env.run()
    assert result.status_code == 200, result.body
    assert result.body["reason_code"] == contracts.PROACTIVE_REASON_PASS_MODEL_PASS
    assert len(env.calls) == 1
    assert env.mgr.sync_message_queue.empty()
    assert not env.mgr.session._conversation_history
    assert not env.owner._characters[CAT].captures
    env.recorded.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["disable", "new_user", "reset"])
async def test_changed_owner_during_real_model_stream_cannot_publish(orchestration, action):
    env = orchestration
    env.resume.clear()
    task = asyncio.create_task(env.run())
    await asyncio.wait_for(env.started.wait(), 2)
    if action == "disable":
        await env.owner.apply_controls(False, True, 2)
    elif action == "new_user":
        env.sink.note_turn(turn("Do not discuss that painting", turn_id="u2"))
    else:
        status = await env.owner.status(CAT)
        await env.owner.reset(CAT, status["epoch"], "reset-test")
    env.resume.set()
    result = await task
    assert result.status_code == 200, result.body
    assert result.body["action"] == "pass", result.body
    assert env.mgr.sync_message_queue.empty()
    assert not env.owner._characters[CAT].captures
    env.recorded.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_or_explicit_no_sources_do_not_invoke_model(orchestration):
    env = orchestration
    result = await env.run([])
    assert result.body["action"] == "pass"
    await env.owner.apply_controls(False, False, 2)
    result = await env.run()
    assert result.body["action"] == "pass"
    assert not env.calls
    env.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_raw_text_ingress_preempts_before_recommendation_observer_notification(orchestration):
    env = orchestration
    env.resume.clear()
    task = asyncio.create_task(env.run())
    await asyncio.wait_for(env.started.wait(), 2)
    # Execute the real ingress seam; do not update recommendation watermark or
    # SID. This models the subsequent offline handoff still blocked on an await.
    assert env.mgr.note_stream_input_ingress({"input_type": "text", "data": "Please stop"})
    assert env.owner.snapshot(CAT) is not None
    env.resume.set()
    result = await task
    assert result.body["reason_code"] == contracts.PROACTIVE_REASON_DELIVERY_PREEMPTED
    assert env.mgr.sync_message_queue.empty()
    assert not env.owner._characters[CAT].captures
    env.recorded.assert_not_awaited()


@pytest.mark.asyncio
async def test_optout_at_final_focus_await_has_no_text_history_or_receipt(orchestration):
    env = orchestration
    entered, resume = asyncio.Event(), asyncio.Event()
    async def focus(_active):
        entered.set()
        await resume.wait()
    env.mgr._push_focus_thinking = focus
    task = asyncio.create_task(env.run())
    await asyncio.wait_for(entered.wait(), 2)
    await env.owner.apply_controls(False, True, 2)
    resume.set()
    result = await task
    assert result.body["action"] == "pass", result.body
    assert env.mgr.sync_message_queue.empty()
    assert not env.mgr.session._conversation_history
    assert not env.owner._characters[CAT].captures
    env.recorded.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("synthetic", [False, True])
async def test_actual_text_input_emits_stable_identity_only_real_turn_is_evidence(tmp_path, monkeypatch, synthetic):
    from main_logic.conversation_turns import ConversationTurnDispatcher
    from tests.unit.test_core_game_route_memory_contract import (
        _make_callback_media_manager, _make_offline_session_for_callback_media,
    )
    from utils.token_tracker import TokenTracker
    owner, sink, _, _ = await setup(tmp_path)
    try:
        session = _make_offline_session_for_callback_media()
        session.stream_text = AsyncMock()
        mgr = _make_callback_media_manager(session)
        mgr._conversation_observer_id = "s1"
        mgr._turn_dispatcher = ConversationTurnDispatcher("Yui", privacy_check=lambda: False)
        seen = []
        mgr._turn_dispatcher.add_sink(sink)
        mgr._turn_dispatcher.add_sink(SimpleNamespace(note_turn=seen.append))
        mgr._inject_pending_user_directives = AsyncMock()
        mgr._dispatch_mini_game_invite_keyword = AsyncMock()
        monkeypatch.setattr(TokenTracker, "get_instance", lambda: SimpleNamespace(
            note_first_user_message=lambda *a: None, note_user_message=lambda *a: None))
        message = {"input_type": "text", "data": "The palette is blue", "request_id": "typed-1"}
        if synthetic:
            message["source"] = "replay"
        await mgr._process_stream_data_internal(message)
        session.stream_text.assert_awaited_once()
        assert len(seen) == 1
        assert (seen[0].turn_id, seen[0].session_id, seen[0].input_mode) == ("typed-1", "s1", "text")
        assert seen[0].synthetic is synthetic
        assert message["_conversation_input_id"] == "typed-1"
        assert bool(owner._characters[CAT].events) is not synthetic
    finally:
        await owner.close()


@pytest.mark.parametrize("mode", ["text", "voice", None])
def test_actual_ai_flush_requires_offline_text_for_recommendation_identity(mode):
    from main_logic.conversation_turns import ConversationTurnDispatcher
    from main_logic.omni_offline_client import OmniOfflineClient
    mgr = _make_mgr()
    mgr.input_mode = mode
    mgr.session = OmniOfflineClient.__new__(OmniOfflineClient)
    mgr._conversation_observer_id = "s1"
    mgr._turn_dispatcher = ConversationTurnDispatcher("Yui", privacy_check=lambda: False)
    seen = []
    mgr._turn_dispatcher.add_sink(SimpleNamespace(note_turn=seen.append))
    mgr._publish_ai_message_to_plugin_bus = lambda *a, **kw: None
    mgr._current_ai_turn_text = "How is the palette?"
    mgr._current_ai_turn_id = "reply-1"
    mgr._current_ai_turn_started_at = 123.0
    mgr._flush_ai_turn_text_to_tracker()
    assert len(seen) == 1
    assert (seen[0].input_mode, seen[0].session_id, seen[0].turn_id) == (
        ("text", "s1", "reply-1") if mode == "text" else (None, None, None))
    assert mgr._current_ai_turn_text == "" and mgr._current_ai_turn_id == ""
