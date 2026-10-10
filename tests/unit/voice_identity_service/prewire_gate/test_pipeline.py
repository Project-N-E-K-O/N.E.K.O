"""Compose the research gate and extraction join, not a production ASR route.

The deterministic scorer/classifier and extracted PCM are test fixtures, not
models or a calibrated identity claim. Assertions concern released bytes only.
"""

from __future__ import annotations

import struct

import pytest

from main_logic.voice_identity_service.prewire_gate import (
    CalibratedIdentityEvidence,
    CalibratedIdentityOutcome,
    ControlledScoringScheduler,
    PrewireGate,
    PrewireIntervalIdentity,
    PrewireIntervalSpec,
    PrewireStreamKey,
    SampleRange,
    ScoringWindowPlan,
)
from main_logic.voice_identity_service.prewire_gate.extraction import (
    ExtractedPrewireAudio,
    PrewireExtractionAdapter,
)

pytestmark = pytest.mark.runtime


class FixtureScorer:
    async def score_async(self, pcm16: bytes, sample_rate_hz: int) -> float:
        assert sample_rate_hz == 16_000
        return 0.8 if struct.unpack_from("<h", pcm16)[0] > 0 else 0.1


class FixtureClassifier:
    def classify(self, observation):
        return CalibratedIdentityEvidence(
            CalibratedIdentityOutcome.OWNER
            if observation.raw_similarity > 0.5
            else CalibratedIdentityOutcome.NONOWNER,
            "fixture_only",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("extraction_first", [True, False])
@pytest.mark.parametrize("classifier_available", [True, False])
async def test_only_keep_extracted_pcm_reaches_sink_after_out_of_order_resolution(
    extraction_first, classifier_available,
):
    scheduler = ControlledScoringScheduler(
        FixtureScorer(), window_plan=ScoringWindowPlan((8,)),
        max_outstanding_jobs=4, max_buffered_pcm_bytes=128,
        deadline_seconds=1.0, close_timeout_seconds=0.1,
    )
    gate = PrewireGate(
        scheduler, classifier=FixtureClassifier() if classifier_available else None,
        window_samples=8, step_samples=8, guard_samples=0,
        max_held_pcm_bytes=128, scoring_parameters_digest="a" * 64,
        required_consistent_observations=1,
    )
    stream = PrewireStreamKey("activated-session", 1)
    versions = dict(profile_generation="profile", model_generation="model",
                    config_generation="config")
    gate.open_stream(stream, **versions)
    adapter = PrewireExtractionAdapter(max_buffered_pcm_bytes=128, max_pending_events=4)
    handle = adapter.open_stream(stream, **versions)
    raw = struct.pack("<24h", *([-100] * 8 + [100] * 8 + [-100] * 8))
    extracted = struct.pack("<24h", *range(24))
    gate.append_pcm(stream, start_sample=0, pcm16=raw)
    submissions = []
    for number in range(3):
        interval = SampleRange(number * 8, (number + 1) * 8)
        spec = PrewireIntervalSpec(
            PrewireIntervalIdentity(stream, number + 1, interval, **versions),
            interval, interval, interval,
            event_ended=True, boundary_trusted=False, independent_event=False,
        )
        submissions.append(gate.submit_interval(spec))
    emitted = []
    try:
        if extraction_first:
            assert adapter.append_extracted(handle, SampleRange(0, 24), extracted) == ()
        # A later result cannot overtake the unclassified first interval.
        for submission in (submissions[1], submissions[2], submissions[0]):
            plan = await gate.resolve(submission)
            if plan is not None:
                for event in plan.events:
                    emitted.extend(adapter.accept_event(handle, event))
                # The adapter accepted the whole local plan, not remote ASR.
                gate.claim(plan)
        if not extraction_first:
            emitted.extend(adapter.append_extracted(handle, SampleRange(0, 24), extracted))
        end = await gate.finish_stream(stream)
        emitted.extend(adapter.accept_event(handle, end))
        sink = [event for event in emitted if isinstance(event, ExtractedPrewireAudio)]
        if classifier_available:
            assert len(sink) == 1
            assert sink[0].original_range == SampleRange(8, 16)
            assert sink[0].asr_range == SampleRange(0, 8)
            assert sink[0].pcm16 == extracted[16:32]
            assert sink[0].pcm16 != raw[16:32]
            assert [event.kind for event in emitted] == ["gap", "enhanced_audio", "gap", "end"]
        else:
            assert sink == []
            assert [event.kind for event in emitted] == ["gap", "gap", "gap", "end"]
        assert adapter.buffered_pcm_bytes == adapter.pending_event_count == 0
        assert gate.held_pcm_bytes == gate.pending_interval_count == 0
    finally:
        adapter.close()
        await gate.close()
