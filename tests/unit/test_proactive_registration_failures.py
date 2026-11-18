"""Real route/storage outcomes consumed by the frontend confirmation boundary."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.proactive_chat.contracts import ProactiveChatResult
from main_logic.recommendation_feedback import RecommendationStore
from main_routers import recommendation_router as routes
from main_routers.system_router import proactive_chat_flow as flow
from tests.unit.test_recommendation_router import client  # noqa: F401


def committed_body(category):
    music = category == "music"
    body = {"success": True, "action": "chat", "turn_id": "committed-turn",
            "response_text": "Already delivered dialogue", "source_mode": category,
            "source_links": [{"title": "Material", "url": "https://i.imgflip.com/fixture.jpg",
                              "type": "meme" if category == "meme" else "link",
                              **({"artist": "Artist"} if music else {})}]}
    if category == "screen":
        body.update(source_mode="vision", source_links=[], screen_observation={"text": "Screen observation"})
    return body


@pytest.mark.parametrize("category", ["meme", "news", "web", "vision", "screen", "music"])
@pytest.mark.parametrize("outcome", ["normal", "capacity", "database", "owner_changed"])
def test_real_committed_route_preserves_dialogue_but_grants_only_registered_cards(client, monkeypatch, category, outcome):
    http, snapshot = client
    http.app.add_api_route("/api/proactive_chat", flow.proactive_chat, methods=["POST"])
    monkeypatch.setattr(flow, "get_config_manager", lambda: SimpleNamespace(
        aget_character_data=AsyncMock(return_value=("Master", "Neko", None, None, None, {}, None, None, None))))
    monkeypatch.setattr(flow, "get_session_manager", lambda: {})
    monkeypatch.setattr(flow, "is_theater_active", lambda _: False)
    body = committed_body(category)

    async def committed(*args, **kwargs):
        if outcome == "owner_changed":
            snapshot["local_user_id"] = "replacement"
        return ProactiveChatResult(body=dict(body))

    # Only upstream generation is isolated. Identity capture/recheck, registry
    # transactions, full-capacity rollback, and HTTP adaptation remain real.
    monkeypatch.setattr(flow.proactive_service, "handle_proactive_chat", committed)
    db_path = routes._operate(lambda store, owner: store.path)
    if outcome == "capacity":
        monkeypatch.setattr(RecommendationStore, "MAX_RECOMMENDATIONS", 1)
        routes._operate(lambda store, owner: store.create(owner, "Neko", "older-turn",
                        {"name": "Older", "artist": "Artist", "url": "https://media.example/old"}, {}))
    elif outcome == "database":
        db_path.write_bytes(b"Existing unreadable database must be preserved")

    result = http.post("/api/proactive_chat", json={"enabled_modes": ["meme"]})
    assert result.status_code == 200
    response = result.json()
    assert response["response_text"] == body["response_text"]
    assert response["turn_id"] == body["turn_id"]
    assert response["source_links"] == body["source_links"]
    cards = response.get("proactive_recommendations", []) + ([response["music_recommendation"]]
            if response.get("music_recommendation") else [])
    assert len(cards) == (1 if outcome == "normal" else 0)
    assert all(card["state"] == "pending" for card in cards)
    if outcome == "database":
        assert db_path.read_bytes() == b"Existing unreadable database must be preserved"
    else:
        def count(store, _owner):
            with store._transaction() as db:
                return db.execute("SELECT count(*) FROM recommendations").fetchone()[0]
        assert routes._operate(count) == (1 if outcome in {"normal", "capacity"} else 0)
