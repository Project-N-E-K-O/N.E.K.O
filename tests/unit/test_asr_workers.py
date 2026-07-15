from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from main_logic.asr_client._infra import (
    AsrSessionConfig,
    _AsrWorkerEvent,
    _AsrWorkerRequest,
)
from main_logic.asr_client.workers import grok, openai, qwen, soniox, step


_END = object()


class _FakeWebSocket:
    def __init__(
        self,
        *,
        initial: list[dict[str, Any]] | None = None,
        on_send: Callable[["_FakeWebSocket", str | bytes], Awaitable[None]]
        | None = None,
    ) -> None:
        self.incoming: asyncio.Queue[str | object] = asyncio.Queue()
        self.sent: list[str | bytes] = []
        self.closed = False
        self.on_send = on_send
        for event in initial or []:
            self.incoming.put_nowait(json.dumps(event))

    async def send(self, payload: str | bytes) -> None:
        if self.closed:
            raise RuntimeError("fake websocket is closed")
        self.sent.append(payload)
        if self.on_send is not None:
            await self.on_send(self, payload)

    async def recv(self) -> str:
        message = await self.incoming.get()
        if message is _END:
            raise RuntimeError("fake websocket closed before ready")
        assert isinstance(message, str)
        return message

    def __aiter__(self) -> _FakeWebSocket:
        return self

    async def __anext__(self) -> str:
        message = await self.incoming.get()
        if message is _END:
            raise StopAsyncIteration
        assert isinstance(message, str)
        return message

    async def server_send(self, event: dict[str, Any]) -> None:
        await self.incoming.put(json.dumps(event))

    async def server_end(self) -> None:
        await self.incoming.put(_END)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.incoming.put(_END)


class _FakeConnector:
    def __init__(self, *websockets: _FakeWebSocket) -> None:
        self.websockets = list(websockets)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, url: str, **kwargs: Any) -> _FakeWebSocket:
        self.calls.append((url, kwargs))
        if not self.websockets:
            raise AssertionError("unexpected extra WebSocket connection")
        return self.websockets.pop(0)


async def _next_event(
    queue: asyncio.Queue[_AsrWorkerEvent],
    kind: str | None = None,
    *,
    timeout: float = 1.0,
) -> _AsrWorkerEvent:
    while True:
        event = await asyncio.wait_for(queue.get(), timeout)
        queue.task_done()
        if kind is None or event.kind == kind:
            return event


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 1.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("condition was not reached")
        await asyncio.sleep(0)


async def _stop_worker(
    task: asyncio.Task[None],
    requests: asyncio.Queue[_AsrWorkerRequest],
    responses: asyncio.Queue[_AsrWorkerEvent],
    *,
    generation: int = 0,
    buffer_epoch: int = 0,
    utterance_id: int = 1,
) -> _AsrWorkerEvent:
    await requests.put(
        _AsrWorkerRequest(
            kind="shutdown",
            generation=generation,
            buffer_epoch=buffer_epoch,
            utterance_id=utterance_id,
        )
    )
    closed = await _next_event(responses, "closed")
    await asyncio.wait_for(task, 1)
    await asyncio.wait_for(requests.join(), 1)
    return closed


