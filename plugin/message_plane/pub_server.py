from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict

import zmq
from loguru import logger


@dataclass
class MessagePlanePubServer:
    endpoint: str

    def __post_init__(self) -> None:
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.PUB)
        self._sock.linger = 0
        self._sock.bind(self.endpoint)
        logger.info("[message_plane] pub server bound: {}", self.endpoint)

    def publish(self, topic: str, event: Dict[str, Any]) -> None:
        t = str(topic).encode("utf-8")
        body = json.dumps(event, ensure_ascii=False).encode("utf-8")
        try:
            self._sock.send_multipart([t, body])
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._sock.close(linger=0)
        except Exception:
            pass
