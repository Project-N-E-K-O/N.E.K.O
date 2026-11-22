"""Qwen TTS must turn a server refusal close into a round-attributed error.

DashScope refuses account-level problems (arrears is 1007 + "…account is in
good standing") with a bare WebSocket close and no error event, on a connection
that already reported "ready". The receive loops used to swallow that close, so
the user saw subtitles with no audio while the log and the frontend looked
healthy.

Reporting is only half of it: the error must be pinned to the round that was
rejected (the core blames ``current_speech_id`` otherwise), must not fire for
closes the worker recovers from by itself, and must actually reach the user.
"""

import asyncio
import json
import queue
import threading
import time
from unittest.mock import AsyncMock

import pytest
import websockets
from websockets.frames import Close

from main_logic.core import LLMSessionManager
from main_logic.core import tts_runtime as tts_runtime_mod
from main_logic.tts_client._infra import (
    SERVER_CLOSE_REFUSAL_CODES,
    classify_server_close,
)
from main_logic.tts_client.workers import qwen as qwen_mod
from main_logic.tts_client.workers.qwen import qwen_realtime_tts_worker
from tests.unit.test_free_tts_connection_lifetime import (  # noqa: F401 - shared doubles
    _Requests,
    _Socket,
)

_ARREARS_REASON = "Access denied, please make sure your account is in good standing."
_TEXT = "你好呀，今天过得怎么样？"
_SID = "speech-1"


class _Frame:
    """Minimal stand-in for a websockets close frame."""

    def __init__(self, code, reason=""):
        self.code = code
        self.reason = reason


class _Closed:
    """ConnectionClosed-shaped object with only the fields the classifier reads."""

    def __init__(self, rcvd, rcvd_then_sent=None):
        self.rcvd = rcvd
        self.sent = rcvd
        self.rcvd_then_sent = rcvd_then_sent


# ─────────────────────────── classifier rules ───────────────────────────


@pytest.mark.parametrize("code", sorted(SERVER_CLOSE_REFUSAL_CODES | {4004, 4001}))
def test_refusal_codes_become_a_structured_error(code):
    payload = classify_server_close(_Closed(_Frame(code, _ARREARS_REASON), True))
    assert payload["data"]["close_code"] == code
    assert payload["data"]["message"] == _ARREARS_REASON


@pytest.mark.parametrize(
    ("code", "why"),
    [
        (1000, "normal closure"),
        (1001, "going away"),
        (1005, "no status in frame; websockets counts it normal"),
        (1011, "server error, the worker reconnects"),
        (1012, "restart, the worker reconnects"),
        (1013, "throttled and recoverable; the free tier maps it explicitly"),
    ],
)
def test_recoverable_closes_stay_silent(code, why):
    assert classify_server_close(_Closed(_Frame(code, ""), True)) is None, why


def test_transport_drop_without_a_close_frame_is_not_a_refusal():
    assert classify_server_close(_Closed(None, None)) is None


def test_our_own_close_is_not_reported_as_a_refusal():
    # sid rotation, interrupt and shutdown all close us first and observe the
    # reply; reporting that would blame a live round for our own teardown.
    assert classify_server_close(_Closed(_Frame(4004, "Not Found"), False)) is None


def test_payload_carries_no_top_level_code_so_core_owns_classification():
    payload = classify_server_close(_Closed(_Frame(1007, _ARREARS_REASON), True))
    assert "code" not in payload


def test_a_provider_can_widen_refusals_without_changing_the_default():
    assert classify_server_close(_Closed(_Frame(1013, "quota"), True)) is None
    widened = classify_server_close(
        _Closed(_Frame(1013, "quota"), True), extra_refusal_codes={1013}
    )
    assert widened is not None


def test_a_real_websockets_exception_is_classified():
    exc = websockets.exceptions.ConnectionClosedError(Close(1007, _ARREARS_REASON), None)
    assert classify_server_close(exc)["data"]["close_code"] == 1007


# ─────────── handler: does the refusal reach the user, and whom ───────────


