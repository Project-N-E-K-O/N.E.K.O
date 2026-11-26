"""Shared Main/Monitor user-model responses pinned to a checked open file."""

from __future__ import annotations

import os
from pathlib import Path
from secrets import token_hex

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import NotModifiedResponse, StaticFiles

from services.model_workshop.paths import (
    WorkshopError,
    WorkshopPaths,
    identity,
    regular,
)
from services.model_workshop.runtime import maintain, snapshot
from services.model_workshop.tasks import _drain_sync


class _PinnedModelFileResponse(FileResponse):
    """Keep FileResponse range/conditional semantics without reopening its pathname.

    A pre-check alone can race publication between lookup and response start.
    Open first, check current metadata, and stream that exact descriptor. The
    worker is drained on cancellation before closing the descriptor.
    """

    def __init__(self, *args, workshop_paths, conditional, **kwargs):
        super().__init__(*args, **kwargs)
        self._workshop_paths = workshop_paths
        self._conditional = conditional
        self._source = None

    def _open_checked_model(self):
        source = regular(Path(self.path))
        try:
            if (
                tuple(identity(self._workshop_paths.root))
                != self._workshop_paths.root_identity
            ):
                raise WorkshopError("model_workshop_root_changed")
            snapshot(paths=self._workshop_paths).require(Path(self.path))
            # Transfer ownership inside the worker, before a cancelled awaiter
            # can lose its result. __call__ closes it only after that worker ends.
            self._source = source
            return os.fstat(source.fileno())
        except BaseException:
            source.close()
            raise

    async def __call__(self, scope, receive, send):
        try:
            try:
                self.stat_result = await _drain_sync(self._open_checked_model)
            except WorkshopError:
                await JSONResponse(
                    {"success": False, "error_code": "model_workshop_unavailable"},
                    status_code=503,
                    headers={"Cache-Control": "no-store", "Retry-After": "1"},
                )(scope, receive, send)
                return
            except (FileNotFoundError, PermissionError):
                await JSONResponse({"detail": "Not Found"}, status_code=404)(
                    scope, receive, send
                )
                return
            # lookup metadata may belong to an older pathname. Rebuild these
            # three headers from the descriptor actually used for this response.
            for name in ("content-length", "last-modified", "etag"):
                if name in self.headers:
                    del self.headers[name]
            self.set_stat_headers(self.stat_result)
            if self._conditional(self.headers, Headers(scope=scope)):
                await NotModifiedResponse(self.headers)(scope, receive, send)
                return
            # The inherited parser/If-Range logic remains authoritative. Its
            # sendfile path is disabled by our overridden body handlers.
            await super().__call__(scope, receive, send)
        finally:
            if self._source is not None:
                await _drain_sync(self._source.close)

    async def _body(self, send, start=0, end=None):
        await _drain_sync(lambda: self._source.seek(start))
        while end is None or start < end:
            count = (
                self.chunk_size if end is None else min(self.chunk_size, end - start)
            )
            chunk = await _drain_sync(lambda: self._source.read(count))
            if not chunk:
                break
            start += len(chunk)
            await send({"type": "http.response.body", "body": chunk, "more_body": True})

    async def _handle_simple(self, send, send_header_only, send_pathsend):
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            }
        )
        if not send_header_only:
            await self._body(send)
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _handle_single_range(self, send, start, end, file_size, send_header_only):
        self.headers["content-range"] = f"bytes {start}-{end - 1}/{file_size}"
        self.headers["content-length"] = str(end - start)
        await send(
            {"type": "http.response.start", "status": 206, "headers": self.raw_headers}
        )
        if not send_header_only:
            await self._body(send, start, end)
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _handle_multiple_ranges(self, send, ranges, file_size, send_header_only):
        boundary = token_hex(13)
        length, header = self.generate_multipart(
            ranges, boundary, file_size, self.headers["content-type"]
        )
        headers = MutableHeaders(raw=list(self.raw_headers))
        headers["content-type"] = f"multipart/byteranges; boundary={boundary}"
        headers["content-length"] = str(length)
        await send(
            {"type": "http.response.start", "status": 206, "headers": headers.raw}
        )
        if not send_header_only:
            for start, end in ranges:
                await send(
                    {
                        "type": "http.response.body",
                        "body": header(start, end),
                        "more_body": True,
                    }
                )
                await self._body(send, start, end)
                await send(
                    {"type": "http.response.body", "body": b"\r\n", "more_body": True}
                )
        await send(
            {
                "type": "http.response.body",
                "body": b""
                if send_header_only
                else f"--{boundary}--".encode("latin-1"),
                "more_body": False,
            }
        )


