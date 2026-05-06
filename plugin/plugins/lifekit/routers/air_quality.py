"""空气质量 router — 基于 Open-Meteo Air Quality API。"""

from __future__ import annotations

from typing import Any, Dict

from plugin.sdk.plugin import plugin_entry, quick_action, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .._api import fetch_air_quality, AirQualityError


def _aqi_level(aqi: int) -> tuple[str, str]:
    """European AQI → (等级, emoji)。"""
    if aqi <= 20:
        return "优", "🟢"
    if aqi <= 40:
        return "良", "🟡"
    if aqi <= 60:
        return "轻度污染", "🟠"
    if aqi <= 80:
        return "中度污染", "🔴"
    if aqi <= 100:
        return "重度污染", "🟣"
    return "严重污染", "⚫"


def _build_advice(aqi: int, pm25: float | None, uv: float | None) -> list[str]:
    """根据 AQI 和 PM2.5 生成建议。"""
    tips = []
    if aqi > 60:
        tips.append("😷 建议佩戴口罩")
    if aqi > 80:
        tips.append("🏠 减少户外活动")
    if aqi <= 40:
        tips.append("🏃 适合户外运动")
    if isinstance(pm25, (int, float)) and pm25 > 75:
        tips.append(f"⚠️ PM2.5 偏高 ({pm25}µg/m³)")
    if isinstance(uv, (int, float)) and uv >= 6:
        tips.append("🧴 紫外线较强，注意防晒")
    return tips


class AirQualityRouter(PluginRouter):
    """air_quality entry：空气质量查询。"""

    def __init__(self):
        super().__init__(name="air_quality")

    @plugin_entry(
        id="air_quality",
        name="空气质量",
        description=(
            "查询当前空气质量指数(AQI)、PM2.5、PM10等。"
            "适合回答「今天空气好不好」「适合跑步吗」「要戴口罩吗」。"
            "可配合 get_weather 获取完整天气信息，或 travel_advice 获取出行建议。"
        ),
        llm_result_fields=["summary", "aqi", "advice", "next_actions"],
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
    @quick_action(icon="🌬️", priority=6)
    async def air_quality(self, city: str = "", **_):
        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        loc, loc_err = await plugin._resolve_location(city or None)
        if not loc:
            return Err(SdkError(i18n.t(loc_err or "error.no_location")))

        tz = str(plugin._cfg.get("timezone", "Asia/Shanghai"))

        try:
            data = await fetch_air_quality(loc["lat"], loc["lon"], tz=tz)
        except AirQualityError as e:
            err_key = "error.forecast_timeout" if e.cause == "timeout" else "error.fetch_failed"
            return Err(SdkError(i18n.t(err_key, city=loc["city"])))

        current = data.get("current", {})
        aqi = current.get("european_aqi")
        if aqi is None:
            return Err(SdkError(f"无法获取 {loc['city']} 的空气质量数据"))

        aqi = int(aqi)
        pm25 = current.get("pm2_5")
        pm10 = current.get("pm10")
        o3 = current.get("ozone")
        no2 = current.get("nitrogen_dioxide")
        uv = current.get("uv_index")

        level, emoji = _aqi_level(aqi)
        advice = _build_advice(aqi, pm25, uv)

        summary = f"{loc['city']} 空气质量 {emoji} {level} (AQI {aqi})"
        if pm25 is not None:
            summary += f"，PM2.5 {pm25}µg/m³"

        # 推送卡片
        detail_parts = []
        if pm25 is not None:
            detail_parts.append(f"PM2.5: {pm25}µg/m³")
        if pm10 is not None:
            detail_parts.append(f"PM10: {pm10}µg/m³")
        if o3 is not None:
            detail_parts.append(f"O₃: {o3}µg/m³")
        if no2 is not None:
            detail_parts.append(f"NO₂: {no2}µg/m³")
        if uv is not None:
            detail_parts.append(f"UV: {uv}")

        blocks = [
            {"type": "text", "text": f"🌬️ {loc['city']} — {emoji} {level} (AQI {aqi})"},
        ]
        if detail_parts:
            blocks.append({"type": "text", "text": " | ".join(detail_parts)})
        if advice:
            blocks.append({"type": "text", "text": "\n".join(advice)})

        plugin.push_chat_content(blocks)

        return Ok({
            "city": loc["city"],
            "summary": summary,
            "aqi": {
                "european_aqi": aqi,
                "level": level,
                "pm2_5": pm25,
                "pm10": pm10,
                "ozone": o3,
                "nitrogen_dioxide": no2,
                "uv_index": uv,
            },
            "advice": advice,
            "next_actions": ["get_weather — 完整天气", "travel_advice — 出行建议", "food_recommend — 美食推荐"],
        })
