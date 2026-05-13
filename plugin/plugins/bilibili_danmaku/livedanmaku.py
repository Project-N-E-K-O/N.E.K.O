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
from typing import Any, Optional


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
        """从 DANMU_MSG 解析弹幕消息"""
        info = data.get("info", [])
        user_info = info[2] if len(info) > 2 else ["", "", ""]
        medal_info = info[3] if len(info) > 3 else []
        user_level_info = info[4] if len(info) > 4 else [0]
        uid_title = info[5] if len(info) > 5 else []
        uid_info = info[7] if len(info) > 7 else [0, "", ""]

        uid = 0
        nickname = ""
        if isinstance(user_info, list) and len(user_info) >= 2:
            uid = user_info[0]
            nickname = str(user_info[1] or "")

        text = info[1] if len(info) > 1 else ""

        admin = bool(info[2][2]) if len(info) > 2 else False
        guard_level = int(info[7][3]) if len(info) > 7 and len(info[7]) > 3 else 0

        medal = None
        if medal_info and len(medal_info) >= 4:
            medal = MedalInfo(
                name=str(medal_info[1] or ""),
                level=int(medal_info[0]),
                up_name=str(medal_info[3] or ""),
                color=int(medal_info[2]) if len(medal_info) > 2 else 0,
                anchor_roomid=int(medal_info[5]) if len(medal_info) > 5 else 0,
            )

        return cls(
            msg_type=MessageType.MSG_DANMAKU,
            uid=uid,
            nickname=nickname,
            text=text,
            room_id=data.get("room_id", 0),
            admin=admin,
            guard_level=guard_level,
            vip=bool(info[7][1]) if len(info) > 7 and len(info[7]) > 1 else False,
            svip=bool(info[7][2]) if len(info) > 7 and len(info[7]) > 2 else False,
            medal=medal,
            user_level=int(user_level_info[0]) if user_level_info else 0,
            fans_medal_name=str(medal_info[1]) if medal_info and len(medal_info) > 1 else "",
            fans_medal_level=int(medal_info[0]) if medal_info else 0,
            face_url=str(user_info[3]) if isinstance(user_info, list) and len(user_info) > 3 else "",
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
            room_id=int(d.get("ruid", 0)),
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
        score += self.guard_level * 1000
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
