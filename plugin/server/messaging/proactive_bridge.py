"""Bridge: message_plane PUB → agent event bus (AGENT_PUSH_ADDR).

Subscribes to the message_plane PUB endpoint, watches for v2 push_message
payloads, and translates them into the legacy ``proactive_message`` /
``music_play_url`` / ``music_allowlist_add`` events that main_server's
``handle_agent_event`` already understands.

The v2 schema (``visibility`` + ``ai_behavior`` + ``parts``) is the single
source of truth — see :mod:`plugin.sdk.shared.core.push_message_schema`.
Legacy ``message_type`` payloads still arrive when an older plugin is
loaded; the SDK adapter (``plugin.core.context.PluginContext.push_message``)
runs the v1→v2 translation client-side so by the time the payload reaches
this bridge it always has v2 fields populated.

Flow: plugin ─(ZMQ ingest)→ message_plane ─(PUB)→ **this bridge** ─(PUSH)→ main_server PULL
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from plugin.logging_config import get_logger
from plugin.sdk.shared.core.push_message_schema import AI_BEHAVIOR_VALUES

try:
    import zmq
except Exception:  # pragma: no cover
    zmq = None

logger = get_logger("server.messaging.proactive_bridge")


# Map (visibility, ai_behavior) → legacy delivery_mode the existing
# main_server proactive_message handler understands.  ``visibility`` is
# treated as a set; we only consult ``"hud"`` membership because
# proactive_message always also fires the agent_notification HUD path.
def _resolve_delivery_mode(visibility: list[str], ai_behavior: str) -> str:
    if ai_behavior == "respond":
        return "proactive"
    if ai_behavior == "read":
        return "passive"
    return "silent"


def _aggregate_text_parts(parts: list[dict[str, Any]]) -> str:
    """Concatenate ``type=text`` parts into a single string."""
    pieces: list[str] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text":
            t = p.get("text")
            if isinstance(t, str) and t:
                pieces.append(t)
    return "\n".join(pieces).strip()


def _media_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter for image/audio/video parts (passed through to the AI session)."""
    out: list[dict[str, Any]] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        if p.get("type") in ("image", "audio", "video"):
            entry: dict[str, Any] = {"type": p.get("type")}
            if isinstance(p.get("binary_base64"), str):
                entry["binary_base64"] = p["binary_base64"]
            if isinstance(p.get("url"), str):
                entry["url"] = p["url"]
            if isinstance(p.get("mime"), str):
                entry["mime"] = p["mime"]
            out.append(entry)
    return out


