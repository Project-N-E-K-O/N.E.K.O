"""建议范围、共享修法和实际修改边界；与自然语言语义压测分开验证。"""
from copy import deepcopy
import json
import pytest

from theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from theater_workshop.sdk.generation.repair import classify_repair, assign_shared_plans
from .test_numeric_v2_generation import _quality_story_context, _generation_setup, _quality_payload, _reviewed_fixture


def fixture(field="/character_state/catgirl"):
    story, authoring = _quality_story_context()
    assessor = NumericV2QualityAssessor()
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    raw = _quality_payload(weak_dimension="plot", target_node_id="mainline_01")
    raw["issues"][0]["repair_targets"] = [{"node_id": "mainline_01", "field": field}]
    report = assessor._validated_assessment(context, raw)
    _reviewed_fixture(report)
    return story, authoring, assessor, context, report


@pytest.mark.parametrize("field", ["/goals/0/description", "/acting_contract/dialogue_policy", "/outgoing_routes/0/transition_contract/source_ids", "/narrative_focus", "/unknown", "/character_state"])
def test_text_label_cannot_grant_permission_to_structural_unknown_or_parent_fields(field):
    # 模型说text不能覆盖真实字段权限；问题和原方案仍然可供作者查看。
    _, _, _, _, report = fixture(field)
    issue = report["issues"][0]
    assert issue["model_repair_scope"] == "text"
    assert issue["repairable"] is False
    assert issue["repair_scope"] == "structure"
    assert issue["modification_plan"]
    assert issue["repair_reason"]


def test_state_permission_uses_story_structure_not_author_cache():
    story, authoring, assessor, _, report = fixture()
    assert report["issues"][0]["repairable"] is True
    del story["nodes"][0]["story_beat"]["character_state"]
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    assert classify_repair(context, report["issues"][0])["repairable"] is False


def test_shared_plan_preserves_all_impacts_and_never_merges_different_targets_or_text():
    # 精确一致才共用方案；同节点的不同修法或同修法的不同字段必须分开。
    _, _, _, _, report = fixture()
    first = report["issues"][0]
    duplicate = deepcopy(first)
    duplicate.update(issue_id="quality_issue_02", problem="同一错误影响节奏", dimension="pacing")
    other_field = deepcopy(first)
    other_field.update(issue_id="quality_issue_03", repair_targets=[{"node_id": "mainline_01", "field": "/opening_scene"}])
    other_plan = deepcopy(first)
    other_plan.update(issue_id="quality_issue_04", modification_plan="不同的修改方案")
    report["issues"] += [duplicate, other_field, other_plan]
    assign_shared_plans(report)
    assert len(report["issues"]) == 4
    assert duplicate["shared_plan_issue_id"] == first["issue_id"]
    assert duplicate["problem"] == "同一错误影响节奏"
    assert "shared_plan_issue_id" not in other_field
    assert "shared_plan_issue_id" not in other_plan


@pytest.mark.parametrize("case", ["outside", "noop", "old", "structure"])
def test_repair_rejects_unplanned_changes_noop_old_report_and_structure(case):
    story, authoring, assessor, _, report = fixture("/goals/0/description" if case == "structure" else "/character_state/catgirl")
    before = deepcopy(story)
    calls = []
    if case == "old": report.pop("repair_plan_version")
    patch = {"character_state": {"catgirl_state": story["nodes"][0]["story_beat"]["character_state"]["catgirl_state"]}}
    if case == "outside": patch = {"opening_scene": "未经方案确认的另一个开场。"}
    def reply(*args, **kwargs):
        calls.append(kwargs["operation"])
        return json.dumps({"node_updates": [{"node_id": "mainline_01", "story_beat": patch}]}, ensure_ascii=False)
    assessor.call_llm = reply
    with pytest.raises(QualityAssessmentError) as caught:
        assessor.optimize_node(story=story, setup=_generation_setup(), authoring=authoring, assessment=report, node_id="mainline_01")
    assert caught.value.code == {"outside":"quality_repair_outside_plan", "noop":"quality_repair_no_change",
                                 "old":"quality_reassessment_required", "structure":"quality_node_not_repairable"}[case]
    assert len(calls) == (0 if case in {"old", "structure"} else 1)
    assert story == before


def test_single_repair_call_receives_one_plan_with_all_impacts():
    story, authoring, assessor, _, report = fixture()
    second = deepcopy(report["issues"][0]);second.update(issue_id="quality_issue_02", problem="第二种影响")
    report["issues"].append(second)
    def reply(messages, **kwargs):
        suggestions = json.loads(messages[1]["content"])["accepted_suggestions"]
        assert len(suggestions) == 1
        assert suggestions[0]["related_impacts"][0]["problem"] == "第二种影响"
        return json.dumps({"node_updates": [{"node_id": "mainline_01", "story_beat": {
            "character_state": {"catgirl_state": "女主双手空着，旧信已经放在柜台上。"},
        }}]}, ensure_ascii=False)
    assessor.call_llm = reply
    repaired = assessor.optimize_node(story=story, setup=_generation_setup(), authoring=authoring, assessment=report, node_id="mainline_01")
    assert repaired["nodes"][1:] == story["nodes"][1:]
    assert repaired["nodes"][0]["story_beat"]["opening_scene"] == story["nodes"][0]["story_beat"]["opening_scene"]
