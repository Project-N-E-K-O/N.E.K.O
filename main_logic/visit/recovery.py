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

"""Startup recovery of visit files (background task, never on the startup path).

Design: ``docs/design/visit-infrastructure.md`` section 3.7.3 item 7 and
PR-08 ``recovery.py``. :func:`visit_spool_recovery` is started with
``asyncio.create_task`` after startup (PR-09b) and runs, in order:

1. the retirement of deleted characters (``pending_retire``), then the
   character-rename reconciliation (``visit_peers.json.pending_rename``, then
   each rename a cloudsave import queued in ``pending_rename_queue``), so
   a rename never moves entries into a name whose leftovers are still to be
   retired, and forgets resolve roster entries under their current name;
2. unfinished local forgets (clearing sentinels, then revocation logs);
3. startup cleanup: only leftover ``.outbox.jsonl`` files are deleted;
4. ``VisitSpool.sweep`` (7 days / 20 MB, pending uploads and unsettled
   visits are never reclaimed for size);
5. every visit's ``state.json``: a crashed visit (``finalized`` empty, not
   live) is marked ``crash``; a visit with digestable lines whose debrief
   is still open gets ``debrief_chip_pending`` (the chips are replayed on the
   next ``visit_bind``; nothing is written to private memory here); the
   visit-region digest and the last-visit summary are completed;
6. pending transcript uploads: every ``.upload.json`` is retried; a
   ``.upload.jsonl`` stream without one (a crash anywhere before finalize
   wrote it) is turned into one first, whatever ``finalized`` says;
7. queued reports in ``visit_reports/``, after their visit's upload.

Unreachable memory_server or Servers only leave files for the next start.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncContextManager

from config.visit_settings import VISIT_REPORTS_DIRNAME, VISIT_SPOOL_DIRNAME, VISIT_SPOOL_RETENTION_DAYS
from main_logic.visit import local_chars, memory_bridge
from main_logic.visit.char_lifecycle import (
    ALL_NAMES as _ALL_NAMES,
    MARKER_CHANGED as _MARKER_CHANGED,
    RetirePersona,
    drop_deleted_queued_renames,
    reconcile_renames,
    reconcile_rename as _reconcile_rename,
    replay_retires,
)
from main_logic.visit.forget_runner import LifecycleGuard, VoidPending, replay_forgets
# Share the transport constant used by identity ticket validation; newly added transport types are recognized automatically,
# avoiding deletion of upload-only visits as corrupt.
from main_logic.visit.identity import _TRANSPORTS as _UPLOAD_TRANSPORTS
from main_logic.visit.memory_commit import (
    ResolveCharName,
    SummaryLLM,
    commit_last_summary,
    commit_visit_region,
    line_order_key,
    track_last_summary,
)
from main_logic.visit.spool import (
    OUTBOX_SUFFIX,
    SPOOL_SUFFIX,
    STATE_SUFFIX,
    UPLOAD_JSON_SUFFIX,
    UPLOAD_JSONL_SUFFIX,
    VisitSpool,
    is_digestable,
    is_spool_open,
    _read_header_strict,
)
from main_logic.visit.subjects import (
    RosterCorruptError,
    path_lock,
    pending_rename_names,
    read_roster_marker,
    read_roster_markers,
)
from memory.scoped_client import ScopedMemoryClient
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger
from utils.visit_wire import VISIT_ID_RE, visit_path

logger = get_module_logger(__name__, "Main")

UPLOAD_DOC_VERSION = 1
_USAGE_KEYS = ("llm_input_tokens", "llm_output_tokens", "tts_requests", "tts_chars")
_LINE_FIELDS = ("lp", "side", "from", "ts", "text", "truncated")

RenderChips = Callable[..., Awaitable[bool]]
"""``render_chips(visit_id, *, own_char, status) -> bool``: try to show the debrief block now.

``status`` is ``'interrupted'`` for a crashed visit, else ``None``; the
callback shows the notice only with the chips (not with a preview or a
failure block). The return value is only whether it reached a connected
display; the pending flag in ``state.json`` stays until the user decides
either way."""

UploadTranscript = Callable[[str, dict], Awaitable["bool | str"]]
"""``upload_transcript(visit_id, upload_doc) -> bool | str``: upload one pending transcript.

``upload_doc`` is the ``.upload.json`` document (``{v, own_visit_uid,
own_char_uid, transport, request}``; ``request`` is the Servers wire body).
Returns True when the local file may go because Servers has the transcript
(accepted, duplicate); a non-empty reason string (e.g. the Servers error
code) when it may go after a terminal rejection, so the queued report
records ``transcript_unavailable`` with that reason (when the file cannot
be deleted, the rejection is recorded in it and it is never uploaded
again); False keeps it for a
later retry (network error, 429, account mismatch: uploads happen only
while the signed-in account is ``own_visit_uid``). The callback may
rewrite the file with its chunk progress."""

SubmitReport = Callable[[str, dict], Awaitable[bool]]
"""``submit_report(visit_id, report_doc) -> bool``: True once Servers accepted the queued report."""

SpawnBackground = Callable[[str, Callable[[], Awaitable[Any]]], Any]
"""``spawn_background(own_char_uid, factory)``: run ``factory()`` as the character's visit background task."""

ResumeDiaryCommit = Callable[[VisitSpool, dict], Awaitable[Any]]
"""``resume_diary_commit(spool, state)``: continue a ``committing:diary`` two-step write (PR-14)."""


@dataclass
class RecoveryReport:
    """What one recovery pass did (for logs and tests)."""

    forgets_clean: bool = True
    renamed: bool = False
    # Deletion retirement (pending_retire) fully completed; False retains marker until next startup.
    retired: bool = True
    crashed: list[str] = field(default_factory=list)
    chips: list[str] = field(default_factory=list)
    digests: dict[str, bool] = field(default_factory=dict)
    summaries: dict[str, bool] = field(default_factory=dict)
    # True: transcript reached Servers (accepted / duplicate); False: retain for retry. Do not count terminal rejection here.
    uploads: dict[str, bool] = field(default_factory=dict)
    # Terminally rejected transcripts and reasons: local files may be deleted, but transcripts can never reach Servers.
    rejected: dict[str, str] = field(default_factory=dict)
    reports: dict[str, bool] = field(default_factory=dict)
    swept: int = 0
    # Transcripts found unavailable this pass and reasons: include in submission even if report-file marker persistence fails.
    transcript_unavailable: dict[str, str] = field(default_factory=dict)
    unavailable_owner: dict[str, str] = field(default_factory=dict)
    """visit_id -> ``own_visit_uid`` of the transcript a ``transcript_unavailable`` reason belongs to.

    Set when the reason could not be checked against the queued report (file
    unreadable): the submission compares it with the report's owner first.
    """


# ── Upload log → upload file ───────────────────────────────────────────────


def _read_stream(path: Path) -> list[dict]:
    records, dropped = _read_stream_counted(path)
    if dropped:
        logger.warning("visit upload stream %s: dropped %d unreadable record(s)", path.name, dropped)
    return records


_NEWLINE = bytes((10,))


def _read_stream_counted(path: Path) -> tuple[list[dict], int]:
    """``(JSON objects of the stream, records dropped)``: a partial trailing line and unparseable lines count."""
    parts = path.read_bytes().split(_NEWLINE)
    dropped = 1 if parts.pop() else 0
    out = []
    for raw in parts:
        try:
            obj = json.loads(raw)
        except (ValueError, RecursionError):
            dropped += bool(raw.strip())
            continue
        if isinstance(obj, dict):
            out.append(obj)
        else:
            dropped += 1
    return out, dropped


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _valid_line(record: dict) -> bool:
    # Rows parse as objects but contain invalid fields (null lp, unknown side, etc.): discard as corrupt,
    # since they make sorting raise and abort the entire recovery pass.
    lp = record.get("lp")
    return (
        all(name in record for name in _LINE_FIELDS)
        and isinstance(lp, int) and not isinstance(lp, bool) and lp >= 0
        and record.get("side") in ("host", "guest")
        and record.get("from") in ("own_cat", "peer_cat", "own_human", "peer_human")
        and _number(record.get("ts")) is not None
        and isinstance(record.get("text"), str) and _utf8_ok(record["text"])
        and isinstance(record.get("truncated"), bool)
    )


