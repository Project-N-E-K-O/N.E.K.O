"""Configuration contracts for NEKO Live runtime behavior."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any

from .contracts_public import public_bool, public_text
from .contracts_types import ActivityLevel, LiveMode, RoastStrength


_LIVE_ROOM_URL_RE = re.compile(
    r"live\.bilibili\.com/(?:h5/|blanc/)?(\d+)", re.IGNORECASE
)


def parse_room_id(value: Any) -> int:
    """Parse a numeric room id or a Bilibili live-room URL; return 0 on failure."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if not isinstance(value, str):
        return 0
    text = value.strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    match = _LIVE_ROOM_URL_RE.search(text)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return 0
    return 0


def normalize_live_platform(value: Any) -> str:
    platform = value.strip().lower() if isinstance(value, str) else "bilibili"
    if platform in {"bili", "bilibili"}:
        return "bilibili"
    if platform in {"douyin", "dy"}:
        return "douyin"
    return "bilibili"


@dataclass
class RoastConfig:
    live_platform: str = "bilibili"
    live_room_ref: str = ""
    live_room_id: int = 0
    live_mode: LiveMode = "co_stream"
    live_enabled: bool = False
    avatar_roast_enabled: bool = True
    avatar_analysis_enabled: bool = True
    danmaku_response_enabled: bool = True
    live_support_events_enabled: bool = True
    warmup_hosting_enabled: bool = True
    idle_hosting_enabled: bool = True
    active_engagement_enabled: bool = True
    developer_tools_enabled: bool = False
    dry_run: bool = False  # Run the full pipeline without pushing output to NEKO.
    roast_once_per_uid: bool = True
    viewer_memory_enabled: bool = True
    roast_strength: RoastStrength = "normal"
    activity_level: ActivityLevel = "standard"
    co_stream_output_policy: str = "auto_low_interrupt"
    solo_output_policy: str = "auto_rate_limited"
    avatar_fetch_timeout_seconds: float = 8.0
    recent_limit: int = 30
    rate_limit_seconds: int = 20
    queue_limit: int = 5
    safety_auto_stop_enabled: bool = True
    safety_window_seconds: int = 60
    safety_pipeline_failure_limit: int = 3
    safety_output_failure_limit: int = 2
    safety_queue_overflow_limit: int = 3
    viewer_store_dir: str = ""  # Empty means the plugin data directory.
    stream_theme: str = ""
    stream_goal: str = ""
    stream_columns: str = ""
    stream_avoid_topics: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "RoastConfig":
        raw = dict(data or {})
        live_mode = _safe_text(raw.get("live_mode"), default="co_stream")
        if live_mode == "solo":
            live_mode = "solo_stream"
        if live_mode not in {"co_stream", "solo_stream"}:
            live_mode = "co_stream"
        roast_strength = _safe_text(raw.get("roast_strength"), default="normal")
        if roast_strength not in {"gentle", "normal", "sharp"}:
            roast_strength = "normal"
        activity_level = _safe_text(raw.get("activity_level"), default="standard")
        if activity_level not in {"quiet", "standard", "active"}:
            activity_level = "standard"
        live_platform = normalize_live_platform(raw.get("live_platform"))
        live_room_ref = raw.get("live_room_ref").strip() if isinstance(raw.get("live_room_ref"), str) else ""
        live_room_id = parse_room_id(raw.get("live_room_id"))
        if live_platform == "bilibili":
            if live_room_id <= 0 and live_room_ref:
                live_room_id = parse_room_id(live_room_ref)
            if not live_room_ref and live_room_id > 0:
                live_room_ref = str(live_room_id)
        else:
            live_room_id = 0
        return cls(
            live_platform=live_platform,
            live_room_ref=live_room_ref,
            live_room_id=live_room_id,
            live_mode=live_mode,  # type: ignore[arg-type]
            live_enabled=_safe_bool(raw.get("live_enabled"), default=False),
            avatar_roast_enabled=_safe_bool(raw.get("avatar_roast_enabled"), default=True),
            avatar_analysis_enabled=_safe_bool(raw.get("avatar_analysis_enabled"), default=True),
            danmaku_response_enabled=_safe_bool(raw.get("danmaku_response_enabled"), default=True),
            live_support_events_enabled=_safe_bool(raw.get("live_support_events_enabled"), default=True),
            warmup_hosting_enabled=_safe_bool(raw.get("warmup_hosting_enabled"), default=True),
            idle_hosting_enabled=_safe_bool(raw.get("idle_hosting_enabled"), default=True),
            active_engagement_enabled=_safe_bool(raw.get("active_engagement_enabled"), default=True),
            developer_tools_enabled=_safe_bool(raw.get("developer_tools_enabled"), default=False),
            dry_run=_safe_bool(raw.get("dry_run"), default=False),
            roast_once_per_uid=_safe_bool(raw.get("roast_once_per_uid"), default=True),
            viewer_memory_enabled=_safe_bool(
                raw.get("viewer_memory_enabled"), default=True
            ),
            roast_strength=roast_strength,  # type: ignore[arg-type]
            activity_level=activity_level,  # type: ignore[arg-type]
            co_stream_output_policy=_safe_text(
                raw.get("co_stream_output_policy"),
                default="auto_low_interrupt",
            ),
            solo_output_policy=_safe_text(
                raw.get("solo_output_policy"),
                default="auto_rate_limited",
            ),
            avatar_fetch_timeout_seconds=_safe_float(
                raw.get("avatar_fetch_timeout_seconds"),
                default=8.0,
                minimum=0.0,
            ),
            recent_limit=_safe_int(raw.get("recent_limit"), default=30, minimum=1, maximum=200),
            rate_limit_seconds=_safe_int(
                raw.get("rate_limit_seconds"),
                default=20,
                minimum=0,
                maximum=3600,
            ),
            queue_limit=_safe_int(raw.get("queue_limit"), default=5, minimum=1, maximum=100),
            safety_auto_stop_enabled=_safe_bool(raw.get("safety_auto_stop_enabled"), default=True),
            safety_window_seconds=_safe_int(
                raw.get("safety_window_seconds"),
                default=60,
                minimum=5,
                maximum=3600,
            ),
            safety_pipeline_failure_limit=_safe_int(
                raw.get("safety_pipeline_failure_limit"),
                default=3,
                minimum=1,
                maximum=100,
            ),
            safety_output_failure_limit=_safe_int(
                raw.get("safety_output_failure_limit"),
                default=2,
                minimum=1,
                maximum=100,
            ),
            safety_queue_overflow_limit=_safe_int(
                raw.get("safety_queue_overflow_limit"),
                default=3,
                minimum=1,
                maximum=100,
            ),
            viewer_store_dir=(
                raw.get("viewer_store_dir").strip()
                if isinstance(raw.get("viewer_store_dir"), str)
                else ""
            ),
            stream_theme=_safe_optional_text(raw.get("stream_theme"), max_len=120),
            stream_goal=_safe_optional_text(raw.get("stream_goal"), max_len=160),
            stream_columns=_safe_optional_text(raw.get("stream_columns"), max_len=160),
            stream_avoid_topics=_safe_optional_text(raw.get("stream_avoid_topics"), max_len=160),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_public_dict(self) -> dict[str, Any]:
        live_platform = normalize_live_platform(self.live_platform)
        live_room_ref = public_text(self.live_room_ref)
        live_room_id = parse_room_id(self.live_room_id)
        if live_platform == "bilibili":
            if live_room_id <= 0 and live_room_ref:
                live_room_id = parse_room_id(live_room_ref)
            if not live_room_ref and live_room_id > 0:
                live_room_ref = str(live_room_id)
        else:
            live_room_id = 0
        return {
            "live_platform": live_platform,
            "live_room_ref": live_room_ref,
            "live_room_id": live_room_id,
            "live_mode": self.live_mode if isinstance(self.live_mode, str) and self.live_mode in {"co_stream", "solo_stream"} else "co_stream",
            "live_enabled": public_bool(self.live_enabled),
            "avatar_roast_enabled": public_bool(self.avatar_roast_enabled, default=True),
            "avatar_analysis_enabled": public_bool(self.avatar_analysis_enabled, default=True),
            "danmaku_response_enabled": public_bool(self.danmaku_response_enabled, default=True),
            "live_support_events_enabled": public_bool(self.live_support_events_enabled, default=True),
            "warmup_hosting_enabled": public_bool(self.warmup_hosting_enabled, default=True),
            "idle_hosting_enabled": public_bool(self.idle_hosting_enabled, default=True),
            "active_engagement_enabled": public_bool(self.active_engagement_enabled, default=True),
            "developer_tools_enabled": public_bool(self.developer_tools_enabled),
            "dry_run": public_bool(self.dry_run, default=False),
            "roast_once_per_uid": public_bool(self.roast_once_per_uid, default=True),
            "viewer_memory_enabled": public_bool(
                self.viewer_memory_enabled, default=True
            ),
            "roast_strength": self.roast_strength if isinstance(self.roast_strength, str) and self.roast_strength in {"gentle", "normal", "sharp"} else "normal",
            "activity_level": self.activity_level if isinstance(self.activity_level, str) and self.activity_level in {"quiet", "standard", "active"} else "standard",
            "co_stream_output_policy": public_text(self.co_stream_output_policy) or "auto_low_interrupt",
            "solo_output_policy": public_text(self.solo_output_policy) or "auto_rate_limited",
            "avatar_fetch_timeout_seconds": _safe_float(
                self.avatar_fetch_timeout_seconds,
                default=8.0,
                minimum=0.0,
            ),
            "recent_limit": _safe_int(self.recent_limit, default=30, minimum=1, maximum=200),
            "rate_limit_seconds": _safe_int(self.rate_limit_seconds, default=20, minimum=0, maximum=3600),
            "queue_limit": _safe_int(self.queue_limit, default=5, minimum=1, maximum=100),
            "safety_auto_stop_enabled": public_bool(self.safety_auto_stop_enabled, default=True),
            "safety_window_seconds": _safe_int(self.safety_window_seconds, default=60, minimum=5, maximum=3600),
            "safety_pipeline_failure_limit": _safe_int(
                self.safety_pipeline_failure_limit,
                default=3,
                minimum=1,
                maximum=100,
            ),
            "safety_output_failure_limit": _safe_int(
                self.safety_output_failure_limit,
                default=2,
                minimum=1,
                maximum=100,
            ),
            "safety_queue_overflow_limit": _safe_int(
                self.safety_queue_overflow_limit,
                default=3,
                minimum=1,
                maximum=100,
            ),
            "viewer_store_dir": public_text(self.viewer_store_dir),
            "stream_theme": public_text(self.stream_theme, max_len=120),
            "stream_goal": public_text(self.stream_goal, max_len=160),
            "stream_columns": public_text(self.stream_columns, max_len=160),
            "stream_avoid_topics": public_text(self.stream_avoid_topics, max_len=160),
        }


def _safe_text(value: Any, *, default: str) -> str:
    if not isinstance(value, str):
        return default
    text = value.strip()
    return text if text else default


def _safe_optional_text(value: Any, *, max_len: int) -> str:
    if not isinstance(value, str):
        return ""
    return public_text(value.strip(), max_len=max_len)


def _safe_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        if value == 1:
            return True
        if value == 0:
            return False
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off"}:
            return False
    return default


def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    number: int
    if isinstance(value, bool):
        number = default
    elif isinstance(value, int):
        number = value
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        number = int(value.strip())
    else:
        number = default
    return max(minimum, min(number, maximum))


def _safe_float(value: Any, *, default: float, minimum: float) -> float:
    number: float
    if isinstance(value, bool):
        number = default
    elif isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            number = default
    else:
        number = default
    if not math.isfinite(number):
        number = default
    return max(minimum, number)
