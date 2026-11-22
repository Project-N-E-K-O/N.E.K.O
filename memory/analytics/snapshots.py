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

"""Lightweight per-character safety snapshots for the Memory Browser.

Allows users to capture a safety checkpoint before batch edits or merges, and
roll back cleanly if needed. Restoring always creates an automatic
``pre_restore`` backup first, validates cloudsave writability, writes files
atomically, and invalidates in-memory manager caches under their locks.

Storage (format 2): every managed JSON file is stored gzip-compressed and
without its embedding vectors. A restore takes the vectors back from the live
files it replaces, matching each entry by id and text fingerprint; entries
with no match keep empty vector fields for the background embedding worker.
Format 1 snapshots (plain copies with vectors) stay listable and restorable.

Restores go through the event log as one ``memory.snapshot_restored`` event
whose file swap runs inside the same ``record_and_save`` critical section, so
a crash halfway through the swap is finished on the next replay.

Manual snapshots and automatic ones (``pre_merge`` / ``pre_restore``) are
pruned separately, each bucket rolling out its oldest entries.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
from contextlib import AsyncExitStack, ExitStack
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from config import MEMORY_BROWSER_AUTO_SNAPSHOT_MAX_KEEP, MEMORY_BROWSER_SNAPSHOT_MAX_KEEP
from memory import cold_store
from memory.event_log import EVT_MEMORY_SNAPSHOT_RESTORED
from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.config_manager import get_config_manager
from utils.file_utils import atomic_write_bytes, atomic_write_json
from utils.logger_config import get_module_logger

from .imprint import _CORE_ENTITIES, _META_ENTITY, _read_json_safe

logger = get_module_logger(__name__, "Memory")

_SNAPSHOTS_DIRNAME = "browser_snapshots"
_SNAPSHOT_FILES: tuple[str, ...] = (
    "persona.json",
    "reflections.json",
    "facts.json",
    "facts_archive.json",
    "reflections_archive.json",
    "persona_corrections.json",
    "user_directives.json",
)
_SAFE_SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

# meta.json "format": 2 = gzip files without vectors; absent = plain copies.
_STORAGE_FORMAT = 2
_COMPRESSED_SUFFIX = ".gz"
AUTO_SNAPSHOT_REASONS: frozenset[str] = frozenset({"pre_merge", "pre_restore"})
# Several merges in a row share the backup taken before the first one.
PRE_MERGE_REUSE_SECONDS = 600

_VECTOR_KEYS: tuple[str, ...] = ("embedding", "embedding_text_sha256", "embedding_model_id")
_LEGACY_VECTOR_KEYS: tuple[str, ...] = ("embedding_model", "embedding_text_hash")


# ── Vector stripping / fingerprints ──────────────────────────────────────


def _walk_dicts(obj: Any) -> Iterator[dict]:
    """Yield every dict nested anywhere in a parsed JSON value."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
        elif isinstance(cur, list):
            stack.extend(v for v in cur if isinstance(v, (dict, list)))


def _strip_vectors(data: Any) -> Any:
    """Empty the vector triple and drop legacy vector keys, in place."""
    for d in _walk_dicts(data):
        for key in _VECTOR_KEYS:
            if key in d:
                d[key] = None
        for key in _LEGACY_VECTOR_KEYS:
            d.pop(key, None)
    return data


