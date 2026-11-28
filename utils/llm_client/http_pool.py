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

"""Loop-scoped shared httpx.AsyncClient pool for one-shot async LLM clients.

One-shot callers throughout the codebase construct a chat client via
``create_chat_llm_async(...)`` and call ``await llm.aclose()`` right after a
single request. OpenAI and Anthropic SDK clients resolve request URLs, headers,
credentials, and timeouts per request without mutating their underlying
``httpx.AsyncClient``, so borrowing a shared stateless ``httpx.AsyncClient``
keyed by ``(event_loop, family, origin, credential_fingerprint, proxy_snapshot,
ssl_context)`` reuses warm TCP + TLS connections transparently across calls
while keeping cookies, credentials, and proxy settings strictly isolated.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
import weakref
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any

from utils.http.pool_utils import (
    DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS,
    credential_fingerprint,
    http_origin_key,
    install_stale_connection_retry,
    snapshot_http_proxy_config,
)
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

SHARED_LLM_HTTP_POOL_KEEPALIVE_SECONDS: float = DEFAULT_HTTP_KEEPALIVE_EXPIRY_SECONDS
SHARED_LLM_HTTP_POOL_MAX_CLIENTS: int = 16


class _SharedPoolEntry:
    """Reference-counted pooled client entry so evicted clients drain cleanly."""

    __slots__ = ("client", "borrows", "evicted", "closing")

    def __init__(self, client: Any) -> None:
        self.client = client
        self.borrows = 0
        self.evicted = False
        self.closing = False


_SHARED_HTTP_POOL_CONTEXT: contextvars.ContextVar[tuple[asyncio.AbstractEventLoop, int] | None] = (
    contextvars.ContextVar("_neko_llm_shared_http_pool_context", default=None)
)

_POOL_LOCK = threading.RLock()
_http_pools: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, OrderedDict[tuple, _SharedPoolEntry]]" = (
    weakref.WeakKeyDictionary()
)
_draining_pools: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, set[_SharedPoolEntry]]" = (
    weakref.WeakKeyDictionary()
)
# Clients removed from _http_pools as idle or raced losers whose async close task
# has not yet finished; registered under _POOL_LOCK before scheduling so shutdown
# cannot miss them during the handoff window.
_pending_close_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, set[Any]]" = (
    weakref.WeakKeyDictionary()
)
_loop_epochs: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, int]" = weakref.WeakKeyDictionary()
_closing_loops: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, int]" = weakref.WeakKeyDictionary()
_pending_close_tasks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, set[asyncio.Task[None]]]" = (
    weakref.WeakKeyDictionary()
)


def _get_loop_epoch(loop: asyncio.AbstractEventLoop) -> int:
    with _POOL_LOCK:
        return _loop_epochs.get(loop, 0)


def get_active_shared_http_pool_context() -> tuple[asyncio.AbstractEventLoop, int] | None:
    """Return ``(loop, epoch)`` armed by ``create_chat_llm_async``, or ``None``."""
    return _SHARED_HTTP_POOL_CONTEXT.get()


def get_active_shared_http_pool_loop() -> asyncio.AbstractEventLoop | None:
    """Return the event loop armed by ``create_chat_llm_async``, or ``None``."""
    ctx = _SHARED_HTTP_POOL_CONTEXT.get()
    return ctx[0] if ctx is not None else None


def set_active_shared_http_pool_loop(
    loop: asyncio.AbstractEventLoop | None,
) -> contextvars.Token[tuple[asyncio.AbstractEventLoop, int] | None]:
    """Arm *loop* and its current pool epoch so worker-thread builds borrow its HTTP pool."""
    if loop is None:
        return _SHARED_HTTP_POOL_CONTEXT.set(None)
    return _SHARED_HTTP_POOL_CONTEXT.set((loop, _get_loop_epoch(loop)))


def reset_active_shared_http_pool_loop(
    token: contextvars.Token[tuple[asyncio.AbstractEventLoop, int] | None],
) -> None:
    """Restore the previous shared HTTP pool loop context."""
    _SHARED_HTTP_POOL_CONTEXT.reset(token)


def _credential_header_values(default_headers: Mapping[str, Any] | None) -> tuple[str, str]:
    if not isinstance(default_headers, Mapping):
        return ("", "")
    auth = ""
    api_key_hdr = ""
    for key, value in default_headers.items():
        lowered = str(key).lower()
        if lowered == "authorization":
            auth = str(value or "")
        elif lowered == "x-api-key":
            api_key_hdr = str(value or "")
    return (auth, api_key_hdr)


def _http_pool_key(
    *,
    family: str,
    base_url: str | None,
    api_key: str | None,
    default_headers: Mapping[str, Any] | None,
    ssl_context: Any,
    proxy_fingerprint: tuple[Any, ...],
    extra_credential: str | None = None,
) -> tuple:
    auth_hdr, api_key_hdr = _credential_header_values(default_headers)
    return (
        str(family or ""),
        http_origin_key(base_url),
        credential_fingerprint(api_key, auth_hdr, api_key_hdr, extra_credential),
        proxy_fingerprint,
        id(ssl_context),
    )


def _register_pending_close_client_locked(loop: asyncio.AbstractEventLoop, client: Any) -> None:
    pending = _pending_close_clients.get(loop)
    if pending is None:
        pending = set()
        _pending_close_clients[loop] = pending
    pending.add(client)


def _discard_pending_close_client(loop: asyncio.AbstractEventLoop, client: Any) -> None:
    with _POOL_LOCK:
        pending = _pending_close_clients.get(loop)
        if pending is not None:
            pending.discard(client)


async def _close_quietly(client: Any, loop: asyncio.AbstractEventLoop | None = None) -> None:
    close = getattr(client, "aclose", None)
    try:
        if close is not None:
            await close()
    except Exception as exc:
        logger.debug("[llm_http_pool] closing shared HTTP client failed: %s", exc)
    finally:
        if loop is not None:
            _discard_pending_close_client(loop, client)


def _start_tracked_close_task(
    loop: asyncio.AbstractEventLoop,
    client: Any,
) -> asyncio.Task[None] | None:
    if loop.is_closed():
        _discard_pending_close_client(loop, client)
        return None
    coro = _close_quietly(client, loop)
    try:
        task = loop.create_task(coro)
    except Exception:
        coro.close()
        _discard_pending_close_client(loop, client)
        return None

    with _POOL_LOCK:
        tasks = _pending_close_tasks.get(loop)
        if tasks is None:
            tasks = set()
            _pending_close_tasks[loop] = tasks
        tasks.add(task)

    def _cleanup(done: asyncio.Task[None]) -> None:
        with _POOL_LOCK:
            loop_tasks = _pending_close_tasks.get(loop)
            if loop_tasks is not None:
                loop_tasks.discard(done)

    task.add_done_callback(_cleanup)
    return task


def _schedule_close_on_loop(loop: asyncio.AbstractEventLoop, client: Any) -> None:
    if loop.is_closed():
        _discard_pending_close_client(loop, client)
        return

    def _spawn() -> None:
        # If shutdown already claimed and started closing this client, skip duplicating.
        with _POOL_LOCK:
            pending = _pending_close_clients.get(loop)
            if pending is None or client not in pending:
                return
            pending.discard(client)
        _start_tracked_close_task(loop, client)

    try:
        loop.call_soon_threadsafe(_spawn)
    except RuntimeError:
        _discard_pending_close_client(loop, client)


def _is_loop_accepting_locked(
    loop: asyncio.AbstractEventLoop,
    expected_epoch: int | None,
) -> bool:
    if loop.is_closed():
        return False
    if _closing_loops.get(loop, 0) > 0:
        return False
    if expected_epoch is not None and _loop_epochs.get(loop, 0) != expected_epoch:
        return False
    return True


def _make_release(
    loop: asyncio.AbstractEventLoop,
    entry: _SharedPoolEntry,
) -> Callable[..., asyncio.Task[None] | None]:
    loop_ref = weakref.ref(loop)
    released = False

    def _release(
        *,
        defer_close: bool = False,
        from_finalizer: bool = False,
    ) -> asyncio.Task[None] | None:
        nonlocal released
        if from_finalizer:
            # GC finalizers may run during any allocation, including while the
            # current thread holds _POOL_LOCK. Defer the release onto the event
            # loop so GC callbacks never acquire locks synchronously.
            target_loop = loop_ref()
            if target_loop is None or target_loop.is_closed():
                return None
            try:
                target_loop.call_soon_threadsafe(_release)
            except RuntimeError:
                # Loop closed between check and scheduling; nothing to drain.
                pass
            return None

        client_to_close = None
        target_loop = None
        with _POOL_LOCK:
            if released:
                return None
            released = True
            entry.borrows = max(0, entry.borrows - 1)
            if entry.evicted and entry.borrows == 0 and not entry.closing:
                entry.closing = True
                target_loop = loop_ref()
                if target_loop is not None:
                    draining = _draining_pools.get(target_loop)
                    if draining is not None:
                        draining.discard(entry)
                    _register_pending_close_client_locked(target_loop, entry.client)
                client_to_close = entry.client
        if client_to_close is None or target_loop is None:
            return None
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is target_loop:
            with _POOL_LOCK:
                pending = _pending_close_clients.get(target_loop)
                if pending is None or client_to_close not in pending:
                    return None
                pending.discard(client_to_close)
            task = _start_tracked_close_task(target_loop, client_to_close)
            return task if defer_close else None
        _schedule_close_on_loop(target_loop, client_to_close)
        return None

    return _release


def acquire_shared_async_http_client(
    loop: asyncio.AbstractEventLoop,
    *,
    family: str,
    base_url: str | None,
    api_key: str | None,
    default_headers: Mapping[str, Any] | None,
    ssl_context: Any,
    make_client: Callable[[dict[str, str | None]], Any],
    label: str = "LLM HTTP",
    expected_epoch: int | None = None,
    extra_credential: str | None = None,
) -> tuple[Any, Callable[..., Any | None]] | tuple[None, None]:
    """Borrow a pooled ``httpx.AsyncClient`` for *loop* and return ``(client, release)``.

    Safe to call from worker threads (such as ``asyncio.to_thread`` inside
    ``create_chat_llm_async``). Proxy configuration is snapshotted once and
    reused for both the pool key and ``make_client(proxy_mount_map)``.
    Returns ``(None, None)`` if *loop* is closed, shutting down, or advanced
    past *expected_epoch* via :func:`aclose_shared_llm_http_clients`, so late
    worker threads fall back to an owned client instead of repopulating a
    closed pool.
    """
    proxy_fp, proxy_mount_map = snapshot_http_proxy_config()
    key = _http_pool_key(
        family=family,
        base_url=base_url,
        api_key=api_key,
        default_headers=default_headers,
        ssl_context=ssl_context,
        proxy_fingerprint=proxy_fp,
        extra_credential=extra_credential,
    )

    with _POOL_LOCK:
        if not _is_loop_accepting_locked(loop, expected_epoch):
            return (None, None)
        pool = _http_pools.get(loop)
        if pool is not None:
            existing = pool.get(key)
            if existing is not None:
                if not getattr(existing.client, "is_closed", False):
                    pool.move_to_end(key)
                    existing.borrows += 1
                    return (existing.client, _make_release(loop, existing))
                existing.evicted = True
                pool.pop(key, None)

    made = install_stale_connection_retry(make_client(proxy_mount_map), label=label)

    loser = None
    evicted_idle: list[Any] = []
    with _POOL_LOCK:
        if not _is_loop_accepting_locked(loop, expected_epoch):
            loser = made
            _register_pending_close_client_locked(loop, loser)
            chosen_entry = None
            release_cb = None
        else:
            pool = _http_pools.get(loop)
            if pool is None:
                pool = OrderedDict()
                _http_pools[loop] = pool
            raced = pool.get(key)
            if raced is not None and not getattr(raced.client, "is_closed", False):
                pool.move_to_end(key)
                raced.borrows += 1
                loser = made
                _register_pending_close_client_locked(loop, loser)
                chosen_entry = raced
            else:
                if raced is not None:
                    raced.evicted = True
                    pool.pop(key, None)
                chosen_entry = _SharedPoolEntry(made)
                chosen_entry.borrows = 1
                pool[key] = chosen_entry
                while len(pool) > SHARED_LLM_HTTP_POOL_MAX_CLIENTS:
                    _, evicted = pool.popitem(last=False)
                    evicted.evicted = True
                    if evicted.borrows == 0 and not evicted.closing:
                        evicted.closing = True
                        _register_pending_close_client_locked(loop, evicted.client)
                        evicted_idle.append(evicted.client)
                    elif evicted.borrows > 0:
                        draining = _draining_pools.get(loop)
                        if draining is None:
                            draining = set()
                            _draining_pools[loop] = draining
                        draining.add(evicted)
            release_cb = _make_release(loop, chosen_entry)

    if loser is not None:
        _schedule_close_on_loop(loop, loser)
    for idle_client in evicted_idle:
        _schedule_close_on_loop(loop, idle_client)
    if chosen_entry is None or release_cb is None:
        return (None, None)
    return (chosen_entry.client, release_cb)


async def aclose_shared_llm_http_clients() -> None:
    """Concurrently close and forget every active and evicted shared LLM HTTP client.

    Close tasks are tracked on the loop and shielded from waiter cancellation:
    if a caller cancels while waiting, the caller receives ``CancelledError``
    while in-flight close tasks continue to completion and can be awaited by a
    subsequent shutdown call.
    """
    loop = asyncio.get_running_loop()
    with _POOL_LOCK:
        _loop_epochs[loop] = _loop_epochs.get(loop, 0) + 1
        _closing_loops[loop] = _closing_loops.get(loop, 0) + 1
        pool = _http_pools.pop(loop, None)
        draining = _draining_pools.pop(loop, None)
        pending_clients = _pending_close_clients.pop(loop, None)
        existing_tasks = list(_pending_close_tasks.get(loop, ()))
        entries: list[_SharedPoolEntry] = []
        if pool:
            for entry in pool.values():
                entry.evicted = True
                if not entry.closing:
                    entry.closing = True
                    entries.append(entry)
        if draining:
            for entry in draining:
                entry.evicted = True
                if not entry.closing:
                    entry.closing = True
                    entries.append(entry)
    try:
        clients: list[Any] = []
        seen_ids: set[int] = set()
        for entry in entries:
            client = entry.client
            cid = id(client)
            if cid not in seen_ids:
                seen_ids.add(cid)
                clients.append(client)
        if pending_clients:
            for client in pending_clients:
                cid = id(client)
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    clients.append(client)

        all_tasks: list[asyncio.Task[None]] = list(existing_tasks)
        for client in clients:
            task = _start_tracked_close_task(loop, client)
            if task is not None:
                all_tasks.append(task)
        if all_tasks:
            await asyncio.gather(
                *(asyncio.shield(task) for task in all_tasks),
                return_exceptions=True,
            )
    finally:
        with _POOL_LOCK:
            remaining = _closing_loops.get(loop, 0) - 1
            if remaining <= 0:
                _closing_loops.pop(loop, None)
            else:
                _closing_loops[loop] = remaining
