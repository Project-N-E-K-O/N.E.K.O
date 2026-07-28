from __future__ import annotations

import pytest

from utils import language_utils
from utils.config_manager.migrations import MigrationsMixin
from tests.fake_clock import patch_module_clock


@pytest.fixture(autouse=True)
def reset_probe_state(monkeypatch):
    monkeypatch.delenv("NEKO_LANGUAGE", raising=False)
    monkeypatch.delenv("NEKO_IS_CHINA_REGION", raising=False)
    language_utils.reset_global_language()
    yield
    language_utils.reset_global_language()


def test_neutral_process_locale_is_provisional(monkeypatch):
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: None)
    monkeypatch.setattr(language_utils, "_get_macos_locale", lambda: None)
    monkeypatch.setattr(language_utils.locale, "getlocale", lambda: (None, None))
    monkeypatch.setenv("LANG", "C.UTF-8")

    assert language_utils._detect_system_language() is None
    assert language_utils._get_system_language() == "en"
    assert language_utils._detect_china_region() is None
    assert language_utils._is_china_region() is False


@pytest.mark.parametrize("raw", ["garbage", "javascript", "english-garbage"])
def test_malformed_locale_is_not_a_conclusive_signal(raw):
    assert language_utils._parse_system_language(raw) is None
    assert language_utils._system_language_signal(raw) is None
    assert language_utils._locale_region_signal(raw) is None


def test_windows_user_locale_is_a_conclusive_region_signal(monkeypatch):
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: "zh-CN")
    monkeypatch.setattr(
        language_utils,
        "_get_macos_locale",
        lambda: pytest.fail("Windows verdict should win"),
    )

    assert language_utils._detect_china_region() is True


def test_valid_unsupported_os_locale_conclusively_falls_back_to_english(monkeypatch):
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: "fr-FR")
    monkeypatch.setattr(language_utils, "_get_macos_locale", lambda: None)
    monkeypatch.setattr(language_utils.locale, "getlocale", lambda: (None, None))
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)

    assert language_utils._detect_system_language() == "en"
    assert language_utils.initialize_global_language() == "en"
    assert language_utils._global_language_initialized is True
    assert language_utils._global_region_initialized is True
    assert language_utils._global_probe_next_retry_monotonic == 0.0


def test_provisional_fallback_retries_after_cooldown_and_recovers(monkeypatch):
    now = [100.0]
    patch_module_clock(monkeypatch, language_utils, monotonic=lambda: now[0])
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)

    region_results = iter((None, True))
    language_results = iter((None, "zh-TW"))
    region_calls: list[int] = []
    language_calls: list[int] = []

    def detect_region():
        region_calls.append(1)
        return next(region_results)

    def detect_language():
        language_calls.append(1)
        return next(language_results)

    monkeypatch.setattr(language_utils, "_detect_china_region", detect_region)
    monkeypatch.setattr(language_utils, "_detect_system_language", detect_language)

    assert language_utils.get_global_language() == "en"
    assert language_utils.get_global_language_full() == "en"
    assert language_utils.get_global_region() == "non-china"
    assert len(region_calls) == len(language_calls) == 1
    assert language_utils._global_language_initialized is False
    assert language_utils._global_region_initialized is False
    assert language_utils._global_probe_next_retry_monotonic == 105.0

    now[0] = 104.999
    assert language_utils.get_global_language() == "en"
    assert language_utils.get_global_region() == "non-china"
    assert len(region_calls) == len(language_calls) == 1

    now[0] = 105.0
    assert language_utils.get_global_language() == "zh"
    assert language_utils.get_global_language_full() == "zh-TW"
    assert language_utils.get_global_region() == "china"
    assert len(region_calls) == len(language_calls) == 2
    assert language_utils._global_language_initialized is True
    assert language_utils._global_region_initialized is True
    assert language_utils._global_probe_next_retry_monotonic == 0.0


def test_probe_retry_uses_bounded_exponential_backoff(monkeypatch):
    now = [0.0]
    patch_module_clock(monkeypatch, language_utils, monotonic=lambda: now[0])
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)
    monkeypatch.setattr(language_utils, "_detect_china_region", lambda: None)
    monkeypatch.setattr(language_utils, "_detect_system_language", lambda: None)

    for expected_delay in (5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 300.0, 300.0):
        language_utils.initialize_global_language()
        deadline = language_utils._global_probe_next_retry_monotonic
        assert deadline - now[0] == expected_delay
        now[0] = deadline


def test_retry_cooldown_starts_after_slow_probe_finishes(monkeypatch):
    stamps = iter((100.0, 104.0))
    patch_module_clock(monkeypatch, language_utils, monotonic=lambda: next(stamps))
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)
    monkeypatch.setattr(language_utils, "_detect_china_region", lambda: None)
    monkeypatch.setattr(language_utils, "_detect_system_language", lambda: None)

    assert language_utils.initialize_global_language() == "en"
    assert language_utils._global_probe_next_retry_monotonic == 109.0


