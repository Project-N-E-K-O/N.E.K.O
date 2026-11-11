"""Contract-review propagation; model doubles do not establish semantic accuracy."""
from copy import deepcopy
import json

import pytest

from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor, QualityAssessmentError
from .test_numeric_v2_generation import _generation_setup, _quality_payload, _quality_reply, _quality_story_context, _contract_checks


def _contract_reply(request, status="clear"):
    checks = _contract_checks(request)
    for row in checks:
        row["status"] = status
    return {"checked_node_ids": request["checked_node_ids"], "issues": [], "contract_checks": checks}


@pytest.mark.parametrize("status,passed", [("clear", True), ("uncertain", False)])
def test_every_hard_condition_is_reviewed_without_rescoring_literature(status, passed):
    story, authoring = _quality_story_context()
    before = deepcopy(story)
    requests = []
    fallback = _quality_reply(_quality_payload())

    def reply(messages, **options):
        request = json.loads(messages[1]["content"])
        requests.append((options["operation"], request, options))
        if options["operation"] == "numeric_v2_fact_review":
            assert "contract_targets" in request
            return json.dumps(_contract_reply(request, status), ensure_ascii=False)
        return fallback(messages, **options)

    assessor = NumericV2QualityAssessor()
    assessor.call_llm = reply
    report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    expected = sum(len(node.get("completion_contract", {}).get("all", [])) for node in story["nodes"])
    completion_checks = [row for row in report["contract_check"]["checks"] if "kind" not in row]
    assert len(completion_checks) == expected > 0
    assert len(report["contract_check"]["checks"]) == sum(
        len(request["contract_targets"]) for operation, request, _ in requests if operation == "numeric_v2_fact_review")
    assert report["contract_check"]["status"] == ("clear" if passed else "needs_rehearsal")
    assert report["contract_check"]["rehearsal"] == "not_run"
    assert all(row["status"] == status for row in report["contract_check"]["checks"])
    assert report["passed"] is passed
    assert report["overall_score"] == 82
    assert report["issues"] == []
    literature = next(request for operation, request, _ in requests if operation == "numeric_v2_quality_assessment")
    assert "contract_check" not in literature["fact_review"]
    assert "contract_checks" not in literature["fact_review"]
    operations = [operation for operation, _, _ in requests]
    assert operations.count("numeric_v2_fact_review") == len(story["nodes"]) + sum(
        len(node.get("route_gates", [])) for node in story["nodes"])
    assert operations.count("numeric_v2_literary_block") == len({
        (node["id"], route["target_node_id"]) for node in story["nodes"] for route in node.get("route_gates", [])})
    assert operations[-1] == "numeric_v2_quality_assessment"
    assert all(options["max_tokens"] == 6000 for _, _, options in requests)
    assert story == before


def test_missing_contract_coverage_cannot_return_a_partial_assessment():
    story, authoring = _quality_story_context()
    before = deepcopy(story)
    calls = []

    def reply(messages, **options):
        calls.append(options["operation"])
        request = json.loads(messages[1]["content"])
        return json.dumps({"checked_node_ids": request["checked_node_ids"], "issues": []})

    assessor = NumericV2QualityAssessor()
    assessor.call_llm = reply
    with pytest.raises(QualityAssessmentError) as raised:
        assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert raised.value.phase == "facts"
    assert calls == ["numeric_v2_fact_review", "numeric_v2_fact_review_format_repair"]
    assert story == before


def test_format_correction_identifies_missing_contract_field_without_filling_it():
    story, authoring = _quality_story_context()
    fallback = _quality_reply(_quality_payload())
    repaired = []

    def reply(messages, **options):
        request = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_fact_review":
            payload = _contract_reply(request)
            if (request["checked_node_ids"] == [story["nodes"][0]["id"]]
                    and request["audit_task"]["kind"] == "node"):
                del payload["contract_checks"][0]["status"]
            return json.dumps(payload, ensure_ascii=False)
        if options["operation"] == "numeric_v2_fact_review_format_repair":
            correction = json.loads(messages[-1]["content"])
            assert "missing=status" in correction["validation_error"]
            repaired.append(correction)
            return json.dumps(_contract_reply(request), ensure_ascii=False)
        return fallback(messages, **options)

    assessor = NumericV2QualityAssessor()
    assessor.call_llm = reply
    report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert len(repaired) == 1
    assert report["contract_check"]["status"] == "clear"


