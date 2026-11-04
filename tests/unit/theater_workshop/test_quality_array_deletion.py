"""Approve exact text deletions without widening a repair to the whole array."""

from copy import deepcopy
from itertools import combinations, product
import json

import pytest

from services.theater.numeric_v2 import NumericV2Compiler
from services.theater_workshop.sdk import WorkshopError
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from services.theater_workshop.sdk.generation.repair import changed_fields

from .test_numeric_v2_generation import (
    _generation_setup, _quality_payload, _quality_reply, _quality_story_context, _reviewed_fixture,
)
from .test_sdk_lifecycle import opened, setup_project


DELIVERY = "/outgoing_routes/0/transition_contract/must_deliver"


def _revision(before, after, allowed, *, protected=(), field="must_deliver"):
    story, authoring = _quality_story_context()
    node = story["nodes"][0]
    route = node["route_gates"][0]
    path = "/must_not_happen" if field == "must_not_happen" else DELIVERY.replace("must_deliver", field)
    if field == "must_not_happen":
        node["story_beat"][field] = before
        update = {"node_id": node["id"], "story_beat": {field: after}}
    else:
        route["transition_contract"][field] = before
        update = {"node_id": node["id"], "routes": [{
            "route_id": route["id"], "transition_contract": {field: after},
        }]}
    original = deepcopy(story)
    assessor = NumericV2QualityAssessor()
    payload = _quality_payload(weak_dimension="plot", target_node_id=node["id"])
    payload["issues"][0]["repair_targets"] = [
        {"node_id": node["id"], "field": path + suffix} for suffix in allowed
    ]
    assessment = _reviewed_fixture(assessor._validated_assessment(
        assessor._assessment_context(story, _generation_setup(), authoring), payload,
    ))
    if protected:
        # Use a report with explicit factual preservation decisions; they must override even a broader literary plan.
        assessment["issues"].append({
            **deepcopy(assessment["issues"][0]), "source": "facts", "issue_id": "preserved_fields",
            "repair_targets": [{"node_id": node["id"], "field": path + suffix} for suffix in protected],
            "evidence_review": {"verdict": "unsupported", "target_checks": [
                {"node_id": node["id"], "field": path + suffix, "requires_change": False,
                 "reason": "该原条目正确，必须保留。"} for suffix in protected
            ]},
        })
    calls = []

    def reply(messages, **options):
        calls.append((messages, options))
        return json.dumps({"node_updates": [update]}, ensure_ascii=False)

    assessor.call_llm = reply
    kwargs = dict(story=story, setup=_generation_setup(), authoring=authoring,
                  assessment=assessment, node_id=node["id"])
    return assessor, kwargs, original, calls


@pytest.mark.parametrize("field", ["must_deliver", "must_preserve", "must_not_happen"])
@pytest.mark.parametrize("before,after,allowed,protected", [(["错误要求", "正确要求"], ["正确要求"], ["/0"], ["/1"])], ids=['before1-after1-allowed1-protected1'])
def test_approved_deletion_preserves_other_original_items(field, before, after, allowed, protected):
    assessor, kwargs, original, calls = _revision(before, after, allowed, protected=protected, field=field)
    repaired = assessor.optimize_node(**kwargs)
    expected = deepcopy(original)
    if field == "must_not_happen":
        expected["nodes"][0]["story_beat"][field] = after
    else:
        expected["nodes"][0]["route_gates"][0]["transition_contract"][field] = after
    assert repaired == expected and kwargs["story"] == original
    assert NumericV2Compiler().compile_v2_2(repaired).story == repaired
    assert len(calls) == 1


@pytest.mark.parametrize("before,after,allowed,protected,error", [
    (["错误要求", "正确要求"], [], ["/0"], [], "quality_repair_outside_plan"),
    (["错误要求", "正确要求"], [], [""], ["/1"], "quality_node_not_repairable"),
    (["错误要求", "正确要求"], ["正确要求"], ["/0"], [""], "quality_node_not_repairable"),
    (["重复要求", "重复要求"], ["重复要求"], ["/0"], [], "quality_repair_outside_plan"),
    (["错误要求", "正确要求"], ["正确要求", "新增要求"], ["/0"], [], "quality_repair_outside_plan"),
    (["错误要求", "正确要求", "末项"], ["末项", "正确要求"], ["/0"], [], "quality_repair_outside_plan"),
    (["错误要求", "正确要求", "末项"], ["正确要求", "替换末项"], ["/0"], [], "quality_repair_outside_plan"),
    (["错误要求"], ["替换要求", "新增要求"], ["/0"], [], "quality_repair_outside_plan"),
])
def test_deletion_does_not_authorize_other_changes(before, after, allowed, protected, error):
    assessor, kwargs, original, calls = _revision(before, after, allowed, protected=protected)
    with pytest.raises(QualityAssessmentError, match=error):
        assessor.optimize_node(**kwargs)
    assert kwargs["story"] == original
    # A parent preservation decision can reject the plan before generation; other failures are rejected after parsing.
    assert len(calls) == (0 if error == "quality_node_not_repairable" else 1)


def test_unique_alignment_matches_an_independent_exhaustive_subsequence_oracle():
    for size in range(1, 6):
        for before in product(("甲", "乙"), repeat=size):
            for remaining in range(size):
                for after in product(("甲", "乙"), repeat=remaining):
                    matches = [indices for indices in combinations(range(size), remaining)
                               if tuple(before[index] for index in indices) == after]
                    expected = ([f"/items/{index}" for index in range(size) if index not in matches[0]]
                                if len(matches) == 1 else ["/items"])
                    assert changed_fields(list(before), list(after), "/items") == expected
    assert changed_fields([{"value": "甲"}, {"value": "乙"}], [{"value": "乙"}], "/items") == ["/items"]


def test_public_sdk_saves_approved_deletion_and_invalidates_old_indexed_report(opened, monkeypatch):
    sdk = opened[0].sdk
    project = setup_project(sdk)
    generated = sdk.generate(project["project_id"], base_revision=project["revision"])["project"]
    node = generated["story"]["nodes"][0]
    route = node["route_gates"][0]
    payload = _quality_payload(weak_dimension="plot", target_node_id=node["id"])
    payload["issues"][0]["repair_targets"] = [{"node_id": node["id"], "field": DELIVERY + "/0"}]
    calls = []
    original_reply = _quality_reply(payload)

    def reply(messages, **options):
        calls.append(options["operation"])
        if options["operation"] == "numeric_v2_quality_single_node_optimization":
            return json.dumps({"node_updates": [{"node_id": node["id"], "routes": [{
                "route_id": route["id"], "transition_contract": {"must_deliver": []},
            }]}]})
        return original_reply(messages, **options)

    monkeypatch.setattr(sdk._quality, "call_llm", reply)
    scored = sdk.assess_quality(project["project_id"], base_revision=generated["revision"])["project"]
    revised = sdk.optimize_node(project["project_id"], node["id"], base_revision=scored["revision"])["project"]
    assert revised["story"]["nodes"][0]["route_gates"][0]["transition_contract"]["must_deliver"] == []
    assert revised["authoring"]["quality_assessment"]["stale"] is True
    assert sdk.get_project(project["project_id"]) == revised
    before_retry = list(calls)
    with pytest.raises(WorkshopError, match="quality_assessment_stale"):
        sdk.optimize_node(project["project_id"], node["id"], base_revision=revised["revision"])
    assert calls == before_retry
