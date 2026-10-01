from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from plugin.server.infrastructure import mutation_auth
from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.routes import plugins as route_module
from plugin.server.routes.security import router as security_router
from utils.host_origin_guard import HostOriginGuardMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


pytestmark = pytest.mark.plugin_unit


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Small app that uses the production routes and the real access guard."""
    monkeypatch.setattr(route_module, "registration_for_plugin_sync", lambda _plugin_id: None)
    monkeypatch.setattr(route_module, "list_registration_records_sync", lambda: [])
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(route_module.router)
    app.include_router(security_router)
    app.add_middleware(HostOriginGuardMiddleware)
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=mutation_auth.TRUSTED_PROXY_IPS)
    return app


def _client(
    app: FastAPI,
    *,
    peer: str = "127.0.0.1",
    host: str = "127.0.0.1:48916",
    headers: dict[str, str] | None = None,
):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 1234)),
        base_url=f"http://{host}",
        headers=headers or {},
    )


def _valid_headers() -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{mutation_auth.MAIN_SERVER_PORT}",
        "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN,
    }


def test_non_ascii_token_is_rejected_without_compare_digest_error() -> None:
    request = SimpleNamespace(headers={"X-CSRF-Token": "é"})
    assert mutation_auth._valid_token(request) is False


@pytest.mark.parametrize("raw,expected", [
    ("http://[fd00:0:0:0:0:0:0:5]:8080", "http://[fd00::5]:8080"),
    ("https://bücher.example", "https://xn--bcher-kva.example:443"),
    ("http://nas.example:0", ""),
])
def test_origin_uses_host_guard_canonical_hostname(raw, expected):
    assert mutation_auth._normalize_origin(raw) == expected


LIFECYCLE_MUTATIONS = [
    ("post", "/plugin/demo/start"),
    ("post", "/plugin/demo/stop"),
    ("post", "/plugin/demo/refresh"),
    ("post", "/plugin/demo/reload"),
    ("delete", "/plugin/demo"),
    ("post", "/plugins/refresh"),
    ("post", "/plugins/reload"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", LIFECYCLE_MUTATIONS)
async def test_foreign_origin_is_rejected_before_lifecycle_side_effects(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    ensure = AsyncMock(return_value=True)
    monkeypatch.setattr(route_module, "ensure_plugin_messaging_started", ensure)
    for service in (route_module.lifecycle_service, route_module.registry_service):
        for name in (
            "start_plugin", "stop_plugin", "reload_plugin", "delete_plugin",
            "refresh_plugin", "refresh_registry", "reload_all_plugins",
        ):
            if hasattr(service, name):
                monkeypatch.setattr(service, name, AsyncMock())

    headers = {**_valid_headers(), "Origin": "https://evil.example"}
    async with _client(app, headers=headers) as client:
        response = await getattr(client, method)(path)

    assert response.status_code == 403
    assert response.headers.get("X-Error-Code") == "csrf_validation_failed"
    assert response.headers.get("X-CSRF-Failure") == "origin"
    ensure.assert_not_awaited()
    for service in (route_module.lifecycle_service, route_module.registry_service):
        for name in (
            "start_plugin", "stop_plugin", "reload_plugin", "delete_plugin",
            "refresh_plugin", "refresh_registry", "reload_all_plugins",
        ):
            candidate = getattr(service, name, None)
            if isinstance(candidate, AsyncMock):
                candidate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null", "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
        {"Origin": "not a URL", "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
        {"Origin": "http://127.0.0.1:49999", "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
        {
            "Origin": f"http://127.0.0.1:{mutation_auth.MAIN_SERVER_PORT}/path",
            "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN,
        },
        {"Referer": "https://evil.example/page", "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
    ],
)
async def test_invalid_browser_provenance_is_rejected(app: FastAPI, headers: dict[str, str]) -> None:
    async with _client(app, headers=headers) as client:
        response = await client.post("/plugin/demo/stop")
    assert response.status_code == 403
    assert response.headers.get("X-Error-Code") == "csrf_validation_failed"
    assert response.json()["detail"]["error_code"] == "csrf_validation_failed"


@pytest.mark.asyncio
async def test_valid_origin_and_token_reach_lifecycle_service(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    stop = AsyncMock(return_value={"success": True, "plugin_id": "demo"})
    ensure = AsyncMock(return_value=True)
    monkeypatch.setattr(route_module.lifecycle_service, "stop_plugin", stop)
    monkeypatch.setattr(route_module, "ensure_plugin_messaging_started", ensure)
    async with _client(app, headers=_valid_headers()) as client:
        response = await client.post("/plugin/demo/stop")
    assert response.status_code == 200
    stop.assert_awaited_once_with("demo", persist_user_intent=True)
    ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_token_bootstrap_is_uncached_and_rejects_foreign_origin(app: FastAPI) -> None:
    async with _client(app, headers={"Origin": "https://evil.example"}) as client:
        response = await client.get("/security/csrf-token")
    assert response.status_code == 403

    async with _client(app, headers={"Origin": f"http://127.0.0.1:{mutation_auth.MAIN_SERVER_PORT}"}) as client:
        response = await client.get("/security/csrf-token")
    assert response.status_code == 200
    assert response.json()["csrf_token"] == mutation_auth.AUTOSTART_CSRF_TOKEN
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["pragma"] == "no-cache"


@pytest.mark.asyncio
async def test_token_bootstrap_accepts_same_origin_referer_with_path(app: FastAPI) -> None:
    async with _client(
        app,
        headers={"Referer": f"http://127.0.0.1:{mutation_auth.MAIN_SERVER_PORT}/ui/plugins"},
    ) as client:
        response = await client.get("/security/csrf-token")
    assert response.status_code == 200
    assert response.json()["csrf_token"] == mutation_auth.AUTOSTART_CSRF_TOKEN


@pytest.mark.asyncio
async def test_token_bootstrap_rejects_referer_only_foreign_request(app: FastAPI) -> None:
    async with _client(
        app,
        headers={"Referer": "https://evil.example/page"},
    ) as client:
        response = await client.get("/security/csrf-token")
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, mutation_auth.AUTOSTART_CSRF_TOKEN], ids=["without-token", "with-token"])
async def test_originless_loopback_native_call_with_optional_token_remains_supported(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    token: str | None,
) -> None:
    stop = AsyncMock(return_value={"success": True, "plugin_id": "demo"})
    monkeypatch.setattr(route_module.lifecycle_service, "stop_plugin", stop)
    headers = {} if token is None else {"X-CSRF-Token": token}
    async with _client(
        app,
        headers=headers,
    ) as client:
        response = await client.post("/plugin/demo/stop")
    assert response.status_code == 200
    stop.assert_awaited_once_with("demo", persist_user_intent=True)


@pytest.mark.asyncio
async def test_originless_loopback_native_call_without_browser_metadata_is_supported(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = AsyncMock(return_value={"success": True, "plugin_id": "demo"})
    monkeypatch.setattr(route_module.lifecycle_service, "stop_plugin", stop)
    async with _client(app) as client:
        response = await client.post("/plugin/demo/stop")
    assert response.status_code == 200
    stop.assert_awaited_once_with("demo", persist_user_intent=True)


@pytest.mark.asyncio
async def test_foreign_origin_on_lan_and_untrusted_hostname_are_rejected(app: FastAPI) -> None:
    async with _client(app, host="192.168.1.5:48911", peer="192.168.1.10", headers=_valid_headers()) as client:
        response = await client.post("/plugin/demo/stop")
    assert response.status_code == 403

    async with _client(app, host="example.test:48916", headers=_valid_headers()) as client:
        response = await client.post("/plugin/demo/stop")
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme,host,peer,forwarded", [
    ("http", "192.168.1.5:48911", "192.168.1.10", False),
    ("http", "192.168.1.5:1081", "127.0.0.1", True),
    ("https", "192.168.1.5:48912", "127.0.0.1", True),
    ("https", "localhost:48912", "127.0.0.1", True),
    ("https", "[fd00::5]:8443", "::1", True),
])
@pytest.mark.parametrize("method,path", LIFECYCLE_MUTATIONS)
async def test_nas_token_bootstrap_and_lifecycle_without_extra_configuration(
    app, monkeypatch, scheme, host, peer, forwarded, method, path,
):
    headers = {"Referer": f"{scheme}://{host}/ui/plugins"}
    if forwarded:
        headers.update({"X-Forwarded-Proto": scheme, "X-Forwarded-For": "192.168.1.10"})
    actions = []
    for service in (route_module.lifecycle_service, route_module.registry_service):
        for name in ("start_plugin", "stop_plugin", "reload_plugin", "delete_plugin",
                     "refresh_plugin", "refresh_registry", "reload_all_plugins"):
            if hasattr(service, name):
                action = AsyncMock(return_value={"success": True})
                monkeypatch.setattr(service, name, action)
                actions.append(action)
    monkeypatch.setattr(route_module, "ensure_plugin_messaging_started", AsyncMock(return_value=True))
    async with _client(app, host=host, peer=peer, headers=headers) as client:
        bootstrap = await client.get("/security/csrf-token")
        assert bootstrap.status_code == 200, bootstrap.text
        assert "no-store" in bootstrap.headers["cache-control"]
        response = await getattr(client, method)(path, headers={
            "Origin": f"{scheme}://{host}", "X-CSRF-Token": bootstrap.json()["csrf_token"],
        })
    assert response.status_code == 200, response.text
    assert sum(action.await_count for action in actions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    {"Origin": "http://192.168.1.5:9999"},
    {"Origin": "https://evil.example"},
    {"Origin": "http://192.168.1.5:48911", "X-CSRF-Token": "wrong"},
    {"Origin": "http://192.168.1.5:48911"},
    {"X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
])
async def test_nas_rejects_foreign_missing_or_invalid_browser_credentials(app, monkeypatch, headers):
    stop = AsyncMock()
    monkeypatch.setattr(route_module.lifecycle_service, "stop_plugin", stop)
    async with _client(app, host="192.168.1.5:48911", peer="192.168.1.10", headers=headers) as client:
        response = await client.post("/plugin/demo/stop")
    assert response.status_code == 403
    stop.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["https://evil.example", "http://192.168.1.6:9999"])
async def test_nas_does_not_expose_token_to_foreign_origins(app, origin):
    async with _client(app, host="192.168.1.5:48911", peer="192.168.1.10") as client:
        for headers in ({"Origin": origin}, {"Referer": f"{origin}/page"}):
            assert (await client.get("/security/csrf-token", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_untrusted_peer_cannot_enter_native_path_with_forged_proxy_client(app):
    async with _client(app, host="192.168.1.5:48912", peer="192.168.1.10", headers={
        "X-Forwarded-Proto": "https",
        "X-Forwarded-For": "127.0.0.1", "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN,
    }) as client:
        assert (await client.get("/security/csrf-token")).status_code == 403
        assert (await client.post("/plugin/demo/stop")).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["wrong", ""])
async def test_originless_native_call_rejects_supplied_invalid_token(app, monkeypatch, token):
    stop = AsyncMock()
    monkeypatch.setattr(route_module.lifecycle_service, "stop_plugin", stop)
    async with _client(app, headers={"X-CSRF-Token": token}) as client:
        assert (await client.post("/plugin/demo/stop")).status_code == 403
    stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_development_origin_requires_explicit_opt_in(app, monkeypatch):
    monkeypatch.setenv("NEKO_PLUGIN_MUTATION_ALLOWED_ORIGINS", "")
    monkeypatch.setattr(mutation_auth, "AUTOSTART_ALLOWED_ORIGINS", ())
    async with _client(app, headers={"Origin": "http://localhost:5173"}) as client:
        assert (await client.get("/security/csrf-token")).status_code == 403
        monkeypatch.setenv("NEKO_PLUGIN_MUTATION_ALLOWED_ORIGINS", "http://localhost:5173")
        assert (await client.get("/security/csrf-token")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("fetch_site,status", [("same-origin", 200), ("same-site", 403), ("cross-site", 403)])
async def test_nas_bootstrap_without_referer_requires_same_origin_metadata(app, fetch_site, status):
    async with _client(app, host="192.168.1.5:48911", peer="192.168.1.10",
                       headers={"Sec-Fetch-Site": fetch_site}) as client:
        assert (await client.get("/security/csrf-token")).status_code == status


@pytest.mark.asyncio
async def test_nas_originless_bootstrap_cannot_use_local_native_compatibility(app):
    async with _client(app, host="192.168.1.5:48911", peer="192.168.1.10") as client:
        assert (await client.get("/security/csrf-token")).status_code == 403


@pytest.mark.asyncio
async def test_trusted_nas_domain_remains_supported(app, monkeypatch):
    monkeypatch.setenv("NEKO_TRUSTED_HOSTS", "nas.example.test")
    # Host trust is captured when constructing the middleware stack.
    async with _client(app, host="nas.example.test:48912", peer="127.0.0.1", headers={
        "Referer": "https://nas.example.test:48912/ui/plugins",
        "X-Forwarded-Proto": "https", "X-Forwarded-For": "192.168.1.10",
    }) as client:
        assert (await client.get("/security/csrf-token")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("host,origin", [
    ("192.168.1.5:80", "https://192.168.1.5:8443"),
    ("nas.example.test", "https://nas.example.test"),
    ("192.168.1.5:48911", "http://192.168.1.5:9999"),
])
async def test_nas_hostname_fallback_bootstrap_and_mutation(app, monkeypatch, host, origin):
    monkeypatch.setenv("NEKO_TRUSTED_HOSTS", "nas.example.test")
    stop = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(route_module.lifecycle_service, "stop_plugin", stop)
    async with _client(app, host=host, peer="127.0.0.1", headers={
        "Referer": f"{origin}/ui/plugins", "X-Forwarded-Proto": "http",
        "X-Forwarded-For": "192.168.1.10",
    }) as client:
        bootstrap = await client.get("/security/csrf-token")
        assert bootstrap.status_code == 200
        denied = await client.post("/plugin/demo/stop", headers={"Origin": origin})
        assert denied.status_code == 403
        assert denied.headers["X-CSRF-Failure"] == "token"
        accepted = await client.post("/plugin/demo/stop", headers={
            "Origin": origin, "X-CSRF-Token": bootstrap.json()["csrf_token"],
        })
        assert accepted.status_code == 200
    stop.assert_awaited_once()


# ── Package import routes ────────────────────────────────────────────────
#
# Uploading or installing a package writes executable plugin code, so these
# routes share the lifecycle contract. Unlike lifecycle routes they carry a
# body, and FastAPI parses bodies before route dependencies, so the guard must
# reject before the multipart/JSON body is read at all.

PACKAGE_IMPORT_ROUTES = [
    "/plugin-cli/upload",
    "/plugin-cli/upload-and-install",
    "/plugin-cli/upload-and-unpack",
    "/plugin-cli/install",
    "/plugin-cli/unpack",
]
_MULTIPART_ROUTES = {"/plugin-cli/upload", "/plugin-cli/upload-and-install", "/plugin-cli/upload-and-unpack"}
_UPLOAD_RESULT = {
    "name": "demo.neko-plugin",
    "path": "demo.neko-plugin",
    "size_bytes": 3,
    "modified_at": "2026-01-01T00:00:00Z",
}
_INSTALL_RESULT = {
    "package_path": "demo.neko-plugin",
    "package_type": "plugin",
    "package_id": "demo",
    "plugins_root": "plugins",
    "installed_plugins": [],
    "metadata_found": True,
    "conflict_strategy": "fail",
    "installed_plugin_count": 0,
}


class _BodyReadTracker:
    """Outermost ASGI wrapper that records whether the request body was read."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.body_reads = 0

    async def __call__(self, scope, receive, send) -> None:
        async def tracked_receive():
            message = await receive()
            if message["type"] == "http.request":
                self.body_reads += 1
            return message

        await self.app(scope, tracked_receive, send)


