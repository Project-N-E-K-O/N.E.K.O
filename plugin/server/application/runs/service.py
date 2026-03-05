from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from pathlib import Path

from plugin._types.models import RunCreateRequest, RunCreateResponse
from plugin.logging_config import get_logger
from plugin.server.application.contracts import UploadBlobResponse, UploadSessionResponse
from plugin.server.domain.errors import ServerDomainError
from plugin.server.domain.normalization import coerce_optional_int, normalize_non_empty_str
from plugin.server.runs.manager import (
    ExportListResponse,
    RunRecord,
    cancel_run as manager_cancel_run,
    create_run as manager_create_run,
    get_run as manager_get_run,
    list_runs as manager_list_runs,
    list_export_for_run as manager_list_export_for_run,
)
from plugin.server.runs.storage import blob_store
from plugin.server.runs.tokens import issue_run_token

logger = get_logger("server.application.runs.service")


def _to_domain_error(
    *,
    code: str,
    message: str,
    status_code: int,
    details: dict[str, object] | None = None,
) -> ServerDomainError:
    return ServerDomainError(
        code=code,
        message=message,
        status_code=status_code,
        details=details or {},
    )


def _cleanup_tmp_upload_file(upload_id: str, file_path: Path) -> None:
    try:
        file_path.unlink(missing_ok=True)
    except FileNotFoundError:
        return
    except (PermissionError, OSError) as exc:
        logger.warning(
            "failed to cleanup temp upload file: upload_id={}, path={}, err_type={}, err={}",
            upload_id,
            str(file_path),
            type(exc).__name__,
            str(exc),
        )


