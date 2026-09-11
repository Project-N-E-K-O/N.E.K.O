from __future__ import annotations
from contextlib import nullcontext
from theater_workshop.host import InProcessPackageGateway

from copy import deepcopy

import pytest

from theater_workshop.sdk.numeric_v2 import NumericV2Compiler
from theater_workshop.sdk.numeric_v2_branch import (
    NumericV2BranchError,
    NumericV2BranchService,
    condition_and_complement,
)
from theater_workshop.sdk.numeric_v2_project_store import NumericV2ProjectStore
from .numeric_v2_fixture import numeric_v2_story


def _unconditional_route(route_id: str, target: str, label: str) -> dict:
    return {
        "id": route_id,
        "target_node_id": target,
        "priority": 100,
        "conditions": {"all": []},
        "transition_contract": {
            "reason": f"剧情自然进入{label}。",
            "bridge_scene_narration": f"夜色推进，调查转入{label}。",
            "source_ids": [],
            "must_deliver": [f"交付进入{label}的线索"],
            "must_preserve": ["保留此前已经成立的关系事实"],
            "tone": "克制",
        },
}


def _ordered_goal(text: str) -> dict:
    return {
        "owner": "catgirl",
        "delivery_type": "catgirl_dialogue",
        "description": text,
        "evidence_mode": "semantic",
        "anchors": [],
        "sources": ["opening"],
        "timing": "turn",
        "dialogue_policy_after": "unchanged",
    }


def _ending_goal(text: str) -> dict:
    return {
        "owner": "environment",
        "delivery_type": "environment_fact",
        "description": text,
        "evidence_mode": "semantic",
        "anchors": [],
        "sources": ["opening"],
        "timing": "opening",
        "dialogue_policy_after": "unchanged",
    }


def test_branch_rejects_player_goal_completed_by_opening():
    goal = _ordered_goal("男主打开封存的证物盒")
    goal.update({
        "owner": "player",
        "delivery_type": "player_action",
        "sources": ["opening"],
        "timing": "opening",
    })

    with pytest.raises(NumericV2BranchError) as caught:
        NumericV2BranchService._validate_ordered_goals(
            [goal],
            path="scenes[0].ordered_goals",
        )

    assert caught.value.details == {
        "path": "scenes[0].ordered_goals[0].timing",
    }


def test_branch_allows_shared_goal_to_follow_previous_goal():
    shared_goal = _ordered_goal("双方同意继续核验下一份证据")
    shared_goal.update({
        "owner": "shared",
        "delivery_type": "shared_agreement",
        "sources": ["previous_goal"],
    })

    goals = NumericV2BranchService._validate_ordered_goals(
        [_ordered_goal("猫娘展示带日期的旧照片"), shared_goal],
        path="scenes[0].ordered_goals",
    )

    assert goals[1]["sources"] == ["previous_goal"]


def test_branch_does_not_match_scene_boundary_prefixes():
    state = _character_state()
    state["scene_boundaries"] = ["避免把女主展示证物的职责转交给男主"]

    normalized = NumericV2BranchService._validate_character_state(
        state,
        path="scenes[0].character_state",
    )

    assert normalized["scene_boundaries"] == [
        "避免把女主展示证物的职责转交给男主"
    ]


def _character_state() -> dict:
    return {
        "catgirl_state": "女主身体状态正常，记得支线来源已经成立的事实。",
        "player_state": "男主身体状态正常，尚未替他预设新的决定。",
        "environment_state": "环境延续当前调查地点和已经出现的证物。",
        "acting_contract": {
            "cognition_state": "normal",
            "memory_state": "available",
            "self_reference_mode": "persona_allowed",
            "persona_scope": "full",
            "dialogue_policy": "required",
            "assertable_self_facts": [],
            "allowed_behaviors": ["核验已经展示的证物"],
            "forbidden_behaviors": ["虚构尚未发生的共同经历"],
        },
        "continuity_from_previous": ["上游已经出现的证物仍在现场"],
        "scene_boundaries": ["不得把女主展示证物的职责转交给男主"],
    }


def _projected_goal(node_id: str, text: str) -> dict:
    return {
        "id": f"{node_id}_goal_01",
        "owner": "catgirl",
        "description": text,
        "evidence": {"mode": "exact", "anchors": [text]},
        "delivery": {
            "type": "catgirl_dialogue",
            "output_field": "performance_dialogue",
            "source_ids": [f"opening.{node_id}"],
            "timing": "turn",
        },
    }


