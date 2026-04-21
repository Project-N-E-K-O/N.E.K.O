"""
命令面板路由

GET  /chat/actions                       — 获取所有操作项
GET  /chat/actions/preferences           — 获取用户偏好
POST /chat/actions/preferences           — 保存用户偏好
POST /chat/actions/{action_id}/execute   — 执行操作
"""
from typing import Optional

from fastapi import APIRouter, Query

from plugin.logging_config import get_logger
from plugin.server.application.actions.aggregation_service import ActionAggregationService
from plugin.server.application.actions.execution_service import ActionExecutionService
from plugin.server.application.actions.preferences_service import PreferencesService
from plugin.server.domain.action_models import (
    ActionExecuteRequest,
    ActionExecuteResponse,
    UserActionPreferences,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import require_admin
from plugin.server.infrastructure.error_mapping import raise_http_from_domain

router = APIRouter()
logger = get_logger("server.routes.actions")
aggregation_service = ActionAggregationService()
execution_service = ActionExecutionService()
preferences_service = PreferencesService()


@router.get("/chat/actions")
async def get_chat_actions(
    plugin_id: Optional[str] = Query(default=None),
    _: str = require_admin,
) -> dict[str, object]:
    try:
        actions = await aggregation_service.aggregate_actions(plugin_id=plugin_id)
        prefs = await preferences_service.load()
        return {
            "actions": [a.model_dump(exclude_none=True) for a in actions],
            "preferences": prefs.model_dump(),
        }
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


# ── Preferences routes MUST be registered before the {action_id:path}
#    route below, otherwise FastAPI would match "preferences" as an action_id.

@router.get("/chat/actions/preferences")
async def get_action_preferences(
    _: str = require_admin,
) -> dict[str, object]:
    prefs = await preferences_service.load()
    return prefs.model_dump()


@router.post("/chat/actions/preferences")
async def save_action_preferences(
    payload: UserActionPreferences,
    _: str = require_admin,
) -> dict[str, object]:
    prefs = await preferences_service.save(payload)
    return prefs.model_dump()


# ── Execute route uses {action_id:path} — must come last.

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
        # Auto-update recent list on successful execution
        if result.success:
            try:
                await preferences_service.touch_recent(action_id)
            except Exception:
                pass  # Non-critical — don't fail the execute response
        return result.model_dump(exclude_none=True)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
