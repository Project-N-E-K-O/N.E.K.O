"""An explicit empty bridge contract preserves real source and target delivery."""

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2 import NumericV2CompileError, NumericV2Compiler
from services.theater.numeric_v2_actor import _transition_contract_for_actor
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError, _parse_output
from services.theater.numeric_v2_cast import NumericV2CastProjection
from services.theater.numeric_v2_performance import performance_content_blocks
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError

from .numeric_v2_fixture import numeric_v2_story
from .test_numeric_v2_generation import (
    _generation_setup, _quality_payload, _quality_story_context, _reviewed_fixture,
)
from .test_sdk_lifecycle import opened


def _optional_story():
    story = numeric_v2_story()
    for route in story["nodes"][0]["route_gates"]:
        route["transition_contract"]["must_deliver"] = []
    return story


@pytest.mark.parametrize("bridge", [None, "两人沿已公开的通道来到廊下。"])
def test_explicit_empty_delivery_compiles_and_only_independent_bridge_is_required(bridge):
    story = _optional_story()
    contract = story["nodes"][0]["route_gates"][0]["transition_contract"]
    if bridge is not None:
        contract["bridge_scene_narration"] = bridge
    compiled = NumericV2Compiler().compile_v2_2(story)
    assert compiled.story == story
    cast = NumericV2CastProjection.from_story(story, player_name="老谢", catgirl_name="小葵")
    projected = _transition_contract_for_actor(cast, contract, target_opening="花店门外阳光温暖。")
    assert projected["must_deliver"] == []
    assert projected["bridge_required"] is (bridge is not None)
    assert projected["bridge_scene_narration"] == (bridge or "")


@pytest.mark.parametrize("value", [None, "交付结果", [" "], [None]], ids=['None', '交付结果', 'value6', 'value7'])
def test_empty_delivery_permission_does_not_accept_wrong_shapes_or_blank_items(value):
    story = numeric_v2_story()
    story["nodes"][0]["route_gates"][0]["transition_contract"]["must_deliver"] = value
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert any("must_deliver" in issue.path for issue in caught.value.issues)


def test_delivery_field_is_still_required_and_existing_package_bytes_do_not_change():
    story = numeric_v2_story()
    expected = json.dumps(story, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert NumericV2Compiler().compile_v2_2(story).json_bytes == expected
    del story["nodes"][0]["route_gates"][0]["transition_contract"]["must_deliver"]
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert any("must_deliver" in issue.path for issue in caught.value.issues)


def _candidate():
    return {
        "source_scene_narration": "旧友答道：“剑匣已收到，多谢。”",
        "source_performance": "（微微颔首）这趟路没有白走。",
        "bridge_scene_narration": "",
        "target_scene_narration": "院门外的积雪映着阳光。",
        "target_performance": "（看向自己的马匹）回程的路还长。",
        "suggested_inputs": [],
    }


@pytest.mark.parametrize("field,value", [
    ("source_performance", None), ("target_performance", ""),
    ("target_scene_narration", ""), ("bridge_scene_narration", None),
])
def test_optional_bridge_does_not_excuse_invalid_source_or_target(field, value):
    candidate = _candidate()
    candidate[field] = value
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps(candidate), transition_required=True, bridge_required=False,
                      source_dialogue_policy="optional")


def test_nonempty_author_bridge_still_rejects_an_empty_generated_bridge():
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps(_candidate()), transition_required=True, bridge_required=True,
                      source_dialogue_policy="optional")


