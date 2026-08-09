from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from plugin.sdk.plugin import Err

from plugin.plugins.lifekit import _poi
from plugin.plugins.lifekit._i18n import I18n
from plugin.plugins.lifekit._coordinates import wgs84_to_gcj02
from plugin.plugins.lifekit._poi import AMapPOI, BaiduPOI, OverpassPOI, POIItem, POIResult, POIService
from plugin.plugins.lifekit.routers.food import FoodRecommendRouter


@pytest.mark.asyncio
async def test_generic_shop_discovery_uses_osm_tag_existence_filter() -> None:
    captured_queries: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured_queries.append(request.content.decode())
        return httpx.Response(200, json={"elements": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OverpassPOI(
            endpoints=("https://available.example/api/interpreter",),
            http_client=client,
        )
        await provider.search("商店", 31.235, 121.475)

    assert len(captured_queries) == 1
    assert '%5B%22shop%22%5D' in captured_queries[0]
    assert '%5B%22name%22~%22' not in captured_queries[0]
    assert '%5B%22shop%22%3D%22yes%22%5D' not in captured_queries[0]


@pytest.mark.asyncio
async def test_overpass_search_recovers_when_one_public_instance_rejects_request() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "unavailable.example":
            return httpx.Response(406, text="Not Acceptable")
        return httpx.Response(
            200,
            json={
                "elements": [
                    {
                        "type": "node",
                        "id": 1,
                        "lat": 31.236,
                        "lon": 121.476,
                        "tags": {
                            "name": "测试餐厅",
                            "amenity": "restaurant",
                            "addr:street": "南京东路",
                        },
                    },
                    {
                        "type": "way",
                        "id": 2,
                        "center": {"lat": 31.2361, "lon": 121.4761},
                        "tags": {
                            "name": "测试餐厅",
                            "amenity": "restaurant",
                            "addr:street": "南京东路",
                        },
                    },
                    {
                        "type": "node",
                        "id": 3,
                        "lat": 31.237,
                        "lon": 121.477,
                        "tags": {
                            "name": "另一家餐厅",
                            "amenity": "restaurant",
                        },
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OverpassPOI(
            endpoints=(
                "https://unavailable.example/api/interpreter",
                "https://available.example/api/interpreter",
            ),
            http_client=client,
        )

        items = await provider.search("餐厅", 31.235, 121.475)

    assert [item.name for item in items] == ["测试餐厅", "另一家餐厅"]


@pytest.mark.asyncio
async def test_default_overpass_order_uses_the_responsive_instance_first() -> None:
    requested_hosts: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(str(request.url.host))
        if request.url.host == "overpass.private.coffee":
            return httpx.Response(200, json={"elements": []})
        raise httpx.ReadTimeout("slow instance", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await OverpassPOI(http_client=client).search("商场", 31.18, 121.42)

    assert requested_hosts == ["overpass.private.coffee"]


@pytest.mark.asyncio
async def test_overpass_failure_log_does_not_include_request_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="private provider response")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OverpassPOI(
            endpoints=("https://sensitive-provider.example/api/interpreter",),
            http_client=client,
        )
        with pytest.raises(RuntimeError):
            await provider.search("机密搜索词", 31.235, 121.475)

    lifekit_log = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == _poi.__name__
    )
    assert "sensitive-provider.example" not in lifekit_log
    assert "private provider response" not in lifekit_log
    assert "机密搜索词" not in lifekit_log
    assert "endpoint_index=0" in lifekit_log


@pytest.mark.asyncio
async def test_food_provider_outage_is_not_reported_as_zero_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RejectingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, url: str, **_: object) -> httpx.Response:
            return httpx.Response(
                503,
                text="provider unavailable",
                request=httpx.Request("POST", url),
            )

    class _Logger:
        def warning(self, *_: object, **__: object) -> None:
            pass

    class _Plugin:
        plugin_id = "lifekit"

        def __init__(self) -> None:
            self._cfg: dict[str, Any] = {}
            self._i18n = I18n(Path(__file__).resolve().parents[1] / "locales")
            self.logger = _Logger()

        def _resolve_locale(self) -> None:
            self._i18n.set_locale("zh-CN")

        async def _resolve_location(self, *_: Any, **__: Any):
            return {"city": "南京东路", "lat": 31.235, "lon": 121.475}, None

    monkeypatch.setattr(_poi.httpx, "AsyncClient", lambda **_: _RejectingClient())
    router = FoodRecommendRouter()
    router._bind(_Plugin())

    result = await router.food_recommend(
        cuisine="餐厅",
        location="上海南京东路",
    )

    assert isinstance(result, Err)
    assert "附近地点搜索失败" in str(result.error)


@pytest.mark.asyncio
async def test_successful_empty_provider_is_not_overridden_by_an_earlier_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _MixedClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, **_: object) -> httpx.Response:
            return httpx.Response(
                503,
                text="configured provider unavailable",
                request=httpx.Request("GET", url),
            )

        async def post(self, url: str, **_: object) -> httpx.Response:
            return httpx.Response(
                200,
                json={"elements": []},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(_poi.httpx, "AsyncClient", lambda **_: _MixedClient())

    result = await POIService({"amap_key": "configured-key"}).search(
        "餐厅",
        31.235,
        121.475,
    )

    assert result.items == []
    assert result.provider == "osm"
    assert result.error == ""


@pytest.mark.asyncio
async def test_amap_uses_gcj02_for_request_and_returns_wgs84(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted_location = ""

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, *, params: dict[str, str]) -> httpx.Response:
            nonlocal submitted_location
            submitted_location = params["location"]
            return httpx.Response(
                200,
                json={
                    "status": "1",
                    "pois": [{"name": "测试商场", "location": submitted_location}],
                },
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(_poi.httpx, "AsyncClient", lambda **_: _Client())
    items = await AMapPOI("key").search("商场", 31.2304, 121.4737)

    submitted_lon, submitted_lat = map(float, submitted_location.split(","))
    assert submitted_lat != pytest.approx(31.2304, abs=0.001)
    assert submitted_lon != pytest.approx(121.4737, abs=0.001)
    assert items[0].lat == pytest.approx(31.2304, abs=1e-5)
    assert items[0].lon == pytest.approx(121.4737, abs=1e-5)


@pytest.mark.asyncio
async def test_baidu_converts_gcj02_response_to_wgs84(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gcj_lat, gcj_lon = wgs84_to_gcj02(31.2304, 121.4737)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, *, params: dict[str, str]) -> httpx.Response:
            assert params["coord_type"] == "1"
            assert params["ret_coordtype"] == "gcj02ll"
            return httpx.Response(
                200,
                json={
                    "status": 0,
                    "results": [
                        {
                            "name": "测试商场",
                            "location": {"lat": gcj_lat, "lng": gcj_lon},
                        }
                    ],
                },
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(_poi.httpx, "AsyncClient", lambda **_: _Client())
    items = await BaiduPOI("key").search("商场", 31.2304, 121.4737)

    assert items[0].lat == pytest.approx(31.2304, abs=1e-6)
    assert items[0].lon == pytest.approx(121.4737, abs=1e-6)


@pytest.mark.asyncio
async def test_search_many_balances_terms_and_records_the_matching_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = POIService({})
    by_term = {
        "商场": [
            POIItem(name="近处商场 A", distance_m=10, lat=31.1, lon=121.1),
            POIItem(name="近处商场 B", distance_m=20, lat=31.2, lon=121.2),
            POIItem(name="近处商场 C", distance_m=30, lat=31.3, lon=121.3),
        ],
        "书店": [
            POIItem(name="远一些的书店", distance_m=500, lat=31.4, lon=121.4),
        ],
    }

    async def fake_search(
        query: str,
        lat: float,
        lon: float,
        radius: int = 3000,
        limit: int = 10,
    ) -> POIResult:
        return POIResult(query=query, items=by_term[query], provider="fake")

    monkeypatch.setattr(service, "search", fake_search)
    result = await service.search_many(
        ("商场", "书店"),
        31.2304,
        121.4737,
        limit=3,
    )

    assert [item.name for item in result.items] == [
        "近处商场 A",
        "远一些的书店",
        "近处商场 B",
    ]
    assert [item.matched_term for item in result.items] == ["商场", "书店", "商场"]
