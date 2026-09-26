import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from main_logic.voice_turn.contracts import AsrFailureEvent, AsrStatusEvent
import main_logic.core as core_module

from tests.support.core_asr_harness import (
    _GateAsyncLock,
    _install_ready_lifecycle,
    _install_replacement_runtime_generation,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def test_abort_bumps_generation_before_waiting_for_registry_cancel() -> None:
    runtime = _Runtime()
    order: list[str] = []
    runtime._asr_runtime.abort = AsyncMock(
        side_effect=lambda _reason: order.append("abort")
    )
    runtime._invalidate_voice_pcm_sync = MagicMock(
        side_effect=lambda _reason: order.append("invalidate")
    )
    runtime._voice_input_registry.wait_idle = AsyncMock(
        side_effect=lambda: order.append("wait_idle")
    )

    await runtime._abort_independent_asr("ingress_backpressure")

    assert order == ["abort", "invalidate", "wait_idle"]


async def test_suspend_advances_runtime_barrier_before_waiting_for_registry_cancel() -> (
    None
):
    runtime = _Runtime()
    order: list[str] = []
    runtime._invalidate_voice_pcm_sync = MagicMock(
        side_effect=lambda _reason: order.append("invalidate")
    )
    runtime._asr_runtime.suspend = AsyncMock(
        side_effect=lambda _reason: order.append("suspend")
    )
    runtime._voice_input_registry.wait_idle = AsyncMock(
        side_effect=lambda: order.append("wait_idle")
    )

    await runtime._suspend_independent_asr("game_takeover")

    assert order == ["suspend", "invalidate", "wait_idle"]


async def test_registry_cancellation_abandons_the_prepared_session_after_swap() -> (
    None
):
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    original_session = runtime.session
    original_session.abandon_external_voice_turn = MagicMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    assert await runtime._prepare_voice_input_turn(token) is True

    replacement = type("Omni", (), {})()
    replacement.abandon_external_voice_turn = MagicMock()
    runtime.session = replacement
    assert runtime._voice_input_registry.invalidate_utterance(
        token,
        reason="session_hot_swap",
    )
    await runtime._voice_input_registry.wait_idle()

    original_session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}",
    )
    replacement.abandon_external_voice_turn.assert_not_called()


async def test_runtime_close_preserves_manager_lifetime_registry_builtins() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    registry = runtime._voice_input_registry
    core_registration = runtime._core_chat_voice_input_registration
    game_registration = runtime._game_voice_input_registration
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    assert await runtime._prepare_voice_input_turn(token) is True
    runtime._asr_runtime.close = AsyncMock()

    await runtime._close_independent_asr(next_route_mode="blocked")
    runtime._ensure_asr_runtime_state()
    runtime._ensure_asr_runtime_state()

    assert runtime._voice_input_registry is registry
    assert runtime._core_chat_voice_input_registration is core_registration
    assert runtime._game_voice_input_registration is game_registration
    assert core_registration.closed is False
    assert game_registration.closed is False
    assert len(registry._records) == 2


async def test_close_invalidates_late_final_before_waiting_for_provider() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    old_epoch = runtime._asr_session_epoch

    await runtime._close_independent_asr(next_route_mode="blocked")
    await runtime._handle_independent_asr_final("late", old_epoch, "glm")

    asr.close.assert_awaited_once_with()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._asr_route_mode == "blocked"


async def test_close_releases_independent_audio_pipeline() -> None:
    runtime = _Runtime()
    pipeline = type("Pipeline", (), {})()
    pipeline.close = AsyncMock()
    runtime._voice_input_audio_pipeline = pipeline

    await runtime._close_independent_asr(next_route_mode="blocked")

    pipeline.close.assert_awaited_once_with()
    assert runtime._voice_input_audio_pipeline is not pipeline


