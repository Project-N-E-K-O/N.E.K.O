import inspect
from unittest.mock import ANY, AsyncMock, MagicMock
import pytest
from main_logic.voice_turn.activity_evidence import RnnoiseEvidence
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from main_logic.voice_turn.contracts import VoiceTranscriptEvent

from tests.support.core_asr_harness import (
    _install_ready_lifecycle,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.unit_fast]


async def test_hot_swap_cache_replay_preserves_rnnoise_evidence() -> None:
    runtime = _Runtime()
    runtime.is_active = True
    runtime.is_hot_swap_imminent = True
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "qwen"
    evidence = RnnoiseEvidence(True, 3, 0.9, 0.6, 0.2, 0.55)
    processed = ProcessedVoiceFrame(
        pcm16=b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        speech_probability=evidence.peak,
        rnnoise_available=True,
        rnnoise_evidence=evidence,
    )
    runtime._voice_input_audio_pipeline.process = AsyncMock(return_value=processed)
    route_audio = AsyncMock(return_value=True)
    runtime._route_microphone_audio = route_audio
    token = runtime._capture_ingress_token()

    await runtime._process_microphone_stream_data(
        {
            "input_type": "audio",
            "sample_rate_hz": 16_000,
            "data": [1] * 160,
        },
        ingress_token=token,
        captured_at=2345.6,
    )

    assert len(runtime.hot_swap_audio_cache) == 1
    route_audio.assert_not_awaited()
    runtime.is_hot_swap_imminent = False
    await runtime._flush_hot_swap_audio_cache()

    route_audio.assert_awaited_once_with(
        processed.pcm16,
        sample_rate_hz=processed.sample_rate_hz,
        speech_probability=processed.speech_probability,
        rnnoise_available=processed.rnnoise_available,
        rnnoise_evidence=evidence,
        ingress_token=token,
        received_at=ANY,
        captured_at=2345.6,
    )


async def test_final_transcript_drops_new_conversation_swap_mid_restore() -> None:
    """A real conversation transition still invalidates the prepared final."""
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    timed_session = runtime.session

    replacement = type("Omni", (), {})()
    replacement.create_response = AsyncMock()
    replacement.submit_external_voice_turn = AsyncMock()
    replacement.abandon_external_voice_turn = MagicMock()

    async def _hot_swap_mid_restore(*_args, **_kwargs) -> None:
        runtime._voice_input_transition_generation += 1
        runtime.session = replacement

    runtime._restore_core_asr_preview_after_final = _hot_swap_mid_restore

    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    await runtime._dispatch_core_asr_transcript(
        VoiceTranscriptEvent(turn_token=token, provider="qwen", text="hello"),
    )

    # CodeRabbit: the race is manufactured inside a hook, so if that hook ever
    # stops being called -- preview restore skipped, moved, or bypassed on the
    # accepted branch -- runtime.session would never move, and all three
    # assertions below would pass while modelling an ordinary final with no hot
    # swap at all. Pin that the swap really happened first.
    assert runtime.session is replacement
    timed_session.create_response.assert_not_awaited()
    replacement.create_response.assert_not_awaited()
    replacement.submit_external_voice_turn.assert_not_awaited()


async def test_hot_swap_reuses_matching_asr_provider() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "gemini"
    runtime._independent_asr_route_key = "gemini"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_not_awaited()


async def test_hot_swap_replaces_asr_before_cached_audio_for_new_core() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "glm"
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "gemini"
    runtime._independent_asr_route_key = "gemini"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_awaited_once_with(
        "audio",
        preserve_hot_swap_audio=True,
    )


async def test_blocked_replacement_session_preserves_external_visual_policy_and_fence() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._set_microphone_route("blocked")
    replacement_session = type("ReplacementOmni", (), {})()
    replacement_session.set_visual_delivery_mode = MagicMock()
    replacement_session.block_raw_visual_delivery = MagicMock()
    runtime.session = replacement_session

    runtime._set_microphone_route("blocked")

    replacement_session.set_visual_delivery_mode.assert_not_called()
    replacement_session.block_raw_visual_delivery.assert_called()


def test_hot_swap_replay_damage_accounts_for_rebound_frames() -> None:
    # Codex P2. Cached pre-swap frames carry a stale route generation, so replay
    # rebinds them onto the new session -- but only the local SEND token is
    # rebound; the frame objects appended to damaged_frames keep their original
    # token. The final `any(_ingress_token_matches(frame.token) ...)` check was
    # therefore false, _invalidate_interrupted_voice_turn was skipped, and a
    # prefix that had already reached the new provider stayed in place: later
    # speech got concatenated across the missing tail instead of the damaged
    # turn being cleared.
    #
    # Structural, and deliberately so: driving _flush_hot_swap_audio_cache to a
    # mid-replay failure needs a cache, live session, route mode and token
    # generations. What this pins is that the rebind records current-route
    # damage and that the damage check consults it.
    import inspect

    from main_logic.core import asr_runtime as asr_runtime_module

    source = inspect.getsource(asr_runtime_module.AsrRuntimeMixin._flush_hot_swap_audio_cache)

    assert "rebound_to_current_route = False" in source, (
        "the flush must track whether any frame was rebound onto the live route"
    )
    assert "nonlocal rebound_to_current_route" in source, (
        "replay_frames must be able to record the rebind"
    )
    # Set at the rebind, consulted at the damage check, in that order.
    set_at = source.index("rebound_to_current_route = True")
    # Anchor on the damage condition itself: a bare-name match also hits the
    # `nonlocal` declaration, which sits BEFORE the rebind and inverted this
    # ordering assertion into a false failure.
    checked_at = source.index("if damaged_frames and (")
    assert source.index("token = rebound") < set_at, (
        "the flag belongs with the rebind it records"
    )
    assert set_at < checked_at
    assert "_invalidate_interrupted_voice_turn" in source[checked_at:], (
        "the damage check must still be what gates the invalidation"
    )
