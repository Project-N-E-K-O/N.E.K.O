"""Regression coverage for the local resource access boundary."""

from types import SimpleNamespace

import pytest

from main_routers import capture_router, community_oauth
from main_routers.system_router import _shared
from utils.deployment import is_behind_proxy, uvicorn_proxy_options


@pytest.fixture(autouse=True)
def local_deployment(monkeypatch):
    for key in ("NEKO_BEHIND_PROXY", "NEKO_ACTIVITY_TRACKER_REMOTE", "ACTIVITY_TRACKER_REMOTE"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [("1", True), (" TRUE ", True), ("yes", True),
                                           ("on", False), ("false", False), ("", False)])
def test_proxy_flag_preserves_startup_semantics(value, expected, monkeypatch):
    monkeypatch.setenv("NEKO_BEHIND_PROXY", value)
    assert is_behind_proxy() is expected


@pytest.mark.unit
@pytest.mark.parametrize("chain", ["127.0.0.1, 203.0.113.9", "203.0.113.9, 127.0.0.1"])
def test_uvicorn_loopback_proxy_trust_preserves_external_peer(chain):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app = FastAPI()

    @app.get("/peer")
    async def peer(request: Request):
        return {"host": request.client.host}

    with TestClient(ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1,::1"),
                    client=("127.0.0.1", 50000)) as client:
        response = client.get("/peer", headers={"X-Forwarded-For": chain})
    assert response.json() == {"host": "203.0.113.9"}


@pytest.mark.unit
@pytest.mark.parametrize("proxy", [False, True])
def test_merged_uvicorn_options_override_environment_defaults(proxy, monkeypatch):
    from uvicorn import Config

    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true" if proxy else "false")
    config = Config(app=lambda: None, **uvicorn_proxy_options())
    assert config.proxy_headers is True
    assert config.forwarded_allow_ips == "127.0.0.1,::1"


@pytest.mark.unit
@pytest.mark.parametrize("check", [
    community_oauth._loopback_request_source,
])
@pytest.mark.parametrize("deployment", ["NEKO_BEHIND_PROXY", "NEKO_ACTIVITY_TRACKER_REMOTE", "ACTIVITY_TRACKER_REMOTE"])
def test_oauth_status_rejects_remote_deployments(check, deployment, monkeypatch):
    monkeypatch.setenv(deployment, "true")
    for headers in ({}, {"x-forwarded-for": "127.0.0.1"}, {"cf-connecting-ip": "203.0.113.9"}):
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers=headers)
        assert check(request) is False


@pytest.mark.unit
@pytest.mark.parametrize("check", [capture_router._is_loopback_request, _shared._is_loopback_request])
@pytest.mark.parametrize("deployment", ["NEKO_BEHIND_PROXY", "NEKO_ACTIVITY_TRACKER_REMOTE", "ACTIVITY_TRACKER_REMOTE"])
def test_other_consumers_preserve_loopback_access_in_remote_deployments(check, deployment, monkeypatch):
    monkeypatch.setenv(deployment, "true")
    assert check(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})) is True
    assert check(SimpleNamespace(client=SimpleNamespace(host="203.0.113.9"), headers={})) is False


@pytest.mark.unit
@pytest.mark.parametrize("deployment", ["NEKO_BEHIND_PROXY", "NEKO_ACTIVITY_TRACKER_REMOTE"])
def test_avatar_upload_preflight_and_route_share_peer_policy(deployment, monkeypatch):
    from app.main_server import _avatar_tool_multipart_preflight
    from fastapi import HTTPException, Request
    from main_routers.cookies_login_router import verify_local_access

    monkeypatch.setenv(deployment, "true")
    # Isolate peer authorization from the independently tested CSRF gate.
    monkeypatch.setattr(_shared, "_validate_local_mutation_request", lambda _request: None)
    scope = {"type": "http", "headers": [], "client": ("127.0.0.1", 50000)}
    assert _avatar_tool_multipart_preflight(scope) is None
    verify_local_access(Request(scope))
    scope["client"] = ("203.0.113.9", 50000)
    assert _avatar_tool_multipart_preflight(scope).status_code == 403
    with pytest.raises(HTTPException):
        verify_local_access(Request(scope))


@pytest.mark.unit
@pytest.mark.parametrize("header", ["x-forwarded-for", "x-real-ip", "forwarded"])
@pytest.mark.parametrize("check", [capture_router._is_loopback_request, _shared._is_loopback_request])
def test_proxy_rewritten_peers_cannot_authorize_local_resources(header, check, monkeypatch):
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={header: "127.0.0.1"})
    assert check(request) is False
    monkeypatch.delenv("NEKO_BEHIND_PROXY")
    assert check(request) is True


@pytest.mark.unit
@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1", "::ffff:7f00:1"])
def test_desktop_mode_uses_processed_loopback_peer_with_local_proxy_headers(host):
    request = SimpleNamespace(client=SimpleNamespace(host=host), headers={"x-forwarded-for": "127.0.0.1"})
    assert community_oauth._loopback_request_source(request) is True


@pytest.mark.unit
@pytest.mark.parametrize("host", ["203.0.113.9", "::ffff:203.0.113.9", "invalid", ""])
def test_nonlocal_peers_cannot_spoof_local_access(host):
    request = SimpleNamespace(client=SimpleNamespace(host=host), headers={"x-forwarded-for": "127.0.0.1"})
    assert community_oauth._loopback_request_source(request) is False


@pytest.mark.unit
@pytest.mark.parametrize("forwarded,expected", [("127.0.0.1", True), ("203.0.113.9", False)])
def test_desktop_proxy_middleware_preserves_local_resource_boundary(forwarded, expected):
    """Local debugging proxies work; HTTP tunnels cannot become local consumers."""
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app = FastAPI()

    @app.get("/access")
    async def access(request: Request):
        return {
            "capture": capture_router._is_loopback_request(request),
            "system": _shared._is_loopback_request(request),
        }

    client = TestClient(
        ProxyHeadersMiddleware(app, trusted_hosts=uvicorn_proxy_options()["forwarded_allow_ips"]),
        client=("127.0.0.1", 50000),
    )
    assert client.get("/access", headers={"X-Forwarded-For": forwarded}).json() == {
        "capture": expected, "system": expected,
    }
