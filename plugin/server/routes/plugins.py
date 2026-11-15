"""
Plugin management routes
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
import asyncio
from plugin.server.application.plugins.lifecycle_service import _project_source_dir_sync

from plugin.logging_config import get_logger
from plugin.server.application.plugins import (
    PluginLifecycleService,
    PluginQueryService,
    PluginRegistryService,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import require_admin
from plugin.server.infrastructure.mutation_auth import require_plugin_mutation_access
from plugin.server.application.plugins.operation_lock import (
    PluginOperationBusy,
    bounded_operation_wait,
    serialized_plugin_operation,
)
from plugin.server.infrastructure.error_mapping import raise_http_from_domain
from plugin.server.lifecycle import ensure_plugin_messaging_started

router = APIRouter()
logger = get_logger("server.routes.plugins")
query_service = PluginQueryService()
lifecycle_service = PluginLifecycleService()
registry_service = PluginRegistryService()


@router.get("/plugin/status")
async def plugin_status(plugin_id: Optional[str] = Query(default=None)) -> dict[str, object]:
    try:
        return await query_service.get_plugin_status(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)

@router.get("/plugins")
async def list_plugins(
    locale: Optional[str] = Query(default=None),
    summary: bool = Query(default=False),
) -> dict[str, object]:
    try:
        return await query_service.list_plugins(locale=locale, summary=summary)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.get("/plugins/{plugin_id}")
async def get_plugin(plugin_id: str, locale: Optional[str] = Query(default=None)) -> dict[str, object]:
    try:
        return await query_service.get_plugin(plugin_id, locale=locale)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


# User-facing plugin operations must not wait indefinitely to acquire the lock.
#
# The frontend gives up after 30s, but the operation can still complete afterward (mutation is protected by asyncio.shield),
# making users see failure even though the plugin started/stopped. Prefer returning 409 promptly within budget, explaining
# who holds the lock. Background callers (autostart reconciliation, installation transactions) bypass this path and remain unchanged.
# Env: NEKO_PLUGIN_OPERATION_WAIT_BUDGET
from plugin.server.application.plugins._env_budgets import env_seconds

_OPERATION_WAIT_BUDGET_SECONDS = env_seconds("NEKO_PLUGIN_OPERATION_WAIT_BUDGET", 20.0)


def _busy_response() -> HTTPException:
    """Shape this 409 like every other error this router emits.

    It went out as a dict ``detail`` with a hard-coded Simplified-Chinese string
    and no ``X-Error-Code`` header, while every other failure here goes through
    ``raise_http_from_domain`` — string detail, header set, message in English
    like the rest of the domain errors. A client keying on the header simply did
    not see this one (this round's adversarial review).
    """
    return HTTPException(
        status_code=409,
        detail="Another plugin operation is in progress; please retry shortly",
        headers={"X-Error-Code": "PLUGIN_OPERATION_BUSY"},
    )


@router.post("/plugin/{plugin_id}/start")
async def start_plugin_endpoint(plugin_id: str, _: str = require_admin,
                                __: None = Depends(require_plugin_mutation_access)) -> dict[str, object]:
    try:
        with bounded_operation_wait(_OPERATION_WAIT_BUDGET_SECONDS):
            if not await ensure_plugin_messaging_started():
                # Start it anyway: entry triggers and @llm_tool calls travel the
                # request router and work fine, so refusing would break more than
                # it fixes. But say so -- having to reconstruct this afterwards
                # from ABSENT log lines is what made the original bug take three
                # sessions to find.
                #
                # Stated as "unverified", not "broken": the bridges are up and
                # reattach by themselves if the plane is only slow, so this often
                # heals with no further action. What it rules out is the silent
                # case -- if it does not heal, the push path says so per message.
                logger.warning(
                    "starting plugin {} before the message delivery path is "
                    "verified: its tool calls work either way; if the path does "
                    "not come up, push_message() still reports success while the "
                    "messages are dropped -- watch for 'message NOT delivered'",
                    plugin_id,
                )
            return await lifecycle_service.start_plugin(plugin_id, persist_user_intent=True)
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{plugin_id}/refresh")
async def refresh_plugin_endpoint(plugin_id: str, _: str = require_admin,
                                  __: None = Depends(require_plugin_mutation_access)) -> dict[str, object]:
    try:
        with bounded_operation_wait(_OPERATION_WAIT_BUDGET_SECONDS):
            return await registry_service.refresh_plugin(plugin_id)
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{plugin_id}/stop")
async def stop_plugin_endpoint(plugin_id: str, _: str = require_admin,
                               __: None = Depends(require_plugin_mutation_access)) -> dict[str, object]:
    try:
        with bounded_operation_wait(_OPERATION_WAIT_BUDGET_SECONDS):
            return await lifecycle_service.stop_plugin(plugin_id, persist_user_intent=True)
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


class PluginAutoStartUpdateRequest(BaseModel):
    auto_start: bool


@serialized_plugin_operation
async def _dispatch_auto_start(plugin_id: str, auto_start: bool) -> dict[str, object]:
    # Workbench projects are never auto-started by the host, so a preference
    # here would be a silent no-op; refuse instead of persisting it.
    if await asyncio.to_thread(_project_source_dir_sync, plugin_id) is not None:
        raise ServerDomainError(
            code="PLUGIN_PROJECT_AUTO_START_UNSUPPORTED",
            message="Workbench projects are not auto-started; start them manually",
            status_code=409,
        )
    return await lifecycle_service.set_plugin_auto_start(plugin_id, auto_start)


@router.put("/plugin/{plugin_id}/auto-start")
async def set_plugin_auto_start_endpoint(plugin_id: str, payload: PluginAutoStartUpdateRequest,
                                         _: str = require_admin,
                                         __: None = Depends(require_plugin_mutation_access)) -> dict[str, object]:
    try:
        with bounded_operation_wait(_OPERATION_WAIT_BUDGET_SECONDS):
            return await _dispatch_auto_start(plugin_id, payload.auto_start)
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.delete("/plugin/{plugin_id}")
async def delete_plugin_endpoint(plugin_id: str, _: str = require_admin,
                                 __: None = Depends(require_plugin_mutation_access)) -> dict[str, object]:
    try:
        with bounded_operation_wait(_OPERATION_WAIT_BUDGET_SECONDS):
            return await lifecycle_service.delete_plugin(plugin_id)
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugins/refresh")
async def refresh_plugins_endpoint(_: str = require_admin,
                                   __: None = Depends(require_plugin_mutation_access)) -> dict[str, object]:
    try:
        with bounded_operation_wait(_OPERATION_WAIT_BUDGET_SECONDS):
            return await registry_service.refresh_registry()
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{plugin_id}/reload")
async def reload_plugin_endpoint(plugin_id: str, _: str = require_admin,
                                 __: None = Depends(require_plugin_mutation_access)) -> dict[str, object]:
    try:
        with bounded_operation_wait(_OPERATION_WAIT_BUDGET_SECONDS):
            return await lifecycle_service.reload_plugin(plugin_id)
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugins/reload")
async def reload_all_plugins_endpoint(_: str = require_admin,
                                      __: None = Depends(require_plugin_mutation_access)) -> dict[str, object]:
    """
    Reload all plugins
    
    Stop every running plugin, then reload.
    Used by the frontend's global reload button.
    """
    try:
        with bounded_operation_wait(_OPERATION_WAIT_BUDGET_SECONDS):
            return await lifecycle_service.reload_all_plugins()
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
