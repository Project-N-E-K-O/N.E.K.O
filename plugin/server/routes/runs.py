"""
Run Protocol 路由
"""
from __future__ import annotations

import json
from typing import NoReturn, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import FileResponse

from plugin._types.models import RunCreateRequest, RunCreateResponse
from plugin.logging_config import get_logger
from plugin.server.application.runs import RunService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.runs.manager import RunCancelRequest, RunRecord

router = APIRouter()
logger = get_logger("server.routes.runs")
run_service = RunService()


def _raise_http_from_domain(error: ServerDomainError) -> NoReturn:
    logger.warning(
        "Domain error: code={}, status_code={}, message={}",
        error.code,
        error.status_code,
        error.message,
    )
    raise HTTPException(status_code=error.status_code, detail=error.message)


@router.post("/runs", response_model=RunCreateResponse)
async def runs_create(payload: RunCreateRequest, request: Request) -> RunCreateResponse:
    try:
        client_host = request.client.host if request.client is not None else None
        return await run_service.create_run(payload, client_host=client_host)
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.get("/runs/{run_id}", response_model=RunRecord)
async def runs_get(run_id: str) -> RunRecord:
    try:
        return run_service.get_run(run_id)
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.post("/runs/{run_id}/uploads")
async def runs_create_upload(run_id: str, request: Request) -> dict[str, object]:
    raw_body: object | None
    try:
        raw_body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError, RuntimeError):
        raw_body = None

    body: dict[str, object] | None = None
    if isinstance(raw_body, dict):
        body = {str(key): value for key, value in raw_body.items()}

    try:
        return run_service.create_upload_session(
            run_id=run_id,
            base_url=str(request.base_url),
            body=body,
        )
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.put("/uploads/{upload_id}")
async def uploads_put(upload_id: str, request: Request) -> dict[str, object]:
    try:
        return await run_service.upload_blob(upload_id=upload_id, chunks=request.stream())
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.get("/runs/{run_id}/blobs/{blob_id}")
async def runs_get_blob(run_id: str, blob_id: str) -> FileResponse:
    try:
        path = run_service.get_blob_path(run_id=run_id, blob_id=blob_id)
        return FileResponse(str(path), filename=f"{blob_id}.bin")
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.post("/runs/{run_id}/cancel", response_model=RunRecord)
async def runs_cancel(run_id: str, payload: RunCancelRequest = Body(default=RunCancelRequest())) -> RunRecord:
    try:
        return run_service.cancel_run(run_id, reason=payload.reason)
    except ServerDomainError as error:
        _raise_http_from_domain(error)


@router.get("/runs/{run_id}/export")
async def runs_export(
    run_id: str,
    after: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
) -> dict[str, object]:
    try:
        response = run_service.list_export_for_run(
            run_id=run_id,
            after=after,
            limit=int(limit),
        )
        return response.model_dump(by_alias=True)
    except ServerDomainError as error:
        _raise_http_from_domain(error)
