"""Live and viewer event contracts for NEKO Live routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts_public import public_int, public_text, public_value
from .contracts_types import LiveMode, TriggerSource, utc_now_iso


@dataclass
class ViewerEvent:
    uid: str
    nickname: str = ""
    avatar_url: str = ""
    danmaku_text: str = ""
    target_lanlan: str = ""
    source: TriggerSource = "developer_sandbox"
    live_mode: LiveMode = "co_stream"
    trace_id: str = ""
    seen_at: str = field(default_factory=utc_now_iso)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "uid": public_text(self.uid),
            "nickname": public_text(self.nickname),
            "avatar_url": public_text(self.avatar_url),
            "danmaku_text": public_text(self.danmaku_text),
            "target_lanlan": public_text(self.target_lanlan),
            "source": public_text(self.source),
            "live_mode": public_text(self.live_mode),
            "trace_id": public_text(self.trace_id),
            "seen_at": public_text(self.seen_at),
        }
        if isinstance(self.raw, dict):
            event_type = public_text(self.raw.get("event_type"))
            if event_type:
                data["event_type"] = event_type
            gift_name = public_text(self.raw.get("gift_name"), max_len=80)
            if gift_name:
                data["gift_name"] = gift_name
                data["support_gift_name"] = gift_name
            gift_coin_type = public_text(self.raw.get("gift_coin_type"), max_len=80)
            if gift_coin_type:
                data["support_gift_coin_type"] = gift_coin_type
            for raw_key, public_key in (
                ("gift_count", "gift_count"),
                ("gift_value", "gift_value"),
                ("gift_num", "support_gift_num"),
                ("gift_total_coin", "support_gift_total_coin"),
                ("gift_price", "support_gift_price"),
                ("guard_level", "support_guard_level"),
            ):
                value = public_int(self.raw.get(raw_key), default=0, minimum=0)
                if value:
                    data[public_key] = value
            topic = self.raw.get("topic_material")
            if isinstance(topic, dict):
                for raw_key, public_key in (
                    ("source", "topic_source"),
                    ("shape", "topic_shape"),
                    ("title", "topic_title"),
                    ("key", "topic_key"),
                    ("family", "topic_family"),
                    ("fun_axis", "topic_fun_axis"),
                    ("hook", "topic_hook"),
                    ("pattern", "topic_pattern"),
                    ("intent", "topic_intent"),
                    ("live_column", "topic_live_column"),
                    ("topic_pack", "topic_pack"),
                    ("reply_affordance", "topic_reply_affordance"),
                    ("recent_topic_skip_reason", "topic_recent_skip_reason"),
                ):
                    value = public_text(topic.get(raw_key))
                    if value:
                        data[public_key] = value
            host_beat = self.raw.get("host_beat")
            if isinstance(host_beat, dict):
                for raw_key, public_key in (
                    ("key", "host_beat_key"),
                    ("shape", "host_beat_shape"),
                    ("family", "host_beat_family"),
                    ("fun_axis", "host_beat_fun_axis"),
                    ("title", "host_beat_title"),
                    ("hint", "host_beat_hint"),
                    ("live_column", "host_beat_live_column"),
                    ("idle_stage", "host_beat_idle_stage"),
                    ("reply_affordance", "host_beat_reply_affordance"),
                ):
                    value = public_text(host_beat.get(raw_key))
                    if value:
                        data[public_key] = value
        return data


@dataclass
class LiveEvent:
    """Lightweight envelope routed by EventBus using ``type``."""

    type: str
    uid: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "live"
    ts: float = 0.0
    schema_version: int = 1
    raw: Any = None

    def to_dict(self) -> dict[str, Any]:
        # Keep raw out of lightweight debug/status projections.
        return {
            "type": public_text(self.type),
            "uid": public_text(self.uid),
            "payload": public_value(self.payload),
            "source": public_text(self.source),
            "ts": _public_timestamp(self.ts),
            "schema_version": public_int(self.schema_version, default=1, minimum=1),
        }


def _public_timestamp(value: Any) -> float:
    projected = public_value(value)
    if isinstance(projected, bool) or not isinstance(projected, (int, float)):
        return 0.0
    return float(projected)
