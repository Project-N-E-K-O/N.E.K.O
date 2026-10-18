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

"""Home-coming debrief (OD-16 v4, design §3.7.4 / §4.6): summary, chips, preview and the two-step diary write.

After the visit is finalized the cat tells the family, in two or three
sentences, who the visit was with and what they talked about:

* input: the in-memory transcript (the upload copy, both sides, ``(lp,
  side_rank)`` order), whatever ``visitMemoryEnabled`` says -- it is only
  said aloud, never stored; at most ``VISIT_DEBRIEF_INPUT_MAX_TOKENS``
  taken whole-line from the newest line backwards, the peer's lines inside
  ``VISIT_PEER_LINES_BLOCK``;
* one isolated-session turn (``VISIT_DEBRIEF_INSTRUCTION``, within
  ``VISIT_CEREMONY_TIMEOUT_S``) → ``strip_emotion_tags`` → ``redact_outbound``
  → token cap → ``assert_no_peer_ngram``; any failure says
  ``VISIT_DEBRIEF_FALLBACK``;
* spoken as a mirror speech registered for the inbox handoff (text only
  with voice off); when the family started talking after the takeover was
  released, it is only shown, after that ordinary turn ended;
* with visit memory on and something to digest: the chips ("keep as diary"
  / "don't keep") and ``state.json{debrief_choice:'ask_later',
  debrief_chip_pending:true}``. Nothing is written to private memory here.

:func:`render_chips` is also the ``render_chips`` callback of PR-08 startup
recovery (PR-09b wires it).

"Keep as diary" (PR-14, ``POST /api/visit/debrief/choice``, :data:`router`):
``diary`` only generates a preview (``main_logic.visit.debrief_writers``)
and shows it verbatim; ``confirm`` writes exactly that preview in two steps
(``visit_facts`` then the keyed ``/cache``) through :func:`run_commit`, the
single place that writes, shared with the backoff timer
(:func:`schedule_commit_retry`) and startup recovery
(:func:`resume_diary_commit`); ``forget`` writes nothing; ``abandon`` gives
up a permanently failed write. Every choice of one visit is serialized
under one per-visit lock. Labels come from ``static/locales`` (``visit.debrief.*``)
in the UI language. A freshly bound display gets whatever block the visit's
current choice owes (:func:`owed_block`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import weakref
from collections.abc import Mapping
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.visit_settings import (
    VISIT_CEREMONY_TIMEOUT_S,
    VISIT_DEBRIEF_INPUT_MAX_TOKENS,
    VISIT_DEBRIEF_MAX_TOKENS,
    VISIT_PEER_NGRAM_N,
)
from main_logic.visit.sanitize import PeerNgramHit, assert_no_peer_ngram, redact_outbound, strip_emotion_tags
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

_STATE_WRITE_MAX_S = 5.0
"""Bound of the ``debrief_chip_pending`` state write (a stalled disk must not hold the exit flow)."""

DEBRIEF_ACTION = "visit_debrief_choice"
"""``react-chat-window:action`` of the chip buttons (handled by the page, PR-12 / PR-14)."""


def chips_request_id(visit_id: str) -> str:
    return f"visit-debrief:{visit_id}"


def t(key: str, lang: Optional[str]) -> str:
    """``visit.debrief.<key>`` from ``static/locales`` in ``lang`` (the page renders it verbatim)."""
    from main_logic.core._shared import get_locale_text

    dotted = f"visit.debrief.{key}"
    return get_locale_text(lang, dotted, dotted)


def chip_blocks(visit_id: str, lang: Optional[str]) -> list[dict]:
    """The chips message: one line of text and the two buttons (``diary`` / ``forget``)."""
    return [
        {"type": "text", "text": t("question", lang)},
        {"type": "buttons", "buttons": [
            {"id": "diary", "label": t("choiceDiary", lang), "action": DEBRIEF_ACTION,
             "payload": {"visit_id": visit_id, "choice": "diary"}},
            {"id": "forget", "label": t("choiceForget", lang), "action": DEBRIEF_ACTION, "variant": "danger",
             "payload": {"visit_id": visit_id, "choice": "forget"}},
        ]},
    ]


async def show_chips(host: Any, visit_id: str, *, own_char: str, lang: Optional[str]) -> bool:
    """Render the chips through ``host`` (a ``VisitHost``); False when no display took them."""
    try:
        return bool(await host.render_chat_blocks(chip_blocks(visit_id, lang),
                                                  request_id=chips_request_id(visit_id),
                                                  source_name=own_char))
    except Exception as exc:  # noqa: BLE001 - Missing page: retain state.json markers for next-bind replay.
        logger.info("visit %s: debrief chips not shown: %s", visit_id[:6], type(exc).__name__)
        return False


async def render_chips(visit_id: str, *, own_char: str, status: Optional[str] = None) -> bool:
    """PR-08 recovery callback: show the block ``visit_id`` currently owes on ``own_char``'s display now.

    ``status='interrupted'`` (a crashed visit) also tells the page the last
    visit was interrupted, but only while the owed block is the chips
    (``CHIP_CHOICES``, ``generating:diary`` included): the preview, the
    committing preview and the failure block never carry it, the same rule
    as the bind replay. The pending flag in ``state.json`` stays until the
    user decides.
    """
    from main_routers.visit_router.host_port import ManagerHost
    from main_routers.visit_router.local_context import prompt_lang

    from main_routers.visit_router.accounts import own_visit_uid

    host = ManagerHost.for_character(own_char)
    if host is None or not host.display_bound():
        # Page unbound, as startup backfill often precedes connection: replay chips/interruption notices together after visit_bind, never partially.
        return False
    owner = await _visit_owner(visit_id)
    if owner is None or owner != await own_visit_uid():
        # Startup backfill callbacks cover all accounts; suppress visits outside the logged-in account until that account returns and binds.
        return False
    return await show_owed(host, visit_id, own_char=own_char, lang=prompt_lang(),
                           interrupted=status == "interrupted")


async def show_owed(host: Any, visit_id: str, *, own_char: str, lang: Optional[str],
                    interrupted: bool = False) -> bool:
    """Render the block ``visit_id`` currently owes (chips, preview or failure block) and its state frame.

    Under the per-visit lock, from the current ``state.json``: a choice made
    meanwhile on another page is never overtaken by an older block.
    """
    async with _visit_lock(visit_id):
        return await _show_owed_locked(host, visit_id, own_char=own_char, lang=lang, interrupted=interrupted)


async def _show_owed_locked(host: Any, visit_id: str, *, own_char: str, lang: Optional[str],
                            interrupted: bool = False) -> bool:
    try:
        state = await _spool(visit_id).read_state()
    except Exception as exc:  # noqa: BLE001 - Unreadable: defer to bind replay.
        logger.debug("visit %s: state unreadable: %s", visit_id[:6], type(exc).__name__)
        return False
    if state is None or owed_request_id(state) is None:
        return False
    try:
        async with contextlib.AsyncExitStack() as stack:
            # Hold character lifecycle/account identity across name/account checks and pushing, preventing renames with old-name reuse or account switches.
            if not await _pin_push(stack, state):
                return False
            return await _push_owed(host, visit_id, state, lang, own_char=own_char, interrupted=interrupted)
    except TimeoutError:
        # Account-bound push exceeded _ACCOUNT_BOUND_PUSH_MAX_S: truncate this attempt, retaining state markers for bind replay.
        logger.info("visit %s: debrief push took longer than the account hold limit; cut off", visit_id[:6])
        return False


async def _push_owed(host: Any, visit_id: str, state: Mapping[str, Any], lang: Optional[str], *,
                     own_char: str, interrupted: bool) -> bool:
    from main_routers.visit_router.accounts import own_visit_uid

    if await _own_char(state) != own_char:
        # Rename occurred after backfill name resolution while waiting for locks; do not push to the potentially reused old name, defer until bind.
        return False
    if await own_visit_uid() != state.get("own_uid"):
        # Check account last because name resolution awaits; never push this visit's preview into a newly logged-in account.
        return False
    state = await reread_for_delivery(state)
    owed = owed_block(state, lang) if state is not None else None
    if owed is None:
        return False
    request_id, blocks, frame = owed
    if interrupted and state["debrief_choice"] in _CHIP_CHOICES:
        # Interruption notices and accompanying blocks use the same locked state; send only with chips, matching bind replay.
        # Same outlet as chips: validate/write one connection object rather than send_status rereading mgr.websocket.
        if not await host.send_frame({"type": "status", "message": json.dumps(
                {"code": "VISIT_INTERRUPTED_LAST_TIME", "details": {"visit_id": visit_id}}, ensure_ascii=False)}):
            # If the notice was not delivered, e.g. connection replacement, withhold the chip too and replay both on next visit_bind.
            return False
        if await own_visit_uid() != state.get("own_uid"):
            return False  # If the user signs out or switches accounts during the await that sends the notice, do not send the chip
    try:
        shown = bool(await host.render_chat_blocks(blocks, request_id=request_id, source_name=own_char))
    except Exception as exc:  # noqa: BLE001 - Missing page: retain state.json markers for next-bind replay.
        logger.info("visit %s: debrief block not shown: %s", visit_id[:6], type(exc).__name__)
        return False
    if shown and frame is not None:
        await host.send_frame(frame)
    if shown:
        correction = await settled_after_delivery(state, request_id)
        if correction is not None:
            await host.send_frame(correction)
    return shown


async def settled_after_delivery(state: Mapping[str, Any], request_id: str) -> Optional[dict]:
    """The ``chosen`` frame that greys the block just delivered, when the visit settled meanwhile (else None).

    Read after the frame was written: "forget this person" voids without the
    visit lock, so it can land while the block is in flight. The two
    interleave as "delivered, then voided"; this frame follows the block, so
    the page always ends greyed (greying is idempotent).
    """
    fresh = await reread_for_delivery(state)
    if fresh is None or owed_request_id(fresh) == request_id:
        return None
    choice = fresh["debrief_choice"]
    if choice not in _FINAL_CHOICES:
        return None
    return _state_frame(state["visit_id"], "chosen", request_id, choice=choice)


async def reread_for_delivery(state: Mapping[str, Any]) -> Optional[dict]:
    """Re-read ``state.json`` right before delivering its owed block (None: deliver nothing).

    Called with the push pinned, after the identity checks: "forget this
    person" voids without the visit lock, so a block derived from a state
    read before those awaits could show a diary that was just cleared. None
    when unreadable or no longer the same visit owner / character.
    """
    try:
        fresh = await _spool(state["visit_id"]).read_state()
    except Exception as exc:  # noqa: BLE001 - Unreadable: skip this push and await next-bind replay.
        logger.debug("visit %s: state unreadable before delivery: %s", state["visit_id"][:6], type(exc).__name__)
        return None
    if (fresh is None or fresh.get("own_uid") != state.get("own_uid")
            or fresh.get("own_char_uid") != state.get("own_char_uid")):
        return None
    return fresh


async def _visit_owner(visit_id: str) -> Optional[str]:
    """``state.json`` ``own_uid`` of ``visit_id`` (None when unreadable: fail closed)."""
    from main_logic.visit.spool import VisitSpool
    from main_routers.visit_router import runtime

    try:
        state = await VisitSpool(runtime.runtime_deps().config_dir(), visit_id).read_state()
    except Exception as exc:  # noqa: BLE001
        logger.debug("visit %s: state unreadable: %s", visit_id[:6], type(exc).__name__)
        return None
    owner = (state or {}).get("own_uid")
    return owner if isinstance(owner, str) and owner else None


async def build_debrief_record(lines: list[dict], lang: Optional[str]) -> str:
    """The record block of the summary turn (newest lines first within the token budget)."""
    from main_logic.visit.memory_commit import record_block_within_budget

    if not lines:
        return ""
    return await asyncio.to_thread(record_block_within_budget, lines, lang, VISIT_DEBRIEF_INPUT_MAX_TOKENS)


async def clean_summary(raw: Optional[str], *, family_names: tuple[str, ...], neutral_term: str,
                        peer_lines: list[str]) -> Optional[str]:
    """Summary text safe to say at home, or None (fallback line) when it copies the peer or is empty."""
    from utils.tokenize import atruncate_to_tokens

    text = strip_emotion_tags(str(raw or "")).strip()
    if not text:
        return None
    if family_names:
        text = redact_outbound(text, family_names=family_names, replacement=neutral_term)
    text = (await atruncate_to_tokens(text, VISIT_DEBRIEF_MAX_TOKENS)).strip()
    try:
        # Peer transcripts may contain many sentences; scan in workers without blocking other visits' event-loop work.
        await asyncio.to_thread(assert_no_peer_ngram, text, peer_lines, n=VISIT_PEER_NGRAM_N)
    except PeerNgramHit:
        logger.info("visit debrief: summary copied the peer, using the fixed line")
        return None
    return text or None


async def run_debrief(rt: Any, *, input_stamp: float) -> None:
    """Finalize step 4 for ``rt`` (a ``VisitRuntime``): summary, chips, ``ask_later``."""
    from config.prompts.prompts_visit import build_visit_debrief_prompt, get_visit_debrief_fallback

    # Include queued dialogue when upload headers stall past archival deadlines, matching /state rather than degrading to generic summaries.
    lines = rt._replay_lines()
    peer_lines = [str(line.get("text") or "") for line in lines if str(line.get("from")).startswith("peer_")]
    text: Optional[str] = None
    if lines:
        block = await build_debrief_record(lines, rt.lang)
        # Use only budgeted whole-visit records, excluding redundant/oversized isolated-session recent history that exposes peer text outside data blocks.
        raw = await rt.one_shot_turn(build_visit_debrief_prompt(block, rt.lang), timeout=VISIT_CEREMONY_TIMEOUT_S,
                                     without_history=True)
        text = await clean_summary(raw, family_names=tuple(rt.family_names), neutral_term=rt.neutral_term,
                                   peer_lines=peer_lines)
    if not text:
        text = get_visit_debrief_fallback(rt.lang)
    family_spoke = rt.host.last_user_input() > input_stamp
    if family_spoke:
        # If family speaks during ritual/summary generation, queue summaries/chips after that turn ends and display silently.
        await rt.wait_family_turn()
        if rt.handoff is not None:
            rt.handoff.skip("debrief")
        try:
            await rt.host.mirror_assistant_output(text, metadata=rt._mirror_meta("visit_debrief"),
                                                  request_id=f"visit-debrief-summary:{rt.visit_id}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("visit %s: debrief summary not shown: %s", rt.visit_id[:6], type(exc).__name__)
    else:
        await rt.speak_home_segment("debrief", text, kind="visit_debrief", silent=rt.finalize_reason == "goodbye")
    await _push_state(rt, "summary")
    # Diary uses spool; actual persisted sentences determine recordability independently of upload logs.
    # spool_lines increments after writer completion. If writes remain pending after bounded finalization, wait briefly before deciding,
    # never classify as empty prematurely. Appends already entered the single-writer queue before spool closure, so closure cannot discard them.
    await rt.settle_spool_appends(_STATE_WRITE_MAX_S)
    await _offer_chips(rt, has_lines=rt.spool_lines > 0)


async def _push_state(rt: Any, phase: str) -> None:
    """``visit_debrief{phase}`` status frame (§4.5; the chips themselves are ``chat_blocks``)."""
    await rt.host.send_frame({"type": "visit_debrief", "visit_id": rt.visit_id, "phase": phase,
                              "request_id": chips_request_id(rt.visit_id), "ts": rt.wall()})


async def _offer_chips(rt: Any, *, has_lines: bool) -> None:
    spool = rt.spool
    if not rt.memory_enabled or spool is None or not has_lines:
        return
    # Bound disk waiting because exit callbacks/teardown follow; stop at deadline and let startup backfill establish actual persistence.
    # Conditional write prevents late updates from replacing quick user choices with ask-later after chips appear.
    writing = asyncio.ensure_future(spool.mark_debrief_pending())
    writing.add_done_callback(lambda t: t.cancelled() or t.exception())
    # Register immediately and remove on completion, preventing loss if exit is canceled here.
    rt._keep_background(writing)
    await asyncio.wait([writing], timeout=_STATE_WRITE_MAX_S)
    if not writing.done():
        logger.warning("visit %s: debrief state still writing; continuing", rt.visit_id[:6])
    elif not writing.cancelled() and writing.exception() is not None:
        # Marker persistence failure still shows chips; next-startup backfill handles recovery.
        logger.warning("visit %s: debrief state not written: %s", rt.visit_id[:6],
                       type(writing.exception()).__name__)
    await show_chips(rt.host, rt.visit_id, own_char=rt.lanlan_name, lang=rt.lang)
    await _push_state(rt, "asked")


# =====================================================================
# Diary recording: preview, two-step writes, and state machine; OD-16 v4 sections 3.7.4/4.6, PR-14.
# =====================================================================

CHOICES = ("diary", "confirm", "forget", "abandon")
_CHIP_CHOICES = (None, "ask_later", "generating:diary")
CHIP_CHOICES = _CHIP_CHOICES
"""Choices whose owed block is the chips (the "interrupted" notice only ever accompanies them)."""
_FINAL_CHOICES = ("diary", "forget", "abandoned")
_PENDING_COMMIT = ("committing:diary", "commit_failed:diary")
_DIARY_LLM_MAX_TOKENS = 900
"""Output budget of the diary call: the entry (``VISIT_DIARY_MAX_TOKENS``) plus up to three facts and the JSON."""

router = APIRouter()

_locks: "weakref.WeakValueDictionary[tuple[int, str], asyncio.Lock]" = weakref.WeakValueDictionary()
_timers: dict[str, asyncio.Task] = {}
_running: set[asyncio.Task] = set()
_timer_epoch = 0

_now = time.time
"""Wall clock of the retry schedule (``debrief_retry.next_at`` survives restarts); tests replace it."""

_sleep = asyncio.sleep
"""Sleep of the retry timers; tests replace it."""


def preview_request_id(visit_id: str) -> str:
    return f"visit-debrief-preview:{visit_id}"


def failed_request_id(visit_id: str, seq: int) -> str:
    return f"visit-debrief-failed:{visit_id}:{int(seq)}"


def _visit_lock(visit_id: str) -> asyncio.Lock:
    # Serialize choices/generation/commit per visit, partitioned by event loop for tests' per-case loops.
    key = (id(asyncio.get_running_loop()), visit_id)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def written_of(writes: Mapping[str, Any]) -> dict:
    """``written`` of §4.6: facts only when some fact was actually new, the diary when ``/cache`` confirmed."""
    return {"facts": int(writes.get("facts_written") or 0) > 0, "cache": writes.get("cache") is True}


def unconfirmed_of(writes: Mapping[str, Any]) -> dict:
    """``unconfirmed`` of §4.6: a step that may have been written without confirmation (in flight counts)."""
    return {
        step: bool(writes.get(f"{step}_unconfirmed") or writes.get(f"{step}_inflight"))
        for step in ("facts", "cache")
    }


def apply_transition(writes: Mapping[str, Any], step: str, event: str, detail: Mapping[str, Any]) -> dict:
    """``debrief_writes`` after one ``commit_diary`` transition (a new dict)."""
    new = dict(writes)
    if event == "inflight":
        new[f"{step}_inflight"] = True
    elif event == "confirmed":
        new[step] = True
        new[f"{step}_inflight"] = False
        new[f"{step}_unconfirmed"] = False
        if step == "facts":
            new["facts_written"] = max(0, int(detail.get("facts_written") or 0))
    elif event == "rejected":
        new[f"{step}_inflight"] = False
    elif event == "indeterminate":
        new[f"{step}_inflight"] = False
        new[f"{step}_unconfirmed"] = True
    else:
        raise ValueError(f"unknown transition {event!r}")
    return new


def preview_blocks(visit_id: str, pending: Mapping[str, Any], lang: Optional[str], *,
                   committing: bool = False) -> list[dict]:
    """The preview message: the diary entry and every fact verbatim (``plain_text`` blocks) and "save / don't keep".

    ``committing`` (replayed while the two-step write is pending): "don't
    keep" is disabled and a status line says it is being saved; "save"
    stays clickable for an immediate retry.
    """
    blocks: list[dict] = [
        {"type": "text", "text": t("previewTitle", lang)},
        {"type": "text", "text": str(pending.get("diary") or ""), "plain_text": True},
    ]
    facts = [str(f) for f in (pending.get("facts") or [])]
    if facts:
        blocks.append({"type": "text", "text": t("previewFactsLabel", lang)})
        blocks.extend({"type": "text", "text": fact, "plain_text": True} for fact in facts)
    forget = {"id": "forget", "label": t("choiceForget", lang), "action": DEBRIEF_ACTION, "variant": "danger",
              "payload": {"visit_id": visit_id, "choice": "forget"}}
    if committing:
        forget["disabled"] = True
    blocks.append({"type": "buttons", "buttons": [
        {"id": "confirm", "label": t("previewConfirm", lang), "action": DEBRIEF_ACTION,
         "payload": {"visit_id": visit_id, "choice": "confirm"}},
        forget,
    ]})
    if committing:
        blocks.append({"type": "status", "tone": "info", "text": t("committing", lang)})
    return blocks


def failed_blocks(visit_id: str, state: Mapping[str, Any], lang: Optional[str]) -> list[dict]:
    """The "write failed" message of a ``commit_failed:diary`` visit (retry / give up, both carrying ``seq``)."""
    seq = int(state["debrief_commit_error"]["seq"])
    written = written_of(state["debrief_writes"])
    unconfirmed = unconfirmed_of(state["debrief_writes"])
    blocks: list[dict] = [{"type": "text", "text": t("commitFailed", lang)}]
    if written["facts"] and not written["cache"]:
        blocks.append({"type": "text", "text": t("commitFailedPartial", lang)})
    if any(unconfirmed.values()):
        blocks.append({"type": "text", "text": t("commitFailedUnknown", lang)})
    blocks.append({"type": "buttons", "buttons": [
        {"id": "retry", "label": t("commitRetry", lang), "action": DEBRIEF_ACTION,
         "payload": {"visit_id": visit_id, "choice": "confirm", "seq": seq}},
        {"id": "abandon", "label": t("commitAbandon", lang), "action": DEBRIEF_ACTION, "variant": "danger",
         "payload": {"visit_id": visit_id, "choice": "abandon", "written": written,
                     "unconfirmed": unconfirmed, "seq": seq}},
    ]})
    return blocks


def _state_frame(visit_id: str, phase: str, request_id: str, **extra: Any) -> dict:
    return {"type": "visit_debrief", "visit_id": visit_id, "phase": phase, "request_id": request_id,
            **extra, "ts": _now()}


def committing_frame(state: Mapping[str, Any]) -> dict:
    """``visit_debrief{committing, seq}``: pages grey "don't keep" and failure blocks up to ``seq``."""
    error = state.get("debrief_commit_error")
    seq = int(error["seq"]) if error else 0
    return _state_frame(state["visit_id"], "committing", preview_request_id(state["visit_id"]), seq=seq)


def commit_failed_frame(state: Mapping[str, Any]) -> dict:
    seq = int(state["debrief_commit_error"]["seq"])
    return _state_frame(state["visit_id"], "commit_failed", failed_request_id(state["visit_id"], seq), seq=seq,
                        written=written_of(state["debrief_writes"]),
                        unconfirmed=unconfirmed_of(state["debrief_writes"]))


def owed_request_id(state: Mapping[str, Any]) -> Optional[str]:
    """The ``request_id`` of the block :func:`owed_block` would deliver (None when nothing is owed)."""
    visit_id = state["visit_id"]
    choice = state["debrief_choice"]
    if choice in _CHIP_CHOICES:
        return chips_request_id(visit_id)
    if choice in ("preview:diary", "committing:diary"):
        return preview_request_id(visit_id)
    if choice == "commit_failed:diary":
        return failed_request_id(visit_id, state["debrief_commit_error"]["seq"])
    return None


def owed_block(state: Mapping[str, Any], lang: Optional[str]) -> Optional[tuple[str, list[dict], Optional[dict]]]:
    """``(request_id, blocks, state frame)`` a page owes for this visit's current choice, or None.

    Chips while undecided (``generating:diary`` too: a crashed generation
    is offered again, never regenerated on its own), the preview while
    ``preview:diary``, the preview with "don't keep" disabled while
    ``committing:diary`` (then a ``committing`` frame), the failure block
    while ``commit_failed:diary`` (then a ``commit_failed`` frame). The
    blocks are built from ``state.json`` only (no LLM).
    """
    visit_id = state["visit_id"]
    choice = state["debrief_choice"]
    request_id = owed_request_id(state)
    if request_id is None:
        return None
    if choice in _CHIP_CHOICES:
        return request_id, chip_blocks(visit_id, lang), None
    if choice == "preview:diary":
        return request_id, preview_blocks(visit_id, state["debrief_pending"], lang), None
    if choice == "committing:diary":
        return request_id, preview_blocks(visit_id, state["debrief_pending"], lang, committing=True), committing_frame(state)
    return request_id, failed_blocks(visit_id, state, lang), commit_failed_frame(state)


def _spool(visit_id: str):
    from main_logic.visit.spool import VisitSpool
    from main_routers.visit_router import runtime

    return VisitSpool(runtime.runtime_deps().config_dir(), visit_id)


def _host(own_char: str):
    from main_routers.visit_router.host_port import ManagerHost

    return ManagerHost.for_character(own_char)


async def _owner_signed_in(owner: str) -> bool:
    from main_routers.visit_router.accounts import own_visit_uid

    # Partition by community account: withhold another account's visit until it returns and binds.
    return await own_visit_uid() == owner


async def _pin_push(stack: contextlib.AsyncExitStack, state: Mapping[str, Any]) -> bool:
    """Hold the visit character's lifecycle and the signed-in account on ``stack``; False when the push must wait.

    Pushes resolve the character's current name, check the account and send
    while both are held: no rename can slip in between and hand the freed
    name (and its page) to another cat, and no logout / account switch can
    land between the account check and the write. False (skip; the marker
    stays in ``state.json`` for the bind replay) when the character config
    is unreadable or an account change is in progress.
    """
    from main_routers.visit_router import runtime

    try:
        await stack.enter_async_context(_hold_lifecycle([state["own_char_uid"]]))
    except Exception as exc:  # noqa: BLE001 - Unreadable character config: skip push, retaining state markers for bind replay.
        logger.info("visit debrief: lifecycle not held, push skipped: %s", type(exc).__name__)
        return False
    # Lock account after character lifecycle, synchronously without await; switches wait for pushes, and ongoing switches prevent pushing.
    return await stack.enter_async_context(runtime.hold_account_for_push())


async def _target(state: Mapping[str, Any]) -> Optional[tuple[str, Any]]:
    """``(current name, host)`` of the visit's character, resolved now from ``own_char_uid``.

    None when another community account is signed in, the character is gone,
    or it has no manager. Called with the character lifecycle and the
    account held (:func:`_pin_push`) through the push: a rename meanwhile
    (the freed name maybe reused by another cat) must not send this visit's
    diary to that cat's page, nor a switch hand it to another account.
    """
    name = await _own_char(state)
    # Check account last because name resolution awaits config reads; catch intervening switches too.
    if not name or not await _owner_signed_in(state["own_uid"]):
        return None
    host = _host(name)
    return (name, host) if host is not None else None


async def _send(state: Mapping[str, Any], frame: dict) -> bool:
    """Push ``frame`` to the visit character's page; best effort, never raises (False when not sent).

    Pushes follow a ``state.json`` change that already happened: a failure
    here (character config unreadable meanwhile, page gone) must not turn
    that outcome into an error. The bind replay catches the page up.
    """
    try:
        async with contextlib.AsyncExitStack() as stack:
            if not await _pin_push(stack, state):
                return False
            target = await _target(state)
            if target is None:
                return False
            return bool(await target[1].send_frame(frame))
    except Exception as exc:  # noqa: BLE001 - Best-effort push: persisted state is corrected through bind replay.
        logger.info("visit debrief: frame not sent: %s", type(exc).__name__)
        return False


async def _render(state: Mapping[str, Any], blocks: list[dict], request_id: str) -> bool:
    """Render ``blocks`` on the visit character's page; best effort like :func:`_send`."""
    try:
        async with contextlib.AsyncExitStack() as stack:
            if not await _pin_push(stack, state):
                return False
            target = await _target(state)
            if target is None:
                return False
            name, host = target
            return bool(await host.render_chat_blocks(blocks, request_id=request_id, source_name=name))
    except Exception as exc:  # noqa: BLE001 - Missing page/etc.: retain state markers for bind replay.
        logger.info("visit debrief: block %s not shown: %s", request_id, type(exc).__name__)
        return False


