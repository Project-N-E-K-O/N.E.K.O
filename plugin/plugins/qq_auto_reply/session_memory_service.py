from __future__ import annotations

import asyncio
import time
from typing import Any


class QQSessionMemoryService:
    GROUP_HISTORY_MAX_MESSAGES = 200
    GROUP_MEMBER_MAX_PARTICIPANTS = 8
    GROUP_MEMBER_MAX_MESSAGES = 50

    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def wait_session_response_complete(self, session: Any, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            if not getattr(session, "_is_responding", False):
                return True
        return False

    async def flush_idle_memory_sessions(self):
        now = time.time()
        idle_sessions = []
        for session_key, user_data in list(self.plugin._user_sessions.items()):
            if not user_data.get("memory_enabled"):
                continue
            last_activity_at = user_data.get("last_activity_at") or now
            if now - last_activity_at >= self.plugin.SESSION_IDLE_TIMEOUT_SECONDS:
                idle_sessions.append(session_key)

        for session_key in idle_sessions:
            async def _finalize_if_still_idle() -> bool:
                current = self.plugin._user_sessions.get(session_key)
                if not current or not current.get("memory_enabled"):
                    return False
                current_last_activity = current.get("last_activity_at") or now
                if time.time() - current_last_activity < self.plugin.SESSION_IDLE_TIMEOUT_SECONDS:
                    return False
                return await self.finalize_user_memory_session(session_key, reason="idle_timeout")

            await self.plugin._run_with_session_lock(session_key, _finalize_if_still_idle)

    async def flush_all_memory_sessions(self, reason: str):
        for session_key, user_data in list(self.plugin._user_sessions.items()):
            if not user_data.get("memory_enabled"):
                continue

            async def _finalize_existing() -> bool:
                current = self.plugin._user_sessions.get(session_key)
                if not current or not current.get("memory_enabled"):
                    return False
                return await self.finalize_user_memory_session(session_key, reason=reason)

            await self.plugin._run_with_session_lock(session_key, _finalize_existing)

    def conversation_slice_to_memory_messages(self, conversation_history: list, start_index: int = 0) -> list[dict[str, Any]]:
        memory_messages = []
        for msg in conversation_history[start_index:]:
            msg_type = getattr(msg, "type", "")
            if msg_type not in ("human", "ai"):
                continue
            role = "user" if msg_type == "human" else "assistant"
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        parts.append(item)
                text = "".join(parts)
            else:
                text = str(content)
            if not text:
                continue
            memory_messages.append({
                "role": role,
                "content": [{"type": "text", "text": text}],
            })
        return memory_messages

    async def post_memory_history(self, endpoint: str, her_name: str, messages: list[dict[str, Any]], timeout: float = 5.0) -> dict[str, Any]:
        return await self.plugin.memory_bridge.post_memory_history(endpoint, her_name, messages, timeout=timeout)

    def record_group_member_turn(self, user_data: dict[str, Any], context: Any) -> None:
        """Keep bounded, actor-attributed user turns for optional member memory."""
        settings = getattr(self.plugin, "_qq_settings", {}) or {}
        if not settings.get("group_member_memory_enabled"):
            return
        if not getattr(context, "is_group", False):
            return
        sender_id = str(getattr(context, "sender_id", "") or "").strip()
        text = str(getattr(context, "message", "") or "").strip()
        if not sender_id or not text:
            return
        buckets = user_data.setdefault("group_member_memory_messages", {})
        if sender_id not in buckets and len(buckets) >= self.GROUP_MEMBER_MAX_PARTICIPANTS:
            return
        messages = buckets.setdefault(sender_id, [])
        messages.append({
            "role": "user",
            "content": [{"type": "text", "text": text}],
        })
        if len(messages) > self.GROUP_MEMBER_MAX_MESSAGES:
            del messages[:-self.GROUP_MEMBER_MAX_MESSAGES]

    async def cache_session_delta(self, session_key: str, user_data: dict[str, Any]) -> int:
        # Busy group chats use one scoped extraction at session finalization.
        # Feeding each group turn into the legacy /cache pipeline would both
        # increase LLM cost and contaminate legacy-private memory.
        if user_data.get("is_group"):
            return 0
        session = user_data.get("session")
        her_name = user_data.get("her_name")
        if not session or not her_name:
            return 0
        conversation_history = getattr(session, "_conversation_history", []) or []
        start_index = int(user_data.get("last_synced_index", 0))
        delta_messages = self.conversation_slice_to_memory_messages(conversation_history, start_index)
        if not delta_messages:
            return 0
        result = await self.post_memory_history("cache", her_name, delta_messages, timeout=5.0)
        if result.get("status") == "error":
            raise RuntimeError(result.get("message", "cache failed"))
        user_data["last_synced_index"] = len(conversation_history)
        user_data["has_cached_memory"] = True
        return len(delta_messages)

    async def finalize_user_memory_session(self, session_key: str, reason: str) -> bool:
        user_data = self.plugin._user_sessions.get(session_key)
        if not user_data or not user_data.get("memory_enabled"):
            return False

        session = user_data.get("session")
        her_name = user_data.get("her_name")
        if not session or not her_name:
            self.plugin._user_sessions.pop(session_key, None)
            return False

        try:
            conversation_history = getattr(session, "_conversation_history", []) or []
            if user_data.get("is_group"):
                group_id = str(user_data.get("group_id") or "").strip()
                scoped_messages = self.conversation_slice_to_memory_messages(
                    conversation_history, 0,
                )[-self.GROUP_HISTORY_MAX_MESSAGES:]
                if group_id and scoped_messages:
                    result = await self.plugin.memory_bridge.post_scoped_memory_history(
                        her_name,
                        scoped_messages,
                        subject=self.plugin.memory_bridge.group_subject(group_id),
                        timeout=30.0,
                    )
                    if result.get("status") == "error":
                        raise RuntimeError(result.get("message", "scoped history failed"))
                    self.plugin.logger.info(
                        f"[{reason}] 已为群 {group_id} 完成 scoped 记忆结算，"
                        f"消息数: {len(scoped_messages)}"
                    )
                member_memory_enabled = bool(
                    (getattr(self.plugin, "_qq_settings", {}) or {}).get(
                        "group_member_memory_enabled", False,
                    )
                )
                member_buckets = (
                    user_data.get("group_member_memory_messages") or {}
                    if member_memory_enabled else {}
                )
                for sender_id, member_messages in list(member_buckets.items()):
                    if not group_id or not sender_id or not member_messages:
                        continue
                    result = await self.plugin.memory_bridge.post_scoped_memory_history(
                        her_name,
                        member_messages,
                        subject=self.plugin.memory_bridge.group_participant_subject(
                            group_id, sender_id,
                        ),
                        timeout=30.0,
                    )
                    if result.get("status") == "error":
                        raise RuntimeError(
                            result.get("message", "scoped participant history failed")
                        )
            else:
                last_synced_index = int(user_data.get("last_synced_index", 0))
                remaining_messages = self.conversation_slice_to_memory_messages(conversation_history, last_synced_index)

                if remaining_messages:
                    result = await self.post_memory_history("process", her_name, remaining_messages, timeout=30.0)
                    if result.get("status") == "error":
                        raise RuntimeError(result.get("message", "process failed"))
                    self.plugin.logger.info(f"[{reason}] 已为用户 {session_key} 完成正式记忆结算，消息数: {len(remaining_messages)}")
                elif user_data.get("has_cached_memory"):
                    settled_messages = self.conversation_slice_to_memory_messages(conversation_history, 0)
                    result = await self.post_memory_history("settle", her_name, settled_messages, timeout=30.0)
                    if result.get("status") == "error":
                        raise RuntimeError(result.get("message", "settle failed"))
                    self.plugin.logger.info(f"[{reason}] 已为用户 {session_key} 完成缓存记忆结算")
        except Exception as e:
            self.plugin.logger.error(f"[{reason}] 用户 {session_key} 的记忆结算失败: {e}")
            return False

        self.plugin._user_sessions.pop(session_key, None)
        try:
            await session.close()
        except Exception as e:
            self.plugin.logger.warning(f"[{reason}] 用户 {session_key} 的本地会话关闭失败: {e}")
        return True

    async def invalidate_private_session(self, qq_number: str) -> None:
        session_key = self.plugin._build_session_key(sender_id=qq_number, is_group=False)

        async def _invalidate() -> None:
            user_data = self.plugin._user_sessions.get(session_key)
            if user_data and user_data.get("memory_enabled"):
                finalized = await self.finalize_user_memory_session(session_key, reason="permission_change")
                if finalized:
                    return

            user_data = self.plugin._user_sessions.pop(session_key, None)
            session = user_data.get("session") if user_data else None
            if session:
                await session.close()

        await self.plugin._run_with_session_lock(session_key, _invalidate)
