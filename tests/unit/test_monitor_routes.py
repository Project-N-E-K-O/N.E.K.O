"""Real ASGI route checks for Monitor authentication and broadcast isolation."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import monitor
from app import monitor_auth


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "route-secret")
    monitor.connected_clients.clear()
    monitor.subtitle_clients.clear()
    monkeypatch.setattr(monitor, "current_subtitle", "private subtitle")
    # Do not start background cleanup; these tests exercise route ownership.
    with TestClient(monitor.app) as test_client:
        yield test_client
    monitor.connected_clients.clear()
    monitor.subtitle_clients.clear()


@pytest.mark.parametrize("path", ["/subtitle_ws", "/ws/neko", "/sync/neko", "/sync_binary/neko"])
@pytest.mark.parametrize("query", ["", "?token=wrong"])
def test_rejected_websocket_has_no_state_or_broadcast(client, monkeypatch, path, query):
    broadcasts = []
    async def record(*args):
        broadcasts.append(args)
    monkeypatch.setattr(monitor, "broadcast_message", record)
    monkeypatch.setattr(monitor, "broadcast_binary", record)
    with client.websocket_connect(path + query) as websocket:
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()
        assert closed.value.code == 1008
    assert not monitor.connected_clients
    assert not monitor.subtitle_clients
    assert not broadcasts
    assert monitor.current_subtitle == "private subtitle"


@pytest.mark.parametrize("path", ["/subtitle", "/neko", "/api/config/page_config", "/api/config/preferences", "/api/live2d/emotion_mapping/neko"])
def test_http_business_routes_reject_missing_or_wrong_token(client, path):
    assert client.get(path).status_code == 401
    response = client.get(path, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert "route-secret" not in response.text


def test_preferences_whitelist_and_header_auth(client, monkeypatch):
    async def preferences():
        return [{"model_path": "model", "position": {"x": 1}, "scale": 2, "secret": "hidden"}]
    monkeypatch.setattr(monitor, "aload_user_preferences", preferences)
    response = client.get("/api/config/preferences", headers={"Authorization": "Bearer route-secret"})
    assert response.status_code == 200
    assert response.json() == [{"model_path": "model", "position": {"x": 1}, "scale": 2}]


def test_authorized_sync_broadcasts_to_authorized_viewer(client):
    with client.websocket_connect("/ws/neko?token=route-secret") as viewer:
        with client.websocket_connect("/sync/neko", headers={"Authorization": "Bearer route-secret"}) as sync:
            message = {"type": "chat", "text": "authorized"}
            sync.send_json(message)
            assert viewer.receive_json() == message


def test_authorized_binary_sync_broadcasts_to_authorized_viewer(client):
    with client.websocket_connect("/ws/neko?token=route-secret") as viewer:
        with client.websocket_connect("/sync_binary/neko?token=route-secret") as sync:
            sync.send_bytes(b"audio bytes")
            assert viewer.receive_bytes() == b"audio bytes"


def test_subtitle_requires_auth_before_current_subtitle(client):
    with client.websocket_connect("/subtitle_ws?token=route-secret") as websocket:
        assert websocket.receive_json() == {"type": "subtitle", "text": "private subtitle"}


@pytest.mark.parametrize("path", ["/subtitle_ws", "/ws/neko", "/sync/neko", "/sync_binary/neko"])
def test_unconfigured_token_keeps_websocket_compatibility(client, monkeypatch, path):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "")
    with client.websocket_connect(path):
        pass
