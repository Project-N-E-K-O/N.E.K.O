from __future__ import annotations

import time
from typing import Any, Dict, Optional

from plugin.server.requests.typing import SendResponse
from plugin.server.runs.manager import get_run, update_run_from_plugin


async def handle_run_update(request: Dict[str, Any], send_response: SendResponse) -> None:
    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    if not isinstance(from_plugin, str) or not from_plugin:
        return
    if not isinstance(request_id, str) or not request_id:
        return

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

    run_id = request.get("run_id")
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

    patch: Dict[str, Any] = {}

    status = request.get("status")
    if isinstance(status, str) and status.strip():
        patch["status"] = status.strip()

    progress = request.get("progress")
    if progress is not None:
        try:
            patch["progress"] = float(progress)
        except Exception:
            _send_error(code="INVALID_ARGUMENT", message="invalid progress")
            return

    step = request.get("step")
    if step is not None:
        try:
            patch["step"] = int(step)
        except Exception:
            _send_error(code="INVALID_ARGUMENT", message="invalid step")
            return

    step_total = request.get("step_total")
    if step_total is not None:
        try:
            patch["step_total"] = int(step_total)
        except Exception:
            _send_error(code="INVALID_ARGUMENT", message="invalid step_total")
            return

    stage = request.get("stage")
    if isinstance(stage, str):
        patch["stage"] = stage

    message = request.get("message")
    if isinstance(message, str):
        patch["message"] = message

    eta_seconds = request.get("eta_seconds")
    if eta_seconds is not None:
        try:
            patch["eta_seconds"] = float(eta_seconds)
        except Exception:
            _send_error(code="INVALID_ARGUMENT", message="invalid eta_seconds")
            return

    metrics = request.get("metrics")
    if isinstance(metrics, dict):
        patch["metrics"] = metrics

    now = float(time.time())
    try:
        updated, applied = update_run_from_plugin(from_plugin=from_plugin, run_id=rid, patch=patch)
        if updated is None:
            _send_error(code="RUN_NOT_FOUND", message="run not found", details={"run_id": rid})
            return
        send_response(
            from_plugin,
            request_id,
            {"ok": True, "applied": bool(applied), "run_id": updated.run_id, "status": updated.status, "updated_at": now},
            None,
            timeout=float(timeout),
        )
    except Exception as e:
        _send_error(code="INTERNAL_ERROR", message=str(e))
