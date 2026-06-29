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

"""Adaptive-backoff gate + per-signature narration cache for ``activity_guess``.

Pure data/decision logic, extracted from ``UserActivityTracker._activity_guess_loop``
so the firing policy can be unit-tested without spinning up the tracker, the
collector, or a live LLM (same split rationale as ``focus_scorer.py``). It owns
no clock and performs no I/O — the tracker passes ``now``.

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
The refresh interval becomes *adaptive to how novel the activity is*. Everything
the gate tracks is keyed PER SIGNATURE so a one-off excursion never disturbs a
separate signature's state:

* **Coarsened signature** — callers pass ``(state, window-category)`` instead of
  the exact app, so flicking between two same-category apps is a no-op and even
  cross-category flicker only registers at the category level.
* **Per-signature exponential backoff** — each distinct signature carries its
  OWN streak and is re-narrated on an interval that grows by ``MULTIPLIER`` with
  each of *its* re-narrations: ``BASE → MULTIPLIER·BASE → MULTIPLIER²·BASE → …``
  capped at ``CAP``. Oscillating between already-narrated signatures therefore
  decays from "every BASE" toward "every CAP", and a one-off new activity does
  NOT reset the backoff a separate, still-oscillating signature has accumulated.
* **Novelty / freshness bypass** — a signature not in the small recently-narrated
  cache, or one whose cached narration was built under an OLDER conversation turn
  (``conv_seq``), fires immediately and restarts that signature's backoff. The
  conversation seq is stored per signature, so a new turn refreshes *every*
  cached activity when it is next visited — not just the first one re-narrated.
  Switching to a genuinely different (or conversation-stale) activity re-narrates
  at once; only re-describing the *same* activity under the *same* conversation
  backs off.

Per-signature narration cache
-----------------------------
The gate also stores the last narration (scores + guess text) **per signature**.
This is what keeps "staleness never makes it wrong" honest: when the backoff
suppresses a re-narration, the consumer asks ``cached(current_signature)`` and
gets the narration for *the activity the user is in right now* — never a leftover
narration for whichever signature happened to fire last. (Within a coarse bucket
the narration stays at category granularity by design — two same-category apps
share one entry; the exact app/title is carried by other snapshot fields, not
this hint.)

The hard ``BASE`` floor is always respected (no two calls closer than ``BASE``),
preserving the old anti-thrash guarantee during rapid flicker. The novelty/
freshness checks deliberately sit *above* the backoff interval so a genuinely new
or conversation-stale activity is never delayed by a grown interval — the cap
only governs re-narrating something already seen under the current conversation.
``record_fired`` is called only after a narration LLM call actually succeeds, so
failed/discarded calls do not advance the backoff or the cache.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable


class ActivityGuessGate:
    """Per-signature activity-narration cache with adaptive backoff.

    Usage (per tick, after the loop's privacy/away/proactive/goodbye bails)::

        if not gate.should_fire(sig, conv_seq, now):
            continue
        result = await run_emotion_tier_llm(...)         # guard conv_seq advance
        gate.record_fired(sig, conv_seq, now, scores, guess)   # only on success

    and at snapshot time the consumer reads the narration for the *current*
    activity::

        scores, guess = gate.cached(current_sig)
    """

    def __init__(
        self, *, base_seconds: float, cap_seconds: float, cache_size: int,
        backoff_multiplier: float = 2.0,
    ):
        if base_seconds <= 0:
            raise ValueError('base_seconds must be > 0')
        # The multiplier must actually grow the interval. A value <= 1 would
        # degrade the backoff to "re-narrate every BASE" — i.e. the original
        # idle burn this gate exists to kill. So, unlike ``cap`` (a soft ceiling
        # that clamps), the multiplier is load-bearing like ``base`` and a
        # misconfig raises rather than silently re-introducing the burn.
        if backoff_multiplier <= 1.0:
            raise ValueError('backoff_multiplier must be > 1')
        self._base = float(base_seconds)
        self._mult = float(backoff_multiplier)
        # ``cap`` is a soft ceiling, not load-bearing like ``base``: a bad value
        # (0 / negative / below base) is clamped up to ``base`` rather than
        # raising, so a misconfigured config knob degrades to "no backoff beyond
        # base" instead of crashing tracker/session init. (``base`` must raise —
        # base <= 0 would zero out the floor and the ``mult**streak`` interval.)
        self._cap = max(float(cap_seconds), self._base)
        self._cache_size = max(1, int(cache_size))
        # signature -> (last_fire_ts, streak, conv_seq, scores, guess), LRU
        # (most-recently fired at the end). Streak, the conversation seq, AND the
        # narration are all stored PER SIGNATURE: a one-off novel activity (or a
        # new conversation turn consumed by another signature) never disturbs a
        # separate signature's backoff, freshness, or cached guess.
        self._narrated: "OrderedDict[Hashable, tuple[float, int, int, dict, str]]" = OrderedDict()
        # Global only for the hard anti-thrash floor (no two LLM calls closer
        # than BASE, regardless of which signature).
        self._last_fire_ts: float | None = None
        # Cap the per-signature streak so ``base * mult**streak`` can't grow
        # without bound — once the interval reaches CAP, a larger streak changes
        # nothing. ``mult > 1`` (enforced above) guarantees ``eff`` strictly
        # grows, so this loop terminates well before the 32 backstop.
        self._max_streak = 0
        eff = self._base
        while eff < self._cap and self._max_streak < 32:
            eff *= self._mult
            self._max_streak += 1

    def should_fire(self, sig: Hashable, conv_seq: int, now: float) -> bool:
        # Hard floor: never two calls closer than BASE — even on novelty or a new
        # conversation turn (preserves the old anti-thrash behaviour during rapid
        # window flicker).
        if self._last_fire_ts is not None and now - self._last_fire_ts < self._base:
            return False
        rec = self._narrated.get(sig)
        if rec is None or rec[2] != conv_seq:
            # Genuinely new activity, or this signature's cached narration was
            # built under an older conversation turn → refresh immediately,
            # bypassing whatever backoff this signature had accumulated.
            return True
        # Same activity, same conversation → re-narrate only after this
        # signature's own grown interval has elapsed.
        last_ts, streak = rec[0], rec[1]
        return (now - last_ts) >= min(self._base * (self._mult ** streak), self._cap)

    def record_fired(
        self,
        sig: Hashable,
        conv_seq: int,
        now: float,
        scores: dict | None = None,
        guess: str = '',
    ) -> None:
        """Record a *successful* narration; advances THIS signature's backoff,
        stamps its conversation seq, and stores its narration for ``cached``."""
        rec = self._narrated.get(sig)
        if rec is None or rec[2] != conv_seq:
            # New signature (or evicted), or fresh conversation context → restart
            # this signature's backoff at streak 0. Other signatures are untouched.
            streak = 0
        else:
            streak = min(rec[1] + 1, self._max_streak)
        self._narrated[sig] = (now, streak, conv_seq, scores if scores is not None else {}, guess)
        self._narrated.move_to_end(sig)
        while len(self._narrated) > self._cache_size:
            self._narrated.popitem(last=False)
        self._last_fire_ts = now

    def cached(self, sig: Hashable) -> tuple[dict, str]:
        """The last narration recorded for ``sig`` as ``(scores, guess)``.

        Returns ``({}, '')`` when this signature has never been narrated (or was
        evicted) — an honest "no narration yet" rather than a leftover narration
        for a different activity. The caller should copy ``scores`` before
        exposing it (the stored dict is shared)."""
        rec = self._narrated.get(sig)
        if rec is None:
            return {}, ''
        return rec[3], rec[4]