async def test_cancelled_core_close_keeps_detached_cleanup_owned() -> None:
    runtime = _Runtime()
    pipeline_close_started = asyncio.Event()
    release_pipeline_close = asyncio.Event()
    registry_wait_started = asyncio.Event()
    release_registry_wait = asyncio.Event()

    async def block_pipeline_close() -> None:
        pipeline_close_started.set()
        await release_pipeline_close.wait()

    async def block_registry_wait() -> None:
        registry_wait_started.set()
        await release_registry_wait.wait()

    pipeline = SimpleNamespace(close=AsyncMock(side_effect=block_pipeline_close))
    runtime._voice_input_audio_pipeline = pipeline
    runtime._voice_input_registry.wait_idle = AsyncMock(
        side_effect=block_registry_wait
    )
    runtime._asr_runtime.close = AsyncMock()

    closing = asyncio.create_task(
        runtime._close_independent_asr(next_route_mode="blocked")
    )
    await asyncio.wait_for(pipeline_close_started.wait(), 1)
    await asyncio.wait_for(registry_wait_started.wait(), 1)
    replacement = runtime._voice_input_audio_pipeline
    cleanup_tasks = set(runtime._core_asr_cleanup_tasks)

    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing

    assert replacement is not pipeline
    assert all(task.cancelled() is False for task in cleanup_tasks)
    release_pipeline_close.set()
    release_registry_wait.set()
    await asyncio.wait_for(asyncio.gather(*cleanup_tasks), 1)

    pipeline.close.assert_awaited_once_with()
    runtime._asr_runtime.close.assert_awaited_once_with()


async def test_cancelled_core_close_waiting_for_pipeline_lock_stays_owned() -> None:
    runtime = _Runtime()
    gate = _GateAsyncLock()
    runtime._voice_input_pipeline_transition_lock = gate
    old_pipeline = SimpleNamespace(close=AsyncMock())
    runtime._voice_input_audio_pipeline = old_pipeline
    runtime._independent_asr_provider = "old-provider"
    runtime._independent_asr_route_key = "old-core"
    runtime._voice_input_registry.wait_idle = AsyncMock()
    runtime._asr_runtime.close = AsyncMock()

    closing = asyncio.create_task(
        runtime._close_independent_asr(next_route_mode="blocked")
    )
    await asyncio.wait_for(gate.requested.wait(), 1)
    close_cleanup = next(
        task
        for task in runtime._core_asr_cleanup_tasks
        if task.get_name() == "core-independent-asr-close"
    )

    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing

    assert close_cleanup.cancelled() is False
    assert runtime._voice_input_audio_pipeline is old_pipeline
    gate.release.set()
    await asyncio.wait_for(asyncio.shield(close_cleanup), 1)

    assert runtime._voice_input_audio_pipeline is not old_pipeline
    assert runtime._independent_asr_provider is None
    assert runtime._independent_asr_route_key is None
    old_pipeline.close.assert_awaited_once_with()
    runtime._voice_input_registry.wait_idle.assert_awaited_once_with()
    runtime._asr_runtime.close.assert_awaited_once_with()


async def test_core_close_detaches_shared_state_before_registry_wait() -> None:
    runtime = _Runtime()
    registry_wait_started = asyncio.Event()
    release_registry_wait = asyncio.Event()

    async def block_registry_wait() -> None:
        registry_wait_started.set()
        await release_registry_wait.wait()

    old_pipeline = SimpleNamespace(close=AsyncMock())
    runtime._voice_input_audio_pipeline = old_pipeline
    runtime._independent_asr_provider = "old-provider"
    runtime._independent_asr_route_key = "old-core"
    runtime._voice_input_registry.wait_idle = AsyncMock(
        side_effect=block_registry_wait
    )
    runtime._asr_runtime.close = AsyncMock()

    closing = asyncio.create_task(
        runtime._close_independent_asr(next_route_mode="blocked")
    )
    await asyncio.wait_for(registry_wait_started.wait(), 1)

    detached_replacement = runtime._voice_input_audio_pipeline
    assert detached_replacement is not old_pipeline
    assert runtime._independent_asr_provider is None
    assert runtime._independent_asr_route_key is None

    runtime._begin_asr_route_operation()
    runtime._independent_asr_provider = "new-provider"
    runtime._independent_asr_route_key = "new-core"
    runtime._set_microphone_route("independent")
    release_registry_wait.set()
    await asyncio.wait_for(closing, 1)

    assert runtime._voice_input_audio_pipeline is detached_replacement
    assert runtime._independent_asr_provider == "new-provider"
    assert runtime._independent_asr_route_key == "new-core"
    assert runtime._asr_route_mode == "independent"
    runtime._asr_runtime.close.assert_not_awaited()


