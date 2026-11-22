"""Read-only view of a plugin's stored permission grants."""

import asyncio

from fastapi import APIRouter

from plugin.logging_config import get_logger
from plugin.server.application.plugins.permission_service import permission_record
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.error_mapping import raise_http_from_domain

router = APIRouter(prefix="/plugin-permissions", tags=["plugin-permissions"])
logger = get_logger("server.routes.plugin_permissions")


@router.get("/plugins/{plugin_id}")
async def get_plugin_permissions(plugin_id: str):
    """Stored grants of one plugin.

    Errors carry a stable code in ``detail.code`` and the ``X-Error-Code``
    header, a displayable ``detail.message`` and ``detail.details``.
    """
    try:
        record = await asyncio.to_thread(permission_record, plugin_id)
    except ServerDomainError as exc:
        raise_http_from_domain(exc, logger=logger, include_details=True)
    return {"plugin_id": plugin_id, **record}
