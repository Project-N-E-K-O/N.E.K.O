from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

import pytest

from utils import language_utils
from tests.fake_clock import patch_module_clock


def _probe_result(value, *, conclusive=None):
    if conclusive is None:
        conclusive = value is not None
    return language_utils._LocaleProbeResult(value, conclusive)


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


@pytest.mark.parametrize(
    "raw",
    [
        "garbage",
        "javascript",
        "english-garbage",
        "notjapanese_Japan",
        "Japanese_",
        "_Japan",
        "Japanese_Japan_extra",
        "Chinese (Unknown)_China",
    ],
)
def test_malformed_locale_is_not_a_conclusive_signal(raw):
    assert language_utils._parse_system_language(raw) is None
    assert language_utils._system_language_signal(raw) is None
    assert language_utils._locale_region_signal(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected", "expected_region"),
    [
        ("Japanese_Japan", "ja", False),
        ("Korean_Korea", "ko", False),
        ("Russian_Russia", "ru", False),
        ("Spanish_Spain", "es", False),
        ("Portuguese_Brazil", "pt", False),
        ("English_United States", "en", False),
        ("Chinese (Simplified)_China", "zh", True),
        ("Chinese (Traditional)_Taiwan", "zh-TW", False),
        ("Chinese_PR China.936", "zh", True),
        ("Chinese (Simplified)_PR-China", "zh", True),
        ("Chinese_CHN", "zh", True),
        ("Chinese_CN", "zh", True),
        ("Chinese_Taiwan", "zh-TW", False),
        ("Chinese_Hong Kong", "zh-TW", False),
        ("Chinese_Macao", "zh-TW", False),
        ("Chinese_Singapore", "zh", False),
        ("Chinese-Simplified", "zh", True),
        ("Chinese-Traditional", "zh-TW", False),
        ("Chinese-HongKong", "zh-TW", False),
        ("Chinese-Singapore", "zh", False),
        ("Japanese_Japan.932", "ja", False),
        ("Portuguese_Brazil@latin", "pt", False),
    ],
)
def test_legacy_python_locale_names_are_conclusive(
    monkeypatch,
    raw,
    expected,
    expected_region,
):
    monkeypatch.setattr(language_utils.platform, "system", lambda: "Linux")
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: None)
    monkeypatch.setattr(language_utils, "_get_macos_locale", lambda: None)
    monkeypatch.setattr(language_utils.locale, "getlocale", lambda: (raw, None))
    monkeypatch.setenv("LANG", "C.UTF-8")

    assert language_utils._parse_system_language(raw) == expected
    assert language_utils._system_language_signal(raw) == expected
    assert language_utils._locale_region_signal(raw) is expected_region
    assert language_utils._detect_system_language() == expected


def test_windows_user_locale_is_a_conclusive_region_signal(monkeypatch):
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: "zh-CN")
    monkeypatch.setattr(
        language_utils,
        "_get_macos_locale",
        lambda: pytest.fail("Windows verdict should win"),
    )

    assert language_utils._detect_china_region() is True


def test_unknown_legacy_chinese_territory_keeps_region_provisional():
    assert language_utils._parse_system_language("Chinese_Unknown") == "zh"
    assert language_utils._system_language_signal("Chinese_Unknown") == "zh"
    assert language_utils._locale_region_signal("Chinese_Unknown") is None
    assert language_utils._locale_region_signal("Chinese") is None


@pytest.mark.parametrize(
    ("platform_name", "windows_locale", "macos_locale"),
    [
        ("Windows", "Chinese_Unknown", None),
        ("Darwin", None, "Chinese_Unknown"),
    ],
)
def test_authoritative_language_only_signal_keeps_region_provisional(
    monkeypatch,
    platform_name,
    windows_locale,
    macos_locale,
):
    monkeypatch.setattr(language_utils.platform, "system", lambda: platform_name)
    monkeypatch.setattr(
        language_utils,
        "_get_windows_locale",
        lambda: windows_locale,
    )
    monkeypatch.setattr(
        language_utils,
        "_get_macos_locale",
        lambda: macos_locale,
    )
    monkeypatch.setattr(
        language_utils.locale,
        "getlocale",
        lambda: ("en_US", "UTF-8"),
    )
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)

    language_probe = language_utils._probe_system_language()
    region_probe = language_utils._probe_china_region()

    assert language_probe == _probe_result("zh")
    assert region_probe == _probe_result(False, conclusive=False)

    assert language_utils.initialize_global_language() == "zh"
    assert language_utils.get_global_region() == "non-china"
    assert language_utils._global_language_initialized is True
    assert language_utils._global_region_initialized is False