@pytest.mark.parametrize(
    ("region", "domain", "legacy_without_item_ids"),
    [
        ("cn", "dashscope.aliyuncs.com", True),
        ("intl", "dashscope-intl.aliyuncs.com", False),
    ],
)
async def test_qwen_manual_regions_payload_and_final(
    monkeypatch,
    region: str,
    domain: str,
    legacy_without_item_ids: bool,
) -> None:
    commit_count = 0

    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        nonlocal commit_count
        assert isinstance(payload, str)
        message = json.loads(payload)
        if message["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif message["type"] == "input_audio_buffer.commit":
            commit_count += 1
            item_id = f"qwen-{commit_count}"
            if legacy_without_item_ids:
                await ws.server_send(
                    {
                        "type": "conversation.item.created",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_audio"}],
                        },
                    }
                )
                await ws.server_send({"type": "input_audio_buffer.committed"})
            else:
                await ws.server_send(
                    {"type": "input_audio_buffer.committed", "item_id": item_id}
                )
            await ws.server_send(
                {
                    "type": "conversation.item.input_audio_transcription.text",
                    "text": "你",
                    "stash": "好",
                    **({} if legacy_without_item_ids else {"item_id": item_id}),
                }
            )
            await ws.server_send(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "你好",
                    **({} if legacy_without_item_ids else {"item_id": item_id}),
                }
            )
        elif message["type"] == "session.finish":
            await ws.server_send({"type": "session.finished"})

    websocket = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(qwen.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        qwen.qwen_asr_worker(
            requests,
            responses,
            "secret-qwen-key",
            AsrSessionConfig(language="zh-CN"),
            region=region,
        )
    )

    assert (await _next_event(responses, "ready")).generation == 0
    pcm = b"\x01\x02" * 320
    await requests.put(
        _AsrWorkerRequest(kind="audio", generation=0, utterance_id=1, audio=pcm)
    )
    await requests.put(_AsrWorkerRequest(kind="commit", generation=0, utterance_id=1))

    partial = await _next_event(responses, "partial")
    final = await _next_event(responses, "final")
    assert (partial.text, final.text, final.utterance_id) == ("你好", "你好", 1)
    assert not task.done(), "commit must keep the provider connection open"

    url, kwargs = connector.calls[0]
    assert urlparse(url).hostname == domain
    assert parse_qs(urlparse(url).query)["model"] == ["qwen3-asr-flash-realtime"]
    assert kwargs["additional_headers"] == {"Authorization": "Bearer secret-qwen-key"}
    messages = [json.loads(payload) for payload in websocket.sent]
    session = next(
        message for message in messages if message["type"] == "session.update"
    )
    assert session["session"]["sample_rate"] == 16_000
    assert session["session"]["turn_detection"] is None
    append = next(
        message
        for message in messages
        if message["type"] == "input_audio_buffer.append"
    )
    assert base64.b64decode(append["audio"]) == pcm

    await _stop_worker(task, requests, responses)


async def test_qwen_manual_duplicate_item_created_does_not_consume_next_commit(
    monkeypatch,
) -> None:
    commit_count = 0

    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        nonlocal commit_count
        assert isinstance(payload, str)
        message = json.loads(payload)
        if message["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif message["type"] == "input_audio_buffer.commit":
            commit_count += 1
        elif message["type"] == "session.finish":
            await ws.server_send({"type": "session.finished"})

    websocket = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(qwen.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        qwen.qwen_asr_worker(
            requests, responses, "key", AsrSessionConfig(), region="intl"
        )
    )
    await _next_event(responses, "ready")
    for utterance_id in (1, 2):
        await requests.put(
            _AsrWorkerRequest(
                kind="audio", generation=0, utterance_id=utterance_id, audio=b"\0\0"
            )
        )
        await requests.put(
            _AsrWorkerRequest(kind="commit", generation=0, utterance_id=utterance_id)
        )
    await _wait_until(lambda: commit_count == 2)
    created = {
        "type": "conversation.item.created",
        "item": {"id": "item-a", "type": "message", "role": "user"},
    }
    await websocket.server_send(created)
    await websocket.server_send(created)
    await websocket.server_send(
        {"type": "input_audio_buffer.committed", "item_id": "item-b"}
    )
    for item_id, transcript in (("item-a", "first"), ("item-b", "second")):
        await websocket.server_send(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": item_id,
                "transcript": transcript,
            }
        )
    first = await _next_event(responses, "final")
    second = await _next_event(responses, "final")
    assert (first.utterance_id, first.text) == (1, "first")
    assert (second.utterance_id, second.text) == (2, "second")
    await _stop_worker(task, requests, responses, utterance_id=3)


async def test_qwen_legacy_without_item_ids_promotes_two_consecutive_commits(
    monkeypatch,
) -> None:
    commit_count = 0

    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        nonlocal commit_count
        assert isinstance(payload, str)
        message = json.loads(payload)
        if message["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif message["type"] == "input_audio_buffer.commit":
            commit_count += 1
            await ws.server_send(
                {
                    "type": "conversation.item.created",
                    "item": {"type": "message", "role": "user"},
                }
            )
            await ws.server_send({"type": "input_audio_buffer.committed"})
            await ws.server_send(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": f"turn-{commit_count}",
                }
            )
        elif message["type"] == "session.finish":
            await ws.server_send({"type": "session.finished"})

    websocket = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(qwen.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        qwen.qwen_asr_worker(
            requests, responses, "key", AsrSessionConfig(), region="cn"
        )
    )
    await _next_event(responses, "ready")
    for utterance_id in (1, 2):
        await requests.put(
            _AsrWorkerRequest(
                kind="audio", generation=0, utterance_id=utterance_id, audio=b"\0\0"
            )
        )
        await requests.put(
            _AsrWorkerRequest(kind="commit", generation=0, utterance_id=utterance_id)
        )
    first = await _next_event(responses, "final")
    second = await _next_event(responses, "final")
    assert (first.utterance_id, first.text) == (1, "turn-1")
    assert (second.utterance_id, second.text) == (2, "turn-2")
    await _stop_worker(task, requests, responses, utterance_id=3)


