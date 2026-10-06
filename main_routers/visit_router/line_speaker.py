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

"""One cat line spoken while it is generated, with subtitles paced by the audio (OD-15 v3 / OD-21 v3).

Design §3.6.4 and §5 PR-09a ("streaming TTS and subtitle alignment"). One
line = one :class:`SpeechStream` = one ``speech_id``:

* **Entry** (:meth:`LineSpeaker.feed`): every LLM delta first goes through
  the goodbye cap (``wu`` lines: ``VISIT_GOODBYE_MAX_CHARS``) and the wire
  budget (``WireBudget.take``, measured on the outbound form). Only the
  accepted part is pushed to TTS *and* fed to the ``ClauseSplitter`` -- so
  what is spoken, the subtitle pieces and the final ``text`` are always the
  same prefix. The first cut finishes the stream, cancels the LLM and marks
  the line ``truncated`` (``goodbye_cap`` / ``wire_size``).
* **TTS input is the raw text** (family names included: the audio only plays
  at home); each subtitle piece is ``sanitize_relay_text(strip_emotion_tags(
  clause))`` of the redacted clause.
* **Release** of piece ``i`` once ``min(time since playback started,
  played_ms) >= threshold(i)``, ``threshold(i) = sum(estimate_speech_ms(raw_j)
  for j < i)`` (estimated on the raw text TTS speaks). ``ended{final:false}``
  (TTS caught up with the LLM) releases every piece generated so far and
  re-anchors the thresholds there; only ``ended{final:true}`` after
  :meth:`finish` ends the line (remaining pieces at once, ``tail_ms = 0``).
  The LLM ending only means "no new pieces".
* **Fallbacks** -- every TTS failure is "abort first, then pace by estimate":
  no first progress within ``VISIT_TTS_START_TIMEOUT_S`` of the first push
  (the visit stops using TTS, ``on_tts_fallback``), no progress for
  ``VISIT_SPEECH_PROGRESS_STALL_S`` after playback started (or after
  ``finish``) without the final ``ended``, ``finish()`` answering
  ``no_worker``. Pieces then follow ``played_ms_last + (now - switch time)``;
  deltas after the abort only feed the splitter. Voice off paces by estimate
  from the first delta. The estimate path ends with ``tail_ms`` = the last
  piece's estimate.
* **Interrupt** (:meth:`LineSpeaker.interrupt`): the speech id is dropped from
  the router *before* the stream is aborted (late progress / ``ended`` from
  the cleared pipeline is ignored), the LLM is cancelled, and the result is
  the released prefix, ``truncated`` with the given reason.

Everything is synchronous and driven by an injected clock: events
(:meth:`feed`, :meth:`llm_done`, :meth:`on_progress`, :meth:`interrupt`) plus
:meth:`tick` at :meth:`next_deadline`. :func:`drive` is the asyncio loop
that ticks one speaker until it is done. The real :class:`SpeechStream` is
``SessionManager.open_mirror_speech_stream`` (PR-09b).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Optional, Protocol, Union

from config.visit_settings import (
    VISIT_GOODBYE_MAX_CHARS,
    VISIT_SPEECH_PROGRESS_STALL_S,
    VISIT_TTS_START_TIMEOUT_S,
)
from main_logic.visit.sanitize import (
    clean_relay_text,
    redact_outbound_boundary,
    redact_outbound_with_spans,
    sanitize_relay_text,
    strip_emotion_tags,
)
from utils.logger_config import get_module_logger
from utils.visit_wire import Clause, ClauseSplitter, WireBudget, estimate_speech_ms

logger = get_module_logger(__name__, "Main")

FINISH_NO_WORKER = "no_worker"
"""``SpeechStream.finish()`` result when the TTS worker is gone (the end marker is not queued either)."""

PACED_AUDIO = "audio"
PACED_ESTIMATE = "estimate"


class SpeechStream(Protocol):
    """One streaming mirror speech (``open_mirror_speech_stream``, PR-09b).

    ``push`` / ``finish`` return False once the stream is closed (aborted or
    finished); ``abort`` is terminal and idempotent. ``finish`` returns
    :data:`FINISH_NO_WORKER` when no TTS worker can take the end marker.
    """

    @property
    def speech_id(self) -> str: ...

    def push(self, delta: str) -> bool: ...

    def finish(self) -> Union[bool, str]: ...

    def abort(self) -> bool: ...


OpenStream = Callable[[Callable[[int], None]], SpeechStream]
"""``open_stream(on_enqueued)``: open this line's stream; ``on_enqueued(n)`` reports TTS chars queued."""


