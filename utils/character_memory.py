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

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from utils.recent_file import (
    acquire_recent_file_locks,
    clear_recent_redirects,
    get_recent_pending_unlocked,
    read_recent_text_unlocked,
    recent_file_lock,
    recent_file_locks,
    redirect_recent_paths,
    release_recent_file_locks,
    restore_recent_redirects,
    set_recent_pending_unlocked,
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


def rename_character_memory_storage(
    config_manager, old_name: str, new_name: str, *, keep_recent_locks: bool = False,
) -> dict[str, Any]:
    runtime_target_dir = get_runtime_character_memory_dir(config_manager, new_name)
    roots = iter_character_memory_roots(config_manager)
    pending_sources = list_character_recent_paths(config_manager, old_name)
    target_recent = runtime_target_dir / "recent.json"
    recent_paths = list(dict.fromkeys([*pending_sources, target_recent]))
    held_locks = acquire_recent_file_locks(recent_paths)
    target_redirect_snapshot: dict[str, str] = {}
    try:
        # 目标角色名可能曾被改走；复用该名字前必须切断旧跳转，否则新角色会写进旧目标。
        target_redirect_snapshot = clear_recent_redirects([target_recent])
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
            "_recent_rename_transaction": {
                "pending_snapshot": pending_snapshot,
                "recent_paths": recent_paths,
                "redirect_sources": pending_sources,
                "target_redirect_snapshot": target_redirect_snapshot,
                "held_locks": held_locks if keep_recent_locks else [],
            },
        }
        if not keep_recent_locks:
            release_recent_file_locks(held_locks)
        return result
    except BaseException:
        restore_recent_redirects(target_redirect_snapshot)
        release_recent_file_locks(held_locks)
        raise


def finalize_character_recent_rename(result: dict[str, Any]) -> None:
    """Release recent locks after the surrounding rename transaction commits."""
    transaction = result.get("_recent_rename_transaction") or {}
    held_locks = transaction.get("held_locks") or []
    if held_locks:
        transaction["held_locks"] = []
        release_recent_file_locks(held_locks)


def rollback_character_recent_rename(result: dict[str, Any]) -> None:
    """Restore pending state and redirects after the surrounding rename rolls back."""
    transaction = result.get("_recent_rename_transaction") or {}
    snapshot = transaction.get("pending_snapshot") or {}
    recent_paths = transaction.get("recent_paths") or list(snapshot)
    held_locks = transaction.get("held_locks") or []
    if not held_locks:
        held_locks = acquire_recent_file_locks(recent_paths)
    try:
        clear_recent_redirects(transaction.get("redirect_sources") or [])
        restore_recent_redirects(transaction.get("target_redirect_snapshot") or {})
        for path, messages in snapshot.items():
            set_recent_pending_unlocked(path, messages)
    finally:
        transaction["held_locks"] = []
        release_recent_file_locks(held_locks)


def clear_character_recent_redirects(config_manager, character_name: str) -> None:
    """Detach obsolete path redirects before a newly created name starts writing."""
    clear_recent_redirects(list_character_recent_paths(config_manager, character_name))


def delete_character_memory_storage(
    config_manager, character_name: str, *, capture_pending: bool = False,
) -> list[Path] | tuple[list[Path], dict[Path, list[Any]]]:
    recent_candidates = list_character_recent_paths(config_manager, character_name)
    clear_recent_redirects(recent_candidates)
    with recent_file_locks(recent_candidates):
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

    if capture_pending:
        return removed_paths, pending_snapshot
    return removed_paths
