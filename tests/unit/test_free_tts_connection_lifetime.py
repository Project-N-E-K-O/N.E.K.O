"""Free TTS sockets must not idle on the per-IP connection-time quota.

The Lanlan free TTS servers bill each IP by WebSocket wall-clock time. These
tests pin the three client behaviors that used to burn it without
synthesizing anything, and the close-frame rejections that replaced HTTP
status checks (the server completes the handshake before it applies limits).
"""

import asyncio
import json
import queue
import threading
import time

import pytest
import websockets
from websockets.frames import Close

from main_logic.tts_client._infra import TTS_SHUTDOWN_SENTINEL
from main_logic.tts_client.workers import _step_protocol


_LANLAN_APP_TTS_URL = "wss://www.lanlan.app/tts"
_OPENING = "This opening chunk is long enough for language detection."


class _Socket:
    """Fake TTS socket; ``on_send`` may inject server events per sent type."""

    def __init__(self, events=(), *, server_close=None, on_send=None, fail_on=()):
        self._fail_on = fail_on if isinstance(fail_on, dict) else set(fail_on)
        self._events = queue.SimpleQueue()
        for event in events:
            self._events.put(json.dumps(event))
        self._server_close = server_close
        self._on_send = on_send or {}
        self.closed = threading.Event()
        self.close_attempts = 0
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        while self._events.empty():
            if self._server_close is not None:
                raise websockets.exceptions.ConnectionClosedError(
                    self._server_close,
                    None,
                )
            if self.closed.is_set():
                raise StopAsyncIteration
            await asyncio.sleep(0)
        return self._events.get()

    async def send(self, payload):
        if self.closed.is_set():
            raise RuntimeError("socket already closed")
        event = json.loads(payload)
        if event["type"] in self._fail_on:
            failure = self._fail_on[event["type"]] if isinstance(self._fail_on, dict) else None
            successes_left = 0
            if isinstance(failure, tuple):
                successes_left, failure = failure
            already_sent = sum(1 for sent in self.sent if sent["type"] == event["type"])
            if already_sent >= successes_left:
                raise failure or RuntimeError("socket dropped during send")
        self.sent.append(event)
        for injected in self._on_send.get(event["type"], ()):
            self._events.put(json.dumps(injected))

    async def close(self):
        self.close_attempts += 1
        self.closed.set()


def _warmup_socket():
    return _Socket([
        {"type": "tts.connection.done", "data": {"session_id": "warmup"}},
        {"type": "tts.response.created"},
    ])


class _Requests:
    """Serve scripted requests; callables run in the blocking ``get`` thread.

    A callable is a barrier: requests after it have not "arrived" yet, so the
    worker's non-blocking look-ahead (``get_nowait`` on the event loop) stops
    there instead of running it on the loop thread.
    """

    def __init__(self, *items):
        self._items = list(items)

    def get(self):
        while self._items:
            item = self._items.pop(0)
            if callable(item):
                item()
                continue
            return item
        return (TTS_SHUTDOWN_SENTINEL, None)

    def get_nowait(self):
        if self._items and not callable(self._items[0]):
            return self._items.pop(0)
        raise queue.Empty


def _install_sockets(monkeypatch, *sockets):
    remaining = list(sockets)
    connects = []

    async def connect(*_args, **_kwargs):
        socket = remaining.pop(0)
        connects.append(socket)
        return socket

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)
    return connects


def _route_to_lanlan_app(monkeypatch):
    monkeypatch.setattr(_step_protocol, "_adjust_free_tts_url", lambda _url: _LANLAN_APP_TTS_URL)
    monkeypatch.setattr(_step_protocol, "_get_tts_language_code", lambda: "en-US")