def _lang() -> str:
    from main_routers.visit_router.local_context import prompt_lang

    return prompt_lang()


async def _own_char(state: Mapping[str, Any]) -> Optional[str]:
    from main_logic.visit import local_chars

    # Resolve current names by uid, following renames; deleted characters yield None.
    return await local_chars.resolve_char_name(state["own_char_uid"])


def _hold_lifecycle(character_uids):
    """Rename / delete of these characters answer 400 while held (``runtime.hold_character_lifecycle``)."""
    from main_routers.visit_router import runtime

    return runtime.hold_character_lifecycle(character_uids)


def _diary_llm():
    from config.visit_settings import VISIT_LLM_TIMEOUT_S
    from main_routers.visit_router.llm import one_shot_llm

    return one_shot_llm(max_tokens=_DIARY_LLM_MAX_TOKENS, timeout=VISIT_LLM_TIMEOUT_S)


async def _family_names() -> tuple[str, ...]:
    from main_routers.visit_router.background import _family_names as family_names

    return await family_names()


# -- Background backoff retries --


def _cancel_timer(visit_id: str) -> None:
    task = _timers.pop(visit_id, None)
    if task is not None and task is not asyncio.current_task() and not task.done():
        task.cancel()


def schedule_commit_retry(visit_id: str, next_at: float, *, epoch: Optional[int] = None) -> None:
    """(Re)arm the one in-process retry of a ``committing:diary`` visit at wall time ``next_at``.

    ``epoch`` (from a commit that started earlier): when :func:`cancel_commit_timers`
    ran since, nothing is armed -- the background was stopped meanwhile.
    """
    if epoch is not None and epoch != _timer_epoch:
        return
    _cancel_timer(visit_id)
    task = asyncio.ensure_future(_retry_at(visit_id, float(next_at)))
    _timers[visit_id] = task


