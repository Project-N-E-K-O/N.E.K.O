"""Versioned, shared thresholds for non-personalized LifeKit advice."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdvicePolicy:
    version: str = "lifekit-general-v1"
    walk_max_km: float = 2.0
    bicycle_max_km: float = 10.0
    transit_min_km: float = 2.0
    driving_min_km: float = 5.0
    cold_below_c: float = 5.0
    cool_below_c: float = 15.0
    mild_below_c: float = 25.0
    heavy_clothing_below_c: float = 10.0
    light_clothing_below_c: float = 22.0
    uv_protection_from: float = 6.0
    uv_extreme_from: float = 8.0
    strong_wind_from_kmh: float = 40.0
    notable_temperature_difference_c: float = 5.0
    aqi_mask_above: int = 60
    aqi_reduce_outdoors_above: int = 80
    aqi_outdoors_ok_at_most: int = 40
    pm25_high_above: float = 75.0

    def route_modes(self, distance_km: float) -> tuple[str, ...]:
        modes: list[str] = []
        if distance_km <= self.walk_max_km:
            modes.append("walking")
        if distance_km <= self.bicycle_max_km:
            modes.append("bicycling")
        if distance_km >= self.transit_min_km:
            modes.append("transit")
        if distance_km >= self.driving_min_km:
            modes.append("driving")
        return tuple(modes or ("transit", "driving"))

    def primary_advice_mode(
        self,
        distance_km: float,
        *,
        has_rain: bool,
    ) -> str:
        if has_rain and distance_km <= self.bicycle_max_km:
            return "transit"
        if distance_km <= self.walk_max_km:
            return "walking"
        if distance_km <= self.bicycle_max_km:
            return "bicycling"
        return ""

    def needs_sun_protection(self, uv_index: float) -> bool:
        return uv_index >= self.uv_protection_from


DEFAULT_ADVICE_POLICY = AdvicePolicy()