def branchable_project(mainline_count: int = 4) -> dict:
    story = numeric_v2_story()
    mainline_ids = [f"main_{index}" for index in range(1, mainline_count + 1)]
    nodes = []
    for index, node_id in enumerate(mainline_ids):
        target = mainline_ids[index + 1] if index + 1 < len(mainline_ids) else "ending_normal"
        title = f"第{index + 1}幕"
        nodes.append({
            "id": node_id,
            "type": "start" if index == 0 else "scene",
            "chapter": title,
            "min_turns": 2,
            "recommended_turns": 4,
            "story_beat": {
                "summary": f"{title}推进两人的共同调查。",
                "opening_scene": f"{title}的调查现场已经准备就绪。",
                "goals": [_projected_goal(node_id, f"{title}必须发生的事实")],
                "must_not_happen": [],
                "catgirl_situation": f"猫娘正在经历{title}。",
                "transition_goal": "让调查自然推进。",
            },
            "route_gates": [_unconditional_route(f"route_{node_id}", target, target)],
        })
    nodes.append({
        "id": "ending_normal",
        "type": "ending",
        "chapter": "普通结局",
        "story_beat": {
            "summary": "调查档案已经整理完毕，花店重新恢复安静。",
            "opening_scene": "整理完成的调查档案摆在花店桌面上。",
            "goals": [_projected_goal("ending_normal", "猫娘说明已经整理完成的调查结果")],
            "must_not_happen": [],
            "catgirl_situation": "她愿意继续相信玩家。",
            "transition_goal": "温柔收束。",
        },
        "route_gates": [],
        "terminal": True,
        "ending_id": "normal",
    })
    story["start_node_id"] = mainline_ids[0]
    story["meta"]["contract_version"] = "v2.2"
    story["initial_state"]["player_address_known"] = False
    story["nodes"] = nodes
    story["endings"] = [{
        "id": "normal",
        "title": "普通结局",
        "summary": "调查档案已经整理完毕，花店重新恢复安静。",
        "terminal": True,
    }]
    for node in nodes:
        for route in node.get("route_gates") or []:
            route["transition_contract"]["source_ids"] = [
                f"goal.{node['story_beat']['goals'][-1]['id']}"
            ]
    return {
        "project_id": "project_branch_test",
        "revision": 7,
        "setup": {"content_boundaries": ["不使用突然失忆"]},
        "authoring": {
            "mainline_node_ids": mainline_ids,
            "route_semantics": {},
            "branch_drafts": {},
            "key_props": [{
                "id": "sealed_evidence_box",
                "name": "封存的证物盒",
                "purpose": "保存带日期的旧照片",
                "states": [{
                    "node_id": "main_1",
                    "owner": "catgirl",
                    "state": "由女主封存保管",
                }],
            }],
            "character_state_arc": {
                "stages": [
                    {**_character_state(), "node_id": node_id}
                    for node_id in mainline_ids
                ],
                "ending_stage": _character_state(),
            },
        },
        "story": story,
    }


def _path_result(plan: dict) -> dict:
    scenes = [{
        "title": f"支线第{index + 1}幕",
        "summary": "猫娘展示新的证据，让误会转化为共同调查。",
        "opening_scene": "旧仓库的灯光落在一只封存的证物盒上。",
        # 支线作者估算只用于诊断，不能把 Runtime 推荐回合从 3 拉长。
        "expected_turns": 12,
        "ordered_goals": [_ordered_goal("猫娘主动展示带日期的旧照片")],
        "must_not_happen": ["不得提前宣布最终结论"],
        "character_state": _character_state(),
        "catgirl_situation": "她仍然谨慎，但愿意核对证据。",
        "transition_goal": "让两人根据证据走向固定终点。",
    } for index in range(plan["length"])]
    transitions = []
    for index in range(plan["length"] + 1):
        transitions.append({
            "from": "source" if index == 0 else f"scene:{index - 1}",
            "to": "endpoint" if index == plan["length"] else f"scene:{index}",
            "reason": "新证据推动两人继续核对事实。",
            "bridge_scene_narration": "调查暂告一段落，新的核对地点已经亮起灯光。",
            "must_preserve": ["幕后原因尚未被最终确认"],
            "tone": "警惕中逐渐合作",
        })
    handling = [{
        "key": item["key"],
        "mode": "carried",
        "placement": "scene:0",
        "reason": "把原主线事实改由支线现场完成。",
    } for item in plan["continuity_items"]]
    result = {
        "condition_reason": "只有进入愿意托付秘密的状态，这条调查支线才自然成立。",
        "scenes": scenes,
        "transitions": transitions,
        "continuity_handling": handling,
    }
    if plan["condition_selection"]["mode"] == "recommend":
        result["condition_key"] = plan["condition_candidates"][-1]["key"]
    return result


