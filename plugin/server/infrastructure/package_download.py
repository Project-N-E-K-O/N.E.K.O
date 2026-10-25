"""Shared package download infrastructure: HTTP client, file ownership and errors."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
import os
from pathlib import Path
import tempfile
from types import ModuleType
from typing import Any, BinaryIO, Literal, TypeVar

from plugin.logging_config import get_logger
from plugin.utils.asyncio_utils import await_cancellation_safe
from plugin.utils.http_imports import ensure_httpx

T = TypeVar("T")
logger = get_logger("server.infrastructure.package_download")
PACKAGE_CONNECTION_LIMIT = 16
# Long enough to span a catalogue lookup or a batch of updates; servers that
# close idle connections sooner only cost the reconnect they would anyway.
PACKAGE_KEEPALIVE_SECONDS = 90.0
# Also bounds a silent connection: no byte for this long fails the request.
REQUEST_TIMEOUT = 10.0
CONNECT_TIMEOUT = 5.0
WARMUP_TIMEOUT = 5.0
_shared_client: tuple[asyncio.AbstractEventLoop, ModuleType, Any] | None = None


async def package_http_client() -> Any:
    """Return the keep-alive client every package request shares.

    TLS setup and GitHub's redirect dominate typical (sub-megabyte) packages,
    so connections outlive one download. Requests beyond the pool limit wait
    for a free connection rather than fail. A client belongs to one loop and
    HTTP backend.
    """
    global _shared_client
    httpx = await ensure_httpx()
    loop = asyncio.get_running_loop()
    if _shared_client is not None:
        owner, backend, client = _shared_client
        if owner is loop and backend is httpx and not client.is_closed:
            return client
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT, pool=None),
        follow_redirects=True, max_redirects=5,
        limits=httpx.Limits(
            max_connections=PACKAGE_CONNECTION_LIMIT, max_keepalive_connections=PACKAGE_CONNECTION_LIMIT,
            keepalive_expiry=PACKAGE_KEEPALIVE_SECONDS,
        ),
    )
    _shared_client = (loop, httpx, client)
    return client


async def aclose_package_http_client() -> None:
    global _shared_client
    if _shared_client is None:
        return
    owner, _, client = _shared_client
    _shared_client = None
    if owner is asyncio.get_running_loop():
        await client.aclose()


async def warm_package_connections(urls: Iterable[str]) -> None:
    """Open keep-alive connections to these URLs' origins for a following download.

    Only the origin root is requested, which answers quickly and does not ask
    a proxy to fetch the package. Best effort by design: a route that cannot
    be reached here is raced and reported by the download itself.
    """
    httpx = await ensure_httpx()
    client = await package_http_client()
    origins = dict.fromkeys(str(httpx.URL(url).copy_with(path="/", query=None, fragment=None)) for url in urls)

    async def warm(origin: str) -> None:
        try:
            await client.head(origin, follow_redirects=False, timeout=WARMUP_TIMEOUT)
        except httpx.HTTPError as exc:
            logger.debug("package connection warm-up failed origin={} error={}", origin, type(exc).__name__)

    await asyncio.gather(*(warm(origin) for origin in origins))


class PackageDownloadDeadline(TimeoutError):
    """The downloader total deadline expired, rather than a filesystem error."""


class PackageDownloadWriteError(Exception):
    """An error from a package file operation, not the HTTP transport."""

    def __init__(self, error: OSError) -> None:
        self.error = error
        super().__init__(str(error))


class PackageSizeExceeded(ValueError):
    def __init__(
        self, actual: int, maximum: int, *,
        source: Literal["content_length", "received_bytes"] = "received_bytes",
    ) -> None:
        self.actual = actual
        self.maximum = maximum
        self.source = source
        super().__init__(f"Package size {actual} exceeds {maximum} bytes")


def _create_download_file(directory: Path) -> Path:
    name: str | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            prefix="neko-market-", suffix=".neko-plugin", dir=directory
        )
        os.close(fd)
    except OSError as exc:
        if name is not None:
            cleanup_download_file(Path(name))
        raise PackageDownloadWriteError(exc) from exc
    return Path(name)


def cleanup_download_file(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("failed to remove downloaded package {}: {}", path, exc)


@asynccontextmanager
async def open_download_file(path: Path) -> AsyncIterator[BinaryIO]:
    """Open a package file for writing; a close failure is a write error."""
    opening = asyncio.create_task(asyncio.to_thread(path.open, "wb"))
    failed = False
    try:
        try:
            handle = await await_cancellation_safe(opening)
        except OSError as exc:
            raise PackageDownloadWriteError(exc) from exc
        yield handle
    except BaseException:
        failed = True
        raise
    finally:
        if opening.done() and not opening.cancelled() and opening.exception() is None:
            try:
                await run_file_operation(opening.result().close)
            except OSError as exc:
                if not failed:
                    raise PackageDownloadWriteError(exc) from exc
                logger.warning("failed to close package while propagating another exception: {}", exc)


@asynccontextmanager
async def owned_download_file(directory: Path) -> AsyncIterator[Path]:
    """Create a package file in ``directory``; remove it unless the block completes."""
    creation = asyncio.create_task(asyncio.to_thread(_create_download_file, directory))
    completed = False
    try:
        yield await await_cancellation_safe(creation)
        completed = True
    finally:
        if not completed and creation.done() and not creation.cancelled() and creation.exception() is None:
            await run_file_operation(cleanup_download_file, creation.result())


@asynccontextmanager
async def package_download_deadline(total_timeout: float) -> AsyncIterator[None]:
    """Bound a download; its own expiry, unlike a file timeout, is a deadline error."""
    deadline = asyncio.timeout(total_timeout)
    try:
        async with deadline:
            yield
    except TimeoutError as exc:
        if deadline.expired():
            raise PackageDownloadDeadline("Package download total deadline expired") from exc
        raise


async def run_file_operation(operation: Callable[..., T], *args: Any) -> T:
    """Run blocking file work off the loop; it finishes even if the caller is cancelled."""
    return await await_cancellation_safe(asyncio.create_task(asyncio.to_thread(operation, *args)))
