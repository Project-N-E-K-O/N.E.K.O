"""Privacy-safe aggregate projection for the current live-room pulse."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


ROOM_PULSE_ACTIVITY_WINDOW_SECONDS = 10.0
ROOM_PULSE_STEADY_MIN_CANDIDATES = 2
ROOM_PULSE_BURST_MIN_CANDIDATES = 5
ROOM_PULSE_HIGH_PRESSURE_MIN_VIEWERS = 3

_PUBLIC_THEME_KEYS = {
    "question_help",
    "meme_play",
    "praise_support",
    "negative_comfort",
    "recommend_tutorial",
    "greeting",
    "small_chat",
}
_REPEATED_SIGNAL_KINDS = {"reaction", "content"}


@dataclass(frozen=True, slots=True)
class RoomPulse:
    """Bounded, deterministic room-state facts safe for runtime status."""

    candidate_count: int = 0
    unique_viewer_count: int = 0
    low_value_ratio: float = 0.0
    question_pressure: str = "none"
    question_support: int = 0
    reaction_pressure: str = "none"
    reaction_support: int = 0
    activity_band: str = "quiet"
    dominant_theme_key: str = ""
    dominant_theme_support: int = 0
    repeated_signal_kind: str = ""
    repeated_signal_support: int = 0

    def to_status(self) -> dict[str, int | float | str]:
        """Return a flat projection for the existing module-status contract."""

        return {
            "room_pulse_version": 0,
            "room_pulse_candidate_count": self.candidate_count,
            "room_pulse_unique_viewer_count": self.unique_viewer_count,
            "room_pulse_low_value_ratio": self.low_value_ratio,
            "room_pulse_question_pressure": self.question_pressure,
            "room_pulse_question_support": self.question_support,
            "room_pulse_reaction_pressure": self.reaction_pressure,
            "room_pulse_reaction_support": self.reaction_support,
            "room_pulse_activity_band": self.activity_band,
            "room_pulse_dominant_theme_key": self.dominant_theme_key,
            "room_pulse_dominant_theme_support": self.dominant_theme_support,
            "room_pulse_repeated_signal_kind": self.repeated_signal_kind,
            "room_pulse_repeated_signal_support": self.repeated_signal_support,
        }


def build_room_pulse(context: Mapping[str, Any] | None) -> RoomPulse:
    """Derive coarse pulse labels from one existing room-topic build pass."""

    if not isinstance(context, Mapping):
        return RoomPulse()

    candidate_count = _bounded_int(context.get("total_candidates"), maximum=80)
    unique_viewer_count = _bounded_int(
        context.get("unique_viewer_count"), maximum=candidate_count
    )
    low_value_count = _bounded_int(
        context.get("low_quality_count"), maximum=candidate_count
    )
    question_support = _bounded_int(
        context.get("question_support_count"), maximum=unique_viewer_count
    )
    reaction_support = _bounded_int(
        context.get("reaction_support_count"), maximum=unique_viewer_count
    )
    recent_activity_count = _bounded_int(
        context.get("recent_activity_count"), maximum=candidate_count
    )
    dominant_theme_key = public_room_theme_key(context.get("dominant_theme_key"))
    dominant_theme_support = _bounded_int(
        context.get("dominant_theme_support"), maximum=unique_viewer_count
    )
    repeated_signal_kind = str(context.get("repeated_signal_kind") or "")
    if repeated_signal_kind not in _REPEATED_SIGNAL_KINDS:
        repeated_signal_kind = ""
    repeated_signal_support = _bounded_int(
        context.get("repeated_signal_support"), maximum=unique_viewer_count
    )
    if repeated_signal_support < 2:
        repeated_signal_kind = ""
        repeated_signal_support = 0

    low_value_ratio = (
        round(low_value_count / candidate_count, 3) if candidate_count else 0.0
    )

    return RoomPulse(
        candidate_count=candidate_count,
        unique_viewer_count=unique_viewer_count,
        low_value_ratio=low_value_ratio,
        question_pressure=_pressure_band(question_support),
        question_support=question_support,
        reaction_pressure=_pressure_band(reaction_support),
        reaction_support=reaction_support,
        activity_band=_activity_band(recent_activity_count),
        dominant_theme_key=dominant_theme_key,
        dominant_theme_support=dominant_theme_support if dominant_theme_key else 0,
        repeated_signal_kind=repeated_signal_kind,
        repeated_signal_support=repeated_signal_support,
    )


def _activity_band(recent_candidate_count: int) -> str:
    if recent_candidate_count >= ROOM_PULSE_BURST_MIN_CANDIDATES:
        return "burst"
    if recent_candidate_count >= ROOM_PULSE_STEADY_MIN_CANDIDATES:
        return "steady"
    return "quiet"


def _pressure_band(unique_viewer_support: int) -> str:
    if unique_viewer_support >= ROOM_PULSE_HIGH_PRESSURE_MIN_VIEWERS:
        return "high"
    if unique_viewer_support > 0:
        return "low"
    return "none"


def public_room_theme_key(value: Any) -> str:
    """Collapse data-derived topic keys before they reach runtime status."""

    key = str(value or "")
    if key in _PUBLIC_THEME_KEYS:
        return key
    if key.startswith("topic:"):
        return "other_topic"
    return ""


def _bounded_int(value: Any, *, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(parsed, max(0, maximum)))