def _path_result_from_context(context: dict) -> dict:
    scene_count = context["author_intent"]["scene_count"]
    result = {
        "condition_reason": "该关系状态让偏离主线的调查自然成立。",
        "scenes": [{
            "title": f"支线第{index + 1}幕",
            "summary": "猫娘主动展示证据并推动调查。",
            "opening_scene": "一只贴有日期标签的证物盒摆在灯下。",
            "ordered_goals": [_ordered_goal("猫娘展示带日期的证据")],
            "must_not_happen": ["不得提前完成固定终点"],
            "character_state": _character_state(),
            "catgirl_situation": "她愿意核对证据，但仍然谨慎。",
            "transition_goal": "让调查走向已经确认的终点。",
        } for index in range(scene_count)],
        "transitions": [{
            "from": "source" if index == 0 else f"scene:{index - 1}",
            "to": "endpoint" if index == scene_count else f"scene:{index}",
            "reason": "证据推动剧情继续。",
            "bridge_scene_narration": "当前核对告一段落，调查转向下一处现场。",
            "must_preserve": ["固定终点尚未提前发生"],
            "tone": "克制",
        } for index in range(scene_count + 1)],
        "continuity_handling": [{
            "key": item["key"],
            "mode": "carried",
            "placement": "scene:0",
            "reason": "在支线现场承接原主线事实。",
        } for item in context["continuity_items"]],
    }
    if context["condition_selection"]["mode"] == "recommend":
        result["condition_key"] = context["condition_candidates"][0]["key"]
    return result


def test_branch_options_hide_raw_thresholds_and_require_exact_rejoin_node():
    project = branchable_project()
    service = NumericV2BranchService()

    options = service.options(project, "main_1")

    assert options["eligible"] is True
    assert options["rejoin"]["targets"][0]["node_id"] == "main_2"
    assert options["rejoin"]["targets"][0]["recommended"] is True
    assert options["rejoin"]["targets"][1]["node_id"] == "main_3"
    assert options["rejoin"]["targets"][1]["skipped_nodes"][0]["node_id"] == "main_2"
    serialized_candidates = str(options["condition_candidates"])
    assert "愿意托付秘密" not in serialized_candidates  # fixture 使用“信赖”这一语义标签
    assert "value" not in serialized_candidates
    assert "min" not in serialized_candidates
    assert "priority" not in serialized_candidates


def test_branch_continuity_goal_defaults_to_semantic_evidence():
    goal = NumericV2BranchService._continuity_goal(
        {"text": "环境保留上游已经出现的证物。"},
        has_previous=False,
    )

    assert goal["delivery_type"] == "environment_fact"
    assert goal["evidence_mode"] == "semantic"
    assert goal["anchors"] == []


def test_branch_pacing_diagnostics_warns_when_scene_has_no_player_exit_action():
    scenes = [{
        "title": "支线幕",
        "ordered_goals": [{"owner": "catgirl", "timing": "turn"}],
    }]

    diagnostics = NumericV2BranchService._pacing_diagnostics(scenes)

    assert diagnostics["status"] == "warning"
    assert "branch_natural_exit_player_action_missing" in diagnostics["scenes"][0]["warning_codes"]


def test_branch_pacing_diagnostics_warns_when_player_exit_is_observer_only():
    scenes = [{
        "title": "支线幕",
        "ordered_goals": [{
            "owner": "player",
            "timing": "turn",
            "description": "无，仅作为观察者见证结局",
        }],
    }]

    diagnostics = NumericV2BranchService._pacing_diagnostics(scenes)

    assert diagnostics["scenes"][0]["natural_exit"]["available"] is False
    assert "branch_natural_exit_player_action_not_actionable" in diagnostics["scenes"][0]["warning_codes"]


def test_branch_pacing_diagnostics_uses_declared_turn_estimate_and_warns_over_forty():
    scenes = [{
        "title": "支线幕",
        "expected_turns": 41,
        "ordered_goals": [{"owner": "player", "timing": "turn"}],
    }]

    diagnostics = NumericV2BranchService._pacing_diagnostics(scenes)

    scene = diagnostics["scenes"][0]
    assert scene["expected_turns"] == 41
    assert scene["estimated_turns"] == 41
    assert "branch_expected_turns_exceed_8" in scene["warning_codes"]
    assert "branch_expected_turns_exceed_40" in scene["warning_codes"]


def test_branch_condition_builds_directional_middle_band_complement():
    metric = {"min": 0, "max": 100, "initial": 20}
    band = {"min": 30, "max": 69, "label": "试探"}

    branch, complement = condition_and_complement("trust", metric, band)

    assert branch == {"all": [
        {"type": "metric_compare", "metric": "trust", "op": ">=", "value": 30},
    ]}
    assert complement == {"all": [
        {"type": "metric_compare", "metric": "trust", "op": "<", "value": 30},
    ]}


