"""Shared facade for message-plane transport."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from typing import Any, Awaitable, Protocol, cast

try:
    import ormsgpack
except Exception:  # pragma: no cover
    ormsgpack = None  # type: ignore

try:
    import zmq
except Exception:  # pragma: no cover
    zmq = None  # type: ignore

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue, PluginContextProtocol
from plugin.sdk_v2.shared.models import Err, Ok, Result
from plugin.sdk_v2.shared.models.exceptions import CapabilityUnavailableError, InvalidArgumentError, SdkError, TransportError

MessagePlaneFacadeError = InvalidArgumentError | TransportError


class MessageHandler(Protocol):
    def __call__(self, payload: JsonObject) -> Awaitable[Result[None, SdkError]]: ...


class _SockLike(Protocol):
    def send(self, data: bytes, **kwargs: Any) -> Any: ...
    def recv(self, **kwargs: Any) -> Any: ...
    def poll(self, timeout: int, flags: int) -> Any: ...


class MessagePlaneTransport:
    """Async-first message-plane facade."""

    def __init__(self, *, plugin_ctx: PluginContextProtocol | None = None):
        self.plugin_ctx = plugin_ctx
        self._handlers: dict[str, list[MessageHandler]] = defaultdict(list)

    @staticmethod
    def _validate_topic(topic: str) -> Result[None, InvalidArgumentError]:
        if not isinstance(topic, str) or topic.strip() == "":
            return Err(InvalidArgumentError("topic must be non-empty"))
        return _OK_NONE

    @staticmethod
    def _validate_timeout(timeout: float) -> Result[None, InvalidArgumentError]:
        if timeout <= 0:
            return Err(InvalidArgumentError("timeout must be > 0"))
        return _OK_NONE

    @staticmethod
    def _normalize_error(error: Exception, *, op_name: str, topic: str | None = None, timeout: float | None = None) -> SdkError:
        return error if isinstance(error, SdkError) else TransportError(str(error), op_name=op_name, topic=topic, timeout=timeout)

    async def _publish(self, topic: str, payload: JsonObject, *, timeout: float, op_name: str) -> Result[None, MessagePlaneFacadeError]:
        try:
            if self.plugin_ctx is not None:
                push = getattr(self.plugin_ctx, "push_message", None)
                if callable(push):
                    await push(text=topic, description=topic, metadata={"payload": payload, "topic": topic}, timeout=timeout)
            for handler in list(self._handlers.get(topic, [])):
                result = await handler(payload)
                if isinstance(result, Err):
                    error = result.error if isinstance(result.error, Exception) else TransportError(str(result.error), op_name=op_name, topic=topic, timeout=timeout)
                    return Err(self._normalize_error(error, op_name=op_name, topic=topic, timeout=timeout))
            return Ok(None)
        except Exception as error:
            return Err(self._normalize_error(error, op_name=op_name, topic=topic, timeout=timeout))

    async def request(self, topic: str, payload: JsonObject, *, timeout: float = 10.0) -> Result[JsonObject | JsonValue | None, MessagePlaneFacadeError]:
        topic_ok = self._validate_topic(topic)
        if isinstance(topic_ok, Err):
            return cast(Result[JsonObject | JsonValue | None, MessagePlaneFacadeError], topic_ok)
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return cast(Result[JsonObject | JsonValue | None, MessagePlaneFacadeError], timeout_ok)
        try:
            requester = getattr(self.plugin_ctx, "message_plane_request", None) if self.plugin_ctx is not None else None
            if callable(requester):
                result = await requester(topic=topic, payload=payload, timeout=timeout)
                if isinstance(result, (dict, list, str, int, float, bool)) or result is None:
                    return Ok(result)
                return Ok({"result": result})
            return Err(
                CapabilityUnavailableError(
                    "message-plane request is not available on plugin_ctx",
                    op_name="message_plane.request",
                    capability="plugin_ctx.message_plane_request",
                    topic=topic,
                    timeout=timeout,
                )
            )
        except Exception as error:
            return cast(Result[JsonObject | JsonValue | None, MessagePlaneFacadeError], Err(error if isinstance(error, SdkError) else TransportError(str(error), op_name="message_plane.request", topic=topic, timeout=timeout)))

    async def notify(self, topic: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[None, MessagePlaneFacadeError]:
        topic_ok = self._validate_topic(topic)
        if isinstance(topic_ok, Err):
            return cast(Result[None, MessagePlaneFacadeError], topic_ok)
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return cast(Result[None, MessagePlaneFacadeError], timeout_ok)
        try:
            return cast(Result[None, MessagePlaneFacadeError], await self._publish(topic, payload, timeout=timeout, op_name="message_plane.notify"))
        except Exception as error:
            return cast(Result[None, MessagePlaneFacadeError], Err(error if isinstance(error, SdkError) else TransportError(str(error), op_name="message_plane.notify", topic=topic, timeout=timeout)))

    async def publish(self, topic: str, payload: JsonObject, *, timeout: float = 5.0) -> Result[None, MessagePlaneFacadeError]:
        topic_ok = self._validate_topic(topic)
        if isinstance(topic_ok, Err):
            return cast(Result[None, MessagePlaneFacadeError], topic_ok)
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return cast(Result[None, MessagePlaneFacadeError], timeout_ok)
        try:
            return cast(Result[None, MessagePlaneFacadeError], await self._publish(topic, payload, timeout=timeout, op_name="message_plane.publish"))
        except Exception as error:
            return cast(Result[None, MessagePlaneFacadeError], Err(error if isinstance(error, SdkError) else TransportError(str(error), op_name="message_plane.publish", topic=topic, timeout=timeout)))

    async def subscribe(self, topic: str, handler: MessageHandler) -> Result[None, MessagePlaneFacadeError]:
        topic_ok = self._validate_topic(topic)
        if isinstance(topic_ok, Err):
            return cast(Result[None, MessagePlaneFacadeError], topic_ok)
        if not callable(handler):
            return cast(Result[None, MessagePlaneFacadeError], Err(InvalidArgumentError("handler must be callable")))
        try:
            self._handlers[topic].append(handler)
            return Ok(None)
        except Exception as error:
            return cast(Result[None, MessagePlaneFacadeError], Err(error if isinstance(error, SdkError) else TransportError(str(error), op_name="message_plane.subscribe", topic=topic)))

    async def unsubscribe(self, topic: str, handler: MessageHandler | None = None) -> Result[int, MessagePlaneFacadeError]:
        topic_ok = self._validate_topic(topic)
        if isinstance(topic_ok, Err):
            return cast(Result[int, MessagePlaneFacadeError], topic_ok)
        if handler is not None and not callable(handler):
            return cast(Result[int, MessagePlaneFacadeError], Err(InvalidArgumentError("handler must be callable")))
        try:
            handlers = self._handlers.get(topic, [])
            if handler is None:
                count = len(handlers)
                self._handlers.pop(topic, None)
                return Ok(count)
            removed = 0
            remaining: list[MessageHandler] = []
            for item in handlers:
                if item is handler:
                    removed += 1
                else:
                    remaining.append(item)
            if remaining:
                self._handlers[topic] = remaining
            else:
                self._handlers.pop(topic, None)
            return Ok(removed)
        except Exception as error:
            return cast(Result[int, MessagePlaneFacadeError], Err(error if isinstance(error, SdkError) else TransportError(str(error), op_name="message_plane.unsubscribe", topic=topic)))


class MessagePlaneRpcClient:
    """Low-level async-first RPC helper client for message-plane integrations."""

    def __init__(self, *, plugin_id: str, endpoint: str) -> None:
        self._plugin_id = str(plugin_id)
        self._endpoint = str(endpoint)
        try:
            import threading

            self._tls = threading.local()
            self._lock = threading.Lock()
        except Exception:
            self._tls = None
            self._lock = None
        self._async_sock_cache: Any | None = None
        self._async_ctx_cache: Any | None = None

    def _get_sock(self) -> Any | None:
        if zmq is None:
            return None
        if self._tls is not None:
            sock = getattr(self._tls, "sock", None)
            if sock is not None:
                return sock
        if self._lock is not None:
            with self._lock:
                if self._tls is not None:
                    cached = getattr(self._tls, "sock", None)
                    if cached is not None:
                        return cached
                ctx = zmq.Context() if self._tls is not None else zmq.Context.instance()
                if self._tls is not None:
                    try:
                        self._tls.ctx = ctx
                    except Exception:
                        pass
                sock = ctx.socket(zmq.DEALER)
                self._configure_sock(sock)
                if self._tls is not None:
                    try:
                        self._tls.sock = sock
                    except Exception:
                        pass
                return sock
        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.DEALER)
        self._configure_sock(sock)
        return sock

    def _configure_sock(self, sock: Any) -> None:
        if zmq is None:
            return
        ident = f"mp:{self._plugin_id}:{int(time.time() * 1000)}".encode("utf-8")
        for opt, val in (
            (getattr(zmq, "IDENTITY", None), ident),
            (getattr(zmq, "LINGER", None), 0),
            (getattr(zmq, "TCP_NODELAY", 1), 1),
            (getattr(zmq, "RCVBUF", None), 2 * 1024 * 1024),
            (getattr(zmq, "SNDBUF", None), 2 * 1024 * 1024),
            (getattr(zmq, "RCVHWM", None), 10000),
            (getattr(zmq, "SNDHWM", None), 10000),
        ):
            if opt is None:
                continue
            try:
                sock.setsockopt(opt, val)
            except Exception:
                pass
        try:
            sock.connect(self._endpoint)
        except Exception:
            pass

    def _next_req_id(self) -> str:
        if self._tls is not None:
            try:
                n = int(getattr(self._tls, "req_seq", 0) or 0) + 1
                self._tls.req_seq = n
                return f"{self._plugin_id}:{n}"
            except Exception:
                pass
        return str(uuid.uuid4())

    async def _get_async_sock(self) -> Any | None:
        if self._async_sock_cache is not None:
            return self._async_sock_cache
        if zmq is None:
            return None
        try:
            import zmq.asyncio as zmq_asyncio  # type: ignore
        except Exception:
            return None
        if self._async_ctx_cache is None:
            self._async_ctx_cache = zmq_asyncio.Context()
        sock = self._async_ctx_cache.socket(zmq.DEALER)
        self._configure_sock(sock)
        self._async_sock_cache = sock
        return sock

    async def request(self, *, op: str, args: JsonObject, timeout: float) -> JsonObject | None:
        if ormsgpack is None or zmq is None:
            return None
        sock = await self._get_async_sock()
        if sock is None:
            return None
        req_id = self._next_req_id()
        req = {"v": 1, "op": op, "req_id": req_id, "args": args, "from_plugin": self._plugin_id}
        try:
            raw = ormsgpack.packb(req)
            await sock.send(raw, flags=0, copy=False, track=False)
        except Exception:
            return None
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return None
            try:
                events = await asyncio.wait_for(sock.poll(timeout=int(remaining * 1000), flags=zmq.POLLIN), timeout=remaining)
                if events == 0:
                    continue
                resp_frame = await sock.recv(flags=0, copy=False)
                resp = ormsgpack.unpackb(bytes(resp_frame))
            except asyncio.TimeoutError:
                return None
            except Exception:
                return None
            if isinstance(resp, dict) and resp.get("req_id") == req_id and resp.get("v") == 1 and isinstance(resp.get("success"), bool):
                return resp

    async def batch_request(self, requests: list[JsonObject], *, timeout: float = 5.0) -> list[JsonObject | None]:
        if not requests:
            return []
        results: list[JsonObject | None] = []
        for item in requests:
            op = str(item.get("op", ""))
            args = item.get("args", {})
            args_obj = args if isinstance(args, dict) else {}
            results.append(await self.request(op=op, args=args_obj, timeout=timeout))
        return results


def format_rpc_error(err: Any) -> str:
    if err is None:
        return "message_plane error"
    if isinstance(err, str):
        return err
    if isinstance(err, dict):
        code = err.get("code")
        msg = err.get("message")
        if isinstance(code, str) and isinstance(msg, str):
            return f"{code}: {msg}" if code else msg
        if isinstance(msg, str):
            return msg
    try:
        return str(err)
    except Exception:
        return "message_plane error"


_OK_NONE = Ok(None)

__all__ = ["MessagePlaneTransport", "MessageHandler", "MessagePlaneRpcClient", "format_rpc_error"]
