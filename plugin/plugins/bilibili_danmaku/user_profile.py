"""
轻量级用户画像追踪器

功能：
- 记录每个发弹幕用户的历史发言、频率
- 追踪送礼记录
- 生成 LLM Prompt 可用的画像上下文
- 定期持久化到 JSON（防断线丢失）
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 单用户画像
@dataclass
class UserProfile:
    """单个用户的画像数据"""
    key: str               # 标识键: str(uid) 或 user_name 兜底
    uid: int = 0
    uname: str = ""
    message_count: int = 0
    recent_texts: List[str] = field(default_factory=list)
    last_seen: float = 0.0
    has_gifted: bool = False
    gift_total: float = 0.0
    gift_count: int = 0
    response_style: str = "默认"  # LLM 推测的回应风格


class UserProfileTracker:
    """
    用户画像追踪器

    用法:
        tracker = UserProfileTracker(data_dir=...)
        tracker.record(uid=12345, uname="小明", text="你好")
        tracker.record_gift(uname="小明", price=20)
        ctx = tracker.get_profile_context()  # -> 供 LLM Prompt 使用

    持久化:
        await tracker.save()
        await tracker.load()
    """

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        max_recent: int = 15,
        max_active: int = 50,
    ):
        self._profiles: Dict[str, UserProfile] = {}
        self._data_dir = data_dir
        self._max_recent = max_recent          # 每个人保留最近发言数
        self._max_active = max_active          # 最多跟踪多少人

        # 简单 LRU 顺序（最近有活动的排前面）
        self._activity_order: List[str] = []

    # ── 公开记录接口 ──────────────────────────────────────

    def record(
        self,
        uid: int,
        uname: str,
        text: str,
        has_gifted: bool = False,
        gift_amount: float = 0.0,
    ):
        """记录一条用户活动"""
        key = str(uid) if uid > 0 else uname
        if not key:
            return

        now = time.time()
        profile = self._profiles.get(key)

        if profile is None:
            # 超过上限时剔除最久没活动的
            if len(self._profiles) >= self._max_active:
                if self._activity_order:
                    oldest = self._activity_order.pop()
                    self._profiles.pop(oldest, None)
            profile = UserProfile(key=key, uid=uid, uname=uname)
            self._profiles[key] = profile

        # 更新信息
        profile.uid = uid
        profile.uname = uname
        profile.message_count += 1
        profile.last_seen = now
        profile.recent_texts.append(text)
        if len(profile.recent_texts) > self._max_recent:
            profile.recent_texts = profile.recent_texts[-self._max_recent:]

        if has_gifted:
            profile.has_gifted = True
            profile.gift_total += gift_amount
            profile.gift_count += 1

        # 移到活动列表前端
        self._touch(key)

    def record_gift(self, uname: str, price: float):
        """记录送礼（无 uid 时用 uname 标识）"""
        # 尝试通过 uname 找到已有 profile
        key = self._find_by_uname(uname)
        if key:
            profile = self._profiles[key]
            profile.has_gifted = True
            profile.gift_total += price
            profile.gift_count += 1
            self._touch(key)
        else:
            # 新建一个仅以 uname 标识的 profile
            self.record(uid=0, uname=uname, text="[送礼]", has_gifted=True, gift_amount=price)

    # ── Prompt 上下文生成 ──────────────────────────────

    def get_profile_context(self, max_count: int = 10) -> str:
        """
        生成供 LLM Prompt 注入的画像上下文

        Returns:
            格式化字符串，描述活跃观众画像
        """
        active = self._get_active_profiles(max_count)
        if not active:
            return ""

        lines = ["观众画像信息（发言较多的活跃观众）："]
        for p in active:
            parts = [f"- @{p.uname}"]

            # 身份标签
            tags = []
            if p.message_count >= 50:
                tags.append("铁粉")
            elif p.message_count >= 10:
                tags.append("活跃观众")
            if p.has_gifted:
                tags.append(f"送礼×{p.gift_count} (¥{p.gift_total:.0f})")
            if p.gift_count >= 3:
                tags.append("大股东")
            if tags:
                parts.append(f"（{'，'.join(tags)}）")

            # 回应风格（非默认才显示）
            if p.response_style and p.response_style != "默认":
                parts.append(f"→ 回应风格：{p.response_style}")

            # 最近发言摘要
            recent = p.recent_texts[-3:] if p.recent_texts else []
            if recent:
                # 去重 + 截断
                seen = set()
                unique = []
                for t in reversed(recent):
                    t_stripped = t.strip()
                    if t_stripped and t_stripped not in seen:
                        seen.add(t_stripped)
                        unique.append(t_stripped)
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

    def update_style(self, uname: str, style: str):
        """更新某用户的回应风格（供 LLM 反馈使用）"""
        key = self._find_by_uname(uname)
        if key:
            self._profiles[key].response_style = style

    # ── 持久化 ──────────────────────────────────────────

    async def save(self):
        """序列化到 JSON"""
        if not self._data_dir:
            return
        self._data_dir.mkdir(parents=True, exist_ok=True)
        path = self._data_dir / "user_profiles.json"
        data = {
            "profiles": {k: asdict(v) for k, v in self._profiles.items()},
            "activity_order": self._activity_order,
        }
        import asyncio
        await asyncio.to_thread(
            lambda: path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        )

    async def load(self):
        """从 JSON 恢复"""
        if not self._data_dir:
            return
        path = self._data_dir / "user_profiles.json"
        if not path.exists():
            return
        try:
            import asyncio
            raw = await asyncio.to_thread(lambda: json.loads(path.read_text(encoding="utf-8")))
            self._profiles.clear()
            for k, v in raw.get("profiles", {}).items():
                self._profiles[k] = UserProfile(**v)
            self._activity_order = raw.get("activity_order", [])
            logger.info(f"已恢复 {len(self._profiles)} 个用户画像")
        except Exception as e:
            logger.warning(f"加载用户画像失败: {e}")

    # ── 内部方法 ────────────────────────────────────────

    def _touch(self, key: str):
        """将 key 移到活动列表最前"""
        if key in self._activity_order:
            self._activity_order.remove(key)
        self._activity_order.insert(0, key)

    def _get_active_profiles(self, max_count: int = 10) -> List[UserProfile]:
        """按活跃度排序返回前 N 个画像"""
        order = self._activity_order[:max_count]
        result = []
        for key in order:
            p = self._profiles.get(key)
            if p and p.message_count >= 2:  # 至少发过2条
                result.append(p)
        return result

    def _find_by_uname(self, uname: str) -> Optional[str]:
        """通过 uname 查找 profile key（无 uid 场景使用）"""
        if not uname:
            return None
        # 优先精确匹配
        for key, p in self._profiles.items():
            if p.uname == uname:
                return key
        return None

    def get_stats(self) -> dict:
        """获取统计"""
        return {
            "total_profiles": len(self._profiles),
            "active_in_order": len(self._activity_order),
            "profiles_with_gifts": sum(1 for p in self._profiles.values() if p.has_gifted),
        }