_LOCAL_FAILURE_RETRY_S = 600
"""Next retry after a retry that failed locally (``state.json`` unreadable / unwritable, ...)."""


async def _retry_at(visit_id: str, next_at: float) -> None:
    epoch = _timer_epoch
    try:
        await _sleep(max(0.0, next_at - _now()))
    except asyncio.CancelledError:
        return
    me = asyncio.current_task()
    if _timers.get(visit_id) is me:
        # Remove this timer first: another transient failure schedules the next one without canceling this running task.
        del _timers[visit_id]
    _running.add(me)
    try:
        # After waking, check under lock for newer timers scheduled by failed manual writes while waiting;
        # skip immediate retry to preserve backoff/new scheduling. Apply to all timers, including local-failure replacements.
        await run_commit(visit_id, "timer")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - Temporary state.json access failures must not terminate automatic retries.
        logger.warning("visit %s: diary retry failed locally, again in %ds: %r",
                       visit_id[:6], _LOCAL_FAILURE_RETRY_S, exc)
        # Writes have unlimited retries; local failures reschedule too, unless stopped/rolled-back generations expire.
        if visit_id not in _timers:
            schedule_commit_retry(visit_id, _now() + _LOCAL_FAILURE_RETRY_S, epoch=epoch)
    finally:
        _running.discard(me)


