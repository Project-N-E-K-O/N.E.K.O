# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""HTTP and WebSocket boundary for Widget Mode."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from main_logic.widget_mode_runtime import widget_mode_coordinator
from main_routers.shared_state import get_session_manager
from main_routers.system_router import _validate_local_mutation_request

router = APIRouter()
logger = logging.getLogger(__name__)
WIDGET_MODE_BROADCAST_SEND_TIMEOUT_SECONDS = 2.0
_widget_mode_broadcast_tasks: set[asyncio.Task[None]] = set()


async def _send_widget_mode_event(
    name: str,
    ws: Any,
    websocket_lock: asyncio.Lock | None,
    payload: dict[str, Any],
) -> None:
    try:
        async with asyncio.timeout(WIDGET_MODE_BROADCAST_SEND_TIMEOUT_SECONDS):
            if websocket_lock is None:
                await ws.send_json(payload)
            else:
                async with websocket_lock:
                    await ws.send_json(payload)
    except TimeoutError:
        logger.warning("[WidgetMode] broadcast timed out for session %r", name)
    except Exception:
        logger.warning("[WidgetMode] broadcast failed for session %r", name, exc_info=True)


async def broadcast_widget_mode_event(payload: dict[str, Any]) -> int:
    """Schedule bounded sends only for explicitly capable pet sessions."""
    delivered = 0
    try:
        session_manager = get_session_manager()
    except Exception:
        logger.warning("[WidgetMode] broadcast skipped: session manager unavailable", exc_info=True)
        return 0
    for name in list(session_manager.keys()):
        try:
            core = session_manager.get(name)
            if getattr(core, "widget_mode_capable", False) is not True:
                continue
            ws = getattr(core, "websocket", None)
            if ws is None or not hasattr(ws, "send_json"):
                continue
            client_state = getattr(ws, "client_state", None)
            state_name = str(getattr(client_state, "name", client_state)).upper()
            if client_state is not None and state_name != "CONNECTED":
                continue
            task = asyncio.create_task(
                _send_widget_mode_event(
                    name,
                    ws,
                    getattr(core, "websocket_lock", None),
                    payload,
                ),
                name="widget_mode_broadcast",
            )
            _widget_mode_broadcast_tasks.add(task)
            task.add_done_callback(_widget_mode_broadcast_tasks.discard)
            delivered += 1
        except Exception:
            logger.warning("[WidgetMode] broadcast setup failed for session %r", name, exc_info=True)
    return delivered


widget_mode_coordinator.set_event_broadcaster(broadcast_widget_mode_event)


def _validate_widget_mode_mutation(request: Request, payload: dict[str, Any]) -> Any:
    return _validate_local_mutation_request(
        request,
        payload=payload,
        error_defaults={"success": False},
    )


def _coerce_enabled_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


@router.get("/api/widget-mode/state")
async def get_widget_mode_state() -> dict[str, Any]:
    return {"success": True, "state": widget_mode_coordinator.snapshot()}


@router.post("/api/widget-mode/enabled")
async def set_widget_mode_enabled(request: Request, payload: dict[str, Any]) -> Any:
    validation_error = _validate_widget_mode_mutation(request, payload)
    if validation_error is not None:
        return validation_error
    state = await widget_mode_coordinator.set_enabled(_coerce_enabled_flag(payload.get("enabled")))
    return {"success": True, "state": state}


@router.post("/api/widget-mode/user-restore")
async def mark_widget_mode_user_restore(
    request: Request,
    payload: dict[str, Any] | None = None,
) -> Any:
    data = payload or {}
    validation_error = _validate_widget_mode_mutation(request, data)
    if validation_error is not None:
        return validation_error
    pet_instance_id = data.get("pet_instance_id")
    state = await widget_mode_coordinator.mark_user_restore(
        str(pet_instance_id) if pet_instance_id is not None else None,
    )
    return {"success": True, "state": state}


@router.post("/api/widget-mode/windows/register")
async def register_widget_mode_window(request: Request, payload: dict[str, Any]) -> Any:
    validation_error = _validate_widget_mode_mutation(request, payload)
    if validation_error is not None:
        return validation_error
    pet_instance_id = payload.get("pet_instance_id")
    if not isinstance(pet_instance_id, str) or not pet_instance_id.strip():
        raise HTTPException(status_code=400, detail="pet_instance_id required")
    capabilities = payload.get("signal_capabilities")
    if capabilities is not None and not isinstance(capabilities, dict):
        raise HTTPException(status_code=400, detail="signal_capabilities must be an object")
    protocol_version = payload.get("widget_mode_protocol_version")
    if protocol_version is not None and not isinstance(protocol_version, int):
        raise HTTPException(status_code=400, detail="widget_mode_protocol_version must be an integer")
    return await widget_mode_coordinator.register_window(
        pet_instance_id=pet_instance_id,
        window_type=str(payload.get("window_type") or "pet"),
        signal_capabilities=capabilities,
        widget_mode_protocol_version=protocol_version,
        widget_mode_compaction_lease_v1=payload.get("widget_mode_compaction_lease_v1") is True,
    )


@router.post("/api/widget-mode/windows/unregister")
async def unregister_widget_mode_window(request: Request, payload: dict[str, Any]) -> Any:
    validation_error = _validate_widget_mode_mutation(request, payload)
    if validation_error is not None:
        return validation_error
    state = await widget_mode_coordinator.unregister_window(
        str(payload.get("pet_instance_id") or ""),
    )
    return {"success": True, "state": state}


@router.post("/api/widget-mode/compaction/ack")
async def acknowledge_widget_mode_compaction(request: Request, payload: dict[str, Any]) -> Any:
    validation_error = _validate_widget_mode_mutation(request, payload)
    if validation_error is not None:
        return validation_error
    state = await widget_mode_coordinator.acknowledge_compaction(
        compaction_cycle_id=str(payload.get("compaction_cycle_id") or ""),
        pet_instance_id=str(payload.get("pet_instance_id") or ""),
        status=str(payload.get("status") or "failed"),
    )
    return {"success": True, "state": state}


@router.post("/api/widget-mode/renderer-suspension/ack")
async def acknowledge_widget_mode_renderer_suspension(
    request: Request,
    payload: dict[str, Any],
) -> Any:
    validation_error = _validate_widget_mode_mutation(request, payload)
    if validation_error is not None:
        return validation_error
    state = await widget_mode_coordinator.acknowledge_renderer_suspension(
        compaction_cycle_id=str(payload.get("compaction_cycle_id") or ""),
        pet_instance_id=str(payload.get("pet_instance_id") or ""),
        success=payload.get("success") is True,
    )
    return {"success": True, "state": state}


@router.post("/api/widget-mode/debug/compaction")
async def debug_trigger_widget_mode_compaction(
    request: Request,
    payload: dict[str, Any] | None = None,
) -> Any:
    if os.environ.get("NEKO_WIDGET_MODE_DEBUG") != "1" and os.environ.get("NEKO_DEBUG") != "1":
        raise HTTPException(status_code=404, detail="debug trigger unavailable")
    data = payload or {}
    validation_error = _validate_widget_mode_mutation(request, data)
    if validation_error is not None:
        return validation_error
    state = await widget_mode_coordinator.trigger_debug_compaction(
        reason=str(data.get("reason") or "debug"),
    )
    return {"success": True, "state": state}
