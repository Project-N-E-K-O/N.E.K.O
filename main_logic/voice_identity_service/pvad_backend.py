"""Composite pVAD/CAMPPlus backend used by private and shared hosts."""

from __future__ import annotations

from typing import Any

import numpy as np

from main_logic.asr_client.speaker_shadow.campplus import CampPlusBackendFactory
from main_logic.voice_identity.pvad.assets import (
    ECAPA_IDENTITY,
    ECAPA_PREPROCESSING_REVISION,
    ECAPA_REFERENCE_METHOD,
    ECAPA_RESOURCE_REVISION,
)
from main_logic.voice_identity.pvad.models import FireRedPvad

from .pvad_policy import (
    FIRST_CHECKPOINT_SAMPLES,
    MINIMUM_SHORT_SAMPLES,
    PVAD_FRAME_SAMPLES,
    PvadActivityEvidence,
    PvadEvidenceKind,
    observe_pvad_score,
)


def is_compatible_activity_reference(model_identity: object, contract: Any) -> bool:
    """Return whether a stored activity reference matches the shipped pVAD."""

    return bool(
        model_identity == ECAPA_IDENTITY
        and contract is not None
        and contract.resource_revision == ECAPA_RESOURCE_REVISION
        and contract.preprocessing_revision == ECAPA_PREPROCESSING_REVISION
        and contract.reference_method == ECAPA_REFERENCE_METHOD
        and contract.sample_rate_hz == 16_000
    )


class PvadBackendFactory:
    """Spawn-safe composition of pVAD observation and CAMPPlus scoring."""

    def __init__(
        self,
        campplus_factory: CampPlusBackendFactory,
        reference: np.ndarray,
    ) -> None:
        self._campplus_factory = campplus_factory
        self._reference = np.array(reference, dtype=np.float32, copy=True)
        self._closed = False

    def __call__(self) -> PvadSpeakerBackend:
        if self._closed:
            raise RuntimeError("pvad_factory_closed")
        campplus = self._campplus_factory()
        try:
            return PvadSpeakerBackend(campplus, FireRedPvad(self._reference))
        except BaseException:
            campplus.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._campplus_factory.close()
        finally:
            self._reference.fill(0)


class PvadSpeakerBackend:
    """Route shadow short audio to pVAD and keep CAMPPlus speaker scoring intact."""

    def __init__(self, campplus, pvad) -> None:
        self._campplus, self._pvad = campplus, pvad
        self._pvad_available = False
        self._closed = False

    def load(self) -> bool:
        if self._closed or not self._campplus.load():
            return False
        try:
            self._pvad_available = bool(self._pvad.load())
        except Exception:
            self._pvad_available = False
        return True

    def observe_short_candidate(
        self,
        pcm16: bytes,
        sample_rate_hz: int,
    ) -> PvadActivityEvidence:
        if (
            type(pcm16) is not bytes
            or len(pcm16) % 2
            or type(sample_rate_hz) is not int
            or sample_rate_hz != 16_000
        ):
            return PvadActivityEvidence(
                PvadEvidenceKind.UNAVAILABLE,
                "invalid_pvad_audio",
            )
        count = len(pcm16) // 2
        if not MINIMUM_SHORT_SAMPLES <= count < FIRST_CHECKPOINT_SAMPLES:
            return PvadActivityEvidence(
                PvadEvidenceKind.UNAVAILABLE,
                "pvad_unsupported_samples",
            )
        if self._closed or not self._pvad_available:
            return PvadActivityEvidence(
                PvadEvidenceKind.UNAVAILABLE,
                "pvad_model_unavailable",
            )
        covered = count // PVAD_FRAME_SAMPLES * PVAD_FRAME_SAMPLES
        try:
            score = self._pvad.score(pcm16[: covered * 2], sample_rate_hz)
        except Exception:
            return PvadActivityEvidence(
                PvadEvidenceKind.UNAVAILABLE,
                "pvad_model_failure",
            )
        return observe_pvad_score(score, input_sample_count=count)

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        """Legacy private-host adapter: pVAD below 1.5 s, CAMPPlus above it."""

        self._validate_score_audio(pcm16)
        if len(pcm16) // 2 >= FIRST_CHECKPOINT_SAMPLES:
            return self._campplus.score(pcm16, sample_rate_hz)
        return self._score_pvad(pcm16, sample_rate_hz)

    def score_with_mode(
        self,
        pcm16: bytes,
        sample_rate_hz: int,
        *,
        mode: str,
    ) -> float:
        """Serve both shared lanes without exposing pVAD scores to pre-wire."""

        if mode not in ("standard", "short_probe"):
            raise ValueError("score_mode_invalid")
        self._validate_score_audio(pcm16)
        if mode == "standard" and len(pcm16) // 2 < FIRST_CHECKPOINT_SAMPLES:
            return self._score_pvad(pcm16, sample_rate_hz)
        score_with_mode = getattr(self._campplus, "score_with_mode", None)
        if callable(score_with_mode):
            return float(
                score_with_mode(pcm16, sample_rate_hz, mode=mode)
            )
        return float(self._campplus.score(pcm16, sample_rate_hz))

    def _score_pvad(self, pcm16: bytes, sample_rate_hz: int) -> float:
        evidence = self.observe_short_candidate(pcm16, sample_rate_hz)
        if evidence.kind is PvadEvidenceKind.UNAVAILABLE:
            raise ValueError(evidence.reason)
        assert evidence.target_activity_score is not None
        return evidence.target_activity_score

    def _validate_score_audio(self, pcm16: bytes) -> None:
        if self._closed:
            raise RuntimeError("pvad_backend_closed")
        if type(pcm16) is not bytes or len(pcm16) % 2:
            raise ValueError("invalid_pvad_audio")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pvad_available = False
        try:
            self._campplus.close()
        finally:
            self._pvad.close()


__all__ = [
    "PvadBackendFactory",
    "PvadSpeakerBackend",
    "is_compatible_activity_reference",
]
