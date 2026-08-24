# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The analyzer call site must hand the current language down to the executor.

``DirectTaskExecutor.analyze_and_execute`` declares ``lang: str = "en"`` and
feeds it to every ``_loc(...)`` lookup that builds the agent's channel
descriptions and system prompts.  ``_do_analyze_and_plan`` used to omit the
argument, so the default silently won and Japanese / Korean / Russian /
Spanish / Portuguese / Chinese users all got English prompts — the localized
templates in ``config/prompts/prompts_agent.py`` were never reachable.

Where the language comes from matters as much as passing it.  agent_server is a
separate process talking to main_server over ZeroMQ, so its own
``get_global_language()`` only derives NEKO_LANGUAGE / Steam / system locale — it
cannot see the frontend i18n truth the session holds in ``user_language``.  The
analyze_request event therefore carries the session locale, and
``_resolve_analyze_lang`` prefers it, falling back to the process global.

These tests are pinned at the **call site** at every hop (event → handler →
executor): a test that only exercises ``analyze_and_execute`` with an explicit
``lang`` cannot catch a caller that forgets to pass it.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

import pytest

from config.prompts.prompts_agent import (
    CHANNEL_DESC_BROWSER_USE,
    UNIFIED_CHANNEL_SYSTEM_PROMPT,
)
from config.prompts.prompts_sys import _loc
from utils.language_utils import language_context

# Scripts that prove the prompt really is in the target language rather than
# English.  Deliberately independent of the template wording: rephrasing a
# template must not silently turn these assertions into no-ops.
_SCRIPT_PROBES = {
    "ja": re.compile(r"[぀-ヿ]"),   # hiragana / katakana
    "ru": re.compile(r"[Ѐ-ӿ]"),   # Cyrillic
    "ko": re.compile(r"[가-힣]"),   # hangul syllables
    "zh": re.compile(r"[一-鿿]"),   # CJK ideographs
}


@pytest.fixture(autouse=True)
def _no_env_language_override(monkeypatch: pytest.MonkeyPatch):
    """``language_context`` yields to ``NEKO_LANGUAGE``; keep it out of the way."""
    monkeypatch.delenv("NEKO_LANGUAGE", raising=False)


class _CapturingExecutor:
    """Stands in for ``Modules.task_executor`` and records the kwargs it got."""

    def __init__(self):
        self.kwargs: Optional[dict[str, Any]] = None

    async def analyze_and_execute(self, **kwargs):
        self.kwargs = kwargs
        return None


def _install_agent_modules(monkeypatch: pytest.MonkeyPatch, executor: Any) -> None:
    from app.agent_server._shared import Modules

    monkeypatch.setattr(Modules, "task_executor", executor, raising=False)
    monkeypatch.setattr(Modules, "analyzer_enabled", True, raising=False)
    monkeypatch.setattr(
        Modules,
        "agent_flags",
        {
            "computer_use_enabled": False,
            "browser_use_enabled": False,
            "user_plugin_enabled": True,
            "openclaw_enabled": False,
            "openfang_enabled": False,
        },
        raising=False,
    )


def _run_analyze(
    monkeypatch: pytest.MonkeyPatch,
    executor: Any,
    ui_language: str,
    session_language: Optional[str] = None,
) -> None:
    """Drive ``_do_analyze_and_plan`` once.

    ``ui_language`` is what this process's global language resolves to;
    ``session_language`` is what rode the analyze_request event from main_server
    (``None`` = the event carried none, so the global is the only source).
    """
    from app.agent_server import api_runtime

    _install_agent_modules(monkeypatch, executor)

    messages = [{"role": "user", "content": "帮我打开浏览器搜索一下天气"}]
    with language_context(ui_language):
        asyncio.run(
            api_runtime._do_analyze_and_plan(
                messages, "喵喵", session_language=session_language,
            )
        )


