"""Prebound requests exclude duplicate discovery work without changing indexes."""

from dataclasses import replace
from copy import deepcopy

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_fact_discovery import DISCOVERY_RULE
from tests.unit.test_theater_numeric_v2_bound_completion_contract import _bound, _install, _review
from tests.unit.test_theater_numeric_v2_fact_discovery import _body, _frame, _items, _packet, KEY, OTHER
from tests.utils.theater_prompt import contract_audit_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


@pytest.mark.asyncio
async def test_all_prebound_targets_remove_discovery_table_rule_and_schema(monkeypatch):
    engine, session = _frame()
    body = _body()
    packet = _packet(engine, session, body)

    def respond(messages, data, kwargs):
        assert "completion_check" not in data and DISCOVERY_RULE not in messages[0].content
        assert "fact_discoveries" not in kwargs["response_format"]["json_schema"]["schema"]["properties"]
        payload = contract_audit_payload({"violations": []}, messages)
        payload["bound_completion_approved_indexes"] = []
        return payload

    calls = _install(monkeypatch, respond)
    sink, discovered = [], []
    assert await _review(engine, session, body, _bound(engine, session, body), sink,
        discovery_packet=packet, discovery_sink=discovered) == ()
    assert sink == [()] and not discovered and len(calls) == 1


@pytest.mark.asyncio
async def test_partial_exclusion_keeps_original_nonzero_server_index(monkeypatch):
    engine, session = _frame(two=True)
    body = _body()
    packet = _packet(engine, session, body)
    index = next(i for i, target in enumerate(packet["targets"]) if target["key"] == OTHER)
    assert index == 1

    def respond(messages, data, kwargs):
        assert [(row["target_index"], row["key"]) for row in data["completion_check"]["targets"]] == [(index, OTHER)]
        schema = kwargs["response_format"]["json_schema"]["schema"]
        assert schema["properties"]["fact_discoveries"]["items"]["properties"]["target_index"]["enum"] == [index]
        payload = contract_audit_payload({"violations": []}, messages)
        payload.update(bound_completion_approved_indexes=[], fact_discoveries=_items(packet, keys=(OTHER,)))
        return payload

    _install(monkeypatch, respond)
    sink, discovered = [], []
    assert await _review(engine, session, body, _bound(engine, session, body), sink,
        discovery_packet=packet, discovery_sink=discovered) == ()
    assert sink == [()] and discovered[0].claims[0]["key"] == OTHER


@pytest.mark.asyncio
async def test_excluded_original_index_cannot_reenter_through_malformed_discovery_reply(monkeypatch):
    engine, session = _frame(two=True)
    body = _body()
    packet = _packet(engine, session, body)

    def respond(messages, data, kwargs):
        payload = contract_audit_payload({"violations": []}, messages)
        payload.update(bound_completion_approved_indexes=[], fact_discoveries=_items(packet, keys=(KEY,)))
        return payload

    _install(monkeypatch, respond)
    sink, discovered = [], []
    assert await _review(engine, session, body, _bound(engine, session, body), sink,
        discovery_packet=packet, discovery_sink=discovered) == ()
    assert sink == [()] and len(discovered) == 1 and not discovered[0].claims and discovered[0].errors


@pytest.mark.asyncio
async def test_discovery_value_comparison_preserves_json_type(monkeypatch):
    engine, session = _frame()
    body = _body()
    packet = deepcopy(_packet(engine, session, body))
    packet["targets"][0]["value"] = 1

    def respond(messages, data, kwargs):
        assert data["completion_check"]["targets"][0]["value"] == 1
        payload = contract_audit_payload({"violations": []}, messages)
        payload.update(bound_completion_approved_indexes=[], fact_discoveries=[])
        return payload

    _install(monkeypatch, respond)
    sink = []
    assert await _review(engine, session, body, _bound(engine, session, body), sink,
        discovery_packet=packet, discovery_sink=[]) == ()
    assert sink == [()]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["stale", "budget"])
async def test_bound_fallback_restores_identical_discovery_request(monkeypatch, invalid):
    engine, session = _frame()
    body = _body()
    packet = _packet(engine, session, body)

    def respond(messages, data, kwargs):
        assert "completion_check" in data and "bound_completion_check" not in data
        payload = contract_audit_payload({"violations": []}, messages)
        payload["fact_discoveries"] = _items(packet)
        return payload

    calls = _install(monkeypatch, respond)
    discovered = []
    assert await _review(engine, session, body, None, [], discovery_packet=packet, discovery_sink=discovered) == ()
    batch = _bound(engine, session, body)
    if invalid == "stale":
        batch = replace(batch, packet={**batch.packet, "binding": {**batch.packet["binding"], "revision": -1}})
    else:
        count = ev.count_tokens
        monkeypatch.setattr(ev, "count_tokens", lambda text: 20000 if "bound_completion_check" in text else count(text))
    sink = []
    assert await _review(engine, session, body, batch, sink, discovery_packet=packet, discovery_sink=discovered) == ()
    assert not sink and [m.content for m in calls[0][0]] == [m.content for m in calls[1][0]]
    assert calls[0][1] == calls[1][1] and all(row.claims[0]["key"] == KEY for row in discovered)
