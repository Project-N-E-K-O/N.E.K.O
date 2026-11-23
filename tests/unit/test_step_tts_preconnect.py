"""Step-protocol TTS opens the next speech's socket while the LLM thinks.

Every speech used to connect only once its first text arrived, putting a full
TLS + WebSocket handshake (about a second to www.lanlan.app) in front of each
reply's first audio. Core now sends ``TTS_PRECONNECT_SENTINEL`` when a reply's
LLM request starts; these tests pin how the worker opens, hands over, expires
and drops that socket, and the core/client side that sends the request.
"""

import asyncio
import queue
import threading
import time
from queue import Queue
from unittest.mock import MagicMock

import pytest
from websockets.frames import Close
from websockets.protocol import State

from main_logic.core import LLMSessionManager
from main_logic.tts_client import TTS_PRECONNECT_SENTINEL
from main_logic.tts_client.workers import _step_protocol
from tests.unit.test_free_tts_connection_lifetime import (
    _OPENING,
    _Requests,
    _Socket,
    _errors,
    _install_sockets,
    _observe,
    _route_to_lanlan_app,
    _run,
    _warmup_socket,
)

pytestmark = pytest.mark.unit

_PRECONNECT = (TTS_PRECONNECT_SENTINEL, None)


class _StatefulSocket(_Socket):
    """A fake socket that reports its open state like a websockets connection."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.send_attempts = 0

    @property
    def state(self):
        return State.CLOSED if self.closed.is_set() else State.OPEN

    async def send(self, payload):
        self.send_attempts += 1
        await super().send(payload)


class _SlowCloseStatefulSocket(_StatefulSocket):
    """A peer that does not finish the close handshake until released."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.release = threading.Event()
        self.closing = False

    @property
    def state(self):
        if self.closed.is_set():
            return State.CLOSED
        return State.CLOSING if self.closing else State.OPEN

    async def __anext__(self):
        if self.closing:
            raise StopAsyncIteration
        return await super().__anext__()

    async def close(self):
        self.close_attempts += 1
        self.closing = True
        while not self.release.is_set():
            await asyncio.sleep(0.005)
        self.closed.set()


def _socket(session_id, *, final_events=(), **kwargs):
    return _StatefulSocket(
        [{"type": "tts.connection.done", "data": {"session_id": session_id}}],
        on_send={"tts.text.done": list(final_events)},
        **kwargs,
    )


def _app_round_end(session_id):
    return [
        {"type": "tts.response.audio.done", "data": {}},
        {"type": "tts.response.done", "data": {"session_id": session_id}},
    ]


def _sent_types(socket):
    return [event["type"] for event in socket.sent]


def _wait(predicate, timeout=2.0):
    def check():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not predicate():
            time.sleep(0.005)

    return check


def _pause(seconds):
    return lambda: time.sleep(seconds)


