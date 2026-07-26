# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""HTTP boundary for the Widget Mode enabled state."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from main_logic.widget_mode_runtime import widget_mode_coordinator
from main_routers.system_router import _validate_local_mutation_request

router = APIRouter()


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
