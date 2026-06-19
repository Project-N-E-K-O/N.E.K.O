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

import config
from main_logic.activity import (
    FocusScorer,
    MasterEmotionReading,
    MasterEmotionTracker,
)
from main_logic.activity.master_emotion import _clamp


# ── _clamp ───────────────────────────────────────────────────────────
def test_clamp_bounds_and_junk():
    assert _clamp(0.5, -1.0, 1.0, 0.0) == 0.5
    assert _clamp(5, -1.0, 1.0, 0.0) == 1.0       # over → hi
    assert _clamp(-9, -1.0, 1.0, 0.0) == -1.0      # under → lo
    assert _clamp("nope", 0.0, 1.0, 0.5) == 0.5    # non-numeric → default
    assert _clamp(None, 0.0, 1.0, 0.5) == 0.5
    assert _clamp(float("nan"), 0.0, 1.0, 0.5) == 0.5  # NaN → default


# ── FocusScorer.emotion signal mapping ───────────────────────────────
def _reading(valence, arousal):
    return MasterEmotionReading(
        valence=valence, arousal=arousal, confidence=0.9, updated_at=0.0,
    )


def test_emotion_signal_distress_is_max():
    s = FocusScorer("t")
    assert s._signal_emotion(_reading(-1.0, 1.0)) == 1.0


def test_emotion_signal_happy_is_zero():
    s = FocusScorer("t")
    # positive valence + high arousal (excitement) must NOT trigger Focus
    assert s._signal_emotion(_reading(1.0, 1.0)) == 0.0


def test_emotion_signal_neutral_high_arousal_is_zero():
    s = FocusScorer("t")
    # neutral valence → no distress even at high arousal (intensity ≠ distress)
    assert s._signal_emotion(_reading(0.0, 1.0)) == 0.0


def test_emotion_signal_positive_high_arousal_is_zero():
    s = FocusScorer("t")
    assert s._signal_emotion(_reading(0.5, 1.0)) == 0.0


def test_emotion_signal_calm_negative_is_low():
    s = FocusScorer("t")
    # negative but low arousal → weak signal
    assert abs(s._signal_emotion(_reading(-1.0, 0.1)) - 0.1) < 1e-9


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
