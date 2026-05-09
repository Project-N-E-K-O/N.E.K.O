"""出行规划 router — 路线 + 天气综合建议。"""

from __future__ import annotations

from typing import Any, Dict, List

from plugin.sdk.plugin import plugin_entry, quick_action, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .._routing import RoutingService, format_duration, format_distance, haversine_km, suggest_modes
from .._api import RAIN_CODES
from .._chat import push_lifekit_content


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
            "规划完成后可用 food_recommend 查看目的地美食。"
        ),
        llm_result_fields=["summary", "routes", "next_actions"],
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
        normalized_mode = mode.strip().lower() if mode else ""
        modes = [normalized_mode] if normalized_mode else None
        routing = await svc.plan(
            origin_loc["lat"], origin_loc["lon"],
            dest_loc["lat"], dest_loc["lon"],
            modes=modes,
            origin_city=str(origin_loc.get("city") or ""),
            destination_city=str(dest_loc.get("city") or ""),
        )

        # 两地天气
        origin_weather, _ = await plugin._get_weather_data(origin_loc)
        dest_weather, _ = await plugin._get_weather_data(dest_loc)
        origin_city = _city_name(origin_loc, "origin")
        dest_city = _city_name(dest_loc, "destination")

        # 构建路线摘要
        route_summaries: List[Dict[str, Any]] = []
        for route in routing.routes:
            entry: Dict[str, Any] = {
                "mode": route.mode,
                "distance": format_distance(route.distance_m),
                "duration": format_duration(route.duration_s),
                "summary": route.summary or _mode_label(route.mode, i18n),
            }
            if route.cost is not None and route.cost != "":
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
        mode_advice = _build_mode_advice(dist_km, origin_weather, dest_weather, i18n)

        # 总结
        summary_parts = [
            f"{origin_city} → {dest_city}",
            f"{i18n.t('trip.distance')}: {dist_km:.1f}km",
        ]
        if routing.routes:
            best = routing.routes[0]
            summary_parts.append(f"{i18n.t('trip.recommended')}: {_mode_label(best.mode, i18n)} {format_duration(best.duration_s)}")
        if mode_advice:
            summary_parts.append(mode_advice)
        summary_parts.extend(weather_tips)

        # 推送出行规划卡片到聊天框
        card_lines = [f"📍 {origin_city} → {dest_city}  ({dist_km:.1f}km)"]
        for r in route_summaries[:3]:
            card_lines.append(f"{_mode_label(r['mode'], i18n)}  {r['distance']}  ⏱{r['duration']}")
        if weather_tips:
            card_lines.append(" ".join(weather_tips))
        if mode_advice:
            card_lines.append(mode_advice)
        push_lifekit_content(plugin, [
            {"type": "text", "text": f"🗺️ {origin_city} → {dest_city}"},
            {"type": "text", "text": "\n".join(card_lines)},
        ])

        return Ok({
            "origin": origin_city,
            "destination": dest_city,
            "distance_km": round(dist_km, 1),
            "summary": " | ".join(summary_parts),
            "routes": route_summaries,
            "weather_tips": weather_tips,
            "mode_advice": mode_advice,
            "provider": routing.provider,
            "next_actions": _build_next_actions(dest_city, i18n),
        })


def _city_name(loc: Dict[str, Any], fallback: str) -> str:
    for key in ("city", "label", "name", "address"):
        value = loc.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _localized(i18n: Any, key: str, default: str, **kwargs: Any) -> str:
    text = i18n.t(key, **kwargs)
    if text == key:
        try:
            return default.format(**kwargs)
        except (KeyError, IndexError):
            return default
    return text


def _mode_label(mode: str, i18n: Any | None = None) -> str:
    fallbacks = {"transit": "🚇 公交/地铁", "walking": "🚶 步行", "bicycling": "🚲 骑行", "driving": "🚗 驾车"}
    if i18n is None:
        return fallbacks.get(mode, mode)
    return _localized(i18n, f"trip.mode.{mode}", fallbacks.get(mode, mode))


def _build_next_actions(dest_city: str, i18n: Any) -> List[str]:
    return [
        _localized(i18n, "trip.next_food", "food_recommend location={city} - destination food", city=dest_city),
        _localized(i18n, "trip.next_nearby", "search_nearby location={city} - search near destination", city=dest_city),
        _localized(i18n, "trip.next_currency", "currency_convert - currency conversion"),
    ]


def _build_weather_tips(
    origin_data: Any, dest_data: Any,
    origin_loc: Dict, dest_loc: Dict,
    i18n: Any, plugin: Any,
) -> List[str]:
    tips: List[str] = []
    if not origin_data and not dest_data:
        return tips

    o_cur = origin_data.get("current", {}) if isinstance(origin_data, dict) else {}
    d_cur = dest_data.get("current", {}) if isinstance(dest_data, dict) else {}
    o_code = o_cur.get("weather_code", -1)
    d_code = d_cur.get("weather_code", -1)
    o_temp = o_cur.get("apparent_temperature")
    d_temp = d_cur.get("apparent_temperature")

    # 任一地有雨 → 带伞
    if o_code in RAIN_CODES or d_code in RAIN_CODES:
        tips.append(i18n.t("advice.rain"))

    # 温差大 → 提醒
    if o_temp is not None and d_temp is not None:
        diff = abs(o_temp - d_temp)
        if diff >= 5:
            tips.append(f"🌡️ {_city_name(origin_loc, 'origin')} {o_temp}°C → {_city_name(dest_loc, 'destination')} {d_temp}°C")

    return tips


def _build_mode_advice(dist_km: float, origin_data: Any, dest_data: Any, i18n: Any) -> str:
    """根据距离和天气给出出行方式建议。"""
    has_rain = False
    if isinstance(origin_data, dict):
        code = origin_data.get("current", {}).get("weather_code", -1)
        if code in RAIN_CODES:
            has_rain = True
    if isinstance(dest_data, dict):
        code = dest_data.get("current", {}).get("weather_code", -1)
        if code in RAIN_CODES:
            has_rain = True

    if dist_km <= 1:
        return _localized(i18n, "trip.mode_advice.transit_rain", "🚇 Rain expected - transit is recommended") if has_rain else _localized(i18n, "trip.mode_advice.walk_near", "🚶 Very close - walking is recommended")
    if dist_km <= 3 and not has_rain:
        return _localized(i18n, "trip.mode_advice.bike_mild", "🚲 Moderate distance and good weather - cycling works well")
    if dist_km <= 5:
        return _localized(i18n, "trip.mode_advice.transit_rain", "🚇 Rain expected - transit is recommended") if has_rain else _localized(i18n, "trip.mode_advice.transit_default", "🚇 Transit is recommended")
    return ""
