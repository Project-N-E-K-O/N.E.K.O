import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock
import pytest
from main_logic.core.asr_runtime import _HotSwapAudioFrame
from main_logic.asr_client.runtime import AsrStartResult, AsrStartStatus
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus, SpeechActivityEvent, VoiceIngressToken, VoiceTranscriptEvent
import main_logic.core as core_module

from tests.support.core_asr_harness import (
    _install_ready_lifecycle,
    _start_and_seal_turn,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


class _HotSwapRuntimeStub:
    def __init__(self, *, start_status: AsrStartStatus) -> None:
        self.session_epoch = 1
        self.audio_generation = 1
        self.active_provider: str | None = "provider-a"
        self.start_status = start_status
        self.submissions: list[tuple[str | None, bytes, object]] = []
        self.abort = AsyncMock()

    def capture_ingress_token(
        self,
        *,
        connection_id: str,
        lease_generation: int,
        route_generation: int,
    ):
        from main_logic.voice_turn.contracts import VoiceIngressToken

        return VoiceIngressToken(
            self.session_epoch,
            connection_id,
            lease_generation,
            route_generation,
            self.audio_generation,
        )

    async def close(self) -> None:
        self.session_epoch += 1
        self.audio_generation += 1
        self.active_provider = None

    async def start(
        self,
        *,
        route_key: str,
        resource_optimization_enabled: bool,
        user_language: str | None = None,
    ) -> AsrStartResult:
        _ = (route_key, resource_optimization_enabled, user_language)
        self.active_provider = (
            "provider-b" if self.start_status is AsrStartStatus.READY else None
        )
        return AsrStartResult(
            self.start_status,
            provider="provider-b",
            session_epoch=self.session_epoch,
        )

    async def submit(self, frame, *, ingress_token) -> AsrSubmitResult:
        self.submissions.append((self.active_provider, frame.pcm16, ingress_token))
        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)


async def test_pre_dispatch_hot_swap_reprepares_turn_on_promoted_session() -> None:
    """A same-route hot swap transfers the final off the closed old arbiter."""
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    prepared_session = runtime.session
    prepared_session.create_response.side_effect = RuntimeError("closed arbiter")

    replacement = type("Omni", (), {})()
    replacement.create_response = AsyncMock()
    replacement.submit_external_voice_turn = AsyncMock()
    replacement.prepare_external_voice_turn = AsyncMock()
    replacement.abandon_external_voice_turn = MagicMock()

    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    runtime.session = replacement
    await runtime._dispatch_core_asr_transcript(
        VoiceTranscriptEvent(turn_token=token, provider="qwen", text="prepared"),
        session_ref=prepared_session,
    )

    prepared_session.create_response.assert_not_awaited()
    replacement.prepare_external_voice_turn.assert_awaited_once_with(
        turn_id=f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    )
    replacement.submit_external_voice_turn.assert_awaited_once_with(
        "prepared",
        turn_id=f"asr-{token.ingress.session_epoch}-{token.turn_id}",
    )


async def test_final_waits_for_shared_swap_barrier_then_uses_promoted_session() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    prepared_session = runtime.session
    prepared_session.abandon_external_voice_turn = MagicMock()

    replacement = type("Omni", (), {})()
    replacement.submit_external_voice_turn = AsyncMock()
    replacement.prepare_external_voice_turn = AsyncMock()
    replacement.abandon_external_voice_turn = MagicMock()

    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    await runtime._core_voice_session_swap_lock.acquire()
    dispatch = asyncio.create_task(
        runtime._dispatch_core_asr_transcript(
            VoiceTranscriptEvent(
                turn_token=token,
                provider="qwen",
                text="after swap",
            ),
            session_ref=prepared_session,
        )
    )
    try:
        await asyncio.sleep(0)
        assert dispatch.done() is False
        runtime.session = replacement
    finally:
        runtime._core_voice_session_swap_lock.release()
    await dispatch

    replacement.prepare_external_voice_turn.assert_awaited_once_with(
        turn_id=f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    )
    replacement.submit_external_voice_turn.assert_awaited_once_with(
        "after swap",
        turn_id=f"asr-{token.ingress.session_epoch}-{token.turn_id}",
    )


