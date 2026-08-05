import asyncio
from queue import Queue
from unittest.mock import AsyncMock

import pytest

from main_logic.core import LLMSessionManager


def _make_inactive_manager(*, starting_count=1):
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lock = asyncio.Lock()
    mgr.input_cache_lock = asyncio.Lock()
    mgr.is_active = False
    mgr.session = None
    mgr._starting_session_count = starting_count
    mgr.session_ready = True
    mgr.pending_input_data = [{"input_type": "text", "data": "stale"}]
    mgr.tts_handler_task = None
    mgr.tts_thread = None
    mgr.tts_request_queue = Queue()
    mgr.tts_response_queue = Queue()
    mgr._audio_stream_epoch = 0
    mgr._user_session_abandon_epoch = 0
    mgr._reset_tts_retry_state = lambda: None
    mgr._clear_audio_stream_queue = lambda reason: None
    mgr._cancel_audio_stream_worker = lambda reason: None

    async def _teardown_tts_runtime(*args, **kwargs):
        return None

    mgr._teardown_tts_runtime = _teardown_tts_runtime
    return mgr


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inactive_end_session_clears_starting_guard_for_frontend_timeout():
    mgr = _make_inactive_manager(starting_count=1)

    await LLMSessionManager.end_session(mgr)

    assert mgr._starting_session_count == 0
    assert mgr.session_ready is False
    assert mgr.pending_input_data == []
    assert mgr._asr_route_mode == "blocked"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inactive_end_session_preserves_starting_guard_for_internal_cleanup():
    mgr = _make_inactive_manager(starting_count=1)

    await LLMSessionManager.end_session(mgr, reset_starting_count=False)

    assert mgr._starting_session_count == 1
    assert mgr.session_ready is True
    assert mgr.pending_input_data == [{"input_type": "text", "data": "stale"}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inactive_end_session_does_not_clear_next_start_pending_input():
    mgr = _make_inactive_manager(starting_count=1)
    teardown_started = asyncio.Event()
    finish_teardown = asyncio.Event()

    async def _teardown_tts_runtime(*args, **kwargs):
        teardown_started.set()
        await finish_teardown.wait()

    mgr._teardown_tts_runtime = _teardown_tts_runtime

    end_task = asyncio.create_task(LLMSessionManager.end_session(mgr))
    await teardown_started.wait()

    assert mgr._starting_session_count == 0
    assert mgr.pending_input_data == []

    async with mgr.input_cache_lock:
        mgr._starting_session_count = 1
        mgr.session_ready = False
        mgr.pending_input_data.append({"input_type": "text", "data": "new"})

    finish_teardown.set()
    await end_task

    assert mgr._starting_session_count == 1
    assert mgr.session_ready is False
    assert mgr.pending_input_data == [{"input_type": "text", "data": "new"}]


class _ConnectedState:
    """Stand-in for starlette WebSocketState.CONNECTED that satisfies the
    codebase pattern ``ws.client_state == ws.client_state.CONNECTED``."""
    @property
    def CONNECTED(self):
        return self


class _FakeConnectedWS:
    client_state = _ConnectedState()


def _make_starting_manager(*, starting_input_mode):
    """Manager pre-positioned at the start_session 'already starting' guard:
    an in-flight start of ``starting_input_mode`` is occupying the count.
    Only the attributes touched before the guard need to be real."""
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.user_language = "zh"
    mgr._conversation_turn_language = "zh-CN"
    mgr._set_conversation_turn_language = lambda *_a, **_k: None
    mgr.session_closed_by_server = True
    mgr.last_audio_send_error_time = 1.0
    mgr._session_start_circuit_open = False
    mgr._starting_session_count = 1
    mgr._starting_input_mode = starting_input_mode
    mgr.session = object()
    mgr.is_active = True
    mgr._audio_stream_epoch = 0
    mgr._user_session_abandon_epoch = 0
    # 跨模式重启前会校验"当前 ws 仍是本请求那把且仍连接"，并清熔断。
    mgr.websocket = _FakeConnectedWS()
    mgr.reset_session_start_circuit = lambda: None
    return mgr


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cross_mode_start_waits_then_restarts_in_requested_mode():
    """User-initiated audio start colliding with an in-flight proactive text
    session: the old code silently dropped the audio request (frontend hung
    until timeout). The new code should wait for the in-flight text to settle,
    then re-enter start_session in the requested (audio) mode rather than
    reusing the text ack."""
    mgr = _make_starting_manager(starting_input_mode="text")
    # 递归重入会走 self.start_session(...)；用实例属性 mock 截住，断言它被
    # 以请求模式再调一次，而不真正跑完整启动路径。
    restart_mock = AsyncMock()
    mgr.start_session = restart_mock

    ws = mgr.websocket  # 重启前会校验 self.websocket is websocket 且连接
    start_task = asyncio.create_task(
        LLMSessionManager.start_session(mgr, ws, False, "audio", user_initiated=True)
    )
    # 让它先进入跨模式等待循环，再放行 in-flight 落定。
    await asyncio.sleep(0.1)
    assert restart_mock.await_count == 0  # 还在等，不该提前重入
    mgr._starting_session_count = 0
    await start_task

    # 重入禁用二次跨模式重启（深度封顶 1）。
    restart_mock.assert_awaited_once_with(
        ws,
        False,
        "audio",
        user_initiated=True,
        _allow_cross_mode_restart=False,
        request_id=None,
        handshake_override=None,
        resource_optimization_override=None,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cross_mode_start_gives_up_when_inflight_never_settles(monkeypatch):
    """When the in-flight start never settles (count never drops to 0), the
    cross-mode branch gives up at the timeout and does not re-enter (avoids
    stacking a second session while the in-flight one is still stuck)."""
    monkeypatch.setattr("main_logic.core.CROSS_MODE_RESTART_WAIT_SECONDS", 0.2)
    mgr = _make_starting_manager(starting_input_mode="text")
    restart_mock = AsyncMock()
    mgr.start_session = restart_mock

    await LLMSessionManager.start_session(mgr, object(), False, "audio", user_initiated=True)

    restart_mock.assert_not_awaited()
    assert mgr._starting_session_count == 1  # in-flight guard 原样保留


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cross_mode_background_start_does_not_restart():
    """A background proactive/greeting cross-mode auto-start
    (user_initiated=False) keeps the original silent return and never
    waits+restarts — otherwise a background text start would tear down the
    user's in-flight voice session."""
    mgr = _make_starting_manager(starting_input_mode="audio")
    restart_mock = AsyncMock()
    mgr.start_session = restart_mock

    # 后台 text 撞上在飞的 audio：默认 user_initiated=False。
    await LLMSessionManager.start_session(mgr, object(), False, "text")

    restart_mock.assert_not_awaited()
    assert mgr._starting_session_count == 1


def _make_deduping_manager(*, route_mode, session_input_mode="audio"):
    """Manager pre-positioned at the SAME-mode dedupe branch: an in-flight audio
    start occupies the count, and it has already settled the route to
    ``route_mode`` for the session whose mode is ``session_input_mode``."""
    mgr = _make_starting_manager(starting_input_mode="audio")
    mgr.input_mode = session_input_mode
    mgr._asr_route_mode = route_mode
    mgr._voice_lease_connection_id = "socket-b"
    mgr.lanlan_name = "test"
    # Lazy-init only; the route fields under test are set explicitly above.
    mgr._ensure_asr_runtime_state = lambda: None
    mgr._independent_asr_handshake_override = None
    mgr._voice_input_resource_optimization_handshake_override = None
    return mgr


def _record_dedupe_calls(mgr):
    """Trace the two dedupe-path effects in call order: the route re-decision
    and the re-ack. Order is the point -- an ack sent before the re-decision
    would carry the very verdict the re-decision exists to replace."""
    calls = []

    async def _redecide(*_a, **kwargs):
        calls.append(("redecide", kwargs))

    async def _ack(input_mode, **kwargs):
        calls.append(("ack", input_mode, kwargs))

    mgr._start_independent_asr_if_enabled = _redecide
    mgr.send_session_started = _ack
    return calls


async def _run_dedupe_start(mgr, *, request_mode="audio", **kwargs):
    """Drive a same-mode start into the dedupe wait, then let the in-flight
    start settle."""
    task = asyncio.create_task(
        LLMSessionManager.start_session(
            mgr, mgr.websocket, False, request_mode, user_initiated=True, **kwargs
        )
    )
    await asyncio.sleep(0.1)
    mgr._starting_session_count = 0
    await task


@pytest.mark.unit
@pytest.mark.asyncio
async def test_same_mode_dedupe_redecides_a_blocked_route_before_reacking():
    """#2539. The dedupe path starts nothing of its own, so it used to re-ack
    with the in-flight start's verdict. Claiming the voice lease (which this
    requester did, synchronously, before start_session) invalidates the
    in-flight ASR start; that start exits ASR_START_STALE, leaves the route on
    its blocked placeholder and emits no status at all. The ack carries the
    placeholder, both windows latch fail-closed, and the microphone never opens
    for the session that did start -- nothing re-decides in-session."""
    mgr = _make_deduping_manager(route_mode="blocked")
    calls = _record_dedupe_calls(mgr)

    await _run_dedupe_start(mgr)

    assert [c[0] for c in calls] == ["redecide", "ack"]
    assert calls[0][1]["handshake_override"] is None
    assert calls[1][1] == "audio"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_same_mode_dedupe_keeps_a_settled_route():
    """A native/independent verdict is valid for whoever holds the microphone.
    Re-deciding would tear down a healthy provider mid-session, so only a
    blocked route is re-run."""
    for settled in ("native", "independent"):
        mgr = _make_deduping_manager(route_mode=settled)
        calls = _record_dedupe_calls(mgr)

        await _run_dedupe_start(mgr)

        assert [c[0] for c in calls] == ["ack"], settled


@pytest.mark.unit
@pytest.mark.asyncio
async def test_same_mode_dedupe_does_not_redecide_against_a_text_session():
    """The dedupe branch treats a missing _starting_input_mode as a match, so an
    audio request can land here against an in-flight TEXT start. A text session
    pins the route to blocked for its whole life; re-deciding would hand a live
    microphone to a session with no audio path."""
    mgr = _make_deduping_manager(route_mode="blocked", session_input_mode="text")
    mgr._starting_input_mode = None
    calls = _record_dedupe_calls(mgr)

    await _run_dedupe_start(mgr)

    assert [c[0] for c in calls] == ["ack"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_same_mode_dedupe_does_not_redecide_for_a_text_request():
    """Dual of the case above: a TEXT request can land in the same-mode branch
    against an in-flight AUDIO start (again via a missing _starting_input_mode).
    Re-deciding on its behalf would open a microphone route for a requester that
    asked for the keyboard."""
    mgr = _make_deduping_manager(route_mode="blocked")
    mgr._starting_input_mode = None
    calls = _record_dedupe_calls(mgr)

    await _run_dedupe_start(mgr, request_mode="text")

    assert [c[0] for c in calls] == ["ack"]
    assert calls[0][1] == "text"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_same_mode_dedupe_redecides_with_this_requests_handshake():
    """The re-decision belongs to THIS request: it must use the handshake
    snapshot carried down from its own start_session call, not whatever the
    shared manager field holds by the time the wait ends (a later request may
    have overwritten it -- that is why start_session snapshots it at all)."""
    mgr = _make_deduping_manager(route_mode="blocked")
    calls = _record_dedupe_calls(mgr)

    async def _overwrite_shared_field_during_wait():
        await asyncio.sleep(0.05)
        mgr._independent_asr_handshake_override = False
        mgr._voice_input_resource_optimization_handshake_override = False

    overwrite = asyncio.create_task(_overwrite_shared_field_during_wait())
    await _run_dedupe_start(
        mgr, handshake_override=True, resource_optimization_override=True
    )
    await overwrite

    assert calls[0][0] == "redecide"
    assert calls[0][1]["handshake_override"] is True
    assert calls[0][1]["resource_optimization_override"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_same_mode_dedupe_skips_reroute_when_the_lease_moved_on():
    """Codex P2. The wait is seconds long, and a THIRD audio start can claim the
    microphone inside it. Re-deciding then would configure the NEW holder's
    route from this superseded window's handshake. The new holder walks the same
    path with a snapshot that matches, so skipping loses nothing -- but the ack
    still goes out, or this requester hangs to its own timeout."""
    mgr = _make_deduping_manager(route_mode="blocked")
    calls = _record_dedupe_calls(mgr)

    async def _third_window_claims_the_microphone():
        await asyncio.sleep(0.05)
        mgr._voice_lease_connection_id = "socket-c"

    claim = asyncio.create_task(_third_window_claims_the_microphone())
    await _run_dedupe_start(mgr)
    await claim

    assert [c[0] for c in calls] == ["ack"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_same_mode_dedupe_skips_reroute_without_room_in_the_deadline(
    monkeypatch,
):
    """Codex P2. The re-decision runs a whole connect-and-retry phase on top of a
    wait that has already spent part of the frontend's 15s deadline. Starting one
    without room for it pushes the re-ack past the point where the client gives
    up -- and the client's timeout fires end_session, tearing down the session
    that did start. Out of budget, the bare re-ack (the pre-existing behaviour)
    is the better trade."""
    monkeypatch.setattr(
        "main_logic.core.asr_runtime.ASR_CONNECT_TOTAL_BUDGET_SECONDS", 100.0
    )
    mgr = _make_deduping_manager(route_mode="blocked")
    calls = _record_dedupe_calls(mgr)

    await _run_dedupe_start(mgr)

    assert [c[0] for c in calls] == ["ack"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_same_mode_dedupe_skips_both_when_inflight_never_settles(monkeypatch):
    """Timeout exit: the in-flight start never settles, so the session/is_active
    it would report may belong to the PREVIOUS session. No ack, and therefore no
    route re-decision either -- re-deciding would tear down the route of a start
    that is still running."""
    monkeypatch.setattr(
        "main_logic.core.lifecycle.FRONTEND_START_SESSION_TIMEOUT_SECONDS", 0.2
    )
    mgr = _make_deduping_manager(route_mode="blocked")
    calls = _record_dedupe_calls(mgr)

    await LLMSessionManager.start_session(
        mgr, mgr.websocket, False, "audio", user_initiated=True
    )

    assert calls == []
    assert mgr._starting_session_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cross_mode_start_skips_restart_when_torn_down_during_wait():
    """When the user actively ends the start during the wait (the frontend 15s
    timeout sends end_session, which bumps _user_session_abandon_epoch and
    zeroes the count), do NOT restart — this distinguishes a genuine settle
    from "user gave up + count was zeroed", avoiding an orphan session whose UI
    was already rejected."""
    mgr = _make_starting_manager(starting_input_mode="text")
    restart_mock = AsyncMock()
    mgr.start_session = restart_mock

    ws = object()
    start_task = asyncio.create_task(
        LLMSessionManager.start_session(mgr, ws, False, "audio", user_initiated=True)
    )
    await asyncio.sleep(0.1)
    # Simulate a frontend-initiated end_session: zero the count AND bump the
    # abandon epoch (mirrors end_session's not-by_server path).
    mgr._user_session_abandon_epoch += 1
    mgr._starting_session_count = 0
    await start_task

    restart_mock.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cross_mode_start_restarts_even_if_inflight_failed_internally():
    """If the in-flight start fails internally, its cleanup() clears
    self.websocket to None (and bumps _audio_stream_epoch) WITHOUT bumping
    _user_session_abandon_epoch — yet the browser connection (the request's own
    ws param) is still open. The user's explicit audio request should STILL
    restart; otherwise the audio promise gets no ack and hangs the full 15s
    (the very bug being fixed)."""
    mgr = _make_starting_manager(starting_input_mode="text")
    restart_mock = AsyncMock()
    mgr.start_session = restart_mock

    ws = mgr.websocket  # the request's ws stays connected throughout
    start_task = asyncio.create_task(
        LLMSessionManager.start_session(mgr, ws, False, "audio", user_initiated=True)
    )
    await asyncio.sleep(0.1)
    # In-flight text start failed → cleanup() clears self.websocket to None and
    # bumps the audio stream epoch, but NOT the user-abandon epoch; count→0.
    mgr._audio_stream_epoch += 1
    mgr.websocket = None
    mgr._starting_session_count = 0
    await start_task

    # param ws still connected + self.websocket is None ⇒ restart proceeds.
    restart_mock.assert_awaited_once_with(
        ws,
        False,
        "audio",
        user_initiated=True,
        _allow_cross_mode_restart=False,
        request_id=None,
        handshake_override=None,
        resource_optimization_override=None,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cross_mode_start_skips_restart_when_websocket_replaced_during_wait():
    """If the browser reloads/disconnects during the wait, the disconnect
    cleanup runs by_server=True (no abandon-epoch bump), but self.websocket is
    swapped/cleared. Restarting with the stale ws would create a session whose
    session_started can't be delivered — so skip the restart when the current
    ws is no longer the one this request came in on."""
    mgr = _make_starting_manager(starting_input_mode="text")
    restart_mock = AsyncMock()
    mgr.start_session = restart_mock

    # The request comes in on the CURRENT ws (must match initially, else the
    # test would pass trivially without exercising the replacement path).
    stale_ws = mgr.websocket
    start_task = asyncio.create_task(
        LLMSessionManager.start_session(mgr, stale_ws, False, "audio", user_initiated=True)
    )
    await asyncio.sleep(0.1)
    # During the wait the connection is replaced (reload): self.websocket now
    # points at a different live connection, so `self.websocket is websocket`
    # (the original stale_ws) is False → restart must be skipped.
    mgr.websocket = _FakeConnectedWS()  # new connection after reload
    mgr._starting_session_count = 0
    await start_task

    restart_mock.assert_not_awaited()
