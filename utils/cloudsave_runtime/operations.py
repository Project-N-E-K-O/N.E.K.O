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

"""Single-character export/import, full local snapshot export/import, and
operation backup/rollback plumbing.

Split out of the former monolithic ``utils/cloudsave_runtime.py``.
"""

from __future__ import annotations

import shutil
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json

# Late-bound package reference: tests monkeypatch attributes such as
# ``utils.cloudsave_runtime._atomic_copy_file`` and ``_apply_runtime_file``
# on the package facade, so those helpers must be resolved through the
# package at call time instead of being from-imported (early bound) here.
from utils import cloudsave_runtime as _facade

from ._shared import (
    CloudsaveOperationError,
    MANAGED_MEMORY_FILENAMES,
    ROOT_MODE_BOOTSTRAP_IMPORTING,
    _assert_deadline_not_exceeded,
    _raise_cloudsave_disabled,
    _raise_for_name_audit,
    _utc_now_iso,
    audit_cloudsave_character_names,
    is_cloudsave_disabled,
    scan_for_sensitive_values,
)
from .bindings import (
    _build_catalog_current_character_payload,
    _build_catalog_index_payload,
    _build_runtime_preferences_payload,
    _collect_workshop_character_origin_candidates,
    _derive_character_binding_summary,
    _extract_conversation_settings,
    _load_staged_json_file,
    _parse_binding_payloads,
    _parse_catalog_character_names,
)
from .bootstrap import (
    bootstrap_local_cloudsave_environment,
    ensure_cloudsave_manifest,
    load_cloudsave_manifest,
    save_cloudsave_manifest,
)
from .fence import cloud_apply_fence
from .snapshots import (
    _build_local_character_snapshot,
    _collect_cloudsave_binding_payloads,
    _load_cloudsave_character_payloads,
    _load_cloudsave_character_unit,
    _stage_single_character_cloudsave_entries,
    build_cloudsave_character_detail,
)
from .staging import (
    _build_manifest_fingerprint,
    _cleanup_empty_parent_dirs,
    _create_staging_workspace,
    _list_existing_cloudsave_files,
    _load_json_if_exists,
    _load_local_tombstones_state,
    _make_tombstones_catalog_payload,
    _normalize_tombstones_state,
    _save_local_tombstones_state,
    _sha256_file,
    _stage_file_copy,
    _stage_json_file,
    _stage_memory_file,
)


def _assert_single_character_name_safe(character_name: str, *, context: str) -> None:
    audit_result = audit_cloudsave_character_names([character_name])
    try:
        _raise_for_name_audit(audit_result, context=context)
    except ValueError as exc:
        raise CloudsaveOperationError(
            "NAME_AUDIT_FAILED",
            str(exc),
            character_name=character_name,
        ) from exc


