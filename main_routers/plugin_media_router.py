# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Same-origin adapter for temporary plugin images.

The plugin server mints ``http://127.0.0.1:<plugin-port>/media/<id>`` and that
address works for the MAIN SERVER, which fetches it in-process on the same
host. It does not work for the BROWSER whenever the browser is somewhere else
-- a Docker deployment, or a phone on the same network -- because 127.0.0.1
then resolves to the viewer's own machine. Under HTTPS it is additionally
blocked as mixed content.

The failure is asymmetric and that is what makes it expensive: the model fetch
succeeds while the picture does not render, so the character describes an image
the user cannot see and the user has no way to tell why.

This route gives the browser a same-origin path instead. It works in the
desktop build (where nginx is absent and this server answers directly) and
behind the container's proxy (where ``/`` already forwards here), so no
deployment-specific URL and no new proxy rule are needed.
"""
from __future__ import annotations

import asyncio
import re

import httpx
from fastapi import APIRouter, HTTPException
from starlette.responses import Response

from config.network import resolve_user_plugin_base
from utils.http.internal_client import get_internal_http_client


router = APIRouter(tags=["plugin-media"])

# Ids are minted by the plugin image store as hex tokens. Constraining the
# shape here keeps this route from being turned into a general proxy for
# whatever path a caller can spell.
_IMAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# One temporary image. The plugin SDK normalizes uploads to at most 8 MiB, so
# anything past this did not come from the upload path.
_MAX_MEDIA_BYTES = 8 * 1024 * 1024

# Per-read idle timeout, and a TOTAL deadline over the whole exchange.
#
# httpx's timeout bounds one read, not the transfer: an upstream trickling a
# few bytes every 2.9s satisfies it forever and holds the connection and the
# task with it (CodeRabbit). Both are needed -- the idle timeout catches a
# stalled peer quickly, the deadline catches a slow one at all.
_FETCH_TIMEOUT_S = 3.0
_TOTAL_DEADLINE_S = 10.0


@router.get("/media/{image_id}")
async def get_plugin_media(image_id: str) -> Response:
    """Stream one temporary plugin image from the plugin server."""
    if not _IMAGE_ID_PATTERN.match(image_id):
        raise HTTPException(status_code=404, detail="temporary image not found")

    # Resolved the SAME way the URL was minted. plugin/core/communication.py
    # reads NEKO_USER_PLUGIN_SERVER_PORT before falling back to the configured
    # base, because the plugin server rewrites that variable when its preferred
    # port is busy. Resolving any other way sends the proxy to the port that
    # did NOT mint the id -- which is exactly where the image is not (Codex).
    #
    # The main server's own resolver implements that rule, so this defers to it
    # rather than adding a fourth spelling that can drift from the minter.
    base = resolve_user_plugin_base().rstrip("/")
    client = get_internal_http_client()
    try:
        async with asyncio.timeout(_TOTAL_DEADLINE_S), client.stream(
            "GET",
            f"{base}/media/{image_id}",
            timeout=_FETCH_TIMEOUT_S,
            follow_redirects=False,
        ) as upstream:
            if upstream.status_code == 404:
                raise HTTPException(status_code=404, detail="temporary image not found")
            upstream.raise_for_status()

            content_type = str(upstream.headers.get("content-type") or "").lower()
            if not content_type.startswith("image/"):
                # The plugin media store only ever serves images; anything else
                # means this is not the store, so do not relay it to a browser.
                raise HTTPException(status_code=502, detail="unexpected media response")

            buffered = bytearray()
            async for chunk in upstream.aiter_bytes():
                buffered.extend(chunk)
                if len(buffered) > _MAX_MEDIA_BYTES:
                    raise HTTPException(status_code=502, detail="media too large")
    except HTTPException:
        raise
    except (TimeoutError, httpx.TimeoutException):
        # httpx.TimeoutException does NOT inherit from the builtin TimeoutError
        # (TransportError -> RequestError -> HTTPError), so connect/read/pool
        # timeouts would otherwise fall into the generic branch and report 502
        # -- hiding a slow upstream behind the same code as a broken one
        # (CodeRabbit). asyncio.TimeoutError IS the builtin on 3.11, so the
        # total deadline above is already covered by the first arm.
        raise HTTPException(status_code=504, detail="plugin media timed out") from None
    except Exception:
        raise HTTPException(status_code=502, detail="plugin media unavailable") from None

    return Response(
        content=bytes(buffered),
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )
