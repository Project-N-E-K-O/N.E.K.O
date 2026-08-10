"""Air-quality router for LifeKit."""

from __future__ import annotations

from typing import Any

from plugin.sdk.plugin import Ok, plugin_entry, quick_action, tr
from plugin.sdk.shared.core.router import PluginRouter

from .._advice_policy import DEFAULT_ADVICE_POLICY
from .._api import AirQualityError, fetch_air_quality
from .._chat import push_lifekit_content
from .._coerce import finite_float, timezone_name
from .._contracts import AirQualityResult, CityParams
from .._location import LocationPurpose
from .._location_entry import (
    apply_location_assumption,
    location_unavailable_result,
    upstream_unavailable_result,
)


def _aqi_level(aqi: int, i18n: Any) -> tuple[str, str]:
    if aqi <= 20:
        return i18n.t("air_quality.level.good"), "green"
    if aqi <= 40:
        return i18n.t("air_quality.level.fair"), "yellow"
    if aqi <= 60:
        return i18n.t("air_quality.level.moderate"), "orange"
    if aqi <= 80:
        return i18n.t("air_quality.level.poor"), "red"
    if aqi <= 100:
        return i18n.t("air_quality.level.very_poor"), "purple"
    return i18n.t("air_quality.level.extremely_poor"), "brown"


def _build_advice(aqi: int, pm25: float | None, uv: float | None, i18n: Any) -> list[str]:
    tips: list[str] = []
    policy = DEFAULT_ADVICE_POLICY
    if aqi > policy.aqi_mask_above:
        tips.append(i18n.t("air_quality.advice.mask"))
    if aqi > policy.aqi_reduce_outdoors_above:
        tips.append(i18n.t("air_quality.advice.reduce_outdoors"))
    if aqi <= policy.aqi_outdoors_ok_at_most:
        tips.append(i18n.t("air_quality.advice.outdoors_ok"))
    if isinstance(pm25, (int, float)) and pm25 > policy.pm25_high_above:
        tips.append(i18n.t("air_quality.advice.pm25_high", value=pm25))
    if isinstance(uv, (int, float)) and policy.needs_sun_protection(uv):
        tips.append(i18n.t("air_quality.advice.uv"))
    return tips


class AirQualityRouter(PluginRouter):
    """air_quality entry."""

    def __init__(self):
        super().__init__(name="air_quality")

    @plugin_entry(
        id="air_quality",
        name=tr("entries.airQuality.name", default="Air quality"),
        description=tr("entries.airQuality.description", default="Query current air quality, PM2.5, PM10, UV, and related advice for a city or saved location."),
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

        loc, loc_err = await plugin._resolve_location(
            city or None,
            purpose=LocationPurpose.AIR_QUALITY,
        )
        if not loc:
            return location_unavailable_result(loc_err, i18n)

        tz = timezone_name(loc.get("timezone"), plugin._cfg.get("timezone"))

        try:
            data = await fetch_air_quality(loc["lat"], loc["lon"], tz=tz)
        except AirQualityError as exc:
            err_key = "error.forecast_timeout" if exc.cause == "timeout" else "error.fetch_failed"
            return upstream_unavailable_result(
                i18n.t(err_key, city=loc["city"]), i18n, location=loc,
            )

        current: dict[str, Any] = data.get("current", {}) if isinstance(data, dict) else {}
        aqi_value = finite_float(current.get("european_aqi"))
        if aqi_value is None:
            return upstream_unavailable_result(
                i18n.t("error.fetch_failed", city=loc["city"]),
                i18n,
                location=loc,
            )

        aqi = int(aqi_value)
        pm25 = current.get("pm2_5")
        pm10 = current.get("pm10")
        o3 = current.get("ozone")
        no2 = current.get("nitrogen_dioxide")
        uv = current.get("uv_index")

        level, tone = _aqi_level(aqi, i18n)
        advice = _build_advice(aqi, pm25, uv, i18n)

        summary = i18n.t("air_quality.summary", city=loc["city"], level=level, aqi=aqi)
        if pm25 is not None:
            summary += i18n.t("air_quality.pm25_suffix", value=pm25)

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

        return Ok(apply_location_assumption({
            "status": "ready",
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
        }, loc, i18n))
