from __future__ import annotations

import math

import pytest

import plugin.settings as settings

pytestmark = pytest.mark.plugin_unit


def test_validate_config_rejects_nan_plugin_startup_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PLUGIN_STARTUP_TIMEOUT", math.nan)

    with pytest.raises(ValueError, match="PLUGIN_STARTUP_TIMEOUT"):
        settings.validate_config()


def test_market_defaults_use_current_public_http_endpoints() -> None:
    assert settings.MARKET_API_URL == "http://market.project-neko.cn"
    assert settings.MARKET_WEB_URL == "http://market.project-neko.cn"
    assert settings.MARKET_ORIGINS == [
        "http://market.project-neko.cn",
        "http://marketplace.project-neko.cn",
    ]


def test_market_origin_validation_allows_http_for_loopback_and_official_market() -> None:
    assert settings._validate_market_origin("http://localhost:5173") == "http://localhost:5173"
    assert settings._validate_market_origin("http://127.0.0.1:48916") == "http://127.0.0.1:48916"
    assert settings._validate_market_origin("http://market.project-neko.cn") == "http://market.project-neko.cn"
    assert (
        settings._validate_market_origin("http://marketplace.project-neko.cn")
        == "http://marketplace.project-neko.cn"
    )

    with pytest.raises(ValueError, match="official Market hosts"):
        settings._validate_market_origin("http://example.com")
