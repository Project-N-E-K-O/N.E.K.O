"""Goal evidence and rejection diagnostics never replace independent approval."""

import json

from jsonschema import Draft202012Validator
import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_fact_discovery as facts
from services.theater.numeric_v2_structured_output import contract_output_schema
from tests.unit.test_theater_numeric_v2_bound_completion_contract import _bound, _install, _review
from tests.unit.test_theater_numeric_v2_fact_discovery import _body, _frame
from tests.utils.theater_prompt import contract_audit_payload
from utils.tokenize import count_tokens


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,description", [
    ("agreement", "双方已同意由角色明天展示资料；不要求今天展示。"),
    ("delivery", "角色已完整展示全部资料。"),
    ("feedback", "玩家已给出自己看完资料后的理解和反馈。"),
])
async def test_prebound_request_uses_goal_specific_proof_without_promoting_promises(monkeypatch, kind, description):
    engine, session = _frame()
    key = engine.nodes["start"]["completion_contract"]["all"][0]["key"]
    engine.fact_contract[key]["description"] = description
    body = _body("资料尚未展示。")
    body["performance"] = "我同意明天展示，今天尚未开始。"

    def respond(messages, data, kwargs):
        system = messages[0].content
        assert "目标只要求提出条款或形成约定" in system
        assert "目标要求实际交付" in system
        assert "主观理解或反馈" in system
        assert "有任一必要条件尚缺、仅被请求/承诺" not in system
        check = data["bound_completion_check"]
        assert check["claims"][0]["description"] == description
        assert any(row["source"] == "player_input" for row in check["claims"][0]["evidence"]) is False
        payload = contract_audit_payload({"violations": []}, messages)
        # The provider explicitly rejects: a promise or diagnostic must never
        # make the framework infer the missing second party, delivery or feedback.
        payload["bound_completion_approved_indexes"] = []
        payload["bound_completion_rejections"] = [{"index": 0, "reason": {
            "agreement": "player_agreement_missing", "delivery": "delivery_missing", "feedback": "player_feedback_missing",
        }[kind], "refs": ["delivery_source"]}]
        return payload

    _install(monkeypatch, respond)
    sink, diagnostics = [], {}
    assert await _review(engine, session, body, _bound(engine, session, body), sink, diagnostics=diagnostics) == ()
    assert sink == [()] and diagnostics["bound_completion_diagnostics_valid"] is True


@pytest.mark.parametrize("rule", [facts.DISCOVERY_RULE, facts.APPROVAL_RULE, ev._COMPLETION_EVIDENCE_RULE])
def test_evidence_rules_do_not_apply_execution_requirements_to_agreement(rule):
    assert "目标" in rule and "约定" in rule
    assert "有任一必要条件尚缺、仅被请求/承诺" not in rule
    assert "只预告、答应或开始交付也不够" not in rule


@pytest.mark.asyncio
@pytest.mark.parametrize("diagnostic", [
    [{"index": 0, "reason": "role_agreement_missing", "refs": ["delivery_source"]}],
    None, [], [{"index": 0, "reason": "supported", "refs": ["delivery_source"]}],
    [{"index": 0, "reason": "role_agreement_missing", "refs": ["not-issued"]}],
    [{"index": True, "reason": "role_agreement_missing", "refs": []}],
    [{"index": 0, "reason": "role_agreement_missing", "refs": []}] * 2,
    [{"index": 0, "reason": "role_agreement_missing", "refs": [], "approved": True}],
])
async def test_bad_diagnostics_do_not_resample_an_explicit_rejection(monkeypatch, diagnostic):
    engine, session = _frame()
    body = _body()

    def respond(messages, data, kwargs):
        payload = contract_audit_payload({"violations": []}, messages)
        payload["bound_completion_approved_indexes"] = []
        if diagnostic is not None: payload["bound_completion_rejections"] = diagnostic
        return payload

    calls = _install(monkeypatch, respond)
    sink, diagnostics = [], {}
    assert await _review(engine, session, body, _bound(engine, session, body), sink, diagnostics=diagnostics) == ()
    assert sink == [()] and len(calls) == 1
    assert diagnostics["bound_completion_diagnostics_valid"] is (diagnostic == [
        {"index": 0, "reason": "role_agreement_missing", "refs": ["delivery_source"]}])


@pytest.mark.asyncio
@pytest.mark.parametrize("indexes", [None, [True], [99], [0]])
async def test_only_approved_indexes_can_grant_a_fact_not_diagnostic_status(monkeypatch, indexes):
    engine, session = _frame()
    body = _body()

    def respond(messages, data, kwargs):
        payload = contract_audit_payload({"violations": []}, messages)
        if indexes is not None: payload["bound_completion_approved_indexes"] = indexes
        payload["bound_completion_rejections"] = [{"index": 0, "reason": "uncertain", "refs": []}]
        return payload

    _install(monkeypatch, respond)
    sink, diagnostics = [], {}
    assert await _review(engine, session, body, _bound(engine, session, body), sink, diagnostics=diagnostics) == ()
    assert sink == ([(0,)] if indexes == [0] and type(indexes[0]) is int else [])
    assert diagnostics["bound_completion_diagnostics_valid"] is False


