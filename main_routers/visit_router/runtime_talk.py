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

"""Speaking side of a visit runtime (design §3.2.4–§3.2.6, §3.6.3, §3.6.4, §4.5 ``stream_data``).

One cat line = one :class:`~main_routers.visit_router.line_speaker.LineSpeaker`
= one ``speech_id``: the isolated session's deltas go through the speaker
(goodbye cap, wire budget, TTS, clause splitter); every released piece is a
``line_delta`` plus a local ``visit_line_delta``; the end of the line --
normal, interrupted, cut by the wrap-up or by the end of the visit -- is
always one ``text{final}`` queued synchronously in the speaker's ``on_done``
(so it precedes any later reliable message such as the human line that cut
it or the ``leave``), then the spool / upload record and the history.

The family's line (host only, §4.5 ``stream_data``): reserve outbox bytes
(a releasable token), ``on_local_human_line``, persist (spool + upload
record), enqueue with the reservation, and ``mirror_user_input`` last.

History: every line goes into the isolated session with its total-order key
``(lp, side_rank)`` and nothing else stays there. A turn's prompt is a system
notice (arrival / your turn / goodbye); after the turn the prompt and the AI
message ``stream_text`` appended are taken out again, and the line is added
as what was actually released (plus ``VISIT_MARK_INTERRUPTED`` when it was
cut off). Both sides therefore feed their LLM the same ordered lines.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from config.visit_settings import (
    VISIT_CEREMONY_TIMEOUT_S,
    VISIT_GOODBYE_LLM_TIMEOUT_S,
    VISIT_HUMAN_LINE_MAX_TOKENS,
    VISIT_LLM_ERROR_FINALIZE_COUNT,
    VISIT_LLM_TIMEOUT_S,
    VISIT_STREAM_DELTAS,
)
from main_logic.visit.room import LineRef, ReplyPlan
from main_logic.visit.sanitize import (
    clamp_peer_line,
    clamp_text_utf8,
    defang_markdown_media,
    make_envelope_nonce,
    redact_outbound,
    sanitize_relay_text,
    strip_emotion_tags,
    wrap_nonce_envelope,
)
from main_routers.visit_router.line_speaker import LineHeader, LineResult, LineSpeaker, ReleasedPiece, drive
from main_routers.visit_router.runtime_common import (
    NATURAL_REASONS,
    PHASE_WRAP_UP,
    WAITING_PHASES,
    addressee_code,
    decode_addressee,
    fixed_line_kind,
    side_prefix,
)
from main_routers.visit_router.session_pool import (
    append_visit_message,
    estimate_turn_usage,
    sort_key,
    sort_visit_history,
    trim_visit_history,
)
from utils.logger_config import get_module_logger
from utils.visit_wire import SILENCING_TRUNC_REASONS, estimate_speech_ms, fit_text_to_wire

logger = get_module_logger(__name__, "Main")

LINE_ABORT_REASONS = frozenset({"human_interrupt", "wrap_up", "tts_error", "llm_error"})
_BUSY_RETRY_S = 1.0
_LINE_CLOSE_WAIT_S = 5.0
_HOME_TURN_WAIT_S = 30.0
_HOME_TURN_START_S = 3.0
_LLM_SETTLE_S = 2.0


@dataclass(eq=False)
class _LineRun:
    """One own cat line in progress."""

    ref: LineRef
    header: LineHeader
    reply_to: Optional[LineRef]
    goodbye: bool
    prompt: str
    speaker: Optional[LineSpeaker] = None
    llm_task: Optional[asyncio.Task] = None
    task: Optional[asyncio.Task] = None
    llm_cancelled: bool = False
    booking: Optional[asyncio.Future] = None
    llm_error: Optional[str] = None
    raw: list[str] = field(default_factory=list)
    payload: Optional[dict] = None
    result: Optional[LineResult] = None


class TalkMixin:
    """Reply scheduling, own lines, the family's lines and the home-coming line (mixed into ``VisitRuntime``)."""

    def _init_talk(self) -> None:
        self._own_line_no = 0
        self.own_line_lp: dict[str, int] = {}
        self._line_kinds: dict[str, str] = {}
        self._line: Optional[_LineRun] = None
        self._reply_task: Optional[asyncio.Task] = None
        self.neutral_term = ""
        self._llm_failures = 0
        self._handoff_stamps: dict[str, float] = {}
        self._last_tail_ms = 0
        self._session_closing: Optional[asyncio.Future] = None
        # Accepted family messages not yet transcribed: one future per sentence, completed on persistence; await with a deadline before archival.
        self.family_records: set[asyncio.Future] = set()
        # Number of sentences writing spool: briefly await completion before debrief decides whether anything is worth recording.
        self._spool_appending = 0
        self._spool_idle = asyncio.Event()
        self._spool_idle.set()

    # -- Small helpers --

    def lang_tag(self) -> Optional[str]:
        return self.lang

    def speaker_label(self, speaker: str, lang: Optional[str] = None) -> str:
        from main_routers.visit_router.local_context import speaker_label

        return speaker_label(speaker, lang or self.lang)

    def _mirror_meta(self, kind: str) -> dict:
        from main_logic.mirror_meta import build_mirror_meta

        return build_mirror_meta(source="neko_visit", kind=kind, session_id=self.visit_id,
                                 event={"memory_enabled": False})

    def _journal_buffering(self) -> bool:
        """The upload header is not written yet (not even started, or still writing), or what was recorded
        meanwhile is not all written yet."""
        opening = getattr(self, "_journal_opening", None)
        if not self.journal.is_open:
            # Not yet writing: peer hello arrived before our room-join report and was accepted/activated; buffer these too,
            # then append with the upload header after joining so opening lines and usage are preserved.
            return (opening is None and not self.journal.sealed) or (opening is not None and not opening.done())
        return bool(self._journal_backlog)

    def _journal_failed(self) -> bool:
        """The upload header could not be written: this visit has no upload stream."""
        opening = getattr(self, "_journal_opening", None)
        return (opening is not None and opening.done() and not self.journal.is_open and not self.journal.sealed
                and (opening.cancelled() or opening.exception() is not None))

    def _on_usage(self, delta: dict) -> None:
        if self._journal_buffering():
            # Upload header still pending: buffer usage too, since note_usage records nothing before the journal starts.
            self._journal_backlog.append({"kind": "usage", "d": dict(delta), "ts": self.wall()})
            return
        self.journal.note_usage(delta)

    def _note_journal_anomaly(self) -> None:
        if self._journal_buffering():
            self._journal_backlog.append({"kind": "anomaly", "ts": self.wall()})
            return
        self.journal.note_anomaly()

    def _on_tts_fallback(self) -> None:
        self.spawn(self.status("VISIT_TTS_FALLBACK"))

    # -- Effect execution, fixed order --

    def apply_effects(self, eff: Any) -> None:
        """Execute ``RoomEffects`` in the fixed order of §3.6.3."""
        if eff is None:
            return
        if eff.violation is not None:
            self._note_journal_anomaly()
        if eff.finalize_reason is not None:
            self.request_finalize(eff.finalize_reason)
            return
        if self.finalizing:
            return
        if eff.abort_speaking is not None:
            self.interrupt_line(eff.abort_speaking)
        if eff.cancel_pending_reply:
            self._cancel_reply()
        if eff.wrap_up.action != "none":
            self._send_wrap_up(eff.wrap_up)
        if eff.ui_state == "wrap_up":
            self._set_phase(PHASE_WRAP_UP)
            w = self.room.wrap_up if self.room is not None else None
            self.spawn(self.push(PHASE_WRAP_UP, reason=getattr(w, "reason", None),
                                 initiated_by=getattr(w, "initiated_by", None)))
        if eff.peer_crop is not None:
            self.spawn(self.push("peer_crop", peer_crop=eff.peer_crop))
            if self.side == "host":
                self.spawn(self.send_media())
        if eff.peer_hidden is not None:
            self.spawn(self.push("peer_hidden" if eff.peer_hidden else "peer_visible"))
        if eff.say_goodbye:
            plan = eff.reply if eff.reply is not None and eff.reply.goodbye else None
            self.schedule_reply(plan, goodbye=True)
        elif eff.reply is not None:
            self.schedule_reply(eff.reply)

    def _send_wrap_up(self, decision: Any) -> None:
        room = self.room
        if room is None:
            return
        if decision.action == "done" and self.side == "host":
            # Send done only after the farewell line finishes playing plus tail_ms (section 3.2.6 item 21).
            tail = self._last_tail_ms
            self.spawn(self._send_done_later(decision, tail / 1000.0))
            return
        self._enqueue_wrap_up(decision)

    async def _send_done_later(self, decision: Any, delay: float) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        if not self.finalizing:
            self._enqueue_wrap_up(decision)

    def _enqueue_wrap_up(self, decision: Any) -> None:
        room = self.room
        w = room.wrap_up
        msg = {
            "t": "wrap_up", "v": 1, "lp": room.own_lp, "ph": decision.action,
            "reason": decision.reason or w.reason or "quiet",
            "initiated_by": w.initiated_by or self.side,
        }
        if decision.action == "speaking":
            msg["ln"] = decision.ln
        try:
            self.outbox.send(msg, now=self.clock())
        except ValueError as exc:
            logger.warning("visit %s: wrap_up not queued: %s", self.visit_id[:6], exc)
        self.kick()

    # -- Reply scheduling --

    def _cancel_reply(self) -> None:
        task = self._reply_task
        self._reply_task = None
        if task is not None and not task.done():
            task.cancel()

    def schedule_reply(self, plan: Optional[ReplyPlan], *, goodbye: bool = False) -> None:
        """Speak after ``plan.not_before`` (None: as soon as allowed); replaces a pending reply."""
        if self.finalizing or self.room is None:
            return
        self._cancel_reply()
        self._reply_task = self.spawn(self._reply_runner(plan, goodbye=goodbye))

    def start_opening_line(self) -> None:
        """Both sides open with one line (``rt == ''``) right after activation."""
        self.schedule_reply(None)

    async def _reply_runner(self, plan: Optional[ReplyPlan], *, goodbye: bool) -> None:
        me = asyncio.current_task()
        try:
            if plan is not None:
                delay = plan.not_before - self.clock()
                if delay > 0:
                    await asyncio.sleep(delay)
            while True:
                line = self._line
                if line is not None and line.task is not None and not line.task.done():
                    await asyncio.wait([line.task])
                    continue
                if self.finalizing or self.room is None:
                    return
                now = self.clock()
                ok, why = self.room.may_start_cat_line(
                    now, goodbye=goodbye, outbox_pending_bytes=self.outbox.pending_bytes,
                    plan=None if goodbye else plan,
                )
                if ok:
                    break
                if why == "minute_cap":
                    await asyncio.sleep(max(0.05, self.room.next_allowed_start(now) - now))
                elif why == "busy":
                    await asyncio.sleep(_BUSY_RETRY_S)
                else:
                    return
            if self._reply_task is me:
                # Once speaking starts, this is no longer unsaid inference: pending-reply cancellation leaves it alone; interruption uses abort.
                self._reply_task = None
            await self.speak_line(reply_to=plan.reply_to if plan is not None and not goodbye else None,
                                  goodbye=goodbye)
        except asyncio.CancelledError:
            return

    # -- One dialogue line --

    def _next_ln(self) -> str:
        self._own_line_no += 1
        return f"{side_prefix(self.side)}{self._own_line_no}"

    def _addressee_of(self, reply_to: Optional[LineRef]) -> str:
        if reply_to is None:
            return addressee_code(self.peer_side, "cat")
        kind = self._line_kinds.get(reply_to.line_id, "cat")
        return addressee_code(reply_to.side, kind)

    def _prompt_for(self, reply_to: Optional[LineRef], goodbye: bool) -> str:
        from config.prompts.prompts_visit import (
            build_wrap_up_prompt,
            get_visit_arrival_notice,
            get_visit_your_turn_notice,
        )

        if goodbye:
            reason = self.room.wrap_up.reason if self.room is not None else "quiet"
            peer_goodbye = self.last_peer_goodbye if self.side == "host" else None
            return build_wrap_up_prompt(self.side, reason or "quiet", self.lang, peer_goodbye=peer_goodbye)
        if reply_to is not None:
            return get_visit_your_turn_notice(self.lang)
        return get_visit_arrival_notice(self.side, self.lang)

    async def speak_line(self, *, reply_to: Optional[LineRef], goodbye: bool) -> None:
        """Generate, speak and close one own line; returns once it is committed."""
        room = self.room
        if room is None or self.finalizing:
            return
        now = self.clock()
        ln = self._next_ln()
        lp = room.next_lp()
        ref = LineRef(ln, lp, self.side)
        self.own_line_lp[ln] = lp
        self._line_kinds[ln] = "cat"
        prompt = self._prompt_for(reply_to, goodbye)
        header = LineHeader(ln=ln, lp=lp, ad=self._addressee_of(reply_to),
                            rt=reply_to.line_id if reply_to is not None else "", wu=goodbye, sp="c",
                            lang=(self.lang or None) and str(self.lang)[:16])
        line = _LineRun(ref=ref, header=header, reply_to=reply_to, goodbye=goodbye, prompt=prompt)
        # Send wrap_up{speaking} before the farewell line starts, in both subtitle modes.
        self.apply_effects(room.on_local_line_started(ref, reply_to, goodbye, now))
        if self.finalizing:
            return
        line.speaker = LineSpeaker(
            visit_id=self.visit_id, header=header, family_names=self.family_names,
            neutral_term=self.neutral_term, voice=self.voice, open_stream=self._stream_opener(line),
            router=self.speech_router, clock=self.clock,
            on_piece=lambda piece: self._on_piece(line, piece),
            on_done=lambda result: self._on_line_done(line, result),
            on_cancel_llm=lambda: self._cancel_llm(line),
            on_tts_fallback=self._on_tts_fallback, on_usage=self._on_usage,
        )
        self._line = line
        typing = {"t": "typing", "v": 1, "lp": lp, "sp": "c"}
        if not VISIT_STREAM_DELTAS:
            typing["on"] = True
        self._send_lossy(typing)
        timeout = VISIT_GOODBYE_LLM_TIMEOUT_S if goodbye else VISIT_LLM_TIMEOUT_S
        line.llm_task = asyncio.ensure_future(self._generate(line, timeout))
        line.task = self.spawn(self._run_line(line))
        await asyncio.wait([line.task])

    def _stream_opener(self, line: _LineRun):
        if not self.voice.enabled:
            return None

        def open_stream(on_enqueued):
            return self.host.open_speech_stream(
                metadata=self._mirror_meta("visit_line"), request_id=line.ref.line_id, on_enqueued=on_enqueued,
            )

        return open_stream

    def _send_lossy(self, msg: dict) -> None:
        try:
            self.outbox.send(msg, now=self.clock())
        except ValueError as exc:
            logger.debug("visit %s: %s not queued: %s", self.visit_id[:6], msg.get("t"), exc)
        self.kick()

    def _cancel_llm(self, line: _LineRun) -> None:
        task = line.llm_task
        if task is not None and not task.done():
            line.llm_cancelled = True
            task.cancel()

    async def _generate(self, line: _LineRun, timeout: float) -> None:
        session = self.session
        speaker = line.speaker
        if session is None:
            line.llm_error = "no_session"
            speaker.llm_done()
            return

        def sink(delta: str) -> None:
            line.raw.append(delta)
            speaker.feed(delta)

        try:
            async with session.turn_lock:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout
                # An abandoned previous generation still emits text: wait within this line's deadline. If it will not stop, open no new stream,
                # or its later text leaks into this line through the same sink.
                if not await _await_stray(session, timeout):
                    line.llm_error = "timeout"
                    # Abandoned generation exceeded a full deadline: every later line would wait and time out too, so this visit can no longer speak.
                    self.request_finalize("llm_error")
                else:
                    sort_visit_history(session)
                    before = {id(m) for m in session.history}
                    session.set_sink(sink)
                    try:
                        await _stream_bounded(session, line.prompt, max(0.0, deadline - loop.time()))
                    except asyncio.TimeoutError:
                        line.llm_error = "timeout"
                    except asyncio.CancelledError:
                        if not line.llm_cancelled:
                            raise
                    except Exception as exc:  # noqa: BLE001 - Finalize failed line generation as llm_error.
                        logger.warning("visit %s: line generation failed: %s", self.visit_id[:6],
                                       type(exc).__name__)
                        line.llm_error = "error"
                    finally:
                        session.set_sink(None)
                        output = "".join(line.raw)
                        try:
                            self._on_usage(estimate_turn_usage(session, output))
                        except Exception:  # noqa: BLE001
                            pass
                        # Remove this round's appended question/reply: keep only spoken lines in history, recorded from released text when the line closes.
                        _drop_new_messages(session, before)
        except asyncio.CancelledError:
            if not line.llm_cancelled:
                speaker.llm_done()
                raise
        if line.goodbye and not line.raw and not speaker.done:
            # Farewell generated nothing: use the fixed sentence so finalization cannot stall.
            from config.prompts.prompts_visit import get_visit_goodbye_fallback

            speaker.feed(get_visit_goodbye_fallback(self.side, self.lang))
        speaker.llm_done()

    async def _run_line(self, line: _LineRun) -> None:
        try:
            await drive(line.speaker, clock=self.clock)
        finally:
            if line.llm_task is not None and not line.llm_task.done():
                # text{final} is queued: bound waiting for LLM termination so an uncooperative task cannot block transcription.
                await asyncio.wait([line.llm_task], timeout=_LLM_SETTLE_S)
                if not line.llm_task.done():
                    line.llm_task.cancel()
            await self._finish_line(line)
            if self._line is line:
                self._line = None

    def _on_piece(self, line: _LineRun, piece: ReleasedPiece) -> None:
        """A subtitle piece is released: ``line_delta`` (streaming mode) + local ``visit_line_delta``."""
        h = line.header
        if VISIT_STREAM_DELTAS:
            msg = {"t": "line_delta", "v": 1, "ln": h.ln, "i": 0, "lp": h.lp, "txt": piece.text,
                   "sp": h.sp, "ad": h.ad, "rt": h.rt, "wu": h.wu}
            try:
                self.outbox.send(msg, now=self.clock(), final_piece=piece.last)
            except ValueError as exc:
                logger.warning("visit %s: line_delta not queued: %s", self.visit_id[:6], exc)
            self.kick()
        ad_side, ad_kind = decode_addressee(h.ad)
        # Local chunks / full lines / speech stops use the same ordered display queue as the peer, preventing full lines preceding stale chunks.
        self._post_display({
            "type": "visit_line_delta", "visit_id": self.visit_id, "line_id": h.ln, "i": piece.index,
            "lp": h.lp, "text": defang_markdown_media(piece.text),
            "speaker": self.speaker_payload(self.side, "cat"), "addressee": {"side": ad_side, "kind": ad_kind},
            "goodbye": h.wu, "ts": self.wall(), "paced": piece.paced,
        }, droppable=True)

    def _final_payload(self, h: Any, text: str, *, truncated: bool, reason: Optional[str], tail_ms: int) -> dict:
        payload = {
            "t": "text", "v": 1, "ln": h.ln, "lp": h.lp, "sp": "c", "ad": h.ad, "rt": h.rt, "wu": h.wu,
            "final": True, "txt": clamp_text_utf8(text), "truncated": truncated, "i_done": 0,
            "tail_ms": max(0, min(int(tail_ms), 12000)),
        }
        if truncated and reason:
            payload["trunc_reason"] = reason
        if h.lang:
            payload["lang"] = h.lang
        return fit_text_to_wire(payload, visit_id=self.visit_id)

    def _interrupted_final_bytes(self) -> int:
        """Bytes of the ``text{final}`` the own line in progress would queue if a human cut it now."""
        line = self._line
        if line is None or line.speaker is None or line.speaker.done:
            return 0
        payload = self._final_payload(line.header, line.speaker.released_text, truncated=True,
                                      reason="human_interrupt", tail_ms=0)
        return self.outbox.encoded_size(payload)[1]

    def _on_line_done(self, line: _LineRun, result: LineResult) -> None:
        """The line ended: queue its ``text{final}`` now (before anything queued later) and book it."""
        line.result = result
        h = line.header
        truncated = bool(result.truncated)
        reason = result.trunc_reason
        if line.llm_error is not None and not truncated:
            # Generation failed midstream: finalize the released prefix normally, marked truncated rather than a completed sentence.
            truncated, reason = True, "llm_error"
        payload = self._final_payload(h, result.text, truncated=truncated, reason=reason, tail_ms=result.tail_ms)
        try:
            self.outbox.send(payload, now=self.clock())
        except ValueError as exc:
            logger.warning("visit %s: text{final} not queued: %s", self.visit_id[:6], exc)
        line.payload = payload
        self._last_tail_ms = int(payload.get("tail_ms") or 0)
        self.last_text_at = self.clock()
        if not VISIT_STREAM_DELTAS:
            self._send_lossy({"t": "typing", "v": 1, "lp": h.lp, "sp": "c", "on": False})
        self.kick()
        ad_side, ad_kind = decode_addressee(h.ad)
        self._post_display(self.visit_line_payload(
            ln=h.ln, lp=h.lp, side=self.side, kind="cat", ad_side=ad_side, ad_kind=ad_kind, reply_to=h.rt,
            goodbye=h.wu, text=payload["txt"], truncated=payload["truncated"],
            i_done=self.outbox.line_i_done(h.ln), trunc_reason=payload.get("trunc_reason"),
        ))
        if self.room is not None:
            self.apply_effects(self.room.on_local_line_done(line.ref, bool(payload["truncated"]), self.clock()))

    async def book_line(self, line: _LineRun) -> None:
        """Record a closed own line in the spool / upload once (``_finish_line`` or shutdown, whichever first).

        One recording task per line, shared by both callers and shielded: a
        caller cancelled at its deadline (shutdown) does not interrupt the
        write, and the other caller waits for that same write.
        """
        payload = line.payload
        if payload is None:
            return
        if line.booking is None:
            h = line.header
            line.booking = asyncio.ensure_future(self.record_line(
                "own_cat", side=self.side, lp=h.lp, ln=h.ln, text=payload["txt"], truncated=payload["truncated"]))
            line.booking.add_done_callback(lambda t: t.cancelled() or t.exception())
            # Shielded from line-task cancellation while spool writes continue. Register at module level so shutdown still finds it after deregistration.
            self._keep_background(line.booking)
        await asyncio.shield(line.booking)

    async def _finish_line(self, line: _LineRun) -> None:
        payload = line.payload
        if payload is None:
            return
        await self.book_line(line)
        await self._add_own_cat_history(line, payload)
        if line.llm_error is not None:
            self._llm_failures += 1
            if self._llm_failures >= VISIT_LLM_ERROR_FINALIZE_COUNT:
                self.request_finalize("llm_error")
        else:
            self._llm_failures = 0

    def interrupt_line(self, reason: str) -> None:
        """Stop the line being spoken now: ``line_abort`` first, then its truncated ``text{final}``."""
        line = self._line
        if line is None or line.speaker is None or line.speaker.done:
            return
        h = line.header
        if reason in LINE_ABORT_REASONS:
            self._send_lossy({"t": "line_abort", "v": 1, "ln": h.ln, "lp": h.lp,
                              "i_done": self.outbox.line_i_done(h.ln), "reason": reason})
            self._post_display({
                "type": "visit_line_abort", "visit_id": self.visit_id, "line_id": h.ln,
                "i_done": self.outbox.line_i_done(h.ln), "reason": reason, "ts": self.wall(),
            })
        line.speaker.interrupt(reason)

    async def close_current_line(self, reason: str) -> None:
        """Finalize step ①: cancel the unspoken reply, cut the line in progress and wait for its commit."""
        self._cancel_reply()
        line = self._line
        if line is None:
            return
        if line.speaker is not None and not line.speaker.done:
            line.speaker.interrupt(reason)
        if line.task is not None:
            await asyncio.wait([line.task], timeout=_LINE_CLOSE_WAIT_S)

    # -- Accounting and history --

    async def record_line(self, speaker: str, *, side: str, lp: int, ln: str, text: str, truncated: bool,
                          on_journaled: Optional[Callable[[], None]] = None) -> None:
        """Spool (memory on) and upload record of one final line.

        ``on_journaled`` is called once the upload record is in (before the
        spool write): that is all the transcript seal needs.
        """
        ts = self.wall()
        clean = clamp_text_utf8(text)
        self._ln_by_key[(lp, side)] = ln
        # Upload journal first: its in-memory entry counts immediately, so slow spool writes cannot lose this line when archival arrives first at shutdown.
        if self._journal_buffering():
            # Upload header still pending on slow disk: buffer and backfill in order afterward so opening lines remain in the transcript;
            # queue newly arriving lines behind any remaining backlog.
            self._journal_backlog.append(dict(kind="line", lp=lp, side=side, speaker=speaker, ts=ts, text=clean,
                                              truncated=bool(truncated)))
            if self.journal.is_open:
                self._flush_journal_backlog()
        elif self.journal.is_open:
            try:
                await self.journal.append_line(lp=lp, side=side, speaker=speaker, ts=ts, text=clean,
                                               truncated=bool(truncated))
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: upload record failed: %s", self.visit_id[:6], type(exc).__name__)
        elif self._journal_failed():
            # Upload header failed, leaving no journal: at least record in-memory transcript for /state replay and debrief.
            try:
                self.journal.remember_line(lp=lp, side=side, speaker=speaker, ts=ts, text=clean,
                                           truncated=bool(truncated))
            except Exception as exc:  # noqa: BLE001
                logger.warning("visit %s: line not kept: %s", self.visit_id[:6], type(exc).__name__)
        if on_journaled is not None:
            on_journaled()
        spool = self.spool
        if spool is not None and self.memory_enabled and spool.is_open:
            self._spool_appending += 1
            self._spool_idle.clear()
            try:
                await spool.append({"lp": lp, "side": side, "ts": ts, "from": speaker, "text": clean,
                                    "ln": ln, "truncated": bool(truncated)})
                self.spool_lines += 1
            except Exception as exc:  # noqa: BLE001 - Cannot write spool: exclude this sentence from visit memory.
                logger.warning("visit %s: spool append failed: %s", self.visit_id[:6], type(exc).__name__)
            finally:
                self._spool_appending -= 1
                if not self._spool_appending:
                    self._spool_idle.set()

    def _history_add(self, ln: str, message: Any, key: tuple[int, int]) -> None:
        """Insert a history message at its sorted place, inside the session turn lock (never blocks the caller)."""
        session = self.session
        if session is None:
            return

        async def add() -> None:
            async with session.turn_lock:
                append_visit_message(session, message, key)
                trim_visit_history(session)

        self.spawn(add())

    def add_peer_history(self, ln: str, lp: int, speaker: str, text: str, *, truncated: bool,
                         trunc_reason: Optional[str]) -> None:
        from config.prompts.prompts_visit import get_visit_mark_interrupted, get_visit_speaker_header
        from utils.llm_client import HumanMessage

        self._line_kinds[ln] = "human" if speaker.endswith("_human") else "cat"
        body = wrap_nonce_envelope(clamp_peer_line(text), nonce=make_envelope_nonce())
        content = f"{get_visit_speaker_header(speaker, self.lang)}\n{body}"
        if truncated and (trunc_reason is None or trunc_reason in SILENCING_TRUNC_REASONS):
            content += "\n" + get_visit_mark_interrupted(self.lang)
        self._history_add(ln, HumanMessage(content=content), sort_key(lp, self.peer_side))

    def _add_own_human_history(self, ln: str, lp: int, text: str) -> None:
        from config.prompts.prompts_visit import get_visit_speaker_header
        from utils.llm_client import HumanMessage

        self._line_kinds[ln] = "human"
        content = f"{get_visit_speaker_header('own_human', self.lang)}\n{text}"
        self._history_add(ln, HumanMessage(content=content), sort_key(lp, self.side))

    async def _add_own_cat_history(self, line: _LineRun, payload: dict) -> None:
        from config.prompts.prompts_visit import get_visit_mark_interrupted
        from utils.llm_client import AIMessage

        session = self.session
        if session is None:
            return
        text = payload["txt"]
        reason = payload.get("trunc_reason")
        if payload.get("truncated") and (reason is None or reason in SILENCING_TRUNC_REASONS):
            # Record spoken text only: the released prefix plus an interrupted marker.
            text = f"{text}{get_visit_mark_interrupted(self.lang)}"
        if not text.strip():
            return
        # Like peer-line history, use another task waiting for the lock so an uncooperative LLM holding it cannot block this line's finalization.
        self._history_add(line.header.ln, AIMessage(content=text), sort_key(line.header.lp, self.side))

    # -- Family typing (host) --

    async def on_stream_message(self, message: dict) -> bool:
        """``stream_data`` while the visit owns the input (§4.5)."""
        input_type = message.get("input_type")
        request_id = message.get("request_id") if isinstance(message.get("request_id"), str) else None
        if input_type == "audio":
            await self._voice_unavailable()
            return True
        if input_type in ("user_image", "avatar_drop_image"):
            # Visit input sends text only, without attachments; these images came from ordinary submissions (unrestored reload attachments / avatar drops).
            # Swallow without forwarding, but reject with request_id so the page marks its ordinary bubble failed.
            await self.status("VISIT_INPUT_REFUSED_SOURCE", request_id=request_id)
            return True
        if input_type != "text":
            # Always swallow screenshots / camera frames.
            return True
        claimed_visit = message.get("visit_id")
        if isinstance(claimed_visit, str) and claimed_visit and claimed_visit != self.visit_id:
            # Page still believes it is in another visit after missed reconnect switch: never send that guest's message to this guest.
            await self.status("VISIT_INPUT_REFUSED_WRAPUP", request_id=request_id)
            return True
        if self.side == "guest":
            await self.status("VISIT_INPUT_REFUSED_AWAY", request_id=request_id)
            return True
        if self.finalizing:
            # Finalizing while takeover remains held: ordinary chat output is suppressed; this line must not leak through.
            await self.status("VISIT_INPUT_REFUSED_WRAPUP", request_id=request_id)
            return True
        room = self.room
        if room is None or not self.activated or not self.ready_exchanged or self.phase in WAITING_PHASES:
            # Lines sent before ready is enqueued get earlier sequence numbers and are accepted then discarded by the inactive peer.
            await self.status("VISIT_INPUT_REFUSED_NOT_READY", request_id=request_id)
            return True
        if room.phase == "wrap_up":
            await self.status("VISIT_INPUT_REFUSED_WRAPUP", request_id=request_id)
            return True
        if not str(message.get("source") or "").startswith("neko_visit:"):
            # Only visit-input submissions are family dialogue. Avatar file drops, history drops, and ordinary submissions before reload
            # restores visit state are not addressed to the guest: reject with request_id for text restoration, never forward.
            await self.status("VISIT_INPUT_REFUSED_SOURCE", request_id=request_id)
            return True
        if room.phase != "active":
            return False
        now = self.clock()
        if not room.can_accept_local_line(now):
            await self.status("VISIT_INPUT_REFUSED_RATE", request_id=request_id)
            return True
        text = self._clean_human_text(message.get("data"))
        if not text.strip():
            return True
        to_own = message.get("source") == "neko_visit:own_cat"
        ad = addressee_code(self.side, "cat") if to_own else addressee_code(self.peer_side, "cat")
        try:
            accepted = await self._send_human_line(text, ad, now, request_id=request_id, to_own=to_own)
        except Exception as exc:  # noqa: BLE001 - Any failure releases the reservation and leaves text in the input box.
            logger.warning("visit %s: family line not sent: %s", self.visit_id[:6], type(exc).__name__)
            accepted = None
        if accepted is None:
            await self.status("VISIT_E_BUSY", request_id=request_id)
        return True

    def _clean_human_text(self, raw: Any) -> str:
        text = str(raw or "")
        if self.family_names:
            text = redact_outbound(text, family_names=self.family_names, replacement=self.neutral_term)
        return clamp_text_utf8(sanitize_relay_text(text, max_tokens=VISIT_HUMAN_LINE_MAX_TOKENS)).strip()

    async def _send_human_line(self, text: str, ad: str, now: float, *, request_id: Any = None,
                               to_own: bool = False) -> Optional[LineRef]:
        room = self.room
        ln = self._next_ln()
        lp = room.next_lp()
        payload = fit_text_to_wire({
            "t": "text", "v": 1, "ln": ln, "lp": lp, "sp": "h", "ad": ad, "rt": "", "wu": False,
            "final": True, "txt": text, "truncated": False, "i_done": 0,
        }, visit_id=self.visit_id)
        _pieces, nbytes = self.outbox.encoded_size(payload)
        # Human lines immediately interrupt local speech, queuing its text{final} first; reserve for both so it cannot consume this line's quota.
        reservation = self.outbox.reserve(nbytes + self._interrupted_final_bytes())
        if reservation is None:
            return None
        ref = LineRef(ln, lp, self.side)
        try:
            # Complete acceptance and rate-limit accounting synchronously so the next input / phase change sees this line.
            self.apply_effects(room.on_local_human_line(ref, now))
            self.own_line_lp[ln] = lp
        except BaseException:
            reservation.release()
            raise

        recorded: asyncio.Future = asyncio.get_running_loop().create_future()
        self.family_records.add(recorded)
        recorded.add_done_callback(self.family_records.discard)

        def journaled(*_args: Any) -> None:
            if not recorded.done():
                recorded.set_result(None)

        def shown() -> None:
            ad_side, ad_kind = decode_addressee(ad)
            self._post_display(self.visit_line_payload(
                ln=ln, lp=lp, side=self.side, kind="human", ad_side=ad_side, ad_kind=ad_kind, reply_to="",
                goodbye=False, text=payload["txt"], truncated=bool(payload["truncated"]),
                trunc_reason=payload.get("trunc_reason"), request_id=request_id,
            ))

        def reply() -> None:
            if to_own and not self.finalizing and self.room is not None and self.room.phase == "active":
                lo, hi = self.deps.reply_gap_s
                self.schedule_reply(ReplyPlan(reply_to=ref, not_before=self.clock() + self.deps.rng.uniform(lo, hi)))

        async def commit() -> None:
            try:
                try:
                    # Persist before sending: at worst local history contains an unsent line, which Servers marks one-sided.
                    # Upload-journal recording counts as persistence; archival needs only that, without waiting for spool disk writes.
                    await self.record_line("own_human", side=self.side, lp=lp, ln=ln, text=payload["txt"],
                                           truncated=bool(payload["truncated"]), on_journaled=journaled)
                finally:
                    journaled()
                if self.outbox.closed:
                    # Persistence outlasted finalization: channel closed and pump stopped, so this sentence cannot be sent (already transcribed).
                    logger.warning("visit %s: family line recorded after the outbox closed; not sent",
                                   self.visit_id[:6])
                    return
                self.outbox.send(payload, now=self.clock(), reservation=reservation)
            finally:
                reservation.release()
            # Once sent, local history, display, mirror, and replies must follow even if the caller is cancelled. Log individual failures only:
            # propagating them returns VISIT_E_BUSY, preserving input that would reach the peer twice on retry.
            self.last_text_at = self.clock()
            self.kick()
            self._after_send_step("history", lambda: self._add_own_human_history(ln, lp, payload["txt"]))
            self._after_send_step("display", shown)
            try:
                # Put irreversible work last: sync_message_queue entries cannot be withdrawn.
                # Mirror the wire-length-trimmed sentence, matching peer delivery, transcript, display, and history.
                await self.host.mirror_user_input(payload["txt"], metadata=self._mirror_meta("visit_human"),
                                                  request_id=request_id)
            except Exception as exc:  # noqa: BLE001 - Already sent: diagnostics only.
                logger.warning("visit %s: mirror_user_input failed: %s", self.visit_id[:6], type(exc).__name__)
            self._after_send_step("reply", reply)

        # After acceptance changes the room and possibly transcript, this sequence must finish despite caller cancellation (shutdown etc.): persist, enqueue,
        # record history, display, and schedule replies. Retain reservation through enqueue; channel-closing leave queues after it. Bound persistence wait before archival.
        committing = asyncio.ensure_future(commit())
        # Still running spool writes/mirroring after handler cancellation: module-level registration lets shutdown find it after visit deregistration; remove on completion.
        self._keep_background(committing)
        # Cancelled before execution (loop shutdown etc.): still complete the persistence future and idempotently release reservation so archival does not wait needlessly.
        committing.add_done_callback(journaled)
        committing.add_done_callback(lambda _t: reservation.release())
        committing.add_done_callback(lambda t: t.cancelled() or t.exception())
        try:
            await asyncio.shield(committing)
        except asyncio.CancelledError:
            # Caller has left, so log later failures here because nobody else will.
            committing.add_done_callback(self._log_orphan_commit)
            raise
        return ref

    def _after_send_step(self, label: str, step: Callable[[], None]) -> None:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - Already sent: diagnostics only.
            logger.warning("visit %s: family line %s failed after send: %s",
                           self.visit_id[:6], label, type(exc).__name__)

    async def settle_spool_appends(self, timeout: float) -> None:
        """Wait (at most ``timeout``) until no line is being appended to the spool."""
        if self._spool_appending and timeout > 0:
            try:
                await asyncio.wait_for(self._spool_idle.wait(), timeout)
            except asyncio.TimeoutError:
                # Deadline reached before writes finish: stop waiting and decide from currently persisted sentence counts, excluding the unfinished line.
                logger.info("visit %s: spool appends still pending; deciding without them", self.visit_id[:6])

    async def settle_family_records(self, timeout: float) -> None:
        """Wait (at most ``timeout``) until every admitted family line has been recorded."""
        pending = [f for f in self.family_records if not f.done()]
        if pending and timeout > 0:
            await asyncio.wait(pending, timeout=timeout)

    def _log_orphan_commit(self, task: "asyncio.Future[None]") -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.warning("visit %s: family line not sent after its handler left: %s",
                           self.visit_id[:6], type(task.exception()).__name__)

    # -- Homecoming ritual line --

    async def say_ritual(self, reason: str, *, input_stamp: float) -> None:
        """The home-coming line: generated for natural ends, a fixed line otherwise (never an LLM then)."""
        from config.prompts.prompts_visit import get_visit_back_home_notice, get_visit_fixed_line

        handoff = self.handoff
        natural = reason in NATURAL_REASONS or (reason == "peer_left" and self.peer_reason in ("home", "wrapup"))
        text: Optional[str] = None
        if natural:
            # Omit visit history: this line addresses family and must never have an opportunity to repeat the peer's original words.
            text = await self.one_shot_turn(get_visit_back_home_notice(self.side, self.lang),
                                            timeout=VISIT_CEREMONY_TIMEOUT_S, without_history=True)
        if not text:
            text = get_visit_fixed_line("ended" if natural else fixed_line_kind(reason), self.lang)
        if self.host.last_user_input() > input_stamp:
            # Family spoke first: abandon the ritual line, without speech or display.
            if handoff is not None:
                handoff.skip("ritual")
            return
        await self.speak_home_segment("ritual", text, kind="visit_ritual", silent=reason == "goodbye")

    async def one_shot_turn(self, prompt: str, *, timeout: float, without_history: bool = False) -> Optional[str]:
        """One extra turn of the isolated session (ritual / debrief); None on failure.

        ``without_history``: the turn sees only the system instructions and
        ``prompt`` (the debrief brings its own bounded record of the visit).
        """
        from utils.llm_client import SystemMessage

        session = self.session
        if session is None:
            return None
        chunks: list[str] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            # Lock acquisition counts toward the deadline; an uncooperative finalizing turn may still hold it.
            await asyncio.wait_for(session.turn_lock.acquire(), timeout)
        except asyncio.TimeoutError:
            logger.info("visit %s: home-coming turn skipped: session still busy", self.visit_id[:6])
            return None
        try:
            if not await _await_stray(session, max(0.0, deadline - loop.time())):
                logger.info("visit %s: home-coming turn skipped: an abandoned turn is still running",
                            self.visit_id[:6])
                return None
            saved = list(session.history)
            before = {id(m) for m in saved}
            if without_history:
                session.history[:] = [m for m in saved if isinstance(m, SystemMessage)]
            session.set_sink(chunks.append)
            try:
                # Share one deadline with lock acquisition, keeping the whole operation within timeout even for uncooperative clients.
                await _stream_bounded(session, prompt, max(0.0, deadline - loop.time()))
            except Exception as exc:  # noqa: BLE001 - Timeout / failure: caller uses the fixed line.
                logger.info("visit %s: home-coming turn failed: %s", self.visit_id[:6], type(exc).__name__)
                return None
            finally:
                session.set_sink(None)
                if without_history:
                    session.history[:] = saved
                    session.forget_untracked()
                else:
                    _drop_new_messages(session, before)
        finally:
            session.turn_lock.release()
        text = strip_emotion_tags("".join(chunks)).strip()
        return text or None

    async def speak_home_segment(self, name: str, text: str, *, kind: str, silent: bool = False) -> None:
        """Say one home-coming segment; its speech id is registered for the inbox handoff first.

        ``silent``: show it only (the family said goodbye: the home stays quiet).
        """
        handoff = self.handoff
        est = estimate_speech_ms(text)
        self._handoff_stamps[name] = self.host.last_user_input()
        request_id = f"visit-{name}:{self.visit_id}"
        meta = self._mirror_meta(kind)
        stream = None

        def on_failed() -> None:
            # Stream stopped before its end marker (TTS failed / enqueue error / turn takeover): no playback progress will exist,
            # so handback stops waiting and no further voice streams open this visit, as with finish failure.
            self.voice.fallen_back = True
            if handoff is not None:
                handoff.skip(name)

        # Once this visit uses estimated timing after TTS failure, open no further voice streams.
        if self.voice.tts_on and not silent:
            try:
                stream = self.host.open_speech_stream(metadata=meta, request_id=request_id,
                                                      on_enqueued=lambda _n: None, on_failed=on_failed)
            except Exception as exc:  # noqa: BLE001 - TTS unavailable: display only.
                logger.info("visit %s: home-coming speech unavailable: %s", self.visit_id[:6], type(exc).__name__)
            if stream is None:
                # Voice-stream startup failed: display this segment only and stop retrying for this visit, as when workers reject it.
                self.voice.fallen_back = True
        if stream is not None and handoff is not None:
            handoff.attach_speech(name, stream.speech_id)
        if stream is not None:
            pushed = stream.push(text)
            finished = stream.finish() if pushed else False
            if pushed and finished is True:
                if handoff is not None:
                    handoff.mark_queued(name, est)
            else:
                # TTS worker closed and cannot accept: no progress will arrive; handback stops waiting and no later voice streams open this visit.
                self.voice.fallen_back = True
                try:
                    stream.abort()
                except Exception:  # noqa: BLE001
                    pass
                if handoff is not None:
                    handoff.skip(name)
        elif handoff is not None:
            if silent:
                handoff.skip(name)
            else:
                # Display only (voice disabled, fallback active, or startup failed): handback waits estimated display duration, never assumes playback completed.
                handoff.mark_queued(name, est)
        try:
            await self.host.mirror_assistant_output(text, metadata=meta, request_id=request_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("visit %s: home-coming line not shown: %s", self.visit_id[:6], type(exc).__name__)

    async def wait_family_turn(self) -> None:
        """Let an ordinary turn the family started finish before showing more (bounded).

        The family's input may not have started a reply yet: wait briefly
        for it to start, then for it to end.
        """
        await self.host.wait_turn_idle(_HOME_TURN_WAIT_S, start_window=_HOME_TURN_START_S)

    def _close_session_in_background(self) -> asyncio.Future:
        """Start closing the isolated session once (later callers wait for the same close)."""
        from main_routers.visit_router.session_pool import close_visit_session

        closing = self._session_closing
        if closing is None:
            stray = getattr(self.session, "stray", None)
            if stray is not None and not stray.done():
                # Abandoned generation ignores cancellation and may outlive session close; module-level registration lets stop_all collect it on shutdown.
                self._keep_background(stray)
            closing = self._session_closing = asyncio.ensure_future(close_visit_session(self.session))
            closing.add_done_callback(lambda t: t.cancelled() or t.exception())  # Failure merely means one missed close; retrieve the exception.
            self._keep_background(closing)
        return closing

    async def close_session(self, timeout: float) -> None:
        """Close the isolated session's client, waiting at most ``timeout``.

        Not ``wait_for``: that waits for the cancelled close to actually end,
        and a client whose own cancellation drain hangs would hold teardown
        forever. Past ``timeout`` the close is left running in the background
        (``_keep_background``) so it can still release its HTTP connections.
        """
        if self.session is None:
            return
        # Close once: if normal finalization stalls then shutdown retries, await the same close task rather than closing one client concurrently.
        closing = self._close_session_in_background()
        # Register close tasks in the background immediately; caller cancellation / timeout cannot prevent completion, and stop_all collects them.
        await asyncio.wait([closing], timeout=timeout)
        if not closing.done():
            logger.warning("visit %s: isolated session still closing; continuing", self.visit_id[:6])


async def _stream_bounded(session: Any, prompt: str, timeout: float) -> None:
    """``stream_text`` under a deadline that never waits without bound for a stubborn cancellation.

    ``asyncio.wait_for`` waits for the cancelled coroutine to actually stop: a
    client that swallows the cancellation would hold the line (and the turn
    lock) forever. Past the deadline, or when the caller is cancelled, the
    stream gets ``_LLM_SETTLE_S`` to stop and is then left behind (its sink is
    already detached on the deadline path). Raises ``TimeoutError`` past the
    deadline, else whatever the stream raised.
    """
    gen = asyncio.ensure_future(session.client.stream_text(prompt))
    gen.add_done_callback(lambda t: t.cancelled() or t.exception())  # Retrieve exceptions when abandoned generation finishes.
    try:
        done, _pending = await asyncio.wait([gen], timeout=timeout)
    except asyncio.CancelledError:
        session.set_sink(None)
        gen.cancel()
        await _settle_or_abandon(session, gen)
        raise
    if not done:
        session.set_sink(None)  # Text emitted after the deadline cannot enter this line.
        gen.cancel()
        await _settle_or_abandon(session, gen)
        raise asyncio.TimeoutError
    gen.result()


async def _settle_or_abandon(session: Any, gen: asyncio.Future) -> None:
    """Give a cancelled stream ``_LLM_SETTLE_S`` to stop; one that does not is left on ``session.stray``.

    The next turn on this session waits for it first (:func:`_await_stray`):
    the delta sink is looked up per delta, so a stream still running would
    feed the next line, and two streams would share one client. When it
    finally stops, what it appended to the history is taken out again.
    """
    # Register before waiting: another caller cancellation during _LLM_SETTLE_S still leaves it tracked on the session.
    session.stray = gen
    gen.add_done_callback(lambda _t: _purge_untracked(session))
    await asyncio.wait([gen], timeout=_LLM_SETTLE_S)
    if gen.done() and session.stray is gen:
        session.stray = None


async def _await_stray(session: Any, timeout: float) -> bool:
    """Wait (bounded) for a turn abandoned earlier on ``session``; False if it is still running."""
    stray = getattr(session, "stray", None)
    if stray is None:
        return True
    if not stray.done():
        await asyncio.wait([stray], timeout=timeout)
        if not stray.done():
            return False
    session.stray = None
    _purge_untracked(session)
    return True


def _purge_untracked(session: Any) -> None:
    """Drop history messages no line put there (a stray turn's prompt / reply); system messages stay.

    Every real line is added with its sort key (``append_visit_message``);
    only a turn's own prompt and reply are untagged, and no turn is running
    while a stray one is (each turn waits for it first).
    """
    from utils.llm_client import SystemMessage

    history = session.history
    history[:] = [m for m in history if isinstance(m, SystemMessage) or session.key_of(m) is not None]
    session.forget_untracked()


def _drop_new_messages(session: Any, before: set[int]) -> None:
    """Remove what a turn appended to the history (its prompt and its reply)."""
    history = session.history
    history[:] = [m for m in history if id(m) in before]
    session.forget_untracked()