def content_fingerprint(raw: bytes) -> str:
    """Fingerprint of a managed file that ignores vectors and formatting.

    A file restored with recovered vectors, the compact copy stored in a
    snapshot, and the same file after the background worker re-embedded it
    all share one fingerprint; any change to the memory content does not.
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "raw:" + hashlib.sha256(raw).hexdigest()
    for d in _walk_dicts(data):
        for key in (*_VECTOR_KEYS, *_LEGACY_VECTOR_KEYS):
            d.pop(key, None)
    canon = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "json:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _text_key(entry: dict) -> tuple[str, str] | None:
    eid = entry.get("id")
    text = entry.get("text")
    if not isinstance(eid, str) or not eid or not isinstance(text, str):
        return None
    return eid, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _vector_donors(raw_files: Iterable[bytes]) -> dict[tuple[str, str], dict]:
    """Index live vectors by (entry id, text fingerprint)."""
    donors: dict[tuple[str, str], dict] = {}
    for raw in raw_files:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        for d in _walk_dicts(data):
            if not d.get("embedding"):
                continue
            key = _text_key(d)
            if key is None or key in donors:
                continue
            donors[key] = {k: d[k] for k in (*_VECTOR_KEYS, *_LEGACY_VECTOR_KEYS) if k in d}
    return donors


def _materialize(stored: bytes, donors: dict[tuple[str, str], dict]) -> bytes:
    """Turn a stored snapshot file into the bytes written back to the live file."""
    try:
        data = json.loads(stored.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return stored
    for d in _walk_dicts(data):
        if d.get("embedding"):
            continue
        key = _text_key(d)
        donor = donors.get(key) if key is not None else None
        if donor:
            d.update(donor)
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def _encode_for_storage(raw: bytes) -> bytes:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return gzip.compress(raw, mtime=0)
    compact = json.dumps(_strip_vectors(data), ensure_ascii=False, separators=(",", ":"))
    return gzip.compress(compact.encode("utf-8"), mtime=0)


# ── Paths / listing ───────────────────────────────────────────────────────


def _is_allowed_snapshot_relpath(relpath: str) -> bool:
    """Validate a posix relative path stored in ``meta.json['files']``."""
    if not isinstance(relpath, str) or not relpath or "\\" in relpath or relpath.startswith("/"):
        return False
    if relpath in _SNAPSHOT_FILES:
        return True
    return cold_store.is_managed_relative(relpath)


def _collect_managed_relpaths(char_dir: Path) -> list[str]:
    """Return all currently existing managed flat files and cold archive shard files."""
    relpaths: list[str] = []
    for fname in _SNAPSHOT_FILES:
        if (char_dir / fname).is_file():
            relpaths.append(fname)
    for rel in sorted(cold_store.shard_relatives(str(char_dir))):
        if cold_store.is_managed_relative(rel) and (char_dir / rel).is_file():
            relpaths.append(rel)
    return relpaths


def _snapshots_root(char_dir: Path) -> Path:
    return char_dir / _SNAPSHOTS_DIRNAME


def _count_snapshot_stats(char_dir: Path) -> dict[str, int]:
    persona_raw = _read_json_safe(char_dir / "persona.json", {})
    persona_count = 0
    if isinstance(persona_raw, dict):
        for ent, sec in persona_raw.items():
            if ent == _META_ENTITY or ent not in _CORE_ENTITIES or not isinstance(sec, dict):
                continue
            for entry in sec.get("facts") or []:
                if isinstance(entry, dict) and not (
                    entry.get("protected") or entry.get("source") == "character_card"
                ):
                    persona_count += 1
    reflections_raw = _read_json_safe(char_dir / "reflections.json", [])
    facts_raw = _read_json_safe(char_dir / "facts.json", [])
    return {
        "persona_count": persona_count,
        "reflections_count": len(reflections_raw) if isinstance(reflections_raw, list) else 0,
        "facts_count": len(facts_raw) if isinstance(facts_raw, list) else 0,
    }


def _is_auto_reason(reason: object) -> bool:
    return str(reason or "") in AUTO_SNAPSHOT_REASONS


def _read_metas(root: Path) -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    if not root.is_dir():
        return out
    for child in root.iterdir():
        if not child.is_dir():
            continue
        meta = _read_json_safe(child / "meta.json", {})
        out.append((child, meta if isinstance(meta, dict) else {}))
    return out


def _prune_old_snapshots_sync(
    root: Path,
    max_keep: int,
    *,
    auto_max_keep: int = MEMORY_BROWSER_AUTO_SNAPSHOT_MAX_KEEP,
    protect: Iterable[str] = (),
) -> None:
    """Roll out the oldest manual and automatic snapshots, each bucket on its own."""
    protected = set(protect)
    buckets: dict[bool, list[tuple[str, Path]]] = {False: [], True: []}
    for child, meta in _read_metas(root):
        created_at = str(meta.get("created_at") or "")
        buckets[_is_auto_reason(meta.get("reason"))].append((created_at or child.name, child))
    for is_auto, entries in buckets.items():
        limit = auto_max_keep if is_auto else max_keep
        if limit <= 0:
            continue
        entries.sort(key=lambda pair: pair[0], reverse=True)
        for _, stale_dir in entries[limit:]:
            if stale_dir.name in protected:
                continue
            try:
                shutil.rmtree(stale_dir, ignore_errors=True)
            except Exception as exc:
                logger.debug("[MemorySnapshots] failed to prune %s: %s", stale_dir, exc)


def list_snapshots(
    character_name: str,
    *,
    memory_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Synchronously list available safety snapshots for a character (newest first)."""
    base = Path(memory_dir) if memory_dir is not None else Path(get_config_manager().memory_dir)
    root = _snapshots_root(base / character_name)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        meta = _read_json_safe(child / "meta.json", None)
        if not isinstance(meta, dict):
            continue
        snap_id = str(meta.get("snapshot_id") or child.name)
        if not _SAFE_SNAPSHOT_ID_RE.match(snap_id):
            continue
        out.append({
            "snapshot_id": snap_id,
            "label": str(meta.get("label") or snap_id),
            "reason": str(meta.get("reason") or "manual"),
            "created_at": str(meta.get("created_at") or ""),
            "files": list(meta.get("files") or []),
            "stats": meta.get("stats") if isinstance(meta.get("stats"), dict) else {},
        })
    out.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return out


def _recent_snapshot_meta(root: Path, reason: str, now: datetime, within_seconds: int) -> dict | None:
    """Newest snapshot of ``reason`` created less than ``within_seconds`` ago."""
    best: tuple[datetime, dict] | None = None
    for child, meta in _read_metas(root):
        if meta.get("reason") != reason or not isinstance(meta.get("files"), list):
            continue
        try:
            created = datetime.fromisoformat(str(meta.get("created_at") or ""))
        except ValueError:
            continue
        age = (now - created).total_seconds()
        if age < 0 or age >= within_seconds:
            continue
        if str(meta.get("snapshot_id") or "") != child.name:
            continue
        if best is None or created > best[0]:
            best = (created, meta)
    return best[1] if best else None


