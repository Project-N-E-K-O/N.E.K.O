import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import recommendation_router as routes
from main_routers.system_router import _shared


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "STORE_PROTOCOL_VERSION", 1)
    snapshot = {"local_user_id": "a", "access_token": "private", "base_url": "https://community.example"}
    lock = threading.RLock()
    @contextmanager
    def fence():
        with lock:
            yield
    monkeypatch.setattr(routes.host, "saved_session_snapshot", lambda: dict(snapshot))
    monkeypatch.setattr(routes.host, "saved_session_writer_fence", fence)
    monkeypatch.setattr(routes, "get_config_manager", lambda: SimpleNamespace(get_runtime_config_path=lambda name: tmp_path / name))
    monkeypatch.setattr(routes, "load_global_conversation_settings", lambda: {"proactiveMusicEnabled": True})
    monkeypatch.setattr(_shared, "AUTOSTART_CSRF_TOKEN", "test-token")
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/proactive")
    with TestClient(app, base_url="http://127.0.0.1:48911",
                    headers={"Origin": "http://127.0.0.1:48911", "X-CSRF-Token": "test-token"}) as http:
        yield http, snapshot


async def create():
    body = {"success": True, "action": "chat", "source_mode": "music", "turn_id": "turn",
            "source_links": [{"title": "Song", "artist": "Artist", "url": "https://example.com/a.mp3"}]}
    owner = await routes.capture_owner()
    await routes.attach_music_recommendation(body, owner, "Neko")
    return body["music_recommendation"]["id"]


@pytest.mark.asyncio
async def test_actual_routes_register_decide_and_export_without_tokens(client):
    http, _ = client
    rec = await create()
    response = http.post(f"/api/proactive/recommendations/{rec}/events", json={"event_id": "accept", "kind": "accepted"})
    assert response.status_code == 200
    assert response.json()["playback_grant"]
    response = http.get("/api/proactive/recommendations/events")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["events"][0]["kind"] == "accepted"
    assert "private" not in response.text
    assert "access_token" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("first,opposite", [("accepted", "skipped"), ("skipped", "accepted")])