def _ui_action_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [p for p in parts if isinstance(p, dict) and p.get("type") == "ui_action"]


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
    """Daemon thread that relays plugin push_message payloads to main_server."""

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
                    parts_raw = sub_sock.recv_multipart()
                except zmq.Again:
                    continue
                except Exception as e:
                    if not self._stop.is_set():
                        logger.debug("proactive bridge recv error: {}", e)
                        time.sleep(0.1)
                    continue

                if len(parts_raw) < 2:
                    continue

                try:
                    event = json.loads(parts_raw[1])
                except Exception:
                    continue

                payload = event.get("payload") if isinstance(event, dict) else None
                if not isinstance(payload, dict):
                    continue

                try:
                    self._dispatch(payload, push_sock)
                except Exception as e:
                    logger.error("Error dispatching push payload: {}", e)
                    continue
        finally:
            try:
                sub_sock.close(linger=0)
            except Exception:
                pass
            try:
                push_sock.close(linger=0)
            except Exception:
                pass

    def _dispatch(self, payload: dict[str, Any], push_sock: Any) -> None:
        """Translate a v2 (or legacy-shimmed) push payload into legacy
        agent-event-bus events and PUSH them to main_server.

        A single push_message can produce multiple events:

        * ``proactive_message`` (text + media for AI session, including
          delivery_mode silent for HUD-only notifications)
        * ``music_play_url`` / ``music_allowlist_add`` (UI side effects
          carried as ui_action parts)

        Empty plumbing — no parts and no actionable signal — is dropped
        with a debug log so plugin authors notice on first run.
        """
        plugin_id = payload.get("plugin_id", "")
        timestamp = payload.get("time", "")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

        # v2 fields are guaranteed by the SDK adapter's translate step,
        # but accept legacy shapes too for safety.
        schema = payload.get("schema")
        visibility = payload.get("visibility") if isinstance(payload.get("visibility"), list) else []
        ai_behavior = payload.get("ai_behavior")
        if ai_behavior not in AI_BEHAVIOR_VALUES:
            ai_behavior = "respond"
        parts = payload.get("parts") if isinstance(payload.get("parts"), list) else []

        target_lanlan = payload.get("target_lanlan") or metadata.get("target_lanlan") or None

        events_out: list[dict[str, Any]] = []

        # ---- ui_action parts → legacy music_* events ----
        for ui in _ui_action_parts(parts):
            action = ui.get("action")
            if action == "media_play_url":
                url = ui.get("url") or metadata.get("url")
                if not isinstance(url, str) or not url.strip():
                    logger.debug(
                        "ui_action=media_play_url missing url; plugin={}",
                        plugin_id,
                    )
                    continue
                events_out.append(
                    {
                        "event_type": "music_play_url",
                        "lanlan_name": target_lanlan,
                        "url": url,
                        "name": ui.get("name") or metadata.get("name"),
                        "artist": ui.get("artist") or metadata.get("artist"),
                        "source": plugin_id,
                        "timestamp": timestamp,
                    }
                )
            elif action == "media_allowlist_add":
                domains = ui.get("domains") or metadata.get("domains") or []
                if not isinstance(domains, list) or not domains:
                    logger.debug(
                        "ui_action=media_allowlist_add missing domains; plugin={}",
                        plugin_id,
                    )
                    continue
                events_out.append(
                    {
                        "event_type": "music_allowlist_add",
                        "lanlan_name": target_lanlan,
                        "domains": list(domains),
                        "source": plugin_id,
                        "timestamp": timestamp,
                    }
                )
            else:
                logger.warning(
                    "ui_action with unknown action={!r}; plugin={}",
                    action, plugin_id,
                )

        # ---- text + media parts → proactive_message (or HUD-only) ----
        text = _aggregate_text_parts(parts)
        # Bridge-level result_parser strips raw JSON envelopes that some
        # plugins still emit when they hand-craft content.  Best-effort.
        if text:
            try:
                from brain.result_parser import parse_push_message_content

                text = parse_push_message_content(text)
            except Exception as e:
                # Best-effort sanitization — fall back to the raw aggregated
                # text if the parser misbehaves on this particular shape.
                logger.debug("parse_push_message_content failed (fallback to raw): {}", e)

        media = _media_parts(parts)
        has_ai_payload = bool(text) or bool(media)

        if has_ai_payload or "hud" in visibility:
            delivery_mode = _resolve_delivery_mode(visibility, ai_behavior)
            proactive_event: dict[str, Any] = {
                "event_type": "proactive_message",
                "lanlan_name": target_lanlan,
                "text": text or "",
                "summary": text or "",
                "detail": text or "",
                "channel": f"plugin:{plugin_id}" if plugin_id else "plugin",
                "task_id": metadata.get("task_id", ""),
                "success": True,
                "status": "completed",
                "delivery_mode": delivery_mode,
                "source_kind": "plugin",
                "source_name": str(plugin_id) if plugin_id else "",
                "timestamp": timestamp,
                # v2 carries media inline; main_server will base64-decode
                # and call session.send_media_input before/after the
                # callback queue depending on ai_behavior.
                "media_parts": media,
                "visibility": list(visibility),
                "ai_behavior": ai_behavior,
            }
            # When ai_behavior=blind we still want the HUD agent_notification
            # to fire (handled by main_server's existing branch).  Setting
            # delivery_mode="silent" tells the proactive_message handler to
            # skip the LLM injection but keep the WS notif.
            events_out.append(proactive_event)

        if not events_out:
            logger.debug(
                "push payload produced no events: plugin={} schema={} parts={}",
                plugin_id, schema, len(parts),
            )
            return

        for ev in events_out:
            try:
                push_sock.send_json(ev, zmq.NOBLOCK)
                logger.info(
                    "proactive bridge forwarded: plugin={} event={}",
                    plugin_id, ev.get("event_type"),
                )
            except Exception as e:
                logger.warning("proactive bridge push failed: {}", e)


_bridge = ProactiveBridge()


def start_proactive_bridge() -> None:
    _bridge.start()


def stop_proactive_bridge() -> None:
    _bridge.stop()
