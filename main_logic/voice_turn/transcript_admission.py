"""Shared display/final policy using local evidence, never ASR confidence."""

from dataclasses import dataclass
from enum import Enum
import unicodedata

from .admission import AdmissionDecision, SpeechEvidence


class TranscriptDisposition(str, Enum):
    ALLOW = "allow"
    HOLD = "hold"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class TranscriptAdmission:
    disposition: TranscriptDisposition
    reason: str


_FILLERS = frozenset({"嗯", "嗯嗯", "啊", "呃", "哦", "噢", "唔", "uh", "um", "hmm"})
_CONTROLS = frozenset({"停", "停止", "取消", "打断", "别说了", "stop", "cancel"})


def assess_transcript(
    text: str, evidence: SpeechEvidence | None, *, is_voice_source: bool, final: bool
) -> TranscriptAdmission:
    normalized = text.strip().casefold()
    while normalized and (
        normalized[0].isspace() or unicodedata.category(normalized[0]).startswith("P")
    ):
        normalized = normalized[1:]
    while normalized and (
        normalized[-1].isspace() or unicodedata.category(normalized[-1]).startswith("P")
    ):
        normalized = normalized[:-1]
    if not is_voice_source or normalized in _CONTROLS or normalized not in _FILLERS:
        return TranscriptAdmission(TranscriptDisposition.ALLOW, "content_allowed")
    if not isinstance(evidence, SpeechEvidence):
        return TranscriptAdmission(TranscriptDisposition.ALLOW, "evidence_missing")
    if evidence.decision is AdmissionDecision.ADMIT:
        return TranscriptAdmission(
            TranscriptDisposition.ALLOW, "confirmed_short_response"
        )
    return TranscriptAdmission(
        TranscriptDisposition.REJECT if final else TranscriptDisposition.HOLD,
        "insufficient_local_speech_evidence",
    )
