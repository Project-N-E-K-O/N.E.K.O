"""Control-request lifetime, independent of ZMQ, SDK facades and server services.

Each peer owns its reply correlation. A dispatcher owns the tasks serving incoming
requests. Transport adapters only send dictionaries and feed replies to receive().
The deadline uses the host's monotonic clock: these endpoints are local processes.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import math
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import Future as ThreadFuture
from concurrent.futures import InvalidStateError
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, overload

SendRequest = Callable[[dict[str, Any], float | None], Awaitable[None]]
CancelRequest = Callable[[dict[str, Any]], Awaitable[None]]
Handler = Callable[["RpcContext", dict[str, Any]], Awaitable[object]]
SendReply = Callable[[dict[str, Any]], Awaitable[None]]

_OWNS_DEADLINE = "__rpc_owns_deadline__"


def owns_deadline(handler: Handler) -> Handler:
    """Mark a handler that answers by ``ctx.deadline`` itself.

    The dispatcher then does not cut it off: an operation that must reply with a
    degraded result (e.g. "applied in memory, not persisted") cannot share the
    deadline with a timer that would cancel it first.
    """
    setattr(handler, _OWNS_DEADLINE, True)
    return handler


def rpc_reply(
    request: Mapping[str, Any], *, result: object = None, error: object = None
) -> dict[str, Any]:
    return {
        "type": "PLUGIN_TO_PLUGIN_RESPONSE",
        "to_plugin": request.get("from_plugin", ""),
        "request_id": request.get("request_id", ""),
        "result": result,
        "error": error,
    }


class RpcClosedError(RuntimeError):
    """The connection closed while a request was outstanding."""

    code = "CONNECTION_CLOSED"


def encode_rpc_reply(
    reply: dict[str, Any],
    encoder: Callable[[dict[str, Any]], Any],
) -> tuple[dict[str, Any], Any]:
    """Encode at the transport seam, replacing unsupported results with a fault."""
    try:
        return reply, encoder(reply)
    except Exception:
        fallback = dict(reply)
        fallback["result"] = None
        fallback["error"] = {
            "code": "SERIALIZATION_FAILED",
            "message": "RPC result is not serializable",
        }
        return fallback, encoder(fallback)


class RpcDeadlineExceededError(TimeoutError):
    """Our execution budget expired, as distinct from an operation's own timeout."""

    code = "DEADLINE_EXCEEDED"


class RpcTransportError(RuntimeError):
    code = "TRANSPORT_ERROR"

    def __init__(self, operation: str, error: Exception):
        self.details = {
            "operation": operation,
            "error_type": type(error).__name__,
            "message": str(error),
        }
        super().__init__(f"{operation} transport send failed ({type(error).__name__})")


def log_rpc_failure(logger: Any, message: str, exc: BaseException) -> None:
    # Exception strings (including validation errors) can contain private input.
    locations = []
    cursor = exc.__traceback__
    while cursor is not None:
        code = cursor.tb_frame.f_code
        locations.append(f"{code.co_filename}:{cursor.tb_lineno} in {code.co_name}")
        cursor = cursor.tb_next
    stack = " -> ".join(locations)
    logger.error(f"{message}, error_type={type(exc).__name__}, stack={stack}")


class RpcRemoteError(RuntimeError):
    def __init__(self, error: object):
        if isinstance(error, Mapping):
            self.code = str(error.get("code") or "REQUEST_FAILED")
            self.message = str(error.get("message") or self.code)
            self.details = error.get("details")
        else:
            self.code, self.message, self.details = "REQUEST_FAILED", str(error), None
        super().__init__(self.message)


@dataclass(frozen=True)
class _CallScope:
    request_id: str
    trace_id: str
    deadline: float | None
    depth: int = 0


@dataclass(frozen=True)
class RpcContext:
    """What the dispatcher has validated about one incoming request.

    ``from_plugin`` is the identity the transport bound to the connection; the
    deadline is the only execution budget a handler may plan against.
    """

    from_plugin: str
    request_id: str
    deadline: float
    trace_id: str
    depth: int

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())


DEFAULT_HANDLER_TIMEOUT_SECONDS = 10.0
MAX_HANDLER_TIMEOUT_SECONDS = 60.0


