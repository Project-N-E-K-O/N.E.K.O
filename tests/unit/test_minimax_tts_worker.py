import json
import queue
import threading
import time
import asyncio
from collections import deque

import numpy as np
import pytest

from main_logic import tts_client


class ControlledQueue:
    def __init__(self):
        self._queue = queue.Queue()
        self._stop = object()

    def put(self, item):
        self._queue.put(item)

    def get(self, timeout=None):
        item = self._queue.get(timeout=timeout)
        if item is self._stop:
            raise EOFError("queue closed")
        return item

    def empty(self):
        return self._queue.empty()

    def close(self):
        self._queue.put(self._stop)


class FakeMiniMaxWebSocket:
    _CLOSE_SENTINEL = object()

    def __init__(self, handshake=None, on_send=None):
        self._recv_queue = deque()
        self.sent_messages = []
        self.closed = False
        self.on_send = on_send
        self.state = getattr(tts_client, "_WsState", None).OPEN if getattr(tts_client, "_WsState", None) else None
        self._recv_queue.append(handshake or {"event": "connected_success"})

    def queue_event(self, payload):
        self._recv_queue.append(payload)

    async def send(self, raw_message):
        message = json.loads(raw_message)
        self.sent_messages.append(message)
        if self.on_send is not None:
            result = self.on_send(self, message)
            if hasattr(result, "__await__"):
                await result

    async def recv(self):
        while not self._recv_queue:
            await asyncio.sleep(0.01)
        item = self._recv_queue.popleft()
        if item is self._CLOSE_SENTINEL:
            self.closed = True
            if getattr(tts_client, "_WsState", None):
                self.state = tts_client._WsState.CLOSED
            raise RuntimeError("fake websocket closed")
        if isinstance(item, dict):
            return json.dumps(item)
        return item

    async def close(self):
        self.force_disconnect()

    def force_disconnect(self):
        if self.closed:
            return
        self.closed = True
        if getattr(tts_client, "_WsState", None):
            self.state = tts_client._WsState.CLOSED
        self._recv_queue.append(self._CLOSE_SENTINEL)


class FakeConnectFactory:
    def __init__(self, *sockets):
        self.sockets = list(sockets)
        self.calls = []

    async def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.sockets:
            raise RuntimeError("no fake websocket left")
        return self.sockets.pop(0)


def _start_worker(request_queue, response_queue, base_url="https://api.minimaxi.com"):
    thread = threading.Thread(
        target=tts_client.minimax_tts_worker,
        args=(request_queue, response_queue, "test-minimax-key", "custom_test_voice", base_url),
        daemon=True,
    )
    thread.start()
    return thread


def _wait_for_queue_item(q, predicate, timeout=3.0):
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        remaining = max(0.01, deadline - time.time())
        try:
            item = q.get(timeout=remaining)
        except queue.Empty:
            continue
        seen.append(item)
        if predicate(item):
            return item, seen
    raise AssertionError(f"Timed out waiting for queue item, seen={seen!r}")


@pytest.mark.unit
def test_get_minimax_tts_ws_url():
    assert tts_client._get_minimax_tts_ws_url("https://api.minimaxi.com/v1") == "wss://api.minimaxi.com/ws/v1/t2a_v2"
    assert tts_client._get_minimax_tts_ws_url("https://api.minimax.io") == "wss://api.minimax.io/ws/v1/t2a_v2"
    assert tts_client._get_minimax_tts_http_url("https://api.minimax.io") == "https://api.minimax.io/v1/t2a_v2"


