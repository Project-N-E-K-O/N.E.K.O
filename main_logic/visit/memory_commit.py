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

"""Visit-region commit and the last-visit summary of one finished visit.

Design: ``docs/design/visit-infrastructure.md`` section 3.7.3 and PR-08
``memory_commit.py``.

Both run once per visit, at finalize (as background work) or from startup
recovery, and both look only at the visit's own ``state.json.memory_enabled``
(frozen when the visit began); the debrief choice never matters here.

* :func:`commit_visit_region` digests the spool into the visit memory region:
  the group digest (``/scoped_history`` on ``group_chat``) and the two peer
  speakers (segments batch). Lines are capped at ``VISIT_DIGEST_MAX_LINES``
  (every cat line, then the newest human lines), sorted by
  ``(lp, side_rank)`` and cut into consecutive batches of at most
  ``SCOPED_HISTORY_BATCH_MAX_MESSAGES``. Each batch carries the key
  ``visit-digest:{visit_id}:{run}:group|segments:{batch}``; the run (only
  ``run=0`` today, no periodic digest) and every batch are registered in
  ``state.json.digest_writes`` before the first request and ticked off one by
  one, so a retry resends exactly the unfinished batches with the same lines.
* :func:`commit_last_summary` makes one LLM call that summarizes the visit for
  the next visit with the same person and stores it in the roster only.

Both hold :func:`peer_lock` ``(own_char_uid, peer_uid)``, the same lock the
local forget takes, so a forget never interleaves with them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import weakref
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import SCOPED_HISTORY_BATCH_MAX_MESSAGES
from config.prompts.prompts_visit import (
    build_visit_last_summary_prompt,
    build_visit_record_block,
    get_family_neutral_term,
    get_visit_speaker_header,
)
from config.visit_settings import (
    VISIT_DIGEST_MAX_LINES,
    VISIT_SHUTDOWN_BUDGET_S,
    VISIT_LAST_SUMMARY_INPUT_MAX_TOKENS,
    VISIT_LAST_SUMMARY_MAX_TOKENS,
    VISIT_LLM_TIMEOUT_S,
    VISIT_PEER_NGRAM_N,
)
from main_logic.visit import memory_bridge
from main_logic.visit.forget import ForgetEpochs, ForgetEpochsUnreadable, ForgetEpochsUnsynced
from main_logic.visit.sanitize import (
    PeerNgramHit,
    assert_no_peer_ngram,
    neutralize_display_name,
    redact_outbound,
    strip_emotion_tags,
)
from main_logic.visit.spool import VisitSpool, is_digestable, is_spool_open
from main_logic.visit.subjects import (
    PeerRoster,
    RosterCorruptError,
    derive_person_id,
    derive_short_code,
    group_chat_subject,
    group_participant_subject,
    participant_subject,
    read_roster_marker,
)
from memory.scoped_client import ScopedMemoryClient
from utils.language_utils import is_supported_language_code
from utils.logger_config import get_module_logger
from utils.tokenize import count_tokens, take_lines_within_token_budget, truncate_to_tokens

logger = get_module_logger(__name__, "Main")

SIDE_RANK = {"host": 0, "guest": 1}
_CAT_SPEAKERS = ("own_cat", "peer_cat")
_PEER_SPEAKERS = ("peer_cat", "peer_human")

ResolveCharName = Callable[[str], Awaitable["str | None"]]
"""``own_char_uid`` -> the local character's current name, ``None`` once deleted."""

SummaryLLM = Callable[[str], Awaitable[str]]
"""One-shot LLM call: prompt in, text out (no session, no history)."""


# -- One lock per pair --

_PEER_LOCKS: "weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock]" = weakref.WeakValueDictionary()


def peer_lock(own_char_uid: str, peer_uid: str) -> asyncio.Lock:
    """Return the process-wide lock of one ``(own_char_uid, peer_uid)`` pair.

    Taken by :func:`commit_visit_region` (finalize and recovery),
    :func:`commit_last_summary` and every local forget of that pair. Callers
    keep the returned lock referenced while holding or waiting on it (the
    registry holds it weakly, an idle pair's lock is dropped).
    """
    key = (str(own_char_uid), str(peer_uid))
    lock = _PEER_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PEER_LOCKS[key] = lock
    return lock


# -- Sentence selection and batching: pure functions --


def line_order_key(line: Mapping[str, Any]) -> tuple[int, int]:
    """Total order of spool lines: ``(lp, side_rank)``, host before guest on a tie."""
    return (int(line["lp"]), SIDE_RANK.get(line.get("side"), 2))


