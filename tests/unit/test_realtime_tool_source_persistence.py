"""Tool provenance survives terminal races while all visible text shares the chat grant."""

import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401
from tests.unit.test_chat_sync_source import DOMAIN, UID, make_runtime, source_for
from tests.unit.test_gemini_chat_source_ownership import _client_manager, _dispatch, _event, _tool_ids
from tests.unit.test_realtime_tool_ownership import _QueueSocket, _gemini_response, _raw_tool_event, _wait_for_tool_tasks


def tool_records(source):
    """Inspect durable provenance even after the reply joined the shared history."""
    with sqlite3.connect(source.path) as db:
        return [{"messageId": identity, **json.loads(raw)} for identity, raw in db.execute(
            "SELECT record_id,original FROM source_local_replies WHERE uid=? AND deleted=0 ORDER BY rowid", (UID,))]


@pytest.mark.asyncio
@pytest.mark.parametrize("api_type", ["gpt", "gemini"])
@pytest.mark.parametrize("tool_outcome", ["success", "error", "flood"])
async def test_provider_terminal_cannot_save_tool_preface_before_deferred_dispatch(tmp_path, monkeypatch, api_type, tool_outcome):
    from google.genai import types
    from main_logic import cross_server
    from main_logic.core.turn import TurnMixin
    from main_logic.omni_realtime_client import _gemini_support
    from main_logic.omni_realtime_client import OmniRealtimeClient
    import services.chat_sync.source as source_module

    runtime = make_runtime(tmp_path)
    config = SimpleNamespace(aload_characters=AsyncMock(return_value={
        "猫娘": {"Lan": {"_reserved": {"character_uid": UID}}},
    }))
    monkeypatch.setattr("services.chat_sync.runtime.get_desktop_chat_sync_runtime", AsyncMock(return_value=runtime))
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", Mock())
    monkeypatch.setattr("httpx.AsyncClient.send", AsyncMock(side_effect=AssertionError("unexpected_http")))
    monkeypatch.setattr(_gemini_support, "types", types)
    original_save, local_save = source_module.save_original, source_module.save_local_reply

    async def save_original(name, role, text, **kwargs):
        return await original_save(name, role, text, config_manager=config, **kwargs)

    async def save_local(name, text, proof, **kwargs):
        return await local_save(name, text, proof, config_manager=config, **kwargs)

    monkeypatch.setattr(source_module, "save_original", save_original)
    monkeypatch.setattr(source_module, "save_local_reply", save_local)
    monkeypatch.setattr(cross_server, "_post_memory_server", AsyncMock(return_value=(True, "", {})))
    monkeypatch.setattr(cross_server, "_publish_analyze_request_with_fallback", AsyncMock(return_value=True))
    manager, client, provider = _client_manager()
    queue = asyncio.Queue()
    source_markers = []

    def enqueue(message):
        if message["type"] == "system" and message["data"] == "chat original tool":
            source_markers.append(message["chat_original_id"])
        queue.put_nowait(message)

    manager.sync_message_queue = SimpleNamespace(put=enqueue)
    terminal_observed = asyncio.Event()

    async def finish_response():
        TurnMixin._queue_turn_end(manager, None)
        terminal_observed.set()

    if api_type == "gpt":
        async def text_delta(text, first):
            await TurnMixin.send_lanlan_response(manager, text, first)

        client = OmniRealtimeClient(
            "wss://example.invalid/realtime", "synthetic", api_type="gpt",
            get_host_turn_id=lambda: manager.current_speech_id,
            on_new_message=manager.handle_new_message, on_text_delta=text_delta,
            on_response_done=finish_response,
        )
        provider = _QueueSocket()
        client.ws = provider
        client._on_connection_attached()
        manager.session = client
        client.on_tool_call = manager._make_tool_call_handler(client)
        client._send_tool_results_openai_realtime = AsyncMock()
    else:
        client.on_response_done = finish_response
    if tool_outcome == "error":
        manager.tool_registry.execute.side_effect = RuntimeError("synthetic tool error")
    elif tool_outcome == "flood":
        client._recent_tool_call_times = [time.time()] * 4
    original_create_task = client._create_tool_task
    release_dispatch = asyncio.Event()

    def defer_tool_task(coro, **kwargs):
        async def deferred():
            await release_dispatch.wait()
            return await coro
        return original_create_task(deferred(), **kwargs)

    monkeypatch.setattr(client, "_create_tool_task", defer_tool_task)
    connector = asyncio.create_task(cross_server.run_sync_connector(
        queue, "Lan", config={"monitor": False, "bullet": False},
    ))
    receiver = asyncio.create_task(client.handle_messages()) if api_type == "gpt" else None

    async def settled():
        done = asyncio.get_running_loop().create_future()
        queue.put_nowait({"type": "system", "data": "session end", "_memory_settlement_done": done})
        await asyncio.wait_for(done, 5)

    try:
        if api_type == "gpt":
            provider.feed({"type": "response.created", "response": {"id": "tool-1"}})
            provider.feed({"type": "response.text.delta", "response_id": "tool-1", "delta": "tool preface"})
            provider.feed(_raw_tool_event("call-1", response_id="tool-1"))
            provider.feed({"type": "response.done", "response": {"id": "tool-1", "status": "completed"}})
            await asyncio.wait_for(terminal_observed.wait(), 5)
        else:
            await _dispatch(client, provider, _event(text="tool preface"))
            await _dispatch(client, provider, _gemini_response(calls=(("call-1", "lookup"),)))
            await _dispatch(client, provider, _event(complete=True))
        await settled()
        source = source_for(runtime)
        # This is durable SQLite state after the actual connector's settlement,
        # while no deferred handler has started. It is not a queue-only check.
        before_dispatch = {
            "pending": len(source.pending(200)),
            "local": [row["text"] for row in tool_records(source)],
        }
        with sqlite3.connect(source.path) as db:
            before_dispatch["originals"] = db.execute("SELECT COUNT(*) FROM source_originals").fetchone()[0]
            before_dispatch["unbound"] = db.execute("SELECT COUNT(*) FROM source_unbound").fetchone()[0]
        before_dispatch["ledger"] = len(runtime.ledger.pending(DOMAIN))
        assert manager.tool_registry.execute.await_count == 0
        release_dispatch.set()
        await _wait_for_tool_tasks(client)
        assert manager.tool_registry.execute.await_count == (0 if tool_outcome == "flood" else 1)
        await settled()
        terminal_observed.clear()
        if api_type == "gpt":
            provider.feed({"type": "input_audio_buffer.speech_started", "item_id": "ordinary-input"})
            provider.feed({"type": "input_audio_buffer.speech_stopped", "item_id": "ordinary-input"})
            provider.feed({"type": "response.created", "response": {"id": "ordinary-2"}})
            provider.feed({"type": "response.text.delta", "response_id": "ordinary-2", "delta": "ordinary voice reply"})
            provider.feed({"type": "response.done", "response": {"id": "ordinary-2", "status": "completed"}})
            await asyncio.wait_for(terminal_observed.wait(), 5)
        else:
            # Finish the legitimate tool-result continuation before the new
            # ordinary voice reply, without relying on a user-scope advance.
            await _dispatch(client, provider, _event(complete=True))
            client._ai_recent_activity_time = 0
            await _dispatch(client, provider, _event(text="ordinary voice reply"))
            await _dispatch(client, provider, _event(complete=True))
        await settled()
        local = tool_records(source)
        pending = source.pending(200)
        assert before_dispatch == {"pending": 1, "local": ["tool preface"],
                                   "originals": 1, "unbound": 0, "ledger": 0}, before_dispatch
        assert [row["text"] for row in local] == ["tool preface"]
        assert source_markers.count(local[0]["messageId"]) == 1
        assert [json.loads(row[0]["original"])["text"] for row in pending] == ["tool preface", "ordinary voice reply"]
        assert source.read_local_replies(source.path, UID) == []
        assert runtime.ledger.pending(DOMAIN) == []
    finally:
        release_dispatch.set()
        await _wait_for_tool_tasks(client)
        for task in (connector, receiver):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(task for task in (connector, receiver) if task is not None), return_exceptions=True)
        arbiter = getattr(client, "_response_arbiter", None)
        if arbiter is not None:
            await arbiter.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("api_type", ["gpt", "gemini"])