def _utf8_ok(text: str) -> bool:
    # Escaped lone surrogates decode into strings but cannot encode as UTF-8: upload encoding would fail and retry forever as a local error.
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def build_upload_doc(
    records: list[dict],
    *,
    visit_id: str,
    finalized_reason: str | None,
    fallback_own_visit_uid: str | None = None,
    fallback_own_char_uid: str | None = None,
) -> dict | None:
    """Build the ``.upload.json`` document of a crashed visit from its upload stream.

    ``records`` are the stream's JSON objects. The first must be the upload
    header ``{kind:'header', visit_id, role, own_visit_uid, started_at,
    own_char_uid, app_version, transport}``; without it, or with a header
    whose ``transport`` is not a non-empty string (nothing else records it),
    ``None`` is returned (corrupt stream). ``ended_at`` is the ``ts`` of the last accepted record
    that has one (a valid line, a usage delta or an anomaly; the header's
    ``started_at`` when there is none), ``usage`` the sum of
    every usage delta, ``anomalies`` the number of anomaly records, ``lines``
    every line record in ``(lp, side_rank)`` order, and ``finalized_reason``
    the given reason or ``'crash'``. A header without ``own_visit_uid`` (an
    older header layout) takes ``fallback_own_visit_uid`` (the visit's own
    ``state.json`` / spool header account) instead of being dropped; a
    missing or malformed ``own_char_uid`` likewise takes
    ``fallback_own_char_uid`` and is ``None`` when there is none (the caller
    must not seal such a document).
    """
    if not records or records[0].get("kind") != "header":
        return None
    header = records[0]
    started_at = _number(header.get("started_at"))
    transport = header.get("transport")
    if (
        header.get("visit_id") != visit_id or started_at is None
        or header.get("role") not in ("host", "guest")
        # Transport is recorded nowhere else and cannot be recovered: missing/wrong type means corrupt log, checked before file writing.
        # Still seal unknown nonempty values (new-version transport), leaving upload-file handling to retain them for later.
        or not isinstance(transport, str) or not transport
    ):
        return None
    usage = {key: 0 for key in _USAGE_KEYS}
    anomalies = 0
    lines: list[dict] = []
    ended_at = started_at
    for record in records[1:]:
        kind = record.get("kind")
        accepted = False
        if kind == "line":
            if _valid_line(record):
                lines.append({name: record[name] for name in _LINE_FIELDS})
                accepted = True
        elif kind == "usage":
            delta = record.get("d")
            if isinstance(delta, dict):
                for key in _USAGE_KEYS:
                    value = delta.get(key)
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                        usage[key] += value
                        # Adopt usage records only if at least one value counts; wholly invalid usage must not extend duration through its timestamp.
                        accepted = True
        elif kind == "anomaly":
            anomalies += 1
            accepted = True
        # Use timestamps only from adopted records; discarded corrupt rows / unknown records must not alter end time or duration.
        ts = _number(record.get("ts")) if accepted else None
        if ts is not None:
            ended_at = ts
    lines.sort(key=line_order_key)
    # Two finite timestamps can still yield an infinite difference (-1.7e308 and 1.7e308); int(inf) raises
    # OverflowError and aborts recovery uploads. Record 0 for uncomputable duration.
    duration = _number(ended_at - started_at)
    request = {
        "visit_id": visit_id,
        "role": header["role"],
        "started_at": started_at,
        "ended_at": ended_at,
        "finalized_reason": finalized_reason or "crash",
        "usage": {"duration_s": max(0, int(duration)) if duration is not None else 0, **usage},
        "lines": lines,
        "anomalies": anomalies,
        "app_version": str(header.get("app_version") or ""),
    }
    return {
        "v": UPLOAD_DOC_VERSION,
        # Earlier upload-header designs omitted this field: on missing/wrong type use this visit's state.json /
        # transcript header room-owning account (upload requires matching login, so None would prevent every account from uploading);
        # record None only if both lack it; still seal, retaining the only log copy.
        "own_visit_uid": _owner_or_none(header.get("own_visit_uid")) or _owner_or_none(fallback_own_visit_uid),
        # Likewise older headers may lack character id: use this visit's state.json / transcript header; if neither has it,
        # return None and let sealing reject before writing (otherwise the sealed file would be deleted as corrupt).
        "own_char_uid": _owner_or_none(header.get("own_char_uid")) or _owner_or_none(fallback_own_char_uid),
        "transport": transport,
        "request": request,
    }


def _envelope_valid(own_char_uid: Any, transport: Any) -> bool:
    # Check type first: objects / arrays are unhashable, so direct set membership raises TypeError and aborts all recovery uploads.
    return (
        isinstance(own_char_uid, str) and bool(own_char_uid)
        and isinstance(transport, str) and transport in _UPLOAD_TRANSPORTS
    )


def _owner_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _spool_header_ids_sync(spool_dir: Path, visit_id: str) -> tuple[str | None, str | None]:
    """``(own_uid, own_char_uid)`` recorded by this visit's spool header, each None when absent."""
    try:
        header = _read_header_strict(visit_path(spool_dir, visit_id, SPOOL_SUFFIX), validate=False)
    except (OSError, ValueError):
        return None, None
    if not header or header.get("visit_id") != visit_id:
        # Replaced/copied header: another visit's account and character must not be applied to this upload.
        return None, None
    return _owner_or_none(header.get("own_uid")), _owner_or_none(header.get("own_char_uid"))


def _with_header_fallback(
    spool_dir: Path, visit_id: str, owner: str | None, char_uid: str | None,
) -> tuple[str | None, str | None]:
    # Consult memory spool header only if state.json is unreadable, filling only missing fields.
    if owner is None or char_uid is None:
        header_owner, header_char = _spool_header_ids_sync(spool_dir, visit_id)
        owner = owner if owner is not None else header_owner
        char_uid = char_uid if char_uid is not None else header_char
    return owner, char_uid


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _sealed_doc_belongs(doc: Any, visit_id: str) -> bool:
    """Whether a ``.upload.json`` is this visit's well-formed sealed upload.

    Only then may the stream next to it be deleted; anything else (another
    visit's document, a wrong version, a broken request) is resealed from
    the stream, the only intact copy.
    """
    if not isinstance(doc, dict) or doc.get("v") != UPLOAD_DOC_VERSION:
        return False
    owner = doc.get("own_visit_uid")
    if owner is not None and (not isinstance(owner, str) or not owner):
        # Upload requires matching login account; invalid values make this file permanently unuploadable.
        return False
    if not _envelope_valid(doc.get("own_char_uid"), doc.get("transport")):
        # With only upload file remaining, no log comparison is possible; verify character id and transport here too, or passing bad envelopes to upload callback
        # would fail on every startup until expiry.
        return False
    request = doc.get("request")
    usage = request.get("usage") if isinstance(request, dict) else None
    return (
        isinstance(request, dict)
        and request.get("visit_id") == visit_id
        and request.get("role") in ("host", "guest")
        and _number(request.get("started_at")) is not None
        and _number(request.get("ended_at")) is not None
        and isinstance(request.get("finalized_reason"), str) and bool(request["finalized_reason"])
        and _count(request.get("anomalies"))
        and isinstance(request.get("app_version"), str)
        # Match build_upload_doc's complete structure: every usage counter must be a nonnegative integer.
        and isinstance(usage, dict)
        and all(_count(usage.get(key)) for key in ("duration_s", *_USAGE_KEYS))
        and isinstance(request.get("lines"), list)
        # Validate each row: upload files with corrupt transcript rows cannot replace complete logs either.
        and all(isinstance(line, dict) and _valid_line(line) for line in request["lines"])
        # Ordering must also match build_upload_doc; replacing a log with out-of-order file would upload that order as authoritative.
        and request["lines"] == sorted(request["lines"], key=line_order_key)
    )


def sealed_upload_doc_usable(doc: Any, visit_id: str) -> bool:
    """Whether a loaded ``.upload.json`` may be uploaded directly (outside startup recovery).

    Same check recovery applies: anything else -- another visit's or another
    version's document, a broken envelope or request -- is left for recovery
    to reseal, quarantine or keep.
    """
    return _sealed_doc_belongs(doc, visit_id)


def sealed_upload_doc_from_another_version(doc: Any, visit_id: str) -> bool:
    """Whether an unusable ``.upload.json`` is this visit's file written by another (newer) version.

    Recovery keeps such a file as is; its owner and anomaly count can still be trusted.
    """
    return _sealed_doc_unrecognized(doc, visit_id)


def _sealed_doc_unrecognized(doc: Any, visit_id: str) -> bool:
    """Whether an invalid ``.upload.json`` looks like another version's intact document.

    A higher ``v``, or an unknown non-empty ``transport`` on an otherwise
    well-formed document, is kept for a later version instead of being
    deleted as corrupt (as with a ``state.json`` this version cannot read).
    """
    if not isinstance(doc, dict):
        return False
    version = doc.get("v")
    if isinstance(version, int) and not isinstance(version, bool) and version > UPLOAD_DOC_VERSION:
        # Cannot validate newer-version structure: exclude only files explicitly identifying another visit.
        request = doc.get("request")
        return not (isinstance(request, dict) and "visit_id" in request and request["visit_id"] != visit_id)
    transport = doc.get("transport")
    if isinstance(transport, str) and transport and transport not in _UPLOAD_TRANSPORTS:
        return _sealed_doc_belongs({**doc, "transport": min(_UPLOAD_TRANSPORTS)}, visit_id)
    return False


