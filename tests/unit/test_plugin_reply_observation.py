from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import main_logic.core.turn as turn_module
from main_logic import core
from main_logic.core.turn import TurnMixin
from main_logic.omni_realtime_client import OmniRealtimeClient, _gemini_support
from main_logic.tool_calling import ToolResult
from tests.unit.test_core_game_route_memory_contract import _make_manager
from tests.unit.test_offline_late_completion_turn_ownership import (
    _client, _core_manager, _text, _wire, _observe, _make_callback_media_manager,
)
from tests.unit.test_realtime_tool_ownership import _GeminiSession

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def _gemini_sdk(monkeypatch):
    """Gemini dispatch runs against the real genai types, without telemetry."""
    from google.genai import types
    from utils.token_tracker import TokenTracker
    monkeypatch.setattr(_gemini_support, "types", types)
    monkeypatch.setattr(TokenTracker, "get_instance",
                        lambda: SimpleNamespace(record=lambda **_kwargs: None))


def _event(*, text=None, user=None, complete=False, interrupted=False):
    return SimpleNamespace(
        tool_call=None,
        tool_call_cancellation=None,
        voice_activity_detection_signal=None,
        server_content=SimpleNamespace(
            input_transcription=SimpleNamespace(text=user) if user else None,
            output_transcription=SimpleNamespace(text=text) if text else None,
            model_turn=None,
            turn_complete=complete,
            interrupted=interrupted,
        ),
    )


def _client_manager():
    manager = _make_manager()
    manager._clear_tts_pipeline = AsyncMock()
    manager.send_user_activity = AsyncMock()
    manager._reset_ai_turn_buffer = lambda: None
    manager.tool_registry = SimpleNamespace(
        get=lambda _name: None,
        execute=AsyncMock(side_effect=lambda call: ToolResult(
            name=call.name, call_id=call.call_id, output={"value": "synthetic"}
        )),
    )

    async def text_delta(text, is_first_chunk):
        await TurnMixin.send_lanlan_response(manager, text, is_first_chunk)

    client = OmniRealtimeClient(
        "wss://example.invalid/realtime", "synthetic", api_type="gemini",
        get_host_turn_id=lambda: manager.current_speech_id,
        on_new_message=manager.handle_new_message,
        on_text_delta=text_delta,
        on_input_transcript=AsyncMock(),
        on_response_done=AsyncMock(),
    )
    provider = _GeminiSession()
    manager.session = client
    client._gemini_session = client.ws = provider
    client._on_connection_attached()
    client.on_tool_call = manager._make_tool_call_handler(client)
    return manager, client, provider


async def _dispatch(client, provider, event):
    await client._process_gemini_response(
        event, provider_session=provider,
        connection_generation=client._connection_generation,
    )


async def test_normal_reply_facts_and_record_share_frozen_identity(monkeypatch):
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())
    facts = []
    records = []
    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort",
                        lambda name, *, fact, payload: facts.append((fact, payload)))
    async def noop():
        pass
    def observed(name, **kwargs):
        records.append(kwargs)
        return noop()
    monkeypatch.setattr(turn_module, "publish_conversation_turn_observed_best_effort", observed)
    client = _client([[_text("hello"), _text(" world"), _text("", "stop")]])
    manager = _observe(_make_callback_media_manager(client))
    del manager.send_lanlan_response
    manager._fire_task = lambda coro: coro.close()
    _wire(manager, client)
    await core.LLMSessionManager._process_stream_data_internal(
        manager, {"input_type": "text", "data": "question", "request_id": "r", "_conversation_input_id": "input"},
    )
    assert [fact for fact, _ in facts] == ["reply.started", "reply.closed"]
    started, closed = [payload for _, payload in facts]
    assert started["text"] and "hello world".startswith(started["text"])
    assert closed["text"] == "hello world"
    assert started["reply_id"] == closed["reply_id"]
    assert started["turn_id"] == closed["turn_id"]
    assert started["turn_id"]
    assert started["input_id"] == closed["input_id"] == "input"
    assert closed["close_reason"] == "completed"
    assistant = [record for record in records if record["turn_type"] == "assistant_message"]
    assert assistant[0]["metadata"]["reply_id"] == started["reply_id"]
    assert assistant[0]["metadata"]["input_id"] == "input"


