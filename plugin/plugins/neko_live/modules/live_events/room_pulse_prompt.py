"""Compact RoomPulse prompt projection for already-scheduled live turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .provider_event import public_text
from .room_pulse import build_room_pulse


ROOM_PULSE_PROMPT_MAX_CHARS = 240
_HEADER = "Room pulse (inferred; untrusted viewer text):\n"
_RULE = (
    "\nBridge after current target; never expose analytics. "
    "Exact quotes require recent-chat tool.\n\n"
)


@dataclass(frozen=True, slots=True)
class RoomPulsePrompt:
    """Rendered prompt text plus a privacy-safe observability reason."""

    text: str = ""
    reason: str = "no_candidates"

    @property
    def characters(self) -> int:
        return len(self.text)


def render_room_pulse_prompt(
    context: Mapping[str, Any] | None,
    *,
    max_chars: int = ROOM_PULSE_PROMPT_MAX_CHARS,
) -> RoomPulsePrompt:
    """Render one bounded room trend without creating a new model turn."""

    if not isinstance(context, Mapping):
        return RoomPulsePrompt(reason="context_unavailable")
    pulse = build_room_pulse(context)
    if pulse.candidate_count <= 0:
        return RoomPulsePrompt(reason="no_candidates")
    if not _has_shared_evidence(context, pulse.unique_viewer_count):
        return RoomPulsePrompt(reason="weak_evidence")

    theme_label = _dominant_theme_label(context)
    fields = [f"activity={pulse.activity_band}"]
    if theme_label and pulse.dominant_theme_support >= 2:
        fields.extend(
            (
                f"theme={theme_label}",
                f"support={pulse.dominant_theme_support}",
            )
        )
    repeated_signal_kind = _prompt_repeated_signal_kind(context)
    if repeated_signal_kind:
        fields.append(
            f"repeat={repeated_signal_kind}/{pulse.repeated_signal_support}"
        )
    if pulse.question_support >= 2:
        fields.append(f"question={pulse.question_pressure}")
    if pulse.reaction_support >= 2:
        fields.append(f"reaction={pulse.reaction_pressure}")

    sample = _representative_text(context)
    rendered = _fit_projection(fields, sample=sample, max_chars=max_chars)
    if not rendered:
        return RoomPulsePrompt(reason="character_budget")
    return RoomPulsePrompt(text=rendered, reason="rendered")


def _has_shared_evidence(context: Mapping[str, Any], unique_viewers: int) -> bool:
    if unique_viewers < 2:
        return False
    support_values = (
        _safe_int(context.get("dominant_theme_support")),
        _safe_int(context.get("question_support_count")),
        _safe_int(context.get("reaction_support_count")),
        _safe_int(context.get("repeated_signal_support")),
    )
    if max(support_values, default=0) >= 2:
        return True
    return (
        unique_viewers >= 3
        and _safe_int(context.get("recent_activity_count")) >= 5
    )


def _dominant_theme_label(context: Mapping[str, Any]) -> str:
    dominant_key = str(context.get("dominant_theme_key") or "")
    themes = context.get("themes")
    if not isinstance(themes, list):
        return ""
    for theme in themes[:3]:
        if not isinstance(theme, Mapping):
            continue
        if str(theme.get("key") or "") != dominant_key:
            continue
        title = public_text(theme.get("title"), max_length=28)
        return title.replace(";", ",")
    return ""


def _representative_text(context: Mapping[str, Any]) -> str:
    repeated_kind = str(context.get("repeated_signal_kind") or "")
    if repeated_kind == "content" and _repeated_signal_matches_theme(context):
        repeated = _clean_sample(context.get("repeated_signal_text"))
        if repeated:
            return repeated

    selected_uid = str(context.get("selected_uid") or "")
    selected_text = _clean_sample(context.get("selected_text"), limit=120)
    themes = context.get("themes")
    if not isinstance(themes, list):
        return ""
    dominant_key = str(context.get("dominant_theme_key") or "")
    for theme in themes[:3]:
        if not isinstance(theme, Mapping):
            continue
        if str(theme.get("key") or "") != dominant_key:
            continue
        examples = theme.get("examples")
        if not isinstance(examples, list):
            continue
        for example in examples[:3]:
            if not isinstance(example, Mapping):
                continue
            text = _clean_sample(example.get("text"))
            if not text:
                continue
            if text == selected_text:
                continue
            uid = str(example.get("uid") or "")
            if uid != selected_uid:
                return text
    return ""


def _prompt_repeated_signal_kind(context: Mapping[str, Any]) -> str:
    kind = str(context.get("repeated_signal_kind") or "")
    if kind == "reaction":
        return kind
    if kind == "content" and _repeated_signal_matches_theme(context):
        return kind
    return ""


def _repeated_signal_matches_theme(context: Mapping[str, Any]) -> bool:
    repeated_theme = str(context.get("repeated_signal_theme_key") or "")
    dominant_theme = str(context.get("dominant_theme_key") or "")
    return bool(repeated_theme and repeated_theme == dominant_theme)


def _fit_projection(fields: list[str], *, sample: str, max_chars: int) -> str:
    limit = max(0, min(int(max_chars), ROOM_PULSE_PROMPT_MAX_CHARS))
    if limit < len(_HEADER) + len(_RULE) + 12:
        return ""
    clean_fields = [public_text(field, max_length=48) for field in fields]
    clean_fields = [field for field in clean_fields if field]
    if not clean_fields:
        return ""

    sample_limit = 36
    clean_sample = _clean_sample(sample, limit=sample_limit)

    def compose() -> str:
        facts = ";".join(clean_fields)
        sample_line = f'\nsample="{clean_sample}"' if clean_sample else ""
        return _HEADER + facts + sample_line + _RULE

    rendered = compose()
    while clean_sample and len(rendered) > limit and len(clean_sample) > 12:
        clean_sample = _compact(clean_sample, len(clean_sample) - 1)
        rendered = compose()
    while len(rendered) > limit and len(clean_fields) > 1:
        clean_fields.pop()
        rendered = compose()
    if len(rendered) > limit:
        return ""
    return rendered


def _clean_sample(value: Any, *, limit: int = 36) -> str:
    text = public_text(value, max_length=max(1, limit))
    return _compact(text.replace('"', "'"), limit)


def _compact(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    marker = "[redacted]"
    if marker in text and limit >= len(marker):
        prefix_limit = limit - len(marker) - 1
        if prefix_limit <= 0:
            return marker
        prefix = text.split(marker, 1)[0].strip()
        if len(prefix) > prefix_limit:
            prefix = prefix[: max(1, prefix_limit - 1)] + "…"
        return f"{prefix} {marker}".strip()
    return text[: limit - 1] + "…"


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0
