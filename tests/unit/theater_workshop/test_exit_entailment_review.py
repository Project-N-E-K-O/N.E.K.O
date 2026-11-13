"""Native gate counterexamples and review wiring, not model semantic accuracy.

Real-model positive/negative trials establish whether the prompts detect these
gaps; the review doubles below only exercise existing evidence and permissions.
"""

from tests.utils.theater_prompt import prompt_payload
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from .test_numeric_v2_evidence import review_payload
from .test_numeric_v2_generation import (
    _fact_review_payload, _generation_setup, _quality_payload, _quality_reply, _quality_story_context,
)


def _case(kind):
    story, authoring = _quality_story_context()
    source, target = story["nodes"][:2]
    key = source["completion_contract"]["all"][0]["key"]
    missing = f"scene:{source['id']}:independent_result"
    descriptions = {
        "agreement": ("双方同意先试听，再讨论记录分类。", "玩家已亲口讲述自己记得的录音缘由。"),
        "delivery": ("玩家已经交还借用的门卡。", "双方选定的物品处置已经实际执行完毕。"),
    }
    first, second = descriptions[kind]
    story["fact_contract"]["facts"][key] = {"value_type": "bool", "visibility": "public", "description": first}
    story["fact_contract"]["facts"][missing] = {"value_type": "bool", "visibility": "public", "description": second}
    source["completion_contract"] = {"all": [{"key": key, "equals": True}]}
    source["story_beat"]["summary"] = first + "之后计划交付另一结果：" + second
    route = source["route_gates"][0]
    route["conditions"] = {"all": []}
    contract = route["transition_contract"]
    contract.update(advance_on_completion=True, must_deliver=[],
                    must_preserve=[second], trigger_fact_ids=[key.rsplit(":", 1)[-1]])
    # Compiled automatic routes omit invitation fields instead of storing blanks.
    for field in ("fallback_offer", "accept_input", "silent_choice", "accept_input_confirmation_only"):
        contract.pop(field, None)
    target["story_beat"]["opening_scene"] = "女主望着窗外，没有要求玩家作新的选择。"
    target["story_beat"]["character_state"]["continuity_from_previous"] = [second]
    return story, authoring, key, missing


def _advance(story, keys):
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="entailment", catgirl_binding={
        "character_id": "character_11111111111111111111111111111111",
        "catgirl_id": "catgirl:character_11111111111111111111111111111111",
        "catgirl_name": "女主", "player_address": "玩家",
        "profile_revision": "characters:test", "profile_hash": "sha256:test",
    }, opening_performance={"performance": "女主点头回应。", "suggested_inputs": []})
    session = replace(session, node_turn_count=engine.nodes[session.current_node_id]["min_turns"])
    # Fact acceptance is an explicit test premise, not a semantic evaluator verdict.
    operations = tuple({"op": "set", "key": key, "value": True, "visibility": "public"} for key in keys)
    outcome = engine.resolve_turn(session, TurnRequestV2("first", 0, "先完成这一步，另一件还没有做。"), (),
                                  fact_operations=operations)
    return engine, outcome


@pytest.mark.parametrize("kind", ["agreement", "delivery"])
def test_planned_independent_result_is_not_a_native_automatic_departure_gate(kind):
    story, _, key, missing = _case(kind)
    before = deepcopy(story)
    engine, outcome = _advance(story, [key])
    assert outcome.ledger_event["from_node_id"] == story["nodes"][0]["id"]
    assert outcome.ledger_event["to_node_id"] == story["nodes"][1]["id"]
    assert outcome.session.current_node_id == story["nodes"][1]["id"]
    facts = outcome.session.story_state["facts"]
    assert facts[key]["value"] is True and missing not in facts
    source_session = replace(outcome.session, current_node_id=story["start_node_id"])
    assert engine.completion_contract_satisfied(source_session) is True
    assert engine.preview_route(story["start_node_id"], outcome.session.metrics, facts=facts) is not None
    assert story == before


