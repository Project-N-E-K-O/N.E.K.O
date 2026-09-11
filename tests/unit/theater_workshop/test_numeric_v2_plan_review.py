"""方案复核只关闭执行权限，不能重写原意见、扩充授权或覆盖旧报告。"""
from copy import deepcopy
import json
import pytest

from theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from theater_workshop.sdk.generation.plan_review import validate_plan_review
from .test_numeric_v2_generation import _quality_story_context, _generation_setup, _quality_payload, _quality_reply


def ready_payload(plans):
    # 测试显式模拟逐方案复核；生产中不能用这个默认肯定结果代替模型判断。
    return {"conflicts": [], "checks": [{"issue_id": p["issue_id"], "status": "ready", "reason": "方案字段与保留要求相容", "missing_targets": []} for p in plans]}


def fixture():
    story, authoring = _quality_story_context()
    agent = NumericV2QualityAssessor()
    context = agent._assessment_context(story, _generation_setup(), authoring)
    report = agent._validated_assessment(context, _quality_payload(weak_dimension="plot", target_node_id="mainline_01"))
    return agent, story, authoring, context, report


@pytest.mark.parametrize("case", ["missing", "duplicate", "unknown", "status", "reason", "targets", "unknown_node", "unknown_field", "already_authorized", "duplicate_target", "ready_missing"])
def test_invalid_plan_review_rejects_whole_result_without_mutation(case):
    _, _, _, context, report = fixture()
    plans = report["issues"];before = deepcopy(plans)
    payload = ready_payload(plans);row = payload["checks"][0]
    target = {"node_id": "mainline_01", "field": "/opening_scene"}
    if case == "missing": payload["checks"] = []
    elif case == "duplicate": payload["checks"] *= 2
    elif case == "unknown": row["issue_id"] = "absent"
    elif case == "status": row["status"] = "maybe"
    elif case == "reason": row["reason"] = " "
    elif case == "targets": row["missing_targets"] = None
    else:
        row.update(status="blocked", missing_targets=[target])
        if case == "unknown_node": target["node_id"] = "absent"
        elif case == "unknown_field": target["field"] = "/absent"
        elif case == "already_authorized": target["field"] = "/summary"
        elif case == "duplicate_target": row["missing_targets"] *= 2
        elif case == "ready_missing": row["status"] = "ready"
    with pytest.raises(ValueError, match="invalid_repair_plan_review"):
        validate_plan_review(context, plans, payload)
    assert plans == before


@pytest.mark.parametrize("status", ["ready", "blocked", "uncertain"])
def test_review_preserves_report_and_never_adds_missing_fields_or_reopens_blocked_plan(status):
    agent, story, authoring, context, report = fixture();before = deepcopy(report)
    def reply(messages, **options):
        assert options["operation"] == "numeric_v2_repair_plan_review" and options["max_retries"] == 1
        plans = json.loads(messages[1]["content"])["plans"]
        assert "repair_node_ids" not in plans[0] and "repairable" not in plans[0]
        payload = ready_payload(plans);payload["checks"][0]["status"] = status
        if status == "blocked": payload["checks"][0]["missing_targets"] = [{"node_id": "mainline_01", "field": "/opening_scene"}]
        return json.dumps(payload)
    agent.call_llm = reply
    reviewed = agent._review_plans(context, report)
    assert report == before
    issue = reviewed["issues"][0]
    for key in ("problem", "modification_plan", "repair_targets", "preserve"):
        assert issue[key] == before["issues"][0][key]
    assert reviewed["scores"] == before["scores"]
    assert issue["repairable"] is (status == "ready")
    assert reviewed["repair_plan_version"] == 3
    if status != "ready":
        # 重算字段权限不能把模型已拦截的方案重新打开。
        agent.call_llm = lambda *a, **kw: pytest.fail("被拦截方案不得调用修订")
        with pytest.raises(QualityAssessmentError, match="quality_node_not_repairable"):
            agent.optimize_node(story=story, setup=_generation_setup(), authoring=authoring, assessment=reviewed, node_id="mainline_01")


