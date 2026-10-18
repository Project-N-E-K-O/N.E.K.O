"""Verify conditional review, complete reports, score deductions and preserved fields; mock models test deterministic boundaries only."""
from copy import deepcopy
import json
import pytest
from theater_workshop.sdk.generation.evidence import validate_evidence_review, review_targets
from theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from .test_numeric_v2_generation import _quality_story_context, _generation_setup, _quality_payload, _quality_wire_payload, _plan_review_reply


def test_scoring_and_repair_share_runtime_rules_without_story_specific_examples():
    # All five entry points must use the same semantics so literary/revision stages cannot reinterpret the framework after factual review corrects it.
    from theater_workshop.sdk.generation.runtime_rules import SCORING_RUNTIME_RULES
    from theater_workshop.sdk.generation.facts import FACT_REVIEW_PROMPT
    from theater_workshop.sdk.generation.evidence import EVIDENCE_REVIEW_PROMPT
    from theater_workshop.sdk.generation.plan_review import PLAN_REVIEW_PROMPT
    from theater_workshop.sdk.generation.quality import _ASSESSMENT_PROMPT, _NODE_OPTIMIZATION_PROMPT
    for prompt in (FACT_REVIEW_PROMPT, EVIDENCE_REVIEW_PROMPT, _ASSESSMENT_PROMPT, _NODE_OPTIMIZATION_PROMPT, PLAN_REVIEW_PROMPT):
        assert prompt.count(SCORING_RUNTIME_RULES) == 1
        # Fact checking must not promote creative preferences into runtime prohibitions; revisions must preserve existing causality.
        assert "不因配角少而扣分或建议增加配角戏份" in prompt
        assert "不能仅因出现配角或配角说话就报告事实错误" in prompt
        assert "不删除既有因果必需的配角回应" in prompt
    assert "不以逐项完成 goals 或枚举 source_ids 作为换幕条件" in SCORING_RUNTIME_RULES
    assert "暂缓或拒绝可留在原节点" in SCORING_RUNTIME_RULES
    assert "通用行动权约束不声明存在新的待办" in SCORING_RUNTIME_RULES
    assert "一人示范不代替另一人的亲手操作" in SCORING_RUNTIME_RULES
    assert "一轮结果不能在出口或结局扩大为全员完成" in SCORING_RUNTIME_RULES
    assert "不据此新增逐人逐步确认" in SCORING_RUNTIME_RULES
    assert "女主的演绎权限" in SCORING_RUNTIME_RULES
    assert "明确要求玩家作新的选择或等待其回答" in SCORING_RUNTIME_RULES
    assert "不能把防止提前结束写成永远不得结束" in SCORING_RUNTIME_RULES
    assert "同一物件在同一时点只能有一份相容状态" in SCORING_RUNTIME_RULES
    assert "本幕准备分工与另一时段的见面应分开邀约" in SCORING_RUNTIME_RULES
    assert "world.rules 与 intervention_capacity 是未直接导出的策划字段" in SCORING_RUNTIME_RULES
    assert all(name not in SCORING_RUNTIME_RULES for name in ("种子", "天文馆", "圆扣", "明信片"))


def review_payload(issues, verdict="supported", keep=()):
    # The test model explicitly answers field by field to simulate review, rather than approving the original report by default in production.
    return {"checks": [{"issue_id": i["issue_id"], "verdict": verdict, "reason": "原文核对结论",
        "target_checks": [{**t, "requires_change": verdict == "supported" and t["field"] not in keep,
                           "reason": "此处保留或改动的依据"} for t in review_targets(i)]} for i in issues]}


def wire_fact(request):
    outline = request["story_outline"]["mainline"][0]
    return {"checked_node_ids": request["checked_node_ids"], "issues": [{
        "category": "state", "severity": "major", "target_node_ids": [outline["id"]],
        "problem": "原报告：开场与状态矛盾", "modification_plan": "原方案：同时修改状态和开场",
        "expected_result": "两处相容", "preserve": [], "repair_scope": "text",
        "repair_targets": [{"node_id": outline["id"], "field": f} for f in ("/character_state/catgirl", "/opening_scene")],
        "evidence_refs": [outline["opening_scene"]["ref"], outline["character_state"]["catgirl"]["ref"]]}]}