@pytest.mark.parametrize("kind", ["agreement", "delivery"])
def test_only_the_necessary_declared_result_can_be_added_to_the_gate(kind):
    story, _, key, missing = _case(kind)
    source = story["nodes"][0]
    source["completion_contract"]["all"].append({"key": missing, "equals": True})
    source["route_gates"][0]["transition_contract"]["trigger_fact_ids"].append("independent_result")
    _, pending = _advance(story, [key])
    assert pending.ledger_event["from_node_id"] == pending.ledger_event["to_node_id"]
    assert pending.session.current_node_id == source["id"]
    _, completed = _advance(story, [key, missing])
    assert completed.session.current_node_id == story["nodes"][1]["id"]


def test_removing_an_unpromised_history_allows_a_new_autonomous_opening_event():
    story, _, key, missing = _case("agreement")
    story["nodes"][0]["route_gates"][0]["transition_contract"]["must_preserve"] = []
    target = story["nodes"][1]["story_beat"]
    target["character_state"]["continuity_from_previous"] = []
    target["opening_scene"] = "女主按下播放器，潮声开始响起；玩家尚未讲述自己的记忆。"
    _, outcome = _advance(story, [key])
    assert outcome.session.current_node_id == story["nodes"][1]["id"]
    assert missing not in outcome.session.story_state["facts"]
    assert story["nodes"][0]["completion_contract"]["all"] == [{"key": key, "equals": True}]


def _binding_reply(request, fact_key):
    # The fixture supplies one known semantic binding; this is not a recognizer.
    definition = next(row for row in request["fact_definitions"] if row["key"] == fact_key)
    return json.dumps({"bindings": [{
        "route_id": route["route_id"], "claim_ref": claim["ref"],
        "kind": "source_history" if claim["text"] == definition["description"]["text"] else "other",
        "fact_key": fact_key if claim["text"] == definition["description"]["text"] else None,
        "value": True if claim["text"] == definition["description"]["text"] else None,
    } for route in request["routes"] for claim in route["claims"]]}, ensure_ascii=False)


def _history_markers(value):
    found = set()

    def collect(item):
        if set(item) == {"exit_history_reviewed_ref"}:
            found.add(item["exit_history_reviewed_ref"])
        return item

    json.loads(json.dumps(value), object_hook=collect)
    return found


def _exit_review_reply(request, issues=(), status=None):
    # Explicit coverage verdicts are mechanical doubles, never semantic evidence.
    checks = []
    for candidate in request["exit_candidates"]:
        result = status or ("gap" if issues else "clear")
        route = next(row for row in request["exit_context"]["routes"] if row["route_id"] == candidate["route_id"])
        refs = (issues[0]["evidence_refs"] if result == "gap" else
                [route["source_context"]["opening_scene"]["ref"]] if result == "clear" else [])
        checks.append({**{key: candidate[key] for key in ("route_id", "fact_key", "value")},
            "status": result, "reason": "机械测试桩只验证覆盖和后续接线，不证明该原文的语义结论。",
            "evidence_refs": refs, "clear_basis": "source_opening" if result == "clear" else None})
    return json.dumps({"checked_node_ids": request["checked_node_ids"], "issues": list(issues),
                       "candidate_checks": checks}, ensure_ascii=False)


