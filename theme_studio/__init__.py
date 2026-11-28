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

"""Theme Studio, a built-in N.E.K.O feature module.

History: this used to be a standalone plugin (``neko_theme_studio``, served on
the plugin port 48916, with an "app patch" injecting the loader into the host
templates). It is now part of the host:

* no plugin host, no separate port, no injector;
* the panel page ``/theme_studio`` is rendered by ``main_routers/pages_router.py``;
* data goes through ``/api/theme_studio/*`` (``main_routers/theme_studio_router.py``);
* the loader assets are ordinary static files under ``static/theme-studio/loader/``
  referenced directly by the templates.

This module is only the thin glue layer (service singleton + entry dispatch).
All business logic lives in :mod:`theme_studio.service`.

Threading: every service method does synchronous disk / zip work and takes the
module-level ``threading.RLock`` that the schedule thread also uses. Async
callers must therefore go through the ``*_async`` helpers here, which run the
work in a worker thread so the shared event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from pathlib import Path
from typing import Any

from ._sdk_compat import ENTRY_REGISTRY, Err, Ok, SdkError
from .i18n import msg as _msg
from .service import (
    API_BASE,
    PRESETS_DIR,
    ThemeStudioService,
)

__all__ = [
    "API_BASE",
    "PRESETS_DIR",
    "ThemeStudioService",
    "backgrounds_dir",
    "data_dir",
    "entries",
    "get_service",
    "get_theme_document",
    "get_theme_document_async",
    "library_dir",
    "presets_dir",
    "run_entry",
    "run_entry_sync",
    "service_started",
    "set_data_dir_override",
    "shutdown_service",
    "shutdown_service_async",
    "startup_service",
    "startup_service_async",
]

logger = logging.getLogger("neko.theme_studio")

_service: ThemeStudioService | None = None
_service_lock = threading.RLock()
_service_started = False
# Set by the app-exit hook (shutdown_service(final=True)); blocks restarts.
_app_exiting = False
_data_dir_override: Path | None = None
# Entries were written for a plugin host that ran them one at a time, and
# several do read-modify-write outside ``_WRITE_LOCK`` (update_theme_meta now
# holds it for the whole edit, but not every entry does). Worker threads must not change that, so dispatch is
# serialised here. Entries finish in milliseconds; only the event loop is freed.
_ENTRY_LOCK = threading.Lock()


def set_data_dir_override(path: Path | str | None) -> None:
    """Pin the data directory (tests / tooling; call before :func:`get_service`)."""
    global _data_dir_override
    _data_dir_override = Path(path) if path else None


def _schedule_allowed() -> bool:
    return not _app_exiting


def get_service() -> ThemeStudioService:
    """Return the process-wide service instance, constructing it on first use."""
    global _service
    if _service is not None:
        return _service
    with _service_lock:
        if _service is None:
            service = ThemeStudioService(data_dir=_data_dir_override)
            # The exit flag is read when the schedule thread is about to start, so
            # a startup racing with the app-exit hook cannot bring it back.
            service.schedule_allowed = _schedule_allowed
            _service = service
        return _service


def service_started() -> bool:
    """Whether startup initialisation (dirs, theme.json, schedule thread) has run."""
    return _service_started


def startup_service() -> ThemeStudioService:
    """Construct and initialise the service (synchronous, idempotent).

    Runs lazily on the first Theme Studio request (in practice the loader's
    first palette poll); it is not hooked into app startup. Does blocking disk
    IO: from async code use :func:`startup_service_async`.
    """
    global _service_started
    service = get_service()
    if _service_started:
        return service
    if _app_exiting:
        # The app is shutting down: a late request still gets the service (its
        # reads and writes are safe), but the schedule thread must not come back.
        return service
    with _service_lock:
        if not _service_started and not _app_exiting:
            service.startup_sync()
            _service_started = True
    return service


async def startup_service_async() -> ThemeStudioService:
    """Async wrapper of :func:`startup_service` that runs it in a worker thread."""
    if _service_started and _service is not None:
        return _service
    return await asyncio.to_thread(startup_service)


def shutdown_service(timeout: float | None = None, *, final: bool = False) -> bool:
    """Stop the schedule thread and let in-flight writes finish.

    Called from the main server's shutdown hook (``on_shutdown`` in
    ``app/main_server``) through :func:`shutdown_service_async`. With a
    ``timeout`` it waits, within that budget in total, for the schedule thread
    to exit and for any running entry to release ``_ENTRY_LOCK``, so a write
    already in progress (theme.json, library, schedule) reaches the disk.
    Every write is atomic (tmp + replace), so nothing half-written is left even
    if the budget runs out. Returns True when everything stopped in time.

    Resets the started flag so a later :func:`startup_service` restarts the
    schedule thread instead of leaving it dead until the process restarts --
    except with ``final=True`` (the app-exit hook): then later requests during
    exit no longer start it again.
    The wait for the service lock (held while startup scans the data directory)
    is also bounded by ``timeout``.
    """
    global _service_started, _app_exiting
    if final:
        _app_exiting = True
    service = _service
    if service is None:
        return True
    deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)

    def remaining() -> float:
        return max(0.0, deadline - time.monotonic()) if deadline is not None else 0.0

    locked = _service_lock.acquire(timeout=remaining()) if deadline is not None else _service_lock.acquire()
    try:
        try:
            service.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Theme Studio shutdown raised (ignored): %s", exc)
        _service_started = False
    finally:
        if locked:
            _service_lock.release()
    if deadline is None:
        return True

    stopped = service._join_schedule_worker(remaining())  # noqa: SLF001
    if _ENTRY_LOCK.acquire(timeout=remaining()):
        _ENTRY_LOCK.release()
        drained = True
    else:
        drained = False
    if not (stopped and drained):
        logger.warning(
            "Theme Studio shutdown timed out (schedule thread stopped=%s, entries drained=%s)",
            stopped,
            drained,
        )
    return stopped and drained


async def shutdown_service_async(timeout: float = 3.0) -> bool:
    """:func:`shutdown_service` with a bounded wait, run in a worker thread."""
    return await asyncio.to_thread(shutdown_service, timeout, final=True)


def entries() -> list[str]:
    """Registered entry ids (panel self-check / debugging)."""
    return sorted(ENTRY_REGISTRY)


def _resolve_entry(entry_id: str) -> Any:
    """Map an entry id to its implementation; unknown ids return ``None``.

    Only ids registered through ``@plugin_entry`` are dispatchable, so lifecycle
    and internal methods (``startup``, ``shutdown``, ``startup_sync``,
    ``enable_file_logging`` ...) can never be reached from ``/run/{entry_id}``.
    The registered function is unbound; the caller passes ``service`` explicitly.
    """
    return ENTRY_REGISTRY.get(entry_id)


def _call_entry(fn: Any, service: Any, payload: dict[str, Any]) -> Any:
    """Invoke an entry synchronously, driving it to completion if it is a coroutine.

    The service's ``async def`` entries never await anything (they are plugin-era
    signatures around synchronous bodies), so running them on a private event
    loop inside the worker thread is cheap and keeps the main loop free.
    """
    result = fn(service, **_clean_kwargs(fn, payload))
    if inspect.isawaitable(result):

        async def _await(awaitable: Any) -> Any:
            return await awaitable

        result = asyncio.run(_await(result))
    return result


def run_entry_sync(entry_id: str, args: dict[str, Any] | None = None) -> Ok | Err:
    """Dispatch one entry synchronously and return ``Ok`` / ``Err``.

    Blocking: call it from a worker thread (see :func:`run_entry`).
    """
    payload = args if isinstance(args, dict) else {}
    fn = _resolve_entry(str(entry_id or ""))
    if fn is None:
        return Err(SdkError(_msg("core.unknownEntry", entry_id=entry_id)))
    try:
        # Inside the try: a failed first startup must reach the panel as
        # ``{ok: false, error}``; ``_service_started`` stays False so the next
        # call retries the startup.
        service = startup_service()
        with _ENTRY_LOCK:
            result = _call_entry(fn, service, payload)
    except SdkError as exc:
        return Err(exc)
    except Exception as exc:  # noqa: BLE001 - every failure must become a readable error
        logger.exception("Theme Studio entry failed: %s", entry_id)
        return Err(SdkError(_msg("core.operationFailedDetail", error=exc)))
    if isinstance(result, (Ok, Err)):
        return result
    return Ok(result)


async def run_entry(entry_id: str, args: dict[str, Any] | None = None) -> Ok | Err:
    """Dispatch one entry in a worker thread so disk / zip work never blocks the loop."""
    return await asyncio.to_thread(run_entry_sync, entry_id, args)


def _clean_kwargs(fn: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only the keyword arguments the entry's signature accepts.

    The panel sometimes sends extra fields. A ``**_`` parameter passes the
    payload through unchanged, matching the former plugin host.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):  # pragma: no cover - builtins only
        return payload
    params = list(sig.parameters.values())[1:]
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return payload
    allowed = {
        p.name
        for p in params
        if p.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    return {k: v for k, v in payload.items() if k in allowed}


def get_theme_document() -> dict[str, Any]:
    """Palette document polled by the loader (blocking; see the async variant).

    Same shape as the ``get_theme`` entry: compiled material variables and
    attributes are attached, otherwise windows would never see materials
    (``_normalize`` drops the on-disk ``material_vars`` mirror). Asset
    fingerprints are attached for the loader's stylesheet / self-upgrade checks.
    """
    service = startup_service()
    palette = service._with_material_vars(service._read_unlocked())  # noqa: SLF001
    try:
        palette = service._with_asset_meta(palette)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        logger.warning("Reading loader asset fingerprints failed (ignored): %s", exc)
    return palette


async def get_theme_document_async() -> dict[str, Any]:
    """:func:`get_theme_document` run in a worker thread."""
    return await asyncio.to_thread(get_theme_document)


def data_dir() -> Path:
    """User data directory (theme.json / themes / backgrounds / schedule)."""
    return get_service().data_dir


def backgrounds_dir() -> Path:
    """Chat background image directory."""
    return get_service()._backgrounds_dir()  # noqa: SLF001


def presets_dir() -> Path:
    """Built-in preset directory (read-only, shipped with the app; has ``images/``)."""
    return PRESETS_DIR


def library_dir() -> Path:
    """User theme library directory (``themes/<id>.json``)."""
    return get_service()._library_dir()  # noqa: SLF001
