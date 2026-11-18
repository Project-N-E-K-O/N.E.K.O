"""Check contract-review coverage and provenance, without asserting model accuracy."""
from copy import deepcopy

import pytest

from services.theater_workshop.sdk.generation.facts import (
    build_contract_targets,
    build_fact_sources,
    validate_fact_review,
)


NPC_KEY = "scene:station/pass~public"


def _context():
    return {
        "characters": {"player_name": "林舟", "catgirl_name": "小岚"},
        "fact_contract": {"facts": {
            "choice": {"value_type": "bool", "visibility": "public",
                       "description": "林舟明确接受已经公开的安排。"},
            NPC_KEY: {"value_type": "bool", "visibility": "public",
                      "description": "管事实际核验文书并公开准许通行；玩家请求不算放行。"},
            "ending_result": {"value_type": "bool", "visibility": "public",
                              "description": "小岚已经归还收据。"},
            "optional": {"value_type": "bool", "visibility": "public",
                         "description": "可选的闲聊，不是离幕条件。"},
        }},
        "mainline": [{
            "id": "main", "summary": "本幕只讨论方案，不执行验章。",
            "opening_scene": "管事把待核验的文书放在桌上。",
            "completion_contract": {"all": [
                {"key": "choice", "equals": True}, {"key": NPC_KEY, "equals": True},
            ]},
            "outgoing_routes": [{"id": "leave", "target_node_id": "ending"}],
        }, {
            "id": "free", "summary": "可以拒绝邀请，继续在场闲聊。",
            "opening_scene": "小岚等待林舟回应。", "outgoing_routes": [],
        }],
        "branches": [{"id": "detour", "nodes": [{
            "id": "branch", "summary": "当场展示收据。", "opening_scene": "收据放在桌上。",
            "fixed_narrations": [{"id": "receipt", "text": "收据上写着已经结清的款项。"}],
            "completion_contract": {"all": [{"fixed_narration_id": "receipt"}]},
            "outgoing_routes": [{"id": "rejoin", "target_node_id": "ending"}],
        }]}],
        "endings": [{
            "id": "ending", "summary": "归还收据后收束。", "opening_scene": "小岚递还收据。",
            "completion_contract": {"all": [{"key": "ending_result", "equals": True}]},
            "outgoing_routes": [],
        }],
    }


def _source_ref(sources, path):
    return next(ref for ref, source in sources.items() if source["path"] == path)


def _request(checked=("main",)):
    context = _context()
    _, sources = build_fact_sources(context)
    targets = build_contract_targets(context, set(checked))
    checks = [{
        "target_id": target["id"], "status": "clear",
        "subject": "按该条件定义的实际主体完成。",
        "completion_evidence": "只用已经实际交付的可观察结果判断。",
        "timing": "该结果发生后才可成立。",
        "refusal": "玩家拒绝或暂缓时可以继续留幕，不自动视为完成。",
        "alternatives": [],
        "evidence_refs": [_source_ref(sources, target["definition_path"])],
    } for target in targets]
    payload = {"checked_node_ids": list(checked), "issues": [], "contract_checks": checks}
    from .test_numeric_v2_generation import _matrix_fixture
    for target, check in zip(targets, checks, strict=True):
        check.update(_matrix_fixture(target))
    return context, sources, targets, payload


def _validate(context, sources, payload):
    return validate_fact_review(context, payload, {"main", "free", "branch", "ending"},
                                evidence_sources=sources,
                                checked_node_ids=set(payload["checked_node_ids"]))


def _issue(sources, *, node_id="main"):
    return {
        "category": "sequence", "severity": "major", "target_node_ids": [node_id],
        "problem": "本幕禁止验章，但离幕条件要求实际验章结果。",
        "evidence_refs": [_source_ref(sources, "/mainline/0/summary"),
                          _source_ref(sources, f"/fact_contract/facts/{NPC_KEY.replace('~', '~0').replace('/', '~1')}/description")],
        "modification_plan": "明确允许执行的阶段并相应调整完成条件。",
        "expected_result": "阶段权限与离幕条件相容。", "preserve": ["保留玩家的选择权"],
        "repair_scope": "structure",
        "repair_targets": [{"node_id": node_id, "field": "/completion_contract"}],
    }


def test_contract_targets_cover_actual_hard_conditions_with_original_paths():
    context = _context()
    before = deepcopy(context)
    targets = build_contract_targets(context, {"main", "branch", "ending", "free"})
    assert len(targets) == 8
    assert len({target["id"] for target in targets}) == 8
    by_path = {target["path"]: target for target in targets}
    main = by_path["/mainline/0/completion_contract/all/1"]
    assert main["node_id"] == "main"
    assert main["requirement"] == {"key": NPC_KEY, "equals": True}
    assert main["definition_path"] == "/fact_contract/facts/scene:station~1pass~0public/description"
    assert main["route_ids"] == ["leave"]
    branch = by_path["/branches/0/nodes/0/completion_contract/all/0"]
    assert branch["node_id"] == "branch"
    assert branch["definition_path"] == "/branches/0/nodes/0/fixed_narrations/0/text"
    assert branch["route_ids"] == ["rejoin"]
    ending = by_path["/endings/0/completion_contract/all/0"]
    assert ending["node_id"] == "ending"
    assert ending["route_ids"] == []
    assert all(target["requirement"].get("key") != "optional" for target in targets)
    assert targets == build_contract_targets(context, {"free", "ending", "branch", "main"})
    assert context == before
    targets[0]["requirement"].clear()
    assert context == before


