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

"""Focus-mode signal scorer (the Focus trigger).

Produces the single scalar score that ``SessionStateMachine.update_focus``
feeds into its hysteresis — a direct weighted sum, NOT bounded to ``[0, 1]``
(it can reach ``sum(weights)`` ≈ 2.0 or go slightly negative on a happy turn;
see ``_weighted_sum``). Scoring is **inline-only**: it reads what the
user just typed (``stream_text``), never the screen. The applicable
signals are keyword + cadence + emotion — keyword/cadence read the message
directly, emotion reads the latest master-emotion VA reading the caller hands
in (no I/O in the scorer). All need a real user message / reading.

The idle (``proactive_chat``) path does NOT score: a proactive turn never
raises the Focus charge. Entering and sustaining Focus is driven solely by
the user's own messages here; proactive turns only let an active episode
cool down (faster when she spoke, slower when she stayed silent — see
``docs/design/focus-truename-mode.md`` and ``LLMSessionManager.
_focus_idle_cooldown``).

The score is a direct weighted sum over the *applicable* signals
(``FOCUS_SIGNAL_WEIGHTS`` applied as absolute weights, NOT renormalised),
so an absent signal (e.g. cadence before the baseline has enough samples)
drops out entirely — it contributes nothing rather than dragging the sum
toward zero. Each present signal adds ``weight × value`` straight into the
score, so the configured weights are the literal per-signal contributions.

The scorer is intentionally thin: the only state it owns is a small
rolling buffer of recent user-message lengths for the cadence signal. One
instance per session, owned alongside ``UserActivityTracker``.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from statistics import median
from typing import Optional

import config
from config.prompts.prompts_focus import scan_vulnerability_keywords


@dataclass(frozen=True)
class FocusScore:
    """Result of one scoring pass: the final score plus the per-signal breakdown.

    ``signals`` holds each sub-signal's value (``[0, 1]`` for keyword /
    cadence / question, SIGNED for emotion — negative on a happy turn), or
    ``None`` when it didn't apply to this path — kept for diagnostics /
    logging so a
    tuner can see *why* a turn fed the accumulator a high or low score
    (the score is integrated into the leaky charge; see ``FOCUS_CHARGE_*``).
    """

    score: float
    signals: dict[str, float | None] = field(default_factory=dict)


class FocusScorer:
    """Per-session Focus signal scorer. Cheap, synchronous, no I/O."""

    def __init__(self, lanlan_name: str) -> None:
        self.lanlan_name = lanlan_name
        # Rolling recent user-message lengths → cadence baseline (median).
        self._recent_lengths: deque[int] = deque(
            maxlen=max(1, int(config.FOCUS_CADENCE_BASELINE_WINDOW)),
        )

    # ── public API ──────────────────────────────────────────────────
    def score(self, *, user_text: str, emotion_reading=None) -> FocusScore:
        """Score one inline turn from the user's message (keyword + cadence + emotion).

        Side effect: when ``user_text`` is a real (non-empty) message, its
        length is appended to the cadence baseline buffer **after** the
        cadence signal is computed (so cadence always compares the current
        message against *prior* messages).

        ``emotion_reading`` is the latest ``MasterEmotionReading`` (duck-typed:
        any object with ``valence``/``arousal`` floats) from the master emotion
        tracker, or ``None`` when unavailable. Stays I/O-free — the caller hands
        in an already-computed reading, the scorer never analyzes. The reading
        lags one turn (it is produced async) — by design, the same way cadence
        compares against prior messages.

        No ``lang`` argument: the keyword signal scans every locale's
        vulnerability table in parallel (mixed-language speech is common),
        so the score is language-agnostic.
        """
        kw = self._signal_keyword(user_text)
        emotion = self._signal_emotion(emotion_reading)
        question = self._signal_question(emotion_reading)
        cadence = self._signal_cadence(user_text)
        # cadence amplifies distress evidence; it is NOT a trigger on its own, and
        # must NOT fire on a happy turn (emotion < 0) — a short cheerful reply
        # should not push Focus. Gate it on POSITIVE evidence only: a present
        # keyword, a complex question, or a distress-side emotion (> 0).
        has_distress_evidence = (
            kw is not None
            or question is not None
            or (emotion is not None and emotion > 0.0)
        )
        if not has_distress_evidence:
            cadence = None

        signals = {"keyword": kw, "cadence": cadence, "emotion": emotion, "question": question}
        score = _weighted_sum(signals, config.FOCUS_SIGNAL_WEIGHTS)

        # Update the cadence baseline for this inline message.
        if user_text.strip():
            self._recent_lengths.append(len(user_text.strip()))

        return FocusScore(score=score, signals=signals)

    def reset(self) -> None:
        """Drop the cadence baseline (call on session teardown / hot-swap)."""
        self._recent_lengths.clear()

    # ── sub-signals ──────────────────────────────────────────────────
    # keyword / emotion are *positive distress evidence*: they return None (not
    # 0.0) when absent, so an empty one simply drops out of the weighted sum —
    # a saturated keyword OR a saturated emotion reading can each trigger Focus
    # on its own. cadence returns 0.0 ("message length normal") rather than
    # None; with no denominator that 0.0 contributes nothing (≡ absent), so
    # cadence only ever ADDS to the score when the length actually dropped.
    def _signal_keyword(self, user_text: str) -> Optional[float]:
        count = scan_vulnerability_keywords(user_text)
        if count <= 0:
            return None  # no vulnerability keyword → no signal, not "evidence against"
        sat = max(1, int(config.FOCUS_KEYWORD_SATURATION))
        return min(count / sat, 1.0)

    def _signal_cadence(self, user_text: str) -> Optional[float]:
        text = user_text.strip()
        if not text:
            return None
        if len(self._recent_lengths) < int(config.FOCUS_CADENCE_MIN_SAMPLES):
            return None
        baseline = median(self._recent_lengths)
        if baseline <= 0:
            return None
        cur = len(text)
        lo = float(config.FOCUS_CADENCE_DROP_RATIO) * baseline
        if cur >= baseline:
            return 0.0
        if cur <= lo:
            return 1.0
        # linear ramp between the drop floor and the baseline
        return (baseline - cur) / (baseline - lo)

    def _signal_emotion(self, emotion_reading) -> Optional[float]:
        """SIGNED emotion signal from the master emotion VA reading.

        Negative valence → POSITIVE distress (push toward Focus, up to +1);
        positive valence → NEGATIVE value (pull Focus down — don't intrude on a
        good mood), down to ``-FOCUS_EMOTION_POSITIVE_SCALE`` (≈ -0.5). Both
        sides share the same arousal amplifier ``m = floor + (1-floor)×arousal``
        (floor = FOCUS_EMOTION_AROUSAL_FLOOR). The positive side is deliberately
        weaker (scaled by POSITIVE_SCALE) — joy nudges away, distress pulls in
        harder. Returns ``None`` ONLY at neutral valence (no reading / no axis /
        valence ≈ 0) so a flat turn doesn't dilute keyword/question; a genuine
        positive reading is a real (negative) vote, not a no-op.
        """
        if emotion_reading is None:
            return None
        valence = getattr(emotion_reading, "valence", None)
        arousal = getattr(emotion_reading, "arousal", None)
        if valence is None or arousal is None:
            return None
        try:
            valence = float(valence)
            arousal = float(arousal)
        except (TypeError, ValueError):
            return None
        floor = max(0.0, min(1.0, float(getattr(config, "FOCUS_EMOTION_AROUSAL_FLOOR", 0.5))))
        pos_scale = max(0.0, min(1.0, float(getattr(config, "FOCUS_EMOTION_POSITIVE_SCALE", 0.5))))
        arousal = max(0.0, min(1.0, arousal))
        m = floor + (1.0 - floor) * arousal  # arousal amplifier ∈ [floor, 1]
        if valence < 0.0:
            # distress: strong-negative (even low-arousal/quiet) reads high.
            return min(1.0, -valence * m)
        if valence > 0.0:
            # joy: pull Focus down, capped at POSITIVE_SCALE (half the distress reach).
            return -min(1.0, valence * m * pos_scale)
        return None  # exactly neutral → no vote (don't dilute other signals)

    def _signal_question(self, emotion_reading) -> Optional[float]:
        """Cognitive-load bonus from the master model's ``complexity`` read — how
        much the user is posing a COMPLEX, OBJECTIVE question (math / logic /
        reasoning), which also merits thinking-on. Orthogonal to distress but
        folded into the same charge. Same "positive evidence only" rule: ``None``
        (drops out) when there is no reading or no complexity, so it never dilutes
        an emotional turn — it only ever adds.
        """
        if emotion_reading is None:
            return None
        complexity = getattr(emotion_reading, "complexity", None)
        if complexity is None:
            return None
        try:
            complexity = float(complexity)
        except (TypeError, ValueError):
            return None
        complexity = max(0.0, min(1.0, complexity))
        return complexity if complexity > 0.0 else None


def _weighted_sum(signals: dict, weights: dict) -> float:
    """Direct weighted sum over applicable (non-None) signals — NO denominator.

    Each present signal contributes ``weight × value`` as an ABSOLUTE amount;
    the weights are the literal per-signal contributions, not renormalised. A
    signal whose value is ``None`` (not applicable to this path) drops out — it
    contributes nothing, neither dragging the score toward zero nor inflating
    it. Returns 0.0 when no signal applies.

    Without a denominator the sum is not renormalised (all signals saturated ⇒
    ``sum(weights.values())``, which may exceed 1.0); the charge accumulator's
    ``FOCUS_CHARGE_CAP`` clamps the downstream charge, and a signal that returns
    exactly ``0.0`` (e.g. cadence "length normal") is equivalent to being absent
    here.
    """
    total = 0.0
    for name, val in signals.items():
        if val is None:
            continue
        total += float(weights.get(name, 0.0)) * float(val)
    return total
