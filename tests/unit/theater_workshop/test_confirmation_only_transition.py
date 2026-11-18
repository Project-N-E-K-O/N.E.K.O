"""Author opt-in for an already-completed stage's confirmation-only accept button."""

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2 import NumericV2CompileError, NumericV2Compiler
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError

from .test_numeric_v2_generation import _generation_setup, _quality_story_context
from .test_sdk_lifecycle import opened


FLAG = "accept_input_confirmation_only"


def confirmation_story():
    story, _ = _quality_story_context()
    node = story["nodes"][0]
    key = f"scene:{node['id']}:completed"
    story.setdefault("fact_contract", {}).setdefault("facts", {})[key] = {
        "value_type": "bool", "visibility": "public",
        "description": "当前阶段的公开任务已经完成。",
    }
    node["completion_contract"] = {"all": [{"key": key, "equals": True}]}
    contract = node["route_gates"][0]["transition_contract"]
    contract.update(fallback_offer="当前事情说清了，我们回到眼前的试卷吧？",
                    accept_input="好，我们看看眼前的试卷。")
    return story, node, contract


@pytest.mark.parametrize("value", [None, "true", 1])
def test_confirmation_only_requires_strict_boolean(value):
    story, _, contract = confirmation_story()
    contract[FLAG] = value
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert any(issue.path.endswith(FLAG) for issue in caught.value.issues)


@pytest.mark.parametrize("missing", ["completion", "empty_completion", "fallback_offer", "accept_input"])
def test_confirmation_only_requires_completed_stage_and_authored_button(missing):
    story, node, contract = confirmation_story()
    contract[FLAG] = True
    if missing == "completion":
        del node["completion_contract"]
    elif missing == "empty_completion":
        node["completion_contract"]["all"] = []
    else:
        del contract[missing]
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert any(issue.code == "transition_confirmation_only_invalid" for issue in caught.value.issues)


@pytest.mark.parametrize("kind", ["ending", "terminal", "silent_choice", "forbidden", "goal_forbidden"])
def test_confirmation_only_cannot_target_ending_or_replace_silent_exit(kind):
    story, node, contract = confirmation_story()
    contract[FLAG] = True
    if kind in {"ending", "terminal"}:
        target = next(n for n in story["nodes"] if n["id"] == node["route_gates"][0]["target_node_id"])
        target["type"] = kind if kind == "ending" else "scene"
        if kind == "terminal":
            target["terminal"] = True
    elif kind == "silent_choice":
        contract["silent_choice"] = {"destination": "桌边", "arrangement": "继续看试卷"}
    elif kind == "forbidden":
        node["story_beat"]["acting_contract"]["dialogue_policy"] = "forbidden"
    else:
        node["story_beat"]["goals"][-1]["delivery"].setdefault("state_effects", {})["dialogue_policy"] = "forbidden"
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert any(issue.code == "transition_confirmation_only_invalid" for issue in caught.value.issues)


def test_absent_or_false_does_not_require_new_confirmation_preconditions():
    story, _ = _quality_story_context()
    for value in (None, False):
        candidate = deepcopy(story)
        for node in candidate["nodes"]:
            node.pop("completion_contract", None)
            for route in node["route_gates"]:
                contract = route["transition_contract"]
                contract.pop("fallback_offer", None)
                contract.pop("accept_input", None)
                if value is False:
                    contract[FLAG] = False
        assert NumericV2Compiler().compile_v2_2(candidate).story == candidate


def test_condition_narration_is_not_statically_rejected_as_unfulfilled():
    story, node, contract = confirmation_story()
    contract[FLAG] = True
    node["story_beat"]["fixed_narrations"] = [{
        "id": "letter", "text": "信上写着：愿你一路平安。",
        "trigger": {"type": "condition", "condition": "玩家实际打开信封。"},
        "after": [], "required_before_exit": True,
    }]
    node["completion_contract"]["all"].append({"fixed_narration_id": "letter"})
    assert NumericV2Compiler().compile_v2_2(story).story == story


