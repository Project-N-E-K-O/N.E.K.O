"""
性能监控路由
"""
from __future__ import annotations

from typing import NoReturn, Optional

from fastapi import APIRouter, HTTPException, Query

from plugin.logging_config import get_logger
from plugin.server.application.monitoring import MetricsQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import require_admin

router = APIRouter()
logger = get_logger("server.routes.metrics")
metrics_query_service = MetricsQueryService()


def _raise_http_from_domain(error: ServerDomainError) -> NoReturn:
    logger.warning(
        "Domain error: code={}, status_code={}, message={}",
        error.code,
        error.status_code,
        error.message,
    )
    raise HTTPException(status_code=error.status_code, detail=error.message)


@router.get("/plugin/metrics")
async def get_all_plugin_metrics(_: str = require_admin) -> dict[str, object]:
    try:
        return await metrics_query_service.get_all_plugin_metrics()
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.get("/plugin/metrics/{plugin_id}")
async def get_plugin_metrics(plugin_id: str, _: str = require_admin) -> dict[str, object]:
    try:
        return await metrics_query_service.get_plugin_metrics(plugin_id)
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.get("/plugin/metrics/{plugin_id}/history")
async def get_plugin_metrics_history(
    plugin_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    _: str = require_admin,
) -> dict[str, object]:
    try:
        return await metrics_query_service.get_plugin_metrics_history(
            plugin_id=plugin_id,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
        )
    except ServerDomainError as error:
        _raise_http_from_domain(error)
