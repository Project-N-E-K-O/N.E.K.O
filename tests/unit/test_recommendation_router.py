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
