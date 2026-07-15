import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic import core as core_facade
from main_logic.core import LLMSessionManager
from main_logic.core.asr_runtime import AsrRuntimeMixin
from main_logic.voice_turn.contracts import SpeechActivityEvent
from utils import preferences


pytestmark = pytest.mark.asyncio


class _Runtime(AsrRuntimeMixin):
    def __init__(self) -> None:
        self._init_asr_runtime_state()
        self.lanlan_name = "Test"
        self.session = type("Omni", (), {})()
        self.session.create_response = AsyncMock()
        self.session.handle_interruption = AsyncMock()
        self.handle_new_message = AsyncMock()
        self.handle_input_transcript = AsyncMock(return_value=True)
        self.send_status = AsyncMock()


async def test_independent_route_sends_pcm_to_asr_only() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert consumed is True
    asr.stream_audio.assert_awaited_once_with(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    assert runtime._asr_audio_bytes == 320
    assert runtime._omni_mic_audio_bytes == 0


async def test_native_route_leaves_pcm_for_omni_and_counts_on_record() -> None:
    runtime = _Runtime()

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    runtime._record_omni_microphone_audio(320)

    assert consumed is False
    assert runtime._asr_audio_bytes == 0
    assert runtime._omni_mic_audio_bytes == 320


async def test_speech_started_interrupts_and_prepares_turn_once() -> None:
    runtime = _Runtime()
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )

    runtime.session.handle_interruption.assert_awaited_once_with()
    runtime.handle_new_message.assert_awaited_once_with()
    assert runtime._asr_turn_prepared is True


async def test_accepted_final_is_recorded_and_injected_once() -> None:
    runtime = _Runtime()
    runtime._asr_provider = "glm"
    epoch = runtime._asr_session_epoch

    await asyncio.gather(
        runtime._handle_independent_asr_final(" hello ", epoch, "glm"),
        runtime._handle_independent_asr_final(" hello ", epoch, "glm"),
    )

    runtime.handle_input_transcript.assert_awaited_once_with(
        "hello",
        is_voice_source=True,
        source="independent_asr",
        metadata={"provider": "glm"},
    )
    runtime.session.create_response.assert_awaited_once_with("hello")


async def test_late_first_final_then_second_final_recovers_in_linear_order() -> None:
    runtime = _Runtime()
    epoch = runtime._asr_session_epoch
    events: list[str] = []

    runtime.session.handle_interruption.side_effect = lambda: events.append(
        "interruption"
    )
    runtime.handle_new_message.side_effect = lambda: events.append("prepare")
    runtime.handle_input_transcript.side_effect = (
        lambda text, **_kwargs: events.append(f"transcript:{text}") or True
    )
    runtime.session.create_response.side_effect = lambda text: events.append(
        f"response:{text}"
    )

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_final("first fragment", epoch, "openai")
    await runtime._handle_independent_asr_final("second fragment", epoch, "openai")

    assert events == [
        "interruption",
        "prepare",
        "transcript:first fragment",
        "response:first fragment",
        "interruption",
        "prepare",
        "transcript:second fragment",
        "response:second fragment",
    ]


async def test_three_pending_finals_recover_without_request_multiplication() -> None:
    runtime = _Runtime()
    epoch = runtime._asr_session_epoch

    for _ in range(3):
        await runtime._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_STARTED,
            epoch,
        )

    for text in ("first", "second", "third"):
        await runtime._handle_independent_asr_final(text, epoch, "openai")

    assert runtime.session.handle_interruption.await_count == 3
    assert runtime.handle_new_message.await_count == 3
    assert [call.args[0] for call in runtime.handle_input_transcript.await_args_list] == [
        "first",
        "second",
        "third",
    ]
    assert [call.args[0] for call in runtime.session.create_response.await_args_list] == [
        "first",
        "second",
        "third",
    ]


async def test_consumed_or_suppressed_final_does_not_create_response() -> None:
    runtime = _Runtime()
    runtime.handle_input_transcript.return_value = False

    await runtime._handle_independent_asr_final(
        "echo",
        runtime._asr_session_epoch,
        "gemini",
    )

    runtime.session.create_response.assert_not_awaited()


async def test_close_invalidates_late_final_before_waiting_for_provider() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    old_epoch = runtime._asr_session_epoch

    await runtime._close_independent_asr()
    await runtime._handle_independent_asr_final("late", old_epoch, "glm")

    asr.close.assert_awaited_once_with()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._asr_route_mode == "native"


