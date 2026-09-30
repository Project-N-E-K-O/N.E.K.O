from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient

from plugin.server import market_protocol_handler
from plugin.server.routes import market_bridge


pytestmark = pytest.mark.plugin_unit
CATALOG_URL = "https://market.invalid"
PACKAGE_URL = "https://github.com/example/demo/releases/download/v2.0.0/demo.neko-plugin"
PACKAGE_SHA256 = "a" * 64
PUBLISHED_AT = "2026-09-30T00:00:00Z"


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    state: dict[str, object] = {
        "releases": [{
            "version": "2.0.0",
            "channel": "stable",
            "package_url": PACKAGE_URL,
            "package_sha256": PACKAGE_SHA256,
            "payload_hash": "b" * 64,
            "created_at": PUBLISHED_AT,
            "yanked_at": None,
        }],
        "requests": [],
        "status": 200,
    }
    real_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if state.get("timeout"):
            raise httpx.ReadTimeout("catalog timeout", request=request)
        if state.get("invalid_json"):
            return httpx.Response(200, content=b"invalid json")
        return httpx.Response(
            state["status"],
            json=state["releases"] if request.url.path == "/api/v1/plugins/42/versions" else [],
            headers={"Location": "https://untrusted.invalid/versions"},
        )

    def client(**kwargs: object) -> httpx.AsyncClient:
        assert kwargs["follow_redirects"] is False
        assert kwargs["timeout"].connect == 3.0
        return real_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(market_bridge.httpx, "AsyncClient", client)
    monkeypatch.setattr(market_bridge, "MARKET_API_URL", CATALOG_URL)
    monkeypatch.setattr(market_bridge, "_tasks", {})
    monkeypatch.setattr(market_bridge, "_task_workers", {})
    return state


def request(**updates: object) -> market_bridge.MarketInstallRequest:
    return market_bridge.MarketInstallRequest(**{
        "plugin_id": "42",
        "version": "2.0.0",
        "package_url": PACKAGE_URL,
        "package_sha256": PACKAGE_SHA256,
        **updates,
    })


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[market_bridge.MarketInstallRequest]:
    payloads: list[market_bridge.MarketInstallRequest] = []

    async def execute(_task_id: str, payload: market_bridge.MarketInstallRequest) -> None:
        payloads.append(payload)

    monkeypatch.setattr(market_bridge, "_execute_install", execute)
    return payloads


