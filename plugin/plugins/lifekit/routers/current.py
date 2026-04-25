"""当前天气 + 每日预报 router。"""

from __future__ import annotations

from typing import Any, Dict, List

from plugin.sdk.plugin import plugin_entry, quick_action, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .._api import daily_val


class CurrentWeatherRouter(PluginRouter):
    """get_weather entry：当前天气 + 未来多日预报。"""

    def __init__(self):
        super().__init__(name="current_weather")

    @plugin_entry(
        id="get_weather",
        name="获取天气",
        description=(
            "查询指定城市（或自动定位）的当前天气和未来预报。"
            "可配合 travel_advice 获取出行建议，或 food_recommend 获取天气适合的美食推荐。"
        ),
        llm_result_fields=["summary", "current", "forecast", "next_actions"],
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名（中文/英文均可），留空则自动定位",
                    "default": "",
                },
            },
        },
    )
    @quick_action(icon="🌤️", priority=10)
    async def get_weather(self, city: str = "", **_):
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

        code = current_raw.get("weather_code", -1)
        current = {
            "weather": plugin._wmo_text(code),
            "temperature": current_raw.get("temperature_2m"),
            "feels_like": current_raw.get("apparent_temperature"),
            "humidity": current_raw.get("relative_humidity_2m"),
            "wind_speed": current_raw.get("wind_speed_10m"),
            "uv_index": current_raw.get("uv_index"),
        }

        forecast: List[Dict[str, Any]] = []
        for i, date in enumerate(daily_raw.get("time", [])):
            d_code = daily_val(daily_raw, "weather_code", i)
            forecast.append({
                "date": date,
                "weather": plugin._wmo_text(d_code) if d_code is not None else "",
                "temp_max": daily_val(daily_raw, "temperature_2m_max", i),
                "temp_min": daily_val(daily_raw, "temperature_2m_min", i),
                "precipitation": daily_val(daily_raw, "precipitation_sum", i),
                "uv_max": daily_val(daily_raw, "uv_index_max", i),
                "wind_max": daily_val(daily_raw, "wind_speed_10m_max", i),
            })

        summary = i18n.t(
            "summary.weather",
            city=loc["city"],
            weather=current["weather"],
            temp=current["temperature"],
            feels=current["feels_like"],
            humidity=current["humidity"],
        )
        if loc.get("_vpn_detected"):
            summary += i18n.t("summary.vpn_hint", ip_city=loc.get("_ip_city", ""))

        # 推送天气卡片到聊天框（直接显示，不经过 LLM）
        forecast_lines = []
        for f in forecast[:3]:
            forecast_lines.append(f"{f['date']}  {f['weather']}  {f.get('temp_min', '')}~{f.get('temp_max', '')}°C")

        blocks = [
            {"type": "text", "text": f"🌤️ {loc['city']} — {current['weather']} {current['temperature']}°C"},
            {"type": "text", "text": f"体感 {current['feels_like']}°C | 💧 {current['humidity']}% | 💨 {current['wind_speed']}km/h"},
        ]
        if forecast_lines:
            blocks.append({"type": "text", "text": "\n".join(forecast_lines)})
        plugin.push_chat_content(blocks)

        return Ok({
            "city": loc["city"],
            "summary": summary,
            "current": current,
            "forecast": forecast,
            "vpn_detected": bool(loc.get("_vpn_detected")),
            "next_actions": ["travel_advice — 出行建议", "food_recommend — 美食推荐", "hourly_forecast — 逐小时预报"],
        })