@pytest.mark.parametrize("fixed", [False, True])
def test_shared_branch_requirement_uses_the_first_visible_definition_once(fixed):
    story, authoring = _quality_story_context()
    branch = deepcopy(story["nodes"][0])
    branch.update(id="shared_branch", route_gates=[])
    renamed = {goal["id"]: "shared_" + goal["id"] for goal in branch["story_beat"]["goals"]}
    for goal in branch["story_beat"]["goals"]:
        goal["id"] = renamed[goal["id"]]
        goal["delivery"]["source_ids"] = [
            "goal." + renamed[ref[5:]] if ref.startswith("goal.") and ref[5:] in renamed else
            "opening.shared_branch" if ref == "opening.mainline_01" else ref
            for ref in goal["delivery"].get("source_ids", [])]
    if fixed:
        branch["story_beat"]["fixed_narrations"] = [{"id": "receipt", "text": "收据列明已结清的款项。"}]
        branch["completion_contract"] = {"all": [{"fixed_narration_id": "receipt"}]}
    story["nodes"].append(branch)
    for node in story["nodes"][:2]:
        node["route_gates"].append({"id": node["id"] + "_detour", "target_node_id": "shared_branch"})
    requests = []
    fallback = _quality_reply(_quality_payload())

    def reply(messages, **options):
        request = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_fact_review":
            if request["checked_node_ids"] == ["shared_branch"]:
                requests.append(request)
                assert len(request["contract_targets"]) == 2
                assert sum("kind" not in target for target in request["contract_targets"]) == 1
                if fixed:
                    assert request["contract_targets"][0]["definition_path"].startswith("/branches/0/")
            return json.dumps(_contract_reply(request), ensure_ascii=False)
        return fallback(messages, **options)

    assessor = NumericV2QualityAssessor()
    assessor.call_llm = reply
    report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    checks = [row for row in report["contract_check"]["checks"] if row["node_id"] == "shared_branch"]
    assert len(requests) == 1 and len(checks) == 2
    assert report["passed"] is True


def test_missing_fact_definition_is_a_facts_error_before_any_model_call():
    story, authoring = _quality_story_context()
    key = story["nodes"][0]["completion_contract"]["all"][0]["key"]
    del story["fact_contract"]["facts"][key]["description"]
    calls = []
    assessor = NumericV2QualityAssessor()
    assessor.call_llm = lambda *args, **options: calls.append(options)
    with pytest.raises(QualityAssessmentError) as raised:
        assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert raised.value.phase == "facts"
    assert calls == []


@pytest.mark.parametrize("verdict,status,severity", [
    ("supported", "requires_revision", "major"), ("unsupported", "uncertain", "major"),
    ("supported", "clear", "minor"),
])
def test_contract_revision_uses_global_issue_ids_and_independent_evidence(verdict, status, severity):
    from .test_numeric_v2_evidence import review_payload

    story, authoring = _quality_story_context()
    fallback = _quality_reply(_quality_payload())
    first_nodes = {node["id"] for node in story["nodes"][:2]}

    def reply(messages, **options):
        request = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_fact_review":
            payload = _contract_reply(request)
            node_id, = request["checked_node_ids"]
            if node_id in first_nodes and request["audit_task"]["kind"] == "node":
                target = request["contract_targets"][0]
                node = next(node for node in request["story_outline"]["mainline"] if node["id"] == node_id)
                payload["issues"] = [{
                    "category": "sequence", "severity": severity, "target_node_ids": [node_id],
                    "problem": "完成条件与开场阶段的原文冲突。", "modification_plan": "核对后调整结构条件。",
                    "expected_result": "完成时点与阶段相容。", "preserve": [], "repair_scope": "structure",
                    "repair_targets": [{"node_id": node_id, "field": "/completion_contract"}],
                    "evidence_refs": [target["definition_ref"], node["opening_scene"]["ref"]],
                }]
                payload["contract_checks"][0]["status"] = "clear" if severity == "minor" else "uncertain"
            return json.dumps(payload, ensure_ascii=False)
        if options["operation"] == "numeric_v2_fact_evidence_review":
            return json.dumps(review_payload(request["proposed_issues"], verdict))
        return fallback(messages, **options)

    assessor = NumericV2QualityAssessor()
    assessor.call_llm = reply
    report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    checks = [row for row in report["contract_check"]["checks"] if row["node_id"] in first_nodes and "kind" not in row]
    assert [row["status"] for row in checks] == [status, status]
    assert [row["issue_ids"] for row in checks] == ([["fact_issue_01"], ["fact_issue_02"]] if verdict == "supported" else [[], []])
    assert all(issue["repairable"] is False for issue in report["issues"])
    assert report["plan_review"]["status"] == "not_needed"
    assert report["passed"] is (severity == "minor")
    assert report["overall_score"] == 82