async def test_asr_stream_failure_never_replays_the_failed_frame_to_omni() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock(side_effect=RuntimeError("sensitive provider body"))
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert consumed is True
    assert runtime._asr_fallback_pending is True
    assert runtime._asr_route_mode == "fallback_pending"
    assert "sensitive provider body" not in str(runtime.send_status.await_args)


async def test_independent_asr_setting_is_persisted_as_a_boolean() -> None:
    assert "independentAsrEnabled" in preferences._ALLOWED_CONVERSATION_SETTINGS


async def test_start_uses_current_core_route_only_after_provider_ready(monkeypatch) -> None:
    import main_logic.core.asr_runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    factory = MagicMock(return_value=asr)
    monkeypatch.setattr(
        core_facade,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(runtime_module, "create_asr_session", factory)

    await runtime._start_independent_asr_if_enabled("audio")

    asr.connect.assert_awaited_once_with()
    assert runtime._asr_session is asr
    assert runtime._asr_provider == "gemini"
    assert runtime._asr_route_mode == "independent"
    assert factory.call_args.args == ("gemini",)


@pytest.mark.parametrize("core_type", ["qwen", "qwen_intl"])
async def test_qwen_core_keeps_native_audio_when_text_injection_is_unsupported(
    monkeypatch,
    core_type: str,
) -> None:
    import main_logic.core.asr_runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = core_type
    factory = MagicMock()
    monkeypatch.setattr(
        core_facade,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(runtime_module, "create_asr_session", factory)

    await runtime._start_independent_asr_if_enabled("audio")

    factory.assert_not_called()
    assert runtime._asr_route_mode == "native"
    assert runtime._asr_session is None
    runtime.send_status.assert_awaited_once()
    status_payload = json.loads(runtime.send_status.await_args.args[0])
    assert status_payload == {
        "code": "ASR_INDEPENDENT_UNAVAILABLE",
        "details": {"provider": core_type},
    }


async def test_start_failure_keeps_native_omni_without_leaking_error(monkeypatch) -> None:
    import main_logic.core.asr_runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "glm"
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock(side_effect=RuntimeError("secret provider response"))
    asr.close = AsyncMock()
    monkeypatch.setattr(
        core_facade,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(runtime_module, "create_asr_session", MagicMock(return_value=asr))

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "native"
    assert runtime._asr_session is None
    assert "secret provider response" not in str(runtime.send_status.await_args)


async def test_hot_swap_reuses_matching_asr_provider() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "gemini"
    runtime._asr_core_type = "gemini"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_not_awaited()


async def test_hot_swap_replaces_asr_before_cached_audio_for_new_core() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "glm"
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "gemini"
    runtime._asr_core_type = "gemini"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_awaited_once_with("audio")


@pytest.mark.parametrize("core_type", ["openai", "glm", "gemini"])
async def test_hot_swap_starts_independent_asr_after_core_route_change(
    core_type: str,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = core_type
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "native"
    runtime._asr_core_type = "free"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_awaited_once_with("audio")


@pytest.mark.parametrize("route_mode", ["native", "fallback_pending"])
async def test_hot_swap_does_not_retry_failed_same_core_route(
    route_mode: str,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    runtime._asr_route_mode = route_mode
    runtime._asr_core_type = "gemini"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_not_awaited()


async def test_session_activation_resolves_asr_before_frontend_ack() -> None:
    order: list[str] = []
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lock = asyncio.Lock()
    manager.input_cache_lock = asyncio.Lock()
    manager.is_active = False
    manager._session_turn_count = 0
    manager.session_start_failure_count = 1
    manager.session_start_last_failure_time = 1.0
    manager._memory_error_retry_after = 1.0
    manager._session_start_circuit_open = True
    manager.pending_agent_callbacks = []
    manager._activity_tracker = type("Tracker", (), {"on_voice_mode": lambda self, value: None})()
    manager.is_goodbye_silent = lambda: False
    manager._drain_pending_context_appends_before_ready = AsyncMock()
    manager._flush_pending_input_data = AsyncMock()
    manager._consume_next_session_context_messages = MagicMock()
    manager._start_independent_asr_if_enabled = AsyncMock(
        side_effect=lambda _mode: order.append("asr")
    )
    manager.send_session_started = AsyncMock(
        side_effect=lambda _mode: order.append("started")
    )

    stop = asyncio.Event()

    class _Session:
        async def handle_messages(self) -> None:
            await stop.wait()

    manager.session = _Session()

    await LLMSessionManager._start_session_activate(
        manager,
        "audio",
        0,
        time.time(),
    )

    assert order == ["asr", "started"]
    stop.set()
    await manager.message_handler_task


async def test_disabled_or_text_session_never_creates_provider(monkeypatch) -> None:
    import main_logic.core.asr_runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    factory = MagicMock()
    monkeypatch.setattr(
        core_facade,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )
    monkeypatch.setattr(runtime_module, "create_asr_session", factory)

    await runtime._start_independent_asr_if_enabled("audio")
    await runtime._start_independent_asr_if_enabled("text")

    factory.assert_not_called()
    assert runtime._asr_route_mode == "native"


async def test_free_core_reports_unavailable_and_stays_native(monkeypatch) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "free"
    monkeypatch.setattr(
        core_facade,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "native"
    assert "ASR_INDEPENDENT_UNAVAILABLE" in runtime.send_status.await_args.args[0]


async def test_provider_error_without_audio_closes_and_falls_back_immediately() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_error(epoch, "glm")
    await asyncio.sleep(0)

    assert runtime._asr_session_epoch == epoch + 1
    assert runtime._asr_route_mode == "native"
    asr.close.assert_awaited_once_with()


async def test_fallback_pending_switches_to_native_only_after_silence(monkeypatch) -> None:
    import main_logic.core.asr_runtime as runtime_module

    runtime = _Runtime()
    runtime._asr_route_mode = "fallback_pending"
    runtime._asr_fallback_pending = True
    monkeypatch.setattr(runtime_module, "_ASR_FALLBACK_SILENCE_SECONDS", 0)

    assert await runtime._route_microphone_audio(
        b"\x00\x00",
        sample_rate_hz=16_000,
    ) is True
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert runtime._asr_route_mode == "native"
    assert runtime._asr_fallback_pending is False


async def test_continuous_silent_pcm_does_not_reset_native_fallback(monkeypatch) -> None:
    import main_logic.core.asr_runtime as runtime_module

    runtime = _Runtime()
    runtime._asr_route_mode = "fallback_pending"
    runtime._asr_fallback_pending = True
    monkeypatch.setattr(runtime_module, "_ASR_FALLBACK_SILENCE_SECONDS", 0.02)

    await runtime._route_microphone_audio(b"\x00\x00", sample_rate_hz=16_000)
    fallback_task = runtime._asr_fallback_task
    for _ in range(5):
        await asyncio.sleep(0.005)
        await runtime._route_microphone_audio(b"\x00\x00", sample_rate_hz=16_000)

    assert runtime._asr_route_mode == "native"
    assert runtime._asr_fallback_task is fallback_task


async def test_injection_failure_is_reported_once_without_provider_body() -> None:
    runtime = _Runtime()
    runtime.session.create_response.side_effect = RuntimeError("sensitive response")

    await runtime._handle_independent_asr_final(
        "hello",
        runtime._asr_session_epoch,
        "gemini",
    )

    assert "ASR_INDEPENDENT_INJECTION_FAILED" in runtime.send_status.await_args.args[0]
    assert "sensitive response" not in str(runtime.send_status.await_args)
    runtime.session.create_response.assert_awaited_once_with("hello")


async def test_session_swap_during_transcript_drops_old_final_injection() -> None:
    runtime = _Runtime()
    old_session = runtime.session
    new_session = type("Omni", (), {"create_response": AsyncMock()})()

    async def swap_session(*_args, **_kwargs) -> bool:
        runtime.session = new_session
        return True

    runtime.handle_input_transcript.side_effect = swap_session

    await runtime._handle_independent_asr_final(
        "belongs to old role",
        runtime._asr_session_epoch,
        "glm",
    )

    old_session.create_response.assert_not_awaited()
    new_session.create_response.assert_not_awaited()


async def test_status_delivery_failure_never_breaks_audio_runtime() -> None:
    runtime = _Runtime()
    runtime.send_status.side_effect = RuntimeError("socket closed")

    await runtime._send_asr_status("ASR_INDEPENDENT_READY", "glm")