async def test_cancelled_successor_close_owns_runtime_cleanup_after_old_close() -> None:
    runtime = _Runtime()
    first_wait_started = asyncio.Event()
    second_wait_started = asyncio.Event()
    release_registry_wait = asyncio.Event()
    wait_calls = 0

    async def block_registry_wait() -> None:
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            first_wait_started.set()
        elif wait_calls == 2:
            second_wait_started.set()
        await release_registry_wait.wait()

    runtime._voice_input_registry.wait_idle = AsyncMock(
        side_effect=block_registry_wait
    )
    runtime._asr_runtime.close = AsyncMock()

    retired_close = asyncio.create_task(
        runtime._close_independent_asr(next_route_mode="blocked")
    )
    await first_wait_started.wait()

    successor_close = asyncio.create_task(
        runtime._close_independent_asr(next_route_mode="blocked")
    )
    await second_wait_started.wait()
    successor_cleanup = tuple(runtime._core_asr_cleanup_tasks)
    successor_close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await successor_close

    release_registry_wait.set()
    await retired_close
    await asyncio.gather(*successor_cleanup)

    runtime._asr_runtime.close.assert_awaited_once_with()


async def test_cancelled_start_settings_swap_keeps_pipeline_cleanup_owned(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def block_pipeline_close() -> None:
        close_started.set()
        await release_close.wait()

    stale_pipeline = SimpleNamespace(
        nr_enabled=True,
        close=AsyncMock(side_effect=block_pipeline_close),
    )
    runtime._voice_input_audio_pipeline = stale_pipeline
    runtime._close_independent_asr = AsyncMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": False,
                "noiseReductionEnabled": False,
            }
        ),
    )

    starting = asyncio.create_task(
        runtime._start_independent_asr_if_enabled("audio")
    )
    await asyncio.wait_for(close_started.wait(), 1)
    replacement = runtime._voice_input_audio_pipeline
    cleanup = next(
        task
        for task in runtime._core_asr_cleanup_tasks
        if task.get_name() == "core-voice-input-pipeline-close"
    )

    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting

    assert replacement is not stale_pipeline
    assert replacement.nr_enabled is False
    assert cleanup.cancelled() is False
    release_close.set()
    await asyncio.wait_for(cleanup, 1)
    stale_pipeline.close.assert_awaited_once_with()


async def test_cancelled_noise_reduction_swap_keeps_pipeline_cleanup_owned() -> None:
    runtime = _Runtime()
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def block_pipeline_close() -> None:
        close_started.set()
        await release_close.wait()

    stale_pipeline = SimpleNamespace(
        nr_enabled=True,
        close=AsyncMock(side_effect=block_pipeline_close),
    )
    runtime._voice_input_audio_pipeline = stale_pipeline

    applying = asyncio.create_task(
        runtime.apply_voice_input_noise_reduction(False)
    )
    await asyncio.wait_for(close_started.wait(), 1)
    replacement = runtime._voice_input_audio_pipeline
    cleanup = next(
        task
        for task in runtime._core_asr_cleanup_tasks
        if task.get_name() == "core-voice-input-pipeline-close"
    )

    applying.cancel()
    with pytest.raises(asyncio.CancelledError):
        await applying

    assert replacement is not stale_pipeline
    assert replacement.nr_enabled is False
    assert cleanup.cancelled() is False
    release_close.set()
    await asyncio.wait_for(cleanup, 1)
    stale_pipeline.close.assert_awaited_once_with()


async def test_startup_close_window_is_blocked_before_settings_resolution(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    class _OldAsr:
        is_ready = True

        async def close(self) -> None:
            close_started.set()
            await release_close.wait()

    runtime._asr_session = _OldAsr()
    runtime._asr_route_mode = "independent"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )

    start_task = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(close_started.wait(), 1)

    assert runtime._asr_route_mode == "blocked"
    assert (
        await runtime._route_microphone_audio(b"\x00\x00", sample_rate_hz=16_000)
        is True
    )

    release_close.set()
    await asyncio.wait_for(start_task, 1)
    assert runtime._asr_route_mode == "native"
    assert not hasattr(runtime._asr_runtime, "_asr_required")