@pytest.mark.parametrize("has_plan", [True, False])
def test_scoring_reviews_all_eligible_plans_once_or_skips(has_plan):
    agent, story, authoring, _, _ = fixture();calls = []
    raw = _quality_payload(weak_dimension="plot" if has_plan else None, target_node_id="mainline_01")
    if has_plan:
        second = deepcopy(raw["issues"][0]);second.update(dimension="pacing", problem="独立影响")
        raw["issues"].append(second)
    original = _quality_reply(raw)
    def reply(messages, **options):
        calls.append(options["operation"])
        if options["operation"] == "numeric_v2_repair_plan_review":
            plans = json.loads(messages[1]["content"])["plans"]
            assert len(plans) == 2
            return json.dumps(ready_payload(plans))
        return original(messages, **options)
    agent.call_llm = reply
    report = agent.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert len(calls) == (3 if has_plan else 2)
    assert report["plan_review"]["status"] == ("complete" if has_plan else "not_needed")




@pytest.mark.parametrize("case", ["valid", "unknown", "self", "duplicate", "reverse_duplicate", "one_id", "three_ids", "empty_reason", "missing", "legacy"])
def test_conflict_pair_is_validated_once_and_blocks_both_plans_without_changing_local_opinions(case):
    # 模型只声明一次真实冲突，程序对称投影到双方；局部ready不覆盖组合冲突，也不丢弃原理由。
    agent, story, authoring, context, report = fixture()
    second = deepcopy(report["issues"][0]);second["issue_id"] = "quality_issue_02"
    report["issues"].append(second);plans = report["issues"];payload = ready_payload(plans)
    ids = [p["issue_id"] for p in plans]
    pair = {"issue_ids": ids[:], "reason": "第一条要求改写，第二条要求逐字保留"}
    payload["conflicts"] = [pair]
    if case == "unknown": pair["issue_ids"][0] = "absent"
    elif case == "self": pair["issue_ids"] = [ids[0], ids[0]]
    elif case == "duplicate": payload["conflicts"] *= 2
    elif case == "reverse_duplicate": payload["conflicts"].append({**pair, "issue_ids": ids[::-1]})
    elif case == "one_id": pair["issue_ids"] = ids[:1]
    elif case == "three_ids": pair["issue_ids"].append("third")
    elif case == "empty_reason": pair["reason"] = " "
    elif case == "missing": payload.pop("conflicts")
    elif case == "legacy": payload["checks"][0]["conflicting_issue_ids"] = [ids[1]]
    before = deepcopy(payload)
    if case != "valid":
        with pytest.raises(ValueError, match="invalid_repair_plan_review"):
            validate_plan_review(context, plans, payload)
        assert payload == before
        return
    result = validate_plan_review(context, plans, payload)
    assert payload == before
    for index, identity in enumerate(ids):
        row = result[identity]
        assert row["status"] == "blocked" and row["local_status"] == "ready"
        assert row["reason"] == payload["checks"][index]["reason"]
        assert row["conflicting_issue_ids"] == [ids[1-index]]
        assert row["conflict_checks"] == [pair]
    agent.call_llm = lambda *a, **kw: json.dumps(payload)
    reviewed = agent._review_plans(context, report)
    assert all(not row["repairable"] for row in reviewed["issues"])
    agent.call_llm = lambda *a, **kw: pytest.fail("冲突双方均不能触发修订模型")
    with pytest.raises(QualityAssessmentError, match="quality_node_not_repairable"):
        agent.optimize_node(story=story, setup=_generation_setup(), authoring=authoring, assessment=reviewed, node_id="mainline_01")