def _stream_doc_sync(
    spool_dir: Path, visit_id: str, finalized_reason: str | None, owner: str | None,
    char_uid: str | None = None,
) -> dict | None:
    """The document resealing the stream would produce, or None when the stream is gone or corrupt.

    Any other read error (permission, a locked file) is raised: the sealed
    document cannot be checked then, so neither file may be acted on.
    """
    try:
        records = _read_stream(visit_path(spool_dir, visit_id, UPLOAD_JSONL_SUFFIX))
    except FileNotFoundError:
        return None
    owner, char_uid = _with_header_fallback(spool_dir, visit_id, owner, char_uid)
    return build_upload_doc(records, visit_id=visit_id, finalized_reason=finalized_reason,
                            fallback_own_visit_uid=owner, fallback_own_char_uid=char_uid)


DROPPED_RECORDS_KEY = "dropped_records"
"""Envelope key of a recovery-sealed upload: records its crashed stream lost (absent when none)."""


def _dropped_line_records(records: list[dict], doc: dict) -> int:
    """Line records of the stream that ``build_upload_doc`` rejected as malformed."""
    line_records = sum(1 for record in records[1:] if record.get("kind") == "line")
    return max(0, line_records - len(doc["request"]["lines"]))


def _recorded_drops(doc: dict) -> int:
    value = doc.get(DROPPED_RECORDS_KEY)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def read_pending_upload_doc_sync(config_dir: Path, visit_id: str) -> tuple[dict, int] | None:
    """``(upload document, records dropped)`` of a visit whose transcript has not reached Servers yet (read only).

    The sealed ``.upload.json`` when :func:`sealed_upload_doc_usable` (with
    the drops recovery recorded when it sealed a crashed stream), else the document its ``.upload.jsonl`` stream would seal into
    (a crashed visit; ``finalized_reason`` ``'crash'``) with the number of
    records the rebuild had to drop (a partial trailing line, unparseable
    lines, malformed line records); None when neither exists or is usable.
    Nothing is written or deleted. Raises ``OSError`` when a file exists but
    cannot be read right now.
    """
    spool_dir = Path(config_dir) / VISIT_SPOOL_DIRNAME
    try:
        doc = _load_json(visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX))
    except ValueError:
        doc = None
    if sealed_upload_doc_usable(doc, visit_id):
        return doc, _recorded_drops(doc)
    try:
        records, dropped = _read_stream_counted(visit_path(spool_dir, visit_id, UPLOAD_JSONL_SUFFIX))
    except FileNotFoundError:
        return None
    owner, char_uid = _with_header_fallback(spool_dir, visit_id, None, None)
    try:
        doc = build_upload_doc(records, visit_id=visit_id, finalized_reason=None,
                               fallback_own_visit_uid=owner, fallback_own_char_uid=char_uid)
    except ValueError:
        return None
    if doc is None:
        return None
    return doc, dropped + _dropped_line_records(records, doc)


def _write_private_json(path: Path, doc: dict) -> None:
    atomic_write_json(path, doc)
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        # Same policy as credential files: permissions are best effort (ineffective on Windows); failure to set does not block uploads.
        logger.debug("visit recovery: chmod 0600 failed for %s: %s", path.name, exc)


class _EnvelopeIncomplete(ValueError):
    """The stream's header lacks ``own_char_uid`` and no other file of the visit records it."""


def _seal_stream_sync(
    spool_dir: Path, visit_id: str, finalized_reason: str | None, owner: str | None = None,
    char_uid: str | None = None,
) -> dict | None:
    stream = visit_path(spool_dir, visit_id, UPLOAD_JSONL_SUFFIX)
    sealed = visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX)
    owner, char_uid = _with_header_fallback(spool_dir, visit_id, owner, char_uid)
    stream_mtime_ns = stream.stat().st_mtime_ns
    records, dropped = _read_stream_counted(stream)
    doc = build_upload_doc(records, visit_id=visit_id, finalized_reason=finalized_reason,
                           fallback_own_visit_uid=owner, fallback_own_char_uid=char_uid)
    if doc is None:
        memory_bridge.diag("upload_stream_corrupt", visit_id=visit_id)
        stream.unlink(missing_ok=True)
        return None
    dropped += _dropped_line_records(records, doc)
    if dropped:
        # Sealing deletes the log: preserve discarded-row count in file so export recognizes incompleteness (upload sends only request, unaffected);
        # upload success also deletes file, so leave one local diagnostic log.
        doc[DROPPED_RECORDS_KEY] = dropped
        memory_bridge.diag("upload_stream_dropped_records", visit_id=visit_id, count=dropped)
    if doc["own_char_uid"] is None:
        # Unrecoverable character id (usually temporarily unreadable state.json): sealed file would be deleted as corrupt,
        # losing the whole transcript alongside the just-deleted log. Do not write; retain log to seal next startup.
        raise _EnvelopeIncomplete(f"no own_char_uid for the upload stream of {visit_id}")
    # Same order as finalize: atomically write .upload.json before deleting log.
    _write_private_json(sealed, doc)
    try:
        # Sealed file's 7-day retention inherits log age; using fresh mtime gives an old transcript another 7 days,
        # keeping queued reports blocked.
        os.utime(sealed, ns=(stream_mtime_ns, stream_mtime_ns))
    except OSError as exc:
        logger.warning("visit recovery: cannot carry the stream age over to %s: %s", sealed.name, exc)
    try:
        stream.unlink(missing_ok=True)
    except OSError as exc:
        # Upload file already sealed: still upload and submit report if log deletion fails (Servers idempotency by visit_id + role
        # makes subsequent resealed uploads duplicate); an undeletable file must not block this visit.
        logger.warning("visit recovery: sealed %s but cannot delete its stream: %s", sealed.name, exc)
    return doc


async def reseal_orphan_stream(config_dir: Path, visit_id: str) -> tuple[str, str | None]:
    """Seal the upload stream of a finished visit whose sealed file was never written.

    Same rules as startup recovery (state.json gives the end reason and the
    envelope fallbacks). Returns ``(status, owner)``: ``'sealed'``, ``'corrupt'``
    (the stream held no usable transcript and is gone) or ``'failed'`` (left
    as it is; try again later), with the ``own_visit_uid`` the transcript
    belongs to when known.
    """
    spool_dir = config_dir / VISIT_SPOOL_DIRNAME
    state = None
    try:
        state = await VisitSpool(config_dir, visit_id).read_state()
    except (OSError, ValueError) as exc:
        logger.warning("visit upload %s: state unreadable, resealing as crash: %s", visit_id, exc)
    reason = state["finalized"] if state else None
    owner = state["own_uid"] if state else None
    char_uid = _owner_or_none(state.get("own_char_uid")) if state else None
    try:
        header_owner, _char = await asyncio.to_thread(_with_header_fallback, spool_dir, visit_id, owner, char_uid)
    except (OSError, ValueError):
        header_owner = owner
    try:
        doc = await asyncio.to_thread(_seal_stream_sync, spool_dir, visit_id, reason, owner, char_uid)
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        logger.warning("visit upload %s: cannot reseal the stream: %s", visit_id, type(exc).__name__)
        return "failed", header_owner
    if doc is None:
        return "corrupt", header_owner
    return "sealed", doc.get("own_visit_uid") or header_owner


