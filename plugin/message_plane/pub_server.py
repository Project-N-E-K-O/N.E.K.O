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
        try:
            self._sock.bind(self.endpoint)
        except Exception as e:
            self._sock.close(linger=0)
            self._sock = None  # 清理 socket
            raise e
        logger.info("pub server bound: {}", self.endpoint)

    def publish(self, topic: str, event: Dict[str, Any]) -> None:
        if self._sock is None:
            raise RuntimeError("Socket is not bound")
        t = str(topic).encode("utf-8")
        body = json.dumps(event, ensure_ascii=False).encode("utf-8")
        try:
            self._sock.send_multipart([t, body])
        except Exception:
            pass

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close(linger=0)
            except Exception:
                pass
            self._sock = None
