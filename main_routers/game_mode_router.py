# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""HTTP API for Game Mode Beta resource protection."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException

from main_logic.game_mode_resource_protection import protector

router = APIRouter()


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
async def set_game_mode_beta_enabled(payload: dict[str, Any]) -> dict[str, Any]:
    enabled = _coerce_enabled_flag(payload.get("enabled"))
    state = await protector.set_enabled(enabled)
    return {"success": True, "state": state}


@router.post("/api/game-mode-beta/manual-restore")
async def mark_game_mode_beta_manual_restore() -> dict[str, Any]:
    state = await protector.mark_manual_restore()
    return {"success": True, "state": state}


@router.post("/api/game-mode-beta/debug/trigger")
async def debug_trigger_game_mode_beta(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if os.environ.get("NEKO_GAME_MODE_DEBUG") != "1" and os.environ.get("NEKO_DEBUG") != "1":
        raise HTTPException(status_code=404, detail="debug trigger unavailable")
    data = payload or {}
    reason = str(data.get("reason") or "debug")
    try:
        percent = float(data.get("percent", 99.0))
    except (TypeError, ValueError):
        percent = 99.0
    state = await protector.debug_trigger(reason=reason, percent=percent)
    return {"success": True, "state": state}
