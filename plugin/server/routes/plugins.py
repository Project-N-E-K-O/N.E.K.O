"""
插件管理路由
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
import asyncio
from plugin.server.routes.development import router as development_router
from plugin.server.application.plugins.development import registration_for_plugin_sync, list_registration_records_sync
from plugin.server.application.plugins.development_service import development_lifecycle_action
from plugin.server.infrastructure.development_access import require_development_access

from plugin.logging_config import get_logger
from plugin.server.application.plugins import (
    PluginLifecycleService,
    PluginQueryService,
    PluginRegistryService,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import require_admin
from plugin.server.application.plugins.operation_lock import (
    PluginOperationBusy,
    bounded_operation_wait,
    serialized_plugin_operation,
)
from plugin.server.infrastructure.error_mapping import raise_http_from_domain
from plugin.server.lifecycle import ensure_plugin_messaging_started

router = APIRouter()
router.include_router(development_router)
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
async def list_plugins(locale: Optional[str] = Query(default=None)) -> dict[str, object]:
    try:
        return await query_service.list_plugins(locale=locale)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


# 用户在等的那些插件操作，抢锁不能无限等。
#
# 前端 30s 就放弃，而放弃之后那次操作照样会落地（mutation 被 asyncio.shield 保
# 着），于是用户看到"失败"、插件其实被启停了。宁可在预算内立刻回 409 并说明是
# 谁占着。后台调用方（自启动对账、安装事务）不经过这里，行为不变。
# Env: NEKO_PLUGIN_OPERATION_WAIT_BUDGET
from plugin.server.application.plugins._env_budgets import env_seconds

_OPERATION_WAIT_BUDGET_SECONDS = env_seconds("NEKO_PLUGIN_OPERATION_WAIT_BUDGET", 20.0)


def _busy_response() -> HTTPException:
    """Shape this 409 like every other error this router emits.

    It went out as a dict ``detail`` with a hard-coded Simplified-Chinese string
    and no ``X-Error-Code`` header, while every other failure here goes through
    ``raise_http_from_domain`` — string detail, header set, message in English
    like the rest of the domain errors. A client keying on the header simply did
    not see this one (本轮对抗复审).
    """
    return HTTPException(
        status_code=409,
        detail="Another plugin operation is in progress; please retry shortly",
        headers={"X-Error-Code": "PLUGIN_OPERATION_BUSY"},
    )


@serialized_plugin_operation
async def _dispatch_lifecycle(request: Request, plugin_id: str, action: str,
                              registration_id: str | None, revision: int | None) -> dict[str, object]:
    # Check provenance after acquiring the same lock as registration/rebinding,
    # so an ordinary request cannot acquire a newly registered development ID.
    if registration_id is not None or await asyncio.to_thread(registration_for_plugin_sync, plugin_id) is not None:
        require_development_access(request)
        return await development_lifecycle_action(plugin_id, action, registration_id, revision)
    if action == "reload":
        return await lifecycle_service.reload_plugin(plugin_id)
    if action == "stop":
        return await lifecycle_service.stop_plugin(plugin_id, persist_user_intent=True)
    return await lifecycle_service.start_plugin(plugin_id, persist_user_intent=True)


@serialized_plugin_operation
async def _dispatch_reload_all(request: Request) -> dict[str, object]:
    try:
        registrations = await asyncio.to_thread(list_registration_records_sync)
    except ServerDomainError as exc:
        if exc.code != "DEVELOPMENT_STORE_INVALID":
            raise
        # Provenance is uncertain: keep local access mandatory, then let the
        # lifecycle path reload known managed hosts and retain unverifiable ones.
        require_development_access(request)
        return await lifecycle_service.reload_all_plugins()
    # Reload-all refreshes the whole registry, including stopped sources.
    # Apply the same provenance boundary as an explicit full refresh.
    if registrations:
        require_development_access(request)
    return await lifecycle_service.reload_all_plugins()


@serialized_plugin_operation
async def _dispatch_refresh(request: Request, plugin_id: str | None = None,
                            registration_id: str | None = None, revision: int | None = None) -> dict[str, object]:
    if plugin_id is None:
        try:
            registrations = await asyncio.to_thread(list_registration_records_sync)
        except ServerDomainError as exc:
            if exc.code != "DEVELOPMENT_STORE_INVALID":
                raise
            # Registry discovery reports the damaged store and skips external
            # sources, while continuing to publish ordinary managed plugins.
            require_development_access(request)
            registrations = []
        if registrations:
            require_development_access(request)
        return await registry_service.refresh_registry()
    registration = await asyncio.to_thread(registration_for_plugin_sync, plugin_id)
    if registration is not None or registration_id is not None:
        require_development_access(request)
        if registration is None or registration.registration_id != registration_id or registration.revision != revision:
            raise ServerDomainError(code="DEVELOPMENT_STALE", message="Development registration changed; refresh and retry", status_code=409)
    return await registry_service.refresh_plugin(plugin_id)


@router.post("/plugin/{plugin_id}/start")
async def start_plugin_endpoint(plugin_id: str, request: Request, _: str = require_admin,
                                registration_id: str | None = None, revision: int | None = Query(default=None, ge=1)) -> dict[str, object]:
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
            return await _dispatch_lifecycle(request, plugin_id, "start", registration_id, revision)
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{plugin_id}/refresh")
async def refresh_plugin_endpoint(plugin_id: str, request: Request, _: str = require_admin,
                                  registration_id: str | None = None, revision: int | None = Query(default=None, ge=1)) -> dict[str, object]:
    try:
        with bounded_operation_wait(_OPERATION_WAIT_BUDGET_SECONDS):
            return await _dispatch_refresh(request, plugin_id, registration_id, revision)
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{plugin_id}/stop")
async def stop_plugin_endpoint(plugin_id: str, request: Request, _: str = require_admin,
                               registration_id: str | None = None, revision: int | None = Query(default=None, ge=1)) -> dict[str, object]:
    try:
        with bounded_operation_wait(_OPERATION_WAIT_BUDGET_SECONDS):
            return await _dispatch_lifecycle(request, plugin_id, "stop", registration_id, revision)
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.delete("/plugin/{plugin_id}")
async def delete_plugin_endpoint(plugin_id: str, _: str = require_admin) -> dict[str, object]:
    try:
        with bounded_operation_wait(_OPERATION_WAIT_BUDGET_SECONDS):
            return await lifecycle_service.delete_plugin(plugin_id)
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugins/refresh")
async def refresh_plugins_endpoint(request: Request, _: str = require_admin) -> dict[str, object]:
    try:
        with bounded_operation_wait(_OPERATION_WAIT_BUDGET_SECONDS):
            return await _dispatch_refresh(request)
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{plugin_id}/reload")
async def reload_plugin_endpoint(plugin_id: str, request: Request, _: str = require_admin,
                                 registration_id: str | None = None, revision: int | None = Query(default=None, ge=1)) -> dict[str, object]:
    try:
        with bounded_operation_wait(_OPERATION_WAIT_BUDGET_SECONDS):
            return await _dispatch_lifecycle(request, plugin_id, "reload", registration_id, revision)
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugins/reload")
async def reload_all_plugins_endpoint(request: Request, _: str = require_admin) -> dict[str, object]:
    """
    重载所有插件
    
    停止所有运行中的插件，然后重新加载。
    用于前端全局重载按钮。
    """
    try:
        with bounded_operation_wait(_OPERATION_WAIT_BUDGET_SECONDS):
            return await _dispatch_reload_all(request)
    except PluginOperationBusy:
        raise _busy_response()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
