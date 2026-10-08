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

"""When a finished visit hands its parked plugin callbacks back (design §3.2.6 item 22, step 4).

After ``release_takeover`` the callbacks still park in the visit inbox
(``hold_callbacks``) until the home-coming line (ritual) and the debrief
summary have been spoken. Both are mirror speeches with their own
``speech_id``; each is registered here *before* its text is pushed to TTS,
in a table that does not depend on the route state (the route slot is gone
by the time the last ``visit_speech_progress{ended}`` arrives).

A segment is *done* once its ``ended{final:true}`` arrived (``final:false``
is only a drained queue that may resume), it was interrupted (the family
spoke after it was queued), or it was skipped (never spoken). The
handoff is due at the first of:

* every expected segment done;
* voice off: all segments shown, plus the sum of their speech estimates;
* ``VISIT_INBOX_HANDOFF_MAX_S`` after the last segment was queued, unless a
  segment is still visibly playing (fresh progress, no ``ended``);
* the absolute deadline ``max(finalize + 30 s, all queued + estimates + 10 s)``
  capped at ``finalize + VISIT_INBOX_HANDOFF_ABS_MAX_S`` -- callbacks are
  always re-delivered, never dropped.

The time while the two LLM calls generate does not count toward the 20 s.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from config.visit_settings import (
    VISIT_INBOX_HANDOFF_ABS_MAX_S,
    VISIT_INBOX_HANDOFF_MAX_S,
    VISIT_SPEECH_PROGRESS_STALL_S,
)

_ABS_FLOOR_S = 30.0
_ABS_MARGIN_S = 10.0

SEGMENTS = ("ritual", "debrief")


@dataclass
class _Segment:
    speech_id: Optional[str] = None
    voiced: bool = False
    est_ms: int = 0
    queued_at: Optional[float] = None
    done: bool = False
    ended: bool = False
    last_progress_at: Optional[float] = None


class InboxHandoff:
    """Handoff bookkeeping of one finished visit (see the module docstring)."""

    def __init__(self, visit_id: str, *, finalize_at: float, clock: Callable[[], float]) -> None:
        self.visit_id = visit_id
        self.finalize_at = float(finalize_at)
        self._clock = clock
        self._segments = {name: _Segment() for name in SEGMENTS}
        self.handed_off = False

    # —— 登记 ——

    def attach_speech(self, name: str, speech_id: str) -> None:
        """Register the speech id of a segment before its text reaches TTS."""
        seg = self._segments[name]
        seg.speech_id = speech_id
        seg.voiced = True
        _by_speech[speech_id] = self

    def mark_queued(self, name: str, est_ms: int) -> None:
        """The segment's text was pushed to TTS (or shown, voice off)."""
        seg = self._segments[name]
        if seg.queued_at is None:
            seg.queued_at = self._clock()
        seg.est_ms = max(0, int(est_ms))

    def skip(self, name: str) -> None:
        """The segment is not spoken at all (abandoned, or text only): counts as queued and done."""
        seg = self._segments[name]
        if seg.queued_at is None:
            seg.queued_at = self._clock()
        seg.done = True

    def abandon(self) -> None:
        """The exit flow failed: segments not queued yet will never be spoken (skip them)."""
        for name, seg in self._segments.items():
            if seg.queued_at is None:
                self.skip(name)

    def mark_done(self, name: str) -> None:
        """The segment ended or was interrupted (an ``ended`` arriving later changes nothing)."""
        self._segments[name].done = True

    def interrupted_since(self, last_input: float, input_stamps: dict[str, float]) -> None:
        """Mark voiced segments done when the family spoke after they were queued.

        ``input_stamps[name]`` is the ordinary-input wall time read when the
        segment was queued; a later ``last_input`` means a new ordinary turn
        took the floor (it interrupts the mirror speech).
        """
        for name, seg in self._segments.items():
            stamp = input_stamps.get(name)
            if seg.voiced and seg.queued_at is not None and stamp is not None and last_input > stamp:
                seg.done = True

    def on_progress(self, speech_id: str, *, ended: bool, final: bool) -> bool:
        """A ``visit_speech_progress`` for one of the registered speech ids; False when unknown."""
        for seg in self._segments.values():
            if seg.speech_id == speech_id:
                seg.last_progress_at = self._clock()
                if ended and final:
                    seg.ended = True
                    seg.done = True
                return True
        return False

    # —— 判定 ——

    def _all_queued_at(self) -> Optional[float]:
        times = [seg.queued_at for seg in self._segments.values()]
        if any(t is None for t in times):
            return None
        return max(t for t in times if t is not None)

    def absolute_deadline(self) -> float:
        """Callbacks are re-delivered at the latest by then (never dropped)."""
        cap = self.finalize_at + VISIT_INBOX_HANDOFF_ABS_MAX_S
        queued = self._all_queued_at()
        if queued is None:
            return cap
        est = sum(seg.est_ms for seg in self._segments.values()) / 1000.0
        return min(max(self.finalize_at + _ABS_FLOOR_S, queued + est + _ABS_MARGIN_S), cap)

    def _playing(self, now: float) -> bool:
        return any(
            seg.voiced and not seg.done and seg.last_progress_at is not None
            and now - seg.last_progress_at < VISIT_SPEECH_PROGRESS_STALL_S
            for seg in self._segments.values()
        )

    def due(self, now: Optional[float] = None) -> bool:
        """True once the parked callbacks may be handed back."""
        now = self._clock() if now is None else float(now)
        if now >= self.absolute_deadline():
            return True
        queued = self._all_queued_at()
        if queued is None:
            return False
        segments = self._segments.values()
        if all(seg.done for seg in segments):
            return True
        if not any(seg.voiced for seg in segments):
            # 语音关：没有 TTS，与插件回调抢不了声音；按两段文本的估时交还
            est = sum(seg.est_ms for seg in segments) / 1000.0
            return now >= queued + est
        if now >= queued + VISIT_INBOX_HANDOFF_MAX_S:
            return not self._playing(now)
        return False

    def close(self) -> None:
        """Forget the speech ids (handoff done)."""
        self.handed_off = True
        for seg in self._segments.values():
            if seg.speech_id is not None and _by_speech.get(seg.speech_id) is self:
                del _by_speech[seg.speech_id]


_by_speech: dict[str, InboxHandoff] = {}


def route_progress(speech_id: str, *, ended: bool, final: bool) -> bool:
    """Deliver a progress report to the handoff that registered ``speech_id`` (route may be gone)."""
    handoff = _by_speech.get(speech_id)
    if handoff is None:
        return False
    return handoff.on_progress(speech_id, ended=ended, final=final)


def pending_speech_ids() -> int:
    """Number of registered handoff speech ids (diagnostics / tests)."""
    return len(_by_speech)


def _reset_for_tests() -> None:
    _by_speech.clear()
