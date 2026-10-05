"""Bounded package downloads with cancellation-safe file ownership."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
import os
from pathlib import Path
import tempfile
from typing import BinaryIO

from plugin.logging_config import get_logger
from plugin.utils.asyncio_utils import await_cancellation_safe
from plugin.utils.http_imports import ensure_httpx

logger = get_logger("server.infrastructure.package_download")


class PackageSizeExceeded(ValueError):
    def __init__(self, actual: int, maximum: int) -> None:
        self.actual = actual
        self.maximum = maximum
        super().__init__(f"Package size {actual} exceeds {maximum} bytes")


def _create_download_file(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix="neko-market-", suffix=".neko-plugin", dir=directory
    )
    os.close(fd)
    return Path(name)


def cleanup_download_file(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("failed to remove downloaded package {}: {}", path, exc)


@asynccontextmanager
async def _open_download_file(path: Path) -> AsyncIterator[BinaryIO]:
    opening = asyncio.create_task(asyncio.to_thread(path.open, "wb"))
    try:
        handle = await await_cancellation_safe(opening)
        yield handle
    finally:
        if opening.done() and not opening.cancelled() and opening.exception() is None:
            await await_cancellation_safe(
                asyncio.create_task(asyncio.to_thread(opening.result().close))
            )


async def download_package_file(
    url: str,
    directory: Path,
    *,
    maximum_bytes: int,
    phase_timeout: float,
    total_timeout: float,
    flush_bytes: int,
    report_progress: Callable[[int, int | None], None],
    check_cancelled: Callable[[], None],
) -> Path:
    check_cancelled()
    httpx = await ensure_httpx()
    check_cancelled()
    creation = asyncio.create_task(asyncio.to_thread(_create_download_file, directory))
    completed = False
    try:
        path = await await_cancellation_safe(creation)
        async with asyncio.timeout(total_timeout):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(phase_timeout),
                follow_redirects=True,
                max_redirects=5,
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    length = response.headers.get("content-length")
                    total = int(length) if length else None
                    if total is not None and total > maximum_bytes:
                        raise PackageSizeExceeded(total, maximum_bytes)
                    received = 0
                    report_progress(received, total)
                    async with _open_download_file(path) as handle:
                        pending: list[bytes] = []
                        pending_bytes = 0

                        async def flush() -> None:
                            nonlocal pending_bytes
                            if pending:
                                await await_cancellation_safe(
                                    asyncio.create_task(
                                        asyncio.to_thread(handle.writelines, pending)
                                    )
                                )
                                pending.clear()
                                pending_bytes = 0

                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            check_cancelled()
                            pending.append(chunk)
                            pending_bytes += len(chunk)
                            received += len(chunk)
                            if received > maximum_bytes:
                                raise PackageSizeExceeded(received, maximum_bytes)
                            if pending_bytes >= flush_bytes:
                                await flush()
                            report_progress(received, total)
                        await flush()
        completed = True
        return path
    finally:
        if (
            not completed
            and creation.done()
            and not creation.cancelled()
            and creation.exception() is None
        ):
            await await_cancellation_safe(
                asyncio.create_task(
                    asyncio.to_thread(cleanup_download_file, creation.result())
                )
            )
