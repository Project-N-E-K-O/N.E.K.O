from __future__ import annotations

from pathlib import Path
import asyncio

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



def test_registry_entries_preview_includes_routers() -> None:
    """静态预览（插件未启动时 UI 列出的 entries）必须把 __routers__ 里装饰的入口也覆盖到喵；
    否则用户在插件管理器里根本看不到 get_weather/unit_convert 这些操作。"""
    from plugin.core.registry import _extract_entries_preview
    from plugin.plugins.lifekit import LifeKitPlugin

    entries = _extract_entries_preview("lifekit", LifeKitPlugin, conf={}, pdata={})
    ids = {e["id"] for e in entries}
    # 随手挑 3 个分别来自不同 router 的代表入口
    for required in ("get_weather", "unit_convert", "food_recommend"):
        assert required in ids, f"static preview missing {required} (got {sorted(ids)})"


def test_lifekit_location_entries_use_pydantic_contracts() -> None:
    from plugin.plugins.lifekit._contracts import (
        AddLocationParams,
        AddLocationResult,
        ListLocationsResult,
        LocationIdParams,
        MessageResult,
        RemoveLocationResult,
    )
    from plugin.plugins.lifekit.routers.locations import LocationsRouter

    entries = LocationsRouter().collect_entries()

    list_meta = entries["list_locations"].meta
    assert list_meta.llm_result_model is ListLocationsResult
    assert list_meta.llm_result_fields == ["count", "locations"]

    add_meta = entries["add_location"].meta
    assert add_meta.params is AddLocationParams
    assert add_meta.llm_result_model is AddLocationResult
    assert add_meta.input_schema is not None
    assert set(add_meta.input_schema["required"]) == {"label", "city"}
    assert {"label", "city", "address", "set_default"} <= set(add_meta.input_schema["properties"])

    remove_meta = entries["remove_location"].meta
    assert remove_meta.params is LocationIdParams
    assert remove_meta.llm_result_model is RemoveLocationResult
    assert remove_meta.input_schema is not None
    assert remove_meta.input_schema["required"] == ["location_id"]

    default_meta = entries["set_default_location"].meta
    assert default_meta.params is LocationIdParams
    assert default_meta.llm_result_model is MessageResult
    assert default_meta.input_schema is not None
    assert default_meta.input_schema["required"] == ["location_id"]


def test_lifekit_core_entries_use_pydantic_contracts() -> None:
    from plugin.plugins.lifekit._contracts import (
        FoodRecommendParams,
        FoodRecommendResult,
        HourlyForecastParams,
        HourlyForecastResult,
        NearbyParams,
        NearbyResult,
        UnitConvertParams,
        UnitConvertResult,
    )
    from plugin.plugins.lifekit.routers.food import FoodRecommendRouter
    from plugin.plugins.lifekit.routers.hourly import HourlyForecastRouter
    from plugin.plugins.lifekit.routers.nearby import NearbyRouter
    from plugin.plugins.lifekit.routers.unit_convert import UnitConvertRouter

    hourly_meta = HourlyForecastRouter().collect_entries()["hourly_forecast"].meta
    assert hourly_meta.params is HourlyForecastParams
    assert hourly_meta.llm_result_model is HourlyForecastResult
    assert hourly_meta.input_schema is not None
    assert "required" not in hourly_meta.input_schema
    assert {"city", "hours"} <= set(hourly_meta.input_schema["properties"])

    nearby_meta = NearbyRouter().collect_entries()["search_nearby"].meta
    assert nearby_meta.params is NearbyParams
    assert nearby_meta.llm_result_model is NearbyResult
    assert nearby_meta.input_schema is not None
    assert nearby_meta.input_schema["required"] == ["query"]

    food_meta = FoodRecommendRouter().collect_entries()["food_recommend"].meta
    assert food_meta.params is FoodRecommendParams
    assert food_meta.llm_result_model is FoodRecommendResult
    assert food_meta.input_schema is not None
    assert "required" not in food_meta.input_schema

    unit_meta = UnitConvertRouter().collect_entries()["unit_convert"].meta
    assert unit_meta.params is UnitConvertParams
    assert unit_meta.llm_result_model is UnitConvertResult
    assert unit_meta.input_schema is not None
    assert set(unit_meta.input_schema["required"]) == {"value", "from_unit", "to_unit"}


def test_lifekit_router_entries_do_not_use_legacy_contracts() -> None:
    from plugin.plugins.lifekit.routers import (
        AirQualityRouter,
        CountdownRouter,
        CurrencyRouter,
        CurrentWeatherRouter,
        FoodRecommendRouter,
        HourlyForecastRouter,
        LocationsRouter,
        NearbyRouter,
        RecipeRouter,
        TravelAdviceRouter,
        TripRouter,
        UnitConvertRouter,
    )

    routers = [
        AirQualityRouter(),
        CountdownRouter(),
        CurrencyRouter(),
        CurrentWeatherRouter(),
        FoodRecommendRouter(),
        HourlyForecastRouter(),
        LocationsRouter(),
        NearbyRouter(),
        RecipeRouter(),
        TravelAdviceRouter(),
        TripRouter(),
        UnitConvertRouter(),
    ]

    for router in routers:
        for entry_id, handler in router.collect_entries().items():
            meta = handler.meta
            assert meta.llm_result_model is not None, f"{entry_id} missing llm_result_model"
            assert meta.llm_result_schema is not None, f"{entry_id} missing llm_result_schema"
            if entry_id not in {"list_locations", "random_recipe"}:
                assert meta.params is not None, f"{entry_id} missing params model"