@pytest.fixture
def package_app(monkeypatch: pytest.MonkeyPatch, tmp_path) -> _BodyReadTracker:
    """Production plugin-cli routes; uploads land in a temporary artifacts root."""
    import plugin.settings as plugin_settings
    from plugin.server.routes import plugin_cli as plugin_cli_routes

    monkeypatch.setattr(plugin_settings, "USER_PLUGIN_PACKAGES_ROOT", tmp_path / "packages")
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(plugin_cli_routes.router)
    app.include_router(security_router)
    app.add_middleware(HostOriginGuardMiddleware)
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=mutation_auth.TRUSTED_PROXY_IPS)
    return _BodyReadTracker(app)


@pytest.fixture
def package_actions(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    """Replace install side effects; uploads still write to the temp root."""
    from plugin.server.routes import plugin_cli as plugin_cli_routes

    actions = {
        "upload_and_install": AsyncMock(return_value={"upload": _UPLOAD_RESULT, "install": _INSTALL_RESULT}),
        "install": AsyncMock(return_value=_INSTALL_RESULT),
    }
    for name, action in actions.items():
        monkeypatch.setattr(plugin_cli_routes.service, name, action)
    return actions


def _uploaded_packages(tmp_path) -> list[str]:
    root = tmp_path / "packages"
    return sorted(item.name for item in root.iterdir()) if root.exists() else []


async def _post_package(client: httpx.AsyncClient, path: str, headers: dict[str, str] | None = None):
    if path in _MULTIPART_ROUTES:
        return await client.post(
            path,
            files={"file": ("demo.neko-plugin", b"pkg", "application/octet-stream")},
            headers=headers,
        )
    return await client.post(path, json={"package": "demo.neko-plugin"}, headers=headers)


def test_package_import_routes_use_pre_body_guard() -> None:
    """Pin the guarded set: a new alias registered with @router.post would be open."""
    from plugin.server.routes import plugin_cli as plugin_cli_routes

    guarded = {
        (method, route.path)
        for route in plugin_cli_routes.router.routes
        if isinstance(route, mutation_auth.PluginMutationGuardedRoute)
        for method in route.methods
    }
    assert guarded == {("POST", path) for path in PACKAGE_IMPORT_ROUTES}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PACKAGE_IMPORT_ROUTES)
