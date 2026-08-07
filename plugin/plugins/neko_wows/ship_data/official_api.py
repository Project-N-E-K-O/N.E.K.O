"""Explicit, fixed-host client for the official Wargaming WoWS API."""

from __future__ import annotations

import copy
import json
import math
import socket
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .module_selection import (
    ModuleGraphError,
    ModuleNode,
    select_terminal_modules,
)

SOURCE_NAME = "official_wargaming_api"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_CACHE_SIZE = 128

REGION_ORIGINS = {
    "na": "https://api.worldofwarships.com",
    "eu": "https://api.worldofwarships.eu",
    "asia": "https://api.worldofwarships.asia",
}

OFFICIAL_LANGUAGES = frozenset({
    "cs", "de", "en", "es", "fr", "ja", "ko", "pl", "pt-br", "ru",
    "th", "tr", "vi", "zh-cn", "zh-tw",
})

_MODULE_PARAMETERS = {
    "artillery": "artillery_id",
    "dive_bomber": "dive_bomber_id",
    "engine": "engine_id",
    "fighter": "fighter_id",
    "fire_control": "fire_control_id",
    "flight_control": "flight_control_id",
    "hull": "hull_id",
    "torpedo_bomber": "torpedo_bomber_id",
    "torpedoes": "torpedoes_id",
}


class _ResponseTooLarge(ValueError):
    pass


class _InvalidOfficialResponse(ValueError):
    pass


def official_error(code: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "data": None, "source": SOURCE_NAME}