async def test_close_unwind_cannot_clear_new_generation_owned_fields() -> None:
    runtime = _Runtime()
    old_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    old_detector = runtime._asr_detector
    assert old_detector is not None
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def close_detector() -> None:
        close_started.set()
        await release_close.wait()

    old_detector.close = AsyncMock(side_effect=close_detector)
    runtime._asr_session_factory = object()
    runtime._asr_transport_selection = object()
    old_transport = asyncio.create_task(asyncio.Event().wait())
    runtime._asr_transport_task = old_transport
    closing = asyncio.create_task(runtime._asr_runtime._close_independent_asr())
    await asyncio.wait_for(close_started.wait(), 1)

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    new_factory = object()
    new_selection = object()
    keep_transport = asyncio.Event()
    new_transport = asyncio.create_task(keep_transport.wait())
    runtime._asr_session_factory = new_factory
    runtime._asr_transport_selection = new_selection
    runtime._asr_transport_task = new_transport
    new_token = runtime._capture_ingress_token()
    runtime._asr_runtime._asr_current_ingress_token = new_token
    new_transcript_dispatcher = runtime._asr_transcript_dispatcher
    new_detector_dispatcher = runtime._asr_detector_dispatcher
    new_audio_dispatcher = runtime._asr_audio_dispatcher
    release_close.set()
    await asyncio.wait_for(closing, 1)

    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    assert runtime._asr_current_ingress_token == new_token
    assert runtime._asr_session_factory is new_factory
    assert runtime._asr_transport_selection is new_selection
    assert runtime._asr_transport_task is new_transport
    assert runtime._asr_transcript_dispatcher is new_transcript_dispatcher
    assert runtime._asr_detector_dispatcher is new_detector_dispatcher
    assert runtime._asr_audio_dispatcher is new_audio_dispatcher
    old_detector.close.assert_awaited_once_with()
    old_session.close.assert_awaited_once_with()
    new_session.close.assert_not_awaited()
    keep_transport.set()
    await new_transport


async def test_old_core_close_cannot_clear_new_pipeline_or_provider() -> None:
    runtime = _Runtime()
    runtime_close_entered = asyncio.Event()
    release_runtime_close = asyncio.Event()

    async def block_runtime_close() -> None:
        runtime_close_entered.set()
        await release_runtime_close.wait()

    runtime._asr_runtime.close = AsyncMock(side_effect=block_runtime_close)
    old_pipeline = runtime._voice_input_audio_pipeline
    old_pipeline.close = AsyncMock()
    runtime._independent_asr_provider = "old-provider"
    runtime._independent_asr_route_key = "old-core"
    runtime._set_microphone_route("independent")

    closing = asyncio.create_task(
        runtime._close_independent_asr(next_route_mode="blocked")
    )
    await asyncio.wait_for(runtime_close_entered.wait(), 1)
    new_pipeline = runtime._voice_input_audio_pipeline
    runtime._begin_asr_route_operation()
    runtime._independent_asr_provider = "new-provider"
    runtime._independent_asr_route_key = "new-core"
    runtime._set_microphone_route("independent")
    release_runtime_close.set()
    await asyncio.wait_for(closing, 1)

    old_pipeline.close.assert_awaited_once_with()
    assert runtime._voice_input_audio_pipeline is new_pipeline
    assert runtime._independent_asr_provider == "new-provider"
    assert runtime._independent_asr_route_key == "new-core"
    assert runtime._asr_route_mode == "independent"


async def test_failure_cancellation_can_publish_without_notification_deadlock() -> (
    None
):
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    current_epoch = runtime._asr_session_epoch

    async def cancellation_wait_idle() -> None:
        assert runtime._asr_notification_lock.locked() is False
        await runtime._send_core_asr_status(
            AsrStatusEvent(
                code="ASR_CANCEL_CLEANUP",
                provider="plugin-consumer",
                session_epoch=current_epoch,
            )
        )

    runtime._voice_input_registry.wait_idle = AsyncMock(
        side_effect=cancellation_wait_idle
    )

    await asyncio.wait_for(
        runtime._handle_core_asr_failure(
            AsrFailureEvent(
                code="ASR_INDEPENDENT_FAILED",
                provider="current-provider",
                session_epoch=current_epoch,
            )
        ),
        1,
    )

    assert "ASR_CANCEL_CLEANUP" in str(runtime.send_status.await_args_list)
