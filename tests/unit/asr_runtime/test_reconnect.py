import asyncio
import json
import logging
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call
import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from main_logic.core import LLMSessionManager
from main_logic.asr_client.endpointing.detector_runtime import DetectorFeedResult
from main_logic.asr_client.lifecycle import VoiceLifecycleEvent, VoiceLifecycleState, VoiceRouteMode
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.contracts import SpeechActivityEvent

from tests.support.core_asr_harness import (
    _ReadyDetector,
    _TestSmartTurnLease,
    _install_ready_lifecycle,
    _install_replacement_runtime_generation,
)

from tests.support.asr_fakes import (
    _Runtime,
    _selection,
)

from tests.unit.asr_runtime._scenarios import (
    _start_runtime_with_callback_candidates,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def test_gemini_prepare_reconnect_replaces_core_receive_task() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.prepare_external_voice_turn = AsyncMock(return_value=True)
    runtime._restart_message_handler_after_session_reconnect = AsyncMock(
        return_value=True
    )

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )

    runtime._restart_message_handler_after_session_reconnect.assert_awaited_once_with(
        runtime.session
    )
    runtime.handle_new_message.assert_awaited_once_with()


async def test_reconnect_listener_replacement_cancels_retired_receive_task() -> None:
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lock = asyncio.Lock()
    manager.is_active = True
    replacement_started = asyncio.Event()

    class Session:
        async def handle_messages(self):
            replacement_started.set()
            await asyncio.Event().wait()

    session = Session()
    manager.session = session
    retired_cancelled = asyncio.Event()

    async def retired_receive_loop():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            retired_cancelled.set()
            raise

    retired_task = asyncio.create_task(retired_receive_loop())
    manager.message_handler_task = retired_task
    await asyncio.sleep(0)

    assert await manager._restart_message_handler_after_session_reconnect(session)
    await asyncio.wait_for(replacement_started.wait(), 1)

    assert retired_cancelled.is_set()
    assert retired_task.done()
    assert manager.message_handler_task is not retired_task
    manager.message_handler_task.cancel()
    await asyncio.gather(manager.message_handler_task, return_exceptions=True)


@pytest.mark.parametrize(
    "error",
    [
        AttributeError("connection already closed"),
        ConnectionClosedError(None, None),
        ConnectionClosedOK(None, None),
    ],
    ids=["attribute-error", "closed-error", "closed-ok"],
)
async def test_native_connection_close_is_latched_and_not_retried(error) -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("native")
    runtime.session_closed_by_server = False
    runtime.last_audio_send_error_time = 0.0
    runtime.audio_error_log_interval = 2.0
    runtime.session.stream_audio = AsyncMock(side_effect=error)

    assert (
        await runtime._route_microphone_audio(b"\x01\x00", sample_rate_hz=16_000)
        is True
    )
    assert (
        await runtime._route_microphone_audio(b"\x01\x00", sample_rate_hz=16_000)
        is True
    )

    assert runtime.session_closed_by_server is True
    runtime.session.stream_audio.assert_awaited_once()


async def test_shadow_native_connection_close_is_not_latched() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("native")
    runtime.session_closed_by_server = False
    runtime._voice_session_activation_factory = SimpleNamespace(enforce=False)
    runtime.session.stream_audio = AsyncMock(
        side_effect=ConnectionClosedOK(None, None)
    )

    await runtime._route_microphone_audio(b"\x01\x00", sample_rate_hz=16_000)
    await runtime._route_microphone_audio(b"\x01\x00", sample_rate_hz=16_000)

    assert runtime.session_closed_by_server is False
    assert runtime.session.stream_audio.await_count == 2


