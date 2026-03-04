"""
健康检查和基础路由
"""
from typing import NoReturn

from fastapi import APIRouter, HTTPException

from plugin.logging_config import get_logger
from plugin.server.application.admin.query_service import AdminQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.utils import now_iso
from plugin.server.infrastructure.auth import require_admin

router = APIRouter()
logger = get_logger("server.routes.health")
admin_query_service = AdminQueryService()


def _raise_http_from_domain(error: ServerDomainError) -> NoReturn:
    logger.warning(
        "Domain error: code={}, status_code={}, message={}",
        error.code,
        error.status_code,
        error.message,
    )
    raise HTTPException(status_code=error.status_code, detail=error.message)


@router.get("/health")
async def health():
    return {"status": "ok", "time": now_iso()}


@router.get("/available")
async def available():
    try:
        return await admin_query_service.get_available()
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.get("/server/info")
async def server_info(_: str = require_admin):
    try:
        return await admin_query_service.get_server_info()
    except ServerDomainError as error:
        _raise_http_from_domain(error)