def test_focus_does_not_add_neighbor_conditions_or_reindex_original_paths():
    context = _context()
    targets = build_contract_targets(context, {"ending"})
    assert len(targets) == 2
    assert targets[0]["node_id"] == "ending"
    assert targets[0]["path"] == "/endings/0/completion_contract/all/0"


def test_target_enumeration_keeps_a_declared_false_value_without_coercion():
    context = _context()
    context["mainline"][0]["completion_contract"]["all"][1]["equals"] = False
    targets = build_contract_targets(context, {"main"})
    assert targets[1]["requirement"] == {"key": NPC_KEY, "equals": False}


@pytest.mark.parametrize("checked", [("main",), ("branch",), ("ending",)])
def test_complete_checks_keep_source_identity_and_do_not_mutate_inputs(checked):
    context, sources, targets, payload = _request(checked)
    before = deepcopy((context, sources, payload))
    review = _validate(context, sources, payload)
    checks = review["contract_checks"]
    assert len(checks) == len(targets)
    for target, check in zip(targets, checks, strict=True):
        assert check["target_id"] == target["id"]
        assert check["node_id"] == target["node_id"]
        assert check["status"] == "clear"
        assert check["issue_ids"] == []
        assert check["evidence"] == [sources[_source_ref(sources, target["definition_path"])]]
        for field in ("subject", "completion_evidence", "timing", "refusal"):
            assert check[field] == payload["contract_checks"][targets.index(target)][field]
    assert (context, sources, payload) == before
    assert review["issues"] == []


def test_response_order_does_not_reassign_condition_identity():
    context, sources, targets, payload = _request()
    payload["contract_checks"][0]["subject"] = "玩家本人明确表态。"
    payload["contract_checks"][1]["subject"] = "在场管事实际核验。"
    payload["contract_checks"].reverse()
    checks = _validate(context, sources, payload)["contract_checks"]
    assert [row["target_id"] for row in checks] == [target["id"] for target in targets]
    assert [row["subject"] for row in checks[:2]] == ["玩家本人明确表态。", "在场管事实际核验。"]


@pytest.mark.parametrize("case", ["missing_field", "empty", "missing_one", "duplicate", "unknown",
                                      "neighbor_target", "non_string_target"])
def test_incomplete_or_forged_target_coverage_is_rejected(case):
    context, sources, targets, payload = _request()
    if case == "missing_field":
        del payload["contract_checks"]
    elif case == "empty":
        payload["contract_checks"] = []
    elif case == "missing_one":
        payload["contract_checks"].pop()
    elif case == "duplicate":
        payload["contract_checks"] = [payload["contract_checks"][0]] * len(targets)
    elif case == "unknown":
        payload["contract_checks"][0]["target_id"] = "not-a-target"
    elif case == "neighbor_target":
        payload["contract_checks"][0]["target_id"] = build_contract_targets(context, {"ending"})[0]["id"]
    else:
        payload["contract_checks"][0]["target_id"] = [targets[0]["id"]]
    with pytest.raises(ValueError):
        _validate(context, sources, payload)


@pytest.mark.parametrize("case", ["missing_refs", "empty_refs", "unknown_ref", "metadata_id", "wrong_target_anchor",
                                      "non_anchor_quote", "duplicate_ref", "tampered_quote", "wrong_source_path"])
def test_contract_check_references_must_bind_the_actual_condition(case):
    context, sources, targets, payload = _request()
    check = payload["contract_checks"][0]
    ref = check["evidence_refs"][0]
    if case == "missing_refs":
        del check["evidence_refs"]
    elif case == "empty_refs":
        check["evidence_refs"] = []
    elif case == "unknown_ref":
        check["evidence_refs"] = ["E999999"]
    elif case == "metadata_id":
        check["evidence_refs"] = [targets[0]["id"]]
    elif case == "wrong_target_anchor":
        check["evidence_refs"] = [_source_ref(sources, targets[1]["definition_path"])]
    elif case == "non_anchor_quote":
        check["evidence_refs"] = [_source_ref(sources, "/mainline/0/opening_scene")]
    elif case == "duplicate_ref":
        check["evidence_refs"] = [ref, ref]
    elif case == "tampered_quote":
        sources[ref]["quote"] = "原稿中不存在的完成结果。"
    else:
        sources[ref]["path"] = "/fact_contract/facts/unknown/description"
    with pytest.raises(ValueError):
        _validate(context, sources, payload)