# ── Main flow ────────────────────────────────────────────────────────────


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _reconcile_rename_guarded(
    config_dir: Path, names: set[str], uid_of: dict[str, str] | None,
    lifecycle_guard: LifecycleGuard | None,
    reload: Callable[[], Awaitable[tuple[set[str], dict[str, str] | None]]] | None = None,
) -> frozenset[str] | None:
    """:func:`_reconcile_rename` under the renamed character's lifecycle guard.

    Recovery runs in the background: without the guard a live rename of the
    same character could interleave between the roster and the spool
    migrations and strand spools under an intermediate name.
    """
    if lifecycle_guard is None:
        return await _reconcile_rename(config_dir, names, uid_of)
    for _attempt in range(3):
        try:
            marker = await read_roster_marker(config_dir, "pending_rename")
        except RosterCorruptError:
            return await _reconcile_rename(config_dir, names, uid_of)
        if not isinstance(marker, dict):
            return await _reconcile_rename(config_dir, names, uid_of)
        uids = {marker["uid"]} if isinstance(marker.get("uid"), str) and marker.get("uid") else set()
        for name in (marker.get("old"), marker.get("new")):
            if uid_of is not None and isinstance(name, str) and name in uid_of:
                uids.add(uid_of[name])
        if not uids:
            return await _reconcile_rename(config_dir, names, uid_of)
        async with lifecycle_guard(sorted(uids)):
            # Another character's rename may replace the marker while waiting for guard: guard was acquired for old marker,
            # not new marker's character. Release on change and reacquire for new marker.
            try:
                current = await read_roster_marker(config_dir, "pending_rename")
            except RosterCorruptError:
                return _ALL_NAMES
            if current != marker:
                if reload is not None:
                    names, uid_of = await reload()
                continue
            # Reread roster under guard: character may rename/delete while waiting; a pre-guard snapshot can infer wrong direction.
            if reload is not None:
                names, uid_of = await reload()
            # Recheck against the guard-acquisition marker during reconciliation too (marker may change while rereading roster).
            result = await _reconcile_rename(config_dir, names, uid_of, expected=marker)
            if result is _MARKER_CHANGED:
                continue
            return result
    logger.warning("visit recovery: pending_rename kept changing while waiting for its guard, deferred")
    return _ALL_NAMES


def _in_flight(config_dir: Path, visit_id: str, is_live: Callable[[str], bool]) -> bool:
    """Whether ``visit_id`` is still being written in this process, so none of its files may be touched.

    True while its ``VisitRuntime`` is registered (``is_live``) or while its
    spool is still held open for appends: a runtime can already be
    unregistered while its writer and finalize writes are queued.
    """
    spool_dir = Path(config_dir) / VISIT_SPOOL_DIRNAME
    return is_live(visit_id) or is_spool_open(visit_path(spool_dir, visit_id, SPOOL_SUFFIX))


async def _cleanup_outboxes(config_dir: Path, live: Callable[[str], bool]) -> None:
    spool_dir = Path(config_dir) / VISIT_SPOOL_DIRNAME
    for visit_id in await VisitSpool.list_visit_ids(config_dir, (OUTBOX_SUFFIX,)):
        if live(visit_id):
            continue
        path = visit_path(spool_dir, visit_id, OUTBOX_SUFFIX)
        try:
            await asyncio.to_thread(path.unlink, True)
        except OSError as exc:
            logger.warning("visit recovery: cannot delete %s: %s", path.name, exc)


async def _has_digestable_lines(spool: VisitSpool, state: dict) -> bool:
    if not is_digestable(state):
        return False
    contents = await spool.read_back()
    return contents.header is not None and any(
        str(line.get("text") or "").strip() for line in contents.lines
    )


async def _recover_visit(
    visit_id: str,
    *,
    config_dir: Path,
    render_chips: RenderChips,
    resolve_char_name: ResolveCharName,
    spawn_background: SpawnBackground | None,
    summary_llm: SummaryLLM | None,
    family_names: Iterable[str],
    resume_diary_commit: ResumeDiaryCommit | None,
    client: ScopedMemoryClient | None,
    report: RecoveryReport,
    skip_names: frozenset[str] = frozenset(),
    local_char_names: Iterable[str] = (),
) -> None:
    spool = VisitSpool(config_dir, visit_id)
    state = await spool.read_state()
    if state is None:
        return
    own_char = await resolve_char_name(state["own_char_uid"])
    if not own_char:
        # Character deleted: retirement owns these visit files; show no chip and write nothing here.
        return
    if own_char in skip_names or state["own_char"] in skip_names:
        # Character rename remains unreconciled: name-based roster lookup may misidentify entries; retain until next startup.
        return
    choice = state["debrief_choice"]
    status = None
    show_chip = False
    if state["finalized"] is None:
        # Show chip only with digestible utterances (memory-disabled visits have no .jsonl); write no private-chat memories.
        # Crash marker and chip marker share one atomic write; a kill between separate steps would prevent future chip detection.
        show_chip = choice in (None, "ask_later") and await _has_digestable_lines(spool, state)
        changes: dict[str, Any] = {"finalized": "crash"}
        if show_chip:
            changes["debrief_chip_pending"] = True
        state = await spool.update_state(**changes)
        report.crashed.append(visit_id)
        status = "interrupted"
    elif state["finalized"] == "shutdown" and choice is None:
        # Fallback: old version omitted ask_later on shutdown or was killed before writing it.
        if await _has_digestable_lines(spool, state):
            state = await spool.update_state(debrief_choice="ask_later")
            show_chip = True
    elif choice is None:
        # Finalized but user undecided: existing chip marker reappears every startup (like ask_later; crashed visits
        # do not show only once). Without marker, any end reason (wrap_up / peer_left, etc.) may have written finalized
        # before a kill or come from an older crashed visit; record and show if digestible utterances exist.
        show_chip = state["debrief_chip_pending"] or await _has_digestable_lines(spool, state)
    elif choice == "ask_later":
        show_chip = True
    if choice in ("generating:diary", "preview:diary", "committing:diary", "commit_failed:diary"):
        # Crash during preview generation / persisted preview awaiting confirmation / two-step commit underway (possibly backing off) / permanent
        # write failure: no LLM calls or writes; replay corresponding block via bind (committing shows 'writing').
        show_chip = True
    if show_chip and state["finalized"] == "crash":
        # Every replay of a crashed visit includes 'unexpected interruption', not only initial crash marking.
        status = "interrupted"
    if show_chip:
        if not state["debrief_chip_pending"]:
            state = await spool.update_state(debrief_chip_pending=True)
        report.chips.append(visit_id)
        try:
            await render_chips(visit_id, own_char=own_char, status=status)
        except Exception as exc:  # noqa: BLE001 - If undeliverable, wait for bind replay.
            logger.warning("visit recovery: render_chips failed for %s: %r", visit_id, exc)
    if choice == "committing:diary" and resume_diary_commit is not None:
        await _maybe_await(resume_diary_commit(spool, state))

    async def digest() -> Any:
        return await commit_visit_region(
            spool, resolve_char_name=resolve_char_name, client=client, family_names=family_names,
            local_char_names=local_char_names,
        )

    async def summary() -> Any:
        # Register as this visit's running summary: new visits for the same pair wait for this task during handover instead of launching another LLM call.
        return await track_last_summary(visit_id, commit_last_summary(
            spool, llm=summary_llm, resolve_char_name=resolve_char_name, family_names=family_names,
        ))

    jobs: list[tuple[str, Callable[[], Awaitable[Any]]]] = []
    if is_digestable(state):
        jobs.append(("digest", digest))
    if not state["last_summary_done"] and summary_llm is not None:
        jobs.append(("summary", summary))
    for kind, factory in jobs:
        if spawn_background is not None:
            result = await _maybe_await(spawn_background(state["own_char_uid"], factory))
        else:
            result = await factory()
        ok = bool(getattr(result, "ok", result))
        (report.digests if kind == "digest" else report.summaries)[visit_id] = ok