class RunService:
    def list_runs(self, *, plugin_id: str | None) -> list[RunRecord]:
        normalized_plugin_id: str | None = None
        if isinstance(plugin_id, str):
            stripped = plugin_id.strip()
            if stripped:
                normalized_plugin_id = stripped

        try:
            return manager_list_runs(plugin_id=normalized_plugin_id)
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            logger.error(
                "list_runs failed: plugin_id={}, err_type={}, err={}",
                normalized_plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise _to_domain_error(
                code="RUN_LIST_FAILED",
                message="Failed to list runs",
                status_code=500,
                details={"plugin_id": normalized_plugin_id or "", "error_type": type(exc).__name__},
            ) from exc

    async def create_run(self, payload: RunCreateRequest, *, client_host: str | None) -> RunCreateResponse:
        try:
            base = await manager_create_run(payload, client_host=client_host)
            token, exp = issue_run_token(run_id=base.run_id, perm="read")
            return RunCreateResponse(
                run_id=base.run_id,
                status=base.status,
                run_token=token,
                expires_at=exp,
            )
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            logger.error(
                "create_run failed: plugin_id={}, entry_id={}, err_type={}, err={}",
                payload.plugin_id,
                payload.entry_id,
                type(exc).__name__,
                str(exc),
            )
            raise _to_domain_error(
                code="RUN_CREATE_FAILED",
                message="Failed to create run",
                status_code=500,
                details={"error_type": type(exc).__name__},
            ) from exc

    def get_run(self, run_id: str) -> RunRecord:
        rec = manager_get_run(run_id)
        if rec is None:
            raise _to_domain_error(
                code="RUN_NOT_FOUND",
                message="run not found",
                status_code=404,
                details={"run_id": run_id},
            )
        return rec

    def create_upload_session(
        self,
        *,
        run_id: str,
        base_url: str,
        body: dict[str, object] | None,
    ) -> UploadSessionResponse:
        rec = manager_get_run(run_id)
        if rec is None:
            raise _to_domain_error(
                code="RUN_NOT_FOUND",
                message="run not found",
                status_code=404,
                details={"run_id": run_id},
            )

        filename: str | None = None
        mime: str | None = None
        max_bytes: int | None = None
        if body is not None:
            filename = normalize_non_empty_str(body.get("filename"))
            mime = normalize_non_empty_str(body.get("mime"))
            max_bytes = coerce_optional_int(body.get("max_bytes"))

        try:
            session = blob_store.create_upload(
                run_id=run_id,
                filename=filename,
                mime=mime,
                max_bytes=max_bytes,
            )
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError) as exc:
            logger.error(
                "create_upload_session failed: run_id={}, err_type={}, err={}",
                run_id,
                type(exc).__name__,
                str(exc),
            )
            raise _to_domain_error(
                code="UPLOAD_SESSION_CREATE_FAILED",
                message="Failed to create upload session",
                status_code=500,
                details={"run_id": run_id, "error_type": type(exc).__name__},
            ) from exc

        normalized_base = base_url.rstrip("/")
        return {
            "upload_id": session.upload_id,
            "blob_id": session.blob_id,
            "upload_url": f"{normalized_base}/uploads/{session.upload_id}",
            "blob_url": f"{normalized_base}/runs/{run_id}/blobs/{session.blob_id}",
        }

    async def upload_blob(self, *, upload_id: str, chunks: AsyncIterable[bytes]) -> UploadBlobResponse:
        session = blob_store.get_upload(upload_id)
        if session is None:
            raise _to_domain_error(
                code="UPLOAD_NOT_FOUND",
                message="upload not found",
                status_code=404,
                details={"upload_id": upload_id},
            )

        rec = manager_get_run(session.run_id)
        if rec is None:
            raise _to_domain_error(
                code="RUN_NOT_FOUND",
                message="run not found",
                status_code=404,
                details={"run_id": session.run_id, "upload_id": upload_id},
            )
        if rec.status not in ("running", "cancel_requested"):
            raise _to_domain_error(
                code="RUN_NOT_RUNNING",
                message="run not running",
                status_code=409,
                details={"run_id": session.run_id, "status": rec.status},
            )

        total_bytes = 0
        try:
            with session.tmp_path.open("wb") as file_obj:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    if not isinstance(chunk, (bytes, bytearray)):
                        continue
                    total_bytes += len(chunk)
                    if total_bytes > int(session.max_bytes):
                        raise _to_domain_error(
                            code="UPLOAD_TOO_LARGE",
                            message="upload too large",
                            status_code=413,
                            details={"upload_id": upload_id, "max_bytes": int(session.max_bytes)},
                        )
                    await asyncio.to_thread(file_obj.write, bytes(chunk))

            blob_store.finalize_upload(upload_id)
            return {
                "ok": True,
                "upload_id": session.upload_id,
                "blob_id": session.blob_id,
                "size": total_bytes,
            }
        except ServerDomainError:
            _cleanup_tmp_upload_file(upload_id, session.tmp_path)
            raise
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError) as exc:
            _cleanup_tmp_upload_file(upload_id, session.tmp_path)
            logger.error(
                "upload_blob failed: upload_id={}, run_id={}, err_type={}, err={}",
                upload_id,
                session.run_id,
                type(exc).__name__,
                str(exc),
            )
            raise _to_domain_error(
                code="UPLOAD_WRITE_FAILED",
                message="Failed to upload blob",
                status_code=500,
                details={
                    "upload_id": upload_id,
                    "run_id": session.run_id,
                    "error_type": type(exc).__name__,
                },
            ) from exc

    def get_blob_path(self, *, run_id: str, blob_id: str) -> Path:
        path = blob_store.get_blob_path(run_id=run_id, blob_id=blob_id)
        if path is None:
            raise _to_domain_error(
                code="BLOB_NOT_FOUND",
                message="blob not found",
                status_code=404,
                details={"run_id": run_id, "blob_id": blob_id},
            )
        return path

    def cancel_run(self, run_id: str, *, reason: str | None) -> RunRecord:
        rec = manager_cancel_run(run_id, reason=reason)
        if rec is None:
            raise _to_domain_error(
                code="RUN_NOT_FOUND",
                message="run not found",
                status_code=404,
                details={"run_id": run_id},
            )
        return rec

    def list_export_for_run(
        self,
        *,
        run_id: str,
        after: str | None,
        limit: int,
    ) -> ExportListResponse:
        rec = manager_get_run(run_id)
        if rec is None:
            raise _to_domain_error(
                code="RUN_NOT_FOUND",
                message="run not found",
                status_code=404,
                details={"run_id": run_id},
            )

        try:
            return manager_list_export_for_run(run_id=run_id, after=after, limit=limit)
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            logger.error(
                "list_export_for_run failed: run_id={}, err_type={}, err={}",
                run_id,
                type(exc).__name__,
                str(exc),
            )
            raise _to_domain_error(
                code="RUN_EXPORT_LIST_FAILED",
                message="Failed to list export items",
                status_code=500,
                details={"run_id": run_id, "error_type": type(exc).__name__},
            ) from exc
