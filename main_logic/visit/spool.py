# -*- coding: utf-8 -*-
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

"""Per-visit line spool and its ``state.json`` (OD-17 v2).

Design: ``docs/design/visit-infrastructure.md`` section 3.7.3 and PR-06
``spool.py``.

Layout, all under ``config_dir/visit_spool/`` (shared with the outbox)::

    <visit_id>.jsonl          header line + one line per spoken line
                              (only when visit memory is on for this visit)
    <visit_id>.state.json     canonical state, every visit, no transcript text
    <visit_id>.upload.json    pending transcript upload (never touched here
    <visit_id>.upload.jsonl   except by the seven-day age rule of ``sweep``)
    <visit_id>.outbox.jsonl   reliable outbox (owned by ``outbox.py``)

Steam cloud save only syncs ``MANAGED_MEMORY_FILENAMES``
(``utils/cloudsave_runtime/snapshots.py``), so nothing here is ever synced.

Writer model: each open :class:`VisitSpool` owns one dedicated writer thread
(a single-worker executor) and one ``O_APPEND`` file descriptor. ``append``
encodes and size-checks the line on the caller's thread, then submits one
``os.write`` of the whole encoded line to that thread. Submission happens
before the coroutine first suspends, so lines land in call order even when
callers do not await each other. The fd is ``fsync``-ed when
:meth:`VisitSpool.fsync_due` says so (every ``VISIT_SPOOL_FSYNC_S``) and once
on :meth:`VisitSpool.close`; a process crash loses nothing (page cache), a
power cut at most one fsync interval. The interface is plain data: dicts in,
dicts out, no event-loop objects are kept.

``state.json`` is written with ``atomic_write_json`` in a worker thread and is
validated against the canonical schema on every read and write. Files are
created owner-only (``0o600``; no effect on Windows, same stance as the
credential files).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import json
import math
import os
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.visit_settings import (
    VISIT_LP_MAX,
    VISIT_SPOOL_DIR_CAP_BYTES,
    VISIT_SPOOL_DIRNAME,
    VISIT_SPOOL_FSYNC_S,
    VISIT_SPOOL_LINE_MAX_BYTES,
    VISIT_SPOOL_RETENTION_DAYS,
    VISIT_TEXT_MAX_BYTES,
)
from main_logic.visit.subjects import derive_pair_id, derive_peer_char_id, path_lock
from utils.file_utils import atomic_write_bytes, atomic_write_json
from utils.logger_config import get_module_logger
from utils.visit_wire import VISIT_ID_RE, require_visit_id, visit_path

logger = get_module_logger(__name__, "Main")

SPOOL_SUFFIX = ".jsonl"
STATE_SUFFIX = ".state.json"
UPLOAD_JSON_SUFFIX = ".upload.json"
UPLOAD_JSONL_SUFFIX = ".upload.jsonl"
OUTBOX_SUFFIX = ".outbox.jsonl"
_KNOWN_SUFFIXES = (
    SPOOL_SUFFIX,
    STATE_SUFFIX,
    UPLOAD_JSON_SUFFIX,
    UPLOAD_JSONL_SUFFIX,
    OUTBOX_SUFFIX,
)
_UPLOAD_SUFFIXES = (UPLOAD_JSON_SUFFIX, UPLOAD_JSONL_SUFFIX)
_VISIT_ID_LEN = 22
_RETENTION_S = VISIT_SPOOL_RETENTION_DAYS * 86400
_O_BINARY = getattr(os, "O_BINARY", 0)

HEADER_FIELDS = (
    "v",
    "visit_id",
    "role",
    "own_uid",
    "own_char",
    "own_char_uid",
    "pair_id",
    "peer_uid",
    "peer_char_id",
    "peer_char_tag",
    "started_at",
    "lang",
)
LINE_REQUIRED_FIELDS = ("lp", "side", "ts", "from", "text")
LINE_OPTIONAL_FIELDS = ("ln", "truncated")
LINE_SPEAKERS = ("own_cat", "peer_cat", "peer_human", "own_human")
_PEER_IDENTITY_FIELDS = ("peer_uid", "pair_id", "peer_char_id")
# Header also includes the peer's stable character label, absent from state.json; clear it when forgetting that person.
_HEADER_PEER_FIELDS = _PEER_IDENTITY_FIELDS + ("peer_char_tag",)

DEBRIEF_CHOICES = (
    None,
    "ask_later",
    "generating:diary",
    "preview:diary",
    "committing:diary",
    "commit_failed:diary",
    "diary",
    "forget",
    "abandoned",
)
# Diary commit in progress / permanent failure awaiting users: state.json debrief_pending and write progress
# are the sole retry basis; neither cleanup removes it. JSONL is not exempt because retries need state.json only.
_COMMIT_PINNED_CHOICES = ("committing:diary", "commit_failed:diary")
_DEBRIEF_WRITE_FLAGS = (
    "facts", "cache", "facts_unconfirmed", "cache_unconfirmed", "facts_inflight", "cache_inflight",
)
# Allowed source states for mark_forget's two terminal outcomes; repeated same-outcome marking is idempotent.
_FORGET_SOURCES = {
    "forget": (None, "ask_later", "generating:diary", "preview:diary", "forget"),
    "abandoned": ("commit_failed:diary", "abandoned"),
}
# Terminal digest abandonment reasons in digest_writes[run].abandoned; region_settled counts as settled.
# batches_mismatch means transcript changed after round start; no_transcript means memory enabled but empty transcript after closure.
DIGEST_ABANDON_REASONS = ("batches_mismatch", "no_transcript")
# Allowed digest_writes[run].plan fields: batching parameters and request rendering frozen at round start.
_PLAN_FIELDS = frozenset({"max_lines", "batch_size", "language", "headers", "displays"})
STATE_FIELDS = frozenset({
    "visit_id",
    "own_uid",
    "own_char",
    "own_char_uid",
    "pair_id",
    "peer_uid",
    "peer_char_id",
    "digested_through_lp",
    "digest_runs",
    "finalized",
    "debrief_choice",
    "debrief_pending",
    "debrief_writes",
    "debrief_retry",
    "debrief_commit_error",
    "debrief_chip_pending",
    "last_summary_done",
    "memory_enabled",
    "digest_writes",
})


class SpoolLineTooLarge(ValueError):
    """Raised when one encoded spool line exceeds ``VISIT_SPOOL_LINE_MAX_BYTES``."""


class SpoolStateUnreadable(RuntimeError):
    """One or more ``state.json`` files exist but cannot be read (forget paths fail closed)."""

    def __init__(self, visit_ids: list[str]) -> None:
        self.visit_ids = list(visit_ids)
        super().__init__(f"unreadable visit state: {', '.join(self.visit_ids)}")


class _NotApplied(Exception):
    """Raised inside a state mutation to abort it without writing; carries the state it saw (``update_state_if``)."""

    def __init__(self, state: dict) -> None:
        super().__init__("debrief_choice moved on")
        self.state = state


class SpoolBusy(RuntimeError):
    """Raised when a header rewrite targets a spool that is still open for appends."""


# Process-level active-write registration: visits hold O_APPEND JSONL descriptors; replacing whole files to rewrite headers then
# makes later appends target replaced old inodes, and replacement itself can fail on Windows. Clearing/renaming
# active visits raises SpoolBusy, retaining unfinished undo-log steps for replay after the visit ends.
_OPEN_SPOOLS: set[str] = set()
_OPEN_SPOOLS_LOCK = threading.Lock()


def _spool_key(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def is_spool_open(path: Path) -> bool:
    """True while some ``VisitSpool`` in this process holds ``path`` open for appends."""
    with _OPEN_SPOOLS_LOCK:
        return _spool_key(path) in _OPEN_SPOOLS


class SpoolStateError(ValueError):
    """Raised when a ``state.json`` document violates the canonical schema."""


class SpoolStateCorrupt(SpoolStateError):
    """``state.json`` content no version can use: not JSON / not UTF-8, nested too deep, or not an object.

    A document that parses into an object but fails :func:`validate_state`
    (e.g. written by a newer version) raises the plain
    :class:`SpoolStateError` instead and must never be treated as corrupt.
    """


# -- Encoding and validation, pure functions --


def _encode_line(obj: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        # isfinite raises on integers beyond floating-point range; treat them as nonnumbers and corrupt state,
        # never letting uncaught exceptions abort whole backfill/reupload rounds.
        return False


def validate_header(header: Mapping[str, Any]) -> dict:
    """Check a spool header line and return a plain dict copy of it."""
    if not isinstance(header, Mapping):
        raise ValueError("spool header must be a mapping")
    keys = set(header)
    if keys != set(HEADER_FIELDS):
        raise ValueError(
            "spool header fields mismatch: missing=%s extra=%s"
            % (sorted(set(HEADER_FIELDS) - keys), sorted(keys - set(HEADER_FIELDS)))
        )
    if type(header["v"]) is not int or header["v"] != 1:
        # true/1.0 compare equal to 1; check types and accept integer 1 only.
        raise ValueError("spool header v must be the integer 1")
    require_visit_id(header["visit_id"])
    if header["role"] not in ("host", "guest"):
        raise ValueError("spool header role must be host or guest")
    for name in ("own_uid", "own_char", "own_char_uid"):
        if not isinstance(header[name], str) or not header[name]:
            raise ValueError(f"spool header {name} must be a non-empty string")
    for name in _HEADER_PEER_FIELDS:
        value = header[name]
        # Forget-person may clear peer fields; otherwise require nonempty strings, never interpreting corrupt types as another pair.
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError(f"spool header {name} must be a non-empty string or null")
    if len({header[name] is None for name in _HEADER_PEER_FIELDS}) > 1:
        # Forget-person clears all peer fields together. Partial identity indicates interrupted wiping/corruption;
        # pair lookup misses it, permanently retaining the remaining peer_uid.
        raise ValueError("spool header peer fields must be all set or all null")
    if header["pair_id"] is not None and header["pair_id"] != derive_pair_id(
        header["own_uid"], header["peer_uid"]
    ):
        # pair_id must match (own_uid,peer_uid), or pair-based clearing cannot find it.
        raise ValueError("spool header pair_id does not match own_uid / peer_uid")
    if header["peer_char_id"] is not None and header["peer_char_id"] != derive_peer_char_id(
        header["peer_uid"], header["peer_char_tag"]
    ):
        # Peer cat IDs must derive from peer_uid/peer_char_tag; incorrect IDs enter tagless
        # state.json, sending backfill/debrief digests and recall to another cat's subject beyond roster clearing.
        raise ValueError("spool header peer_char_id does not match peer_uid / peer_char_tag")
    if not _is_number(header["started_at"]):
        raise ValueError("spool header started_at must be a number")
    return dict(header)


def encode_spool_line(line: Mapping[str, Any]) -> bytes:
    """Validate one spoken line and return its encoded JSONL bytes.

    Required fields: ``lp`` (int), ``side`` (str), ``ts`` (number), ``from``
    (one of ``LINE_SPEAKERS``), ``text`` (str, at most ``VISIT_TEXT_MAX_BYTES``
    UTF-8 bytes, already sanitized by the caller). Optional: ``ln`` (str) and
    ``truncated`` (bool). The encoded line, newline included, must fit in
    ``VISIT_SPOOL_LINE_MAX_BYTES``; an oversized line raises
    :class:`SpoolLineTooLarge` and is never truncated.
    """
    if not isinstance(line, Mapping):
        raise ValueError("spool line must be a mapping")
    keys = set(line)
    missing = set(LINE_REQUIRED_FIELDS) - keys
    extra = keys - set(LINE_REQUIRED_FIELDS) - set(LINE_OPTIONAL_FIELDS)
    if missing or extra:
        raise ValueError(
            "spool line fields mismatch: missing=%s extra=%s" % (sorted(missing), sorted(extra))
        )
    if not _is_int(line["lp"]) or not 0 <= line["lp"] <= VISIT_LP_MAX:
        # Match wire/room ranges; out-of-range lp corrupts backfill/upload ordering.
        raise ValueError("spool line lp must be an int in 0..VISIT_LP_MAX")
    if line["side"] not in ("host", "guest"):
        # Only two protocol roles exist; other strings misattribute backfill and fail upload schemas.
        raise ValueError("spool line side must be host or guest")
    if not _is_number(line["ts"]):
        raise ValueError("spool line ts must be a number")
    if line["from"] not in LINE_SPEAKERS:
        raise ValueError(f"spool line from must be one of {LINE_SPEAKERS}")
    text = line["text"]
    if not isinstance(text, str):
        raise ValueError("spool line text must be a string")
    if len(text.encode("utf-8")) > VISIT_TEXT_MAX_BYTES:
        raise ValueError("spool line text exceeds VISIT_TEXT_MAX_BYTES")
    if "ln" in line and not isinstance(line["ln"], str):
        raise ValueError("spool line ln must be a string")
    if "truncated" in line and not isinstance(line["truncated"], bool):
        raise ValueError("spool line truncated must be a bool")
    data = _encode_line(line)
    if len(data) > VISIT_SPOOL_LINE_MAX_BYTES:
        raise SpoolLineTooLarge(
            f"encoded spool line is {len(data)} bytes > {VISIT_SPOOL_LINE_MAX_BYTES}"
        )
    return data


def new_debrief_writes() -> dict:
    """Return the initial ``debrief_writes`` progress record (nothing written, nothing in flight)."""
    record: dict[str, Any] = {name: False for name in _DEBRIEF_WRITE_FLAGS}
    record["facts_written"] = 0
    return record


def new_state(
    *,
    own_uid: str,
    own_char: str,
    own_char_uid: str,
    pair_id: str,
    peer_uid: str,
    peer_char_id: str,
    memory_enabled: bool,
    visit_id: str | None = None,
) -> dict:
    """Return a fresh canonical ``state.json`` document for one visit.

    The peer fields are required: the state is written when the visit
    activates, once the peer is known. A ``None`` peer field in a state on
    disk therefore always means "forget this person" erased it.

    ``visit_id`` may be left ``None``: :meth:`VisitSpool.write_state` binds
    the document to its own visit when writing it.

    ``memory_enabled`` is the ``visitMemoryEnabled`` value read once when the
    visit activates; it stays fixed for the whole visit. ``own_uid`` is this
    side's verified ``visit_uid`` (the community account the visit ran under):
    visit data is partitioned by account, and crash recovery needs it to
    derive the person-level subject even after the user switched accounts.
    It is not a peer field and survives "forget this person".
    """
    state = {
        "visit_id": visit_id,
        "own_uid": own_uid,
        "own_char": own_char,
        "own_char_uid": own_char_uid,
        "pair_id": pair_id,
        "peer_uid": peer_uid,
        "peer_char_id": peer_char_id,
        "digested_through_lp": -1,
        "digest_runs": 0,
        "finalized": None,
        "debrief_choice": None,
        "debrief_pending": None,
        "debrief_writes": new_debrief_writes(),
        "debrief_retry": None,
        "debrief_commit_error": None,
        "debrief_chip_pending": False,
        "last_summary_done": False,
        "memory_enabled": memory_enabled,
        "digest_writes": {},
    }
    for name in ("pair_id", "peer_uid", "peer_char_id"):
        # New state always includes peers; None peer fields mean forget-person cleared them,
        # enabling debrief invalidation to recognize visits without confusing never-bound visits.
        if not isinstance(state[name], str) or not state[name]:
            raise SpoolStateError(f"new state needs a bound peer ({name})")
    return validate_state(state)


def _check_batch_map(value: Any, where: str) -> None:
    if not isinstance(value, dict):
        raise SpoolStateError(f"{where} must be an object")
    # Writers build contiguous 0..N-1 batch tables. Gaps ({"1":true}) or noncanonical 01 keys indicate lost batches;
    # checking only remaining values would delete transcripts before missing batches were acknowledged.
    if set(value) != {str(i) for i in range(len(value))}:
        raise SpoolStateError(f"{where} keys must be the batch numbers 0..N-1")
    for key, done in value.items():
        if not isinstance(done, bool):
            raise SpoolStateError(f"{where}[{key}] must be a bool")


def validate_state(state: Any, *, visit_id: str | None = None) -> dict:
    """Check ``state`` against the canonical schema and return a deep copy.

    The field set must match ``STATE_FIELDS`` exactly. ``debrief_choice`` is
    restricted to ``DEBRIEF_CHOICES``; ``preview:diary``,
    ``committing:diary`` and ``commit_failed:diary`` require a non-empty
    ``debrief_pending`` (it is persisted before any of them is entered),
    ``commit_failed:diary`` also a ``debrief_commit_error``, and
    ``generating:diary`` an empty one. ``debrief_writes`` carries the
    two-step progress (``facts`` / ``cache`` done, ``facts_written``,
    ``*_unconfirmed``, ``*_inflight``); ``debrief_retry`` is null or
    ``{attempts, next_at}``; ``debrief_commit_error`` is null or
    ``{step, status, at, seq}``. Raises :class:`SpoolStateError`.

    ``visit_id`` is ``None`` only in a document not yet written; with the
    ``visit_id`` argument (every read and write) it must equal it.
    """
    if not isinstance(state, Mapping):
        raise SpoolStateError("state must be an object")
    keys = set(state)
    if keys != STATE_FIELDS:
        raise SpoolStateError(
            "state fields mismatch: missing=%s extra=%s"
            % (sorted(STATE_FIELDS - keys), sorted(keys - STATE_FIELDS))
        )
    stored = state["visit_id"]
    if stored is not None:
        try:
            require_visit_id(stored)
        except ValueError as exc:
            raise SpoolStateError("state visit_id is malformed") from exc
    if visit_id is not None and stored != visit_id:
        # Replaced/copied state.json must not assign another visit's ownership to this visit's files.
        raise SpoolStateError("state.json belongs to another visit")
    for name in ("own_uid", "own_char", "own_char_uid"):
        if not isinstance(state[name], str) or not state[name]:
            raise SpoolStateError(f"{name} must be a non-empty string")
    for name in _PEER_IDENTITY_FIELDS:
        value = state[name]
        if value is not None and (not isinstance(value, str) or not value):
            raise SpoolStateError(f"{name} must be a non-empty string or null")
    if len({state[name] is None for name in _PEER_IDENTITY_FIELDS}) > 1:
        # Like headers, partially cleared identities evade pair lookup and leave peer_uid on disk forever.
        raise SpoolStateError("peer_uid / pair_id / peer_char_id must be all set or all null")
    if state["pair_id"] is not None and state["pair_id"] != derive_pair_id(
        state["own_uid"], state["peer_uid"]
    ):
        # Like headers, mismatched pair IDs make forget-person miss the visit.
        raise SpoolStateError("pair_id does not match own_uid / peer_uid")
    if not _is_int(state["digested_through_lp"]) or state["digested_through_lp"] < -1:
        raise SpoolStateError("digested_through_lp must be an int >= -1")
    if not _is_int(state["digest_runs"]) or state["digest_runs"] < 0:
        raise SpoolStateError("digest_runs must be an int >= 0")
    finalized = state["finalized"]
    if finalized is not None and (not isinstance(finalized, str) or not finalized):
        raise SpoolStateError("finalized must be null or a reason string")
    choice = state["debrief_choice"]
    if choice not in DEBRIEF_CHOICES:
        raise SpoolStateError(f"debrief_choice {choice!r} is not allowed")
    pending = state["debrief_pending"]
    if pending is not None:
        if not isinstance(pending, Mapping) or set(pending) != {"diary", "facts"}:
            raise SpoolStateError("debrief_pending must be null or {diary, facts}")
        if not isinstance(pending["diary"], str):
            raise SpoolStateError("debrief_pending.diary must be a string")
        facts = pending["facts"]
        if not isinstance(facts, list) or not all(isinstance(f, str) for f in facts):
            raise SpoolStateError("debrief_pending.facts must be a list of strings")
    pending_empty = pending is None or (not pending["diary"] and not pending["facts"])
    if choice in ("preview:diary",) + _COMMIT_PINNED_CHOICES and pending_empty:
        raise SpoolStateError(f"{choice} requires a persisted debrief_pending")
    if choice == "generating:diary" and pending is not None:
        raise SpoolStateError("generating:diary requires an empty debrief_pending")
    writes_raw = state["debrief_writes"]
    if choice == "diary" and not (
        isinstance(writes_raw, Mapping) and writes_raw.get("facts") is True
        and writes_raw.get("cache") is True
    ):
        # Diary commit requires two successful steps; partial diary state otherwise looks settled and loses transcripts,
        # while startup backfill never completes the missing step.
        raise SpoolStateError("diary requires both debrief writes to be done")
    writes = state["debrief_writes"]
    if (
        not isinstance(writes, Mapping)
        or set(writes) != set(_DEBRIEF_WRITE_FLAGS) | {"facts_written"}
        or not all(isinstance(writes[name], bool) for name in _DEBRIEF_WRITE_FLAGS)
        or not _is_int(writes["facts_written"]) or writes["facts_written"] < 0
    ):
        raise SpoolStateError(
            "debrief_writes must be {facts, cache, *_unconfirmed, *_inflight: bool, facts_written: int >= 0}"
        )
    retry = state["debrief_retry"]
    if retry is not None and not (
        isinstance(retry, Mapping) and set(retry) == {"attempts", "next_at"}
        and _is_int(retry["attempts"]) and retry["attempts"] >= 0 and _is_number(retry["next_at"])
    ):
        # Persist attempt counts and next-retry times so restarts preserve backoff.
        raise SpoolStateError("debrief_retry must be null or {attempts: int >= 0, next_at: number}")
    error = state["debrief_commit_error"]
    if error is not None and not (
        isinstance(error, Mapping) and set(error) == {"step", "status", "at", "seq"}
        and error["step"] in ("facts", "cache") and _is_int(error["status"])
        and _is_number(error["at"]) and _is_int(error["seq"]) and error["seq"] >= 1
    ):
        raise SpoolStateError(
            "debrief_commit_error must be null or {step: facts|cache, status: int, at: number, seq: int >= 1}"
        )
    if choice == "commit_failed:diary" and error is None:
        # Failed-write blocks need this to identify failed steps and replace blocks by seq; without it failure blocks cannot replay.
        raise SpoolStateError("commit_failed:diary requires a debrief_commit_error")
    for name in ("debrief_chip_pending", "last_summary_done", "memory_enabled"):
        if not isinstance(state[name], bool):
            raise SpoolStateError(f"{name} must be a bool")
    runs = state["digest_writes"]
    if not isinstance(runs, Mapping):
        raise SpoolStateError("digest_writes must be an object")
    # Runs register as 0..N-1; digest_runs advances only after all batches finish, so the last run may remain active.
    # Missing runs ({"1":...}) let settlement inspect remaining runs and delete transcripts before absent runs complete.
    if set(runs) != {str(i) for i in range(len(runs))}:
        raise SpoolStateError("digest_writes keys must be the run numbers 0..N-1")
    if state["digest_runs"] not in (len(runs), len(runs) - 1):
        raise SpoolStateError("digest_runs does not match the registered digest_writes runs")
    for run, record in runs.items():
        if not isinstance(record, Mapping) or set(record) - {"epochs", "plan", "membership", "abandoned"} != {
            "requested_at", "through_lp", "group", "segments",
        }:
            raise SpoolStateError(
                f"digest_writes[{run}] must be {{requested_at, through_lp, group, segments"
                f"[, epochs, plan, membership, abandoned]}}"
            )
        # Terminal abandonment: transcript membership changed after run start; remaining batches can no longer use old keys.
        if "abandoned" in record and record["abandoned"] not in DIGEST_ABANDON_REASONS:
            raise SpoolStateError(f"digest_writes[{run}].abandoned must be one of {DIGEST_ABANDON_REASONS}")
        membership = record.get("membership")
        # Freeze batch-membership fingerprints at run start; resumed checks detect shifted boundaries even with unchanged batch counts after unreadable lines.
        if membership is not None and not (
            isinstance(membership, Mapping) and set(membership) == {"group", "segments"}
            and all(
                isinstance(membership[part], list)
                and isinstance(record[part], Mapping) and len(membership[part]) == len(record[part])
                and all(isinstance(value, str) and value for value in membership[part])
                for part in ("group", "segments")
            )
        ):
            raise SpoolStateError(
                f"digest_writes[{run}].membership must be {{group, segments}} lists matching the batch counts"
            )
        plan = record.get("plan", {})
        # Freeze sentence limits/batch sizes at run start so upgrades changing constants still resume the original plan.
        # Also freeze rendered requests: actual language, speaker prefixes in groups, and both segments'
        # peer display names. They enter server fingerprints; recalculating after upgrades causes permanent same-key/different-body 422.
        if not isinstance(plan, Mapping) or set(plan) - _PLAN_FIELDS or not all(
            _is_int(plan[name]) and plan[name] >= 1 for name in ("max_lines", "batch_size") if name in plan
        ):
            raise SpoolStateError(f"digest_writes[{run}].plan must be {{max_lines, batch_size}} ints >= 1")
        language = plan.get("language")
        if language is not None and not (isinstance(language, str) and language):
            raise SpoolStateError(f"digest_writes[{run}].plan.language must be a non-empty string or null")
        for name in ("headers", "displays"):
            if name in plan and not (
                isinstance(plan[name], Mapping)
                and all(isinstance(k, str) and k and isinstance(v, str) for k, v in plan[name].items())
            ):
                raise SpoolStateError(f"digest_writes[{run}].plan.{name} must map speakers to strings")
        epochs = record.get("epochs", {})
        # Record subject-forget generations at run start and reuse on keyed retries; servers discard outputs initiated before clearing.
        if not isinstance(epochs, Mapping) or not all(
            isinstance(key, str) and key and _is_int(value) and value >= 0
            for key, value in epochs.items()
        ):
            raise SpoolStateError(f"digest_writes[{run}].epochs must map subject keys to ints >= 0")
        if not _is_number(record["requested_at"]):
            raise SpoolStateError(f"digest_writes[{run}].requested_at must be a number")
        if not _is_int(record["through_lp"]) or not 0 <= record["through_lp"] <= VISIT_LP_MAX:
            # No legal row reaches invalid watermarks. digested_through_lp must equal some run's through_lp
            # or -1, consequently limiting it to -1..VISIT_LP_MAX.
            raise SpoolStateError(f"digest_writes[{run}].through_lp must be an int in 0..VISIT_LP_MAX")
        _check_batch_map(record["group"], f"digest_writes[{run}].group")
        _check_batch_map(record["segments"], f"digest_writes[{run}].segments")
    # Every run starts after the prior watermark, with strictly increasing through_lp; digested_through_lp advances only when
    # all its batches complete. Inconsistent watermarks split settlement/backfill conclusions: recovery believes
    # extraction reached 999 without any run covering those sentences, yet transcripts are deleted.
    previous = -1
    for i in range(len(runs)):
        through = runs[str(i)]["through_lp"]
        if through <= previous:
            raise SpoolStateError("digest_writes through_lp must increase run by run")
        previous = through
    completed = state["digest_runs"]
    expected = runs[str(completed - 1)]["through_lp"] if completed else -1
    if state["digested_through_lp"] != expected:
        raise SpoolStateError("digested_through_lp does not match the last completed digest run")
    return copy.deepcopy(dict(state))


def is_digestable(state: Mapping[str, Any]) -> bool:
    """Whether this visit's lines may be digested into the visit memory region.

    Exactly ``state.json.memory_enabled``: the ``visitMemoryEnabled`` value
    frozen when the visit activated. The current configuration is never
    consulted, so a mid-visit change only affects the next visit.
    """
    return state.get("memory_enabled") is True


def region_settled(state: Mapping[str, Any]) -> bool:
    """Whether the visit-region digest and the last-visit summary are both done.

    With memory on, at least one digest run must be registered, every
    registered run must be counted in ``digest_runs`` (a run still in
    progress is not settled), and every run must have at least one group and
    one segments batch, all complete. An empty batch map means the batches
    are not registered yet, not that they are done. A run marked
    ``abandoned`` (its remaining batches can never be sent with their keys)
    counts as settled.
    """
    if state.get("last_summary_done") is not True:
        return False
    if not is_digestable(state):
        return True
    runs = state.get("digest_writes") or {}
    if not runs or len(runs) != state.get("digest_runs"):
        return False
    for record in runs.values():
        if record.get("abandoned"):
            # Terminally abandoned runs cannot succeed on retry; count settled and release transcripts rather than retrying uselessly at every startup.
            continue
        for part in ("group", "segments"):
            batches = record.get(part) or {}
            # Empty tables mean batches not registered, not all complete. Writers register runs before batches;
            # choosing do-not-record between steps must not delete unextracted transcripts.
            if not batches or not all(batches.values()):
                return False
    return True


def debrief_final(state: Mapping[str, Any]) -> bool:
    """Whether the debrief reached a final outcome (or never applies: memory off).

    Final means ``diary`` with both writes done, ``forget`` or ``abandoned``.
    ``committing:diary`` / ``commit_failed:diary`` are *not* final: a retry is
    pending or the user still has to choose.
    """
    if not is_digestable(state):
        return True
    choice = state.get("debrief_choice")
    if choice == "diary":
        writes = state.get("debrief_writes") or {}
        return writes.get("facts") is True and writes.get("cache") is True
    return choice in ("forget", "abandoned")


def debrief_releases_transcript(state: Mapping[str, Any]) -> bool:
    """Whether the debrief no longer needs the ``.jsonl``.

    True once :func:`debrief_final`, and also in ``committing:diary`` /
    ``commit_failed:diary``: from then on retries only use
    ``state.json.debrief_pending``, which stays pinned (see
    :func:`debrief_pins_state`).
    """
    return debrief_final(state) or debrief_pins_state(state)


def debrief_pins_state(state: Mapping[str, Any] | None) -> bool:
    """Whether ``state.json`` must survive both sweeps: a diary commit is in flight or failed."""
    return bool(state) and state.get("debrief_choice") in _COMMIT_PINNED_CHOICES


def transcript_releasable(state: Mapping[str, Any]) -> bool:
    """Region settled and the debrief done with the transcript: the ``.jsonl`` may be reclaimed.

    Not "the visit is over": a visit still committing (or failed) its diary
    is releasable while :func:`debrief_final` is False and
    :func:`debrief_pins_state` keeps its ``state.json``.
    """
    return region_settled(state) and debrief_releases_transcript(state)


@dataclass
class SpoolContents:
    """What a spool replay recovered: the header, the complete lines, the drops."""

    header: dict | None
    lines: list[dict] = field(default_factory=list)
    dropped_lines: int = 0


# -- File helpers, worker-thread execution --


def _spool_dir(config_dir: str | Path) -> Path:
    return Path(config_dir) / VISIT_SPOOL_DIRNAME


def _split_name(name: str) -> tuple[str, str] | None:
    """Return ``(visit_id, suffix)`` for a spool directory file name, else ``None``."""
    visit_id, suffix = name[:_VISIT_ID_LEN], name[_VISIT_ID_LEN:]
    if suffix not in _KNOWN_SUFFIXES or not VISIT_ID_RE.fullmatch(visit_id):
        return None
    return visit_id, suffix


def _list_names(spool_dir: Path) -> list[tuple[str, str, Path]]:
    """``(visit_id, suffix, path)`` of every spool-shaped name, without touching the files."""
    try:
        names = os.listdir(spool_dir)
    except FileNotFoundError:
        return []
    out = []
    for name in names:
        parsed = _split_name(name)
        if parsed is not None:
            out.append((parsed[0], parsed[1], spool_dir / name))
    return out


def _scan(spool_dir: Path) -> list[tuple[str, str, Path, os.stat_result]]:
    """:func:`_list_names` plus ``stat``; an entry whose ``stat`` fails is logged and skipped."""
    out = []
    for visit_id, suffix, path in _list_names(spool_dir):
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            # Skip only individual symlink-loop/permission failures; a permanently bad entry must not block
            # entire sweeps and outbox cleanup.
            logger.warning("visit spool: cannot stat %s (%s); skipping it this pass", path.name, exc)
            continue
        out.append((visit_id, suffix, path, st))
    return out


def _load_state_json(path: Path) -> dict:
    """Parse ``state.json`` into an object without schema validation.

    Raises :class:`SpoolStateCorrupt` when the content is unusable by any
    version (invalid JSON or UTF-8, too deeply nested, not an object);
    ``FileNotFoundError`` and other ``OSError`` propagate unchanged.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except RecursionError as exc:
        # Excessively nested content is unreadable by the parser and unusable by every version.
        raise SpoolStateCorrupt(f"{path.name} is too deeply nested") from exc
    except ValueError as exc:
        # JSONDecodeError / UnicodeDecodeError; OSError is not ValueError and still propagates.
        raise SpoolStateCorrupt(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SpoolStateCorrupt(f"{path.name} is not a JSON object")
    return data


def _scrub_peer_identity(doc: dict) -> bool:
    """Null the peer identity of a ``state.json`` object in place; return whether it changed.

    Besides ``peer_uid / pair_id / peer_char_id`` this drops every
    ``digest_writes[*].epochs`` (its keys are subject keys that embed the pair
    id and the person id) and every ``digest_writes[*].plan.displays`` (the
    peer's self-chosen cat and human names). Once the identity is gone no
    digest of the visit runs again (``peer_forgotten``), so neither has any
    use left.
    Works on a schema-invalid object too and touches nothing else in it.
    """
    changed = False
    for name in _PEER_IDENTITY_FIELDS:
        if doc.get(name) is not None:
            doc[name] = None
            changed = True
    runs = doc.get("digest_writes")
    if isinstance(runs, dict):
        for record in runs.values():
            if isinstance(record, dict) and "epochs" in record:
                del record["epochs"]
                changed = True
            plan = record.get("plan") if isinstance(record, dict) else None
            if isinstance(plan, dict) and "displays" in plan:
                # Peer-reported cat/person names are the same data class as header peer_char_tag.
                del plan["displays"]
                changed = True
    return changed


def _raw_state_may_name(
    path: Path, own_char_uid: str, pair_ids: frozenset[str], own_uid: str | None = None,
) -> bool:
    """Whether a parseable but schema-invalid ``state.json`` may belong to this (character, pair).

    False when its raw fields clearly name another character, another pair
    or another account, or carry no peer identity at all any more (already
    wiped: nothing of any person is left to clear); anything unclear counts
    as "may be ours" (fail closed).
    """
    try:
        raw = _load_state_json(path)
    except (OSError, ValueError):
        return True
    if all(name in raw and raw[name] is None for name in _PEER_IDENTITY_FIELDS):
        # Completely erased peer identity leaves nobody to clear, e.g. earlier clearing rewrote raw fields;
        # state unreadable by this version must not block every later clear for this character.
        return False
    char = raw.get("own_char_uid")
    if isinstance(char, str) and char and char != own_char_uid:
        return False
    account = raw.get("own_uid")
    if own_uid is not None and isinstance(account, str) and account and account != own_uid:
        # Visits under other accounts are unrelated, distinguishable even when peer identity and pair_id are erased.
        return False
    pair = raw.get("pair_id")
    return not (isinstance(pair, str) and pair and pair not in pair_ids)


def _raw_state_names_pair(
    path: Path, own_char_uid: str, pair_ids: frozenset[str], own_uid: str | None = None,
) -> bool:
    """Whether a parseable but schema-invalid ``state.json`` explicitly names this (character, pair)."""
    try:
        raw = _load_state_json(path)
    except (OSError, ValueError):
        return False
    if not _names_pair(raw, own_char_uid, pair_ids):
        return False
    account = raw.get("own_uid")
    return own_uid is None or not (isinstance(account, str) and account and account != own_uid)


def _read_state_file(path: Path) -> dict | None:
    # Filenames identify visits; missing/mismatched state visit_id after copies/replacements makes ownership untrusted,
    # or retirement/clearing/renaming would mutate another visit's files.
    expected = path.name[: -len(STATE_SUFFIX)] if path.name.endswith(STATE_SUFFIX) else None
    try:
        # Corrupt content, including deep nesting/nonobjects, raises SpoolStateCorrupt. Parsed objects undergo schema checks,
        # yielding SpoolStateError only; other versions may read those, so never delete them.
        data = _load_state_json(path)
    except FileNotFoundError:
        return None
    if expected is None:
        raise SpoolStateError(f"{path.name} is not a state file name")
    return validate_state(data, visit_id=expected)


def _retention_exempt(state_path: Path, age_s: float) -> bool:
    """Whether a visit's ``state.json`` must survive the 7-day expiry this round.

    True while a diary commit is in flight or failed permanently
    (``committing:diary`` / ``commit_failed:diary``): ``debrief_pending``
    and the write progress are the only basis of the retry. When
    ``state.json`` exists but cannot be read (``OSError``, e.g. locked by
    antivirus or backup) the visit is kept for at most one more retention
    period (``age_s`` below twice the retention), so a transient lock never
    deletes a half-committed diary while a permanently unreadable state
    cannot pin its files forever. A missing or corrupt (schema-invalid)
    state follows the normal expiry.
    """
    try:
        state = _read_state_file(state_path)
    except ValueError:
        return False
    except OSError as exc:
        keep = age_s < 2 * _RETENTION_S
        logger.warning("visit spool: state %s unreadable (%s); %s",
                       state_path.name, exc, "keeping it this sweep" if keep else "reclaiming")
        return keep
    return debrief_pins_state(state)


def _names_pair(doc: dict | None, own_char_uid: str, pair_ids: frozenset[str]) -> bool:
    return (
        doc is not None
        and doc.get("own_char_uid") == own_char_uid
        and doc.get("pair_id") in pair_ids
    )


def _try_read_state(path: Path) -> dict | None:
    try:
        return _read_state_file(path)
    except (OSError, ValueError) as exc:
        logger.warning("visit spool: unreadable state %s: %s", path.name, exc)
        return None


def _read_header_strict(path: Path, *, validate: bool = True) -> dict | None:
    """Read a spool header for the forget path: ``None`` only when the file is absent.

    A truncated (no newline), unparseable or non-object first line raises
    ``ValueError``; other read errors raise ``OSError``. ``validate=False``
    only requires a well-formed object (ownership lookups must still accept
    headers of older versions that lack ``own_char_uid``).
    """
    try:
        with open(path, "rb") as f:
            first = f.readline()
    except FileNotFoundError:
        return None
    if not first.endswith(b"\n"):
        raise ValueError(f"spool header of {path.name} is truncated")
    try:
        header = json.loads(first)
    except RecursionError as exc:
        raise ValueError(f"spool header of {path.name} is too deeply nested") from exc
    if not isinstance(header, dict):
        raise ValueError(f"spool header of {path.name} is not an object")
    # Full schema validation permits forget-person-null peer fields. Headers missing fields, such as {},
    # cannot mean another pair.
    return validate_header(header) if validate else header


def _rewrite_header(path: Path, mutate, *, strict: bool = False) -> bool:
    """Atomically rewrite the first line of a spool file; return whether it changed.

    Raises :class:`SpoolBusy` when the file is still open for appends in this
    process (an in-flight visit). The check and the replacement run under
    the same lock as the writer's register-then-open, so a spool cannot be
    opened in between.
    """
    with _OPEN_SPOOLS_LOCK:
        if _spool_key(path) in _OPEN_SPOOLS:
            raise SpoolBusy(f"spool {path.name} is still being written")
        return _rewrite_header_locked(path, mutate, strict)


def _rewrite_header_locked(path: Path, mutate, strict: bool) -> bool:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return False
    idx = data.find(b"\n")
    header: Any = None
    if idx >= 0:
        try:
            header = json.loads(data[:idx])
        except (ValueError, RecursionError):
            header = None
    if not isinstance(header, dict):
        if strict:
            # Clear path: corrupt headers do not prove erasure; retain for replay and seven-day sweep expiry.
            raise SpoolStateUnreadable([path.name])
        return False
    if not mutate(header):
        return False
    atomic_write_bytes(path, _encode_line(header) + data[idx + 1:])
    return True


def _unlink_unless_open(path: Path) -> bool:
    """Unlink a spool ``.jsonl`` unless some writer in this process holds it open.

    The registry lock is held through the unlink, the same exclusion the
    header rewrite uses, so a writer cannot register in between.
    """
    with _OPEN_SPOOLS_LOCK:
        if _spool_key(path) in _OPEN_SPOOLS:
            return False
        return _unlink(path)


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _sweep_unlink(path: Path) -> bool:
    """:func:`_unlink` for the sweep: any other ``OSError`` is logged and the file kept."""
    try:
        return _unlink(path)
    except OSError as exc:
        # Busy Windows sharing conflicts, permissions, or transcript-named directories wait until next sweep,
        # preventing one permanently locked old file blocking all reclamation.
        logger.warning("visit spool: could not delete %s (%s); keeping it for a later sweep",
                       path.name, exc)
        return False


def _ask_is_live(is_live: Callable[[str], bool], visit_id: str) -> bool:
    """Call the sweep's ``is_live`` for one visit; an exception counts as live (keep its files)."""
    try:
        return bool(is_live(visit_id))
    except Exception as exc:  # noqa: BLE001 - Unknown means conservatively active; one visit's failure cannot stop the sweep.
        logger.warning("visit spool: is_live(%s) failed (%r); keeping the visit this sweep", visit_id, exc)
        return True


def _parse_spool_bytes(data: bytes, visit_id: str) -> SpoolContents:
    parts = data.split(b"\n")
    tail = parts.pop()  # Empty string when ending in a newline.
    dropped = 0
    if tail:
        dropped += 1
        logger.warning(
            "visit spool %s: dropped a partial trailing line (%d bytes)", visit_id, len(tail)
        )
    header: dict | None = None
    lines: list[dict] = []
    for index, raw in enumerate(parts):
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                raise ValueError("not an object")
            # Parsed objects still require write-time schema validation: missing fields, invalid speakers, and invalid lp
            # cannot become recovered transcripts for backfill/upload.
            if index == 0:
                obj = validate_header(obj)
                if obj["visit_id"] != visit_id:
                    # Renamed/replaced files must not attach another visit's header/transcript to this visit.
                    logger.error("visit spool %s: header belongs to another visit, ignoring the file",
                                 visit_id)
                    return SpoolContents(header=None, lines=[], dropped_lines=len(parts) + (1 if tail else 0))
            else:
                encode_spool_line(obj)
        except (ValueError, RecursionError):
            dropped += 1
            logger.warning("visit spool %s: dropped an unreadable line #%d", visit_id, index)
            continue
        if index == 0:
            header = obj
        else:
            lines.append(obj)
    return SpoolContents(header=header, lines=lines, dropped_lines=dropped)


# ── VisitSpool ──────────────────────────────────────────────────────────


class VisitSpool:
    """The spool and ``state.json`` of one visit.

    Lifecycle of the transcript part: :meth:`open` once, :meth:`append` per
    spoken line, :meth:`fsync` whenever :meth:`fsync_due`, :meth:`close` at
    finalize. ``state.json`` methods work whether or not the spool is open.
    """

    def __init__(self, config_dir: str | Path, visit_id: str) -> None:
        self.config_dir = Path(config_dir)
        self.visit_id = require_visit_id(visit_id)
        self.spool_dir = _spool_dir(config_dir)
        self.jsonl_path = visit_path(self.spool_dir, self.visit_id, SPOOL_SUFFIX)
        self.state_path = visit_path(self.spool_dir, self.visit_id, STATE_SUFFIX)
        self._fd: int | None = None
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._last_fsync: float = 0.0
        self._dirty = False

    # -- Transcript writing --

    @property
    def is_open(self) -> bool:
        """Whether this instance currently holds the spool's writer fd."""
        return self._fd is not None

    def _submit(self, fn, *args) -> asyncio.Future:
        if self._executor is None:
            raise RuntimeError("visit spool is not open")
        return asyncio.wrap_future(self._executor.submit(fn, *args))

    def _open_sync(self, data: bytes) -> int:
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | _O_BINARY
        key = _spool_key(self.jsonl_path)
        # Register before opening under the same lock as header rewrites: rewriters see registration and SpoolBusy,
        # or finish replacement before registration, after which O_EXCL opening fails.
        with _OPEN_SPOOLS_LOCK:
            if key in _OPEN_SPOOLS:
                # Another instance writes this visit: adding then discarding on failure would remove
                # its registration, allowing clear/rename to replace a file still being written.
                raise SpoolBusy(f"spool {self.jsonl_path.name} is already open for appends")
            _OPEN_SPOOLS.add(key)
            try:
                fd = os.open(self.jsonl_path, flags, 0o600)
            except BaseException:
                _OPEN_SPOOLS.discard(key)
                raise
        try:
            self._write_all(fd, data)
            os.fsync(fd)
        except BaseException:
            try:
                os.close(fd)
            except OSError as exc:
                # close errors on network/removable drives must not skip file removal/deregistration,
                # leaving SpoolBusy until process exit, or mask the original exception.
                logger.warning("visit spool %s: closing the failed new spool failed: %s",
                               self.visit_id, exc)
            with _OPEN_SPOOLS_LOCK:
                # Delete newly created files while still registered; retaining partial headers makes retries
                # fail O_EXCL with FileExistsError forever after a single transient disk fault.
                try:
                    os.unlink(self.jsonl_path)
                except OSError as exc:
                    logger.warning("visit spool %s: could not remove the failed new spool: %s",
                                   self.visit_id, exc)
                _OPEN_SPOOLS.discard(key)
            raise
        return fd

    @staticmethod
    def _write_all(fd: int, data: bytes) -> None:
        # Write a full line once; rare short regular-file writes continue with the remainder.
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]

    def _append_sync(self, data: bytes) -> None:
        fd = self._fd
        if fd is None:
            raise RuntimeError("visit spool is closed")
        self._write_all(fd, data)

    def _fsync_sync(self) -> None:
        if self._fd is not None:
            os.fsync(self._fd)

    def _close_sync(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            # Deregister in outermost finally: fsync/close failures on network/removable drives must never
            # skip it. _fd is already cleared and executor discarded, leaving no second chance and SpoolBusy
            # until process exit. If fsync failed first, merely log close errors and propagate the original.
            try:
                try:
                    os.fsync(fd)
                except BaseException:
                    try:
                        os.close(fd)
                    except OSError as exc:
                        logger.warning("visit spool %s: close after a failed fsync failed: %s",
                                       self.visit_id, exc)
                    raise
                os.close(fd)
            finally:
                with _OPEN_SPOOLS_LOCK:
                    _OPEN_SPOOLS.discard(_spool_key(self.jsonl_path))

    def _discard_orphan_open(self, fut: "concurrent.futures.Future[int]") -> None:
        """Undo an ``_open_sync`` whose awaiting ``open`` was cancelled.

        Closes the fd, deletes the file (it was created by this very
        ``O_EXCL`` open, so it holds only the header) and drops the in-flight
        registration, so a retry with the same visit id can open again.
        """
        if fut.cancelled() or fut.exception() is not None:
            return
        try:
            os.close(fut.result())
        except OSError as exc:
            # Invalid fds are fine; avoid leakage and still deregister.
            logger.debug("visit spool: closing orphan fd failed: %s", exc)
        with _OPEN_SPOOLS_LOCK:
            # O_EXCL just created this header-only file containing peer identity; retaining it makes same-visit retries
            # fail FileExistsError and keeps peer data until seven-day collection.
            try:
                _unlink(self.jsonl_path)
            except OSError as exc:
                logger.warning("visit spool: removing orphan %s failed: %s",
                               self.jsonl_path.name, exc)
            _OPEN_SPOOLS.discard(_spool_key(self.jsonl_path))

    async def open(self, header: Mapping[str, Any], *, now: float) -> None:
        """Create ``<visit_id>.jsonl`` (``O_EXCL``, ``0o600``) and write the header line.

        ``header`` must carry exactly ``HEADER_FIELDS`` with ``v == 1`` and this
        spool's ``visit_id``. ``now`` (required) seeds the fsync cadence and
        must come from the same clock later passed to :meth:`fsync_due` /
        :meth:`fsync` (any clock, used consistently; the header's
        ``started_at`` is wall time and is not used for this).
        """
        if self._executor is not None:
            raise RuntimeError("visit spool already open")
        clean = validate_header(header)
        if clean["visit_id"] != self.visit_id:
            raise ValueError("spool header visit_id does not match this spool")
        data = _encode_line(clean)
        if len(data) > VISIT_SPOOL_LINE_MAX_BYTES:
            raise SpoolLineTooLarge("spool header too large")
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"visit-spool-{self.visit_id[:6]}"
        )
        fut = executor.submit(self._open_sync, data)
        try:
            self._fd = await asyncio.wrap_future(fut)
        except BaseException:
            # Cancellation can leave _open_sync running, returning an unclaimed registered fd.
            # Await completion, close the fd, and deregister so later
            # clear/rename operations do not remain SpoolBusy.
            fut.add_done_callback(self._discard_orphan_open)
            executor.shutdown(wait=False)
            raise
        self._executor = executor
        # Use only the caller's clock: started_at is wall time; monotonic-driven runtimes using it
        # as baseline would get fsync_due=False throughout the visit.
        self._last_fsync = float(now)
        self._dirty = False

    async def append(self, line: Mapping[str, Any]) -> None:
        """Append one spoken line (see :func:`encode_spool_line`) as a single write."""
        data = encode_spool_line(line)
        fut = self._submit(self._append_sync, data)
        self._dirty = True
        # Accepted submitted lines cannot be withdrawn by caller cancellation, including while queued behind writes.
        await asyncio.shield(fut)

    def fsync_due(self, now: float) -> bool:
        """Whether unsynced lines exist and ``VISIT_SPOOL_FSYNC_S`` passed since the last fsync.

        ``now`` must come from the same clock as the ``now`` given to :meth:`open`.
        """
        return (
            self._fd is not None
            and self._dirty
            # Compare now>=start+interval: floating-point now-last can be 29.999..., delaying due fsync one tick.
            and now >= self._last_fsync + VISIT_SPOOL_FSYNC_S
        )

    async def fsync(self, now: float) -> None:
        """Flush the spool to disk on the writer thread (after every queued append).

        On failure the spool stays dirty and the cadence is not advanced, so
        :meth:`fsync_due` keeps asking for a retry.
        """
        fut = self._submit(self._fsync_sync)
        # Clear dirty flags before submission so later appends mark dirty again rather than being cleared by this success.
        self._dirty = False
        previous = self._last_fsync
        self._last_fsync = now
        try:
            await fut
        except BaseException:
            self._dirty = True
            self._last_fsync = previous
            raise

    async def close(self) -> None:
        """Fsync and close the spool (the finalize fsync); idempotent.

        Cancelling the caller does not cancel the close itself: the worker
        task may still be queued behind an append, and dropping it would leak
        the fd and leave the path registered as in flight (later forget /
        rename would report ``SpoolBusy`` until restart).
        """
        executor = self._executor
        if executor is None:
            return
        try:
            await asyncio.shield(asyncio.wrap_future(executor.submit(self._close_sync)))
        finally:
            self._executor = None
            executor.shutdown(wait=False)

    async def read_back(self) -> SpoolContents:
        """Replay the spool from disk.

        A partial trailing line left by a crash (no newline) and any line that
        does not parse or fails the schema (:func:`validate_header` for the
        first line, :func:`encode_spool_line` for the others) are dropped,
        counted in ``dropped_lines`` and logged; every valid line is returned.
        A file whose header names another ``visit_id`` (renamed or swapped)
        is ignored as a whole: ``header`` is None, no line is returned and
        every line counts as dropped.
        """

        def read() -> SpoolContents:
            # Read/parse in workers: long transcripts can be tens of megabytes; per-line JSON decoding/validation
            # on the event loop stalls active WebSockets.
            try:
                data = self.jsonl_path.read_bytes()
            except FileNotFoundError:
                return SpoolContents(header=None)
            return _parse_spool_bytes(data, self.visit_id)

        return await asyncio.to_thread(read)

    # ── state.json ──

    def _write_state_sync(self, state: Mapping[str, Any]) -> dict:
        if not isinstance(state, Mapping):
            validate_state(state)    # Raise the shared SpoolStateError.
        given = state.get("visit_id")
        if given is not None and given != self.visit_id:
            raise SpoolStateError("state visit_id does not match this spool")
        if "visit_id" in state:
            state = {**state, "visit_id": self.visit_id}
        clean = validate_state(state, visit_id=self.visit_id)
        with path_lock(self.state_path):
            atomic_write_json(self.state_path, clean)
        return clean

    async def write_state(self, state: Mapping[str, Any]) -> dict:
        """Validate and atomically write ``state.json``; return the written copy.

        The document is bound to this spool's visit: a ``None``
        ``visit_id`` is filled in (the key itself is required like every
        other field), a different one raises
        :class:`SpoolStateError`. Reads require the stored ``visit_id`` to
        match the file name, so a swapped or copied ``state.json`` is treated
        as corrupt instead of lending its ownership to another visit.
        """
        return await asyncio.to_thread(self._write_state_sync, state)

    async def read_state(self) -> dict | None:
        """Read and validate ``state.json``; ``None`` when it does not exist."""
        return await asyncio.to_thread(_read_state_file, self.state_path)

    async def read_raw_state(self) -> dict | None:
        """Parse ``state.json`` into its raw object without schema validation; ``None`` when absent.

        Only for ownership checks on a document :meth:`read_state` rejects.
        Raises :class:`SpoolStateCorrupt` when the content is unusable and
        ``OSError`` when the file cannot be read.
        """

        def read() -> dict | None:
            try:
                return _load_state_json(self.state_path)
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(read)

    async def read_header(self) -> dict | None:
        """Read and validate the spool header; ``None`` when the ``.jsonl`` does not exist.

        Raises ``ValueError`` for a truncated, unparseable or schema-invalid
        header and ``OSError`` when the file cannot be read.
        """
        return await asyncio.to_thread(_read_header_strict, self.jsonl_path)

    def _update_state_sync(self, mutate) -> dict:
        with path_lock(self.state_path):
            state = _read_state_file(self.state_path)
            if state is None:
                raise FileNotFoundError(str(self.state_path))
            mutate(state)
            clean = validate_state(state, visit_id=self.visit_id)
            atomic_write_json(self.state_path, clean)
            return clean

    async def mark_debrief_pending(self) -> dict:
        """``debrief_choice='ask_later'`` + ``debrief_chip_pending`` unless a newer choice is already recorded.

        The chips may be answered while this write is still queued: a choice
        recorded meanwhile is never put back to ``ask_later``.
        """

        def mutate(state: dict) -> None:
            if state.get("debrief_choice") in (None, "ask_later"):
                state["debrief_choice"] = "ask_later"
                state["debrief_chip_pending"] = True

        return await asyncio.to_thread(self._update_state_sync, mutate)

    async def mark_no_transcript(self, *, now: float) -> bool:
        """Record the terminal "abandoned" outcome of a memory-on visit with no transcript line.

        For a finalized visit whose spool holds no line at all (nothing was
        ever appended, or every append failed): one digest run closed as
        ``abandoned='no_transcript'`` (watermark 0) and, while nobody chose
        yet, ``debrief_choice='abandoned'``. The visit then counts as settled
        by the unchanged :func:`region_settled` / :func:`debrief_final` rules
        instead of being retried on every start. No-op (False) with memory
        off or once any digest run is registered; a recorded choice is kept.
        """
        def applies(state: Mapping[str, Any]) -> bool:
            return is_digestable(state) and not state["digest_writes"] and not state["digest_runs"]

        current = await self.read_state()
        if current is None or not applies(current):
            # Skip unnecessary rewrites: rewriting every startup refreshes mtime and prevents age-based state.json collection forever.
            return False
        changed = False

        def mutate(state: dict) -> None:
            nonlocal changed
            if not applies(state):
                return
            state["digest_writes"] = {"0": {
                "requested_at": float(now), "through_lp": 0, "group": {}, "segments": {},
                "abandoned": "no_transcript",
            }}
            state["digest_runs"] = 1
            state["digested_through_lp"] = 0
            if state["debrief_choice"] is None:
                state["debrief_choice"] = "abandoned"
                state["debrief_chip_pending"] = False
            changed = True

        await asyncio.to_thread(self._update_state_sync, mutate)
        return changed

    async def update_state_if(self, expected_choice: Any, **changes: Any) -> tuple[bool, dict]:
        """Apply ``changes`` only while ``debrief_choice`` is still ``expected_choice`` (a value or a tuple of values).

        Returns ``(True, new state)``, or ``(False, the state found)`` without
        writing when the choice moved on.

        For transitions that race with writers outside the debrief lock (the
        void step of "forget this person" turns undecided debriefs into
        ``forget``): an unconditional write after a long await would overwrite
        that newer terminal choice.
        """
        expected = expected_choice if isinstance(expected_choice, tuple) else (expected_choice,)

        def mutate(state: dict) -> None:
            if state["debrief_choice"] not in expected:
                raise _NotApplied(copy.deepcopy(state))
            state.update(copy.deepcopy(changes))

        try:
            return True, await asyncio.to_thread(self._update_state_sync, mutate)
        except _NotApplied as lost:
            return False, lost.state

    async def update_state(self, **changes: Any) -> dict:
        """Read-modify-write ``state.json`` with ``changes``; the result is validated."""

        def mutate(state: dict) -> None:
            state.update(copy.deepcopy(changes))

        return await asyncio.to_thread(self._update_state_sync, mutate)

    def _delete_if_settled_sync(self) -> bool:
        # Same per-path locks as header rewrites, JSONL before state: deletion after a rewriter reads but before
        # replacement would let atomic replacement resurrect a transcript just deemed deletable.
        with path_lock(self.jsonl_path), path_lock(self.state_path):
            state = _read_state_file(self.state_path)
            if state is None or not transcript_releasable(state):
                return False
            # Other instances may retain write fds while close queues; deleting then makes their appends
            # target deleted inodes, vanishing together on close.
            return _unlink_unless_open(self.jsonl_path)

    async def delete_if_settled(self) -> bool:
        """Delete ``.jsonl`` once :func:`transcript_releasable` holds.

        Called by ``mark_forget`` and, later, by whichever of the digest commit
        and the last-summary commit finishes last. Returns whether it deleted.
        """
        if self._fd is not None:
            return False
        return await asyncio.to_thread(self._delete_if_settled_sync)

    async def mark_forget(self, *, final_choice: str = "forget") -> bool:
        """Record a final debrief choice that writes nothing more: ``forget`` or ``abandoned``.

        ``final_choice='forget'`` (default) records "do not record" (no
        private memory is written). ``final_choice='abandoned'`` records the
        user abandoning a permanently
        failed diary commit instead: allowed only from ``commit_failed:diary``
        (or again from ``abandoned``); the step already written is not undone
        and the spool is deleted by the same rule, the final choice staying
        ``abandoned`` meanwhile. ``forget`` is allowed from ``null`` /
        ``ask_later`` / ``generating:diary`` / ``preview:diary`` (or again
        from ``forget``). Any other state raises :class:`SpoolStateError`.

        The ``.jsonl`` is deleted right away only when every digest batch of
        every run and the last-visit summary are done; otherwise it is kept
        and deleted by :meth:`delete_if_settled` once they are, because
        "do not record" never cancels the visit-region digest. Returns whether
        the ``.jsonl`` was deleted now.
        """

        if final_choice not in _FORGET_SOURCES:
            raise ValueError("final_choice must be 'forget' or 'abandoned'")

        def mutate(state: dict) -> None:
            if state["debrief_choice"] not in _FORGET_SOURCES[final_choice]:
                # Started/completed commits cannot become do-not-record; abandonment is allowed only after permanent failure.
                raise SpoolStateError(
                    f"cannot record {final_choice} from debrief_choice={state['debrief_choice']!r}"
                )
            state["debrief_choice"] = final_choice
            state["debrief_pending"] = None
            state["debrief_retry"] = None
            state["debrief_chip_pending"] = False

        async def txn() -> bool:
            await asyncio.to_thread(self._update_state_sync, mutate)
            return await self.delete_if_settled()

        # Recording do-not-record and deleting settled transcripts are one transaction completed despite cancellation,
        # or forget persists while nobody retries transcript deletion on already settled visits.
        return await asyncio.shield(txn())

    def _delete_peer_fields_sync(self) -> None:
        def clear_header(header: dict) -> bool:
            changed = False
            for name in _HEADER_PEER_FIELDS:
                if header.get(name) is not None:
                    header[name] = None
                    changed = True
            return changed

        with path_lock(self.jsonl_path):
            _rewrite_header(self.jsonl_path, clear_header, strict=True)
        with path_lock(self.state_path):
            try:
                state = _read_state_file(self.state_path)
            except SpoolStateCorrupt:
                # Invalid JSON is unusable by everyone but may retain peer fields. Delete like drop_corrupt_state,
                # preventing erased headers plus failed state cleanup from blocking every replay.
                self.state_path.unlink(missing_ok=True)
                return
            except SpoolStateError:
                # Parsed objects incompatible with current schemas, e.g. downgrade reading newer state, are rewritten as raw
                # objects clearing peer identity only, bypassing validate_state and preserving unknown newer fields.
                raw = _load_state_json(self.state_path)
                if _scrub_peer_identity(raw):
                    atomic_write_json(self.state_path, raw)
                return
            if state is not None and _scrub_peer_identity(state):
                atomic_write_json(self.state_path, validate_state(state))

    async def delete_peer_fields(self) -> None:
        """Erase ``peer_uid / pair_id / peer_char_id`` from ``state.json`` and the spool header.

        The local "forget this person" counterpart of removing the roster
        entry. Idempotent. Must not run while this instance holds the writer
        fd (the header rewrite replaces the file). ``digest_writes[*].epochs``
        (keyed by subject keys that embed the pair and person ids) is dropped
        too. A ``state.json`` whose content is corrupt (see
        :class:`SpoolStateCorrupt`) is deleted; one that parses but fails the
        current schema is rewritten from its raw object with only those
        fields cleared, everything else kept as it is.
        """
        if self._fd is not None:
            raise RuntimeError("cannot rewrite the header of an open spool")
        await asyncio.to_thread(self._delete_peer_fields_sync)

    # -- Directory operations, class methods --

    @classmethod
    def _owner_of(cls, spool_dir: Path, visit_id: str) -> tuple[str | None, str | None]:
        """Return ``(own_char_uid, own_char)`` from ``state.json``, else from the header.

        Strict: an existing but unreadable / schema-invalid ``state.json`` or
        header raises (``OSError`` / ``ValueError``) instead of reading as
        "owned by nobody", so transactional callers keep their marker.
        """
        state = _read_state_file(visit_path(spool_dir, visit_id, STATE_SUFFIX))
        if state is not None:
            return state["own_char_uid"], state["own_char"]
        header = _read_header_strict(visit_path(spool_dir, visit_id, SPOOL_SUFFIX),
                                     validate=False)
        if header is not None:
            uid = header.get("own_char_uid")
            name = header.get("own_char")
            # Legacy headers may omit own_char_uid and fall back to names; present values must be nonempty strings.
            # own_char must also be nonempty; bad values match neither UID nor name fallback,
            # making retirement appear successful while leaving this character's transcripts on disk.
            if "own_char_uid" in header and (not isinstance(uid, str) or not uid):
                raise ValueError(f"spool header of {visit_id} has a malformed own_char_uid")
            if not isinstance(name, str) or not name:
                raise ValueError(f"spool header of {visit_id} has a malformed own_char")
            return uid, name
        return None, None

    @classmethod
    async def list_visit_ids(cls, config_dir: str | Path, suffixes: Iterable[str]) -> list[str]:
        """Return the sorted visit ids that have a file with one of ``suffixes`` (names only, no stat)."""
        return await asyncio.to_thread(cls._visit_ids, _spool_dir(config_dir), tuple(suffixes))

    @classmethod
    def _visit_ids(cls, spool_dir: Path, suffixes: Iterable[str]) -> list[str]:
        wanted = set(suffixes)
        # List names without stat: unstatable visits must remain discoverable so individual clear/retire/rename
        # operations fail closed rather than silently missing them in scans.
        return sorted({vid for vid, suffix, _p in _list_names(spool_dir) if suffix in wanted})

    @classmethod
    def _retire_char_sync(
        cls, config_dir: Path, character_uid: str, legacy_name: str | None
    ) -> list[str]:
        spool_dir = _spool_dir(config_dir)
        retired = []
        unreadable: list[str] = []
        busy: list[str] = []
        for visit_id in cls._visit_ids(spool_dir, (SPOOL_SUFFIX, STATE_SUFFIX)):
            try:
                owner_uid, owner_name = cls._owner_of(spool_dir, visit_id)
            except (OSError, ValueError):
                # Unknown ownership cannot mean another character: propagate so deletion retains retirement markers for startup reconciliation.
                unreadable.append(visit_id)
                continue
            if owner_uid:
                match = owner_uid == character_uid
            else:
                match = legacy_name is not None and owner_name == legacy_name
            if not match:
                continue
            jsonl = visit_path(spool_dir, visit_id, SPOOL_SUFFIX)
            state_path = visit_path(spool_dir, visit_id, STATE_SUFFIX)
            # Same per-path locks as state/header writers, JSONL then state: concurrent read-modify-write
            # updates either complete before deletion or later fail on absence,
            # never recreating files after retirement.
            with path_lock(jsonl), path_lock(state_path), _OPEN_SPOOLS_LOCK:
                # Like rename/clear, skip active visits then raise SpoolBusy, retaining transaction
                # markers for replay afterward. Deletion failures such as Windows PermissionError
                # count as unreadable without escaping the loop and blocking other visits.
                if _spool_key(jsonl) in _OPEN_SPOOLS:
                    busy.append(visit_id)
                    continue
                try:
                    _unlink(jsonl)
                    _unlink(state_path)
                except OSError:
                    unreadable.append(visit_id)
                    continue
            retired.append(visit_id)
        if unreadable:
            raise SpoolStateUnreadable(unreadable)
        if busy:
            raise SpoolBusy(f"spools still being written: {', '.join(busy)}")
        return retired

    @classmethod
    async def retire_char(
        cls,
        config_dir: str | Path,
        character_uid: str,
        *,
        legacy_name: str | None = None,
    ) -> list[str]:
        """Delete the ``.jsonl`` and ``state.json`` of every visit owned by a deleted character.

        Ownership is ``own_char_uid == character_uid``. Visits whose files
        lack ``own_char_uid`` (older versions) fall back to ``own_char ==
        legacy_name``; the caller passes ``legacy_name`` only while no new
        character of the same name exists. ``.upload.json(l)`` and
        ``visit_reports/`` are never touched. Returns the retired visit ids.
        After handling every other visit, raises :class:`SpoolStateUnreadable`
        when some visit's ownership cannot be read or its files cannot be
        deleted, else :class:`SpoolBusy` when some owned visit is still open
        for appends (retry after it closes).
        """
        return await asyncio.to_thread(
            cls._retire_char_sync, Path(config_dir), character_uid, legacy_name
        )

    @classmethod
    def _rename_own_char_sync(cls, config_dir: Path, old: str, new: str) -> list[str]:
        spool_dir = _spool_dir(config_dir)
        renamed = []

        def fix_header(header: dict) -> bool:
            if header.get("own_char") == old:
                header["own_char"] = new
                return True
            return False

        unreadable: list[str] = []
        busy: list[str] = []
        for visit_id in cls._visit_ids(spool_dir, (SPOOL_SUFFIX, STATE_SUFFIX)):
            changed = False
            jsonl = visit_path(spool_dir, visit_id, SPOOL_SUFFIX)
            state_path = visit_path(spool_dir, visit_id, STATE_SUFFIX)
            # Rename is transactional: never silently skip unreadable visits or pending_rename clears while
            # they remain under nonexistent old names. Process available visits, then raise for retained markers/reconciliation.
            try:
                with path_lock(jsonl):
                    try:
                        changed |= _rewrite_header(jsonl, fix_header, strict=True)
                    except SpoolBusy:
                        # Active visits are busy only when owned by the renamed character; other characters may be visiting
                        # during runtime rename and must not block migration.
                        try:
                            header = _read_header_strict(jsonl, validate=False)
                        except (OSError, ValueError):
                            # Just registered with incomplete header (_open_sync writes after unlocking): ownership is unknown and may
                            # belong to the renamed character. Treat as busy, retaining markers for retry rather than leaving it under
                            # the old name; not unreadable. This window lasts only one write.
                            raise SpoolBusy(f"spool {jsonl.name} is being opened") from None
                        if header is not None and header.get("own_char") == old:
                            raise
                with path_lock(state_path):
                    state = _read_state_file(state_path)
                    if state is not None and state["own_char"] == old:
                        state["own_char"] = new
                        atomic_write_json(state_path, validate_state(state))
                        changed = True
            except SpoolBusy:
                busy.append(visit_id)
            except (OSError, ValueError, SpoolStateUnreadable):
                unreadable.append(visit_id)
            if changed:
                renamed.append(visit_id)
        if unreadable:
            raise SpoolStateUnreadable(unreadable)
        if busy:
            raise SpoolBusy(f"spools still being written: {', '.join(busy)}")
        return renamed

    @classmethod
    async def rename_own_char(cls, config_dir: str | Path, old: str, new: str) -> list[str]:
        """Rewrite ``own_char`` from ``old`` to ``new`` in every spool header and ``state.json``.

        Visits whose ``.jsonl`` is already gone are found through
        ``state.json.own_char``. Visits already carrying ``new`` are skipped,
        so the call is idempotent and safe to rerun from startup
        reconciliation (and to run as ``new -> old`` for a rollback). Returns
        the visit ids that changed. Visits of other characters still being
        written are left alone; one of the renamed character still being
        written raises :class:`SpoolBusy` after every other visit is handled,
        as does one still being opened (its header not written yet, so its
        owner is unknown). Raises :class:`SpoolStateUnreadable` (after rewriting every readable
        visit) when some header or ``state.json`` cannot be read.
        """
        if not old or not new or old == new:
            return []
        return await asyncio.to_thread(cls._rename_own_char_sync, Path(config_dir), old, new)

    @classmethod
    def _find_visits_sync(
        cls, config_dir: Path, own_char_uid: str, pair_ids: frozenset[str],
        corrupt_wiped: list[str] | None = None, own_uid: str | None = None,
        strict: bool = True,
    ) -> list[str]:
        spool_dir = _spool_dir(config_dir)
        found = []
        unreadable: list[str] = []
        for visit_id in cls._visit_ids(spool_dir, (SPOOL_SUFFIX, STATE_SUFFIX)):
            # Clear paths read strictly: settled visits often retain only state.json. Skipping unreadable state
            # would complete wipe_spool and remove undo logs while peer fields remain.
            state_unreadable = False
            state_corrupt = False
            state_schema = False
            schema_excludes = False
            schema_names = False
            try:
                state = _read_state_file(visit_path(spool_dir, visit_id, STATE_SUFFIX))
            except FileNotFoundError:
                state = None
            except SpoolStateCorrupt:
                # Intrinsically corrupt content (invalid JSON/nonobjects/excessive nesting) is unusable by everyone,
                # unlike transient OSError unreadability.
                state = None
                state_unreadable = True
                state_corrupt = True
            except SpoolStateError:
                # Parsed but current-schema-incompatible state may be readable by other versions;
                # never delete. Record whether raw fields explicitly exclude this clear,
                # while checking headers normally and clearing peer fields if they still identify this pair.
                schema_excludes = not _raw_state_may_name(
                    visit_path(spool_dir, visit_id, STATE_SUFFIX), own_char_uid, pair_ids, own_uid,
                )
                schema_names = _raw_state_names_pair(
                    visit_path(spool_dir, visit_id, STATE_SUFFIX), own_char_uid, pair_ids, own_uid,
                )
                state = None
                state_unreadable = True
                state_schema = True
            except OSError:
                # For unreadable state, inspect headers first. Explicit other-character/other-pair headers exclude the visit;
                # unrelated corrupt files cannot block all clears forever. Only unreadable headers or erased identity
                # for the same character, leaving person ambiguity, count as unreadable.
                state = None
                state_unreadable = True
            if _names_pair(state, own_char_uid, pair_ids):
                found.append(visit_id)
                continue
            # With only JSONL or state no longer identifying ownership, headers identify visits. Corrupt headers cannot mean
            # another pair or wipe_spool completes with original peer fields still present.
            try:
                header = _read_header_strict(visit_path(spool_dir, visit_id, SPOOL_SUFFIX))
            except (OSError, ValueError):
                unreadable.append(visit_id)
                continue
            header_wiped = header is not None and (
                header.get("own_char_uid") == own_char_uid and header.get("pair_id") is None
            )
            if _names_pair(header, own_char_uid, pair_ids):
                found.append(visit_id)
            elif schema_names and (header is None or header_wiped):
                # Incompatible state raw fields identify this pair but headers are absent/erased, e.g. header wiped before failed
                # state rewrite: wipe raw state identity rather than blocking every replay as unreadable.
                found.append(visit_id)
            elif schema_names:
                # Incompatible raw state identifies this pair while headers identify elsewhere: inconsistent identities cannot
                # be excluded by headers or justify erasing another person's header. Fail closed as unreadable.
                unreadable.append(visit_id)
            elif state_unreadable and (header is None or header_wiped):
                if schema_excludes:
                    # Headers absent/erased or unrelated, with incompatible raw state explicitly identifying another
                    # character/pair/account: both exclude this clear, so skip without blocking/deleting.
                    continue
                if state_corrupt and header is None:
                    # Corrupt state.json alone, common after settlement, is unusable and ownership unknown. Blocking
                    # would stall every character's clears; if it belongs to the erased person, deletion is appropriate anyway.
                    # Report separately for deletion by the clear path.
                    if corrupt_wiped is not None:
                        corrupt_wiped.append(visit_id)
                    continue
                if state_corrupt and header is not None:
                    # Header identity erased for this character and state.json corrupt: nobody can use it,
                    # and it must not block all future clears. This function only finds files, also during startup handoff,
                    # so delete nothing here; report separately for the clear path to remove it and any residual
                    # peer fields.
                    # Report only same-account visits, using own_uid preserved in headers during wiping; other accounts are unrelated.
                    if corrupt_wiped is not None and (own_uid is None or header.get("own_uid") == own_uid):
                        corrupt_wiped.append(visit_id)
                    continue
                if state_schema and header is None:
                    # Only incompatible state remains, with raw fields neither identifying nor excluding this pair:
                    # ownership unknown but not corrupt because other versions can read it. Skip without deleting/blocking,
                    # or downgrades would block every local character's clears.
                    continue
                unreadable.append(visit_id)
        if unreadable and strict:
            raise SpoolStateUnreadable(unreadable)
        return found

    @classmethod
    async def find_visits_for_pairs(
        cls, config_dir: str | Path, own_char_uid: str, pair_ids: Iterable[str],
        *, corrupt_wiped: list[str] | None = None, own_uid: str | None = None,
        strict: bool = True,
    ) -> list[str]:
        """Return visit ids of ``own_char_uid`` whose state or header still names one of ``pair_ids``.

        Matching on ``pair_id`` (which embeds both community accounts) rather
        than ``peer_uid`` keeps another local account's visits with the same
        person untouched. Raises :class:`SpoolStateUnreadable` when a
        ``state.json`` exists but cannot be read (the forget step then stays
        pending instead of silently missing that visit).
        """
        return await asyncio.to_thread(
            cls._find_visits_sync, Path(config_dir), own_char_uid, frozenset(pair_ids), corrupt_wiped,
            own_uid, strict,
        )

    @classmethod
    async def drop_corrupt_state(cls, config_dir: str | Path, visit_id: str) -> None:
        """Delete a content-corrupt ``state.json`` (unusable by anyone) during a forget."""
        path = visit_path(_spool_dir(config_dir), visit_id, STATE_SUFFIX)

        def drop() -> None:
            # Share state.json writers' file lock to prevent interleaving with update_state.
            with path_lock(path):
                try:
                    _load_state_json(path)
                except FileNotFoundError:
                    # Already absent: nothing to delete.
                    return
                except SpoolStateCorrupt:
                    # Delete only intrinsically corrupt content, matching discovery's invalid JSON/nonobject/deep-nesting criteria;
                    # never touch parsed objects, even when schema-incompatible.
                    path.unlink(missing_ok=True)

        await asyncio.to_thread(drop)

    @classmethod
    def _sweep_sync(
        cls, config_dir: Path, now: float, live_ids: frozenset[str] = frozenset(), uploads: str = "all",
    ) -> list[Path]:
        spool_dir = _spool_dir(config_dir).resolve()
        deleted: list[Path] = []
        remaining = []
        scanned = _scan(spool_dir)
        # Visits with pending uploads need state.json finalized for sealing, or for account/character checks and
        # legacy ownerless-file account recovery when upload files stand alone. Keep state with deferred uploads until retry
        # completes, then age-collect normally; otherwise normal endings seal as crashes and ownerless files never upload.
        pending_uploads = {visit_id for visit_id, suffix, _p, _st in scanned if suffix in _UPLOAD_SUFFIXES}
        for visit_id, suffix, path, st in scanned:
            is_upload = suffix in _UPLOAD_SUFFIXES
            if (
                (uploads == "defer" and (is_upload or (suffix == STATE_SUFFIX and visit_id in pending_uploads)))
                or (uploads == "only" and not is_upload)
            ):
                remaining.append((visit_id, suffix, path, st))
                continue
            if now - st.st_mtime <= _RETENTION_S:
                remaining.append((visit_id, suffix, path, st))
                continue
            state_path = visit_path(spool_dir, visit_id, STATE_SUFFIX)
            # Share state/header per-path locks, JSONL then state, and recheck expiry under lock:
            # deleting after a rewriter reads but before replacement resurrects expired transcripts with fresh mtime.
            with path_lock(visit_path(spool_dir, visit_id, SPOOL_SUFFIX)), path_lock(state_path):
                try:
                    st = path.stat()
                except FileNotFoundError:
                    continue
                except OSError:
                    remaining.append((visit_id, suffix, path, st))
                    continue
                if now - st.st_mtime <= _RETENTION_S:
                    remaining.append((visit_id, suffix, path, st))
                    continue
                # Diary commits in progress / permanent failures awaiting handling (committing / commit_failed:diary)
                # never expire: state.json debrief_writes/debrief_pending are the sole completion basis,
                # whose deletion leaves permanent partial work. Exempt state only, not JSONL, or ignored
                # permanent failures accumulate transcripts forever.
                # Apply exemptions only to state.json itself, using its own age for grace periods.
                if suffix == STATE_SUFFIX and _retention_exempt(state_path, now - st.st_mtime):
                    remaining.append((visit_id, suffix, path, st))
                    continue
                # Leave entire actively written visits untouched, even if clock jumps make them look seven days old. Per-path locks cannot stop
                # append; deletion sends writes into unlinked inodes, disappearing with recovery data on close.
                # Check and delete under the registration lock so open cannot register between them.
                # Memory-disabled active visits have upload journals only, no registered memory spool; use caller active-visit checks.
                live = visit_id in live_ids
                with _OPEN_SPOOLS_LOCK:
                    busy = live or _spool_key(visit_path(spool_dir, visit_id, SPOOL_SUFFIX)) in _OPEN_SPOOLS
                    unlinked = False if busy else _sweep_unlink(path)
                if not unlinked:
                    if busy or path.exists():
                        remaining.append((visit_id, suffix, path, st))
                else:
                    deleted.append(path)
                    if suffix in _UPLOAD_SUFFIXES:
                        logger.warning(
                            "visit spool: gave up pending upload %s after %d days",
                            path.name, VISIT_SPOOL_RETENTION_DAYS,
                        )
        if uploads == "only":
            return deleted
        total = sum(st.st_size for _v, _s, _p, st in remaining)
        if total <= VISIT_SPOOL_DIR_CAP_BYTES:
            return deleted
        # Above collection limits, reclaim settled visits oldest-first; leave pending uploads and unsettled visits untouched.
        by_visit: dict[str, list[tuple[str, Path, os.stat_result]]] = {}
        for visit_id, suffix, path, st in remaining:
            by_visit.setdefault(visit_id, []).append((suffix, path, st))
        candidates = []
        for visit_id, files in by_visit.items():
            if visit_id in live_ids:
                continue
            if any(suffix in _UPLOAD_SUFFIXES for suffix, _p, _st in files):
                # Pending-upload sealing needs state.json/memory spool for account/end reasons, or state to verify identity and
                # backfill account ownership in legacy ownerless upload files.
                # Retain the whole visit until upload succeeds or expires and pending files disappear, then reclaim next round.
                continue
            state = _try_read_state(visit_path(spool_dir, visit_id, STATE_SUFFIX))
            if state is None or not transcript_releasable(state):
                continue
            reclaimable = [(p, st) for suffix, p, st in files if suffix not in _UPLOAD_SUFFIXES]
            if reclaimable:
                oldest = min(st.st_mtime for _p, st in reclaimable)
                candidates.append((oldest, visit_id, reclaimable))
        candidates.sort()
        for _oldest, visit_id, files in candidates:
            if total <= VISIT_SPOOL_DIR_CAP_BYTES:
                break
            jsonl = visit_path(spool_dir, visit_id, SPOOL_SUFFIX)
            state_path = visit_path(spool_dir, visit_id, STATE_SUFFIX)
            # Share per-path state/header locks, JSONL then state, rechecking settlement under lock:
            # deleting between a rewriter's read and atomic replacement would resurrect the file.
            with path_lock(jsonl), path_lock(state_path):
                state = _try_read_state(state_path)
                if state is None or not transcript_releasable(state):
                    continue
                # Recheck committing/permanently failed visits under lock: transcripts are reclaimable, state.json must remain.
                if debrief_pins_state(state):
                    files = [(p, st) for p, st in files if p != state_path]
                # Some instance still writes this visit with closure queued: skip the whole visit; deleting state alone
                # leaves transcripts whose settlement can never be recognized next sweep.
                with _OPEN_SPOOLS_LOCK:
                    if _spool_key(jsonl) in _OPEN_SPOOLS:
                        continue
                    # Delete state.json last and retain it if any earlier deletion fails. Otherwise an undeletable transcript
                    # survives without readable settlement state and occupies capacity forever.
                    failed = False
                    for path, st in sorted(files, key=lambda f: f[0] == state_path):
                        if path == state_path and failed:
                            break
                        if _sweep_unlink(path):
                            deleted.append(path)
                            total -= st.st_size
                        else:
                            # Do not use exists() to distinguish already absent: access errors also return False.
                            # Conservatively retain state this round; reclaim after reading it next round.
                            failed = True
        return deleted

    @classmethod
    def _unreclaimable_bytes_sync(cls, config_dir: Path, live_ids: frozenset[str]) -> int:
        by_visit: dict[str, list[tuple[str, os.stat_result]]] = {}
        for visit_id, suffix, path in _list_names(_spool_dir(config_dir)):
            if suffix == OUTBOX_SUFFIX or visit_id in live_ids:
                continue
            try:
                st = path.stat()
            except FileNotFoundError:
                continue
            # Propagate other stat sharing/permission errors: unknown-size files cannot count as free; callers reject as full.
            by_visit.setdefault(visit_id, []).append((suffix, st))
        total = 0
        for visit_id, files in by_visit.items():
            if not any(suffix in _UPLOAD_SUFFIXES for suffix, _st in files):
                state = _try_read_state(visit_path(_spool_dir(config_dir), visit_id, STATE_SUFFIX))
                if state is not None and transcript_releasable(state):
                    # Settled visits occupy no admission quota because capacity collection removes them; diary committing/failed visits retain state.json only.
                    if debrief_pins_state(state):
                        total += sum(st.st_size for suffix, st in files if suffix == STATE_SUFFIX)
                    continue
            total += sum(st.st_size for _suffix, st in files)
        return total

    @classmethod
    async def unreclaimable_bytes(
        cls, config_dir: str | Path, *, is_live: Callable[[str], bool] | None = None,
    ) -> int:
        """Bytes the size-based sweep (step 2 of :meth:`sweep`) may never reclaim.

        Every file (outbox streams aside) of a visit with a pending upload or
        whose ``state.json`` is missing, unreadable or not
        :func:`transcript_releasable`: pending uploads plus unsettled spools,
        the admission cap ``VISIT_UPLOAD_PENDING_CAP_BYTES`` bounds them. Visits
        for which ``is_live`` answers True are left out (the visit in flight
        has its own per-file limits); ``is_live`` runs on the event loop only.
        Raises ``OSError`` when a counted file cannot be ``stat``-ed (unknown
        size: the admission check fails closed).
        """
        live_ids: frozenset[str] = frozenset()
        if is_live is not None:
            candidates = await asyncio.to_thread(cls._visit_ids, _spool_dir(config_dir), _KNOWN_SUFFIXES)
            live_ids = frozenset(visit_id for visit_id in candidates if _ask_is_live(is_live, visit_id))
        return await asyncio.to_thread(cls._unreclaimable_bytes_sync, Path(config_dir), live_ids)

    @classmethod
    async def sweep(
        cls, config_dir: str | Path, now: float, *, is_live: Callable[[str], bool] | None = None,
        uploads: str = "all",
    ) -> list[Path]:
        """Reclaim spool directory space; return the deleted paths.

        ``now`` must be wall-clock epoch seconds (``time.time()``): file ages
        are ``now - st_mtime``. Unlike :meth:`fsync_due` / :meth:`fsync`
        (any clock, used consistently), a monotonic clock here makes every
        file look fresh, so nothing would ever expire.

        1. Every file older than ``VISIT_SPOOL_RETENTION_DAYS`` (by mtime) is
           deleted, pending uploads included (their seven-day limit), except
           the ``state.json`` of a visit whose diary commit is in flight or
           failed permanently (``committing:diary`` / ``commit_failed:diary``):
           it stays until both writes finish or the user abandons.
        2. If the directory still exceeds ``VISIT_SPOOL_DIR_CAP_BYTES``, only
           settled visits (digest and last summary done, debrief final or
           committing) are reclaimed, oldest first, until it fits; a
           committing / failed visit keeps its ``state.json``. Unsettled visits, however
           large, and pending ``.upload.json`` / ``.upload.jsonl`` files are
           never deleted for size (a visit with a pending upload keeps all its
           files: sealing may need its ``state.json`` or memory spool for the
           owning account, and a lone sealed upload is checked against its
           ``state.json``); the admission cap
           ``VISIT_UPLOAD_PENDING_CAP_BYTES`` bounds them instead.

        ``uploads`` narrows step 1 for the pending uploads: ``"all"`` (the
        default) treats them like every other file, ``"defer"`` leaves them
        out (the caller expires them after one more upload attempt), together
        with the ``state.json`` of a visit that still has a pending upload
        (sealing reads its finalized reason, a lone sealed upload is checked
        against it), and
        ``"only"`` expires nothing but them and skips step 2.

        A file that cannot be deleted (locked, no permission, a directory) is
        logged and kept for a later sweep; the rest of the sweep continues.
        Visits for which ``is_live`` answers True (in flight, possibly with
        only an upload stream) are never touched. ``is_live`` is called on
        the event loop only (it usually reads loop-owned registries): once
        per visit id found in the directory, before the worker thread
        starts; an ``is_live`` that raises counts as live for that visit.
        """
        live_ids: frozenset[str] = frozenset()
        if is_live is not None:
            # Snapshot active visits on the event loop before offloading: is_live reads loop-owned registries,
            # and cross-thread iteration can race mutations. Visits starting afterward create new files untouched by age
            # collection; capacity collection targets settled visits only, also excluding them.
            candidates = await asyncio.to_thread(cls._visit_ids, _spool_dir(config_dir), _KNOWN_SUFFIXES)
            live_ids = frozenset(visit_id for visit_id in candidates if _ask_is_live(is_live, visit_id))
        if uploads not in ("all", "defer", "only"):
            raise ValueError(f"unknown uploads mode {uploads!r}")
        return await asyncio.to_thread(cls._sweep_sync, Path(config_dir), now, live_ids, uploads)