def select_digest_lines(
    lines: Iterable[Mapping[str, Any]], max_lines: int | None = None,
) -> tuple[list[dict], int]:
    """Cap one run's lines at ``max_lines`` (default ``VISIT_DIGEST_MAX_LINES``).

    Every cat line (both sides) is kept first, then human lines from the
    newest backwards until the cap. Returns ``(selected, dropped)`` with
    ``selected`` in ``(lp, side_rank)`` order.
    """
    limit = VISIT_DIGEST_MAX_LINES if max_lines is None else int(max_lines)
    ordered = sorted((dict(line) for line in lines), key=line_order_key)
    cats = [line for line in ordered if line["from"] in _CAT_SPEAKERS]
    humans = [line for line in ordered if line["from"] not in _CAT_SPEAKERS]
    if len(cats) > limit:
        # Protocol permits at most 80 catgirl lines per visit; excess means corrupt data. Still bound total count and keep the latest.
        cats = cats[-limit:]
    room = max(0, limit - len(cats))
    kept_humans = humans[-room:] if room else []
    selected = sorted(cats + kept_humans, key=line_order_key)
    return selected, len(ordered) - len(selected)


def split_batches(lines: Sequence[dict], size: int | None = None) -> list[list[dict]]:
    """Cut ``lines`` (already ordered) into consecutive batches of at most ``size``."""
    size = SCOPED_HISTORY_BATCH_MAX_MESSAGES if size is None else int(size)
    return [list(lines[i:i + size]) for i in range(0, len(lines), size)]


def plan_digest_batches(
    selected: Sequence[dict], size: int | None = None,
) -> tuple[list[list[dict]], list[list[dict]]]:
    """Return ``(group_batches, segments_batches)`` of one run.

    The group digest takes every selected line; the segments batches take the
    selected peer lines only, both peer speakers counted together per batch.
    """
    peer = [line for line in selected if line["from"] in _PEER_SPEAKERS]
    return split_batches(selected, size), split_batches(peer, size)


def _batch_fingerprint(batch: Sequence[Mapping[str, Any]]) -> str:
    """Short digest of which lines a batch holds and what they say (``lp``, ``side``, speaker, text)."""
    # Include body text: if lines change after round creation, resumed work cannot reuse old plans/keys for different content.
    identity = [[line["lp"], line["side"], line["from"], line.get("text")] for line in batch]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def digest_key(visit_id: str, run: int, part: str, batch: int) -> str:
    """``visit-digest:{visit_id}:{run}:{part}:{batch}`` (``part`` is ``group`` or ``segments``)."""
    return f"visit-digest:{visit_id}:{run}:{part}:{batch}"


# -- Visit-region digest --


@dataclass
class CommitResult:
    """Outcome of :func:`commit_visit_region`.

    ``ok`` means nothing is left to retry for now (done, or skipped because
    there is nothing this function may do); ``requests`` counts the scoped
    history calls sent; ``skipped`` names the reason nothing was sent.
    """

    ok: bool
    requests: int = 0
    skipped: str | None = None
    run: int | None = None
    dropped_lines: int = 0


def _visit_subjects(state: Mapping[str, Any]) -> list[dict]:
    pair_id = state["pair_id"]
    return [
        group_chat_subject(pair_id),
        group_participant_subject(pair_id, state["peer_char_id"]),
        participant_subject(derive_person_id(state["own_uid"], state["peer_uid"])),
    ]


_IDENTITY_FIELDS = ("own_uid", "own_char_uid", "peer_uid", "pair_id", "peer_char_id")