async def test_v2_opposite_decision_is_rejected_with_exact_terminal_handoff(client, monkeypatch, first, opposite):
    import base64
    import hashlib
    import json
    http, _ = client
    monkeypatch.setattr(routes, "STORE_PROTOCOL_VERSION", 2)
    owner = await routes.capture_owner()
    rec = await create()
    endpoint = f"/api/proactive/recommendations/{rec}/events"
    context = {"version": "desktop-feedback-capture-v2", "owner_key": owner["key"]}
    original = {"event_id": "original-choice", "kind": first, "context": context}
    assert http.post(endpoint, json=original).status_code == 200
    rejected = {"event_id": "opposite-choice", "kind": opposite, "context": context,
                "occurred_at": 1e-7, "reason": "猫😀"}
    canonical = json.dumps(rejected, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    response = http.post(endpoint, json=rejected,
        headers={"X-NEKO-Feedback-Canonical": base64.b64encode(canonical).decode()})
    assert response.status_code == 409
    assert response.json() == {"success": False, "error_code": "recommendation_decision_conflict",
        "telemetry": {"event_id": "opposite-choice", "payload_hash": hashlib.sha256(canonical).hexdigest(),
                      "status": "discarded", "reason": "decision_conflict"}}
    replayed = http.post(endpoint, json=original)
    assert replayed.status_code == 200
    assert not replayed.json()["applied"] and replayed.json()["state"] == first


@pytest.mark.asyncio
async def test_account_switch_and_disabled_feature_refuse_play(client, monkeypatch):
    http, snapshot = client
    rec = await create()
    snapshot["local_user_id"] = "b"
    payload = {"event_id": "accept", "kind": "accepted"}
    assert http.post(f"/api/proactive/recommendations/{rec}/events", json=payload).status_code == 409
    snapshot["local_user_id"] = "a"
    monkeypatch.setattr(routes, "load_global_conversation_settings", lambda: {"proactiveMusicEnabled": False})
    assert http.post(f"/api/proactive/recommendations/{rec}/events", json=payload).json()["error_code"] == "recommendation_disabled"


def test_export_and_feedback_require_origin_and_csrf(client):
    http, _ = client
    assert http.get("/api/proactive/recommendations/events", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert http.post("/api/proactive/recommendations/id/events", json={"event_id": "x", "kind": "shown"},
                     headers={"Origin": "https://attacker.example"}).status_code == 403
    assert http.post("/api/proactive/recommendations/id/events", json={"event_id": "x", "kind": "like"}).status_code == 422
    assert http.post("/api/proactive/recommendations/id/events", json={"event_id": "x", "kind": "shown", "listened_seconds": True}).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("category,setting", [("news", "proactiveNewsChatEnabled"), ("video", "proactiveVideoChatEnabled")])
async def test_disabled_second_window_does_not_erase_saved_content_acceptance(client, monkeypatch, category, setting):
    http, _ = client
    body = {"success": True, "action": "chat", "source_mode": category,
            "source_links": [{"title": "Shared source", "url": "https://example.com/story", "mode": category}]}
    await routes.attach_proactive_recommendations(body, await routes.capture_owner(), "Neko")
    rec = body["proactive_recommendations"][0]
    endpoint = f"/api/proactive/recommendations/{rec['id']}/events"
    accepted = http.post(endpoint, json={"event_id": "window-a", "kind": "accepted"})
    assert accepted.status_code == 200 and accepted.json()["action_grant"]
    monkeypatch.setattr(routes, "load_global_conversation_settings", lambda: {setting: False})
    refused = http.post(endpoint, json={"event_id": "window-b", "kind": "accepted"})
    assert refused.status_code == 409
    assert refused.json()["error_code"] == "recommendation_disabled"
    events = http.get("/api/proactive/recommendations/events").json()["events"]
    assert [(event["kind"], event["event_id"]) for event in events] == [("accepted", "window-a")]
    monkeypatch.setattr(routes, "load_global_conversation_settings", lambda: {setting: True})
    replay = http.post(endpoint, json={"event_id": "window-b", "kind": "accepted"})
    assert replay.status_code == 200 and replay.json()["state"] == "accepted"
    assert not replay.json()["action_grant"]


@pytest.mark.asyncio
async def test_account_replacement_during_generation_does_not_reassign_recommendation(client):
    _, snapshot = client
    old_owner = await routes.capture_owner()
    snapshot["local_user_id"] = "b"
    body = {"success": True, "action": "chat", "source_mode": "music", "source_links": [{"url": "https://example.com/a.mp3"}]}
    await routes.attach_music_recommendation(body, old_owner, "Neko")
    assert body["music_recommendation"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('category', ['news', 'community', 'video', 'personal', 'meme', 'vision', 'window', 'work_break', 'anti_slack'])
async def test_content_register_decide_and_export_with_the_correct_category(client, category):
    http, _ = client
    body = {'success': True, 'action': 'chat', 'source_mode': category, 'turn_id': 'source-turn',
            'source_links': [{'title': 'A topic', 'url': 'https://example.com/content?token=private', 'mode': category}]}
    await routes.attach_proactive_recommendations(body, await routes.capture_owner(), 'Neko')
    rec = body['proactive_recommendations'][0]
    assert rec['category'] == category
    endpoint = f"/api/proactive/recommendations/{rec['id']}/events"
    assert http.post(endpoint, json={'event_id': 'shown', 'kind': 'shown'}).status_code == 200
    accepted = http.post(endpoint, json={'event_id': 'choice', 'kind': 'accepted'}).json()
    assert accepted['action_grant'] and not accepted['playback_grant']
    events = http.get('/api/proactive/recommendations/events').json()['events']
    assert {row['category'] for row in events} == {category}
    assert 'token=' not in str(events) and 'private' not in str(events)


@pytest.mark.asyncio
async def test_content_permissions_owner_and_screen_privacy(client, monkeypatch):
    http, snapshot = client
    body = {'success': True, 'action': 'chat', 'source_mode': 'vision', 'turn_id': 'screen-turn',
            'response_text': 'private conversation', 'screenshot_data': 'private screen',
            'source_links': [{'title': 'A source', 'url': 'https://example.com/source', 'mode': 'vision'}]}
    owner = await routes.capture_owner()
    await routes.attach_proactive_recommendations(body, owner, 'Neko')
    rec = body['proactive_recommendations'][0]
    assert 'private' not in str(rec)
    endpoint = f"/api/proactive/recommendations/{rec['id']}/events"
    monkeypatch.setattr(routes, 'load_global_conversation_settings', lambda: {'proactiveVisionEnabled': False})
    assert http.post(endpoint, json={'event_id': 'accept', 'kind': 'accepted'}).json()['error_code'] == 'recommendation_disabled'
    snapshot['local_user_id'] = 'replacement'
    await routes.attach_proactive_recommendations(body, owner, 'Neko')
    assert body['proactive_recommendations'] == []


@pytest.mark.asyncio
async def test_content_registration_is_bounded_safe_and_does_not_duplicate_music_or_games(client):
    body = {'success': True, 'action': 'chat', 'source_mode': 'both',
            'music_recommendation': {'track': {'url': 'https://music.example/song'}},
            'source_links': [{'url': 'javascript:alert(1)'}, {'url': 'https://name:secret@example.com'},
                             {'url': 'https://music.example/song', 'artist': 'Music'},
                             *[{'url': f'https://example.com/{index}', 'mode': 'news'} for index in range(8)]]}
    await routes.attach_proactive_recommendations(body, await routes.capture_owner(), 'Neko')
    assert len(body['proactive_recommendations']) == 3
    assert {item['category'] for item in body['proactive_recommendations']} == {'news'}
    assert not any('music.example' in item['content']['url'] for item in body['proactive_recommendations'])
    for channel in ['mini_game', 'work_break_game_invite']:
        invitation = {'success': True, 'action': 'chat', 'channel': channel}
        await routes.attach_proactive_recommendations(invitation, await routes.capture_owner(), 'Neko')
        assert 'proactive_recommendations' not in invitation


@pytest.mark.asyncio
@pytest.mark.parametrize('category', [None, 'chat', 'unknown', 'reminiscence', 'news', 'vision', 'window', 'work_break', 'anti_slack'])
async def test_dialogue_without_material_never_creates_a_placeholder_card_or_record(client, category):
    http, _ = client
    body = {'success': True, 'action': 'chat', 'response_text': 'Delivered dialogue',
            'source_mode': category, 'source_links': [{'url': 'javascript:alert(1)'}]}
    await routes.attach_proactive_recommendations(body, await routes.capture_owner(), 'Neko')
    assert body['proactive_recommendations'] == []
    assert body['response_text'] == 'Delivered dialogue'
    def count(store, owner):
        with store._transaction() as db:
            return db.execute('SELECT count(*) FROM recommendations').fetchone()[0]
    assert await routes.asyncio.to_thread(routes._operate, count) == 0
    assert http.get('/api/proactive/recommendations/events').json()['events'] == []


@pytest.mark.asyncio
async def test_unclassified_real_link_uses_web_instead_of_generic_topic(client):
    body = {'success': True, 'action': 'chat', 'source_links': [{'title': 'A real source', 'url': 'https://example.com/story'}]}
    await routes.attach_proactive_recommendations(body, await routes.capture_owner(), 'Neko')
    assert body['proactive_recommendations'][0]['category'] == 'web'


@pytest.mark.asyncio
async def test_committed_screen_observation_has_feedback_without_retaining_screen_text(client, monkeypatch):
    http, snapshot = client
    body = {'success': True, 'action': 'chat', 'source_mode': 'chat', 'turn_id': 'visual-turn',
            'screen_observation': {'text': 'Private screen observation'}, 'source_links': []}
    owner = await routes.capture_owner()
    await routes.attach_proactive_recommendations(body, owner, 'Neko')
    rec = body['proactive_recommendations'][0]
    assert rec['category'] == 'vision'
    assert rec['content'] == {'title': 'Private screen observation', 'source': '', 'url': ''}
    endpoint = f"/api/proactive/recommendations/{rec['id']}/events"
    assert http.post(endpoint, json={'event_id': 'visual-shown', 'kind': 'shown'}).status_code == 200
    monkeypatch.setattr(routes, 'load_global_conversation_settings', lambda: {'proactiveVisionEnabled': False})
    assert http.post(endpoint, json={'event_id': 'visual-choice', 'kind': 'accepted'}).json()['error_code'] == 'recommendation_disabled'
    monkeypatch.setattr(routes, 'load_global_conversation_settings', lambda: {'proactiveVisionEnabled': True})
    accepted = http.post(endpoint, json={'event_id': 'visual-choice', 'kind': 'accepted'}).json()
    assert accepted['state'] == 'accepted' and not accepted['playback_grant']
    events = http.get('/api/proactive/recommendations/events').json()['events']
    assert [event['kind'] for event in events] == ['shown', 'accepted']
    assert all(event['category'] == 'vision' for event in events)
    def stored(store, owner):
        with store._transaction() as db:
            return str([tuple(row) for row in db.execute('SELECT * FROM recommendations')])
    assert 'Private screen observation' not in await routes.asyncio.to_thread(routes._operate, stored)
    snapshot['local_user_id'] = 'replacement'
    await routes.attach_proactive_recommendations(body, owner, 'Neko')
    assert body['proactive_recommendations'] == []


@pytest.mark.asyncio
async def test_v2_actual_route_handoff_uses_original_json_hash_and_claims_dispatch_once(client, monkeypatch):
    from main_logic.recommendation_feedback import RecommendationStore
    http, _ = client
    monkeypatch.setattr(routes, "STORE_PROTOCOL_VERSION", 2)
    snapshot = {"version": "desktop-feedback-capture-v2", "owner_key": routes._owner()["key"],
                "account_incarnation": "fixture", "clear_generation": 0, "registration_id": "fixture",
                "registration_version": 1, "binding_revision": 1,
                "capture": {"clock_session_id": "fixture", "tick_ms": 1.5, "anchor_id": "fixture"},
                "time_proof": {"anchor_token": "fixture", "send_tick_ms": 0, "receive_tick_ms": 1}}
    monkeypatch.setattr(routes, "_control", lambda owner: snapshot)
    body = {"success": True, "action": "chat", "source_mode": "music", "turn_id": "fixture",
            "source_links": [{"title": "Fixture", "artist": "Fixture", "url": "https://example.com/fixture"}]}
    await routes.attach_music_recommendation(body, await routes.capture_owner(), "Fixture")
    card = body["music_recommendation"]
    value = {"event_id": "fixture-accept", "kind": "accepted", "occurred_at": 1,
             "listened_seconds": 0, "position_seconds": 0,
             "context": snapshot, "offer_context": card["offer_context"]}
    endpoint = f"/api/proactive/recommendations/{card['id']}"
    reply = http.post(endpoint + "/events", json=value)
    assert reply.status_code == 200 and reply.json()["playback_grant"]
    assert reply.json()["telemetry"]["payload_hash"] == RecommendationStore.event_identity(value)[2]
    assert reply.json()["telemetry"]["status"] == "durable"
    retried = http.post(endpoint + "/events", json=value).json()
    assert not retried["applied"] and retried["telemetry"] == reply.json()["telemetry"]
    assert http.post(endpoint + "/dispatch", json={"event_id": "fixture-accept"}).json()["dispatch_grant"]
    assert not http.post(endpoint + "/dispatch", json={"event_id": "fixture-accept"}).json()["dispatch_grant"]
    changed = {**value, "occurred_at": 2}
    assert http.post(endpoint + "/events", json=changed).status_code == 409


@pytest.mark.asyncio
async def test_v2_late_legacy_request_does_not_receive_new_capture(client, monkeypatch):
    http, _ = client
    rec = await create()  # A real pre-upgrade operational offer.
    monkeypatch.setattr(routes, "STORE_PROTOCOL_VERSION", 2)
    monkeypatch.setattr(routes, "_control", lambda owner: None)
    reply = http.post(f"/api/proactive/recommendations/{rec}/events", json={"event_id": "old-accept", "kind": "accepted"})
    assert reply.status_code == 200 and reply.json()["playback_grant"]
    assert reply.json()["telemetry"]["status"] == "discarded"
    assert reply.json()["telemetry"]["reason"] == "legacy_capture_missing"
