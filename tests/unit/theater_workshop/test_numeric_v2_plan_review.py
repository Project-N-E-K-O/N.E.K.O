"""Plan review may only disable execution, not rewrite original feedback, expand authorization or overwrite the previous report."""

from tests.utils.theater_prompt import prompt_payload
from copy import deepcopy
import json
import pytest

from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from services.theater_workshop.sdk.generation.plan_review import validate_plan_review
from .test_numeric_v2_generation import _quality_story_context, _generation_setup, _quality_payload, _quality_reply


def ready_payload(plans):
    # Tests explicitly simulate per-plan model review; production must not substitute this default approval for model judgment.
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
        plans = prompt_payload(messages[1]["content"])["plans"]
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
        # Recomputing field permissions must not reopen plans already blocked by the model.
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
            plans = prompt_payload(messages[1]["content"])["plans"]
            assert len(plans) == 2
            return json.dumps(ready_payload(plans))
        return original(messages, **options)
    agent.call_llm = reply
    report = agent.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert calls.count("numeric_v2_repair_plan_review") == int(has_plan)
    assert calls.count("numeric_v2_fact_review") == len(story["nodes"]) + sum(
        len(node.get("route_gates", [])) for node in story["nodes"])
    assert calls.count("numeric_v2_literary_block") == len({
        (node["id"], route["target_node_id"]) for node in story["nodes"] for route in node.get("route_gates", [])})
    assert report["plan_review"]["status"] == ("complete" if has_plan else "not_needed")




@pytest.mark.parametrize("case", ["valid", "unknown", "self", "duplicate", "reverse_duplicate", "one_id", "three_ids", "empty_reason", "missing", "legacy"])
def test_conflict_pair_is_validated_once_and_blocks_both_plans_without_changing_local_opinions(case):
    # The model declares a real conflict once; the program projects it symmetrically onto both plans. Local readiness neither overrides combined conflicts nor loses original reasons.
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
    from services.theater_workshop.sdk.generation.repair import classify_repair
    agent, story, authoring, context, report = fixture()
    issue = report["issues"][0]
    issue["repair_targets"][0]["field"] = field
    issue["plan_review"] = {**ready_payload([issue])["checks"][0], "conflicting_issue_ids": []}
    report["repair_plan_version"] = 3
    classified = classify_repair(context, issue, require_plan_review=True)
    assert classified["repairable"] is False
    assert classified["repair_reason"] == "方案涉及当前节点优化不支持的字段，请手动调整。"
    agent.call_llm = lambda *a, **kw: pytest.fail("模型ready不能覆盖程序权限")
    with pytest.raises(QualityAssessmentError, match="quality_node_not_repairable"):
        agent.optimize_node(story=story, setup=_generation_setup(), authoring=authoring, assessment=report, node_id="mainline_01")


def test_provider_failure_has_plan_phase_and_does_not_publish_report():
    from services.theater_workshop.sdk.model import LLMCallFailure
    agent, _, _, context, report = fixture();before = deepcopy(report)
    agent.call_llm = lambda *a, **kw: LLMCallFailure("timeout", error_code="model_timeout", exception_type="TimeoutError")
    with pytest.raises(QualityAssessmentError) as caught:
        agent._review_plans(context, report)
    assert caught.value.code == "model_timeout" and caught.value.phase == "plan_review"
    assert report == before


def test_plan_review_separates_original_accusation_from_repair_commands_without_hiding_report():
    # Native replies once cited state fields from problem to broaden repairs; input isolation affects only plan review, while reports retain original opinions.
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
        request = prompt_payload(messages[1]["content"]);calls.append(options)
        plan = request["plans"][0]
        assert "problem" not in plan and "evidence" not in plan
        for key in ("modification_plan", "repair_targets", "preserve", "evidence_review"):
            assert plan[key] == issue[key]
        outline = request["story_outline"]
        visible, original = outline["mainline"][0], context["mainline"][0]
        for field in ("summary", "opening_scene", "text_repair_fields"):
            assert visible[field] == original[field]
        assert "goals" not in visible and "acting_contract" not in visible
        assert [node["id"] for node in outline["mainline"]] == [node["id"] for node in context["mainline"]]
        assert outline["mainline"][1]["text_omitted"] is True
        assert outline["mainline"][2]["text_omitted"] is True
        assert "opening_scene" not in outline["mainline"][2]
        assert "pairs_to_compare" not in request
        assert request["protected_repair_targets"] == [{"node_id": "mainline_01", "field": "/opening_scene"}]
        return json.dumps(ready_payload(request["plans"]))
    agent.call_llm = reply
    result = agent._review_plans(context, report)
    assert len(calls) == 1 and calls[0]["max_retries"] == 1
    assert report == before
    for key in ("problem", "evidence", "modification_plan", "repair_targets", "evidence_review"):
        assert result["issues"][0][key] == before["issues"][0][key]
    assert result["scores"] == before["scores"]


