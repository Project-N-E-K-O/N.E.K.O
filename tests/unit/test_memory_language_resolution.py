from __future__ import annotations

from types import SimpleNamespace

import pytest

from config.prompts.prompts_memory import (
    get_fact_extraction_ai_aware_prompt,
    get_fact_extraction_prompt,
)
from utils import language_utils


def test_macos_locale_reads_apple_locale(monkeypatch):
    monkeypatch.setattr(language_utils.platform, "system", lambda: "Darwin")

    def fake_run(command, **_kwargs):
        assert command == ["/usr/bin/defaults", "read", "-g", "AppleLocale"]
        return SimpleNamespace(returncode=0, stdout='"zh_CN@calendar=gregorian"\n')

    monkeypatch.setattr(language_utils.subprocess, "run", fake_run)

    assert language_utils._get_macos_locale() == "zh_CN"


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
    monkeypatch.setattr(language_utils, "_is_china_region", lambda: True)
    monkeypatch.setattr(language_utils, "_get_steam_language", lambda: "japanese")
    monkeypatch.setattr(language_utils, "_get_system_language", lambda: "zh")

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


def test_zh_tw_fact_extraction_reuses_zh_template_body():
    # zh-TW has no dedicated template and must resolve to the zh body verbatim,
    # with nothing prepended that would make the two diverge.
    assert get_fact_extraction_prompt("zh-TW") == get_fact_extraction_prompt("zh")
    assert get_fact_extraction_ai_aware_prompt("zh-TW") == get_fact_extraction_ai_aware_prompt("zh")