def test_lifekit_entry_runtime_injects_pydantic_params() -> None:
    from plugin.plugins.lifekit._contracts import UnitConvertParams
    from plugin.plugins.lifekit.routers.unit_convert import UnitConvertRouter
    from plugin.sdk.shared.core.entry_runtime import prepare_entry_kwargs

    entry = UnitConvertRouter().collect_entries()["unit_convert"]
    kwargs = prepare_entry_kwargs(
        plugin_id="lifekit",
        entry_id="unit_convert",
        handler=entry.handler,
        meta=entry.meta,
        args={"value": "180", "from_unit": " cm ", "to_unit": " inch "},
    )

    assert "params" in kwargs
    assert isinstance(kwargs["params"], UnitConvertParams)
    assert kwargs["params"].value == 180
    assert kwargs["params"].from_unit == "cm"
    assert kwargs["params"].to_unit == "inch"


class _DummyLogger:
    def info(self, *_args, **_kwargs):
        pass

    def debug(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass

    def exception(self, *_args, **_kwargs):
        pass


class _DummyLifeKitContext:
    plugin_id = "lifekit"
    metadata = {}
    logger = _DummyLogger()
    config_path = Path(__file__).resolve().parents[3] / "plugins" / "lifekit" / "plugin.toml"
    bus = None
    _effective_config = {
        "lifekit": {},
        "plugin": {
            "store": {
                "enabled": False,
            },
        },
    }

    async def get_own_effective_config(self, profile_name=None, timeout=5.0):
        return dict(self._effective_config)

    async def get_own_config(self, timeout=5.0):
        return dict(self._effective_config)

    async def get_own_base_config(self, timeout=5.0):
        return dict(self._effective_config)

    async def get_own_profiles_state(self, timeout=5.0):
        return {}

    async def get_own_profile_config(self, profile_name: str, timeout=5.0):
        return {}

    async def update_own_config(self, updates, timeout=10.0):
        self._effective_config.update(updates)
        return dict(self._effective_config)

    async def upsert_own_profile_config(self, profile_name, config, *, make_active=False, timeout=10.0):
        return dict(config)

    async def delete_own_profile_config(self, profile_name: str, timeout=10.0):
        return {}

    async def set_own_active_profile(self, profile_name: str, timeout=10.0):
        return {}

    async def query_plugins(self, filters, timeout=5.0):
        return []

    async def trigger_plugin_event(self, **kwargs):
        return {}

    async def get_system_config(self, timeout=5.0):
        return {}

    async def query_memory(self, bucket_id: str, query: str, timeout=5.0):
        return []

    async def run_update_async(self, **kwargs):
        return {}

    async def export_push_async(self, **kwargs):
        return {}

    def push_message(self, **kwargs):
        return {}

    def update_status(self, status):
        pass


@pytest.mark.asyncio
async def test_lifekit_startup_uses_host_global_locale(monkeypatch) -> None:
    from plugin.plugins import lifekit as lifekit_module
    from plugin.plugins.lifekit import LifeKitPlugin

    monkeypatch.setattr(lifekit_module, "get_system_timezone", lambda: "UTC")
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "en")

    plugin = LifeKitPlugin(_DummyLifeKitContext())
    result = await plugin.startup()

    assert result.is_ok()
    assert result.value == {"status": "ready"}
    assert plugin._i18n.locale == "en"


class _MemoryStore:
    enabled = True

    def __init__(self):
        self._data = {}

    async def get(self, key, default=None):
        await asyncio.sleep(0.01)
        value = self._data.get(key, default)
        return [dict(item) for item in value] if isinstance(value, list) else value

    async def set(self, key, value):
        await asyncio.sleep(0.01)
        self._data[key] = [dict(item) for item in value]
        from plugin.sdk.shared.models import Ok

        return Ok(None)


@pytest.mark.asyncio
async def test_lifekit_add_location_serializes_store_updates(monkeypatch) -> None:
    from plugin.plugins.lifekit import LifeKitPlugin
    from plugin.plugins.lifekit import routers

    async def fake_geocode(city: str, **_kwargs):
        return {"city": city, "lat": 1.0, "lon": 2.0, "country": "ZZ"}

    monkeypatch.setattr(routers.locations, "geocode_city", fake_geocode)

    plugin = LifeKitPlugin(_DummyLifeKitContext())
    plugin.store = _MemoryStore()
    router = next(item for item in plugin._routers if item.name() == "locations")

    first, second = await asyncio.gather(
        router.add_location(label="Home", city="Tokyo"),
        router.add_location(label="Office", city="Osaka"),
    )

    assert first.is_ok()
    assert second.is_ok()
    locations = plugin.store._data["saved_locations"]
    assert {item["label"] for item in locations} == {"Home", "Office"}
    assert sum(1 for item in locations if item.get("is_default")) == 1
