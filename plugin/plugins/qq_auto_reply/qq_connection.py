"""QQ 连接抽象基类 — 统一 NapCat (OneBot) 和 QQ 开放平台两种接入方式"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class QQConnectionBase(ABC):
    """QQ 连接抽象基类

    所有 QQ 接入方式（NapCat OneBot、QQ 开放平台）都实现此接口，
    向上层（message_dispatcher、pipeline 等）输出统一的内部消息格式。
    """

    # 内部消息格式字段说明（所有子类的 receive_message() 必须返回此格式）:
    # {
    #     "message_type": "group" | "private",
    #     "user_id": str,
    #     "user_nickname": str | None,
    #     "content": str,
    #     "message_id": str,
    #     "timestamp": int,
    #     "is_at_bot": bool,
    #     "is_reply_to_bot": bool,
    #     "group_id": str,             # 仅群聊
    #     "quoted_message_id": str,
    #     "mentioned_user_ids": [str],
    #     "mentions_other_user": bool,
    #     "mentions_all": bool,
    #     "raw": dict,
    #     "attachments": [dict],
    # }

    @abstractmethod
    async def connect(self) -> None:
        """建立连接（WebSocket + 鉴权 + 心跳）"""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接，清理资源"""
        ...

    @abstractmethod
    async def receive_message(self, timeout: float = 1.0) -> Optional[dict[str, Any]]:
        """阻塞接收一条消息，返回标准化格式 dict，超时返回 None"""
        ...

    @abstractmethod
    async def send_group_message_segments(
        self, group_id: str, segments: list[dict[str, Any]], *, record_sent: bool = True
    ) -> Optional[str]:
        """发送群聊消息（平台原生格式），返回 message_id"""
        ...

    @abstractmethod
    async def send_private_message_segments(
        self, user_id: str, segments: list[dict[str, Any]]
    ) -> Optional[str]:
        """发送私聊消息（平台原生格式），返回 message_id"""
        ...

    @abstractmethod
    async def send_group_poke(self, group_id: str, user_id: str) -> bool:
        """发送群聊戳一戳，返回是否成功"""
        ...

    @abstractmethod
    async def send_group_image(
        self, group_id: str, image_data: str, *, reply_message_id: str = "", at_user_id: str = ""
    ) -> Optional[str]:
        """发送群聊图片"""
        ...

    @abstractmethod
    async def send_group_record(
        self, group_id: str, file_uri: str, *, reply_message_id: str = "", at_user_id: str = ""
    ) -> None:
        """发送群聊语音"""
        ...

    @abstractmethod
    async def get_login_status(self) -> dict[str, Any]:
        """返回登录状态: {"status": "online"|"offline", "self_id": str|None, "nickname": str|None}"""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """是否已连接"""
        ...

    @abstractmethod
    def record_sent_message_id(self, message_id: str) -> None:
        """记录已发送的消息 ID（供 is_reply_to_bot 检测用）"""
        ...

    token: str = ""  # 访问令牌（兼容 settings_service 的直接属性访问）

    @property
    def needs_attention(self) -> bool:
        """是否需要注意力机制（NapCat 需要，开放平台不需要）"""
        return True

    @property
    def supports_voice(self) -> bool:
        """是否支持语音回复"""
        return True

    @property
    def supports_poke(self) -> bool:
        """是否支持戳一戳"""
        return True

    @property
    def receives_all_messages(self) -> bool:
        """是否接收群聊全部消息（开放平台仅 @bot）"""
        return True

    @property
    @abstractmethod
    def onebot_url(self) -> str:
        """反向 WebSocket 监听地址（NapCat 作为 WS Client 连接到此地址）"""
        ...

    @onebot_url.setter
    @abstractmethod
    def onebot_url(self, value: str) -> None:
        ...
