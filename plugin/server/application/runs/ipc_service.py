from __future__ import annotations

import base64
import binascii
import time
import uuid
from collections.abc import Mapping

from pydantic import ValidationError

from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.server.runs.manager import ExportItem, get_run, update_run_from_plugin
from plugin.server.runs.manager import append_export_item as manager_append_export_item
from plugin.settings import EXPORT_INLINE_BINARY_MAX_BYTES

logger = get_logger("server.application.runs.ipc")

_KNOWN_RUNTIME_ERRORS = (
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    TimeoutError,
)

_SUPPORTED_EXPORT_TYPES = ("text", "json", "url", "binary", "binary_url")


def _to_domain_error(
    *,
    code: str,
    message: str,
    status_code: int,
    details: dict[str, object] | None = None,
) -> ServerDomainError:
    return ServerDomainError(
        code=code,
        message=message,
        status_code=status_code,
        details=details or {},
    )


def _normalize_run_id(run_id: object) -> str:
    if not isinstance(run_id, str) or not run_id.strip():
        raise _to_domain_error(
            code="INVALID_ARGUMENT",
            message="run_id is required",
            status_code=400,
        )
    return run_id.strip()


def _normalize_export_type(export_type: object) -> str:
    if not isinstance(export_type, str) or not export_type.strip():
        raise _to_domain_error(
            code="INVALID_ARGUMENT",
            message="export_type is required",
            status_code=400,
        )
    normalized = export_type.strip()
    if normalized not in _SUPPORTED_EXPORT_TYPES:
        raise _to_domain_error(
            code="INVALID_ARGUMENT",
            message="unsupported export_type",
            status_code=400,
            details={"export_type": normalized, "allowed": list(_SUPPORTED_EXPORT_TYPES)},
        )
    return normalized


def _normalize_metadata(metadata: object) -> dict[str, object]:
    if not isinstance(metadata, Mapping):
        return {}
    normalized: dict[str, object] = {}
    for key_obj, value in metadata.items():
        if isinstance(key_obj, (bytes, bytearray)):
            try:
                key = bytes(key_obj).decode("utf-8")
            except UnicodeDecodeError:
                key = key_obj.decode("utf-8", errors="ignore")
            normalized[key] = value
            continue
        if isinstance(key_obj, str):
            normalized[key_obj] = value
            continue
        normalized[str(key_obj)] = value
    return normalized


def _ensure_owned_run(*, from_plugin: str, run_id: str):
    run_record = get_run(run_id)
    if run_record is None:
        raise _to_domain_error(
            code="RUN_NOT_FOUND",
            message="run not found",
            status_code=404,
            details={"run_id": run_id},
        )
    if run_record.plugin_id != from_plugin:
        raise _to_domain_error(
            code="FORBIDDEN",
            message="forbidden",
            status_code=403,
            details={"run_id": run_id, "owner_plugin_id": run_record.plugin_id},
        )
    return run_record