def cancel_commit_timers() -> list[asyncio.Task]:
    """Cancel every pending or running diary retry (shutdown / startup rollback); returns the tasks.

    Bumps the timer epoch, so a commit already past its request does not arm
    a new retry afterwards.
    """
    global _timer_epoch
    _timer_epoch += 1
    tasks = list(_timers.values()) + list(_running)
    _timers.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    return tasks


def pending_commit_retries() -> dict[str, asyncio.Task]:
    """The armed retries by visit id (for tests and diagnostics)."""
    return dict(_timers)


async def resume_diary_commit(spool: Any, state: Mapping[str, Any]) -> None:
    """Startup recovery callback: arm the retry of a ``committing:diary`` visit at its persisted ``next_at``.

    A ``next_at`` still in the future is waited for (restarts never bypass the
    backoff); a visit without ``debrief_retry`` (older version) is due now.
    """
    visit_id = spool.visit_id
    async with _visit_lock(visit_id):
        # Backfill holds scan-time snapshots; never overwrite newer next_at scheduled by an intervening failed manual write.
        if visit_id in _timers:
            return
        current = await spool.read_state()
        if current is None or current["debrief_choice"] != "committing:diary":
            return
        retry = current.get("debrief_retry")
        next_at = float(retry["next_at"]) if isinstance(retry, Mapping) else 0.0
        schedule_commit_retry(visit_id, next_at)


