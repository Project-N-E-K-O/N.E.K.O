from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import sqlite3
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from main_logic.audio_message_payload import MAX_AUDIO_BYTES, decode_audio_message
from main_logic.core.audio_message import AudioMessageMixin
from main_logic.omni_realtime_client import OmniRealtimeClient, TurnDetectionMode
from main_routers import websocket_router
from services.chat_audio_history import ChatAudioHistory


def message(pcm=b"\x01\x00" * 8000):
    return {
        "request_id": f"{int(time.time() * 1000)}-{uuid.uuid4()}",
        "sample_rate_hz": 16000,
        "audio": base64.b64encode(pcm).decode(),
    }


class Manager(AudioMessageMixin):
    def __init__(self):
        self.lanlan_name = "YUI"
        self._config_manager = object()
        self.session = None
        self._audio_message_records = {}
        self._audio_message_lock = asyncio.Lock()
        self._voice_proactive_inject_lock = asyncio.Lock()
        self._starting_session_count = 0
        self._session_generation = 0
        self.client = SimpleNamespace(submit_audio_message=AsyncMock(), _connection_generation=0)
        self.request_end_session = Mock()
        self.reset_session_start_circuit = Mock()
        self.sync_message_queue = SimpleNamespace(put=Mock())
        self.handle_input_transcript = AsyncMock(return_value=True)
        self.starts = 0

    async def start_session(self, websocket, **kwargs):
        self.starts += 1
        self.session = self.client
        self.is_active = self.session_ready = True
        self.input_mode = kwargs["input_mode"]
        self._session_generation += 1
        self._start_operation = SimpleNamespace(
            valid=True, request_id=kwargs["request_id"],
            task=asyncio.current_task(), generation=self._session_generation,
        )


@pytest.fixture(autouse=True)
def ordinary_route(monkeypatch):
    monkeypatch.setattr("main_logic.core.audio_message.is_external_route_active", lambda _: False)
    monkeypatch.setattr("main_logic.core.audio_message.is_theater_active", lambda _: False)


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    history = ChatAudioHistory(tmp_path, "local-yui")
    monkeypatch.setattr("main_logic.core.audio_message.history_for_character", AsyncMock(return_value=history))
    return history


@pytest.mark.asyncio
async def test_interrupted_audio_submit_does_not_retire_a_replacement_connection():
    manager, socket = Manager(), SimpleNamespace(send_json=AsyncMock())

    async def submit(*_args, is_current, **_kwargs):
        assert is_current()
        manager.client._connection_generation += 1
        assert not is_current()
        raise ConnectionError("recording connection replaced")

    manager.client.submit_audio_message.side_effect = submit
    await manager.submit_audio_message(message(), socket, is_current=lambda: True, save_local=True)

    assert socket.send_json.await_args.args[0]["state"] == "failed"
    assert socket.send_json.await_args.args[0]["code"] == "unconfirmed"
    manager.request_end_session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_fails", [False, True])
async def test_durable_receipt_prevents_second_provider_submission_after_restart(
    isolated_history, monkeypatch, reply_fails,
):
    history = isolated_history
    first, restarted = Manager(), Manager()
    first.handle_input_transcript = AsyncMock()
    socket = SimpleNamespace(send_json=AsyncMock())
    payload = message()

    async def submit(pcm, request_id, *, is_current, on_accepted, on_transcript):
        assert is_current()
        assert history.read_audio(request_id).endswith(pcm)
        await on_accepted()
        await on_transcript("same message")
        if reply_fails:
            raise ConnectionError("response creation failed after audio acceptance")

    first.client.submit_audio_message.side_effect = submit
    await first.submit_audio_message(payload, socket, is_current=lambda: True, save_local=True)
    expected = "failed" if reply_fails else "completed"
    assert socket.send_json.call_args.args[0]["state"] == expected
    monkeypatch.setattr("services.chat_audio_history._PROCESS_ID", uuid.uuid4().hex)
    await restarted.submit_audio_message(payload, socket, is_current=lambda: True)
    first.client.submit_audio_message.assert_awaited_once()
    restarted.client.submit_audio_message.assert_not_called()
    assert socket.send_json.call_args.args[0]["state"] == expected
    row = history.page(None, 10)["messages"][0]
    assert row["state"] == "sent" and row["transcript"] == "same message"
    first.handle_input_transcript.assert_awaited_once_with(
        "same message", source_game_route_identity=None, audio_message_request_id=payload["request_id"],
        audio_message_transcript_final=True,
    )


@pytest.mark.asyncio
async def test_no_save_delivery_reserves_only_receipt_and_cannot_be_replayed_with_save(isolated_history):
    history = isolated_history
    first, restarted = Manager(), Manager()
    socket = SimpleNamespace(send_json=AsyncMock())
    payload = message()
    request_id = payload["request_id"]

    async def submit(pcm, request_id, *, is_current, on_accepted, on_transcript):
        assert is_current()
        assert history.receipt(request_id)["state"] == "sending"
        await on_accepted()
        await on_transcript("remote recording")
        raise TimeoutError("reply timed out after confirmed delivery")

    first.client.submit_audio_message.side_effect = submit
    await first.submit_audio_message(payload, socket, is_current=lambda: True)
    assert history.receipt(request_id)["state"] == "accepted_unconfirmed"
    assert history.receipt(request_id)["transcript"] == ""
    assert history.read_audio(request_id) is None
    assert history.page(None, 10)["messages"] == []
    first.handle_input_transcript.assert_awaited_once_with(
        "remote recording", source_game_route_identity=None, audio_message_request_id=request_id,
        audio_message_transcript_final=True,
    )
    assert [call.args[0]["type"] for call in socket.send_json.await_args_list] == ["audio_message_result"]

    await restarted.submit_audio_message(payload, socket, is_current=lambda: True, save_local=True)
    restarted.client.submit_audio_message.assert_not_called()
    assert socket.send_json.call_args.args[0]["state"] == "failed"
    assert history.read_audio(request_id) is None