async def test_reply_start_after_guard_and_interruption_identity(monkeypatch):
    facts = []
    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort",
                        lambda name, *, fact, payload: facts.append((fact, payload)))
    manager = _core_manager()
    del manager.send_lanlan_response
    reply = manager._begin_reply_turn(speech_id=manager.current_speech_id)
    reply.session = manager.session
    await manager.send_lanlan_response(" ", reply_owner=reply)
    assert facts == []
    await manager.send_lanlan_response("no", reply_owner=reply, publish_if=lambda: False)
    assert facts == []
    await manager.send_lanlan_response("visible", reply_owner=reply)
    manager._close_reply_observation(reply, "interrupted")
    manager._close_reply_observation(reply, "completed")
    assert [event for event, _ in facts] == ["reply.started", "reply.closed"]
    assert facts[-1][1]["reply_id"] == facts[0][1]["reply_id"]
    assert facts[-1][1]["close_reason"] == "interrupted"


async def test_playback_router_publishes_explicit_presentation_and_reasons(monkeypatch):
    import json
    from fastapi import WebSocketDisconnect
    from main_routers import websocket_router
    from main_logic import agent_event_bus

    signals = []
    facts = []
    class Manager:
        pending_agent_callbacks = []
        websocket = None
        def set_user_language(self, value):
            pass
        def on_voice_playback_signal(self, **kwargs):
            signals.append(kwargs)
        async def cleanup(self, **kwargs):
            pass
    class Socket:
        client = "playback-test"
        def __init__(self):
            self.messages = [
                {"action": "voice_play_start", "turnId": "turn", "presentationId": "pres", "presenterId": "window"},
                {"action": "voice_play_end", "turnId": "turn", "presentationId": "pres", "presenterId": "window", "stopReason": "canceled"},
            ]
        async def accept(self):
            pass
        async def receive_text(self):
            if not self.messages:
                raise WebSocketDisconnect()
            return json.dumps(self.messages.pop(0))
    monkeypatch.setattr(websocket_router, "get_config_manager", lambda: object())
    monkeypatch.setattr(websocket_router, "get_session_manager", lambda: {"Test": Manager()})
    monkeypatch.setattr(websocket_router, "get_session_id", lambda: {})
    monkeypatch.setattr(websocket_router, "_fire_task", lambda coro: coro.close())
    monkeypatch.setattr(agent_event_bus, "schedule_conversation_fact_observed_best_effort",
                        lambda name, *, fact, payload: facts.append((fact, payload)))
    await websocket_router.websocket_endpoint(Socket(), "Test")
    assert [x["playing"] for x in signals] == [True, False]
    assert [f[0] for f in facts] == ["playback.started", "playback.stopped"]
    assert facts[0][1]["state"] == "playing"
    assert facts[1][1]["stop_reason"] == "canceled"
    assert facts[1][1]["presentation_id"] == "pres"
    assert facts[1][1]["presenter_id"] == "window"
    assert facts[1][1]["reply_id"] is None


async def test_retry_does_not_close_and_final_discard_closes_once(monkeypatch):
    facts = []
    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort",
                        lambda name, *, fact, payload: facts.append((fact, payload)))
    manager = _core_manager()
    reply = manager._begin_reply_turn(speech_id=manager.current_speech_id, request_id="r")
    reply.session = manager.session
    manager._active_text_request_id = "r"
    await manager.handle_response_discarded("invalid", 1, 2, True, request_id="r", reply_turn=reply)
    assert facts == []
    await manager.handle_response_discarded("invalid", 2, 2, False, request_id="r", reply_turn=reply)
    assert [fact for fact, _ in facts] == ["reply.closed"]
    assert facts[0][1]["close_reason"] == "discarded"
    assert facts[0][1]["reply_id"] == reply.observation_id