def test_branch_condition_keeps_reverse_progress_after_crossing_middle_band():
    metric = {"min": 0, "max": 100, "initial": 80}
    band = {"min": 30, "max": 69, "label": "开始动摇"}

    branch, complement = condition_and_complement("pressure", metric, band)

    assert branch == {"all": [
        {"type": "metric_compare", "metric": "pressure", "op": "<=", "value": 69},
    ]}
    assert complement == {"all": [
        {"type": "metric_compare", "metric": "pressure", "op": ">", "value": 69},
    ]}


def test_branch_conditions_are_ranked_by_source_scene_reachability():
    project = branchable_project(mainline_count=6)
    service = NumericV2BranchService()

    early = service.options(project, "main_1")["condition_candidates"]
    middle = service.options(project, "main_3")["condition_candidates"]
    late = service.options(project, "main_5")["condition_candidates"]

    assert [item["reachability"] for item in early] == [
        "recommended",
        "difficult",
        "unreachable",
    ]
    assert [item["reachability"] for item in middle] == [
        "recommended",
        "recommended",
        "difficult",
    ]
    assert [item["reachability"] for item in late] == [
        "recommended",
        "recommended",
        "difficult",
    ]
    assert early[-1]["available"] is False
    # v2.2 按每轮约 2 点估算；从初始 20 到高位 70 仍需超过 8 回合。
    assert middle[-1]["available"] is False


def test_model_recommendation_only_receives_normally_reachable_conditions():
    project = branchable_project(mainline_count=6)
    plan = NumericV2BranchService().prepare_ending(
        project,
        source_node_id="main_3",
        ending_direction="两人决定共同保管证据。",
        condition_selection={"mode": "recommend"},
    )

    assert len(plan["condition_candidates"]) == 2
    assert {item["reachability"] for item in plan["condition_candidates"]} == {"recommended"}


def test_fixed_condition_rejects_mathematically_unreachable_band():
    project = branchable_project(mainline_count=6)
    service = NumericV2BranchService()
    high = service.options(project, "main_1")["condition_candidates"][-1]

    with pytest.raises(NumericV2BranchError, match="branch_condition_unreachable"):
        service.prepare_ending(
            project,
            source_node_id="main_1",
            ending_direction="两人立即托付最深的秘密。",
            condition_selection={"mode": "fixed", "key": high["key"]},
        )


def test_d7_pacing_keeps_distinct_entry_scenarios_and_warns_on_partial_reachability():
    # 两条前序路线分别把入口代表值落在高位和低位，验证不能压成一个统一入口。
    project = branchable_project(mainline_count=3)
    start = next(node for node in project["story"]["nodes"] if node["id"] == "main_1")
    start["route_gates"] = [
        {
            **_unconditional_route("route_high_entry", "main_2", "高位入口"),
            "conditions": {"all": [{"type": "metric_compare", "metric": "trust", "op": ">=", "value": 70}]},
        },
        {
            **_unconditional_route("route_low_entry", "main_2", "低位入口"),
            "conditions": {"all": [{"type": "metric_compare", "metric": "trust", "op": "<", "value": 10}]},
        },
    ]

    candidate = NumericV2BranchService().options(project, "main_2")["condition_candidates"][-1]

    assert candidate["pacing_status"] == "warning"
    assert candidate["available"] is True
    assert candidate["entry_scenario_count"] == 2
    assert candidate["estimated_extra_turns"] > 8
    assert "branch_pacing_partial_reachability" in candidate["pacing_warning_codes"]


def test_d7_pacing_blocks_when_every_entry_scenario_is_too_slow():
    # 所有入口都需要超过 8 轮时，候选必须阻断而不是继续交给模型。
    project = branchable_project(mainline_count=3)
    start = next(node for node in project["story"]["nodes"] if node["id"] == "main_1")
    start["route_gates"] = [{
        **_unconditional_route("route_low_entry", "main_2", "低位入口"),
        "conditions": {"all": [{"type": "metric_compare", "metric": "trust", "op": "<", "value": 10}]},
    }]

    options = NumericV2BranchService().options(project, "main_2")
    high = options["condition_candidates"][-1]

    assert high["pacing_status"] == "blocked"
    assert high["available"] is False
    assert options["pacing_diagnostics"][-1]["warning_codes"] == ["branch_pacing_all_unreachable"]


