"""出行规划 router — 路线 + 天气综合建议。"""

from __future__ import annotations

from typing import Any, Dict, List

from plugin.sdk.plugin import plugin_entry, quick_action, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .._routing import RoutingService, format_duration, format_distance, haversine_km, suggest_modes
from .._api import RAIN_CODES


class TripRouter(PluginRouter):
    """trip_advice entry：路线规划 + 天气综合出行建议。"""

    def __init__(self):
        super().__init__(name="trip")

    @plugin_entry(
        id="trip_advice",
        name="出行规划",
        description=(
            "规划从起点到终点的出行方案，结合天气给出综合建议。"
            "支持保存的地点标签（如'家'、'公司'）或城市名。"
            "自动推荐合适的出行方式（步行/骑行/公交/驾车）。"
        ),
        llm_result_fields=["summary", "routes"],
        input_schema={
            "type": "object",
            "properties": {
                "origin": {
                    "type": "string",
                    "description": "起点（地点标签或城市名，留空用默认地点）",
                    "default": "",
                },
                "destination": {
                    "type": "string",
                    "description": "终点（地点标签或城市名）",
                },
                "mode": {
                    "type": "string",
                    "description": "出行方式: transit/walking/bicycling/driving，留空自动推荐",
                    "default": "",
                },
            },
            "required": ["destination"],
        },
    )
    @quick_action(icon="🗺️", priority=7)
    async def trip_advice(self, destination: str = "", origin: str = "", mode: str = "", **_):
        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        if not destination.strip():
            return Err(SdkError(i18n.t("trip.no_destination")))

        # 解析起点
        origin_loc, origin_err = await plugin._resolve_location(origin or None)
        if not origin_loc:
            return Err(SdkError(i18n.t(origin_err or "error.no_location") + " (origin)"))

        # 解析终点
        dest_loc, dest_err = await plugin._resolve_location(destination)
        if not dest_loc:
            return Err(SdkError(i18n.t(dest_err or "error.no_location") + " (destination)"))

        # 直线距离
        dist_km = haversine_km(origin_loc["lat"], origin_loc["lon"], dest_loc["lat"], dest_loc["lon"])

        # 路线规划
        svc = RoutingService(plugin._cfg)
        modes = [mode] if mode else None
        routing = await svc.plan(
            origin_loc["lat"], origin_loc["lon"],
            dest_loc["lat"], dest_loc["lon"],
            modes=modes,
        )

        # 两地天气
        origin_weather, _ = await plugin._get_weather_data(origin_loc)
        dest_weather, _ = await plugin._get_weather_data(dest_loc)

        # 构建路线摘要
        route_summaries: List[Dict[str, Any]] = []
        for route in routing.routes:
            entry: Dict[str, Any] = {
                "mode": route.mode,
                "distance": format_distance(route.distance_m),
                "duration": format_duration(route.duration_s),
                "summary": route.summary or _mode_label(route.mode),
            }
            if route.cost:
                entry["cost"] = route.cost
            if route.steps:
                entry["steps"] = [
                    {"instruction": s.instruction, "mode": s.mode, "duration": format_duration(s.duration_s)}
                    for s in route.steps[:8]
                ]
            route_summaries.append(entry)

        # 天气综合建议
        weather_tips = _build_weather_tips(origin_weather, dest_weather, origin_loc, dest_loc, i18n, plugin)

        # 出行方式建议
        mode_advice = _build_mode_advice(dist_km, origin_weather, dest_weather)

        # 总结
        summary_parts = [
            f"{origin_loc['city']} → {dest_loc['city']}",
            f"{i18n.t('trip.distance')}: {dist_km:.1f}km",
        ]
        if routing.routes:
            best = routing.routes[0]
            summary_parts.append(f"{i18n.t('trip.recommended')}: {_mode_label(best.mode)} {format_duration(best.duration_s)}")
        if mode_advice:
            summary_parts.append(mode_advice)
        summary_parts.extend(weather_tips)

        # 推送出行规划卡片到聊天框
        card_lines = [f"📍 {origin_loc['city']} → {dest_loc['city']}  ({dist_km:.1f}km)"]
        for r in route_summaries[:3]:
            card_lines.append(f"{_mode_label(r['mode'])}  {r['distance']}  ⏱{r['duration']}")
        if weather_tips:
            card_lines.append(" ".join(weather_tips))
        if mode_advice:
            card_lines.append(mode_advice)
        plugin.push_chat_content([
            {"type": "text", "text": f"🗺️ {origin_loc['city']} → {dest_loc['city']}"},
            {"type": "text", "text": "\n".join(card_lines)},
        ])

        return Ok({
            "origin": origin_loc["city"],
            "destination": dest_loc["city"],
            "distance_km": round(dist_km, 1),
            "summary": " | ".join(summary_parts),
            "routes": route_summaries,
            "weather_tips": weather_tips,
            "mode_advice": mode_advice,
            "provider": routing.provider,
        })


def _mode_label(mode: str) -> str:
    return {"transit": "🚇 公交/地铁", "walking": "🚶 步行", "bicycling": "🚲 骑行", "driving": "🚗 驾车"}.get(mode, mode)


def _build_weather_tips(
    origin_data: Any, dest_data: Any,
    origin_loc: Dict, dest_loc: Dict,
    i18n: Any, plugin: Any,
) -> List[str]:
    tips: List[str] = []
    if not origin_data or not dest_data:
        return tips

    o_cur = origin_data.get("current", {})
    d_cur = dest_data.get("current", {})
    o_code = o_cur.get("weather_code", -1)
    d_code = d_cur.get("weather_code", -1)
    o_temp = o_cur.get("apparent_temperature")
    d_temp = d_cur.get("apparent_temperature")

    # 任一地有雨 → 带伞
    if o_code in RAIN_CODES or d_code in RAIN_CODES:
        tips.append("🌂 " + i18n.t("advice.rain"))

    # 温差大 → 提醒
    if o_temp is not None and d_temp is not None:
        diff = abs(o_temp - d_temp)
        if diff >= 5:
            tips.append(f"🌡️ {origin_loc['city']} {o_temp}°C → {dest_loc['city']} {d_temp}°C")

    return tips


def _build_mode_advice(dist_km: float, origin_data: Any, dest_data: Any) -> str:
    """根据距离和天气给出出行方式建议。"""
    has_rain = False
    if origin_data:
        code = origin_data.get("current", {}).get("weather_code", -1)
        if code in RAIN_CODES:
            has_rain = True
    if dest_data:
        code = dest_data.get("current", {}).get("weather_code", -1)
        if code in RAIN_CODES:
            has_rain = True

    if dist_km <= 1:
        return "🚶 距离很近，建议步行" if not has_rain else "🚇 有雨，建议公交/地铁"
    if dist_km <= 3 and not has_rain:
        return "🚲 距离适中，天气好适合骑行"
    if dist_km <= 5:
        return "🚇 建议公交/地铁" if not has_rain else "🚇 有雨，建议公交/地铁"
    return ""
