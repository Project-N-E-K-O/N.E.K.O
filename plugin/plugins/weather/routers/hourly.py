"""逐小时预报 router（未来 48 小时）。"""

from __future__ import annotations

from typing import Any, Dict, List

from plugin.sdk.plugin import plugin_entry, quick_action, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .._api import fetch_forecast, RAIN_CODES, SNOW_CODES

_HOURLY_VARS = (
    "temperature_2m,apparent_temperature,precipitation_probability,"
    "precipitation,weather_code,wind_speed_10m,uv_index"
)


class HourlyForecastRouter(PluginRouter):
    """hourly_forecast entry：未来 48 小时逐小时天气。"""

    def __init__(self):
        super().__init__(name="hourly_forecast")

    @plugin_entry(
        id="hourly_forecast",
        name="逐小时预报",
        description="查询未来 48 小时的逐小时天气预报，包含温度变化、降水概率、风力等。适合回答「明天下午会不会下雨」这类问题。",
        llm_result_fields=["summary", "hours"],
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名，留空则自动定位",
                    "default": "",
                },
                "hours": {
                    "type": "integer",
                    "description": "预报小时数（1-168，默认 48）",
                    "default": 48,
                },
            },
        },
    )
    @quick_action(icon="📊", priority=8)
    async def hourly_forecast(self, city: str = "", hours: int = 48, **_):
        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        loc = await plugin._resolve_location(city)
        if not loc:
            return Err(SdkError(i18n.t("error.no_location")))

        hours = max(1, min(int(hours), 168))
        tz = str(plugin._cfg.get("timezone", "Asia/Shanghai"))

        data = await fetch_forecast(
            loc["lat"], loc["lon"],
            days=1,
            tz=tz,
            hourly_vars=_HOURLY_VARS,
            forecast_hours=hours,
        )
        if not data:
            return Err(SdkError(i18n.t("error.fetch_failed", city=loc["city"])))

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        result_hours: List[Dict[str, Any]] = []

        for i, t in enumerate(times):
            code = _safe_idx(hourly, "weather_code", i)
            result_hours.append({
                "time": t,
                "temp": _safe_idx(hourly, "temperature_2m", i),
                "feels_like": _safe_idx(hourly, "apparent_temperature", i),
                "precip_prob": _safe_idx(hourly, "precipitation_probability", i),
                "precip_mm": _safe_idx(hourly, "precipitation", i),
                "weather": plugin._wmo_text(code) if code is not None else "",
                "weather_code": code,
                "wind_speed": _safe_idx(hourly, "wind_speed_10m", i),
                "uv_index": _safe_idx(hourly, "uv_index", i),
            })

        # 生成摘要：温度范围 + 降水时段
        temps = [h["temp"] for h in result_hours if h["temp"] is not None]
        rain_hours = [
            h["time"] for h in result_hours
            if isinstance(h.get("weather_code"), int) and h["weather_code"] in RAIN_CODES
        ]

        parts = [f"{loc['city']}"]
        if temps:
            parts.append(i18n.t(
                "summary.hourly_temp_range",
                low=min(temps), high=max(temps), count=len(result_hours),
            ))
        if rain_hours:
            # 只显示前几个降水时段
            shown = rain_hours[:4]
            suffix = f" (+{len(rain_hours) - 4})" if len(rain_hours) > 4 else ""
            parts.append(i18n.t(
                "summary.hourly_rain_times",
                times=", ".join(t.split("T")[1] if "T" in t else t for t in shown) + suffix,
            ))
        else:
            parts.append(i18n.t("summary.hourly_no_rain"))

        summary = " | ".join(parts)

        return Ok({
            "city": loc["city"],
            "summary": summary,
            "hours": result_hours,
            "total_hours": len(result_hours),
        })


def _safe_idx(data: Dict[str, Any], field: str, idx: int) -> Any:
    arr = data.get(field)
    if isinstance(arr, list) and idx < len(arr):
        return arr[idx]
    return None