def test_d7_pacing_blocks_unknown_cycle_before_source():
    # 无法可靠穿过循环得到来源幕时，诊断必须明确是未知前序而非“困难可达”。
    project = branchable_project(mainline_count=3)
    start = next(node for node in project["story"]["nodes"] if node["id"] == "main_1")
    start["route_gates"] = [{
        **_unconditional_route("route_cycle", "main_1", "循环入口"),
    }]

    options = NumericV2BranchService().options(project, "main_2")

    assert all(item["pacing_status"] == "blocked" for item in options["condition_candidates"])
    assert all(
        "branch_pacing_entry_unknown" in item["pacing_warning_codes"]
        for item in options["condition_candidates"]
    )


def test_late_rejoin_disables_target_when_continuity_cannot_fit_three_scenes():
    project = branchable_project(mainline_count=6)
    service = NumericV2BranchService()

    target = service.options(project, "main_1")["rejoin"]["targets"][-1]

    assert target["node_id"] == "main_6"
    assert target["minimum_length"] > 3
    assert target["available"] is False
    assert target["allowed_lengths"] == []
    assert target["unavailable_reason"] == "branch_continuity_exceeds_three_scenes"


def test_new_branch_ending_does_not_inherit_unrelated_normal_ending_state():
    project = branchable_project()
    project["authoring"]["character_state_arc"]["ending_stage"]["acting_contract"]["dialogue_policy"] = "forbidden"
    project["authoring"]["character_state_arc"]["ending_stage"]["continuity_from_previous"] = ["男主已离开"]
    before = deepcopy(project)
    service = NumericV2BranchService()
    options = service.options(project, "main_3")

    plan = service.prepare_ending(
        project,
        source_node_id="main_3",
        ending_direction="两人在调查现场回应结果，自然收束。",
        condition_selection={"mode": "fixed", "key": options["condition_candidates"][0]["key"]},
    )

    arc = plan["context"]["global"]["character_state_arc"]
    assert "ending_stage" not in arc
    assert arc["stages"] == before["authoring"]["character_state_arc"]["stages"][:3]
    assert plan["context"]["source"] == before["story"]["nodes"][2]
    assert project == before


def test_existing_branch_ending_keeps_selected_endpoint_state():
    project = branchable_project()
    state = _character_state()
    project["story"]["nodes"][-1]["story_beat"]["character_state"] = state
    service = NumericV2BranchService()
    options = service.options(project, "main_3")

    plan = service.prepare_path(
        project, source_node_id="main_3", endpoint_mode="existing_ending",
        endpoint_node_id="ending_normal", direction="调查后回到既定结局。", length=1,
        condition_selection={"mode": "fixed", "key": options["condition_candidates"][0]["key"]},
    )

    assert "ending_stage" not in plan["context"]["global"]["character_state_arc"]
    assert plan["context"]["endpoint"]["story_beat"]["character_state"] == state


def test_branch_context_uses_edited_scene_state_over_original_outline():
    project = branchable_project()
    source = project["story"]["nodes"][2]
    source["story_beat"]["character_state"] = {
        "catgirl_state": "女主已把证物盒放到桌上，尚未交付。",
        "player_state": "男主在桌旁，尚未接取证物盒。",
    }
    source["story_beat"]["acting_contract"] = {"dialogue_policy": "optional"}
    before = deepcopy(project)
    service = NumericV2BranchService()
    options = service.options(project, "main_3")

    plan = service.prepare_ending(
        project, source_node_id="main_3", ending_direction="交接后自然收束。",
        condition_selection={"mode": "fixed", "key": options["condition_candidates"][0]["key"]},
    )

    stage = plan["context"]["global"]["character_state_arc"]["stages"][-1]
    assert stage["player_state"] == source["story_beat"]["character_state"]["player_state"]
    assert stage["acting_contract"] == {"dialogue_policy": "optional"}
    assert project == before


