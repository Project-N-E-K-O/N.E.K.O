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
feeds into its hysteresis. The SAME scorer instance serves both trigger
paths (see ``docs/design/focus-truename-mode.md``) so behaviour can't
diverge:

  * **Inline** (`stream_text`): a real user message just arrived. Pass
    ``user_text=...``; the applicable signals are keyword + cadence +
    open_thread.
  * **Idle** (`proactive_chat`): a silence window with no new message.
    Pass ``user_text=None``; the applicable signals are silence +
    open_thread.

Per-path applicability matters: signals that need a fresh message
(keyword, cadence) are ``None`` on the idle path, and the silence signal
is ``None`` on the inline path (the user just spoke — silence is
meaningless). The score is a weighted average over only the *applicable*
signals (``FOCUS_SIGNAL_WEIGHTS`` renormalised to the present subset), so
an absent signal never silently drags the average toward zero.

The scorer is intentionally thin: all Layer-1/2 raw evidence already
lives on ``ActivitySnapshot`` (``seconds_since_user_msg`` /
``unfinished_thread`` / ``open_threads``); the only state the scorer owns
is a small rolling buffer of recent user-message lengths for the cadence
signal. One instance per session, owned alongside ``UserActivityTracker``.
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
    def score(
        self,
        snapshot,
        *,
        user_text: Optional[str] = None,
    ) -> FocusScore:
        """Score the current turn. ``user_text`` non-None ⇒ inline path; None ⇒ idle path.

        Side effect: when ``user_text`` is a real (non-empty) message, its
        length is appended to the cadence baseline buffer **after** the
        cadence signal is computed (so cadence always compares the current
        message against *prior* messages). Idle calls leave the buffer
        untouched.

        No ``lang`` argument: the keyword signal scans every locale's
        vulnerability table in parallel (mixed-language speech is common),
        so the score is language-agnostic.
        """
        kw = self._signal_keyword(user_text)
        cadence = self._signal_cadence(user_text)
        silence = self._signal_silence(snapshot, user_text)
        open_thread = self._signal_open_thread(snapshot, user_text)

        signals = {
            "keyword": kw,
            "cadence": cadence,
            "silence": silence,
            "open_thread": open_thread,
        }
        score = _weighted_average(signals, config.FOCUS_SIGNAL_WEIGHTS)

        # Update the cadence baseline only for real inline messages.
        if user_text is not None and user_text.strip():
            self._recent_lengths.append(len(user_text.strip()))

        return FocusScore(score=score, signals=signals)

    def reset(self) -> None:
        """Drop the cadence baseline (call on session teardown / hot-swap)."""
        self._recent_lengths.clear()

    # ── sub-signals (each → [0, 1] or None when not applicable) ──────
    def _signal_keyword(self, user_text: Optional[str]) -> Optional[float]:
        if user_text is None:
            return None
        count = scan_vulnerability_keywords(user_text)
        sat = max(1, int(config.FOCUS_KEYWORD_SATURATION))
        return min(count / sat, 1.0)

    def _signal_cadence(self, user_text: Optional[str]) -> Optional[float]:
        if user_text is None:
            return None
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

    def _signal_silence(self, snapshot, user_text: Optional[str]) -> Optional[float]:
        # Silence is only meaningful on the idle path (no fresh message).
        if user_text is not None:
            return None
        secs = getattr(snapshot, "seconds_since_user_msg", None)
        if secs is None:
            return None
        lo = float(config.FOCUS_SILENCE_MIN_SECONDS)
        hi = float(config.FOCUS_SILENCE_FULL_SECONDS)
        if secs < lo:
            return 0.0
        if secs >= hi or hi <= lo:
            return 1.0
        return (secs - lo) / (hi - lo)

    def _signal_open_thread(self, snapshot, user_text: Optional[str]) -> Optional[float]:
        # Idle-path only. On the inline path the just-arrived message has
        # already cleared the tracker's unfinished_thread (on_user_message
        # runs before scoring), so reading it here would be a structural 0
        # that just dilutes the inline average — and a user replying to an
        # open question is already captured by the keyword cue. So this is
        # the anchor for "she comes back to what was left open" only when
        # there's no fresh message (idle).
        if user_text is not None:
            return None
        has_unfinished = getattr(snapshot, "unfinished_thread", None) is not None
        has_open = bool(getattr(snapshot, "open_threads", None))
        return 1.0 if (has_unfinished or has_open) else 0.0


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
