"""Semantic uncertainty warns while concrete findings retain bounded confirmation."""

import pytest

from services.theater import numeric_v2_evaluator as ev
from tests.unit.test_theater_numeric_v2_bound_completion_contract import _bound, _install, _review
from tests.unit.test_theater_numeric_v2_fact_discovery import _body, _frame
from tests.utils.theater_prompt import contract_audit_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


def _confirmed(data, *, change=None):
    findings = data.get("findings", [data.get("finding")])
    rows = []
    for finding in findings:
        row = {"verdict": "clear", "reason": "整块仅交付角色自身行为。",
               "clearance": [finding["candidate"]["id"]], "player_action_scope": "none"}
        if change is not None:
            change(finding, row)
        rows.append(row)
    return {"verdicts": rows} if "findings" in data else rows[0]


@pytest.mark.asyncio
async def test_wrong_quote_repair_keeps_author_uncertainty_as_a_diagnostic(monkeypatch):
    engine, session = _frame()
    body = _body()

    def respond(messages, data, kwargs):
        if "player_authority_repair" in data:
            assert data["player_authority_repair"] == {"candidate_refs": ["c0"]}
            return {"player_authority": {"c0": "none"}}
        payload = contract_audit_payload({"violations": []}, messages)
        if len(calls) == 1:
            payload["player_authority"]["c0"] = [{"quote": "请工作人员核对。", "authorization_ref": "p0"}]
            payload["bound_completion_approved_indexes"] = [0]
        payload["author_checks"] = {str(row["rule_index"]): "clear" for row in data["author_checks_required"]}
        payload["author_checks"]["0"] = "uncertain"
        return payload

    calls = _install(monkeypatch, respond)
    sink, diagnostics = [], {}
    problems = await _review(engine, session, body, _bound(engine, session, body), sink, diagnostics=diagnostics)
    assert problems == () and diagnostics["unresolved_author_rules"] == []
    assert diagnostics["author_coverage_complete"] is False
    assert diagnostics["review_warnings"] == [{"check": "author_boundary", "rule_index": 0}]
    assert sink == [(0,)] and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("grounded", [False, True])
@pytest.mark.parametrize("approved", [[], [0]])
async def test_semantic_warnings_do_not_add_confirmation_to_grounded_findings(monkeypatch, grounded, approved):
    engine, session = _frame()
    body = _body()
    pending = ["c2"] if grounded else ["c0", "c2"]
    observed = []

    def respond(messages, data, kwargs):
        assert "player_authority_repair" not in data
        if "finding" in data or "findings" in data:
            findings = data.get("findings", [data.get("finding")])
            observed.extend(findings)
            assert [finding["candidate"]["id"] for finding in findings] == ["c1"]
            assert all(finding["quote"] == finding["candidate"]["text"] for finding in findings)
            return _confirmed(data)
        payload = contract_audit_payload({"violations": []}, messages)
        for ref in pending:
            payload["player_authority"][ref] = "uncertain"
        if grounded:
            action = next(row for row in data["sources"] if row["id"] == "c1")
            payload["player_authority"]["c1"] = [{"quote": action["text"], "authorization_ref": ""}]
        payload["bound_completion_approved_indexes"] = approved
        return payload

    calls = _install(monkeypatch, respond)
    sink, diagnostics = [], {}
    assert await _review(engine, session, body, _bound(engine, session, body), sink, diagnostics=diagnostics) == ()
    assert len(observed) == int(grounded) and len(calls) == (2 if grounded else 1)
    assert sink == [tuple(approved)]
    assert diagnostics["player_authority_complete"] is False
    assert all(diagnostics["player_authority_blocks"][ref] == "uncertain" for ref in pending)
    assert {row["candidate_ref"] for row in diagnostics["review_warnings"]} == set(pending)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["present", "uncertain", "missing_scope", "missing_verdict", "timeout"])
async def test_concrete_player_finding_keeps_protocol_failures_separate_from_uncertainty(monkeypatch, bad):
    engine, session = _frame()
    body = _body()

    def respond(messages, data, kwargs):
        if "finding" in data or "findings" in data:
            if bad == "timeout":
                raise TimeoutError
            if bad == "missing_verdict":
                return {"verdicts": []}

            def change(finding, row):
                if finding["candidate"]["id"] != "c2":
                    return
                if bad in {"present", "uncertain"}: row["player_action_scope"] = bad
                elif bad == "missing_scope": row.pop("player_action_scope")
            return _confirmed(data, change=change)
        payload = contract_audit_payload({"violations": []}, messages)
        payload["player_authority"]["c0"] = "uncertain"
        current = next(row for row in data["sources"] if row["id"] == "c2")
        payload["player_authority"]["c2"] = [{"quote": current["text"], "authorization_ref": ""}]
        payload["bound_completion_approved_indexes"] = [0]
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    if bad == "timeout":
        with pytest.raises(ev.NumericV2EvaluatorError, match="timeout"):
            await _review(engine, session, body, _bound(engine, session, body), sink)
    else:
        problems = await _review(engine, session, body, _bound(engine, session, body), sink)
        assert (ev._PLAYER_AUTHORITY_RULE in problems) is (bad != "uncertain")
    assert sink == ([(0,)] if bad == "uncertain" else []) and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", ["omitted", "player_input", "other_candidate", "partial_candidate"])
