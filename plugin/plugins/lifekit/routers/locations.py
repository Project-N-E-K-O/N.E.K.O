"""Saved-location management router for LifeKit."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, tr, ui
from plugin.sdk.shared.core.router import PluginRouter

from .._api import GeocodeError, geocode_city
from .._coerce import clean_text
from .._contracts import (
    AddLocationParams,
    AddLocationResult,
    ListLocationsResult,
    LocationIdParams,
    MessageResult,
    RemoveLocationResult,
)
from .._location import is_location_clarification, location_error_key_from_cause
from .._location_entry import location_failure_result

_STORE_KEY = "saved_locations"


class LocationsRouter(PluginRouter):
    """Manage saved locations: list, add, remove, and set default."""

    def __init__(self):
        super().__init__(name="locations")

    async def _load(self) -> List[Dict[str, Any]]:
        plugin = self.main_plugin
        if not plugin.store.enabled:
            return []
        result = await plugin.store.get(_STORE_KEY, [])
        if hasattr(result, "is_ok") and callable(result.is_ok):
            if result.is_ok():
                data = result.value
            else:
                plugin.logger.warning("store.get failed: {}", result.error)
                return []
        elif hasattr(result, "value"):
            data = result.value
        else:
            data = result
        return [dict(item) for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def _save(self, locations: List[Dict[str, Any]]) -> bool:
        plugin = self.main_plugin
        if not plugin.store.enabled:
            plugin.logger.error("PluginStore is disabled, cannot save locations")
            return False
        result = await plugin.store.set(_STORE_KEY, locations)
        if hasattr(result, "is_ok") and callable(result.is_ok):
            if not result.is_ok():
                plugin.logger.error("store.set failed: {}", result.error)
                return False
        return True

    def _new_location_id(self, locations: List[Dict[str, Any]]) -> str:
        existing = {str(loc.get("id")) for loc in locations if loc.get("id")}
        for _ in range(20):
            candidate = uuid.uuid4().hex[:8]
            if candidate not in existing:
                return candidate
        raise RuntimeError("failed to generate unique location id")

    @plugin_entry(
        id="list_locations",
        name=tr("entries.listLocations.name", default="List saved locations"),
        description=tr("entries.listLocations.description", default="List all saved LifeKit locations."),
        llm_result_model=ListLocationsResult,
    )
    async def list_locations(self, **_):
        locations = await self._load()
        return Ok({"count": len(locations), "locations": locations})

    @ui.action(
        label=tr("actions.addLocation.label", default="Add location"),
        icon="+",
        tone="success",
        group="locations",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="add_location",
        name=tr("entries.addLocation.name", default="Add saved location"),
        description=tr(
            "entries.addLocation.description",
            default="Add a saved location by label and city, geocoding coordinates automatically.",
        ),
        params=AddLocationParams,
        llm_result_model=AddLocationResult,
    )
    async def add_location(
        self,
        params: AddLocationParams | None = None,
        label: str = "",
        city: str = "",
        address: str = "",
        set_default: bool = False,
        **_,
    ):
        if params is not None:
            label = params.label
            city = params.city
            address = params.address
            set_default = params.set_default

        clean_label = clean_text(label)
        clean_city = clean_text(city)
        if not clean_label:
            return Err(SdkError("Location label cannot be empty"))
        if not clean_city:
            return Err(SdkError("City cannot be empty"))

        plugin = self.main_plugin
        plugin._resolve_locale()
        locale = plugin._i18n.locale

        clean_address = clean_text(address)
        resolved_query = clean_city
        try:
            geo = await geocode_city(resolved_query, locale=locale)
        except GeocodeError as exc:
            error_key = location_error_key_from_cause(exc.cause)
            if not is_location_clarification(error_key):
                plugin.logger.warning("geocode failed for {}: {}", clean_city, exc)
            return location_failure_result(
                error_key,
                plugin._i18n,
                field_name="city",
                requested_location=clean_city,
                context={
                    "label": clean_label,
                    "address": clean_address,
                    "set_default": set_default,
                },
            )
        except Exception as exc:
            plugin.logger.warning("geocode failed for {}: {}", clean_city, exc)
            return location_failure_result(
                "error.geocode_failed",
                plugin._i18n,
                field_name="city",
                requested_location=clean_city,
                context={
                    "label": clean_label,
                    "address": clean_address,
                    "set_default": set_default,
                },
            )
        if not geo:
            return location_failure_result(
                "error.city_not_found",
                plugin._i18n,
                field_name="city",
                requested_location=clean_city,
                context={
                    "label": clean_label,
                    "address": clean_address,
                    "set_default": set_default,
                },
            )

        async with plugin._locations_lock:
            locations = await self._load()
            for loc in locations:
                if loc.get("label") == clean_label:
                    return Err(SdkError(f"Location label already exists: {clean_label}"))

            new_loc: Dict[str, Any] = {
                "id": self._new_location_id(locations),
                "label": clean_label,
                "city": geo.get("admin2") or geo["city"],
                "display_name": geo["city"],
                "address": clean_address,
                "lat": geo["lat"],
                "lon": geo["lon"],
                "country": geo.get("country", ""),
                "country_code": geo.get("country", ""),
                "admin1": geo.get("admin1", ""),
                "admin2": geo.get("admin2", ""),
                "precision": geo.get("_location_precision", "city"),
                "source": geo.get("_location_source", "geocoder"),
                "verified": bool(geo.get("_location_verified", True)),
                "schema_version": 2,
                "resolved_query": resolved_query,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
                "is_default": False,
            }

            if set_default or not locations:
                for loc in locations:
                    loc["is_default"] = False
                new_loc["is_default"] = True

            locations.append(new_loc)
            if not await self._save(locations):
                return Err(SdkError("Save failed. Please check whether plugin storage is enabled."))

        return Ok({
            "status": "ready",
            "message": f"Added location: {new_loc['label']} ({new_loc['city']})",
            "location": new_loc,
        })

    @ui.action(
        label=tr("actions.removeLocation.label", default="Remove location"),
        icon="x",
        tone="danger",
        group="locations",
        order=30,
        confirm=tr("actions.removeLocation.confirm", default="Remove this saved location?"),
        refresh_context=True,
    )
    @plugin_entry(
        id="remove_location",
        name=tr("entries.removeLocation.name", default="Remove saved location"),
        description=tr("entries.removeLocation.description", default="Remove a saved location by ID or label."),
        params=LocationIdParams,
        llm_result_model=RemoveLocationResult,
    )
    async def remove_location(self, params: LocationIdParams | None = None, location_id: str = "", **_):
        if params is not None:
            location_id = params.location_id

        plugin = self.main_plugin
        key = clean_text(location_id)
        async with plugin._locations_lock:
            locations = await self._load()
            before = len(locations)
            locations = [loc for loc in locations if loc.get("id") != key and loc.get("label") != key]
            if len(locations) == before:
                return Err(SdkError(f"Location not found: {key}"))

            if locations and not any(loc.get("is_default") for loc in locations):
                locations[0]["is_default"] = True

            if not await self._save(locations):
                return Err(SdkError("Save failed"))
        return Ok({"message": f"Removed location: {key}", "remaining": len(locations)})

    @ui.action(
        label=tr("actions.setDefaultLocation.label", default="Set default"),
        icon="*",
        tone="primary",
        group="locations",
        order=20,
        refresh_context=True,
    )
    @plugin_entry(
        id="set_default_location",
        name=tr("entries.setDefaultLocation.name", default="Set default location"),
        description=tr("entries.setDefaultLocation.description", default="Set the location preferred by weather and travel tools."),
        params=LocationIdParams,
        llm_result_model=MessageResult,
    )
    async def set_default_location(self, params: LocationIdParams | None = None, location_id: str = "", **_):
        if params is not None:
            location_id = params.location_id

        plugin = self.main_plugin
        key = clean_text(location_id)
        async with plugin._locations_lock:
            locations = await self._load()
            found = False
            for loc in locations:
                if loc.get("id") == key or loc.get("label") == key:
                    loc["is_default"] = True
                    found = True
                else:
                    loc["is_default"] = False
            if not found:
                return Err(SdkError(f"Location not found: {key}"))
            if not await self._save(locations):
                return Err(SdkError("Save failed"))
        return Ok({"message": f"Default location set: {key}"})
