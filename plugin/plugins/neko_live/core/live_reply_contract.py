"""Plugin-owned NEKO Live reply contract declarations.

This module is intentionally independent from ``main_logic``. It describes
what the plugin wants from an eventual generic host output boundary, while the
current host may simply treat the metadata as opaque.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


REPLY_CONTRACT_NAME = "short_tts_line"
PLUGIN_OUTPUT_POLICY_KEY = "neko_roast_output_policy"
REPLY_TARGET_CHARS = 14
DEFAULT_DISPATCH_REPLY_CHARS = 28
DANMAKU_EXPANDED_REPLY_CHARS = 56
DANMAKU_ROOM_BRIDGE_REPLY_CHARS = 48
DISPATCH_REPLY_CHAR_LIMITS = {
    "avatar_roast": 32,
    "danmaku_response": 28,
    "live_support_events": 32,
    "warmup_hosting": 56,
    "idle_hosting": 64,
    "active_engagement": 72,
}
ROUTE_CEILINGS = dict(DISPATCH_REPLY_CHAR_LIMITS)
HOST_MODULES = {"warmup_hosting", "idle_hosting", "active_engagement"}
EXPANDED_REPLY_MODE = "expanded"
ROOM_BRIDGE_REPLY_MODE = "room_bridge"


@dataclass(frozen=True, slots=True)
class LiveReplyContract:
    """A plugin-side output request that can later map to a generic host API."""

    uid: str
    live_mode: str
    response_module_hint: str
    max_reply_chars: int
    reply_contract: str = REPLY_CONTRACT_NAME
    plugin: str = "neko_roast"
    demo: bool = False

    def to_metadata(self) -> dict[str, Any]:
        policy = build_plugin_output_policy(
            response_module_hint=self.response_module_hint,
            max_reply_chars=self.max_reply_chars,
        )
        return {
            "plugin": self.plugin,
            "uid": self.uid,
            "live_mode": self.live_mode,
            "demo": bool(self.demo),
            "live_reply_contract": self.reply_contract,
            "max_reply_chars": int(self.max_reply_chars),
            "response_module_hint": self.response_module_hint,
            PLUGIN_OUTPUT_POLICY_KEY: policy,
        }


def max_reply_chars_for_module(module: str) -> int:
    return DISPATCH_REPLY_CHAR_LIMITS.get(str(module or "").strip(), DEFAULT_DISPATCH_REPLY_CHARS)


def build_plugin_output_policy(*, response_module_hint: str, max_reply_chars: int) -> dict[str, Any]:
    """Return plugin-owned speech policy metadata for observability.

    The host treats this as opaque metadata. It must not imply that host/core
    owns NEKO Live wording, shaping, repeat suppression, or memory isolation.
    """
    module = str(response_module_hint or "").strip()
    return {
        "owner": "neko_roast",
        "host_role": "opaque_transport",
        "speech_strategy": "plugin_prompt_contract",
        "response_module_hint": module,
        "max_reply_chars": int(max_reply_chars),
        "recent_output_scope": "plugin_recent_live_outputs",
    }


def build_live_reply_contract(
    *,
    uid: str,
    live_mode: str,
    response_module_hint: str,
    demo: bool = False,
) -> LiveReplyContract:
    module = str(response_module_hint or "").strip()
    return LiveReplyContract(
        uid=str(uid or ""),
        live_mode=str(live_mode or ""),
        response_module_hint=module,
        max_reply_chars=max_reply_chars_for_module(module),
        demo=bool(demo),
    )


def build_reply_metadata(
    *,
    uid: str,
    live_mode: str,
    response_module_hint: str,
    demo: bool = False,
) -> dict[str, Any]:
    return build_live_reply_contract(
        uid=uid,
        live_mode=live_mode,
        response_module_hint=response_module_hint,
        demo=demo,
    ).to_metadata()


def is_live_reply_metadata(metadata: Mapping[str, Any] | None) -> bool:
    return isinstance(metadata, Mapping) and metadata.get("live_reply_contract") == REPLY_CONTRACT_NAME


def response_module(metadata: Mapping[str, Any] | None) -> str:
    if not isinstance(metadata, Mapping):
        return ""
    value = metadata.get("response_module_hint")
    return value.strip() if isinstance(value, str) else ""


def reply_length_mode(metadata: Mapping[str, Any] | None) -> str:
    if not isinstance(metadata, Mapping):
        return ""
    value = metadata.get("reply_length_mode")
    return value.strip() if isinstance(value, str) else ""


def is_expanded_danmaku_reply(metadata: Mapping[str, Any] | None) -> bool:
    return (
        response_module(metadata) == "danmaku_response"
        and reply_length_mode(metadata) == EXPANDED_REPLY_MODE
    )


def is_room_bridge_danmaku_reply(metadata: Mapping[str, Any] | None) -> bool:
    return (
        response_module(metadata) == "danmaku_response"
        and reply_length_mode(metadata) == ROOM_BRIDGE_REPLY_MODE
    )


def is_longer_danmaku_reply(metadata: Mapping[str, Any] | None) -> bool:
    return is_expanded_danmaku_reply(metadata) or is_room_bridge_danmaku_reply(metadata)


def route_ceiling_for_metadata(metadata: Mapping[str, Any] | None) -> int | None:
    if is_expanded_danmaku_reply(metadata):
        return DANMAKU_EXPANDED_REPLY_CHARS
    if is_room_bridge_danmaku_reply(metadata):
        return DANMAKU_ROOM_BRIDGE_REPLY_CHARS
    return ROUTE_CEILINGS.get(response_module(metadata))


def coerce_live_reply_limit(value: Any) -> int | None:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return None
    if limit <= 0:
        return None
    return min(limit, 80)


def reply_limit_from_metadata(metadata: Mapping[str, Any] | None) -> int | None:
    if not is_live_reply_metadata(metadata):
        return None
    metadata_limit = coerce_live_reply_limit((metadata or {}).get("max_reply_chars"))
    module_limit = route_ceiling_for_metadata(metadata)
    candidates = [value for value in (metadata_limit, module_limit) if value]
    return min(candidates) if candidates else None