def _header_matches_state(header: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    """Whether the transcript header and ``state.json`` describe the same visit identity."""
    return all(header.get(name) == state[name] for name in _IDENTITY_FIELDS)


def _protected_display_names(
    own_char: str, lang: str | None, family_names: Iterable[str], local_char_names: Iterable[str],
) -> tuple[str, ...]:
    """Names a peer-reported display name must never take over (see ``neutralize_display_name``).

    The local character and every other local character, the local family
    names, the neutral family term, and the speaker labels of all four visit
    speakers (own cat / own human as well as the two peer labels).
    """
    labels = tuple(
        get_visit_speaker_header(speaker, lang).strip("[] ")
        for speaker in ("own_cat", "own_human", "peer_cat", "peer_human")
    )
    return (own_char, *local_char_names, *labels, get_family_neutral_term(lang), *family_names)


async def _peer_displays(
    config_dir: Path, state: Mapping[str, Any], own_char: str, lang: str | None,
    family_names: Iterable[str] = (), local_char_names: Iterable[str] = (),
) -> tuple[str, str]:
    cat_label = get_visit_speaker_header("peer_cat", lang).strip("[] ")
    human_label = get_visit_speaker_header("peer_human", lang).strip("[] ")
    short = derive_short_code(state["peer_uid"])
    roster = PeerRoster(config_dir, own_uid=state["own_uid"])
    # Display names are decorative; malformed roster entries fall back to generic labels rather than failing segment digests.
    peer = await roster.get_peer(state["peer_uid"])
    entry = await roster.get_char_entry(state["peer_uid"], own_char)
    chars = entry.get("chars") if isinstance(entry, dict) else None
    cat_info = chars.get(state["peer_char_id"]) if isinstance(chars, dict) else None
    peer = peer if isinstance(peer, dict) else {}
    cat_info = cat_info if isinstance(cat_info, dict) else {}
    # Replace peer-reported names impersonating local characters/family/side speaker labels with generic labels; otherwise peer speech enters
    # speaker_label/display_name as local characters, you, or your family, corrupting extracted fact attribution.
    protected = _protected_display_names(own_char, lang, family_names, local_char_names)
    cat = neutralize_display_name(cat_info.get("display_name"), protected_names=protected,
                                  generic_label=cat_label, short_code=short)
    human = neutralize_display_name(peer.get("display_name"), protected_names=protected,
                                    generic_label=human_label, short_code=short)
    return cat, human


async def commit_visit_region(
    spool: VisitSpool,
    *,
    resolve_char_name: ResolveCharName,
    client: ScopedMemoryClient | None = None,
    shutdown: bool = False,
    now: float | None = None,
    family_names: Iterable[str] = (),
    local_char_names: Iterable[str] = (),
) -> CommitResult:
    """Digest one finished visit into the visit memory region (finalize or recovery).

    Runs only when ``state.json.memory_enabled`` (the value frozen at the
    start of the visit) is true and the visit is finalized; otherwise zero
    requests. Inside :func:`peer_lock` it resumes the unfinished run (same
    ``through_lp``, ``requested_at``, forget generations and batches) or
    registers a new one for the lines past ``digested_through_lp``, then sends
    every unfinished group batch and segments batch, ticking each off in
    ``state.json`` as soon as it is confirmed. The first failure stops the
    run (the rest waits for the next recovery). When every batch of the run
    is done, ``digested_through_lp`` / ``digest_runs`` advance, and the
    ``.jsonl`` is deleted once the visit is settled.

    A run freezes in its plan, besides the batching parameters, everything
    the requests render that the server fingerprints under a key: the
    ``language`` actually sent, the group speaker prefixes and the two peer
    display names; a resumed run sends exactly those. A resumed run whose
    batches no longer match the transcript (lines changed or lost after it
    opened) is marked ``abandoned`` and closed (the watermark advances, the
    visit can settle) instead of resending other lines under its keys. A
    visit with no transcript line at all (no file, or only its header) gets
    the terminal "abandoned" outcome (:meth:`VisitSpool.mark_no_transcript`),
    at finalize and in the startup recovery alike.

    ``family_names`` and ``local_char_names`` (the current names of the other
    local characters) are protected from peer display names: a peer named
    like one of them is stored under the generic peer label instead.
    """
    state = await spool.read_state()
    if state is None:
        return CommitResult(ok=True, skipped="no_state")
    if not is_digestable(state):
        return CommitResult(ok=True, skipped="memory_off")
    if state["finalized"] is None:
        # Never submit before finalize; organize visit regions once at finalization or crash backfill.
        return CommitResult(ok=False, skipped="not_finalized")
    if state["peer_uid"] is None:
        return CommitResult(ok=True, skipped="peer_forgotten")
    lock = peer_lock(state["own_char_uid"], state["peer_uid"])

    async def locked() -> CommitResult:
        async with lock:
            return await _commit_locked(spool, resolve_char_name=resolve_char_name,
                                        client=client, shutdown=shutdown, now=now,
                                        family_names=family_names,
                                        local_char_names=local_char_names)

    if shutdown:
        # One shutdown budget covers the entire commit: lock waiting, possibly behind an LLM summary call, and successive batches
        # share VISIT_SHUTDOWN_BUDGET_S, leaving remaining work for backfill on timeout.
        try:
            result = await asyncio.wait_for(locked(), VISIT_SHUTDOWN_BUDGET_S)
        except asyncio.TimeoutError:
            logger.warning("visit digest exceeded the shutdown budget; left for recovery")
            return CommitResult(ok=False, skipped="shutdown_budget")
    else:
        result = await locked()
    if result.ok:
        await spool.delete_if_settled()
    return result


async def _commit_locked(
    spool: VisitSpool,
    *,
    resolve_char_name: ResolveCharName,
    client: ScopedMemoryClient | None,
    shutdown: bool,
    now: float | None,
    family_names: Iterable[str] = (),
    local_char_names: Iterable[str] = (),
) -> CommitResult:
    # Reread under lock: clearing this person may erase peer identity while waiting.
    state = await spool.read_state()
    if state is None:
        return CommitResult(ok=True, skipped="no_state")
    if not is_digestable(state):
        return CommitResult(ok=True, skipped="memory_off")
    if state["peer_uid"] is None:
        return CommitResult(ok=True, skipped="peer_forgotten")
    if await memory_bridge.forget_in_progress(spool.config_dir, state["own_char_uid"], state["peer_uid"],
                                              own_uid=state["own_uid"]):
        # A partially completed clear, journaled for replay while memory_server is unavailable, already incremented the clear generation;
        # starting now bypasses server rejection and rewrites cleared memories. Wait for clear completion and peer-identity erasure.
        return CommitResult(ok=False, skipped="forget_in_progress")
    name = await resolve_char_name(state["own_char_uid"])
    if not name:
        return CommitResult(ok=True, skipped="character_deleted")
    contents = await spool.read_back()
    header = contents.header
    if header is None:
        if not contents.dropped_lines:
            # Memory enabled but finalized transcript missing/empty means no speech now or ever; persist abandoned terminal state,
            # or every startup retries forever and capacity reclamation cannot remove it. Both finalization/backfill reach here.
            # Invalid/wrong-visit headers with dropped_lines > 0 indicate corruption, not absence of speech.
            await spool.mark_no_transcript(now=time.time() if now is None else float(now))
        return CommitResult(ok=True, skipped="no_transcript")
    if not _header_matches_state(header, state):
        memory_bridge.diag("digest_header_mismatch", visit_id=spool.visit_id)
        return CommitResult(ok=False, skipped="header_mismatch")
    lang = header.get("lang")
    runs = {k: dict(v) for k, v in state["digest_writes"].items()}
    resume = bool(runs) and state["digest_runs"] == len(runs) - 1
    previous = state["digested_through_lp"]
    if resume:
        run = len(runs) - 1
        record = runs[str(run)]
        through = record["through_lp"]
    else:
        fresh = [line for line in contents.lines if line["lp"] > previous]
        if not fresh:
            if not runs and not contents.lines and not contents.dropped_lines:
                # Header-only transcripts after all appends failed or immediate closure use the same terminal state as missing transcripts.
                await spool.mark_no_transcript(now=time.time() if now is None else float(now))
            return CommitResult(ok=True, skipped="nothing_new")
        run = len(runs)
        through = max(line["lp"] for line in fresh)
    run_lines = [line for line in contents.lines if previous < line["lp"] <= through]
    # Resume with original batching parameters; upgraded total/per-batch limits must not change batch IDs or idempotency keys.
    plan = dict(record.get("plan") or {}) if resume else {}
    max_lines = int(plan.get("max_lines", VISIT_DIGEST_MAX_LINES))
    batch_size = int(plan.get("batch_size", SCOPED_HISTORY_BATCH_MAX_MESSAGES))
    if batch_size > SCOPED_HISTORY_BATCH_MAX_MESSAGES:
        # Original per-batch count exceeds the current uplink cap after upgrade: the old plan cannot be sent,
        # but rebatching under new keys conflicts with registered batches/server records. Avoid blocking every startup with exceptions;
        # record diagnostics and retain this round for age-based reclamation after 7 days.
        memory_bridge.diag("digest_plan_exceeds_wire_limit", visit_id=spool.visit_id, run=run,
                           batch_size=batch_size, limit=SCOPED_HISTORY_BATCH_MAX_MESSAGES)
        return CommitResult(ok=False, skipped="plan_exceeds_wire_limit", run=run)
    selected, dropped = select_digest_lines(run_lines, max_lines)
    group_batches, segment_batches = plan_digest_batches(selected, batch_size)
    subjects = _visit_subjects(state)
    membership = {
        "group": [_batch_fingerprint(batch) for batch in group_batches],
        "segments": [_batch_fingerprint(batch) for batch in segment_batches],
    }
    if resume:
        recorded = record.get("membership")
        if (
            len(record["group"]) != len(group_batches) or len(record["segments"]) != len(segment_batches)
            # Equal batch counts with shifted boundaries, e.g. unreadable discarded lines, make acknowledged batches miss newly included sentences
            # and pending batches reuse keys for other sentences. Verify each batch's original membership fingerprint.
            or (recorded is not None and recorded != membership)
        ):
            # Batching depends only on through_lp and line order; mismatches imply transcript edits, forbidding reuse of old keys for different sentences.
            # Terminal failure: missing transcript rows do not repair themselves and retries cannot succeed. Abandon this round and advance the watermark,
            # treating region_settled as settled; release transcripts normally instead of rereading/relogging on every startup for 7 days.
            memory_bridge.diag("digest_batches_mismatch", visit_id=spool.visit_id, run=run)
            record["abandoned"] = "batches_mismatch"
            await spool.update_state(digest_writes=runs, digested_through_lp=through, digest_runs=run + 1)
            return CommitResult(ok=True, skipped="batches_mismatch", run=run, dropped_lines=dropped)
        # Resume with frozen original request rendering: language, group speaker prefixes, and segment display names enter
        # server fingerprints. Recomputing after language/label/roster changes yields permanent 422 for same-key/different-body requests.
        # Legacy plans without these fields recompute using current rules, the same algorithm used when those prefield rounds started.
        language = plan["language"] if "language" in plan else _wire_language(lang)
        headers = plan.get("headers")
        if headers is None:
            headers = memory_bridge.group_speaker_headers(selected, lang)
        displays = plan.get("displays")
        if language is not None and not is_supported_language_code(language):
            # Original language code no longer supported: the client drops it, making the mismatched request body inevitably 422.
            # Handle like reduced batching caps: log diagnostics and retain the round for 7-day age reclamation.
            memory_bridge.diag("digest_plan_language_unsupported", visit_id=spool.visit_id, run=run)
            return CommitResult(ok=False, skipped="plan_language_unsupported", run=run)
    else:
        try:
            # Before opening a round, raise local clear generation to the server tombstone's current value; after cloud restore/device changes local counts restart at 0,
            # and the server silently discards whole rounds older than its tombstone.
            await memory_bridge.sync_forget_epochs(name, subjects, config_dir=spool.config_dir, client=client)
            epochs = await ForgetEpochs(spool.config_dir).get(subjects)
        except ForgetEpochsUnsynced as exc:
            memory_bridge.diag("forget_epochs_unsynced", error=str(exc))
            return CommitResult(ok=False, skipped="epochs_unsynced")
        except ForgetEpochsUnreadable as exc:
            memory_bridge.diag("forget_epochs_unreadable", error=str(exc))
            return CommitResult(ok=False, skipped="epochs_unreadable")
        language = _wire_language(lang)
        headers = memory_bridge.group_speaker_headers(selected, lang)
        displays = None
        if segment_batches:
            cat_display, human_display = await _peer_displays(
                spool.config_dir, state, name, lang, family_names, local_char_names,
            )
            displays = {"peer_cat": cat_display, "peer_human": human_display}
        plan = {"max_lines": max_lines, "batch_size": batch_size, "language": language, "headers": headers}
        if displays is not None:
            plan["displays"] = displays
        record = {
            "requested_at": time.time() if now is None else float(now),
            "through_lp": through,
            "group": {str(b): False for b in range(len(group_batches))},
            "segments": {str(b): False for b in range(len(segment_batches))},
            "epochs": epochs,
            # Freeze batching parameters and actual request rendering at round creation; reuse unchanged on resume.
            "plan": plan,
            "membership": membership,
        }
        runs[str(run)] = record
        # Persist through_lp / requested_at / every batch ID / clear generation before the first request; retries reuse them.
        await spool.update_state(digest_writes=runs)
        if dropped:
            memory_bridge.diag("digest_lines_capped", visit_id=spool.visit_id, dropped=dropped)
    epochs = dict(record.get("epochs") or {})
    requested_at = record["requested_at"]
    requests = 0
    group_subject = subjects[0]
    for b, batch in enumerate(group_batches):
        if record["group"][str(b)]:
            continue
        requests += 1
        ok = await memory_bridge.post_visit_digest(
            name, state["pair_id"], batch, subject=group_subject, lang=language,
            idempotency_key=digest_key(spool.visit_id, run, "group", b),
            client_requested_at=requested_at,
            subject_epochs=_epochs_for(epochs, [group_subject]),
            shutdown=shutdown, client=client, speaker_headers=headers,
        )
        if not ok:
            return CommitResult(ok=False, requests=requests, run=run, dropped_lines=dropped)
        record["group"][str(b)] = True
        await spool.update_state(digest_writes=runs)
    if segment_batches and displays is None:
        # Legacy plans lack frozen display names; compute from the current roster.
        cat_display, human_display = await _peer_displays(spool.config_dir, state, name, lang, family_names,
                                                          local_char_names)
        displays = {"peer_cat": cat_display, "peer_human": human_display}
    for b, batch in enumerate(segment_batches):
        if record["segments"][str(b)]:
            continue
        requests += 1
        ok = await memory_bridge.post_visit_segments(
            name, pair_id=state["pair_id"], own_uid=state["own_uid"], peer_uid=state["peer_uid"],
            peer_char_id=state["peer_char_id"], peer_cat_display=displays["peer_cat"],
            peer_human_display=displays["peer_human"], lines=batch,
            idempotency_key=digest_key(spool.visit_id, run, "segments", b),
            client_requested_at=requested_at,
            subject_epochs=_epochs_for(epochs, subjects[1:]), lang=language,
            shutdown=shutdown, client=client,
        )
        if not ok:
            return CommitResult(ok=False, requests=requests, run=run, dropped_lines=dropped)
        record["segments"][str(b)] = True
        await spool.update_state(digest_writes=runs)
    # All batches acknowledged: advance watermark in the same atomic write as the batch table.
    await spool.update_state(
        digest_writes=runs, digested_through_lp=through, digest_runs=run + 1,
    )
    return CommitResult(ok=True, requests=requests, run=run, dropped_lines=dropped)


def _wire_language(lang: Any) -> str | None:
    """The ``language`` a digest request actually carries: ``lang`` when it is a supported code."""
    return lang if is_supported_language_code(lang) else None


def _epochs_for(epochs: Mapping[str, int], subjects: Iterable[Mapping[str, str]]) -> dict[str, int]:
    out = {}
    for subject in subjects:
        key = f"{subject['subject_kind']}:{subject['subject_id']}"
        out[key] = int(epochs.get(key, 0))
    return out


# -- Previous-visit summary --

_SUMMARY_TASKS: dict[str, "asyncio.Future[Any]"] = {}


def track_last_summary(visit_id: str, awaitable: Awaitable[Any]) -> "asyncio.Future[Any]":
    """Register the in-flight last-summary commit of ``visit_id`` and return it as a future.

    :func:`last_summary_handoff` waits on registered futures instead of
    starting a second commit for the same visit.
    """
    future = asyncio.ensure_future(awaitable)
    _SUMMARY_TASKS[visit_id] = future

    def _forget(done: "asyncio.Future[Any]") -> None:
        if _SUMMARY_TASKS.get(visit_id) is done:
            _SUMMARY_TASKS.pop(visit_id, None)

    future.add_done_callback(_forget)
    return future


async def last_summary_handoff(
    config_dir: str | Path,
    *,
    own_uid: str,
    own_char_uid: str,
    peer_uid: str,
    start_summary: Callable[[VisitSpool], Awaitable[Any]] | None = None,
    is_live: Callable[[str], bool] | None = None,
    opening_visit_id: str | None = None,
) -> None:
    """Let the previous visits of this pair finish their last-visit summary.

    ``opening_visit_id`` is the visit being opened right now: it is never
    treated as a previous visit, whatever order the caller registers it as
    live in.

    For every local visit of ``(own_char_uid, pair)`` whose summary is not
    done and that is no longer running (finalized, or not live: a crash not
    recovered yet; never while this process still holds its transcript open
    for appends, the same rule startup recovery applies), wait for its
    registered commit or start one through
    ``start_summary`` (which goes through the visit background-task entry).
    Then take and release :func:`peer_lock`, so a commit holding it has
    finished. Callers bound the whole wait (``VISIT_LAST_SUMMARY_HANDOFF_S``);
    the commits themselves are shielded and keep running after a timeout.
    """
    from main_logic.visit.subjects import derive_pair_id

    pair_id = derive_pair_id(own_uid, peer_uid)
    try:
        # Permissive lookup: skip unreadable visits and return only confirmed matches for this pair; unrelated corruption must not
        # eliminate waiting for all previous visits of this pair.
        visit_ids = await VisitSpool.find_visits_for_pairs(config_dir, own_char_uid, [pair_id], strict=False)
    except Exception as exc:  # noqa: BLE001 - Scan failures shorten waiting without blocking opening.
        logger.warning("visit last-summary handoff: cannot scan spools: %s", exc)
        visit_ids = []
    waits: list[asyncio.Future[Any]] = []
    for visit_id in visit_ids:
        if visit_id == opening_visit_id:
            # The visit currently opening may not yet register as in flight; never misclassify it as crashed.
            continue
        running = _SUMMARY_TASKS.get(visit_id)
        if running is not None:
            waits.append(running)
            continue
        if start_summary is None or (is_live is not None and is_live(visit_id)):
            continue
        spool = VisitSpool(config_dir, visit_id)
        if is_spool_open(spool.jsonl_path):
            # Match backfill: if this process still has its writer open after is_live unregisters while appends/finalization queue,
            # marking crash would summarize partial transcripts and overwrite/reject real finalization later.
            continue
        try:
            state = await spool.read_state()
        except Exception:  # noqa: BLE001
            continue
        if state is None or state["last_summary_done"]:
            continue
        if state["finalized"] is None:
            if is_live is None:
                continue
            # Confirmed inactive but unfinalized visits crashed before backfill reached them. Mark crash as backfill does,
            # leaving chip restoration to backfill, then allow summary finalization gates; never summarize half an active transcript.
            try:
                await spool.update_state(finalized="crash")
            except Exception as exc:  # noqa: BLE001 - If marking fails, defer generation to backfill.
                logger.warning("visit last-summary handoff: cannot mark %s crashed: %r", visit_id, exc)
                continue
        waits.append(track_last_summary(visit_id, start_summary(spool)))
    if waits:
        await asyncio.gather(*(asyncio.shield(w) for w in waits), return_exceptions=True)
    async with peer_lock(own_char_uid, peer_uid):
        pass


def record_block_within_budget(lines: Sequence[Mapping[str, Any]], lang: str | None, budget: int) -> str:
    """``VISIT_RECORD_BLOCK`` of the newest whole lines whose rendering fits ``budget`` tokens (oldest first).

    Shared by the last-visit summary and the debrief summary: lines are taken
    from the newest backwards until the budget is used up; a single newest
    line that alone exceeds it is shortened instead of sent whole.
    """
    ordered = sorted(lines, key=line_order_key)
    rendered = [
        f"{get_visit_speaker_header(line['from'], lang)} {line.get('text') or ''}"
        for line in ordered
    ]
    allowance = budget
    while True:
        kept, _ = take_lines_within_token_budget(list(reversed(rendered)), max(allowance, 1))
        chosen = ordered[len(ordered) - len(kept):]
        block = build_visit_record_block([(line["from"], line.get("text") or "") for line in chosen], lang)
        excess = count_tokens(block) - budget
        if excess <= 0:
            return block
        if len(chosen) <= 1:
            if not chosen:
                return block
            # Only the latest sentence remains and exceeds budget: truncate it instead of sending oversized text whole,
            # which would repeatedly time out and retry each startup.
            line = chosen[0]
            text = line.get("text") or ""
            room = max(count_tokens(text) - excess - 8, 1)
            return build_visit_record_block([(line["from"], truncate_to_tokens(text, room))], lang)
        # Data-block delimiters/speaker groups add overhead beyond per-sentence budgets; tighten by the excess and reselect whole sentences newest first.
        allowance -= excess + 8


async def _retired(config_dir: Path, own_char_uid: str) -> bool:
    try:
        marker = await read_roster_marker(config_dir, "pending_retire")
    except RosterCorruptError:
        return True
    if not isinstance(marker, list):
        return False
    return any(isinstance(item, dict) and item.get("character_uid") == own_char_uid for item in marker)


async def _mark_summary_done(spool: VisitSpool, own_char_uid: str) -> bool:
    # Before last_summary_done, ensure the visit was not retired: missing state.json or retired-character markers prohibit writing/recreation.
    if await _retired(spool.config_dir, own_char_uid):
        return False
    try:
        await spool.update_state(last_summary_done=True)
    except FileNotFoundError:
        return False
    return True


async def commit_last_summary(
    spool: VisitSpool,
    roster: PeerRoster | None = None,
    *,
    llm: SummaryLLM,
    resolve_char_name: ResolveCharName,
    family_names: Iterable[str] = (),
) -> bool:
    """Generate and store the last-visit summary of one finished visit (roster only).

    Same timing, gate and lock as :func:`commit_visit_region`. With memory
    off, no transcript, no digestable line or a forgotten peer it stores
    nothing (an older summary stays) and marks ``last_summary_done``.
    Otherwise one LLM call (``VISIT_LAST_SUMMARY_INSTRUCTION``, timeout
    ``VISIT_LLM_TIMEOUT_S``) over a record block of at most
    ``VISIT_LAST_SUMMARY_INPUT_MAX_TOKENS`` taken whole-line from the newest
    line backwards, the peer's lines inside ``VISIT_PEER_LINES_BLOCK``; the
    output goes through ``strip_emotion_tags``, ``redact_outbound`` and
    ``truncate_to_tokens(VISIT_LAST_SUMMARY_MAX_TOKENS)``, and is dropped when
    it copies ``VISIT_PEER_NGRAM_N`` consecutive units of the peer. The text is
    written with :meth:`PeerRoster.set_last_summary` under the character's
    current name (looked up by ``own_char_uid``; a deleted character stores
    nothing and leaves ``state.json`` alone). Nothing goes to memory_server,
    private memory or any session.

    Returns True once the visit is handled (stored or decided not to store),
    False when the LLM failed (recovery retries; ``last_summary_done`` stays
    false).
    """
    state = await spool.read_state()
    if state is None or state["last_summary_done"]:
        return True
    if state["finalized"] is None:
        # Share commit_visit_region's gate: active transcripts are incomplete; generating and marking done now prevents
        # regeneration on finalization and gives the next visit a truncated summary.
        return False
    if not is_digestable(state) or state["peer_uid"] is None or state["pair_id"] is None:
        if await _mark_summary_done(spool, state["own_char_uid"]):
            # A post-clear summary may settle this visit; reclaim transcripts at the normal shared point.
            await spool.delete_if_settled()
        return True
    lock = peer_lock(state["own_char_uid"], state["peer_uid"])
    async with lock:
        handled = await _summary_locked(spool, roster, llm=llm, resolve_char_name=resolve_char_name,
                                        family_names=family_names)
    if handled:
        await spool.delete_if_settled()
    return handled


async def _summary_locked(
    spool: VisitSpool,
    roster: PeerRoster | None,
    *,
    llm: SummaryLLM,
    resolve_char_name: ResolveCharName,
    family_names: Iterable[str],
) -> bool:
    state = await spool.read_state()
    if state is None or state["last_summary_done"]:
        return True
    own_char_uid = state["own_char_uid"]
    if state["peer_uid"] is None or state["pair_id"] is None:
        # Clearing this person erased peer identity while waiting for the lock: do not save.
        await _mark_summary_done(spool, own_char_uid)
        return True
    if await memory_bridge.forget_in_progress(spool.config_dir, own_char_uid, state["peer_uid"],
                                              own_uid=state["own_uid"]):
        # Clear incomplete: summaries written before remove_char would later disappear, but if remove_char already
        # ran, writing recreates the entry's summary. Wait for clear completion.
        return False
    contents = await spool.read_back()
    lines = [line for line in contents.lines if str(line.get("text") or "").strip()]
    if contents.header is None or not lines:
        await _mark_summary_done(spool, own_char_uid)
        return True
    if not _header_matches_state(contents.header, state):
        # Transcript headers and state identify different visits, possibly another local character for the same account/peer;
        # never save that transcript's summary under state's character.
        memory_bridge.diag("summary_header_mismatch", visit_id=spool.visit_id)
        return False
    lang = contents.header.get("lang")
    own_char = await resolve_char_name(own_char_uid)
    if not own_char:
        # Character deleted: write neither roster nor state.json; retirement removes this visit.
        return True
    if roster is None:
        roster = PeerRoster(spool.config_dir, own_uid=state["own_uid"])
    elif roster.own_uid != state["own_uid"]:
        raise ValueError("roster belongs to another community account than this visit")
    try:
        # Before paying for LLM generation, read the roster entry with the same strict write rules: corrupt rosters necessarily raise
        # RosterCorruptError on write, so generating first would repeatedly bill every retained visit on each startup.
        entry = await roster.get_char_entry(state["peer_uid"], own_char, strict=True)
    except RosterCorruptError as exc:
        memory_bridge.diag("summary_roster_unreadable", visit_id=spool.visit_id, error=str(exc))
        return False
    pairs = entry.get("pairs") if entry is not None else None
    if not isinstance(pairs, list) or state["pair_id"] not in pairs:
        # set_last_summary writes only existing entries whose pairs include this pair, preventing late summaries undoing clears;
        # if unwritable now, generation wastes money. Mark done only, as with False write returns.
        await _mark_summary_done(spool, own_char_uid)
        return True
    existing = entry.get("last_summary")
    if isinstance(existing, dict) and existing.get("visit_id") == spool.visit_id:
        # A previous attempt wrote the roster summary but died before done; avoid another billed LLM call,
        # which would replace the committed summary at the same ended_at, and merely record done.
        await _mark_summary_done(spool, own_char_uid)
        return True
    block = await asyncio.to_thread(
        record_block_within_budget, lines, lang, VISIT_LAST_SUMMARY_INPUT_MAX_TOKENS,
    )
    prompt = build_visit_last_summary_prompt(own_char, block, lang)
    try:
        raw = await asyncio.wait_for(llm(prompt), VISIT_LLM_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - Any failure skips this generation; backfill retries.
        logger.warning("visit last summary generation failed for %s: %r", spool.visit_id, exc)
        return False
    text = strip_emotion_tags(str(raw or ""))
    text = redact_outbound(text, family_names=list(family_names),
                           replacement=get_family_neutral_term(lang))
    text = (await asyncio.to_thread(truncate_to_tokens, text, VISIT_LAST_SUMMARY_MAX_TOKENS)).strip()
    peer_texts = [str(line["text"]) for line in lines if line["from"] in _PEER_SPEAKERS]
    try:
        # Scan all transcript text in workers because peer lines may be numerous; do not block the loop.
        await asyncio.to_thread(assert_no_peer_ngram, text, peer_texts, n=VISIT_PEER_NGRAM_N)
    except PeerNgramHit:
        memory_bridge.diag("last_summary_peer_ngram", visit_id=spool.visit_id)
        text = ""
    if text:
        # Use the final canonically ordered line's timestamp, not the maximum: clock correction can leave a future timestamp,
        # which roster ordering would keep above every later visit's summary forever.
        ended_at = float(max(contents.lines, key=line_order_key)["ts"])
        await roster.set_last_summary(
            state["peer_uid"], own_char, visit_id=spool.visit_id, ended_at=ended_at,
            text=text, pair_id=state["pair_id"],
        )
    await _mark_summary_done(spool, own_char_uid)
    return True

