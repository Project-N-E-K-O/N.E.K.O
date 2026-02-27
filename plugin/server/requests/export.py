from __future__ import annotations

import base64
import time
import uuid
from typing import Any, Dict, Optional

from plugin.server.requests.typing import SendResponse
from plugin.server.runs.manager import ExportItem, append_export_item, get_run
from plugin.settings import EXPORT_INLINE_BINARY_MAX_BYTES


async def handle_export_push(request: Dict[str, Any], send_response: SendResponse) -> None:
    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    if not isinstance(from_plugin, str) or not from_plugin:
        return
    if not isinstance(request_id, str) or not request_id:
        return

    run_id = request.get("run_id")
    export_type = request.get("export_type")
    description = request.get("description", None)
    label = request.get("label", None)
    text = request.get("text", None)
    json_data = request.get("json", None)
    url = request.get("url", None)
    binary_base64 = request.get("binary_base64", None)
    binary_url = request.get("binary_url", None)
    mime = request.get("mime", None)
    metadata = request.get("metadata", None)

    def _error_payload(*, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "code": str(code),
            "message": str(message),
        }
        if isinstance(details, dict) and details:
            payload["details"] = details
        return payload

    def _send_error(*, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        send_response(
            from_plugin,
            request_id,
            None,
            _error_payload(code=code, message=message, details=details),
            timeout=float(timeout),
        )

    if not isinstance(run_id, str) or not run_id.strip():
        _send_error(code="INVALID_ARGUMENT", message="run_id is required")
        return
    rid = str(run_id).strip()

    rec = get_run(rid)
    if rec is None:
        _send_error(code="RUN_NOT_FOUND", message="run not found", details={"run_id": rid})
        return

    if rec.plugin_id != from_plugin:
        _send_error(
            code="FORBIDDEN",
            message="forbidden",
            details={"run_id": rid, "owner_plugin_id": rec.plugin_id},
        )
        return

    if not isinstance(export_type, str) or not export_type.strip():
        _send_error(code="INVALID_ARGUMENT", message="export_type is required")
        return

    et = export_type.strip()
    if et not in ("text", "json", "url", "binary", "binary_url"):
        _send_error(
            code="INVALID_ARGUMENT",
            message="unsupported export_type",
            details={"export_type": et, "allowed": ["text", "json", "url", "binary", "binary_url"]},
        )
        return

    decoded_bytes: Optional[bytes] = None
    if et == "text":
        if not isinstance(text, str):
            _send_error(code="INVALID_ARGUMENT", message="text is required")
            return
    elif et == "json":
        if json_data is None:
            _send_error(code="INVALID_ARGUMENT", message="json is required")
            return
    elif et == "url":
        if not isinstance(url, str) or not url.strip():
            _send_error(code="INVALID_ARGUMENT", message="url is required")
            return
    elif et == "binary_url":
        if not isinstance(binary_url, str) or not binary_url.strip():
            _send_error(code="INVALID_ARGUMENT", message="binary_url is required")
            return
    elif et == "binary":
        if not isinstance(binary_base64, str) or not binary_base64:
            _send_error(code="INVALID_ARGUMENT", message="binary_base64 is required")
            return
        try:
            decoded_bytes = base64.b64decode(binary_base64, validate=True)
        except Exception:
            _send_error(code="INVALID_ARGUMENT", message="invalid binary_base64")
            return
        try:
            if EXPORT_INLINE_BINARY_MAX_BYTES is not None and int(EXPORT_INLINE_BINARY_MAX_BYTES) > 0:
                if len(decoded_bytes) > int(EXPORT_INLINE_BINARY_MAX_BYTES):
                    _send_error(
                        code="PAYLOAD_TOO_LARGE",
                        message="binary too large",
                        details={"max_bytes": int(EXPORT_INLINE_BINARY_MAX_BYTES)},
                    )
                    return
        except Exception:
            pass

    export_item_id = str(uuid.uuid4())
    created_at = float(time.time())

    meta_out: Dict[str, Any] = {}
    if isinstance(metadata, dict):
        for k, v in metadata.items():
            try:
                kk = k.decode("utf-8") if isinstance(k, (bytes, bytearray)) else str(k)
            except Exception:
                kk = str(k)
            meta_out[kk] = v

    item_kwargs: Dict[str, Any] = {
        "export_item_id": export_item_id,
        "run_id": rid,
        "type": et,
        "created_at": created_at,
        "label": str(label) if isinstance(label, str) else None,
        "description": str(description) if isinstance(description, str) else None,
        "mime": str(mime) if isinstance(mime, str) and mime else None,
        "metadata": meta_out,
    }

    if et == "text":
        item_kwargs["text"] = text
    elif et == "json":
        item_kwargs["json"] = json_data
    elif et == "url":
        item_kwargs["url"] = url
    elif et == "binary_url":
        item_kwargs["binary_url"] = binary_url
    elif et == "binary":
        item_kwargs["binary"] = binary_base64

    try:
        item = ExportItem.model_validate(item_kwargs)
        append_export_item(item)
        send_response(from_plugin, request_id, {"export_item_id": export_item_id}, None, timeout=float(timeout))
    except Exception as e:
        _send_error(code="INTERNAL_ERROR", message=str(e))
