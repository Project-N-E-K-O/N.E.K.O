from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_bytes

from ._mutation_lock import mutation_lock
from ._strict_file import read_bounded_regular_file
from .pack_jobs import trusted_live_root


REMOVAL_OPERATIONS_NAME = "pack-remove-operations.json"
MAX_REMOVAL_OPERATIONS_BYTES = 512 * 1024
MAX_TERMINAL_REMOVAL_OPERATIONS = 100
MAX_PENDING_REMOVAL_OPERATIONS = 32
PENDING_REMOVAL_OPERATION_TTL_SECONDS = 24 * 60 * 60
_OPERATION_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_STATUSES = frozenset(("pending", "committed", "failed"))


class KnowledgeRemovalOperationError(RuntimeError):
    pass


def validate_removal_operation_id(value: object) -> str:
    operation_id = str(value or "").strip()
    if not _OPERATION_ID.fullmatch(operation_id):
        raise ValueError("invalid knowledge removal operation id")
    return operation_id


def begin_removal_operation(
    knowledge_root: str | Path,
    operation_id: str,
    request: dict[str, str],
) -> dict[str, Any]:
    operation_id = validate_removal_operation_id(operation_id)
    canonical_request = _canonical_request(request)
    path = _operations_path(knowledge_root)
    with mutation_lock(path):
        payload = _load_operations_for_access(path)
        operations = payload["operations"]
        existing = operations.get(operation_id)
        if existing is not None:
            if existing["request"] != canonical_request:
                raise KnowledgeRemovalOperationError(
                    "knowledge_removal_operation_identity_mismatch"
                )
            if existing["status"] == "pending":
                existing = {
                    **existing,
                    "attempts": int(existing["attempts"]) + 1,
                    "updated_at": time.time(),
                }
                operations[operation_id] = existing
                _write_operations(path, payload)
            return dict(existing)
        now = time.time()
        record = {
            "operation_id": operation_id,
            "request": canonical_request,
            "status": "pending",
            "result": None,
            "attempts": 1,
            "created_at": now,
            "updated_at": now,
        }
        operations[operation_id] = record
        _write_operations(path, payload)
        return dict(record)


