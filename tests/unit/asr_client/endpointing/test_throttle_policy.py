from __future__ import annotations

import pytest

from main_logic.asr_client.endpointing.activity_evidence import SileroEvidence
from main_logic.asr_client.endpointing.throttle_policy import (
    ThrottleAction,
    ThrottleStrategy,
    VoiceThrottlePolicy,
)
from main_logic.voice_turn.activity_evidence import RnnoiseEvidence
from main_logic.voice_turn.contracts import SpeechActivityEvent


def _evidence(
    *,
    peak: float,
    mean: float,
    last: float,
    frame_count: int = 1,
) -> RnnoiseEvidence:
    return RnnoiseEvidence(
        available=True,
        frame_count=frame_count,
        peak=peak,
        mean=mean,
        last=last,
        ema=mean,
    )


def test_disabled_optimization_processes_pcm_without_granting_authority() -> None:
    policy = VoiceThrottlePolicy(resource_optimization_enabled=False)

    decision = policy.decide(
        _evidence(peak=0.0, mean=0.0, last=0.0),
        candidate_open=False,
        allow_baseline_update=True,
    )

    assert decision.action is ThrottleAction.PROCESS_PCM
    assert not {
        "allow_provider_audio",
        "complete_turn",
        "publish_final",
        "select_provider",
        "fallback_omni",
        "bypass_mic_lease",
    } & {action.value for action in ThrottleAction}


def test_rnnoise_onset_uses_peak_instead_of_last_probability() -> None:
    policy = VoiceThrottlePolicy(resource_optimization_enabled=True)

    decision = policy.decide(
        _evidence(peak=0.8, mean=0.35, last=0.05),
        candidate_open=False,
        allow_baseline_update=False,
    )

    assert decision.action is ThrottleAction.PREWARM


def test_open_candidate_keeps_low_probability_pcm() -> None:
    policy = VoiceThrottlePolicy(resource_optimization_enabled=True)

    decision = policy.decide(
        _evidence(peak=0.0, mean=0.0, last=0.0),
        candidate_open=True,
        allow_baseline_update=False,
    )

    assert decision.action is ThrottleAction.KEEP_CANDIDATE_OPEN


def test_unavailable_or_incomplete_rnnoise_fails_open_to_local_detection() -> None:
    policy = VoiceThrottlePolicy(resource_optimization_enabled=True)

    unavailable = policy.decide(
        RnnoiseEvidence.unavailable(),
        candidate_open=False,
        allow_baseline_update=False,
    )
    incomplete = policy.decide(
        RnnoiseEvidence(True, 0, None, None, None, None),
        candidate_open=False,
        allow_baseline_update=False,
    )

    assert unavailable.action is ThrottleAction.OPEN_CANDIDATE
    assert incomplete.action is ThrottleAction.OPEN_CANDIDATE


def test_baseline_updates_only_from_caller_approved_quiet_idle_chunks() -> None:
    policy = VoiceThrottlePolicy(
        resource_optimization_enabled=True,
        minimum_baseline_samples=2,
        baseline_alpha=1.0,
    )
    quiet = _evidence(peak=0.1, mean=0.1, last=0.1)
    loud = _evidence(peak=0.8, mean=0.7, last=0.6)

    policy.decide(quiet, candidate_open=False, allow_baseline_update=False)
    policy.decide(quiet, candidate_open=True, allow_baseline_update=True)
    policy.observe_silero(SpeechActivityEvent.SPEECH_STARTED)
    policy.decide(quiet, candidate_open=False, allow_baseline_update=True)
    policy.observe_silero(SpeechActivityEvent.NONE)
    policy.decide(loud, candidate_open=False, allow_baseline_update=True)
    assert policy.baseline is None

    policy.decide(quiet, candidate_open=False, allow_baseline_update=True)
    policy.decide(quiet, candidate_open=False, allow_baseline_update=True)

    assert policy.baseline == 0.1
    assert policy.onset_threshold == pytest.approx(0.22)