@pytest.mark.parametrize("field", ["subject", "completion_evidence", "timing", "refusal"])
@pytest.mark.parametrize("value", ["", "  ", None, []])
def test_each_execution_question_needs_an_explicit_answer(field, value):
    context, sources, _, payload = _request()
    payload["contract_checks"][0][field] = value
    with pytest.raises(ValueError):
        _validate(context, sources, payload)


@pytest.mark.parametrize("status", ["passed", "complete", "approved", "requires_revision", "", None, True])
def test_model_status_cannot_claim_runtime_approval(status):
    context, sources, _, payload = _request()
    payload["contract_checks"][0]["status"] = status
    with pytest.raises(ValueError):
        _validate(context, sources, payload)


def test_uncertain_condition_is_preserved_without_inventing_a_conflict_or_edit():
    context, sources, _, payload = _request()
    check = payload["contract_checks"][0]
    check.update(status="uncertain", completion_evidence="原文未说明如何区分完整体验与仅展示。",
                 timing="尚不能确认选择生效的阶段。", refusal="未声明拒绝后的故事处理，需排演确认。")
    review = _validate(context, sources, payload)
    result = review["contract_checks"][0]
    assert result["status"] == "uncertain"
    assert result["completion_evidence"] == check["completion_evidence"]
    assert result["issue_ids"] == []
    assert review["issues"] == []
    assert "repair_targets" not in result
    assert "repairable" not in result


@pytest.mark.parametrize("status", ["clear", "uncertain"])
def test_explicit_issue_does_not_authorize_revision_before_independent_review(status):
    context, sources, _, payload = _request()
    payload["issues"] = [_issue(sources)]
    payload["contract_checks"][1]["status"] = status
    review = _validate(context, sources, payload)
    assert review["contract_checks"][1]["status"] == status
    assert all(check["issue_ids"] == [] for check in review["contract_checks"])
    assert review["issues"][0]["issue_id"] == "fact_issue_01"
    assert review["issues"][0]["repair_scope"] == "structure"
    assert review["issues"][0]["repairable"] is False
    assert review["issues"][0]["repair_targets"] == [{"node_id": "main", "field": "/completion_contract"}]
    assert "repair_targets" not in review["contract_checks"][1]


def test_existing_issue_does_not_assign_contract_check_ids_by_node_alone():
    context, sources, _, payload = _request()
    issue = _issue(sources)
    issue["evidence_refs"] = [_source_ref(sources, "/mainline/0/summary"),
                              _source_ref(sources, "/mainline/0/opening_scene")]
    payload["issues"] = [issue]
    review = _validate(context, sources, payload)
    assert len(review["issues"]) == 1
    assert all(check["status"] == "clear" and check["issue_ids"] == []
               for check in review["contract_checks"])


@pytest.mark.parametrize("field, value", [("repairable", True), ("repair_scope", "text"),
                                           ("repair_targets", [{"node_id": "main", "field": "/completion_contract"}]),
                                           ("issue_ids", ["fact_issue_01"])])
def test_contract_checks_cannot_add_edit_authority(field, value):
    context, sources, _, payload = _request()
    payload["contract_checks"][0][field] = value
    with pytest.raises(ValueError):
        _validate(context, sources, payload)


@pytest.mark.parametrize("status", ["clear", "uncertain", "requires_revision"])
@pytest.mark.parametrize("indexes", [[], [0], [-1], [False], [0, 0], None])
def test_legacy_issue_indexes_are_rejected_even_with_an_existing_issue(status, indexes):
    context, sources, _, payload = _request()
    payload["issues"] = [_issue(sources)]
    check = payload["contract_checks"][1]
    check.update(status=status, issue_indexes=indexes)
    with pytest.raises(ValueError):
        _validate(context, sources, payload)


def test_model_cannot_skip_independent_review_by_declaring_revision_required():
    context, sources, _, payload = _request()
    payload["issues"] = [_issue(sources)]
    payload["contract_checks"][1]["status"] = "requires_revision"
    with pytest.raises(ValueError):
        _validate(context, sources, payload)


@pytest.mark.parametrize("omit", [False, True])
def test_no_hard_condition_needs_no_fabricated_contract_check(omit):
    context = _context()
    # A scene with neither a hard condition nor actual automatic entry material
    # needs no fabricated check. Ordinary entry material now has its own duty.
    context["mainline"][1].pop("opening_scene")
    context["mainline"][1].pop("summary")
    _, sources = build_fact_sources(context)
    targets = build_contract_targets(context, {"free"})
    payload = {"checked_node_ids": ["free"], "issues": [], "contract_checks": []}
    assert targets == []
    if omit:
        del payload["contract_checks"]
    review = _validate(context, sources, payload)
    assert review["contract_checks"] == []
    assert review["issues"] == []


def test_free_interaction_cannot_accept_a_neighbor_check_as_its_own():
    context, sources, _, payload = _request(("free",))
    _, _, _, other_payload = _request(("main",))
    payload["contract_checks"] = other_payload["contract_checks"]
    with pytest.raises(ValueError):
        _validate(context, sources, payload)
