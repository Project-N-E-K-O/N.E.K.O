"""One bad audit can use its confirmation slot without rewriting the Actor."""

import pytest

from services.theater import numeric_v2_evaluator as ev
from tests.unit.test_theater_numeric_v2_bound_completion_contract import _bound, _install, _review
from tests.unit.test_theater_numeric_v2_fact_discovery import _body, _frame, _packet, _items, OTHER
from tests.utils.theater_prompt import contract_audit_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


def _audit(messages, data, *, defect=None, scope=False):
    payload = contract_audit_payload({"violations": []}, messages)
    blocks = [row for row in data["sources"] if row["id"].startswith("c")]
    if scope:
        payload["departure_scope"] = [{"candidate_ref": row["id"], "quote": row["text"],
            "scope": "current_scene"} for row in blocks]
    if defect in {"author", "mixed"}:
        payload["author_checks"] = {str(row["rule_index"]): {
            "candidate_ref": "c0", "quote": "审核者另写的无来源引文", "prior_ref": "c0"}
            for row in data["author_checks_required"]}
    if defect in {"departure", "mixed"}:
        payload["departure_scope"][0]["quote"] = "同样没有出现在任何候选块中的句子"
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["author", "departure", "mixed"])
@pytest.mark.parametrize("approved", [[], [0]])
async def test_bad_known_quotes_repair_same_audit_and_freeze_fact_approval(monkeypatch, defect, approved):
    engine, session = _frame()
    body, calls = _body(), []
    scope = defect != "author"

    def respond(messages, data, kwargs):
        payload = _audit(messages, data, defect=defect if len(calls) == 1 else None, scope=scope)
        if len(calls) == 1:
            payload["bound_completion_approved_indexes"] = approved
        else:
            assert "审核报告" in messages[0].content
            assert not {"bound_completion_check", "completion_check"} & data.keys()
            assert not {"bound_completion_approved_indexes", "fact_discoveries"} & kwargs["response_format"]["json_schema"]["schema"]["properties"].keys()
        return payload

    calls = _install(monkeypatch, respond)
    sink, diagnostics = [], {}
    kwargs = dict(departure_attempt_observed=True, departure_blocker="completion_pending",
        exit_mismatch_reason="当前仍留柜台。") if scope else {}
    assert await _review(engine, session, body, _bound(engine, session, body), sink, diagnostics=diagnostics, **kwargs) == ()
    assert len(calls) == 2 and sink == [tuple(approved)]
    assert diagnostics["contract_protocol_repaired"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("second", ["violation", "uncertain", "player_uncertain", "bad_quote", "extra_facts", "json", "timeout"])
async def test_second_audit_keeps_protocol_errors_and_uncertainty_distinct_without_third_call(monkeypatch, second):
    engine, session = _frame()
    body, calls = _body(), []

    def respond(messages, data, kwargs):
        if len(calls) == 1:
            payload = _audit(messages, data, defect="author")
            payload["bound_completion_approved_indexes"] = [0]
            return payload
        if second == "timeout": raise TimeoutError
        if second == "json": return "{"  # A JSON string is not a valid audit object either.
        payload = _audit(messages, data, defect="author" if second == "bad_quote" else None)
        if second == "violation":
            payload["author_checks"] = {str(row["rule_index"]): "clear" for row in data["author_checks_required"]}
            payload["author_checks"]["0"] = {"candidate_ref": "c0", "quote": body["scene_narration"], "prior_ref": ""}
        elif second == "uncertain":
            payload["author_checks"] = {str(row["rule_index"]): "uncertain" for row in data["author_checks_required"]}
        elif second == "player_uncertain": payload["player_authority"]["c0"] = "uncertain"
        elif second == "extra_facts": payload["bound_completion_approved_indexes"] = []
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    if second in {"violation", "uncertain", "player_uncertain"}:
        problems = await _review(engine, session, body, _bound(engine, session, body), sink)
        assert bool(problems) is (second == "violation")
    else:
        with pytest.raises(ev.NumericV2EvaluatorError):
            await _review(engine, session, body, _bound(engine, session, body), sink)
    assert len(calls) == 2 and sink == ([(0,)] if second in {"uncertain", "player_uncertain"} else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("second", ["violation", "uncertain"])
async def test_player_only_repair_warns_or_rejects_without_third_confirmation(monkeypatch, second):
    engine, session = _frame()
    body, calls = _body(), []

    def respond(messages, data, kwargs):
        if "player_authority_repair" in data:
            assert len(calls) == 2
            return {"player_authority": {"c0": "uncertain" if second == "uncertain" else [
                {"quote": body["scene_narration"], "authorization_ref": ""}]}}
        payload = _audit(messages, data)
        payload["player_authority"]["c0"] = [{"quote": "请工作人员核对。", "authorization_ref": "p0"}]
        payload["bound_completion_approved_indexes"] = [0]
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    problems = await _review(engine, session, body, _bound(engine, session, body), sink)
    assert (ev._PLAYER_AUTHORITY_RULE in problems) is (second == "violation")
    assert len(calls) == 2 and sink == ([(0,)] if second == "uncertain" else [])


@pytest.mark.asyncio
async def test_repair_cannot_reopen_rejected_fact_or_resample_discovery(monkeypatch):
    engine, session = _frame(two=True)
    body, calls = _body(), []
    packet = _packet(engine, session, body)

    def respond(messages, data, kwargs):
        payload = _audit(messages, data, defect="author" if len(calls) == 1 else None)
        if len(calls) == 1:
            payload.update(bound_completion_approved_indexes=[], fact_discoveries=_items(packet, keys=(OTHER,)))
        else:
            assert not {"completion_check", "bound_completion_check"} & data.keys()
        return payload

    calls = _install(monkeypatch, respond)
    sink, discovered = [], []
    assert await _review(engine, session, body, _bound(engine, session, body), sink,
        discovery_packet=packet, discovery_sink=discovered) == ()
    assert len(calls) == 2 and sink == [()]
    assert [claim["key"] for claim in discovered[0].claims] == [OTHER]


@pytest.mark.asyncio
async def test_repair_cannot_replace_first_rejection_with_a_new_fact_approval(monkeypatch):
    engine, session = _frame()
    body, calls = _body(), []

    def respond(messages, data, kwargs):
        payload = _audit(messages, data, defect="author" if len(calls) == 1 else None)
        payload["bound_completion_approved_indexes"] = [] if len(calls) == 1 else [0]
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    with pytest.raises(ev.NumericV2EvaluatorOutputError):
        await _review(engine, session, body, _bound(engine, session, body), sink)
    assert len(calls) == 2 and not sink


@pytest.mark.asyncio
async def test_repair_retains_existing_input_budget_without_an_extra_call(monkeypatch):
    engine, session = _frame()
    body, calls = _body(), []
    real_count = ev.count_tokens
    monkeypatch.setattr(ev, "count_tokens", lambda text: 10001 if "上一份审核报告的引文" in text else real_count(text))

    def respond(messages, data, kwargs):
        payload = _audit(messages, data, defect="author")
        payload["bound_completion_approved_indexes"] = [0]
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    with pytest.raises(ev.NumericV2EvaluatorError, match="input_over_budget"):
        await _review(engine, session, body, _bound(engine, session, body), sink)
    assert len(calls) == 1 and not sink


@pytest.mark.asyncio
async def test_a_grounded_first_finding_cannot_be_resampled_as_an_audit_repair(monkeypatch):
    engine, session = _frame()
    body = _body("两人已经进入候车厅。")

    def respond(messages, data, kwargs):
        payload = _audit(messages, data)
        payload["author_checks"] = {str(row["rule_index"]): "clear" for row in data["author_checks_required"]}
        payload["author_checks"]["0"] = {"candidate_ref": "c0", "quote": body["scene_narration"], "prior_ref": ""}
        payload["player_authority"]["c1"] = [{"quote": "请工作人员核对。", "authorization_ref": "p0"}]
        payload["bound_completion_approved_indexes"] = [0]
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    with pytest.raises(ev.NumericV2EvaluatorOutputError):
        await _review(engine, session, body, _bound(engine, session, body), sink)
    assert len(calls) == 1 and not sink
