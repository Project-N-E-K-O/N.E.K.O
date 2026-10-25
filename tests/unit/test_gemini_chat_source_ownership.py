"""Exercise chat provenance through actual Gemini dispatch and tool-result sends."""

import asyncio
import gc
import time
import weakref
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.core.turn import TurnMixin
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_realtime_client import _gemini_support
from main_logic.tool_calling import ToolResult
from tests.unit.test_core_game_route_memory_contract import _make_manager
from tests.unit.test_realtime_tool_ownership import (
    _GeminiSession,
    _gemini_response,
    _wait_for_tool_tasks,
)


@pytest.fixture(autouse=True)
def _sdk_without_external_telemetry(monkeypatch):
    from google.genai import types
    from utils.token_tracker import TokenTracker

    monkeypatch.setattr(_gemini_support, "types", types)
    monkeypatch.setattr(TokenTracker, "get_instance", lambda: SimpleNamespace(record=lambda **_kwargs: None))


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


def _responses(manager):
    return [event["data"] for event in manager.sync_message_queue.messages
            if event["type"] == "json" and event["data"]["type"] == "gemini_response"]


def _tool_ids(manager):
    return {event["chat_original_id"] for event in manager.sync_message_queue.messages
            if event["type"] == "system" and event["data"] == "chat original tool"}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_finishes_after_terminal", [False, True])
@pytest.mark.parametrize("tool_error", [False, True])
async def test_gemini_tool_continuation_does_not_taint_next_voice_turn(tool_finishes_after_terminal, tool_error):
    manager, client, provider = _client_manager()
    await _dispatch(client, provider, _event(text="tool preface"))
    started, release = asyncio.Event(), asyncio.Event()

    async def execute(call):
        started.set()
        await release.wait()
        if tool_error:
            raise RuntimeError("synthetic tool error")
        return ToolResult(name=call.name, call_id=call.call_id, output={"ok": True})

    manager.tool_registry.execute = execute
    await _dispatch(client, provider, _gemini_response(calls=(("call-1", "lookup"),)))
    await asyncio.wait_for(started.wait(), 1)
    if tool_finishes_after_terminal:
        await _dispatch(client, provider, _event(complete=True))
    release.set()
    await _wait_for_tool_tasks(client)
    assert len(provider.tool_responses) == 1
    if not tool_finishes_after_terminal:
        # A tool result may start its continuation while the prior response is
        # still open; the final terminal below is the authoritative end.
        client._is_responding = False
    client._ai_recent_activity_time = time.time() - 5
    await _dispatch(client, provider, _event(text="tool continuation"))
    continuation = _responses(manager)[-1]
    assert continuation["chat_original_source"]["recordId"] in _tool_ids(manager)
    await _dispatch(client, provider, _event(complete=True))
    assert client._gemini_tool_response_source is None
    # Gemini can send a late transcription after turn_complete. The existing
    # host identity still carries its fixed tool proof without a live token.
    await _dispatch(client, provider, _event(text="late tool transcript"))
    late_tail = _responses(manager)[-1]
    assert late_tail["turn_id"] == continuation["turn_id"]
    assert late_tail["chat_original_source"]["recordId"] in _tool_ids(manager)
    await _dispatch(client, provider, _event(complete=True))
    unchanged_scope = client._tool_scope_generation
    await _dispatch(client, provider, _event(user="next ordinary voice"))
    client._ai_recent_activity_time = 0
    await _dispatch(client, provider, _event(text="ordinary voice reply"))
    ordinary = _responses(manager)[-1]
    assert client._tool_scope_generation == unchanged_scope
    assert ordinary["turn_id"] != continuation["turn_id"]
    assert ordinary["chat_original_source"]["origin"] == "chat"
    assert ordinary["chat_original_source"]["recordId"] not in _tool_ids(manager)


@pytest.mark.asyncio
async def test_gemini_voice_proactive_after_tool_uses_its_actual_inject_owner():
    manager, client, provider = _client_manager()
    await _dispatch(client, provider, _event(text="tool preface"))
    await _dispatch(client, provider, _gemini_response(calls=(("call-1", "lookup"),)))
    await _wait_for_tool_tasks(client)
    await _dispatch(client, provider, _event(complete=True))
    client._ai_recent_activity_time = client._user_recent_activity_time = 0

    async def send_client_content(**_kwargs):
        await _dispatch(client, provider, _event(text="ordinary proactive reply"))
        await _dispatch(client, provider, _event(complete=True))

    provider.send_client_content = send_client_content
    assert await client.prompt_ephemeral(language="en") is True
    response = _responses(manager)[-1]
    assert response["chat_original_source"]["origin"] == "proactive"
    assert response["chat_original_source"]["recordId"] not in _tool_ids(manager)


@pytest.mark.asyncio
async def test_gemini_tool_continuation_marks_each_new_reply_once():
    manager, client, provider = _client_manager()
    await _dispatch(client, provider, _event(text="tool preface"))
    await _dispatch(client, provider, _gemini_response(calls=(("call-1", "lookup"),)))
    await _wait_for_tool_tasks(client)
    client._is_responding = False
    client._ai_recent_activity_time = 0
    for text in ("tool", " continuation", " tail"):
        await _dispatch(client, provider, _event(text=text))
    response = _responses(manager)[-1]
    record_id = response["chat_original_source"]["recordId"]
    assert response["chat_original_source"]["origin"] == "tool"
    markers = [event for event in manager.sync_message_queue.messages
               if event["type"] == "system" and event["data"] == "chat original tool"
               and event["chat_original_id"] == record_id]
    assert len(markers) == 1