@pytest.mark.parametrize(
    ("platform_name", "windows_locale", "macos_locale"),
    [
        ("Windows", "global", None),
        ("Darwin", None, "global"),
    ],
)
def test_authoritative_region_only_signal_keeps_language_provisional(
    monkeypatch,
    platform_name,
    windows_locale,
    macos_locale,
):
    monkeypatch.setattr(language_utils.platform, "system", lambda: platform_name)
    monkeypatch.setattr(
        language_utils,
        "_get_windows_locale",
        lambda: windows_locale,
    )
    monkeypatch.setattr(
        language_utils,
        "_get_macos_locale",
        lambda: macos_locale,
    )
    monkeypatch.setattr(
        language_utils.locale,
        "getlocale",
        lambda: ("ja_JP", "UTF-8"),
    )
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)

    language_probe = language_utils._probe_system_language()
    region_probe = language_utils._probe_china_region()

    assert language_probe == _probe_result("ja", conclusive=False)
    assert region_probe == _probe_result(False)

    assert language_utils.initialize_global_language() == "ja"
    assert language_utils.get_global_region() == "non-china"
    assert language_utils._global_language_initialized is False
    assert language_utils._global_region_initialized is True


def test_valid_unsupported_os_locale_conclusively_falls_back_to_english(monkeypatch):
    monkeypatch.setattr(language_utils.platform, "system", lambda: "Windows")
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
        return _probe_result(next(region_results))

    def detect_language():
        language_calls.append(1)
        return _probe_result(next(language_results))

    monkeypatch.setattr(language_utils, "_probe_china_region", detect_region)
    monkeypatch.setattr(language_utils, "_probe_system_language", detect_language)

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


def test_one_retry_generation_shares_transient_macos_failure(monkeypatch):
    now = [100.0]
    available = [False]
    calls: list[str] = []
    patch_module_clock(monkeypatch, language_utils, monotonic=lambda: now[0])
    monkeypatch.setattr(
        language_utils,
        "_macos_locale_cache",
        language_utils._MACOS_LOCALE_UNSET,
    )
    monkeypatch.setattr(language_utils.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: None)
    # A lower-priority process locale can be valid but stale. It may provide
    # this attempt's best-effort value, but it must not hide the failed
    # authoritative macOS probe and become a permanent cache entry.
    monkeypatch.setattr(
        language_utils.locale,
        "getlocale",
        lambda: ("en_US", "UTF-8"),
    )
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)

    def fake_run(command, **_kwargs):
        calls.append(command[-1])
        if not available[0]:
            raise language_utils.subprocess.TimeoutExpired(command, 1.0)
        return SimpleNamespace(returncode=0, stdout='"zh_CN"\n')

    monkeypatch.setattr(language_utils.subprocess, "run", fake_run)

    assert language_utils.get_global_language() == "en"
    assert language_utils.get_global_region() == "non-china"
    assert calls == ["AppleLocale", "AppleLanguages"]
    assert language_utils._global_language_initialized is False
    assert language_utils._global_region_initialized is False

    now[0] = 104.999
    assert language_utils.get_global_region() == "non-china"
    assert calls == ["AppleLocale", "AppleLanguages"]

    available[0] = True
    now[0] = 105.0
    assert language_utils.get_global_language_full() == "zh-CN"
    assert language_utils.get_global_region() == "china"
    assert calls == ["AppleLocale", "AppleLanguages", "AppleLocale"]
    assert language_utils._global_language_initialized is True
    assert language_utils._global_region_initialized is True


def test_windows_api_failure_with_valid_lower_locale_stays_provisional(
    monkeypatch,
):
    now = [200.0]
    available = [False]
    windows_calls: list[int] = []
    patch_module_clock(monkeypatch, language_utils, monotonic=lambda: now[0])
    monkeypatch.setattr(language_utils.platform, "system", lambda: "Windows")
    monkeypatch.setattr(language_utils, "_get_macos_locale", lambda: None)
    monkeypatch.setattr(
        language_utils.locale,
        "getlocale",
        lambda: ("en_US", "UTF-8"),
    )
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)

    def windows_locale():
        windows_calls.append(1)
        return "zh-CN" if available[0] else None

    monkeypatch.setattr(language_utils, "_get_windows_locale", windows_locale)

    assert language_utils.get_global_language() == "en"
    assert language_utils.get_global_region() == "non-china"
    assert language_utils._global_language_initialized is False
    assert language_utils._global_region_initialized is False
    assert language_utils._global_probe_next_retry_monotonic == 205.0
    assert len(windows_calls) == 1

    now[0] = 204.999
    assert language_utils.get_global_language() == "en"
    assert len(windows_calls) == 1

    available[0] = True
    now[0] = 205.0
    assert language_utils.get_global_language_full() == "zh-CN"
    assert language_utils.get_global_region() == "china"
    assert language_utils._global_language_initialized is True
    assert language_utils._global_region_initialized is True
    assert len(windows_calls) == 2


