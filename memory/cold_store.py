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

"""Cold storage: memory kept on this disk only, never recalled or organized.

Three kinds of rows live here:

* facts that lost a dedup arbitration, were retracted, or belong to a group
  subject that went quiet -- sharded under ``memory/<char>/fact_cold/``;
* archived reflections -- the existing ``reflection_archive/`` shards;
* archived persona entries -- the existing ``persona_archive/`` shards.

All three share the shard layout of :mod:`memory.archive_shards`, so a write
touches only today's shard whatever the total size.

This module is the only reader of cold row TEXT. Everything else gets a
"stub": the row without its text, tags, vectors or version history, plus a
``text_sha256`` so a caller can still tell whether a wording was seen before.
Stubs are read shard by shard and cached on each shard's size and mtime;
there is no index file to rewrite.

Full rows are for restore, file export and privacy erasure only. A static
guard test (tests/unit/test_cold_store_guard.py) keeps that list short.

Cold rows never travel in a long-term sync snapshot. The snapshot names them
in its ``cold`` list by id and status only (docs/design/long-term-memory-sync-v3.md).
Cold storage is not part of the Steam cloud save either.

Every function takes the character's memory directory (``memory/<char>``).
Each blocking function has an ``a``-prefixed async twin.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
from collections import Counter
from datetime import datetime
from typing import Callable, Iterable

from memory.archive_shards import (
    PERSONA_ARCHIVE_DIRNAME,
    REFLECTION_ARCHIVE_DIRNAME,
    _list_shard_files,
    append_to_shard_sync,
    shard_filename_for,
)
from utils.file_utils import atomic_write_json, read_json_tolerating_replace
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")

FACT_COLD_DIRNAME = "fact_cold"
COLD_DIRNAMES = {
    "fact": FACT_COLD_DIRNAME,
    "reflection": REFLECTION_ARCHIVE_DIRNAME,
    "persona": PERSONA_ARCHIVE_DIRNAME,
}
_KIND_BY_DIRNAME = {dirname: kind for kind, dirname in COLD_DIRNAMES.items()}

# Fields a stub never carries: the wording and everything derived from it.
_TEXT_FIELDS = frozenset((
    "text", "tags", "version_history", "embedding", "embedding_text_sha256",
    "embedding_model_id", "embedding_dim", "recent_mentions",
))

_RELATIVE_RE = re.compile(
    r"^(?P<dir>" + "|".join(map(re.escape, COLD_DIRNAMES.values())) + r")/"
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9a-f]{8}\.json$"
)


class ColdStoreUnreadable(RuntimeError):
    """A cold shard exists but cannot be read as a JSON list of rows."""


class ColdArchiveSubjectUnavailable(ValueError):
    """An archived persona row has no unambiguous subject left on this device."""

    def __init__(self):
        super().__init__("long_term_archive_subject_unavailable")


def cold_dir(directory: str, kind: str = "fact") -> str:
    return os.path.join(str(directory), COLD_DIRNAMES[kind])


def text_sha256(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def legacy_entry_id(subject: object, text: object) -> str:
    """Portable id of a row stored without one (same formula as the sync export)."""
    return "legacy_" + hashlib.sha256(f"{subject}:{text}".encode()).hexdigest()[:24]


def stub(row: dict, *, subject_field: str = "entity") -> dict:
    """The row without its wording. ``legacy_id`` stands in for a missing id."""
    result = {key: value for key, value in row.items() if key not in _TEXT_FIELDS}
    result["text_sha256"] = text_sha256(row.get("text"))
    if not row.get("id"):
        result["legacy_id"] = legacy_entry_id(row.get(subject_field), row.get("text"))
    return result


# ── shard IO ───────────────────────────────────────────────────────────

# path -> ((size, mtime_ns), stubs)
_stub_cache: dict[str, tuple[tuple[int, int], list[dict]]] = {}
_stub_cache_guard = threading.Lock()


def _read_rows(path: str) -> list[dict]:
    try:
        data = read_json_tolerating_replace(path)
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as exc:
        raise ColdStoreUnreadable(f"cold shard unreadable: {os.path.basename(path)}: {exc}") from exc
    if not isinstance(data, list):
        raise ColdStoreUnreadable(f"cold shard is not a list: {os.path.basename(path)}")
    return [row for row in data if isinstance(row, dict)]


def _shard_paths(directory: str, kind: str) -> list[str]:
    folder = cold_dir(directory, kind)
    try:
        return [os.path.join(folder, filename) for filename, _, _ in _list_shard_files(folder)]
    except OSError as exc:
        raise ColdStoreUnreadable(f"cold directory unreadable: {exc}") from exc


def _shard_stubs(path: str, subject_field: str) -> list[dict]:
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return []
    stamp = (st.st_size, st.st_mtime_ns)
    with _stub_cache_guard:
        cached = _stub_cache.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    stubs = [stub(row, subject_field=subject_field) for row in _read_rows(path)]
    with _stub_cache_guard:
        _stub_cache[path] = (stamp, stubs)
    return stubs


def _forget_cached(path: str) -> None:
    with _stub_cache_guard:
        _stub_cache.pop(path, None)


def _subject_field(kind: str) -> str:
    return "entity_key" if kind == "persona" else "entity"


def status(directory: str, *, kind: str = "fact", ids: Iterable[object] | None = None) -> list[dict]:
    """Stubs of the cold rows of one kind, oldest shard first; ``ids`` narrows by row id.

    Raises :class:`ColdStoreUnreadable` when a shard cannot be read, so a
    caller deciding "was this already seen" never mistakes a broken shard
    for an empty one.
    """
    wanted = None if ids is None else {str(item) for item in ids}
    result: list[dict] = []
    for path in _shard_paths(directory, kind):
        for item in _shard_stubs(path, _subject_field(kind)):
            if wanted is None or str(item.get("id")) in wanted:
                result.append(dict(item))
    return result


def private_keys(directory: str, kind: str) -> set[tuple[object, object]]:
    """(subject, id) of the private-chat cold rows of one kind; raises ``ColdStoreUnreadable``."""
    field = _subject_field(kind)
    return {(item.get(field), item.get("id")) for item in status(directory, kind=kind)
            if _private(item) and item.get("id")}


def without_cold_copies(directory: str, kind: str, rows: list, subject_of: Callable[[dict], object]) -> list:
    """``rows`` minus the private-chat rows that are also cold: a cold copy wins over any other.

    Such a pair comes from a cloud save restoring an older file after the row
    went cold here. Group rows are left alone: a subject restore brings them
    back under their own ids. An unreadable cold store filters nothing.
    """
    try:
        cold = private_keys(directory, kind)
    except ColdStoreUnreadable as exc:
        logger.warning(f"[ColdStore] 冷存读取失败，暂不按冷存过滤: {exc}")
        return rows
    if not cold:
        return rows
    return [row for row in rows
            if not (isinstance(row, dict) and _private(row) and (subject_of(row), row.get("id")) in cold)]


async def astatus(directory: str, *, kind: str = "fact", ids: Iterable[object] | None = None) -> list[dict]:
    return await asyncio.to_thread(status, directory, kind=kind, ids=ids)


def read_full(directory: str, *, kind: str = "fact") -> list[dict]:
    """Every cold row of one kind with its text. Restore, export and erasure only."""
    rows: list[dict] = []
    for path in _shard_paths(directory, kind):
        rows.extend(_read_rows(path))
    return rows


async def aread_full(directory: str, *, kind: str = "fact") -> list[dict]:
    return await asyncio.to_thread(read_full, directory, kind=kind)


def append(
    directory: str, rows: list[dict], *,
    identity: Callable[[dict], object] | None = None,
    now: datetime | None = None,
) -> int:
    """Add fact rows to today's cold shard; returns how many were written.

    With ``identity``, a row whose identity is already cold is skipped, so a
    retried two-file commit never stores a row twice. A row without an
    identity (``identity`` returns None) is always written.
    """
    if not rows:
        return 0
    fresh = list(rows)
    if identity is not None:
        held = {key for key in (identity(item) for item in status(directory)) if key is not None}
        fresh = []
        for row in rows:
            key = identity(row)
            if key is not None and key in held:
                continue
            if key is not None:
                held.add(key)
            fresh.append(row)
    if not fresh:
        return 0
    folder = cold_dir(directory)
    written = append_to_shard_sync(folder, [dict(row) for row in fresh], now=now)
    if written:
        _forget_cached(written)
    return len(fresh)


async def aappend(
    directory: str, rows: list[dict], *,
    identity: Callable[[dict], object] | None = None,
    now: datetime | None = None,
) -> int:
    return await asyncio.to_thread(append, directory, rows, identity=identity, now=now)


def rewrite_rows(directory: str, change: Callable[[dict], dict | None], *, kind: str = "fact") -> list[dict]:
    """Rewrite cold rows in place; ``change`` returns the new row, the same row, or None to drop it.

    Every shard is read before any is written, so an unreadable shard fails
    the whole call without a partial rewrite. Returns the original rows that
    were changed or dropped.
    """
    plans: list[tuple[str, list[dict]]] = []
    touched: list[dict] = []
    for path in _shard_paths(directory, kind):
        rows = _read_rows(path)
        kept: list[dict] = []
        changed = False
        for row in rows:
            result = change(row)
            if result is row:
                kept.append(row)
                continue
            changed = True
            touched.append(row)
            if result is not None:
                kept.append(result)
        if changed:
            plans.append((path, kept))
    for path, kept in plans:
        atomic_write_json(path, kept, indent=2, ensure_ascii=False)
        _forget_cached(path)
    return touched


async def arewrite_rows(directory: str, change: Callable[[dict], dict | None], *, kind: str = "fact") -> list[dict]:
    return await asyncio.to_thread(rewrite_rows, directory, change, kind=kind)


# Fields a private restored copy leaves behind: the demotion itself, and what
# would make the copy travel in a sync snapshot as the old (cold) row.
_RESTORE_DROPPED = ("arbitration_archived_at", "arbitration_reason", "superseded_by", "subject_archived_at",
                    "arbitration_restored_at", "merged_from_ids", "_sync_metadata", "source_sync_id", "archived_at")


def restore(
    directory: str, select: Callable[[dict], bool], *,
    activate: Callable[[list[dict]], None],
    restored_at: str | None = None,
) -> list[dict]:
    """Bring the cold fact rows ``select`` picks back to the active pool; returns the revived rows.

    A private-chat row comes back as a copy under a new id (``restored_from``
    names the old one, ``created_at`` is now) and its cold row stays: other
    devices know the old id as cold, and reusing it would have them demote
    the copy again. A group-subject row is not synced, so it comes back under
    its own id, stamped ``restored_at``, and leaves cold storage.

    ``activate(rows)`` must durably add the revived rows to the active pool;
    cold storage changes only after it returns, so a crash in between leaves
    a duplicate (read as cold) rather than a lost row.
    """
    from memory.scopes import subject_from_entry

    now = datetime.now()
    stamp = restored_at or now.isoformat()

    def group(row: dict) -> bool:
        return subject_from_entry(row) is not None

    revived: list[dict] = []
    leaving = False
    for row in read_full(directory):
        if not select(row):
            continue
        if group(row):
            copy = {key: value for key, value in row.items()
                    if key not in ("arbitration_archived_at", "arbitration_reason", "superseded_by",
                                   "subject_archived_at")}
            copy["restored_at"] = stamp
            leaving = True
        else:
            copy = {key: value for key, value in row.items() if key not in _RESTORE_DROPPED}
            digest = hashlib.sha256(f"{row.get('id')}|{now.isoformat()}|{len(revived)}".encode()).hexdigest()[:8]
            copy.update(id=f"fact_{now.strftime('%Y%m%d%H%M%S')}_{digest}", restored_from=row.get("id"),
                        created_at=now.isoformat())
        revived.append(copy)
    if not revived:
        return []
    activate(revived)
    if leaving:
        rewrite_rows(directory, lambda row: None if select(row) and group(row) else row)
    return revived


async def arestore(
    directory: str, select: Callable[[dict], bool], *,
    activate: Callable[[list[dict]], None],
    restored_at: str | None = None,
) -> list[dict]:
    return await asyncio.to_thread(restore, directory, select, activate=activate, restored_at=restored_at)


def forget(directory: str, select: Callable[[dict], bool]) -> list[dict]:
    """Erase matching cold fact rows for good. Raises before any write when a shard is unreadable."""
    return rewrite_rows(directory, lambda row: None if select(row) else row)


async def aforget(directory: str, select: Callable[[dict], bool]) -> list[dict]:
    return await asyncio.to_thread(forget, directory, select)


# ── long-term sync helpers ─────────────────────────────────────────────


def is_managed_relative(relative: str) -> bool:
    """Whether a path relative to the character directory is a cold shard."""
    return bool(_RELATIVE_RE.fullmatch(relative))


def shard_relatives(directory: str) -> list[str]:
    """Every cold shard path, relative to the character directory (names only, nothing read)."""
    return [f"{dirname}/{filename}" for dirname in COLD_DIRNAMES.values()
            for filename, _, _ in _list_shard_files(os.path.join(str(directory), dirname))]


def new_shard_relative(kind: str, date: datetime, taken: Iterable[str] = ()) -> str:
    """A fresh shard path relative to the character directory that is not taken yet."""
    taken = set(taken)
    while True:
        relative = f"{COLD_DIRNAMES[kind]}/{shard_filename_for(date)}"
        if relative not in taken:
            return relative


def sync_stubs(directory: str, *, repair: Callable[[str, list[dict]], None] | None = None) -> dict[str, list[dict]]:
    """Stubs of every cold row for the long-term sync, by kind.

    Older persona archive rows omit their subject; the durable archive event
    in ``events.ndjson`` names it. Rows resolved that way are written back
    through ``repair(relative, rows)`` when given; a row that stays ambiguous
    raises :class:`ColdArchiveSubjectUnavailable`, because guessing a subject
    could publish a row under the wrong one.
    """
    result = {kind: status(directory, kind=kind) for kind in COLD_DIRNAMES}
    if any(_private(item) and not item.get("entity_key") for item in result["persona"]):
        shards = _resolve_persona_subjects(directory, repair)
        result["persona"] = [stub(row, subject_field="entity_key") for rows in shards.values() for row in rows]
    return result


def _private(row: dict) -> bool:
    return not (row.get("scope") or row.get("subject_kind"))


def _resolve_persona_subjects(directory, repair):
    """Fill in missing persona subjects from the archive events; returns the shards by filename."""
    subjects_known = ("master", "neko", "relationship")
    folder = cold_dir(directory, "persona")
    shards = {os.path.basename(path): _read_rows(path) for path in _shard_paths(directory, "persona")}
    unresolved = {(filename, row.get("id")) for filename, rows in shards.items()
                  for row in rows if _private(row) and not row.get("entity_key")}
    copies = Counter((filename, row.get("id")) for filename, rows in shards.items()
                     for row in rows if _private(row))
    subjects: dict = {}
    try:
        with open(os.path.join(str(directory), "events.ndjson"), encoding="utf-8") as events:
            for line in events:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
                    continue
                payload = event["payload"]
                if not isinstance(payload.get("archive_shard_path"), str) or not isinstance(payload.get("entry_id"), str):
                    continue
                key = (payload["archive_shard_path"], payload["entry_id"])
                if key in unresolved and event.get("type") == "persona.fact_added" and payload.get("entity_key") in subjects_known:
                    snapshot = payload.get("entry_snapshot")
                    archived_at = payload.get("archived_at") or (snapshot.get("archived_at") if isinstance(snapshot, dict) else None)
                    if archived_at is not None and not isinstance(archived_at, str):
                        continue
                    subjects.setdefault(key, set()).add((archived_at, payload["entity_key"]))
    except FileNotFoundError:
        pass
    unavailable = False
    for filename, rows in shards.items():
        repaired = False
        for row in rows:
            if not (_private(row) and not row.get("entity_key")):
                continue
            key = (filename, row.get("id"))
            candidates = subjects.get(key, ())
            matched = {subject for archived_at, subject in candidates if archived_at and archived_at == row.get("archived_at")}
            if not matched and copies[key] == 1 and all(not archived_at for archived_at, _ in candidates):
                matched = {subject for _, subject in candidates}
            if len(matched) != 1:
                unavailable = True
                continue
            row["entity_key"] = matched.pop()
            repaired = True
        if repaired and repair is not None:
            repair(f"{COLD_DIRNAMES['persona']}/{filename}", rows)
            _forget_cached(os.path.join(folder, filename))
    if unavailable:
        raise ColdArchiveSubjectUnavailable()
    return shards
