import json
import queue

from main_logic.tts_client._infra import TTS_SHUTDOWN_SENTINEL
from main_logic.tts_client.workers import _step_protocol


class _FakeTtsSocket:
    def __init__(self, events, *, fail_send_at=None, fail_send_from=None):
        self._events = queue.SimpleQueue()
        for event in events:
            self._events.put(json.dumps(event))
        self._closed = False
        self._send_count = 0
        self._fail_send_at = fail_send_at
        self._fail_send_from = fail_send_from
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        while self._events.empty():
            if self._closed:
                raise StopAsyncIteration
            import asyncio

            await asyncio.sleep(0)
        item = self._events.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def send(self, payload):
        self._send_count += 1
        if (
            self._send_count == self._fail_send_at
            or (
                self._fail_send_from is not None
                and self._send_count >= self._fail_send_from
            )
        ):
            raise RuntimeError("socket dropped during buffered delta")
        self.sent.append(json.loads(payload))

    async def close(self):
        self._closed = True
        self._events.put(None)


class _AutoShutdownQueue:
    """Return shutdown once all dynamically queued retry work is consumed."""

    def __init__(self):
        self._items = []

    def put(self, item):
        self._items.append(item)

    def get(self):
        if self._items:
            return self._items.pop(0)
        return (TTS_SHUTDOWN_SENTINEL, None)

    def get_nowait(self):
        if self._items:
            return self._items.pop(0)
        raise queue.Empty


def test_buffered_delta_failure_reconnects_and_replays_text(monkeypatch):
    initial = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "warmup"}},
        {"type": "tts.response.created"},
    ])
    broken = _FakeTtsSocket(
        [{"type": "tts.connection.done", "data": {"session_id": "broken"}}],
        fail_send_at=2,
    )
    replacement = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "replacement"}},
    ])
    sockets = iter([initial, broken, replacement])

    async def connect(*_args, **_kwargs):
        return next(sockets)

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)

    requests = queue.Queue()
    responses = queue.Queue()
    text = "This buffered first chunk is long enough for language detection."
    requests.put(("speech-1", text))
    requests.put((None, None))
    requests.put((TTS_SHUTDOWN_SENTINEL, None))

    _step_protocol.run_step_protocol_tts_worker(
        requests,
        responses,
        "test-key",
        "test-voice",
        provider_key="step",
    )

    replacement_events = replacement.sent
    assert [event["type"] for event in replacement_events] == [
        "tts.create",
        "tts.text.delta",
        "tts.text.done",
    ]
    assert replacement_events[1]["data"]["text"] == text


def test_replay_create_failure_invalidates_replacement_socket(monkeypatch):
    initial = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "warmup"}},
        {"type": "tts.response.created"},
    ])
    broken = _FakeTtsSocket(
        [{"type": "tts.connection.done", "data": {"session_id": "broken"}}],
        fail_send_at=2,
    )
    replacement = _FakeTtsSocket(
        [{"type": "tts.connection.done", "data": {"session_id": "replacement"}}],
        fail_send_from=1,
    )
    recovered = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "recovered"}},
    ])
    sockets = iter([initial, broken, replacement, recovered])

    async def connect(*_args, **_kwargs):
        return next(sockets)

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)

    requests = queue.Queue()
    responses = queue.Queue()
    first = "The first buffered chunk is long enough to trigger create."
    second = "A later chunk on the same speech id must reconnect cleanly."
    requests.put(("speech-1", first))
    requests.put(("speech-1", second))
    requests.put((None, None))
    requests.put((TTS_SHUTDOWN_SENTINEL, None))

    _step_protocol.run_step_protocol_tts_worker(
        requests,
        responses,
        "test-key",
        "test-voice",
        provider_key="step",
    )

    assert [event["type"] for event in recovered.sent] == [
        "tts.create",
        "tts.text.delta",
        "tts.text.done",
    ]
    assert recovered.sent[1]["data"]["text"] == first + second
    assert replacement._closed is True


def test_turn_end_reconnects_retained_prefix_after_replay_create_failure(monkeypatch):
    initial = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "warmup"}},
        {"type": "tts.response.created"},
    ])
    broken = _FakeTtsSocket(
        [{"type": "tts.connection.done", "data": {"session_id": "broken"}}],
        fail_send_at=2,
    )
    replacement = _FakeTtsSocket(
        [{"type": "tts.connection.done", "data": {"session_id": "replacement"}}],
        fail_send_from=1,
    )
    recovered = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "recovered"}},
    ])
    sockets = iter([initial, broken, replacement, recovered])

    async def connect(*_args, **_kwargs):
        return next(sockets)

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)

    requests = queue.Queue()
    responses = queue.Queue()
    text = "The only buffered chunk must survive through the turn boundary."
    requests.put(("speech-1", text))
    requests.put((None, None))
    requests.put((TTS_SHUTDOWN_SENTINEL, None))

    _step_protocol.run_step_protocol_tts_worker(
        requests,
        responses,
        "test-key",
        "test-voice",
        provider_key="step",
    )

    assert [event["type"] for event in recovered.sent] == [
        "tts.create",
        "tts.text.delta",
        "tts.text.done",
    ]
    assert recovered.sent[1]["data"]["text"] == text
    assert replacement._closed is True