@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "https://evil.example", "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
    {"Origin": "null"},
    {"Origin": "http://127.0.0.1:49999"},
], ids=["foreign", "foreign-with-token", "null", "other-local-port"])
async def test_cross_site_package_import_is_rejected_before_body_is_read(
    package_app, package_actions, tmp_path, path, headers,
) -> None:
    async with _client(package_app, headers=headers) as client:
        response = await _post_package(client, path)
    assert response.status_code == 403
    assert response.headers.get("X-CSRF-Failure") == "origin"
    assert response.json()["detail"]["error_code"] == "csrf_validation_failed"
    assert package_app.body_reads == 0
    assert _uploaded_packages(tmp_path) == []
    for action in package_actions.values():
        action.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/plugin-cli/install", "/plugin-cli/unpack"])
async def test_cross_site_untyped_json_install_is_rejected(package_app, package_actions, path) -> None:
    # fetch(url, {method: "POST", mode: "no-cors", body: new Blob([json])})
    # sends no Content-Type and no preflight; FastAPI would parse it as JSON.
    async with _client(package_app, headers={"Origin": "https://evil.example"}) as client:
        response = await client.post(path, content=b'{"package": "demo.neko-plugin"}')
    assert response.status_code == 403
    assert package_app.body_reads == 0
    for action in package_actions.values():
        action.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PACKAGE_IMPORT_ROUTES)
