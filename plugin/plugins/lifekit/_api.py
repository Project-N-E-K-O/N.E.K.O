"""Open-Meteo / ip-api 网络请求封装。"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

_GEOIP_BASE = "http://ip-api.com/json/"
_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
_UA = "NEKO-LifeKit-Plugin/0.2"

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


# ── 错误类型 ─────────────────────────────────────────────────────

class WeatherAPIError(Exception):
    """天气 API 调用失败的基类。"""
    def __init__(self, message: str, cause: str = "unknown"):
        super().__init__(message)
        self.cause = cause  # "timeout" | "network" | "api_error" | "not_found"


class GeoIPError(WeatherAPIError):
    """IP 定位失败。"""


class GeocodeError(WeatherAPIError):
    """城市 geocoding 失败。"""


class ForecastError(WeatherAPIError):
    """天气预报 API 失败。"""


# ── API 函数 ─────────────────────────────────────────────────────

async def geoip_locate(locale: str = "zh-CN", timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    """IP 定位。成功返回 dict，无结果返回 None，网络问题抛 GeoIPError。"""
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
            return None
    except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout):
        raise GeoIPError("IP locate timed out", cause="timeout")
    except httpx.ConnectError:
        raise GeoIPError("IP locate connection failed", cause="network")
    except Exception as e:
        raise GeoIPError(f"IP locate failed: {e}", cause="network")


_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


async def geocode_city(city: str, locale: str = "zh-CN", timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """Geocoding：先试 Open-Meteo（城市名），再试 Nominatim（支持地址）。"""
    lang = LOCALE_TO_GEOCODE_LANG.get(locale, "en")

    # 1. Open-Meteo（快，但只支持城市名）
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
    except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout):
        pass  # 继续尝试 Nominatim
    except httpx.ConnectError:
        pass
    except Exception:
        pass

    # 2. Nominatim fallback（支持地址、POI、街道等）
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(_NOMINATIM_URL, params={
                "q": city, "format": "json", "limit": "1",
                "accept-language": lang,
            }, headers={"User-Agent": _UA})
            results = r.json()
            if isinstance(results, list) and results:
                hit = results[0]
                display = hit.get("display_name", city).split(",")[0].strip()
                return {
                    "city": display,
                    "lat": float(hit["lat"]),
                    "lon": float(hit["lon"]),
                    "country": "",
                }
    except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout):
        raise GeocodeError(f"Geocode '{city}' timed out", cause="timeout")
    except httpx.ConnectError:
        raise GeocodeError(f"Geocode '{city}' connection failed", cause="network")
    except Exception as e:
        raise GeocodeError(f"Geocode '{city}' failed: {e}", cause="network")

    return None


async def fetch_forecast(
    lat: float, lon: float,
    *,
    days: int = 3,
    tz: str = "Asia/Shanghai",
    hourly_vars: Optional[str] = None,
    forecast_hours: Optional[int] = None,
    timeout: float = 8.0,
) -> Dict[str, Any]:
    """调用 Open-Meteo Forecast API。成功返回 dict，失败抛 ForecastError。"""
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
            raise ForecastError(f"API returned HTTP {r.status_code}", cause="api_error")
    except ForecastError:
        raise
    except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout):
        raise ForecastError("Weather API timed out", cause="timeout")
    except httpx.ConnectError:
        raise ForecastError("Weather API connection failed", cause="network")
    except Exception as e:
        raise ForecastError(f"Weather API failed: {e}", cause="network")


def daily_val(daily: Dict[str, Any], field: str, idx: int) -> Any:
    """安全取 daily 数组元素。"""
    arr = daily.get(field)
    if isinstance(arr, list) and idx < len(arr):
        return arr[idx]
    return None


# ── Air Quality ──────────────────────────────────────────────────

_AQI_VARS = "european_aqi,pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone,uv_index"


class AirQualityError(WeatherAPIError):
    """空气质量 API 失败。"""


async def fetch_air_quality(
    lat: float, lon: float,
    *,
    tz: str = "Asia/Shanghai",
    timeout: float = 8.0,
) -> Dict[str, Any]:
    """调用 Open-Meteo Air Quality API。返回 current 数据。"""
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": _AQI_VARS,
        "timezone": tz,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(_AIR_QUALITY_URL, params=params)
            if r.status_code == 200:
                return r.json()
            raise AirQualityError(f"Air quality API returned HTTP {r.status_code}", cause="api_error")
    except AirQualityError:
        raise
    except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout):
        raise AirQualityError("Air quality API timed out", cause="timeout")
    except httpx.ConnectError:
        raise AirQualityError("Air quality API connection failed", cause="network")
    except Exception as e:
        raise AirQualityError(f"Air quality API failed: {e}", cause="network")
