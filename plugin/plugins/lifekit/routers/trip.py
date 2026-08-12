"""出行规划 router — 路线 + 天气综合建议。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from plugin.sdk.plugin import Ok, plugin_entry, quick_action, tr
from plugin.sdk.shared.core.router import PluginRouter

from .._advice_policy import DEFAULT_ADVICE_POLICY
from .._api import RAIN_CODES
from .._chat import push_lifekit_content
from .._contracts import TripAdviceParams, TripAdviceResult
from .._geodesy import haversine_km
from .._location import LocationPurpose
from .._location_entry import (
    apply_location_assumptions,
    location_unavailable_result,
    upstream_unavailable_result,
)
from .._routing import RoutingService, format_distance, format_duration


class TripRouter(PluginRouter):
    """trip_advice entry：路线规划 + 天气综合出行建议。"""

    def __init__(self):
        super().__init__(name="trip")

    @plugin_entry(
        id="trip_advice",
        name=tr("entries.tripAdvice.name", default="Plan a trip"),
        description=tr("entries.tripAdvice.description", default="Plan routes between cities or saved locations and combine them with weather advice."),
        params=TripAdviceParams,
        llm_result_model=TripAdviceResult,
    )
    @quick_action(icon="🗺️", priority=7)
    async def trip_advice(
        self,
        params: TripAdviceParams | None = None,
        destination: str = "",
        origin: str = "",
        mode: str = "",
        **_,
    ):
        if params is not None:
            destination = params.destination
            origin = params.origin
            mode = params.mode

        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        if not destination.strip():
            return location_unavailable_result("error.no_location", i18n)

        # 起终点彼此独立，并发解析，避免把两段 geocoding 延迟串起来。
        (origin_loc, origin_err), (dest_loc, dest_err) = await asyncio.gather(
            plugin._resolve_location(
                origin or None,
                purpose=LocationPurpose.ROUTE_ORIGIN,
            ),
            plugin._resolve_location(
                destination,
                purpose=LocationPurpose.ROUTE_DESTINATION,
            ),
        )
        if not origin_loc:
            return location_unavailable_result(origin_err, i18n)
        if not dest_loc:
            return location_unavailable_result(dest_err, i18n)

        # 直线距离
        dist_km = haversine_km(origin_loc["lat"], origin_loc["lon"], dest_loc["lat"], dest_loc["lon"])

        # 路线规划
        svc = RoutingService(plugin._cfg)
        mode_raw = mode.strip().lower() if mode else ""
        _VALID_MODES = {"transit", "walking", "bicycling", "driving"}
        mode_aliases = {
            "walk": "walking",
            "foot": "walking",
            "bike": "bicycling",
            "bicycle": "bicycling",
            "cycle": "bicycling",
            "drive": "driving",
            "car": "driving",
            "bus": "transit",
            "subway": "transit",
            "metro": "transit",
            "public transport": "transit",
        }
        mode_clean = mode_aliases.get(mode_raw, mode_raw)
        mode_assumption = ""
        if mode_clean not in _VALID_MODES:
            if mode_clean:
                mode_assumption = i18n.t(
                    "trip.invalid_mode",
                    mode=mode_raw,
                    valid=", ".join(
                        _mode_label(value, i18n)
                        for value in sorted(_VALID_MODES)
                    ),
                )
            mode_clean = ""
        selected_mode = mode_clean or "auto"
        modes = [mode_clean] if mode_clean else None
        routing, (origin_weather, _), (dest_weather, _) = await asyncio.gather(
            svc.plan(
                origin_loc["lat"], origin_loc["lon"],
                dest_loc["lat"], dest_loc["lon"],
                modes=modes,
            ),
            plugin._get_weather_data(origin_loc),
            plugin._get_weather_data(dest_loc),
        )
        if routing.error and not routing.routes:
            return upstream_unavailable_result(
                i18n.t("trip.route_unavailable"),
                i18n,
                location=dest_loc,
            )

        # 构建路线摘要
        route_summaries: List[Dict[str, Any]] = []
        for route in routing.routes:
            entry: Dict[str, Any] = {
                "mode": route.mode,
                "distance": format_distance(route.distance_m),
                "duration": format_duration(route.duration_s),
                "summary": route.summary or _mode_label(route.mode, i18n),
            }
            if route.cost:
                entry["cost"] = route.cost
            if route.steps:
                entry["steps"] = [
                    {
                        "instruction": _localized_step_instruction(s, i18n),
                        "mode": s.mode,
                        "duration": format_duration(s.duration_s),
                    }
                    for s in route.steps[:8]
                ]
            route_summaries.append(entry)

        # 天气综合建议
        weather_tips = _build_weather_tips(origin_weather, dest_weather, origin_loc, dest_loc, i18n, plugin)

        # 出行方式建议
        mode_advice = _build_mode_advice(dist_km, origin_weather, dest_weather, i18n)

        # 总结
        summary_parts = [
            f"{origin_loc['city']} → {dest_loc['city']}",
            f"{i18n.t('trip.distance')}: {dist_km:.1f}km",
        ]
        if routing.routes:
            best = routing.routes[0]
            summary_parts.append(f"{i18n.t('trip.recommended')}: {_mode_label(best.mode, i18n)} {format_duration(best.duration_s)}")
        if mode_advice:
            summary_parts.append(mode_advice)
        if mode_assumption:
            summary_parts.append(mode_assumption)
        summary_parts.extend(weather_tips)

        # 推送出行规划卡片到聊天框
        card_lines = [f"📍 {origin_loc['city']} → {dest_loc['city']}  ({dist_km:.1f}km)"]
        for r in route_summaries[:3]:
            card_lines.append(f"{_mode_label(r['mode'], i18n)}  {r['distance']}  ⏱{r['duration']}")
        if weather_tips:
            card_lines.append(" ".join(weather_tips))
        if mode_advice:
            card_lines.append(mode_advice)
        if mode_assumption:
            card_lines.append(mode_assumption)
        push_lifekit_content(plugin, [
            {"type": "text", "text": f"🗺️ {origin_loc['city']} → {dest_loc['city']}"},
            {"type": "text", "text": "\n".join(card_lines)},
        ])

        return Ok(apply_location_assumptions({
            "status": "ready",
            "origin": origin_loc["city"],
            "destination": dest_loc["city"],
            "distance_km": round(dist_km, 1),
            "summary": " | ".join(summary_parts),
            "routes": route_summaries,
            "weather_tips": weather_tips,
            "mode_advice": mode_advice,
            "requested_mode": mode_raw,
            "selected_mode": selected_mode,
            "mode_assumption": mode_assumption,
            "provider": routing.provider,
            "next_actions": [
                f"food_recommend location={dest_loc['city']}",
                f"search_nearby request={dest_loc['city']} location_hint={dest_loc['city']} place_intent=explore",
                "currency_convert",
            ],
        }, (origin_loc, dest_loc), i18n))


def _mode_label(mode: str, i18n: Any) -> str:
    key = {
        "transit": "trip.mode_transit",
        "walking": "trip.mode_walking",
        "bicycling": "trip.mode_bicycling",
        "driving": "trip.mode_driving",
    }.get(mode)
    return i18n.t(key) if key else mode


def _localized_step_instruction(step: Any, i18n: Any) -> str:
    if step.instruction:
        return step.instruction
    if step.mode == "walk":
        return i18n.t("trip.step_walk", distance=format_distance(step.distance_m))
    if step.line_name:
        return i18n.t("trip.step_take", line=step.line_name)
    return _mode_label(step.mode, i18n)


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
        if diff >= DEFAULT_ADVICE_POLICY.notable_temperature_difference_c:
            tips.append(f"🌡️ {origin_loc['city']} {o_temp}°C → {dest_loc['city']} {d_temp}°C")

    return tips


def _build_mode_advice(
    dist_km: float,
    origin_data: Any,
    dest_data: Any,
    i18n: Any,
) -> str:
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

    advised_mode = DEFAULT_ADVICE_POLICY.primary_advice_mode(
        dist_km,
        has_rain=has_rain,
    )
    if advised_mode == "walking":
        return i18n.t("trip.near_walk")
    if advised_mode == "bicycling":
        return i18n.t("trip.good_bike")
    if advised_mode == "transit":
        return i18n.t("trip.rain_transit") if has_rain else i18n.t("trip.transit_advice")
    return ""
