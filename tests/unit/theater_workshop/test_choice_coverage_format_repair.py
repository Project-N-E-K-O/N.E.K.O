"""Minimized from new-author/A quality-004..007; no live API or original package mutation.

The real replies used string ``true`` and ``key=true`` for bool requirements,
then removed alternatives on one format correction. Doubles test the protocol,
not whether the cited fiction semantically contains those alternatives.
"""
from copy import deepcopy
import json

import pytest

from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from .test_choice_contract_coverage import _alternative
from .test_completion_contract_review import _request, _validate
from .test_numeric_v2_generation import (
    _fact_review_payload, _generation_setup, _quality_payload, _quality_reply, _quality_story_context,
)


@pytest.mark.parametrize("value", ["true", "scene:mainline_03:recording_decision_confirmed=true"])
def test_actual_a_bool_strings_raise_type_error_without_coercion(value):
    context, sources, targets, payload = _request()
    check = payload["contract_checks"][0]
    row = _alternative(value)
    row["evidence_refs"] = check["evidence_refs"][:]
    check["alternatives"] = [row]
    before = deepcopy(payload)
    with pytest.raises(ValueError, match="choice_completion_value_type.*expected_json_type=bool"):
        _validate(context, sources, payload)
    assert payload == before


@pytest.mark.parametrize("expected,value,type_name", [(2, "2", "int"), (1, True, "int"), ("done", True, "string")])
def test_type_diagnostics_apply_to_int_and_string_requirements(expected, value, type_name):
    context, sources, _targets, payload = _request()
    context["mainline"][0]["completion_contract"]["all"][0]["equals"] = expected
    context["fact_contract"]["facts"]["choice"]["value_type"] = type_name
    check = payload["contract_checks"][0]
    row = _alternative(value)
    row["evidence_refs"] = check["evidence_refs"][:]
    check["alternatives"] = [row]
    with pytest.raises(ValueError, match=f"expected_json_type={type_name}"):
        _validate(context, sources, payload)


def _assess_correction(mutate_first, mutate_second, *, check_feedback=None):
    story, authoring = _quality_story_context()
    fallback = _quality_reply(_quality_payload())
    requests, first = [], None

    def model(messages, **options):
        nonlocal first
        operation = options["operation"]
        request = json.loads(messages[1]["content"])
        if (operation in {"numeric_v2_fact_review", "numeric_v2_fact_review_format_repair"}
                and request["checked_node_ids"] == [story["start_node_id"]]
                and request["audit_task"]["kind"] == "node"):
            requests.append(operation)
            if operation == "numeric_v2_fact_review":
                first = _fact_review_payload(request)
                target, = [row for row in request["contract_targets"] if "equals" in row["requirement"]]
                alternative = _alternative("true", route_ids=target["route_ids"])
                alternative["evidence_refs"] = [target["definition_ref"]]
                first["contract_checks"][0]["alternatives"] = [alternative]
                mutate_first(first, request)
                return json.dumps(first, ensure_ascii=False)
            feedback = json.loads(messages[-1]["content"])
            if check_feedback:
                check_feedback(feedback, request)
            corrected = deepcopy(first)
            mutate_second(corrected, request)
            return json.dumps(corrected, ensure_ascii=False)
        return fallback(messages, **options)

    assessor = NumericV2QualityAssessor(model)
    before = deepcopy((story, authoring))
    try:
        report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
        assert (story, authoring) == before
        return report, requests
    finally:
        assert requests == ["numeric_v2_fact_review", "numeric_v2_fact_review_format_repair"]


def _keep(_payload, _request):
    pass


def _correct_type(payload, _request):
    for row in payload["contract_checks"][0]["alternatives"]:
        if row["kind"] == "completed":
            row["value"] = True


def test_a_format_feedback_gives_the_typed_requirement_and_preserves_rows():
    def feedback(data, request):
        target, = [row for row in request["contract_targets"] if "equals" in row["requirement"]]
        assert data["completion_value_types"] == [{
            "target_id": target["id"], "expected_json_type": "bool", "requirement": target["requirement"]}]
        assert data["required_alternatives"][0]["target_id"] == target["id"]
        assert data["required_alternatives"][0]["evidence_refs"] == [target["definition_ref"]]
        assert "严禁自动转换" in data["instruction"]

    report, _ = _assess_correction(_keep, _correct_type, check_feedback=feedback)
    assert report["contract_check"]["checks"][0]["alternatives"][0]["value"] is True


def test_actual_a_clear_to_empty_format_bypass_is_rejected():
    def clear(payload, _request):
        payload["contract_checks"][0]["alternatives"] = []

    with pytest.raises(QualityAssessmentError) as raised:
        _assess_correction(_keep, clear)
    assert raised.value.phase == "facts"


@pytest.mark.parametrize("removed_kind", ["completed", "refusal"])
def test_independent_same_citation_alternatives_cannot_be_replaced_with_duplicate_slots(removed_kind):
    def first(payload, _request):
        rows = payload["contract_checks"][0]["alternatives"]
        other = deepcopy(rows[0])
        other["reason"] = "同一原字段中的另一项独立合法安排。"
        rows.append(other)
        refusal = deepcopy(rows[0])
        refusal.update(kind="refusal", value=None, outcome="stay", route_ids=[], entry_compatible=None,
                       reason="玩家未作成决定时可以拒绝并合理留幕。")
        rows.append(refusal)

    def second(payload, request):
        _correct_type(payload, request)
        rows = payload["contract_checks"][0]["alternatives"]
        rows.pop(1 if removed_kind == "completed" else 2)
        rows.append(deepcopy(rows[0]))

    with pytest.raises(QualityAssessmentError):
        _assess_correction(first, second)