@pytest.mark.unit
def test_minimax_worker_probes_ready_and_streams_audio_per_turn(monkeypatch):
    pcm_bytes = (np.arange(3000, dtype=np.int16)).tobytes()

    async def task_on_send(ws, message):
        event = message.get("event")
        if event == "task_start":
            ws.queue_event({"event": "task_started"})
        elif event == "task_continue":
            ws.queue_event(
                {
                    "event": "task_continued",
                    "data": {"audio": pcm_bytes.hex()},
                    "is_final": True,
                }
            )
        elif event == "task_finish":
            ws.queue_event({"event": "task_finished"})

    probe_ws = FakeMiniMaxWebSocket()
    task_ws = FakeMiniMaxWebSocket(on_send=task_on_send)
    factory = FakeConnectFactory(probe_ws, task_ws)
    monkeypatch.setattr(tts_client.websockets, "connect", factory)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    ready_item, _ = _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))
    assert ready_item == ("__ready__", True)

    request_queue.put(("speech-1", "你好"))
    request_queue.put(("speech-1", "世界今天"))
    request_queue.put((None, None))

    audio_item, seen = _wait_for_queue_item(
        response_queue,
        lambda item: isinstance(item, tuple) and len(item) == 3 and item[0] == "__audio__",
    )
    assert audio_item[1] == "speech-1"
    assert isinstance(audio_item[2], bytes)
    assert len(audio_item[2]) > 0
    assert ("__reconnecting__", "TTS_RECONNECTING") not in seen

    request_queue.close()
    thread.join(timeout=2.0)
    assert not thread.is_alive()

    assert probe_ws.sent_messages == []
    assert probe_ws.closed is True
    assert [msg["event"] for msg in task_ws.sent_messages] == ["task_start", "task_continue", "task_finish"]
    assert task_ws.sent_messages[1]["text"] == "你好世界今天"
    assert task_ws.closed is True
    assert len(factory.calls) == 2
    assert factory.calls[0][0] == "wss://api.minimaxi.com/ws/v1/t2a_v2"


