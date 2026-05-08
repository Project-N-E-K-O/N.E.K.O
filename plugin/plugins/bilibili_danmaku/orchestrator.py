"""
引导词生成编排器

功能：
- 接收聚合后的弹幕批次
- 构建 LLM Prompt 并调用 LLM 生成引导词
- LLM 失败/超时时降级为简单统计摘要
- 集成用户画像追踪器，注入观众画像上下文
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

from .aggregator import BatchedDanmaku
from .llm_client import LLMClient
from .user_profile import UserProfileTracker

logger = logging.getLogger(__name__)


class GuidanceOrchestrator:
    """
    引导词编排器

    流程：
    聚合批次 → 提取弹幕文本 → 注入画像上下文 → 调用 LLM → 成功？→ 返回引导词
                                                           ↓ 失败
                                                       降级统计摘要
    """

    def __init__(
        self,
        llm_client: LLMClient,
        knowledge_context: str = "",
        degrade_on_empty: bool = True,
        tracker: Optional[UserProfileTracker] = None,
        neko_name: str = "",
        prompt_template: str = "",
    ):
        """
        Args:
            llm_client: LLM 调用客户端
            knowledge_context: 专属知识库上下文（供 LLM Prompt 使用），支持占位符 {name}
            degrade_on_empty: LLM 失败时是否自动降级
            tracker: 用户画像追踪器（可选），注入后 Prompt 中会包含观众画像
            neko_name: 猫娘/AI 的名字，用于替换占位符 {name}
            prompt_template: 自定义 System Prompt 模板，留空使用默认模板，支持占位符 {name}/{knowledge_context}
        """
        self.llm_client = llm_client
        self.knowledge_context = knowledge_context
        self.degrade_on_empty = degrade_on_empty
        self.tracker = tracker
        self.neko_name = neko_name
        self.prompt_template = prompt_template

        # 统计
        self.total_processed = 0
        self.llm_success = 0
        self.degraded = 0

    # ── 占位符替换 ────────────────────────────────────────────────────────────

    def _fill_placeholders(self, text: str) -> str:
        """将文本中的占位符替换为实际值

        支持的占位符：
          {name}        → 猫娘/AI 名字（neko_name）
          {neko_name}   → 同上
        """
        if not text:
            return text
        replacements = {
            "{name}": self.neko_name or "",
            "{neko_name}": self.neko_name or "",
        }
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        return text

    def _build_knowledge_context(self) -> str:
        """构建完整的上下文（知识库 + 观众画像），并替换占位符"""
        parts = []
        if self.knowledge_context:
            parts.append(self._fill_placeholders(self.knowledge_context))
        if self.tracker:
            profile_ctx = self.tracker.get_profile_context(max_count=10)
            if profile_ctx:
                parts.append("---\n" + profile_ctx)
        return "\n\n".join(parts) if parts else ""

    async def generate(
        self,
        batch: BatchedDanmaku,
    ) -> Optional[str]:
        """
        根据聚合弹幕批次生成引导词

        Args:
            batch: 聚合的弹幕批次

        Returns:
            引导词字符串，极端失败可能返回 None
        """
        if not batch.entries:
            return None

        self.total_processed += 1

        # 提取弹幕文本
        danmaku_texts = [e.text for e in batch.entries]

        # 1. 尝试 LLM 生成
        guidance = await self.llm_client.generate_guidance(
            danmaku_texts=danmaku_texts,
            knowledge_context=self._build_knowledge_context(),
            system_prompt_override=self._fill_placeholders(self.prompt_template) if self.prompt_template else None,
        )

        if guidance:
            self.llm_success += 1
            return guidance

        # 2. LLM 失败，降级
        if self.degrade_on_empty:
            self.degraded += 1
            return self._degrade_summary(batch)

        return None

    async def generate_from_texts(
        self,
        danmaku_texts: list[str],
        total_original_count: int,
    ) -> Optional[str]:
        """
        直接根据弹幕文本列表生成引导词（不依赖 aggregator 的 BatchedDanmaku）

        用于强制推送等场景
        """
        if not danmaku_texts:
            return None

        self.total_processed += 1

        guidance = await self.llm_client.generate_guidance(
            danmaku_texts=danmaku_texts,
            knowledge_context=self._build_knowledge_context(),
            system_prompt_override=self._fill_placeholders(self.prompt_template) if self.prompt_template else None,
        )

        if guidance:
            self.llm_success += 1
            return guidance

        if self.degrade_on_empty:
            self.degraded += 1
            texts_for_stats = danmaku_texts[:30]  # 降级时也只取前30
            return self._simple_stat_summary(texts_for_stats, total_original_count)

        return None

    def _degrade_summary(self, batch: BatchedDanmaku) -> str:
        """降级方案：统计摘要"""
        texts = [e.text for e in batch.entries]
        return self._simple_stat_summary(texts, batch.total_count)

    def _simple_stat_summary(self, texts: list[str], total_count: int) -> str:
        """
        降级统计摘要生成
        - 统计弹幕数量
        - 提取高频词
        - 不使用 jieba，用简单英文分词 + 单字过滤
        """
        if not texts:
            return "(空)"

        # 简单词频统计
        all_words: list[str] = []
        for t in texts:
            # 中文：按单字分割；英文：按空格分割
            words = []
            buffer = ""
            for ch in t:
                if ord(ch) > 127:  # 非 ASCII（中文）
                    if buffer.strip():
                        words.append(buffer.strip().lower())
                        buffer = ""
                    words.append(ch)
                elif ch.isspace():
                    if buffer.strip():
                        words.append(buffer.strip().lower())
                        buffer = ""
                else:
                    buffer += ch
            if buffer.strip():
                words.append(buffer.strip().lower())
            all_words.extend(words)

        # 过滤常见无意义字
        stop_chars = {"的", "了", "是", "在", "我", "你", "他", "她", "它",
                      "们", "这", "那", "不", "就", "也", "还", "都", "要",
                      "有", "和", "与", "对", "把", "被", "让", "给", "上",
                      "下", "来", "去", "没", "吗", "啊", "吧", "呢", "呵",
                      "呀", "哦", "嗯", "哈", "嘿", "哇", "哎", "喂"}

        word_counts = Counter(
            w for w in all_words
            if len(w) > 1 or ord(w) > 127 and w not in stop_chars
        )

        top_words = word_counts.most_common(10)
        keyword_hint = "、".join(f"{w}({c}次)" for w, c in top_words[:5])

        sampled_count = len(texts)

        if total_count > sampled_count:
            note = f"（弹幕较多，从中采样了 {sampled_count} 条）"
        else:
            note = ""

        return (
            f"【弹幕统计摘要】收到 {total_count} 条弹幕{note}。"
            f"弹幕讨论高频词：{keyword_hint}。"
            f"请 AI 结合当前直播内容，围绕以上话题做出自然的回应。"
        )

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "total_processed": self.total_processed,
            "llm_success": self.llm_success,
            "degraded": self.degraded,
            "llm_success_rate": round(
                self.llm_success / max(self.total_processed, 1) * 100, 1
            ),
        }