async def test_proactive_client_uses_one_observed_identity_for_record(monkeypatch):
    import asyncio
    import main_logic.omni_offline_client._lifecycle as lifecycle_module
    facts = []
    copies = []
    def note(name, *, fact, payload):
        facts.append((fact, payload))
    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort", note)
    monkeypatch.setattr(lifecycle_module, "schedule_conversation_fact_observed_best_effort", note)
    async def bus_publish(name, **kwargs):
        copies.append(kwargs)
        return True
    monkeypatch.setattr(lifecycle_module, "publish_conversation_turn_observed_best_effort", bus_publish)
    client = _client([[_text("greeting"), _text("", "stop")]])
    tasks = []
    def spawn(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task
    client._fire_bus_task = spawn
    manager = _core_manager()
    del manager.send_lanlan_response
    manager.session = client
    _wire(manager, client)
    client.on_proactive_done = None
    await client.prompt_ephemeral("greet")
    if tasks:
        await asyncio.gather(*tasks)
    assert [fact for fact, _ in facts] == ["reply.started", "reply.closed"]
    reply_id = facts[0][1]["reply_id"]
    assert facts[1][1]["reply_id"] == reply_id
    assert facts[1][1]["close_reason"] == "completed"
    reply = next(item for item in copies if item["turn_type"] == "proactive_reply")
    assert reply["metadata"]["reply_id"] == reply_id


async def test_external_asr_reply_uses_single_observation_identity(monkeypatch):
    facts = []
    records = []
    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort",
                        lambda name, *, fact, payload: facts.append((fact, payload)))
    async def noop():
        pass
    def observed(name, **kwargs):
        records.append(kwargs)
        return noop()
    monkeypatch.setattr(turn_module, "publish_conversation_turn_observed_best_effort", observed)
    client = _client([[_text("voice answer"), _text("", "stop")]])
    manager = _core_manager()
    del manager.send_lanlan_response
    manager._fire_task = lambda coro: coro.close()
    manager.session = client
    _wire(manager, client)
    await client._run_external_voice_stream("transcript", turn_id="voice-turn")
    assert [fact for fact, _ in facts] == ["reply.started", "reply.closed"]
    assert facts[0][1]["reply_id"] == facts[1][1]["reply_id"]
    assert facts[1][1]["origin"]["kind"] == "user_interaction"
    assert facts[1][1]["close_reason"] == "completed"
    assert records[-1]["metadata"]["reply_id"] == facts[0][1]["reply_id"]


async def test_realtime_response_scopes_observation_and_keeps_unknown_origin(monkeypatch):
    import main_logic.omni_realtime_client._transport as transport_module
    from tests.unit.test_realtime_arbiter_native_path import _native_client
    facts = []
    records = []
    def note(name, *, fact, payload):
        facts.append((fact, payload))
    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort", note)
    monkeypatch.setattr(transport_module, "schedule_conversation_fact_observed_best_effort", note)
    async def noop():
        pass
    def observed(name, **kwargs):
        records.append(kwargs)
        return noop()
    monkeypatch.setattr(turn_module, "publish_conversation_turn_observed_best_effort", observed)
    client = _native_client()
    client.lanlan_name = "Lan"
    manager = _core_manager()
    del manager.send_lanlan_response
    manager.session = client
    manager._fire_task = lambda coro: coro.close()
    client._begin_response_lifecycle("response-1")
    await client._observed_text_callback(manager.handle_text_data, "realtime", True)
    client._close_realtime_conversation_observation("completed")
    await manager._emit_turn_end(None)
    assert [kind for kind, _ in facts] == ["reply.started", "reply.closed"]
    assert facts[0][1]["reply_id"] == facts[1][1]["reply_id"]
    assert facts[1][1]["close_reason"] == "completed"
    assert facts[0][1]["origin"] is None
    assert records[-1]["metadata"]["reply_id"] == facts[0][1]["reply_id"]


async def test_native_gemini_accepted_turn_emits_observed_boundaries(monkeypatch, _gemini_sdk):
    import main_logic.omni_realtime_client._transport as transport_module
    from utils.token_tracker import TokenTracker
    facts = []
    def note(name, *, fact, payload):
        facts.append((fact, payload))
    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort", note)
    monkeypatch.setattr(transport_module, "schedule_conversation_fact_observed_best_effort", note)
    monkeypatch.setattr(TokenTracker, "get_instance", lambda: SimpleNamespace(record=lambda **kwargs: None))
    manager, client, provider = _client_manager()
    client.lanlan_name = manager.lanlan_name
    await _dispatch(client, provider, _event(text="hello"))
    await _dispatch(client, provider, _event(complete=True))
    assert [kind for kind, _ in facts] == ["reply.started", "reply.closed"]
    assert facts[0][1]["reply_id"] == facts[1][1]["reply_id"]
    assert facts[1][1]["close_reason"] == "completed"