def _skip_backoff(monkeypatch):
    real_sleep = _step_protocol.asyncio.sleep

    async def no_delay(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(_step_protocol.asyncio, "sleep", no_delay)


def _run(requests, responses, provider_key="free"):
    _step_protocol.run_step_protocol_tts_worker(
        requests,
        responses,
        "free-access",
        "test-voice",
        provider_key=provider_key,
    )


def _errors(responses):
    return [
        json.loads(item[1])
        for item in list(responses.queue)
        if isinstance(item, tuple) and item[0] == "__error__"
    ]


def _observe(observations, key, predicate, timeout=2.0):
    def check():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not predicate():
            time.sleep(0.005)
        observations[key] = predicate()

    return check


@pytest.mark.parametrize("provider_key", ["free", "step"])
def test_warmup_socket_is_closed_once_readiness_is_reported(monkeypatch, provider_key):
    _route_to_lanlan_app(monkeypatch)
    warmup = _warmup_socket()
    _install_sockets(monkeypatch, warmup)
    observations = {}
    responses = queue.Queue()

    # Nothing is spoken: the worker idles on the request queue. The warmup
    # socket must already be closed there, not at shutdown.
    _run(
        _Requests(_observe(observations, "warmup_closed", warmup.closed.is_set)),
        responses,
        provider_key=provider_key,
    )

    assert ("__ready__", True) in list(responses.queue)
    assert observations == {"warmup_closed": True}
    assert warmup.close_attempts == 1


class _SlowCloseSocket(_Socket):
    """A peer that does not answer the close handshake until released."""

    def __init__(self, events):
        super().__init__(events)
        self.release = threading.Event()

    async def close(self):
        self.close_attempts += 1
        while not self.release.is_set():
            await asyncio.sleep(0)
        self.closed.set()


def test_slow_warmup_close_does_not_delay_the_first_speech(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    warmup = _SlowCloseSocket([
        {"type": "tts.connection.done", "data": {"session_id": "warmup"}},
        {"type": "tts.response.created"},
    ])
    speech = _speech_socket("speech", final_events=[])
    _install_sockets(monkeypatch, warmup, speech)
    observations = {}

    def first_speech_went_out_while_warmup_closes():
        _observe(
            observations,
            "speech_created",
            lambda: any(event["type"] == "tts.create" for event in speech.sent),
        )()
        observations["warmup_still_closing"] = not warmup.closed.is_set()
        warmup.release.set()

    # Backstop so a worker that awaits the close still finishes (and fails).
    backstop = threading.Timer(1.0, warmup.release.set)
    backstop.start()
    try:
        _run(
            _Requests(
                ("speech-1", _OPENING),
                first_speech_went_out_while_warmup_closes,
            ),
            queue.Queue(),
        )
    finally:
        backstop.cancel()

    assert observations == {"speech_created": True, "warmup_still_closing": True}
    assert warmup.close_attempts == 1


def _speech_socket(session_id, *, final_events):
    return _Socket(
        [{"type": "tts.connection.done", "data": {"session_id": session_id}}],
        on_send={"tts.text.done": final_events},
    )


def test_lanlan_app_socket_closes_after_the_rounds_final_done(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    speech = _speech_socket(
        "speech",
        final_events=[
            {"type": "tts.response.audio.done", "data": {}},
            {"type": "tts.response.done", "data": {"session_id": "speech"}},
        ],
    )
    _install_sockets(monkeypatch, _warmup_socket(), speech)
    observations = {}
    responses = queue.Queue()

    _run(
        _Requests(
            ("speech-1", _OPENING),
            (None, None),
            _observe(observations, "closed_before_next_request", speech.closed.is_set),
        ),
        responses,
    )

    assert [event["type"] for event in speech.sent] == [
        "tts.create",
        "tts.text.delta",
        "tts.text.done",
    ]
    assert observations == {"closed_before_next_request": True}


def test_per_sentence_audio_done_does_not_close_the_socket(monkeypatch):
    # lanlan.app sends tts.response.audio.done after EVERY sentence; only the
    # single tts.response.done ends the round. Closing on audio.done would cut
    # the tail sentences.
    _route_to_lanlan_app(monkeypatch)
    speech = _speech_socket(
        "speech",
        final_events=[{"type": "tts.response.audio.done", "data": {}}],
    )
    _install_sockets(monkeypatch, _warmup_socket(), speech)
    observations = {}

    _run(
        _Requests(
            ("speech-1", _OPENING),
            (None, None),
            _observe(observations, "closed", speech.closed.is_set, timeout=0.3),
        ),
        queue.Queue(),
    )

    assert observations == {"closed": False}


def test_non_lanlan_app_route_keeps_existing_socket_lifetime(monkeypatch):
    speech = _speech_socket(
        "speech",
        final_events=[{"type": "tts.response.done", "data": {"session_id": "speech"}}],
    )
    _install_sockets(monkeypatch, _warmup_socket(), speech)
    observations = {}

    _run(
        _Requests(
            ("speech-1", _OPENING),
            (None, None),
            _observe(observations, "closed", speech.closed.is_set, timeout=0.3),
        ),
        queue.Queue(),
        provider_key="step",
    )

    assert observations == {"closed": False}


def _rejecting_socket(close_code, reason):
    return _Socket(server_close=Close(close_code, reason))


def test_quota_rejected_speech_is_reported_once_and_not_retried_per_chunk(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    _skip_backoff(monkeypatch)
    next_speech = _speech_socket("next", final_events=[])
    connects = _install_sockets(
        monkeypatch,
        _warmup_socket(),
        _rejecting_socket(1008, "Total daily connection time limit reached"),
        next_speech,
    )
    responses = queue.Queue()

    _run(
        _Requests(
            ("speech-1", _OPENING),
            ("speech-1", " A second chunk of the rejected reply."),
            (None, None),
            # The next reply is the implicit retry and must not be blocked.
            ("speech-2", _OPENING),
        ),
        responses,
    )

    assert len(connects) == 3
    assert _errors(responses) == [{
        "code": "API_QUOTA_TIME",
        "data": {
            "close_code": 1008,
            "message": "Total daily connection time limit reached",
        },
    }]
    assert ("__reconnecting__", "TTS_RECONNECTING") not in list(responses.queue)
    assert next_speech.sent[0]["type"] == "tts.create"
    # The dropped terminal still closes the stream, so completion waiters
    # (already told the round failed) do not sit out their timeout.
    assert ("__audio_done__", "speech-1") in list(responses.queue)


def test_quota_rejection_at_startup_reports_quota_before_not_ready(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    _install_sockets(
        monkeypatch,
        _rejecting_socket(1008, "Total daily connection time limit reached"),
    )
    responses = queue.Queue()

    _run(_Requests(), responses)

    items = list(responses.queue)
    assert items[-1] == ("__ready__", False)
    assert _errors(responses)[0]["code"] == "API_QUOTA_TIME"


def test_mid_round_quota_close_is_reported(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    speech = _Socket(
        [{"type": "tts.connection.done", "data": {"session_id": "speech"}}],
        on_send={"tts.text.done": []},
    )
    _install_sockets(monkeypatch, _warmup_socket(), speech)
    responses = queue.Queue()

    def server_spends_quota():
        speech._server_close = Close(1008, "Total connection time limit reached for today")

    _run(
        _Requests(
            ("speech-1", _OPENING),
            server_spends_quota,
            _observe({}, "reported", lambda: bool(_errors(responses))),
        ),
        responses,
    )

    assert [error["code"] for error in _errors(responses)] == ["API_QUOTA_TIME"]


def test_mid_round_rejection_ends_the_rest_of_that_speech(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    _skip_backoff(monkeypatch)
    speech = _Socket(
        [{"type": "tts.connection.done", "data": {"session_id": "speech"}}],
    )
    next_speech = _speech_socket("next", final_events=[])
    connects = _install_sockets(monkeypatch, _warmup_socket(), speech, next_speech)
    responses = queue.Queue()

    def server_spends_quota():
        speech._server_close = Close(1008, "Total connection time limit reached for today")

    _run(
        _Requests(
            ("speech-1", _OPENING),
            server_spends_quota,
            _observe({}, "reported", lambda: bool(_errors(responses))),
            ("speech-1", " More text of the rejected reply."),
            (None, None),
            ("speech-2", _OPENING),
        ),
        responses,
    )

    # No reconnect for the rest of speech-1; speech-2 is the next retry.
    assert connects == [connects[0], speech, next_speech]
    assert [event["type"] for event in speech.sent] == ["tts.create", "tts.text.delta"]
    assert [error["code"] for error in _errors(responses)] == ["API_QUOTA_TIME"]


def test_rejection_while_replaying_buffered_text_ends_that_speech(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    _skip_backoff(monkeypatch)
    dropped = _Socket(
        [{"type": "tts.connection.done", "data": {"session_id": "dropped"}}],
        fail_on={"tts.text.delta"},
    )
    next_speech = _speech_socket("next", final_events=[])
    connects = _install_sockets(
        monkeypatch,
        _warmup_socket(),
        dropped,
        _rejecting_socket(1008, "Total connection time limit reached for today"),
        next_speech,
    )
    responses = queue.Queue()

    _run(
        _Requests(
            ("speech-1", _OPENING),
            ("speech-1", " More text of the rejected reply."),
            (None, None),
            ("speech-2", _OPENING),
        ),
        responses,
    )

    assert len(connects) == 4
    assert [error["code"] for error in _errors(responses)] == ["API_QUOTA_TIME"]
    assert next_speech.sent[0]["type"] == "tts.create"


@pytest.mark.parametrize(
    "failing_send",
    [
        "tts.create",
        "tts.text.delta",  # the buffered opening, sent with tts.create
        ("tts.text.delta", 1),  # a later chunk on the live socket
    ],
)
def test_send_that_hits_a_server_rejection_ends_that_speech(monkeypatch, failing_send):
    # The sender can see the close before the receive task, whose
    # cancellation then prevents it from reporting.
    _route_to_lanlan_app(monkeypatch)
    _skip_backoff(monkeypatch)
    rejection = websockets.exceptions.ConnectionClosedError(
        Close(1008, "Total connection time limit reached for today"),
        None,
    )
    send_type, successes = (
        failing_send if isinstance(failing_send, tuple) else (failing_send, 0)
    )
    speech = _Socket(
        [{"type": "tts.connection.done", "data": {"session_id": "speech"}}],
        fail_on={send_type: (successes, rejection)},
    )
    next_speech = _speech_socket("next", final_events=[])
    connects = _install_sockets(monkeypatch, _warmup_socket(), speech, next_speech)
    responses = queue.Queue()

    _run(
        _Requests(
            ("speech-1", _OPENING),
            ("speech-1", " More text of the rejected reply."),
            (None, None),
            ("speech-2", _OPENING),
        ),
        responses,
    )

    assert connects == [connects[0], speech, next_speech]
    assert {error["code"] for error in _errors(responses)} == {"API_QUOTA_TIME"}
    assert next_speech.sent[0]["type"] == "tts.create"
    assert ("__audio_done__", "speech-1") in list(responses.queue)


class _CloseDuringSendSocket(_Socket):
    """The server's close lands while a send is in flight.

    The receive task observes the close during the send's yields, then the
    send itself fails with the same close.
    """

    def __init__(self, events, *, close, fail_type, successes):
        super().__init__(events)
        self._close = close
        self._fail_type = fail_type
        self._successes = successes

    async def send(self, payload):
        event = json.loads(payload)
        already_sent = sum(1 for sent in self.sent if sent["type"] == event["type"])
        if event["type"] == self._fail_type and already_sent >= self._successes:
            self._server_close = self._close
            for _ in range(20):
                await asyncio.sleep(0)
            raise websockets.exceptions.ConnectionClosedError(self._close, None)
        await super().send(payload)


def test_one_close_seen_by_sender_and_receiver_is_reported_once(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    _skip_backoff(monkeypatch)
    speech = _CloseDuringSendSocket(
        [{"type": "tts.connection.done", "data": {"session_id": "speech"}}],
        close=Close(1013, "Rate limit exceeded. Try again later."),
        fail_type="tts.text.delta",
        successes=1,
    )
    _install_sockets(monkeypatch, _warmup_socket(), speech)
    responses = queue.Queue()

    _run(
        _Requests(
            ("speech-1", _OPENING),
            ("speech-1", " A later chunk sent while the close arrives."),
            (None, None),
        ),
        responses,
    )

    assert [error["code"] for error in _errors(responses)] == ["API_RATE_LIMIT"]


@pytest.mark.parametrize(
    "close",
    [Close(1008, "Access denied: IP is blacklisted"), Close(4004, "Not Found")],
)
def test_permanent_rejection_hands_retry_policy_to_the_core(monkeypatch, close):
    _route_to_lanlan_app(monkeypatch)
    _skip_backoff(monkeypatch)
    connects = _install_sockets(monkeypatch, _warmup_socket(), _rejecting_socket(close.code, close.reason))
    responses = queue.Queue()

    _run(
        _Requests(
            ("speech-1", _OPENING),
            ("speech-2", _OPENING),
        ),
        responses,
    )

    # No connect for speech-2: the worker reported not-ready and exited, so
    # the core's NO_RETRY gate decides from here.
    assert len(connects) == 2
    items = list(responses.queue)
    assert items[-1] == ("__ready__", False)
    # The rejected round's terminal never gets processed; its stream end is
    # still signalled before the worker exits.
    assert items[-2] == ("__audio_done__", "speech-1")


@pytest.mark.parametrize(
    ("close", "expected_code"),
    (
        (Close(1008, "Total daily connection time limit reached"), "API_QUOTA_TIME"),
        (Close(1008, "Total connection time limit reached for today"), "API_QUOTA_TIME"),
        (Close(1008, "Access denied: IP not in whitelist"), "API_ACCESS_DENIED"),
        (Close(1008, "Access denied: IP is blacklisted"), "API_ACCESS_DENIED"),
        (Close(1013, "Rate limit exceeded. Try again later."), "API_RATE_LIMIT"),
        (Close(1013, "Too many concurrent requests"), "API_RATE_LIMIT"),
        (Close(4004, "Not Found"), "TTS_CONFIG_INVALID"),
        (Close(1011, "Internal server error"), None),
        (Close(1008, "Invalid first message"), None),
        (Close(1000, ""), None),
    ),
)
def test_free_server_close_frames_map_to_stable_codes(close, expected_code):
    classified = _step_protocol._classify_lanlan_server_close(
        websockets.exceptions.ConnectionClosedError(close, None)
    )
    if expected_code is None:
        assert classified is None
    else:
        assert classified["code"] == expected_code


def test_non_close_exceptions_and_local_closes_are_not_rejections():
    assert _step_protocol._classify_lanlan_server_close(RuntimeError("boom")) is None
    assert _step_protocol._classify_lanlan_server_close(
        websockets.exceptions.ConnectionClosedOK(None, Close(1000, ""))
    ) is None
