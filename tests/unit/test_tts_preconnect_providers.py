"""Qwen, Grok and ElevenLabs open the next speech's socket while the LLM thinks.

These workers used to connect only once a speech's first text arrived. They
now answer core's ``TTS_PRECONNECT_SENTINEL`` through the shared
``SpareSocket`` (main_logic/tts_client/_preconnect.py). Unlike the Step
protocol on lanlan.app, their upstreams keep a finished round's socket open
until the next speech, so a preconnect first retires that socket when its
round is over; the spare opens only once it has closed. Every test checks
that no connect ever happens beside another open or closing socket.
"""

import asyncio
import base64
import json
import queue
import threading
import time
from dataclasses import dataclass
from functools import partial
from typing import Callable

import pytest
import websockets
from websockets.protocol import State

from main_logic.tts_client import TTS_PRECONNECT_SENTINEL, _preconnect
from main_logic.tts_client._infra import TTS_AUDIO_DONE_SENTINEL, TTS_SHUTDOWN_SENTINEL
from main_logic.tts_client._preconnect import worker_supports_preconnect
from main_logic.tts_client.workers import elevenlabs as elevenlabs_mod
from main_logic.tts_client.workers import grok as grok_mod
from main_logic.tts_client.workers import qwen as qwen_mod
from tests.unit.test_free_tts_connection_lifetime import _Requests, _observe

pytestmark = pytest.mark.unit

_PRECONNECT = (TTS_PRECONNECT_SENTINEL, None)
_OPENING = "This opening chunk is long enough for language detection."
_PCM_B64 = base64.b64encode(b"\x01\x00" * 480).decode("ascii")


class _Sock:
    """Fake provider socket with websockets-style state and scripted replies."""

    def __init__(self, name, kind, *, greeting=(), replies=None, slow_close=False):
        self.name = name
        self._kind = kind
        self._events = queue.SimpleQueue()
        for event in greeting:
            self._put(event)
        self._replies = replies or {}
        self.sent = []
        self.send_attempts = 0
        self.close_attempts = 0
        self.closing = False
        self.closed = threading.Event()
        self.release = threading.Event()
        if not slow_close:
            self.release.set()

    def _put(self, event):
        self._events.put(event if isinstance(event, (str, bytes)) else json.dumps(event))

    @property
    def state(self):
        if self.closed.is_set():
            return State.CLOSED
        return State.CLOSING if self.closing else State.OPEN

    def __aiter__(self):
        return self

    async def __anext__(self):
        while self._events.empty():
            if self.closing or self.closed.is_set():
                raise StopAsyncIteration
            await asyncio.sleep(0.005)
        return self._events.get()

    async def send(self, payload):
        self.send_attempts += 1
        if self.closing or self.closed.is_set():
            raise RuntimeError(f"{self.name}: socket already closed")
        event = json.loads(payload)
        self.sent.append(event)
        for reply in self._replies.get(self._kind(event), ()):
            self._put(reply)

    async def close(self):
        self.close_attempts += 1
        self.closing = True
        while not self.release.is_set():
            await asyncio.sleep(0.005)
        self.closed.set()


def _type_kind(event):
    return event.get("type")


def _elevenlabs_kind(event):
    if "voices" in event:
        return "init"
    if event.get("close_socket"):
        return "close_socket"
    return "text"


@dataclass
class _Provider:
    name: str
    module: object
    run: Callable
    has_warmup: bool
    make_socket: Callable
    spoken: Callable
    quiet_guard: bool


def _qwen_socket(name, slow_close=False):
    return _Sock(
        name,
        _type_kind,
        replies={
            "session.update": [{"type": "session.updated"}],
            "input_text_buffer.commit": [
                {"type": "response.audio.delta", "delta": _PCM_B64},
                {"type": "response.done"},
            ],
        },
        slow_close=slow_close,
    )


def _grok_socket(name, slow_close=False):
    return _Sock(
        name,
        _type_kind,
        replies={"text.done": [
            {"type": "audio.delta", "delta": _PCM_B64},
            {"type": "audio.done"},
        ]},
        slow_close=slow_close,
    )


def _elevenlabs_socket(name, slow_close=False):
    return _Sock(
        name,
        _elevenlabs_kind,
        replies={"close_socket": [{"audio": _PCM_B64}, {"isFinal": True}]},
        slow_close=slow_close,
    )


