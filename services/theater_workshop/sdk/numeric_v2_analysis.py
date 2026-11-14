"""Provide deterministic author diagnostics for whole-story Numeric v2 metrics and routes."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from difflib import SequenceMatcher
from itertools import product
import math
import re
from typing import Any, Mapping


# Comparisons change truth only near thresholds; enumerating representative points exactly evaluates supported conditions.
_MAX_REPRESENTATIVE_STATES = 50_000
# Path diagnostics serve author guidance only; graphs above the complexity cap return unknown explicitly, never guesses.
_MAX_PACING_PATH_STATES = 512
_COMPARATORS = {
    "==": lambda left, right: left == right,
    "!=": lambda left, right: left != right,
    ">": lambda left, right: left > right,
    "<": lambda left, right: left < right,
    ">=": lambda left, right: left >= right,
    "<=": lambda left, right: left <= right,
}
# For conditions based on these subjects' own actions, the only runtime witness may be narrative written by Actor this turn.
_ACTOR_WITNESS_SUBJECTS = ("猫娘", "女主", "新载体", "机体", "猫耳")
# Player subjects and exclusion clauses do not mean the neko satisfies a condition merely by appearing in its text.
_PLAYER_ACTOR_TOKENS = ("玩家", "男主", "你")
_EXCLUSION_MARKERS = ("不算", "不作为", "不能算", "不表示", "不算作", "并非", "不能代替")
_INDEPENDENT_DISCLOSURE_REQUIREMENT = re.compile(
    r"玩家|男主|你|并且|且(?!女主说明|猫娘说明)|同时|同意|理解|确认|决定|选择|承诺|接受|拒绝|授权|"
    r"接过|接稳|递出|递给|交接|交付|移交|交还|签字|操作|核验|"
    r"随后|后续|接下来|下一轮|等到|如果|尚未"
)


@dataclass(frozen=True)
class NumericV2AnalysisWarning:
    """Show author hints alongside compilation results without writing them into Story Package."""

    code: str
    path: str
    message: str


def _condition_actor(condition: str) -> str:
    """Return the actor a condition is satisfied by: ``actor`` (neko), ``player`` or ``""``.

    Chinese conditions are read clause by clause. The first subject token in a positive clause is
    the actor; exclusion clauses such as "玩家拿着不算" are skipped, and a clause without a subject
    inherits the previous actor.
    """  # noqa: DOCSTRING_CJK
    # Conditions are written in Chinese; retain original example sentences to explain evaluation.

    actor = ""
    for clause in re.split(r"[，。；、,.;!?！？\n]+", condition):
        text = clause.strip()
        if not text or any(marker in text for marker in _EXCLUSION_MARKERS):
            continue
        positions = [(text.find(token), "player") for token in _PLAYER_ACTOR_TOKENS if token in text]
        positions.extend((text.find(token), "actor") for token in _ACTOR_WITNESS_SUBJECTS if token in text)
        if positions:
            actor = min(positions)[1]
    return actor


def _fixed_narration_witness_warnings(ordered_nodes: list[Mapping[str, Any]]) -> list[NumericV2AnalysisWarning]:
    """Warn when a condition-triggered fixed narration can only be witnessed by the actor's own prose.

    Runtime evidence for a condition includes the current (uncommitted) candidate text, so a
    condition whose subject is the neko can be satisfied by the actor writing the condition
    itself and then being cited as proof (issue 2.141).
    """

    warnings: list[NumericV2AnalysisWarning] = []
    for node_index, node in enumerate(ordered_nodes):
        beat = node.get("story_beat") if isinstance(node, Mapping) else None
        if not isinstance(beat, Mapping):
            continue
        for position, item in enumerate(beat.get("fixed_narrations") or []):
            if not isinstance(item, Mapping):
                continue
            trigger = item.get("trigger")
            if not isinstance(trigger, Mapping) or trigger.get("type") != "condition":
                continue
            condition = str(trigger.get("condition") or "")
            if _condition_actor(condition) != "actor":
                continue
            warnings.append(NumericV2AnalysisWarning(
                code="fixed_narration_condition_actor_witnessed",
                path=f"nodes[{node_index}].story_beat.fixed_narrations[{position}].trigger.condition",
                message=(
                    f"固定旁白 {item.get('id')!r} 的条件由当前角色的自身动作满足。运行时可用证据包含演员本轮写下的正文，"
                    "因此存在“演员先写下条件成立、再被该句自证触发”的风险；引文核验只证明该句出现在某个来源里，不区分该来源是否为尚未提交的候选。"
                    "建议改用 entry 触发，或把条件绑定到玩家输入可验证的动作。"
                ),
            ))
    return warnings


def _matches_entry_disclosure(description: str, original: str) -> bool:
    """Recognize explicit display claims with substantial literal support, without inferring prose semantics."""

    claim, _, exclusion = description.strip().removesuffix("。").partition("。")
    if len(claim) > 200 or not claim.startswith("已公开展示") or _INDEPENDENT_DISCLOSURE_REQUIREMENT.search(claim):
        return False
    if exclusion and not re.fullmatch(r"(?:仅|只)[^。！？\n]+(?:不能证明|不足以证明|不算)", exclusion):
        return False
    claim_text = re.sub(r"[\W_]+", "", claim.removeprefix("已公开展示"))
    original_text = re.sub(r"[\W_]+", "", original)
    matches = SequenceMatcher(None, claim_text, original_text, autojunk=False).get_matching_blocks()
    # Ignore scattered common characters; require an eight-character anchor and twelve literal characters overall.
    return any(block.size >= 8 for block in matches) and sum(block.size for block in matches if block.size >= 4) >= 12


def _entry_disclosure_gate_warnings(story: Mapping[str, Any], nodes: list[Mapping[str, Any]]) -> list[NumericV2AnalysisWarning]:
    """Spot a narrow duplicate gate without declaring valid fact routes unreachable."""

    contract = story.get("fact_contract")
    facts = contract.get("facts") if isinstance(contract, Mapping) else None
    if not isinstance(facts, Mapping):
        return []
    warnings: list[NumericV2AnalysisWarning] = []
    for index, node in enumerate(nodes):
        routes = node.get("route_gates") or []
        if len(routes) != 1 or not isinstance(routes[0], Mapping):
            continue
        projected = _condition_rows(routes[0])
        if projected is None or projected[0] != "all" or len(projected[1]) != 1:
            continue
        condition = projected[1][0]
        key = condition.get("key")
        if (condition.get("type") != "fact_equals" or condition.get("value") is not True
                or not isinstance(key, str) or not key.startswith(f"scene:{node.get('id')}:")):
            continue
        fact = facts.get(key)
        if (not isinstance(fact, Mapping) or fact.get("value_type") != "bool" or fact.get("visibility") != "public"
                or fact.get("evidence_source") != "performance" or "evidence_anchor" in fact):
            continue
        beat = node.get("story_beat")
        completion = node.get("completion_contract")
        if not isinstance(beat, Mapping) or not isinstance(completion, Mapping):
            continue
        receipts = {row["fixed_narration_id"] for row in completion.get("all") or []
                    if isinstance(row, Mapping) and isinstance(row.get("fixed_narration_id"), str)}
        description = fact.get("description")
        if not receipts or not isinstance(description, str):
            continue
        for piece in beat.get("fixed_narrations") or []:
            if not isinstance(piece, Mapping) or not isinstance(piece.get("id"), str) or piece["id"] not in receipts:
                continue
            trigger, text = piece.get("trigger"), piece.get("text")
            if (piece.get("required_before_exit") is not True or piece.get("after") != []
                    or trigger != {"type": "entry"} or not isinstance(text, str)
                    or not _matches_entry_disclosure(description, text)):
                continue
            warnings.append(NumericV2AnalysisWarning(
                code="route_fact_duplicates_entry_narration",
                path=f"nodes[{index}].route_gates[0].conditions.all[0]",
                message=f"出口事实 {key!r} 疑似重复核验必显入幕旁白 {piece['id']!r} 已交付的披露。"
                        "若没有独立的理解、授权或行动结果，可只用现有展示回执作为完成条件。",
            ))
            break
    return warnings


def _metric_definitions(story: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    definitions: dict[str, dict[str, int]] = {}
    for metric_id, raw in (story.get("metric_schema") or {}).items():
        if not isinstance(raw, Mapping):
            continue
        limits = raw.get("per_turn_limit") or {}
        try:
            definitions[str(metric_id)] = {
                "min": int(raw["min"]),
                "max": int(raw["max"]),
                "initial": int(raw.get("initial", raw["min"])),
                "increase": int(limits["increase"]),
                "decrease": int(limits["decrease"]),
            }
        except (KeyError, TypeError, ValueError):
            # Formal compilation rejects invalid definitions first; standalone analysis returns unknown rather than introducing separate errors.
            continue
    return definitions


def _nodes(story: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    ordered = [dict(node) for node in story.get("nodes") or [] if isinstance(node, Mapping)]
    by_id = {
        str(node.get("id")): node
        for node in ordered
        if str(node.get("id") or "")
    }
    return ordered, by_id


def _structural_reachable_nodes(
    start_node_ids: set[str],
    nodes_by_id: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    """Compute a conservative closure over structural outgoing edges when precise analysis cannot continue."""

    reachable: set[str] = set()
    stack = [node_id for node_id in start_node_ids if node_id in nodes_by_id]
    while stack:
        node_id = stack.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        node = nodes_by_id[node_id]
        for route in node.get("route_gates") or []:
            if not isinstance(route, Mapping):
                continue
            target_id = str(route.get("target_node_id") or "")
            if target_id in nodes_by_id and target_id not in reachable:
                stack.append(target_id)
    return reachable


def _cyclic_node_ids(nodes_by_id: Mapping[str, Mapping[str, Any]]) -> set[str]:
    """Return nodes in any directed cycle; classify cyclic pacing as unknown."""

    adjacency = {
        node_id: {
            str(route.get("target_node_id") or "")
            for route in node.get("route_gates") or []
            if isinstance(route, Mapping)
            and str(route.get("target_node_id") or "") in nodes_by_id
        }
        for node_id, node in nodes_by_id.items()
    }

    def can_return(source_id: str, target_id: str) -> bool:
        stack = [target_id]
        visited: set[str] = set()
        while stack:
            node_id = stack.pop()
            if node_id == source_id:
                return True
            if node_id in visited:
                continue
            visited.add(node_id)
            stack.extend(adjacency.get(node_id, ()))
        return False

    return {
        source_id
        for source_id, targets in adjacency.items()
        if any(can_return(source_id, target_id) for target_id in targets)
    }


def _condition_rows(route: Mapping[str, Any]) -> tuple[str, list[Mapping[str, Any]]] | None:
    conditions = route.get("conditions")
    if not isinstance(conditions, Mapping):
        return None
    modes = [mode for mode in ("all", "any") if mode in conditions]
    if len(modes) != 1 or not isinstance(conditions.get(modes[0]), list):
        return None
    rows = conditions[modes[0]]
    if any(not isinstance(row, Mapping) for row in rows):
        return None
    return modes[0], list(rows)


def entry_route_scope(story: Mapping[str, Any], target_id: str) -> dict[str, Any]:
    """Group public fact predicates by what is required when each incoming edge is selected."""
    definitions = (story.get("fact_contract") or {}).get("facts") or {}
    routes = []
    for node in story.get("nodes") or ():
        for route in node.get("route_gates") or ():
            if route.get("target_node_id") != target_id:
                continue
            mode, rows = _condition_rows(route) or ("all", [])
            required = rows if mode == "all" or len(rows) == 1 else []
            facts = {row["key"]: row["value"] for row in required
                     if row.get("type") == "fact_equals" and type(row.get("value")) in (bool, int, str)
                     and (definitions.get(row.get("key")) or {}).get("visibility") == "public"}
            routes.append({"source_node_id": node.get("id"), "route_id": route.get("id"), "gate_facts": facts})
    initial_entry = story.get("start_node_id") == target_id
    common = {}
    if routes and not initial_entry:
        common = {
            key: value for key, value in routes[0]["gate_facts"].items()
            if all(type(other["gate_facts"].get(key)) is type(value)
                   and other["gate_facts"].get(key) == value for other in routes[1:])
        }
    for route in routes:
        route["gate_facts"] = {key: value for key, value in route["gate_facts"].items() if key not in common}
    return {"initial_entry": initial_entry, "common_gate_facts": common, "routes": routes}


def _conditions_match(route: Mapping[str, Any], values: Mapping[str, int]) -> bool | None:
    projected = _condition_rows(route)
    if projected is None:
        return None
    mode, rows = projected
    checks: list[bool] = []
    for row in rows:
        if row.get("type") == "fact_equals":
            # Authored equality is not evidence the player will commit this fact.
            return None
        metric_id = str(row.get("metric") or "")
        comparator = _COMPARATORS.get(str(row.get("op") or ""))
        if metric_id not in values or comparator is None:
            return None
        try:
            checks.append(comparator(int(values[metric_id]), int(row["value"])))
        except (KeyError, TypeError, ValueError):
            return None
    return any(checks) if mode == "any" else all(checks)


def _route_path(node_index: int, route_index: int | None = None) -> str:
    path = f"nodes[{node_index}]"
    return f"{path}.route_gates[{route_index}]" if route_index is not None else path


def _representative_values(
    metric_id: str,
    bounds: tuple[int, int],
    routes: list[Mapping[str, Any]],
) -> list[int]:
    """Return finite integer representatives covering the true and false partitions of every comparison condition."""

    minimum, maximum = bounds
    values = {minimum, maximum}
    for route in routes:
        projected = _condition_rows(route)
        if projected is None:
            continue
        for row in projected[1]:
            if str(row.get("metric") or "") != metric_id:
                continue
            try:
                threshold = int(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
            for candidate in (threshold - 1, threshold, threshold + 1):
                if minimum <= candidate <= maximum:
                    values.add(candidate)
    return sorted(values)


def _selected_bounds_from_points(
    points: list[tuple[int, ...]],
    referenced_metrics: list[str],
    dimensions: list[list[int]],
    bounds: Mapping[str, tuple[int, int]],
) -> tuple[dict[str, tuple[int, int]] | None, bool]:
    """Collapse only complete, contiguous Cartesian products into intervals; nonconvex sets must remain unknown."""

    if not points:
        return None, False
    if not referenced_metrics:
        return dict(bounds), False
    unique_points = set(points)
    selected_values: list[list[int]] = []
    for index, domain in enumerate(dimensions):
        values = sorted({point[index] for point in unique_points})
        positions = [position for position, value in enumerate(domain) if value in values]
        # For a dimension with holes, min/max incorrectly propagates unreachable intermediate states to the next scene.
        if positions != list(range(positions[0], positions[-1] + 1)):
            return None, True
        selected_values.append(values)
    # Individually continuous dimensions are insufficient; verify cross-metric correlations are preserved too.
    if len(unique_points) != math.prod(len(values) for values in selected_values):
        return None, True
    selected_bounds = dict(bounds)
    for metric_id, values in zip(referenced_metrics, selected_values):
        selected_bounds[metric_id] = (values[0], values[-1])
    return selected_bounds, False


def _route_selection(
    node: Mapping[str, Any],
    bounds: Mapping[str, tuple[int, int]],
) -> dict[str, Any]:
    """Enumerate priority outcomes exactly and propagate only when entrance sets can safely collapse into intervals."""

    routes = [route for route in node.get("route_gates") or [] if isinstance(route, Mapping)]
    states = {
        str(route.get("id") or ""): {
            "condition_seen": False,
            "selected_seen": False,
            "selected_bounds": None,
            "selected_bounds_unknown": False,
        }
        for route in routes
    }
    referenced_metrics = sorted({
        str(row.get("metric") or "")
        for route in routes
        for projected in [_condition_rows(route)]
        if projected is not None
        for row in projected[1]
        if str(row.get("metric") or "") in bounds
    })
    dimensions = [
        _representative_values(metric_id, bounds[metric_id], routes)
        for metric_id in referenced_metrics
    ]
    state_count = math.prod(len(values) for values in dimensions) if dimensions else 1
    if state_count > _MAX_REPRESENTATIVE_STATES:
        return {"routes": states, "gap_seen": False, "unknown": True}

    selected_points: dict[str, list[tuple[int, ...]]] = {
        route_id: [] for route_id in states
    }
    gap_seen = False
    for combination in product(*dimensions) if dimensions else [()]:
        values = dict(zip(referenced_metrics, combination))
        matched: list[Mapping[str, Any]] = []
        for route in routes:
            verdict = _conditions_match(route, values)
            if verdict is None:
                return {"routes": states, "gap_seen": gap_seen, "unknown": True}
            if verdict:
                route_id = str(route.get("id") or "")
                states[route_id]["condition_seen"] = True
                matched.append(route)
        if not matched:
            gap_seen = True
            continue
        highest = max(int(route.get("priority", 0)) for route in matched)
        winners = [route for route in matched if int(route.get("priority", 0)) == highest]
        if len(winners) != 1:
            return {"routes": states, "gap_seen": gap_seen, "unknown": True}
        winner_id = str(winners[0].get("id") or "")
        states[winner_id]["selected_seen"] = True
        selected_points[winner_id].append(tuple(
            values[metric_id] for metric_id in referenced_metrics
        ))
    for route_id, points in selected_points.items():
        selected_bounds, bounds_unknown = _selected_bounds_from_points(
            points,
            referenced_metrics,
            dimensions,
            bounds,
        )
        states[route_id]["selected_bounds"] = selected_bounds
        states[route_id]["selected_bounds_unknown"] = bounds_unknown
    return {"routes": states, "gap_seen": gap_seen, "unknown": False}


def _expanded_bounds(
    entry: Mapping[str, tuple[int, int]],
    node: Mapping[str, Any],
    definitions: Mapping[str, Mapping[str, int]],
    *,
    extra_turns: int = 0,
) -> dict[str, tuple[int, int]]:
    """Expand possible intervals using soft recommended turn counts for pacing hints only, without constraining Runtime."""

    minimum_turns = int(node.get("min_turns", 0) or 0)
    recommended_turns = int(node.get("recommended_turns", minimum_turns) or minimum_turns)
    turns = max(minimum_turns, recommended_turns) + max(0, extra_turns)
    return {
        metric_id: (
            max(definition["min"], entry[metric_id][0] - turns * definition["decrease"]),
            min(definition["max"], entry[metric_id][1] + turns * definition["increase"]),
        )
        for metric_id, definition in definitions.items()
    }


def _minimum_extra_turns(
    node: Mapping[str, Any],
    route_id: str,
    entry: Mapping[str, tuple[int, int]],
    definitions: Mapping[str, Mapping[str, int]],
) -> tuple[int | None, bool]:
    """Find the first additional turn count beyond the soft recommendation at which a route can become selectable."""

    maximum_span = max(
        (definition["max"] - definition["min"] for definition in definitions.values()),
        default=0,
    )
    for extra_turns in range(1, maximum_span + 1):
        selection = _route_selection(
            node,
            _expanded_bounds(entry, node, definitions, extra_turns=extra_turns),
        )
        if selection["unknown"]:
            return None, True
        if selection["routes"].get(route_id, {}).get("selected_seen"):
            return extra_turns, False
    return None, False


def _normalized_transition_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _transition_signature(route: Mapping[str, Any]) -> tuple[str, str] | None:
    contract = route.get("transition_contract")
    if not isinstance(contract, Mapping):
        return None
    reason = _normalized_transition_text(contract.get("reason"))
    bridge = _normalized_transition_text(contract.get("bridge_scene_narration"))
    if not bridge:
        bridge = _normalized_transition_text("|".join(
            str(item) for item in contract.get("must_deliver") or []
        ))
    return (reason, bridge) if reason and bridge else None


def _duplicate_transition_warnings(
    ordered_nodes: list[dict[str, Any]],
) -> list[NumericV2AnalysisWarning]:
    warnings: list[NumericV2AnalysisWarning] = []
    for node_index, node in enumerate(ordered_nodes):
        routes = [route for route in node.get("route_gates") or [] if isinstance(route, Mapping)]
        for left_index, left in enumerate(routes):
            left_signature = _transition_signature(left)
            if left_signature is None:
                continue
            for right_index in range(left_index + 1, len(routes)):
                right = routes[right_index]
                if (
                    str(left.get("target_node_id") or "") == str(right.get("target_node_id") or "")
                    or left_signature != _transition_signature(right)
                ):
                    continue
                warnings.append(NumericV2AnalysisWarning(
                    code="route_transition_duplicate",
                    path=_route_path(node_index),
                    message=(
                        f"路线 {left.get('id')} 与 {right.get('id')} 通往不同目标，却复用了相同的转场原因和可见桥段；"
                        "请让玩家看见真实不同的行动或后果。"
                    ),
                ))
    return warnings


def _analyze_numeric_v2_story(story: Mapping[str, Any]) -> tuple[NumericV2AnalysisWarning, ...]:
    """Analyze graph-wide metric reachability, soft pacing risks, priority shadowing and transition distinguishability."""

    definitions = _metric_definitions(story)
    ordered_nodes, nodes_by_id = _nodes(story)
    node_indexes = {
        str(node.get("id") or ""): index
        for index, node in enumerate(ordered_nodes)
        if str(node.get("id") or "")
    }
    start_node_id = str(story.get("start_node_id") or "")
    if start_node_id not in nodes_by_id:
        return tuple((
            *_duplicate_transition_warnings(ordered_nodes),
            *_fixed_narration_witness_warnings(ordered_nodes),
        ))

    global_bounds = {
        metric_id: (definition["min"], definition["max"])
        for metric_id, definition in definitions.items()
    }
    global_selections: dict[str, dict[str, Any]] = {}
    warnings: list[NumericV2AnalysisWarning] = []
    for node_id, node in nodes_by_id.items():
        if node.get("type") == "ending" or node.get("terminal") is True:
            continue
        selection = _route_selection(node, global_bounds)
        global_selections[node_id] = selection
        node_index = node_indexes[node_id]
        if selection["unknown"]:
            warnings.append(NumericV2AnalysisWarning(
                code="route_analysis_unknown",
                path=_route_path(node_index),
                message="该幕依赖运行时事实，或条件组合超出当前确定性分析范围，无法可靠判断全部路线。",
            ))

    # Theoretical reachability uses full metric declaration ranges; soft recommended turns must not become hard limits.
    eventually_reachable = {start_node_id}
    reachability_unknown: set[str] = set()
    stack = [start_node_id]
    while stack:
        node_id = stack.pop()
        node = nodes_by_id[node_id]
        selection = global_selections.get(node_id)
        if not selection:
            continue
        if selection["unknown"]:
            # If the current scene cannot be enumerated exactly, downstream remains unknown, never inferred mathematically unreachable.
            unknown_targets = {
                str(route.get("target_node_id") or "")
                for route in node.get("route_gates") or []
                if isinstance(route, Mapping)
            }
            reachability_unknown.update(
                _structural_reachable_nodes(unknown_targets, nodes_by_id)
            )
            continue
        for route in node.get("route_gates") or []:
            if not isinstance(route, Mapping):
                continue
            route_state = selection["routes"].get(str(route.get("id") or ""), {})
            if not route_state.get("selected_seen"):
                continue
            target_id = str(route.get("target_node_id") or "")
            if target_id in nodes_by_id and target_id not in eventually_reachable:
                eventually_reachable.add(target_id)
                stack.append(target_id)

    for node_id in sorted(eventually_reachable, key=lambda item: node_indexes.get(item, 10**9)):
        node = nodes_by_id[node_id]
        selection = global_selections.get(node_id)
        if not selection or selection["unknown"]:
            continue
        node_index = node_indexes[node_id]
        if selection["gap_seen"]:
            warnings.append(NumericV2AnalysisWarning(
                code="route_condition_gap",
                path=_route_path(node_index),
                message="该幕存在合法数值状态没有任何路线可选；玩家接受转场时可能无法离幕。",
            ))
        for route_index, route in enumerate(node.get("route_gates") or []):
            if not isinstance(route, Mapping):
                continue
            route_id = str(route.get("id") or "")
            route_state = selection["routes"].get(route_id, {})
            if not route_state.get("condition_seen"):
                warnings.append(NumericV2AnalysisWarning(
                    code="route_mathematically_impossible",
                    path=_route_path(node_index, route_index),
                    message=f"路线 {route_id} 的数值条件在声明范围内没有任何可满足状态。",
                ))
            elif not route_state.get("selected_seen"):
                warnings.append(NumericV2AnalysisWarning(
                    code="route_priority_shadowed",
                    path=_route_path(node_index, route_index),
                    message=(
                        f"路线 {route_id} 的全部可满足状态都会被更高 priority 的兄弟路线抢先选择，"
                        "因此 Runtime 永远不会选中它。"
                    ),
                ))

    for node_id, node in nodes_by_id.items():
        if node_id in eventually_reachable or node_id in reachability_unknown:
            continue
        code = "ending_numeric_unreachable" if node.get("type") == "ending" else "node_numeric_unreachable"
        warnings.append(NumericV2AnalysisWarning(
            code=code,
            path=_route_path(node_indexes[node_id]),
            message="该节点虽然在结构图上有路径，但按数值条件与 priority 永远无法被 Runtime 选中。",
        ))

    cyclic_nodes = _cyclic_node_ids(nodes_by_id)
    pacing_cyclic_nodes = cyclic_nodes.intersection(
        eventually_reachable.union(reachability_unknown)
    )
    cycle_affected_nodes = _structural_reachable_nodes(pacing_cyclic_nodes, nodes_by_id)
    for node_id in sorted(pacing_cyclic_nodes, key=lambda item: node_indexes[item]):
        warnings.append(NumericV2AnalysisWarning(
            code="route_analysis_unknown",
            path=_route_path(node_indexes[node_id]),
            message="该幕位于剧情循环中，重复进入会累积新的软回合预算，无法可靠给出确定性节奏结论。",
        ))

    route_stats: dict[str, dict[str, Any]] = {}
    recommended_reachable = {start_node_id}
    # Loops and nonconvex entries, plus their downstream nodes, remain unknown without deterministic pacing warnings.
    pacing_unknown_nodes = set(cycle_affected_nodes).union(reachability_unknown)
    pacing_stack: list[tuple[str, dict[str, tuple[int, int]], tuple[str, ...]]] = [(
        start_node_id,
        {
            metric_id: (definition["initial"], definition["initial"])
            for metric_id, definition in definitions.items()
        },
        (),
    )]
    processed_states = 0
    pacing_analysis_incomplete = False
    while pacing_stack:
        node_id, entry_bounds, path = pacing_stack.pop()
        processed_states += 1
        if processed_states > _MAX_PACING_PATH_STATES:
            pacing_analysis_incomplete = True
            warnings.append(NumericV2AnalysisWarning(
                code="route_analysis_unknown",
                path="nodes",
                message="可达路径组合超过作者诊断上限，后续软节奏结果无法可靠展开。",
            ))
            break
        node = nodes_by_id.get(node_id)
        if not isinstance(node, Mapping):
            continue
        recommended_reachable.add(node_id)
        if node.get("type") == "ending" or node.get("terminal") is True:
            continue
        if node_id in cycle_affected_nodes:
            continue
        expanded = _expanded_bounds(entry_bounds, node, definitions)
        selection = _route_selection(node, expanded)
        for route in node.get("route_gates") or []:
            if not isinstance(route, Mapping):
                continue
            route_id = str(route.get("id") or "")
            stats = route_stats.setdefault(route_id, {
                "source_node_id": node_id,
                "seen": 0,
                "selected": 0,
                "minimum_extra_turns": None,
                "unknown": False,
            })
            stats["seen"] += 1
            if selection["unknown"]:
                stats["unknown"] = True
                target_id = str(route.get("target_node_id") or "")
                pacing_unknown_nodes.update(
                    _structural_reachable_nodes({target_id}, nodes_by_id)
                )
                continue
            route_state = selection["routes"].get(route_id, {})
            if route_state.get("selected_seen"):
                stats["selected"] += 1
                target_id = str(route.get("target_node_id") or "")
                if route_state.get("selected_bounds_unknown"):
                    stats["unknown"] = True
                    pacing_unknown_nodes.update(
                        _structural_reachable_nodes({target_id}, nodes_by_id)
                    )
                    continue
                if target_id in (*path, node_id):
                    stats["unknown"] = True
                    pacing_unknown_nodes.update(
                        _structural_reachable_nodes({target_id}, nodes_by_id)
                    )
                    continue
                selected_bounds = route_state.get("selected_bounds")
                if target_id in nodes_by_id and isinstance(selected_bounds, Mapping):
                    pacing_stack.append((target_id, dict(selected_bounds), (*path, node_id)))
                continue
            global_state = (global_selections.get(node_id) or {}).get("routes", {}).get(route_id, {})
            if not global_state.get("selected_seen"):
                continue
            extra_turns, unknown = _minimum_extra_turns(node, route_id, entry_bounds, definitions)
            stats["unknown"] = stats["unknown"] or unknown
            if extra_turns is not None:
                current = stats["minimum_extra_turns"]
                stats["minimum_extra_turns"] = extra_turns if current is None else min(current, extra_turns)

    route_locations = {
        str(route.get("id") or ""): (node_indexes[str(node.get("id") or "")], route_index)
        for node in ordered_nodes
        if str(node.get("id") or "") in node_indexes
        for route_index, route in enumerate(node.get("route_gates") or [])
        if isinstance(route, Mapping)
    }
    for route_id, stats in route_stats.items():
        location = route_locations.get(route_id)
        if location is None:
            continue
        if stats["unknown"]:
            warnings.append(NumericV2AnalysisWarning(
                code="route_analysis_unknown",
                path=_route_path(*location),
                message=f"路线 {route_id} 依赖运行时事实、循环或复杂入口，无法可靠给出软节奏结论。",
            ))
            continue
        if pacing_analysis_incomplete:
            continue
        if stats["selected"] == 0 and stats["minimum_extra_turns"] is not None:
            warnings.append(NumericV2AnalysisWarning(
                code="route_pacing_difficult",
                path=_route_path(*location),
                message=(
                    f"路线 {route_id} 在 recommended_turns 的软节奏内无法进入，理论上至少还需要约 "
                    f"{stats['minimum_extra_turns']} 个普通回合；这不是自动换幕门槛。"
                ),
            ))
        elif 0 < stats["selected"] < stats["seen"]:
            warnings.append(NumericV2AnalysisWarning(
                code="route_pacing_partial",
                path=_route_path(*location),
                message=f"路线 {route_id} 只在部分前序入口能于软推荐节奏内进入，其他入口需要额外回合。",
            ))

    pacing_unreached = eventually_reachable - recommended_reachable - pacing_unknown_nodes
    for node_id in sorted(pacing_unreached, key=lambda item: node_indexes[item]):
        if pacing_analysis_incomplete:
            break
        node = nodes_by_id[node_id]
        if node.get("type") != "ending" and node.get("terminal") is not True:
            continue
        warnings.append(NumericV2AnalysisWarning(
            code="ending_pacing_difficult",
            path=_route_path(node_indexes[node_id]),
            message="该结局理论可达，但按当前各幕 recommended_turns 的软节奏无法到达，需要额外演绎回合。",
        ))

    warnings.extend(_duplicate_transition_warnings(ordered_nodes))
    warnings.extend(_fixed_narration_witness_warnings(ordered_nodes))
    unique: list[NumericV2AnalysisWarning] = []
    seen: set[tuple[str, str, str]] = set()
    for warning in warnings:
        signature = (warning.code, warning.path, warning.message)
        if signature not in seen:
            seen.add(signature)
            unique.append(warning)
    return tuple(unique)


def analyze_numeric_v2_story(story: Mapping[str, Any]) -> tuple[NumericV2AnalysisWarning, ...]:
    """Retain extreme reachability checks and estimate recommended pacing at two points per turn without changing runtime scoring."""
    ordered_nodes, _ = _nodes(story)
    warnings = (*_analyze_numeric_v2_story(story), *_entry_disclosure_gate_warnings(story, ordered_nodes))
    definitions = _metric_definitions(story)
    if not definitions:
        return warnings
    normal_story = deepcopy(dict(story))
    for metric_id, definition in definitions.items():
        # The user's two-point-per-turn estimate sets recommended-turn budgets; do not infer budgets from sampled model intensity.
        # Preserve lower author hard clamps; reuse path, priority, and accumulated-turn analysis without borrowing later scenes' turns.
        normal_story["metric_schema"][metric_id]["per_turn_limit"] = {
            direction: min(2, definition[direction])
            for direction in ("increase", "decrease")
        }
    normal_codes = {"route_pacing_difficult", "route_pacing_partial", "ending_pacing_difficult"}
    normal_warnings = tuple(
        NumericV2AnalysisWarning(
            code=warning.code.replace("_pacing_", "_normal_pacing_"),
            path=warning.path,
            message="按每轮增减2点估算推荐节奏（实际变化仍按行为判定并受单轮限幅约束）：" + warning.message,
        )
        for warning in _analyze_numeric_v2_story(normal_story)
        if warning.code in normal_codes
    )
    return warnings + normal_warnings


__all__ = ["NumericV2AnalysisWarning", "analyze_numeric_v2_story"]
