# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""HTTP API for Game Mode Beta resource protection."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from main_logic.game_mode_resource_protection import protector
from main_routers.shared_state import get_session_manager
from main_routers.system_router import _validate_local_mutation_request

router = APIRouter()
logger = logging.getLogger(__name__)
GAME_MODE_BROADCAST_SEND_TIMEOUT_SECONDS = 2.0
_game_mode_broadcast_tasks: set[asyncio.Task[None]] = set()


async def _send_game_mode_event(name: str, ws: Any, payload: dict[str, Any]) -> None:
    try:
        async with asyncio.timeout(GAME_MODE_BROADCAST_SEND_TIMEOUT_SECONDS):
            await ws.send_json(payload)
    except TimeoutError:
        logger.warning("[GameModeBeta] broadcast timed out for session %r", name)
    except Exception:
        logger.warning("[GameModeBeta] broadcast failed for session %r", name, exc_info=True)


async def broadcast_game_mode_event(payload: dict[str, Any]) -> int:
    """Schedule bounded websocket sends without holding the protector lock on I/O."""
    delivered = 0
    try:
        session_manager = get_session_manager()
    except Exception:
        logger.warning("[GameModeBeta] broadcast skipped: session manager unavailable", exc_info=True)
        return 0

    for name in list(session_manager.keys()):
        try:
            core = session_manager.get(name)
            ws = getattr(core, "websocket", None)
            if ws is None or not hasattr(ws, "send_json"):
                continue
            client_state = getattr(ws, "client_state", None)
            state_name = str(client_state).upper()
            if client_state is not None and "CONNECTED" not in state_name:
                continue
            task = asyncio.create_task(
                _send_game_mode_event(name, ws, payload),
                name="game_mode_beta_broadcast",
            )
            _game_mode_broadcast_tasks.add(task)
            task.add_done_callback(_game_mode_broadcast_tasks.discard)
            delivered += 1
        except Exception:
            logger.warning("[GameModeBeta] broadcast failed for session %r", name, exc_info=True)
            continue
    return delivered


protector.set_broadcaster(broadcast_game_mode_event)


def _validate_game_mode_mutation(request: Request, payload: dict[str, Any]) -> Any:
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
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", ""}:
            return False
    return False


@router.get("/api/game-mode-beta/state")
async def get_game_mode_beta_state() -> dict[str, Any]:
    return {"success": True, "state": protector.snapshot()}


@router.post("/api/game-mode-beta/enabled")
async def set_game_mode_beta_enabled(request: Request, payload: dict[str, Any]) -> Any:
    validation_error = _validate_game_mode_mutation(request, payload)
    if validation_error is not None:
        return validation_error
    enabled = _coerce_enabled_flag(payload.get("enabled"))
    state = await protector.set_enabled(enabled)
    return {"success": True, "state": state}


@router.post("/api/game-mode-beta/manual-restore")
async def mark_game_mode_beta_manual_restore(request: Request, payload: dict[str, Any] | None = None) -> Any:
    data = payload or {}
    validation_error = _validate_game_mode_mutation(request, data)
    if validation_error is not None:
        return validation_error
    pet_instance_id = data.get("pet_instance_id")
    state = await protector.mark_manual_restore(
        str(pet_instance_id) if pet_instance_id is not None else None,
    )
    return {"success": True, "state": state}


@router.get("/api/game-mode-beta/settings")
async def get_game_mode_beta_settings() -> dict[str, Any]:
    return protector.settings_snapshot()


@router.post("/api/game-mode-beta/settings")
async def set_game_mode_beta_settings(request: Request, payload: dict[str, Any]) -> Any:
    validation_error = _validate_game_mode_mutation(request, payload)
    if validation_error is not None:
        return validation_error
    current = protector.settings_snapshot()
    auto_cat = payload.get("auto_cat_on_game", current["auto_cat_on_game"])
    mode = payload.get("game_trigger_mode", current["game_trigger_mode"])
    if not isinstance(auto_cat, bool):
        raise HTTPException(status_code=400, detail="auto_cat_on_game must be boolean")
    if not isinstance(mode, str) or mode not in {"smart", "instant"}:
        raise HTTPException(status_code=400, detail="game_trigger_mode must be 'smart' or 'instant'")
    return await protector.set_settings(
        auto_cat_on_game=auto_cat,
        game_trigger_mode=mode,
    )