_PROVIDERS = [
    _Provider(
        "qwen",
        qwen_mod,
        lambda requests, responses: qwen_mod.qwen_realtime_tts_worker(
            requests, responses, "sk-test", "Momo",
        ),
        True,
        _qwen_socket,
        lambda sock: [e["text"] for e in sock.sent if e.get("type") == "input_text_buffer.append"],
        True,
    ),
    _Provider(
        "grok",
        grok_mod,
        lambda requests, responses: grok_mod.grok_streaming_tts_worker(
            requests, responses, "xai-test", "eve",
        ),
        True,
        _grok_socket,
        lambda sock: [e["delta"] for e in sock.sent if e.get("type") == "text.delta"],
        True,
    ),
    _Provider(
        "elevenlabs",
        elevenlabs_mod,
        lambda requests, responses: elevenlabs_mod.elevenlabs_tts_worker(
            requests, responses, "xi-test", "21m00Tcm4TlvDq8ikWAM",
        ),
        False,
        _elevenlabs_socket,
        lambda sock: [e["inputs"][0]["text"] for e in sock.sent if "inputs" in e],
        False,
    ),
]


@pytest.fixture(params=_PROVIDERS, ids=lambda p: p.name)
def provider(request, monkeypatch):
    spec = request.param
    monkeypatch.setattr(qwen_mod, "_resolve_qwen_realtime_tts_url", lambda: "wss://dashscope.test/realtime")
    for module in (qwen_mod, grok_mod, elevenlabs_mod):
        monkeypatch.setattr(module, "_record_tts_telemetry", lambda *_a, **_k: None)
    # Most tests are about the retire itself, not the quiet guard.
    monkeypatch.setattr(qwen_mod, "FINISHED_ROUND_QUIET_SECONDS", 0.0)
    monkeypatch.setattr(grok_mod, "FINISHED_ROUND_QUIET_SECONDS", 0.0)
    return spec


class _Connects:
    """Hands out scripted sockets and records what else was alive at each connect."""

    def __init__(self, monkeypatch, sockets):
        self._remaining = list(sockets)
        self.sockets = []
        self.held_at_connect = []

        async def connect(*_args, **kwargs):
            self.held_at_connect.append(
                [s.name for s in self.sockets if s.state is not State.CLOSED]
            )
            socket = self._remaining.pop(0)
            socket.connect_kwargs = kwargs
            self.sockets.append(socket)
            return socket

        monkeypatch.setattr(websockets, "connect", connect)

    def __len__(self):
        return len(self.sockets)

    def names(self):
        return [s.name for s in self.sockets]

    def assert_one_connection_at_a_time(self):
        assert all(not held for held in self.held_at_connect), (
            f"connected beside a live socket: {self.held_at_connect}"
        )


def _wait(predicate, timeout=3.0):
    def check():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not predicate():
            time.sleep(0.005)

    return check


def _pause(seconds):
    return lambda: time.sleep(seconds)


def _audio_done(responses, speech_id):
    return lambda: (TTS_AUDIO_DONE_SENTINEL, speech_id) in list(responses.queue)


def _warmups(provider):
    return [provider.make_socket("warmup")] if provider.has_warmup else []


# ───────────────────────────── adoption ─────────────────────────────


def test_first_speech_adopts_the_preconnected_socket(provider, monkeypatch):
    spare = provider.make_socket("spare")
    warmups = _warmups(provider)
    connects = _Connects(monkeypatch, [*warmups, spare])
    responses = queue.Queue()

    provider.run(
        _Requests(
            _PRECONNECT,
            _wait(lambda: len(connects) == len(warmups) + 1),
            ("speech-1", _OPENING),
            (None, None),
            _wait(_audio_done(responses, "speech-1")),
        ),
        responses,
    )

    assert connects.names() == [s.name for s in warmups] + ["spare"], (
        "the speech must not open a socket of its own"
    )
    assert provider.spoken(spare) == [_OPENING]
    assert (TTS_AUDIO_DONE_SENTINEL, "speech-1") in list(responses.queue)
    connects.assert_one_connection_at_a_time()


