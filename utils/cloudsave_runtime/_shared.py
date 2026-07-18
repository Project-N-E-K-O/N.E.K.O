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

"""Shared constants, exception types, availability gates, character-name
auditing and sensitive-value scanning for the cloudsave runtime package.

Split out of the former monolithic ``utils/cloudsave_runtime.py``.
"""

from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any

from utils.character_name import PROFILE_NAME_MAX_UNITS, validate_character_name


# Keep the historical logger name of the pre-split monolithic module so
# existing logging configuration and log filtering keep working.
logger = logging.getLogger("utils.cloudsave_runtime")


ROOT_MODE_NORMAL = "normal"


ROOT_MODE_BOOTSTRAP_IMPORTING = "bootstrap_importing"


ROOT_MODE_BOOTSTRAP_READONLY = "bootstrap_readonly"


ROOT_MODE_DEFERRED_INIT = "deferred_init"


ROOT_MODE_MAINTENANCE_READONLY = "maintenance_readonly"


CLOUDSAVE_DISABLED_ENV = "NEKO_CLOUDSAVE_DISABLED"


CLOUDSAVE_DISABLED_LOCAL_STATE_UNAVAILABLE = "local_state_unavailable"


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


SENSITIVE_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "cookies",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "session_token",
        "auth_token",
        "bearer_token",
        "sessionid",
        "session_id",
    }
)


SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9._-]{12,}\b"),
    re.compile(r"\bbearer\s+[A-Za-z0-9._-]{12,}\b", re.IGNORECASE),
    re.compile(r"\b(?:api[_\-\s]*key|authorization|cookie|token)\s*[:=]\s*[^\s]{8,}\b", re.IGNORECASE),
)


GLOBAL_CONVERSATION_KEY = "__global_conversation__"


MANAGED_MEMORY_FILENAMES = (
    "recent.json",
    "settings.json",
    "facts.json",
    "facts_archive.json",
    # 外部导入逐日幂等 sidecar（可选：仅导入过、且有无 fact 载体天的角色才有）。
    # 必须与 facts.json 同处一个 cloudsave 同步/回滚单元：sidecar 记的是空抽取/
    # 全去重天的 processed 指纹，若它随云同步而 facts 回滚（或反之）会与账本失配，
    # 故一起 hash/上传/删除/恢复（缺失文件在各遍历处 is_file/exists 判断跳过）。
    "external_import_state.json",
    "persona.json",
    "persona_corrections.json",
    "reflections.json",
    "reflections_archive.json",
    "surfaced.json",
    "time_indexed.db",
)


MANAGED_CLOUDSAVE_PREFIXES = (
    "characters/",
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
    "card_faces",
    "cloudsave",
    "cloudsave_backups",
    ".cloudsave_staging",
)


NON_RUNTIME_CONTENT_DIR_NAMES = {
    "cloudsave",
    "cloudsave_backups",
    ".cloudsave_staging",
}


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
    "card_faces",
)


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


class CloudsaveOperationError(RuntimeError):
    """Raised when a single-character cloudsave operation cannot proceed safely."""

    def __init__(self, code: str, message: str, *, character_name: str = ""):
        self.code = str(code or "CLOUDSAVE_OPERATION_FAILED")
        self.character_name = str(character_name or "")
        super().__init__(message)


class CloudsaveDeadlineExceeded(RuntimeError):
    """Raised when a cloudsave job exceeds its pre-apply time budget."""

    def __init__(self, operation: str, stage: str):
        self.operation = str(operation or "cloudsave")
        self.stage = str(stage or "unknown")
        self.code = "CLOUDSAVE_DEADLINE_EXCEEDED"
        super().__init__(f"{self.operation} exceeded deadline before stage={self.stage}")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _assert_deadline_not_exceeded(
    deadline_monotonic: float | None,
    *,
    operation: str,
    stage: str,
) -> None:
    if deadline_monotonic is None:
        return
    if time.monotonic() <= float(deadline_monotonic):
        return
    raise CloudsaveDeadlineExceeded(operation=operation, stage=stage)


def is_cloudsave_provider_available(config_manager) -> bool:
    """Centralize provider availability so future remote probes only need one hook."""
    if is_cloudsave_disabled():
        return False
    override = getattr(config_manager, "cloudsave_provider_available", None)
    if override is None:
        return True
    return bool(override)


def cloudsave_disabled_reason() -> str:
    return str(os.environ.get(CLOUDSAVE_DISABLED_ENV) or "").strip()


def is_cloudsave_disabled() -> bool:
    return bool(cloudsave_disabled_reason())


def is_cloudsave_disabled_due_to_local_state_unavailable() -> bool:
    return cloudsave_disabled_reason() == CLOUDSAVE_DISABLED_LOCAL_STATE_UNAVAILABLE


def _raise_cloudsave_disabled(operation: str, *, character_name: str = "") -> None:
    reason = cloudsave_disabled_reason() or "unknown"
    raise CloudsaveOperationError(
        "CLOUDSAVE_PROVIDER_UNAVAILABLE",
        f"Cloudsave is disabled for this session ({reason}); skipped {operation}.",
        character_name=character_name,
    )


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
            # Cloudsave paths legitimately use names like "N.E.K.O" in both
            # directory names and legacy "*.json" mirrors. Keep the broader
            # filesystem safety checks, but allow embedded dots here.
            allow_dots=True,
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


def _ensure_local_state_directory_or_raise(config_manager, context: str) -> None:
    if config_manager.ensure_local_state_directory():
        return
    if hasattr(config_manager, "_raise_local_state_directory_error"):
        config_manager._raise_local_state_directory_error(context)
    diagnostic = getattr(config_manager, "_last_local_state_directory_error", None)
    if diagnostic is not None:
        raise diagnostic
    raise OSError("failed to ensure local state directory")


def scan_for_sensitive_values(payload: Any, *, path: str = "$") -> list[str]:
    """Scan nested payloads for obviously sensitive key/value markers."""
    findings: list[str] = []

    if isinstance(payload, dict):
        for key, value in payload.items():
            key_str = str(key)
            normalized_key = re.sub(r"[\s\-]+", "_", key_str.strip().lower())
            normalized_key = re.sub(r"_+", "_", normalized_key).strip("_")
            if normalized_key in SENSITIVE_KEY_NAMES:
                findings.append(f"{path}.{key_str}")
            findings.extend(scan_for_sensitive_values(value, path=f"{path}.{key_str}"))
        return findings

    if isinstance(payload, list):
        for index, item in enumerate(payload):
            findings.extend(scan_for_sensitive_values(item, path=f"{path}[{index}]"))
        return findings

    if isinstance(payload, str):
        value = payload.strip()
        if any(pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS):
            findings.append(path)
    return findings


# Public alias for a helper consumed outside the package
# (``utils/steam_cloud_bundle.py``).
assert_deadline_not_exceeded = _assert_deadline_not_exceeded
