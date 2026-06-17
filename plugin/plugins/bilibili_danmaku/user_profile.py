"""
统一用户记录系统

基于参考 C++ 应用 (Bilibili-MagicalDanmaku) 的 UserSettings 设计，
合并原有 UserProfileTracker (轻量) + DanmakuMemory (用户部分)，
新增本地昵称、用户备注、用户分类、来访记录、永久禁言等功能。

参考 C++ 功能映射：
  UserSettings::localNicknames  → set_local_nickname / get_local_nickname
  UserSettings::userMarks       → set_note / get_note
  UserSettings::careUsers       → set_user_cared / is_user_cared
  UserSettings::strongNotifyUsers → set_user_strong_notify
  UserSettings::notWelcomeUsers → set_user_not_welcome / is_user_not_welcome
  UserSettings::notReplyUsers   → set_user_not_reply / is_user_not_reply
  EternalBlockUser              → set_user_blocked / is_user_blocked
  danmakuCounts (come/gold/silver) → message_count, gift_total, come_count
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────
_MAX_RECENT_TEXTS = 20               # 每人保留最近发言数
_MAX_ACTIVE_USERS = 200              # 最多追踪的用户数
_VIP_GIFT_THRESHOLD = 50             # 送礼超此金额标记 VIP
_REGULAR_MSG_THRESHOLD = 10          # 发言超此条数标记常客


# ==================================================================
# 数据类
# ==================================================================

@dataclass
class UserRecord:
    """统一的用户记录

    合并 UserProfile (经典) + AudienceProfile (增强) 所有字段，
    并新增来自参考 C++ 应用的功能。
    """
    # ── 基础标识 ──
    key: str                         # 标识键: str(uid) 或 uname 兜底
    uid: int = 0
    uname: str = ""

    # ── 统计 ──
    message_count: int = 0
    gift_total: float = 0.0
    gift_count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    avg_message_length: float = 0.0
    recent_texts: List[str] = field(default_factory=list)

    # ── 话题偏好 ──
    topics: dict = field(default_factory=dict)   # topic_name -> mention_count
    top_topic: str = ""

    # ── 互动风格（LLM 推测） ──
    interaction_style: str = "默认"    # 提问型/梗型/闲聊型/支持型
    response_preference: str = ""      # 希望猫娘怎么回应

    # ── 身份标签 ──
    is_regular: bool = False           # 常客（发言 >= _REGULAR_MSG_THRESHOLD）
    is_vip: bool = False               # 大股东（送礼 > _VIP_GIFT_THRESHOLD）

    # ── 本地昵称 & 备注（参考 C++: localNicknames / userMarks） ──
    local_nickname: str = ""           # 自定义本地昵称，覆盖原始 uname
    note: str = ""                     # 用户备注文本
    note_created_at: float = 0.0
    note_updated_at: float = 0.0

    # ── 用户分类（参考 C++: careUsers / strongNotifyUsers / ...） ──
    is_cared: bool = False             # 特别关心
    is_strong_notify: bool = False     # 强提醒
    is_blocked: bool = False           # 永久禁言
    blocked_at: float = 0.0
    block_reason: str = ""
    is_not_welcome: bool = False       # 不自动欢迎
    is_not_reply: bool = False         # 不自动回复

    # ── 来访记录（参考 C++:  danmakuCounts 中的 come/comeTime） ──
    come_count: int = 0                # 进入直播间的次数
    last_come_time: float = 0.0        # 最后一次进入时间

    # ── 便捷方法 ──

    def get_display_name(self) -> str:
        """获取显示名：本地昵称 > 原始昵称"""
        return self.local_nickname or self.uname

    def add_topic(self, topic: str, count: int = 1):
        """更新话题偏好"""
        self.topics[topic] = self.topics.get(topic, 0) + count
        if self.topics:
            self.top_topic = max(self.topics, key=self.topics.get)

    def is_inactive(self, now: float, ttl: float = 3600) -> bool:
        """是否长时间无活动"""
        return (now - self.last_seen) > ttl


# ==================================================================
# 统一用户记录管理器
# ==================================================================

class UserRecordManager:
    """
    统一用户记录管理器

    替代 UserProfileTracker 的全部功能 + DanmakuMemory 的用户画像部分，
    并新增参考 C++ 应用的分类/备注/禁言等功能。

    用法:
        records = UserRecordManager(data_dir=Path("data/user_records"))
        await records.load()

        # 记录活动
        records.record(uid=12345, uname="小明", text="你好")
        records.record_gift(uname="小明", price=20)
        records.record_entry(uid=12345, uname="小明")

        # 查询
        ctx = records.get_profile_context()
        summary = records.get_audience_summary()

        # 管理（参考 C++ 功能）
        records.set_local_nickname(12345, "我的小明")
        records.set_note(12345, "老粉，喜欢唱歌")
        records.set_user_cared(12345, True)
        records.set_user_blocked(99999, reason="广告机器人")

        await records.save()
    """

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        max_active: int = _MAX_ACTIVE_USERS,
        max_recent: int = _MAX_RECENT_TEXTS,
    ):
        self._profiles: dict[str, UserRecord] = {}
        self._data_dir = data_dir
        self._max_active = max_active
        self._max_recent = max_recent
        self._activity_order: list[str] = []  # LRU: 最近活跃在前

    # ── 记录接口 ──────────────────────────────────────────────

    def record(
        self,
        uid: int,
        uname: str,
        text: str,
        has_gifted: bool = False,
        gift_amount: float = 0.0,
    ):
        """记录一条用户活动（弹幕）

        兼容 UserProfileTracker.record() 接口。
        """
        key = str(uid) if uid > 0 else uname
        if not key:
            return

        now = time.time()
        profile = self._get_or_create(key, uid, uname)

        # 更新统计
        profile.uid = uid
        profile.uname = uname
        profile.message_count += 1
        profile.last_seen = now
        if profile.first_seen == 0:
            profile.first_seen = now

        # 平均长度
        profile.avg_message_length = (
            (profile.avg_message_length * (profile.message_count - 1) + len(text))
            / profile.message_count
        )

        # 最近发言
        profile.recent_texts.append(text)
        if len(profile.recent_texts) > self._max_recent:
            profile.recent_texts = profile.recent_texts[-self._max_recent:]

        # 常客标记
        profile.is_regular = profile.message_count >= _REGULAR_MSG_THRESHOLD

        if has_gifted:
            profile.gift_total += gift_amount
            profile.gift_count += 1
            profile.is_vip = profile.gift_total > _VIP_GIFT_THRESHOLD

        self._touch(key)

    def record_danmaku(self, uid: int, uname: str, text: str):
        """记录弹幕 — DanmakuMemory 兼容接口"""
        self.record(uid=uid, uname=uname, text=text)

    def record_gift(self, uid: int = 0, uname: str = "", price: float = 0.0):
        """记录送礼"""
        key = str(uid) if uid > 0 else self._find_by_uname(uname)
        if not key:
            key = uname
        if not key:
            return
        profile = self._get_or_create(key, uid, uname)
        # 回写缺失的 uid/uname（已有记录通过昵称查找创建的，后续获得 UID 时需要刷新）
        if uid > 0 and not profile.uid:
            profile.uid = uid
        if uname and not profile.uname:
            profile.uname = uname
        profile.gift_total += price
        profile.gift_count += 1
        profile.is_vip = profile.gift_total > _VIP_GIFT_THRESHOLD
        self._touch(key)

    def record_entry(self, uid: int, uname: str):
        """记录用户进入直播间（参考 C++: userComeTimes）

        新增功能，供 _process_entry_event 调用。
        """
        key = str(uid) if uid > 0 else uname
        if not key:
            return

        now = time.time()
        profile = self._get_or_create(key, uid, uname)
        profile.uid = uid
        profile.uname = uname
        profile.come_count += 1
        profile.last_come_time = now
        profile.last_seen = now
        self._touch(key)

    # ── 用户分类管理（参考 C++ 功能） ─────────────────────────

    def set_local_nickname(self, uid: int, nickname: str) -> bool:
        """设置本地昵称（参考 C++: localNicknames）"""
        key = str(uid) if uid > 0 else ""
        if not key:
            return False
        profile = self._get_or_create(key, uid, "")
        profile.local_nickname = nickname
        return True

    def get_local_nickname(self, uid: int) -> str:
        """获取本地昵称"""
        key = str(uid) if uid > 0 else ""
        profile = self._profiles.get(key)
        return profile.local_nickname if profile else ""

    def set_note(self, uid: int, text: str) -> bool:
        """设置用户备注（参考 C++: userMarks）"""
        key = str(uid) if uid > 0 else ""
        if not key:
            return False
        now = time.time()
        profile = self._get_or_create(key, uid, "")
        if not profile.note:
            profile.note_created_at = now
        profile.note = text
        profile.note_updated_at = now
        return True

    def get_note(self, uid: int) -> str:
        """获取用户备注"""
        key = str(uid) if uid > 0 else ""
        profile = self._profiles.get(key)
        return profile.note if profile else ""

    def set_user_cared(self, uid: int, cared: bool = True) -> bool:
        """设置特别关心（参考 C++: careUsers）"""
        key = str(uid) if uid > 0 else ""
        if not key:
            return False
        self._get_or_create(key, uid, "").is_cared = cared
        return True

    def is_user_cared(self, uid: int) -> bool:
        key = str(uid) if uid > 0 else ""
        profile = self._profiles.get(key)
        return profile.is_cared if profile else False

    def set_user_strong_notify(self, uid: int, notify: bool = True) -> bool:
        """设置强提醒（参考 C++: strongNotifyUsers）"""
        key = str(uid) if uid > 0 else ""
        if not key:
            return False
        self._get_or_create(key, uid, "").is_strong_notify = notify
        return True

    def set_user_blocked(
        self, uid: int, blocked: bool = True, reason: str = ""
    ) -> bool:
        """设置永久禁言（参考 C++: EternalBlockUser）"""
        key = str(uid) if uid > 0 else ""
        if not key:
            return False
        profile = self._get_or_create(key, uid, "")
        profile.is_blocked = blocked
        if blocked:
            profile.blocked_at = time.time()
            profile.block_reason = reason
        else:
            profile.blocked_at = 0.0
            profile.block_reason = ""
        return True

    def is_user_blocked(self, uid: int) -> bool:
        key = str(uid) if uid > 0 else ""
        profile = self._profiles.get(key)
        return profile.is_blocked if profile else False

    def set_user_not_welcome(self, uid: int, not_welcome: bool = True) -> bool:
        """设置不自动欢迎（参考 C++: notWelcomeUsers）"""
        key = str(uid) if uid > 0 else ""
        if not key:
            return False
        self._get_or_create(key, uid, "").is_not_welcome = not_welcome
        return True

    def is_user_not_welcome(self, uid: int) -> bool:
        key = str(uid) if uid > 0 else ""
        profile = self._profiles.get(key)
        return profile.is_not_welcome if profile else False

    def set_user_not_reply(self, uid: int, not_reply: bool = True) -> bool:
        """设置不自动回复（参考 C++: notReplyUsers）"""
        key = str(uid) if uid > 0 else ""
        if not key:
            return False
        self._get_or_create(key, uid, "").is_not_reply = not_reply
        return True

    def is_user_not_reply(self, uid: int) -> bool:
        key = str(uid) if uid > 0 else ""
        profile = self._profiles.get(key)
        return profile.is_not_reply if profile else False

    def get_cared_users(self) -> list[UserRecord]:
        """获取所有特别关心的用户"""
        return [p for p in self._profiles.values() if p.is_cared]

    def get_blocked_users(self) -> list[UserRecord]:
        """获取所有被禁言的用户"""
        return [p for p in self._profiles.values() if p.is_blocked]

    # ── 查询接口 ──────────────────────────────────────────────

    def get_profile_context(self, max_count: int = 10) -> str:
        """生成 LLM Prompt 可用的画像上下文

        兼容 UserProfileTracker.get_profile_context() 接口。
        """
        active = self._get_active_profiles(max_count)
        if not active:
            return ""

        lines = ["观众画像信息（发言较多的活跃观众）："]
        for p in active:
            parts = [f"- @{p.get_display_name()}"]

            # 身份标签
            tags = []
            if p.message_count >= 50:
                tags.append("铁粉")
            elif p.message_count >= 10:
                tags.append("活跃观众")
            if p.is_vip:
                tags.append(f"送礼×{p.gift_count} (¥{p.gift_total:.0f})")
            if p.gift_count >= 3:
                tags.append("大股东")
            if p.note:
                tags.append(f"备注: {p.note[:20]}")
            if tags:
                parts.append(f"（{'，'.join(tags)}）")

            # 互动风格
            if p.interaction_style and p.interaction_style != "默认":
                parts.append(f"→ 互动风格：{p.interaction_style}")

            # 最近发言摘要
            recent = p.recent_texts[-3:] if p.recent_texts else []
            if recent:
                seen = set()
                unique = []
                for t in reversed(recent):
                    t_s = t.strip()
                    if t_s and t_s not in seen:
                        seen.add(t_s)
                        unique.append(t_s)
                    if len(unique) >= 3:
                        break
                if unique:
                    parts.append(f"最近说：{' | '.join(reversed(unique))}")

            lines.append(" ".join(parts))

        lines.append("")
        lines.append("根据画像信息，对不同类型的观众使用不同的回应方式：")
        lines.append("- 铁粉/送礼观众 → 可以撒娇、亲昵、点名互动")
        lines.append("- 活跃观众 → 热情回应、延展话题")
        lines.append("- 新观众/低频用户 → 友善引导、多欢迎")
        lines.append("- 所有回应都应符合猫娘虚拟主播的角色设定")

        return "\n".join(lines)

    def get_audience_summary(self, max_count: int = 5) -> str:
        """生成观众摘要文本（供 LLM 分析使用）

        兼容 DanmakuMemory.get_audience_summary() 接口。
        """
        active = self._get_active_profiles(max_count)
        if not active:
            return ""

        lines = ["活跃观众画像："]
        for p in active:
            tags = []
            if p.is_regular:
                tags.append("常客")
            if p.is_vip:
                tags.append("大股东")
            if p.top_topic:
                tags.append(f"常聊: {p.top_topic}")
            if p.note:
                tags.append(f"备注: {p.note[:15]}")
            tag_str = f"（{'，'.join(tags)}）" if tags else ""
            lines.append(f"- @{p.get_display_name()}{tag_str} 发言{p.message_count}次")
            if p.interaction_style != "默认":
                lines[-1] += f" 风格:{p.interaction_style}"
        return "\n".join(lines)

    def get_active_users(self, max_count: int = 10) -> list[UserRecord]:
        """获取活跃用户列表"""
        return self._get_active_profiles(max_count)

    def get_user_by_uid(self, uid: int) -> Optional[UserRecord]:
        """通过 UID 查找用户"""
        key = str(uid) if uid > 0 else ""
        return self._profiles.get(key)

    def get_user_by_uname(self, uname: str) -> Optional[UserRecord]:
        """通过昵称查找用户"""
        key = self._find_by_uname(uname)
        return self._profiles.get(key) if key else None

    def update_style(self, uname: str, style: str):
        """更新互动风格"""
        self.update_interaction_style(uname, style)

    def update_interaction_style(self, uname: str, style: str):
        """更新互动风格"""
        key = self._find_by_uname(uname)
        if key:
            self._profiles[key].interaction_style = style

    # ── 持久化 ──────────────────────────────────────────────

    async def save(self):
        """序列化到 JSON"""
        if not self._data_dir:
            return
        self._data_dir.mkdir(parents=True, exist_ok=True)
        path = self._data_dir / "user_records.json"
        data = {
            "profiles": {k: asdict(v) for k, v in self._profiles.items()},
            "activity_order": self._activity_order,
        }
        import asyncio
        await asyncio.to_thread(
            lambda: path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        )
        logger.info(f"已保存 {len(self._profiles)} 个用户记录")

    async def load(self):
        """从 JSON 恢复"""
        if not self._data_dir:
            return
        path = self._data_dir / "user_records.json"
        if not path.exists():
            # 迁移旧版 user_profiles.json
            legacy = self._data_dir / "user_profiles.json"
            if legacy.exists():
                try:
                    import shutil
                    shutil.copy2(legacy, path)
                    logger.info(f"已从 {legacy.name} 迁移到 {path.name}")
                except Exception as exc:
                    logger.warning(f"迁移旧版用户记录失败: {exc}")
            else:
                return
        try:
            import asyncio
            raw = await asyncio.to_thread(
                lambda: json.loads(path.read_text(encoding="utf-8"))
            )
            self._profiles.clear()
            all_fields = set(UserRecord.__dataclass_fields__)
            for k, v in raw.get("profiles", {}).items():
                filtered = {fk: fv for fk, fv in v.items() if fk in all_fields}
                self._profiles[k] = UserRecord(**filtered)
            self._activity_order = raw.get("activity_order", [])
            logger.info(f"已恢复 {len(self._profiles)} 个用户记录")
        except Exception as e:
            logger.warning(f"加载用户记录失败: {e}")

    # ── 统计 ──────────────────────────────────────────────

    def get_stats(self) -> dict:
        """获取统计"""
        profiles = self._profiles.values()
        return {
            "total_users": len(self._profiles),
            "active_users": len(self._activity_order),
            "regular_count": sum(1 for p in profiles if p.is_regular),
            "vip_count": sum(1 for p in profiles if p.is_vip),
            "blocked_count": sum(1 for p in profiles if p.is_blocked),
            "cared_count": sum(1 for p in profiles if p.is_cared),
            "total_gifts": sum(p.gift_count for p in profiles),
            "total_messages": sum(p.message_count for p in profiles),
        }

    # ── 内部方法 ──────────────────────────────────────────

    def _get_or_create(self, key: str, uid: int, uname: str) -> UserRecord:
        """获取或创建用户记录"""
        if key in self._profiles:
            return self._profiles[key]

        # 超过上限时剔除最久没活动的
        if len(self._profiles) >= self._max_active and self._activity_order:
            oldest = self._activity_order.pop()
            self._profiles.pop(oldest, None)

        profile = UserRecord(key=key, uid=uid, uname=uname)
        self._profiles[key] = profile
        return profile

    def _find_by_uname(self, uname: str) -> Optional[str]:
        """通过 uname 查找 key"""
        if not uname:
            return None
        for key, p in self._profiles.items():
            if p.uname == uname:
                return key
        return None

    def _touch(self, key: str):
        """将 key 移到活动列表最前 (LRU)"""
        if key in self._activity_order:
            self._activity_order.remove(key)
        self._activity_order.insert(0, key)

    def _get_active_profiles(self, max_count: int = 10) -> list[UserRecord]:
        """按活跃度排序返回前 N 个画像（至少发过 2 条）"""
        order = self._activity_order[:max_count]
        result = []
        for key in order:
            p = self._profiles.get(key)
            if p and p.message_count >= 2:
                result.append(p)
        return result