@pytest.mark.parametrize("guaranteed", [False, True])
def test_only_unproven_exit_bindings_receive_special_review_without_automatic_judgment(guaranteed):
    story, authoring, _, missing = _case("agreement")
    source = story["nodes"][0]
    if guaranteed:
        source["route_gates"][0]["conditions"] = {"all": [
            {"type": "fact_equals", "key": missing, "value": True}]}
    before = deepcopy((story, authoring))
    fallback = _quality_reply(_quality_payload())
    seen, reviewed_refs = [], set()

    def reply(messages, **options):
        request = prompt_payload(messages[1]["content"])
        if options["operation"] == "numeric_v2_exit_fact_binding":
            response = _binding_reply(request, missing)
            reviewed_refs.update(row["claim_ref"] for row in json.loads(response)["bindings"]
                                 if row["kind"] == "source_history")
            return response
        if options["operation"] == "numeric_v2_exit_fact_review":
            seen.append(request["exit_candidates"])
            assert request["exit_context"]["source_node_id"] == source["id"]
            return _exit_review_reply(request)
        if options["operation"] == "numeric_v2_fact_review" and request["checked_node_ids"] == [source["id"]]:
            assert "exit_candidates" not in request and "exit_history_reviewed_route_ids" not in request
            assert _history_markers(request["story_outline"]) == reviewed_refs
        return fallback(messages, **options)

    report = NumericV2QualityAssessor(reply).assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert len(seen) == (0 if guaranteed else 1)
    assert report["passed"] is True and report["issues"] == []
    assert report["overall_score"] == 82
    assert (story, authoring) == before


def test_foreign_scene_history_stays_visible_while_current_exit_receives_special_review():
    story, authoring, foreign_key, _ = _case("agreement")
    previous, source, target = story["nodes"][:3]
    previous["route_gates"][0]["transition_contract"]["must_preserve"] = []
    source["story_beat"]["character_state"]["continuity_from_previous"] = []
    foreign_text = story["fact_contract"]["facts"][foreign_key]["description"]
    missing = f"scene:{source['id']}:independent_result"
    current_text = "玩家已亲口讲述本幕录音唤起的记忆。"
    story["fact_contract"]["facts"][missing] = {
        "value_type": "bool", "visibility": "public", "description": current_text}
    contract = source["route_gates"][0]["transition_contract"]
    contract.update(advance_on_completion=True, must_deliver=[], must_preserve=[current_text, foreign_text])
    for field in ("fallback_offer", "accept_input", "silent_choice", "accept_input_confirmation_only"):
        contract.pop(field, None)
    source["route_gates"][0]["conditions"] = {"all": []}
    target["story_beat"]["character_state"]["continuity_from_previous"] = [current_text, foreign_text]
    NumericV2Engine.from_mapping(story)
    before = deepcopy((story, authoring))
    fallback = _quality_reply(_quality_payload())
    bindings, specials, main_packets = [], [], []

    def reply(messages, **options):
        request = prompt_payload(messages[1]["content"])
        operation = options["operation"]
        if operation == "numeric_v2_exit_fact_binding":
            assert request["source_node_id"] == source["id"]
            definitions = {row["key"] for row in request["fact_definitions"]}
            assert missing in definitions and foreign_key not in definitions
            response = json.loads(_binding_reply(request, missing))
            claims = {claim["ref"]: claim["text"] for route in request["routes"] for claim in route["claims"]}
            foreign = [row for row in response["bindings"] if claims[row["claim_ref"]] == foreign_text]
            assert len(foreign) == 2
            assert all(row["kind"] == "other" and row["fact_key"] is None and row["value"] is None
                       for row in foreign)
            bindings.append((request, response))
            return json.dumps(response, ensure_ascii=False)
        if operation == "numeric_v2_exit_fact_review":
            specials.append(request)
            assert {candidate["fact_key"] for candidate in request["exit_candidates"]} == {missing}
            return _exit_review_reply(request)
        if (operation == "numeric_v2_fact_review" and request["checked_node_ids"] == [source["id"]]
                and request["audit_task"]["kind"] == "transition"):
            main_packets.append(request)
            outline = request["story_outline"]
            by_id = {node["id"]: node for node in outline["mainline"]}
            preserve = by_id[source["id"]]["outgoing_routes"][0]["transition_contract"]["must_preserve"]
            continuity = by_id[target["id"]]["character_state"]["continuity"]
            binding, response = bindings[0]
            claims = {claim["ref"]: claim for route in binding["routes"] for claim in route["claims"]}
            for claim in (preserve[1], continuity[1]):
                assert claim == claims[claim["ref"]] and claim["text"] == foreign_text
            reviewed = {row["claim_ref"] for row in response["bindings"] if row["kind"] == "source_history"}
            assert _history_markers(outline) == reviewed
            assert {preserve[1]["ref"], continuity[1]["ref"]}.isdisjoint(reviewed)
        return fallback(messages, **options)

    report = NumericV2QualityAssessor(reply).assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert len(bindings) == len(specials) == len(main_packets) == 1
    assert report["passed"] is True and report["overall_score"] == 82
    assert (story, authoring) == before