def test_first_speech_adopts_the_preconnected_socket(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    spare = _socket("spare", final_events=_app_round_end("spare"))
    connects = _install_sockets(monkeypatch, _warmup_socket(), spare)

    _run(_Requests(_PRECONNECT, ("speech-1", _OPENING), (None, None)), queue.Queue())

    assert connects[1:] == [spare], "the speech must not open a socket of its own"
    assert _sent_types(spare) == ["tts.create", "tts.text.delta", "tts.text.done"]
    assert spare.sent[0]["data"]["session_id"] == "spare"


def test_next_speech_adopts_a_socket_opened_after_the_previous_round_closed(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    first = _socket("first", final_events=_app_round_end("first"))
    spare = _socket("spare", final_events=_app_round_end("spare"))
    connects = _install_sockets(monkeypatch, _warmup_socket(), first, spare)
    observations = {}

    _run(
        _Requests(
            ("speech-1", _OPENING),
            (None, None),
            _wait(first.closed.is_set),
            _PRECONNECT,
            _wait(lambda: len(connects) == 3),
            _observe(observations, "spare_opened_before_text", lambda: len(connects) == 3),
            ("speech-2", _OPENING),
            (None, None),
        ),
        queue.Queue(),
    )

    assert observations == {"spare_opened_before_text": True}
    assert connects[1:] == [first, spare]
    assert _sent_types(spare) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_no_second_socket_while_the_previous_round_socket_is_open(monkeypatch):
    # Off lanlan.app a finished round keeps its socket until the next speech;
    # a spare beside it would be a second concurrent connection on one IP.
    first = _socket("first")
    second = _socket("second")
    connects = _install_sockets(monkeypatch, _warmup_socket(), first, second)
    observations = {}

    _run(
        _Requests(
            ("speech-1", _OPENING),
            (None, None),
            _PRECONNECT,
            _pause(0.1),
            _observe(observations, "connects_before_speech_2", lambda: len(connects), timeout=0),
            ("speech-2", _OPENING),
            (None, None),
        ),
        queue.Queue(),
        provider_key="step",
    )

    assert observations == {"connects_before_speech_2": 2}
    assert first.closed.is_set(), "speech-2 still replaces the old socket as before"
    assert _sent_types(second) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_unused_spare_is_closed_after_the_idle_window(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    monkeypatch.setattr(_step_protocol, "_PRECONNECT_IDLE_SECONDS", 0.05)
    spare = _socket("spare")
    fresh = _socket("fresh", final_events=_app_round_end("fresh"))
    _install_sockets(monkeypatch, _warmup_socket(), spare, fresh)
    observations = {}

    _run(
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
    assert _sent_types(fresh) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_spare_dropped_by_the_server_is_not_adopted(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    spare = _socket("spare")
    fresh = _socket("fresh", final_events=_app_round_end("fresh"))
    connects = _install_sockets(monkeypatch, _warmup_socket(), spare, fresh)

    _run(
        _Requests(
            _PRECONNECT,
            _wait(lambda: len(connects) == 2),
            _pause(0.05),
            spare.closed.set,  # the server closed it while it idled
            ("speech-1", _OPENING),
            (None, None),
        ),
        queue.Queue(),
    )

    # Checked before use, not discovered by a failed tts.create and a
    # recovery reconnect: the speech goes straight to a fresh socket.
    assert spare.send_attempts == 0
    assert _sent_types(fresh) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_failed_spare_handshake_falls_back_to_a_normal_connect_silently(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    broken = _StatefulSocket(server_close=Close(1011, "internal error"))
    fresh = _socket("fresh", final_events=_app_round_end("fresh"))
    _install_sockets(monkeypatch, _warmup_socket(), broken, fresh)
    responses = queue.Queue()

    _run(_Requests(_PRECONNECT, ("speech-1", _OPENING), (None, None)), responses)

    assert broken.closed.is_set()
    assert _sent_types(fresh) == ["tts.create", "tts.text.delta", "tts.text.done"]
    assert _errors(responses) == []


def test_spare_still_handshaking_gets_only_the_normal_confirm_window(monkeypatch):
    # Its own handshake bound is long because it normally runs while the LLM
    # thinks; once a speech waits on it, it must not hold that speech (or a
    # queued interrupt) longer than a fresh socket's confirmation window.
    _route_to_lanlan_app(monkeypatch)
    monkeypatch.setattr(_step_protocol, "_PRECONNECT_HANDSHAKE_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(_step_protocol, "_NEW_SOCKET_CONFIRM_TIMEOUT_SECONDS", 0.1)
    stuck = _StatefulSocket()  # never sends tts.connection.done
    fresh = _socket("fresh", final_events=_app_round_end("fresh"))
    _install_sockets(monkeypatch, _warmup_socket(), stuck, fresh)

    started = time.monotonic()
    _run(_Requests(_PRECONNECT, ("speech-1", _OPENING), (None, None)), queue.Queue())
    elapsed = time.monotonic() - started

    assert elapsed < 2.5, f"waited {elapsed:.1f}s on a spare stuck in its handshake"
    assert stuck.closed.is_set()
    assert stuck.send_attempts == 0
    assert _sent_types(fresh) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_confirm_window_counts_from_the_spares_connect_not_from_the_take(monkeypatch):
    # The spare connected well before the speech arrived and is still
    # waiting for tts.connection.done: a fresh socket's window would already
    # be over, so the speech gives up on it at once instead of granting it a
    # whole new window.
    _route_to_lanlan_app(monkeypatch)
    monkeypatch.setattr(_step_protocol, "_PRECONNECT_HANDSHAKE_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(_step_protocol, "_NEW_SOCKET_CONFIRM_TIMEOUT_SECONDS", 0.4)
    stuck = _StatefulSocket()  # connects at once, never confirms
    fresh = _socket("fresh", final_events=_app_round_end("fresh"))
    marks = {}

    def on_connect(socket):
        if socket is fresh:
            marks["fresh_connect"] = time.monotonic()

    connects = _recording_connects(monkeypatch, [_warmup_socket(), stuck, fresh], on_connect)

    _run(
        _Requests(
            _PRECONNECT,
            _wait(lambda: len(connects) == 2),
            _pause(0.6),  # connected 0.6s ago, past the 0.4s window
            lambda: marks.setdefault("speech", time.monotonic()),
            ("speech-1", _OPENING),
            (None, None),
        ),
        queue.Queue(),
    )

    waited = marks["fresh_connect"] - marks["speech"]
    assert waited < 0.25, f"granted the stuck spare a new window ({waited:.2f}s)"
    assert stuck.closed.is_set()
    assert _sent_types(fresh) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_slow_connecting_spare_is_not_cut_off_by_the_confirm_window(monkeypatch):
    # A fresh socket's confirmation window starts once it is connected, so a
    # spare still connecting (TLS to www.lanlan.app alone takes ~1s) must get
    # the same: cancelling it would only delay the speech further.
    _route_to_lanlan_app(monkeypatch)
    monkeypatch.setattr(_step_protocol, "_NEW_SOCKET_CONFIRM_TIMEOUT_SECONDS", 0.1)
    spare = _socket("spare", final_events=_app_round_end("spare"))
    # Only used if the spare is wrongly cut off (then the speech reconnects).
    fallback = _socket("fallback", final_events=_app_round_end("fallback"))
    remaining = [_warmup_socket(), spare, fallback]
    connects = []

    async def connect(*_args, **_kwargs):
        socket = remaining.pop(0)
        connects.append(socket)
        if socket is spare:
            await asyncio.sleep(0.4)  # connecting outlasts the confirm window
        return socket

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)

    _run(_Requests(_PRECONNECT, ("speech-1", _OPENING), (None, None)), queue.Queue())

    assert connects[1:] == [spare]
    assert _sent_types(spare) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_a_failed_spare_is_replaced_by_the_next_preconnect(monkeypatch):
    # Without this a dead spare would swallow every preconnect until its
    # 15s expiry, and the next reply would connect after its first text.
    _route_to_lanlan_app(monkeypatch)
    broken = _StatefulSocket(server_close=Close(1011, "internal error"))
    spare = _socket("spare", final_events=_app_round_end("spare"))
    connects = _install_sockets(monkeypatch, _warmup_socket(), broken, spare)

    _run(
        _Requests(
            _PRECONNECT,
            _wait(broken.closed.is_set),
            _PRECONNECT,
            _wait(lambda: len(connects) == 3),
            _observe(observations := {}, "replaced_before_text", lambda: len(connects) == 3),
            ("speech-1", _OPENING),
            (None, None),
        ),
        queue.Queue(),
    )

    assert observations == {"replaced_before_text": True}
    assert _sent_types(spare) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_no_spare_while_the_warmup_socket_is_still_closing(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    warmup = _SlowCloseStatefulSocket([
        {"type": "tts.connection.done", "data": {"session_id": "warmup"}},
        {"type": "tts.response.created"},
    ])
    fresh = _socket("fresh", final_events=_app_round_end("fresh"))
    connects = _install_sockets(monkeypatch, warmup, fresh)
    observations = {}
    backstop = threading.Timer(2.0, warmup.release.set)
    backstop.start()
    try:
        _run(
            _Requests(
                _PRECONNECT,
                _pause(0.1),
                _observe(observations, "connects", lambda: len(connects), timeout=0),
                warmup.release.set,
                ("speech-1", _OPENING),
                (None, None),
            ),
            queue.Queue(),
        )
    finally:
        backstop.cancel()

    assert observations == {"connects": 1}, "a spare beside the closing warmup socket"
    assert _sent_types(fresh) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_no_spare_while_the_previous_round_socket_is_still_closing(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    first = _SlowCloseStatefulSocket(
        [{"type": "tts.connection.done", "data": {"session_id": "first"}}],
        on_send={"tts.text.done": _app_round_end("first")},
    )
    second = _socket("second", final_events=_app_round_end("second"))
    connects = _install_sockets(monkeypatch, _warmup_socket(), first, second)
    observations = {}
    backstop = threading.Timer(2.0, first.release.set)
    backstop.start()
    try:
        _run(
            _Requests(
                ("speech-1", _OPENING),
                (None, None),
                _wait(lambda: first.closing),
                _PRECONNECT,
                _pause(0.1),
                _observe(observations, "connects", lambda: len(connects), timeout=0),
                first.release.set,
                ("speech-2", _OPENING),
                (None, None),
            ),
            queue.Queue(),
        )
    finally:
        backstop.cancel()

    assert observations == {"connects": 2}, "a spare beside the closing round socket"
    assert _sent_types(second) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_cutting_off_a_spare_does_not_hold_a_queued_interrupt(monkeypatch):
    # A peer that never answers the close handshake held the cut-off (and the
    # interrupt queued behind it) for websockets' whole close_timeout.
    _route_to_lanlan_app(monkeypatch)
    monkeypatch.setattr(_step_protocol, "_NEW_SOCKET_CONFIRM_TIMEOUT_SECONDS", 0.1)
    stuck = _SlowCloseStatefulSocket([])  # connects, never confirms, slow to close
    _install_sockets(monkeypatch, _warmup_socket(), stuck)
    observations = {}
    backstop = threading.Timer(3.0, stuck.release.set)
    backstop.start()
    try:
        _run(
            _Requests(
                _PRECONNECT,
                ("speech-1", _OPENING),
                ("__interrupt__", None),
                # The worker only asks for the next request after it handled
                # the interrupt.
                _observe(
                    observations,
                    "interrupt_handled_while_spare_closing",
                    lambda: not stuck.closed.is_set(),
                    timeout=0,
                ),
                stuck.release.set,
            ),
            queue.Queue(),
        )
    finally:
        backstop.cancel()

    assert observations == {"interrupt_handled_while_spare_closing": True}
    assert stuck.closed.is_set(), "the cut-off spare is still closed in the end"


def _recording_connects(monkeypatch, sockets, on_connect):
    remaining = list(sockets)
    connects = []

    async def connect(*_args, **_kwargs):
        socket = remaining.pop(0)
        on_connect(socket)
        connects.append(socket)
        return socket

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)
    return connects


def test_speech_connects_only_after_a_cut_off_spare_finished_closing(monkeypatch):
    # The background close must not let a second connection overlap it: the
    # free server limits connections per IP.
    _route_to_lanlan_app(monkeypatch)
    monkeypatch.setattr(_step_protocol, "_NEW_SOCKET_CONFIRM_TIMEOUT_SECONDS", 0.1)
    stuck = _SlowCloseStatefulSocket([])  # connects, never confirms, slow to close
    fresh = _socket("fresh", final_events=_app_round_end("fresh"))
    spare_closed_at_fresh_connect = []

    def on_connect(socket):
        if socket is fresh:
            spare_closed_at_fresh_connect.append(stuck.closed.is_set())

    _recording_connects(monkeypatch, [_warmup_socket(), stuck, fresh], on_connect)
    release = threading.Timer(0.3, stuck.release.set)
    release.start()
    try:
        _run(_Requests(_PRECONNECT, ("speech-1", _OPENING), (None, None)), queue.Queue())
    finally:
        release.cancel()

    assert spare_closed_at_fresh_connect == [True]
    assert _sent_types(fresh) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_interrupt_during_the_wait_for_a_spare_close_drops_the_connect(monkeypatch):
    # The speech waits for a cut-off spare to finish closing before it
    # connects; an interrupt arriving in that wait must win, not wait out the
    # close and then let the interrupted speech connect anyway.
    _route_to_lanlan_app(monkeypatch)
    monkeypatch.setattr(_step_protocol, "_NEW_SOCKET_CONFIRM_TIMEOUT_SECONDS", 0.1)
    stuck = _SlowCloseStatefulSocket([])  # connects, never confirms, slow to close
    fresh = _socket("fresh", final_events=_app_round_end("fresh"))
    connects = _install_sockets(monkeypatch, _warmup_socket(), stuck, fresh)
    requests = queue.Queue()  # live queue: the interrupt lands while the worker waits
    for item in (_PRECONNECT, ("speech-1", _OPENING), (None, None)):
        requests.put(item)

    def interrupt_once_waiting():
        _wait(lambda: stuck.closing)()
        time.sleep(0.2)  # the finish is now waiting for the close
        requests.put(("__interrupt__", None))
        time.sleep(0.3)
        stuck.release.set()
        requests.put((_step_protocol.TTS_SHUTDOWN_SENTINEL, None))

    driver = threading.Thread(target=interrupt_once_waiting, daemon=True)
    driver.start()
    backstop = threading.Timer(5.0, stuck.release.set)
    backstop.start()
    try:
        _run(requests, queue.Queue())
    finally:
        backstop.cancel()
        driver.join(timeout=5)

    assert fresh not in connects, "the interrupted speech still connected"
    assert stuck.closed.is_set()


def test_cutting_off_a_spare_never_abandons_its_close_half_way(monkeypatch):
    # The spare gave up on its own handshake and is closing when the speech
    # arrives. Cutting it off then must not cancel that close: the socket
    # would stay CLOSING while the speech's own connect went ahead beside it.
    _route_to_lanlan_app(monkeypatch)
    monkeypatch.setattr(_step_protocol, "_PRECONNECT_HANDSHAKE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(_step_protocol, "_NEW_SOCKET_CONFIRM_TIMEOUT_SECONDS", 0.1)
    stuck = _SlowCloseStatefulSocket([])  # connects, never confirms, slow to close
    fresh = _socket("fresh", final_events=_app_round_end("fresh"))
    spare_closed_at_fresh_connect = []

    def on_connect(socket):
        if socket is fresh:
            spare_closed_at_fresh_connect.append(stuck.closed.is_set())

    _recording_connects(monkeypatch, [_warmup_socket(), stuck, fresh], on_connect)
    backstop = threading.Timer(3.0, stuck.release.set)
    backstop.start()
    try:
        _run(
            _Requests(
                _PRECONNECT,
                _wait(lambda: stuck.closing),  # its handshake gave up; closing now
                ("speech-1", _OPENING),
                (None, None),
                _pause(0.2),
                stuck.release.set,
            ),
            queue.Queue(),
        )
    finally:
        backstop.cancel()

    assert spare_closed_at_fresh_connect == [True]
    assert _sent_types(fresh) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_a_spare_already_closed_by_the_server_is_replaced_at_once(monkeypatch):
    # Closing a CLOSED socket waits for nothing, so it must not count as a
    # close in progress that holds back the replacement.
    _route_to_lanlan_app(monkeypatch)
    dropped = _socket("dropped")
    spare = _socket("spare", final_events=_app_round_end("spare"))
    connects = _install_sockets(monkeypatch, _warmup_socket(), dropped, spare)
    observations = {}

    _run(
        _Requests(
            _PRECONNECT,
            _wait(lambda: len(connects) == 2),
            _pause(0.05),
            dropped.closed.set,  # the server closed it while it idled
            _PRECONNECT,
            _observe(observations, "replaced", lambda: len(connects) == 3, timeout=0.5),
            ("speech-1", _OPENING),
            (None, None),
        ),
        queue.Queue(),
    )

    assert observations == {"replaced": True}
    assert dropped.send_attempts == 0
    assert _sent_types(spare) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_no_new_spare_while_a_retired_one_is_still_closing(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    monkeypatch.setattr(_step_protocol, "_PRECONNECT_IDLE_SECONDS", 0.05)
    expired = _SlowCloseStatefulSocket(
        [{"type": "tts.connection.done", "data": {"session_id": "expired"}}],
    )
    fresh = _socket("fresh", final_events=_app_round_end("fresh"))
    connects = _install_sockets(monkeypatch, _warmup_socket(), expired, fresh)
    observations = {}
    backstop = threading.Timer(2.0, expired.release.set)
    backstop.start()
    try:
        _run(
            _Requests(
                _PRECONNECT,
                _wait(lambda: expired.closing),  # expired, closing in the background
                _PRECONNECT,
                _pause(0.1),
                _observe(observations, "connects", lambda: len(connects), timeout=0),
                expired.release.set,
                ("speech-1", _OPENING),
                (None, None),
            ),
            queue.Queue(),
        )
    finally:
        backstop.cancel()

    assert observations == {"connects": 2}, "a spare beside the closing expired one"
    assert _sent_types(fresh) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_a_new_reply_restarts_the_kept_spares_idle_window(monkeypatch):
    # The second reply's preconnect keeps the existing spare; its idle window
    # must count from that reply, or it expires right under it.
    _route_to_lanlan_app(monkeypatch)
    monkeypatch.setattr(_step_protocol, "_PRECONNECT_IDLE_SECONDS", 0.3)
    spare = _socket("spare", final_events=_app_round_end("spare"))
    fallback = _socket("fallback", final_events=_app_round_end("fallback"))
    connects = _install_sockets(monkeypatch, _warmup_socket(), spare, fallback)

    _run(
        _Requests(
            _PRECONNECT,
            _pause(0.2),
            _PRECONNECT,  # the next reply starts; the spare is still unused
            _pause(0.2),  # past the first window, inside the restarted one
            ("speech-1", _OPENING),
            (None, None),
        ),
        queue.Queue(),
    )

    assert connects[1:] == [spare], "the spare expired under the reply"
    assert _sent_types(spare) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_spare_socket_uses_a_short_close_timeout(monkeypatch):
    # Every later connect waits for a retired spare's close, so a peer that
    # ignores the close handshake must not cost websockets' default 10s.
    _route_to_lanlan_app(monkeypatch)
    spare = _socket("spare", final_events=_app_round_end("spare"))
    remaining = [_warmup_socket(), spare]
    connect_kwargs = {}

    async def connect(*_args, **kwargs):
        socket = remaining.pop(0)
        connect_kwargs[id(socket)] = kwargs
        return socket

    monkeypatch.setattr(_step_protocol.websockets, "connect", connect)

    _run(_Requests(_PRECONNECT, ("speech-1", _OPENING), (None, None)), queue.Queue())

    assert connect_kwargs[id(spare)].get("close_timeout") == 2.0


def test_repeated_preconnects_open_one_spare(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    spare = _socket("spare", final_events=_app_round_end("spare"))
    connects = _install_sockets(monkeypatch, _warmup_socket(), spare)

    _run(
        _Requests(_PRECONNECT, _PRECONNECT, ("speech-1", _OPENING), (None, None)),
        queue.Queue(),
    )

    assert connects[1:] == [spare]
    assert _sent_types(spare) == ["tts.create", "tts.text.delta", "tts.text.done"]


def test_shutdown_closes_an_unused_spare(monkeypatch):
    _route_to_lanlan_app(monkeypatch)
    spare = _socket("spare")
    connects = _install_sockets(monkeypatch, _warmup_socket(), spare)

    _run(_Requests(_PRECONNECT, _wait(lambda: len(connects) == 2), _pause(0.05)), queue.Queue())

    assert spare.closed.is_set()
    assert spare.sent == []


def test_preconnect_is_a_control_id_not_a_speech():
    assert TTS_PRECONNECT_SENTINEL in _step_protocol._NON_SPEECH_REQUEST_IDS


# ───────────────────────── core: who gets the request ─────────────────────────


def test_only_workers_with_a_preconnect_branch_declare_it():
    from main_logic import tts_client

    supporting = {
        name for name in dir(tts_client)
        if name.endswith("_tts_worker")
        and getattr(getattr(tts_client, name), "supports_preconnect", False)
    }
    # A worker added here needs a TTS_PRECONNECT_SENTINEL branch first.
    assert supporting == {
        "free_realtime_tts_worker",
        "step_realtime_tts_worker",
        "qwen_realtime_tts_worker",
        "grok_streaming_tts_worker",
        "elevenlabs_tts_worker",
    }


def _make_mgr() -> LLMSessionManager:
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.tts_request_queue = Queue()
    mgr.tts_thread = MagicMock()
    mgr.tts_thread.is_alive.return_value = True
    mgr.tts_ready = True
    mgr._tts_preconnect_supported = True
    mgr._tts_quota_blocked = False
    mgr.use_tts = True
    return mgr


def _queued(mgr):
    items = []
    while not mgr.tts_request_queue.empty():
        items.append(mgr.tts_request_queue.get_nowait())
    return items


def test_reply_start_queues_one_preconnect():
    mgr = _make_mgr()
    assert LLMSessionManager.request_tts_preconnect(mgr) is True
    assert _queued(mgr) == [_PRECONNECT]


@pytest.mark.parametrize("field, value", [
    ("_tts_preconnect_supported", False),  # the worker would read it as a speech id
    ("_tts_quota_blocked", True),          # every connect is refused until the window passes
    ("tts_ready", False),                  # no worker is listening yet
    ("use_tts", False),                    # replies are not spoken through this worker
])
def test_preconnect_is_not_sent_where_it_cannot_help(field, value):
    mgr = _make_mgr()
    setattr(mgr, field, value)
    assert LLMSessionManager.request_tts_preconnect(mgr) is False
    assert _queued(mgr) == []


def test_preconnect_is_not_sent_to_a_dead_worker():
    mgr = _make_mgr()
    mgr.tts_thread.is_alive.return_value = False
    assert LLMSessionManager.request_tts_preconnect(mgr) is False
    assert _queued(mgr) == []