@pytest.mark.asyncio
async def test_handler_reports_arrears_at_once_and_blames_the_rejected_round(
    monkeypatch,
):
    """Arrears must notify on the first occurrence, pinned to the rejected round.

    ``API_ARREARS`` is in IMMEDIATE_REPORT_TTS_CODES, so it bypasses the
    "only the 3rd failure" damper; the ``__tts_sentence_failed__`` marker is what
    stops the core from blaming the round that had already started.
    """
    monkeypatch.setattr(
        tts_runtime_mod.GAME_SPEECH_AUDIO_CACHE, "fail_capture", lambda *_a: None
    )
    response_queue = queue.Queue()
    response_queue.put(("__tts_sentence_failed__", _SID, ""))
    response_queue.put(
        (
            "__error__",
            json.dumps(
                {
                    "type": "error",
                    "data": {"close_code": 1007, "message": _ARREARS_REASON},
                }
            ),
        )
    )

    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.current_speech_id = "next-round"
    mgr.tts_response_queue = response_queue
    mgr.tts_cache_lock = asyncio.Lock()
    mgr.tts_ready = True
    mgr._tts_replay_speech_id = None
    mgr._tts_replay_sentence_audio_emitted = False
    mgr._last_tts_error_code = ""
    mgr._tts_retry_notify_count = 0
    mgr._tts_runtime_is_current = lambda _runtime: True
    mgr._activate_configured_tts_fallback_after_capacity = AsyncMock(return_value=False)
    mgr._confirm_pending_ai_voice_echo = lambda *_a: None
    mgr._discard_pending_ai_voice_echo = lambda: None
    failed_rounds = []
    mgr._mark_game_speech_delivery_failed = lambda sid=None: failed_rounds.append(sid)
    notices = []

    async def send_status(message):
        notices.append(json.loads(message))

    mgr.send_status = send_status

    task = asyncio.create_task(LLMSessionManager.tts_response_handler(mgr))
    deadline = time.time() + 2.0
    while not response_queue.empty() and time.time() < deadline:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert [n["code"] for n in notices] == ["API_ARREARS"]
    # Both the marker and error branches mark this round as a delivery failure; both must apply to the rejected
    # round, rather than the next round that has already started.
    assert set(failed_rounds) == {_SID}
    assert "next-round" not in failed_rounds
    assert mgr._last_tts_error_code == "API_ARREARS"


# ─────────────────────────── worker integration ───────────────────────────


class _RefusingSocket(_Socket):
    """Socket double whose server refuses; ``observed`` says who saw it first.

    ``observed`` is set at the exact moment the close reaches the worker — either
    raised out of ``__anext__`` or carried by a failed send — so the test waits on
    the real event instead of guessing a sleep duration.
    """

    def __init__(self, code=1007, reason=_ARREARS_REASON, fail_on_send=None):
        refusal = websockets.exceptions.ConnectionClosedError(Close(code, reason), None)
        super().__init__(
            on_send={"session.update": [{"type": "session.updated"}]},
            fail_on=(
                {"input_text_buffer.append": refusal} if fail_on_send else ()
            ),
        )
        self._refusal = Close(code, reason)
        self.observed = threading.Event()

    async def __anext__(self):
        """Same contract as the shared double, but poll with a real sleep.

        ``_Socket.__anext__`` busy-waits with ``asyncio.sleep(0)`` while no event
        is queued; inside the worker that hoggs the GIL, which has been observed
        tipping a wall-clock bound in ``test_qwen_provider_fallback.py``. A 20 ms
        poll keeps the behaviour at ~1/1000 the cost.
        """
        while self._events.empty():
            if self._server_close is not None:
                self.observed.set()
                raise websockets.exceptions.ConnectionClosedError(
                    self._server_close, None
                )
            if self.closed.is_set():
                raise StopAsyncIteration
            await asyncio.sleep(0.02)
        return self._events.get()

    async def send(self, payload):
        event_type = json.loads(payload)["type"]
        if event_type in self._fail_on:
            # The sending side sees closure first: the exact 1007 path observed in the incident logs.
            self.observed.set()
        await super().send(payload)
        if event_type == "input_text_buffer.commit":
            self._server_close = self._refusal