@pytest.mark.parametrize("case", ["missing", "duplicate", "unknown", "field_missing", "field_duplicate", "field_new", "truthy", "unknown_verdict", "contradictory", "empty_reason"])
def test_evidence_review_rejects_incomplete_or_expanded_results(case):
    # Missing entries, string booleans, or new fields cannot expand revision permissions; failure leaves the original report unchanged.
    issues = [{"issue_id": "f1", "problem": "保留原问题", "repair_targets": [{"node_id": "n", "field": "/opening_scene"}]}]
    before = deepcopy(issues);payload = review_payload(issues);check = payload["checks"][0]
    if case == "missing": payload["checks"] = []
    elif case == "duplicate": payload["checks"] *= 2
    elif case == "unknown": check["issue_id"] = "f2"
    elif case == "field_missing": check["target_checks"] = []
    elif case == "field_duplicate": check["target_checks"] *= 2
    elif case == "field_new": check["target_checks"][0]["field"] = "/summary"
    elif case == "truthy": check["target_checks"][0]["requires_change"] = "false"
    elif case == "unknown_verdict": check["verdict"] = "maybe"
    elif case == "contradictory": check["verdict"] = "unsupported"
    elif case == "empty_reason": check["target_checks"][0]["reason"] = " "
    with pytest.raises(ValueError, match="invalid_fact_evidence_review"):
        validate_evidence_review(issues, payload)
    assert issues == before


@pytest.mark.parametrize("verdict", ["supported", "unsupported", "uncertain", "no_issues"])
def test_conditional_review_keeps_originals_and_controls_score_and_repair(verdict):
    story, authoring = _quality_story_context();before = deepcopy(story)
    assessor = NumericV2QualityAssessor();calls = []
    def reply(messages, **kwargs):
        request = json.loads(messages[1]["content"]);calls.append(kwargs["operation"])
        assert kwargs["max_retries"] == 1
        if kwargs["operation"] == "numeric_v2_fact_review":
            return json.dumps({"checked_node_ids": request["checked_node_ids"], "issues": []} if verdict == "no_issues" else wire_fact(request), ensure_ascii=False)
        if kwargs["operation"] == "numeric_v2_fact_evidence_review":
            return json.dumps(review_payload(request["proposed_issues"], verdict, keep=("/opening_scene",)), ensure_ascii=False)
        if kwargs["operation"] == "numeric_v2_repair_plan_review":
            return _plan_review_reply(messages)
        facts = request["fact_review"]
        assert len(facts["issues"]) == (1 if verdict == "supported" else 0)
        # In real stress testing, replaying a fully rejected issue made the literary model deduct points again; pass only IDs forbidden for association, without replaying accusations.
        assert "excluded_issues" not in facts
        assert len(facts["excluded_issue_ids"]) == (1 if verdict in ("unsupported", "uncertain") else 0)
        if verdict in ("unsupported", "uncertain"):
            assert "原报告：开场与状态矛盾" not in messages[1]["content"]
        return json.dumps(_quality_wire_payload(_quality_payload()), ensure_ascii=False)
    assessor.call_llm = reply
    report = assessor.assess(story=story, authoring=authoring, setup=_generation_setup())
    assert len(calls) == (4 if verdict == "supported" else 2 if verdict == "no_issues" else 3)
    assert report["passed"] is (verdict != "supported")
    assert report["repair_plan_version"] == 3
    assert story == before
    if verdict == "no_issues":
        assert report["fact_check"]["evidence_review_status"] == "not_needed"
        return
    issue = report["issues"][0]
    assert issue["problem"] == "原报告：开场与状态矛盾"
    assert issue["modification_plan"] == "原方案：同时修改状态和开场"
    assert len(issue["repair_targets"]) == 2
    assert issue["repairable"] is (verdict == "supported")
    # Even if the original plan still requests an opening change, preservation requirements from review must reach the actual revision request; return no candidate on scope violations.
    def patch(messages, **kwargs):
        request = json.loads(messages[1]["content"])
        assert request["accepted_suggestions"][0]["repair_targets"] == [{"node_id": "mainline_01", "field": "/character_state/catgirl"}]
        return json.dumps({"node_updates": [{"node_id": "mainline_01", "story_beat": {"opening_scene": "越界重写正确开场"}}]})
    assessor.call_llm = patch
    with pytest.raises(QualityAssessmentError, match="quality_repair_outside_plan" if verdict == "supported" else "quality_node_not_repairable"):
        assessor.optimize_node(story=story, authoring=authoring, setup=_generation_setup(), assessment=report, node_id="mainline_01")
    assert story == before


