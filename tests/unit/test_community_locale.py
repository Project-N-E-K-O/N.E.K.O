"""Device bootstrap hints stay independent of interface language settings."""

import pytest

from utils import community_locale, language_utils


@pytest.fixture(autouse=True)
def isolate_device_probes(monkeypatch):
    system_language_probe = language_utils._get_system_language
    monkeypatch.setattr(language_utils, "_get_system_language", lambda: "en")
    monkeypatch.setattr(language_utils, "is_china_region", lambda: False)
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: None)
    monkeypatch.setattr(language_utils, "_get_macos_locale", lambda: None)
    monkeypatch.setattr(community_locale.locale, "getlocale", lambda: (None, None))
    monkeypatch.delenv("LANG", raising=False)

    def reject_interface_probe():
        pytest.fail("Account initialization must use the device, not the interface language")

    monkeypatch.setattr(language_utils, "get_global_language_full", reject_interface_probe)
    monkeypatch.setattr(language_utils, "_get_steam_language", reject_interface_probe)
    return system_language_probe


@pytest.mark.parametrize("device_language, expected", [
    ("zh-TW", "zh-TW"), ("zh-HK", "zh-TW"), ("zh-MO", "zh-TW"),
    ("zh-Hant", "zh-TW"), ("zh", "zh-CN"), ("zh_CN", "zh-CN"),
    ("ja-JP", "ja"), ("ko-KR", "ko"), ("pt-BR", "pt"),
    ("ru-RU", "ru"), ("es-ES", "es"), ("en-US", "en"), ("fr-FR", "en"),
])
def test_bootstrap_normalizes_device_language(
    monkeypatch, isolate_device_probes, device_language, expected,
):
    monkeypatch.setattr(language_utils, "_get_system_language", isolate_device_probes)
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: device_language)
    assert community_locale.community_bootstrap_locale_hints()["locale"] == expected


def test_spanish_device_is_independent_of_english_interface(monkeypatch, isolate_device_probes):
    monkeypatch.setattr(language_utils, "_get_system_language", isolate_device_probes)
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: "es-ES")
    monkeypatch.setattr(language_utils, "get_global_language_full", lambda: "en")
    assert community_locale.community_bootstrap_locale_hints() == {"locale": "es", "region": "ES"}


@pytest.mark.parametrize("source", ["windows", "macos", "process", "environment"])
def test_macao_bootstrap_preserves_raw_locale(monkeypatch, isolate_device_probes, source):
    monkeypatch.setattr(language_utils, "_get_system_language", isolate_device_probes)
    if source == "windows":
        monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: "zh-MO")
    elif source == "macos":
        monkeypatch.setattr(language_utils, "_get_macos_locale", lambda: "zh-MO")
    elif source == "process":
        monkeypatch.setattr(community_locale.locale, "getlocale", lambda: ("zh_MO", None))
    else:
        monkeypatch.setenv("LANG", "zh_MO.UTF-8")
    assert community_locale.community_bootstrap_locale_hints() == {"locale": "zh-TW", "region": "MO"}


def test_china_region_is_separate_from_language(monkeypatch):
    monkeypatch.setattr(language_utils, "is_china_region", lambda: True)
    assert community_locale.community_bootstrap_locale_hints() == {"locale": "en", "region": "CN"}


@pytest.mark.parametrize("windows, macos, python_locale, env_locale, expected", [
    ("en-JP", "ja-JP", "en_US", "ko_KR.UTF-8", "JP"),
    (None, "zh-Hant-TW", "en_US", "ko_KR.UTF-8", "TW"),
    (None, None, "pt_BR", "ko_KR.UTF-8", "BR"),
    (None, None, None, "ko_KR.UTF-8", "KR"),
    (None, None, "en", "", None),
    (None, None, None, "C.UTF-8", None),
    (None, None, None, "en_001.UTF-8", None),
])
def test_bootstrap_region_source_priority(
    monkeypatch, windows, macos, python_locale, env_locale, expected,
):
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: windows)
    monkeypatch.setattr(language_utils, "_get_macos_locale", lambda: macos)
    monkeypatch.setattr(community_locale.locale, "getlocale", lambda: (python_locale, None))
    monkeypatch.setenv("LANG", env_locale)
    hints = community_locale.community_bootstrap_locale_hints()
    assert hints == ({"locale": "en", "region": expected} if expected else {"locale": "en"})


@pytest.mark.parametrize("error", [ValueError, TypeError])
def test_unavailable_process_locale_omits_region(monkeypatch, error):
    def unavailable():
        raise error("unavailable locale")

    monkeypatch.setattr(community_locale.locale, "getlocale", unavailable)
    assert community_locale.community_bootstrap_locale_hints() == {"locale": "en"}