@pytest.mark.unit
def test_minimax_worker_handshake_failure_reports_not_ready(monkeypatch):
    bad_ws = FakeMiniMaxWebSocket(handshake={"event": "bad_handshake"})
    factory = FakeConnectFactory(bad_ws)
    monkeypatch.setattr(tts_client.websockets, "connect", factory)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    not_ready_item, seen = _wait_for_queue_item(
        response_queue,
        lambda item: item == ("__ready__", False),
    )
    assert not_ready_item == ("__ready__", False)
    assert any(isinstance(item, tuple) and item[0] == "__error__" for item in seen)

    request_queue.close()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_minimax_worker_unexpected_disconnect_triggers_reconnecting(monkeypatch):
    async def task_on_send(ws, message):
        event = message.get("event")
        if event == "task_start":
            ws.queue_event({"event": "task_started"})
        elif event == "task_continue":
            ws.force_disconnect()

    probe_ws = FakeMiniMaxWebSocket()
    task_ws = FakeMiniMaxWebSocket(on_send=task_on_send)
    factory = FakeConnectFactory(probe_ws, task_ws)
    monkeypatch.setattr(tts_client.websockets, "connect", factory)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))

    request_queue.put(("speech-1", "你好"))
    request_queue.put(("speech-1", "世界今天"))

    reconnecting_item, _ = _wait_for_queue_item(
        response_queue,
        lambda item: item == ("__reconnecting__", "TTS_RECONNECTING"),
    )
    assert reconnecting_item == ("__reconnecting__", "TTS_RECONNECTING")
    assert len(factory.calls) == 2

    request_queue.close()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_minimax_worker_switches_speech_id_without_reusing_old_connection(monkeypatch):
    pcm_bytes = (np.arange(2500, dtype=np.int16)).tobytes()

    async def old_turn_on_send(ws, message):
        if message.get("event") == "task_start":
            ws.queue_event({"event": "task_started"})

    async def new_turn_on_send(ws, message):
        event = message.get("event")
        if event == "task_start":
            ws.queue_event({"event": "task_started"})
        elif event == "task_continue":
            ws.queue_event(
                {
                    "event": "task_continued",
                    "data": {"audio": pcm_bytes.hex()},
                    "is_final": True,
                }
            )
        elif event == "task_finish":
            ws.queue_event({"event": "task_finished"})

    probe_ws = FakeMiniMaxWebSocket()
    old_turn_ws = FakeMiniMaxWebSocket(on_send=old_turn_on_send)
    new_turn_ws = FakeMiniMaxWebSocket(on_send=new_turn_on_send)
    factory = FakeConnectFactory(probe_ws, old_turn_ws, new_turn_ws)
    monkeypatch.setattr(tts_client.websockets, "connect", factory)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))

    request_queue.put(("speech-old", "abcdef"))
    request_queue.put(("speech-new", "ghijkl"))
    request_queue.put((None, None))

    audio_item, _ = _wait_for_queue_item(
        response_queue,
        lambda item: isinstance(item, tuple) and len(item) == 3 and item[0] == "__audio__",
    )
    assert audio_item[1] == "speech-new"
    assert probe_ws.closed is True
    assert old_turn_ws.closed is True
    assert [msg["event"] for msg in old_turn_ws.sent_messages] == ["task_start", "task_continue"]
    assert [msg["event"] for msg in new_turn_ws.sent_messages] == ["task_start", "task_continue", "task_finish"]

    request_queue.close()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_minimax_worker_preserves_tail_audio_until_close_flush(monkeypatch):
    # Keep resampled output below the 4KB aggregation threshold so audio only
    # appears when the receive-loop finally block flushes on close.
    pcm_bytes = (np.arange(1500, dtype=np.int16)).tobytes()
    original_asyncio_wait = asyncio.wait
    wait_call_count = 0

    async def patched_asyncio_wait(*args, **kwargs):
        nonlocal wait_call_count
        wait_call_count += 1
        if wait_call_count == 2:
            pending = set(args[0])
            return set(), pending
        return await original_asyncio_wait(*args, **kwargs)

    async def task_on_send(ws, message):
        event = message.get("event")
        if event == "task_start":
            ws.queue_event({"event": "task_started"})
        elif event == "task_continue":
            ws.queue_event(
                {
                    "event": "task_continued",
                    "data": {"audio": pcm_bytes.hex()},
                }
            )

    probe_ws = FakeMiniMaxWebSocket()
    task_ws = FakeMiniMaxWebSocket(on_send=task_on_send)
    factory = FakeConnectFactory(probe_ws, task_ws)
    monkeypatch.setattr(tts_client.websockets, "connect", factory)
    monkeypatch.setattr(asyncio, "wait", patched_asyncio_wait)
    monkeypatch.setattr(tts_client.asyncio, "wait", patched_asyncio_wait)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))

    request_queue.put(("speech-tail", "abcdef"))
    request_queue.put((None, None))

    audio_item, _ = _wait_for_queue_item(
        response_queue,
        lambda item: isinstance(item, tuple) and len(item) == 3 and item[0] == "__audio__",
    )
    assert audio_item[1] == "speech-tail"
    assert len(audio_item[2]) > 0
    assert [msg["event"] for msg in task_ws.sent_messages] == ["task_start", "task_continue", "task_finish"]

    request_queue.close()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_minimax_worker_degrades_next_turn_after_slow_stream(monkeypatch):
    slow_pcm = (np.arange(3000, dtype=np.int16)).tobytes()
    buffered_pcm = (np.arange(2200, dtype=np.int16)).tobytes()

    async def slow_turn_on_send(ws, message):
        event = message.get("event")
        if event == "task_start":
            ws.queue_event({"event": "task_started"})
        elif event == "task_continue":
            ws.queue_event(
                {
                    "event": "task_continued",
                    "data": {"audio": slow_pcm.hex()},
                    "is_final": False,
                }
            )
            await asyncio.sleep(0.55)
            ws.queue_event(
                {
                    "event": "task_continued",
                    "data": {"audio": slow_pcm.hex()},
                    "is_final": True,
                }
            )
        elif event == "task_finish":
            ws.queue_event({"event": "task_finished"})

    async def fake_buffered_synthesize(*args, **kwargs):
        return buffered_pcm

    probe_ws = FakeMiniMaxWebSocket()
    slow_ws = FakeMiniMaxWebSocket(on_send=slow_turn_on_send)
    factory = FakeConnectFactory(probe_ws, slow_ws)
    monkeypatch.setattr(tts_client.websockets, "connect", factory)
    monkeypatch.setattr(tts_client, "_minimax_http_synthesize_pcm", fake_buffered_synthesize)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue, base_url="https://api.minimax.io")

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))

    request_queue.put(("speech-slow", "abcdef"))
    request_queue.put((None, None))
    request_queue.put(("speech-buffered", "这是下一轮句级缓冲播放测试。"))
    request_queue.put((None, None))

    buffered_audio, seen = _wait_for_queue_item(
        response_queue,
        lambda item: isinstance(item, tuple) and len(item) == 3 and item[0] == "__audio__" and item[1] == "speech-buffered",
        timeout=6.0,
    )
    assert buffered_audio[1] == "speech-buffered"
    assert ("__status__", "TTS_DEGRADED_BUFFERING") in seen
    assert len(factory.calls) == 2
    assert [msg["event"] for msg in slow_ws.sent_messages] == ["task_start", "task_continue", "task_finish"]

    request_queue.close()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_minimax_worker_recovers_streaming_after_buffered_hold(monkeypatch):
    slow_pcm = (np.arange(3000, dtype=np.int16)).tobytes()
    healthy_pcm = (np.arange(2600, dtype=np.int16)).tobytes()
    buffered_pcm = (np.arange(2200, dtype=np.int16)).tobytes()

    async def slow_turn_on_send(ws, message):
        event = message.get("event")
        if event == "task_start":
            ws.queue_event({"event": "task_started"})
        elif event == "task_continue":
            ws.queue_event(
                {
                    "event": "task_continued",
                    "data": {"audio": slow_pcm.hex()},
                    "is_final": False,
                }
            )
            await asyncio.sleep(0.55)
            ws.queue_event(
                {
                    "event": "task_continued",
                    "data": {"audio": slow_pcm.hex()},
                    "is_final": True,
                }
            )
        elif event == "task_finish":
            ws.queue_event({"event": "task_finished"})

    def make_healthy_turn_ws():
        async def healthy_turn_on_send(ws, message):
            event = message.get("event")
            if event == "task_start":
                ws.queue_event({"event": "task_started"})
            elif event == "task_continue":
                ws.queue_event(
                    {
                        "event": "task_continued",
                        "data": {"audio": healthy_pcm.hex()},
                        "is_final": True,
                    }
                )
            elif event == "task_finish":
                ws.queue_event({"event": "task_finished"})

        return FakeMiniMaxWebSocket(on_send=healthy_turn_on_send)

    async def fake_buffered_synthesize(*args, **kwargs):
        return buffered_pcm

    probe_ws = FakeMiniMaxWebSocket()
    slow_ws = FakeMiniMaxWebSocket(on_send=slow_turn_on_send)
    healthy_ws_1 = make_healthy_turn_ws()
    healthy_ws_2 = make_healthy_turn_ws()
    healthy_ws_3 = make_healthy_turn_ws()
    factory = FakeConnectFactory(probe_ws, slow_ws, healthy_ws_1, healthy_ws_2, healthy_ws_3)
    monkeypatch.setattr(tts_client.websockets, "connect", factory)
    monkeypatch.setattr(tts_client, "_minimax_http_synthesize_pcm", fake_buffered_synthesize)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue, base_url="https://api.minimax.io")

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))

    request_queue.put(("speech-1", "abcdef"))
    request_queue.put((None, None))

    for sid in ("speech-2", "speech-3", "speech-4"):
        request_queue.put((sid, "这是降级模式下的句级缓冲播放测试。"))
        request_queue.put((None, None))

    for sid in ("speech-5", "speech-6", "speech-7"):
        request_queue.put((sid, "健康流式恢复测试文本abcdef。"))
        request_queue.put((None, None))

    recovered_item, seen = _wait_for_queue_item(
        response_queue,
        lambda item: item == ("__status__", "TTS_STREAM_RECOVERED"),
        timeout=8.0,
    )
    assert recovered_item == ("__status__", "TTS_STREAM_RECOVERED")
    assert ("__status__", "TTS_DEGRADED_BUFFERING") in seen
    assert len(factory.calls) == 5

    request_queue.close()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