@pytest.mark.parametrize("defer_record", [False, True])
@pytest.mark.parametrize("new_boundary", [False, True])
async def test_gemini_late_transcript_corrects_original_reply_record(monkeypatch, _gemini_sdk, defer_record, new_boundary):
    import main_logic.omni_realtime_client._transport as transport_module
    from plugin.message_plane.stores import TopicStore
    from plugin.server.application.conversations import ConversationService

    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance",
                        lambda: SimpleNamespace(record=lambda **_: None))
    store = TopicStore("conversations", 10)
    service = ConversationService(store.conversation_message, update_message=store.patch_conversation)
    facts, records, pending = [], [], []

    def note(name, *, fact, payload):
        facts.append((fact, payload))
        service.accept_fact(fact, payload)

    async def noop():
        pass

    def observed(name, **kwargs):
        records.append(kwargs)
        payload = {"id": f"message-{len(records)}", "content": kwargs["content"],
                   "reply_id": kwargs["metadata"].get("reply_id"),
                   "origin": kwargs["metadata"].get("origin"),
                   "metadata": {"turn_type": kwargs["turn_type"], "lanlan_name": name}}
        if defer_record:
            pending.append(payload)
        else:
            service.accept_message(store.publish("all", payload))
        return noop()

    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort", note)
    monkeypatch.setattr(transport_module, "schedule_conversation_fact_observed_best_effort", note)
    monkeypatch.setattr(turn_module, "publish_conversation_turn_observed_best_effort", observed)
    manager, client, provider = _client_manager()
    client.lanlan_name = manager.lanlan_name
    manager._note_ai_turn = lambda **_: None
    manager._reset_ai_turn_buffer = turn_module.TurnMixin._reset_ai_turn_buffer.__get__(manager)
    manager._fire_task = lambda coro: coro.close()

    async def complete():
        manager._flush_ai_turn_text_to_tracker()

    client.on_response_done = complete
    await _dispatch(client, provider, _event(text="first"))
    await _dispatch(client, provider, _event(complete=True))
    reply_id = facts[0][1]["reply_id"]
    original_turn = facts[0][1]["turn_id"]
    manager.current_speech_id = "after-terminal"
    await _dispatch(client, provider, _event(text="<happy> continuation"))
    await _dispatch(client, provider, _event(complete=True))
    for payload in pending:
        service.accept_message(store.publish("all", payload))
    pending.clear()
    assert len(records) == 1
    assert [fact for fact, _ in facts] == ["reply.started", "reply.closed", "reply.updated"]
    assert {payload["reply_id"] for _, payload in facts} == {reply_id}
    assert client._realtime_conversation_observation["turn_id"] == original_turn
    assert service.get_reply(reply_id)["text"] == "first continuation"
    assert store.conversation_message("message-1")["text"] == "first continuation"
    # An inject inside the late-frame window cannot prove which request owns
    # ID-less content. A recognized new boundary can carry its active source.
    token = "proactive-injection"
    client._gemini_proactive_outcome_owner = (
        client._connection_generation, provider, token, None, client._tool_scope_generation,
    )
    client._gemini_proactive_outcome = (token, None, None)
    client._proactive_inject_awaiting_outcome = True
    if new_boundary:
        client._ai_recent_activity_time = 0
    await _dispatch(client, provider, _event(text="proactive"))
    observation = client._realtime_conversation_observation
    await _dispatch(client, provider, _event(complete=True))
    for payload in pending:
        service.accept_message(store.publish("all", payload))
    pending.clear()
    assert service.get_reply(reply_id)["text"] == "first continuation"
    if not new_boundary:
        assert observation is None
        assert len(records) == 2 and records[-1]["metadata"].get("reply_id") is None
        assert [fact for fact, _ in facts] == ["reply.started", "reply.closed", "reply.updated"]
        return
    proactive_reply = observation["reply_id"]
    assert proactive_reply != reply_id
    assert service.get_reply(proactive_reply)["origin"] is None
    assert service.get_reply(proactive_reply)["text"] == "proactive"
    await _dispatch(client, provider, _event(text=" tail"))
    await _dispatch(client, provider, _event(complete=True))
    for payload in pending:
        service.accept_message(store.publish("all", payload))
    assert len(records) == 2
    assert service.get_reply(proactive_reply)["text"] == "proactive tail"
    assert service.get_reply(reply_id)["text"] == "first continuation"