def test_evidence_format_repair_lists_all_nested_targets_without_prejudging():
    story, authoring, _, missing = _case("agreement")
    before = deepcopy((story, authoring))
    source_id = story["start_node_id"]
    fallback = _quality_reply(_quality_payload())
    calls, evidence_inputs = [], []

    def reply(messages, **options):
        operation = options["operation"]
        calls.append(operation)
        request = prompt_payload(messages[1]["content"])
        if operation == "numeric_v2_exit_fact_binding":
            return _binding_reply(request, missing)
        if operation == "numeric_v2_exit_fact_review":
            return _exit_review_reply(request)
        if (operation == "numeric_v2_fact_review" and request["checked_node_ids"] == [source_id]
                and request["audit_task"]["kind"] == "node"):
            node = next(row for row in request["story_outline"]["mainline"] if row["id"] == source_id)
            issues = [{
                "category": "state", "severity": "major", "target_node_ids": [source_id],
                "problem": f"机械接线问题{index}，由独立复核决定是否成立。", "modification_plan": "手动核对所列字段。",
                "expected_result": "保留正确原文。", "preserve": [], "repair_scope": "structure",
                "repair_targets": [{"node_id": source_id, "field": field} for field in fields],
                "evidence_refs": [request["contract_targets"][0]["definition_ref"], node[evidence]["ref"]],
            } for index, (fields, evidence) in enumerate([
                (("/opening_scene", "/character_state/catgirl"), "opening_scene"),
                (("/summary", "/outgoing_routes/0/transition_contract/reason"), "summary"),
            ])]
            return json.dumps(_fact_review_payload(request, issues), ensure_ascii=False)
        if operation in {"numeric_v2_fact_evidence_review", "numeric_v2_fact_evidence_review_format_repair"}:
            assert options["max_retries"] == 1
            assert len(request["proposed_issues"]) == 1
            checks = [review_payload([issue], "supported" if "问题0" in issue["problem"] else "unsupported")["checks"][0]
                      for issue in request["proposed_issues"]]
            if operation == "numeric_v2_fact_evidence_review":
                evidence_inputs.append(request)
                misplaced = [target for check in checks for target in check.pop("target_checks")]
                return json.dumps({"checks": checks, "target_checks": misplaced}, ensure_ascii=False)
            assert request == evidence_inputs[-1]
            correction = prompt_payload(messages[-1]["content"])
            assert correction["required_checks"] == [{
                "issue_id": issue["issue_id"], "target_checks": issue["repair_targets"],
            } for issue in request["proposed_issues"]]
            assert len(correction["required_checks"]) == 1
            assert all(len(check["target_checks"]) == 2 for check in correction["required_checks"])
            return json.dumps({"checks": checks}, ensure_ascii=False)
        return fallback(messages, **options)

    report = NumericV2QualityAssessor(reply).assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert calls.count("numeric_v2_fact_evidence_review") == 2
    assert calls.count("numeric_v2_fact_evidence_review_format_repair") == 2
    assert [issue["evidence_review"]["verdict"] for issue in report["issues"]] == ["supported", "unsupported"]
    assert all(issue["repairable"] is False for issue in report["issues"])
    assert report["passed"] is False and report["overall_score"] == 82
    assert (story, authoring) == before