# ---------------------------------------------------------------------------
# 1. The call site passes lang — the default must never win
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ui_language, expected_lang",
    [
        ("ja", "ja"),
        ("ko", "ko"),
        ("ru", "ru"),
        ("es", "es"),
        ("pt", "pt"),
        ("zh-CN", "zh"),
        # Short code by design: zh-TW resolves to 'zh' here, same as every other
        # subsystem that reads get_global_language().  Switching the agent
        # prompts to full codes belongs to the zh-TW migration (issue #2500),
        # not to this fix — flip this expectation there.
        ("zh-TW", "zh"),
        ("en", "en"),
    ],
)
def test_do_analyze_and_plan_passes_current_language(
    monkeypatch: pytest.MonkeyPatch, ui_language: str, expected_lang: str
):
    executor = _CapturingExecutor()
    _run_analyze(monkeypatch, executor, ui_language)

    assert executor.kwargs is not None, "analyze_and_execute was never called"
    # Explicit membership check, not .get(): relying on the "en" default is the
    # bug this test exists for, and .get() would paper over it for en users.
    assert "lang" in executor.kwargs, (
        "_do_analyze_and_plan must pass lang explicitly; otherwise "
        "analyze_and_execute's lang='en' default silently wins"
    )
    assert executor.kwargs["lang"] == expected_lang


def test_default_lang_is_english_so_omitting_it_would_be_a_regression():
    """Guards the premise of the test above.

    If the default ever stops being ``"en"``, "the caller forgot to pass lang"
    would no longer show up as English, and the assertions above would be
    testing something weaker than they claim.
    """
    import inspect

    from brain.task_executor import DirectTaskExecutor

    sig = inspect.signature(DirectTaskExecutor.analyze_and_execute)
    assert sig.parameters["lang"].default == "en"


# ---------------------------------------------------------------------------
# 2. End-to-end: the lang that reaches the executor selects the prompt template
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, content: str):
        self.content = content
        self.calls: list[list[dict[str, str]]] = []

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        return _FakeResponse(self.content)


class _PromptCapturingExecutor:
    """Feeds the received ``lang`` into the real unified-channel assembly.

    This closes the loop: the language selected in the UI must end up choosing
    the actual system prompt string the agent LLM is called with, not just some
    keyword in transit.
    """

    def __init__(self):
        self.system_prompt: Optional[str] = None
        self.lang: Optional[str] = None

    async def analyze_and_execute(self, **kwargs):
        from brain.task_executor import DirectTaskExecutor

        self.lang = kwargs.get("lang")

        executor = object.__new__(DirectTaskExecutor)
        fake_llm = _FakeLLM("{}")
        executor._get_llm = lambda **_kw: fake_llm
        executor._retrieve_relevant_corrections = lambda *a, **kw: []
        executor._build_correction_lessons_block = lambda _lessons: ""

        await executor._assess_unified_channels(
            "LATEST_USER_REQUEST: 打开浏览器搜天气",
            browser_available=True,
            lang=self.lang,
        )
        self.system_prompt = fake_llm.calls[0][0]["content"]
        return None


def _expected_unified_prompt(lang: str) -> str:
    return _loc(UNIFIED_CHANNEL_SYSTEM_PROMPT, lang).format(
        channels_block=_loc(CHANNEL_DESC_BROWSER_USE, lang),
        keys_json='["browser_use"]',
        json_fields=(
            '  "browser_use": {"can_execute": boolean, '
            '"task_description": "brief description", "reason": "why"},'
        ),
    )


@pytest.mark.parametrize("ui_language", ["ja", "ru", "ko", "zh-CN"])
def test_agent_system_prompt_follows_ui_language(
    monkeypatch: pytest.MonkeyPatch, ui_language: str
):
    executor = _PromptCapturingExecutor()
    _run_analyze(monkeypatch, executor, ui_language)

    assert executor.system_prompt, "unified assessment never built a system prompt"
    short = "zh" if ui_language.startswith("zh") else ui_language

    # Exact template selection …
    assert executor.system_prompt == _expected_unified_prompt(short)
    # … and it is not the English one we used to always send.
    assert executor.system_prompt != _expected_unified_prompt("en")
    # … and it really is written in that language, independent of wording.
    assert _SCRIPT_PROBES[short].search(executor.system_prompt), (
        f"{short} system prompt carries no {short} script — "
        "looks like an English template leaked through"
    )


def test_english_ui_still_gets_the_english_prompt(monkeypatch: pytest.MonkeyPatch):
    """The fix must not shift English users onto a different template."""
    executor = _PromptCapturingExecutor()
    _run_analyze(monkeypatch, executor, "en")

    assert executor.system_prompt == _expected_unified_prompt("en")


