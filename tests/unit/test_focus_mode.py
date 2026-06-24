"""Focus mode v1 unit tests: hysteresis state machine + signal scorer + lexicon scans.

Coverage:
1. ``_focus_decide`` pure leaky-accumulator transition: strong-single enter /
   scattered-cue accumulation / charge cap / decayed exit / noise-doesn't-stick /
   hard-cap exit / topic-switch exit-and-clear.
2. ``FocusScorer`` (inline-only): keyword + cadence sub-signals, direct
   weighted-sum scoring (no denominator), cadence baseline roll.
3. ``SessionStateMachine.update_focus``: async enter/exit, FOCUS_EXIT payload,
   retention override, reset clearing, master-switch-off degradation.
4. ``prompts_focus`` lexicon scans: vulnerability count, cross-locale (mixed
   language) scanning, topic-switch anchoring.
5. ``stream_text`` thinking-on threading (Path A wiring).
6. Idle cooldown: proactive ticks decay charge (two-tier retention by whether
   the turn spoke), never enter; thinking read is mode-only; decay is pinned to
   the observed episode + turn (skips on no-episode / episode-changed / inline
   recharge of the same episode / user takeover mid-turn).
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

import config
from config.prompts.prompts_focus import (
    detect_topic_switch,
    scan_vulnerability_keywords,
)
from main_logic.activity.focus_scorer import FocusScorer
from main_logic.session_state import (
    CognitionMode,
    FocusThresholds,
    SessionEvent,
    SessionStateMachine,
    TurnOwner,
    _focus_decide,
    _FocusAction,
)


# ── helpers ─────────────────────────────────────────────────────────
def _th(retention=0.5, enter=1.0, exit=0.3, hard_cap_turns=8, enabled=True,
        cap=1.0, time_decay=0.0, time_decay_activated=0.0):
    return FocusThresholds(
        enabled=enabled, retention=retention, enter=enter, exit=exit,
        hard_cap_turns=hard_cap_turns, cap=cap, time_decay=time_decay,
        time_decay_activated=time_decay_activated,
    )




# ── 1. pure leaky-accumulator transition ───────────────────────────
def test_decide_enter_on_strong_single_score():
    # One strong message (score == enter) crosses immediately: 0*0.5 + 1.0 = 1.0.
    d = _focus_decide(mode=CognitionMode.REGULAR, focus_turn_count=0, charge=0.0,
                      score=1.0, topic_changed=False, th=_th())
    assert d.action is _FocusAction.ENTER
    assert d.turn_count == 1 and d.charge == 1.0


def test_decide_stay_regular_below_enter():
    d = _focus_decide(mode=CognitionMode.REGULAR, focus_turn_count=0, charge=0.0,
                      score=0.4, topic_changed=False, th=_th())
    assert d.action is _FocusAction.STAY
    assert d.charge == 0.4  # accumulating, not yet at enter


def test_decide_scattered_cues_accumulate_to_enter():
    # Two moderate turns add up past the bar (prior charge 0.67 + new 0.67):
    # 0.67*0.5 + 0.67 = 1.005 → capped to enter=1.0 → ENTER.
    d = _focus_decide(mode=CognitionMode.REGULAR, focus_turn_count=0, charge=0.67,
                      score=0.67, topic_changed=False, th=_th())
    assert d.action is _FocusAction.ENTER
    assert d.charge == 1.0  # capped at enter


def test_decide_charge_capped_at_enter():
    d = _focus_decide(mode=CognitionMode.REGULAR, focus_turn_count=0, charge=0.9,
                      score=0.9, topic_changed=False, th=_th())
    # 0.9*0.5 + 0.9 = 1.35 → cap 1.0
    assert d.action is _FocusAction.ENTER and d.charge == 1.0


def test_decide_charge_climbs_above_enter_to_cap():
    # Entry no longer caps charge at `enter`: with cap=1.0 > enter=0.6, sustained
    # strong scores keep climbing toward the cap (drives a brighter/longer glow).
    th = _th(enter=0.6, cap=1.0)
    d = _focus_decide(mode=CognitionMode.FOCUS, focus_turn_count=1, charge=0.9,
                      score=0.9, topic_changed=False, th=th)
    # 0.9*0.5 + 0.9 = 1.35 → cap 1.0, NOT clamped down to enter 0.6
    assert d.action is _FocusAction.STAY and d.charge == 1.0


def test_decide_negative_score_clamps_charge_at_zero():
    # A happy turn now yields a NEGATIVE score (emotion votes Focus down); the
    # accumulator must clamp at 0, not go negative.
    th = _th(enter=0.6)
    d = _focus_decide(mode=CognitionMode.REGULAR, focus_turn_count=0, charge=0.2,
                      score=-0.5, topic_changed=False, th=th)
    # 0.2*0.5 + (-0.5) = -0.4 → clamped to 0.0, stays REGULAR
    assert d.action is _FocusAction.STAY and d.charge == 0.0


def test_time_decay_floors_at_activation():
    from main_logic.session_state import _decay_charge_over_time
    th = _th(enter=0.6, time_decay=0.02, time_decay_activated=0.01)
    # Below enter → fast rate, bleeds toward 0: 0.5 - 0.02*5 = 0.4
    assert abs(_decay_charge_over_time(0.5, 5.0, th) - 0.4) < 1e-9
    # At/above enter → slow rate: 0.9 - 0.01*5 = 0.85 (still above the 0.6 floor)
    assert abs(_decay_charge_over_time(0.9, 5.0, th) - 0.85) < 1e-9
    # Activated charge can ONLY decay down to enter, never below — dropping out
    # of 0.6 needs a turn, not silence: 1.0 for 60s → max(0.6, 0.4) = 0.6.
    assert abs(_decay_charge_over_time(1.0, 60.0, th) - 0.6) < 1e-9
    # Sitting at the floor stays at the floor no matter how long.
    assert _decay_charge_over_time(0.6, 10000.0, th) == 0.6
    # Below enter clamps to zero; no rates configured → no decay.
    assert _decay_charge_over_time(0.1, 100.0, th) == 0.0
    assert _decay_charge_over_time(0.5, 100.0, _th(enter=0.6)) == 0.5


def test_decide_focus_stays_while_charge_above_exit():
    # charge 1.0, neutral turn: 1.0*0.5 + 0 = 0.5 >= exit 0.3 → STAY.
    d = _focus_decide(mode=CognitionMode.FOCUS, focus_turn_count=1, charge=1.0,
                      score=0.0, topic_changed=False, th=_th())
    assert d.action is _FocusAction.STAY
    assert d.turn_count == 2 and d.charge == 0.5


def test_decide_focus_exits_when_charge_decays():
    # charge 0.5, neutral: 0.25 < exit 0.3 → EXIT (leaked away).
    d = _focus_decide(mode=CognitionMode.FOCUS, focus_turn_count=3, charge=0.5,
                      score=0.0, topic_changed=False, th=_th())
    assert d.action is _FocusAction.EXIT
    assert d.reason == "decayed"


def test_decide_noisy_midscore_does_not_stick_forever():
    # The old streak bug: a mid-score blip kept resetting the exit counter.
    # With the leak, a 0.26 blip only slows decay; it still drains out.
    th = _th()
    charge = 1.0
    seq = [0.0, 0.26, 0.0, 0.26, 0.0]  # the PR-observed cadence-noise pattern
    exited = False
    for i, s in enumerate(seq):
        d = _focus_decide(mode=CognitionMode.FOCUS, focus_turn_count=i + 1,
                          charge=charge, score=s, topic_changed=False, th=th)
        if d.action is _FocusAction.EXIT:
            exited = True
            break
        charge = d.charge
    assert exited  # never gets stuck on


def test_decide_hard_cap_exit():
    d = _focus_decide(mode=CognitionMode.FOCUS, focus_turn_count=8, charge=1.0,
                      score=0.9, topic_changed=False, th=_th(hard_cap_turns=8))
    assert d.action is _FocusAction.EXIT
    assert d.reason == "hard_cap"


def test_decide_topic_switch_exits_focus_and_clears_regular():
    d = _focus_decide(mode=CognitionMode.FOCUS, focus_turn_count=1, charge=1.0,
                      score=0.95, topic_changed=True, th=_th())
    assert d.action is _FocusAction.EXIT and d.reason == "topic_switch"
    # in REGULAR a topic switch drops the OLD accumulator (no leak), score=0
    d2 = _focus_decide(mode=CognitionMode.REGULAR, focus_turn_count=0, charge=0.8,
                       score=0.0, topic_changed=True, th=_th())
    assert d2.action is _FocusAction.STAY and d2.charge == 0.0


def test_decide_topic_switch_seeds_new_topic_with_current_score():
    # A topic-switch opener that is ITSELF vulnerable ("对了，我撑不住了") must
    # not be dropped: the old charge is cleared (no leak) but this turn's score
    # seeds the new topic from a clean slate.
    d = _focus_decide(mode=CognitionMode.REGULAR, focus_turn_count=0, charge=0.9,
                      score=0.4, topic_changed=True, th=_th(enter=1.0))
    assert d.action is _FocusAction.STAY and d.charge == 0.4  # seeded, not 0, not 0.9
    # a strong vulnerable pivot enters Focus immediately on the new topic
    d2 = _focus_decide(mode=CognitionMode.REGULAR, focus_turn_count=0, charge=0.0,
                       score=1.0, topic_changed=True, th=_th(enter=1.0))
    assert d2.action is _FocusAction.ENTER and d2.charge == 1.0


def test_decide_hard_cap_yields_exactly_n_focus_turns():
    # Sustained strong signal keeps charge at the cap, so only the hard cap
    # ends it — exactly hard_cap_turns thinking-on turns.
    th = _th(hard_cap_turns=4)
    mode = CognitionMode.REGULAR
    count, charge = 0, 0.0
    focus_turns = 0
    for _ in range(10):
        d = _focus_decide(mode=mode, focus_turn_count=count, charge=charge,
                          score=1.0, topic_changed=False, th=th)
        if d.action is _FocusAction.ENTER:
            mode = CognitionMode.FOCUS
            count, charge = d.turn_count, d.charge
            focus_turns += 1
        elif d.action is _FocusAction.STAY and mode is CognitionMode.FOCUS:
            count, charge = d.turn_count, d.charge
            focus_turns += 1
        elif d.action is _FocusAction.STAY:
            charge = d.charge  # regular accumulating (none here, enters turn 1)
        elif d.action is _FocusAction.EXIT:
            break
    assert focus_turns == 4


# ── 2. FocusScorer (inline-only: keyword + cadence) ─────────────────
def test_scorer_keyword_inline():
    s = FocusScorer("x")
    res = s.score(user_text="今天好累，感觉一个人撑不住了")
    assert res.signals["keyword"] is not None and res.signals["keyword"] > 0
    assert "silence" not in res.signals  # idle signals removed
    assert res.score > 0


def test_scorer_no_signal_is_zero():
    s = FocusScorer("x")
    res = s.score(user_text="嗯，那个文件我改好了发你了")
    # No vulnerability keyword → None (positive-evidence-only); cadence not enough
    # samples → None. All signals absent → score 0.0.
    assert res.signals["keyword"] is None
    assert res.score == 0.0


def test_scorer_cadence_drop_after_baseline():
    s = FocusScorer("x")
    # Feed long messages to build a baseline (each call appends after scoring).
    for _ in range(4):
        s.score(user_text="这是一段比较长的正常聊天消息内容大概三十个字符以上")
    # Query the cadence sub-signal directly: score() gates cadence out when no
    # distress evidence (keyword/emotion) is present, so going through score()
    # here would mask the cadence computation this test is about.
    assert s._signal_cadence("嗯。") is not None and s._signal_cadence("嗯。") > 0.5


def test_scorer_cadence_none_without_baseline():
    s = FocusScorer("x")
    res = s.score(user_text="嗯。")
    assert res.signals["cadence"] is None  # below FOCUS_CADENCE_MIN_SAMPLES


# ── 3. SessionStateMachine.update_focus (async) ─────────────────────
def _patch_charge(monkeypatch, *, retention=0.5, enter=1.0, exit=0.3, hard_cap=99,
                  idle_silent=0.95, idle_replied=0.6):
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", True)
    monkeypatch.setattr(config, "FOCUS_CHARGE_RETENTION", retention)
    monkeypatch.setattr(config, "FOCUS_CHARGE_ENTER", enter)
    monkeypatch.setattr(config, "FOCUS_CHARGE_EXIT", exit)
    monkeypatch.setattr(config, "FOCUS_HARD_CAP_TURNS", hard_cap)
    monkeypatch.setattr(config, "FOCUS_IDLE_SILENT_RETENTION", idle_silent)
    monkeypatch.setattr(config, "FOCUS_IDLE_REPLIED_RETENTION", idle_replied)


async def test_sm_enter_and_exit_cycle(monkeypatch):
    _patch_charge(monkeypatch)  # retention 0.5, enter 1.0, exit 0.3
    sm = SessionStateMachine(lanlan_name="x")
    events = []
    sm.subscribe(None, lambda ev, pl: events.append((ev, pl)))

    assert await sm.update_focus(1.0) is CognitionMode.FOCUS  # charge 1.0 → enter
    assert sm.mode is CognitionMode.FOCUS
    assert events[0][0] is SessionEvent.FOCUS_ENTER
    ep_id = events[0][1]["episode_id"]
    assert ep_id and ep_id.startswith("x-")

    # neutral turn: charge 1.0*0.5 = 0.5 ≥ exit 0.3 → still FOCUS
    assert await sm.update_focus(0.0) is CognitionMode.FOCUS
    # neutral again: 0.5*0.5 = 0.25 < 0.3 → leaked out, exit
    assert await sm.update_focus(0.0) is CognitionMode.REGULAR
    assert sm.mode is CognitionMode.REGULAR
    exit_evt = [e for e in events if e[0] is SessionEvent.FOCUS_EXIT]
    assert exit_evt and exit_evt[0][1]["episode_id"] == ep_id
    assert exit_evt[0][1]["reason"] == "decayed"
    assert "episode_started_at" in exit_evt[0][1]


async def test_sm_scattered_cues_accumulate_to_enter(monkeypatch):
    # The product ask: gradual vulnerability across turns adds up to enter,
    # without any single message crossing the bar alone.
    _patch_charge(monkeypatch, retention=0.5, enter=1.0)
    sm = SessionStateMachine(lanlan_name="x")
    assert await sm.update_focus(0.6) is CognitionMode.REGULAR  # charge 0.6
    assert await sm.update_focus(0.6) is CognitionMode.REGULAR  # 0.6*0.5+0.6=0.9
    assert await sm.update_focus(0.6) is CognitionMode.FOCUS    # 0.9*0.5+0.6=1.05→cap, enter


async def test_sm_hard_cap_exit(monkeypatch):
    _patch_charge(monkeypatch, hard_cap=3)
    sm = SessionStateMachine(lanlan_name="x")
    modes = [await sm.update_focus(1.0) for _ in range(5)]
    # Cap=3: 3 focus turns then forced REGULAR exit at turn 4, even though
    # charge stays at the cap. Turn 5 re-enters (sustained strong signal) —
    # the cap bounds episode length, not total focus time.
    assert [m is CognitionMode.FOCUS for m in modes[:4]] == [True, True, True, False]
    assert modes[4] is CognitionMode.FOCUS  # re-entry allowed


async def test_sm_topic_switch_immediate_exit(monkeypatch):
    _patch_charge(monkeypatch)
    sm = SessionStateMachine(lanlan_name="x")
    await sm.update_focus(1.0)
    assert sm.mode is CognitionMode.FOCUS
    assert await sm.update_focus(1.0, topic_changed=True) is CognitionMode.REGULAR


async def test_sm_master_switch_off_is_noop(monkeypatch):
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", False)
    sm = SessionStateMachine(lanlan_name="x")
    assert await sm.update_focus(0.99) is CognitionMode.REGULAR
    assert sm.mode is CognitionMode.REGULAR


async def test_sm_disable_mid_episode_clears_stale_focus(monkeypatch):
    # Enter focus, then flip the master switch off: the next update_focus
    # must drop the stale FOCUS rather than leaving it active.
    _patch_charge(monkeypatch)
    sm = SessionStateMachine(lanlan_name="x")
    await sm.update_focus(1.0)
    assert sm.mode is CognitionMode.FOCUS
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", False)
    assert await sm.update_focus(0.0) is CognitionMode.REGULAR
    assert sm.mode is CognitionMode.REGULAR


async def test_sm_disable_clears_charge_even_in_regular(monkeypatch):
    # Accumulator sitting in REGULAR just below the enter bar; flag off must
    # zero the charge too, so re-enabling can't enter on stale pre-disable charge.
    _patch_charge(monkeypatch, enter=1.0)
    sm = SessionStateMachine(lanlan_name="x")
    await sm.update_focus(0.6)  # REGULAR, charge building (~0.6)
    assert sm.mode is CognitionMode.REGULAR
    assert sm.snapshot()["focus_charge"] > 0
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", False)
    await sm.update_focus(0.0)
    assert sm.snapshot()["focus_charge"] == 0.0
    # re-enable: a lone mild cue must NOT enter (charge started from zero)
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", True)
    assert await sm.update_focus(0.6) is CognitionMode.REGULAR


async def test_sm_clear_focus_silent_no_exit_event(monkeypatch):
    # clear_focus drops FOCUS→REGULAR + zeroes charge but fires NO FOCUS_EXIT
    # (repetition recovery wipes the conversation; a degenerate loop is not a
    # coherent episode to synthesize). Mirrors reset's silent focus clear.
    _patch_charge(monkeypatch)
    sm = SessionStateMachine(lanlan_name="x")
    events = []
    sm.subscribe(None, lambda ev, pl: events.append(ev))
    await sm.update_focus(1.0)
    assert sm.mode is CognitionMode.FOCUS
    events.clear()
    await sm.clear_focus()
    assert sm.mode is CognitionMode.REGULAR
    assert sm.snapshot()["focus_charge"] == 0.0
    assert SessionEvent.FOCUS_EXIT not in events
    # also zeroes a REGULAR charge sitting under the bar
    await sm.update_focus(0.6)
    assert sm.snapshot()["focus_charge"] > 0
    await sm.clear_focus()
    assert sm.snapshot()["focus_charge"] == 0.0


async def test_sm_reset_clears_focus(monkeypatch):
    _patch_charge(monkeypatch)
    sm = SessionStateMachine(lanlan_name="x")
    await sm.update_focus(1.0)
    assert sm.mode is CognitionMode.FOCUS
    await sm.reset(force=True)
    assert sm.mode is CognitionMode.REGULAR
    assert sm.snapshot()["mode"] == "regular"


# ── 4. prompts_focus lexicon ────────────────────────────────────────
def test_vulnerability_keyword_count():
    assert scan_vulnerability_keywords("好累，一个人，没意思") >= 3
    assert scan_vulnerability_keywords("今天天气不错") == 0


def test_vulnerability_denests_nested_phrases():
    # "好难受" matches both "难受" and "好难受"; de-nesting counts it once,
    # so one cue can't double-count toward saturation.
    assert scan_vulnerability_keywords("好难受") == 1
    assert scan_vulnerability_keywords("so lonely") == 1


def test_vulnerability_cross_locale_mixed_language():
    # Scan runs across ALL locale tables (mixed-language speech is common):
    # an EN cue in an otherwise-CJK message is still counted, and CJK + EN
    # cues stacked count as distinct hits.
    assert scan_vulnerability_keywords("今天 so tired，好累") >= 2
    assert scan_vulnerability_keywords("exhausted and so alone") >= 2


def test_vulnerability_profanity_counts_as_cue():
    # Profanity/venting is part of the vulnerability lexicon now — swearing is
    # a strong distress tell. Sampled across every locale.
    for msg in [
        "草泥马这也太难了", "靠北喔", "fuck this", "this is bullshit",
        "もうくそだ", "씨발 진짜", "блять как же тяжело", "joder qué mierda",
        "que merda, caralho",
    ]:
        assert scan_vulnerability_keywords(msg) >= 1, msg


def test_vulnerability_profanity_avoids_short_substring_false_positives():
    # The risky short forms (ass / 操 / 幹 / hell) must NOT match ordinary words
    # — we only ship the longer, non-embedding profanity forms.
    for clean in [
        "please pass the class", "let me assume the worst",
        "我先操作一下电脑", "去操场跑步", "他幹活很认真", "shell command",
        # reviewer-flagged neutral substrings we deliberately dropped:
        "垃圾桶在哪", "垃圾分类怎么做",          # bare 垃圾 removed
        "找 tmdb 上的评分", "tmdb 这部电影",      # bare tmd removed (TMDB)
        "застрахуй меня", "надо страхуй оформить",  # bare хуй removed (insurance)
    ]:
        assert scan_vulnerability_keywords(clean) == 0, clean


def test_vulnerability_excludes_filler_interjections():
    # Mild interjections that have become everyday filler are deliberately NOT
    # cues — too noisy as a distress signal (卧槽好牛 / damn cool are often
    # positive; 馬鹿 is affectionate; 존나 means "very").
    for filler in [
        "卧槽这也太牛了", "我擦真的假的", "wtf is this", "damn that's cool",
        "馬鹿だなあ", "존나 좋아", "hostia tío", "que cacete bom",
    ]:
        assert scan_vulnerability_keywords(filler) == 0, filler


def test_topic_switch_anchored_at_start():
    assert detect_topic_switch("对了，今天天气怎么样") is True
    assert detect_topic_switch("by the way, did you eat") is True
    # cross-locale: an EN pivot is detected even though no lang is passed
    # (and vice-versa) — mixed-language users pivot in either tongue.
    assert detect_topic_switch("btw 你吃了吗") is True
    # marker buried mid-sentence is not a pivot
    assert detect_topic_switch("我觉得对了这个想法不错") is False


# ── 5. stream_text thinking-on threading (Path A wiring) ────────────
async def _drain(agen):
    return [c async for c in agen]


def test_focus_stream_overrides_decision():
    """The thinking-on override decision + provider-extra preservation.
    ``stream_text`` applies this before streaming: thinking-on whenever focus is
    active (vision turns included now); when it overrides, it FLIPS the provider's
    thinking knob to its enabled form (per provider dialect) while keeping
    non-thinking extras (e.g. web_search)."""
    from main_logic.omni_offline_client import OmniOfflineClient as _C
    # thinking.type provider (GLM/Kimi/Doubao) → flipped to enabled (not dropped to None)
    assert _C._focus_stream_overrides(True, "glm-5.2") == {
        "extra_body": {"thinking": {"type": "enabled"}}
    }
    # Anthropic claude: enable needs {type:adaptive} via OpenAI-compat; 本 PR 暂不翻 →
    # 保持 disabled（安全退化，绝不 400），adaptive 接入留 follow-up
    assert _C._focus_stream_overrides(True, "claude-opus-4-7") == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }
    # free server (model 名固定 free-model) shares the thinking.type dialect
    assert _C._focus_stream_overrides(True, "free-model") == {
        "extra_body": {"thinking": {"type": "enabled"}}
    }
    # OpenAI-shape bool knob flips False→True
    assert _C._focus_stream_overrides(True, "qwen-flash") == {
        "extra_body": {"enable_thinking": True}
    }
    # unknown model → no resolved extra_body → None
    assert _C._focus_stream_overrides(True, "test-model") == {"extra_body": None}
    # step-2-mini ships a web_search tool → it MUST survive (not nuked to None)
    so = _C._focus_stream_overrides(True, "step-2-mini")
    assert so["extra_body"] is not None and "tools" in so["extra_body"]
    # thinking off → no override (vision no longer gates thinking)
    assert _C._focus_stream_overrides(False, "step-2-mini") == {}


def test_focus_extra_body_provider_dialects():
    """focus_extra_body flips each provider's thinking knob to its ENABLED form
    per that provider's own dialect (not a blind key-drop). Free server defaults
    to disabled and flips to enabled; non-thinking extras and MiniMax's
    reasoning_split (no on/off semantics) are preserved unchanged."""
    from config.providers import (
        focus_extra_body, get_extra_body, EXTRA_BODY_CLAUDE,
    )
    from config.providers import EXTRA_BODY_MINIMAX  # not re-exported via config/__init__

    # free server (model 名固定 free-model): regular turn sends thinking DISABLED…
    assert get_extra_body("free-model") == {"thinking": {"type": "disabled"}}
    assert get_extra_body("free-model") == EXTRA_BODY_CLAUDE
    # …凝神 flips it to enabled (thinking.type dialect)
    assert focus_extra_body("free-model") == {"thinking": {"type": "enabled"}}

    # per-dialect enabled forms
    assert focus_extra_body("qwen-flash") == {"enable_thinking": True}
    assert focus_extra_body("glm-5.2") == {"thinking": {"type": "enabled"}}
    assert focus_extra_body("gemini-2.5-flash") == {
        "extra_body": {"google": {"thinking_config": {"thinking_budget": 800}}}
    }
    assert focus_extra_body("gemini-3-flash-preview") == {
        "extra_body": {"google": {"thinking_config": {"thinking_level": "low", "include_thoughts": True}}}
    }
    assert focus_extra_body("google/gemini-2.5-flash") == {"reasoning": {"effort": "low"}}

    # Anthropic claude: enable 须 {type:adaptive}(OpenAI-compat)，本 PR 暂不翻 → 凝神保持 disabled
    assert get_extra_body("claude-opus-4-7") == {"thinking": {"type": "disabled"}}
    assert focus_extra_body("claude-opus-4-7") == {"thinking": {"type": "disabled"}}
    # MiniMax reasoning_split is not an on/off knob → preserved, not flipped
    assert focus_extra_body("MiniMax-M2.5") == EXTRA_BODY_MINIMAX
    # non-thinking provider extra (step web_search) preserved on a focus turn
    assert "tools" in focus_extra_body("step-2-mini")
    # unknown / empty model → no extra_body
    assert focus_extra_body("nonexistent") is None
    assert focus_extra_body("") is None

    # returns a fresh dict each call — mutating the result can't poison the constant
    focus_extra_body("qwen-flash")["enable_thinking"] = False
    assert focus_extra_body("qwen-flash") == {"enable_thinking": True}
    # deepcopy also protects NESTED dialects (glm thinking dict) from alias pollution
    nested = focus_extra_body("glm-5.2")
    nested["thinking"]["type"] = "disabled"
    assert focus_extra_body("glm-5.2") == {"thinking": {"type": "enabled"}}


async def test_focus_override_threads_through_visible_stream():
    """The override returned above must reach ``llm.astream`` unchanged through
    the real production path (``_astream_visible_with_tools`` → tool-leak filter
    → ``_astream_with_tools`` → ``astream``); regular turns thread no extra_body."""
    from main_logic.omni_offline_client import OmniOfflineClient

    captured = []

    class _FakeLLM:
        async def astream(self, messages, **overrides):
            captured.append(overrides)
            return
            yield  # unreachable — marks this as an async generator

    def _make_client():
        c = OmniOfflineClient.__new__(OmniOfflineClient)
        c._use_genai_sdk = False
        c._genai_tools_unsupported = False
        c.max_tool_iterations = 1
        c.on_tool_call = None
        c._tool_definitions = []
        c.base_url = "https://example.test/v1"
        c.model = "test-model"
        c.llm = _FakeLLM()
        return c

    # focus turn (no images, unknown model): _focus_stream_overrides → {"extra_body": None}
    c = _make_client()
    overrides = OmniOfflineClient._focus_stream_overrides(True, c.model)
    await _drain(c._astream_visible_with_tools(["m"], **overrides))
    assert captured[-1].get("extra_body", "MISSING") is None

    # regular turn: no extra_body threaded
    c2 = _make_client()
    await _drain(c2._astream_visible_with_tools(["m"], **OmniOfflineClient._focus_stream_overrides(False, c2.model)))
    assert "extra_body" not in captured[-1]


# ── 6. caller-level Focus gates: charge hygiene + privacy-independence ─────
# Lock two things: (a) the disabled gate clears residual REGULAR charge (the
# caller must call update_focus even in REGULAR, else a charge frozen under the
# enter bar survives a disabled window); (b) Focus scores the user's MESSAGE,
# not the screen — it is privacy-independent and fetches no activity snapshot on
# the inline path (privacy mode governs SCREEN visibility only; see
# docs/contributing/developer-notes.md rule 6).
def _bare_mgr():
    from main_logic.core import LLMSessionManager
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.state = SessionStateMachine(lanlan_name="x")
    mgr.lanlan_name = "x"
    mgr._focus_scorer = FocusScorer("x")
    mgr._focus_artifacts_pending = False
    mgr._focus_artifacts_history_start = None
    # Deliberately NO _activity_tracker: the inline path must not touch it
    # (Focus scores the message, not the screen — privacy-independent).
    return mgr


async def test_inline_gate_disabled_clears_regular_charge(monkeypatch):
    _patch_charge(monkeypatch, enter=1.0)
    mgr = _bare_mgr()
    await mgr.state.update_focus(0.6)  # REGULAR, charge building (~0.6)
    assert mgr.state.snapshot()["focus_charge"] > 0
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", False)
    assert await mgr._focus_inline_decision("anything") is False
    assert mgr.state.snapshot()["focus_charge"] == 0.0


async def test_inline_focus_is_privacy_independent(monkeypatch):
    # Focus scores the user's MESSAGE (keyword + cadence), never the screen,
    # so it must NOT be gated on privacy mode and must NOT fetch an activity
    # snapshot. _bare_mgr has no _activity_tracker — if the inline path tried
    # to read the screen it would AttributeError. A strongly vulnerable message
    # still enters FOCUS regardless of any privacy state.
    # A keyword-only message saturates at score = FOCUS_SIGNAL_WEIGHTS["keyword"]
    # (weighted SUM, no denominator). At the PRODUCTION enter (0.6) that would NOT
    # enter single-turn — an accepted trade-off (the lexicon is a cheap signal, must
    # stack with emotion or accumulate). This test only asserts the inline path is
    # privacy-independent (never reads the screen / AttributeErrors), so it pins
    # enter just below the keyword saturation — derived from config so a future
    # weight tweak can't silently break it — to isolate that wiring from the bar.
    keyword_full = config.FOCUS_SIGNAL_WEIGHTS["keyword"]
    _patch_charge(monkeypatch, enter=max(0.0, keyword_full - 0.1))
    mgr = _bare_mgr()
    assert await mgr._focus_inline_decision("好累，一个人，没意思，撑不住了") is True
    assert mgr.state.mode is CognitionMode.FOCUS


async def test_idle_thinking_is_read_only(monkeypatch):
    # _focus_idle_thinking reports whether we're in Focus WITHOUT mutating the
    # charge — the decay is deferred to the post-turn cooldown.
    _patch_charge(monkeypatch, enter=1.0)
    mgr = _bare_mgr()
    await mgr.state.update_focus(1.0)  # FOCUS
    charge_before = mgr.state.snapshot()["focus_charge"]
    assert mgr._focus_idle_thinking() is True
    assert mgr.state.snapshot()["focus_charge"] == charge_before  # unchanged
    # disabled → False (and no mutation here either)
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", False)
    assert mgr._focus_idle_thinking() is False


async def test_idle_cooldown_replied_exits_focus_faster(monkeypatch):
    # Inline drives entry; speaking proactive turns (replied=True) cool the
    # episode down to the exit bar in a few ticks. Slow (silent) decays less.
    _patch_charge(monkeypatch, enter=1.0, exit=0.3, idle_silent=0.95, idle_replied=0.6)
    mgr = _bare_mgr()
    await mgr.state.update_focus(1.0)  # inline enter, charge cap 1.0
    snap = mgr.state.snapshot()
    tok, turn = snap["focus_episode_id"], snap["focus_turn_count"]
    assert mgr.state.mode is CognitionMode.FOCUS
    # silent tick barely moves it (×0.95); replied tick spends it (×0.6)
    await mgr._focus_idle_cooldown(replied=False, episode_token=tok, turn_token=turn)
    assert abs(mgr.state.snapshot()["focus_charge"] - 0.95) < 1e-9
    # 0.95 → 0.57 → 0.342 → 0.2052 (<0.3) ⇒ exits on the 3rd replied tick
    for _ in range(2):
        await mgr._focus_idle_cooldown(replied=True, episode_token=tok, turn_token=turn)
        assert mgr.state.mode is CognitionMode.FOCUS
    await mgr._focus_idle_cooldown(replied=True, episode_token=tok, turn_token=turn)
    assert mgr.state.mode is CognitionMode.REGULAR


async def test_idle_cooldown_does_not_spend_hard_cap(monkeypatch):
    # Idle cooldown ticks must NOT count toward FOCUS_HARD_CAP_TURNS (that bounds
    # inline turns). With a tiny hard cap, many silent cooldowns stay in FOCUS as
    # long as the charge holds, instead of force-exiting after hard_cap polls.
    _patch_charge(monkeypatch, enter=1.0, exit=0.1, hard_cap=2, idle_silent=0.99)
    mgr = _bare_mgr()
    await mgr.state.update_focus(1.0)
    snap = mgr.state.snapshot()
    tok, turn = snap["focus_episode_id"], snap["focus_turn_count"]
    for _ in range(5):  # > hard_cap, but cooldown doesn't bump turn_count
        await mgr._focus_idle_cooldown(replied=False, episode_token=tok, turn_token=turn)
    assert mgr.state.mode is CognitionMode.FOCUS  # not force-exited by hard cap
    # entry set turn_count=1; cooldown ticks (count_turn=False) never bumped it
    assert mgr.state.snapshot()["focus_turn_count"] == 1


async def test_idle_cooldown_skips_when_episode_changed(monkeypatch):
    # Race guard: if the observed episode is no longer current (inline exited /
    # re-entered while the proactive turn finished), the stale cooldown is a no-op.
    _patch_charge(monkeypatch, enter=1.0, idle_replied=0.6)
    mgr = _bare_mgr()
    await mgr.state.update_focus(1.0)  # FOCUS, episode A
    charge_now = mgr.state.snapshot()["focus_charge"]
    await mgr._focus_idle_cooldown(replied=True, episode_token="stale-other-episode")
    assert mgr.state.snapshot()["focus_charge"] == charge_now  # untouched


# ── 7. per-user master switch (对话设置 → focusCognitionEnabled) ─────────────
# A new conversation setting gates 凝神 entirely: off ⇒ never enters Focus and any
# residual charge is cleared, exactly like the global FOCUS_MODE_ENABLED flag.
# Defaults to on when unset. master emotion read is independent (not gated here).
def _stub_user_focus_setting(monkeypatch, *, enabled):
    settings = {} if enabled is None else {"focusCognitionEnabled": enabled}

    async def _aload():
        return settings

    # String targets so we don't import main_logic.core a second way (the module
    # is already pulled in via `from main_logic.core import ...` in _bare_mgr).
    monkeypatch.setattr("main_logic.core.aload_global_conversation_settings", _aload)
    monkeypatch.setattr("main_logic.core.load_global_conversation_settings", lambda: settings)


async def test_inline_gate_user_setting_off_blocks_and_clears(monkeypatch):
    # Global flag on, but the user turned the per-user 凝神 switch off → a strongly
    # vulnerable message must NOT enter Focus, and residual charge is cleared.
    _patch_charge(monkeypatch, enter=1.0)
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", True)
    _stub_user_focus_setting(monkeypatch, enabled=False)
    mgr = _bare_mgr()
    await mgr.state.update_focus(0.6)  # build some charge first
    assert mgr.state.snapshot()["focus_charge"] > 0
    assert await mgr._focus_inline_decision("好累，一个人，撑不住了") is False
    assert mgr.state.mode is CognitionMode.REGULAR
    assert mgr.state.snapshot()["focus_charge"] == 0.0


async def test_inline_gate_user_setting_default_on_allows(monkeypatch):
    # Setting absent ⇒ defaults to on, so the global flag alone governs entry.
    _patch_charge(monkeypatch, enter=1.0)
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", True)
    _stub_user_focus_setting(monkeypatch, enabled=None)  # key absent
    mgr = _bare_mgr()
    assert await mgr._focus_inline_decision("好累，一个人，没意思，撑不住了") is True
    assert mgr.state.mode is CognitionMode.FOCUS


async def test_idle_thinking_honors_user_setting(monkeypatch):
    # Even while state.mode is still FOCUS, a proactive turn must not run
    # thinking-on once the user switched 凝神 off (defense in depth).
    _patch_charge(monkeypatch, enter=1.0)
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", True)
    _stub_user_focus_setting(monkeypatch, enabled=True)
    mgr = _bare_mgr()
    await mgr.state.update_focus(1.0)  # FOCUS
    assert mgr._focus_idle_thinking() is True
    _stub_user_focus_setting(monkeypatch, enabled=False)
    assert mgr._focus_idle_thinking() is False


async def test_idle_cooldown_skips_when_no_episode_observed(monkeypatch):
    # A proactive turn that ran while REGULAR observes no episode (token=None).
    # The cooldown must NOT erode the pre-entry accumulator the inline path is
    # building toward ENTER — entering Focus is the inline path's job alone.
    _patch_charge(monkeypatch, enter=1.0, idle_silent=0.95, idle_replied=0.6)
    mgr = _bare_mgr()
    await mgr.state.update_focus(0.6)  # REGULAR, charge building under the bar
    assert mgr.state.mode is CognitionMode.REGULAR
    snap = mgr.state.snapshot()
    assert snap["focus_episode_id"] is None
    charge_now = snap["focus_charge"]
    await mgr._focus_idle_cooldown(
        replied=True, episode_token=None, turn_token=snap["focus_turn_count"],
    )
    assert mgr.state.snapshot()["focus_charge"] == charge_now  # untouched
    assert mgr.state.mode is CognitionMode.REGULAR


async def test_idle_cooldown_skips_when_inline_recharged_same_episode(monkeypatch):
    # Turn race within ONE episode: a user message lands mid-flight and the
    # inline path recharges the same episode (turn count bumps). The stale
    # proactive cooldown must not decay that fresh, user-driven charge — the
    # turn-count token mismatch makes it a no-op even though the episode id matches.
    _patch_charge(monkeypatch, retention=0.5, enter=1.0, idle_replied=0.6)
    mgr = _bare_mgr()
    await mgr.state.update_focus(1.0)  # FOCUS, episode A, turn_count 1
    snap = mgr.state.snapshot()
    ep_tok, turn_tok = snap["focus_episode_id"], snap["focus_turn_count"]
    # Inline turn recharges the same episode while the proactive turn finishes.
    await mgr.state.update_focus(0.5)  # same episode A, turn_count -> 2
    fresh = mgr.state.snapshot()
    assert fresh["focus_episode_id"] == ep_tok          # same episode
    assert fresh["focus_turn_count"] != turn_tok        # but turn moved
    charge_fresh = fresh["focus_charge"]
    await mgr._focus_idle_cooldown(
        replied=True, episode_token=ep_tok, turn_token=turn_tok,
    )
    assert mgr.state.snapshot()["focus_charge"] == charge_fresh  # not decayed


async def test_idle_cooldown_skips_after_user_takeover(monkeypatch):
    # User typed during the proactive turn: USER_INPUT flips owner→USER and
    # aborts the turn, but the inline focus update lands LATER (after mini-game /
    # agent-callback handling), so the episode + turn token still match when the
    # aborted proactive turn runs its cooldown. That stale tick must not decay
    # the charge before the user's own message is scored — owner==USER gates it.
    _patch_charge(monkeypatch, enter=1.0, idle_silent=0.7, idle_replied=0.6)
    mgr = _bare_mgr()
    await mgr.state.update_focus(1.0)  # FOCUS, episode A, turn token unchanged
    snap = mgr.state.snapshot()
    ep_tok, turn_tok = snap["focus_episode_id"], snap["focus_turn_count"]
    charge_now = snap["focus_charge"]
    mgr.state.owner = TurnOwner.USER  # user took over mid-turn (USER_INPUT)
    await mgr._focus_idle_cooldown(
        replied=False, episode_token=ep_tok, turn_token=turn_tok,
    )
    assert mgr.state.snapshot()["focus_charge"] == charge_now  # not decayed
    assert mgr.state.mode is CognitionMode.FOCUS


async def test_idle_cooldown_replied_decays_even_when_owner_user(monkeypatch):
    # A proactive turn that DID commit a reply (replied=True) genuinely spent the
    # episode. Even if the user fired back fast enough to flip owner→USER before
    # the cooldown ran (and the inline focus update hasn't landed yet, so the
    # token still matches), the replied retention must STILL apply — the
    # owner==USER shortcut is only for UNDELIVERED (replied=False) turns.
    _patch_charge(monkeypatch, enter=1.0, idle_silent=0.7, idle_replied=0.6)
    mgr = _bare_mgr()
    await mgr.state.update_focus(1.0)  # FOCUS, charge 1.0
    snap = mgr.state.snapshot()
    ep_tok, turn_tok = snap["focus_episode_id"], snap["focus_turn_count"]
    mgr.state.owner = TurnOwner.USER  # user fired back after the reply committed
    await mgr._focus_idle_cooldown(
        replied=True, episode_token=ep_tok, turn_token=turn_tok,
    )
    assert abs(mgr.state.snapshot()["focus_charge"] - 0.6) < 1e-9  # still decayed ×0.6


async def test_idle_cooldown_disabled_clears_regular_charge(monkeypatch):
    _patch_charge(monkeypatch, enter=1.0)
    mgr = _bare_mgr()
    await mgr.state.update_focus(0.6)
    assert mgr.state.snapshot()["focus_charge"] > 0
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", False)
    await mgr._focus_idle_cooldown(replied=False, episode_token=None)
    assert mgr.state.snapshot()["focus_charge"] == 0.0


async def test_update_focus_retention_override_and_count_turn(monkeypatch):
    # retention_override replaces FOCUS_CHARGE_RETENTION for one tick; count_turn
    # =False decays without bumping the hard-cap turn counter.
    _patch_charge(monkeypatch, retention=0.5, enter=1.0, exit=0.01)
    sm = SessionStateMachine(lanlan_name="x")
    await sm.update_focus(1.0)  # FOCUS, charge 1.0, turn_count 1
    tc_before = sm.snapshot()["focus_turn_count"]
    await sm.update_focus(0.0, retention_override=0.9, count_turn=False)
    assert abs(sm.snapshot()["focus_charge"] - 0.9) < 1e-9  # override applied
    assert sm.snapshot()["focus_turn_count"] == tc_before    # not bumped


# ── 8. 凝神退出：历史 thinking + 已闭合 tool call 清理 ─────────────────────
# 凝神退出(任何路径)时，把上一个 episode 留在历史里的 reasoning_content + 已闭合
# tool call 配对清掉，防止带偏退出后的 REGULAR 对话乃至新 session。
def _tool_pair(call_id="c1", *, reasoning=None, name="t"):
    """Build one assistant tool_calls message + the following tool result (closed pair)."""
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": call_id, "type": "function",
                        "function": {"name": name, "arguments": "{}"}}],
    }
    if reasoning is not None:
        assistant["reasoning_content"] = reasoning
    result = {"role": "tool", "tool_call_id": call_id, "name": name, "content": "ok"}
    return assistant, result


def test_purge_closed_tool_calls():
    """A closed tool-call pair (assistant tool_calls + tool result) is deleted as a
    whole, together with the reasoning_content riding on it; plain Human/AI/System
    messages are kept."""
    from main_logic.core import _purge_closed_tool_calls
    from utils.llm_client import SystemMessage, HumanMessage, AIMessage

    a, r = _tool_pair("c1", reasoning="让我想想要不要调工具…")
    history = [SystemMessage(content="sys"), HumanMessage(content="查天气"), a, r,
               AIMessage(content="今天晴")]
    removed = _purge_closed_tool_calls(history)
    assert removed == 2
    # 只剩 system / human / ai，dict(tool 配对)及 reasoning_content 全没了
    assert all(not isinstance(m, dict) for m in history)
    assert [type(m).__name__ for m in history] == ["SystemMessage", "HumanMessage", "AIMessage"]


def test_purge_keeps_unclosed_and_empty():
    from main_logic.core import _purge_closed_tool_calls
    from utils.llm_client import HumanMessage
    # 未闭合：assistant 有 call 但没有对应 tool result → 保留(不破坏在途状态)
    a, _ = _tool_pair("c9")
    history = [HumanMessage(content="hi"), a]
    assert _purge_closed_tool_calls(history) == 0
    assert len(history) == 2
    assert _purge_closed_tool_calls([]) == 0


def test_purge_multiple_calls_one_turn():
    from main_logic.core import _purge_closed_tool_calls
    # 一个 assistant 两个 call + 两条 result → 全闭合，删 3 条
    history = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "f", "arguments": "{}"}},
            {"id": "b", "type": "function", "function": {"name": "g", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "name": "f", "content": "1"},
        {"role": "tool", "tool_call_id": "b", "name": "g", "content": "2"},
    ]
    assert _purge_closed_tool_calls(history) == 3
    assert history == []


def test_purge_respects_start():
    """start confines the purge to the episode's history suffix: closed tool calls before start are kept."""
    from main_logic.core import _purge_closed_tool_calls
    pre_a, pre_r = _tool_pair("pre")
    ep_a, ep_r = _tool_pair("ep", reasoning="…")
    history = [pre_a, pre_r, ep_a, ep_r]
    # start=2 → 只清 index>=2 的那对，保留 Focus 之前的
    assert _purge_closed_tool_calls(history, start=2) == 2
    assert history == [pre_a, pre_r]
    # start 越界 → no-op（历史已被 wipe/缩短的兜底）
    assert _purge_closed_tool_calls(history, start=99) == 0
    assert history == [pre_a, pre_r]