def create_snapshot_sync(
    character_name: str,
    *,
    label: str = "",
    reason: str = "manual",
    memory_dir: Path | None = None,
    max_keep: int = MEMORY_BROWSER_SNAPSHOT_MAX_KEEP,
    auto_max_keep: int = MEMORY_BROWSER_AUTO_SNAPSHOT_MAX_KEEP,
    protect: Iterable[str] = (),
) -> dict[str, Any]:
    """Synchronously create a safety snapshot of core long-term memory JSON files.

    A ``pre_merge`` request within ``PRE_MERGE_REUSE_SECONDS`` of the previous
    one returns that snapshot instead of taking a new one. ``protect`` names
    snapshot ids pruning must leave alone (the one a restore is reading).
    """
    base = Path(memory_dir) if memory_dir is not None else Path(get_config_manager().memory_dir)
    char_dir = base / character_name
    char_dir.mkdir(parents=True, exist_ok=True)
    root = _snapshots_root(char_dir)
    root.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    clean_reason = str(reason or "manual")[:32]
    if clean_reason == "pre_merge":
        recent = _recent_snapshot_meta(root, clean_reason, now, PRE_MERGE_REUSE_SECONDS)
        if recent is not None:
            return recent

    snap_id = f"snap_{now.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"
    snap_dir = root / snap_id
    snap_dir.mkdir(parents=True, exist_ok=True)

    copied_files: list[str] = []
    for relpath in _collect_managed_relpaths(char_dir):
        src = char_dir / relpath
        if src.is_file():
            data = src.read_bytes()
            dst = snap_dir / (relpath + _COMPRESSED_SUFFIX)
            dst.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(str(dst), _encode_for_storage(data))
            copied_files.append(relpath)

    clean_label = str(label or "").strip()[:80]
    if not clean_label:
        clean_label = now.strftime("%Y-%m-%d %H:%M")

    meta = {
        "snapshot_id": snap_id,
        "character_name": character_name,
        "label": clean_label,
        "reason": clean_reason,
        "created_at": now.isoformat(),
        "format": _STORAGE_FORMAT,
        "files": copied_files,
        "stats": _count_snapshot_stats(char_dir),
    }
    atomic_write_json(str(snap_dir / "meta.json"), meta, indent=2, ensure_ascii=False)
    _prune_old_snapshots_sync(root, max_keep=max_keep, auto_max_keep=auto_max_keep, protect=protect)
    return meta


# ── Restore ───────────────────────────────────────────────────────────────


def _resolve_snapshot_dir(char_dir: Path, snapshot_id: str) -> Path:
    if not _SAFE_SNAPSHOT_ID_RE.match(str(snapshot_id or "")):
        raise ValueError("Invalid snapshot_id")
    root = _snapshots_root(char_dir)
    snap_dir = (root / snapshot_id).resolve()
    try:
        snap_dir.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Invalid snapshot path") from exc
    return snap_dir


def _load_snapshot_source(char_dir: Path, snapshot_id: str) -> tuple[dict, list[str], dict[str, bytes]]:
    """Read and validate a snapshot: ``(meta, files, stored bytes per file)``.

    Stored bytes are the decompressed JSON (vectors empty for format 2, as
    copied for format 1). Raises ValueError / FileNotFoundError on anything
    that would make the restore incomplete.
    """
    snap_dir = _resolve_snapshot_dir(char_dir, snapshot_id)
    meta = _read_json_safe(snap_dir / "meta.json", None)
    if not snap_dir.is_dir() or not isinstance(meta, dict):
        raise FileNotFoundError(f"Snapshot not found: {snapshot_id}")

    # Validate meta['files'] strictly (allow explicit [], reject missing/invalid).
    raw_files = meta.get("files")
    if not isinstance(raw_files, list):
        raise ValueError(f"Snapshot manifest missing valid files list: {snapshot_id}")
    recorded_files: list[str] = []
    for entry in raw_files:
        if not isinstance(entry, str) or not _is_allowed_snapshot_relpath(entry):
            raise ValueError(f"Snapshot manifest contains invalid file entry: {entry!r}")
        if entry not in recorded_files:
            recorded_files.append(entry)

    compressed = meta.get("format") == _STORAGE_FORMAT
    staged: dict[str, bytes] = {}
    for relpath in recorded_files:
        stored_name = relpath + _COMPRESSED_SUFFIX if compressed else relpath
        snap_file = (snap_dir / stored_name).resolve()
        try:
            snap_file.relative_to(snap_dir)
        except ValueError as exc:
            raise ValueError(f"Invalid snapshot file path: {relpath}") from exc
        if not snap_file.is_file():
            raise FileNotFoundError(f"Snapshot file missing: {snapshot_id}/{relpath}")
        raw = snap_file.read_bytes()
        if compressed:
            try:
                raw = gzip.decompress(raw)
            except (OSError, EOFError) as exc:
                raise ValueError(f"Snapshot file corrupt: {snapshot_id}/{relpath}") from exc
        # Validate JSON integrity before modifying live memory.
        if relpath.endswith(".json"):
            json.loads(raw.decode("utf-8"))
        staged[relpath] = raw
    return meta, recorded_files, staged