async def test_transport_only_close_enters_deep_sleep_without_closing_detector() -> (
    None
):
    runtime = _Runtime()
    asr = type("Asr", (), {"close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    detector = type("Detector", (), {"close": AsyncMock()})()
    runtime._asr_detector = detector

    await runtime._close_transport_only()

    assert runtime._asr_session is None
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DEEP_SLEEP
    assert runtime._asr_detector is detector
    assert runtime._asr_route_mode == "independent"
    asr.close.assert_awaited_once_with()
    detector.close.assert_not_awaited()


@pytest.mark.parametrize(
    "replacement",
    ["epoch", "lifecycle", "session", "transport", "state"],
)
async def test_stale_transport_expiry_never_closes_successor(
    replacement: str,
) -> None:
    runtime = _Runtime()
    original_session = type("Asr", (), {"close": AsyncMock()})()
    runtime._asr_session = original_session
    runtime._asr_route_mode = "independent"
    policy = replace(resolve_provider_policy("openai", "provider"), warm_transport_ms=0)
    original_lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        shadow_mode=False,
    )
    original_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    original_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    original_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    original_lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    original_lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    runtime._asr_lifecycle = original_lifecycle
    runtime._schedule_transport_warm_expiry(
        runtime._asr_session_epoch,
        expected_state=VoiceLifecycleState.WARM_IDLE,
    )

    successor_session = type("Asr", (), {"close": AsyncMock()})()
    if replacement == "epoch":
        runtime._asr_session_epoch += 1
    elif replacement == "lifecycle":
        successor_lifecycle = VoiceInputLifecycleController(
            provider_policy=policy,
            shadow_mode=False,
        )
        successor_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
        runtime._asr_lifecycle = successor_lifecycle
    elif replacement == "session":
        runtime._asr_session = successor_session
    elif replacement == "transport":
        original_lifecycle.invalidate_transport()
    else:
        original_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
        original_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)

    expected_current_session = (
        successor_session if replacement == "session" else original_session
    )
    assert runtime._asr_session is expected_current_session
    expiry = runtime._asr_warm_expiry_task
    assert expiry is not None
    await asyncio.wait_for(expiry, 1)

    original_session.close.assert_not_awaited()
    expected_current_session.close.assert_not_awaited()


async def test_deep_sleep_speech_reconnects_and_flushes_pending_audio() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "qwen"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.WARM_EXPIRED)
    detector = _ReadyDetector()
    detector.feed = AsyncMock(
        return_value=DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,), True)
    )
    runtime._asr_detector = detector
    new_asr = type("Asr", (), {})()
    new_asr.is_ready = True
    connect_started = asyncio.Event()
    connect_release = asyncio.Event()

    async def connect() -> None:
        connect_started.set()
        await connect_release.wait()

    new_asr.connect = AsyncMock(side_effect=connect)
    new_asr.stream_audio = AsyncMock()
    runtime._asr_session_factory = MagicMock(return_value=new_asr)
    runtime._asr_transport_selection = _selection("qwen")

    await runtime._route_microphone_audio(
        b"\x03\x00" * 160,
        sample_rate_hz=16_000,
    )
    await asyncio.wait_for(connect_started.wait(), 1)

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING
    assert runtime._asr_lifecycle.pending_connect_bytes == 320
    connect_release.set()
    assert runtime._asr_transport_task is not None
    await runtime._asr_transport_task

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    new_asr.connect.assert_awaited_once_with()
    new_asr.stream_audio.assert_awaited_once_with(
        b"\x03\x00" * 160,
        sample_rate_hz=16_000,
    )


async def test_reconnected_start_callback_survives_abort_start_generation_change(
    monkeypatch,
) -> None:
    runtime, sessions, callbacks, _detector = (
        await _start_runtime_with_callback_candidates(monkeypatch)
    )
    component = runtime._asr_runtime
    original_start_generation = component._asr_start_generation

    await component.abort("hard_mute")

    assert component._asr_start_generation > original_start_generation
    assert component._asr_session is None
    assert component._asr_lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    sessions[0].close.assert_awaited_once_with()

    await component._restart_transport(max_attempts=1)

    assert len(callbacks) == 2
    assert component._asr_session is sessions[1]
    updated_ingress = runtime._capture_ingress_token()
    component._asr_current_ingress_token = updated_ingress
    old_activity = callbacks[0]["on_speech_activity"]
    new_activity = callbacks[1]["on_speech_activity"]
    assert callable(old_activity)
    assert callable(new_activity)

    await old_activity(SpeechActivityEvent.SPEECH_STARTED)

    assert component._asr_lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN

    await new_activity(SpeechActivityEvent.SPEECH_STARTED)

    assert component._asr_current_ingress_token == updated_ingress
    assert component._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    sessions[1].close.assert_not_awaited()


async def test_restart_closes_not_ready_session_before_replacement() -> None:
    runtime = _Runtime()
    events: list[str] = []

    async def close_old() -> None:
        events.append("old.close")

    async def connect_new() -> None:
        events.append("new.connect")

    old_session = SimpleNamespace(
        is_ready=False,
        close=AsyncMock(side_effect=close_old),
    )
    candidate = SimpleNamespace(
        is_ready=True,
        connect=AsyncMock(side_effect=connect_new),
        close=AsyncMock(),
    )
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    runtime._asr_session_factory = MagicMock(return_value=candidate)
    runtime._asr_transport_selection = _selection("qwen")

    await runtime._restart_transport(max_attempts=1)

    assert events == ["old.close", "new.connect"]
    old_session.close.assert_awaited_once_with()
    candidate.connect.assert_awaited_once_with()
    candidate.close.assert_not_awaited()
    assert runtime._asr_session is candidate


