from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from plugin.plugins.lifekit import _poi
from plugin.plugins.lifekit._coordinates import wgs84_to_gcj02
from plugin.plugins.lifekit._i18n import I18n
from plugin.plugins.lifekit._poi import (
    UPSTREAM_TIMEOUT,
    AMapPOI,
    BaiduPOI,
    OverpassPOI,
    POIItem,
    POIProviderError,
    POIResult,
    POIService,
)
from plugin.plugins.lifekit.routers.food import FoodRecommendRouter
from plugin.sdk.plugin import Err, Ok


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
async def test_overpass_ignores_malformed_elements_in_valid_response() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"elements": [7, {"tags": "invalid"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OverpassPOI(
            endpoints=("https://available.example/api/interpreter",),
            http_client=client,
        )

        assert await provider.search("餐厅", 31.235, 121.475) == []


@pytest.mark.asyncio
async def test_amap_non_object_response_is_a_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        _poi.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )

    with pytest.raises(POIProviderError):
        await AMapPOI("secret").search("餐厅", 31.235, 121.475)


@pytest.mark.parametrize(
    ("search_term", "expected_filter"),
    [
        ("博物馆", '["tourism"="museum"]'),
        ("美术馆", '["tourism"="gallery"]'),
        ("书店", '["shop"="books"]'),
        ("室内游乐场", '["leisure"="indoor_play"]'),
        ("酒吧", '["amenity"="bar"]'),
        ("夜店", '["amenity"="nightclub"]'),
        ("火锅", '["cuisine"~"^(hot_pot|hotpot)$"]'),
        ("烧烤", '["cuisine"~"^(barbecue|grill)$"]'),
        ("日料", '["cuisine"~"^(japanese|sushi)$"]'),
        ("素食餐厅", '["diet:vegetarian"~"^(yes|only)$"]'),
        ("甜品店", '["shop"~"^(confectionery|pastry)$"]'),
    ],
)
@pytest.mark.asyncio
async def test_typed_intent_terms_use_osm_category_filters(
    search_term: str,
    expected_filter: str,
) -> None:
    captured_query = ""

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal captured_query
        captured_query = parse_qs(request.content.decode())["data"][0]
        return httpx.Response(200, json={"elements": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OverpassPOI(
            endpoints=("https://available.example/api/interpreter",),
            http_client=client,
        )
        await provider.search(search_term, 31.235, 121.475)

    assert expected_filter in captured_query
    assert '["name"~' not in captured_query


@pytest.mark.parametrize(
    "search_term",
    [
        "茶馆", "川菜", "粤菜", "炖菜", "便当", "小龙虾",
        "刨冰", "凉面", "麻辣烫", "羊肉汤", "热干面", "炖汤",
        "brunch", "自助餐", "中餐厅", "西餐", "法餐", "面馆",
        "拉面", "大排档", "串串",
    ],
)
@pytest.mark.asyncio
async def test_lossy_food_categories_preserve_the_original_name(
    search_term: str,
) -> None:
    captured_query = ""

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal captured_query
        captured_query = parse_qs(request.content.decode())["data"][0]
        return httpx.Response(200, json={"elements": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OverpassPOI(
            endpoints=("https://available.example/api/interpreter",),
            http_client=client,
        )
        await provider.search(search_term, 31.235, 121.475)

    assert f'["name"~"{search_term}",i]' in captured_query


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
async def test_overpass_returns_first_success_without_waiting_for_a_slow_instance() -> None:
    slow_started = asyncio.Event()
    never_finishes = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "slow.example":
            slow_started.set()
            await never_finishes.wait()
            raise AssertionError("the slow instance should be cancelled")

        await slow_started.wait()
        return httpx.Response(
            200,
            json={
                "elements": [
                    {
                        "type": "node",
                        "id": 1,
                        "lat": 31.236,
                        "lon": 121.476,
                        "tags": {"name": "及时返回的餐厅", "amenity": "restaurant"},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OverpassPOI(
            endpoints=(
                "https://slow.example/api/interpreter",
                "https://available.example/api/interpreter",
            ),
            http_client=client,
        )
        items = await asyncio.wait_for(
            provider.search("餐厅", 31.18, 121.42),
            timeout=0.6,
        )

    assert [item.name for item in items] == ["及时返回的餐厅"]


@pytest.mark.asyncio
async def test_overpass_runtime_remark_does_not_cancel_a_healthy_instance() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "broken.example":
            return httpx.Response(
                200,
                json={"remark": "runtime error: Query timed out", "elements": []},
            )
        await asyncio.sleep(0)
        return httpx.Response(
            200,
            json={
                "elements": [{
                    "type": "node",
                    "id": 1,
                    "lat": 31.236,
                    "lon": 121.476,
                    "tags": {"name": "健康实例餐厅", "amenity": "restaurant"},
                }]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OverpassPOI(
            endpoints=(
                "https://broken.example/api/interpreter",
                "https://healthy.example/api/interpreter",
            ),
            http_client=client,
        )
        items = await provider.search("餐厅", 31.18, 121.42)

    assert [item.name for item in items] == ["健康实例餐厅"]


@pytest.mark.asyncio
async def test_overpass_applies_one_timeout_budget_to_all_instances() -> None:
    never_finishes = asyncio.Event()

    async def respond(_: httpx.Request) -> httpx.Response:
        await never_finishes.wait()
        raise AssertionError("timed-out requests should be cancelled")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OverpassPOI(
            endpoints=(
                "https://slow-a.example/api/interpreter",
                "https://slow-b.example/api/interpreter",
                "https://slow-c.example/api/interpreter",
            ),
            http_client=client,
        )
        with pytest.raises(POIProviderError) as exc_info:
            await asyncio.wait_for(
                provider.search("餐厅", 31.18, 121.42, timeout=0.03),
                timeout=0.2,
            )

    assert exc_info.value.code == UPSTREAM_TIMEOUT


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
    assert "endpoint_count=1" in lifekit_log


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
    assert result.error.code == "UPSTREAM_UNAVAILABLE"
    assert "附近地点搜索失败" in str(result.error)


@pytest.mark.asyncio
async def test_food_scene_does_not_randomly_replace_the_search_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []

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
            return {"city": "上海", "lat": 31.235, "lon": 121.475}, None

        async def _get_weather_data(self, *_: Any, **__: Any):
            return {"current": {"weather_code": 61, "temperature_2m": 20}}, None

    async def capture_search(
        _self: POIService,
        query: str,
        *_: Any,
        **__: Any,
    ) -> POIResult:
        queries.append(query)
        return POIResult(query=query, provider="test")

    monkeypatch.setattr(POIService, "search", capture_search)
    router = FoodRecommendRouter()
    router._bind(_Plugin())

    first = await router.food_recommend(scene="约会", location="上海")
    second = await router.food_recommend(scene="约会", location="上海")

    assert isinstance(first, Ok)
    assert isinstance(second, Ok)
    assert queries == ["餐厅", "餐厅"]


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
async def test_primary_poi_success_does_not_contact_fallback_provider() -> None:
    fallback_called = False

    class _PrimaryProvider:
        name = "primary"

        async def search(self, *_: Any, **__: Any) -> list[POIItem]:
            return [POIItem(name="首选结果")]

    class _FallbackProvider:
        name = "fallback"

        async def search(self, *_: Any, **__: Any) -> list[POIItem]:
            nonlocal fallback_called
            fallback_called = True
            return [POIItem(name="不应调用")]

    service = POIService({})
    service._providers = [_PrimaryProvider(), _FallbackProvider()]

    result = await service.search("餐厅", 31.235, 121.475)

    assert [item.name for item in result.items] == ["首选结果"]
    assert result.provider == "primary"
    assert fallback_called is False


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
