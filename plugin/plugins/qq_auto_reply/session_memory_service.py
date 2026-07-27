from __future__ import annotations

from .pipeline_models import is_synthetic_source

import asyncio
import time
from typing import Any


class QQSessionMemoryService:
    GROUP_HISTORY_MAX_MESSAGES = 200
    GROUP_MEMBER_MAX_PARTICIPANTS = 8
    GROUP_MEMBER_MAX_MESSAGES = 50
    # 冲不出去时的硬顶：服务端挂掉的情况下也不能无界增长，但要留出比
    # 触发线更大的余量，别一到线就开始丢。
    GROUP_MEMBER_HARD_LIMIT = 150
    # 未结算的群历史积压到这个数就后台冲一次。复读守卫（main_logic 的
    # omni client，全模式共享）会把 _conversation_history 整个换成只剩
    # 系统消息，此前未落盘的轮次当场消失；在它之前主动落盘，能把损失从
    # "整场会话"压到最多这么多轮。
    GROUP_DIGEST_BACKLOG_TRIGGER = 40

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
            # pending_disable_settle 会话也要排：opt-out 之后到达的轮次会
            # 把 memory_enabled 打成 False，但 cutoff 之前的已授权前缀只
            # 存在于内存里，等着转变任务结算。关机只 join 有限时间，任务
            # 卡住/失败时这里是最后一次机会（finalize 用 cutoff 截断，
            # 不会带出 opt-out 之后的内容）。
            if not user_data.get("memory_enabled") and not user_data.get(
                "pending_disable_settle"
            ):
                continue

            async def _finalize_existing() -> bool:
                current = self.plugin._user_sessions.get(session_key)
                if not current:
                    return False
                if not current.get("memory_enabled"):
                    if not current.get("pending_disable_settle"):
                        return False
                    # 关机兜底：临时按 opt-in 结算，cutoff 保证只带出
                    # opt-out 之前的历史。
                    current["memory_enabled"] = True
                # 关机只有一次机会：撞上每轮批次上限（返回 False 但游标有
                # 进展）就继续排，零进展才停——上限是防饥饿，不是弃数据。
                prev_progress = self._settlement_progress(current)
                while True:
                    finalized = await self.finalize_user_memory_session(
                        session_key, reason=reason,
                    )
                    if finalized:
                        return True
                    survivor = self.plugin._user_sessions.get(session_key)
                    if not survivor:
                        return finalized
                    progress = self._settlement_progress(survivor)
                    if progress == prev_progress:
                        return finalized
                    prev_progress = progress

            await self.plugin._run_with_session_lock(session_key, _finalize_existing)

    @staticmethod
    def prune_draft_row_refs(user_data: dict[str, Any] | None) -> None:
        """Drop marks whose rows are no longer in the session history.

        The lists hold the row objects themselves (identity comparison), so
        an active group that keeps merging or failing deliveries would grow
        them forever — and with them the rows they pin in memory. A row the
        history no longer contains can never be matched again, so it is
        dead weight."""
        if not isinstance(user_data, dict):
            return
        session = user_data.get("session")
        history = getattr(session, "_conversation_history", None)
        if not isinstance(history, list):
            return
        live = {id(row) for row in history}
        for key in ("undelivered_draft_rows", "provisional_draft_rows"):
            rows = user_data.get(key)
            if not rows:
                continue
            kept = [row for row in rows if id(row) in live]
            if len(kept) != len(rows):
                rows[:] = kept

    @staticmethod
    def _settlement_progress(user_data: dict[str, Any] | None) -> tuple:
        """What "made progress" means for one settlement round.

        The group digest cursor alone is not enough: a round can flush
        several member buckets and still fail on the group side, leaving
        the cursor untouched. Stopping there strands the remaining member
        memory for good at shutdown."""
        if not isinstance(user_data, dict):
            return ()
        return (
            int(user_data.get("last_group_digest_index", 0) or 0),
            len(user_data.get("group_member_memory_messages") or {}),
            len(user_data.get("pending_settle_buckets") or {}),
        )

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
        stop_at_provisional: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """Oldest-first digest batch with an exact cursor.

        Collect up to max_messages eligible messages starting at start_index
        and return them with the raw index just past the last row consumed.
        Filtered-out rows (non human/ai, empty text) advance the cursor but
        produce no messages, so the caller never skips a stretch of history
        the way a newest-N slice would."""
        messages: list[dict[str, Any]] = []
        next_index = max(0, start_index)
        provisional_ids = (
            {
                id(row)
                for row in (
                    (user_data or {}).get("provisional_draft_rows") or []
                )
            }
            if stop_at_provisional else frozenset()
        )
        for raw_index in range(next_index, len(conversation_history)):
            if id(conversation_history[raw_index]) in provisional_ids:
                # 在途草稿（buffer 等待中，投递决策未定）：游标停在它之前
                # ——越过后若草稿被真投递并撤标，这条回复就永远进不了
                # scoped 记忆。定局（投递/合并）后屏障解除。仅 focus
                # digest 用；finalize/teardown 穿透（按名单过滤），避免
                # 残留屏障卡死最终结算。
                break
            converted = self.conversation_slice_to_memory_messages(
                conversation_history[raw_index:raw_index + 1],
                user_data=user_data,
            )
            if converted and len(messages) + len(converted) > max_messages:
                break
            messages.extend(converted)
            next_index = raw_index + 1
        return messages, next_index

    def session_history_len(self, session_key: str) -> int:
        user_data = (getattr(self.plugin, "_user_sessions", {}) or {}).get(
            session_key
        )
        if not isinstance(user_data, dict):
            return 0
        session = user_data.get("session")
        return len(getattr(session, "_conversation_history", None) or [])

    def record_synthetic_prompt_rows(
        self, session_key: str, history_len_before: int,
        *, include_ai_rows: bool = False,
    ) -> None:
        """Synthetic control turns (rapid-fire flush / proactive speech) run
        the full pipeline, appending a fabricated human instruction row to
        the shared history. Record those rows into the exclusion list so
        digest/cache never extracts them as participant utterances; the
        delivered ai reply rows stay. Callers must take history_len_before
        INSIDE the session lock or a racing real user row gets mis-captured."""
        user_data = (getattr(self.plugin, "_user_sessions", {}) or {}).get(
            session_key
        )
        if not isinstance(user_data, dict):
            return
        if not user_data.get("is_group"):
            # 私聊 pre_buffer 场景：第二条起的真实用户消息只活在 flush
            # prompt 里（handle_private_message 在 pre_buffer 后直接返回，
            # 不进正常历史）——排除整行会让私聊长期记忆丢真实输入。包装
            # 噪声交提取端消化，完整性优先。群路径的成员消息每条都走过
            # 正常轮次、已在历史里，照常排除合成行。
            return
        session = user_data.get("session")
        history = getattr(session, "_conversation_history", None) or []
        rows = user_data.setdefault("undelivered_draft_rows", [])
        for msg in history[max(0, history_len_before):]:
            msg_type = getattr(msg, "type", "")
            if msg_type != "human" and not (
                include_ai_rows and msg_type == "ai"
            ):
                # include_ai_rows：合并 summary 由 OFF 时代缓冲输入衍生时，
                # 其 ai 行也不得入库（调用方判定 consent 时代）。
                continue
            if not any(existing is msg for existing in rows):
                rows.append(msg)

    def record_tail_undelivered_ai_row(self, session_key: str) -> None:
        """Mark the newest ai row as undelivered after a FAILED direct send.

        History-backed replies that bypass the buffer (synthetic turns, or
        no buffer service) already sit in the shared history when delivery
        fails — without this, the next digest/finalize extracts the unsent
        reply as durable memory. Failed sends are final (no retry), so the
        row goes straight to the exclusion list, not the provisional set."""
        user_data = (getattr(self.plugin, "_user_sessions", {}) or {}).get(
            session_key
        )
        if not isinstance(user_data, dict) or not user_data.get("is_group"):
            return
        session = user_data.get("session")
        history = getattr(session, "_conversation_history", None) or []
        for msg in reversed(history):
            if getattr(msg, "type", "") != "ai":
                continue
            rows = user_data.setdefault("undelivered_draft_rows", [])
            if not any(existing is msg for existing in rows):
                rows.append(msg)
            return

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
            or is_synthetic_source(getattr(context, "source_kind", ""))
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
            # 名额满：八个只说过几句的人各占一格、谁都到不了排空线，而群
            # 一直活跃也等不到 idle 结算——照原样直接 return 会把第九个
            # （可能很活跃的）发言人永久挡在成员记忆之外。改为催一次排空，
            # 排空成功会腾空名额，本轮先跳过、下一轮就能进。
            user_data["member_flush_due"] = True
            self.plugin.logger.info(
                f"成员记忆名额已满（{len(buckets)}），已请求排空，"
                f"{sender_id} 本轮跳过"
            )
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
        if len(messages) >= self.GROUP_MEMBER_MAX_MESSAGES:
            # 活跃群永远等不到 idle 结算，焦点 digest 又只冲群历史：到线
            # 就丢最早的，等于在服务端完全健康的情况下永久丢掉已授权的
            # 成员发言。标记待冲，由每轮的异步钩子后台排空。
            user_data["member_flush_due"] = True
        if len(messages) > self.GROUP_MEMBER_HARD_LIMIT:
            self.plugin.logger.warning(
                f"成员 {sender_id} 的记忆队列超过硬顶（冲刷持续失败），"
                f"丢弃最早的 {len(messages) - self.GROUP_MEMBER_HARD_LIMIT} 条"
            )
            del messages[:-self.GROUP_MEMBER_HARD_LIMIT]

    async def cache_session_delta(self, session_key: str, user_data: dict[str, Any]) -> int:
        # Busy group chats use one scoped extraction at session finalization.
        # Feeding each group turn into the legacy /cache pipeline would both
        # increase LLM cost and contaminate legacy-private memory.
        if user_data.get("is_group"):
            self.prune_draft_row_refs(user_data)
            session = user_data.get("session")
            history = getattr(session, "_conversation_history", []) or []
            backlog = len(history) - int(
                user_data.get("last_group_digest_index", 0) or 0
            )
            if backlog >= self.GROUP_DIGEST_BACKLOG_TRIGGER and not user_data.get(
                "group_digest_draining"
            ):
                user_data["group_digest_draining"] = True
                self.plugin._spawn_memory_sync_task(
                    self._drain_group_digest(session_key)
                )
            if user_data.get("member_flush_due") and not user_data.get(
                "member_drain_in_flight"
            ):
                # 每轮都会走到这里（legacy /cache 对群是 no-op），拿它当
                # 排空点：后台跑，不拖慢本轮回复；取会话锁避免与结算撞车。
                # in-flight 去重与 digest 排空同口径——记忆服务变慢时，连续
                # 轮次会不断排队新 task，全都堵在同一把会话锁上无界堆积。
                # 判据必须在建协程**之前**，否则重复时会留下没人 await 的
                # 协程。在飞时不清 due 标：下一轮再判，别把信号吞掉。
                user_data.pop("member_flush_due", None)
                user_data["member_drain_in_flight"] = True
                self.plugin._spawn_memory_sync_task(
                    self._drain_member_buckets(session_key)
                )
            return 0
        session = user_data.get("session")
        her_name = user_data.get("her_name")
        if not session or not her_name:
            return 0
        conversation_history = getattr(session, "_conversation_history", []) or []
        start_index = int(user_data.get("last_synced_index", 0))
        # /cache 跑在生成钩子时刻，早于投递决策：尾部刚生成的 ai 行可能
        # 随后被 buffer 合并成 summary 取代（打标发生在 schedule_reply，
        # 晚于此处）。滞后一拍——本轮回复留给下一次 cache/finalize，那时
        # 排除名单已定型；用户消息照常先落库。
        history_upper = len(conversation_history)
        while (
            history_upper > start_index
            and getattr(conversation_history[history_upper - 1], "type", "") == "ai"
        ):
            history_upper -= 1
        delta_messages = self.conversation_slice_to_memory_messages(
            conversation_history[:history_upper], start_index, user_data=user_data,
        )
        if not delta_messages:
            return 0
        result = await self.post_memory_history("cache", her_name, delta_messages, timeout=5.0)
        if result.get("status") == "error":
            raise RuntimeError(result.get("message", "cache failed"))
        user_data["last_synced_index"] = history_upper
        user_data["has_cached_memory"] = True
        return len(delta_messages)

    async def _drain_group_digest(self, session_key: str) -> None:
        """Push the group's backlog before it can be lost.

        The repetition guard swaps the whole conversation history for a
        bare system message; anything not yet persisted at that moment is
        gone. Draining on a backlog threshold does not remove that window
        (the guard lives in the shared omni client), it bounds it."""
        async def _drain() -> None:
            user_data = self.plugin._user_sessions.get(session_key)
            if not user_data:
                return
            try:
                if not user_data.get("is_group") or not user_data.get(
                    "memory_enabled"
                ):
                    return
                if user_data.get("pending_disable_settle"):
                    # opt-out 结算未完成（快速 re-enable 会让上面的 flag
                    # 重新为真）：积压交转变任务按 cutoff 结算，实时排空
                    # 用的是旧游标、没有 cutoff 也没有 nonconsent floor。
                    return
                if user_data.get("pending_enable_rebase") is not None:
                    # retain 结算后、ON rebase 前的 limbo：游标还停在
                    # opt-out 区间之前，此处推送会把 OFF 期间的行入库。
                    return
                group_id = str(user_data.get("group_id") or "").strip()
                her_name = user_data.get("her_name")
                session = user_data.get("session")
                history = getattr(session, "_conversation_history", []) or []
                if not group_id or not her_name or not history:
                    return
                await self._settle_group_digest_batches(
                    user_data=user_data, group_id=group_id, her_name=her_name,
                    reason="digest_backlog",
                    conversation_history=history,
                    last_group_digest_index=int(
                        user_data.get("last_group_digest_index", 0) or 0
                    ),
                    # 在途草稿处停下：把它当"未投递"过滤掉却推进游标，会让
                    # 随后真送出的那条回复永远留在游标之后、进不了 scoped
                    # 历史。finalize/teardown 仍穿透（那里命运已定）。
                    stop_at_provisional=True,
                )
            except Exception as exc:
                # 失败留待下一轮/idle 结算：游标停在最后一个成功批次。
                self.plugin.logger.warning(
                    f"[digest_backlog] 群积压冲刷失败 ({session_key}): {exc}"
                )
            finally:
                user_data.pop("group_digest_draining", None)

        await self.plugin._run_with_session_lock(session_key, _drain)

    async def _drain_member_buckets(self, session_key: str) -> None:
        """Flush member buckets that hit the cap, instead of dropping the
        oldest authorized turns of a group that never goes idle."""
        async def _drain() -> None:
            user_data = self.plugin._user_sessions.get(session_key)
            if not user_data:
                return
            try:
                if not user_data.get("is_group") or not user_data.get(
                    "memory_enabled"
                ):
                    return
                if not (getattr(self.plugin, "_qq_settings", {}) or {}).get(
                    "group_member_memory_enabled", False,
                ):
                    return
                group_id = str(user_data.get("group_id") or "").strip()
                her_name = user_data.get("her_name")
                if not group_id or not her_name:
                    return
                failed = await self._flush_member_buckets(
                    user_data, group_id=group_id, her_name=her_name,
                    reason="member_bucket_cap",
                )
                if failed:
                    # 冲失败的桶留在原地等下一轮：due 标已经被调度器消费
                    # 掉，不重新置起来的话要等这个成员再攒满一轮才会重试
                    # （硬顶兜底防无界增长）。
                    user_data["member_flush_due"] = True
                    self.plugin.logger.warning(
                        f"[member_bucket_cap] 群 {group_id} 有 {len(failed)} 个"
                        f"成员队列冲刷失败，留待下轮"
                    )
            finally:
                user_data.pop("member_drain_in_flight", None)

        await self.plugin._run_with_session_lock(session_key, _drain)

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
                # label 与 bucket 同生命周期：只弹 bucket 的话，活跃群会
                # 让 label 映射无限增长，而参与者名额是按 bucket 数算的，
                # 关闭成员记忆时（bucket 已空）也没人清这些残留。
                if isinstance(member_labels, dict):
                    member_labels.pop(sender_id, None)
                return None

        # 冲刷进行中标记：设置侧的快照合并看它决定"追加进这一代"还是
        # "另起一代"。往正在飞的那一代里追加会被它成功后的整桶 pop 带走。
        user_data["member_flush_in_progress"] = True
        flush_jobs = [
            _flush_one_member(sender_id, member_messages)
            for sender_id, member_messages in list(member_buckets.items())
            if sender_id and member_messages
        ]
        if not flush_jobs:
            self._finish_member_flush_generation(user_data)
            return []
        try:
            return [sid for sid in await asyncio.gather(*flush_jobs) if sid]
        finally:
            self._finish_member_flush_generation(user_data)

    @staticmethod
    def _finish_member_flush_generation(user_data: dict[str, Any]) -> None:
        """Promote the epoch that accumulated while a flush was in flight."""
        user_data.pop("member_flush_in_progress", None)
        next_buckets = user_data.pop("pending_settle_buckets_next", None)
        next_labels = user_data.pop("pending_settle_labels_next", None)
        if next_buckets:
            pending = user_data.setdefault("pending_settle_buckets", {})
            for sender, msgs in next_buckets.items():
                pending.setdefault(sender, []).extend(msgs)
            user_data["pending_member_settle"] = True
        if next_labels:
            user_data.setdefault("pending_settle_labels", {}).update(next_labels)

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
                failed: list[str] = []
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
                if failed and current.get("member_settle_rollback_pending"):
                    # 设置写盘失败的回滚正在排队：这些轮次是在先前已保存
                    # 的 consent 下收集的，结算又失败——清掉快照会让回滚
                    # 任务无从恢复、永久丢失。保留待回滚合并。
                    self.plugin.logger.warning(
                        f"[member_memory_disabled] 群 {group_id} 结算失败且"
                        f"回滚待处理，保留快照待恢复"
                    )
                    return
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
                # 群 digest 与成员 bucket 各自成败：某一批 history 反复提
                # 取失败时，成员队列（上限 50）会被后续正当流量顶掉最早的
                # 发言——它们的 scoped 请求本来是能成功的，不该被群侧的
                # 故障连累。
                try:
                    group_settled = await self._settle_group_digest_batches(
                        user_data=user_data, group_id=group_id,
                        her_name=her_name, reason=reason,
                        conversation_history=conversation_history,
                        last_group_digest_index=last_group_digest_index,
                    )
                except Exception as digest_error:
                    self.plugin.logger.error(
                        f"[{reason}] 群 {group_id} scoped 结算失败: {digest_error}"
                    )
                    group_settled = False
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
                if not group_settled:
                    # 成员侧已经排空，群 digest 留给下一轮（游标精确）。
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

    async def _settle_group_digest_batches(
        self, *, user_data: dict[str, Any], group_id: str, her_name: str,
        reason: str, conversation_history: list, last_group_digest_index: int,
        stop_at_provisional: bool = False,
    ) -> bool:
        """Push the group's pending history in batches, oldest first.

        Returns False when batches remain (the cap keeps one flush from
        holding the session lock for minutes); raises when a batch fails,
        so the cursor stays at the last confirmed batch."""
        digest_batches_left = 5
        while group_id:
            if digest_batches_left <= 0:
                self.plugin.logger.info(
                    f"[{reason}] 群 {group_id} 本轮结算达批次上限，剩余待下一轮"
                )
                return False
            digest_batches_left -= 1
            scoped_messages, next_index = self._slice_group_history_batch(
                conversation_history, last_group_digest_index,
                self.GROUP_HISTORY_MAX_MESSAGES,
                user_data=user_data,
                stop_at_provisional=stop_at_provisional,
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
        return True

    async def invalidate_group_sessions(
        self, *, enabled: bool, discard_only: bool = False,
    ) -> None:
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
                    if current.pop("group_settle_rollback_pending", None):
                        # 回滚路径（OFF 从未写盘成功）：fail-closed 清理把
                        # 游标推到了 len(history)，恢复 opt-out 之前的位置，
                        # 否则这段一直处于 ON 的已授权历史永远进不了库。
                        restored = current.pop("pre_optout_digest_index", None)
                        if restored is not None:
                            current["last_group_digest_index"] = min(
                                max(0, int(restored)), len(history),
                            )
                            current["memory_enabled"] = True
                            return
                    current.pop("pre_optout_digest_index", None)
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
                if discard_only:
                    # 回滚路径（开启保存失败）：失败窗口的历史是在"从未
                    # 成功保存的 opt-in"下收的，普通 OFF 结算会把它 digest
                    # 入库——恰好持久化本该拒绝的数据。按未授权丢弃：游标
                    # 推过窗口、清 bucket、flag 关。nonconsent floor 靠不
                    # 住（窗口内轮次可能在 flag=True 下完成、没 bump）。
                    current["memory_enabled"] = False
                    current.pop("group_opt_out_cutoff", None)
                    current["last_group_digest_index"] = len(history)
                    current.pop("group_member_memory_messages", None)
                    current.pop("group_member_memory_labels", None)
                    return
                # 有标会话按转变结算，不信可变的 per-request flag。
                current["memory_enabled"] = True
                finalized = False
                prev_progress = self._settlement_progress(current)
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
                    progress = self._settlement_progress(survivor)
                    if progress == prev_progress:
                        # 无进展 = 真失败；有进展（游标推进**或**成员队列
                        # 变短）= 只是撞上每轮批次上限，继续排——上限是防
                        # 锁饥饿的，不是放弃已授权数据的理由。
                        break
                    prev_progress = progress
                # 成功路径 session 已被 finalize 弹出（retain 场景除外——
                # 会话保留待 rebase）；仍把 flag 置 False：rebase 任务接手
                # 前的窗口里，idle flush 不得把 OFF 期间的行当 opt-in 入库，
                # rebase 任务会在推进游标越过它们之后再置回 True。
                current["memory_enabled"] = False
                if not finalized:
                    # 记下 opt-out 之前的游标：若这次 OFF 其实没写盘成功、
                    # 随后回滚回 ON，fail-closed 推到 len(history) 的游标会
                    # 让那段已授权历史被永久跳过（rebase 单调不回退）。
                    current.setdefault(
                        "pre_optout_digest_index",
                        int(current.get("last_group_digest_index", 0) or 0),
                    )
                    current["last_group_digest_index"] = len(history)
                    current.pop("group_member_memory_messages", None)
                    current.pop("group_member_memory_labels", None)
                    if not current.get("member_settle_rollback_pending"):
                        # 快照与活 bucket 同一口径：这次 opt-out 结算失败按
                        # fail-closed 丢弃，留着快照会让它在重新开启记忆或
                        # 成员开关变化时被后续 finalize 提交，绕过本次
                        # opt-out。回滚待办在场时保留——那条路径要靠它把
                        # 上一个已保存时代的收集恢复回活 bucket。
                        current.pop("pending_settle_buckets", None)
                        current.pop("pending_settle_labels", None)
                        current.pop("pending_member_settle", None)

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
