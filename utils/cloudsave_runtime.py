from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unicodedata
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import DEFAULT_CONFIG_DATA
from utils.character_name import PROFILE_NAME_MAX_UNITS, validate_character_name
from utils.file_utils import atomic_write_json


ROOT_MODE_NORMAL = "normal"
ROOT_MODE_BOOTSTRAP_IMPORTING = "bootstrap_importing"
ROOT_MODE_BOOTSTRAP_READONLY = "bootstrap_readonly"
ROOT_MODE_DEFERRED_INIT = "deferred_init"
ROOT_MODE_MAINTENANCE_READONLY = "maintenance_readonly"

WRITE_BLOCKING_MODES = frozenset(
    {
        ROOT_MODE_BOOTSTRAP_IMPORTING,
        ROOT_MODE_BOOTSTRAP_READONLY,
        ROOT_MODE_DEFERRED_INIT,
        ROOT_MODE_MAINTENANCE_READONLY,
    }
)

SENSITIVE_TOKENS = (
    "api_key",
    "authorization",
    "bearer",
    "cookie",
    "token",
    "sk-",
)

GLOBAL_CONVERSATION_KEY = "__global_conversation__"
MANAGED_MEMORY_FILENAMES = (
    "recent.json",
    "settings.json",
    "facts.json",
    "facts_archive.json",
    "persona.json",
    "persona_corrections.json",
    "reflections.json",
    "surfaced.json",
    "time_indexed.db",
)
MANAGED_CLOUDSAVE_PREFIXES = (
    "catalog/",
    "profiles/",
    "bindings/",
    "memory/",
    "overrides/",
    "meta/",
)
LEGACY_RUNTIME_DIR_NAMES = (
    "config",
    "memory",
    "plugins",
    "live2d",
    "vrm",
    "mmd",
    "workshop",
    "character_cards",
    "cloudsave",
    "cloudsave_backups",
    ".cloudsave_staging",
)
LEGACY_OPTIONAL_STATE_FILES = (
    "cloudsave_local_state.json",
)
TARGET_OPTIONAL_STATE_FILES = (
    "root_state.json",
    "cloudsave_local_state.json",
    "character_tombstones.json",
)
ROOT_CONFIG_MERGE_FILES = (
    "core_config.json",
    "voice_storage.json",
    "workshop_config.json",
)
RUNTIME_ASSET_DIR_NAMES = (
    "plugins",
    "live2d",
    "vrm",
    "mmd",
    "workshop",
    "character_cards",
)

_cloud_apply_lock_handle = None
_cloud_apply_lock_file = None


class MaintenanceModeError(RuntimeError):
    """Raised when a write is attempted while the global cloudsave fence is active."""

    def __init__(self, mode: str, *, operation: str = "write", target: str = ""):
        self.mode = str(mode or ROOT_MODE_NORMAL)
        self.operation = str(operation or "write")
        self.target = str(target or "")
        self.code = "CLOUDSAVE_WRITE_FENCE_ACTIVE"
        detail = f"{self.operation} blocked while root_state.mode={self.mode}"
        if self.target:
            detail = f"{detail} ({self.target})"
        super().__init__(detail)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_default_cloudsave_manifest(*, client_id: str = "") -> dict[str, Any]:
    """Build the minimal local manifest skeleton for phase 0."""
    return {
        "schema_version": 1,
        "min_reader_schema_version": 1,
        "min_app_version": "",
        "client_id": str(client_id or ""),
        "device_id": "",
        "sequence_number": 0,
        "exported_at_utc": "",
        "files": {},
        "fingerprint": "",
    }


def _json_canonical_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _create_staging_workspace(config_manager, prefix: str) -> Path:
    config_manager.ensure_cloudsave_structure()
    return Path(
        tempfile.mkdtemp(
            prefix=f"{prefix}-",
            dir=str(config_manager.cloudsave_staging_dir),
        )
    )


def _atomic_copy_file(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=str(target_path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as temp_file, open(source_path, "rb") as source_file:
            shutil.copyfileobj(source_file, temp_file)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, target_path)
    except Exception:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
        raise


def _stage_json_file(stage_root: Path, relative_path: str, payload: Any) -> Path:
    target_path = stage_root / relative_path
    atomic_write_json(target_path, payload, ensure_ascii=False, indent=2)
    return target_path


def _stage_file_copy(stage_root: Path, relative_path: str, source_path: Path) -> Path:
    staged_path = stage_root / relative_path
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, staged_path)
    return staged_path


def _list_existing_cloudsave_files(config_manager) -> set[str]:
    existing_files: set[str] = set()
    for prefix in MANAGED_CLOUDSAVE_PREFIXES:
        prefix_path = config_manager.cloudsave_dir / prefix.rstrip("/")
        if not prefix_path.exists():
            continue
        for file_path in prefix_path.rglob("*"):
            if file_path.is_file():
                existing_files.add(str(file_path.relative_to(config_manager.cloudsave_dir)).replace("\\", "/"))
    return existing_files


def _cleanup_empty_parent_dirs(path: Path, stop_at: Path) -> None:
    current = path.parent
    while current != stop_at and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _build_manifest_fingerprint(*, client_id: str, sequence_number: int, files: dict[str, Any]) -> str:
    payload = {
        "client_id": client_id,
        "sequence_number": int(sequence_number),
        "files": files,
    }
    return _sha256_bytes(_json_canonical_dumps(payload).encode("utf-8"))


def _normalize_tombstone_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    character_name = str(entry.get("character_name") or entry.get("name") or "").strip()
    if not character_name:
        return None
    try:
        sequence_number = int(entry.get("sequence_number") or 0)
    except (TypeError, ValueError):
        sequence_number = 0
    return {
        "character_name": character_name,
        "deleted_at": str(entry.get("deleted_at") or ""),
        "sequence_number": sequence_number,
    }


def _normalize_tombstones_state(payload: Any) -> dict[str, Any]:
    raw_entries = []
    if isinstance(payload, dict):
        raw_entries = payload.get("tombstones") or []
    elif isinstance(payload, list):
        raw_entries = payload

    normalized_entries: dict[str, dict[str, Any]] = {}
    for raw_entry in raw_entries:
        normalized_entry = _normalize_tombstone_entry(raw_entry)
        if normalized_entry is None:
            continue
        key = normalized_entry["character_name"]
        existing_entry = normalized_entries.get(key)
        if existing_entry is None or normalized_entry["sequence_number"] >= existing_entry["sequence_number"]:
            normalized_entries[key] = normalized_entry

    return {
        "version": 1,
        "tombstones": [
            normalized_entries[name]
            for name in sorted(normalized_entries)
        ],
    }


def _load_local_tombstones_state(config_manager) -> dict[str, Any]:
    return _normalize_tombstones_state(config_manager.load_character_tombstones_state())


def _save_local_tombstones_state(config_manager, payload: Any) -> dict[str, Any]:
    normalized_state = _normalize_tombstones_state(payload)
    config_manager.save_character_tombstones_state(normalized_state)
    return normalized_state


def _load_tombstone_names_from_state_path(state_path: Path) -> set[str]:
    payload = _load_json_if_exists(state_path)
    normalized_state = _normalize_tombstones_state(payload)
    return {
        entry["character_name"]
        for entry in normalized_state.get("tombstones") or []
        if isinstance(entry, dict) and entry.get("character_name")
    }


