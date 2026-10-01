from __future__ import annotations

import math
import warnings

import pytest

import plugin.settings as settings

pytestmark = pytest.mark.plugin_unit


def test_validate_config_rejects_nan_plugin_startup_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PLUGIN_STARTUP_TIMEOUT", math.nan)

    with pytest.raises(ValueError, match="PLUGIN_STARTUP_TIMEOUT"):
        settings.validate_config()


def test_market_defaults_use_https_public_endpoints() -> None:
    assert settings.MARKET_API_URL == "https://market.project-neko.cn"
    assert settings.MARKET_WEB_URL == "https://market.project-neko.cn"
    assert settings.MARKET_ORIGINS == [
        "https://market.project-neko.cn",
        "https://marketplace.project-neko.cn",
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


# Values these names had before they were removed from the module namespace.
# Installed plugins may still import them, so they must keep resolving.
_LEGACY_ALIAS_VALUES = {
    "PLUGIN_CONFIG_ROOT": lambda: settings.BUILTIN_PLUGIN_CONFIG_ROOT,
    "MARKET_URL": lambda: settings.MARKET_API_URL,
    "RESULT_CONSUMER_SLEEP_INTERVAL": lambda: 0.1,
    "PLUGIN_LOG_LEVEL": lambda: "INFO",
    "PLUGIN_LOG_MAX_BYTES": lambda: 5 * 1024 * 1024,
    "PLUGIN_LOG_BACKUP_COUNT": lambda: 10,
    "PLUGIN_LOG_MAX_FILES": lambda: 20,
    "NEKO_LOGURU_LEVEL": lambda: "INFO",
}


def test_legacy_alias_table_matches_the_compat_layer() -> None:
    assert set(settings._DEPRECATED_ALIASES) == set(_LEGACY_ALIAS_VALUES)


@pytest.mark.parametrize("name", sorted(_LEGACY_ALIAS_VALUES))
def test_legacy_alias_keeps_its_value_and_warns_on_access(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.delenv("NEKO_LOGURU_LEVEL", raising=False)

    with pytest.warns(DeprecationWarning, match=f"plugin.settings.{name} is deprecated"):
        value = getattr(settings, name)

    assert value == _LEGACY_ALIAS_VALUES[name]()


def test_legacy_alias_supports_from_import() -> None:
    namespace: dict[str, object] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        exec("from plugin.settings import MARKET_URL, PLUGIN_LOG_LEVEL", namespace)

    assert namespace["MARKET_URL"] == settings.MARKET_API_URL
    assert namespace["PLUGIN_LOG_LEVEL"] == "INFO"


@pytest.mark.parametrize(
    "name",
    [
        # The names that were exported via __all__ before the removal.
        "PLUGIN_CONFIG_ROOT",
        "MARKET_URL",
        "RESULT_CONSUMER_SLEEP_INTERVAL",
        "PLUGIN_LOG_LEVEL",
        "PLUGIN_LOG_MAX_BYTES",
        "PLUGIN_LOG_BACKUP_COUNT",
        "PLUGIN_LOG_MAX_FILES",
    ],
)
def test_legacy_alias_survives_star_import(name: str) -> None:
    namespace: dict[str, object] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        exec("from plugin.settings import *", namespace)

    assert namespace[name] == _LEGACY_ALIAS_VALUES[name]()


def test_neko_loguru_level_alias_still_reads_its_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEKO_LOGURU_LEVEL", "DEBUG")

    with pytest.warns(DeprecationWarning):
        assert settings.NEKO_LOGURU_LEVEL == "DEBUG"


def test_unknown_settings_attribute_still_raises() -> None:
    with pytest.raises(AttributeError, match="NOT_A_REAL_SETTING"):
        getattr(settings, "NOT_A_REAL_SETTING")
