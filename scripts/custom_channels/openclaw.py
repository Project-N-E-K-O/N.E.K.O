# -*- coding: utf-8 -*-
"""CoPaw custom OpenClaw channel for N.E.K.O.

This channel starts a small local HTTP bridge that exposes:

- GET  /health
- POST /neko/send

N.E.K.O can call this bridge with either plain text or multimodal
``content_parts`` payloads. The channel then forwards the request into the
current CoPaw agent pipeline and waits for the final reply before returning
JSON to the caller.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentscope_runtime.engine.schemas.agent_schemas import (
    AudioContent,
    ContentType,
    FileContent,
    ImageContent,
    MessageType,
    Role,
    RunStatus,
    TextContent,
    VideoContent,
)

from copaw.app.channels.base import BaseChannel, OutgoingContentPart
from copaw.app.channels.schema import ChannelType
from copaw.config import get_config_path

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8089
DEFAULT_REPLY_TIMEOUT = 300.0
_PROGRESS_REPLY_MARKERS = (
    "好的",
    "收到",
    "明白",
    "我来",
    "我试试",
    "稍等",
    "请稍等",
    "正在",
    "处理中",
    "开始",
    "马上",
    "这就",
    "我先",
    "先帮你",
)
_PROGRESS_INDICATORS = (
    "正在",
    "处理中",
    "进行中",
    "稍等",
    "请稍等",
    "...",
    "…",
)


def _cfg_get(config: Any, key: str, default: Any = None) -> Any:
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _normalize_timeout(value: Any, default: float = DEFAULT_REPLY_TIMEOUT) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return default
    return timeout if timeout > 0 else default


def _is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().lower()
    if not normalized:
        return True
    if normalized in {"127.0.0.1", "localhost", "::1"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _serialize_part(part: Any) -> Dict[str, Any]:
    content_type = getattr(part, "type", None)
    payload: Dict[str, Any] = {"type": content_type}
    # `part` can be different content objects from the runtime schema, so
    # getattr is intentional here to keep serialization duck-typed and safe.
    if content_type == ContentType.TEXT:
        payload["text"] = getattr(part, "text", "") or ""
    elif content_type == ContentType.REFUSAL:
        payload["refusal"] = getattr(part, "refusal", "") or ""
    elif content_type == ContentType.IMAGE:
        payload["image_url"] = getattr(part, "image_url", "") or ""
    elif content_type == ContentType.VIDEO:
        payload["video_url"] = getattr(part, "video_url", "") or ""
    elif content_type == ContentType.AUDIO:
        payload["data"] = getattr(part, "data", "") or ""
        if getattr(part, "format", None):
            payload["format"] = getattr(part, "format")
    elif content_type == ContentType.FILE:
        payload["file_url"] = getattr(part, "file_url", "") or ""
        if getattr(part, "filename", None):
            payload["filename"] = getattr(part, "filename")
        if getattr(part, "file_id", None):
            payload["file_id"] = getattr(part, "file_id")
    return payload


def _part_from_dict(item: Any) -> Optional[Any]:
    if not isinstance(item, dict):
        return None
    part_type = str(item.get("type") or "").strip().lower()
    if part_type == "text":
        return TextContent(
            type=ContentType.TEXT,
            text=str(item.get("text") or ""),
        )
    if part_type == "image":
        image_url = str(item.get("image_url") or item.get("url") or "").strip()
        if not image_url:
            return None
        return ImageContent(type=ContentType.IMAGE, image_url=image_url)
    if part_type == "video":
        video_url = str(item.get("video_url") or item.get("url") or "").strip()
        if not video_url:
            return None
        return VideoContent(type=ContentType.VIDEO, video_url=video_url)
    if part_type == "audio":
        data = str(item.get("data") or item.get("url") or "").strip()
        if not data:
            return None
        return AudioContent(
            type=ContentType.AUDIO,
            data=data,
            format=item.get("format"),
        )
    if part_type == "file":
        file_url = str(item.get("file_url") or item.get("url") or "").strip()
        if not file_url:
            return None
        return FileContent(
            type=ContentType.FILE,
            file_url=file_url,
            filename=item.get("filename"),
            file_id=item.get("file_id"),
        )
    return None


def _build_content_parts(payload: Dict[str, Any]) -> List[Any]:
    parts: List[Any] = []

    text = payload.get("text")
    if isinstance(text, str) and text:
        parts.append(TextContent(type=ContentType.TEXT, text=text))

    raw_parts = payload.get("content_parts")
    if isinstance(raw_parts, list):
        for item in raw_parts:
            part = _part_from_dict(item)
            if part is not None:
                parts.append(part)

    raw_images = payload.get("images")
    if raw_images is None:
        image_items: List[Any] = []
    elif isinstance(raw_images, list):
        image_items = raw_images
    else:
        image_items = [raw_images]

    for image in image_items:
        if isinstance(image, str) and image.strip():
            parts.append(
                ImageContent(type=ContentType.IMAGE, image_url=image.strip()),
            )
        elif isinstance(image, dict):
            part = _part_from_dict({"type": "image", **image})
            if part is not None:
                parts.append(part)

    raw_audios = payload.get("audios")
    if raw_audios is None:
        raw_audios = payload.get("audio")
    if raw_audios is None:
        audio_items: List[Any] = []
    elif isinstance(raw_audios, list):
        audio_items = raw_audios
    else:
        audio_items = [raw_audios]

    for audio in audio_items:
        if isinstance(audio, str) and audio.strip():
            parts.append(AudioContent(type=ContentType.AUDIO, data=audio.strip()))
        elif isinstance(audio, dict):
            part = _part_from_dict({"type": "audio", **audio})
            if part is not None:
                parts.append(part)

    raw_files = payload.get("files")
    if raw_files is None:
        file_items: List[Any] = []
    elif isinstance(raw_files, list):
        file_items = raw_files
    else:
        file_items = [raw_files]

    for file_item in file_items:
        if isinstance(file_item, str) and file_item.strip():
            parts.append(
                FileContent(type=ContentType.FILE, file_url=file_item.strip()),
            )
        elif isinstance(file_item, dict):
            part = _part_from_dict({"type": "file", **file_item})
            if part is not None:
                parts.append(part)

    return parts


def _looks_like_progress_only_reply(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    compact = normalized.replace(" ", "").replace("\n", "")
    if len(compact) > 36:
        return False
    has_marker = any(marker in compact for marker in _PROGRESS_REPLY_MARKERS)
    if not has_marker:
        return False

    if any(indicator in compact for indicator in _PROGRESS_INDICATORS):
        return True

    trailing_ellipsis_stripped = compact.rstrip(".…")
    has_trailing_ellipsis = trailing_ellipsis_stripped != compact
    if not has_trailing_ellipsis:
        return False

    return any(
        trailing_ellipsis_stripped.endswith(marker)
        for marker in _PROGRESS_REPLY_MARKERS
    )


class OpenClawInboundRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    channel_id: str = Field(default="neko")
    sender_id: str = Field(default="neko_user")
    session_id: Optional[str] = None
    text: Optional[str] = None
    content_parts: Optional[List[Dict[str, Any]]] = None
    images: Optional[List[Any]] = None
    audios: Optional[List[Any]] = None
    files: Optional[List[Any]] = None
    meta: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_audio_alias(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if value.get("audios") is None and value.get("audio") is not None:
            normalized = dict(value)
            normalized["audios"] = normalized.get("audio")
            return normalized
        return value

    @field_validator("images", "audios", "files", mode="before")
    @classmethod
    def _coerce_single_item_to_list(cls, value: Any) -> Any:
        if value is None or isinstance(value, list):
            return value
        return [value]


class OpenClawChannel(BaseChannel):
    channel: ChannelType = "openclaw"
    display_name = "OpenClaw"

    def __init__(
        self,
        process,
        enabled: bool = True,
        bot_prefix: str = "",
        on_reply_sent=None,
        show_tool_details: bool = True,
        filter_tool_messages: bool = False,
        filter_thinking: bool = False,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        reply_timeout: float = DEFAULT_REPLY_TIMEOUT,
        route_prefix: str = "",
        auth_token: str = "",
        **kwargs,
    ):
        super().__init__(
            process,
            on_reply_sent=on_reply_sent,
            show_tool_details=show_tool_details,
            filter_tool_messages=filter_tool_messages,
            filter_thinking=filter_thinking,
        )
        self.enabled = bool(enabled)
        self.bot_prefix = bot_prefix or ""
        self.host = str(host or DEFAULT_HOST).strip() or DEFAULT_HOST
        self.port = int(port or DEFAULT_PORT)
        self.reply_timeout = _normalize_timeout(reply_timeout)
        self.auth_token = str(
            auth_token or kwargs.get("auth_token") or os.getenv("OPENCLAW_AUTH_TOKEN") or "",
        ).strip()
        if not _is_loopback_host(self.host) and not self.auth_token:
            raise ValueError(
                "OpenClaw auth_token is required when binding to a non-loopback host.",
            )
        self.route_prefix = "/" + str(route_prefix or "").strip().strip("/")
        if self.route_prefix == "/":
            self.route_prefix = ""
        self._app: Optional[FastAPI] = None
        self._server: Optional[uvicorn.Server] = None
        self._task: Optional[asyncio.Task[Any]] = None
        self._routes_ready = asyncio.Event()
        self._pending_replies: Dict[str, Dict[str, Any]] = {}
        workspace_dir = kwargs.get("workspace_dir")
        self._workspace_dir = (
            Path(workspace_dir).expanduser() if workspace_dir else None
        )
        self._kwargs = kwargs

    @classmethod
    def from_config(
        cls,
        process,
        config,
        on_reply_sent=None,
        show_tool_details: bool = True,
        filter_tool_messages: bool = False,
        filter_thinking: bool = False,
        **kwargs,
    ):
        return cls(
            process=process,
            enabled=bool(_cfg_get(config, "enabled", True)),
            bot_prefix=str(_cfg_get(config, "bot_prefix", "") or ""),
            on_reply_sent=on_reply_sent,
            show_tool_details=show_tool_details,
            filter_tool_messages=filter_tool_messages,
            filter_thinking=filter_thinking,
            host=str(_cfg_get(config, "host", DEFAULT_HOST) or DEFAULT_HOST),
            port=int(_cfg_get(config, "port", DEFAULT_PORT) or DEFAULT_PORT),
            reply_timeout=_cfg_get(
                config,
                "reply_timeout",
                _cfg_get(config, "replyTimeout", DEFAULT_REPLY_TIMEOUT),
            ),
            auth_token=str(
                _cfg_get(
                    config,
                    "auth_token",
                    _cfg_get(config, "authToken", os.getenv("OPENCLAW_AUTH_TOKEN", "")),
                )
                or ""
            ),
            route_prefix=str(
                _cfg_get(
                    config,
                    "route_prefix",
                    _cfg_get(config, "routePrefix", ""),
                )
                or ""
            ),
            **kwargs,
        )

    @classmethod
    def from_env(cls, process, on_reply_sent=None):
        return cls(
            process=process,
            on_reply_sent=on_reply_sent,
        )

    def resolve_session_id(
        self,
        sender_id: str,
        channel_meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        meta = channel_meta or {}
        session_id = str(meta.get("session_id") or "").strip()
        if session_id:
            return session_id
        conversation_id = str(meta.get("conversation_id") or "").strip()
        if conversation_id:
            return conversation_id
        role_name = str(meta.get("role_name") or "").strip()
        if role_name:
            return f"{self.channel}:{role_name}:{sender_id}"
        return f"{self.channel}:{sender_id}"

    def get_to_handle_from_request(self, request: Any) -> str:
        return str(getattr(request, "session_id", "") or getattr(request, "user_id", "") or "")

    @staticmethod
    def _sanitize_meta_for_request(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        raw = dict(meta or {})
        raw.pop("reply_future", None)
        raw.pop("reply_loop", None)
        raw.pop("incoming_message", None)
        return raw

    def build_agent_request_from_native(self, native_payload: Any):
        payload = native_payload if isinstance(native_payload, dict) else {}
        channel_id = str(payload.get("channel_id") or self.channel)
        sender_id = str(payload.get("sender_id") or "neko_user")
        meta = dict(payload.get("meta") or {})
        if payload.get("session_id") and "session_id" not in meta:
            meta["session_id"] = payload.get("session_id")
        content_parts = payload.get("content_parts") or _build_content_parts(payload)
        session_id = str(payload.get("session_id") or self.resolve_session_id(sender_id, meta))
        request_meta = self._sanitize_meta_for_request(meta)
        request = self.build_agent_request_from_user_content(
            channel_id=channel_id,
            sender_id=sender_id,
            session_id=session_id,
            content_parts=content_parts,
            channel_meta=request_meta,
        )
        request.user_id = sender_id
        request.channel_meta = request_meta
        return request

    async def _before_consume_process(self, request: Any) -> None:
        meta = getattr(request, "channel_meta", None)
        if isinstance(meta, dict):
            request.channel_meta = self._sanitize_meta_for_request(meta)

    def _safe_set_future_result(self, future: asyncio.Future[Any], value: Any) -> None:
        if not future.done():
            future.set_result(value)

    def _set_reply_payload(self, meta: Dict[str, Any], payload: Dict[str, Any]) -> None:
        reply_token = str(meta.get("reply_token") or "").strip()
        if not reply_token:
            return
        pending = self._pending_replies.pop(reply_token, None)
        if not pending:
            return
        reply_loop = pending.get("loop")
        reply_future = pending.get("future")
        if reply_loop is None or reply_future is None:
            return
        reply_loop.call_soon_threadsafe(
            self._safe_set_future_result,
            reply_future,
            payload,
        )

    def _make_reply_payload(
        self,
        parts: List[OutgoingContentPart],
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        text_chunks: List[str] = []
        serialized_parts: List[Dict[str, Any]] = []
        for part in parts:
            serialized = _serialize_part(part)
            serialized_parts.append(serialized)
            if serialized.get("type") == ContentType.TEXT and serialized.get("text"):
                text_chunks.append(str(serialized["text"]))
            elif serialized.get("type") == ContentType.REFUSAL and serialized.get("refusal"):
                text_chunks.append(str(serialized["refusal"]))

        reply_text = "\n".join(chunk for chunk in text_chunks if chunk).strip()
        payload = {
            "success": True,
            "reply": reply_text,
            "content_parts": serialized_parts,
        }
        if meta:
            payload["session_id"] = meta.get("session_id") or meta.get("conversation_id")
            payload["sender_id"] = meta.get("sender_id") or meta.get("original_sender_id")
            payload["channel_id"] = meta.get("origin_channel_id") or self.channel
        return payload

    def _build_reply_payload_from_message(
        self,
        message: Any,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if getattr(message, "type", None) != MessageType.MESSAGE:
            return None
        if getattr(message, "role", None) != Role.ASSISTANT:
            return None
        parts = self._message_to_content_parts(message)
        if not parts:
            return None
        has_user_visible_content = any(
            getattr(part, "type", None) in (
                ContentType.TEXT,
                ContentType.REFUSAL,
                ContentType.IMAGE,
                ContentType.VIDEO,
                ContentType.AUDIO,
                ContentType.FILE,
            )
            for part in parts
        )
        if not has_user_visible_content:
            return None
        payload = self._make_reply_payload(parts, meta)
        if _looks_like_progress_only_reply(payload.get("reply", "")):
            return None
        return payload

    def _build_final_reply_payload_from_response(
        self,
        response_event: Any,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        response = response_event
        if getattr(response_event, "data", None) is not None:
            response = response_event.data
        elif getattr(response_event, "response", None) is not None:
            response = response_event.response
        output = getattr(response, "output", None) or []
        for message in reversed(output):
            payload = self._build_reply_payload_from_message(message, meta)
            if payload:
                return payload
        return None

    def _resolve_http_bridge_reply_payload(
        self,
        *,
        last_response: Any,
        last_completed_message_payload: Optional[Dict[str, Any]],
        request: Any,
        send_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        final_reply_payload = None
        if (
            last_response is not None
            and getattr(last_response, "status", None) == RunStatus.Completed
        ):
            final_reply_payload = self._build_final_reply_payload_from_response(
                last_response,
                send_meta,
            )
        if final_reply_payload is None:
            final_reply_payload = last_completed_message_payload
        if final_reply_payload is not None:
            return final_reply_payload
        return {
            "success": False,
            "reply": "",
            "error": "OpenClaw did not produce a final reply payload.",
            "session_id": send_meta.get("session_id") or getattr(request, "session_id", ""),
            "sender_id": send_meta.get("sender_id") or send_meta.get("original_sender_id"),
            "channel_id": send_meta.get("origin_channel_id") or self.channel,
        }

    def _desired_channel_config(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "bot_prefix": self.bot_prefix,
            "filter_tool_messages": self._filter_tool_messages,
            "filter_thinking": self._filter_thinking,
            "host": self.host,
            "port": self.port,
            "reply_timeout": self.reply_timeout,
            "route_prefix": self.route_prefix.lstrip("/"),
        }

    def _check_auth(self, request: Request) -> None:
        if not self.auth_token:
            return
        bearer = request.headers.get("authorization", "")
        header_token = request.headers.get("x-openclaw-token", "")
        provided = header_token.strip()
        if not provided and bearer.lower().startswith("bearer "):
            provided = bearer[7:].strip()
        if provided != self.auth_token:
            raise HTTPException(status_code=401, detail="OpenClaw auth token is required")

    def _merge_channel_config_file(self, config_path: Path, *, create_if_missing: bool = False) -> None:
        if not config_path.exists():
            if not create_if_missing:
                logger.warning(
                    "openclaw auto-config skipped: config not found at %s",
                    config_path,
                )
                return
            data: Dict[str, Any] = {}
        else:
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                logger.exception(
                    "openclaw auto-config failed to read %s",
                    config_path,
                )
                return

        channels = data.get("channels")
        if not isinstance(channels, dict):
            channels = {}
            data["channels"] = channels

        existing = channels.get(self.channel)
        if not isinstance(existing, dict):
            existing = {}

        desired = self._desired_channel_config()
        updated = dict(existing)
        changed = self.channel not in channels
        for key, value in desired.items():
            if updated.get(key) != value:
                updated[key] = value
                changed = True

        if not changed:
            return

        channels[self.channel] = updated
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            logger.info(
                "openclaw auto-config persisted to %s",
                config_path,
            )
        except Exception:
            logger.exception(
                "openclaw auto-config failed to write %s",
                config_path,
            )

    def _ensure_channel_config_persisted(self) -> None:
        try:
            self._merge_channel_config_file(
                Path(get_config_path()).expanduser(),
                create_if_missing=True,
            )
        except Exception:
            logger.exception("openclaw auto-config failed for root config")

        if self._workspace_dir:
            self._merge_channel_config_file(
                self._workspace_dir / "agent.json",
                create_if_missing=False,
            )

    async def _send_error_via_meta(
        self,
        to_handle: str,
        send_meta: Dict[str, Any],
        err_text: str,
    ) -> None:
        await self.send_content_parts(
            to_handle,
            [TextContent(type=ContentType.TEXT, text=err_text)],
            send_meta or {},
        )

    async def _run_process_loop(
        self,
        request: Any,
        to_handle: str,
        send_meta: Dict[str, Any],
    ) -> None:
        last_response = None
        last_completed_message_payload = None
        http_bridge_reply = bool((send_meta or {}).get("reply_token"))
        try:
            async for event in self._process(request):
                obj = getattr(event, "object", None)
                status = getattr(event, "status", None)
                if obj == "message" and status == RunStatus.Completed:
                    if http_bridge_reply:
                        last_completed_message_payload = self._build_reply_payload_from_message(
                            event,
                            send_meta,
                        ) or last_completed_message_payload
                    else:
                        await self.on_event_message_completed(
                            request,
                            to_handle,
                            event,
                            send_meta,
                        )
                elif obj == "response":
                    last_response = event
                    await self.on_event_response(request, event)
            err_msg = self._get_response_error_message(last_response)
            if err_msg:
                await self._send_error_via_meta(
                    to_handle,
                    send_meta,
                    f"Error: {err_msg}",
                )
            elif http_bridge_reply:
                self._set_reply_payload(
                    send_meta,
                    self._resolve_http_bridge_reply_payload(
                        last_response=last_response,
                        last_completed_message_payload=last_completed_message_payload,
                        request=request,
                        send_meta=send_meta,
                    ),
                )
            if self._on_reply_sent:
                args = self.get_on_reply_sent_args(request, to_handle)
                self._on_reply_sent(self.channel, *args)
        except Exception:
            logger.exception("channel consume_one failed")
            await self._send_error_via_meta(
                to_handle,
                send_meta,
                "An error occurred while processing your request.",
            )

    async def _stream_with_tracker(
        self,
        payload: Any,
    ):
        request = self._payload_to_request(payload)

        if isinstance(payload, dict):
            send_meta = dict(payload.get("meta") or {})
            if payload.get("session_webhook"):
                send_meta["session_webhook"] = payload["session_webhook"]
        else:
            send_meta = getattr(request, "channel_meta", None) or {}

        bot_prefix = getattr(self, "bot_prefix", None) or getattr(
            self,
            "_bot_prefix",
            "",
        )
        if bot_prefix and "bot_prefix" not in send_meta:
            send_meta = {**send_meta, "bot_prefix": bot_prefix}

        to_handle = self.get_to_handle_from_request(request)
        await self._before_consume_process(request)

        last_response = None
        last_completed_message_payload = None
        http_bridge_reply = bool((send_meta or {}).get("reply_token"))
        process_iterator = None
        try:
            process_iterator = self._process(request)
            async for event in process_iterator:
                if hasattr(event, "model_dump_json"):
                    data = event.model_dump_json()
                elif hasattr(event, "json"):
                    data = event.json()
                else:
                    data = json.dumps({"text": str(event)})

                yield f"data: {data}\n\n"

                obj = getattr(event, "object", None)
                status = getattr(event, "status", None)

                if obj == "message" and status == RunStatus.Completed:
                    if http_bridge_reply:
                        last_completed_message_payload = self._build_reply_payload_from_message(
                            event,
                            send_meta,
                        ) or last_completed_message_payload
                    else:
                        await self.on_event_message_completed(
                            request,
                            to_handle,
                            event,
                            send_meta,
                        )
                elif obj == "response":
                    last_response = event
                    await self.on_event_response(request, event)

            err_msg = self._get_response_error_message(last_response)
            if err_msg:
                await self._on_consume_error(
                    request,
                    to_handle,
                    f"Error: {err_msg}",
                )
            elif http_bridge_reply:
                self._set_reply_payload(
                    send_meta,
                    self._resolve_http_bridge_reply_payload(
                        last_response=last_response,
                        last_completed_message_payload=last_completed_message_payload,
                        request=request,
                        send_meta=send_meta,
                    ),
                )

            if self._on_reply_sent:
                args = self.get_on_reply_sent_args(request, to_handle)
                self._on_reply_sent(self.channel, *args)

        except asyncio.CancelledError:
            logger.info(
                f"channel task cancelled: "
                f"session={getattr(request, 'session_id', '')[:30]}",
            )
            if process_iterator is not None:
                await process_iterator.aclose()
            raise

        except Exception as e:
            logger.exception(
                f"channel _stream_with_tracker failed: {e}, "
                f"session={getattr(request, 'session_id', 'N/A')[:30]}, "
                f"agent={to_handle}",
            )
            await self._on_consume_error(
                request,
                to_handle,
                "Internal error",
            )
            raise

    async def _handle_health(self) -> Dict[str, Any]:
        ready = bool(
            self.enabled
            and self._server is not None
            and getattr(self._server, "started", False)
            and self._task is not None
            and not self._task.done()
        )
        return {
            "enabled": self.enabled,
            "ready": ready,
            "reasons": ["OpenClaw channel bridge is running"] if ready else ["OpenClaw channel is not ready"],
            "provider": self.channel,
            "ok": True,
            "channel": self.channel,
            "host": self.host,
            "port": self.port,
        }

    async def _handle_neko_send(self, body: OpenClawInboundRequest, request: Request) -> Dict[str, Any]:
        if not self.enabled:
            raise HTTPException(status_code=503, detail="OpenClaw channel is disabled")
        if self._enqueue is None:
            raise HTTPException(status_code=503, detail="OpenClaw channel queue is not ready")

        loop = asyncio.get_running_loop()
        raw = body.model_dump(mode="python")
        meta = dict(raw.get("meta") or {})
        reply_timeout = _normalize_timeout(
            meta.get("reply_timeout", meta.get("replyTimeout", self.reply_timeout)),
            self.reply_timeout,
        )
        sender_id = str(raw.get("sender_id") or "neko_user")
        session_id = str(raw.get("session_id") or meta.get("session_id") or "").strip()

        if not session_id:
            session_id = self.resolve_session_id(sender_id, meta)

        reply_token = uuid.uuid4().hex
        reply_future = loop.create_future()
        self._pending_replies[reply_token] = {
            "loop": loop,
            "future": reply_future,
            "session_id": session_id,
            "sender_id": sender_id,
        }

        meta["reply_timeout"] = reply_timeout
        meta["reply_token"] = reply_token
        meta["session_id"] = session_id
        meta["original_sender_id"] = sender_id
        meta["origin_channel_id"] = str(raw.get("channel_id") or self.channel)
        meta["client_host"] = getattr(request.client, "host", "") if request.client else ""

        native_payload = {
            "channel_id": str(raw.get("channel_id") or "neko"),
            "sender_id": sender_id,
            "session_id": session_id,
            "content_parts": _build_content_parts(raw),
            "meta": meta,
        }

        if not native_payload["content_parts"]:
            native_payload["content_parts"] = [
                TextContent(type=ContentType.TEXT, text=" "),
            ]

        logger.info(
            "openclaw recv: sender=%s session=%s parts=%s",
            sender_id[:48],
            session_id[:64],
            len(native_payload["content_parts"]),
        )

        try:
            self._enqueue(native_payload)
            # Add a small buffer beyond reply_timeout so enqueue/transport latency
            # does not time out reply_future exactly at the user-facing limit.
            result = await asyncio.wait_for(reply_future, timeout=reply_timeout + 10.0)
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail=f"Timed out waiting for CoPaw reply after {reply_timeout}s",
            ) from exc
        finally:
            self._pending_replies.pop(reply_token, None)

        if not isinstance(result, dict):
            return {
                "success": True,
                "reply": str(result or ""),
                "session_id": session_id,
                "sender_id": sender_id,
            }

        result.setdefault("session_id", session_id)
        result.setdefault("sender_id", sender_id)
        result.setdefault("channel_id", native_payload["channel_id"])
        return result

    def _build_http_app(self) -> FastAPI:
        app = FastAPI(title="OpenClaw Custom Channel", docs_url=None, redoc_url=None, openapi_url=None)

        @app.get(f"{self.route_prefix}/health")
        async def health(request: Request) -> Dict[str, Any]:
            self._check_auth(request)
            return await self._handle_health()

        @app.post(f"{self.route_prefix}/neko/send")
        async def neko_send(body: OpenClawInboundRequest, request: Request) -> Dict[str, Any]:
            self._check_auth(request)
            return await self._handle_neko_send(body, request)

        return app

    async def start(self) -> None:
        if not self.enabled:
            logger.info("openclaw channel disabled, skipping HTTP bridge startup")
            return
        if self._task and not self._task.done():
            return

        self._ensure_channel_config_persisted()

        self._app = self._build_http_app()
        config = uvicorn.Config(
            self._app,
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=False,
            loop="asyncio",
        )
        self._server = uvicorn.Server(config)

        async def _run_server() -> None:
            try:
                await self._server.serve()
            except Exception:
                logger.exception("openclaw channel failed to start")
                raise

        self._task = asyncio.create_task(_run_server(), name="openclaw_custom_channel_server")
        while not getattr(self._server, "started", False):
            if self._task.done():
                exc = self._task.exception()
                if exc is not None:
                    raise exc
                raise RuntimeError("openclaw channel failed to start")
            await asyncio.sleep(0.05)
        self._routes_ready.set()
        logger.info(
            "openclaw channel started at http://%s:%s%s",
            self.host,
            self.port,
            self.route_prefix or "",
        )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                logger.debug(
                    "openclaw channel task ended with an exception during shutdown",
                    exc_info=True,
                )
        self._task = None
        self._server = None
        self._app = None
        self._routes_ready = asyncio.Event()

    async def send_content_parts(
        self,
        to_handle: str,
        parts: List[OutgoingContentPart],
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = self._make_reply_payload(parts, meta)
        if meta and meta.get("reply_token") is not None:
            self._set_reply_payload(meta, payload)
            return
        await self.send(to_handle, payload.get("reply", "") or "", meta)

    async def send(
        self,
        to_handle: str,
        text: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        if meta and meta.get("reply_token") is not None:
            self._set_reply_payload(
                meta,
                {
                    "success": True,
                    "reply": text or "",
                    "content_parts": [
                        {
                            "type": ContentType.TEXT,
                            "text": text or "",
                        },
                    ],
                    "session_id": meta.get("session_id") or to_handle,
                    "sender_id": meta.get("sender_id") or meta.get("original_sender_id"),
                    "channel_id": meta.get("origin_channel_id") or self.channel,
                },
            )
            return

        logger.warning(
            "openclaw proactive send is not implemented yet: to_handle=%s text=%r",
            to_handle,
            (text or "")[:200],
        )
