"""附近搜索 router — POI 搜索 + 天气结合建议。"""

from __future__ import annotations

from typing import Any, Dict, List

from plugin.sdk.plugin import plugin_entry, quick_action, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .._poi import POIService
from .._api import RAIN_CODES
from .._routing import format_distance


class NearbyRouter(PluginRouter):
    """search_nearby entry：附近 POI 搜索。"""

    def __init__(self):
        super().__init__(name="nearby")

    @plugin_entry(
        id="search_nearby",
        name="附近搜索",
        description=(
            "搜索附近的餐厅、咖啡店、景点、超市等。"
            "支持保存的地点标签或城市名作为搜索中心。"
        ),
        llm_result_fields=["summary", "results"],
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（如：火锅、咖啡、超市、景点）",
                },
                "location": {
                    "type": "string",
                    "description": "搜索中心（地点标签或城市名，留空用默认位置）",
                    "default": "",
                },
                "radius": {
                    "type": "integer",
                    "description": "搜索半径（米，默认 3000）",
                    "default": 3000,
                },
            },
            "required": ["query"],
        },
    )
    @quick_action(icon="🔍", priority=6)
    async def search_nearby(self, query: str = "", location: str = "", radius: int = 3000, **_):
        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        if not query.strip():
            return Err(SdkError(i18n.t("nearby.no_query")))

        # 解析搜索中心
        loc, loc_err = await plugin._resolve_location(location or None)
        if not loc:
            return Err(SdkError(i18n.t(loc_err or "error.no_location")))

        radius = max(500, min(int(radius), 50000))

        # POI 搜索
        svc = POIService(plugin._cfg)
        poi_result = await svc.search(query.strip(), loc["lat"], loc["lon"], radius=radius, limit=10)

        if not poi_result.items:
            return Ok({
                "summary": i18n.t("nearby.no_results", query=query, location=loc["city"]),
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
        summary = i18n.t("nearby.summary", query=query, location=loc["city"], count=len(results), top=top3)
        if weather_tip:
            summary += f" | {weather_tip}"

        return Ok({
            "summary": summary,
            "results": results,
            "count": len(results),
            "provider": poi_result.provider,
            "weather_tip": weather_tip,
        })