def _swap_live_files(
    char_dir: Path,
    target_bytes: dict[str, bytes],
    orig_bytes: dict[str, bytes],
    all_targets: list[str],
    rollback_errors: list | None = None,
) -> tuple[list[str], list[str]]:
    """Write ``target_bytes`` and delete every other target; roll back on failure.

    Rollback failures are logged and, when ``rollback_errors`` is given,
    appended to it, so the caller can tell a clean rollback from a partial one.
    """
    touched: list[str] = []
    restored: list[str] = []
    removed: list[str] = []
    try:
        for relpath in all_targets:
            target_file = char_dir / relpath
            if relpath in target_bytes:
                target_file.parent.mkdir(parents=True, exist_ok=True)
                touched.append(relpath)
                atomic_write_bytes(str(target_file), target_bytes[relpath])
                restored.append(relpath)
            elif target_file.is_file():
                touched.append(relpath)
                try:
                    os.remove(target_file)
                    removed.append(relpath)
                except FileNotFoundError:
                    # Already absent: matches desired post-restore state.
                    pass
    except Exception:
        # Roll back every modified or deleted file before propagating failure.
        for relpath in reversed(touched):
            target_file = char_dir / relpath
            try:
                if relpath in orig_bytes:
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_bytes(str(target_file), orig_bytes[relpath])
                elif target_file.is_file():
                    os.remove(target_file)
            except OSError as rb_exc:
                logger.warning("[MemorySnapshots] rollback failed for %s: %s", relpath, rb_exc)
                if rollback_errors is not None:
                    rollback_errors.append(rb_exc)
        raise
    return restored, removed


def _advance_view_generation(char_dir: Path) -> None:
    # Readers that span the swap then retry instead of mixing two states.
    from memory.long_term_sync import advance_view_generation
    advance_view_generation(char_dir)


def _drop_thread_locked_caches(character_name: str, fact_store, directives_manager) -> None:
    if fact_store is not None:
        facts_cache = getattr(fact_store, "_facts", None)
        if isinstance(facts_cache, dict):
            facts_cache.pop(character_name, None)
    if directives_manager is not None:
        dm_cache = getattr(directives_manager, "_cache", None)
        if isinstance(dm_cache, dict):
            dm_cache.pop(character_name, None)
        dm_stat = getattr(directives_manager, "_cache_stat", None)
        if isinstance(dm_stat, dict):
            dm_stat.pop(character_name, None)


def _enter_thread_locks(stack: ExitStack, character_name: str, fact_store, directives_manager) -> None:
    if fact_store is not None and callable(getattr(fact_store, "_get_lock", None)):
        stack.enter_context(fact_store._get_lock(character_name))
    if directives_manager is not None and callable(getattr(directives_manager, "_get_lock", None)):
        stack.enter_context(directives_manager._get_lock(character_name))