@dataclass
class VoiceState:
    """Per-visit TTS state: the ``visitVoiceEnabled`` read at start and the one-way fallback."""

    enabled: bool
    fallen_back: bool = False

    @property
    def tts_on(self) -> bool:
        return self.enabled and not self.fallen_back


@dataclass(frozen=True)
class LineHeader:
    """The fields of this line's ``text`` / first ``line_delta`` known before it is spoken."""

    ln: str
    lp: int
    ad: str
    rt: str
    wu: bool = False
    sp: str = "c"
    lang: Optional[str] = None

    def wire(self) -> dict:
        out = {"ln": self.ln, "sp": self.sp, "ad": self.ad, "rt": self.rt, "wu": self.wu}
        out["lang"] = self.lang
        return out


@dataclass(frozen=True)
class ReleasedPiece:
    """A subtitle piece to send now (``line_delta`` + local ``visit_line_delta``)."""

    index: int
    text: str
    paced: str
    last: bool = False


@dataclass(frozen=True)
class LineResult:
    """How the line ended; ``text`` is exactly the released pieces joined."""

    text: str
    truncated: bool
    trunc_reason: Optional[str]
    tail_ms: int
    pieces: int
    spoken: bool = False
    """True when the end was signalled by the audio itself (``ended{final:true}``)."""


class SpeechRouter:
    """``speech_id`` -> live speaker of one visit; unknown and dropped ids are ignored."""

    def __init__(self) -> None:
        self._speakers: dict[str, "LineSpeaker"] = {}

    def register(self, speech_id: str, speaker: "LineSpeaker") -> None:
        self._speakers[speech_id] = speaker

    def unregister(self, speech_id: Optional[str]) -> None:
        if speech_id is not None:
            self._speakers.pop(speech_id, None)

    def route(self, speech_id: str, *, played_ms: int, ended: bool, final: bool,
              now: Optional[float] = None) -> bool:
        """Hand a ``visit_speech_progress`` to its line; False for unknown / finished ids."""
        speaker = self._speakers.get(speech_id)
        if speaker is None:
            return False
        speaker.on_progress(played_ms, ended=ended, final=final, now=now)
        return True

    def __len__(self) -> int:
        return len(self._speakers)

    def clear(self) -> list["LineSpeaker"]:
        """Forget every speech id (visit stop); returns the speakers that were still registered."""
        speakers = list(self._speakers.values())
        self._speakers.clear()
        return speakers


def _noop(*_args: object) -> None:
    return None


@dataclass
class _Piece:
    clause: Clause
    text: str
    est_ms: int


@dataclass
class _Callbacks:
    on_piece: Callable[[ReleasedPiece], None] = _noop
    on_done: Callable[[LineResult], None] = _noop
    on_cancel_llm: Callable[[], None] = _noop
    on_tts_fallback: Callable[[], None] = _noop
    on_usage: Callable[[dict], None] = _noop
    on_wake: Callable[[], None] = _noop


