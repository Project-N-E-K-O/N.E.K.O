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

"""Adaptive-backoff gate for the ``activity_guess`` emotion-tier loop.

Pure decision logic, extracted from ``UserActivityTracker._activity_guess_loop``
so the firing policy can be unit-tested without spinning up the tracker, the
collector, or a live LLM (same split rationale as ``focus_scorer.py``).

Problem it solves
-----------------
The activity heartbeat re-narrates "what the user is doing" via an emotion-tier
LLM call, consumed only by the proactive-chat prompt. The narration is silent
(no business log), so the only visible trace is an httpx POST every ~40s. The
old gate fired whenever the ``(state, exact-app, subcategory)`` signature
changed, throttled solely by a flat 30s floor. A user flicking between two apps
(e.g. an IM window and a browser) flips that signature on every switch, so the
floor lets one LLM call through every ~40s indefinitely — pure idle burn that
re-describes the same two activities over and over.

What this gate changes
----------------------
The refresh interval becomes *adaptive to how novel the activity is*:

* **Coarsened signature** — callers pass ``(state, window-category)`` instead of
  the exact app, so flicking between two same-category apps is a no-op and even
  cross-category flicker only registers at the category level.
* **Per-signature exponential backoff** — each distinct signature is re-narrated
  on an interval that grows while the recent activity set stays stable (no
  genuinely new signature): ``BASE → 2·BASE → …`` capped at ``CAP``. Oscillating
  between already-narrated signatures therefore decays from "every BASE" toward
  "every CAP".
* **Novelty bypass** — a signature not in the small recently-narrated cache, or a
  new conversation turn (``conv_seq`` advanced), fires immediately and resets the
  backoff. Switching to a genuinely different activity (work → game) re-narrates
  at once; only re-describing the *same* activities backs off. Because staleness
  only ever accrues on an activity that hasn't changed, the proactive prompt
  never reads a narration that is wrong — just one that is unchanged.

The hard ``BASE`` floor is always respected (no two calls closer than ``BASE``),
preserving the old anti-thrash guarantee during rapid flicker. The novelty check
deliberately sits *above* the backoff interval so a genuinely new activity is
never delayed by a grown interval — the cap only governs re-narrating something
already seen.

This object is **decision-only**: it owns no clock and performs no I/O. The
tracker passes ``now`` (monotonic-ish wall time) and calls ``record_fired`` only
after a narration LLM call actually succeeds, so failed/discarded calls do not
advance the backoff.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable


class ActivityGuessGate:
    """Decide whether the activity_guess loop should issue an LLM call this tick.

    Usage (per tick, after the loop's privacy/away/proactive/goodbye bails)::

        if not gate.should_fire(sig, conv_seq, now):
            continue
        ... run the emotion-tier LLM, guard against conv_seq advancing ...
        gate.record_fired(sig, conv_seq, now)   # only on success
    """

    def __init__(self, *, base_seconds: float, cap_seconds: float, cache_size: int):
        if base_seconds <= 0:
            raise ValueError('base_seconds must be > 0')
        self._base = float(base_seconds)
        # A cap below base is meaningless; clamp so the interval is always >= base.
        self._cap = max(float(cap_seconds), self._base)
        self._cache_size = max(1, int(cache_size))
        # signature -> last fire timestamp (LRU; most-recently fired at the end).
        self._narrated: "OrderedDict[Hashable, float]" = OrderedDict()
        # Consecutive non-novel fires; grows the backoff interval, reset by novelty.
        self._streak = 0
        self._last_fire_ts: float | None = None
        self._last_fire_conv_seq: int | None = None
        # Cap the streak so ``base * 2**streak`` can't grow without bound — once
        # the interval reaches CAP, a larger streak changes nothing.
        self._max_streak = 0
        eff = self._base
        while eff < self._cap and self._max_streak < 32:
            eff *= 2
            self._max_streak += 1

    def _conv_advanced(self, conv_seq: int) -> bool:
        """A new conversation turn since the last fire is itself fresh context."""
        return (
            self._last_fire_conv_seq is not None
            and conv_seq != self._last_fire_conv_seq
        )

    def _effective_interval(self) -> float:
        return min(self._base * (2 ** self._streak), self._cap)

    def should_fire(self, sig: Hashable, conv_seq: int, now: float) -> bool:
        # Hard floor: never two calls closer than BASE — even on novelty or a new
        # conversation turn (preserves the old anti-thrash behaviour during rapid
        # window flicker).
        if self._last_fire_ts is not None and now - self._last_fire_ts < self._base:
            return False
        last_ts = self._narrated.get(sig)
        if last_ts is None or self._conv_advanced(conv_seq):
            # Genuinely new activity, or new conversation → refresh immediately,
            # bypassing whatever backoff the stable period had accumulated.
            return True
        # Same activity, no new conversation → re-narrate only after the grown
        # interval has elapsed for this signature.
        return (now - last_ts) >= self._effective_interval()

    def record_fired(self, sig: Hashable, conv_seq: int, now: float) -> None:
        """Record a *successful* narration; advances backoff + the LRU cache."""
        is_novel = (sig not in self._narrated) or self._conv_advanced(conv_seq)
        if is_novel:
            self._streak = 0
        else:
            self._streak = min(self._streak + 1, self._max_streak)
        self._narrated[sig] = now
        self._narrated.move_to_end(sig)
        while len(self._narrated) > self._cache_size:
            self._narrated.popitem(last=False)
        self._last_fire_ts = now
        self._last_fire_conv_seq = conv_seq
