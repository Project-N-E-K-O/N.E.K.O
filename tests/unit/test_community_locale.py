"""Device bootstrap hints stay independent of interface language settings."""

import pytest
from unittest.mock import Mock

from utils import community_locale, language_utils
from tests.fake_clock import patch_module_clock

@pytest.fixture(autouse=True)
def isolate_device_probes(monkeypatch):
    system_language_probe = language_utils._get_system_language
    monkeypatch.setattr(language_utils, "_get_system_language", lambda: "en")
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: None)
    monkeypatch.setattr(language_utils, "_get_macos_locale", lambda: None)
    monkeypatch.setattr(community_locale.locale, "getlocale", lambda: (None, None))
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("NEKO_IS_CHINA_REGION", raising=False)

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
    monkeypatch.setenv("NEKO_IS_CHINA_REGION", "true")
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


@pytest.mark.parametrize("source", ["windows", "macos"])
def test_bootstrap_samples_transient_device_probe_once_and_retries_next_call(
    monkeypatch, isolate_device_probes, source,
):
    monkeypatch.setattr(language_utils, "_get_system_language", isolate_device_probes)
    probe = Mock(side_effect=[None, "es-CH"])
    monkeypatch.setattr(language_utils, f"_get_{source}_locale", probe)

    assert community_locale.community_bootstrap_locale_hints() == {"locale": "en"}
    assert probe.call_count == 1
    assert language_utils._system_locale_probe_snapshot.get() is None
    assert community_locale.community_bootstrap_locale_hints() == {"locale": "es", "region": "CH"}
    assert probe.call_count == 2
    assert language_utils._system_locale_probe_snapshot.get() is None


@pytest.mark.parametrize("override", ["false", "FALSE", "0", "no", "off"])
@pytest.mark.parametrize("device_locale, expected", [
    ("zh-CN", {"locale": "zh-CN"}),
    ("es-ES", {"locale": "es", "region": "ES"}),
])
def test_bootstrap_honors_negative_region_override(
    monkeypatch, isolate_device_probes, override, device_locale, expected,
):
    monkeypatch.setenv("NEKO_IS_CHINA_REGION", override)
    monkeypatch.setattr(language_utils, "_get_system_language", isolate_device_probes)
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: device_locale)
    assert community_locale.community_bootstrap_locale_hints() == expected


@pytest.mark.parametrize("override", ["true", "TRUE", "1", "yes", "on"])
def test_bootstrap_honors_positive_region_override(
    monkeypatch, isolate_device_probes, override,
):
    monkeypatch.setenv("NEKO_IS_CHINA_REGION", override)
    monkeypatch.setattr(language_utils, "_get_system_language", isolate_device_probes)
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: "es-ES")
    assert community_locale.community_bootstrap_locale_hints() == {"locale": "es", "region": "CN"}


@pytest.mark.parametrize("override", [None, "", "invalid"])
def test_bootstrap_region_auto_detection_keeps_china(
    monkeypatch, isolate_device_probes, override,
):
    if override is None:
        monkeypatch.delenv("NEKO_IS_CHINA_REGION", raising=False)
    else:
        monkeypatch.setenv("NEKO_IS_CHINA_REGION", override)
    monkeypatch.setattr(language_utils, "_get_system_language", isolate_device_probes)
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: "zh-CN")
    assert community_locale.community_bootstrap_locale_hints() == {"locale": "zh-CN", "region": "CN"}


def test_bootstrap_device_region_recovers_during_global_probe_backoff(
    monkeypatch, isolate_device_probes,
):
    patch_module_clock(monkeypatch, language_utils, monotonic=lambda: 100.0)
    monkeypatch.delenv("NEKO_IS_CHINA_REGION", raising=False)
    monkeypatch.setattr(language_utils, "_get_system_language", isolate_device_probes)
    monkeypatch.setattr(language_utils, "_global_language_initialized", True)
    monkeypatch.setattr(language_utils, "_global_region_initialized", False)
    monkeypatch.setattr(language_utils, "_global_region", None)
    monkeypatch.setattr(language_utils, "_global_probe_next_retry_monotonic", 0.0)
    monkeypatch.setattr(language_utils, "_global_probe_retry_delay_seconds", 1.0)
    windows = Mock(side_effect=[None, "en-CN"])
    monkeypatch.setattr(language_utils, "_get_windows_locale", windows)

    assert language_utils.get_global_region() == "non-china"
    retry_at = language_utils._global_probe_next_retry_monotonic
    assert retry_at > 100.0
    assert language_utils._global_region_initialized is False
    assert community_locale.community_bootstrap_locale_hints() == {"locale": "en", "region": "CN"}
    assert windows.call_count == 2
    assert language_utils._global_region == "non-china"
    assert language_utils._global_region_initialized is False
    assert language_utils._global_probe_next_retry_monotonic == retry_at


