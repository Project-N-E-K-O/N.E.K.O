"""Open-Meteo / ip-api 网络请求封装。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

_GEOIP_BASE = "http://ip-api.com/json/"
_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_UA = "NEKO-Weather-Plugin/0.1"

# locale → ip-api lang
LOCALE_TO_GEOIP_LANG: Dict[str, str] = {
    "zh-CN": "zh-CN", "zh-TW": "zh-CN", "en": "en",
}
# locale → Open-Meteo geocoding language
LOCALE_TO_GEOCODE_LANG: Dict[str, str] = {
    "zh-CN": "zh", "zh-TW": "zh", "en": "en",
}

# WMO 降水/降雪代码集
RAIN_CODES = frozenset({51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99})
SNOW_CODES = frozenset({71, 73, 75, 77, 85, 86})


async def geoip_locate(locale: str = "zh-CN", timeout: float = 4.0) -> Optional[Dict[str, Any]]:
    lang = LOCALE_TO_GEOIP_LANG.get(locale, "en")
    url = f"{_GEOIP_BASE}?fields=city,lat,lon,countryCode,regionName,timezone&lang={lang}"
    try:
        async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as c:
            r = await c.get(url, headers={"User-Agent": _UA})
            d = r.json()
            lat, lon = d.get("lat"), d.get("lon")
            if lat is not None and lon is not None:
                return {
                    "city": d.get("city") or d.get("regionName") or "",
                    "lat": float(lat),
                    "lon": float(lon),
                    "country": d.get("countryCode", ""),
                    "ip_timezone": d.get("timezone", ""),
                }
    except Exception:
        pass
    return None


async def geocode_city(city: str, locale: str = "zh-CN", timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    lang = LOCALE_TO_GEOCODE_LANG.get(locale, "en")
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(_GEOCODE_URL, params={"name": city, "count": 1, "language": lang})
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


async def fetch_forecast(
    lat: float, lon: float,
    *,
    days: int = 3,
    tz: str = "Asia/Shanghai",
    hourly_vars: Optional[str] = None,
    forecast_hours: Optional[int] = None,
    timeout: float = 8.0,
) -> Optional[Dict[str, Any]]:
    """调用 Open-Meteo Forecast API。

    Args:
        hourly_vars: 逐小时变量（逗号分隔），None 则不请求 hourly 数据。
        forecast_hours: 限制逐小时数据的时间范围（小时数）。
    """
    params: Dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,uv_index",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,uv_index_max,wind_speed_10m_max",
        "forecast_days": min(max(days, 1), 7),
        "timezone": tz,
    }
    if hourly_vars:
        params["hourly"] = hourly_vars
    if forecast_hours is not None and forecast_hours > 0:
        params["forecast_hours"] = forecast_hours
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(_FORECAST_URL, params=params)
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return None


def daily_val(daily: Dict[str, Any], field: str, idx: int) -> Any:
    """安全取 daily 数组元素。"""
    arr = daily.get(field)
    if isinstance(arr, list) and idx < len(arr):
        return arr[idx]
    return None