async def test_hot_swap_lifecycle_guards_close_and_promote_with_voice_barrier() -> None:
    source = inspect.getsource(
        core_module.LLMSessionManager._perform_final_swap_sequence
    )

    barrier = source.index("async with core_voice_session_lock")
    close = source.index("old_main_session.close()", barrier)
    promote = source.index("self.session = new_session", close)
    barrier_exit = source.index("if not _promote_allowed", promote)
    assert barrier < close < promote < barrier_exit
    assert "asyncio.timeout_at" in source[barrier:promote]


@pytest.mark.parametrize("core_type", ["openai", "glm", "gemini"])
async def test_hot_swap_starts_independent_asr_after_core_route_change(
    core_type: str,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = core_type
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "blocked"
    runtime._independent_asr_route_key = "free"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_awaited_once_with(
        "audio",
        preserve_hot_swap_audio=True,
    )


async def test_hot_swap_does_not_retry_failed_same_core_route() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "blocked"
    runtime._independent_asr_route_key = "gemini"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_not_awaited()


async def test_failed_independent_start_preserves_external_visual_route_memory(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.session.set_visual_delivery_mode = MagicMock()
    runtime.session.block_raw_visual_delivery = MagicMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    start_mock = AsyncMock(
        return_value=AsrStartResult(
            status=AsrStartStatus.FAILED,
            failure_code="ASR_CONNECT_FAILED",
        )
    )
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "blocked"
    assert runtime._visual_route_mode == "independent"
    runtime.session.block_raw_visual_delivery.assert_called()


async def test_unreadable_independent_setting_preserves_visual_route_on_hot_swap(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.session.set_visual_delivery_mode = MagicMock()
    runtime.session.block_raw_visual_delivery = MagicMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(side_effect=OSError("preferences unavailable")),
    )
    runtime.set_independent_asr_handshake(True)

    await runtime._start_independent_asr_if_enabled("audio")
    await runtime._reconcile_independent_asr_after_core_change()

    assert runtime._asr_route_mode == "blocked"
    assert runtime._visual_route_mode == "independent"
    runtime.session.block_raw_visual_delivery.assert_called()


async def test_session_swap_during_transcript_reprepares_promoted_final() -> None:
    runtime = _Runtime()
    old_session = runtime.session
    old_session.create_response.side_effect = RuntimeError("closed arbiter")
    new_session = type(
        "Omni",
        (),
        {
            "create_response": AsyncMock(),
            "prepare_external_voice_turn": AsyncMock(),
            "submit_external_voice_turn": AsyncMock(),
            "abandon_external_voice_turn": MagicMock(),
        },
    )()

    async def swap_session(*_args, **_kwargs) -> bool:
        runtime.session = new_session
        return True

    runtime.handle_input_transcript.side_effect = swap_session
    await _start_and_seal_turn(runtime, "glm")

    await runtime._handle_independent_asr_final(
        "belongs to old role",
        runtime._asr_session_epoch,
        "glm",
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    old_session.create_response.assert_not_awaited()
    new_session.create_response.assert_not_awaited()
    new_session.prepare_external_voice_turn.assert_awaited_once()
    new_session.submit_external_voice_turn.assert_awaited_once()


@pytest.mark.parametrize("route_mode", ["independent", "native"])
async def test_same_core_session_promotion_resyncs_visual_delivery_mode(
    route_mode: str,
) -> None:
    """A promoted session inherits the live route even when provider key is unchanged."""
    runtime = _Runtime()
    runtime.core_api_type = "qwen"
    runtime.input_mode = "audio"
    runtime._asr_route_mode = route_mode
    runtime._independent_asr_route_key = "qwen"
    runtime._start_independent_asr_if_enabled = AsyncMock()
    replacement_session = type("ReplacementOmni", (), {})()
    replacement_session._supports_native_image = True
    replacement_session.set_visual_delivery_mode = MagicMock()
    replacement_session.block_raw_visual_delivery = MagicMock()
    runtime.session = replacement_session

    await runtime._reconcile_independent_asr_after_core_change()

    if route_mode == "independent":
        replacement_session.set_visual_delivery_mode.assert_not_called()
        replacement_session.block_raw_visual_delivery.assert_called_once_with()
    else:
        replacement_session.set_visual_delivery_mode.assert_called_once_with("native")
    runtime._start_independent_asr_if_enabled.assert_not_awaited()


async def test_provider_hot_swap_drops_cached_pcm_from_old_asr_generation(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    bridge = _HotSwapRuntimeStub(start_status=AsrStartStatus.READY)
    object.__setattr__(runtime, "_asr_runtime", bridge)
    runtime.core_api_type = "glm"
    runtime.input_mode = "audio"
    runtime.is_active = True
    runtime.is_hot_swap_imminent = True
    runtime.session.stream_audio = AsyncMock()
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "provider-a"
    runtime._independent_asr_route_key = "gemini"
    old_token = runtime._capture_ingress_token()
    assert runtime.hot_swap_audio_cache.append(
        _HotSwapAudioFrame(
            pcm16=b"\x01\x00" * 160,
            token=old_token,
            audio_stream_epoch=runtime._audio_stream_epoch,
        )
    )
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )

    await runtime._reconcile_independent_asr_after_core_change()

    assert len(runtime.hot_swap_audio_cache) == 1
    assert runtime._asr_route_mode == "independent"
    await runtime._flush_hot_swap_audio_cache()

    assert bridge.submissions == []
    runtime.session.stream_audio.assert_not_awaited()


async def test_failed_provider_hot_swap_blocks_and_discards_cached_pcm(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    bridge = _HotSwapRuntimeStub(start_status=AsrStartStatus.UNAVAILABLE)
    object.__setattr__(runtime, "_asr_runtime", bridge)
    runtime.core_api_type = "glm"
    runtime.input_mode = "audio"
    runtime.is_active = True
    runtime.is_hot_swap_imminent = True
    runtime.session.stream_audio = AsyncMock()
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "provider-a"
    runtime._independent_asr_route_key = "gemini"
    assert runtime.hot_swap_audio_cache.append(
        _HotSwapAudioFrame(
            pcm16=b"\x01\x00" * 160,
            token=runtime._capture_ingress_token(),
            audio_stream_epoch=runtime._audio_stream_epoch,
        )
    )
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )

    await runtime._reconcile_independent_asr_after_core_change()
    assert runtime._asr_route_mode == "blocked"
    await runtime._flush_hot_swap_audio_cache()

    assert bridge.submissions == []
    assert not runtime.hot_swap_audio_cache
    runtime.session.stream_audio.assert_not_awaited()


async def test_final_swap_barrier_timeout_drops_without_blocking_dispatcher() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    runtime._core_voice_session_swap_barrier_timeout_s = 0.01
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)

    await runtime._core_voice_session_swap_lock.acquire()
    try:
        await asyncio.wait_for(
            runtime._dispatch_core_asr_transcript(
                VoiceTranscriptEvent(
                    turn_token=token,
                    provider="qwen",
                    text="bounded",
                )
            ),
            timeout=0.5,
        )
    finally:
        runtime._core_voice_session_swap_lock.release()

    runtime.session.create_response.assert_not_awaited()


async def test_core_swap_cancels_blocked_old_final_without_touching_new_state() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    old_epoch = runtime._asr_session_epoch
    old_core_session = runtime.session
    transcript_started = asyncio.Event()
    release_transcript = asyncio.Event()

    async def block_transcript(_text: str, **_kwargs: object) -> bool:
        transcript_started.set()
        await release_transcript.wait()
        return True

    runtime.handle_input_transcript.side_effect = block_transcript
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        old_epoch,
    )
    await runtime._handle_independent_asr_endpoint(old_epoch)
    await runtime._handle_independent_asr_final("old", old_epoch, "qwen")
    await transcript_started.wait()

    await runtime._close_independent_asr(next_route_mode="blocked")
    new_core_session = type("NewCore", (), {})()
    new_core_session.create_response = AsyncMock()
    new_core_session.handle_interruption = AsyncMock()
    runtime.session = new_core_session
    _install_ready_lifecycle(runtime, "qwen")
    new_lifecycle = runtime._asr_lifecycle
    assert new_lifecycle is not None
    expected_state = new_lifecycle.snapshot.state

    release_transcript.set()
    await asyncio.sleep(0)

    old_core_session.create_response.assert_not_awaited()
    new_core_session.create_response.assert_not_awaited()
    assert runtime._asr_lifecycle is new_lifecycle
    assert new_lifecycle.snapshot.state is expected_state
    assert runtime._asr_sealed_turn_token is None