async def test_qwen_server_vad_maps_items_and_reconnects_on_clear(monkeypatch) -> None:
    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        if isinstance(payload, str):
            message = json.loads(payload)
            if message["type"] == "session.update":
                await ws.server_send({"type": "session.updated"})
            elif message["type"] == "session.finish":
                await ws.server_send({"type": "session.finished"})

    first = _FakeWebSocket(on_send=on_send)
    second = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(first, second)
    monkeypatch.setattr(qwen.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        qwen.qwen_asr_worker(
            requests,
            responses,
            "key",
            AsrSessionConfig(endpointing_mode="server_vad"),
        )
    )
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(
            kind="audio", generation=0, buffer_epoch=0, utterance_id=1, audio=b"\0\0"
        )
    )
    await first.server_send(
        {"type": "input_audio_buffer.speech_started", "item_id": "one"}
    )
    await first.server_send(
        {"type": "input_audio_buffer.speech_started", "item_id": "one"}
    )
    await first.server_send(
        {"type": "input_audio_buffer.speech_started", "item_id": "two"}
    )
    started_one = await _next_event(responses, "utterance_started")
    started_two = await _next_event(responses, "utterance_started")
    assert (started_one.utterance_id, started_two.utterance_id) == (1, 2)

    for item_id, text in (("two", "second"), ("one", "first")):
        await first.server_send(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": item_id,
                "transcript": text,
            }
        )
    final_two = await _next_event(responses, "final")
    final_one = await _next_event(responses, "final")
    assert (final_two.utterance_id, final_two.text) == (2, "second")
    assert (final_one.utterance_id, final_one.text) == (1, "first")

    await requests.put(
        _AsrWorkerRequest(kind="clear", generation=0, buffer_epoch=1, utterance_id=3)
    )
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=1,
            utterance_id=3,
            audio=b"\x03\x04",
        )
    )
    await _wait_until(lambda: len(connector.calls) == 2)
    await _wait_until(
        lambda: any(
            isinstance(payload, str)
            and json.loads(payload).get("type") == "input_audio_buffer.append"
            for payload in second.sent
        )
    )
    assert len(connector.calls) == 2
    await _stop_worker(
        task,
        requests,
        responses,
        buffer_epoch=1,
        utterance_id=3,
    )


async def test_step_manual_payload_and_cumulative_partial(monkeypatch) -> None:
    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        assert isinstance(payload, str)
        message = json.loads(payload)
        if message["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif message["type"] == "input_audio_buffer.commit":
            await ws.server_send(
                {"type": "input_audio_buffer.committed", "item_id": "step-1"}
            )
            await ws.server_send(
                {
                    "type": "conversation.item.input_audio_transcription.delta",
                    "item_id": "step-1",
                    "text": "你好，请问",
                    "stash": "退款",
                }
            )
            await ws.server_send(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": "step-1",
                    "transcript": "你好，请问退款流程",
                }
            )

    websocket = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(step.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        step.step_asr_worker(
            requests,
            responses,
            "step-key",
            AsrSessionConfig(language="zh-CN"),
        )
    )
    await _next_event(responses, "ready")
    pcm = b"\x11\x22" * 160
    await requests.put(
        _AsrWorkerRequest(kind="audio", generation=0, utterance_id=1, audio=pcm)
    )
    await requests.put(_AsrWorkerRequest(kind="commit", generation=0, utterance_id=1))
    partial = await _next_event(responses, "partial")
    final = await _next_event(responses, "final")
    assert partial.text == "你好，请问退款"
    assert final.text == "你好，请问退款流程"
    assert not task.done()

    url, kwargs = connector.calls[0]
    assert url == "wss://api.stepfun.com/v1/realtime/asr/stream"
    assert kwargs["additional_headers"] == {"Authorization": "Bearer step-key"}
    messages = [json.loads(payload) for payload in websocket.sent]
    session = messages[0]["session"]["audio"]["input"]
    assert session["format"] == {
        "type": "pcm",
        "codec": "pcm_s16le",
        "rate": 16_000,
        "bits": 16,
        "channel": 1,
    }
    assert session["transcription"]["model"] == "stepaudio-2.5-asr-stream"
    assert "turn_detection" not in session
    append = next(
        message
        for message in messages
        if message["type"] == "input_audio_buffer.append"
    )
    assert base64.b64decode(append["audio"]) == pcm
    await _stop_worker(task, requests, responses)


