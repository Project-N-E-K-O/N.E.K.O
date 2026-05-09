from __future__ import annotations

import pytest

from plugin.plugins import lifekit
from plugin.plugins.lifekit import LifeKitPlugin
from plugin.plugins.lifekit._routing import Route, RoutingResult
from plugin.plugins.lifekit._i18n import LRUCache
from plugin.plugins.lifekit.routers import hourly as hourly_router
from plugin.plugins.lifekit.routers import trip as trip_router
from plugin.plugins.lifekit.routers.hourly import _safe_idx
from plugin.plugins.lifekit.routers.trip import TripRouter, _build_mode_advice, _build_next_actions, _build_weather_tips, _mode_label

pytestmark = pytest.mark.plugin_unit


@pytest.mark.asyncio
async def test_weather_data_uses_auto_timezone_for_blank_config(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = object.__new__(LifeKitPlugin)
    plugin._cfg = {"timezone": "", "forecast_days": 3}
    plugin._cache = LRUCache(4)
    captured: dict[str, str] = {}

    async def fake_fetch_forecast(lat: float, lon: float, *, days: int, tz: str):
        captured["tz"] = tz
        return {"ok": True}

    monkeypatch.setattr(lifekit, "fetch_forecast", fake_fetch_forecast)

    data, error = await plugin._get_weather_data({"lat": 31.2, "lon": 121.4})

    assert data == {"ok": True}
    assert error == ""
    assert captured["tz"] == "auto"


def test_hourly_safe_idx_rejects_negative_index() -> None:
    assert _safe_idx({"temperature": [1, 2, 3]}, "temperature", -1) is None


def test_trip_helpers_localize_visible_text_and_handle_partial_weather() -> None:
    i18n = lifekit.I18n(lifekit._LOCALES_DIR)
    i18n.set_locale("en")

    tips = _build_weather_tips(
        {"current": {"weather_code": 61}},
        None,
        {"label": "Home", "lat": 1, "lon": 2},
        {"address": "Office", "lat": 3, "lon": 4},
        i18n,
        object(),
    )

    assert tips == ["🌂 Rain expected — bring an umbrella"]
    assert _mode_label("transit", i18n) == "🚇 Transit"
    assert _build_mode_advice(0.5, {"current": {"weather_code": 61}}, None, i18n) == "🚇 Rain expected - transit is recommended"
    assert _build_next_actions("Tokyo", i18n) == [
        "food_recommend location=Tokyo - destination food",
        "search_nearby location=Tokyo - search near destination",
        "currency_convert - currency conversion",
    ]


@pytest.mark.asyncio
async def test_trip_advice_normalizes_mode_and_preserves_zero_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    class MainPlugin:
        _cfg: dict[str, object] = {}
        _i18n = lifekit.I18n(lifekit._LOCALES_DIR)

        def _resolve_locale(self) -> None:
            self._i18n.set_locale("en")

        async def _resolve_location(self, city: str | None):
            name = city or "Home"
            return {"city": name, "lat": 31.2, "lon": 121.4}, ""

        async def _get_weather_data(self, loc: dict[str, object]):
            return None, ""

    captured: dict[str, object] = {}

    class RoutingService:
        def __init__(self, cfg: dict[str, object]) -> None:
            captured["cfg"] = cfg

        async def plan(self, *args: object, **kwargs: object) -> RoutingResult:
            captured["modes"] = kwargs.get("modes")
            return RoutingResult(
                origin_name="Home",
                destination_name="Office",
                routes=[Route(mode="driving", distance_m=1200, duration_s=300, cost=0)],  # type: ignore[arg-type]
                provider="fake",
            )

    monkeypatch.setattr(trip_router, "RoutingService", RoutingService)
    monkeypatch.setattr(trip_router, "push_lifekit_content", lambda *_: None)
    router = TripRouter()
    router._bind(MainPlugin())

    result = await router.trip_advice(destination="Office", origin="Home", mode=" DRIVING ")

    assert result.is_ok()
    assert captured["modes"] == ["driving"]
    assert result.value["routes"][0]["cost"] == 0


@pytest.mark.asyncio
async def test_hourly_forecast_expands_forecast_days_for_requested_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    class MainPlugin:
        _cfg = {"timezone": ""}
        _i18n = lifekit.I18n(lifekit._LOCALES_DIR)

        def _resolve_locale(self) -> None:
            return None

        async def _resolve_location(self, city: str):
            return {"city": "Shanghai", "lat": 31.2, "lon": 121.4}, ""

        def _wmo_text(self, code: int) -> str:
            return "Sunny"

    router = hourly_router.HourlyForecastRouter()
    router._bind(MainPlugin())
    captured: dict[str, object] = {}

    async def fake_fetch_forecast(*args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "hourly": {
                "time": ["2026-05-06T00:00"],
                "weather_code": [0],
                "temperature_2m": [20],
            }
        }

    monkeypatch.setattr(hourly_router, "fetch_forecast", fake_fetch_forecast)
    monkeypatch.setattr(hourly_router, "push_lifekit_content", lambda *_: None)

    result = await router.hourly_forecast(hours=48)

    assert result.is_ok()
    assert captured["days"] == 2
    assert captured["tz"] == "auto"


@pytest.mark.asyncio
async def test_update_config_entry_coerces_blank_timezone_to_auto() -> None:
    plugin = object.__new__(LifeKitPlugin)
    stored: dict[str, object] = {}

    class Config:
        async def update(self, updates: dict[str, object]) -> None:
            stored.update(updates)

        async def dump(self, timeout: float = 5.0) -> dict[str, object]:
            return stored

    plugin.config = Config()
    plugin._cfg = {}
    plugin._resolve_locale = lambda: None

    result = await plugin.update_config_entry(timezone="   ")

    assert result.is_ok()
    assert stored == {"lifekit": {"timezone": "auto"}}
