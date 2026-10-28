"""Assess the plot direction used by Runtime without granting new edit permissions."""

from copy import deepcopy
import json

import pytest

from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError, _content_hash

from .test_numeric_v2_generation import _plan_review_reply, _quality_payload, _quality_story_context, _quality_wire_payload


DIRECTION = "  开场信件由女主持有。\n她先核对封口，再等玩家决定是否交给柜员。  "


def test_real_assessment_and_revision_preserve_direction_evidence_and_permissions():
    story, authoring = _quality_story_context()
    beat = story["nodes"][0]["story_beat"]
    beat.update(narrative_summary=DIRECTION, opening_scene="开场信件仍由玩家保管。")
    before = deepcopy(story)
    calls = []

    def reply(messages, **options):
        operation = options["operation"]
        calls.append(operation)
        body = json.loads(messages[1]["content"])
        node = body["story_outline"]["mainline"][0]
        if operation == "numeric_v2_fact_review":
            if body["checked_node_ids"] != ["mainline_01"]:
                return json.dumps({"checked_node_ids": body["checked_node_ids"], "issues": []})
            assert node["narrative_summary"]["text"] == DIRECTION
            return json.dumps({"checked_node_ids": body["checked_node_ids"], "issues": [{
                "category": "ownership", "severity": "major", "target_node_ids": [node["id"]],
                "problem": "两处开场写了互斥的信件持有人。",
                "modification_plan": "将详细方向中的开场持有人改回玩家。",
                "expected_result": "开场信件持有人一致。", "preserve": ["保留正确开场"],
                # A model-supplied text label must not grant permission to edit this read-only field.
                "repair_scope": "text", "repair_targets": [{"node_id": node["id"], "field": "/narrative_summary"}],
                "evidence_refs": [node["narrative_summary"]["ref"], node["opening_scene"]["ref"]],
            }]})
        assert node["narrative_summary"] == DIRECTION
        if operation == "numeric_v2_fact_evidence_review":
            issue = body["proposed_issues"][0]
            assert issue["evidence"][0] == {"path": "/mainline/0/narrative_summary", "quote": DIRECTION}
            return json.dumps({"checks": [{
                "issue_id": issue["issue_id"], "verdict": "supported", "reason": "同一开场持有人冲突。",
                "target_checks": [{**issue["repair_targets"][0], "requires_change": True, "reason": "详细方向有误。"}],
            }]})
        if operation == "numeric_v2_quality_assessment":
            fact = body["fact_review"]["issues"][0]
            assert fact["repair_scope"] == "structure" and fact["repairable"] is False
            assert fact["evidence"][0]["quote"] == DIRECTION
            return json.dumps(_quality_wire_payload(_quality_payload(
                weak_dimension="prose_style", target_node_id=node["id"],
            )))
        if operation == "numeric_v2_repair_plan_review":
            assert all(target["field"] != "/narrative_summary"
                       for plan in body["plans"] for target in plan["repair_targets"])
            return _plan_review_reply(messages)
        assert operation == "numeric_v2_quality_single_node_optimization"
        assert body["target_node"]["story_beat"]["narrative_summary"] == DIRECTION
        return json.dumps({"node_updates": [{"node_id": node["id"], "story_beat": {"summary": "修订后的简要概览。"}}]})

    assessor = NumericV2QualityAssessor(reply)
    report = assessor.assess(story=story, setup={}, authoring=authoring)
    context = assessor._assessment_context(story, {}, authoring)
    assert report["content_sha256"] == _content_hash(context)
    fact = next(issue for issue in report["issues"] if issue["source"] == "facts")
    assert fact["repairable"] is False and fact["repair_scope"] == "structure"
    assert report["passed"] is False
    repaired = assessor.optimize_node(story=story, setup={}, authoring=authoring,
                                     assessment=report, node_id="mainline_01")
    assert repaired["nodes"][0]["story_beat"]["narrative_summary"] == DIRECTION
    expected = deepcopy(before)
    expected["nodes"][0]["story_beat"]["summary"] = "修订后的简要概览。"
    assert repaired == expected and story == before
    assert len(calls) == len(story["nodes"]) + 4
    readonly_report = {**report, "issues": [fact], "relationship_advice": []}
    with pytest.raises(QualityAssessmentError, match="quality_node_not_repairable"):
        assessor.optimize_node(story=story, setup={}, authoring=authoring,
                               assessment=readonly_report, node_id="mainline_01")
    assert len(calls) == len(story["nodes"]) + 4
    with pytest.raises(QualityAssessmentError, match="invalid_quality_repair"):
        assessor._apply_node_updates(story, {"node_updates": [{
            "node_id": "mainline_01", "story_beat": {"narrative_summary": "未经授权的替换。"},
        }]}, ["mainline_01"])
