from __future__ import annotations

from main_logic.asr_client.activity_evidence import RnnoiseEvidence
from main_logic.asr_client.throttle_policy import (
    ThrottleAction,
    ThrottleStrategy,
    VoiceThrottlePolicy,
)
from main_logic.voice_turn.contracts import SpeechActivityEvent


def _evidence(*, peak: float, mean: float, last: float) -> RnnoiseEvidence:
    return RnnoiseEvidence(
        available=True,
        frame_count=1,
        peak=peak,
        mean=mean,
        last=last,
        ema=mean,
    )


def test_disabled_optimization_never_grants_local_evidence_upload_authority() -> None:
    policy = VoiceThrottlePolicy(resource_optimization_enabled=False)

    decision = policy.decide(
        _evidence(peak=0.0, mean=0.0, last=0.0),
        candidate_open=False,
        allow_baseline_update=True,
    )

    assert decision.action is ThrottleAction.ALLOW_PROVIDER_AUDIO


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


def test_unavailable_rnnoise_defers_to_silero_without_skipping_pcm() -> None:
    policy = VoiceThrottlePolicy(resource_optimization_enabled=True)

    decision = policy.decide(
        RnnoiseEvidence.unavailable(),
        candidate_open=False,
        allow_baseline_update=False,
    )

    assert decision.action is ThrottleAction.OPEN_CANDIDATE


def test_baseline_updates_only_when_caller_allows_idle_observation() -> None:
    policy = VoiceThrottlePolicy(
        resource_optimization_enabled=True,
        minimum_baseline_samples=2,
        baseline_alpha=1.0,
    )
    quiet = _evidence(peak=0.1, mean=0.1, last=0.1)

    policy.decide(quiet, candidate_open=False, allow_baseline_update=False)
    assert policy.baseline is None
    policy.decide(quiet, candidate_open=True, allow_baseline_update=True)
    assert policy.baseline is None
    policy.observe_silero(SpeechActivityEvent.SPEECH_STARTED)
    policy.decide(quiet, candidate_open=False, allow_baseline_update=True)
    assert policy.baseline is None

    policy.observe_silero(SpeechActivityEvent.NONE)
    policy.decide(quiet, candidate_open=False, allow_baseline_update=True)
    policy.decide(quiet, candidate_open=False, allow_baseline_update=True)

    assert policy.baseline == 0.1
    assert policy.onset_threshold == 0.22


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
    assert not {
        "complete_turn",
        "publish_final",
        "select_provider",
        "fallback_omni",
        "bypass_mic_lease",
    } & {action.value for action in ThrottleAction}


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


def test_shadow_metrics_record_only_low_cardinality_strategy_outcomes() -> None:
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
    assert metrics.fusion_trigger_count == 2
    assert metrics.rnnoise_silero_disagreement_count == 2
