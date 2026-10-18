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
  at home) minus the decoration tags (``<happy>``, stripped across deltas by
  :class:`EmotionTagFilter` before both TTS and the splitter, so a tag is
  never read aloud nor cut in half by a clause boundary); each subtitle
  piece is ``sanitize_relay_text(strip_emotion_tags(clause))`` of the
  redacted clause, and the thresholds estimate the tag-free raw text after
  the same markdown / bracket / muted-symbol stripping the chat TTS path
  applies (a stage direction in parentheses is not spoken, so it adds no
  time).
* **Release** of piece ``i`` once ``min(time since playback started,
  played_ms) >= threshold(i)``, ``threshold(i) = sum(estimate_speech_ms(raw_j)
  for j < i)`` (estimated on the raw text TTS speaks). ``ended{final:false}``
  (the audio queued so far has played, the LLM is still writing) releases
  nothing by itself -- pieces still follow ``played_ms``, since generated
  clauses may still be waiting for synthesis -- it only pauses the stall
  timer until new audio is queued; only ``ended{final:true}`` after
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
    drop_emotion_tags,
    fold_text,
    redact_outbound_boundary,
    redact_outbound_with_spans,
    sanitize_relay_text,
    strip_emotion_tags,
)
from utils.frontend_utils import TtsBracketStripper, TtsMarkdownStripper, strip_tts_muted_symbols
from utils.logger_config import get_module_logger
from utils.visit_wire import Clause, ClauseSplitter, WireBudget, estimate_speech_ms, grapheme_safe_cut

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
    def speech_id(self) -> str:
        """The speech id progress reports carry."""

    def push(self, delta: str) -> bool:
        """Queue more text; False once the stream is closed."""

    def finish(self) -> Union[bool, str]:
        """Queue the end marker; :data:`FINISH_NO_WORKER` when no worker can take it."""

    def abort(self) -> bool:
        """Drop what is queued and stop (terminal, idempotent)."""


OpenStream = Callable[[Callable[[int], None]], SpeechStream]
"""``open_stream(on_enqueued)``: open this line's stream; ``on_enqueued(n)`` reports TTS chars queued."""

_GOODBYE_HOLD_WINDOW = 16
"""Goodbye lines hold back their trailing grapheme cluster only within this many code points of the cap."""


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