def _make_tombstones_catalog_payload(*, tombstones: list[dict[str, Any]], sequence_number: int, exported_at: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sequence_number": sequence_number,
        "exported_at_utc": exported_at,
        "tombstones": deepcopy(tombstones),
    }


def _normalize_audit_name(raw_name: Any) -> str:
    return unicodedata.normalize("NFC", str(raw_name or "").strip())


def audit_cloudsave_character_names(
    character_names: list[str] | tuple[str, ...],
    tombstone_names: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    entries_by_key: dict[str, list[dict[str, Any]]] = {}

    def _record_entry(source: str, raw_name: Any):
        original = "" if raw_name is None else str(raw_name)
        trimmed = original.strip()
        normalized = _normalize_audit_name(original)

        if original != trimmed:
            errors.append({
                "type": "trimmed_whitespace",
                "source": source,
                "name": original,
            })

        validation = validate_character_name(
            trimmed,
            allow_dots=False,
            max_units=PROFILE_NAME_MAX_UNITS,
        )
        if not validation.ok:
            errors.append({
                "type": "invalid_name",
                "source": source,
                "name": original,
                "code": validation.code,
                "invalid_char": validation.invalid_char,
            })

        if trimmed and normalized != trimmed:
            warnings.append({
                "type": "normalization_changed",
                "source": source,
                "name": original,
                "normalized_name": normalized,
            })

        if normalized:
            casefold_key = normalized.casefold()
            entries_by_key.setdefault(casefold_key, []).append({
                "source": source,
                "name": original,
                "normalized_name": normalized,
            })

    for name in character_names:
        _record_entry("character", name)
    for name in tombstone_names:
        _record_entry("tombstone", name)

    for casefold_key, entries in entries_by_key.items():
        normalized_names = {entry["normalized_name"] for entry in entries}
        original_names = {entry["name"] for entry in entries}
        if len(entries) > 1 and (len(normalized_names) > 1 or len(original_names) > 1):
            errors.append({
                "type": "casefold_conflict",
                "casefold_key": casefold_key,
                "entries": entries,
            })

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def _raise_for_name_audit(audit_result: dict[str, Any], *, context: str) -> None:
    errors = audit_result.get("errors") or []
    if not errors:
        return

    rendered_errors = []
    for error in errors[:5]:
        error_type = error.get("type")
        if error_type == "casefold_conflict":
            rendered_errors.append(
                "casefold_conflict:"
                + ",".join(f"{entry.get('source')}={entry.get('name')}" for entry in error.get("entries") or [])
            )
        elif error_type == "invalid_name":
            rendered_errors.append(
                f"invalid_name:{error.get('source')}={error.get('name')}({error.get('code')})"
            )
        else:
            rendered_errors.append(f"{error_type}:{error.get('source')}={error.get('name')}")
    raise ValueError(f"{context} character name audit failed: {'; '.join(rendered_errors)}")


def _runtime_root_has_user_content(root: Path) -> bool:
    if not root.exists():
        return False
    for name in LEGACY_RUNTIME_DIR_NAMES:
        candidate = root / name
        if candidate.is_file():
            return True
        if candidate.is_dir():
            try:
                for child in candidate.iterdir():
                    if _is_ignorable_runtime_entry(child):
                        continue
                    return True
            except StopIteration:
                continue
    return False


def _is_ignorable_runtime_entry(path: Path) -> bool:
    name = path.name
    if name == ".gitkeep":
        return True
    if name.startswith("."):
        return True
    if name == "__pycache__":
        return True
    return False


def _copy_runtime_root_entries(source_root: Path, destination_root: Path) -> list[str]:
    copied_paths: list[str] = []
    for name in LEGACY_RUNTIME_DIR_NAMES:
        source_path = source_root / name
        if not source_path.exists():
            continue
        destination_path = destination_root / name
        if source_path.is_dir():
            shutil.copytree(source_path, destination_path, dirs_exist_ok=True)
        else:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)
        copied_paths.append(name)
    return copied_paths