def complete_removal_operation(
    knowledge_root: str | Path,
    operation_id: str,
    *,
    status: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    operation_id = validate_removal_operation_id(operation_id)
    if status not in {"committed", "failed"}:
        raise ValueError("invalid knowledge removal operation status")
    path = _operations_path(knowledge_root)
    with mutation_lock(path):
        payload = _load_operations_for_access(path)
        record = payload["operations"].get(operation_id)
        if record is None:
            raise KnowledgeRemovalOperationError(
                "knowledge_removal_operation_unknown"
            )
        if record["status"] != "pending":
            return dict(record)
        record = {
            **record,
            "status": status,
            "result": dict(result),
            "updated_at": time.time(),
        }
        payload["operations"][operation_id] = record
        _write_operations(path, payload)
        return dict(record)


def get_removal_operation(
    knowledge_root: str | Path,
    operation_id: str,
) -> dict[str, Any] | None:
    operation_id = validate_removal_operation_id(operation_id)
    path = _operations_path(knowledge_root)
    with mutation_lock(path):
        record = _load_operations_for_access(path)["operations"].get(operation_id)
        return dict(record) if record is not None else None


def _operations_path(knowledge_root: str | Path) -> Path:
    root = trusted_live_root(Path(knowledge_root))
    if root is None:
        raise KnowledgeRemovalOperationError("knowledge_root_untrusted")
    return root / REMOVAL_OPERATIONS_NAME


def _canonical_request(request: dict[str, str]) -> dict[str, str]:
    expected = (
        "pack_id",
        "expected_provider",
        "expected_provider_package_id",
        "expected_remote_id",
    )
    if set(request) != set(expected):
        raise ValueError("invalid knowledge removal operation request")
    canonical = {key: str(request[key]) for key in expected}
    if not canonical["pack_id"]:
        raise ValueError("invalid knowledge removal operation request")
    return canonical


def _load_operations(path: Path) -> dict[str, Any]:
    try:
        raw = read_bounded_regular_file(
            path,
            max_bytes=MAX_REMOVAL_OPERATIONS_BYTES,
        )
    except FileNotFoundError:
        return {"schema_version": 1, "operations": {}}
    except (OSError, ValueError) as exc:
        raise KnowledgeRemovalOperationError(
            "knowledge_removal_operation_registry_invalid"
        ) from exc
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgeRemovalOperationError(
            "knowledge_removal_operation_registry_invalid"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise KnowledgeRemovalOperationError(
            "knowledge_removal_operation_registry_invalid"
        )
    operations = payload.get("operations")
    if not isinstance(operations, dict):
        raise KnowledgeRemovalOperationError(
            "knowledge_removal_operation_registry_invalid"
        )
    validated: dict[str, dict[str, Any]] = {}
    for key, value in operations.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise KnowledgeRemovalOperationError(
                "knowledge_removal_operation_registry_invalid"
            )
        try:
            operation_id = validate_removal_operation_id(key)
            request = _canonical_request(value.get("request", {}))
        except (TypeError, ValueError) as exc:
            raise KnowledgeRemovalOperationError(
                "knowledge_removal_operation_registry_invalid"
            ) from exc
        status = value.get("status")
        result = value.get("result")
        if (
            operation_id != value.get("operation_id")
            or status not in _STATUSES
            or (status == "pending" and result is not None)
            or (status != "pending" and not isinstance(result, dict))
            or not _is_finite_timestamp(value.get("created_at"))
            or not _is_finite_timestamp(value.get("updated_at"))
            or isinstance(value.get("attempts"), bool)
            or not isinstance(value.get("attempts"), int)
            or int(value["attempts"]) < 1
        ):
            raise KnowledgeRemovalOperationError(
                "knowledge_removal_operation_registry_invalid"
            )
        validated[key] = {
            "operation_id": operation_id,
            "request": request,
            "status": status,
            "result": dict(result) if isinstance(result, dict) else None,
            "attempts": int(value["attempts"]),
            "created_at": float(value["created_at"]),
            "updated_at": float(value["updated_at"]),
        }
    return {"schema_version": 1, "operations": validated}


def _is_finite_timestamp(value: object) -> bool:
    # json.loads accepts NaN/Infinity, but _write_operations refuses them
    # (allow_nan=False), and a NaN updated_at never expires. Reject on load so
    # the record is reported as an invalid registry instead of breaking writes.
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _load_operations_for_access(path: Path) -> dict[str, Any]:
    """Load and durably expire stale pending records under the caller's lock."""
    payload = _load_operations(path)
    if _expire_stale_pending_operations(payload):
        _write_operations(path, payload)
    return payload


def _expire_stale_pending_operations(payload: dict[str, Any]) -> bool:
    now = time.time()
    changed = False
    operations = payload["operations"]
    for operation_id, record in tuple(operations.items()):
        if (
            record["status"] == "pending"
            and now - float(record["updated_at"])
            > PENDING_REMOVAL_OPERATION_TTL_SECONDS
        ):
            operations[operation_id] = {
                **record,
                "status": "failed",
                "result": {"ok": False, "reason": "removal_operation_expired"},
                "updated_at": now,
            }
            changed = True
    return changed


def _write_operations(path: Path, payload: dict[str, Any]) -> None:
    _expire_stale_pending_operations(payload)
    operations = payload["operations"]
    pending_count = sum(
        record["status"] == "pending" for record in operations.values()
    )
    if pending_count > MAX_PENDING_REMOVAL_OPERATIONS:
        raise KnowledgeRemovalOperationError(
            "knowledge_removal_operation_registry_full"
        )
    terminal = sorted(
        (
            (operation_id, record)
            for operation_id, record in operations.items()
            if record["status"] != "pending"
        ),
        key=lambda item: (float(item[1]["updated_at"]), item[0]),
        reverse=True,
    )
    retained_terminal = {
        operation_id
        for operation_id, _record in terminal[:MAX_TERMINAL_REMOVAL_OPERATIONS]
    }
    retained = {
        operation_id: record
        for operation_id, record in operations.items()
        if record["status"] == "pending" or operation_id in retained_terminal
    }
    payload["operations"] = retained
    canonical = json.dumps(
        {"schema_version": 1, "operations": retained},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(canonical) > MAX_REMOVAL_OPERATIONS_BYTES:
        raise KnowledgeRemovalOperationError(
            "knowledge_removal_operation_registry_full"
        )
    atomic_write_bytes(path, canonical)
