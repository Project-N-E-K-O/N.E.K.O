"""Semantic uncertainty warns without inventing a violation, permission, or fact."""

from copy import deepcopy

from jsonschema import Draft202012Validator
import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_runtime import NumericV2Engine
from services.theater.numeric_v2_structured_output import contract_output_schema
from tests.unit.test_theater_numeric_v2_bound_completion_contract import _bound, _install, _review
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_fact_discovery import _body, _frame, _items, _packet, OTHER
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.utils.theater_prompt import contract_audit_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("check", ["author_boundary", "player_authority"])
@pytest.mark.parametrize("explicit_approval", [False, True])
async def test_complete_uncertainty_delivers_once_without_inventing_fact_approval(monkeypatch, caplog, check, explicit_approval):
    engine, session = _frame(two=True)
    body = _body()
    packet = _packet(engine, session, body)
    before = deepcopy(session.to_dict())

    def respond(messages, data, kwargs):
        payload = contract_audit_payload({"violations": []}, messages)
        if check == "author_boundary":
            payload["author_checks"]["uncertain"] = payload["author_checks"]["clear"]
            payload["author_checks"]["clear"] = []
        else:
            payload["player_authority"]["c0"] = "uncertain"
        if explicit_approval:
            payload["bound_completion_approved_indexes"] = [0]
        payload["fact_discoveries"] = _items(packet, keys=(OTHER,))
        return payload

    calls = _install(monkeypatch, respond)
    approved, discovered, diagnostics = [], [], {}
    assert await _review(engine, session, body, _bound(engine, session, body), approved,
        diagnostics=diagnostics, discovery_packet=packet, discovery_sink=discovered) == ()
    assert len(calls) == 1
    assert approved == ([(0,)] if explicit_approval else [])
    assert len(discovered) == 1 and discovered[0].claims[0]["key"] == OTHER
    assert session.to_dict() == before
    assert diagnostics["review_warnings"][0]["check"] == check
    assert diagnostics["author_coverage_complete" if check == "author_boundary" else "player_authority_complete"] is False
    assert "remained uncertain" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["uncertain", "violation"])
@pytest.mark.parametrize("proof", [[], ["unknown-source"]])
async def test_only_valid_explicit_uncertainty_is_a_delivery_warning(monkeypatch, verdict, proof):
    engine, session = _frame()
    body = _body()

    def respond(messages, data, kwargs):
        if "sources" in data:
            return contract_audit_payload({"violations": [{"rule_index": 0,
                "candidate_ref": "c0", "quote": body["scene_narration"], "prior_ref": ""}]}, messages)
        assert "不是本条指控的玩家权限问题时填uncertain" not in messages[0].content
        assert "只有玩家权限指控另填player_action_scope，其他指控省略" in messages[0].content
        assert "player_action_scope" not in kwargs["response_format"]["json_schema"]["schema"]["properties"]
        return {"verdict": verdict, "reason": "原文仍不足以确认这项指控。", "clearance": proof}

    calls = _install(monkeypatch, respond)
    diagnostics = {}
    problems = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=engine.nodes["start"], session=session, actor_performance=body,
        player_input="请工作人员核对。", diagnostics=diagnostics)
    soft = verdict == "uncertain" and not proof
    assert bool(problems) is not soft
    assert bool(diagnostics.get("review_warnings")) is soft
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_another_supplied_committed_original_can_refute_the_first_prior(monkeypatch):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="other-prior", catgirl_binding=_binding(), opening_performance={
        "scene_narration": "管理员站在柜边，柜门仍然关闭。",
        "performance": "管理员已经明确许可角色打开柜门查看。", "suggested_inputs": []})
    seen = []

    def respond(messages, data, kwargs):
        if "sources" in data:
            seen.extend(row for row in data["sources"] if row["id"].startswith("h"))
            return contract_audit_payload({"violations": [{"rule_index": 0, "candidate_ref": "c0",
                "quote": "打开柜门", "prior_ref": "h0"}]}, messages)
        assert data["finding"]["prior"]["id"] == "h0"
        assert next(row for row in data["clearance_sources"] if row["id"] == "h1")["text"] == seen[1]["text"]
        return {"verdict": "clear", "reason": "已提交原文包含管理员的明确许可。", "clearance": ["h1"]}

    calls = _install(monkeypatch, respond)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": ["未经管理员许可不得打开柜门。"]}}, session=session,
        actor_performance={"performance": "（打开柜门）里面是空的。"}, player_input="看看情况。") == ()
    assert len(calls) == 2


