"""Adapter between NEKO Live requests and host-visible metadata.

The bridge is deliberately metadata-only for now. It gives the plugin a single
place to connect to a future generic host output-contract API without teaching
the host about NEKO Live internals today.
"""

from __future__ import annotations

from typing import Any

from ..core.contracts import InteractionRequest
from ..core.live_reply_contract import (
    DANMAKU_EXPANDED_REPLY_CHARS,
    DANMAKU_ROOM_BRIDGE_REPLY_CHARS,
    EXPANDED_REPLY_MODE,
    LiveReplyContract,
    PLUGIN_OUTPUT_POLICY_KEY,
    ROOM_BRIDGE_REPLY_MODE,
    build_plugin_output_policy,
    build_live_reply_contract,
    max_reply_chars_for_module,
)


def response_module_hint(request: InteractionRequest) -> str:
    support_event_type = request.metadata.get("support_event_type")
    if isinstance(support_event_type, str) and support_event_type.strip():
        return "live_support_events"
    source = str(request.event.source or "")
    if source in {"warmup_hosting", "idle_hosting", "active_engagement"}:
        return source
    if source in {"live_danmaku", "manual_live_simulation"}:
        return "avatar_roast" if request.allow_avatar_image else "danmaku_response"
    if source == "developer_sandbox":
        return "developer_sandbox"
    return source or "unknown"


_EXPANDED_DANMAKU_MARKERS = (
    "\u8bb2\u4e2a\u7b11\u8bdd",
    "\u8bb2\u7b11\u8bdd",
    "\u7b11\u8bdd",
    "\u6765\u4e2a\u7b11\u8bdd",
    "\u6765\u4e00\u4e2a",
    "\u8bb2\u8bb2",
    "\u8be6\u7ec6",
    "\u5c55\u5f00",
    "\u8bf4\u8bf4",
    "\u89e3\u91ca",
    "\u600e\u4e48\u770b",
    "\u4e3a\u4ec0\u4e48",
    "\u4e3a\u5565",
    "\u600e\u4e48\u529e",
    "\u8d77\u4e2a\u5916\u53f7",
    "\u8d77\u5916\u53f7",
    "\u5410\u69fd",
    "\u9510\u8bc4",
    "\u8bc4\u4ef7",
    "\u635f\u4e00\u4e0b",
    "\u8c03\u4f83",
    "\u7f16\u4e00\u4e2a",
    "\u6765\u4e00\u6bb5",
    "tell me a joke",
    "joke",
    "why",
    "explain",
    "say more",
)


def wants_expanded_danmaku_reply(request: InteractionRequest) -> bool:
    if response_module_hint(request) != "danmaku_response":
        return False
    text = str(request.event.danmaku_text or "").casefold()
    dense = "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    return any(marker.casefold() in text or marker.casefold() in dense for marker in _EXPANDED_DANMAKU_MARKERS)


def wants_room_bridge_danmaku_reply(request: InteractionRequest) -> bool:
    if response_module_hint(request) != "danmaku_response":
        return False
    value = request.metadata.get("reply_length_mode")
    return isinstance(value, str) and value.strip() == ROOM_BRIDGE_REPLY_MODE


def contract_for_request(request: InteractionRequest, *, demo: bool = False) -> LiveReplyContract:
    module = response_module_hint(request)
    return build_live_reply_contract(
        uid=request.identity.uid,
        live_mode=request.live_mode,
        response_module_hint=module,
        demo=demo,
    )


def max_reply_chars_for_request(request: InteractionRequest) -> int:
    if wants_expanded_danmaku_reply(request):
        return DANMAKU_EXPANDED_REPLY_CHARS
    if wants_room_bridge_danmaku_reply(request):
        return DANMAKU_ROOM_BRIDGE_REPLY_CHARS
    return max_reply_chars_for_module(response_module_hint(request))


def metadata_for_request(
    request: InteractionRequest,
    *,
    demo: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = contract_for_request(request, demo=demo).to_metadata()
    trace_id = str(getattr(request.event, "trace_id", "") or "").strip()
    if trace_id:
        metadata["trace_id"] = trace_id
    source = str(getattr(request.event, "source", "") or "").strip()
    if source:
        metadata["live_event_source"] = source
    if wants_expanded_danmaku_reply(request):
        metadata["reply_length_mode"] = EXPANDED_REPLY_MODE
        metadata["max_reply_chars"] = DANMAKU_EXPANDED_REPLY_CHARS
    elif wants_room_bridge_danmaku_reply(request):
        metadata["reply_length_mode"] = ROOM_BRIDGE_REPLY_MODE
        metadata["max_reply_chars"] = DANMAKU_ROOM_BRIDGE_REPLY_CHARS
    for key in (
        "danmaku_profile",
        "danmaku_reply_target",
        "danmaku_reply_shape",
        "danmaku_anchor_hint",
        "danmaku_viewer_nickname",
        "danmaku_target_viewer_nickname",
        "viewer_claimed_support",
        "room_theme",
        "meme_hint_ids",
        "meme_hint_tags",
    ):
        value = request.metadata.get(key)
        if isinstance(value, str) and value.strip():
            metadata[key] = value.strip()
    if extra:
        metadata.update(extra)
    _sync_plugin_output_policy(metadata)
    return metadata


def _sync_plugin_output_policy(metadata: dict[str, Any]) -> None:
    module = response_module_hint_from_metadata(metadata)
    try:
        max_reply_chars = int(metadata.get("max_reply_chars") or 0)
    except (TypeError, ValueError):
        max_reply_chars = 0
    if not module or max_reply_chars <= 0:
        return
    metadata[PLUGIN_OUTPUT_POLICY_KEY] = build_plugin_output_policy(
        response_module_hint=module,
        max_reply_chars=max_reply_chars,
    )


def response_module_hint_from_metadata(metadata: dict[str, Any]) -> str:
    value = metadata.get("response_module_hint")
    return value.strip() if isinstance(value, str) else ""
