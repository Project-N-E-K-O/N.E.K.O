"""美食推荐 router — 基于位置 + 天气的餐饮推荐。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from plugin.sdk.plugin import Ok, plugin_entry, quick_action, tr
from plugin.sdk.shared.core.router import PluginRouter

from .._api import RAIN_CODES
from .._chat import push_lifekit_content
from .._coerce import clamp_int, clean_text
from .._contracts import FoodRecommendParams, FoodRecommendResult
from .._location import LocationPurpose
from .._location_entry import (
    apply_location_assumption,
    location_unavailable_result,
    upstream_unavailable_result,
)
from .._poi import POIService
from .._routing import format_distance


class FoodRecommendRouter(PluginRouter):
    """food_recommend entry：基于位置和天气的美食推荐。"""

    def __init__(self):
        super().__init__(name="food_recommend")

    @plugin_entry(
        id="food_recommend",
        name=tr("entries.foodRecommend.name", default="Recommend food"),
        description=tr("entries.foodRecommend.description", default="Recommend nearby food from location, weather, cuisine, and dining occasion."),
        params=FoodRecommendParams,
        llm_result_model=FoodRecommendResult,
    )
    @quick_action(icon="🍜", priority=7)
    async def food_recommend(
        self, params: FoodRecommendParams | None = None, cuisine: str = "", scene: str = "",
        location: str = "", radius: int = 3000, **_,
    ):
        if params is not None:
            cuisine = params.cuisine
            scene = params.scene
            location = params.location
            radius = params.radius

        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n
        radius = clamp_int(radius, 3000, 500, 50000)
        clean_cuisine = clean_text(cuisine)
        clean_scene = clean_text(scene)

        loc, loc_err = await plugin._resolve_location(
            location or None,
            purpose=LocationPurpose.FOOD,
        )
        if not loc:
            return location_unavailable_result(loc_err, i18n)

        # 确定搜索关键词
        query = clean_cuisine or None
        weather_reason = ""

        if not query:
            # 根据天气 + 场景推荐
            try:
                weather_data, _ = await asyncio.wait_for(
                    plugin._get_weather_data(loc),
                    timeout=1.0,
                )
            except TimeoutError:
                weather_data = None
            query, weather_reason = self._pick_query(weather_data, clean_scene, i18n)

        # POI 搜索
        svc = POIService(plugin._cfg)
        poi_result = await svc.search(query, loc["lat"], loc["lon"], radius=radius, limit=8)

        if poi_result.error:
            plugin.logger.warning(
                "Food search failed: provider_count={}",
                len(poi_result.provider.split(",")) if poi_result.provider else 0,
            )
            return upstream_unavailable_result(
                i18n.t("error.poi_search_failed"),
                i18n,
                location=loc,
            )

        if not poi_result.items:
            return Ok(apply_location_assumption({
                "status": "ready",
                "summary": i18n.t("food.no_results", location=loc["city"], query=query),
                "recommendations": [],
                "query": query,
            }, loc, i18n))

        # 构建推荐列表
        recs: List[Dict[str, Any]] = []
        for item in poi_result.items:
            entry: Dict[str, Any] = {
                "name": item.name,
                "distance": format_distance(item.distance_m),
                "type": item.type_name,
            }
            if item.address:
                entry["address"] = item.address
            if item.rating:
                entry["rating"] = item.rating
            recs.append(entry)

        # 摘要
        top_names = "、".join(r["name"] for r in recs[:3])
        summary = i18n.t("food.summary", location=loc["city"], query=query, top=top_names)
        if weather_reason:
            summary = f"{weather_reason}，{summary}"

        # 推送卡片
        card_lines = []
        for r in recs[:5]:
            line = f"📍 {r['name']}  {r['distance']}"
            if r.get("rating"):
                line += f"  ⭐{r['rating']}"
            card_lines.append(line)

        push_lifekit_content(plugin, [
            {"type": "text", "text": f"🍜 {loc['city']} — " + i18n.t("runtime.food_title", query=query)},
            {"type": "text", "text": "\n".join(card_lines)},
        ])

        return Ok(apply_location_assumption({
            "status": "ready",
            "summary": summary,
            "recommendations": recs,
            "query": query,
            "weather_reason": weather_reason,
            "provider": poi_result.provider,
            "next_actions": [f"search_recipe query={query}", "trip_advice"],
        }, loc, i18n))

    @staticmethod
    def _pick_query(weather_data: Any, scene: str, i18n: Any) -> tuple[str, str]:
        """Keep retrieval broad; weather and occasion only explain the result."""
        query = "餐厅"

        scene_key = clean_text(scene)
        if scene_key:
            return query, i18n.t("runtime.food_scene", scene=scene_key)

        if weather_data:
            cur = weather_data.get("current", {})
            code = cur.get("weather_code", -1)
            temp = cur.get("apparent_temperature") or cur.get("temperature_2m")

            if code in RAIN_CODES:
                return query, i18n.t("runtime.food_rain")
            if isinstance(temp, (int, float)):
                if temp >= 30:
                    return query, i18n.t("runtime.food_hot", temp=temp)
                if temp <= 8:
                    return query, i18n.t("runtime.food_cold", temp=temp)

        return query, ""
