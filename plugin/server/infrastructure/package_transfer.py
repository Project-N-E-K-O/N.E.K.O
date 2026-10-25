"""Package downloads over ranked routes: a hedged opening race, then failover.

Market packages are mostly under a megabyte, so connection setup and GitHub's
redirect dominate their time; racing routes is what shortens them. Parallel
ranges measured no reliable gain on large packages and are deliberately absent.

Routes open in ranked order, at most RACE_WIDTH at a time, each once the
previous one has stayed silent for its expected round trip or has failed. The
first to send acceptable headers carries the whole body. When it fails
mid-stream, the remaining routes race again for the rest: one that honours the
Range request resumes after the bytes already written, one that ignores it
starts the file over. Routes that lost the opening race, or were still
connecting when it ended, go to the back of the queue, as does a route that
delivered bytes before failing, so untried routes come first and even a
single route survives dropped connections. A route leaves the queue only by
failing without progress, and the total deadline bounds the retries that do
make progress. Routes must agree on the size;
content integrity is left to the caller's SHA-256 check.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, BinaryIO, TypeVar

from plugin.logging_config import get_logger
from plugin.server.infrastructure.package_download import (
    PackageDownloadWriteError,
    PackageSizeExceeded,
    open_download_file,
    owned_download_file,
    package_download_deadline,
    package_http_client,
    run_file_operation,
)
from plugin.server.infrastructure.package_sources import (
    RouteObservation,
    commit_route_observations,
    estimate_route,
    route_generation,
)
from plugin.utils.asyncio_utils import await_cancellation_safe
from plugin.utils.http_imports import ensure_httpx

T = TypeVar("T")
logger = get_logger("server.infrastructure.package_transfer")

RACE_WIDTH = 3
# A route may stay silent for this multiple of its expected latency before the
# next one opens beside it. Unmeasured routes open together.
HEDGE_LATENCY_FACTOR = 1.5
HEDGE_MIN_SECONDS = 0.15
HEDGE_MAX_SECONDS = 1.0
CHUNK_BYTES = 64 * 1024
# A 200 MiB package needs only 50 worker writes; progress and cancellation
# are still checked every chunk.
FLUSH_BYTES = 4 * 1024 * 1024
# A smaller body ends before its connection ramps up and says little about throughput.
THROUGHPUT_SAMPLE_BYTES = 512 * 1024
# A remembered GitHub asset URL answering with one of these has expired.
EXPIRED_ASSET_STATUSES = frozenset((401, 403, 404, 410))

Route = tuple[str, str]


class _RejectedResponse(ValueError):
    """A route answered, but not with the requested package bytes."""


@dataclass
class _Opened:
    source_id: str
    url: str
    response: Any
    # Where the body starts in the package: the resume offset, or 0 when the route ignored Range.
    offset: int
    total: int | None
    latency: float
    headers_at: float


def _hedge_delay(source_id: str) -> float:
    estimate = estimate_route(source_id)
    delay = min(HEDGE_MAX_SECONDS, max(HEDGE_MIN_SECONDS, estimate.latency * HEDGE_LATENCY_FACTOR))
    return delay * estimate.confidence


def _declared_length(headers: Any) -> int | None:
    try:
        value = int(headers["content-length"])
    except (KeyError, ValueError, TypeError):
        return None
    return value if value >= 0 else None


def _content_range(value: str) -> tuple[int, int] | None:
    """(first byte, total size) of a ``bytes first-last/total`` header."""
    unit, _, spec = value.partition(" ")
    interval, _, size = spec.partition("/")
    first, _, last = interval.partition("-")
    try:
        first_byte, last_byte, total = int(first), int(last), int(size)
    except ValueError:
        return None
    if unit != "bytes" or not 0 <= first_byte <= last_byte < total:
        return None
    return first_byte, total


async def _file_operation(operation: Callable[..., T], *args: Any) -> T:
    try:
        return await run_file_operation(operation, *args)
    except OSError as exc:
        raise PackageDownloadWriteError(exc) from exc


def _restart_file(handle: BinaryIO) -> None:
    handle.seek(0)
    handle.truncate()


async def _discard(openings: Iterable[asyncio.Task[_Opened]]) -> None:
    """Close the responses of routes that lost the race, once they settle."""
    for result in await asyncio.gather(*openings, return_exceptions=True):
        if isinstance(result, _Opened):
            await result.response.aclose()


class _Transfer:
    def __init__(
        self, httpx: Any, routes: Sequence[Route], *, maximum_bytes: int,
        report_progress: Callable[[int, int | None], None],
        report_route: Callable[[str], None],
        check_cancelled: Callable[[], None],
    ) -> None:
        self.queue = list(routes)
        self.maximum_bytes = maximum_bytes
        self.report_progress = report_progress
        self.report_route = report_route
        self.check_cancelled = check_cancelled
        self.route_failures: tuple[type[Exception], ...] = (httpx.HTTPError, OSError, _RejectedResponse)
        # Bytes already in the file, and the package size once a route declares it.
        self.received = 0
        self.total: int | None = None
        # Where each route redirected, such as GitHub's signed asset URL, so
        # returning to it skips the redirect.
        self.resolved: dict[str, str] = {}
        self.observations: list[RouteObservation] = []
        self.last_error: Exception | None = None

    async def run(self, path: Path) -> None:
        async with open_download_file(path) as handle:
            while True:
                opened = await self._open_first()
                resumed_at = self.received
                try:
                    await self._carry(opened, handle)
                    return
                except self.route_failures as exc:
                    self.last_error = exc
                    logger.warning(
                        "[market-download] route failed mid-transfer source={} received={} error={}",
                        opened.source_id, self.received, type(exc).__name__,
                    )
                    if self.received > resumed_at:
                        self.queue.append((opened.source_id, opened.url))
                    if not self.queue:
                        raise

    async def _open_first(self) -> _Opened:
        """Open queued routes, staggered by hedge delay, until one answers acceptably."""
        pending: dict[asyncio.Task[_Opened], Route] = {}
        try:
            while True:
                wait: float | None = None
                if self.queue and len(pending) < RACE_WIDTH:
                    route = self.queue.pop(0)
                    pending[asyncio.create_task(self._open(*route))] = route
                    if self.queue:
                        wait = _hedge_delay(route[0])
                elif not pending:
                    assert self.last_error is not None, "every route failed without an error"
                    raise self.last_error
                done, _ = await asyncio.wait(pending, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
                opened: list[_Opened] = []
                fatal: BaseException | None = None
                for task in done:
                    source_id, _ = pending.pop(task)
                    error = task.exception()
                    if error is None:
                        opened.append(task.result())
                    elif isinstance(error, self.route_failures):
                        self.last_error = error
                        self.observations.append(RouteObservation(source_id, failed=True))
                        logger.info(
                            "[market-download] route did not open source={} error={}",
                            source_id, type(error).__name__,
                        )
                    else:
                        fatal = fatal or error
                winner = None if fatal else next(iter(opened), None)
                for loser in opened:
                    if loser is not winner:
                        await loser.response.aclose()
                        # It answered, so it can carry the rest if the winner fails.
                        self.queue.append((loser.source_id, loser.url))
                if fatal is not None:
                    raise fatal
                if winner is not None:
                    return winner
        finally:
            if pending:
                # Still connecting is not failing: these routes stay queued too.
                self.queue.extend(pending.values())
                for task in pending:
                    task.cancel()
                await await_cancellation_safe(asyncio.create_task(_discard(pending)))

    async def _open(self, source_id: str, url: str) -> _Opened:
        client = await package_http_client()
        headers = {"Accept-Encoding": "identity"}
        if self.received:
            headers["Range"] = f"bytes={self.received}-"
        began = time.monotonic()
        requested = self.resolved.get(source_id, url)
        response = await client.send(client.build_request("GET", requested, headers=headers), stream=True)
        if requested != url and response.status_code in EXPIRED_ASSET_STATUSES:
            await response.aclose()
            del self.resolved[source_id]
            response = await client.send(client.build_request("GET", url, headers=headers), stream=True)
        try:
            opened = self._accept(source_id, url, response, began)
        except BaseException:
            await response.aclose()
            raise
        if str(response.url) != url:
            self.resolved[source_id] = str(response.url)
        return opened

    def _accept(self, source_id: str, url: str, response: Any, began: float) -> _Opened:
        response.raise_for_status()
        # An encoded body is package bytes only after decoding, so its
        # length and range say nothing about the package.
        encoded = response.headers.get("content-encoding", "identity").lower() != "identity"
        if response.status_code == 206:
            parsed = _content_range(response.headers.get("content-range", ""))
            if encoded or not self.received or parsed is None or parsed[0] != self.received:
                raise _RejectedResponse("unexpected partial response")
            offset, total = parsed
        elif response.status_code == 200:
            offset, total = 0, None if encoded else _declared_length(response.headers)
        else:
            raise _RejectedResponse(f"unexpected status {response.status_code}")
        if total is not None:
            if self.total is not None and total != self.total:
                raise _RejectedResponse("route declared a different package size")
            if total > self.maximum_bytes:
                self.report_progress(self.received, total)
                raise PackageSizeExceeded(total, self.maximum_bytes, source="content_length")
        headers_at = time.monotonic()
        return _Opened(source_id, url, response, offset, total, headers_at - began, headers_at)

    async def _carry(self, opened: _Opened, handle: BinaryIO) -> None:
        """Write one route's body after the bytes already in the file."""
        try:
            self.report_route(opened.source_id)
            if opened.offset != self.received:
                await _file_operation(_restart_file, handle)
                self.received = 0
            if opened.total is not None:
                self.total = opened.total
            start = self.received
            self.report_progress(self.received, self.total)
            pending: list[bytes] = []
            pending_bytes = 0

            async def flush() -> None:
                nonlocal pending_bytes
                if pending:
                    await _file_operation(handle.writelines, list(pending))
                    pending.clear()
                    pending_bytes = 0

            try:
                async for chunk in opened.response.aiter_bytes(CHUNK_BYTES):
                    self.check_cancelled()
                    pending.append(chunk)
                    pending_bytes += len(chunk)
                    self.received += len(chunk)
                    if self.received > self.maximum_bytes:
                        self.report_progress(self.received, self.total)
                        raise PackageSizeExceeded(self.received, self.maximum_bytes)
                    if pending_bytes >= FLUSH_BYTES:
                        await flush()
                    self.report_progress(self.received, self.total)
                # A short body would leave a gap; anything else is for the SHA-256 check.
                if self.total is not None and self.received < self.total:
                    raise _RejectedResponse("body ended before the declared size")
            except self.route_failures:
                self.observations.append(RouteObservation(opened.source_id, failed=True))
                # Keep what arrived: the next route resumes after it.
                await flush()
                raise
            await flush()
        finally:
            await opened.response.aclose()
        body_seconds = time.monotonic() - opened.headers_at
        body_bytes = self.received - start
        sampled = body_bytes >= THROUGHPUT_SAMPLE_BYTES and body_seconds > 0
        self.observations.append(RouteObservation(
            opened.source_id, latency=opened.latency,
            throughput=body_bytes / body_seconds if sampled else None,
        ))


async def download_package(
    routes: Sequence[Route],
    directory: Path,
    *,
    maximum_bytes: int,
    total_timeout: float,
    report_progress: Callable[[int, int | None], None],
    report_route: Callable[[str], None],
    check_cancelled: Callable[[], None],
) -> Path:
    """Download one package over ``routes``, best first, into a new file in ``directory``.

    Raises the last route's error once every route has failed. What the
    transfer learned about each route is recorded however it ends.
    """
    if not routes:
        raise ValueError("no download routes")
    check_cancelled()
    httpx = await ensure_httpx()
    generation = route_generation()
    transfer = _Transfer(
        httpx, routes, maximum_bytes=maximum_bytes, report_progress=report_progress,
        report_route=report_route, check_cancelled=check_cancelled,
    )
    try:
        async with owned_download_file(directory) as path:
            async with package_download_deadline(total_timeout):
                await transfer.run(path)
        return path
    finally:
        await commit_route_observations(transfer.observations, generation)
