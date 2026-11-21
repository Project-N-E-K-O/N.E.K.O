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
# The longest text a page may send for one field (rules, an entry): what
# is kept is a file a person wrote, not a store of documents.
_TEXT_LIMIT = 200_000


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
    switches: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    read_browser: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    write_browser: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> APIRouter:
    """Return the ``/console`` routes over the host ``get_host`` gives.

    ``token`` returns what ``X-CSRF-Token`` has to be. ``open_directory``
    shows a directory in the system's file manager (``utils.native_directory``
    by default). ``switches`` returns where the switches of the agent
    process stand; ``read_browser`` and ``write_browser`` are its routes for
    the browser settings. Without them those routes answer 404: a host on
    its own has no switches.
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

    # -- the switches, and the browser the tools use -----------------------
    #
    # Read here; a switch is changed through the main server
    # (``/api/agent/command``), which keeps its sessions in step.

    @router.get("/switches")
    async def get_switches() -> dict[str, Any]:
        """Return where the switches stand and why a capability is not ready. Nothing is used up by reading."""
        if switches is None:
            raise _refuse(404, "no_switches")
        return {"success": True, "switches": await switches()}

    @router.get("/browser")
    async def get_browser() -> dict[str, Any]:
        """Return which browser the browser tools use and how, with the browsers found on this computer."""
        if read_browser is None:
            raise _refuse(404, "no_switches")
        return {"success": True, **(await read_browser())}

    @router.post("/browser")
    async def set_browser(payload: dict[str, Any]) -> dict[str, Any]:
        """Change which browser the browser tools use and how."""
        if write_browser is None:
            raise _refuse(404, "no_switches")
        try:
            changed = await write_browser(payload or {})
        except HTTPException as exc:
            if exc.status_code != 400:
                raise
            raise _refuse(400, "bad_browser_setting", message=str(exc.detail)) from None
        return {"success": True, **{key: value for key, value in changed.items() if key != "success"}}

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

    # -- the task agent space ---------------------------------------------
    #
    # ``project`` names a project, none of it the space all projects share;
    # an archived project is named by its directory and ``archive_year``.

    def scope(project: Any, archive_year: Any = None) -> tuple[str | None, str | None]:
        for value in (project, archive_year):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise _refuse(400, "bad_name")
        return project, archive_year

    async def space_call(work: Awaitable[Any]) -> Any:
        """Await a call into the space; what it refuses is told with a code."""
        from nekoclaw.host.space import SpaceError

        try:
            return await work
        except SpaceError as exc:
            raise _refuse(400, "space_refused", message=str(exc)) from None
        except ValueError:
            # A name that is no name of a directory (``safe_segment``).
            raise _refuse(400, "bad_name") from None

    def text_of(payload: dict[str, Any], name: str, *, required: bool = False) -> str | None:
        value = payload.get(name)
        if value is None and not required:
            return None
        if not isinstance(value, str) or len(value) > _TEXT_LIMIT:
            raise _refuse(400, "bad_text", field=name)
        return value

    @router.get("/space/projects")
    async def list_projects() -> dict[str, Any]:
        """Return the projects, the archived ones last."""
        host = await get_host()
        projects = await space_call(host.space.list_projects())
        return {"success": True, "projects": [project.to_json() for project in projects]}

    @router.get("/space/rules")
    async def read_rules(project: str | None = None) -> dict[str, Any]:
        """Return the rules the user wrote for a project, or for every task agent."""
        host = await get_host()
        project, _ = scope(project)
        return {"success": True, "text": await space_call(host.space.read_rules(project))}

    @router.put("/space/rules")
    async def write_rules(payload: dict[str, Any]) -> dict[str, Any]:
        """Write the rules as the user gave them."""
        payload = payload or {}
        project, _ = scope(payload.get("project"))
        text = text_of(payload, "text", required=True)
        host = await get_host()
        await space_call(host.space.write_rules(text, project))
        return {"success": True, "text": await space_call(host.space.read_rules(project))}

    @router.get("/space/index")
    async def read_index(project: str | None = None) -> dict[str, Any]:
        """Return the index a task agent is given when it starts."""
        host = await get_host()
        project, _ = scope(project)
        return {"success": True, "text": await space_call(host.space.read_index(project))}

    @router.get("/space/entries")
    async def list_entries(project: str | None = None, archive_year: str | None = None) -> dict[str, Any]:
        """Return the entries of a project or of the space, without their text."""
        host = await get_host()
        project, archive_year = scope(project, archive_year)
        entries = await space_call(host.space.list_entries(project, archive_year=archive_year))
        return {"success": True, "entries": [entry.to_json() for entry in entries]}

    @router.post("/space/entries")
    async def file_entry(payload: dict[str, Any]) -> dict[str, Any]:
        """Keep an entry the user wrote."""
        from nekoclaw.host.space import ENTRY_TYPES

        payload = payload or {}
        project, _ = scope(payload.get("project"))
        title = text_of(payload, "title", required=True)
        content = text_of(payload, "content", required=True)
        kind = payload.get("kind", ENTRY_TYPES[0])
        if kind not in ENTRY_TYPES:
            raise _refuse(400, "bad_kind")
        if not title.strip():
            raise _refuse(400, "bad_text", field="title")
        host = await get_host()
        entry, _new = await space_call(host.space.file_entry(
            project, title=title, content=content, kind=kind, description=text_of(payload, "description") or "",
        ))
        return {"success": True, "entry": entry.to_json()}

    @router.get("/space/entries/{entry_id}")
    async def read_entry(entry_id: str, project: str | None = None, archive_year: str | None = None) -> dict[str, Any]:
        """Return one entry with its text."""
        host = await get_host()
        project, archive_year = scope(project, archive_year)
        entry = await space_call(host.space.read_entry(entry_id, project, archive_year=archive_year))
        if entry is None:
            raise _refuse(404, "no_entry")
        return {"success": True, "entry": entry.to_json()}

    @router.patch("/space/entries/{entry_id}")
    async def edit_entry(entry_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Change an entry: each of ``title``, ``description``, ``kind``, ``content`` that is given."""
        from nekoclaw.host.space import ENTRY_TYPES

        payload = payload or {}
        project, archive_year = scope(payload.get("project"), payload.get("archive_year"))
        changes = {name: text_of(payload, name) for name in ("title", "description", "content")}
        changes = {name: value for name, value in changes.items() if value is not None}
        if payload.get("kind") is not None:
            if payload["kind"] not in ENTRY_TYPES:
                raise _refuse(400, "bad_kind")
            changes["kind"] = payload["kind"]
        if not changes:
            raise _refuse(400, "nothing_to_change")
        if "title" in changes and not changes["title"].strip():
            raise _refuse(400, "bad_text", field="title")
        host = await get_host()
        entry = await space_call(host.space.edit_entry(entry_id, project, archive_year=archive_year, **changes))
        if entry is None:
            raise _refuse(404, "no_entry")
        return {"success": True, "entry": entry.to_json()}

    @router.delete("/space/entries/{entry_id}")
    async def delete_entry(entry_id: str, project: str | None = None, archive_year: str | None = None) -> dict[str, Any]:
        """Remove an entry. The same note is not kept again when a task agent files it."""
        host = await get_host()
        project, archive_year = scope(project, archive_year)
        if not await space_call(host.space.delete_entry(entry_id, project, archive_year=archive_year)):
            raise _refuse(404, "no_entry")
        return {"success": True}

    @router.get("/space/removed")
    async def read_removed(project: str | None = None, archive_year: str | None = None) -> dict[str, Any]:
        """Return what is remembered of the entries the user removed, which are not kept again."""
        host = await get_host()
        project, archive_year = scope(project, archive_year)
        notes = await space_call(host.space.read_notes(project, archive_year=archive_year))
        return {"success": True, "removed": notes.get("removed", [])}

    @router.get("/space/reviews")
    async def list_reviews(project: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Return the reviews of a project, or of every project, newest first, without their text."""
        host = await get_host()
        project, _ = scope(project)
        reviews = await space_call(host.space.list_reviews(project, limit=max(1, min(int(limit), 500))))
        return {"success": True, "reviews": [review.to_json() for review in reviews]}

    @router.get("/space/reviews/{commission_id}")
    async def read_review(commission_id: str) -> dict[str, Any]:
        """Return the review of a commission with its text."""
        host = await get_host()
        review = await space_call(host.space.read_review(commission_id))
        if review is None:
            raise _refuse(404, "no_review")
        return {"success": True, "review": review.to_json()}

    @router.delete("/space/reviews/{commission_id}")
    async def delete_review(commission_id: str) -> dict[str, Any]:
        """Remove the review of a commission."""
        host = await get_host()
        if not await space_call(host.space.delete_review(commission_id)):
            raise _refuse(404, "no_review")
        return {"success": True}

    # -- how she likes tasks done -----------------------------------------

    def preference_json(preference: Any) -> dict[str, Any]:
        return {
            "id": preference.id, "kind": preference.kind, "text": preference.text, "origin": preference.origin,
            "observations": preference.observations, "active": preference.active,
            "lanlan_name": preference.lanlan_name, "updated_at": preference.updated_at,
        }

    @router.get("/preferences")
    async def list_preferences() -> dict[str, Any]:
        """Return what was kept of how the user wants tasks done."""
        host = await get_host()
        return {"success": True, "preferences": [preference_json(item) for item in await host.preferences.list()]}

    @router.patch("/preferences/{preference_id}")
    async def edit_preference(preference_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Change a preference: its ``text``, and whether it is ``active``."""
        payload = payload or {}
        text = text_of(payload, "text")
        active = payload.get("active")
        if active is not None and not isinstance(active, bool):
            raise _refuse(400, "bad_flag")
        if text is None and active is None:
            raise _refuse(400, "nothing_to_change")
        if text is not None and not text.strip():
            raise _refuse(400, "bad_text", field="text")
        host = await get_host()
        preference = await host.preferences.get(preference_id)
        if preference is None:
            raise _refuse(404, "no_preference")
        if text is not None:
            preference = await host.preferences.edit(preference_id, text=text)
        if active is not None and preference is not None:
            preference = await host.preferences.set_active(preference_id, active)
        if preference is None:
            raise _refuse(404, "no_preference")
        return {"success": True, "preference": preference_json(preference)}

    @router.delete("/preferences/{preference_id}")
    async def remove_preference(preference_id: int) -> dict[str, Any]:
        """Forget a preference."""
        host = await get_host()
        if not await host.preferences.remove(preference_id):
            raise _refuse(404, "no_preference")
        return {"success": True}

    return router
