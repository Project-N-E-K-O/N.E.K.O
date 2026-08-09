from pathlib import Path
from typing import Any

import httpx
import pytest

from app.agent_server.channels.user_plugin import _plugin_terminal_status
from plugin.sdk.plugin import Err, Ok

from plugin.plugins.lifekit import LifeKitPlugin
from plugin.plugins.lifekit._i18n import I18n
from plugin.plugins.lifekit import _poi
from plugin.plugins.lifekit._contracts import NearbyResult
from plugin.plugins.lifekit._location import (
    LocationCandidate,
    LocationResolver,
)
from plugin.plugins.lifekit.routers.nearby import NearbyRouter


class _CapturingLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, template: str, *args: object) -> None:
        self.messages.append(template.format(*args))

    def warning(self, template: str, *args: object) -> None:
        self.messages.append(template.format(*args))


class _EmptyPOIClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, **_: object) -> httpx.Response:
        return httpx.Response(
            200,
            json={"elements": []},
            request=httpx.Request("POST", url),
        )


class _NearbyPlugin:
    plugin_id = "lifekit"

    def __init__(self) -> None:
        self._i18n = I18n(Path(__file__).resolve().parents[1] / "locales")
        self._cfg: dict[str, Any] = {}
        self.messages: list[dict[str, Any]] = []
        self.logger = _CapturingLogger()

    def _resolve_locale(self) -> None:
        self._i18n.set_locale("zh-CN")

    async def _resolve_location(self, *_: Any, **__: Any):
        return {"city": "吉林市", "lat": 43.8, "lon": 126.5}, None

    async def _get_weather_data(self, *_: Any, **__: Any):
        return None, None

    def push_message(self, **kwargs: Any) -> dict[str, bool]:
        self.messages.append(kwargs)
        return {"ok": True}


class _FailedLocationPlugin(_NearbyPlugin):
    async def _resolve_location(self, *_: Any, **__: Any):
        return None, "error.geocode_failed"


class _AmbiguousLocationPlugin(_NearbyPlugin):
    async def _resolve_location(self, *_: Any, **__: Any):
        return None, "error.location_ambiguous"


@pytest.mark.asyncio
async def test_nearby_logs_do_not_include_raw_request_or_location() -> None:
    plugin = _AmbiguousLocationPlugin()
    router = NearbyRouter()
    router._bind(plugin)

    result = await router.search_nearby(
        request="和客户谈机密项目",
        search_terms=["咖啡馆"],
        location="私人会所地址",
        _ctx={"latest_user_request": "和客户谈机密项目"},
    )

    assert isinstance(result, Ok)
    logged = "\n".join(plugin.logger.messages)
    assert "和客户谈机密项目" not in logged
    assert "私人会所地址" not in logged