@pytest.mark.parametrize("invalid_apple_locale", ["C", "garbage"])
def test_invalid_apple_locale_falls_through_to_apple_languages(
    monkeypatch,
    invalid_apple_locale,
):
    calls: list[str] = []
    monkeypatch.setattr(
        language_utils,
        "_macos_locale_cache",
        language_utils._MACOS_LOCALE_UNSET,
    )
    monkeypatch.setattr(language_utils.platform, "system", lambda: "Darwin")

    def fake_run(command, **_kwargs):
        key = command[-1]
        calls.append(key)
        if key == "AppleLocale":
            return SimpleNamespace(
                returncode=0,
                stdout=f'"{invalid_apple_locale}"\n',
            )
        return SimpleNamespace(
            returncode=0,
            stdout='(\n    "zh-Hans-CN"\n)\n',
        )

    monkeypatch.setattr(language_utils.subprocess, "run", fake_run)

    assert language_utils._get_macos_locale() == "zh-Hans-CN"
    assert language_utils._get_macos_locale() == "zh-Hans-CN"
    assert calls == ["AppleLocale", "AppleLanguages"]


def test_probe_retry_uses_bounded_exponential_backoff(monkeypatch):
    now = [0.0]
    patch_module_clock(monkeypatch, language_utils, monotonic=lambda: now[0])
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)
    monkeypatch.setattr(
        language_utils,
        "_probe_china_region",
        lambda: _probe_result(None),
    )
    monkeypatch.setattr(
        language_utils,
        "_probe_system_language",
        lambda: _probe_result(None),
    )

    for expected_delay in (5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 300.0, 300.0):
        language_utils.initialize_global_language()
        deadline = language_utils._global_probe_next_retry_monotonic
        assert deadline - now[0] == expected_delay
        now[0] = deadline


def test_only_provisional_region_is_reprobed_after_cooldown(monkeypatch):
    now = [100.0]
    patch_module_clock(monkeypatch, language_utils, monotonic=lambda: now[0])
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: "japanese")
    monkeypatch.setattr(
        language_utils,
        "_probe_system_language",
        lambda: pytest.fail("a conclusive Steam language must not be reprobed"),
    )
    region_results = iter((None, True))
    region_calls: list[int] = []

    def detect_region():
        region_calls.append(1)
        return _probe_result(next(region_results))

    monkeypatch.setattr(language_utils, "_probe_china_region", detect_region)

    assert language_utils.get_global_language() == "ja"
    assert language_utils.get_global_region() == "non-china"
    assert language_utils._global_language_initialized is True
    assert language_utils._global_region_initialized is False
    assert language_utils._global_probe_next_retry_monotonic == 105.0

    now[0] = 105.0
    assert language_utils.get_global_region() == "china"
    assert language_utils.get_global_language() == "ja"
    assert language_utils._global_region_initialized is True
    assert len(region_calls) == 2


def test_only_provisional_language_is_reprobed_after_cooldown(monkeypatch):
    now = [200.0]
    patch_module_clock(monkeypatch, language_utils, monotonic=lambda: now[0])
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)
    language_results = iter((None, "zh-TW"))
    language_calls: list[int] = []
    region_calls: list[int] = []

    def detect_language():
        language_calls.append(1)
        return _probe_result(next(language_results))

    def detect_region():
        region_calls.append(1)
        return _probe_result(False)

    monkeypatch.setattr(language_utils, "_probe_system_language", detect_language)
    monkeypatch.setattr(language_utils, "_probe_china_region", detect_region)

    assert language_utils.get_global_language() == "en"
    assert language_utils.get_global_region() == "non-china"
    assert language_utils._global_language_initialized is False
    assert language_utils._global_region_initialized is True
    assert language_utils._global_probe_next_retry_monotonic == 205.0

    now[0] = 205.0
    assert language_utils.get_global_language_full() == "zh-TW"
    assert language_utils.get_global_region() == "non-china"
    assert language_utils._global_language_initialized is True
    assert len(language_calls) == 2
    assert len(region_calls) == 1


