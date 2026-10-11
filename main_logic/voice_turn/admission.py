"""Sample-scoped local speech evidence; independent of endpoint authority.

Thresholds are experiment defaults, not ASR confidence or proof of identity.
The owner supplies normalized 16 kHz window offsets exactly once and seals at
its endpoint boundary. Network waits and replayed PCM never advance evidence.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import math


class AdmissionDecision(str, Enum):
    PENDING = "pending"
    ADMIT = "admit"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class AdmissionConfig:
    sample_rate: int = 16_000
    minimum_voiced_ms: int = 224
    window_ms: int = 480
    minimum_occupancy: float = 0.65
    maximum_gap_ms: int = 96
    maximum_candidate_ms: int = 640
    onset_probability: float = 0.5
    # Opt-in experimental policy, deliberately not calibrated deployment values.
    experimental_short_speech: bool = False
    uncertain_probability: float = 0.35
    maximum_uncertain_run_ms: int = 96
    maximum_uncertain_ms: int = 128
    short_minimum_voiced_ms: int = 192
    short_minimum_run_ms: int = 96
    short_end_ms: int = 128
    # Existing 700 ms pending capacity minus 128 ms of onset pre-roll.
    short_maximum_candidate_ms: int = 572
    short_minimum_occupancy: float = 0.8
    short_minimum_probability: float = 0.7

    def __post_init__(self) -> None:
        if self.sample_rate != 16_000:
            raise ValueError("admission requires normalized 16 kHz audio")
        if (
            not 0
            < self.minimum_voiced_ms
            <= self.window_ms
            <= self.maximum_candidate_ms
        ):
            raise ValueError("invalid admission duration limits")
        if not 0 < self.maximum_gap_ms < self.maximum_candidate_ms:
            raise ValueError("invalid admission gap limit")
        if not 0 < self.minimum_occupancy <= 1 or not 0 < self.onset_probability <= 1:
            raise ValueError("invalid admission probability")
        if self.experimental_short_speech:
            if not 0 <= self.uncertain_probability < self.onset_probability:
                raise ValueError("invalid uncertain probability")
            if not (
                0
                < self.maximum_uncertain_run_ms
                <= self.maximum_uncertain_ms
                < self.maximum_candidate_ms
            ):
                raise ValueError("invalid uncertain duration limits")
            if not (
                0
                < self.short_minimum_run_ms
                <= self.short_minimum_voiced_ms
                < self.minimum_voiced_ms
                and 0 < self.short_end_ms
                and self.short_minimum_voiced_ms + self.short_end_ms
                <= self.short_maximum_candidate_ms
                <= min(self.maximum_candidate_ms, 572)
            ):
                raise ValueError("invalid short speech duration limits")
            if not (
                self.minimum_occupancy <= self.short_minimum_occupancy <= 1
                and self.onset_probability <= self.short_minimum_probability <= 1
            ):
                raise ValueError("invalid short speech quality limits")


@dataclass(frozen=True, slots=True)
class SpeechEvidence:
    scope_id: str
    candidate_id: int
    audio_start_sample: int
    audio_end_sample: int
    observed_samples: int
    voiced_samples: int
    longest_speech_run_samples: int
    longest_gap_samples: int
    probability_mean: float
    probability_peak: float
    decision: AdmissionDecision
    reason: str
    rnnoise_mean: float | None = None
    playback_active: bool | None = None
    admission_path: str | None = None
    terminal_audio_end_sample: int | None = None
    uncertain_samples: int = 0
    longest_uncertain_run_samples: int = 0
    internal_gap_samples: int = 0
    trailing_low_samples: int = 0
    core_samples: int = 0
    core_probability_mean: float = 0.0
    missing_samples: int = 0

    @property
    def observed_audio_ms(self) -> float:
        return self.observed_samples / 16

    @property
    def voiced_audio_ms(self) -> float:
        return self.voiced_samples / 16


class CandidateAdmission:
    """Bounded pending candidate plus frozen decision; not a session FSM.

    An accepted utterance stays accepted through silence until its owner seals
    it. A rejected candidate can be replaced by a later voiced window, with a
    new identity. A returned frozen snapshot never changes with its successor.
    """

    def __init__(self, scope_id: str, config: AdmissionConfig | None = None) -> None:
        self.scope_id = scope_id
        self.config = config or AdmissionConfig()
        self._cursor = 0
        self._candidate_sequence = 0
        self._snapshot: SpeechEvidence | None = None
        self._window: deque[tuple[int, int, bool]] = deque()
        self._speech_run = self._gap = 0
        self._weighted_probability = 0.0
        self._uncertain_run = 0
        self._trailing_low = 0
        self._core_weighted_probability = 0.0
        self._speech_run_windows = self._longest_speech_run_windows = 0

    @property
    def snapshot(self) -> SpeechEvidence | None:
        return self._snapshot

    def seal(self) -> SpeechEvidence | None:
        snapshot = self._snapshot
        self._snapshot = None
        self._window.clear()
        self._speech_run = self._gap = 0
        self._weighted_probability = 0.0
        self._uncertain_run = self._trailing_low = 0
        self._core_weighted_probability = 0.0
        self._speech_run_windows = self._longest_speech_run_windows = 0
        return snapshot

    def observe(
        self, start_sample: int, end_sample: int, probability: float
    ) -> SpeechEvidence | None:
        if start_sample < self._cursor:
            raise ValueError("duplicate or overlapping admission evidence")
        if end_sample <= start_sample or start_sample < 0:
            raise ValueError("invalid audio sample interval")
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("invalid local speech probability")
        previous_cursor = self._cursor
        self._cursor = end_sample
        voiced = probability >= self.config.onset_probability
        old = self._snapshot
        if old is not None and old.decision is AdmissionDecision.REJECT:
            if not voiced:
                return old
            self.seal()
            old = None
        if old is None:
            if not voiced:
                return None
            self._candidate_sequence += 1
            old = SpeechEvidence(
                self.scope_id,
                self._candidate_sequence,
                start_sample,
                start_sample,
                0,
                0,
                0,
                0,
                0.0,
                0.0,
                AdmissionDecision.PENDING,
                "collecting",
            )
        # Missing windows are gaps, never inferred silence or human speech.
        missing = max(0, start_sample - max(previous_cursor, old.audio_start_sample))
        size = end_sample - start_sample
        self._gap += missing
        longest_gap = max(old.longest_gap_samples, self._gap)
        if voiced:
            self._speech_run = (self._speech_run if not missing else 0) + size
            self._speech_run_windows = (
                self._speech_run_windows if not missing else 0
            ) + 1
            self._gap = 0
        else:
            self._gap += size
            self._speech_run = 0
            self._speech_run_windows = 0
        self._longest_speech_run_windows = max(
            self._longest_speech_run_windows, self._speech_run_windows
        )
        longest_gap = max(longest_gap, self._gap)
        self._weighted_probability += probability * size
        observed = old.observed_samples + size
        uncertain = (
            self.config.uncertain_probability
            <= probability
            < self.config.onset_probability
        )
        self._uncertain_run = (
            (self._uncertain_run if not missing else 0) + size if uncertain else 0
        )
        self._trailing_low = (
            (self._trailing_low if not missing else 0) + size
            if probability < self.config.uncertain_probability
            else 0
        )
        if not self._trailing_low:
            self._core_weighted_probability = self._weighted_probability
        core = end_sample - old.audio_start_sample - self._trailing_low
        core_mean = self._core_weighted_probability / core if core else 0.0
        uncertain_samples = old.uncertain_samples + (size if uncertain else 0)
        longest_uncertain = max(old.longest_uncertain_run_samples, self._uncertain_run)
        voiced_samples = old.voiced_samples + (size if voiced else 0)
        longest_run = max(old.longest_speech_run_samples, self._speech_run)
        missing_samples = old.missing_samples + missing
        self._window.append((start_sample, end_sample, voiced))
        cutoff = end_sample - self.config.window_ms * 16
        while self._window and self._window[0][1] <= cutoff:
            self._window.popleft()
        window_voiced = sum(
            end - max(start, cutoff) for start, end, speech in self._window if speech
        )
        span = end_sample - max(old.audio_start_sample, cutoff)
        decision, reason = old.decision, old.reason
        if decision is AdmissionDecision.PENDING:
            if self.config.experimental_short_speech:
                decision, reason = self._experimental_decision(
                    missing_samples=missing_samples,
                    candidate_span=end_sample - old.audio_start_sample,
                    uncertain_samples=uncertain_samples,
                    longest_uncertain=longest_uncertain,
                    window_voiced=window_voiced,
                    window_span=span,
                    voiced_samples=voiced_samples,
                    longest_run=longest_run,
                    core=core,
                    core_mean=core_mean,
                )
            elif longest_gap > self.config.maximum_gap_ms * 16:
                decision, reason = AdmissionDecision.REJECT, "speech_gap"
            elif (
                end_sample - old.audio_start_sample
                > self.config.maximum_candidate_ms * 16
            ):
                decision, reason = AdmissionDecision.REJECT, "candidate_timeout"
            elif (
                window_voiced >= self.config.minimum_voiced_ms * 16
                and window_voiced / span >= self.config.minimum_occupancy
            ):
                decision, reason = AdmissionDecision.ADMIT, "speech_window"
        self._snapshot = SpeechEvidence(
            self.scope_id,
            old.candidate_id,
            old.audio_start_sample,
            end_sample,
            observed,
            voiced_samples,
            longest_run,
            longest_gap,
            self._weighted_probability / observed,
            max(old.probability_peak, probability),
            decision,
            reason,
            admission_path=(
                old.admission_path
                or ("short" if reason == "short_speech_end" else "ordinary")
                if decision is AdmissionDecision.ADMIT
                else None
            ),
            terminal_audio_end_sample=(
                old.terminal_audio_end_sample or end_sample
                if decision is not AdmissionDecision.PENDING
                else None
            ),
            uncertain_samples=uncertain_samples,
            longest_uncertain_run_samples=longest_uncertain,
            internal_gap_samples=max(0, core - voiced_samples - missing_samples),
            trailing_low_samples=self._trailing_low,
            core_samples=core,
            core_probability_mean=core_mean,
            missing_samples=missing_samples,
        )
        return self._snapshot

    def _experimental_decision(
        self,
        *,
        missing_samples: int,
        candidate_span: int,
        uncertain_samples: int,
        longest_uncertain: int,
        window_voiced: int,
        window_span: int,
        voiced_samples: int,
        longest_run: int,
        core: int,
        core_mean: float,
    ) -> tuple[AdmissionDecision, str]:
        """Assess a complete bounded core without selecting its best subspan.

        Trailing low evidence establishes a local end observation, not an ASR
        endpoint. Unknown scores remain in the core and never become voice.
        """
        config = self.config
        if missing_samples:
            return AdmissionDecision.REJECT, "audio_range_missing"
        if candidate_span > config.maximum_candidate_ms * 16:
            return AdmissionDecision.REJECT, "candidate_timeout"
        if (
            longest_uncertain > config.maximum_uncertain_run_ms * 16
            or uncertain_samples > config.maximum_uncertain_ms * 16
        ):
            return AdmissionDecision.REJECT, "uncertain_budget"
        if (
            window_voiced >= config.minimum_voiced_ms * 16
            and window_voiced / window_span >= config.minimum_occupancy
        ):
            return AdmissionDecision.ADMIT, "speech_window"
        if self._trailing_low >= config.short_end_ms * 16:
            if candidate_span > config.short_maximum_candidate_ms * 16:
                return AdmissionDecision.REJECT, "short_audio_budget"
            if (
                voiced_samples >= config.short_minimum_voiced_ms * 16
                and longest_run >= config.short_minimum_run_ms * 16
                and self._longest_speech_run_windows >= 3
                and core > 0
                and voiced_samples / core >= config.short_minimum_occupancy
                and core_mean >= config.short_minimum_probability
            ):
                return AdmissionDecision.ADMIT, "short_speech_end"
            return AdmissionDecision.REJECT, "short_speech_quality"
        return AdmissionDecision.PENDING, "collecting"
