"""Verify conditional review, complete reports, score deductions and preserved fields; mock models test deterministic boundaries only."""
from copy import deepcopy
import json
import pytest
from services.theater_workshop.sdk.generation.evidence import validate_evidence_review, review_targets
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from .test_numeric_v2_generation import (
    _quality_story_context, _generation_setup, _quality_payload, _quality_wire_payload,
    _plan_review_reply, _fact_review_payload, _quality_reply,
)
from services.theater_workshop.sdk.generation.facts import fact_review_tasks


def test_scoring_and_repair_share_runtime_rules_without_story_specific_examples():
    # Fact stages share task-specific execution rules; editing follows approved plans.
    from services.theater_workshop.sdk.generation.runtime_rules import SCORING_RUNTIME_RULES
    from services.theater_workshop.sdk.generation.facts import FACT_REVIEW_PROMPT, FACT_RUNTIME_RULES
    from services.theater_workshop.sdk.generation.evidence import EVIDENCE_REVIEW_PROMPT
    from services.theater_workshop.sdk.generation.plan_review import PLAN_REVIEW_PROMPT
    from services.theater_workshop.sdk.generation.quality import _ASSESSMENT_PROMPT, _NODE_OPTIMIZATION_PROMPT
    for prompt in (FACT_REVIEW_PROMPT, EVIDENCE_REVIEW_PROMPT):
        assert prompt.count(FACT_RUNTIME_RULES) == 1
        assert SCORING_RUNTIME_RULES not in prompt
        # Fact checking must not promote creative preferences into runtime prohibitions; revisions must preserve existing causality.
        assert "不因配角多少、关系升温或个人风格偏好报事实错误" in prompt
        assert "不删除因果必需的配角回应" in prompt
        for rule in ("演出角色权限不授权玩家的新台词、操作或决定", "runtime.player_input是动态来源标记",
                     "普通出幕需玩家接受已经公开的同一安排", "拒绝、追问、暂缓可以留幕，不新增分支或确认锁",
                     "自动出幕只依据原completion_contract及route conditions，不把可选goals变门槛",
                     "未受保证的来源历史不能在桥段自证", "接受后正常移动、角色自主展示与环境当时变化可以首次发生",
                     "展示不等于玩家体验、理解或接受", "本幕仅讨论的禁令不套到已获准目标幕",
                     "不能把不同入路保证取并集"):
            assert rule in prompt
    assert SCORING_RUNTIME_RULES not in _ASSESSMENT_PROMPT
    assert SCORING_RUNTIME_RULES not in _NODE_OPTIMIZATION_PROMPT
    assert SCORING_RUNTIME_RULES not in PLAN_REVIEW_PROMPT
    assert "保留玩家行动权及路线条件" in PLAN_REVIEW_PROMPT
    assert "因果必需的配角回应" in _NODE_OPTIMIZATION_PROMPT
    assert "不因配角少而扣分" in _ASSESSMENT_PROMPT
    assert "保留明确需要的配角职责" in _ASSESSMENT_PROMPT
    assert "不以逐项完成 goals 或枚举 source_ids 作为换幕条件" in SCORING_RUNTIME_RULES
    assert "暂缓或拒绝可留在原节点" in SCORING_RUNTIME_RULES
    assert "通用行动权约束不声明存在新的待办" in SCORING_RUNTIME_RULES
    assert "一人示范不代替另一人的亲手操作" in SCORING_RUNTIME_RULES
    assert "一轮结果不能在出口或结局扩大为全员完成" in SCORING_RUNTIME_RULES
    assert "不据此新增逐人逐步确认" in SCORING_RUNTIME_RULES
    assert "演出角色的演绎权限" in SCORING_RUNTIME_RULES
    assert "明确要求玩家作新的选择或等待其回答" in SCORING_RUNTIME_RULES
    assert "不能把防止提前结束写成永远不得结束" in SCORING_RUNTIME_RULES
    assert "同一物件在同一时点只能有一份相容状态" in SCORING_RUNTIME_RULES
    assert "本幕准备分工与另一时段的见面应分开邀约" in SCORING_RUNTIME_RULES
    assert "world.rules 与 intervention_capacity 是未直接导出的策划字段" in SCORING_RUNTIME_RULES
    assert all(name not in SCORING_RUNTIME_RULES for name in ("种子", "天文馆", "圆扣", "明信片"))


