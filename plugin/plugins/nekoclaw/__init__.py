"""
NekoClaw Plugin

N.E.K.O 的 NekoClaw 能力插件。
负责把用户任务转发给本地或远端 NekoClaw 服务，并将回复结果回传给插件调用方。

使用说明：
1. 在 N.E.K.O 的插件配置中提供 `nekoclaw` 配置段，例如：
   {
     "nekoclaw": {
       "url": "http://127.0.0.1:8089",
       "timeout": 300.0,
       "default_sender_id": "neko_user"
     }
   }
2. 通过 `check_connection` 检查 NekoClaw 服务是否已启动且可访问。
3. 根据任务类型选择合适入口：
   - `chat`：纯文本任务，适合搜索、分析、执行一般指令。
   - `chat_with_image`：文本 + 单张图片，适合看图问答、截图分析。
   - `chat_multimodal`：文本 + 多附件，适合图片、视频、音频、文件混合输入。

调用约定：
- `sender_id` 用于区分用户；未传时使用配置中的 `default_sender_id`。
- `session_id` 用于维持上下文；未传时会根据 `sender_id` 自动生成。
- 所有入口成功时都返回 `Ok(...)`，失败时返回 `Err(SdkError(...))`。
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
class NekoclawPlugin(NekoPluginBase):
    """
    NekoClaw 插件适配层。

    该类对外暴露多个 `plugin_entry`，供 NEKO 或其他插件统一调用。
    核心职责是：
    - 读取插件配置并维护连接参数
    - 将请求转换为 NekoClaw HTTP 接口需要的 payload
    - 对返回结果进行标准化封装
    - 将连接错误、超时、HTTP 错误转换为 SDK 可识别的错误对象
    """

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: Dict[str, Any] = {}
        self._url: str = "http://127.0.0.1:8089"
        self._timeout: float = 300.0
        self._http_timeout: float = 315.0
        self._default_sender_id: str = "neko_user"

    @lifecycle(id="startup")
    async def startup(self, **_):
        """启动时加载 `nekoclaw` 配置，并缓存服务地址、超时和默认发送者。"""
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg.get("nekoclaw") if isinstance(cfg.get("nekoclaw"), dict) else {}

        self._url = self._cfg.get("url", "http://127.0.0.1:8089").rstrip("/")
        self._timeout = float(self._cfg.get("timeout", 300.0))
        self._http_timeout = max(self._timeout + 15.0, self._timeout)
        self._default_sender_id = self._cfg.get("default_sender_id", "neko_user")

        self.logger.info(
            "NekoClaw started: url={}, timeout={}, http_timeout={}",
            self._url,
            self._timeout,
            self._http_timeout,
        )
        return Ok({"status": "running", "url": self._url})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        """插件关闭时记录日志；当前无需主动释放额外资源。"""
        self.logger.info("NekoClaw shutdown")
        return Ok({"status": "stopped"})

    async def _send_request(
        self,
        text: str,
        sender_id: Optional[str] = None,
        session_id: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        向 NekoClaw 的 `/neko/send` 接口发送一次标准请求。

        参数说明：
        - `text`：任务正文，不能为空。
        - `sender_id`：用户标识；为空时回退到默认发送者。
        - `session_id`：会话标识；为空时自动按发送者生成。
        - `attachments`：可选附件列表，元素形如 `{"type": "...", "url": "..."}`。
        - `meta`：透传给服务端的额外元信息。
        """
        sender_id = sender_id or self._default_sender_id
        session_id = session_id or f"neko_session_{sender_id}"

        payload_meta = dict(meta or {})
        payload_meta.setdefault("reply_timeout", self._timeout)

        payload = {
            "channel_id": "neko",
            "sender_id": sender_id,
            "session_id": session_id,
            "text": text,
            "meta": payload_meta,
        }

        if attachments:
            payload["attachments"] = attachments

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self._http_timeout, connect=min(10.0, self._http_timeout))) as client:
                response = await client.post(
                    f"{self._url}/neko/send",
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException:
            raise SdkError(f"NekoClaw 请求超时 ({self._timeout}s)")
        except httpx.HTTPStatusError as e:
            raise SdkError(f"NekoClaw 返回错误: HTTP {e.response.status_code}")
        except Exception as e:
            raise SdkError(f"NekoClaw 连接失败: {e}")

    @plugin_entry(
        id="chat",
        name="委托NekoClaw处理任务",
        description=(
            "将用户的需求或请求交由 NekoClaw 执行，并将处理结果返回给 NEKO 转述给用户。"
            "适用场景：用户有需要实际执行的任务、需要调用外部工具或服务、希望获取额外能力（如搜索、操作、分析）的请求。"
            "只要用户有具体的任务意图，即应调用此插件。NekoClaw 会自主处理后返回结果，NEKO 再用自己的语气转述给用户。"
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
        timeout=1800.0,
        llm_result_fields=["reply"],
    )
    async def chat(
        self,
        text: str,
        sender_id: Optional[str] = None,
        session_id: Optional[str] = None,
        **_,
    ):
        """
        发送纯文本任务。

        适用场景：
        - 让 NekoClaw 执行一般文本指令
        - 不需要附带图片或其他媒体
        - 希望保留会话上下文时可显式传入 `session_id`
        """
        if not text or not text.strip():
            return Err(SdkError("消息内容不能为空"))

        try:
            result = await self._send_request(
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
        name="发送图文消息",
        description=(
            "向 NekoClaw 发送文本和图片消息并获取回复。"
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
        timeout=1800.0,
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
        """
        发送图文任务。

        使用约定：
        - `image_url` 与 `image_path` 二选一，优先使用 `image_url`
        - 传入本地路径时会在插件内读取文件并编码为 data URL 后再转发
        - 未提供图片时，本入口仍可工作，但更推荐直接调用 `chat`
        """
        if not text or not text.strip():
            return Err(SdkError("消息内容不能为空"))

        attachments = []

        if image_url:
            attachments.append({"type": "image", "url": image_url})
        elif image_path:
            path = Path(image_path).expanduser()
            if not path.exists():
                return Err(SdkError(f"图片文件不存在: {image_path}"))
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
            result = await self._send_request(
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
        name="发送多模态消息",
        description=(
            "向 NekoClaw 发送包含多种媒体类型的消息。"
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
        timeout=1800.0,
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
        """
        发送多模态任务。

        `attachments` 形如：
        [
          {"type": "image", "url": "https://..."},
          {"type": "audio", "url": "https://..."}
        ]

        说明：
        - 支持的 `type` 为 `image`、`video`、`audio`、`file`
        - 未识别的类型会降级为 `file`
        - 空 URL 会被忽略
        """
        if not text or not text.strip():
            return Err(SdkError("消息内容不能为空"))

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
            result = await self._send_request(
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
        name="检查NekoClaw连接",
        description="检查与 NekoClaw 服务的连接状态",
    )
    async def check_connection(self, **_):
        """
        检查 NekoClaw 服务是否可达。

        适合在以下时机调用：
        - 插件初始化完成后做连通性自检
        - 前端或调度层需要确认服务当前是否在线
        - 排查 `chat` / `chat_with_image` / `chat_multimodal` 调用失败原因
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._url}/health")
                if response.status_code == 200:
                    return Ok({
                        "connected": True,
                        "url": self._url,
                        "status": "healthy",
                    })
                else:
                    return Ok({
                        "connected": False,
                        "url": self._url,
                        "status": f"HTTP {response.status_code}",
                    })
        except httpx.ConnectError:
            return Ok({
                "connected": False,
                "url": self._url,
                "status": "connection_refused",
            })
        except Exception as e:
            return Ok({
                "connected": False,
                "url": self._url,
                "status": str(e),
            })
