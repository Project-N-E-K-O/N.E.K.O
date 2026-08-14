"""Small, provider-independent geographic calculations."""

from __future__ import annotations

import math


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in kilometres for two WGS84 points."""
    first_latitude = math.radians(lat1)
    second_latitude = math.radians(lat2)
    latitude_delta = second_latitude - first_latitude
    longitude_delta = math.radians(lon2 - lon1)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(first_latitude)
        * math.cos(second_latitude)
        * math.sin(longitude_delta / 2) ** 2
    )
    haversine = min(1.0, max(0.0, haversine))
    return 6371.0 * 2 * math.atan2(
        math.sqrt(haversine), math.sqrt(1 - haversine)
    )