def test_mixed_confirmation_schema_allows_nonplayer_verdicts_without_fake_scope():
    plain = {"verdict": "uncertain", "reason": "原文未证实违规。", "clearance": []}
    schema = contract_output_schema(grounded=True, confirmation=True, confirmation_many=True,
                                    check_player_authority=True)
    Draft202012Validator(schema).validate({"verdicts": [plain, {**plain, "player_action_scope": "none"}]})
    for variant in schema["properties"]["verdicts"]["items"]["anyOf"]:
        assert set(variant["required"]) == set(variant["properties"])
        assert variant["additionalProperties"] is False
    assert not Draft202012Validator(schema).is_valid({"verdicts": [{**plain, "player_action_scope": "invented"}]})


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
async def test_player_confirmation_cannot_hide_missing_scope_behind_uncertainty(monkeypatch, mixed):
    engine, session = _frame()
    body = _body()

    def respond(messages, data, kwargs):
        if "sources" in data:
            payload = contract_audit_payload({"violations": [{"rule_index": 0, "candidate_ref": "c0",
                "quote": body["scene_narration"], "prior_ref": ""}] if mixed else []}, messages)
            payload["player_authority"]["c0"] = [{"quote": body["scene_narration"], "authorization_ref": ""}]
            return payload
        verdict = {"verdict": "uncertain", "reason": "该完整块仍需核对。", "clearance": []}
        return {"verdicts": [verdict for _ in data["findings"]]} if mixed else verdict

    calls = _install(monkeypatch, respond)
    diagnostics = {}
    problems = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=engine.nodes["start"], session=session, actor_performance=body,
        player_input="我只等候。", diagnostics=diagnostics)
    assert ev._PLAYER_AUTHORITY_RULE in problems
    assert all(row["check"] != "confirmation" or row["rule_index"] != next(
        finding["rule_index"] for finding in diagnostics["findings"] if finding["rule"] == ev._PLAYER_AUTHORITY_RULE)
        for row in diagnostics.get("review_warnings", []))
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("report", ["uncertain", "violation", "missing"])
async def test_fixed_original_cause_distinguishes_uncertainty_from_grounded_conflicts(monkeypatch, report):
    engine, session = _frame()
    body = _body()
    body["fixed_narrations"] = [{"position": "after", "text": "录音传来馆长的声音。"}]

    def respond(messages, data, kwargs):
        if "sources" not in data:
            return {"verdict": "violation", "reason": "原文交付之前确实没有录音启动前因。", "clearance": []}
        payload = contract_audit_payload({"violations": []}, messages)
        fixed = next(row for row in data["sources"] if row.get("program_fixed_original"))
        payload["fixed_original_cause"] = ({fixed["id"]: report} if report == "uncertain" else
            {fixed["id"]: {"quote": fixed["text"], "prior_ref": ""}} if report == "violation" else {})
        return payload

    calls = _install(monkeypatch, respond)
    diagnostics = {}
    problems = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=engine.nodes["start"], session=session, actor_performance=body,
        player_input="先在柜台旁等候。", diagnostics=diagnostics)
    assert bool(problems) is (report != "uncertain")
    assert diagnostics["fixed_original_cause_complete"] is (report == "violation")
    assert len(calls) == (2 if report == "violation" else 1)
    if report == "uncertain":
        assert diagnostics["review_warnings"] == [{"check": "fixed_original_cause", "candidate_ref": next(
            ref for ref in diagnostics["fixed_original_cause"])}]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_verdict", ["clear", "violation", "uncertain"])
@pytest.mark.parametrize("proof", [[], ["unknown-source"], [{"source_ref": "c0", "quote": "伪造原文"}]])
async def test_uncertain_player_scope_cannot_override_violation_or_accept_forged_proofs(monkeypatch, raw_verdict, proof):
    engine, session = _frame()
    body = _body()
    before = deepcopy(session.to_dict())

    def respond(messages, data, kwargs):
        if "sources" in data:
            payload = contract_audit_payload({"violations": []}, messages)
            payload["player_authority"]["c0"] = [{"quote": body["scene_narration"], "authorization_ref": ""}]
            return payload
        return {"verdict": raw_verdict, "reason": "整块的动作主体仍无法确认。", "clearance": proof,
                "player_action_scope": "uncertain"}

    calls = _install(monkeypatch, respond)
    diagnostics = {}
    problems = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=engine.nodes["start"], session=session, actor_performance=body,
        player_input="我只等候。", diagnostics=diagnostics)
    warning = raw_verdict != "violation" and not proof
    assert bool(problems) is not warning
    assert bool(diagnostics.get("review_warnings")) is warning
    assert session.to_dict() == before and len(calls) == 2
    if warning:
        assert diagnostics["confirmation_verdicts"][0]["verdict"] == "uncertain"
    elif raw_verdict == "violation":
        assert diagnostics["confirmation_verdicts"][0]["verdict"] == "violation"


@pytest.mark.asyncio
async def test_confirmed_unauthorized_player_action_stays_blocked_when_scope_is_uncertain(monkeypatch):
    engine, session = _frame()
    body = {"scene_narration": "你替工作人员签下长期维护合同。", "performance": ""}
    before = deepcopy(session.to_dict())

    def respond(messages, data, kwargs):
        if "sources" in data:
            payload = contract_audit_payload({"violations": []}, messages)
            payload["player_authority"]["c0"] = [{"quote": body["scene_narration"], "authorization_ref": ""}]
            return payload
        return {"verdict": "violation", "reason": "玩家只授权等候，没有授权签订合同。", "clearance": [],
                "player_action_scope": "uncertain"}

    calls = _install(monkeypatch, respond)
    diagnostics = {}
    problems = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=engine.nodes["start"], session=session, actor_performance=body,
        player_input="我只在旁边等候。", diagnostics=diagnostics)
    assert ev._PLAYER_AUTHORITY_RULE in problems
    assert diagnostics["confirmation_verdicts"][0]["verdict"] == "violation"
    assert not diagnostics.get("review_warnings")
    assert len(calls) == 2 and session.to_dict() == before
