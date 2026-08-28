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
        self, group_id: str, image_data: str, *, reply_message_id: str = "", at_user_id: str = "", sub_type: str = ""
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
    def supports_ark_cards(self) -> bool:
        """是否支持 Ark 富卡片（仅开放平台；OneBot 后端不支持，降级文本）"""
        return False

    def is_group_muted(self, group_id: str) -> bool:
        """检查 bot 是否在该群被禁言（含全体禁言）。

        NapCat 通过 OneBot notice 事件跟踪禁言状态；
        开放平台不跟踪此状态，默认返回 False。
        """
        return False

    @property
    def self_id(self) -> str:
        """bot 自己的 user id（未知时为空串）。``_self_id`` 的公开别名。"""
        return str(getattr(self, "_self_id", "") or "")

    @property
    def sent_message_ids(self) -> dict[str, float]:
        """已发送消息 ID → 发送时间戳。``_sent_message_ids`` 的公开别名。"""
        return getattr(self, "_sent_message_ids", {})

    async def enrich_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """展开引用/转发/语音/文件 + 注入 VLM 图片描述。

        默认 no-op；``QQClient`` 覆写为真实实现（其 ``needs_attention`` 为 True，
        由调用方按需触发）。这一方法在 dispatcher 的 eligibility 过滤之后、
        落 backlog / 复检黑名单之前调用，返回（可能被改写的）消息 dict。
        """
        return message

    async def send_group_ark_card(
        self, group_id: str, ark_obj: dict[str, Any], **_: Any
    ) -> bool:
        """发送群聊 Ark 富卡片。仅开放平台支持（``supports_ark_cards`` 为
        True）；OneBot 后端在调用前已被 ``supports_ark_cards`` 拦截降级，这里
        直接抛 NotImplementedError 作兜底。"""
        raise NotImplementedError("Ark 富卡片仅开放平台通道支持")

    # ── 入站消息广播钩子（适配器 → 订阅者）──────────────────────
    # 任何插件都可用 ``set_inbound_sink`` 挂一个接收器：连接层每收到一条规范化
    # 消息就调它一次。qq_auto_reply 用它把入站 QQ 消息推给其它插件；其它插件
    # 若自持连接，也可直接挂自己的 sink 接收。绝对不阻塞消息管线（尽力而为）。
    _INBOUND_SINK_ATTR = "_inbound_sink"

    @property
    def inbound_sink(self) -> Any | None:
        """已注册的入站消息接收器（None = 未注册）。"""
        return getattr(self, self._INBOUND_SINK_ATTR, None)

    def set_inbound_sink(self, sink: Any | None) -> None:
        """注册入站消息接收器 ``async sink(message: dict) -> None``。

        每次 ``receive_message()`` 产出一条规范化消息后，连接层会调用它并吞掉
        一切异常（广播是尽力而为，绝不能拖垮消息管线）。传 ``None`` 取消。
        """
        setattr(self, self._INBOUND_SINK_ATTR, sink)

    async def _dispatch_inbound(self, message: dict[str, Any]) -> None:
        """内部：把一条入站消息交给已注册的 sink（fire-and-forget，容错）。"""
        sink = self.inbound_sink
        if sink is None:
            return
        try:
            result = sink(message)
            if hasattr(result, "__await__"):
                await result
        except Exception:
            pass

    @property
    @abstractmethod
    def onebot_url(self) -> str:
        """反向 WebSocket 监听地址（NapCat 作为 WS Client 连接到此地址）"""
        ...

    @onebot_url.setter
    @abstractmethod
    def onebot_url(self, value: str) -> None:
        ...