@pytest.mark.asyncio
async def test_deleting_playback_does_not_discard_the_already_sent_turn(isolated_history):
    manager, socket = Manager(), SimpleNamespace(send_json=AsyncMock())
    payload = message()
    request_id = payload["request_id"]

    async def submit(_pcm, _request_id, *, is_current, on_accepted, on_transcript):
        assert is_current()
        await on_accepted()
        await asyncio.to_thread(isolated_history.delete, request_id)
        assert await on_transcript("keep this turn in memory") is True

    manager.client.submit_audio_message.side_effect = submit
    await manager.submit_audio_message(payload, socket, is_current=lambda: True, save_local=True)

    assert socket.send_json.await_args.args[0]["state"] == "completed"
    assert isolated_history.page(None, 10)["messages"] == []
    assert isolated_history.read_audio(request_id) is None
    manager.handle_input_transcript.assert_awaited_once_with(
        "keep this turn in memory", source_game_route_identity=None, audio_message_request_id=request_id,
        audio_message_transcript_final=True,
    )
    manager.reset_session_start_circuit.assert_called_once()


@pytest.mark.asyncio
async def test_interrupted_no_save_reservation_is_terminal_after_restart(isolated_history, monkeypatch):
    history = isolated_history
    payload = message()
    assert history.reserve(*decode_audio_message(payload), save_local=False)
    monkeypatch.setattr("services.chat_audio_history._PROCESS_ID", uuid.uuid4().hex)
    manager, socket = Manager(), SimpleNamespace(send_json=AsyncMock())
    await manager.submit_audio_message(payload, socket, is_current=lambda: True)
    assert manager.starts == 0
    assert socket.send_json.call_args.args[0] == {
        "type": "audio_message_result", "request_id": payload["request_id"],
        "state": "failed", "code": "unconfirmed",
    }


@pytest.mark.asyncio
async def test_legacy_sent_receipt_preserves_audio_without_claiming_a_reply(isolated_history):
    history = isolated_history
    payload = message()
    request_id = payload["request_id"]
    assert history.reserve(*decode_audio_message(payload))
    assert history.update(request_id, state="sent")

    manager, socket = Manager(), SimpleNamespace(send_json=AsyncMock())
    await manager.submit_audio_message(payload, socket, is_current=lambda: True)
    manager.client.submit_audio_message.assert_not_called()
    assert socket.send_json.call_args.args[0]["state"] == "failed"
    assert history.page(None, 1)["messages"][0]["state"] == "sent"
    assert history.read_audio(request_id) is not None


@pytest.mark.asyncio
async def test_completion_is_unconfirmed_when_the_final_receipt_cannot_be_saved(isolated_history, monkeypatch):
    history = isolated_history
    manager, restarted = Manager(), Manager()
    socket = SimpleNamespace(send_json=AsyncMock())
    payload = message()
    lock = None
    blocked = asyncio.Event()

    async def submit(*_args, on_accepted, **_kwargs):
        nonlocal lock
        await on_accepted()
        lock = sqlite3.connect(history.path)
        lock.execute("BEGIN IMMEDIATE")
        blocked.set()

    manager.client.submit_audio_message.side_effect = submit
    first = asyncio.create_task(manager.submit_audio_message(payload, socket, is_current=lambda: True))
    try:
        await asyncio.wait_for(blocked.wait(), timeout=2)
        await manager.submit_audio_message(payload, socket, is_current=lambda: True)
        await first
    finally:
        if lock is not None:
            lock.close()
        await asyncio.gather(first, return_exceptions=True)

    monkeypatch.setattr("services.chat_audio_history._PROCESS_ID", uuid.uuid4().hex)
    await restarted.submit_audio_message(payload, socket, is_current=lambda: True)
    results = [call.args[0] for call in socket.send_json.await_args_list]
    assert [(item["state"], item["code"]) for item in results] == [
        ("sending", ""), ("failed", "unconfirmed"), ("failed", "unconfirmed"),
    ]
    manager.client.submit_audio_message.assert_awaited_once()
    restarted.client.submit_audio_message.assert_not_called()


@pytest.mark.asyncio
async def test_storage_failure_precedes_model_start(tmp_path, monkeypatch):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("blocked")
    history = ChatAudioHistory(blocked, "local-yui")
    monkeypatch.setattr("main_logic.core.audio_message.history_for_character", AsyncMock(return_value=history))
    manager = Manager()
    socket = SimpleNamespace(send_json=AsyncMock())
    payload = message()
    await manager.submit_audio_message(payload, socket, is_current=lambda: True)
    assert manager.starts == 0
    assert manager._audio_message_records == {}
    assert socket.send_json.call_args.args[0]["code"] == "storage"