@pytest.mark.parametrize("context_copies", [0, 1, 3])
def test_path_draft_carries_skipped_facts_and_compiles_before_apply(context_copies):
    project = branchable_project()
    service = NumericV2BranchService(id_factory=iter([
        "scene", "r0", "r1", "draft",
    ]).__next__)
    options = service.options(project, "main_3")
    condition_key = options["condition_candidates"][1]["key"]
    plan = service.prepare_path(
        project,
        source_node_id="main_3",
        endpoint_mode="mainline",
        endpoint_node_id="main_4",
        direction="误会让两人转而调查一张旧照片。",
        length=1,
        condition_selection={"mode": "fixed", "key": condition_key},
    )
    assert plan["context"]["global"]["key_props"][0]["id"] == "sealed_evidence_box"

    result = _path_result(plan)
    state_text = "".join(_character_state()[key] for key in (
        "catgirl_state", "player_state", "environment_state",
    ))
    extra = result["scenes"][0]["catgirl_situation"]
    result["scenes"][0]["catgirl_situation"] = state_text * context_copies + extra
    draft = service.finish_path(plan, result)
    story, semantics, key_props = service.build_story(project, draft)
    compiled = NumericV2Compiler(InProcessPackageGateway()).compile(story)

    assert compiled.story["schema"] == "neko.story.numeric.v2"
    branch_scene = next(node for node in story["nodes"] if node["id"] == "node_branch_scene")
    assert branch_scene["story_beat"]["catgirl_situation"].split("\n道具资料")[0] == state_text + extra
    assert branch_scene["min_turns"] == 3
    assert branch_scene["recommended_turns"] == 3
    assert branch_scene["story_beat"]["acting_contract"]["memory_state"] == "available"
    assert branch_scene["story_beat"]["character_state"] == {
        key: value
        for key, value in _character_state().items()
        if key != "acting_contract"
    }
    assert branch_scene["story_beat"]["goals"][-1]["evidence"] == {
        "mode": "semantic",
        "anchors": [],
    }
    for item in plan["continuity_items"]:
        assert item["text"] in [
            goal["description"] for goal in branch_scene["story_beat"]["goals"]
        ]
    assert "must_happen" not in branch_scene["story_beat"]
    assert branch_scene["story_beat"]["opening_scene"]
    source = next(node for node in story["nodes"] if node["id"] == "main_3")
    assert source["route_gates"][0]["conditions"]["all"][0]["op"] == "<"
    assert source["route_gates"][1]["conditions"]["all"][0]["op"] == ">="
    assert semantics["route_branch_r0"]["label"].startswith("当信任度达到")
    assert key_props == project["authoring"]["key_props"]


@pytest.mark.parametrize("opening_transfer", [False, True])
def test_branch_scene_persists_key_prop_state_change_on_stable_node_id(opening_transfer):
    project = branchable_project()
    service = NumericV2BranchService(id_factory=iter([
        "scene", "r0", "r1", "draft",
    ]).__next__)
    options = service.options(project, "main_3")
    plan = service.prepare_path(
        project,
        source_node_id="main_3",
        endpoint_mode="mainline",
        endpoint_node_id="main_4",
        direction="调查结束后由双方共同保管证物盒。",
        length=1,
        condition_selection={"mode": "fixed", "key": options["condition_candidates"][0]["key"]},
    )
    result = _path_result(plan)
    if opening_transfer:
        result["scenes"][0]["character_state"]["catgirl_state"] = "女主已在开场将证物盒放到双方之间的台面。"
    else:
        result["scenes"][0]["key_prop_state_changes"] = [{
            "id": "sealed_evidence_box",
            "owner": "shared",
            "state": "盒盖已经开启，带日期的旧照片由双方共同保管",
        }]

    draft = service.finish_path(plan, result)
    story, _, key_props = service.build_story(project, draft)

    branch_node = next(node for node in story["nodes"] if node["id"] == "node_branch_scene")
    # 本幕的显式状态可能已经在开场改变持有者；旧道具台账不能覆盖入幕状态。
    assert "仅定义用途，不表示已取得或操作完成" in branch_node["story_beat"]["catgirl_situation"]
    assert "当前归属为女主" not in branch_node["story_beat"]["catgirl_situation"]
    assert "保存带日期的旧照片" in branch_node["story_beat"]["catgirl_situation"]
    assert branch_node["story_beat"]["character_state"]["catgirl_state"] == result["scenes"][0]["character_state"]["catgirl_state"]
    if opening_transfer:
        assert key_props == project["authoring"]["key_props"]
    else:
        assert "当前归属为双方共同" in branch_node["route_gates"][0]["transition_contract"]["must_preserve"][-1]
        assert key_props[0]["states"][-1] == {
            "node_id": "node_branch_scene",
            "owner": "shared",
            "state": "盒盖已经开启，带日期的旧照片由双方共同保管",
        }


def test_guided_path_rejects_intentionally_replaced_continuity():
    project = branchable_project()
    service = NumericV2BranchService()
    options = service.options(project, "main_1")
    plan = service.prepare_path(
        project,
        source_node_id="main_1",
        endpoint_mode="mainline",
        endpoint_node_id="main_3",
        direction="误会让两人调查旧照片。",
        length=1,
        condition_selection={"mode": "fixed", "key": options["condition_candidates"][0]["key"]},
    )
    result = _path_result(plan)
    result["continuity_handling"][0]["mode"] = "intentionally_replaced"

    with pytest.raises(NumericV2BranchError, match="branch_continuity_incomplete"):
        service.finish_path(plan, result)


