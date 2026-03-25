"""
N.E.K.O Channel for NekoClaw

NekoClaw 自定义渠道，用于接收来自 N.E.K.O 插件的消息。

安装方法：
1. 将此文件复制到 ~/.copaw/custom_channels/neko_channel.py
2. 在 ~/.copaw/config.json 中添加配置：

   "channels": {
     "neko": {
       "enabled": true,
       "bot_prefix": "",
       "host": "127.0.0.1",
       "port": 8089
     }
   }

3. 重启运行时服务
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any, Dict, List, Optional

from agentscope_runtime.engine.schemas.agent_schemas import (
    TextContent,
    ImageContent,
    VideoContent,
    AudioContent,
    FileContent,
    ContentType,
)
from copaw.app.channels.base import BaseChannel
from copaw.app.channels.schema import ChannelType

try:
    from aiohttp import web
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


class NekoChannel(BaseChannel):
    """N.E.K.O 渠道，通过 HTTP 接收消息，默认仅监听本机 `127.0.0.1`。"""

    channel: ChannelType = "neko"

    @staticmethod
    def parse_reply_timeout(value: object, default: float | None = 300.0) -> float | None:
        if value in (None, ""):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else None

    def __init__(
        self,
        process,
        enabled: bool = True,
        bot_prefix: str = "",
        host: str = "127.0.0.1",
        port: int = 8089,
        reply_timeout: float | None = 300.0,
        **kwargs,
    ):
        super().__init__(process, on_reply_sent=kwargs.get("on_reply_sent"))
        self.enabled = enabled
        self.bot_prefix = bot_prefix
        self.host = host
        self.port = port
        self.reply_timeout = reply_timeout
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._pending_replies: Dict[str, asyncio.Future] = {}
        self._pending_sender_replies: Dict[str, deque[asyncio.Future]] = {}

    @classmethod
    def from_config(cls, process, config, on_reply_sent=None, show_tool_details=True):
        return cls(
            process=process,
            enabled=getattr(config, "enabled", True),
            bot_prefix=getattr(config, "bot_prefix", ""),
            host=getattr(config, "host", "127.0.0.1"),
            port=getattr(config, "port", 8089),
            reply_timeout=cls.parse_reply_timeout(getattr(config, "reply_timeout", 300.0)),
            on_reply_sent=on_reply_sent,
        )

    @classmethod
    def from_env(cls, process, on_reply_sent=None):
        import os
        return cls(
            process=process,
            enabled=os.getenv("NEKO_CHANNEL_ENABLED", "true").lower() == "true",
            host=os.getenv("NEKO_CHANNEL_HOST", "127.0.0.1"),
            port=int(os.getenv("NEKO_CHANNEL_PORT", "8089")),
            reply_timeout=cls.parse_reply_timeout(os.getenv("NEKO_CHANNEL_REPLY_TIMEOUT", "300")),
            on_reply_sent=on_reply_sent,
        )

    def build_agent_request_from_native(self, native_payload):
        """将 N.E.K.O 消息转换为 AgentRequest"""
        payload = native_payload if isinstance(native_payload, dict) else {}

        channel_id = payload.get("channel_id") or self.channel
        sender_id = payload.get("sender_id") or "unknown"
        meta = payload.get("meta") or {}
        session_id = payload.get("session_id") or self.resolve_session_id(sender_id, meta)

        content_parts = []

        # 添加文本内容
        text = payload.get("text", "")
        if text:
            content_parts.append(TextContent(type=ContentType.TEXT, text=text))

        # 处理附件
        for att in payload.get("attachments") or []:
            att_type = (att.get("type") or "file").lower()
            url = att.get("url") or ""
            if not url:
                continue

            if att_type == "image":
                content_parts.append(ImageContent(type=ContentType.IMAGE, image_url=url))
            elif att_type == "video":
                content_parts.append(VideoContent(type=ContentType.VIDEO, video_url=url))
            elif att_type == "audio":
                content_parts.append(AudioContent(type=ContentType.AUDIO, data=url))
            else:
                content_parts.append(FileContent(type=ContentType.FILE, file_url=url))

        if not content_parts:
            content_parts = [TextContent(type=ContentType.TEXT, text="")]

        request = self.build_agent_request_from_user_content(
            channel_id=channel_id,
            sender_id=sender_id,
            session_id=session_id,
            content_parts=content_parts,
            channel_meta=meta,
        )
        request.channel_meta = meta
        return request

    async def start(self):
        """启动 HTTP 服务器"""
        if not self.enabled:
            return

        if not HAS_AIOHTTP:
            raise RuntimeError("aiohttp is required for NekoChannel. Install with: pip install aiohttp")

        self._app = web.Application()
        self._app.router.add_post("/neko/send", self._handle_send)
        self._app.router.add_get("/health", self._handle_health)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

        print(f"[NekoChannel] HTTP server started on http://{self.host}:{self.port}")

    async def stop(self):
        """停止 HTTP 服务器"""
        if self._site:
            await self._site.stop()
            self._site = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._app = None
        print("[NekoChannel] HTTP server stopped")

    async def send(self, to_handle, text, meta=None):
        """发送回复（存储到 pending replies）"""
        meta = meta or {}
        request_id = meta.get("request_id")

        future = None
        if request_id:
            future = self._pending_replies.get(request_id)
        elif to_handle:
            queue = self._pending_sender_replies.get(to_handle)
            while queue and queue[0].done():
                queue.popleft()
            if queue:
                future = queue[0]

        if future is not None:
            if not future.done():
                future.set_result(text)
        else:
            print(f"[NekoChannel] send() called but no matching future: to_handle={to_handle} request_id={request_id} pending_request_ids={list(self._pending_replies.keys())}")

    async def _handle_health(self, request: web.Request) -> web.Response:
        """健康检查端点"""
        return web.json_response({"status": "healthy", "channel": "neko"})

    async def _handle_send(self, request: web.Request) -> web.Response:
        """处理来自 N.E.K.O 的消息"""
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "Invalid payload: expected object"}, status=400)
        if "meta" in payload and payload.get("meta") is not None and not isinstance(payload.get("meta"), dict):
            return web.json_response({"error": "Invalid meta: expected object"}, status=400)

        reply_timeout: float | None = self.reply_timeout
        try:
            meta_obj = payload.get("meta") or {}
            if isinstance(meta_obj, dict):
                reply_timeout = self.parse_reply_timeout(
                    meta_obj.get("reply_timeout", reply_timeout),
                    default=reply_timeout,
                )
        except Exception:
            pass

        # 生成请求 ID 用于追踪回复
        import uuid
        request_id = str(uuid.uuid4())[:8]

        # 添加 request_id 到 meta
        meta = payload.get("meta") or {}
        meta["request_id"] = request_id
        payload["meta"] = meta

        # 创建 Future 用于等待回复
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending_replies[request_id] = future
        sender_id = payload.get("sender_id")
        if sender_id:
            self._pending_sender_replies.setdefault(sender_id, deque()).append(future)

        try:
            # 入队处理
            self._enqueue(payload)

            # 等待回复（默认最多 300 秒，可由请求方通过 meta.reply_timeout 覆盖）
            try:
                reply = await asyncio.wait_for(future, timeout=reply_timeout)
            except asyncio.TimeoutError:
                reply = "[超时：NekoClaw 未在规定时间内响应]"

            return web.json_response({
                "reply": self.bot_prefix + reply if self.bot_prefix else reply,
                "sender_id": payload.get("sender_id"),
                "session_id": payload.get("session_id"),
                "request_id": request_id,
            })

        finally:
            self._pending_replies.pop(request_id, None)
            if sender_id:
                queue = self._pending_sender_replies.get(sender_id)
                if queue:
                    filtered_queue = deque(
                        pending_future for pending_future in queue if pending_future is not future
                    )
                    if filtered_queue:
                        self._pending_sender_replies[sender_id] = filtered_queue
                    else:
                        self._pending_sender_replies.pop(sender_id, None)
