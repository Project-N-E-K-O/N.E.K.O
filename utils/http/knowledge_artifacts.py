"""Stream allowlisted knowledge artifacts without buffering them in each proxy."""

from __future__ import annotations

import json

from fastapi.responses import JSONResponse, StreamingResponse

MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


class _OwnedResponse(StreamingResponse):
    """Close the upstream even when sending headers fails before iteration."""

    def __init__(self, upstream, content, **kwargs):
        self.upstream = upstream
        super().__init__(content, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.upstream.aclose()


async def relay_artifact(client, url: str, *, params, headers=None, timeout=30.0):
    """Relay one trusted loopback artifact with a bounded error-body contract."""
    request = client.build_request("GET", url, params=params, headers=headers, timeout=timeout)
    upstream = await client.send(request, stream=True)
    if upstream.status_code != 200:
        try:
            body = bytearray()
            async for block in upstream.aiter_bytes():
                body.extend(block)
                if len(body) > 64 * 1024:
                    raise ValueError("oversized artifact error")
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("invalid artifact error")
            return JSONResponse(payload, status_code=upstream.status_code)
        except (ValueError, UnicodeError):
            return JSONResponse({"ok": False, "reason": "knowledge_invalid_response"}, status_code=502)
        finally:
            await upstream.aclose()
    declared = upstream.headers.get("content-length")
    if declared and (not declared.isdigit() or int(declared) > MAX_ARTIFACT_BYTES):
        await upstream.aclose()
        return JSONResponse({"ok": False, "reason": "payload_too_large"}, status_code=413)

    async def chunks():
        received = 0
        try:
            async for block in upstream.aiter_bytes(64 * 1024):
                received += len(block)
                if received > MAX_ARTIFACT_BYTES:
                    raise ValueError("artifact exceeded its bounded stream")
                yield block
        finally:
            await upstream.aclose()

    safe = {key: upstream.headers[key] for key in ("content-type", "content-length", "content-disposition") if key in upstream.headers}
    safe.update({"cache-control": "private, no-store", "x-content-type-options": "nosniff"})
    return _OwnedResponse(upstream, chunks(), headers=safe)
