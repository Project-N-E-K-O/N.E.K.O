"""
生活助手插件 (Life Kit)

基于地理位置的多功能生活服务：
- 当前天气 + 每日预报 (get_weather)
- 逐小时预报 (hourly_forecast)
- 穿衣 / 带伞 / 紫外线等出行建议 (travel_advice)
- 路线规划 (trip_advice)
- 常用地点管理 (list/add/remove/set_default_location)
- 附近 POI 搜索 (search_nearby)

模块化架构：entry 通过 Router 注册，便于横向扩展。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    Ok,
    Err,
    SdkError,
    PluginSettings,
    SettingsField,
)

from ._i18n import I18n, LRUCache
from ._geo import get_system_timezone, detect_vpn_conflict
from ._api import geoip_locate, geocode_city, fetch_forecast, GeoIPError, GeocodeError, ForecastError, WeatherAPIError
from .routers import (
    CurrentWeatherRouter, TravelAdviceRouter, HourlyForecastRouter,
    LocationsRouter, TripRouter, NearbyRouter,
    FoodRecommendRouter, RecipeRouter,
    AirQualityRouter, CurrencyRouter,
)

_LOCALES_DIR = Path(__file__).parent / "locales"


@neko_plugin
class LifeKitPlugin(NekoPluginBase):
    """生活助手插件 — 生命周期 + 共享基础设施。"""

    class Settings(PluginSettings):
        """生活助手配置 — hot 字段会自动出现在聊天面板中。"""
        model_config = {"toml_section": "lifekit"}

        default_city: str = SettingsField("", hot=True, description="默认城市（留空则自动定位）")
        timezone: str = SettingsField("Asia/Shanghai", hot=True, description="时区")
        forecast_days: int = SettingsField(3, hot=True, ge=1, le=7, description="预报天数")
        cache_ttl_seconds: int = SettingsField(1800, description="缓存有效期（秒）")
        locale: str = SettingsField("", hot=True, description="语言（留空自动检测）", json_schema_extra={"hot": True, "enum": ["", "zh-CN", "zh-TW", "en"]})
        force_locale: bool = SettingsField(False, description="强制使用上面的语言设置")

    # 声明 router 类，供主进程静态扫描 entry 元数据
    __routers__ = [
        CurrentWeatherRouter, TravelAdviceRouter, HourlyForecastRouter,
        LocationsRouter, TripRouter, NearbyRouter,
        FoodRecommendRouter, RecipeRouter,
        AirQualityRouter, CurrencyRouter,
    ]

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._cache = LRUCache(32)
        self._cfg: Dict[str, Any] = {}
        self._i18n = I18n(_LOCALES_DIR)

        # 注册 routers — 必须在 __init__ 中，collect_entries 在 startup 之前调用
        for router_cls in self.__routers__:
            self.include_router(router_cls())

    # ── 生命周期 ──

    @lifecycle(id="startup")
    async def startup(self, **_):
        await self._reload_config()

        # 确保 store 启用（和 memo_reminder 同样的处理）
        if not self.store.enabled:
            store_cfg = (await self.config.dump(timeout=5.0) or {}).get("plugin", {})
            store_cfg = store_cfg.get("store", {}) if isinstance(store_cfg, dict) else {}
            if isinstance(store_cfg, dict) and store_cfg.get("enabled"):
                self.store.enabled = True
                self.logger.info("Store enabled from config (was disabled at init)")
            else:
                self.store.enabled = True
                self.logger.warning("Store force-enabled (config missing plugin.store.enabled)")

        # 从主干查询全局语言
        lang = await self.fetch_user_language(timeout=3.0)
        self._resolve_locale()
        # 注册 Web UI（地点管理面板）
        self.register_static_ui("static")
        self.logger.info(
            "LifeKitPlugin started, locale={}, host_lang={}, store={}",
            self._i18n.locale, lang or "(none)", self.store.enabled,
        )
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "stopped"})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        await self._reload_config()
        return Ok({"status": "reloaded"})

    async def _reload_config(self):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg.get("lifekit", {}) if isinstance(cfg.get("lifekit"), dict) else {}

    # ── locale 解析（供 routers 调用）──

    def _resolve_locale(self) -> None:
        """优先级：force_locale > host lang > toml locale > 系统时区"""
        force = bool(self._cfg.get("force_locale", False))
        configured = str(self._cfg.get("locale", "")).strip()

        if force and configured:
            self._i18n.set_locale(configured)
            return

        host_lang = self.get_user_language()
        if host_lang:
            self._i18n.set_locale(host_lang)
            return

        if configured:
            self._i18n.set_locale(configured)
            return

        tz = get_system_timezone() or ""
        if tz.startswith("Asia/Taipei") or tz.startswith("Asia/Hong_Kong"):
            self._i18n.set_locale("zh-TW")
        elif tz.startswith("Asia/Shanghai") or tz.startswith("Asia/Chongqing"):
            self._i18n.set_locale("zh-CN")
        else:
            self._i18n.set_locale("en")

    # ── 共享：位置解析（供 routers 调用）──

    async def _resolve_location(self, city: Optional[str] = None) -> tuple[Optional[Dict[str, Any]], str]:
        """解析位置。返回 (location_dict, error_key)。

        成功时 error_key 为空字符串，失败时为 i18n key。
        """
        locale = self._i18n.locale
        target = (city or "").strip()

        # 1. 用户本次指定的城市
        if target:
            # 检查是否匹配保存的地点标签
            saved = await self._get_saved_default_or_named(target)
            if saved:
                return saved, ""
            try:
                loc = await geocode_city(target, locale=locale)
                if loc:
                    return loc, ""
                return None, "error.city_not_found"
            except GeocodeError as e:
                return None, "error.geocode_timeout" if e.cause == "timeout" else "error.geocode_failed"

        # 2. 保存的默认地点（PluginStore）
        saved_default = await self._get_saved_default_or_named(None)
        if saved_default:
            return saved_default, ""

        # 3. 配置文件的 default_city
        default = self._cfg.get("default_city", "")
        if default:
            try:
                loc = await geocode_city(default, locale=locale)
                if loc:
                    return loc, ""
                return None, "error.city_not_found"
            except GeocodeError as e:
                return None, "error.geocode_timeout" if e.cause == "timeout" else "error.geocode_failed"

        # IP 定位
        ip_loc = None
        try:
            ip_loc = await geoip_locate(locale=locale)
        except GeoIPError:
            pass  # IP 定位失败不致命，继续 fallback

        if ip_loc is None:
            fallback = await self._timezone_fallback()
            if fallback:
                return fallback, ""
            return None, "error.no_location"

        ip_tz = ip_loc.get("ip_timezone", "")
        system_tz = get_system_timezone()

        if detect_vpn_conflict(ip_tz, system_tz):
            self.logger.info("VPN detected: IP tz={} vs system tz={}", ip_tz, system_tz)
            fallback = await self._timezone_fallback(system_tz)
            if fallback:
                fallback["_vpn_detected"] = True
                fallback["_ip_city"] = ip_loc.get("city", "")
                return fallback, ""

        ip_loc.pop("ip_timezone", None)
        return ip_loc, ""

    async def _timezone_fallback(self, system_tz: Optional[str] = None) -> Optional[Dict[str, Any]]:
        tz = system_tz or get_system_timezone()
        if not tz:
            return None
        fallback_city = self._i18n.t(f"tz_city.{tz}")
        if fallback_city == f"tz_city.{tz}":
            parts = tz.split("/")
            fallback_city = parts[-1].replace("_", " ") if len(parts) >= 2 else ""
        if fallback_city:
            return await geocode_city(fallback_city, locale=self._i18n.locale)
        return None

    # ── 共享：天气数据（LRU 缓存，供 routers 调用）──

    async def _get_weather_data(self, loc: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], str]:
        """获取天气数据。返回 (data, error_key)。"""
        cache_key = f"{loc['lat']:.2f},{loc['lon']:.2f}"
        ttl = int(self._cfg.get("cache_ttl_seconds", 1800))
        cached = self._cache.get(cache_key, ttl)
        if cached is not None:
            return cached, ""

        days = int(self._cfg.get("forecast_days", 3))
        tz = str(self._cfg.get("timezone", "Asia/Shanghai"))
        try:
            data = await fetch_forecast(loc["lat"], loc["lon"], days=days, tz=tz)
            self._cache.put(cache_key, data)
            return data, ""
        except ForecastError as e:
            if e.cause == "timeout":
                return None, "error.forecast_timeout"
            return None, "error.fetch_failed"

    def _wmo_text(self, code: int) -> str:
        text = self._i18n.t(f"wmo.{code}")
        if text == f"wmo.{code}":
            return self._i18n.t("error.unknown_weather", code=code)
        return text

    async def _get_saved_default_or_named(self, name: Optional[str]) -> Optional[Dict[str, Any]]:
        """从 PluginStore 读取保存的地点。

        name=None → 返回默认地点；name="家" → 返回标签匹配的地点。
        """
        try:
            result = await self.store.get("saved_locations", [])
            locations = result.value if hasattr(result, "value") else result
            if not isinstance(locations, list) or not locations:
                return None
            if name:
                for loc in locations:
                    if loc.get("label") == name:
                        return {"city": loc["city"], "lat": loc["lat"], "lon": loc["lon"], "country": loc.get("country", "")}
                return None
            # 返回默认地点
            for loc in locations:
                if loc.get("is_default"):
                    return {"city": loc["city"], "lat": loc["lat"], "lon": loc["lon"], "country": loc.get("country", "")}
            return None
        except Exception:
            return None

    # ── 配置读写（供 Web UI 调用）──

    @plugin_entry(
        id="get_config",
        name="获取配置",
        description="获取天气插件当前配置。",
    )
    async def get_config_entry(self, **_):
        return Ok(dict(self._cfg))

    @plugin_entry(
        id="update_config",
        name="更新配置",
        description="更新天气插件配置字段。",
        input_schema={
            "type": "object",
            "properties": {
                "default_city": {"type": "string"},
                "timezone": {"type": "string"},
                "forecast_days": {"type": "integer"},
                "locale": {"type": "string"},
                "cache_ttl_seconds": {"type": "integer"},
            },
        },
    )
    async def update_config_entry(self, **kwargs):
        allowed = {"default_city", "timezone", "forecast_days", "locale", "cache_ttl_seconds", "force_locale"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and not k.startswith("_")}
        if not updates:
            return Err(SdkError("No valid fields to update"))
        try:
            await self.config.update({"lifekit": updates})
            await self._reload_config()
            return Ok({"message": "Config updated", "config": dict(self._cfg)})
        except Exception as e:
            return Err(SdkError(f"Config update failed: {e}"))