def test_exit_execution_rule_is_shared_by_generation_review_and_revision():
    from services.theater_workshop.sdk.generation.runtime_rules import COMPLETION_RESULT_RULE, EXIT_EXECUTION_RULE
    from services.theater_workshop.sdk.generation.staged import _SCENE_PROMPT
    from services.theater_workshop.sdk.generation.numeric_v2 import (
        _MAINLINE_PROMPT, _MAINLINE_CONTINUATION_PROMPT, _NODE_ENHANCEMENT_PROMPT,
        _BRANCH_ENDING_PROMPT, _BRANCH_PATH_PROMPT,
    )
    from services.theater_workshop.sdk.generation.facts import FACT_REVIEW_PROMPT
    from services.theater_workshop.sdk.generation.evidence import EVIDENCE_REVIEW_PROMPT
    from services.theater_workshop.sdk.generation.plan_review import PLAN_REVIEW_PROMPT
    from services.theater_workshop.sdk.generation.quality import _ASSESSMENT_PROMPT, _NODE_OPTIMIZATION_PROMPT

    for prompt in (
        _MAINLINE_PROMPT, _MAINLINE_CONTINUATION_PROMPT,
    ):
        assert prompt.count(EXIT_EXECUTION_RULE) == 1
    for prompt in (FACT_REVIEW_PROMPT, EVIDENCE_REVIEW_PROMPT):
        assert "普通出幕需玩家接受已经公开的同一安排" in prompt
        assert "自动出幕只依据原completion_contract及route conditions" in prompt
        assert "接受后正常移动、角色自主展示与环境当时变化可以首次发生" in prompt
    assert "接受前的 fallback_offer/silent_choice" in FACT_REVIEW_PROMPT
    assert "accept_input 与接受后的桥段和目标开场" in FACT_REVIEW_PROMPT
    assert "fallback_offer/silent_choice在接受前展示，accept_input是待玩家选择" in EVIDENCE_REVIEW_PROMPT
    for prompt in (_NODE_ENHANCEMENT_PROMPT, _BRANCH_ENDING_PROMPT, _BRANCH_PATH_PROMPT):
        assert EXIT_EXECUTION_RULE not in prompt
    for prompt in (_MAINLINE_PROMPT, _MAINLINE_CONTINUATION_PROMPT, _SCENE_PROMPT, _BRANCH_PATH_PROMPT):
        assert prompt.count(COMPLETION_RESULT_RULE) == 1
        assert prompt.count("只用 fixed_narration_id 引用该段") == 1
        assert "必要的玩家选择、许可或实际交接仍分别按各自结果登记" in prompt
    for prompt in (_SCENE_PROMPT, _BRANCH_PATH_PROMPT, _NODE_ENHANCEMENT_PROMPT, _NODE_OPTIMIZATION_PROMPT):
        assert "可变的内容、数量、归属" in prompt
    assert "新安排须获玩家授权" in _BRANCH_PATH_PROMPT
    assert "主动要求同一公开安排无需再确认，拒绝或暂缓可留幕" in _BRANCH_PATH_PROMPT
    assert "entry_prerequisite_fact_ids" in _BRANCH_PATH_PROMPT
    assert "结局入幕后不接收玩家输入" in _BRANCH_ENDING_PROMPT
    assert "outgoing_routes.conditions 是对应出口的硬条件" in _NODE_ENHANCEMENT_PROMPT
    assert EXIT_EXECUTION_RULE not in _ASSESSMENT_PROMPT
    assert EXIT_EXECUTION_RULE not in PLAN_REVIEW_PROMPT
    assert EXIT_EXECUTION_RULE not in _NODE_OPTIMIZATION_PROMPT
    assert "邀约在接受前，桥段在接受后" in _NODE_OPTIMIZATION_PROMPT
    assert "按 completion_contract 与路线 conditions" in _NODE_OPTIMIZATION_PROMPT
    assert "普通换幕须有玩家授权" in _ASSESSMENT_PROMPT
    # 不能再用“出口都在接受后”把尚待选择的按钮或邀约读成已完成的桥段。
    assert "outgoing_routes是来源核心互动成立、玩家接受换场后的桥段" not in EVIDENCE_REVIEW_PROMPT
    assert "它属于当前节点互动完成后的出幕时点，must_preserve 可保留本幕目标已交付的结果" not in FACT_REVIEW_PROMPT


