"""Traditional Chinese must survive the trip from the global locale to the
prompt table (issue #2500, step 2 — scattered call sites + plugins).

Every test here sets the process locale to Traditional Chinese with the real
``language_context`` and then asserts on the *rendered* prompt, not on a
normalizer in isolation. Each one pins three outcomes apart:

* the Traditional template is the one that came out;
* it is NOT the Simplified template (the old bug — a short-code collapse);
* it is NOT the English template (the failure mode a naive "just pass the
  full code" fix introduces, because the full code for Simplified is
  ``zh-CN`` while these tables key Simplified as ``zh``).

The third assertion is the reason each test also runs the Simplified case:
a fix that drops Simplified users to English would pass a Traditional-only
test.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from utils.language_utils import language_context


# ---------------------------------------------------------------------------
# main_logic/activity/tracker.py — activity_guess narration locale
# ---------------------------------------------------------------------------


def _drive_activity_guess_once(monkeypatch):
    """Run exactly one iteration of ``_activity_guess_loop`` and return the
    ``lang`` values it handed to its two consumers.

    The loop body is the call site under test, so it is driven for real
    rather than re-derived here; everything it touches on the way there is
    stubbed on the instance.
    """
    from main_logic.activity import tracker as tracker_mod

    seen: dict[str, str] = {}

    monkeypatch.setattr(tracker_mod, "_ACTIVITY_GUESS_TICK_SECONDS", 0)
    monkeypatch.setattr(tracker_mod, "_privacy_mode_active", lambda: False)
    monkeypatch.setattr(tracker_mod, "_proactive_chat_enabled", lambda: True)
    monkeypatch.setattr(
        tracker_mod, "observation_from_system", lambda *a, **k: object(),
    )

    async def _fake_call_activity_guess(**kwargs):
        seen["activity"] = kwargs["lang"]
        # The loop returns on CancelledError, so this both records the value
        # and ends the iteration deterministically.
        raise asyncio.CancelledError

    from main_logic.activity import llm_enrichment
    monkeypatch.setattr(
        llm_enrichment, "call_activity_guess", _fake_call_activity_guess,
    )

    inst = object.__new__(tracker_mod.UserActivityTracker)
    inst.lanlan_name = "Neko"
    inst._conv_seq = 0
    inst._user_msg_buffer = []
    inst._ai_msg_buffer = []
    rule_snap = SimpleNamespace(state="focused_work")
    inst._sm = SimpleNamespace(
        _prefs=None,
        update_system=lambda *a, **k: None,
        update_window=lambda *a, **k: None,
        get_snapshot=lambda **k: rule_snap,
    )
    inst._select_system_snapshot = lambda ts: object()
    inst._tick_break_reminders = lambda snap, **k: None

    async def _drain():
        return None

    inst._drain_context_prompt = _drain
    inst._process_topic_candidates_if_ready = (
        lambda *, lang, now: seen.__setitem__("topic", lang)
    )
    inst._is_narration_suppressed = lambda: False
    inst._coarse_activity_sig = lambda snap: ("focused_work", "x")
    inst._activity_guess_gate = SimpleNamespace(
        should_fire=lambda *a, **k: True,
        record_fired=lambda *a, **k: None,
    )
    inst._snapshot_signals_for_llm = lambda snap, **k: {}

    asyncio.run(inst._activity_guess_loop())
    return seen


@pytest.mark.parametrize(
    ("ui_locale", "expected"),
    [("zh-TW", "zh-TW"), ("zh-CN", "zh-CN"), ("ja", "ja")],
)
def test_activity_guess_loop_passes_full_locale(monkeypatch, ui_locale, expected):
    """The narration locale must reach ``call_activity_guess`` as a full code.

    ``ACTIVITY_GUESS_PROMPTS`` carries a distinct ``zh-TW`` template and
    ``llm_enrichment._normalize_lang`` knows how to select it, so collapsing
    to the short code here is the one step that made Traditional users read
    a Simplified narration.
    """
    with language_context(ui_locale):
        seen = _drive_activity_guess_once(monkeypatch)

    assert seen["activity"] == expected
    # Both consumers ride the same value; the topic pool already needed full.
    assert seen["topic"] == expected


def test_activity_guess_traditional_locale_selects_traditional_template():
    """The value the loop now passes must actually change the prompt text —
    otherwise the flip above would be a no-op rename."""
    from config.prompts.prompts_activity import ACTIVITY_GUESS_PROMPTS
    from main_logic.activity.llm_enrichment import (
        _normalize_lang,
        _select_lang_template,
    )

    traditional = _select_lang_template(
        ACTIVITY_GUESS_PROMPTS, _normalize_lang("zh-TW"),
    )
    simplified = _select_lang_template(
        ACTIVITY_GUESS_PROMPTS, _normalize_lang("zh-CN"),
    )
    assert traditional == ACTIVITY_GUESS_PROMPTS["zh-TW"]
    assert simplified == ACTIVITY_GUESS_PROMPTS["zh"]
    assert traditional != simplified
    assert traditional != ACTIVITY_GUESS_PROMPTS["en"]


# ---------------------------------------------------------------------------
# plugin/plugins/game_agent_minecraft — user_lang() + PROMPTS tables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ui_locale", "expected"),
    [("zh-TW", "zh-TW"), ("zh-CN", "zh"), ("zh", "zh"), ("ja", "ja"), ("en", "en")],
)
def test_minecraft_user_lang_resolves_to_table_key(ui_locale, expected):
    """``user_lang`` must land on a key of this module's own tables.

    Traditional keeps its own key; Simplified collapses to ``zh`` rather
    than leaking ``zh-CN`` — which is not a key here, so the
    ``in SUPPORTED_LANGS`` guard would have bounced every Simplified user
    to English.
    """
    from plugin.plugins.game_agent_minecraft import prompts

    with language_context(ui_locale):
        assert prompts.user_lang() == expected
    assert expected in prompts.SUPPORTED_LANGS


def test_minecraft_prompts_render_traditional_not_simplified_or_english():
    from plugin.plugins.game_agent_minecraft import prompts

    with language_context("zh-TW"):
        cue = prompts.t("TASK_NOT_CONNECTED", lang=prompts.user_lang())
    assert cue == prompts.PROMPTS["TASK_NOT_CONNECTED"]["zh-TW"]
    assert cue != prompts.PROMPTS["TASK_NOT_CONNECTED"]["zh"]
    assert cue != prompts.PROMPTS["TASK_NOT_CONNECTED"]["en"]


def test_minecraft_t_degrades_chinese_variants_to_simplified_not_english():
    """A future table that forgets ``zh-TW`` must fall back to Simplified.

    English mid-conversation is a language switch; Simplified is merely the
    wrong script. Same rule as ``prompts_sys._loc``.
    """
    from plugin.plugins.game_agent_minecraft import prompts

    incomplete = {"zh": "简体", "en": "English"}
    original = prompts.PROMPTS.get("__TEST_ONLY__")
    prompts.PROMPTS["__TEST_ONLY__"] = incomplete
    try:
        assert prompts.t("__TEST_ONLY__", lang="zh-TW") == "简体"
        assert prompts.t("__TEST_ONLY__", lang="ko") == "English"
    finally:
        if original is None:
            prompts.PROMPTS.pop("__TEST_ONLY__", None)
        else:  # pragma: no cover - defensive
            prompts.PROMPTS["__TEST_ONLY__"] = original
