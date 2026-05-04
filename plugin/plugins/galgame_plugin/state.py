from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import ADVANCE_SPEED_MEDIUM, DATA_SOURCE_NONE, MODE_COMPANION, STATE_IDLE


@dataclass(slots=True)
class GalgameSharedState:
    bound_game_id: str = ""
    available_game_ids: list[str] = field(default_factory=list)
    mode: str = MODE_COMPANION
    push_notifications: bool = True
    advance_speed: str = ADVANCE_SPEED_MEDIUM
    active_game_id: str = ""
    active_session_id: str = ""
    active_session_meta: dict[str, Any] = field(default_factory=dict)
    active_data_source: str = DATA_SOURCE_NONE
    latest_snapshot: dict[str, Any] = field(default_factory=dict)
    history_events: list[dict[str, Any]] = field(default_factory=list)
    history_lines: list[dict[str, Any]] = field(default_factory=list)
    history_observed_lines: list[dict[str, Any]] = field(default_factory=list)
    history_choices: list[dict[str, Any]] = field(default_factory=list)
    screen_type: str = ""
    screen_ui_elements: list[dict[str, Any]] = field(default_factory=list)
    screen_confidence: float = 0.0
    screen_debug: dict[str, Any] = field(default_factory=dict)
    dedupe_window: list[dict[str, str]] = field(default_factory=list)
    line_buffer: bytes = b""
    stream_reset_pending: bool = False
    last_error: dict[str, Any] = field(default_factory=dict)
    next_poll_at_monotonic: float = 0.0
    current_connection_state: str = STATE_IDLE
    events_byte_offset: int = 0
    events_file_size: int = 0
    last_seq: int = 0
    last_seen_data_monotonic: float = 0.0
    warmup_session_id: str = ""
    memory_reader_runtime: dict[str, Any] = field(default_factory=dict)
    memory_reader_target: dict[str, Any] = field(default_factory=dict)
    ocr_reader_runtime: dict[str, Any] = field(default_factory=dict)
    ocr_capture_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    ocr_window_target: dict[str, Any] = field(default_factory=dict)
    plugin_error: str = ""


def build_initial_state(
    *,
    mode: str,
    push_notifications: bool,
    advance_speed: str = ADVANCE_SPEED_MEDIUM,
) -> GalgameSharedState:
    return GalgameSharedState(
        mode=mode,
        push_notifications=push_notifications,
        advance_speed=advance_speed,
    )