@pytest.mark.parametrize("cached_region, device_locale, expected", [
    ("china", "es-ES", {"locale": "es", "region": "ES"}),
    ("non-china", "en-CN", {"locale": "en", "region": "CN"}),
])
def test_bootstrap_current_device_hints_do_not_read_or_change_global_region(
    monkeypatch, isolate_device_probes, cached_region, device_locale, expected,
):
    monkeypatch.delenv("NEKO_IS_CHINA_REGION", raising=False)
    monkeypatch.setattr(language_utils, "_get_system_language", isolate_device_probes)
    monkeypatch.setattr(language_utils, "_global_region", cached_region)
    monkeypatch.setattr(language_utils, "_global_region_initialized", True)
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: device_locale)

    assert community_locale.community_bootstrap_locale_hints() == expected
    assert language_utils._global_region == cached_region
    assert language_utils._global_region_initialized is True


@pytest.mark.parametrize("device_locale, expected", [
    ("en-u-ca-gregory", {"locale": "en"}),
    ("ja-u-ca-japanese", {"locale": "ja"}),
    ("ja-x-US", {"locale": "ja"}),
    ("ja-JP-u-ca-japanese", {"locale": "ja", "region": "JP"}),
    ("zh-Hant-u-ca-chinese", {"locale": "zh-TW"}),
    ("en-001-u-ca-gregory", {"locale": "en"}),
])
def test_bootstrap_does_not_treat_locale_extensions_as_regions(
    monkeypatch, isolate_device_probes, device_locale, expected,
):
    monkeypatch.setattr(language_utils, "_get_system_language", isolate_device_probes)
    monkeypatch.setenv("LANG", device_locale)
    assert community_locale.community_bootstrap_locale_hints() == expected


@pytest.mark.parametrize("device_locale, expected", [
    ("ca_ES@valencia", {"locale": "en", "region": "ES"}),
    ("ca_ES.UTF-8@valencia", {"locale": "en", "region": "ES"}),
    ("ja_JP@calendar=japanese", {"locale": "ja", "region": "JP"}),
    ("zh_Hant_TW@calendar=roc", {"locale": "zh-TW", "region": "TW"}),
])
def test_bootstrap_region_ignores_posix_modifiers(
    monkeypatch, isolate_device_probes, device_locale, expected,
):
    monkeypatch.setattr(language_utils, "_get_system_language", isolate_device_probes)
    monkeypatch.setenv("LANG", device_locale)
    assert community_locale.community_bootstrap_locale_hints() == expected


@pytest.mark.parametrize("error", [ValueError, TypeError])
@pytest.mark.parametrize("fallback", [None, "ja_JP.UTF-8"])
def test_bootstrap_memoizes_process_locale_errors_for_one_call(
    monkeypatch, isolate_device_probes, error, fallback,
):
    monkeypatch.setattr(language_utils, "_get_system_language", isolate_device_probes)
    probe = Mock(side_effect=[error("temporary locale read failure"), ("es_US", None)])
    monkeypatch.setattr(community_locale.locale, "getlocale", probe)
    if fallback:
        monkeypatch.setenv("LANG", fallback)

    expected = {"locale": "ja", "region": "JP"} if fallback else {"locale": "en"}
    assert community_locale.community_bootstrap_locale_hints() == expected
    assert probe.call_count == 1
    assert language_utils._system_locale_probe_snapshot.get() is None
    assert community_locale.community_bootstrap_locale_hints() == {"locale": "es", "region": "US"}
    assert probe.call_count == 2
    assert language_utils._system_locale_probe_snapshot.get() is None
