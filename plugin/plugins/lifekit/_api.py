"""Open-Meteo / IP-geolocation network request wrappers."""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from ._geocoders import nominatim_candidates, open_meteo_candidates
from ._location import LocationPurpose, LocationRequest, LocationResolver, LocationStatus

# HTTPS IP geolocation provider; the old free ip-api.com endpoint used plain HTTP and prohibited commercial use, meow.
_GEOIP_BASE = "https://ipapi.co"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
_UA = "NEKO-LifeKit-Plugin/0.3"

# ipapi.co uses ISO 639 short codes; a Content-Language header suffices.
LOCALE_TO_GEOIP_LANG: Dict[str, str] = {
    "zh-CN": "zh",
    "zh-TW": "zh",
    "en": "en",
    "ja": "ja",
    "ko": "ko",
    "ru": "ru",
    "es": "es",
    "pt": "pt",
}

# WMO precipitation/snowfall code set.
RAIN_CODES = frozenset({51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99})
SNOW_CODES = frozenset({71, 73, 75, 77, 85, 86})


# ── Error types ─────────────────────────────────────────────────────

class WeatherAPIError(Exception):
    """Base class for weather API failures."""
    def __init__(self, message: str, cause: str = "unknown"):
        super().__init__(message)
        self.cause = cause  # "timeout" | "network" | "api_error" | "not_found"


class GeoIPError(WeatherAPIError):
    """IP geolocation failed."""


class GeocodeError(WeatherAPIError):
    """City geocoding failed."""


class ForecastError(WeatherAPIError):
    """Weather forecast API failed."""


# ── API functions ─────────────────────────────────────────────────────

async def geoip_locate(locale: str = "zh-CN", timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    """IP geolocation (HTTPS via ipapi.co). Return a dict on success, None without results; raise GeoIPError for network issues."""
    lang = LOCALE_TO_GEOIP_LANG.get(locale, "en")
    url = f"{_GEOIP_BASE}/json/"
    try:
        async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as c:
            r = await c.get(url, headers={"User-Agent": _UA, "Accept-Language": lang})
            d = r.json()
            lat, lon = d.get("latitude"), d.get("longitude")
            if lat is not None and lon is not None:
                return {
                    "city": d.get("city") or d.get("region") or "",
                    "lat": float(lat),
                    "lon": float(lon),
                    "country": d.get("country_code", ""),
                    "ip_timezone": d.get("timezone", ""),
                }
            return None
    except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout):
        raise GeoIPError("IP locate timed out", cause="timeout")
    except httpx.ConnectError:
        raise GeoIPError("IP locate connection failed", cause="network")
    except Exception as e:
        raise GeoIPError(f"IP locate failed: {e}", cause="network")


async def geocode_city(city: str, locale: str = "zh-CN", timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """Strict compatibility wrapper used by saved-location writes."""

    async def open_meteo(query: str, **kwargs: Any):
        return await open_meteo_candidates(
            query,
            locale=str(kwargs.get("locale") or locale),
            country_code=str(kwargs.get("country_code") or ""),
            timeout=min(timeout, 2.0),
        )

    async def nominatim(query: str, **kwargs: Any):
        return await nominatim_candidates(
            query,
            locale=str(kwargs.get("locale") or locale),
            country_code=str(kwargs.get("country_code") or ""),
            timeout=timeout,
        )

    result = await LocationResolver(
        open_meteo=open_meteo,
        nominatim=nominatim,
    ).resolve(
        LocationRequest(
            text=city,
            purpose=LocationPurpose.SAVE,
            allow_geoip=False,
            locale=locale,
        )
    )
    if result.status is LocationStatus.RESOLVED and result.location is not None:
        return result.location.as_legacy_dict()
    if result.status is LocationStatus.PROVIDER_FAILED:
        raise GeocodeError(
            f"Geocode '{city}' failed",
            cause=result.cause or "network",
        )
    if result.status in {
        LocationStatus.AMBIGUOUS,
        LocationStatus.NEEDS_CONFIRMATION,
        LocationStatus.NOT_FOUND,
        LocationStatus.NO_LOCATION,
    }:
        raise GeocodeError(f"Geocode '{city}' unresolved", cause=result.status.value)
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
    """Call the Open-Meteo Forecast API. Return a dict on success; raise ForecastError on failure."""
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
                payload = r.json()
                if not isinstance(payload, dict):
                    raise ForecastError(
                        "Weather API returned an invalid response",
                        cause="api_error",
                    )
                return payload
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
    """Safely retrieve a daily-array element; reject negative indices to avoid silently showing the wrong day on upstream index fallback, meow."""
    arr = daily.get(field)
    if isinstance(arr, list) and 0 <= idx < len(arr):
        return arr[idx]
    return None


# ── Air Quality ──────────────────────────────────────────────────

_AQI_VARS = "european_aqi,pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone,uv_index"


class AirQualityError(WeatherAPIError):
    """Air quality API failed."""


async def fetch_air_quality(
    lat: float, lon: float,
    *,
    tz: str = "Asia/Shanghai",
    timeout: float = 8.0,
) -> Dict[str, Any]:
    """Call the Open-Meteo Air Quality API. Return current data."""
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
                payload = r.json()
                if not isinstance(payload, dict):
                    raise AirQualityError(
                        "Air quality API returned an invalid response",
                        cause="api_error",
                    )
                return payload
            raise AirQualityError(f"Air quality API returned HTTP {r.status_code}", cause="api_error")
    except AirQualityError:
        raise
    except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout):
        raise AirQualityError("Air quality API timed out", cause="timeout")
    except httpx.ConnectError:
        raise AirQualityError("Air quality API connection failed", cause="network")
    except Exception as e:
        raise AirQualityError(f"Air quality API failed: {e}", cause="network")
