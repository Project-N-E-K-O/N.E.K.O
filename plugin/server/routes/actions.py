"""
快捷操作面板路由

GET  /chat/actions              — 获取所有操作项
POST /chat/actions/{action_id}/execute — 执行操作
"""
from typing import Optional

from fastapi import APIRouter, Query

from plugin.logging_config import get_logger
from plugin.server.application.actions.aggregation_service import ActionAggregationService
from plugin.server.application.actions.execution_service import ActionExecutionService
from plugin.server.domain.action_models import ActionExecuteRequest, ActionExecuteResponse
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import require_admin
from plugin.server.infrastructure.error_mapping import raise_http_from_domain

router = APIRouter()
logger = get_logger("server.routes.actions")
aggregation_service = ActionAggregationService()
execution_service = ActionExecutionService()


@router.get("/chat/actions")
async def get_chat_actions(
    plugin_id: Optional[str] = Query(default=None),
    _: str = require_admin,
) -> dict[str, object]:
    try:
        actions = await aggregation_service.aggregate_actions(plugin_id=plugin_id)
        categories = sorted({a.category for a in actions})
        return {
            "actions": [a.model_dump(exclude_none=True) for a in actions],
            "categories": categories,
        }
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/chat/actions/{action_id:path}/execute")
async def execute_chat_action(
    action_id: str,
    payload: ActionExecuteRequest,
    _: str = require_admin,
) -> dict[str, object]:
    try:
        result: ActionExecuteResponse = await execution_service.execute(
            action_id=action_id,
            value=payload.value,
        )
        return result.model_dump(exclude_none=True)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
