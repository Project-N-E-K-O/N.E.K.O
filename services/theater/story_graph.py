"""提供轻量小剧场的静态图查询和 Choice 路由。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from typing import Any

from . import rules


def node_by_id(story: dict[str, Any], node_id: str) -> dict[str, Any]:
    """按稳定 ID 查找节点。"""  # noqa: DOCSTRING_CJK
    for node in story.get("narrative_nodes") or []:
        if isinstance(node, dict) and str(node.get("node_id") or "") == node_id:
            return node
    return {}


def current_node(story: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """读取当前提交节点。"""  # noqa: DOCSTRING_CJK
    return node_by_id(story, str(state.get("current_node_id") or ""))


def outgoing_nodes(story: dict[str, Any], state: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """按作者顺序返回当前可达的边和目标节点。"""  # noqa: DOCSTRING_CJK
    current_id = str(state.get("current_node_id") or "")
    completed = set(state.get("completed_node_ids") or [])
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for edge in story.get("edges") or []:
        if not isinstance(edge, dict) or str(edge.get("from_node") or "") != current_id:
            continue
        node = node_by_id(story, str(edge.get("to_node") or ""))
        if not node or str(node.get("node_id") or "") in completed:
            continue
        if rules.node_is_available(node, state):
            candidates.append((edge, node))
    return candidates


def suggestion_options(story: dict[str, Any], state: dict[str, Any]) -> list[dict[str, str]]:
    """从当前出边的目标节点生成稳定行动/对白选项。"""  # noqa: DOCSTRING_CJK
    options: list[dict[str, str]] = []
    overrides = state.get("choice_label_overrides") if isinstance(state.get("choice_label_overrides"), dict) else {}
    for edge, node in outgoing_nodes(story, state):
        suggestions = [item for item in node.get("suggestions") or [] if isinstance(item, dict)]
        matched = [item for item in suggestions if _suggestion_matches_edge(item, edge)] or suggestions[:1]
        if not matched:
            matched = [{"label": str(node.get("title") or "继续剧情")}]
        for index, suggestion in enumerate(matched):
            static_label = str(suggestion.get("label") or "").strip()
            if not static_label:
                continue
            choice_id = str(suggestion.get("choice_id") or f"choice_{node.get('node_id')}_{index + 1}")
            # 模型只能改显示文案；ID、模式、目标和 callback 始终来自作者静态图。
            label = str(overrides.get(choice_id) or static_label).strip()
            options.append(
                {
                    "choice_id": choice_id,
                    "label": label,
                    "choice_mode": _choice_mode(suggestion, static_label),
                    "target_node_id": str(node.get("node_id") or ""),
                    "callback": str(suggestion.get("callback") or ""),
                }
            )
    return _dedupe_options(options)


def resolve_choice(story: dict[str, Any], state: dict[str, Any], choice_id: str) -> dict[str, Any]:
    """只在当前可见选项中解析 Choice，防止提交过期按钮。"""  # noqa: DOCSTRING_CJK
    for option in suggestion_options(story, state):
        if option["choice_id"] == str(choice_id or ""):
            return option
    return {}


def _suggestion_matches_edge(suggestion: dict[str, Any], edge: dict[str, Any]) -> bool:
    """优先选择与入边语义一致的目标节点文案。"""  # noqa: DOCSTRING_CJK
    behavior = str(suggestion.get("behavior_hint") or "")
    meaning = str(suggestion.get("meaning_hint") or "")
    return (not behavior or behavior == str(edge.get("behavior") or "")) and (
        not meaning or meaning == str(edge.get("meaning") or "")
    )


def _choice_mode(suggestion: dict[str, Any], label: str) -> str:
    """兼容旧剧本：显式值优先，带引号的第一人称句子归为对白。"""  # noqa: DOCSTRING_CJK
    explicit = str(suggestion.get("choice_mode") or "").strip()
    if explicit in {"action", "dialogue"}:
        return explicit
    if any(mark in label for mark in ('“', '”', '「', '」', '："', ':"')):
        return "dialogue"
    return "action"


def _dedupe_options(options: list[dict[str, str]]) -> list[dict[str, str]]:
    """按 Choice ID 去重，避免同一目标的重复边生成重复按钮。"""  # noqa: DOCSTRING_CJK
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for option in options:
        if option["choice_id"] in seen:
            continue
        seen.add(option["choice_id"])
        result.append(option)
    return result
