import ast
import asyncio
import inspect
import json
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call
import pytest
from main_logic.asr_client.runtime import IndependentAsrRuntime
from main_logic.asr_client.lifecycle import VoiceLifecycleState, VoiceTurnToken, VoiceRouteMode
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus, SpeechActivityEvent, VoiceIngressToken

from tests.unit.asr_runtime._scenarios import (
    _start_runtime_with_callback_candidates,
)

from tests.support.asr_fakes import (
    _Runtime,
)

from tests.support.core_asr_harness import (
    _ReadyDetector,
    _install_active_smart_turn,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def test_draining_pending_turn_overflow_discards_candidate_and_reports_backpressure() -> (
    None
):
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "provider"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()
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

    await runtime._route_microphone_audio(
        b"\x01\x00" * (16_000 * 9),
        sample_rate_hz=16_000,
    )

    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_session is asr
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
    assert runtime._asr_sealed_turn_token is not None
    assert runtime._asr_lifecycle.pending_turn_bytes == 0
    assert runtime._asr_lifecycle.has_pending_turn is False
    runtime._asr_detector.reset.assert_not_awaited()
    runtime._asr_detector.discard_provider_successor.assert_awaited_once_with(
        runtime._asr_provider_candidate_fence
    )
    assert any(
        "ASR_INGRESS_BACKPRESSURE" in call.args[0]
        for call in runtime.send_status.await_args_list
    )
    assert runtime._omni_mic_audio_bytes == 0


async def test_active_ingress_backpressure_releases_keyed_core_turn_without_blocking(
    monkeypatch,
) -> None:
    runtime, sessions, callbacks, detector = (
        await _start_runtime_with_callback_candidates(
            monkeypatch,
            candidate_count=1,
        )
    )
    current_pause_id: str | None = None

    async def prepare_external_voice_turn(*, turn_id: str) -> None:
        nonlocal current_pause_id
        current_pause_id = turn_id

    def abandon_external_voice_turn(turn_id: str | None = None) -> None:
        nonlocal current_pause_id
        if turn_id is not None and turn_id != current_pause_id:
            return
        current_pause_id = None

    runtime.session.prepare_external_voice_turn = AsyncMock(
        side_effect=prepare_external_voice_turn
    )
    runtime.session.abandon_external_voice_turn = MagicMock(
        side_effect=abandon_external_voice_turn
    )
    core_session = runtime.session
    component = runtime._asr_runtime
    lifecycle = component._asr_lifecycle
    assert lifecycle is not None
    current_ingress = runtime._capture_ingress_token()
    component._asr_current_ingress_token = current_ingress
    on_activity = callbacks[0]["on_speech_activity"]
    assert callable(on_activity)

    await on_activity(SpeechActivityEvent.SPEECH_STARTED)

    assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    prepared_turn_id = lifecycle.snapshot.turn_id
    runtime.session.prepare_external_voice_turn.assert_awaited_once()
    external_turn_id = (
        runtime.session.prepare_external_voice_turn.await_args.kwargs["turn_id"]
    )
    assert current_pause_id == external_turn_id
    backpressure_status_started = asyncio.Event()
    release_backpressure_status = asyncio.Event()

    async def block_backpressure_status(payload: str) -> None:
        if json.loads(payload).get("code") == "ASR_INGRESS_BACKPRESSURE":
            backpressure_status_started.set()
            await release_backpressure_status.wait()

    runtime.send_status.side_effect = block_backpressure_status
    backpressure_task = asyncio.create_task(
        component._handle_audio_ingress_backpressure(current_ingress)
    )
    await asyncio.wait_for(backpressure_status_started.wait(), 1)

    next_turn = VoiceTurnToken(
        ingress=runtime._capture_ingress_token(),
        turn_id=lifecycle.snapshot.turn_id,
    )
    assert next_turn.turn_id != prepared_turn_id
    assert await runtime._prepare_core_voice_turn(next_turn) is True
    assert runtime.session.prepare_external_voice_turn.await_count == 2
    next_external_turn_id = (
        runtime.session.prepare_external_voice_turn.await_args.kwargs["turn_id"]
    )
    assert next_external_turn_id != external_turn_id
    assert current_pause_id == next_external_turn_id

    release_backpressure_status.set()
    await asyncio.wait_for(backpressure_task, 1)

    assert runtime.session is core_session
    assert runtime._asr_route_mode == "independent"
    assert component._asr_lifecycle is lifecycle
    assert lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    assert component._asr_session is None
    assert component._asr_detector is detector
    assert component._asr_current_ingress_token is None
    sessions[0].close.assert_awaited_once_with()
    detector.reset.assert_awaited_once_with()
    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        external_turn_id
    )
    assert current_pause_id == next_external_turn_id
    assert all(
        "ASR_INDEPENDENT_FAILED" not in call.args[0]
        for call in runtime.send_status.await_args_list
    )


