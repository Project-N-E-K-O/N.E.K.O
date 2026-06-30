from __future__ import annotations

import math

import pytest

import plugin.settings as settings

pytestmark = pytest.mark.plugin_unit


def test_validate_config_rejects_nan_plugin_startup_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PLUGIN_STARTUP_TIMEOUT", math.nan)

    with pytest.raises(ValueError, match="PLUGIN_STARTUP_TIMEOUT"):
        settings.validate_config()