async def _upload_pending(
    config_dir: Path,
    *,
    live: Callable[[str], bool],
    upload_transcript: UploadTranscript | None,
    submit_report: SubmitReport | None,
    report: RecoveryReport,
    retry_later: Callable[[str], Any] | None = None,
) -> set[str]:
    """Retry pending uploads; return the visit ids whose upload is still pending.

    ``retry_later(visit_id)`` re-arms the in-process retry for a visit whose
    files could not be read or written right now (the upload callback, which
    normally does that, is not reached for it).
    """
    spool_dir = config_dir / VISIT_SPOOL_DIRNAME
    pending: set[str] = set()
    sealed = set(await VisitSpool.list_visit_ids(config_dir, (UPLOAD_JSON_SUFFIX,)))
    # Upload file verified against log this pass (resealed / matching log): envelope already follows log authority.
    stream_checked: set[str] = set()
    for visit_id in await VisitSpool.list_visit_ids(config_dir, (UPLOAD_JSONL_SUFFIX,)):
        if live(visit_id):
            continue
        stream = visit_path(spool_dir, visit_id, UPLOAD_JSONL_SUFFIX)
        state = None
        try:
            state = await VisitSpool(config_dir, visit_id).read_state()
        except (OSError, ValueError) as exc:
            logger.warning("visit recovery: state of %s unreadable, upload marked crash: %s", visit_id, exc)
        reason = state["finalized"] if state else None
        owner = state["own_uid"] if state else None
        char_uid = _owner_or_none(state.get("own_char_uid")) if state else None
        reseal_reason = reason
        if visit_id in sealed:
            # Crash after writing .upload.json but before deleting log: upload file is the visit's actual sealed copy;
            # retaining log causes resealing and duplicate upload after success. Verify upload readability before deleting log;
            # if upload file corrupt, reseal from log (overwrite corruption), currently the only complete copy.
            try:
                sealed_doc = await asyncio.to_thread(
                    _load_json, visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX),
                )
            except (OSError, ValueError):
                sealed_doc = None
            belongs = _sealed_doc_belongs(sealed_doc, visit_id)
            if belongs:
                # Every resealable field must match file (envelope, transcript rows, usage, timestamps): missing-row / edited
                # files must not replace complete logs, which cannot be recovered after deletion. Exclude chunk progress added by
                # upload callbacks, or failed log deletion would trigger resealing and reset progress on every recovery pass.
                # End reason must match a definite reason in state; if state unreadable or crash (normal finalization crashed after
                # upload write but before state.finalized, and this recovery pass initially marked crash),
                # retain file's reason instead of resealing normally ended visits as crash.
                sealed_reason = sealed_doc["request"]["finalized_reason"]
                # Use the same reason when resealing mismatched files too: valid file format with other field mismatches
                # must not change normally ended visits to crash either.
                reseal_reason = sealed_reason if reason in (None, "crash") else reason
                try:
                    expected = await asyncio.to_thread(
                        _stream_doc_sync, spool_dir, visit_id, reseal_reason, owner, char_uid,
                    )
                except (OSError, ValueError, TypeError, OverflowError) as exc:
                    # Log exists but temporarily unreadable (permissions / locked), or rebuilding fails (same exceptions as sealing below):
                    # cannot compare; leave both unchanged and skip upload this pass. Do not assume log absence and proceed,
                    # risking deletion of complete log and upload of missing-row files; do not let exceptions abort all uploads/reports either.
                    logger.warning("visit recovery: stream of %s unreadable, upload deferred: %s", visit_id, exc)
                    sealed.discard(visit_id)
                    pending.add(visit_id)
                    continue
                belongs = expected is None or all(sealed_doc.get(name) == value for name, value in expected.items())
                if belongs and expected is not None:
                    stream_checked.add(visit_id)
            if belongs:
                try:
                    await asyncio.to_thread(stream.unlink, True)
                except OSError as exc:
                    # Still upload on deletion failure: Servers is idempotent by visit_id + role, so resealing residual logs
                    # and uploading next time only yields duplicate. Blocking upload would let a permanently undeletable file
                    # prevent this visit's transcript and queued report from ever being submitted.
                    logger.warning("visit recovery: cannot delete stale stream %s: %s", stream.name, exc)
                continue
            # Remove from pending uploads first: failed resealing must not pass corrupt / wrong-visit files to upload callback.
            sealed.discard(visit_id)
            logger.warning("visit recovery: sealed upload of %s unreadable or not this visit's, resealing from its stream",
                           visit_id)
        # Transcript recovery uploads do not depend on finalized: if log exists and upload file was not written, build from log.
        try:
            # Failed sealing may delete corrupt logs; first capture room account from log header for reasons if state.json unreadable.
            stream_owner = await asyncio.to_thread(_upload_stream_owner_sync, spool_dir, visit_id)
            doc = await asyncio.to_thread(_seal_stream_sync, spool_dir, visit_id, reseal_reason, owner, char_uid)
        except (OSError, ValueError, TypeError, OverflowError) as exc:
            # Skip only an unreadable/unwritable log, without blocking other visits' uploads and reports.
            logger.warning("visit recovery: cannot seal %s: %s", stream.name, exc)
            pending.add(visit_id)
            if (retry_later is not None and isinstance(exc, OSError)
                    and not await asyncio.to_thread(visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX).exists)):
                # If only log remains, background can reseal it. If a corrupt sealed file also remains, background exits
                # on that unusable file instead of resealing; leave for next startup recovery.
                retry_later(visit_id)
            continue
        if doc is not None:
            sealed.add(visit_id)
            stream_checked.add(visit_id)
        elif not await _drop_corrupt_sealed(spool_dir, visit_id):
            # Corrupt log and no valid upload file for this visit: block report if that file cannot be deleted yet.
            pending.add(visit_id)
        else:
            # Corrupt log with no valid sealed file: transcript can never upload; record reason on queued report.
            # (Take ownership from state.json account; do not affect another account's report on shared computers.)
            await _mark_report_transcript_unavailable(
                config_dir, visit_id, "corrupt", report, owner=_owner_or_none(owner) or stream_owner)
    for visit_id in sorted(sealed):
        if live(visit_id):
            # In-flight visit's transcript not yet uploaded: its queued report must also wait.
            pending.add(visit_id)
            continue
        path = visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX)
        try:
            doc = await asyncio.to_thread(_load_json, path)
        except OSError as exc:
            logger.warning("visit recovery: pending upload %s unreadable: %s", path.name, exc)
            pending.add(visit_id)
            if retry_later is not None:
                # Temporarily unreadable (Windows sharing violation): this pass's pending state disappears on return, so hand off to background instead of waiting for restart.
                retry_later(visit_id)
            continue
        except ValueError:
            doc = False
        if doc is None:
            continue
        if not _sealed_doc_belongs(doc, visit_id):
            if _sealed_doc_unrecognized(doc, visit_id):
                # Higher version / unknown transport probably means intact newer-version file after downgrade;
                # upgrading can upload it. Like unrecognized state.json, retain for later instead of deleting as corruption.
                logger.warning("visit recovery: pending upload %s was written by another version, kept", path.name)
                pending.add(visit_id)
                continue
            # Corrupt / wrong-visit upload file without recoverable log: transcript unrecoverable like corrupt logs,
            # so delete without passing to upload callback, then submit queued reports normally.
            if not await _drop_corrupt_sealed(spool_dir, visit_id):
                pending.add(visit_id)
            else:
                await _mark_report_transcript_unavailable(
                    config_dir, visit_id, "corrupt", report, owner=await _state_owner(config_dir, visit_id))
            continue
        if visit_id not in stream_checked:
            matches, state_owner = await _sealed_matches_state(config_dir, visit_id, doc)
            if not matches:
                # Only upload file remains: character id or room account disagrees with this visit's state.json (another character /
                # account's file). Submission would use wrong identity; treat as wrong-visit file.
                if not await _drop_corrupt_sealed(spool_dir, visit_id):
                    pending.add(visit_id)
                else:
                    await _mark_report_transcript_unavailable(
                        config_dir, visit_id, "corrupt", report, owner=state_owner)
                continue
            if doc.get("own_visit_uid") is None and state_owner is not None:
                # Legacy ownerless sealed file: supplement from this visit's state.json account, or account-only upload callback
                # can never match a logged-in account.
                doc = {**doc, "own_visit_uid": state_owner}
        try:
            before = await asyncio.to_thread(path.stat)
        except OSError:
            before = None
        rejected = doc.get("rejected")
        terminal_reason: str | None = None
        if isinstance(rejected, str) and rejected:
            # Previous terminal rejection with local deletion failure: no full reupload; continue deleting and submitting reports.
            terminal_reason = rejected
        else:
            if upload_transcript is None:
                pending.add(visit_id)
                continue
            try:
                outcome = await upload_transcript(visit_id, doc)
                ok = bool(outcome)
                if isinstance(outcome, str) and outcome:
                    # Terminal rejection: local file may be deleted, but this transcript can never reach Servers.
                    terminal_reason = outcome
            except Exception as exc:  # noqa: BLE001 - Retain files on any failure and retry later.
                logger.warning("visit recovery: upload of %s failed: %r", visit_id, exc)
                ok = False
                if retry_later is not None:
                    # Callback failed before scheduling its own background retry (temporary local preparation read/write error): recovery runs once, so hand off to background.
                    retry_later(visit_id)
            if terminal_reason is None:
                # Terminal rejection is not upload success: uploads True means only that transcript reached Servers.
                report.uploads[visit_id] = ok
            if not ok:
                if before is not None:
                    # Chunk upload may persist progress then return False; retention uses mtime, so retries must not
                    # extend 7-day expiry indefinitely, keeping failed uploads alive and queued reports waiting forever.
                    await asyncio.to_thread(_restore_mtime, path, before)
                pending.add(visit_id)
                continue
        if terminal_reason is not None:
            report.rejected[visit_id] = terminal_reason
            # Record transcript unavailability on queued report first (design §4.7), then delete upload file: this file is the sole
            # durable terminal-rejection record. Deleting first without report marking (or exiting between steps)
            # would submit the report without its reason on next startup. Retain upload file marked rejected if marking fails and retry later.
            if not await _mark_report_transcript_unavailable(
                    config_dir, visit_id, terminal_reason, report, owner=_owner_or_none(doc.get("own_visit_uid"))):
                if terminal_reason != rejected:
                    await asyncio.to_thread(_mark_sealed_rejected, path, terminal_reason, before)
                # Supplement this pass's submission with report's reason.
                await _submit_report(config_dir, visit_id, submit_report, report)
                continue
        try:
            await asyncio.to_thread(path.unlink, True)
        except OSError as exc:
            # Uploaded but locally undeletable: do not block other visits or classify as unuploaded; Servers accepted it,
            # so submit queued report normally, retaining file until later duplicate receipt and deletion.
            logger.warning("visit recovery: uploaded %s but cannot delete it: %s", path.name, exc)
            if terminal_reason is not None and terminal_reason != rejected:
                # Undeletable after terminal rejection: mark rejection in file so next startup skips full reupload and repeat rejection.
                await asyncio.to_thread(_mark_sealed_rejected, path, terminal_reason, before)
        # After successful transcript upload (or terminal rejection with reason recorded), submit this visit's queued report.
        await _submit_report(config_dir, visit_id, submit_report, report)
    return pending


