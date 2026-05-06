from __future__ import annotations

import pytest

from plugin.plugins import lifekit
from plugin.plugins.lifekit import LifeKitPlugin
from plugin.plugins.lifekit._i18n import LRUCache
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
