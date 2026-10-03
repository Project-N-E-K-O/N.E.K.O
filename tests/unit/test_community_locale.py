"""Regional defaults are hints; logged-in recommendation preferences live in Verse."""

from unittest.mock import patch

from utils.community_locale import community_locale_hints


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