def test_finished_round_socket_is_retired_and_replaced_before_the_next_text(provider, monkeypatch):
    first = provider.make_socket("first")
    spare = provider.make_socket("spare")
    warmups = _warmups(provider)
    connects = _Connects(monkeypatch, [*warmups, first, spare])
    responses = queue.Queue()
    observations = {}

    provider.run(
        _Requests(
            ("speech-1", _OPENING),
            (None, None),
            _wait(_audio_done(responses, "speech-1")),
            _PRECONNECT,
            _wait(lambda: len(connects) == len(warmups) + 2),
            _observe(observations, "spare_open_before_text", lambda: len(connects) == len(warmups) + 2),
            ("speech-2", _OPENING),
            (None, None),
            _wait(_audio_done(responses, "speech-2")),
        ),
        responses,
    )

    assert observations == {"spare_open_before_text": True}
    assert first.closed.is_set()
    assert provider.spoken(spare) == [_OPENING]
    connects.assert_one_connection_at_a_time()


def test_unfinished_round_keeps_its_socket_and_gets_no_spare(provider, monkeypatch):
    # The round's terminal request has not gone out: its audio may still be
    # coming, and a spare beside it would be a second connection.
    first = provider.make_socket("first")
    second = provider.make_socket("second")
    warmups = _warmups(provider)
    connects = _Connects(monkeypatch, [*warmups, first, second])
    observations = {}

    provider.run(
        _Requests(
            ("speech-1", _OPENING),
            _wait(lambda: provider.spoken(first) == [_OPENING]),
            _PRECONNECT,
            _pause(0.1),
            _observe(observations, "connects", lambda: len(connects), timeout=0),
            _observe(observations, "first_open", lambda: first.state is State.OPEN, timeout=0),
            ("speech-2", _OPENING),
            (None, None),
        ),
        queue.Queue(),
    )

    assert observations == {"connects": len(warmups) + 1, "first_open": True}
    assert provider.spoken(second) == [_OPENING]
    connects.assert_one_connection_at_a_time()


def test_round_with_recent_audio_is_not_retired(provider, monkeypatch):
    if not provider.quiet_guard:
        pytest.skip("the session-final event already ends this provider's round")
    # A done event may land ahead of the round's last audio; a round that
    # was still producing audio a moment ago keeps its socket.
    monkeypatch.setattr(provider.module, "FINISHED_ROUND_QUIET_SECONDS", 60.0)
    first = provider.make_socket("first")
    second = provider.make_socket("second")
    warmups = _warmups(provider)
    connects = _Connects(monkeypatch, [*warmups, first, second])
    responses = queue.Queue()
    observations = {}

    provider.run(
        _Requests(
            ("speech-1", _OPENING),
            (None, None),
            _wait(_audio_done(responses, "speech-1")),
            _PRECONNECT,
            _pause(0.1),
            _observe(observations, "connects", lambda: len(connects), timeout=0),
            _observe(observations, "first_open", lambda: first.state is State.OPEN, timeout=0),
            ("speech-2", _OPENING),
            (None, None),
        ),
        responses,
    )

    assert observations == {"connects": len(warmups) + 1, "first_open": True}
    assert provider.spoken(second) == [_OPENING]
    connects.assert_one_connection_at_a_time()


# ─────────────────────────── spare lifetime ───────────────────────────


def test_spare_dropped_by_the_server_is_not_adopted(provider, monkeypatch):
    spare = provider.make_socket("spare")
    fresh = provider.make_socket("fresh")
    warmups = _warmups(provider)
    connects = _Connects(monkeypatch, [*warmups, spare, fresh])

    provider.run(
        _Requests(
            _PRECONNECT,
            _wait(lambda: len(connects) == len(warmups) + 1),
            _pause(0.05),
            spare.closed.set,  # the server closed it while it idled
            ("speech-1", _OPENING),
            (None, None),
        ),
        queue.Queue(),
    )

    assert spare.send_attempts == 0
    assert provider.spoken(fresh) == [_OPENING]


def test_unused_spare_is_closed_after_the_idle_window(provider, monkeypatch):
    monkeypatch.setattr(_preconnect, "PRECONNECT_IDLE_SECONDS", 0.05)
    spare = provider.make_socket("spare")
    fresh = provider.make_socket("fresh")
    warmups = _warmups(provider)
    connects = _Connects(monkeypatch, [*warmups, spare, fresh])
    observations = {}

    provider.run(
        _Requests(
            _PRECONNECT,
            _observe(observations, "spare_closed", spare.closed.is_set),
            ("speech-1", _OPENING),
            (None, None),
        ),
        queue.Queue(),
    )

    assert observations == {"spare_closed": True}
    assert spare.sent == []
    assert provider.spoken(fresh) == [_OPENING]
    connects.assert_one_connection_at_a_time()


