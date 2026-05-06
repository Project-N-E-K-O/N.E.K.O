"""Tests for lifekit plugin geo utilities."""

from __future__ import annotations

from typing import Any

import pytest

from plugin.plugins.lifekit import _api
from plugin.plugins.lifekit._geo import detect_vpn_conflict, get_system_timezone


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
    assert result is None or (isinstance(result, str) and bool(result.strip()))


def test_daily_val_rejects_negative_index() -> None:
    assert _api.daily_val({"temperature": [1, 2, 3]}, "temperature", -1) is None


@pytest.mark.asyncio
async def test_geoip_locate_maps_https_provider_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def json(self) -> dict[str, Any]:
            return {
                "success": True,
                "city": "Shanghai",
                "latitude": 31.2304,
                "longitude": 121.4737,
                "country_code": "CN",
                "timezone": {"id": "Asia/Shanghai"},
            }

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str, **_: object) -> Response:
            assert url == "https://ipwho.is/"
            return Response()

    monkeypatch.setattr(_api, "_http_client", lambda timeout: Client())

    result = await _api.geoip_locate()

    assert result == {
        "city": "Shanghai",
        "lat": 31.2304,
        "lon": 121.4737,
        "country": "CN",
        "ip_timezone": "Asia/Shanghai",
    }