async def test_step_server_vad_maps_utterances_and_reconnects(monkeypatch) -> None:
    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        if isinstance(payload, str) and json.loads(payload)["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})

    first = _FakeWebSocket(on_send=on_send)
    second = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(first, second)
    monkeypatch.setattr(step.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        step.step_asr_worker(
            requests,
            responses,
            "key",
            AsrSessionConfig(endpointing_mode="server_vad"),
        )
    )
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(kind="audio", generation=0, utterance_id=1, audio=b"\0\0")
    )
    await first.server_send(
        {"type": "input_audio_buffer.speech_started", "item_id": "step-vad"}
    )
    await first.server_send(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "step-vad",
            "transcript": "done",
        }
    )
    assert (await _next_event(responses, "utterance_started")).utterance_id == 1
    assert (await _next_event(responses, "final")).text == "done"

    await requests.put(
        _AsrWorkerRequest(kind="clear", generation=0, buffer_epoch=1, utterance_id=2)
    )
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=1,
            utterance_id=2,
            audio=b"\x01\x02",
        )
    )
    await _wait_until(lambda: len(connector.calls) == 2)
    await _wait_until(
        lambda: any(
            isinstance(payload, str)
            and json.loads(payload).get("type") == "input_audio_buffer.append"
            for payload in second.sent
        )
    )
    await _stop_worker(
        task,
        requests,
        responses,
        buffer_epoch=1,
        utterance_id=2,
    )


async def test_openai_transcription_resampling_and_out_of_order_finals(
    monkeypatch,
) -> None:
    commit_count = 0

    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        nonlocal commit_count
        assert isinstance(payload, str)
        message = json.loads(payload)
        if message["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif message["type"] == "input_audio_buffer.commit":
            commit_count += 1
            await ws.server_send(
                {
                    "type": "input_audio_buffer.committed",
                    "item_id": f"openai-{commit_count}",
                }
            )

    websocket = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(openai.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        openai.openai_asr_worker(
            requests,
            responses,
            "openai-key",
            AsrSessionConfig(language="en-US"),
        )
    )
    await _next_event(responses, "ready")
    pcm = b"\x01\x00" * 1_600
    for utterance_id in (1, 2):
        await requests.put(
            _AsrWorkerRequest(
                kind="audio",
                generation=0,
                utterance_id=utterance_id,
                audio=pcm,
            )
        )
        await requests.put(
            _AsrWorkerRequest(kind="commit", generation=0, utterance_id=utterance_id)
        )
    await _wait_until(lambda: commit_count == 2)
    await websocket.server_send(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "openai-2",
            "transcript": "second",
        }
    )
    await websocket.server_send(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "openai-1",
            "transcript": "first",
        }
    )
    second = await _next_event(responses, "final")
    first = await _next_event(responses, "final")
    assert (second.utterance_id, second.text) == (2, "second")
    assert (first.utterance_id, first.text) == (1, "first")

    url, kwargs = connector.calls[0]
    assert "model=gpt-realtime-whisper" in url
    assert kwargs["additional_headers"] == {"Authorization": "Bearer openai-key"}
    messages = [json.loads(payload) for payload in websocket.sent]
    session = messages[0]["session"]
    assert session["type"] == "transcription"
    audio_input = session["audio"]["input"]
    assert audio_input["format"] == {"type": "audio/pcm", "rate": 24_000}
    assert audio_input["transcription"]["model"] == "gpt-realtime-whisper"
    assert audio_input["turn_detection"] is None
    assert "response.create" not in {message["type"] for message in messages}
    wire_audio = b"".join(
        base64.b64decode(message["audio"])
        for message in messages
        if message["type"] == "input_audio_buffer.append"
    )
    assert len(wire_audio) > len(pcm) * 2
    assert len(wire_audio) < len(pcm) * 4
    await _stop_worker(task, requests, responses, utterance_id=3)


async def test_openai_native_clear_and_mode_rejection(monkeypatch) -> None:
    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        if isinstance(payload, str) and json.loads(payload)["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})

    websocket = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(openai.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        openai.openai_asr_worker(requests, responses, "key", AsrSessionConfig())
    )
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(kind="clear", generation=0, buffer_epoch=1, utterance_id=2)
    )
    await _wait_until(
        lambda: any(
            isinstance(payload, str)
            and json.loads(payload).get("type") == "input_audio_buffer.clear"
            for payload in websocket.sent
        )
    )
    assert len(connector.calls) == 1
    await _stop_worker(
        task,
        requests,
        responses,
        buffer_epoch=1,
        utterance_id=2,
    )

    rejected_requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    rejected_responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    rejected = asyncio.create_task(
        openai.openai_asr_worker(
            rejected_requests,
            rejected_responses,
            "key",
            AsrSessionConfig(endpointing_mode="server_vad"),
        )
    )
    error = await _next_event(rejected_responses, "error")
    assert error.error_code == "ASR_ENDPOINTING_NOT_SUPPORTED"
    await _next_event(rejected_responses, "closed")
    await asyncio.wait_for(rejected, 1)
    assert len(connector.calls) == 1