def test_conclusive_steam_and_os_signals_are_not_reprobed(monkeypatch):
    steam_calls: list[int] = []
    region_calls: list[int] = []

    def steam_language():
        steam_calls.append(1)
        return "english"

    def detect_region():
        region_calls.append(1)
        return False

    monkeypatch.setattr(language_utils, "_get_steam_language", steam_language)
    monkeypatch.setattr(language_utils, "_detect_china_region", detect_region)
    monkeypatch.setattr(
        language_utils,
        "_detect_system_language",
        lambda: pytest.fail("system language must not run after a Steam verdict"),
    )

    assert language_utils.get_global_language() == "en"
    assert language_utils.get_global_language_full() == "en"
    assert language_utils.get_global_region() == "non-china"
    assert len(steam_calls) == len(region_calls) == 1

    language_utils.reset_global_language()
    steam_calls.clear()
    region_calls.clear()
    system_calls: list[int] = []

    def no_steam():
        steam_calls.append(1)
        return None

    def system_language():
        system_calls.append(1)
        return "ja"

    monkeypatch.setattr(language_utils, "_get_steam_language", no_steam)
    monkeypatch.setattr(language_utils, "_detect_system_language", system_language)

    assert language_utils.get_global_language() == "ja"
    assert language_utils.get_global_language_full() == "ja"
    assert language_utils.get_global_region() == "non-china"
    assert len(steam_calls) == len(system_calls) == len(region_calls) == 1


def test_set_refresh_and_reset_keep_probe_confidence_consistent(monkeypatch):
    now = [20.0]
    patch_module_clock(monkeypatch, language_utils, monotonic=lambda: now[0])
    monkeypatch.setattr(language_utils, "_detect_china_region", lambda: None)
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)
    monkeypatch.setattr(language_utils, "_detect_system_language", lambda: None)

    assert language_utils.initialize_global_language() == "en"
    assert language_utils._global_probe_next_retry_monotonic == 25.0

    language_utils.set_global_language("ja")
    assert language_utils._global_language_initialized is True
    assert language_utils._global_region_initialized is False
    assert language_utils._global_probe_next_retry_monotonic == 0.0

    monkeypatch.setattr(language_utils, "_detect_china_region", lambda: True)
    assert language_utils.refresh_global_language("schinese") is True
    assert language_utils.get_global_language() == "zh"
    assert language_utils.get_global_region() == "china"
    assert language_utils._global_probe_next_retry_monotonic == 0.0

    language_utils.reset_global_language()
    assert language_utils._global_language is None
    assert language_utils._global_region is None
    assert language_utils._global_language_initialized is False
    assert language_utils._global_region_initialized is False
    assert language_utils._global_probe_next_retry_monotonic == 0.0


def test_language_refresh_does_not_postpone_region_retry(monkeypatch):
    now = [30.0]
    patch_module_clock(monkeypatch, language_utils, monotonic=lambda: now[0])
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)
    monkeypatch.setattr(language_utils, "_detect_system_language", lambda: None)
    region_calls: list[int] = []

    def no_region():
        region_calls.append(1)
        return None

    monkeypatch.setattr(language_utils, "_detect_china_region", no_region)
    assert language_utils.initialize_global_language() == "en"
    assert language_utils._global_probe_next_retry_monotonic == 35.0

    now[0] = 31.0
    assert language_utils.refresh_global_language("ja") is True
    assert language_utils._global_probe_next_retry_monotonic == 35.0
    assert len(region_calls) == 1


def test_same_language_refresh_is_no_change_while_region_stays_provisional(
    monkeypatch,
):
    patch_module_clock(monkeypatch, language_utils, monotonic=lambda: 10.0)
    monkeypatch.setattr(language_utils, "_detect_china_region", lambda: None)
    language_utils.set_global_language("ja")

    assert language_utils.refresh_global_language("ja") is False
    assert language_utils._global_language_initialized is True
    assert language_utils._global_region_initialized is False


def test_config_migration_does_not_persist_a_provisional_english_fallback(
    monkeypatch,
    tmp_path,
):
    characters_dir = tmp_path / "characters"
    characters_dir.mkdir()
    english_source = characters_dir / "en.json"
    english_source.write_text("{}", encoding="utf-8")

    class Manager(MigrationsMixin):
        project_config_dir = tmp_path

        @staticmethod
        def _log(_message):
            return None

    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)
    monkeypatch.setattr(language_utils, "_detect_system_language", lambda: None)
    assert Manager()._get_localized_characters_source() is None

    monkeypatch.setattr(language_utils, "_detect_system_language", lambda: "en")
    assert Manager()._get_localized_characters_source() == english_source
