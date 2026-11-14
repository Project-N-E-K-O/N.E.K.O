"""Require separate static coverage of completion, exit arrangement and actual entry material."""

from copy import deepcopy

import pytest

from services.theater_workshop.sdk.generation.facts import bind_contract_targets, build_contract_targets, build_fact_sources, validate_fact_review
from .test_completion_contract_review import _context, _source_ref


def _case(choice, bridge):
    context = _context()
    context["start_node_id"] = "main"
    context["mainline"][0]["outgoing_routes"][0]["transition_contract"] = {
        "reason": choice, "bridge_scene_narration": bridge,
        "fallback_offer": "你愿意按刚才商量的安排继续吗？", "accept_input": "好，我愿意。",
    }
    return context


def _review(context, focus="main", status="clear"):
    _, sources = build_fact_sources(context)
    targets = build_contract_targets(context, {focus})
    bind_contract_targets(targets, sources)
    payload = {"checked_node_ids": [focus], "issues": [], "contract_checks": [{
        "target_id": target["id"], "status": status, "subject": "原文指定的实际行动者。",
        "completion_evidence": "按该项原文核对其前因及授权，不当作已发生事实。",
        "timing": "区分来源、接受后的桥段与实际自动入幕材料。",
        "refusal": "拒绝或暂缓仍可留幕，不增加完成锁。", "alternatives": [],
        "evidence_refs": [_source_ref(sources, target["definition_path"])],
    } for target in targets]}
    from .test_numeric_v2_generation import _matrix_fixture
    for target, check in zip(targets, payload["contract_checks"], strict=True):
        check.update(_matrix_fixture(target, reason_quote=sources[target["definition_ref"]]["quote"]))
    return sources, targets, payload


@pytest.mark.parametrize("choice,bridge", [
    ("可以先电话询问或去社区核实。", "玩家已出门走进社区值班处。"),
    ("可选择当面签收或委托快递寄回。", "玩家已经到寄件窗口办理快递。"),
])
def test_each_authored_route_and_entry_needs_its_own_original_cited_check(choice, bridge):
    context = _case(choice, bridge)
    before = deepcopy(context)
    sources, targets, payload = _review(context)
    assert [row.get("kind", "completion") for row in targets] == [
        "completion", "completion", "transition_contract", "entry_ownership"]
    assert [row["id"] for row in targets[:2]] == ["main:completion:0", "main:completion:1"]
    route, entry = targets[2:]
    assert route["definition_path"] == "/mainline/0/outgoing_routes/0/transition_contract/reason"
    assert route["route_ids"] == ["leave"] and route["requirement"] == {}
    assert entry["definition_path"] == "/mainline/0/opening_scene" and entry["requirement"] == {}
    for omit_kind in ("transition_contract", "entry_ownership"):
        incomplete = deepcopy(payload)
        incomplete["contract_checks"] = [row for row, target in zip(payload["contract_checks"], targets)
                                        if target.get("kind") != omit_kind]
        with pytest.raises(ValueError, match="incomplete_contract_review"):
            validate_fact_review(context, incomplete, {"main"}, evidence_sources=sources)
    reviewed = validate_fact_review(context, payload, {"main"}, evidence_sources=sources)
    assert len(reviewed["contract_checks"]) == 4 and reviewed["issues"] == []
    assert context == before


@pytest.mark.parametrize("status", ["clear", "uncertain"])
def test_coverage_verdicts_never_create_facts_issues_or_new_refusal_routes(status):
    context = _case("玩家接受后可同行去长街，也可暂缓留在本幕。", "两人沿已接受的路线走到长街。")
    context["mainline"][0]["opening_scene"] = "小岚自己打开台灯，向玩家展示登记信息。"
    sources, targets, payload = _review(context, status=status)
    assert any(target.get("kind") == "entry_ownership" for target in targets)
    reviewed = validate_fact_review(context, payload, {"main"}, evidence_sources=sources)
    assert reviewed["issues"] == []
    assert all(row["status"] == status and row["issue_ids"] == [] for row in reviewed["contract_checks"])
    assert context["mainline"][0]["outgoing_routes"][0]["target_node_id"] == "ending"


@pytest.mark.parametrize("kind", ["transition_contract", "entry_ownership"])
def test_noncompletion_checks_cannot_invent_typed_alternatives(kind):
    context = _case("玩家接受后去长街。", "两人沿已接受路线走到长街。")
    sources, targets, payload = _review(context)
    index = next(i for i, target in enumerate(targets) if target.get("kind") == kind)
    check = payload["contract_checks"][index]
    check["alternatives"] = [{"kind": "deferral", "value": None, "outcome": "stay", "route_ids": [],
        "entry_compatible": None, "reason": "不凭审核生成选项。", "evidence_refs": check["evidence_refs"][:]}]
    with pytest.raises(ValueError, match="noncompletion_alternatives"):
        validate_fact_review(context, payload, {"main"}, evidence_sources=sources)


@pytest.mark.parametrize("initial,mixed,opening_expected", [(False, False, False), (True, False, True), (False, True, True)])
def test_entry_checks_follow_fixed_only_and_actual_initial_or_mixed_inbounds(initial, mixed, opening_expected):
    context = _context()
    context["start_node_id"] = "free" if initial else "main"
    route = context["mainline"][0]["outgoing_routes"][0]
    route.update(target_node_id="free", transition_contract={"reason": "继续", "fixed_entry_only": True})
    target = context["mainline"][1]
    target["fixed_narrations"] = [{"id": "original", "text": "店员打开抽屉展示收据。", "trigger": {"type": "entry"}}]
    if mixed:
        context["branches"][0]["nodes"][0]["outgoing_routes"][0].update(
            target_node_id="free", transition_contract={"reason": "继续", "fixed_entry_only": False})
    targets = build_contract_targets(context, {"free"})
    paths = {target["definition_path"] for target in targets}
    assert ("/mainline/1/opening_scene" in paths) is opening_expected
    assert "/mainline/1/fixed_narrations/0/text" in paths


def test_legacy_opening_check_cites_actual_fallback_field():
    context = _case("继续", "两人走到长街。")
    context["mainline"][0].pop("opening_scene")
    target = next(row for row in build_contract_targets(context, {"main"}) if row.get("kind") == "entry_ownership")
    assert target["definition_path"] == "/mainline/0/summary"
