"""Air-quality router for LifeKit."""

from __future__ import annotations

from typing import Any

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, quick_action
from plugin.sdk.shared.core.router import PluginRouter

from .._api import AirQualityError, fetch_air_quality
from .._chat import push_lifekit_content
from .._coerce import finite_float
from .._contracts import AirQualityResult, CityParams


def _aqi_level(aqi: int) -> tuple[str, str]:
    if aqi <= 20:
        return "Good", "green"
    if aqi <= 40:
        return "Fair", "yellow"
    if aqi <= 60:
        return "Moderate", "orange"
    if aqi <= 80:
        return "Poor", "red"
    if aqi <= 100:
        return "Very poor", "purple"
    return "Extremely poor", "brown"


def _build_advice(aqi: int, pm25: float | None, uv: float | None) -> list[str]:
    tips: list[str] = []
    if aqi > 60:
        tips.append("Consider wearing a mask")
    if aqi > 80:
        tips.append("Reduce outdoor activity")
    if aqi <= 40:
        tips.append("Outdoor activity is generally suitable")
    if isinstance(pm25, (int, float)) and pm25 > 75:
        tips.append(f"PM2.5 is high ({pm25} ug/m3)")
    if isinstance(uv, (int, float)) and uv >= 6:
        tips.append("UV is strong; use sun protection")
    return tips


class AirQualityRouter(PluginRouter):
    """air_quality entry."""

    def __init__(self):
        super().__init__(name="air_quality")

    @plugin_entry(
        id="air_quality",
        name="Air quality",
        description="Query current air quality, PM2.5, PM10, UV, and related advice for a city or saved/default location.",
        params=CityParams,
        llm_result_model=AirQualityResult,
    )
    @quick_action(icon="air", priority=6)
    async def air_quality(self, params: CityParams | None = None, city: str = "", **_):
        if params is not None:
            city = params.city

        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        loc, loc_err = await plugin._resolve_location(city or None)
        if not loc:
            return Err(SdkError(i18n.t(loc_err or "error.no_location")))

        tz = str(plugin._cfg.get("timezone", "Asia/Shanghai"))

        try:
            data = await fetch_air_quality(loc["lat"], loc["lon"], tz=tz)
        except AirQualityError as exc:
            err_key = "error.forecast_timeout" if exc.cause == "timeout" else "error.fetch_failed"
            return Err(SdkError(i18n.t(err_key, city=loc["city"])))

        current: dict[str, Any] = data.get("current", {}) if isinstance(data, dict) else {}
        aqi_value = finite_float(current.get("european_aqi"))
        if aqi_value is None:
            return Err(SdkError(f"Unable to get air quality data for {loc['city']}"))

        aqi = int(aqi_value)
        pm25 = current.get("pm2_5")
        pm10 = current.get("pm10")
        o3 = current.get("ozone")
        no2 = current.get("nitrogen_dioxide")
        uv = current.get("uv_index")

        level, tone = _aqi_level(aqi)
        advice = _build_advice(aqi, pm25, uv)

        summary = f"{loc['city']} air quality: {level} (AQI {aqi})"
        if pm25 is not None:
            summary += f", PM2.5 {pm25} ug/m3"

        detail_parts = []
        if pm25 is not None:
            detail_parts.append(f"PM2.5: {pm25} ug/m3")
        if pm10 is not None:
            detail_parts.append(f"PM10: {pm10} ug/m3")
        if o3 is not None:
            detail_parts.append(f"O3: {o3} ug/m3")
        if no2 is not None:
            detail_parts.append(f"NO2: {no2} ug/m3")
        if uv is not None:
            detail_parts.append(f"UV: {uv}")

        blocks = [{"type": "text", "text": f"{loc['city']} - {level} (AQI {aqi})"}]
        if detail_parts:
            blocks.append({"type": "text", "text": " | ".join(detail_parts)})
        if advice:
            blocks.append({"type": "text", "text": "\n".join(advice)})

        push_lifekit_content(plugin, blocks)

        return Ok({
            "city": loc["city"],
            "summary": summary,
            "aqi": {
                "european_aqi": aqi,
                "level": level,
                "tone": tone,
                "pm2_5": pm25,
                "pm10": pm10,
                "ozone": o3,
                "nitrogen_dioxide": no2,
                "uv_index": uv,
            },
            "advice": advice,
            "next_actions": ["get_weather", "travel_advice", "food_recommend"],
        })