def test_shutdown_closes_an_unused_spare(provider, monkeypatch):
    spare = provider.make_socket("spare")
    warmups = _warmups(provider)
    connects = _Connects(monkeypatch, [*warmups, spare])

    provider.run(
        _Requests(_PRECONNECT, _wait(lambda: len(connects) == len(warmups) + 1), _pause(0.05)),
        queue.Queue(),
    )

    assert spare.closed.is_set()
    assert spare.sent == []


def test_spare_socket_uses_a_short_close_timeout(provider, monkeypatch):
    spare = provider.make_socket("spare")
    warmups = _warmups(provider)
    connects = _Connects(monkeypatch, [*warmups, spare])

    provider.run(
        _Requests(_PRECONNECT, _wait(lambda: len(connects) == len(warmups) + 1), ("speech-1", _OPENING), (None, None)),
        queue.Queue(),
    )

    assert 0 < spare.connect_kwargs.get("close_timeout", 10.0) <= 2.0


# ────────────────── retired socket still closing ──────────────────


def test_speech_waits_for_the_retired_socket_and_opens_only_one_connection(provider, monkeypatch):
    # The next speech arrives while the retired round socket is still
    # closing: the preconnect waiting on that close is dropped, the speech
    # connects itself once the close is done, and nothing else connects.
    first = provider.make_socket("first", slow_close=True)
    fresh = provider.make_socket("fresh")
    warmups = _warmups(provider)
    connects = _Connects(monkeypatch, [*warmups, first, fresh])
    responses = queue.Queue()
    release = threading.Timer(0.3, first.release.set)
    backstop = threading.Timer(3.0, first.release.set)
    backstop.start()
    try:
        provider.run(
            _Requests(
                ("speech-1", _OPENING),
                (None, None),
                _wait(_audio_done(responses, "speech-1")),
                _PRECONNECT,
                _wait(lambda: first.closing),
                release.start,
                ("speech-2", _OPENING),
                (None, None),
                _wait(_audio_done(responses, "speech-2")),
                _pause(0.1),
            ),
            responses,
        )
    finally:
        release.cancel()
        backstop.cancel()

    assert connects.names() == [s.name for s in warmups] + ["first", "fresh"]
    assert provider.spoken(fresh) == [_OPENING]
    connects.assert_one_connection_at_a_time()


def test_interrupt_during_the_wait_for_a_retired_close_drops_the_connect(provider, monkeypatch):
    first = provider.make_socket("first", slow_close=True)
    fresh = provider.make_socket("fresh")
    warmups = _warmups(provider)
    connects = _Connects(monkeypatch, [*warmups, first, fresh])
    responses = queue.Queue()
    requests = queue.Queue()  # live queue: the interrupt lands while the worker waits

    def drive():
        requests.put(("speech-1", _OPENING))
        requests.put((None, None))
        _wait(_audio_done(responses, "speech-1"))()
        requests.put(_PRECONNECT)
        _wait(lambda: first.closing)()
        requests.put(("speech-2", _OPENING))
        time.sleep(0.2)  # speech-2 is now waiting for the close
        requests.put(("__interrupt__", None))
        time.sleep(0.3)
        first.release.set()
        time.sleep(0.1)
        requests.put((TTS_SHUTDOWN_SENTINEL, None))

    driver = threading.Thread(target=drive, daemon=True)
    driver.start()
    backstop = threading.Timer(5.0, first.release.set)
    backstop.start()
    try:
        provider.run(requests, responses)
    finally:
        backstop.cancel()
        driver.join(timeout=5)

    assert fresh not in connects.sockets, "the interrupted speech still connected"
    assert first.closed.is_set()


# ──────────────────────────── declaration ────────────────────────────


def test_partial_bound_worker_still_declares_preconnect():
    # The ElevenLabs resolver binds base_url with functools.partial.
    bound = partial(elevenlabs_mod.elevenlabs_tts_worker, base_url="https://api.elevenlabs.io")
    assert worker_supports_preconnect(bound)
    assert worker_supports_preconnect(partial(bound, voice_id="v"))


def test_worker_without_the_flag_is_not_sent_preconnects():
    from main_logic.tts_client.workers import gemini

    assert not worker_supports_preconnect(gemini.gemini_tts_worker)
    assert not worker_supports_preconnect(partial(gemini.gemini_tts_worker))
