"""
QQ 客户端封装（基于 OneBot 协议）

支持通过 WebSocket 连接到 OneBot 实现（如 NapCat、LLOneBot、go-cqhttp）
"""

import asyncio
import json
from typing import Any, Dict, Optional
import websockets


class QQClient:
    """OneBot 协议客户端"""

    def __init__(self, onebot_url: str, token: Optional[str] = None, logger=None):
        self.onebot_url = onebot_url
        self.token = token
        self.logger = logger
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._receive_task: Optional[asyncio.Task] = None

    async def connect(self):
        """连接到 OneBot 服务"""
        try:
            # 构建 WebSocket URL
            url = self.onebot_url
            headers = {}

            # 尝试多种 token 认证方式
            if self.token:
                # 方式1: 作为 URL 参数
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}access_token={self.token}"

                # 方式2: 同时添加到 headers
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
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self.ws:
            await self.ws.close()
            self.ws = None

        if self.logger:
            self.logger.info("Disconnected from OneBot")

    async def _receive_loop(self):
        """接收消息循环（含断线重连）"""
        retry_delay = 1.0
        while True:
            if not self.ws:
                break
            try:
                raw_message = await self.ws.recv()
                retry_delay = 1.0  # 收到消息说明连接正常，重置重试间隔

                message = json.loads(raw_message)

                # 调试：打印所有收到的消息
                if self.logger:
                    self.logger.debug(f"Received raw message: {message}")

                # 过滤出私聊消息和群聊消息
                if message.get("post_type") == "message":
                    msg_type = message.get("message_type")
                    if msg_type == "private":
                        await self._message_queue.put(message)
                        if self.logger:
                            self.logger.info(f"Queued private message from {message.get('user_id')}")
                    elif msg_type == "group":
                        await self._message_queue.put(message)
                        if self.logger:
                            self.logger.info(f"Queued group message from group {message.get('group_id')}, user {message.get('user_id')}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"WebSocket disconnected: {e}, reconnecting in {retry_delay:.0f}s...")
                self.ws = None
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)  # 指数退避，最长30秒
                try:
                    await self.connect()
                except Exception as ce:
                    if self.logger:
                        self.logger.error(f"Reconnect failed: {ce}")

    async def receive_message(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """接收一条消息，返回标准化格式"""
        try:
            raw_msg = await asyncio.wait_for(self._message_queue.get(), timeout=timeout)

            # 标准化消息格式
            msg_type = raw_msg.get("message_type")

            # 提取用户昵称（OneBot 标准）
            sender_info = raw_msg.get("sender", {})
            user_nickname = sender_info.get("nickname") or sender_info.get("card") or None

            result = {
                "message_type": msg_type,  # "private" 或 "group"
                "user_id": str(raw_msg.get("user_id")),
                "user_nickname": user_nickname,  # QQ 昵称
                "content": raw_msg.get("raw_message", ""),
                "message_id": raw_msg.get("message_id"),
                "timestamp": raw_msg.get("time"),
                "raw": raw_msg,
            }

            # 群聊消息额外字段
            if msg_type == "group":
                result["group_id"] = str(raw_msg.get("group_id"))
                # 检查是否 @ 了机器人
                result["is_at_bot"] = self._check_at_bot(raw_msg)

            return result
        except asyncio.TimeoutError:
            return None

    def _check_at_bot(self, raw_msg: Dict[str, Any]) -> bool:
        """检查消息是否 @ 了机器人"""
        # OneBot 标准：message 字段包含 CQ 码
        message = raw_msg.get("message", [])
        if isinstance(message, list):
            for seg in message:
                if seg.get("type") == "at":
                    # 检查是否 @ 了机器人（qq 字段为 "all" 或机器人自己的 QQ）
                    at_qq = seg.get("data", {}).get("qq")
                    if at_qq == "all":
                        return True
                    # 可以通过 self_id 判断是否 @ 了自己
                    if str(at_qq) == str(raw_msg.get("self_id")):
                        return True
        return False

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
            self.logger.debug(f"Sent message to {user_id}: {message}")

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
            self.logger.debug(f"Sent group message to {group_id}: {message}")
