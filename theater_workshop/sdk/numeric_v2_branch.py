"""Deterministic author contract for ending-first Numeric v2 branches."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Callable, Mapping
import uuid

from .numeric_v2 import (
    _is_actionable_player_exit,
    acting_contract_to_package,
    character_state_to_package,
    scene_turn_budget,
)


_GOAL_DELIVERY_OUTPUTS = {
    "catgirl_dialogue": "performance_dialogue",
    "catgirl_action": "performance_action",
    "environment_fact": "scene_update",
    "player_action": "player_input",
    "shared_agreement": "shared",
    "semantic_state": "evaluator",
}
_GOAL_DELIVERY_OWNERS = {
    "catgirl_dialogue": {"catgirl"},
    "catgirl_action": {"catgirl"},
    "environment_fact": {"environment"},
    "player_action": {"player"},
    "shared_agreement": {"shared"},
    "semantic_state": {"catgirl", "player", "shared"},
}
_GOAL_SOURCE_REFS = {"opening", "player_input", "previous_goal"}
_GOAL_TIMINGS = {"opening", "turn"}
_DIALOGUE_POLICIES = {"required", "optional", "forbidden", "unchanged"}
_ACTING_ENUMS = {
    "cognition_state": {"fresh_boot", "limited", "normal"},
    "memory_state": {"empty", "partial", "available"},
    "self_reference_mode": {"system_neutral", "persona_allowed"},
    "persona_scope": {"style_only", "full"},
    "dialogue_policy": {"required", "optional", "forbidden"},
}


BRANCH_INPUT_TARGET_TOKENS = 12_000
BRANCH_INPUT_HARD_LIMIT = 16_000
_AUTHOR_INPUT_RESERVE_TOKENS = 4_000
_CONTINUITY_ITEMS_PER_SCENE = 2
# D7 的作者侧估算参数：正常一轮按约 2 点推进，单条入口最多允许额外 8 轮。
_NORMAL_METRIC_DELTA = 2
_MAX_EXTRA_TURNS = 8
_MAX_ENTRY_SCENARIOS = 64
_PLACEHOLDER_BAND_LABELS = {
    "低位",
    "中位",
    "高位",
    "当前状态",
    "low",
    "middle",
    "mid",
    "high",
    "current",
}
class NumericV2BranchError(ValueError):
    """A branch source, ending, draft or model result failed the deterministic contract."""

    def __init__(self, code: str, details: Mapping[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = deepcopy(dict(details or {}))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _estimate_tokens(value: Any) -> int:
    """Estimate Chinese conservatively at one token per character; still check actual model capacity before calling it."""

    return len(_canonical(value))


def _opaque_key(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256("\n".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _nodes(story: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node.get("id")): node
        for node in story.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }


def _is_unconditional(route: Mapping[str, Any]) -> bool:
    conditions = route.get("conditions")
    return isinstance(conditions, Mapping) and set(conditions) == {"all"} and conditions.get("all") == []


def condition_and_complement(
    metric_id: str,
    metric: Mapping[str, Any],
    band: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate cumulative conditions toward the target band from initial values, with strict integer complements."""

    minimum = int(metric["min"])
    maximum = int(metric["max"])
    band_min = int(band["min"])
    band_max = int(band["max"])
    initial = int(metric.get("initial", minimum))

    def compare(op: str, value: int) -> dict[str, Any]:
        return {"type": "metric_compare", "metric": metric_id, "op": op, "value": value}

    if band_min > initial:
        # 从较低初始状态向上推进时，更深的关系也应继续满足支线，不能因越过
        # 中间 band 上界而回到原主线。
        branch = {"all": [compare(">=", band_min)]}
        complement = {"all": [compare("<", band_min)]}
    elif band_max < initial:
        # 反向恶化路线同理：下降到目标状态后，继续下降仍属于该方向。
        branch = {"all": [compare("<=", band_max)]}
        complement = {"all": [compare(">", band_max)]}
    elif band_min == minimum:
        branch = {"all": [compare("<=", band_max)]}
        complement = {"all": [compare(">", band_max)]}
    elif band_max == maximum:
        branch = {"all": [compare(">=", band_min)]}
        complement = {"all": [compare("<", band_min)]}
    else:
        branch = {"all": [compare(">=", band_min), compare("<=", band_max)]}
        complement = {"any": [compare("<", band_min), compare(">", band_max)]}
    return branch, complement


