# -*- coding: utf-8 -*-
"""
Storage-location bootstrap API for the main web app.

Stage 1 only serves the data required by the homepage overlay so the first-run
selection happens inside the main web UI after the app opens normally.
"""

from fastapi import APIRouter, Response

from main_routers.shared_state import get_config_manager
from utils.storage_location_bootstrap import build_storage_location_bootstrap_payload

router = APIRouter(prefix="/api/storage/location", tags=["storage_location"])


@router.get("/bootstrap")
async def get_storage_location_bootstrap(response: Response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    config_manager = get_config_manager()
    return build_storage_location_bootstrap_payload(config_manager)