@pytest.mark.parametrize(
    "value",
    [
        "男主在打烊后向女主展示一份商业计划。",
        "女主强迫男主在契约上签字。",
    ],
)
def test_guided_path_does_not_regex_classify_opening_events(value):
    project = branchable_project()
    service = NumericV2BranchService()
    options = service.options(project, "main_3")
    plan = service.prepare_path(
        project,
        source_node_id="main_3",
        endpoint_mode="mainline",
        endpoint_node_id="main_4",
        direction="新证据引出另一条调查路线。",
        length=1,
        condition_selection={"mode": "fixed", "key": options["condition_candidates"][0]["key"]},
    )
    result = _path_result(plan)
    result["scenes"][0]["opening_scene"] = value

    draft = service.finish_path(plan, result)

    assert draft["scenes"][0]["opening_scene"] == value


def test_guided_path_does_not_regex_classify_transition_delivery():
    project = branchable_project()
    service = NumericV2BranchService()
    options = service.options(project, "main_3")
    plan = service.prepare_path(
        project,
        source_node_id="main_3",
        endpoint_mode="mainline",
        endpoint_node_id="main_4",
        direction="新证据引出另一条调查路线。",
        length=1,
        condition_selection={"mode": "fixed", "key": options["condition_candidates"][0]["key"]},
    )
    result = _path_result(plan)
    result["transitions"][0]["bridge_scene_narration"] = "男主进入旧仓库并打开暗门。"

    draft = service.finish_path(plan, result)

    assert draft["transitions"][0]["bridge_scene_narration"] == "男主进入旧仓库并打开暗门。"


def test_guided_path_does_not_use_text_similarity_as_transition_validation():
    project = branchable_project()
    service = NumericV2BranchService()
    options = service.options(project, "main_3")
    plan = service.prepare_path(
        project,
        source_node_id="main_3",
        endpoint_mode="mainline",
        endpoint_node_id="main_4",
        direction="新证据引出另一条调查路线。",
        length=1,
        condition_selection={"mode": "fixed", "key": options["condition_candidates"][0]["key"]},
    )
    result = _path_result(plan)
    result["transitions"][0]["bridge_scene_narration"] = result["scenes"][0]["opening_scene"]

    draft = service.finish_path(plan, result)

    assert draft["transitions"][0]["bridge_scene_narration"] == result["scenes"][0]["opening_scene"]


@pytest.mark.parametrize("context_copies", [0, 1, 3])
def test_new_ending_is_confirmed_before_path_and_uses_resolved_condition(context_copies):
    project = branchable_project()
    values = iter(["ending-draft", "scene", "r0", "r1", "end-node", "end", "path-draft"])
    service = NumericV2BranchService(id_factory=values.__next__)
    ending_plan = service.prepare_ending(
        project,
        source_node_id="main_3",
        ending_direction="两人接受真相有代价，但决定共同承担。",
        condition_selection={"mode": "recommend"},
    )
    chosen_key = ending_plan["condition_candidates"][1]["key"]
    state_text = "".join(_character_state()[key] for key in (
        "catgirl_state", "player_state", "environment_state",
    ))
    ending_draft = service.finish_ending(ending_plan, {
        "condition_key": chosen_key,
        "title": "共同承担的清晨",
        "summary": "真相已经公开，相关后果也摆在所有人面前。",
        "opening_scene": "公开后的清晨，保存多年的证据摆在桌面上。",
        "ordered_goals": [_ending_goal("结局开场已经展示真相公开和两人共同承担后果")],
        "irreversible_facts": ["真相已经由两人共同公开"],
        "character_state": _character_state(),
        "catgirl_situation": state_text * context_copies + "她不再独自保护秘密。",
        "tone": "克制而坚定",
    })
    confirmed = deepcopy(ending_draft["ending"])
    confirmed["title"] = "作者确认后的结局"
    path_plan = service.prepare_path(
        project,
        source_node_id=None,
        endpoint_mode="new_ending",
        endpoint_node_id=None,
        direction="",
        length=1,
        condition_selection=None,
        ending_draft=ending_draft,
        confirmed_ending=confirmed,
    )

    assert path_plan["condition_selection"] == {"mode": "fixed", "key": chosen_key}
    draft = service.finish_path(path_plan, _path_result(path_plan))
    story, _, _ = service.build_story(project, draft)
    NumericV2Compiler(InProcessPackageGateway()).compile(story)
    branch_node = next(node for node in story["nodes"] if node["id"] == "node_branch_scene")
    assert "上游已经出现的证物仍在现场" in branch_node["route_gates"][0]["transition_contract"]["must_preserve"]
    assert any(ending["title"] == "作者确认后的结局" for ending in story["endings"])
    ending_node = next(
        node for node in story["nodes"]
        if node["id"] == draft["ending_node_id"]
    )
    assert ending_node["story_beat"]["catgirl_situation"] == state_text + "她不再独自保护秘密。"
    assert ending_node["story_beat"]["goals"] == [{
        "id": f"{draft['ending_node_id']}_goal_01",
        "owner": "environment",
        "description": "结局开场已经展示真相公开和两人共同承担后果",
        "evidence": {"mode": "semantic", "anchors": []},
        "delivery": {
            "type": "environment_fact",
            "output_field": "scene_update",
            "source_ids": [f"opening.{draft['ending_node_id']}"],
            "timing": "opening",
        },
    }]


