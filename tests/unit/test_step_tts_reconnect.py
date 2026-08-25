import json
import queue
import threading

from main_logic.tts_client._infra import TTS_SHUTDOWN_SENTINEL
from main_logic.tts_client.workers import _step_protocol


class _FakeTtsSocket:
    def __init__(
        self,
        events,
        *,
        fail_send_at=None,
        fail_send_from=None,
        close_error=None,
        before_close=None,
    ):
        self._events = queue.SimpleQueue()
        for event in events:
            self._events.put(json.dumps(event))
        self._closed = False
        self._send_count = 0
        self._fail_send_at = fail_send_at
        self._fail_send_from = fail_send_from
        self._close_error = close_error
        self._before_close = before_close
        self.close_attempts = 0
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
        if self._closed:
            raise RuntimeError("sent 1000 (OK); no close frame received")
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
        self.close_attempts += 1
        if self._before_close is not None:
            self._before_close()
        if self._close_error is not None:
            raise self._close_error
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


def _run_control_arriving_during_replacement_connect(
    monkeypatch,
    control_sid,
    *,
    candidate_close_error=None,
    handshake_outcome="success",
    control_arrival="connect",
):
    control_boundary_reached = threading.Event()
    control_queued = threading.Event()
    coordination_errors = []

    def coordinate_candidate_close():
        if control_arrival != "close":
            return
        control_boundary_reached.set()
        if not control_queued.wait(timeout=2.0):
            coordination_errors.append("control request was not queued")

    initial = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "warmup"}},
        {"type": "tts.response.created"},
    ])
    previous = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "previous"}},
    ])
    candidate_session_id = (
        None if handshake_outcome == "missing_session" else "candidate"
    )
    candidate = _FakeTtsSocket(
        [{
            "type": "tts.connection.done",
            "data": {"session_id": candidate_session_id},
        }],
        close_error=candidate_close_error,
        before_close=coordinate_candidate_close,
    )
    sockets = [initial, previous, candidate]
    connect_attempt = 0

    requests = queue.Queue()

    def enqueue_control():
        if not control_boundary_reached.wait(timeout=2.0):
            coordination_errors.append("control boundary was not reached")
            requests.put((TTS_SHUTDOWN_SENTINEL, None))
            return
        requests.put((control_sid, None))
        if control_sid == "__interrupt__":
            requests.put((TTS_SHUTDOWN_SENTINEL, None))
        control_queued.set()

    producer = threading.Thread(target=enqueue_control)
    producer.start()

    async def connect(*_args, **_kwargs):
        nonlocal connect_attempt
        connect_attempt += 1
        if connect_attempt == 3 and control_arrival == "connect":
            control_boundary_reached.set()
            if not control_queued.wait(timeout=2.0):
                coordination_errors.append("control request was not queued")
        return sockets[connect_attempt - 1]

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)

    if handshake_outcome == "timeout":
        real_wait_for = _step_protocol.asyncio.wait_for

        async def timeout_replacement_handshake(awaitable, timeout):
            if (
                connect_attempt == 3
                and getattr(getattr(awaitable, "cr_code", None), "co_name", "")
                == "wait_conn"
            ):
                awaitable.close()
                raise _step_protocol.asyncio.TimeoutError
            return await real_wait_for(awaitable, timeout)

        monkeypatch.setattr(
            _step_protocol.asyncio,
            "wait_for",
            timeout_replacement_handshake,
        )

    real_sleep = _step_protocol.asyncio.sleep
    retry_backoffs = []

    async def record_retry_backoff(seconds):
        if seconds == 1.0:
            retry_backoffs.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(_step_protocol.asyncio, "sleep", record_retry_backoff)

    responses = queue.Queue()
    requests.put(("speech-a", "previous speech"))
    requests.put((None, None))
    requests.put(("speech-b", "stale opening"))

    _step_protocol.run_step_protocol_tts_worker(
        requests,
        responses,
        "test-key",
        "test-voice",
        provider_key="step",
    )

    producer.join(timeout=2.0)
    assert not producer.is_alive()
    assert coordination_errors == []
    assert connect_attempt == 3
    assert candidate.sent == []
    assert candidate.close_attempts == 1
    assert retry_backoffs == []
    assert not any(
        item == ("__reconnecting__", "TTS_RECONNECTING")
        for item in list(responses.queue)
    )
    if candidate_close_error is None:
        assert candidate._closed is True


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

    requests = _AutoShutdownQueue()
    responses = queue.Queue()
    text = "This buffered first chunk is long enough for language detection."
    requests.put(("speech-1", text))
    requests.put((None, None))

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

    requests = _AutoShutdownQueue()
    responses = queue.Queue()
    first = "The first buffered chunk is long enough to trigger create."
    second = "A later chunk on the same speech id must reconnect cleanly."
    requests.put(("speech-1", first))
    requests.put(("speech-1", second))
    requests.put((None, None))

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

    requests = _AutoShutdownQueue()
    responses = queue.Queue()
    text = "The only buffered chunk must survive through the turn boundary."
    requests.put(("speech-1", text))
    requests.put((None, None))

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


