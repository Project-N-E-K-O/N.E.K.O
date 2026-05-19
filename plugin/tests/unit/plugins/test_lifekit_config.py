from __future__ import annotations

from pathlib import Path

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