def _resolve_handler_timeout(value: object) -> float:
    """Anything but a positive finite number means "use the default"."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_HANDLER_TIMEOUT_SECONDS
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_HANDLER_TIMEOUT_SECONDS
    return min(float(value), MAX_HANDLER_TIMEOUT_SECONDS)


_scope: contextvars.ContextVar[_CallScope | None] = contextvars.ContextVar(
    "plugin_rpc_scope", default=None
)


def remaining_budget(timeout: float | None, deadline: float | None) -> float | None:
    if deadline is None:
        return timeout
    remaining = max(0.0, deadline - time.monotonic())
    return remaining if timeout is None else min(timeout, remaining)


@overload
def request_deadline(request: Mapping[str, Any], timeout: float) -> float: ...


@overload
def request_deadline(
    request: Mapping[str, Any], timeout: float | None
) -> float | None: ...


def request_deadline(request: Mapping[str, Any], timeout: float | None) -> float | None:
    deadline = None if timeout is None else time.monotonic() + timeout
    supplied = request.get("_request_deadline_monotonic")
    if (
        isinstance(supplied, (float, int))
        and not isinstance(supplied, bool)
        and math.isfinite(supplied)
    ):
        deadline = float(supplied) if deadline is None else min(deadline, supplied)
    return deadline


@contextmanager
def request_scope(request: Mapping[str, Any]):
    """Carry one local control call's deadline and trace into nested requests."""
    request_id = str(request.get("request_id") or request.get("req_id") or uuid.uuid4())
    depth = request.get("_rpc_depth", 0)
    if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0 or depth > 32:
        raise RpcRemoteError(
            {"code": "CALL_DEPTH_EXCEEDED", "message": "Invalid plugin call depth"}
        )
    current = _CallScope(
        request_id,
        str(request.get("_rpc_trace_id") or request_id),
        request_deadline(request, None),
        depth,
    )
    token = _scope.set(current)
    try:
        yield current
    finally:
        _scope.reset(token)


