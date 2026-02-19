from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

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
            import socket

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

    try:
        rpc = MessagePlaneRpcClient(plugin_id="server", endpoint=str(endpoint))
        resp = rpc.request(op="health", args={}, timeout=float(timeout_s))
        if not isinstance(resp, dict):
            return False
        if not resp.get("ok"):
            return False
        return True
    except Exception:
        return False


def _resolve_rust_message_plane_bin(configured: str) -> str:
    # Priority:
    # 1) Explicit path from settings/env
    # 2) Binary bundled in wheel (neko-message-plane-bin)
    # 3) Fallback to PATH
    cfg = str(configured or "").strip()
    if cfg and cfg != "neko-message-plane":
        return cfg
    try:
        from neko_message_plane_wheel import get_binary_path

        p = str(get_binary_path() or "").strip()
        if p:
            return p
    except Exception:
        pass
    return cfg or "neko-message-plane"


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

    def start(self) -> MessagePlaneEndpoints:
        if self._run_mode == "external":
            return self._start_external()
        return self._start_embedded()

    def _start_embedded(self) -> MessagePlaneEndpoints:
        if self._thread is not None and self._thread.is_alive():
            return self._endpoints
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
        return self._endpoints

    def _start_external(self) -> MessagePlaneEndpoints:
        if self._proc is not None and self._proc.poll() is None:
            return self._endpoints

        env = dict(os.environ)
        env.setdefault("NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT", str(self._endpoints.rpc))
        env.setdefault("NEKO_MESSAGE_PLANE_ZMQ_PUB_ENDPOINT", str(self._endpoints.pub))
        env.setdefault("NEKO_MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT", str(self._endpoints.ingest))

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
            return self._endpoints

        _wait_tcp_ready(str(self._endpoints.rpc), timeout_s=3.0)
        _wait_tcp_ready(str(self._endpoints.ingest), timeout_s=3.0)
        _wait_tcp_ready(str(self._endpoints.pub), timeout_s=3.0)
        return self._endpoints

    def stop(self) -> None:
        if self._run_mode == "external":
            p = self._proc
            self._proc = None
            if p is None:
                return
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:
                pass
            try:
                p.wait(timeout=1.0)
            except Exception:
                try:
                    if p.poll() is None:
                        p.kill()
                except Exception:
                    pass
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

    def health_check(self, *, timeout_s: float = 1.0) -> bool:
        if not _wait_tcp_ready(str(self._endpoints.rpc), timeout_s=float(timeout_s)):
            return False
        return _rpc_health_check(str(self._endpoints.rpc), timeout_s=float(timeout_s))


class RustMessagePlaneRunner(MessagePlaneRunner):
    def __init__(self, *, endpoints: MessagePlaneEndpoints, binary_path: Optional[str] = None, workers: int = 0) -> None:
        self._endpoints = endpoints
        self._binary_path = _resolve_rust_message_plane_bin(binary_path or "neko-message-plane")
        self._workers = workers
        self._proc: subprocess.Popen | None = None

    def start(self) -> MessagePlaneEndpoints:
        if self._proc is not None and self._proc.poll() is None:
            return self._endpoints

        cmd = [
            self._binary_path,
            "--rpc-endpoint",
            str(self._endpoints.rpc),
            "--ingest-endpoint",
            str(self._endpoints.ingest),
            "--pub-endpoint",
            str(self._endpoints.pub),
        ]
        
        if self._workers != 0:
            cmd.extend(["--workers", str(self._workers)])

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=None,
                stderr=None,
                close_fds=True,
            )
            workers_info = f"workers={self._workers or 'auto'}"
            logger.info("message_plane rust process started pid={} {}", int(self._proc.pid), workers_info)
        except Exception as e:
            self._proc = None
            logger.warning("message_plane rust process start failed: {}", e)
            return self._endpoints

        # Don't block the main event loop (FastAPI lifespan). Do the readiness wait in background.
        try:
            time.sleep(0.05)
        except Exception:
            pass
        try:
            if self._proc is not None and self._proc.poll() is not None:
                rc = self._proc.returncode
                logger.warning("message_plane rust process exited early (code={})", rc)
                return self._endpoints
        except Exception:
            pass

        def _bg_wait_ready() -> None:
            try:
                # Give the process a short window to bind sockets and accept RPC.
                deadline = time.time() + 5.0
                last_err: str | None = None
                while time.time() < deadline:
                    try:
                        # If the process died, stop waiting.
                        if self._proc is not None and self._proc.poll() is not None:
                            logger.warning(
                                "message_plane rust process exited early during readiness wait (code={})",
                                self._proc.returncode,
                            )
                            return
                    except Exception:
                        pass

                    if not _wait_tcp_ready(str(self._endpoints.rpc), timeout_s=0.5):
                        last_err = "rpc not ready"
                    elif not _wait_tcp_ready(str(self._endpoints.ingest), timeout_s=0.5):
                        last_err = "ingest not ready"
                    elif not _wait_tcp_ready(str(self._endpoints.pub), timeout_s=0.5):
                        last_err = "pub not ready"
                    else:
                        ok = self.health_check(timeout_s=0.5)
                        if ok:
                            logger.info("message_plane rust ready")
                            return
                        last_err = "rpc health_check failed"

                    try:
                        time.sleep(0.2)
                    except Exception:
                        pass

                logger.warning("message_plane rust health_check failed (may still be starting): {}", last_err)
            except Exception as e:
                logger.warning("message_plane rust readiness wait failed: {}", e)

        try:
            t = threading.Thread(target=_bg_wait_ready, daemon=True, name="message-plane-rust-wait")
            t.start()
        except Exception:
            pass
        return self._endpoints

    def stop(self) -> None:
        p = self._proc
        self._proc = None
        if p is None:
            return
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=1.0)
        except Exception:
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass

    def health_check(self, *, timeout_s: float = 1.0) -> bool:
        if not _wait_tcp_ready(str(self._endpoints.rpc), timeout_s=float(timeout_s)):
            return False
        return _rpc_health_check(str(self._endpoints.rpc), timeout_s=float(timeout_s))


def build_message_plane_runner() -> MessagePlaneRunner:
    from plugin.settings import (
        MESSAGE_PLANE_BACKEND,
        MESSAGE_PLANE_RUST_BIN,
        MESSAGE_PLANE_RUN_MODE,
        MESSAGE_PLANE_WORKERS,
        MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT,
        MESSAGE_PLANE_ZMQ_PUB_ENDPOINT,
        MESSAGE_PLANE_ZMQ_RPC_ENDPOINT,
    )

    backend = str(MESSAGE_PLANE_BACKEND).strip().lower()
    endpoints = MessagePlaneEndpoints(
        rpc=str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT),
        pub=str(MESSAGE_PLANE_ZMQ_PUB_ENDPOINT),
        ingest=str(MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT),
    )

    if backend == "rust":
        return RustMessagePlaneRunner(
            endpoints=endpoints,
            binary_path=str(MESSAGE_PLANE_RUST_BIN),
            workers=int(MESSAGE_PLANE_WORKERS),
        )
    return PythonMessagePlaneRunner(run_mode=str(MESSAGE_PLANE_RUN_MODE), endpoints=endpoints)
