from __future__ import annotations

from typing import Any

from .backlog_models import QQGroupBacklog


class QQSummaryBuilder:
    @staticmethod
    def build_group_summary(group: dict[str, Any], conversations: dict[str, Any], label_map: dict[str, str] | None = None) -> dict[str, Any]:
        keys = list(group.get("conversation_keys") or [])
        highlights: list[str] = []
        labels = dict(label_map or {})
        for key in keys:
            conversation = conversations.get(key) or {}
            display_name = str(conversation.get("display_name") or key)
            messages = list(conversation.get("messages") or [])
            unreviewed = [item for item in messages if item.get("review_status") == "unreviewed"]
            important = [item for item in unreviewed if str(item.get("category") or "chat") != "chat"]
            if not important:
                continue
            top = important[-1]
            category = str(top.get("category") or "chat")
            category_label = labels.get(category, category)
            text = str(top.get("text") or "").strip()
            if len(text) > 60:
                text = text[:57] + "..."
            highlights.append(f"{display_name}（{category_label}）：{text}")

        return {
            "group_id": str(group.get("group_id") or ""),
            "display_name": str(group.get("display_name") or ""),
            "unread_count": int(group.get("unread_count") or 0),
            "label_counts": dict(group.get("label_counts") or {}),
            "highlights": highlights[:10],
        }

    @classmethod
    def build_all_group_summaries(
        cls,
        state: dict[str, Any],
        label_map: dict[str, str] | None = None,
        configured_groups: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        groups = dict(state.get("groups") or {})
        conversations = dict(state.get("conversations") or {})
        for item in list(configured_groups or []):
            if not isinstance(item, dict):
                continue
            group_id = str(item.get("group_id") or "").strip()
            if not group_id or group_id in groups:
                continue
            groups[group_id] = QQGroupBacklog(
                group_id=group_id,
                display_name=f"QQ群 {group_id}",
            ).to_dict()
        ordered = sorted(
            groups.values(),
            key=lambda item: int((item or {}).get("last_message_at") or 0),
            reverse=True,
        )
        return [cls.build_group_summary(group, conversations, label_map=label_map) for group in ordered]
