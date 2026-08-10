"""出行建议 router。"""

from __future__ import annotations

from typing import Any, Dict, List

from plugin.sdk.plugin import Ok, plugin_entry, quick_action, tr
from plugin.sdk.shared.core.router import PluginRouter

from .._advice_policy import DEFAULT_ADVICE_POLICY
from .._api import RAIN_CODES, SNOW_CODES
from .._chat import push_lifekit_content
from .._contracts import CityParams, TravelAdviceResult
from .._i18n import I18n
from .._location import LocationPurpose
from .._location_entry import (
    apply_location_assumption,
    location_unavailable_result,
    upstream_unavailable_result,
)


def build_travel_advice(
    current: Dict[str, Any], daily: Dict[str, Any], t: I18n,
) -> Dict[str, Any]:
    """根据天气数据生成出行建议（使用体感温度）。"""
    feels = current.get("apparent_temperature")
    temp = current.get("temperature_2m")
    ref = feels if feels is not None else temp
    code = current.get("weather_code", -1)
    uv = current.get("uv_index")
    wind = current.get("wind_speed_10m")
    uv = uv if isinstance(uv, (int, float)) else 0
    wind = wind if isinstance(wind, (int, float)) else 0

    tips: List[str] = []
    policy = DEFAULT_ADVICE_POLICY

    if ref is not None:
        if ref < policy.cold_below_c:
            tips.append(t.t("advice.cold"))
        elif ref < policy.cool_below_c:
            tips.append(t.t("advice.cool"))
        elif ref < policy.mild_below_c:
            tips.append(t.t("advice.mild"))
        else:
            tips.append(t.t("advice.hot"))

    if code in RAIN_CODES:
        tips.append(t.t("advice.rain"))
    elif code in SNOW_CODES:
        tips.append(t.t("advice.snow"))

    if uv >= policy.uv_extreme_from:
        tips.append(t.t("advice.uv_extreme"))
    elif policy.needs_sun_protection(uv):
        tips.append(t.t("advice.uv_high"))

    if wind >= policy.strong_wind_from_kmh:
        tips.append(t.t("advice.wind_strong"))

    daily_codes = daily.get("weather_code", [])
    daily_dates = daily.get("time", [])
    rain_days = [
        daily_dates[i] for i, c in enumerate(daily_codes)
        if c in RAIN_CODES and i < len(daily_dates)
    ]
    if rain_days:
        tips.append(t.t("advice.rain_forecast", dates=", ".join(rain_days)))

    eff = ref if ref is not None else 20
    if eff < policy.heavy_clothing_below_c:
        clothing = t.t("clothing.heavy")
    elif eff < policy.light_clothing_below_c:
        clothing = t.t("clothing.light")
    else:
        clothing = t.t("clothing.cool")

    return {
        "tips": tips,
        "clothing": clothing,
        "umbrella": code in RAIN_CODES,
        "sunscreen": policy.needs_sun_protection(uv),
    }


class TravelAdviceRouter(PluginRouter):
    """travel_advice entry：穿衣/带伞/防晒等出行建议。"""

    def __init__(self):
        super().__init__(name="travel_advice")

    @plugin_entry(
        id="travel_advice",
        name=tr("entries.travelAdvice.name", default="Travel advice"),
        description=tr("entries.travelAdvice.description", default="Give clothing, umbrella, sunscreen, and travel advice based on weather."),
        params=CityParams,
        llm_result_model=TravelAdviceResult,
    )
    @quick_action(icon="🧳", priority=9)
    async def travel_advice(self, params: CityParams | None = None, city: str = "", **_):
        if params is not None:
            city = params.city

        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        loc, loc_err = await plugin._resolve_location(city, purpose=LocationPurpose.WEATHER)
        if not loc:
            return location_unavailable_result(loc_err, i18n)

        data, data_err = await plugin._get_weather_data(loc)
        if not data:
            return upstream_unavailable_result(
                i18n.t(data_err or "error.fetch_failed", city=loc["city"]),
                i18n,
                location=loc,
            )

        current_raw = data.get("current", {})
        daily_raw = data.get("daily", {})
        advice = build_travel_advice(current_raw, daily_raw, i18n)

        summary = i18n.t("summary.travel_prefix", city=loc["city"])
        summary += " ".join(advice["tips"][:3])

        # 推送出行建议卡片到聊天框
        card_lines = []
        if advice["tips"]:
            card_lines.append(" · ".join(advice["tips"][:3]))
        card_lines.append(f"👔 {advice['clothing']}")
        extras = []
        if advice["umbrella"]:
            extras.append("☂️ " + i18n.t("advice.bring_umbrella"))
        if advice["sunscreen"]:
            extras.append("🧴 " + i18n.t("advice.bring_sunscreen"))
        if extras:
            card_lines.append(" | ".join(extras))

        push_lifekit_content(plugin, [
            {"type": "text", "text": f"🧳 {loc['city']} — {i18n.t('runtime.travel_title')}"},
            {"type": "text", "text": "\n".join(card_lines)},
        ])

        return Ok(apply_location_assumption({
            "status": "ready",
            "city": loc["city"],
            "summary": summary,
            **advice,
            "next_actions": ["food_recommend", "trip_advice"],
        }, loc, i18n))