@pytest.mark.asyncio
async def test_gemini_tool_source_cannot_cross_reconnection():
    manager, client, provider = _client_manager()
    await _dispatch(client, provider, _event(text="old preface"))
    await _dispatch(client, provider, _gemini_response(calls=(("call-1", "lookup"),)))
    await _wait_for_tool_tasks(client)
    old_generation = client._connection_generation
    replacement = _GeminiSession()
    client._gemini_session = client.ws = replacement
    client._on_connection_attached()
    client._is_responding = False
    client._ai_recent_activity_time = 0
    await client._process_gemini_response(
        _event(text="retired content"), provider_session=provider,
        connection_generation=old_generation,
    )
    await _dispatch(client, replacement, _event(text="replacement voice reply"))
    assert [item["text"] for item in _responses(manager)] == ["old preface", "replacement voice reply"]
    assert _responses(manager)[-1]["chat_original_source"]["recordId"] not in _tool_ids(manager)


@pytest.mark.asyncio
async def test_terminal_callback_cannot_clear_a_new_tool_result_source():
    manager, client, provider = _client_manager()
    await _dispatch(client, provider, _event(text="tool preface"))
    await _dispatch(client, provider, _gemini_response(calls=(("call-1", "lookup"),)))
    await _wait_for_tool_tasks(client)
    source_before = client._gemini_tool_response_source
    owner = source_before[0]

    async def send_late_result():
        await client._send_tool_result_gemini(
            [ToolResult(name="lookup", call_id="late-1", output={"ok": True})],
            provider_session=provider, owner=owner,
        )

    client.on_response_done = send_late_result
    await _dispatch(client, provider, _event(complete=True))
    assert client._gemini_tool_response_source is not source_before
    assert client._gemini_tool_response_source[0] is owner
    client.on_response_done = AsyncMock()
    client._ai_recent_activity_time = 0
    await _dispatch(client, provider, _event(text="late tool continuation"))
    assert _responses(manager)[-1]["chat_original_source"]["recordId"] in _tool_ids(manager)
    await _dispatch(client, provider, _event(complete=True))
    assert client._gemini_tool_response_source is None


@pytest.mark.asyncio
async def test_combined_tool_call_terminal_clears_dispatch_but_keeps_late_result_local():
    manager, client, provider = _client_manager()
    await _dispatch(client, provider, _event(text="tool preface"))
    started, release = asyncio.Event(), asyncio.Event()

    async def execute(call):
        started.set()
        await release.wait()
        return ToolResult(name=call.name, call_id=call.call_id, output={"ok": True})

    manager.tool_registry.execute = execute
    combined = _gemini_response(calls=(("combined-1", "lookup"),))
    combined.server_content = _event(complete=True).server_content
    await _dispatch(client, provider, combined)
    await asyncio.wait_for(started.wait(), 1)
    assert client._gemini_tool_response_source is None
    client._ai_recent_activity_time = 0
    await _dispatch(client, provider, _event(text="independent voice before delayed result"))
    ordinary = _responses(manager)[-1]
    assert ordinary["chat_original_source"]["recordId"] not in _tool_ids(manager)
    await _dispatch(client, provider, _event(complete=True))
    release.set()
    await _wait_for_tool_tasks(client)
    assert len(provider.tool_responses) == 1
    client._ai_recent_activity_time = 0
    await _dispatch(client, provider, _event(text="delayed tool result reply"))
    assert _responses(manager)[-1]["chat_original_source"]["recordId"] in _tool_ids(manager)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_uncertain_tool_response_send_stays_local_only_until_its_terminal(cancelled):
    manager, client, provider = _client_manager()
    owner = client._capture_tool_task_owner(provider)

    async def failed_send(**_kwargs):
        await _dispatch(client, provider, _event(text="tool reply before failed SDK acknowledgement"))
        if cancelled:
            raise asyncio.CancelledError()
        raise OSError("synthetic SDK send failure")

    provider.send_tool_response = failed_send
    send = client._send_tool_result_gemini(
        [ToolResult(name="lookup", call_id="send-1", output={"ok": True})],
        provider_session=provider, owner=owner,
    )
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await send
    else:
        await send
    assert _responses(manager)[-1]["chat_original_source"]["recordId"] in _tool_ids(manager)
    await _dispatch(client, provider, _event(complete=True))
    assert client._gemini_tool_response_source is None
    client._ai_recent_activity_time = 0
    await _dispatch(client, provider, _event(text="ordinary reply after failed send"))
    assert _responses(manager)[-1]["chat_original_source"]["recordId"] not in _tool_ids(manager)


@pytest.mark.asyncio
@pytest.mark.parametrize("closes_source_owner", [False, True])
async def test_connection_retirement_releases_only_its_source_reference(closes_source_owner):
    from main_logic.core.session_records import ConnectionRecord

    class ClosedClient:
        async def close(self):
            pass

    manager = _make_manager()
    retired = ClosedClient()
    replacement = ClosedClient()
    manager.session = replacement
    manager._chat_original_tool_owner = (retired if closes_source_owner else replacement, object(), "speech")
    retired_ref = weakref.ref(retired)
    record = ConnectionRecord(retired, retired.close, None)
    await manager._close_connection_record(record)
    if closes_source_owner:
        assert manager._chat_original_tool_owner is None
    else:
        assert manager._chat_original_tool_owner[0] is replacement
    del retired, record
    # Cleanup task callbacks release their coroutine/record on the next loop
    # pass. The manager itself remains alive while the old client is collected.
    await asyncio.sleep(0)
    gc.collect()
    assert retired_ref() is None
