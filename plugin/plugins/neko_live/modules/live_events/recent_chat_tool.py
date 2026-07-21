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
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 3,
            "default": 1,
            "description": (
                "按最新到更早返回本场最后几条弹幕；1=最后一条，2 可回答上上条，最多 3 条。"
            ),
        },
        "position": {
            "type": "integer",
            "minimum": 1,
            "maximum": 3,
            "description": (
                "可选的精确位置：1=最新一条，2=前一条，3=前两条。"
                "设置后只返回该位置的一条，避免模型自行数列表。"
            ),
        },
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
            "用户询问刚刚、最新或谁说了什么时必须无 query 调用；"
            "询问最新一条时设置 position=1，前一条设置 position=2，前两条设置 position=3；"
            "插件只返回目标位置，不能自行从列表数。"
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
    limit: Any = 1,
    query: Any = "",
    position: Any = None,
) -> dict[str, Any]:
    runtime = getattr(plugin, "runtime", None)
    if runtime is None or not bool(getattr(runtime, "_accepting_live_events", False)):
        return {"available": False, "status": "not_live", "entries": []}
    clean_limit = _clean_limit(limit)
    clean_position = _clean_position(position)
    clean_query = clean_relevance_query(public_text(query, max_length=80))
    invalid_position = (
        not clean_query and position is not None and clean_position is None
    )
    live_events = getattr(runtime, "live_events", None)
    if clean_query:
        snapshot = getattr(live_events, "relevant_chat_snapshot", None)
        entries = (
            snapshot(query=clean_query, limit=1) if callable(snapshot) else []
        )
        mode = "relevant"
    else:
        snapshot = getattr(live_events, "recent_chat_snapshot", None)
        read_limit = clean_position or clean_limit
        entries = (
            snapshot(limit=read_limit)
            if callable(snapshot) and not invalid_position
            else []
        )
        if clean_position:
            entries = entries[clean_position - 1 : clean_position]
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
            else (
                "no_match"
                if clean_query
                else "invalid_position"
                if invalid_position
                else "position_unavailable"
                if clean_position
                else "empty"
            )
        ),
        "mode": mode,
        "platform": platform,
        "room_ref": room_ref,
        "entries": entries,
    }
    if clean_position and not clean_query:
        result["position"] = clean_position
    return result


def _clean_limit(value: Any) -> int:
    if isinstance(value, bool):
        return 1
    try:
        return min(3, max(1, int(value)))
    except (TypeError, ValueError):
        return 1


def _clean_position(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        position = int(value)
    except (TypeError, ValueError):
        return None
    return position if 1 <= position <= 3 else None
