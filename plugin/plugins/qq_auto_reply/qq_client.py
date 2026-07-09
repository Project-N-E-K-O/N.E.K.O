"""
QQ 客户端封装（基于 OneBot 协议）

支持通过 WebSocket 连接到 OneBot 实现（如 NapCat、LLOneBot、go-cqhttp）
"""

import asyncio
import json
import re
import secrets
import time
from typing import Any, Dict, Optional
import websockets

from .qq_connection import QQConnectionBase


class QQClient(QQConnectionBase):
    """OneBot 协议客户端"""

    def __init__(self, *, onebot_url: str, token: str = "", logger: Any = None, message_queue_size: int = 100):
        self._onebot_url = str(onebot_url or "").strip()
        self.token = str(token or "")
        self.logger = logger
        self.ws = None
        self._receive_task: Optional[asyncio.Task] = None
        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, int(message_queue_size or 100)))
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

    def is_connected(self) -> bool:
        return self.ws is not None

    async def get_login_status(self) -> dict[str, Any]:
        if self.ws and self._self_id:
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


    async def connect(self):
        """连接到 OneBot 服务"""
        try:
            if self._receive_task and not self._receive_task.done():
                return
            self._closing = False

            url = self.onebot_url
            headers = {}

            if self.token:
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}access_token={self.token}"
                headers["Authorization"] = f"Bearer {self.token}"

            self.ws = await websockets.connect(url, additional_headers=headers if headers else None)
            self._receive_task = asyncio.create_task(self._receive_loop())
            if self.logger:
                self.logger.info(f"Connected to OneBot at {self.onebot_url}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to connect to OneBot: {e}")
            raise

    async def disconnect(self):
        """断开连接"""
        self._closing = True
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        for future in list(self._pending_actions.values()):
            if not future.done():
                future.cancel()
        self._pending_actions.clear()

        if self.ws:
            await self.ws.close()
            self.ws = None

        if self.logger:
            self.logger.info("Disconnected from OneBot")

    async def _open_websocket(self):
        url = self.onebot_url
        headers = {}
        if self.token:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}access_token={self.token}"
            headers["Authorization"] = f"Bearer {self.token}"
        self.ws = await websockets.connect(url, additional_headers=headers if headers else None)

    async def _receive_loop(self):
        """接收消息循环（含断线重连）"""
        retry_delay = 1.0
        while not self._closing:
            if not self.ws:
                try:
                    await self._open_websocket()
                    retry_delay = 1.0
                    if self.logger:
                        self.logger.info(f"Reconnected to OneBot at {self.onebot_url}")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Reconnect failed: {e}, retrying in {retry_delay:.0f}s...")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 30.0)
                    continue
            try:
                raw_message = await self.ws.recv()
                retry_delay = 1.0

                message = json.loads(raw_message)

                if self.logger:
                    self.logger.debug(f"Received raw message: {message}")

                echo = message.get("echo")
                if echo and echo in self._pending_actions:
                    future = self._pending_actions.pop(str(echo), None)
                    if future and not future.done():
                        future.set_result(message)
                    continue

                if message.get("post_type") == "message":
                    msg_type = message.get("message_type")
                    if msg_type in {"private", "group"}:
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
                    try:
                        self._message_queue.put_nowait(message)
                    except asyncio.QueueFull:
                        pass
                    if self.logger:
                        self.logger.info(f"Queued poke notice: group {message.get('group_id')}, target {message.get('target_id')}, user {message.get('user_id')}")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self.logger and not self._closing:
                    self.logger.warning(f"WebSocket disconnected: {e}, reconnecting in {retry_delay:.0f}s...")
                if self.ws:
                    try:
                        await self.ws.close()
                    except Exception:
                        pass
                self.ws = None
                if self._closing:
                    break
                for echo, future in list(self._pending_actions.items()):
                    if not future.done():
                        future.set_exception(RuntimeError("WebSocket disconnected before action response"))
                    self._pending_actions.pop(echo, None)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)

    async def receive_message(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """接收一条消息，返回标准化格式"""
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

            result = {
                "message_type": msg_type,
                "user_id": str(raw_msg.get("user_id")),
                "user_nickname": user_nickname,
                "content": raw_msg.get("raw_message", ""),
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

    async def call_action(self, action: str, params: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Dict[str, Any]:
        if not self.ws:
            raise RuntimeError("Not connected to OneBot")

        echo = secrets.token_hex(8)
        future = asyncio.get_running_loop().create_future()
        self._pending_actions[echo] = future
        payload = {
            "action": action,
            "params": params or {},
            "echo": echo,
        }
        try:
            await self.ws.send(json.dumps(payload))
        except Exception:
            self._pending_actions.pop(echo, None)
            if not future.done():
                future.cancel()
            raise
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending_actions.pop(echo, None)
        if response.get("status") == "failed":
            raise RuntimeError(response.get("wording") or f"OneBot action failed: {action}")
        return response.get("data") or {}

    async def get_login_info(self) -> Dict[str, Any]:
        return await self.call_action("get_login_info", timeout=5.0)

    async def get_friend_list(self) -> list[Dict[str, Any]]:
        data = await self.call_action("get_friend_list", timeout=10.0)
        return data if isinstance(data, list) else []

    async def get_group_list(self) -> list[Dict[str, Any]]:
        data = await self.call_action("get_group_list", timeout=10.0)
        return data if isinstance(data, list) else []

    async def send_message(self, user_id: str, message: str):
        """发送私聊消息"""
        if not self.ws:
            raise RuntimeError("Not connected to OneBot")

        payload = {
            "action": "send_private_msg",
            "params": {
                "user_id": int(user_id),
                "message": message,
            },
        }

        await self.ws.send(json.dumps(payload))
        if self.logger:
            self.logger.debug(f"Sent message to {user_id}")

    async def send_group_message(self, group_id: str, message: str):
        """发送群聊消息"""
        if not self.ws:
            raise RuntimeError("Not connected to OneBot")

        payload = {
            "action": "send_group_msg",
            "params": {
                "group_id": int(group_id),
                "message": message,
            },
        }

        await self.ws.send(json.dumps(payload))
        if self.logger:
            self.logger.debug(f"Sent group message to {group_id}")

    async def send_private_message_segments(self, user_id: str, segments: list[Dict[str, Any]]):
        """发送私聊消息片段"""
        if not self.ws:
            raise RuntimeError("Not connected to OneBot")

        payload = {
            "action": "send_private_msg",
            "params": {
                "user_id": int(user_id),
                "message": segments,
            },
        }

        await self.ws.send(json.dumps(payload))
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
        if not self.ws:
            raise RuntimeError("Not connected to OneBot")

        echo = f"send_group_{group_id}_{id(segments)}"
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
        await self.ws.send(json.dumps(payload))

        try:
            response = await asyncio.wait_for(future, timeout=10.0)
            message_id = str((response.get("data") or {}).get("message_id") or "")
            if message_id and record_sent:
                self.record_sent_message_id(message_id)
            self._pending_actions.pop(echo, None)
            return message_id if message_id else None
        except asyncio.TimeoutError:
            self._pending_actions.pop(echo, None)
            return None
        except Exception:
            self._pending_actions.pop(echo, None)
            raise
        if self.logger:
            self.logger.debug(f"Sent segmented group message to {group_id}")

    async def send_group_poke(self, group_id: str, user_id: str) -> bool:
        """发送群聊戳一戳"""
        if not self.ws:
            raise RuntimeError("Not connected to OneBot")
        try:
            payload = {
                "action": "send_poke",
                "params": {
                    "group_id": int(group_id),
                    "user_id": int(user_id),
                },
            }
            await self.ws.send(json.dumps(payload))
            if self.logger:
                self.logger.info(f"Sent poke to user {user_id} in group {group_id}")
            return True
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to send poke: {e}")
            return False

    async def send_group_image(self, group_id: str, image_data: str, *, reply_message_id: str = "", at_user_id: str = "") -> Optional[str]:
        """发送群聊图片

        Args:
            group_id: 群号
            image_data: 图片 URL、base64 字符串（带 base64:// 前缀）或本地文件路径
        """
        if not self.ws:
            raise RuntimeError("Not connected to OneBot")
        segments: list[Dict[str, Any]] = []
        if str(reply_message_id or "").strip():
            segments.append({"type": "reply", "data": {"id": str(reply_message_id)}})
        if str(at_user_id or "").strip():
            segments.append({"type": "at", "data": {"qq": str(at_user_id)}})
        segments.append({"type": "image", "data": {"file": str(image_data)}})
        return await self.send_group_message_segments(group_id, segments, record_sent=False)