async def test_openai_clear_keeps_old_commit_tombstone(monkeypatch) -> None:
    commit_count = 0

    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        nonlocal commit_count
        assert isinstance(payload, str)
        message = json.loads(payload)
        if message["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif message["type"] == "input_audio_buffer.commit":
            commit_count += 1

    websocket = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(openai.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        openai.openai_asr_worker(requests, responses, "key", AsrSessionConfig())
    )
    await _next_event(responses, "ready")

    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=0,
            utterance_id=1,
            audio=b"\0\0" * 100,
        )
    )
    await requests.put(
        _AsrWorkerRequest(kind="commit", generation=0, buffer_epoch=0, utterance_id=1)
    )
    await _wait_until(lambda: commit_count == 1)
    await requests.put(
        _AsrWorkerRequest(kind="clear", generation=0, buffer_epoch=1, utterance_id=2)
    )
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=1,
            utterance_id=2,
            audio=b"\1\1" * 100,
        )
    )
    await requests.put(
        _AsrWorkerRequest(kind="commit", generation=0, buffer_epoch=1, utterance_id=2)
    )
    await _wait_until(lambda: commit_count == 2)

    for item_id, transcript in (("old", "old final"), ("new", "new final")):
        await websocket.server_send(
            {"type": "input_audio_buffer.committed", "item_id": item_id}
        )
        await websocket.server_send(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": item_id,
                "transcript": transcript,
            }
        )
    old_final = await _next_event(responses, "final")
    new_final = await _next_event(responses, "final")
    assert (
        old_final.buffer_epoch,
        old_final.utterance_id,
        old_final.text,
    ) == (0, 1, "old final")
    assert (
        new_final.buffer_epoch,
        new_final.utterance_id,
        new_final.text,
    ) == (1, 2, "new final")
    await _stop_worker(
        task,
        requests,
        responses,
        buffer_epoch=1,
        utterance_id=3,
    )


async def test_grok_manual_binary_finalize_and_shutdown(monkeypatch) -> None:
    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        if not isinstance(payload, str):
            return
        message = json.loads(payload)
        if message["type"] == "finalize":
            await ws.server_send(
                {
                    "type": "transcript.partial",
                    "text": "manual final",
                    "is_final": True,
                    "speech_final": True,
                }
            )
        elif message["type"] == "audio.done":
            await ws.server_send({"type": "transcript.done", "duration": 1.0})

    websocket = _FakeWebSocket(
        initial=[{"type": "transcript.created"}],
        on_send=on_send,
    )
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(grok.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        grok.grok_asr_worker(
            requests,
            responses,
            "grok-key",
            AsrSessionConfig(language="zh-CN"),
        )
    )
    await _next_event(responses, "ready")
    pcm = b"\x12\x34" * 160
    await requests.put(
        _AsrWorkerRequest(kind="audio", generation=0, utterance_id=1, audio=pcm)
    )
    await requests.put(_AsrWorkerRequest(kind="commit", generation=0, utterance_id=1))
    final = await _next_event(responses, "final")
    assert (final.utterance_id, final.text) == (1, "manual final")
    assert not task.done()

    url, kwargs = connector.calls[0]
    query = parse_qs(urlparse(url).query)
    assert urlparse(url).path == "/v1/stt"
    assert query == {
        "sample_rate": ["16000"],
        "encoding": ["pcm"],
        "interim_results": ["true"],
        "language": ["zh"],
    }
    assert "smart_turn" not in query
    assert kwargs["additional_headers"] == {"Authorization": "Bearer grok-key"}
    assert pcm in websocket.sent
    assert {
        json.loads(payload)["type"]
        for payload in websocket.sent
        if isinstance(payload, str)
    } == {"finalize"}
    await _stop_worker(task, requests, responses)
    assert any(
        isinstance(payload, str) and json.loads(payload)["type"] == "audio.done"
        for payload in websocket.sent
    )


