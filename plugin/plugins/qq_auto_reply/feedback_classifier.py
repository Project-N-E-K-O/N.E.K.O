from __future__ import annotations

import re
from typing import Any


class QQFeedbackClassifier:
    @staticmethod
    def default_labels() -> list[dict[str, Any]]:
        return [
            {
                "id": "issue",
                "label": "问题",
                "keywords": ["报错", "错误", "异常", "崩溃", "闪退", "卡住", "卡死", "无法", "不能", "没反应", "失效", "bug", "问题"],
                "priority": 100,
            },
            {
                "id": "feedback",
                "label": "反馈",
                "keywords": ["建议", "体验", "不好用", "不方便", "希望", "吐槽", "优化", "改进", "反馈"],
                "priority": 80,
            },
            {
                "id": "mention",
                "label": "点名",
                "keywords": [r"@用户\d+", r"@全体成员", "主人", "在吗", "看到请回复", "麻烦看下"],
                "priority": 60,
            },
        ]

    @classmethod
    def classify(cls, text: str, labels: list[dict[str, Any]] | None = None) -> str:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return "chat"
        candidates = cls._normalize_labels(labels or cls.default_labels())
        matched: list[tuple[int, str, bool]] = []  # (priority, label_id, is_blacklist)
        for label in candidates:
            label_id = str(label.get("id") or "").strip()
            priority = int(label.get("priority") or 0)
            keywords = label.get("keywords") or []
            if not label_id or not isinstance(keywords, list):
                continue
            matched_label = False
            for pattern in keywords:
                pattern_text = str(pattern).strip()
                if not pattern_text:
                    continue
                try:
                    if re.search(pattern_text, normalized, re.IGNORECASE):
                        matched_label = True
                        break
                except re.error:
                    continue
            if matched_label:
                is_blacklist = priority < 0
                matched.append((priority, label_id, is_blacklist))
        if not matched:
            return "chat"
        # 正优先级优先于负优先级（黑名单）
        positive = [(p, lid) for p, lid, bl in matched if not bl]
        if positive:
            positive.sort(key=lambda item: item[0], reverse=True)
            return positive[0][1]
        # 全部是负优先级 → 黑名单
        return "blacklist"

    @classmethod
    def is_blacklisted(cls, text: str, labels: list[dict[str, Any]] | None = None) -> bool:
        """检测消息是否命中黑名单（优先级为负数的标签）"""
        return cls.classify(text, labels) == "blacklist"

    @staticmethod
    def _normalize_labels(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in labels:
            if not isinstance(item, dict):
                continue
            label_id = str(item.get("id") or "").strip()
            if not label_id or label_id in seen_ids:
                continue
            keywords = item.get("keywords")
            if not isinstance(keywords, list):
                keywords = []
            try:
                priority = int(item.get("priority") or 0)
            except Exception:
                priority = 0
            normalized.append({
                "id": label_id,
                "label": str(item.get("label") or label_id),
                "keywords": [str(keyword).strip() for keyword in keywords if str(keyword).strip()],
                "priority": priority,
            })
            seen_ids.add(label_id)
        return normalized
