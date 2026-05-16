"""
弹幕房间级记忆系统

维护房间级别的记忆维度（不涉及单个用户）：
1. 话题时间线 — 追踪讨论主题的生命周期
2. 弹幕吞吐 — 密度统计与趋势
3. 互动节奏 — 静默时长 / 提问比例

所有记忆有过期机制，定期清理。

用户画像相关功能已迁移到 user_profile.py 的 UserRecordManager。
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────
_DEFAULT_TOPIC_IDLE_TIMEOUT = 300    # 话题 5 分钟无提及则标记非活跃
_DEFAULT_MAX_ACTIVE_TOPICS = 20      # 最多同时追踪 20 个话题
_DEFAULT_MAX_DENSITY_HISTORY = 20    # 密度历史记录窗口数


# ========================================================================
# 话题时间线
# ========================================================================

@dataclass
class TopicTimeline:
    """话题时间线 — 追踪一个话题的完整生命周期"""
    topic_id: str
    keywords: set = field(default_factory=set)
    first_seen: float = 0.0
    last_seen: float = 0.0
    mention_count: int = 0
    sentiment_sum: float = 0.0
    peak_density: int = 0
    is_active: bool = True
    window_counts: list[int] = field(default_factory=list)

    @property
    def avg_sentiment(self) -> float:
        return self.sentiment_sum / max(self.mention_count, 1)

    @property
    def lifetime(self) -> float:
        return self.last_seen - self.first_seen

    def is_idle(self, now: float, timeout: float = _DEFAULT_TOPIC_IDLE_TIMEOUT) -> bool:
        return (now - self.last_seen) > timeout


# ========================================================================
# 房间级记忆系统
# ========================================================================

class DanmakuMemory:
    """
    弹幕房间级记忆系统

    职责：
    - 维护话题时间线（不涉及用户身份）
    - 维护弹幕密度统计
    - 维护互动节奏（静默时长、提问比例）
    - 提供查询接口供分析流水线使用
    - 定期清理过期话题

    注意：用户画像相关功能已迁移到 UserRecordManager。
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        topic_idle_timeout: float = _DEFAULT_TOPIC_IDLE_TIMEOUT,
        max_active_topics: int = _DEFAULT_MAX_ACTIVE_TOPICS,
    ):
        self._data_dir = Path(data_dir) if data_dir else None
        self._topic_idle_timeout = topic_idle_timeout
        self._max_active_topics = max_active_topics

        # 话题时间线
        self._topics: dict[str, TopicTimeline] = {}

        # 交互模式追踪（纯计数，不关联用户）
        self._question_count: int = 0
        self._greeting_count: int = 0

        # 弹幕吞吐统计
        self._window_density: list[int] = []
        self._max_density_history = _DEFAULT_MAX_DENSITY_HISTORY

        # 静默计时
        self.last_danmaku_time: float = 0.0

    # ── 记录接口 ──────────────────────────────────────────────

    def record_danmaku(self, uid: int, uname: str, text: str):
        """
        记录一条弹幕的房间级指标

        注意：仅跟踪房间级指标（静默计时、提问检测）。
        用户画像记录已迁移到 UserRecordManager.record()。
        """
        now = time.time()
        self.last_danmaku_time = now

        # 检测提问
        if "?" in text or "？" in text:
            self._question_count += 1

    def record_gift(self, uname: str, price: float):
        """
        仅更新静默计时。

        用户送礼记录已迁移到 UserRecordManager.record_gift()。
        """
        self.last_danmaku_time = time.time()

    def record_topic(
        self,
        topic_id: str,
        keywords: set[str],
        sentiment: float = 0.0,
        window_count: int = 1,
    ):
        """记录一个话题在本窗口的出现"""
        now = time.time()
        if topic_id not in self._topics:
            self._topics[topic_id] = TopicTimeline(
                topic_id=topic_id,
                keywords=set(keywords),
                first_seen=now,
                last_seen=now,
            )
            if len(self._topics) > self._max_active_topics * 2:
                self._trim_topics()
        topic = self._topics[topic_id]
        topic.keywords.update(keywords)
        topic.last_seen = now
        topic.mention_count += window_count
        topic.sentiment_sum += sentiment * window_count
        topic.window_counts.append(window_count)
        if window_count > topic.peak_density:
            topic.peak_density = window_count
        topic.is_active = True

    def record_window_density(self, count: int):
        """记录一个窗口的弹幕密度"""
        self._window_density.append(count)
        if len(self._window_density) > self._max_density_history:
            self._window_density = self._window_density[-self._max_density_history:]

    # ── 查询接口 ──────────────────────────────────────────────

    def get_active_topics(self, now: float | None = None) -> list[TopicTimeline]:
        """获取当前活跃话题列表"""
        if now is None:
            now = time.time()
        active = []
        for t in self._topics.values():
            if not t.is_idle(now, self._topic_idle_timeout) and t.is_active:
                active.append(t)
            elif t.is_idle(now, self._topic_idle_timeout):
                t.is_active = False
        active.sort(key=lambda t: t.mention_count, reverse=True)
        return active[:self._max_active_topics]

    def get_hot_topic(self) -> Optional[TopicTimeline]:
        """获取当前最热话题"""
        active = self.get_active_topics()
        return active[0] if active else None

    def get_topic_summary(self) -> str:
        """生成话题摘要文本（供 LLM 分析使用）"""
        active = self.get_active_topics()
        if not active:
            return ""
        lines = ["当前话题："]
        for t in active[:5]:
            kw = "、".join(list(t.keywords)[:5])
            if t.avg_sentiment > 0.3:
                sentiment_str = "积极"
            elif t.avg_sentiment < -0.3:
                sentiment_str = "消极"
            else:
                sentiment_str = "中性"
            lines.append(f"- [{sentiment_str}] {kw}（提及{t.mention_count}次）")
        return "\n".join(lines)

    def get_density_status(self) -> dict:
        """获取弹幕密度状态"""
        if not self._window_density:
            return {"current": 0, "avg": 0, "trend": "stable"}
        current = self._window_density[-1]
        avg = sum(self._window_density) / len(self._window_density)
        if len(self._window_density) >= 3:
            recent = sum(self._window_density[-3:]) / 3
            if recent > avg * 1.5:
                trend = "rising"
            elif recent < avg * 0.5:
                trend = "falling"
            else:
                trend = "stable"
        else:
            trend = "stable"
        return {"current": current, "avg": round(avg, 1), "trend": trend}

    def get_silent_seconds(self, now: float | None = None) -> float:
        """获取静默时长"""
        if now is None:
            now = time.time()
        if self.last_danmaku_time == 0:
            return 0.0
        return now - self.last_danmaku_time

    def get_question_ratio(self, total_danmaku: int) -> float:
        """获取提问比例"""
        if total_danmaku == 0:
            return 0.0
        return self._question_count / max(total_danmaku, 1)

    # ── 内部方法 ──────────────────────────────────────────────

    def _trim_topics(self):
        """清理最久没活跃的话题"""
        sorted_topics = sorted(
            self._topics.values(),
            key=lambda t: t.last_seen,
        )
        for t in sorted_topics[:len(sorted_topics) // 2]:
            if not t.is_active:
                self._topics.pop(t.topic_id, None)

    def cleanup(self, now: float | None = None):
        """定期清理过期话题"""
        if now is None:
            now = time.time()

        # 清理长时间无提及的话题
        idle = [
            tid for tid, t in self._topics.items()
            if t.is_idle(now, self._topic_idle_timeout * 3)
        ]
        for tid in idle:
            del self._topics[tid]

        # 重置本轮计数器
        self._question_count = 0

    # ── 持久化 ──────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "topics": {
                tid: {
                    "topic_id": t.topic_id,
                    "keywords": list(t.keywords),
                    "first_seen": t.first_seen,
                    "last_seen": t.last_seen,
                    "mention_count": t.mention_count,
                    "sentiment_sum": t.sentiment_sum,
                    "peak_density": t.peak_density,
                }
                for tid, t in self._topics.items()
            },
            "window_density": self._window_density,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DanmakuMemory":
        mem = cls()
        for tid, tdata in data.get("topics", {}).items():
            mem._topics[tid] = TopicTimeline(
                topic_id=tdata.get("topic_id", tid),
                keywords=set(tdata.get("keywords", [])),
                first_seen=tdata.get("first_seen", 0),
                last_seen=tdata.get("last_seen", 0),
                mention_count=tdata.get("mention_count", 0),
                sentiment_sum=tdata.get("sentiment_sum", 0),
                peak_density=tdata.get("peak_density", 0),
            )
        mem._window_density = list(data.get("window_density", []))
        return mem

    async def save(self):
        if not self._data_dir:
            return
        self._data_dir.mkdir(parents=True, exist_ok=True)
        path = self._data_dir / "danmaku_memory.json"
        import asyncio
        await asyncio.to_thread(
            lambda: path.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        )

    async def load(self):
        if not self._data_dir:
            return
        path = self._data_dir / "danmaku_memory.json"
        if not path.exists():
            return
        try:
            import asyncio
            raw = await asyncio.to_thread(
                lambda: json.loads(path.read_text(encoding="utf-8"))
            )
            loaded = self.from_dict(raw)
            self._topics = loaded._topics
            self._window_density = loaded._window_density
            logger.info(f"已恢复话题记忆: {len(self._topics)} 个话题")
        except Exception as e:
            logger.warning(f"加载话题记忆失败: {e}")

    def get_stats(self) -> dict:
        return {
            "topic_count": len(self._topics),
            "active_topics": len(self.get_active_topics()),
            "silent_seconds": int(self.get_silent_seconds()),
            "question_count": self._question_count,
        }
