"""Platform-owned capability disclosure and explicit user decisions."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from plugin.logging_config import get_logger
from plugin.server.application.plugins.capability_analysis_service import (
    plugin_source,
    schedule_analysis,
)
from plugin.server.application.plugins.consent_service import (
    approve_plugin,
    consent_details,
    consent_notices,
    deny_plugin,
    revoke_plugin,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.application.plugins.operation_lock import (
    bounded_operation_wait,
    PluginOperationBusy,
)
from plugin.server.infrastructure.error_mapping import raise_http_from_domain
from plugin.server.infrastructure.mutation_auth import require_plugin_mutation_access

router = APIRouter(prefix="/plugin-consent", tags=["plugin-consent"])
logger = get_logger("server.routes.plugin_consent")


class ConsentDecision(BaseModel):
    approved: bool


@router.get("/plugins/{plugin_id}")
async def get_consent(plugin_id: str):
    try:
        return await asyncio.to_thread(consent_details, plugin_id)
    except ServerDomainError as exc:
        raise_http_from_domain(exc, logger=logger)


@router.post(
    "/plugins/{plugin_id}",
    dependencies=[Depends(require_plugin_mutation_access)],
)
async def decide_consent(plugin_id: str, body: ConsentDecision):
    try:
        with bounded_operation_wait(20.0):
            if body.approved:
                return await approve_plugin(plugin_id)
            return await deny_plugin(plugin_id)
    except PluginOperationBusy:
        raise HTTPException(
            status_code=409,
            detail="Another plugin operation is in progress",
            headers={"X-Error-Code": "PLUGIN_OPERATION_BUSY"},
        )
    except ServerDomainError as exc:
        raise_http_from_domain(exc, logger=logger)


@router.delete(
    "/plugins/{plugin_id}",
    dependencies=[Depends(require_plugin_mutation_access)],
)
async def revoke_consent(plugin_id: str):
    try:
        with bounded_operation_wait(20.0):
            return await revoke_plugin(plugin_id)
    except PluginOperationBusy:
        raise HTTPException(
            status_code=409,
            detail="Another plugin operation is in progress",
            headers={"X-Error-Code": "PLUGIN_OPERATION_BUSY"},
        )
    except ServerDomainError as exc:
        raise_http_from_domain(exc, logger=logger)


@router.post(
    "/plugins/{plugin_id}/analysis",
    dependencies=[Depends(require_plugin_mutation_access)],
)
async def retry_analysis(plugin_id: str):
    try:
        await asyncio.to_thread(plugin_source, plugin_id)
        await asyncio.to_thread(schedule_analysis, plugin_id, force=True)
        return await asyncio.to_thread(consent_details, plugin_id)
    except ServerDomainError as exc:
        raise_http_from_domain(exc, logger=logger)


@router.get("/notices")
async def get_consent_notices():
    try:
        return await asyncio.to_thread(consent_notices)
    except ServerDomainError as exc:
        raise_http_from_domain(exc, logger=logger)
