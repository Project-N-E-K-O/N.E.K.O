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
_PREFIX_CORRECTIONS = (
    ("悠宜悠宜", "悠怡悠怡"),
    ("呦呦呦", "悠怡悠怡"),
    ("哟哟哟", "悠怡悠怡"),
    ("悠宜", "悠怡"),
    ("友谊", "悠怡"),
)


def correct_wake_name_prefix(text: str) -> str:
    """Correct one listed leading spelling, preserving the surrounding text.

    Whitespace and opening quotes may precede the name. Matching is literal and
    longest first; punctuation, suffixes, and interior mentions are untouched.
    """
    start = 0
    while start < len(text) and (
        text[start].isspace() or text[start] in _OPENING_QUOTES
    ):
        start += 1

    for spelling, correction in _PREFIX_CORRECTIONS:
        if text.startswith(spelling, start):
            return text[:start] + correction + text[start + len(spelling) :]
    return text
