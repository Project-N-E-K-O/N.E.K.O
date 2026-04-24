from __future__ import annotations

import asyncio
import concurrent.futures
import os
import socket
import threading
import time
from dataclasses import dataclass

from plugin.logging_config import logger


@dataclass(frozen=True)
class MessagePlaneEndpoints:
    rpc: str
    pub: str
    ingest: str


class MessagePlaneRunner:
    def start(self) -> MessagePlaneEndpoints:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def health_check(self, *, timeout_s: float = 1.0) -> bool:
        raise NotImplementedError


def _parse_wait_tcp_target(endpoint: str) -> tuple[str, int] | None:
    ep = str(endpoint)
    if not ep.startswith("tcp://"):
        return None
    rest = ep[len("tcp://") :]
    if ":" not in rest:
        return None
    host, port_s = rest.rsplit(":", 1)
    host = host.strip() or "127.0.0.1"
    try:
        port = int(port_s)
    except Exception:
        return None
    if port <= 0 or port > 65535:
        return None
    return host, port


def _wait_tcp_ready(endpoint: str, *, timeout_s: float = 2.0) -> bool:
    # Blocking TCP readiness probe. Safe to call from a dedicated thread (e.g. runner startup
    # thread), but NOT from an asyncio event-loop thread — use ``_wait_tcp_ready_async`` there.
    target = _parse_wait_tcp_target(endpoint)
    if target is None:
        # Non-tcp endpoint or malformed port: preserve original "assume ready" behavior.
        return True
    host, port = target

    deadline = time.time() + max(0.0, float(timeout_s))
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except Exception:
            try:
                time.sleep(0.05)
            except Exception:
                pass
    return False


async def _wait_tcp_ready_async(endpoint: str, *, timeout_s: float = 2.0) -> bool:
    # Non-blocking equivalent of ``_wait_tcp_ready`` for use inside an asyncio event loop.
    # Same semantics: 0.2s per-attempt connect timeout, 0.05s retry interval, ``timeout_s``
    # overall deadline, returns True on first successful connect else False.
    target = _parse_wait_tcp_target(endpoint)
    if target is None:
        return True
    host, port = target

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(timeout_s))
    while loop.time() < deadline:
        remaining = deadline - loop.time()
        attempt_timeout = 0.2 if remaining > 0.2 else max(remaining, 0.0)
        if attempt_timeout <= 0.0:
            break
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=attempt_timeout
            )
            return True
        except (asyncio.TimeoutError, OSError, ConnectionError):
            # Expected during readiness probe: endpoint not yet listening — retry until deadline.
            pass
        except Exception:
            # Belt-and-suspenders: don't let an unexpected error kill the retry loop.
            pass
        finally:
            if writer is not None:
                try:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
                except Exception:
                    pass
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception:
            # asyncio.sleep should only raise CancelledError; swallow anything else to keep retrying.
            pass
    return False


def _rpc_health_check(endpoint: str, *, timeout_s: float = 1.0) -> bool:
    try:
        from plugin.core.message_plane_transport import MessagePlaneRpcClient
    except Exception:
        return False

    def _check_once() -> bool:
        rpc = MessagePlaneRpcClient(plugin_id="server", endpoint=str(endpoint))
        resp = rpc.request(op="health", args={}, timeout=float(timeout_s))
        # request() should be sync here; if a coroutine leaks out, treat as unhealthy.
        if asyncio.iscoroutine(resp):
            resp.close()
            return False
        if not isinstance(resp, dict):
            return False
        if not resp.get("ok"):
            return False
        return True

    try:
        asyncio.get_running_loop()
        in_event_loop = True
    except RuntimeError:
        in_event_loop = False

    try:
        if in_event_loop:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_check_once)
                return bool(fut.result(timeout=max(float(timeout_s), 0.1) + 0.5))
        return _check_once()
    except Exception:
        return False


