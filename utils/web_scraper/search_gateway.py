"""Controlled main-process access to the web-search plugin.

All proactive search traffic goes through the plugin run protocol. This module
coordinates proactive backend transitions and adds a longer-lived cache and
failure cooldown, while the plugin remains the final upstream resilience layer.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import suppress
import os
import time
from typing import Any, Dict, Optional
import uuid

import httpx

from config import USER_PLUGIN_SERVER_PORT

from ._shared import logger


_CACHE_TTL_SECONDS = 600.0
_CACHE_STALE_SECONDS = 1800.0
_FAILURE_COOLDOWN_SECONDS = 300.0
_MIN_RUN_INTERVAL_SECONDS = 5.0
_MAX_CACHE_ENTRIES = 64
_RUN_TIMEOUT_SECONDS = 30.0
_POLL_INTERVAL_SECONDS = 0.25
_MAX_POLL_INTERVAL_SECONDS = 2.0
_FLOW_CONTROL_ERROR_CODES = frozenset(
    {
        "web_search_backend_blocked",
        "web_search_backend_busy",
        "web_search_backend_cooldown",
    }
)

_cache: OrderedDict[
    tuple[str, str, int],
    tuple[float, float, Dict[str, Any]],
] = OrderedDict()
_next_run_at: Dict[str, float] = {}
_failure_cooldown_until: Dict[str, float] = {}
_inflight: Dict[tuple[str, str, int], asyncio.Task[Dict[str, Any]]] = {}
_waiters: Dict[tuple[str, str, int], int] = {}
_backend_locks: Dict[str, asyncio.Lock] = {}
_cleanup_tasks: set[asyncio.Task[None]] = set()
_runtime_loop: Optional[asyncio.AbstractEventLoop] = None


class _PluginThrottleError(RuntimeError):
    """The plugin is healthy but intentionally refused work for now."""


def _copy_result(result: Dict[str, Any]) -> Dict[str, Any]:
    copied = dict(result)
    copied["results"] = [
        dict(item) for item in result.get("results", []) if isinstance(item, dict)
    ]
    return copied


def _prepare_runtime_loop() -> asyncio.AbstractEventLoop:
    global _runtime_loop
    loop = asyncio.get_running_loop()
    if _runtime_loop is not loop:
        # Tasks cannot cross event loops. The durable cache and cooldown clocks
        # remain valid across host lifecycle restarts, but in-flight ownership
        # belongs to one loop only.
        _runtime_loop = loop
        _inflight.clear()
        _waiters.clear()
        _backend_locks.clear()
        _cleanup_tasks.clear()
    return loop


def _cached(
    key: tuple[str, str, int],
    *,
    fresh: bool = True,
) -> Optional[Dict[str, Any]]:
    cached = _cache.get(key)
    if cached is None:
        return None
    fresh_until, stale_until, result = cached
    now = time.monotonic()
    if now >= stale_until:
        _cache.pop(key, None)
        return None
    if fresh and now >= fresh_until:
        return None
    if not fresh and (not result.get("success") or not result.get("results")):
        return None
    _cache.move_to_end(key)
    return _copy_result(result)


def _store(key: tuple[str, str, int], result: Dict[str, Any]) -> None:
    now = time.monotonic()
    _cache[key] = (
        now + _CACHE_TTL_SECONDS,
        now + _CACHE_TTL_SECONDS + _CACHE_STALE_SECONDS,
        _copy_result(result),
    )
    _cache.move_to_end(key)
    while len(_cache) > _MAX_CACHE_ENTRIES:
        _cache.popitem(last=False)


def _error_message(value: object, default: str) -> str:
    if isinstance(value, dict):
        nested = value.get("error")
        source = nested if isinstance(nested, dict) else value
        return str(source.get("message") or source.get("code") or default)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _plugin_error(value: object, default: str) -> RuntimeError:
    message = _error_message(value, default)
    code = ""
    if isinstance(value, dict):
        nested = value.get("error")
        source = nested if isinstance(nested, dict) else value
        code = str(source.get("code") or "").strip().casefold()
    if code in _FLOW_CONTROL_ERROR_CODES:
        return _PluginThrottleError(message)
    return RuntimeError(message)


def _extract_export(payload: object) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("搜索插件返回了无效导出数据")
    for item in payload.get("items") or []:
        if not isinstance(item, dict) or item.get("type") != "json":
            continue
        raw = item.get("json")
        if raw is None:
            raw = item.get("json_data")
        if not isinstance(raw, dict):
            continue
        current = raw
        # Run exports contain the server envelope and the child-host response
        # envelope before the plugin's own result payload.
        for _ in range(4):
            if current.get("success") is False or current.get("error"):
                raise _plugin_error(
                    current.get("error"), "搜索插件执行失败"
                )
            nested = current.get("data")
            if not isinstance(nested, dict):
                break
            current = nested
        if isinstance(current.get("results"), list):
            return current
    raise RuntimeError("搜索插件没有返回结构化结果")


def _plugin_server_base() -> str:
    raw_port = os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "").strip()
    try:
        port = int(raw_port) if raw_port else int(USER_PLUGIN_SERVER_PORT)
    except (TypeError, ValueError):
        port = int(USER_PLUGIN_SERVER_PORT)
    if not 1 <= port <= 65535:
        port = int(USER_PLUGIN_SERVER_PORT)
    return f"http://127.0.0.1:{port}"


async def _cancel_run(base: str, run_id: str) -> None:
    try:
        timeout = httpx.Timeout(0.5, connect=0.2)
        async with httpx.AsyncClient(
            timeout=timeout,
            proxy=None,
            trust_env=False,
        ) as client:
            await client.post(
                f"{base}/runs/{run_id}/cancel",
                json={"reason": "search gateway stopped waiting"},
            )
    except Exception:
        logger.debug("取消遗留联网搜索任务失败 (run_id=%s)", run_id)


def _schedule_cancel(base: str, run_id: str) -> None:
    task = asyncio.create_task(_cancel_run(base, run_id))
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_tasks.discard)


async def _invoke_plugin(
    query: str,
    limit: int,
    backend: Optional[str],
    preferred_backend: Optional[str],
    run_timeout: float = _RUN_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    base = _plugin_server_base()
    loop = asyncio.get_running_loop()
    run_timeout = max(0.01, min(float(run_timeout), _RUN_TIMEOUT_SECONDS))
    deadline = loop.time() + run_timeout

    def remaining_timeout() -> float:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("等待联网搜索插件超时")
        return min(5.0, remaining)

    args: Dict[str, Any] = {
        "query": query,
        "max_results": limit,
        "_ctx": {"entry_timeout": run_timeout},
    }
    if backend in {"anysearch", "baidu", "duckduckgo"}:
        args["backend"] = backend
    elif preferred_backend in {"anysearch", "baidu", "duckduckgo"}:
        # Keep plugin auto semantics while making its primary coordinator match
        # the gateway's reserved slot.
        args["preferred_backend"] = preferred_backend
    body = {
        "task_id": f"proactive-search-{uuid.uuid4().hex}",
        "plugin_id": "web_search",
        "entry_id": "search",
        "args": args,
    }
    timeout = httpx.Timeout(5.0, connect=1.0)
    async with httpx.AsyncClient(timeout=timeout, proxy=None, trust_env=False) as client:
        request_timeout = remaining_timeout()
        async with asyncio.timeout(request_timeout):
            response = await client.post(
                f"{base}/runs",
                json=body,
                timeout=request_timeout,
            )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"联网搜索插件不可用（HTTP {response.status_code}）")
        accepted = response.json()
        run_id = accepted.get("run_id") if isinstance(accepted, dict) else None
        if not isinstance(run_id, str) or not run_id:
            raise RuntimeError("联网搜索插件未返回有效任务编号")

        terminal = {"succeeded", "failed", "canceled", "timeout"}
        poll_interval = _POLL_INTERVAL_SECONDS
        terminal_seen = False
        try:
            while True:
                request_timeout = remaining_timeout()
                async with asyncio.timeout(request_timeout):
                    response = await client.get(
                        f"{base}/runs/{run_id}",
                        timeout=request_timeout,
                    )
                if response.status_code != 200:
                    raise RuntimeError(
                        f"读取联网搜索任务失败（HTTP {response.status_code}）"
                    )
                candidate = response.json()
                run_data = candidate if isinstance(candidate, dict) else {}
                status = str(run_data.get("status") or "")
                if status in terminal:
                    terminal_seen = True
                    if status != "succeeded":
                        raise _plugin_error(
                            run_data.get("error"),
                            f"联网搜索任务状态：{status}",
                        )
                    break
                await asyncio.sleep(
                    min(poll_interval, max(0.0, deadline - loop.time()))
                )
                poll_interval = min(
                    poll_interval * 1.5,
                    _MAX_POLL_INTERVAL_SECONDS,
                )

            request_timeout = remaining_timeout()
            async with asyncio.timeout(request_timeout):
                response = await client.get(
                    f"{base}/runs/{run_id}/export",
                    params={"limit": 20},
                    timeout=request_timeout,
                )
            if response.status_code != 200:
                raise RuntimeError(
                    f"导出联网搜索结果失败（HTTP {response.status_code}）"
                )
            data = _extract_export(response.json())
        except BaseException:
            if not terminal_seen:
                _schedule_cancel(base, run_id)
            raise

    normalized_results = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            continue
        normalized_results.append(
            {
                "title": title,
                "abstract": str(item.get("snippet") or item.get("abstract") or "").strip(),
                "url": url,
            }
        )
    return {
        "success": bool(normalized_results),
        "query": query,
        "results": normalized_results,
        "error": "" if normalized_results else "未获得搜索结果",
    }


async def _invoke_in_backend_slot(
    query: str,
    limit: int,
    reservation_backend: str,
    deadline: float,
    *,
    entry_backend: Optional[str] = None,
    preferred_backend: Optional[str] = None,
) -> Dict[str, Any]:
    """Serialize one known backend or the neutral automatic-dispatch scope."""
    loop = asyncio.get_running_loop()
    lock = _backend_locks.setdefault(reservation_backend, asyncio.Lock())
    remaining = deadline - loop.time()
    if remaining <= 0:
        raise TimeoutError("等待联网搜索插件超时")
    try:
        async with asyncio.timeout(remaining):
            await lock.acquire()
    except TimeoutError as error:
        raise _PluginThrottleError("联网搜索后端繁忙，请稍后重试") from error

    try:
        now = time.monotonic()
        cooldown_until = _failure_cooldown_until.get(reservation_backend, 0.0)
        if now < cooldown_until:
            raise _PluginThrottleError("联网搜索暂处于失败冷却期")
        throttle_wait = max(0.0, _next_run_at.get(reservation_backend, 0.0) - now)
        if throttle_wait:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("等待联网搜索插件超时")
            async with asyncio.timeout(remaining):
                await asyncio.sleep(throttle_wait)

        _next_run_at[reservation_backend] = time.monotonic() + _MIN_RUN_INTERVAL_SECONDS
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("等待联网搜索插件超时")
        try:
            return await _invoke_plugin(
                query,
                limit,
                entry_backend,
                preferred_backend,
                run_timeout=remaining,
            )
        except _PluginThrottleError:
            raise
        except Exception:
            _failure_cooldown_until[reservation_backend] = (
                time.monotonic() + _FAILURE_COOLDOWN_SECONDS
            )
            raise
    finally:
        lock.release()


async def search_via_plugin(
    query: str,
    limit: int = 5,
    *,
    backend: Optional[str] = None,
    preferred_backend: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a cached, rate-limited search through the web_search plugin."""
    normalized_query = " ".join(str(query or "").split())
    if len(normalized_query) < 2:
        return {"success": False, "error": "搜索关键词太短", "results": []}
    try:
        requested_limit = max(1, min(int(limit), 10))
    except (TypeError, ValueError):
        requested_limit = 5
    plugin_limit = max(3, requested_limit)
    requested_backend = backend if backend in {"anysearch", "baidu", "duckduckgo"} else None
    hinted_backend = (
        preferred_backend
        if preferred_backend in {"anysearch", "baidu", "duckduckgo"}
        else None
    )
    # The plugin owns automatic AnySearch -> regional-engine fallback and may
    # be configured to use a fixed legacy engine.  The host therefore cannot
    # truthfully attribute an automatic call to AnySearch: keep that call in a
    # neutral scope and let the plugin coordinate the actual backend. Explicit
    # or hinted choices remain isolated by their known backend.
    reservation_backend = requested_backend or hinted_backend or "auto"
    cache_scope = reservation_backend
    key = (cache_scope, normalized_query.casefold(), requested_limit)
    cached = _cached(key)
    if cached is not None:
        return cached

    loop = _prepare_runtime_loop()
    task = _inflight.get(key)
    if task is None:
        stale = _cached(key, fresh=False)

        async def execute() -> Dict[str, Any]:
            deadline = loop.time() + _RUN_TIMEOUT_SECONDS
            try:
                result = await _invoke_in_backend_slot(
                    normalized_query,
                    plugin_limit,
                    reservation_backend,
                    deadline,
                    entry_backend=requested_backend,
                    preferred_backend=hinted_backend,
                )
            except _PluginThrottleError as error:
                # Busy/cooldown responses are normal flow control from a
                # healthy plugin, not grounds for a five-minute outage.
                return stale or {
                    "success": False,
                    "error": str(error),
                    "results": [],
                }
            except Exception as error:
                logger.warning(
                    "受控联网搜索失败 (query_len=%s, error_type=%s)",
                    len(normalized_query),
                    type(error).__name__,
                )
                return stale or {
                    "success": False,
                    "error": str(error),
                    "results": [],
                }

            result["results"] = [
                dict(item)
                for item in result.get("results", [])[:requested_limit]
                if isinstance(item, dict)
            ]
            result["success"] = bool(result["results"])
            if result["success"]:
                result["error"] = ""

            # A valid empty result is cacheable too; otherwise a stable obscure
            # window title would still trigger one upstream search every refresh.
            _store(key, result)
            return _copy_result(result)

        task = loop.create_task(execute())
        _inflight[key] = task

        def finish(done: asyncio.Task[Dict[str, Any]]) -> None:
            if _inflight.get(key) is done:
                _inflight.pop(key, None)

        task.add_done_callback(finish)

    _waiters[key] = _waiters.get(key, 0) + 1
    try:
        return _copy_result(await asyncio.shield(task))
    finally:
        remaining = _waiters.get(key, 1) - 1
        if remaining > 0:
            _waiters[key] = remaining
        else:
            _waiters.pop(key, None)
            if not task.done():
                if _inflight.get(key) is task:
                    _inflight.pop(key, None)
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
