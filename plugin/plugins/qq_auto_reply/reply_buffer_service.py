"""
LLM 驱动的回复缓冲与发送延迟

消息到达 → LLM 生成回复 + 等待时间 → 异步等待 → 发送
等待期间新消息到达 → LLM 决定合并/替换/丢弃 → 重置计时

LLM 通过 <wait>N</wait> 标签指定等待秒数（默认 0，立即发送）。
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Optional


class PendingReply:
    """待发送的回复（缓冲模式：收消息时不合成，等暂停后统一生成回复）"""
    __slots__ = ("buffered_texts", "wait_until", "task", "topic_hint", "message_count",
                 "sender_id", "is_group", "group_id", "_acked", "first_blocks",
                 "draft_rows", "mention_context", "has_nonconsent_input",
                 "consent_snapshot", "used_fallback_reply", "generation")

    def __init__(self, first_text: str, wait_seconds: float, sender_id: str, is_group: bool, group_id: str):
        self.buffered_texts: list[str] = [first_text]  # 缓冲的消息文本
        self.wait_until = time.time() + wait_seconds
        self.task: Optional[asyncio.Task] = None
        # 代际：每次新消息作废当前等待任务时 +1。归属**不能**只看
        # pending.task——追加消息的路径先 cancel、再 await（10-16 条的
        # 简短确认轮），替补任务要等那个 await 结束才建，窗口里被取消的
        # 那一代看自己仍是 pending.task，照样会把 pending 摘出表；等替补
        # 建好时表已空，它在归属检查处直接返回，那条消息永远没人回。
        # 计数器在 cancel 那一刻同步作废旧代，与替补何时创建无关。
        self.generation: int = 0
        self.topic_hint: str = ""
        self.message_count: int = 1
        self.sender_id = sender_id
        self.is_group = is_group
        self.group_id = group_id
        self._acked = False
        self.first_blocks: list = []
        # 本缓冲期截停的草稿历史行（消息对象引用）：单条路径投递后只撤
        # 这些行的未投递记录，绝不动此前合并场景留下的旧标。
        self.draft_rows: list = []
        # 最近一次截停轮的 context：单条路径真投递后补记 scoped mention
        # （合并场景丢弃——草稿没人看到，不推进 suppression 计数）。
        self.mention_context = None
        # 缓冲期内任一输入是在群记忆 OFF 时收到的：合并 summary 由这些
        # 输入衍生，若投递前切 ON，其 ai 行会落在 rebase 边界之后——不标
        # 记的话 OFF 时代内容经 summary 间接入库。
        self.has_nonconsent_input = False
        # 生成这条草稿时所依赖的记忆授权快照（{开关: 值}）：草稿在缓冲里
        # 等待期间授权可能被撤销，届时不得把已注入的 scoped/跨群内容送
        # 出去。开关本身没有会话级 teardown（尤其 cross-group），只能在
        # 投递前比对。
        self.consent_snapshot: dict = {}
        # 本草稿来自直连 fallback（共享历史没有对应 ai 行）：真投递后要
        # 补一行，否则 digest 只留半边对话。
        self.used_fallback_reply = False


class QQReplyBufferService:
    """LLM 驱动的异步回复缓冲"""

    DEFAULT_WAIT_SECONDS = 3.0      # 群聊默认等待 3 秒
    DEFAULT_WAIT_PRIVATE = 6.0      # 私聊默认等待 6 秒（对方往往在连续输出）

    @staticmethod
    def _participant_memory_at_receipt(pending: PendingReply) -> bool | None:
        """Receipt-time consent for a synthetic private buffer request."""
        if pending.is_group:
            return None
        return not pending.has_nonconsent_input

    def _mark_latest_draft_undelivered(
        self, session_key: str, pending: "PendingReply | None" = None,
    ) -> Any | None:
        """截停时把共享历史尾部的草稿 ai 行记入 user_data 的未投递名单。

        多条合并场景只投递新生成的 summary，被取代的草稿从未离开进程，
        却已经躺在会话历史里——digest/cache 若无差别序列化，会把没人见过
        的话提取成持久记忆，之后的回复能"回忆"从未发生的披露。

        名单放 user_data（插件自有 dict，永远可写）而非消息对象属性：
        对不可写/陌生消息类型不存在"打标失败被吞、草稿静默放行"的模式。
        名单持对象强引用，序列化侧按身份（id）比对——引用保活使 id 稳定，
        名单随会话 pop 一并销毁。单条路径真正投递后由
        _clear_undelivered_marks 只撤本次 pending 的行。"""
        user_data = (getattr(self.plugin, "_user_sessions", {}) or {}).get(
            session_key
        )
        if not isinstance(user_data, dict):
            return None
        session = user_data.get("session")
        history = getattr(session, "_conversation_history", None) or []
        for msg in reversed(history):
            if getattr(msg, "type", "") != "ai":
                continue
            rows = user_data.setdefault("undelivered_draft_rows", [])
            if not any(existing is msg for existing in rows):
                rows.append(msg)
            provisional = user_data.setdefault("provisional_draft_rows", [])
            if not any(existing is msg for existing in provisional):
                # 在途集合：投递决策未定型前，focus digest 的游标不得越过
                # 本行——若之后单条投递并撤标，越过的游标会让这条真回复
                # 永远进不了 scoped 记忆。单条投递或合并定局时移除。
                provisional.append(msg)
            if pending is not None and not any(
                existing is msg for existing in pending.draft_rows
            ):
                pending.draft_rows.append(msg)
            return msg
        return None

    def _consent_revoked_since(self, pending) -> bool:
        """True when any consent switch this draft relied on is now off."""
        snapshot = getattr(pending, "consent_snapshot", None)
        if not snapshot:
            return False
        settings = getattr(self.plugin, "_qq_settings", {}) or {}
        for key, was_enabled in snapshot.items():
            if was_enabled and not settings.get(key, False):
                return True
        return False

    @staticmethod
    def _settle_provisional(user_data, pending) -> None:
        """本 pending 的草稿命运已定（投递或被合并取代）：解除游标屏障。"""
        if not isinstance(user_data, dict):
            return
        provisional = user_data.get("provisional_draft_rows")
        if not provisional:
            return
        for row in pending.draft_rows:
            provisional[:] = [r for r in provisional if r is not row]

    @staticmethod
    def _bind_draft_to_pending(draft_row: Any, pending: "PendingReply") -> None:
        """把开头选中的草稿行绑到 pending——绝不重扫历史：10-16 条分支的
        rapid_fire_flush 确认回复在 schedule_reply 中途真实投出并追加进
        历史，重扫会把这条已发出的 ack 误抓进未投递名单、永久漏出记忆。"""
        if draft_row is None:
            return
        if not any(existing is draft_row for existing in pending.draft_rows):
            pending.draft_rows.append(draft_row)

    def _session_history_len(self, session_key: str) -> int:
        return self.plugin.session_memory_service.session_history_len(session_key)

    def _record_synthetic_prompt_rows(
        self, session_key: str, history_len_before: int,
    ) -> None:
        # 实现挪到 session_memory_service（proactive 合成轮同用）；语义见
        # record_synthetic_prompt_rows docstring。
        self.plugin.session_memory_service.record_synthetic_prompt_rows(
            session_key, history_len_before,
        )

    def _clear_undelivered_marks(
        self, session_key: str, pending: "PendingReply",
    ) -> None:
        """只撤销本次 pending 实际投递的草稿行——此前合并场景留下的旧
        未投递记录必须保留，否则"从未发生的回复"会重新进入 digest/cache。"""
        user_data = (getattr(self.plugin, "_user_sessions", {}) or {}).get(
            session_key
        )
        if not isinstance(user_data, dict):
            return
        rows = user_data.get("undelivered_draft_rows")
        if rows:
            for delivered in pending.draft_rows:
                rows[:] = [row for row in rows if row is not delivered]
        self._settle_provisional(user_data, pending)
    MAX_WAIT_SECONDS = 10.0         # 最多等 10 秒

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._pending: dict[str, PendingReply] = {}  # session_key → PendingReply

    # ── 提取 LLM 指定的等待时间 ──

    @classmethod
    def extract_wait_seconds(cls, raw_text: str) -> tuple[str, float]:
        """从 LLM 输出中提取 <wait>N</wait> 标签，返回 (清理后文本, 等待秒数)。"""
        import re
        match = re.search(r"<wait>(\d+(?:\.\d+)?)</wait>", raw_text, re.IGNORECASE)
        if match:
            try:
                secs = float(match.group(1))
                secs = max(0.0, min(cls.MAX_WAIT_SECONDS, secs))
                clean = re.sub(r"<wait>\d+(?:\.\d+)?</wait>", "", raw_text, count=1, flags=re.IGNORECASE)
                return clean.strip(), secs
            except ValueError:
                pass
        return raw_text, cls.DEFAULT_WAIT_SECONDS

    # ── 话题摘要 ──

    @staticmethod
    def _topic_hint(text: str) -> str:
        """从文本中提取简短话题摘要（前 30 字）。"""
        t = str(text or "").strip()
        return t[:30] if t else ""

    def has_pending(self, session_key: str) -> bool:
        """检查是否有等待中的缓冲（含 LLM 生成中未完成的）。"""
        p = self._pending.get(session_key)
        return p is not None and (p.task is None or not p.task.done())

    def pre_buffer(
        self,
        session_key: str,
        message_text: str,
        sender_id: str,
        is_group: bool,
        group_id: str,
        *,
        participant_memory_at_receipt: bool | None = None,
    ) -> bool:
        """消息到达时调用（LLM 生成前）：创建/追加缓冲，返回 True 表示跳过 pipeline。"""
        now = time.time()
        existing = self._pending.get(session_key)

        if existing and (existing.task is None or not existing.task.done()):
            # 已有缓冲 → 追加
            self._supersede(existing)
            existing.buffered_texts.append(message_text)
            # 私聊第二条起会在这里直接 return，不再构造 QQReplyRequest，也
            # 不会经过 schedule_reply(consented=...)。必须在接收边界把 OFF
            # 章并进同一个 pending，否则 OFF→ON 后 synthetic flush 会把这条
            # 输入误当成已授权。
            if not is_group and participant_memory_at_receipt is False:
                existing.has_nonconsent_input = True
            existing.message_count += 1
            n = existing.message_count
            if n <= 2:       extra = random.uniform(6.0, 10.0)
            elif n <= 4:     extra = random.uniform(10.0, 16.0)
            elif n <= 7:     extra = random.uniform(13.0, 19.0)
            elif n <= 16:    extra = random.uniform(6.0, 11.0)
            else:            extra = 0.0
            existing.wait_until = now + extra
            existing.task = asyncio.create_task(
                self._deliver_after_wait(session_key, existing, existing.generation)
            )
            self.plugin._emit_log("DEBUG", f"[Buffer] 预缓冲追加（共{n}条），等待 {extra:.1f}s，跳过 LLM 生成")
            return True

        # 无缓冲 → 创建新缓冲，等 pipeline 完成后 schedule_reply 会填充回复
        pending = PendingReply(
            first_text=message_text,
            wait_seconds=6.0,
            sender_id=sender_id,
            is_group=is_group,
            group_id=group_id,
        )
        pending.task = None  # 尚未启动等待（等 schedule_reply 来启动）
        if not is_group and participant_memory_at_receipt is False:
            pending.has_nonconsent_input = True
        self._pending[session_key] = pending
        return False  # 首次消息，走 pipeline

    def get_state(self) -> dict:
        """返回当前缓冲状态（供前端展示）。"""
        now = time.time()
        items = []
        for key, p in self._pending.items():
            remaining = max(0.0, p.wait_until - now)
            items.append({
                "session": key,
                "messages": p.message_count,
                "wait_remaining": round(remaining, 1),
                "is_group": p.is_group,
            })
        return {"pending": items, "count": len(items)}

    # ── 调度回复 ──

    async def schedule_reply(
        self,
        session_key: str,
        reply_text: str,
        raw_text: str,
        blocks: list,
        wait_seconds: float,
        sender_id: str,
        is_group: bool,
        group_id: str = "",
        extra_count: int = 0,
        history_backed: bool = True,
        mention_context=None,
        consented: bool = True,
        consent_snapshot: dict | None = None,
        used_fallback_reply: bool = False,
    ) -> None:
        """缓冲一条消息。如果已有等待中的缓冲，追加消息并重置等待计时。

        history_backed=False：本轮回复来自直连 LLM fallback，共享会话历史
        没有本轮的 ai 行——反扫会误把上一条已投递回复记成未投递草稿。"""
        # 存入缓冲前去除 XML 标签（raw_text 可能含 <msg><text> 等）
        import re
        clean_text = re.sub(r"<[^>]+>", "", str(reply_text or raw_text or "")).strip()
        if not clean_text:
            clean_text = str(reply_text or raw_text or "").strip()
        # 这条回复被截停进缓冲、尚未投递——历史尾部的 ai 行先记入未投递
        # 名单；单条路径真正送出后只撤本次 pending 的行，多条合并路径草稿
        # 永不投递、记录留存。pending 解析后用同一引用补关联，不重扫。
        # fallback 轮（history_backed=False）历史里没有本轮行，不标。
        draft_row = (
            self._mark_latest_draft_undelivered(session_key)
            if history_backed else None
        )
        existing = self._pending.get(session_key)
        if existing is not None:
            # 必须早于 10-16 ack / 17+ 强制总结这两条内嵌 pipeline：它们在
            # 本函数中途就跑，且 17+ 分支直接 return——尾部再打标就来不及，
            # 衍生 ai 行会漏出 include_ai_rows 清理，授权依赖也会在内嵌
            # 总结跑完之后才并起来（那条总结的 prompt 里已经含着本轮这条
            # 记忆派生草稿，而它自己的干净上下文没有可撤销的依赖）。
            if not consented:
                existing.has_nonconsent_input = True
            if consent_snapshot is not None:
                self._merge_consent_snapshot(existing, consent_snapshot)

        if existing and existing.task and not existing.task.done():
            # 已有缓冲 → 追加消息，转发子条数计入。作废必须早于下面
            # 10-16 条确认轮的 await：替补任务要等那个 await 结束才建。
            self._supersede(existing)
            existing.buffered_texts.append(clean_text)
            existing.first_blocks = blocks  # 保留原始 blocks（sticker/poke/record 等）
            existing.message_count += 1 + max(0, extra_count)
            # 动态等待：6~20s 正态分布，中间最长（峰值 ~16s），两头短
            n = existing.message_count
            if n <= 2:
                extra = random.uniform(6.0, 10.0)
            elif n <= 4:
                extra = random.uniform(10.0, 16.0)
            elif n <= 7:
                extra = random.uniform(13.0, 19.0)
            elif n <= 16:
                extra = random.uniform(6.0, 11.0)
            else:
                extra = 0.0
            existing.wait_until = time.time() + extra
            self.plugin._emit_log("DEBUG", f"缓冲追加（共{n}条），等待 {extra:.1f}s")

            # 10-16 条 → 走 pipeline 发简短确认
            if (
                10 <= n < 17
                and not getattr(existing, "_acked", False)
                and not self._consent_revoked_since(existing)
            ):
                # 内嵌轮的 prompt 里嵌着缓冲里的旧草稿（那是 bot 自己带着
                # 记忆生成的回复文本）。它会为自己重算一份干净快照，闸恒
                # 不响——授权已撤销时唯一正确的做法是根本不跑这一轮。
                existing._acked = True
                hist_before = self._session_history_len(session_key)
                try:
                    from .pipeline_models import QQReplyRequest
                    combined = "\n".join(f"[{i+1}] {t[:100]}" for i, t in enumerate(existing.buffered_texts[-5:]))
                    request = QQReplyRequest(
                        message_text=f"[系统] 对方连续发了多条消息，你需要发一句简短的话表示\"我在听\"吗？如果需要，只回复那句话（不超过10个字，要自然，符合人设）；如果不需要，回复空内容。以下是最近内容：\n{combined}",
                        sender_id=existing.sender_id or "0",
                        is_group=existing.is_group,
                        group_id=existing.group_id if existing.is_group else None,
                        is_at_bot=True,
                        source_kind="rapid_fire_flush",
                        fallback_to_text_on_voice_failure=True,
                        inherited_consent_snapshot=dict(
                            existing.consent_snapshot or {}
                        ),
                        participant_memory_at_receipt=(
                            self._participant_memory_at_receipt(existing)
                        ),
                    )
                    await self.plugin.reply_pipeline.run(request)  # handler 已持本会话锁，重取会自锁死
                except Exception as e:
                    self.plugin._emit_log("WARN", f"[Buffer] 简短确认失败: {e}")
                finally:
                    self._record_synthetic_prompt_rows(session_key, hist_before)
                    if existing.has_nonconsent_input:
                        # ack 的 prompt 内嵌了缓冲内容摘录：OFF 时代输入
                        # 存在时其 ai 行按同一口径排除。
                        self.plugin.session_memory_service.record_synthetic_prompt_rows(
                            session_key, hist_before, include_ai_rows=True,
                        )

            # 17+ 条 → 走 pipeline 强制总结 + 清空缓冲
            if n >= 17 and self._consent_revoked_since(existing):
                # 与 ack 同理：总结的 prompt 会原样引用这些记忆派生的旧
                # 草稿。授权撤销后不总结、不投递，草稿保持未投递（排除
                # 记录留存）并解除游标屏障——与 _deliver_after_wait 的
                # 撤销分支同一口径。
                self.plugin._emit_log(
                    "WARN", "[Buffer] 记忆授权已撤销，丢弃缓冲中的旧回复（强制总结轮）",
                )
                self._bind_draft_to_pending(draft_row, existing)
                self._supersede(existing)
                self._pending.pop(session_key, None)
                self._settle_provisional(
                    (getattr(self.plugin, "_user_sessions", {}) or {}).get(
                        session_key
                    ),
                    existing,
                )
                return
            if n >= 17:
                # 本分支提前 return，函数尾部的补关联不会执行——先把本轮
                # 草稿行绑上，否则 settle 按 draft_rows 清 provisional 时
                # 漏掉它，游标屏障永久卡死、此后所有消息进不了 scoped 记忆。
                self._bind_draft_to_pending(draft_row, existing)
                self._supersede(existing)
                self._pending.pop(session_key, None)
                hist_before = self._session_history_len(session_key)
                try:
                    from .pipeline_models import QQReplyRequest
                    combined = "\n".join(f"[{i+1}] {t[:150]}" for i, t in enumerate(existing.buffered_texts))
                    request = QQReplyRequest(
                        message_text=f"[系统] 对方连续发了以下消息，请用一两句话自然总结回复：\n{combined}",
                        sender_id=existing.sender_id or "0",
                        is_group=existing.is_group,
                        group_id=existing.group_id if existing.is_group else None,
                        is_at_bot=True,
                        source_kind="rapid_fire_flush",
                        fallback_to_text_on_voice_failure=True,
                        inherited_consent_snapshot=dict(
                            existing.consent_snapshot or {}
                        ),
                        participant_memory_at_receipt=(
                            self._participant_memory_at_receipt(existing)
                        ),
                    )
                    await self.plugin.reply_pipeline.run(request)  # handler 已持本会话锁，重取会自锁死
                except Exception as e:
                    self.plugin._emit_log("WARN", f"[Buffer] 强制总结失败: {e}")
                finally:
                    self._record_synthetic_prompt_rows(session_key, hist_before)
                    if existing.has_nonconsent_input:
                        # 与 _deliver_after_wait 的合并分支对齐：缓冲含
                        # OFF 时代输入时，衍生总结的 ai 行同样不得入库。
                        self.plugin.session_memory_service.record_synthetic_prompt_rows(
                            session_key, hist_before, include_ai_rows=True,
                        )
                    self._settle_provisional(
                        (getattr(self.plugin, "_user_sessions", {}) or {}).get(
                            session_key
                        ),
                        existing,
                    )
                return
        else:
            # 新缓冲：pre_buffer 可能已创建了占位 pending
            existing = self._pending.get(session_key)
            if existing and existing.task is None:
                # pre_buffer 占位 → 填充回复文本，启动等待
                existing.buffered_texts[0] = clean_text  # 替换占位文本为 LLM 回复
                existing.wait_until = time.time() + wait_seconds
                existing.sender_id = sender_id
                existing.is_group = is_group
                existing.group_id = group_id
                existing.first_blocks = blocks
                existing.topic_hint = self._topic_hint(raw_text or reply_text)
            else:
                # 完全新缓冲
                existing = PendingReply(
                    first_text=clean_text,
                    wait_seconds=wait_seconds,
                    sender_id=sender_id,
                    is_group=is_group,
                    group_id=group_id,
                )
                existing.first_blocks = blocks
                existing.message_count += max(0, extra_count)
                existing.topic_hint = self._topic_hint(raw_text or reply_text)
                self._pending[session_key] = existing

        # 启动等待任务
        existing.sender_id = sender_id  # 更新（可能变化）
        existing.is_group = is_group
        existing.group_id = group_id
        # 补关联：把开头选中的草稿行绑到本 pending（复用引用，不重扫历史），
        # 单条投递后可精确撤销。
        self._bind_draft_to_pending(draft_row, existing)
        existing.mention_context = mention_context
        existing.used_fallback_reply = bool(used_fallback_reply)
        if consent_snapshot is not None:
            self._merge_consent_snapshot(existing, consent_snapshot)
        if not consented:
            existing.has_nonconsent_input = True
        existing.task = asyncio.create_task(
            self._deliver_after_wait(session_key, existing, existing.generation)
        )

    def cancel_pending(self, session_key: str, user_data: Any) -> Any:
        """Kill a buffered reply outright and return its task, if any.

        The one entry point for "this session is going away" (discard,
        shutdown): unlike the in-flight cleanups this is deliberately
        generation-blind — every generation shares one PendingReply and all
        of them end here. The draft stays undelivered (the exclusion list
        keeps it out of memory) while the provisional barrier is released,
        so the digest cursor can move past a row nobody will ever deliver.
        Callers that need to join the cancellation use the return value."""
        pending = self._pending.pop(session_key, None)
        if pending is None:
            return None
        task = getattr(pending, "task", None)
        cancelled = None
        if task is not None and not task.done():
            task.cancel()
            cancelled = task
        self._settle_provisional(user_data, pending)
        return cancelled

    @staticmethod
    def _is_current_generation(pending: PendingReply, generation: int) -> bool:
        """True when the caller's generation is still the buffer's live one."""
        return int(getattr(pending, "generation", 0)) == int(generation)

    @staticmethod
    def _supersede(pending: PendingReply) -> None:
        """Cancel the waiting task and retire its generation, in one step.

        Must run before any await that follows the cancellation: the retired
        task resumes inside that window and would otherwise still pass as the
        owner."""
        task = getattr(pending, "task", None)
        if task is not None and not task.done():
            task.cancel()
        pending.generation = int(getattr(pending, "generation", 0)) + 1

    def _detach_pending(
        self, session_key: str, pending: PendingReply, generation: int,
    ) -> bool:
        """Give up the registry slot, but only for the generation that owns it.

        A new message cancels the running task and starts a replacement **on
        the same PendingReply** (both pre_buffer and schedule_reply reuse the
        object and only swap `task`), so object identity cannot tell the two
        apart. Popping from the cancelled generation strands that message: the
        replacement finds the slot empty at its own ownership check, returns,
        and nobody ever answers. Returns whether we are still the owner, so
        callers can gate the settlement the same way."""
        if not self._is_current_generation(pending, generation):
            return False
        if self._pending.get(session_key) is pending:
            self._pending.pop(session_key, None)
        return True

    @staticmethod
    def _merge_consent_snapshot(pending: PendingReply, snapshot: dict) -> None:
        """并集而非覆盖：合并进同一缓冲的旧草稿可能依赖了此刻已撤销的授权，
        用新快照（全 False）覆盖会让撤销检查看不到 true→false 的落差，旧
        草稿的内容还会被并进 summary prompt。"""
        merged = dict(getattr(pending, "consent_snapshot", None) or {})
        for key, was_enabled in (snapshot or {}).items():
            merged[key] = bool(merged.get(key)) or bool(was_enabled)
        pending.consent_snapshot = merged

    async def _deliver_after_wait(
        self, session_key: str, pending: PendingReply, generation: int = 0,
    ) -> None:
        """等待暂停后，汇总缓冲消息让 LLM 生成最终回复并发送。"""
        now = time.time()
        delay = max(0.0, pending.wait_until - now)
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return  # 新消息打断了等待

        if (
            self._pending.get(session_key) is not pending
            or not self._is_current_generation(pending, generation)
        ):
            # 两道都要：换了 pending 对象（缓冲已作废）与同对象换了代际
            # （新消息就地重建了任务）是两回事，后者对象身份分辨不出。
            return

        if self._consent_revoked_since(pending):
            # 等待期间授权被撤销：这条草稿是在旧授权下生成的（prompt 里
            # 可能带 scoped/跨群内容），不得再送出。草稿保持未投递、屏障
            # 解除、不记 mention。
            self.plugin._emit_log(
                "WARN", "[Buffer] 记忆授权已撤销，丢弃缓冲中的旧回复",
            )
            if self._detach_pending(session_key, pending, generation):
                self._settle_provisional(
                    (getattr(self.plugin, "_user_sessions", {}) or {}).get(
                        session_key
                    ),
                    pending,
                )
            return

        # 汇总缓冲内容
        texts = pending.buffered_texts
        if pending.message_count == 1:
            from .pipeline_models import QQMessageBlock, QQDeliveryPlan
            # 优先用原始 blocks（保留 sticker/poke/record），否则纯文本
            if pending.first_blocks:
                blocks = pending.first_blocks
            else:
                import re
                clean_text = re.sub(r"<[^>]+>", "", texts[0]).strip() or texts[0]
                blocks = [QQMessageBlock(text=clean_text)]
            plan = QQDeliveryPlan(
                target_type="group" if pending.is_group else "private",
                target_id=pending.group_id if pending.is_group else pending.sender_id,
                blocks=blocks,
                fallback_to_text_on_voice_failure=True,
            )
            try:
                delivery = await self.plugin.reply_delivery_node.deliver(
                    plan,
                    consent_gate=lambda: self._consent_revoked_since(pending),
                )
            except Exception as e:
                # NapCat 传输失败以异常上浮：与"未确认"同等对待——不跑
                # 清理会让 provisional 屏障永久卡死后续 digest。
                self.plugin._emit_log("WARN", f"[Buffer] 单条投递失败: {e}")
                delivery = None
            if delivery is None or not getattr(delivery, "delivered", False):
                # 发送未确认（开放平台失败返回 None 不抛异常）：草稿仍属
                # 未投递——排除记录保留、mention 不记，没送出去的回复不得
                # 进 scoped 提取。命运已定（不重试），解除游标屏障。
                if self._detach_pending(session_key, pending, generation):
                    # 屏障与 pending 同进退：替补代际还在缓冲时草稿命运未
                    # 定，解除屏障会让游标越过一条随后可能被投递的行。
                    self._settle_provisional(
                        (getattr(self.plugin, "_user_sessions", {}) or {}).get(
                            session_key
                        ),
                        pending,
                    )
                return
            # 投递一确认就把 pending 摘掉：结算被 shield 保住了，但**本
            # 协程**仍可能在等会话锁时被取消，pop 就轮不到执行。那之后
            # pre_buffer 会复用这个 pending，把新消息追加进 buffered_texts
            # ——而里面装的是 bot 自己刚发出去的回复，替补任务于是把自己的
            # 回复当成"对方又发了一条"去总结。
            # 投递期间就来了新消息（已有替补）时摘不掉：那条已投递的回复会
            # 混进替补的总结素材里，比起把新消息整条丢掉不回，这是更轻的
            # 代价——正常无替补路径不受影响。
            self._detach_pending(session_key, pending, generation)
            # 单条草稿真的送出去了：只撤本次 pending 的未投递记录——此前
            # 合并场景留下的旧记录必须留存。
            async def _settle_delivered() -> None:
                self._clear_undelivered_marks(session_key, pending)
                if pending.mention_context is None or not texts:
                    return
                from .pipeline_models import delivered_blocks_text

                delivered_text = (
                    delivered_blocks_text(pending.first_blocks) or texts[0]
                )
                if pending.used_fallback_reply:
                    # 对偶直投路径：fallback 草稿此刻才真正送达，补历史行
                    # （整条计划的正文，不只首块）。
                    try:
                        self.plugin.reply_generation_service.append_fallback_ai_row(
                            pending.mention_context, delivered_text,
                        )
                    except Exception as e:
                        self.plugin._emit_log(
                            "WARN", f"[Buffer] fallback 历史行补写失败: {e}",
                        )
                # mention 计数绑定实际投递：单条路径此刻才真正送达。
                try:
                    await self.plugin.reply_generation_service.record_scoped_mentions_on_delivery(
                        pending.mention_context, delivered_text,
                    )
                except Exception as e:
                    self.plugin._emit_log("WARN", f"[Buffer] mention 补记失败: {e}")

            # 必须在会话锁内：等待期间到达的群消息会照常跑完整条 pipeline，
            # 无锁追加会把 fallback 行插进那一轮的 human/ai 中间——随后的
            # 反扫会把这条"已投递"的行当成未投递草稿标掉（真回复被排除出
            # digest），而那一轮自己的未投递草稿反倒没被标。
            # shield：投递已经确认了，这之后的清理不能被取消。新消息到达
            # 时 pre_buffer 会取消这个仍在"活跃"的任务，若取消落在拿锁的
            # 等待里，撤未投递标、补 fallback 行、记 mention 全都不会跑——
            # 用户已经收到的回复会被永久当成未投递、进不了 scoped 记忆。
            # 显式建 task 并登记：shield 自己会造一个内层 task，但那个
            # task 没人跟踪——关机时外层被取消即算 done，_session_locks 会
            # 被清掉，而它还在改历史；紧接着重启建的新锁拦不住它，正好撞上
            # 这把锁本来要防的那个顺序竞态。
            settle_task = self.plugin._spawn_memory_sync_task(
                self.plugin._run_with_session_lock(session_key, _settle_delivered),
                session_key=session_key,
            )
            await asyncio.shield(settle_task)
            return

        # 多条缓冲 → 走 pipeline 生成总结（兼容 Lanlan）
        self.plugin._emit_log("INFO", f"缓冲{pending.message_count}条消息，走 pipeline 生成总结...")
        try:
            from .pipeline_models import QQReplyRequest
            combined = "\n".join(f"[{i+1}] {t[:150]}" for i, t in enumerate(texts))
            request = QQReplyRequest(
                message_text=f"[系统] 对方连续发了 {len(texts)} 条消息，请用一两句话自然总结回复：\n{combined}",
                sender_id=pending.sender_id or "0",
                is_group=pending.is_group,
                group_id=pending.group_id if pending.is_group else None,
                is_at_bot=True,
                source_kind="rapid_fire_flush",
                fallback_to_text_on_voice_failure=True,
                inherited_consent_snapshot=dict(
                    pending.consent_snapshot or {}
                ),
                participant_memory_at_receipt=(
                    self._participant_memory_at_receipt(pending)
                ),
            )
            async def _run_flush() -> Any:
                # before 必须在会话锁内取：锁外窗口插入的真实用户行会落进
                # [before:] 切片、被当成合成 prompt 误排除出记忆。
                hist_before = self._session_history_len(session_key)
                try:
                    return await self.plugin.reply_pipeline.run(request)
                finally:
                    self._record_synthetic_prompt_rows(session_key, hist_before)
                    if pending.has_nonconsent_input:
                        # 缓冲含 OFF 时代输入：summary 的 ai 行由它们衍生，
                        # 投递前切 ON 会让该行落在 rebase 边界后——同样排除。
                        self.plugin.session_memory_service.record_synthetic_prompt_rows(
                            session_key, hist_before, include_ai_rows=True,
                        )

            await self.plugin._run_with_session_lock(session_key, _run_flush)
        except Exception as e:
            self.plugin._emit_log("WARN", f"[Buffer] 总结pipeline失败: {e}")
        finally:
            # 合并定局（无论成败）：pending 必须出表、屏障必须解除——
            # 异常路径漏掉任何一个都会让 digest 永远停在死草稿行前。
            # 草稿永久未投递（排除名单保留）。
            # ⚠️只在自己仍是当代任务时定局：等总结 pipeline 期间新消息会
            # 取消本任务、就地建替补，此时定局的该是替补而不是我们——无条件
            # 收摊会把替补摘出表，它随即在归属检查处直接返回，那条新消息既
            # 没回复也没人再管。synthetic 标记不受归属影响：那些行是本轮
            # 真写进历史的，不标就会被当成用户发言进 digest。
            if self._detach_pending(session_key, pending, generation):
                self._settle_provisional(
                    (getattr(self.plugin, "_user_sessions", {}) or {}).get(
                        session_key
                    ),
                    pending,
                )

    # ── LLM 合并决策 ──

    async def _generate_ack(self, texts: list[str]) -> str:
        """让 LLM 决定是否发简短确认，以及确认内容。返回空字符串表示不发。"""
        try:
            from utils.config_manager import get_config_manager
            from utils.llm_client import create_chat_llm_async
            model_config = get_config_manager().get_model_api_config("conversation")
            if not model_config.get("base_url") or not model_config.get("model"):
                return ""

            recent = "\n".join(f"[{i+1}] {t[:100]}" for i, t in enumerate(texts[-5:]))
            llm = await create_chat_llm_async(
                model=str(model_config["model"]),
                base_url=str(model_config["base_url"]),
                api_key=str(model_config.get("api_key", "")),
                max_completion_tokens=50,
                timeout=5.0,
                provider_type=model_config.get("provider_type"),
            )
            from utils.token_tracker import set_call_type
            set_call_type("conversation")
            prompt = (
                "对方连续发了多条消息，以下是最近的内容：\n\n"
                f"{recent}\n\n"
                "你需要发一句简短的话表示\"我在听\"吗？如果需要，只输出那句话（不超过10个字，要自然，比如\"嗯嗯\"\"继续\"\"听着呢\"等，要符合你的人设）；"
                "如果不需要，只输出 SKIP。\n"
                "只输出确认语或 SKIP，不要输出其他内容。"
            )
            resp = await asyncio.wait_for(
                llm.ainvoke([{"role": "user", "content": prompt}]),
                timeout=5.0,
            )
            result = str(getattr(resp, "content", "") or "").strip()
            if result and result.upper() != "SKIP":
                return result[:20]
        except Exception:
            pass
        return ""

    async def _summarize_buffered(self, texts: list[str], is_group: bool) -> str:
        """缓冲结束后，让 LLM 看所有缓冲消息生成一条总结回复。"""
        try:
            combined = "\n".join(f"[{i+1}] {t[:150]}" for i, t in enumerate(texts))
            prompt = (
                f"对方连续发了 {len(texts)} 条消息，内容如下：\n\n"
                f"{combined}\n\n"
                "请用一两句话自然回复，总结或回应对方的要点。不要逐条回复，像真人在听对方讲完一堆话之后的自然反应。"
            )

            # 通过 OmniOfflineClient 调 LLM（兼容 Lanlan API）
            from main_logic.omni_offline_client import OmniOfflineClient
            from utils.config_manager import get_config_manager as _gcm
            import asyncio as _asyncio
            _cm = _gcm()
            # 线路会连 base_url 一起冻进下面的 OmniOfflineClient，先给仍在飞的区域
            # 探测一个收尾窗口。已落定时零开销；fail-open，不因探测出错而不回消息。
            try:
                await _cm.aensure_region_resolved()
            except Exception as _geo_err:
                self.plugin._emit_log("WARN", f"[GeoIP] 区域落定失败，退化到当前配置继续: {_geo_err}")
            _mc = _cm.get_model_api_config("conversation")
            resp_text = ""
            async def _on_text(t: str, _first: bool = False) -> None:
                nonlocal resp_text
                resp_text += t
            client = OmniOfflineClient(
                base_url=str(_mc.get("base_url", "")),
                api_key=str(_mc.get("api_key", "")),
                model=str(_mc.get("model", "")),
                on_text_delta=_on_text,
            )
            await _asyncio.wait_for(client.stream_text(prompt), timeout=10.0)
            result = resp_text.strip()
            if result:
                return result

            # 回退：raw LLM
            from utils.config_manager import get_config_manager
            from utils.llm_client import create_chat_llm_async
            model_config = get_config_manager().get_model_api_config("conversation")
            if not model_config.get("base_url") or not model_config.get("model"):
                return ""
            llm = await create_chat_llm_async(
                model=str(model_config["model"]), base_url=str(model_config["base_url"]),
                api_key=str(model_config.get("api_key", "")),
                max_completion_tokens=300, timeout=10.0,
                provider_type=model_config.get("provider_type"),
            )
            resp = await _asyncio.wait_for(llm.ainvoke([{"role": "user", "content": prompt}]), timeout=10.0)
            result = str(getattr(resp, "content", "") or "").strip()
            return result if result else ""
        except Exception as e:
            self.plugin._emit_log("WARN", f"[Buffer] 总结LLM调用失败: {e}")
            return ""
