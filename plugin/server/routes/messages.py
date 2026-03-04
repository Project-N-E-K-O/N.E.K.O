"""
消息队列路由
"""
from __future__ import annotations

from typing import NoReturn, Optional

from fastapi import APIRouter, HTTPException, Query

from plugin.logging_config import get_logger
from plugin.server.application.messages import MessageQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.settings import MESSAGE_QUEUE_DEFAULT_MAX_COUNT

router = APIRouter()
logger = get_logger("server.routes.messages")
message_query_service = MessageQueryService()


def _raise_http_from_domain(error: ServerDomainError) -> NoReturn:
    logger.warning(
        "Domain error: code={}, status_code={}, message={}",
        error.code,
        error.status_code,
        error.message,
    )
    raise HTTPException(status_code=error.status_code, detail=error.message)


@router.get("/plugin/messages")
async def get_plugin_messages(
    plugin_id: Optional[str] = Query(default=None),
    max_count: int = Query(default=MESSAGE_QUEUE_DEFAULT_MAX_COUNT, ge=1, le=1000),
    priority_min: Optional[int] = Query(default=None, description="最低优先级（包含）"),
) -> dict[str, object]:
    try:
        return await message_query_service.get_plugin_messages(
            plugin_id=plugin_id,
            max_count=max_count,
            priority_min=priority_min,
        )
    except ServerDomainError as error:
        _raise_http_from_domain(error)
