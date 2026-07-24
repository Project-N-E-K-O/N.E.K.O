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

"""System status, social bootstrap, token usage and pending-notice endpoints.

Split out of the former monolithic ``main_routers/system_router.py``.
"""

import os
from typing import Any

from fastapi import Request
from fastapi.responses import Response
from utils.storage_location_bootstrap import build_storage_location_bootstrap_payload

from ._shared import (
    _get_system_config_manager,
    _json_no_store_response,
    _read_json_object,
    _set_no_store_headers,
    _validate_local_mutation_request,
    logger,
    router,
)


def _derive_system_lifecycle_state(storage_bootstrap: dict[str, Any]) -> str:
    if not isinstance(storage_bootstrap, dict):
        return "starting"

    if (
        bool(storage_bootstrap.get("selection_required"))
        or bool(storage_bootstrap.get("migration_pending"))
        or bool(storage_bootstrap.get("recovery_required"))
        or bool(str(storage_bootstrap.get("blocking_reason") or "").strip())
    ):
        return "migration_required"

    return "ready"


@router.get("/system/status")
async def get_system_status(response: Response):
    """Return a lightweight readiness snapshot for the web bootstrap sentinel."""
    _set_no_store_headers(response)

    try:
        config_manager = _get_system_config_manager()
        storage_bootstrap = build_storage_location_bootstrap_payload(config_manager)
        lifecycle_state = _derive_system_lifecycle_state(storage_bootstrap)
        return {
            "ok": True,
            "status": lifecycle_state,
            "ready": lifecycle_state == "ready",
            "storage": {
                "selection_required": bool(storage_bootstrap.get("selection_required")),
                "migration_pending": bool(storage_bootstrap.get("migration_pending")),
                "recovery_required": bool(storage_bootstrap.get("recovery_required")),
                "legacy_cleanup_pending": bool(storage_bootstrap.get("legacy_cleanup_pending")),
                "blocking_reason": str(storage_bootstrap.get("blocking_reason") or ""),
                "last_error_summary": str(storage_bootstrap.get("last_error_summary") or ""),
                "stage": storage_bootstrap.get("stage") or "",
            },
        }
    except Exception as exc:
        logger.warning("system status probe unavailable during startup: %s", exc)
        return {
            "ok": True,
            "status": "starting",
            "ready": False,
            "storage": {
                "selection_required": False,
                "migration_pending": False,
                "recovery_required": False,
                "legacy_cleanup_pending": False,
                "blocking_reason": "",
                "last_error_summary": "",
                "stage": "",
            },
        }


@router.get("/system/client-id")
async def get_system_client_id(response: Response):
    """Return the persistent client ID used by N.E.K.O.Servers."""
    _set_no_store_headers(response)
    try:
        config_manager = _get_system_config_manager()
        state_path = config_manager.cloudsave_local_state_path
        needs_persist = not state_path.exists()
        state = config_manager.load_cloudsave_local_state()
        client_id = state.get("client_id") if isinstance(state, dict) else None
        if not isinstance(client_id, str) or not client_id:
            state = config_manager.build_default_cloudsave_local_state()
            client_id = state.get("client_id")
            needs_persist = True
        if not isinstance(client_id, str) or not client_id:
            raise ValueError("cloudsave state did not provide a client_id")
        if needs_persist and isinstance(state, dict):
            config_manager.save_cloudsave_local_state(state)
        return {"ok": True, "client_id": client_id}
    except Exception as exc:
        logger.warning("system client-id endpoint failed: %s", exc)
        return _json_no_store_response(
            {"ok": False, "error": "internal_error"},
            status_code=500,
        )


_DEFAULT_NEKO_SOCIAL_BASE_URL = "https://community.project-neko.cn"


@router.get("/system/social/config")
async def get_system_social_config(response: Response):
    """Return the configured N.E.K.O.Servers base URL."""
    _set_no_store_headers(response)
    raw = os.environ.get("NEKO_SOCIAL_BASE_URL", "")
    base_url = raw.strip().rstrip("/") or _DEFAULT_NEKO_SOCIAL_BASE_URL
    return {
        "ok": True,
        "social_base_url": base_url,
        "enabled": True,
    }


@router.get("/token-usage")
async def get_token_usage(days: int = 7):
    """Return LLM token usage statistics for the last N days."""
    from utils.token_tracker import TokenTracker
    return TokenTracker.get_instance().get_stats(days=min(days, 90))


@router.get("/pending-notices")
async def get_pending_notices():
    """Fetch pending pop-up notices on frontend page load (read-only snapshot; does not clear the queue).

    Returns {"notices": [...], "cursor": N}; after display the frontend must pass the
    cursor back to the ack endpoint, ensuring only the notices shown this time are
    deleted and entries enqueued between the two requests are never lost.
    """
    from main_logic.core import peek_prominent_notices
    notices, cursor = peek_prominent_notices()
    return {"notices": notices, "cursor": cursor}


@router.post("/pending-notices/ack")
async def ack_pending_notices(request: Request):
    """Called after the frontend has shown the notices; deletes only notices up to the cursor (cursor ack, avoids TOCTOU)."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    from main_logic.core import drain_prominent_notices
    try:
        body = await _read_json_object(request)
        cursor = int(body.get("cursor", 0))
    except Exception:
        cursor = 0
    drain_prominent_notices(cursor)
    return {"ok": True}