@pytest.mark.asyncio
@pytest.mark.parametrize("updates", [
    {"package_url": "https://attacker.invalid/demo.neko-plugin", "package_sha256": "c" * 64},
    {"package_sha256": "c" * 64},
    {"package_url": "https://attacker.invalid/demo.neko-plugin", "canonical_package_url": PACKAGE_URL},
    {"package_url": "https://github.com/attacker/demo/releases/download/v2.0.0/demo.neko-plugin", "canonical_package_url": PACKAGE_URL},
    {"package_url": "https://unlisted-proxy.invalid/" + PACKAGE_URL, "canonical_package_url": PACKAGE_URL},
    {"package_url": "https://gh-proxy.com/" + PACKAGE_URL + "?different=1", "canonical_package_url": PACKAGE_URL},
    {"plugin_id": "43"},
    {"package_url": "https://gh-proxy.com/https://[malformed", "canonical_package_url": PACKAGE_URL},
    {"version": "3.0.0"},
    {"channel": "beta"},
    {"plugin_id": None},
    {"version": None},
    {"payload_hash": "c" * 64},
    {"published_at": "2020-01-01T00:00:00Z"},
])
async def test_install_rejects_unbound_requests_before_queueing(
    catalog: dict[str, object],
    queued: list[market_bridge.MarketInstallRequest],
    updates: dict[str, object],
) -> None:
    with pytest.raises(HTTPException) as exc:
        await market_bridge.market_install(request(**updates), token=market_bridge.get_bridge_token())

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "market_release_mismatch"
    assert not queued
    assert not market_bridge._tasks
    assert not market_bridge._task_workers


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["install", "upgrade", "reinstall", "override_builtin"])
async def test_all_install_modes_require_catalog_binding(
    catalog: dict[str, object],
    queued: list[market_bridge.MarketInstallRequest],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    manager = SimpleNamespace(find_active_user_entry=lambda _id: SimpleNamespace(channel="market", removed=False, root_id="user"))
    monkeypatch.setattr(market_bridge, "get_install_source_manager", lambda: manager)

    async def confirmation(_payload: object) -> SimpleNamespace:
        return SimpleNamespace(confirmation_token="confirmed", builtin_manifest_sha256="d" * 64)

    monkeypatch.setattr(market_bridge, "_build_market_override_confirmation", confirmation)
    with pytest.raises(HTTPException) as exc:
        await market_bridge.market_install(
            request(mode=mode, confirmation_token="confirmed", package_sha256="c" * 64),
            token=market_bridge.get_bridge_token(),
        )
    assert exc.value.detail["code"] == "market_release_mismatch"
    assert not queued
    assert not market_bridge._tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy", [None, *[base for name, base in market_bridge._GITHUB_PROXY_SOURCES if name != "github-direct"]])
@pytest.mark.parametrize("with_canonical", [False, True])
async def test_catalog_release_accepts_direct_and_supported_mirrors(
    catalog: dict[str, object],
    queued: list[market_bridge.MarketInstallRequest],
    proxy: str | None,
    with_canonical: bool,
) -> None:
    payload = request(
        package_url=f"{proxy or ''}{PACKAGE_URL}",
        canonical_package_url=PACKAGE_URL if with_canonical else None,
        package_sha256=PACKAGE_SHA256.upper(),
    )
    accepted = await market_bridge.market_install(payload, token=market_bridge.get_bridge_token())
    await market_bridge._task_workers[accepted.task_id]

    assert accepted.status == "pending"
    assert len(queued) == 1
    verified = queued[0]
    assert verified.package_url == payload.package_url
    assert verified.canonical_package_url == PACKAGE_URL
    assert verified.package_sha256 == PACKAGE_SHA256
    assert verified.payload_hash == "b" * 64
    assert verified.published_at == PUBLISHED_AT
    assert verified.channel == "stable"
    assert payload.payload_hash is None  # original caller data is not mutated
    [lookup] = catalog["requests"]
    assert str(lookup.url) == f"{CATALOG_URL}/api/v1/plugins/42/versions?channel=stable"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    {"status": 302}, {"status": 404}, {"status": 503}, {"timeout": True}, {"invalid_json": True},
])
async def test_catalog_failure_never_falls_back_to_caller_evidence(
    catalog: dict[str, object],
    queued: list[market_bridge.MarketInstallRequest],
    failure: dict[str, object],
) -> None:
    catalog.update(failure)
    with pytest.raises(HTTPException) as exc:
        await market_bridge.market_install(request(), token=market_bridge.get_bridge_token())
    assert exc.value.status_code == 502
    assert exc.value.detail["code"] == "market_catalog_unavailable"
    assert not queued
    assert not market_bridge._tasks


@pytest.mark.asyncio
async def test_missing_catalog_rejects_install(
    catalog: dict[str, object], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market_bridge, "MARKET_API_URL", "")
    with pytest.raises(HTTPException) as exc:
        await market_bridge.market_install(request(), token=market_bridge.get_bridge_token())
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "market_catalog_not_configured"
    assert not catalog["requests"]
    assert not market_bridge._tasks


@pytest.mark.asyncio
async def test_yanked_release_cannot_be_installed(catalog: dict[str, object]) -> None:
    catalog["releases"][0]["yanked_at"] = PUBLISHED_AT
    with pytest.raises(HTTPException) as exc:
        await market_bridge.market_install(request(), token=market_bridge.get_bridge_token())
    assert exc.value.detail["code"] == "market_release_mismatch"
    assert not market_bridge._tasks


@pytest.mark.asyncio
async def test_invalid_bridge_token_does_not_query_catalog(catalog: dict[str, object]) -> None:
    with pytest.raises(HTTPException) as exc:
        await market_bridge.market_install(request(), token="invalid-token")
    assert exc.value.status_code == 403
    assert not catalog["requests"]


def test_external_protocol_link_cannot_install_non_catalog_package(
    catalog: dict[str, object],
    queued: list[market_bridge.MarketInstallRequest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(market_bridge.router)
    # The factory installed by catalog uses MockTransport only for Market
    # lookups. Route the protocol handler's local POST through the real ASGI app.
    catalog_client: Callable[..., httpx.AsyncClient] = market_bridge.httpx.AsyncClient

    def client(**kwargs: object) -> httpx.AsyncClient:
        if "follow_redirects" in kwargs:
            return catalog_client(**kwargs)
        return AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)

    monkeypatch.setattr(market_bridge.httpx, "AsyncClient", client)
    monkeypatch.setattr(market_protocol_handler, "_load_bridge_info", lambda: {
        "token": market_bridge.get_bridge_token(), "port": 48911,
    })
    uri = "neko://install?" + urlencode({
        "url": "https://attacker.invalid/demo.neko-plugin",
        "sha256": "c" * 64,
        "id": "42",
        "version": "2.0.0",
    })
    assert market_protocol_handler.handle_uri(uri) == 1
    assert len(catalog["requests"]) == 1
    assert not queued
    assert not market_bridge._tasks
