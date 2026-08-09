"""Coordinate-system conversions used at map-provider boundaries."""

from __future__ import annotations

import math


_GCJ_SEMI_MAJOR_AXIS_M = 6378245.0
_GCJ_ECCENTRICITY_SQUARED = 0.006693421622965943


def wgs84_to_gcj02(lat: float, lon: float) -> tuple[float, float]:
    """Convert WGS84 to GCJ-02; coordinates outside China are unchanged."""
    if _outside_china(lat, lon):
        return lat, lon
    delta_lat, delta_lon = _gcj_delta(lat, lon)
    return lat + delta_lat, lon + delta_lon


def gcj02_to_wgs84(lat: float, lon: float) -> tuple[float, float]:
    """Convert GCJ-02 to WGS84 using a small bounded inverse iteration."""
    if _outside_china(lat, lon):
        return lat, lon

    estimate_lat, estimate_lon = lat, lon
    for _ in range(8):
        converted_lat, converted_lon = wgs84_to_gcj02(estimate_lat, estimate_lon)
        estimate_lat -= converted_lat - lat
        estimate_lon -= converted_lon - lon
    return estimate_lat, estimate_lon


def _outside_china(lat: float, lon: float) -> bool:
    return not (72.004 <= lon <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _gcj_delta(lat: float, lon: float) -> tuple[float, float]:
    shifted_lon = lon - 105.0
    shifted_lat = lat - 35.0
    delta_lat = _transform_lat(shifted_lon, shifted_lat)
    delta_lon = _transform_lon(shifted_lon, shifted_lat)
    rad_lat = math.radians(lat)
    magic = 1.0 - _GCJ_ECCENTRICITY_SQUARED * math.sin(rad_lat) ** 2
    sqrt_magic = math.sqrt(magic)
    delta_lat = delta_lat * 180.0 / (
        (
            _GCJ_SEMI_MAJOR_AXIS_M
            * (1.0 - _GCJ_ECCENTRICITY_SQUARED)
            / (magic * sqrt_magic)
        )
        * math.pi
    )
    delta_lon = delta_lon * 180.0 / (
        (_GCJ_SEMI_MAJOR_AXIS_M / sqrt_magic) * math.cos(rad_lat) * math.pi
    )
    return delta_lat, delta_lon


def _transform_lat(lon: float, lat: float) -> float:
    result = -100.0 + 2.0 * lon + 3.0 * lat + 0.2 * lat**2
    result += 0.1 * lon * lat + 0.2 * math.sqrt(abs(lon))
    result += (20.0 * math.sin(6.0 * lon * math.pi) + 20.0 * math.sin(2.0 * lon * math.pi)) * 2.0 / 3.0
    result += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    result += (160.0 * math.sin(lat / 12.0 * math.pi) + 320.0 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return result


def _transform_lon(lon: float, lat: float) -> float:
    result = 300.0 + lon + 2.0 * lat + 0.1 * lon**2
    result += 0.1 * lon * lat + 0.1 * math.sqrt(abs(lon))
    result += (20.0 * math.sin(6.0 * lon * math.pi) + 20.0 * math.sin(2.0 * lon * math.pi)) * 2.0 / 3.0
    result += (20.0 * math.sin(lon * math.pi) + 40.0 * math.sin(lon / 3.0 * math.pi)) * 2.0 / 3.0
    result += (150.0 * math.sin(lon / 12.0 * math.pi) + 300.0 * math.sin(lon / 30.0 * math.pi)) * 2.0 / 3.0
    return result
