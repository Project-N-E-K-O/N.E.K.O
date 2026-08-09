"""附近搜索 router — POI 搜索 + 天气结合建议。"""

from __future__ import annotations

from typing import Any, Dict, List

from plugin.sdk.plugin import plugin_entry, quick_action, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .._poi import POIService
from .._api import RAIN_CODES
from .._coerce import clamp_int, clean_text
from .._contracts import NearbyParams, NearbyResult
from .._nearby_intent import (
    NearbyIntentRequest,
    NearbyIntentResolver,
    NearbyIntentStatus,
)
from .._routing import format_distance
from .._location import (
    LocationPurpose,
    is_location_clarification,
    location_clarification_payload,
    location_error_key,
)

_INTENT_RESOLVER = NearbyIntentResolver()


class NearbyRouter(PluginRouter):
    """search_nearby entry：附近 POI 搜索。"""

    def __init__(self):
        super().__init__(name="nearby")

    @plugin_entry(
        id="search_nearby",
        name="附近搜索",
        description=(
            "根据关键词或自然语言需求搜索附近的餐厅、咖啡店、景点、超市等。"
            "支持保存的地点标签或城市名作为搜索中心；类型或位置不明确时会请求一次澄清。"
        ),
        params=NearbyParams,
        llm_result_model=NearbyResult,
    )
    @quick_action(icon="🔍", priority=6)
    async def search_nearby(
        self,
        params: NearbyParams | None = None,
        query: str = "",
        location: str = "",
        radius: int = 3000,
        _ctx: dict[str, Any] | None = None,
        **_,
    ):
        if params is not None:
            query = params.query
            location = params.location
            radius = params.radius

        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        raw_request = clean_text((_ctx or {}).get("latest_user_request"))
        intent = _INTENT_RESOLVER.resolve(
            NearbyIntentRequest(
                raw_request=raw_request or clean_text(query),
                proposed_query=clean_text(query),
                proposed_location=clean_text(location),
                proposed_radius=radius,
                locale=i18n.locale,
                is_conversational=bool(raw_request),
            )
        )

        # 解析搜索中心
        loc, loc_err = await plugin._resolve_location(
            intent.location or None,
            purpose=LocationPurpose.NEARBY,
        )
        needs_category = intent.status is NearbyIntentStatus.NEEDS_CLARIFICATION
        if not loc and not is_location_clarification(loc_err):
            return Err(SdkError(i18n.t(location_error_key(loc_err))))

        needs_location = not loc
        if needs_category or needs_location:
            choices = list(intent.choices)
            choices_text = i18n.t("nearby.list_separator").join(choices)
            if needs_category and needs_location:
                clarification = i18n.t("nearby.clarify_both", choices=choices_text)
            elif needs_category:
                clarification = i18n.t("nearby.clarify_category", choices=choices_text)
            else:
                detail = i18n.t(location_error_key(loc_err))
                clarification = i18n.t("nearby.clarify_location", detail=detail)
            return Ok(
                location_clarification_payload(
                    clarification,
                    error=loc_err,
                    field_name="location",
                    requested_location=intent.location,
                    context={
                        "kind": "nearby",
                        "query": intent.query,
                        "category_id": intent.category_id,
                        "location": intent.location,
                        "radius": intent.radius,
                    },
                    choices=choices if needs_category else None,
                )
            )

        clean_query = intent.query
        if not clean_query:
            return Err(SdkError(i18n.t("nearby.no_query")))

        radius = clamp_int(intent.radius, 3000, 500, 50000)

        # POI 搜索
        svc = POIService(plugin._cfg)
        poi_result = await svc.search(clean_query, loc["lat"], loc["lon"], radius=radius, limit=10)

        if not poi_result.items:
            return Ok({
                "status": "ready",
                "summary": i18n.t("nearby.no_results", query=clean_query, location=loc["city"]),
                "results": [],
                "count": 0,
            })

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
            entry: Dict[str, Any] = {
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
            results.append(entry)

        # 摘要
        top3 = ", ".join(r["name"] for r in results[:3])
        summary = i18n.t("nearby.summary", query=clean_query, location=loc["city"], count=len(results), top=top3)
        if weather_tip:
            summary += f" | {weather_tip}"

        return Ok({
            "status": "ready",
            "summary": summary,
            "results": results,
            "count": len(results),
            "provider": poi_result.provider,
            "weather_tip": weather_tip,
        })
