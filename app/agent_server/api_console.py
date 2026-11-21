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

"""What the console pages read and change of the task agents: routes of the agent process.

The console (``frontend/plugin-manager``) is served by the plugin server and
talks to that server only; the plugin server passes ``/console/...`` on to
here (``plugin/server/routes/console_proxy.py``). Every route asks for the
token of this launch in ``X-CSRF-Token``: the plugin server adds it after
its own check that the request comes from a page of this application. A
page of another site that posts to this port has no way to read the token,
so it cannot change a permission or have dsh installed.

The router is built by ``build_console_router`` from what it needs (how to
get the host, the token), so that it is tested without the agent process:
this module imports nothing of it.

A refusal carries ``{"code": ...}`` as its detail; the page shows the text
it has for the code, in the user's language.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

logger = logging.getLogger(__name__)

TOKEN_HEADER = "X-CSRF-Token"
# How long the sizes of the directories are added up for, all of them
# together; what was not reached by then is reported as not measured.
_SIZE_BUDGET_SECONDS = 1.5
# The categories a page shows a row for. The setting of the MCP servers is
# not among them: there is nowhere yet to configure a server.
_SHOWN_CATEGORIES: tuple[str, ...] = (
    "workdir_files", "outside_read", "outside_write", "run_command",
    "keyboard_mouse", "browser", "plugin_entry", "managed_delivery", "other",
)
_UNSET = object()


def _refuse(status: int, code: str, **more: Any) -> HTTPException:
    return HTTPException(status, {"code": code, **more})


def _error_code(text: Any) -> str | None:
    """Return which kind of failure dsh's last error was, for the page to word it."""
    if not text:
        return None
    text = str(text)
    if "not released for this platform" in text:
        return "dsh_platform"
    if "could not be downloaded" in text:
        return "dsh_download"
    if "in use" in text:
        return "dsh_in_use"
    return "dsh_install"


def _measure(paths: list[Path], budget: float) -> list[tuple[int | None, bool]]:
    """Blocking: return ``(bytes, whole)`` for each directory; None for one that is not there."""
    deadline = time.monotonic() + budget
    measured: list[tuple[int | None, bool]] = []
    for path in paths:
        if not path.is_dir():
            measured.append((None, True))
            continue
        total, whole = 0, True
        try:
            for directory, _names, files in os.walk(path):
                if time.monotonic() > deadline:
                    whole = False
                    break
                for name in files:
                    try:
                        total += os.lstat(os.path.join(directory, name)).st_size
                    except OSError:
                        continue
        except OSError:
            whole = False
        measured.append((total, whole))
    return measured