def test_placeholder_bands_block_model_generation_candidates():
    project = branchable_project()
    for index, band in enumerate(project["story"]["metric_schema"]["trust"]["bands"]):
        band["label"] = ["低位", "中位", "高位"][index]

    options = NumericV2BranchService().options(project, "main_1")

    assert options["eligible"] is False
    assert options["condition_candidates"] == []
    assert "branch_metric_required" in options["blocking_reasons"]


def test_branch_draft_persists_without_revision_and_applies_idempotently(tmp_path):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    created = store.create()
    project_data = branchable_project()
    generated = store.finish_generation(
        created["project_id"],
        base_revision=created["revision"],
        story=project_data["story"],
        mainline_node_ids=project_data["authoring"]["mainline_node_ids"],
    )
    draft = {
        "draft_id": "branch_draft_test",
        "kind": "path",
        "status": "preview",
        "base_revision": generated["revision"],
    }

    saved = store.save_branch_draft(
        created["project_id"],
        base_revision=generated["revision"],
        draft=draft,
    )
    reopened = store.get(created["project_id"])

    assert saved["status"] == "preview"
    assert reopened["revision"] == generated["revision"]
    assert reopened["authoring"]["mainline_node_ids"] == ["main_1", "main_2", "main_3", "main_4"]

    first = store.commit_branch_draft(
        created["project_id"],
        draft_id=draft["draft_id"],
        base_revision=generated["revision"],
        story=generated["story"],
        route_semantics={"route_main_1": {"generated": True, "label": "语义条件"}},
        key_props=generated["authoring"]["key_props"],
    )
    second = store.commit_branch_draft(
        created["project_id"],
        draft_id=draft["draft_id"],
        base_revision=generated["revision"],
        story=generated["story"],
        route_semantics={},
        key_props=generated["authoring"]["key_props"],
    )

    assert first["revision"] == generated["revision"] + 1
    assert second["revision"] == first["revision"]
    assert second["authoring"]["branch_drafts"][draft["draft_id"]]["status"] == "applied"








@pytest.mark.parametrize("boundaries", [[], ["不得把女主展示证物的职责转交给男主"]])
def test_branch_api_and_service_preserve_state_boundary_scope(boundaries):
    # API 接收、支线归一化共用允许空数组的合同，已有硬边界仍逐字保留。
    from theater_workshop.sdk.contracts import BranchCharacterStatePayload
    state = _character_state()
    state["scene_boundaries"] = boundaries
    payload = BranchCharacterStatePayload.model_validate(state).model_dump()
    normalized = NumericV2BranchService._validate_character_state(payload, path="state")
    assert normalized["scene_boundaries"] == boundaries
    assert normalized["continuity_from_previous"] == state["continuity_from_previous"]


@pytest.mark.parametrize("boundaries", [None, [f"不得越界{i}" for i in range(5)]])
def test_branch_boundary_relaxation_keeps_shape_and_upper_limit(boundaries):
    from pydantic import ValidationError
    from theater_workshop.sdk.contracts import BranchCharacterStatePayload
    # 两层都拒绝非法形状与超长数组；不是依靠下一层兜住接口漏验。
    state = _character_state()
    state["scene_boundaries"] = boundaries
    with pytest.raises(ValidationError):
        BranchCharacterStatePayload.model_validate(state)
    with pytest.raises(NumericV2BranchError):
        NumericV2BranchService._validate_character_state(state, path="state")


@pytest.mark.parametrize("mode", ["all", "any"])
def test_entrance_projection_does_not_ignore_untracked_metric(mode):
    schema = {"trust": {"min": 0, "max": 100}, "courage": {"min": 0, "max": 10}}
    conditions = {mode: [{"type": "metric_compare", "metric": "courage", "op": ">=", "value": 20}]}
    assert NumericV2BranchService._condition_alternatives(conditions, schema, "trust", 0) is None
    conditions[mode][0].update(metric="trust", value=20)
    assert NumericV2BranchService._condition_alternatives(conditions, schema, "trust", 0) == [20]