@pytest.mark.parametrize("verdict,passed", [("supported", False), ("unsupported", True)])
def test_sequence_gap_uses_existing_evidence_verdict_without_rescoring_or_edit_authority(verdict, passed):
    story, authoring, key, missing = _case("agreement")
    before = deepcopy((story, authoring))
    source_id = story["nodes"][0]["id"]
    literary = _quality_payload()
    fallback = _quality_reply(literary)
    calls = []

    def reply(messages, **options):
        operation = options["operation"]
        calls.append(operation)
        request = prompt_payload(messages[1]["content"])
        if operation == "numeric_v2_exit_fact_binding":
            return _binding_reply(request, missing)
        if operation == "numeric_v2_exit_fact_review":
            candidate, = request["exit_candidates"]
            assert candidate["source_node_id"] == source_id and candidate["fact_key"] == missing
            assert candidate["guaranteed"] is False
            definition = next(row["description"] for row in request["exit_context"]["fact_definitions"] if row["key"] == key)
            return _exit_review_reply(request, [{
                "category": "sequence", "severity": "major", "target_node_ids": [source_id],
                "problem": "同意试听可以先成立，但出口将尚未发生的玩家陈述当成必经历史。",
                "modification_plan": "核对实际必要结果，收窄承接或调整完成条件，不把所有目标加锁。",
                "expected_result": "每个合法出幕状态均能支持承接。", "preserve": ["玩家可以暂缓陈述"],
                "repair_scope": "structure",
                "repair_targets": [{"node_id": source_id, "field": "/completion_contract"}],
                # The selected gate and carried claim demonstrate the gap without the omitted-result definition.
                "evidence_refs": [definition["ref"], candidate["claim_refs"][0]],
            }])
        if operation == "numeric_v2_fact_evidence_review":
            issue, = request["proposed_issues"]
            paths = {item["path"] for item in issue["evidence"]}
            assert f"/fact_contract/facts/{key}/description" in paths
            assert "/mainline/0/outgoing_routes/0/transition_contract/must_preserve/0" in paths
            return json.dumps(review_payload([issue], verdict))
        return fallback(messages, **options)

    report = NumericV2QualityAssessor(reply).assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert report["passed"] is passed
    assert report["overall_score"] == 82
    assert {key: row["score"] for key, row in report["scores"].items()} == {
        key: row["score"] for key, row in literary["scores"].items()}
    issue, = report["issues"]
    assert issue["evidence_review"]["verdict"] == verdict
    assert issue["repairable"] is False
    assert report["plan_review"]["status"] == "not_needed"
    assert calls.count("numeric_v2_exit_fact_binding") == 1
    assert calls.count("numeric_v2_exit_fact_review") == 1
    assert calls.count("numeric_v2_fact_evidence_review") == 1
    assert (story, authoring) == before


@pytest.mark.parametrize("operation,failure_kind", [
    (operation, kind) for operation in ("numeric_v2_exit_fact_binding", "numeric_v2_exit_fact_review")
    for kind in ("invalid_json", "timeout")
] + [("numeric_v2_fact_review", "hidden_reference"),
     ("numeric_v2_exit_fact_review", "drop_issue_on_correction"),
     ("numeric_v2_exit_fact_review", "unseen_summary"),
     ("numeric_v2_exit_fact_review", "missing_candidate_check"),
     ("numeric_v2_exit_fact_review", "unparsed_then_empty"),
     ("numeric_v2_exit_fact_review", "inconclusive")])