@pytest.mark.parametrize("retirement", ["scope", "manager", "connection"])
async def test_realtime_source_acceptance_rejects_retired_owners(api_type, retirement):
    from main_logic.core.turn import TurnMixin
    from main_logic.tool_calling import ToolCall

    manager, client, provider = _client_manager()
    client._is_gemini = api_type == "gemini"
    client._send_tool_results_openai_realtime = AsyncMock()
    await TurnMixin.send_lanlan_response(manager, "ordinary current reply", True)
    owner = client._capture_tool_task_owner(provider)
    if retirement == "scope":
        client.note_user_turn_started()
    elif retirement == "manager":
        manager.session = object()
    else:
        client.ws = client._gemini_session = object()
        client._on_connection_attached()
    call = ToolCall(name="lookup", arguments={}, call_id="retired-1")
    if api_type == "gemini":
        client._start_gemini_tool_batch([call], owner)
    else:
        client._start_raw_tool_call(call, owner)
    await _wait_for_tool_tasks(client)
    assert _tool_ids(manager) == set()
    assert manager._chat_original_turns[manager.current_speech_id]["origin"] == "chat"
    assert getattr(manager, "_chat_original_tool_owner", None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [
    "proactive_tool", "combined_fast_terminal", "independent_proactive",
    "proactive_tool_separate_terminal", "proactive_tool_combined_terminal",
    "proactive_tool_preface_separate_terminal", "proactive_tool_preface_combined_terminal",
])
async def test_gemini_tool_boundaries_preserve_durable_source(tmp_path, monkeypatch, scenario):
    from google.genai import types
    from main_logic import cross_server
    from main_logic.core.turn import TurnMixin
    from main_logic.tool_calling import ToolResult
    from main_logic.omni_realtime_client import _gemini_support
    import services.chat_sync.source as source_module

    runtime = make_runtime(tmp_path)
    config = SimpleNamespace(aload_characters=AsyncMock(return_value={
        "猫娘": {"Lan": {"_reserved": {"character_uid": UID}}},
    }))
    monkeypatch.setattr("services.chat_sync.runtime.get_desktop_chat_sync_runtime", AsyncMock(return_value=runtime))
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", Mock())
    monkeypatch.setattr("httpx.AsyncClient.send", AsyncMock(side_effect=AssertionError("unexpected_http")))
    monkeypatch.setattr(_gemini_support, "types", types)
    original_save, local_save = source_module.save_original, source_module.save_local_reply

    async def save_original(name, role, text, **kwargs):
        return await original_save(name, role, text, config_manager=config, **kwargs)

    async def save_local(name, text, proof, **kwargs):
        return await local_save(name, text, proof, config_manager=config, **kwargs)

    monkeypatch.setattr(source_module, "save_original", save_original)
    monkeypatch.setattr(source_module, "save_local_reply", save_local)
    monkeypatch.setattr(cross_server, "_post_memory_server", AsyncMock(return_value=(True, "", {})))
    monkeypatch.setattr(cross_server, "_publish_analyze_request_with_fallback", AsyncMock(return_value=True))
    manager, client, provider = _client_manager()
    queue = asyncio.Queue()
    manager.sync_message_queue = SimpleNamespace(put=queue.put_nowait)

    async def finish_response():
        TurnMixin._queue_turn_end(manager, None)

    async def settled():
        done = asyncio.get_running_loop().create_future()
        queue.put_nowait({"type": "system", "data": "session end", "_memory_settlement_done": done})
        await asyncio.wait_for(done, 5)

    async def proactive_content(**_kwargs):
        if scenario.startswith("proactive_tool"):
            # Gemini may request a tool before the first visible reply. The
            # first output then opens a new host identity under the injection.
            if "preface" in scenario:
                await _dispatch(client, provider, _event(text="proactive tool preface"))
            if "terminal" in scenario:
                started, release = asyncio.Event(), asyncio.Event()

                async def delayed_tool(call):
                    started.set()
                    await release.wait()
                    return ToolResult(name=call.name, call_id=call.call_id, output={"ok": True})

                manager.tool_registry.execute = delayed_tool
            call_event = _gemini_response(calls=(("call-1", "lookup"),))
            if "combined_terminal" in scenario:
                call_event.server_content = _event(complete=True).server_content
            await _dispatch(client, provider, call_event)
            if "terminal" in scenario:
                await asyncio.wait_for(started.wait(), 1)
                if "separate_terminal" in scenario:
                    await _dispatch(client, provider, _event(complete=True))
                # Exercise the review's lifecycle edge through the real receiver:
                # the outcome has settled before the delayed result is sent.
                try:
                    assert client._gemini_proactive_outcome_owner is None
                finally:
                    release.set()
            await _wait_for_tool_tasks(client)
            assert provider.tool_responses
            if "terminal" in scenario:
                client._ai_recent_activity_time = 0
        await _dispatch(client, provider, _event(text="proactive reply"))
        await _dispatch(client, provider, _event(text=" tail"))
        await _dispatch(client, provider, _event(complete=True))

    client.on_response_done = finish_response
    connector = asyncio.create_task(cross_server.run_sync_connector(
        queue, "Lan", config={"monitor": False, "bullet": False},
    ))
    try:
        if scenario == "combined_fast_terminal":
            async def allow_fast_result():
                await _wait_for_tool_tasks(client)

            manager._clear_tts_pipeline = allow_fast_result
            combined = _gemini_response(calls=(("fast-1", "lookup"),))
            combined.server_content = _event(text="combined tool reply", complete=True).server_content
            await _dispatch(client, provider, combined)
            assert provider.tool_responses
            # The fast result replaced this frame's dispatch token while the
            # host was opening. Its token belongs to the future continuation.
            assert client._gemini_tool_response_source is not None
            client._ai_recent_activity_time = 0
            await _dispatch(client, provider, _event(text="fast tool continuation"))
            await _dispatch(client, provider, _event(complete=True))
            assert client._gemini_tool_response_source is None
        else:
            if scenario == "independent_proactive":
                # An ordinary new injection must not inherit an earlier tool
                # token even if the preceding provider omitted its terminal.
                await _dispatch(client, provider, _event(text="old tool preface"))
                await _dispatch(client, provider, _gemini_response(calls=(("old-1", "lookup"),)))
                await _wait_for_tool_tasks(client)
                client._is_responding = False
                await settled()
            client._ai_recent_activity_time = client._user_recent_activity_time = 0
            provider.send_client_content = proactive_content
            assert await client.prompt_ephemeral(language="en") is True
        await settled()
        source = source_for(runtime)
        pending_text = [json.loads(entry[0]["original"])["text"] for entry in source.pending(200)]
        local_text = [entry["text"] for entry in tool_records(source)]
        if scenario.startswith("proactive_tool"):
            assert {"local": local_text, "pending": pending_text} == {
                "local": (["proactive tool preface"] if "preface" in scenario else []) + ["proactive reply tail"],
                "pending": (["proactive tool preface"] if "preface" in scenario else []) + ["proactive reply tail"],
            }
        elif scenario == "combined_fast_terminal":
            assert local_text == ["combined tool reply", "fast tool continuation"]
            assert pending_text == local_text
        else:
            assert local_text == ["old tool preface"]
            assert pending_text == ["old tool preface", "proactive reply tail"]
        assert runtime.ledger.pending(DOMAIN) == []
        client._ai_recent_activity_time = 0
        await _dispatch(client, provider, _event(text="next ordinary voice"))
        await _dispatch(client, provider, _event(complete=True))
        await settled()
        pending_after = [entry[0]["original"] for entry in source.pending(200)]
        assert len(pending_after) == len(pending_text) + 1
        assert any("next ordinary voice" in text for text in pending_after)
        assert [entry["text"] for entry in tool_records(source)] == local_text
        assert source.read_local_replies(source.path, UID) == []
    finally:
        await _wait_for_tool_tasks(client)
        connector.cancel()
        await asyncio.gather(connector, return_exceptions=True)
