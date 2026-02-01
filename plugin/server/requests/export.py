from __future__ import annotations

import base64
import time
import uuid
from typing import Any, Dict, Optional

from plugin.server.requests.typing import SendResponse
from plugin.server.runs.manager import ExportItem, append_export_item
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
    text = request.get("text", None)
    url = request.get("url", None)
    binary_base64 = request.get("binary_base64", None)
    binary_url = request.get("binary_url", None)
    mime = request.get("mime", None)
    metadata = request.get("metadata", None)

    if not isinstance(run_id, str) or not run_id.strip():
        send_response(from_plugin, request_id, None, "run_id is required", timeout=float(timeout))
        return
    if not isinstance(export_type, str) or not export_type.strip():
        send_response(from_plugin, request_id, None, "export_type is required", timeout=float(timeout))
        return
    
    et = export_type.strip()
    if et not in ("text", "url", "binary", "binary_url"):
        send_response(from_plugin, request_id, None, "unsupported export_type", timeout=float(timeout))
        return

    decoded_bytes: Optional[bytes] = None
    if et == "text":
        if not isinstance(text, str):
            send_response(from_plugin, request_id, None, "text is required", timeout=float(timeout))
            return
    elif et == "url":
        if not isinstance(url, str) or not url.strip():
            send_response(from_plugin, request_id, None, "url is required", timeout=float(timeout))
            return
    elif et == "binary_url":
        if not isinstance(binary_url, str) or not binary_url.strip():
            send_response(from_plugin, request_id, None, "binary_url is required", timeout=float(timeout))
            return
    elif et == "binary":
        if not isinstance(binary_base64, str) or not binary_base64:
            send_response(from_plugin, request_id, None, "binary_base64 is required", timeout=float(timeout))
            return
        try:
            decoded_bytes = base64.b64decode(binary_base64, validate=True)
        except Exception:
            send_response(from_plugin, request_id, None, "invalid binary_base64", timeout=float(timeout))
            return
        try:
            if EXPORT_INLINE_BINARY_MAX_BYTES is not None and int(EXPORT_INLINE_BINARY_MAX_BYTES) > 0:
                if len(decoded_bytes) > int(EXPORT_INLINE_BINARY_MAX_BYTES):
                    send_response(from_plugin, request_id, None, "binary too large", timeout=float(timeout))
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
        "run_id": str(run_id).strip(),
        "type": et,
        "created_at": created_at,
        "description": str(description) if isinstance(description, str) else None,
        "mime": str(mime) if isinstance(mime, str) and mime else None,
        "metadata": meta_out,
    }

    if et == "text":
        item_kwargs["text"] = text
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
        send_response(from_plugin, request_id, None, str(e), timeout=float(timeout))