def review_payload(issues, verdict="supported", keep=()):
    # The test model explicitly answers field by field to simulate review, rather than approving the original report by default in production.
    return {"checks": [{"issue_id": i["issue_id"], "verdict": verdict, "reason": "原文核对结论",
        "target_checks": [{**t, "requires_change": verdict == "supported" and t["field"] not in keep,
                           "preserve_original": verdict == "supported" and t["field"] in keep,
                           "reason": "此处保留或改动的依据"} for t in review_targets(i)]} for i in issues]}


def wire_fact(request):
    outline = request["story_outline"]["mainline"][0]
    return _fact_review_payload(request, [{
        "category": "state", "severity": "major", "target_node_ids": [outline["id"]],
        "problem": "原报告：开场与状态矛盾", "modification_plan": "原方案：同时修改状态和开场",
        "expected_result": "两处相容", "preserve": [], "repair_scope": "text",
        "repair_targets": [{"node_id": outline["id"], "field": f} for f in ("/character_state/catgirl", "/opening_scene")],
        "evidence_refs": [outline["opening_scene"]["ref"], outline["character_state"]["catgirl"]["ref"]]}])


def test_review_keeps_entry_narration_state_timing_in_both_prompts():
    from services.theater_workshop.sdk.generation.evidence import EVIDENCE_REVIEW_PROMPT
    from services.theater_workshop.sdk.generation.facts import FACT_REVIEW_PROMPT, OPENING_STATE_COMPARISON_RULE
    from services.theater_workshop.sdk.generation.runtime_rules import ENTRY_STATE_TIMING_RULE

    assert ENTRY_STATE_TIMING_RULE not in OPENING_STATE_COMPARISON_RULE
    assert FACT_REVIEW_PROMPT.count(ENTRY_STATE_TIMING_RULE) == 1
    assert EVIDENCE_REVIEW_PROMPT.count(ENTRY_STATE_TIMING_RULE) == 1
    assert "opening_scene与character_state同属开场演完时点" not in EVIDENCE_REVIEW_PROMPT
    assert "两者都在开场演完" not in FACT_REVIEW_PROMPT


@pytest.mark.parametrize("last_batch_invalid", [False, True])
def test_neighbor_only_issue_is_not_repeated_and_evidence_review_stays_local(last_batch_invalid):
    story, authoring = _quality_story_context()
    first, neighbor, two_hops, last = authoring["mainline_node_ids"]
    batches = []
    literary = []

    def model(messages, **options):
        operation = options["operation"]
        request = json.loads(messages[1]["content"])
        if operation == "numeric_v2_fact_review":
            focus, = request["checked_node_ids"]
            task = request["audit_task"]
            if focus not in (first, last):
                return json.dumps(_fact_review_payload(request))
            owner = focus
            node = next(node for node in request["story_outline"]["mainline"] if node["id"] == owner)
            report = wire_fact({**request, "story_outline": {"mainline": [node]}})
            if owner == first:
                neighbor_node = (next(row["entry"] for row in request["story_outline"]["route_entries"]
                                      if row["entry"]["id"] == neighbor) if task["kind"] == "node" else
                                 next(row for row in request["story_outline"]["mainline"] if row["id"] == neighbor))
                report["issues"][0]["evidence_refs"].append(neighbor_node["opening_scene"]["ref"])
            return json.dumps(report)
        if operation.startswith("numeric_v2_fact_evidence_review"):
            issue, = request["proposed_issues"]
            owner, = issue["target_node_ids"]
            batches.append(owner)
            assert next(node for node in request["story_outline"]["mainline"]
                        if node["id"] == (last if owner == first else first))["text_omitted"]
            if owner == first:
                assert request["story_outline"]["mainline"][2]["id"] == two_hops
                assert request["story_outline"]["mainline"][2]["text_omitted"]
                assert "opening_scene" in request["story_outline"]["mainline"][1]
                assert {"path": "/mainline/1/opening_scene"} in issue["evidence"]
            if owner == last and last_batch_invalid:
                return "{}"
            return json.dumps(review_payload([issue]))
        if operation == "numeric_v2_repair_plan_review":
            return _plan_review_reply(messages)
        literary.append(operation)
        return _quality_reply(_quality_payload())(messages, **options)

    assessor = NumericV2QualityAssessor(model)
    if last_batch_invalid:
        with pytest.raises(QualityAssessmentError) as caught:
            assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
        assert caught.value.phase == "evidence"
        assert batches == [first, last, last]
        assert literary == []
    else:
        report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
        assert batches == [first, last]
        assert [issue["target_node_ids"] for issue in report["issues"]] == [[first], [last]]


