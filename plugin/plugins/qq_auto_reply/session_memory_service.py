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

    def _slice_group_history_batch(
        self, conversation_history: list, start_index: int, max_messages: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Oldest-first digest batch with an exact cursor.

        Collect up to max_messages eligible messages starting at start_index
        and return them with the raw index just past the last row consumed.
        Filtered-out rows (non human/ai, empty text) advance the cursor but
        produce no messages, so the caller never skips a stretch of history
        the way a newest-N slice would."""
        messages: list[dict[str, Any]] = []
        next_index = max(0, start_index)
        for raw_index in range(next_index, len(conversation_history)):
            converted = self.conversation_slice_to_memory_messages(
                conversation_history[raw_index:raw_index + 1],
            )
            if converted and len(messages) + len(converted) > max_messages:
                break
            messages.extend(converted)
            next_index = raw_index + 1
        return messages, next_index

    def record_group_member_turn(self, user_data: dict[str, Any], context: Any) -> None:
        """Keep bounded, actor-attributed user turns for optional member memory."""
        settings = getattr(self.plugin, "_qq_settings", {}) or {}
        if not settings.get("group_member_memory_enabled"):
            return
        if not getattr(context, "is_group", False):
            return
        if (
            getattr(context, "group_facing", False)
            or getattr(context, "group_scene_mode", "") == "group_collective"
        ):
            # 群体面向/合成轮（proactive 的"[系统]…"控制指令等）不是成员
            # 发言——按 sender 入 bucket 会把捏造的偏好挂到该成员 scope。
            return
        sender_id = str(getattr(context, "sender_id", "") or "").strip()
        text = str(getattr(context, "message", "") or "").strip()
        if not sender_id or not text:
            return
        buckets = user_data.setdefault("group_member_memory_messages", {})
        if sender_id not in buckets and len(buckets) >= self.GROUP_MEMBER_MAX_PARTICIPANTS:
            return
        # 记录发言人展示名（备注名 > 群昵称 > 纯 QQ 号），finalize 时作为
        # speaker_label 传给提取端点——不带则提取 prompt 会把成员发言当私聊
        # 主人的发言抽取。label 是原始用户数据（昵称/号码），无 i18n 词。
        permission_mgr = getattr(self.plugin, "permission_mgr", None)
        custom_nickname = (
            permission_mgr.get_nickname(sender_id) if permission_mgr else None
        )
        nickname = str(
            custom_nickname or getattr(context, "user_nickname", "") or ""
        ).strip()
        labels = user_data.setdefault("group_member_memory_labels", {})
        labels[sender_id] = f"{nickname}({sender_id})" if nickname else sender_id
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

    async def _flush_member_buckets(
        self, user_data: dict[str, Any], *, group_id: str, her_name: str,
        reason: str,
    ) -> list[str]:
        """Concurrently flush member buckets (semaphore 4).

        Success pops the bucket; failures are collected and stay queued for
        the next sweep. Serial 8x30s used to hold the session lock ~4 min,
        exhausting the global message semaphore and never fitting the host
        shutdown kill window."""
        member_buckets = user_data.get("group_member_memory_messages") or {}
        member_labels = user_data.get("group_member_memory_labels") or {}
        member_flush_sem = asyncio.Semaphore(4)

        async def _flush_one_member(
            sender_id: str, member_messages: list,
        ) -> str | None:
            async with member_flush_sem:
                try:
                    result = await self.plugin.memory_bridge.post_scoped_memory_history(
                        her_name,
                        member_messages,
                        subject=self.plugin.memory_bridge.group_participant_subject(
                            group_id, sender_id,
                        ),
                        speaker_label=(
                            str(member_labels.get(sender_id) or sender_id)[:64]
                        ),
                        timeout=30.0,
                    )
                    if result.get("status") == "error":
                        raise RuntimeError(
                            result.get(
                                "message",
                                "scoped participant history failed",
                            )
                        )
                except Exception as exc:
                    self.plugin.logger.error(
                        f"[{reason}] 群 {group_id} 成员 {sender_id} "
                        f"记忆结算失败: {exc}"
                    )
                    return sender_id
                member_buckets.pop(sender_id, None)
                return None

        flush_jobs = [
            _flush_one_member(sender_id, member_messages)
            for sender_id, member_messages in list(member_buckets.items())
            if sender_id and member_messages
        ]
        if not flush_jobs:
            return []
        return [sid for sid in await asyncio.gather(*flush_jobs) if sid]

    async def settle_member_buckets_on_disable(self) -> None:
        """group_member_memory_enabled ON->OFF transition: settle buckets
        collected under consent now — finalize substitutes an empty mapping
        while the option is off, so without this the collected participant
        turns would be silently discarded at session teardown. Buckets that
        fail to settle are dropped fail-closed (nothing may linger after
        opt-out)."""
        for session_key, user_data in list(self.plugin._user_sessions.items()):
            if not user_data.get("is_group"):
                continue

            async def _settle_one() -> None:
                current = self.plugin._user_sessions.get(session_key)
                if not current:
                    return
                group_id = str(current.get("group_id") or "").strip()
                her_name = current.get("her_name")
                buckets = current.get("group_member_memory_messages") or {}
                if group_id and her_name and buckets:
                    failed = await self._flush_member_buckets(
                        current, group_id=group_id, her_name=her_name,
                        reason="member_memory_disabled",
                    )
                    if failed:
                        self.plugin.logger.error(
                            f"[member_memory_disabled] 群 {group_id} 有 "
                            f"{len(failed)} 个成员 bucket 结算失败，按 opt-out "
                            f"丢弃"
                        )
                current.pop("group_member_memory_messages", None)
                current.pop("group_member_memory_labels", None)

            await self.plugin._run_with_session_lock(session_key, _settle_one)

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
                last_group_digest_index = max(
                    0, int(user_data.get("last_group_digest_index", 0)),
                )
                if last_group_digest_index > len(conversation_history):
                    # 会话历史被重复守卫重置/收缩后旧游标越界：钳到当前
                    # 长度并回写，否则此后追加的轮次会被当成"已结算"
                    # 永久跳过。绝不回退（避免重放）。
                    last_group_digest_index = len(conversation_history)
                    user_data["last_group_digest_index"] = last_group_digest_index
                # 先旧后新分批结算，游标只推进到本批实际覆盖的原始下标。
                # 旧写法单发 `[-200:]` 会把超过窗口的中段永久跳过（游标却
                # 直接跳到 len(history)）——活跃群完全可复现的数据丢失。
                # 每批一次 scoped 提取，失败即停：已成功批次的游标推进
                # 保留，剩余留给下一轮 flush 重试。限批（5）：无界排水会
                # 持会话锁数分钟、拖垮全局 semaphore 与关机串行 sweep；
                # 剩余批次返回 False 留给下一轮继续（游标精确不丢）。
                digest_batches_left = 5
                while group_id:
                    if digest_batches_left <= 0:
                        self.plugin.logger.info(
                            f"[{reason}] 群 {group_id} 本轮结算达批次上限，"
                            f"剩余待下一轮"
                        )
                        return False
                    digest_batches_left -= 1
                    scoped_messages, next_index = self._slice_group_history_batch(
                        conversation_history, last_group_digest_index,
                        self.GROUP_HISTORY_MAX_MESSAGES,
                    )
                    if not scoped_messages:
                        if next_index > last_group_digest_index:
                            # 尾部全是被过滤的行：推进游标即可，无须发送。
                            user_data["last_group_digest_index"] = next_index
                        break
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
                    user_data["last_group_digest_index"] = next_index
                    last_group_digest_index = next_index
                    user_data["group_memory_flushed"] = True
                member_memory_enabled = bool(
                    (getattr(self.plugin, "_qq_settings", {}) or {}).get(
                        "group_member_memory_enabled", False,
                    )
                )
                failed_member_ids: list[str] = []
                if member_memory_enabled and group_id:
                    failed_member_ids = await self._flush_member_buckets(
                        user_data, group_id=group_id, her_name=her_name,
                        reason=reason,
                    )
                if failed_member_ids:
                    self.plugin.logger.error(
                        f"[{reason}] 群 {group_id} 仍有 "
                        f"{len(failed_member_ids)} 个成员记忆待重试"
                    )
                    return False
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

    async def invalidate_group_sessions(self, *, enabled: bool) -> None:
        """Sync existing group sessions with a group_memory_enabled flip.

        ON to OFF: settle buffers recorded under consent now (same one scoped
        extraction the idle flush would have run, just earlier); on failure
        fail closed — mark the session memory-disabled, advance the digest
        cursor, and drop member buckets so nothing persists after opt-out.
        OFF to ON: advance the digest cursor past history accumulated while
        opted out, so those turns are never retroactively extracted.
        """
        for session_key, user_data in list(self.plugin._user_sessions.items()):
            if not user_data.get("is_group"):
                continue

            async def _sync_one() -> None:
                current = self.plugin._user_sessions.get(session_key)
                if not current:
                    return
                session = current.get("session")
                history = getattr(session, "_conversation_history", []) or []
                if enabled:
                    # 无条件重定位游标：settings 写入与本任务异步执行之间，
                    # 抢先到达的群消息会把 memory_enabled 先置 True——按
                    # flag 跳过会让 opt-out 期间积累的历史被下一次 flush
                    # 追溯提取。以"策略转变"为准，不信 per-request 缓存；
                    # 竞态窗口内的少量新轮次被一并跳过，属 fail-closed。
                    current["last_group_digest_index"] = len(history)
                    current["memory_enabled"] = True
                    return
                if not current.get("memory_enabled"):
                    # 竞态：settings 写 OFF 后、本任务运行前，抢先请求已把
                    # 缓存 flag 刷成 False——buffer 里仍是 opt-in 期间的
                    # 轮次。转变权威在策略不在 per-request 缓存：恢复 flag
                    # 让 finalize 得以结算（对偶 enable 分支的无条件重定位）。
                    current["memory_enabled"] = True
                finalized = False
                try:
                    finalized = await self.finalize_user_memory_session(
                        session_key, reason="group_memory_disabled",
                    )
                except Exception as exc:
                    self.plugin.logger.error(
                        f"群记忆关闭时结算失败 ({session_key}): {exc}"
                    )
                # 成功路径 session 已被 finalize 弹出；仍把本地引用的 flag
                # 置 False——任何持有旧引用的路径都不得再当它 opt-in。
                current["memory_enabled"] = False
                if not finalized:
                    current["last_group_digest_index"] = len(history)
                    current.pop("group_member_memory_messages", None)
                    current.pop("group_member_memory_labels", None)

            await self.plugin._run_with_session_lock(session_key, _sync_one)

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
