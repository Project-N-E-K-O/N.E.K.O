"""
CoPaw Bridge Plugin

连接 CoPaw 多模态渠道的 N.E.K.O 插件。
支持发送文本和图片消息到 CoPaw，并接收回复。

使用前需要：
1. 在 CoPaw 端安装自定义渠道（见下方 custom_channels/neko_channel.py）
2. 配置 CoPaw 的 config.json 启用 neko 渠道
3. 启动 CoPaw 服务（默认 http://127.0.0.1:8088）
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import httpx

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    Ok,
    Err,
    SdkError,
)


@neko_plugin
class CoPawBridgePlugin(NekoPluginBase):
    """CoPaw 桥接插件，支持多模态消息收发"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: Dict[str, Any] = {}
        self._copaw_url: str = "http://127.0.0.1:8088"
        self._timeout: float = 60.0
        self._default_sender_id: str = "neko_user"

    @lifecycle(id="startup")
    async def startup(self, **_):
        """启动时加载配置"""
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg.get("copaw") if isinstance(cfg.get("copaw"), dict) else {}

        self._copaw_url = self._cfg.get("url", "http://127.0.0.1:8089").rstrip("/")
        self._timeout = float(self._cfg.get("timeout", 60.0))
        self._default_sender_id = self._cfg.get("default_sender_id", "neko_user")

        self.logger.info(
            "CoPawBridge started: url={}, timeout={}",
            self._copaw_url,
            self._timeout,
        )
        return Ok({"status": "running", "url": self._copaw_url})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        """关闭插件"""
        self.logger.info("CoPawBridge shutdown")
        return Ok({"status": "stopped"})

    async def _send_to_copaw(
        self,
        text: str,
        sender_id: Optional[str] = None,
        session_id: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """发送消息到 CoPaw 并获取回复"""
        sender_id = sender_id or self._default_sender_id
        session_id = session_id or f"neko_session_{sender_id}"

        payload = {
            "channel_id": "neko",
            "sender_id": sender_id,
            "session_id": session_id,
            "text": text,
            "meta": meta or {},
        }

        if attachments:
            payload["attachments"] = attachments

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._copaw_url}/neko/send",
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException:
            raise SdkError(f"CoPaw 请求超时 ({self._timeout}s)")
        except httpx.HTTPStatusError as e:
            raise SdkError(f"CoPaw 返回错误: HTTP {e.response.status_code}")
        except Exception as e:
            raise SdkError(f"CoPaw 连接失败: {e}")

    @plugin_entry(
        id="chat",
        name = "发送消息到CoPaw",
        description=(
            "向 CoPaw 发送文本消息并获取回复。"
            "支持会话上下文，可指定 sender_id 区分不同用户。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要发送的文本消息",
                },
                "sender_id": {
                    "type": "string",
                    "description": "发送者 ID（可选，用于区分不同用户）",
                },
                "session_id": {
                    "type": "string",
                    "description": "会话 ID（可选，用于保持会话上下文）",
                },
            },
            "required": ["text"],
        },
        llm_result_fields=["reply"],
    )
    async def chat(
        self,
        text: str,
        sender_id: Optional[str] = None,
        session_id: Optional[str] = None,
        **_,
    ):
        """发送文本消息到 CoPaw"""
        if not text or not text.strip():
            return Err(SdkError("消息内容不能为空"))

        try:
            result = await self._send_to_copaw(
                text=text,
                sender_id=sender_id,
                session_id=session_id,
            )
            reply = result.get("reply", "")
            return Ok({
                "reply": reply,
                "sender_id": result.get("sender_id"),
                "session_id": result.get("session_id"),
            })
        except SdkError as e:
            return Err(e)
        except Exception as e:
            self.logger.exception("chat failed")
            return Err(SdkError(f"发送失败: {e}"))

    @plugin_entry(
        id="chat_with_image",
        name="发送图文消息到CoPaw",
        description=(
            "向 CoPaw 发送文本和图片消息并获取回复。"
            "图片可通过 URL 或本地文件路径提供。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要发送的文本消息",
                },
                "image_url": {
                    "type": "string",
                    "description": "图片 URL（与 image_path 二选一）",
                },
                "image_path": {
                    "type": "string",
                    "description": "本地图片路径（与 image_url 二选一）",
                },
                "sender_id": {
                    "type": "string",
                    "description": "发送者 ID（可选）",
                },
                "session_id": {
                    "type": "string",
                    "description": "会话 ID（可选）",
                },
            },
            "required": ["text"],
        },
        llm_result_fields=["reply"],
    )
    async def chat_with_image(
        self,
        text: str,
        image_url: Optional[str] = None,
        image_path: Optional[str] = None,
        sender_id: Optional[str] = None,
        session_id: Optional[str] = None,
        **_,
    ):
        """发送文本和图片消息到 CoPaw"""
        if not text or not text.strip():
            return Err(SdkError("消息内容不能为空"))

        attachments = []

        if image_url:
            attachments.append({"type": "image", "url": image_url})
        elif image_path:
            path = Path(image_path).expanduser()
            if not path.exists():
                return Err(SdkError(f"图片文件不存在: {image_path}"))
            # 读取图片并转为 base64 data URL
            try:
                image_data = path.read_bytes()
                b64_data = base64.b64encode(image_data).decode("utf-8")
                ext = path.suffix.lower().lstrip(".") or "png"
                mime_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}
                mime = f"image/{mime_map.get(ext, 'png')}"
                data_url = f"data:{mime};base64,{b64_data}"
                attachments.append({"type": "image", "url": data_url})
            except Exception as e:
                return Err(SdkError(f"读取图片失败: {e}"))

        try:
            result = await self._send_to_copaw(
                text=text,
                sender_id=sender_id,
                session_id=session_id,
                attachments=attachments if attachments else None,
            )
            reply = result.get("reply", "")
            return Ok({
                "reply": reply,
                "sender_id": result.get("sender_id"),
                "session_id": result.get("session_id"),
                "has_image": bool(attachments),
            })
        except SdkError as e:
            return Err(e)
        except Exception as e:
            self.logger.exception("chat_with_image failed")
            return Err(SdkError(f"发送失败: {e}"))

    @plugin_entry(
        id="chat_multimodal",
        name="发送多模态消息到CoPaw",
        description=(
            "向 CoPaw 发送包含多种媒体类型的消息。"
            "支持文本、图片、视频、音频、文件附件。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要发送的文本消息",
                },
                "attachments": {
                    "type": "array",
                    "description": "附件列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["image", "video", "audio", "file"],
                                "description": "附件类型",
                            },
                            "url": {
                                "type": "string",
                                "description": "附件 URL",
                            },
                        },
                        "required": ["type", "url"],
                    },
                },
                "sender_id": {
                    "type": "string",
                    "description": "发送者 ID（可选）",
                },
                "session_id": {
                    "type": "string",
                    "description": "会话 ID（可选）",
                },
            },
            "required": ["text"],
        },
        llm_result_fields=["reply"],
    )
    async def chat_multimodal(
        self,
        text: str,
        attachments: Optional[List[Dict[str, str]]] = None,
        sender_id: Optional[str] = None,
        session_id: Optional[str] = None,
        **_,
    ):
        """发送多模态消息到 CoPaw"""
        if not text or not text.strip():
            return Err(SdkError("消息内容不能为空"))

        # 验证附件格式
        validated_attachments = []
        if attachments:
            for att in attachments:
                att_type = att.get("type", "file").lower()
                att_url = att.get("url", "")
                if not att_url:
                    continue
                if att_type not in ("image", "video", "audio", "file"):
                    att_type = "file"
                validated_attachments.append({"type": att_type, "url": att_url})

        try:
            result = await self._send_to_copaw(
                text=text,
                sender_id=sender_id,
                session_id=session_id,
                attachments=validated_attachments if validated_attachments else None,
            )
            reply = result.get("reply", "")
            return Ok({
                "reply": reply,
                "sender_id": result.get("sender_id"),
                "session_id": result.get("session_id"),
                "attachment_count": len(validated_attachments),
            })
        except SdkError as e:
            return Err(e)
        except Exception as e:
            self.logger.exception("chat_multimodal failed")
            return Err(SdkError(f"发送失败: {e}"))

    @plugin_entry(
        id="check_connection",
        name="检查CoPaw连接",
        description="检查与 CoPaw 服务的连接状态",
    )
    async def check_connection(self, **_):
        """检查 CoPaw 服务是否可达"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._copaw_url}/health")
                if response.status_code == 200:
                    return Ok({
                        "connected": True,
                        "url": self._copaw_url,
                        "status": "healthy",
                    })
                else:
                    return Ok({
                        "connected": False,
                        "url": self._copaw_url,
                        "status": f"HTTP {response.status_code}",
                    })
        except httpx.ConnectError:
            return Ok({
                "connected": False,
                "url": self._copaw_url,
                "status": "connection_refused",
            })
        except Exception as e:
            return Ok({
                "connected": False,
                "url": self._copaw_url,
                "status": str(e),
            })