# -- Shared two-step commits for endpoints, timers, and startup backfill --


async def run_commit(visit_id: str, trigger: str) -> tuple[int, dict]:
    """Run (or continue) the two-step write of ``visit_id`` under its lock; ``(http status, body)``.

    The only place a diary is written: the ``confirm`` endpoint, the retry
    timer and startup recovery all come here. Done → ``diary``; transient →
    stays ``committing:diary`` with the next backoff step persisted and
    armed; permanent → ``commit_failed:diary`` with the failure block.
    """
    async with _visit_lock(visit_id):
        return await _run_commit_locked(visit_id, trigger)


async def _run_commit_locked(visit_id: str, trigger: str) -> tuple[int, dict]:
    epoch = _timer_epoch
    spool = _spool(visit_id)
    state = await spool.read_state()
    if state is None:
        return 404, {"error": "unknown_or_expired"}
    if state["debrief_choice"] != "committing:diary":
        return _not_committing(state)
    if trigger == "timer" and visit_id in _timers:
        # A newer timer appeared after this one woke and removed itself from _timers; replacement invalidates this attempt.
        return 409, {"error": "superseded"}
    # Hold character lifecycle during both writes; rename/deletion gets 400, preventing split writes across old/new names.
    async with _hold_lifecycle([state["own_char_uid"]]):
        return await _commit_held(visit_id, trigger, spool, state, epoch)


