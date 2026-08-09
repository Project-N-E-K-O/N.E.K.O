"""Network adapters that return location candidates instead of a first hit."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ._location import LocationCandidate

_OPEN_METEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "NEKO-LifeKit-Plugin/0.3"
_NOMINATIM_LOCK = asyncio.Lock()


class GeocoderError(Exception):
    def __init__(self, message: str, *, cause: str):
        super().__init__(message)
        self.cause = cause


async def open_meteo_candidates(
    query: str,
    *,
    locale: str = "zh-CN",
    country_code: str = "",
    timeout: float = 2.0,
) -> list[LocationCandidate]:
    params: dict[str, Any] = {
        "name": query,
        "count": 10,
        "language": _language(locale),
    }
    if country_code:
        params["countryCode"] = country_code.upper()

    payload = await _get_json(_OPEN_METEO_URL, params=params, timeout=timeout)
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []

    candidates: list[LocationCandidate] = []
    for hit in results:
        if not isinstance(hit, dict):
            continue
        try:
            candidates.append(
                LocationCandidate(
                    display_name=str(hit.get("name") or query).strip(),
                    latitude=float(hit["latitude"]),
                    longitude=float(hit["longitude"]),
                    country_code=str(hit.get("country_code") or "").upper(),
                    admin1=str(hit.get("admin1") or "").strip(),
                    admin2=str(hit.get("admin2") or "").strip(),
                    precision=_open_meteo_precision(str(hit.get("feature_code") or "")),
                    source="open_meteo",
                    timezone=str(hit.get("timezone") or ""),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return candidates


async def nominatim_candidates(
    query: str,
    *,
    locale: str = "zh-CN",
    country_code: str = "",
    timeout: float = 2.0,
) -> list[LocationCandidate]:
    params: dict[str, Any] = {
        "q": query,
        "format": "jsonv2",
        "limit": 10,
        "addressdetails": 1,
        "accept-language": _language(locale),
    }
    if country_code:
        params["countrycodes"] = country_code.lower()

    async with _NOMINATIM_LOCK:
        payload = await _get_json(
            _NOMINATIM_URL,
            params=params,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
        )
    if not isinstance(payload, list):
        return []

    candidates: list[LocationCandidate] = []
    for hit in payload:
        if not isinstance(hit, dict):
            continue
        address = hit.get("address") if isinstance(hit.get("address"), dict) else {}
        try:
            candidates.append(
                LocationCandidate(
                    display_name=_nominatim_name(hit, query),
                    latitude=float(hit["lat"]),
                    longitude=float(hit["lon"]),
                    country_code=str(address.get("country_code") or "").upper(),
                    admin1=str(
                        address.get("state") or address.get("province") or ""
                    ).strip(),
                    admin2=str(
                        address.get("city")
                        or address.get("municipality")
                        or address.get("county")
                        or ""
                    ).strip(),
                    precision=_nominatim_precision(
                        str(hit.get("addresstype") or hit.get("type") or "")
                    ),
                    source="nominatim",
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return candidates


async def _get_json(
    url: str,
    *,
    params: dict[str, Any],
    timeout: float,
    headers: dict[str, str] | None = None,
) -> Any:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=params, headers=headers)
            if response.status_code != 200:
                raise GeocoderError(
                    f"geocoder returned HTTP {response.status_code}",
                    cause="api_error",
                )
            return response.json()
    except GeocoderError:
        raise
    except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise GeocoderError("geocoder timed out", cause="timeout") from exc
    except httpx.ConnectError as exc:
        raise GeocoderError("geocoder connection failed", cause="network") from exc
    except Exception as exc:
        raise GeocoderError("invalid geocoder response", cause="network") from exc


def _language(locale: str) -> str:
    language = "zh" if locale.startswith("zh") else locale.split("-", 1)[0]
    return language if language in {"zh", "en", "ja", "ko", "ru", "es", "pt"} else "en"


def _open_meteo_precision(feature_code: str) -> str:
    code = feature_code.upper()
    if code.startswith("PPLA") or code == "PPLC":
        return "city"
    if code.startswith("ADM1"):
        return "region"
    if code.startswith("ADM2"):
        return "district"
    return "locality"


def _nominatim_precision(address_type: str) -> str:
    kind = address_type.casefold()
    if kind in {
        "house",
        "building",
        "road",
        "street",
        "residential",
        "commercial",
        "amenity",
    }:
        return "address"
    if kind in {"borough", "district", "county", "city_district", "suburb"}:
        return "district"
    if kind in {"city", "town", "municipality"}:
        return "city"
    if kind in {"state", "province", "region"}:
        return "region"
    return "locality"


def _nominatim_name(hit: dict[str, Any], fallback: str) -> str:
    named = str(hit.get("name") or "").strip()
    if named:
        return named
    display = str(hit.get("display_name") or fallback)
    return display.split(",", 1)[0].strip()
