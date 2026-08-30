from __future__ import annotations

import signal
import secrets
import threading
from typing import Optional

from plugin.logging_config import logger

from plugin.settings import (
    MESSAGE_PLANE_FRAMES_STORE_MAXLEN,
    MESSAGE_PLANE_STORE_MAXLEN,
    MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT,
    MESSAGE_PLANE_ZMQ_PUB_ENDPOINT,
    MESSAGE_PLANE_ZMQ_RPC_ENDPOINT,
)

from .ingest_server import MessagePlaneIngestServer
from .pub_server import MessagePlanePubServer
from .rpc_server import MessagePlaneRpcServer
from .stores import build_default_store_registry


def run_message_plane(
    *,
    rpc_endpoint: Optional[str] = None,
    pub_endpoint: Optional[str] = None,
    ingest_endpoint: Optional[str] = None,
    auth_token: Optional[str] = None,
) -> None:
    endpoint = rpc_endpoint or MESSAGE_PLANE_ZMQ_RPC_ENDPOINT
    pub_ep = pub_endpoint or MESSAGE_PLANE_ZMQ_PUB_ENDPOINT
    ingest_ep = ingest_endpoint or MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT
    # Standalone entry point: no bridge shares this process, so a caller that
    # does not supply a credential gets a fresh one and effectively runs a
    # write-closed plane.
    resolved_token = str(auth_token or "").strip() or secrets.token_urlsafe(32)

    stores = build_default_store_registry(
        maxlen=MESSAGE_PLANE_STORE_MAXLEN,
        frames_maxlen=MESSAGE_PLANE_FRAMES_STORE_MAXLEN,
    )

    pub_srv = MessagePlanePubServer(endpoint=pub_ep)
    ingest_srv = MessagePlaneIngestServer(
        endpoint=ingest_ep,
        stores=stores,
        pub_server=pub_srv,
        auth_token=resolved_token,
    )
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
