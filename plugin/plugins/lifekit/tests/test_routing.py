from __future__ import annotations

import asyncio

import httpx
import pytest
from plugin.plugins.lifekit import _routing
from plugin.plugins.lifekit._routing import AMapProvider, OSRMProvider, Route, RoutingService


@pytest.mark.asyncio
async def test_amap_routing_converts_wgs84_request_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted: dict[str, str] = {}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, *, params: dict[str, str]) -> httpx.Response:
            submitted.update(params)
            return httpx.Response(
                200,
                json={"status": "1", "route": {"paths": []}},
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(_routing.httpx, "AsyncClient", lambda **_: _Client())

    await AMapProvider("key").plan_route(
        31.2304, 121.4737, 31.2200, 121.4800, "walking",
    )

    origin_lon, origin_lat = map(float, submitted["origin"].split(","))
    assert origin_lat != pytest.approx(31.2304, abs=0.001)
    assert origin_lon != pytest.approx(121.4737, abs=0.001)


@pytest.mark.asyncio
async def test_amap_transit_uses_provider_type_instead_of_chinese_line_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, **_: object) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "route": {
                        "transits": [
                            {
                                "distance": "1000",
                                "duration": "600",
                                "segments": [
                                    {
                                        "bus": {
                                            "buslines": [
                                                {
                                                    "name": "Metro Line 2",
                                                    "type": "subway",
                                                    "distance": "900",
                                                    "duration": "500",
                                                    "via_num": "3",
                                                }
                                            ]
                                        }
                                    }
                                ],
                            }
                        ]
                    },
                },
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(_routing.httpx, "AsyncClient", lambda **_: _Client())

    routes = await AMapProvider("key").plan_route(
        31.2, 121.4, 31.3, 121.5, "transit",
    )

    assert routes[0].steps[0].mode == "subway"
    assert routes[0].steps[0].instruction == ""
    assert routes[0].steps[0].line_name == "Metro Line 2"


@pytest.mark.parametrize(
    ("mode", "expected_base"),
    [
        ("walking", "https://routing.openstreetmap.de/routed-foot/route/v1/driving/"),
        ("bicycling", "https://routing.openstreetmap.de/routed-bike/route/v1/driving/"),
        ("driving", "https://router.project-osrm.org/route/v1/driving/"),
    ],
)
@pytest.mark.asyncio
async def test_default_osrm_uses_mode_specific_public_dataset(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_base: str,
) -> None:
    requested_url = ""

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, **_: object) -> httpx.Response:
            nonlocal requested_url
            requested_url = url
            return httpx.Response(
                200,
                json={"code": "NoRoute"},
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(_routing.httpx, "AsyncClient", lambda **_: _Client())

    assert await OSRMProvider().plan_route(31.2, 121.4, 31.3, 121.5, mode) == []
    assert requested_url.startswith(expected_base)


@pytest.mark.asyncio
async def test_routing_modes_start_concurrently() -> None:
    both_started = asyncio.Event()
    started: set[str] = set()

    class _Provider:
        name = "test"
        supports_transit = True

        async def plan_route(
            self,
            origin_lat: float,
            origin_lon: float,
            dest_lat: float,
            dest_lon: float,
            mode: str,
            timeout: float = 10.0,
        ):
            started.add(mode)
            if len(started) >= 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.1)
            return [Route(mode=mode, distance_m=1, duration_s=1)]

    service = RoutingService({})
    service._providers = [_Provider()]

    result = await service.plan(31.2, 121.4, 31.3, 121.5, modes=["walking", "driving"])

    assert {route.mode for route in result.routes} == {"walking", "driving"}


@pytest.mark.asyncio
async def test_routing_has_one_total_timeout_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    never_finishes = asyncio.Event()

    class _Provider:
        name = "slow"
        supports_transit = True

        async def plan_route(self, *_: float, **__: object):
            await never_finishes.wait()
            return []

    monkeypatch.setattr(_routing, "ROUTING_TOTAL_TIMEOUT_SECONDS", 0.03)
    service = RoutingService({})
    service._providers = [_Provider()]

    result = await asyncio.wait_for(
        service.plan(31.2, 121.4, 31.3, 121.5, modes=["walking", "driving"]),
        timeout=0.2,
    )

    assert result.routes == []
    assert result.error.startswith("timeout:")


@pytest.mark.asyncio
async def test_slow_primary_provider_does_not_block_same_mode_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    never_finishes = asyncio.Event()

    class _SlowProvider:
        name = "slow"
        supports_transit = False

        async def plan_route(self, *_: float, **__: object):
            await never_finishes.wait()
            return []

    class _FallbackProvider:
        name = "fallback"
        supports_transit = False

        async def plan_route(
            self,
            origin_lat: float,
            origin_lon: float,
            dest_lat: float,
            dest_lon: float,
            mode: str,
            timeout: float = 10.0,
        ):
            return [Route(mode=mode, distance_m=1, duration_s=1)]

    monkeypatch.setattr(_routing, "ROUTING_PROVIDER_HEDGE_SECONDS", 0.01)
    service = RoutingService({})
    service._providers = [_SlowProvider(), _FallbackProvider()]

    result = await asyncio.wait_for(
        service.plan(31.2, 121.4, 31.3, 121.5, modes=["walking"]),
        timeout=0.2,
    )

    assert [route.mode for route in result.routes] == ["walking"]
    assert result.provider == "fallback"


@pytest.mark.asyncio
async def test_transit_without_a_capable_provider_is_unavailable() -> None:
    service = RoutingService({})

    result = await service.plan(
        31.2, 121.4, 31.3, 121.5,
        modes=["transit"],
    )

    assert result.routes == []
    assert result.error == "provider_error:no_provider:transit"
