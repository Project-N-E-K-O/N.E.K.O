"""
Run Protocol 路由
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query, Body
from fastapi.responses import FileResponse
from loguru import logger

from plugin._types.models import RunCreateRequest, RunCreateResponse
from plugin.server.infrastructure.error_handler import handle_plugin_error
from plugin.server.runs.manager import (
    RunCancelRequest,
    RunRecord,
    ExportListResponse,
    create_run,
    get_run,
    cancel_run,
    list_export_for_run,
)
from plugin.server.runs.websocket import issue_run_token
from plugin.server.runs.storage import blob_store

router = APIRouter()


@router.post("/runs", response_model=RunCreateResponse)
async def runs_create(payload: RunCreateRequest, request: Request):
    try:
        client_host = request.client.host if request.client else None
        base = await create_run(payload, client_host=client_host)
        token, exp = issue_run_token(run_id=base.run_id, perm="read")
        return RunCreateResponse(run_id=base.run_id, status=base.status, run_token=token, expires_at=exp)
    except Exception as e:
        logger.error(f"Error creating run: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{run_id}", response_model=RunRecord)
async def runs_get(run_id: str):
    try:
        rec = get_run(run_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="run not found")
        return rec
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get run")
        raise handle_plugin_error(e, "Failed to get run", 500) from e


@router.post("/runs/{run_id}/uploads")
async def runs_create_upload(run_id: str, request: Request):
    try:
        rec = get_run(run_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="run not found")

        body = None
        try:
            body = await request.json()
        except Exception:
            body = None
        filename = None
        mime = None
        max_bytes = None
        if isinstance(body, dict):
            filename = body.get("filename")
            mime = body.get("mime")
            max_bytes = body.get("max_bytes")

        sess = blob_store.create_upload(run_id=run_id, filename=filename, mime=mime, max_bytes=max_bytes)
        base = str(request.base_url).rstrip("/")
        upload_url = f"{base}/uploads/{sess.upload_id}"
        blob_url = f"{base}/runs/{run_id}/blobs/{sess.blob_id}"
        return {"upload_id": sess.upload_id, "blob_id": sess.blob_id, "upload_url": upload_url, "blob_url": blob_url}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create upload")
        raise handle_plugin_error(e, "Failed to create upload", 500) from e


@router.put("/uploads/{upload_id}")
async def uploads_put(upload_id: str, request: Request):
    sess = blob_store.get_upload(upload_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="upload not found")

    rec = get_run(sess.run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run not found")
    if rec.status not in ("running", "cancel_requested"):
        raise HTTPException(status_code=409, detail="run not running")

    try:
        total = 0
        with sess.tmp_path.open("wb") as f:
            async for chunk in request.stream():
                if not chunk:
                    continue
                if not isinstance(chunk, (bytes, bytearray)):
                    continue
                total += len(chunk)
                if total > int(sess.max_bytes):
                    raise HTTPException(status_code=413, detail="upload too large")
                f.write(chunk)
        blob_store.finalize_upload(upload_id)
        return {"ok": True, "upload_id": sess.upload_id, "blob_id": sess.blob_id, "size": total}
    except HTTPException:
        try:
            if sess.tmp_path.exists():
                sess.tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            if sess.tmp_path.exists():
                sess.tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        logger.exception("Failed to upload blob")
        raise handle_plugin_error(e, "Failed to upload blob", 500) from e


@router.get("/runs/{run_id}/blobs/{blob_id}")
async def runs_get_blob(run_id: str, blob_id: str):
    try:
        p = blob_store.get_blob_path(run_id=run_id, blob_id=blob_id)
        if p is None:
            raise HTTPException(status_code=404, detail="blob not found")
        return FileResponse(str(p), filename=f"{blob_id}.bin")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to download blob")
        raise handle_plugin_error(e, "Failed to download blob", 500) from e


@router.post("/runs/{run_id}/cancel", response_model=RunRecord)
async def runs_cancel(run_id: str, payload: RunCancelRequest = Body(default=RunCancelRequest())):
    try:
        rec = cancel_run(run_id, reason=payload.reason)
        if rec is None:
            raise HTTPException(status_code=404, detail="run not found")
        return rec
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to cancel run")
        raise handle_plugin_error(e, "Failed to cancel run", 500) from e


@router.get("/runs/{run_id}/export")
async def runs_export(
    run_id: str,
    after: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
):
    try:
        rec = get_run(run_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="run not found")
        resp = list_export_for_run(run_id=run_id, after=after, limit=int(limit))
        return resp.model_dump(by_alias=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to list export items")
        raise handle_plugin_error(e, "Failed to list export items", 500) from e