@pytest.mark.asyncio
async def test_duplicate_after_audio_acceptance_and_after_reply_completion_submits_once():
    manager, socket = Manager(), SimpleNamespace(send_json=AsyncMock())
    entered, finish = asyncio.Event(), asyncio.Event()

    async def submit(pcm, request_id, *, is_current, on_accepted, on_transcript):
        assert is_current()
        await on_accepted()
        entered.set()
        await finish.wait()

    manager.client.submit_audio_message.side_effect = submit
    payload = message()
    first = asyncio.create_task(manager.submit_audio_message(payload, socket, is_current=lambda: True))
    await entered.wait()
    await manager.submit_audio_message(payload, socket, is_current=lambda: True)
    assert socket.send_json.call_args.args[0]["state"] == "sending"
    finish.set()
    await first
    await manager.submit_audio_message(payload, socket, is_current=lambda: True)
    assert socket.send_json.call_args.args[0]["state"] == "completed"
    assert manager.starts == 1
    manager.client.submit_audio_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_uncertain_failure_is_terminal_and_never_replays():
    manager, socket = Manager(), SimpleNamespace(send_json=AsyncMock())
    manager.client.submit_audio_message.side_effect = ConnectionError("after provider write")
    payload = message()
    await manager.submit_audio_message(payload, socket, is_current=lambda: True)
    await manager.submit_audio_message(payload, socket, is_current=lambda: True)
    assert manager.starts == 1
    manager.client.submit_audio_message.assert_awaited_once()
    assert socket.send_json.call_args.args[0]["state"] == "failed"
    manager.request_end_session.assert_called_once_with(by_server=True)


@pytest.mark.asyncio
async def test_old_failed_submit_never_retires_successor():
    manager, socket = Manager(), SimpleNamespace(send_json=AsyncMock())
    successor = object()

    async def replace(*args, **kwargs):
        manager.session = successor
        raise ConnectionError("connection taken over")

    manager.client.submit_audio_message.side_effect = replace
    await manager.submit_audio_message(message(), socket, is_current=lambda: True)
    assert manager.session is successor
    manager.request_end_session.assert_not_called()


@pytest.mark.asyncio
async def test_superseded_start_never_uses_or_closes_successor():
    manager, socket = Manager(), SimpleNamespace(send_json=AsyncMock())
    original = manager.start_session

    async def start(*args, **kwargs):
        await original(*args, **kwargs)
        manager._start_operation.request_id = "successor"

    manager.start_session = start
    await manager.submit_audio_message(message(), socket, is_current=lambda: True)
    manager.client.submit_audio_message.assert_not_called()
    manager.request_end_session.assert_not_called()


@pytest.mark.asyncio
async def test_id_cannot_be_reused_with_different_audio():
    manager, socket = Manager(), SimpleNamespace(send_json=AsyncMock())
    payload = message()
    await manager.submit_audio_message(payload, socket, is_current=lambda: True)
    payload["audio"] = message(b"\x02\x00" * 8000)["audio"]
    await manager.submit_audio_message(payload, socket, is_current=lambda: True)
    assert socket.send_json.call_args.args[0]["code"] == "id_conflict"
    assert manager.starts == 1


@pytest.mark.asyncio
async def test_websocket_route_delivers_duplicate_audio_id_once(monkeypatch):
    class Socket:
        client = "test-client"

        def __init__(self):
            self.inbound = asyncio.Queue()
            self.outbound = asyncio.Queue()

        async def accept(self):
            pass

        async def receive(self):
            return await self.inbound.get()

        async def send_json(self, payload):
            self.outbound.put_nowait(payload)

    manager = Manager()
    manager.websocket = None
    manager.pending_agent_callbacks = []
    manager.cleanup = AsyncMock()
    socket = Socket()
    session_ids = {}
    monkeypatch.setattr(websocket_router, "get_config_manager", lambda: object())
    monkeypatch.setattr(websocket_router, "get_session_manager", lambda: {"YUI": manager})
    monkeypatch.setattr(websocket_router, "get_session_id", lambda: session_ids)
    endpoint = asyncio.create_task(websocket_router.websocket_endpoint(socket, "YUI"))
    payload = {"action": "audio_message", **message()}
    frame = {"type": "websocket.receive", "text": json.dumps(payload)}
    try:
        socket.inbound.put_nowait(frame)
        socket.inbound.put_nowait(frame)
        replies = [await asyncio.wait_for(socket.outbound.get(), 2) for _ in range(2)]
        assert all(reply["request_id"] == payload["request_id"] for reply in replies)
        assert {reply["state"] for reply in replies} <= {"sending", "completed"}
        assert "completed" in {reply["state"] for reply in replies}
        manager.client.submit_audio_message.assert_awaited_once()
    finally:
        socket.inbound.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(endpoint, 2)


@pytest.mark.parametrize("pcm", [b"", b"x" * 3201, b"x" * (MAX_AUDIO_BYTES + 2)], ids=["empty", "odd", "oversized"])
def test_rejects_empty_odd_and_oversized_recordings(pcm):
    with pytest.raises(ValueError, match="invalid"):
        decode_audio_message(message(pcm))


def test_expired_id_stays_rejected_after_receipt_eviction():
    payload = message()
    payload["request_id"] = f"{int((time.time() - 1800) * 1000)}-{uuid.uuid4()}"
    with pytest.raises(ValueError, match="expired"):
        decode_audio_message(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "rejected", "locked-transcript", "tool-continuation"])
