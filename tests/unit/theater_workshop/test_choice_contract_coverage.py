"""Choice reachability is distinct from proving a chosen exit's history."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater_workshop.sdk.generation.exit_review import fact_is_guaranteed
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from services.theater_workshop.host import open_workshop
from services.theater_workshop.sdk.generation.quality import QualityAssessmentError
from .test_completion_contract_review import _request, _validate
from .test_numeric_v2_generation import _fact_review_payload, _quality_payload, _quality_reply
from .test_numeric_v2_names import named_outline
from .test_sdk_lifecycle import TestConfig, setup_project
from .test_staged_generation import staged_reply


def _alternative(value="B", *, kind="completed", outcome="continue", route_ids=None, compatible=True):
    return {"kind": kind, "value": value, "outcome": outcome,
            "route_ids": ["leave"] if route_ids is None else route_ids,
            "entry_compatible": compatible, "reason": "原稿明确允许的合法结果及其后续。",
            "evidence_refs": []}


def _case(value="A"):
    context, sources, targets, payload = _request()
    key = "choice"
    context["fact_contract"]["facts"][key].update(value_type="string", description="双方可最终选择A或B。")
    definition_ref = payload["contract_checks"][0]["evidence_refs"][0]
    sources[definition_ref]["quote"] = "双方可最终选择A或B。"
    context["mainline"][0]["completion_contract"]["all"][0]["equals"] = value
    for row in payload["contract_checks"]:
        row["alternatives"] = []
    alternative = _alternative()
    alternative["evidence_refs"] = payload["contract_checks"][0]["evidence_refs"][:]
    payload["contract_checks"][0]["alternatives"] = [alternative]
    return context, sources, payload


def test_wrong_clear_cannot_hide_a_legal_final_value_excluded_by_the_literal_gate():
    context, sources, payload = _case()
    route = {"advance_on_completion": True, "completion_contract": context["mainline"][0]["completion_contract"]}
    assert fact_is_guaranteed(route, "choice", "A") is True
    with pytest.raises(ValueError, match="choice"):
        _validate(context, sources, payload)


@pytest.mark.parametrize("mutation", ["missing", "unknown_route", "fixed_wrong_entry", "blocked_clear",
                                      "deferral_as_completion", "nonfinal_continue"])
def test_choice_coverage_protocol_rejects_missing_or_self_contradictory_clear(mutation):
    context, sources, payload = _case("B")
    check = payload["contract_checks"][0]
    alternative = check["alternatives"][0]
    if mutation == "missing":
        del check["alternatives"]
    elif mutation == "unknown_route":
        alternative["route_ids"] = ["invented_route"]
    elif mutation == "fixed_wrong_entry":
        alternative["entry_compatible"] = False
    elif mutation == "blocked_clear":
        alternative["outcome"] = "blocked"
    elif mutation == "nonfinal_continue":
        alternative.update(kind="refusal", value=None)
    else:
        alternative.update(kind="deferral", outcome="stay", route_ids=[], entry_compatible=None)
    with pytest.raises(ValueError):
        _validate(context, sources, payload)


def test_an_allowed_final_value_and_a_nonfinal_refusal_can_both_be_clear():
    context, sources, payload = _case("B")
    refusal = deepcopy(payload["contract_checks"][0]["alternatives"][0])
    refusal.update(kind="refusal", value=None, outcome="stay", route_ids=[], entry_compatible=None)
    payload["contract_checks"][0]["alternatives"].append(refusal)
    before = deepcopy((context, sources, payload))
    result = _validate(context, sources, payload)
    assert result["contract_checks"][0]["alternatives"] == payload["contract_checks"][0]["alternatives"]
    assert (context, sources, payload) == before


def test_explicitly_unreachable_final_alternative_keeps_the_check_uncertain():
    context, sources, payload = _case()
    check = payload["contract_checks"][0]
    check["status"] = "uncertain"
    check["alternatives"][0].update(outcome="blocked", route_ids=[], entry_compatible=None)
    result = _validate(context, sources, payload)
    assert result["contract_checks"][0]["status"] == "uncertain"
    assert result["contract_checks"][0]["alternatives"][0]["value"] == "B"


@pytest.mark.parametrize("settled_flag", [True, False])
def test_sdk_generation_compilation_and_native_choice_match_the_audit_gate(tmp_path, settled_flag):
    outline = named_outline()
    chapter = outline["mainline_chapters"][0]
    description = ("双方已共同在A或B中明确选定一项，具体内容不由true固定。" if settled_flag else
                   "双方共同确定的最终内容，合法结果为A或B。")
    chapter["completion_facts"] = [{"id": "decision", "description": description,
        "value_type": "bool" if settled_flag else "string", "target_value": True if settled_flag else "A",
        "visibility": "story"}]
    if settled_flag:
        chapter["completion_facts"].append({"id": "selected_content", "description": "双方实际选定内容的原值为A或B。",
            "value_type": "string", "target_value": "A", "visibility": "story"})
    chapter["exit_plan"].update(trigger_fact_ids=["decision"], entry_prerequisite_fact_ids=["decision"],
                                advance_on_completion=True, player_decision="", fallback_offer="", accept_input="")
    chapter["exit_plan"].pop("silent_choice", None)
    outline["mainline_chapters"][1]["opening_scene"] = "小岚将记录本翻到新的一页，继续讨论两人刚确定的安排。"
    fallback = _quality_reply(_quality_payload())
    calls = []
    invalid_sent = False
    corrected = False

    def model(messages, **options):
        nonlocal invalid_sent, corrected
        request = json.loads(messages[1]["content"])
        operation = options["operation"]
        calls.append(operation)
        if settled_flag and operation == "numeric_v2_chapter_generation" and request["chapter_index"] == 2:
            prerequisites = request["previous_scene"]["chapter"]["entry_prerequisites"]
            assert [(fact["id"], fact["value_type"], fact["target_value"]) for fact in prerequisites] == [
                ("decision", "bool", True)]
            assert "selected_content" not in {fact["id"] for fact in prerequisites}
        if operation.startswith("numeric_v2_exit_fact_binding"):
            return json.dumps({"bindings": [{"route_id": route["route_id"], "claim_ref": claim["ref"],
                "kind": "other", "fact_key": None, "value": None}
                for route in request["routes"] for claim in route["claims"]]})
        if operation in {"numeric_v2_fact_review", "numeric_v2_fact_review_format_repair"}:
            result = _fact_review_payload(request)
            if request["checked_node_ids"] == ["mainline_01"] and request["audit_task"]["kind"] == "node":
                target, = [row for row in request["contract_targets"] if "equals" in row["requirement"]]
                alternative = _alternative(True if settled_flag else "B", route_ids=target["route_ids"])
                alternative["evidence_refs"] = [target["definition_ref"]]
                refusal = deepcopy(alternative)
                refusal.update(kind="refusal", value=None, outcome="stay", route_ids=[], entry_compatible=None)
                result["contract_checks"][0]["alternatives"] = [alternative, refusal]
            return json.dumps(result, ensure_ascii=False)
        if operation in {"numeric_v2_quality_assessment", "numeric_v2_repair_plan_review",
                         "numeric_v2_literary_block", "numeric_v2_literary_block_format_repair"}:
            return fallback(messages, **options)
        if settled_flag and operation == "numeric_v2_chapter_generation" and request["chapter_index"] == 1:
            if not invalid_sent:
                invalid_sent = True
                invalid = deepcopy(outline)
                invalid["mainline_chapters"][0]["completion_facts"][0].update(
                    value_type="string", target_value="any_non_empty")
                return staged_reply(messages, operation, invalid)
            assert any(issue["code"] == "completion_fact_target_placeholder"
                       and issue["path"] == "chapter.completion_facts[0]" for issue in request["issues"])
            corrected = True
        return staged_reply(messages, operation, outline)

    host = open_workshop(TestConfig(tmp_path), model_call=model)
    try:
        project = setup_project(host.sdk)
        generated = host.sdk.generate(project["project_id"], base_revision=project["revision"])["project"]
        assert corrected is settled_flag
        story = generated["story"]
        source = story["nodes"][0]
        engine = NumericV2Engine.from_mapping(story)
        session = engine.create_session(session_id="choice-coverage", catgirl_binding={
            "character_id": "character_" + "1" * 32, "catgirl_id": "catgirl:character_" + "1" * 32,
            "catgirl_name": "小岚", "player_address": "玩家", "profile_revision": "characters:test",
            "profile_hash": "sha256:test"},
            opening_performance={"performance": "小岚等待玩家选择。", "suggested_inputs": []})
        session = replace(session, node_turn_count=source["min_turns"])
        key = "scene:mainline_01:decision"
        operations = [{"op": "set", "key": key, "value": True if settled_flag else "B", "visibility": "story"}]
        if settled_flag:
            operations.append({"op": "set", "key": "scene:mainline_01:selected_content", "value": "B", "visibility": "story"})
        outcome = engine.resolve_turn(session, TurnRequestV2("choice", 0, "双方已共同确定选择B。"), (),
                                      fact_operations=tuple(operations))
        assert (outcome.session.current_node_id == "mainline_02") is settled_flag
        refusal = engine.resolve_turn(session, TurnRequestV2("refusal", 0, "我先不决定，继续聊一会儿。"), ())
        assert refusal.session.current_node_id == "mainline_01"
        if settled_flag:
            assert outcome.session.story_state["facts"]["scene:mainline_01:selected_content"]["value"] == "B"
            scored = host.sdk.assess_quality(project["project_id"], base_revision=generated["revision"])["project"]
            assert scored["authoring"]["quality_assessment"]["contract_check"]["status"] == "clear"
            assert scored["story"] == story
        else:
            record = host.sdk.root / (project["project_id"] + ".json")
            before = record.read_bytes()
            with pytest.raises(QualityAssessmentError):
                host.sdk.assess_quality(project["project_id"], base_revision=generated["revision"])
            assert calls[-1] == "numeric_v2_fact_review_format_repair"
            assert record.read_bytes() == before
            assert host.sdk.get_project(project["project_id"]) == generated
    finally:
        host.sdk.close()
