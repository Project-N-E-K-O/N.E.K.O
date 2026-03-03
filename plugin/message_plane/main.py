from __future__ import annotations

import signal
import threading
from typing import Optional

from loguru import logger

from plugin.settings import (
    MESSAGE_PLANE_STORE_MAXLEN,
    MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT,
    MESSAGE_PLANE_ZMQ_PUB_ENDPOINT,
    MESSAGE_PLANE_ZMQ_RPC_ENDPOINT,
)

from .ingest_server import MessagePlaneIngestServer
from .pub_server import MessagePlanePubServer
from .rpc_server import MessagePlaneRpcServer
from .stores import StoreRegistry, TopicStore


def run_message_plane(
    *,
    rpc_endpoint: Optional[str] = None,
    pub_endpoint: Optional[str] = None,
    ingest_endpoint: Optional[str] = None,
) -> None:
    endpoint = rpc_endpoint or MESSAGE_PLANE_ZMQ_RPC_ENDPOINT
    pub_ep = pub_endpoint or MESSAGE_PLANE_ZMQ_PUB_ENDPOINT
    ingest_ep = ingest_endpoint or MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT

    stores = StoreRegistry(default_store="messages")
    # conversations 是独立的 store，用于存储对话上下文（与 messages 分离）
    for name in ("messages", "events", "lifecycle", "runs", "export", "memory", "conversations"):
        stores.register(TopicStore(name=name, maxlen=MESSAGE_PLANE_STORE_MAXLEN))

    pub_srv = MessagePlanePubServer(endpoint=pub_ep)
    ingest_srv = MessagePlaneIngestServer(endpoint=ingest_ep, stores=stores, pub_server=pub_srv)
    srv = MessagePlaneRpcServer(endpoint=endpoint, pub_server=pub_srv, stores=stores)

    ingest_thread = threading.Thread(target=ingest_srv.serve_forever, daemon=True)
    ingest_thread.start()

    def _stop(*_args: object) -> None:
        try:
            srv.stop()
        except Exception:
            logger.debug("error stopping rpc server during shutdown")
        try:
            ingest_srv.stop()
        except Exception:
            logger.debug("error stopping ingest server during shutdown")
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
    except Exception:
        logger.debug("failed to register signal handlers")

    try:
        srv.serve_forever()
    finally:
        try:
            srv.close()
        except Exception:
            pass
        try:
            ingest_srv.stop()
        except Exception:
            pass
        try:
            ingest_thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            pub_srv.close()
        except Exception:
            pass
        logger.info("stopped")


if __name__ == "__main__":
    run_message_plane()
