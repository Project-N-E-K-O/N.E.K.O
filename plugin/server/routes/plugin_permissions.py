"""Plugin permission grants, panel data and the privacy overview.

Errors carry a stable code in ``detail.code`` and the ``X-Error-Code``
header, a displayable ``detail.message`` and ``detail.details``.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from plugin.logging_config import get_logger
from plugin.sdk.shared.permissions.catalog import PERMISSIONS
from plugin.server.application.plugins import permission_panel
from plugin.server.application.plugins.operation_lock import (
    PluginOperationBusy,
    bounded_operation_wait,
    serialized_plugin_operation,
)
from plugin.server.application.plugins.permission_service import (
    clear_permission,
    decide_permission,
    permission_record,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.error_mapping import raise_http_from_domain
from plugin.server.infrastructure.mutation_auth import require_plugin_mutation_access

router = APIRouter(prefix="/plugin-permissions", tags=["plugin-permissions"])
logger = get_logger("server.routes.plugin_permissions")


class PermissionDecision(BaseModel):
    state: Literal["granted", "denied"]
    condition: Literal["in_use", "anytime"] = "in_use"


@router.get("/catalog")
async def get_permission_catalog():
    """Risk tiers, privacy groups and per-permission facts shared with the UI."""
    return permission_panel.catalog()


@router.get("/plugins/{plugin_id}")
async def get_plugin_permissions(plugin_id: str):
    """Stored grants of one plugin."""
    try:
        record = await asyncio.to_thread(permission_record, plugin_id)
    except ServerDomainError as exc:
        raise_http_from_domain(exc, logger=logger, include_details=True)
    return {"plugin_id": plugin_id, **record}


@router.get("/plugins/{plugin_id}/panel")
async def get_plugin_permission_panel(plugin_id: str):
    """Permission tab data: declaration vs. scan, decisions, counts, last use."""
    try:
        return await asyncio.to_thread(permission_panel.plugin_panel, plugin_id)
    except PluginOperationBusy:
        raise _busy()
    except ServerDomainError as exc:
        raise_http_from_domain(exc, logger=logger, include_details=True)


@router.get("/pending")
async def get_pending_permissions():
    try:
        return await asyncio.to_thread(permission_panel.pending)
    except PluginOperationBusy:
        raise _busy()
    except ServerDomainError as exc:
        raise_http_from_domain(exc, logger=logger, include_details=True)


@router.get("/privacy")
async def get_privacy_overview():
    try:
        return await asyncio.to_thread(permission_panel.privacy_overview)
    except PluginOperationBusy:
        raise _busy()
    except ServerDomainError as exc:
        raise_http_from_domain(exc, logger=logger, include_details=True)


@router.get("/privacy/{group_id}")
async def get_privacy_group(group_id: str):
    try:
        return await asyncio.to_thread(permission_panel.privacy_group, group_id)
    except PluginOperationBusy:
        raise _busy()
    except ServerDomainError as exc:
        raise_http_from_domain(exc, logger=logger, include_details=True)


def _known_permission(permission: str) -> str:
    if permission not in PERMISSIONS:
        raise ServerDomainError(
            "PERMISSION_UNKNOWN", "Unknown permission", 404, details={"permission": permission}
        )
    return permission


@serialized_plugin_operation
async def _decide(plugin_id: str, permission: str, decision: PermissionDecision) -> None:
    await asyncio.to_thread(permission_panel.ensure_plugin_known, plugin_id)
    await asyncio.to_thread(
        permission_panel.check_condition_allowed,
        plugin_id,
        permission,
        decision.state,
        decision.condition,
    )
    await asyncio.to_thread(
        decide_permission, plugin_id, permission, decision.state, condition=decision.condition
    )
    await asyncio.to_thread(permission_panel.forget_refused, plugin_id, permission)


@serialized_plugin_operation
async def _clear(plugin_id: str, permission: str) -> None:
    await asyncio.to_thread(permission_panel.ensure_plugin_known, plugin_id)
    await asyncio.to_thread(clear_permission, plugin_id, permission)
    await asyncio.to_thread(permission_panel.forget_refused, plugin_id, permission)


def _busy() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "PLUGIN_OPERATION_BUSY",
            "message": "Another plugin operation is in progress",
            "details": {},
        },
        headers={"X-Error-Code": "PLUGIN_OPERATION_BUSY"},
    )


@router.put(
    "/plugins/{plugin_id}/permissions/{permission}",
    dependencies=[Depends(require_plugin_mutation_access)],
)
async def put_permission_decision(plugin_id: str, permission: str, body: PermissionDecision):
    """Allow or refuse one permission; returns the updated panel data."""
    try:
        _known_permission(permission)
        permission_panel.check_refusable(permission, body.state)
        with bounded_operation_wait(20.0):
            await _decide(plugin_id, permission, body)
        # Built after the operation lock is released.
        return await asyncio.to_thread(permission_panel.plugin_panel, plugin_id)
    except PluginOperationBusy:
        raise _busy()
    except ServerDomainError as exc:
        raise_http_from_domain(exc, logger=logger, include_details=True)


@router.delete(
    "/plugins/{plugin_id}/permissions/{permission}",
    dependencies=[Depends(require_plugin_mutation_access)],
)
async def delete_permission_decision(plugin_id: str, permission: str):
    """Return one permission to undecided; returns the updated panel data."""
    try:
        _known_permission(permission)
        with bounded_operation_wait(20.0):
            await _clear(plugin_id, permission)
        return await asyncio.to_thread(permission_panel.plugin_panel, plugin_id)
    except PluginOperationBusy:
        raise _busy()
    except ServerDomainError as exc:
        raise_http_from_domain(exc, logger=logger, include_details=True)
