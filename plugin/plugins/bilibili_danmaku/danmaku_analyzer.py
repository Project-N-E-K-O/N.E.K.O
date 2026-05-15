"""
弹幕分析流水线

职责：
- 接收聚合后的弹幕批次
- 运行分析流水线：话题聚类 → 情绪分析 → 节奏检测 → 趋势判断
- 每步支持 LLM 正常 + 本地降级
- 输出 IntelligenceCard 列表

设计原则：
- 每步分析独立降级，互不影响
- 降级产物数据结构与正常产物一致
- 不直接调 LLM，通过回调函数注入（方便测试和替换）
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from .aggregator import BatchedDanmaku
from .danmaku_memory import DanmakuMemory
from .plugin_utils import extract_json
from .intelligence_card import (
    IntelligenceCard,
    CARD_TYPE_TOPIC_REPORT,
    CARD_TYPE_RHYTHM_ALERT,
    CARD_TYPE_INTERACTION_SUGGESTION,
    make_topic_card,
    make_rhythm_card,
    make_silent_card,
)

logger = logging.getLogger(__name__)

# 简单情绪词典（本地降级用）
_POSITIVE_WORDS = {
    "可爱", "好看", "好听", "喜欢", "爱了", "好棒", "厉害", "牛",
    "漂亮", "美", "赞", "优秀", "大神", "好活", "笑了", "哈哈哈",
    "哈哈", "hhh", "笑死", "好耶", "nice", "wow", "awsl", "老婆",
    "贴心", "温柔", "有趣", "好喜欢", "prpr", "舔屏",
}

_NEGATIVE_WORDS = {
    "无聊", "没意思", "不行", "不好", "难看", "难听", "卡", "延迟",
    "退钱", "垃圾", "菜", "弱", "不看了", "走了", "关了", "没活",
    "尬", "油腻", "嘁", "切", "无语", "救命", "受不了", "吐了",
}


class DanmakuAnalyzer:
    """
    弹幕分析流水线

    每个分析步骤都有两种模式：
    - llm_*: 调用 LLM 分析
    - local_*: 本地规则分析（降级）
    """

    def __init__(
        self,
        memory: DanmakuMemory,
        llm_call: Optional[Callable[[str, list[dict]], Optional[str]]] = None,
    ):
        """
        Args:
            memory: 弹幕记忆系统
            llm_call: LLM 调用回调，签名 async (system_prompt, messages) -> str | None
                    为 None 时所有分析步骤直接使用本地降级
        """
        self._memory = memory
        self._llm_call = llm_call

    # ── 话题分析 ──────────────────────────────────────────────

    async def analyze_topics(
        self,
        batch: BatchedDanmaku,
    ) -> Optional[IntelligenceCard]:
        """
        话题分析：识别当前窗口的核心讨论主题
        优先 LLM，降级到关键词频率分析
        """
        if not batch.entries:
            return None

        texts = [e.text for e in batch.entries]

        # 1. LLM 分析
        if self._llm_call:
            try:
                topics = await self._llm_analyze_topics(texts, batch.total_count)
                if topics:
                    return topics
            except Exception as e:
                logger.debug(f"LLM话题分析失败，降级到本地: {e}")

        # 2. 本地降级
        return self._local_analyze_topics(texts, batch.total_count)

    async def _llm_analyze_topics(
        self,
        texts: list[str],
        total_count: int,
    ) -> Optional[IntelligenceCard]:
        """LLM 话题分析"""
        system_prompt = (
            "你是 N.E.K.O 弹幕情报处理系统的话题分析模块。"
            "不要角色扮演，不要表达情绪，不要使用口语化表达。"
            "你的任务：从弹幕列表中提取核心讨论主题。"
            "返回严格 JSON 格式："
            '{"topic": "主题名称", "keywords": ["关键词1", "关键词2"], '
            '"sentiment": 0.5, "summary": "一句话描述", "suggestion": "建议（可选）"}'
            "sentiment 范围 -1.0~1.0，正数为积极，负数为消极。"
        )
        # 采样弹幕文本（避免 token 太多）
        sample_texts = texts[:50]
        danmaku_block = "\n".join(f"- {t}" for t in sample_texts)
        user_prompt = (
            f"以下是最近 {total_count} 条弹幕中的 {len(sample_texts)} 条样本：\n\n"
            f"{danmaku_block}\n\n"
            f"请分析核心讨论主题，输出 JSON。"
            f"如果没有明确主题，输出空 JSON: {{\"topic\": \"\", \"keywords\": [], \"sentiment\": 0, \"summary\": \"\"}}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        raw = await self._llm_call("analyze_topics", messages)
        if not raw:
            return None

        # 解析 JSON
        import json
        try:
            # 尝试从文本中提取 JSON
            data = extract_json(raw)
            if not data:
                return None

            topic = data.get("topic", "").strip()
            if not topic:
                return None

            keywords = data.get("keywords", [])
            sentiment = float(data.get("sentiment", 0))
            summary = data.get("summary", f"观众在讨论{topic}")
            suggestion = data.get("suggestion", "")

            # 更新记忆
            self._memory.record_topic(
                topic_id=topic,
                keywords=set(keywords),
                sentiment=sentiment,
                window_count=len(texts),
            )

            return make_topic_card(
                summary=summary,
                keywords=keywords,
                sentiment=sentiment,
                mention_count=len(texts),
                suggestion=suggestion,
                priority=6 if sentiment < -0.5 or sentiment > 0.7 else 5,
            )
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.debug(f"解析LLM话题输出失败: {e}")
            return None

    def _local_analyze_topics(
        self,
        texts: list[str],
        total_count: int,
    ) -> Optional[IntelligenceCard]:
        """本地降级：LLM 不可用时，直接挑几条有意思的弹幕推给猫娘"""
        if not texts:
            return None

        scored: list[tuple[int, str]] = []
        for t in texts:
            score = 0
            t_lower = t.lower()

            # 包含情绪词 → 有内容
            if any(w in t_lower for w in _POSITIVE_WORDS):
                score += 3
            if any(w in t_lower for w in _NEGATIVE_WORDS):
                score += 2

            # 包含疑问 → 可能在互动
            if any(q in t for q in ("吗", "？", "什么", "怎么", "为啥", "为什么", "啥", "哪", "?")):
                score += 2

            # 包含 B站 表情 → 活跃
            if "[" in t and "]" in t:
                score += 1

            # 较长 → 更有内容
            if len(t) > 8:
                score += 1
            if len(t) > 15:
                score += 1

            scored.append((score, t))

        # 按分数降序，选前 3 条
        scored.sort(key=lambda x: -x[0])
        selected = [t for _, t in scored[:3]]

        if not selected:
            return None

        # 格式化：直接展示弹幕原文
        if len(selected) == 1:
            summary = f"观众: {selected[0]}"
        else:
            summary = "\n".join(f"· {t}" for t in selected)

        return IntelligenceCard(
            card_type=CARD_TYPE_INTERACTION_SUGGESTION,
            priority=5,
            summary=summary,
            suggestion="可以跟观众说说",
            degraded=True,
            diagnostic="local_fallback: llm_unavailable",
            batch_info={"total_danmaku": total_count, "selected": len(selected)},
        )

    # ── 节奏检测（纯本地规则，不调 LLM）────────────────────────

    async def detect_rhythm(
        self,
        batch: BatchedDanmaku,
    ) -> Optional[IntelligenceCard]:
        """
        弹幕节奏检测

        检测指标：
        - 密度突变：当前窗口密度 vs 历史平均
        - 刷屏行为：短时间内大量重复内容
        - 情绪极化：负面情绪超过阈值
        """
        density = batch.total_count
        density_status = self._memory.get_density_status()

        # 密度突变检测
        avg = density_status.get("avg", 0)
        if avg > 0 and density > avg * 3 and density > 20:
            return make_rhythm_card(
                summary=f"弹幕突然增多，密度是平时的{density/avg:.0f}倍",
                density=density,
                suggestion="可以关注一下直播间发生了什么",
                priority=6,
            )

        # 极端高密度
        if density > 60:
            return make_rhythm_card(
                summary=f"弹幕刷屏中，每秒{density//15}条",
                density=density,
                suggestion="弹幕刷得太快，不用逐条说",
                priority=7,
            )

        return None

    # ── 全流水线 ──────────────────────────────────────────────

    async def run_pipeline(
        self,
        batch: BatchedDanmaku,
    ) -> list[IntelligenceCard]:
        """
        运行全部分析流水线

        Returns:
            情报卡片列表，可能为空
        """
        cards: list[IntelligenceCard] = []
        if not batch.entries:
            return cards

        # 记录密度
        self._memory.record_window_density(batch.total_count)

        # 1. 节奏检测（最快，不调 LLM）
        rhythm_card = await self.detect_rhythm(batch)
        if rhythm_card:
            cards.append(rhythm_card)

        # 2. 话题分析（可能调 LLM，可能降级）
        topic_card = await self.analyze_topics(batch)
        if topic_card:
            cards.append(topic_card)

        return cards

    def get_stats(self) -> dict:
        return {}
