"""Provider-neutral policy for local voice resource decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from main_logic.voice_turn.contracts import SpeechActivityEvent

from .activity_evidence import ActivityEvidence, RnnoiseEvidence, SileroEvidence


class ThrottleAction(Enum):
    SKIP_IDLE_PCM = "skip_idle_pcm"
    PREWARM = "prewarm"
    OPEN_CANDIDATE = "open_candidate"
    KEEP_CANDIDATE_OPEN = "keep_candidate_open"
    ALLOW_PROVIDER_AUDIO = "allow_provider_audio"
    CLOSE_IDLE_TRANSPORT = "close_idle_transport"


class ThrottleStrategy(Enum):
    RNNOISE_ONLY = "rnnoise_only"
    SILERO_ONLY = "silero_only"
    RNNOISE_PREWARM_SILERO_CONFIRM = "rnnoise_prewarm_silero_confirm"
    FUSION = "fusion"


@dataclass(frozen=True, slots=True)
class ThrottleDecision:
    action: ThrottleAction
    evidence: ActivityEvidence
    onset_threshold: float
    shadow_actions: tuple[tuple[ThrottleStrategy, ThrottleAction], ...] = ()


@dataclass(frozen=True, slots=True)
class ThrottleShadowMetrics:
    evidence_chunk_count: int
    incomplete_chunk_count: int
    rnnoise_trigger_count: int
    silero_trigger_count: int
    fusion_trigger_count: int
    rnnoise_silero_disagreement_count: int


class VoiceThrottlePolicy:
    """Use local evidence only to manage resources, never endpoint authority."""

    def __init__(
        self,
        *,
        resource_optimization_enabled: bool,
        bootstrap_onset: float = 0.35,
        baseline_margin: float = 0.12,
        minimum_onset: float = 0.20,
        maximum_onset: float = 0.65,
        baseline_alpha: float = 0.05,
        minimum_baseline_samples: int = 20,
    ) -> None:
        for name, value in (
            ("bootstrap_onset", bootstrap_onset),
            ("baseline_margin", baseline_margin),
            ("minimum_onset", minimum_onset),
            ("maximum_onset", maximum_onset),
            ("baseline_alpha", baseline_alpha),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within [0, 1]")
        if minimum_onset > maximum_onset:
            raise ValueError("minimum_onset cannot exceed maximum_onset")
        if minimum_baseline_samples <= 0:
            raise ValueError("minimum_baseline_samples must be positive")
        self.resource_optimization_enabled = bool(
            resource_optimization_enabled
        )
        self._bootstrap_onset = bootstrap_onset
        self._baseline_margin = baseline_margin
        self._minimum_onset = minimum_onset
        self._maximum_onset = maximum_onset
        self._baseline_alpha = baseline_alpha
        self._minimum_baseline_samples = minimum_baseline_samples
        self._baseline: float | None = None
        self._baseline_samples = 0
        self._silero = SileroEvidence(False)
        self._evidence_chunk_count = 0
        self._incomplete_chunk_count = 0
        self._shadow_trigger_counts = {
            strategy: 0 for strategy in ThrottleStrategy
        }
        self._rnnoise_silero_disagreement_count = 0

    @property
    def baseline(self) -> float | None:
        return self._baseline

    @property
    def onset_threshold(self) -> float:
        if (
            self._baseline is None
            or self._baseline_samples < self._minimum_baseline_samples
        ):
            return self._bootstrap_onset
        return min(
            self._maximum_onset,
            max(self._minimum_onset, self._baseline + self._baseline_margin),
        )

    def observe_silero(
        self,
        activity: SpeechActivityEvent,
        *,
        available: bool = True,
        probability: float | None = None,
    ) -> None:
        self._silero = SileroEvidence(available, activity, probability)

    def reset_candidate_activity(self) -> None:
        """Forget candidate-scoped VAD state while retaining ambient baseline."""

        self._silero = SileroEvidence(self._silero.available)

    @property
    def shadow_metrics(self) -> ThrottleShadowMetrics:
        return ThrottleShadowMetrics(
            evidence_chunk_count=self._evidence_chunk_count,
            incomplete_chunk_count=self._incomplete_chunk_count,
            rnnoise_trigger_count=self._shadow_trigger_counts[
                ThrottleStrategy.RNNOISE_ONLY
            ],
            silero_trigger_count=self._shadow_trigger_counts[
                ThrottleStrategy.SILERO_ONLY
            ],
            fusion_trigger_count=self._shadow_trigger_counts[
                ThrottleStrategy.FUSION
            ],
            rnnoise_silero_disagreement_count=(
                self._rnnoise_silero_disagreement_count
            ),
        )

    def decide(
        self,
        rnnoise: RnnoiseEvidence,
        *,
        candidate_open: bool,
        allow_baseline_update: bool,
    ) -> ThrottleDecision:
        silero_active = self._silero.activity in {
            SpeechActivityEvent.SPEECH_STARTED,
            SpeechActivityEvent.SPEECH_RESUMED,
        }
        if (
            allow_baseline_update
            and not candidate_open
            and not silero_active
            and rnnoise.available
            and rnnoise.frame_count > 0
            and rnnoise.mean is not None
        ):
            if self._baseline is None:
                self._baseline = rnnoise.mean
            else:
                self._baseline = (
                    self._baseline_alpha * rnnoise.mean
                    + (1.0 - self._baseline_alpha) * self._baseline
                )
            self._baseline_samples += rnnoise.frame_count

        threshold = self.onset_threshold
        evidence = ActivityEvidence(
            rnnoise.with_baseline(self._baseline),
            self._silero,
        )
        if not self.resource_optimization_enabled:
            action = ThrottleAction.ALLOW_PROVIDER_AUDIO
        elif candidate_open:
            action = ThrottleAction.KEEP_CANDIDATE_OPEN
        elif silero_active:
            action = ThrottleAction.OPEN_CANDIDATE
        elif not rnnoise.available or rnnoise.frame_count == 0 or rnnoise.peak is None:
            action = ThrottleAction.OPEN_CANDIDATE
        elif rnnoise.peak >= threshold:
            action = ThrottleAction.PREWARM
        else:
            action = ThrottleAction.SKIP_IDLE_PCM

        rnnoise_action = (
            ThrottleAction.PREWARM
            if rnnoise.available
            and rnnoise.frame_count > 0
            and rnnoise.peak is not None
            and rnnoise.peak >= threshold
            else ThrottleAction.SKIP_IDLE_PCM
        )
        silero_action = (
            ThrottleAction.OPEN_CANDIDATE
            if silero_active
            else ThrottleAction.SKIP_IDLE_PCM
        )
        shadow_actions = (
            (ThrottleStrategy.RNNOISE_ONLY, rnnoise_action),
            (ThrottleStrategy.SILERO_ONLY, silero_action),
            (
                ThrottleStrategy.RNNOISE_PREWARM_SILERO_CONFIRM,
                rnnoise_action,
            ),
            (
                ThrottleStrategy.FUSION,
                ThrottleAction.OPEN_CANDIDATE
                if silero_active
                else rnnoise_action,
            ),
        )
        if rnnoise.available:
            if rnnoise.frame_count > 0:
                self._evidence_chunk_count += 1
            else:
                self._incomplete_chunk_count += 1
        positive_actions = {
            ThrottleAction.PREWARM,
            ThrottleAction.OPEN_CANDIDATE,
        }
        for strategy, shadow_action in shadow_actions:
            if shadow_action in positive_actions:
                self._shadow_trigger_counts[strategy] += 1
        if (rnnoise_action in positive_actions) != (
            silero_action in positive_actions
        ):
            self._rnnoise_silero_disagreement_count += 1
        return ThrottleDecision(
            action,
            evidence,
            threshold,
            shadow_actions,
        )
