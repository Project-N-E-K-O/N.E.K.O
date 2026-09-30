"""Separate raw endpoint activity from permission to open an ASR turn."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
import logging
from uuid import uuid4

from main_logic.voice_turn.admission import (
    AdmissionConfig,
    AdmissionDecision,
    CandidateAdmission,
    SpeechEvidence,
)
from main_logic.voice_turn.contracts import SpeechActivityEvent
from .silero_vad import SileroActivityGate, SileroVad
from .config import SmartTurnConfig

logger = logging.getLogger(__name__)


class AdmissionAudioRangeError(ValueError):
    """Source PCM cannot be attributed to one continuous admission stream."""


@dataclass(frozen=True, slots=True)
class AdmissionActivity:
    activity: SpeechActivityEvent
    evidence: SpeechEvidence
    audio_start_sample: int


class AdmissionActivityGate(SileroActivityGate):
    """One Silero inference, two event views. Raw events retain their meaning.

    Only ``admission_events`` may open a new external user turn. ``feed`` still
    returns raw events for SmartTurn continuation and endpoint evaluation.
    Reset is owned by the detector worker; provider seal only ends admission.
    """

    def __init__(
        self,
        vad: SileroVad,
        config: SmartTurnConfig,
        *,
        admission_config: AdmissionConfig | None = None,
        admission_shadow_config: AdmissionConfig | None = None,
    ) -> None:
        self._admission_config = admission_config or AdmissionConfig()
        self._admission_shadow_config = admission_shadow_config
        super().__init__(vad, config)

    def reset(self) -> None:
        super().reset()
        scope = uuid4().hex
        self._admission = CandidateAdmission(scope, self._admission_config)
        self._shadow_admission = (
            CandidateAdmission(scope + "-shadow", self._admission_shadow_config)
            if self._admission_shadow_config is not None else None
        )
        self._shadow_rejected_end = 0
        self._activity_low_windows = 0
        self._sample_cursor = 0
        self.admission_events: tuple[SpeechActivityEvent, ...] = ()
        self.admission_records: tuple[AdmissionActivity, ...] = ()
        self._published = False
        self._seen_samples = 0
        self._source_offset = 0
        self._rejected_end = 0
        self.event_evidence = None
        self.event_audio_start_sample = None
        self.had_admission = False

    @property
    def evidence(self):
        value = self._admission.snapshot
        if value is None:
            return None
        return replace(
            value,
            audio_start_sample=value.audio_start_sample + self._source_offset,
            audio_end_sample=value.audio_end_sample + self._source_offset,
            terminal_audio_end_sample=(
                value.terminal_audio_end_sample + self._source_offset
                if value.terminal_audio_end_sample is not None else None
            ),
        )

    @property
    def retained_start_sample(self):
        value = self._admission.snapshot
        if value is None:
            return None
        return self._source_offset + max(
            self._rejected_end, value.audio_start_sample - 2048, 0
        )

    def feed_at(self, pcm16: bytes, source_end_sample: int):
        count = len(pcm16) // 2
        if source_end_sample < count or len(pcm16) % 2:
            raise AdmissionAudioRangeError("invalid admission PCM source range")
        if self._seen_samples and source_end_sample != (
            self._source_offset + self._seen_samples + count
        ):
            raise AdmissionAudioRangeError("discontinuous admission PCM source range")
        self._seen_samples += count
        self._source_offset = source_end_sample - self._seen_samples
        return self.feed(pcm16)

    def seal_admission(self, *, start_sample: int | None = None):
        """End admission at the owner's absolute PCM boundary.

        The VAD can still hold a partial model window before this boundary.
        Keep that context for inference, but never count it for the successor.
        """
        boundary = (
            self._sample_cursor if start_sample is None
            else start_sample - self._source_offset
        )
        if boundary < self._sample_cursor:
            raise ValueError("admission boundary precedes observed audio")
        sealed = self._admission.seal()
        if self._shadow_admission is not None:
            self._shadow_admission.seal()
        self._shadow_rejected_end = max(self._shadow_rejected_end, boundary)
        self._published = False
        self.admission_events = ()
        self._rejected_end = max(
            self._rejected_end,
            boundary,
        )
        self.event_evidence = None
        self.event_audio_start_sample = None
        self.had_admission = False
        return sealed

    @property
    def shadow_evidence(self):
        value = self._shadow_admission.snapshot if self._shadow_admission else None
        if value is None:
            return None
        return replace(
            value,
            audio_start_sample=value.audio_start_sample + self._source_offset,
            audio_end_sample=value.audio_end_sample + self._source_offset,
            terminal_audio_end_sample=(
                value.terminal_audio_end_sample + self._source_offset
                if value.terminal_audio_end_sample is not None else None
            ),
        )

    def _observe_shadow(self, end: int, probability: float) -> None:
        shadow = self._shadow_admission
        if shadow is None:
            return
        before = shadow.snapshot
        start = max(self._sample_cursor, self._shadow_rejected_end)
        if start >= end:
            return
        evidence = shadow.observe(start, end, probability)
        if evidence is None:
            return
        if evidence.decision is AdmissionDecision.REJECT:
            self._shadow_rejected_end = max(self._shadow_rejected_end, end)
        if before is None or (before.candidate_id, before.decision) != (
            evidence.candidate_id, evidence.decision,
        ):
            logger.info(
                "[voice-admission-shadow] scope=%s candidate=%s start_sample=%s "
                "end_sample=%s decision=%s reason=%s voiced_ms=%.1f "
                "uncertain_ms=%.1f internal_gap_ms=%.1f trailing_low_ms=%.1f",
                evidence.scope_id, evidence.candidate_id,
                evidence.audio_start_sample + self._source_offset,
                evidence.audio_end_sample + self._source_offset,
                evidence.decision.value, evidence.reason, evidence.voiced_audio_ms,
                evidence.uncertain_samples / 16, evidence.internal_gap_samples / 16,
                evidence.trailing_low_samples / 16,
            )
        # Shadow acceptance must not persist forever when the authoritative
        # legacy gate never started activity for this short word. End only the
        # shadow observation at the same existing activity pause boundary.
        if (
            evidence.decision is AdmissionDecision.ADMIT
            and self._activity_low_windows >= self._candidate_silence_windows
        ):
            shadow.seal()
            self._shadow_rejected_end = max(self._shadow_rejected_end, end)

    def process_probabilities(self, probabilities: Iterable[float]):
        raw_events = []
        records = []
        for probability in probabilities:
            # Boundaries belong to model windows, not websocket packets. One
            # large packet can contain a pause and a successor candidate.
            raw = super().process_probabilities((probability,))
            if probability >= self._config.onset_probability:
                self._activity_low_windows = 0
            elif probability < self._config.offset_probability:
                self._activity_low_windows += 1
            previous = self._admission.snapshot
            end = self._sample_cursor + SileroVad.WINDOW_SAMPLES
            self._observe_shadow(end, probability)
            start = max(self._sample_cursor, self._rejected_end)
            # Probability describes the whole model window; weight only its
            # owned successor duration. Raw VAD above still sees every window.
            local = (
                self._admission.observe(start, end, probability)
                if start < end else None
            )
            self._sample_cursor = end
            if local is not None and local.decision is AdmissionDecision.REJECT:
                self._rejected_end = max(self._rejected_end, local.audio_end_sample)
            evidence = self.evidence
            if evidence is not None and (
                previous is None
                or (previous.candidate_id, previous.decision)
                != (evidence.candidate_id, evidence.decision)
            ):
                logger.info(
                    "[voice-admission] scope=%s candidate=%s start_sample=%s end_sample=%s "
                    "observed_ms=%.1f voiced_ms=%.1f longest_run_ms=%.1f longest_gap_ms=%.1f "
                    "probability_mean=%.3f probability_peak=%.3f rnnoise_mean=%s "
                    "playback_active=%s decision=%s reason=%s admission_path=%s "
                    "uncertain_ms=%.1f internal_gap_ms=%.1f trailing_low_ms=%.1f "
                    "candidate_span_ms=%.1f",
                    evidence.scope_id,
                    evidence.candidate_id,
                    evidence.audio_start_sample,
                    evidence.audio_end_sample,
                    evidence.observed_audio_ms,
                    evidence.voiced_audio_ms,
                    evidence.longest_speech_run_samples / 16,
                    evidence.longest_gap_samples / 16,
                    evidence.probability_mean,
                    evidence.probability_peak,
                    evidence.rnnoise_mean,
                    evidence.playback_active,
                    evidence.decision.value,
                    evidence.reason,
                    evidence.admission_path,
                    evidence.uncertain_samples / 16,
                    evidence.internal_gap_samples / 16,
                    evidence.trailing_low_samples / 16,
                    (evidence.audio_end_sample - evidence.audio_start_sample) / 16,
                )
            if evidence is not None and evidence.decision is AdmissionDecision.ADMIT:
                if not self._published and self._admission_config.experimental_short_speech:
                    raw += self.confirm_admitted_activity(
                        trailing_silence_windows=self._activity_low_windows,
                    )
                admitted = (
                    raw if self._published else (
                        SpeechActivityEvent.SPEECH_STARTED,
                        *(event for event in raw if event is SpeechActivityEvent.CANDIDATE_PAUSE),
                    )
                )
                self._published = True
                self.had_admission = True
                records.extend(
                    AdmissionActivity(event, evidence, self.retained_start_sample)
                    for event in admitted
                )
            raw_events.extend(raw)
            if SpeechActivityEvent.CANDIDATE_PAUSE in raw:
                # Only the potential next onset needs new evidence. Streaming
                # ASR audio and raw SmartTurn continuation stay uninterrupted.
                self._admission.seal()
                if self._shadow_admission is not None:
                    self._shadow_admission.seal()
                self._shadow_rejected_end = max(self._shadow_rejected_end, self._sample_cursor)
                self._published = False
                self._rejected_end = max(self._rejected_end, self._sample_cursor)
        self.admission_records = tuple(records)
        self.admission_events = tuple(record.activity for record in records)
        self.event_evidence = records[-1].evidence if records else self.evidence
        self.event_audio_start_sample = (
            records[-1].audio_start_sample if records else self.retained_start_sample
        )
        return tuple(raw_events)