async def _pending_upload_owner(config_dir: Path, visit_id: str) -> str | None:
    """Owner of the visit's pending transcript: the sealed upload's own, else ``state.json``.

    The sealed upload is self-contained and names its own account (a file a
    newer version wrote is kept as is even when ``state.json`` names another
    account); ``state.json`` covers legacy ownerless files and a lone stream.
    """
    try:
        doc = await asyncio.to_thread(
            _load_json, visit_path(config_dir / VISIT_SPOOL_DIRNAME, visit_id, UPLOAD_JSON_SUFFIX))
    except (OSError, ValueError):
        doc = None
    # Trust only files belonging to this visit, not wrong-visit / corrupt temporarily undeletable files' accounts. Current-version files must also match state.json
    # (character/account mismatch means rejected file; use state's account). This version cannot validate newer files, so trust their own account.
    if isinstance(doc, dict) and _sealed_doc_belongs(doc, visit_id):
        matches, state_owner = await _sealed_matches_state(config_dir, visit_id, doc)
        owner = _owner_or_none(doc.get("own_visit_uid")) if matches else state_owner
    elif isinstance(doc, dict) and _sealed_doc_unrecognized(doc, visit_id):
        owner = _owner_or_none(doc.get("own_visit_uid"))
    else:
        owner = None
    if owner is None:
        owner = await _state_owner(config_dir, visit_id)
    if owner is None:
        # No trusted sealed file and unreadable state.json: this visit's upload-log header records the room account.
        owner = await asyncio.to_thread(_upload_stream_owner_sync, config_dir / VISIT_SPOOL_DIRNAME, visit_id)
    return owner


async def pending_upload_owner(config_dir: Path, visit_id: str) -> str | None:
    """Community ``visit_uid`` owning the visit's pending transcript (the rule uploads follow).

    The sealed upload's own owner, else ``state.json``, else the upload
    stream header: legacy ownerless files are attributed the same way the
    upload path backfills them.
    """
    return await _pending_upload_owner(Path(config_dir), visit_id)


def _upload_stream_owner_sync(spool_dir: Path, visit_id: str) -> str | None:
    """``own_visit_uid`` named by this visit's upload stream header; None when absent or not this visit's."""
    try:
        with open(visit_path(spool_dir, visit_id, UPLOAD_JSONL_SUFFIX), "rb") as handle:
            first = handle.readline()
    except OSError:
        return None
    try:
        header = json.loads(first)
    except (ValueError, RecursionError):
        return None
    if not isinstance(header, dict) or header.get("kind") != "header" or header.get("visit_id") != visit_id:
        return None
    return _owner_or_none(header.get("own_visit_uid"))


async def _state_owner(config_dir: Path, visit_id: str) -> str | None:
    """``own_uid`` recorded in the visit's ``state.json``; None when absent or unreadable."""
    try:
        state = await VisitSpool(config_dir, visit_id).read_state()
    except (OSError, ValueError):
        return None
    return _owner_or_none(state.get("own_uid")) if state else None


async def _drop_corrupt_sealed(spool_dir: Path, visit_id: str) -> bool:
    """Delete an invalid ``.upload.json`` that has no stream to reseal from; False if it is still there."""
    path = visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX)
    if not await asyncio.to_thread(path.exists):
        return True
    memory_bridge.diag("upload_doc_corrupt", visit_id=visit_id)
    try:
        await asyncio.to_thread(path.unlink, True)
    except OSError as exc:
        logger.warning("visit recovery: cannot delete corrupt upload %s: %s", path.name, exc)
        return False
    return True


async def sealed_upload_doc_matches_state(
    config_dir: Path, visit_id: str, doc: dict,
) -> tuple[bool | None, str | None]:
    """``(matches, state owner)``: whether a usable sealed document agrees with the visit's ``state.json``.

    Its character id, and its account when both name one, must be the ones
    the state records; ``(True, None)`` when the visit has no state.
    ``(None, None)`` when the state exists but cannot be read or validated
    right now: the check could not be made (unlike startup recovery, which
    treats that like no state).
    """
    try:
        state = await VisitSpool(config_dir, visit_id).read_state()
    except (OSError, ValueError):
        return None, None
    return _doc_matches_state(state, doc)


async def _sealed_matches_state(config_dir: Path, visit_id: str, doc: dict) -> tuple[bool, str | None]:
    """``(matches, state owner)`` for a sealed upload checked against this visit's ``state.json``.

    ``matches`` is False when the document's ``own_char_uid``, or its non-null
    ``own_visit_uid``, differs from the one the state records (character ids
    are machine-local and shared across accounts, so both are checked). The
    state's ``own_uid`` is returned so a legacy document without an owner can
    take it. ``(True, None)`` when the state is absent or unreadable.
    """
    try:
        state = await VisitSpool(config_dir, visit_id).read_state()
    except (OSError, ValueError):
        return True, None
    return _doc_matches_state(state, doc)


def _doc_matches_state(state: dict | None, doc: dict) -> tuple[bool, str | None]:
    if not state:
        return True, None
    char_uid = _owner_or_none(state.get("own_char_uid"))
    owner = _owner_or_none(state.get("own_uid"))
    if char_uid is not None and doc.get("own_char_uid") != char_uid:
        return False, owner
    sealed_owner = doc.get("own_visit_uid")
    if owner is not None and sealed_owner is not None and sealed_owner != owner:
        return False, owner
    return True, owner


def _mark_sealed_rejected(path: Path, reason: str, before: os.stat_result | None) -> None:
    try:
        doc = _load_json(path)
        if not isinstance(doc, dict):
            return
        _write_private_json(path, {**doc, "rejected": reason})
    except (OSError, ValueError) as exc:
        logger.warning("visit recovery: cannot mark %s rejected: %s", path.name, exc)
        return
    if before is not None:
        # Recording a marker is not a retry: calculate retention from original mtime.
        _restore_mtime(path, before)


def _restore_mtime(path: Path, before: os.stat_result) -> None:
    try:
        current = path.stat()
        if current.st_mtime_ns > before.st_mtime_ns:
            os.utime(path, ns=(current.st_atime_ns, before.st_mtime_ns))
    except OSError as exc:
        logger.warning("visit recovery: cannot restore the mtime of %s: %s", path.name, exc)


