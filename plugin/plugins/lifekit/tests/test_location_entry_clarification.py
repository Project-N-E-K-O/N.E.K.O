import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from plugin.sdk.plugin import Err, Ok

from plugin.plugins.lifekit._i18n import I18n
from plugin.plugins.lifekit._location import (
    LocationCandidate,
    LocationProblem,
    LocationPurpose,
)
from plugin.plugins.lifekit._api import GeocodeError
from plugin.plugins.lifekit._contracts import (
    AddLocationResult,
    AirQualityResult,
    FoodRecommendResult,
    GetWeatherResult,
    HourlyForecastResult,
    NearbyResult,
    TravelAdviceResult,
    TripAdviceResult,
)
from plugin.plugins.lifekit.routers.air_quality import AirQualityRouter
from plugin.plugins.lifekit.routers.current import CurrentWeatherRouter
from plugin.plugins.lifekit.routers.food import FoodRecommendRouter
from plugin.plugins.lifekit.routers.hourly import HourlyForecastRouter
from plugin.plugins.lifekit.routers.locations import LocationsRouter
from plugin.plugins.lifekit.routers.travel import TravelAdviceRouter
from plugin.plugins.lifekit.routers.trip import TripRouter


class _AmbiguousLocationPlugin:
    plugin_id = "lifekit"

    def __init__(self) -> None:
        self._i18n = I18n(Path(__file__).resolve().parents[1] / "locales")

    def _resolve_locale(self) -> None:
        self._i18n.set_locale("zh-CN")

    async def _resolve_location(self, *_: Any, **__: Any):
        return None, "error.location_ambiguous"


class _FailedLocationPlugin(_AmbiguousLocationPlugin):
    async def _resolve_location(self, *_: Any, **__: Any):
        return None, "error.geocode_failed"


class _ClarifiableLocationPlugin(_AmbiguousLocationPlugin):
    def __init__(self, error_key: str) -> None:
        super().__init__()
        self.error_key = error_key

    async def _resolve_location(self, *_: Any, **__: Any):
        return None, self.error_key


class _CandidateLocationPlugin(_AmbiguousLocationPlugin):
    async def _resolve_location(self, *_: Any, **__: Any):
        candidates = (
            LocationCandidate(
                display_name="吉林市",
                latitude=43.85,
                longitude=126.56,
                country_code="CN",
                admin1="吉林省",
                precision="city",
            ),
            LocationCandidate(
                display_name="吉林",
                latitude=25.00,
                longitude=121.89,
                country_code="TW",
                admin1="台湾",
                precision="locality",
            ),
        )
        return None, LocationProblem(
            error_key="error.location_ambiguous",
            requested_location="吉林",
            purpose=LocationPurpose.WEATHER,
            candidates=candidates,
        )


class _AmbiguousDestinationPlugin(_AmbiguousLocationPlugin):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def _resolve_location(self, *_: Any, **__: Any):
        self.calls += 1
        if self.calls == 1:
            return {"city": "上海", "lat": 31.2, "lon": 121.5}, ""
        return None, "error.location_ambiguous"


class _Logger:
    def warning(self, *_: Any, **__: Any) -> None:
        return None


class _LocationStorePlugin(_AmbiguousLocationPlugin):
    logger = _Logger()

    def __init__(self) -> None:
        super().__init__()
        self._locations_lock = asyncio.Lock()


@pytest.mark.asyncio
async def test_weather_entry_requests_clarification_for_ambiguous_city() -> None:
    router = CurrentWeatherRouter()
    router._bind(_AmbiguousLocationPlugin())

    result = await router.get_weather(city="上海")

    assert isinstance(result, Ok)
    assert result.value["status"] == "clarify"
    assert "补充城市、省份或国家" in result.value["summary"]
    assert result.value["context"]["field"] == "city"
    assert result.value["context"]["requested_location"] == "上海"