@pytest.mark.parametrize("value", [None, True])
def test_quality_projects_only_declared_confirmation_flag_without_text_edit_permission(value):
    story, node, contract = confirmation_story()
    if value is not None:
        contract[FLAG] = value
    _, authoring = _quality_story_context()
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    projected = next(n for n in context["mainline"] if n["id"] == node["id"])
    target = projected["outgoing_routes"][0]["transition_contract"]
    if value is None:
        assert FLAG not in target
    else:
        assert target[FLAG] is value
    assert not any(FLAG in field for field in projected["text_repair_fields"])
    update = {"node_updates": [{"node_id": node["id"], "routes": [{
        "route_id": node["route_gates"][0]["id"], "transition_contract": {FLAG: True},
    }]}]}
    with pytest.raises(QualityAssessmentError, match="invalid_quality_repair"):
        NumericV2QualityAssessor._apply_node_updates(story, update, [node["id"]])


def test_quality_text_revision_preserves_existing_confirmation_declaration():
    story, node, contract = confirmation_story()
    contract[FLAG] = True
    update = {"node_updates": [{"node_id": node["id"], "routes": [{
        "route_id": node["route_gates"][0]["id"], "transition_contract": {"reason": "本幕任务已完成。"},
    }]}]}
    repaired = NumericV2QualityAssessor._apply_node_updates(story, update, [node["id"]])
    assert repaired["nodes"][0]["route_gates"][0]["transition_contract"][FLAG] is True


def test_real_sdk_roundtrip_keeps_confirmation_flag_and_all_other_story_fields(opened):
    sdk = opened[0].sdk
    story, _, _ = confirmation_story()
    project = sdk.import_story(story)
    updated = deepcopy(project["story"])
    updated["nodes"][0]["route_gates"][0]["transition_contract"][FLAG] = True
    project = sdk.update_project(project["project_id"], base_revision=project["revision"], changes={"story": updated})
    compiled = sdk.compile(project["project_id"], base_revision=project["revision"])
    sdk.validate(project["project_id"], base_revision=project["revision"])
    exported = sdk.export(project["project_id"], base_revision=project["revision"])
    assert exported.json_bytes == compiled["json_bytes"]
    assert json.loads(exported.json_bytes) == updated
    assert sdk.import_story(json.loads(exported.json_bytes))["story"] == updated


@pytest.mark.parametrize("field,code,path", [
    ("story_beat", "expected_object", "nodes[0].story_beat"),
    ("acting_contract", "expected_object", "nodes[0].story_beat.acting_contract"),
    ("goals", "expected_array", "nodes[0].story_beat.goals"),
    ("delivery", "expected_object", "nodes[0].story_beat.goals[0].delivery"),
    ("state_effects", "expected_object", "nodes[0].story_beat.goals[0].delivery.state_effects"),
    ("route_gates", "expected_array", "nodes[0].route_gates"),
])
def test_legacy_malformed_contract_keeps_precise_compiler_issue(field, code, path):
    story, _ = _quality_story_context()
    node = story["nodes"][0]
    node.pop("completion_contract", None)
    assert "completion_contract" not in node
    assert all(FLAG not in route["transition_contract"] for route in node["route_gates"])
    if field == "route_gates":
        node[field] = 1
    elif field == "story_beat":
        node[field] = "invalid"
    elif field == "acting_contract":
        node["story_beat"][field] = "invalid"
    elif field == "goals":
        node["story_beat"][field] = 5
    elif field == "delivery":
        node["story_beat"]["goals"][0][field] = "invalid"
    else:
        node["story_beat"]["goals"][0]["delivery"][field] = "invalid"
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert (code, path) in {(issue.code, issue.path) for issue in caught.value.issues}
    assert all(issue.code != "malformed_package" for issue in caught.value.issues)
