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

"""
EventLog — per-character append-only audit + replay log (P2 infrastructure).

Why it exists (see docs/design/memory-event-log-rfc.md):
  P0 persisted the rebuttal cursor; P1 added the outbox so background tasks
  killed mid-flight can be re-run. The remaining structural problem: the
  views (facts.json / reflections.json / persona.json) are the only record
  of "state transitions" — there is no ordered history, so "crashed halfway
  through a view write" is invisible and cross-file invariants cannot be
  checked.

  This module adds events.ndjson (per character): every state transition
  writes the event **first**, then the view. At startup the reconciler
  compares the log tail against the sentinel (events_applied.json) and
  replays into the views any events the views never persisted.

Retention:
  Writers only ever append. Already-applied history in front of the
  sentinel is pruned by a low-frequency background task
  (compact_applied_history): the sentinel's own line and every line after
  it are kept verbatim, events_applied.json is never written, and the
  rewrite is a single atomic replace, so replay sees exactly the same tail
  before and after. See docs/design/memory-event-log-rfc.md, "Compaction".

Non-goals:
  - Not full event sourcing (views remain the hand-editable "source of truth").
  - Not a rule engine or state-machine DSL.
  - Not cross-character (per-character files; the architecture assumes a
    single writer).

Write discipline (RFC §3.4):
  Every write site that emits events must go through record_and_save, which
  puts the five steps load → append → mutate → save → sentinel-advance
  inside one per-character threading.Lock, the whole wrapped in one
  asyncio.to_thread worker. Never hold the lock across an await — continuing
  the outbox / cursors pattern.

  Why append precedes mutate: the view returned by load is often a shared
  cache held by the manager; if we mutated first and append then threw
  (e.g. an fsync OSError), the cache would keep "dirty changes with no
  corresponding event", and any later normal save would flush them to
  disk, breaking the event↔view correspondence. Appending successfully
  before mutating guarantees the cache only changes after the event is
  durably on disk.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import threading
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Callable, Iterable, Iterator, NamedTuple

from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_text, atomic_write_json
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")


# ── Event type constants (RFC §3.3, 12 legacy + 3 evidence = 15 types) ───

EVT_FACT_ADDED = "fact.added"
EVT_FACT_ABSORBED = "fact.absorbed"
EVT_FACT_ARCHIVED = "fact.archived"
EVT_REFLECTION_SYNTHESIZED = "reflection.synthesized"
EVT_REFLECTION_STATE_CHANGED = "reflection.state_changed"
EVT_REFLECTION_SURFACED = "reflection.surfaced"
EVT_REFLECTION_REBUTTED = "reflection.rebutted"
EVT_PERSONA_FACT_ADDED = "persona.fact_added"
EVT_PERSONA_FACT_MENTIONED = "persona.fact_mentioned"
EVT_PERSONA_SUPPRESSED = "persona.suppressed"
EVT_CORRECTION_QUEUED = "correction.queued"
EVT_CORRECTION_RESOLVED = "correction.resolved"

# memory-evidence-rfc §3.3 — 3 new event types for user-driven evidence.
# All three are full-snapshot payloads (§3.3.5) so handlers are trivially
# idempotent on replay.
EVT_REFLECTION_EVIDENCE_UPDATED = "reflection.evidence_updated"
EVT_PERSONA_EVIDENCE_UPDATED = "persona.evidence_updated"
EVT_PERSONA_ENTRY_UPDATED = "persona.entry_updated"
EVT_LONG_TERM_MEMORY_REPLACED = "memory.long_term_replaced"

ALL_EVENT_TYPES: frozenset[str] = frozenset({
    EVT_FACT_ADDED, EVT_FACT_ABSORBED, EVT_FACT_ARCHIVED,
    EVT_REFLECTION_SYNTHESIZED, EVT_REFLECTION_STATE_CHANGED,
    EVT_REFLECTION_SURFACED, EVT_REFLECTION_REBUTTED,
    EVT_PERSONA_FACT_ADDED, EVT_PERSONA_FACT_MENTIONED, EVT_PERSONA_SUPPRESSED,
    EVT_CORRECTION_QUEUED, EVT_CORRECTION_RESOLVED,
    EVT_REFLECTION_EVIDENCE_UPDATED, EVT_PERSONA_EVIDENCE_UPDATED,
    EVT_PERSONA_ENTRY_UPDATED,
    EVT_LONG_TERM_MEMORY_REPLACED,
})


# memory-evidence-rfc §3.3.7 — source enum values, defined as module-level constants for future extension.
# Not enforced (event_log does not filter them), but available to reconciler handlers / tests for validation.
EVIDENCE_SOURCE_USER_FACT = "user_fact"            # Stage-2 signal (§3.4)
EVIDENCE_SOURCE_USER_CONFIRM = "user_confirm"      # check_feedback confirmed
EVIDENCE_SOURCE_USER_REBUT = "user_rebut"          # check_feedback denied
EVIDENCE_SOURCE_USER_IGNORE = "user_ignore"        # check_feedback ignored
EVIDENCE_SOURCE_USER_KEYWORD_REBUT = "user_keyword_rebut"  # negative-keyword hook
EVIDENCE_SOURCE_MIGRATION_SEED = "migration_seed"  # §5 one-shot migration
EVIDENCE_SOURCE_PROMOTE_MERGE = "promote_merge"    # persona.entry_updated

ALL_EVIDENCE_SOURCES: frozenset[str] = frozenset({
    EVIDENCE_SOURCE_USER_FACT, EVIDENCE_SOURCE_USER_CONFIRM,
    EVIDENCE_SOURCE_USER_REBUT, EVIDENCE_SOURCE_USER_IGNORE,
    EVIDENCE_SOURCE_USER_KEYWORD_REBUT, EVIDENCE_SOURCE_MIGRATION_SEED,
    EVIDENCE_SOURCE_PROMOTE_MERGE,
})


# ── Applied-history compaction (docs/design/memory-event-log-rfc.md) ────

_EVENTS_FILENAME = 'events.ndjson'
_SENTINEL_FILENAME = 'events_applied.json'

# Trigger: a journal is examined once it reaches this size, OR once its first
# non-empty line is this old. Size alone would never reach the small, old
# journals, and those still carry archived entry text that should not stay
# on disk forever.
_COMPACT_TRIGGER_BYTES = 512 * 1024
_COMPACT_TRIGGER_AGE_DAYS = 30

# How much already-applied history in front of the sentinel survives a
# compaction (for the funnel endpoint and for diagnosis). Pending an owner
# decision; these two are the only knobs. Both 0 means "anchor line plus
# pinned archive copies only".
_RETAIN_APPLIED_DAYS = 7
_RETAIN_APPLIED_MAX_BYTES = 256 * 1024

# os.replace "target is busy" on Windows (ACCESS_DENIED / SHARING_VIOLATION).
_BUSY_WINERRORS = frozenset({5, 32})


class CompactionResult(NamedTuple):
    """What one compact_applied_history call did. Counts and ids only.

    outcome is one of: compacted, not_due, nothing_to_drop,
    no_character_dir, no_journal, journal_unreadable, journal_changed,
    write_blocked, replay_in_progress, replay_paused, sentinel_absent,
    sentinel_null, sentinel_unreadable, sentinel_malformed,
    sentinel_not_found, self_check_failed, busy, write_failed. Every
    outcome other than ``compacted`` leaves events.ndjson byte-for-byte
    unchanged.
    """

    outcome: str
    dropped_lines: int = 0
    dropped_bytes: int = 0
    kept_before_anchor: int = 0
    pending_tail: int = 0
    pinned: int = 0
    pinned_event_ids: tuple[str, ...] = ()


def _iter_journal_stream(
    stream: Iterable[str],
) -> Iterator[tuple[int, str, dict | None]]:
    """Yield ``(lineno, raw, record_or_None)`` for every line of a journal.

    ``raw`` is the line without its line terminator. ``record`` is None for
    a blank line, a line that is not JSON, a JSON value that is not an
    object, or an object without ``event_id`` -- the exact rule replay has
    always used, so compaction and replay agree on what a record is.

    The stream must be a text-mode file (or io.StringIO) and is iterated
    line by line. Never switch this to ``str.splitlines()``: entry text is
    written with ensure_ascii=False, so U+2028 / U+2029 / U+0085 can appear
    inside a JSON string, and splitlines() breaks a record there while file
    iteration does not.
    """
    for lineno, line in enumerate(stream, start=1):
        raw = line[:-1] if line.endswith('\n') else line
        stripped = raw.strip()
        if not stripped:
            yield lineno, raw, None
            continue
        try:
            rec = json.loads(stripped)
        except ValueError:
            yield lineno, raw, None
            continue
        if not isinstance(rec, dict) or 'event_id' not in rec:
            yield lineno, raw, None
            continue
        yield lineno, raw, rec


def _iter_journal_lines(path: str) -> Iterator[tuple[int, str, dict | None]]:
    """Open ``path`` in text mode and run it through _iter_journal_stream.

    Raises OSError / UnicodeDecodeError to the caller; a missing file is the
    caller's business too.
    """
    with open(path, encoding='utf-8') as f:
        yield from _iter_journal_stream(f)


def _line_bytes(raw: str) -> int:
    """On-disk size of one journal line, counting a one-byte terminator."""
    return len(raw.encode('utf-8')) + 1


def _naive_local(value: datetime) -> datetime:
    """Normalize to the journal's naive local-clock convention."""
    if value.tzinfo is None:
        return value
    from memory.evidence_analytics import to_naive_local
    return to_naive_local(value)