async def test_not_ready_close_cannot_overwrite_replacement_generation() -> None:
    runtime = _Runtime()
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def close_old() -> None:
        close_started.set()
        await release_close.wait()

    old_session = SimpleNamespace(
        is_ready=False,
        close=AsyncMock(side_effect=close_old),
    )
    old_factory = MagicMock()
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    runtime._asr_session_factory = old_factory
    runtime._asr_transport_selection = _selection("qwen")

    restarting = asyncio.create_task(runtime._restart_transport(max_attempts=1))
    await asyncio.wait_for(close_started.wait(), 1)
    assert runtime._asr_session is None

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    new_factory = object()
    new_selection = object()
    runtime._asr_session_factory = new_factory
    runtime._asr_transport_selection = new_selection
    release_close.set()
    await asyncio.wait_for(restarting, 1)

    old_session.close.assert_awaited_once_with()
    old_factory.assert_not_called()
    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    assert runtime._asr_session_factory is new_factory
    assert runtime._asr_transport_selection is new_selection
    new_session.close.assert_not_awaited()


async def test_adopted_restart_cancellation_fails_closed_and_propagates(
    monkeypatch,
) -> None:
    runtime, sessions, _callbacks, detector = (
        await _start_runtime_with_callback_candidates(monkeypatch)
    )
    component = runtime._asr_runtime
    lifecycle = component._asr_lifecycle
    assert lifecycle is not None
    started_epoch = component._asr_session_epoch
    on_failure = AsyncMock(side_effect=component._callbacks.on_failure)
    component._callbacks = replace(component._callbacks, on_failure=on_failure)

    await component._close_transport_only()
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    component._asr_pending_speech_confirmed = True
    component._asr_current_ingress_token = runtime._capture_ingress_token()
    prepare_started = asyncio.Event()
    keep_preparing = asyncio.Event()

    async def block_prepare(_epoch: int) -> None:
        prepare_started.set()
        await keep_preparing.wait()

    component._prepare_independent_asr_turn = AsyncMock(side_effect=block_prepare)
    restarting = asyncio.create_task(component._restart_transport(max_attempts=3))
    await asyncio.wait_for(prepare_started.wait(), 1)
    assert component._asr_session is sessions[1]

    # Authoritative cancellation targets the shared owner, not one waiter.
    assert component._asr_transport_task is not None
    component._asr_transport_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await restarting
    while component._asr_close_tasks:
        await asyncio.gather(
            *tuple(component._asr_close_tasks),
            return_exceptions=True,
        )

    on_failure.assert_awaited_once()
    failure = on_failure.await_args.args[0]
    assert failure.code == "ASR_INDEPENDENT_FAILED"
    assert failure.session_epoch == started_epoch + 1
    sessions[1].close.assert_awaited_once_with()
    detector.close.assert_awaited_once_with()
    assert component._asr_session is None
    assert component._asr_lifecycle is None
    assert component._asr_detector is None
    assert component._asr_session_factory is None
    assert component._asr_transport_selection is None
    assert component._asr_session_epoch == started_epoch + 1
    assert runtime._asr_route_mode == "blocked"
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert {
        "code": "ASR_INDEPENDENT_FAILED",
        "details": {
            "provider": "qwen",
            "session_epoch": started_epoch + 1,
        },
    } in statuses


async def test_adopted_restart_exception_fails_closed_without_retry(
    monkeypatch,
) -> None:
    runtime, sessions, callbacks, detector = (
        await _start_runtime_with_callback_candidates(monkeypatch)
    )
    component = runtime._asr_runtime
    lifecycle = component._asr_lifecycle
    assert lifecycle is not None
    started_epoch = component._asr_session_epoch

    await component._close_transport_only()

    assert lifecycle.snapshot.state is VoiceLifecycleState.DEEP_SLEEP
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    assert lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING
    component._asr_pending_speech_confirmed = True
    component._asr_current_ingress_token = runtime._capture_ingress_token()
    component._prepare_independent_asr_turn = AsyncMock(
        side_effect=RuntimeError("post-adoption recovery failed")
    )

    await component._restart_transport(max_attempts=3)
    while component._asr_close_tasks:
        await asyncio.gather(
            *tuple(component._asr_close_tasks),
            return_exceptions=True,
        )

    assert len(callbacks) == 2
    sessions[0].close.assert_awaited_once_with()
    sessions[1].connect.assert_awaited_once_with()
    sessions[1].close.assert_awaited_once_with()
    component._prepare_independent_asr_turn.assert_awaited_once_with(started_epoch)
    detector.close.assert_awaited_once_with()
    assert component._asr_session is None
    assert component._asr_lifecycle is None
    assert component._asr_detector is None
    assert component._asr_session_factory is None
    assert component._asr_transport_selection is None
    assert component._asr_session_epoch == started_epoch + 1
    assert runtime._asr_route_mode == "blocked"
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert {
        "code": "ASR_INDEPENDENT_FAILED",
        "details": {
            "provider": "qwen",
            "session_epoch": started_epoch + 1,
        },
    } in statuses