# ---------------------------------------------------------------------------
# 3. Every localized agent template is reachable through the short code
# ---------------------------------------------------------------------------


def test_short_codes_resolve_to_distinct_localized_templates():
    """``_loc`` must return a real per-language template for all 7 short codes.

    A missing key falls back to English inside ``_loc``, which is exactly the
    failure mode this PR fixes — catch it here instead of at runtime.
    """
    english = _loc(UNIFIED_CHANNEL_SYSTEM_PROMPT, "en")
    for short in ("zh", "ja", "ko", "ru", "es", "pt"):
        assert _loc(UNIFIED_CHANNEL_SYSTEM_PROMPT, short) != english
        assert _loc(CHANNEL_DESC_BROWSER_USE, short) != _loc(CHANNEL_DESC_BROWSER_USE, "en")


# ---------------------------------------------------------------------------
# 4. Session locale beats the agent process's own locale
# ---------------------------------------------------------------------------


def test_session_language_wins_over_process_global(monkeypatch: pytest.MonkeyPatch):
    """The frontend UI language is the truth; the machine's locale is the fallback.

    A user on a Chinese Windows / Steam who runs the UI in English must get English
    analyzer prompts — the process global would say ``zh``.
    """
    executor = _CapturingExecutor()
    _run_analyze(monkeypatch, executor, ui_language="zh-CN", session_language="en")

    assert executor.kwargs["lang"] == "en"


def test_session_language_wins_in_the_other_direction(monkeypatch: pytest.MonkeyPatch):
    """Symmetric case: English machine, Japanese UI → Japanese prompts."""
    executor = _PromptCapturingExecutor()
    _run_analyze(monkeypatch, executor, ui_language="en", session_language="ja")

    assert executor.lang == "ja"
    assert executor.system_prompt == _expected_unified_prompt("ja")
    assert _SCRIPT_PROBES["ja"].search(executor.system_prompt)


def test_session_language_is_short_normalized(monkeypatch: pytest.MonkeyPatch):
    """The event carries a full code; the prompt lookup needs the short one.

    ``prompts_agent.py`` has no ``zh-CN`` key, so forwarding the full code
    verbatim would take ``_loc``'s fallback path and log a warning on every turn.
    """
    executor = _CapturingExecutor()
    _run_analyze(monkeypatch, executor, ui_language="en", session_language="zh-CN")
    assert executor.kwargs["lang"] == "zh"

    executor = _CapturingExecutor()
    # Same short-code decision as the global path: zh-TW folds to 'zh' until the
    # #2500 migration switches the agent prompts to full codes.
    _run_analyze(monkeypatch, executor, ui_language="en", session_language="zh-TW")
    assert executor.kwargs["lang"] == "zh"


@pytest.mark.parametrize("garbage", ["undefined", "null", "estonian", "", "  ", "xx-YY"])
def test_garbage_session_language_falls_back_to_the_global(
    monkeypatch: pytest.MonkeyPatch, garbage: str
):
    """``normalize_language_code`` maps anything unrecognized to 'en'.

    Without an upfront validity fence, a corrupted ``localStorage`` value would
    look like a deliberate English choice and beat the correct global fallback.
    """
    executor = _CapturingExecutor()
    _run_analyze(monkeypatch, executor, ui_language="ja", session_language=garbage)

    assert executor.kwargs["lang"] == "ja"


def test_missing_session_language_falls_back_to_the_global(monkeypatch: pytest.MonkeyPatch):
    """Events published before this field existed (or with no session truth yet)."""
    executor = _CapturingExecutor()
    _run_analyze(monkeypatch, executor, ui_language="ru", session_language=None)

    assert executor.kwargs["lang"] == "ru"


# ---------------------------------------------------------------------------
# 5. The event → handler hop: the language must survive it
# ---------------------------------------------------------------------------


