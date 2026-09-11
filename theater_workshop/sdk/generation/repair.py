"""Use explicit assessment repair scopes without inferring field paths from prose or expanding node-edit permissions."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .facts import _resolve_pointer
from .evidence import effective_targets, fields_overlap

# 评分目录与实际补丁校验共用同一组权限，防止一边显示可修、一边拒绝相同字段。
STORY_BEAT_FIELDS = {"summary", "opening_scene", "must_not_happen", "catgirl_situation", "transition_goal", "character_state"}
CHARACTER_STATE_TEXT_FIELDS = {"catgirl_state", "player_state", "environment_state"}
TRANSITION_CONTRACT_FIELDS = {"reason", "bridge_scene_narration", "must_deliver", "must_preserve", "tone"}

REPAIR_PLAN_RULE = """每条问题和人物关系建议必须列repair_targets:[{"node_id":"真实节点ID","field":"该节点在story_outline内的相对JSON Pointer"}]，指出修改方案确实需要改变的全部字段，不把仅作证据或需要保留的字段列为修改目标。例：/character_state/catgirl、/goals/0/description、/outgoing_routes/0/transition_contract/bridge_scene_narration。结局的/summary是结局记录摘要，/scene_summary是节点正文摘要，两处都要改时分别列出。只能直接修的字段由节点text_repair_fields列出；goals等不在其中的字段仍可提出建议，但只能手动调整。新增节点或无法定位已有字段时repair_targets=[]，标structure。不能为了获得text权限将实际要改的目标描述写成summary。相同修改位置且相同修法尽量复用同一方案文字，独立影响仍分别说明。"""


def text_repair_fields(node: Mapping[str, Any]) -> list[str]:
    """Map editable fields of the actual node to assessment paths; missing state structures cannot gain edit permissions from author caches."""
    fields = ["/title", "/summary", "/opening_scene", "/must_not_happen", "/relationship_state", "/transition_goal"]
    if node.get("type") == "ending":
        fields.append("/scene_summary")
    state = (node.get("story_beat") or {}).get("character_state") or {}
    fields.extend(f"/character_state/{key.removesuffix('_state')}"
                  for key in CHARACTER_STATE_TEXT_FIELDS if isinstance(state.get(key), str))
    for index, route in enumerate(node.get("route_gates") or []):
        if isinstance(route, Mapping) and isinstance(route.get("transition_contract"), dict):
            fields.extend(f"/outgoing_routes/{index}/transition_contract/{key}" for key in TRANSITION_CONTRACT_FIELDS)
    return sorted(fields)


def context_nodes(context: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in [*(context.get("mainline") or []),
            *(context.get("endings") or []),
            *(node for branch in context.get("branches") or [] for node in branch.get("nodes") or [])]}


def classify_repair(context: Mapping[str, Any], issue: Mapping[str, Any], *, protected: list[dict] | None = None, require_plan_review: bool = False) -> dict[str, Any]:
    """Preserve original suggestions completely; unclear or unauthorized fields disable direct repair without dropping findings or inventing executable plans."""
    result = deepcopy(dict(issue))
    # 原方案留作展示，实际执行范围以逐字段复核为准；未复核的事实不能直接执行。
    targets = effective_targets(issue) if issue.get("source") == "facts" else issue.get("repair_targets")
    nodes = context_nodes(context)
    reason = ""
    covered = set()
    if not isinstance(targets, list) or not targets:
        reason = "事实复核未确认可执行的修改，不据此优化。" if issue.get("source") == "facts" else "未明确现有修改字段，需要重新评分或手动调整。"
    else:
        for target in targets:
            try:
                node_id, field = target["node_id"], target["field"]
                if node_id not in issue["target_node_ids"]:
                    raise ValueError("unexpected_target")
                node = nodes[node_id]
                _resolve_pointer(node, field)
                covered.add(node_id)
                if not any(field == allowed or field.startswith(allowed + "/") for allowed in node["text_repair_fields"]):
                    reason = "方案涉及当前节点优化不支持的字段，请手动调整。"
                if any(row["node_id"] == node_id and fields_overlap(field, row["field"]) for row in protected or []):
                    reason = "方案涉及事实复核要求保持原文的字段，不执行此方案。"
            except (KeyError, IndexError, TypeError, ValueError):
                reason = "修改位置未能在当前故事中核实，请重新评分或手动调整。"
                break
        if not reason and issue.get("source") != "facts" and covered != set(issue["target_node_ids"]):
            reason = "部分关联节点未列出修改字段，请补全方案后再优化。"
    if not reason and issue.get("model_repair_scope", issue.get("repair_scope")) == "structure":
        reason = "方案声明需要结构调整，请手动处理。"
    # 初次分类仅选择待复核方案；执行时必须已有肯定复核，重算权限也不能重开被拦截方案。
    review = issue.get("plan_review")
    if not reason and (require_plan_review or review is not None):
        if (not isinstance(review, Mapping) or review.get("status") != "ready"
                or review.get("missing_targets") != [] or review.get("conflicting_issue_ids") != []):
            reason = "方案复核未确认可执行，请查看复核意见或重新评分。"
    result.update(model_repair_scope=issue.get("model_repair_scope", issue.get("repair_scope")),
                  repair_scope="structure" if reason else "text", repairable=not reason,
                  repair_reason=reason, repair_node_ids=sorted(covered) if not reason else [],
                  repair_targets=deepcopy(issue.get("repair_targets")) if isinstance(issue.get("repair_targets"), list) else [])
    return result


def assign_shared_plans(report: dict[str, Any]) -> None:
    """Merge only identical locations, plans and execution permissions; retain every issue and impact without semantic deduplication."""
    seen = {}
    for issue in [*report["issues"], *report["relationship_advice"]]:
        targets = issue.get("repair_targets") or []
        if not targets or any(not isinstance(item, Mapping) or not isinstance(item.get("node_id"), str)
                              or not isinstance(item.get("field"), str) for item in targets):
            continue
        key = (tuple(sorted((item["node_id"], item["field"]) for item in targets)),
               tuple(sorted(issue["target_node_ids"])), issue["repair_scope"],
               # 同文方案也可能因保留项而得到不同复核结论，不能共享到另一个执行权限。
               (issue.get("plan_review") or {}).get("status"),
               str(issue.get("modification_plan") or issue.get("suggestion") or "").strip())
        if key in seen:
            issue["shared_plan_issue_id"] = seen[key]
        else:
            seen[key] = issue["issue_id"]


def changed_fields(before: Any, after: Any, path: str = "") -> list[str]:
    """Compare changes in the actual assessment projection to prevent incidental edits to correct fields outside the plan."""
    if before == after:
        return []
    if isinstance(before, Mapping) and isinstance(after, Mapping) and set(before) == set(after):
        return [changed for key in before for changed in changed_fields(
            before[key], after[key], path + "/" + key.replace("~", "~0").replace("/", "~1"))]
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [changed for index, item in enumerate(before)
                for changed in changed_fields(item, after[index], f"{path}/{index}")]
    return [path]
