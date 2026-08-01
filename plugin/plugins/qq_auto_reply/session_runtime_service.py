from __future__ import annotations

import asyncio
import time
from typing import Any

from .pipeline_models import QQReplyContext


# 会被 handle_message 就地改写成 group 轮的通知类型：会话键必须与
# 改写后一致，否则这些轮次全程无锁。新增改写分支时同步补进来（
# test_converted_notice_types_take_the_group_session_lock 会盯着）。
CONVERTED_NOTICE_TYPES = frozenset({"group_increase", "poke"})


class QQSessionRuntimeService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def message_session_key(self, message: dict[str, Any]) -> str | None:
        """Session lock key for a raw inbound message.

        Notices that handle_message rewrites into real group turns must
        resolve here too: the key is taken before that rewrite, so leaving
        them unkeyed runs the converted turn with no session lock at all."""
        message_type = str(message.get("message_type") or "").strip()
        sender_id = str(message.get("user_id") or "").strip()
        if not sender_id:
            return None
        if message_type == "private":
            return self.plugin._build_session_key(sender_id=sender_id, is_group=False)
        if message_type == "group" or (
            message_type == "notice"
            and str(message.get("notice_type") or "").strip() in CONVERTED_NOTICE_TYPES
        ):
            # 入群通知/戳一戳会在 handle_message 里就地改写成 group 轮，而
            # 会话键在那之前就取好了：不认这两类通知，转换出来的真实群轮
            # 就绕开了会话锁，与同群普通消息并发跑历史——合成轮的排除窗口
            # 会把对方那一轮的真人发言一起圈进去，永久排除出 scoped 记忆。
            group_id = str(message.get("group_id") or "").strip()
            if not group_id:
                return None
            return self.plugin._build_session_key(sender_id=sender_id, is_group=True, group_id=group_id)
        return None

    def build_generation_session_key(self, context: QQReplyContext) -> str:
        session_key = self.plugin._build_session_key(
            sender_id=context.sender_id,
            is_group=context.is_group,
            group_id=context.group_id,
        )
        if context.ephemeral_session:
            return f"{session_key}:ephemeral:{time.time_ns()}"
        return session_key

    def prime_generation_session_state(
        self,
        user_data: dict[str, Any],
        *,
        session_key: str,
        context: QQReplyContext,
    ) -> tuple[Any, list[str]]:
        user_session = user_data["session"]
        reply_chunks = user_data["reply_chunks"]
        user_data["last_activity_at"] = time.time()
        user_data.setdefault("lock", asyncio.Lock())
        user_data["session_key"] = session_key
        user_data["sender_id"] = context.sender_id
        user_data["permission_level"] = context.permission_level
        user_data["is_group"] = context.is_group
        user_data["group_id"] = context.group_id
        user_data["user_title"] = context.user_title
        user_data["user_nickname"] = context.user_nickname
        persist = context.persist_memory
        if context.is_group and persist:
            # 以实时策略门控：请求可能在 OFF 写入前解析出 persist=True、
            # 会话在转变盖章循环之后才插入——陈旧的 True 会让 opt-out 后
            # 的历史被 idle flush 入库。显式 False（proactive 等）不受影响。
            persist = bool(
                (getattr(self.plugin, "_qq_settings", {}) or {}).get(
                    "group_memory_enabled", False,
                )
            )
        elif not context.is_group and persist:
            mode = user_data.get("private_memory_mode")
            turn_mode = getattr(context, "private_memory_mode", None) or (
                "legacy" if context.permission_level == "admin"
                else "participant"
            )
            if mode is None:
                # OFF 时代创建的会话首次拿到 persist=True（开关中途打开）：
                # 此刻补章。结算目标从此定格，per-turn 权限漂移不再改它。
                mode = turn_mode
                user_data["private_memory_mode"] = mode
            if mode != turn_mode:
                # One session has exactly one persistence domain. A permission
                # transition invalidates it; a queued turn from the other era
                # must not be retargeted into the surviving domain.
                persist = False
            elif mode == "participant":
                # 对偶群分支的实时策略门控：OFF 盖章循环之后才插入的会话
                # 不得凭陈旧的 True 继续收集。
                persist = bool(
                    (getattr(self.plugin, "_qq_settings", {}) or {}).get(
                        "private_participant_memory_enabled", False,
                    )
                )
            elif mode == "legacy" and turn_mode != "legacy":
                # 会话以 admin 语料结算、当前发言人却不再是 admin（降权）：
                # fail-closed 停写。继续写会把非 admin 的发言并进主人的
                # legacy 私聊语料；改道 participant 也不行——先前的历史
                # 属于 legacy 时代，混写更糟。与降权前的旧行为一致（旧代
                # 码里非 admin 私聊 persist 恒 False）。
                persist = False
        user_data["memory_enabled"] = persist
        user_data["memory_context_used"] = context.memory_context_used
        user_data["ephemeral_session"] = context.ephemeral_session
        user_data["login_status"] = context.login_status
        user_data["login_self_id"] = context.login_self_id
        user_data["login_nickname"] = context.login_nickname
        # 每轮巡检 digest 游标：OmniOfflineClient 的重复守卫会把
        # _conversation_history 重置成只剩 system message，旧游标若大于
        # 当前长度，之后追加的轮次都会被 digest/finalize 当成"已结算"
        # 永久跳过。此处钳制最及时（每个生成轮都会经过）。
        history_len = len(
            getattr(user_session, "_conversation_history", []) or []
        )
        if int(user_data.get("last_group_digest_index", 0) or 0) > history_len:
            user_data["last_group_digest_index"] = history_len
        participant_cursor = int(
            user_data.get("last_participant_digest_index", 0) or 0
        )
        if participant_cursor > history_len:
            # participant 游标同受重复守卫重置影响（对偶群游标钳制）。
            user_data["last_participant_digest_index"] = history_len
            if user_data.get("private_memory_mode") == "participant":
                previous_epoch = user_data.get("_speaker_trust_activity_epoch")
                next_epoch = time.time_ns()
                if str(next_epoch) == str(previous_epoch):
                    next_epoch += 1
                user_data["_speaker_trust_activity_epoch"] = next_epoch
        if int(user_data.get("nonconsent_history_end", 0) or 0) > history_len:
            # 历史被重置后旧的未授权边界同样越界：不钳的话 max() 地板会
            # 把重置后新授权轮当成已处理丢弃。
            user_data["nonconsent_history_end"] = history_len
        return user_session, reply_chunks

    async def get_session_lock(self, session_key: str) -> asyncio.Lock:
        async with self.plugin._session_locks_guard:
            lock = self.plugin._session_locks.get(session_key)
            if lock is None:
                lock = asyncio.Lock()
                self.plugin._session_locks[session_key] = lock
            return lock

    async def run_with_session_lock(self, session_key: str, coro_factory) -> Any:
        session_lock = await self.get_session_lock(session_key)
        async with session_lock:
            return await coro_factory()

    async def discard_session(self, session_key: str, *, reason: str) -> bool:
        """Returns True when the session is gone; False when a failed settle
        intentionally kept it (callers must NOT overwrite the key)."""
        # 记忆开启的会话在 discard 前先 best-effort 结算：群会话的 scoped
        # 缓冲只存在于 user_data（无 per-turn /cache），私聊 /cache 失败时
        # last_synced_index 之后的增量同样只活在本地历史——pop+close 都会
        # 销毁唯一副本。集中在这里做，prompt 变更/登录身份变化/超时等所有
        # discard 入口统一受益；ephemeral（memory_enabled falsy）自然跳过；
        # finalize 成功会自己弹出会话，下面的 pop 变成无害 no-op。
        # pending_disable_settle 也算：OFF 盖章后新轮 prime 会按实时配置把
        # flag 打成 False，但 cutoff 前的已授权缓冲还在等排队的 OFF 结算。
        # discard 必须像关机兜底一样临时恢复 flag，让本次重试真正进入
        # finalizer；只保留 marker 却仍以 False 调用会永远 early-return。
        # 在途的延迟回复必须先定局：会话被销毁后 buffer 任务仍可能成功
        # 送出回复，而 _clear_undelivered_marks 已无 user_data 可更新——
        # 参与者真收到的回复会永久缺席 scoped 记忆。这里取消它（与
        # stop_runtime 同口径：草稿保持未投递、屏障解除），再结算。
        buffer_service = getattr(self.plugin, "reply_buffer_service", None)
        if buffer_service is not None:
            buffer_service.cancel_pending(
                session_key, self.plugin._user_sessions.get(session_key),
            )
        if self.plugin._has_pending_session_settlement(session_key):
            # 空的 pending 槽不等于「已定局」：投递一确认，buffer 就把
            # pending 摘掉并把结算排到同一把会话锁上。此刻 finalize 会在
            # 那条已投递的 ai 行还挂着未投递标记时定稿——用户真收到的回复
            # 缺席 digest，随后的结算又改在一份已被弹走的 user_data 上。
            # 不 await（本函数常在会话锁内被调用，等于自锁），按既有约定
            # 返 False：会话留着，调用方下一轮重试。
            self.plugin.logger.warning(
                f"投递结算未完成，暂不销毁会话（{session_key}, reason={reason}）"
            )
            return False
        peek = self.plugin._user_sessions.get(session_key)
        finalized = False
        if peek and (
            peek.get("memory_enabled") or peek.get("pending_disable_settle")
        ):
            restore_disabled = bool(
                peek.get("pending_disable_settle")
                and not peek.get("memory_enabled")
            )
            if restore_disabled:
                peek["memory_enabled"] = True
            try:
                finalized = await self.plugin.session_memory_service.finalize_user_memory_session(
                    session_key, reason=f"discard:{reason}",
                )
            except Exception as exc:
                self.plugin.logger.error(
                    f"[{reason}] 丢弃前群记忆结算失败 ({session_key}): {exc}"
                )
            finally:
                survivor = self.plugin._user_sessions.get(session_key)
                if restore_disabled and survivor is not None:
                    survivor["memory_enabled"] = False
            if not finalized and session_key in self.plugin._user_sessions:
                # 结算失败（memory server 不可用等）：不弹出——弹出即销毁
                # 缓冲唯一副本。保留会话让下一轮 sweep/discard 重试结算；
                # 记忆完整性优先于"立刻换新会话"。
                self.plugin.logger.warning(
                    f"[{reason}] 群记忆结算未完成，保留会话与缓冲待重试 ({session_key})"
                )
                return False
        user_data = self.plugin._user_sessions.pop(session_key, None)
        session = user_data.get("session") if user_data else None
        if session is None and not finalized and peek:
            # finalize 的 early-exit（缺 her_name/session 元数据）会弹出
            # user_data 但不关闭 session——用进入时的引用兜底关闭，防泄漏。
            # finalize 成功时它自己已关闭，不能重复关。
            session = peek.get("session")
        if session:
            try:
                await session.close()
            except Exception as close_error:
                self.plugin.logger.warning(f"[{reason}] 关闭会话失败: {close_error}")
        return True

    async def session_housekeeping_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.plugin.SESSION_SWEEP_INTERVAL_SECONDS)
                # 群显示名刷新挂在这个既有周期上（TTL 门在服务内部，绝大多
                # 数轮次是零开销判断），不为它单开定时器。
                display_names = getattr(self.plugin, "display_name_service", None)
                if display_names is not None:
                    display_names.maybe_schedule_refresh()
                await self.plugin._flush_idle_memory_sessions()
                if getattr(self.plugin, "attention_service", None):
                    await self.plugin.attention_service.decay_all()
        except asyncio.CancelledError:
            raise
