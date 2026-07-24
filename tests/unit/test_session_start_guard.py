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
        ws, False, "audio", user_initiated=True, _allow_cross_mode_restart=False
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
        ws, False, "audio", user_initiated=True, _allow_cross_mode_restart=False
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