class LineSpeaker:
    """Speaker of one cat line (see the module docstring)."""

    def __init__(
        self,
        *,
        visit_id: str,
        header: LineHeader,
        family_names: Sequence[str],
        neutral_term: str,
        voice: VoiceState,
        open_stream: Optional[OpenStream],
        router: SpeechRouter,
        clock: Callable[[], float],
        on_piece: Callable[[ReleasedPiece], None] = _noop,
        on_done: Callable[[LineResult], None] = _noop,
        on_cancel_llm: Callable[[], None] = _noop,
        on_tts_fallback: Callable[[], None] = _noop,
        on_usage: Callable[[dict], None] = _noop,
        start_timeout_s: float = VISIT_TTS_START_TIMEOUT_S,
        stall_s: float = VISIT_SPEECH_PROGRESS_STALL_S,
        goodbye_max_chars: int = VISIT_GOODBYE_MAX_CHARS,
    ) -> None:
        names = [n for n in family_names if isinstance(n, str) and n.strip()]

        def redact(text: str):
            return redact_outbound_with_spans(text, family_names=names, replacement=neutral_term)

        boundary = redact_outbound_boundary(names) if names else None
        self.header = header
        self._clock = clock
        self._voice = voice
        self._open_stream = open_stream
        self._router = router
        self._cb = _Callbacks(on_piece, on_done, on_cancel_llm, on_tts_fallback, on_usage)
        self._start_timeout = float(start_timeout_s)
        self._stall = float(stall_s)
        self._budget = WireBudget(
            visit_id=visit_id, header=header.wire(), redact=redact, sanitize=sanitize_relay_text,
            clean=clean_relay_text, redact_boundary=boundary,
        )
        self._splitter = ClauseSplitter(
            redact=redact, holdback_chars=max((len(n) for n in names), default=1) - 1,
            redact_boundary=boundary,
        )
        self._goodbye_left: Optional[int] = int(goodbye_max_chars) if header.wu else None
        self._pieces: list[_Piece] = []
        self._released = 0
        self._emitted: list[str] = []
        self._anchor_index = 0
        self._anchor_ms = 0
        self._llm_done = False
        self._cut_reason: Optional[str] = None
        self._done = False
        self._result: Optional[LineResult] = None
        # TTS
        self._stream: Optional[SpeechStream] = None
        self._stream_dead = False
        self._finished = False
        self._finish_at: Optional[float] = None
        self._first_push_at: Optional[float] = None
        self._play_start_at: Optional[float] = None
        self._last_progress_at: Optional[float] = None
        self._played_ms = 0
        self._drained = False
        # 估时
        self._mode = PACED_AUDIO if voice.tts_on and open_stream is not None else PACED_ESTIMATE
        self._est_base_ms = 0
        self._est_origin: Optional[float] = None

    # ── 查询 ─────────────────────────────────────────────────────────

    @property
    def done(self) -> bool:
        return self._done

    @property
    def result(self) -> Optional[LineResult]:
        return self._result

    @property
    def speech_id(self) -> Optional[str]:
        return self._stream.speech_id if self._stream is not None else None

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def released_text(self) -> str:
        return "".join(self._emitted)

    @property
    def spoken_text(self) -> str:
        """Raw text accepted for this line (what TTS was given)."""
        return self._budget.accepted_text

    def set_wake(self, wake: Callable[[], None]) -> None:
        """Called whenever an event may have moved :meth:`next_deadline` (used by :func:`drive`)."""
        self._cb.on_wake = wake

    def _now(self, now: Optional[float]) -> float:
        return float(self._clock() if now is None else now)

    # ── 入口 ─────────────────────────────────────────────────────────

    def feed(self, delta: str, *, now: Optional[float] = None) -> str:
        """One LLM delta; returns the part accepted for TTS and subtitles."""
        if self._done or self._llm_done or not delta:
            return ""
        now = self._now(now)
        capped = delta
        goodbye_cut = False
        if self._goodbye_left is not None:
            capped = delta[: self._goodbye_left]
            goodbye_cut = len(capped) < len(delta)
        accepted = self._budget.take(capped) if capped else ""
        if self._goodbye_left is not None:
            self._goodbye_left -= len(accepted)
        if accepted:
            self._speak(accepted, now)
            self._add_clauses(self._splitter.feed(accepted))
        if self._budget.exhausted or goodbye_cut:
            self._cut_reason = "wire_size" if self._budget.exhausted else "goodbye_cap"
            self._cb.on_cancel_llm()
            self._end_of_text(now)
        else:
            self._advance(now)
        self._cb.on_wake()
        return accepted

    def llm_done(self, *, now: Optional[float] = None) -> None:
        """The LLM finished (or was cancelled): no more pieces will be generated."""
        if self._done or self._llm_done:
            return
        self._end_of_text(self._now(now))
        self._cb.on_wake()

    def on_progress(self, played_ms: int, *, ended: bool, final: bool, now: Optional[float] = None) -> None:
        """A ``visit_speech_progress`` of this line's speech id."""
        if self._done or self._mode != PACED_AUDIO or self._stream_dead:
            return
        now = self._now(now)
        played = max(0, int(played_ms)) if isinstance(played_ms, int) else self._played_ms
        if self._play_start_at is None:
            self._play_start_at = now - played / 1000.0
        self._played_ms = max(self._played_ms, played)
        self._last_progress_at = now
        if ended and final and self._finished:
            # 前端已收到本行结束标记、排程终点已播过：剩余分片一次放出，没有尾巴要等
            self._release_through(len(self._pieces), PACED_AUDIO)
            self._complete(tail_ms=0, spoken=True)
        elif ended:
            # TTS 追上了 LLM：已生成的分片都念过了，放出并以此为新锚点；之后的分片随新音频按 played_ms 放
            self._drained = True
            self._release_through(len(self._pieces), PACED_AUDIO)
            self._anchor_index, self._anchor_ms = len(self._pieces), self._played_ms
        else:
            self._drained = False
            self._advance(now)
        self._cb.on_wake()

    def interrupt(self, reason: str, *, now: Optional[float] = None) -> Optional[LineResult]:
        """Stop now (human interrupt, wrap-up cut, visit end); returns the result (None if already done)."""
        if self._done:
            return None
        # 先登记终止（之后的进度 / 清管线回报的 ended 一律忽略），再清 TTS 管线
        self._router.unregister(self.speech_id)
        self._stop_stream()
        if not self._llm_done:
            self._cb.on_cancel_llm()
        self._llm_done = True
        result = LineResult(text=self.released_text, truncated=True, trunc_reason=reason, tail_ms=0,
                            pieces=len(self._emitted))
        self._finish_line(result)
        return result

    # ── 计时 ─────────────────────────────────────────────────────────

    def next_deadline(self) -> Optional[float]:
        """When :meth:`tick` must run next (clock units), or None while only events can move it."""
        if self._done:
            return None
        if self._mode == PACED_ESTIMATE:
            if self._est_origin is None or self._released >= len(self._pieces):
                return None
            due = self._threshold(self._released) - self._est_base_ms
            return self._est_origin + max(0, due) / 1000.0
        if self._stream is None or self._stream_dead:
            return None
        if self._last_progress_at is None:
            return None if self._first_push_at is None else self._first_push_at + self._start_timeout
        if self._finished:
            return max(self._finish_at or 0.0, self._last_progress_at) + self._stall
        if self._drained:
            return None
        return self._last_progress_at + self._stall

    def tick(self, *, now: Optional[float] = None) -> None:
        """Run timers due at ``now``: start timeout, progress stall, estimate releases."""
        if self._done:
            return
        now = self._now(now)
        if self._mode == PACED_AUDIO and self._stream is not None and not self._stream_dead:
            deadline = self.next_deadline()
            if deadline is not None and now >= deadline:
                if self._last_progress_at is None:
                    self._fallback(now, start_timeout=True)
                else:
                    logger.info("visit line %s: speech progress stalled, pacing by estimate", self.header.ln)
                    self._to_estimate(now)
        self._advance(now)
        self._cb.on_wake()

    # ── 内部 ─────────────────────────────────────────────────────────

    def _speak(self, accepted: str, now: float) -> None:
        if self._mode != PACED_AUDIO:
            if self._est_origin is None:
                self._est_origin = now
            return
        if self._stream is None:
            if not self._open(now):
                return
        if self._stream_dead or self._stream is None:
            return
        if self._first_push_at is None:
            self._first_push_at = now
        self._stream.push(accepted)

    def _open(self, now: float) -> bool:
        try:
            stream = self._open_stream(lambda n: self._usage_chars(n))  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 - TTS 未就绪：本场改走估时
            logger.warning("visit line %s: speech stream unavailable: %s", self.header.ln, type(exc).__name__)
            stream = None
        if stream is None:
            self._fallback(now, start_timeout=True)
            return False
        self._stream = stream
        self._router.register(stream.speech_id, self)
        self._cb.on_usage({"tts_requests": 1})
        return True

    def _usage_chars(self, n: int) -> None:
        if isinstance(n, int) and n > 0:
            self._cb.on_usage({"tts_chars": n})

    def _add_clauses(self, clauses: Sequence[Clause]) -> None:
        for clause in clauses:
            text = sanitize_relay_text(strip_emotion_tags(clause.text))
            self._pieces.append(_Piece(clause=clause, text=text, est_ms=estimate_speech_ms(clause.raw)))

    def _threshold(self, index: int) -> int:
        start = min(self._anchor_index, index)
        return self._anchor_ms + sum(p.est_ms for p in self._pieces[start:index])

    def _reference_ms(self, now: float) -> Optional[int]:
        if self._mode == PACED_ESTIMATE:
            if self._est_origin is None:
                return None
            return self._est_base_ms + round((now - self._est_origin) * 1000)
        if self._play_start_at is None:
            return None
        return min(round((now - self._play_start_at) * 1000), self._played_ms)

    def _advance(self, now: float) -> None:
        if self._done:
            return
        ref = self._reference_ms(now)
        if ref is not None:
            while self._released < len(self._pieces) and ref >= self._threshold(self._released):
                self._release_one(self._mode)
        if self._mode == PACED_ESTIMATE and self._llm_done and self._released >= len(self._pieces):
            last = self._pieces[-1].est_ms if self._pieces else 0
            self._complete(tail_ms=last, spoken=False)

    def _release_through(self, count: int, paced: str) -> None:
        while self._released < min(count, len(self._pieces)):
            self._release_one(paced)

    def _release_one(self, paced: str) -> None:
        piece = self._pieces[self._released]
        self._released += 1
        if not piece.text:
            return
        last = self._llm_done and self._released == len(self._pieces)
        index = len(self._emitted)
        self._emitted.append(piece.text)
        self._cb.on_piece(ReleasedPiece(index=index, text=piece.text, paced=paced, last=last))

    def _end_of_text(self, now: float) -> None:
        if self._llm_done:
            return
        self._llm_done = True
        self._add_clauses(self._splitter.flush())
        if self._mode == PACED_AUDIO and self._stream is not None and not self._stream_dead:
            outcome = self._stream.finish()
            self._finished = True
            self._finish_at = now
            if outcome == FINISH_NO_WORKER:
                logger.warning("visit line %s: TTS worker gone at finish, pacing by estimate", self.header.ln)
                self._to_estimate(now)
        elif self._mode == PACED_AUDIO and self._stream is None:
            # 一个字都没接纳（空回复 / 首段就被截掉）：没开过流，按估时收口
            self._to_estimate(now)
        self._advance(now)

    def _fallback(self, now: float, *, start_timeout: bool) -> None:
        if start_timeout and not self._voice.fallen_back:
            self._voice.fallen_back = True
            self._cb.on_tts_fallback()
        self._to_estimate(now)

    def _to_estimate(self, now: float) -> None:
        """Abort the stream (old audio never plays later) and pace the rest by estimate."""
        self._stop_stream()
        if self._mode == PACED_ESTIMATE:
            return
        self._mode = PACED_ESTIMATE
        self._est_base_ms = self._played_ms if self._play_start_at is not None else 0
        self._est_origin = now

    def _stop_stream(self) -> None:
        stream = self._stream
        if stream is None or self._stream_dead:
            self._stream_dead = True
            return
        self._router.unregister(stream.speech_id)
        self._stream_dead = True
        try:
            stream.abort()
        except Exception as exc:  # noqa: BLE001 - 中止失败不能挡住估时收口
            logger.warning("visit line %s: speech abort failed: %s", self.header.ln, type(exc).__name__)

    def _complete(self, *, tail_ms: int, spoken: bool) -> None:
        truncated = self._cut_reason is not None
        result = LineResult(text=self.released_text, truncated=truncated, trunc_reason=self._cut_reason,
                            tail_ms=int(tail_ms), pieces=len(self._emitted), spoken=spoken)
        self._finish_line(result)

    def _finish_line(self, result: LineResult) -> None:
        self._done = True
        self._result = result
        self._router.unregister(self.speech_id)
        self._cb.on_done(result)


async def drive(speaker: LineSpeaker, *, clock: Callable[[], float]) -> Optional[LineResult]:
    """Tick ``speaker`` at its deadlines until it is done; returns its result.

    Events (deltas, progress, interrupts) wake the loop through
    :meth:`LineSpeaker.set_wake`; cancelling this task does not end the line.
    """
    wake = asyncio.Event()
    speaker.set_wake(wake.set)
    while not speaker.done:
        deadline = speaker.next_deadline()
        wake.clear()
        if deadline is None:
            await wake.wait()
        else:
            delay = deadline - clock()
            if delay > 0:
                try:
                    await asyncio.wait_for(wake.wait(), delay)
                except asyncio.TimeoutError:
                    pass
        speaker.tick()
    return speaker.result

