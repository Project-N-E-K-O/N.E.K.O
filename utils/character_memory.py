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

from __future__ import annotations

import asyncio
import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from utils.recent_file import (
    acquire_recent_file_locks,
    activate_recent_paths,
    fence_recent_deletions_and_clear_redirects,
    get_recent_pending_unlocked,
    read_recent_text_unlocked,
    recent_file_lock,
    recent_file_locks,
    redirect_recent_paths,
    release_recent_file_locks,
    restore_recent_deletions,
    restore_recent_redirects,
    restore_recent_registry_state,
    set_recent_pending_unlocked,
    snapshot_recent_redirects,
    snapshot_recent_deletions,
    write_recent_payload_unlocked,
)


LEGACY_CHARACTER_MEMORY_FILE_MAP = {
    "recent_{name}.json": "recent.json",
    "settings_{name}.json": "settings.json",
    "facts_{name}.json": "facts.json",
    "facts_archive_{name}.json": "facts_archive.json",
    "persona_{name}.json": "persona.json",
    "persona_corrections_{name}.json": "persona_corrections.json",
    "reflections_{name}.json": "reflections.json",
    "reflections_archive_{name}.json": "reflections_archive.json",
    "surfaced_{name}.json": "surfaced.json",
    "time_indexed_{name}": "time_indexed.db",
    "time_indexed_{name}.db": "time_indexed.db",
}

LEGACY_CHARACTER_MEMORY_EXTRA_ENTRIES = (
    "semantic_memory_{name}",
)

MESSAGE_NAME_FIELDS = ("speaker", "author", "name", "character")


def iter_character_memory_roots(config_manager) -> list[Path]:
    """Return all runtime root directories holding character memory (deduped, insertion order kept).

    Only currently active runtime paths are returned:
      - ``memory_dir``: the current runtime's ``<app_docs>/memory``.
      - ``project_memory_dir``: the seed/default memory location under the project directory.

    Legacy paths (``Documents\\N.E.K.O\\memory`` and other CFA fallbacks or roots
    written by old versions) are **not** included. That data is handled separately by
    the two paths below, so deletion/cleanup logic never accidentally touches
    non-runtime locations:

      - Startup soft migration: ``ConfigManager.migrate_legacy_documents_memory`` only
        moves directories still present in ``characters.json[猫娘]`` to the runtime.
      - Manual cleanup button: the Workshop page's "clean up legacy memory" scan +
        user-checked deletion.
    """  # noqa: DOCSTRING_CJK
    roots: list[Path] = []
    seen: set[str] = set()

    for raw_path in (
        getattr(config_manager, "memory_dir", None),
        getattr(config_manager, "project_memory_dir", None),
    ):
        if not raw_path:
            continue
        try:
            root = Path(raw_path)
        except Exception:
            continue
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)

    return roots


def get_runtime_character_memory_dir(config_manager, character_name: str) -> Path:
    return Path(config_manager.memory_dir) / character_name


def list_character_memory_paths(config_manager, character_name: str) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()

    entry_names = [character_name]
    entry_names.extend(
        pattern.format(name=character_name)
        for pattern in LEGACY_CHARACTER_MEMORY_FILE_MAP
    )
    entry_names.extend(
        pattern.format(name=character_name)
        for pattern in LEGACY_CHARACTER_MEMORY_EXTRA_ENTRIES
    )

    for base_dir in iter_character_memory_roots(config_manager):
        for entry_name in entry_names:
            entry_path = base_dir / entry_name
            normalized_path = str(entry_path)
            if not entry_path.exists() or normalized_path in seen:
                continue
            seen.add(normalized_path)
            paths.append(entry_path)

    return paths


def character_memory_exists(config_manager, character_name: str) -> bool:
    return bool(list_character_memory_paths(config_manager, character_name))


