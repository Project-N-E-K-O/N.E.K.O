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

from plugin.logging_config import get_logger
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
from plugin.utils.http_imports import ensure_httpx

T = TypeVar("T")
logger = get_logger("server.infrastructure.package_fetch")
POLL_SECONDS = 0.1
# How long a stopped caller waits for the transfer to close its responses and
# remove its partial file. Bounded so a wedged loop cannot hold it forever.
CLEANUP_SECONDS = 10.0


class NoRoutesLeft(Exception):
    """Every route for the asset was excluded by the caller."""


class PackageLoopUnavailable(RuntimeError):
    """The server loop stopped before the transfer finished."""


class RouteFailed(Exception):
    """A ``single_route`` transfer failed; ``source_id`` names the route it used."""

    def __init__(self, source_id: str, error: BaseException) -> None:
        super().__init__(f"route {source_id} failed: {type(error).__name__}: {error}")
        self.source_id = source_id
        self.error = error


@dataclass(frozen=True)
class FetchedAsset:
    path: Path
    """A new file in the requested directory; the caller owns and removes it."""
    sources: tuple[str, ...]
    """Every route whose bytes are in the file, in order. Several when a
    transfer resumed on another route after a failure."""
    generation: int
    """The route history generation the transfer started in."""

    @property
    def single_source(self) -> str | None:
        """The one route that delivered the whole file, or None for a spliced file."""
        return self.sources[0] if len(self.sources) == 1 else None


def _start_private_loop() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run() -> None:
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        try:
            loop.run_forever()
        finally:
            loop.close()

    thread = threading.Thread(target=run, name="package-fetch-loop", daemon=True)
    thread.start()
    ready.wait()
    return loop, thread


def _stop_private_loop(loop: asyncio.AbstractEventLoop, thread: threading.Thread, wait: float) -> None:
    """Let the private loop finish what it still runs, then stop and close it.

    A transfer the caller stopped may still be cleaning up, or a route
    history write still saving: they complete on the loop's own thread. The
    caller waits for that for at most ``wait`` seconds.
    """

    async def drain() -> None:
        current = asyncio.current_task()
        remaining = [task for task in asyncio.all_tasks() if task is not current]
        if remaining:
            # Bounded: a cleanup that hangs must not keep the loop alive for good.
            await asyncio.wait(remaining, timeout=CLEANUP_SECONDS)
        try:
            # The shared client now belongs to this loop; close it with the loop.
            await aclose_package_http_client()
        finally:
            loop.stop()

    asyncio.run_coroutine_threadsafe(drain(), loop)
    thread.join(wait)


def _supervise(
    loop: asyncio.AbstractEventLoop,
    factory: Callable[[], Coroutine[Any, Any, T]],
    check_stopped: Callable[[], None],
    on_poll: Callable[[], None],
    cleanup_seconds: float,
    discard: Callable[[T], None] | None = None,
) -> T:
    """Run ``factory()`` on ``loop`` and wait for it on this thread.

    When ``check_stopped`` or ``on_poll`` raises, the task is cancelled and
    awaited for up to ``cleanup_seconds`` before the error propagates. A
    result the caller will not receive because of that (the task completed
    meanwhile, or completes later) is handed to ``discard`` exactly once.
    """
    finished = threading.Event()
    holder: dict[str, asyncio.Task[T]] = {}
    abandon = threading.Lock()
    state = {"abandoned": False, "discarded": False}

    def discard_result(task: asyncio.Task[T]) -> None:
        with abandon:
            if not state["abandoned"] or state["discarded"]:
                return
            if task.cancelled() or task.exception() is not None:
                return
            state["discarded"] = True
        if discard is not None:
            try:
                discard(task.result())
            except Exception as exc:
                # Must not hide the error being propagated, nor block finished.
                logger.warning("could not discard an abandoned package download: {}", exc)

    def done(task: asyncio.Task[T]) -> None:
        try:
            if not task.cancelled():
                task.exception()  # Retrieved here, so the loop does not log it as unhandled.
            discard_result(task)
        finally:
            finished.set()

    def start() -> None:
        task = loop.create_task(factory())
        task.add_done_callback(done)
        holder["task"] = task

    def cancel() -> None:
        task = holder.get("task")
        if task is not None:
            task.cancel()

    loop.call_soon_threadsafe(start)
    while not finished.wait(POLL_SECONDS):
        try:
            on_poll()
            check_stopped()
            if loop.is_closed() or not loop.is_running():
                raise PackageLoopUnavailable("the server loop stopped during a package download")
        except BaseException:
            with abandon:
                state["abandoned"] = True
            # Callbacks run in order, so this cancel follows the start.
            try:
                loop.call_soon_threadsafe(cancel)
            except RuntimeError:
                pass  # The loop is closed; nothing of the transfer runs any more.
            else:
                finished.wait(cleanup_seconds)
            task = holder.get("task")
            if task is not None and task.done():
                # It may have completed while on_poll ran, before the cancel.
                discard_result(task)
            raise
    task = holder["task"]
    if task.cancelled():
        # Cancelled on the loop's side, e.g. by the server shutting down.
        raise PackageLoopUnavailable("the package download was cancelled on the server loop")
    return task.result()