class PythonMessagePlaneRunner(MessagePlaneRunner):
    def __init__(self, *, endpoints: MessagePlaneEndpoints) -> None:
        self._endpoints = endpoints

        self._thread: threading.Thread | None = None
        self._ingest_thread: threading.Thread | None = None
        self._rpc = None
        self._ingest = None
        self._pub = None

    def _cleanup_embedded(
        self,
        *,
        rpc_srv=None,
        ingest_srv=None,
        pub_srv=None,
        ingest_thread: threading.Thread | None = None,
        rpc_thread: threading.Thread | None = None,
    ) -> None:
        try:
            if rpc_srv is not None:
                rpc_srv.stop()
        except Exception:
            pass
        try:
            if ingest_srv is not None:
                ingest_srv.stop()
        except Exception:
            pass
        try:
            if rpc_srv is not None:
                rpc_srv.close()
        except Exception:
            pass
        try:
            if ingest_srv is not None:
                ingest_srv.close()
        except Exception:
            pass
        try:
            if ingest_thread is not None and ingest_thread.is_alive():
                ingest_thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            if rpc_thread is not None and rpc_thread.is_alive():
                rpc_thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            if pub_srv is not None:
                pub_srv.close()
        except Exception:
            pass

        self._rpc = None
        self._ingest = None
        self._pub = None
        self._thread = None
        self._ingest_thread = None

    def start(self) -> MessagePlaneEndpoints:
        return self._start_embedded()

    def _start_embedded(self) -> MessagePlaneEndpoints:
        if self._thread is not None and self._thread.is_alive():
            return self._endpoints
        rpc_srv = None
        ingest_srv = None
        pub_srv = None
        ingest_thread = None
        t = None
        try:
            from plugin.message_plane.ingest_server import MessagePlaneIngestServer
            from plugin.message_plane.pub_server import MessagePlanePubServer
            from plugin.message_plane.rpc_server import MessagePlaneRpcServer
            from plugin.message_plane.stores import StoreRegistry, TopicStore
            from plugin.settings import MESSAGE_PLANE_STORE_MAXLEN

            stores = StoreRegistry(default_store="messages")
            # conversations 是独立的 store，用于存储对话上下文（与 messages 分离）
            for name in ("messages", "events", "lifecycle", "runs", "export", "memory", "conversations"):
                stores.register(TopicStore(name=name, maxlen=MESSAGE_PLANE_STORE_MAXLEN))

            pub_srv = MessagePlanePubServer(endpoint=str(self._endpoints.pub))
            ingest_srv = MessagePlaneIngestServer(endpoint=str(self._endpoints.ingest), stores=stores, pub_server=pub_srv)
            rpc_srv = MessagePlaneRpcServer(endpoint=str(self._endpoints.rpc), pub_server=pub_srv, stores=stores)

            ingest_thread = threading.Thread(target=ingest_srv.serve_forever, daemon=True, name="message-plane-ingest")
            ingest_thread.start()

            def _run_rpc() -> None:
                try:
                    rpc_srv.serve_forever()
                finally:
                    try:
                        rpc_srv.close()
                    except Exception:
                        pass

            t = threading.Thread(target=_run_rpc, daemon=True, name="message-plane-rpc")
            t.start()

            self._thread = t
            self._ingest_thread = ingest_thread
            self._rpc = rpc_srv
            self._ingest = ingest_srv
            self._pub = pub_srv
            logger.info("message_plane embedded started")
        except Exception as e:
            logger.warning("message_plane embedded start failed: {}", e)
            self._cleanup_embedded(
                rpc_srv=rpc_srv,
                ingest_srv=ingest_srv,
                pub_srv=pub_srv,
                ingest_thread=ingest_thread,
                rpc_thread=t,
            )
            raise
        return self._endpoints

    def stop(self) -> None:
        rpc_srv = self._rpc
        ingest_srv = self._ingest
        pub_srv = self._pub
        ingest_thread = self._ingest_thread
        rpc_thread = self._thread

        self._rpc = None
        self._ingest = None
        self._pub = None
        self._thread = None
        self._ingest_thread = None

        self._cleanup_embedded(
            rpc_srv=rpc_srv,
            ingest_srv=ingest_srv,
            pub_srv=pub_srv,
            ingest_thread=ingest_thread,
            rpc_thread=rpc_thread,
        )

    def health_check(self, *, timeout_s: float = 1.0) -> bool:
        if not _wait_tcp_ready(str(self._endpoints.rpc), timeout_s=float(timeout_s)):
            return False
        return _rpc_health_check(str(self._endpoints.rpc), timeout_s=float(timeout_s))

    async def health_check_async(self, *, timeout_s: float = 1.0) -> bool:
        # Async-safe variant of ``health_check`` — never blocks the event loop thread.
        if not await _wait_tcp_ready_async(str(self._endpoints.rpc), timeout_s=float(timeout_s)):
            return False
        return await asyncio.to_thread(
            _rpc_health_check, str(self._endpoints.rpc), timeout_s=float(timeout_s)
        )


