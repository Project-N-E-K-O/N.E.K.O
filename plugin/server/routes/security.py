"""Security bootstrap endpoints for the plugin manager."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from plugin.server.infrastructure.mutation_auth import (
    csrf_token,
    require_plugin_token_bootstrap_access,
)

router = APIRouter()


@router.get("/security/csrf-token")
async def get_csrf_token(
    request: Request,
    _: None = Depends(require_plugin_token_bootstrap_access),
) -> JSONResponse:
    response = JSONResponse({"csrf_token": csrf_token()})
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response