def test_new_speech_connect_exception_replays_opening_with_same_sid_chunk(monkeypatch):
    initial = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "warmup"}},
        {"type": "tts.response.created"},
    ])
    previous = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "previous"}},
    ])
    recovered = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "recovered"}},
    ])
    connect_attempt = 0

    async def connect(*_args, **_kwargs):
        nonlocal connect_attempt
        connect_attempt += 1
        if connect_attempt == 3:
            raise TimeoutError("timed out during opening handshake")
        return {
            1: initial,
            2: previous,
            4: recovered,
        }[connect_attempt]

    real_sleep = _step_protocol.asyncio.sleep

    async def no_delay(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)
    monkeypatch.setattr(_step_protocol.asyncio, "sleep", no_delay)

    requests = _AutoShutdownQueue()
    responses = queue.Queue()
    opening = "The opening text must survive the failed replacement handshake."
    continuation = " The same speech continues after recovery."
    requests.put(("speech-a", "previous speech"))
    requests.put((None, None))
    requests.put(("speech-b", opening))
    requests.put(("speech-b", continuation))
    requests.put((None, None))

    _step_protocol.run_step_protocol_tts_worker(
        requests,
        responses,
        "test-key",
        "test-voice",
        provider_key="step",
    )

    assert previous._closed is True
    assert [event["type"] for event in recovered.sent] == [
        "tts.create",
        "tts.text.delta",
        "tts.text.done",
    ]
    assert recovered.sent[1]["data"]["text"] == opening + continuation


def test_turn_end_recovers_opening_after_new_speech_connect_exception(monkeypatch):
    initial = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "warmup"}},
        {"type": "tts.response.created"},
    ])
    previous = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "previous"}},
    ])
    recovered = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "recovered"}},
    ])
    connect_attempt = 0

    async def connect(*_args, **_kwargs):
        nonlocal connect_attempt
        connect_attempt += 1
        if connect_attempt == 3:
            raise TimeoutError("timed out during opening handshake")
        return {
            1: initial,
            2: previous,
            4: recovered,
        }[connect_attempt]

    real_sleep = _step_protocol.asyncio.sleep

    async def no_delay(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)
    monkeypatch.setattr(_step_protocol.asyncio, "sleep", no_delay)

    requests = _AutoShutdownQueue()
    responses = queue.Queue()
    opening = "This only chunk must be replayed when turn-end arrives."
    requests.put(("speech-a", "previous speech"))
    requests.put((None, None))
    requests.put(("speech-b", opening))
    requests.put((None, None))

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
    assert recovered.sent[1]["data"]["text"] == opening


def test_control_preempts_recovery_after_new_speech_connect_exception(monkeypatch):
    initial = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "warmup"}},
        {"type": "tts.response.created"},
    ])
    previous = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "previous"}},
    ])
    unexpected_recovery = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "unexpected"}},
    ])
    connect_attempt = 0

    async def connect(*_args, **_kwargs):
        nonlocal connect_attempt
        connect_attempt += 1
        if connect_attempt == 3:
            requests.put(("__interrupt__", None))
            raise TimeoutError("timed out during opening handshake")
        return [initial, previous, unexpected_recovery][connect_attempt - 1]

    real_sleep = _step_protocol.asyncio.sleep

    retry_backoffs = []

    async def no_delay(seconds):
        if seconds == 1.0:
            retry_backoffs.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)
    monkeypatch.setattr(_step_protocol.asyncio, "sleep", no_delay)

    requests = _AutoShutdownQueue()
    responses = queue.Queue()
    requests.put(("speech-a", "previous speech"))
    requests.put((None, None))
    requests.put(("speech-b", "stale opening"))
    requests.put(("speech-b", "stale continuation"))

    _step_protocol.run_step_protocol_tts_worker(
        requests,
        responses,
        "test-key",
        "test-voice",
        provider_key="step",
    )

    assert connect_attempt == 3
    assert unexpected_recovery.sent == []
    assert retry_backoffs == []
    assert not any(
        item == ("__reconnecting__", "TTS_RECONNECTING")
        for item in list(responses.queue)
    )