@pytest.mark.parametrize("case", ["missing", "duplicate", "unknown", "field_missing", "field_duplicate", "field_new", "truthy", "unknown_verdict", "contradictory", "empty_reason", "preserve_missing", "preserve_truthy", "preserve_conflict"])
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
    elif case == "preserve_missing":
        check["target_checks"][0]["requires_change"] = False
        del check["target_checks"][0]["preserve_original"]
    elif case == "preserve_truthy": check["target_checks"][0]["preserve_original"] = "true"
    elif case == "preserve_conflict": check["target_checks"][0]["preserve_original"] = True
    with pytest.raises(ValueError, match="invalid_fact_evidence_review"):
        validate_evidence_review(issues, payload)
    assert issues == before


@pytest.mark.parametrize("verdict", ["supported", "unsupported", "uncertain", "no_issues"])
@pytest.mark.parametrize("format_repair", [False, True])
def test_conditional_review_keeps_originals_and_controls_score_and_repair(verdict, format_repair):
    from utils.tokenize import count_tokens
    from services.theater_workshop.sdk.generation.runtime_rules import ENTRY_STATE_TIMING_RULE

    story, authoring = _quality_story_context();before = deepcopy(story)
    assessor = NumericV2QualityAssessor();calls = []
    def reply(messages, **kwargs):
        request = json.loads(messages[1]["content"]);calls.append(kwargs["operation"])
        assert kwargs["max_retries"] == 1
        if kwargs["operation"].startswith(("numeric_v2_fact_review", "numeric_v2_fact_evidence_review")):
            assert messages[0]["content"].count(ENTRY_STATE_TIMING_RULE) == 1
        if kwargs["operation"] == "numeric_v2_fact_review":
            return json.dumps(_fact_review_payload(request) if verdict == "no_issues"
                              or request["audit_task"]["kind"] != "node"
                              or request["checked_node_ids"] != ["mainline_01"] else wire_fact(request), ensure_ascii=False)
        if kwargs["operation"] == "numeric_v2_fact_evidence_review" and format_repair:
            return "{" + "未闭合的长段答复。" * 700
        if kwargs["operation"] in {"numeric_v2_fact_evidence_review", "numeric_v2_fact_evidence_review_format_repair"}:
            if kwargs["operation"].endswith("_format_repair"):
                assert count_tokens(messages[-2]["content"]) <= 512
                assert "省略中段" in messages[-2]["content"]
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
        return _quality_reply(_quality_payload())(messages, **kwargs)
    assessor.call_llm = reply
    report = assessor.assess(story=story, authoring=authoring, setup=_generation_setup())
    task_count = len(fact_review_tasks(assessor._assessment_context(story, _generation_setup(), authoring)))
    block_count = len(report["literary_coverage"]["blocks"])
    assert len(calls) == task_count + block_count + (3 if verdict == "supported" else 1 if verdict == "no_issues" else 2) + int(format_repair and verdict != "no_issues")
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