def _decode_binary_payload(binary_base64: object) -> bytes:
    if not isinstance(binary_base64, str) or not binary_base64:
        raise _to_domain_error(
            code="INVALID_ARGUMENT",
            message="binary_base64 is required",
            status_code=400,
        )
    try:
        decoded = base64.b64decode(binary_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _to_domain_error(
            code="INVALID_ARGUMENT",
            message="invalid binary_base64",
            status_code=400,
        ) from exc

    max_bytes = EXPORT_INLINE_BINARY_MAX_BYTES
    if max_bytes is None:
        return decoded

    try:
        limit = int(max_bytes)
    except (TypeError, ValueError):
        return decoded

    if limit > 0 and len(decoded) > limit:
        raise _to_domain_error(
            code="PAYLOAD_TOO_LARGE",
            message="binary too large",
            status_code=413,
            details={"max_bytes": limit},
        )
    return decoded


def _coerce_optional_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _normalize_patch(payload: Mapping[str, object]) -> dict[str, object]:
    patch: dict[str, object] = {}

    status_obj = payload.get("status")
    if isinstance(status_obj, str) and status_obj.strip():
        patch["status"] = status_obj.strip()

    progress_obj = payload.get("progress")
    if progress_obj is not None:
        if isinstance(progress_obj, bool):
            raise _to_domain_error(
                code="INVALID_ARGUMENT",
                message="invalid progress",
                status_code=400,
            )
        try:
            patch["progress"] = float(progress_obj)
        except (TypeError, ValueError) as exc:
            raise _to_domain_error(
                code="INVALID_ARGUMENT",
                message="invalid progress",
                status_code=400,
            ) from exc

    step_obj = payload.get("step")
    if step_obj is not None:
        if isinstance(step_obj, bool):
            raise _to_domain_error(
                code="INVALID_ARGUMENT",
                message="invalid step",
                status_code=400,
            )
        try:
            patch["step"] = int(step_obj)
        except (TypeError, ValueError) as exc:
            raise _to_domain_error(
                code="INVALID_ARGUMENT",
                message="invalid step",
                status_code=400,
            ) from exc

    step_total_obj = payload.get("step_total")
    if step_total_obj is not None:
        if isinstance(step_total_obj, bool):
            raise _to_domain_error(
                code="INVALID_ARGUMENT",
                message="invalid step_total",
                status_code=400,
            )
        try:
            patch["step_total"] = int(step_total_obj)
        except (TypeError, ValueError) as exc:
            raise _to_domain_error(
                code="INVALID_ARGUMENT",
                message="invalid step_total",
                status_code=400,
            ) from exc

    stage_obj = payload.get("stage")
    if isinstance(stage_obj, str):
        patch["stage"] = stage_obj

    message_obj = payload.get("message")
    if isinstance(message_obj, str):
        patch["message"] = message_obj

    eta_seconds_obj = payload.get("eta_seconds")
    if eta_seconds_obj is not None:
        if isinstance(eta_seconds_obj, bool):
            raise _to_domain_error(
                code="INVALID_ARGUMENT",
                message="invalid eta_seconds",
                status_code=400,
            )
        try:
            patch["eta_seconds"] = float(eta_seconds_obj)
        except (TypeError, ValueError) as exc:
            raise _to_domain_error(
                code="INVALID_ARGUMENT",
                message="invalid eta_seconds",
                status_code=400,
            ) from exc

    metrics_obj = payload.get("metrics")
    if isinstance(metrics_obj, Mapping):
        metrics: dict[str, object] = {}
        for key_obj, value in metrics_obj.items():
            if not isinstance(key_obj, str):
                raise _to_domain_error(
                    code="INVALID_ARGUMENT",
                    message="invalid metrics",
                    status_code=400,
                )
            metrics[key_obj] = value
        patch["metrics"] = metrics

    return patch


def _map_run_update_runtime_error(error: RuntimeError, *, run_id: str) -> ServerDomainError:
    message = str(error)
    if message == "forbidden":
        return _to_domain_error(
            code="FORBIDDEN",
            message="forbidden",
            status_code=403,
            details={"run_id": run_id},
        )
    if (
        message.startswith("invalid")
        or message in {"stage too long", "message too long"}
    ):
        return _to_domain_error(
            code="INVALID_ARGUMENT",
            message=message,
            status_code=400,
            details={"run_id": run_id},
        )
    return _to_domain_error(
        code="RUN_UPDATE_FAILED",
        message=message or "run update failed",
        status_code=500,
        details={"run_id": run_id},
    )


class RunIpcService:
    def push_export(self, *, from_plugin: str, payload: Mapping[str, object]) -> dict[str, object]:
        run_id = _normalize_run_id(payload.get("run_id"))
        _ensure_owned_run(from_plugin=from_plugin, run_id=run_id)
        export_type = _normalize_export_type(payload.get("export_type"))

        text = payload.get("text")
        json_data = payload.get("json")
        url = payload.get("url")
        binary_base64 = payload.get("binary_base64")
        binary_url = payload.get("binary_url")

        if export_type == "text" and not isinstance(text, str):
            raise _to_domain_error(
                code="INVALID_ARGUMENT",
                message="text is required",
                status_code=400,
            )
        if export_type == "json" and json_data is None:
            raise _to_domain_error(
                code="INVALID_ARGUMENT",
                message="json is required",
                status_code=400,
            )
        if export_type == "url" and (not isinstance(url, str) or not url.strip()):
            raise _to_domain_error(
                code="INVALID_ARGUMENT",
                message="url is required",
                status_code=400,
            )
        if export_type == "binary_url" and (not isinstance(binary_url, str) or not binary_url.strip()):
            raise _to_domain_error(
                code="INVALID_ARGUMENT",
                message="binary_url is required",
                status_code=400,
            )
        if export_type == "binary":
            _decode_binary_payload(binary_base64)

        export_item_id = str(uuid.uuid4())
        item_kwargs: dict[str, object] = {
            "export_item_id": export_item_id,
            "run_id": run_id,
            "type": export_type,
            "created_at": float(time.time()),
            "label": _coerce_optional_str(payload.get("label")),
            "description": _coerce_optional_str(payload.get("description")),
            "mime": _coerce_optional_str(payload.get("mime")),
            "metadata": _normalize_metadata(payload.get("metadata")),
        }

        if export_type == "text":
            item_kwargs["text"] = text
        elif export_type == "json":
            item_kwargs["json"] = json_data
        elif export_type == "url":
            item_kwargs["url"] = url
        elif export_type == "binary_url":
            item_kwargs["binary_url"] = binary_url
        elif export_type == "binary":
            item_kwargs["binary"] = binary_base64

        try:
            item = ExportItem.model_validate(item_kwargs)
            manager_append_export_item(item)
        except ValidationError as exc:
            raise _to_domain_error(
                code="INVALID_ARGUMENT",
                message="invalid export payload",
                status_code=400,
                details={"error_type": type(exc).__name__},
            ) from exc
        except _KNOWN_RUNTIME_ERRORS as exc:
            logger.error(
                "push_export failed: plugin_id={}, run_id={}, err_type={}, err={}",
                from_plugin,
                run_id,
                type(exc).__name__,
                str(exc),
            )
            raise _to_domain_error(
                code="EXPORT_PUSH_FAILED",
                message="failed to push export item",
                status_code=500,
                details={"run_id": run_id, "error_type": type(exc).__name__},
            ) from exc

        return {"export_item_id": export_item_id}

    def update_run(self, *, from_plugin: str, payload: Mapping[str, object]) -> dict[str, object]:
        run_id = _normalize_run_id(payload.get("run_id"))
        _ensure_owned_run(from_plugin=from_plugin, run_id=run_id)
        patch = _normalize_patch(payload)

        now = float(time.time())
        try:
            updated, applied = update_run_from_plugin(
                from_plugin=from_plugin,
                run_id=run_id,
                patch=patch,
            )
        except RuntimeError as exc:
            raise _map_run_update_runtime_error(exc, run_id=run_id) from exc
        except _KNOWN_RUNTIME_ERRORS as exc:
            logger.error(
                "update_run failed: plugin_id={}, run_id={}, err_type={}, err={}",
                from_plugin,
                run_id,
                type(exc).__name__,
                str(exc),
            )
            raise _to_domain_error(
                code="RUN_UPDATE_FAILED",
                message="failed to update run",
                status_code=500,
                details={"run_id": run_id, "error_type": type(exc).__name__},
            ) from exc

        if updated is None:
            raise _to_domain_error(
                code="RUN_NOT_FOUND",
                message="run not found",
                status_code=404,
                details={"run_id": run_id},
            )

        return {
            "ok": True,
            "applied": bool(applied),
            "run_id": updated.run_id,
            "status": updated.status,
            "updated_at": now,
        }