async def test_confirmed_absence_scope_binds_its_own_whole_block_without_redundant_proof(monkeypatch, proof):
    engine, session = _frame()
    body = _body()
    observed = []

    def respond(messages, data, kwargs):
        if "finding" in data or "findings" in data:
            def change(finding, row):
                if finding["candidate"]["id"] != "c2":
                    return
                observed.append(finding["candidate"])
                if proof == "omitted": row["clearance"] = []
                elif proof == "player_input": row["clearance"] = ["p0"]
                elif proof == "other_candidate": row["clearance"] = ["c0"]
                else: row["clearance"] = [{"source_ref": "c2", "quote": finding["quote"][:2]}]
            return _confirmed(data, change=change)
        payload = contract_audit_payload({"violations": []}, messages)
        payload["player_authority"]["c0"] = "uncertain"
        current = next(row for row in data["sources"] if row["id"] == "c2")
        payload["player_authority"]["c2"] = [{"quote": current["text"], "authorization_ref": ""}]
        payload["bound_completion_approved_indexes"] = [0]
        return payload

    calls = _install(monkeypatch, respond)
    sink, diagnostics = [], {}
    assert await _review(engine, session, body, _bound(engine, session, body), sink, diagnostics=diagnostics) == ()
    effective = next(row for row in diagnostics["confirmation_verdicts"] if row["candidate_ref"] == "c2")
    assert effective["clearance"] == [{"source_ref": "c2", "quote": observed[0]["text"]}]
    assert diagnostics["player_authority_complete"] is False
    assert diagnostics["player_authority_blocks"]["c0"] == "uncertain"
    assert sink == [(0,)] and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["wrong_quote", "unknown_block"])
async def test_pending_authority_does_not_expand_protocol_repair_to_bad_refs(monkeypatch, invalid):
    engine, session = _frame()
    body = _body()

    def respond(messages, data, kwargs):
        payload = contract_audit_payload({"violations": []}, messages)
        payload["player_authority"]["c2"] = "uncertain"
        if invalid == "wrong_quote":
            payload["player_authority"]["c0"] = [{"quote": "请工作人员核对。", "authorization_ref": "p0"}]
        else:
            payload["player_authority"]["c999"] = "uncertain"
        payload["bound_completion_approved_indexes"] = [0]
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    with pytest.raises(ev.NumericV2EvaluatorOutputError, match="player_authority_invalid"):
        await _review(engine, session, body, _bound(engine, session, body), sink)
    assert len(calls) == 1 and not sink


@pytest.mark.asyncio
async def test_authority_warning_preserves_missing_fixed_original_cause(monkeypatch):
    engine, session = _frame()
    body = _body()
    body["fixed_narrations"] = [{"position": "after", "text": "柜台灯亮了。"}]

    def respond(messages, data, kwargs):
        if "finding" in data or "findings" in data:
            return _confirmed(data)
        payload = contract_audit_payload({"violations": []}, messages)
        payload["player_authority"]["c2"] = "uncertain"
        payload["fixed_original_cause"] = {}
        payload["bound_completion_approved_indexes"] = [0]
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    problems = await _review(engine, session, body, _bound(engine, session, body), sink)
    assert ev._FIXED_ORIGINAL_CAUSE_RULE in problems and ev._PLAYER_AUTHORITY_RULE not in problems
    assert not sink and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("author", ["uncertain", "violation"])
async def test_authority_warning_does_not_clear_a_confirmed_author_violation(monkeypatch, author):
    engine, session = _frame()
    body = _body()

    def respond(messages, data, kwargs):
        if "finding" in data or "findings" in data:
            def change(finding, row):
                if finding["rule"] != ev._PLAYER_AUTHORITY_RULE:
                    row.update(verdict="violation", clearance=[])
            return _confirmed(data, change=change)
        payload = contract_audit_payload({"violations": []}, messages)
        payload["player_authority"]["c2"] = "uncertain"
        payload["author_checks"] = {str(row["rule_index"]): "clear" for row in data["author_checks_required"]}
        payload["author_checks"]["0"] = ("uncertain" if author == "uncertain" else {
            "candidate_ref": "c0", "quote": body["scene_narration"], "prior_ref": ""})
        payload["bound_completion_approved_indexes"] = [0]
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    problems = await _review(engine, session, body, _bound(engine, session, body), sink)
    assert bool(problems) is (author == "violation") and ev._PLAYER_AUTHORITY_RULE not in problems
    assert sink == ([(0,)] if author == "uncertain" else [])
    assert len(calls) == (1 if author == "uncertain" else 2)
