"""Deterministic location resolution shared by LifeKit routers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import math
import re
from typing import Awaitable, Callable, Optional
import unicodedata


class LocationPurpose(str, Enum):
    WEATHER = "weather"
    AIR_QUALITY = "air_quality"
    NEARBY = "nearby"
    FOOD = "food"
    ROUTE_ORIGIN = "route_origin"
    ROUTE_DESTINATION = "route_destination"
    SAVE = "save"


READ_ONLY_LOCATION_PURPOSES = frozenset(
    {
        LocationPurpose.WEATHER,
        LocationPurpose.AIR_QUALITY,
        LocationPurpose.NEARBY,
        LocationPurpose.FOOD,
        LocationPurpose.ROUTE_ORIGIN,
        LocationPurpose.ROUTE_DESTINATION,
    }
)


class LocationStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NOT_FOUND = "not_found"
    PROVIDER_FAILED = "provider_failed"
    NO_LOCATION = "no_location"


_CLARIFIABLE_ERROR_KEYS = frozenset(
    {
        "error.location_ambiguous",
        "error.location_confirmation_required",
        "error.city_not_found",
        "error.no_location",
    }
)


@dataclass(frozen=True)
class LocationProblem:
    error_key: str
    requested_location: str = ""
    purpose: LocationPurpose = LocationPurpose.WEATHER
    candidates: tuple["LocationCandidate", ...] = field(default_factory=tuple)
    cause: str = ""


LocationError = str | LocationProblem | None


def location_error_key(error: LocationError) -> str:
    if isinstance(error, LocationProblem):
        return error.error_key
    return error or "error.no_location"


def is_location_clarification(error: LocationError) -> bool:
    """Return whether another user turn can resolve this location outcome."""
    return location_error_key(error) in _CLARIFIABLE_ERROR_KEYS


def location_clarification_payload(
    summary: str,
    *,
    error: LocationError = None,
    field_name: str = "location",
    requested_location: str = "",
    context: Optional[dict[str, object]] = None,
    choices: Optional[list[str]] = None,
) -> dict[str, object]:
    """Build the host-recognized result for a location clarification turn."""
    problem = error if isinstance(error, LocationProblem) else None
    candidates = problem.candidates if problem is not None else ()
    candidate_payloads = [
        {
            "id": _candidate_id(item),
            "display_name": item.display_name,
            "country_code": item.country_code,
            "admin1": item.admin1,
            "admin2": item.admin2,
            "precision": item.precision,
        }
        for item in candidates
    ]
    choice_labels = [item.display_label() for item in candidates]
    duplicate_labels = {
        label for label in choice_labels if choice_labels.count(label) > 1
    }
    candidate_choices = [
        f"{label} · #{_candidate_id(item)}" if label in duplicate_labels else label
        for label, item in zip(choice_labels, candidates)
    ]
    if candidate_choices:
        summary = "\n".join((summary, *(f"- {item}" for item in candidate_choices)))
    clarification_context: dict[str, object] = {
        "kind": "location",
        "field": field_name,
        "requested_location": (
            problem.requested_location if problem is not None else requested_location
        ),
    }
    if problem is not None:
        clarification_context["purpose"] = problem.purpose.value
    if candidate_payloads:
        clarification_context["candidates"] = candidate_payloads
    if context:
        clarification_context.update(context)
    return {
        "status": "clarify",
        "summary": summary,
        "choices": choices if choices is not None else candidate_choices,
        "context": clarification_context,
    }


def _candidate_id(candidate: "LocationCandidate") -> str:
    identity = "\x1f".join(
        (
            candidate.display_name.strip().casefold(),
            candidate.country_code.strip().upper(),
            candidate.admin1.strip().casefold(),
            candidate.admin2.strip().casefold(),
            candidate.precision.strip().casefold(),
            f"{candidate.latitude:.6f}",
            f"{candidate.longitude:.6f}",
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


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

    def display_label(self) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        for raw_part in (
            self.display_name,
            self.admin1,
            self.admin2,
            self.country_code,
        ):
            part = raw_part.strip()
            key = part.casefold()
            if part and key not in seen:
                parts.append(part)
                seen.add(key)
        return " · ".join(parts)

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
class LocationAssumption:
    selected_label: str
    alternatives: tuple[str, ...] = field(default_factory=tuple)


def select_primary_candidate(
    candidates: tuple[LocationCandidate, ...],
    *,
    locale: str,
    purpose: LocationPurpose,
) -> Optional[LocationCandidate]:
    """Choose one reproducible best-effort candidate for a read-only query."""
    eligible = tuple(
        candidate
        for candidate in candidates
        if _candidate_is_eligible(candidate, purpose)
    )
    if not eligible:
        return None
    normalized_locale = locale.strip().replace("_", "-").casefold()
    preferred_country = {
        "zh-cn": "CN",
        "zh-tw": "TW",
        "ja": "JP",
        "ko": "KR",
    }.get(normalized_locale, "")

    def rank(candidate: LocationCandidate) -> tuple[object, ...]:
        return (
            -int(bool(preferred_country) and candidate.country_code == preferred_country),
            -int(candidate.verified),
            -_place_kind_rank(candidate.precision),
            -{"open_meteo": 2, "nominatim": 1}.get(candidate.source, 0),
            _normalise_place_name(candidate.display_name),
            candidate.country_code,
            _admin_key(candidate.admin1),
            _admin_key(candidate.admin2),
            round(candidate.latitude, 6),
            round(candidate.longitude, 6),
        )

    return min(eligible, key=rank)


def assumed_location_payload(
    selected: LocationCandidate,
    candidates: tuple[LocationCandidate, ...],
) -> dict[str, object]:
    """Project a best-effort location and its correction context for routers."""
    payload = selected.as_legacy_dict()
    payload.update(
        {
            "_location_assumption": LocationAssumption(
                selected_label=selected.display_label(),
                alternatives=tuple(
                    candidate.display_label()
                    for candidate in candidates
                    if candidate != selected
                ),
            ),
        }
    )
    return payload


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
    cause: str = ""


_LOCATION_STATUS_ERROR_KEYS = {
    LocationStatus.AMBIGUOUS: "error.location_ambiguous",
    LocationStatus.NEEDS_CONFIRMATION: "error.location_confirmation_required",
    LocationStatus.NOT_FOUND: "error.city_not_found",
    LocationStatus.PROVIDER_FAILED: "error.geocode_failed",
    LocationStatus.NO_LOCATION: "error.no_location",
}


def location_problem_from_resolution(
    resolution: LocationResolution,
    *,
    requested_location: str,
    purpose: LocationPurpose,
) -> LocationProblem:
    error_key = _LOCATION_STATUS_ERROR_KEYS.get(
        resolution.status,
        "error.no_location",
    )
    if resolution.status is LocationStatus.PROVIDER_FAILED and resolution.cause == "timeout":
        error_key = "error.geocode_timeout"
    return LocationProblem(
        error_key=error_key,
        requested_location=requested_location,
        purpose=purpose,
        candidates=resolution.candidates,
        cause=resolution.cause,
    )


def location_error_key_from_cause(cause: str) -> str:
    return {
        "ambiguous": "error.location_ambiguous",
        "needs_confirmation": "error.location_confirmation_required",
        "not_found": "error.city_not_found",
        "no_location": "error.no_location",
        "timeout": "error.geocode_timeout",
    }.get(cause, "error.geocode_failed")


Geocoder = Callable[..., Awaitable[list[LocationCandidate]]]
SavedLocationLoader = Callable[[], Awaitable[list["SavedLocation"]]]
GeoIPLocator = Callable[[], Awaitable[Optional[LocationCandidate]]]
DefaultTextProvider = Callable[[], str]


@dataclass(frozen=True)
class SavedLocation:
    label: str
    location: LocationCandidate
    is_default: bool = False


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
                if not _candidate_is_eligible(item.location, request.purpose):
                    return LocationResolution(
                        LocationStatus.NEEDS_CONFIRMATION,
                        candidates=(item.location,),
                    )
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
                except Exception as exc:
                    return LocationResolution(
                        LocationStatus.PROVIDER_FAILED,
                        cause=_provider_failure_cause(exc),
                    )
                if ip_location is not None:
                    if not _candidate_is_eligible(ip_location, request.purpose):
                        return LocationResolution(
                            LocationStatus.NEEDS_CONFIRMATION,
                            candidates=(ip_location,),
                        )
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
                if not _candidate_is_eligible(item.location, request.purpose):
                    return LocationResolution(
                        LocationStatus.NEEDS_CONFIRMATION,
                        candidates=(item.location,),
                    )
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
        provider_failures: list[str] = []
        try:
            candidates.extend(
                await self._open_meteo(
                    text,
                    country_code=request.country_hint.strip().upper(),
                    locale=request.locale,
                )
            )
            provider_succeeded = True
        except Exception as exc:
            provider_failures.append(_provider_failure_cause(exc))

        disambiguation_succeeded = False
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
        except Exception as exc:
            provider_failures.append(_provider_failure_cause(exc))
        candidates = _normalise_candidates(candidates, request.country_hint)

        eligible = _preferred_candidates(
            _eligible_candidates(candidates, request.purpose),
            text,
        )
        countries = {item.country_code for item in eligible if item.country_code}
        if not request.country_hint.strip() and len(countries) > 1:
            return LocationResolution(
                LocationStatus.AMBIGUOUS,
                candidates=tuple(eligible),
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
        if provider_failures and not disambiguation_succeeded:
            return LocationResolution(
                LocationStatus.PROVIDER_FAILED,
                cause=_preferred_failure_cause(provider_failures),
            )
        status = LocationStatus.NOT_FOUND if provider_succeeded else LocationStatus.PROVIDER_FAILED
        return LocationResolution(
            status,
            cause=(
                _preferred_failure_cause(provider_failures)
                if status is LocationStatus.PROVIDER_FAILED
                else ""
            ),
        )

    async def _load_saved_locations(self) -> list[SavedLocation]:
        if self._saved_locations is None:
            return []
        try:
            return await self._saved_locations()
        except Exception:
            return []


def _extract_country_hint(text: str) -> tuple[str, str]:
    match = re.fullmatch(r"(.+?)(?:\s*[,，]\s*|\s+)([^,，\s]+)", text)
    if not match:
        return text, ""
    country = _COUNTRY_ALIASES.get(match.group(2).casefold(), "")
    if not country:
        return text, ""
    return match.group(1).strip(), country


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
    return _coalesce_provider_duplicates(list(unique.values()))


def _coalesce_provider_duplicates(
    candidates: list[LocationCandidate],
) -> list[LocationCandidate]:
    coalesced: list[LocationCandidate] = []
    for candidate in candidates:
        for index, existing in enumerate(coalesced):
            if _same_administrative_place(candidate, existing):
                coalesced[index] = _merge_provider_duplicates(candidate, existing)
                break
        else:
            coalesced.append(candidate)
    return coalesced


def _same_administrative_place(
    first: LocationCandidate,
    second: LocationCandidate,
) -> bool:
    administrative_precisions = {"city", "district"}
    detailed_precisions = {"address", "locality"}
    both_administrative = (
        first.precision in administrative_precisions
        and second.precision in administrative_precisions
    )
    both_detailed = (
        first.precision in detailed_precisions
        and second.precision in detailed_precisions
        and first.precision == second.precision
    )
    if not both_administrative and not both_detailed:
        return False
    if first.country_code != second.country_code:
        return False
    if _without_admin_suffix(
        _normalise_place_name(first.display_name)
    ) != _without_admin_suffix(_normalise_place_name(second.display_name)):
        return False

    first_admin = _admin_key(first.admin1)
    second_admin = _admin_key(second.admin1)
    if first_admin and second_admin and first_admin != second_admin:
        return False
    if both_detailed:
        first_admin2 = _admin_key(first.admin2)
        second_admin2 = _admin_key(second.admin2)
        if first_admin2 and second_admin2 and first_admin2 != second_admin2:
            return False
    return _distance_km(first, second) <= 10.0


def _merge_provider_duplicates(
    first: LocationCandidate,
    second: LocationCandidate,
) -> LocationCandidate:
    preferred, fallback = max(
        ((first, second), (second, first)),
        key=lambda pair: _provider_candidate_preference(pair[0]),
    )
    return replace(
        preferred,
        admin1=preferred.admin1 or fallback.admin1,
        admin2=preferred.admin2 or fallback.admin2,
        timezone=preferred.timezone or fallback.timezone,
        verified=preferred.verified or fallback.verified,
    )


def _provider_candidate_preference(candidate: LocationCandidate) -> tuple[object, ...]:
    source_rank = {"open_meteo": 2, "nominatim": 1}.get(candidate.source, 0)
    normalised_name = _normalise_place_name(candidate.display_name)
    return (
        source_rank,
        bool(candidate.timezone),
        -len(normalised_name),
        normalised_name,
        candidate.latitude,
        candidate.longitude,
    )


def _distance_km(first: LocationCandidate, second: LocationCandidate) -> float:
    first_latitude = math.radians(first.latitude)
    second_latitude = math.radians(second.latitude)
    latitude_delta = second_latitude - first_latitude
    longitude_delta = math.radians(second.longitude - first.longitude)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(first_latitude)
        * math.cos(second_latitude)
        * math.sin(longitude_delta / 2) ** 2
    )
    haversine = min(1.0, max(0.0, haversine))
    return 6371.0 * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


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
    return [item for item in candidates if _candidate_is_eligible(item, purpose)]


def _preferred_candidates(
    candidates: list[LocationCandidate], requested_name: str
) -> list[LocationCandidate]:
    """Discard weaker search hits before deciding whether a place is ambiguous.

    Providers return substring matches alongside exact places.  Ambiguity is only
    meaningful among equally relevant hits, not between a city and every village
    whose name happens to contain the city's name.
    """
    if len(candidates) < 2:
        return candidates

    match_ranks = [
        _candidate_name_match_rank(candidate.display_name, requested_name)
        for candidate in candidates
    ]
    best_match = max(match_ranks)
    if best_match <= 0:
        return candidates
    matched = [
        candidate
        for candidate, rank in zip(candidates, match_ranks)
        if rank == best_match
    ]

    context_ranks = [
        _candidate_context_match_rank(candidate, requested_name)
        for candidate in matched
    ]
    best_context = max(context_ranks)
    if best_context > 0:
        matched = [
            candidate
            for candidate, rank in zip(matched, context_ranks)
            if rank == best_context
        ]

    precision_ranks = [_place_kind_rank(candidate.precision) for candidate in matched]
    best_precision = max(precision_ranks)
    return [
        candidate
        for candidate, rank in zip(matched, precision_ranks)
        if rank == best_precision
    ]


def _candidate_name_match_rank(candidate_name: str, requested_name: str) -> int:
    candidate = _normalise_place_name(candidate_name)
    requested = _normalise_place_name(requested_name)
    if not candidate or not requested:
        return 0
    if candidate == requested:
        return 3
    if _without_admin_suffix(candidate) == _without_admin_suffix(requested):
        return 3
    if requested in candidate or candidate in requested:
        return 1
    return 0


def _candidate_context_match_rank(
    candidate: LocationCandidate,
    requested_name: str,
) -> int:
    """Score administrative names already present in the user's text.

    This is deliberately data-driven: it uses the provider's own admin fields
    and never contains a table of special cities or streets.  For example, an
    address result whose ``admin1`` is present in ``上海南京东路`` outranks the
    otherwise identical street name returned for another province.
    """
    requested = _normalise_place_name(requested_name)
    if not requested:
        return 0
    matches: set[str] = set()
    for value in (candidate.admin1, candidate.admin2):
        normalised = _normalise_place_name(value)
        if not normalised:
            continue
        for variant in (normalised, _without_admin_suffix(normalised)):
            if variant and variant in requested:
                matches.add(variant)
                break
    return len(matches)


def _normalise_place_name(value: str) -> str:
    normalised = unicodedata.normalize("NFKC", value)
    return "".join(normalised.strip().casefold().split())


def _without_admin_suffix(value: str) -> str:
    for suffix in _ADMIN_SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix):
            return value[: -len(suffix)]
    return value


def _place_kind_rank(precision: str) -> int:
    if precision in {"city", "district"}:
        return 2
    if precision in {"address", "locality"}:
        return 1
    return 0


def _candidate_is_eligible(
    candidate: LocationCandidate,
    purpose: LocationPurpose,
) -> bool:
    if purpose in {LocationPurpose.NEARBY, LocationPurpose.FOOD}:
        accepted = {"city", "district", "address"}
    else:
        accepted = {"city", "district", "address", "locality"}
    return candidate.precision in accepted


def _provider_failure_cause(exc: Exception) -> str:
    cause = str(getattr(exc, "cause", "") or "").strip().casefold()
    if cause in {"timeout", "api_error", "network"}:
        return cause
    return "network"


def _preferred_failure_cause(causes: list[str]) -> str:
    for cause in ("timeout", "api_error", "network"):
        if cause in causes:
            return cause
    return "network"
