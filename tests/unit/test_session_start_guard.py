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
    return mgr


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cross_mode_start_waits_then_restarts_in_requested_mode():
    """用户点语音(audio)时撞上 proactive 自起的 text 会话(在飞)：旧实现静默
    丢弃 audio 请求 → 前端干等超时。新实现应等 in-flight text 落定后递归
    重入起一个 audio 会话（而非复用 text 的 ack）。"""
    mgr = _make_starting_manager(starting_input_mode="text")
    # 递归重入会走 self.start_session(...)；用实例属性 mock 截住，断言它被
    # 以请求模式再调一次，而不真正跑完整启动路径。
    restart_mock = AsyncMock()
    mgr.start_session = restart_mock

    ws = object()
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
    """in-flight 启动一直不落定（count 不归 0）时，跨模式分支等到上限后放弃，
    不递归重入（避免在 in-flight 仍卡着时叠起第二个会话）。"""
    monkeypatch.setattr("main_logic.core.FRONTEND_START_SESSION_TIMEOUT_SECONDS", 0.2)
    mgr = _make_starting_manager(starting_input_mode="text")
    restart_mock = AsyncMock()
    mgr.start_session = restart_mock

    await LLMSessionManager.start_session(mgr, object(), False, "audio", user_initiated=True)

    restart_mock.assert_not_awaited()
    assert mgr._starting_session_count == 1  # in-flight guard 原样保留


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cross_mode_background_start_does_not_restart():
    """后台 proactive/greeting 的跨模式 auto-start（user_initiated=False）撞车时
    维持原静默 return，绝不等待+重启——否则后台 text 启动会反过来顶掉用户
    正在飞的语音会话。"""
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
    """等待期间发生 end_session（前端 15s 超时会发，_audio_stream_epoch 递增、
    且把 count 清 0）时，不重启——区分真落定与"用户已放弃 + 被清零"，避免起
    一个 UI 已 reject 的孤儿会话。"""
    mgr = _make_starting_manager(starting_input_mode="text")
    restart_mock = AsyncMock()
    mgr.start_session = restart_mock

    ws = object()
    start_task = asyncio.create_task(
        LLMSessionManager.start_session(mgr, ws, False, "audio", user_initiated=True)
    )
    await asyncio.sleep(0.1)
    # 模拟 end_session：清零 count 的同时 bump epoch（与真实 end_session 一致）。
    mgr._audio_stream_epoch += 1
    mgr._starting_session_count = 0
    await start_task

    restart_mock.assert_not_awaited()
