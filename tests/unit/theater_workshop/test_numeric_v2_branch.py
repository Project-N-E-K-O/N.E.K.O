from __future__ import annotations
from contextlib import nullcontext
from services.theater_workshop.host import InProcessPackageGateway

from copy import deepcopy

import pytest

from services.theater_workshop.sdk.numeric_v2 import NumericV2Compiler
from services.theater_workshop.sdk.numeric_v2_branch import (
    NumericV2BranchError,
    NumericV2BranchService,
    condition_and_complement,
)
from services.theater_workshop.sdk.numeric_v2_project_store import NumericV2ProjectStore
from .numeric_v2_fixture import numeric_v2_story


@pytest.mark.parametrize('has_previous', [False, True])
def test_carried_player_semantic_goal_requires_player_input(has_previous):
    goal = NumericV2BranchService._continuity_goal({
        'text': '玩家确认线索。',
        'contract': {'owner': 'player', 'delivery_type': 'semantic_state', 'evidence_mode': 'semantic'},
    }, has_previous=has_previous)
    assert goal['sources'] == ['player_input']
    NumericV2BranchService._validate_ordered_goals([goal], path='goals')


def test_entry_scenarios_prune_downstream_diamonds(monkeypatch):
    project = branchable_project()
    nodes = project['story']['nodes']
    start = next(node for node in nodes if node['id'] == 'main_1')
    start['route_gates'].append(_unconditional_route('downstream', 'diamond_0', '下游'))
    for index in range(18):
        nodes.append({'id': f'diamond_{index}', 'route_gates': [
            _unconditional_route(f'left_{index}', f'left_{index}', '左侧'),
            _unconditional_route(f'right_{index}', f'right_{index}', '右侧')]})
        for side in ('left', 'right'):
            nodes.append({'id': f'{side}_{index}', 'route_gates': [
                _unconditional_route(f'join_{side}_{index}', f'diamond_{index + 1}', '合流')]})
    nodes.append({'id': 'diamond_18', 'route_gates': []})
    service = NumericV2BranchService()
    original = service._condition_alternatives
    calls = []

    def counted(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(service, '_condition_alternatives', counted)
    result = service._entry_scenarios(project, 'main_2', 'trust')
    assert result['scenarios']
    assert len(calls) == 1


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
        # Author branch estimates are diagnostic only; they must not extend Runtime's recommended three turns.
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
            **({
                "fallback_offer": "如果你愿意，我们带着这份证据继续核对下一处记录。",
                "accept_input": "好，我们带着这份证据继续核对下一处记录。",
            } if not (
                index == plan["length"] and plan.get("endpoint_mode") != "mainline"
            ) else {}),
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
            **({
                "fallback_offer": "如果你愿意，我们带着这份证据继续核对下一处现场。",
                "accept_input": "好，我们带着这份证据继续核对下一处现场。",
            } if not (
                index == scene_count
                and (context.get("endpoint") or {}).get("mode") != "mainline"
            ) else {}),
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


def test_fresh_branch_rejects_pseudo_fact_target_and_instruction_in_accept_input():
    import json

    from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2GenerationError, NumericV2Generator

    context = {"author_intent": {"scene_count": 1}, "continuity_items": [],
               "condition_selection": {"mode": "fixed"}}
    candidate = _path_result_from_context(context)
    candidate["scenes"][0]["completion_facts"] = [{
        "id": "choice", "description": "玩家已作选择。",
        "value_type": "string", "target_value": "any_non_empty", "visibility": "public",
    }]
    candidate["transitions"][0]["accept_input"] = "明确选择全拓、只挑部分或暂缓"
    candidate["transitions"][1]["trigger_fact_ids"] = ["choice"]
    generator = NumericV2Generator(lambda *_a, **_k: json.dumps(candidate, ensure_ascii=False))
    with pytest.raises(NumericV2GenerationError, match="invalid_model_json") as caught:
        generator.generate_branch_path(context=context)
    assert {(issue["code"], issue["path"]) for issue in caught.value.issues} == {
        ("completion_fact_target_placeholder", "scenes[0].completion_facts[0]"),
        ("transition_accept_input_placeholder", "transitions[0].accept_input"),
    }


@pytest.mark.parametrize("ambiguous", [False, True])
def test_branch_path_resolves_author_fixed_narration_from_original_direction(ambiguous):
    import json
    from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2GenerationError, NumericV2Generator

    source = "第一行：钟停了。\n第二行：编号003。"
    marked = "[原文起]" + source + "[原文止]"
    direction = marked * (2 if ambiguous else 1)
    context = {"author_intent": {"scene_count": 1, "direction": direction}, "continuity_items": [],
               "condition_selection": {"mode": "fixed"}}
    candidate = _path_result_from_context(context)
    candidate["scenes"][0]["fixed_narrations"] = [{
        "id": "author_log", "text_source": {"start_after": "[原文起]", "end_before": "[原文止]"},
        "trigger": {"type": "entry"}, "after": [], "required_before_exit": False,
    }]
    candidate["scenes"][0]["completion_facts"] = [{
        "id": "read_log", "description": "程序已播放作者原文。", "value_type": "bool",
        "target_value": True, "visibility": "public", "fixed_narration_id": "author_log",
    }]
    candidate["transitions"][1]["trigger_fact_ids"] = ["read_log"]

    def model(messages, **options):
        assert options["operation"] == "numeric_v2_branch_path"
        assert "不能用 exact anchors 代替" in messages[0]["content"]
        return json.dumps(candidate, ensure_ascii=False)

    generator = NumericV2Generator(model)
    if ambiguous:
        with pytest.raises(NumericV2GenerationError) as exc:
            generator.generate_branch_path(context=context)
        assert exc.value.issues[0]["path"] == "scenes[0].fixed_narrations[0].text_source"
        return
    fixed, = generator.generate_branch_path(context=context)["scenes"][0]["fixed_narrations"]
    assert fixed["text"] == source
    assert "text_source" not in fixed


@pytest.mark.parametrize("already_in_ending", [False, True])
def test_branch_path_rejects_missing_marked_original_unless_fixed_endpoint_owns_it(already_in_ending):
    import json
    from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2GenerationError, NumericV2Generator

    source = "钟停在零点。"
    context = {"author_intent": {"scene_count": 1, "direction": f"逐字展示[原文起]{source}[原文止]"},
               "continuity_items": [], "condition_selection": {"mode": "fixed"}}
    if already_in_ending:
        context["endpoint"] = {"mode": "new_ending", "ending": {"fixed_narrations": [{"text": source}]}}
    candidate = _path_result_from_context(context)
    candidate["scenes"][0]["completion_facts"] = [{
        "id": "log", "description": "猫娘展示带日期的证据。", "value_type": "bool",
        "target_value": True, "visibility": "public",
    }]
    candidate["transitions"][1]["trigger_fact_ids"] = ["log"]
    generator = NumericV2Generator(lambda messages, **options: json.dumps(candidate, ensure_ascii=False))
    if already_in_ending:
        assert generator.generate_branch_path(context=context) == candidate
    else:
        with pytest.raises(NumericV2GenerationError) as exc:
            generator.generate_branch_path(context=context)
        assert exc.value.issues[0]["code"] == "author_passages_missing"


@pytest.mark.parametrize("omitted", [False, True])
def test_new_branch_ending_requires_marked_original_and_sends_direction_once(omitted):
    import json
    from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2GenerationError, NumericV2Generator

    source = "铭牌上刻着编号和公开日期。"
    direction = f"支线结局入幕展示[原文起]{source}[原文止]"
    service = NumericV2BranchService(id_factory=lambda: "marked-ending")
    project = branchable_project()
    plan = service.prepare_ending(project, source_node_id="main_3", ending_direction=direction,
                                  condition_selection={"mode": "recommend"})
    candidate = {
        "condition_key": plan["condition_candidates"][0]["key"], "title": "清晨",
        "summary": "证据已经公开。", "opening_scene": "保存多年的证据摆在桌上。",
        "ordered_goals": [_ending_goal("结局开场已经展示证据公开")],
        "irreversible_facts": ["证据已经公开"], "character_state": _character_state(),
        "catgirl_situation": "她不再独自保护秘密。", "tone": "克制而坚定",
    }
    if not omitted:
        candidate["fixed_narrations"] = [{
            "id": "plaque", "text_source": {"start_after": "[原文起]", "end_before": "[原文止]"},
            "trigger": {"type": "entry"}, "after": [], "required_before_exit": False,
        }]

    def model(messages, **options):
        assert messages[1]["content"].count(direction) == 1
        return json.dumps(candidate, ensure_ascii=False)

    generator = NumericV2Generator(model)
    if omitted:
        with pytest.raises(NumericV2GenerationError) as exc:
            generator.generate_branch_ending(context=plan["context"])
        assert exc.value.issues[0]["code"] == "author_passages_missing"
    else:
        draft = service.finish_ending(plan, generator.generate_branch_ending(context=plan["context"]))
        assert draft["ending"]["fixed_narrations"][0]["text"] == source
        path_args = dict(source_node_id="main_3", endpoint_mode="new_ending", endpoint_node_id=None,
                         length=1, condition_selection=None, ending_draft=draft, confirmed_ending=draft["ending"])
        path = service.prepare_path(project, direction="", **path_args)
        branch = _path_result_from_context(path["context"])
        branch["scenes"][0]["fixed_narrations"] = deepcopy(draft["ending"]["fixed_narrations"])
        branch["scenes"][0]["completion_facts"] = [{
            "id": "log", "description": "程序已展示作者原文。", "value_type": "bool",
            "target_value": True, "visibility": "public", "fixed_narration_id": "plaque",
        }]
        branch["transitions"][1]["trigger_fact_ids"] = ["log"]
        assert json.dumps(path["context"], ensure_ascii=False).count(source) == 1
        path_generator = NumericV2Generator(lambda messages, **options: json.dumps(branch, ensure_ascii=False))
        with pytest.raises(NumericV2GenerationError) as exc:
            path_generator.generate_branch_path(context=path["context"])
        assert exc.value.issues[0]["code"] == "branch_endpoint_original_repeated"

        explicit = service.prepare_path(project, direction=f"第一幕也逐字展示[原文起]{source}[原文止]", **path_args)
        piece = branch["scenes"][0]["fixed_narrations"][0]
        piece.pop("text")
        piece["text_source"] = {"start_after": "[原文起]", "end_before": "[原文止]"}
        resolved = path_generator.generate_branch_path(context=explicit["context"])
        assert resolved["scenes"][0]["fixed_narrations"][0]["text"] == source
        branch["transitions"][1]["bridge_scene_narration"] += source
        with pytest.raises(NumericV2GenerationError) as exc:
            path_generator.generate_branch_path(context=explicit["context"])
        assert exc.value.issues[0]["path"] == "transitions[1].bridge_scene_narration"


@pytest.mark.parametrize("placement", ["ending_only", "second_act", "condition", "first_entry"])
def test_branch_marked_original_keeps_explicit_act_and_entry(placement):
    import json
    from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2GenerationError, NumericV2Generator

    original = "铭牌上刻着案号 XQ-701。"
    piece = {"id": "plaque", "trigger": {"type": "entry"}, "after": [], "required_before_exit": False}
    context = {
        "author_intent": {"scene_count": 2, "direction": f"支线第一幕开场：逐字展示[原文起]{original}[原文止]"},
        "continuity_items": [], "condition_selection": {"mode": "fixed"},
        "endpoint": {"mode": "new_ending", "ending": {"fixed_narrations": [{**piece, "text": original}]}},
    }
    candidate = _path_result_from_context(context)
    for index, scene in enumerate(candidate["scenes"]):
        scene["completion_facts"] = [{"id": "done", "description": "记录核对已完成。",
                                     "value_type": "bool", "target_value": True, "visibility": "public"}]
        candidate["transitions"][index + 1]["trigger_fact_ids"] = ["done"]
    if placement != "ending_only":
        piece = {**piece, "text_source": {"start_after": "[原文起]", "end_before": "[原文止]"}}
        if placement == "condition":
            piece["trigger"] = {"type": "condition", "condition": "玩家提出核对案号。"}
        candidate["scenes"][int(placement == "second_act")]["fixed_narrations"] = [piece]

    generator = NumericV2Generator(lambda messages, **options: json.dumps(candidate, ensure_ascii=False))
    if placement == "first_entry":
        result = generator.generate_branch_path(context=context)
        assert result["scenes"][0]["fixed_narrations"][0]["text"] == original
    else:
        with pytest.raises(NumericV2GenerationError) as caught:
            generator.generate_branch_path(context=context)
        assert caught.value.issues[0]["code"] == (
            "author_passage_condition_unsupported" if placement == "condition" else "branch_author_original_wrong_placement"
        )


@pytest.mark.parametrize("mode, label", [("path", "支线第一幕"), ("ending", "支线结局")])
def test_branch_generation_rejects_explicit_player_condition_before_model(mode, label):
    from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2GenerationError, NumericV2Generator

    direction = f"{label}只有玩家打开档案柜后才展示[原文起]案号 XQ-701。[原文止]。"
    context = {"author_intent": {"direction": direction}}

    def model(*args, **kwargs):
        pytest.fail("不支持的原文触发不得进入生成")

    generate = getattr(NumericV2Generator(model), f"generate_branch_{mode}")
    with pytest.raises(NumericV2GenerationError, match="invalid_author_original") as caught:
        generate(context=context)
    assert caught.value.issues[0]["code"] == "author_passage_condition_unsupported"


def test_persisted_branch_preview_cannot_apply_a_condition_original():
    service = NumericV2BranchService()
    project = branchable_project()
    plan = service.prepare_path(project, source_node_id="main_1", endpoint_mode="existing_ending",
                                endpoint_node_id="ending_normal", direction="从旧档案调查入手。",
                                length=1, condition_selection={"mode": "recommend"})
    preview = service.finish_path(plan, _path_result(plan))
    preview["scenes"][0]["fixed_narrations"] = [{
        "id": "notice", "text": "案号 XQ-701。", "trigger": {"type": "condition", "condition": "玩家打开档案柜。"},
        "after": [], "required_before_exit": False,
    }]
    with pytest.raises(NumericV2BranchError, match="author_passage_condition_unsupported") as caught:
        service.build_story(project, preview)
    assert caught.value.details["path"] == "scenes[0].fixed_narrations[0].trigger"


def test_branch_fact_capacity_rejects_overflow_before_preview_and_on_apply():
    import json
    from services.theater.numeric_v2 import _FACT_CONTRACT_MAX_KEYS
    from services.theater.numeric_v2_runtime import NumericV2Engine
    from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2GenerationError, NumericV2Generator

    service = NumericV2BranchService()
    project = branchable_project()
    project["story"]["fact_contract"] = {"facts": {
        f"global:fact_{index}": {"value_type": "bool", "visibility": "public", "description": "既有样本。"}
        for index in range(_FACT_CONTRACT_MAX_KEYS)
    }}
    plan = service.prepare_path(project, source_node_id="main_1", endpoint_mode="existing_ending",
                                endpoint_node_id="ending_normal", direction="从旧档案调查入手。",
                                length=1, condition_selection={"mode": "recommend"})
    context = plan["context"]
    assert context["author_intent"]["remaining_typed_fact_keys"] == 0
    output = _path_result_from_context(context)
    output["scenes"][0]["completion_facts"] = [
        {"id": "notice", "description": "程序已展示档案原文。", "value_type": "bool", "target_value": True, "visibility": "public"},
    ]
    output["transitions"][1]["trigger_fact_ids"] = ["notice"]
    generator = NumericV2Generator(lambda *args, **kwargs: json.dumps(output, ensure_ascii=False))
    with pytest.raises(NumericV2GenerationError) as caught:
        generator.generate_branch_path(context=context)
    assert caught.value.issues[0]["code"] == "fact_contract_limit_exceeded"

    output["scenes"][0]["fixed_narrations"] = [{
        "id": "log", "text": "入幕可见档案编号 QX-701。", "trigger": {"type": "entry"},
        "after": [], "required_before_exit": False,
    }]
    output["scenes"][0]["completion_facts"][0]["fixed_narration_id"] = "log"
    preview = service.finish_path(plan, generator.generate_branch_path(context=context))
    story, _, _ = service.build_story(project, preview)
    assert len(NumericV2Engine.from_mapping(story).fact_contract) == _FACT_CONTRACT_MAX_KEYS

    preview["scenes"][0]["completion_facts"][0].pop("fixed_narration_id")
    with pytest.raises(NumericV2BranchError, match="fact_contract_limit_exceeded"):
        service.build_story(project, preview)


def test_branch_path_context_includes_referenced_source_fact_and_skipped_display_evidence():
    import json

    project = branchable_project()
    story = project["story"]
    source, skipped = story["nodes"][:2]
    key = "scene:main_1:window_permission"
    text = "管理员允许从北窗领取 FQ-901 号封袋。"
    story["fact_contract"] = {"facts": {key: {"value_type": "bool", "visibility": "public", "description": text}}}
    source["completion_contract"] = {"all": [{"key": key, "equals": True}]}
    skipped["completion_contract"] = {"all": [{"fixed_narration_id": "map"}]}
    selected = {"id": "map", "text": "地图标出北窗只开放到 17:45。", "trigger": {"type": "entry"},
                "after": [], "required_before_exit": False}
    unselected = {**selected, "id": "note", "text": "信封是蓝色的。"}
    skipped["story_beat"]["fixed_narrations"] = [selected, unselected]
    for node in (source, skipped):
        node["route_gates"][0]["transition_contract"].update(
            fallback_offer="一起去下一处核对吗？", accept_input="好，一起去下一处核对。")
    plan = NumericV2BranchService().prepare_path(project, source_node_id="main_1", endpoint_mode="mainline",
        endpoint_node_id="main_3", direction="保留地图信息。", length=2, condition_selection={"mode": "recommend"})
    context = plan["context"]
    assert context["source_completion_facts"][key]["description"] == text
    assert context["original_exit"]["route_id"] == source["route_gates"][0]["id"]
    assert "transition_contract" not in context["original_exit"]
    assert context["skipped_mainline"][0]["display_evidence"] == [
        {field: selected[field] for field in ("id", "text", "trigger")}]
    assert unselected["text"] not in json.dumps(context, ensure_ascii=False)


@pytest.mark.parametrize(("story_name", "source_id", "target_id"), [
    ("story_ccc0763441e3.json", "mainline_02", "mainline_04"),
    ("story_neon_rebuild_ea2e45dab7.json", "mainline_03", "mainline_05"),
    ("story_neon_rebuild_ea2e45dab7.json", "mainline_05", "mainline_07"),
])
def test_builtin_mainline_rejoin_request_keeps_native_constraints_under_10000(story_name, source_id, target_id):
    import json
    from pathlib import Path

    from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator
    from services.theater_workshop.sdk.numeric_v2_project_store import _normalize_authoring
    from utils.tokenize import count_tokens

    story_path = Path(__file__).resolve().parents[3] / "assets" / "theater" / story_name
    story = json.loads(story_path.read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in story["nodes"]}
    mainline = sorted(node_id for node_id in nodes if node_id.startswith("mainline_"))
    project = {"story": story, "authoring": _normalize_authoring({"mainline_node_ids": mainline}, story), "revision": 1}
    before = deepcopy(project)
    service = NumericV2BranchService(token_counter=count_tokens)
    target = next(row for row in service.options(project, source_id)["rejoin"]["targets"] if row["node_id"] == target_id)
    plan = service.prepare_path(project, source_node_id=source_id, endpoint_mode="mainline", endpoint_node_id=target_id,
                                direction="两人核对这一处的相关记录。", length=target["allowed_lengths"][0],
                                condition_selection={"mode": "recommend"})

    class Captured(Exception):
        pass

    def inspect_request(messages, **options):
        assert count_tokens(json.dumps(messages, ensure_ascii=False)) <= 10_000
        wire = json.loads(messages[1]["content"])
        expected_gates = deepcopy(nodes[source_id]["route_gates"])
        for gate in expected_gates:
            contract = gate["transition_contract"]
            bridge = contract.get("bridge_scene_narration")
            if bridge and bridge in contract.get("must_deliver", ()):
                contract["must_deliver"] = [item for item in contract["must_deliver"] if item != bridge]
        assert wire["source"]["route_gates"] == expected_gates
        for wire_node, node_id in ((wire["source"], source_id), (wire["endpoint"], target_id)):
            original, packed = nodes[node_id]["story_beat"], wire_node["story_beat"]
            for key in ("opening_scene", "fixed_narrations", "character_state", "acting_contract", "summary"):
                assert packed.get(key) == original.get(key)
            boundaries = (packed.get("character_state") or {}).get("scene_boundaries") or []
            assert set(packed.get("must_not_happen", []) + boundaries) == set(original.get("must_not_happen", []) + boundaries)
        assert "summary" not in wire["endpoint"]
        target_beat = wire["endpoint"]["story_beat"]
        assert "goals" not in target_beat
        assert {goal["description"] for goal in nodes[target_id]["story_beat"]["goals"]
                if goal["delivery"]["timing"] == "turn"} <= set(target_beat.get("reserved_turn_events", ()))
        assert [row["key"] for row in wire["condition_candidates"]] == [row["key"] for row in plan["condition_candidates"]]
        assert [row["key"] for row in wire["continuity_items"]] == [row["key"] for row in plan["continuity_items"]]
        raise Captured

    with pytest.raises(Captured):
        NumericV2Generator(inspect_request, token_counter=count_tokens).generate_branch_path(context=plan["context"])
    assert plan["endpoint"]["story_beat"] == nodes[target_id]["story_beat"]
    assert project == before


def test_rejoin_keeps_turn_reveal_and_distinct_exact_anchor_in_model_context():
    project = branchable_project()
    target = project["story"]["nodes"][1]
    goal = target["story_beat"]["goals"][0]
    event = "只有玩家核对封签后，女主才揭示案件编号。"
    goal.update(description=event, evidence={"mode": "exact", "anchors": [event, "案件编号 QX-759"]})
    service = NumericV2BranchService()
    plan = service.prepare_path(project, source_node_id="main_1", endpoint_mode="mainline", endpoint_node_id="main_2",
                                direction="支线不重复第二幕的事件。", length=1, condition_selection={"mode": "recommend"})
    assert plan["context"]["endpoint"]["story_beat"]["reserved_turn_events"] == [event, "案件编号 QX-759"]
    assert plan["endpoint"]["story_beat"]["goals"][0] == goal


def test_rejoin_carries_distinct_exact_anchors_from_matching_goal_descriptions():
    import json

    from services.theater.numeric_v2 import NumericV2Compiler as NativeCompiler
    from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator
    from utils.tokenize import count_tokens

    project = branchable_project()
    story = project["story"]
    for node in story["nodes"][:-2]:
        node["route_gates"][0]["transition_contract"].update(
            fallback_offer="一起去下一处核对吗？", accept_input="好，一起去下一处核对。")
    skipped = story["nodes"][1]
    first = skipped["story_beat"]["goals"][0]
    first.update(description="女主逐条读出原始封签编号。", evidence={"mode": "exact", "anchors": ["封签编号 QX-701"]})
    second = {**deepcopy(first), "id": "main_2_goal_02", "evidence": {"mode": "exact", "anchors": ["封签编号 QX-929"]}}
    duplicate = {**deepcopy(first), "id": "main_2_goal_03"}
    skipped["story_beat"]["goals"].extend([second, duplicate])
    NativeCompiler().compile_v2_2(story)
    service = NumericV2BranchService(token_counter=count_tokens)
    target = next(row for row in service.options(project, "main_1")["rejoin"]["targets"] if row["node_id"] == "main_3")
    plan = service.prepare_path(project, source_node_id="main_1", endpoint_mode="mainline", endpoint_node_id="main_3",
                                direction="支线继续登记两张封签。", length=target["minimum_length"],
                                condition_selection={"mode": "recommend"})
    anchors = ["封签编号 QX-701", "封签编号 QX-929"]

    def model(messages, **_options):
        wire = json.loads(messages[1]["content"])
        carried = [row for row in wire["continuity_items"] if row["text"] == first["description"]]
        assert [row["contract"]["anchors"] for row in carried] == [[anchor] for anchor in anchors]
        candidate = _path_result_from_context(wire)
        for index, scene in enumerate(candidate["scenes"]):
            scene["completion_facts"] = [{"id": "reviewed", "description": "可见封签已经核对。",
                "value_type": "bool", "target_value": True, "visibility": "public"}]
            candidate["transitions"][index + 1]["trigger_fact_ids"] = ["reviewed"]
        return json.dumps(candidate, ensure_ascii=False)

    generator = NumericV2Generator(model, token_counter=count_tokens)
    response = generator.generate_branch_path(context=plan["context"])
    preview = service.finish_path(plan, response)
    applied, _, _ = service.build_story(project, preview)
    NativeCompiler().compile_v2_2(applied)
    original_ids = {node["id"] for node in story["nodes"]}
    carried_anchors = [anchor for node in applied["nodes"] if node["id"] not in original_ids
                       for goal in node["story_beat"]["goals"] for anchor in goal["evidence"]["anchors"]]
    assert carried_anchors == anchors
    response["continuity_handling"][0]["placement"] = "transition:0"
    with pytest.raises(NumericV2BranchError, match="branch_continuity_placement_invalid"):
        service.finish_path(plan, response)


@pytest.mark.parametrize("source_id", ["mainline_03", "mainline_04"])
@pytest.mark.parametrize("target_id", ["ending_normal", "ending_node_93800992b3", "ending_node_48523131db"])
def test_builtin_existing_ending_branch_fits_host_budget_without_losing_evidence(monkeypatch, source_id, target_id):
    import json
    from pathlib import Path
    from types import SimpleNamespace

    from services.theater_workshop.host import NekoWorkshopModel
    from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator
    from services.theater_workshop.sdk.numeric_v2_project_store import _normalize_authoring
    from utils import llm_client
    from utils.tokenize import count_tokens

    path = Path(__file__).resolve().parents[3] / "assets" / "theater" / "story_neon_rebuild_ea2e45dab7.json"
    story = json.loads(path.read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in story["nodes"]}
    order = sorted(node_id for node_id in nodes if node_id.startswith("mainline_"))
    project = {"story": story, "authoring": _normalize_authoring({"mainline_node_ids": order}, story), "revision": 1}
    before = deepcopy(project)
    service = NumericV2BranchService(token_counter=count_tokens)
    plan = service.prepare_path(project, source_node_id=source_id, endpoint_mode="existing_ending",
                                endpoint_node_id=target_id, direction="从这里核对一项现有记录后继续。",
                                length=1, condition_selection={"mode": "recommend"})
    seen = []

    class Client:
        def invoke(self, messages, **_options):
            wire = json.loads(messages[1]["content"])
            seen.append(wire)
            assert count_tokens(json.dumps(messages, ensure_ascii=False)) <= 10_000
            assert wire["source"]["route_gates"] == nodes[source_id]["route_gates"]
            assert "endpoint.exit_fact_conditions" not in messages[0]["content"]
            for packed, node_id in ((wire["source"]["story_beat"], source_id),):
                original = nodes[node_id]["story_beat"]
                for field in ("opening_scene", "fixed_narrations", "character_state", "acting_contract", "summary"):
                    assert packed.get(field) == original.get(field)
                state = original.get("character_state", {})
                boundaries = set(state.get("scene_boundaries", ()))
                assert set(packed.get("must_not_happen", ())) | boundaries == set(original.get("must_not_happen", ())) | boundaries
                assert packed.get("catgirl_situation", state.get("catgirl_state")) == original.get("catgirl_situation")
                for goal, full_goal in zip(packed["goals"], original["goals"], strict=True):
                    assert goal["description"] == full_goal["description"]
                    assert (goal.get("evidence") or {}).get("anchors", []) == full_goal["evidence"]["anchors"]
                    assert goal["delivery"].get("state_effects") == full_goal["delivery"].get("state_effects")
            assert "story_beat" not in wire["endpoint"]
            required = wire["endpoint"]["required_after_path"]
            original = nodes[target_id]["story_beat"]
            assert required["terminal_observation"] == original["opening_scene"]
            assert required.get("summary") == original.get("summary")
            assert required.get("acting_contract") == original.get("acting_contract")
            assert required.get("state_constraints") == original.get("character_state")
            assert wire["endpoint"].get("reserved_endpoint_originals") == original.get("fixed_narrations")
            state = original.get("character_state", {})
            boundaries = set(state.get("scene_boundaries", ()))
            assert set(required.get("must_not_happen", ())) | boundaries == set(original.get("must_not_happen", ())) | boundaries
            assert required.get("terminal_character_context", state.get("catgirl_state")) == original.get("catgirl_situation")
            assert required["outcomes"] == [goal["description"] for goal in original["goals"]]
            constraints = {row["outcome_index"]: row for row in required.get("outcome_constraints", ())}
            for index, goal in enumerate(original["goals"]):
                assert constraints.get(index, {}).get("anchors", []) == goal["evidence"]["anchors"]
                assert constraints.get(index, {}).get("state_effects") == goal["delivery"].get("state_effects")
            assert [row["key"] for row in wire["condition_candidates"]] == [row["key"] for row in plan["condition_candidates"]]
            for row in wire["recent_upstream"]:
                original = nodes[row["node_id"]]["story_beat"]
                assert row["opening_scene"] == original["opening_scene"]
                assert [goal["description"] for goal in row["goals"]] == [
                    goal["description"] for goal in original["goals"]]
                assert [goal.get("anchors") or [] for goal in row["goals"]] == [
                    goal["evidence"]["anchors"] for goal in original["goals"]]
            result = _path_result_from_context(wire)
            result["scenes"][0]["completion_facts"] = [{"id": "checked", "description": "已核对该记录。",
                "value_type": "bool", "target_value": True, "visibility": "public"}]
            result["transitions"][1]["trigger_fact_ids"] = ["checked"]
            return SimpleNamespace(content=json.dumps(result, ensure_ascii=False), response_metadata={})

        def close(self):
            pass

    monkeypatch.setattr(llm_client, "create_chat_llm", lambda **_options: Client())
    model = NekoWorkshopModel({"model": "fixture-model", "base_url": "https://example.invalid/v1",
                               "max_input_tokens": 10_000})
    assert NumericV2Generator(model, token_counter=count_tokens).generate_branch_path(context=plan["context"])["scenes"]
    assert len(seen) == 1
    assert plan["endpoint"]["story_beat"] == nodes[target_id]["story_beat"]
    assert project == before


def test_rejoin_preflight_uses_injected_token_counter_for_long_valid_originals(monkeypatch):
    import json
    from types import SimpleNamespace
    from services.theater.numeric_v2_runtime import NumericV2Engine
    from services.theater_workshop.host import NekoWorkshopModel
    from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator
    from utils import llm_client
    from utils.tokenize import count_tokens

    project = branchable_project()
    sentences = (
        "The former archivist inventoried the envelopes and left the reviewed receipts on a nearby table in the registry. ",
        "At the front desk, an archivist displayed the original ledger of the shared collections and documented incoming changes. ",
    )
    for node, sentence in zip(project["story"]["nodes"][:2], sentences, strict=True):
        node["story_beat"]["fixed_narrations"] = [{"id": "log", "text": (sentence * 62).strip(),
            "trigger": {"type": "entry"}, "after": [], "required_before_exit": False}]
    NumericV2Engine.from_mapping(project["story"])
    assert not NumericV2BranchService().options(project, "main_1")["rejoin"]["targets"][0]["available"]
    service = NumericV2BranchService(token_counter=count_tokens)
    assert service.options(project, "main_1")["rejoin"]["targets"][0]["available"]
    plan = service.prepare_path(project, source_node_id="main_1", endpoint_mode="mainline", endpoint_node_id="main_2",
                                direction="核对一条旁支线索后回到第二幕。", length=1, condition_selection={"mode": "recommend"})
    response = _path_result_from_context(plan["context"])
    response["scenes"][0]["completion_facts"] = [{"id": "done", "description": "已经核对支线线索。",
        "value_type": "bool", "target_value": True, "visibility": "public"}]
    response["transitions"][1]["trigger_fact_ids"] = ["done"]

    class Client:
        def invoke(self, messages, **_options):
            return SimpleNamespace(content=json.dumps(response, ensure_ascii=False), response_metadata={})

        def close(self):
            pass

    monkeypatch.setattr(llm_client, "create_chat_llm", lambda **_options: Client())
    model = NekoWorkshopModel({"model": "fixture-model", "base_url": "https://example.invalid/v1", "max_input_tokens": 10_000})
    result = NumericV2Generator(model, token_counter=count_tokens).generate_branch_path(context=plan["context"])
    draft = service.finish_path(plan, result)
    NumericV2Engine.from_mapping(service.build_story(project, draft)[0])


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
    assert "愿意托付秘密" not in serialized_candidates  # The fixture uses the semantic trust label.
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
    # v2.2 estimates about two points per turn; moving from initial 20 to high 70 still takes over eight turns.
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
    # Two predecessor routes yield high and low entry representatives, verifying they cannot collapse into one common entry.
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
    # If every entry needs over eight turns, block the candidate rather than passing it to the model.
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
    # If loops prevent reliable source-scene resolution, diagnose an unknown predecessor rather than difficult reachability.
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


@pytest.mark.parametrize(("key", "required", "value", "mode", "accepted"), [
    ("scene:main_1:confirmed", True, True, "all", True),
    ("scene:main_1:confirmed", "同意", "同意", "all", True),
    ("scene:main_1:confirmed", 2, 2, "all", True),
    ("scene:main_1:confirmed", True, False, "all", False),
    ("scene:main_1:confirmed", True, 1, "all", False),
    ("scene:main_1:confirmed", None, True, "all", False),
    ("scene:main_1:confirmed", True, True, "any", False),
    ("scene:main_2:confirmed", True, True, "all", False),
    ("global:confirmed", True, True, "all", False),
])
def test_branch_pacing_only_uses_exact_local_required_facts(key, required, value, mode, accepted):
    project = branchable_project(mainline_count=6)
    nodes = project["story"]["nodes"]
    first, fourth = nodes[0], nodes[3]
    first["completion_contract"] = {"all": [
        {"key": key, "equals": required} if required is not None else {"fixed_narration_id": "confirmation"},
    ]}
    first["route_gates"][0]["conditions"] = {mode: [{"type": "fact_equals", "key": key, "value": value}]}
    fourth_key = "scene:main_4:approved"
    fourth["completion_contract"] = {"all": [{"key": fourth_key, "equals": True}]}
    fourth["route_gates"][0]["conditions"] = {"all": [
        {"type": "fact_equals", "key": fourth_key, "value": True},
        {"type": "metric_compare", "metric": "trust", "op": ">=", "value": 70},
    ]}
    before = deepcopy(project)
    service = NumericV2BranchService()
    result = service._entry_scenarios(project, "main_5", "trust")
    if accepted:
        assert result == {
            "scenarios": [{"path": ["route_main_1", "route_main_2", "route_main_3", "route_main_4"], "value": 70}],
            "unknown_reasons": [],
        }
    else:
        assert result == {"scenarios": [], "unknown_reasons": ["condition_unknown:route_main_1"]}
    assert service.options(project, "main_1")["blocking_reasons"] == ["branch_source_unconditional_exit_required"]
    assert project == before


def test_late_rejoin_disables_target_when_continuity_cannot_fit_three_scenes():
    project = branchable_project(mainline_count=6)
    for index, node in enumerate(project["story"]["nodes"][1:4], start=1):
        extra = _projected_goal(node["id"], f"必须逐字交付的补充条款{index}")
        extra["id"] = f"{node['id']}_goal_02"
        node["story_beat"]["goals"].append(extra)
    service = NumericV2BranchService()

    target = service.options(project, "main_1")["rejoin"]["targets"][-1]

    assert target["node_id"] == "main_6"
    assert target["minimum_length"] > 3
    assert target["available"] is False
    assert target["allowed_lengths"] == []
    assert target["unavailable_reason"] == "branch_continuity_exceeds_three_scenes"


@pytest.mark.parametrize("evidence_source", ["goal", "entry"])
def test_rejoin_counts_only_missing_deliveries_and_keeps_coverage_in_preview(evidence_source):
    project = branchable_project(mainline_count=6)
    for node in project["story"]["nodes"][1:4]:
        node["story_beat"]["goals"][0]["evidence"] = {"mode": "semantic", "anchors": []}
    service = NumericV2BranchService()
    options = service.options(project, "main_1")
    assert options["rejoin"]["targets"][-1]["allowed_lengths"] == [1, 2, 3]
    plan = service.prepare_path(
        project, source_node_id="main_1", endpoint_mode="mainline", endpoint_node_id="main_6",
        direction="两人从另一处会合。", length=1,
        condition_selection={"mode": "fixed", "key": options["condition_candidates"][0]["key"]},
    )
    result = _path_result(plan)
    with pytest.raises(NumericV2BranchError, match="branch_length_too_short") as caught:
        service.finish_path(plan, result)
    assert caught.value.details == {"allowed_lengths": [2, 3]}

    covered, original_route, uncertain, required = result["continuity_handling"]
    covered.update(mode="covered", quote="不在候选中的引文")
    original_route.update(mode="route_only")
    uncertain.update(mode="uncertain")
    with pytest.raises(NumericV2BranchError, match="branch_continuity_evidence_invalid"):
        service.finish_path(plan, result)
    scene = result["scenes"][0]
    first_goal = scene["ordered_goals"][0]["description"]
    covered["quote"] = first_goal
    if evidence_source == "entry":
        covered["quote"] = "照片旁的登记单上写着核对结果。"
        scene["fixed_narrations"] = [{"id": "record", "text": covered["quote"], "trigger": {"type": "entry"}}]
    required["mode"] = "route_only"
    with pytest.raises(NumericV2BranchError, match="branch_continuity_incomplete"):
        service.finish_path(plan, result)
    required["mode"] = "carried"

    draft = service.finish_path(plan, result)
    assert [row["mode"] for row in draft["continuity_handling"]] == ["covered", "route_only", "uncertain", "carried"]
    assert [goal["description"] for goal in draft["scenes"][0]["ordered_goals"]] == [
        first_goal, plan["continuity_items"][2]["text"], plan["continuity_items"][3]["text"],
    ]


@pytest.mark.parametrize("mode", ["blocked", "alternative", "other_writer"])
def test_rejoin_does_not_skip_the_only_writer_of_a_required_exit_fact(mode, monkeypatch):
    project = branchable_project()
    story = project["story"]
    source, skipped, rejoin = story["nodes"][:3]
    key = "scene:main_2:locker_permission"
    definition = {"value_type": "bool", "visibility": "public", "description": "保管员批准封袋从北窗交接。"}
    story["fact_contract"] = {"facts": {key: definition}}
    skipped["completion_contract"] = {"all": [{"key": key, "equals": True}]}
    skipped["route_gates"][0]["transition_contract"].update(
        fallback_offer="要去下一处吗？", accept_input="好，我们去下一处。")
    route = rejoin["route_gates"][0]
    condition = {"type": "fact_equals", "key": key, "value": True}
    route["conditions"] = {"all": [condition]}
    if mode == "alternative":
        route["conditions"] = {"any": [condition, {"type": "metric_compare", "metric": "trust", "op": ">=", "value": 0}]}
    if mode == "other_writer":
        source["completion_contract"] = {"all": [{"key": key, "equals": True}]}
    service = NumericV2BranchService()
    targets = service.options(project, "main_1")["rejoin"]["targets"]
    target = next(row for row in targets if row["node_id"] == "main_3")
    assert target["available"] is (mode != "blocked")
    assert next(row for row in targets if row["node_id"] == "main_2")["available"]
    params = dict(source_node_id="main_1", endpoint_mode="mainline", endpoint_node_id="main_3",
                  direction="绕开第二幕并回接第三幕。", length=2, condition_selection={"mode": "recommend"})
    if mode == "blocked":
        assert target["unavailable_reason"] == "branch_rejoin_skips_required_fact"
        with pytest.raises(NumericV2BranchError, match="branch_rejoin_skips_required_fact") as caught:
            service.prepare_path(project, **params)
        assert caught.value.details["fact_keys"] == [key]
        with monkeypatch.context() as old_generator:
            old_generator.setattr(service, "_missing_rejoin_fact_keys", lambda *_: [])
            plan = service.prepare_path(project, **params)
            draft = service.finish_path(plan, _path_result(plan))
        with pytest.raises(NumericV2BranchError, match="branch_rejoin_skips_required_fact"):
            service.build_story(project, draft)
    else:
        endpoint = service.prepare_path(project, **params)["context"]["endpoint"]
        assert endpoint["exit_fact_conditions"] == {route["id"]: route["conditions"]}
        assert endpoint["exit_fact_definitions"] == {key: definition}


@pytest.mark.parametrize("mode", ["later_gate", "writer_behind_gate", "reachable_writer", "alternate_ending"])
def test_rejoin_checks_downstream_routes_without_blocking_reachable_alternatives(mode):
    project = branchable_project()
    story = project["story"]
    _, skipped, rejoin, later = story["nodes"][:4]
    key = "scene:main_2:locker_permission"
    story["fact_contract"] = {"facts": {
        key: {"value_type": "bool", "visibility": "public", "description": "保管员已批准移交。"},
    }}
    completion = {"all": [{"key": key, "equals": True}]}
    skipped["completion_contract"] = completion
    gated_node = rejoin if mode == "writer_behind_gate" else later
    gated_node["route_gates"][0]["conditions"] = {"all": [{"type": "fact_equals", "key": key, "value": True}]}
    if mode in ("writer_behind_gate", "reachable_writer"):
        later["completion_contract"] = completion
    if mode == "alternate_ending":
        rejoin["route_gates"][0]["conditions"] = {
            "all": [{"type": "metric_compare", "metric": "trust", "op": ">=", "value": 1}],
        }
        rejoin["route_gates"].append({**_unconditional_route("route_alternate", "ending_normal", "备用结局"), "priority": 50})

    service = NumericV2BranchService()
    target = next(row for row in service.options(project, "main_1")["rejoin"]["targets"]
                  if row["node_id"] == "main_3")
    blocked = mode in ("later_gate", "writer_behind_gate")
    assert target["available"] is not blocked
    if blocked:
        assert target["unavailable_reason"] == "branch_rejoin_skips_required_fact"
        assert target["blocking_fact_keys"] == [key]


def test_new_branch_ending_does_not_inherit_unrelated_normal_ending_state():
    project = branchable_project()
    project["authoring"]["character_state_arc"]["ending_stage"]["acting_contract"]["dialogue_policy"] = "forbidden"
    project["authoring"]["character_state_arc"]["ending_stage"]["continuity_from_previous"] = ["男主已离开"]
    project["authoring"]["relationship_arc"] = {"stages": [
        {"node_id": "main_1", "stage_ceiling": "familiar"},
        {"node_id": "main_3", "stage_ceiling": "cooperative"},
        {"node_id": "main_4", "stage_ceiling": "trusted"},
    ]}
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
    assert plan["context"]["global"]["relationship_arc"]["stages"] == before["authoring"]["relationship_arc"]["stages"][:2]
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


@pytest.mark.parametrize(("authored_bridge", "carried_text"), [
    ("", ""),
    ("", "窗外响起闭馆的钟声。"),
    ("两人已从仓库回到入口。", ""),
])
def test_branch_ending_bridge_is_optional_and_carried_events_still_materialize(authored_bridge, carried_text):
    import json

    from services.theater.numeric_v2_actor import _transition_contract_for_actor
    from services.theater.numeric_v2_cast import NumericV2CastProjection
    from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator

    project = branchable_project()
    service = NumericV2BranchService()
    options = service.options(project, "main_3")
    plan = service.prepare_path(
        project, source_node_id="main_3", endpoint_mode="existing_ending",
        endpoint_node_id="ending_normal", direction="调查后回到既定结局。", length=1,
        condition_selection={"mode": "fixed", "key": options["condition_candidates"][0]["key"]},
    )
    if carried_text:
        plan["continuity_items"] = [{"key": "ending_bridge", "text": carried_text}]
        plan["context"]["continuity_items"] = plan["continuity_items"]
    candidate = _path_result(plan)
    candidate["scenes"][0]["completion_facts"] = [{
        "id": "evidence_shown", "description": "女主实际展示带日期的照片。",
        "value_type": "bool", "target_value": True, "visibility": "public",
        "evidence_source": "performance",
    }]
    candidate["transitions"][1]["trigger_fact_ids"] = ["evidence_shown"]
    candidate["transitions"][1]["bridge_scene_narration"] = authored_bridge
    if carried_text:
        candidate["continuity_handling"][0]["placement"] = "transition:1"

    def model(messages, **_):
        assert "末条同地结局无独立变化时 bridge_scene_narration 填空" in messages[0]["content"]
        assert "不抄入 scene_boundaries 或 must_not_happen" in messages[0]["content"]
        return json.dumps(candidate, ensure_ascii=False)

    result = NumericV2Generator(model).generate_branch_path(context=plan["context"])
    broken = deepcopy(result)
    broken["transitions"][0]["bridge_scene_narration"] = ""
    with pytest.raises(NumericV2BranchError) as caught:
        service.finish_path(plan, broken)
    assert caught.value.details["path"] == "transitions[0].bridge_scene_narration"

    draft = service.finish_path(plan, result)
    story, _, _ = service.build_story(project, draft)
    exported = json.loads(NumericV2Compiler(InProcessPackageGateway()).compile(story).json_bytes)
    node = next(node for node in exported["nodes"] if node["id"] == draft["scene_ids"][0])
    contract = node["route_gates"][0]["transition_contract"]
    expected = authored_bridge + carried_text
    assert contract.get("bridge_scene_narration", "") == expected
    assert ("bridge_scene_narration" in contract) == bool(expected)
    assert contract["must_deliver"] == ([expected] if expected else [])
    projected = _transition_contract_for_actor(
        NumericV2CastProjection("", "", "玩家", "角色"), contract, target_opening="",
    )
    assert projected["bridge_required"] == bool(expected)


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
    assert "player_state" not in stage and "acting_contract" not in stage
    assert plan["context"]["source"]["story_beat"]["character_state"] == source["story_beat"]["character_state"]
    assert plan["context"]["source"]["story_beat"]["acting_contract"] == {"dialogue_policy": "optional"}
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
    completion_key = "scene:node_branch_scene:branch_complete"
    assert branch_scene["completion_contract"] == {
        "all": [{"key": completion_key, "equals": True}],
    }
    assert branch_scene["route_gates"][0]["transition_contract"]["trigger_fact_ids"] == [
        "branch_complete"
    ]
    assert story["fact_contract"]["facts"][completion_key] == {
        "value_type": "bool",
        "visibility": "public",
        "description": result["scenes"][0]["ordered_goals"][-1]["description"],
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


def test_branch_path_rejects_missing_or_generic_accept_input():
    project = branchable_project()
    service = NumericV2BranchService()
    options = service.options(project, "main_3")
    plan = service.prepare_path(
        project,
        source_node_id="main_3",
        endpoint_mode="mainline",
        endpoint_node_id="main_4",
        direction="误会让两人转而调查一张旧照片。",
        length=1,
        condition_selection={"mode": "fixed", "key": options["condition_candidates"][0]["key"]},
    )
    result = _path_result(plan)
    result["transitions"][0].pop("accept_input")
    with pytest.raises(NumericV2BranchError, match="branch_transition_accept_input_required"):
        service.finish_path(plan, result)

    result = _path_result(plan)
    result["transitions"][0]["accept_input"] = "我接受这个安排，继续进入下一阶段。"
    with pytest.raises(NumericV2BranchError, match="branch_transition_accept_input_generic"):
        service.finish_path(plan, result)


def _silent_exit_path(position, initial, changes):
    from utils.tokenize import count_tokens
    project = branchable_project()
    if position == "source":
        beat = next(node for node in project["story"]["nodes"] if node["id"] == "main_3")["story_beat"]
        beat["acting_contract"] = {**_character_state()["acting_contract"], "dialogue_policy": initial}
        beat["acting_contract"].pop("assertable_self_facts")
        base = beat["goals"][0]
        beat["goals"] = [
            {**deepcopy(base), "id": f"main_3_goal_{index + 1:02d}",
             "evidence": {"mode": "semantic", "anchors": []},
             "delivery": {**base["delivery"], "type": "catgirl_action", "output_field": "performance_action",
                          **({"state_effects": {"dialogue_policy": policy}} if policy != "unchanged" else {})}}
            for index, policy in enumerate(changes)
        ]
    service = NumericV2BranchService(id_factory=iter(["scene", "r0", "r1", "draft"]).__next__,
                                     token_counter=count_tokens)
    options = service.options(project, "main_3")
    plan = service.prepare_path(project, source_node_id="main_3", endpoint_mode="mainline",
        endpoint_node_id="main_4", direction="根据当前证据继续核对。", length=1,
        condition_selection={"mode": "fixed", "key": options["condition_candidates"][0]["key"]})
    result = _path_result(plan)
    if position == "scene":
        scene = result["scenes"][0]
        scene["character_state"]["acting_contract"]["dialogue_policy"] = initial
        scene["ordered_goals"] = [
            {**_ordered_goal("猫娘收好已经展示的证据"), "delivery_type": "catgirl_action",
             "dialogue_policy_after": policy}
            for policy in changes
        ]
    index = 0 if position == "source" else 1
    result["transitions"][index].pop("fallback_offer")
    result["transitions"][index]["silent_choice"] = {
        "destination": "下一处核对地点", "arrangement": "继续核对证据"}
    return project, service, plan, result, index


@pytest.mark.parametrize("position", ["source", "scene"])
@pytest.mark.parametrize("initial,changes,allowed", [
    ("optional", ["forbidden"], True),
    ("forbidden", ["unchanged"], True),
    ("optional", ["forbidden", "unchanged"], True),
    ("optional", ["forbidden", "required"], False),
    ("optional", ["forbidden", "optional"], False),
    ("optional", ["unchanged"], False),
])
def test_branch_missing_fallback_requires_final_typed_forbidden_policy(position, initial, changes, allowed):
    project, service, plan, result, index = _silent_exit_path(position, initial, changes)
    if not allowed:
        with pytest.raises(NumericV2BranchError, match="branch_transition_fallback_offer_required"):
            service.finish_path(plan, result)
        return

    draft = service.finish_path(plan, result)
    story, _, _ = service.build_story(project, draft)
    node_id = "main_3" if position == "source" else "node_branch_scene"
    node = next(node for node in story["nodes"] if node["id"] == node_id)
    contract = node["route_gates"][-1 if position == "source" else 0]["transition_contract"]

    assert "fallback_offer" not in contract
    assert contract["accept_input"] == result["transitions"][index]["accept_input"]
    NumericV2Compiler(InProcessPackageGateway()).compile(story)


@pytest.mark.parametrize("position", ["source", "scene"])
@pytest.mark.parametrize("field,missing,code", [
    ("accept_input", True, "branch_transition_accept_input_required"),
    ("fallback_offer", False, "branch_transition_fallback_offer_required"),
])
def test_branch_forbidden_exit_keeps_acceptance_and_present_value_validation(position, field, missing, code):
    _, service, plan, result, index = _silent_exit_path(position, "optional", ["forbidden"])
    if missing:
        result["transitions"][index].pop(field)
    else:
        result["transitions"][index][field] = None

    with pytest.raises(NumericV2BranchError, match=code):
        service.finish_path(plan, result)


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
    # Explicit scene state may already change the holder at entry; old prop ledgers must not override entry state.
    assert "仅定义用途，不表示已取得或操作完成" in branch_node["story_beat"]["catgirl_situation"]
    assert "当前归属为女主" not in branch_node["story_beat"]["catgirl_situation"]
    assert "保存带日期的旧照片" in branch_node["story_beat"]["catgirl_situation"]
    assert branch_node["story_beat"]["character_state"]["catgirl_state"] == result["scenes"][0]["character_state"]["catgirl_state"]
    if opening_transfer:
        assert key_props == project["authoring"]["key_props"]
    else:
        assert not any("当前归属为双方共同" in item for item in
                       branch_node["route_gates"][0]["transition_contract"]["must_preserve"])
        assert "保存带日期的旧照片" in branch_node["route_gates"][0]["transition_contract"]["must_preserve"][-1]
        assert key_props[0]["states"][-1] == {
            "node_id": "node_branch_scene",
            "owner": "shared",
            "state": "盒盖已经开启，带日期的旧照片由双方共同保管",
        }


def test_rejoin_keeps_skipped_bridge_out_of_required_continuity():
    project = branchable_project()
    story = project["story"]
    skipped = story["nodes"][1]
    old_route = skipped["route_gates"][0]
    key = "scene:main_2:clue_shown"
    definition = {"value_type": "bool", "visibility": "public", "description": "线索已经展示。"}
    story["fact_contract"] = {"facts": {key: definition}}
    old_route["conditions"] = {"all": [{"type": "fact_equals", "key": key, "value": True}]}

    service = NumericV2BranchService()
    options = service.options(project, "main_1")
    plan = service.prepare_path(
        project, source_node_id="main_1", endpoint_mode="mainline", endpoint_node_id="main_3",
        direction="两人从另一处会合。", length=1,
        condition_selection={"mode": "fixed", "key": options["condition_candidates"][0]["key"]},
    )

    assert [item["text"] for item in plan["continuity_items"]] == [skipped["story_beat"]["goals"][0]["description"]]
    assert plan["context"]["endpoint"]["existing_entry_scope"] == {
        "initial_entry": False,
        "common_gate_facts": {key: True},
        "routes": [{"source_node_id": "main_2", "route_id": "route_main_2", "gate_facts": {}}],
        "fact_definitions": {key: definition},
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
    for node in (branch_node, ending_node):
        assert node["story_beat"]["content_boundaries"] == project["setup"]["content_boundaries"]
        assert not set(project["setup"]["content_boundaries"]).intersection(node["story_beat"]["must_not_happen"])
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


def test_new_ending_path_compacts_writer_context_without_editing_confirmed_ending():
    project = branchable_project(mainline_count=6)
    source = project["story"]["nodes"][4]
    source_beat = source["story_beat"]
    source_beat["character_state"] = _character_state()
    source_beat["catgirl_situation"] = "".join(source_beat["character_state"][key] for key in (
        "catgirl_state", "player_state", "environment_state",
    )) + "她守在档案桌旁。"
    boundary = "不得公开住户的姓名"
    project["setup"]["content_boundaries"] = [boundary]
    source_beat["content_boundaries"] = [boundary]
    original_transition = source["route_gates"][0]["transition_contract"]
    original_transition["must_deliver"].append(original_transition["bridge_scene_narration"])
    source_beat["must_not_happen"] = [
        *source_beat["character_state"]["scene_boundaries"], boundary, "不得重做已经结束的调查",
    ]
    key = "scene:main_5:evidence_reviewed"
    fact = {"value_type": "bool", "visibility": "public", "description": "证物已经核对。"}
    project["story"]["fact_contract"] = {"facts": {key: fact}}
    source["completion_contract"] = {"all": [{"key": key, "equals": True}]}
    legacy_stage = {"stage_ceiling": "familiar"}
    source_stage = {"node_id": "main_5", "known_player_facts": ["男主已经看到证物。"]}
    project["authoring"]["relationship_arc"] = {"stages": [
        legacy_stage, {"node_id": "main_1", "stage_ceiling": "familiar"}, source_stage,
        {"node_id": "main_6", "stage_ceiling": "trusted"},
    ]}
    before = deepcopy(project)
    service = NumericV2BranchService()
    options = service.options(project, "main_5")
    selection = {"mode": "fixed", "key": options["condition_candidates"][0]["key"]}
    ending_plan = service.prepare_ending(project, source_node_id="main_5",
        ending_direction="整理证物后收束。", condition_selection=selection)
    opening = "核对完的证物已经收在档案盒里。"
    ending_draft = service.finish_ending(ending_plan, {
        "title": "收好档案",
        "summary": opening,
        "opening_scene": opening,
        "ordered_goals": [_ending_goal("已经把核对完的证物收进档案盒。")],
        "irreversible_facts": ["证物已经核对。"],
        "character_state": _character_state(),
        "catgirl_situation": "她终于可以放心离开档案室。",
        "tone": "克制",
        "fixed_narrations": [{"id": "plaque", "text": "档案编号：十七。", "trigger": {"type": "entry"},
                              "after": [], "required_before_exit": False}],
    })
    confirmed = deepcopy(ending_draft["ending"])
    plan = service.prepare_path(project, source_node_id=None, endpoint_mode="new_ending", endpoint_node_id=None,
        direction="核对一件证物后收好。", length=1, condition_selection=None,
        ending_draft=ending_draft, confirmed_ending=confirmed)
    context = plan["context"]
    assert [row["node_id"] for row in ending_plan["context"]["global"]["character_state_arc"]["stages"]] == [
        "main_1", "main_2", "main_3", "main_4", "main_5",
    ]
    assert context["global"]["relationship_arc"]["stages"] == [legacy_stage, source_stage]
    state_arc = context["global"]["character_state_arc"]
    assert state_arc["stages"] == ending_plan["context"]["global"]["character_state_arc"]["stages"][-3:]
    assert context["source_completion_facts"] == {key: fact}
    expected_gates = deepcopy(source["route_gates"])
    expected_gates[0]["transition_contract"]["must_deliver"].remove(original_transition["bridge_scene_narration"])
    assert context["source"]["route_gates"] == expected_gates
    assert ending_plan["context"]["source"]["route_gates"] == expected_gates
    assert context["source"]["story_beat"]["character_state"] == source_beat["character_state"]
    assert context["source"]["story_beat"]["catgirl_situation"] == "她守在档案桌旁。"
    assert context["global"]["content_boundaries"] == [boundary]
    assert "content_boundaries" not in context["source"]["story_beat"]
    assert context["source"]["story_beat"]["must_not_happen"] == ["不得重做已经结束的调查"]
    assert context["original_exit"] == {"target_title": "第6幕", "route_id": "route_main_5"}
    assert context["condition_selection"] == selection
    assert context["condition_candidates"][0]["behavior_hint"] == options["condition_candidates"][0]["behavior_hint"]
    for row, original in zip(context["recent_upstream"], project["story"]["nodes"][2:4], strict=True):
        original_beat = original["story_beat"]
        assert row["opening_scene"] == original_beat["opening_scene"]
        assert row["goals"] == [{"id": goal["id"], "owner": goal["owner"], "description": goal["description"],
                                 "timing": "turn", "anchors": goal["evidence"]["anchors"]}
                                for goal in original_beat["goals"]]
    projected = deepcopy(confirmed)
    projected.pop("summary")
    projected.pop("catgirl_situation")
    for field in ("catgirl_state", "player_state", "environment_state"):
        projected["character_state"].pop(field)
    assert context["endpoint"]["ending"] == projected
    assert plan["endpoint"]["ending"] == ending_draft["ending"] == confirmed
    assert project == before


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
    # API acceptance and branch normalization share the empty-array contract; preserve existing hard boundaries verbatim.
    from services.theater_workshop.sdk.contracts import BranchCharacterStatePayload
    state = _character_state()
    state["scene_boundaries"] = boundaries
    payload = BranchCharacterStatePayload.model_validate(state).model_dump()
    normalized = NumericV2BranchService._validate_character_state(payload, path="state")
    assert normalized["scene_boundaries"] == boundaries
    assert normalized["continuity_from_previous"] == state["continuity_from_previous"]


@pytest.mark.parametrize("boundaries", [None, [f"不得越界{i}" for i in range(5)]])
def test_branch_boundary_relaxation_keeps_shape_and_upper_limit(boundaries):
    from pydantic import ValidationError
    from services.theater_workshop.sdk.contracts import BranchCharacterStatePayload
    # Both layers reject invalid shapes and oversized arrays rather than relying on downstream validation.
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


@pytest.mark.parametrize("position", ["source", "scene"])
@pytest.mark.parametrize("choice", [None, {}, {"destination": "石桥"},
    {"destination": "石桥", "arrangement": ""},
    {"destination": "石桥", "arrangement": "核对\n记录"}])
def test_generated_silent_branch_rejects_missing_or_malformed_choice(position, choice):
    _, service, plan, result, index = _silent_exit_path(position, "optional", ["forbidden"])
    if choice is None:
        result["transitions"][index].pop("silent_choice")
    else:
        result["transitions"][index]["silent_choice"] = choice
    with pytest.raises(NumericV2BranchError, match="branch_transition_silent_choice_required"):
        service.finish_path(plan, result)


@pytest.mark.parametrize("field", ["destination", "arrangement"])
@pytest.mark.parametrize("tokens", [30, 31])
def test_branch_silent_choice_enforces_compiler_token_limit(field, tokens):
    _, service, plan, result, index = _silent_exit_path("scene", "optional", ["forbidden"])
    result["transitions"][index]["silent_choice"][field] = " a" * tokens
    if tokens > 30:
        with pytest.raises(NumericV2BranchError, match="branch_transition_silent_choice_required"):
            service.finish_path(plan, result)
    else:
        service.finish_path(plan, result)


def test_silent_branch_without_counter_returns_validation_error():
    _, _, plan, result, _ = _silent_exit_path("scene", "optional", ["forbidden"])
    with pytest.raises(NumericV2BranchError, match="branch_transition_silent_choice_required"):
        NumericV2BranchService().finish_path(plan, result)
