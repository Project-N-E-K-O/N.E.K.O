# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Host API of Theme Studio (``/api/theme_studio``).

These endpoints used to be "plugin host + plugin entries"; the host now serves
them directly:

* ``GET  /theme.json``                 palette polled by the loader every 1.5 s.
* ``GET  /palette``                    REST alias of the above.
* ``POST /run/{entry_id}``             dispatch of a registered entry (writes).
* ``GET  /backgrounds/{filename}``     chat background images.
* ``GET  /presets/{filename}``         raw built-in theme JSON (hover preview).
* ``GET  /presets/images/{filename}``  preview images shipped with built-ins.
* ``GET  /themes/{filename}``          raw JSON of one user library theme.
* ``GET  /entries``                    registered entry ids (debugging).

Access: the loader runs in every window, including remote / reverse-proxy
clients, so the read-only endpoints are not restricted to loopback (doing so
would 403 every window every 1.5 s and flood the security warning log). Only
``/run/{entry_id}``, which can write the palette, library and images, keeps
``verify_local_access``.

All input is untrusted; content validation happens in the service layer
(variable whitelist, value formats, zip limits). This module only guards that
file paths cannot escape their directory. Every handler that touches the disk
runs that work in a worker thread so the shared event loop never blocks.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from main_routers.cookies_login_router import verify_local_access
from theme_studio import (
    backgrounds_dir,
    entries,
    get_theme_document_async,
    library_dir,
    presets_dir,
    run_entry,
    startup_service_async,
)

router = APIRouter(
    prefix="/api/theme_studio",
    tags=["theme-studio"],
)

#: Background / preset / library file names: letters, digits, dot, underscore,
#: dash only. Rules out ``..`` and path separators at the source.
_RE_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_RE_ENTRY_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _no_store(payload: dict[str, Any]) -> JSONResponse:
    """Palette response; must not be cached or the 1.5 s poll reads stale data."""
    response = JSONResponse(content=payload)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def _safe_file_sync(directory: Path, filename: str) -> Path:
    """Resolve ``directory / filename`` and make sure it stays inside the directory."""
    if not filename or not _RE_SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=400, detail="invalid filename")
    root = Path(directory).resolve()
    candidate = (root / filename).resolve()
    if candidate.parent != root:
        raise HTTPException(status_code=400, detail="invalid filename")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return candidate


async def _safe_file(directory: Path, filename: str) -> Path:
    """:func:`_safe_file_sync` run in a worker thread (it stats the disk)."""
    return await asyncio.to_thread(_safe_file_sync, directory, filename)


@router.get("/theme.json")
async def get_theme_json() -> JSONResponse:
    """Current palette; the loader and the panel both read this endpoint."""
    return _no_store(await get_theme_document_async())


@router.get("/palette")
async def get_palette() -> JSONResponse:
    """REST alias of ``/theme.json`` for scripts and debugging."""
    return _no_store(await get_theme_document_async())


@router.post("/run/{entry_id}", dependencies=[Depends(verify_local_access)])
async def run_theme_entry(
    entry_id: str,
    args: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """Dispatch one registered entry; replies ``{ok, result}`` or ``{ok: false, error}``.

    Unlike the plugin run protocol this answers synchronously (no run id +
    polling); the work itself runs in a worker thread.
    """
    if not _RE_ENTRY_ID.match(entry_id or ""):
        raise HTTPException(status_code=400, detail="invalid entry id")
    result = await run_entry(entry_id, args)
    if result.ok:
        return JSONResponse(content={"ok": True, "result": result.value})
    error = result.error
    message = str(error) if error is not None else "操作失败"
    return JSONResponse(
        content={"ok": False, "error": message, "entry_id": entry_id},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/entries")
async def list_theme_entries() -> dict[str, Any]:
    """Registered entry ids (panel self-check / debugging)."""
    return {"count": len(entries()), "entries": entries()}


@router.get("/backgrounds/{filename}")
async def get_theme_background(filename: str) -> FileResponse:
    """Chat background image; the loader writes this URL into ``--nts-chat-bg-image``."""
    await startup_service_async()
    path = await _safe_file(backgrounds_dir(), filename)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})


# ---------------------------------------------------------------------------
# Read-only theme files
#
# Hover preview needs the raw JSON of a single theme (``presets/<id>.json`` and
# ``themes/<id>.json``) plus ``presets/images/<hash>``. Built-ins live in the
# code tree (read-only) and the library lives in the user data directory, so
# each gets its own route. These are plain file reads instead of entries
# because hovering is frequent and entry dispatch (with validation) is slower.
# ---------------------------------------------------------------------------


@router.get("/presets/{filename}")
async def get_theme_preset(filename: str) -> FileResponse:
    """Raw JSON of a built-in theme (read-only, shipped with the app)."""
    path = await _safe_file(presets_dir(), filename)
    return FileResponse(
        path,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/presets/images/{filename}")
async def get_theme_preset_image(filename: str) -> FileResponse:
    """Preview image shipped with a built-in theme."""
    path = await _safe_file(presets_dir() / "images", filename)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/themes/{filename}")
async def get_theme_library_item(filename: str) -> FileResponse:
    """Raw JSON of one user library theme (hover preview)."""
    await startup_service_async()
    path = await _safe_file(library_dir(), filename)
    return FileResponse(
        path, media_type="application/json", headers={"Cache-Control": "no-store"}
    )
