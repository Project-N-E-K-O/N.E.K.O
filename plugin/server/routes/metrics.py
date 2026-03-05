"""
性能监控路由
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from plugin.logging_config import get_logger
from plugin.server.application.contracts import (
    AllPluginMetricsResponse,
    PluginMetricsHistoryResponse,
    PluginMetricsResponse,
)
from plugin.server.application.monitoring import MetricsQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import require_admin
from plugin.server.infrastructure.error_mapping import raise_http_from_domain

router = APIRouter()
logger = get_logger("server.routes.metrics")
metrics_query_service = MetricsQueryService()


@router.get("/plugin/metrics")
async def get_all_plugin_metrics(_: str = require_admin) -> AllPluginMetricsResponse:
    try:
        return await metrics_query_service.get_all_plugin_metrics()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.get("/plugin/metrics/{plugin_id}")
async def get_plugin_metrics(plugin_id: str, _: str = require_admin) -> PluginMetricsResponse:
    try:
        return await metrics_query_service.get_plugin_metrics(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.get("/plugin/metrics/{plugin_id}/history")
async def get_plugin_metrics_history(
    plugin_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    _: str = require_admin,
) -> PluginMetricsHistoryResponse:
    try:
        return await metrics_query_service.get_plugin_metrics_history(
            plugin_id=plugin_id,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