async def test_same_epoch_reconnect_survives_old_abort_release() -> None:
    runtime = _Runtime()
    old_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    lifecycle = runtime._asr_lifecycle
    old_detector = runtime._asr_detector
    assert lifecycle is not None
    assert old_detector is not None
    release_started = asyncio.Event()
    release_old_lease = asyncio.Event()

    class _BlockingLease:
        async def release(self) -> None:
            release_started.set()
            await release_old_lease.wait()

    runtime._asr_smart_turn_lease = _BlockingLease()
    aborting = asyncio.create_task(runtime._asr_runtime.abort("test_abort"))
    await asyncio.wait_for(release_started.wait(), 1)

    new_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    new_detector = _ReadyDetector()
    new_lease = _TestSmartTurnLease(object())
    runtime._asr_session = new_session
    runtime._asr_detector = new_detector
    runtime._asr_smart_turn_lease = new_lease
    lifecycle.invalidate_transport()
    runtime._asr_runtime._asr_current_ingress_token = runtime._capture_ingress_token()
    release_old_lease.set()
    await asyncio.wait_for(aborting, 1)

    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is lifecycle
    assert runtime._asr_detector is new_detector
    assert runtime._asr_smart_turn_lease is new_lease
    old_session.close.assert_awaited_once_with()
    new_session.close.assert_not_awaited()
    new_detector.reset.assert_not_awaited()
    assert new_lease.released is False


async def test_transport_restart_task_failure_is_logged(caplog) -> None:
    runtime = _Runtime()
    component = runtime._asr_runtime

    async def failing_restart(_operation) -> None:
        raise RuntimeError("restart boom")

    component._run_transport_connect_operation = failing_restart
    with caplog.at_level(logging.ERROR, logger="main_logic.asr_client._infra"):
        component._ensure_transport_restart_task()
        task = component._asr_transport_task
        assert task is not None
        await asyncio.wait([task])
        await asyncio.sleep(0)

    assert "independent-asr-transport-restart" in caplog.text
    assert "restart boom" in caplog.text


@pytest.mark.unit
async def test_reconnect_listener_join_is_bounded() -> None:
    """A receive task that swallows cancellation must not wedge the swap lock."""
    from main_logic.core import LLMSessionManager

    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lanlan_name = "Test"
    manager.lock = asyncio.Lock()
    manager.is_active = True
    manager._core_voice_listener_cancel_timeout_s = 0.05
    manager.session_ready = True
    manager._close_independent_asr = AsyncMock()
    manager.send_session_ended_by_server = AsyncMock()
    session = SimpleNamespace(handle_messages=AsyncMock(), close=AsyncMock())
    manager.session = session

    stuck_release = asyncio.Event()

    async def stuck_listener() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await stuck_release.wait()

    listener = asyncio.create_task(stuck_listener())
    manager.message_handler_task = listener
    await asyncio.sleep(0)

    installed = await asyncio.wait_for(
        manager._restart_message_handler_after_session_reconnect(session),
        5.0,
    )

    # fail-closed：停不下来的 listener 还绑在退休会话上，不能在它之上再装一个
    # receive 循环；调用方都把 False 当成"放弃这次重连"。
    assert installed is False
    session.handle_messages.assert_not_called()

    # 而且必须把这条会话**退休**掉：只返回 False 会留下一个看起来还活着、实际没有
    # receive 循环的 client，之后每一轮都撞上同一个卡死的 task 再超时一次，语音从此
    # 永远收不到回复。
    assert manager.session is None
    assert manager.message_handler_task is None
    assert manager.is_active is False
    assert manager.session_ready is False

    # 会话没了，麦克风也必须收掉：否则独立 ASR 继续往一个不存在的回答会话投
    # transcript，用户说什么都石沉大海。
    manager._close_independent_asr.assert_awaited_once_with(next_route_mode="blocked")
    manager.send_session_ended_by_server.assert_awaited_once_with()

    stuck_release.set()
    await asyncio.gather(listener, return_exceptions=True)
    for _ in range(50):
        if session.close.await_count:
            break
        await asyncio.sleep(0.01)
    session.close.assert_awaited_once_with()