def test_twenty_identical_incomplete_slots_can_recover_as_one_original_alternative():
    def first(payload, _request):
        rows = payload["contract_checks"][0]["alternatives"]
        payload["contract_checks"][0]["alternatives"] = [deepcopy(rows[0]) for _ in range(20)]

    def second(payload, request):
        payload["contract_checks"][0]["alternatives"] = payload["contract_checks"][0]["alternatives"][:1]
        _correct_type(payload, request)

    report, _ = _assess_correction(first, second)
    assert len(report["contract_check"]["checks"][0]["alternatives"]) == 1


def test_format_repair_cannot_change_an_already_typed_legal_value_to_fit_the_gate():
    def first(payload, _request):
        payload["contract_checks"][0]["alternatives"][0]["value"] = False

    with pytest.raises(QualityAssessmentError):
        _assess_correction(first, _correct_type)


def test_bad_type_and_route_fields_can_be_corrected_without_changing_the_choice_identity():
    def first(payload, _request):
        payload["contract_checks"][0]["alternatives"][0]["route_ids"] = ["unknown_route"]

    def second(payload, request):
        _correct_type(payload, request)
        payload["contract_checks"][0]["alternatives"][0]["route_ids"] = request["contract_targets"][0]["route_ids"]

    report, _ = _assess_correction(first, second)
    assert report["contract_check"]["checks"][0]["alternatives"][0]["value"] is True


def test_unverifiable_reference_slot_does_not_become_a_required_alternative():
    def first(payload, _request):
        rows = payload["contract_checks"][0]["alternatives"]
        forged = deepcopy(rows[0])
        forged["evidence_refs"] = ["not_a_visible_source"]
        rows.append(forged)

    def second(payload, request):
        payload["contract_checks"][0]["alternatives"].pop()
        _correct_type(payload, request)

    report, _ = _assess_correction(first, second)
    assert len(report["contract_check"]["checks"][0]["alternatives"]) == 1


def test_first_reply_empty_alternatives_remains_a_model_semantics_limit():
    story, authoring = _quality_story_context()
    model = _quality_reply(_quality_payload())
    report = NumericV2QualityAssessor(model).assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert all(check["alternatives"] == [] for check in report["contract_check"]["checks"])


@pytest.mark.parametrize("compatible", [False, None, True])
def test_handoff_format_repair_preserves_the_recorded_verdict_while_restoring_citations(compatible):
    story, authoring = _quality_story_context()
    fallback = _quality_reply(_quality_payload())
    calls = []

    def model(messages, **options):
        request = json.loads(messages[1]["content"])
        operation = options["operation"]
        if (operation in {"numeric_v2_fact_review", "numeric_v2_fact_review_format_repair"}
                and request["audit_task"]["kind"] == "transition"
                and request["checked_node_ids"] == [story["start_node_id"]]):
            calls.append(operation)
            result = _fact_review_payload(request)
            check = result["contract_checks"][0]
            if operation == "numeric_v2_fact_review":
                check["status"] = "uncertain"
                row = check["choice_checks"][0]
                row.update(compatible=compatible, counterexample="玩家仅接受核实，未保证固定桥段中的额外行动。" if compatible is False else "")
                row["downstream_refs"].pop()
            else:
                feedback = json.loads(messages[-1]["content"])
                assert any(row.get("compatible", "absent") is compatible
                           for row in feedback["required_alternatives"])
            # The format correction restores citations but also silently changes
            # a semantic judgment to clear, which must not be accepted.
            return json.dumps(result, ensure_ascii=False)
        return fallback(messages, **options)

    assessor = NumericV2QualityAssessor(model)
    if compatible is True:
        assert assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)["passed"] is True
    else:
        with pytest.raises(QualityAssessmentError) as raised:
            assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
        assert raised.value.phase == "facts"
        assert raised.value.provider_details["validation_error"] == "fact_format_repair_changed_choice_coverage"
    assert calls == ["numeric_v2_fact_review", "numeric_v2_fact_review_format_repair"]


def test_sent_review_example_value_satisfies_the_actual_bool_contract():
    story, authoring = _quality_story_context()
    fallback = _quality_reply(_quality_payload())
    sent_values = []

    def model(messages, **options):
        if options["operation"] not in {"numeric_v2_fact_review", "numeric_v2_fact_review_format_repair"}:
            return fallback(messages, **options)
        request = json.loads(messages[1]["content"])
        prompt = messages[0]["content"]
        start = prompt.index("每项为{", prompt.index("alternatives仅用于")) + len("每项为")
        example, _ = json.JSONDecoder().raw_decode(prompt[start:])
        payload = _fact_review_payload(request)
        for target, check in zip(request["contract_targets"], payload["contract_checks"], strict=True):
            if "equals" not in target["requirement"]:
                assert check["alternatives"] == []
                continue
            assert type(target["requirement"]["equals"]) is bool
            alternative = _alternative(example["value"], route_ids=target["route_ids"])
            alternative["evidence_refs"] = [target["definition_ref"]]
            check["alternatives"] = [alternative]
            sent_values.append(alternative["value"])
        return json.dumps(payload, ensure_ascii=False)

    report = NumericV2QualityAssessor(model).assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert sent_values and all(value is True for value in sent_values)
    assert all(check["alternatives"][0]["value"] is True for check in report["contract_check"]["checks"]
               if "equals" in check["requirement"])
