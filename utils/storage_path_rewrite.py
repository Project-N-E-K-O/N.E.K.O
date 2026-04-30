from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from utils.storage_policy import normalize_runtime_root, paths_equal


WORKSHOP_CONFIG_PATH_FIELDS = (
    "default_workshop_folder",
    "user_mod_folder",
    "user_workshop_folder",
    "steam_workshop_path",
)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _rebase_path_if_under_root(value: Any, *, source_root: Path, target_root: Path) -> Any:
    if value is None:
        return None
    if not isinstance(value, (str, Path)):
        return value

    raw_value = str(value).strip()
    if not raw_value:
        return value

    try:
        original_path = normalize_runtime_root(raw_value)
    except Exception:
        return value

    if paths_equal(original_path, source_root):
        return str(target_root)
    if not _is_relative_to(original_path, source_root):
        if isinstance(value, str) and "\\" in raw_value:
            try:
                slash_normalized_path = normalize_runtime_root(raw_value.replace("\\", "/"))
            except Exception:
                return value
            if paths_equal(slash_normalized_path, source_root):
                return str(target_root)
            if not _is_relative_to(slash_normalized_path, source_root):
                return value
            return str(target_root / slash_normalized_path.relative_to(source_root))
        return value

    return str(target_root / original_path.relative_to(source_root))


def rebase_runtime_bound_workshop_config_paths(
    payload: Any,
    *,
    source_root: Path | str,
    target_root: Path | str,
) -> Any:
    """Rebase workshop config paths that still point inside the old runtime root.

    Storage-root migration intentionally keeps external user-selected paths
    intact. Only paths under the source runtime root are rewritten to the
    corresponding target runtime path.
    """
    if not isinstance(payload, dict):
        return payload

    normalized_source_root = normalize_runtime_root(source_root)
    normalized_target_root = normalize_runtime_root(target_root)
    updated_payload = deepcopy(payload)

    changed = False
    for field_name in WORKSHOP_CONFIG_PATH_FIELDS:
        if field_name not in updated_payload:
            continue
        previous_value = updated_payload.get(field_name)
        next_value = _rebase_path_if_under_root(
            previous_value,
            source_root=normalized_source_root,
            target_root=normalized_target_root,
        )
        if isinstance(next_value, str) and next_value != previous_value:
            updated_payload[field_name] = next_value
            changed = True

    return updated_payload if changed else payload