@pytest.mark.asyncio
async def test_typed_hints_drive_nearby_search_without_free_form_retrieval_terms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def open_meteo_candidates(
        query: str,
        **_: object,
    ) -> list[LocationCandidate]:
        if query != "南京东路":
            return []
        return [
            LocationCandidate(
                display_name="南京东路",
                latitude=31.235,
                longitude=121.475,
                country_code="CN",
                admin1="上海市",
                admin2="上海市",
                precision="address",
                source="open_meteo",
            )
        ]

    async def nominatim_candidates(
        _query: str,
        **_: object,
    ) -> list[LocationCandidate]:
        return []

    class _RestaurantClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, url: str, **_: object) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "type": "node",
                            "id": 11,
                            "lat": 31.236,
                            "lon": 121.476,
                            "tags": {
                                "name": "南京东路餐厅",
                                "amenity": "restaurant",
                            },
                        }
                    ]
                },
                request=httpx.Request("POST", url),
            )

    class _NaturalLanguagePlugin(_NearbyPlugin):
        _resolve_location = LifeKitPlugin._resolve_location

        def __init__(self) -> None:
            super().__init__()
            self._cfg = {"enable_geoip": False}
            self._location_resolver = LocationResolver(
                open_meteo=open_meteo_candidates,
                nominatim=nominatim_candidates,
            )

    monkeypatch.setattr(
        _poi.httpx,
        "AsyncClient",
        lambda **_: _RestaurantClient(),
    )
    router = NearbyRouter()
    router._bind(_NaturalLanguagePlugin())

    result = await router.search_nearby(
        request="南京东路附近有什么好吃的",
        location_hint="南京东路",
        place_intent="food",
        _ctx={"latest_user_request": "南京东路附近有什么好吃的"},
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "ready"
    assert result.value["searched_terms"] == ["餐厅"]
    assert result.value["count"] == 1
    assert result.value["results"][0]["name"] == "南京东路餐厅"


@pytest.mark.parametrize(
    ("place_intent", "expected_terms"),
    [
        ("food", ["餐厅"]),
        ("coffee", ["咖啡馆", "茶馆"]),
        ("shopping", ["商店", "购物中心"]),
        ("outdoors", ["公园", "景点"]),
        ("culture", ["博物馆", "美术馆", "书店"]),
        ("family", ["室内游乐场", "公园", "博物馆"]),
        ("nightlife", ["酒吧", "夜店"]),
        ("service", ["医院", "药店", "银行", "停车场"]),
        ("explore", ["景点", "公园", "咖啡馆", "书店"]),
    ],
)
@pytest.mark.asyncio
async def test_each_typed_place_intent_maps_to_bounded_search_terms(
    monkeypatch: pytest.MonkeyPatch,
    place_intent: str,
    expected_terms: list[str],
) -> None:
    monkeypatch.setattr(_poi.httpx, "AsyncClient", lambda **_: _EmptyPOIClient())
    router = NearbyRouter()
    router._bind(_NearbyPlugin())

    result = await router.search_nearby(
        request="附近有什么合适的地方",
        place_intent=place_intent,
        _ctx={"latest_user_request": "附近有什么合适的地方"},
    )

    assert isinstance(result, Ok)
    assert result.value["searched_terms"] == expected_terms
    assert 1 <= len(result.value["searched_terms"]) <= 4


@pytest.mark.asyncio
async def test_typed_preferences_only_add_provider_searchable_terms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_poi.httpx, "AsyncClient", lambda **_: _EmptyPOIClient())
    router = NearbyRouter()
    router._bind(_NearbyPlugin())

    result = await router.search_nearby(
        request="附近找一家安静、适合约会的火锅店",
        place_intent="food",
        preference_hints=["火锅", "安静", "适合约会", "火锅"],
        _ctx={"latest_user_request": "附近找一家安静、适合约会的火锅店"},
    )

    assert isinstance(result, Ok)
    assert result.value["searched_terms"] == ["火锅", "餐厅"]


