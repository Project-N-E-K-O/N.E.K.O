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
    RemoveLocationResult,
    SetDefaultLocationResult,
)
from .._location import is_location_clarification, location_error_key_from_cause
from .._location_entry import location_failure_result
from .._write_confirmation import WriteConfirmationGate, confirmation_scope

_STORE_KEY = "saved_locations"


class LocationsRouter(PluginRouter):
    """Manage saved locations: list, add, remove, and set default."""

    def __init__(self):
        super().__init__(name="locations")
        self._write_confirmations = WriteConfirmationGate()

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
        confirmed: bool = False,
        confirmation_token: str = "",
        _ctx: dict[str, Any] | None = None,
        **_,
    ):
        if params is not None:
            label = params.label
            city = params.city
            address = params.address
            set_default = params.set_default
            confirmed = params.confirmed
            confirmation_token = params.confirmation_token

        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n
        clean_label = clean_text(label)
        clean_city = clean_text(city)
        if not clean_label:
            return Err(SdkError(i18n.t("locations.label_required")))
        if not clean_city:
            return Err(SdkError(i18n.t("locations.city_required")))
        locale = plugin._i18n.locale

        clean_address = clean_text(address)
        confirmation_payload = {
            "label": clean_label,
            "city": clean_city,
            "address": clean_address,
            "set_default": set_default,
        }
        authorized, next_token = self._write_confirmations.authorize_or_issue(
            action="add_location",
            payload=confirmation_payload,
            confirmed=confirmed,
            token=confirmation_token,
            scope=confirmation_scope(_ctx),
        )
        if not authorized:
            return Ok({
                "status": "clarify",
                "summary": i18n.t("locations.confirm_add", label=clean_label, city=clean_city),
                "choices": [i18n.t("locations.confirm"), i18n.t("locations.cancel")],
                "confirmation_token": next_token,
                "context": {
                    **confirmation_payload,
                    "confirmed": True,
                    "confirmation_token": next_token,
                },
            })
        resolved_query = " ".join(
            part for part in (clean_city, clean_address) if part
        )
        try:
            geo = await geocode_city(resolved_query, locale=locale)
        except GeocodeError as exc:
            error_key = location_error_key_from_cause(exc.cause)
            if not is_location_clarification(error_key):
                plugin.logger.warning("Geocode failed: cause={}", exc.cause)
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
            plugin.logger.warning(
                "Geocode failed with unexpected error: type={}",
                type(exc).__name__,
            )
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
                    return Err(SdkError(i18n.t("locations.duplicate", label=clean_label)))

            country = clean_text(geo.get("country"))
            country_code = clean_text(geo.get("country_code"))
            if not country_code and len(country) == 2 and country.isalpha():
                country_code = country.upper()
            new_loc: Dict[str, Any] = {
                "id": self._new_location_id(locations),
                "label": clean_label,
                "city": geo.get("admin2") or geo["city"],
                "display_name": geo["city"],
                "address": clean_address,
                "lat": geo["lat"],
                "lon": geo["lon"],
                "country": country,
                "country_code": country_code,
                "admin1": geo.get("admin1", ""),
                "admin2": geo.get("admin2", ""),
                "precision": geo.get("_location_precision", "city"),
                "source": geo.get("_location_source", "geocoder"),
                "timezone": geo.get("timezone", ""),
                "verified": bool(geo.get("_location_verified", False)),
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
                return Err(SdkError(i18n.t("locations.save_failed")))

        summary = plugin._i18n.t(
            "panel.messages.locationAdded",
            label=new_loc["label"],
            city=new_loc["city"],
        )
        return Ok({
            "status": "ready",
            "summary": summary,
            "message": summary,
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
    async def remove_location(
        self,
        params: LocationIdParams | None = None,
        location_id: str = "",
        confirmed: bool = False,
        confirmation_token: str = "",
        _ctx: dict[str, Any] | None = None,
        **_,
    ):
        if params is not None:
            location_id = params.location_id
            confirmed = params.confirmed
            confirmation_token = params.confirmation_token

        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n
        key = clean_text(location_id)
        confirmation_payload = {"location_id": key}
        authorized, next_token = self._write_confirmations.authorize_or_issue(
            action="remove_location",
            payload=confirmation_payload,
            confirmed=confirmed,
            token=confirmation_token,
            scope=confirmation_scope(_ctx),
        )
        if not authorized:
            return Ok({
                "status": "clarify",
                "summary": i18n.t("locations.confirm_remove", label=key),
                "choices": [i18n.t("locations.confirm"), i18n.t("locations.cancel")],
                "confirmation_token": next_token,
                "context": {
                    **confirmation_payload,
                    "confirmed": True,
                    "confirmation_token": next_token,
                },
            })
        async with plugin._locations_lock:
            locations = await self._load()
            before = len(locations)
            locations = [loc for loc in locations if loc.get("id") != key and loc.get("label") != key]
            if len(locations) == before:
                return Err(SdkError(i18n.t("locations.not_found", label=key)))

            if locations and not any(loc.get("is_default") for loc in locations):
                locations[0]["is_default"] = True

            if not await self._save(locations):
                return Err(SdkError(i18n.t("locations.save_failed")))
        message = i18n.t("locations.removed", label=key)
        return Ok({
            "status": "ready",
            "summary": message,
            "message": message,
            "remaining": len(locations),
        })

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
        llm_result_model=SetDefaultLocationResult,
    )
    async def set_default_location(
        self,
        params: LocationIdParams | None = None,
        location_id: str = "",
        confirmed: bool = False,
        confirmation_token: str = "",
        _ctx: dict[str, Any] | None = None,
        **_,
    ):
        if params is not None:
            location_id = params.location_id
            confirmed = params.confirmed
            confirmation_token = params.confirmation_token

        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n
        key = clean_text(location_id)
        confirmation_payload = {"location_id": key}
        authorized, next_token = self._write_confirmations.authorize_or_issue(
            action="set_default_location",
            payload=confirmation_payload,
            confirmed=confirmed,
            token=confirmation_token,
            scope=confirmation_scope(_ctx),
        )
        if not authorized:
            return Ok({
                "status": "clarify",
                "summary": i18n.t("locations.confirm_default", label=key),
                "choices": [i18n.t("locations.confirm"), i18n.t("locations.cancel")],
                "confirmation_token": next_token,
                "context": {
                    **confirmation_payload,
                    "confirmed": True,
                    "confirmation_token": next_token,
                },
            })
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
                return Err(SdkError(i18n.t("locations.not_found", label=key)))
            if not await self._save(locations):
                return Err(SdkError(i18n.t("locations.save_failed")))
        message = i18n.t("locations.default_set", label=key)
        return Ok({"status": "ready", "summary": message, "message": message})
