"""Bridge: message_plane PUB → agent event bus (AGENT_PUSH_ADDR).

Subscribes to the message_plane PUB endpoint, watches for messages
with ``message_type == "proactive_notification"``, and forwards them
as ``proactive_message`` events to main_server's PULL socket so the
AI can deliver a proactive spoken response.

Flow: plugin ─(ZMQ ingest)→ message_plane ─(PUB)→ **this bridge** ─(PUSH)→ main_server PULL
"""
from __future__ import annotations

import json
import os
import threading
import time

from plugin.logging_config import get_logger

try:
    import zmq
except Exception:  # pragma: no cover
    zmq = None

logger = get_logger("server.messaging.proactive_bridge")


def _resolve_agent_push_addr() -> str:
    raw = os.getenv("NEKO_ZMQ_AGENT_PUSH_PORT", "").strip()
    if raw:
        try:
            port = int(raw)
            if 1 <= port <= 65535:
                return f"tcp://127.0.0.1:{port}"
        except (ValueError, TypeError):
            pass
    return "tcp://127.0.0.1:48962"


class ProactiveBridge:
    """Daemon thread that relays proactive plugin notifications to main_server."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if zmq is None:
            logger.warning("pyzmq not available; proactive bridge disabled")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        t = threading.Thread(target=self._run, daemon=True, name="proactive-bridge")
        self._thread = t
        t.start()
        logger.info("proactive bridge started")

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None and t.is_alive():
            t.join(timeout=2.0)

    def _run(self) -> None:
        from plugin.settings import MESSAGE_PLANE_ZMQ_PUB_ENDPOINT

        pub_endpoint = os.getenv(
            "NEKO_MESSAGE_PLANE_ZMQ_PUB_ENDPOINT",
            str(MESSAGE_PLANE_ZMQ_PUB_ENDPOINT),
        )
        agent_push_addr = _resolve_agent_push_addr()

        # Brief wait for message_plane PUB to bind before we connect.
        time.sleep(1.0)
        if self._stop.is_set():
            return

        ctx = zmq.Context.instance()
        sub_sock = ctx.socket(zmq.SUB)
        sub_sock.linger = 0
        sub_sock.setsockopt(zmq.RCVTIMEO, 1000)
        sub_sock.connect(pub_endpoint)
        sub_sock.setsockopt_string(zmq.SUBSCRIBE, "messages.")

        push_sock = ctx.socket(zmq.PUSH)
        push_sock.linger = 1000
        push_sock.connect(agent_push_addr)

        logger.info(
            "proactive bridge connected: sub={} push={}",
            pub_endpoint,
            agent_push_addr,
        )

        try:
            while not self._stop.is_set():
                try:
                    parts = sub_sock.recv_multipart()
                except zmq.Again:
                    continue
                except Exception as e:
                    if not self._stop.is_set():
                        logger.debug("proactive bridge recv error: {}", e)
                        time.sleep(0.1)
                    continue

                if len(parts) < 2:
                    continue

                try:
                    event = json.loads(parts[1])
                except Exception:
                    continue

                payload = event.get("payload") if isinstance(event, dict) else None
                if not isinstance(payload, dict):
                    continue

                if not isinstance(payload, dict):
                    continue

                msg_type = payload.get("message_type")
                plugin_id = payload.get("plugin_id", "")
                metadata = payload.get("metadata") or {}

                # -------- 1. Proactive Spoken Notification --------
                if msg_type == "proactive_notification":
                    raw_content = payload.get("content")
                    # 通过 result_parser 确保 content 不含原始 JSON
                    try:
                        from brain.result_parser import parse_push_message_content
                        content = parse_push_message_content(raw_content)
                    except Exception:
                        content = str(raw_content or "").strip()

                    if not content:
                        continue

                    forward_event = {
                        "event_type": "proactive_message",
                        "lanlan_name": metadata.get("target_lanlan") or None,
                        "text": content,
                        "summary": content,
                        "detail": content,
                        "channel": f"plugin:{plugin_id}" if plugin_id else "plugin",
                        "task_id": metadata.get("task_id", ""),
                        "success": True,
                        "status": "completed",
                        "timestamp": payload.get("time", ""),
                    }
                
                # -------- 2. Music Allowlist Add --------
                elif msg_type == "music_allowlist_add":
                    forward_event = {
                        "event_type": "music_allowlist_add",
                        "domains": metadata.get("domains", []),
                        "source": plugin_id,
                        "timestamp": payload.get("time", "")
                    }

                # -------- 3. Music Direct Play --------
                elif msg_type == "music_play_url":
                    forward_event = {
                        "event_type": "music_play_url",
                        "url": metadata.get("url"),
                        "name": metadata.get("name"),
                        "artist": metadata.get("artist"),
                        "source": plugin_id,
                        "timestamp": payload.get("time", "")
                    }
                else:
                    continue

                try:
                    push_sock.send_json(forward_event, zmq.NOBLOCK)
                    logger.info(
                        "proactive bridge forwarded: plugin={} type={}",
                        plugin_id,
                        forward_event.get("event_type"),
                    )
                except Exception as e:
                    logger.warning("proactive bridge push failed: {}", e)
        finally:
            try:
                sub_sock.close(linger=0)
            except Exception:
                pass
            try:
                push_sock.close(linger=0)
            except Exception:
                pass


_bridge = ProactiveBridge()


def start_proactive_bridge() -> None:
    _bridge.start()


def stop_proactive_bridge() -> None:
    _bridge.stop()
