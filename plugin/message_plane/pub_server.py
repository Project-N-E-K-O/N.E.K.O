from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Dict

import zmq
from plugin.logging_config import logger


def _json_default(obj: Any) -> Any:
    """Make the PUB JSON serialiser tolerate non-JSON-native values.

    The message_plane wire payload carries a legacy ``binary_data`` field as
    raw ``bytes`` (the canonical image already rides in ``parts[].binary_base64``).
    Plain ``json.dumps`` raises on ``bytes``, and that failure was swallowed
    upstream — silently dropping every image-bearing push_message before it
    reached any subscriber. Base64-encode bytes; stringify anything else so a
    single unexpected field can never drop the whole message.
    """
    if isinstance(obj, (bytes, bytearray)):
        return base64.b64encode(bytes(obj)).decode("ascii")
    logger.debug("pub server: stringifying unexpected non-JSON value of type {}", type(obj).__name__)
    return str(obj)


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
        try:
            body = json.dumps(event, ensure_ascii=False, default=_json_default).encode("utf-8")
            self._sock.send_multipart([t, body])
        except Exception as exc:
            logger.debug("pub server publish failed (topic={}): {}", topic, exc)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close(linger=0)
            except Exception:
                pass
            self._sock = None