async def _commit_held(visit_id: str, trigger: str, spool: Any, state: Mapping[str, Any],
                       epoch: int) -> tuple[int, dict]:
    from config.visit_settings import VISIT_DEBRIEF_COMMIT_BACKOFF_S
    from main_logic.visit import debrief_writers

    own_char = await _own_char(state)
    if not own_char:
        # Deleted character: retirement owns visit files; stop writing.
        return 404, {"error": "unknown_or_expired"}
    writes = dict(state["debrief_writes"])

    async def on_transition(step: str, event: str, detail: Mapping[str, Any]) -> None:
        nonlocal writes
        writes = apply_transition(writes, step, event, detail)
        # Persist before commit_diary continues, retaining evidence of possible writes after in-flight crashes.
        await spool.update_state(debrief_writes=writes)

    result = await debrief_writers.commit_diary(own_char, visit_id, state["debrief_pending"], writes, on_transition)
    lang = _lang()
    if result.kind == "done":
        await spool.update_state(debrief_choice="diary", debrief_pending=None, debrief_retry=None,
                                 debrief_chip_pending=False)
        _cancel_timer(visit_id)
        try:
            await spool.delete_if_settled()
        except Exception as exc:  # noqa: BLE001 - Diary/state persisted: defer cleanup failures to later sweeps without changing the result.
            logger.info("visit %s: settled files not removed yet: %s", visit_id[:6], type(exc).__name__)
        await _send(state, _state_frame(visit_id, "chosen", preview_request_id(visit_id), choice="diary"))
        if trigger != "http":
            # Originating pages append status themselves; background completion without an origin sends a separate update.
            await _render(state, [{"type": "status", "tone": "success", "text": t("savedDiary", lang)}],
                          f"visit-debrief-done:{visit_id}")
        return 200, {"ok": True, "applied": "diary"}
    if result.kind == "transient":
        retry = state["debrief_retry"] or {"attempts": 0, "next_at": 0.0}
        attempts = int(retry["attempts"]) + 1
        delay = VISIT_DEBRIEF_COMMIT_BACKOFF_S[min(attempts, len(VISIT_DEBRIEF_COMMIT_BACKOFF_S)) - 1]
        next_at = _now() + delay
        await spool.update_state(debrief_retry={"attempts": attempts, "next_at": next_at})
        schedule_commit_retry(visit_id, next_at, epoch=epoch)
        logger.warning("visit %s: diary write step %s not done (status %s); retry %d in %ds",
                       visit_id[:6], result.step, result.status, attempts, delay)
        return 503, {"retry": True, "next_retry_at": next_at}
    previous = state["debrief_commit_error"]
    seq = (int(previous["seq"]) if previous else 0) + 1
    state = await spool.update_state(
        debrief_choice="commit_failed:diary", debrief_retry=None,
        debrief_commit_error={"step": result.step, "status": int(result.status), "at": _now(), "seq": seq},
    )
    _cancel_timer(visit_id)
    logger.warning("visit %s: diary write step %s rejected (status %s); waiting for the user",
                   visit_id[:6], result.step, result.status)
    await _send(state, commit_failed_frame(state))
    await _render(state, failed_blocks(visit_id, state, lang), failed_request_id(visit_id, seq))
    return 502, {"error": "commit_failed", "step": result.step, "status": int(result.status),
                 "written": written_of(state["debrief_writes"]),
                 "unconfirmed": unconfirmed_of(state["debrief_writes"])}


