"""
MagicalDanmaku LiveDanmaku 数据模型（Python 移植版）

功能：
- MessageType 枚举（15+种 B站消息类型，对标 C++ livedanmaku.h）
- LiveDanmaku dataclass（50+ 字段，完整覆盖用户/礼物/粉丝牌/房间/PK）
- 工厂方法：从各种 B站 WS 消息体解析
- get_score() 打分（guard > admin > medal > user level > text length）
- to_dict() 序列化（按 msgType 分条件序列化，避免冗余字段）
- from_dict() 反序列化（对标 C++ fromDanmakuJson）
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional


# ── 消息类型枚举 ─────────────────────────────────────────────────


class MessageType(IntEnum):
    """B站直播 WS 消息类型（对标 C++ livedanmaku.h）"""
    MSG_DEF = 0             # 默认/未定义
    MSG_DANMAKU = 1         # 弹幕
    MSG_GIFT = 2            # 礼物
    MSG_WELCOME = 3         # 欢迎/进场
    MSG_DIANGE = 4          # 点歌
    MSG_GUARD_BUY = 5       # 上舰（大航海）
    MSG_WELCOME_GUARD = 6   # 舰长/高能入场
    MSG_FANS = 7            # 粉丝数变化
    MSG_ATTENTION = 8       # 关注
    MSG_BLOCK = 9           # 禁言
    MSG_MSG = 10            # 普通消息/公告
    MSG_SHARE = 11          # 分享
    MSG_PK_BEST = 12        # PK 最佳
    MSG_SUPER_CHAT = 13     # 醒目留言
    MSG_EXTRA = 14          # 其他/未知


# ── 嵌套数据类 ──────────────────────────────────────────────────


@dataclass
class MedalInfo:
    """粉丝牌信息（对标 C++ medal 字段）"""
    name: str = ""               # 牌子名称，如 "戒不掉"
    level: int = 0               # 牌子等级
    up_name: str = ""            # 主播名称（牌子的主播），对应 C++ medal_up
    color: str = ""              # 牌子颜色（6位 hex 字符串），对应 C++ medal_color
    anchor_roomid: str = ""      # 牌子所属主播房间号，对应 C++ anchor_roomid


@dataclass
class GiftInfo:
    """礼物信息"""
    gift_id: int = 0
    gift_name: str = ""
    num: int = 1
    coin_type: str = "silver"    # silver/gold
    total_coin: int = 0
    price: int = 0               # 单价（金瓜子）
    face_url: str = ""           # 礼物图标 URL


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
    uidentity: int = 0           # 正式会员 (C++ uidentity)
    iphone: int = 0              # 手机实名 (C++ iphone)


# ── LiveDanmaku 主类 ────────────────────────────────────────────


@dataclass
class LiveDanmaku:
    """
    LiveDanmaku — 单条 B站直播消息（对标 C++ livedanmaku.h）

    覆盖 50+ 字段，包含完整的用户/礼物/粉丝牌/房间/PK/身份信息。
    通过工厂方法从不同 WS 指令解析。
    """
    # 基础字段
    msg_type: MessageType = MessageType.MSG_DANMAKU
    uid: int = 0
    nickname: str = ""
    text: str = ""
    timeline: float = field(default_factory=time.time)
    room_id: int = 0
    room_id_str: str = ""              # C++ roomId（字符串形式）

    # 用户身份标记位（对标 C++ setUserInfo）
    admin: bool = False
    guard_level: int = 0               # 0=无, 1=总督, 2=提督, 3=舰长
    vip: bool = False
    svip: bool = False
    uidentity: int = 0                 # 正式会员 (C++ uidentity)
    iphone: int = 0                    # 手机实名 (C++ iphone)

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
    attention: int = 0                 # 0=未关注, 1=已关注

    # 用户头像
    face_url: str = ""

    # PK 大乱斗字段（对标 C++）
    opposite: bool = False             # 是否是大乱斗对面的过来
    to_view: bool = False              # 是否是自己这边过去串门的
    view_return: bool = False          # 自己这边过去串门回来的
    pk_link: bool = False              # 是否是PK连接的

    # 弹幕标识（对标 C++）
    no_reply: bool = False             # 不需要处理的弹幕
    auto_send: bool = False            # 自己发送的弹幕

    # 机器人相关
    robot: bool = False

    # 提醒字段
    prev_timestamp: int = 0            # 上次关注时间戳
    first: int = 0                     # 初次：1；新的：2 (C++ first)
    special: int = 0                   # 特别关注标记 (C++ special)

    # 重试
    retry: int = 0                     # 重试次数 (C++ retry)

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
        uidentity = int(info[6][1]) if len(info) > 6 and len(info[6]) > 1 else 0
        iphone = int(info[7][1]) if len(info) > 7 and len(info[7]) > 1 else 0

        medal = None
        if medal_info and len(medal_info) >= 4:
            medal_color = medal_info[2] if len(medal_info) > 2 else 0
            medal_color_hex = ("000000" + hex(int(medal_color))[2:])[-6:] if medal_color else ""
            medal = MedalInfo(
                name=str(medal_info[1] or ""),
                level=int(medal_info[0]),
                up_name=str(medal_info[3] or ""),
                color=medal_color_hex,
                anchor_roomid=str(medal_info[5]) if len(medal_info) > 5 else "",
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
            uidentity=uidentity,
            iphone=iphone,
            medal=medal,
            user_level=int(user_level_info[0]) if user_level_info else 0,
            fans_medal_name=str(medal_info[1]) if medal_info and len(medal_info) > 1 else "",
            fans_medal_level=int(medal_info[0]) if medal_info else 0,
            face_url=str(user_info[3]) if isinstance(user_info, list) and len(user_info) > 3 else "",
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_diange(cls, data: dict) -> "LiveDanmaku":
        """从点歌消息解析（MSG_DIANGE）
        
        示例 JSON：
        {"cmd": "...", "data": {"uname": "用户", "uid": 123, "song_name": "歌名", ...}}
        """
        d = data.get("data", {})
        song_name = str(d.get("song_name", ""))
        return cls(
            msg_type=MessageType.MSG_DIANGE,
            uid=int(d.get("uid", 0)),
            nickname=str(d.get("uname", "")),
            text=song_name,
            room_id=int(data.get("room_id", 0)),
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_pk_best(cls, data: dict) -> "LiveDanmaku":
        """从 PK_LOTTERY_START 或其他 PK 消息解析最佳用户"""
        d = data.get("data", {})
        uid = int(d.get("uid", 0))
        nickname = str(d.get("uname", ""))
        votes = int(d.get("votes", 0) or 0)
        return cls(
            msg_type=MessageType.MSG_PK_BEST,
            uid=uid,
            nickname=nickname,
            text=f"PK 最佳: {nickname} ({votes}票)" if nickname else "PK 最佳更新",
            room_id=int(data.get("room_id", 0)),
            gift=GiftInfo(total_coin=votes) if votes else None,
            extra_json=json.dumps(data, ensure_ascii=False),
        )

    @classmethod
    def from_fans_change(cls, data: dict) -> "LiveDanmaku":
        """从粉丝数变更消息解析（MSG_FANS）"""
        d = data.get("data", {})
        fans = int(d.get("fans", 0))
        fans_club = int(d.get("fans_club", 0))
        delta_fans = int(d.get("delta_fans", 0))
        delta_fans_club = int(d.get("delta_fans_club", 0))
        sign_fans = "+" if delta_fans >= 0 else ""
        sign_club = "+" if delta_fans_club >= 0 else ""
        return cls(
            msg_type=MessageType.MSG_FANS,
            uid=0,
            nickname="",
            text=f"粉丝: {fans}({sign_fans}{delta_fans}), 粉丝团: {fans_club}({sign_club}{delta_fans_club})",
            room_id=int(data.get("room_id", 0)),
            fans_medal_level=fans_club,
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
                color="",
                anchor_roomid=str(d.get("medal_info", {}).get("anchor_roomid", "") or d.get("anchor_roomid", "")),
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
                up_name=str(d.get("medal_info", {}).get("anchor_uname", "")),
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
        uid = int(d.get("uid", 0))
        # uname 优先，copy_writing 仅作兜底文本
        nickname = str(d.get("uname") or d.get("username") or "").strip()
        raw_copy = d.get("copy_writing") or ""
        copy_writing = str(raw_copy).strip() if raw_copy else "高能用户进场"
        return cls(
            msg_type=MessageType.MSG_WELCOME_GUARD,
            uid=uid,
            nickname=nickname or f"用户{uid}",
            text=copy_writing,
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
        elif cmd in ("PK_LOTTERY_START", "PK_LOTTERY_END", "PK_BEST"):
            return cls.from_pk_best(data)
        elif cmd == "VOICE_JOIN_ROOM_COUNT_INFO":
            return cls.from_diange(data)
        elif cmd in ("ROOM_RANK", "USER_TOAST_MSG"):
            return cls.from_fans_change(data)
        return None

    # ── 辅助设置方法（对标 C++ setter 链式调用） ────────────

    def set_medal(self, room_id: str, name: str, level: int, color: str, up: str = "") -> "LiveDanmaku":
        """设置粉丝牌信息"""
        self.medal = MedalInfo(name=name, level=level, up_name=up, color=color, anchor_roomid=room_id)
        return self

    def set_guard_level(self, level: int) -> "LiveDanmaku":
        """设置大航海等级"""
        self.guard_level = level
        return self

    def set_user_info(self, admin: int = 0, vip: int = 0, svip: int = 0, uidentity: int = 0, iphone: int = 0, guard: int = 0) -> "LiveDanmaku":
        """设置用户身份信息（对标 C++ setUserInfo）"""
        self.admin = bool(admin)
        self.vip = bool(vip)
        self.svip = bool(svip)
        self.uidentity = uidentity
        self.iphone = iphone
        self.guard_level = guard
        return self

    def add_gift(self, count: int, total_coin: int, timeline: float | None = None) -> "LiveDanmaku":
        """累加礼物数量和金额（对标 C++ addGift）"""
        if self.gift:
            self.gift.num += count
            self.gift.total_coin += total_coin
        else:
            self.gift = GiftInfo(num=count, total_coin=total_coin)
        if timeline is not None:
            self.timeline = timeline
        return self

    def set_opposite(self, op: bool) -> "LiveDanmaku":
        self.opposite = op
        return self

    def set_to_view(self, to: bool) -> "LiveDanmaku":
        self.to_view = to
        return self

    def set_view_return(self, re: bool) -> "LiveDanmaku":
        self.view_return = re
        return self

    def set_pk_link(self, link: bool) -> "LiveDanmaku":
        self.pk_link = link
        return self

    def set_robot(self, r: bool) -> "LiveDanmaku":
        self.robot = r
        return self

    def set_no_reply(self) -> "LiveDanmaku":
        self.no_reply = True
        return self

    def set_first(self, first: int) -> "LiveDanmaku":
        self.first = first
        return self

    def set_special(self, s: int) -> "LiveDanmaku":
        self.special = s
        return self

    def with_room_id(self, room_id: str) -> "LiveDanmaku":
        self.room_id_str = room_id
        self.room_id = int(room_id) if room_id.isdigit() else 0
        return self

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

    def get_guard_name(self) -> str:
        """获取大航海等级名称"""
        _guard_names = {1: "总督", 2: "提督", 3: "舰长"}
        return _guard_names.get(self.guard_level, "")

    def is_gold_coin(self) -> bool:
        return self.gift is not None and self.gift.coin_type == "gold"

    def is_silver_coin(self) -> bool:
        return self.gift is not None and self.gift.coin_type == "silver"

    # ── 序列化 ─────────────────────────────────────────────

    def to_dict(self) -> dict:
        """序列化为字典（对标 C++ toJson，按 msgType 分条件序列化）"""
        obj = {
            "nickname": self.nickname,
            "uid": self.uid,
            "timeline": self.timeline,
            "msgType": int(self.msg_type),
        }
        if self.room_id:
            obj["room_id"] = self.room_id

        # 弹幕/SC 特有字段
        if self.msg_type in (MessageType.MSG_DANMAKU, MessageType.MSG_SUPER_CHAT):
            obj["text"] = self.text
            obj["admin"] = self.admin
            obj["vip"] = self.vip
            obj["svip"] = self.svip
            obj["level"] = self.user_level
            obj["uidentity"] = self.uidentity
            obj["iphone"] = self.iphone
            obj["guard_level"] = self.guard_level
            obj["no_reply"] = self.no_reply

        # 礼物/上舰/SC 特有字段
        if self.msg_type in (MessageType.MSG_GIFT, MessageType.MSG_GUARD_BUY, MessageType.MSG_SUPER_CHAT):
            if self.gift:
                obj["gift_id"] = self.gift.gift_id
                obj["gift_name"] = self.gift.gift_name
                obj["number"] = self.gift.num
                obj["coin_type"] = self.gift.coin_type
                obj["total_coin"] = self.gift.total_coin
            if self.msg_type == MessageType.MSG_GUARD_BUY:
                obj["guard_level"] = self.guard_level
                obj["first"] = self.first

        # 进场
        elif self.msg_type == MessageType.MSG_WELCOME:
            obj["admin"] = self.admin
            obj["number"] = self.gift.num if self.gift else 0

        # 舰长进场
        elif self.msg_type == MessageType.MSG_WELCOME_GUARD:
            obj["admin"] = self.admin
            obj["guard_level"] = self.guard_level

        # 点歌
        elif self.msg_type == MessageType.MSG_DIANGE:
            obj["text"] = self.text

        # 粉丝
        elif self.msg_type == MessageType.MSG_FANS:
            obj["fans_medal_level"] = self.fans_medal_level

        # 关注
        elif self.msg_type == MessageType.MSG_ATTENTION:
            obj["attention"] = self.attention
            if self.special:
                obj["special"] = self.special

        # 消息
        elif self.msg_type == MessageType.MSG_MSG:
            obj["text"] = self.text

        # PK 最佳
        elif self.msg_type == MessageType.MSG_PK_BEST:
            obj["level"] = self.user_level
            if self.gift:
                obj["total_coin"] = self.gift.total_coin

        # 粉丝牌（只要有名有级就输出）
        if self.medal and (self.medal.name or self.medal.level):
            obj["anchor_roomid"] = self.medal.anchor_roomid
            obj["medal_name"] = self.medal.name
            obj["medal_level"] = self.medal.level
            obj["medal_color"] = self.medal.color
            obj["medal_up"] = self.medal.up_name

        # PK 相关
        if self.opposite:
            obj["opposite"] = self.opposite
        if self.to_view:
            obj["to_view"] = self.to_view
        if self.view_return:
            obj["view_return"] = self.view_return
        if self.pk_link:
            obj["pk_link"] = self.pk_link
        if self.robot:
            obj["robot"] = self.robot
        if self.prev_timestamp:
            obj["prev_timestamp"] = self.prev_timestamp
        if self.room_id_str:
            obj["room_id_str"] = self.room_id_str
        if self.face_url:
            obj["face_url"] = self.face_url

        return obj

    @classmethod
    def from_dict(cls, data: dict) -> "LiveDanmaku":
        """从字典反序列化（对标 C++ fromDanmakuJson）"""
        danmaku = cls()
        danmaku.text = str(data.get("text", ""))
        danmaku.uid = int(data.get("uid", 0))
        danmaku.nickname = str(data.get("nickname", ""))
        danmaku.timeline = data.get("timeline", time.time())
        danmaku.admin = bool(data.get("admin", False))
        danmaku.vip = bool(data.get("vip", False))
        danmaku.svip = bool(data.get("svip", False))
        danmaku.user_level = int(data.get("level", 0))
        danmaku.room_id = int(data.get("room_id", 0))

        # 粉丝牌
        medal_name = str(data.get("medal_name", ""))
        if medal_name:
            danmaku.medal = MedalInfo(
                name=medal_name,
                level=int(data.get("medal_level", 0)),
                up_name=str(data.get("medal_up", "")),
                color=str(data.get("medal_color", "")),
                anchor_roomid=str(data.get("anchor_roomid", "")),
            )

        # 礼物 — 按 gift_name 或 total_coin 存在性重建（SC 可能 gift_id=0）
        gift_name = str(data.get("gift_name", ""))
        total_coin = int(data.get("total_coin", 0))
        if gift_name or total_coin:
            danmaku.gift = GiftInfo(
                gift_id=int(data.get("gift_id", 0)),
                gift_name=gift_name,
                num=int(data.get("number", 1)),
                coin_type=str(data.get("coin_type", "silver")),
                total_coin=total_coin,
            )

        danmaku.msg_type = MessageType(int(data.get("msgType", 1)))
        danmaku.fans_medal_level = int(data.get("fans_medal_level", 0))
        danmaku.attention = bool(data.get("attention", False))
        danmaku.guard_level = int(data.get("guard_level", 0))
        danmaku.uidentity = int(data.get("uidentity", 0))
        danmaku.iphone = int(data.get("iphone", 0))
        danmaku.no_reply = bool(data.get("no_reply", False))
        danmaku.opposite = bool(data.get("opposite", False))
        danmaku.to_view = bool(data.get("to_view", False))
        danmaku.view_return = bool(data.get("view_return", False))
        danmaku.pk_link = bool(data.get("pk_link", False))
        danmaku.robot = bool(data.get("robot", False))
        danmaku.first = int(data.get("first", 0))
        danmaku.special = int(data.get("special", 0))
        danmaku.prev_timestamp = int(data.get("prev_timestamp", 0))
        danmaku.room_id_str = str(data.get("room_id_str", ""))
        danmaku.face_url = str(data.get("face_url", ""))
        danmaku.extra_json = json.dumps(data.get("extra", {})) if "extra" in data else ""

        return danmaku
