from __future__ import annotations

import pytest

from plugin.plugins.lifekit._coordinates import gcj02_to_wgs84, wgs84_to_gcj02


def test_coordinate_conversion_is_noop_outside_china() -> None:
    assert wgs84_to_gcj02(35.6762, 139.6503) == (35.6762, 139.6503)
    assert gcj02_to_wgs84(35.6762, 139.6503) == (35.6762, 139.6503)


def test_shanghai_coordinates_round_trip_between_wgs84_and_gcj02() -> None:
    wgs_lat, wgs_lon = 31.2304, 121.4737

    gcj_lat, gcj_lon = wgs84_to_gcj02(wgs_lat, wgs_lon)
    round_trip_lat, round_trip_lon = gcj02_to_wgs84(gcj_lat, gcj_lon)

    assert abs(gcj_lat - wgs_lat) > 0.001
    assert abs(gcj_lon - wgs_lon) > 0.001
    assert round_trip_lat == pytest.approx(wgs_lat, abs=1e-6)
    assert round_trip_lon == pytest.approx(wgs_lon, abs=1e-6)
