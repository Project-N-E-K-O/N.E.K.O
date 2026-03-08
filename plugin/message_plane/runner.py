from __future__ import annotations

import asyncio
import concurrent.futures
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from loguru import logger


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


def _wait_tcp_ready(endpoint: str, *, timeout_s: float = 2.0) -> bool:
    ep = str(endpoint)
    if not ep.startswith("tcp://"):
        return True
    rest = ep[len("tcp://") :]
    if ":" not in rest:
        return True
    host, port_s = rest.rsplit(":", 1)
    host = host.strip() or "127.0.0.1"
    try:
        port = int(port_s)
    except Exception:
        return True

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


def _rpc_health_check(endpoint: str, *, timeout_s: float = 1.0) -> bool:
    try:
        from plugin.sdk.message_plane_transport import MessagePlaneRpcClient
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
    def __init__(self, *, run_mode: str, endpoints: MessagePlaneEndpoints) -> None:
        self._run_mode = str(run_mode)
        self._endpoints = endpoints

        self._thread: threading.Thread | None = None
        self._ingest_thread: threading.Thread | None = None
        self._rpc = None
        self._ingest = None
        self._pub = None
        self._proc: subprocess.Popen | None = None

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

    def _terminate_process(self, proc: subprocess.Popen | None) -> None:
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=1.0)
            return
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            return
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            return
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            logger.warning("message_plane process did not exit after kill pid={}", getattr(proc, "pid", "?"))
        except Exception:
            pass

    def start(self) -> MessagePlaneEndpoints:
        if self._run_mode == "external":
            return self._start_external()
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

    def _start_external(self) -> MessagePlaneEndpoints:
        if self._proc is not None and self._proc.poll() is None:
            return self._endpoints

        env = dict(os.environ)
        env["NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT"] = str(self._endpoints.rpc)
        env["NEKO_MESSAGE_PLANE_ZMQ_PUB_ENDPOINT"] = str(self._endpoints.pub)
        env["NEKO_MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT"] = str(self._endpoints.ingest)

        try:
            cmd = [sys.executable, "-m", "plugin.message_plane.main"]
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=None,
                stderr=None,
                close_fds=True,
                env=env,
            )
            logger.info("message_plane external process started pid={}", int(self._proc.pid))
        except Exception as e:
            self._proc = None
            logger.warning("message_plane external process start failed: {}", e)
            raise

        for label, ep in [("rpc", self._endpoints.rpc), ("ingest", self._endpoints.ingest), ("pub", self._endpoints.pub)]:
            if not _wait_tcp_ready(str(ep), timeout_s=3.0):
                logger.warning("message_plane {} endpoint not ready: {}", label, ep)
        return self._endpoints

    def stop(self) -> None:
        if self._run_mode == "external":
            p = self._proc
            self._proc = None
            if p is None:
                return
            self._terminate_process(p)
            return

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
        MESSAGE_PLANE_RUN_MODE,
        MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT,
        MESSAGE_PLANE_ZMQ_PUB_ENDPOINT,
        MESSAGE_PLANE_ZMQ_RPC_ENDPOINT,
    )

    run_mode = os.getenv("NEKO_MESSAGE_PLANE_RUN_MODE", str(MESSAGE_PLANE_RUN_MODE)).strip().lower()
    if run_mode not in ("embedded", "external"):
        run_mode = str(MESSAGE_PLANE_RUN_MODE)

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

    return PythonMessagePlaneRunner(run_mode=run_mode, endpoints=endpoints)
