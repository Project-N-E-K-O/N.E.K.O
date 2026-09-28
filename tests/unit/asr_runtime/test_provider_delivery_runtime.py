import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, call
import pytest
from main_logic.asr_client.lifecycle import VoiceLifecycleState, VoiceRouteMode
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.contracts import SpeechActivityEvent

from tests.support.core_asr_harness import (
    _ReadyDetector,
    _install_ready_lifecycle,
    _start_and_seal_turn,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def test_provider_final_watchdog_blocks_only_independent_asr() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"is_ready": True, "close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    policy = replace(
        resolve_provider_policy("qwen", "manual"),
        provider_final_timeout_ms=10,
    )
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()

    await _start_and_seal_turn(runtime)
    # 10ms 超时跟 Windows 的 15.6ms 定时器分辨率同量级，固定 sleep 到底睡多久
    # 完全看运气；守护任务跑完才是「已开火」的权威信号，直接等它。
    watchdog = runtime._asr_final_watchdog_task
    assert watchdog is not None
    await asyncio.wait_for(watchdog, 5)

    assert runtime._asr_route_mode == "blocked"
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._omni_mic_audio_bytes == 0


async def test_provider_final_watchdog_honors_per_provider_policy_timeout() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"is_ready": True, "close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = "glm"
    runtime._asr_route_mode = "independent"
    # Segmented providers resolve a longer final timeout than the streaming
    # default; scale both down so the watchdog must track the policy value.
    policy = replace(
        resolve_provider_policy("glm", "manual"),
        # 500ms 而不是 80ms：下面「还没到点」那半只能靠时间证明，被测窗口必须
        # 远大于 Windows 的 15.6ms 定时器分辨率，否则余量不到一个 tick。
        provider_final_timeout_ms=500,
    )
    assert resolve_provider_policy("glm", "manual").provider_final_timeout_ms == 40_000
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()

    await _start_and_seal_turn(runtime, "glm")
    armed_at = time.monotonic()
    watchdog = runtime._asr_final_watchdog_task
    assert watchdog is not None

    # 单个 sleep 睡了多久在 Windows 上不可信（15.6ms 分辨率，既会提前弹出也会
    # 超发），所以只信真实时钟：轮询到确实过了 150ms —— 远超一个 tick，也远小于
    # 上面的 500ms 窗口。醒来后必须先复查挂钟再断言：这一觉可能被别的任务拖长而
    # 睡过了观察窗口（甚至睡过 500ms 守护窗口），那时守护任务改成 blocked 是合法的，
    # 先断言就会把「事后才发生」误报成「窗口内提前开火」。
    # A watchdog stuck on the shared default (10 ms in the scaled test above)
    # would have fired by now; the per-provider override keeps it armed.
    deadline = armed_at + 0.15
    while True:
        await asyncio.sleep(0.005)
        if time.monotonic() >= deadline:
            break
        assert runtime._asr_route_mode == "independent"

    # 正向那半不猜时间：守护任务自己跑完（内部 await 完错误处理才结束）即同步点。
    await asyncio.wait_for(watchdog, 5)
    elapsed = time.monotonic() - armed_at

    assert runtime._asr_route_mode == "blocked"
    # 上界也必须钉住，否则这条用例只主张「五秒内会开火」：把 per-provider 超时写成
    # 常量 2s、或者把 ms 当成 s 换算错的回归，在 150ms 观察窗口里同样还是
    # independent，然后在 5s 内跑完，照样通过。配置是 500ms，给 3 倍余量。
    assert elapsed < 1.5, (
        f"守护任务没有按 per-provider 的 500ms 超时开火，实际 {elapsed:.3f}s"
    )


