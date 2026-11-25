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
import math
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
# The categories the permission table shows a row for. The setting of the
# MCP servers is not among them: it is shown with the servers (``/mcp``).
_SHOWN_CATEGORIES: tuple[str, ...] = (
    "workdir_files", "outside_read", "outside_write", "run_command",
    "keyboard_mouse", "browser", "plugin_entry", "managed_delivery", "other", "open_session",
)
_UNSET = object()
# No setting of a page is a number beyond this; the largest number a row of the ledger is counted by.
_NUMBER_MAX = 10**15
_ROW_MAX = 2**63 - 1
# The longest text a page may send for one field (rules, an entry): what
# is kept is a file a person wrote, not a store of documents.
_TEXT_LIMIT = 200_000
# The longest words a page may look for in what was said.
_QUERY_LIMIT = 500


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
    pick_directory: Callable[[str], str | None] | None = None,
    character: Callable[[], Awaitable[str | None]] | None = None,
    switches: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    read_browser: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    write_browser: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> APIRouter:
    """Return the ``/console`` routes over the host ``get_host`` gives.

    ``token`` returns what ``X-CSRF-Token`` has to be. ``open_directory``
    shows a directory in the system's file manager (``utils.native_directory``
    by default); ``pick_directory`` shows the system's dialog for choosing
    a folder, starting at the directory it is given, and returns what was
    chosen or None when the user closed it. ``character`` returns the name
    of the character the user is with now, whose task agent a new one is
    when the page does not say. ``switches`` returns where the switches of the agent
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
        from nekopower.host.permissions import AUTO_POLICY, MODES

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
        from nekopower.host.permissions import MODES, PermissionFileError

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
        except OSError as exc:
            # The file could not be written; the setting is still what it was.
            logger.warning("permission setting for %s could not be saved: %s", category, type(exc).__name__)
            raise _refuse(409, "permissions_not_saved") from None
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
        back to the setting. ``idle_compact_on`` (true or false) switches
        summing up idle sessions on or off. ``model_id`` is the ``id``
        of the window the page shows; when the model configured now is
        another (or the same name at another address), nothing is
        changed and the answer is 409 ``stale``.
        """
        payload = payload or {}
        changes: dict[str, Any] = {}
        if "idle_compact_on" in payload:
            if not isinstance(payload["idle_compact_on"], bool):
                raise _refuse(400, "bad_flag", field="idle_compact_on")
            changes["idle_compact_on"] = payload["idle_compact_on"]
        for name in ("context_window", "compact_at", "idle_compact_after"):
            value = payload.get(name, _UNSET)
            if value is _UNSET:
                continue
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise _refuse(400, "bad_number", field=name)
            # Told here: further in, a number too long to be a float is an error of another kind.
            if value is not None and not (isinstance(value, int) or math.isfinite(value)) or (
                value is not None and abs(value) > _NUMBER_MAX
            ):
                raise _refuse(400, "out_of_range", field=name)
            changes[name] = value
        if not changes:
            raise _refuse(400, "nothing_to_change")
        # The model the page read the settings of: they are not written to another one.
        expected = payload.get("model_id")
        if expected is not None:
            if not isinstance(expected, str) or not expected:
                raise _refuse(400, "bad_name", field="model_id")
            changes["expected_model"] = expected
        host = await get_host()
        try:
            window = await host.set_model_window(**changes)
        except LookupError:
            raise _refuse(409, "no_model") from None
        except RuntimeError as exc:
            if type(exc).__name__ != "ModelChanged":
                raise
            raise _refuse(409, "stale", model=str(exc)) from None
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

    async def there(host: Any, project: str | None) -> None:
        """Refuse to write for a project that is not there (or is archived).

        Writing would make its directory: a project nobody began, which
        then stands in the way of the archived one of its name.
        """
        if project is not None and await space_call(host.space.get_project(project)) is None:
            raise _refuse(404, "no_project")

    async def space_call(work: Awaitable[Any]) -> Any:
        """Await a call into the space; what it refuses is told with a code."""
        from nekopower.host.space import SpaceError

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

    @router.post("/space/pick-directory")
    async def choose_directory(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Show the system's dialog for choosing a folder, on the computer the application runs on.

        Returns ``path``, or ``cancelled`` when the user closed the dialog.
        Nothing is changed by it: what is chosen is sent back by the page
        with whatever it is for.
        """
        start = (payload or {}).get("start")
        picker = pick_directory
        if picker is None:
            from utils import native_directory

            def picker(start_path: str) -> str | None:
                try:
                    return native_directory.pick_directory(start_path=start_path)
                except native_directory.DirectoryPickerCancelled:
                    return None

        try:
            chosen = await asyncio.to_thread(picker, start if isinstance(start, str) else "")
        except Exception as exc:  # noqa: BLE001 - NativeDirectoryError: no dialog can be shown here
            logger.warning("the dialog for choosing a folder could not be shown (exc_type=%s)", type(exc).__name__)
            raise _refuse(500, "picker_unavailable") from None
        if not chosen:
            return {"success": True, "cancelled": True}
        return {"success": True, "path": str(chosen)}

    @router.post("/space/projects/workdir")
    async def set_project_workdir(payload: dict[str, Any]) -> dict[str, Any]:
        """Change where the task agents of a project work. Nothing is moved; the next commission starts there."""
        payload = payload or {}
        project, _ = scope(payload.get("project"))
        workdir = payload.get("workdir")
        if project is None or not isinstance(workdir, str):
            raise _refuse(400, "bad_name")
        host = await get_host()
        try:
            changed = await host.set_project_workdir(project, workdir)
        except LookupError:
            raise _refuse(404, "no_project") from None
        except ValueError as exc:
            code = str(exc)
            if code not in ("not_absolute", "not_a_directory", "host_directory"):
                # A name that is no name of a directory (``safe_segment``).
                code = "bad_name"
            raise _refuse(400, code) from None
        except RuntimeError as exc:
            if type(exc).__name__ == "HostShuttingDown":
                # Asked for while the service is going down: as if it were not there to answer.
                raise _refuse(503, "agent_unreachable") from None
            if type(exc).__name__ == "ProjectUnreadable":
                # Its record cannot be read: nothing of it was changed.
                raise _refuse(409, "space_refused") from None
            if type(exc).__name__ != "ProjectBusy":
                raise
            raise _refuse(409, "project_busy", reason=str(exc)) from None
        return {"success": True, "project": changed}

    @router.get("/space/rules")
    async def read_rules(project: str | None = None, archive_year: str | None = None) -> dict[str, Any]:
        """Return the rules the user wrote for a project (an archived one by its year), or for every task agent."""
        host = await get_host()
        project, archive_year = scope(project, archive_year)
        return {"success": True, "text": await space_call(host.space.read_rules(project, archive_year=archive_year))}

    @router.put("/space/rules")
    async def write_rules(payload: dict[str, Any]) -> dict[str, Any]:
        """Write the rules as the user gave them."""
        payload = payload or {}
        project, _ = scope(payload.get("project"))
        if payload.get("archive_year") is not None:
            # Archived rules are shown as they were; written, they would go to a project of the same name.
            raise _refuse(400, "space_refused")
        text = text_of(payload, "text", required=True)
        host = await get_host()
        await there(host, project)
        await space_call(host.space.write_rules(text, project))
        return {"success": True, "text": await space_call(host.space.read_rules(project))}

    @router.get("/space/index")
    async def read_index(project: str | None = None, archive_year: str | None = None) -> dict[str, Any]:
        """Return the index a task agent is given when it starts; an archived project's by its year."""
        host = await get_host()
        project, archive_year = scope(project, archive_year)
        return {"success": True, "text": await space_call(host.space.read_index(project, archive_year=archive_year))}

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
        from nekopower.host.space import ENTRY_TYPES

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
        await there(host, project)
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
        from nekopower.host.space import ENTRY_TYPES

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
    async def list_reviews(project: str | None = None, archive_year: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Return the reviews of a project, or of every project, newest first, without their text.

        Of a project that is named, those of the one that is there now;
        with ``archive_year``, those of the one archived that year. Two
        projects of a name are two projects.
        """
        host = await get_host()
        project, archive_year = scope(project, archive_year)
        wanted = max(1, min(int(limit), 500))
        reviews = await space_call(host.space.list_reviews(project, limit=None if project is not None else wanted))
        if project is not None:
            reviews = [review for review in reviews if review.archive_year == archive_year][:wanted]
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

    # -- the task agents at a glance, and making one -----------------------

    @router.get("/board")
    async def get_board(project: str | None = None, known_revision: str | None = None) -> dict[str, Any]:
        """Return the task agents by project, one card each; ``unchanged`` when nothing moved since ``known_revision``."""
        host = await get_host()
        project, _ = scope(project)
        return {"success": True, **(await host.board(project, None, known_revision or None))}

    @router.get("/templates")
    async def get_templates(project: str | None = None) -> dict[str, Any]:
        """Return what kinds of task agent there are to choose from."""
        host = await get_host()
        project, _ = scope(project)
        return {"success": True, "templates": await host.templates(project)}

    @router.post("/familiars/draft")
    async def draft_familiar(payload: dict[str, Any]) -> dict[str, Any]:
        """Fill in the form of a new task agent from a sentence about it. One call of the summary model; nothing is made."""
        payload = payload or {}
        text = text_of(payload, "text", required=True)
        workdir = payload.get("workdir")
        if workdir is not None and not isinstance(workdir, str):
            raise _refuse(400, "bad_text", field="workdir")
        host = await get_host()
        if workdir:
            # The names of what is in it go to a model: only a directory a task agent could be given.
            try:
                await host.working_directory(workdir)
            except ValueError as exc:
                code = str(exc)
                raise _refuse(
                    400, code if code in ("not_absolute", "not_a_directory", "host_directory") else "bad_name"
                ) from None
        try:
            draft = await host.draft_session(text, workdir or None)
        except RuntimeError as exc:
            if type(exc).__name__ != "DraftError":
                raise
            reason = str(getattr(exc, "reason", "failed"))
            raise _refuse(400 if reason == "empty" else 502, "draft_failed", reason=reason) from None
        return {"success": True, "draft": draft}

    @router.post("/familiars")
    async def make_familiar(payload: dict[str, Any]) -> dict[str, Any]:
        """Make a task agent for something lasting: a new project and its own session. Nothing is started."""
        payload = payload or {}
        fields = {
            name: text_of(payload, name) or ""
            for name in ("name", "preset", "title", "duty", "description", "rules")
        }
        workdir = payload.get("workdir")
        if workdir is not None and not isinstance(workdir, str):
            raise _refuse(400, "bad_text", field="workdir")
        lanlan_name = payload.get("lanlan_name")
        if not isinstance(lanlan_name, str) or not lanlan_name.strip():
            lanlan_name = await character() if character is not None else None
        host = await get_host()
        try:
            # With no preset named, one that does the work: a boss only hands work on to the others.
            made = await host.open_session(
                lanlan_name or "", fields["name"], preset=fields["preset"] or "general", title=fields["title"],
                duty=fields["duty"], description=fields["description"], workdir=workdir or None, rules=fields["rules"],
            )
        except ValueError as exc:
            code = str(exc)
            known = ("no_character", "bad_name", "name_taken", "bad_preset", "not_absolute", "not_a_directory", "host_directory")
            raise _refuse(400, code if code in known else "bad_name") from None
        return {"success": True, **made}

    @router.patch("/familiars/{session_id}/card")
    async def write_card(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Write the title, the duty or the summary of a task agent's card, as the user's own words.

        What the user wrote is not written over by a review or by the task
        agent itself; an empty line empties it and keeps it the user's.
        """
        payload = payload or {}
        lines = {name: text_of(payload, name) for name in ("title", "duty", "summary")}
        lines = {name: value for name, value in lines.items() if value is not None}
        if not lines:
            raise _refuse(400, "nothing_to_change")
        host = await get_host()
        session, written = await host.ledger.write_card(session_id, by="user", **lines)
        if session is None:
            raise _refuse(404, "no_familiar")
        return {"success": True, "written": list(written)}

    @router.post("/space/projects/description")
    async def describe_project(payload: dict[str, Any]) -> dict[str, Any]:
        """Write what a project is for, as the user's own words."""
        payload = payload or {}
        project, _ = scope(payload.get("project"))
        description = text_of(payload, "description", required=True)
        if project is None or not description.strip():
            raise _refuse(400, "bad_text", field="description")
        host = await get_host()
        changed = await space_call(host.space.describe_project(project, description, by="user"))
        if changed is None:
            raise _refuse(404, "no_project")
        return {"success": True, "project": changed.to_json()}

    # -- talking to a task agent ------------------------------------------
    #
    # By the commission it has in hand (``commission.id`` on its card).

    @router.post("/commissions/{commission_id}/tell")
    async def tell_commission(commission_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Pass a sentence of the user's on to a task agent that is at work."""
        text = text_of(payload or {}, "text", required=True)
        if not text.strip():
            raise _refuse(400, "bad_text", field="text")
        host = await get_host()
        if not await host.tell(commission_id, text):
            # It is over, or not one a task agent works on: nothing took the sentence.
            raise _refuse(409, "not_taken")
        return {"success": True}

    @router.post("/commissions/{commission_id}/answer")
    async def answer_commission(commission_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Give the user's answer to a task agent that waits for one: ``choice`` for a question with options, else ``text``."""
        payload = payload or {}
        text = text_of(payload, "text") or ""
        choice, question_id = payload.get("choice"), payload.get("question_id")
        for value in (choice, question_id):
            if value is not None and not isinstance(value, str):
                raise _refuse(400, "bad_text", field="choice")
        if not text.strip() and not choice:
            raise _refuse(400, "bad_text", field="text")
        host = await get_host()
        if not await host.answer(commission_id, text, choice=choice, question_id=question_id):
            # It asked nothing, or what it asked was answered or withdrawn meanwhile.
            raise _refuse(409, "not_taken")
        return {"success": True}

    @router.post("/commissions/{commission_id}/stop")
    async def stop_commission(commission_id: str) -> dict[str, Any]:
        """Stop what a task agent is doing, as the user asks."""
        host = await get_host()
        stopped = await host.abort(commission_id, reason="stopped")
        if stopped is None:
            raise _refuse(404, "no_commission")
        return {"success": True, "status": stopped.status}

    @router.post("/space/projects/archive")
    async def archive_project(payload: dict[str, Any]) -> dict[str, Any]:
        """Put a project away into the archive. Nothing is removed, and a commission that names it brings it back."""
        payload = payload or {}
        project, _ = scope(payload.get("project"))
        if project is None:
            raise _refuse(400, "bad_name")
        host = await get_host()
        try:
            archived = await space_call(host.archive_project(project))
        except LookupError:
            raise _refuse(404, "no_project") from None
        except RuntimeError as exc:
            kind = type(exc).__name__
            if kind == "HostShuttingDown":
                raise _refuse(503, "agent_unreachable") from None
            if kind == "ProjectUnreadable":
                raise _refuse(409, "space_refused") from None
            if kind == "ProjectNotMoved":
                # A file in its directory is held open: it stays where it is.
                raise _refuse(409, "archive_failed") from None
            if kind != "ProjectBusy":
                raise
            raise _refuse(409, "project_busy", reason=str(exc)) from None
        return {"success": True, "project": archived}

    # -- more task agents for a project that is there --------------------------

    async def whose(payload: dict[str, Any]) -> str:
        """Return the character a task agent is for: the one the page names, else the one the user is with now."""
        lanlan_name = payload.get("lanlan_name")
        if isinstance(lanlan_name, str) and lanlan_name.strip():
            return lanlan_name
        return (await character() if character is not None else None) or ""

    @router.post("/familiars/member")
    async def add_member(payload: dict[str, Any]) -> dict[str, Any]:
        """Add a task agent to a project that is there: a member that does work of it. Nothing is started.

        Whether it works in the project's directory or in a git worktree of
        its own is the host's to tell (``seat`` in the answer).
        """
        payload = payload or {}
        project, _ = scope(payload.get("project"))
        if project is None:
            raise _refuse(400, "bad_name")
        fields = {name: text_of(payload, name) or "" for name in ("preset", "title", "duty")}
        host = await get_host()
        try:
            made = await host.open_member(
                await whose(payload), project, preset=fields["preset"] or "general",
                title=fields["title"], duty=fields["duty"],
            )
        except LookupError:
            raise _refuse(404, "no_project") from None
        except ValueError as exc:
            code = str(exc)
            if code == "too_many_members":
                # The project has as many members that take work as it may (``PROJECT_MEMBERS_MAX``).
                raise _refuse(409, code) from None
            raise _refuse(400, code if code in ("no_character", "bad_preset") else "bad_name") from None
        return {"success": True, **made}

    @router.post("/space/projects/leader")
    async def give_leader(payload: dict[str, Any]) -> dict[str, Any]:
        """Give a project a leader, which hands its work out to the others; ``made`` False when it had one."""
        payload = payload or {}
        project, _ = scope(payload.get("project"))
        if project is None:
            raise _refuse(400, "bad_name")
        host = await get_host()
        try:
            made = await host.derive_leader(project, await whose(payload))
        except LookupError:
            raise _refuse(404, "no_project") from None
        except ValueError as exc:
            raise _refuse(400, "no_character" if str(exc) == "no_character" else "bad_name") from None
        return {"success": True, **made}

    # -- the git worktrees task agents work in, and landing what they changed --
    #
    # A worktree is named by its ``id``; see ``NekoPowerHost.worktrees``.

    def tree_refused(exc: Exception) -> HTTPException:
        if isinstance(exc, LookupError):
            return _refuse(404, "no_worktree")
        code = str(exc)
        # ``at_work``: its commission is under way; ``busy``: another decision on it is carried
        # out; ``gone``: its directory, or the repository of its project, is not there any more (it
        # can be discarded); ``not_opened``: it is there and git could not open it just now (try
        # again in a while); ``moved``: one the user sees to was made from where the project worked
        # before (it moved, or was put away), and is not merged there.
        return _refuse(409, code if code in ("at_work", "busy", "gone", "not_opened", "moved") else "worktree_refused")

    @router.get("/worktrees")
    async def list_worktrees(project: str | None = None) -> dict[str, Any]:
        """Return the git worktrees task agents work in or left for the user, of one project or of all, the oldest first."""
        project, _ = scope(project)
        host = await get_host()
        return {"success": True, "worktrees": await host.worktrees(project)}

    @router.post("/worktrees/{tree_id}/land")
    async def land_worktree(tree_id: str) -> dict[str, Any]:
        """Merge what a worktree holds into its project's directory: once more, or for the first time where the user is asked first."""
        host = await get_host()
        try:
            landed = await host.land_worktree(tree_id)
        except (LookupError, ValueError) as exc:
            raise tree_refused(exc) from None
        return {"success": True, **landed}

    @router.post("/worktrees/{tree_id}/discard")
    async def discard_worktree(tree_id: str) -> dict[str, Any]:
        """Remove a worktree with what it holds; ``removed`` False when its directory is removed when the host next starts."""
        host = await get_host()
        try:
            removed = await host.discard_worktree(tree_id)
        except (LookupError, ValueError) as exc:
            raise tree_refused(exc) from None
        return {"success": True, **removed}

    @router.post("/worktrees/{tree_id}/keep")
    async def keep_worktree(tree_id: str) -> dict[str, Any]:
        """Leave a worktree to the user, who sees to it personally: nothing reminds of it any more."""
        host = await get_host()
        try:
            kept = await host.keep_worktree(tree_id)
        except (LookupError, ValueError) as exc:
            raise tree_refused(exc) from None
        return {"success": True, "worktree": kept}

    @router.post("/worktrees/{tree_id}/open")
    async def open_worktree(tree_id: str) -> dict[str, Any]:
        """Show the project's directory within a worktree in the system's file manager. Only one the host keeps."""
        host = await get_host()
        found = next((tree for tree in await host.worktrees() if tree.get("id") == tree_id), None)
        if found is None:
            raise _refuse(404, "no_worktree")
        path = Path(str(found.get("workdir") or found.get("path") or ""))
        if not str(path) or not await asyncio.to_thread(path.is_dir):
            raise _refuse(404, "place_missing")
        opener = open_directory
        if opener is None:
            from utils.native_directory import open_directory as opener
        try:
            await asyncio.to_thread(opener, path)
        except Exception as exc:  # noqa: BLE001 - NativeDirectoryError, with its own code
            logger.warning("showing a worktree failed (exc_type=%s)", type(exc).__name__)
            raise _refuse(500, "open_failed") from None
        return {"success": True}

    @router.get("/landing")
    async def get_landing() -> dict[str, Any]:
        """Return how what a task agent changed in its worktree is landed: ``mode`` (``auto`` / ``ask``) and ``inspect``."""
        host = await get_host()
        return {"success": True, **(await host.landing_settings())}

    @router.post("/landing")
    async def set_landing(payload: dict[str, Any]) -> dict[str, Any]:
        """Set what is given of ``mode`` and ``inspect``; returns the settings as they stand."""
        payload = payload or {}
        if "mode" not in payload and "inspect" not in payload:
            raise _refuse(400, "nothing_to_change")
        mode, inspect = payload.get("mode"), payload.get("inspect")
        if "mode" in payload and mode not in ("auto", "ask"):
            raise _refuse(400, "bad_mode")
        if "inspect" in payload and not isinstance(inspect, bool):
            raise _refuse(400, "bad_flag")
        host = await get_host()
        try:
            changed = await host.set_landing_settings(mode=mode, inspect=inspect)
        except ValueError as exc:
            raise _refuse(400, "bad_flag" if str(exc) == "bad_inspect" else "bad_mode") from None
        except OSError:
            # Its file could not be written: the settings are what they were.
            raise _refuse(409, "landing_not_saved") from None
        return {"success": True, **changed}

    # -- what happened among the task agents, and who waits for whom -------

    def whole_number(value: Any, code: str, *, least: int = 0) -> int | None:
        """Return a number a page sent as a query; None for none. What is no such number is refused with ``code``."""
        if value is None or value == "":
            return None
        try:
            number = int(str(value), 10)
        except ValueError:
            raise _refuse(400, code) from None
        if not least <= number <= _ROW_MAX:
            raise _refuse(400, code)
        return number

    @router.get("/timeline")
    async def get_timeline(
        project: str | None = None, after: str | None = None, limit: str | None = None
    ) -> dict[str, Any]:
        """Return a page of what happened among the task agents, in the order it happened; see ``NekoPowerHost.timeline``."""
        project, _ = scope(project)
        rows = whole_number(limit, "bad_limit", least=1)
        host = await get_host()
        try:
            page = await host.timeline(project, None, after or None, rows)
        except ValueError:
            # Not a cursor a page of the timeline gave.
            raise _refuse(400, "bad_cursor") from None
        return {"success": True, **page}

    @router.get("/timeline/start")
    async def get_timeline_start() -> dict[str, Any]:
        """Return where a page begins reading the timeline for what happened lately; see ``NekoPowerHost.timeline_start``."""
        host = await get_host()
        return {"success": True, **(await host.timeline_start())}

    @router.get("/waits")
    async def get_waits(project: str | None = None) -> dict[str, Any]:
        """Return who waits for whom now; see ``NekoPowerHost.waits``."""
        project, _ = scope(project)
        host = await get_host()
        return {"success": True, **(await host.waits(project, None))}

    # -- the model each kind of task agent is sent to ------------------------

    @router.get("/role-models")
    async def get_role_models() -> dict[str, Any]:
        """Return the model setting of every preset, what it comes to, and the slots to choose from."""
        host = await get_host()
        return {"success": True, **(await host.role_models())}

    @router.post("/role-models")
    async def set_role_model(payload: dict[str, Any]) -> dict[str, Any]:
        """Set the model the new task agents of one preset are sent to; ``value`` None goes back to its default."""
        payload = payload or {}
        preset, value = payload.get("preset"), payload.get("value")
        host = await get_host()
        try:
            view = await host.set_role_model(preset, value)
        except ValueError as exc:
            code = str(exc)
            known = (
                "unknown_preset", "cannot_inherit", "bad_value", "no_slots", "slot_not_found",
                "model_config_unreadable", "capability_missing",
            )
            raise _refuse(400, code if code in known else "bad_value") from None
        except OSError:
            # The file of settings is there and cannot be read: nothing was written over it.
            raise _refuse(409, "role_models_unreadable") from None
        return {"success": True, **view}

    # -- one task agent: what was said in it, what it used ---------------------

    @router.get("/familiars/{session_id}/transcript")
    async def get_transcript(
        session_id: str, before: str | None = None, limit: str | None = None, at: str | None = None
    ) -> dict[str, Any]:
        """Return a page of what was said in a task agent's session, the last entries first asked for.

        ``before`` is the place a page before gave; ``at`` asks for the one
        entry that begins there, whole. A session dsh holds, or one nothing
        was said in, answers ``readable`` false with the reason.
        """
        place = whole_number(before, "bad_place")
        rows = whole_number(limit, "bad_limit", least=1)
        one = whole_number(at, "bad_place")
        host = await get_host()
        try:
            page = await host.transcript(session_id, place, rows, one)
        except ValueError as exc:
            code = str(exc)
            if code == "record_unreadable":
                raise _refuse(409, code) from None
            raise _refuse(413 if code == "too_large" else 400, code if code in ("bad_place", "too_large") else "bad_place") from None
        if page is None:
            raise _refuse(404, "no_familiar")
        return {"success": True, **page}

    @router.get("/familiars/{session_id}/transcript/search")
    async def search_transcript(session_id: str, q: str | None = None) -> dict[str, Any]:
        """Return where the words ``q`` occur in what was said in a task agent's session, the latest first.

        Each hit is opened whole with ``transcript?at=``. A session dsh
        holds, or one nothing was said in, answers ``readable`` false.
        """
        if not isinstance(q, str) or not q.split() or len(q) > _QUERY_LIMIT:
            raise _refuse(400, "bad_query")
        host = await get_host()
        try:
            found = await host.search_transcript(session_id, q)
        except ValueError as exc:
            code = str(exc)
            raise _refuse(409 if code == "record_unreadable" else 400, code if code == "record_unreadable" else "bad_query") from None
        if found is None:
            raise _refuse(404, "no_familiar")
        return {"success": True, **found}

    @router.get("/familiars/{session_id}/usage")
    async def get_session_usage(session_id: str) -> dict[str, Any]:
        """Return what a task agent used over all it did, and what the commission it has in hand may."""
        host = await get_host()
        usage = await host.session_usage(session_id)
        if usage is None:
            raise _refuse(404, "no_familiar")
        return {"success": True, **usage}

    # -- the commissions ----------------------------------------------------

    @router.get("/commissions")
    async def list_commissions(
        status: str | None = None, project: str | None = None, loose: str | None = None,
        days: str | None = None, limit: str | None = None,
    ) -> dict[str, Any]:
        """Return the commissions, the newest first: those under way and those that changed lately.

        ``status`` is one status or several with commas between them;
        ``project`` keeps to one project, ``loose`` (``1`` or ``true``) to
        those of no project, and the two are not asked for together;
        ``days`` how far back (0: however long ago).
        """
        from nekopower.host.ledger import STATUSES

        wanted = None
        if status:
            wanted = tuple(part.strip() for part in status.split(",") if part.strip())
            if not wanted or any(part not in (*STATUSES, "resting") for part in wanted):
                raise _refuse(400, "bad_status")
        project, _ = scope(project)
        loosely = (loose or "").strip().lower()
        if loosely not in ("", "0", "false", "1", "true"):
            raise _refuse(400, "bad_name")
        if loosely in ("1", "true"):
            if project is not None:
                # Of one project and of none at once: no commission is both.
                raise _refuse(400, "bad_name")
            project = ""
        span = whole_number(days, "bad_days")
        rows = whole_number(limit, "bad_limit", least=1)
        host = await get_host()
        return {"success": True, **(await host.commissions(wanted, project, span, rows))}

    @router.get("/commissions/{commission_id}/usage")
    async def get_commission_usage(commission_id: str) -> dict[str, Any]:
        """Return what a commission used and what it may; see ``NekoPowerHost.usage``."""
        host = await get_host()
        usage = await host.usage(commission_id)
        if usage is None:
            raise _refuse(404, "no_commission")
        return {"success": True, **usage}

    # -- the MCP tools, and how each server is set -----------------------------

    @router.get("/mcp")
    async def get_mcp() -> dict[str, Any]:
        """Return the MCP tools by server, with the setting of each server; see ``NekoPowerHost.mcp_tools``."""
        host = await get_host()
        # Read first: the version is of the settings as they were read for it.
        view = await host.mcp_tools()
        return {"success": True, "version": host.permission_store.version, **view}

    @router.post("/mcp/permission")
    async def set_mcp_permission(payload: dict[str, Any]) -> dict[str, Any]:
        """Set how one MCP server is treated, or all of them when ``server`` is not given.

        A server set to ``auto`` is left without a setting of its own and
        goes by the one all servers share, as one that was never set does
        (``PermissionStore.clear_mcp_server``).
        """
        from nekopower.host.permissions import MCP_SERVER_CATEGORY, MODES, PermissionFileError

        payload = payload or {}
        server, mode = payload.get("server"), payload.get("mode")
        if server is not None and (
            not isinstance(server, str) or not server.strip() or server.strip() == "*" or len(server) > 200
        ):
            raise _refuse(400, "bad_server")
        if mode not in MODES:
            raise _refuse(400, "bad_mode")
        expected = payload.get("expected_version")
        if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int)):
            raise _refuse(400, "bad_version")
        host = await get_host()
        try:
            if server is not None and mode == "auto":
                changed = await host.permission_store.clear_mcp_server(server.strip(), expected_version=expected)
            else:
                changed = await host.permission_store.set_mode(
                    MCP_SERVER_CATEGORY, mode, server.strip() if server is not None else None, expected_version=expected
                )
        except PermissionFileError:
            raise _refuse(409, "permissions_unreadable") from None
        except OSError:
            # The file could not be written.
            raise _refuse(409, "permissions_not_saved") from None
        if not changed:
            raise _refuse(409, "stale")
        view = await host.mcp_tools()
        return {"success": True, "version": host.permission_store.version, **view}

    # -- how much one commission may use ---------------------------------------

    @router.get("/usage-limit")
    async def get_usage_limit() -> dict[str, Any]:
        """Return how much the work on one commission may use before the user is asked."""
        host = await get_host()
        return {"success": True, **(await host.usage_limit())}

    @router.post("/usage-limit")
    async def set_usage_limit(payload: dict[str, Any]) -> dict[str, Any]:
        """Set that limit, in tokens; ``tokens`` None goes back to the default."""
        payload = payload or {}
        # Said in so many words: a payload that names no ``tokens`` asks for nothing, and does
        # not put the limit back to the default.
        if "tokens" not in payload:
            raise _refuse(400, "not_a_number", field="tokens")
        tokens = payload["tokens"]
        if tokens is not None and (isinstance(tokens, bool) or not isinstance(tokens, int)):
            raise _refuse(400, "not_a_number", field="tokens")
        host = await get_host()
        try:
            view = await host.set_usage_limit(tokens)
        except ValueError:
            raise _refuse(400, "out_of_range", field="tokens") from None
        except OSError:
            # Its file could not be written: the limit is what it was.
            raise _refuse(409, "limit_not_saved") from None
        return {"success": True, **view}

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
        if not 0 < preference_id <= _ROW_MAX:
            raise _refuse(404, "no_preference")
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
        if not 0 < preference_id <= _ROW_MAX:
            raise _refuse(404, "no_preference")
        host = await get_host()
        if not await host.preferences.remove(preference_id):
            raise _refuse(404, "no_preference")
        return {"success": True}

    return router
