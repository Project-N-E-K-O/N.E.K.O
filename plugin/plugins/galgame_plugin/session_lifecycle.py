from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import AbstractSet, Any, Literal

from .models import DATA_SOURCE_MEMORY_READER, DATA_SOURCE_OCR_READER


SESSION_ORIGIN_CURRENT_RUN = "current_run"
SESSION_ORIGIN_PREEXISTING = "preexisting"
SessionOrigin = Literal["current_run", "preexisting"]
SessionIdentity = tuple[str, str, str]

_SNAPSHOT_STATE_EVENT_TYPES = frozenset(
    {
        "session_started",
        "screen_classified",
        "line_observed",
        "line_changed",
        "choices_shown",
        "choice_selected",
        "scene_changed",
        "save_loaded",
    }
)


def _parse_session_started_at(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return float(parsed.timestamp())
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def session_identity_key(
    *,
    data_source: str,
    game_id: str,
    session_id: str,
) -> SessionIdentity:
    return (
        str(data_source or "").strip(),
        str(game_id or "").strip(),
        str(session_id or "").strip(),
    )


def classify_session_origin(
    *,
    data_source: str,
    game_id: str = "",
    session_id: str,
    started_at: str,
    plugin_run_started_at: float,
    memory_reader_session_id: str = "",
    ocr_reader_session_id: str = "",
    startup_existing_session_ids: AbstractSet[str | SessionIdentity] | None = None,
) -> SessionOrigin:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return SESSION_ORIGIN_PREEXISTING

    normalized_source = str(data_source or "").strip()
    if (
        normalized_source == DATA_SOURCE_MEMORY_READER
        and str(memory_reader_session_id or "").strip() == normalized_session_id
    ):
        return SESSION_ORIGIN_CURRENT_RUN
    if (
        normalized_source == DATA_SOURCE_OCR_READER
        and str(ocr_reader_session_id or "").strip() == normalized_session_id
    ):
        return SESSION_ORIGIN_CURRENT_RUN

    if startup_existing_session_ids is not None:
        startup_identity = session_identity_key(
            data_source=normalized_source,
            game_id=game_id,
            session_id=normalized_session_id,
        )
        if (
            startup_identity in startup_existing_session_ids
            or normalized_session_id in startup_existing_session_ids
        ):
            return SESSION_ORIGIN_PREEXISTING

    started_at_timestamp = _parse_session_started_at(started_at)
    if started_at_timestamp is None:
        return SESSION_ORIGIN_PREEXISTING
    try:
        run_started_at = float(plugin_run_started_at)
    except (TypeError, ValueError, OverflowError):
        return SESSION_ORIGIN_PREEXISTING
    if started_at_timestamp > run_started_at:
        return SESSION_ORIGIN_CURRENT_RUN
    return SESSION_ORIGIN_PREEXISTING


def event_releases_empty_snapshot_gate(event: object) -> bool:
    if not isinstance(event, Mapping):
        return False
    return str(event.get("type") or "") in _SNAPSHOT_STATE_EVENT_TYPES