@pytest.mark.asyncio
async def test_location_clarification_preserves_candidate_context() -> None:
    router = CurrentWeatherRouter()
    router._bind(_CandidateLocationPlugin())

    result = await router.get_weather(city="吉林")

    assert isinstance(result, Ok)
    assert result.value["choices"] == ["吉林市 · 吉林省 · CN", "吉林 · 台湾 · TW"]
    assert result.value["context"]["purpose"] == "weather"
    first_candidate = result.value["context"]["candidates"][0]
    assert first_candidate["id"]
    assert {key: value for key, value in first_candidate.items() if key != "id"} == {
        "display_name": "吉林市",
        "country_code": "CN",
        "admin1": "吉林省",
        "admin2": "",
        "precision": "city",
    }


@pytest.mark.asyncio
async def test_location_clarification_distinguishes_identical_candidate_labels() -> None:
    candidates = tuple(
        LocationCandidate(
            display_name="Springfield",
            latitude=latitude,
            longitude=longitude,
            country_code="US",
            admin1="Illinois",
            precision="locality",
        )
        for latitude, longitude in ((39.78, -89.64), (39.80, -89.62))
    )

    class _DuplicateCandidatePlugin(_AmbiguousLocationPlugin):
        async def _resolve_location(self, *_: Any, **__: Any):
            return None, LocationProblem(
                error_key="error.location_ambiguous",
                requested_location="Springfield",
                purpose=LocationPurpose.WEATHER,
                candidates=candidates,
            )

    router = CurrentWeatherRouter()
    router._bind(_DuplicateCandidatePlugin())

    result = await router.get_weather(city="Springfield")

    assert isinstance(result, Ok)
    assert len(set(result.value["choices"])) == 2
    candidate_ids = [
        item["id"] for item in result.value["context"]["candidates"]
    ]
    assert len(set(candidate_ids)) == 2
    assert all(candidate_id in choice for candidate_id, choice in zip(
        candidate_ids,
        result.value["choices"],
    ))