def test_public_quality_preserves_contract_checks_and_failed_reassessment(tmp_path):
    from services.theater_workshop.host import open_workshop
    from .test_sdk_lifecycle import TestConfig, fixed_model, setup_project

    broken = False
    operations = []
    fallback = _quality_reply(_quality_payload())

    def model(messages, **options):
        operation = options["operation"]
        operations.append(operation)
        if operation in {"numeric_v2_story_frame", "numeric_v2_chapter_generation",
                         "numeric_v2_ending_generation", "numeric_v2_opening_state_extraction"}:
            return fixed_model(messages, **options)
        if broken:
            return "invalid JSON"
        if operation == "numeric_v2_fact_review":
            return json.dumps(_contract_reply(json.loads(messages[1]["content"]), "uncertain"), ensure_ascii=False)
        return fallback(messages, **options)

    host = open_workshop(TestConfig(tmp_path), model_call=model)
    try:
        project = setup_project(host.sdk)
        generated = host.sdk.generate(project["project_id"], base_revision=project["revision"])["project"]
        assert "numeric_v2_fact_review" not in operations
        scored = host.sdk.assess_quality(project["project_id"], base_revision=generated["revision"])["project"]
        assessment = scored["authoring"]["quality_assessment"]
        assert assessment["contract_check"]["status"] == "needs_rehearsal"
        assert assessment["contract_check"]["checks"]
        assert assessment["passed"] is False
        assert scored["revision"] == generated["revision"]
        assert scored["story"] == generated["story"]
        assert host.sdk.get_project(project["project_id"]) == scored
        broken = True
        with pytest.raises(QualityAssessmentError):
            host.sdk.assess_quality(project["project_id"], base_revision=scored["revision"])
        assert host.sdk.get_project(project["project_id"]) == scored
        story = deepcopy(scored["story"])
        story["nodes"][0]["story_beat"]["summary"] += "本幕保留自由回应。"
        updated = host.sdk.update_project(project["project_id"], base_revision=scored["revision"], changes={"story": story})
        assert updated["authoring"]["quality_assessment"]["stale"] is True
        assert updated["authoring"]["quality_assessment"]["contract_check"] == assessment["contract_check"]
    finally:
        host.sdk.close()


def test_target_opening_issue_does_not_invalidate_a_clear_source_condition():
    from .test_numeric_v2_evidence import review_payload

    story, authoring = _quality_story_context()
    source = story["nodes"][0]
    ending = story["nodes"][-1]
    source["route_gates"][0]["target_node_id"] = ending["id"]
    key = source["completion_contract"]["all"][0]["key"]
    fallback = _quality_reply(_quality_payload())

    def reply(messages, **options):
        request = json.loads(messages[1]["content"])
        if options["operation"] == "numeric_v2_fact_review":
            payload = _contract_reply(request)
            if request["checked_node_ids"] == [ending["id"]] and request["audit_task"]["kind"] == "node":
                outline = request["story_outline"]
                target = next(node for node in outline["endings"] if node["id"] == ending["id"])
                payload["issues"] = [{
                    "category": "sequence", "severity": "major", "target_node_ids": [ending["id"]],
                    "problem": "目标开场与已保证的结果冲突。", "modification_plan": "只修目标开场。",
                    "expected_result": "目标承接原有正确条件。", "preserve": ["来源完成条件保持原文"],
                    "repair_scope": "text", "repair_targets": [{"node_id": ending["id"], "field": "/opening_scene"}],
                    "evidence_refs": [outline["fact_contract"]["facts"][key]["description"]["ref"], target["opening_scene"]["ref"]],
                }]
            return json.dumps(payload, ensure_ascii=False)
        if options["operation"] == "numeric_v2_fact_evidence_review":
            return json.dumps(review_payload(request["proposed_issues"]))
        return fallback(messages, **options)

    assessor = NumericV2QualityAssessor()
    assessor.call_llm = reply
    report = assessor.assess(story=story, setup=_generation_setup(), authoring=authoring)
    check = next(row for row in report["contract_check"]["checks"] if row["node_id"] == source["id"])
    assert check["status"] == "clear" and check["issue_ids"] == []
    assert report["passed"] is False
    assert len(report["issues"]) == 1
    assert report["issues"][0]["target_node_ids"] == [ending["id"]]
