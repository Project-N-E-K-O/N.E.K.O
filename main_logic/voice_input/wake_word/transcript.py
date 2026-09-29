"""Explicit sentence-prefix corrections for a verified wake-word turn.

The caller owns activation and turn eligibility. This module holds the ticket
identity and rewrites known ASR spellings at the start of a final transcript.
"""

from dataclasses import dataclass

from main_logic.voice_input.activation.contracts import ActivationGeneration
from main_logic.voice_turn.contracts import VoiceTurnToken


@dataclass(eq=False, slots=True)
class _WakeNameCorrection:
    """One activation's correction eligibility, bound to at most one turn."""

    generation: ActivationGeneration
    runtime: object
    delivery_revision: int
    turn_token: VoiceTurnToken | None = None


_OPENING_QUOTES = frozenset("\"'“‘「『")
# Observed Qwen spellings from user reports and recording/TTS replays.
# Keep compound matches before singles.
_PREFIX_CORRECTIONS = (
    ("欢迎悠怡", "悠怡悠怡"),
    ("欢迎优依", "悠怡悠怡"),
    ("悠宜悠宜", "悠怡悠怡"),
    ("悠移悠移", "悠怡悠怡"),
    ("优仪优仪", "悠怡悠怡"),
    ("悠矣悠矣", "悠怡悠怡"),
    ("有有有", "悠怡悠怡"),
    ("呦呦呦", "悠怡悠怡"),
    ("哟哟哟", "悠怡悠怡"),
    ("悠宜", "悠怡"),
    ("友谊", "悠怡"),
    ("优姨", "悠怡"),
    ("悠移", "悠怡"),
    ("优仪", "悠怡"),
    ("优矣", "悠怡"),
    ("悠矣", "悠怡"),
)
# Ambiguous words/names must not become a general prefix rewrite (e.g. 忧郁症).
_STANDALONE_CORRECTIONS = (
    ("又一又一", "悠怡悠怡"),
    ("忧郁忧郁", "悠怡悠怡"),
    ("忧郁", "悠怡"),
    ("由于", "悠怡"),
    ("英语", "悠怡"),
    ("刘怡", "悠怡"),
)
_UTTERANCE_TRAILING_CHARACTERS = frozenset("。.!！?？…\"'”’」』")


def correct_wake_name_prefix(text: str) -> str:
    """Correct one listed leading spelling, preserving the surrounding text.

    Whitespace and opening quotes may precede the name. Matching is literal and
    longest first; punctuation, suffixes, and interior mentions are untouched.
    Ambiguous ordinary words additionally require an otherwise empty utterance.
    """
    start = 0
    while start < len(text) and (
        text[start].isspace() or text[start] in _OPENING_QUOTES
    ):
        start += 1

    for spelling, correction in _PREFIX_CORRECTIONS:
        if text.startswith(spelling, start):
            return text[:start] + correction + text[start + len(spelling) :]
    for spelling, correction in _STANDALONE_CORRECTIONS:
        if text.startswith(spelling, start):
            suffix = text[start + len(spelling) :]
            if all(ch.isspace() or ch in _UTTERANCE_TRAILING_CHARACTERS for ch in suffix):
                return text[:start] + correction + suffix
    return text
