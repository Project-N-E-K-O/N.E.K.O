"""出行建议 router。"""

from __future__ import annotations

from typing import Any, Dict, List

from plugin.sdk.plugin import plugin_entry, quick_action, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .._api import RAIN_CODES, SNOW_CODES
from .._i18n import I18n


def build_travel_advice(
    current: Dict[str, Any], daily: Dict[str, Any], t: I18n,
) -> Dict[str, Any]:
    """根据天气数据生成出行建议（使用体感温度）。"""
    feels = current.get("apparent_temperature")
    temp = current.get("temperature_2m")
    ref = feels if feels is not None else temp
    code = current.get("weather_code", -1)
    uv = current.get("uv_index", 0)
    wind = current.get("wind_speed_10m", 0)

    tips: List[str] = []

    if ref is not None:
        if ref < 5:
            tips.append(t.t("advice.cold"))
        elif ref < 15:
            tips.append(t.t("advice.cool"))
        elif ref < 25:
            tips.append(t.t("advice.mild"))
        else:
            tips.append(t.t("advice.hot"))

    if code in RAIN_CODES:
        tips.append(t.t("advice.rain"))
    elif code in SNOW_CODES:
        tips.append(t.t("advice.snow"))

    if uv >= 8:
        tips.append(t.t("advice.uv_extreme"))
    elif uv >= 5:
        tips.append(t.t("advice.uv_high"))

    if wind >= 40:
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
    if eff < 10:
        clothing = t.t("clothing.heavy")
    elif eff < 22:
        clothing = t.t("clothing.light")
    else:
        clothing = t.t("clothing.cool")

    return {
        "tips": tips,
        "clothing": clothing,
        "umbrella": code in RAIN_CODES,
        "sunscreen": uv >= 5,
    }


class TravelAdviceRouter(PluginRouter):
    """travel_advice entry：穿衣/带伞/防晒等出行建议。"""

    def __init__(self):
        super().__init__(name="travel_advice")

    @plugin_entry(
        id="travel_advice",
        name="出行建议",
        description="根据天气给出穿衣、带伞、防晒等出行建议。",
        llm_result_fields=["summary", "tips"],
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名，留空则自动定位",
                    "default": "",
                },
            },
        },
    )
    @quick_action(icon="🧳", priority=9)
    async def travel_advice(self, city: str = "", **_):
        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        loc, loc_err = await plugin._resolve_location(city)
        if not loc:
            return Err(SdkError(i18n.t(loc_err or "error.no_location")))

        data, data_err = await plugin._get_weather_data(loc)
        if not data:
            return Err(SdkError(i18n.t(data_err or "error.fetch_failed", city=loc["city"])))

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
            extras.append("☂️ " + i18n.t("advice.bring_umbrella", fallback="带伞"))
        if advice["sunscreen"]:
            extras.append("🧴 " + i18n.t("advice.bring_sunscreen", fallback="防晒"))
        if extras:
            card_lines.append(" | ".join(extras))

        plugin.push_chat_content([
            {"type": "text", "text": f"🧳 {loc['city']} — {i18n.t('entry.travel_advice', fallback='出行建议')}"},
            {"type": "text", "text": "\n".join(card_lines)},
        ])

        return Ok({
            "city": loc["city"],
            "summary": summary,
            **advice,
        })