def test_retry_cooldown_starts_after_slow_probe_finishes(monkeypatch):
    stamps = [100.0, 104.0]

    def monotonic():
        return stamps.pop(0) if len(stamps) > 1 else stamps[0]

    patch_module_clock(monkeypatch, language_utils, monotonic=monotonic)
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)
    monkeypatch.setattr(
        language_utils,
        "_probe_china_region",
        lambda: _probe_result(None),
    )
    monkeypatch.setattr(
        language_utils,
        "_probe_system_language",
        lambda: _probe_result(None),
    )

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
        return _probe_result(False)

    monkeypatch.setattr(language_utils, "_get_steam_language", steam_language)
    monkeypatch.setattr(language_utils, "_probe_china_region", detect_region)
    monkeypatch.setattr(
        language_utils,
        "_probe_system_language",
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
        return _probe_result("ja")

    monkeypatch.setattr(language_utils, "_get_steam_language", no_steam)
    monkeypatch.setattr(language_utils, "_probe_system_language", system_language)

    assert language_utils.get_global_language() == "ja"
    assert language_utils.get_global_language_full() == "ja"
    assert language_utils.get_global_region() == "non-china"
    assert len(steam_calls) == len(system_calls) == len(region_calls) == 1


def test_concurrent_mixed_getters_share_one_initialization_attempt(monkeypatch):
    start = threading.Event()
    region_calls: list[int] = []
    language_calls: list[int] = []

    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)

    def detect_region():
        region_calls.append(1)
        return _probe_result(True)

    def detect_language():
        language_calls.append(1)
        return _probe_result("zh-TW")

    monkeypatch.setattr(language_utils, "_probe_china_region", detect_region)
    monkeypatch.setattr(language_utils, "_probe_system_language", detect_language)

    calls = (
        [(language_utils.get_global_language, "zh")] * 16
        + [(language_utils.get_global_language_full, "zh-TW")] * 16
        + [(language_utils.get_global_region, "china")] * 16
    )

    def run(getter, expected):
        assert start.wait(timeout=5)
        return getter(), expected

    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        futures = [
            executor.submit(run, getter, expected)
            for getter, expected in calls
        ]
        start.set()
        results = [future.result(timeout=5) for future in futures]

    assert all(actual == expected for actual, expected in results)
    assert len(region_calls) == 1
    assert len(language_calls) == 1


def test_set_refresh_and_reset_keep_probe_confidence_consistent(monkeypatch):
    now = [20.0]
    patch_module_clock(monkeypatch, language_utils, monotonic=lambda: now[0])
    monkeypatch.setattr(
        language_utils,
        "_probe_china_region",
        lambda: _probe_result(None),
    )
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: None)
    monkeypatch.setattr(
        language_utils,
        "_probe_system_language",
        lambda: _probe_result(None),
    )

    assert language_utils.initialize_global_language() == "en"
    assert language_utils._global_probe_next_retry_monotonic == 25.0

    language_utils.set_global_language("ja")
    assert language_utils._global_language_initialized is True
    assert language_utils._global_region_initialized is False
    assert language_utils._global_probe_next_retry_monotonic == 0.0

    monkeypatch.setattr(
        language_utils,
        "_probe_china_region",
        lambda: _probe_result(True),
    )
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
    monkeypatch.setattr(
        language_utils,
        "_probe_system_language",
        lambda: _probe_result(None),
    )
    region_calls: list[int] = []

    def no_region():
        region_calls.append(1)
        return _probe_result(None)

    monkeypatch.setattr(language_utils, "_probe_china_region", no_region)
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
    monkeypatch.setattr(
        language_utils,
        "_probe_china_region",
        lambda: _probe_result(None),
    )
    language_utils.set_global_language("ja")

    assert language_utils.refresh_global_language("ja") is False
    assert language_utils._global_language_initialized is True
    assert language_utils._global_region_initialized is False


def test_same_language_refresh_reports_region_recovery(monkeypatch):
    now = [10.0]
    patch_module_clock(monkeypatch, language_utils, monotonic=lambda: now[0])
    region_results = iter((None, True))
    monkeypatch.setattr(
        language_utils,
        "_probe_china_region",
        lambda: _probe_result(next(region_results)),
    )
    language_utils.set_global_language("ja")

    assert language_utils.refresh_global_language("ja") is False
    assert language_utils._global_region_initialized is False
    assert language_utils._global_probe_next_retry_monotonic == 15.0

    now[0] = 15.0
    assert language_utils.refresh_global_language("ja") is True
    assert language_utils.get_global_language() == "ja"
    assert language_utils.get_global_region() == "china"
    assert language_utils._global_region_initialized is True