def _load_json(path: Path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except RecursionError as exc:
        raise ValueError("too deeply nested") from exc


async def _submit_report(
    config_dir: Path, visit_id: str, submit_report: SubmitReport | None, report: RecoveryReport,
    *, transcript_gated: bool = False, retry_later: Callable[[str], Any] | None = None,
) -> None:
    if submit_report is None:
        return
    path = visit_path(config_dir / VISIT_REPORTS_DIRNAME, visit_id, ".json")
    try:
        doc = await asyncio.to_thread(_load_json, path)
    except (OSError, ValueError) as exc:
        logger.warning("visit recovery: queued report %s unreadable: %s", path.name, exc)
        if retry_later is not None and isinstance(exc, OSError):
            # Temporarily unreadable (locked): report-only visits have no upload task to retry, so hand off to background until readable.
            retry_later(visit_id)
        return
    if doc is None:
        return
    if not isinstance(doc, dict) or doc.get("visit_id") != visit_id:
        # Contents and filename identify different visits (copied / edited): submission reports the wrong visit and deletes this file after acceptance.
        # originally intended report. Neither submit nor delete; rename into quarantine. Leaving it here blocks later reports as already queued
        # and warns again every startup.
        await asyncio.to_thread(_quarantine_report, path, visit_id)
        return
    if transcript_gated and doc.get("include_transcript") is not False:
        # Transcript not uploaded: reports attaching it wait; explicitly unattached reports bypass the transcript gate. If pending transcript belongs to another
        # known account on a shared computer, it is not this report's side and need not be awaited.
        transcript_owner = await _pending_upload_owner(config_dir, visit_id)
        report_owner = doc.get("own_visit_uid")
        if not (transcript_owner and report_owner and report_owner != transcript_owner):
            return
    unavailable = report.transcript_unavailable.get(visit_id)
    unavailable_owner = report.unavailable_owner.get(visit_id)
    if unavailable_owner and doc.get("own_visit_uid") and doc["own_visit_uid"] != unavailable_owner:
        # Reason belongs to another account's transcript side on a shared computer; exclude from this report.
        unavailable = None
    if unavailable and doc.get("include_transcript") is not False and not doc.get("transcript_unavailable"):
        # Report-file unavailability marker write failed (full disk / permissions): still include in submission so Servers knows transcript is gone.
        doc = {**doc, "transcript_unavailable": unavailable}
    try:
        ok = bool(await submit_report(visit_id, doc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("visit recovery: report of %s failed: %r", visit_id, exc)
        ok = False
    report.reports[visit_id] = ok
    if ok:
        # Delete report only after Servers acceptance, under the per-path lock shared by rewriters; delete only the submitted copy,
        # since another undelivered report may replace it during submission.
        try:
            await asyncio.to_thread(_unlink_same_report_locked, path, doc)
        except OSError as exc:
            logger.warning("visit recovery: report %s accepted but cannot delete it: %s", path.name, exc)


REPORT_IDENTITY = ("visit_id", "own_account", "own_visit_uid", "queued_at")
"""Fields that tell one queued report from a later one for the same visit."""


def _unlink_same_report_locked(path: Path, submitted: dict) -> None:
    with path_lock(path):
        try:
            current = _load_json(path)
        except (OSError, ValueError):
            return
        if isinstance(current, dict) and all(current.get(k) == submitted.get(k) for k in REPORT_IDENTITY):
            path.unlink(missing_ok=True)


def _report_belongs(doc: Any, visit_id: str) -> bool:
    return isinstance(doc, dict) and doc.get("visit_id") == visit_id


def _quarantine_report(path: Path, visit_id: str) -> None:
    with path_lock(path):
        # Reread under lock: between initial read and lock acquisition, file may become this visit's own report.
        try:
            doc = _load_json(path)
        except (OSError, ValueError):
            return
        if doc is None or _report_belongs(doc, visit_id):
            return
        target = path.with_name(f"{path.name}.mismatch")
        n = 1
        while target.exists():
            # Do not overwrite earlier quarantine copies; those are also undelivered reports.
            target = path.with_name(f"{path.name}.{n}.mismatch")
            n += 1
        memory_bridge.diag("report_visit_mismatch", visit_id=visit_id)
        try:
            os.replace(path, target)
        except OSError as exc:
            logger.warning("visit recovery: queued report %s does not belong to its visit, cannot move it aside: %s",
                           path.name, exc)
            return
        logger.warning("visit recovery: queued report %s does not belong to its visit, moved to %s",
                       path.name, target.name)


def _expired_upload_visits(deleted: Iterable[Path]) -> set[str]:
    out = set()
    for path in deleted:
        for suffix in (UPLOAD_JSON_SUFFIX, UPLOAD_JSONL_SUFFIX):
            if path.name.endswith(suffix):
                out.add(path.name[: -len(suffix)])
                break
    return out


async def _mark_report_transcript_unavailable(
    config_dir: Path, visit_id: str, reason: str, report: RecoveryReport, *, owner: str | None = None,
) -> bool:
    """Record on a queued report why its transcript will never be uploaded.

    The reason is also kept on ``report`` so the submission of this pass
    carries it even when the report file cannot be rewritten. Returns False
    only when the report file could not be rewritten (nothing queued, a
    report of another visit or an existing marker count as done). With
    ``owner`` (the transcript's ``own_visit_uid``), a report queued by a
    different known account is left alone: on a computer shared by several
    community accounts it is the other participant's report.
    """
    path = visit_path(config_dir / VISIT_REPORTS_DIRNAME, visit_id, ".json")
    try:
        applies = await asyncio.to_thread(_mark_report_sync, path, visit_id, reason, owner)
    except (OSError, ValueError) as exc:
        # Cannot persist: supplement this pass's submission from report. Ownership not yet verified; record together and compare before submission.
        report.transcript_unavailable.setdefault(visit_id, reason)
        if owner is not None:
            report.unavailable_owner.setdefault(visit_id, owner)
        logger.warning("visit recovery: cannot mark report %s transcript_unavailable: %s", path.name, exc)
        return False
    if applies:
        report.transcript_unavailable.setdefault(visit_id, reason)
    return True


def _mark_report_sync(path: Path, visit_id: str, reason: str, owner: str | None = None) -> bool:
    """Write the reason into the queued report; False when that report is another known account's."""
    # Read/write share one per-path lock; if report is accepted/deleted or abandoned between reading and writing, atomic write would recreate it,
    # causing another submission.
    with path_lock(path):
        doc = _load_json(path)
        if isinstance(doc, dict) and owner is not None and doc.get("own_visit_uid") \
                and doc["own_visit_uid"] != owner:
            # Another account's queued report on shared computer: this transcript is not its side; do not record its reason (record if ownership unknown).
            return False
        if isinstance(doc, dict) and doc.get("include_transcript") is False:
            # Unattached reports do not record transcript reasons or supplement them at submission.
            return False
        if not _report_belongs(doc, visit_id) or doc.get("transcript_unavailable"):
            # Wrong-visit report (copied / edited file) must not receive this transcript's reason.
            return True
        if not path.exists():
            # Deleters bypassing this lock: recheck existence immediately before writing.
            return True
        _write_private_json(path, {**doc, "transcript_unavailable": reason})
    return True


async def _submit_reports(
    config_dir: Path, *, skip: set[str], submit_report: SubmitReport | None, report: RecoveryReport,
    retry_later: Callable[[str], Any] | None = None,
) -> None:
    directory = config_dir / VISIT_REPORTS_DIRNAME
    try:
        names = await asyncio.to_thread(os.listdir, directory)
    except FileNotFoundError:
        return
    for name in sorted(names):
        visit_id = name[: -len(".json")] if name.endswith(".json") else ""
        if not VISIT_ID_RE.fullmatch(visit_id) or visit_id in report.reports:
            continue
        await _submit_report(config_dir, visit_id, submit_report, report, transcript_gated=visit_id in skip,
                             retry_later=retry_later)


async def visit_spool_recovery(
    render_chips: RenderChips,
    upload_transcript: UploadTranscript | None = None,
    *,
    is_live: Callable[[str], bool],
    spawn_background: SpawnBackground | None = None,
    config_dir: str | Path | None = None,
    resolve_char_name: ResolveCharName | None = None,
    list_char_names: Callable[[], Awaitable[Iterable[str]]] | None = None,
    summary_llm: SummaryLLM | None = None,
    family_names: Iterable[str] = (),
    submit_report: SubmitReport | None = None,
    retry_later: Callable[[str], Any] | None = None,
    resume_diary_commit: ResumeDiaryCommit | None = None,
    void_pending: VoidPending | None = None,
    lifecycle_guard: LifecycleGuard | None = None,
    retire_persona: RetirePersona | None = None,
    config_lock: Callable[[], AsyncContextManager[Any]] | None = None,
    client: ScopedMemoryClient | None = None,
    now: float | None = None,
) -> RecoveryReport:
    """Run one startup recovery pass over the visit files (see the module docstring).

    ``is_live(visit_id)`` (required) tells visits of a ``VisitRuntime`` alive
    in this process (their files are never touched): recovery runs as a
    background task, so a visit may start before it reaches the uploads, and
    sealing its stream or deleting its outbox would truncate it. A visit whose
    spool is still held open for appends counts as live as well (its runtime
    may already be unregistered while its writes are queued). ``spawn_background`` routes the
    digest / summary commits through the character's visit background-task
    entry. ``retry_later(visit_id)`` (``schedule_visit_retry``) re-arms the
    background upload retry of a visit whose files were transiently
    unreadable. ``summary_llm`` is required for last-visit summaries (without it
    they wait for a later pass). ``lifecycle_guard`` is the clearing
    endpoints' rename / delete guard, held while forgets are replayed.
    ``retire_persona`` deletes a deleted character's visit persona and
    ``config_lock`` is the character-config mutation lock each pending
    retirement is settled under. The
    other callbacks are optional and their
    steps are skipped (files kept) when missing. Independent of
    ``visitMemoryEnabled`` and of the ``NEKO_VISIT_ENABLED`` release switch.
    """
    report = RecoveryReport()
    if config_dir is None:
        from utils.config_manager import get_config_manager

        config_dir = get_config_manager().config_dir
    config_dir = Path(config_dir)
    resolve = resolve_char_name or local_chars.resolve_char_name
    live = is_live

    local_names: frozenset[str] = frozenset()

    def in_flight(visit_id: str) -> bool:
        return _in_flight(config_dir, visit_id, is_live)
    # Reconcile rename before erasure replay: erasure looks up roster by current name; incomplete migration leaves entries
    # under old name, so remove_char 'succeeds' without deleting anything, then migration moves entries and summaries to new name.
    try:
        # Strictly validate character config regardless of roster source: ordinary loading silently drops invalid entries and returns a partial roster,
        # misleading rename direction and causing erasure replay below to mistake unparseable names for deleted characters.
        # Unreadable config / invalid entries defer this entire section (raise into the branch below).
        async def load_names() -> tuple[set[str], dict[str, str] | None]:
            await local_chars.ensure_characters_readable()
            uid_of = None if list_char_names is not None else await local_chars.load_local_characters()
            names = set(await list_char_names()) if list_char_names is not None else set(uid_of)
            return names, uid_of

        names, uid_of = await load_names()
        # Protect other local cats' names too: peers naming themselves after another local cat must not replace speaker labels.
        local_names = frozenset(names)
    except Exception as exc:  # noqa: BLE001 - Recovery sections fail independently.
        logger.error("visit recovery: character config unreadable, rename / retirement deferred: %r", exc)
        load_names = None
    if load_names is not None:
        try:
            # Character deletion committed but retirement incomplete (failure / crash): complete visit, roster, and visit-persona retirement,
            # or a new same-name character gets old chips and the name stays reserved. Before rename reconciliation, since renames (including cloud imports)
            # may migrate another character's entries into this name, which later name-based retirement would delete too.
            report.retired = await replay_retires(
                config_dir, load_names, retire_persona=retire_persona, config_lock=config_lock,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("visit recovery: retirement replay failed: %r", exc)
            report.retired = False
        try:
            # Retirement replay takes/releases config lock per entry and may be slow; cloud downloads can restore a character under a third name meanwhile.
            # Reread roster under lock before deciding which queued renames belong to deleted characters; stale snapshots would discard restored renames as deleted.
            if config_lock is not None:
                async with config_lock():
                    names, uid_of = await load_names()
                    await drop_deleted_queued_renames(config_dir, uid_of)
            else:
                names, uid_of = await load_names()
                await drop_deleted_queued_renames(config_dir, uid_of)
            # Cloud snapshot renames several characters: queue the rest for sequential reconciliation under each character's guard with the same rules.
            unsettled = await reconcile_renames(
                config_dir, names, uid_of,
                reconcile=lambda: _reconcile_rename_guarded(config_dir, names, uid_of, lifecycle_guard, load_names),
                # Already removed under lock using refreshed roster; repeating outside lock with that roster would treat newly recorded queued renames as deleted.
                drop_deleted=False,
            )
        except Exception as exc:  # noqa: BLE001 - Recovery sections fail independently.
            logger.error("visit recovery: rename reconciliation failed: %r", exc)
            unsettled = _ALL_NAMES
    else:
        report.retired = False
        unsettled = _ALL_NAMES
    if unsettled is not _ALL_NAMES:
        # If active-slot rename cannot reconcile, queued renames have not run either; their names are unreconciled too, so erasure replay and
        # per-visit recovery must skip them (new-name lookup misses old-name entries and 'succeeds' doing nothing).
        try:
            held = pending_rename_names(*await read_roster_markers(
                config_dir, "pending_rename", "pending_rename_queue"))
        except RosterCorruptError:
            held = None
        unsettled = _ALL_NAMES if held is None else frozenset(unsettled | held)
    names_settled = unsettled is not _ALL_NAMES
    report.renamed = unsettled == frozenset()
    if names_settled:
        try:
            # An unreconciled rename concerns its two names only; erasure replay defers those itself.
            report.forgets_clean = await replay_forgets(
                config_dir, resolve_char_name=resolve, client=client, void_pending=void_pending,
                lifecycle_guard=lifecycle_guard,
                # Character config was confirmed readable above; an unresolved name here means deleted.
                drop_deleted_chars=True,
            ) and report.renamed
        except Exception as exc:  # noqa: BLE001
            logger.error("visit recovery: forget replay failed: %r", exc)
            report.forgets_clean = False
    else:
        # Rename unreconciled: erasure and per-visit recovery find roster entries by current name, while entries may remain
        # under old name, making erasure 'succeed' without deleting anything. Defer until next startup (transcript upload/reporting do not depend on names).
        logger.warning("visit recovery: pending rename unresolved, forget replay and visit recovery deferred")
        report.forgets_clean = False
    await _cleanup_outboxes(config_dir, in_flight)
    sweep_now = time.time() if now is None else now
    try:
        # Do not reclaim pending transcripts by age yet: 'abandon after 7 days of failed upload since ending' (design §4.7); this startup must
        # try at least once before abandonment, so long-absent users do not lose transcripts without any recovery upload attempt.
        swept = await VisitSpool.sweep(config_dir, sweep_now, is_live=live, uploads="defer")
        report.swept = len(swept)
    except Exception as exc:  # noqa: BLE001
        logger.error("visit recovery: sweep failed: %r", exc)
    for visit_id in (await VisitSpool.list_visit_ids(config_dir, (STATE_SUFFIX,)) if names_settled else []):
        if in_flight(visit_id):
            continue
        try:
            await _recover_visit(
                visit_id, config_dir=config_dir, render_chips=render_chips, skip_names=unsettled,
                resolve_char_name=resolve, spawn_background=spawn_background,
                summary_llm=summary_llm, family_names=family_names,
                resume_diary_commit=resume_diary_commit, client=client, report=report,
                local_char_names=local_names,
            )
        except Exception as exc:  # noqa: BLE001 - one broken session must not block the rest
            logger.warning("visit recovery: visit %s skipped: %r", visit_id, exc)
    pending = await _upload_pending(
        config_dir, live=in_flight, upload_transcript=upload_transcript,
        submit_report=submit_report, report=report, retry_later=retry_later,
    )
    try:
        # Abandon still-unuploaded transcripts older than 7 days only after trying recovery upload. Without an upload callback none were tried:
        # do not abandon untried, but sensitive transcripts cannot remain indefinitely or block attached reports forever; retain at most one extra
        # retention period (twice original age in total), then abandon on expiry too.
        expiry_now = sweep_now if upload_transcript is not None else sweep_now - VISIT_SPOOL_RETENTION_DAYS * 86400
        # Capture ownership before deletion: deleting the self-identifying sealed file with unreadable state.json loses account identity.
        upload_owners = {
            visit_id: await _pending_upload_owner(config_dir, visit_id)
            for visit_id in await VisitSpool.list_visit_ids(config_dir, (UPLOAD_JSON_SUFFIX, UPLOAD_JSONL_SUFFIX))
        }
        swept = await VisitSpool.sweep(config_dir, expiry_now, is_live=live, uploads="only")
        report.swept += len(swept)
        # Abandoned pending transcript: submit its queued report normally afterward (design §4.7), first marking
        # transcript_unavailable in report file instead of treating it as though no transcript ever existed.
        spool_dir = config_dir / VISIT_SPOOL_DIRNAME
        for visit_id in sorted(_expired_upload_visits(swept)):
            # Log and sealed file expire by their own mtimes: if one remains, it may still upload next time;
            # transcript becomes unavailable only once both are gone.
            remaining = [visit_path(spool_dir, visit_id, suffix) for suffix in (UPLOAD_JSON_SUFFIX, UPLOAD_JSONL_SUFFIX)]
            if await asyncio.to_thread(lambda paths=remaining: any(path.exists() for path in paths)):
                continue
            # Use ownership captured from sealed file / state.json before deletion (exclude other accounts' reports on shared computers).
            owner = upload_owners.get(visit_id) or await _state_owner(config_dir, visit_id)
            await _mark_report_transcript_unavailable(config_dir, visit_id, "expired", report, owner=owner)
            # Transcript will never arrive: submit queued report this pass without waiting.
            pending.discard(visit_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("visit recovery: upload expiry failed: %r", exc)
    # Independently scan report queue too: continue reports whose transcript already uploaded (.upload.json deleted) but report remains pending.
    streams = set(await VisitSpool.list_visit_ids(config_dir, (UPLOAD_JSONL_SUFFIX,)))
    await _submit_reports(config_dir, skip=pending | streams, submit_report=submit_report, report=report,
                          retry_later=retry_later)
    return report
