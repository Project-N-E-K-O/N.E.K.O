# -*- coding: utf-8 -*-
"""
Sharded archive storage helpers (memory-evidence-rfc §3.5.4).

Both `memory.reflection` and `memory.persona` archive entries into per-day
shard files under a directory like ``memory/<char>/<kind>_archive/``. To
keep symmetry (CLAUDE.md "对偶性是硬性要求"), the sharding logic lives in
this single module — both managers just declare which directory and call
in.

File naming: ``<YYYY-MM-DD>_<uuid8>.json`` where the date is the archival
date (local-clock) and ``<uuid8>`` is the first 8 hex chars of a uuid4.
A new shard is created when today's most recent shard already has
``ARCHIVE_FILE_MAX_ENTRIES`` entries (RFC §3.5.4).

Migration helper (`migrate_flat_archive_to_shards`) is reused for the
one-shot migration of legacy ``reflections_archive.json`` (RFC §3.5.5).
Persona has no legacy flat file but the helper is generic so any future
consumer can call it.

All public helpers are async-first; callers provide sync paths via
``asyncio.to_thread`` if they need a sync twin (we don't expose one
because the only callers — the periodic archive sweep loop and the
one-shot startup migration — are async).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime
from typing import Callable, Iterable

from config import ARCHIVE_FILE_MAX_ENTRIES
from utils.file_utils import (
    atomic_write_json,
    atomic_write_json_async,
    read_json_async,
)
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")

# Shard filename: <YYYY-MM-DD>_<uuid8>.json
# uuid8 captured for migration uniqueness assertions; date captured to
# group multiple-shard same-day appends.
_SHARD_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<uuid8>[0-9a-f]{8})\.json$")

# Sentinel file written under the archive dir once the legacy flat-file
# migration has been applied for that character. Lets us short-circuit
# `migrate_flat_archive_to_shards` on every boot without re-scanning the
# directory contents.
MIGRATION_SENTINEL_FILENAME = ".migrated_from_flat"


def _new_uuid8() -> str:
    """First 8 hex chars of a uuid4. Collision risk for our use case
    (per-day, ≤ tens-of-shards) is negligible; we still re-roll on
    collision in `_pick_shard_path`."""
    return uuid.uuid4().hex[:8]


def _list_shard_files(archive_dir: str) -> list[tuple[str, str, str]]:
    """Return [(filename, date_str, uuid8), ...] for valid shards in the
    directory, sorted by filename. Non-matching files (incl. the migration
    sentinel) are silently ignored."""
    if not os.path.isdir(archive_dir):
        return []
    entries: list[tuple[str, str, str]] = []
    for fn in os.listdir(archive_dir):
        m = _SHARD_RE.match(fn)
        if m:
            entries.append((fn, m.group("date"), m.group("uuid8")))
    entries.sort(key=lambda t: t[0])
    return entries


def _pick_shard_path_for_today(
    archive_dir: str, today: str, current_size_by_filename: dict[str, int],
) -> str:
    """Decide which shard path to append into for `today`.

    Strategy (chatgpt-codex review #934 — earlier "lexical-last only"
    strategy was buggy because the uuid8 suffix is RANDOM, so the
    lexically-last shard is not necessarily the most-recently-written one
    nor the one with capacity. If lex-last happened to be full but an
    earlier same-day shard still had room, every append rolled a fresh
    shard → unbounded shard proliferation):

    - Scan ALL of today's shards in lex order; return the FIRST one with
      ``current_size < ARCHIVE_FILE_MAX_ENTRIES``. This naturally coalesces
      same-day appends into earlier shards (a shard fills before the next
      one is touched) while staying deterministic for the
      `apick_today_shard_path` predict-then-write contract.
    - If every same-day shard is full (or there are none) → mint a fresh
      uuid8 shard, re-rolling on the (vanishingly rare) filename collision.
    """
    todays = [
        (fn, u8) for (fn, d, u8) in _list_shard_files(archive_dir)
        if d == today
    ]
    for fn, _ in todays:
        if current_size_by_filename.get(fn, 0) < ARCHIVE_FILE_MAX_ENTRIES:
            return os.path.join(archive_dir, fn)

    existing_names = {fn for fn, _, _ in _list_shard_files(archive_dir)}
    while True:
        candidate = f"{today}_{_new_uuid8()}.json"
        if candidate not in existing_names:
            return os.path.join(archive_dir, candidate)


class ShardCorruptError(Exception):
    """Raised by `_aread_shard` when a shard file exists but is unusable
    (non-JSON / not a list). Callers MUST catch this and treat the shard
    as full so the picker doesn't reuse + overwrite the corrupt file
    (coderabbit PR #934 round-2 Major #1 — corrupt shard isolation).

    The corrupt file is left untouched on disk for manual recovery.
    """

    def __init__(self, path: str, reason: str):
        super().__init__(f"shard corrupt at {path}: {reason}")
        self.path = path
        self.reason = reason


async def _aread_shard(path: str) -> list[dict]:
    """Read a shard file's contents.

    Returns [] on missing (truly empty / non-existent file). Raises
    ``ShardCorruptError`` on non-JSON / non-list contents — callers
    must catch and treat as full so the picker can't reuse the file
    and `atomic_write_json_async` can't overwrite the corrupt original
    (coderabbit PR #934 round-2 Major #1).

    Symmetric to the sync twin's `(json.JSONDecodeError, OSError)` →
    `ARCHIVE_FILE_MAX_ENTRIES` defensive posture in
    ``append_to_shard_sync`` (lines 277-285).
    """
    if not await asyncio.to_thread(os.path.exists, path):
        return []
    try:
        data = await read_json_async(path)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[ArchiveShard] 读取分片失败 {path}: {e}")
        raise ShardCorruptError(path, str(e)) from e
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    logger.warning(f"[ArchiveShard] 分片不是 list，忽略: {path}")
    raise ShardCorruptError(path, f"top-level is {type(data).__name__}, not list")


async def apick_today_shard_path(
    archive_dir: str, *, now: datetime | None = None,
) -> str:
    """Resolve the path of the shard a single-entry append would land in,
    materializing it (as an empty list) so a subsequent call sees the
    same file.

    Used by archive callers that need to stamp `archive_shard_path` into
    the entry BEFORE persisting it (so the on-disk record matches the
    value consumers see). The two-step "pick → mutate entry → append"
    contract requires the picked path to be deterministic across the
    two reads; we materialize an empty shard here to lock the choice.

    Caller MUST follow this with a single-entry `aappend_to_shard`. For
    multi-entry appends call `aappend_to_shard` directly — the chunked
    path can spill across multiple shards which a single "predict" call
    cannot describe.
    """
    await asyncio.to_thread(os.makedirs, archive_dir, exist_ok=True)
    if now is None:
        now = datetime.now()
    today = now.date().isoformat()
    sizes: dict[str, int] = {}
    for fn, date_str, _ in _list_shard_files(archive_dir):
        if date_str == today:
            shard_path = os.path.join(archive_dir, fn)
            try:
                shard_data = await _aread_shard(shard_path)
                sizes[fn] = len(shard_data)
            except ShardCorruptError as e:
                # Coderabbit PR #934 round-2 Major #1: corrupt shards
                # are reported as full so the picker can't reuse them
                # (else atomic_write_json_async would overwrite the
                # corrupt original and lose any salvageable data).
                logger.warning(
                    f"[ArchiveShard] 损坏分片视为已满，跳过复用 {shard_path}: {e}"
                )
                sizes[fn] = ARCHIVE_FILE_MAX_ENTRIES
            except Exception as e:
                logger.warning(f"[ArchiveShard] 探测分片大小失败 {shard_path}: {e}")
                sizes[fn] = ARCHIVE_FILE_MAX_ENTRIES
    path = _pick_shard_path_for_today(archive_dir, today, sizes)
    if not await asyncio.to_thread(os.path.exists, path):
        # Materialize as empty list so subsequent _list_shard_files calls
        # see this filename and don't roll a different uuid8 on the next
        # pick. Callers append to this file via `aappend_to_shard` which
        # reads + extends + atomic-writes — the empty seed is correctly
        # consumed.
        await atomic_write_json_async(path, [], indent=2, ensure_ascii=False)
    return path


async def aappend_to_shard(
    archive_dir: str, entries: list[dict], *,
    now: datetime | None = None,
    stamper: Callable[[list[dict], str], None] | None = None,
) -> str:
    """Append `entries` into today's shard (creating new ones as needed).

    Returns the absolute path of the LAST shard written to. If the entries
    overflow `ARCHIVE_FILE_MAX_ENTRIES` for today's currently-open shard,
    they spill into one or more freshly-created shards.

    The optional ``stamper`` callback (chatgpt-codex / coderabbit review
    #934) is invoked as ``stamper(chunk, shard_basename)`` immediately
    BEFORE the chunk is serialized to disk — this lets callers attach
    per-chunk metadata (notably ``archive_shard_path`` so on-disk records
    carry their own correct shard filename even when overflow spreads
    one batch across multiple shards). Stamper mutates entries in place;
    its return value is ignored. Without a stamper the previous
    "write-only" behavior is preserved.

    Atomicity:
      Each shard write goes through `atomic_write_json_async`. The
      sequence of writes across shards is NOT a single transaction — but
      since each shard is independent and the caller's mutation
      (removing the entry from the active view) happens AFTER this call
      via record_and_save, the worst-case crash window is "entry
      duplicated in archive but still in active view" → next sweep can
      observe + retry. Acceptable per RFC §3.11 ("preserved provenance").
    """
    if not entries:
        return ""
    await asyncio.to_thread(os.makedirs, archive_dir, exist_ok=True)
    if now is None:
        now = datetime.now()
    today = now.date().isoformat()

    # Compute current sizes for all of today's shards in one pass — we
    # only really need the "latest today" but precomputing keeps the
    # loop logic uniform when overflow forces us to roll a new shard.
    sizes: dict[str, int] = {}
    for fn, date_str, _ in _list_shard_files(archive_dir):
        if date_str == today:
            shard_path = os.path.join(archive_dir, fn)
            try:
                shard_data = await _aread_shard(shard_path)
                sizes[fn] = len(shard_data)
            except ShardCorruptError as e:
                # Coderabbit PR #934 round-2 Major #1: corrupt shards
                # are reported as full so the picker can't reuse them
                # (else atomic_write_json_async would overwrite the
                # corrupt original and lose any salvageable data).
                logger.warning(
                    f"[ArchiveShard] 损坏分片视为已满，跳过复用 {shard_path}: {e}"
                )
                sizes[fn] = ARCHIVE_FILE_MAX_ENTRIES
            except Exception as e:
                logger.warning(f"[ArchiveShard] 探测分片大小失败 {shard_path}: {e}")
                sizes[fn] = ARCHIVE_FILE_MAX_ENTRIES  # treat as full → don't reuse

    last_written = ""
    remaining = list(entries)
    while remaining:
        shard_path = _pick_shard_path_for_today(archive_dir, today, sizes)
        try:
            existing = await _aread_shard(shard_path)
        except ShardCorruptError as e:
            # The picker just selected a fresh shard or one we already
            # sized as <max above. Reaching this branch means the
            # filename we picked turns out to be corrupt on the second
            # read (e.g. concurrent writer truncated it). Mark full
            # and re-pick — same defensive posture as the size probe.
            logger.warning(
                f"[ArchiveShard] 选中分片读取损坏，标记为已满重新选 {shard_path}: {e}"
            )
            sizes[os.path.basename(shard_path)] = ARCHIVE_FILE_MAX_ENTRIES
            continue
        capacity = ARCHIVE_FILE_MAX_ENTRIES - len(existing)
        if capacity <= 0:
            # Brand-new shard rolled in, but a concurrent writer filled
            # it between our list and our read. Mark as full and re-pick.
            sizes[os.path.basename(shard_path)] = ARCHIVE_FILE_MAX_ENTRIES
            continue
        chunk = remaining[:capacity]
        remaining = remaining[capacity:]
        if stamper is not None:
            # Stamp BEFORE merge+write so the on-disk record matches
            # what consumers see. Per-chunk basename is correct even
            # under overflow.
            stamper(chunk, os.path.basename(shard_path))
        merged = existing + chunk
        await atomic_write_json_async(shard_path, merged, indent=2, ensure_ascii=False)
        sizes[os.path.basename(shard_path)] = len(merged)
        last_written = shard_path
    return last_written


def append_to_shard_sync(
    archive_dir: str, entries: list[dict], *,
    now: datetime | None = None,
    stamper: Callable[[list[dict], str], None] | None = None,
) -> str:
    """Sync twin of `aappend_to_shard` — symmetry with sync save paths
    (CLAUDE.md). Used by sync test fixtures and by the one-shot migration
    when called from a non-async context.

    See ``aappend_to_shard`` for ``stamper`` semantics.
    """
    if not entries:
        return ""
    os.makedirs(archive_dir, exist_ok=True)
    if now is None:
        now = datetime.now()
    today = now.date().isoformat()

    sizes: dict[str, int] = {}
    for fn, date_str, _ in _list_shard_files(archive_dir):
        if date_str == today:
            shard_path = os.path.join(archive_dir, fn)
            try:
                with open(shard_path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    sizes[fn] = len(data)
                else:
                    sizes[fn] = ARCHIVE_FILE_MAX_ENTRIES
            except (json.JSONDecodeError, OSError):
                sizes[fn] = ARCHIVE_FILE_MAX_ENTRIES

    last_written = ""
    remaining = list(entries)
    while remaining:
        shard_path = _pick_shard_path_for_today(archive_dir, today, sizes)
        existing: list[dict] = []
        if os.path.exists(shard_path):
            try:
                with open(shard_path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    existing = [x for x in data if isinstance(x, dict)]
            except (json.JSONDecodeError, OSError):
                existing = []
        capacity = ARCHIVE_FILE_MAX_ENTRIES - len(existing)
        if capacity <= 0:
            sizes[os.path.basename(shard_path)] = ARCHIVE_FILE_MAX_ENTRIES
            continue
        chunk = remaining[:capacity]
        remaining = remaining[capacity:]
        if stamper is not None:
            stamper(chunk, os.path.basename(shard_path))
        merged = existing + chunk
        atomic_write_json(shard_path, merged, indent=2, ensure_ascii=False)
        sizes[os.path.basename(shard_path)] = len(merged)
        last_written = shard_path
    return last_written


def ensure_entry_in_named_shard_sync(
    archive_dir: str, shard_basename: str, entry: dict,
) -> bool:
    """Idempotent helper: ensure ``entry`` (keyed by ``entry['id']``) is
    present in the shard file ``<archive_dir>/<shard_basename>``,
    creating the file if missing.

    Used by reconciler archive handlers (`evidence_handlers.py`) to
    self-heal a missing shard write. Background: archive flow writes
    the event log first then appends to a shard; if the process dies
    between those two steps, the active view loses the entry but the
    shard never gets it. Replaying the event lets the handler call us
    here so the entry is reconstructed from the event payload's
    ``entry_snapshot`` (coderabbit PR #934 round-2 Major #3).

    Returns True if a write happened (entry was missing or shard was
    absent); False if the entry was already present.

    Corruption posture: if the named shard exists but is corrupt
    (non-JSON / not list), we leave it untouched (raise nothing — log
    + return False) rather than overwriting it. Same isolation rule
    as `_aread_shard` (Major #1). The replay will be retried on the
    next reconciler boot; an operator can fix the corrupt shard
    manually in between.
    """
    if not isinstance(entry, dict):
        return False
    eid = entry.get('id')
    if not eid:
        return False
    os.makedirs(archive_dir, exist_ok=True)
    shard_path = os.path.join(archive_dir, shard_basename)
    existing: list[dict] = []
    if os.path.exists(shard_path):
        try:
            with open(shard_path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                f"[ArchiveShard] self-heal 跳过损坏分片 {shard_path}: {e}"
            )
            return False
        if not isinstance(data, list):
            logger.warning(
                f"[ArchiveShard] self-heal 跳过非 list 分片 {shard_path}"
            )
            return False
        existing = [x for x in data if isinstance(x, dict)]
        for e in existing:
            if e.get('id') == eid:
                return False  # Already present → idempotent no-op.
    merged = existing + [dict(entry)]
    atomic_write_json(shard_path, merged, indent=2, ensure_ascii=False)
    return True


def shard_filename_for(now: datetime) -> str:
    """Helper for consumers that need to predict / record a shard
    filename without doing the IO. Caller should pair with
    `_new_uuid8()` if it needs a fresh suffix.

    Currently only used by tests / future debug tooling — production
    code should use `aappend_to_shard` which picks the path internally.
    """
    return f"{now.date().isoformat()}_{_new_uuid8()}.json"


# ── one-shot migration of legacy flat archive ──────────────────────────


def _entries_grouped_by_archived_date(
    entries: Iterable[dict],
) -> dict[str, list[dict]]:
    """Group archive entries by their `archived_at` ISO-date prefix.

    Entries with missing / unparsable `archived_at` fall under today's
    bucket (best-effort: better to keep the data than drop it).
    """
    today_str = datetime.now().date().isoformat()
    buckets: dict[str, list[dict]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        archived_at = entry.get("archived_at")
        date_str: str
        if isinstance(archived_at, str) and archived_at:
            try:
                date_str = datetime.fromisoformat(archived_at).date().isoformat()
            except ValueError:
                date_str = today_str
        else:
            date_str = today_str
        buckets.setdefault(date_str, []).append(entry)
    return buckets


def _write_shards_for_date(
    archive_dir: str, date_str: str, entries: list[dict],
) -> int:
    """Distribute `entries` into shards under `archive_dir`, all bearing
    `date_str` as their date prefix. Returns the number of shard files
    written.
    """
    written = 0
    chunk_idx = 0
    while chunk_idx < len(entries):
        chunk = entries[chunk_idx:chunk_idx + ARCHIVE_FILE_MAX_ENTRIES]
        # Re-roll on collision against existing shards from concurrent
        # daily writes; very unlikely but cheap.
        existing_names = {fn for fn, _, _ in _list_shard_files(archive_dir)}
        while True:
            candidate = f"{date_str}_{_new_uuid8()}.json"
            if candidate not in existing_names:
                break
        path = os.path.join(archive_dir, candidate)
        atomic_write_json(path, chunk, indent=2, ensure_ascii=False)
        written += 1
        chunk_idx += ARCHIVE_FILE_MAX_ENTRIES
    return written


def migrate_flat_archive_to_shards_sync(
    flat_path: str, archive_dir: str,
) -> tuple[bool, int, int]:
    """Migrate a legacy flat archive file into the sharded directory.

    Returns ``(migrated, entries_count, shards_written)``.

    Idempotency:
      - If the migration sentinel file already exists in `archive_dir`,
        returns ``(False, 0, 0)`` immediately.
      - If `flat_path` does not exist, also returns ``(False, 0, 0)`` —
        nothing to migrate.

    Failure semantics:
      If shard writes succeed for some but not all groups (e.g. disk
      full mid-way), this raises so the caller can leave the flat file
      intact as fallback (RFC §3.5.5: "迁移失败 → 保留 flat 文件
      fallback"). Successful writes accumulate; partial state is
      recoverable on next boot since the flat file remains and the
      sentinel is only written after success.
    """
    sentinel_path = os.path.join(archive_dir, MIGRATION_SENTINEL_FILENAME)
    if os.path.exists(sentinel_path):
        return False, 0, 0
    if not os.path.exists(flat_path):
        return False, 0, 0

    with open(flat_path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                f"[ArchiveShard] 旧 flat 归档 {flat_path} 解析失败，跳过迁移 (保留原文件): {e}"
            )
            return False, 0, 0

    if not isinstance(data, list):
        logger.warning(
            f"[ArchiveShard] 旧 flat 归档 {flat_path} 不是 list，跳过迁移 (保留原文件)"
        )
        return False, 0, 0

    os.makedirs(archive_dir, exist_ok=True)
    buckets = _entries_grouped_by_archived_date(data)
    total_entries = sum(len(v) for v in buckets.values())
    total_shards = 0
    for date_str, group in sorted(buckets.items()):
        total_shards += _write_shards_for_date(archive_dir, date_str, group)

    # Write the sentinel BEFORE deleting the flat file: a crash between
    # the two leaves us with both the sentinel and the original — next
    # boot's idempotent guard short-circuits, the flat file then becomes
    # human-deletable cruft. Reverse order would risk losing data.
    atomic_write_json(sentinel_path, {
        "migrated_at": datetime.now().isoformat(),
        "source": os.path.basename(flat_path),
        "entries": total_entries,
        "shards": total_shards,
    })

    try:
        os.remove(flat_path)
    except OSError as e:
        # Sentinel is in place; the leftover flat file is harmless until
        # an operator removes it manually. Log loud so it's noticed.
        logger.warning(
            f"[ArchiveShard] 迁移成功但删除旧 flat 文件 {flat_path} 失败: {e}"
        )

    logger.info(
        f"[ArchiveShard] 迁移 {flat_path} → {archive_dir}: "
        f"{total_entries} 条 → {total_shards} 个分片"
    )
    return True, total_entries, total_shards


async def amigrate_flat_archive_to_shards(
    flat_path: str, archive_dir: str,
) -> tuple[bool, int, int]:
    """Async twin — see `migrate_flat_archive_to_shards_sync` docstring.

    The migration is one-shot at startup; throughput doesn't justify a
    truly async implementation, so we just hop to a worker thread.
    """
    return await asyncio.to_thread(
        migrate_flat_archive_to_shards_sync, flat_path, archive_dir,
    )