def _event_ts_local(value: object) -> datetime | None:
    """Parse a record's ``ts``; None when it is missing or not usable.

    Not usable covers more than "not ISO 8601": an aware value the platform
    cannot move to local time raises OverflowError (year 1 / 9999 next to
    an offset) or, on Windows, OSError (anything before 1970). One such
    hand-edited line must count as old, like any unparseable ts, instead of
    failing every compaction of that journal.
    """
    if not isinstance(value, str):
        return None
    try:
        return _naive_local(datetime.fromisoformat(value))
    except (ValueError, OverflowError, OSError):
        return None


def _first_line_says_due(path: str, now: datetime) -> bool:
    """Age trigger: is the first non-empty line at least the trigger age?

    Reads one line, not the file. A first line that cannot be read or
    parsed counts as due; the full scan then decides what, if anything, can
    go. An empty journal is never due.
    """
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    rec = json.loads(stripped)
                except ValueError:
                    return True
                ts = _event_ts_local(rec.get('ts')) if isinstance(rec, dict) else None
                if ts is None:
                    return True
                return now - ts >= timedelta(days=_COMPACT_TRIGGER_AGE_DAYS)
    except (OSError, UnicodeDecodeError):
        return True
    return False


def _file_identity(st: os.stat_result) -> tuple[int, int, int]:
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _assemble_compacted_body(kept_lines: list[str]) -> str:
    """Join kept lines into the replacement body, always newline-terminated."""
    return '\n'.join(kept_lines) + '\n'


def _body_keeps_tail(body: str, sentinel: str, old_tail_ids: list) -> bool:
    """Pre-write self-check on the replacement body, parsed from memory.

    The body is split by the same text-stream iteration replay uses. It must
    contain a record whose event_id is the sentinel, and the ids after the
    first such record must equal what followed the anchor before.
    """
    found = False
    ids: list = []
    for _, _, rec in _iter_journal_stream(io.StringIO(body, newline=None)):
        if rec is None:
            continue
        if found:
            ids.append(rec.get('event_id'))
        elif rec.get('event_id') == sentinel:
            found = True
    return found and ids == old_tail_ids


# ── Type aliases for _record_and_save callbacks (RFC §3.4) ───────────────

SyncLoadView = Callable[[str], object]           # (character_name) -> view_obj
SyncMutateView = Callable[[object], None]        # (view_obj) -> None, mutates in place
SyncSaveView = Callable[[str, object], None]     # (character_name, view_obj) -> None

# Apply handler: takes (character_name, event_payload) and is responsible for
# loading the relevant view, applying the event, AND persisting it before
# returning. Returns True if the apply actually changed state; False if
# idempotent no-op. Critical invariant: sentinel only advances after handler
# returns successfully, so handler MUST persist before returning — otherwise
# a process crash between handler-return and sentinel-write would lose the
# change while marking the event as applied.
#
# Handlers MUST be synchronous (no async/await) and use the sync IO helpers
# (atomic_write_json, not its a-twin): Reconciler.areconcile runs them on a
# worker thread via EventLog.apply_and_advance, without await. An async
# handler would return a coroutine that never runs (and would be truthy, so
# the sentinel would advance over an event that was never applied), silently
# breaking reconciliation. A handler must also never touch an asyncio
# primitive (it is off the event loop) nor call back into EventLog: it runs
# under the per-character lock, which is a plain non-reentrant threading.Lock.
ApplyHandler = Callable[[str, dict], bool]


class SentinelAdvanceError(RuntimeError):
    """The apply handler already succeeded, but the sentinel could not move.

    Raised only by apply_and_advance, and only after apply_fn returned — so
    it is never a handler failure and must not be reported as one. On-disk
    state is "view updated, sentinel not advanced", i.e. the event replays
    again on the next boot (handlers are idempotent).
    """