def test_control_preempts_recovery_when_handshake_has_no_session_id(monkeypatch):
    initial = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "warmup"}},
        {"type": "tts.response.created"},
    ])
    previous = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "previous"}},
    ])
    missing_session = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {}},
    ])
    unexpected_recovery = _FakeTtsSocket([
        {"type": "tts.connection.done", "data": {"session_id": "unexpected"}},
    ])
    connect_attempt = 0

    async def connect(*_args, **_kwargs):
        nonlocal connect_attempt
        connect_attempt += 1
        if connect_attempt == 3:
            for index in range(1000):
                requests.put(("speech-b", f"stale continuation {index}"))
            requests.put(("__interrupt__", None))
        return [
            initial,
            previous,
            missing_session,
            unexpected_recovery,
        ][connect_attempt - 1]

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)

    requests = _AutoShutdownQueue()
    responses = queue.Queue()
    requests.put(("speech-a", "previous speech"))
    requests.put((None, None))
    requests.put(("speech-b", "stale opening"))

    _step_protocol.run_step_protocol_tts_worker(
        requests,
        responses,
        "test-key",
        "test-voice",
        provider_key="step",
    )

    assert connect_attempt == 3
    assert missing_session._closed is True
    assert unexpected_recovery.sent == []


def test_interrupt_arriving_during_replacement_connect_preempts_candidate(monkeypatch):
    _run_control_arriving_during_replacement_connect(monkeypatch, "__interrupt__")


def test_shutdown_arriving_during_replacement_connect_preempts_candidate(monkeypatch):
    _run_control_arriving_during_replacement_connect(
        monkeypatch,
        TTS_SHUTDOWN_SENTINEL,
    )


def test_interrupt_preempts_when_candidate_close_fails(monkeypatch):
    _run_control_arriving_during_replacement_connect(
        monkeypatch,
        "__interrupt__",
        candidate_close_error=RuntimeError("close failed"),
    )


def test_shutdown_preempts_when_candidate_close_fails(monkeypatch):
    _run_control_arriving_during_replacement_connect(
        monkeypatch,
        TTS_SHUTDOWN_SENTINEL,
        candidate_close_error=RuntimeError("close failed"),
    )


def test_interrupt_preempts_missing_session_close_failure(monkeypatch):
    _run_control_arriving_during_replacement_connect(
        monkeypatch,
        "__interrupt__",
        candidate_close_error=RuntimeError("close failed"),
        handshake_outcome="missing_session",
    )


def test_shutdown_preempts_missing_session_close_failure(monkeypatch):
    _run_control_arriving_during_replacement_connect(
        monkeypatch,
        TTS_SHUTDOWN_SENTINEL,
        candidate_close_error=RuntimeError("close failed"),
        handshake_outcome="missing_session",
    )


def test_interrupt_preempts_handshake_timeout_close_failure(monkeypatch):
    _run_control_arriving_during_replacement_connect(
        monkeypatch,
        "__interrupt__",
        candidate_close_error=RuntimeError("close failed"),
        handshake_outcome="timeout",
    )


def test_shutdown_preempts_handshake_timeout_close_failure(monkeypatch):
    _run_control_arriving_during_replacement_connect(
        monkeypatch,
        TTS_SHUTDOWN_SENTINEL,
        candidate_close_error=RuntimeError("close failed"),
        handshake_outcome="timeout",
    )


def test_interrupt_arriving_during_missing_session_close_preempts(monkeypatch):
    _run_control_arriving_during_replacement_connect(
        monkeypatch,
        "__interrupt__",
        handshake_outcome="missing_session",
        control_arrival="close",
    )


def test_shutdown_arriving_during_handshake_timeout_close_preempts(monkeypatch):
    _run_control_arriving_during_replacement_connect(
        monkeypatch,
        TTS_SHUTDOWN_SENTINEL,
        handshake_outcome="timeout",
        control_arrival="close",
    )
