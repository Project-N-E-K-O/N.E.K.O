from __future__ import annotations

from typing import Any

import httpx
import pytest

from plugin.plugins.lifekit import _routing
from plugin.plugins.lifekit._routing import AMapProvider, OSRMProvider, RoutingProviderError, RoutingService

pytestmark = pytest.mark.plugin_unit


class _FailingProvider:
    name = "broken"
    supports_transit = True

    async def plan_route(self, *_: object, **__: object):
        raise RoutingProviderError(self.name, "timeout")


class _EmptyProvider:
    name = "empty"
    supports_transit = True

    async def plan_route(self, *_: object, **__: object):
        return []


def test_routing_provider_error_sanitizes_detail() -> None:
    error = RoutingProviderError("demo", "  one\n two  " + ("x" * 200))

    assert error.provider == "demo"
    assert "\n" not in error.detail
    assert len(error.detail) <= 160


@pytest.mark.asyncio
async def test_routing_service_reports_provider_failures() -> None:
    service = RoutingService({})
    service._providers = [_FailingProvider(), _EmptyProvider()]

    result = await service.plan(31.2, 121.4, 31.3, 121.5, modes=["walking"])

    assert result.routes == []
    assert result.error == "provider_error:broken:walking:timeout"


@pytest.mark.asyncio
async def test_amap_provider_converts_wgs84_to_gcj02() -> None:
    provider = AMapProvider("key")
    captured: dict[str, str] = {}

    async def fake_simple(url: str, origin: str, dest: str, mode: str, timeout: float):
        captured["origin"] = origin
        captured["dest"] = dest
        return []

    provider._simple = fake_simple  # type: ignore[method-assign]

    await provider.plan_route(31.2304, 121.4737, 31.2200, 121.4600, "walking")

    assert captured["origin"] != "121.473700,31.230400"
    assert captured["dest"] != "121.460000,31.220000"


@pytest.mark.asyncio
async def test_osrm_no_route_remains_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"code": "NoRoute"}

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, *_: object, **__: object) -> Response:
            return Response()

    monkeypatch.setattr(_routing.httpx, "AsyncClient", lambda **_: Client())

    routes = await OSRMProvider().plan_route(31.2, 121.4, 31.3, 121.5, "walking")

    assert routes == []


@pytest.mark.asyncio
async def test_osrm_network_error_is_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, *_: object, **__: object) -> object:
            raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(_routing.httpx, "AsyncClient", lambda **_: Client())

    with pytest.raises(RoutingProviderError, match="ConnectError"):
        await OSRMProvider().plan_route(31.2, 121.4, 31.3, 121.5, "walking")
