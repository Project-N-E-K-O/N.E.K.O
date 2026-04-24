"""Tests for weather plugin geo utilities."""

from __future__ import annotations

from plugin.plugins.weather._geo import detect_vpn_conflict, get_system_timezone


def test_detect_vpn_conflict_same_tz():
    assert detect_vpn_conflict("Asia/Shanghai", "Asia/Shanghai") is False


def test_detect_vpn_conflict_different_tz():
    # Shanghai (+8) vs LA (-7) = 15h difference
    assert detect_vpn_conflict("America/Los_Angeles", "Asia/Shanghai") is True


def test_detect_vpn_conflict_close_tz():
    # Tokyo (+9) vs Shanghai (+8) = 1h difference, below threshold
    assert detect_vpn_conflict("Asia/Tokyo", "Asia/Shanghai") is False


def test_detect_vpn_conflict_empty():
    assert detect_vpn_conflict("", "Asia/Shanghai") is False
    assert detect_vpn_conflict("Asia/Shanghai", "") is False
    assert detect_vpn_conflict("", "") is False


def test_detect_vpn_conflict_invalid():
    assert detect_vpn_conflict("Invalid/Zone", "Asia/Shanghai") is False


def test_get_system_timezone_returns_string_or_none():
    result = get_system_timezone()
    assert result is None or (isinstance(result, str) and "/" in result)