def export_cloudsave_character_unit(config_manager, character_name: str, *, overwrite: bool = False) -> dict[str, Any]:
    if is_cloudsave_disabled():
        _raise_cloudsave_disabled("single_character_upload", character_name=character_name)
    bootstrap_local_cloudsave_environment(config_manager)
    _assert_single_character_name_safe(character_name, context="single_character_upload")

    with cloud_apply_fence(
        config_manager,
        mode=ROOT_MODE_BOOTSTRAP_IMPORTING,
        reason=f"single_character_upload:{character_name}",
    ):
        characters_payload = config_manager.load_characters()
        character_payload = (characters_payload.get("猫娘") or {}).get(character_name)
        if not isinstance(character_payload, dict):
            raise CloudsaveOperationError(
                "LOCAL_CHARACTER_NOT_FOUND",
                f"local character not found: {character_name}",
                character_name=character_name,
            )

        existing_cloud_unit = _load_cloudsave_character_unit(config_manager, character_name)
        if existing_cloud_unit is not None and not overwrite:
            raise CloudsaveOperationError(
                "CLOUD_CHARACTER_EXISTS",
                f"cloud character already exists: {character_name}",
                character_name=character_name,
            )

        stage_root = _create_staging_workspace(config_manager, "single-export")
        cloud_state = config_manager.load_cloudsave_local_state()
        sequence_number = max(1, int(cloud_state.get("next_sequence_number") or 1))
        exported_at = _utc_now_iso()
        manifest = ensure_cloudsave_manifest(config_manager)
        workshop_origin_index = _collect_workshop_character_origin_candidates(config_manager)
        binding_payload = _derive_character_binding_summary(
            config_manager,
            character_name,
            character_payload,
            workshop_origin_index=workshop_origin_index,
        )
        local_summary = _build_local_character_snapshot(
            config_manager,
            character_name=character_name,
            character_payload=character_payload,
            characters_config_path=Path(config_manager.get_runtime_config_path("characters.json")),
            workshop_origin_index=workshop_origin_index,
        )

        staged_entries: dict[str, Path] = {}
        existing_cloud_character_map, _tombstone_names = _load_cloudsave_character_payloads(config_manager)
        cloud_profiles_payload = _load_json_if_exists(config_manager.cloudsave_profiles_dir / "characters.json")
        if not isinstance(cloud_profiles_payload, dict):
            cloud_profiles_payload = {}
        cloud_profiles_payload = deepcopy(cloud_profiles_payload)
        merged_cloud_character_map = deepcopy(existing_cloud_character_map)
        merged_cloud_character_map[character_name] = deepcopy(character_payload)
        cloud_profiles_payload["猫娘"] = {
            name: deepcopy(payload)
            for name, payload in sorted(merged_cloud_character_map.items())
        }
        staged_entries["profiles/characters.json"] = _stage_json_file(
            stage_root,
            "profiles/characters.json",
            cloud_profiles_payload,
        )

        staged_entries[f"bindings/{character_name}.json"] = _stage_json_file(
            stage_root,
            f"bindings/{character_name}.json",
            binding_payload,
        )

        character_memory_dir = Path(config_manager.memory_dir) / character_name
        staged_memory_relative_paths: set[str] = set()
        for filename in MANAGED_MEMORY_FILENAMES:
            source_path = character_memory_dir / filename
            if not source_path.is_file():
                continue
            relative_path = f"memory/{character_name}/{filename}"
            staged_entries[relative_path] = _stage_memory_file(stage_root, relative_path, source_path)
            staged_memory_relative_paths.add(relative_path)

        single_character_entries, meta_payload = _stage_single_character_cloudsave_entries(
            config_manager,
            stage_root,
            character_name=character_name,
            character_payload=character_payload,
            binding_payload=binding_payload,
            sequence_number=sequence_number,
            exported_at=exported_at,
            client_id=str(cloud_state.get("client_id", "")),
            device_id=str(manifest.get("device_id", "")),
        )
        staged_entries.update(single_character_entries)

        merged_binding_payloads = _collect_cloudsave_binding_payloads(config_manager)
        merged_binding_payloads[character_name] = deepcopy(binding_payload)
        updated_catalog_payload = _build_catalog_index_payload(
            character_names=sorted(merged_cloud_character_map),
            characters_payload=cloud_profiles_payload,
            binding_payloads=merged_binding_payloads,
            sequence_number=sequence_number,
            exported_at=exported_at,
        )
        staged_entries["catalog/catgirls_index.json"] = _stage_json_file(
            stage_root,
            "catalog/catgirls_index.json",
            updated_catalog_payload,
        )

        updated_tombstones_payload = _remove_tombstone_from_catalog_payload(
            _load_json_if_exists(config_manager.cloudsave_catalog_dir / "character_tombstones.json"),
            character_name=character_name,
            sequence_number=sequence_number,
            exported_at=exported_at,
        )
        staged_entries["catalog/character_tombstones.json"] = _stage_json_file(
            stage_root,
            "catalog/character_tombstones.json",
            updated_tombstones_payload,
        )

        upload_tag = exported_at.replace(":", "").replace(".", "")
        backup_root = config_manager.cloudsave_backups_dir / f"character-upload-{upload_tag}" / character_name

        existing_cloud_memory_root = config_manager.cloudsave_memory_dir / character_name
        existing_cloud_character_root = config_manager.cloudsave_dir / "characters" / character_name
        delete_targets: set[Path] = set()
        for base_dir in (existing_cloud_memory_root, existing_cloud_character_root / "memory"):
            if not base_dir.is_dir():
                continue
            for child in base_dir.iterdir():
                if not child.is_file():
                    continue
                if base_dir == existing_cloud_memory_root:
                    relative_path = f"memory/{character_name}/{child.name}"
                else:
                    relative_path = f"characters/{character_name}/memory/{child.name}"
                if relative_path not in staged_entries:
                    delete_targets.add(child)

        mutation_targets = {
            config_manager.cloudsave_profiles_dir / "characters.json",
            config_manager.cloudsave_bindings_dir / f"{character_name}.json",
            config_manager.cloudsave_catalog_dir / "catgirls_index.json",
            config_manager.cloudsave_catalog_dir / "character_tombstones.json",
            config_manager.cloudsave_dir / "characters" / character_name,
            config_manager.cloudsave_memory_dir / character_name,
            config_manager.cloudsave_manifest_path,
            config_manager.cloudsave_local_state_path,
        }
        backup_records = _snapshot_existing_targets(
            config_manager,
            backup_root,
            mutation_targets | delete_targets,
        )

        try:
            for relative_path, staged_path in staged_entries.items():
                _facade._atomic_copy_file(staged_path, config_manager.cloudsave_dir / relative_path)

            for target_path in sorted(delete_targets):
                if target_path.exists():
                    target_path.unlink()
                    _cleanup_empty_parent_dirs(target_path, config_manager.cloudsave_dir)

            manifest = _rebuild_cloudsave_manifest_from_disk(
                config_manager,
                sequence_number=sequence_number,
                exported_at=exported_at,
                client_id=str(cloud_state.get("client_id", "")),
            )
            cloud_state["next_sequence_number"] = sequence_number + 1
            cloud_state["last_applied_manifest_fingerprint"] = str(manifest.get("fingerprint") or "")
            cloud_state["last_successful_export_at"] = exported_at
            config_manager.save_cloudsave_local_state(cloud_state)
        except Exception:
            _restore_backup_records(backup_records)
            raise

        detail = build_cloudsave_character_detail(config_manager, character_name)
        return {
            "character_name": character_name,
            "sequence_number": sequence_number,
            "meta": meta_payload,
            "manifest": manifest,
            "local_summary": local_summary,
            "detail": detail,
        }


