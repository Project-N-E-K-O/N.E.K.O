"""Small resilience helpers for the web-search plugin.

The caller may invoke search frequently, but identical concurrent queries are
collapsed into one upstream request and recent results are served from memory.
Network retries are deliberately bounded so a traffic spike is not amplified.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random
import time
from typing import Any, Awaitable, Callable, Dict, Hashable, List, Mapping, Optional

import httpx

SearchResults = List[Dict[str, str]]

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_NO_RETRY_STATUS = frozenset({429})


def _copy_results(results: SearchResults) -> SearchResults:
    return [dict(item) for item in results]


def retry_after_seconds(
    headers: Mapping[str, str],
    *,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Parse Retry-After seconds or an HTTP date; invalid values are ignored."""
    value = headers.get("Retry-After")
    if not value:
        return None
    try:
        delay = float(value.strip())
        return max(0.0, delay) if math.isfinite(delay) else None
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(value)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return max(0.0, (target - current).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def should_skip_fallback(error: BaseException) -> bool:
    """Return whether another endpoint would violate an upstream cooldown."""
    if getattr(error, "retry_after_seconds", None) is not None:
        return True
    if not isinstance(error, httpx.HTTPStatusError):
        return False
    response = error.response
    return response.status_code == 429 or retry_after_seconds(response.headers) is not None


class SearchCooldownError(RuntimeError):
    """The selected search engine is cooling down after an upstream block."""


class SearchBusyError(RuntimeError):
    """The selected search engine already has a different query in flight."""


@dataclass
class _BackendState:
    lock: asyncio.Lock
    next_allowed: float = 0.0
    cooldown_until: float = 0.0
    block_count: int = 0


async def request_with_retry(
    request: Callable[[], Awaitable[httpx.Response]],
    *,
    max_attempts: int = 2,
    base_delay: float = 0.5,
    max_delay: float = 4.0,
) -> httpx.Response:
    """Retry bounded transport/5xx failures, but never retry rate limits."""
    attempts = max(1, min(int(max_attempts), 3))
    base = max(0.0, float(base_delay))
    delay_cap = max(base, float(max_delay))

    for attempt in range(attempts):
        try:
            response = await request()
            if response.status_code in _NO_RETRY_STATUS:
                response.raise_for_status()
            if response.status_code not in _RETRYABLE_STATUS:
                response.raise_for_status()
                return response
            if attempt + 1 >= attempts:
                response.raise_for_status()
            server_delay = retry_after_seconds(response.headers)
            # Never retry earlier than the server requested. A long cooldown is
            # outside this interactive call's small retry budget, so surface the
            # 429/5xx now instead of increasing anti-bot pressure.
            if server_delay is not None and server_delay > delay_cap:
                response.raise_for_status()
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt + 1 >= attempts:
                raise
            server_delay = None

        exponential = min(delay_cap, base * (2**attempt))
        delay = server_delay if server_delay is not None else exponential
        # A small jitter prevents simultaneous callers from retrying in lockstep.
        delay = min(delay_cap, delay + random.uniform(0.0, max(0.05, delay * 0.25)))
        await asyncio.sleep(delay)

    raise RuntimeError("unreachable retry state")


@dataclass(frozen=True)
class _CacheEntry:
    results: SearchResults
    fresh_until: float
    stale_until: float


@dataclass
class _InflightFlight:
    """Per-key coalesced fetch state tracking active waiter deadlines."""

    task: asyncio.Task[SearchResults]
    waiter_deadlines: Dict[object, Optional[float]] = field(default_factory=dict)
    fetch_started: bool = False
    wait_until: Optional[float] = None
    _deadline_event: asyncio.Event = field(default_factory=asyncio.Event)
    _state_event: asyncio.Event = field(default_factory=asyncio.Event)

    def effective_wait_deadline(self) -> Optional[float]:
        if not self.waiter_deadlines:
            return None
        deadlines = self.waiter_deadlines.values()
        if any(deadline is None for deadline in deadlines):
            return None
        return max(d for d in deadlines if d is not None)

    def notify_deadline_changed(self) -> None:
        self._deadline_event.set()
        self._deadline_event = asyncio.Event()

    def notify_state_changed(self) -> None:
        self._state_event.set()
        self._state_event = asyncio.Event()


class SearchCoordinator:
    """TTL cache plus single-flight coalescing for identical searches."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 120.0,
        stale_seconds: float = 600.0,
        max_entries: int = 128,
        min_interval_seconds: float = 0.75,
        cooldown_seconds: float = 60.0,
        max_cooldown_seconds: float = 3600.0,
        queue_wait_seconds: float = 2.0,
    ) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.stale_seconds = max(0.0, float(stale_seconds))
        self.max_entries = max(1, min(int(max_entries), 1024))
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.max_cooldown_seconds = max(
            self.cooldown_seconds,
            float(max_cooldown_seconds),
        )
        self.queue_wait_seconds = max(0.0, float(queue_wait_seconds))
        self._cache: OrderedDict[Hashable, _CacheEntry] = OrderedDict()
        self._inflight: Dict[Hashable, _InflightFlight] = {}
        self._backends: Dict[Hashable, _BackendState] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _cooldown_for_error(
        self,
        state: _BackendState,
        error: BaseException,
    ) -> Optional[float]:
        declared = getattr(error, "retry_after_seconds", None)
        server_delay: Optional[float] = None
        is_block = bool(getattr(error, "is_search_block", False)) or declared is not None
        if isinstance(error, httpx.HTTPStatusError):
            response = error.response
            server_delay = retry_after_seconds(response.headers)
            is_block = response.status_code == 429 or server_delay is not None
        if not is_block:
            return None

        state.block_count += 1
        exponent = min(state.block_count - 1, 30)
        progressive = min(
            self.max_cooldown_seconds,
            self.cooldown_seconds * (2**exponent),
        )
        values = [progressive, server_delay or 0.0]
        if declared is not None:
            try:
                values.append(float(declared))
            except (TypeError, ValueError):
                pass
        # The configured cap applies to our own progressive penalty. A longer
        # upstream Retry-After must still be respected.
        return max(values)

    async def _acquire_backend_lock(
        self,
        state: _BackendState,
        flight: Optional[_InflightFlight],
        wait_deadline: Optional[float],
    ) -> None:
        if not state.lock.locked():
            await state.lock.acquire()
            return
        start = time.monotonic()
        queue_deadline = start + self.queue_wait_seconds
        acquire_task = asyncio.create_task(state.lock.acquire())
        try:
            while not acquire_task.done():
                deadline_event = flight._deadline_event if flight is not None else None
                effective = (
                    flight.effective_wait_deadline()
                    if flight is not None
                    else wait_deadline
                )
                limit = (
                    queue_deadline
                    if effective is None
                    else min(queue_deadline, effective)
                )
                remaining = limit - time.monotonic()
                if remaining <= 0:
                    raise SearchBusyError("search backend is busy; retry shortly")
                wait_tasks: set[asyncio.Task[Any]] = {acquire_task}
                event_task: Optional[asyncio.Task[bool]] = None
                if deadline_event is not None:
                    event_task = asyncio.create_task(deadline_event.wait())
                    wait_tasks.add(event_task)
                try:
                    done, _pending = await asyncio.wait(
                        wait_tasks,
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    if event_task is not None and not event_task.done():
                        event_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await event_task
                if acquire_task in done:
                    acquire_task.result()
                    return
                if (
                    deadline_event is not None
                    and ((event_task is not None and event_task in done) or deadline_event.is_set())
                ):
                    continue
                raise SearchBusyError("search backend is busy; retry shortly")
        except BaseException:
            if acquire_task.done() and not acquire_task.cancelled():
                with suppress(Exception):
                    if acquire_task.exception() is None:
                        state.lock.release()
            else:
                acquire_task.cancel()
                try:
                    acquired = await acquire_task
                except asyncio.CancelledError:
                    acquired = False
                if acquired:
                    state.lock.release()
            raise

    async def _guarded_fetch(
        self,
        backend: Hashable,
        fetch: Callable[[], Awaitable[SearchResults]],
        *,
        wait_deadline: Optional[float] = None,
        flight: Optional[_InflightFlight] = None,
    ) -> SearchResults:
        state = self._backends.setdefault(backend, _BackendState(asyncio.Lock()))
        await self._acquire_backend_lock(state, flight, wait_deadline)
        try:
            now = time.monotonic()
            if now < state.cooldown_until:
                remaining = state.cooldown_until - now
                raise SearchCooldownError(
                    f"search backend cooling down for {remaining:.1f}s"
                )
            wait_seconds = state.next_allowed - now
            if wait_seconds > 0:
                effective = (
                    flight.effective_wait_deadline()
                    if flight is not None
                    else wait_deadline
                )
                if effective is not None:
                    remaining_wait = effective - now
                    if remaining_wait <= 0 or wait_seconds >= remaining_wait:
                        raise SearchBusyError(
                            "search backend rate-limited; retry shortly"
                        )
                if flight is not None:
                    flight.wait_until = state.next_allowed
                    flight.notify_state_changed()
                await asyncio.sleep(wait_seconds)
            if flight is not None:
                flight.wait_until = None
                flight.fetch_started = True
                flight.notify_state_changed()
            try:
                results = await fetch()
            except Exception as error:
                cooldown = self._cooldown_for_error(state, error)
                if cooldown is not None:
                    state.cooldown_until = max(
                        state.cooldown_until,
                        time.monotonic() + cooldown,
                    )
                raise
            else:
                state.block_count = 0
                return results
            finally:
                # Space requests from completion, including failed attempts.
                state.next_allowed = max(
                    state.next_allowed,
                    time.monotonic() + self.min_interval_seconds,
                )
        finally:
            state.lock.release()

    def _entry(self, key: Hashable, *, fresh: bool) -> Optional[SearchResults]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        deadline = entry.fresh_until if fresh else entry.stale_until
        if time.monotonic() > deadline:
            if not fresh:
                self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return _copy_results(entry.results)

    def _store(self, key: Hashable, results: SearchResults) -> None:
        now = time.monotonic()
        self._cache[key] = _CacheEntry(
            results=_copy_results(results),
            fresh_until=now + self.ttl_seconds,
            stale_until=now + self.ttl_seconds + self.stale_seconds,
        )
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)

    def stale(self, key: Hashable) -> Optional[SearchResults]:
        """Return a retained result after a caller-level timeout, if available."""
        return self._entry(key, fresh=False)

    async def _await_flight(
        self,
        flight: _InflightFlight,
        caller_deadline: Optional[float],
    ) -> SearchResults:
        if caller_deadline is None:
            return await asyncio.shield(flight.task)
        while not flight.task.done() and not flight.fetch_started:
            state_event = flight._state_event
            if flight.wait_until is not None and flight.wait_until >= caller_deadline:
                raise SearchBusyError("search backend rate-limited; retry shortly")
            remaining = max(0.0, caller_deadline - time.monotonic())
            wait_tasks: set[asyncio.Task[Any]] = {flight.task}
            event_task = asyncio.create_task(state_event.wait())
            wait_tasks.add(event_task)
            try:
                done, _pending = await asyncio.wait(
                    wait_tasks,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                if not event_task.done():
                    event_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await event_task
            if flight.task in done:
                return flight.task.result()
            if event_task in done or state_event.is_set():
                continue
            raise SearchBusyError("search backend is busy; retry shortly")
        return await asyncio.shield(flight.task)

    async def run(
        self,
        key: Hashable,
        fetch: Callable[[], Awaitable[SearchResults]],
        *,
        max_wait_seconds: Optional[float] = None,
    ) -> SearchResults:
        cached = self._entry(key, fresh=True)
        if cached is not None:
            return cached

        caller_deadline: Optional[float] = None
        if max_wait_seconds is not None:
            wait_budget = float(max_wait_seconds)
            if math.isfinite(wait_budget):
                caller_deadline = time.monotonic() + max(0.0, wait_budget)

        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            # Host lifecycles may use separate asyncio.run() calls. Futures must
            # never leak into a different event loop; the plain cache may persist.
            self._loop = loop
            self._inflight.clear()
            self._backends.clear()

        waiter_token = object()
        flight = self._inflight.get(key)
        if flight is None:
            backend = key[0] if isinstance(key, tuple) and key else key

            async def fetch_and_cache() -> SearchResults:
                results = await self._guarded_fetch(
                    backend,
                    fetch,
                    wait_deadline=caller_deadline,
                    flight=flight,
                )
                if results:
                    self._store(key, results)
                else:
                    self._cache.pop(key, None)
                return results

            task = loop.create_task(fetch_and_cache())
            flight = _InflightFlight(
                task=task,
                waiter_deadlines={waiter_token: caller_deadline},
            )
            self._inflight[key] = flight

            def finish(done: asyncio.Task[SearchResults]) -> None:
                if self._inflight.get(key) is flight:
                    self._inflight.pop(key, None)
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(finish)
        else:
            flight.waiter_deadlines[waiter_token] = caller_deadline
            flight.notify_deadline_changed()

        try:
            return _copy_results(await self._await_flight(flight, caller_deadline))
        except Exception:
            stale = self._entry(key, fresh=False)
            if stale is not None:
                return stale
            raise
        finally:
            flight.waiter_deadlines.pop(waiter_token, None)
            if flight.waiter_deadlines:
                flight.notify_deadline_changed()
            elif not flight.task.done():
                if self._inflight.get(key) is flight:
                    self._inflight.pop(key, None)
                flight.task.cancel()
                # Python 3.11 can race cancellation with the internal lock
                # waiter. Give it a turn and repeat cancellation so a caller
                # timeout remains a hard bound without leaking the fetch.
                await asyncio.sleep(0)
                if not flight.task.done():
                    flight.task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await flight.task
