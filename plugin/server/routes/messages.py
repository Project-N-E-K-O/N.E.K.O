"""
消息队列路由
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from plugin.logging_config import get_logger
from plugin.server.application.messages import MessageQueryService
from plugin.server.application.contracts import MessageQueryResponse
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.error_mapping import raise_http_from_domain
from plugin.settings import MESSAGE_QUEUE_DEFAULT_MAX_COUNT

router = APIRouter()
logger = get_logger("server.routes.messages")
message_query_service = MessageQueryService()


@router.get("/plugin/messages")
async def get_plugin_messages(
    plugin_id: Optional[str] = Query(default=None),
    max_count: int = Query(default=MESSAGE_QUEUE_DEFAULT_MAX_COUNT, ge=1, le=1000),
    priority_min: Optional[int] = Query(default=None, description="最低优先级（包含）"),
) -> MessageQueryResponse:
    try:
        return await message_query_service.get_plugin_messages(
            plugin_id=plugin_id,
            max_count=max_count,
            priority_min=priority_min,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
