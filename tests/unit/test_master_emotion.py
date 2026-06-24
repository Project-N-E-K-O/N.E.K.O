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

"""Unit tests for the master (user) emotion tracker and its Focus signal."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import config
from main_logic.activity import (
    FocusScorer,
    MasterEmotionReading,
    MasterEmotionTracker,
)
from main_logic.activity.master_emotion import _clamp


@pytest.fixture(autouse=True)
def _disable_reading_ttl(monkeypatch):
    # Most tests pin a fixed epoch-ish now=100.0 for throttle/seq determinism;
    # the real-time TTL gate (time.time() - updated_at) would treat that as long
    # expired. Disable aging by default — the TTL test re-enables it explicitly.
    monkeypatch.setattr(config, "MASTER_EMOTION_READING_TTL_SEC", 0)


# ── _clamp ───────────────────────────────────────────────────────────
def test_clamp_bounds_and_junk():
    assert _clamp(0.5, -1.0, 1.0, 0.0) == 0.5
    assert _clamp(5, -1.0, 1.0, 0.0) == 1.0       # over → hi
    assert _clamp(-9, -1.0, 1.0, 0.0) == -1.0      # under → lo
    assert _clamp("nope", 0.0, 1.0, 0.5) == 0.5    # non-numeric → default
    assert _clamp(None, 0.0, 1.0, 0.5) == 0.5
    assert _clamp(float("nan"), 0.0, 1.0, 0.5) == 0.5  # NaN → default


# ── FocusScorer.emotion signal mapping ───────────────────────────────
def _reading(valence, arousal, complexity=0.0):
    return MasterEmotionReading(
        valence=valence, arousal=arousal, confidence=0.9, updated_at=0.0,
        complexity=complexity,
    )


def test_question_signal_from_complexity():
    s = FocusScorer("t")
    # The cognitive-load bonus comes straight from the model's complexity read,
    # positive-evidence-only (None when absent so it never dilutes emotion).
    assert s._signal_question(_reading(0.0, 0.0, complexity=0.8)) == 0.8
    assert s._signal_question(_reading(0.0, 0.0, complexity=0.0)) is None
    assert s._signal_question(SimpleNamespace(complexity=None)) is None
    assert s._signal_question(None) is None


def test_question_can_trigger_alone_and_merges_with_emotion():
    # A complex objective question with NO distress (neutral valence) still scores
    # on its own — focus = emotion OR cognitive load.
    s = FocusScorer("t")
    res = s.score(user_text="求这道题的极限", emotion_reading=_reading(0.0, 0.2, complexity=0.9))
    assert res.signals["emotion"] is None and res.signals["question"] == 0.9
    # lone present trigger contributes weight×value (no denominator)
    assert abs(res.score - config.FOCUS_SIGNAL_WEIGHTS["question"] * 0.9) < 1e-9
    # Distress + complex question merge via the weighted sum (question lifts
    # the score above the emotion-only value, never dilutes it).
    s2 = FocusScorer("t")
    res2 = s2.score(user_text="想搞懂这道题", emotion_reading=_reading(-0.8, 0.9, complexity=0.8))
    assert res2.signals["keyword"] is None  # no vulnerability word in this text
    assert res2.signals["emotion"] is not None and res2.signals["question"] == 0.8
    assert res2.score > res2.signals["emotion"]


def test_score_negative_when_user_is_happy():
    s = FocusScorer("t")
    res = s.score(user_text="今天超开心的", emotion_reading=_reading(0.9, 0.8))
    assert res.signals["keyword"] is None
    assert res.signals["emotion"] is not None and res.signals["emotion"] < 0
    assert res.signals["cadence"] is None  # gated: a happy turn is not distress evidence
    assert res.score < 0  # a good mood votes Focus DOWN (drains charge)


def test_emotion_signal_distress_is_max():
    s = FocusScorer("t")
    assert s._signal_emotion(_reading(-1.0, 1.0)) == 1.0


def test_emotion_signal_happy_pulls_focus_down():
    s = FocusScorer("t")
    # positive valence is now a SIGNED anti-focus vote (don't intrude on a good
    # mood), capped at -POSITIVE_SCALE. valence +1, arousal 1 → m=1 → -0.5.
    assert s._signal_emotion(_reading(1.0, 1.0)) == -0.5
    # cited floor case: valence +1, arousal 0.3 → m=0.65 → -0.5*0.65 = -0.325.
    assert abs(s._signal_emotion(_reading(1.0, 0.3)) - (-0.325)) < 1e-9


def test_emotion_signal_neutral_is_none():
    s = FocusScorer("t")
    # EXACTLY neutral valence → no vote either way (don't dilute other signals).
    assert s._signal_emotion(_reading(0.0, 1.0)) is None


def test_emotion_signal_mild_positive_is_small_negative():
    s = FocusScorer("t")
    # +0.5, arousal 1 → -(0.5 * 1.0 * 0.5) = -0.25 (half the reach of distress).
    assert abs(s._signal_emotion(_reading(0.5, 1.0)) - (-0.25)) < 1e-9


def test_emotion_signal_calm_negative_still_fires():
    s = FocusScorer("t")
    # Valence DRIVES distress; arousal only amplifies (with a floor). Quiet
    # sadness (strong-negative valence, low arousal) must still produce a solid
    # signal — exactly the vulnerability Focus is meant to catch — not get zeroed
    # out the way a pure arousal×negativity product would.
    calm = s._signal_emotion(_reading(-1.0, 0.1))
    assert calm is not None and calm > 0.4
    # arousal still amplifies: same valence, higher arousal → stronger signal.
    assert s._signal_emotion(_reading(-1.0, 1.0)) > calm


def test_emotion_arousal_floor_knob(monkeypatch):
    s = FocusScorer("t")
    # floor=0 → legacy pure arousal×negativity product (arousal gates): low
    # arousal stays weak, reproducing the old mapping exactly.
    monkeypatch.setattr(config, "FOCUS_EMOTION_AROUSAL_FLOOR", 0.0)
    assert abs(s._signal_emotion(_reading(-1.0, 0.1)) - 0.1) < 1e-9
    # floor=1 → arousal ignored entirely, distress = negativity (pure valence).
    monkeypatch.setattr(config, "FOCUS_EMOTION_AROUSAL_FLOOR", 1.0)
    assert abs(s._signal_emotion(_reading(-0.7, 0.0)) - 0.7) < 1e-9
    assert abs(s._signal_emotion(_reading(-0.7, 1.0)) - 0.7) < 1e-9


def test_emotion_signal_none_when_no_reading():
    s = FocusScorer("t")
    assert s._signal_emotion(None) is None


def test_emotion_signal_none_when_axis_missing():
    s = FocusScorer("t")
    # duck-typed object missing arousal → drops out (None), never crashes
    assert s._signal_emotion(SimpleNamespace(valence=-0.5, arousal=None)) is None


def test_score_includes_emotion_and_drops_when_absent():
    s = FocusScorer("t")
    with_reading = s.score(user_text="嗯。", emotion_reading=_reading(-0.8, 0.7))
    assert with_reading.signals["emotion"] is not None
    # No reading → emotion signal is None and excluded from the weighted average.
    s2 = FocusScorer("t")
    without = s2.score(user_text="嗯。")
    assert without.signals["emotion"] is None


def test_emotion_alone_can_saturate_score():
    # #2: a saturated distress reading (emotion signal 1.0) with no keyword/cadence
    # contributes its full weight to the score (keyword None drops out), so emotion
    # alone — 0.7 ≥ FOCUS_CHARGE_ENTER (0.6) — triggers Focus independently of the
    # vulnerability lexicon.
    s = FocusScorer("t")
    res = s.score(user_text="嗯", emotion_reading=_reading(-1.0, 1.0))
    assert res.signals["keyword"] is None
    assert res.signals["emotion"] == 1.0
    assert abs(res.score - config.FOCUS_SIGNAL_WEIGHTS["emotion"]) < 1e-9


def test_stale_neutral_emotion_does_not_dilute_keyword():
    # #1: a keyword-positive turn with a stale neutral reading scores on keyword
    # alone (emotion None drops out), preserving the single-turn entry that
    # keyword-only scoring used to give.
    s = FocusScorer("t")
    res = s.score(user_text="今天好累，感觉一个人撑不住了", emotion_reading=_reading(0.0, 1.0))
    assert res.signals["emotion"] is None
    assert res.signals["keyword"] is not None and res.signals["keyword"] > 0
    # keyword alone contributes weight×value; not diluted by the stale-neutral 0
    assert abs(res.score - config.FOCUS_SIGNAL_WEIGHTS["keyword"] * res.signals["keyword"]) < 1e-9


def test_cadence_alone_does_not_trigger():
    # cadence amplifies distress evidence, not a trigger: a built baseline + a
    # short neutral reply ("嗯。") with no keyword/emotion must gate cadence out
    # and score 0, not renormalise cadence to a full 1.0.
    s = FocusScorer("t")
    for _ in range(4):
        s.score(user_text="这是一段比较长的正常聊天消息内容大概三十个字符以上")
    res = s.score(user_text="嗯。")
    assert res.signals["cadence"] is None  # gated: no distress evidence present
    assert res.score == 0.0


def test_cadence_counts_when_distress_present():
    # with a distress signal present, cadence is NOT gated and amplifies it.
    s = FocusScorer("t")
    for _ in range(4):
        s.score(user_text="这是一段比较长的正常聊天消息内容大概三十个字符以上")
    res = s.score(user_text="嗯。", emotion_reading=_reading(-0.9, 0.9))
    assert res.signals["cadence"] is not None and res.signals["cadence"] > 0.5
    assert res.signals["emotion"] is not None


def test_reading_expires_after_ttl(monkeypatch):
    fake, _ = _fake_tier('{"valence": -0.9, "arousal": 0.9}')
    _patch_tier(monkeypatch, fake)
    t = MasterEmotionTracker("t")
    asyncio.run(t.analyze("难过", now=100.0))  # updated_at=100.0 (ancient epoch)
    monkeypatch.setattr(config, "MASTER_EMOTION_READING_TTL_SEC", 120.0)
    assert t.latest is None  # ancient updated_at vs real now → expired
    monkeypatch.setattr(config, "MASTER_EMOTION_READING_TTL_SEC", 0)
    assert t.latest is not None  # aging disabled → served again


# ── MasterEmotionTracker._parse robustness ───────────────────────────
def test_parse_valid_json():
    r = MasterEmotionTracker._parse(
        '{"valence": -0.6, "arousal": 0.8, "confidence": 0.7}',
        now=123.0, source="撑不住了",
    )
    assert r is not None
    assert r.valence == -0.6 and r.arousal == 0.8 and r.confidence == 0.7
    assert r.updated_at == 123.0


def test_parse_markdown_wrapped_json():
    # robust_json_loads tolerates ```json fences the emotion tier may emit.
    r = MasterEmotionTracker._parse(
        '```json\n{"valence": 0.2, "arousal": 0.3}\n```',
        now=1.0, source="x",
    )
    assert r is not None
    assert r.valence == 0.2 and r.arousal == 0.3
    assert r.confidence == 0.5  # default when omitted
    # uppercase fence label (```JSON) is tolerated too
    r2 = MasterEmotionTracker._parse(
        '```JSON\n{"valence": -0.1, "arousal": 0.4}\n```', now=1.0, source="x",
    )
    assert r2 is not None and r2.arousal == 0.4


def test_parse_out_of_range_is_clamped():
    r = MasterEmotionTracker._parse(
        '{"valence": -5, "arousal": 9, "confidence": 2}', now=1.0, source="x",
    )
    assert r.valence == -1.0 and r.arousal == 1.0 and r.confidence == 1.0


def test_parse_complexity_field():
    r = MasterEmotionTracker._parse(
        '{"valence": -0.5, "arousal": 0.5, "complexity": 0.9}', now=1.0, source="x",
    )
    assert r is not None and r.complexity == 0.9
    # Missing complexity defaults to 0.0 (no cognitive bonus) — unlike the axes,
    # its absence must NOT reject an otherwise-valid reading.
    r2 = MasterEmotionTracker._parse('{"valence": -0.5, "arousal": 0.5}', now=1.0, source="x")
    assert r2 is not None and r2.complexity == 0.0
    # Out-of-range complexity clamps into [0, 1].
    r3 = MasterEmotionTracker._parse(
        '{"valence": 0, "arousal": 0, "complexity": 9}', now=1.0, source="x",
    )
    assert r3.complexity == 1.0


def test_parse_external_intent_field():
    # external_intent rides this same cheap call as a signal for the agent gate.
    r = MasterEmotionTracker._parse(
        '{"valence": 0, "arousal": 0, "external_intent": 0.8}', now=1.0, source="x",
    )
    assert r is not None and r.external_intent == 0.8
    # CRITICAL — unlike complexity, a MISSING external_intent is None, NOT 0.0.
    # The consuming agent gate must fail open (run the expensive assessment) when
    # it has no usable signal, never read a phantom 0.0 as "confidently no
    # external need" and brake a real tool request. Absence must not reject the reading.
    r2 = MasterEmotionTracker._parse('{"valence": 0, "arousal": 0}', now=1.0, source="x")
    assert r2 is not None and r2.external_intent is None
    # null / non-numeric → None (fail-open), reading still valid.
    r3 = MasterEmotionTracker._parse(
        '{"valence": 0, "arousal": 0, "external_intent": null}', now=1.0, source="x",
    )
    assert r3 is not None and r3.external_intent is None
    r4 = MasterEmotionTracker._parse(
        '{"valence": 0, "arousal": 0, "external_intent": "lots"}', now=1.0, source="x",
    )
    assert r4 is not None and r4.external_intent is None
    # out-of-range clamps into [0, 1] when present.
    r5 = MasterEmotionTracker._parse(
        '{"valence": 0, "arousal": 0, "external_intent": 9}', now=1.0, source="x",
    )
    assert r5.external_intent == 1.0
    r6 = MasterEmotionTracker._parse(
        '{"valence": 0, "arousal": 0, "external_intent": -3}', now=1.0, source="x",
    )
    assert r6.external_intent == 0.0


def test_parse_rejects_garbage():
    assert MasterEmotionTracker._parse("not json at all", now=1.0, source="x") is None
    assert MasterEmotionTracker._parse("[1, 2, 3]", now=1.0, source="x") is None  # not a dict
    assert MasterEmotionTracker._parse('{"mood": "sad"}', now=1.0, source="x") is None  # neither axis


def test_parse_rejects_partial_reading():
    # A partial response must be rejected, NOT defaulted to 0 — a missing arousal
    # would zero the distress signal and misread strong negative affect as calm.
    assert MasterEmotionTracker._parse('{"valence": -0.9}', now=1.0, source="x") is None
    assert MasterEmotionTracker._parse('{"arousal": 0.8}', now=1.0, source="x") is None
    # axis present but null / non-numeric is also incomplete
    assert MasterEmotionTracker._parse('{"valence": -0.9, "arousal": null}', now=1.0, source="x") is None
    assert MasterEmotionTracker._parse('{"valence": "bad", "arousal": 0.5}', now=1.0, source="x") is None
    # both axes present → confidence may still be omitted (defaults)
    ok = MasterEmotionTracker._parse('{"valence": -0.9, "arousal": 0.8}', now=1.0, source="x")
    assert ok is not None and ok.confidence == 0.5


# ── MasterEmotionTracker.analyze (async, throttle, gating) ───────────
def _fake_tier(payload):
    """Build an async stand-in for ``_invoke_emotion_tier`` returning ``payload``
    and counting calls. Patch onto the llm_enrichment module."""
    calls = {"n": 0}

    async def fake(prompt, *, timeout, label):
        calls["n"] += 1
        return payload

    return fake, calls


def _patch_tier(monkeypatch, fake):
    import main_logic.activity.llm_enrichment as le
    monkeypatch.setattr(le, "_invoke_emotion_tier", fake)


def test_analyze_updates_latest(monkeypatch):
    fake, calls = _fake_tier('{"valence": -0.7, "arousal": 0.6, "confidence": 0.8}')
    _patch_tier(monkeypatch, fake)
    t = MasterEmotionTracker("t")
    r = asyncio.run(t.analyze("我今天很难过", now=100.0))
    assert r is not None and t.latest is r
    assert calls["n"] == 1
    assert t.latest.valence == -0.7


def test_analyze_throttled_within_interval(monkeypatch):
    fake, calls = _fake_tier('{"valence": -0.5, "arousal": 0.5}')
    _patch_tier(monkeypatch, fake)
    t = MasterEmotionTracker("t")
    asyncio.run(t.analyze("a", now=100.0))
    # within MASTER_EMOTION_MIN_INTERVAL_SEC → skipped, no model call
    r2 = asyncio.run(t.analyze("b", now=100.0 + config.MASTER_EMOTION_MIN_INTERVAL_SEC - 0.5))
    assert r2 is None
    assert calls["n"] == 1
    # past the interval → fires again
    r3 = asyncio.run(t.analyze("c", now=100.0 + config.MASTER_EMOTION_MIN_INTERVAL_SEC + 0.1))
    assert r3 is not None
    assert calls["n"] == 2


def test_analyze_disabled_is_noop(monkeypatch):
    fake, calls = _fake_tier('{"valence": -0.5, "arousal": 0.5}')
    _patch_tier(monkeypatch, fake)
    monkeypatch.setattr(config, "MASTER_EMOTION_ENABLED", False)
    t = MasterEmotionTracker("t")
    assert asyncio.run(t.analyze("我很难过", now=100.0)) is None
    assert calls["n"] == 0
    assert t.latest is None


def test_analyze_empty_text_is_noop(monkeypatch):
    fake, calls = _fake_tier('{"valence": 0, "arousal": 0}')
    _patch_tier(monkeypatch, fake)
    t = MasterEmotionTracker("t")
    assert asyncio.run(t.analyze("   ", now=100.0)) is None
    assert calls["n"] == 0


def test_analyze_bad_response_keeps_previous(monkeypatch):
    fake_ok, _ = _fake_tier('{"valence": -0.9, "arousal": 0.9}')
    _patch_tier(monkeypatch, fake_ok)
    t = MasterEmotionTracker("t")
    asyncio.run(t.analyze("难过", now=100.0))
    prev = t.latest
    assert prev is not None
    # next call returns junk → latest must stay the previous good reading
    fake_bad, _ = _fake_tier("garbage not json")
    _patch_tier(monkeypatch, fake_bad)
    r = asyncio.run(t.analyze("再说点", now=200.0))
    assert r is None
    assert t.latest is prev


def test_reset_clears_state(monkeypatch):
    fake, _ = _fake_tier('{"valence": -0.5, "arousal": 0.5}')
    _patch_tier(monkeypatch, fake)
    t = MasterEmotionTracker("t")
    asyncio.run(t.analyze("难过", now=100.0))
    assert t.latest is not None
    t.reset()
    assert t.latest is None
    # after reset the throttle is cleared too → an immediate call fires
    r = asyncio.run(t.analyze("难过", now=101.0))
    assert r is not None


def test_analyze_drops_stale_out_of_order(monkeypatch):
    # Simulate a newer analysis (or reset) bumping _seq while this call awaits
    # the slow tier → the older result must be dropped, not written to latest.
    t = MasterEmotionTracker("t")

    async def fake(prompt, *, timeout, label):
        t._seq += 1  # a newer turn kicked off during the await
        return '{"valence": -0.5, "arousal": 0.5}'

    _patch_tier(monkeypatch, fake)
    assert asyncio.run(t.analyze("x", now=100.0)) is None
    assert t.latest is None


def test_latest_hidden_when_switch_off(monkeypatch):
    fake, _ = _fake_tier('{"valence": -0.5, "arousal": 0.5}')
    _patch_tier(monkeypatch, fake)
    t = MasterEmotionTracker("t")
    asyncio.run(t.analyze("难过", now=100.0))
    assert t.latest is not None
    # flip the switch off after a reading exists → latest disappears
    monkeypatch.setattr(config, "MASTER_EMOTION_ENABLED", False)
    assert t.latest is None


def test_input_is_bounded(monkeypatch):
    captured = {}

    async def fake(prompt, *, timeout, label):
        captured["prompt"] = prompt
        return '{"valence": 0, "arousal": 0}'

    _patch_tier(monkeypatch, fake)
    monkeypatch.setattr(config, "MASTER_EMOTION_MAX_INPUT_CHARS", 10)
    t = MasterEmotionTracker("t")
    asyncio.run(t.analyze("x" * 100, now=100.0))
    assert captured["prompt"].endswith("x" * 10)
    assert ("x" * 11) not in captured["prompt"]


def test_to_profile_sample(monkeypatch):
    t = MasterEmotionTracker("t")
    assert t.to_profile_sample() is None
    t._latest = _reading(-0.4, 0.6)
    sample = t.to_profile_sample()
    assert sample == {"valence": -0.4, "arousal": 0.6, "confidence": 0.9, "at": 0.0}
    # honors the switch (reads via self.latest), same as latest
    monkeypatch.setattr(config, "MASTER_EMOTION_ENABLED", False)
    assert t.to_profile_sample() is None


def test_gate_signal_for_registry(monkeypatch):
    # The cross-server analyze_request publisher reads the combined pre-gate
    # signal by lanlan_name + current user text (no core handle). Verify the
    # registry bridge, the freshness (turn) match, and the complexity fold.
    from main_logic.activity.master_emotion import gate_signal_for

    # Unknown lanlan → None (fail-open: the agent gate runs the assessment).
    assert gate_signal_for("nobody-here", "anything") is None

    fake, _ = _fake_tier('{"valence": 0, "arousal": 0, "external_intent": 0.7}')
    _patch_tier(monkeypatch, fake)
    t = MasterEmotionTracker("regtest")  # registers itself on init
    # No reading yet → None.
    assert gate_signal_for("regtest", "帮我打开浏览器") is None
    asyncio.run(t.analyze("帮我打开浏览器", now=100.0))
    # Matching turn text → external_intent (complexity 0 → max is external_intent).
    assert gate_signal_for("regtest", "帮我打开浏览器") == 0.7
    # Freshness: a DIFFERENT user text → None (stale / other turn → fail open),
    # so an earlier turn's signal can never gate the current one.
    assert gate_signal_for("regtest", "完全不一样的另一句话") is None
    # Match is whitespace-insensitive.
    assert gate_signal_for("regtest", "  帮我打开浏览器  ") == 0.7

    # Complexity fold: a hard reasoning turn (low action, high complexity) keeps
    # the gate OPEN — max(0.1, 0.8) = 0.8 — so openfang-style reasoning requests
    # are not braked.
    fake2, _ = _fake_tier('{"valence": 0, "arousal": 0, "external_intent": 0.1, "complexity": 0.8}')
    _patch_tier(monkeypatch, fake2)
    asyncio.run(t.analyze("这道题怎么一步步推导出来", now=200.0))
    assert gate_signal_for("regtest", "这道题怎么一步步推导出来") == 0.8

    # A turn whose model output carried NO external_intent → None (fail-open),
    # even though a valid emotion reading exists this turn.
    fake3, _ = _fake_tier('{"valence": -0.5, "arousal": 0.5}')
    _patch_tier(monkeypatch, fake3)
    asyncio.run(t.analyze("我好难过", now=300.0))
    assert t.latest is not None and gate_signal_for("regtest", "我好难过") is None

    # Honors the master switch (reads through .latest).
    fake4, _ = _fake_tier('{"valence": 0, "arousal": 0, "external_intent": 0.9}')
    _patch_tier(monkeypatch, fake4)
    asyncio.run(t.analyze("运行一下", now=400.0))
    assert gate_signal_for("regtest", "运行一下") == 0.9
    monkeypatch.setattr(config, "MASTER_EMOTION_ENABLED", False)
    assert gate_signal_for("regtest", "运行一下") is None


def test_truncated_input_nulls_external_intent(monkeypatch):
    # When the analyzed text is longer than MASTER_EMOTION_MAX_INPUT_CHARS, the
    # model only saw the opening; an action verb / info request in the unseen tail
    # would make external_intent unreliable → it is nulled (the gate then fails open).
    # Emotion axes (judgeable from the opening) stay valid.
    monkeypatch.setattr(config, "MASTER_EMOTION_MAX_INPUT_CHARS", 20)
    fake, _ = _fake_tier('{"valence": -0.3, "arousal": 0.4, "external_intent": 0.9}')
    _patch_tier(monkeypatch, fake)
    t = MasterEmotionTracker("trunc")
    r = asyncio.run(t.analyze("x" * 100, now=100.0))  # 100 > 20 → truncated
    assert r is not None
    assert r.external_intent is None                  # nulled due to truncation
    assert r.valence == -0.3 and r.arousal == 0.4   # emotion preserved
    # Short input (<= max) keeps external_intent.
    fake2, _ = _fake_tier('{"valence": 0, "arousal": 0, "external_intent": 0.9}')
    _patch_tier(monkeypatch, fake2)
    t2 = MasterEmotionTracker("trunc2")
    r2 = asyncio.run(t2.analyze("short", now=100.0))
    assert r2.external_intent == 0.9


def test_gate_signal_long_text_no_prefix_collision(monkeypatch):
    # A prefix-truncated match key would treat two long messages that share a
    # long prefix (but differ at the end) as the SAME turn — letting a stale
    # low-external reading brake a real external request in the latter half. The
    # full-text fingerprint must reject that → fail open (None).
    from main_logic.activity.master_emotion import gate_signal_for

    # Raise the input cap so the truncation guard does NOT fire here — this test
    # isolates the fingerprint behavior (full text vs the old 280-char prefix).
    monkeypatch.setattr(config, "MASTER_EMOTION_MAX_INPUT_CHARS", 100000)
    long_prefix = "这是一段很长的对话内容反复出现并且超过二百八十个字符的前缀" * 20  # > 280 chars
    fake, _ = _fake_tier('{"valence": 0, "arousal": 0, "external_intent": 0.05}')
    _patch_tier(monkeypatch, fake)
    t = MasterEmotionTracker("longtest")
    asyncio.run(t.analyze(long_prefix + "前半段只是闲聊", now=100.0))
    # Same long prefix, different tail = a different turn → must NOT reuse the
    # prior low-external reading (which would wrongly brake the real request).
    assert gate_signal_for("longtest", long_prefix + "后半段请帮我打开浏览器并搜索") is None
    # The exact same text still matches (sanity check).
    assert gate_signal_for("longtest", long_prefix + "前半段只是闲聊") == 0.05