def build_console_router(
    get_host: Callable[[], Awaitable[Any]],
    token: Callable[[], str],
    *,
    open_directory: Callable[[Path], None] | None = None,
) -> APIRouter:
    """Return the ``/console`` routes over the host ``get_host`` gives.

    ``token`` returns what ``X-CSRF-Token`` has to be. ``open_directory``
    shows a directory in the system's file manager (``utils.native_directory``
    by default).
    """

    def authorized(request: Request) -> None:
        given = request.headers.get(TOKEN_HEADER) or ""
        expected = token() or ""
        if not expected or not secrets.compare_digest(given.encode(), expected.encode()):
            raise _refuse(403, "forbidden")

    router = APIRouter(prefix="/console", dependencies=[Depends(authorized)])
    # The download that was started from here, and why the last one failed
    # when the installation itself does not say (dsh was in use).
    install: dict[str, Any] = {"task": None, "error": None}

    # -- the runtime ------------------------------------------------------

    async def runtime_view(host: Any) -> dict[str, Any]:
        status = await host.dsh_status()
        running = install["task"]
        if running is not None and not running.done():
            # From the press of the button on, not only once the first bytes arrive.
            status["downloading"] = True
        error = None if status.get("downloading") else status.get("last_error") or install["error"]
        return {
            "success": True,
            "dsh": {**status, "last_error": error, "last_error_code": _error_code(error)},
            "plugins": await host.dsh_plugins(),
        }

    @router.get("/runtime")
    async def get_runtime() -> dict[str, Any]:
        """Return which runtime new commissions use, what there is of dsh, and its plugins."""
        return await runtime_view(await get_host())

    @router.post("/runtime/preference")
    async def set_runtime_preference(payload: dict[str, Any]) -> dict[str, Any]:
        """Set which runtime new commissions run in: ``auto`` (dsh when installed) or ``builtin``."""
        host = await get_host()
        try:
            await host.set_dsh_preference(str((payload or {}).get("preference")))
        except ValueError:
            raise _refuse(400, "bad_preference") from None
        return await runtime_view(host)

    @router.post("/runtime/dsh/install")
    async def install_dsh() -> dict[str, Any]:
        """Start downloading and installing dsh; ``GET /console/runtime`` says how far it is."""
        host = await get_host()
        running = install["task"]
        if running is None or running.done():
            install["error"] = None

            async def work() -> None:
                try:
                    await host.install_dsh()
                except Exception as exc:  # noqa: BLE001 - shown to the user by the next status
                    install["error"] = str(exc)
                    logger.warning("installing dsh failed (exc_type=%s)", type(exc).__name__)

            install["task"] = asyncio.create_task(work())
        return await runtime_view(host)

    @router.post("/runtime/dsh/uninstall")
    async def uninstall_dsh(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Remove dsh; with ``purge`` also what it kept (its sessions, the plugins the user added)."""
        host = await get_host()
        running = install["task"]
        if running is not None and not running.done():
            raise _refuse(409, "dsh_downloading")
        try:
            await host.uninstall_dsh(purge=(payload or {}).get("purge") is True)
        except Exception as exc:  # noqa: BLE001 - DshUnavailable, imported nowhere here
            if type(exc).__name__ != "DshUnavailable":
                raise
            raise _refuse(409, _error_code(str(exc)) or "dsh_install") from None
        install["error"] = None
        return await runtime_view(host)

    # -- permissions ------------------------------------------------------

    async def permissions_view(host: Any) -> dict[str, Any]:
        from nekoclaw.host.permissions import AUTO_POLICY, MODES

        store = host.permission_store
        snapshot = await store.snapshot()
        rows = []
        for category in _SHOWN_CATEGORIES:
            rows.append({
                "category": category,
                "mode": snapshot["categories"].get(category, "auto"),
                # What "auto" comes to.
                "auto": AUTO_POLICY[category],
            })
        return {
            "success": True,
            "version": store.version,
            "unreadable": bool(snapshot.get("unreadable")),
            "modes": list(MODES),
            "categories": rows,
        }

    @router.get("/permissions")
    async def get_permissions() -> dict[str, Any]:
        """Return every category a page shows, with its setting and what ``auto`` comes to."""
        return await permissions_view(await get_host())

    @router.post("/permissions")
    async def set_permission(payload: dict[str, Any]) -> dict[str, Any]:
        """Set the mode of one category. ``expected_version`` refuses a change made on an older view."""
        from nekoclaw.host.permissions import MODES, PermissionFileError

        payload = payload or {}
        category, mode = payload.get("category"), payload.get("mode")
        if category not in _SHOWN_CATEGORIES:
            raise _refuse(400, "bad_category")
        if mode not in MODES:
            raise _refuse(400, "bad_mode")
        expected = payload.get("expected_version")
        if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int)):
            raise _refuse(400, "bad_version")
        host = await get_host()
        try:
            changed = await host.permission_store.set_mode(category, mode, expected_version=expected)
        except PermissionFileError:
            raise _refuse(409, "permissions_unreadable") from None
        if not changed:
            raise _refuse(409, "stale")
        return await permissions_view(host)

    # -- the model's window -----------------------------------------------

    @router.get("/window")
    async def get_window() -> dict[str, Any]:
        """Return what sessions of the task agent model are managed for; ``window`` is None without a model."""
        host = await get_host()
        return {"success": True, "window": await host.model_window()}

    @router.post("/window")
    async def set_window(payload: dict[str, Any]) -> dict[str, Any]:
        """Change the window, where a conversation is summed up, how long a session is idle before it is.

        Each of ``context_window``, ``compact_at`` (tokens) and
        ``idle_compact_after`` (seconds) that is given is set; ``null`` goes
        back to the setting.
        """
        payload = payload or {}
        changes: dict[str, Any] = {}
        for name in ("context_window", "compact_at", "idle_compact_after"):
            value = payload.get(name, _UNSET)
            if value is _UNSET:
                continue
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise _refuse(400, "bad_number", field=name)
            changes[name] = value
        if not changes:
            raise _refuse(400, "nothing_to_change")
        host = await get_host()
        try:
            window = await host.set_model_window(**changes)
        except LookupError:
            raise _refuse(409, "no_model") from None
        except ValueError as exc:
            raise _refuse(400, "out_of_range", message=str(exc)) from None
        return {"success": True, "window": window}

    # -- where things are -------------------------------------------------

    @router.get("/places")
    async def get_places() -> dict[str, Any]:
        """Return the directories the host keeps things in, and how much each holds."""
        host = await get_host()
        places = host.places()
        measured = await asyncio.to_thread(_measure, [Path(place["path"]) for place in places], _SIZE_BUDGET_SECONDS)
        return {
            "success": True,
            "places": [
                {**place, "exists": size is not None, "bytes": size, "bytes_whole": whole}
                for place, (size, whole) in zip(places, measured, strict=True)
            ],
        }

    @router.post("/places/open")
    async def open_place(payload: dict[str, Any]) -> dict[str, Any]:
        """Show one of those directories in the system's file manager. Only one of them, by its key."""
        host = await get_host()
        key = (payload or {}).get("key")
        path = next((Path(place["path"]) for place in host.places() if place["key"] == key), None)
        if path is None:
            raise _refuse(400, "bad_place")
        if not await asyncio.to_thread(path.is_dir):
            raise _refuse(404, "place_missing")
        opener = open_directory
        if opener is None:
            from utils.native_directory import open_directory as opener
        try:
            await asyncio.to_thread(opener, path)
        except Exception as exc:  # noqa: BLE001 - NativeDirectoryError, with its own code
            logger.warning("showing a directory failed (exc_type=%s)", type(exc).__name__)
            raise _refuse(500, "open_failed") from None
        return {"success": True}

    return router