async def test_final_does_not_double_count_sampled_streaming_wire_audio() -> None:
    runtime = _Runtime()
    session = SimpleNamespace(is_ready=True, provider_wire_audio_ms=480)
    runtime._asr_session = session
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")
    # Streaming sessions advance the counter inside stream_audio, so the
    # per-chunk dispatcher sample has already recorded the full amount.
    runtime._sync_provider_wire_metrics(session)
    assert runtime._asr_lifecycle.metrics.provider_wire_audio_ms == 480

    await runtime._handle_independent_asr_final("hello", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    metrics = runtime._asr_lifecycle.metrics
    assert metrics.provider_wire_audio_ms == 480
    assert metrics.cloud_audio_ms == 480
    assert runtime._asr_last_provider_wire_audio_ms == 480


async def test_websocket_core_submits_one_external_turn_after_local_history() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "qwen"
    runtime.session.submit_external_voice_turn = AsyncMock()
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")

    await runtime._handle_independent_asr_final(" hello ", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_awaited_once_with(
        "hello",
        is_voice_source=True,
        source="independent_asr",
        metadata={"provider": "qwen"},
        source_game_route_identity=None,
    )
    runtime.session.submit_external_voice_turn.assert_awaited_once()
    call = runtime.session.submit_external_voice_turn.await_args
    assert call.args == ("hello",)
    assert call.kwargs["turn_id"].startswith("asr-")
    runtime.session.create_response.assert_not_awaited()


async def test_failed_lease_release_does_not_skip_accepted_final_delivery() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "glm")
    component = runtime._asr_runtime
    component._asr_lifecycle.provider_policy = replace(
        component._asr_lifecycle.provider_policy,
        warm_transport_ms=60_000,
    )
    epoch = component._asr_session_epoch
    await _start_and_seal_turn(runtime, "glm")
    lease = component._asr_smart_turn_lease
    assert lease is not None

    async def raising_release() -> None:
        raise RuntimeError("release boom")

    lease.release = raising_release

    await runtime._handle_independent_asr_final("hello", epoch, "glm")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert component._asr_smart_turn_lease is None
    assert (
        component._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    )
    runtime.handle_input_transcript.assert_awaited_once_with(
        "hello",
        is_voice_source=True,
        source="independent_asr",
        metadata={"provider": "glm"},
        source_game_route_identity=None,
    )
    assert component._asr_warm_expiry_task is not None
    component._asr_warm_expiry_task.cancel()


async def test_provider_final_lock_then_overflow_preserves_accepted_final() -> None:
    runtime = _Runtime()
    asr = type(
        "Asr",
        (),
        {
            "is_ready": True,
            "stream_audio": AsyncMock(),
            "close": AsyncMock(),
        },
    )()
    runtime._asr_session = asr
    _install_ready_lifecycle(runtime, "openai")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)
    completion_started = asyncio.Event()
    completion_release = asyncio.Event()

    async def complete_provider_candidate(_fence) -> bool:
        completion_started.set()
        await completion_release.wait()
        return False

    detector.complete_provider_candidate.side_effect = complete_provider_candidate
    epoch = runtime._asr_session_epoch
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    runtime._asr_lifecycle.accept_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    ingress_token = runtime._asr_runtime._asr_current_ingress_token
    assert ingress_token is not None

    final_task = asyncio.create_task(
        runtime._handle_independent_asr_final("first", epoch, "openai")
    )
    await asyncio.wait_for(completion_started.wait(), 1)
    overflow_task = asyncio.create_task(
        runtime._handle_audio_ingress_backpressure(
            ingress_token,
            observed_state=VoiceLifecycleState.DRAINING,
        )
    )
    await asyncio.sleep(0)
    assert final_task.done() is False
    assert overflow_task.done() is False
    assert runtime._asr_final_lock.locked()
    completion_release.set()
    await asyncio.gather(final_task, overflow_task)
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_awaited_once_with(
        "first",
        is_voice_source=True,
        source="independent_asr",
        metadata={"provider": "openai"},
        source_game_route_identity=None,
    )
    assert runtime._asr_lifecycle.has_pending_turn is False
    assert runtime._asr_sealed_turn_token is None
