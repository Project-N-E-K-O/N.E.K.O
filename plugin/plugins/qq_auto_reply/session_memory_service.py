from __future__ import annotations

from .display_name_service import QQDisplayNameService
from .pipeline_models import is_synthetic_source

import asyncio
import hashlib
import json
import time
from typing import Any


_CURRENT_TURN_AI_ROW = object()



class _HistoryBoundary(int):
    """A history length that remembers which session object produced it.

    Synthetic turns capture the boundary before running the pipeline and
    mark everything appended after it. The pipeline may replace the session
    itself (identity/character change), and an index from the old history
    means nothing in the new one — carrying the session along is what lets
    the marker notice. A strong reference on purpose: id() would let a
    freed session's address come back as a false match."""

    def __new__(cls, value: int, session: object = None) -> "_HistoryBoundary":
        obj = super().__new__(cls, value)
        obj.session = session
        return obj


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
    # 一趟排空的形状：最多 GROUP_MEMBER_MAX_PARTICIPANTS 个桶，打包成若干
    # **批**（每批 ≤200 条消息、≤8 段，见 _pack_member_batches），并发
    # MEMBER_FLUSH_CONCURRENCY，每批发一次 scoped history segments 请求
    # （那是一次 LLM 抽取，所以超时给得很宽）。批的单请求输入工作量上界
    # 与旧的逐成员单发同口径（200 条），批数 ≤ 桶数 ≤ 名额，所以下面按
    # "波数 × 单发超时"推导的等待上限在最坏情形下与逐成员时代一致。
    # 别写死——这些数任何一个改了，等待上限必须跟着走。
    MEMBER_FLUSH_CONCURRENCY = 4
    # speaker_label 的长度上限，与 /scoped_history 路由的校验同一个数。
    MEMBER_LABEL_MAX_CHARS = 64
    SCOPED_HISTORY_TIMEOUT_SECONDS = 30.0
    # 结算前等待该会话在途排空的上限。分两档，判据是"放弃等待要付什么"：
    # · 短档（idle sweep）：放弃只是多等一个 sweep 周期，下轮自然重来。
    # · 长档（关机 / opt-out 结算）：放弃就换成丢数据或让已提升的一代
    #   失去消费者，没有便宜的下一轮。所以长档按**整趟**排空的最坏用时
    #   算（波数 × 单发超时），而不是一次请求——只覆盖一次请求的话，第二
    #   波还攥着快照时等待就到点了。这正好是改前的实际行为（整趟排空持
    #   会话锁，这些路径只能干等到它结束），因此不构成关机变慢的回归。
    # 波数 × 单发超时只是排空的**理论**用时；信号量交接、超时清理、gather
    # 收尾、任务调度都还要时间。等待上限必须严格大于它，恰好相等会在排空
    # 正要返回的那一刻判它"还在途"——白等了整整一趟，然后照样按超时处理。
    SETTLE_JOIN_SLACK_SECONDS = 5.0
    SETTLE_JOIN_TIMEOUT_SECONDS = 5.0
    SETTLE_JOIN_TIMEOUT_LONG_SECONDS = SCOPED_HISTORY_TIMEOUT_SECONDS * (
        -(-GROUP_MEMBER_MAX_PARTICIPANTS // MEMBER_FLUSH_CONCURRENCY)
    ) + SETTLE_JOIN_SLACK_SECONDS

    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def _await_pending_session_settlement(
        self, session_key: str, *, timeout: float | None = None,
    ) -> bool:
        """Let this session's registered settlement work finish first.

        Returns False when it is still outstanding after the bound — the
        caller decides whether that session may be finalized anyway.

        finalize pops the session, and the member drain holds the only
        copy of the buckets it popped out of the live mapping: pop first
        and the drain has nowhere to hand its failures back to.

        Must run OUTSIDE the session lock. The drain takes that lock again
        to return its snapshot, so waiting while holding it deadlocks."""
        tasks = [
            task
            for task in (
                (getattr(self.plugin, "_session_settle_tasks", None) or {})
                .get(session_key) or ()
            )
            if not task.done()
        ]
        if not tasks:
            return True
        _done, still_pending = await asyncio.wait(
            tasks,
            timeout=(
                self.SETTLE_JOIN_TIMEOUT_SECONDS if timeout is None else timeout
            ),
        )
        if not still_pending:
            return True
        # 不取消：取消一趟排空等于丢掉它攥着的那批已授权发言。
        self.plugin.logger.warning(
            f"等待会话结算工作超时（{session_key}），仍有 "
            f"{len(still_pending)} 项在途"
        )
        return False

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

            # 锁外先等在途排空：finalize 会弹出会话，而排空攥着从活映射
            # 里 pop 出来的唯一副本（discard_session 有自己的推迟判据，
            # 这条路径没有）。
            if not await self._await_pending_session_settlement(session_key):
                # 等不到就整轮跳过：进程还在跑，下一次 sweep 会重来，而
                # idle 会话多等一个 sweep 周期没有任何代价——把它弹掉才有
                # （排空就再也没地方还失败的桶了）。
                self.plugin.logger.info(
                    f"排空在途，本轮跳过 idle 结算（{session_key}），下次 sweep 重试"
                )
                continue
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

            # 同 idle 路径：关机时 shutdown 只 join 1s 就放行，30s 的
            # scoped POST 完全可能还在飞——这里再给一次有界的等待，别在
            # 排空还攥着快照时把会话弹掉（弹掉即销毁唯一副本）。
            # 与 idle 不同的是**等不到也照样结算**：这是最后一次机会，跳过
            # 意味着这个会话的群 digest 也一起没了（那通常比成员桶大得多）。
            # 两种损失里选小的，排空自己会把丢掉的量记进 error 日志。
            if not await self._await_pending_session_settlement(
                session_key,
                timeout=self.SETTLE_JOIN_TIMEOUT_LONG_SECONDS,
            ):
                self.plugin.logger.warning(
                    f"排空仍在途但已是最后一次结算机会（{session_key}, "
                    f"reason={reason}），继续结算群侧数据"
                )
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
            # participant 结算的进展同样按游标衡量：少了它，关机 while
            # 循环会把"私聊批次上限打断"误判成零进展而提前放弃。
            int(user_data.get("last_participant_digest_index", 0) or 0),
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
        return _HistoryBoundary(
            len(getattr(session, "_conversation_history", None) or []), session,
        )

    def record_synthetic_prompt_rows(
        self, session_key: str, history_len_before: int,
        *, include_ai_rows: bool = False,
        replacement_user_texts: list[str] | None = None,
    ) -> int:
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
            return 0
        session = user_data.get("session")
        history = getattr(session, "_conversation_history", None) or []
        baseline = int(history_len_before)
        measured_on = getattr(history_len_before, "session", None)
        if measured_on is not None and measured_on is not session:
            # 本轮把会话换掉了（登录身份/角色变化触发重建）：旧长度在新
            # 历史上没有意义，旧的更长时切片直接切空、那条捏造的 [系统]
            # 行就当成真人发言进了 scoped 记忆。新会话里现有的行全是本轮
            # 写的，从头算即可。
            baseline = 0

        if (
            not user_data.get("is_group")
            and user_data.get("private_memory_mode") == "participant"
        ):
            rows = user_data.setdefault("undelivered_draft_rows", [])
            # Private pre-buffer inputs after the first one never traverse the
            # pipeline. Replace the fabricated rapid-fire human control row
            # in-place with those real inputs, preserving their position
            # before the generated AI reply. This avoids both prompt-wrapper
            # leakage and loss of the actual participant messages.
            human_indexes = [
                index
                for index in range(max(0, baseline), len(history))
                if getattr(history[index], "type", "") == "human"
            ]
            texts = [
                str(value).strip()
                for value in (replacement_user_texts or [])
                if str(value).strip()
            ]
            inserted = 0
            if human_indexes and texts:
                try:
                    from langchain_core.messages import HumanMessage

                    replacement_rows = [
                        HumanMessage(content=value) for value in texts
                    ]
                except Exception:
                    from types import SimpleNamespace

                    replacement_rows = [
                        SimpleNamespace(type="human", content=value)
                        for value in texts
                    ]
                first_index = human_indexes[0]
                for index in reversed(human_indexes[1:]):
                    del history[index]
                history[first_index:first_index + 1] = replacement_rows
                inserted = len(replacement_rows)
            else:
                for index in human_indexes:
                    msg = history[index]
                    if not any(existing is msg for existing in rows):
                        rows.append(msg)
            if include_ai_rows:
                for msg in history[max(0, baseline):]:
                    if (
                        getattr(msg, "type", "") == "ai"
                        and not any(existing is msg for existing in rows)
                    ):
                        rows.append(msg)
                # _run_session_generation stamped the OFF-era boundary before
                # this synthetic control row was expanded into multiple real
                # inputs. Move the floor past every newly materialized row so
                # none can be collected after participant memory is re-enabled.
                user_data["nonconsent_history_end"] = max(
                    int(user_data.get("nonconsent_history_end", 0) or 0),
                    len(history),
                )
            return inserted

        if not user_data.get("is_group"):
            return 0
        rows = user_data.setdefault("undelivered_draft_rows", [])
        for msg in history[max(0, baseline):]:
            msg_type = getattr(msg, "type", "")
            if msg_type != "human" and not (
                include_ai_rows and msg_type == "ai"
            ):
                # include_ai_rows：合并 summary 由 OFF 时代缓冲输入衍生时，
                # 其 ai 行也不得入库（调用方判定 consent 时代）。
                continue
            if not any(existing is msg for existing in rows):
                rows.append(msg)
        return 0

    def record_tail_undelivered_ai_row(
        self, session_key: str, ai_row: Any = _CURRENT_TURN_AI_ROW,
    ) -> None:
        """Mark the newest ai row as undelivered after a FAILED direct send.

        History-backed replies that bypass the buffer (synthetic turns, or
        no buffer service) already sit in the shared history when delivery
        fails — without this, the next digest/finalize extracts the unsent
        reply as durable memory. Failed sends are final (no retry), so the
        row goes straight to the exclusion list, not the provisional set."""
        user_data = (getattr(self.plugin, "_user_sessions", {}) or {}).get(
            session_key
        )
        if not isinstance(user_data, dict):
            return
        if (
            not user_data.get("is_group")
            and user_data.get("private_memory_mode") != "participant"
        ):
            return
        session = user_data.get("session")
        history = getattr(session, "_conversation_history", None) or []
        if ai_row is not _CURRENT_TURN_AI_ROW:
            # Direct delivery carries the originating row identity across its
            # await. A later generation may already have replaced the mutable
            # session-wide pointer by the time this send fails.
            row = ai_row
            if row is None or not any(existing is row for existing in history):
                return
            rows = user_data.setdefault("undelivered_draft_rows", [])
            if not any(existing is row for existing in rows):
                rows.append(row)
            provisional = user_data.get("provisional_draft_rows", [])
            user_data["provisional_draft_rows"] = [
                existing for existing in provisional if existing is not row
            ]
            return
        if "current_turn_ai_row" in user_data:
            # 生成路径记下了本轮到底写没写 ai 行：按身份标，没写就什么都不
            # 标。扫"最新一条 ai"在本轮无行时会打到上一条已投递的回复上。
            row = user_data.get("current_turn_ai_row")
            if row is None or not any(existing is row for existing in history):
                return
            rows = user_data.setdefault("undelivered_draft_rows", [])
            if not any(existing is row for existing in rows):
                rows.append(row)
            return
        for msg in reversed(history):
            if getattr(msg, "type", "") != "ai":
                continue
            rows = user_data.setdefault("undelivered_draft_rows", [])
            if not any(existing is msg for existing in rows):
                rows.append(msg)
            return

    def record_provisional_ai_row(self, session_key: str, ai_row: Any) -> None:
        """Fence an exact history row while direct delivery is in flight."""
        user_data = (getattr(self.plugin, "_user_sessions", {}) or {}).get(
            session_key
        )
        if not isinstance(user_data, dict):
            return
        if (
            not user_data.get("is_group")
            and user_data.get("private_memory_mode") != "participant"
        ):
            return
        history = getattr(
            user_data.get("session"), "_conversation_history", None,
        ) or []
        if ai_row is None or not any(existing is ai_row for existing in history):
            return
        for key in ("undelivered_draft_rows", "provisional_draft_rows"):
            rows = user_data.setdefault(key, [])
            if not any(existing is ai_row for existing in rows):
                rows.append(ai_row)

    def settle_provisional_ai_row(
        self, session_key: str, ai_row: Any, *, delivered: bool,
    ) -> None:
        """Resolve a direct-send fence without relying on a mutable tail."""
        user_data = (getattr(self.plugin, "_user_sessions", {}) or {}).get(
            session_key
        )
        if not isinstance(user_data, dict) or ai_row is None:
            return
        provisional = user_data.get("provisional_draft_rows", [])
        user_data["provisional_draft_rows"] = [
            existing for existing in provisional if existing is not ai_row
        ]
        undelivered = user_data.setdefault("undelivered_draft_rows", [])
        if delivered:
            user_data["undelivered_draft_rows"] = [
                existing for existing in undelivered if existing is not ai_row
            ]
        elif not any(existing is ai_row for existing in undelivered):
            undelivered.append(ai_row)

    def record_group_member_turn(self, user_data: dict[str, Any], context: Any) -> None:
        """Keep bounded, actor-attributed user turns for optional member memory."""
        settings = getattr(self.plugin, "_qq_settings", {}) or {}
        if not settings.get("group_member_memory_enabled"):
            return
        if not settings.get("group_memory_enabled"):
            # 子开关不能越过父开关：群记忆关着时不收集任何成员发言。
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
        # "(sender_id)" 后缀是这条 label 的**保底可追溯部分**，必须活过截断：
        # 昵称既可能是群名片（用户自己改）也可能是后台设的备注名；后者的
        # 新写入已经校验，但历史配置仍可能含超长/结构字符。先按剩余额度裁
        # 昵称再拼后缀，否则一个 64 字以上的历史昵称会把后缀整个挤掉——
        # 服务端中和完只剩空串，那一批会拖住同批其他成员反复重试。
        suffix = f"({sender_id})"
        nickname_budget = self.MEMBER_LABEL_MAX_CHARS - len(suffix)
        if nickname and nickname_budget > 0:
            labels[sender_id] = f"{nickname[:nickname_budget]}{suffix}"
        else:
            labels[sender_id] = sender_id[:self.MEMBER_LABEL_MAX_CHARS]
        messages = buckets.setdefault(sender_id, [])
        # context.permission_level is the group's admission tier here, not
        # this member's user permission.  Resolve the member profile directly
        # so a trusted group cannot promote every speaker's trust baseline.
        permission_level = self._speaker_permission_level_for(sender_id)
        sequence = user_data.get("group_member_message_sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            # Hot-reload compatibility: stamp already buffered rows before
            # assigning the first sequence to a new row. Message content can
            # never choose or influence this code-side ordering key.
            sequence = 0
            for buffered in buckets.values():
                for buffered_message in buffered or []:
                    if not isinstance(buffered_message, dict):
                        continue
                    sequence += 1
                    buffered_message.setdefault("_speaker_sequence", sequence)
            user_data["group_member_message_sequence"] = sequence
        sequence += 1
        user_data["group_member_message_sequence"] = sequence
        activity_id = hashlib.sha256(
            f"{sender_id}|{time.time_ns()}|{len(messages)}|{text}".encode("utf-8")
        ).hexdigest()[:24]
        messages.append({
            "role": "user",
            "content": [{"type": "text", "text": text}],
            # These request-side snapshots are ignored by the LLM message
            # converter. They let a delayed flush split one speaker's bucket
            # by the permission held when each message was authored.
            "_speaker_permission_level": permission_level,
            "_speaker_activity_id": activity_id,
            "_speaker_sequence": sequence,
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
                # 登记成该会话的结算工作：排空的 POST 现在跑在锁外，会话锁
                # 不再是天然屏障，discard_session 会在它还攥着快照时把会话
                # 弹掉、把失败的桶留在一份没人消费的 user_data 上。带上
                # session_key，discard 就会按既有约定推迟并下轮重试。
                self.plugin._spawn_memory_sync_task(
                    self._drain_member_buckets(session_key),
                    session_key=session_key,
                )
            return 0
        if user_data.get("private_memory_mode") == "participant":
            # 私聊 participant 会话绝不进 legacy /cache（那是主人的语料）；
            # 与群分支同构，这里只当调度点——积压过线时后台冲一次 scoped
            # digest（复读守卫会把共享历史整段换掉，未落盘轮次当场消失，
            # 与群 digest 同一根因同一治法）。必须放在下面的 memory_enabled
            # 闸**之前**：participant 会话的 memory_enabled 为 True，落到
            # 闸后就会继续走 legacy /cache 分支。
            if not user_data.get("memory_enabled"):
                return 0
            self.prune_draft_row_refs(user_data)
            session = user_data.get("session")
            history = getattr(session, "_conversation_history", []) or []
            backlog = len(history) - int(
                user_data.get("last_participant_digest_index", 0) or 0
            )
            if backlog >= self.GROUP_DIGEST_BACKLOG_TRIGGER and not user_data.get(
                "participant_digest_draining"
            ):
                user_data["participant_digest_draining"] = True
                self.plugin._spawn_memory_sync_task(
                    self._drain_participant_digest(session_key)
                )
            return 0
        if not user_data.get("memory_enabled"):
            # 私聊 legacy 语料是**主人**的：非 admin 好友的 memory_enabled
            # 恒为 False，他们的消息一条都不得进 /cache。闸放在被调方而不是
            # 调用方——_cache_session_delta 有多个入口（成功路径自己判过，
            # 静默轮的记忆管家、旁路调度都没判），逐个补必漏。群分支在上面
            # 就返回了，不受影响（那两个排空各有自己的闸）。
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

    async def _drain_participant_digest(self, session_key: str) -> None:
        """私聊 participant 会话的积压冲刷（对偶 _drain_group_digest）。

        同一根因：复读守卫会把共享历史整段换成只剩系统消息，未落盘的
        轮次当场消失；按积压线主动落盘把损失压到有界。整段在会话锁内跑，
        与结算天然串行。"""
        async def _drain() -> None:
            user_data = self.plugin._user_sessions.get(session_key)
            if not user_data:
                return
            try:
                if (
                    user_data.get("private_memory_mode") != "participant"
                    or not user_data.get("memory_enabled")
                ):
                    return
                if user_data.get("pending_disable_settle"):
                    # opt-out 结算未完成：实时排空用的是旧游标、没有
                    # cutoff 围栏，交转变/兜底任务按 cutoff 结算。
                    return
                if not (getattr(self.plugin, "_qq_settings", {}) or {}).get(
                    "private_participant_memory_enabled", False,
                ):
                    # 写点前复检实时策略（对偶读侧）：OFF 之后不再推送。
                    return
                sender_id = str(user_data.get("sender_id") or "").strip()
                her_name = user_data.get("her_name")
                session = user_data.get("session")
                history = getattr(session, "_conversation_history", []) or []
                if not sender_id or not her_name or not history:
                    return
                # Generation and this drain use different locks. Freeze the
                # authorized prefix before the first HTTP await so rows added
                # after a concurrent opt-out cannot enter a later batch.
                history_snapshot = list(history)
                cursor = max(
                    0, int(user_data.get("last_participant_digest_index", 0) or 0),
                )
                # 未授权边界地板（对偶 finalize / focus digest）：OFF 时代
                # 的行不得被实时排空推上去。
                cursor = max(
                    cursor, int(user_data.get("nonconsent_history_end", 0) or 0),
                )
                if cursor > len(history):
                    cursor = len(history)
                    user_data["last_participant_digest_index"] = cursor
                await self._settle_participant_digest_batches(
                    user_data=user_data, sender_id=sender_id,
                    her_name=her_name, reason="participant_digest_backlog",
                    conversation_history=history_snapshot,
                    last_participant_digest_index=cursor,
                    # 在途草稿处停下（对偶群积压冲刷）：越过后草稿被真投递
                    # 时，那条回复永远进不了 scoped 历史。
                    stop_at_provisional=True,
                )
            except Exception as exc:
                # 失败留待下一轮/idle 结算：游标停在最后一个成功批次。
                self.plugin.logger.warning(
                    f"[participant_digest_backlog] 私聊积压冲刷失败 "
                    f"({session_key}): {exc}"
                )
            finally:
                user_data.pop("participant_digest_draining", None)

        await self.plugin._run_with_session_lock(session_key, _drain)

    def _participant_speaker_label(
        self, user_data: dict[str, Any], sender_id: str,
    ) -> str:
        """私聊对话方的 speaker_label（备注名 > 昵称 > 纯 QQ 号）。

        与 record_group_member_turn 的组装规则严格同构："(sender_id)"
        后缀是保底可追溯部分，必须活过截断——否则超长昵称会把后缀挤掉，
        服务端中和完只剩空串。"""
        permission_mgr = getattr(self.plugin, "permission_mgr", None)
        custom_nickname = (
            permission_mgr.get_nickname(sender_id) if permission_mgr else None
        )
        nickname = str(
            custom_nickname or user_data.get("user_nickname") or ""
        ).strip()
        suffix = f"({sender_id})"
        nickname_budget = self.MEMBER_LABEL_MAX_CHARS - len(suffix)
        if nickname and nickname_budget > 0:
            return f"{nickname[:nickname_budget]}{suffix}"
        return str(sender_id)[:self.MEMBER_LABEL_MAX_CHARS]

    async def _settle_participant_digest_batches(
        self, *, user_data: dict[str, Any], sender_id: str, her_name: str,
        reason: str, conversation_history: list,
        last_participant_digest_index: int,
        stop_at_provisional: bool = False,
    ) -> bool:
        """私聊 participant 历史的分批结算（对偶 _settle_group_digest_batches）。

        与群 digest 的差异只在请求形状：私聊历史含 user（对方）与 ai
        （角色）两种行，走 segments 批形状会把 ai 行也前缀成对方发言——
        所以用 legacy 单发形状（speaker_label 只顶替 user 轮的渲染名），
        并随请求带 speaker_trust / display_name（与群成员段同一组字段，
        trust 形状一致是发言人信赖度阶段一的硬约束）。"""
        digest_batches_left = 5
        speaker_label = self._participant_speaker_label(user_data, sender_id)
        display_name = QQDisplayNameService.display_name_from_label(
            speaker_label, sender_id,
        )
        permission_level = self._speaker_permission_level_for(
            sender_id, user_data.get("permission_level"),
        )
        speaker_trust = self._speaker_trust_for(sender_id, permission_level)
        speaker_is_owner = self._speaker_is_owner_for(
            sender_id, permission_level,
        )
        subject = self.plugin.memory_bridge.participant_subject(sender_id)
        while True:
            if digest_batches_left <= 0:
                self.plugin.logger.info(
                    f"[{reason}] 私聊 {sender_id} 本轮结算达批次上限，"
                    f"剩余待下一轮"
                )
                return False
            digest_batches_left -= 1
            scoped_messages, next_index = self._slice_group_history_batch(
                conversation_history, last_participant_digest_index,
                self.GROUP_HISTORY_MAX_MESSAGES,
                user_data=user_data,
                stop_at_provisional=stop_at_provisional,
            )
            if not scoped_messages:
                if (
                    stop_at_provisional
                    and next_index < len(conversation_history)
                    and any(
                        row is conversation_history[next_index]
                        for row in (
                            user_data.get("provisional_draft_rows") or []
                        )
                    )
                ):
                    # Retained opt-out sessions must keep their marker/cutoff
                    # until this in-flight reply's delivery is decided. A
                    # successful empty result would consume the cutoff and
                    # leave no later settlement owner for the delivered row.
                    return False
                if next_index > last_participant_digest_index:
                    # 尾部全是被过滤的行：推进游标即可，无须发送。
                    user_data["last_participant_digest_index"] = next_index
                break
            participant_extra: dict[str, Any] = {}
            if display_name:
                # 与群 digest 同约定：拿不到显示名就不带参。
                participant_extra["display_name"] = display_name
            if speaker_is_owner:
                participant_extra["speaker_is_owner"] = True
            result = await self.plugin.memory_bridge.post_scoped_memory_history(
                her_name,
                scoped_messages,
                subject=subject,
                speaker_label=speaker_label,
                speaker_trust=speaker_trust,
                speaker_id=f"qq:{sender_id}",
                timeout=30.0,
                **participant_extra,
            )
            if result.get("status") == "error":
                raise RuntimeError(
                    result.get("message", "scoped participant history failed")
                )
            await self._apply_speaker_trust_update(
                sender_id,
                scoped_messages,
                result.get("trust_events") or [],
                activity_identity=(
                    f"participant:{her_name}:"
                    f"{user_data.setdefault('_speaker_trust_activity_epoch', time.time_ns())}:"
                    f"{last_participant_digest_index}:{next_index}"
                ),
            )
            self.plugin.logger.info(
                f"[{reason}] 已为私聊 {sender_id} 完成 scoped 记忆结算，"
                f"消息数: {len(scoped_messages)}"
            )
            user_data["last_participant_digest_index"] = next_index
            last_participant_digest_index = next_index
        return True

    async def _drain_member_buckets(self, session_key: str) -> None:
        """Flush member buckets that hit the cap, instead of dropping the
        oldest authorized turns of a group that never goes idle.

        The session lock is held only to take the snapshot and to hand the
        failures back — never across the scoped POSTs. One sweep is at
        worst two waves of four concurrent batch requests at up to 30s
        each (typically a single packed batch); holding the lock for that
        stalls every message in the group, and the handlers queued behind
        it keep their share of the global message semaphore, so a couple
        of always-busy groups could wedge the whole plugin (private chats
        included)."""
        snapshot: dict[str, list] = {}
        snapshot_labels: dict[str, str] = {}
        flush_target: dict[str, Any] = {}
        # 会话在飞行期间没了、但授权还在时，把最后一次重试排到锁外——
        # 会话已经不存在，在锁内发 30s 的请求只会挡住同 key 的新会话。
        orphan_retry: dict[str, Any] = {}

        async def _take_snapshot() -> bool:
            user_data = self.plugin._user_sessions.get(session_key)
            if not user_data:
                return False
            if not user_data.get("is_group") or not user_data.get(
                "memory_enabled"
            ):
                return False
            if not (getattr(self.plugin, "_qq_settings", {}) or {}).get(
                "group_member_memory_enabled", False,
            ):
                return False
            group_id = str(user_data.get("group_id") or "").strip()
            her_name = user_data.get("her_name")
            if not group_id or not her_name:
                return False
            buckets = user_data.get("group_member_memory_messages") or {}
            labels = user_data.get("group_member_memory_labels") or {}
            ready = [s for s, msgs in buckets.items() if s and msgs]
            # 一趟最多带走一个名额的量。归还失败快照时参与者数可以暂时到
            # 名额的两倍（见 _return_snapshot：宁可超额也不丢掉整个已授权
            # 发言人），不设这个上限的话，那种局面下的排空会变成四波，而
            # 结算侧的等待上限是按两波算的——等待到点时后面几波还攥着快照。
            # 限住工作量比拉长等待好：多出来的下一轮接着排。
            deferred = ready[self.GROUP_MEMBER_MAX_PARTICIPANTS:]
            for sender_id in ready[:self.GROUP_MEMBER_MAX_PARTICIPANTS]:
                snapshot[sender_id] = buckets.pop(sender_id)
                label = labels.pop(sender_id, None)
                if label:
                    snapshot_labels[sender_id] = label
            if not snapshot:
                return False
            if deferred:
                # 留下的那些要再排一轮，否则得等这些成员各自再攒满一次。
                user_data["member_flush_due"] = True
                self.plugin.logger.info(
                    f"[member_bucket_cap] 群 {group_id} 参与者超额，本轮先带走 "
                    f"{len(snapshot)} 个，剩 {len(deferred)} 个下轮再排"
                )
            # 整桶搬走：参与者名额当场腾空，冲刷期间新到的发言写进全新的
            # 一代，和在飞的这一代互不覆盖。冲刷标记必须在锁内就位——设置侧
            # 的 opt-out 快照靠它决定"另起一代"，锁一放它就可能跑起来。
            # 这一层计数覆盖整趟排空（含把失败桶还回去），所以中途并发的
            # finalize 冲刷结束时不会替我们把标记清掉。
            self._enter_member_flush(user_data)
            flush_target["user_data"] = user_data
            flush_target["group_id"] = group_id
            flush_target["her_name"] = her_name
            return True

        async def _return_snapshot() -> None:
            # _flush_member_buckets 成功即 pop，所以此刻 snapshot 里剩下的
            # 正好是没冲出去的那些。
            held = flush_target.get("user_data")
            user_data = self.plugin._user_sessions.get(session_key)
            if user_data is not held:
                # 身份比对而不是判空：会话在飞行期间被结算弹出后，排队中的
                # 群消息可能已经用同一个 key 建了**新**会话。这份快照属于旧
                # 那一份 user_data——挂到顶替者身上，随后的冲刷会拿新会话的
                # her_name 去写，等于把这些发言存进另一个角色的记忆库。
                # 这份旧 dict 已经没有消费者了，提升与否都不改变去向；但计数
                # 还是要放掉（重新绑定同一份 dict 的路径否则会永远看到"冲刷
                # 中"），并且把真正丢掉的量记下来——不持锁换来的代价必须看得
                # 见，不能悄悄消失。
                stranded = 0
                if isinstance(held, dict):
                    stranded = len(held.get("group_member_memory_messages") or {})
                    # 两个在飞标记同源，就得同时放掉。只放计数、留着
                    # member_drain_in_flight 的话，一旦这份 dict 被重新绑回
                    # _user_sessions，cache_session_delta 的调度判据就永久为
                    # 假——成员桶排空再也不会被排上，只能等硬顶丢弃收场。
                    held.pop("member_drain_in_flight", None)
                    self._finish_member_flush_generation(held)
                # 日志分级的判据统一成一句话：**error 是"本想留下却没留住"，
                # warning 是"按策略本来就该丢"**。opt-out 撤掉之后的丢弃属于
                # 后者（fail-closed 是设计），把它记成 error 会让真正的意外
                # 丢失淹没在噪音里。
                consent_on = self._member_memory_consent_live()
                lost = (
                    self.plugin.logger.error if consent_on
                    else self.plugin.logger.warning
                )
                if snapshot and consent_on:
                    # 开关都还开着 = 会话不是被 opt-out 撤掉的，这些桶仍是
                    # 已授权且唯一的副本。排在锁外再试一次：改前会话锁挡着
                    # 结算，紧随其后的 finalize 总会替它们重试一次，本 PR
                    # 拆锁之后没人接手了。opt-out 撤的场景照旧丢弃（fail
                    # closed），不在这里复活。
                    orphan_retry["snapshot"] = dict(snapshot)
                    orphan_retry["labels"] = dict(snapshot_labels)
                replaced = "并已被新会话顶替" if user_data is not None else ""
                if stranded:
                    # 滞留在孤儿映射上的那一代没有任何补救余地。
                    lost(
                        f"[member_bucket_cap] 群 {flush_target.get('group_id')} "
                        f"冲刷期间会话已结算并弹出{replaced}：{stranded} 个滞留"
                        f"队列丢失"
                    )
                if not snapshot:
                    return
                if orphan_retry.get("snapshot"):
                    # 还有救就别报「丢失」：末次重试成功时什么都没丢，留一条
                    # 判丢的日志只会把排查的人带偏。真丢了由重试之后那条记。
                    self.plugin.logger.warning(
                        f"[member_bucket_cap] 群 {flush_target.get('group_id')} "
                        f"冲刷期间会话已结算并弹出{replaced}：{len(snapshot)} 个"
                        f"未冲成功的成员队列转末次重试"
                    )
                else:
                    lost(
                        f"[member_bucket_cap] 群 {flush_target.get('group_id')} "
                        f"冲刷期间会话已结算并弹出{replaced}：{len(snapshot)} 个"
                        f"未冲成功的成员队列丢失"
                    )
                return
            try:
                if not snapshot:
                    return
                if not user_data.get("memory_enabled") or not (
                    self._member_memory_consent_live()
                ):
                    # 冲刷飞行期间 opt-out：按 fail-closed 丢弃。放回队列
                    # 等下一轮重发，等于让撤销授权之前收集的发言在 opt-out
                    # 之后继续往服务端跑。
                    self.plugin.logger.warning(
                        f"[member_bucket_cap] 群 {flush_target.get('group_id')} "
                        f"冲刷期间成员记忆被关闭，丢弃 {len(snapshot)} 个未冲"
                        f"成功的成员队列"
                    )
                    return
                buckets = user_data.setdefault("group_member_memory_messages", {})
                labels = user_data.setdefault("group_member_memory_labels", {})
                for sender_id, messages in snapshot.items():
                    # 失败的旧发言排在冲刷期间新到的之前，保持时间顺序。
                    merged = messages + list(buckets.get(sender_id) or [])
                    if len(merged) > self.GROUP_MEMBER_HARD_LIMIT:
                        # 硬顶要重新压：快照与新一代各自有界，拼起来没有。
                        # 服务端持续挂掉时，每轮失败都把上一轮整批加回来，
                        # 队列会无界增长——硬顶正是为这个场景存在的。
                        self.plugin.logger.warning(
                            f"[member_bucket_cap] 成员 {sender_id} 的队列归还后"
                            f"超过硬顶（{len(merged)}），丢弃最早的 "
                            f"{len(merged) - self.GROUP_MEMBER_HARD_LIMIT} 条"
                        )
                        del merged[:-self.GROUP_MEMBER_HARD_LIMIT]
                    buckets[sender_id] = merged
                    if sender_id in snapshot_labels:
                        # 冲刷期间又发言的人可能带来更新的展示名，不覆盖。
                        labels.setdefault(sender_id, snapshot_labels[sender_id])
                if len(buckets) > self.GROUP_MEMBER_MAX_PARTICIPANTS:
                    # 名额只在 record_group_member_turn 的入口把关，归还会
                    # 暂时越线（上限 2 倍：活映射自己进不了新人）。不在这里
                    # 丢人——丢掉的是已授权的整个发言人，比暂时超额更糟——
                    # 但要留痕，并且下一轮排空会把它压回去。
                    self.plugin.logger.warning(
                        f"[member_bucket_cap] 群 {flush_target.get('group_id')} "
                        f"归还后参与者数 {len(buckets)} 超过名额 "
                        f"{self.GROUP_MEMBER_MAX_PARTICIPANTS}，待下轮排空压回"
                    )
                # due 标已被调度器消费掉：不重新置起来的话，要等这些成员各自
                # 再攒满一轮才会重试（硬顶兜底防无界增长）。
                user_data["member_flush_due"] = True
                self.plugin.logger.warning(
                    f"[member_bucket_cap] 群 {flush_target.get('group_id')} 有 "
                    f"{len(snapshot)} 个成员队列冲刷失败，留待下轮"
                )
            finally:
                user_data.pop("member_drain_in_flight", None)
                # 排空这一层的计数在锁内放掉：到这里失败桶已经归位，此刻
                # 若是最后一个在飞的冲刷，快照提升拿到的才是完整的一代。
                self._finish_member_flush_generation(user_data)

        if not await self.plugin._run_with_session_lock(
            session_key, _take_snapshot,
        ):
            stale = self.plugin._user_sessions.get(session_key)
            if stale is not None:
                stale.pop("member_drain_in_flight", None)
            return
        try:
            await self._flush_member_buckets(
                flush_target["user_data"],
                group_id=flush_target["group_id"],
                her_name=flush_target["her_name"],
                reason="member_bucket_cap",
                buckets=snapshot,
                labels=snapshot_labels,
                # 一趟最多两波：第一波在飞时落下的 opt-out，第二波必须看见。
                # 剩下的桶会以"失败"回到 snapshot，再由 _return_snapshot 按
                # fail-closed 丢弃。
                # 注意只有这条"在实时授权下收集"的路径该开它——finalize 与
                # settle_member_buckets_on_disable 冲的是 opt-out 之前收集、
                # 等着结算的那批，开了等于把它们连同结算一起废掉。
                require_consent=True,
            )
        finally:
            await self.plugin._run_with_session_lock(
                session_key, _return_snapshot,
            )
            if orphan_retry.get("snapshot") and not self._member_memory_consent_live():
                # 授权是在锁内采样的，这一步却在锁外——中间落下的 opt-out
                # 必须在**发请求之前**再看一眼，否则这次重试会把撤销之后
                # 本该 fail-closed 丢弃的发言推上去。判据放在本调用点而不是
                # _flush_member_buckets 里：那个函数被 opt-out 结算复用，
                # 而后者恰恰是在开关已经 False 之后调用的。
                self.plugin.logger.warning(
                    f"[member_bucket_orphan_retry] 群 "
                    f"{flush_target.get('group_id')} 末次重试前授权已撤销，"
                    f"按 fail-closed 丢弃 {len(orphan_retry['snapshot'])} 个队列"
                )
                orphan_retry.clear()
            if orphan_retry.get("snapshot"):
                try:
                    await self._flush_member_buckets(
                        flush_target["user_data"],
                        group_id=flush_target["group_id"],
                        her_name=flush_target["her_name"],
                        reason="member_bucket_orphan_retry",
                        buckets=orphan_retry["snapshot"],
                        labels=orphan_retry["labels"],
                        require_consent=True,
                        # 同 opt-out 结算：这是末次重试，失败就"就此丢失"
                        # （下面那条 error 日志说的正是这个）。而且这些桶
                        # 已经作为一批失败过一次——再打一次同样的包，一次
                        # 传输抖动就能连着抹掉同样的 8 个人。
                        isolate_segments=True,
                    )
                except Exception as exc:
                    self.plugin.logger.error(
                        f"[member_bucket_orphan_retry] 群 "
                        f"{flush_target.get('group_id')} 末次重试失败: {exc}"
                    )
                left = len(orphan_retry["snapshot"])
                if left:
                    self.plugin.logger.error(
                        f"[member_bucket_orphan_retry] 群 "
                        f"{flush_target.get('group_id')} 末次重试后仍有 "
                        f"{left} 个成员队列未能入库，就此丢失"
                    )

    async def _flush_member_buckets(
        self, user_data: dict[str, Any], *, group_id: str, her_name: str,
        reason: str, buckets: dict | None = None, labels: dict | None = None,
        require_consent: bool = False, isolate_segments: bool = False,
    ) -> list[str]:
        """Flush member buckets in packed batches (semaphore 4).

        每批一次 /scoped_history segments 请求 = 一次 LLM 抽取，服务端按
        段分派回各自的 subject，响应逐段报 ok/failed。成功段当场 pop
        bucket+label；失败段（或整批异常）留在映射里等下一轮 sweep——
        与逐成员时代同一份"成功即弹、失败保留"契约，只是粒度从每人一次
        请求变成每批一次。抽取调用数从 O(发言人数) 降到 O(总消息数/批容
        量)；最坏（每桶都接近硬顶）退化回一桶一批，与旧形态同形。

        ``isolate_segments``: 一桶一请求，放弃打包省下的那几次 LLM 调用。
        只有 **失败即永久丢弃** 的调用方该开它——那里"整批一起失败"意味着
        一次传输抖动同时抹掉 ≤8 个人（打包前是 1 个）。有重试的路径不用开：
        整批失败只是让这 8 个人晚一轮，代价与 1 个人晚一轮同量级，而打包
        省下的调用是每次 sweep 都在省的。

        Serial 8x30s used to hold the session lock ~4 min, exhausting the
        global message semaphore and never fitting the host shutdown kill
        window."""
        member_buckets = (
            buckets if buckets is not None
            else user_data.get("group_member_memory_messages") or {}
        )
        member_labels = (
            labels if labels is not None
            else user_data.get("group_member_memory_labels") or {}
        )
        member_flush_sem = asyncio.Semaphore(self.MEMBER_FLUSH_CONCURRENCY)

        def _chronological_segment_groups() -> list[list[dict[str, Any]]]:
            ordered_messages: list[tuple[tuple[int, int, int, int], str, Any]] = []
            for bucket_index, (sender_id, messages) in enumerate(
                list(member_buckets.items())
            ):
                for message_index, message in enumerate(list(messages or [])):
                    raw_sequence = (
                        message.get("_speaker_sequence")
                        if isinstance(message, dict) else None
                    )
                    if isinstance(raw_sequence, int) and not isinstance(
                        raw_sequence, bool
                    ):
                        key = (0, raw_sequence, bucket_index, message_index)
                    else:
                        # Synthetic/legacy rows without the internal stamp keep
                        # the historical deterministic bucket order.
                        key = (1, bucket_index, message_index, 0)
                    ordered_messages.append((key, sender_id, message))
            ordered_messages.sort(key=lambda item: item[0])

            segments: list[dict[str, Any]] = []
            for _, sender_id, message in ordered_messages:
                fallback = self._speaker_permission_level_for(sender_id)
                raw_level = (
                    message.get("_speaker_permission_level")
                    if isinstance(message, dict) else None
                )
                level = self._speaker_permission_level_for(
                    sender_id, raw_level or fallback,
                )
                if (
                    not segments
                    or segments[-1]["sender_id"] != sender_id
                    or segments[-1]["permission_level"] != level
                ):
                    segments.append({
                        "sender_id": sender_id,
                        "permission_level": level,
                        "messages": [],
                    })
                segments[-1]["messages"].append(message)
            # Each chronological run is its own packable group. The packer
            # may coalesce adjacent runs into one request but cannot reorder
            # them across speakers.
            return [[segment] for segment in segments]

        speaker_segment_groups = _chronological_segment_groups()

        async def _flush_one_batch(batch_specs: list[dict]) -> list[str]:
            async with member_flush_sem:
                batch_senders = list(dict.fromkeys(
                    str(spec.get("sender_id") or "") for spec in batch_specs
                    if spec.get("sender_id")
                ))
                if require_consent and not self._member_memory_consent_live():
                    # 逐批复检（与旧的逐请求复检同语义），因为信号量排队与
                    # gather 的任务调度都是挂起点：调用点检查过之后、真正发
                    # 出之前，设置侧完全可能把开关翻掉。默认关着——opt-out
                    # 结算复用本函数，而它恰恰是在开关已 False 之后调用的，
                    # 那条路径必须放行。
                    self.plugin.logger.warning(
                        f"[{reason}] 群 {group_id} 一批 {len(batch_senders)} "
                        f"个成员发出前授权已撤销，按 fail-closed 丢弃"
                    )
                    return list(batch_senders)
                def _member_segment(spec: dict) -> dict[str, Any]:
                    sender_id = spec["sender_id"]
                    permission_level = spec["permission_level"]
                    messages = []
                    for message in spec["messages"]:
                        if (
                            isinstance(message, dict)
                            and "_trust_signal_excluded_fact_ids" in message
                        ):
                            message = dict(message)
                            message.pop("_trust_signal_excluded_fact_ids", None)
                        messages.append(message)
                    segment: dict[str, Any] = {
                        "messages": messages,
                        "subject": (
                            self.plugin.memory_bridge.group_participant_subject(
                                group_id, sender_id,
                            )
                        ),
                        "speaker_label": (
                            str(member_labels.get(sender_id) or sender_id)[:64]
                        ),
                        # 全局信赖度快照：权限基线 + 有界演化，随段落盘到
                        # 该段抽出的每条 fact 上。
                        "speaker_trust": self._speaker_trust_for(
                            sender_id, permission_level,
                        ),
                        "speaker_id": f"qq:{sender_id}",
                    }
                    if self._speaker_is_owner_for(
                        sender_id, permission_level,
                    ):
                        segment["speaker_is_owner"] = True
                    # 显示名 = label 剥掉 "(sender_id)" 后缀的昵称本体
                    # （persona 标题里 subject_id 已含数字 id，不重复）。
                    # label 退化成纯 id 时不加键，标题回退裸 id 形态。
                    display_name = QQDisplayNameService.display_name_from_label(
                        member_labels.get(sender_id), sender_id,
                    )
                    if display_name:
                        segment["display_name"] = display_name
                    return segment

                segments = [
                    _member_segment(spec) for spec in batch_specs
                ]
                try:
                    # 外层再包一次墙钟上限：httpx 的 timeout= 是给 connect /
                    # read / write / pool **各自**一份，不是整次请求的总时长
                    # ——连接池被别的群排空占满时，光等池就能花掉一份，再花
                    # 一份读响应。等待上限是按"波数 × 单发超时"推的，单发不
                    # 真的封顶，那个推导就不成立。批的输入工作量上界与旧单
                    # 发同口径（≤200 条消息 = 一次抽取），30s 预算不变。
                    result = await asyncio.wait_for(
                        self.plugin.memory_bridge.post_scoped_memory_history_batch(
                            her_name,
                            segments,
                            timeout=self.SCOPED_HISTORY_TIMEOUT_SECONDS,
                        ),
                        timeout=self.SCOPED_HISTORY_TIMEOUT_SECONDS,
                    )
                    if result.get("status") == "error":
                        raise RuntimeError(
                            result.get(
                                "message",
                                "scoped participant history failed",
                            )
                        )
                    segment_results = result.get("segments")
                    if (
                        not isinstance(segment_results, list)
                        or len(segment_results) != len(batch_specs)
                    ):
                        # 响应形状与请求对不上时绝不按位置乱猜——整批按
                        # 失败保留重试（fail-closed）。
                        raise RuntimeError(
                            "malformed batch response: segment count mismatch"
                        )
                except Exception as exc:
                    self.plugin.logger.error(
                        f"[{reason}] 群 {group_id} 一批 {len(batch_senders)} "
                        f"个成员记忆结算失败: {exc}"
                    )
                    return list(batch_senders)
                failed: set[str] = set()
                successful_message_ids: dict[str, set[int]] = {}
                chronological_predecessor_failed = False
                for segment_index, (spec, segment_result) in enumerate(zip(
                    batch_specs, segment_results,
                )):
                    sender_id = spec["sender_id"]
                    if (
                        isinstance(segment_result, dict)
                        and segment_result.get("status") == "ok"
                    ):
                        if (
                            chronological_predecessor_failed
                            and self._speaker_is_owner_for(
                                sender_id, spec.get("permission_level"),
                            )
                        ):
                            # The server persisted this segment, but could not
                            # derive its owner signals from a predecessor that
                            # failed in the same request.  Retain the messages
                            # and retry after the missing fact has landed;
                            # exact dedup keeps the fact write idempotent.
                            self.plugin.logger.warning(
                                f"[{reason}] 群 {group_id} 主人 {sender_id} "
                                "的前序段失败，保留本段重试信任信号"
                            )
                            later_fact_ids = {
                                str(fact_id)
                                for later_result in segment_results[
                                    segment_index + 1:
                                ]
                                if isinstance(later_result, dict)
                                and later_result.get("status") == "ok"
                                for fact_id in (
                                    later_result.get("fact_ids") or []
                                )
                                if fact_id
                            }
                            if later_fact_ids:
                                for message in spec["messages"]:
                                    if not isinstance(message, dict):
                                        continue
                                    existing = message.get(
                                        "_trust_signal_excluded_fact_ids"
                                    )
                                    excluded = {
                                        str(fact_id) for fact_id in (
                                            existing
                                            if isinstance(existing, list)
                                            else []
                                        ) if fact_id
                                    }
                                    excluded.update(later_fact_ids)
                                    message[
                                        "_trust_signal_excluded_fact_ids"
                                    ] = sorted(excluded)
                            failed.add(sender_id)
                            continue
                        try:
                            dropped = int(segment_result.get("dropped") or 0)
                        except (TypeError, ValueError):
                            dropped = 0
                        if dropped:
                            # 服务端丢的是**不承载内容**的垃圾条目（归属由
                            # 段对象给定），所以照样 pop；记一行是为了让
                            # "模型输出在变脏"在插件日志里留痕。
                            self.plugin.logger.info(
                                f"[{reason}] 群 {group_id} 成员 {sender_id} "
                                f"本次抽取丢弃了 {dropped} 条无内容条目"
                            )
                        trust_events = list(
                            segment_result.get("trust_events") or []
                        )
                        excluded_fact_ids = {
                            str(fact_id)
                            for message in spec["messages"]
                            if isinstance(message, dict)
                            for fact_id in (
                                message.get(
                                    "_trust_signal_excluded_fact_ids"
                                ) or []
                            )
                            if fact_id
                        }
                        if excluded_fact_ids:
                            unfiltered_count = len(trust_events)
                            trust_events = [
                                event for event in trust_events
                                if isinstance(event, dict)
                                and str(event.get("source_fact_id") or "")
                                not in excluded_fact_ids
                            ]
                            suppressed = unfiltered_count - len(trust_events)
                            if suppressed:
                                self.plugin.logger.warning(
                                    f"[{reason}] 群 {group_id} 主人 {sender_id} "
                                    f"重试信任信号忽略 {suppressed} 条后序事实"
                                )
                        await self._apply_speaker_trust_update(
                            sender_id,
                            list(spec["messages"]),
                            trust_events,
                            activity_identity=self._group_activity_identity(
                                group_id, spec,
                            ),
                        )
                        successful_message_ids.setdefault(sender_id, set()).update(
                            id(message) for message in spec["messages"]
                        )
                        continue
                    self.plugin.logger.error(
                        f"[{reason}] 群 {group_id} 成员 {sender_id} "
                        f"记忆结算失败（批内单段失败）"
                    )
                    failed.add(sender_id)
                    chronological_predecessor_failed = True
                for sender_id in batch_senders:
                    succeeded = successful_message_ids.get(sender_id, set())
                    if succeeded and sender_id in member_buckets:
                        member_buckets[sender_id] = [
                            message
                            for message in member_buckets[sender_id]
                            if id(message) not in succeeded
                        ]
                    if member_buckets.get(sender_id):
                        continue
                    member_buckets.pop(sender_id, None)
                    # label 与 bucket 同生命周期：只弹 bucket 的话，活跃
                    # 群会让 label 映射无限增长，而参与者名额是按 bucket
                    # 数算的，关闭成员记忆时（bucket 已空）也没人清这些
                    # 残留。批内失败段的 label 与 bucket 一起留下。
                    if isinstance(member_labels, dict):
                        member_labels.pop(sender_id, None)
                return [sid for sid in batch_senders if sid in failed]

        # 冲刷进行中标记：设置侧的快照合并看它决定"追加进这一代"还是
        # "另起一代"。往正在飞的那一代里追加会被它成功后的整桶 pop 带走。
        self._enter_member_flush(user_data)
        batches = self._pack_member_segment_groups(
            speaker_segment_groups, isolate_segments=isolate_segments,
        )
        if not batches:
            self._finish_member_flush_generation(user_data)
            return []
        try:
            chains: list[list[list[dict]]] = []
            sender_chains: dict[str, list[list[dict]]] = {}
            has_owner_segment = any(
                self._speaker_is_owner_for(
                    str(spec.get("sender_id") or ""),
                    spec.get("permission_level"),
                )
                for batch in batches for spec in batch
            )
            if has_owner_segment:
                # Owner confirmation/correction signals consume facts created
                # by earlier segments. Preserve the global authored order
                # across request boundaries as well as inside each request.
                chains.append(batches)
            else:
                for batch in batches:
                    senders = {
                        str(spec.get("sender_id") or "") for spec in batch
                        if spec.get("sender_id")
                    }
                    if len(senders) == 1:
                        sender_id = next(iter(senders))
                        chain = sender_chains.get(sender_id)
                        if chain is None:
                            chain = []
                            sender_chains[sender_id] = chain
                            chains.append(chain)
                        chain.append(batch)
                    else:
                        chains.append([batch])

            async def _flush_chain(chain: list[list[dict]]) -> list[str]:
                failed: list[str] = []
                for batch_index, batch in enumerate(chain):
                    batch_failed = await _flush_one_batch(batch)
                    failed.extend(batch_failed)
                    if batch_failed:
                        # Later chronological work must not overtake a failed
                        # predecessor. Report its still-buffered senders too,
                        # so callers do not mistake "not attempted" for ok.
                        failed.extend(
                            str(spec.get("sender_id") or "")
                            for later in chain[batch_index + 1:]
                            for spec in later if spec.get("sender_id")
                        )
                        break
                return list(dict.fromkeys(failed))

            failed_lists = await asyncio.gather(
                *(_flush_chain(chain) for chain in chains)
            )
            return [sid for failed in failed_lists for sid in failed]
        finally:
            self._finish_member_flush_generation(user_data)

    @staticmethod
    def _pack_member_batches(
        member_buckets: dict, *, isolate_segments: bool = False,
    ) -> list[list[str]]:
        """Greedy-pack sender buckets into batches of sender ids.

        ``isolate_segments`` 把每批压到一段（= 打包前的形态），给"失败即
        永久丢弃"的调用方用。

        每批：段数 ≤ SCOPED_HISTORY_BATCH_MAX_SEGMENTS、消息总量 ≤
        SCOPED_HISTORY_BATCH_MAX_MESSAGES（服务端同口径校验）。单桶硬顶
        GROUP_MEMBER_HARD_LIMIT(150) < 批容量(200)，一个桶永远不用跨批
        拆分；防御性地，真出现超限单桶时让它独占一批（服务端 422 → 该批
        按失败保留，与旧单发 422 行为一致，不在这里静默截断）。一趟
        sweep 的桶数 ≤ GROUP_MEMBER_MAX_PARTICIPANTS，批数 ≤ 桶数，所以
        SETTLE_JOIN_TIMEOUT_LONG_SECONDS 的"波数 × 单发超时"推导在最坏
        情形下与逐成员时代一致。"""
        from config import (
            SCOPED_HISTORY_BATCH_MAX_MESSAGES,
            SCOPED_HISTORY_BATCH_MAX_SEGMENTS,
        )

        max_segments = (
            1 if isolate_segments else SCOPED_HISTORY_BATCH_MAX_SEGMENTS
        )
        batches: list[list[str]] = []
        current: list[str] = []
        current_messages = 0
        for sender_id, messages in list(member_buckets.items()):
            if not sender_id or not messages:
                continue
            count = len(messages)
            if current and (
                current_messages + count > SCOPED_HISTORY_BATCH_MAX_MESSAGES
                or len(current) >= max_segments
            ):
                batches.append(current)
                current = []
                current_messages = 0
            current.append(sender_id)
            current_messages += count
        if current:
            batches.append(current)
        return batches

    @staticmethod
    def _pack_member_segment_groups(
        groups: list[list[dict]], *, isolate_segments: bool = False,
    ) -> list[list[dict]]:
        """Pack ordered permission runs within the server's batch limits."""
        from config import (
            SCOPED_HISTORY_BATCH_MAX_MESSAGES,
            SCOPED_HISTORY_BATCH_MAX_SEGMENTS,
        )

        max_segments = 1 if isolate_segments else SCOPED_HISTORY_BATCH_MAX_SEGMENTS
        split_groups: list[tuple[list[dict], bool]] = []
        for group in groups:
            chunks: list[list[dict]] = []
            chunk: list[dict] = []
            chunk_messages = 0
            for raw_spec in group:
                spec_messages = list(raw_spec.get("messages") or [])
                while spec_messages:
                    room = SCOPED_HISTORY_BATCH_MAX_MESSAGES - chunk_messages
                    if chunk and (len(chunk) >= max_segments or room <= 0):
                        chunks.append(chunk)
                        chunk = []
                        chunk_messages = 0
                        room = SCOPED_HISTORY_BATCH_MAX_MESSAGES
                    take = min(len(spec_messages), room)
                    spec = dict(raw_spec)
                    spec["messages"] = spec_messages[:take]
                    chunk.append(spec)
                    chunk_messages += take
                    spec_messages = spec_messages[take:]
                    if len(chunk) >= max_segments or (
                        chunk_messages >= SCOPED_HISTORY_BATCH_MAX_MESSAGES
                    ):
                        chunks.append(chunk)
                        chunk = []
                        chunk_messages = 0
            if chunk:
                chunks.append(chunk)
            oversized = len(chunks) > 1
            split_groups.extend((part, oversized) for part in chunks)

        batches: list[list[dict]] = []
        current: list[dict] = []
        current_messages = 0
        for group, split_from_sender in split_groups:
            if not group:
                continue
            group_messages = sum(len(spec.get("messages") or []) for spec in group)
            if isolate_segments or split_from_sender:
                if current:
                    batches.append(current)
                    current = []
                    current_messages = 0
                batches.append(group)
                continue
            if current and (
                current_messages + group_messages > SCOPED_HISTORY_BATCH_MAX_MESSAGES
                or len(current) + len(group) > SCOPED_HISTORY_BATCH_MAX_SEGMENTS
            ):
                batches.append(current)
                current = []
                current_messages = 0
            current.extend(group)
            current_messages += group_messages
        if current:
            batches.append(current)
        return batches

    def _group_display_name(self, group_id: object) -> str | None:
        """The group's human-readable name for scoped writes, or None.

        防御性 getattr：合成测试的 plugin stub 未必装配 display_name
        service；名字是装饰性元数据，拿不到就退化成不带（persona 保留
        上次盖上的名字，自愈）。"""
        service = getattr(self.plugin, "display_name_service", None)
        if service is None:
            return None
        try:
            return service.group_display_name(group_id)
        except Exception:
            return None

    def _speaker_trust_for(
        self, sender_id: str, permission_level: str | None = None,
    ) -> float:
        """Resolve current global trust using a frozen permission baseline."""
        permission_mgr = getattr(self.plugin, "permission_mgr", None)
        try:
            get_trust = getattr(permission_mgr, "get_speaker_trust", None)
            if get_trust is not None:
                return get_trust(sender_id, permission_level)
            get_level = getattr(permission_mgr, "get_permission_level", None)
            if get_level is not None:
                permission_level = permission_level or get_level(sender_id)
        except Exception:
            # Permission lookup is best-effort here; use the frozen/default
            # config baseline below when lightweight integrations omit it.
            pass
        from config import SPEAKER_TRUST_BY_PERMISSION_LEVEL, SPEAKER_TRUST_DEFAULT
        return SPEAKER_TRUST_BY_PERMISSION_LEVEL.get(
            permission_level or "none", SPEAKER_TRUST_DEFAULT,
        )

    def _speaker_permission_level_for(
        self, sender_id: str, permission_level: str | None = None,
    ) -> str:
        """Freeze one canonical permission tier at message-authoring time."""
        level = str(permission_level or "").strip().lower()
        if level == "user":
            level = "normal"
        if level in {"admin", "trusted", "normal", "none"}:
            return level
        manager = getattr(self.plugin, "permission_mgr", None)
        try:
            get_level = getattr(manager, "get_permission_level", None)
            if get_level is not None:
                resolved = str(get_level(sender_id) or "none").strip().lower()
                return resolved if resolved in {
                    "admin", "trusted", "normal", "none",
                } else "none"
        except Exception:
            # Lightweight integrations can omit permission state entirely.
            pass
        return "none"

    def _speaker_is_owner_for(
        self, sender_id: str, permission_level: str | None = None,
    ) -> bool:
        """Use the request-side permission tier, never trust score or content."""
        level = permission_level
        if level is None:
            manager = getattr(self.plugin, "permission_mgr", None)
            try:
                get_level = getattr(manager, "get_permission_level", None)
                if get_level is not None:
                    level = get_level(sender_id)
            except Exception:
                level = None
        return level == "admin"

    async def _apply_speaker_trust_update(
        self, sender_id: str, messages: list[dict], trust_events: list[dict],
        *, activity_identity: str | None = None,
    ) -> None:
        """Persist one idempotent raw-activity event plus server-verified signals."""
        canonical = json.dumps(messages or [], ensure_ascii=False, sort_keys=True)
        event_id = "activity_" + hashlib.sha256(
            f"qq:{sender_id}|{activity_identity or canonical}|{canonical}".encode(
                "utf-8"
            )
        ).hexdigest()[:24]
        from memory.speaker_trust import observation_texts
        authored_count = len(observation_texts(messages or []))
        settings_service = getattr(self.plugin, "settings_service", None)
        apply_update = getattr(
            settings_service, "apply_speaker_trust_update", None,
        )
        # Lightweight integrations and old test harnesses may intentionally
        # omit persistence services.  Fact writes must remain usable there.
        if apply_update is None:
            return
        ok = await apply_update(
            sender_id=sender_id,
            message_count=authored_count,
            activity_event_id=event_id,
            trust_events=trust_events,
        )
        if not ok:
            # 记忆已安全落盘；trust 是派生权重，失败不回滚事实，只明确留痕。
            self.plugin.logger.warning(
                f"speaker trust 演化未持久化 sender={sender_id} event={event_id}"
            )

    @staticmethod
    def _group_activity_identity(group_id: str, spec: dict) -> str:
        """Stable across retries, distinct for equal text in separate batches."""
        message_ids = [
            str(message.get("_speaker_activity_id") or "")
            for message in spec.get("messages") or []
            if isinstance(message, dict) and message.get("_speaker_activity_id")
        ]
        if message_ids:
            batch_identity = "|".join(message_ids)
        else:
            batch_identity = json.dumps(
                spec.get("messages") or [], ensure_ascii=False, sort_keys=True,
            )
        return (
            f"group:{group_id}:{spec.get('permission_level') or 'none'}:"
            f"{batch_identity}"
        )

    def _member_memory_consent_live(self) -> bool:
        """Whether member memory is still authorized right now.

        Both switches count: the member option is a child of the group one,
        so the parent going off revokes it too."""
        settings = getattr(self.plugin, "_qq_settings", {}) or {}
        return bool(
            settings.get("group_member_memory_enabled", False)
            and settings.get("group_memory_enabled", False)
        )

    @staticmethod
    def _enter_member_flush(user_data: dict[str, Any]) -> None:
        """Register one more flush as in flight.

        A depth counter, not a boolean: the cap drain runs its POSTs with
        the session lock released, so a finalize/idle/shutdown settlement
        can start its own flush on the live mapping meanwhile. With a
        boolean, whichever finished first cleared the mark while the other
        still owned an in-flight mapping — and an opt-out landing in that
        window would copy that mapping into the settlement snapshot and
        submit the same messages twice."""
        user_data["member_flush_in_progress"] = int(
            user_data.get("member_flush_in_progress") or 0
        ) + 1

    @staticmethod
    def _exit_member_flush(user_data: dict[str, Any]) -> bool:
        """Drop one flush; True when it was the last one in flight."""
        depth = int(user_data.get("member_flush_in_progress") or 0) - 1
        if depth > 0:
            user_data["member_flush_in_progress"] = depth
            return False
        user_data.pop("member_flush_in_progress", None)
        return True

    @classmethod
    def _finish_member_flush_generation(cls, user_data: dict[str, Any]) -> None:
        """Snapshot what the finished flush left behind, if an opt-out asked.

        The settings path defers its snapshot while a flush is in flight —
        popping the live mapping there would hand the in-flight request's
        own payload to a second submission. Whatever remains here (entries
        it failed to commit, plus turns written during it) is what the
        opt-out settlement should carry. Only the last flush standing may
        take that snapshot; an earlier one finishing says nothing about the
        mapping another flush is still holding."""
        if not cls._exit_member_flush(user_data):
            return
        if not user_data.pop("member_snapshot_due", None):
            return
        fresh_buckets = user_data.pop("group_member_memory_messages", None) or {}
        fresh_labels = user_data.pop("group_member_memory_labels", None) or {}
        if not fresh_buckets:
            return
        pending = user_data.setdefault("pending_settle_buckets", {})
        for sender, msgs in fresh_buckets.items():
            pending.setdefault(sender, []).extend(msgs)
        user_data.setdefault("pending_settle_labels", {}).update(fresh_labels)
        user_data["pending_member_settle"] = True
        # 供结算侧区分"这些是冲刷失败的残留（按 opt-out 丢弃）"与"这是
        # 冲刷结束后才快照出来的一代（必须留着排队）"。
        user_data["member_settle_generation_promoted"] = True

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
                # 清掉进来之前就挂着的标记：那可能是一次**上限排空**失败后
                # 顶上来的同一批数据，正是这次结算要处理的。留着它，等这次
                # 重试也失败时就会被误认成"更晚的一代"而保留下来，绕过
                # fail-closed 丢弃。只认这次结算过程中才产生的提升。
                current.pop("member_settle_generation_promoted", None)
                failed: list[str] = []
                if group_id and her_name and snapshot:
                    failed = await self._flush_member_buckets(
                        current, group_id=group_id, her_name=her_name,
                        reason="member_memory_disabled",
                        buckets=snapshot,
                        labels=current.get("pending_settle_labels") or {},
                        # 这条路径**没有下一轮**：失败的桶按 opt-out 语义
                        # 当场永久丢弃。打包让一次传输抖动的爆炸半径从
                        # 1 个成员涨到整批（≤8），而它省下的那几次 LLM
                        # 调用只在这个罕见的用户主动转变上省一次。一桶
                        # 一请求把半径还原成打包前的样子；最坏用时
                        # （2 波 × 30s = 60s）正是 SETTLE_JOIN_TIMEOUT_
                        # LONG_SECONDS 当初按逐成员形态推出来的那个数。
                        isolate_segments=True,
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
                # 只清"这一代真的冲完了"的快照：冲刷期间第二次 OFF 会把
                # 新一代放进 *_next，冲完由 _finish_member_flush_generation
                # 顶上来——无条件整个 pop 会把那一代在它排队结算之前抹掉。
                # 冲刷失败留下的残留不在此列，仍按 opt-out 丢弃。
                if current.pop("member_settle_generation_promoted", None):
                    self.plugin.logger.info(
                        "[member_memory_disabled] 冲刷期间又攒了新一代成员"
                        "快照，保留待下轮结算"
                    )
                    current["pending_member_settle"] = True
                    return
                current.pop("pending_settle_buckets", None)
                current.pop("pending_settle_labels", None)
                current.pop("pending_member_settle", None)

            # 锁外先等在途排空：会话锁不再是屏障，这个结算可能抢在排空
            # 之前拿到锁，看到还没提升的空 pending_settle_buckets，什么都
            # 没做就收尾——随后排空提升出来的那一代就没有消费者了，会一直
            # 滞留到某次 idle/finalize（活跃群可能永远等不到）。等它落地，
            # 提升出来的一代正好被下面的 _settle_one 冲掉。
            if not await self._await_pending_session_settlement(
                session_key, timeout=self.SETTLE_JOIN_TIMEOUT_LONG_SECONDS,
            ):
                # 等不到就整轮跳过，而且**不能**让 _settle_one 跑：它收尾时
                # 会把 pending_member_settle 抹掉，而排空随后才提升出那一代
                # ——标记没了就再没有消费者，opt-out 之后一直滞留。留着标记
                # 与快照，交给后续的 finalize / 下一次转变消费。
                self.plugin.logger.warning(
                    f"排空在途，本轮跳过 opt-out 成员结算（{session_key}），"
                    f"标记保留待后续结算"
                )
                continue
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
            elif user_data.get("private_memory_mode") == "participant":
                # 私聊 participant 会话：以对方为主体的 scoped 结算，**绝不**
                # 落到下面的 legacy /process——那是主人的私聊语料。
                cutoff = user_data.get("participant_opt_out_cutoff", None)
                consumed_cutoff = cutoff
                if cutoff is not None:
                    # opt-out 截止点（对偶群分支）：只结算开关翻 OFF 时刻
                    # 之前的历史。
                    conversation_history = conversation_history[:max(0, int(cutoff))]
                sender_id = str(user_data.get("sender_id") or "").strip()
                if not sender_id:
                    # 防御性 fail-closed：没有 sender 就没有合法的写入目标。
                    # 宁可丢弃这段缓冲（走正常 pop+close 收尾），也不能把
                    # 它写进任何别的语料域。
                    self.plugin.logger.error(
                        f"[{reason}] participant 会话 {session_key} 缺 "
                        f"sender_id，按 fail-closed 丢弃未结算缓冲"
                    )
                else:
                    last_participant_digest_index = max(
                        0, int(user_data.get("last_participant_digest_index", 0)),
                    )
                    # 未授权边界地板 + cutoff 豁免（对偶群分支）：cutoff
                    # 之后记下的边界属于下一个时代，套到本窗口会把整段已
                    # 授权前缀当作已处理而丢弃。
                    nonconsent_floor = int(
                        user_data.get("nonconsent_history_end", 0) or 0
                    )
                    if cutoff is not None and nonconsent_floor > int(cutoff):
                        nonconsent_floor = 0
                    last_participant_digest_index = max(
                        last_participant_digest_index, nonconsent_floor,
                    )
                    if last_participant_digest_index > len(conversation_history):
                        # 历史被重复守卫重置后旧游标越界：钳到当前长度并
                        # 回写（对偶群分支），绝不回退。
                        last_participant_digest_index = len(conversation_history)
                        user_data["last_participant_digest_index"] = (
                            last_participant_digest_index
                        )
                    try:
                        participant_settled = (
                            await self._settle_participant_digest_batches(
                                user_data=user_data, sender_id=sender_id,
                                her_name=her_name, reason=reason,
                                conversation_history=conversation_history,
                                last_participant_digest_index=(
                                    last_participant_digest_index
                                ),
                                stop_at_provisional=retain_session,
                            )
                        )
                    except Exception as digest_error:
                        self.plugin.logger.error(
                            f"[{reason}] 私聊 {sender_id} scoped 结算失败: "
                            f"{digest_error}"
                        )
                        participant_settled = False
                    if not participant_settled:
                        # 游标停在最后一个成功批次，留给下一轮重试。
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
            cutoff_key = (
                "group_opt_out_cutoff" if user_data.get("is_group")
                else "participant_opt_out_cutoff"
            )
            if user_data.get(cutoff_key) == consumed_cutoff:
                user_data.pop(cutoff_key, None)
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
            # 拿不到群名就不带参（而不是传 None）：优雅退化的同时保持旧
            # 调用形状——display_name 只在真有名字时出现。
            digest_extra: dict[str, Any] = {}
            group_display_name = self._group_display_name(group_id)
            if group_display_name:
                digest_extra["display_name"] = group_display_name
            result = await self.plugin.memory_bridge.post_scoped_memory_history(
                her_name,
                scoped_messages,
                subject=self.plugin.memory_bridge.group_subject(group_id),
                timeout=30.0,
                **digest_extra,
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

            # 第四条要等在途排空的结算路径（前三条：idle / 关机 / opt-out
            # 成员结算）。UI 把群记忆与成员记忆联动关闭时，_sync_memory_
            # transitions 先跑成员结算、紧接着跑这里；成员结算等不到时会
            # 跳过，但这里的 finalize 照样把会话弹掉——skip 就白做了，排空
            # 攥着的快照一样孤儿化。
            # 与 opt-out 成员结算不同，这里**等不到也必须继续**：这是隐私
            # 转变，跳过等于 OFF 没落实，会话还挂着 memory_enabled。
            if not await self._await_pending_session_settlement(
                session_key, timeout=self.SETTLE_JOIN_TIMEOUT_LONG_SECONDS,
            ):
                self.plugin.logger.warning(
                    f"排空在途但群记忆转变必须落实（{session_key}），继续结算"
                )
            await self.plugin._run_with_session_lock(session_key, _sync_one)

    async def invalidate_private_session(self, qq_number: str) -> None:
        session_key = self.plugin._build_session_key(sender_id=qq_number, is_group=False)

        async def _invalidate() -> None:
            user_data = self.plugin._user_sessions.get(session_key)
            buffer_service = getattr(
                self.plugin, "reply_buffer_service", None,
            )
            if buffer_service is not None:
                # Permission mutation has already happened. Kill every delayed
                # reply from the old permission era before either settlement
                # or the retained-retry branch can leave it deliverable.
                buffer_service.cancel_pending(session_key, user_data)
            if user_data and (
                user_data.get("memory_enabled")
                or user_data.get("pending_disable_settle")
            ):
                retrying_disabled_settlement = bool(
                    user_data.get("pending_disable_settle")
                    and not user_data.get("memory_enabled")
                )
                if retrying_disabled_settlement:
                    # The cutoff is the authorization boundary; temporarily
                    # enable the finalizer exactly as discard/shutdown do.
                    user_data["memory_enabled"] = True
                try:
                    finalized = await self.finalize_user_memory_session(
                        session_key, reason="permission_change",
                    )
                finally:
                    survivor = self.plugin._user_sessions.get(session_key)
                    if retrying_disabled_settlement and survivor is user_data:
                        user_data["memory_enabled"] = False
                if finalized:
                    return
                if user_data.get("private_memory_mode") == "participant":
                    # A failed participant settlement retains unsent history
                    # for the next retry. Popping/closing here would make the
                    # failure irreversible and silently discard that history.
                    self.plugin.logger.warning(
                        f"参与者私聊结算失败，保留会话等待重试（{session_key}）"
                    )
                    user_data["pending_permission_discard"] = True
                    return

            user_data = self.plugin._user_sessions.pop(session_key, None)
            session = user_data.get("session") if user_data else None
            if session:
                await session.close()

        await self.plugin._run_with_session_lock(session_key, _invalidate)
