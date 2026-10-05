"""Regional defaults are hints; logged-in recommendation preferences live in Verse."""

from unittest.mock import patch

import pytest

from utils.community_locale import community_bootstrap_locale_hints, community_locale_hints


def test_china_region_is_sent_separately_from_english_ui():
    with patch("utils.language_utils.get_global_language_full", return_value="en"), patch(
        "utils.language_utils.is_china_region", return_value=True
    ):
        assert community_locale_hints() == {"locale": "en", "region": "CN"}


def test_traditional_chinese_and_japan_territory_are_preserved():
    with patch("utils.language_utils.get_global_language_full", return_value="zh-TW"), patch(
        "utils.language_utils.is_china_region", return_value=False
    ), patch("utils.language_utils._get_windows_locale", return_value=None), patch(
        "utils.language_utils._get_macos_locale", return_value=None
    ), patch("utils.community_locale.locale.getlocale", return_value=("en_JP", "UTF-8")):
        assert community_locale_hints() == {"locale": "zh-TW", "region": "JP"}


def test_windows_device_territory_works_even_when_python_locale_is_unset():
    with patch("utils.language_utils.get_global_language_full", return_value="en"), patch(
        "utils.language_utils.is_china_region", return_value=False
    ), patch("utils.language_utils._get_windows_locale", return_value="en-JP"), patch(
        "utils.community_locale.locale.getlocale", return_value=(None, None)
    ):
        assert community_locale_hints() == {"locale": "en", "region": "JP"}


def test_bootstrap_uses_spanish_device_while_anonymous_recommendations_use_english_ui():
    with patch("utils.language_utils.get_global_language_full", return_value="en"), patch(
        "utils.language_utils.is_china_region", return_value=False
    ), patch("utils.language_utils._get_windows_locale", return_value="es-ES"):
        assert community_bootstrap_locale_hints() == {"locale": "es", "region": "ES"}
        assert community_locale_hints() == {"locale": "en", "region": "ES"}


@pytest.mark.parametrize("device_language, expected", [
    ("zh-TW", "zh-TW"), ("zh-HK", "zh-TW"), ("zh-Hant", "zh-TW"),
    ("zh-CN", "zh-CN"), ("ja-JP", "ja"), ("ko-KR", "ko"),
    ("pt-BR", "pt"), ("ru-RU", "ru"), ("es-ES", "es"),
    ("en-US", "en"), ("fr-FR", "en"),
])
def test_bootstrap_language_mapping_does_not_read_interface_language(device_language, expected):
    with patch("utils.language_utils._get_system_language", return_value=device_language), patch(
        "utils.language_utils.get_global_language_full", side_effect=AssertionError("UI language must not initialize the account")
    ), patch("utils.language_utils.is_china_region", return_value=True):
        assert community_bootstrap_locale_hints() == {"locale": expected, "region": "CN"}