def test_turn_end_create_failure_retries_on_fresh_socket_with_backoff(monkeypatch):
    initial = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "warmup"}},
        {"type": "tts.response.created"},
    ])
    broken = _FakeTtsSocket(
        [{"type": "tts.connection.done", "data": {"session_id": "broken"}}],
        fail_send_at=2,
    )
    replacement = _FakeTtsSocket(
        [{"type": "tts.connection.done", "data": {"session_id": "replacement"}}],
        fail_send_from=1,
    )
    terminal_broken = _FakeTtsSocket(
        [{"type": "tts.connection.done", "data": {"session_id": "terminal-broken"}}],
        fail_send_from=1,
    )
    recovered = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "recovered"}},
    ])
    sockets = iter([initial, broken, replacement, terminal_broken, recovered])

    async def connect(*_args, **_kwargs):
        return next(sockets)

    real_sleep = _step_protocol.asyncio.sleep

    async def no_delay(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)
    monkeypatch.setattr(_step_protocol.asyncio, "sleep", no_delay)

    requests = _AutoShutdownQueue()
    responses = queue.Queue()
    text = "The terminal retry must reconnect instead of spinning on a dead socket."
    requests.put(("speech-1", text))
    requests.put((None, None))

    _step_protocol.run_step_protocol_tts_worker(
        requests,
        responses,
        "test-key",
        "test-voice",
        provider_key="step",
    )

    assert terminal_broken._closed is True
    assert [event["type"] for event in recovered.sent] == [
        "tts.create",
        "tts.text.delta",
        "tts.text.done",
    ]
    assert recovered.sent[1]["data"]["text"] == text


def test_finish_retry_precedes_queued_new_speech(monkeypatch):
    initial = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "warmup"}},
        {"type": "tts.response.created"},
    ])
    old_broken = _FakeTtsSocket(
        [{"type": "tts.connection.done", "data": {"session_id": "old-broken"}}],
        fail_send_from=1,
    )
    old_recovered = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "old-recovered"}},
    ])
    new_socket = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "new"}},
    ])
    sockets = iter([initial, old_broken, old_recovered, new_socket])
    connect_attempt = 0

    async def connect(*_args, **_kwargs):
        nonlocal connect_attempt
        connect_attempt += 1
        if connect_attempt == 3:
            raise RuntimeError("first terminal reconnect failed")
        return next(sockets)

    real_sleep = _step_protocol.asyncio.sleep

    async def no_delay(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)
    monkeypatch.setattr(_step_protocol.asyncio, "sleep", no_delay)

    requests = _AutoShutdownQueue()
    responses = queue.Queue()
    requests.put(("speech-old", "old"))
    requests.put((None, None))
    requests.put(("speech-new", "new text remains open"))

    _step_protocol.run_step_protocol_tts_worker(
        requests,
        responses,
        "test-key",
        "test-voice",
        provider_key="step",
    )

    assert [event["type"] for event in old_recovered.sent] == [
        "tts.create",
        "tts.text.delta",
        "tts.text.done",
    ]
    assert old_recovered.sent[1]["data"]["text"] == "old"
    assert [event["type"] for event in new_socket.sent] == [
        "tts.create",
        "tts.text.delta",
    ]
    assert new_socket.sent[1]["data"]["text"] == "new text remains open"


def test_interrupt_preempts_finish_retry_after_backoff(monkeypatch):
    initial = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "warmup"}},
        {"type": "tts.response.created"},
    ])
    old_broken = _FakeTtsSocket(
        [{"type": "tts.connection.done", "data": {"session_id": "old-broken"}}],
        fail_send_from=1,
    )
    unexpected_retry = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "unexpected"}},
    ])
    sockets = iter([initial, old_broken, unexpected_retry])
    connect_attempts = 0

    async def connect(*_args, **_kwargs):
        nonlocal connect_attempts
        connect_attempts += 1
        return next(sockets)

    real_sleep = _step_protocol.asyncio.sleep

    async def no_delay(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)
    monkeypatch.setattr(_step_protocol.asyncio, "sleep", no_delay)

    requests = _AutoShutdownQueue()
    responses = queue.Queue()
    requests.put(("speech-old", "old"))
    requests.put((None, None))
    requests.put(("__interrupt__", None))
    requests.put((TTS_SHUTDOWN_SENTINEL, None))

    _step_protocol.run_step_protocol_tts_worker(
        requests,
        responses,
        "test-key",
        "test-voice",
        provider_key="step",
    )

    assert connect_attempts == 2
    assert unexpected_retry.sent == []
    assert old_broken._closed is True
