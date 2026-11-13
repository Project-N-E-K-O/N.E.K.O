"""Bad audit quotations consume one audit repair, never an Actor rewrite."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_evaluator as ev
from tests.unit.test_theater_numeric_v2_bound_completion_contract import _bound, _install, _review
from tests.unit.test_theater_numeric_v2_fact_discovery import _body, _frame, _items, _packet, OTHER
from tests.utils.theater_prompt import contract_audit_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [[], [0]])
async def test_wrong_source_quote_repairs_only_bad_block_and_freezes_fact_verdict(monkeypatch, approved):
    engine, session = _frame()
    body = _body()
    first = None

    def respond(messages, data, kwargs):
        nonlocal first
        if "player_authority_repair" in data:
            assert data["player_authority_repair"]["candidate_refs"] == ["c0"]
            assert not {"completion_check", "bound_completion_check"} & data.keys()
            schema = kwargs["response_format"]["json_schema"]["schema"]
            assert set(schema["properties"]) == {"player_authority"}
            assert set(schema["properties"]["player_authority"]["properties"]) == {"c0"}
            assert data["player_authority_repair"] == {"candidate_refs": ["c0"]}
            return {"player_authority": {"c0": "none"}}
        first = contract_audit_payload({"violations": []}, messages)
        first["player_authority"]["c0"] = [{"quote": "请工作人员核对。", "authorization_ref": "p0"}]
        first["bound_completion_approved_indexes"] = approved
        return deepcopy(first)

    calls = _install(monkeypatch, respond)
    sink, diagnostics = [], {}
    assert await _review(engine, session, body, _bound(engine, session, body), sink, diagnostics=diagnostics) == ()
    assert sink == [tuple(approved)] and len(calls) == 2
    assert diagnostics["player_authority_protocol_repaired"] == ["c0"]
    assert diagnostics["player_authority_protocol_first_report"] == first["player_authority"]


@pytest.mark.asyncio
@pytest.mark.parametrize("repaired", [
    {}, {"c0": "uncertain"}, {"c0": []}, {"c0": "none", "c999": "none"},
    {"c0": [{"quote": "请工作人员核对。", "authorization_ref": "p0"}]},
    {"c0": [{"quote": "工作人员", "authorization_ref": ""}]},
])
async def test_invalid_or_semantic_nonclear_repair_never_publishes_facts(monkeypatch, repaired):
    engine, session = _frame()
    body = _body()

    def respond(messages, data, kwargs):
        if "player_authority_repair" in data:
            return {"player_authority": repaired}
        payload = contract_audit_payload({"violations": []}, messages)
        payload["player_authority"]["c0"] = [{"quote": "错引玩家原话", "authorization_ref": "p0"}]
        payload["bound_completion_approved_indexes"] = [0]
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    if repaired in ({"c0": "uncertain"}, {"c0": [{"quote": "工作人员", "authorization_ref": ""}]}):
        assert ev._PLAYER_AUTHORITY_RULE in await _review(engine, session, body, _bound(engine, session, body), sink)
    else:
        with pytest.raises(ev.NumericV2EvaluatorOutputError):
            await _review(engine, session, body, _bound(engine, session, body), sink)
    assert not sink and len(calls) == 2


@pytest.mark.asyncio
async def test_mixed_grounded_finding_and_protocol_error_fails_without_third_audit(monkeypatch):
    engine, session = _frame()
    body = _body()

    def respond(messages, data, kwargs):
        payload = contract_audit_payload({"violations": [{"rule_index": 0, "candidate_ref": "c0",
            "quote": body["scene_narration"], "prior_ref": ""}]}, messages)
        payload["player_authority"]["c0"] = [{"quote": "错引玩家原话", "authorization_ref": "p0"}]
        return payload

    calls = _install(monkeypatch, respond)
    with pytest.raises(ev.NumericV2EvaluatorOutputError):
        await _review(engine, session, body, None, [])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_grounded_unauthorized_player_action_still_uses_existing_confirmation(monkeypatch):
    engine, session = _frame()
    body = _body("玩家主动签下姓名。")

    def respond(messages, data, kwargs):
        assert "player_authority_repair" not in data
        if "finding" in data:
            return {"verdict": "violation", "reason": "没有玩家授权。", "clearance": []}
        payload = contract_audit_payload({"violations": []}, messages)
        payload["player_authority"]["c0"] = [{"quote": body["scene_narration"], "authorization_ref": ""}]
        return payload

    calls = _install(monkeypatch, respond)
    assert ev._PLAYER_AUTHORITY_RULE in await _review(engine, session, body, None, [])
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["missing", "empty", "unknown_authorization", "wrong_quote"])
async def test_only_invalid_block_is_reissued_and_valid_blocks_stay_frozen(monkeypatch, error):
    engine, session = _frame()
    body = _body()
    original_refs = []

    def respond(messages, data, kwargs):
        if "player_authority_repair" in data:
            assert data["player_authority_repair"]["candidate_refs"] == ["c0"]
            # Supplying a previously valid block would exceed the repair scope.
            return {"player_authority": {"c0": "none"}}
        payload = contract_audit_payload({"violations": []}, messages)
        original_refs.extend(payload["player_authority"])
        if error == "missing": payload["player_authority"].pop("c0")
        elif error == "empty": payload["player_authority"]["c0"] = []
        else: payload["player_authority"]["c0"] = [{"quote": body["scene_narration"] if error == "unknown_authorization" else "错引",
            "authorization_ref": "unissued" if error == "unknown_authorization" else "p0"}]
        return payload

    calls = _install(monkeypatch, respond)
    diagnostics = {}
    assert await _review(engine, session, body, None, [], diagnostics=diagnostics) == ()
    assert len(original_refs) > 1 and len(calls) == 2
    assert diagnostics["player_authority_blocks"] == {ref: "none" for ref in original_refs}


@pytest.mark.asyncio
@pytest.mark.parametrize("other", ["author", "fixed_cause", "semantic_uncertain"])
async def test_other_incomplete_check_prevents_protocol_resampling(monkeypatch, other):
    engine, session = _frame()
    body = _body()
    if other == "fixed_cause":
        body["fixed_narrations"] = [{"text": "录音响起。", "position": "before", "id": "original"}]

    def respond(messages, data, kwargs):
        payload = contract_audit_payload({"violations": []}, messages)
        payload["player_authority"]["c0"] = [{"quote": "错引", "authorization_ref": "p0"}]
        if other == "author": payload["author_checks"] = {}
        elif other == "fixed_cause": payload["fixed_original_cause"] = {}
        else: payload["player_authority"][list(payload["player_authority"])[-1]] = "uncertain"
        return payload

    calls = _install(monkeypatch, respond)
    with pytest.raises(ev.NumericV2EvaluatorOutputError):
        await _review(engine, session, body, None, [])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_repair_cannot_change_good_block_or_attach_fact_verdict(monkeypatch):
    engine, session = _frame()
    body = _body()

    def respond(messages, data, kwargs):
        if "player_authority_repair" in data:
            return {"player_authority": {"c0": "none"}, "bound_completion_approved_indexes": [0]}
        payload = contract_audit_payload({"violations": []}, messages)
        payload["player_authority"]["c0"] = [{"quote": "错引", "authorization_ref": "p0"}]
        payload["bound_completion_approved_indexes"] = []
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    with pytest.raises(ev.NumericV2EvaluatorOutputError):
        await _review(engine, session, body, _bound(engine, session, body), sink)
    assert not sink and len(calls) == 2


@pytest.mark.asyncio
async def test_c04_original_player_quote_never_substitutes_for_role_action(monkeypatch):
    engine, session = _frame()
    player = "请你拉开遮板展示。我只在标线外看，先不调照明，也不作核对结论。"
    body = {"performance": "（拉开遮板，侧身让出视线）复制铜扣有四个圆孔，边缘完整。你可以在标线外观看。"}

    def respond(messages, data, kwargs):
        if "player_authority_repair" in data:
            assert data["player_authority_repair"] == {"candidate_refs": ["c0"]}
            assert data["sources"][0]["text"] == "拉开遮板，侧身让出视线"
            assert "角色自身动作不需要玩家授权" in messages[0].content
            return {"player_authority": {"c0": "none"}}
        payload = contract_audit_payload({"violations": []}, messages)
        payload["player_authority"] = {
            "c0": [{"quote": "请你拉开遮板展示。", "authorization_ref": "p0"}], "c1": "none"}
        return payload

    calls = _install(monkeypatch, respond)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=engine.nodes["start"], session=session, actor_performance=body, player_input=player,
        include_all_segments=True, cast_scope={"player": ["周砚"], "active_catgirl": ["叶澄"]}) == ()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_protocol_repair_preserves_first_discovery_without_resampling_rejected_bound_claim(monkeypatch):
    engine, session = _frame(two=True)
    body = _body()
    packet = _packet(engine, session, body)

    def respond(messages, data, kwargs):
        if "player_authority_repair" in data:
            assert "fact_discoveries" not in kwargs["response_format"]["json_schema"]["schema"]["properties"]
            assert "completion_check" not in data and "bound_completion_check" not in data
            return {"player_authority": {"c0": "none"}}
        payload = contract_audit_payload({"violations": []}, messages)
        payload["player_authority"]["c0"] = [{"quote": "错引", "authorization_ref": "p0"}]
        payload.update(bound_completion_approved_indexes=[], fact_discoveries=_items(packet, keys=(OTHER,)))
        return payload

    calls = _install(monkeypatch, respond)
    sink, discovered = [], []
    assert await _review(engine, session, body, _bound(engine, session, body), sink,
        discovery_packet=packet, discovery_sink=discovered) == ()
    assert sink == [()] and [claim["key"] for claim in discovered[0].claims] == [OTHER] and len(calls) == 2


@pytest.mark.asyncio
async def test_confirmation_separates_rule_target_and_delivered_event(monkeypatch):
    engine, session = _frame()
    body = {"performance": "（点头）可以，我就按给你看。"}
    node = {"id": "start", "story_beat": {"must_not_happen": ["本幕只商量，不实际按键。"]}}

    def respond(messages, data, kwargs):
        if "finding" in data:
            assert "reason分别说明原rule禁止的交付类型和正文实际交付的动作、发言或状态" in messages[0].content
            assert "对白正在交付受限行为或宣告其发生也算执行" not in messages[0].content
            assert set(kwargs["response_format"]["json_schema"]["schema"]["properties"]) == {
                "verdict", "reason", "clearance", "player_action_scope"}
            return {"verdict": "clear", "reason": "禁的是实际按键，原句只表达同意和承诺。",
                    "clearance": ["c1"], "player_action_scope": "uncertain"}
        payload = contract_audit_payload({"violations": [{"rule_index": 0, "candidate_ref": "c1",
            "quote": "可以，我就按给你看。", "prior_ref": ""}]}, messages)
        return payload

    calls = _install(monkeypatch, respond)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=node, session=session, actor_performance=body, player_input="你愿意按测试键展示吗？") == ()
    assert len(calls) == 2