def _already_chosen(state: Mapping[str, Any]) -> tuple[int, dict]:
    choice = state["debrief_choice"]
    if choice in _PENDING_COMMIT:
        return 409, {"error": "already_chosen", "choice": "diary", "completed": False,
                     "state": "committing" if choice == "committing:diary" else "commit_failed"}
    return 409, {"error": "already_chosen", "choice": choice, "completed": True}


def _not_committing(state: Mapping[str, Any]) -> tuple[int, dict]:
    choice = state["debrief_choice"]
    if choice in _CHIP_CHOICES or choice == "preview:diary":
        return 409, {"error": "not_previewed"}
    return _already_chosen(state)


# ── POST /api/visit/debrief/choice ───────────────────────────────────────


async def _choose(visit_id: str, choice: str, seq: Optional[int], lanlan_name: str) -> tuple[int, dict]:
    from main_logic.visit.spool import is_digestable
    from main_routers.visit_router.accounts import own_visit_uid

    spool = _spool(visit_id)
    lock = _visit_lock(visit_id)
    async with lock:
        # Capture generation on lock entry; shutdown/rollback during any later await prevents rescheduling after failed confirmation.
        epoch = _timer_epoch
        state = await spool.read_state()
        # Treat other community accounts' visits as absent on shared computers; memory-disabled visits have no chips anyway.
        if (state is None or state["own_uid"] != await own_visit_uid() or not is_digestable(state)
                or state["finalized"] is None):
            return 404, {"error": "unknown_or_expired"}
        own_char = await _own_char(state)
        if not own_char or own_char != lanlan_name:
            # Origin no longer belongs to this catgirl, possibly after old-name reuse: clicking stale blocks must neither
            # return this preview there nor change its choice.
            return 404, {"error": "unknown_or_expired"}
        current = state["debrief_choice"]
        if choice == "diary":
            return await _choose_diary(spool, state, own_char)
        if choice == "forget":
            if current in _CHIP_CHOICES or current == "preview:diary":
                from main_logic.visit.debrief_writers import write_forget

                await write_forget(spool)
                # Frames target the clicked block: forgetting from preview disables that preview, not the earlier chip.
                target = preview_request_id(visit_id) if current == "preview:diary" else chips_request_id(visit_id)
                await _send(state, _state_frame(visit_id, "chosen", target, choice="forget"))
                return 200, {"ok": True, "applied": "forget"}
            return _already_chosen(state)
        if choice == "abandon":
            if current != "commit_failed:diary":
                if current in _FINAL_CHOICES:
                    return _already_chosen(state)
                return 409, {"error": "not_failed"}
            if seq != state["debrief_commit_error"]["seq"]:
                return 409, {"error": "stale_failure", "seq": state["debrief_commit_error"]["seq"]}
            from main_logic.visit.debrief_writers import write_forget

            written = written_of(state["debrief_writes"])
            unconfirmed = unconfirmed_of(state["debrief_writes"])
            await write_forget(spool, final_choice="abandoned")
            _cancel_timer(visit_id)
            await _send(state, _state_frame(visit_id, "chosen", failed_request_id(visit_id, seq),
                                               choice="abandoned", written=written, unconfirmed=unconfirmed))
            return 200, {"ok": True, "applied": "abandoned", "written": written, "unconfirmed": unconfirmed}
        # confirm
        if current == "preview:diary":
            applied, state = await spool.update_state_if("preview:diary", debrief_choice="committing:diary")
            if not applied:
                return _already_chosen(state)
        elif current == "commit_failed:diary":
            if seq != state["debrief_commit_error"]["seq"]:
                # Clicked legacy failure block or missing seq bypass: honor the latest block without resubmission.
                return 409, {"error": "stale_failure", "seq": state["debrief_commit_error"]["seq"]}
            # Retry resets backoff and completes only the unfinished step.
            state = await spool.update_state(debrief_choice="committing:diary", debrief_retry=None)
        elif current == "committing:diary":
            # Manual write during committing also resets backoff; repeated clicks must not defer automatic retries by an hour.
            state = await spool.update_state(debrief_retry=None)
        else:
            return _not_committing(state)
        _cancel_timer(visit_id)
        await _send(state, committing_frame(state))
        try:
            return await _run_commit_locked(visit_id, "http")
        except asyncio.CancelledError:
            raise
        except Exception:
            # Local failure, e.g. temporary state.json write failure: prior timer was removed, so reschedule instead of stranding committing.
            if visit_id not in _timers:
                schedule_commit_retry(visit_id, _now() + _LOCAL_FAILURE_RETRY_S, epoch=epoch)
            raise


async def _caller_still_current(state: Mapping[str, Any], own_char: str) -> bool:
    """Whether the page that sent this request may still get the preview text in the response.

    ``own_char`` is the name the request was resolved under: when the
    character was renamed meanwhile (its old name maybe given to another
    cat) or the account changed, the preview is not returned to that page.
    It is persisted and was pushed by uid to the character's current page;
    the bind replay shows it there. No await follows a True answer before
    the response is returned.
    """
    from main_routers.visit_router.accounts import own_visit_uid

    if await _own_char(state) != own_char:
        return False
    # Check account last because name resolution awaits.
    return await own_visit_uid() == state["own_uid"]


