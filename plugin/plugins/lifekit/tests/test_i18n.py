"""LifeKit-owned regression tests for locale resources and locale wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from plugin.plugins.lifekit import LifeKitPlugin
from plugin.plugins.lifekit._api import LOCALE_TO_GEOIP_LANG
from plugin.plugins.lifekit._geocoders import _language
from plugin.plugins.lifekit._i18n import SUPPORTED_LOCALES, I18n

_LOCALES_DIR = Path(__file__).resolve().parents[1] / "locales"


def test_flat_key_containing_dots_is_readable() -> None:
    assert I18n(_LOCALES_DIR).t("plugin.description") != "plugin.description"


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("ja", "場所名が曖昧です"),
        ("ko", "위치 이름이 모호합니다"),
        ("ru", "Название места неоднозначно"),
        ("es", "El nombre del lugar es ambiguo"),
        ("pt", "O nome do local é ambíguo"),
    ],
)
def test_location_clarification_is_localized_for_every_supported_locale(
    locale: str,
    expected: str,
) -> None:
    i18n = I18n(_LOCALES_DIR)
    i18n.set_locale(locale)

    assert i18n.locale == locale
    assert i18n.t("error.location_ambiguous").startswith(expected)


def test_every_supported_locale_has_the_same_recursive_schema() -> None:
    def schema(value: object, path: str = "") -> set[tuple[str, str]]:
        if isinstance(value, dict):
            result: set[tuple[str, str]] = set()
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                result.update(schema(child, child_path))
            return result
        return {(path, "list" if isinstance(value, list) else "scalar")}

    bundles = {
        locale: json.loads(
            (_LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8")
        )
        for locale in SUPPORTED_LOCALES
    }
    expected = schema(bundles["en"])

    assert set(bundles) == set(SUPPORTED_LOCALES)
    assert all(schema(bundle) == expected for bundle in bundles.values())


@pytest.mark.parametrize("locale", ["ja", "ko", "ru", "es", "pt"])
def test_key_user_paths_are_translated_in_every_locale(locale: str) -> None:
    i18n = I18n(_LOCALES_DIR)
    key_paths = (
        "plugin.description",
        "wmo.0",
        "advice.cold",
        "error.no_location",
        "panel.subtitle",
        "quickstart.title",
    )

    for path in key_paths:
        assert i18n.t(path, locale=locale) != i18n.t(path, locale="en")


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("zh-CN", "、"),
        ("zh-TW", "、"),
        ("ja", "、"),
        ("en", ", "),
        ("ko", ", "),
        ("ru", ", "),
        ("es", ", "),
        ("pt", ", "),
    ],
)
def test_nearby_list_separator_is_localized(locale: str, expected: str) -> None:
    assert I18n(_LOCALES_DIR).t("nearby.list_separator", locale=locale) == expected


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
@pytest.mark.parametrize(
    "key",
    ["advice.bring_umbrella", "advice.bring_sunscreen"],
)
def test_travel_reminder_labels_exist_in_every_supported_locale(
    locale: str,
    key: str,
) -> None:
    assert I18n(_LOCALES_DIR).t(key, locale=locale) != key


def test_spanish_rain_forecast_formats_multiple_dates_naturally() -> None:
    assert I18n(_LOCALES_DIR).t(
        "advice.rain_forecast",
        locale="es",
        dates="lunes, martes",
    ) == "📅 Se espera lluvia: lunes, martes"


def test_settings_and_geocoders_expose_every_supported_locale() -> None:
    locale_schema = LifeKitPlugin.Settings.model_json_schema()["properties"]["locale"]

    assert set(locale_schema["enum"]) == {"", *SUPPORTED_LOCALES}
    assert set(LOCALE_TO_GEOIP_LANG) == set(SUPPORTED_LOCALES)
    assert {_language(locale) for locale in SUPPORTED_LOCALES} == {
        "zh",
        "en",
        "ja",
        "ko",
        "ru",
        "es",
        "pt",
    }
