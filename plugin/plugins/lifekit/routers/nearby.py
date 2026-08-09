"""附近搜索 router — POI 搜索 + 天气结合建议。"""

from __future__ import annotations

from typing import Any, Dict, List

from plugin.sdk.plugin import plugin_entry, quick_action, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .._poi import POIService, UPSTREAM_TIMEOUT, UPSTREAM_UNAVAILABLE
from .._api import RAIN_CODES
from .._coerce import clamp_int, clean_text
from .._contracts import NearbyParams, NearbyResult
from .._nearby_discovery import (
    DiscoveryRequest,
    NearbyDiscovery,
    SearchCenter,
    normalize_search_terms,
)
from .._nearby_intent import (
    normalize_place_intent,
    normalize_preference_hints,
    search_terms_for_hints,
)
from .._routing import format_distance
from .._location import (
    LocationPurpose,
    location_error_key,
)
from .._location_entry import apply_location_assumption

LOCATION_REQUIRED = "LOCATION_REQUIRED"
LOCATION_PROVIDER_UNAVAILABLE = "LOCATION_PROVIDER_UNAVAILABLE"


class NearbyRouter(PluginRouter):
    """search_nearby entry：附近 POI 搜索。"""

    def __init__(self):
        super().__init__(name="nearby")

    @plugin_entry(
        id="search_nearby",
        name="附近搜索",
        description=(
            "搜索某条路、地标、当前位置或城市附近的餐厅、咖啡、商店、景点和生活服务。"
            "request 保留用户原话；能确定时填写 location_hint，并选择最接近的 place_intent，"
            "preference_hints 只填写用户明确说出的简短偏好。不确定时使用 explore；"
            "不要生成地图召回词，也不要要求用户先选择地点类别。"
        ),
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
        intent = normalize_place_intent(place_intent)
        preferences = normalize_preference_hints(preference_hints)
        terms = _clean_search_terms(search_terms) or search_terms_for_hints(
            intent,
            preferences,
        )
        if not request_text or not terms:
            return Err(SdkError(i18n.t("nearby.no_query")))
        clean_location = clean_text(location_hint) or clean_text(location)
        radius = clamp_int(radius, 3000, 500, 50000)

        # 解析搜索中心
        loc, loc_err = await plugin._resolve_location(
            clean_location or None,
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
            return Ok(_upstream_unavailable_payload(
                i18n=i18n,
                request=request_text,
                searched_terms=terms,
                error_code=error_code,
                summary=i18n.t("location.unavailable", detail=detail),
            ))

        discovery = NearbyDiscovery(POIService(plugin._cfg))
        poi_results = await discovery.discover(
            DiscoveryRequest(search_terms=terms, radius=radius),
            (
                SearchCenter(
                    latitude=float(loc["lat"]),
                    longitude=float(loc["lon"]),
                ),
            ),
        )
        poi_result = poi_results[0]
        executed_terms = poi_result.searched_terms
        query_label = i18n.t("nearby.list_separator").join(executed_terms)

        if poi_result.error:
            plugin.logger.warning(
                "Nearby search failed: term_count={}, provider_count={}",
                len(terms),
                len(poi_result.provider.split(",")) if poi_result.provider else 0,
            )
            return Ok(apply_location_assumption(_upstream_unavailable_payload(
                i18n=i18n,
                request=request_text,
                searched_terms=executed_terms,
                error_code=poi_result.error_code,
            ), loc, i18n))

        if not poi_result.items:
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
        weather_data, _ = await plugin._get_weather_data(loc)
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


def _clean_search_terms(values: list[str] | None) -> tuple[str, ...]:
    return normalize_search_terms(values)


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