def import_cloudsave_character_unit(
    config_manager,
    character_name: str,
    *,
    overwrite: bool = False,
    backup_before_overwrite: bool = True,
) -> dict[str, Any]:
    if is_cloudsave_disabled():
        _raise_cloudsave_disabled("single_character_download", character_name=character_name)
    bootstrap_local_cloudsave_environment(config_manager)
    _assert_single_character_name_safe(character_name, context="single_character_download")

    with cloud_apply_fence(
        config_manager,
        mode=ROOT_MODE_BOOTSTRAP_IMPORTING,
        reason=f"single_character_download:{character_name}",
    ):
        cloud_unit = _load_cloudsave_character_unit(config_manager, character_name)
        if cloud_unit is None:
            raise CloudsaveOperationError(
                "CLOUD_CHARACTER_NOT_FOUND",
                f"cloud character not found: {character_name}",
                character_name=character_name,
            )

        runtime_characters = config_manager.load_characters()
        local_exists = character_name in (runtime_characters.get("猫娘") or {})
        if local_exists and not overwrite:
            raise CloudsaveOperationError(
                "LOCAL_CHARACTER_EXISTS",
                f"local character already exists: {character_name}",
                character_name=character_name,
            )

        stage_root = _create_staging_workspace(config_manager, "single-import")
        apply_time = _utc_now_iso()
        updated_characters = deepcopy(runtime_characters)
        updated_characters.setdefault("猫娘", {})
        updated_characters["猫娘"][character_name] = deepcopy(cloud_unit["profile"])
        current_character_name = str(updated_characters.get("当前猫娘") or "")
        if not current_character_name:
            updated_characters["当前猫娘"] = character_name
        characters_stage_path = _stage_json_file(
            stage_root,
            "__runtime__/profiles/characters.json",
            updated_characters,
        )

        updated_tombstones_state = _remove_tombstone_from_state_payload(
            config_manager.load_character_tombstones_state(),
            character_name=character_name,
        )
        tombstones_stage_path = _stage_json_file(
            stage_root,
            "__runtime__/state/character_tombstones.json",
            updated_tombstones_state,
        )

        cloud_state = config_manager.load_cloudsave_local_state()
        cloud_state["last_successful_import_at"] = apply_time
        cloud_state_stage_path = _stage_json_file(
            stage_root,
            "__runtime__/state/cloudsave_local_state.json",
            cloud_state,
        )

        runtime_targets: dict[Path, Path] = {
            Path(config_manager.get_runtime_config_path("characters.json")): characters_stage_path,
            config_manager.character_tombstones_state_path: tombstones_stage_path,
            config_manager.cloudsave_local_state_path: cloud_state_stage_path,
        }
        expected_memory_filenames: set[str] = set()
        for filename, source_path in (cloud_unit.get("memory_files") or {}).items():
            target_stage_path = _stage_file_copy(
                stage_root,
                f"__runtime__/memory/{character_name}/{filename}",
                source_path,
            )
            runtime_targets[Path(config_manager.memory_dir) / character_name / filename] = target_stage_path
            expected_memory_filenames.add(filename)

        delete_file_targets: set[Path] = set()
        target_memory_dir = Path(config_manager.memory_dir) / character_name
        for filename in MANAGED_MEMORY_FILENAMES:
            if filename in expected_memory_filenames:
                continue
            candidate = target_memory_dir / filename
            if candidate.exists():
                delete_file_targets.add(candidate)

        backup_root = config_manager.cloudsave_backups_dir / f"character-download-{apply_time.replace(':', '').replace('.', '')}" / character_name
        backup_targets = set(runtime_targets) | delete_file_targets
        if backup_before_overwrite or not local_exists:
            backup_targets.add(target_memory_dir)
        backup_records = _snapshot_existing_targets(config_manager, backup_root, backup_targets)
        _write_operation_backup_metadata(
            config_manager,
            backup_root,
            operation="character_download",
            character_name=character_name,
            backup_records=backup_records,
        )

        try:
            for target_path, staged_path in runtime_targets.items():
                _facade._apply_runtime_file(staged_path, target_path)

            for target_path in sorted(delete_file_targets):
                if target_path.exists():
                    target_path.unlink()
                    _cleanup_empty_parent_dirs(target_path, Path(config_manager.memory_dir))
        except Exception:
            _restore_backup_records(backup_records)
            raise

        detail = build_cloudsave_character_detail(config_manager, character_name)
        return {
            "character_name": character_name,
            "applied_at_utc": apply_time,
            "detail": detail,
            "backup_path": str(backup_root),
        }


