from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json


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


def iter_character_memory_roots(config_manager) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    for raw_path in (
        getattr(config_manager, "memory_dir", None),
        getattr(config_manager, "project_memory_dir", None),
    ):
        if not raw_path:
            continue
        root = Path(raw_path)
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)

    return roots


def get_runtime_character_memory_dir(config_manager, character_name: str) -> Path:
    return Path(config_manager.memory_dir) / character_name


def character_memory_exists(config_manager, character_name: str) -> bool:
    for base_dir in iter_character_memory_roots(config_manager):
        if (base_dir / character_name).exists():
            return True

        for pattern in LEGACY_CHARACTER_MEMORY_FILE_MAP:
            if (base_dir / pattern.format(name=character_name)).exists():
                return True

        for pattern in LEGACY_CHARACTER_MEMORY_EXTRA_ENTRIES:
            if (base_dir / pattern.format(name=character_name)).exists():
                return True

    return False


def _move_path(source_path: Path, target_path: Path) -> bool:
    if not source_path.exists():
        return False

    if source_path.is_dir():
        return _merge_directories(source_path, target_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        source_path.unlink()
        return True

    shutil.move(str(source_path), str(target_path))
    return True


def _merge_directories(source_dir: Path, target_dir: Path) -> bool:
    if not source_dir.exists():
        return False

    if not target_dir.exists():
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_dir), str(target_dir))
        return True

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
    name_fields = ("speaker", "author", "name", "character", "role")

    for field in name_fields:
        value = item.get(field)
        if isinstance(value, str) and value == old_name:
            item[field] = new_name
            changed = True

    nested_data = item.get("data")
    if isinstance(nested_data, dict):
        for field in name_fields:
            value = nested_data.get(field)
            if isinstance(value, str) and value == old_name:
                nested_data[field] = new_name
                changed = True

        content = nested_data.get("content")
        if isinstance(content, str):
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
            nested_data["content"] = content

    return changed


def rewrite_recent_file_character_name(recent_path: Path, old_name: str, new_name: str) -> bool:
    if old_name == new_name or not recent_path.is_file():
        return False

    try:
        with open(recent_path, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except Exception:
        return False

    if not isinstance(payload, list):
        return False

    changed = False
    for item in payload:
        if not isinstance(item, dict):
            continue
        changed = _rewrite_recent_message_character_name(item, old_name, new_name) or changed

    if changed:
        atomic_write_json(recent_path, payload, ensure_ascii=False, indent=2)

    return changed


def rename_character_memory_storage(config_manager, old_name: str, new_name: str) -> dict[str, Any]:
    runtime_target_dir = get_runtime_character_memory_dir(config_manager, new_name)
    changed = False

    for base_dir in iter_character_memory_roots(config_manager):
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

    changed = rewrite_recent_file_character_name(
        runtime_target_dir / "recent.json",
        old_name,
        new_name,
    ) or changed

    return {
        "changed": changed,
        "runtime_dir": runtime_target_dir,
        "exists_after": runtime_target_dir.exists(),
    }


def delete_character_memory_storage(config_manager, character_name: str) -> list[Path]:
    removed_paths: list[Path] = []

    delete_patterns = [character_name]
    delete_patterns.extend(
        pattern.format(name=character_name)
        for pattern in LEGACY_CHARACTER_MEMORY_FILE_MAP
    )
    delete_patterns.extend(
        pattern.format(name=character_name)
        for pattern in LEGACY_CHARACTER_MEMORY_EXTRA_ENTRIES
    )

    for base_dir in iter_character_memory_roots(config_manager):
        for entry_name in delete_patterns:
            entry_path = base_dir / entry_name
            if not entry_path.exists():
                continue

            if entry_path.is_dir():
                shutil.rmtree(entry_path)
            else:
                entry_path.unlink()
            removed_paths.append(entry_path)

    return removed_paths