async def test_real_arbiter_waits_for_audio_commit_before_exactly_one_response(
    outcome, isolated_history, monkeypatch,
):
    reject = outcome == "rejected"
    locked_transcript = outcome == "locked-transcript"
    uses_tool = outcome == "tool-continuation"
    from main_logic.tool_calling import ToolResult

    tool_started, release_tool, continuation_sent = asyncio.Event(), asyncio.Event(), asyncio.Event()
    turn_done = AsyncMock()

    async def tool(call):
        tool_started.set()
        await release_tool.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient("wss://example.invalid", "test", model="qwen-test", api_type="qwen",
                                turn_detection_mode=TurnDetectionMode.MANUAL, on_input_transcript=AsyncMock(),
                                on_tool_call=tool, on_response_done=turn_done)
    events = []
    commit_sent = asyncio.Event()
    response_sent = asyncio.Event()
    transcript_received = asyncio.Event()

    class Peer:
        closed = False

        def __init__(self):
            self.inbound = asyncio.Queue()

        def feed(self, event):
            self.inbound.put_nowait(json.dumps(event))

        async def send(self, payload):
            event = json.loads(payload)
            events.append(event)
            if event["type"] == "input_audio_buffer.commit":
                commit_sent.set()
            if event["type"] == "response.create":
                if uses_tool and response_sent.is_set():
                    self.feed({"type": "response.created", "response": {"id": "response-two"}})
                    continuation_sent.set()
                    return
                self.feed({"type": "response.created", "response": {"id": "response-one"}})
                response_sent.set()
                if uses_tool:
                    self.feed({
                        "type": "response.function_call_arguments.done", "response_id": "response-one",
                        "name": "lookup", "call_id": "lookup-one", "arguments": "{}",
                    })
                elif not locked_transcript:
                    self.finish_reply()

        def finish_reply(self):
            self.feed({"type": "response.audio_transcript.delta", "delta": "hello", "response_id": "response-one"})
            self.feed({"type": "response.done", "response": {"id": "response-one", "status": "completed"}})

        async def close(self):
            self.closed = True
            self.inbound.put_nowait(None)

        async def __aiter__(self):
            while (item := await self.inbound.get()) is not None:
                yield item

    peer = Peer()
    client.ws = peer
    receiver = asyncio.create_task(client.handle_messages())
    peer.feed({"type": "session.updated", "session": {"id": "session-one", "input_audio_format": "pcm16"}})
    pcm = b"\x01\x00" * 128000
    if locked_transcript or uses_tool:
        manager = Manager()
        manager.client = client
        manager.handle_input_transcript = AsyncMock(return_value=True)
        socket = SimpleNamespace(send_json=AsyncMock())
        payload = message(pcm)
        deliver = client._deliver_input_transcript

        async def observe_transcript(*args, **kwargs):
            await deliver(*args, **kwargs)
            transcript_received.set()

        monkeypatch.setattr(client, "_deliver_input_transcript", observe_transcript)
        task = asyncio.create_task(manager.submit_audio_message(payload, socket, is_current=lambda: True, save_local=True))
    else:
        task = asyncio.create_task(client.submit_audio_message(pcm, str(uuid.uuid4()), is_current=lambda: True))
    try:
        await asyncio.wait_for(commit_sent.wait(), 2)
        assert [event["type"] for event in events] == ["input_audio_buffer.append"] * 8 + ["input_audio_buffer.commit"]
        if reject:
            peer.feed({"type": "error", "error": {"message": "rejected", "event_id": events[-1]["event_id"]}})
        else:
            peer.feed({"type": "error", "error": {"message": "unrelated error", "event_id": "other-event"}})
            peer.feed({"type": "input_audio_buffer.committed", "item_id": "audio-one"})
        if uses_tool:
            await asyncio.wait_for(tool_started.wait(), 2)
            peer.feed({
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "audio-one", "transcript": "Check tomorrow's schedule.",
            })
            peer.feed({"type": "response.done", "response": {"id": "response-one", "status": "completed"}})
            await asyncio.wait_for(transcript_received.wait(), 2)
            assert not task.done()
            turn_done.assert_not_awaited()
            release_tool.set()
            await asyncio.wait_for(continuation_sent.wait(), 2)
            assert not task.done()
            assert [event["item"]["call_id"] for event in events if event["type"] == "conversation.item.create"] == [
                "lookup-one",
            ]
            peer.feed({"type": "response.audio_transcript.delta", "delta": "hello", "response_id": "response-two"})
            peer.feed({"type": "response.done", "response": {"id": "response-two", "status": "completed"}})
        if locked_transcript:
            await asyncio.wait_for(response_sent.wait(), 2)
            writer = sqlite3.connect(isolated_history.path)
            writer.execute("BEGIN IMMEDIATE")
            try:
                peer.feed({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": "audio-one", "transcript": "Check tomorrow's schedule.",
                })
                await asyncio.wait_for(transcript_received.wait(), 4)
                assert not client._audio_message_transcript.published_final
            finally:
                writer.close()
            peer.finish_reply()
        if reject:
            with pytest.raises(Exception):
                await asyncio.wait_for(task, 3)
        else:
            await asyncio.wait_for(task, 3)
        if locked_transcript or uses_tool:
            assert socket.send_json.await_args.args[0]["state"] == "completed"
            assert isolated_history.receipt(payload["request_id"])["transcript"] == "Check tomorrow's schedule."
            assert client._audio_message_transcript.published_final
            manager.handle_input_transcript.assert_awaited_once()
        if locked_transcript:
            client.ws = Peer()
            client._on_connection_attached()
            await client._deliver_input_transcript("New connection", item_id="next-item")
            client.on_input_transcript.assert_awaited_once_with("New connection")
        if uses_tool:
            turn_done.assert_awaited_once()
        assert sum(event["type"] == "response.create" for event in events) == (0 if reject else 2 if uses_tool else 1)
        assert b"".join(base64.b64decode(event["audio"]) for event in events if event["type"] == "input_audio_buffer.append") == pcm
    finally:
        release_tool.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await peer.close()
        await asyncio.wait_for(receiver, 3)
        await client.close()


@pytest.mark.asyncio
async def test_server_vad_session_cannot_submit_a_recording():
    client = OmniRealtimeClient("wss://example.invalid", "test", model="qwen-test")
    with pytest.raises(RuntimeError, match="manual"):
        await client.submit_audio_message(b"\x00\x00" * 8000, "id", is_current=lambda: True)