async def test_gemini_ambiguous_injection_retires_open_previous_observation(monkeypatch, _gemini_sdk):
    import main_logic.omni_realtime_client._transport as transport_module

    facts = []
    note = lambda name, *, fact, payload: facts.append((fact, payload))
    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort", note)
    monkeypatch.setattr(transport_module, "schedule_conversation_fact_observed_best_effort", note)
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance",
                        lambda: SimpleNamespace(record=lambda **_: None))
    manager, client, provider = _client_manager()
    client.lanlan_name = manager.lanlan_name
    await _dispatch(client, provider, _event(text="original"))
    previous = client._realtime_conversation_observation
    await client.handle_interruption()
    await _dispatch(client, provider, _event(complete=True))
    assert previous["started"] and not previous["closed"]
    owner = (client._connection_generation, provider, "new-inject", None, client._tool_scope_generation)
    client._gemini_proactive_outcome_owner = owner
    await _dispatch(client, provider, _event(text="ambiguous"))
    assert previous["closed"] and client._realtime_conversation_observation is None
    assert facts[-1][0] == "reply.closed"
    assert facts[-1][1]["reply_id"] == previous["reply_id"]
    assert facts[-1][1]["close_reason"] == "unknown"
    assert client._gemini_proactive_outcome_owner is owner


async def test_provider_failure_without_text_is_not_reported_completed(monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())
    facts = []
    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort",
                        lambda name, *, fact, payload: facts.append((fact, payload)))
    client = _client([RuntimeError("provider failed")])
    manager = _observe(_make_callback_media_manager(client))
    del manager.send_lanlan_response
    manager._fire_task = lambda coro: coro.close()
    _wire(manager, client)
    await core.LLMSessionManager._process_stream_data_internal(
        manager, {"input_type": "text", "data": "question", "request_id": "r"},
    )
    assert [kind for kind, _ in facts] == ["reply.closed"]
    assert facts[0][1]["close_reason"] == "failed"
    assert facts[0][1]["audio_expected"] is False


async def test_post_commit_repetition_failure_keeps_observed_reply_completed(monkeypatch):
    from types import SimpleNamespace
    from tests.unit.test_offline_turn_cancellation_e2e import _client as make_client, _reply_chunks

    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())
    client = make_client()
    client.script = [_reply_chunks("openai", "kept reply")] * 3

    async def failed_check(_text):
        raise RuntimeError("repetition check unavailable")

    client._check_repetition = failed_check
    owner = SimpleNamespace(reply_tail=None, observation_close_reason="unknown")
    await client.stream_text("question", reply_owner=owner)
    assert any(getattr(item, "content", None) == "kept reply" for item in client._conversation_history)
    assert owner.observation_close_reason == "completed"


async def test_closing_stalled_offline_reply_does_not_claim_completion(monkeypatch):
    import asyncio
    from unittest.mock import MagicMock
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())
    facts = []
    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort",
                        lambda name, *, fact, payload: facts.append((fact, payload)))
    parked = asyncio.Event()
    release = asyncio.Event()
    async def wait():
        parked.set()
        await release.wait()
    client = _client([[_text("shown before close"), wait]])
    manager = _observe(_make_callback_media_manager(client))
    del manager.send_lanlan_response
    manager._fire_task = lambda coro: coro.close()
    _wire(manager, client)
    task = asyncio.create_task(core.LLMSessionManager._process_stream_data_internal(
        manager, {"input_type": "text", "data": "question", "request_id": "r"},
    ))
    try:
        await asyncio.wait_for(parked.wait(), 2)
        await client.close()
        release.set()
        await asyncio.wait_for(task, 2)
        closed = [payload for kind, payload in facts if kind == "reply.closed"]
        assert len(closed) == 1
        assert closed[0]["close_reason"] in ("unknown", "interrupted")
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_direct_proactive_commit_observation_shares_record_identity(monkeypatch):
    import asyncio
    from main_logic.session_state import SessionStateMachine
    facts = []
    records = []
    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort",
                        lambda name, *, fact, payload: facts.append((fact, payload)))
    async def noop():
        pass
    def observed(name, **kwargs):
        records.append(kwargs)
        return noop()
    monkeypatch.setattr(turn_module, "publish_conversation_turn_observed_best_effort", observed)
    manager = _core_manager()
    del manager.send_lanlan_response
    manager._proactive_write_lock = asyncio.Lock()
    manager.state = SessionStateMachine(lanlan_name="Lan")
    manager.session = None
    manager._fire_task = lambda coro: coro.close()
    result = await manager.finish_proactive_delivery("proactive words", expected_speech_id=manager.current_speech_id)
    assert result is True
    assert [kind for kind, _ in facts] == ["reply.started", "reply.closed"]
    assert facts[0][1]["reply_id"] == facts[1][1]["reply_id"]
    assert facts[0][1]["origin"]["is_proactive"] is True
    assert records[-1]["metadata"]["reply_id"] == facts[0][1]["reply_id"]


