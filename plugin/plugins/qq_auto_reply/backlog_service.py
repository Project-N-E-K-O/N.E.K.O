from __future__ import annotations

import time
from typing import Any

from .backlog_models import QQBacklogMessage
from .feedback_classifier import QQFeedbackClassifier
from .summary_builder import QQSummaryBuilder


class QQBacklogService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def _label_defs(self) -> list[dict[str, Any]]:
        return list((self.plugin._qq_settings or {}).get("backlog_labels") or [])

    def _label_map(self) -> dict[str, str]:
        return {
            str(item.get("id") or "").strip(): str(item.get("label") or item.get("id") or "").strip()
            for item in self._label_defs()
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }

    def _label_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "id": str(item.get("id") or "").strip(),
                "label": str(item.get("label") or item.get("id") or "").strip(),
                "priority": int(item.get("priority") or 0),
            }
            for item in self._label_defs()
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]

    async def record_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("message_type") or "").strip()
        sender_id = str(message.get("user_id") or "").strip()
        if not sender_id:
            return
        message_text = self.plugin._sanitize_message_text(str(message.get("content") or "").strip())
        if not message_text:
            return
        sender_name = str(message.get("user_nickname") or sender_id).strip() or sender_id
        message_id = str(message.get("message_id") or "")
        timestamp = int(message.get("timestamp") or 0)
        # 黑名单优先检测：命中负优先级标签 → 不记录
        if QQFeedbackClassifier.is_blacklisted(message_text, self._label_defs()):
            return
        category = QQFeedbackClassifier.classify(message_text, self._label_defs())
        # 点名标签只用 is_at_bot 判定，不用 @用户\d+ 正则（避免 @任何人都会计入）
        if message.get("is_at_bot"):
            if category != "mention":
                category = "mention"
        elif category == "mention":
            category = "chat"

        if message_type == "private":
            permission_level = self.plugin.permission_mgr.get_permission_level(sender_id) if self.plugin.permission_mgr else "none"
            if permission_level == "none":
                return
            conversation_key = self.plugin._build_session_key(sender_id=sender_id, is_group=False)
            backlog_message = QQBacklogMessage(
                conversation_key=conversation_key,
                conversation_type="private",
                source_id=sender_id,
                sender_id=sender_id,
                sender_name=sender_name,
                text=message_text,
                message_id=message_id,
                timestamp=timestamp,
                permission_level=permission_level,
                category=category,
                raw=dict(message.get("raw") or {}),
            )
            display_name = self.plugin.permission_mgr.get_nickname(sender_id) if self.plugin.permission_mgr else None
            await self.plugin.backlog_store.append_message(
                backlog_message,
                conversation_display_name=display_name or sender_name or sender_id,
            )
            return

        if message_type != "group":
            return
        group_id = str(message.get("group_id") or "").strip()
        if not group_id:
            return
        group_level = self.plugin.group_permission_mgr.get_group_level(group_id) if self.plugin.group_permission_mgr else "none"
        if group_level == "none":
            return
        conversation_key = self.plugin._build_backlog_conversation_key(sender_id=sender_id, is_group=True, group_id=group_id)
        backlog_message = QQBacklogMessage(
            conversation_key=conversation_key,
            conversation_type="group",
            source_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=message_text,
            message_id=message_id,
            timestamp=timestamp,
            group_id=group_id,
            group_level=group_level,
            is_at_bot=bool(message.get("is_at_bot")),
            category=category,
            raw=dict(message.get("raw") or {}),
        )
        display_name = self.plugin.permission_mgr.get_nickname(sender_id) if self.plugin.permission_mgr else None
        await self.plugin.backlog_store.append_message(
            backlog_message,
            conversation_display_name=display_name or sender_name or sender_id,
            group_display_name=f"QQ群 {group_id}",
        )

    async def get_summary_payload(self) -> dict[str, Any]:
        state = await self.plugin.backlog_store.load()
        summaries = QQSummaryBuilder.build_all_group_summaries(
            state,
            label_map=self._label_map(),
            configured_groups=self.plugin.group_permission_mgr.list_groups() if self.plugin.group_permission_mgr else [],
        )
        label_counts: dict[str, int] = {}
        for item in summaries:
            for label_id, count in dict(item.get("label_counts") or {}).items():
                normalized_label_id = str(label_id or "").strip()
                if not normalized_label_id:
                    continue
                label_counts[normalized_label_id] = label_counts.get(normalized_label_id, 0) + int(count or 0)
        return {
            "groups": summaries,
            "group_count": len(summaries),
            "unread_count": sum(int(item.get("unread_count") or 0) for item in summaries),
            "label_counts": label_counts,
            "labels": self._label_payload(),
        }

    async def get_group_detail_payload(self, group_id: str) -> dict[str, Any]:
        detail = await self.plugin.backlog_store.get_group_detail(group_id)
        detail["labels"] = self._label_payload()
        return detail

    async def mark_group_reviewed_payload(self, group_id: str) -> dict[str, Any]:
        state = await self.plugin.backlog_store.mark_group_reviewed(group_id)
        configured_groups = self.plugin.group_permission_mgr.list_groups() if self.plugin.group_permission_mgr else []
        summaries = QQSummaryBuilder.build_all_group_summaries(state, configured_groups=configured_groups)
        return {
            "status": "reviewed",
            "group_id": group_id,
            "groups": summaries,
        }

    async def maybe_notify_summary(self, *, group_id: str) -> None:
        if not self.plugin._admin_qq:
            return
        state = await self.plugin.backlog_store.load()
        groups = dict(state.get("groups") or {})
        conversations = dict(state.get("conversations") or {})
        group = groups.get(group_id)
        if not isinstance(group, dict):
            return
        label_counts = dict(group.get("label_counts") or {})
        label_map = self._label_map()
        # 只推送关键词标签（非 chat 且非 mention 或 mention 但确实是 @bot 触发的）
        issue_counts = {
            lid: int(count or 0)
            for lid, count in label_counts.items()
            if str(lid or "").strip() and str(lid or "").strip() != "chat"
        }
        issue_count = sum(issue_counts.values())
        if issue_count < self.plugin._backlog_issue_notify_threshold:
            return
        last_notified_at = int(group.get("last_notified_at") or 0)
        now = int(time.time())
        if now - last_notified_at < self.plugin._backlog_notify_cooldown_seconds:
            return
        summary = QQSummaryBuilder.build_group_summary(group, conversations, label_map=label_map)
        highlights = list(summary.get("highlights") or [])[:3]
        highlight_text = "；".join(highlights) if highlights else "暂无具体摘要"
        # 替换 "点名" 标签为猫娘名字
        from utils.config_manager import get_config_manager
        try:
            _, her_name, _, _, _, _, _, _, _ = get_config_manager().get_character_data()
        except Exception:
            her_name = "neko"
        def _resolve_label(lid: str) -> str:
            name = label_map.get(lid, lid)
            if lid == "mention":
                return f"@{her_name}"
            return name
        label_summary_parts = [
            f"{_resolve_label(lid)} {cnt} 条"
            for lid, cnt in issue_counts.items()
            if cnt > 0
        ]
        unread_count = int(summary.get("unread_count") or 0)
        label_summary_text = "，".join(label_summary_parts) if label_summary_parts else "已分类消息 0 条"
        notify_text = (
            f"[QQ backlog] {summary.get('display_name') or ('QQ群 ' + group_id)} "
            f"关键词 {issue_count} 条：{label_summary_text}。"
            f"重点：{highlight_text}"
        )
        self.plugin.push_message(
            visibility=[],
            ai_behavior="respond",
            parts=[{"type": "text", "text": notify_text}],
            source=self.plugin.plugin_id,
            metadata={
                "delivery_semantics": "passive",
                "kind": "qq_backlog_summary",
                "group_id": group_id,
                "unread_count": unread_count,
                "label_counts": label_counts,
            },
        )
        await self.plugin.backlog_store.update_group_last_notified_at(group_id, now)
