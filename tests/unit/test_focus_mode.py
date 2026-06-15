"""Focus mode v1 unit tests: hysteresis state machine + signal scorer + lexicon scans.

Coverage:
1. ``_focus_decide`` pure hysteresis: enter / stay / low-streak exit /
   hard-cap exit / topic-switch exit / asymmetric (Schmitt) thresholds.
2. ``FocusScorer``: keyword/cadence/silence/open_thread sub-signals, inline
   vs idle path applicability + weight renormalisation, cadence baseline roll.
3. ``SessionStateMachine.update_focus``: async enter/exit, FOCUS_EXIT payload,
   reset clearing, master-switch-off degradation.
4. ``prompts_focus`` lexicon scans: vulnerability count, topic-switch anchoring,
   locale fallback.
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
def _th(t_in=0.6, t_out=0.2, exit_low_streak=3, hard_cap_turns=8, enabled=True):
    return FocusThresholds(
        enabled=enabled, t_in=t_in, t_out=t_out,
        exit_low_streak=exit_low_streak, hard_cap_turns=hard_cap_turns,
    )


class _Snap:
    """Minimal ActivitySnapshot stand-in for the scorer (duck-typed)."""

    def __init__(self, *, seconds_since_user_msg=None, unfinished_thread=None,
                 open_threads=None):
        self.seconds_since_user_msg = seconds_since_user_msg
        self.unfinished_thread = unfinished_thread
        self.open_threads = open_threads or []


# ── 1. pure hysteresis ──────────────────────────────────────────────
def test_decide_enter_on_high_score():
    d = _focus_decide(mode=CognitionMode.REGULAR, focus_turn_count=0, low_streak=0,
                      score=0.7, topic_changed=False, th=_th())
    assert d.action is _FocusAction.ENTER
    assert d.turn_count == 1 and d.low_streak == 0


def test_decide_stay_regular_below_t_in():
    d = _focus_decide(mode=CognitionMode.REGULAR, focus_turn_count=0, low_streak=0,
                      score=0.6, topic_changed=False, th=_th())  # not strictly >
    assert d.action is _FocusAction.STAY
    assert d.turn_count == 0


def test_decide_focus_stays_above_t_out():
    d = _focus_decide(mode=CognitionMode.FOCUS, focus_turn_count=1, low_streak=0,
                      score=0.3, topic_changed=False, th=_th())
    assert d.action is _FocusAction.STAY
    assert d.turn_count == 2 and d.low_streak == 0


def test_decide_hysteresis_band_does_not_exit():
    # score between t_out and t_in keeps focus alive (no flip-flop).
    d = _focus_decide(mode=CognitionMode.FOCUS, focus_turn_count=2, low_streak=2,
                      score=0.4, topic_changed=False, th=_th())
    assert d.action is _FocusAction.STAY
    assert d.low_streak == 0  # reset because score >= t_out


def test_decide_low_streak_exit():
    # 3rd consecutive sub-t_out turn exits (exit_low_streak=3).
    d = _focus_decide(mode=CognitionMode.FOCUS, focus_turn_count=4, low_streak=2,
                      score=0.1, topic_changed=False, th=_th())
    assert d.action is _FocusAction.EXIT
    assert d.reason == "low_streak"


def test_decide_low_streak_not_yet():
    d = _focus_decide(mode=CognitionMode.FOCUS, focus_turn_count=4, low_streak=1,
                      score=0.1, topic_changed=False, th=_th())
    assert d.action is _FocusAction.STAY
    assert d.low_streak == 2


def test_decide_hard_cap_exit():
    d = _focus_decide(mode=CognitionMode.FOCUS, focus_turn_count=8, low_streak=0,
                      score=0.9, topic_changed=False, th=_th(hard_cap_turns=8))
    assert d.action is _FocusAction.EXIT
    assert d.reason == "hard_cap"


def test_decide_topic_switch_exit_overrides_high_score():
    d = _focus_decide(mode=CognitionMode.FOCUS, focus_turn_count=1, low_streak=0,
                      score=0.95, topic_changed=True, th=_th())
    assert d.action is _FocusAction.EXIT
    assert d.reason == "topic_switch"


def test_decide_hard_cap_yields_exactly_n_focus_turns():
    # Simulate a run that never drops below t_out: should produce exactly
    # hard_cap_turns thinking-on turns then exit.
    th = _th(hard_cap_turns=4)
    mode = CognitionMode.REGULAR
    count, low = 0, 0
    focus_turns = 0
    for _ in range(10):
        d = _focus_decide(mode=mode, focus_turn_count=count, low_streak=low,
                          score=0.9, topic_changed=False, th=th)
        if d.action is _FocusAction.ENTER:
            mode = CognitionMode.FOCUS
            count, low = d.turn_count, d.low_streak
            focus_turns += 1
        elif d.action is _FocusAction.STAY and mode is CognitionMode.FOCUS:
            count, low = d.turn_count, d.low_streak
            focus_turns += 1
        elif d.action is _FocusAction.EXIT:
            break
    assert focus_turns == 4


# ── 2. FocusScorer ──────────────────────────────────────────────────
def test_scorer_keyword_inline():
    s = FocusScorer("x")
    res = s.score(_Snap(), user_text="今天好累，感觉一个人撑不住了", lang="zh")
    assert res.signals["keyword"] is not None and res.signals["keyword"] > 0
    assert res.signals["silence"] is None  # inline path: silence N/A
    assert res.score > 0


def test_scorer_no_signal_is_zero():
    s = FocusScorer("x")
    res = s.score(_Snap(), user_text="嗯，那个文件我改好了发你了", lang="zh")
    # No vulnerability keyword, no open thread, cadence not enough samples.
    assert res.signals["keyword"] == 0.0
    assert res.score == 0.0


def test_scorer_cadence_drop_after_baseline():
    s = FocusScorer("x")
    # Feed long messages to build a baseline (each call appends after scoring).
    for _ in range(4):
        s.score(_Snap(), user_text="这是一段比较长的正常聊天消息内容大概三十个字符以上", lang="zh")
    res = s.score(_Snap(), user_text="嗯。", lang="zh")
    assert res.signals["cadence"] is not None and res.signals["cadence"] > 0.5


def test_scorer_cadence_none_without_baseline():
    s = FocusScorer("x")
    res = s.score(_Snap(), user_text="嗯。", lang="zh")
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


# ── 3. SessionStateMachine.update_focus (async) ─────────────────────
async def test_sm_enter_and_exit_cycle(monkeypatch):
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", True)
    monkeypatch.setattr(config, "FOCUS_SCORE_T_IN", 0.6)
    monkeypatch.setattr(config, "FOCUS_SCORE_T_OUT", 0.2)
    monkeypatch.setattr(config, "FOCUS_EXIT_LOW_STREAK", 2)
    monkeypatch.setattr(config, "FOCUS_HARD_CAP_TURNS", 99)
    sm = SessionStateMachine(lanlan_name="x")
    events = []
    sm.subscribe(None, lambda ev, pl: events.append((ev, pl)))

    assert await sm.update_focus(0.9) is CognitionMode.FOCUS
    assert sm.mode is CognitionMode.FOCUS
    assert events[0][0] is SessionEvent.FOCUS_ENTER
    ep_id = events[0][1]["episode_id"]
    assert ep_id and ep_id.startswith("x-")

    # one low turn — not yet exit
    assert await sm.update_focus(0.05) is CognitionMode.FOCUS
    # second consecutive low turn — exit
    assert await sm.update_focus(0.05) is CognitionMode.REGULAR
    assert sm.mode is CognitionMode.REGULAR
    exit_evt = [e for e in events if e[0] is SessionEvent.FOCUS_EXIT]
    assert exit_evt and exit_evt[0][1]["episode_id"] == ep_id
    assert exit_evt[0][1]["reason"] == "low_streak"
    assert "episode_started_at" in exit_evt[0][1]


async def test_sm_hard_cap_exit(monkeypatch):
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", True)
    monkeypatch.setattr(config, "FOCUS_SCORE_T_IN", 0.6)
    monkeypatch.setattr(config, "FOCUS_SCORE_T_OUT", 0.2)
    monkeypatch.setattr(config, "FOCUS_EXIT_LOW_STREAK", 99)
    monkeypatch.setattr(config, "FOCUS_HARD_CAP_TURNS", 3)
    sm = SessionStateMachine(lanlan_name="x")
    modes = [await sm.update_focus(0.9) for _ in range(5)]
    # Cap=3: exactly 3 focus turns then a forced REGULAR exit at turn 4,
    # even though the score never dropped. Turn 5 re-enters as a NEW
    # episode (sustained high score) — the cap bounds episode length, not
    # total focus time (see _focus_decide hard_cap comment).
    assert [m is CognitionMode.FOCUS for m in modes[:4]] == [True, True, True, False]
    assert modes[4] is CognitionMode.FOCUS  # re-entry allowed


async def test_sm_hard_cap_no_reentry_when_score_drops(monkeypatch):
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", True)
    monkeypatch.setattr(config, "FOCUS_SCORE_T_IN", 0.6)
    monkeypatch.setattr(config, "FOCUS_HARD_CAP_TURNS", 3)
    sm = SessionStateMachine(lanlan_name="x")
    for _ in range(3):
        await sm.update_focus(0.9)
    # cap fires; this turn scores low → exits and stays regular
    assert await sm.update_focus(0.1) is CognitionMode.REGULAR
    assert await sm.update_focus(0.1) is CognitionMode.REGULAR


async def test_sm_topic_switch_immediate_exit(monkeypatch):
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", True)
    monkeypatch.setattr(config, "FOCUS_SCORE_T_IN", 0.6)
    sm = SessionStateMachine(lanlan_name="x")
    await sm.update_focus(0.9)
    assert sm.mode is CognitionMode.FOCUS
    assert await sm.update_focus(0.9, topic_changed=True) is CognitionMode.REGULAR


async def test_sm_master_switch_off_is_noop(monkeypatch):
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", False)
    sm = SessionStateMachine(lanlan_name="x")
    assert await sm.update_focus(0.99) is CognitionMode.REGULAR
    assert sm.mode is CognitionMode.REGULAR


async def test_sm_reset_clears_focus(monkeypatch):
    monkeypatch.setattr(config, "FOCUS_MODE_ENABLED", True)
    monkeypatch.setattr(config, "FOCUS_SCORE_T_IN", 0.6)
    sm = SessionStateMachine(lanlan_name="x")
    await sm.update_focus(0.9)
    assert sm.mode is CognitionMode.FOCUS
    await sm.reset(force=True)
    assert sm.mode is CognitionMode.REGULAR
    assert sm.snapshot()["mode"] == "regular"


# ── 4. prompts_focus lexicon ────────────────────────────────────────
def test_vulnerability_keyword_count():
    assert scan_vulnerability_keywords("好累，一个人，没意思", "zh") >= 3
    assert scan_vulnerability_keywords("今天天气不错", "zh") == 0


def test_vulnerability_unknown_locale_falls_back_zh():
    # Unknown lang → zh table (contract: treat unknown as Chinese user).
    assert scan_vulnerability_keywords("好累", "xx") > 0


def test_topic_switch_anchored_at_start():
    assert detect_topic_switch("对了，今天天气怎么样", "zh") is True
    assert detect_topic_switch("by the way, did you eat", "en") is True
    # marker buried mid-sentence is not a pivot
    assert detect_topic_switch("我觉得对了这个想法不错", "zh") is False


# ── 5. stream_text thinking-on threading (Path A wiring) ────────────
async def _drain(agen):
    return [c async for c in agen]


def test_focus_stream_overrides_decision():
    """The thinking-on override decision, including the vision guard.

    This is the contract ``stream_text`` applies before streaming: thinking-on
    only when focus is active AND there are no pending images (a vision model +
    thinking reliably times out, so an image-bearing focus turn stays
    thinking-off)."""
    from main_logic.omni_offline_client import OmniOfflineClient as _C
    assert _C._focus_stream_overrides(True, False) == {"extra_body": None}
    assert _C._focus_stream_overrides(True, True) == {}    # vision guard
    assert _C._focus_stream_overrides(False, False) == {}
    assert _C._focus_stream_overrides(False, True) == {}


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

    # focus turn (no images): _focus_stream_overrides → {"extra_body": None}
    c = _make_client()
    overrides = OmniOfflineClient._focus_stream_overrides(True, False)
    await _drain(c._astream_visible_with_tools(["m"], **overrides))
    assert captured[-1].get("extra_body", "MISSING") is None

    # regular turn: no extra_body threaded
    c2 = _make_client()
    await _drain(c2._astream_visible_with_tools(["m"], **OmniOfflineClient._focus_stream_overrides(False, False)))
    assert "extra_body" not in captured[-1]