class WorkshopStaticMixin:
    """Only user-model mounts opt in; repository/Steam assets keep their original class."""

    def __init__(self, *args, config_manager, maintain_main=False, **kwargs):
        super().__init__(*args, **kwargs)
        self._workshop_config = config_manager
        self._workshop_paths = WorkshopPaths.capture(config_manager)
        self._maintain_main = maintain_main

    async def get_response(self, path, scope):
        if self._maintain_main:
            await _drain_sync(lambda: maintain(self._workshop_config))
        original = await super().get_response(path, scope)
        if original.status_code not in (200, 304):
            return original
        full_path, info = await _drain_sync(lambda: self.lookup_path(path))
        if info is None:
            return JSONResponse({"detail": "Not Found"}, status_code=404, headers={"cache-control": "no-store"})
        # All user-model files are pinned, including unregistered ordinary
        # ones: an import beginning after lookup must not replace their handle.
        return _PinnedModelFileResponse(
            full_path,
            stat_result=info,
            headers=dict(original.headers),
            workshop_paths=self._workshop_paths,
            conditional=self.is_not_modified,
        )


class ReadOnlyWorkshopStaticFiles(WorkshopStaticMixin, StaticFiles):
    """Monitor uses this read-only adapter; it never acquires Main ownership."""


class ModelWorkshopRequestGate:
    """Main maintenance and named-model checks before legacy endpoint fallbacks.

    Request bodies remain owned by their existing handlers. This adapter only
    reads route/query identifiers; it never buffers multipart or changes caps.
    """

    def __init__(self, app, config_getter, maintain_main=True):
        self.app = app
        self.config_getter = config_getter
        self.maintain_main = maintain_main

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        kind, name = None, None
        for prefix, model_kind in (
            ("/api/live2d/", "live2d"),
            ("/api/model/vrm/", "vrm"),
            ("/api/model/mmd/", "mmd"),
        ):
            if path.startswith(prefix):
                kind = model_kind
                tail = path[len(prefix) :].strip("/").split("/")
                named = {
                    "model_config",
                    "emotion_mapping",
                    "model_files",
                    "model_parameters",
                    "load_model_parameters",
                    "save_model_parameters",
                    "upload_file",
                    "physics_profile",
                    "expressions",
                    "model_config_by_id",
                    "model_files_by_id",
                }
                if len(tail) == 2 and tail[0] in named:
                    name = tail[1]
                elif (
                    model_kind == "mmd"
                    and tail == ["emotion_mapping"]
                    and scope.get("method") == "GET"
                ):
                    from starlette.requests import Request

                    name = Request(scope).query_params.get("model")
                break
        if kind is not None:
            from services.model_workshop.runtime import require_identifier

            try:
                config = self.config_getter()
                if self.maintain_main:
                    await _drain_sync(lambda: maintain(config))
                if name:
                    await _drain_sync(lambda: require_identifier(config, kind, name))
            except WorkshopError as exc:
                await JSONResponse(
                    {"success": False, "error_code": str(exc)},
                    status_code=503,
                    headers={"Cache-Control": "no-store", "Retry-After": "1"},
                )(scope, receive, send)
                return
        rejected_response = False

        async def checked_send(message):
            nonlocal rejected_response
            if rejected_response:
                return
            if name and message["type"] == "http.response.start":
                # The route may have awaited I/O after the entry check. Check
                # again before sending bytes, including legacy fallback replies.
                try:
                    await _drain_sync(lambda: require_identifier(config, kind, name))
                except WorkshopError as exc:
                    rejected_response = True
                    await JSONResponse(
                        {"success": False, "error_code": str(exc)},
                        status_code=503,
                        headers={"Cache-Control": "no-store", "Retry-After": "1"},
                    )(scope, receive, send)
                    return
            await send(message)

        await self.app(scope, receive, checked_send if name else send)
