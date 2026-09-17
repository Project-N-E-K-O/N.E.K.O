import asyncio
from types import SimpleNamespace
from unittest.mock import call
import pytest
from main_logic.voice_turn.contracts import SpeechActivityEvent

from tests.support.core_asr_harness import (
    _install_ready_lifecycle,
    _start_and_seal_turn,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


async def test_accepted_final_is_recorded_and_injected_once() -> None:
    runtime = _Runtime()
    runtime._asr_provider = "glm"
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "glm")

    await asyncio.gather(
        runtime._handle_independent_asr_final(" hello ", epoch, "glm"),
        runtime._handle_independent_asr_final(" hello ", epoch, "glm"),
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_awaited_once_with(
        "hello",
        is_voice_source=True,
        source="independent_asr",
        metadata={"provider": "glm"},
        source_game_route_identity=None,
    )
    runtime.session.create_response.assert_awaited_once_with("hello")


async def test_final_records_segmented_wire_audio_committed_at_seal() -> None:
    runtime = _Runtime()
    session = SimpleNamespace(is_ready=True, provider_wire_audio_ms=0)
    runtime._asr_session = session
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "glm")
    # Segmented sessions advance the cumulative counter only at the seal-time
    # physical-segment commit, after the dispatcher's last per-chunk sample.
    session.provider_wire_audio_ms = 480

    await runtime._handle_independent_asr_final("hello", epoch, "glm")
    await runtime._wait_asr_transcript_dispatch_idle()

    metrics = runtime._asr_lifecycle.metrics
    assert metrics.provider_wire_audio_ms == 480
    assert metrics.cloud_audio_ms == 480
    assert runtime._asr_last_provider_wire_audio_ms == 480


async def test_late_first_final_then_second_final_recovers_in_linear_order() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch
    events: list[str] = []

    runtime.session.handle_interruption.side_effect = lambda: events.append(
        "interruption"
    )
    runtime.handle_new_message.side_effect = lambda: events.append("prepare")
    runtime.handle_input_transcript.side_effect = lambda text, **_kwargs: (
        events.append(f"transcript:{text}") or True
    )
    runtime.session.create_response.side_effect = lambda text: events.append(
        f"response:{text}"
    )

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first fragment", epoch, "openai")
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("second fragment", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert events.count("interruption") == 2
    assert events.count("prepare") == 2
    assert [event for event in events if event.startswith("transcript:")] == [
        "transcript:first fragment",
        "transcript:second fragment",
    ]
    assert [event for event in events if event.startswith("response:")] == [
        "response:first fragment",
        "response:second fragment",
    ]


async def test_three_pending_finals_recover_without_request_multiplication() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    for text in ("first", "second", "third"):
        await runtime._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_STARTED,
            epoch,
        )
        await runtime._handle_independent_asr_endpoint(epoch)
        await runtime._handle_independent_asr_final(text, epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert runtime.session.handle_interruption.await_count == 3
    assert runtime.handle_new_message.await_count == 3
    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == [
        "first",
        "second",
        "third",
    ]
    assert [
        call.args[0] for call in runtime.session.create_response.await_args_list
    ] == [
        "first",
        "second",
        "third",
    ]


async def test_consumed_or_suppressed_final_does_not_create_response() -> None:
    runtime = _Runtime()
    runtime.handle_input_transcript.return_value = False
    await _start_and_seal_turn(runtime, "gemini")

    await runtime._handle_independent_asr_final(
        "echo",
        runtime._asr_session_epoch,
        "gemini",
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.session.create_response.assert_not_awaited()
