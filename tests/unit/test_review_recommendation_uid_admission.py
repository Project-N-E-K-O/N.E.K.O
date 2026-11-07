"""Missing character identity cannot create offers outside preference policy."""
from copy import deepcopy

import pytest

from main_routers import recommendation_router as routes
from tests.unit.test_recommendation_router import client  # noqa: F401
from utils.config_manager.reserved_schema import set_reserved


def material(category):
    return {
        "success": True, "action": "chat", "source_mode": category,
        "response_text": "Already committed dialogue", "turn_id": "fixture-turn",
        "source_links": [{"title": "Fixture source", "url": "https://example.com/item",
                          **({"artist": "Fixture artist"} if category == "music" else {})}],
    }


def count_offers(store, _owner):
    with store._transaction() as db:
        return db.execute("SELECT count(*) FROM recommendations").fetchone()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [1, 2])
@pytest.mark.parametrize("category", ["music", "news"])
@pytest.mark.parametrize("invalid_uid", [None, "invalid", "A" * 32])
async def test_supported_capture_with_missing_or_malformed_uid_never_registers_card(
    client, monkeypatch, protocol, category, invalid_uid,
):
    monkeypatch.setattr(routes, "STORE_PROTOCOL_VERSION", protocol)
    monkeypatch.setattr(routes, "_control", lambda _owner: None)
    profile = routes.get_config_manager().load_characters()["猫娘"]["Neko"]
    set_reserved(profile, "character_uid", invalid_uid)
    owner = await routes.capture_owner()
    _view, uid = await routes.capture_policy("Neko", owner)
    assert uid is None
    body = material(category)
    original = deepcopy(body)
    attach = routes.attach_music_recommendation if category == "music" else routes.attach_proactive_recommendations
    await attach(body, owner, "Neko", character_uid=uid)
    assert body == original
    assert routes._operate(count_offers) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [1, 2])
@pytest.mark.parametrize("category", ["music", "news"])
@pytest.mark.parametrize("invalid_uid", ["", "invalid", 7, False])
async def test_attachment_boundary_also_rejects_invalid_explicit_uid(
    client, monkeypatch, protocol, category, invalid_uid,
):
    monkeypatch.setattr(routes, "STORE_PROTOCOL_VERSION", protocol)
    monkeypatch.setattr(routes, "_control", lambda _owner: None)
    owner = await routes.capture_owner()
    body = material(category)
    original = deepcopy(body)
    attach = routes.attach_music_recommendation if category == "music" else routes.attach_proactive_recommendations
    await attach(body, owner, "Neko", character_uid=invalid_uid)
    assert body == original
    assert routes._operate(count_offers) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [1, 2])
@pytest.mark.parametrize("category", ["music", "news"])
async def test_valid_captured_uid_keeps_card_and_commits_refusal_policy(client, monkeypatch, protocol, category):
    http, _ = client
    monkeypatch.setattr(routes, "STORE_PROTOCOL_VERSION", protocol)
    monkeypatch.setattr(routes, "_control", lambda _owner: None)
    owner = await routes.capture_owner()
    _view, uid = await routes.capture_policy("Neko", owner)
    assert uid == "a" * 32
    body = material(category)
    attach = routes.attach_music_recommendation if category == "music" else routes.attach_proactive_recommendations
    await attach(body, owner, "Neko", character_uid=uid)
    card = body["music_recommendation"] if category == "music" else body["proactive_recommendations"][0]
    reply = http.post(f"/api/proactive/recommendations/{card['id']}/events", json={
        "event_id": "fixture-refusal", "kind": "skipped",
    })
    assert reply.status_code == 200
    assert reply.json()["policy_character_uid"] == uid
    assert reply.json()["policy"][category]["decline_count"] == 1
    assert routes._operate(count_offers) == 1