async def test_grok_manual_preserves_natural_segments_before_commit(
    monkeypatch,
) -> None:
    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        if not isinstance(payload, str):
            return
        message = json.loads(payload)
        if message["type"] == "finalize":
            await ws.server_send(
                {
                    "type": "transcript.partial",
                    "text": "后半段",
                    "is_final": True,
                    "speech_final": True,
                }
            )
        elif message["type"] == "audio.done":
            await ws.server_send({"type": "transcript.done", "duration": 1.0})

    websocket = _FakeWebSocket(
        initial=[{"type": "transcript.created"}], on_send=on_send
    )
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(grok.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        grok.grok_asr_worker(requests, responses, "key", AsrSessionConfig())
    )
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(kind="audio", generation=0, utterance_id=1, audio=b"\0\0")
    )
    await _wait_until(lambda: b"\0\0" in websocket.sent)
    await websocket.server_send(
        {
            "type": "transcript.partial",
            "text": "前半段",
            "is_final": True,
            "speech_final": True,
        }
    )
    assert (await _next_event(responses, "partial")).text == "前半段"

    await requests.put(
        _AsrWorkerRequest(kind="audio", generation=0, utterance_id=1, audio=b"\1\1")
    )
    await requests.put(_AsrWorkerRequest(kind="commit", generation=0, utterance_id=1))
    final = await _next_event(responses, "final")
    assert final.text == "前半段后半段"
    assert any(
        isinstance(payload, str) and json.loads(payload)["type"] == "finalize"
        for payload in websocket.sent
    )
    await _stop_worker(task, requests, responses)


async def test_grok_server_vad_three_states_and_clear_reconnect(monkeypatch) -> None:
    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        if isinstance(payload, str) and json.loads(payload)["type"] == "audio.done":
            await ws.server_send({"type": "transcript.done", "duration": 1.0})

    first = _FakeWebSocket(initial=[{"type": "transcript.created"}], on_send=on_send)
    second = _FakeWebSocket(initial=[{"type": "transcript.created"}], on_send=on_send)
    connector = _FakeConnector(first, second)
    monkeypatch.setattr(grok.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        grok.grok_asr_worker(
            requests,
            responses,
            "key",
            AsrSessionConfig(endpointing_mode="server_vad"),
        )
    )
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(kind="audio", generation=0, utterance_id=1, audio=b"\0\0")
    )
    await _wait_until(lambda: any(isinstance(item, bytes) for item in first.sent))
    for text, is_final, speech_final in (
        ("mutable", False, False),
        ("locked", True, False),
        ("utterance", True, True),
    ):
        await first.server_send(
            {
                "type": "transcript.partial",
                "text": text,
                "is_final": is_final,
                "speech_final": speech_final,
            }
        )
    started = await _next_event(responses, "utterance_started")
    mutable = await _next_event(responses, "partial")
    locked = await _next_event(responses, "partial")
    final = await _next_event(responses, "final")
    assert started.utterance_id == 1
    assert (mutable.text, locked.text, final.text) == (
        "mutable",
        "locked",
        "utterance",
    )

    await requests.put(
        _AsrWorkerRequest(kind="clear", generation=0, buffer_epoch=1, utterance_id=2)
    )
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=0,
            buffer_epoch=1,
            utterance_id=2,
            audio=b"\x56\x78",
        )
    )
    await _wait_until(lambda: len(connector.calls) == 2)
    await _wait_until(lambda: b"\x56\x78" in second.sent)
    await _stop_worker(
        task,
        requests,
        responses,
        buffer_epoch=1,
        utterance_id=2,
    )


@pytest.mark.parametrize(
    ("module", "worker"),
    [
        (qwen, qwen.qwen_asr_worker),
        (step, step.step_asr_worker),
        (openai, openai.openai_asr_worker),
        (grok, grok.grok_asr_worker),
    ],
)
async def test_workers_reject_unsupported_languages_without_connecting(
    monkeypatch,
    module,
    worker,
) -> None:
    connect_calls = 0

    async def unexpected_connect(*args, **kwargs):
        nonlocal connect_calls
        connect_calls += 1
        raise AssertionError("language validation must precede network access")

    monkeypatch.setattr(module.websockets, "connect", unexpected_connect)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    await worker(
        requests,
        responses,
        "key",
        AsrSessionConfig(language="eo"),
    )
    error = await _next_event(responses, "error")
    assert error.error_code == "ASR_LANGUAGE_NOT_SUPPORTED"
    assert (await _next_event(responses, "closed")).kind == "closed"
    assert connect_calls == 0


