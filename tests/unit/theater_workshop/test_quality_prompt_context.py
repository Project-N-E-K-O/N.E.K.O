"""Verify assessment inputs and output limits without widening node repair permissions."""
from copy import deepcopy
import json

import pytest

from theater_workshop.sdk.generation.facts import build_fact_sources
from theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from theater_workshop.sdk.generation.repair import classify_repair
from .test_numeric_v2_evidence import review_payload
from .test_numeric_v2_generation import (
    _generation_setup, _quality_payload, _quality_reply, _quality_story_context, _quality_wire_payload,
)
from .test_numeric_v2_plan_review import ready_payload


@pytest.mark.parametrize("effect", ["none", "positive", "negative", None])
def test_assessment_receives_actual_relationship_effect_and_existing_missing_default(effect):
    story, authoring = _quality_story_context()
    metric = story["metric_schema"]["trust"]
    if effect is None:
        metric.pop("relationship_effect", None)
    else:
        metric["relationship_effect"] = effect
    before = deepcopy(story)
    assessor = NumericV2QualityAssessor()
    received = []
    original = _quality_reply(_quality_payload())

    def reply(messages, **options):
        received.append((options["operation"], json.loads(messages[1]["content"])["story_outline"]))
        return original(messages, **options)

    assessor.call_llm = reply
    assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert [operation for operation, _ in received] == [
        "numeric_v2_fact_review", "numeric_v2_quality_assessment",
    ]
    assert received[0][1]["metrics"][0]["relationship_effect"]["text"] == (effect or "none")
    assert received[1][1]["metrics"][0]["relationship_effect"] == (effect or "none")
    assert story == before


def test_formal_background_is_citable_and_reaches_all_assessment_and_repair_stages():
    story, authoring = _quality_story_context()
    background = '花店的前门整日开放。\n门口贴着“访客可直接进入”的告示。'
    opening = "女主仍称前门关闭，要求访客从后门进入。"
    corrected = "女主指向敞开的前门，让访客看到入口的告示。"
    story["intro"]["background"] = background
    story["nodes"][0]["story_beat"]["opening_scene"] = opening
    # 未导出的世界规则不能为了补全正式背景而一并成为运行事实。
    authoring["world"] = {"rules": ["HIDDEN_AUTHOR_RULE_DO_NOT_PROJECT"]}
    before, authoring_before = deepcopy(story), deepcopy(authoring)
    assessor = NumericV2QualityAssessor()
    calls = []
    target = {"node_id": "mainline_01", "field": "/opening_scene"}

    def reply(messages, **options):
        operation = options["operation"]
        calls.append(operation)
        request = json.loads(messages[1]["content"])
        assert "HIDDEN_AUTHOR_RULE_DO_NOT_PROJECT" not in messages[1]["content"]
        outline = request["story_outline"]
        if operation == "numeric_v2_fact_review":
            assert outline["background"]["text"] == background
            assert list(outline).index("background") < list(outline).index("mainline")
            return json.dumps({"checked_node_ids": request["checked_node_ids"], "issues": [{
                "category": "state", "severity": "major", "target_node_ids": ["mainline_01"],
                "problem": "开场与正式背景的前门状态冲突。", "modification_plan": "仅把开场的入口说明改成前门开放。",
                "expected_result": "开场承接正式背景。", "preserve": ["保留正式背景原文。"],
                "repair_scope": "text", "repair_targets": [target],
                "evidence_refs": [outline["background"]["ref"], outline["mainline"][0]["opening_scene"]["ref"]],
            }]}, ensure_ascii=False)
        assert outline["background"] == background
        assert all("/background" not in node["text_repair_fields"] for node in outline["mainline"])
        if operation == "numeric_v2_fact_evidence_review":
            # 复核尚未发生，不能把由“缺少复核”推导的否定结论反过来交给裁判。
            assert "repairable" not in request["proposed_issues"][0]
            assert "repair_reason" not in request["proposed_issues"][0]
            return json.dumps(review_payload(request["proposed_issues"]), ensure_ascii=False)
        if operation == "numeric_v2_quality_assessment":
            return json.dumps(_quality_wire_payload(_quality_payload()), ensure_ascii=False)
        if operation == "numeric_v2_repair_plan_review":
            return json.dumps(ready_payload(request["plans"]), ensure_ascii=False)
        assert operation == "numeric_v2_quality_single_node_optimization"
        assert request["accepted_suggestions"][0]["repair_targets"] == [target]
        return json.dumps({"node_updates": [{"node_id": "mainline_01", "story_beat": {
            "opening_scene": corrected,
        }}]}, ensure_ascii=False)

    assessor.call_llm = reply
    report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert report["issues"][0]["evidence"] == [
        {"path": "/background", "quote": background},
        {"path": "/mainline/0/opening_scene", "quote": opening},
    ]
    assert report["issues"][0]["repairable"] is True
    repaired = assessor.optimize_node(story=story, setup=_generation_setup(), authoring=authoring,
                                      assessment=report, node_id="mainline_01")
    expected = deepcopy(before)
    expected["nodes"][0]["story_beat"]["opening_scene"] = corrected
    assert repaired == expected
    assert story == before and authoring == authoring_before
    assert calls == ["numeric_v2_fact_review", "numeric_v2_fact_evidence_review", "numeric_v2_quality_assessment",
                     "numeric_v2_repair_plan_review", "numeric_v2_quality_single_node_optimization"]


