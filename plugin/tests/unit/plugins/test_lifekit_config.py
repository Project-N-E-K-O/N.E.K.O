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



def test_router_decorated_entries_respect_prefix_and_conflict() -> None:
    """装饰器入口必须在 collect_entries() 时按当前 prefix 解析，
    并和 add_entry 保持相同的冲突语义、meta.id 与 key 一致喵。"""
    from plugin.sdk.plugin import plugin_entry
    from plugin.sdk.shared.core.router import PluginRouter
    from plugin.sdk.shared.models.exceptions import EntryConflictError

    class _FooRouter(PluginRouter):
        @plugin_entry(id="do_thing")
        async def do_thing(self):
            return None

    # prefix 在 __init__ 之后变更时，新 key 必须生效，meta.id 也要跟 key 一致
    router = _FooRouter()
    assert router.entry_ids == ["do_thing"]
    router.set_prefix("foo.")
    entries = router.collect_entries()
    assert list(entries.keys()) == ["foo.do_thing"]
    assert entries["foo.do_thing"].meta.id == "foo.do_thing"

    # 装饰器 id 与 add_entry 已注册的 id 冲突时必须显式报错（不静默覆盖）
    import asyncio

    conflict_router = _FooRouter()
    asyncio.run(conflict_router.add_entry("do_thing", lambda payload: None))

    try:
        conflict_router.collect_entries()
    except EntryConflictError:
        return
    raise AssertionError("expected EntryConflictError for duplicate entry id")
