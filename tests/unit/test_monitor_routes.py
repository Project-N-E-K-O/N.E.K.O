"""Real ASGI route checks for Monitor authentication and broadcast isolation."""
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import monitor
from app import monitor_auth

COOKIE = monitor_auth.VIEWER_SESSION_COOKIE


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "route-secret")
    monitor.connected_clients.clear()
    monitor.subtitle_clients.clear()
    monkeypatch.setattr(monitor, "current_subtitle", "private subtitle")
    with TestClient(monitor.app) as test_client:
        yield test_client
    monitor.connected_clients.clear()
    monitor.subtitle_clients.clear()


def _session_headers(origin=None):
    headers = {"Cookie": f"{COOKIE}={monitor_auth.issue_viewer_session()}"}
    if origin:
        headers["Origin"] = origin
    return headers


def _assert_ws_rejected(client, path, headers=None):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(path, headers=headers or {}):
            pass
    assert closed.value.code == 1008


@pytest.mark.parametrize("path", ["/subtitle_ws", "/ws/neko", "/sync/neko", "/sync_binary/neko"])
@pytest.mark.parametrize("query", ["", "?token=wrong"])
def test_rejected_websocket_has_no_state_or_broadcast(client, monkeypatch, path, query):
    broadcasts = []
    async def record(*args):
        broadcasts.append(args)
    monkeypatch.setattr(monitor, "broadcast_message", record)
    monkeypatch.setattr(monitor, "broadcast_binary", record)
    _assert_ws_rejected(client, path + query)
    assert not monitor.connected_clients
    assert not monitor.subtitle_clients
    assert not broadcasts
    assert monitor.current_subtitle == "private subtitle"


@pytest.mark.parametrize("path", [
    "/subtitle", "/neko", "/api/config/page_config", "/api/config/preferences",
    "/api/live2d/emotion_mapping/neko",
    # No handler opts in: the middleware protects routes by default.
    "/openapi.json",
])
def test_http_routes_reject_missing_or_wrong_token(client, path):
    assert client.get(path).status_code == 401
    response = client.get(path, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert "route-secret" not in response.text


def test_static_assets_stay_public(client):
    assert client.get("/static/theme-manager.js").status_code == 200


def test_preferences_whitelist_and_header_auth(client, monkeypatch):
    async def preferences():
        return [{"model_path": "model", "position": {"x": 1}, "scale": 2, "secret": "hidden"}]
    monkeypatch.setattr(monitor, "aload_user_preferences", preferences)
    response = client.get("/api/config/preferences", headers={"Authorization": "Bearer route-secret"})
    assert response.status_code == 200
    assert response.json() == [{"model_path": "model", "position": {"x": 1}, "scale": 2}]


def test_query_token_page_load_redirects_to_token_free_url_with_session_cookie(client):
    response = client.get("/neko?lanlan_name=neko&token=route-secret", follow_redirects=False)
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.path == "/neko"
    assert parse_qs(location.query) == {"lanlan_name": ["neko"]}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{COOKIE}=")
    assert "route-secret" not in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert "Secure" not in set_cookie
    assert monitor_auth.verify_viewer_session(response.cookies[COOKIE])


def test_query_token_over_https_marks_session_cookie_secure(monkeypatch):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "route-secret")
    with TestClient(monitor.app, base_url="https://testserver") as https_client:
        response = https_client.get("/subtitle?token=route-secret", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/subtitle"
    assert "Secure" in response.headers["set-cookie"]


def test_session_cookie_authenticates_viewer_http_routes(client, monkeypatch):
    async def preferences():
        return []
    monkeypatch.setattr(monitor, "aload_user_preferences", preferences)
    response = client.get("/api/config/preferences", headers=_session_headers())
    assert response.status_code == 200


@pytest.mark.parametrize("cookie", ["route-secret", "not-a-session", "1.deadbeef"])
def test_raw_token_or_forged_cookie_is_not_a_session(client, cookie):
    headers = {"Cookie": f"{COOKIE}={cookie}"}
    assert client.get("/api/config/preferences", headers=headers).status_code == 401
    _assert_ws_rejected(client, "/subtitle_ws", headers)


def test_rotated_token_invalidates_existing_session(client, monkeypatch):
    headers = _session_headers()
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "rotated-secret")
    assert client.get("/api/config/preferences", headers=headers).status_code == 401


def test_authorized_sync_broadcasts_to_authorized_viewer(client):
    with client.websocket_connect("/ws/neko?token=route-secret") as viewer:
        with client.websocket_connect("/sync/neko", headers={"Authorization": "Bearer route-secret"}) as sync:
            message = {"type": "chat", "text": "authorized"}
            sync.send_json(message)
            assert viewer.receive_json() == message


