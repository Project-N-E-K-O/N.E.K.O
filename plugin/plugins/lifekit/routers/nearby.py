"""附近搜索 router — POI 搜索 + 天气结合建议。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, quick_action, tr
from plugin.sdk.shared.core.router import PluginRouter

from .._api import RAIN_CODES
from .._coerce import clamp_int, clean_text
from .._contracts import NearbyParams, NearbyResult
from .._entry_errors import unavailable_error
from .._location import (
    LocationPurpose,
    location_error_key,
)
from .._location_entry import apply_location_assumption
from .._nearby_discovery import (
    DiscoveryRequest,
    NearbyDiscovery,
    SearchCenter,
)
from .._nearby_intent import (
    build_nearby_request_plan,
)
from .._poi import UPSTREAM_TIMEOUT, UPSTREAM_UNAVAILABLE, POIService
from .._routing import format_distance

LOCATION_REQUIRED = "LOCATION_REQUIRED"
LOCATION_PROVIDER_UNAVAILABLE = "LOCATION_PROVIDER_UNAVAILABLE"


class NearbyRouter(PluginRouter):
    """search_nearby entry：附近 POI 搜索。"""

    def __init__(self):
        super().__init__(name="nearby")

    @plugin_entry(
        id="search_nearby",
        name=tr("entries.searchNearby.name", default="Search nearby"),
        description=tr("entries.searchNearby.description", default="Search near a road, landmark, current position, or city. Preserve the user's wording in request; use typed intent and explicit preference hints, and do not invent provider search terms."),
        params=NearbyParams,
        llm_result_model=NearbyResult,
    )
    @quick_action(icon="🔍", priority=6)
    async def search_nearby(
        self,
        params: NearbyParams | None = None,
        request: str = "",
        search_terms: list[str] | None = None,
        location: str = "",
        location_hint: str = "",
        place_intent: str = "",
        preference_hints: list[str] | None = None,
        radius: int = 3000,
        _ctx: dict[str, Any] | None = None,
        **_,
    ):
        projected_params = params is not None
        if params is not None:
            request = params.request
            location_hint = params.location_hint
            place_intent = params.place_intent
            preference_hints = params.preference_hints
            radius = params.radius

        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        raw_request = clean_text((_ctx or {}).get("latest_user_request"))
        request_text = raw_request or clean_text(request)
        plan = build_nearby_request_plan(
            request_text=request_text,
            raw_request=raw_request,
            projected_params=projected_params,
            location_hint=location_hint,
            legacy_location=location,
            place_intent=place_intent,
            preference_hints=preference_hints,
            search_terms=search_terms,
        )
        terms = plan.search_terms
        if not request_text or not terms:
            return Err(SdkError(i18n.t("nearby.no_query")))
        radius = clamp_int(radius, 3000, 500, 50000)

        # 解析搜索中心
        loc, loc_err = await plugin._resolve_location(
            plan.location or None,
            purpose=LocationPurpose.NEARBY,
        )
        if not loc:
            error_key = location_error_key(loc_err)
            detail = i18n.t(error_key)
            error_code = (
                LOCATION_PROVIDER_UNAVAILABLE
                if error_key in {"error.geocode_timeout", "error.geocode_failed"}
                else LOCATION_REQUIRED
            )
            plugin.logger.info(
                "Nearby search has no usable location: reason={}",
                error_key,
            )
            payload = _upstream_unavailable_payload(
                i18n=i18n,
                request=request_text,
                searched_terms=terms,
                error_code=error_code,
                summary=i18n.t("location.unavailable", detail=detail),
            )
            return unavailable_error(
                payload["summary"], code=error_code, details=payload
            )

        discovery = NearbyDiscovery(POIService(plugin._cfg))
        weather_task = asyncio.create_task(plugin._get_weather_data(loc))
        try:
            poi_results = await discovery.discover(
                DiscoveryRequest(search_terms=terms, radius=radius),
                (
                    SearchCenter(
                        latitude=float(loc["lat"]),
                        longitude=float(loc["lon"]),
                    ),
                ),
            )
        except BaseException:
            weather_task.cancel()
            await asyncio.gather(weather_task, return_exceptions=True)
            raise
        poi_result = poi_results[0]
        executed_terms = poi_result.searched_terms
        query_label = i18n.t("nearby.list_separator").join(executed_terms)

        if poi_result.error:
            weather_task.cancel()
            await asyncio.gather(weather_task, return_exceptions=True)
            plugin.logger.warning(
                "Nearby search failed: term_count={}, provider_count={}",
                len(terms),
                len(poi_result.provider.split(",")) if poi_result.provider else 0,
            )
            payload = apply_location_assumption(_upstream_unavailable_payload(
                i18n=i18n,
                request=request_text,
                searched_terms=executed_terms,
                error_code=poi_result.error_code,
            ), loc, i18n)
            return unavailable_error(
                payload["summary"],
                code=payload["error_code"],
                details=payload,
            )

        if not poi_result.items:
            weather_task.cancel()
            await asyncio.gather(weather_task, return_exceptions=True)
            plugin.logger.info(
                "Nearby search completed: term_count={}, count=0, provider={}",
                len(terms),
                poi_result.provider or "none",
            )
            return Ok(apply_location_assumption({
                "status": "ready",
                "summary": i18n.t("nearby.no_results", query=query_label, location=loc["city"]),
                "request": request_text,
                "searched_terms": list(executed_terms),
                "results": [],
                "count": 0,
            }, loc, i18n))

        # 获取天气（用于建议）
        weather_data = None
        if weather_task.done() and not weather_task.cancelled():
            weather_data, _ = weather_task.result()
        else:
            weather_task.cancel()
            await asyncio.gather(weather_task, return_exceptions=True)
        weather_tip = ""
        if weather_data:
            code = weather_data.get("current", {}).get("weather_code", -1)
            if code in RAIN_CODES:
                weather_tip = i18n.t("nearby.rain_tip")

        # 构建结果
        results: List[Dict[str, Any]] = []
        for item in poi_result.items:
            results.append(_poi_item_payload(item))

        # 摘要
        top3 = ", ".join(r["name"] for r in results[:3])
        summary = i18n.t("nearby.summary", query=query_label, location=loc["city"], count=len(results), top=top3)
        if weather_tip:
            summary += f" | {weather_tip}"

        plugin.logger.info(
            "Nearby search completed: term_count={}, count={}, provider={}",
            len(terms),
            len(results),
            poi_result.provider,
        )

        return Ok(apply_location_assumption({
            "status": "ready",
            "summary": summary,
            "request": request_text,
            "searched_terms": list(executed_terms),
            "results": results,
            "count": len(results),
            "provider": poi_result.provider,
            "weather_tip": weather_tip,
        }, loc, i18n))


def _poi_item_payload(item: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": item.name,
        "distance": format_distance(item.distance_m),
        "type": item.type_name,
    }
    if item.address:
        entry["address"] = item.address
    if item.tel:
        entry["tel"] = item.tel
    if item.rating:
        entry["rating"] = item.rating
    if item.matched_term:
        entry["matched_term"] = item.matched_term
    return entry


def _upstream_unavailable_payload(
    *,
    i18n: Any,
    request: str,
    searched_terms: tuple[str, ...],
    error_code: str,
    location_groups: list[dict[str, Any]] | None = None,
    retriable: bool = True,
    summary: str = "",
) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "summary": summary or i18n.t("nearby.provider_unavailable"),
        "assumed": False,
        "assumed_location": "",
        "ambiguity_warning": "",
        "request": request,
        "searched_terms": list(searched_terms),
        "results": [],
        "count": 0,
        "error_code": (
            error_code
            if error_code in {
                UPSTREAM_TIMEOUT,
                UPSTREAM_UNAVAILABLE,
                LOCATION_REQUIRED,
                LOCATION_PROVIDER_UNAVAILABLE,
            }
            else UPSTREAM_UNAVAILABLE
        ),
        "retriable": retriable,
        "location_groups": location_groups or [],
    }