def _collect_memory_stage_entries(
    config_manager,
    stage_root: Path,
    character_names: list[str],
    *,
    deadline_monotonic: float | None = None,
    operation: str = "export",
) -> dict[str, Path]:
    staged_entries: dict[str, Path] = {}
    for character_name in sorted(character_names):
        character_dir = Path(config_manager.memory_dir) / character_name
        for filename in MANAGED_MEMORY_FILENAMES:
            _assert_deadline_not_exceeded(
                deadline_monotonic,
                operation=operation,
                stage=f"stage_memory:{character_name}:{filename}",
            )
            source_path = character_dir / filename
            if not source_path.is_file():
                continue
            relative_path = f"memory/{character_name}/{filename}"
            staged_entries[relative_path] = _stage_memory_file(stage_root, relative_path, source_path)
    return staged_entries


def _managed_target_relative_path(config_manager, target_path: Path) -> Path:
    normalized_target = Path(target_path).expanduser().resolve(strict=False)
    runtime_root = Path(config_manager.app_docs_dir).expanduser().resolve(strict=False)
    anchor_root = Path(getattr(config_manager, "anchor_root", config_manager.app_docs_dir)).expanduser().resolve(strict=False)

    candidate_roots = [("runtime", runtime_root)]
    if anchor_root != runtime_root:
        candidate_roots.append(("anchor", anchor_root))
    candidate_roots.sort(key=lambda item: len(item[1].parts), reverse=True)

    for scope, root in candidate_roots:
        try:
            relative_path = normalized_target.relative_to(root)
        except ValueError:
            continue
        return Path(scope) / relative_path

    raise ValueError(f"unmanaged cloudsave backup target: {target_path}")


def _resolve_managed_target_path(config_manager, relative_path: str) -> Path:
    normalized_relative_path = str(relative_path or "").strip().replace("\\", "/")
    if not normalized_relative_path:
        raise ValueError("managed backup relative path is empty")

    parts = Path(normalized_relative_path)
    if not parts.parts or parts.is_absolute() or ".." in parts.parts:
        raise ValueError("managed backup relative path is invalid")

    scope = parts.parts[0]
    suffix = Path(*parts.parts[1:]) if len(parts.parts) > 1 else Path()
    if scope == "anchor":
        root = Path(getattr(config_manager, "anchor_root", config_manager.app_docs_dir))
    elif scope == "runtime":
        root = Path(config_manager.app_docs_dir)
    else:
        # Backward compatibility for backups created before dual-root metadata was introduced.
        root = Path(config_manager.app_docs_dir)
        suffix = Path(normalized_relative_path)

    resolved_root = root.expanduser().resolve(strict=False)
    candidate = (root / suffix).expanduser().resolve(strict=False)
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("managed backup relative path escapes storage root") from exc
    return candidate


def _build_backup_path(config_manager, backup_root: Path, target_path: Path) -> Path:
    return backup_root / _managed_target_relative_path(config_manager, target_path)


def _snapshot_existing_targets(config_manager, backup_root: Path, targets: set[Path]) -> list[dict[str, Any]]:
    backup_records: list[dict[str, Any]] = []
    for target_path in sorted(targets, key=lambda path: (len(path.parts), str(path))):
        relative_path = _managed_target_relative_path(config_manager, target_path)
        record = {
            "target": target_path,
            "backup": None,
            "is_dir": target_path.is_dir(),
            "relative_path": str(relative_path).replace("\\", "/"),
        }
        if target_path.exists():
            backup_path = _build_backup_path(config_manager, backup_root, target_path)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if target_path.is_dir():
                shutil.copytree(target_path, backup_path, dirs_exist_ok=True)
            else:
                shutil.copy2(target_path, backup_path)
            record["backup"] = backup_path
        backup_records.append(record)
    return backup_records


def _restore_backup_records(backup_records: list[dict[str, Any]]) -> None:
    for record in sorted(backup_records, key=lambda item: len(item["target"].parts), reverse=True):
        target_path = record["target"]
        if target_path.exists():
            if target_path.is_dir():
                shutil.rmtree(target_path, ignore_errors=True)
            else:
                target_path.unlink()
        backup_path = record.get("backup")
        if backup_path is None or not backup_path.exists():
            continue
        if record.get("is_dir"):
            shutil.copytree(backup_path, target_path, dirs_exist_ok=True)
        else:
            _facade._apply_runtime_file(backup_path, target_path)


def _write_operation_backup_metadata(
    config_manager,
    backup_root: Path,
    *,
    operation: str,
    character_name: str,
    backup_records: list[dict[str, Any]],
) -> Path:
    payload = {
        "schema_version": 1,
        "operation": operation,
        "character_name": character_name,
        "targets": [
            {
                "relative_path": str(record.get("relative_path") or ""),
                "had_backup": record.get("backup") is not None,
                "is_dir": bool(record.get("is_dir", False)),
            }
            for record in backup_records
        ],
    }
    metadata_path = backup_root / "_operation.json"
    atomic_write_json(metadata_path, payload, ensure_ascii=False, indent=2)
    return metadata_path