def _purge_mgr(monkeypatch, history):
    """Bare LLMSessionManager + a fake OmniOfflineClient carrying _conversation_history."""
    from main_logic.core import LLMSessionManager
    from main_logic.omni_offline_client import OmniOfflineClient
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "x"
    mgr.state = SessionStateMachine(lanlan_name="x")
    mgr._focus_artifacts_pending = False
    mgr._focus_artifacts_history_start = None
    sess = OmniOfflineClient.__new__(OmniOfflineClient)
    sess._conversation_history = history
    mgr.session = sess

    async def _noop(*a, **k):
        return None
    mgr._push_focus_indicator = _noop
    mgr._push_focus_charge = _noop
    return mgr, sess


async def test_maybe_purge_only_after_exit(monkeypatch):
    """_maybe_purge cleans once only when Focus was entered (armed) AND has dropped
    back to REGULAR; no-ops when not armed / still in FOCUS; after cleaning it
    disarms and is idempotent."""
    _patch_charge(monkeypatch, enter=1.0, exit=0.3)
    a, r = _tool_pair("c1", reasoning="…")
    mgr, sess = _purge_mgr(monkeypatch, [a, r])

    # 没进过 focus（未 arm）→ 不清
    await mgr._maybe_purge_focus_artifacts()
    assert len(sess._conversation_history) == 2

    # arm 且仍在 FOCUS → 不清（残留还在用）
    mgr._focus_artifacts_pending = True
    await mgr.state.update_focus(1.0)
    assert mgr.state.mode is CognitionMode.FOCUS
    await mgr._maybe_purge_focus_artifacts()
    assert len(sess._conversation_history) == 2

    # silent 退出回 REGULAR → 清 + disarm
    await mgr.state.clear_focus()
    assert mgr.state.mode is CognitionMode.REGULAR
    await mgr._maybe_purge_focus_artifacts()
    assert sess._conversation_history == []
    assert mgr._focus_artifacts_pending is False
    # 幂等：再调不报错、无副作用
    await mgr._maybe_purge_focus_artifacts()
    assert sess._conversation_history == []