def test_diagnostic_schema_uses_issued_indexes_refs_and_no_positive_reason():
    schema = contract_output_schema(grounded=True, bound_completion_count=1,
                                    bound_completion_refs=["e0", "delivery_source"])
    field = schema["properties"]["bound_completion_rejections"]
    assert "bound_completion_rejections" in schema["required"]
    item = field["items"]["properties"]
    assert item["index"]["enum"] == [0]
    assert item["refs"]["items"]["enum"] == ["e0", "delivery_source"]
    assert "supported" not in item["reason"]["enum"]


@pytest.mark.asyncio
async def test_full_approval_with_empty_rejections_matches_the_requested_schema(monkeypatch):
    engine, session = _frame()
    body = _body()

    def respond(messages, data, kwargs):
        payload = contract_audit_payload({"violations": []}, messages)
        payload["bound_completion_approved_indexes"] = [0]
        payload["bound_completion_rejections"] = []
        payload["author_checks"] = {str(row["rule_index"]): "clear" for row in data["author_checks_required"]}
        assert not list(Draft202012Validator(kwargs["response_format"]["json_schema"]["schema"]).iter_errors(payload))
        return payload

    _install(monkeypatch, respond)
    sink, diagnostics = [], {}
    assert await _review(engine, session, body, _bound(engine, session, body), sink, diagnostics=diagnostics) == ()
    assert sink == [(0,)] and diagnostics["bound_completion_diagnostics_valid"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("borrow", [False, True])
async def test_rejection_diagnostic_cannot_borrow_another_claim_original(monkeypatch, borrow):
    engine, session = _frame(two=True)
    for definition in engine.fact_contract.values():
        definition.pop("evidence_source", None)
    body = _body()
    from tests.unit.test_theater_numeric_v2_fact_discovery import _packet, _items, KEY, OTHER
    packet = _packet(engine, session, body)
    proposed = facts.parse_discoveries(_items(packet, keys=(KEY, OTHER)), packet)
    player = next(row for row in packet["sources"] if row["kind"] == "current_player")
    batch = facts.bind_source_delivery(engine, (
        {**proposed.claims[0], "evidence": []},
        {**proposed.claims[1], "evidence": [{"source": "player_input", "quote": player["text"]}]},
    ), packet)
    batch.validate()
    unrelated = next(row["ref"] for row in batch.packet["sources"] if row["kind"] == "current_player")
    assert unrelated not in batch.bindings[0] and unrelated in batch.bindings[1]

    def respond(messages, data, kwargs):
        payload = contract_audit_payload({"violations": []}, messages)
        payload["bound_completion_approved_indexes"] = []
        schema = kwargs["response_format"]["json_schema"]["schema"]
        assert unrelated in schema["properties"]["bound_completion_rejections"]["items"]["properties"]["refs"]["items"]["enum"]
        payload["bound_completion_rejections"] = [
            {"index": 0, "reason": "role_agreement_missing", "refs": [unrelated if borrow else "delivery_source"]},
            {"index": 1, "reason": "role_agreement_missing", "refs": ["delivery_source"]},
        ]
        return payload

    _install(monkeypatch, respond)
    sink, diagnostics = [], {}
    assert await _review(engine, session, body, batch, sink, diagnostics=diagnostics) == ()
    assert sink == [()] and diagnostics["bound_completion_diagnostics_valid"] is (not borrow)


@pytest.mark.asyncio
async def test_body_audit_distinguishes_speaker_action_subject_and_actual_execution(monkeypatch):
    engine, session = _frame()
    body = _body("")
    body["performance"] = "（点头，将手搭在资料封面）好，我之后打开给你看。"

    def respond(messages, data, kwargs):
        assert data["sources"][0]["default_actor_id"] == "active_catgirl"
        assert "先确定实际主体" in messages[0].content
        assert "发言本身" in messages[0].content and "物理" in messages[0].content
        assert "c中在场角色当轮开口是现场交付；" not in messages[0].content
        return contract_audit_payload({"violations": []}, messages)

    _install(monkeypatch, respond)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=engine.nodes["start"], session=session, actor_performance=body, player_input="请你展示。") == ()


def test_clear_contract_with_bounded_rejection_diagnostics_fits_existing_720_output_budget():
    payload = {"player_authority": {f"c{i}": "none" for i in range(16)},
        "author_checks": {str(i): "clear" for i in range(40)}, "violations": [],
        "fact_discoveries": [{"target_index": i, "refs": ["e0"]} for i in range(4)],
        "bound_completion_approved_indexes": [], "bound_completion_rejections": [
            {"index": i, "reason": "delivery_missing", "refs": ["delivery_source"]} for i in range(4)]}
    assert count_tokens(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) <= 720
