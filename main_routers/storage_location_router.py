# -*- coding: utf-8 -*-
"""
Storage-location bootstrap API for the main web app.

Stage 2 keeps the same homepage bootstrap entry, and adds the first real
selection submit API used by the main web UI.
"""

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field, field_validator

from main_routers.shared_state import get_config_manager
from utils.storage_location_bootstrap import build_storage_location_bootstrap_payload
from utils.storage_policy import (
    StorageSelectionValidationError,
    compute_anchor_root,
    normalize_runtime_root,
    paths_equal,
    save_storage_policy,
    validate_selected_root,
)

router = APIRouter(prefix="/api/storage/location", tags=["storage_location"])


class StorageLocationSelectionRequest(BaseModel):
    selected_root: str = Field(..., min_length=1, max_length=4096)
    selection_source: str = Field(default="user_selected", min_length=1, max_length=64)

    @field_validator("selected_root", "selection_source")
    @classmethod
    def _strip_whitespace(cls, value: str) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("value cannot be empty")
        return stripped


def _set_no_cache_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


@router.get("/bootstrap")
async def get_storage_location_bootstrap(response: Response):
    _set_no_cache_headers(response)

    config_manager = get_config_manager()
    return build_storage_location_bootstrap_payload(config_manager)


@router.post("/select")
async def post_storage_location_select(
    payload: StorageLocationSelectionRequest,
    response: Response,
):
    _set_no_cache_headers(response)

    config_manager = get_config_manager()
    current_root = normalize_runtime_root(config_manager.app_docs_dir)
    anchor_root = compute_anchor_root(config_manager, current_root=current_root)

    try:
        normalized_selected_root = validate_selected_root(
            config_manager,
            payload.selected_root,
            current_root=current_root,
            anchor_root=anchor_root,
        )
    except StorageSelectionValidationError as exc:
        response.status_code = 400
        return {
            "ok": False,
            "error_code": exc.error_code,
            "error": exc.message,
        }

    if paths_equal(normalized_selected_root, current_root):
        policy_payload = save_storage_policy(
            config_manager,
            selected_root=current_root,
            selection_source=payload.selection_source,
            anchor_root=anchor_root,
        )
        return {
            "ok": True,
            "result": "continue_current_session",
            "selected_root": str(current_root),
            "selection_source": policy_payload["selection_source"],
        }

    return {
        "ok": True,
        "result": "restart_required",
        "selected_root": str(normalized_selected_root),
        "selection_source": payload.selection_source,
    }