def _move_path(source_path: Path, target_path: Path) -> bool:
    if not source_path.exists():
        return False

    if source_path.is_dir():
        return _merge_directories(source_path, target_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing memory file while moving "
            f"{source_path} -> {target_path}"
        )

    shutil.move(str(source_path), str(target_path))
    return True


def _merge_directories(source_dir: Path, target_dir: Path) -> bool:
    if not source_dir.exists():
        return False

    if not target_dir.exists():
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_dir), str(target_dir))
        return True

    # Pre-flight: check for conflicts before moving anything
    for child in source_dir.iterdir():
        candidate = target_dir / child.name
        if candidate.exists():
            raise FileExistsError(
                f"Refusing to overwrite existing path while merging directories "
                f"{source_dir} -> {target_dir}: conflict at {child.name}"
            )

    changed = False
    for child in sorted(source_dir.iterdir(), key=lambda item: item.name):
        changed = _move_path(child, target_dir / child.name) or changed

    try:
        source_dir.rmdir()
    except OSError:
        pass

    return changed


def _rewrite_recent_message_character_name(item: dict[str, Any], old_name: str, new_name: str) -> bool:
    changed = False

    for field in MESSAGE_NAME_FIELDS:
        value = item.get(field)
        if isinstance(value, str) and value == old_name:
            item[field] = new_name
            changed = True

    nested_data = item.get("data")
    if isinstance(nested_data, dict):
        for field in MESSAGE_NAME_FIELDS:
            value = nested_data.get(field)
            if isinstance(value, str) and value == old_name:
                nested_data[field] = new_name
                changed = True

        content, content_changed = _rewrite_recent_content_character_name(
            nested_data.get("content"), old_name, new_name,
        )
        if content_changed:
            nested_data["content"] = content
            changed = True

    return changed


def _rewrite_recent_content_character_name(content: Any, old_name: str, new_name: str) -> tuple[Any, bool]:
    if not isinstance(content, str):
        return content, False
    changed = False
    for pattern in (
        f"{old_name}说：",
        f"{old_name}说:",
        f"{old_name}:",
        f"{old_name}->",
        f"[{old_name}]",
        f"{old_name} | ",
    ):
        if pattern in content:
            content = content.replace(pattern, pattern.replace(old_name, new_name))
            changed = True
    return content, changed


def _rewrite_pending_message_character_name(message: Any, old_name: str, new_name: str) -> Any:
    rewritten = deepcopy(message)
    if isinstance(rewritten, dict):
        _rewrite_recent_message_character_name(rewritten, old_name, new_name)
        return rewritten
    for field in MESSAGE_NAME_FIELDS:
        if getattr(rewritten, field, None) == old_name:
            setattr(rewritten, field, new_name)
    content, changed = _rewrite_recent_content_character_name(
        getattr(rewritten, "content", None), old_name, new_name,
    )
    if changed:
        setattr(rewritten, "content", content)
    return rewritten


def _rewrite_recent_file_character_name_unlocked(
    recent_path: Path, old_name: str, new_name: str,
) -> bool:
    if old_name == new_name or not recent_path.is_file():
        return False
    try:
        payload = json.loads(read_recent_text_unlocked(recent_path))
    except Exception:
        return False
    if not isinstance(payload, list):
        return False
    changed = False
    for item in payload:
        if isinstance(item, dict):
            changed = _rewrite_recent_message_character_name(item, old_name, new_name) or changed
    if changed:
        write_recent_payload_unlocked(recent_path, payload)
    return changed


def rewrite_recent_file_character_name(recent_path: Path, old_name: str, new_name: str) -> bool:
    """Rewrite the old character name inside a recent file. Blocking — worker thread only.

    Read and write live in one critical section so a concurrent memory_server
    writer cannot land between them and lose its own append.
    """
    with recent_file_lock(recent_path):
        return _rewrite_recent_file_character_name_unlocked(recent_path, old_name, new_name)


