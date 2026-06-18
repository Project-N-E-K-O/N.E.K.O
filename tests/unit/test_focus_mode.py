"""Focus mode v1 unit tests: hysteresis state machine + signal scorer + lexicon scans.

Coverage:
1. ``_focus_decide`` pure leaky-accumulator transition: strong-single enter /
   scattered-cue accumulation / charge cap / decayed exit / noise-doesn't-stick /
   hard-cap exit / topic-switch exit-and-clear.
2. ``FocusScorer``: keyword/cadence/silence/open_thread sub-signals, inline
   vs idle path applicability + weight renormalisation, cadence baseline roll.
3. ``SessionStateMachine.update_focus``: async enter/exit, FOCUS_EXIT payload,
   reset clearing, master-switch-off degradation.
4. ``prompts_focus`` lexicon scans: vulnerability count, cross-locale (mixed
   language) scanning, topic-switch anchoring.
5. ``stream_text`` thinking-on threading (Path A wiring).
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
    _focus_decide,
    _FocusAction,
)


# ── helpers ─────────────────────────────────────────────────────────
def _th(retention=0.5, enter=1.0, exit=0.3, hard_cap_turns=8, enabled=True):
    return FocusThresholds(
        enabled=enabled, retention=retention, enter=enter, exit=exit,
        hard_cap_turns=hard_cap_turns,
    )


class _Snap:
    """Minimal ActivitySnapshot stand-in for the scorer (duck-typed)."""

    def __init__(self, *, seconds_since_user_msg=None, unfinished_thread=None,
                 open_threads=None):
        self.seconds_since_user_msg = seconds_since_user_msg
        self.unfinished_thread = unfinished_thread
        self.open_threads = open_threads or []


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


# ── 2. FocusScorer ──────────────────────────────────────────────────
def test_scorer_keyword_inline():
    s = FocusScorer("x")
    res = s.score(_Snap(), user_text="今天好累，感觉一个人撑不住了")
    assert res.signals["keyword"] is not None and res.signals["keyword"] > 0
    assert res.signals["silence"] is None  # inline path: silence N/A
    assert res.score > 0


def test_scorer_no_signal_is_zero():
    s = FocusScorer("x")
    res = s.score(_Snap(), user_text="嗯，那个文件我改好了发你了")
    # No vulnerability keyword, no open thread, cadence not enough samples.
    assert res.signals["keyword"] == 0.0
    assert res.score == 0.0


def test_scorer_cadence_drop_after_baseline():
    s = FocusScorer("x")
    # Feed long messages to build a baseline (each call appends after scoring).
    for _ in range(4):
        s.score(_Snap(), user_text="这是一段比较长的正常聊天消息内容大概三十个字符以上")
    res = s.score(_Snap(), user_text="嗯。")
    assert res.signals["cadence"] is not None and res.signals["cadence"] > 0.5


def test_scorer_cadence_none_without_baseline():
    s = FocusScorer("x")
    res = s.score(_Snap(), user_text="嗯。")
    assert res.signals["cadence"] is None  # below FOCUS_CADENCE_MIN_SAMPLES


def test_scorer_idle_silence_and_renorm():
    s = FocusScorer("x")
    # Idle path (user_text=None): only silence + open_thread apply.
    res = s.score(_Snap(seconds_since_user_msg=config.FOCUS_SILENCE_FULL_SECONDS + 10),
                  user_text=None)
    assert res.signals["keyword"] is None and res.signals["cadence"] is None
    assert res.signals["silence"] == 1.0
    # silence weight 0.2 + open_thread 0.15 (=0) → renorm: 1.0*0.2/(0.35)=0.571
    assert 0.5 < res.score < 0.62


def test_scorer_open_thread_lifts_idle_score():
    s = FocusScorer("x")
    res = s.score(
        _Snap(seconds_since_user_msg=config.FOCUS_SILENCE_FULL_SECONDS + 10,
              open_threads=["上次没聊完的换工作的事"]),
        user_text=None,
    )
    assert res.signals["open_thread"] == 1.0
    assert res.score == 1.0  # both applicable idle signals saturated


def test_scorer_open_thread_idle_only():
    # On the inline path the just-arrived message has already cleared the
    # tracker's unfinished_thread, so open_thread is N/A (None) there — it
    # must not be a structural 0 that dilutes the inline average.
    s = FocusScorer("x")
    res = s.score(_Snap(open_threads=["残留的开放话题"]), user_text="今天好累")
    assert res.signals["open_thread"] is None


# ── 3. SessionStateMachine.update_focus (async) ─────────────────────
def _patch_charge(monkeypatch, *, retention=0.5, enter=1.0, exit=0.3, hard_cap=99):
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", True)
    monkeypatch.setattr(config, "FOCUS_CHARGE_RETENTION", retention)
    monkeypatch.setattr(config, "FOCUS_CHARGE_ENTER", enter)
    monkeypatch.setattr(config, "FOCUS_CHARGE_EXIT", exit)
    monkeypatch.setattr(config, "FOCUS_HARD_CAP_TURNS", hard_cap)


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
    """The thinking-on override decision, vision guard, and provider-extra
    preservation. ``stream_text`` applies this before streaming: thinking-on
    only when focus is active AND not on a vision model; and when it does
    override, it strips only thinking keys (keeps e.g. web_search)."""
    from main_logic.omni_offline_client import OmniOfflineClient as _C
    # pure-thinking provider → override drops to None (nothing non-thinking left)
    assert _C._focus_stream_overrides(True, False, "claude-sonnet-4-6") == {"extra_body": None}
    # unknown model → no resolved extra_body → None
    assert _C._focus_stream_overrides(True, False, "test-model") == {"extra_body": None}
    # step-2-mini ships a web_search tool → it MUST survive (not nuked to None)
    so = _C._focus_stream_overrides(True, False, "step-2-mini")
    assert so["extra_body"] is not None and "tools" in so["extra_body"]
    # vision guard / not-thinking → no override at all
    assert _C._focus_stream_overrides(True, True, "step-2-mini") == {}
    assert _C._focus_stream_overrides(False, False, "step-2-mini") == {}


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
    overrides = OmniOfflineClient._focus_stream_overrides(True, False, c.model)
    await _drain(c._astream_visible_with_tools(["m"], **overrides))
    assert captured[-1].get("extra_body", "MISSING") is None

    # regular turn: no extra_body threaded
    c2 = _make_client()
    await _drain(c2._astream_visible_with_tools(["m"], **OmniOfflineClient._focus_stream_overrides(False, False, c2.model)))
    assert "extra_body" not in captured[-1]


async def test_check_repetition_resets_vision_guard():
    """Repetition recovery wipes _conversation_history → the sticky Focus vision
    guard must recompute from the only thing that survives a wipe: a real
    persistent vision-model switch. Shared-model (no switch) clears it; a
    committed separate vision model keeps it (the switch is irreversible)."""
    from main_logic.omni_offline_client import OmniOfflineClient

    def _mk(committed):
        c = OmniOfflineClient.__new__(OmniOfflineClient)
        c._recent_responses = ["同一句重复回复", "同一句重复回复"]
        c._max_recent_responses = 5
        c._repetition_threshold = 0.5
        c._conversation_history = []
        c.on_repetition_detected = None
        c._focus_vision_committed = committed
        c._focus_images_seen = True  # set earlier by an image-bearing turn
        return c

    # shared-model profile (no persistent switch) → flag clears after wipe
    c1 = _mk(False)
    assert await c1._check_repetition("同一句重复回复") is True
    assert c1._focus_images_seen is False
    # separate vision model committed → flag survives (irreversible switch)
    c2 = _mk(True)
    assert await c2._check_repetition("同一句重复回复") is True
    assert c2._focus_images_seen is True


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
    _patch_charge(monkeypatch, enter=1.0)
    mgr = _bare_mgr()
    assert await mgr._focus_inline_decision("好累，一个人，没意思，撑不住了") is True
    assert mgr.state.mode is CognitionMode.FOCUS


async def test_idle_gate_snapshot_none_preserves_charge(monkeypatch):
    # No snapshot this idle tick (tracker unavailable / privacy nulls SCREEN
    # data): the idle path skips scoring but must NOT clear the accumulator —
    # the privacy-independent inline path keeps driving the episode, so
    # clearing here would wipe a legitimate in-progress Focus.
    _patch_charge(monkeypatch, enter=1.0)  # leaves FOCUS_MODE_ENABLED True
    mgr = _bare_mgr()
    await mgr.state.update_focus(0.6)
    _charge_before = mgr.state.snapshot()["focus_charge"]
    assert _charge_before > 0
    assert await mgr._focus_idle_decision(None) is False
    assert mgr.state.snapshot()["focus_charge"] == _charge_before  # preserved


async def test_idle_gate_disabled_clears_regular_charge(monkeypatch):
    _patch_charge(monkeypatch, enter=1.0)
    mgr = _bare_mgr()
    await mgr.state.update_focus(0.6)
    assert mgr.state.snapshot()["focus_charge"] > 0
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", False)
    assert await mgr._focus_idle_decision(_Snap(seconds_since_user_msg=300)) is False
    assert mgr.state.snapshot()["focus_charge"] == 0.0