@pytest.mark.asyncio
@pytest.mark.parametrize("session", [{}, {"id": "session-one", "input_audio_format": "pcm16",
                                         "turn_detection": {"type": "server_vad"}}])
async def test_manual_mode_rejected_by_provider_leaves_the_recording_unsent(session):
    client = OmniRealtimeClient("wss://example.invalid", "test", model="qwen-test", api_type="qwen",
                                turn_detection_mode=TurnDetectionMode.MANUAL)
    client.send_event = AsyncMock()
    task = asyncio.create_task(client.submit_audio_message(b"\x01\x00" * 8000, "id", is_current=lambda: True))
    try:
        await asyncio.sleep(0)
        client._observe_audio_message_event({"type": "session.updated", "session": session})
        with pytest.raises(ConnectionError, match="did not confirm manual"):
            await asyncio.wait_for(task, 2)
        client.send_event.assert_not_called()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("transcript_final", [
    "before_reply_end", "after_reply_end", "markerless", "late_markerless", "interrupted",
])
async def test_gemini_recording_has_one_start_audio_end_sequence(monkeypatch, transcript_final):
    from google.genai import types
    from utils.token_tracker import TokenTracker

    monkeypatch.setattr("main_logic.omni_realtime_client._gemini_support.types", types)
    monkeypatch.setattr(TokenTracker, "get_instance", lambda: SimpleNamespace(record=Mock()))
    client = OmniRealtimeClient("wss://example.invalid", "test", model="gemini-test",
                                api_type="gemini", turn_detection_mode=TurnDetectionMode.MANUAL)
    events = []
    completed = AsyncMock()
    accepted = AsyncMock()
    transcript = AsyncMock(return_value=True)
    client.on_response_done = completed

    async def receive(**fields):
        await client._process_gemini_response(
            types.LiveServerMessage(server_content=types.LiveServerContent(**fields)),
            provider_session=peer, connection_generation=client._connection_generation,
        )

    async def send_realtime_input(**kwargs):
        events.append(kwargs)
        if "activity_end" in kwargs:
            if transcript_final == "interrupted":
                await receive(interrupted=True)
                await receive(turn_complete=True)
                return
            markerless = transcript_final in ("markerless", "late_markerless")
            await receive(input_transcription=types.Transcription(
                text="Gemini ", finished=None if markerless else False,
            ))
            await receive(output_transcription=types.Transcription(text="Reply starts"))
            transcript.assert_not_awaited()
            if transcript_final not in ("after_reply_end", "late_markerless"):
                await receive(input_transcription=types.Transcription(
                    text="recording", finished=None if markerless else True,
                ))
            await receive(turn_complete=True)

    peer = SimpleNamespace(send_realtime_input=send_realtime_input)
    client._gemini_session = client.ws = peer
    client._on_connection_attached()
    pcm = b"\x01\x00" * 8000
    submission = client.submit_audio_message(
        pcm, str(uuid.uuid4()), is_current=lambda: True, on_accepted=accepted, on_transcript=transcript,
    )
    if transcript_final == "interrupted":
        with pytest.raises(ConnectionError, match="interrupted"):
            await submission
        accepted.assert_not_awaited()
        return
    await submission
    if transcript_final == "late_markerless":
        transcript.assert_awaited_once_with("Gemini ", final=False)
        await receive(input_transcription=types.Transcription(text="recording"))
        assert [call.args[0] for call in transcript.await_args_list] == ["Gemini ", "Gemini recording"]
        accepted.assert_awaited_once()
        return
    if transcript_final == "after_reply_end":
        transcript.assert_awaited_once_with("Gemini ", final=False)
        await receive(input_transcription=types.Transcription(text="recording", finished=True))
    if transcript_final != "markerless":
        await receive(input_transcription=types.Transcription(text="Duplicate Gemini transcript", finished=True))
    assert [list(event) for event in events] == [["activity_start"], ["audio"], ["activity_end"]]
    assert events[1]["audio"] == {"data": pcm, "mime_type": "audio/pcm;rate=16000"}
    completed.assert_awaited_once()
    accepted.assert_awaited_once()
    if transcript_final == "after_reply_end":
        assert transcript.await_count == 2
        transcript.assert_awaited_with("Gemini recording", final=True)
    else:
        transcript.assert_awaited_once_with("Gemini recording", final=transcript_final != "markerless")


