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

"""Generic, plugin-agnostic proactive-delivery pacing/ordering front stage.

Problem (observed in voice mode especially): proactive cues — plugin
``push_message(ai_behavior="respond")``, greeting, agent task results — are
produced far faster than the assistant can speak them, and the legacy gate
released the next cue on the realtime API's ``response.done`` (generation
finished) while the FRONTEND was still playing buffered audio. Result: she
talks non-stop / interrupts herself, and a low-value "state digest" cue
competes equally with an urgent "you got hit" cue.

This manager sits IN FRONT of the existing, race-tested
``LLMSessionManager.enqueue_agent_callback`` + ``trigger_agent_callbacks``
delivery core (it does NOT replace it). It owns the WAITING cues and decides
WHICH cue and WHEN to hand one off, applying:

* **Priority ordering** — HIGHER number = more important (the repo-wide
  convention shared by existing producers: bilibili gift/SC=9, memo
  reminder=8, study answer_evaluated=5, and the HUD ``priority_min`` filter).
  ``priority`` arrives from ``push_message(priority=...)``; unspecified
  default (0) = least important, so a cue that set a priority always outranks
  one that didn't. minecraft is tagged on the same scale (alert highest).
* **Coalescing** — OPT-IN: queued cues sharing an explicit ``coalesce_key``
  collapse to the newest. An unset key never coalesces (unique per cue), so
  no existing plugin regresses by having distinct cues silently dropped.
  Minecraft opts in per category (alert / completion / in_progress /
  keep_going).
* **Batched + playback gate** — cues that pile up while she is speaking are
  released TOGETHER as one batch (the legacy "one LLM turn for several
  near-simultaneous cues" behaviour), and never while audio is playing.
  Release happens after the FRONTEND reports ``voice_play_end`` (or
  ``text_end``), plus a min-gap. Only one batch is in flight at a time.
* **Min-gap pacing** — never release within ``min_gap_s`` of the last
  playback end (anti-flood).
* **Preemption / staleness** — when the gate opens the current highest
  priority cue wins the slot; a cue that has waited longer than ``ttl_s``
  is dropped rather than spoken stale.

The manager runs entirely inside the asyncio event loop; all public methods
are synchronous and schedule the actual (awaitable) hand-off via
``create_task``. There is therefore no internal lock — the single-threaded
loop serialises everything between ``await`` points.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("main_logic.proactive_delivery")

DELIVERY_ACK_FUTURE_KEY = "_proactive_delivery_ack_future"
DELIVERY_RETRACTED_KEY = "_proactive_delivery_retracted"


def resolve_callback_delivery_ack(callback: dict, delivered: bool) -> None:
    """Resolve an optional in-memory delivery acknowledgement future."""
    future = callback.get(DELIVERY_ACK_FUTURE_KEY)
    if future is None:
        return
    try:
        if not future.done():
            future.set_result(bool(delivered))
    except Exception:
        logger.debug("delivery ack future resolution failed", exc_info=True)


def effective_priority(raw: Any) -> int:
    # Repo-wide convention (the "greatest common denominator" of existing
    # producers): HIGHER number = more important. Matches every current
    # producer — bilibili gift/SC=9, memo reminder=8, study answer_evaluated=5
    # — and the HUD ``priority_min`` filter. Unspecified / invalid → 0 = least
    # important (a cue that didn't set a priority never preempts one that did).
    # minecraft is tagged on this SAME scale (alert highest). Within a release
    # batch, cues sort by importance DESC, then FIFO (see _QueuedCue.sort_key).
    try:
        return int(raw)
    except (TypeError, ValueError, OverflowError):
        return 0


class _QueuedCue:
    __slots__ = ("eff_priority", "seq", "coalesce_key", "callback", "submitted_at")

    def __init__(self, eff_priority: int, seq: int, coalesce_key: str,
                 callback: dict, submitted_at: float) -> None:
        self.eff_priority = eff_priority
        self.seq = seq
        self.coalesce_key = coalesce_key
        self.callback = callback
        self.submitted_at = submitted_at

    @property
    def sort_key(self) -> tuple[int, int]:
        # Importance DESC (higher = more important → first), then FIFO within
        # the same importance. Negate priority so a plain ascending sort yields
        # most-important-first.
        return (-self.eff_priority, self.seq)


class ProactiveDeliveryManager:
    def __init__(
        self,
        *,
        # Receives the WHOLE released batch (list of callback dicts), not a
        # single cue — releases are batched (see _pump / _run_deliver).
        deliver: Callable[[list[dict]], Awaitable[Any]],
        name: str = "",
        min_gap_s: float = 2.0,
        inflight_timeout_s: float = 12.0,
        ttl_s: float = 90.0,
        max_play_s: float = 45.0,
        can_release: Optional[Callable[[], bool]] = None,
        busy_recheck_s: float = 0.5,
    ) -> None:
        # ``deliver`` does the actual hand-off into the existing pipeline
        # (enqueue_agent_callback + trigger_agent_callbacks). Awaited inside
        # a task so a slow/blocking delivery can't stall the loop.
        self._deliver = deliver
        self._name = name
        self._min_gap_s = float(min_gap_s)
        self._inflight_timeout_s = float(inflight_timeout_s)
        self._ttl_s = float(ttl_s)
        # Optional predicate (core's gate, inverted): returns False when the
        # session is busy in ways the playback gate alone can't see — a
        # response still GENERATING (is_active_response, before any
        # voice_play_start) or the SM not IDLE. When it returns False we keep
        # cues IN the manager (so coalescing/priority still apply to later
        # cues) and recheck shortly, instead of releasing into the inner
        # trigger which would just defer them into pending_agent_callbacks,
        # outside manager ordering.
        self._can_release = can_release
        self._busy_recheck_s = float(busy_recheck_s)
        # Watchdog ceiling for a missing voice_play_end: above a normal single
        # reply, but short enough to recover a dropped end-signal reasonably
        # fast. The common cause (frontend disconnect/refresh) is already
        # handled by session teardown (_reset_proactive_gate on end_session/ws
        # drop); this only backstops the rare "connection alive but end signal
        # lost" case. Set above typical reply length so it doesn't cut off a
        # long answer mid-playback (Codex P2).
        self._max_play_s = float(max_play_s)

        self._queue: list[_QueuedCue] = []
        self._seq = itertools.count()

        # Gate state. ``_playing`` spans voice_play_start..voice_play_end (or
        # text_start..text_end). ``_inflight`` guards single-flight between a
        # release and its playback confirmation; ``_inflight_deadline`` lets
        # us recover if a released cue never produces playback (deferred by
        # the inner gate, text with no audio, frontend disconnect).
        self._playing = False
        self._play_start_ts = 0.0
        self._inflight = False
        self._inflight_deadline = 0.0
        self._last_play_end_ts = 0.0

        self._pump_handle: Optional[asyncio.TimerHandle] = None

    # ── helpers ──────────────────────────────────────────────────────────
    @property
    def min_gap_s(self) -> float:
        """Min seconds between proactive turns (read-only). Callers that retry
        delivery outside the manager (e.g. core's voice_play_end re-fire of a
        deferred cue) should honor this for pacing parity."""
        return self._min_gap_s

    def _now(self) -> float:
        return time.monotonic()

    def _resolve_key(self, callback: dict, coalesce_key: Optional[str]) -> str:
        # Coalescing is OPT-IN: a cue collapses with another only when both
        # set the SAME explicit coalesce_key. An unset key yields a unique
        # sentinel so the cue never coalesces. This is deliberate — defaulting
        # to ``source`` would silently drop DISTINCT important cues that share
        # a source (e.g. a bilibili gift vs a super-chat, two memo reminders,
        # a study answer vs mastery event), regressing every existing plugin
        # that emits multiple proactive cues. Plugins opt in by passing
        # coalesce_key (minecraft tags per category: mc_alert / mc_completion
        # / mc_in_progress / mc_keep_going).
        k = (coalesce_key or "").strip()
        if k:
            return k
        return f"__uniq:{next(self._seq)}"

    # ── producer ─────────────────────────────────────────────────────────
    def submit(self, callback: dict, *, priority: Any = 0,
               coalesce_key: Optional[str] = None) -> None:
        key = self._resolve_key(callback, coalesce_key)
        eff = effective_priority(priority)
        # Coalesce: newest replaces any queued cue with the same key.
        if self._queue:
            dropped = [c for c in self._queue if c.coalesce_key == key]
            if dropped:
                self._queue = [c for c in self._queue if c.coalesce_key != key]
                for cue in dropped:
                    resolve_callback_delivery_ack(cue.callback, False)
                logger.debug(
                    "[proactive%s] coalesced %d queued cue(s) on key=%r",
                    self._suffix(), len(dropped), key,
                )
        self._queue.append(
            _QueuedCue(eff, next(self._seq), key, callback, self._now())
        )
        logger.debug(
            "[proactive%s] submit key=%r eff_priority=%d queue=%d",
            self._suffix(), key, eff, len(self._queue),
        )
        self._schedule_pump(0.0)

    def retract(self, callback: dict) -> bool:
        """Remove a not-yet-released callback from the manager queue."""
        callback[DELIVERY_RETRACTED_KEY] = True
        delivery_id = callback.get("_callback_delivery_id")
        callback_obj_id = id(callback)
        remaining: list[_QueuedCue] = []
        removed = False
        for cue in self._queue:
            queued = cue.callback
            same_callback = id(queued) == callback_obj_id
            same_delivery = (
                bool(delivery_id)
                and queued.get("_callback_delivery_id") == delivery_id
            )
            if same_callback or same_delivery:
                removed = True
                resolve_callback_delivery_ack(queued, False)
                continue
            remaining.append(cue)
        self._queue = remaining
        return removed

    # ── lifecycle signals (from LifecycleEventBus) ───────────────────────
    def on_playback_start(self, **_: Any) -> None:
        self._playing = True
        self._play_start_ts = self._now()

    def on_playback_end(self, **_: Any) -> None:
        self._playing = False
        self._inflight = False
        self._last_play_end_ts = self._now()
        # Wait out the min-gap before the next release.
        self._schedule_pump(self._min_gap_s)

    # text-mode boundaries reuse the same gating semantics
    on_text_start = on_playback_start
    on_text_end = on_playback_end

    def reset_gate(self) -> None:
        """Clear ONLY the playback-gate / single-flight state — NOT the queue.
        Call on session lifecycle boundaries so a dropped voice_play_end
        (frontend disconnect/refresh, teardown mid-playback) can't leave the
        gate stuck closed and wedge delivery. Queued cues are preserved; the
        caller drains them via drain_pending() and hands them to
        pending_agent_callbacks for redelivery, so proactive cues are never
        dropped on teardown (they are generally important)."""
        self._playing = False
        self._play_start_ts = 0.0
        self._inflight = False
        self._inflight_deadline = 0.0
        self._last_play_end_ts = 0.0
        if self._pump_handle is not None:
            self._pump_handle.cancel()
            self._pump_handle = None

    def drain_pending(self) -> list:
        """Pop and return all queued cue callbacks (clearing the queue), in the
        SAME priority order a normal release would use (priority asc, then
        FIFO). Used on session teardown to move not-yet-released cues into
        pending_agent_callbacks so the reconnect path redelivers them rather
        than losing them — exporting in queue/append order would drop the
        priority-asc + FIFO ordering, letting a late high-priority cue trail
        behind earlier low-priority ones on redelivery."""
        ordered = sorted(self._queue, key=lambda c: c.sort_key)
        self._queue = []
        return [c.callback for c in ordered]

    # ── pump ─────────────────────────────────────────────────────────────
    def _suffix(self) -> str:
        return f":{self._name}" if self._name else ""

    def _schedule_pump(self, delay: float) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (e.g. constructed before the server is up).
            # A later signal/submit that runs in-loop will reschedule.
            return
        if self._pump_handle is not None:
            # Collapse multiple scheduled pumps; keep the soonest.
            self._pump_handle.cancel()
        self._pump_handle = loop.call_later(max(0.0, delay), self._pump)

    def _drop_stale(self) -> None:
        if self._ttl_s <= 0 or not self._queue:
            return
        now = self._now()
        fresh: list[_QueuedCue] = []
        for c in self._queue:
            if now - c.submitted_at > self._ttl_s:
                resolve_callback_delivery_ack(c.callback, False)
                logger.info(
                    "[proactive%s] dropping stale cue key=%r age=%.1fs (ttl=%.0fs)",
                    self._suffix(), c.coalesce_key, now - c.submitted_at, self._ttl_s,
                )
            else:
                fresh.append(c)
        self._queue = fresh

    def release_inflight_noop(self) -> None:
        """Free the inflight slot for a release that delivered nothing.

        ``_pump`` arms ``_inflight`` before invoking ``deliver``, expecting a
        playback/text lifecycle signal (or the inflight timeout) to clear it.
        When a released batch delivers nothing — e.g. every cue is dropped at a
        release-time gate — no such signal arrives, so the slot would stay
        armed for the whole timeout window and hold back the next cue. The
        deliver callback calls this to release the slot immediately and re-pump.
        """
        self._inflight = False
        self._schedule_pump(0.0)

    def _pump(self) -> None:
        self._pump_handle = None
        self._drop_stale()
        if not self._queue:
            return
        now = self._now()
        if self._playing:
            # Watchdog: voice_play_end may never arrive (frontend disconnect /
            # refresh mid-playback). If playback has "run" longer than any
            # plausible utterance, treat the flag as stale and re-open the
            # gate rather than wedge the queue forever.
            if self._max_play_s > 0 and now - self._play_start_ts > self._max_play_s:
                logger.warning(
                    "[proactive%s] playback watchdog: no voice_play_end after %.0fs; clearing stuck playing flag",
                    self._suffix(), now - self._play_start_ts,
                )
                self._playing = False
            else:
                # Still audibly speaking — don't inject; re-check at the
                # watchdog deadline in case no end signal arrives.
                if self._max_play_s > 0:
                    self._schedule_pump(self._play_start_ts + self._max_play_s - now)
                return
        if self._inflight:
            if now < self._inflight_deadline:
                # Released cue still awaiting playback confirmation.
                self._schedule_pump(self._inflight_deadline - now)
                return
            # Timed out without playback — release the slot and continue.
            logger.debug("[proactive%s] inflight timed out; releasing slot", self._suffix())
            self._inflight = False
        gap_remaining = self._min_gap_s - (now - self._last_play_end_ts)
        if self._last_play_end_ts > 0.0 and gap_remaining > 0:
            self._schedule_pump(gap_remaining)
            return
        # Core-gate parity: the playback gate above can't see a response that's
        # still GENERATING (is_active_response, before any voice_play_start) or
        # an SM not-IDLE. Releasing then would have the inner trigger defer the
        # cues into pending_agent_callbacks — OUTSIDE manager ordering, so later
        # same-key/higher-priority cues couldn't coalesce/reorder them. Keep
        # them queued and recheck shortly instead (Codex P2).
        if self._can_release is not None:
            try:
                ok = bool(self._can_release())
            except Exception:
                ok = True  # predicate failure must not wedge delivery
            if not ok:
                self._schedule_pump(self._busy_recheck_s)
                return
        # Gate open: release the ENTIRE pending batch in one shot (sorted by
        # priority), preserving the legacy "near-simultaneous proactive cues
        # are drained into ONE LLM turn" behaviour. The playback gate above
        # already guaranteed she has finished speaking, so this batch won't
        # interrupt audio. Cues that arrive while she speaks accumulate and go
        # out as the next batch after voice_play_end + min-gap.
        batch = sorted(self._queue, key=lambda c: c.sort_key)
        self._queue = []
        self._inflight = True
        self._inflight_deadline = now + self._inflight_timeout_s
        callbacks = [c.callback for c in batch]
        logger.info(
            "[proactive%s] release batch n=%d keys=%s",
            self._suffix(), len(callbacks), [c.coalesce_key for c in batch],
        )
        asyncio.create_task(self._run_deliver(callbacks))
        # Arm the inflight-timeout: if no playback signal arrives (deliver
        # deferred by the inner gate / text with no audio / frontend
        # disconnect) the deadline pump frees the slot so later batches aren't
        # wedged. Normal completion (playback_end / next submit) reschedules a
        # sooner pump anyway.
        self._schedule_pump(self._inflight_timeout_s)

    async def _run_deliver(self, callbacks: list[dict]) -> None:
        callbacks = [cb for cb in callbacks if not cb.get(DELIVERY_RETRACTED_KEY)]
        if not callbacks:
            self._inflight = False
            self._schedule_pump(0.0)
            return
        try:
            await self._deliver(callbacks)
        except Exception:
            for callback in callbacks:
                resolve_callback_delivery_ack(callback, False)
            logger.exception("[proactive%s] deliver failed", self._suffix())
            # Free the slot so the queue isn't wedged on a failed hand-off.
            self._inflight = False
            self._schedule_pump(0.0)