async def test_idle_cooldown_exit_purges_history(monkeypatch):
    """A proactive idle cooldown that decays Focus out must also purge the episode
    history artifacts — covering the exit path outside the inline decision, so a
    later proactive/greeting prompt_ephemeral can't replay them."""
    _patch_charge(monkeypatch, enter=1.0, exit=0.3, idle_replied=0.6, idle_silent=0.6)
    a, r = _tool_pair("c1", reasoning="…")
    mgr, sess = _purge_mgr(monkeypatch, [a, r])
    # 模拟 inline 已进 FOCUS 并 arm（idle 路径自身不会 enter）
    mgr._focus_artifacts_pending = True
    mgr._focus_artifacts_history_start = 0
    await mgr.state.update_focus(1.0)  # FOCUS, charge 1.0
    snap = mgr.state.snapshot()
    tok, turn = snap["focus_episode_id"], snap["focus_turn_count"]
    assert mgr.state.mode is CognitionMode.FOCUS

    # 第一次 cooldown：1.0*0.6=0.6 ≥ exit 0.3 → 仍 FOCUS，历史不动
    await mgr._focus_idle_cooldown(replied=True, episode_token=tok, turn_token=turn)
    assert mgr.state.mode is CognitionMode.FOCUS
    assert len(sess._conversation_history) == 2

    # 继续衰减直到退出：0.36 → 0.216 < 0.3 → EXIT 时清历史
    await mgr._focus_idle_cooldown(replied=True, episode_token=tok, turn_token=turn)
    await mgr._focus_idle_cooldown(replied=True, episode_token=tok, turn_token=turn)
    assert mgr.state.mode is CognitionMode.REGULAR
    assert sess._conversation_history == []
    assert mgr._focus_artifacts_pending is False