def test_exit_review_failure_preserves_the_saved_report(tmp_path, operation, failure_kind):
    from services.theater_workshop.host import open_workshop
    from services.theater_workshop.sdk.model import LLMCallFailure
    from .test_sdk_lifecycle import TestConfig

    story, _, _, missing = _case("agreement")
    fallback = _quality_reply(_quality_payload())
    broken = False
    calls, source_summary_refs, rejected_replies = [], [], []

    def reply(messages, **options):
        current = options["operation"]
        calls.append(current)
        request = prompt_payload(messages[1]["content"])
        if (not broken and current == "numeric_v2_fact_review"
                and request["checked_node_ids"] == [story["start_node_id"]]
                and request["audit_task"]["kind"] == "node"):
            source_summary_refs.append(request["story_outline"]["mainline"][0]["summary"]["ref"])
        if (broken and current in {operation, operation + "_format_repair"}
                and (failure_kind != "hidden_reference" or request["audit_task"]["kind"] == "transition")):
            if failure_kind in {"missing_candidate_check", "unparsed_then_empty"}:
                if failure_kind == "unparsed_then_empty" and not current.endswith("_format_repair"):
                    return "not JSON"
                return json.dumps({"checked_node_ids": request["checked_node_ids"], "issues": []})
            if failure_kind == "inconclusive":
                return _exit_review_reply(request, status="uncertain")
            if failure_kind in {"drop_issue_on_correction", "unseen_summary"}:
                if failure_kind == "drop_issue_on_correction" and current.endswith("_format_repair"):
                    assert messages[-2]["role"] == "assistant"
                    assert prompt_payload(messages[-2]["content"])["response"] == rejected_replies[0]
                    return _exit_review_reply(request)
                candidate, = request["exit_candidates"]
                foreign_ref = "not_a_visible_ref"
                if failure_kind == "unseen_summary":
                    foreign_ref, = source_summary_refs
                    visible = set()

                    def collect(item):
                        if set(item) == {"ref", "text"}:
                            visible.add(item["ref"])
                        return item

                    json.loads(json.dumps(request["exit_context"]), object_hook=collect)
                    assert foreign_ref not in visible
                response = _exit_review_reply(request, [{
                    "category": "sequence", "severity": "major", "target_node_ids": request["checked_node_ids"],
                    "problem": "专项试图用未展示的原字段证明承接冲突。", "modification_plan": "先核对实际输入的原文。",
                    "expected_result": "仅使用本次可见原文。", "preserve": [], "repair_scope": "text",
                    "repair_targets": [{"node_id": request["checked_node_ids"][0], "field": "/summary"}],
                    "evidence_refs": [candidate["claim_refs"][0], foreign_ref],
                }])
                rejected_replies.append(response)
                return response
            if failure_kind == "hidden_reference":
                source = request["story_outline"]["mainline"][0]
                hidden_ref = source["outgoing_routes"][0]["transition_contract"]["must_preserve"][0]["exit_history_reviewed_ref"]
                return json.dumps(_fact_review_payload(request, [{
                    "category": "sequence", "severity": "major", "target_node_ids": [source["id"]],
                    "problem": "主审核试图重新引用已交给专项的不可见原文。",
                    "modification_plan": "不能据不可见原文执行修改。", "expected_result": "原文保持。",
                    "preserve": [], "repair_scope": "text",
                    "repair_targets": [{"node_id": source["id"], "field": "/summary"}],
                    "evidence_refs": [request["contract_targets"][0]["definition_ref"], hidden_ref],
                }]), ensure_ascii=False)
            return ("{}" if failure_kind == "invalid_json" else LLMCallFailure(
                "Injected timeout", error_code="model_timeout", exception_type="InjectedTimeout"))
        if current == "numeric_v2_exit_fact_binding":
            return _binding_reply(request, missing)
        if current == "numeric_v2_exit_fact_review":
            return _exit_review_reply(request)
        return fallback(messages, **options)

    host = open_workshop(TestConfig(tmp_path), model_call=reply)
    try:
        project = host.sdk.import_story(story)
        scored = host.sdk.assess_quality(project["project_id"], base_revision=project["revision"])["project"]
        record = host.sdk.root / (project["project_id"] + ".json")
        before, offset = record.read_bytes(), len(calls)
        broken = True
        with pytest.raises(QualityAssessmentError) as error:
            host.sdk.assess_quality(project["project_id"], base_revision=scored["revision"])
        assert error.value.phase == "facts"
        if failure_kind == "inconclusive":
            assert error.value.code == "exit_fact_review_inconclusive"
        if failure_kind in {"drop_issue_on_correction", "unseen_summary"}:
            assert operation + "_format_repair" in calls[offset:]
        assert "numeric_v2_quality_assessment" not in calls[offset:]
        assert record.read_bytes() == before
        assert host.sdk.get_project(project["project_id"]) == scored
    finally:
        host.sdk.close()


