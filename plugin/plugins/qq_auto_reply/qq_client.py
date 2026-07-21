"""
QQ 客户端封装（基于 OneBot 协议）

启动反向 WebSocket 服务器，等待 NapCat/LLOneBot/go-cqhttp 等 OneBot 实现
作为 WS 客户端连接。与 AstrBot 的 aiocqhttp 反向 WS 模式一致。
"""

import asyncio
import json
import re
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

import websockets
from websockets.exceptions import ConnectionClosed

from .qq_connection import QQConnectionBase


class QQClient(QQConnectionBase):
    """OneBot 协议客户端（反向 WebSocket 服务器）"""

    def __init__(self, *, onebot_url: str, token: str = "", logger: Any = None, emit_log: Any = None, message_queue_size: int = 100):
        self._onebot_url = str(onebot_url or "").strip()
        self.token = str(token or "")
        self.logger = logger
        self._emit_log = emit_log or (lambda level, msg: None)

        # 从 onebot_url 解析监听地址
        self._listen_host = "0.0.0.0"
        self._listen_port = 6199
        parsed = urlparse(self._onebot_url) if self._onebot_url else None
        if parsed and parsed.hostname:
            self._listen_host = parsed.hostname
            if parsed.port:
                self._listen_port = parsed.port

        self._server: Optional[websockets.WebSocketServer] = None
        self._connected_clients: set[websockets.WebSocketServerProtocol] = set()
        self._main_client: Optional[websockets.WebSocketServerProtocol] = None  # 最新的连接，用于发 API 调用
        self._receive_task: Optional[asyncio.Task] = None
        self._message_queue_maxsize = max(1, int(message_queue_size or 100))
        self._message_queue: asyncio.Queue = None  # lazy init in connect()
        self._pending_actions: Dict[str, asyncio.Future] = {}
        self._closing = False
        self._sent_message_ids: Dict[str, float] = {}  # message_id → sent_at timestamp
        self._self_id: str = ""
        self._self_nickname: str = ""

    @property
    def onebot_url(self) -> str:
        return self._onebot_url

    @onebot_url.setter
    def onebot_url(self, value: str) -> None:
        self._onebot_url = str(value or "").strip()
        self._listen_host = "0.0.0.0"
        self._listen_port = 6199
        parsed = urlparse(self._onebot_url) if self._onebot_url else None
        if parsed and parsed.hostname:
            self._listen_host = parsed.hostname
        if parsed and parsed.port:
            self._listen_port = parsed.port

    def is_connected(self) -> bool:
        # 清理已断开的连接
        dead = {c for c in self._connected_clients if getattr(c, 'close_code', None) is not None}
        self._connected_clients -= dead
        if self._main_client in dead:
            self._main_client = next(iter(self._connected_clients), None)
        return len(self._connected_clients) > 0

    async def get_login_status(self) -> dict[str, Any]:
        if self._connected_clients and self._self_id:
            return {"status": "online", "self_id": self._self_id, "nickname": self._self_nickname or None}
        return {"status": "offline", "self_id": None, "nickname": None}

    @staticmethod
    def _looks_like_path(value: str) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        if text.startswith("file://"):
            return True
        if re.match(r"^[A-Za-z]:[\\/]", text):
            return True
        return text.startswith("/")

    @classmethod
    def _build_image_attachment(cls, segment: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(segment, dict) or segment.get("type") != "image":
            return None
        data = segment.get("data")
        if not isinstance(data, dict):
            return None
        raw_url = str(data.get("url") or "").strip()
        raw_path = str(data.get("path") or "").strip()
        raw_file = str(data.get("file") or "").strip()
        locator_type = ""
        locator_value = ""
        if raw_url:
            locator_type = "url"
            locator_value = raw_url
        elif raw_path:
            locator_type = "path"
            locator_value = raw_path
        elif raw_file:
            locator_type = "path" if cls._looks_like_path(raw_file) else "file"
            locator_value = raw_file
        if not locator_value:
            return None
        attachment = {
            "type": "image_url",
            "url": locator_value,
            "locator_type": locator_type,
            "source": "onebot:image",
        }
        if raw_path:
            attachment["path"] = raw_path
        if raw_file:
            attachment["file"] = raw_file
        return attachment

    @classmethod
    def _extract_attachments(cls, raw_msg: Dict[str, Any]) -> list[Dict[str, Any]]:
        segments = raw_msg.get("message")
        if not isinstance(segments, list):
            return []
        attachments: list[Dict[str, Any]] = []
        for segment in segments:
            attachment = cls._build_image_attachment(segment)
            if attachment:
                attachments.append(attachment)
            elif isinstance(segment, dict) and segment.get("type") == "record":
                data = segment.get("data")
                if isinstance(data, dict):
                    file_id = str(data.get("file") or "").strip()
                    if file_id:
                        attachments.append({"type": "record", "file": file_id})
        return attachments

    @classmethod
    def _extract_interaction_context(cls, raw_msg: Dict[str, Any]) -> Dict[str, Any]:
        segments = raw_msg.get("message")
        self_id = str(raw_msg.get("self_id") or "").strip()
        if not isinstance(segments, list):
            return {
                "quoted_message_id": "",
                "mentioned_user_ids": [],
                "mentions_other_user": False,
                "mentions_all": False,
                "mentions_bot": False,
            }

        quoted_message_id = ""
        mentioned_user_ids: list[str] = []
        mentions_other_user = False
        mentions_all = False
        mentions_bot = False

        for segment in segments:
            if not isinstance(segment, dict):
                continue
            segment_type = str(segment.get("type") or "").strip()
            data = segment.get("data")
            if not isinstance(data, dict):
                continue
            if segment_type == "reply":
                quoted_message_id = str(data.get("id") or data.get("message_id") or quoted_message_id).strip()
                continue
            if segment_type != "at":
                continue
            mentioned_id = str(data.get("qq") or "").strip()
            if not mentioned_id:
                continue
            if mentioned_id == "all":
                mentions_all = True
                continue
            mentioned_user_ids.append(mentioned_id)
            if self_id and mentioned_id == self_id:
                mentions_bot = True
            else:
                mentions_other_user = True

        return {
            "quoted_message_id": quoted_message_id,
            "mentioned_user_ids": mentioned_user_ids,
            "mentions_other_user": mentions_other_user,
            "mentions_all": mentions_all,
            "mentions_bot": mentions_bot,
        }

    # ── 反向 WebSocket 服务器 ─────────────────────────────────

    async def connect(self):
        """启动反向 WebSocket 服务器，等待 OneBot 客户端连接"""
        if self._server is not None:
            return
        self._closing = False
        # 在当前 event loop 中重新创建队列（避免跨 loop 绑定错误）
        self._message_queue = asyncio.Queue(maxsize=self._message_queue_maxsize)
        self._server = await websockets.serve(
            self._handle_client,
            host=self._listen_host,
            port=self._listen_port,
        )
        if self.logger:
            self.logger.info(f"Reverse WS server listening on {self._listen_host}:{self._listen_port}")

    async def disconnect(self):
        """关闭服务器和所有客户端连接"""
        self._closing = True

        # 取消所有待处理请求
        for future in list(self._pending_actions.values()):
            if not future.done():
                future.cancel()
        self._pending_actions.clear()

        # 关闭所有已连接的客户端
        for client in list(self._connected_clients):
            try:
                await client.close()
            except Exception:
                pass
        self._connected_clients.clear()
        self._main_client = None

        # 停止服务器
        if self._server:
            try:
                self._server.close()
            except RuntimeError:
                pass  # event loop already closed
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

        if self.logger:
            self.logger.info("Reverse WS server stopped")

    # ── 客户端连接处理 ────────────────────────────────────────

    def _check_token(self, websocket: websockets.WebSocketServerProtocol) -> bool:
        """验证客户端 token，如果未配置 token 则允许所有连接"""
        if not self.token:
            return True

        # 从 query string 提取 access_token
        try:
            request_path = websocket.request.path if websocket.request else "/"
        except Exception:
            request_path = "/"
        parsed = urlparse(request_path)
        params = parse_qs(parsed.query)
        access_token = params.get("access_token", [None])[0]
        if access_token == self.token:
            return True

        # 从 Authorization header 提取 Bearer token
        try:
            auth_header = websocket.request.headers.get("Authorization", "") if websocket.request else ""
        except Exception:
            auth_header = ""
        if auth_header.startswith("Bearer ") and auth_header[7:] == self.token:
            return True

        return False

    async def _handle_client(self, websocket: websockets.WebSocketServerProtocol):
        """处理一个 Napcat 客户端连接"""
        # Token 鉴权
        if not self._check_token(websocket):
            if self.logger:
                addr = websocket.remote_address if hasattr(websocket, 'remote_address') else "unknown"
                self.logger.warning(f"Rejected unauthorized client from {addr}")
            await websocket.close(1008, "Unauthorized")
            return

        # 注册客户端
        self._connected_clients.add(websocket)
        self._main_client = websocket
        if self.logger:
            addr = websocket.remote_address if hasattr(websocket, 'remote_address') else "unknown"
            self.logger.info(f"Napcat client connected from {addr}")
            self._emit_log("INFO", "Napcat 客户端已连接，正在获取账号信息...")

        # 首次连接时异步获取登录信息（不阻塞消息循环）
        if not self._self_id:
            import asyncio
            asyncio.create_task(self._fetch_login_info_async())

        try:
            async for raw_message in websocket:
                try:
                    await self._process_incoming(raw_message)
                except Exception:
                    if self.logger:
                        self.logger.exception("Error processing incoming message")
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            if self.logger and not self._closing:
                self.logger.exception("Unexpected error in client handler")
        finally:
            self._connected_clients.discard(websocket)
            if self._main_client is websocket:
                # 回退到下一个可用连接
                self._main_client = next(iter(self._connected_clients), None)
            if self.logger:
                addr = websocket.remote_address if hasattr(websocket, 'remote_address') else "unknown"
                self.logger.info(f"Napcat client disconnected from {addr}")

    # ── 消息处理 ──────────────────────────────────────────────

    def _transcribe_record_segments(self, message: Dict[str, Any]) -> None:
        """提取语音段信息。转录需要 STT 端点确认后再接。"""
        segments = message.get("message")
        if not isinstance(segments, list):
            return
        record_files = []
        for seg in segments:
            if isinstance(seg, dict) and seg.get("type") == "record":
                f = str(seg.get("data", {}).get("file") or "").strip()
                if f:
                    record_files.append(f)
        if record_files:
            message["_record_files"] = record_files
            if self.logger:
                self.logger.info(f"检测到 {len(record_files)} 条语音 (file_id={record_files})")

    @staticmethod
    def _expand_forward_segments(message: Dict[str, Any]) -> None:
        """展开消息中的转发段（forward/合并转发），提取嵌套消息文本。

        将转发消息中的每条子消息格式化为 "[转发] 发送者: 内容" 追加到 raw_message。
        """
        segments = message.get("message")
        if not isinstance(segments, list):
            return
        forward_texts: list[str] = []
        for seg in segments:
            if not isinstance(seg, dict) or seg.get("type") != "forward":
                continue
            data = seg.get("data") or {}
            # 某些 OneBot 实现直接展开子消息在 data.messages 里
            sub_msgs = data.get("messages") or []
            if not isinstance(sub_msgs, list):
                continue
            for sub in sub_msgs:
                if not isinstance(sub, dict):
                    continue
                sender = sub.get("sender", {})
                sender_name = (sender.get("card") or sender.get("nickname") or str(sub.get("user_id") or "")).strip()
                sub_segments = sub.get("message") or []
                sub_text = ""
                if isinstance(sub_segments, list):
                    for s in sub_segments:
                        if isinstance(s, dict) and s.get("type") == "text":
                            sub_text += str(s.get("data", {}).get("text", ""))
                        elif isinstance(s, dict) and s.get("type") == "image":
                            sub_text += "[图片]"
                        elif isinstance(s, dict) and s.get("type") == "face":
                            sub_text += "[表情]"
                elif isinstance(sub_segments, str):
                    sub_text = sub_segments
                if sub_text.strip():
                    forward_texts.append(f"[转发] {sender_name}: {sub_text.strip()}")
        if forward_texts:
            raw = str(message.get("raw_message") or "").strip()
            expanded = "\n".join(forward_texts)
            message["raw_message"] = f"{raw}\n{expanded}" if raw else expanded
            if not message.get("content"):
                message["content"] = message["raw_message"]
            # 转发子条数计入缓冲计数
            message["_forward_sub_count"] = len(forward_texts)

    async def _process_incoming(self, raw_message: str):
        """处理一条来自 OneBot 客户端的消息"""
        message = json.loads(raw_message)

        # echo 匹配：这是对之前 call_action 的响应
        echo = message.get("echo")
        if echo and echo in self._pending_actions:
            future = self._pending_actions.pop(str(echo), None)
            if future and not future.done():
                future.set_result(message)
            return

        # 事件路由
        if message.get("post_type") == "message":
            msg_type = message.get("message_type")
            if msg_type in {"private", "group"}:
                # 检查转发消息段
                self._expand_forward_segments(message)
                # 检查语音消息段 → 异步转文字
                self._transcribe_record_segments(message)
                if not self._message_queue:
                    return
                try:
                    self._message_queue.put_nowait(message)
                except asyncio.QueueFull:
                    if self.logger:
                        self.logger.warning("Message queue full; dropping oldest message")
                    _ = self._message_queue.get_nowait()
                    self._message_queue.put_nowait(message)
                if self.logger:
                    if msg_type == "private":
                        self.logger.info(f"Queued private message from {message.get('user_id')}")
                    else:
                        self.logger.info(f"Queued group message from group {message.get('group_id')}, user {message.get('user_id')}")
        elif message.get("post_type") == "notice" and message.get("notice_type") == "notify" and message.get("sub_type") == "poke":
            # 戳一戳事件：入队以便自动回戳
            if not self._message_queue:
                return
            try:
                self._message_queue.put_nowait(message)
            except asyncio.QueueFull:
                pass
            if self.logger:
                self.logger.info(f"Queued poke notice: group {message.get('group_id')}, target {message.get('target_id')}, user {message.get('user_id')}")

    async def receive_message(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """接收一条消息，返回标准化格式"""
        if not self._message_queue:
            return None
        try:
            raw_msg = await asyncio.wait_for(self._message_queue.get(), timeout=timeout)

            # 追踪自身 QQ 号
            self_id = raw_msg.get("self_id")
            if self_id:
                self._self_id = str(self_id)

            # 戳一戳通知事件
            if raw_msg.get("post_type") == "notice":
                return {
                    "message_type": "notice",
                    "notice_type": raw_msg.get("sub_type", ""),
                    "user_id": str(raw_msg.get("user_id") or ""),
                    "group_id": str(raw_msg.get("group_id") or ""),
                    "target_id": str(raw_msg.get("target_id") or ""),
                    "timestamp": raw_msg.get("time"),
                    "raw": raw_msg,
                }

            msg_type = raw_msg.get("message_type")
            sender_info = raw_msg.get("sender", {})
            user_nickname = sender_info.get("nickname") or sender_info.get("card") or None

            # 语音消息标注
            content = str(raw_msg.get("raw_message") or "")
            has_record = any(
                isinstance(s, dict) and s.get("type") == "record"
                for s in (raw_msg.get("message") or [])
                if isinstance(s, dict)
            )
            if has_record and not content.strip():
                content = "[语音]"
            elif has_record:
                content = f"[语音] {content}"

            result = {
                "message_type": msg_type,
                "user_id": str(raw_msg.get("user_id")),
                "user_nickname": user_nickname,
                "content": content,
                "message_id": raw_msg.get("message_id"),
                "timestamp": raw_msg.get("time"),
                "raw": raw_msg,
                "attachments": self._extract_attachments(raw_msg),
            }

            if msg_type == "group":
                interaction_context = self._extract_interaction_context(raw_msg)
                result["group_id"] = str(raw_msg.get("group_id"))
                result["quoted_message_id"] = interaction_context["quoted_message_id"]
                result["mentioned_user_ids"] = interaction_context["mentioned_user_ids"]
                result["mentions_other_user"] = interaction_context["mentions_other_user"]
                result["mentions_all"] = interaction_context["mentions_all"]
                is_reply_to_bot = self._is_reply_to_bot_message(
                    interaction_context["quoted_message_id"],
                )
                result["is_at_bot"] = (
                    interaction_context["mentions_bot"]
                    or interaction_context["mentions_all"]
                    or is_reply_to_bot
                )
                result["is_reply_to_bot"] = is_reply_to_bot

            return result
        except asyncio.TimeoutError:
            return None

    def _check_at_bot(self, raw_msg: Dict[str, Any]) -> bool:
        """检查消息是否 @ 了机器人"""
        message = raw_msg.get("message", [])
        if isinstance(message, list):
            for seg in message:
                if seg.get("type") == "at":
                    at_qq = seg.get("data", {}).get("qq")
                    if at_qq == "all":
                        return True
                    if str(at_qq) == str(raw_msg.get("self_id")):
                        return True
        return False

    _SENT_MSG_TTL_SECONDS = 3600  # 已发送消息 ID 缓存 1 小时

    def record_sent_message_id(self, message_id: str) -> None:
        """记录已发送的消息 ID（用于检测回复是否是回给 bot 的）"""
        mid = str(message_id or "").strip()
        if mid:
            import time
            self._sent_message_ids[mid] = time.time()
            self._cleanup_sent_message_cache()

    def _is_reply_to_bot_message(self, quoted_message_id: str) -> bool:
        """检查被引用的消息是否是 bot 发送的"""
        qid = str(quoted_message_id or "").strip()
        if not qid:
            return False
        return qid in self._sent_message_ids

    def _cleanup_sent_message_cache(self) -> None:
        """清理过期的已发送消息 ID"""
        import time
        now = time.time()
        expired = [
            mid for mid, ts in self._sent_message_ids.items()
            if now - ts > self._SENT_MSG_TTL_SECONDS
        ]
        for mid in expired:
            del self._sent_message_ids[mid]

    # ── OneBot API 调用 ────────────────────────────────────────

    async def call_action(self, action: str, params: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Dict[str, Any]:
        if not self._main_client:
            raise RuntimeError("No Napcat client connected")
        # ServerConnection 没有 .open，用 close_code 判断
        if getattr(self._main_client, 'close_code', None) is not None:
            self._connected_clients.discard(self._main_client)
            self._main_client = next(iter(self._connected_clients), None)
            if not self._main_client:
                raise RuntimeError("No Napcat client connected")

        echo = secrets.token_hex(8)
        future = asyncio.get_running_loop().create_future()
        self._pending_actions[echo] = future
        payload = {
            "action": action,
            "params": params or {},
            "echo": echo,
        }
        try:
            await self._main_client.send(json.dumps(payload))
            if self.logger:
                self.logger.info(f"call_action sent: {action} echo={echo}")
        except Exception:
            self._pending_actions.pop(echo, None)
            if not future.done():
                future.cancel()
            raise
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            if self.logger:
                self.logger.info(f"call_action response: {action} status={response.get('status')}")
            if response.get("status") == "failed":
                raise RuntimeError(response.get("wording") or f"OneBot action failed: {action}")
            return response.get("data") or {}
        except asyncio.TimeoutError:
            self._emit_log("ERROR", f"call_action 超时: {action} (10秒未响应)")
            if self.logger:
                self.logger.warning(f"call_action timeout: {action} echo={echo}")
            raise
        finally:
            self._pending_actions.pop(echo, None)

    async def _fetch_login_info_async(self) -> None:
        """后台任务：获取登录信息并缓存（不阻塞消息处理）。"""
        try:
            await asyncio.sleep(0.5)  # 等连接稳定
            self._emit_log("INFO", "正在请求 get_login_info...")
            await self.get_login_info()
        except Exception as e:
            self._emit_log("ERROR", f"获取账号信息失败: {e}")
            if self.logger:
                self.logger.warning(f"Background login info fetch failed: {e}")

    async def get_login_info(self) -> Dict[str, Any]:
        data = await self.call_action("get_login_info", timeout=5.0)
        uid = str(data.get("user_id") or "").strip()
        nick = str(data.get("nickname") or "").strip()
        if uid:
            self._self_id = uid
        if nick:
            self._self_nickname = nick
        self._emit_log("INFO", f"账号信息: QQ={uid or '?'} 昵称={nick or '?'}")
        if self.logger:
            self.logger.info(f"get_login_info: self_id={uid}, nickname={nick}")
        return data

    async def get_friend_list(self) -> list[Dict[str, Any]]:
        data = await self.call_action("get_friend_list", timeout=10.0)
        return data if isinstance(data, list) else []

    async def get_group_list(self) -> list[Dict[str, Any]]:
        data = await self.call_action("get_group_list", timeout=10.0)
        return data if isinstance(data, list) else []

    async def send_message(self, user_id: str, message: str):
        """发送私聊消息"""
        if not self._main_client:
            raise RuntimeError("No Napcat client connected")

        payload = {
            "action": "send_private_msg",
            "params": {
                "user_id": int(user_id),
                "message": message,
            },
        }

        await self._main_client.send(json.dumps(payload))
        if self.logger:
            self.logger.debug(f"Sent message to {user_id}")

    async def send_group_message(self, group_id: str, message: str):
        """发送群聊消息"""
        if not self._main_client:
            raise RuntimeError("No Napcat client connected")

        payload = {
            "action": "send_group_msg",
            "params": {
                "group_id": int(group_id),
                "message": message,
            },
        }

        await self._main_client.send(json.dumps(payload))
        if self.logger:
            self.logger.debug(f"Sent group message to {group_id}")

    async def send_private_message_segments(self, user_id: str, segments: list[Dict[str, Any]]):
        """发送私聊消息片段"""
        if not self._main_client:
            raise RuntimeError("No Napcat client connected")

        payload = {
            "action": "send_private_msg",
            "params": {
                "user_id": int(user_id),
                "message": segments,
            },
        }

        await self._main_client.send(json.dumps(payload))
        if self.logger:
            self.logger.debug(f"Sent segmented private message to {user_id}")

    async def send_private_record(self, user_id: str, file_uri: str):
        """发送私聊语音"""
        await self.send_private_message_segments(user_id, [{"type": "record", "data": {"file": str(file_uri or "")}}])

    async def send_group_record(self, group_id: str, file_uri: str, *, reply_message_id: str = "", at_user_id: str = ""):
        """发送群聊语音"""
        segments: list[Dict[str, Any]] = []
        if str(reply_message_id or "").strip():
            segments.append({"type": "reply", "data": {"id": str(reply_message_id)}})
        if str(at_user_id or "").strip():
            segments.append({"type": "at", "data": {"qq": str(at_user_id)}})
        segments.append({"type": "record", "data": {"file": str(file_uri or "")}})
        await self.send_group_message_segments(group_id, segments)

    async def send_group_message_segments(self, group_id: str, segments: list[Dict[str, Any]], *, record_sent: bool = True, keyboard: str = "") -> Optional[str]:
        """发送群聊消息片段，返回 message_id"""
        if not self._main_client:
            raise RuntimeError("No Napcat client connected")

        echo = secrets.token_hex(8)
        payload = {
            "action": "send_group_msg",
            "params": {
                "group_id": int(group_id),
                "message": segments,
            },
            "echo": echo,
        }

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_actions[echo] = future
        try:
            await self._main_client.send(json.dumps(payload))
            response = await asyncio.wait_for(future, timeout=10.0)
            message_id = str((response.get("data") or {}).get("message_id") or "")
            if message_id and record_sent:
                self.record_sent_message_id(message_id)
            return message_id if message_id else None
        except asyncio.TimeoutError:
            return None
        except Exception:
            raise
        finally:
            self._pending_actions.pop(echo, None)
        if self.logger:
            self.logger.debug(f"Sent segmented group message to {group_id}")

    async def send_group_poke(self, group_id: str, user_id: str) -> bool:
        """发送群聊戳一戳"""
        if not self._main_client:
            raise RuntimeError("No Napcat client connected")
        try:
            payload = {
                "action": "send_poke",
                "params": {
                    "group_id": int(group_id),
                    "user_id": int(user_id),
                },
            }
            await self._main_client.send(json.dumps(payload))
            if self.logger:
                self.logger.info(f"Sent poke to user {user_id} in group {group_id}")
            return True
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to send poke: {e}")
            return False

    async def get_record(self, file_id: str, output_format: str = "mp3") -> dict[str, Any]:
        """获取语音文件（返回 base64 编码的音频数据）。"""
        return await self.call_action("get_record", {"file": file_id, "out_format": output_format}, timeout=15.0)

    async def send_group_image(self, group_id: str, image_data: str, *, reply_message_id: str = "", at_user_id: str = "") -> Optional[str]:
        """发送群聊图片

        Args:
            group_id: 群号
            image_data: 图片 URL、base64 字符串（带 base64:// 前缀）或本地文件路径
        """
        if not self._main_client:
            raise RuntimeError("No Napcat client connected")
        segments: list[Dict[str, Any]] = []
        if str(reply_message_id or "").strip():
            segments.append({"type": "reply", "data": {"id": str(reply_message_id)}})
        if str(at_user_id or "").strip():
            segments.append({"type": "at", "data": {"qq": str(at_user_id)}})
        segments.append({"type": "image", "data": {"file": str(image_data)}})
        return await self.send_group_message_segments(group_id, segments, record_sent=False)