def _drive_worker(socket_factory):
    """Run the real worker to completion in this test's own loop; return emissions.

    Deliberately thread-free: ``qwen_realtime_tts_worker`` calls ``asyncio.run``
    itself, so a synchronous caller gets a deterministic, self-contained loop.
    """
    # The callable is a barrier: _Requests runs it in the blocking get() thread,
    # so the worker stops pulling until the close has actually been observed.
    def _wait_until_observed():
        deadline = time.time() + 5
        while time.time() < deadline:
            if any(socket.observed.is_set() for socket in sockets):
                return
            time.sleep(0.01)
        raise AssertionError("worker never observed the close")

    req_q = _Requests((_SID, _TEXT), (None, None), _wait_until_observed)
    resp_q = queue.Queue()
    sockets = []

    async def _connect(*_args, **_kwargs):
        socket = socket_factory()
        sockets.append(socket)
        return socket

    original_connect = qwen_mod.websockets.connect
    qwen_mod.websockets.connect = _connect
    try:
        qwen_realtime_tts_worker(req_q, resp_q, "sk-test-key", "")
    finally:
        qwen_mod.websockets.connect = original_connect
    emitted = []
    while True:
        try:
            emitted.append(resp_q.get_nowait())
        except queue.Empty:
            break
    return emitted, sockets


def test_refusal_after_commit_is_reported_once_and_pinned_to_the_round():
    emitted, _sockets = _drive_worker(_RefusingSocket)
    heads = [item[0] for item in emitted if isinstance(item, tuple)]
    assert "__ready__" in heads, f"握手应当成功：{emitted}"

    errors = [item for item in emitted if item[0] == "__error__"]
    assert len(errors) == 1, f"一次拒绝只能报一次：{heads}"
    payload = json.loads(errors[0][1])
    assert payload["data"]["close_code"] == 1007
    assert "code" not in payload

    marker_index = heads.index("__tts_sentence_failed__")
    error_index = heads.index("__error__")
    assert error_index == marker_index + 1, "marker 必须紧邻 error 之前"
    assert emitted[marker_index][1] == _SID
    assert emitted.count(("__audio_done__", _SID)) == 1


def test_recoverable_close_reports_nothing():
    emitted, _sockets = _drive_worker(lambda: _RefusingSocket(code=1011, reason=""))
    heads = [item[0] for item in emitted if isinstance(item, tuple)]
    assert "__error__" not in heads, f"1011 是可恢复断链，不该惊动用户：{heads}"


def test_send_side_observed_refusal_is_reported_too():
    """The field case: the sender sees the 1007 first, not the receive task.

    The user's log shows the refusal surfacing as a failed text send, so a
    receive-loop-only reporter would have left that round silent with nothing on
    the queue.
    """
    emitted, _sockets = _drive_worker(
        lambda: _RefusingSocket(fail_on_send=True)
    )
    heads = [item[0] for item in emitted if isinstance(item, tuple)]
    assert "__tts_sentence_failed__" in heads and "__error__" in heads, (
        f"发送侧观察到的拒绝也必须上报：{heads}"
    )
    errors = [item for item in emitted if item[0] == "__error__"]
    assert len(errors) == 1, f"同一次拒绝只报一次：{heads}"
    assert json.loads(errors[0][1])["data"]["close_code"] == 1007
    assert heads.index("__error__") == heads.index("__tts_sentence_failed__") + 1


@pytest.mark.asyncio
async def test_handler_shows_close_code_not_provider_text(monkeypatch):
    """Unclassified refusals must not put peer-controlled text on screen."""
    monkeypatch.setattr(
        tts_runtime_mod.GAME_SPEECH_AUDIO_CACHE, "fail_capture", lambda *_a: None
    )
    response_queue = queue.Queue()
    payload = json.dumps(
        {"type": "error", "data": {"close_code": 4004, "message": "Not Found"}}
    )
    response_queue.put(("__error__", payload))

    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.current_speech_id = _SID
    mgr.tts_response_queue = response_queue
    mgr.tts_cache_lock = asyncio.Lock()
    mgr.tts_ready = True
    mgr._tts_replay_speech_id = None
    mgr._tts_replay_sentence_audio_emitted = False
    mgr._last_tts_error_code = ""
    mgr._tts_retry_notify_count = 2  # Skip the 'first two occurrences are silent' threshold and check only displayed content
    mgr._tts_runtime_is_current = lambda _runtime: True
    mgr._activate_configured_tts_fallback_after_capacity = AsyncMock(return_value=False)
    mgr._confirm_pending_ai_voice_echo = lambda *_a: None
    mgr._discard_pending_ai_voice_echo = lambda: None
    mgr._mark_game_speech_delivery_failed = lambda sid=None: None
    notices = []

    async def send_status(message):
        notices.append(json.loads(message))

    mgr.send_status = send_status

    task = asyncio.create_task(LLMSessionManager.tts_response_handler(mgr))
    deadline = time.time() + 2.0
    while not response_queue.empty() and time.time() < deadline:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert len(notices) == 1, notices
    detail = notices[0]["details"]["msg"]
    assert detail == "WebSocket close code 4004", detail
    assert "Not Found" not in json.dumps(notices[0])