class SentinelConflictError(RuntimeError):
    """Another writer moved the sentinel while this replay was in flight.

    Deliberately NOT a SentinelAdvanceError. That class means "the view is
    already repaired, only the progress marker is behind"; this one is
    raised BEFORE apply_fn runs, so nothing was applied and nothing on disk
    changed. Catching the two together would report a round that has done
    nothing as a round whose write landed.

    Two facts follow from the conflict, and both matter:

      - Writing our own event_id now would move the sentinel BACKWARDS over
        events the other writer already claimed as applied. Those events
        would return to the tail, and any of them without a registered
        handler would pause replay on every subsequent boot — a permanently
        stuck reconciler.
      - The tail this round was holding is now behind the other writer's
        sentinel. record_and_save writes the id of the line it just
        appended, i.e. the end of the journal, and does so unconditionally
        (see its docstring): the events still queued in this round were
        declared "already applied" by that write even though they were not.
        Replaying them as-is would push older payloads over the newer
        writer's values, and the newer event would not be replayed again to
        correct it.

    The caller's job on this error is therefore not "stop" and not "keep
    going with the stale list" — it is to re-derive the work from the
    journal so the newer events are replayed on top, in journal order. See
    Reconciler.areconcile.
    """


class EventLog:
    """Per-character append-only event journal with reconciliation support.

    Public API is dual (sync + async twins). Sync methods are safe to call
    from async def code paths ONLY via asyncio.to_thread — they do blocking
    file IO. The _record_and_save helper and its a-twin are the normal
    entry points for wiring into existing save sites.
    """

    def __init__(self):
        self._config_manager = get_config_manager()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # Reconciler rounds currently in flight, per character. Guarded by
        # _locks_guard. A round releases the character lock between events,
        # so the lock alone cannot tell compaction that a round is running.
        self._replay_rounds: dict[str, int] = {}
        # Characters whose replay paused (did not drain) at least once in
        # this process. Guarded by _locks_guard; never cleared. The instance
        # is reused across hot reloads, so this survives them too.
        self._replay_paused: set[str] = set()

    # ── paths / locks ───────────────────────────────────────────

    def _events_path(self, name: str) -> str:
        # Late import avoids memory/__init__.py ↔ memory/event_log.py cycle
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            _EVENTS_FILENAME,
        )

    def _sentinel_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            _SENTINEL_FILENAME,
        )

    def _character_dir_nocreate(self, name: str) -> str:
        """The character directory path, WITHOUT creating it.

        _events_path / _sentinel_path go through ensure_character_dir, which
        makedirs. Compaction must never bring a deleted character's
        directory back, so it builds every path from this one.
        """
        return os.path.join(str(self._config_manager.memory_dir), name)

    def _get_lock(self, name: str) -> threading.Lock:
        if name not in self._locks:
            with self._locks_guard:
                if name not in self._locks:
                    self._locks[name] = threading.Lock()
        return self._locks[name]

    # ── replay-round bookkeeping (read by compaction) ───────────

    @contextmanager
    def replay_round(self, name: str) -> Iterator[None]:
        """Mark one reconciliation round for ``name`` as in flight.

        Reconciler.areconcile wraps its whole round in this, including the
        frozen rescans, exceptions and cancellation. Compaction refuses to
        run while the count is non-zero: a round re-reads the journal from
        its own last-applied event after a sentinel conflict, and if that
        line had been compacted away the rescan would fall back to a full
        replay and drop the events it still owed.

        Lock order is "character lock -> _locks_guard"; nothing takes them
        the other way round.
        """
        with self._locks_guard:
            self._replay_rounds[name] = self._replay_rounds.get(name, 0) + 1
        try:
            yield
        finally:
            with self._locks_guard:
                remaining = self._replay_rounds.get(name, 0) - 1
                if remaining > 0:
                    self._replay_rounds[name] = remaining
                else:
                    self._replay_rounds.pop(name, None)

    def _replay_round_active(self, name: str) -> bool:
        with self._locks_guard:
            return self._replay_rounds.get(name, 0) > 0

    def _record_replay_outcome(self, name: str, *, drained: bool) -> None:
        """Remember a round that stopped before draining its tail.

        Sticky for the life of the process: a later round that drains
        cannot tell whether a live write orphaned the paused tail in
        between (the known sentinel limitation in record_and_save), so the
        character stays out of compaction until the next start.
        """
        if drained:
            return
        with self._locks_guard:
            self._replay_paused.add(name)

    def replay_paused_in_process(self, name: str) -> bool:
        """Whether any reconciliation round for ``name`` paused in this process."""
        with self._locks_guard:
            return name in self._replay_paused

    # ── low-level append (no lock — caller must hold it) ────────

    def _write_line_unlocked(self, path: str, line: str) -> None:
        """Append + flush + fsync. fsync failures must propagate — record_and_save's
        durability contract is "the event hits disk before the view advances"; if
        fsync failed silently while the later view.save succeeded, the event↔view
        correspondence would break and the reconciler couldn't repair it.
        The caller (record_and_save) handles the exception, guaranteeing the view
        is never wrongly advanced."""
        with open(path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
            f.flush()
            os.fsync(f.fileno())

    def _append_unlocked(self, name: str, event_type: str, payload: dict) -> str:
        """Write one event record under an already-held lock. Returns event_id.

        Fails fast on unknown event_type. If unregistered types were let through and
        record_and_save crashed after the event append but before the view save, the
        Reconciler would have no handler on restart and could only skip it (and if
        it were pushed toward pause-on-unknown instead, the whole tail would be
        stuck) — an expensive repair path. Blocking at the write site is the
        cheapest line of defense.
        """
        if event_type not in ALL_EVENT_TYPES:
            raise ValueError(
                f"[EventLog] {name}: unknown event type {event_type!r}; "
                f"refusing to write unreplayable event"
            )
        event_id = str(uuid.uuid4())
        record = {
            'event_id': event_id,
            'type': event_type,
            'ts': datetime.now().isoformat(),
            'payload': payload,
        }
        line = json.dumps(record, ensure_ascii=False)
        self._write_line_unlocked(self._events_path(name), line)
        return event_id

    # ── public API: standalone append (no view coupling) ────────

    def append(self, name: str, event_type: str, payload: dict) -> str:
        """Append a single event. Prefer _record_and_save for writes that
        also mutate a view — this standalone API is for tests / migrations /
        events without a corresponding view update."""
        with self._get_lock(name):
            return self._append_unlocked(name, event_type, payload)

    # ── read_since / sentinel ───────────────────────────────────

    def _read_all_records(self, path: str) -> list[dict]:
        """Parse every line; skip corrupt ones with a warning. Caller holds lock."""
        if not os.path.exists(path):
            return []
        records: list[dict] = []
        for lineno, raw, rec in _iter_journal_lines(path):
            if rec is None:
                if raw.strip():
                    # Line number and length only: a journal line can carry
                    # an archived entry's full text.
                    logger.warning(
                        f"[EventLog] {path} 第 {lineno} 行不是有效事件记录"
                        f"（长度 {len(raw)}），跳过"
                    )
                continue
            records.append(rec)
        return records

    def read_since(self, name: str, after_event_id: str | None) -> list[dict]:
        """Return events after the sentinel, in file-position order.

        If after_event_id is None or not found in the current body, return
        ALL records (safe default per RFC §3.5 -- apply handlers are
        idempotent). Compaction never removes the sentinel's own line, so
        "not found" means the sentinel and the journal disagree for some
        other reason (hand edits, a restore that brought back only one of
        the two files); every record in the body is then treated as unapplied.
        """
        with self._get_lock(name):
            records = self._read_all_records(self._events_path(name))
        if after_event_id is None:
            return records
        for i, rec in enumerate(records):
            if rec.get('event_id') == after_event_id:
                return records[i + 1:]
        # The sentinel names an event that is not in the body. Compaction
        # always keeps that line, so this is not its doing.
        # Safe default: replay everything currently in the body.
        logger.info(
            f"[EventLog] {name}: sentinel event_id {after_event_id} 不在当前 body，"
            f"回退到全量 replay（{len(records)} 条）"
        )
        return records

    def read_sentinel(self, name: str) -> str | None:
        """Load last_applied_event_id from sentinel file. Safe defaults per RFC §3.5."""
        path = self._sentinel_path(name)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[EventLog] {name}: sentinel 读取失败 {e}；视作 null")
            return None
        if not isinstance(data, dict):
            logger.warning(f"[EventLog] {name}: sentinel 格式异常（非 dict），视作 null")
            return None
        last = data.get('last_applied_event_id')
        if last is not None and not isinstance(last, str):
            return None
        return last

    def _read_sentinel_state_unlocked(self, name: str) -> tuple[str, str | None]:
        """Strict sentinel read for compaction: ``(state, event_id)``.

        state is ``absent`` (no file), ``unreadable`` (OSError or bad
        UTF-8), ``malformed`` (not JSON, not an object, or an id that is
        neither a string nor null), ``null`` or ``ok``. read_sentinel folds
        all of the first four into None, which is right for replay (it then
        replays everything) but wrong here: compaction may only cut in
        front of an anchor it has actually read. Never creates the
        character directory. Caller holds the character lock.
        """
        path = os.path.join(self._character_dir_nocreate(name), _SENTINEL_FILENAME)
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except FileNotFoundError:
            return 'absent', None
        except UnicodeDecodeError:
            return 'unreadable', None
        except ValueError:
            return 'malformed', None
        except OSError:
            return 'unreadable', None
        if not isinstance(data, dict):
            return 'malformed', None
        last = data.get('last_applied_event_id')
        if last is None:
            return 'null', None
        if not isinstance(last, str):
            return 'malformed', None
        return 'ok', last

    def _write_sentinel_unlocked(self, name: str, event_id: str | None) -> None:
        """Unconditionally overwrite events_applied.json. Caller holds the lock.

        Unconditional means exactly that: this helper does NOT enforce
        monotonicity, and cannot. event_id is a uuid4 — two ids carry no
        order relative to each other; the only ordering that exists is the
        position in events.ndjson, which this helper never reads. Writers
        that must not move the sentinel backwards have to establish the
        ordering themselves (see apply_and_advance's compare-and-set).
        """
        atomic_write_json(
            self._sentinel_path(name),
            {'last_applied_event_id': event_id, 'ts': datetime.now().isoformat()},
        )

    def advance_sentinel(self, name: str, event_id: str | None) -> None:
        """Persist the new sentinel atomically, under the per-character lock.

        Sentinel writers must serialise against record_and_save, which
        rewrites the same file at the tail of its own critical section.
        Callers that already hold the lock must use _write_sentinel_unlocked
        instead — threading.Lock is not reentrant.

        Guarantees mutual exclusion only, NOT monotonicity: the write is
        still unconditional, so a caller holding a stale event_id can move
        the sentinel backwards. record_and_save is safe because the id it
        writes is the one it just appended (the newest event by
        construction); everyone else has to reason about it.
        """
        # This public method has no production callers now (reconcile uses apply_and_advance,
        # and record_and_save uses _write_sentinel_unlocked); the lock remains here for
        # "safe by default," so future call sites cannot accidentally create an unlocked writer.
        with self._get_lock(name):
            self._write_sentinel_unlocked(name, event_id)

    def apply_and_advance(
        self,
        name: str,
        event_id: str | None,
        apply_fn: Callable[[], bool],
        *,
        expected_sentinel: str | None,
        advance: bool = True,
    ) -> bool:
        """Run one replay apply and advance the sentinel in ONE critical section.

        apply_fn is a reconciler apply-handler already bound to its
        (character, payload); it loads → mutates → persists its own view.
        expected_sentinel is the sentinel value the replay based its tail
        on — the previous event's id, or the value read when the round
        started. It is checked against the on-disk sentinel BEFORE apply_fn
        runs, so a stale event is never applied (see the conflict branch
        below). advance=False skips both the check and the write: the caller
        has already learned the sentinel is no longer theirs to move and has
        re-derived its work list from the journal, so re-checking would only
        raise again. Running the pair here — inside the same per-character
        lock record_and_save uses, on a worker thread — buys three things:

          1. Once a live writer has entered record_and_save's critical
             section, its read-modify-write can no longer interleave with
             the handler's read-modify-write on the same view file. This is
             a partial guarantee, and the boundary matters: the reflection
             and persona write paths load their view snapshot BEFORE
             entering (memory/reflection/evidence_flow.py loads, then hands
             the snapshot to record_and_save as sync_load_view), so a live
             writer parked between its own load and its own lock acquisition
             can still save a pre-replay snapshot over the repair. What keeps
             that from happening in production is the startup ordering —
             reconciliation completes before any live writer is resumed (see
             app/memory_server/runtime.py) — and this lock is the second
             line of defence, not the first.
          2. The sentinel write joins the handler in the same critical
             section: the pair is atomic with respect to other writers, and
             the sentinel can never advance without the handler's write
             having landed first.
          3. The handler's file IO leaves the event loop, which is what
             makes it eligible for file_utils' busy-retry backoff (that
             backoff is deliberately disabled on the loop) and stops its
             fsync from blocking the loop.

        Returns whatever apply_fn returned (True = the view actually
        changed). Raises:
          - SentinelConflictError, before apply_fn is called at all, if the
            on-disk sentinel is no longer expected_sentinel (someone
            advanced it while this replay was in flight). Nothing is applied
            and the sentinel is left alone;
          - whatever apply_fn raised, with the sentinel untouched — the
            caller's pause-and-retry semantics;
          - SentinelAdvanceError if the sentinel write itself fails, so the
            caller can tell an IO problem from a handler problem.
        """
        with self._get_lock(name):
            if advance:
                # Monotonic compare-and-set: event_id is uuid4, so IDs cannot be ordered by value;
                # the only way to tell "whether the new sentinel is farther ahead" is to check that "the on-disk sentinel is still the one I started with."
                # Without this check, the deadlock reproduced in review can occur: a live writer appends an event with no registered
                # handler in this process and advances the sentinel; replay then writes the sentinel back to the old ID → that event
                # returns to the tail → every subsequent startup hits "unregistered event type, pause replay."
                #
                # The check must happen **before** apply_fn. A handler does "read disk → update target-entry
                # fields → replace the whole record"; running it once already writes this stale event's fields into the view.
                # If a newer writer changed the same set of fields on the same entry, the stale values overwrite the new ones;
                # detecting the CAS conflict afterward is too late (and that writer's event is after the sentinel,
                # so it will not be replayed again to correct the view).
                # No in-process writer can interleave between the check and apply: record_and_save /
                # compact_applied_history / advance_sentinel all acquire this lock, and both steps
                # run inside the critical section where we already hold it.
                current = self.read_sentinel(name)
                if current != expected_sentinel:
                    raise SentinelConflictError(
                        f"{name}: sentinel moved to {current!r} before replaying "
                        f"{event_id!r} (expected {expected_sentinel!r}); refusing "
                        f"to apply a tail the sentinel already passed"
                    )
            changed = apply_fn()
            if not advance:
                return bool(changed)
            try:
                self._write_sentinel_unlocked(name, event_id)
            except Exception as e:
                # The handler has already persisted its changes; a failure here is purely an I/O problem writing the sentinel.
                # Wrap it in a dedicated exception so callers do not record it as "handler failure."
                raise SentinelAdvanceError(
                    f"{name}: apply of {event_id!r} persisted but the sentinel "
                    f"write failed: {e}"
                ) from e
        return bool(changed)

    # ── applied-history compaction ──────────────────────────────

    def compact_applied_history(
        self,
        name: str,
        *,
        needs_journal_copy: Callable[[dict], bool] | None = None,
        now: datetime | None = None,
    ) -> CompactionResult:
        """Drop already-applied history in front of the sentinel.

        Invariants, whatever the outcome:
          - the sentinel's line and every line after it (corrupt and blank
            lines included) survive verbatim and in order; only the line
            terminator is normalized and a missing final newline is added;
          - events_applied.json is never opened for writing;
          - no event id is created;
          - the only disk write is one atomic_write_text of events.ndjson,
            so a crash leaves either the old body or the new one, and both
            give read_since(sentinel) the same answer.

        In front of the anchor (the first record whose event_id equals the
        sentinel, the same match read_since makes), a record is kept only
        when ``needs_journal_copy(record)`` is true -- pinned regardless of
        age or size, because the journal may hold the only copy of an
        archived entry -- or when it falls inside the retention window
        (_RETAIN_APPLIED_DAYS, capped at _RETAIN_APPLIED_MAX_BYTES, oldest
        evicted first). ``memory.long_term_replaced`` is never retained.
        Everything else in front of the anchor goes, including lines replay
        would skip anyway.

        Skips without touching the body when the journal is not due
        (smaller than _COMPACT_TRIGGER_BYTES and first line younger than
        _COMPACT_TRIGGER_AGE_DAYS), when a reconciliation round is in
        flight or paused earlier in this process, when the sentinel is not
        a readable event id, when that id is not in the body, when the cloud
        save write fence is up, or when the file changed under the scan.

        Never creates a directory: paths come from the no-create character
        directory, and the final write refuses to recreate a vanished
        parent. The pre-write checks and the replace run inside
        cloudsave_writable_transaction, taken BEFORE the character lock --
        the order a cloud import already uses (fence first, character lock
        later, through reload and long-term sync).

        ``needs_journal_copy`` exceptions propagate and nothing is written.
        Blocking file IO: call from a worker thread, or use
        acompact_applied_history.
        """
        char_dir = self._character_dir_nocreate(name)
        if not os.path.isdir(char_dir):
            return CompactionResult('no_character_dir')
        path = os.path.join(char_dir, _EVENTS_FILENAME)
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return CompactionResult('no_journal')
        except OSError:
            return CompactionResult('journal_unreadable')
        now = _naive_local(now) if now is not None else datetime.now()
        if st.st_size < _COMPACT_TRIGGER_BYTES and not _first_line_says_due(path, now):
            return CompactionResult('not_due')

        from utils.cloudsave_runtime import (
            MaintenanceModeError,
            cloudsave_writable_transaction,
        )
        try:
            with cloudsave_writable_transaction(
                self._config_manager,
                operation='compact',
                target=f'memory/{name}/{_EVENTS_FILENAME}',
            ):
                with self._get_lock(name):
                    return self._compact_locked(
                        name, char_dir, path, needs_journal_copy, now,
                    )
        except MaintenanceModeError:
            return CompactionResult('write_blocked')

    def _compact_locked(
        self,
        name: str,
        char_dir: str,
        path: str,
        needs_journal_copy: Callable[[dict], bool] | None,
        now: datetime,
    ) -> CompactionResult:
        """Body of compact_applied_history. Caller holds the character lock."""
        if self._replay_round_active(name):
            return CompactionResult('replay_in_progress')
        if self.replay_paused_in_process(name):
            return CompactionResult('replay_paused')
        state, sentinel = self._read_sentinel_state_unlocked(name)
        if state != 'ok':
            return CompactionResult(f'sentinel_{state}')
        try:
            st0 = os.stat(path)
        except FileNotFoundError:
            return CompactionResult('no_journal')
        except OSError:
            return CompactionResult('journal_unreadable')

        cutoff = now - timedelta(days=_RETAIN_APPLIED_DAYS)
        cap = _RETAIN_APPLIED_MAX_BYTES
        anchor_found = False
        tail: list[str] = []
        old_tail_ids: list = []
        pinned: list[tuple[int, str, str]] = []
        window: deque[tuple[int, str, int]] = deque()
        window_bytes = 0
        total_lines = 0
        total_bytes = 0
        lines = _iter_journal_lines(path)
        try:
            while True:
                try:
                    item = next(lines, None)
                except (OSError, UnicodeDecodeError):
                    return CompactionResult('journal_unreadable')
                if item is None:
                    break
                lineno, raw, rec = item
                size = _line_bytes(raw)
                total_lines += 1
                total_bytes += size
                if anchor_found:
                    tail.append(raw)
                    if rec is not None:
                        old_tail_ids.append(rec.get('event_id'))
                    continue
                if rec is None:
                    continue
                if rec.get('event_id') == sentinel:
                    anchor_found = True
                    tail.append(raw)
                    continue
                if rec.get('type') == EVT_LONG_TERM_MEMORY_REPLACED:
                    # Reference events are no-ops once their inbox is
                    # consumed; old head-style ones are whole snapshots.
                    continue
                if needs_journal_copy is not None and needs_journal_copy(rec):
                    pinned.append((lineno, raw, str(rec.get('event_id'))))
                    continue
                ts = _event_ts_local(rec.get('ts'))
                if ts is None or ts < cutoff:
                    continue
                window.append((lineno, raw, size))
                window_bytes += size
                while window and window_bytes > cap:
                    window_bytes -= window.popleft()[2]
        finally:
            lines.close()

        if not anchor_found:
            return CompactionResult('sentinel_not_found')

        kept_before = sorted(
            [(lineno, raw) for lineno, raw, _ in pinned]
            + [(lineno, raw) for lineno, raw, _ in window]
        )
        kept_lines = [raw for _, raw in kept_before] + tail
        dropped_lines = total_lines - len(kept_lines)
        stats = {
            'kept_before_anchor': len(kept_before),
            'pending_tail': len(old_tail_ids),
            'pinned': len(pinned),
            'pinned_event_ids': tuple(event_id for _, _, event_id in pinned),
        }
        if dropped_lines <= 0:
            return CompactionResult('nothing_to_drop', **stats)
        dropped_bytes = total_bytes - sum(_line_bytes(raw) for raw in kept_lines)

        body = _assemble_compacted_body(kept_lines)
        if not _body_keeps_tail(body, sentinel, old_tail_ids):
            logger.error(
                f"[EventLog] {name}: 压缩自检失败（新正文里锚点或其后的事件序列"
                f"与原文不一致），放弃写盘；锚点 {sentinel}，尾巴 {len(old_tail_ids)} 条"
            )
            return CompactionResult('self_check_failed', **stats)

        if not os.path.isdir(char_dir):
            return CompactionResult('no_character_dir', **stats)
        try:
            st1 = os.stat(path)
        except FileNotFoundError:
            return CompactionResult('journal_changed', **stats)
        except OSError:
            return CompactionResult('journal_unreadable', **stats)
        if _file_identity(st1) != _file_identity(st0):
            # Another process appended, or a restore replaced the file.
            return CompactionResult('journal_changed', **stats)

        try:
            atomic_write_text(path, body, encoding='utf-8', create_parents=False)
        except FileNotFoundError:
            if not os.path.isdir(char_dir):
                return CompactionResult('no_character_dir', **stats)
            return CompactionResult('write_failed', **stats)
        except PermissionError as e:
            if getattr(e, 'winerror', None) in _BUSY_WINERRORS:
                return CompactionResult('busy', **stats)
            return CompactionResult('write_failed', **stats)
        except OSError:
            return CompactionResult('write_failed', **stats)
        return CompactionResult(
            'compacted',
            dropped_lines=dropped_lines,
            dropped_bytes=dropped_bytes,
            **stats,
        )

    # ── _record_and_save (RFC §3.4) ─────────────────────────────

    def record_and_save(
        self,
        name: str,
        event_type: str,
        payload: dict,
        *,
        sync_load_view: SyncLoadView,
        sync_mutate_view: SyncMutateView,
        sync_save_view: SyncSaveView,
    ) -> str:
        """The canonical event-emitting write:
        load view → append event → mutate view → save view → advance sentinel.

        All five steps run inside a single per-character threading.Lock so
        no two coroutines can race a read-modify-write cycle. Returns the
        newly-allocated event_id.

        Append runs BEFORE mutate to avoid dirtying the shared cache if the
        event fails to persist; see the block comment inside the method
        body for the full rationale.

        The sync twins (load_X / save_X) are the right choice here: we are
        ALREADY on a worker thread (the _arecord_and_save a-twin hops us
        into one), and using async twins would pointlessly re-schedule
        through asyncio.to_thread and risk event-loop locking anti-patterns.

        KNOWN LIMITATION — the sentinel write is an unconditional claim.
        The final step writes the sentinel to the id just appended, which
        by definition is the last line of the journal, so this method can
        never move the sentinel backwards. What it does not check is
        whether everything BEFORE that line was applied. Setting the
        sentinel to the journal end asserts exactly that, and the assertion
        is false whenever an unapplied tail is still sitting on disk: those
        events fall behind the sentinel and no later boot replays them.
        Whoever hits that has lost them silently.

        It is reachable. Replay pauses (and leaves a tail) on an
        unregistered event type or a raising handler; the very next live
        write then orphans whatever was left. Startup ordering does not
        help — this has nothing to do with reconciliation running
        concurrently.

        Closing it properly means the sentinel can no longer be "an event
        id": a writer would have to know whether the journal ahead of its
        own line is drained, which is a full scan on every write, or the
        sentinel has to become a position/applied-set with an on-disk
        format migration. Both are RFC-level changes
        (docs/design/memory-event-log-rfc.md, concurrency section) and are
        deliberately out of scope here. Do NOT read "the sentinel only ever
        moves forward" as "no event can be skipped".
        """
        with self._get_lock(name):
            view = sync_load_view(name)
            # Order: load → append (may raise on fsync failure) → mutate → save.
            # Append first because sync_load_view often returns the manager's
            # shared cache; if append raises after mutation, the cache is dirty but
            # The event was not persisted; any later normal save would persist "a change with no corresponding event,"
            # breaking the event↔view relationship. Appending successfully before mutation guarantees:
            #   - append failure: view/cache untouched, no state leaked
            #   - mutate/save failure: event is already in the log, and the reconciler can repair it
            event_id = self._append_unlocked(name, event_type, payload)
            sync_mutate_view(view)
            sync_save_view(name, view)
            # Inline sentinel write: still under the lock, still on this
            # worker thread — the unlocked helper, because advance_sentinel
            # takes the same non-reentrant lock we are already holding.
            self._write_sentinel_unlocked(name, event_id)
        return event_id

    # ── async duals ─────────────────────────────────────────────

    async def aappend(self, name: str, event_type: str, payload: dict) -> str:
        return await asyncio.to_thread(self.append, name, event_type, payload)

    async def aread_since(self, name: str, after_event_id: str | None) -> list[dict]:
        return await asyncio.to_thread(self.read_since, name, after_event_id)

    async def aread_sentinel(self, name: str) -> str | None:
        return await asyncio.to_thread(self.read_sentinel, name)

    async def aadvance_sentinel(self, name: str, event_id: str | None) -> None:
        await asyncio.to_thread(self.advance_sentinel, name, event_id)

    async def aapply_and_advance(
        self,
        name: str,
        event_id: str | None,
        apply_fn: Callable[[], bool],
        *,
        expected_sentinel: str | None,
        advance: bool = True,
    ) -> bool:
        return await asyncio.to_thread(
            self.apply_and_advance, name, event_id, apply_fn,
            expected_sentinel=expected_sentinel, advance=advance,
        )

    async def acompact_applied_history(
        self,
        name: str,
        *,
        needs_journal_copy: Callable[[dict], bool] | None = None,
        now: datetime | None = None,
    ) -> CompactionResult:
        return await asyncio.to_thread(
            self.compact_applied_history, name,
            needs_journal_copy=needs_journal_copy, now=now,
        )

    async def arecord_and_save(
        self,
        name: str,
        event_type: str,
        payload: dict,
        *,
        sync_load_view: SyncLoadView,
        sync_mutate_view: SyncMutateView,
        sync_save_view: SyncSaveView,
    ) -> str:
        return await asyncio.to_thread(
            self.record_and_save, name, event_type, payload,
            sync_load_view=sync_load_view,
            sync_mutate_view=sync_mutate_view,
            sync_save_view=sync_save_view,
        )


# ── Reconciler scaffolding (RFC §3.5) ─────────────────────────────────────

# After freezing the sentinel, probe toward the log tail again after each scan; this is the maximum number of probes. Only "a writer committed
# during the previous scan" consumes a probe, so normal startup uses none. Eight gives the process a chance to finish after writes
# stop without letting continuous writes keep startup stuck forever.
_MAX_FROZEN_RESCANS = 8


class Reconciler:
    """Applies event-log tail onto views on startup.

    Handlers for each event type are registered externally (by memory_server
    in P2.b). An event type without a registered handler PAUSES replay for
    that character (sentinel left on the last applied event); it is never
    skipped, because skipping would lose it for good. See areconcile.
    """

    def __init__(self, event_log: EventLog):
        self._event_log = event_log
        self._handlers: dict[str, ApplyHandler] = {}

    def register(self, event_type: str, handler: ApplyHandler) -> None:
        if event_type not in ALL_EVENT_TYPES:
            logger.warning(
                f"[Reconciler] 注册未登记事件类型 {event_type!r}（handler 仍生效，但请检查 typo）"
            )
        self._handlers[event_type] = handler

    async def areconcile(self, name: str) -> int:
        """P2.a.1 scaffold: dispatch tail events to registered handlers,
        advance sentinel per event, preserve sentinel on handler raise.
        P2.b wires concrete save paths.

        Handler contract (ApplyHandler): each handler MUST load → apply →
        save its own view before returning. The Reconciler only advances
        the sentinel after handler returns; if handler skipped the save,
        a crash between handler-return and sentinel-write would silently
        lose the change. Modeled off record_and_save — the per-event-type
        equivalent is per-handler responsibility.

        Concurrency: the primary guarantee is exclusivity, not locking.
        The only production caller (app/memory_server/runtime.py) awaits
        this loop to completion BEFORE it resumes the outbox, so no live
        writer is running while the tail is replayed. That ordering is what
        closes the loss window, because a live writer that loaded its view
        snapshot before the replay would otherwise save it back over the
        repair — with the sentinel already past the event, so no later boot
        replays it again: silent, permanent loss.

        Underneath, handler-call and sentinel-advance are one critical
        section (EventLog.apply_and_advance) on the per-character lock that
        record_and_save uses, on a worker thread. That is defence in depth
        for the residual window (writers that already read their snapshot)
        and for any future caller that reconciles outside the startup path.

        Failure semantics (intentional): if a handler raises, we STOP the
        whole reconcile loop for this character and leave the sentinel on
        the last successfully-applied event. Rationale — compound
        transitions (reflection.state_changed followed by persona.fact_added)
        have a causal dependency; applying downstream events past a failed
        upstream one would produce an inconsistent view. Per-character
        reconciliation resumes on next boot. Unknown event types ALSO pause
        the loop: advancing past an unknown event would permanently lose it
        (a later version that adds the handler couldn't recover it), and
        applying subsequent known events could silently fork the view from
        the unreplayed mutation. Writes are gated by _append_unlocked's
        ALL_EVENT_TYPES check, so an unknown type here means a rollback
        to an older binary — operator must upgrade back or manually
        surgery the log.

        Two things that are NOT handler failures and are handled apart from
        them, because they leave different state on disk:
          - the sentinel moved under us (another writer advanced it past
            what this round assumed). The tail this round is carrying is
            now stale: it sits behind that writer's sentinel, and applying
            it as-is would push older payloads over newer values with no
            later replay to correct them. So the round re-reads the journal
            from its own last-applied position through to the END of the
            file and replays that, in journal order — the queued repairs
            still land, and the other writer's events land after them, so
            the newest payload for any given entry wins. The sentinel is
            frozen from then on: writing our older id back would return the
            other writer's events to the tail, and one of them without a
            registered handler would wedge every future boot. Nothing needs
            to be written at the end either — the other writer already
            parked the sentinel at the journal end.
            That end is a snapshot, so the frozen pass re-probes for newly
            appended events every time it drains, up to _MAX_FROZEN_RESCANS
            times. An event landing after the snapshot is not in the tail,
            yet the stale payloads queued ahead of it still are: replaying
            those would push older values over the view it just wrote, and
            with the sentinel now parked on its id no later boot replays it
            back.
          - the sentinel write failed (IO). The handler already persisted;
            only the progress marker is behind, so the event replays
            idempotently next boot. Logged as such, not as a handler fault.

        Re-applying events that were already applied is safe by the same
        property the whole design leans on elsewhere (read_since falls back
        to a full replay when the sentinel is missing or not in the body):
        handlers carry full-snapshot payloads and are idempotent.

        Round bookkeeping: the whole round runs inside
        EventLog.replay_round, so compaction cannot cut the journal under
        it, and a round that stops before draining its tail (any of the
        pause / stop branches above, an exception, cancellation) is recorded
        with EventLog._record_replay_outcome; compaction then leaves that
        character alone for the rest of the process. The return value is
        still just the number of events whose handler changed a view.
        """
        with self._event_log.replay_round(name):
            drained = False
            try:
                applied_count, drained = await self._areconcile_round(name)
            finally:
                self._event_log._record_replay_outcome(name, drained=drained)
        return applied_count

    async def _areconcile_round(self, name: str) -> tuple[int, bool]:
        """One reconciliation round; returns ``(applied_count, drained)``.

        ``drained`` is True only when the round reached the end of the
        journal. Every early return leaves unapplied events on disk.
        """
        last_applied = await self._event_log.aread_sentinel(name)
        tail = await self._event_log.aread_since(name, last_applied)
        applied_count = 0
        # CAS every event, and do it **before** running its handler: expected is where this replay believes the on-disk
        # sentinel should be — initially the last_applied read at startup,
        # then the event_id just written by the previous event. A mismatch means another writer changed the sentinel, so
        # this tail is stale and no event may be applied; see apply_and_advance.
        expected_sentinel = last_applied
        # Once the sentinel no longer belongs to this replay, freeze it and reload pending events from the log
        # (see the except branch below); this tail is stale and cannot be applied as-is.
        sentinel_frozen = False
        # After freezing, the sentinel no longer advances, so "scan to the end of the log" means one snapshot while
        # writers may still append later. An event committed after that snapshot is absent from this tail, but an earlier stale
        # payload is still waiting to be replayed — applying it would overwrite the view just written by that later event,
        # while the sentinel now points to **its** id. At next startup, read_since begins after it, so the event
        # will never be replayed to fix the view. What is lost is the writer's content, permanently, not this run's progress.
        # Therefore after each scan, probe toward the tail again until no new events are found.
        rescans_left = _MAX_FROZEN_RESCANS
        i = 0
        while True:
            if i >= len(tail):
                if not sentinel_frozen:
                    return applied_count, True
                extra = await self._event_log.aread_since(name, expected_sentinel)
                if not extra:
                    return applied_count, True
                if rescans_left <= 0:
                    # Bound the loop so continuous writes cannot keep this scan open forever or hang startup.
                    # Stopping has a cost (entries remaining before the sentinel will not be replayed next startup),
                    # so report it rather than truncating silently.
                    logger.warning(
                        f"[Reconciler] {name}: 哨兵冻结后连续 {_MAX_FROZEN_RESCANS} 轮"
                        f"都有新事件追加，停止重扫；仍有 {len(extra)} 条未重放且位于"
                        f"哨兵之前，下次开机不会自动补上"
                    )
                    return applied_count, False
                rescans_left -= 1
                tail = extra
                i = 0
                continue
            event = tail[i]
            event_type = event.get('type')
            event_id = event.get('event_id')
            if event_type not in self._handlers:
                logger.warning(
                    f"[Reconciler] {name}: 遇到未注册事件类型 {event_type!r} "
                    f"(id={event_id})，暂停 replay，sentinel 保留在上一条已应用事件；"
                    f"请检查是否需要升级到支持该类型的版本"
                )
                return applied_count, False
            handler = self._handlers[event_type]
            payload = event.get('payload') or {}
            try:
                # The handler does its own load → apply → save; see the ApplyHandler contract.
                # Run the whole operation (handler + sentinel advancement) through apply_and_advance: under
                # the per-character lock, on a worker thread, together with record_and_save
                # Centralize on one writer. If the handler raises, the locked sentinel write does not run,
                # and the exception passes through to_thread to the except below — failure semantics are unchanged.
                changed = await self._event_log.aapply_and_advance(
                    name, event_id, lambda: handler(name, payload),
                    expected_sentinel=expected_sentinel,
                    advance=not sentinel_frozen,
                )
                if changed:
                    applied_count += 1
                expected_sentinel = event_id
                i += 1
            except SentinelConflictError as e:
                # This is not a failure, and this event was not applied (the conflict check runs before the handler).
                # Another writer already advanced the sentinel. By writing it, that writer claimed
                # everything before it had been applied; this tail is after it and is now stale. Applying it as-is
                # would overwrite the value the writer just stored with an old payload, while that writer's newer
                # event is before the sentinel and will not be replayed again to correct it.
                #
                # So neither "stop" nor "apply as usual": reload pending work in log order,
                # from this run's last applied position (expected_sentinel) through end of file.
                # Queued repairs are still persisted, and that writer's event is replayed after them,
                # so the latest payload for the same item is written last — ordering follows line position in the log,
                # which is the event log's intended semantics. Reapplication is harmless: handlers assign full snapshots,
                # are idempotent, and read_since already replays everything when the sentinel is lost.
                #
                # Freeze the sentinel from now on (advance=False). Writing the old ID would move that writer's event back to
                # the tail, and any event without a registered handler would then make every later startup stop at
                # "unregistered event type"; that character could never be reconciled again. No cleanup write is needed:
                # the other writer already left the sentinel at the end of the log, which is exactly where this rescan ends.
                if sentinel_frozen:
                    # Structurally unreachable: after freezing, advance=False, so apply_and_advance
                    # no longer performs CAS and cannot raise this exception. Keep the fallback in case a future
                    # change to advance semantics would otherwise turn this into an infinite loop.
                    logger.warning(
                        f"[Reconciler] {name}/{event_type}/{event_id}: 哨兵冻结后"
                        f"仍报冲突，停止本轮 replay: {e}"
                    )
                    return applied_count, False
                sentinel_frozen = True
                logger.warning(
                    f"[Reconciler] {name}/{event_type}/{event_id}: 哨兵已被其他写者"
                    f"推进，手上的尾巴已陈旧；改为从本轮已应用位置重扫到日志末尾并"
                    f"按日志顺序重放，此后不再推进哨兵: {e}"
                )
                tail = await self._event_log.aread_since(name, expected_sentinel)
                i = 0
            except SentinelAdvanceError as e:
                # Handler succeeded but sentinel write failed. Attribute this separately from handler failure: on disk,
                # the view changed but the sentinel did not, so replay this event idempotently next startup.
                logger.warning(
                    f"[Reconciler] {name}/{event_type}/{event_id} 哨兵写入失败"
                    f"（handler 已成功应用）: {e}；下次开机重放该条"
                )
                return applied_count, False
            except Exception as e:
                logger.warning(
                    f"[Reconciler] {name}/{event_type}/{event_id} handler 失败: {e}；"
                    f"保留 sentinel 在上一条位置，下次重试"
                )
                return applied_count, False
