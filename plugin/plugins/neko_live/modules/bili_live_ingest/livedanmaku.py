"""
MagicalDanmaku LiveDanmaku 数据模型（Python 移植版）

功能：
- MessageType 枚举（15种 B站消息类型）
- LiveDanmaku dataclass（30+ 字段）
- 工厂方法：从各种 B站 WS 消息体解析
- get_score() 打分（guard > admin > medal > user level > text length）
- to_dict() 序列化
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


# ── 消息类型枚举 ─────────────────────────────────────────────────


class MessageType(IntEnum):
    """B站直播 WS 消息类型"""
    MSG_DANMAKU = 1
    MSG_GIFT = 2
    MSG_WELCOME = 3
    MSG_GUARD_BUY = 5
    MSG_WELCOME_GUARD = 6
    MSG_FANS = 7
    MSG_ATTENTION = 8
    MSG_BLOCK = 9
    MSG_MSG = 10
    MSG_SHARE = 11
    MSG_SUPER_CHAT = 13
    MSG_EXTRA = 14


# ── 嵌套数据类 ──────────────────────────────────────────────────


@dataclass
class MedalInfo:
    """粉丝牌信息"""
    name: str = ""
    level: int = 0
    up_name: str = ""           # 主播名称（牌子的主播）
    color: int = 0
    anchor_roomid: int = 0


@dataclass
class GiftInfo:
    """礼物信息"""
    gift_id: int = 0
    gift_name: str = ""
    num: int = 1
    coin_type: str = "silver"   # silver/gold
    total_coin: int = 0
    price: int = 0               # 单价（金瓜子）


@dataclass
class UserInfo:
    """用户信息"""
    uid: int = 0
    nickname: str = ""
    face_url: str = ""
    user_level: int = 0
    admin: bool = False          # 是否房管
    guard_level: int = 0         # 0=无, 1=总督, 2=提督, 3=舰长
    vip: bool = False
    svip: bool = False


# ── LiveDanmaku 主类 ────────────────────────────────────────────


@dataclass
class LiveDanmaku:
    """
    LiveDanmaku — 单条 B站直播消息

    覆盖 30+ 字段，包含完整的用户/礼物/粉丝牌/房间信息。
    通过工厂方法从不同 WS 指令解析。
    """
    # 基础字段
    msg_type: MessageType = MessageType.MSG_DANMAKU
    uid: int = 0
    nickname: str = ""
    text: str = ""
    timeline: float = field(default_factory=time.time)
    room_id: int = 0

    # 标记位
    admin: bool = False
    guard_level: int = 0          # 0=无, 1=总督, 2=提督, 3=舰长
    vip: bool = False
    svip: bool = False

    # 粉丝牌
    medal: Optional[MedalInfo] = None

    # 用户等级
    user_level: int = 0

    # 礼物信息
    gift: Optional[GiftInfo] = None

    # Provider-neutral support scheduling metadata (safe scalar values only).
    provider_event_id: str = ""
    provider_timestamp_ms: int = 0
    combo_id: str = ""
    combo_count: int = 0
    combo_end: Optional[bool] = None

    # 粉丝数据
    fans_medal_name: str = ""
    fans_medal_level: int = 0

    # 关注状态
    attention: int = 0            # 0=未关注, 1=已关注

    # 用户头像
    face_url: str = ""

    # 原始 JSON（调试用）
    extra_json: str = ""

    # ── 工厂方法 ─────────────────────────────────────────────

    @classmethod
    def from_danmaku(cls, data: dict) -> "LiveDanmaku":
        """从 DANMU_MSG 解析弹幕消息。

        B站 ``DANMU_MSG.info`` 真实结构（仅取本类用到的字段）：

        - ``info[1]`` 弹幕文本。
        - ``info[2]`` 用户数组 ``[uid, uname, is_admin, is_vip, is_svip, ...]``。
        - ``info[3]`` 粉丝牌数组 ``[level, name, up_name, room_id, color, ...]``（可为空）。
        - ``info[4]`` 用户等级数组 ``[user_level, ...]``。
        - ``info[7]`` 大航海等级（**普通 int**：0 无 / 1 总督 / 2 提督 / 3 舰长）。

        历史 bug：旧实现把 ``info[7]`` 当作可下标的列表（``info[7][3]`` / ``info[7][1]``），
        但它实际是 int —— 任意一条正常弹幕都会在 ``len(info[7])`` 处抛 ``TypeError``，被
        ``_dispatch_message`` 的 ``except`` 吞掉，导致 ``on_event("DANMU_MSG")`` 永不触发
        （计数恒为 0）。同时 ``admin`` 只判了外层 ``len(info) > 2``、未判内层长度，短 ``info[2]``
        会 ``IndexError``。此处按真实结构重写并补齐内层长度 / 类型守卫。
        """
        info = data.get("info", [])
        user_info = info[2] if len(info) > 2 and isinstance(info[2], list) else []
        medal_info = info[3] if len(info) > 3 and isinstance(info[3], list) else []
        user_level_info = info[4] if len(info) > 4 and isinstance(info[4], list) else []

        uid = 0
        nickname = ""
        admin = False
        vip = False
        svip = False
        if len(user_info) >= 2:
            uid = user_info[0]
            nickname = str(user_info[1] or "")
        if len(user_info) > 2:
            admin = bool(user_info[2])
        if len(user_info) > 3:
            vip = bool(user_info[3])
        if len(user_info) > 4:
            svip = bool(user_info[4])

        text = str(info[1]) if len(info) > 1 and info[1] is not None else ""

        # info[7] 是 int（大航海等级）；偶有实现返回列表，做一次兜底。
        guard_level = 0
        if len(info) > 7:
            guard_raw = info[7]
            try:
                if isinstance(guard_raw, list):
                    guard_level = int(guard_raw[0]) if guard_raw else 0
                else:
                    guard_level = int(guard_raw or 0)
            except (TypeError, ValueError):
                guard_level = 0

        user_level = 0
        if user_level_info:
            try:
                user_level = int(user_level_info[0])
            except (TypeError, ValueError):
                user_level = 0

        medal = None
        if len(medal_info) >= 4:
            try:
                medal = MedalInfo(
                    name=str(medal_info[1] or ""),
                    level=int(medal_info[0]),
                    up_name=str(medal_info[2] or "") if len(medal_info) > 2 else "",
                    anchor_roomid=int(medal_info[3]) if len(medal_info) > 3 else 0,
                    color=int(medal_info[4]) if len(medal_info) > 4 else 0,
                )
            except (TypeError, ValueError):
                medal = None

        fans_medal_name = ""
        fans_medal_level = 0
        if len(medal_info) >= 2:
            fans_medal_name = str(medal_info[1] or "")
            try:
                fans_medal_level = int(medal_info[0])
            except (TypeError, ValueError):
                fans_medal_level = 0

        return cls(
            msg_type=MessageType.MSG_DANMAKU,
            uid=uid,
            nickname=nickname,
            text=text,
            room_id=data.get("room_id", 0),
            admin=admin,
            guard_level=guard_level,
            vip=vip,
            svip=svip,
            medal=medal,
            user_level=user_level,
            fans_medal_name=fans_medal_name,
            fans_medal_level=fans_medal_level,
            # 弹幕 payload 不含头像 URL，头像由下游 bili_identity 按 UID 抓取。
            face_url="",
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_gift(cls, data: dict) -> "LiveDanmaku":
        """从 SEND_GIFT 解析礼物消息"""
        d = data.get("data", {})
        return cls(
            msg_type=MessageType.MSG_GIFT,
            uid=int(d.get("uid", 0)),
            nickname=str(d.get("uname", "")),
            text=f"赠送 {d.get('num', 1)} 个 {d.get('giftName', '礼物')}",
            room_id=int(d.get("room_id") or d.get("ruid", 0)),
            medal=MedalInfo(
                name=str(d.get("medal_info", {}).get("medal_name", "")),
                level=int(d.get("medal_info", {}).get("medal_level", 0)),
                up_name=str(d.get("medal_info", {}).get("medal_up_name", "")),
            ) if d.get("medal_info") else None,
            gift=GiftInfo(
                gift_id=int(d.get("giftId", 0)),
                gift_name=str(d.get("giftName", "礼物")),
                num=int(d.get("num", 1)),
                coin_type=str(d.get("coin_type", "silver")),
                total_coin=int(d.get("total_coin", 0)),
                price=int(d.get("price", 0)),
            ),
            user_level=int(d.get("level", 0)),
            face_url=str(d.get("face", "")),
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_sc(cls, data: dict) -> "LiveDanmaku":
        """从 SUPER_CHAT_MESSAGE 解析 SC"""
        d = data.get("data", {})
        return cls(
            msg_type=MessageType.MSG_SUPER_CHAT,
            uid=int(d.get("uid", 0)),
            nickname=str(d.get("user_info", {}).get("uname", "")),
            text=str(d.get("message", "")),
            room_id=int(d.get("room_id", 0)),
            admin=bool(d.get("user_info", {}).get("admin", False)),
            medal=MedalInfo(
                name=str(d.get("medal_info", {}).get("medal_name", "")),
                level=int(d.get("medal_info", {}).get("medal_level", 0)),
            ) if d.get("medal_info") else None,
            gift=GiftInfo(
                gift_name="Super Chat",
                total_coin=int(d.get("price", 0)) * 1000,
                price=int(d.get("price", 0)) * 1000,
            ),
            user_level=int(d.get("user_info", {}).get("user_level", 0)),
            face_url=str(d.get("user_info", {}).get("face", "")),
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_interact(cls, data: dict) -> "LiveDanmaku":
        """从 INTERACT_WORD 解析互动消息（进场/关注）"""
        d = data.get("data", {})
        msg_type_val = int(d.get("msg_type", 0))
        return cls(
            msg_type=MessageType.MSG_WELCOME if msg_type_val in (1, 3) else MessageType.MSG_ATTENTION if msg_type_val == 2 else MessageType.MSG_EXTRA,
            uid=int(d.get("uid", 0)),
            nickname=str(d.get("uname", "")),
            text=str(d.get("uname", "")) + (" 进入直播间" if msg_type_val in (1, 3) else " 关注了主播"),
            room_id=int(d.get("room_id", 0)),
            medal=MedalInfo(
                name=str(d.get("medal_info", {}).get("medal_name", "")),
                level=int(d.get("medal_info", {}).get("medal_level", 0)),
                up_name=str(d.get("medal_info", {}).get("medal_up_name", "")),
            ) if d.get("medal_info") else None,
            guard_level=int(d.get("guard_level", 0)),
            user_level=int(d.get("level", 0)),
            attention=int(d.get("attention", 0)),
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_guard_buy(cls, data: dict) -> "LiveDanmaku":
        """从 GUARD_BUY 解析上舰消息"""
        d = data.get("data", {})
        guard_level = int(d.get("guard_level", 0))
        guard_names = {1: "总督", 2: "提督", 3: "舰长"}
        guard_name = guard_names.get(guard_level, f"等级{guard_level}")
        return cls(
            msg_type=MessageType.MSG_GUARD_BUY,
            uid=int(d.get("uid", 0)),
            nickname=str(d.get("username", "")),
            text=f"购买了 {guard_name}",
            room_id=int(d.get("room_id", 0)),
            guard_level=guard_level,
            gift=GiftInfo(
                gift_id=int(d.get("gift_id", 0)),
                gift_name=guard_name,
                num=int(d.get("num", 1)),
                total_coin=int(d.get("price", 0)),
                price=int(d.get("price", 0)),
            ),
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_entry_effect(cls, data: dict) -> "LiveDanmaku":
        """从 ENTRY_EFFECT 解析高能用户进场"""
        d = data.get("data", {})
        return cls(
            msg_type=MessageType.MSG_WELCOME_GUARD,
            uid=int(d.get("uid", 0)),
            nickname=str(d.get("copy_writing", "")).split(" ")[0] if d.get("copy_writing") else str(d.get("uname", "")),
            text=str(d.get("copy_writing", "高能用户进场")),
            room_id=int(d.get("room_id", 0)),
            guard_level=3,  # ENTRY_EFFECT 通常为舰长以上
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_like(cls, data: dict) -> "LiveDanmaku":
        """从 LIKE_INFO_V3_CLICK 解析点赞"""
        d = data.get("data", {})
        return cls(
            msg_type=MessageType.MSG_EXTRA,
            uid=int(d.get("uid", 0)),
            nickname=str(d.get("uname", "")),
            text="点赞了直播间",
            room_id=int(d.get("room_id", 0)),
            user_level=int(d.get("level", 0)),
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_online_rank(cls, data: dict) -> "LiveDanmaku":
        """从 ONLINE_RANK_V2 / ONLINE_RANK_TOP3 解析高能榜"""
        d = data.get("data", {})
        names = []
        if "list" in d:
            for item in d["list"][:3]:
                names.append(str(item.get("name", "")))
        elif "name" in d:
            names = [str(d.get("name", ""))]
        text = "高能榜: " + ", ".join(names) if names else "高能榜更新"
        return cls(
            msg_type=MessageType.MSG_EXTRA,
            uid=0,
            nickname="",
            text=text,
            room_id=int(data.get("room_id", 0)),
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_anchor_lot(cls, data: dict) -> "LiveDanmaku":
        """从 ANCHOR_LOT_START / ANCHOR_LOT_END 解析天选抽奖"""
        d = data.get("data", {})
        is_start = data.get("cmd", "").endswith("_START")
        return cls(
            msg_type=MessageType.MSG_EXTRA,
            uid=0,
            nickname="",
            text="天选时刻开始啦！快去参与抽奖！" if is_start else "天选时刻已结束",
            room_id=int(d.get("room_id", 0)),
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_block(cls, data: dict) -> "LiveDanmaku":
        """从 ROOM_BLOCK_MSG 解析禁言"""
        d = data.get("data", {})
        return cls(
            msg_type=MessageType.MSG_BLOCK,
            uid=int(d.get("uid", 0)),
            nickname=str(d.get("uname", "")),
            text=f"{d.get('uname', '用户')} 被禁言",
            room_id=int(data.get("room_id", 0)),
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_watched_change(cls, data: dict) -> "LiveDanmaku":
        """从 WATCHED_CHANGE 解析看过人数变化"""
        d = data.get("data", {})
        num = int(d.get("num", 0))
        text_small = d.get("text_small", "")
        return cls(
            msg_type=MessageType.MSG_EXTRA,
            uid=0,
            nickname="",
            text=f"累计看过: {text_small or num}",
            room_id=int(data.get("room_id", 0)),
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_notice(cls, data: dict) -> "LiveDanmaku":
        """从 NOTICE_MSG 解析公告"""
        d = data.get("data", {})
        notice_text = ""
        if isinstance(d, dict):
            notice_text = str(d.get("real_room_notice", "") or d.get("msg", "") or "")
        full_cmd = data.get("cmd", "")
        if not notice_text and "full" in data:
            notice_text = str(data.get("full", ""))
        return cls(
            msg_type=MessageType.MSG_MSG,
            uid=0,
            nickname="",
            text=notice_text or f"公告: {full_cmd}",
            room_id=int(data.get("room_id", 0) or (d.get("room_id", 0) if isinstance(d, dict) else 0)),
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_raw_json(cls, raw: str) -> Optional["LiveDanmaku"]:
        """从原始 JSON 字符串解析（兜底方法）"""
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            return None
        cmd = data.get("cmd", "")
        if "DANMU_MSG" in cmd:
            return cls.from_danmaku(data)
        elif cmd == "SEND_GIFT":
            return cls.from_gift(data)
        elif "SUPER_CHAT_MESSAGE" in cmd:
            return cls.from_sc(data)
        elif cmd == "INTERACT_WORD":
            return cls.from_interact(data)
        elif cmd == "GUARD_BUY":
            return cls.from_guard_buy(data)
        elif cmd == "ENTRY_EFFECT":
            return cls.from_entry_effect(data)
        elif cmd == "LIKE_INFO_V3_CLICK":
            return cls.from_like(data)
        elif cmd in ("ONLINE_RANK_V2", "ONLINE_RANK_TOP3"):
            return cls.from_online_rank(data)
        elif cmd in ("ANCHOR_LOT_START", "ANCHOR_LOT_END"):
            return cls.from_anchor_lot(data)
        elif cmd == "ROOM_BLOCK_MSG":
            return cls.from_block(data)
        elif cmd == "WATCHED_CHANGE":
            return cls.from_watched_change(data)
        elif cmd == "NOTICE_MSG":
            return cls.from_notice(data)
        return None

    # ── 方法 ─────────────────────────────────────────────────

    def get_score(self) -> float:
        """
        计算弹幕的综合评分
        用于降级模式下的优选/排序
        """
        score = 0.0
        _guard_score = {1: 3000, 2: 2000, 3: 1000}
        score += _guard_score.get(self.guard_level, 0)
        if self.admin:
            score += 500
        if self.vip:
            score += 100
        if self.svip:
            score += 200
        if self.medal:
            score += self.medal.level * 10
        score += self.user_level * 2
        text_len = len(self.text.strip())
        score += min(text_len, 100)

        # 高价值内容额外加分
        if self.msg_type == MessageType.MSG_SUPER_CHAT:
            score += 5000  # SC 优先
        elif self.msg_type == MessageType.MSG_GIFT:
            if self.gift:
                score += min(self.gift.total_coin / 100, 1000)  # 高价值礼物
        elif self.msg_type == MessageType.MSG_GUARD_BUY:
            score += 3000  # 上舰

        return score

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "msg_type": int(self.msg_type),
            "uid": self.uid,
            "nickname": self.nickname,
            "text": self.text,
            "timeline": self.timeline,
            "room_id": self.room_id,
            "admin": self.admin,
            "guard_level": self.guard_level,
            "vip": self.vip,
            "svip": self.svip,
            "medal": {
                "name": self.medal.name,
                "level": self.medal.level,
                "up_name": self.medal.up_name,
                "color": self.medal.color,
            } if self.medal else None,
            "user_level": self.user_level,
            "gift": {
                "gift_id": self.gift.gift_id,
                "gift_name": self.gift.gift_name,
                "num": self.gift.num,
                "coin_type": self.gift.coin_type,
                "total_coin": self.gift.total_coin,
                "price": self.gift.price,
            } if self.gift else None,
            "face_url": self.face_url,
            "attention": self.attention,
        }
