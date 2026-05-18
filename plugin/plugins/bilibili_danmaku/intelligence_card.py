"""
情报卡片数据模型

情报卡片是背景 LLM 向猫娘传递信息的标准格式。
所有层级（正常/降级）输出同一种结构，猫娘侧无需区分处理。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

# ── 卡片类型枚举 ─────────────────────────────────────────────
CARD_TYPE_AUDIENCE_TREND = "audience_trend"        # 观众趋势
CARD_TYPE_TOPIC_REPORT = "topic_report"            # 话题报告
CARD_TYPE_RHYTHM_ALERT = "rhythm_alert"            # 节奏警报
CARD_TYPE_INTERACTION_SUGGESTION = "interaction_suggestion"  # 互动建议
CARD_TYPE_SILENT_PERIOD = "silent_period"          # 静默提醒
CARD_TYPE_SYSTEM_STANDBY = "system_standby"        # 系统待机通知
CARD_TYPE_SYSTEM_RECOVERY = "system_recovery"      # 系统恢复通知
CARD_TYPE_IMPORTANT_EVENT = "important_event"      # 重要事件(SC/大额礼物)
CARD_TYPE_QUERY_RESPONSE = "query_response"        # 猫娘查询回复


@dataclass
class IntelligenceCard:
    """
    情报卡片 — 背景 LLM 的输出单元

    Attributes:
        card_type:     卡片类型
        priority:      优先级 0-10（0=静默通知, 5=普通, 10=立即打断猫娘）
        summary:       一句话摘要（猫娘可直接念/看）
        details:       结构化详情（按 card_type 有不同的 schema）
        suggestion:    建议行动（可选）
        related_uids:  相关用户 UID 列表
        degraded:      是否为降级产物
        diagnostic:    降级原因（degraded=True 时必填）
        expires_at:    情报时效时间戳（过期后猫娘不应再据此回应）
        created_at:    创建时间戳
        batch_info:    统计信息（本次聚合的弹幕数量、时间跨度等）
        _raw_prompt:   内部调试用，不推送给猫娘
    """
    card_type: str
    priority: int = 5
    summary: str = ""
    details: dict = field(default_factory=dict)
    suggestion: str = ""
    related_uids: list[int] = field(default_factory=list)
    degraded: bool = False
    diagnostic: str = ""
    expires_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    batch_info: dict = field(default_factory=dict)
    trace_id: str = ""

    def __post_init__(self):
        if self.expires_at <= 0:
            self.expires_at = self.created_at + 120  # 默认2分钟过期

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    @property
    def is_urgent(self) -> bool:
        return self.priority >= 8

    def to_dict(self) -> dict:
        """序列化为字典（推送给猫娘时使用）"""
        return {
            "card_type": self.card_type,
            "priority": self.priority,
            "summary": self.summary,
            "details": self.details,
            "suggestion": self.suggestion,
            "related_uids": self.related_uids,
            "degraded": self.degraded,
            "diagnostic": self.diagnostic if self.degraded else "",
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "batch_info": self.batch_info,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IntelligenceCard":
        return cls(
            card_type=data.get("card_type", CARD_TYPE_QUERY_RESPONSE),
            priority=data.get("priority", 5),
            summary=data.get("summary", ""),
            details=data.get("details", {}),
            suggestion=data.get("suggestion", ""),
            related_uids=data.get("related_uids", []),
            degraded=data.get("degraded", False),
            diagnostic=data.get("diagnostic", ""),
            expires_at=data.get("expires_at", 0),
            created_at=data.get("created_at", 0),
            batch_info=data.get("batch_info", {}),
        )

    @staticmethod
    def format_for_catgirl(card: "IntelligenceCard") -> str:
        """将情报卡片格式化为猫娘可直接使用的自然语言"""
        degraded_tag = " [降级信息]" if card.degraded else ""

        if card.card_type == CARD_TYPE_TOPIC_REPORT:
            text = f"📊 观众话题{degraded_tag}：{card.summary}"
            if card.suggestion:
                text += f"\n💡 {card.suggestion}"

        elif card.card_type == CARD_TYPE_AUDIENCE_TREND:
            text = f"👥 观众动态{degraded_tag}：{card.summary}"

        elif card.card_type == CARD_TYPE_RHYTHM_ALERT:
            text = f"⚡ 弹幕节奏{degraded_tag}：{card.summary}"
            if card.suggestion:
                text += f"\n💡 {card.suggestion}"

        elif card.card_type == CARD_TYPE_INTERACTION_SUGGESTION:
            text = f"🎯 互动建议{degraded_tag}：{card.summary}"
            if card.suggestion:
                text += f"\n💡 {card.suggestion}"

        elif card.card_type == CARD_TYPE_SILENT_PERIOD:
            text = f"🔇 {card.summary}"
            if card.suggestion:
                text += f"\n💡 {card.suggestion}"

        elif card.card_type == CARD_TYPE_SYSTEM_STANDBY:
            text = f"💤 {card.summary}"

        elif card.card_type == CARD_TYPE_SYSTEM_RECOVERY:
            text = f"✅ {card.summary}"

        elif card.card_type == CARD_TYPE_IMPORTANT_EVENT:
            text = f"🔔 {card.summary}"

        elif card.card_type == CARD_TYPE_QUERY_RESPONSE:
            text = card.summary

        else:
            text = card.summary

        return text


# ── 快捷工厂函数 ──────────────────────────────────────────────

def make_topic_card(
    summary: str,
    keywords: list[str],
    sentiment: float,
    mention_count: int,
    suggestion: str = "",
    priority: int = 5,
    degraded: bool = False,
    diagnostic: str = "",
) -> IntelligenceCard:
    return IntelligenceCard(
        card_type=CARD_TYPE_TOPIC_REPORT,
        priority=priority,
        summary=summary,
        details={
            "keywords": keywords,
            "sentiment": sentiment,
            "mention_count": mention_count,
        },
        suggestion=suggestion,
        degraded=degraded,
        diagnostic=diagnostic,
    )


def make_rhythm_card(
    summary: str,
    density: int,
    suggestion: str = "",
    priority: int = 7,
    degraded: bool = False,
    diagnostic: str = "",
) -> IntelligenceCard:
    return IntelligenceCard(
        card_type=CARD_TYPE_RHYTHM_ALERT,
        priority=priority,
        summary=summary,
        details={"peak_density": density},
        suggestion=suggestion,
        degraded=degraded,
        diagnostic=diagnostic,
    )


def make_silent_card(
    silent_seconds: float,
    suggestion: str = "",
) -> IntelligenceCard:
    return IntelligenceCard(
        card_type=CARD_TYPE_SILENT_PERIOD,
        priority=2,
        summary=f"直播间已经安静了{int(silent_seconds // 60)}分钟",
        details={"silent_seconds": silent_seconds},
        suggestion=suggestion,
    )


def make_standby_card(reason: str) -> IntelligenceCard:
    return IntelligenceCard(
        card_type=CARD_TYPE_SYSTEM_STANDBY,
        priority=0,
        summary=f"弹幕情报系统进入待机模式: {reason}",
        details={"standby_reason": reason},
    )


def make_recovery_card() -> IntelligenceCard:
    return IntelligenceCard(
        card_type=CARD_TYPE_SYSTEM_RECOVERY,
        priority=1,
        summary="弹幕情报系统已恢复",
    )
