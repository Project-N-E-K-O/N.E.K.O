"""Shared first-success racing and cleanup for upstream candidate URLs."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


async def race_candidate_requests(
    urls: list[str],
    request: Callable[[str], Awaitable[dict[str, Any]]],
    *,
    timeout: float | None = None,
    prefer_configured_order: bool = False,
    timeout_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return first success; preserve each caller's failure-selection policy.

    Configured-order mode chooses the first configured completed failure (and
    breaks simultaneous success ties by configured order). Otherwise failures
    are selected in completion order. Every exit cancels and drains losers.
    """
    failures: dict[int, dict[str, Any]] = {}
    completion_order: list[int] = []

    async def run_one(index: int, url: str) -> dict[str, Any]:
        try:
            result = await request(url)
        except Exception as exc:
            result = {"success": False, "error": str(exc), "error_code": "unknown"}
        completion_order.append(index)
        if result.get("success"):
            return {**result, "resolved_url": url}
        return result

    tasks = [asyncio.create_task(run_one(index, url)) for index, url in enumerate(urls)]
    pending = set(tasks)
    deadline = asyncio.get_running_loop().time() + timeout if timeout is not None else None
    result = {"success": False, "error_code": "unknown"}
    try:
        while pending:
            remaining = max(0, deadline - asyncio.get_running_loop().time()) if deadline is not None else None
            done, pending = await asyncio.wait(
                pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                result = dict(timeout_result or {"success": False, "error_code": "timeout"})
                break
            order = range(len(tasks)) if prefer_configured_order else completion_order
            for index in order:
                if tasks[index] not in done:
                    continue
                result = tasks[index].result()
                if result.get("success"):
                    return result
                failures[index] = result
        if failures:
            index = min(failures) if prefer_configured_order else next(
                index for index in completion_order if index in failures
            )
            result = failures[index]
        return result
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