def restore_cloudsave_operation_backup(config_manager, backup_root: str | Path) -> None:
    backup_root_path = Path(backup_root)
    metadata = _load_json_if_exists(backup_root_path / "_operation.json")
    if not isinstance(metadata, dict):
        raise FileNotFoundError(f"cloudsave backup metadata missing: {backup_root_path}")

    backup_records: list[dict[str, Any]] = []
    for target in metadata.get("targets") or []:
        if not isinstance(target, dict):
            continue
        relative_path = str(target.get("relative_path") or "").strip().replace("\\", "/")
        if not relative_path:
            continue
        runtime_target = _resolve_managed_target_path(config_manager, relative_path)
        backup_path = backup_root_path / relative_path
        backup_records.append(
            {
                "target": runtime_target,
                "backup": backup_path if bool(target.get("had_backup")) and backup_path.exists() else None,
                "is_dir": bool(target.get("is_dir", False)),
            }
        )
    _restore_backup_records(backup_records)


def _rebuild_cloudsave_manifest_from_disk(
    config_manager,
    *,
    sequence_number: int,
    exported_at: str,
    client_id: str,
) -> dict[str, Any]:
    manifest = ensure_cloudsave_manifest(config_manager)
    files = {
        relative_path: {
            "sha256": _sha256_file(config_manager.cloudsave_dir / relative_path),
            "size": (config_manager.cloudsave_dir / relative_path).stat().st_size,
        }
        for relative_path in sorted(_list_existing_cloudsave_files(config_manager))
    }
    manifest.update(
        {
            "schema_version": 1,
            "min_reader_schema_version": 1,
            "min_app_version": "",
            "client_id": str(client_id or manifest.get("client_id", "")),
            "device_id": str(manifest.get("device_id", "")),
            "sequence_number": int(sequence_number),
            "exported_at_utc": exported_at,
            "files": files,
        }
    )
    manifest["fingerprint"] = _build_manifest_fingerprint(
        client_id=str(manifest.get("client_id", "")),
        sequence_number=int(manifest.get("sequence_number") or 0),
        files=files,
    )
    save_cloudsave_manifest(config_manager, manifest)
    return manifest


def _default_catalog_index_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sequence_number": 0,
        "exported_at_utc": "",
        "characters": [],
    }


def _default_tombstones_catalog_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sequence_number": 0,
        "exported_at_utc": "",
        "tombstones": [],
    }


def _upsert_catalog_character_entry(
    catalog_payload: Any,
    *,
    character_entry: dict[str, Any],
    sequence_number: int,
    exported_at: str,
) -> dict[str, Any]:
    payload = deepcopy(catalog_payload) if isinstance(catalog_payload, dict) else _default_catalog_index_payload()
    entries_by_name: dict[str, dict[str, Any]] = {}
    for entry in payload.get("characters") or []:
        if not isinstance(entry, dict):
            continue
        existing_name = str(entry.get("character_name") or "").strip()
        if existing_name:
            entries_by_name[existing_name] = deepcopy(entry)
    entry_name = str(character_entry.get("character_name") or "").strip()
    if entry_name:
        entries_by_name[entry_name] = deepcopy(character_entry)
    payload["schema_version"] = 1
    payload["sequence_number"] = int(sequence_number)
    payload["exported_at_utc"] = exported_at
    payload["characters"] = [entries_by_name[name] for name in sorted(entries_by_name)]
    return payload


def _remove_tombstone_from_catalog_payload(
    tombstones_payload: Any,
    *,
    character_name: str,
    sequence_number: int,
    exported_at: str,
) -> dict[str, Any]:
    payload = deepcopy(tombstones_payload) if isinstance(tombstones_payload, dict) else _default_tombstones_catalog_payload()
    tombstones_state = _normalize_tombstones_state(payload)
    filtered_tombstones = [
        entry
        for entry in tombstones_state.get("tombstones") or []
        if str(entry.get("character_name") or "") != character_name
    ]
    return {
        "schema_version": 1,
        "sequence_number": int(sequence_number),
        "exported_at_utc": exported_at,
        "tombstones": filtered_tombstones,
    }


def _remove_tombstone_from_state_payload(
    tombstones_payload: Any,
    *,
    character_name: str,
) -> dict[str, Any]:
    tombstones_state = _normalize_tombstones_state(tombstones_payload)
    return {
        "version": 1,
        "tombstones": [
            entry
            for entry in tombstones_state.get("tombstones") or []
            if str(entry.get("character_name") or "") != character_name
        ],
    }