class RpcPeer:
    """One request table shared safely by callers on different threads/loops."""

    def __init__(
        self,
        send: SendRequest,
        *,
        cancel: CancelRequest | None = None,
        logger: Any = None,
    ):
        self._send = send
        self._cancel = cancel
        self._logger = logger or logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Future]] = {}
        self._cancellations: set[asyncio.Task] = set()
        self._exchanges: set[asyncio.Task] = set()
        self._closed = False

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    async def request(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout: float | None,
        request_id: str | None = None,
    ) -> object:
        if timeout is not None and (
            isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number or None")
        request_id = request_id or str(uuid.uuid4())
        parent = _scope.get()
        deadline = request_deadline(payload, timeout)
        if parent and parent.deadline is not None:
            deadline = (
                parent.deadline if deadline is None else min(deadline, parent.deadline)
            )
        request = dict(payload)
        request.update(
            type=operation,
            request_id=request_id,
            timeout=timeout,
            _rpc_trace_id=parent.trace_id if parent else request_id,
            _rpc_parent_request_id=parent.request_id if parent else None,
            _rpc_depth=parent.depth + 1 if parent else 0,
        )
        if deadline is not None:
            request["_request_deadline_monotonic"] = deadline
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        with self._lock:
            if self._closed:
                raise RpcClosedError("Plugin RPC connection is closed")
            if request_id in self._pending:
                raise ValueError(f"Duplicate RPC request ID: {request_id}")
            self._pending[request_id] = loop, future

        async def exchange():
            if future.done():
                return future.result()
            budget = remaining_budget(timeout, deadline)
            if budget is not None and budget <= 0:
                raise RpcDeadlineExceededError("Request deadline expired before send")
            request["timeout"] = budget
            send_task = asyncio.create_task(self._send(request, budget))
            try:
                await asyncio.wait(
                    {send_task, future}, return_when=asyncio.FIRST_COMPLETED
                )
                if future.done():
                    return future.result()
                try:
                    await send_task
                except (TimeoutError, RpcClosedError, RpcRemoteError):
                    raise
                except Exception as exc:
                    log_rpc_failure(
                        self._logger,
                        f"RPC send failed: operation={operation}, request_id={request_id}",
                        exc,
                    )
                    self._send_cancel(request)
                    raise RpcTransportError(operation, exc) from exc
                return await future
            finally:
                if not send_task.done():
                    send_task.cancel()
                await asyncio.gather(send_task, return_exceptions=True)

        started = time.monotonic()
        timer = asyncio.timeout(remaining_budget(timeout, deadline))
        self._logger.debug(
            f"RPC started: operation={operation}, request_id={request_id}, "
            f"trace_id={request['_rpc_trace_id']}, parent_request_id={request['_rpc_parent_request_id']}"
        )
        exchange_task = asyncio.create_task(exchange())
        with self._lock:
            self._exchanges.add(exchange_task)
        try:
            async with timer:
                return await exchange_task
        except TimeoutError as exc:
            if not timer.expired() and not isinstance(exc, RpcDeadlineExceededError):
                self._send_cancel(request)
                raise RpcRemoteError(
                    {
                        "code": "TRANSPORT_TIMEOUT",
                        "message": f"{operation} transport timed out",
                    }
                ) from None
            self._logger.error(
                f"RPC timed out: operation={operation}, request_id={request_id}, "
                f"trace_id={request['_rpc_trace_id']}, elapsed={time.monotonic() - started:.3f}s"
            )
            self._send_cancel(request)
            raise RpcDeadlineExceededError(
                f"{operation} timed out after {timeout}s"
            ) from None
        except asyncio.CancelledError:
            self._send_cancel(request)
            raise
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
                self._exchanges.discard(exchange_task)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()
            self._logger.debug(
                f"RPC finished: operation={operation}, request_id={request_id}, "
                f"trace_id={request['_rpc_trace_id']}, elapsed={time.monotonic() - started:.3f}s"
            )

    def _send_cancel(self, request: dict[str, Any]) -> None:
        if self._cancel is None:
            return
        with self._lock:
            if self._closed:
                return

        async def cancel():
            try:
                await asyncio.wait_for(self._cancel(request), timeout=0.25)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.debug(
                    f"RPC cancellation delivery failed: request_id={request['request_id']}, error_type={type(exc).__name__}"
                )

        task = asyncio.create_task(cancel())
        with self._lock:
            self._cancellations.add(task)
        task.add_done_callback(self._cancel_done)

    def _cancel_done(self, task: asyncio.Task) -> None:
        with self._lock:
            self._cancellations.discard(task)

    def receive(
        self, request_id: str, *, result: object = None, error: object = None
    ) -> bool:
        with self._lock:
            waiter = self._pending.get(request_id)
        if waiter is None:
            self._logger.debug(f"Late or unknown RPC reply: request_id={request_id}")
            return False
        loop, future = waiter

        def resolve():
            if not future.done():
                if error is not None:
                    future.set_exception(RpcRemoteError(error))
                else:
                    future.set_result(result)

        try:
            loop.call_soon_threadsafe(resolve)
        except RuntimeError:
            return False
        return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            pending, self._pending = tuple(self._pending.values()), {}
        for loop, future in pending:

            def fail(future=future):
                if not future.done():
                    future.set_exception(
                        RpcClosedError("Plugin RPC connection closed during request")
                    )

            try:
                loop.call_soon_threadsafe(fail)
            except RuntimeError:
                pass

    async def aclose(self) -> None:
        self.close()
        with self._lock:
            tasks = tuple(self._cancellations | self._exchanges)
        waits = []

        for task in tasks:
            owner = task.get_loop()
            if owner is asyncio.get_running_loop():
                waits.append(task)
            elif not task.done():
                completion: ThreadFuture = ThreadFuture()

                def observe(task=task, completion=completion):
                    def finished(_task):
                        try:
                            completion.set_result(None)
                        except InvalidStateError:
                            pass

                    if task.done():
                        finished(task)
                    else:
                        task.add_done_callback(finished)

                try:
                    owner.call_soon_threadsafe(observe)
                except RuntimeError:
                    continue
                waits.append(
                    asyncio.wait_for(asyncio.wrap_future(completion), timeout=1.0)
                )
        if waits:
            results = await asyncio.gather(*waits, return_exceptions=True)
            if any(isinstance(result, TimeoutError) for result in results):
                self._logger.warning(
                    "RPC owner event loop stopped before request cleanup completed"
                )


class RpcDispatcher:
    """Own incoming tasks and turn each handler outcome into one wire reply.

    Admission is nonblocking: overload produces BUSY rather than parking child
    requests behind parents that hold all available slots.
    """

    def __init__(
        self,
        handlers: Mapping[str, Handler],
        *,
        max_in_flight: int = 256,
        error_mapper: Callable[[Exception], object | None] | None = None,
        logger: Any = None,
    ):
        self._handlers = handlers
        self._limit = max_in_flight
        self._error_mapper = error_mapper
        self._logger = logger or logging.getLogger(__name__)
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._active = 0
        self._closed = False

    def submit(self, request: dict[str, Any], send_reply: SendReply) -> bool:
        owner = str(request.get("_rpc_connection_id") or request.get("from_plugin", ""))
        key = owner, str(request.get("request_id", ""))
        if request.get("type") == "RPC_DISCONNECT":
            for (connection_id, _), task in tuple(self._tasks.items()):
                if connection_id == owner:
                    task.cancel()
            return True
        if request.get("type") == "RPC_CANCEL":
            task = self._tasks.get(key)
            if task is not None:
                task.cancel()
            return True
        if key in self._tasks:
            self._logger.warning(
                f"Duplicate in-flight RPC request ignored: request_id={key[1]}"
            )
            return True
        if self._closed or len(self._tasks) >= self._limit:
            return False

        async def serve():
            reply = await self.dispatch(request)
            try:
                await asyncio.wait_for(send_reply(reply), timeout=1.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_rpc_failure(
                    self._logger,
                    f"RPC reply delivery failed: request_id={key[1]}, plugin_id={request.get('from_plugin')}, connection_id={key[0]}",
                    exc,
                )

        task = asyncio.create_task(serve(), name=f"plugin-rpc:{key[1]}")
        self._tasks[key] = task
        task.add_done_callback(lambda done: self._finished(key, done))
        return True

    def _finished(self, key: tuple[str, str], task: asyncio.Task) -> None:
        self._tasks.pop(key, None)
        if not task.cancelled() and task.exception() is not None:
            log_rpc_failure(
                self._logger,
                f"RPC request task failed: request_id={key[1]}",
                task.exception(),
            )

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        reply = rpc_reply(request)
        if self._closed or self._active >= self._limit:
            reply["error"] = {
                "code": "BUSY",
                "message": "Plugin RPC dispatcher is unavailable",
            }
            return reply
        from_plugin_raw = reply["to_plugin"]
        request_id_raw = reply["request_id"]
        if (
            not isinstance(from_plugin_raw, str)
            or not from_plugin_raw.strip()
            or not isinstance(request_id_raw, str)
            or not request_id_raw.strip()
        ):
            reply["error"] = {
                "code": "INVALID_REQUEST",
                "message": "from_plugin and request_id are required",
            }
            return reply
        from_plugin = from_plugin_raw.strip()
        request_id = request_id_raw.strip()
        operation = str(request.get("type", ""))
        handler = self._handlers.get(operation)
        if handler is None:
            reply["error"] = {
                "code": "UNKNOWN_OPERATION",
                "message": f"Unknown request type: {operation}",
            }
            return reply
        timeout = _resolve_handler_timeout(request.get("timeout"))
        deadline = request_deadline(request, timeout)
        depth = request.get("_rpc_depth", 0)
        if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0:
            reply["error"] = {
                "code": "INVALID_REQUEST",
                "message": "Call depth must be a non-negative integer",
            }
            return reply
        if depth > 32:
            reply["error"] = {
                "code": "CALL_DEPTH_EXCEEDED",
                "message": "Plugin call depth exceeded",
            }
            return reply
        trace_id = str(request.get("_rpc_trace_id") or request_id)
        ctx = RpcContext(
            from_plugin=from_plugin,
            request_id=request_id,
            deadline=deadline,
            trace_id=trace_id,
            depth=depth,
        )
        scope = _CallScope(request_id, trace_id, deadline, depth)
        token = _scope.set(scope)
        self._active += 1
        timer = None
        try:
            budget = ctx.remaining()
            if budget <= 0:
                raise RpcDeadlineExceededError("Request expired in queue")
            if getattr(handler, _OWNS_DEADLINE, False):
                reply["result"] = await handler(ctx, request)
            else:
                timer = asyncio.timeout(budget)
                async with timer:
                    reply["result"] = await handler(ctx, request)
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            expired = (
                (timer is not None and timer.expired())
                or isinstance(exc, RpcDeadlineExceededError)
            )
            reply["error"] = {
                "code": "DEADLINE_EXCEEDED" if expired else "OPERATION_TIMEOUT",
                "message": f"{operation} request deadline exceeded"
                if expired
                else str(exc) or "Host operation timed out",
            }
            self._logger.warning(
                f"RPC timeout: operation={operation}, request_id={scope.request_id}, trace_id={scope.trace_id}, code={reply['error']['code']}"
            )
        except Exception as exc:
            mapped = self._error_mapper(exc) if self._error_mapper else None
            if mapped is None and isinstance(exc, RpcRemoteError):
                mapped = {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            if mapped is None:
                mapped = {
                    "code": "INTERNAL_ERROR",
                    "message": "Internal plugin request error",
                }
                log_rpc_failure(
                    self._logger,
                    f"RPC handler failed: operation={operation}, request_id={scope.request_id}, trace_id={scope.trace_id}",
                    exc,
                )
            else:
                error_code = (
                    mapped.get("code", "REQUEST_FAILED")
                    if isinstance(mapped, Mapping)
                    else "REQUEST_FAILED"
                )
                self._logger.warning(
                    f"RPC request failed: operation={operation}, request_id={scope.request_id}, code={error_code}, error_type={type(exc).__name__}"
                )
            reply["error"] = mapped
        finally:
            self._active -= 1
            _scope.reset(token)
        return reply

    async def close(self) -> None:
        self._closed = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