@pytest.mark.parametrize(
    ("router_type", "method_name", "kwargs"),
    [
        (HourlyForecastRouter, "hourly_forecast", {"city": "上海"}),
        (AirQualityRouter, "air_quality", {"city": "上海"}),
        (TravelAdviceRouter, "travel_advice", {"city": "上海"}),
        (
            FoodRecommendRouter,
            "food_recommend",
            {"location": "上海", "cuisine": "火锅"},
        ),
        (
            TripRouter,
            "trip_advice",
            {"origin": "上海", "destination": "北京"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_location_entries_request_clarification_instead_of_failing(
    router_type: type,
    method_name: str,
    kwargs: dict[str, Any],
) -> None:
    router = router_type()
    router._bind(_AmbiguousLocationPlugin())

    result = await getattr(router, method_name)(**kwargs)

    assert isinstance(result, Ok)
    assert result.value["status"] == "clarify"
    assert "补充城市、省份或国家" in result.value["summary"]


@pytest.mark.asyncio
async def test_location_clarification_preserves_non_location_entry_parameters() -> None:
    hourly = HourlyForecastRouter()
    hourly._bind(_AmbiguousLocationPlugin())
    hourly_result = await hourly.hourly_forecast(city="上海", hours=72)

    food = FoodRecommendRouter()
    food._bind(_AmbiguousLocationPlugin())
    food_result = await food.food_recommend(
        location="上海",
        cuisine="日料",
        scene="约会",
        radius=4200,
    )

    assert isinstance(hourly_result, Ok)
    assert hourly_result.value["context"]["hours"] == 72
    assert isinstance(food_result, Ok)
    assert food_result.value["context"]["cuisine"] == "日料"
    assert food_result.value["context"]["scene"] == "约会"
    assert food_result.value["context"]["radius"] == 4200


@pytest.mark.parametrize(
    "error_key",
    [
        "error.location_confirmation_required",
        "error.city_not_found",
        "error.no_location",
    ],
)
@pytest.mark.parametrize(
    ("router_type", "method_name", "kwargs"),
    [
        (CurrentWeatherRouter, "get_weather", {"city": "上海"}),
        (HourlyForecastRouter, "hourly_forecast", {"city": "上海"}),
        (AirQualityRouter, "air_quality", {"city": "上海"}),
        (TravelAdviceRouter, "travel_advice", {"city": "上海"}),
        (
            FoodRecommendRouter,
            "food_recommend",
            {"location": "上海", "cuisine": "火锅"},
        ),
        (
            TripRouter,
            "trip_advice",
            {"origin": "上海", "destination": "北京"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_location_entries_clarify_every_user_correctable_outcome(
    error_key: str,
    router_type: type,
    method_name: str,
    kwargs: dict[str, Any],
) -> None:
    router = router_type()
    router._bind(_ClarifiableLocationPlugin(error_key))

    result = await getattr(router, method_name)(**kwargs)

    assert isinstance(result, Ok)
    assert result.value["status"] == "clarify"
    assert result.value["summary"]


@pytest.mark.asyncio
async def test_weather_entry_keeps_provider_failure_as_error() -> None:
    router = CurrentWeatherRouter()
    router._bind(_FailedLocationPlugin())

    result = await router.get_weather(city="上海")

    assert isinstance(result, Err)


@pytest.mark.parametrize(
    ("router_type", "method_name", "kwargs"),
    [
        (HourlyForecastRouter, "hourly_forecast", {"city": "上海"}),
        (AirQualityRouter, "air_quality", {"city": "上海"}),
        (TravelAdviceRouter, "travel_advice", {"city": "上海"}),
        (
            FoodRecommendRouter,
            "food_recommend",
            {"location": "上海", "cuisine": "火锅"},
        ),
        (
            TripRouter,
            "trip_advice",
            {"origin": "上海", "destination": "北京"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_location_entries_keep_provider_failure_as_error(
    router_type: type,
    method_name: str,
    kwargs: dict[str, Any],
) -> None:
    router = router_type()
    router._bind(_FailedLocationPlugin())

    result = await getattr(router, method_name)(**kwargs)

    assert isinstance(result, Err)


@pytest.mark.asyncio
async def test_trip_entry_identifies_ambiguous_destination_as_clarification() -> None:
    router = TripRouter()
    router._bind(_AmbiguousDestinationPlugin())

    result = await router.trip_advice(
        origin="上海",
        destination="朝阳",
        mode="transit",
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "clarify"
    assert result.value["context"]["field"] == "destination"
    assert result.value["context"]["origin"] == "上海"
    assert result.value["context"]["destination"] == "朝阳"
    assert result.value["context"]["mode"] == "transit"


@pytest.mark.parametrize(
    "cause",
    ["ambiguous", "needs_confirmation", "not_found", "no_location"],
)
@pytest.mark.asyncio
async def test_add_location_entry_clarifies_user_correctable_geocode_outcome(
    monkeypatch: Any,
    cause: str,
) -> None:
    async def unresolved(*_: Any, **__: Any):
        raise GeocodeError("unresolved", cause=cause)

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.locations.geocode_city",
        unresolved,
    )
    router = LocationsRouter()
    router._bind(_LocationStorePlugin())

    result = await router.add_location(
        label="出差",
        city="上海",
        address="浦东新区",
        set_default=True,
    )

    assert isinstance(result, Ok)
    assert result.value["status"] == "clarify"
    assert result.value["summary"]
    assert result.value["context"]["address"] == "浦东新区"
    assert result.value["context"]["set_default"] is True


@pytest.mark.asyncio
async def test_add_location_entry_keeps_provider_failure_as_error(
    monkeypatch: Any,
) -> None:
    async def failed(*_: Any, **__: Any):
        raise GeocodeError("failed", cause="network")

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.locations.geocode_city",
        failed,
    )
    router = LocationsRouter()
    router._bind(_LocationStorePlugin())

    result = await router.add_location(label="出差", city="上海")

    assert isinstance(result, Err)


@pytest.mark.asyncio
async def test_add_location_success_uses_localized_summary(monkeypatch: Any) -> None:
    async def resolved(*_: Any, **__: Any):
        return {
            "city": "上海",
            "lat": 31.2,
            "lon": 121.5,
            "country": "CN",
        }

    async def load_locations() -> list[dict[str, Any]]:
        return []

    async def save_locations(_: list[dict[str, Any]]) -> bool:
        return True

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.locations.geocode_city",
        resolved,
    )
    plugin = _LocationStorePlugin()
    router = LocationsRouter()
    router._bind(plugin)
    monkeypatch.setattr(router, "_load", load_locations)
    monkeypatch.setattr(router, "_save", save_locations)

    result = await router.add_location(label="家", city="上海")

    assert isinstance(result, Ok)
    assert result.value["summary"] == "已添加：家（上海）"
    assert result.value["message"] == result.value["summary"]


@pytest.mark.parametrize("outcome", [None, RuntimeError("unexpected provider error")])
@pytest.mark.asyncio
async def test_add_location_entry_classifies_untyped_geocode_outcomes(
    monkeypatch: Any,
    outcome: object,
) -> None:
    async def geocode_outcome(*_: Any, **__: Any):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.locations.geocode_city",
        geocode_outcome,
    )
    router = LocationsRouter()
    router._bind(_LocationStorePlugin())

    result = await router.add_location(label="出差", city="上海")

    if outcome is None:
        assert isinstance(result, Ok)
        assert result.value["status"] == "clarify"
    else:
        assert isinstance(result, Err)


@pytest.mark.parametrize(
    "result_model",
    [
        AddLocationResult,
        GetWeatherResult,
        HourlyForecastResult,
        AirQualityResult,
        TravelAdviceResult,
        FoodRecommendResult,
        NearbyResult,
        TripAdviceResult,
    ],
)
def test_location_result_contracts_preserve_clarification(result_model: type) -> None:
    result = result_model.model_validate(
        {"status": "clarify", "summary": "请补充位置", "choices": []}
    )

    assert result.status == "clarify"
    assert result.summary == "请补充位置"


@pytest.mark.parametrize(
    "result_model",
    [
        AddLocationResult,
        GetWeatherResult,
        HourlyForecastResult,
        AirQualityResult,
        TravelAdviceResult,
        FoodRecommendResult,
        NearbyResult,
        TripAdviceResult,
    ],
)
def test_location_result_contracts_reject_incomplete_ready_payload(
    result_model: type,
) -> None:
    with pytest.raises(ValidationError):
        result_model.model_validate({"status": "ready"})


@pytest.mark.parametrize(
    "result_model",
    [
        AddLocationResult,
        GetWeatherResult,
        HourlyForecastResult,
        AirQualityResult,
        TravelAdviceResult,
        FoodRecommendResult,
        NearbyResult,
        TripAdviceResult,
    ],
)
def test_location_result_contracts_reject_clarification_without_summary(
    result_model: type,
) -> None:
    with pytest.raises(ValidationError):
        result_model.model_validate({"status": "clarify"})

    with pytest.raises(ValidationError):
        result_model.model_validate({"status": "clarify", "summary": "   "})


@pytest.mark.parametrize(
    ("result_model", "payload"),
    [
        (
            AddLocationResult,
            {
                "summary": "已保存",
                "message": "已保存",
                "location": {"city": "上海"},
            },
        ),
        (
            GetWeatherResult,
            {
                "city": "上海",
                "summary": "晴",
                "current": {"temperature": 30},
                "forecast": [],
            },
        ),
        (
            HourlyForecastResult,
            {"city": "上海", "summary": "未来两小时", "hours": [], "total_hours": 0},
        ),
        (
            AirQualityResult,
            {"city": "上海", "summary": "良", "aqi": {"value": 42}, "advice": []},
        ),
        (
            TravelAdviceResult,
            {"city": "上海", "summary": "适合出行", "tips": []},
        ),
        (
            FoodRecommendResult,
            {"summary": "推荐", "recommendations": [], "query": "火锅"},
        ),
        (
            NearbyResult,
            {
                "summary": "未找到",
                "request": "附近有什么值得逛的",
                "searched_terms": ["商店", "书店", "咖啡馆"],
                "results": [],
                "count": 0,
            },
        ),
        (
            TripAdviceResult,
            {
                "origin": "上海",
                "destination": "北京",
                "distance_km": 1067.0,
                "summary": "路线建议",
                "routes": [],
            },
        ),
    ],
)
def test_location_result_contracts_accept_complete_ready_payload(
    result_model: type,
    payload: dict[str, Any],
) -> None:
    result = result_model.model_validate({"status": "ready", **payload})

    assert result.status == "ready"