async def test_workers_report_unexpected_disconnect(monkeypatch) -> None:
    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        if isinstance(payload, str) and json.loads(payload)["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})

    websocket = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(openai.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        openai.openai_asr_worker(requests, responses, "key", AsrSessionConfig())
    )
    await _next_event(responses, "ready")
    await websocket.server_end()
    error = await _next_event(responses, "error")
    assert error.error_code == "ASR_OPENAI_DISCONNECTED"
    await _next_event(responses, "closed")
    await asyncio.wait_for(task, 1)


async def test_soniox_binary_pcm_token_state_machine_and_end_dedup(monkeypatch) -> None:
    websocket = _FakeWebSocket()
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(soniox.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        soniox.soniox_asr_worker(
            requests,
            responses,
            "soniox-key",
            AsrSessionConfig(language="auto", endpointing_mode="server_vad"),
            region="jp",
        )
    )
    await _next_event(responses, "ready")
    config = json.loads(websocket.sent[0])
    assert connector.calls[0][0] == soniox.SONIOX_REGION_URLS["jp"]
    assert config == {
        "api_key": "soniox-key",
        "model": "stt-rt-v5",
        "audio_format": "pcm_s16le",
        "sample_rate": 16_000,
        "num_channels": 1,
        "enable_endpoint_detection": True,
        "enable_language_identification": True,
        "language_hints": ["en", "ja", "es"],
    }

    pcm = b"\x12\x34" * 800
    await requests.put(
        _AsrWorkerRequest(kind="audio", generation=2, buffer_epoch=3, audio=pcm)
    )
    await _wait_until(lambda: pcm in websocket.sent)
    assert pcm in websocket.sent
    assert not any(
        isinstance(payload, str) and "audio" in payload
        for payload in websocket.sent[1:]
    )

    await websocket.server_send(
        {
            "tokens": [
                {"text": "Hello ", "is_final": True},
                {"text": "wor", "is_final": False},
            ]
        }
    )
    started = await _next_event(responses, "utterance_started")
    partial = await _next_event(responses, "partial")
    assert (started.generation, started.buffer_epoch, started.utterance_id) == (2, 3, 1)
    assert partial.text == "Hello wor"

    await websocket.server_send(
        {"tokens": [{"text": "world", "is_final": True}]}
    )
    assert (await _next_event(responses, "partial")).text == "Hello world"
    await websocket.server_send(
        {"tokens": [{"text": "<end>", "is_final": True}]}
    )
    final = await _next_event(responses, "final")
    assert final.text == "Hello world"
    assert "<end>" not in final.text

    await websocket.server_send(
        {
            "tokens": [
                {"text": "<end>", "is_final": True},
                {"text": "<fin>", "is_final": True},
            ]
        }
    )
    await asyncio.sleep(0.01)
    assert responses.empty()
    await websocket.server_send(
        {"tokens": [{"text": "temporary", "is_final": False}]}
    )
    assert (await _next_event(responses, "utterance_started")).utterance_id == 2
    assert (await _next_event(responses, "partial")).text == "temporary"
    await websocket.server_send(
        {"tokens": [{"text": "<end>", "is_final": True}]}
    )
    await asyncio.sleep(0.01)
    assert responses.empty()
    await websocket.server_send(
        {
            "tokens": [
                {"text": "日本語🙂", "is_final": True},
                {"text": "<end>", "is_final": True},
            ]
        }
    )
    assert (await _next_event(responses, "utterance_started")).utterance_id == 3
    assert (await _next_event(responses, "final")).text == "日本語🙂"
    await _stop_worker(task, requests, responses, generation=2, buffer_epoch=3)
    assert websocket.sent[-1] == b""


async def test_soniox_manual_finalize_waits_for_fin(monkeypatch) -> None:
    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        if isinstance(payload, str) and json.loads(payload).get("type") == "finalize":
            await ws.server_send(
                {
                    "tokens": [
                        {"text": "manual text", "is_final": True},
                        {"text": "<fin>", "is_final": True},
                    ]
                }
            )

    websocket = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(soniox.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        soniox.soniox_asr_worker(
            requests,
            responses,
            "key",
            AsrSessionConfig(endpointing_mode="manual"),
            region="eu",
        )
    )
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(kind="audio", generation=0, utterance_id=1, audio=b"\0\0")
    )
    await requests.put(_AsrWorkerRequest(kind="commit", generation=0, utterance_id=1))
    assert (await _next_event(responses, "final")).text == "manual text"
    await _stop_worker(task, requests, responses)


async def test_soniox_reconnects_once_and_replays_current_audio(monkeypatch) -> None:
    first = _FakeWebSocket()
    second = _FakeWebSocket()
    connector = _FakeConnector(first, second)
    monkeypatch.setattr(soniox.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        soniox.soniox_asr_worker(
            requests,
            responses,
            "key",
            AsrSessionConfig(endpointing_mode="server_vad"),
        )
    )
    await _next_event(responses, "ready")
    pcm = b"\x20\x10" * 320
    await requests.put(
        _AsrWorkerRequest(kind="audio", generation=4, buffer_epoch=5, audio=pcm)
    )
    await _wait_until(lambda: pcm in first.sent)
    await first.server_end()
    await _wait_until(lambda: len(connector.calls) == 2)
    await _wait_until(lambda: pcm in second.sent)
    await second.server_send(
        {
            "tokens": [
                {"text": "replayed", "is_final": True},
                {"text": "<end>", "is_final": True},
            ]
        }
    )
    assert (await _next_event(responses, "utterance_started")).generation == 4
    assert (await _next_event(responses, "final")).text == "replayed"
    await _stop_worker(task, requests, responses, generation=4, buffer_epoch=5)


