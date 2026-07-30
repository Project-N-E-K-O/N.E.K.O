from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import main_logic.core as core_module

from main_logic.core.asr_runtime import AsrRuntimeMixin
from main_logic.asr_client.detector_runtime import DetectorFeedResult
from main_logic.asr_client.lifecycle import VoiceLifecycleState


pytestmark = pytest.mark.asyncio


class _Runtime(AsrRuntimeMixin):
    def __init__(self) -> None:
        self._init_asr_runtime_state()
        self.lanlan_name = "HardRoute"
        self.core_api_type = "gemini"
        self.send_status = AsyncMock()

    def __getattr__(self, name: str):
        component = self.__dict__.get("_asr_runtime")
        if component is not None and hasattr(component, name):
            return getattr(component, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value) -> None:
        component = self.__dict__.get("_asr_runtime")
        if name in {
            "_asr_route_mode",
            "_microphone_route_generation",
            "_voice_input_audio_pipeline",
            "_independent_asr_provider",
            "_independent_asr_route_key",
        }:
            object.__setattr__(self, name, value)
            return
        if component is not None and (
            name.startswith("_asr_")
            or name
            in {
                "_voice_input_audio_pipeline",
                "_voice_input_resource_optimization_enabled",
            }
        ):
            setattr(component, name, value)
            return
        object.__setattr__(self, name, value)


async def test_fresh_runtime_is_fail_closed_until_independent_asr_is_ready() -> None:
    runtime = _Runtime()

    assert runtime._asr_route_mode == "blocked"
    assert await runtime._route_microphone_audio(
        b"\x00\x00",
        sample_rate_hz=16_000,
    ) is True
    assert runtime._omni_mic_audio_bytes == 0


async def test_disabled_independent_asr_preserves_omni_native_audio(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime._voice_lease_synchronized = True
    runtime._voice_lease_owner = "core"
    runtime._voice_input_suppressed = False
    runtime.session = type("Omni", (), {})()
    runtime.session.stream_audio = AsyncMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "native"
    assert not hasattr(runtime._asr_runtime, "_asr_required")
    assert await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    ) is True
    runtime.session.stream_audio.assert_awaited_once_with(b"\x01\x00" * 160)
    assert runtime._omni_mic_audio_bytes == 320
    assert "ASR_INDEPENDENT_DISABLED" in runtime.send_status.await_args.args[0]


async def test_native_pcm_is_preprocessed_by_core_without_runtime_submit() -> None:
    runtime = _Runtime()
    runtime._voice_lease_synchronized = True
    runtime._voice_lease_owner = "core"
    runtime._voice_input_suppressed = False
    runtime._set_microphone_route("native")
    runtime.is_active = True
    runtime.is_hot_swap_imminent = False
    runtime.session = type("Omni", (), {"stream_audio": AsyncMock()})()
    runtime._asr_runtime.submit = AsyncMock()
    token = runtime._capture_ingress_token()

    await runtime._process_microphone_stream_data(
        {
            "input_type": "audio",
            "sample_rate_hz": 16_000,
            "data": [1] * 160,
        },
        ingress_token=token,
    )

    runtime.session.stream_audio.assert_awaited_once_with(b"\x01\x00" * 160)
    runtime._asr_runtime.submit.assert_not_awaited()
    assert not hasattr(runtime._asr_runtime, "process_audio")
    assert not hasattr(runtime._asr_runtime, "activate_native_route")


async def test_text_session_stays_fail_closed_for_accidental_microphone_frames(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )

    await runtime._start_independent_asr_if_enabled("text")

    assert runtime._asr_route_mode == "blocked"
    assert not hasattr(runtime._asr_runtime, "_asr_route_mode")
    assert await runtime._route_microphone_audio(
        b"\x00\x00",
        sample_rate_hz=16_000,
    ) is True


async def test_ready_independent_asr_owns_an_active_lifecycle_controller(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    asr.stream_audio = AsyncMock()
    detector = type("Detector", (), {})()
    detector.feed = AsyncMock(return_value=DetectorFeedResult((), True))
    detector.reset = AsyncMock()
    detector.close = AsyncMock()
    selection = type(
        "Selection",
        (),
        {"provider_key": "gemini", "endpointing_mode": "manual"},
    )()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(runtime_module, "_resolve_asr_selection", lambda _core: selection)
    monkeypatch.setattr(runtime_module, "_create_asr_session_from_selection", MagicMock(return_value=asr))
    monkeypatch.setattr(
        runtime_module,
        "DetectorRuntime",
        MagicMock(return_value=detector),
    )

    assert await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="core",
        hard_muted=False,
        focus_suppressed=False,
    ) is True
    await runtime._start_independent_asr_if_enabled("audio")
    await runtime._route_microphone_audio(
        b"\x01\x00" * 1_600,
        sample_rate_hz=16_000,
    )

    assert runtime._asr_lifecycle is not None
    assert runtime._asr_lifecycle.shadow_mode is False
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    assert runtime._asr_lifecycle.metrics.suppressed_silence_ms == 100
    assert runtime._asr_lifecycle.metrics.shadow_suppressed_audio_ms == 0
    asr.stream_audio.assert_not_awaited()