@pytest.mark.parametrize("preserve_original", [False, True])
def test_supported_fact_preservation_distinguishes_local_no_change(preserve_original):
    story, authoring = _quality_story_context()
    node = story["nodes"][0]
    node_id = node["id"]
    opening = "林舟说：我决定把旧信交给你。女主把铜钥匙放在桌上。"
    node["story_beat"]["opening_scene"] = opening
    node["story_beat"]["character_state"]["catgirl_state"] = "女主仍把铜钥匙放在口袋。"
    story["intro"]["player_name"] = "林舟"
    before = deepcopy(story)
    reviewed = []

    def reply(messages, **options):
        request = json.loads(messages[1]["content"])
        operation = options["operation"]
        if operation == "numeric_v2_fact_review":
            checked = request["checked_node_ids"]
            if request["audit_task"]["kind"] != "node" or checked != [node_id]:
                return json.dumps(_fact_review_payload(request))
            fact = wire_fact(request)
            state_issue, = fact["issues"]
            outline = request["story_outline"]
            fact["issues"].append({
                **state_issue, "category": "ownership", "problem": "开场代替林舟决定交信。",
                "modification_plan": "去除替玩家发言，保留女主的钥匙动作。",
                "repair_targets": [{"node_id": node_id, "field": "/opening_scene"}],
                "evidence_refs": [outline["mainline"][0]["opening_scene"]["ref"], outline["characters"]["player_name"]["ref"]],
            })
            return json.dumps(fact)
        if operation == "numeric_v2_fact_evidence_review":
            result = review_payload(request["proposed_issues"])
            for issue, check in zip(request["proposed_issues"], result["checks"], strict=True):
                if any(target["field"] == "/character_state/catgirl" for target in issue["repair_targets"]):
                    for field in check["target_checks"]:
                        if field["field"] == "/opening_scene":
                            field.update(requires_change=False, preserve_original=preserve_original)
            return json.dumps(result)
        if operation == "numeric_v2_repair_plan_review":
            reviewed.extend(plan["issue_id"] for plan in request["plans"])
            return _plan_review_reply(messages)
        if operation == "numeric_v2_quality_single_node_optimization":
            patch = {"character_state": {"catgirl_state": "铜钥匙放在桌上。"}}
            if not preserve_original:
                patch["opening_scene"] = "女主把铜钥匙放在桌上。"
            return json.dumps({"node_updates": [{"node_id": node_id, "story_beat": patch}]})
        return _quality_reply(_quality_payload())(messages, **options)

    assessor = NumericV2QualityAssessor(reply)
    report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    state, ownership = report["issues"]
    assert state["repairable"] is True
    assert ownership["repairable"] is not preserve_original
    assert reviewed == ([state["issue_id"]] if preserve_original else [state["issue_id"], ownership["issue_id"]])
    repaired = assessor.optimize_node(story=story, setup=_generation_setup(), authoring=authoring,
                                      assessment=report, node_id=node_id)
    assert (repaired["nodes"][0]["story_beat"]["opening_scene"] == opening) is preserve_original
    assert story == before


def test_rejected_fact_id_cannot_supply_literary_deduction():
    # Unsupported issues must not cause renewed deductions through literary associations with fact IDs.
    story, authoring = _quality_story_context();assessor = NumericV2QualityAssessor()
    context = assessor._assessment_context(story, _generation_setup(), authoring)
    raw = _quality_payload();raw["scores"]["plot"].update(score=60, fact_issue_ids=["rejected"])
    assessor.call_llm = _quality_reply(raw)
    with pytest.raises(QualityAssessmentError):
        assessor._assess_literature(context, {"issues": [], "excluded_issue_ids": ["rejected"]})


@pytest.mark.parametrize("verdict", ["unsupported", "uncertain"])
def test_unconfirmed_fact_issue_cannot_lock_an_independently_supported_repair(verdict):
    story, authoring = _quality_story_context()
    first = authoring["mainline_node_ids"][0]
    target = {"node_id": first, "field": "/opening_scene"}
    operations = []

    def model(messages, **options):
        operation = options["operation"]
        operations.append(operation)
        request = json.loads(messages[1]["content"])
        if operation == "numeric_v2_fact_review":
            checked = request["checked_node_ids"]
            if request["audit_task"]["kind"] != "node" or checked != [first]:
                return json.dumps(_fact_review_payload(request))
            report = wire_fact(request)
            confirmed = report["issues"][0]
            confirmed["repair_targets"] = [target]
            report["issues"].insert(0, {**confirmed, "category": "ownership", "problem": "另一条未经证实的报告"})
            return json.dumps(report)
        if operation == "numeric_v2_fact_evidence_review":
            return json.dumps({"checks": [check for issue in request["proposed_issues"]
                for check in review_payload([issue], verdict if issue["problem"] == "另一条未经证实的报告" else "supported")["checks"]]})
        if operation == "numeric_v2_repair_plan_review":
            assert request["protected_repair_targets"] == []
            assert [plan["issue_id"] for plan in request["plans"]] == ["fact_issue_02"]
            return _plan_review_reply(messages)
        assert request["fact_review"]["protected_repair_targets"] == []
        return _quality_reply(_quality_payload())(messages, **options)

    report = NumericV2QualityAssessor(model).assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert [issue["repairable"] for issue in report["issues"]] == [False, True]
    assert operations[-1] == "numeric_v2_repair_plan_review"




def test_protected_field_blocks_literary_parent_and_unnecessary_node():
    from services.theater_workshop.sdk.generation.repair import classify_repair
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
