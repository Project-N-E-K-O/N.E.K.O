"""A bad location neither erases factual evidence nor authorizes guessed edits."""
from copy import deepcopy
import json

import pytest

from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from .test_numeric_v2_generation import _quality_story_context, _generation_setup, _quality_reply, _quality_payload, _fact_review_payload
from .test_numeric_v2_evidence import review_payload


@pytest.mark.parametrize("repair", ["valid", "unresolved", "drop", "malformed"])
def test_invalid_fact_location_gets_one_reselection_without_losing_the_issue(repair):
    story, authoring = _quality_story_context()
    before = deepcopy(story)
    fallback = _quality_reply(_quality_payload())
    corrections = []
    first = None

    def reply(messages, **options):
        nonlocal first
        op = options["operation"]
        data = json.loads(messages[1]["content"])
        if op == "numeric_v2_fact_review" and data["audit_task"]["id"] == "node:mainline_01":
            node = data["story_outline"]["mainline"][0]
            first = _fact_review_payload(data, [{
                "category": "sequence", "severity": "major", "target_node_ids": [node["id"]],
                "problem": "登记结果与完成条件承接不一致。", "modification_plan": "核对完成条件所需的实际结果。",
                "expected_result": "完成条件与已保证的结果一致。", "preserve": ["保留正确开场。"],
                "repair_scope": "structure", "repair_targets": [{"node_id": node["id"],
                    "field": "/outgoing_routes/0/completion_contract/all"}],
                "evidence_refs": [data["contract_targets"][0]["definition_ref"], node["opening_scene"]["ref"]],
            }])
            return json.dumps(first)
        if op == "numeric_v2_fact_review_format_repair":
            correction = json.loads(messages[-1]["content"])
            assert "invalid_fact_repair_target" in correction["validation_error"]
            assert "/completion_contract" in data["repair_target_catalog"]["mainline_01"]
            assert "/outgoing_routes/0/completion_contract/all" not in data["repair_target_catalog"]["mainline_01"]
            corrections.append(correction)
            result = deepcopy(first)
            if repair == "valid": result["issues"][0]["repair_targets"][0]["field"] = "/completion_contract"
            elif repair == "drop": result["issues"] = []
            elif repair == "malformed": result["issues"] = {"unexpected":"object"}
            return json.dumps(result)
        if op == "numeric_v2_fact_evidence_review":
            return json.dumps(review_payload(data["proposed_issues"], "supported"))
        return fallback(messages, **options)

    assessor = NumericV2QualityAssessor(reply)
    if repair in {"drop", "malformed"}:
        with pytest.raises(QualityAssessmentError):
            assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    else:
        report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
        issue, = report["issues"]
        assert issue["problem"] == first["issues"][0]["problem"]
        assert issue["repairable"] is False
        assert issue["evidence_review"]["verdict"] == "supported"
        assert report["passed"] is False
    assert len(corrections) == 1
    assert story == before