@pytest.mark.parametrize("token", [None, "wrong", ""], ids=["missing", "wrong", "empty"])
async def test_package_import_requires_valid_token_from_trusted_origin(
    package_app, package_actions, tmp_path, path, token,
) -> None:
    headers = {"Origin": f"http://127.0.0.1:{mutation_auth.MAIN_SERVER_PORT}"}
    if token is not None:
        headers["X-CSRF-Token"] = token
    async with _client(package_app, headers=headers) as client:
        response = await _post_package(client, path)
    assert response.status_code == 403
    assert response.headers.get("X-CSRF-Failure") == "token"
    assert response.json()["detail"]["csrf_failure"] == "token"
    assert package_app.body_reads == 0
    assert _uploaded_packages(tmp_path) == []
    for action in package_actions.values():
        action.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PACKAGE_IMPORT_ROUTES)
async def test_trusted_origin_with_token_imports_package(package_app, package_actions, tmp_path, path) -> None:
    async with _client(package_app, headers=_valid_headers()) as client:
        response = await _post_package(client, path)
    assert response.status_code == 200, response.text
    assert package_app.body_reads > 0
    if path == "/plugin-cli/upload":
        assert _uploaded_packages(tmp_path) == ["demo.neko-plugin"]
    elif path in _MULTIPART_ROUTES:
        assert _uploaded_packages(tmp_path) == ["demo.neko-plugin"]
        package_actions["upload_and_install"].assert_awaited_once()
    else:
        package_actions["install"].assert_awaited_once()
        assert package_actions["install"].await_args.kwargs["package"] == "demo.neko-plugin"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PACKAGE_IMPORT_ROUTES)