def _load_json_if_exists(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            return json.load(file_obj)
    except Exception:
        return None


def _directory_has_meaningful_content(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        for child in path.iterdir():
            if _is_ignorable_runtime_entry(child):
                continue
            return True
    except Exception:
        return False
    return False


def _collect_memory_character_names(root: Path) -> set[str]:
    memory_root = root / "memory"
    character_names: set[str] = set()
    if not memory_root.is_dir():
        return character_names
    try:
        for child in memory_root.iterdir():
            if _is_ignorable_runtime_entry(child):
                continue
            if child.is_dir() and _directory_has_meaningful_content(child):
                character_names.add(child.name)
            elif child.is_file():
                character_names.add(child.stem)
    except Exception:
        return character_names
    return character_names


def _load_seed_characters_payload(config_manager) -> dict[str, Any]:
    localized_source = None
    try:
        localized_source = config_manager._get_localized_characters_source()
    except Exception:
        localized_source = None
    if localized_source is not None:
        payload = _load_json_if_exists(Path(localized_source))
        if isinstance(payload, dict):
            return payload
    fallback_payload = config_manager.get_default_characters()
    return fallback_payload if isinstance(fallback_payload, dict) else {}


def _normalize_catgirl_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    normalized_payload = deepcopy(payload)
    try:
        from utils.config_manager import migrate_catgirl_reserved

        migrate_catgirl_reserved(normalized_payload)
    except Exception:
        pass
    return normalized_payload


def _character_payload_looks_default(config_manager, name: str, payload: Any) -> bool:
    normalized_payload = _normalize_catgirl_payload(payload)
    if normalized_payload is None:
        return False
    default_payload = _normalize_catgirl_payload((_load_seed_characters_payload(config_manager).get("猫娘") or {}).get(name))
    return default_payload is not None and normalized_payload == default_payload


def _master_payload_looks_default(config_manager, payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    default_payload = _load_seed_characters_payload(config_manager).get("主人")
    return default_payload is not None and payload == default_payload


def _normalize_preferences_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return deepcopy(payload)
    if isinstance(payload, dict):
        return [deepcopy(payload)]
    return []


def _preferences_entry_key(entry: Any) -> str:
    if isinstance(entry, dict) and entry.get("model_path") is not None:
        return f"model_path:{entry.get('model_path')}"
    return _json_canonical_dumps(entry)


def _merge_preferences_payloads(legacy_payload: Any, current_payload: Any) -> list[Any]:
    merged_entries: dict[str, Any] = {}
    ordered_keys: list[str] = []
    for payload in (_normalize_preferences_payload(legacy_payload), _normalize_preferences_payload(current_payload)):
        for entry in payload:
            key = _preferences_entry_key(entry)
            if key not in merged_entries:
                ordered_keys.append(key)
            merged_entries[key] = deepcopy(entry)
    return [merged_entries[key] for key in ordered_keys]


def _deep_merge_json_dicts(legacy_payload: Any, current_payload: Any) -> dict[str, Any]:
    legacy_dict = deepcopy(legacy_payload) if isinstance(legacy_payload, dict) else {}
    current_dict = current_payload if isinstance(current_payload, dict) else {}
    for key, value in current_dict.items():
        if isinstance(legacy_dict.get(key), dict) and isinstance(value, dict):
            legacy_dict[key] = _deep_merge_json_dicts(legacy_dict[key], value)
        else:
            legacy_dict[key] = deepcopy(value)
    return legacy_dict


def _config_payload_looks_default(filename: str, payload: Any) -> bool:
    default_payload = DEFAULT_CONFIG_DATA.get(filename)
    if filename == "user_preferences.json":
        return _normalize_preferences_payload(payload) == _normalize_preferences_payload(default_payload)
    if isinstance(default_payload, dict):
        return isinstance(payload, dict) and deepcopy(payload) == deepcopy(default_payload)
    if isinstance(default_payload, list):
        return isinstance(payload, list) and deepcopy(payload) == deepcopy(default_payload)
    return False


def _config_payload_looks_seeded(config_manager, filename: str, payload: Any) -> bool:
    project_payload = _load_json_if_exists(Path(config_manager.project_config_dir) / filename)
    if project_payload is not None:
        if filename == "user_preferences.json":
            return _normalize_preferences_payload(payload) == _normalize_preferences_payload(project_payload)
        return deepcopy(payload) == deepcopy(project_payload)
    return _config_payload_looks_default(filename, payload)


def _merge_characters_payloads(
    config_manager,
    legacy_payload: Any,
    current_payload: Any,
    *,
    preserve_current_only_defaults: bool,
) -> dict[str, Any]:
    legacy_dict = deepcopy(legacy_payload) if isinstance(legacy_payload, dict) else {}
    current_dict = deepcopy(current_payload) if isinstance(current_payload, dict) else {}
    merged_payload = deepcopy(legacy_dict)

    for key, value in current_dict.items():
        if key not in {"猫娘", "主人", "当前猫娘"}:
            merged_payload[key] = deepcopy(value)

    legacy_catgirls = legacy_dict.get("猫娘") or {}
    current_catgirls = current_dict.get("猫娘") or {}
    merged_catgirls: dict[str, Any] = {}
    for name in sorted(set(legacy_catgirls) | set(current_catgirls)):
        legacy_character = legacy_catgirls.get(name)
        current_character = current_catgirls.get(name)
        if legacy_character is None:
            if not preserve_current_only_defaults and _character_payload_looks_default(config_manager, name, current_character):
                continue
            chosen = current_character
        elif current_character is None:
            chosen = legacy_character
        else:
            current_default = _character_payload_looks_default(config_manager, name, current_character)
            legacy_default = _character_payload_looks_default(config_manager, name, legacy_character)
            if current_default and not legacy_default:
                chosen = legacy_character
            elif legacy_default and not current_default:
                chosen = current_character
            else:
                chosen = current_character
        if chosen is not None:
            merged_catgirls[name] = deepcopy(chosen)
    merged_payload["猫娘"] = merged_catgirls

    legacy_master = legacy_dict.get("主人")
    current_master = current_dict.get("主人")
    if legacy_master is None:
        if current_master is not None:
            merged_payload["主人"] = deepcopy(current_master)
    elif current_master is None:
        merged_payload["主人"] = deepcopy(legacy_master)
    else:
        current_master_default = _master_payload_looks_default(config_manager, current_master)
        legacy_master_default = _master_payload_looks_default(config_manager, legacy_master)
        chosen_master = legacy_master if current_master_default and not legacy_master_default else current_master
        merged_payload["主人"] = deepcopy(chosen_master)

    current_current_name = str(current_dict.get("当前猫娘") or "")
    legacy_current_name = str(legacy_dict.get("当前猫娘") or "")
    if current_current_name and current_current_name in merged_catgirls:
        current_current_payload = current_catgirls.get(current_current_name)
        current_default = _character_payload_looks_default(config_manager, current_current_name, current_current_payload)
        if current_current_name not in legacy_catgirls and not preserve_current_only_defaults and current_default:
            current_current_name = ""
        elif current_current_name not in legacy_catgirls or not current_default:
            merged_payload["当前猫娘"] = current_current_name
        elif legacy_current_name and legacy_current_name in merged_catgirls:
            merged_payload["当前猫娘"] = legacy_current_name
        else:
            merged_payload["当前猫娘"] = current_current_name
    elif legacy_current_name and legacy_current_name in merged_catgirls:
        merged_payload["当前猫娘"] = legacy_current_name
    elif current_current_name and current_current_name in merged_catgirls:
        merged_payload["当前猫娘"] = current_current_name
    elif merged_catgirls:
        merged_payload["当前猫娘"] = next(iter(merged_catgirls))
    else:
        merged_payload["当前猫娘"] = ""

    return merged_payload


def _runtime_root_summary(config_manager, root: Path) -> dict[str, Any]:
    config_root = root / "config"
    characters_path = config_root / "characters.json"
    user_preferences_path = config_root / "user_preferences.json"
    voice_storage_path = config_root / "voice_storage.json"
    workshop_config_path = config_root / "workshop_config.json"
    core_config_path = config_root / "core_config.json"

    characters_payload = _load_json_if_exists(characters_path)
    user_preferences_payload = _load_json_if_exists(user_preferences_path)
    voice_storage_payload = _load_json_if_exists(voice_storage_path)
    core_config_payload = _load_json_if_exists(core_config_path)
    if not isinstance(characters_payload, dict):
        characters_payload = None
    character_names = set((characters_payload or {}).get("猫娘", {}) or {})
    default_character_names = set((_load_seed_characters_payload(config_manager).get("猫娘") or {}).keys())

    asset_dirs_with_content = {
        dir_name: _directory_has_meaningful_content(root / dir_name)
        for dir_name in RUNTIME_ASSET_DIR_NAMES
    }
    memory_character_names = _collect_memory_character_names(root)
    score = (
        len(character_names) * 3
        + len(memory_character_names) * 2
        + (3 if user_preferences_path.is_file() else 0)
        + (2 if voice_storage_path.is_file() else 0)
        + (1 if workshop_config_path.is_file() else 0)
        + (1 if core_config_path.is_file() else 0)
        + sum(2 for has_content in asset_dirs_with_content.values() if has_content)
    )

    return {
        "has_user_content": _runtime_root_has_user_content(root),
        "characters_payload": characters_payload,
        "character_names": character_names,
        "memory_character_names": memory_character_names,
        "has_user_preferences": user_preferences_path.is_file(),
        "has_voice_storage": voice_storage_path.is_file(),
        "has_workshop_config": workshop_config_path.is_file(),
        "has_core_config": core_config_path.is_file(),
        "asset_dirs_with_content": asset_dirs_with_content,
        "looks_like_seeded": (
            bool(character_names)
            and character_names.issubset(default_character_names)
            and not memory_character_names
            and (
                not user_preferences_path.is_file()
                or _config_payload_looks_seeded(config_manager, "user_preferences.json", user_preferences_payload)
            )
            and (
                not voice_storage_path.is_file()
                or _config_payload_looks_seeded(config_manager, "voice_storage.json", voice_storage_payload)
            )
            and not workshop_config_path.is_file()
            and (
                not core_config_path.is_file()
                or _config_payload_looks_seeded(config_manager, "core_config.json", core_config_payload)
            )
            and not any(asset_dirs_with_content.values())
        ),
        "score": score,
    }


def _legacy_root_provides_repair_benefit(config_manager, source_summary: dict[str, Any], target_summary: dict[str, Any]) -> tuple[bool, str]:
    if not target_summary["has_user_content"]:
        return True, "target_missing"

    source_is_richer = source_summary["score"] > target_summary["score"]

    if (source_is_richer or target_summary["looks_like_seeded"]) and source_summary["character_names"] - target_summary["character_names"]:
        return True, "missing_characters"

    if (source_is_richer or target_summary["looks_like_seeded"]) and source_summary["memory_character_names"] - target_summary["memory_character_names"]:
        return True, "missing_memory"

    for flag_name, reason in (
        ("has_user_preferences", "missing_user_preferences"),
        ("has_voice_storage", "missing_voice_storage"),
        ("has_workshop_config", "missing_workshop_config"),
        ("has_core_config", "missing_core_config"),
    ):
        if (source_is_richer or target_summary["looks_like_seeded"]) and source_summary[flag_name] and not target_summary[flag_name]:
            return True, reason

    for dir_name, source_has_content in source_summary["asset_dirs_with_content"].items():
        if (source_is_richer or target_summary["looks_like_seeded"]) and source_has_content and not target_summary["asset_dirs_with_content"].get(dir_name):
            return True, f"missing_{dir_name}"

    source_characters = (source_summary.get("characters_payload") or {}).get("猫娘", {}) or {}
    target_characters = (target_summary.get("characters_payload") or {}).get("猫娘", {}) or {}
    for name in sorted(set(source_characters) & set(target_characters)):
        if (
            _character_payload_looks_default(config_manager, name, target_characters.get(name))
            and not _character_payload_looks_default(config_manager, name, source_characters.get(name))
        ):
            return True, "upgrade_default_character"

    if target_summary["looks_like_seeded"] and source_summary["score"] > target_summary["score"]:
        return True, "repair_seeded_target"

    return False, ""


def _stage_merged_runtime_configs(config_manager, *, source_root: Path, target_root: Path, temp_root: Path, target_summary: dict[str, Any]) -> None:
    config_dir = temp_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    target_tombstone_names = _load_tombstone_names_from_state_path(
        target_root / "state" / "character_tombstones.json"
    )

    source_characters = _load_json_if_exists(source_root / "config" / "characters.json")
    target_characters = _load_json_if_exists(target_root / "config" / "characters.json")
    if source_characters is not None or target_characters is not None:
        merged_characters = _merge_characters_payloads(
            config_manager,
            source_characters,
            target_characters,
            preserve_current_only_defaults=not target_summary["looks_like_seeded"],
        )
        if target_tombstone_names:
            merged_catgirls = merged_characters.get("猫娘") or {}
            for deleted_name in target_tombstone_names:
                merged_catgirls.pop(deleted_name, None)
            merged_characters["猫娘"] = merged_catgirls
            current_name = str(merged_characters.get("当前猫娘") or "")
            if current_name in target_tombstone_names:
                merged_characters["当前猫娘"] = next(iter(merged_catgirls), "")
        atomic_write_json(config_dir / "characters.json", merged_characters, ensure_ascii=False, indent=2)

    source_preferences = _load_json_if_exists(source_root / "config" / "user_preferences.json")
    target_preferences = _load_json_if_exists(target_root / "config" / "user_preferences.json")
    if source_preferences is not None or target_preferences is not None:
        merged_preferences = _merge_preferences_payloads(source_preferences, target_preferences)
        atomic_write_json(config_dir / "user_preferences.json", merged_preferences, ensure_ascii=False, indent=2)

    for filename in ROOT_CONFIG_MERGE_FILES:
        source_payload = _load_json_if_exists(source_root / "config" / filename)
        target_payload = _load_json_if_exists(target_root / "config" / filename)
        if source_payload is None and target_payload is None:
            continue
        merged_payload = _deep_merge_json_dicts(source_payload, target_payload)
        atomic_write_json(config_dir / filename, merged_payload, ensure_ascii=False, indent=2)


def _copy_optional_legacy_state(*, source_root: Path, target_root: Path, temp_root: Path) -> list[str]:
    copied_paths: list[str] = []
    for filename in TARGET_OPTIONAL_STATE_FILES:
        target_path = target_root / "state" / filename
        if not target_path.is_file():
            continue
        _stage_file_copy(temp_root, f"state/{filename}", target_path)
        copied_paths.append(f"state/{filename}")
    for filename in LEGACY_OPTIONAL_STATE_FILES:
        source_path = source_root / "state" / filename
        if not source_path.is_file() or (temp_root / "state" / filename).is_file():
            continue
        _stage_file_copy(temp_root, f"state/{filename}", source_path)
        copied_paths.append(f"state/{filename}")
    return copied_paths


def _create_legacy_import_backup_path(target_root: Path) -> Path:
    backup_pool = target_root.parent / f".{target_root.name}.legacy-import-backups"
    backup_pool.mkdir(parents=True, exist_ok=True)
    backup_slot = Path(tempfile.mkdtemp(prefix="backup-", dir=str(backup_pool)))
    return backup_slot / target_root.name


def _replace_runtime_root(target_root: Path, temp_root: Path, *, backup_path: Path | None = None) -> None:
    if backup_path is None:
        if target_root.exists():
            shutil.rmtree(target_root, ignore_errors=True)
        os.replace(temp_root, target_root)
        return

    restore_required = False
    try:
        if target_root.exists():
            os.replace(target_root, backup_path)
            restore_required = True
        os.replace(temp_root, target_root)
    except Exception:
        if restore_required and backup_path.exists() and not target_root.exists():
            os.replace(backup_path, target_root)
        raise


def _legacy_source_was_already_imported(
    root_state: Any,
    *,
    source_root: Path,
    target_root: Path,
) -> bool:
    """Treat legacy root import as a one-shot bootstrap repair per source root.

    Once a legacy root has already been imported and the migrated target has
    completed at least one successful boot, future startups should treat the
    current runtime root as the source of truth. Otherwise, deletions performed
    in the new runtime root can be "repaired" back from the stale legacy root.
    """
    if not isinstance(root_state, dict):
        return False
    if str(root_state.get("current_root") or "") != str(target_root):
        return False
    if not str(root_state.get("last_successful_boot_at") or "").strip():
        return False
    if str(root_state.get("last_migration_source") or "") != str(source_root):
        return False
    last_result = str(root_state.get("last_migration_result") or "")
    return last_result.startswith("legacy_root_")


def import_legacy_runtime_root_if_needed(config_manager) -> dict[str, Any]:
    """One-time bootstrap import from legacy roots into the deterministic app data root."""
    target_root = Path(config_manager.app_docs_dir)
    target_has_user_content = _runtime_root_has_user_content(target_root)
    target_summary = _runtime_root_summary(config_manager, target_root)
    existing_root_state = None
    try:
        if config_manager.root_state_path.is_file():
            existing_root_state = config_manager.load_root_state()
    except Exception:
        existing_root_state = None
    saw_legacy_source = False

    for source_root in config_manager.get_legacy_app_root_candidates():
        source_root = Path(source_root)
        if not _runtime_root_has_user_content(source_root):
            continue
        saw_legacy_source = True
        if _legacy_source_was_already_imported(
            existing_root_state,
            source_root=source_root,
            target_root=target_root,
        ):
            continue

        source_summary = _runtime_root_summary(config_manager, source_root)
        should_repair, repair_reason = _legacy_root_provides_repair_benefit(
            config_manager,
            source_summary,
            target_summary,
        )
        if target_has_user_content and not should_repair:
            continue

        temp_root = target_root.parent / f".{target_root.name}.bootstrap-import"
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)
        temp_root.parent.mkdir(parents=True, exist_ok=True)

        copied_paths: list[str] = []
        backup_path: Path | None = None
        try:
            copied_paths.extend(_copy_runtime_root_entries(source_root, temp_root))
            if target_has_user_content:
                _copy_runtime_root_entries(target_root, temp_root)
                _stage_merged_runtime_configs(
                    config_manager,
                    source_root=source_root,
                    target_root=target_root,
                    temp_root=temp_root,
                    target_summary=target_summary,
                )
                backup_path = _create_legacy_import_backup_path(target_root)
            copied_paths.extend(_copy_optional_legacy_state(source_root=source_root, target_root=target_root, temp_root=temp_root))

            if not copied_paths:
                shutil.rmtree(temp_root, ignore_errors=True)
                continue

            _replace_runtime_root(target_root, temp_root, backup_path=backup_path)
            return {
                "migrated": True,
                "source": str(source_root),
                "copied_paths": sorted(set(copied_paths)),
                "backup_path": str(backup_path) if backup_path is not None else "",
                "repair_reason": repair_reason,
                "result": "legacy_root_repaired_target" if target_has_user_content else "legacy_root_imported",
            }
        except Exception:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise

    if target_has_user_content:
        return {
            "migrated": False,
            "source": "",
            "copied_paths": [],
            "backup_path": "",
            "repair_reason": "",
            "result": "target_root_already_initialized" if saw_legacy_source or target_summary["has_user_content"] else "no_legacy_root_found",
        }

    return {
        "migrated": False,
        "source": "",
        "copied_paths": [],
        "backup_path": "",
        "repair_reason": "",
        "result": "no_legacy_root_found",
    }


def _load_user_preferences_entries(config_manager) -> list[dict[str, Any]]:
    preferences_path = Path(config_manager.get_config_path("user_preferences.json"))
    if not preferences_path.exists():
        return []
    try:
        with open(preferences_path, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except Exception:
        return []
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return payload
    return []


def _extract_conversation_settings(config_manager) -> dict[str, Any]:
    for entry in _load_user_preferences_entries(config_manager):
        if isinstance(entry, dict) and entry.get("model_path") == GLOBAL_CONVERSATION_KEY:
            return {
                key: value
                for key, value in entry.items()
                if key != "model_path"
            }
    return {}


def _build_runtime_preferences_payload(config_manager, conversation_settings: dict[str, Any]) -> list[dict[str, Any]]:
    preferences = [
        entry
        for entry in _load_user_preferences_entries(config_manager)
        if not isinstance(entry, dict) or entry.get("model_path") != GLOBAL_CONVERSATION_KEY
    ]
    filtered_settings = {
        key: value
        for key, value in (conversation_settings or {}).items()
        if key != "model_path"
    }
    if filtered_settings:
        preferences.append({
            "model_path": GLOBAL_CONVERSATION_KEY,
            **filtered_settings,
        })
    return preferences


def _derive_binding_model_reference(character_payload: dict[str, Any]) -> tuple[str, str]:
    from utils.config_manager import get_reserved

    runtime_model_type = str(
        get_reserved(character_payload, "avatar", "model_type", default="live2d", legacy_keys=("model_type",))
    ).strip().lower()
    live2d_model_path = str(
        get_reserved(character_payload, "avatar", "live2d", "model_path", default="", legacy_keys=("live2d",))
        or ""
    ).strip()
    vrm_model_path = str(
        get_reserved(character_payload, "avatar", "vrm", "model_path", default="", legacy_keys=("vrm",))
        or ""
    ).strip()
    mmd_model_path = str(
        get_reserved(character_payload, "avatar", "mmd", "model_path", default="")
        or ""
    ).strip()

    if runtime_model_type in {"live3d", "vrm"}:
        if mmd_model_path:
            return "mmd", mmd_model_path.replace("\\", "/")
        if vrm_model_path:
            return "vrm", vrm_model_path.replace("\\", "/")
        if live2d_model_path:
            return "live2d", live2d_model_path.replace("\\", "/")
        return "vrm", ""

    return "live2d", live2d_model_path.replace("\\", "/")


def _derive_binding_asset_source(*, model_ref: str, stored_asset_source: str, asset_source_id: str) -> str:
    normalized_ref = str(model_ref or "").strip().replace("\\", "/")
    normalized_source = str(stored_asset_source or "").strip().lower()

    if normalized_source == "steam_workshop" or asset_source_id or normalized_ref.startswith("/workshop/"):
        return "steam_workshop"
    if normalized_source == "builtin":
        return "builtin"
    if normalized_source in {"manual_external", "external"}:
        return "manual_external"
    if normalized_source in {"local_imported", "local"}:
        if normalized_ref.startswith("/static/") or (normalized_ref and not normalized_ref.startswith("/")):
            return "builtin"
        return "local_imported"
    if normalized_ref.startswith(("http://", "https://")):
        return "manual_external"
    if normalized_ref.startswith(("/user_live2d/", "/user_live2d_local/", "/user_vrm/", "/user_mmd/")):
        return "local_imported"
    if normalized_ref.startswith("/static/") or (normalized_ref and not normalized_ref.startswith("/")):
        return "builtin"
    return "local_imported" if normalized_ref else ""


def _derive_binding_asset_source_id(*, model_ref: str, stored_source_id: str) -> str:
    source_id = str(stored_source_id or "").strip()
    if source_id:
        return source_id
    normalized_ref = str(model_ref or "").strip().replace("\\", "/")
    if normalized_ref.startswith("/workshop/"):
        parts = normalized_ref.split("/")
        if len(parts) >= 3:
            return parts[2]
    return ""


def _derive_binding_asset_display_name(model_ref: str) -> str:
    normalized_ref = str(model_ref or "").strip().replace("\\", "/")
    if not normalized_ref:
        return ""
    if normalized_ref.endswith(".model3.json"):
        parts = [part for part in normalized_ref.split("/") if part]
        if len(parts) >= 2:
            return parts[-2]
        return Path(parts[-1]).stem.replace(".model3", "")
    if normalized_ref.endswith((".vrm", ".pmx", ".pmd", ".vmd", ".vrma")):
        return Path(normalized_ref).stem
    parts = [part for part in normalized_ref.split("/") if part]
    return parts[-1] if parts else normalized_ref


def _resolve_binding_file_path(
    config_manager,
    *,
    model_type: str,
    model_ref: str,
    asset_source: str,
    asset_source_id: str,
) -> Path | None:
    normalized_ref = str(model_ref or "").strip().replace("\\", "/")
    if not normalized_ref or normalized_ref.startswith(("http://", "https://")):
        return None

    candidates: list[Path] = []
    readable_live2d_dir = getattr(config_manager, "readable_live2d_dir", None)

    if model_type == "live2d":
        if normalized_ref.startswith("/user_live2d/"):
            relative_part = normalized_ref[len("/user_live2d/"):]
            if readable_live2d_dir is not None:
                candidates.append(Path(readable_live2d_dir) / relative_part)
            candidates.append(Path(config_manager.live2d_dir) / relative_part)
        elif normalized_ref.startswith("/user_live2d_local/"):
            candidates.append(Path(config_manager.live2d_dir) / normalized_ref[len("/user_live2d_local/"):])
        elif normalized_ref.startswith("/workshop/"):
            candidates.append(Path(config_manager.workshop_dir) / "/".join(normalized_ref.split("/")[2:]))
        else:
            if asset_source == "steam_workshop" and asset_source_id:
                candidates.append(Path(config_manager.workshop_dir) / asset_source_id / normalized_ref)
                candidates.append(Path(config_manager.workshop_dir) / asset_source_id / Path(normalized_ref).name)
            if asset_source == "local_imported":
                if readable_live2d_dir is not None:
                    candidates.append(Path(readable_live2d_dir) / normalized_ref)
                candidates.append(Path(config_manager.live2d_dir) / normalized_ref)
            candidates.append(Path(config_manager.project_root) / "static" / normalized_ref)
    elif model_type == "vrm":
        if normalized_ref.startswith("/user_vrm/"):
            candidates.append(Path(config_manager.vrm_dir) / normalized_ref[len("/user_vrm/"):])
        elif normalized_ref.startswith("/static/vrm/"):
            candidates.append(Path(config_manager.project_root) / "static" / "vrm" / normalized_ref[len("/static/vrm/"):])
        elif normalized_ref.startswith("/workshop/"):
            candidates.append(Path(config_manager.workshop_dir) / "/".join(normalized_ref.split("/")[2:]))
    elif model_type == "mmd":
        if normalized_ref.startswith("/user_mmd/"):
            candidates.append(Path(config_manager.mmd_dir) / normalized_ref[len("/user_mmd/"):])
        elif normalized_ref.startswith("/static/mmd/"):
            candidates.append(Path(config_manager.project_root) / "static" / "mmd" / normalized_ref[len("/static/mmd/"):])
        elif normalized_ref.startswith("/workshop/"):
            candidates.append(Path(config_manager.workshop_dir) / "/".join(normalized_ref.split("/")[2:]))

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _derive_binding_asset_state(*, resolved_path: Path | None, asset_source: str, model_ref: str) -> str:
    if resolved_path is not None and resolved_path.is_file():
        return "ready"
    if not str(model_ref or "").strip():
        return "missing"
    if asset_source == "steam_workshop":
        return "downloadable"
    if asset_source in {"local_imported", "manual_external"}:
        return "import_required"
    return "missing"


def _derive_binding_experience_overrides(character_payload: dict[str, Any]) -> dict[str, Any]:
    from utils.config_manager import get_reserved

    overrides = {
        "touch_set": deepcopy(get_reserved(character_payload, "touch_set", default={}) or {}),
        "vrm_lighting": deepcopy(get_reserved(character_payload, "avatar", "vrm", "lighting", default={}) or {}),
        "mmd_lighting": deepcopy(get_reserved(character_payload, "avatar", "mmd", "lighting", default={}) or {}),
        "mmd_rendering": deepcopy(get_reserved(character_payload, "avatar", "mmd", "rendering", default={}) or {}),
        "mmd_physics": deepcopy(get_reserved(character_payload, "avatar", "mmd", "physics", default={}) or {}),
        "mmd_cursor_follow": deepcopy(get_reserved(character_payload, "avatar", "mmd", "cursor_follow", default={}) or {}),
    }
    return {
        key: value
        for key, value in overrides.items()
        if value not in ({}, None, [])
    }


def _derive_character_binding_summary(config_manager, character_name: str, character_payload: dict[str, Any]) -> dict[str, Any]:
    from utils.config_manager import get_reserved

    binding_model_type, model_ref = _derive_binding_model_reference(character_payload)
    stored_source = get_reserved(character_payload, "avatar", "asset_source", default="")
    stored_source_id = get_reserved(
        character_payload,
        "avatar",
        "asset_source_id",
        default="",
        legacy_keys=("live2d_item_id", "item_id"),
    )
    asset_source_id = _derive_binding_asset_source_id(model_ref=model_ref, stored_source_id=str(stored_source_id or ""))
    asset_source = _derive_binding_asset_source(
        model_ref=model_ref,
        stored_asset_source=str(stored_source or ""),
        asset_source_id=asset_source_id,
    )
    resolved_path = _resolve_binding_file_path(
        config_manager,
        model_type=binding_model_type,
        model_ref=model_ref,
        asset_source=asset_source,
        asset_source_id=asset_source_id,
    )
    asset_state = _derive_binding_asset_state(
        resolved_path=resolved_path,
        asset_source=asset_source,
        model_ref=model_ref,
    )
    asset_fingerprint = _sha256_file(resolved_path) if resolved_path is not None else ""

    fallback_model_ref = ""
    if asset_state != "ready" and binding_model_type != "live2d":
        fallback_model_ref = "mao_pro/mao_pro.model3.json"

    return {
        "character_name": character_name,
        "model_type": binding_model_type,
        "asset_source": asset_source,
        "asset_source_id": asset_source_id,
        "model_ref": model_ref,
        "asset_display_name": _derive_binding_asset_display_name(model_ref),
        "asset_fingerprint": asset_fingerprint,
        "asset_state": asset_state,
        "fallback_model_ref": fallback_model_ref,
        "last_verified_at": _utc_now_iso() if resolved_path is not None else "",
        "experience_overrides": _derive_binding_experience_overrides(character_payload),
    }


def _build_catalog_index_payload(
    *,
    character_names: list[str],
    characters_payload: dict[str, Any],
    binding_payloads: dict[str, dict[str, Any]],
    sequence_number: int,
    exported_at: str,
) -> dict[str, Any]:
    catgirls_payload = characters_payload.get("猫娘") or {}
    return {
        "schema_version": 1,
        "sequence_number": sequence_number,
        "exported_at_utc": exported_at,
        "characters": [
            {
                "character_name": name,
                "entry_sequence_number": sequence_number,
                "has_memory": True,
                "model_type": binding_payloads.get(name, {}).get("model_type", ""),
                "asset_source": binding_payloads.get(name, {}).get("asset_source", ""),
                "asset_source_id": binding_payloads.get(name, {}).get("asset_source_id", ""),
                "asset_state": binding_payloads.get(name, {}).get("asset_state", ""),
                "asset_display_name": binding_payloads.get(name, {}).get("asset_display_name", ""),
                "asset_fingerprint": binding_payloads.get(name, {}).get("asset_fingerprint", ""),
                "display_name": str((catgirls_payload.get(name) or {}).get("档案名") or name),
            }
            for name in character_names
        ],
    }


def _load_staged_json_file(staged_entries: dict[str, Path], relative_path: str, *, required: bool = False) -> Any:
    staged_path = staged_entries.get(relative_path)
    if staged_path is None:
        if required:
            raise ValueError(f"cloudsave import requires {relative_path}")
        return None
    with open(staged_path, "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _parse_binding_payloads(staged_entries: dict[str, Path]) -> dict[str, dict[str, Any]]:
    binding_payloads: dict[str, dict[str, Any]] = {}
    for relative_path, staged_path in staged_entries.items():
        if not relative_path.startswith("bindings/") or not relative_path.endswith(".json"):
            continue
        binding_name = Path(relative_path).stem
        with open(staged_path, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        if not isinstance(payload, dict):
            raise ValueError(f"{relative_path} must contain a JSON object")
        payload_name = str(payload.get("character_name") or "").strip()
        if payload_name and payload_name != binding_name:
            raise ValueError(f"{relative_path} character_name does not match filename")
        binding_payloads[binding_name] = payload
    return binding_payloads


def _parse_catalog_character_names(payload: Any) -> set[str]:
    if payload is None:
        return set()
    if not isinstance(payload, dict):
        raise ValueError("catalog/catgirls_index.json must contain a JSON object")
    names: set[str] = set()
    for entry in payload.get("characters") or []:
        if not isinstance(entry, dict):
            raise ValueError("catalog/catgirls_index.json contains a non-object entry")
        name = str(entry.get("character_name") or "").strip()
        if not name:
            raise ValueError("catalog/catgirls_index.json contains an empty character_name")
        names.add(name)
    return names


def _build_catalog_current_character_payload(*, current_character_name: str, exported_at: str, sequence_number: int) -> dict[str, Any]:
    return {
        "current_character_name": current_character_name,
        "last_known_name": current_character_name,
        "applied_at_utc": exported_at,
        "entry_sequence_number": sequence_number,
    }


def _collect_memory_stage_entries(config_manager, stage_root: Path, character_names: list[str]) -> dict[str, Path]:
    staged_entries: dict[str, Path] = {}
    for character_name in sorted(character_names):
        character_dir = Path(config_manager.memory_dir) / character_name
        for filename in MANAGED_MEMORY_FILENAMES:
            source_path = character_dir / filename
            if not source_path.is_file():
                continue
            relative_path = f"memory/{character_name}/{filename}"
            staged_entries[relative_path] = _stage_file_copy(stage_root, relative_path, source_path)
    return staged_entries


def _build_backup_path(config_manager, backup_root: Path, target_path: Path) -> Path:
    return backup_root / target_path.relative_to(config_manager.app_docs_dir)


def export_local_cloudsave_snapshot(config_manager) -> dict[str, Any]:
    """Export the current local runtime truth into cloudsave/ with manifest-last semantics."""
    bootstrap_local_cloudsave_environment(config_manager)

    with cloud_apply_fence(
        config_manager,
        mode=ROOT_MODE_BOOTSTRAP_IMPORTING,
        reason="local_cloudsave_export",
    ):
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
        binding_payloads = {
            name: _derive_character_binding_summary(config_manager, name, (characters_payload.get("猫娘") or {}).get(name, {}))
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
        for name, binding_payload in binding_payloads.items():
            staged_entries[f"bindings/{name}.json"] = _stage_json_file(
                stage_root,
                f"bindings/{name}.json",
                binding_payload,
            )
        staged_entries.update(_collect_memory_stage_entries(config_manager, stage_root, character_names))

        files = {
            relative_path: {
                "sha256": _sha256_file(staged_path),
                "size": staged_path.stat().st_size,
            }
            for relative_path, staged_path in sorted(staged_entries.items())
        }

        manifest = ensure_cloudsave_manifest(config_manager)
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

        for relative_path, staged_path in staged_entries.items():
            _atomic_copy_file(staged_path, config_manager.cloudsave_dir / relative_path)

        stale_files = _list_existing_cloudsave_files(config_manager) - set(staged_entries)
        for relative_path in sorted(stale_files):
            target_path = config_manager.cloudsave_dir / relative_path
            if target_path.exists():
                target_path.unlink()
                _cleanup_empty_parent_dirs(target_path, config_manager.cloudsave_dir)

        save_cloudsave_manifest(config_manager, manifest)

        cloud_state["next_sequence_number"] = sequence_number + 1
        cloud_state["last_successful_export_at"] = exported_at
        config_manager.save_cloudsave_local_state(cloud_state)

        return {
            "manifest": manifest,
            "staged_file_count": len(staged_entries),
            "name_audit": name_audit,
        }


def import_local_cloudsave_snapshot(config_manager) -> dict[str, Any]:
    """Import the current local cloudsave snapshot back into runtime truth with rollback."""
    bootstrap_local_cloudsave_environment(config_manager)
    with cloud_apply_fence(
        config_manager,
        mode=ROOT_MODE_BOOTSTRAP_IMPORTING,
        reason="local_cloudsave_import",
    ):
        manifest = load_cloudsave_manifest(config_manager)
        manifest_files = manifest.get("files") or {}
        if not isinstance(manifest_files, dict) or not manifest_files:
            raise ValueError("cloudsave manifest does not contain any staged files")

        stage_root = _create_staging_workspace(config_manager, "import")
        staged_entries: dict[str, Path] = {}
        for relative_path in sorted(manifest_files):
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

        try:
            for target_path, staged_path in runtime_targets.items():
                _atomic_copy_file(staged_path, target_path)

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
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_path, target_path)
            raise


def load_cloudsave_manifest(config_manager, default_value: dict[str, Any] | None = None) -> dict[str, Any]:
    if default_value is None:
        cloud_state = config_manager.load_cloudsave_local_state()
        default_value = build_default_cloudsave_manifest(client_id=cloud_state.get("client_id", ""))
    return config_manager._load_json_file(config_manager.cloudsave_manifest_path, default_value)


def save_cloudsave_manifest(config_manager, data: dict[str, Any]) -> None:
    config_manager.ensure_cloudsave_structure()
    atomic_write_json(config_manager.cloudsave_manifest_path, data, ensure_ascii=False, indent=2)


def ensure_cloudsave_manifest(config_manager) -> dict[str, Any]:
    config_manager.ensure_cloudsave_structure()
    cloud_state = config_manager.load_cloudsave_local_state()
    manifest = load_cloudsave_manifest(
        config_manager,
        default_value=build_default_cloudsave_manifest(client_id=cloud_state.get("client_id", "")),
    )
    changed = False
    if manifest.get("client_id") != cloud_state.get("client_id", ""):
        manifest["client_id"] = cloud_state.get("client_id", "")
        changed = True
    if "schema_version" not in manifest:
        manifest["schema_version"] = 1
        changed = True
    if "min_reader_schema_version" not in manifest:
        manifest["min_reader_schema_version"] = 1
        changed = True
    if "min_app_version" not in manifest:
        manifest["min_app_version"] = ""
        changed = True
    if "device_id" not in manifest:
        manifest["device_id"] = ""
        changed = True
    if "sequence_number" not in manifest:
        manifest["sequence_number"] = 0
        changed = True
    if "exported_at_utc" not in manifest:
        manifest["exported_at_utc"] = ""
        changed = True
    if "files" not in manifest or not isinstance(manifest.get("files"), dict):
        manifest["files"] = {}
        changed = True
    if "fingerprint" not in manifest:
        manifest["fingerprint"] = ""
        changed = True
    if changed or not config_manager.cloudsave_manifest_path.exists():
        save_cloudsave_manifest(config_manager, manifest)
    return manifest


def bootstrap_local_cloudsave_environment(config_manager) -> dict[str, Any]:
    """Initialize phase-0 local cloudsave skeleton and state files."""
    legacy_import = import_legacy_runtime_root_if_needed(config_manager)
    if not config_manager.ensure_cloudsave_structure():
        raise OSError("failed to ensure cloudsave directory structure")

    config_manager.ensure_cloudsave_state_files()

    root_state = config_manager.load_root_state()
    root_state, recovered_stale_mode = _recover_stale_write_blocking_mode(config_manager, root_state)
    root_changed = False
    app_root = str(config_manager.app_docs_dir)
    if root_state.get("current_root") != app_root:
        root_state["current_root"] = app_root
        root_changed = True
    if not root_state.get("last_known_good_root"):
        root_state["last_known_good_root"] = app_root
        root_changed = True
    if not root_state.get("last_successful_boot_at"):
        root_state["last_successful_boot_at"] = ""
        root_changed = True
    if legacy_import.get("source"):
        root_state["last_migration_source"] = str(legacy_import["source"])
        root_state["last_migration_result"] = str(legacy_import.get("result") or "")
        root_changed = True
        if legacy_import.get("backup_path"):
            root_state["last_migration_backup"] = str(legacy_import["backup_path"])
            root_changed = True
    elif recovered_stale_mode:
        root_changed = True
    elif not root_state.get("last_migration_result"):
        root_state["last_migration_result"] = str(legacy_import.get("result") or "bootstrap_initialized")
        root_changed = True
    if root_changed:
        config_manager.save_root_state(root_state)

    cloud_state = config_manager.load_cloudsave_local_state()
    cloud_changed = False
    if not cloud_state.get("client_id"):
        cloud_state["client_id"] = config_manager.build_default_cloudsave_local_state()["client_id"]
        cloud_changed = True
    next_seq = int(cloud_state.get("next_sequence_number") or 0)
    if next_seq < 1:
        cloud_state["next_sequence_number"] = 1
        cloud_changed = True
    if cloud_changed:
        config_manager.save_cloudsave_local_state(cloud_state)

    manifest = ensure_cloudsave_manifest(config_manager)
    return {
        "root_state": config_manager.load_root_state(),
        "cloudsave_local_state": config_manager.load_cloudsave_local_state(),
        "manifest": manifest,
        "legacy_import": legacy_import,
    }


def get_root_state(config_manager) -> dict[str, Any]:
    return config_manager.load_root_state()


def get_root_mode(config_manager) -> str:
    state = get_root_state(config_manager)
    return str(state.get("mode") or ROOT_MODE_NORMAL)


def set_root_mode(config_manager, mode: str, **updates: Any) -> dict[str, Any]:
    state = get_root_state(config_manager)
    state["mode"] = str(mode or ROOT_MODE_NORMAL)
    for key, value in updates.items():
        if value is not None:
            state[key] = value
    config_manager.save_root_state(state)
    return state


def is_write_fence_active(config_manager) -> bool:
    return get_root_mode(config_manager) in WRITE_BLOCKING_MODES


def assert_cloudsave_writable(config_manager, *, operation: str = "write", target: str = "") -> None:
    mode = get_root_mode(config_manager)
    if mode in WRITE_BLOCKING_MODES:
        raise MaintenanceModeError(mode, operation=operation, target=target)


def maintenance_error_payload(exc: MaintenanceModeError) -> dict[str, Any]:
    return {
        "success": False,
        "error": exc.code,
        "code": exc.code,
        "mode": exc.mode,
        "operation": exc.operation,
        "target": exc.target,
        "retryable": True,
    }


def scan_for_sensitive_values(payload: Any, *, path: str = "$") -> list[str]:
    """Scan nested payloads for obviously sensitive key/value markers."""
    findings: list[str] = []

    if isinstance(payload, dict):
        for key, value in payload.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if any(token in key_lower for token in SENSITIVE_TOKENS):
                findings.append(f"{path}.{key_str}")
            findings.extend(scan_for_sensitive_values(value, path=f"{path}.{key_str}"))
        return findings

    if isinstance(payload, list):
        for index, item in enumerate(payload):
            findings.extend(scan_for_sensitive_values(item, path=f"{path}[{index}]"))
        return findings

    if isinstance(payload, str):
        value_lower = payload.lower()
        if any(token in value_lower for token in SENSITIVE_TOKENS):
            findings.append(path)
    return findings


def _cloud_apply_mutex_name(config_manager) -> str:
    digest = hashlib.sha1(str(config_manager.app_docs_dir).encode("utf-8")).hexdigest()[:12]
    return rf"Global\NEKO_CLOUD_APPLY_LOCK_{digest}"


def acquire_cloud_apply_lock(config_manager) -> bool:
    """Acquire the cross-process cloud apply lock used by maintenance mode."""
    global _cloud_apply_lock_handle, _cloud_apply_lock_file

    config_manager.ensure_local_state_directory()
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            ERROR_ALREADY_EXISTS = 183
            handle = kernel32.CreateMutexW(None, True, _cloud_apply_mutex_name(config_manager))
            last_err = kernel32.GetLastError()
            if handle != 0:
                if last_err != ERROR_ALREADY_EXISTS:
                    _cloud_apply_lock_handle = handle
                    return True
                kernel32.CloseHandle(handle)
                return False
            return False
        except Exception:
            return True

    try:
        import fcntl

        lock_path = config_manager.local_state_dir / "cloud_apply.lock"
        lock_file = open(lock_path, "w")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            lock_file.close()
            return False
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        _cloud_apply_lock_file = lock_file
        return True
    except Exception:
        return True


def release_cloud_apply_lock(config_manager) -> None:
    global _cloud_apply_lock_handle, _cloud_apply_lock_file

    if sys.platform == "win32":
        if _cloud_apply_lock_handle is None:
            return
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.ReleaseMutex(_cloud_apply_lock_handle)
            kernel32.CloseHandle(_cloud_apply_lock_handle)
        except Exception:
            pass
        _cloud_apply_lock_handle = None
        return

    if _cloud_apply_lock_file is None:
        return
    try:
        import fcntl

        fcntl.flock(_cloud_apply_lock_file.fileno(), fcntl.LOCK_UN)
        _cloud_apply_lock_file.close()
    except Exception:
        pass
    _cloud_apply_lock_file = None
    try:
        os.unlink(config_manager.local_state_dir / "cloud_apply.lock")
    except Exception:
        pass


def _process_holds_cloud_apply_lock() -> bool:
    return _cloud_apply_lock_handle is not None or _cloud_apply_lock_file is not None


def _recover_stale_write_blocking_mode(config_manager, root_state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    current_mode = str(root_state.get("mode") or ROOT_MODE_NORMAL)
    if current_mode not in WRITE_BLOCKING_MODES:
        return root_state, False

    if _process_holds_cloud_apply_lock():
        return root_state, False

    if not acquire_cloud_apply_lock(config_manager):
        return root_state, False

    try:
        recovered_state = dict(root_state)
        recovered_state["mode"] = ROOT_MODE_NORMAL
        recovered_state["last_migration_result"] = f"recovered_stale_mode:{current_mode}"
        config_manager.save_root_state(recovered_state)
        return recovered_state, True
    finally:
        release_cloud_apply_lock(config_manager)


@contextmanager
def cloud_apply_fence(config_manager, *, mode: str = ROOT_MODE_MAINTENANCE_READONLY, reason: str = ""):
    """Acquire the global cloud apply lock and switch root_state into maintenance."""
    previous_state = get_root_state(config_manager)
    previous_mode = str(previous_state.get("mode") or ROOT_MODE_NORMAL)
    if not acquire_cloud_apply_lock(config_manager):
        raise MaintenanceModeError(
            get_root_mode(config_manager),
            operation="acquire_lock",
            target="cloud_apply_lock",
        )
    try:
        set_root_mode(
            config_manager,
            mode,
            last_migration_result=reason or previous_state.get("last_migration_result", ""),
        )
        yield get_root_state(config_manager)
    finally:
        try:
            set_root_mode(config_manager, previous_mode)
        finally:
            release_cloud_apply_lock(config_manager)
