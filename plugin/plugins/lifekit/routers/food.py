"""美食推荐 router — 基于位置 + 天气的餐饮推荐。"""

from __future__ import annotations

from typing import Any, Dict, List

from plugin.sdk.plugin import plugin_entry, quick_action, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .._poi import POIService
from .._api import RAIN_CODES
from .._chat import push_lifekit_content
from .._routing import format_distance

# 天气 → 推荐关键词映射
_WEATHER_FOOD: Dict[str, List[str]] = {
    "hot":  ["冷饮", "冰淇淋", "沙拉", "刨冰", "凉面"],
    "cold": ["火锅", "炖菜", "麻辣烫", "羊肉汤", "热干面"],
    "rain": ["火锅", "烧烤", "炖汤", "麻辣烫"],
    "mild": ["咖啡", "甜品", "面包", "brunch"],
}

# 场景 → 搜索关键词
_SCENE_KEYWORDS: Dict[str, List[str]] = {
    "聚餐":   ["火锅", "烧烤", "自助餐", "中餐厅"],
    "约会":   ["西餐", "日料", "咖啡厅", "法餐"],
    "一人食": ["面馆", "快餐", "便当", "拉面"],
    "家庭":   ["中餐厅", "粤菜", "自助餐", "火锅"],
    "宵夜":   ["烧烤", "小龙虾", "大排档", "串串"],
}


class FoodRecommendRouter(PluginRouter):
    """food_recommend entry：基于位置和天气的美食推荐。"""

    def __init__(self):
        super().__init__(name="food_recommend")

    @plugin_entry(
        id="food_recommend",
        name="美食推荐",
        description=(
            "根据当前位置、天气和场景推荐附近美食。"
            "支持指定口味偏好、用餐场景和预算。"
            "如果用户想自己做，可用 search_recipe 查菜谱。"
        ),
        llm_result_fields=["summary", "recommendations", "next_actions"],
        input_schema={
            "type": "object",
            "properties": {
                "cuisine": {
                    "type": "string",
                    "description": "口味/菜系偏好（如：火锅、日料、川菜、意大利菜），留空则根据天气推荐",
                    "default": "",
                },
                "scene": {
                    "type": "string",
                    "description": "用餐场景：聚餐/约会/一人食/家庭/宵夜，留空不限",
                    "default": "",
                },
                "location": {
                    "type": "string",
                    "description": "位置（地点标签或城市名，留空用默认位置）",
                    "default": "",
                },
                "radius": {
                    "type": "integer",
                    "description": "搜索半径（米，默认 3000）",
                    "default": 3000,
                },
            },
        },
    )
    @quick_action(icon="🍜", priority=7)
    async def food_recommend(
        self, cuisine: str = "", scene: str = "",
        location: str = "", radius: int = 3000, **_,
    ):
        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        loc, loc_err = await plugin._resolve_location(location or None)
        if not loc:
            return Err(SdkError(i18n.t(loc_err or "error.no_location")))

        radius = max(500, min(int(radius), 50000))

        # 确定搜索关键词
        query = cuisine.strip() if cuisine.strip() else None
        weather_reason = ""

        if not query:
            # 根据天气 + 场景推荐
            weather_data, _ = await plugin._get_weather_data(loc)
            query, weather_reason = self._pick_query(weather_data, scene)

        # POI 搜索
        svc = POIService(plugin._cfg)
        poi_result = await svc.search(query, loc["lat"], loc["lon"], radius=radius, limit=8)

        if not poi_result.items:
            return Ok({
                "summary": f"在 {loc['city']} 附近没有找到「{query}」相关的餐厅",
                "recommendations": [],
                "query": query,
            })

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
        summary = f"{loc['city']}附近推荐「{query}」: {top_names}"
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
            {"type": "text", "text": f"🍜 {loc['city']} — {query}推荐"},
            {"type": "text", "text": "\n".join(card_lines)},
        ])

        return Ok({
            "summary": summary,
            "recommendations": recs,
            "query": query,
            "weather_reason": weather_reason,
            "provider": poi_result.provider,
            "next_actions": [f"search_recipe query={query} — 自己做{query}", "trip_advice — 规划去餐厅的路线"],
        })

    @staticmethod
    def _pick_query(weather_data: Any, scene: str) -> tuple[str, str]:
        """根据天气和场景选择搜索关键词。返回 (query, reason)。"""
        import random

        # 场景优先
        scene_key = scene.strip()
        if scene_key and scene_key in _SCENE_KEYWORDS:
            kw = random.choice(_SCENE_KEYWORDS[scene_key])
            return kw, f"🎯 {scene_key}场景"

        # 天气推荐
        if weather_data:
            cur = weather_data.get("current", {})
            code = cur.get("weather_code", -1)
            temp = cur.get("apparent_temperature") or cur.get("temperature_2m")

            if code in RAIN_CODES:
                kw = random.choice(_WEATHER_FOOD["rain"])
                return kw, "🌧️ 下雨天适合吃点暖的"
            if isinstance(temp, (int, float)):
                if temp >= 30:
                    kw = random.choice(_WEATHER_FOOD["hot"])
                    return kw, f"🌡️ {temp}°C 太热了，来点凉的"
                if temp <= 8:
                    kw = random.choice(_WEATHER_FOOD["cold"])
                    return kw, f"🌡️ {temp}°C 挺冷的，吃点热乎的"

        # 默认
        kw = random.choice(_WEATHER_FOOD["mild"])
        return kw, ""