def test_authorized_binary_sync_broadcasts_to_authorized_viewer(client):
    with client.websocket_connect("/ws/neko", headers=_session_headers("http://testserver")) as viewer:
        with client.websocket_connect("/sync_binary/neko?token=route-secret") as sync:
            sync.send_bytes(b"audio bytes")
            assert viewer.receive_bytes() == b"audio bytes"


def test_subtitle_requires_auth_before_current_subtitle(client):
    with client.websocket_connect("/subtitle_ws?token=route-secret") as websocket:
        assert websocket.receive_json() == {"type": "subtitle", "text": "private subtitle"}


@pytest.mark.parametrize("path", ["/sync/neko", "/sync_binary/neko"])
def test_viewer_session_cannot_reach_producer_routes(client, path):
    _assert_ws_rejected(client, path, _session_headers("http://testserver"))


@pytest.mark.parametrize("origin", ["http://evil.example", "http://testserver:48916"])
def test_session_websocket_rejects_cross_origin(client, origin):
    _assert_ws_rejected(client, "/ws/neko", _session_headers(origin))


@pytest.mark.parametrize("origin", [
    None,
    "http://testserver",
    # TLS terminated at a proxy: the browser Origin is https while the
    # upstream handshake is plain ws.
    "https://testserver",
])
def test_session_websocket_accepts_same_host(client, origin):
    with client.websocket_connect("/subtitle_ws", headers=_session_headers(origin)) as websocket:
        assert websocket.receive_json() == {"type": "subtitle", "text": "private subtitle"}


def test_explicit_token_is_not_blocked_by_cookie_origin_check(client):
    headers = _session_headers("http://testserver:48916")
    with client.websocket_connect("/subtitle_ws?token=route-secret", headers=headers) as websocket:
        assert websocket.receive_json() == {"type": "subtitle", "text": "private subtitle"}


@pytest.mark.parametrize("path", ["/subtitle_ws", "/ws/neko", "/sync/neko", "/sync_binary/neko"])
def test_unconfigured_token_keeps_websocket_compatibility(client, monkeypatch, path):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "")
    with client.websocket_connect(path):
        pass


def test_unconfigured_token_keeps_http_compatibility(client, monkeypatch):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "")
    async def preferences():
        return []
    monkeypatch.setattr(monitor, "aload_user_preferences", preferences)
    assert client.get("/api/config/preferences").status_code == 200
    # No token configured: a stray ?token= must not trigger the cookie exchange.
    assert client.get("/openapi.json?token=x", follow_redirects=False).status_code == 200


@pytest.fixture
def viewer_client(client, monkeypatch):
    monkeypatch.setattr(monitor_auth, "MONITOR_VIEWER_TOKEN", "viewer-secret")
    return client


def test_viewer_token_reads_viewer_routes(viewer_client):
    with viewer_client.websocket_connect("/subtitle_ws?token=viewer-secret") as websocket:
        assert websocket.receive_json() == {"type": "subtitle", "text": "private subtitle"}
    with viewer_client.websocket_connect("/ws/neko", headers={"Authorization": "Bearer viewer-secret"}):
        pass
    response = viewer_client.get("/neko?token=viewer-secret", follow_redirects=False)
    assert response.status_code == 303
    assert monitor_auth.verify_viewer_session(response.cookies[COOKIE])


@pytest.mark.parametrize("path", ["/sync/neko", "/sync_binary/neko"])
@pytest.mark.parametrize("transport", ["query", "header"])
def test_viewer_token_cannot_write_producer_routes(viewer_client, path, transport):
    if transport == "query":
        _assert_ws_rejected(viewer_client, path + "?token=viewer-secret")
    else:
        _assert_ws_rejected(viewer_client, path, {"Authorization": "Bearer viewer-secret"})


def test_rotating_viewer_token_invalidates_sessions(viewer_client, monkeypatch):
    headers = _session_headers()
    monkeypatch.setattr(monitor_auth, "MONITOR_VIEWER_TOKEN", "rotated-viewer")
    assert viewer_client.get("/api/config/preferences", headers=headers).status_code == 401


@pytest.mark.parametrize("raw_url, location", [
    # A decoded "#" must not truncate the character name.
    ("/Neko%232?token=route-secret", "/Neko%232"),
    ("/Neko%3Fx?token=route-secret&a=1", "/Neko%3Fx?a=1"),
    # Leading slashes would make a protocol-relative open redirect.
    ("//evil.example/?token=route-secret", "/evil.example/"),
    ("/%5Cevil.example?token=route-secret", "/%5Cevil.example"),
])
def test_query_token_redirect_keeps_raw_path_and_stays_on_site(client, raw_url, location):
    response = client.get("http://testserver" + raw_url, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == location


def test_lifespan_installs_log_redaction_for_any_launch_mode(client):
    import logging
    for name in ("uvicorn.access", "uvicorn.error"):
        assert any(isinstance(f, monitor_auth.MonitorQueryLogFilter) for f in logging.getLogger(name).filters)