async def test_independent_route_sends_pcm_to_asr_only() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    await _install_active_smart_turn(runtime)

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    assert consumed is True
    asr.stream_audio.assert_awaited_once_with(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    assert runtime._asr_audio_bytes == 320
    assert runtime._omni_mic_audio_bytes == 0


async def test_stale_submit_drops_only_current_frame() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.STALE)
    )

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert runtime._asr_route_mode == "independent"


async def test_unavailable_submit_blocks_core_route() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "qwen"
    runtime._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
    )
    clear_queue = MagicMock(wraps=runtime._clear_audio_stream_queue)
    clear_cache = MagicMock(wraps=runtime.hot_swap_audio_cache.clear)
    runtime._clear_audio_stream_queue = clear_queue
    runtime.hot_swap_audio_cache.clear = clear_cache

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert runtime._asr_route_mode == "blocked"
    clear_queue.assert_called_once_with("independent_asr_unavailable")
    clear_cache.assert_called_once_with()


async def test_stale_unavailable_submit_cannot_block_replacement_route() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "provider-a"
    submit_started = asyncio.Event()
    release_submit = asyncio.Event()

    async def unavailable_after_replacement(*_args, **_kwargs):
        submit_started.set()
        await release_submit.wait()
        return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)

    runtime._asr_runtime.submit = AsyncMock(side_effect=unavailable_after_replacement)
    clear_queue = MagicMock(wraps=runtime._clear_audio_stream_queue)
    clear_cache = MagicMock(wraps=runtime.hot_swap_audio_cache.clear)
    runtime._clear_audio_stream_queue = clear_queue
    runtime.hot_swap_audio_cache.clear = clear_cache
    routed = asyncio.create_task(
        runtime._route_microphone_audio(
            b"\x01\x00" * 160,
            sample_rate_hz=16_000,
        )
    )
    await asyncio.wait_for(submit_started.wait(), 1)

    new_core_session = SimpleNamespace(stream_audio=AsyncMock())
    new_asr_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime.session = new_core_session
    runtime._asr_runtime._asr_audio_generation += 1
    runtime._asr_session = new_asr_session
    runtime._independent_asr_provider = "provider-b"
    runtime._asr_runtime._asr_current_ingress_token = runtime._capture_ingress_token()
    release_submit.set()
    await asyncio.wait_for(routed, 1)

    assert runtime._asr_route_mode == "independent"
    assert runtime._independent_asr_provider == "provider-b"
    assert runtime.session is new_core_session
    assert runtime._asr_session is new_asr_session
    clear_queue.assert_not_called()
    clear_cache.assert_not_called()


async def test_fresh_blocked_route_consumes_pcm_without_omni() -> None:
    runtime = _Runtime()

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert consumed is True
    assert runtime._asr_audio_bytes == 0
    assert runtime._omni_mic_audio_bytes == 0


async def test_submit_without_lifecycle_returns_typed_unavailable() -> None:
    runtime = _Runtime()
    result = await runtime._asr_runtime.submit(
        ProcessedVoiceFrame(b"\x01\x00" * 160, 16_000, 0.0, False),
        ingress_token=VoiceIngressToken(0, "socket", 0, 0, 0),
    )

    assert result == AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
    assert not isinstance(result, bool)


async def test_submit_has_only_typed_top_level_return_paths() -> None:
    source = textwrap.dedent(inspect.getsource(IndependentAsrRuntime.submit))
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.AsyncFunctionDef)
    returns: list[ast.Return] = []

    def collect(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Return):
                returns.append(child)
            else:
                collect(child)

    for statement in function.body:
        collect(statement)

    assert returns
    assert all(
        isinstance(return_node.value, ast.Call)
        and isinstance(return_node.value.func, ast.Name)
        and return_node.value.func.id == "AsrSubmitResult"
        for return_node in returns
    )


async def test_asr_stream_failure_never_replays_the_failed_frame_to_omni() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock(side_effect=RuntimeError("sensitive provider body"))
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    await _install_active_smart_turn(runtime, "qwen")

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    assert consumed is True
    assert runtime._asr_route_mode == "blocked"
    assert runtime._asr_session is None
    assert "sensitive provider body" not in str(runtime.send_status.await_args)