@pytest.mark.parametrize("token", [None, "valid"], ids=["without-token", "with-token"])
async def test_originless_loopback_native_package_import_remains_supported(
    package_app, package_actions, path, token,
) -> None:
    headers = {} if token is None else {"X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN}
    async with _client(package_app, headers=headers) as client:
        response = await _post_package(client, path)
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PACKAGE_IMPORT_ROUTES)
@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"},
    {"Referer": "https://evil.example/page"},
    {"X-CSRF-Token": "wrong"},
], ids=["fetch-metadata", "referer", "wrong-token"])
async def test_originless_package_import_rejects_browser_metadata_and_bad_token(
    package_app, package_actions, tmp_path, path, headers,
) -> None:
    async with _client(package_app, headers=headers) as client:
        response = await _post_package(client, path)
    assert response.status_code == 403
    assert package_app.body_reads == 0
    assert _uploaded_packages(tmp_path) == []
    for action in package_actions.values():
        action.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme,host,peer,forwarded", [
    ("http", "192.168.1.5:48911", "192.168.1.10", False),
    ("http", "192.168.1.5:1081", "127.0.0.1", True),
    ("https", "192.168.1.5:48912", "127.0.0.1", True),
    ("https", "[fd00::5]:8443", "::1", True),
])
@pytest.mark.parametrize("path", PACKAGE_IMPORT_ROUTES)
async def test_nas_package_import_without_extra_configuration(
    package_app, package_actions, scheme, host, peer, forwarded, path,
) -> None:
    headers = {"Referer": f"{scheme}://{host}/ui/plugins"}
    if forwarded:
        headers.update({"X-Forwarded-Proto": scheme, "X-Forwarded-For": "192.168.1.10"})
    async with _client(package_app, host=host, peer=peer, headers=headers) as client:
        bootstrap = await client.get("/security/csrf-token")
        assert bootstrap.status_code == 200, bootstrap.text
        denied = await _post_package(client, path, headers={"Origin": f"{scheme}://{host}"})
        assert denied.status_code == 403
        assert denied.headers["X-CSRF-Failure"] == "token"
        assert package_app.body_reads == 0
        accepted = await _post_package(client, path, headers={
            "Origin": f"{scheme}://{host}", "X-CSRF-Token": bootstrap.json()["csrf_token"],
        })
    assert accepted.status_code == 200, accepted.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PACKAGE_IMPORT_ROUTES)
@pytest.mark.parametrize("headers", [
    {},
    {"X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
    {"Origin": "http://192.168.1.6:9999", "X-CSRF-Token": mutation_auth.AUTOSTART_CSRF_TOKEN},
], ids=["originless", "originless-with-token", "foreign-lan-origin"])
async def test_nas_rejects_originless_or_foreign_package_import(
    package_app, package_actions, tmp_path, path, headers,
) -> None:
    async with _client(package_app, host="192.168.1.5:48911", peer="192.168.1.10", headers=headers) as client:
        response = await _post_package(client, path)
    assert response.status_code == 403
    assert package_app.body_reads == 0
    assert _uploaded_packages(tmp_path) == []
    for action in package_actions.values():
        action.assert_not_awaited()