async def test_reply_turn_aliases_only_actual_published_speech_ids(monkeypatch):
    facts = []
    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort",
                        lambda name, *, fact, payload: facts.append((fact, payload)))
    manager = _core_manager()
    del manager.send_lanlan_response
    reply = manager._begin_reply_turn(speech_id="first")
    await manager.send_lanlan_response("one", turn_id="first", reply_owner=reply)
    reply.speech_id = "rotated-but-unpublished"
    await manager.send_lanlan_response("two", turn_id="second", reply_owner=reply)
    manager._close_reply_observation(reply, "completed")
    assert facts[0][1]["turn_id"] == "first"
    assert facts[1][1]["turn_id"] == "second"
    assert facts[0][1]["reply_id"] == facts[1][1]["reply_id"]


@pytest.mark.parametrize("started", [False, True])
async def test_realtime_successor_closes_only_started_previous_observation(monkeypatch, started):
    from main_logic.omni_realtime_client import _transport
    from tests.unit.test_realtime_arbiter_native_path import _native_client

    facts = []
    monkeypatch.setattr(_transport, "schedule_conversation_fact_observed_best_effort",
                        lambda name, *, fact, payload: facts.append((fact, payload)))
    client = _native_client()
    client.lanlan_name = "Lan"
    try:
        client._begin_response_lifecycle("previous")
        previous = client._realtime_conversation_observation
        previous["started"] = started
        client._begin_response_lifecycle("successor")
        assert client._current_response_id == "successor"
        assert client._realtime_conversation_observation is not previous
        assert not client._realtime_conversation_observation["closed"]
        assert len(facts) == int(started)
        if started:
            assert facts[0][1]["reply_id"] == previous["reply_id"]
            assert facts[0][1]["close_reason"] == "unknown"
    finally:
        await client.close()


async def test_realtime_release_closes_matching_observation_without_touching_successor(monkeypatch):
    from unittest.mock import AsyncMock
    from main_logic.omni_realtime_client import _transport
    from tests.unit.test_realtime_arbiter_native_path import _native_client

    facts = []
    monkeypatch.setattr(_transport, "schedule_conversation_fact_observed_best_effort",
                        lambda name, *, fact, payload: facts.append((fact, payload)))
    client = _native_client()
    client.lanlan_name = "Lan"
    client._notify_turn_finished = AsyncMock()
    try:
        client._begin_response_lifecycle("live")
        observation = client._realtime_conversation_observation
        observation["started"] = True
        await client._on_arbiter_stuck_release("timeout", "other")
        assert not observation["closed"] and not facts
        assert client._current_response_id == "live"
        await client._on_arbiter_stuck_release("timeout", "live")
        assert observation["closed"]
        assert len(facts) == 1
        assert facts[0][1]["reply_id"] == observation["reply_id"]
        assert facts[0][1]["close_reason"] == "unknown"
        client._close_realtime_conversation_observation("completed")
        assert len(facts) == 1
    finally:
        await client.close()


@pytest.mark.parametrize("cancelled", [False, True])
async def test_direct_proactive_send_failure_closes_observation_and_preserves_exception(monkeypatch, cancelled):
    import asyncio
    from main_logic.agent_event_bus import conversation_reply_observation
    from main_logic.session_state import SessionStateMachine

    facts = []
    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort",
                        lambda name, *, fact, payload: facts.append((fact, payload)))
    manager = _core_manager()
    del manager.send_lanlan_response
    manager._proactive_write_lock = asyncio.Lock()
    manager.state = SessionStateMachine(lanlan_name="Lan")
    manager.session = None
    original_send = manager.send_lanlan_response
    error = asyncio.CancelledError() if cancelled else RuntimeError("send failed")

    async def failed_send(*args, **kwargs):
        await original_send(*args, **kwargs)
        raise error

    manager.send_lanlan_response = failed_send
    before = conversation_reply_observation.get()
    with pytest.raises(type(error)) as caught:
        await manager.finish_proactive_delivery("visible", expected_speech_id=manager.current_speech_id)
    assert caught.value is error
    assert conversation_reply_observation.get() is before
    assert [kind for kind, _ in facts] == ["reply.started", "reply.closed"]
    assert facts[0][1]["reply_id"] == facts[1][1]["reply_id"]
    assert facts[1][1]["close_reason"] == ("interrupted" if cancelled else "failed")
