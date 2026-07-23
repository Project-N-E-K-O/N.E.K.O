from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from utils.file_utils import atomic_write_json_async, read_json_async

from .backlog_models import QQBacklogConversation, QQBacklogMessage, QQGroupBacklog


class QQBacklogStore:
    FILE_NAME = "backlog_state.json"

    def __init__(self, base_dir: Path, *, retention_limit: int = 200):
        self._path = Path(base_dir) / self.FILE_NAME
        self._lock = asyncio.Lock()
        self._retention_limit = max(20, int(retention_limit or 200))

    @property
    def path(self) -> Path:
        return self._path

    def default_state(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "conversations": {},
            "groups": {},
            "group_attention_state": {},
        }

    async def exists(self) -> bool:
        return self._path.is_file()

    async def load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return self.default_state()
        payload = await read_json_async(self._path)
        if not isinstance(payload, dict):
            return self.default_state()
        merged = self.default_state()
        merged.update(payload)
        merged["conversations"] = payload.get("conversations") if isinstance(payload.get("conversations"), dict) else {}
        merged["groups"] = payload.get("groups") if isinstance(payload.get("groups"), dict) else {}
        for group in merged["groups"].values():
            if isinstance(group, dict):
                if not isinstance(group.get("label_counts"), dict):
                    group["label_counts"] = self._legacy_label_counts(group)
        return merged

    async def save(self, state: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            normalized = self.default_state()
            normalized.update(dict(state or {}))
            normalized["conversations"] = dict(normalized.get("conversations") or {})
            normalized["groups"] = dict(normalized.get("groups") or {})
            await atomic_write_json_async(self._path, normalized)
            return normalized

    async def append_message(self, message: QQBacklogMessage, *, conversation_display_name: str, group_display_name: str | None = None) -> dict[str, Any]:
        async with self._lock:
            state = await self.load()
            conversations = state["conversations"]
            groups = state["groups"]

            conversation = conversations.get(message.conversation_key)
            if not isinstance(conversation, dict):
                conversation = QQBacklogConversation(
                    conversation_key=message.conversation_key,
                    conversation_type=message.conversation_type,
                    source_id=message.source_id,
                    display_name=conversation_display_name,
                    group_id=message.group_id,
                ).to_dict()

            conversation["conversation_type"] = message.conversation_type
            conversation["source_id"] = message.source_id
            conversation["display_name"] = conversation_display_name
            conversation["group_id"] = message.group_id
            conversation["last_message_at"] = int(message.timestamp or 0)
            conversation["last_message_id"] = str(message.message_id or "")

            messages = list(conversation.get("messages") or [])
            if str(message.message_id or "") and any(str(item.get("message_id") or "") == str(message.message_id or "") for item in messages):
                return state
            messages.append(message.to_dict())
            if len(messages) > self._retention_limit:
                messages = messages[-self._retention_limit:]
            conversation["messages"] = messages
            conversation["unread_count"] = sum(1 for item in messages if item.get("review_status") == "unreviewed")
            conversations[message.conversation_key] = conversation

            if message.group_id:
                group = groups.get(message.group_id)
                if not isinstance(group, dict):
                    group = QQGroupBacklog(
                        group_id=message.group_id,
                        display_name=group_display_name or f"QQ群 {message.group_id}",
                    ).to_dict()
                group["display_name"] = group_display_name or group.get("display_name") or f"QQ群 {message.group_id}"
                group["last_message_at"] = int(message.timestamp or 0)
                group["last_message_id"] = str(message.message_id or "")
                keys = list(group.get("conversation_keys") or [])
                if message.conversation_key not in keys:
                    keys.append(message.conversation_key)
                group["conversation_keys"] = keys
                group["unread_count"] = sum(
                    int((conversations.get(key) or {}).get("unread_count") or 0)
                    for key in keys
                )
                group["label_counts"] = self._count_group_labels(conversations, keys)
                groups[message.group_id] = group

            state["conversations"] = conversations
            state["groups"] = groups
            await atomic_write_json_async(self._path, state)
            return state

    async def purge_old_reviewed(self, *, max_age_seconds: int = 86400) -> int:
        """删除已审核超过 max_age_seconds 的消息，返回清除条数。"""
        async with self._lock:
            state = await self.load()
            conversations = state["conversations"]
            groups = state["groups"]
            now = int(__import__("time").time())
            cutoff = now - max_age_seconds
            total_removed = 0
            affected_groups: set[str] = set()

            for key, conv in list(conversations.items()):
                if not isinstance(conv, dict):
                    continue
                old = list(conv.get("messages") or [])
                kept = []
                removed_count = 0
                for item in old:
                    ts = int(item.get("timestamp") or item.get("recorded_at") or 0)
                    if item.get("review_status") == "reviewed" and ts < cutoff:
                        removed_count += 1
                        continue
                    kept.append(item)
                if removed_count > 0:
                    conv["messages"] = kept
                    # 更新未读计数：只统计 kept 中 unreviewed 的
                    conv["unread_count"] = sum(1 for m in kept if m.get("review_status") != "reviewed")
                    if "group_id" in (old[0] if old else {}):
                        affected_groups.add(str(old[0].get("group_id", "")))
                    conversations[key] = conv
                    total_removed += removed_count

            # 清理空会话
            empty_keys = [k for k, c in conversations.items() if isinstance(c, dict) and not c.get("messages")]
            for k in empty_keys:
                del conversations[k]
                for gid, group in list(groups.items()):
                    if isinstance(group, dict) and k in (group.get("conversation_keys") or []):
                        group["conversation_keys"] = [ck for ck in group.get("conversation_keys") or [] if ck != k]
                        groups[gid] = group

            # 更新受影响群的统计
            for gid in affected_groups:
                group = groups.get(gid)
                if not isinstance(group, dict):
                    continue
                keys = group.get("conversation_keys") or []
                total_unread = 0
                labels: dict[str, int] = {}
                for ck in keys:
                    c = conversations.get(ck)
                    if not isinstance(c, dict):
                        continue
                    total_unread += int(c.get("unread_count") or 0)
                    for msg in c.get("messages") or []:
                        if msg.get("review_status") == "unreviewed":
                            cat = str(msg.get("category") or "chat")
                            labels[cat] = int(labels.get(cat, 0)) + 1
                group["unread_count"] = total_unread
                group["label_counts"] = labels
                groups[gid] = group

            # 清理空群
            empty_groups = [gid for gid, g in groups.items() if isinstance(g, dict) and not g.get("conversation_keys")]
            for gid in empty_groups:
                del groups[gid]

            state["conversations"] = conversations
            state["groups"] = groups
            await atomic_write_json_async(self._path, state)
            return total_removed

    async def mark_group_reviewed(self, group_id: str) -> dict[str, Any]:
        async with self._lock:
            state = await self.load()
            groups = state["groups"]
            conversations = state["conversations"]
            group = groups.get(group_id)
            if not isinstance(group, dict):
                return state
            now = int(__import__("time").time())
            keys = list(group.get("conversation_keys") or [])
            last_reviewed_message_id = ""
            for key in keys:
                conversation = conversations.get(key)
                if not isinstance(conversation, dict):
                    continue
                messages = []
                for item in list(conversation.get("messages") or []):
                    updated = dict(item)
                    updated["review_status"] = "reviewed"
                    last_reviewed_message_id = str(updated.get("message_id") or last_reviewed_message_id)
                    messages.append(updated)
                conversation["messages"] = messages
                conversation["unread_count"] = 0
                conversation["last_reviewed_at"] = now
                conversation["last_reviewed_message_id"] = last_reviewed_message_id or str(conversation.get("last_message_id") or "")
                conversations[key] = conversation
            group["unread_count"] = 0
            group["label_counts"] = {}
            group["last_notified_at"] = now
            groups[group_id] = group
            state["groups"] = groups
            state["conversations"] = conversations
            await atomic_write_json_async(self._path, state)
            return state

    async def ensure_group_placeholder(self, group_id: str, *, group_display_name: str | None = None) -> dict[str, Any]:
        async with self._lock:
            state = await self.load()
            groups = state["groups"]
            normalized_group_id = str(group_id or "").strip()
            if not normalized_group_id:
                return state
            group = groups.get(normalized_group_id)
            if not isinstance(group, dict):
                group = QQGroupBacklog(
                    group_id=normalized_group_id,
                    display_name=group_display_name or f"QQ群 {normalized_group_id}",
                ).to_dict()
            else:
                group["display_name"] = group_display_name or group.get("display_name") or f"QQ群 {normalized_group_id}"
            groups[normalized_group_id] = group
            state["groups"] = groups
            await atomic_write_json_async(self._path, state)
            return state

    async def remove_group_placeholder(self, group_id: str, *, force: bool = False) -> dict[str, Any]:
        async with self._lock:
            state = await self.load()
            groups = state["groups"]
            normalized_group_id = str(group_id or "").strip()
            if not normalized_group_id:
                return state
            group = groups.get(normalized_group_id)
            if not isinstance(group, dict):
                return state
            keys = list(group.get("conversation_keys") or [])
            # 有待审阅消息且非强制删除 → 保护性跳过
            if keys and not force:
                return state
            groups.pop(normalized_group_id, None)
            state["groups"] = groups
            await atomic_write_json_async(self._path, state)
            return state

    async def update_group_last_notified_at(self, group_id: str, timestamp: int) -> dict[str, Any]:
        async with self._lock:
            state = await self.load()
            groups = state["groups"]
            normalized_group_id = str(group_id or "").strip()
            if not normalized_group_id:
                return state
            group = groups.get(normalized_group_id)
            if not isinstance(group, dict):
                return state
            group["last_notified_at"] = int(timestamp or 0)
            groups[normalized_group_id] = group
            state["groups"] = groups
            await atomic_write_json_async(self._path, state)
            return state

    async def get_recent_group_messages(self, group_id: str, *, limit: int = 5, exclude_message_id: str = "") -> list[dict[str, Any]]:
        state = await self.load()
        groups = state["groups"]
        conversations = state["conversations"]
        group = groups.get(str(group_id or "").strip())
        if not isinstance(group, dict):
            return []
        excluded = str(exclude_message_id or "").strip()
        timeline: list[dict[str, Any]] = []
        for key in list(group.get("conversation_keys") or []):
            conversation = conversations.get(key)
            if not isinstance(conversation, dict):
                continue
            for item in list(conversation.get("messages") or []):
                if not isinstance(item, dict):
                    continue
                message_id = str(item.get("message_id") or "").strip()
                if excluded and message_id == excluded:
                    continue
                timeline.append(item)
        timeline.sort(key=lambda item: (int(item.get("timestamp") or 0), str(item.get("message_id") or "")))
        if limit > 0:
            timeline = timeline[-int(limit):]
        return timeline


    async def get_unreviewed_messages_since(self, group_id: str, since_timestamp: int = 0, *, limit: int = 30) -> list[dict[str, Any]]:
        """取出群中自 since_timestamp 以来的未审核消息（供回溯补回使用）。"""
        state = await self.load()
        groups = state["groups"]
        conversations = state["conversations"]
        group = groups.get(str(group_id or "").strip())
        if not isinstance(group, dict):
            return []
        results: list[dict[str, Any]] = []
        for key in list(group.get("conversation_keys") or []):
            conv = conversations.get(key)
            if not isinstance(conv, dict):
                continue
            for item in list(conv.get("messages") or []):
                if not isinstance(item, dict):
                    continue
                if item.get("review_status") != "unreviewed":
                    continue
                ts = int(item.get("timestamp") or 0)
                if since_timestamp > 0 and ts < since_timestamp:
                    continue
                results.append(item)
        results.sort(key=lambda item: (int(item.get("timestamp") or 0), str(item.get("message_id") or "")))
        if limit > 0:
            results = results[-int(limit):]
        return results

    async def mark_message_reviewed(self, message_id: str) -> None:
        """将指定消息标记为已审核（AI 回复后即时调用）。"""
        mid = str(message_id or "").strip()
        if not mid:
            return
        async with self._lock:
            state = await self.load()
            conversations = state["conversations"]
            groups = state["groups"]
            updated_group_ids: set[str] = set()
            for conv_key, conv in list(conversations.items()):
                if not isinstance(conv, dict):
                    continue
                for item in list(conv.get("messages") or []):
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("message_id") or "").strip() == mid and item.get("review_status") != "reviewed":
                        item["review_status"] = "reviewed"
                        conv["unread_count"] = max(0, int(conv.get("unread_count") or 0) - 1)
                        if "group_id" in item:
                            updated_group_ids.add(str(item["group_id"] or ""))
                        continue
                conversations[conv_key] = conv
            # 更新群级统计
            for gid in updated_group_ids:
                group = groups.get(gid)
                if isinstance(group, dict):
                    group["unread_count"] = int(group.get("unread_count") or 0) - 1
                    if group["unread_count"] < 0:
                        group["unread_count"] = 0
                    # 重新计算 label_counts
                    group["label_counts"] = {}
                    for key in list(group.get("conversation_keys") or []):
                        conv = conversations.get(key)
                        if not isinstance(conv, dict):
                            continue
                        for item in list(conv.get("messages") or []):
                            if not isinstance(item, dict):
                                continue
                            if item.get("review_status") == "unreviewed":
                                cat = str(item.get("category") or "chat")
                                group["label_counts"][cat] = int(group["label_counts"].get(cat, 0)) + 1
                    groups[gid] = group
            state["conversations"] = conversations
            state["groups"] = groups
            await atomic_write_json_async(self._path, state)

    async def get_group_detail(self, group_id: str) -> dict[str, Any]:
        state = await self.load()
        groups = state["groups"]
        conversations = state["conversations"]
        group = groups.get(group_id)
        if not isinstance(group, dict):
            return {"group": None, "conversations": []}
        detail_conversations: list[dict[str, Any]] = []
        for key in list(group.get("conversation_keys") or []):
            conversation = conversations.get(key)
            if not isinstance(conversation, dict):
                continue
            detail_conversations.append({
                "conversation_key": key,
                "display_name": conversation.get("display_name") or key,
                "unread_count": int(conversation.get("unread_count") or 0),
                "messages": [item for item in list(conversation.get("messages") or []) if item.get("review_status") == "unreviewed"],
            })
        return {"group": group, "conversations": detail_conversations}

    @staticmethod
    def _count_group_labels(conversations: dict[str, Any], keys: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for key in keys:
            for item in list((conversations.get(key) or {}).get("messages") or []):
                if item.get("review_status") != "unreviewed":
                    continue
                category = str(item.get("category") or "chat").strip() or "chat"
                if category == "chat":
                    continue
                counts[category] = counts.get(category, 0) + 1
        return counts

    @staticmethod
    def _legacy_label_counts(group: dict[str, Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for key in ("issue", "feedback", "mention"):
            value = int(group.get(f"{key}_count") or 0)
            if value > 0:
                counts[key] = value
        return counts