def _number(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise _InvalidOfficialResponse("boolean is not a combat number")
    if not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise _InvalidOfficialResponse("invalid combat number") from exc
    if not math.isfinite(result):
        raise _InvalidOfficialResponse("non-finite combat number")
    rounded = round(result, 6)
    return int(rounded) if rounded.is_integer() else rounded


def _positive(value: Any) -> int | float | None:
    result = _number(value)
    return result if result is not None and result >= 0 else None


def _scaled(value: Any, factor: float) -> int | float | None:
    result = _positive(value)
    return _number(float(result) * factor) if result is not None else None


def _ratio(value: Any) -> int | float | None:
    result = _positive(value)
    if result is None:
        return None
    number = float(result)
    if number > 1:
        number /= 100.0
    if number > 1:
        return None
    return _number(number)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _text(value: Any, *, maximum: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:maximum]


def _without_none(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in values.items()
        if value is not None and value not in ({}, (), [])
    }


class OfficialWowsApiClient:
    """Short-lived official lookups with an in-memory success-only LRU."""

    def __init__(
        self,
        *,
        application_id: str,
        region: str = "asia",
        timeout_seconds: float = 5.0,
        cache_ttl_seconds: float = 300.0,
        cache_size: int = DEFAULT_CACHE_SIZE,
        transport=None,
        clock=time.monotonic,
    ) -> None:
        self.application_id = str(application_id or "").strip()
        self.region = str(region or "").strip().casefold()
        self.timeout_seconds = max(0.1, min(30.0, float(timeout_seconds)))
        self.cache_ttl_seconds = max(0.0, float(cache_ttl_seconds))
        self.cache_size = max(1, min(1024, int(cache_size)))
        self._transport = transport or self._default_transport
        self._clock = clock
        self._lock = threading.RLock()
        self._cache: OrderedDict[
            tuple[str, str, int, str], tuple[float, dict[str, Any]]
        ] = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0

    def apply_config(self, cfg: Any) -> None:
        with self._lock:
            before = (
                self.application_id,
                self.region,
                self.timeout_seconds,
                self.cache_ttl_seconds,
            )
            self.application_id = str(
                getattr(cfg, "official_api_application_id", "") or "").strip()
            self.region = str(
                getattr(cfg, "official_api_region", "asia") or "").casefold()
            self.timeout_seconds = max(0.1, min(
                30.0, float(getattr(cfg, "official_api_timeout_seconds", 5.0))))
            self.cache_ttl_seconds = max(
                0.0, float(getattr(cfg, "official_api_cache_ttl_seconds", 300.0)))
            after = (
                self.application_id,
                self.region,
                self.timeout_seconds,
                self.cache_ttl_seconds,
            )
            if after != before:
                self._cache.clear()

    def query_ship(
        self,
        ship: str,
        *,
        configuration: str = "top",
        language: str = "en",
    ) -> dict[str, Any]:
        """Resolve one exact official display name, never a fuzzy match."""
        if isinstance(ship, str) and ship.strip().isdigit():
            return self.query_ship_id(
                int(ship.strip()), configuration=configuration, language=language)
        error = self._validate(configuration, language)
        if error:
            return official_error(error)
        wanted = _text(ship).casefold()
        if not wanted:
            return official_error("ship_not_found")
        payload, code = self._request(
            "/wows/encyclopedia/ships/",
            {"language": language.casefold(), "search": _text(ship)},
        )
        if code:
            return official_error(code)
        matches = []
        for raw in _mapping(payload.get("data")).values():
            value = _mapping(raw)
            if _text(value.get("name")).casefold() == wanted:
                ship_id = value.get("ship_id")
                if isinstance(ship_id, int) and not isinstance(ship_id, bool):
                    matches.append(ship_id)
        if len(set(matches)) != 1:
            return official_error("ship_not_found")
        return self.query_ship_id(
            matches[0], configuration=configuration, language=language)

    def query_ship_id(
        self,
        ship_id: int,
        *,
        configuration: str = "top",
        language: str = "en",
    ) -> dict[str, Any]:
        error = self._validate(configuration, language)
        if error:
            return official_error(error)
        if (
            isinstance(ship_id, bool)
            or not isinstance(ship_id, int)
            or ship_id <= 0
        ):
            return official_error("ship_not_found")

        language = language.casefold()
        cache_key = (self.region, language, ship_id, configuration)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        ship_payload, code = self._request(
            "/wows/encyclopedia/ships/",
            {"language": language, "ship_id": ship_id},
        )
        if code:
            return official_error(code)
        ship_data = self._data_item(ship_payload, ship_id)
        if ship_data is None:
            return official_error("ship_not_found")
        modules = self._select_terminal_modules(ship_data)
        if modules is None:
            return official_error("invalid_response")

        profile_params: dict[str, Any] = {
            "language": language,
            "ship_id": ship_id,
            **modules,
        }
        profile_payload, code = self._request(
            "/wows/encyclopedia/shipprofile/", profile_params)
        if code:
            return official_error(code)
        profile_data = self._data_item(profile_payload, ship_id)
        if profile_data is None:
            return official_error("invalid_response")

        try:
            normalized = {
                "ship": self._normalize_ship(ship_data, ship_id),
                "configuration": "top",
                "modules": modules,
                "profile": self._normalize_profile(profile_data),
                "region": self.region,
                "language": language,
                "queried_at_utc": datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"),
            }
        except _InvalidOfficialResponse:
            return official_error("invalid_response")
        result = {
            "ok": True,
            "code": "ok",
            "data": normalized,
            "source": SOURCE_NAME,
        }
        self._cache_put(cache_key, result)
        return copy.deepcopy(result)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._purge_expired(float(self._clock()))
            return {
                "region": self.region,
                "cache_entries": len(self._cache),
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
            }

    def _validate(self, configuration: str, language: str) -> str:
        if self.region not in REGION_ORIGINS:
            return "invalid_region"
        if configuration != "top":
            return "invalid_configuration"
        if not isinstance(language, str) or language.casefold() not in OFFICIAL_LANGUAGES:
            return "invalid_language"
        if not self.application_id:
            return "missing_application_id"
        return ""

    def _request(
        self,
        path: str,
        params: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], str]:
        origin = REGION_ORIGINS.get(self.region)
        if origin is None:
            return {}, "invalid_region"
        query = urlencode({
            "application_id": self.application_id,
            **params,
        })
        url = f"{origin}{path}?{query}"
        try:
            status, body = self._transport(
                url, self.timeout_seconds, MAX_RESPONSE_BYTES)
            if not isinstance(body, (bytes, bytearray)):
                return {}, "invalid_response"
            if len(body) > MAX_RESPONSE_BYTES:
                return {}, "invalid_response"
        except (TimeoutError, socket.timeout):
            return {}, "timeout"
        except _ResponseTooLarge:
            return {}, "invalid_response"
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                return {}, "timeout"
            return {}, "network_error"
        except OSError:
            return {}, "network_error"
        except Exception:
            return {}, "network_error"

        status_code = int(status) if isinstance(status, int) else 0
        if status_code in (401, 403):
            return {}, "unauthorized"
        if status_code == 429:
            return {}, "rate_limited"
        if status_code < 200 or status_code >= 300:
            return {}, "upstream_error"
        try:
            payload = json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return {}, "invalid_response"
        if not isinstance(payload, Mapping):
            return {}, "invalid_response"
        if payload.get("status") == "error":
            return {}, self._api_error_code(_mapping(payload.get("error")))
        if payload.get("status") != "ok" or not isinstance(payload.get("data"), Mapping):
            return {}, "invalid_response"
        return payload, ""

    @staticmethod
    def _api_error_code(error: Mapping[str, Any]) -> str:
        code = str(error.get("code", "")).casefold()
        message = str(error.get("message", "")).casefold()
        combined = f"{code} {message}"
        if (
            code in {"401", "402", "407", "408"}
            or "application" in combined
            or "auth" in combined
        ):
            return "unauthorized"
        if "limit" in combined or "rate" in combined or code == "429":
            return "rate_limited"
        return "upstream_error"

    @staticmethod
    def _data_item(
        payload: Mapping[str, Any], ship_id: int
    ) -> Mapping[str, Any] | None:
        data = _mapping(payload.get("data"))
        item = data.get(str(ship_id), data.get(ship_id))
        return item if isinstance(item, Mapping) else None

    @staticmethod
    def _select_terminal_modules(
        ship: Mapping[str, Any],
    ) -> dict[str, int] | None:
        raw_modules_value = ship.get("modules")
        tree_value = ship.get("modules_tree")
        if not isinstance(raw_modules_value, Mapping) or not raw_modules_value:
            return None
        if not isinstance(tree_value, Mapping) or not tree_value:
            return None
        raw_modules = raw_modules_value
        tree = tree_value
        selected: dict[str, int] = {}
        try:
            for slot, parameter in _MODULE_PARAMETERS.items():
                if slot not in raw_modules:
                    continue
                raw_slot = raw_modules.get(slot)
                raw_values = _sequence(raw_slot)
                if not raw_values:
                    if raw_slot not in ((), []):
                        return None
                    if slot == "hull":
                        return None
                    continue
                if any(
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value <= 0
                    for value in raw_values
                ):
                    return None
                values = tuple(enumerate(raw_values))
                candidates = []
                for position, module_id in values:
                    stable_key = str(module_id)
                    if stable_key in tree:
                        raw_node = tree[stable_key]
                    elif module_id in tree:
                        raw_node = tree[module_id]
                    else:
                        return None
                    if not isinstance(raw_node, Mapping):
                        return None
                    node = _mapping(raw_node)
                    candidates.append(ModuleNode(
                        stable_key=stable_key,
                        payload=module_id,
                        index=OfficialWowsApiClient._module_tree_index(
                            node, position),
                        xp=node.get("price_xp", 0),
                        credits=node.get("price_credit", 0),
                        explicit_top=any(
                            node.get(field) is True
                            for field in ("top", "isTop", "is_top")
                        ),
                        next_refs=OfficialWowsApiClient._module_tree_next_refs(
                            node),
                        has_explicit_graph=True,
                    ))
                terminals = select_terminal_modules(candidates)
                if not terminals:
                    return None
                selected[parameter] = terminals[0].payload
        except ModuleGraphError:
            return None
        if "hull_id" not in selected:
            return None
        return dict(sorted(selected.items()))

    @staticmethod
    def _module_tree_index(node: Mapping[str, Any], fallback: int) -> int:
        for field in ("index", "module_index"):
            if field not in node:
                continue
            value = node.get(field)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ModuleGraphError(f"invalid {field} field")
            return value
        return fallback

    @staticmethod
    def _module_tree_next_refs(node: Mapping[str, Any]) -> tuple[str, ...]:
        raw = node.get("next_modules")
        if raw is None:
            return ()
        values = _sequence(raw)
        if not values and raw not in ((), []):
            raise ModuleGraphError("invalid next_modules field")
        references = []
        for value in values:
            if isinstance(value, bool):
                raise ModuleGraphError("invalid next_modules reference")
            if isinstance(value, int) and value > 0:
                references.append(str(value))
                continue
            if isinstance(value, str) and value.strip():
                references.append(value.strip())
                continue
            raise ModuleGraphError("invalid next_modules reference")
        return tuple(references)

    @staticmethod
    def _normalize_ship(raw: Mapping[str, Any], ship_id: int) -> dict[str, Any]:
        return _without_none({
            "ship_id": ship_id,
            "name": _text(raw.get("name")) or None,
            "tier": _positive(raw.get("tier")),
            "ship_class": _text(raw.get("type")) or None,
            "nation": _text(raw.get("nation")) or None,
            "is_premium": raw.get("is_premium")
            if isinstance(raw.get("is_premium"), bool) else None,
        })

    @classmethod
    def _normalize_profile(cls, raw: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        hull = _mapping(raw.get("hull"))
        survivability = _without_none({
            "hit_points": _positive(hull.get("health")),
            "torpedo_protection_ratio": _ratio(
                hull.get("torpedoes_protection", hull.get("torpedo_protection"))),
        })
        if survivability:
            result["survivability"] = survivability

        mobility_raw = _mapping(raw.get("mobility"))
        mobility = _without_none({
            "max_speed_knots": _positive(mobility_raw.get("max_speed")),
            "turning_radius_m": _positive(mobility_raw.get("turning_radius")),
            "rudder_shift_s": _positive(mobility_raw.get("rudder_time")),
        })
        if mobility:
            result["mobility"] = mobility

        concealment_raw = _mapping(raw.get("concealment"))
        concealment = _without_none({
            "surface_detect_m": _scaled(
                concealment_raw.get("detect_distance_by_ship"), 1000.0),
            "air_detect_m": _scaled(
                concealment_raw.get("detect_distance_by_plane"), 1000.0),
        })
        if concealment:
            result["concealment"] = concealment

        artillery_raw = _mapping(raw.get("artillery"))
        artillery = cls._normalize_artillery(artillery_raw)
        if artillery:
            result["main_battery"] = artillery

        torpedoes_raw = _mapping(raw.get("torpedoes"))
        torpedoes = _without_none({
            "range_m": _scaled(torpedoes_raw.get("distance"), 1000.0),
            "reload_s": _positive(torpedoes_raw.get("reload_time")),
            "speed_knots": _positive(torpedoes_raw.get("torpedo_speed")),
            "max_damage": _positive(torpedoes_raw.get("max_damage")),
            "detectability_m": _scaled(
                torpedoes_raw.get("visibility_dist"), 1000.0),
        })
        if torpedoes:
            result["torpedoes"] = torpedoes

        aa_raw = _mapping(raw.get("anti_aircraft"))
        aa_slots = []
        for slot in _mapping(aa_raw.get("slots")).values():
            value = _mapping(slot)
            normalized = _without_none({
                "caliber_mm": _positive(value.get("caliber")),
                "continuous_dps": _positive(
                    value.get("avg_damage", value.get("damage"))),
                "guns": _positive(value.get("guns")),
            })
            if normalized:
                aa_slots.append(normalized)
        if aa_slots:
            result["anti_air"] = {"auras": tuple(aa_slots)}
        return result

    @staticmethod
    def _normalize_artillery(raw: Mapping[str, Any]) -> dict[str, Any]:
        reload_s = _positive(raw.get("shot_delay"))
        if reload_s is None:
            gun_rate = _positive(raw.get("gun_rate"))
            if gun_rate:
                reload_s = _number(60.0 / float(gun_rate))
        projectiles = []
        for shell in _mapping(raw.get("shells")).values():
            value = _mapping(shell)
            normalized = _without_none({
                "ammo_type": _text(value.get("type"), maximum=24) or None,
                "max_damage": _positive(value.get("damage")),
                "fire_chance_ratio": _ratio(value.get("burn_probability")),
                "initial_velocity_mps": _positive(value.get("bullet_speed")),
            })
            if normalized:
                projectiles.append(normalized)
        return _without_none({
            "range_m": _scaled(raw.get("distance"), 1000.0),
            "reload_s": reload_s,
            "rotation_180_s": _positive(raw.get("rotation_time")),
            "dispersion_m": _positive(raw.get("max_dispersion")),
            "projectiles": tuple(projectiles),
        })

    def _cache_get(
        self,
        key: tuple[str, str, int, str],
    ) -> dict[str, Any] | None:
        now = float(self._clock())
        with self._lock:
            self._purge_expired(now)
            item = self._cache.get(key)
            if item is None:
                self._cache_misses += 1
                return None
            expires_at, value = item
            if expires_at <= now:
                self._cache.pop(key, None)
                self._cache_misses += 1
                return None
            self._cache.move_to_end(key)
            self._cache_hits += 1
            return copy.deepcopy(value)

    def _cache_put(
        self,
        key: tuple[str, str, int, str],
        value: dict[str, Any],
    ) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        expires_at = float(self._clock()) + self.cache_ttl_seconds
        with self._lock:
            self._cache[key] = (expires_at, copy.deepcopy(value))
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, item in self._cache.items() if item[0] <= now]
        for key in expired:
            self._cache.pop(key, None)

    @staticmethod
    def _default_transport(
        url: str,
        timeout: float,
        max_bytes: int,
    ) -> tuple[int, bytes]:
        request = Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "N.E.K.O-neko_wows/1",
        })
        try:
            response = urlopen(request, timeout=timeout)
        except HTTPError as exc:
            body = exc.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise _ResponseTooLarge from None
            return int(exc.code), body
        with response:
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise _ResponseTooLarge
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise _ResponseTooLarge
            return int(response.getcode()), body


__all__ = [
    "MAX_RESPONSE_BYTES",
    "OFFICIAL_LANGUAGES",
    "OfficialWowsApiClient",
    "REGION_ORIGINS",
    "SOURCE_NAME",
    "official_error",
]
