"""Deliver optional author-owned text without asking a model to reproduce it."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Callable, Mapping

from utils.tokenize import count_tokens


MAX_FIXED_NARRATIONS = 8
MAX_FIXED_NARRATION_TOKENS = 2000
_PLACEHOLDER = re.compile(r"\{\{(catgirl_name|player_name)\}\}")


def validate_definitions(collector, beat: Mapping[str, Any], path: str) -> None:
    """Validate optional pieces; their raw text has a separate, bounded budget."""
    if "fixed_narrations" not in beat:
        return
    rows = collector.array(beat["fixed_narrations"], f"{path}.fixed_narrations")
    if len(rows) > MAX_FIXED_NARRATIONS:
        collector.add("too_many_fixed_narrations", path, "每幕最多八个固定旁白片段。")
    seen: dict[str, str] = {}
    total_tokens = 0
    for index, raw in enumerate(rows):
        item_path = f"{path}.fixed_narrations[{index}]"
        item = collector.obj(raw, item_path)
        if set(item) != {"id", "text", "trigger", "after", "required_before_exit"}:
            collector.add("fixed_narration_fields_invalid", item_path, "固定旁白字段不完整或含未知字段。")
        piece_id = collector.require_id(item.get("id"), f"{item_path}.id")
        if piece_id in seen:
            collector.add("duplicate_fixed_narration_id", item_path, "同一幕的固定旁白编号不能重复。")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip() or text != text.strip():
            collector.add("fixed_narration_text_invalid", item_path, "原文必须非空且不含首尾空白；正文内换行原样保留。")
        else:
            total_tokens += count_tokens(text)
            if re.search(r"\{\{.*?\}\}", _PLACEHOLDER.sub("", text)):
                collector.add("fixed_narration_placeholder_invalid", item_path, "仅支持 catgirl_name 和 player_name 姓名占位符。")
        trigger = collector.obj(item.get("trigger"), f"{item_path}.trigger")
        kind = trigger.get("type")
        if kind == "condition":
            collector.require_text(trigger.get("condition"), f"{item_path}.trigger.condition")
        if (not isinstance(kind, str) or kind not in {"entry", "condition"}
                or set(trigger) != ({"type"} if kind == "entry" else {"type", "condition"})):
            collector.add("fixed_narration_trigger_invalid", item_path, "触发方式必须为入幕或明确的剧情条件。")
        after = collector.array(item.get("after"), f"{item_path}.after")
        if any(not isinstance(key, str) or key not in seen for key in after) or len(after) != len(set(map(str, after))):
            collector.add("fixed_narration_dependency_invalid", item_path, "前置片段只能引用同幕更早且不重复的编号。")
        if kind == "entry" and any(seen.get(str(key)) != "entry" for key in after):
            collector.add("fixed_narration_entry_dependency_invalid", item_path, "入幕片段不能等待幕内条件片段。")
        if not isinstance(item.get("required_before_exit"), bool):
            collector.add("fixed_narration_required_invalid", item_path, "离幕前必显标记必须是布尔值。")
        seen[piece_id] = str(kind)
    if total_tokens > MAX_FIXED_NARRATION_TOKENS:
        collector.add("fixed_narration_budget_exceeded", path, "每幕固定原文合计不能超过2000 tokens；超限报错，不截断。")


def definitions(node: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(node.get("story_beat", {}).get("fixed_narrations", []))


def displayed_ids(session: Any) -> set[tuple[str, str]]:
    """Use committed history, retained by existing forget/resume semantics."""
    result = set()
    if session is None:
        return result
    for record in [session.opening_performance, *session.performance_history]:
        for part in record.get("segments", [record]):
            for item in part.get("fixed_narrations", []):
                result.add((item["node_id"], item["id"]))
    return result


def pending_definitions(node: Mapping[str, Any], session: Any) -> list[dict[str, Any]]:
    seen = displayed_ids(session)
    return [item for item in definitions(node) if (node["id"], item["id"]) not in seen]


def required_pending(node: Mapping[str, Any], session: Any) -> bool:
    return any(item["required_before_exit"] for item in pending_definitions(node, session))


def render_text(text: str, bindings: Mapping[str, str]) -> str:
    """Substitute explicit names once; never rewrite ordinary text or serial IDs."""
    return _PLACEHOLDER.sub(lambda match: bindings[match.group(1)], text)


def _bindings(binding: Mapping[str, Any], known: bool) -> dict[str, str]:
    return {"catgirl_name": str(binding.get("catgirl_name") or "当前猫娘"),
            "player_name": str(binding.get("player_address") or "你") if known else "你"}


def _piece(node_id: str, definition: Mapping[str, Any], binding: Mapping[str, Any], known: bool) -> dict[str, Any]:
    names = _bindings(binding, known)
    return {"node_id": node_id, "id": definition["id"],
            "text": render_text(definition["text"], names), "bindings": names,
            "position": "before" if definition["trigger"]["type"] == "entry" else "after"}


def add_entry(node: Mapping[str, Any], performance: Mapping[str, Any], binding: Mapping[str, Any],
              known: bool, *, session: Any = None) -> dict[str, Any]:
    result = deepcopy(dict(performance))
    existing = {item["id"] for item in result.get("fixed_narrations", [])}
    pieces = [_piece(node["id"], item, binding, known) for item in pending_definitions(node, session)
              if item["trigger"]["type"] == "entry" and item["id"] not in existing]
    if pieces:
        result["fixed_narrations"] = [*result.get("fixed_narrations", []), *pieces]
    return result


def actor_note(node: Mapping[str, Any], session: Any, binding: Mapping[str, Any], known: bool,
               *, project_condition: Callable[[str], str] = str) -> str:
    """Only entry text is readable before delivery; condition text stays private."""
    rows = pending_definitions(node, session)
    if not rows:
        return ""
    lines = ["固定旁白由程序按原文插入，禁止在正文、旁白或推荐中复述其全文。"
             "入幕原文在场景建立后、猫娘开口前展示，可承接反应；"
             "条件片段在本轮实际触发动作及复核后才展示，不得提前读到或演出阅读后的反应。"]
    for item in rows:
        if item["trigger"]["type"] == "entry":
            lines.append("入幕固定旁白：" + render_text(item["text"], _bindings(binding, known)))
        else:
            lines.append(f"待触发片段 {item['id']}：{project_condition(item['trigger']['condition'])}；"
                         f"前置片段：{','.join(item['after']) or '无'}。不得替玩家执行触发动作。")
    if any(item["required_before_exit"] for item in rows):
        lines.append("当前尚有离幕前必显片段；回应当前互动，不提前邀请跳到下一幕或结束。")
    return "\n".join(lines)


def review_candidates(node: Mapping[str, Any], session: Any) -> list[dict[str, Any]]:
    return [{"id": item["id"], "condition": item["trigger"]["condition"], "after": item["after"]}
            for item in pending_definitions(node, session) if item["trigger"]["type"] == "condition"]


def apply_triggers(node: Mapping[str, Any], session: Any, performance: Mapping[str, Any],
                   claims: tuple[dict[str, str], ...], player_input: str, *, known: bool) -> dict[str, Any]:
    """Accept only cited final-draft events; authored order decides insertion order."""
    from .numeric_v2_context import performance_history_records

    result = deepcopy(dict(performance))
    container = result["segments"][0] if isinstance(result.get("segments"), list) else result
    sources = [player_input, *(str(container.get(key) or "") for key in ("performance", "scene_narration")),
               *(row["text"] for row in performance_history_records(session))]
    selected = {claim["id"] for claim in claims if claim.get("evidence")
                and any(claim["evidence"] in text for text in sources)}
    seen = displayed_ids(session)
    pieces = []
    for item in definitions(node):
        key = (node["id"], item["id"])
        if (item["trigger"]["type"] == "condition" and item["id"] in selected and key not in seen
                and all((node["id"], parent) in seen for parent in item["after"])):
            pieces.append(_piece(node["id"], item, session.catgirl_binding, known))
            seen.add(key)
    if pieces:
        container["fixed_narrations"] = [*container.get("fixed_narrations", []), *pieces]
    return result


def validate_delivery(story: Mapping[str, Any], performance: Mapping[str, Any], *, session: Any = None,
                      node_id: str | None = None) -> None:
    """Validate immutable text, placement and dependencies on commit and replay."""
    nodes = {node["id"]: node for node in story["nodes"]}
    seen = displayed_ids(session)
    parts = performance.get("segments", [performance])
    for part in parts:
        expected_node = node_id or (session.current_node_id if session is not None else story["start_node_id"])
        if part.get("phase") == "target_opening":
            expected_node = performance.get("visible_node_id")
        raw = part.get("fixed_narrations", [])
        if not isinstance(raw, list) or len(raw) > MAX_FIXED_NARRATIONS:
            raise ValueError("numeric_fixed_narration_invalid")
        if not isinstance(expected_node, str) or expected_node not in nodes:
            raise ValueError("numeric_fixed_narration_invalid")
        allowed = {item["id"]: item for item in definitions(nodes[expected_node])}
        for piece in raw:
            if (not isinstance(piece, dict) or set(piece) != {"node_id", "id", "text", "position", "bindings"}
                    or piece.get("node_id") != expected_node or not isinstance(piece.get("id"), str)
                    or piece["id"] not in allowed
                    or part.get("phase") == "transition_bridge"):
                raise ValueError("numeric_fixed_narration_invalid")
            item = allowed[piece["id"]]
            if (session is None or part.get("phase") == "target_opening") and item["trigger"]["type"] != "entry":
                raise ValueError("numeric_fixed_narration_invalid")
            key = (expected_node, piece["id"])
            names = piece["bindings"]
            if (not isinstance(names, dict) or set(names) != {"catgirl_name", "player_name"}
                    or any(not isinstance(value, str) or not value for value in names.values())
                    or piece["text"] != render_text(item["text"], names)
                    or piece["position"] != ("before" if item["trigger"]["type"] == "entry" else "after")
                    or key in seen or any((expected_node, parent) not in seen for parent in item["after"])):
                raise ValueError("numeric_fixed_narration_invalid")
            seen.add(key)
        if session is None or part.get("phase") == "target_opening":
            if any(item["trigger"]["type"] == "entry" and (expected_node, item["id"]) not in seen
                   for item in allowed.values()):
                raise ValueError("numeric_fixed_narration_entry_missing")