def _drive_session_event(monkeypatch: pytest.MonkeyPatch, event: dict[str, Any]) -> dict[str, Any]:
    """Feed one analyze_request into ``_on_session_event``, capture the plan kwargs."""
    from app.agent_server import api_runtime
    from app.agent_server._shared import Modules

    _install_agent_modules(monkeypatch, _CapturingExecutor())
    # A fresh fingerprint table, or the dedupe drops the event.
    monkeypatch.setattr(Modules, "last_user_turn_fingerprint", {}, raising=False)
    monkeypatch.setattr(Modules, "last_proactive_assistant_fingerprint", {}, raising=False)
    monkeypatch.setattr(Modules, "proactive_analyze_count", {}, raising=False)

    captured: dict[str, Any] = {}

    async def _fake_plan(messages, lanlan_name, **kwargs):
        captured.update(kwargs)
        captured["called"] = True

    monkeypatch.setattr(api_runtime, "_background_analyze_and_plan", _fake_plan)

    async def _go():
        await api_runtime._on_session_event(event)
        # _create_tracked_task schedules; give it one loop turn to run.
        await asyncio.sleep(0)

    asyncio.run(_go())
    return captured


def test_event_language_reaches_the_analyze_plan(monkeypatch: pytest.MonkeyPatch):
    captured = _drive_session_event(
        monkeypatch,
        {
            "event_type": "analyze_request",
            "lanlan_name": "喵喵",
            "trigger": "turn_end",
            "language": "ja",
            "messages": [{"role": "user", "content": "打开浏览器"}],
        },
    )

    assert captured.get("called"), "_background_analyze_and_plan was never scheduled"
    assert captured.get("session_language") == "ja"


def test_event_language_reaches_the_proactive_path(monkeypatch: pytest.MonkeyPatch):
    """The proactive branch returns early — it needs the same wiring, separately."""
    captured = _drive_session_event(
        monkeypatch,
        {
            "event_type": "analyze_request",
            "lanlan_name": "喵喵",
            "trigger": "turn_end",
            "proactive": True,
            "language": "ru",
            "messages": [
                {"role": "user", "content": "早"},
                {"role": "assistant", "content": "我刚看到一条新闻"},
            ],
        },
    )

    assert captured.get("called"), "proactive analyze was never scheduled"
    assert captured.get("session_language") == "ru"


# ---------------------------------------------------------------------------
# 6. The publisher side: every call site must hand the session locale over
# ---------------------------------------------------------------------------


def test_publisher_puts_the_language_on_the_event(monkeypatch: pytest.MonkeyPatch):
    from main_logic import agent_event_bus as bus

    published: list[dict[str, Any]] = []

    class _Bridge:
        owner_loop = None
        owner_thread_id = None

        async def publish_analyze_request(self, event):
            published.append(event)
            return False  # stop after the first attempt; we only inspect the payload

    async def _go(language):
        published.clear()
        monkeypatch.setattr(bus, "_main_bridge_ref", _Bridge(), raising=False)
        import threading

        _Bridge.owner_loop = asyncio.get_running_loop()
        _Bridge.owner_thread_id = threading.get_ident()
        await bus.publish_analyze_request_reliably(
            lanlan_name="喵喵",
            trigger="turn_end",
            messages=[{"role": "user", "content": "x"}],
            retries=0,
            language=language,
        )
        return published[0] if published else None

    with_lang = asyncio.run(_go("zh-TW"))
    assert with_lang is not None and with_lang.get("language") == "zh-TW"

    # Omitted when unknown, like conversation_id / external_intent / proactive —
    # the agent then keeps its process-global fallback.
    without = asyncio.run(_go(None))
    assert without is not None and "language" not in without


def test_every_analyze_publish_call_site_passes_a_language():
    """Auto-discovered: a new publish site that forgets ``language`` fails here.

    This is the same defect class the PR fixes — a caller silently falling back to
    a default — so the guard discovers call sites instead of listing them.
    """
    import ast
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    targets = {
        "publish_analyze_request_reliably",
        "_publish_analyze_request_with_fallback",
    }
    checked = 0
    offenders: list[str] = []

    for path in sorted((repo_root / "main_logic").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in targets:
                continue
            checked += 1
            if not any(kw.arg == "language" for kw in node.keywords):
                offenders.append(f"{path.relative_to(repo_root).as_posix()}:{node.lineno}")

    assert checked >= 3, f"found only {checked} publish call sites — did the API get renamed?"
    assert not offenders, f"analyze publish call sites missing language=: {offenders}"
