"""Role-scoped LLM tool lifecycle and read-only recent-chat result projection."""

from __future__ import annotations

from typing import Any

from ...adapters.neko_dispatcher import resolve_plugin_target_lanlan
from .provider_event import event_room_ref, public_text
from .recent_chat_relevance import clean_relevance_query


TOOL_NAME = "get_recent_live_chat"
TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "maxLength": 80,
            "description": (
                "可选的当前具体话题。提供后只在低压状态下返回一条相关、未回复且未接过的近期弹幕；"
                "不要传整段对话，也不要为了轮询弹幕而调用。"
            ),
        },
    },
}


def is_recent_chat_tool_registered(plugin: Any) -> bool:
    return any(item.get("name") == TOOL_NAME for item in plugin.list_llm_tools())


def set_recent_chat_tool_enabled(plugin: Any, enabled: bool) -> bool:
    existing = next(
        (item for item in plugin.list_llm_tools() if item.get("name") == TOOL_NAME),
        None,
    )
    if not enabled:
        return plugin.unregister_llm_tool(TOOL_NAME) if existing else False

    role = resolve_plugin_target_lanlan(plugin)
    if not role:
        return False
    if existing and existing.get("role") == role:
        return True
    if existing:
        plugin.unregister_llm_tool(TOOL_NAME)
    plugin.register_llm_tool(
        name=TOOL_NAME,
        description=(
            "读取 NEKO Live 当前场次实际收到的近期弹幕。"
            "不带 query 时返回最多三条近期记录，严格按最新到较早排列；"
            "允许存在正常直播延迟，由你结合用户问题、相对时间和直播语境自然选择、复述或概括，"
            "不要把记录称为候选 1/2/3，也不要捏造列表之外的弹幕。"
            "普通直播对话仅当一个具体当前话题确实会因观众近期发言而更自然时，才可带 query 调用一次。"
            "相关模式只返回一条低压、未回复、未使用的匹配弹幕；没有结果时不得猜测或反复调用。"
        ),
        parameters=TOOL_PARAMETERS,
        handler=plugin._get_recent_live_chat_tool,
        timeout=5.0,
        role=role,
    )
    return True


def recent_chat_tool_result(
    plugin: Any,
    query: Any = "",
) -> dict[str, Any]:
    runtime = getattr(plugin, "runtime", None)
    if runtime is None or not bool(getattr(runtime, "_accepting_live_events", False)):
        return {"available": False, "status": "not_live", "entries": []}
    clean_query = clean_relevance_query(public_text(query, max_length=80))
    live_events = getattr(runtime, "live_events", None)
    if clean_query:
        snapshot = getattr(live_events, "relevant_chat_snapshot", None)
        entries = (
            snapshot(query=clean_query, limit=1) if callable(snapshot) else []
        )
        mode = "relevant"
    else:
        snapshot = getattr(live_events, "recent_chat_snapshot", None)
        entries = snapshot(limit=3) if callable(snapshot) else []
        mode = (
            "session_tail"
            if any(
                not bool(item.get("within_fresh_window", True))
                for item in entries
                if isinstance(item, dict)
            )
            else "latest"
        )
    provider = getattr(runtime, "live_provider", None)
    platform_value = getattr(provider, "platform", "")
    platform = (
        platform_value
        if isinstance(platform_value, str)
        and platform_value in {"bilibili", "douyin"}
        else ""
    )
    room_ref_getter = getattr(provider, "configured_room_ref", None)
    try:
        room_ref_value = room_ref_getter() if callable(room_ref_getter) else ""
    except Exception:
        room_ref_value = ""
    room_ref = event_room_ref({"room_ref": room_ref_value})
    result = {
        "available": bool(entries),
        "status": (
            "ok"
            if entries
            else ("no_match" if clean_query else "empty")
        ),
        "mode": mode,
        "platform": platform,
        "room_ref": room_ref,
        "entries": entries,
    }
    return result
