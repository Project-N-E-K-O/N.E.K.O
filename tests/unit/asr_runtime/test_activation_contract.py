import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from main_logic.asr_client.runtime import AsrStartResult, AsrStartStatus
import main_logic.core as core_module
import main_logic.voice_turn.audio_input as audio_input_module
from main_logic.core.asr_runtime import AsrRuntimeMixin
from main_logic.voice_turn.activity_evidence import RnnoiseEvidence

from tests.support.asr_fakes import (
    _CoreActivationFactory,
    _Runtime,
    _selection,
)

from tests.support.core_asr_harness import (
    _ReadyDetector,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


async def test_core_forgets_future_verifier_when_physical_detach_degrades() -> None:
    runtime = _Runtime()
    runtime._speaker_shadow_factory = MagicMock()
    runtime._asr_runtime.set_speaker_verifier_factory = AsyncMock(return_value=False)

    updated = await runtime.set_speaker_verifier_factory(
        None,
        activation_generation="revoked-profile",
    )

    assert updated is False
    assert runtime._speaker_shadow_factory is None


async def test_factory_audio_contract_blocks_until_session_pipeline_matches() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.session.stream_audio = AsyncMock()
    factory = _CoreActivationFactory()
    factory.noise_reduction_enabled = False
    await runtime.set_voice_session_activation_factory(
        factory,
        activation_generation="profile",
        activation_required=True,
    )
    frame = b"\x01\x00" * 160

    assert await runtime._route_microphone_audio(frame, sample_rate_hz=16_000)
    runtime.session.stream_audio.assert_not_awaited()
    assert factory.runtimes == []

    runtime._voice_input_noise_reduction_enabled = False
    assert await runtime._route_microphone_audio(frame, sample_rate_hz=16_000)
    assert len(factory.runtimes) == 1


async def test_core_passes_only_configured_speaker_shadow_factory(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    factory = MagicMock()
    runtime._speaker_shadow_factory = factory
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    start_mock = AsyncMock(
        return_value=AsrStartResult(
            status=AsrStartStatus.FAILED,
            failure_code="ASR_START_STALE",
        )
    )
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    await runtime._start_independent_asr_if_enabled("audio")

    assert start_mock.await_args.kwargs["speaker_shadow_factory"] is factory
    factory.assert_not_called()


async def test_noise_reduction_disabled_reaches_pipeline_audio_processor(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    created: list[dict] = []

    class _RecordingProcessor:
        def __init__(self, **kwargs) -> None:
            created.append(kwargs)
            self.speech_probability = 0.0
            self.rnnoise_available = False

        def process_chunk(self, _audio_bytes: bytes) -> bytes:
            return b""

        def close(self) -> None:
            return None

    monkeypatch.setattr(audio_input_module, "AudioProcessor", _RecordingProcessor)
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

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._voice_input_noise_reduction_enabled is False
    assert runtime._voice_input_audio_pipeline.nr_enabled is False
    await runtime._voice_input_audio_pipeline.process(
        b"\x01\x00" * 480,
        sample_rate_hz=48_000,
    )
    assert created[-1]["noise_reduce_enabled"] is False

    started_pipeline = runtime._voice_input_audio_pipeline
    await runtime._close_independent_asr(next_route_mode="blocked")

    assert runtime._voice_input_audio_pipeline is not started_pipeline
    assert runtime._voice_input_audio_pipeline.nr_enabled is False
    await runtime._voice_input_audio_pipeline.process(
        b"\x01\x00" * 480,
        sample_rate_hz=48_000,
    )
    assert len(created) == 2
    assert created[-1]["noise_reduce_enabled"] is False


@pytest.mark.parametrize("factory_fails", [False, True])
async def test_speaker_shadow_factory_is_lightweight_sync_and_fail_open(
    monkeypatch,
    factory_fails: bool,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    selection = _selection("qwen", "provider")
    session = SimpleNamespace(
        is_ready=True,
        connect=AsyncMock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        lambda _core_type: selection,
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        lambda _core_type, **_kwargs: session,
    )
    detector_factory = MagicMock(return_value=_ReadyDetector())
    monkeypatch.setattr(runtime_module, "DetectorRuntime", detector_factory)
    shadow = SimpleNamespace(close=AsyncMock())
    factory_threads: list[threading.Thread] = []

    def factory():
        factory_threads.append(threading.current_thread())
        if factory_fails:
            raise RuntimeError("missing shadow backend")
        return shadow

    result = await runtime._asr_runtime.start(
        route_key="qwen",
        resource_optimization_enabled=True,
        speaker_shadow_factory=factory,
    )

    assert result.status is AsrStartStatus.READY
    assert factory_threads == [threading.main_thread()]
    assert detector_factory.call_args.kwargs["speaker_shadow"] is (
        None if factory_fails else shadow
    )


async def test_start_installs_latest_verifier_published_during_connect(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    selection = _selection("qwen", "provider")
    connect_started = asyncio.Event()
    connect_release = asyncio.Event()

    async def connect() -> None:
        connect_started.set()
        await connect_release.wait()

    session = SimpleNamespace(
        is_ready=True,
        connect=connect,
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        lambda _core_type: selection,
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        lambda _core_type, **_kwargs: session,
    )
    detector_factory = MagicMock(return_value=_ReadyDetector())
    monkeypatch.setattr(runtime_module, "DetectorRuntime", detector_factory)
    stale_shadow = SimpleNamespace(close=AsyncMock())
    current_shadow = SimpleNamespace(close=AsyncMock())
    stale_factory = MagicMock(return_value=stale_shadow)
    current_factory = MagicMock(return_value=current_shadow)

    start_task = asyncio.create_task(
        runtime._asr_runtime.start(
            route_key="qwen",
            resource_optimization_enabled=True,
            speaker_shadow_factory=stale_factory,
        )
    )
    await asyncio.wait_for(connect_started.wait(), 1.0)
    assert await runtime._asr_runtime.set_speaker_verifier_factory(
        current_factory,
        activation_generation="current-profile",
    )
    connect_release.set()
    result = await asyncio.wait_for(start_task, 1.0)

    assert result.status is AsrStartStatus.READY
    stale_factory.assert_not_called()
    current_factory.assert_called_once_with()
    assert detector_factory.call_args.kwargs["speaker_shadow"] is current_shadow


async def test_voice_activity_uses_pcm_when_rnnoise_is_disabled():
    pcm = b"\xff\x7f" * 160
    assert AsrRuntimeMixin._voice_session_activation_has_speech(
        pcm, speech_probability=0.0, rnnoise_available=False
    )
    assert AsrRuntimeMixin._voice_session_activation_has_speech(
        pcm,
        speech_probability=0.0,
        rnnoise_available=True,
        rnnoise_evidence=RnnoiseEvidence(True, 0, None, None, None, None),
    )