@pytest.mark.parametrize("field", ["/narrative_focus", "/acting_contract/dialogue_policy"])
def test_model_ready_never_grants_structural_permission(field):
    from theater_workshop.sdk.generation.repair import classify_repair
    agent, story, authoring, context, report = fixture()
    issue = report["issues"][0]
    issue["repair_targets"][0]["field"] = field
    issue["plan_review"] = ready_payload([issue])["checks"][0]
    report["repair_plan_version"] = 3
    assert classify_repair(context, issue, require_plan_review=True)["repairable"] is False
    agent.call_llm = lambda *a, **kw: pytest.fail("模型ready不能覆盖程序权限")
    with pytest.raises(QualityAssessmentError, match="quality_node_not_repairable"):
        agent.optimize_node(story=story, setup=_generation_setup(), authoring=authoring, assessment=report, node_id="mainline_01")


def test_provider_failure_has_plan_phase_and_does_not_publish_report():
    from theater_workshop.sdk.model import LLMCallFailure
    agent, _, _, context, report = fixture();before = deepcopy(report)
    agent.call_llm = lambda *a, **kw: LLMCallFailure("timeout", error_code="model_timeout", exception_type="TimeoutError")
    with pytest.raises(QualityAssessmentError) as caught:
        agent._review_plans(context, report)
    assert caught.value.code == "model_timeout" and caught.value.phase == "plan_review"
    assert report == before


def test_plan_review_separates_original_accusation_from_repair_commands_without_hiding_report():
    # 原生回复曾引用problem里的状态字段扩大修法；输入隔离只作用于方案复核，报告不删原意见。
    agent, _, _, context, report = fixture()
    issue = report["issues"][0]
    issue.update(source="facts", problem="背景提及女主状态、玩家状态和环境状态。",
                 evidence=[{"path": "/mainline/0/opening_scene", "quote": context["mainline"][0]["opening_scene"]}])
    issue["repair_targets"].append({"node_id": "mainline_01", "field": "/opening_scene"})
    issue["evidence_review"] = {"verdict": "supported", "reason": "只修摘要，开场保持原文", "target_checks": [
        {**target, "requires_change": target["field"] == "/summary", "reason": "已核对需改或保留"}
        for target in issue["repair_targets"]]}
    before = deepcopy(report);calls = []
    def reply(messages, **options):
        request = json.loads(messages[1]["content"]);calls.append(options)
        plan = request["plans"][0]
        assert "problem" not in plan and "evidence" not in plan
        for key in ("modification_plan", "repair_targets", "preserve", "evidence_review"):
            assert plan[key] == issue[key]
        assert request["story_outline"] == context
        assert request["pairs_to_compare"] == []
        assert request["protected_repair_targets"] == [{"node_id": "mainline_01", "field": "/opening_scene"}]
        return json.dumps(ready_payload(request["plans"]))
    agent.call_llm = reply
    result = agent._review_plans(context, report)
    assert len(calls) == 1 and calls[0]["max_retries"] == 1
    assert report == before
    for key in ("problem", "evidence", "modification_plan", "repair_targets", "evidence_review"):
        assert result["issues"][0][key] == before["issues"][0][key]
    assert result["scores"] == before["scores"]


def test_three_eligible_plans_have_each_distinct_pair_once_and_ignore_manual_plan():
    # 三条方案含非相邻组合；结构建议不进入本次可执行候选，也不伪造多一次模型调用。
    agent, _, _, context, report = fixture()
    for identity in ("quality_issue_02", "quality_issue_03", "manual"):
        row = deepcopy(report["issues"][0]);row["issue_id"] = identity
        if identity == "manual": row["repairable"] = False
        report["issues"].append(row)
    calls = []
    def reply(messages, **options):
        body = json.loads(messages[1]["content"]);calls.append(options)
        assert [pair["issue_ids"] for pair in body["pairs_to_compare"]] == [
            ["quality_issue_01", "quality_issue_02"], ["quality_issue_01", "quality_issue_03"],
            ["quality_issue_02", "quality_issue_03"]]
        return json.dumps(ready_payload(body["plans"]))
    agent.call_llm = reply
    reviewed = agent._review_plans(context, report)
    assert len(calls) == 1 and reviewed["plan_review"]["reviewed_count"] == 3
    assert "plan_review" not in reviewed["issues"][-1]
