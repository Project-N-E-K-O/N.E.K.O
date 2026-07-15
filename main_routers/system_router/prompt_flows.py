# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tutorial-prompt and autostart-prompt state endpoints (thin wrappers
around utils.tutorial_prompt_state / utils.autostart_prompt_state).

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import _read_json_object, _validate_local_mutation_request, router
from fastapi import Request
from fastapi.responses import JSONResponse
from ..shared_state import get_config_manager
from utils.autostart_prompt_state import (
    get_autostart_prompt_state_response,
    process_autostart_prompt_heartbeat,
    record_autostart_prompt_shown,
    record_autostart_prompt_decision,
)
from utils.tutorial_prompt_state import (
    get_tutorial_prompt_state_response,
    process_tutorial_prompt_heartbeat,
    record_tutorial_prompt_shown,
    record_tutorial_prompt_decision,
    record_tutorial_started,
    record_tutorial_completed,
    reset_tutorial_prompt_state,
)


@router.get("/tutorial-prompt/state")
async def get_tutorial_prompt_state():
    """Return a snapshot of the tutorial prompt state."""
    return get_tutorial_prompt_state_response(config_manager=get_config_manager())


@router.post("/tutorial-prompt/heartbeat")
async def post_tutorial_prompt_heartbeat(request: Request):
    """Record homepage idle and interaction state, and decide whether to prompt the tutorial."""
    payload = await _read_json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        return validation_error

    return process_tutorial_prompt_heartbeat(payload, config_manager=get_config_manager())


@router.post("/tutorial-prompt/shown")
async def post_tutorial_prompt_shown(request: Request):
    """Record that the tutorial prompt was actually shown to the user."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    payload = await _read_json_object(request)

    try:
        return record_tutorial_prompt_shown(payload, config_manager=get_config_manager())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@router.post("/tutorial-prompt/decision")
async def post_tutorial_prompt_decision(request: Request):
    """Record the user's decision on the tutorial prompt."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    payload = await _read_json_object(request)

    try:
        return record_tutorial_prompt_decision(payload, config_manager=get_config_manager())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@router.post("/tutorial-prompt/reset")
async def post_tutorial_prompt_reset(request: Request):
    """Reset the homepage tutorial state, for the manual reset entry in the memory browser."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    return reset_tutorial_prompt_state(config_manager=get_config_manager())


@router.get("/autostart-prompt/state")
async def get_autostart_prompt_state():
    """Return a snapshot of the autostart prompt state."""
    return get_autostart_prompt_state_response(config_manager=get_config_manager())


@router.post("/autostart-prompt/heartbeat")
async def post_autostart_prompt_heartbeat(request: Request):
    """Record homepage idle and interaction state, and decide whether to prompt about autostart."""
    payload = await _read_json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        return validation_error

    return process_autostart_prompt_heartbeat(payload, config_manager=get_config_manager())


@router.post("/autostart-prompt/shown")
async def post_autostart_prompt_shown(request: Request):
    """Record that the autostart prompt was actually shown to the user."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    payload = await _read_json_object(request)

    try:
        return record_autostart_prompt_shown(payload, config_manager=get_config_manager())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@router.post("/autostart-prompt/decision")
async def post_autostart_prompt_decision(request: Request):
    """Record the user's decision on the autostart prompt."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    payload = await _read_json_object(request)

    try:
        return record_autostart_prompt_decision(payload, config_manager=get_config_manager())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@router.post("/tutorial-prompt/tutorial-started")
async def post_tutorial_started(request: Request):
    """Record that the homepage tutorial actually started."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    payload = await _read_json_object(request)

    try:
        return record_tutorial_started(payload, config_manager=get_config_manager())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@router.post("/tutorial-prompt/tutorial-completed")
async def post_tutorial_completed(request: Request):
    """Record that the homepage tutorial was completed."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    payload = await _read_json_object(request)

    try:
        return record_tutorial_completed(payload, config_manager=get_config_manager())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
