"""
天气出行插件 (Weather & Travel)

基于 Open-Meteo（免费无 Key）提供：
- 当前天气 + 未来多日预报
- 穿衣 / 带伞 / 紫外线等出行建议
- IP 自动定位（含 VPN 矛盾检测）或手动指定城市

配置通过 plugin.toml [weather] 段 + profile 系统管理。
i18n 文件位于 locales/ 目录，支持 zh-CN / zh-TW / en。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    Ok,
    Err,
    SdkError,
)

# ── 常量 ─────────────────────────────────────────────────────────

_RAIN_CODES = frozenset({51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99})
_SNOW_CODES = frozenset({71, 73, 75, 77, 85, 86})

_GEOIP_URL = "http://ip-api.com/json/?fields=city,lat,lon,countryCode,regionName,timezone&lang=zh-CN"
_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_UA = "NEKO-Weather-Plugin/0.1"

_LOCALES_DIR = Path(__file__).parent / "locales"
_DEFAULT_LOCALE = "zh-CN"
_SUPPORTED_LOCALES = ("zh-CN", "zh-TW", "en")


# ── i18n ─────────────────────────────────────────────────────────

class I18n:
    """轻量 i18n：从 locales/*.json 加载翻译，支持 dot-path 取值和模板插值。"""

    def __init__(self, locales_dir: Path, default: str = _DEFAULT_LOCALE):
        self._bundles: Dict[str, Dict[str, Any]] = {}
        self._default = default
        self._locale = default
        self._load_all(locales_dir)

    def _load_all(self, locales_dir: Path) -> None:
        for code in _SUPPORTED_LOCALES:
            fp = locales_dir / f"{code}.json"
            if fp.exists():
                with open(fp, "r", encoding="utf-8") as f:
                    self._bundles[code] = json.load(f)

    @property
    def locale(self) -> str:
        return self._locale

    def set_locale(self, code: str) -> None:
        normalized = self._normalize(code)
        if normalized in self._bundles:
            self._locale = normalized

    def _normalize(self, code: str) -> str:
        """将各种语言代码归一到支持的 locale key。"""
        if not code:
            return self._default
        c = code.strip().replace("_", "-")
        # 精确匹配
        if c in self._bundles:
            return c
        # 大小写不敏感
        lower = c.lower()
        for key in self._bundles:
            if key.lower() == lower:
                return key
        # 前缀匹配: "zh" → "zh-CN", "en-US" → "en"
        prefix = lower.split("-")[0]
        for key in self._bundles:
            if key.lower().startswith(prefix):
                return key
        return self._default

    def t(self, path: str, locale: Optional[str] = None, **kwargs: Any) -> str:
        """按 dot-path 取翻译文本，支持 {key} 模板插值。

        查找顺序：指定 locale → 当前 locale → default locale → 返回 path 本身。
        """
        for code in self._resolve_chain(locale):
            bundle = self._bundles.get(code)
            if bundle is None:
                continue
            val = self._get_nested(bundle, path)
            if val is not None:
                text = str(val)
                if kwargs:
                    try:
                        text = text.format(**kwargs)
                    except (KeyError, IndexError):
                        pass
                return text
        return path

    def _resolve_chain(self, locale: Optional[str]) -> List[str]:
        chain: List[str] = []
        if locale:
            n = self._normalize(locale)
            chain.append(n)
        if self._locale not in chain:
            chain.append(self._locale)
        if self._default not in chain:
            chain.append(self._default)
        return chain

    @staticmethod
    def _get_nested(d: Dict[str, Any], path: str) -> Any:
        parts = path.split(".")
        cur: Any = d
        for p in parts:
            if isinstance(cur, dict):
                cur = cur.get(p)
            else:
                return None
        return cur


# ── 系统时区检测 ─────────────────────────────────────────────────

def _get_system_timezone() -> Optional[str]:
    """获取系统本地时区名称（IANA 格式）。"""
    try:
        local_tz = datetime.now().astimezone().tzinfo
        if local_tz is None:
            return None
        tz_name = str(local_tz)
        if "/" not in tz_name:
            tz_name = _read_system_tz_iana() or tz_name
        return tz_name if "/" in tz_name else None
    except Exception:
        return None


def _read_system_tz_iana() -> Optional[str]:
    tz_env = os.environ.get("TZ", "").strip()
    if tz_env and "/" in tz_env:
        return tz_env.lstrip(":")
    try:
        with open("/etc/timezone", "r") as f:
            val = f.read().strip()
            if "/" in val:
                return val
    except Exception:
        pass
    try:
        link = os.readlink("/etc/localtime")
        idx = link.find("zoneinfo/")
        if idx >= 0:
            return link[idx + len("zoneinfo/"):]
    except Exception:
        pass
    return None


def _tz_offset_hours(tz_name: str) -> Optional[float]:
    try:
        zi = ZoneInfo(tz_name)
        offset = datetime.now(zi).utcoffset()
        if offset is not None:
            return offset.total_seconds() / 3600.0
    except Exception:
        pass
    return None


def _detect_vpn_conflict(ip_timezone: str, system_tz: Optional[str]) -> bool:
    """IP 时区与系统时区偏差 ≥ 2h 视为 VPN。"""
    if not ip_timezone or not system_tz:
        return False
    ip_off = _tz_offset_hours(ip_timezone)
    sys_off = _tz_offset_hours(system_tz)
    if ip_off is None or sys_off is None:
        return False
    return abs(ip_off - sys_off) >= 2.0


# ── 网络工具 ─────────────────────────────────────────────────────

async def _geoip_locate(timeout: float = 4.0) -> Optional[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as c:
            r = await c.get(_GEOIP_URL, headers={"User-Agent": _UA})
            d = r.json()
            lat, lon = d.get("lat"), d.get("lon")
            if lat is not None and lon is not None:
                city = d.get("city") or d.get("regionName") or ""
                return {
                    "city": city,
                    "lat": float(lat),
                    "lon": float(lon),
                    "country": d.get("countryCode", ""),
                    "ip_timezone": d.get("timezone", ""),
                }
    except Exception:
        pass
    return None


async def _geocode_city(city: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(_GEOCODE_URL, params={"name": city, "count": 1, "language": "zh"})
            results = r.json().get("results")
            if results:
                hit = results[0]
                return {
                    "city": hit.get("name", city),
                    "lat": float(hit["latitude"]),
                    "lon": float(hit["longitude"]),
                    "country": hit.get("country_code", ""),
                }
    except Exception:
        pass
    return None


async def _fetch_forecast(
    lat: float, lon: float, days: int = 3, tz: str = "Asia/Shanghai", timeout: float = 8.0,
) -> Optional[Dict[str, Any]]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,uv_index",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,uv_index_max,wind_speed_10m_max",
        "forecast_days": min(max(days, 1), 7),
        "timezone": tz,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(_FORECAST_URL, params=params)
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return None


# ── 出行建议生成 ─────────────────────────────────────────────────

def _build_travel_advice(
    current: Dict[str, Any], daily: Dict[str, Any], t: "I18n",
) -> Dict[str, Any]:
    temp = current.get("temperature_2m")
    code = current.get("weather_code", -1)
    uv = current.get("uv_index", 0)
    wind = current.get("wind_speed_10m", 0)

    tips: List[str] = []

    if temp is not None:
        if temp < 5:
            tips.append(t.t("advice.cold"))
        elif temp < 15:
            tips.append(t.t("advice.cool"))
        elif temp < 25:
            tips.append(t.t("advice.mild"))
        else:
            tips.append(t.t("advice.hot"))

    if code in _RAIN_CODES:
        tips.append(t.t("advice.rain"))
    elif code in _SNOW_CODES:
        tips.append(t.t("advice.snow"))

    if uv >= 8:
        tips.append(t.t("advice.uv_extreme"))
    elif uv >= 5:
        tips.append(t.t("advice.uv_high"))

    if wind >= 40:
        tips.append(t.t("advice.wind_strong"))

    daily_codes = daily.get("weather_code", [])
    daily_dates = daily.get("time", [])
    rain_days = [
        daily_dates[i] for i, c in enumerate(daily_codes)
        if c in _RAIN_CODES and i < len(daily_dates)
    ]
    if rain_days:
        tips.append(t.t("advice.rain_forecast", dates=", ".join(rain_days)))

    eff_temp = temp if temp is not None else 20
    if eff_temp < 10:
        clothing = t.t("clothing.heavy")
    elif eff_temp < 22:
        clothing = t.t("clothing.light")
    else:
        clothing = t.t("clothing.cool")

    return {
        "tips": tips,
        "clothing": clothing,
        "umbrella": code in _RAIN_CODES,
        "sunscreen": uv >= 5,
    }


# ── 插件主体 ─────────────────────────────────────────────────────

@neko_plugin
class WeatherPlugin(NekoPluginBase):
    """天气出行插件"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._cache: Dict[str, Any] = {}
        self._cfg: Dict[str, Any] = {}
        self._i18n = I18n(_LOCALES_DIR)

    # ── 生命周期 ──

    @lifecycle(id="startup")
    async def startup(self, **_):
        await self._reload_config()
        self.logger.info("WeatherPlugin started, locale={}", self._i18n.locale)
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
        # 从配置读取语言偏好
        lang = self._cfg.get("language", "")
        if lang:
            self._i18n.set_locale(lang)
        else:
            self._detect_locale()

    def _detect_locale(self) -> None:
        """根据系统时区自动推断 locale。"""
        tz = _get_system_timezone() or ""
        if tz.startswith("Asia/Taipei") or tz.startswith("Asia/Hong_Kong"):
            self._i18n.set_locale("zh-TW")
        elif tz.startswith("Asia/Shanghai") or tz.startswith("Asia/Chongqing"):
            self._i18n.set_locale("zh-CN")
        else:
            self._i18n.set_locale("en")

    # ── 位置解析 ──

    async def _resolve_location(self, city: Optional[str] = None) -> Optional[Dict[str, Any]]:
        target = (city or "").strip()
        if target:
            return await _geocode_city(target)

        default = self._cfg.get("default_city", "")
        if default:
            return await _geocode_city(default)

        ip_loc = await _geoip_locate()
        if ip_loc is None:
            return await self._timezone_fallback()

        ip_tz = ip_loc.get("ip_timezone", "")
        system_tz = _get_system_timezone()

        if _detect_vpn_conflict(ip_tz, system_tz):
            self.logger.info(
                "VPN detected: IP tz={} vs system tz={}", ip_tz, system_tz,
            )
            fallback = await self._timezone_fallback(system_tz)
            if fallback:
                fallback["_vpn_detected"] = True
                fallback["_ip_city"] = ip_loc.get("city", "")
                return fallback

        ip_loc.pop("ip_timezone", None)
        return ip_loc

    async def _timezone_fallback(self, system_tz: Optional[str] = None) -> Optional[Dict[str, Any]]:
        tz = system_tz or _get_system_timezone()
        if not tz:
            return None
        fallback_city = self._i18n.t(f"tz_city.{tz}")
        # t() 返回 path 本身说明没命中，尝试从时区名提取
        if fallback_city == f"tz_city.{tz}":
            parts = tz.split("/")
            fallback_city = parts[-1].replace("_", " ") if len(parts) >= 2 else ""
        if fallback_city:
            return await _geocode_city(fallback_city)
        return None

    # ── 天气数据（带缓存）──

    async def _get_weather_data(self, loc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        cache_key = f"{loc['lat']:.2f},{loc['lon']:.2f}"
        ttl = int(self._cfg.get("cache_ttl_seconds", 1800))
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached["ts"]) < ttl:
            return cached["data"]

        days = int(self._cfg.get("forecast_days", 3))
        tz = str(self._cfg.get("timezone", "Asia/Shanghai"))
        data = await _fetch_forecast(loc["lat"], loc["lon"], days=days, tz=tz)
        if data:
            self._cache[cache_key] = {"data": data, "ts": time.time()}
        return data

    # ── 辅助：WMO code → 翻译文本 ──

    def _wmo_text(self, code: int) -> str:
        text = self._i18n.t(f"wmo.{code}")
        if text == f"wmo.{code}":
            return self._i18n.t("error.unknown_weather", code=code)
        return text

    # ── Entry: 获取天气 ──

    @plugin_entry(
        id="get_weather",
        name="获取天气",
        description="查询指定城市（或自动定位）的当前天气和未来预报。",
        llm_result_fields=["summary", "current", "forecast"],
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名（中文/英文均可），留空则自动定位",
                    "default": "",
                },
            },
        },
    )
    async def get_weather(self, city: str = "", **_):
        loc = await self._resolve_location(city)
        if not loc:
            return Err(SdkError(self._i18n.t("error.no_location")))

        data = await self._get_weather_data(loc)
        if not data:
            return Err(SdkError(self._i18n.t("error.fetch_failed", city=loc["city"])))

        current_raw = data.get("current", {})
        daily_raw = data.get("daily", {})

        code = current_raw.get("weather_code", -1)
        current = {
            "weather": self._wmo_text(code),
            "temperature": current_raw.get("temperature_2m"),
            "feels_like": current_raw.get("apparent_temperature"),
            "humidity": current_raw.get("relative_humidity_2m"),
            "wind_speed": current_raw.get("wind_speed_10m"),
            "uv_index": current_raw.get("uv_index"),
        }

        forecast: List[Dict[str, Any]] = []
        dates = daily_raw.get("time", [])
        for i, date in enumerate(dates):
            d_code = (daily_raw.get("weather_code") or [])[i] if i < len(daily_raw.get("weather_code", [])) else -1
            forecast.append({
                "date": date,
                "weather": self._wmo_text(d_code),
                "temp_max": (daily_raw.get("temperature_2m_max") or [])[i] if i < len(daily_raw.get("temperature_2m_max", [])) else None,
                "temp_min": (daily_raw.get("temperature_2m_min") or [])[i] if i < len(daily_raw.get("temperature_2m_min", [])) else None,
                "precipitation": (daily_raw.get("precipitation_sum") or [])[i] if i < len(daily_raw.get("precipitation_sum", [])) else None,
                "uv_max": (daily_raw.get("uv_index_max") or [])[i] if i < len(daily_raw.get("uv_index_max", [])) else None,
                "wind_max": (daily_raw.get("wind_speed_10m_max") or [])[i] if i < len(daily_raw.get("wind_speed_10m_max", [])) else None,
            })

        summary = self._i18n.t(
            "summary.weather",
            city=loc["city"],
            weather=current["weather"],
            temp=current["temperature"],
            feels=current["feels_like"],
            humidity=current["humidity"],
        )
        if loc.get("_vpn_detected"):
            summary += self._i18n.t("summary.vpn_hint", ip_city=loc.get("_ip_city", ""))

        return Ok({
            "city": loc["city"],
            "summary": summary,
            "current": current,
            "forecast": forecast,
            "vpn_detected": bool(loc.get("_vpn_detected")),
        })

    # ── Entry: 出行建议 ──

    @plugin_entry(
        id="travel_advice",
        name="出行建议",
        description="根据天气给出穿衣、带伞、防晒等出行建议。",
        llm_result_fields=["summary", "tips"],
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名，留空则自动定位",
                    "default": "",
                },
            },
        },
    )
    async def travel_advice(self, city: str = "", **_):
        loc = await self._resolve_location(city)
        if not loc:
            return Err(SdkError(self._i18n.t("error.no_location")))

        data = await self._get_weather_data(loc)
        if not data:
            return Err(SdkError(self._i18n.t("error.fetch_failed", city=loc["city"])))

        current_raw = data.get("current", {})
        daily_raw = data.get("daily", {})
        advice = _build_travel_advice(current_raw, daily_raw, self._i18n)

        summary = self._i18n.t("summary.travel_prefix", city=loc["city"])
        summary += " ".join(advice["tips"][:3])

        return Ok({
            "city": loc["city"],
            "summary": summary,
            **advice,
        })