@pytest.mark.parametrize("shared", [False, True])
def test_exit_ref_directory_binds_each_route_to_visible_original_material(shared):
    story, authoring, key, missing = _case("agreement")
    source = story["nodes"][0]
    for suffix, value_type, value in (("sample_count", "int", 0), ("selected_value", "string", "record")):
        typed_key = f"scene:{source['id']}:{suffix}"
        story["fact_contract"]["facts"][typed_key] = {
            "value_type": value_type, "visibility": "public", "description": f"原文保留 {suffix} 的值。"}
        source["completion_contract"]["all"].append({"key": typed_key, "equals": value})
    source["route_gates"][0]["conditions"] = {"all": [
        {"type": "fact_equals", "key": key, "value": True},
        {"type": "fact_equals", "key": missing, "value": True}]}
    alternative = deepcopy(source["route_gates"][0])
    alternative.update(id="alternate_exit", target_node_id=story["nodes"][2]["id"], priority=200,
                       conditions={"any": [{"type": "fact_equals", "key": key, "value": True},
                                           {"type": "fact_equals", "key": missing, "value": True}]})
    alternative["transition_contract"]["must_preserve"].append("保留现场已有的环境细节。")
    source["route_gates"].append(alternative)
    assert missing not in {row["key"] for row in source["completion_contract"]["all"]}
    route_candidates = [row for row in NumericV2Engine.from_mapping(story).scene_fact_targets(source["id"])
                        if row["key"] == missing]
    assert route_candidates == [
        {"key": missing, "value": True, "route_id": source["route_gates"][0]["id"], "condition_mode": "all"},
        {"key": missing, "value": True, "route_id": "alternate_exit", "condition_mode": "any"},
    ]
    before = deepcopy((story, authoring))
    assessor = NumericV2QualityAssessor()
    if shared:
        context = assessor._assessment_context(story, _generation_setup(), authoring)
        canonical = context["mainline"][:3]
        context["mainline"] = context["mainline"][3:]
        context["branches"] = [{"id": branch_id, "nodes": deepcopy(canonical)} for branch_id in ("left", "right")]
        assessor._assessment_context = lambda *_: context
    captured, binding_packets, exit_packets = [], [], []

    def capture(messages, **options):
        packet = prompt_payload(messages[1]["content"])
        if options["operation"] == "numeric_v2_exit_fact_binding":
            binding_packets.append(packet)
            return _binding_reply(packet, missing)
        if options["operation"] == "numeric_v2_exit_fact_review":
            exit_packets.append(packet)
            return _exit_review_reply(packet)
        assert options["operation"] == "numeric_v2_fact_review"
        if packet["checked_node_ids"] == [source["id"]] and packet["audit_task"]["kind"] == "transition":
            captured.append(packet)
            if len(captured) == 2:
                raise RuntimeError("exit_packet_captured")
        return json.dumps(_fact_review_payload(packet))

    assessor.call_llm = capture
    with pytest.raises(RuntimeError, match="exit_packet_captured"):
        assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert len(captured) == len(binding_packets) == 2
    assert [packet["audit_task"]["route_id"] for packet in captured] == [route["id"] for route in source["route_gates"]]
    all_other_refs = set()
    for packet, binding, original in zip(captured, binding_packets, source["route_gates"], strict=True):
        outline = packet["story_outline"]
        row, = binding["routes"]
        assert "exit_review_refs" not in packet
        assert row["route_id"] == packet["audit_task"]["route_id"] == original["id"]
        visible_refs = set()

        def collect(item):
            if set(item) == {"ref", "text"}:
                visible_refs.add(item["ref"])
            return item

        json.loads(json.dumps(outline), object_hook=collect)
        original_claims = {claim["ref"]: claim for claim in row["claims"]}
        restored = json.loads(json.dumps(outline), object_hook=lambda item: (
            original_claims[item["exit_history_reviewed_ref"]] if set(item) == {"exit_history_reviewed_ref"} else item))
        nodes = restored["branches"][0]["nodes"] if shared else restored["mainline"]
        by_id = {node["id"]: node for node in nodes}
        projected = by_id[source["id"]]
        route, = projected["outgoing_routes"]
        assert row["target_node_id"] == route["target_node_id"] == original["target_node_id"]
        claims = {item["ref"]: item for item in [*route["transition_contract"]["must_preserve"],
                  *by_id[row["target_node_id"]]["character_state"]["continuity"]]}
        assert row["claims"] == list(claims.values())
        target_node = next(node for node in story["nodes"] if node["id"] == row["target_node_id"])
        assert [claim["text"] for claim in row["claims"]] == [*original["transition_contract"]["must_preserve"],
            *target_node["story_beat"]["character_state"]["continuity_from_previous"]]
        assert row["conditions"] == original["conditions"]
        assert row["completion_contract"] == source["completion_contract"]
        assert len(packet["contract_targets"]) == 1 and packet["contract_targets"][0]["kind"] == "transition_contract"
        for requirement in source["completion_contract"]["all"]:
            definition = next(item["description"] for item in binding["fact_definitions"]
                              if item["key"] == requirement["key"])
            assert definition == outline["fact_contract"]["facts"][requirement["key"]]["description"]
        unmark = lambda item: item["text"] if set(item) == {"ref", "text"} else item
        assert json.loads(json.dumps(route["conditions"]), object_hook=unmark) == original["conditions"]
        assert json.loads(json.dumps(projected["completion_contract"]), object_hook=unmark) == source["completion_contract"]
        response = json.loads(_binding_reply(binding, missing))
        expected_reviewed = {item["claim_ref"] for item in response["bindings"] if item["kind"] == "source_history"}
        assert _history_markers(outline) == expected_reviewed
        other_refs = {item["claim_ref"] for item in response["bindings"] if item["kind"] == "other"}
        all_other_refs.update(other_refs)
        assert other_refs <= visible_refs and not (expected_reviewed & visible_refs)
        assert not (expected_reviewed & other_refs)
    assert all_other_refs
    exit_packet, = exit_packets
    candidate, = exit_packet["exit_candidates"]
    assert "evidence" not in candidate
    assert exit_packet["exit_context"] == binding_packets[1]
    assert candidate["route_id"] == "alternate_exit"  # all has sufficient proof; any does not.
    assert candidate["fact_key"] == missing and candidate["value"] is True
    assert candidate["claim_refs"] == [projected["outgoing_routes"][0]["transition_contract"]["must_preserve"][0]["ref"]]
    original_text = {**{ref: claim["text"] for ref, claim in original_claims.items()},
                     **{item["description"]["ref"]: item["description"]["text"] for item in binding["fact_definitions"]}}
    assert [original_text[ref] for ref in candidate["claim_refs"]] == [story["fact_contract"]["facts"][missing]["description"]]
    assert original_text[candidate["definition_ref"]] == story["fact_contract"]["facts"][missing]["description"]
    if shared:
        assert outline["branches"][1]["nodes"] == [{"id": node["id"],
            "shared_node_ref": f"/branches/0/nodes/{index}"} for index, node in enumerate(nodes)]
    assert (story, authoring) == before