class NumericV2BranchService:
    """Validate candidates, context and model results, and construct Story data before atomic application."""

    def __init__(self, id_factory: Callable[[], str] | None = None):
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex[:10])

    def options(self, project: Mapping[str, Any], source_node_id: str) -> dict[str, Any]:
        story = self._story(project)
        nodes = _nodes(story)
        source = nodes.get(source_node_id)
        source_reasons = self._source_reasons(source, nodes)
        candidates = self._condition_candidates(project, source_node_id)
        if not candidates:
            source_reasons.append("branch_metric_required")

        rejoin_targets, mainline_reason = self._rejoin_targets(project, source_node_id)
        endings = []
        ending_rows = {
            str(ending.get("id")): ending
            for ending in story.get("endings") or []
            if isinstance(ending, Mapping)
        }
        for node in story.get("nodes") or []:
            if not isinstance(node, Mapping) or node.get("type") != "ending":
                continue
            ending = ending_rows.get(str(node.get("ending_id"))) or {}
            endings.append({
                "node_id": node.get("id"),
                "ending_id": node.get("ending_id"),
                "title": ending.get("title") or node.get("chapter") or "未命名结局",
                "summary": ending.get("summary") or (node.get("story_beat") or {}).get("summary") or "",
            })
        return {
            "source": {
                "node_id": source_node_id,
                "title": source.get("chapter") if isinstance(source, Mapping) else "",
                "summary": (source.get("story_beat") or {}).get("summary") if isinstance(source, Mapping) else "",
            },
            "eligible": not source_reasons,
            "blocking_reasons": list(dict.fromkeys(source_reasons)),
            "condition_candidates": [self._public_candidate(candidate) for candidate in candidates],
            # 诊断只服务作者侧向导，不写入 Story Package，也不传给 Runtime/Evaluator。
            "pacing_diagnostics": [
                self._public_pacing_diagnostic(candidate)
                for candidate in candidates
                if candidate.get("pacing_diagnostic")
            ],
            "rejoin": {
                "available": not mainline_reason and bool(rejoin_targets),
                "unavailable_reason": mainline_reason,
                "targets": rejoin_targets,
            },
            "existing_endings": endings,
            "lengths": [1, 2, 3],
        }

    def prepare_ending(
        self,
        project: Mapping[str, Any],
        *,
        source_node_id: str,
        ending_direction: str,
        condition_selection: Mapping[str, Any],
    ) -> dict[str, Any]:
        direction = str(ending_direction or "").strip()
        if not direction:
            raise NumericV2BranchError("branch_ending_direction_required")
        source, original_route = self._require_source(project, source_node_id)
        selection, model_candidates = self._normalize_condition_selection(
            project,
            source_node_id,
            condition_selection,
        )
        context = self._base_context(
            project,
            source=source,
            original_route=original_route,
            endpoint={"mode": "new_ending", "requested_result": direction},
            skipped_nodes=[],
            continuity_items=[],
            condition_candidates=model_candidates,
            direction=direction,
            length=None,
        )
        context["condition_selection"] = deepcopy(selection)
        self._require_context_budget(context)
        return {
            "kind": "ending",
            "base_revision": project.get("revision"),
            "context_fingerprint": self._fingerprint(project),
            "source_node_id": source_node_id,
            "original_route_id": original_route.get("id"),
            "original_target_node_id": original_route.get("target_node_id"),
            "ending_direction": direction,
            "condition_selection": selection,
            "condition_candidates": model_candidates,
            "context": context,
            "estimated_input_tokens": _estimate_tokens(context),
        }

    def finish_ending(self, plan: Mapping[str, Any], model_result: Mapping[str, Any]) -> dict[str, Any]:
        ending = self._validate_ending(
            model_result, cast_names=plan["context"]["global"]["intro"],
        )
        condition = self._resolve_condition(plan, model_result)
        ending.pop("condition_key", None)
        return {
            "draft_id": f"branch_draft_{self._id_factory()}",
            "kind": "ending",
            "status": "ending_review",
            "base_revision": plan["base_revision"],
            "context_fingerprint": plan["context_fingerprint"],
            "source_node_id": plan["source_node_id"],
            "original_route_id": plan["original_route_id"],
            "original_target_node_id": plan["original_target_node_id"],
            "ending_direction": plan["ending_direction"],
            "condition": condition,
            "ending": ending,
            "estimated_input_tokens": plan["estimated_input_tokens"],
        }

    def prepare_path(
        self,
        project: Mapping[str, Any],
        *,
        source_node_id: str | None,
        endpoint_mode: str,
        endpoint_node_id: str | None,
        direction: str,
        length: int,
        condition_selection: Mapping[str, Any] | None,
        ending_draft: Mapping[str, Any] | None = None,
        confirmed_ending: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if length not in {1, 2, 3}:
            raise NumericV2BranchError("branch_length_invalid")
        story = self._story(project)
        nodes = _nodes(story)

        if endpoint_mode == "new_ending":
            if not isinstance(ending_draft, Mapping) or ending_draft.get("status") != "ending_review":
                raise NumericV2BranchError("branch_ending_draft_required")
            if ending_draft.get("base_revision") != project.get("revision"):
                raise NumericV2BranchError("branch_draft_stale")
            if ending_draft.get("context_fingerprint") != self._fingerprint(project):
                raise NumericV2BranchError("branch_draft_stale")
            source_node_id = str(ending_draft.get("source_node_id") or "")
            ending = self._validate_ending(confirmed_ending or {}, cast_names=story.get("intro"))
            endpoint = {"mode": "new_ending", "ending": ending}
            selection = {"mode": "fixed", "key": (ending_draft.get("condition") or {}).get("key")}
            model_candidates = [deepcopy(dict(ending_draft.get("condition") or {}))]
            ending_draft_id = ending_draft.get("draft_id")
            author_direction = str(direction or "").strip() or str(ending_draft.get("ending_direction") or "")
            skipped_nodes: list[dict[str, Any]] = []
            continuity_items: list[dict[str, Any]] = []
            endpoint_node_id = None
        else:
            if endpoint_mode not in {"mainline", "existing_ending"}:
                raise NumericV2BranchError("branch_endpoint_mode_invalid")
            author_direction = str(direction or "").strip()
            if not author_direction:
                raise NumericV2BranchError("branch_direction_required")
            selection, model_candidates = self._normalize_condition_selection(
                project,
                str(source_node_id or ""),
                condition_selection or {},
            )
            endpoint = self._resolve_existing_endpoint(
                project,
                endpoint_mode,
                str(endpoint_node_id or ""),
                str(source_node_id or ""),
            )
            ending_draft_id = None
            skipped_nodes = endpoint.pop("skipped_nodes", [])
            continuity_items = endpoint.pop("continuity_items", [])
            allowed_lengths = endpoint.pop("allowed_lengths", [1, 2, 3])
            if length not in allowed_lengths:
                raise NumericV2BranchError(
                    "branch_length_too_short",
                    {"allowed_lengths": allowed_lengths},
                )

        source, original_route = self._require_source(project, str(source_node_id or ""))
        context = self._base_context(
            project,
            source=source,
            original_route=original_route,
            endpoint=endpoint,
            skipped_nodes=skipped_nodes,
            continuity_items=continuity_items,
            condition_candidates=model_candidates,
            direction=author_direction,
            length=length,
        )
        context["condition_selection"] = deepcopy(selection)
        self._require_context_budget(context)
        return {
            "kind": "path",
            "base_revision": project.get("revision"),
            "context_fingerprint": self._fingerprint(project),
            "source_node_id": source_node_id,
            "source_title": source.get("chapter"),
            "original_route_id": original_route.get("id"),
            "original_target_node_id": original_route.get("target_node_id"),
            "endpoint_mode": endpoint_mode,
            "endpoint_node_id": endpoint_node_id,
            "endpoint": endpoint,
            "ending_draft_id": ending_draft_id,
            "condition_selection": selection,
            "condition_candidates": model_candidates,
            "direction": author_direction,
            "length": length,
            "skipped_nodes": skipped_nodes,
            "continuity_items": continuity_items,
            "context": context,
            "estimated_input_tokens": _estimate_tokens(context),
        }

    def finish_path(self, plan: Mapping[str, Any], model_result: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._validate_path_result(plan, model_result)
        condition = self._resolve_condition(plan, model_result)
        scenes = normalized["scenes"]
        transitions = normalized["transitions"]
        continuity = normalized["continuity_handling"]
        continuity_by_key = {item["key"]: item for item in plan["continuity_items"]}
        for handling in continuity:
            item = continuity_by_key[handling["key"]]
            placement = handling["placement"]
            kind, raw_index = placement.split(":", 1)
            index = int(raw_index)
            if kind == "scene":
                goals = scenes[index]["ordered_goals"]
                carried_goal = self._continuity_goal(item, has_previous=bool(goals))
                if not any(goal.get("description") == carried_goal["description"] for goal in goals):
                    goals.append(carried_goal)
                if len(goals) > 6:
                    raise NumericV2BranchError("branch_scene_goal_limit_exceeded")
            else:
                if (item.get("contract") or {}).get("owner") == "player":
                    raise NumericV2BranchError("branch_continuity_placement_invalid")
                # 连续性桥段由 Runtime 原文交付；这里直接合并作者事实，避免再把它
                # 伪装成旧 must_deliver 或让 Actor 临场补写。
                bridge = transitions[index]["bridge_scene_narration"]
                if item["text"] not in bridge:
                    transitions[index]["bridge_scene_narration"] = f"{bridge}{item['text']}"

        metric = condition["metric"]
        branch_conditions, complement = condition_and_complement(
            metric["id"],
            metric,
            condition["band"],
        )
        scene_ids = [f"node_branch_{self._id_factory()}" for _ in scenes]
        route_ids = [f"route_branch_{self._id_factory()}" for _ in range(len(scenes) + 1)]
        ending_node_id = f"ending_node_{self._id_factory()}" if plan["endpoint_mode"] == "new_ending" else None
        ending_id = f"ending_{self._id_factory()}" if ending_node_id else None
        endpoint_title = self._endpoint_title(plan)
        return {
            "draft_id": f"branch_draft_{self._id_factory()}",
            "kind": "path",
            "status": "preview",
            "base_revision": plan["base_revision"],
            "context_fingerprint": plan["context_fingerprint"],
            "source_node_id": plan["source_node_id"],
            "source_title": plan["source_title"],
            "original_route_id": plan["original_route_id"],
            "original_target_node_id": plan["original_target_node_id"],
            "endpoint_mode": plan["endpoint_mode"],
            "endpoint_node_id": plan["endpoint_node_id"],
            "endpoint_title": endpoint_title,
            "ending_draft_id": plan["ending_draft_id"],
            "new_ending": deepcopy(plan["endpoint"].get("ending")),
            "direction": plan["direction"],
            "length": plan["length"],
            "condition": condition,
            "condition_reason": normalized["condition_reason"],
            "branch_conditions": branch_conditions,
            "original_conditions": complement,
            "scenes": scenes,
            "transitions": transitions,
            # 支线节奏诊断只服务作者预览，不写入 Story Package。
            "pacing_diagnostics": self._pacing_diagnostics(scenes),
            "continuity_items": deepcopy(plan["continuity_items"]),
            "continuity_handling": continuity,
            "skipped_nodes": deepcopy(plan["skipped_nodes"]),
            "scene_ids": scene_ids,
            "route_ids": route_ids,
            "ending_node_id": ending_node_id,
            "ending_id": ending_id,
            "estimated_input_tokens": plan["estimated_input_tokens"],
            "created_node_ids": scene_ids + ([ending_node_id] if ending_node_id else []),
        }

    def build_story(
        self,
        project: Mapping[str, Any],
        draft: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
        if draft.get("kind") != "path" or draft.get("status") != "preview":
            raise NumericV2BranchError("branch_draft_not_ready")
        if draft.get("base_revision") != project.get("revision"):
            raise NumericV2BranchError("branch_draft_stale")
        if draft.get("context_fingerprint") != self._fingerprint(project):
            raise NumericV2BranchError("branch_draft_stale")

        story = deepcopy(self._story(project))
        nodes = _nodes(story)
        source = nodes.get(str(draft.get("source_node_id")))
        if not isinstance(source, dict) or len(source.get("route_gates") or []) != 1:
            raise NumericV2BranchError("branch_source_changed")
        original_route = source["route_gates"][0]
        if (
            original_route.get("id") != draft.get("original_route_id")
            or original_route.get("target_node_id") != draft.get("original_target_node_id")
            or not _is_unconditional(original_route)
        ):
            raise NumericV2BranchError("branch_source_changed")

        scene_ids = list(draft.get("scene_ids") or [])
        route_ids = list(draft.get("route_ids") or [])
        scenes = list(draft.get("scenes") or [])
        transitions = list(draft.get("transitions") or [])
        if len(scene_ids) != len(scenes) or len(route_ids) != len(scenes) + 1:
            raise NumericV2BranchError("branch_draft_invalid")
        source_node_id = str(draft.get("source_node_id") or "")
        active_key_props = self._active_key_props(project, source_node_id)
        persisted_key_props = deepcopy(list((project.get("authoring") or {}).get("key_props") or []))

        endpoint_node_id = draft.get("endpoint_node_id")
        if draft.get("endpoint_mode") == "new_ending":
            ending = dict(draft.get("new_ending") or {})
            endpoint_node_id = draft.get("ending_node_id")
            ending_id = draft.get("ending_id")
            if not endpoint_node_id or not ending_id:
                raise NumericV2BranchError("branch_draft_invalid")
            boundaries = [
                str(item).strip()
                for item in (project.get("setup") or {}).get("content_boundaries") or []
                if str(item).strip()
            ]
            story.setdefault("endings", []).append({
                "id": ending_id,
                "title": ending["title"],
                "summary": ending["summary"],
                "terminal": True,
            })
            story.setdefault("nodes", []).append({
                "id": endpoint_node_id,
                "type": "ending",
                "chapter": ending["title"],
                "story_beat": {
                    **({"fixed_narrations": deepcopy(ending["fixed_narrations"])}
                       if "fixed_narrations" in ending else {}),
                    "summary": ending["summary"],
                    "opening_scene": ending["opening_scene"],
                    "goals": self._project_goals(
                        str(endpoint_node_id),
                        ending["ordered_goals"],
                    ),
                    "must_not_happen": boundaries + [
                        f"不得逆转：{item}" for item in ending["irreversible_facts"]
                    ] + list(ending["character_state"]["scene_boundaries"]),
                    "catgirl_situation": self._character_scene_context(
                        ending["character_state"],
                        ending["catgirl_situation"],
                    ),
                    "character_state": character_state_to_package(ending["character_state"]),
                    "acting_contract": acting_contract_to_package(
                        ending["character_state"]["acting_contract"]
                    ),
                    "transition_goal": ending["tone"],
                },
                "route_gates": [],
                "terminal": True,
                "ending_id": ending_id,
            })
        if endpoint_node_id not in _nodes(story):
            raise NumericV2BranchError("branch_endpoint_changed")

        original_route["conditions"] = deepcopy(dict(draft["original_conditions"]))
        original_route["priority"] = 100
        first_transition = deepcopy(dict(transitions[0]))
        first_transition["must_preserve"] = list(dict.fromkeys([
            *first_transition["must_preserve"],
            *self._key_prop_facts(active_key_props),
        ]))
        source["route_gates"].append({
            "id": route_ids[0],
            "target_node_id": scene_ids[0],
            "priority": 200,
            "conditions": deepcopy(dict(draft["branch_conditions"])),
            "transition_contract": self._transition_contract(
                first_transition,
                source_ids=[self._last_goal_fact_id(source)],
            ),
        })
        for index, scene in enumerate(scenes):
            target_id = scene_ids[index + 1] if index + 1 < len(scene_ids) else endpoint_node_id
            projected_goals = self._project_goals(scene_ids[index], scene["ordered_goals"])
            # 分支的 expected_turns 同样只保留在作者诊断中，Runtime 推荐值固定为 3。
            min_turns, recommended_turns = scene_turn_budget(scene["ordered_goals"])
            scene_context = self._character_scene_context(
                scene["character_state"],
                scene["catgirl_situation"],
            )
            # 与主线一致：旧台账不覆盖本幕开场后的显式持物状态；出幕变化仍写入台账与转场。
            scene_prop_facts = [
                f"关键道具“{str(prop.get('name') or '').strip()}”[{str(prop.get('id') or '').strip()}]："
                f"用途为{str(prop.get('purpose') or '').strip()}。"
                for prop in active_key_props
            ]
            if scene_prop_facts:
                scene_context += "\n道具资料（仅定义用途，不表示已取得或操作完成）：" + "；".join(scene_prop_facts)
            changes = list(scene.get("key_prop_state_changes") or [])
            self._append_key_prop_changes(active_key_props, changes, node_id=scene_ids[index])
            self._append_key_prop_changes(persisted_key_props, changes, node_id=scene_ids[index])
            outgoing_transition = deepcopy(dict(transitions[index + 1]))
            outgoing_transition["must_preserve"] = list(dict.fromkeys([
                *outgoing_transition["must_preserve"],
                *self._key_prop_facts(active_key_props),
            ]))
            story["nodes"].append({
                "id": scene_ids[index],
                "type": "scene",
                "chapter": scene["title"],
                "min_turns": min_turns,
                "recommended_turns": recommended_turns,
                "story_beat": {
                    **({"fixed_narrations": deepcopy(scene["fixed_narrations"])}
                       if "fixed_narrations" in scene else {}),
                    "summary": scene["summary"],
                    "opening_scene": scene["opening_scene"],
                    "narrative_focus": scene["narrative_focus"],
                    "goals": projected_goals,
                    "must_not_happen": list(dict.fromkeys([
                        *deepcopy(scene["must_not_happen"]),
                        *scene["character_state"]["scene_boundaries"],
                    ])),
                    "catgirl_situation": scene_context,
                    "character_state": character_state_to_package(scene["character_state"]),
                    "acting_contract": acting_contract_to_package(
                        scene["character_state"]["acting_contract"]
                    ),
                    "transition_goal": scene["transition_goal"],
                },
                "route_gates": [{
                    "id": route_ids[index + 1],
                    "target_node_id": target_id,
                    "priority": 100,
                    "conditions": {"all": []},
                    "transition_contract": self._transition_contract(
                        outgoing_transition,
                        source_ids=[f"goal.{projected_goals[-1]['id']}"],
                    ),
                }],
            })

        condition = draft["condition"]
        route_semantics = {
            route_ids[0]: {
                "generated": True,
                "label": condition["label"],
                "detail": draft.get("condition_reason") or "",
            },
            original_route["id"]: {
                "generated": True,
                "label": f"当不处于“{condition['band_label']}”状态时继续原主线",
                "detail": "由支线触发区间的完整补集确定性生成。",
            },
        }
        return story, route_semantics, persisted_key_props

    def _story(self, project: Mapping[str, Any]) -> dict[str, Any]:
        story = project.get("story")
        if not isinstance(story, dict):
            raise NumericV2BranchError("project_story_required")
        return story

    @staticmethod
    def _source_reasons(source: Any, nodes: Mapping[str, Any]) -> list[str]:
        if not isinstance(source, Mapping):
            return ["branch_source_not_found"]
        if source.get("type") == "ending":
            return ["branch_source_terminal"]
        beat = source.get("story_beat") or {}
        if not str(beat.get("opening_scene") or "").strip() or not isinstance(beat.get("goals"), list):
            return ["branch_v2_1_source_required"]
        routes = [route for route in source.get("route_gates") or [] if isinstance(route, Mapping)]
        reasons = []
        if len(routes) != 1:
            reasons.append("branch_source_single_exit_required")
        elif not _is_unconditional(routes[0]):
            reasons.append("branch_source_unconditional_exit_required")
        elif routes[0].get("target_node_id") not in nodes:
            reasons.append("branch_source_target_missing")
        return reasons

    def _require_source(
        self,
        project: Mapping[str, Any],
        source_node_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        story = self._story(project)
        nodes = _nodes(story)
        source = nodes.get(source_node_id)
        reasons = self._source_reasons(source, nodes)
        if reasons:
            raise NumericV2BranchError(reasons[0])
        if not self._condition_candidates(project, source_node_id):
            raise NumericV2BranchError("branch_metric_required")
        return source, source["route_gates"][0]

    def _condition_candidates(
        self,
        project: Mapping[str, Any],
        source_node_id: str,
    ) -> list[dict[str, Any]]:
        story = self._story(project)
        pacing = self._source_pacing(project, source_node_id)
        candidates: list[dict[str, Any]] = []
        for metric_id, raw_metric in (story.get("metric_schema") or {}).items():
            if not isinstance(raw_metric, Mapping):
                continue
            metric = deepcopy(dict(raw_metric))
            bands = sorted(
                [dict(band) for band in metric.get("bands") or [] if isinstance(band, Mapping)],
                key=lambda band: (band.get("min", 0), band.get("max", 0)),
            )
            if len(bands) < 2 or not self._valid_bands(metric, bands):
                continue
            initial = int(metric.get("initial", metric.get("min", 0)))
            for band in bands:
                label = str(band.get("label") or "").strip()
                if label.casefold() in {item.casefold() for item in _PLACEHOLDER_BAND_LABELS}:
                    continue
                if int(band["min"]) <= initial <= int(band["max"]):
                    state_hint = "默认状态已经处于这一阶段"
                    behavior = "继续按当前关系状态互动即可保持"
                elif int(band["min"]) > initial:
                    state_hint = "默认状态尚未达到，需要持续正向推动"
                    behavior = "；".join(str(item) for item in metric.get("increase_criteria") or [])
                else:
                    state_hint = "默认状态高于这一阶段，需要出现反向变化"
                    behavior = "；".join(str(item) for item in metric.get("decrease_criteria") or [])
                reachability = self._band_reachability(metric, band, pacing)
                pacing_diagnostic = self._pacing_diagnostic(
                    project,
                    source_node_id,
                    metric_id,
                    metric,
                    band,
                )
                if pacing_diagnostic["status"] == "pass":
                    state_hint = f"{state_hint}；按正常变化约需 {pacing_diagnostic['estimated_extra_turns']} 回合"
                elif pacing_diagnostic["status"] == "warning":
                    state_hint = f"{state_hint}；部分前序路线超过 8 回合，可能受前序选择影响"
                else:
                    state_hint = f"{state_hint}；当前前序路线无法在 8 回合内可靠达到"
                key = _opaque_key(
                    "condition",
                    project.get("project_id"),
                    project.get("revision"),
                    metric_id,
                    band["min"],
                    band["max"],
                    label,
                )
                candidates.append({
                    "key": key,
                    "label": self._condition_label(metric, band, initial, label, metric_id),
                    "metric_name": metric.get("name") or metric_id,
                    "band_label": label,
                    "behavior_hint": behavior,
                    "state_hint": state_hint,
                    "reachability": reachability["status"],
                    # D7 以入口情景的 8 回合限制为准；旧 reachability 仅保留作兼容展示。
                    "available": pacing_diagnostic["status"] != "blocked",
                    "pacing_status": pacing_diagnostic["status"],
                    "estimated_extra_turns": pacing_diagnostic.get("estimated_extra_turns"),
                    "entry_scenario_count": len(pacing_diagnostic.get("entry_scenarios") or []),
                    "pacing_warning_codes": list(pacing_diagnostic.get("warning_codes") or []),
                    "pacing_diagnostic": pacing_diagnostic,
                    "metric": {**metric, "id": metric_id},
                    "band": band,
                })
        return candidates

    def _pacing_diagnostic(
        self,
        project: Mapping[str, Any],
        source_node_id: str,
        metric_id: str,
        metric: Mapping[str, Any],
        band: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Estimate metric branches independently for each reachable entrance route and produce author diagnostics."""

        entry = self._entry_scenarios(project, source_node_id, metric_id)
        if entry["unknown_reasons"]:
            # 任一前序无法可靠还原就阻断，避免用猜测覆盖真实路线差异。
            scenarios = [
                {
                    "path": list(item["path"]),
                    "status": "reachable" if item["value"] is not None else "unknown",
                    "estimated_extra_turns": (
                        self._extra_turns_to_band(metric, band, item["value"])
                        if item["value"] is not None
                        else None
                    ),
                }
                for item in entry["scenarios"]
            ]
            return {
                "status": "blocked",
                "warning_codes": ["branch_pacing_entry_unknown"],
                "source_node_id": source_node_id,
                "metric_id": metric_id,
                "band_label": str(band.get("label") or ""),
                "estimated_extra_turns": None,
                "entry_scenarios": scenarios,
                "unknown_reasons": list(entry["unknown_reasons"]),
            }
        if not entry["scenarios"]:
            return {
                "status": "blocked",
                "warning_codes": ["branch_pacing_no_entry"],
                "source_node_id": source_node_id,
                "metric_id": metric_id,
                "band_label": str(band.get("label") or ""),
                "estimated_extra_turns": None,
                "entry_scenarios": [],
                "unknown_reasons": [],
            }

        public_scenarios: list[dict[str, Any]] = []
        estimates: list[int] = []
        for item in entry["scenarios"]:
            try:
                turns = self._extra_turns_to_band(metric, band, item["value"])
            except (KeyError, TypeError, ValueError):
                # 未通过 v2.2 限幅校验的草稿不能被作者侧估算器默认为可达。
                return {
                    "status": "blocked",
                    "warning_codes": ["branch_pacing_entry_unknown"],
                    "source_node_id": source_node_id,
                    "metric_id": metric_id,
                    "band_label": str(band.get("label") or ""),
                    "estimated_extra_turns": None,
                    "entry_scenarios": [],
                    "unknown_reasons": ["metric_limit_invalid"],
                }
            estimates.append(turns)
            public_scenarios.append({
                "path": list(item["path"]),
                "status": "reachable" if turns <= _MAX_EXTRA_TURNS else "unreachable",
                "estimated_extra_turns": turns,
            })
        reachable = [item for item in estimates if item <= _MAX_EXTRA_TURNS]
        if not reachable:
            status = "blocked"
            warning_codes = ["branch_pacing_all_unreachable"]
        elif len(reachable) != len(estimates):
            status = "warning"
            warning_codes = ["branch_pacing_partial_reachability"]
        else:
            status = "pass"
            warning_codes = []
        return {
            "status": status,
            "warning_codes": warning_codes,
            "source_node_id": source_node_id,
            "metric_id": metric_id,
            "band_label": str(band.get("label") or ""),
            "estimated_extra_turns": max(estimates),
            "entry_scenarios": public_scenarios,
            "unknown_reasons": [],
        }

    @staticmethod
    def _extra_turns_to_band(
        metric: Mapping[str, Any],
        band: Mapping[str, Any],
        current_value: int,
    ) -> int:
        """Estimate band entry at about two points per turn, using the actual cap when below two."""

        band_min = int(band["min"])
        band_max = int(band["max"])
        limits = metric.get("per_turn_limit") or {}
        raw_increase = int(limits.get("increase", 0))
        raw_decrease = int(limits.get("decrease", 0))
        if not 1 <= raw_increase <= 5 or not 1 <= raw_decrease <= 5:
            raise ValueError("v2_2_turn_limit_out_of_range")
        increase = min(_NORMAL_METRIC_DELTA, raw_increase)
        decrease = min(_NORMAL_METRIC_DELTA, raw_decrease)
        if current_value < band_min:
            return math.ceil((band_min - current_value) / increase)
        if current_value > band_max:
            return math.ceil((current_value - band_max) / decrease)
        return 0

    def _entry_scenarios(
        self,
        project: Mapping[str, Any],
        source_node_id: str,
        metric_id: str,
    ) -> dict[str, Any]:
        """Enumerate upstream routes to the source scene, preserving representative condition values for each route."""

        story = self._story(project)
        nodes = _nodes(story)
        metric_schema = story.get("metric_schema") or {}
        metric = metric_schema.get(metric_id)
        start_id = str(story.get("start_node_id") or "")
        if not start_id or start_id not in nodes or not isinstance(metric, Mapping):
            return {"scenarios": [], "unknown_reasons": ["start_or_metric_missing"]}
        try:
            initial = int(metric.get("initial", metric.get("min", 0)))
        except (TypeError, ValueError):
            return {"scenarios": [], "unknown_reasons": ["metric_initial_invalid"]}
        scenarios: list[dict[str, Any]] = []
        unknown_reasons: list[str] = []
        stack: list[tuple[str, int, list[str], frozenset[str]]] = [
            (start_id, initial, [], frozenset())
        ]
        while stack:
            node_id, value, path, visited = stack.pop()
            if node_id == source_node_id:
                scenarios.append({"path": path, "value": value})
                continue
            if node_id in visited:
                unknown_reasons.append(f"cycle:{node_id}")
                continue
            node = nodes.get(node_id)
            if not isinstance(node, Mapping):
                unknown_reasons.append(f"node_missing:{node_id}")
                continue
            routes = [route for route in node.get("route_gates") or [] if isinstance(route, Mapping)]
            next_visited = visited | {node_id}
            for route in routes:
                target_id = str(route.get("target_node_id") or "")
                if target_id not in nodes:
                    unknown_reasons.append(f"target_missing:{target_id or node_id}")
                    continue
                alternatives = self._condition_alternatives(
                    route.get("conditions"),
                    metric_schema,
                    metric_id,
                    value,
                )
                if alternatives is None:
                    unknown_reasons.append(f"condition_unknown:{route.get('id') or target_id}")
                    continue
                for next_value in alternatives:
                    if len(scenarios) + len(stack) >= _MAX_ENTRY_SCENARIOS:
                        unknown_reasons.append("entry_scenario_overflow")
                        break
                    stack.append((target_id, next_value, path + [str(route.get("id") or target_id)], next_visited))
        return {
            "scenarios": scenarios,
            "unknown_reasons": list(dict.fromkeys(unknown_reasons)),
        }

    @staticmethod
    def _condition_alternatives(
        conditions: Any,
        metric_schema: Mapping[str, Any],
        metric_id: str,
        current_value: int,
    ) -> list[int] | None:
        """Project all/any metric_compare conditions into interpretable representative entrance values."""

        if not isinstance(conditions, Mapping):
            return None if conditions not in (None, {}) else [current_value]
        if set(conditions) - {"all", "any"}:
            return None
        if "all" in conditions and "any" in conditions:
            return None
        mode = "any" if "any" in conditions else "all"
        rows = conditions.get(mode)
        if not isinstance(rows, list):
            return None
        if not rows:
            return [current_value]
        groups = [[row] for row in rows] if mode == "any" else [rows]
        values: list[int] = []
        for group in groups:
            value = current_value
            checks: list[tuple[str, int]] = []
            for raw in group:
                if not isinstance(raw, Mapping) or raw.get("type") != "metric_compare":
                    return None
                condition_metric = str(raw.get("metric") or "")
                if condition_metric not in metric_schema:
                    return None
                if condition_metric != metric_id:
                    # This solver tracks one metric; another metric is an unknown
                    # constraint, never evidence that the entrance is reachable.
                    return None
                op = str(raw.get("op") or "")
                try:
                    threshold = int(raw["value"])
                except (KeyError, TypeError, ValueError):
                    return None
                if op in {">=", ">", "<=", "<", "=="}:
                    if op == ">=" and value < threshold:
                        value = threshold
                    elif op == ">" and value <= threshold:
                        value = threshold + 1
                    elif op == "<=" and value > threshold:
                        value = threshold
                    elif op == "<" and value >= threshold:
                        value = threshold - 1
                    elif op == "==":
                        value = threshold
                else:
                    return None
                checks.append((op, threshold))
            if any(
                not NumericV2BranchService._condition_holds(value, op, threshold)
                for op, threshold in checks
            ):
                # 当前 any 分支的这一项不可满足时，只淘汰该项，不影响其他入口项。
                continue
            definition = metric_schema.get(metric_id) or {}
            try:
                minimum = int(definition["min"])
                maximum = int(definition["max"])
            except (KeyError, TypeError, ValueError):
                return None
            if not minimum <= value <= maximum:
                # 条件本身数学上不可能满足，只淘汰这条前序路线，不把它误报成未知。
                continue
            values.append(value)
        return values

    @staticmethod
    def _condition_holds(value: int, op: str, threshold: int) -> bool:
        return {
            ">=": value >= threshold,
            ">": value > threshold,
            "<=": value <= threshold,
            "<": value < threshold,
            "==": value == threshold,
        }.get(op, False)

    @staticmethod
    def _public_pacing_diagnostic(diagnostic: Mapping[str, Any]) -> dict[str, Any]:
        """Hide metric thresholds and show authors only route provenance, state and turn estimates."""

        diagnostic = diagnostic.get("pacing_diagnostic") or diagnostic
        return {
            "status": diagnostic.get("status"),
            "warning_codes": list(diagnostic.get("warning_codes") or []),
            "source_node_id": diagnostic.get("source_node_id"),
            "metric_id": diagnostic.get("metric_id"),
            "band_label": diagnostic.get("band_label"),
            "estimated_extra_turns": diagnostic.get("estimated_extra_turns"),
            "entry_scenarios": deepcopy(list(diagnostic.get("entry_scenarios") or [])),
            "unknown_reasons": list(diagnostic.get("unknown_reasons") or []),
        }

    @staticmethod
    def _condition_label(
        metric: Mapping[str, Any],
        band: Mapping[str, Any],
        initial: int,
        band_label: str,
        metric_id: str,
    ) -> str:
        """Show cumulative direction so authors do not mistake intermediate bands for a requirement to remain within the interval."""

        metric_name = metric.get("name") or metric_id
        if int(band["min"]) > initial:
            suffix = "或更深状态" if int(band["max"]) < int(metric["max"]) else "状态"
            return f"当{metric_name}达到“{band_label}”{suffix}"
        if int(band["max"]) < initial:
            suffix = "或更低状态" if int(band["min"]) > int(metric["min"]) else "状态"
            return f"当{metric_name}降至“{band_label}”{suffix}"
        return f"当{metric_name}处于“{band_label}”状态"

    def _source_pacing(
        self,
        project: Mapping[str, Any],
        source_node_id: str,
    ) -> dict[str, int] | None:
        """Count story scenes and theoretical turn budgets already accumulated at the source scene."""

        story = self._story(project)
        nodes = _nodes(story)
        order = list((project.get("authoring") or {}).get("mainline_node_ids") or [])
        if source_node_id not in order:
            return None
        prefix = order[:order.index(source_node_id) + 1]
        if any(node_id not in nodes or nodes[node_id].get("type") == "ending" for node_id in prefix):
            return None
        recommended_turns = 0
        for node_id in prefix:
            node = nodes[node_id]
            minimum = int(node.get("min_turns", 1))
            recommended_turns += max(minimum, int(node.get("recommended_turns", minimum)))
        return {
            # 路线在来源幕完成后判定，因此来源幕本身也属于可积累数值的剧情机会。
            "scene_count": len(prefix),
            "recommended_turns": recommended_turns,
        }

    @staticmethod
    def _band_reachability(
        metric: Mapping[str, Any],
        band: Mapping[str, Any],
        pacing: Mapping[str, int] | None,
    ) -> dict[str, Any]:
        """Distinguish bands reachable at normal pacing, only theoretically reachable, and mathematically unreachable."""

        if pacing is None:
            return {"status": "unknown", "scene_count": 0}
        minimum = int(metric["min"])
        maximum = int(metric["max"])
        initial = int(metric.get("initial", minimum))
        limits = metric.get("per_turn_limit") or {}
        try:
            increase = max(0, int(limits.get("increase", 0)))
            decrease = max(0, int(limits.get("decrease", 0)))
        except (TypeError, ValueError):
            # 草稿尚未通过 v2.2 限幅校验时，只能显示未知，不擅自推导可达性。
            return {"status": "unknown", "scene_count": int((pacing or {}).get("scene_count", 0))}
        scene_count = int(pacing["scene_count"])
        recommended_turns = int(pacing["recommended_turns"])

        # 正常节奏按每幕一次有效数值变化估算；理论范围才假设每回合都达到最大变化。
        practical_min = max(minimum, initial - scene_count * decrease)
        practical_max = min(maximum, initial + scene_count * increase)
        theoretical_min = max(minimum, initial - recommended_turns * decrease)
        theoretical_max = min(maximum, initial + recommended_turns * increase)
        band_min = int(band["min"])
        band_max = int(band["max"])

        def intersects(left: int, right: int) -> bool:
            return band_min <= right and band_max >= left

        if intersects(practical_min, practical_max):
            status = "recommended"
        elif intersects(theoretical_min, theoretical_max):
            status = "difficult"
        else:
            status = "unreachable"
        return {"status": status, "scene_count": scene_count}

    @staticmethod
    def _valid_bands(metric: Mapping[str, Any], bands: list[dict[str, Any]]) -> bool:
        try:
            cursor = int(metric["min"])
            maximum = int(metric["max"])
            for band in bands:
                if int(band["min"]) != cursor or int(band["max"]) < cursor:
                    return False
                if not str(band.get("label") or "").strip():
                    return False
                cursor = int(band["max"]) + 1
            return cursor == maximum + 1
        except (KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def _public_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: deepcopy(candidate[key])
            for key in (
                "key",
                "label",
                "metric_name",
                "band_label",
                "behavior_hint",
                    "state_hint",
                    "reachability",
                    "available",
                    "pacing_status",
                    "estimated_extra_turns",
                    "entry_scenario_count",
                    "pacing_warning_codes",
                )
        }

    def _normalize_condition_selection(
        self,
        project: Mapping[str, Any],
        source_node_id: str,
        raw_selection: Mapping[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        candidates = self._condition_candidates(project, source_node_id)
        mode = str(raw_selection.get("mode") or "")
        if mode == "fixed":
            key = str(raw_selection.get("key") or "")
            selected = next((candidate for candidate in candidates if candidate["key"] == key), None)
            if not selected:
                raise NumericV2BranchError("branch_condition_unknown")
            if selected.get("available") is False:
                raise NumericV2BranchError("branch_condition_unreachable")
            return {"mode": "fixed", "key": key}, [selected]
        if mode == "recommend" and candidates:
            # 自动推荐只看正常节奏可达的 band；作者仍可明确选择“理论可达”的困难条件。
            recommended = [
                candidate
                for candidate in candidates
                if candidate.get("pacing_status") in {"pass", "warning"}
            ]
            if recommended:
                return {"mode": "recommend"}, recommended
            available = [candidate for candidate in candidates if candidate.get("available") is not False]
            if available:
                return {"mode": "recommend"}, available
        raise NumericV2BranchError("branch_condition_required")

    def _resolve_condition(
        self,
        plan: Mapping[str, Any],
        model_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        selection = plan["condition_selection"]
        key = selection.get("key") if selection.get("mode") == "fixed" else model_result.get("condition_key")
        selected = next(
            (candidate for candidate in plan["condition_candidates"] if candidate.get("key") == key),
            None,
        )
        if not selected:
            raise NumericV2BranchError("branch_condition_unknown")
        return deepcopy(dict(selected))

    def _rejoin_targets(
        self,
        project: Mapping[str, Any],
        source_node_id: str,
    ) -> tuple[list[dict[str, Any]], str | None]:
        story = self._story(project)
        nodes = _nodes(story)
        order = list((project.get("authoring") or {}).get("mainline_node_ids") or [])
        if not order or source_node_id not in order:
            return [], "mainline_order_required"
        if any(node_id not in nodes or nodes[node_id].get("type") == "ending" for node_id in order):
            return [], "mainline_order_invalid"
        for index in range(len(order) - 1):
            if not any(
                route.get("target_node_id") == order[index + 1]
                for route in nodes[order[index]].get("route_gates") or []
                if isinstance(route, Mapping)
            ):
                return [], "mainline_order_invalid"
        source_index = order.index(source_node_id)
        targets: list[dict[str, Any]] = []
        for target_index in range(source_index + 1, len(order)):
            target_id = order[target_index]
            skipped, continuity = self._continuity(story, order, source_index, target_index)
            minimum = max(
                1,
                len(skipped),
                math.ceil(len(continuity) / _CONTINUITY_ITEMS_PER_SCENE),
            )
            available = minimum <= 3
            reason = None if available else "branch_continuity_exceeds_three_scenes"
            estimate_context = {
                "source": nodes[source_node_id],
                "target": nodes[target_id],
                "skipped": skipped,
                "continuity": continuity,
            }
            estimate = _estimate_tokens(estimate_context) + _AUTHOR_INPUT_RESERVE_TOKENS
            if estimate > BRANCH_INPUT_HARD_LIMIT:
                available = False
                reason = "branch_context_too_large"
            targets.append({
                "node_id": target_id,
                "title": nodes[target_id].get("chapter") or target_id,
                "summary": (nodes[target_id].get("story_beat") or {}).get("summary") or "",
                "recommended": target_index == source_index + 1,
                "skipped_nodes": skipped,
                "continuity_items": continuity,
                "minimum_length": minimum,
                "allowed_lengths": list(range(minimum, 4)) if available else [],
                "estimated_input_tokens": estimate,
                "available": available,
                "unavailable_reason": reason,
            })
        return targets, None

    def _continuity(
        self,
        story: Mapping[str, Any],
        order: list[str],
        source_index: int,
        target_index: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        nodes = _nodes(story)
        skipped_ids = order[source_index + 1:target_index]
        skipped = [{
            "node_id": node_id,
            "title": nodes[node_id].get("chapter") or node_id,
            "summary": (nodes[node_id].get("story_beat") or {}).get("summary") or "",
        } for node_id in skipped_ids]
        continuity: list[dict[str, Any]] = []
        seen: set[str] = set()
        for node_id in skipped_ids:
            node = nodes[node_id]
            beat = node.get("story_beat") or {}
            goals = beat.get("goals")
            if isinstance(goals, list):
                for index, goal in enumerate(goals):
                    if not isinstance(goal, Mapping):
                        continue
                    description = str(goal.get("description") or "")
                    delivery = goal.get("delivery") or {}
                    evidence = goal.get("evidence") or {}
                    self._append_continuity(
                        continuity,
                        seen,
                        node_id,
                        "goal",
                        index,
                        description,
                        contract={
                            "owner": goal.get("owner"),
                            "delivery_type": delivery.get("type"),
                            "description": description,
                            "evidence_mode": evidence.get("mode"),
                            "anchors": deepcopy(evidence.get("anchors") or []),
                        },
                    )
            else:
                # 旧作者草稿仍可被读取；新支线会把它显式投影成环境目标，
                # 不再把 must_happen 写回新节点。
                for index, text in enumerate(beat.get("must_happen") or []):
                    self._append_continuity(continuity, seen, node_id, "legacy_goal", index, text)
            next_index = order.index(node_id) + 1
            next_id = order[next_index] if next_index < len(order) else ""
            route = next(
                (
                    item for item in node.get("route_gates") or []
                    if isinstance(item, Mapping) and item.get("target_node_id") == next_id
                ),
                None,
            )
            contract = (route or {}).get("transition_contract", {})
            bridge = contract.get("bridge_scene_narration")
            if bridge:
                self._append_continuity(continuity, seen, node_id, "bridge", 0, bridge)
            else:
                for index, text in enumerate(contract.get("must_deliver") or []):
                    self._append_continuity(continuity, seen, node_id, "legacy_bridge", index, text)
        return skipped, continuity

    @staticmethod
    def _append_continuity(
        rows: list[dict[str, Any]],
        seen: set[str],
        node_id: str,
        kind: str,
        index: int,
        raw_text: Any,
        contract: Mapping[str, Any] | None = None,
    ) -> None:
        text = str(raw_text or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        rows.append({
            "key": _opaque_key("continuity", node_id, kind, index, text),
            "text": text,
            "source_node_id": node_id,
            "contract": deepcopy(dict(contract or {})),
        })

    def _resolve_existing_endpoint(
        self,
        project: Mapping[str, Any],
        mode: str,
        endpoint_node_id: str,
        source_node_id: str,
    ) -> dict[str, Any]:
        story = self._story(project)
        nodes = _nodes(story)
        node = nodes.get(endpoint_node_id)
        if not isinstance(node, Mapping):
            raise NumericV2BranchError("branch_endpoint_not_found")
        if mode == "existing_ending":
            if node.get("type") != "ending":
                raise NumericV2BranchError("branch_endpoint_not_ending")
            ending = next(
                (
                    item for item in story.get("endings") or []
                    if isinstance(item, Mapping) and item.get("id") == node.get("ending_id")
                ),
                {},
            )
            return {
                "mode": mode,
                "node_id": endpoint_node_id,
                "title": ending.get("title") or node.get("chapter"),
                "summary": ending.get("summary") or (node.get("story_beat") or {}).get("summary"),
                "story_beat": deepcopy(node.get("story_beat") or {}),
            }

        targets, reason = self._rejoin_targets(project, source_node_id)
        if reason:
            raise NumericV2BranchError(reason)
        target = next((item for item in targets if item["node_id"] == endpoint_node_id), None)
        if not target:
            raise NumericV2BranchError("branch_rejoin_target_invalid")
        if not target["available"]:
            raise NumericV2BranchError(str(target["unavailable_reason"] or "branch_rejoin_target_invalid"))
        return {
            "mode": mode,
            "node_id": endpoint_node_id,
            "title": target["title"],
            "summary": target["summary"],
            "story_beat": deepcopy(node.get("story_beat") or {}),
            "skipped_nodes": deepcopy(target["skipped_nodes"]),
            "continuity_items": deepcopy(target["continuity_items"]),
            "allowed_lengths": list(target["allowed_lengths"]),
        }

    def _upstream_node_ids(self, project: Mapping[str, Any], source_node_id: str) -> set[str]:
        story = self._story(project)
        nodes = _nodes(story)
        reverse: dict[str, set[str]] = {}
        for node_id, node in nodes.items():
            for route in node.get("route_gates") or []:
                if isinstance(route, Mapping):
                    reverse.setdefault(str(route.get("target_node_id") or ""), set()).add(node_id)
        ancestor_ids: set[str] = set()
        stack = [source_node_id]
        while stack:
            node_id = stack.pop()
            if not node_id or node_id in ancestor_ids:
                continue
            ancestor_ids.add(node_id)
            stack.extend(reverse.get(node_id, set()))

        return ancestor_ids

    def _active_key_props(
        self,
        project: Mapping[str, Any],
        source_node_id: str,
    ) -> list[dict[str, Any]]:
        """Expose only prop states already established at the branch source and its upstream nodes."""

        ancestor_ids = self._upstream_node_ids(project, source_node_id)

        mainline_ids = list((project.get("authoring") or {}).get("mainline_node_ids") or [])
        visible_props: list[dict[str, Any]] = []
        for raw_prop in (project.get("authoring") or {}).get("key_props") or []:
            if not isinstance(raw_prop, Mapping):
                continue
            prop = deepcopy(dict(raw_prop))
            states: list[dict[str, Any]] = []
            for raw_state in prop.get("states") or []:
                if not isinstance(raw_state, Mapping):
                    continue
                state = deepcopy(dict(raw_state))
                node_id = str(state.get("node_id") or "")
                chapter_index = state.get("chapter_index")
                if (
                    not node_id
                    and isinstance(chapter_index, int)
                    and not isinstance(chapter_index, bool)
                    and 1 <= chapter_index <= len(mainline_ids)
                ):
                    node_id = str(mainline_ids[chapter_index - 1])
                    state.pop("chapter_index", None)
                    state["node_id"] = node_id
                if node_id in ancestor_ids:
                    states.append(state)
            if states:
                prop["states"] = states
                visible_props.append(prop)
        return visible_props

    def _base_context(
        self,
        project: Mapping[str, Any],
        *,
        source: Mapping[str, Any],
        original_route: Mapping[str, Any],
        endpoint: Mapping[str, Any],
        skipped_nodes: list[dict[str, Any]],
        continuity_items: list[dict[str, Any]],
        condition_candidates: list[dict[str, Any]],
        direction: str,
        length: int | None,
    ) -> dict[str, Any]:
        story = self._story(project)
        public_candidates = [self._public_candidate(candidate) for candidate in condition_candidates]
        target = _nodes(story).get(str(original_route.get("target_node_id"))) or {}
        state_arc = deepcopy((project.get("authoring") or {}).get("character_state_arc") or {})
        # 原普通结局的规划不是新支线的上游事实；已选终点的状态由 endpoint 单独提供。
        state_arc.pop("ending_stage", None)
        upstream_ids = self._upstream_node_ids(project, str(source.get("id") or ""))
        state_arc["stages"] = [
            stage for stage in state_arc.get("stages") or []
            if isinstance(stage, Mapping) and stage.get("node_id") in upstream_ids
        ]
        # 节点完善／修订更新的是 Story；旧大纲不能把已修正的状态重新注入支线。
        nodes = _nodes(story)
        for stage in state_arc["stages"]:
            beat = (nodes.get(stage.get("node_id")) or {}).get("story_beat") or {}
            stage.update(deepcopy(beat.get("character_state") or {}))
            if beat.get("acting_contract"):
                stage["acting_contract"] = deepcopy(beat["acting_contract"])
        return {
            "global": {
                "intro": deepcopy(story.get("intro") or {}),
                "catgirl_binding": deepcopy(story.get("catgirl_binding") or {}),
                "content_boundaries": deepcopy((project.get("setup") or {}).get("content_boundaries") or []),
                # 关系弧只约束支线的关系上限和认知连续性，不进入正式 Story Package。
                "relationship_arc": deepcopy((project.get("authoring") or {}).get("relationship_arc") or {}),
                "character_state_arc": state_arc,
                "key_props": self._active_key_props(project, str(source.get("id") or "")),
            },
            "source": deepcopy(dict(source)),
            "recent_upstream": self._recent_upstream(project, str(source.get("id") or "")),
            "original_exit": {
                "target_title": target.get("chapter"),
                "target_summary": (target.get("story_beat") or {}).get("summary"),
                "transition_contract": deepcopy(original_route.get("transition_contract") or {}),
            },
            "endpoint": deepcopy(dict(endpoint)),
            "skipped_mainline": deepcopy(skipped_nodes),
            "continuity_items": deepcopy(continuity_items),
            "condition_candidates": public_candidates,
            # 把 8 回合作为作者侧软节奏目标传给支线模型，仍不改变运行时门槛。
            "author_intent": {
                "direction": direction,
                "scene_count": length,
                "scene_expected_turns_target": 8,
            },
        }

    def _recent_upstream(self, project: Mapping[str, Any], source_node_id: str) -> list[dict[str, Any]]:
        story = self._story(project)
        nodes = _nodes(story)
        order = list((project.get("authoring") or {}).get("mainline_node_ids") or [])
        if source_node_id not in order:
            return []
        index = order.index(source_node_id)
        result = []
        for node_id in order[max(0, index - 2):index]:
            node = nodes.get(node_id) or {}
            result.append({
                "node_id": node_id,
                "title": node.get("chapter"),
                "summary": (node.get("story_beat") or {}).get("summary"),
                "opening_scene": (node.get("story_beat") or {}).get("opening_scene"),
                "goals": deepcopy((node.get("story_beat") or {}).get("goals") or []),
            })
        return result

    @staticmethod
    def _require_context_budget(context: Mapping[str, Any]) -> None:
        estimate = _estimate_tokens(context)
        if estimate > BRANCH_INPUT_HARD_LIMIT:
            raise NumericV2BranchError(
                "branch_context_too_large",
                {"estimated_input_tokens": estimate, "hard_limit": BRANCH_INPUT_HARD_LIMIT},
            )

    @staticmethod
    def _fingerprint(project: Mapping[str, Any]) -> str:
        payload = {
            "revision": project.get("revision"),
            "story": project.get("story"),
            "mainline_node_ids": (project.get("authoring") or {}).get("mainline_node_ids") or [],
        }
        return f"sha256:{hashlib.sha256(_canonical(payload).encode('utf-8')).hexdigest()}"

    def _validate_ending(
        self, candidate: Mapping[str, Any], *, cast_names: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(candidate, Mapping):
            raise NumericV2BranchError("invalid_branch_ending")
        result = deepcopy(dict(candidate))
        for field in ("title", "summary", "opening_scene", "catgirl_situation", "tone"):
            result[field] = self._required_text(result.get(field), f"ending.{field}")
        result["ordered_goals"] = self._validate_ordered_goals(
            result.get("ordered_goals"),
            path="ending.ordered_goals",
        )
        if len(result["ordered_goals"]) != 1:
            raise NumericV2BranchError("invalid_branch_generation", {"path": "ending.ordered_goals"})
        ending_goal = result["ordered_goals"][0]
        if (
            ending_goal["owner"] != "environment"
            or ending_goal["delivery_type"] != "environment_fact"
            or ending_goal["evidence_mode"] != "semantic"
            or ending_goal["anchors"]
            or ending_goal["sources"] != ["opening"]
            or ending_goal["timing"] != "opening"
            or ending_goal["dialogue_policy_after"] != "unchanged"
        ):
            raise NumericV2BranchError("invalid_branch_generation", {"path": "ending.ordered_goals[0]"})
        result["irreversible_facts"] = self._text_list(
            result.get("irreversible_facts"),
            "ending.irreversible_facts",
            required=True,
        )
        result["character_state"] = self._validate_character_state(
            result.get("character_state"),
            path="ending.character_state",
            cast_names=cast_names,
        )
        allowed = {
            "title",
            "summary",
            "opening_scene",
            "ordered_goals",
            "irreversible_facts",
            "character_state",
            "catgirl_situation",
            "tone",
            "condition_key",
            "fixed_narrations",
        }
        if set(result).difference(allowed):
            raise NumericV2BranchError("invalid_branch_ending")
        return result

    def _validate_path_result(
        self,
        plan: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(candidate, Mapping):
            raise NumericV2BranchError("invalid_branch_generation")
        result = deepcopy(dict(candidate))
        allowed = {"condition_key", "condition_reason", "scenes", "transitions", "continuity_handling"}
        if set(result).difference(allowed):
            raise NumericV2BranchError("invalid_branch_generation")
        result["condition_reason"] = self._required_text(result.get("condition_reason"), "condition_reason")
        available_prop_ids = {
            str(prop.get("id") or "")
            for prop in ((plan.get("context") or {}).get("global") or {}).get("key_props") or []
            if isinstance(prop, Mapping) and str(prop.get("id") or "")
        }
        scenes = result.get("scenes")
        if not isinstance(scenes, list) or len(scenes) != plan["length"]:
            raise NumericV2BranchError("branch_scene_count_mismatch")
        normalized_scenes = []
        for index, raw in enumerate(scenes):
            if not isinstance(raw, Mapping):
                raise NumericV2BranchError("invalid_branch_generation")
            scene = deepcopy(dict(raw))
            for field in ("title", "summary", "opening_scene", "catgirl_situation", "transition_goal"):
                scene[field] = self._required_text(scene.get(field), f"scenes[{index}].{field}")
            # 新输出可显式提供叙事重心；旧草稿缺少时用已有过渡目标回填，保持同一运行时字段。
            scene["narrative_focus"] = self._required_text(
                scene.get("narrative_focus") or scene["transition_goal"],
                f"scenes[{index}].narrative_focus",
            )
            expected_turns = scene.get("expected_turns")
            if expected_turns is not None and (
                not isinstance(expected_turns, int)
                or isinstance(expected_turns, bool)
                or not 3 <= expected_turns <= 120
            ):
                raise NumericV2BranchError(
                    "invalid_branch_generation",
                    {"path": f"scenes[{index}].expected_turns"},
                )
            scene["ordered_goals"] = self._validate_ordered_goals(
                scene.get("ordered_goals"),
                path=f"scenes[{index}].ordered_goals",
            )
            changes = scene.get("key_prop_state_changes", [])
            if not isinstance(changes, list):
                raise NumericV2BranchError(
                    "invalid_branch_generation",
                    {"path": f"scenes[{index}].key_prop_state_changes"},
                )
            normalized_changes: list[dict[str, str]] = []
            changed_ids: set[str] = set()
            for change_index, raw_change in enumerate(changes):
                change_path = f"scenes[{index}].key_prop_state_changes[{change_index}]"
                if not isinstance(raw_change, Mapping) or set(raw_change) != {"id", "owner", "state"}:
                    raise NumericV2BranchError("invalid_branch_generation", {"path": change_path})
                prop_id = str(raw_change.get("id") or "").strip()
                owner = str(raw_change.get("owner") or "").strip()
                state = self._required_text(raw_change.get("state"), f"{change_path}.state")
                if (
                    prop_id not in available_prop_ids
                    or prop_id in changed_ids
                    or owner not in {"catgirl", "player", "environment", "shared"}
                ):
                    raise NumericV2BranchError("invalid_branch_generation", {"path": change_path})
                changed_ids.add(prop_id)
                normalized_changes.append({"id": prop_id, "owner": owner, "state": state})
            scene["key_prop_state_changes"] = normalized_changes
            scene["must_not_happen"] = self._text_list(scene.get("must_not_happen"), "must_not_happen")
            scene["character_state"] = self._validate_character_state(
                scene.get("character_state"),
                path=f"scenes[{index}].character_state",
                cast_names=plan["context"]["global"]["intro"],
            )
            normalized_scenes.append(scene)

        transitions = result.get("transitions")
        if not isinstance(transitions, list) or len(transitions) != plan["length"] + 1:
            raise NumericV2BranchError("branch_transition_count_mismatch")
        normalized_transitions = []
        for index, raw in enumerate(transitions):
            if not isinstance(raw, Mapping):
                raise NumericV2BranchError("invalid_branch_generation")
            transition = deepcopy(dict(raw))
            expected_from = "source" if index == 0 else f"scene:{index - 1}"
            expected_to = "endpoint" if index == plan["length"] else f"scene:{index}"
            if transition.get("from") != expected_from or transition.get("to") != expected_to:
                raise NumericV2BranchError("branch_transition_chain_invalid")
            for field in ("reason", "bridge_scene_narration", "tone"):
                transition[field] = self._required_text(transition.get(field), f"transitions[{index}].{field}")
            transition["must_preserve"] = self._text_list(transition.get("must_preserve"), "must_preserve")
            if index < len(normalized_scenes):
                transition["must_preserve"] = list(dict.fromkeys([
                    *transition["must_preserve"],
                    *normalized_scenes[index]["character_state"]["continuity_from_previous"],
                ]))
            elif isinstance((plan.get("endpoint") or {}).get("ending"), Mapping):
                ending_state = ((plan.get("endpoint") or {}).get("ending") or {}).get("character_state") or {}
                transition["must_preserve"] = list(dict.fromkeys([
                    *transition["must_preserve"],
                    *ending_state.get("continuity_from_previous", []),
                ]))
            normalized_transitions.append(transition)

        handling = result.get("continuity_handling")
        if not isinstance(handling, list):
            raise NumericV2BranchError("branch_continuity_incomplete")
        expected_keys = {item["key"] for item in plan["continuity_items"]}
        seen: set[str] = set()
        normalized_handling = []
        for raw in handling:
            if not isinstance(raw, Mapping):
                raise NumericV2BranchError("branch_continuity_incomplete")
            row = deepcopy(dict(raw))
            key = str(row.get("key") or "")
            placement = str(row.get("placement") or "")
            if key not in expected_keys or key in seen or row.get("mode") != "carried":
                raise NumericV2BranchError("branch_continuity_incomplete")
            if not self._valid_placement(placement, plan["length"]):
                raise NumericV2BranchError("branch_continuity_placement_invalid")
            row["reason"] = self._required_text(row.get("reason"), "continuity.reason")
            seen.add(key)
            normalized_handling.append(row)
        if seen != expected_keys:
            raise NumericV2BranchError("branch_continuity_incomplete")
        return {
            "condition_reason": result["condition_reason"],
            "scenes": normalized_scenes,
            "transitions": normalized_transitions,
            "continuity_handling": normalized_handling,
        }

    @staticmethod
    def _pacing_diagnostics(scenes: list[Mapping[str, Any]]) -> dict[str, Any]:
        """Suggest natural exits and expected expansion length from each branch scene's ordinary goal count."""

        rows: list[dict[str, Any]] = []
        warning_codes: list[str] = []
        for index, scene in enumerate(scenes):
            goals = scene.get("ordered_goals") or []
            turn_goal_count = sum(
                1
                for goal in goals
                if isinstance(goal, Mapping) and str(goal.get("timing") or "turn") == "turn"
            )
            player_exit_goals = [
                goal
                for goal in goals
                if isinstance(goal, Mapping)
                and goal.get("owner") == "player"
                and str(goal.get("timing") or "turn") == "turn"
            ]
            has_player_exit = any(
                _is_actionable_player_exit(goal.get("description"))
                for goal in player_exit_goals
            )
            estimated_turns = max(3, turn_goal_count + 1)
            declared_turns = scene.get("expected_turns")
            if isinstance(declared_turns, int) and not isinstance(declared_turns, bool):
                estimated_turns = max(estimated_turns, declared_turns)
            scene_warnings: list[str] = []
            if not has_player_exit:
                if player_exit_goals:
                    # 填了 player 目标但只写观察/等待时，单独提示作者补成可提交动作。
                    scene_warnings.append("branch_natural_exit_player_action_not_actionable")
                else:
                    scene_warnings.append("branch_natural_exit_player_action_missing")
            if not isinstance(declared_turns, int) or isinstance(declared_turns, bool):
                # 旧支线草稿没有声明值时仍可预览，但不把结构下限伪装成模型估计。
                scene_warnings.append("branch_expected_turns_unknown")
            if estimated_turns > 8:
                scene_warnings.append("branch_expected_turns_exceed_8")
            if estimated_turns > 40:
                scene_warnings.append("branch_expected_turns_exceed_40")
            scene_warnings = list(dict.fromkeys(scene_warnings))
            warning_codes.extend(scene_warnings)
            rows.append({
                "scene_index": index,
                "title": str(scene.get("title") or f"支线第 {index + 1} 幕"),
                "estimated_turns": estimated_turns,
                "expected_turns": declared_turns if isinstance(declared_turns, int) and not isinstance(declared_turns, bool) else None,
                "turn_goal_count": turn_goal_count,
                "natural_exit": {"available": has_player_exit},
                "warning_codes": scene_warnings,
            })
        return {
            "status": "warning" if warning_codes else "pass",
            "soft_limit": 8,
            "scenes": rows,
            "warning_codes": list(dict.fromkeys(warning_codes)),
        }

    @staticmethod
    def _valid_placement(value: str, scene_count: int) -> bool:
        try:
            kind, raw_index = value.split(":", 1)
            index = int(raw_index)
        except (ValueError, TypeError):
            return False
        return (kind == "scene" and 0 <= index < scene_count) or (
            kind == "transition" and 0 <= index <= scene_count
        )

    @staticmethod
    def _required_text(value: Any, path: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise NumericV2BranchError("invalid_branch_generation", {"path": path})
        return text

    @classmethod
    def _text_list(cls, value: Any, path: str, *, required: bool = False) -> list[str]:
        if not isinstance(value, list) or (required and not value):
            raise NumericV2BranchError("invalid_branch_generation", {"path": path})
        rows = [cls._required_text(item, path) for item in value]
        return list(dict.fromkeys(rows))

    @classmethod
    def _validate_character_state(
        cls, value: Any, *, path: str, cast_names: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Branches reuse the mainline state-arc contract; free text must not omit character subjects."""

        if not isinstance(value, Mapping):
            raise NumericV2BranchError("invalid_branch_generation", {"path": path})
        state = deepcopy(dict(value))
        for field, prefix in (
            ("catgirl_state", (cast_names or {}).get("catgirl_name", "女主")),
            ("player_state", (cast_names or {}).get("player_name", "男主")),
            ("environment_state", "环境"),
        ):
            state[field] = cls._required_text(state.get(field), f"{path}.{field}")
            if not state[field].startswith(prefix):
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{path}.{field}"})
        for field in ("continuity_from_previous", "scene_boundaries"):
            state[field] = cls._text_list(
                state.get(field),
                f"{path}.{field}",
                # 连续事实仍必填；没有额外状态限制时允许空边界，避免支线回填凑禁令。
                required=field == "continuity_from_previous",
            )
            if len(state[field]) > 4:
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{path}.{field}"})
        contract = state.get("acting_contract")
        if not isinstance(contract, Mapping):
            raise NumericV2BranchError("invalid_branch_generation", {"path": f"{path}.acting_contract"})
        normalized_contract = deepcopy(dict(contract))
        for field, allowed in _ACTING_ENUMS.items():
            if normalized_contract.get(field) not in allowed:
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{path}.acting_contract.{field}"})
        for field, maximum in (
            ("assertable_self_facts", 8),
            ("allowed_behaviors", 4),
            ("forbidden_behaviors", 4),
        ):
            normalized_contract[field] = cls._text_list(
                normalized_contract.get(field),
                f"{path}.acting_contract.{field}",
            )
            if len(normalized_contract[field]) > maximum:
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{path}.acting_contract.{field}"})
        if normalized_contract["cognition_state"] == "fresh_boot" and (
            normalized_contract["memory_state"] != "empty"
            or normalized_contract["self_reference_mode"] != "system_neutral"
            or normalized_contract["persona_scope"] != "style_only"
            or not normalized_contract["assertable_self_facts"]
        ):
            raise NumericV2BranchError("invalid_branch_generation", {"path": f"{path}.acting_contract"})
        state["acting_contract"] = normalized_contract
        return state

    @staticmethod
    def _character_scene_context(state: Mapping[str, Any], existing: Any) -> str:
        parts = [
            str(state.get("catgirl_state") or "").strip(),
            str(state.get("player_state") or "").strip(),
            str(state.get("environment_state") or "").strip(),
        ]
        state_context = "".join(
            part if part[-1:] in "。！？" else f"{part}。"
            for part in parts
            if part
        )
        context = str(existing or "").strip()
        # 与主线组装一致：仅移除完全相同的状态前缀，保留支线的独立补充和引用。
        while state_context and context.startswith(state_context):
            context = context[len(state_context):].lstrip()
        if context and context[-1:] not in "。！？":
            context += "。"
        return state_context + context

    @staticmethod
    def _key_prop_facts(key_props: list[Mapping[str, Any]]) -> list[str]:
        owner_labels = {
            "catgirl": "女主",
            "player": "男主",
            "environment": "现场",
            "shared": "双方共同",
        }
        facts: list[str] = []
        for prop in key_props:
            states = [state for state in prop.get("states") or [] if isinstance(state, Mapping)]
            if not states:
                continue
            state = states[-1]
            owner = owner_labels.get(str(state.get("owner") or ""), str(state.get("owner") or ""))
            facts.append(
                f"关键道具“{str(prop.get('name') or '').strip()}”[{str(prop.get('id') or '').strip()}]："
                f"用途为{str(prop.get('purpose') or '').strip()}；当前归属为{owner}；"
                f"状态为{str(state.get('state') or '').strip()}。"
            )
        return facts

    @staticmethod
    def _append_key_prop_changes(
        key_props: list[dict[str, Any]],
        changes: list[Mapping[str, Any]],
        *,
        node_id: str,
    ) -> None:
        props_by_id = {str(prop.get("id") or ""): prop for prop in key_props}
        for change in changes:
            prop = props_by_id.get(str(change.get("id") or ""))
            if not isinstance(prop, dict):
                continue
            prop.setdefault("states", []).append({
                "node_id": node_id,
                "owner": str(change.get("owner") or ""),
                "state": str(change.get("state") or ""),
            })

    @staticmethod
    def _transition_contract(
        transition: Mapping[str, Any],
        *,
        source_ids: list[str],
    ) -> dict[str, Any]:
        bridge = str(transition["bridge_scene_narration"])
        return {
            "reason": transition["reason"],
            "bridge_scene_narration": bridge,
            "source_ids": list(dict.fromkeys(source_ids)),
            "must_deliver": [bridge],
            "must_preserve": deepcopy(transition["must_preserve"]),
            "tone": transition["tone"],
        }

    @classmethod
    def _validate_ordered_goals(cls, value: Any, *, path: str) -> list[dict[str, Any]]:
        """Validate explicit responsibilities from branch models without inferring owner or output location from prose."""

        if not isinstance(value, list) or not value or len(value) > 6:
            raise NumericV2BranchError("invalid_branch_generation", {"path": path})
        normalized: list[dict[str, Any]] = []
        opening_goal_count = 0
        for index, raw in enumerate(value):
            goal_path = f"{path}[{index}]"
            if not isinstance(raw, Mapping):
                raise NumericV2BranchError("invalid_branch_generation", {"path": goal_path})
            goal = deepcopy(dict(raw))
            allowed = {
                "owner", "delivery_type", "description", "evidence_mode", "anchors", "sources",
                "timing", "dialogue_policy_after",
            }
            if set(goal).difference(allowed):
                raise NumericV2BranchError("invalid_branch_generation", {"path": goal_path})
            owner = str(goal.get("owner") or "")
            delivery_type = str(goal.get("delivery_type") or "")
            evidence_mode = str(goal.get("evidence_mode") or "")
            if delivery_type not in _GOAL_DELIVERY_OUTPUTS:
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{goal_path}.delivery_type"})
            if owner not in _GOAL_DELIVERY_OWNERS[delivery_type]:
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{goal_path}.owner"})
            goal["description"] = cls._required_text(goal.get("description"), f"{goal_path}.description")
            goal["anchors"] = cls._text_list(goal.get("anchors"), f"{goal_path}.anchors")
            if evidence_mode not in {"exact", "semantic"}:
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{goal_path}.evidence_mode"})
            if evidence_mode == "exact" and not goal["anchors"]:
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{goal_path}.anchors"})
            if evidence_mode == "semantic" and goal["anchors"]:
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{goal_path}.anchors"})
            if delivery_type == "semantic_state" and evidence_mode != "semantic":
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{goal_path}.evidence_mode"})
            sources = goal.get("sources")
            if not isinstance(sources, list) or not sources or len(sources) > 3:
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{goal_path}.sources"})
            if any(
                source not in _GOAL_SOURCE_REFS or (source == "previous_goal" and index == 0)
                for source in sources
            ):
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{goal_path}.sources"})
            goal["owner"] = owner
            goal["delivery_type"] = delivery_type
            goal["evidence_mode"] = evidence_mode
            goal["sources"] = list(dict.fromkeys(str(source) for source in sources))
            timing = str(goal.get("timing") or "turn")
            if timing not in _GOAL_TIMINGS:
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{goal_path}.timing"})
            if timing == "opening":
                opening_goal_count += 1
                if owner not in {"catgirl", "environment"}:
                    raise NumericV2BranchError("invalid_branch_generation", {"path": f"{goal_path}.timing"})
            if owner == "player" and "player_input" not in sources:
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{goal_path}.sources"})
            goal["timing"] = timing
            dialogue_policy = str(goal.get("dialogue_policy_after") or "unchanged")
            if dialogue_policy not in _DIALOGUE_POLICIES:
                raise NumericV2BranchError("invalid_branch_generation", {"path": f"{goal_path}.dialogue_policy_after"})
            goal["dialogue_policy_after"] = dialogue_policy
            normalized.append(goal)
        if opening_goal_count > 1:
            raise NumericV2BranchError("invalid_branch_generation", {"path": path})
        return normalized

    @staticmethod
    def _continuity_goal(item: Mapping[str, Any], *, has_previous: bool) -> dict[str, Any]:
        contract = item.get("contract") if isinstance(item.get("contract"), Mapping) else {}
        owner = str(contract.get("owner") or "environment")
        delivery_type = str(contract.get("delivery_type") or "environment_fact")
        evidence_mode = str(contract.get("evidence_mode") or "semantic")
        anchors = [str(value).strip() for value in contract.get("anchors") or [] if str(value).strip()]
        text = str(item.get("text") or "").strip()
        if evidence_mode == "exact" and not anchors:
            anchors = [text]
        sources = ["player_input"] if delivery_type == "player_action" else [
            "previous_goal" if has_previous else "opening"
        ]
        result = {
            "owner": owner,
            "delivery_type": delivery_type,
            "description": text,
            "evidence_mode": evidence_mode,
            "anchors": anchors,
            "sources": sources,
            "timing": "turn",
            "dialogue_policy_after": "unchanged",
        }
        return result

    @staticmethod
    def _project_goals(node_id: str, ordered_goals: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for index, raw in enumerate(ordered_goals):
            goal = dict(raw)
            goal_id = f"{node_id}_goal_{index + 1:02d}"
            source_ids: list[str] = []
            for source in goal["sources"]:
                if source == "opening":
                    source_ids.append(f"opening.{node_id}")
                elif source == "player_input":
                    source_ids.append("runtime.player_input")
                elif source == "previous_goal":
                    source_ids.append(f"goal.{projected[-1]['id']}")
            delivery_type = str(goal["delivery_type"])
            delivery = {
                "type": delivery_type,
                "output_field": _GOAL_DELIVERY_OUTPUTS[delivery_type],
                "source_ids": list(dict.fromkeys(source_ids)),
                "timing": str(goal.get("timing") or "turn"),
            }
            dialogue_policy = str(goal.get("dialogue_policy_after") or "unchanged")
            if dialogue_policy != "unchanged":
                delivery["state_effects"] = {"dialogue_policy": dialogue_policy}
            projected.append({
                "id": goal_id,
                "owner": goal["owner"],
                "description": goal["description"],
                "evidence": {
                    "mode": goal["evidence_mode"],
                    "anchors": deepcopy(goal["anchors"]),
                },
                "delivery": delivery,
            })
        return projected

    @staticmethod
    def _last_goal_fact_id(node: Mapping[str, Any]) -> str:
        goals = (node.get("story_beat") or {}).get("goals") or []
        if not goals or not isinstance(goals[-1], Mapping) or not goals[-1].get("id"):
            raise NumericV2BranchError("branch_v2_1_source_required")
        return f"goal.{goals[-1]['id']}"

    @staticmethod
    def _endpoint_title(plan: Mapping[str, Any]) -> str:
        endpoint = plan["endpoint"]
        if plan["endpoint_mode"] == "new_ending":
            return endpoint["ending"]["title"]
        return str(endpoint.get("title") or "固定终点")


__all__ = [
    "BRANCH_INPUT_HARD_LIMIT",
    "BRANCH_INPUT_TARGET_TOKENS",
    "NumericV2BranchError",
    "NumericV2BranchService",
    "condition_and_complement",
]
