from __future__ import annotations

from types import SimpleNamespace

import pytest

from config.prompts.prompts_memory import (
    get_fact_extraction_ai_aware_prompt,
    get_fact_extraction_prompt,
)
from utils import language_utils


def test_macos_locale_reads_apple_locale(monkeypatch):
    # _get_macos_locale 带进程级缓存；不清掉的话本用例会读到别的用例留下的值
    # （在非 macOS 的 CI 上那个值是 None），断言就永远看不到 fake_run 的结果。
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
    # initialize_global_language() 会经 _is_china_region 和 _get_system_language
    # 各调一次；每次未命中都是一个 1s 超时的 subprocess，而且整个初始化持
    # _global_language_lock。两个全局 getter 又都能从 async 请求路径到达，
    # 冷启动多花几秒就会卡住事件循环。
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
    # 只缓存确定性结论。探测失败（超时 / 非零退出 / 空输出）如果也被缓存，一次
    # 启动期超时就会让整个进程生命周期都拿不到系统 locale——区域判定会掉到
    # non-china、语言判定会掉到英文，而「其它信号都不可靠」正是本函数要兜的场景。
    monkeypatch.setattr(
        language_utils, "_macos_locale_cache", language_utils._MACOS_LOCALE_UNSET
    )
    monkeypatch.setattr(language_utils.platform, "system", lambda: "Darwin")
    attempts: list[str] = []

    def flaky_run(command, **_kwargs):
        attempts.append(command[-1])
        if len(attempts) <= 2:
            # 前两次（AppleLocale + AppleLanguages）都超时 → 本轮整体失败
            raise language_utils.subprocess.TimeoutExpired(command, 1.0)
        return SimpleNamespace(returncode=0, stdout='"zh_CN"\n')

    monkeypatch.setattr(language_utils.subprocess, "run", flaky_run)

    assert language_utils._get_macos_locale() is None
    # 失败没被写进缓存，所以下一次调用会重新探测并拿到真实 locale
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