def test_onset_threshold_is_bounded_after_baseline_warmup() -> None:
    high = VoiceThrottlePolicy(
        resource_optimization_enabled=True,
        baseline_margin=0.5,
        maximum_onset=0.65,
        minimum_baseline_samples=1,
    )
    low = VoiceThrottlePolicy(
        resource_optimization_enabled=True,
        baseline_margin=0.0,
        minimum_onset=0.2,
        minimum_baseline_samples=1,
    )

    high.decide(
        _evidence(peak=0.3, mean=0.3, last=0.3),
        candidate_open=False,
        allow_baseline_update=True,
    )
    low.decide(
        _evidence(peak=0.05, mean=0.05, last=0.05),
        candidate_open=False,
        allow_baseline_update=True,
    )

    assert high.onset_threshold == 0.65
    assert low.onset_threshold == 0.2


def test_shadow_results_cover_all_supported_strategies() -> None:
    policy = VoiceThrottlePolicy(resource_optimization_enabled=True)

    decision = policy.decide(
        _evidence(peak=0.8, mean=0.4, last=0.1),
        candidate_open=False,
        allow_baseline_update=False,
    )

    assert {strategy for strategy, _action in decision.shadow_actions} == set(
        ThrottleStrategy
    )


def test_candidate_reset_clears_silero_activity_but_keeps_baseline() -> None:
    policy = VoiceThrottlePolicy(
        resource_optimization_enabled=True,
        minimum_baseline_samples=1,
    )
    quiet = _evidence(peak=0.1, mean=0.1, last=0.1)
    policy.decide(quiet, candidate_open=False, allow_baseline_update=True)
    policy.observe_silero(SpeechActivityEvent.SPEECH_STARTED)

    policy.reset_candidate_activity()
    decision = policy.decide(
        quiet,
        candidate_open=False,
        allow_baseline_update=False,
    )

    assert policy.baseline == 0.1
    assert decision.evidence.silero.activity is None
    assert decision.action is ThrottleAction.SKIP_IDLE_PCM


def test_shadow_metrics_record_only_low_cardinality_outcomes() -> None:
    policy = VoiceThrottlePolicy(resource_optimization_enabled=True)

    policy.decide(
        _evidence(peak=0.8, mean=0.4, last=0.1),
        candidate_open=False,
        allow_baseline_update=False,
    )
    policy.observe_silero(SpeechActivityEvent.SPEECH_STARTED)
    policy.decide(
        RnnoiseEvidence(True, 0, None, None, None, None),
        candidate_open=False,
        allow_baseline_update=False,
    )

    metrics = policy.shadow_metrics
    assert metrics.evidence_chunk_count == 1
    assert metrics.incomplete_chunk_count == 1
    assert metrics.rnnoise_trigger_count == 1
    assert metrics.silero_trigger_count == 1
    assert metrics.rnnoise_silero_disagreement_count == 2
    assert not hasattr(metrics, "pcm")
    assert not hasattr(metrics, "probability_sequence")


def test_shadow_actions_do_not_duplicate_confirm_strategy_as_fusion() -> None:
    policy = VoiceThrottlePolicy(resource_optimization_enabled=True)

    decision = policy.decide(
        _evidence(peak=0.8, mean=0.4, last=0.1),
        candidate_open=False,
        allow_baseline_update=False,
    )

    strategies = [strategy for strategy, _ in decision.shadow_actions]
    assert strategies == [
        ThrottleStrategy.RNNOISE_ONLY,
        ThrottleStrategy.SILERO_ONLY,
        ThrottleStrategy.RNNOISE_PREWARM_SILERO_CONFIRM,
    ]
    assert len(strategies) == len(set(strategies))


def test_unavailable_silero_cannot_carry_activity_or_probability() -> None:
    with pytest.raises(
        ValueError,
        match="unavailable Silero evidence cannot carry activity",
    ):
        SileroEvidence(
            False,
            SpeechActivityEvent.SPEECH_STARTED,
            0.9,
        )