def _run(
    factory: Callable[[], Coroutine[Any, Any, T]],
    check_stopped: Callable[[], None],
    on_poll: Callable[[], None] = lambda: None,
    cleanup_seconds: float = CLEANUP_SECONDS,
    discard: Callable[[T], None] | None = None,
) -> T:
    loop = package_loop()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("package_fetch blocks; call it from a worker thread, not an event loop")
    if loop is not None:
        return _supervise(loop, factory, check_stopped, on_poll, cleanup_seconds, discard)
    private, thread = _start_private_loop()
    try:
        return _supervise(private, factory, check_stopped, on_poll, cleanup_seconds, discard)
    finally:
        _stop_private_loop(private, thread, cleanup_seconds)


def fetch_release_asset(
    url: str,
    directory: Path,
    *,
    maximum_bytes: int,
    deadline: float,
    check_stopped: Callable[[], None],
    progress: Callable[[int, int | None], None],
    exclude: Collection[str] = (),
    single_route: bool = False,
) -> FetchedAsset:
    """Download one GitHub release asset over the market's routes. Blocks.

    ``deadline`` is a ``time.monotonic()`` value; past it the transfer raises
    :class:`PackageDownloadDeadline`. ``check_stopped`` is called on the loop
    for every chunk and on this thread while waiting; whatever it raises stops
    the transfer and propagates. ``progress`` is called on this thread.
    Routes named in ``exclude`` are skipped; :class:`NoRoutesLeft` when that
    leaves none. With ``single_route`` only the best remaining route is used,
    so a file that fails its checksum can be blamed on it, and its failure is
    raised as :class:`RouteFailed`. Other failures are those of
    ``download_package``.
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
        httpx = await ensure_httpx()
        generation = route_generation()
        routes = [route for route in download_candidates(url, preferences) if route[0] not in exclude]
        if not routes:
            raise NoRoutesLeft(f"no download route left for {url}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PackageDownloadDeadline("Package download total deadline expired")
        if single_route:
            routes = routes[:1]
        # Routes whose bytes the file holds. The transfer reports a route as
        # it starts carrying, then its first progress gives the offset it
        # writes from: 0 means it started the file over.
        sources: list[str] = []
        starting: list[str | None] = [None]

        def report_route(source_id: str) -> None:
            starting[0] = source_id

        def report_progress(received: int, total: int | None) -> None:
            source_id = starting[0]
            if source_id is not None:
                starting[0] = None
                if not received:
                    sources.clear()
                if source_id not in sources:
                    sources.append(source_id)
            latest[0] = (received, total)

        stops: list[BaseException] = []

        def check_cancelled() -> None:
            try:
                check_stopped()
            except BaseException as stop:
                stops.append(stop)
                raise

        try:
            path = await download_package(
                routes, directory, maximum_bytes=maximum_bytes, total_timeout=remaining,
                report_progress=report_progress, report_route=report_route, check_cancelled=check_cancelled,
            )
        except (OSError, ValueError, httpx.HTTPError) as exc:
            # What the route did: not the deadline, not a stop (whatever its
            # type), and not a local write failure (not OSError or ValueError).
            if single_route and not isinstance(exc, PackageDownloadDeadline) and exc not in stops:
                raise RouteFailed(routes[0][0], exc) from exc
            raise
        return FetchedAsset(path, tuple(sources) or (routes[0][0],), generation)

    asset = _run(fetch, check_stopped, relay, discard=lambda unused: unused.path.unlink(missing_ok=True))
    try:
        relay()
    except BaseException:
        # The caller never receives the file, so it is removed here.
        asset.path.unlink(missing_ok=True)
        raise
    return asset


def record_route_failure(source_id: str, generation: int, *, check_stopped: Callable[[], None]) -> None:
    """Penalize one route in the shared history, e.g. for serving a file that failed its checksum.

    The observation counts as soon as the server loop takes it; this waits
    only for it to be saved, and stops waiting when ``check_stopped`` raises.
    Best effort: a failure to save only loses the observation after a restart.
    """

    stops: list[BaseException] = []

    def checked() -> None:
        try:
            check_stopped()
        except BaseException as stop:
            stops.append(stop)
            raise

    async def commit() -> None:
        # A caller stopped before the loop got here leaves the history alone.
        checked()
        await commit_route_observations([RouteObservation(source_id, failed=True)], generation)

    try:
        # Saving needs no cleanup, so a stopped caller does not wait for it.
        _run(commit, checked, cleanup_seconds=0)
    except (OSError, PackageLoopUnavailable) as exc:
        if exc in stops:
            raise  # A stop, whatever its type, reaches the caller.
        logger.warning("could not record a failure of download route {}: {}", source_id, exc)
