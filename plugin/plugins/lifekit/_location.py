"""Deterministic location resolution shared by LifeKit routers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import re
from typing import Awaitable, Callable, Optional


class LocationPurpose(str, Enum):
    WEATHER = "weather"
    AIR_QUALITY = "air_quality"
    NEARBY = "nearby"
    FOOD = "food"
    ROUTE_ORIGIN = "route_origin"
    ROUTE_DESTINATION = "route_destination"
    SAVE = "save"


class LocationStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NOT_FOUND = "not_found"
    PROVIDER_FAILED = "provider_failed"
    NO_LOCATION = "no_location"


@dataclass(frozen=True)
class LocationCandidate:
    display_name: str
    latitude: float
    longitude: float
    country_code: str = ""
    admin1: str = ""
    admin2: str = ""
    precision: str = "city"
    source: str = ""
    verified: bool = False
    timezone: str = ""

    def as_legacy_dict(self) -> dict[str, object]:
        return {
            "city": self.display_name,
            "lat": self.latitude,
            "lon": self.longitude,
            "country": self.country_code,
            "admin1": self.admin1,
            "admin2": self.admin2,
            "_location_precision": self.precision,
            "_location_source": self.source,
            "_location_verified": self.verified,
        }


@dataclass(frozen=True)
class LocationRequest:
    text: str = ""
    purpose: LocationPurpose = LocationPurpose.WEATHER
    country_hint: str = ""
    allow_geoip: bool = True
    locale: str = "zh-CN"


@dataclass(frozen=True)
class LocationResolution:
    status: LocationStatus
    location: Optional[LocationCandidate] = None
    candidates: tuple[LocationCandidate, ...] = field(default_factory=tuple)


Geocoder = Callable[..., Awaitable[list[LocationCandidate]]]
SavedLocationLoader = Callable[[], Awaitable[list["SavedLocation"]]]
GeoIPLocator = Callable[[], Awaitable[Optional[LocationCandidate]]]
DefaultTextProvider = Callable[[], str]


@dataclass(frozen=True)
class SavedLocation:
    label: str
    location: LocationCandidate
    is_default: bool = False


_ZH_NAME = re.compile(r"^[\u3400-\u9fff]{2,6}$")
_ADMIN_SUFFIXES = ("市", "省", "区", "县", "州", "旗")
_COUNTRY_ALIASES = {
    "cn": "CN",
    "china": "CN",
    "中国": "CN",
    "中国大陆": "CN",
    "中华人民共和国": "CN",
    "tw": "TW",
    "taiwan": "TW",
    "台湾": "TW",
    "us": "US",
    "usa": "US",
    "美国": "US",
    "jp": "JP",
    "japan": "JP",
    "日本": "JP",
}


class LocationResolver:
    """Resolve explicit location text without guessing between valid places."""

    def __init__(
        self,
        *,
        open_meteo: Geocoder,
        nominatim: Geocoder,
        saved_locations: Optional[SavedLocationLoader] = None,
        geoip: Optional[GeoIPLocator] = None,
        default_text: Optional[DefaultTextProvider] = None,
    ):
        self._open_meteo = open_meteo
        self._nominatim = nominatim
        self._saved_locations = saved_locations
        self._geoip = geoip
        self._default_text = default_text

    async def resolve(self, request: LocationRequest) -> LocationResolution:
        text = request.text.strip()
        text, explicit_country = _extract_country_hint(text)
        if explicit_country and not request.country_hint.strip():
            request = replace(request, country_hint=explicit_country)
        saved = await self._load_saved_locations()
        if not text:
            for item in saved:
                if not item.is_default:
                    continue
                if item.location.verified or request.purpose in {
                    LocationPurpose.WEATHER,
                    LocationPurpose.AIR_QUALITY,
                }:
                    return LocationResolution(
                        LocationStatus.RESOLVED,
                        location=item.location,
                        candidates=(item.location,),
                    )
                return LocationResolution(
                    LocationStatus.NEEDS_CONFIRMATION,
                    candidates=(item.location,),
                )
            default_text = self._default_text().strip() if self._default_text else ""
            if default_text:
                return await self._resolve_explicit(request, default_text)
            if request.allow_geoip and self._geoip is not None:
                try:
                    ip_location = await self._geoip()
                except Exception:
                    ip_location = None
                if ip_location is not None:
                    if request.purpose in {
                        LocationPurpose.WEATHER,
                        LocationPurpose.AIR_QUALITY,
                    }:
                        return LocationResolution(
                            LocationStatus.RESOLVED,
                            location=ip_location,
                            candidates=(ip_location,),
                        )
                    return LocationResolution(
                        LocationStatus.NEEDS_CONFIRMATION,
                        candidates=(ip_location,),
                    )
            return LocationResolution(LocationStatus.NO_LOCATION)

        for item in saved:
            if item.label.strip().casefold() == text.casefold():
                if not item.location.verified and request.purpose not in {
                    LocationPurpose.WEATHER,
                    LocationPurpose.AIR_QUALITY,
                }:
                    return LocationResolution(
                        LocationStatus.NEEDS_CONFIRMATION,
                        candidates=(item.location,),
                    )
                return LocationResolution(
                    LocationStatus.RESOLVED,
                    location=item.location,
                    candidates=(item.location,),
                )

        return await self._resolve_explicit(request, text)

    async def _resolve_explicit(
        self, request: LocationRequest, text: str
    ) -> LocationResolution:
        candidates: list[LocationCandidate] = []
        provider_succeeded = False
        disambiguation_required = _has_implicit_city_variant(text)
        for query in _query_variants(text):
            try:
                candidates.extend(
                    await self._open_meteo(
                        query,
                        country_code=request.country_hint.strip().upper(),
                        locale=request.locale,
                    )
                )
                provider_succeeded = True
            except Exception:
                continue

        candidates = _normalise_candidates(candidates, request.country_hint)
        disambiguation_succeeded = False
        if len(candidates) != 1 or disambiguation_required:
            try:
                candidates.extend(
                    await self._nominatim(
                        text,
                        country_code=request.country_hint.strip().upper(),
                        locale=request.locale,
                    )
                )
                provider_succeeded = True
                disambiguation_succeeded = True
            except Exception:
                pass
            candidates = _normalise_candidates(candidates, request.country_hint)

        countries = {item.country_code for item in candidates if item.country_code}
        if not request.country_hint.strip() and len(countries) > 1:
            return LocationResolution(
                LocationStatus.AMBIGUOUS,
                candidates=tuple(candidates),
            )

        eligible = _eligible_candidates(candidates, request.purpose)
        if disambiguation_required and not disambiguation_succeeded:
            if len(eligible) > 1:
                return LocationResolution(
                    LocationStatus.AMBIGUOUS,
                    candidates=tuple(eligible),
                )
            return LocationResolution(
                LocationStatus.PROVIDER_FAILED,
                candidates=tuple(candidates),
            )

        if len(eligible) == 1:
            selected = replace(eligible[0], verified=True)
            return LocationResolution(
                LocationStatus.RESOLVED,
                location=selected,
                candidates=(selected,),
            )
        if len(eligible) > 1:
            return LocationResolution(
                LocationStatus.AMBIGUOUS,
                candidates=tuple(eligible),
            )
        if candidates:
            return LocationResolution(
                LocationStatus.NEEDS_CONFIRMATION,
                candidates=tuple(candidates),
            )
        status = (
            LocationStatus.NOT_FOUND
            if provider_succeeded
            else LocationStatus.PROVIDER_FAILED
        )
        return LocationResolution(status)

    async def _load_saved_locations(self) -> list[SavedLocation]:
        if self._saved_locations is None:
            return []
        try:
            return await self._saved_locations()
        except Exception:
            return []


def _query_variants(text: str) -> tuple[str, ...]:
    if _has_implicit_city_variant(text):
        return (f"{text}市", text)
    return (text,)


def _extract_country_hint(text: str) -> tuple[str, str]:
    match = re.fullmatch(r"(.+?)(?:\s*[,，]\s*|\s+)([^,，\s]+)", text)
    if not match:
        return text, ""
    country = _COUNTRY_ALIASES.get(match.group(2).casefold(), "")
    if not country:
        return text, ""
    return match.group(1).strip(), country


def _has_implicit_city_variant(text: str) -> bool:
    return bool(_ZH_NAME.fullmatch(text) and not text.endswith(_ADMIN_SUFFIXES))


def _normalise_candidates(
    candidates: list[LocationCandidate], country_hint: str
) -> list[LocationCandidate]:
    hard_country = country_hint.strip().upper()
    unique: dict[tuple[object, ...], LocationCandidate] = {}
    for item in candidates:
        country = item.country_code.strip().upper()
        if hard_country and country != hard_country:
            continue
        key: tuple[object, ...] = (
            country,
            _admin_key(item.admin1),
            item.display_name.strip().casefold(),
            item.precision,
        )
        if item.precision == "district":
            key += (_admin_key(item.admin2),)
        elif item.precision in {"locality", "address"}:
            key += (
                _admin_key(item.admin2),
                round(item.latitude, 3),
                round(item.longitude, 3),
            )
        unique.setdefault(key, replace(item, country_code=country))
    return list(unique.values())


def _admin_key(value: str) -> str:
    key = value.strip().casefold()
    for suffix in (
        "特别行政区",
        "维吾尔自治区",
        "壮族自治区",
        "回族自治区",
        "自治区",
        "省",
        "市",
    ):
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def _eligible_candidates(
    candidates: list[LocationCandidate], purpose: LocationPurpose
) -> list[LocationCandidate]:
    if purpose in {LocationPurpose.NEARBY, LocationPurpose.FOOD}:
        accepted = {"city", "district", "address"}
    else:
        accepted = {"city", "district", "address", "locality"}
    return [item for item in candidates if item.precision in accepted]
