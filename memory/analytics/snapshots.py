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
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
from contextlib import AsyncExitStack, ExitStack
from datetime import datetime
from pathlib import Path
from typing import Any

from config import MEMORY_BROWSER_SNAPSHOT_MAX_KEEP
from memory.archive_shards import PERSONA_ARCHIVE_DIRNAME, REFLECTION_ARCHIVE_DIRNAME
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
_SNAPSHOT_ARCHIVE_DIRS: tuple[str, ...] = (
    PERSONA_ARCHIVE_DIRNAME,
    REFLECTION_ARCHIVE_DIRNAME,
)
_SAFE_SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_SAFE_ARCHIVE_FILENAME_RE = re.compile(r"^(?:\.migrated_from_flat|[A-Za-z0-9_.-]+\.json)$")


def _is_allowed_snapshot_relpath(relpath: str) -> bool:
    """Validate a posix relative path stored in ``meta.json['files']``."""
    if not isinstance(relpath, str) or not relpath or "\\" in relpath or relpath.startswith("/"):
        return False
    if relpath in _SNAPSHOT_FILES:
        return True
    parts = relpath.split("/")
    if len(parts) == 2 and parts[0] in _SNAPSHOT_ARCHIVE_DIRS:
        return bool(_SAFE_ARCHIVE_FILENAME_RE.match(parts[1])) and ".." not in parts[1]
    return False


def _collect_managed_relpaths(char_dir: Path) -> list[str]:
    """Return all currently existing managed flat files and archive shard files."""
    relpaths: list[str] = []
    for fname in _SNAPSHOT_FILES:
        if (char_dir / fname).is_file():
            relpaths.append(fname)
    for dirname in _SNAPSHOT_ARCHIVE_DIRS:
        adir = char_dir / dirname
        if not adir.is_dir():
            continue
        for child in sorted(adir.iterdir(), key=lambda p: p.name):
            if child.is_file() and _SAFE_ARCHIVE_FILENAME_RE.match(child.name):
                relpaths.append(f"{dirname}/{child.name}")
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