def _restore_snapshot_files_sync(
    character_name: str,
    snapshot_id: str,
    *,
    memory_dir: Path,
    max_keep: int = MEMORY_BROWSER_SNAPSHOT_MAX_KEEP,
    auto_max_keep: int = MEMORY_BROWSER_AUTO_SNAPSHOT_MAX_KEEP,
    fact_store=None,
    directives_manager=None,
    event_log=None,
    progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    char_dir = memory_dir / character_name
    # Read and validate ALL recorded snapshot files BEFORE creating pre_restore.
    meta, recorded_files, staged = _load_snapshot_source(char_dir, snapshot_id)

    payload: dict[str, Any] = {}
    plan: dict[str, Any] = {}
    failure: Exception | None = None

    with ExitStack() as lock_stack:
        def _load(_name: str) -> dict[str, Any]:
            # Runs first inside record_and_save, under the event-log lock and
            # before the append: the payload is completed here, from the
            # live files as they are right now. The thread-level store locks
            # stay held through the swap so concurrent FactStore /
            # UserDirectives writers cannot flush stale state over it.
            _enter_thread_locks(lock_stack, character_name, fact_store, directives_manager)
            pre_backup = create_snapshot_sync(
                character_name,
                label=f"Auto backup before restoring {meta.get('label') or snapshot_id}",
                reason="pre_restore",
                memory_dir=memory_dir,
                max_keep=max_keep,
                auto_max_keep=auto_max_keep,
                protect=(snapshot_id,),
            )
            current_managed = _collect_managed_relpaths(char_dir)
            orig_bytes = {rel: (char_dir / rel).read_bytes() for rel in current_managed}
            donors = _vector_donors(orig_bytes.values())
            target_bytes = {rel: _materialize(staged[rel], donors) for rel in recorded_files}
            all_targets = list(dict.fromkeys([*recorded_files, *current_managed]))
            target_fps: dict[str, str | None] = {rel: None for rel in all_targets}
            target_fps.update({rel: content_fingerprint(staged[rel]) for rel in recorded_files})
            pre_fps: dict[str, str | None] = {rel: None for rel in all_targets}
            pre_fps.update({rel: content_fingerprint(raw) for rel, raw in orig_bytes.items()})
            payload.update({
                "snapshot_id": snapshot_id,
                "target_files": target_fps,
                "pre_restore_snapshot_id": pre_backup["snapshot_id"],
                "pre_restore_files": pre_fps,
            })
            plan.update({
                "pre_backup": pre_backup,
                "orig_bytes": orig_bytes,
                "target_bytes": target_bytes,
                "all_targets": all_targets,
            })
            return plan

        def _save(_name: str, view: dict[str, Any]) -> None:
            # Runs after the event was appended.
            view["save_started"] = True
            _advance_view_generation(char_dir)
            restored, removed = _swap_live_files(
                char_dir, view["target_bytes"], view["orig_bytes"], view["all_targets"],
                rollback_errors=view.setdefault("rollback_errors", []),
            )
            view["swapped"] = True
            if progress is not None:
                # The files are replaced from here on, even if the sentinel
                # write after this callback fails.
                progress["swapped"] = True
            view["restored_files"] = restored
            view["removed_files"] = removed
            _drop_thread_locked_caches(character_name, fact_store, directives_manager)

        if event_log is None:
            _save(character_name, _load(character_name))
        else:
            sentinel_before = event_log.read_sentinel(character_name)
            try:
                event_log.record_and_save(
                    character_name,
                    EVT_MEMORY_SNAPSHOT_RESTORED,
                    payload,
                    sync_load_view=_load,
                    sync_mutate_view=lambda _view: None,
                    sync_save_view=_save,
                )
            except Exception as exc:
                failure = exc

    if failure is not None:
        if plan.get("swapped"):
            # Only the sentinel write after the swap failed. The files are
            # restored and the event left behind is a no-op on replay (every
            # file already at the target), so this is a successful restore.
            logger.warning(
                "[MemorySnapshots] %s: restored %s, but recording its completion failed: %s",
                character_name, snapshot_id, failure,
            )
        else:
            # Outside the store locks: voiding takes the event-log lock, which
            # is always taken before them.
            if plan.get("save_started") and not plan.get("rollback_errors"):
                # The user is told the restore failed and the files are back
                # as they were: the event must not finish it later.
                _void_restore_event(event_log, character_name, sentinel_before, payload)
            raise failure

    return {
        "restored_snapshot_id": snapshot_id,
        "restored_label": str(meta.get("label") or snapshot_id),
        "pre_restore_snapshot_id": plan["pre_backup"]["snapshot_id"],
        "restored_files": plan.get("restored_files", []),
        "removed_files": plan.get("removed_files", []),
        "restored_at": datetime.now().isoformat(),
    }


def _void_restore_event(event_log, character_name: str, sentinel_before, payload: dict) -> None:
    """Advance the sentinel onto a restore event whose swap was rolled back cleanly.

    Only when the sentinel is still where it was before the append (a
    compare-and-set through apply_and_advance with a no-op step), and only
    for this very event: the last record of the tail, matched by its
    pre-restore snapshot id. Never raises; the original error is what the
    caller reports.
    """
    try:
        tail = event_log.read_since(character_name, sentinel_before)
        record = tail[-1] if tail else None
        if (
            record is None
            or record.get("type") != EVT_MEMORY_SNAPSHOT_RESTORED
            or (record.get("payload") or {}).get("pre_restore_snapshot_id")
            != payload.get("pre_restore_snapshot_id")
        ):
            return
        event_log.apply_and_advance(
            character_name, record.get("event_id"), lambda: False,
            expected_sentinel=sentinel_before,
        )
        logger.info(
            "[MemorySnapshots] %s: failed restore of %s rolled back; its event will not replay",
            character_name, payload.get("snapshot_id"),
        )
    except Exception as exc:
        logger.warning(
            "[MemorySnapshots] %s: could not void the failed restore event: %s", character_name, exc,
        )


def _rebuild_fact_index_sync(character_name: str, char_dir: Path, fact_store) -> None:
    time_indexed = getattr(fact_store, "_time_indexed", None) if fact_store is not None else None
    if time_indexed is None or not callable(getattr(time_indexed, "rebuild_fact_index", None)):
        return
    raw = _read_json_safe(char_dir / "facts.json", [])
    archived = _read_json_safe(char_dir / "facts_archive.json", [])
    time_indexed.rebuild_fact_index(
        character_name,
        [*(raw if isinstance(raw, list) else []), *(archived if isinstance(archived, list) else [])],
    )


_RESTORE_DONE = "done"              # every file at the target version
_RESTORE_INTERRUPTED = "interrupted"  # each file at target or pre, not all target
_RESTORE_CHANGED = "changed"        # some file at neither: changed after the restore


def _restore_payload_parts(payload: dict) -> tuple[str, dict, dict] | None:
    snapshot_id = payload.get("snapshot_id")
    target = payload.get("target_files")
    pre = payload.get("pre_restore_files")
    if (
        not isinstance(snapshot_id, str)
        or not isinstance(target, dict)
        or not isinstance(pre, dict)
        or not all(_is_allowed_snapshot_relpath(rel) for rel in (*target, *pre))
    ):
        return None
    return snapshot_id, target, pre


def _compare_with_restore(char_dir: Path, target: dict, pre: dict):
    """``(state, live bytes, current fingerprints, relpaths)`` of the managed files."""
    live = {rel: (char_dir / rel).read_bytes() for rel in _collect_managed_relpaths(char_dir)}
    relpaths = set(live) | set(target) | set(pre)
    current = {rel: content_fingerprint(live[rel]) if rel in live else None for rel in relpaths}
    if any(current[rel] not in (target.get(rel), pre.get(rel)) for rel in relpaths):
        state = _RESTORE_CHANGED
    elif all(current[rel] == target.get(rel) for rel in relpaths):
        state = _RESTORE_DONE
    else:
        state = _RESTORE_INTERRUPTED
    return state, live, current, relpaths


def _load_matching_snapshot(char_dir: Path, snapshot_id: str, target: dict):
    """``(files, stored bytes)`` when the snapshot can still produce ``target``, else None."""
    try:
        _meta, recorded_files, staged = _load_snapshot_source(char_dir, snapshot_id)
    except (FileNotFoundError, ValueError, OSError):
        return None
    expected = {rel for rel, fp in target.items() if fp is not None}
    if set(recorded_files) != expected or any(
        content_fingerprint(staged[rel]) != target[rel] for rel in recorded_files
    ):
        return None
    return recorded_files, staged


def make_snapshot_restore_in_effect(*, memory_dir_fn: Callable[[], Any] | None = None):
    """Build the Reconciler tail-cut check for ``memory.snapshot_restored``.

    True when the restore is in effect or replay will finish it: every file
    at the target, some file changed after it, or an interrupted swap whose
    snapshot can still produce the target. Only then are the unapplied
    events in front of it obsolete. A restore that never got going and
    cannot be finished (its snapshot gone) leaves the files as they were,
    so those events must still be replayed. Read-only.
    """

    def _in_effect(name: str, payload: dict) -> bool:
        parts = _restore_payload_parts(payload)
        if parts is None:
            return False
        snapshot_id, target, pre = parts
        memory_dir = Path(memory_dir_fn() if memory_dir_fn is not None else get_config_manager().memory_dir)
        char_dir = memory_dir / name
        if not char_dir.is_dir():
            return False
        try:
            state, _live, _current, _relpaths = _compare_with_restore(char_dir, target, pre)
        except OSError:
            return False
        if state != _RESTORE_INTERRUPTED:
            return True
        return _load_matching_snapshot(char_dir, snapshot_id, target) is not None

    return _in_effect


def make_snapshot_restore_handler(
    *,
    persona_manager=None,
    fact_store=None,
    reflection_engine=None,
    directives_manager_fn: Callable[[], Any] | None = None,
    memory_dir_fn: Callable[[], Any] | None = None,
):
    """Build the ``memory.snapshot_restored`` replay handler.

    Each managed file is compared with the restore's target version and with
    the version it replaced (fingerprints in the payload, vectors ignored):

      - every file at one of the two, not all at the target: the swap was
        interrupted, so the remaining files are copied from the snapshot;
      - all at the target, or any file at neither (changed after the
        restore): nothing is done;
      - snapshot gone or unreadable: logged and skipped.

    Never raises for those outcomes, so one restore cannot pause replay.
    A write error while finishing the copy does raise, after rolling back,
    and the event is retried on the next start.
    """

    def _directives():
        if directives_manager_fn is not None:
            return directives_manager_fn()
        try:
            from memory.user_directives import get_user_directives_manager
            return get_user_directives_manager()
        except Exception:
            return None

    def _apply(name: str, payload: dict) -> bool:
        parts = _restore_payload_parts(payload)
        if parts is None:
            logger.warning("[MemorySnapshots] %s: malformed snapshot-restore event skipped", name)
            return False
        snapshot_id, target, pre = parts
        memory_dir = Path(memory_dir_fn() if memory_dir_fn is not None else get_config_manager().memory_dir)
        char_dir = memory_dir / name
        if not char_dir.is_dir():
            return False
        dm = _directives()

        with ExitStack() as lock_stack:
            _enter_thread_locks(lock_stack, name, fact_store, dm)
            state, live, current, relpaths = _compare_with_restore(char_dir, target, pre)
            if state == _RESTORE_CHANGED:
                logger.info(
                    "[MemorySnapshots] %s: memory changed after restore %s; replay leaves it",
                    name, snapshot_id,
                )
                return False
            if state == _RESTORE_DONE:
                return False

            source = _load_matching_snapshot(char_dir, snapshot_id, target)
            if source is None:
                logger.warning(
                    "[MemorySnapshots] %s: interrupted restore of %s cannot be finished "
                    "(snapshot gone, unreadable or changed); skipped",
                    name, snapshot_id,
                )
                return False
            recorded_files, staged = source

            donors = _vector_donors(live.values())
            pending = {
                rel: _materialize(staged[rel], donors)
                for rel in recorded_files
                if current.get(rel) != target[rel]
            }
            leftovers = [rel for rel in relpaths if target.get(rel) is None and rel in live]
            _advance_view_generation(char_dir)
            _swap_live_files(char_dir, pending, live, [*pending, *leftovers])
            _drop_thread_locked_caches(name, fact_store, dm)
            if persona_manager is not None:
                cache = getattr(persona_manager, "_personas", None)
                if isinstance(cache, dict):
                    cache.pop(name, None)
            if reflection_engine is not None:
                cache = getattr(reflection_engine, "_reflections", None)
                if isinstance(cache, dict):
                    cache.pop(name, None)
            try:
                _rebuild_fact_index_sync(name, char_dir, fact_store)
            except Exception as exc:
                logger.warning("[MemorySnapshots] %s: fact index rebuild after replay failed: %s", name, exc)
        _clear_star_cache()
        logger.info("[MemorySnapshots] %s: finished interrupted restore of %s", name, snapshot_id)
        return True

    return _apply


def register_snapshot_restore_replay(
    reconciler,
    *,
    persona_manager=None,
    fact_store=None,
    reflection_engine=None,
    directives_manager_fn: Callable[[], Any] | None = None,
    memory_dir_fn: Callable[[], Any] | None = None,
) -> None:
    """Register the snapshot-restore handler and tail cut.

    An unregistered type would pause replay. The tail cut lets replay skip
    unapplied events written before a restore that is in effect.
    """
    reconciler.register(
        EVT_MEMORY_SNAPSHOT_RESTORED,
        make_snapshot_restore_handler(
            persona_manager=persona_manager,
            fact_store=fact_store,
            reflection_engine=reflection_engine,
            directives_manager_fn=directives_manager_fn,
            memory_dir_fn=memory_dir_fn,
        ),
    )
    reconciler.register_tail_cut(
        EVT_MEMORY_SNAPSHOT_RESTORED,
        make_snapshot_restore_in_effect(memory_dir_fn=memory_dir_fn),
    )


def _clear_star_cache() -> None:
    try:
        from .star_map import _STAR_CACHE, _STAR_CACHE_LOCK
        with _STAR_CACHE_LOCK:
            _STAR_CACHE.clear()
    except Exception as cache_exc:
        logger.debug("[MemorySnapshots] failed to clear star-map cache: %s", cache_exc)


async def alist_snapshots(
    character_name: str,
    *,
    memory_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Non-blocking async wrapper around ``list_snapshots``."""
    return await asyncio.to_thread(list_snapshots, character_name, memory_dir=memory_dir)


def _create_snapshot_locked_sync(
    character_name: str,
    *,
    label: str = "",
    reason: str = "manual",
    memory_dir: Path,
    max_keep: int = MEMORY_BROWSER_SNAPSHOT_MAX_KEEP,
    auto_max_keep: int = MEMORY_BROWSER_AUTO_SNAPSHOT_MAX_KEEP,
    fact_store=None,
    directives_manager=None,
) -> dict[str, Any]:
    with ExitStack() as lock_stack:
        _enter_thread_locks(lock_stack, character_name, fact_store, directives_manager)
        return create_snapshot_sync(
            character_name,
            label=label,
            reason=reason,
            memory_dir=memory_dir,
            max_keep=max_keep,
            auto_max_keep=auto_max_keep,
        )


async def acreate_snapshot(
    character_name: str,
    *,
    label: str = "",
    reason: str = "manual",
    config_manager=None,
    memory_dir: Path | None = None,
    persona_manager=None,
    fact_store=None,
    reflection_engine=None,
    directives_manager=None,
    max_keep: int = MEMORY_BROWSER_SNAPSHOT_MAX_KEEP,
    auto_max_keep: int = MEMORY_BROWSER_AUTO_SNAPSHOT_MAX_KEEP,
) -> dict[str, Any]:
    """Create a safety snapshot under the same manager lock hierarchy as restore."""
    cm = config_manager or get_config_manager()
    assert_cloudsave_writable(
        cm,
        operation="save",
        target=f"memory/{character_name}/{_SNAPSHOTS_DIRNAME}",
    )
    resolved_dir = Path(memory_dir) if memory_dir is not None else Path(cm.memory_dir)

    dm = directives_manager
    if dm is None:
        try:
            from memory.user_directives import get_user_directives_manager
            dm = get_user_directives_manager()
        except Exception:
            dm = None

    async with AsyncExitStack() as stack:
        # Lock order MUST match long_term_sync._views_locked
        # (fact_store._get_persist_alock -> reflection_engine._get_alock -> persona_manager._get_alock)
        # as well as promotion (reflection_engine -> persona_manager) to prevent lock-order deadlocks.
        if fact_store is not None and hasattr(fact_store, "_get_persist_alock"):
            await stack.enter_async_context(fact_store._get_persist_alock(character_name))
        if reflection_engine is not None and hasattr(reflection_engine, "_get_alock"):
            await stack.enter_async_context(reflection_engine._get_alock(character_name))
        if persona_manager is not None and hasattr(persona_manager, "_get_alock"):
            await stack.enter_async_context(persona_manager._get_alock(character_name))

        assert_cloudsave_writable(
            cm,
            operation="save",
            target=f"memory/{character_name}/{_SNAPSHOTS_DIRNAME}",
        )
        worker = asyncio.create_task(
            asyncio.to_thread(
                _create_snapshot_locked_sync,
                character_name,
                label=label,
                reason=reason,
                memory_dir=resolved_dir,
                max_keep=max_keep,
                auto_max_keep=auto_max_keep,
                fact_store=fact_store,
                directives_manager=dm,
            )
        )
        cancelled = False
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            if not worker.cancelled():
                worker.exception()
            raise asyncio.CancelledError
        return worker.result()


async def arestore_snapshot(
    character_name: str,
    snapshot_id: str,
    *,
    config_manager=None,
    memory_dir: Path | None = None,
    persona_manager=None,
    fact_store=None,
    reflection_engine=None,
    directives_manager=None,
    event_log=None,
    views_lock=None,
    max_keep: int = MEMORY_BROWSER_SNAPSHOT_MAX_KEEP,
    auto_max_keep: int = MEMORY_BROWSER_AUTO_SNAPSHOT_MAX_KEEP,
) -> dict[str, Any]:
    """Atomically restore a snapshot under manager locks and invalidate caches.

    ``views_lock(name)`` is the memory owner's async context manager for the
    whole set of views (admission, settle, reload and the three manager
    locks); without it the three manager locks are taken here in the same
    order. With ``event_log`` the swap is journaled as one
    ``memory.snapshot_restored`` event.
    """
    cm = config_manager or get_config_manager()
    assert_cloudsave_writable(
        cm,
        operation="save",
        target=f"memory/{character_name}/persona.json",
    )
    resolved_dir = Path(memory_dir) if memory_dir is not None else Path(cm.memory_dir)

    dm = directives_manager
    if dm is None:
        try:
            from memory.user_directives import get_user_directives_manager
            dm = get_user_directives_manager()
        except Exception:
            dm = None

    async with AsyncExitStack() as stack:
        if views_lock is not None:
            await stack.enter_async_context(views_lock(character_name))
        else:
            # Lock order MUST match long_term_sync._views_locked
            # (fact_store._get_persist_alock -> reflection_engine._get_alock -> persona_manager._get_alock)
            # as well as promotion (reflection_engine -> persona_manager) to prevent lock-order deadlocks.
            if fact_store is not None and hasattr(fact_store, "_get_persist_alock"):
                await stack.enter_async_context(fact_store._get_persist_alock(character_name))
            if reflection_engine is not None and hasattr(reflection_engine, "_get_alock"):
                await stack.enter_async_context(reflection_engine._get_alock(character_name))
            if persona_manager is not None and hasattr(persona_manager, "_get_alock"):
                await stack.enter_async_context(persona_manager._get_alock(character_name))

        # Re-check cloudsave writability AFTER acquiring locks so maintenance mode
        # entered while waiting for locks immediately aborts before modifying files.
        assert_cloudsave_writable(
            cm,
            operation="save",
            target=f"memory/{character_name}/persona.json",
        )

        # Shield the worker thread across repeated cancellations so AsyncExitStack
        # cannot release manager locks while the thread is still swapping files.
        progress: dict[str, Any] = {}
        worker = asyncio.create_task(
            asyncio.to_thread(
                _restore_snapshot_files_sync,
                character_name,
                snapshot_id,
                memory_dir=resolved_dir,
                max_keep=max_keep,
                auto_max_keep=auto_max_keep,
                fact_store=fact_store,
                directives_manager=dm,
                event_log=event_log,
                progress=progress,
            )
        )
        cancelled = False
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                # Read below through worker.exception(); the cleanup that
                # follows must still run when the swap landed.
                pass

        worker_exc = None if worker.cancelled() else worker.exception()
        if progress.get("swapped") or (worker_exc is None and not worker.cancelled()):
            # Invalidate async-locked manager caches while still holding locks
            # whenever the files were swapped: even if cancelled mid-flight, and
            # even if a step after the swap (the sentinel write) raised. A cached
            # pre-restore persona would otherwise be saved back over the restore.
            if persona_manager is not None:
                personas_cache = getattr(persona_manager, "_personas", None)
                if isinstance(personas_cache, dict):
                    personas_cache.pop(character_name, None)

            if reflection_engine is not None:
                refl_cache = getattr(reflection_engine, "_reflections", None)
                if isinstance(refl_cache, dict):
                    refl_cache.pop(character_name, None)

            _clear_star_cache()

            # Rebuild FTS near-duplicate index from the restored facts.json while still
            # holding fact_store._get_persist_alock so restored/un-archived facts never
            # miss near-duplicate checks or retain pre-restore tokens.
            time_indexed = getattr(fact_store, "_time_indexed", None) if fact_store is not None else None
            if time_indexed is not None and callable(getattr(time_indexed, "arebuild_fact_index", None)):
                async def _rebuild_fts() -> None:
                    raw = await asyncio.to_thread(
                        _read_json_safe, resolved_dir / character_name / "facts.json", [],
                    )
                    restored_facts = raw if isinstance(raw, list) else []
                    archived_raw = await asyncio.to_thread(
                        _read_json_safe, resolved_dir / character_name / "facts_archive.json", [],
                    )
                    restored_archive = archived_raw if isinstance(archived_raw, list) else []
                    await time_indexed.arebuild_fact_index(
                        character_name, [*restored_facts, *restored_archive],
                    )

                rebuild_task = asyncio.create_task(_rebuild_fts())
                while not rebuild_task.done():
                    try:
                        await asyncio.shield(rebuild_task)
                    except asyncio.CancelledError:
                        cancelled = True
                    except Exception:
                        # Logged below; a derived-index failure must not fail the restore.
                        pass
                if not rebuild_task.cancelled():
                    rebuild_exc = rebuild_task.exception()
                    if rebuild_exc is not None:
                        logger.warning(
                            "[MemorySnapshots] failed to rebuild FTS index after restore for %s: %s",
                            character_name,
                            rebuild_exc,
                        )

        if cancelled:
            raise asyncio.CancelledError
        if worker_exc is not None:
            raise worker_exc
        result = worker.result()

    return result