@pytest.mark.asyncio
async def test_exploratory_request_searches_across_multiple_retrieval_terms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _PlaceClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, url: str, **_: object) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "type": "node",
                            "id": 21,
                            "lat": 43.801,
                            "lon": 126.501,
                            "tags": {"name": "安静空间", "amenity": "cafe"},
                        }
                    ]
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(_poi.httpx, "AsyncClient", lambda **_: _PlaceClient())
    plugin = _NearbyPlugin()
    router = NearbyRouter()
    router._bind(plugin)

    result = await router.search_nearby(
        request="找个适合聊天但别太吵的地方",
        search_terms=["咖啡馆", "茶馆", "书店", "公园"],
        _ctx={"latest_user_request": "找个适合聊天但别太吵的地方"},
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "ready"
    assert result.value["searched_terms"] == ["咖啡馆", "茶馆", "书店", "公园"]
    assert result.value["count"] == 1
    assert result.value["results"][0]["matched_term"] == "咖啡馆"
    assert "choices" not in result.value


@pytest.mark.parametrize(
    ("user_request", "search_terms"),
    [
        ("下雨天能带孩子去哪", ["室内游乐场", "博物馆", "商场"]),
        ("附近有没有卖相机配件的", ["相机店", "电子产品店", "摄影器材"]),
        ("随便找几个值得逛的小店", ["商店", "书店", "咖啡馆", "精品店"]),
    ],
)
@pytest.mark.asyncio
async def test_open_ended_needs_execute_the_llm_retrieval_plan(
    monkeypatch: pytest.MonkeyPatch,
    user_request: str,
    search_terms: list[str],
) -> None:
    class _DiscoveryClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, url: str, **_: object) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "type": "node",
                            "id": 31,
                            "lat": 43.801,
                            "lon": 126.501,
                            "tags": {"name": "召回结果", "shop": "yes"},
                        }
                    ]
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(_poi.httpx, "AsyncClient", lambda **_: _DiscoveryClient())
    router = NearbyRouter()
    router._bind(_NearbyPlugin())

    result = await router.search_nearby(
        request=user_request,
        search_terms=search_terms,
        _ctx={"latest_user_request": user_request},
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "ready"
    assert result.value["searched_terms"] == search_terms
    assert result.value["count"] == 1


@pytest.mark.asyncio
async def test_broad_request_returns_unavailable_on_location_provider_failure() -> None:
    plugin = _FailedLocationPlugin()
    router = NearbyRouter()
    router._bind(plugin)

    result = await router.search_nearby(
        request="我附近有啥地方可去吗？",
        search_terms=["公园", "景点", "咖啡馆", "书店"],
        _ctx={"latest_user_request": "我附近有啥地方可去吗？"},
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "unavailable"
    assert result.value["error_code"] == "LOCATION_PROVIDER_UNAVAILABLE"
    assert result.value["retriable"] is True


@pytest.mark.asyncio
async def test_specific_request_returns_unavailable_on_location_provider_failure() -> None:
    plugin = _FailedLocationPlugin()
    router = NearbyRouter()
    router._bind(plugin)

    result = await router.search_nearby(
        request="附近的公园",
        search_terms=["公园"],
        _ctx={"latest_user_request": "附近的公园"},
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "unavailable"
    assert result.value["error_code"] == "LOCATION_PROVIDER_UNAVAILABLE"
    assert result.value["retriable"] is True


@pytest.mark.asyncio
async def test_nearby_provider_timeout_returns_a_meaningful_retriable_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RejectingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, url: str, **_: object) -> httpx.Response:
            request = httpx.Request("POST", url)
            raise httpx.ReadTimeout("private upstream detail", request=request)

    monkeypatch.setattr(_poi.httpx, "AsyncClient", lambda **_: _RejectingClient())
    router = NearbyRouter()
    router._bind(_NearbyPlugin())

    result = await router.search_nearby(
        request="上海南京东路附近的餐厅",
        search_terms=["餐厅"],
        location="上海南京东路",
        _ctx={"latest_user_request": "上海南京东路附近的餐厅"},
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "unavailable"
    assert result.value["error_code"] == "UPSTREAM_TIMEOUT"
    assert result.value["retriable"] is True
    assert "地图服务" in result.value["summary"]
    assert "private upstream detail" not in str(result.value)
    NearbyResult.model_validate(result.value)


@pytest.mark.asyncio
async def test_nearby_does_not_disguise_programming_errors_as_upstream_outages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, url: str, **_: object) -> httpx.Response:
            raise AssertionError("provider adapter bug")

    monkeypatch.setattr(_poi.httpx, "AsyncClient", lambda **_: _BrokenClient())
    router = NearbyRouter()
    router._bind(_NearbyPlugin())

    with pytest.raises(AssertionError, match="provider adapter bug"):
        await router.search_nearby(
            request="上海南京东路附近的餐厅",
            search_terms=["餐厅"],
            location="上海南京东路",
            _ctx={"latest_user_request": "上海南京东路附近的餐厅"},
        )


@pytest.mark.asyncio
async def test_nearby_without_a_usable_location_returns_completed_unavailable() -> None:
    router = NearbyRouter()
    router._bind(_AmbiguousLocationPlugin())

    result = await router.search_nearby(
        request="吉林附近的公园",
        location_hint="吉林",
        place_intent="outdoors",
        preference_hints=["公园"],
        radius=2500,
        _ctx={"latest_user_request": "吉林附近的公园"},
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "unavailable"
    assert result.value["request"] == "吉林附近的公园"
    assert result.value["searched_terms"] == ["公园", "景点"]
    assert result.value["results"] == []
    assert result.value["error_code"] == "LOCATION_REQUIRED"
    assert result.value["retriable"] is True
    assert _plugin_terminal_status(True, result.value) == "completed"
    NearbyResult.model_validate(result.value)


@pytest.mark.asyncio
async def test_nearby_does_not_execute_an_ineligible_region_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def region_candidate(*_: Any, **__: Any):
        return [
            LocationCandidate(
                display_name="吉林省",
                latitude=43.7,
                longitude=126.2,
                country_code="CN",
                precision="region",
                source="open_meteo",
            )
        ]

    async def no_nominatim_candidates(*_: Any, **__: Any):
        return []

    class _MustNotSearchClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, *_: Any, **__: Any) -> httpx.Response:
            raise AssertionError("an ineligible region must not become a POI center")

    class _RegionPlugin(_NearbyPlugin):
        _resolve_location = LifeKitPlugin._resolve_location

        def __init__(self) -> None:
            super().__init__()
            self._cfg = {"enable_geoip": False}
            self._location_resolver = LocationResolver(
                open_meteo=region_candidate,
                nominatim=no_nominatim_candidates,
            )

    monkeypatch.setattr(_poi.httpx, "AsyncClient", lambda **_: _MustNotSearchClient())
    router = NearbyRouter()
    router._bind(_RegionPlugin())

    result = await router.search_nearby(
        request="吉林省附近有什么",
        location_hint="吉林省",
        place_intent="explore",
        _ctx={"latest_user_request": "吉林省附近有什么"},
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "unavailable"
    assert result.value["error_code"] == "LOCATION_REQUIRED"


@pytest.mark.asyncio
async def test_ambiguous_location_searches_one_primary_center_without_mixing_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def open_meteo_candidates(*_: Any, **__: Any):
        return [
            LocationCandidate(
                display_name="南京东路",
                latitude=31.45,
                longitude=121.10,
                country_code="CN",
                admin1="江苏省",
                admin2="太仓市",
                precision="address",
                source="open_meteo",
            ),
            LocationCandidate(
                display_name="南京东路",
                latitude=31.235,
                longitude=121.475,
                country_code="CN",
                admin1="上海市",
                admin2="上海市",
                precision="address",
                source="open_meteo",
            ),
        ]

    async def no_nominatim_candidates(*_: Any, **__: Any):
        return []

    class _SuccessfulClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            query = str(kwargs.get("data", {}).get("data", ""))
            is_shanghai = "31.235,121.475" in query
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "type": "node",
                            "id": 1 if is_shanghai else 2,
                            "lat": 31.236 if is_shanghai else 31.451,
                            "lon": 121.476 if is_shanghai else 121.101,
                            "tags": {
                                "name": "上海餐厅" if is_shanghai else "太仓餐厅",
                                "amenity": "restaurant",
                            },
                        }
                    ]
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(_poi.httpx, "AsyncClient", lambda **_: _SuccessfulClient())

    class _ReadOnlyNearbyPlugin(_NearbyPlugin):
        _resolve_location = LifeKitPlugin._resolve_location

        def __init__(self) -> None:
            super().__init__()
            self._cfg = {"enable_geoip": False}
            self._location_resolver = LocationResolver(
                open_meteo=open_meteo_candidates,
                nominatim=no_nominatim_candidates,
            )

    router = NearbyRouter()
    router._bind(_ReadOnlyNearbyPlugin())

    result = await router.search_nearby(
        request="南京东路附近的餐厅",
        search_terms=["餐厅"],
        location="南京东路",
        _ctx={"latest_user_request": "南京东路附近的餐厅"},
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "ready"
    assert result.value["count"] == 1
    assert [item["name"] for item in result.value["results"]] == ["上海餐厅"]
    assert result.value["assumed_location"] == "南京东路 · 上海市 · CN"
    assert "南京东路 · 江苏省 · 太仓市 · CN" in result.value["ambiguity_warning"]
    assert result.value["ambiguity_warning"] in result.value["summary"]
    assert "太仓餐厅" not in str(result.value)
    NearbyResult.model_validate(result.value)
