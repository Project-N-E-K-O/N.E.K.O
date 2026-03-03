"""
消息队列路由
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from plugin._types.exceptions import PluginError
from plugin.server.infrastructure.error_handler import handle_plugin_error
from plugin.server.services import get_messages_from_queue
from plugin.server.infrastructure.utils import now_iso
from plugin.settings import MESSAGE_QUEUE_DEFAULT_MAX_COUNT

router = APIRouter()


@router.get("/plugin/messages")
async def get_plugin_messages(
    plugin_id: Optional[str] = Query(default=None),
    max_count: int = Query(default=MESSAGE_QUEUE_DEFAULT_MAX_COUNT, ge=1, le=1000),
    priority_min: Optional[int] = Query(default=None, description="最低优先级（包含）"),
):
    try:
        messages = await asyncio.to_thread(
            get_messages_from_queue,
            plugin_id=plugin_id,
            max_count=max_count,
            priority_min=priority_min,
        )
        
        return {
            "messages": messages,
            "count": len(messages),
            "time": now_iso(),
        }
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError) as e:
        raise handle_plugin_error(e, "Failed to get plugin messages", 500) from e
    except Exception as e:
        logger.exception("Failed to get plugin messages: Unexpected error")
        raise handle_plugin_error(e, "Failed to get plugin messages", 500) from e