@pytest.mark.asyncio
@pytest.mark.parametrize("continuation", ["text", "silent", "send-fails", "send-fails-after-reply", "send-fails-after-replacement"])
async def test_gemini_audio_receipt_waits_for_reply_after_tools(monkeypatch, continuation):
    from google.genai import types
    from main_logic.omni_realtime_client._audio import _GeminiAudioMessage
    from main_logic.tool_calling import ToolResult
    from tests.unit.test_realtime_tool_ownership import _GeminiSession, _gemini_response, _wait_for_tool_tasks
    from utils.token_tracker import TokenTracker

    monkeypatch.setattr("main_logic.omni_realtime_client._gemini_support.types", types)
    monkeypatch.setattr(TokenTracker, "get_instance", lambda: SimpleNamespace(record=Mock()))
    entered, release, early_terminal = asyncio.Event(), asyncio.Event(), asyncio.Event()
    accepted, completed = AsyncMock(), AsyncMock()
    replacement = _GeminiAudioMessage(asyncio.get_running_loop().create_future(), on_transcript=None)

    async def call_tool(call):
        entered.set()
        await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = OmniRealtimeClient(
        "wss://example.invalid", "test", api_type="gemini", turn_detection_mode=TurnDetectionMode.MANUAL,
        on_tool_call=call_tool, on_response_done=completed,
    )
    peer = _GeminiSession()

    async def receive(event=None, **fields):
        await client._process_gemini_response(
            event or types.LiveServerMessage(server_content=types.LiveServerContent(**fields)),
            provider_session=peer, connection_generation=client._connection_generation,
        )

    send_tool_response = peer.send_tool_response

    async def send_tool(**fields):
        await send_tool_response(**fields)
        if continuation == "send-fails-after-reply":
            await receive(output_transcription=types.Transcription(text="The result is ready."), turn_complete=True)
        if continuation == "send-fails-after-replacement":
            client._gemini_audio_message = replacement
        if continuation.startswith("send-fails"):
            raise ConnectionError("provider connection closed during the tool write")

    peer.send_tool_response = send_tool

    async def send(**fields):
        if "activity_end" in fields:
            await receive(_gemini_response(calls=(("call-1", "lookup"),)))
            await entered.wait()
            await receive(turn_complete=True)
            early_terminal.set()

    peer.send_realtime_input = send
    client._gemini_session = client.ws = peer
    client._on_connection_attached()
    task = asyncio.create_task(client.submit_audio_message(
        b"\x01\x00" * 8000, str(uuid.uuid4()), is_current=lambda: True, on_accepted=accepted,
    ))
    try:
        await asyncio.wait_for(early_terminal.wait(), 2)
        accepted.assert_not_awaited()
        completed.assert_not_awaited()
        assert not task.done() and not peer.tool_responses
        release.set()
        await _wait_for_tool_tasks(client)
        assert len(peer.tool_responses) == 1
        if continuation == "send-fails-after-replacement":
            assert not client._gemini_audio_message.outcome.done()
            assert not task.done()
            return
        if continuation == "send-fails":
            with pytest.raises(ConnectionError, match="Gemini audio message"):
                await asyncio.wait_for(asyncio.shield(task), 2)
            accepted.assert_not_awaited()
            completed.assert_not_awaited()
            assert len(peer.tool_responses) == 1
            return
        if continuation != "send-fails-after-reply":
            await receive(turn_complete=True)
            assert not task.done()
            if continuation == "silent":
                await receive(generation_complete=True)
                assert not task.done()
                await receive(turn_complete=True)
            else:
                await receive(output_transcription=types.Transcription(text="The result is ready."), turn_complete=True)
        await asyncio.wait_for(task, 2)
        accepted.assert_awaited_once()
        completed.assert_awaited_once()
    finally:
        replacement.outcome.cancel()
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await _wait_for_tool_tasks(client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary", [
        "session-retirement", "provider-final", "shutdown", "queued-session-end",
        "failed-before-send", "storage-startup-rejected", "publication-guard", "lost-cache-response",
        "lost-session-end-response", "no-final", "no-transcript", "flush-failed", "clear-during-monitor",
    ],
)
async def test_gemini_audio_memory_uses_the_latest_transcript_before_its_reply(
    isolated_history, monkeypatch, boundary,
):
    from google.genai import types
    from app.main_server import character_runtime
    from main_logic import cross_server
    from main_logic.core.turn import TurnMixin
    from tests.unit.test_core_game_route_memory_contract import _make_transcript_manager
    from utils.token_tracker import TokenTracker

    bounded = boundary in {"no-final", "no-transcript"}
    monkeypatch.setattr("main_logic.omni_realtime_client._gemini_support.types", types)
    monkeypatch.setattr(TokenTracker, "get_instance", lambda: SimpleNamespace(record=Mock()))
    analyze = AsyncMock(return_value=True)
    monkeypatch.setattr(cross_server, "_publish_analyze_request_with_fallback", analyze)
    save_original = AsyncMock(side_effect=lambda *_args, record_id, **_kwargs: record_id)
    monkeypatch.setattr(cross_server, "save_original", save_original)

    monitor_blocked, release_monitor, drain_enqueued = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class SyncQueue(character_runtime._SyncMessageQueue):
        shutdown_result = None

        def put(self, item):
            super().put(item)
            if "_shutdown_memory_done" in item:
                self.shutdown_result = item["_shutdown_memory_done"]
                drain_enqueued.set()

    queue = SyncQueue()
    cached, monitor_end = asyncio.Event(), asyncio.Event()
    settlements = asyncio.Queue()
    writes = []
    forwarded = []
    cache_attempts = 0
    flush_attempts = 0
    pre_send_failures = {"failed-before-send": 2, "lost-session-end-response": 1}.get(boundary, 0)
    loses_response = boundary in {"lost-cache-response", "lost-session-end-response"}

    async def post(endpoint, _name, messages, *, flush_pending=False, **_kwargs):
        nonlocal cache_attempts, flush_attempts
        if flush_pending:
            assert endpoint == "cache" and messages == []
            flush_attempts += 1
            if boundary == "flush-failed":
                return False, "pending recent history not persisted", {}
            return True, "", {}
        if endpoint == "cache":
            cache_attempts += 1
            if cache_attempts <= pre_send_failures:
                raise httpx.ConnectError("temporary memory server connection failure")
            if boundary == "storage-startup-rejected" and cache_attempts <= 2:
                return False, "HTTP 409", {
                    "error_code": "storage_startup_blocked", "ok": False, "limited_mode": True,
                }
            if boundary == "publication-guard" and cache_attempts <= 2:
                return False, "HTTP 409", {"status": "cancelled", "message": "character release in progress"}
            writes.append([(item["role"], item["content"][0]["text"]) for item in messages])
            cached.set()
            if loses_response:
                raise httpx.ReadError("connection dropped after saving")
        elif endpoint in {"settle", "process"}:
            settlements.put_nowait((endpoint, messages))
        return True, "", None

    async def forward(_slot, event):
        if event["type"] == "block_monitor" or (
            boundary == "clear-during-monitor" and event["type"] == "user_transcript"
        ):
            monitor_blocked.set()
            await release_monitor.wait()
            return True
        forwarded.append(event)
        if event["type"] == "turn end":
            monitor_end.set()
        return True

    monkeypatch.setattr(cross_server, "_post_memory_server", post)
    monkeypatch.setattr(cross_server, "_try_send_json", forward)
    manager = Manager()
    memory = _make_transcript_manager()
    memory._broadcast_voice_transcript_observed = AsyncMock()
    memory._should_suppress_dirty_voice_transcript = Mock(return_value=True)
    memory.sync_message_queue = manager.sync_message_queue = queue
    manager.handle_input_transcript = memory.handle_input_transcript

    async def text_delta(text, first):
        await TurnMixin.send_lanlan_response(memory, text, first)

    async def done():
        TurnMixin._queue_turn_end(memory, None)

    client = OmniRealtimeClient(
        "wss://example.invalid", "test", api_type="gemini", turn_detection_mode=TurnDetectionMode.MANUAL,
        on_text_delta=text_delta, on_response_done=done,
    )
    memory.session = manager.client = client

    async def receive(**fields):
        await client._process_gemini_response(
            types.LiveServerMessage(server_content=types.LiveServerContent(**fields)),
            provider_session=peer, connection_generation=client._connection_generation,
        )

    async def send(**fields):
        if "activity_end" in fields:
            if boundary != "no-transcript":
                await receive(input_transcription=types.Transcription(
                    text="Please check tomorrow." if bounded else "Please ",
                ))
            await receive(output_transcription=types.Transcription(text="I'll look it up."))
            await receive(turn_complete=True)

    peer = SimpleNamespace(send_realtime_input=send)
    client._gemini_session = client.ws = peer
    client._on_connection_attached()
    connector = asyncio.create_task(cross_server.run_sync_connector(
        queue, "Lan", config={"monitor": False, "bullet": False},
    ))
    try:
        payload = message()
        socket = SimpleNamespace(send_json=AsyncMock())
        await manager.submit_audio_message(payload, socket, is_current=lambda: True, save_local=True)
        assert socket.send_json.await_args.args[0]["state"] == "completed"
        await asyncio.wait_for(monitor_end.wait(), 2)
        assert [event["type"] for event in forwarded] == ["user_activity", "gemini_response", "turn end"]
        assert writes == []
        save_original.assert_not_awaited()

        if not bounded:
            await receive(input_transcription=types.Transcription(
                text="check tomorrow.", finished=True if boundary in {"provider-final", "clear-during-monitor"} else None,
            ))
        if boundary == "clear-during-monitor":
            await asyncio.wait_for(monitor_blocked.wait(), 2)
            queue.recent_context_version += 1
            queue.put({"type": "system", "data": "session end"})
            release_monitor.set()
            assert await asyncio.wait_for(settlements.get(), 2) == ("settle", [])
            assert writes == []
            analyze.assert_not_awaited()
            return
        if boundary != "no-transcript":
            assert isolated_history.receipt(payload["request_id"])["transcript"] == "Please check tomorrow."
        if boundary == "session-retirement":
            queue.put({"type": "system", "data": "session end"})
        if not bounded and boundary not in {"session-retirement", "provider-final"}:
            queue.put({"type": "json", "data": {"type": "block_monitor"}})
            await asyncio.wait_for(monitor_blocked.wait(), 2)
            if boundary != "shutdown":
                queue.put({"type": "system", "data": "session end"})
            role = SimpleNamespace(sync_task=connector, sync_message_queue=queue)
            monkeypatch.setattr(character_runtime, "role_state", {"Lan": role})
            drain = asyncio.create_task(character_runtime.drain_sync_connector_memory())
            await asyncio.wait_for(drain_enqueued.wait(), 2)
            release_monitor.set()
            await asyncio.wait_for(drain, 2)
            assert cached.is_set()
            assert flush_attempts == 1
            assert cache_attempts == (3 if boundary in {"storage-startup-rejected", "publication-guard"} else pre_send_failures + 1)
            assert queue.shutdown_result.result() is (not loses_response and boundary != "flush-failed")
            character_runtime.cleanup(log=False)
            assert not await character_runtime.join_sync_connector_tasks(2)
            assert connector.cancelled()
        await asyncio.wait_for(cached.wait(), 2)
        assert len(writes) == 1
        if boundary == "no-transcript":
            assert [role for role, _text in writes[0]] == ["assistant"]
            save_original.assert_not_awaited()
        else:
            assert [role for role, _text in writes[0]] == ["user", "assistant"]
            assert writes[0][0][1] == "Please check tomorrow."
            assert save_original.await_count == 1
            assert save_original.await_args.args == ("Lan", "user", "Please check tomorrow.")
            assert save_original.await_args.kwargs["character_uid"] == isolated_history.character_uid
        assert writes[0][-1][1].endswith("I'll look it up.")
        assert [event["type"] for event in forwarded].count("turn end") == 1
        if bounded:
            analyze.assert_awaited_once()
            await receive(input_transcription=types.Transcription(text=" Late correction.", finished=True))
            assert isolated_history.receipt(payload["request_id"])["transcript"].endswith("Late correction.")
            assert len(writes) == 1
            assert save_original.await_count == int(boundary != "no-transcript")
    finally:
        release_monitor.set()
        connector.cancel()
        await asyncio.gather(connector, return_exceptions=True)


