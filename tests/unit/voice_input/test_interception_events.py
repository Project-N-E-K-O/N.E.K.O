from dataclasses import replace

import pytest

from main_logic.voice_input.interception_events import (
    InterceptionDeliveryReceipt,
    InterceptionDeliveryStage,
    InterceptionOutputEvent,
    InterceptionOutputIdentity,
    InterceptionOutputKind,
)


def _audio():
    return InterceptionOutputEvent(
        InterceptionOutputIdentity("session", 1, "profile", "model", "config"),
        "interval-1", 0, InterceptionOutputKind.AUDIO, 100, 104,
        asr_start_sample=0, asr_end_sample=4, pcm16=b"\x01\x00" * 4,
    )


def test_audio_keeps_original_and_asr_axes_distinct():
    audio = _audio()
    gap = replace(
        audio, interval_id="interval-2", sequence=1,
        kind=InterceptionOutputKind.GAP, start_sample=104, end_sample=200,
        asr_start_sample=4, asr_end_sample=4, pcm16=b"", reason="not_owner",
    )
    end = replace(
        gap, interval_id="end", sequence=2, kind=InterceptionOutputKind.END,
        start_sample=200, end_sample=200,
    )
    assert gap.end_sample > gap.start_sample
    assert end.asr_end_sample == audio.asr_end_sample


@pytest.mark.parametrize("changes", [
    {"pcm16": b"short"},
    {"asr_end_sample": 3},
    {"asr_end_sample": None},
    {"sequence": True},
    {"start_sample": -1},
    {"sample_rate_hz": 0},
    {"kind": InterceptionOutputKind.GAP},
    {"kind": InterceptionOutputKind.END, "pcm16": b""},
])
def test_invalid_output_cannot_reach_a_provider(changes):
    with pytest.raises((ValueError, TypeError)):
        replace(_audio(), **changes)


def test_provider_confirmation_needs_evidence_not_a_successful_socket_write():
    audio = _audio()
    written = InterceptionDeliveryReceipt(
        audio, InterceptionDeliveryStage.TRANSPORT_WRITTEN, 1.0,
    )
    with pytest.raises(ValueError):
        replace(written, stage=InterceptionDeliveryStage.PROVIDER_CONFIRMED)
    confirmed = replace(
        written, stage=InterceptionDeliveryStage.PROVIDER_CONFIRMED,
        confirmation_id="provider-item-1",
    )
    assert confirmed.event is written.event


@pytest.mark.parametrize("observed", [float("inf"), float("nan"), -1, True])
def test_receipt_observation_uses_valid_monotonic_time(observed):
    with pytest.raises(ValueError):
        InterceptionDeliveryReceipt(
            _audio(), InterceptionDeliveryStage.LOCAL_ACCEPTED, observed,
        )


def test_authority_cannot_have_empty_versions():
    with pytest.raises(ValueError):
        replace(_audio().identity, model_generation="")