def test_rejected_fact_id_cannot_supply_literary_deduction():
    # Unsupported issues must not cause renewed deductions through literary associations with fact IDs.
    story, authoring = _quality_story_context();assessor = NumericV2QualityAssessor()
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    raw = _quality_payload();raw["scores"]["plot"].update(score=60, fact_issue_ids=["rejected"])
    assessor.call_llm = lambda *args, **kwargs: json.dumps(_quality_wire_payload(raw))
    with pytest.raises(QualityAssessmentError):
        assessor._assess_literature(context, {"issues": [], "excluded_issue_ids": ["rejected"]})




def test_protected_field_blocks_literary_parent_and_unnecessary_node():
    from theater_workshop.sdk.generation.repair import classify_repair
    # When an array item is preserved, literary plans cannot bypass that by replacing the whole array; nodes requiring no change within the same issue remain non-executable.
    story, authoring = _quality_story_context();assessor = NumericV2QualityAssessor()
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    first, second = context["mainline"][:2]
    issue = {"target_node_ids": [first["id"]], "repair_scope": "text",
             "repair_targets": [{"node_id": first["id"], "field": "/must_not_happen"}]}
    assert classify_repair(context, issue)["repairable"] is True
    assert classify_repair(context, issue, protected=[{"node_id": first["id"], "field": "/must_not_happen/0"}])["repairable"] is False
    targets = [{"node_id": node["id"], "field": "/opening_scene"} for node in (first, second)]
    fact = {"source": "facts", "repair_scope": "text", "target_node_ids": [first["id"], second["id"]], "repair_targets": targets,
            "evidence_review": {"verdict": "supported", "target_checks": [{**t, "requires_change": i == 0} for i, t in enumerate(targets)]}}
    classified = classify_repair(context, fact)
    assert classified["repairable"] is True
    assert classified["repair_node_ids"] == [first["id"]]
    assert classified["target_node_ids"] == [first["id"], second["id"]]
    assert classified["repair_targets"] == targets
    assessor.call_llm = lambda *args, **kwargs: pytest.fail("保留节点不能调用修订模型")
    with pytest.raises(QualityAssessmentError, match="quality_node_not_repairable"):
        assessor.optimize_node(story=story, setup=_generation_setup(), authoring=authoring,
            assessment={"repair_plan_version": 3, "issues": [fact]}, node_id=second["id"])


@pytest.mark.parametrize("version", [None, 1, 2])
def test_pre_evidence_reports_need_reassessment_before_model_call(version):
    # Both old fieldless reports and previous-version reports with fields but no review remain readable, but cannot execute directly.
    story, authoring = _quality_story_context();assessor = NumericV2QualityAssessor()
    assessor.call_llm = lambda *args, **kwargs: pytest.fail("旧报告不能调用模型")
    with pytest.raises(QualityAssessmentError, match="quality_reassessment_required"):
        assessor.optimize_node(story=story, setup=_generation_setup(), authoring=authoring,
            assessment={"repair_plan_version": version}, node_id=story["nodes"][0]["id"])
