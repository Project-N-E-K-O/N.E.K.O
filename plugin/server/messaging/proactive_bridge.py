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
from plugin.reply_contract import parse_agent_reply_spec

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

                if payload.get("message_type") != "proactive_notification":
                    continue

                raw_content = payload.get("content")
                metadata = payload.get("metadata") or {}
                # 通过 result_parser 确保 content 不含原始 JSON
                try:
                    from brain.result_parser import parse_push_message_content
                    render_content = parse_push_message_content(raw_content)
                except Exception:
                    render_content = str(raw_content or "").strip()
                reply_spec = parse_agent_reply_spec(
                    metadata if isinstance(metadata, dict) else None,
                    default_reply=True,
                    default_mode="proactive",
                )
                if not reply_spec.reply_enabled:
                    continue
                if reply_spec.include is False:
                    render_content = ""
                summary = reply_spec.summary or reply_spec.detail or render_content
                detail = reply_spec.detail or render_content
                content = summary or detail

                raw_str = str(raw_content or "")
                if content != raw_str.strip():
                    logger.info(
                        "proactive bridge: content parsed: '{}' → '{}'",
                        raw_str[:120], content[:120],
                    )
                else:
                    logger.info(
                        "proactive bridge: content passthrough: '{}'",
                        content[:120],
                    )

                if not content:
                    continue

                plugin_id = payload.get("plugin_id", "")

                proactive_event = {
                    "event_type": "proactive_message",
                    "lanlan_name": metadata.get("target_lanlan") or None,
                    "text": content,
                    "summary": summary,
                    "detail": detail,
                    "channel": f"plugin:{plugin_id}" if plugin_id else "plugin",
                    "task_id": metadata.get("task_id", ""),
                    "success": True,
                    "status": "completed",
                    "timestamp": payload.get("time", ""),
                }

                try:
                    push_sock.send_json(proactive_event, zmq.NOBLOCK)
                    logger.info(
                        "proactive bridge forwarded: plugin={} content={}",
                        plugin_id,
                        content[:80],
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
