from __future__ import annotations

from types import SimpleNamespace

import pytest

from config.prompts.prompts_memory import (
    get_fact_extraction_ai_aware_prompt,
    get_fact_extraction_prompt,
)
from utils import language_utils


def test_macos_locale_reads_apple_locale(monkeypatch):
    # _get_macos_locale has process-level caching; without clearing it, this case reads another test's cached value
    # (None on non-macOS CI), and assertions never see fake_run's result.
    monkeypatch.setattr(
        language_utils, "_macos_locale_cache", language_utils._MACOS_LOCALE_UNSET
    )
    monkeypatch.setattr(language_utils.platform, "system", lambda: "Darwin")

    def fake_run(command, **_kwargs):
        assert command == ["/usr/bin/defaults", "read", "-g", "AppleLocale"]
        return SimpleNamespace(returncode=0, stdout='"zh_CN@calendar=gregorian"\n')

    monkeypatch.setattr(language_utils.subprocess, "run", fake_run)

    assert language_utils._get_macos_locale() == "zh_CN"


def test_macos_locale_is_read_once_per_process(monkeypatch):
    """Repeated lookups must not respawn `defaults`."""
    # initialize_global_language() calls through _is_china_region and _get_system_language
    # once each; every cache miss is a subprocess with a 1s timeout, while initialization holds
    # _global_language_lock. Both global getters are reachable from async request paths,
    # so several extra cold-start seconds block the event loop.
    monkeypatch.setattr(
        language_utils, "_macos_locale_cache", language_utils._MACOS_LOCALE_UNSET
    )
    monkeypatch.setattr(language_utils.platform, "system", lambda: "Darwin")
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='"ja_JP"\n')

    monkeypatch.setattr(language_utils.subprocess, "run", fake_run)

    assert language_utils._get_macos_locale() == "ja_JP"
    assert language_utils._get_macos_locale() == "ja_JP"
    assert language_utils._get_macos_locale() == "ja_JP"
    assert len(calls) == 1, f"defaults 被重复调用了 {len(calls)} 次"


def test_macos_locale_probe_failure_is_retried_not_cached(monkeypatch):
    """A transient `defaults` failure must not pin the process to a wrong locale."""
    # Cache only definitive results. Caching failed probes (timeout / nonzero exit / empty output) would let one
    # startup timeout lose the system locale for the process lifetime, falling back to
    # non-China region and English precisely when this function must compensate for unreliable signals.
    monkeypatch.setattr(
        language_utils, "_macos_locale_cache", language_utils._MACOS_LOCALE_UNSET
    )
    monkeypatch.setattr(language_utils.platform, "system", lambda: "Darwin")
    attempts: list[str] = []

    def flaky_run(command, **_kwargs):
        attempts.append(command[-1])
        if len(attempts) <= 2:
            # Both initial probes (AppleLocale + AppleLanguages) time out -> this attempt fails overall.
            raise language_utils.subprocess.TimeoutExpired(command, 1.0)
        return SimpleNamespace(returncode=0, stdout='"zh_CN"\n')

    monkeypatch.setattr(language_utils.subprocess, "run", flaky_run)

    assert language_utils._get_macos_locale() is None
    # Failure was not cached, so the next call probes again and obtains the real locale.
    assert language_utils._get_macos_locale() == "zh_CN"


def test_macos_locale_non_darwin_is_cached(monkeypatch):
    """"Not macOS" is conclusive, so it is cached and never re-probed."""
    monkeypatch.setattr(
        language_utils, "_macos_locale_cache", language_utils._MACOS_LOCALE_UNSET
    )
    systems: list[int] = []

    def counted_system():
        systems.append(1)
        return "Windows"

    monkeypatch.setattr(language_utils.platform, "system", counted_system)

    assert language_utils._get_macos_locale() is None
    assert language_utils._get_macos_locale() is None
    assert language_utils._get_macos_locale() is None
    assert len(systems) == 1, f"platform.system() 被重复调用了 {len(systems)} 次"


def test_system_language_uses_macos_locale_before_neutral_process_locale(monkeypatch):
    monkeypatch.setattr(language_utils, "_get_windows_locale", lambda: None)
    monkeypatch.setattr(language_utils, "_get_macos_locale", lambda: "zh_Hant_TW")
    monkeypatch.setattr(language_utils.locale, "getlocale", lambda: (None, None))
    monkeypatch.setenv("LANG", "C.UTF-8")

    assert language_utils._get_system_language() == "zh-TW"


def test_global_language_still_prefers_steam_over_system_locale(monkeypatch):
    monkeypatch.setattr(language_utils, "_global_language", None)
    monkeypatch.setattr(language_utils, "_global_language_full", None)
    monkeypatch.setattr(language_utils, "_global_region", None)
    monkeypatch.setattr(language_utils, "_global_language_initialized", False)
    monkeypatch.setattr(language_utils, "_global_region_initialized", False)
    monkeypatch.setattr(language_utils, "_global_probe_next_retry_monotonic", 0.0)
    monkeypatch.setattr(
        language_utils,
        "_probe_china_region",
        lambda: language_utils._LocaleProbeResult(True, True),
    )
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: "japanese")
    monkeypatch.setattr(
        language_utils,
        "_probe_system_language",
        lambda: language_utils._LocaleProbeResult("zh", True),
    )

    assert language_utils.initialize_global_language() == "ja"
    assert language_utils.get_global_language_full() == "ja"


@pytest.mark.parametrize(
    "locale",
    ["zh-CN", "zh-TW", "en-US", "ja-JP", "ko-KR", "ru-RU", "es-ES", "pt-BR"],
)
def test_fact_extraction_prompts_resolve_per_locale(locale):
    for getter in (get_fact_extraction_prompt, get_fact_extraction_ai_aware_prompt):
        prompt = getter(locale)
        assert '"text"' in prompt
        assert "======以上为对话======" in prompt


def test_zh_tw_fact_extraction_uses_its_own_template():
    """zh-TW now has dedicated fact templates (issue #2500, batch 1).

    This assertion used to run the other way — zh-TW resolved to the zh body
    verbatim — because no Traditional template existed. What it was really
    guarding is still guarded below: nothing may be *prepended* to a Simplified
    body to fake Traditional output, which is the approach #1542 tried and
    reverted. So the Traditional prompt has to be its own text rather than the zh
    body with something bolted on.
    """
    for getter in (get_fact_extraction_prompt, get_fact_extraction_ai_aware_prompt):
        traditional = getter("zh-TW")
        simplified = getter("zh")
        assert traditional != simplified
        assert simplified not in traditional, "Traditional must not wrap the zh body"
