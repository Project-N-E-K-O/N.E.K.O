"""
健康检查和基础路由
"""
from fastapi import APIRouter

from plugin.logging_config import get_logger
from plugin.server.application.contracts import AvailableResponse, ServerInfoResponse
from plugin.server.application.admin.query_service import AdminQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.utils.time_utils import now_iso
from plugin.server.infrastructure.auth import require_admin
from plugin.server.infrastructure.error_mapping import raise_http_from_domain

router = APIRouter()
logger = get_logger("server.routes.health")
admin_query_service = AdminQueryService()


@router.get("/health")
async def health():
    return {"status": "ok", "time": now_iso()}


@router.get("/available")
async def available() -> AvailableResponse:
    try:
        return await admin_query_service.get_available()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.get("/server/info")
async def server_info(_: str = require_admin) -> ServerInfoResponse:
    try:
        return await admin_query_service.get_server_info()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