@pytest.mark.asyncio
async def test_empty_bridge_keeps_source_npc_and_target_through_commit_restore_and_fork(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_optional_story()), tmp_path)
    current = await runtime.start_session(session_id="optional-bridge", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    outcome = runtime.prepare_turn(current, TurnRequestV2("finish", 0, "多谢，任务已毕。"), (),
                                   scene_complete=True, natural_ending_ready=True)
    assert outcome.session.status == "ended"
    parsed = _parse_output(json.dumps(_candidate()), transition_required=True, bridge_required=False,
                           source_dialogue_policy="optional")
    performance = runtime.engine.finalize_transition_performance(outcome, parsed,
        target_opening="院门外的积雪映着阳光。", bridge_required=False, source_dialogue_policy="optional")
    assert performance["segments"][1] == {"phase": "transition_bridge", "scene_narration": ""}
    assert performance["segments"][0]["scene_narration"] == _candidate()["source_scene_narration"]
    assert performance["segments"][0]["performance"] == _candidate()["source_performance"]
    assert performance["segments"][2]["performance"] == _candidate()["target_performance"]
    assert [block["text"] for block in performance_content_blocks(performance)][0] == _candidate()["source_scene_narration"]
    committed = await runtime.commit_turn(outcome, performance)
    assert await runtime.restore_session("optional-bridge") == committed
    fork = await runtime.fork_session_for_test("optional-bridge", session_id="bridge-fork", through_revision=1)
    assert fork.session.performance_history[0]["segments"] == performance["segments"]
    assert await runtime.restore_session("bridge-fork") == fork


def test_real_sdk_import_update_compile_validate_export_preserves_empty_array(opened):
    sdk = opened[0].sdk
    project = sdk.import_story(_optional_story())
    changed = deepcopy(project["story"])
    changed["meta"]["revision"] = "optional_bridge_revision"
    project = sdk.update_project(project["project_id"], base_revision=project["revision"],
                                 changes={"story": changed})
    compiled = sdk.compile(project["project_id"], base_revision=project["revision"])
    sdk.validate(project["project_id"], base_revision=project["revision"])
    exported = sdk.export(project["project_id"], base_revision=project["revision"])
    assert exported.json_bytes == compiled["json_bytes"]
    assert json.loads(exported.json_bytes) == changed
    assert sdk.import_story(json.loads(exported.json_bytes))["story"] == changed


@pytest.mark.parametrize("bridge", [None, "两人沿已公开的通道来到廊下。"])
def test_quality_revision_preserves_explicit_empty_delivery_and_existing_bridge(bridge):
    story, authoring = _quality_story_context()
    node = story["nodes"][0]
    route = node["route_gates"][0]
    contract = route["transition_contract"]
    if bridge is None:
        contract.pop("bridge_scene_narration", None)
    else:
        contract["bridge_scene_narration"] = bridge
    original = deepcopy(story)
    assessor = NumericV2QualityAssessor()
    payload = _quality_payload(weak_dimension="plot", target_node_id=node["id"])
    payload["issues"][0]["repair_targets"] = [{
        "node_id": node["id"], "field": "/outgoing_routes/0/transition_contract/must_deliver",
    }]
    assessment = assessor._validated_assessment(
        assessor._assessment_context(story, _generation_setup(), authoring), payload,
    )
    _reviewed_fixture(assessment)
    calls = []

    def reply(messages, **options):
        calls.append((messages, options))
        return json.dumps({"node_updates": [{"node_id": node["id"], "routes": [{
            "route_id": route["id"], "transition_contract": {"must_deliver": []},
        }]}]}, ensure_ascii=False)

    assessor.call_llm = reply
    repaired = assessor.optimize_node(
        story=story, setup=_generation_setup(), authoring=authoring,
        assessment=assessment, node_id=node["id"],
    )
    expected = deepcopy(original)
    expected["nodes"][0]["route_gates"][0]["transition_contract"]["must_deliver"] = []
    assert repaired == expected
    assert story == original
    assert NumericV2Compiler().compile_v2_2(repaired).story == repaired
    repaired_contract = repaired["nodes"][0]["route_gates"][0]["transition_contract"]
    cast = NumericV2CastProjection.from_story(story, player_name="老谢", catgirl_name="小葵")
    projected = _transition_contract_for_actor(cast, repaired_contract, target_opening="廊下阳光温暖。")
    assert projected["bridge_required"] is (bridge is not None)
    assert projected["bridge_scene_narration"] == (bridge or "")
    assert len(calls) == 1
    assert calls[0][1]["operation"] == "numeric_v2_quality_single_node_optimization"


@pytest.mark.parametrize("update", [{"must_deliver": None}, {"must_deliver": [" "]}, {"must_deliver": [None]}, {"bridge_scene_narration": None}, {"bridge_scene_narration": ""}, {"reason": ""}, {}], ids=['update0', 'update6', 'update7', 'update9', 'update10', 'update11', 'update12'])
def test_quality_revision_keeps_shape_and_nonempty_item_checks(update):
    story = numeric_v2_story()
    before = deepcopy(story)
    node = story["nodes"][0]
    payload = {"node_updates": [{"node_id": node["id"], "routes": [{
        "route_id": node["route_gates"][0]["id"], "transition_contract": update,
    }]}]}
    with pytest.raises(QualityAssessmentError, match="invalid_quality_repair"):
        NumericV2QualityAssessor._apply_node_updates(story, payload, [node["id"]])
    assert story == before