async def _choose_diary(spool: Any, state: Mapping[str, Any], own_char: str) -> tuple[int, dict]:
    from main_logic.visit import debrief_writers
    from main_routers.visit_router.accounts import own_visit_uid

    visit_id = spool.visit_id
    current = state["debrief_choice"]
    if current == "preview:diary":
        # Reuse persisted previews without regeneration; share new-generation output checks under character/account locks and reread choices last.
        try:
            return await _answer_preview(spool, state, own_char, state["debrief_pending"])
        except TimeoutError:
            return 503, {"retry": True}
    if current not in _CHIP_CHOICES:
        return _already_chosen(state)
    # Conditional transition must not overwrite clear-person invalidation, which bypasses this lock and changes undecided debriefs to forget.
    # Expected values include all chip states: normal transitions, including late finalization None->ask_later after chip persistence
    # or retry generating->generating, are not competing writes. Invalidation's forget still blocks them.
    # Atomically include pending-delivery markers; if bounded finalization marking failed, reconnect can still replay chips after generation failure.
    applied, found = await spool.update_state_if(_CHIP_CHOICES, debrief_choice="generating:diary",
                                                 debrief_chip_pending=True)
    if not applied:
        return _already_chosen(found)
    try:
        preview = await debrief_writers.generate_diary_preview(
            spool, llm=_diary_llm(), own_char=own_char, family_names=await _family_names(),
        )
    except debrief_writers.NothingToRecord:
        # Chips require spool sentences; reaching here means spool disappeared later. Mark abandoned, as for memory-on/no-transcript,
        # rather than replaying chips that inevitably return 404 on every bind.
        applied, found = await spool.update_state_if("generating:diary", debrief_choice="abandoned",
                                                     debrief_pending=None, debrief_chip_pending=False)
        if not applied:
            return _already_chosen(found)
        try:
            await spool.delete_if_settled()
        except Exception as exc:  # noqa: BLE001 - Abandoned persisted and chip replay disabled: defer cleanup, still sending disabled-state frames.
            logger.info("visit %s: settled files not removed yet: %s", visit_id[:6], type(exc).__name__)
        # Chips remain active on already-open pages; disable with choice='abandoned'.
        await _send(state, _state_frame(visit_id, "chosen", chips_request_id(visit_id), choice="abandoned"))
        return 404, {"error": "unknown_or_expired"}
    except debrief_writers.DiaryGenerationFailed:
        # Retain generating:diary so the next diary click regenerates.
        return 503, {"retry": True}
    pending = preview.to_pending()
    # Atomically persist preview/state/pending delivery so bind replays this version; write only while still generating,
    # never restoring this person's diary/facts if clearing invalidated the visit as forget during generation.
    applied, found = await spool.update_state_if("generating:diary", debrief_pending=pending,
                                                 debrief_choice="preview:diary", debrief_chip_pending=True)
    if not applied:
        return _already_chosen(found)
    if await own_visit_uid() != state["own_uid"]:
        # Logout/account switch during generation: retain persisted previews for that account's bind, without pushing/returning to the current page.
        return 404, {"error": "unknown_or_expired"}
    lang = _lang()
    await _render(state, preview_blocks(visit_id, pending, lang), preview_request_id(visit_id))
    await _send(state, _state_frame(visit_id, "preview", preview_request_id(visit_id)))
    try:
        return await _answer_preview(spool, state, own_char, pending)
    except TimeoutError:
        # Account-bound deadline expired: return no body; persisted previews remain available through frontend retry/bind replay.
        return 503, {"retry": True}


async def _answer_preview(spool: Any, state: Mapping[str, Any], own_char: str,
                          pending: Mapping[str, Any]) -> tuple[int, dict]:
    """The HTTP answer carrying a freshly generated preview, decided with the caller pinned.

    The character lifecycle and the signed-in account are held from the
    caller check to the final state read (:func:`_pin_push`): no rename or
    account switch can land after the caller was confirmed. The choice is
    re-read last, since "forget this person" voids without the visit lock;
    nothing is awaited between that read and the returned preview.
    """
    visit_id = spool.visit_id
    async with contextlib.AsyncExitStack() as stack:
        if not await _pin_push(stack, state):
            # Switching account/unreadable character config: return no body; replay persisted previews by uid to its current page.
            return 404, {"error": "unknown_or_expired"}
        caller_current = await _caller_still_current(state, own_char)
        try:
            now = await spool.read_state()
        except Exception:  # noqa: BLE001 - Unreadable: withhold body because invalidation is uncertain; bind replay uses authoritative state.json.
            return 503, {"retry": True}
        if now is None or now["debrief_choice"] != "preview:diary":
            if now is not None:
                # Visit invalidated during push: disable the just-pushed preview and return no preview body.
                await _send(now, _state_frame(visit_id, "chosen", preview_request_id(visit_id),
                                              choice=now["debrief_choice"]))
                return _already_chosen(now)
            return 404, {"error": "unknown_or_expired"}
        if not caller_current:
            # Rename/old-name reuse or account switch during generation: never return to the stale origin page.
            return 404, {"error": "unknown_or_expired"}
        from main_routers.visit_router.display_socket import chat_blocks_frame

        # If push missed the origin through disconnection/write failure, use this identical block to display locally with request_id deduplication.
        frame = chat_blocks_frame(preview_blocks(visit_id, pending, _lang()),
                                  request_id=preview_request_id(visit_id), source_name=own_char)
        return 200, {"ok": True, "applied": "preview", "preview": dict(pending), "chat_blocks": frame}


def _parse_choice(payload: Mapping[str, Any]) -> tuple[Optional[str], Optional[str], Optional[int],
                                                        Optional[str], Optional[str]]:
    """``(visit_id, choice, seq, lanlan_name, error code)``; ``lanlan_name`` is the requesting page's character."""
    from main_routers.visit_router.http import _valid_name
    from utils.visit_wire import VISIT_ID_RE

    visit_id = payload.get("visit_id")
    if not isinstance(visit_id, str) or VISIT_ID_RE.fullmatch(visit_id) is None:
        return None, None, None, None, "visit_id_format"
    choice = payload.get("choice")
    if choice not in CHOICES:
        return None, None, None, None, "invalid_choice"
    seq = payload.get("seq")
    if seq is not None and (isinstance(seq, bool) or not isinstance(seq, int) or seq < 1):
        return None, None, None, None, "invalid_seq"
    lanlan_name = payload.get("lanlan_name")
    if not _valid_name(lanlan_name):
        # Match other visit endpoints' character-name validation: nonempty, printable, at most 128 characters.
        return None, None, None, None, "invalid_lanlan_name"
    return visit_id, choice, seq, lanlan_name, None


@router.post("/debrief/choice")
async def debrief_choice(request: Request):
    """The user's answer to the debrief chips / preview / failure block (§4.6; state machine in §3.7.4)."""
    from main_routers.system_router._shared import _read_json_object
    from main_routers.visit_router.local_guard import http_denied

    payload = await _read_json_object(request)
    denied = http_denied(request, payload)
    if denied is not None:
        return denied
    visit_id, choice, seq, lanlan_name, error = _parse_choice(payload)
    if error is not None:
        return JSONResponse({"ok": False, "code": error}, status_code=400)
    try:
        status, body = await _choose(visit_id, choice, seq, lanlan_name)
    except Exception as exc:  # noqa: BLE001 - state.json access failures etc. are retryable.
        logger.warning("visit %s: debrief choice %s failed: %r", visit_id[:6], choice, exc)
        return JSONResponse({"retry": True}, status_code=503)
    return JSONResponse(body, status_code=status)