@pytest.mark.asyncio
async def test_memory_publication_guard_is_the_only_retryable_cancelled_409(monkeypatch):
    from main_logic import cross_server

    post = AsyncMock(side_effect=[
        httpx.Response(409, json={"status": "cancelled", "message": "character release in progress"}),
        httpx.Response(409, json={"status": "cancelled", "message": "write result unknown"}),
    ])
    monkeypatch.setattr(cross_server, "get_internal_http_client", lambda: SimpleNamespace(post=post))
    for retryable in (True, False):
        success, _, payload = await cross_server._post_memory_server("cache", "Lan", [], timeout_s=1)
        assert success is False
        assert cross_server._memory_write_rejected_before_persistence(payload) is retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("read_before_clear", [False, True])
async def test_audio_memory_discards_a_recording_queued_before_context_clear(monkeypatch, read_before_clear):
    from main_logic import cross_server

    forward = AsyncMock()
    monkeypatch.setattr(cross_server, "_try_send_json", forward)
    queue = asyncio.Queue()
    queue.recent_context_version = 0 if read_before_clear else 1
    old_reply = {"type": "gemini_response", "data": "old reply"}
    new_input = {"type": "user", "data": {"input_type": "text", "data": "new context"}}
    queue.put_nowait({"type": "audio_message_memory_start", "request_id": "old", "context_version": 0})
    queue.put_nowait({
        "type": "audio_message_memory_transcript", "request_id": "old", "final": True, "data": {"data": "old recording"},
    })
    async with contextlib.aclosing(cross_server._order_audio_memory_events(queue, None)) as events:
        if read_before_clear:
            barrier = {"type": "json", "data": {"type": "barrier"}}
            queue.put_nowait(barrier)
            assert await asyncio.wait_for(anext(events), 1) == (barrier, False)
            queue.recent_context_version = 1
        for message in (
            {"type": "json", "data": old_reply},
            {"type": "system", "data": "turn end"},
            new_input,
        ):
            queue.put_nowait(message)
        assert await asyncio.wait_for(anext(events), 1) == (new_input, False)
    assert [call.args[1]["type"] for call in forward.await_args_list] == ["user_activity", "gemini_response", "turn end"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timing", ["before-completion", "after-completion", "disconnect-before-retry", "cancel-receiver-during-write"],
)
async def test_gemini_retries_final_transcript_after_sqlite_unlock(isolated_history, monkeypatch, timing):
    from google.genai import types
    from utils.token_tracker import TokenTracker

    monkeypatch.setattr("main_logic.omni_realtime_client._gemini_support.types", types)
    monkeypatch.setattr(TokenTracker, "get_instance", lambda: SimpleNamespace(record=Mock()))
    manager = Manager()
    manager.handle_input_transcript = AsyncMock(return_value=True)
    response_done = AsyncMock()
    client = manager.client = OmniRealtimeClient(
        "wss://example.invalid", "test", api_type="gemini", turn_detection_mode=TurnDetectionMode.MANUAL,
        on_text_delta=AsyncMock(), on_response_done=response_done,
    )
    transcript = "Check tomorrow's schedule."
    current = True
    disconnects = timing in {"disconnect-before-retry", "cancel-receiver-during-write"}

    async def receive(**fields):
        await client._process_gemini_response(
            types.LiveServerMessage(server_content=types.LiveServerContent(**fields)),
            provider_session=peer, connection_generation=client._connection_generation,
        )

    async def receive_while_locked():
        nonlocal current
        writer = sqlite3.connect(isolated_history.path)
        writer.execute("BEGIN IMMEDIATE")
        try:
            event = types.Transcription(text=transcript, finished=True)
            if timing == "cancel-receiver-during-write":
                started = asyncio.Event()
                loop = asyncio.get_running_loop()
                update = isolated_history.update

                def observe_write(request_id, **changes):
                    if "transcript" in changes:
                        loop.call_soon_threadsafe(started.set)
                    return update(request_id, **changes)

                monkeypatch.setattr(isolated_history, "update", observe_write)
                receiver = asyncio.create_task(receive(input_transcription=event))
                try:
                    await asyncio.wait_for(started.wait(), 2)
                    current = False
                    receiver.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(receiver, 4)
                finally:
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
            else:
                await receive(input_transcription=event)
            assert not client._gemini_audio_message.published_final
            if disconnects:
                current = False
        finally:
            writer.close()

    async def send(**fields):
        if "activity_end" in fields:
            if timing == "before-completion":
                await receive_while_locked()
            await receive(output_transcription=types.Transcription(text="Understood."))
            await receive(turn_complete=True)

    peer = SimpleNamespace(send_realtime_input=AsyncMock(side_effect=send))
    client._gemini_session = client.ws = peer
    client._on_connection_attached()
    socket = SimpleNamespace(send_json=AsyncMock())
    payload = message()
    await manager.submit_audio_message(payload, socket, is_current=lambda: current, save_local=True)
    assert socket.send_json.await_args.args[0]["state"] == "completed"
    if timing != "before-completion":
        await receive_while_locked()
        await asyncio.wait_for(client._gemini_audio_message.transcript_retry, 2)
    row = isolated_history.receipt(payload["request_id"])
    assert row["state"] == "completed"
    assert row["transcript"] == transcript
    assert client._gemini_audio_message.published_final
    # The transcript arrived while the original turn owned it. Losing the
    # route while SQLite is busy must not republish it to a replacement turn.
    manager.handle_input_transcript.assert_awaited_once_with(
        transcript, source_game_route_identity=None, audio_message_request_id=payload["request_id"],
        audio_message_transcript_final=True,
    )
    assert peer.send_realtime_input.await_count == 3
    response_done.assert_awaited_once()