def test_different_formal_backgrounds_change_assessment_and_citation_inputs():
    story, authoring = _quality_story_context()
    inputs, sources = [], []
    for background in ("花店前门开放。", "花店前门关闭，访客须走后门。"):
        story["intro"]["background"] = background
        context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
        inputs.append(context)
        _, evidence = build_fact_sources(context)
        sources.append(evidence)
    assert inputs[0] != inputs[1]
    assert sources[0] != sources[1]


@pytest.mark.parametrize("update", [
    {"background": "重写全局背景。"},
    {"story_beat": {"background": "把背景伪装成节点文本。"}},
])
def test_formal_background_does_not_gain_node_repair_permission(update):
    story, authoring = _quality_story_context()
    before = deepcopy(story)
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    issue = {"target_node_ids": ["mainline_01"], "repair_scope": "text",
             "repair_targets": [{"node_id": "mainline_01", "field": "/background"}]}
    assert classify_repair(context, issue)["repairable"] is False
    with pytest.raises(QualityAssessmentError, match="invalid_quality_repair"):
        NumericV2QualityAssessor._apply_node_updates(story, {"node_updates": [
            {"node_id": "mainline_01", **update},
        ]}, ["mainline_01"])
    assert story == before


def test_actual_literature_request_declares_metric_count_limit():
    story, authoring = _quality_story_context()
    assessor = NumericV2QualityAssessor()
    original = _quality_reply(_quality_payload())
    seen = []

    def reply(messages, **options):
        if options["operation"] == "numeric_v2_quality_assessment":
            seen.append(messages[0]["content"])
            request = json.loads(messages[1]["content"])
            assert request["allowed_target_node_ids"] == sorted(node["id"] for node in story["nodes"])
            assert "metrics 不是节点" in messages[0]["content"]
            assert "positive、negative、none" in messages[0]["content"]
        return original(messages, **options)

    assessor.call_llm = reply
    assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert len(seen) == 1
    assert "recommended_count 必须为 0—4 的整数" in seen[0]


@pytest.mark.parametrize("count,accepted", [(0, True), (4, True), (5, False), (-1, False), (1.5, False), (True, False)])
def test_metric_advice_count_remains_strict_and_is_never_clamped(count, accepted):
    story, authoring = _quality_story_context()
    assessor = NumericV2QualityAssessor()
    payload = _quality_payload()
    payload["metric_advice"]["recommended_count"] = count
    assessor.call_llm = _quality_reply(payload)
    if accepted:
        report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
        assert report["metric_advice"]["recommended_count"] == count
    else:
        with pytest.raises(QualityAssessmentError, match="invalid_quality_assessment") as error:
            assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
        assert error.value.phase == "literature"