def test_plan_review_sends_each_eligible_plan_once_and_preserves_nonadjacent_conflicts():
    # All distinct IDs can be compared without sending an input row for every pair.
    agent, _, _, context, report = fixture()
    for identity in ("quality_issue_02", "quality_issue_03", "manual"):
        row = deepcopy(report["issues"][0]);row["issue_id"] = identity
        if identity == "manual": row["repairable"] = False
        report["issues"].append(row)
    calls = []
    def reply(messages, **options):
        body = prompt_payload(messages[1]["content"]);calls.append(options)
        assert "pairs_to_compare" not in body
        assert [plan["issue_id"] for plan in body["plans"]] == [
            "quality_issue_01", "quality_issue_02", "quality_issue_03",
        ]
        payload = ready_payload(body["plans"])
        payload["conflicts"] = [{"issue_ids": ["quality_issue_01", "quality_issue_03"],
                                 "reason": "两项要求同一个开场有不同结果。"}]
        return json.dumps(payload)
    agent.call_llm = reply
    reviewed = agent._review_plans(context, report)
    assert len(calls) == 1 and reviewed["plan_review"]["reviewed_count"] == 3
    assert [issue["repairable"] for issue in reviewed["issues"]] == [False, True, False, False]
    assert "plan_review" not in reviewed["issues"][-1]


@pytest.mark.parametrize("reverse", [False, True])
def test_plan_review_can_locate_a_named_but_unauthorized_route(reverse):
    from services.theater.numeric_v2 import NumericV2Compiler

    story, authoring = _quality_story_context()
    node = story["nodes"][0]
    first = node["route_gates"][0]
    second = {**deepcopy(first), "id": "diagnostic_exit", "priority": first["priority"] - 1}
    metric = next(iter(story["metric_schema"]))
    for route, op in ((first, "<"), (second, ">=")):
        route["conditions"] = {"all": [{"type": "metric_compare", "metric": metric, "op": op, "value": 50}]}
    node["route_gates"] = [second, first] if reverse else [first, second]
    NumericV2Compiler().compile_v2_2(story)
    payload = _quality_payload(weak_dimension="plot", target_node_id=node["id"])
    payload["issues"][0]["modification_plan"] = "改摘要，并改 diagnostic_exit 的转场原因。"
    fallback = _quality_reply(payload)

    def model(messages, **options):
        if options["operation"] != "numeric_v2_repair_plan_review":
            return fallback(messages, **options)
        request = prompt_payload(messages[1]["content"])
        scoped = request["story_outline"]["mainline"][0]
        assert scoped["outgoing_routes"] == [{key: route[key] for key in ("id", "target_node_id")}
                                             for route in node["route_gates"]]
        index = next(index for index, route in enumerate(scoped["outgoing_routes"]) if route["id"] == "diagnostic_exit")
        response = ready_payload(request["plans"])
        response["checks"][0].update(status="blocked", missing_targets=[{
            "node_id": node["id"], "field": f"/outgoing_routes/{index}/transition_contract/reason",
        }])
        return json.dumps(response)

    report = NumericV2QualityAssessor(model).assess(story=story, setup=_generation_setup(), authoring=authoring)
    issue, = report["issues"]
    index = 0 if reverse else 1
    assert issue["plan_review"]["missing_targets"] == [{"node_id": node["id"], "field": f"/outgoing_routes/{index}/transition_contract/reason"}]
    assert issue["repairable"] is False


def _neon_plan_fixture():
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "assets/theater/story_neon_rebuild_ea2e45dab7.json"
    story = json.loads(path.read_text(encoding="utf-8"))
    authoring = {"mainline_node_ids": [f"mainline_{index:02d}" for index in range(1, 8)]}
    return story, authoring


