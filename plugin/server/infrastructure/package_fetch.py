"""Blocking entry to the package transfer for code running on worker threads.

Plugin tool preparation (uv and the managed Python, see plugin_tool_runtime)
runs on a worker thread, while the market's routes, user preferences and
route statistics belong to the server's event loop. This module submits one
release-asset download to that loop through the same ``download_candidates``
ranking and ``download_package`` transfer the market uses, so both share one
preference and one history. In a process without a bound server loop (a
script, a test) the transfer runs on a private loop for its duration.

The caller's thread polls while the transfer runs: it relays progress on its
own thread, and when its stop check raises (cancel, deadline) it cancels the
transfer and waits for its cleanup before propagating. Integrity is the
caller's job: it checks the returned file against a digest it trusts, and
reports a route that served wrong bytes with :func:`record_route_failure`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Coroutine
from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Any, TypeVar

from plugin.server.infrastructure.package_download import (
    PackageDownloadDeadline,
    aclose_package_http_client,
    package_loop,
)
from plugin.server.infrastructure.package_sources import (
    RouteObservation,
    commit_route_observations,
    download_candidates,
    load_preferences,
    route_generation,
)
from plugin.server.infrastructure.package_transfer import download_package

T = TypeVar("T")
POLL_SECONDS = 0.1
# How long a stopped caller waits for the transfer to close its responses and
# remove its partial file. Bounded so a wedged loop cannot hold it forever.
CLEANUP_SECONDS = 10.0


class NoRoutesLeft(Exception):
    """Every route for the asset was excluded by the caller."""


class PackageLoopUnavailable(RuntimeError):
    """The server loop stopped before the transfer finished."""


@dataclass(frozen=True)
class FetchedAsset:
    path: Path
    """A new file in the requested directory; the caller owns and removes it."""
    source_id: str
    """The route that delivered the last bytes."""
    generation: int
    """The route history generation the transfer started in."""


def _start_private_loop() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run() -> None:
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        loop.run_forever()

    thread = threading.Thread(target=run, name="package-fetch-loop", daemon=True)
    thread.start()
    ready.wait()
    return loop, thread


def _stop_private_loop(loop: asyncio.AbstractEventLoop, thread: threading.Thread) -> None:
    try:
        # The shared client now belongs to this loop; close it with the loop.
        asyncio.run_coroutine_threadsafe(aclose_package_http_client(), loop).result(CLEANUP_SECONDS)
    except Exception:
        pass  # Best effort: the loop is discarded either way.
    loop.call_soon_threadsafe(loop.stop)
    thread.join(CLEANUP_SECONDS)
    if not thread.is_alive():
        loop.close()


def _supervise(
    loop: asyncio.AbstractEventLoop,
    factory: Callable[[], Coroutine[Any, Any, T]],
    check_stopped: Callable[[], None],
    on_poll: Callable[[], None],
) -> T:
    """Run ``factory()`` on ``loop`` and wait for it on this thread."""
    finished = threading.Event()
    holder: dict[str, asyncio.Task[T]] = {}

    def start() -> None:
        task = loop.create_task(factory())
        task.add_done_callback(lambda _task: finished.set())
        holder["task"] = task

    def cancel() -> None:
        task = holder.get("task")
        if task is not None:
            task.cancel()

    loop.call_soon_threadsafe(start)
    while not finished.wait(POLL_SECONDS):
        on_poll()
        try:
            check_stopped()
            if loop.is_closed() or not loop.is_running():
                raise PackageLoopUnavailable("the server loop stopped during a package download")
        except BaseException:
            # Callbacks run in order, so this cancel follows the start.
            try:
                loop.call_soon_threadsafe(cancel)
            except RuntimeError:
                pass  # The loop is closed; nothing of the transfer runs any more.
            else:
                finished.wait(CLEANUP_SECONDS)
            raise
    on_poll()
    return holder["task"].result()


def _run(
    factory: Callable[[], Coroutine[Any, Any, T]],
    check_stopped: Callable[[], None],
    on_poll: Callable[[], None] = lambda: None,
) -> T:
    loop = package_loop()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("package_fetch blocks; call it from a worker thread, not an event loop")
    if loop is not None:
        return _supervise(loop, factory, check_stopped, on_poll)
    private, thread = _start_private_loop()
    try:
        return _supervise(private, factory, check_stopped, on_poll)
    finally:
        _stop_private_loop(private, thread)


def fetch_release_asset(
    url: str,
    directory: Path,
    *,
    maximum_bytes: int,
    deadline: float,
    check_stopped: Callable[[], None],
    progress: Callable[[int, int | None], None],
    exclude: Collection[str] = (),
) -> FetchedAsset:
    """Download one GitHub release asset over the market's routes. Blocks.

    ``deadline`` is a ``time.monotonic()`` value; past it the transfer raises
    :class:`PackageDownloadDeadline`. ``check_stopped`` is called on the loop
    for every chunk and on this thread while waiting; whatever it raises stops
    the transfer and propagates. ``progress`` is called on this thread.
    Routes named in ``exclude`` are skipped; :class:`NoRoutesLeft` when that
    leaves none. Other failures are those of ``download_package``.
    """
    # The preference file is read here, off the loop.
    preferences, _ = load_preferences()
    # Written on the loop, read on this thread; replacing the item is atomic.
    latest: list[tuple[int, int | None] | None] = [None]
    reported: list[tuple[int, int | None] | None] = [None]

    def relay() -> None:
        current = latest[0]
        if current is not None and current != reported[0]:
            reported[0] = current
            progress(*current)

    async def fetch() -> FetchedAsset:
        generation = route_generation()
        routes = [route for route in download_candidates(url, preferences) if route[0] not in exclude]
        if not routes:
            raise NoRoutesLeft(f"no download route left for {url}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PackageDownloadDeadline("Package download total deadline expired")
        carried: list[str] = []

        def report_progress(received: int, total: int | None) -> None:
            latest[0] = (received, total)

        path = await download_package(
            routes, directory, maximum_bytes=maximum_bytes, total_timeout=remaining,
            report_progress=report_progress, report_route=carried.append, check_cancelled=check_stopped,
        )
        return FetchedAsset(path, carried[-1], generation)

    return _run(fetch, check_stopped, relay)


def record_route_failure(asset: FetchedAsset) -> None:
    """Penalize the route that delivered ``asset``, e.g. for failing its checksum. Blocks.

    Best effort: a failure here only loses one observation.
    """

    async def commit() -> None:
        await commit_route_observations([RouteObservation(asset.source_id, failed=True)], asset.generation)

    try:
        _run(commit, lambda: None)
    except Exception:
        pass
