"""Step-protocol TTS opens the next speech's socket while the LLM thinks.

Every speech used to connect only once its first text arrived, putting a full
TLS + WebSocket handshake (about a second to www.lanlan.app) in front of each
reply's first audio. Core now sends ``TTS_PRECONNECT_SENTINEL`` when a reply's
LLM request starts; these tests pin how the worker opens, hands over, expires
and drops that socket, and the core/client side that sends the request.
"""

import asyncio
import queue
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

    @property
    def state(self):
        return State.CLOSED if self.closed.is_set() else State.OPEN


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

    assert spare.sent == []
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


def test_only_step_protocol_workers_declare_preconnect():
    from main_logic import tts_client

    supporting = {
        name for name in dir(tts_client)
        if name.endswith("_tts_worker")
        and getattr(getattr(tts_client, name), "supports_preconnect", False)
    }
    # A worker added here needs a TTS_PRECONNECT_SENTINEL branch first.
    assert supporting == {"free_realtime_tts_worker", "step_realtime_tts_worker"}


def _make_mgr() -> LLMSessionManager:
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.tts_request_queue = Queue()
    mgr.tts_thread = MagicMock()
    mgr.tts_thread.is_alive.return_value = True
    mgr.tts_ready = True
    mgr._tts_preconnect_supported = True
    mgr._tts_quota_blocked = False
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
