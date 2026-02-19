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

    run_id = request.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        send_response(from_plugin, request_id, None, "run_id is required", timeout=float(timeout))
        return

    rec = get_run(run_id)
    if rec is None:
        send_response(from_plugin, request_id, None, "run not found", timeout=float(timeout))
        return

    if rec.plugin_id != from_plugin:
        send_response(from_plugin, request_id, None, "forbidden", timeout=float(timeout))
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
            send_response(from_plugin, request_id, None, "invalid progress", timeout=float(timeout))
            return

    step = request.get("step")
    if step is not None:
        try:
            patch["step"] = int(step)
        except Exception:
            send_response(from_plugin, request_id, None, "invalid step", timeout=float(timeout))
            return

    step_total = request.get("step_total")
    if step_total is not None:
        try:
            patch["step_total"] = int(step_total)
        except Exception:
            send_response(from_plugin, request_id, None, "invalid step_total", timeout=float(timeout))
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
            send_response(from_plugin, request_id, None, "invalid eta_seconds", timeout=float(timeout))
            return

    metrics = request.get("metrics")
    if isinstance(metrics, dict):
        patch["metrics"] = metrics

    now = float(time.time())
    try:
        updated, applied = update_run_from_plugin(from_plugin=from_plugin, run_id=str(run_id).strip(), patch=patch)
        if updated is None:
            send_response(from_plugin, request_id, None, "run not found", timeout=float(timeout))
            return
        send_response(
            from_plugin,
            request_id,
            {"ok": True, "applied": bool(applied), "run_id": updated.run_id, "status": updated.status, "updated_at": now},
            None,
            timeout=float(timeout),
        )
    except Exception as e:
        send_response(from_plugin, request_id, None, str(e), timeout=float(timeout))
