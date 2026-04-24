"""
天气出行插件 (Weather & Travel)

基于 Open-Meteo（免费无 Key）提供：
- 当前天气 + 每日预报 (get_weather)
- 逐小时预报 (hourly_forecast)
- 穿衣 / 带伞 / 紫外线等出行建议 (travel_advice)

模块化架构：entry 通过 Router 注册，便于横向扩展。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    lifecycle,
    Ok,
    Err,
    SdkError,
)

from ._i18n import I18n, LRUCache
from ._geo import get_system_timezone, detect_vpn_conflict
from ._api import geoip_locate, geocode_city, fetch_forecast
from .routers import CurrentWeatherRouter, TravelAdviceRouter, HourlyForecastRouter

_LOCALES_DIR = Path(__file__).parent / "locales"


@neko_plugin
class WeatherPlugin(NekoPluginBase):
    """天气出行插件 — 生命周期 + 共享基础设施。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._cache = LRUCache(32)
        self._cfg: Dict[str, Any] = {}
        self._i18n = I18n(_LOCALES_DIR)

        # 注册 routers（模块化 entry）— 必须在 __init__ 中注册，
        # 这样 collect_entries() 在 startup 之前就能扫描到 router 的 entry。
        self.include_router(CurrentWeatherRouter())
        self.include_router(TravelAdviceRouter())
        self.include_router(HourlyForecastRouter())

    # ── 生命周期 ──

    @lifecycle(id="startup")
    async def startup(self, **_):
        await self._reload_config()

        # 从主干查询全局语言
        lang = await self.fetch_user_language(timeout=3.0)
        self._resolve_locale()
        self.logger.info(
            "WeatherPlugin started, locale={}, host_lang={}, routers=3",
            self._i18n.locale, lang or "(none)",
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
        self._cfg = cfg.get("weather", {}) if isinstance(cfg.get("weather"), dict) else {}

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

    async def _resolve_location(self, city: Optional[str] = None) -> Optional[Dict[str, Any]]:
        locale = self._i18n.locale
        target = (city or "").strip()
        if target:
            return await geocode_city(target, locale=locale)

        default = self._cfg.get("default_city", "")
        if default:
            return await geocode_city(default, locale=locale)

        ip_loc = await geoip_locate(locale=locale)
        if ip_loc is None:
            return await self._timezone_fallback()

        ip_tz = ip_loc.get("ip_timezone", "")
        system_tz = get_system_timezone()

        if detect_vpn_conflict(ip_tz, system_tz):
            self.logger.info("VPN detected: IP tz={} vs system tz={}", ip_tz, system_tz)
            fallback = await self._timezone_fallback(system_tz)
            if fallback:
                fallback["_vpn_detected"] = True
                fallback["_ip_city"] = ip_loc.get("city", "")
                return fallback

        ip_loc.pop("ip_timezone", None)
        return ip_loc

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

    async def _get_weather_data(self, loc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        cache_key = f"{loc['lat']:.2f},{loc['lon']:.2f}"
        ttl = int(self._cfg.get("cache_ttl_seconds", 1800))
        cached = self._cache.get(cache_key, ttl)
        if cached is not None:
            return cached

        days = int(self._cfg.get("forecast_days", 3))
        tz = str(self._cfg.get("timezone", "Asia/Shanghai"))
        data = await fetch_forecast(loc["lat"], loc["lon"], days=days, tz=tz)
        if data:
            self._cache.put(cache_key, data)
        return data

    def _wmo_text(self, code: int) -> str:
        text = self._i18n.t(f"wmo.{code}")
        if text == f"wmo.{code}":
            return self._i18n.t("error.unknown_weather", code=code)
        return text