def list_character_recent_paths(config_manager, character_name: str) -> list[Path]:
    return list(dict.fromkeys(
        candidate
        for base_dir in iter_character_memory_roots(config_manager)
        for candidate in (
            base_dir / character_name / "recent.json",
            base_dir / f"recent_{character_name}.json",
        )
    ))


def begin_character_recent_transaction(
    config_manager, *character_names: str,
) -> dict[str, Any]:
    """Acquire all recent-file locks needed by a character lifecycle transaction."""
    recent_paths = list(dict.fromkeys(
        path
        for character_name in character_names
        for path in list_character_recent_paths(config_manager, character_name)
    ))
    return {
        "recent_paths": recent_paths,
        "held_locks": acquire_recent_file_locks(recent_paths),
    }


def release_character_recent_transaction(transaction: dict[str, Any] | None) -> None:
    """Release a lifecycle transaction's recent locks exactly once."""
    transaction = transaction or {}
    held_locks = transaction.get("held_locks") or []
    if held_locks:
        transaction["held_locks"] = []
        release_recent_file_locks(held_locks)


def rename_character_memory_storage(
    config_manager,
    old_name: str,
    new_name: str,
    *,
    keep_recent_locks: bool = False,
    recent_transaction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_target_dir = get_runtime_character_memory_dir(config_manager, new_name)
    roots = iter_character_memory_roots(config_manager)
    pending_sources = list_character_recent_paths(config_manager, old_name)
    target_recent_paths = list_character_recent_paths(config_manager, new_name)
    target_recent = runtime_target_dir / "recent.json"
    transaction = recent_transaction or begin_character_recent_transaction(
        config_manager, old_name, new_name,
    )
    recent_paths = transaction["recent_paths"]
    redirect_snapshot = snapshot_recent_redirects(recent_paths)
    deletion_snapshot = snapshot_recent_deletions(recent_paths)
    activation_scope: set[str] = set()
    generation_snapshot: dict[str, tuple[int, int]] = {}
    pending_snapshot: dict[Path, list[Any]] = {}
    try:
        # 目标角色名可能曾被改走；复用该名字前必须切断旧跳转，否则新角色会写进旧目标。
        (
            _,
            activation_scope,
            activation_deletion_snapshot,
            generation_snapshot,
        ) = activate_recent_paths(target_recent_paths)
        deletion_snapshot |= activation_deletion_snapshot
        pending_snapshot = {
            path: deepcopy(get_recent_pending_unlocked(path))
            for path in recent_paths
        }
        changed = False
        for base_dir in roots:
            changed = _merge_directories(base_dir / old_name, runtime_target_dir) or changed

            for legacy_name, target_name in LEGACY_CHARACTER_MEMORY_FILE_MAP.items():
                source_path = base_dir / legacy_name.format(name=old_name)
                target_path = runtime_target_dir / target_name
                changed = _move_path(source_path, target_path) or changed

            for legacy_name in LEGACY_CHARACTER_MEMORY_EXTRA_ENTRIES:
                source_path = base_dir / legacy_name.format(name=old_name)
                if source_path.exists():
                    target_path = runtime_target_dir / "semantic_memory_legacy"
                    changed = _move_path(source_path, target_path) or changed

        changed = _rewrite_recent_file_character_name_unlocked(
            target_recent, old_name, new_name,
        ) or changed

        target_pending = get_recent_pending_unlocked(target_recent)
        for source_recent in pending_sources:
            if source_recent == target_recent:
                continue
            source_pending = get_recent_pending_unlocked(source_recent)
            set_recent_pending_unlocked(source_recent, [])
            target_pending.extend(
                _rewrite_pending_message_character_name(message, old_name, new_name)
                for message in source_pending
            )
        set_recent_pending_unlocked(target_recent, target_pending)
        redirect_recent_paths(pending_sources, target_recent)

        result = {
            "changed": changed,
            "runtime_dir": runtime_target_dir,
            "exists_after": runtime_target_dir.exists(),
            "_recent_rename_transaction": transaction,
        }
        transaction.update({
            "pending_snapshot": pending_snapshot,
            "redirect_snapshot": redirect_snapshot,
            "deletion_snapshot": deletion_snapshot,
            "activation_scope": activation_scope,
            "generation_snapshot": generation_snapshot,
        })
        if not keep_recent_locks:
            release_character_recent_transaction(transaction)
        return result
    except BaseException:
        restore_recent_registry_state(
            list(set(recent_paths) | activation_scope),
            redirect_snapshot,
            deletion_snapshot,
            generation_snapshot,
        )
        for path, messages in pending_snapshot.items():
            set_recent_pending_unlocked(path, messages)
        if not keep_recent_locks:
            release_character_recent_transaction(transaction)
        raise


def finalize_character_recent_rename(result: dict[str, Any]) -> None:
    """Release recent locks after the surrounding rename transaction commits."""
    release_character_recent_transaction(result.get("_recent_rename_transaction"))


def rollback_character_recent_rename(result: dict[str, Any]) -> None:
    """Restore pending state and redirects after the surrounding rename rolls back."""
    transaction = result.get("_recent_rename_transaction") or {}
    snapshot = transaction.get("pending_snapshot") or {}
    recent_paths = transaction.get("recent_paths") or list(snapshot)
    activation_scope = transaction.get("activation_scope") or set()
    held_locks = transaction.get("held_locks") or []
    if not held_locks:
        held_locks = acquire_recent_file_locks(recent_paths)
    try:
        restore_recent_registry_state(
            list(set(recent_paths) | set(activation_scope)),
            transaction.get("redirect_snapshot") or {},
            transaction.get("deletion_snapshot") or set(),
            transaction.get("generation_snapshot") or {},
        )
        for path, messages in snapshot.items():
            set_recent_pending_unlocked(path, messages)
    finally:
        transaction["held_locks"] = held_locks
        release_character_recent_transaction(transaction)


def clear_character_recent_redirects(config_manager, character_name: str) -> None:
    """Detach obsolete path redirects before a newly created name starts writing."""
    recent_paths = list_character_recent_paths(config_manager, character_name)
    with recent_file_locks(recent_paths):
        activate_recent_paths(recent_paths)


def begin_character_recent_activation(
    config_manager, *character_names: str,
) -> dict[str, Any]:
    """Activate a reused character identity while retaining its recent locks."""
    transaction = begin_character_recent_transaction(config_manager, *character_names)
    recent_paths = transaction["recent_paths"]
    try:
        (
            redirect_snapshot,
            activation_scope,
            deletion_snapshot,
            generation_snapshot,
        ) = activate_recent_paths(recent_paths)
        transaction.update({
            "redirect_snapshot": redirect_snapshot,
            "activation_scope": activation_scope,
            "deletion_snapshot": deletion_snapshot,
            "generation_snapshot": generation_snapshot,
        })
        return transaction
    except BaseException:
        release_character_recent_transaction(transaction)
        raise


def rollback_character_recent_activation(transaction: dict[str, Any]) -> None:
    """Restore a reused character identity when publishing its config fails."""
    recent_paths = transaction.get("recent_paths") or []
    activation_scope = transaction.get("activation_scope") or set()
    held_locks = transaction.get("held_locks") or []
    if not held_locks:
        held_locks = acquire_recent_file_locks(recent_paths)
    try:
        restore_recent_registry_state(
            list(set(recent_paths) | set(activation_scope)),
            transaction.get("redirect_snapshot") or {},
            transaction.get("deletion_snapshot") or set(),
            transaction.get("generation_snapshot") or {},
        )
    finally:
        transaction["held_locks"] = held_locks
        release_character_recent_transaction(transaction)


async def _await_task_to_completion(task: asyncio.Task) -> Any:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def asave_characters_with_recent_activation(
    config_manager, characters: dict[str, Any], *character_names: str,
) -> bool:
    """Publish config and recent identities; return whether cancellation was deferred."""
    activation_task = asyncio.create_task(asyncio.to_thread(
        begin_character_recent_activation, config_manager, *character_names,
    ))
    try:
        transaction = await asyncio.shield(activation_task)
    except asyncio.CancelledError:
        transaction = await _await_task_to_completion(activation_task)
        await _await_task_to_completion(asyncio.create_task(asyncio.to_thread(
            rollback_character_recent_activation, transaction,
        )))
        raise

    save_task = asyncio.create_task(config_manager.asave_characters(characters))
    try:
        await asyncio.shield(save_task)
    except asyncio.CancelledError:
        try:
            await _await_task_to_completion(save_task)
        except BaseException:
            await _await_task_to_completion(asyncio.create_task(asyncio.to_thread(
                rollback_character_recent_activation, transaction,
            )))
        else:
            release_character_recent_transaction(transaction)
            return True
        raise
    except BaseException:
        await _await_task_to_completion(asyncio.create_task(asyncio.to_thread(
            rollback_character_recent_activation, transaction,
        )))
        raise
    else:
        release_character_recent_transaction(transaction)
        return False


def delete_character_memory_storage(
    config_manager,
    character_name: str,
    *,
    capture_pending: bool = False,
    keep_recent_locks: bool = False,
    recent_transaction: dict[str, Any] | None = None,
) -> list[Path] | tuple[list[Path], dict[str, Any]]:
    transaction = recent_transaction or begin_character_recent_transaction(
        config_manager, character_name,
    )
    recent_candidates = transaction["recent_paths"]
    redirect_snapshot, deletion_scope, deletion_snapshot = (
        fence_recent_deletions_and_clear_redirects(recent_candidates)
    )
    pending_snapshot: dict[Path, list[Any]] = {}
    try:
        pending_snapshot = {
            path: deepcopy(get_recent_pending_unlocked(path))
            for path in recent_candidates
        }
        removed_paths: list[Path] = []
        for entry_path in list_character_memory_paths(config_manager, character_name):
            if entry_path.is_dir():
                shutil.rmtree(entry_path)
            else:
                entry_path.unlink()
            removed_paths.append(entry_path)

        for recent_path in recent_candidates:
            set_recent_pending_unlocked(recent_path, [])
        transaction.update({
            "pending_snapshot": pending_snapshot,
            "redirect_snapshot": redirect_snapshot,
            "deletion_snapshot": deletion_snapshot,
            "deletion_scope": deletion_scope,
        })
        if not keep_recent_locks:
            release_character_recent_transaction(transaction)
        if capture_pending:
            return removed_paths, transaction
        return removed_paths
    except BaseException:
        restore_recent_redirects(redirect_snapshot)
        restore_recent_deletions(list(deletion_scope), deletion_snapshot)
        for path, messages in pending_snapshot.items():
            set_recent_pending_unlocked(path, messages)
        if not keep_recent_locks:
            release_character_recent_transaction(transaction)
        raise


def finalize_character_recent_delete(result: dict[str, Any]) -> None:
    """Release recent locks after the surrounding delete transaction commits."""
    release_character_recent_transaction(result)


def rollback_character_recent_delete(result: dict[str, Any]) -> None:
    """Restore pending state and redirects after a delete transaction rolls back."""
    recent_paths = result.get("recent_paths") or []
    deletion_scope = result.get("deletion_scope") or recent_paths
    held_locks = result.get("held_locks") or []
    if not held_locks:
        held_locks = acquire_recent_file_locks(recent_paths)
        result["held_locks"] = held_locks
    try:
        restore_recent_redirects(result.get("redirect_snapshot") or {})
        restore_recent_deletions(
            list(deletion_scope), result.get("deletion_snapshot") or set(),
        )
        for path, messages in (result.get("pending_snapshot") or {}).items():
            set_recent_pending_unlocked(path, messages)
    finally:
        release_character_recent_transaction(result)
