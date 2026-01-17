from __future__ import annotations

import os
import signal
from typing import Optional

from loguru import logger

from .pub_server import MessagePlanePubServer
from .rpc_server import MessagePlaneRpcServer


def run_message_plane(
    *,
    rpc_endpoint: Optional[str] = None,
    pub_endpoint: Optional[str] = None,
) -> None:
    endpoint = rpc_endpoint or os.getenv("NEKO_MESSAGE_PLANE_RPC", "tcp://127.0.0.1:38865")
    pub_ep = pub_endpoint or os.getenv("NEKO_MESSAGE_PLANE_PUB", "tcp://127.0.0.1:38866")
    pub_srv = MessagePlanePubServer(endpoint=pub_ep)
    srv = MessagePlaneRpcServer(endpoint=endpoint, pub_server=pub_srv)

    def _stop(*_args: object) -> None:
        try:
            srv.stop()
        except Exception:
            pass

    try:
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
    except Exception:
        pass

    try:
        srv.serve_forever()
    finally:
        try:
            srv.close()
        except Exception:
            pass
        try:
            pub_srv.close()
        except Exception:
            pass
        logger.info("[message_plane] stopped")


if __name__ == "__main__":
    run_message_plane()