class _FakeQwenSocket(_Socket):
    def __init__(self, *, fail_on_send=()):
        super().__init__(
            on_send={"session.update": [{"type": "session.updated"}]},
            fail_on=fail_on_send,
        )

    async def send(self, payload):
        if json.loads(payload)["type"] == "input_text_buffer.commit":
            if not any(event.get("text", "").strip() for event in self.sent):
                raise RuntimeError("empty text buffer")
        await super().send(payload)

    async def __anext__(self):
        while self._events.empty():
            if self._server_close is not None:
                raise websockets.exceptions.ConnectionClosedError(self._server_close, None)
            if self.closed.is_set():
                raise StopAsyncIteration
            await asyncio.sleep(0.01)
        return self._events.get()


def test_qwen_connect_failure_preserves_first_chunk_and_retries(monkeypatch):
    """F0180: transient websockets.connect failure must preserve tts_text and replay on retry."""
    initial = _FakeQwenSocket()
    recovered = _FakeQwenSocket()
    attempts = 0

    async def fake_connect(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return initial
        if attempts == 2:
            raise OSError("transient connect failure")
        return recovered

    real_sleep = qwen_mod.asyncio.sleep

    async def fast_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr(qwen_mod.websockets, "connect", fake_connect)
    monkeypatch.setattr(qwen_mod.asyncio, "sleep", fast_sleep)

    req_q = _Requests((_SID, "你好，这是第一句。"), (None, None))
    resp_q = queue.Queue()
    qwen_realtime_tts_worker(req_q, resp_q, "sk-test-key", "")

    assert attempts == 3
    sent_types = [p["type"] for p in recovered.sent]
    assert sent_types == ["session.update", "input_text_buffer.append", "input_text_buffer.commit"]
    assert recovered.sent[1]["text"] == "你好，这是第一句。"


def test_qwen_append_failure_replays_failed_and_subsequent_chunks_in_order(monkeypatch):
    """F0180: recoverable input_text_buffer.append failure must preserve chunk and replay with next."""
    initial = _FakeQwenSocket()

    class _FailSecondAppendSocket(_FakeQwenSocket):
        def __init__(self):
            super().__init__()
            self._append_count = 0

        async def send(self, payload):
            event = json.loads(payload)
            if event.get("type") == "input_text_buffer.append":
                self._append_count += 1
                if self._append_count == 2:
                    raise RuntimeError("socket dropped on chunk 2")
            await super().send(payload)

    broken = _FailSecondAppendSocket()
    recovered = _FakeQwenSocket()
    sockets = [initial, broken, recovered]

    async def fake_connect(*_args, **_kwargs):
        return sockets.pop(0)

    monkeypatch.setattr(qwen_mod.websockets, "connect", fake_connect)

    req_q = _Requests(
        (_SID, "第一段足够长的文字。"),
        (_SID, "第二段失败。"),
        (_SID, "第三段。"),
        (None, None),
    )
    resp_q = queue.Queue()
    qwen_realtime_tts_worker(req_q, resp_q, "sk-test-key", "")

    appends = [p["text"] for p in recovered.sent if p["type"] == "input_text_buffer.append"]
    assert "".join(appends) == "第一段足够长的文字。第二段失败。第三段。"
    assert any(p["type"] == "input_text_buffer.commit" for p in recovered.sent)


def test_qwen_persistent_connect_failure_stops_at_max_retries_and_recovers(monkeypatch):
    """F0180: persistent connect failures must stop at max retries, emit error/done, and recover."""
    initial = _FakeQwenSocket()
    recovered = _FakeQwenSocket()
    attempts = 0

    async def fake_connect(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return initial
        if attempts <= 1 + qwen_mod._QWEN_MAX_RECONNECT_ATTEMPTS:
            raise OSError("persistent network outage")
        return recovered

    delays = []
    real_sleep = qwen_mod.asyncio.sleep

    async def record_sleep(delay):
        if delay >= 0.5:
            delays.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(qwen_mod.websockets, "connect", fake_connect)
    monkeypatch.setattr(qwen_mod.asyncio, "sleep", record_sleep)

    req_q = _Requests((_SID, "失败的一句。"), (None, None), ("speech-2", "恢复后的一句。"), (None, None))
    resp_q = queue.Queue()
    qwen_realtime_tts_worker(req_q, resp_q, "sk-test-key", "")

    emitted = []
    while not resp_q.empty():
        emitted.append(resp_q.get_nowait())

    assert delays == [0.5, 1.0]
    assert ("__tts_sentence_failed__", _SID, "") in emitted
    errors = [json.loads(item[1]) for item in emitted if isinstance(item, tuple) and item[0] == "__error__"]
    assert any(e.get("code") == "TTS_CONNECTION_FAILED" for e in errors)
    assert ("__audio_done__", _SID) in emitted
    assert any(
        p.get("text") == "恢复后的一句。"
        for p in recovered.sent
        if p.get("type") == "input_text_buffer.append"
    )


def test_qwen_control_signal_preempts_reconnect_backoff(monkeypatch):
    """F0180: __interrupt__ queued before backoff must preempt remaining reconnect attempts."""
    initial = _FakeQwenSocket()
    attempts = 0
    req_q = queue.Queue()
    req_q.put((_SID, "被中断的话。"))

    async def fake_connect(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return initial
        # Enqueue __interrupt__ and shutdown right as the first reconnect attempt fails.
        req_q.put(("__interrupt__", None))
        req_q.put((qwen_mod.TTS_SHUTDOWN_SENTINEL, None))
        raise OSError("connect failed")

    sleep_calls = []
    real_sleep = qwen_mod.asyncio.sleep

    async def record_sleep(delay):
        if delay >= 0.5:
            sleep_calls.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(qwen_mod.websockets, "connect", fake_connect)
    monkeypatch.setattr(qwen_mod.asyncio, "sleep", record_sleep)

    resp_q = queue.Queue()
    qwen_realtime_tts_worker(req_q, resp_q, "sk-test-key", "")

    assert attempts == 2
    assert sleep_calls == []


def test_qwen_exhausted_retries_fences_remaining_chunks_of_same_speech(monkeypatch):
    """F0180: exhausted retries must fence subsequent chunks and terminal of that speech_id."""
    initial = _FakeQwenSocket()
    recovered = _FakeQwenSocket()
    attempts = 0

    async def fake_connect(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return initial
        if attempts <= 1 + qwen_mod._QWEN_MAX_RECONNECT_ATTEMPTS:
            raise OSError("persistent network outage")
        return recovered

    real_sleep = qwen_mod.asyncio.sleep

    async def fast_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr(qwen_mod.websockets, "connect", fake_connect)
    monkeypatch.setattr(qwen_mod.asyncio, "sleep", fast_sleep)

    req_q = _Requests(
        (_SID, "第一句。"),
        (_SID, "同轮第二句。"),
        (None, None),
        ("speech-2", "第二轮恢复。"),
        (None, None),
    )
    resp_q = queue.Queue()
    qwen_realtime_tts_worker(req_q, resp_q, "sk-test-key", "")

    emitted = list(resp_q.queue)
    assert emitted.count(("__tts_sentence_failed__", _SID, "")) == 1
    errors = [
        json.loads(item[1])
        for item in emitted
        if isinstance(item, tuple) and item[0] == "__error__"
    ]
    assert [err.get("code") for err in errors] == ["TTS_CONNECTION_FAILED"]
    assert emitted.count(("__audio_done__", _SID)) == 1
    appends = [
        p["text"]
        for p in recovered.sent
        if p.get("type") == "input_text_buffer.append"
    ]
    assert appends == ["第二轮恢复。"]
    assert attempts == 1 + qwen_mod._QWEN_MAX_RECONNECT_ATTEMPTS + 1


def test_qwen_finish_requested_flush_failure_retries_and_exhausts(monkeypatch):
    """F0180: transient finish_requested flush failure retries and exhaustion emits audio_done."""
    # Part 1: transient flush failure on terminal reconnect retries and succeeds.
    initial = _FakeQwenSocket()
    broken_flush = _FakeQwenSocket(fail_on_send=("session.update",))
    recovered = _FakeQwenSocket()
    recovered._on_send = {
        "session.update": [{"type": "session.updated"}],
        "input_text_buffer.commit": [{"type": "response.done"}],
    }
    sockets = iter([initial, broken_flush, recovered])

    async def fake_connect_recover(*_args, **_kwargs):
        return next(sockets)

    real_sleep = qwen_mod.asyncio.sleep

    async def fast_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr(qwen_mod.websockets, "connect", fake_connect_recover)
    monkeypatch.setattr(qwen_mod.asyncio, "sleep", fast_sleep)

    req_q = _Requests((_SID, "短句"), (None, None))
    resp_q = queue.Queue()
    qwen_realtime_tts_worker(req_q, resp_q, "sk-test-key", "")

    emitted = list(resp_q.queue)
    assert [p["type"] for p in recovered.sent] == [
        "session.update",
        "input_text_buffer.append",
        "input_text_buffer.commit",
    ]
    assert recovered.sent[1]["text"] == "短句"
    assert emitted.count(("__audio_done__", _SID)) == 1

    # Part 2: persistent finish_requested flush failure exhausts with error + audio_done and recovers.
    initial_2 = _FakeQwenSocket()
    recovered_2 = _FakeQwenSocket()
    connect_calls = 0

    async def fake_connect_exhaust(*_args, **_kwargs):
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls == 1:
            return initial_2
        if connect_calls <= 1 + qwen_mod._QWEN_MAX_RECONNECT_ATTEMPTS:
            return _FakeQwenSocket(fail_on_send=("session.update",))
        return recovered_2

    monkeypatch.setattr(qwen_mod.websockets, "connect", fake_connect_exhaust)
    req_q_2 = _Requests((_SID, "短句"), (None, None), ("speech-2", "第二轮。"), (None, None))
    resp_q_2 = queue.Queue()
    qwen_realtime_tts_worker(req_q_2, resp_q_2, "sk-test-key", "")

    emitted_2 = list(resp_q_2.queue)
    assert emitted_2.count(("__tts_sentence_failed__", _SID, "")) == 1
    assert emitted_2.count(("__audio_done__", _SID)) == 1
    assert any(
        p.get("text") == "第二轮。"
        for p in recovered_2.sent
        if p.get("type") == "input_text_buffer.append"
    )


@pytest.mark.parametrize("text", ["短句", "这是一段足够长的正常回复文本。"])
def test_qwen_commit_transient_failure_retries_and_emits_audio_done(monkeypatch, text):
    """F0180: transient input_text_buffer.commit failure must retry and emit audio_done."""
    initial = _FakeQwenSocket()
    broken_commit = _FakeQwenSocket(fail_on_send=("input_text_buffer.commit",))
    recovered = _FakeQwenSocket()
    recovered._on_send = {
        "session.update": [{"type": "session.updated"}],
        "input_text_buffer.commit": [{"type": "response.done"}],
    }
    sockets = iter([initial, broken_commit, recovered])

    async def fake_connect(*_args, **_kwargs):
        return next(sockets)

    real_sleep = qwen_mod.asyncio.sleep

    async def fast_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr(qwen_mod.websockets, "connect", fake_connect)
    monkeypatch.setattr(qwen_mod.asyncio, "sleep", fast_sleep)

    req_q = _Requests((_SID, text), (None, None))
    resp_q = queue.Queue()
    qwen_realtime_tts_worker(req_q, resp_q, "sk-test-key", "")

    emitted = list(resp_q.queue)
    assert [p["type"] for p in recovered.sent] == [
        "session.update", "input_text_buffer.append", "input_text_buffer.commit",
    ]
    assert recovered.sent[1]["text"] == text
    assert emitted.count(("__audio_done__", _SID)) == 1


@pytest.mark.parametrize("fail_event", ["session.update", "input_text_buffer.append"])
def test_qwen_connect_succeeds_then_send_fails_stays_capped_at_max_retries(
    monkeypatch, fail_event
):
    """F0180: connect success followed by immediate send failure must stay capped at max retries."""
    initial = _FakeQwenSocket()
    recovered = _FakeQwenSocket()
    attempts = 0
    delays = []

    async def fake_connect(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return initial
        if attempts <= 1 + qwen_mod._QWEN_MAX_RECONNECT_ATTEMPTS:
            return _FakeQwenSocket(fail_on_send=(fail_event,))
        return recovered

    real_sleep = qwen_mod.asyncio.sleep

    async def record_sleep(delay):
        if delay >= 0.5:
            delays.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(qwen_mod.websockets, "connect", fake_connect)
    monkeypatch.setattr(qwen_mod.asyncio, "sleep", record_sleep)

    req_q = _Requests(
        (_SID, "第一段足够长的测试文本。"),
        (None, None),
        ("speech-2", "第二轮恢复文本。"),
        (None, None),
    )
    resp_q = queue.Queue()
    qwen_realtime_tts_worker(req_q, resp_q, "sk-test-key", "")

    emitted = list(resp_q.queue)
    assert attempts == 1 + qwen_mod._QWEN_MAX_RECONNECT_ATTEMPTS + 1
    assert delays == [0.5, 1.0]
    assert emitted.count(("__tts_sentence_failed__", _SID, "")) == 1
    errors = [
        json.loads(item[1])
        for item in emitted
        if isinstance(item, tuple) and item[0] == "__error__"
    ]
    assert [err.get("code") for err in errors] == ["TTS_CONNECTION_FAILED"]
    assert emitted.count(("__audio_done__", _SID)) == 1
    assert any(
        p.get("text") == "第二轮恢复文本。"
        for p in recovered.sent
        if p.get("type") == "input_text_buffer.append"
    )


def test_interrupt_during_reconnect_discards_staged_audio(monkeypatch):
    requests = _Requests((_SID, _TEXT), (None, None))
    initial = _FakeQwenSocket()
    recovered = _FakeQwenSocket()
    sockets = iter([initial, None, recovered])

    async def connect(*_args, **_kwargs):
        socket = next(sockets)
        if socket is None:
            raise OSError("connection unavailable")
        if socket is recovered:
            requests._items.append(("__interrupt__", None))
        return socket

    monkeypatch.setattr(qwen_mod.websockets, "connect", connect)
    responses = queue.Queue()
    qwen_realtime_tts_worker(requests, responses, "test-key", "")
    assert recovered.closed.is_set()
    assert recovered.sent == []
    assert ("__audio_done__", _SID) not in responses.queue


def test_partial_audio_before_commit_failure_is_never_replayed(monkeypatch):
    requests = _Requests((_SID, _TEXT), (None, None))
    audio_received = threading.Event()

    class Socket(_FakeQwenSocket):
        async def send(self, payload):
            await super().send(payload)
            if json.loads(payload)["type"] == "input_text_buffer.append":
                self._events.put(json.dumps({"type": "response.audio.delta", "delta": "AAAAAA=="}))
                assert await asyncio.to_thread(audio_received.wait, 2)

    def resample(*args):
        audio_received.set()
        return b""

    sockets = iter([_FakeQwenSocket(), Socket(fail_on_send=("input_text_buffer.commit",))])
    connect_count = 0

    async def connect(*_args, **_kwargs):
        nonlocal connect_count
        connect_count += 1
        return next(sockets)

    monkeypatch.setattr(qwen_mod, "_resample_audio", resample)
    monkeypatch.setattr(qwen_mod.websockets, "connect", connect)
    responses = queue.Queue()
    qwen_realtime_tts_worker(requests, responses, "test-key", "")
    emitted = list(responses.queue)
    assert audio_received.is_set()
    assert connect_count == 2
    assert emitted.count(("__tts_sentence_failed__", _SID, "")) == 1
    assert emitted.count(("__audio_done__", _SID)) == 1
