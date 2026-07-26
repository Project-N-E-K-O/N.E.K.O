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
                # 关机只有一次机会：撞上每轮批次上限（返回 False 但游标有
                # 进展）就继续排，零进展才停——上限是防饥饿，不是弃数据。
                prev_cursor = int(
                    current.get("last_group_digest_index", 0) or 0
                )
                while True:
                    finalized = await self.finalize_user_memory_session(
                        session_key, reason=reason,
                    )
                    if finalized:
                        return True
                    survivor = self.plugin._user_sessions.get(session_key)
                    if not survivor:
                        return finalized
                    new_cursor = int(
                        survivor.get("last_group_digest_index", 0) or 0
                    )
                    if new_cursor <= prev_cursor:
                        return finalized
                    prev_cursor = new_cursor

            await self.plugin._run_with_session_lock(session_key, _finalize_existing)

    def conversation_slice_to_memory_messages(
        self, conversation_history: list, start_index: int = 0,
        *, user_data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        memory_messages = []
        # 排除名单：被 rapid-fire 合并取代的未投递草稿（ai 行）与合成
        # flush 控制 prompt（human 行，内含草稿副本）——没人说过/没人见过
        # 的文本不得被提取成持久记忆（群 digest 与私聊 /cache 同源过滤）。
        # 名单在 user_data 上（buffer 记入，插件自有 dict 无"打标失败"
        # 模式），按对象身份比对——名单持强引用保活，id 稳定。
        undelivered_ids = {
            id(row)
            for row in ((user_data or {}).get("undelivered_draft_rows") or [])
        }
        for msg in conversation_history[start_index:]:
            msg_type = getattr(msg, "type", "")
            if msg_type not in ("human", "ai"):
                continue
            if id(msg) in undelivered_ids:
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
        *, user_data: dict[str, Any] | None = None,
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
                user_data=user_data,
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
        if not getattr(context, "member_memory_enabled", False):
            # 完成时刻（上一行）与发言时刻（context 快照）都要有授权：
            # 生成期间才切 ON 的轮，发言人说话时并无成员记忆 consent，
            # 不得回溯入 bucket；反向（说话时 ON、完成时 OFF）由上一行
            # 挡住。缺字段的合成调用方 fail-closed。
            return
        if not getattr(context, "is_group", False):
            return
        if (
            getattr(context, "group_facing", False)
            or getattr(context, "group_scene_mode", "") == "group_collective"
            or getattr(context, "source_kind", "") in (
                "proactive_speech", "rapid_fire_flush", "buffer_delayed",
                "retroactive_review",
            )
        ):
            # 群体面向/合成轮（proactive 的"[系统]…"控制指令等）不是成员
            # 发言——按 sender 入 bucket 会把捏造的偏好挂到该成员 scope。
            # retroactive_review 的 context 在回看时刻构建，快照看不到
            # 发言时刻的 member 政策（原话可能出自 OFF 时代），且其文本
            # 是"[回溯补回]…"合成包装——同样不入 bucket。
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
        delta_messages = self.conversation_slice_to_memory_messages(
            conversation_history, start_index, user_data=user_data,
        )
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
        reason: str, buckets: dict | None = None, labels: dict | None = None,
    ) -> list[str]:
        """Concurrently flush member buckets (semaphore 4).

        Success pops the bucket; failures are collected and stay queued for
        the next sweep. Serial 8x30s used to hold the session lock ~4 min,
        exhausting the global message semaphore and never fitting the host
        shutdown kill window."""
        member_buckets = (
            buckets if buckets is not None
            else user_data.get("group_member_memory_messages") or {}
        )
        member_labels = (
            labels if labels is not None
            else user_data.get("group_member_memory_labels") or {}
        )
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
                snapshot = current.get("pending_settle_buckets") or {}
                if group_id and her_name and snapshot:
                    failed = await self._flush_member_buckets(
                        current, group_id=group_id, her_name=her_name,
                        reason="member_memory_disabled",
                        buckets=snapshot,
                        labels=current.get("pending_settle_labels") or {},
                    )
                    if failed:
                        self.plugin.logger.error(
                            f"[member_memory_disabled] 群 {group_id} 有 "
                            f"{len(failed)} 个成员 bucket 结算失败，按 opt-out "
                            f"丢弃"
                        )
                # 只清快照与标记；re-enable 后新授权轮写入的活 bucket
                # 不受迟到结算任务影响。
                current.pop("pending_settle_buckets", None)
                current.pop("pending_settle_labels", None)
                current.pop("pending_member_settle", None)

            await self.plugin._run_with_session_lock(session_key, _settle_one)

    async def finalize_user_memory_session(
        self, session_key: str, reason: str, *, retain_session: bool = False,
    ) -> bool:
        user_data = self.plugin._user_sessions.get(session_key)
        if not user_data or not user_data.get("memory_enabled"):
            return False

        session = user_data.get("session")
        her_name = user_data.get("her_name")
        if not session or not her_name:
            self.plugin._user_sessions.pop(session_key, None)
            return False

        consumed_cutoff = None
        try:
            conversation_history = getattr(session, "_conversation_history", []) or []
            if user_data.get("is_group"):
                # get 而非 pop：finalize 失败时 cutoff 必须留存，重试仍
                # 以 opt-out 时刻为界；成功路径整个 user_data 被弹出作废。
                cutoff = user_data.get("group_opt_out_cutoff", None)
                consumed_cutoff = cutoff
                if cutoff is not None:
                    # opt-out 截止点：只结算策略翻 OFF 时刻之前的历史，
                    # 竞态窗口内追加的轮次绝不入库。
                    conversation_history = conversation_history[:max(0, int(cutoff))]
                group_id = str(user_data.get("group_id") or "").strip()
                last_group_digest_index = max(
                    0, int(user_data.get("last_group_digest_index", 0)),
                )
                # 未授权边界地板：session 可能由"OFF 期间已解析 persist=
                # False 的请求"在转变盖章之后才创建（无 enable 标记），其
                # 未授权轮位于游标 0 之后——digest 起点不得低于该边界。
                # 但 opt-out 结算窗口（cutoff 之前）是更早的授权区间：
                # cutoff 之后记下的未授权边界属于下一个时代，套到本窗口
                # 会把整段已授权前缀当作已处理而丢弃。
                nonconsent_floor = int(
                    user_data.get("nonconsent_history_end", 0) or 0
                )
                if cutoff is not None and nonconsent_floor > int(cutoff):
                    nonconsent_floor = 0
                last_group_digest_index = max(
                    last_group_digest_index, nonconsent_floor,
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
                        user_data=user_data,
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
                # OFF 时代快照优先冲掉：member 开关同步关掉后、后台结算
                # 任务跑到之前，并发的 idle/discard finalizer 凭快照照常
                # 结算，不因全局 flag 已 False 丢弃 opt-in 期间的收集。
                snapshot = user_data.get("pending_settle_buckets")
                if snapshot and group_id:
                    failed_member_ids += await self._flush_member_buckets(
                        user_data, group_id=group_id, her_name=her_name,
                        reason=reason, buckets=snapshot,
                        labels=user_data.get("pending_settle_labels") or {},
                    )
                    if not snapshot:
                        user_data.pop("pending_settle_buckets", None)
                        user_data.pop("pending_settle_labels", None)
                        user_data.pop("pending_member_settle", None)
                if member_memory_enabled and group_id:
                    failed_member_ids += await self._flush_member_buckets(
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
                remaining_messages = self.conversation_slice_to_memory_messages(
                    conversation_history, last_synced_index, user_data=user_data,
                )

                if remaining_messages:
                    result = await self.post_memory_history("process", her_name, remaining_messages, timeout=30.0)
                    if result.get("status") == "error":
                        raise RuntimeError(result.get("message", "process failed"))
                    self.plugin.logger.info(f"[{reason}] 已为用户 {session_key} 完成正式记忆结算，消息数: {len(remaining_messages)}")
                elif user_data.get("has_cached_memory"):
                    settled_messages = self.conversation_slice_to_memory_messages(
                        conversation_history, 0, user_data=user_data,
                    )
                    result = await self.post_memory_history("settle", her_name, settled_messages, timeout=30.0)
                    if result.get("status") == "error":
                        raise RuntimeError(result.get("message", "settle failed"))
                    self.plugin.logger.info(f"[{reason}] 已为用户 {session_key} 完成缓存记忆结算")
        except Exception as e:
            self.plugin.logger.error(f"[{reason}] 用户 {session_key} 的记忆结算失败: {e}")
            return False

        if retain_session:
            # 快速 OFF→ON：ON 任务已排队等着 rebase 本会话——旧时代结算
            # 完毕后保留会话，pop+close 会把 ON 之后追加的已授权轮次连带
            # 销毁（共享上下文与群记忆双双丢失）。cutoff 已随本次结算消费
            # 完毕，留着会让后续 finalize 永远截断在旧时代边界。
            # compare-and-pop：分批结算窗口长达数分钟，期间第二次 OFF 盖章
            # 会覆写 cutoff——那个更新的 cutoff 本次并未消费，删掉它会让
            # 排队中的第二个 OFF 结算失去 opt-out 围栏。
            if user_data.get("group_opt_out_cutoff") == consumed_cutoff:
                user_data.pop("group_opt_out_cutoff", None)
            return True
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
                    boundary = current.pop("pending_enable_rebase", None)
                    if boundary is None:
                        # 无标 = 转变之后才创建的会话，全程 opt-in，
                        # rebase 会误跳其正当轮次——不碰。
                        return
                    # rebase 到 enable 时刻的边界（同步盖章），之后到达的
                    # 轮次全部保留；boundary=True 兼容旧标记取当前长度。
                    if boundary is True:
                        boundary = len(history)
                    # 与"未授权轮结束位置"取 max：enable 时间戳打下时可能
                    # 有一轮 persist=False 的生成还在途，其行落在时间戳之
                    # 后——隐私优先于完整性，宁可多跳过也不入库。
                    boundary = max(
                        int(boundary),
                        int(current.get("nonconsent_history_end", 0) or 0),
                    )
                    # 死 cutoff 不得跨时代存活：OFF 结算失败（fail-closed）
                    # 会留下 cutoff。rebase 之后它会让后续 finalize 把历史
                    # 截断在旧时代边界、越界钳制再把游标回退到 cutoff——
                    # 空片"成功"后 pop+close，新时代行未结算即被销毁。旧
                    # 时代已按 fail-closed 处理完毕，这里消费掉。
                    current.pop("group_opt_out_cutoff", None)
                    # 游标只前进不覆写回退：retain 结算到 rebase 之间的
                    # 窗口里，焦点 digest 可能已把新时代行推送入库并推进
                    # 游标——回退会让那些行被下一次 finalize 重复结算。
                    current["last_group_digest_index"] = min(
                        max(
                            int(current.get("last_group_digest_index", 0) or 0),
                            boundary,
                        ),
                        len(history),
                    )
                    current["memory_enabled"] = True
                    return
                if not current.pop("pending_disable_settle", None):
                    # 无标 = opt-out 之后才创建（memory_enabled 本就 False），
                    # 结算它会把 opt-out 后的内容入库——不碰。
                    return
                # 有标会话按转变结算，不信可变的 per-request flag。
                current["memory_enabled"] = True
                finalized = False
                prev_cursor = int(
                    current.get("last_group_digest_index", 0) or 0
                )
                while True:
                    # 每次迭代重读：ON 章由 settings 写入路径同步盖下，可能
                    # 落在本任务运行中途。有 ON 章 = 新时代已开启、rebase 任
                    # 务已排队——结算旧时代但保留会话，销毁会把 ON 之后追加
                    # 的已授权轮次一并丢掉，rebase 任务随后也找不到会话。
                    retain = current.get("pending_enable_rebase") is not None
                    try:
                        finalized = await self.finalize_user_memory_session(
                            session_key, reason="group_memory_disabled",
                            retain_session=retain,
                        )
                    except Exception as exc:
                        self.plugin.logger.error(
                            f"群记忆关闭时结算失败 ({session_key}): {exc}"
                        )
                        break
                    if finalized:
                        break
                    survivor = self.plugin._user_sessions.get(session_key)
                    if not survivor:
                        break
                    new_cursor = int(
                        survivor.get("last_group_digest_index", 0) or 0
                    )
                    if new_cursor <= prev_cursor:
                        # 无进展 = 真失败；有进展 = 只是撞上每轮批次上限，
                        # 继续排——上限是防锁饥饿的，不是放弃已授权数据的
                        # 理由。
                        break
                    prev_cursor = new_cursor
                # 成功路径 session 已被 finalize 弹出（retain 场景除外——
                # 会话保留待 rebase）；仍把 flag 置 False：rebase 任务接手
                # 前的窗口里，idle flush 不得把 OFF 期间的行当 opt-in 入库，
                # rebase 任务会在推进游标越过它们之后再置回 True。
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