def _prune_old_snapshots_sync(root: Path, max_keep: int) -> None:
    if not root.is_dir() or max_keep <= 0:
        return
    entries: list[tuple[str, Path]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        meta = _read_json_safe(child / "meta.json", {})
        created_at = str(meta.get("created_at") or "") if isinstance(meta, dict) else ""
        entries.append((created_at or child.name, child))
    entries.sort(key=lambda pair: pair[0], reverse=True)
    for _, stale_dir in entries[max_keep:]:
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


def create_snapshot_sync(
    character_name: str,
    *,
    label: str = "",
    reason: str = "manual",
    memory_dir: Path | None = None,
    max_keep: int = MEMORY_BROWSER_SNAPSHOT_MAX_KEEP,
) -> dict[str, Any]:
    """Synchronously create a safety snapshot of core long-term memory JSON files."""
    base = Path(memory_dir) if memory_dir is not None else Path(get_config_manager().memory_dir)
    char_dir = base / character_name
    char_dir.mkdir(parents=True, exist_ok=True)
    root = _snapshots_root(char_dir)
    root.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    snap_id = f"snap_{now.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"
    snap_dir = root / snap_id
    snap_dir.mkdir(parents=True, exist_ok=True)

    copied_files: list[str] = []
    for relpath in _collect_managed_relpaths(char_dir):
        src = char_dir / relpath
        if src.is_file():
            data = src.read_bytes()
            dst = snap_dir / relpath
            dst.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(str(dst), data)
            copied_files.append(relpath)

    clean_label = str(label or "").strip()[:80]
    if not clean_label:
        clean_label = now.strftime("%Y-%m-%d %H:%M")

    meta = {
        "snapshot_id": snap_id,
        "character_name": character_name,
        "label": clean_label,
        "reason": str(reason or "manual")[:32],
        "created_at": now.isoformat(),
        "files": copied_files,
        "stats": _count_snapshot_stats(char_dir),
    }
    atomic_write_json(str(snap_dir / "meta.json"), meta, indent=2, ensure_ascii=False)
    _prune_old_snapshots_sync(root, max_keep=max_keep)
    return meta


def _restore_snapshot_files_sync(
    character_name: str,
    snapshot_id: str,
    *,
    memory_dir: Path,
    max_keep: int = MEMORY_BROWSER_SNAPSHOT_MAX_KEEP,
    fact_store=None,
    directives_manager=None,
) -> dict[str, Any]:
    if not _SAFE_SNAPSHOT_ID_RE.match(str(snapshot_id or "")):
        raise ValueError("Invalid snapshot_id")

    char_dir = memory_dir / character_name
    root = _snapshots_root(char_dir)
    snap_dir = (root / snapshot_id).resolve()
    try:
        snap_dir.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Invalid snapshot path") from exc

    meta = _read_json_safe(snap_dir / "meta.json", None)
    if not snap_dir.is_dir() or not isinstance(meta, dict):
        raise FileNotFoundError(f"Snapshot not found: {snapshot_id}")

    # 1. Validate meta['files'] manifest strictly (allow explicit [], reject missing/invalid)
    #    and read/validate ALL recorded snapshot files BEFORE creating pre_restore
    #    (creating pre_restore prunes old snapshots when the vault is at capacity,
    #    which could otherwise delete the very oldest snapshot we are restoring).
    raw_files = meta.get("files")
    if not isinstance(raw_files, list):
        raise ValueError(f"Snapshot manifest missing valid files list: {snapshot_id}")
    recorded_files: list[str] = []
    for entry in raw_files:
        if not isinstance(entry, str) or not _is_allowed_snapshot_relpath(entry):
            raise ValueError(f"Snapshot manifest contains invalid file entry: {entry!r}")
        if entry not in recorded_files:
            recorded_files.append(entry)

    recorded_set = set(recorded_files)
    staged_bytes: dict[str, bytes] = {}
    for relpath in recorded_files:
        snap_file = (snap_dir / relpath).resolve()
        try:
            snap_file.relative_to(snap_dir)
        except ValueError as exc:
            raise ValueError(f"Invalid snapshot file path: {relpath}") from exc
        if not snap_file.is_file():
            raise FileNotFoundError(f"Snapshot file missing: {snapshot_id}/{relpath}")
        raw = snap_file.read_bytes()
        # Validate JSON integrity for JSON files before modifying live memory.
        if relpath.endswith(".json"):
            json.loads(raw.decode("utf-8"))
        staged_bytes[relpath] = raw

    # 2. Hold thread-level store locks across pre_restore backup, live file swap,
    #    and cache eviction so concurrent FactStore / UserDirectives writers
    #    cannot flush pre-restore cached state over the restored files.
    with ExitStack() as lock_stack:
        if fact_store is not None and callable(getattr(fact_store, "_get_lock", None)):
            lock_stack.enter_context(fact_store._get_lock(character_name))
        if directives_manager is not None and callable(getattr(directives_manager, "_get_lock", None)):
            lock_stack.enter_context(directives_manager._get_lock(character_name))

        # Capture a safety backup of current state (including archives) before overwriting.
        pre_backup = create_snapshot_sync(
            character_name,
            label=f"Auto backup before restoring {meta.get('label') or snapshot_id}",
            reason="pre_restore",
            memory_dir=memory_dir,
            max_keep=max_keep,
        )

        # Stage current live managed files in memory so mid-restore write/delete errors
        # can be rolled back completely under the same locks.
        current_managed = _collect_managed_relpaths(char_dir)
        orig_bytes: dict[str, bytes] = {}
        for relpath in current_managed:
            orig_bytes[relpath] = (char_dir / relpath).read_bytes()

        all_targets = list(dict.fromkeys([*recorded_files, *current_managed]))
        touched_targets: list[str] = []
        restored_files: list[str] = []
        removed_files: list[str] = []
        try:
            for relpath in all_targets:
                target_file = char_dir / relpath
                if relpath in recorded_set:
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    touched_targets.append(relpath)
                    atomic_write_bytes(str(target_file), staged_bytes[relpath])
                    restored_files.append(relpath)
                elif target_file.is_file():
                    touched_targets.append(relpath)
                    try:
                        os.remove(target_file)
                        removed_files.append(relpath)
                    except FileNotFoundError:
                        # Already absent — matches desired post-restore state.
                        pass
        except Exception:
            # Roll back every modified or deleted file before propagating failure.
            for relpath in reversed(touched_targets):
                target_file = char_dir / relpath
                try:
                    if relpath in orig_bytes:
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        atomic_write_bytes(str(target_file), orig_bytes[relpath])
                    elif target_file.is_file():
                        os.remove(target_file)
                except OSError as rb_exc:
                    logger.warning("[MemorySnapshots] rollback failed for %s: %s", relpath, rb_exc)
            raise

        if fact_store is not None:
            facts_cache = getattr(fact_store, "_facts", None)
            if isinstance(facts_cache, dict):
                facts_cache.pop(character_name, None)

        if directives_manager is not None:
            dm_cache = getattr(directives_manager, "_cache", None)
            if isinstance(dm_cache, dict):
                dm_cache.pop(character_name, None)

    return {
        "restored_snapshot_id": snapshot_id,
        "restored_label": str(meta.get("label") or snapshot_id),
        "pre_restore_snapshot_id": pre_backup["snapshot_id"],
        "restored_files": restored_files,
        "removed_files": removed_files,
        "restored_at": datetime.now().isoformat(),
    }


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
    fact_store=None,
    directives_manager=None,
) -> dict[str, Any]:
    with ExitStack() as lock_stack:
        if fact_store is not None and callable(getattr(fact_store, "_get_lock", None)):
            lock_stack.enter_context(fact_store._get_lock(character_name))
        if directives_manager is not None and callable(getattr(directives_manager, "_get_lock", None)):
            lock_stack.enter_context(directives_manager._get_lock(character_name))
        return create_snapshot_sync(
            character_name,
            label=label,
            reason=reason,
            memory_dir=memory_dir,
            max_keep=max_keep,
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
        # Same lock hierarchy as arestore_snapshot (reflection -> persona -> fact persist)
        # so manual snapshot capture is mutually exclusive with restore and archive writes.
        if reflection_engine is not None and hasattr(reflection_engine, "_get_alock"):
            await stack.enter_async_context(reflection_engine._get_alock(character_name))
        if persona_manager is not None and hasattr(persona_manager, "_get_alock"):
            await stack.enter_async_context(persona_manager._get_alock(character_name))
        if fact_store is not None and hasattr(fact_store, "_get_persist_alock"):
            await stack.enter_async_context(fact_store._get_persist_alock(character_name))

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
    max_keep: int = MEMORY_BROWSER_SNAPSHOT_MAX_KEEP,
) -> dict[str, Any]:
    """Atomically restore a snapshot under manager locks and invalidate caches."""
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
        # Lock order MUST match promotion (reflection_engine -> persona_manager)
        # to prevent lock-order inversion deadlocks.
        if reflection_engine is not None and hasattr(reflection_engine, "_get_alock"):
            await stack.enter_async_context(reflection_engine._get_alock(character_name))
        if persona_manager is not None and hasattr(persona_manager, "_get_alock"):
            await stack.enter_async_context(persona_manager._get_alock(character_name))
        if fact_store is not None and hasattr(fact_store, "_get_persist_alock"):
            await stack.enter_async_context(fact_store._get_persist_alock(character_name))

        # Re-check cloudsave writability AFTER acquiring locks so maintenance mode
        # entered while waiting for locks immediately aborts before modifying files.
        assert_cloudsave_writable(
            cm,
            operation="save",
            target=f"memory/{character_name}/persona.json",
        )

        # Shield the worker thread across repeated cancellations so AsyncExitStack
        # cannot release manager locks while the thread is still swapping files.
        worker = asyncio.create_task(
            asyncio.to_thread(
                _restore_snapshot_files_sync,
                character_name,
                snapshot_id,
                memory_dir=resolved_dir,
                max_keep=max_keep,
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

        worker_exc = None if worker.cancelled() else worker.exception()
        if worker_exc is None and not worker.cancelled():
            # Always invalidate async-locked manager caches while still holding locks
            # whenever the worker committed disk changes, even if cancelled mid-flight.
            if persona_manager is not None:
                personas_cache = getattr(persona_manager, "_personas", None)
                if isinstance(personas_cache, dict):
                    personas_cache.pop(character_name, None)

            if reflection_engine is not None:
                refl_cache = getattr(reflection_engine, "_reflections", None)
                if isinstance(refl_cache, dict):
                    refl_cache.pop(character_name, None)

            try:
                from .star_map import _STAR_CACHE, _STAR_CACHE_LOCK
                with _STAR_CACHE_LOCK:
                    _STAR_CACHE.clear()
            except Exception as cache_exc:
                logger.debug("[MemorySnapshots] failed to clear star-map cache: %s", cache_exc)

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
                    await time_indexed.arebuild_fact_index(character_name, restored_facts)

                rebuild_task = asyncio.create_task(_rebuild_fts())
                while not rebuild_task.done():
                    try:
                        await asyncio.shield(rebuild_task)
                    except asyncio.CancelledError:
                        cancelled = True
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
