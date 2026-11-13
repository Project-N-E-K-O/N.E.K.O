"""Prebound facts can share a contract review, while discoveries stay unapproved."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_fact_discovery as discovery
from services.theater.numeric_v2_structured_output import contract_output_schema
from tests.unit.test_theater_numeric_v2_fact_discovery import _body, _frame, _items, _packet, KEY, OTHER
from tests.utils.theater_prompt import contract_audit_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


def _bound(engine, session, body, message="请工作人员核对。"):
    packet = _packet(engine, session, body, message)
    proposed = discovery.parse_discoveries(_items(packet), packet).claims[0]
    return discovery.bind_source_delivery(engine, ({**proposed, "evidence": []},), packet)


def _install(monkeypatch, respond):
    calls = []

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            calls.append((messages, kwargs))
            data = json.loads(messages[1].content.split("\n", 1)[1])
            return SimpleNamespace(content=json.dumps(respond(messages, data, kwargs), ensure_ascii=False))

    async def factory(*args, **kwargs):
        assert kwargs["max_completion_tokens"] <= 720
        return Client()

    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    return calls


async def _review(engine, session, body, batch, sink, **kwargs):
    return await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=engine.nodes[session.current_node_id], session=session, actor_performance=body,
        player_input="请工作人员核对。", include_all_segments=True,
        bound_completion_batch=batch, bound_completion_sink=sink, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("indexes", [[0], []])
async def test_prebound_approval_uses_original_indexes_and_preserves_sources(monkeypatch, indexes):
    engine, session = _frame()
    body = _body()
    batch = _bound(engine, session, body)
    before = deepcopy(batch)

    def respond(messages, data, kwargs):
        check = data["bound_completion_check"]
        assert check["claims"][0]["index"] == 0 and check["claims"][0]["key"] == KEY
        assert check["evidence_bindings"][0]["refs"] == list(batch.bindings[0])
        assert check["evidence_source_table"] == batch.packet["sources"]
        assert "只返回原index，不新增、改写或重排候选" in messages[0].content
        payload = contract_audit_payload({"violations": []}, messages)
        payload["bound_completion_approved_indexes"] = indexes
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    assert await _review(engine, session, body, batch, sink) == ()
    assert sink == [tuple(indexes)] and len(calls) == 1 and batch == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["revision", "node", "session", "package", "facts", "input", "body", "packet", "binding"])
async def test_stale_or_unbound_batch_falls_back_without_approval(monkeypatch, mutation):
    engine, session = _frame()
    body = _body()
    batch = _bound(engine, session, body)
    if mutation == "revision": session = replace(session, revision=session.revision + 1)
    elif mutation == "node": batch.packet["binding"]["node_id"] = "other"
    elif mutation == "session": batch.packet["binding"]["session_id"] = "other"
    elif mutation == "package": batch.packet["binding"]["package_hash"] = "other"
    elif mutation == "facts": session = replace(session, story_state={**session.story_state, "facts": {KEY: {"value": False}}})
    elif mutation == "input": batch = _bound(engine, session, body, "不同的玩家原话。")
    elif mutation == "body": body["performance"] += "继续等候。"
    elif mutation == "packet": batch.packet["sources"][0]["text"] = "改变原文。"
    else: batch = replace(batch, bindings=(("not-issued",),))

    def respond(messages, data, kwargs):
        assert "bound_completion_check" not in data
        return contract_audit_payload({"violations": []}, messages)

    calls = _install(monkeypatch, respond)
    sink = []
    assert await _review(engine, session, body, batch, sink) == ()
    assert sink == [] and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("indexes", [[True], [-1], [1], [0, 0], "0", None])
async def test_invalid_or_missing_approval_never_enters_sink(monkeypatch, indexes):
    engine, session = _frame()
    body = _body()

    def respond(messages, data, kwargs):
        payload = contract_audit_payload({"violations": []}, messages)
        if indexes is not None: payload["bound_completion_approved_indexes"] = indexes
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    assert await _review(engine, session, body, _bound(engine, session, body), sink) == ()
    assert sink == [] and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmation", ["violation", "uncertain", "clear"])
async def test_prebound_approval_waits_for_whole_body_confirmation(monkeypatch, confirmation):
    engine, session = _frame()
    body = _body()
    rule = engine.nodes["start"]["story_beat"]["must_not_happen"][0]

    def respond(messages, data, kwargs):
        if "sources" not in data:
            return {"verdict": confirmation, "reason": "按本轮原文核对。", "clearance": ["p0"] if confirmation == "clear" else []}
        payload = contract_audit_payload({"violations": [{"rule_index": 0, "candidate_ref": "c0",
            "quote": body["scene_narration"], "prior_ref": ""}]}, messages)
        payload["bound_completion_approved_indexes"] = [0]
        return payload

    calls = _install(monkeypatch, respond)
    sink = []
    problems = await _review(engine, session, body, _bound(engine, session, body), sink)
    assert bool(problems) is (confirmation != "clear")
    assert sink == ([(0,)] if confirmation == "clear" else []) and len(calls) == 2
    if problems: assert rule in problems


@pytest.mark.asyncio
async def test_new_discoveries_and_prebound_approval_have_separate_indexes(monkeypatch):
    engine, session = _frame(two=True)
    body = _body()
    packet = _packet(engine, session, body)
    discovered = []

    def respond(messages, data, kwargs):
        assert "completion_check" in data and "bound_completion_check" in data
        payload = contract_audit_payload({"violations": []}, messages)
        payload["bound_completion_approved_indexes"] = []
        payload["fact_discoveries"] = _items(packet, keys=(OTHER,))
        return payload

    _install(monkeypatch, respond)
    sink = []
    assert await _review(engine, session, body, _bound(engine, session, body), sink,
        discovery_packet=packet, discovery_sink=discovered) == ()
    assert sink == [()] and len(discovered) == 1 and discovered[0].claims[0]["key"] == OTHER


@pytest.mark.asyncio
async def test_bound_addition_over_budget_uses_identical_original_contract_request(monkeypatch):
    engine, session = _frame()
    body = _body()
    calls = _install(monkeypatch, lambda messages, data, kwargs: contract_audit_payload({"violations": []}, messages))
    reviewer = ev.NumericV2MetricEvaluator(object())
    kwargs = dict(node=engine.nodes["start"], session=session, actor_performance=body, player_input="请工作人员核对。")
    assert await reviewer.verify_contract_boundaries(**kwargs) == ()
    original_count = ev.count_tokens
    monkeypatch.setattr(ev, "count_tokens", lambda value: 20000 if "bound_completion_check" in str(value) else original_count(value))
    sink = []
    assert await reviewer.verify_contract_boundaries(**kwargs, bound_completion_batch=_bound(engine, session, body), bound_completion_sink=sink) == ()
    assert sink == [] and len(calls) == 2
    assert [m.content for m in calls[0][0]] == [m.content for m in calls[1][0]]
    assert calls[0][1] == calls[1][1]


@pytest.mark.asyncio
async def test_no_contract_review_cannot_publish_prebound_approval(monkeypatch):
    engine, session = _frame()
    body = _body()
    calls = _install(monkeypatch, lambda *args: pytest.fail("must not request a model"))
    sink = []
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session, actor_performance=body, player_input="请工作人员核对。",
        author_boundaries_only=True, bound_completion_batch=_bound(engine, session, body), bound_completion_sink=sink) == ()
    assert sink == [] and not calls


def test_bound_approval_schema_uses_only_issued_indexes_and_not_confirmation():
    schema = contract_output_schema(grounded=True, bound_completion_count=2)
    indexes = schema["properties"]["bound_completion_approved_indexes"]
    assert indexes["items"]["enum"] == [0, 1] and indexes["maxItems"] == 2
    assert "bound_completion_approved_indexes" not in contract_output_schema(grounded=True)["properties"]
    assert "bound_completion_approved_indexes" not in contract_output_schema(
        grounded=True, confirmation=True, bound_completion_count=2)["properties"]


@pytest.mark.asyncio
async def test_subset_approval_keeps_server_claim_order(monkeypatch):
    engine, session = _frame(two=True)
    body = _body()
    packet = _packet(engine, session, body)
    proposed = discovery.parse_discoveries(_items(packet, keys=(KEY, OTHER)), packet)
    batch = discovery.bind_source_delivery(engine,
        tuple({**claim, "evidence": []} for claim in proposed.claims), packet)

    def respond(messages, data, kwargs):
        assert [(row["index"], row["key"]) for row in data["bound_completion_check"]["claims"]] == [(0, KEY), (1, OTHER)]
        payload = contract_audit_payload({"violations": []}, messages)
        payload["bound_completion_approved_indexes"] = [1]
        return payload

    _install(monkeypatch, respond)
    sink = []
    assert await _review(engine, session, body, batch, sink) == ()
    assert sink == [(1,)] and batch.select(sink[0]).operations[0]["key"] == OTHER


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["performance", "player_input"])
async def test_piggyback_preserves_player_and_performance_evidence_types(monkeypatch, source):
    engine, session = _frame(source=source)
    body = _body()
    packet = _packet(engine, session, body)
    items = _items(packet) if source == "performance" else [{"target_index": 0, "refs": ["e0"]}]
    batch = discovery.parse_discoveries(items, packet)
    batch.validate()

    def respond(messages, data, kwargs):
        check = data["bound_completion_check"]
        claim = check["claims"][0]
        proof = next(row for row in check["evidence_source_table"] if row["ref"] == batch.bindings[0][0])
        assert proof["source"] == claim["evidence"][0]["source"] == (
            "actor_performance" if source == "performance" else "player_input")
        assert proof["kind"] == ("current_draft" if source == "performance" else "current_player")
        assert "玩家动作、选择、主观理解或反馈须玩家原话" in messages[0].content
        payload = contract_audit_payload({"violations": []}, messages)
        payload["bound_completion_approved_indexes"] = []
        return payload

    _install(monkeypatch, respond)
    sink = []
    assert await _review(engine, session, body, batch, sink) == ()
    assert sink == [()]


@pytest.mark.asyncio
async def test_historical_condition_and_new_delivery_retain_independent_originals(monkeypatch):
    engine, session = _frame()
    body = _body("工作人员将已核对的通行证交回。")
    historical = "工作人员已核对通行证。"
    packet = discovery.build_packet(engine, session, body, "请工作人员核对。",
        pending_targets=ev._pending_completion_facts(engine, session),
        history_metadata={"allowed_keys": [KEY]}, history_originals=[historical])
    proposed = discovery.parse_discoveries(_items(packet), packet).claims[0]
    proposed["evidence"] = [{"source": "actor_performance", "quote": historical}]
    batch = discovery.bind_source_delivery(engine, (proposed,), packet)
    batch.validate()

    def respond(messages, data, kwargs):
        check = data["bound_completion_check"]
        table = {row["ref"]: row for row in check["evidence_source_table"]}
        bound = [table[ref] for ref in check["evidence_bindings"][0]["refs"]]
        assert [row["kind"] for row in bound] == ["committed_current_visit", "current_draft"]
        assert bound[0]["text"] == historical and bound[1]["field"] == "source_delivery"
        payload = contract_audit_payload({"violations": []}, messages)
        payload["bound_completion_approved_indexes"] = [0]
        return payload

    _install(monkeypatch, respond)
    sink = []
    assert await _review(engine, session, body, batch, sink) == ()
    assert sink == [(0,)]


@pytest.mark.asyncio
@pytest.mark.parametrize("unchecked", ["author", "player", "fixed_cause"])
async def test_unresolved_whole_body_coverage_never_publishes_approval(monkeypatch, unchecked):
    engine, session = _frame()
    body = _body()
    if unchecked == "fixed_cause":
        body["fixed_narrations"] = [{"position": "after", "text": "柜台灯亮了。"}]

    def respond(messages, data, kwargs):
        if "finding" in data or "findings" in data:
            return {"verdict": "uncertain", "reason": "玩家权限仍未判明。", "clearance": []}
        payload = contract_audit_payload({"violations": []}, messages)
        if unchecked == "author": payload.pop("author_checks")
        elif unchecked == "player": payload["player_authority"]["c0"] = "uncertain"
        else: payload.pop("fixed_original_cause")
        payload["bound_completion_approved_indexes"] = [0]
        return payload

    _install(monkeypatch, respond)
    sink = []
    assert await _review(engine, session, body, _bound(engine, session, body), sink)
    assert sink == []