def export_local_cloudsave_snapshot(
    config_manager,
    *,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Export the current local runtime truth into cloudsave/ with manifest-last semantics."""
    if is_cloudsave_disabled():
        _raise_cloudsave_disabled("local_cloudsave_export")
    bootstrap_local_cloudsave_environment(config_manager)

    with cloud_apply_fence(
        config_manager,
        mode=ROOT_MODE_BOOTSTRAP_IMPORTING,
        reason="local_cloudsave_export",
    ):
        _assert_deadline_not_exceeded(
            deadline_monotonic,
            operation="export",
            stage="prepare_export",
        )
        stage_root = _create_staging_workspace(config_manager, "export")
        cloud_state = config_manager.load_cloudsave_local_state()
        sequence_number = max(1, int(cloud_state.get("next_sequence_number") or 1))
        exported_at = _utc_now_iso()

        characters_payload = config_manager.load_characters()
        conversation_settings = _extract_conversation_settings(config_manager)
        tombstones_state = _load_local_tombstones_state(config_manager)
        tombstones = tombstones_state.get("tombstones") or []
        live_character_names = sorted((characters_payload.get("猫娘") or {}).keys())
        live_name_set = set(live_character_names)
        filtered_tombstones = [
            tombstone
            for tombstone in tombstones
            if tombstone.get("character_name") not in live_name_set
        ]
        if filtered_tombstones != tombstones:
            tombstones_state["tombstones"] = filtered_tombstones
            tombstones_state = _save_local_tombstones_state(config_manager, tombstones_state)
            tombstones = tombstones_state.get("tombstones") or []
        tombstone_names = [tombstone["character_name"] for tombstone in tombstones]
        name_audit = audit_cloudsave_character_names(live_character_names, tombstone_names)
        _raise_for_name_audit(name_audit, context="export")
        character_names = live_character_names
        current_character_name = str(characters_payload.get("当前猫娘") or "")
        workshop_origin_index = _collect_workshop_character_origin_candidates(config_manager)
        binding_payloads = {
            name: _derive_character_binding_summary(
                config_manager,
                name,
                (characters_payload.get("猫娘") or {}).get(name, {}),
                workshop_origin_index=workshop_origin_index,
            )
            for name in character_names
        }

        sensitive_findings = scan_for_sensitive_values(characters_payload, path="profiles.characters")
        if sensitive_findings:
            raise ValueError(f"sensitive values detected in export payload: {', '.join(sensitive_findings)}")

        staged_entries: dict[str, Path] = {
            "profiles/characters.json": _stage_json_file(stage_root, "profiles/characters.json", characters_payload),
            "profiles/conversation_settings.json": _stage_json_file(
                stage_root,
                "profiles/conversation_settings.json",
                conversation_settings,
            ),
            "catalog/catgirls_index.json": _stage_json_file(
                stage_root,
                "catalog/catgirls_index.json",
                _build_catalog_index_payload(
                    character_names=character_names,
                    characters_payload=characters_payload,
                    binding_payloads=binding_payloads,
                    sequence_number=sequence_number,
                    exported_at=exported_at,
                ),
            ),
            "catalog/current_character.json": _stage_json_file(
                stage_root,
                "catalog/current_character.json",
                _build_catalog_current_character_payload(
                    current_character_name=current_character_name,
                    exported_at=exported_at,
                    sequence_number=sequence_number,
                ),
            ),
            "catalog/character_tombstones.json": _stage_json_file(
                stage_root,
                "catalog/character_tombstones.json",
                _make_tombstones_catalog_payload(
                    tombstones=tombstones,
                    sequence_number=sequence_number,
                    exported_at=exported_at,
                ),
            ),
        }
        manifest = ensure_cloudsave_manifest(config_manager)
        manifest_device_id = str(manifest.get("device_id", ""))
        for name, binding_payload in binding_payloads.items():
            _assert_deadline_not_exceeded(
                deadline_monotonic,
                operation="export",
                stage=f"stage_character:{name}",
            )
            staged_entries[f"bindings/{name}.json"] = _stage_json_file(
                stage_root,
                f"bindings/{name}.json",
                binding_payload,
            )
            single_character_entries, _meta_payload = _stage_single_character_cloudsave_entries(
                config_manager,
                stage_root,
                character_name=name,
                character_payload=(characters_payload.get("猫娘") or {}).get(name, {}),
                binding_payload=binding_payload,
                sequence_number=sequence_number,
                exported_at=exported_at,
                client_id=str(cloud_state.get("client_id", "")),
                device_id=manifest_device_id,
            )
            staged_entries.update(single_character_entries)
        staged_entries.update(
            _collect_memory_stage_entries(
                config_manager,
                stage_root,
                character_names,
                deadline_monotonic=deadline_monotonic,
                operation="export",
            )
        )

        _assert_deadline_not_exceeded(
            deadline_monotonic,
            operation="export",
            stage="finalize_manifest",
        )
        files = {
            relative_path: {
                "sha256": _sha256_file(staged_path),
                "size": staged_path.stat().st_size,
            }
            for relative_path, staged_path in sorted(staged_entries.items())
        }

        manifest.update(
            {
                "schema_version": 1,
                "min_reader_schema_version": 1,
                "min_app_version": "",
                "client_id": str(cloud_state.get("client_id", "")),
                "device_id": str(manifest.get("device_id", "")),
                "sequence_number": sequence_number,
                "exported_at_utc": exported_at,
                "files": files,
            }
        )
        manifest["fingerprint"] = _build_manifest_fingerprint(
            client_id=manifest["client_id"],
            sequence_number=sequence_number,
            files=files,
        )

        _assert_deadline_not_exceeded(
            deadline_monotonic,
            operation="export",
            stage="apply_snapshot",
        )
        for relative_path, staged_path in staged_entries.items():
            _facade._atomic_copy_file(staged_path, config_manager.cloudsave_dir / relative_path)

        stale_files = _list_existing_cloudsave_files(config_manager) - set(staged_entries)
        for relative_path in sorted(stale_files):
            target_path = config_manager.cloudsave_dir / relative_path
            if target_path.exists():
                target_path.unlink()
                _cleanup_empty_parent_dirs(target_path, config_manager.cloudsave_dir)

        save_cloudsave_manifest(config_manager, manifest)

        cloud_state["next_sequence_number"] = sequence_number + 1
        cloud_state["last_applied_manifest_fingerprint"] = manifest["fingerprint"]
        cloud_state["last_successful_export_at"] = exported_at
        config_manager.save_cloudsave_local_state(cloud_state)

        return {
            "manifest": manifest,
            "staged_file_count": len(staged_entries),
            "name_audit": name_audit,
        }


def import_local_cloudsave_snapshot(
    config_manager,
    *,
    deadline_monotonic: float | None = None,
    use_cloud_apply_fence: bool = True,
) -> dict[str, Any]:
    """Import the current local cloudsave snapshot back into runtime truth with rollback."""
    if is_cloudsave_disabled():
        _raise_cloudsave_disabled("local_cloudsave_import")
    bootstrap_local_cloudsave_environment(config_manager)
    fence_scope = (
        cloud_apply_fence(
            config_manager,
            mode=ROOT_MODE_BOOTSTRAP_IMPORTING,
            reason="local_cloudsave_import",
        )
        if use_cloud_apply_fence
        else nullcontext()
    )
    with fence_scope:
        _assert_deadline_not_exceeded(
            deadline_monotonic,
            operation="import",
            stage="prepare_import",
        )
        manifest = load_cloudsave_manifest(config_manager)
        manifest_files = manifest.get("files") or {}
        if not isinstance(manifest_files, dict) or not manifest_files:
            raise ValueError("cloudsave manifest does not contain any staged files")

        stage_root = _create_staging_workspace(config_manager, "import")
        staged_entries: dict[str, Path] = {}
        for relative_path in sorted(manifest_files):
            _assert_deadline_not_exceeded(
                deadline_monotonic,
                operation="import",
                stage=f"stage_file:{relative_path}",
            )
            source_path = config_manager.cloudsave_dir / relative_path
            if not source_path.is_file():
                raise FileNotFoundError(f"cloudsave file missing from manifest: {relative_path}")
            staged_entries[relative_path] = _stage_file_copy(stage_root, relative_path, source_path)

        computed_files = {
            relative_path: {
                "sha256": _sha256_file(staged_path),
                "size": staged_path.stat().st_size,
            }
            for relative_path, staged_path in sorted(staged_entries.items())
        }
        computed_fingerprint = _build_manifest_fingerprint(
            client_id=str(manifest.get("client_id", "")),
            sequence_number=int(manifest.get("sequence_number") or 0),
            files=computed_files,
        )
        if manifest.get("fingerprint") and manifest["fingerprint"] != computed_fingerprint:
            raise ValueError("cloudsave manifest fingerprint mismatch")

        characters_payload = _load_staged_json_file(staged_entries, "profiles/characters.json", required=True)
        if not isinstance(characters_payload, dict):
            raise ValueError("profiles/characters.json must contain a JSON object")

        conversation_settings = _load_staged_json_file(staged_entries, "profiles/conversation_settings.json") or {}
        if not isinstance(conversation_settings, dict):
            raise ValueError("profiles/conversation_settings.json must contain a JSON object")

        binding_payloads = _parse_binding_payloads(staged_entries)
        catalog_index_payload = _load_staged_json_file(staged_entries, "catalog/catgirls_index.json")
        current_character_catalog_payload = _load_staged_json_file(staged_entries, "catalog/current_character.json")
        tombstones_catalog_payload = _load_staged_json_file(staged_entries, "catalog/character_tombstones.json") or {}
        tombstones_state = _normalize_tombstones_state(tombstones_catalog_payload)
        tombstones = tombstones_state.get("tombstones") or []
        tombstone_names = [tombstone["character_name"] for tombstone in tombstones]

        sensitive_findings = scan_for_sensitive_values(characters_payload, path="profiles.characters")
        if sensitive_findings:
            raise ValueError(f"sensitive values detected in import payload: {', '.join(sensitive_findings)}")

        character_map = deepcopy(characters_payload.get("猫娘") or {})
        live_character_names = sorted(character_map.keys())
        name_audit = audit_cloudsave_character_names(live_character_names, tombstone_names)
        _raise_for_name_audit(name_audit, context="import")

        catalog_character_names = _parse_catalog_character_names(catalog_index_payload)
        if catalog_character_names and catalog_character_names != set(live_character_names):
            raise ValueError("catalog/catgirls_index.json is inconsistent with profiles/characters.json")
        if binding_payloads and set(binding_payloads) != set(live_character_names):
            raise ValueError("bindings/ payloads are inconsistent with profiles/characters.json")

        for tombstone_name in tombstone_names:
            character_map.pop(tombstone_name, None)
        characters_payload["猫娘"] = character_map

        requested_current_name = str(characters_payload.get("当前猫娘") or "").strip()
        if isinstance(current_character_catalog_payload, dict):
            catalog_current_name = str(current_character_catalog_payload.get("current_character_name") or "").strip()
            if catalog_current_name:
                requested_current_name = catalog_current_name

        imported_character_names = sorted(character_map.keys())
        if requested_current_name and requested_current_name in character_map:
            characters_payload["当前猫娘"] = requested_current_name
        elif imported_character_names:
            characters_payload["当前猫娘"] = imported_character_names[0]
        else:
            characters_payload["当前猫娘"] = ""
        apply_time = _utc_now_iso()
        backup_root = config_manager.cloudsave_backups_dir / f"import-{apply_time.replace(':', '').replace('.', '')}"

        characters_stage_path = _stage_json_file(
            stage_root,
            "__runtime__/profiles/characters.json",
            characters_payload,
        )
        runtime_targets: dict[Path, Path] = {
            Path(config_manager.get_runtime_config_path("characters.json")): characters_stage_path,
        }

        preferences_stage_path = _stage_json_file(
            stage_root,
            "__runtime__/user_preferences.json",
            _build_runtime_preferences_payload(config_manager, conversation_settings),
        )
        runtime_targets[Path(config_manager.get_runtime_config_path("user_preferences.json"))] = preferences_stage_path

        for relative_path, staged_path in staged_entries.items():
            if not relative_path.startswith("memory/"):
                continue
            parts = Path(relative_path).parts
            if len(parts) != 3:
                raise ValueError(f"unsupported cloudsave memory path: {relative_path}")
            _, character_name, filename = parts
            if character_name in tombstone_names:
                continue
            runtime_targets[Path(config_manager.memory_dir) / character_name / filename] = staged_path

        cloud_state = config_manager.load_cloudsave_local_state()
        cloud_state["last_applied_manifest_fingerprint"] = computed_fingerprint
        cloud_state["last_successful_import_at"] = apply_time
        cloud_state_stage_path = _stage_json_file(
            stage_root,
            "__runtime__/state/cloudsave_local_state.json",
            cloud_state,
        )
        runtime_targets[config_manager.cloudsave_local_state_path] = cloud_state_stage_path
        tombstones_state_stage_path = _stage_json_file(
            stage_root,
            "__runtime__/state/character_tombstones.json",
            tombstones_state,
        )
        runtime_targets[config_manager.character_tombstones_state_path] = tombstones_state_stage_path

        delete_file_targets: set[Path] = set()
        delete_dir_targets: set[Path] = set()
        for character_name in imported_character_names:
            character_dir = Path(config_manager.memory_dir) / character_name
            for filename in MANAGED_MEMORY_FILENAMES:
                relative_path = f"memory/{character_name}/{filename}"
                target_path = character_dir / filename
                if relative_path not in staged_entries and target_path.exists():
                    delete_file_targets.add(target_path)

        memory_root = Path(config_manager.memory_dir)
        if memory_root.exists():
            for child in memory_root.iterdir():
                if child.is_dir() and child.name not in imported_character_names:
                    delete_dir_targets.add(child)

        backup_records: list[dict[str, Any]] = []
        for target_path in sorted(
            set(runtime_targets) | delete_file_targets | delete_dir_targets,
            key=lambda path: len(path.parts),
        ):
            record = {
                "target": target_path,
                "backup": None,
                "is_dir": target_path.is_dir(),
            }
            if target_path.exists():
                backup_path = _build_backup_path(config_manager, backup_root, target_path)
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                if target_path.is_dir():
                    shutil.copytree(target_path, backup_path, dirs_exist_ok=True)
                else:
                    shutil.copy2(target_path, backup_path)
                record["backup"] = backup_path
            backup_records.append(record)

        _assert_deadline_not_exceeded(
            deadline_monotonic,
            operation="import",
            stage="apply_runtime",
        )
        try:
            for target_path, staged_path in runtime_targets.items():
                _facade._apply_runtime_file(staged_path, target_path)

            for target_path in sorted(delete_file_targets):
                if target_path.exists():
                    target_path.unlink()
                    _cleanup_empty_parent_dirs(target_path, Path(config_manager.memory_dir))

            for target_path in sorted(delete_dir_targets, key=lambda path: len(path.parts), reverse=True):
                if target_path.exists():
                    shutil.rmtree(target_path)

            return {
                "manifest_fingerprint": computed_fingerprint,
                "applied_character_count": len(imported_character_names),
                "name_audit": name_audit,
            }
        except Exception:
            for record in sorted(backup_records, key=lambda item: len(item["target"].parts), reverse=True):
                target_path = record["target"]
                if target_path.exists():
                    if target_path.is_dir():
                        shutil.rmtree(target_path, ignore_errors=True)
                    else:
                        target_path.unlink()
                backup_path = record["backup"]
                if backup_path is None or not backup_path.exists():
                    continue
                if record["is_dir"]:
                    shutil.copytree(backup_path, target_path, dirs_exist_ok=True)
                else:
                    _facade._apply_runtime_file(backup_path, target_path)
            raise