@pytest.mark.parametrize("count", [1, 2, 13])
def test_neon_summary_plans_share_one_request_below_host_cap(monkeypatch, count):
    from types import SimpleNamespace
    from services.theater_workshop.host import NekoWorkshopModel
    from utils import llm_client
    from utils.tokenize import count_tokens

    story, authoring = _neon_plan_fixture()
    ids = [node["id"] for node in story["nodes"]][:count]
    payload = _quality_payload(weak_dimension="plot", target_node_id=ids[0])
    original = payload["issues"][0]
    payload["issues"] = [{**original, "target_node_ids": [node_id],
                          "repair_targets": [{"node_id": node_id, "field": "/summary"}]}
                         for node_id in ids]
    fallback = _quality_reply(payload)
    sent = []

    class Client:
        def invoke(self, messages, **options):
            sent.append(messages)
            body = prompt_payload(messages[1]["content"])
            assert [plan["target_node_ids"][0] for plan in body["plans"]] == ids
            outline = body["story_outline"]
            selected = [row for row in [*outline["mainline"], *outline["endings"],
                        *(row for branch in outline["branches"] for row in branch["nodes"])]
                        if not row.get("text_omitted") and not row.get("shared_node_ref")]
            assert {row["id"] for row in selected} == set(ids)
            assert all("summary" in row and "goals" not in row and "acting_contract" not in row for row in selected)
            response = ready_payload(body["plans"])
            if count == 2:
                response["conflicts"] = [{"issue_ids": [row["issue_id"] for row in body["plans"]],
                                          "reason": "两条摘要给同一原文设定了不同归属。"}]
            return SimpleNamespace(content=json.dumps(response, ensure_ascii=False), response_metadata={})

        def close(self):
            pass

    monkeypatch.setattr(llm_client, "create_chat_llm", lambda **kwargs: Client())
    host = NekoWorkshopModel({"model": "fixture", "base_url": "https://example.invalid/v1", "max_input_tokens": 10_000})

    def reply(messages, **options):
        if options["operation"] == "numeric_v2_repair_plan_review":
            return host(messages, **options)
        return fallback(messages, **options)

    assessor = NumericV2QualityAssessor(reply)
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    # Isolate plan packing from this historical asset's unresolved source references.
    assessment = assessor._validated_assessment(context, payload)
    report = assessor._review_plans(context, assessment)
    assert len(sent) == 1 and count_tokens(json.dumps(sent[0], ensure_ascii=False)) <= 10_000
    assert all(row["repairable"] is (count != 2) for row in report["issues"])


def test_plan_review_does_not_trim_protected_originals_to_fit_budget(monkeypatch):
    from services.theater.numeric_v2 import NumericV2Compiler
    from services.theater_workshop.host import NekoWorkshopModel
    from services.theater_workshop.sdk import WorkshopError
    from utils import llm_client
    from utils.tokenize import count_tokens

    story, authoring = _neon_plan_fixture()
    agent = NumericV2QualityAssessor()
    checks = []
    texts = []
    for index, node in enumerate(story["nodes"][:7]):
        text = f"档案{index}。" + "监测终端上保留一行可读序列号，连续显示公开的供电与连接状态。" * 60
        texts.append(text)
        pieces = node["story_beat"].setdefault("fixed_narrations", [])
        pieces.append({"id": "protected_archive", "trigger": {"type": "entry"}, "after": [],
                       "required_before_exit": False, "text": text})
        checks.append({"node_id": node["id"], "field": f"/fixed_narrations/{len(pieces) - 1}/text",
                       "requires_change": False, "preserve_original": True})
    assert sum(map(count_tokens, texts)) > 10_000
    NumericV2Compiler().compile_v2_2(story)
    context = agent._assessment_context(story, _generation_setup(), authoring)
    report = agent._validated_assessment(context, _quality_payload(weak_dimension="plot", target_node_id="mainline_01"))
    report["issues"].append({"source": "facts", "repairable": False,
                             "evidence_review": {"verdict": "supported", "target_checks": checks}})
    before = deepcopy(report)
    monkeypatch.setattr(llm_client, "create_chat_llm", lambda **kwargs: pytest.fail("over-cap request reached client"))
    host = NekoWorkshopModel({"model": "fixture", "base_url": "https://example.invalid/v1", "max_input_tokens": 10_000})

    def reply(messages, **options):
        assert all(text in messages[1]["content"] for text in texts)
        return host(messages, **options)

    agent.call_llm = reply
    with pytest.raises(WorkshopError, match="workshop_model_input_budget_exceeded"):
        agent._review_plans(context, report)
    assert report == before