async def test_openai_transcription_failed_preserves_utterance_metadata(
    monkeypatch,
) -> None:
    async def on_send(ws: _FakeWebSocket, payload: str | bytes) -> None:
        assert isinstance(payload, str)
        message = json.loads(payload)
        if message["type"] == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif message["type"] == "input_audio_buffer.commit":
            await ws.server_send(
                {"type": "input_audio_buffer.committed", "item_id": "failed-item"}
            )
            await ws.server_send(
                {
                    "type": "conversation.item.input_audio_transcription.failed",
                    "item_id": "failed-item",
                }
            )

    websocket = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(openai.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        openai.openai_asr_worker(
            requests, responses, "key", AsrSessionConfig(language="en")
        )
    )
    await _next_event(responses, "ready")
    await requests.put(
        _AsrWorkerRequest(
            kind="audio",
            generation=3,
            buffer_epoch=7,
            utterance_id=9,
            audio=b"\0\0" * 320,
        )
    )
    await requests.put(
        _AsrWorkerRequest(
            kind="commit", generation=3, buffer_epoch=7, utterance_id=9
        )
    )
    error = await _next_event(responses, "error")
    assert (error.generation, error.buffer_epoch, error.utterance_id) == (3, 7, 9)
    assert error.error_code == "ASR_OPENAI_TRANSCRIPTION_FAILED"
    await _stop_worker(
        task, requests, responses, generation=3, buffer_epoch=7, utterance_id=10
    )


async def test_soniox_auth_error_is_terminal_without_reconnect(monkeypatch) -> None:
    websocket = _FakeWebSocket(
        initial=[
            {
                "error_code": 401,
                "error_message": "not logged",
                "request_id": "request-1",
            }
        ]
    )
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(soniox.websockets, "connect", connector)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        soniox.soniox_asr_worker(
            requests,
            responses,
            "bad-key",
            AsrSessionConfig(endpointing_mode="server_vad"),
        )
    )
    await _next_event(responses, "ready")
    error = await _next_event(responses, "error")
    assert error.error_code == "ASR_CREDENTIALS_REJECTED"
    await _next_event(responses, "closed")
    await asyncio.wait_for(task, 1)
    assert len(connector.calls) == 1


async def test_soniox_rate_limit_backs_off_and_reconnects_only_once(
    monkeypatch,
) -> None:
    rate_limit_event = {
        "error_code": 429,
        "error_message": "rate limited",
        "request_id": "request-rate-limit",
    }
    connector = _FakeConnector(
        _FakeWebSocket(initial=[rate_limit_event]),
        _FakeWebSocket(initial=[rate_limit_event]),
    )
    monkeypatch.setattr(soniox.websockets, "connect", connector)
    monkeypatch.setattr(soniox, "_RETRY_BACKOFF_BASE_SECONDS", 0.0)
    requests: asyncio.Queue[_AsrWorkerRequest] = asyncio.Queue()
    responses: asyncio.Queue[_AsrWorkerEvent] = asyncio.Queue()
    task = asyncio.create_task(
        soniox.soniox_asr_worker(
            requests,
            responses,
            "key",
            AsrSessionConfig(endpointing_mode="server_vad"),
        )
    )
    await _next_event(responses, "ready")
    error = await _next_event(responses, "error")
    assert error.error_code == "ASR_RATE_LIMITED"
    await _next_event(responses, "closed")
    await asyncio.wait_for(task, 1)
    assert len(connector.calls) == 2


def test_auth_rejection_classification() -> None:
    class Response:
        status_code = 401

    error = RuntimeError("must not be inspected or logged")
    error.response = Response()  # type: ignore[attr-defined]
    assert qwen._qwen_is_auth_rejection(error)
    assert step._step_is_auth_rejection(error)
    assert openai._openai_is_auth_rejection(error)
    assert grok._grok_is_auth_rejection(error)

    ordinary_error = RuntimeError("network failure")
    assert not qwen._qwen_is_auth_rejection(ordinary_error)
    assert not step._step_is_auth_rejection(ordinary_error)
    assert not openai._openai_is_auth_rejection(ordinary_error)
    assert not grok._grok_is_auth_rejection(ordinary_error)
