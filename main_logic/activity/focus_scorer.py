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

Produces the single [0, 1] score that ``SessionStateMachine.update_focus``
feeds into its hysteresis. Scoring is **inline-only**: it reads what the
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

The score is a weighted average over the *applicable* signals
(``FOCUS_SIGNAL_WEIGHTS`` renormalised to the present subset), so an
absent signal (e.g. cadence before the baseline has enough samples) never
silently drags the average toward zero.

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

    ``signals`` holds each sub-signal's value in [0, 1], or ``None`` when
    it didn't apply to this path — kept for diagnostics / logging so a
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
        cadence = self._signal_cadence(user_text)
        emotion = self._signal_emotion(emotion_reading)

        signals = {"keyword": kw, "cadence": cadence, "emotion": emotion}
        score = _weighted_average(signals, config.FOCUS_SIGNAL_WEIGHTS)

        # Update the cadence baseline for this inline message.
        if user_text.strip():
            self._recent_lengths.append(len(user_text.strip()))

        return FocusScore(score=score, signals=signals)

    def reset(self) -> None:
        """Drop the cadence baseline (call on session teardown / hot-swap)."""
        self._recent_lengths.clear()

    # ── sub-signals ──────────────────────────────────────────────────
    # keyword / emotion are *positive distress evidence*: they return None (not
    # 0.0) when absent, so an empty one never dilutes the other in the weighted
    # average — a saturated keyword OR a saturated emotion reading can each
    # trigger Focus on its own. cadence is a behavioural signal whose 0.0
    # ("message length normal") is informative, so it keeps 0.0 in the denom.
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
        """Distress signal from the master emotion VA reading.

        High arousal × negative valence → distress, the model-grade upgrade of
        the vulnerability-keyword signal. Returns ``None`` (drops out of the
        weighted average) when there is no reading OR no distress — a stale
        neutral/positive reading must not dilute a current keyword-positive
        turn, same "positive evidence only" rule as ``_signal_keyword``.
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
        # arousal ∈ [0,1] = intensity; negativity = max(0, -valence) fires ONLY
        # in the negative-valence half (valence -1→1, 0→0, +1→0). So distress =
        # arousal × negativity ≈ 1 for strong-negative & high-arousal, and 0 for
        # neutral OR positive affect — neither calm, mere intensity, nor elation
        # triggers Focus, matching the "high arousal + NEGATIVE valence" def.
        # Focus is for vulnerability / distress.
        negativity = max(0.0, -valence)
        distress = max(0.0, min(1.0, arousal * negativity))
        # No distress (neutral / positive / stale) → None, not 0.0 (see above).
        return distress if distress > 0.0 else None


def _weighted_average(signals: dict, weights: dict) -> float:
    """Weighted average over applicable (non-None) signals, weights renormalised.

    A signal whose value is ``None`` (not applicable to this path) is
    excluded from both numerator and denominator, so it neither counts as
    zero nor inflates the result. Returns 0.0 when no signal applies.
    """
    num = 0.0
    den = 0.0
    for name, val in signals.items():
        if val is None:
            continue
        w = float(weights.get(name, 0.0))
        num += w * float(val)
        den += w
    if den <= 0.0:
        return 0.0
    return num / den