async def test_inline_decision_purges_episode_only(monkeypatch):
    """inline end-to-end + episode-scope: entering FOCUS records the history start;
    exiting purges only the closed tool calls produced during the episode, keeping
    the plain tool calls from before Focus."""
    from main_logic.omni_offline_client import OmniOfflineClient
    keyword_full = config.FOCUS_SIGNAL_WEIGHTS["keyword"]
    _patch_charge(monkeypatch, enter=max(0.0, keyword_full - 0.1), exit=0.3, retention=0.5)
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", True)
    _stub_user_focus_setting(monkeypatch, enabled=True)

    # Focus 之前就有一对普通(非 thinking) tool call —— 必须保留
    pre_a, pre_r = _tool_pair("pre", name="weather")
    mgr = _bare_mgr()
    sess = OmniOfflineClient.__new__(OmniOfflineClient)
    sess._conversation_history = [pre_a, pre_r]
    mgr.session = sess

    async def _noop(*x, **k):
        return None
    mgr._push_focus_indicator = _noop
    mgr._push_focus_charge = _noop

    # vulnerable message → enter FOCUS；记下 history 起点(=2)
    assert await mgr._focus_inline_decision("好累，一个人，没意思，撑不住了") is True
    assert mgr.state.mode is CognitionMode.FOCUS
    assert mgr._focus_artifacts_history_start == 2
    # 模拟 episode 期间又产生一对(带 reasoning_content 的)闭合 tool call
    ep_a, ep_r = _tool_pair("ep", reasoning="思考链…")
    sess._conversation_history.extend([ep_a, ep_r])

    # neutral message → 衰减出 FOCUS → REGULAR → 只清 episode 期间的(index>=2)
    assert await mgr._focus_inline_decision("嗯那个文件改好了发你了") is False
    assert mgr.state.mode is CognitionMode.REGULAR
    assert sess._conversation_history == [pre_a, pre_r]  # Focus 前的保留
    assert mgr._focus_artifacts_pending is False
    assert mgr._focus_artifacts_history_start is None