@router.post("/api/game-mode-beta/windows/register")
async def register_game_mode_beta_window(request: Request, payload: dict[str, Any]) -> Any:
    validation_error = _validate_game_mode_mutation(request, payload)
    if validation_error is not None:
        return validation_error
    pet_instance_id = payload.get("pet_instance_id")
    if not isinstance(pet_instance_id, str) or not pet_instance_id.strip():
        raise HTTPException(status_code=400, detail="pet_instance_id required")
    capabilities = payload.get("signal_capabilities")
    if capabilities is not None and not isinstance(capabilities, dict):
        raise HTTPException(status_code=400, detail="signal_capabilities must be an object")
    return await protector.register_window(
        pet_instance_id=pet_instance_id,
        window_type=str(payload.get("window_type") or "pet"),
        signal_capabilities=capabilities,
    )


@router.post("/api/game-mode-beta/windows/unregister")
async def unregister_game_mode_beta_window(request: Request, payload: dict[str, Any]) -> Any:
    validation_error = _validate_game_mode_mutation(request, payload)
    if validation_error is not None:
        return validation_error
    return {
        "success": True,
        "state": await protector.unregister_window(str(payload.get("pet_instance_id") or "")),
    }


@router.post("/api/game-mode-beta/ack")
async def acknowledge_game_mode_beta_switch(request: Request, payload: dict[str, Any]) -> Any:
    validation_error = _validate_game_mode_mutation(request, payload)
    if validation_error is not None:
        return validation_error
    state = await protector.acknowledge_switch(
        cycle_id=str(payload.get("cycle_id") or ""),
        pet_instance_id=str(payload.get("pet_instance_id") or ""),
        status=str(payload.get("status") or "failed"),
    )
    return {"success": True, "state": state}


@router.post("/api/game-mode-beta/deep-sleep-ack")
async def acknowledge_game_mode_beta_deep_sleep(request: Request, payload: dict[str, Any]) -> Any:
    validation_error = _validate_game_mode_mutation(request, payload)
    if validation_error is not None:
        return validation_error
    state = await protector.acknowledge_deep_sleep(
        cycle_id=str(payload.get("cycle_id") or ""),
        pet_instance_id=str(payload.get("pet_instance_id") or ""),
        success=payload.get("success") is True,
    )
    return {"success": True, "state": state}


@router.post("/api/game-mode-beta/reset-candidate")
async def reset_game_mode_beta_candidate(request: Request, payload: dict[str, Any] | None = None) -> Any:
    data = payload or {}
    validation_error = _validate_game_mode_mutation(request, data)
    if validation_error is not None:
        return validation_error
    state = await protector.reset_game_candidate(str(data.get("reason") or "external-reset"))
    return {"success": True, "state": state}


@router.post("/api/game-mode-beta/debug/trigger")
async def debug_trigger_game_mode_beta(request: Request, payload: dict[str, Any] | None = None) -> Any:
    if os.environ.get("NEKO_GAME_MODE_DEBUG") != "1" and os.environ.get("NEKO_DEBUG") != "1":
        raise HTTPException(status_code=404, detail="debug trigger unavailable")
    data = payload or {}
    validation_error = _validate_game_mode_mutation(request, data)
    if validation_error is not None:
        return validation_error
    reason = str(data.get("reason") or "debug")
    try:
        percent = float(data.get("percent", 99.0))
    except (TypeError, ValueError):
        percent = 99.0
    state = await protector.debug_trigger(reason=reason, percent=percent)
    return {"success": True, "state": state}
