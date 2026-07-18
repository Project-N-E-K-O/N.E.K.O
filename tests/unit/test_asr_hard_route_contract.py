from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic import core as core_facade
from main_logic.core.asr_runtime import AsrRuntimeMixin
from main_logic.asr_client.detector_runtime import DetectorFeedResult
from main_logic.asr_client.lifecycle_contracts import VoiceLifecycleState


pytestmark = pytest.mark.asyncio


class _Runtime(AsrRuntimeMixin):
    def __init__(self) -> None:
        self._init_asr_runtime_state()
        self.lanlan_name = "HardRoute"
        self.core_api_type = "gemini"
        self.send_status = AsyncMock()


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
        core_facade,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "native"
    assert runtime._asr_required is False
    assert await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    ) is True
    runtime.session.stream_audio.assert_awaited_once_with(b"\x01\x00" * 160)
    assert runtime._omni_mic_audio_bytes == 320
    assert "ASR_INDEPENDENT_DISABLED" in runtime.send_status.await_args.args[0]


async def test_text_session_stays_fail_closed_for_accidental_microphone_frames(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(
        core_facade,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )

    await runtime._start_independent_asr_if_enabled("text")

    assert runtime._asr_route_mode == "blocked"
    assert runtime._asr_required is False
    assert await runtime._route_microphone_audio(
        b"\x00\x00",
        sample_rate_hz=16_000,
    ) is True


async def test_ready_independent_asr_owns_an_active_lifecycle_controller(
    monkeypatch,
) -> None:
    import main_logic.core.asr_runtime as runtime_module

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
        core_facade,
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
