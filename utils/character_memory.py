from __future__ import annotations

import json
import shutil
import uuid
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

MESSAGE_NAME_FIELDS = ("speaker", "author", "name", "character")


def iter_character_memory_roots(config_manager) -> list[Path]:
    """返回所有承载角色记忆的运行时根目录（去重、保持插入顺序）。

    只返回当前激活的 runtime 路径：
      - ``memory_dir``：当前运行时的 ``<app_docs>/memory``。
      - ``project_memory_dir``：项目目录下的种子/默认 memory 位置。

    历史遗留路径（``Documents\\N.E.K.O\\memory`` 等 CFA 回退或老版本写过
    的根）**不**在此列。那类数据由以下两条路径单独处理，避免删除/清理
    逻辑意外波及非运行时位置：

      - 启动软迁移：``ConfigManager.migrate_legacy_documents_memory`` 只
        把仍在 ``characters.json[猫娘]`` 的目录搬到 runtime。
      - 手动清理按钮：创意工坊页面的"清理遗留记忆"扫描 + 勾选删除。
    """
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


def _conflict_backup_root(target_dir: Path) -> Path:
    return target_dir.parent / f".{target_dir.name}.rename_conflicts" / uuid.uuid4().hex


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _mark_protected_target(path: Path, protected_targets: set[str] | None) -> None:
    if protected_targets is None:
        return
    protected_targets.add(_path_key(path))
    if path.is_dir():
        for descendant in path.rglob("*"):
            protected_targets.add(_path_key(descendant))


def _move_conflicting_target(target_path: Path, backup_root: Path) -> None:
    backup_path = backup_root / target_path.name
    counter = 1
    while backup_path.exists():
        backup_path = backup_root / f"{target_path.name}.{counter}"
        counter += 1

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(target_path), str(backup_path))


def _move_path(
    source_path: Path,
    target_path: Path,
    *,
    replace_existing: bool = False,
    conflict_backup_root: Path | None = None,
    protected_targets: set[str] | None = None,
) -> bool:
    if not source_path.exists():
        return False
    try:
        if source_path.resolve() == target_path.resolve():
            return False
    except OSError:
        pass

    if source_path.is_dir():
        return _merge_directories(
            source_path,
            target_path,
            replace_existing=replace_existing,
            conflict_backup_root=conflict_backup_root,
            protected_targets=protected_targets,
        )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        if protected_targets is not None and _path_key(target_path) in protected_targets:
            if conflict_backup_root is None:
                conflict_backup_root = _conflict_backup_root(target_path.parent)
            _move_conflicting_target(source_path, conflict_backup_root)
            return True
        if not replace_existing:
            raise FileExistsError(
                f"Refusing to overwrite existing memory file while moving "
                f"{source_path} -> {target_path}"
            )
        if conflict_backup_root is None:
            conflict_backup_root = _conflict_backup_root(target_path.parent)
        _move_conflicting_target(target_path, conflict_backup_root)

    shutil.move(str(source_path), str(target_path))
    _mark_protected_target(target_path, protected_targets)
    return True


def _merge_directories(
    source_dir: Path,
    target_dir: Path,
    *,
    replace_existing: bool = False,
    conflict_backup_root: Path | None = None,
    protected_targets: set[str] | None = None,
) -> bool:
    if not source_dir.exists():
        return False
    try:
        if source_dir.resolve() == target_dir.resolve():
            return False
    except OSError:
        pass

    if not target_dir.exists():
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_dir), str(target_dir))
        _mark_protected_target(target_dir, protected_targets)
        return True

    if not target_dir.is_dir():
        if protected_targets is not None and _path_key(target_dir) in protected_targets:
            if conflict_backup_root is None:
                conflict_backup_root = _conflict_backup_root(target_dir.parent)
            _move_conflicting_target(source_dir, conflict_backup_root)
            return True
        if not replace_existing:
            raise FileExistsError(
                f"Refusing to overwrite existing path while merging directories "
                f"{source_dir} -> {target_dir}: target is not a directory"
            )
        if conflict_backup_root is None:
            conflict_backup_root = _conflict_backup_root(target_dir.parent)
        _move_conflicting_target(target_dir, conflict_backup_root)
        shutil.move(str(source_dir), str(target_dir))
        _mark_protected_target(target_dir, protected_targets)
        return True

    if not replace_existing:
        # Pre-flight: check for conflicts before moving anything.
        for child in source_dir.iterdir():
            candidate = target_dir / child.name
            if candidate.exists():
                raise FileExistsError(
                    f"Refusing to overwrite existing path while merging directories "
                    f"{source_dir} -> {target_dir}: conflict at {child.name}"
                )

    if conflict_backup_root is None:
        conflict_backup_root = _conflict_backup_root(target_dir)

    changed = False
    for child in sorted(source_dir.iterdir(), key=lambda item: item.name):
        changed = _move_path(
            child,
            target_dir / child.name,
            replace_existing=replace_existing,
            conflict_backup_root=conflict_backup_root,
            protected_targets=protected_targets,
        ) or changed

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
    protected_targets: set[str] = set()

    for base_dir in iter_character_memory_roots(config_manager):
        conflict_backup_root = _conflict_backup_root(runtime_target_dir)
        changed = _merge_directories(
            base_dir / old_name,
            runtime_target_dir,
            replace_existing=True,
            conflict_backup_root=conflict_backup_root,
            protected_targets=protected_targets,
        ) or changed

        for legacy_name, target_name in LEGACY_CHARACTER_MEMORY_FILE_MAP.items():
            source_path = base_dir / legacy_name.format(name=old_name)
            target_path = runtime_target_dir / target_name
            changed = _move_path(
                source_path,
                target_path,
                replace_existing=True,
                conflict_backup_root=conflict_backup_root,
                protected_targets=protected_targets,
            ) or changed

        for legacy_name in LEGACY_CHARACTER_MEMORY_EXTRA_ENTRIES:
            source_path = base_dir / legacy_name.format(name=old_name)
            if source_path.exists():
                target_path = runtime_target_dir / "semantic_memory_legacy"
                changed = _move_path(
                    source_path,
                    target_path,
                    replace_existing=True,
                    conflict_backup_root=conflict_backup_root,
                    protected_targets=protected_targets,
                ) or changed

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
    for entry_path in list_character_memory_paths(config_manager, character_name):
        if entry_path.is_dir():
            shutil.rmtree(entry_path)
        else:
            entry_path.unlink()
        removed_paths.append(entry_path)

    return removed_paths
