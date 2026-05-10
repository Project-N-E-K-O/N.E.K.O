from __future__ import annotations

import pytest

from plugin.plugins.lifekit.routers.hourly import _safe_idx

pytestmark = pytest.mark.plugin_unit


def test_hourly_safe_idx_rejects_negative_index() -> None:
    assert _safe_idx({"temperature": [1, 2, 3]}, "temperature", -1) is None



def test_router_plugin_entries_are_registered() -> None:
    """确保 @plugin_entry 装饰的 router 方法被 PluginRouter.__init__ 自动注册；
    否则 LifeKitPlugin.collect_entries 返回的入口只有 lifecycle，12 个 router 的
    get_weather/find_food/... 全部失效喵。"""
    from plugin.plugins.lifekit.routers import (
        CurrentWeatherRouter, TravelAdviceRouter, HourlyForecastRouter,
        LocationsRouter, TripRouter, NearbyRouter,
        FoodRecommendRouter, RecipeRouter,
        AirQualityRouter, CurrencyRouter,
        CountdownRouter, UnitConvertRouter,
    )

    expected_entries = {
        CurrentWeatherRouter: {"get_weather"},
        TravelAdviceRouter: {"travel_advice"},
        HourlyForecastRouter: {"hourly_forecast"},
        LocationsRouter: {"list_locations", "add_location", "remove_location", "set_default_location"},
        TripRouter: {"trip_advice"},
        NearbyRouter: {"search_nearby"},
        FoodRecommendRouter: {"food_recommend"},
        RecipeRouter: {"search_recipe", "random_recipe"},
        AirQualityRouter: {"air_quality"},
        CurrencyRouter: {"currency_convert"},
        CountdownRouter: {"countdown", "days_between"},
        UnitConvertRouter: {"unit_convert"},
    }

    for router_cls, ids in expected_entries.items():
        router = router_cls()
        registered = set(router.entry_ids)
        missing = ids - registered
        assert not missing, f"{router_cls.__name__} missing entries: {missing}"