class EmotionTagFilter:
    """Streaming :func:`drop_emotion_tags`: an unclosed ``<...`` tail waits for the next delta.

    A tail longer than any tag (``<`` + up to 33 more characters) cannot
    become one and is let through; :meth:`flush` returns whatever is still
    held (an unclosed ``<`` is ordinary text then).
    """

    _HOLD_MAX = 34

    def __init__(self) -> None:
        self._held = ""

    def feed(self, text: str) -> str:
        text = self._held + (text or "")
        self._held = ""
        cut = text.rfind("<")
        if cut != -1 and ">" not in text[cut:] and len(text) - cut <= self._HOLD_MAX:
            text, self._held = text[:cut], text[cut:]
        return drop_emotion_tags(text)

    def flush(self) -> str:
        held, self._held = self._held, ""
        return held


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
            # When a line truncates early (goodbye hard cap / wire budget), final flush must replace even a trailing partial user name:
            # truncation may land inside the name, and that remaining prefix must not leave the system
            return redact_outbound_with_spans(text, family_names=names, replacement=neutral_term,
                                              partial_tail=self._closing_cut)

        def budget_redact(text: str) -> str:
            # Always measure the budget as if truncating here: replace a trailing partial name with neutral wording just as finalization does,
            # ensuring final text cannot exceed its approved length even when neutral wording is longer than the name prefix
            return redact_outbound_with_spans(text, family_names=names, replacement=neutral_term,
                                              partial_tail=True)[0]

        boundary = redact_outbound_boundary(names) if names else None
        self._closing_cut = False
        self.header = header
        self._clock = clock
        self._voice = voice
        self._open_stream = open_stream
        self._router = router
        self._cb = _Callbacks(on_piece, on_done, on_cancel_llm, on_tts_fallback, on_usage)
        self._start_timeout = float(start_timeout_s)
        self._stall = float(stall_s)
        self._budget = WireBudget(
            visit_id=visit_id, header=header.wire(), redact=budget_redact, sanitize=sanitize_relay_text,
            clean=clean_relay_text, redact_boundary=boundary,
        )
        self._tags = EmotionTagFilter()
        # Estimate from actual TTS synthesis text, using main chat's markdown/parentheses/mute-symbol stripping with state across clauses
        self._est_bracket = TtsBracketStripper()
        # Determine speakability on push through the same stripping chain as TTS, maintaining state across pushes
        self._push_markdown = TtsMarkdownStripper()
        self._push_bracket = TtsBracketStripper()
        self._splitter = ClauseSplitter(
            redact=redact, holdback_chars=max((len(n) for n in names), default=1) - 1,
            redact_boundary=boundary,
        )
        self._goodbye_left: Optional[int] = int(goodbye_max_chars) if header.wu else None
        # Near the goodbye budget, a segment's final grapheme may continue in the next segment (a flag's second regional indicator, ZWJ sequence,
        # combining mark); withhold it until the next segment/finalization so truncation can retreat to the cluster start. Far below budget,
        # do not withhold it, avoiding one-character delays in incremental playback
        self._goodbye_hold = ""
        # Released goodbye text: segment the combined released text + current segment, evaluating cross-segment graphemes as whole clusters
        self._goodbye_taken = ""
        # Withhold trailing text that might start the user's name before sentence splitting: punctuation splitting retains no tail ("J. Smith"), and hard splits reserve only
        # original code-point tails, missing names expanded by zero-width characters ("小" + zero-width + "明"); prefixes split before the full name arrives
        # cannot be recognized and would leak unchanged. Determine withholding using folded text
        self._held_names = sorted({k for k in (fold_text(n.strip()) for n in names) if k})
        self._held_max = max((len(k) for k in self._held_names), default=0)
        self._name_hold = ""
        self._pieces: list[_Piece] = []
        self._released = 0
        self._emitted: list[str] = []
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
        # Time estimates
        self._mode = PACED_AUDIO if voice.tts_on and open_stream is not None else PACED_ESTIMATE
        self._est_base_ms = 0
        self._est_origin: Optional[float] = None

    # ── Queries ─────────────────────────────────────────────────────────

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

    # ── Entry points ─────────────────────────────────────────────────────────

    def feed(self, delta: str, *, now: Optional[float] = None) -> str:
        """One LLM delta; returns the part accepted for TTS and subtitles."""
        if self._done or self._llm_done or not delta:
            return ""
        now = self._now(now)
        # Strip tags before goodbye caps and wire budgets so measurements match outgoing text and recognize names split by tags
        accepted = self._admit(self._tags.feed(delta))
        if accepted:
            self._take_text(accepted, now)
        if self._cut_reason is not None:
            self._cb.on_cancel_llm()
            self._end_of_text(now)
        else:
            self._advance(now)
        self._cb.on_wake()
        return accepted

    def _admit(self, text: str, *, final: bool = False) -> str:
        """Goodbye cap then wire budget on tag-free text; sets ``_cut_reason`` on the first cut."""
        if self._cut_reason is not None:
            return ""
        if self._goodbye_left is not None:
            text, self._goodbye_hold = self._goodbye_hold + text, ""
        if not text:
            return ""
        capped = text
        goodbye_cut = False
        if self._goodbye_left is not None:
            left = self._goodbye_left
            if len(text) > left:
                # Retreat truncation to a grapheme boundary in released text + current segment; released text is irreversible, so retreat no farther than its end
                taken = len(self._goodbye_taken)
                cut = max(grapheme_safe_cut(self._goodbye_taken + text, taken + left), taken)
                capped = text[: cut - taken]
            elif not final and left - len(text) < _GOODBYE_HOLD_WINDOW:
                keep = grapheme_safe_cut(text, len(text) - 1)
                capped, self._goodbye_hold = text[:keep], text[keep:]
            goodbye_cut = len(capped) + len(self._goodbye_hold) < len(text)
        accepted = self._budget.take(capped) if capped else ""
        if self._goodbye_left is not None:
            self._goodbye_left -= len(accepted)
            self._goodbye_taken += accepted
        if self._budget.exhausted or goodbye_cut:
            self._cut_reason = "wire_size" if self._budget.exhausted else "goodbye_cap"
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
        # The watchdog recognizes only advancing progress: frozen playback still periodically reports unchanged played_ms,
        # so refreshing each report would prevent stall detection forever; terminal ended is handled separately
        if self._last_progress_at is None or played > self._played_ms:
            self._last_progress_at = now
        self._played_ms = max(self._played_ms, played)
        if ended and final and self._finished:
            # Frontend received the line's end marker and passed the scheduled endpoint: release remaining chunks together, with no tail left to await
            self._release_through(len(self._pieces), PACED_AUDIO)
            self._complete(tail_ms=0, spoken=True)
        elif ended:
            # Queued audio drained while the LLM still writes: do not release subtitles on that alone, because split chunks may still be synthesizing;
            # continue following played_ms, but suspend stall timing until new audio queues
            self._drained = True
            self._advance(now)
        else:
            self._drained = False
            self._advance(now)
        self._cb.on_wake()

    def interrupt(self, reason: str, *, now: Optional[float] = None) -> Optional[LineResult]:
        """Stop now (human interrupt, wrap-up cut, visit end); returns the result (None if already done)."""
        if self._done:
            return None
        # Register termination first, ignoring later progress/ended from pipeline clearing, then clear TTS
        self._router.unregister(self.speech_id)
        self._stop_stream()
        if not self._llm_done:
            self._cb.on_cancel_llm()
        self._llm_done = True
        result = LineResult(text=self.released_text, truncated=True, trunc_reason=reason, tail_ms=0,
                            pieces=len(self._emitted))
        self._finish_line(result)
        return result

    # ── Timing ─────────────────────────────────────────────────────────

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
            if self._first_push_at is not None:
                return self._first_push_at + self._start_timeout
            # No character has entered synthesis yet: allow the same duration after the final marker, since final-flush text may still be in flight
            return self._finish_at + self._start_timeout if self._finished else None
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
                if self._last_progress_at is None and self._first_push_at is None:
                    # The whole line stripped to silent content is not a TTS failure; finalize only this line using estimates
                    self._to_estimate(now)
                elif self._last_progress_at is None:
                    self._fallback(now, start_timeout=True)
                else:
                    logger.info("visit line %s: speech progress stalled, pacing by estimate", self.header.ln)
                    self._to_estimate(now)
        self._advance(now)
        self._cb.on_wake()

    # ── Internals ─────────────────────────────────────────────────────────

    def _take_text(self, text: str, now: float) -> None:
        """Tag-free accepted text: to TTS and to the splitter, the same string."""
        if not text:
            return
        if self._mode != PACED_AUDIO:
            if self._est_origin is None:
                self._est_origin = now
        else:
            self._push_tts(text, now)
        if self._held_names:
            text = self._name_hold + text
            cut = self._name_prefix_start(text)
            text, self._name_hold = text[:cut], text[cut:]
        if text:
            self._add_clauses(self._splitter.feed(text))

    def _name_prefix_start(self, text: str) -> int:
        """Start of the longest suffix of ``text`` that is a proper prefix of a (folded) family name."""
        # Retreat until folded length exceeds the longest name; zero-width/other empty-folding characters consume no slots regardless of how far back they extend
        lo, folded_len = len(text), 0
        while lo > 0 and folded_len <= self._held_max:
            lo -= 1
            folded_len += len(fold_text(text[lo]))
        for start in range(lo, len(text)):
            folded = fold_text(text[start:])
            if folded and any(len(folded) < len(k) and k.startswith(folded) for k in self._held_names):
                return start
        return len(text)

    def _push_tts(self, text: str, now: float) -> None:
        if not text:
            return
        if self._voice.fallen_back and self._first_push_at is None:
            # Another line switched this session to estimation before this line pushed speakable text (no stream, or only stage directions received):
            # stop opening/pushing streams and estimate directly
            self._to_estimate(now)
            return
        if self._stream is None:
            if not self._open(now):
                return
        if self._stream_dead or self._stream is None:
            return
        if not self._stream.push(text):
            # The stream closed externally (worker exit, etc.), so no audio can arrive; release subtitles using estimates immediately, without waiting for stall fallback.
            # Rejection before any progress, including immediate stream closure, equals playback-start timeout; disable TTS for this session
            logger.warning("visit line %s: speech stream closed on push, pacing by estimate", self.header.ln)
            if self._last_progress_at is None:
                self._fallback(now, start_timeout=True)
            else:
                self._to_estimate(now)
            return
        if not self._speakable(text):
            # TTS strips this to empty (stage directions, partial markdown): silent content starts/restarts no timers
            return
        if self._first_push_at is None:
            # Start playback timing when the first speakable text is pushed, including text pending while TTS is unready
            self._first_push_at = now
        if self._drained:
            # New speakable text queues after drain: restart stall timing now
            self._drained = False
            self._last_progress_at = now

    def _speakable_tail(self) -> bool:
        """Whether text the push-side strippers still hold becomes speakable when the line ends."""
        tail = self._push_markdown.flush()
        spoken = strip_tts_muted_symbols(self._push_bracket.feed(tail) + self._push_bracket.flush())
        return bool(spoken.strip())

    def _speakable(self, text: str) -> bool:
        """Whether ``text`` (one push) contains anything the TTS chain would actually speak."""
        spoken = strip_tts_muted_symbols(self._push_bracket.feed(self._push_markdown.feed(text)))
        return bool(spoken.strip())

    def _open(self, now: float) -> bool:
        try:
            stream = self._open_stream(self._usage_chars)
        except Exception as exc:  # noqa: BLE001 - TTS unready: switch this session to estimates
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
            if self._first_push_at is None:
                # Push did not recognize speakable content (partial markdown flushed only at finalization), but TTS actually queued it: start timing now
                self._first_push_at = self._now(None)
                self._cb.on_wake()
            self._cb.on_usage({"tts_chars": n})

    def _add_clauses(self, clauses: Sequence[Clause]) -> None:
        for clause in clauses:
            text = sanitize_relay_text(strip_emotion_tags(clause.text))
            self._pieces.append(_Piece(clause=clause, text=text, est_ms=self._spoken_estimate(clause.raw)))

    def _spoken_estimate(self, raw: str) -> int:
        """Estimate of what TTS actually synthesizes from ``raw`` (0 when nothing of it is spoken).

        Markdown is stripped per clause and flushed (an unclosed marker's text
        is still spoken by the TTS end-of-line flush, and must be timed in its
        own clause); brackets keep their state across the line's clauses (a
        stage direction can span a sentence end).
        """
        markdown = TtsMarkdownStripper()
        plain = markdown.feed(raw) + markdown.flush(keep_symbol_markers=True)
        spoken = strip_tts_muted_symbols(self._est_bracket.feed(plain))
        return estimate_speech_ms(spoken) if spoken.strip() else 0

    def _threshold(self, index: int) -> int:
        return sum(p.est_ms for p in self._pieces[:index])

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
        # A retained partial '<...' is not a tag at finalization: budget, speak, and subtitle it normally; discard directly for already-truncated lines
        self._take_text(self._admit(self._tags.flush(), final=True), now)
        self._closing_cut = self._cut_reason is not None
        if self._name_hold:
            self._add_clauses(self._splitter.feed(self._name_hold))
            self._name_hold = ""
        self._add_clauses(self._splitter.flush())
        if self._mode == PACED_AUDIO and self._stream is not None and not self._stream_dead:
            if self._first_push_at is None and self._speakable_tail():
                # Unclosed markdown withheld on push becomes speakable after finalization; begin playback timing now
                self._first_push_at = now
            outcome = self._stream.finish()
            self._finished = True
            self._finish_at = now
            if outcome == FINISH_NO_WORKER or outcome is False:
                # Worker exited/stream closed: cannot send the end marker, so endpoint ended will never arrive
                logger.warning("visit line %s: TTS stream did not take the end marker, pacing by estimate",
                               self.header.ln)
                if self._last_progress_at is None and self._first_push_at is not None:
                    # Failure after speakable text was pushed but before any playback equals playback-start failure; disable TTS for this session
                    self._fallback(now, start_timeout=True)
                else:
                    self._to_estimate(now)
        elif self._mode == PACED_AUDIO and self._stream is None:
            # No text accepted (empty reply / first segment truncated): no stream ever opened, so finalize with estimates
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
        except Exception as exc:  # noqa: BLE001 - abort failures must not block estimated finalization
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
        # Wake drive: interruptions can occur without timers (LLM thinking, voice disabled and fully released, or drained audio),
        # otherwise it would wait forever
        self._cb.on_wake()


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
                    pass        # Deadline reached: handle in tick below
        speaker.tick()
    return speaker.result

