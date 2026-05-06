from __future__ import annotations

import pytest

from plugin.plugins import lifekit
from plugin.plugins.lifekit import LifeKitPlugin
from plugin.plugins.lifekit._i18n import LRUCache
from plugin.plugins.lifekit.routers import hourly as hourly_router
from plugin.plugins.lifekit.routers.hourly import _safe_idx

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
