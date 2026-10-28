"""New branches declare causal completion; legacy drafts keep their old mapping."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from services.theater_workshop.host import InProcessPackageGateway
from services.theater_workshop.sdk.generation.numeric_v2 import (
    NumericV2GenerationError, NumericV2Generator,
)
from services.theater_workshop.sdk.numeric_v2 import NumericV2Compiler
from services.theater_workshop.sdk.numeric_v2_branch import NumericV2BranchError, NumericV2BranchService
from .test_numeric_v2_branch import branchable_project, _ordered_goal, _path_result
from .test_sdk_lifecycle import opened


def _case(project=None):
    project = project or branchable_project()
    service = NumericV2BranchService()
    options = service.options(project, "main_3")
    plan = service.prepare_path(
        project, source_node_id="main_3", endpoint_mode="mainline",
        endpoint_node_id="main_4", direction="先获准交付证物，再交付并致谢。",
        length=1, condition_selection={
            "mode": "fixed", "key": options["condition_candidates"][1]["key"],
        },
    )
    result = _path_result(plan)
    result["scenes"][0]["ordered_goals"] = [
        {**_ordered_goal("玩家允许将证物交给保管员。"), "owner": "player",
         "delivery_type": "player_action", "sources": ["player_input"]},
        {**_ordered_goal("保管员已经接稳证物。"), "owner": "environment",
         "delivery_type": "environment_fact"},
        _ordered_goal("猫娘感谢保管员。"),
    ]
    result["scenes"][0]["completion_facts"] = [
        {"id": fact_id, "description": text, "value_type": "bool",
         "target_value": True, "visibility": "public"}
        for fact_id, text in (
            ("handoff_allowed", "玩家允许将证物交给保管员。"),
            ("evidence_received", "保管员已经接稳证物。"),
            ("thanks_expressed", "猫娘感谢保管员。"),
        )
    ]
    # Courtesy is valid performance, but the author does not make it a prerequisite.
    result["transitions"][1]["trigger_fact_ids"] = ["handoff_allowed", "evidence_received"]
    return project, service, plan, result


def test_new_branch_completion_uses_selected_results_not_the_final_goal():
    project, service, plan, result = _case()
    before = deepcopy((project, result))
    draft = service.finish_path(plan, result)
    story, _, _ = service.build_story(project, draft)
    compiled = NumericV2Compiler(InProcessPackageGateway()).compile(story)
    node_id = draft["scene_ids"][0]
    node = next(node for node in compiled.story["nodes"] if node["id"] == node_id)
    assert node["completion_contract"] == {"all": [
        {"key": f"scene:{node_id}:handoff_allowed", "equals": True},
        {"key": f"scene:{node_id}:evidence_received", "equals": True},
    ]}
    assert f"scene:{node_id}:branch_complete" not in story["fact_contract"]["facts"]
    assert story["fact_contract"]["facts"][f"scene:{node_id}:thanks_expressed"]["description"] == "猫娘感谢保管员。"
    assert node["route_gates"][0]["transition_contract"]["trigger_fact_ids"] == [
        "handoff_allowed", "evidence_received",
    ]
    assert node["story_beat"]["must_not_happen"] == list(dict.fromkeys([
        *result["scenes"][0]["must_not_happen"],
        *result["scenes"][0]["character_state"]["scene_boundaries"],
    ]))
    assert (project, result) == before

    engine = NumericV2Engine.from_mapping(story)
    session = replace(engine.create_session(
        session_id="branch_completion", catgirl_binding=_binding(), opening_performance=_opening(),
    ), current_node_id=node_id)
    for index, fact_id in enumerate(("thanks_expressed", "handoff_allowed", "evidence_received")):
        session = engine.resolve_turn(
            session, TurnRequestV2(f"t{index}", session.revision, f"第{index}项公开结果。"), (),
            fact_operations=({"op": "set", "key": f"scene:{node_id}:{fact_id}",
                              "value": True, "visibility": "public"},),
        ).session
        assert engine.completion_contract_satisfied(session) is (index == 2)


def test_real_generator_preserves_explicit_completion_and_exit_choice():
    _, _, plan, result = _case()
    before = deepcopy(result)
    calls = []
    generator = NumericV2Generator()

    def model(messages, **kwargs):
        calls.append((messages, kwargs))
        return json.dumps(result, ensure_ascii=False)

    generator.call_llm = model
    actual = generator.generate_branch_path(context=plan["context"])
    assert actual == before
    assert len(calls) == 1
    prompt = calls[0][0][0]["content"]
    assert "completion_facts" in prompt
    assert "trigger_fact_ids" in prompt
    assert "不能从最后一个感谢、回应或 ordered_goal 反推先前步骤已完成" in prompt
    assert "fallback_offer/accept_input" in prompt


def test_legacy_draft_is_readable_but_cannot_be_returned_by_fresh_generation():
    project, service, plan, _ = _case()
    legacy = _path_result(plan)
    draft = service.finish_path(plan, legacy)
    story, _, _ = service.build_story(project, draft)
    key = f"scene:{draft['scene_ids'][0]}:branch_complete"
    assert story["fact_contract"]["facts"][key]["description"] == legacy["scenes"][0]["ordered_goals"][-1]["description"]
    assert "completion_facts" not in draft["scenes"][0]
    generator = NumericV2Generator()
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(legacy, ensure_ascii=False)
    with pytest.raises(NumericV2GenerationError):
        generator.generate_branch_path(context=plan["context"])


@pytest.mark.parametrize("damage,path", [
    ("missing_facts", "scenes[0].completion_facts"),
    ("empty_facts", "scenes[0].completion_facts"),
    ("type", "scenes[0].completion_facts[0]"),
    ("duplicate", "scenes[0].completion_facts[1]"),
    ("missing_triggers", "transitions[1].trigger_fact_ids"),
    ("empty_triggers", "transitions[1].trigger_fact_ids"),
    ("unknown_trigger", "transitions[1].trigger_fact_ids"),
])
def test_new_generation_rejects_incomplete_or_invalid_completion_contract(damage, path):
    _, _, plan, result = _case()
    scene, transition = result["scenes"][0], result["transitions"][1]
    if damage == "missing_facts":
        del scene["completion_facts"]
    elif damage == "empty_facts":
        scene["completion_facts"] = []
    elif damage == "type":
        scene["completion_facts"][0]["target_value"] = 1
    elif damage == "duplicate":
        scene["completion_facts"][1]["id"] = scene["completion_facts"][0]["id"]
    elif damage == "missing_triggers":
        del transition["trigger_fact_ids"]
    elif damage == "empty_triggers":
        transition["trigger_fact_ids"] = []
    else:
        transition["trigger_fact_ids"] = ["unwritten_result"]
    generator = NumericV2Generator()
    generator.call_llm = lambda *_args, **_kwargs: json.dumps(result, ensure_ascii=False)
    with pytest.raises(NumericV2GenerationError) as caught:
        generator.generate_branch_path(context=plan["context"])
    assert caught.value.issues[0]["path"] == path


def test_explicit_completion_cannot_silently_fall_back_when_a_draft_is_malformed():
    project, service, plan, result = _case()
    draft = service.finish_path(plan, result)
    draft["transitions"][1]["trigger_fact_ids"] = ["unwritten_result"]
    before = deepcopy(project)
    with pytest.raises(NumericV2BranchError):
        service.build_story(project, draft)
    assert project == before


def test_new_branch_display_completion_preserves_original_text_without_semantic_surrogate():
    project, service, plan, result = _case()
    scene = result["scenes"][0]
    scene["fixed_narrations"] = [{
        "id": "receipt", "text": "交接记录：\n编号 07；保管方已签收。",
        "trigger": {"type": "entry"}, "after": [], "required_before_exit": False,
    }]
    scene["completion_facts"].append({
        "id": "receipt_displayed", "description": "交接记录原文已经展示。",
        "value_type": "bool", "target_value": True, "visibility": "public",
        "fixed_narration_id": "receipt",
    })
    result["transitions"][1]["trigger_fact_ids"].append("receipt_displayed")
    draft = service.finish_path(plan, result)
    story, _, _ = service.build_story(project, draft)
    compiled = NumericV2Compiler(InProcessPackageGateway()).compile(story)
    node = next(node for node in compiled.story["nodes"] if node["id"] == draft["scene_ids"][0])
    assert node["completion_contract"]["all"][-1] == {"fixed_narration_id": "receipt"}
    assert node["story_beat"]["fixed_narrations"] == scene["fixed_narrations"]
    assert f"scene:{node['id']}:receipt_displayed" not in story["fact_contract"]["facts"]


@pytest.mark.parametrize("field,value", [
    ("id", "含糊标识"), ("value_type", "object"), ("visibility", "private"),
    ("description", ""), ("fixed_narration_id", "missing"),
])
def test_explicit_branch_fact_rejects_invalid_declarations(field, value):
    _, service, plan, result = _case()
    result["scenes"][0]["completion_facts"][0][field] = value
    with pytest.raises(NumericV2BranchError) as caught:
        service.finish_path(plan, result)
    assert caught.value.details["path"] == "scenes[0].completion_facts[0]"


def test_valid_branch_applies_and_exports_without_rewriting_boundaries_or_choices(opened):
    sdk = opened[0].sdk
    original = branchable_project()
    created = sdk.create_project()
    project = sdk._store.finish_generation(
        created["project_id"], base_revision=created["revision"],
        story=original["story"], mainline_node_ids=original["authoring"]["mainline_node_ids"],
    )
    project, service, plan, result = _case(project)
    draft = service.finish_path(plan, result)
    sdk._store.save_branch_draft(project["project_id"], base_revision=project["revision"], draft=draft)
    applied = sdk.apply_branch(project["project_id"], draft["draft_id"], base_revision=project["revision"])
    revision = applied["project"]["revision"]
    sdk.compile(project["project_id"], base_revision=revision)
    sdk.validate(project["project_id"], base_revision=revision)
    candidate = sdk.export(project["project_id"], base_revision=revision)
    exported = json.loads(candidate.json_bytes)
    node = next(node for node in exported["nodes"] if node["id"] == draft["scene_ids"][0])
    transition = node["route_gates"][0]["transition_contract"]
    for field in ("fallback_offer", "accept_input", "reason", "bridge_scene_narration"):
        assert transition[field] == result["transitions"][1][field]
    assert node["completion_contract"]["all"] == [
        {"key": f"scene:{node['id']}:{key}", "equals": True}
        for key in result["transitions"][1]["trigger_fact_ids"]
    ]
    assert node["story_beat"]["must_not_happen"] == list(dict.fromkeys([
        *result["scenes"][0]["must_not_happen"],
        *result["scenes"][0]["character_state"]["scene_boundaries"],
    ]))
    assert sdk.apply_branch(project["project_id"], draft["draft_id"], base_revision=project["revision"])["project"]["revision"] == revision


def test_invalid_explicit_draft_does_not_partially_apply_to_project(opened):
    sdk = opened[0].sdk
    original = branchable_project()
    created = sdk.create_project()
    project = sdk._store.finish_generation(
        created["project_id"], base_revision=created["revision"],
        story=original["story"], mainline_node_ids=original["authoring"]["mainline_node_ids"],
    )
    project, service, plan, result = _case(project)
    draft = service.finish_path(plan, result)
    draft["transitions"][1]["trigger_fact_ids"] = ["unwritten_result"]
    sdk._store.save_branch_draft(project["project_id"], base_revision=project["revision"], draft=draft)
    path = sdk.root / f"{project['project_id']}.json"
    before = path.read_bytes()
    with pytest.raises(NumericV2BranchError):
        sdk.apply_branch(project["project_id"], draft["draft_id"], base_revision=project["revision"])
    assert path.read_bytes() == before