def _parse_tcp_endpoint(endpoint: str) -> tuple[str, int] | None:
    if not isinstance(endpoint, str) or not endpoint.startswith("tcp://"):
        return None
    host_port = endpoint[6:]
    if ":" not in host_port:
        return None
    host, port_text = host_port.rsplit(":", 1)
    if not host:
        return None
    try:
        port = int(port_text)
    except (TypeError, ValueError):
        return None
    if port <= 0 or port > 65535:
        return None
    return host, port


def _is_tcp_port_available(host: str, port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            probe.close()
        except OSError:
            pass


def _resolve_endpoint_with_fallback(endpoint: str, used_ports: set[tuple[str, int]]) -> str:
    parsed = _parse_tcp_endpoint(endpoint)
    if parsed is None:
        return endpoint
    host, base_port = parsed

    if (host, base_port) not in used_ports and _is_tcp_port_available(host, base_port):
        used_ports.add((host, base_port))
        return endpoint

    for port in range(base_port + 1, base_port + 200):
        hp = (host, port)
        if hp in used_ports:
            continue
        if _is_tcp_port_available(host, port):
            used_ports.add(hp)
            fallback = f"tcp://{host}:{port}"
            logger.warning("message_plane endpoint occupied, fallback: {} -> {}", endpoint, fallback)
            return fallback

    return endpoint


def build_message_plane_runner() -> MessagePlaneRunner:
    from plugin.settings import (
        MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT,
        MESSAGE_PLANE_ZMQ_PUB_ENDPOINT,
        MESSAGE_PLANE_ZMQ_RPC_ENDPOINT,
    )

    rpc_env = os.getenv("NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT", str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT))
    pub_env = os.getenv("NEKO_MESSAGE_PLANE_ZMQ_PUB_ENDPOINT", str(MESSAGE_PLANE_ZMQ_PUB_ENDPOINT))
    ingest_env = os.getenv("NEKO_MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT", str(MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT))

    used_ports: set[tuple[str, int]] = set()
    rpc_ep = _resolve_endpoint_with_fallback(str(rpc_env), used_ports)
    pub_ep = _resolve_endpoint_with_fallback(str(pub_env), used_ports)
    ingest_ep = _resolve_endpoint_with_fallback(str(ingest_env), used_ports)

    os.environ["NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT"] = rpc_ep
    os.environ["NEKO_MESSAGE_PLANE_ZMQ_PUB_ENDPOINT"] = pub_ep
    os.environ["NEKO_MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT"] = ingest_ep

    endpoints = MessagePlaneEndpoints(
        rpc=rpc_ep,
        pub=pub_ep,
        ingest=ingest_ep,
    )

    return PythonMessagePlaneRunner(endpoints=endpoints)
