from __future__ import annotations

import asyncio

from typing import Any

from .permission import PermissionManager
from .group_permission import GroupPermissionManager


class QQSettingsService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def _stamp_group_memory_transition(self, *, enabled_after: bool) -> None:
        """同步（无 await）给"转变时刻已存在"的群会话打标：后台任务只处理
        带标会话——转变之后新建的会话天然无标、不被误结算/误 rebase（结构
        性保证，取代按可变 memory_enabled flag 猜测的启发式）。快速反向切换
        保留未消费的对向标记（ON 不清 disable 章，OFF 不覆写未消费的
        cutoff），排队中的各时代结算任务按转变锁次序各自消费。"""
        for ud in list(getattr(self.plugin, "_user_sessions", {}).values()):
            if not ud.get("is_group"):
                continue
            sess = ud.get("session")
            hist_len = len(getattr(sess, "_conversation_history", []) or [])
            if enabled_after:
                # 不清 disable 标记/cutoff：快速 OFF→ON 时排队中的 OFF
                # 结算还没消费它们——转变锁保证 OFF 任务先跑（结算到
                # cutoff 并弹掉自己的标记），随后 ON 任务再按本边界
                # rebase，两个时代各自成立。
                # 存转变时刻的边界：后台任务若用运行时 len(history)，
                # enable 之后到达的正当轮次会被一并跳过。
                ud["pending_enable_rebase"] = hist_len
            else:
                if not ud.get("pending_disable_settle"):
                    # cutoff：结算只到 opt-out 时刻，竞态窗口内的新轮次
                    # 不入库。
                    ud["group_opt_out_cutoff"] = hist_len
                # else：上一次 OFF 的结算还没消费其 cutoff（OFF→ON→OFF
                # 且首个结算被别的群拖延）——保留更早的界。覆写会把
                # finalize 的 floor 豁免判据（floor>cutoff 才归零）打
                # 歪：第一 OFF 时代记下的 nonconsent floor 落在新 cutoff
                # 之下，反过来盖掉第一时代之前尚未 digest 的已授权积压。
                # 保守代价=中间短暂 ON 时代的行按未授权丢弃。
                ud["pending_disable_settle"] = True
                ud.pop("pending_enable_rebase", None)

    def _stamp_participant_memory_transition(
        self, *, enabled_after: bool,
    ) -> list[tuple[dict[str, Any], int]]:
        """私聊 participant 记忆开关转变的同步盖章（对偶群版）。

        OFF：给既有 participant 会话盖 cutoff + pending 章——结算只到
        opt-out 时刻，竞态窗口内的新轮次不入库；消费者是后台结算任务与
        discard/关机兜底（它们本就认 pending_disable_settle）。
        ON：把未授权边界推到转变时刻——OFF 时代可能有未 stamp 的尾行
        （nonconsent 边界只在生成轮 finally 记），floor 一推即闭合；带
        未消费 disable 章的会话不动（旧时代结算先行，finalize 的
        floor>cutoff 豁免保证它仍只结算到 cutoff）。"""
        created_markers: list[tuple[dict[str, Any], int]] = []
        for ud in list(getattr(self.plugin, "_user_sessions", {}).values()):
            if ud.get("is_group"):
                continue
            sess = ud.get("session")
            hist_len = len(getattr(sess, "_conversation_history", []) or [])
            if enabled_after:
                if ud.get("pending_disable_settle"):
                    # The old opt-out prefix still owns this session. Reusing it
                    # after re-enable would append new authorized rows behind
                    # the old cutoff, and the eventual retry would truncate
                    # them. Force bootstrap to settle/discard it first.
                    ud["pending_permission_discard"] = True
                    continue
                if ud.get("memory_enabled"):
                    continue
                ud["nonconsent_history_end"] = max(
                    int(ud.get("nonconsent_history_end", 0) or 0), hist_len,
                )
                continue
            if ud.get("private_memory_mode") != "participant":
                # legacy admin 会话与从未开过记忆的会话都不参与 participant
                # 的 opt-out 结算。
                continue
            if not ud.get("pending_disable_settle"):
                ud["participant_opt_out_cutoff"] = hist_len
                created_markers.append((ud, hist_len))
            # else：上一次 OFF 的结算还没消费其 cutoff——保留更早的界
            # （与群版同理：覆写会打歪 floor 豁免判据）。
            ud["pending_disable_settle"] = True
        return created_markers

    async def _settle_participant_sessions_on_disable(self) -> None:
        """participant 开关 ON->OFF：把带章会话按 cutoff 结算掉。

        对偶 invalidate_group_sessions 的 OFF 半边，但刻意薄得多：失败
        **保留**章与 cutoff 交给 discard/关机兜底重试（它们本就消费
        pending_disable_settle），不做群版的 fail-closed 销毁与回滚恢复
        ——cutoff 围栏保证无论谁最终结算，入库的都只有 opt-out 之前的
        已授权前缀。"""
        lock = getattr(self.plugin, "_memory_transition_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self.plugin._memory_transition_lock = lock
        async with lock:
            for session_key in list(
                getattr(self.plugin, "_user_sessions", {}).keys()
            ):
                async def _settle_one(key: str = session_key) -> None:
                    current = self.plugin._user_sessions.get(key)
                    if not current or current.get("is_group"):
                        return
                    if current.get("private_memory_mode") != "participant":
                        return
                    if not current.get("pending_disable_settle"):
                        return
                    # 临时按 opt-in 结算（对偶关机兜底）：cutoff 保证只带
                    # 出 opt-out 之前的历史。
                    current["memory_enabled"] = True
                    finalized = False
                    svc = self.plugin.session_memory_service
                    prev_progress = svc._settlement_progress(current)
                    while True:
                        # A plain ON->OFF transition has no future work that
                        # needs this client: let a successful finalization pop
                        # and close it.  Only a rapid OFF->ON transition stamps
                        # pending_permission_discard; that path must retain the
                        # old session until bootstrap can replace its memory
                        # domain safely.
                        retain = bool(current.get("pending_permission_discard"))
                        try:
                            finalized = await svc.finalize_user_memory_session(
                                key, reason="participant_memory_disabled",
                                retain_session=retain,
                            )
                        except Exception as exc:
                            self.plugin.logger.error(
                                f"[participant_memory_disabled] 私聊会话结算"
                                f"失败 ({key}): {exc}"
                            )
                            break
                        survivor = self.plugin._user_sessions.get(key)
                        if finalized or not survivor:
                            break
                        progress = svc._settlement_progress(survivor)
                        if progress == prev_progress:
                            break
                        prev_progress = progress
                    current = self.plugin._user_sessions.get(key)
                    if current is None:
                        return
                    current["memory_enabled"] = False
                    if finalized:
                        current.pop("pending_disable_settle", None)
                    else:
                        self.plugin.logger.warning(
                            f"[participant_memory_disabled] 会话 {key} 结算"
                            f"未完成，保留标记与 cutoff 待 discard/关机兜底"
                        )

                await self.plugin._run_with_session_lock(
                    session_key, _settle_one,
                )

    async def _persist_with_consent_rollback(
        self, *, group_memory_before: bool, group_memory_after: bool,
        member_memory_before: bool, member_memory_after: bool,
        cross_group_before: bool | None, cross_group_after: bool | None = None,
        participant_memory_before: bool | None = None,
        participant_memory_after: bool | None = None,
        participant_markers_created: list[
            tuple[dict[str, Any], int]
        ] | None = None,
        deferred_opt_ins: dict[str, bool] | None = None,
    ) -> bool:
        # 取消路径也要能发布：写盘被 shield 保护，取消 await 不取消它。
        """Persist settings and roll consent back if the write did not land.

        Cancellation counts as "not written": CancelledError bypasses
        persist_business_config's own except Exception, and leaving the
        runtime flags on an unpersisted opt-in would keep collecting."""
        rollback_kwargs = dict(
            group_memory_before=group_memory_before,
            group_memory_after=group_memory_after,
            member_memory_before=member_memory_before,
            member_memory_after=member_memory_after,
            cross_group_before=cross_group_before,
            cross_group_after=cross_group_after,
            participant_memory_before=participant_memory_before,
            participant_memory_after=participant_memory_after,
            participant_markers_created=participant_markers_created,
        )
        # 写盘跑成独立 task：config_store.save 内部是 to_thread 的原子写，
        # 取消这个 await 并不会取消那个线程——它可能照样把新配置落盘。
        # 直接按"没写成"回滚会让磁盘与运行时永久相反（重启后才暴露）。
        # 取消时先把 task 等出真实结果，再决定是否回滚，然后再抛。
        save_task = asyncio.ensure_future(
            self.persist_business_config(overlay=deferred_opt_ins)
        )
        try:
            success = await asyncio.shield(save_task)
        except asyncio.CancelledError:
            try:
                success = await save_task
            except asyncio.CancelledError:
                # 写盘本身也被取消（不是仅我们这次 await）：没落盘。
                success = False
            except Exception as exc:
                self.plugin.logger.error(f"取消期间的配置写盘失败: {exc}")
                success = False
            self._rollback_unpersisted_memory_toggles(success, **rollback_kwargs)
            if success and deferred_opt_ins:
                # 写盘真的落地了（shield 让它跑完）：磁盘已是新值，此处不
                # 发布的话运行时会一直停在关闭，直到重启才突然打开——用户
                # 眼里就是"取消了的操作过一阵自己生效了"。
                self._publish_consent_opt_ins(deferred_opt_ins)
            raise
        except BaseException:
            self._rollback_unpersisted_memory_toggles(False, **rollback_kwargs)
            raise
        self._rollback_unpersisted_memory_toggles(success, **rollback_kwargs)
        return success

    def _clamp_member_to_group(
        self, deferred_opt_ins: dict[str, bool] | None = None,
    ) -> None:
        """Member memory is a child of group memory — enforce it here.

        The dashboard unchecks both together, but the action takes each key
        on its own: `group_memory_enabled=False` alone left the member flag
        true, and collection gates only on that flag, so participant buckets
        kept filling after the opt-out and would be flushed the next time
        group memory came back on."""
        if self.plugin._qq_settings.get("group_memory_enabled", False):
            return
        if deferred_opt_ins is not None and deferred_opt_ins.get(
            "group_memory_enabled"
        ) and deferred_opt_ins.get("group_member_memory_enabled"):
            # 父子都在同一次扣发队列里：两个键会在写盘成功后一起发布，
            # 此刻"父还没生效"是延迟发布的中间态，不是没授权。首次开箱
            # 正走这条路（两个面板每次保存都同时提交两个复选框），按未
            # 授权砍掉 member 会连磁盘一起写成关闭——用户看着勾上了，
            # 回来一刷新又是关的，得再存一次才算数。
            # 只有父开关在队列里则不放行：那条请求根本没要开成员记忆，
            # 残留的 group_member_memory_enabled=true（手改配置/更早的
            # 版本写下的）必须在这里清掉，不能随父开关一起被发布出去。
            return
        if deferred_opt_ins is not None:
            deferred_opt_ins.pop("group_member_memory_enabled", None)
        self.plugin._qq_settings["group_member_memory_enabled"] = False

    def _publish_consent_opt_ins(self, opt_ins: dict[str, bool]) -> None:
        """Apply opt-ins that were held back until the write landed.

        Stamping and session sync happen here too: doing them before the
        write would let a failed save leave marked sessions behind for a
        consent that never took effect."""
        group_before = bool(
            self.plugin._qq_settings.get("group_memory_enabled", False)
        )
        participant_before = bool(
            self.plugin._qq_settings.get(
                "private_participant_memory_enabled", False,
            )
        )
        for key in opt_ins:
            self.plugin._qq_settings[key] = True
        # 迟发的 opt-in 同样受父子约束：群记忆关着时把 member 打开无效。
        self._clamp_member_to_group()
        group_after = bool(
            self.plugin._qq_settings.get("group_memory_enabled", False)
        )
        if not participant_before and bool(
            self.plugin._qq_settings.get(
                "private_participant_memory_enabled", False,
            )
        ):
            # ON 盖章：把 OFF 会话的未授权边界推到此刻，OFF 时代未 stamp
            # 的尾行不得随后续结算入库。无须后台任务——prime 的实时门控
            # 会让下一轮起 memory_enabled 翻 True。
            self._stamp_participant_memory_transition(enabled_after=True)
        if group_after != group_before:
            self._stamp_group_memory_transition(enabled_after=True)
            self._spawn_group_memory_sync_task(
                self._sync_memory_transitions(
                    settle_members=False,
                    group_transition=True,
                    group_enabled_after=True,
                )
            )
        self.plugin._emit_log(
            "INFO", f"记忆开关已开启并写盘: {sorted(opt_ins)}"
        )

    @property
    def _consent_transaction_lock(self) -> asyncio.Lock:
        """Serializes read-before → mutate → persist → rollback."""
        lock = getattr(self, "_consent_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._consent_lock = lock
        return lock

    def _rollback_unpersisted_memory_toggles(
        self, persisted: bool, *,
        group_memory_before: bool, group_memory_after: bool,
        member_memory_before: bool, member_memory_after: bool,
        cross_group_before: bool | None = None,
        cross_group_after: bool | None = None,
        participant_memory_before: bool | None = None,
        participant_memory_after: bool | None = None,
        participant_markers_created: list[
            tuple[dict[str, Any], int]
        ] | None = None,
    ) -> None:
        """落盘失败时回滚记忆 consent 开关：重启会回到旧值，运行时若继续
        按新值收集，等于在"未成功保存的授权"下入库。回滚运行时政策并按
        反向转变重新盖章+结算（与用户手动切回等价，标记模型天然支持连续
        切换）。member 单独回滚：OFF 回滚（开失败）下新收集的活 bucket 在
        finalize 被空映射替换、按 fail-closed 丢弃；ON 回滚（关失败）已
        分离的快照由结算任务照常入库。"""
        if persisted:
            return
        if cross_group_before is not None and cross_group_after is not None:
            # 只回滚"本次请求确实改过"的开关：每个保存请求都会带上这个
            # 字段（哪怕它没动这个开关），照旧值恢复会把另一个请求刚刚
            # 成功落盘的 opt-out 顶回 ON——磁盘已退出、运行时却继续跨群
            # 披露，直到重启才暴露。（被更晚的保存超越由事务序号挡住。）
            if cross_group_before != cross_group_after:
                # 跨群上下文也是 consent 开关：写盘失败后留着新值，群轮会
                # 在"从未成功保存的授权"下注入其他群的最近消息。纯读取
                # 开关，恢复 flag 即可（无会话级结算）。
                self.plugin._qq_settings["allow_cross_group_context"] = cross_group_before
                self.plugin._emit_log(
                    "WARNING",
                    "跨群上下文开关变更未能写盘，已回滚运行时策略",
                )
        if (
            participant_memory_before is not None
            and participant_memory_after is not None
            and participant_memory_before != participant_memory_after
        ):
            # participant 开关只有 ON→OFF 方向能走到这里（OFF→ON 被延迟
            # 发布扣着，写盘失败时根本没发布过）。恢复运行时策略并撤掉
            # 本次盖下且尚未被结算消费的章——已消费的（结算到 cutoff）是
            # 在合法授权时代内入库的，无须也无法撤销。
            self.plugin._qq_settings["private_participant_memory_enabled"] = (
                participant_memory_before
            )
            if participant_memory_before:
                # Remove only markers created by this failed transaction.
                # Older pending settlements deliberately survive rapid
                # ON/OFF toggles and must keep their original retry cutoff.
                for ud, cutoff in participant_markers_created or []:
                    if (
                        ud.get("pending_disable_settle")
                        and int(
                            ud.get("participant_opt_out_cutoff", -1) or 0
                        ) == cutoff
                    ):
                        ud.pop("pending_disable_settle", None)
                        ud.pop("participant_opt_out_cutoff", None)
                # A receipt-authorized turn may have created and primed its
                # participant session while the failed OFF save was awaiting
                # disk I/O.  It has no transition marker, but priming observed
                # the temporary live OFF state and left memory_enabled=False.
                # Restore only current participant sessions; post-OFF turns
                # are stamped with mode=None, while older pending settlements
                # must remain frozen until their original cutoff is handled.
                for ud in list(
                    getattr(self.plugin, "_user_sessions", {}).values()
                ):
                    if (
                        ud.get("is_group")
                        or ud.get("private_memory_mode") != "participant"
                        or ud.get("pending_disable_settle")
                        or ud.get("pending_permission_discard")
                        or ud.get("pending_identity_discard")
                    ):
                        continue
                    ud["memory_enabled"] = True
            self.plugin._emit_log(
                "WARNING",
                "私聊成员记忆开关变更未能写盘，已回滚运行时策略",
            )
        if group_memory_before != group_memory_after:
            self.plugin._qq_settings["group_memory_enabled"] = group_memory_before
            self.plugin._qq_settings["group_member_memory_enabled"] = member_memory_before
            if group_memory_before:
                # 回滚方向是"回到 ON"（before 是旧值：ON→OFF 保存失败时
                # 它为 True）。并发的 disable 结算若失败会把游标推到
                # len(history) 当 opt-out 清理——标记让 rebase 恢复
                # opt-out 之前的位置，别让这段已授权历史被永久跳过。
                for ud in list(
                    getattr(self.plugin, "_user_sessions", {}).values()
                ):
                    if ud.get("is_group"):
                        ud["group_settle_rollback_pending"] = True
            if member_memory_before and not member_memory_after:
                # 双开关同关（UI 联动）后写盘失败：member 侧的 bucket 已被
                # 挪进 pending 快照，排队的 opt-out 结算会按 opt-out 语义
                # 清掉它们——与 member-only 分支同样需要保护+恢复，否则
                # 先前已保存 consent 下收集的轮次永久丢失。
                for ud in list(
                    getattr(self.plugin, "_user_sessions", {}).values()
                ):
                    if ud.get("is_group"):
                        ud["member_settle_rollback_pending"] = True
                self._spawn_group_memory_sync_task(
                    self._restore_member_snapshots()
                )
            self._stamp_group_memory_transition(enabled_after=group_memory_before)
            # 回滚到 OFF（开启保存失败）时用 discard 语义：失败窗口内收到
            # 的消息是在"从未成功保存的 opt-in"下入历史的，普通 OFF 结算
            # 会把它们 digest 入库——恰好持久化了本该拒绝的数据。丢弃而非
            # 结算。回滚到 ON（关闭保存失败）方向照常 rebase。
            self._spawn_group_memory_sync_task(
                self._sync_memory_transitions(
                    settle_members=False,
                    group_transition=True,
                    group_enabled_after=group_memory_before,
                    rollback_discard=not group_memory_before,
                )
            )
            self.plugin._emit_log(
                "WARNING",
                "群记忆开关变更未能写盘，已回滚运行时策略（保持磁盘与内存一致）",
            )
        elif member_memory_before != member_memory_after:
            self.plugin._qq_settings["group_member_memory_enabled"] = member_memory_before
            if member_memory_before:
                # 关闭保存失败：OFF 盖章已把活 bucket 快照进 pending 槽，
                # 排队的 opt-out 结算失败时会按 opt-out 丢弃它们——但这些
                # 轮次是在先前已保存的 consent 下收集的，保存失败后应留在
                # 活 bucket 等正常结算。恢复必须与在途结算串行（转变锁+
                # 会话锁）：无锁复制会与 awaiting HTTP 的 flush 任务共享
                # 快照对象，flush 成功只清旧对象、live 副本残留重复提取。
                # 同步打标（早于结算任务拿转变锁）：结算失败时不得按
                # opt-out 丢弃快照——回滚任务随后要用它恢复。
                for ud in list(
                    getattr(self.plugin, "_user_sessions", {}).values()
                ):
                    if ud.get("is_group"):
                        ud["member_settle_rollback_pending"] = True
                self._spawn_group_memory_sync_task(
                    self._restore_member_snapshots()
                )
            if not member_memory_before:
                # 开启保存失败：失败窗口内收集的 bucket 属于"从未成功保存
                # 的 opt-in"——留着的话，之后成功开启时会与新授权项混合
                # 入库。对齐群开关回滚的 discard 语义，直接丢弃活 bucket
                # （pending 快照属于之前已保存的时代，不动）。
                for ud in list(
                    getattr(self.plugin, "_user_sessions", {}).values()
                ):
                    if ud.get("is_group"):
                        ud.pop("group_member_memory_messages", None)
                        ud.pop("group_member_memory_labels", None)
            self.plugin._emit_log(
                "WARNING",
                "成员记忆开关变更未能写盘，已回滚运行时策略",
            )

    async def _restore_member_snapshots(self) -> None:
        """Merge pending settlement snapshots back into live buckets.

        Only for the member ON->OFF save-failure rollback. Runs under the
        transition lock (serialized with any in-flight settlement) and the
        per-session lock; a settlement that completed first has already
        consumed or dropped the snapshot, making this a no-op."""
        lock = getattr(self.plugin, "_memory_transition_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self.plugin._memory_transition_lock = lock
        async with lock:
            for session_key in list(
                getattr(self.plugin, "_user_sessions", {}).keys()
            ):
                async def _restore_one(key: str = session_key) -> None:
                    ud = self.plugin._user_sessions.get(key)
                    if not isinstance(ud, dict) or not ud.get("is_group"):
                        return
                    ud.pop("member_settle_rollback_pending", None)
                    snapshot = ud.pop("pending_settle_buckets", None)
                    snap_labels = ud.pop("pending_settle_labels", None) or {}
                    ud.pop("pending_member_settle", None)
                    if not snapshot:
                        return
                    live = ud.setdefault("group_member_memory_messages", {})
                    for sender, msgs in snapshot.items():
                        live[sender] = list(msgs) + list(live.get(sender, []))
                    live_labels = ud.setdefault(
                        "group_member_memory_labels", {}
                    )
                    for sender, label in snap_labels.items():
                        live_labels.setdefault(sender, label)

                await self.plugin._run_with_session_lock(
                    session_key, _restore_one,
                )

    async def _sync_memory_transitions(
        self, *, settle_members: bool, group_transition: bool,
        group_enabled_after: bool, rollback_discard: bool = False,
    ) -> None:
        """Ordered transition sync: member buckets settle BEFORE the group
        invalidation, so disabling both toggles at once (the UI links them)
        cannot drop buckets via a finalize that already sees the member
        option off."""
        # 串行化连续开关切换：快速 OFF→ON 会让两个后台任务交错，后一个
        # 转变可能在前一个结算完成前改写会话状态。
        lock = getattr(self.plugin, "_memory_transition_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self.plugin._memory_transition_lock = lock
        async with lock:
            if settle_members:
                await self.plugin.session_memory_service.settle_member_buckets_on_disable()
            if group_transition:
                await self.plugin.session_memory_service.invalidate_group_sessions(
                    enabled=group_enabled_after,
                    discard_only=rollback_discard,
                )

    def _spawn_group_memory_sync_task(self, coro) -> None:
        self.plugin._spawn_memory_sync_task(coro)

    async def load_business_config(self) -> dict[str, Any]:
        self.plugin._qq_settings = await self.plugin.config_store.load()
        self.plugin.backlog_store = self.plugin._create_backlog_store_from_settings(self.plugin._qq_settings)
        self._enforce_attention_for_dynamic_mode()
        return dict(self.plugin._qq_settings)

    async def ensure_business_config_initialized(self) -> dict[str, Any]:
        if not await self.plugin.config_store.exists():
            return self.plugin.config_store.default_config()
        return await self.load_business_config()

    async def create_business_config(self) -> dict[str, Any]:
        self.plugin._qq_settings = await self.plugin.config_store.create_empty()
        return dict(self.plugin._qq_settings)

    async def persist_business_config(
        self, overlay: dict[str, Any] | None = None,
    ) -> bool:
        """overlay: 要写进磁盘、但暂时不对运行时可见的键。

        延迟生效的 opt-in 走这里：磁盘必须记下用户请求的新值（否则重启后
        开关自己弹回去），而运行时要等写盘成功、由调用方显式发布。save()
        会返回规范化后的新 dict 并顶替 _qq_settings——发布之前得把这些键
        按旧值压回去，不然"延迟"会被这次顶替悄悄抵消。"""
        try:
            self.plugin._qq_settings["trusted_users"] = self.plugin.permission_mgr.list_users() if self.plugin.permission_mgr else []
            self.plugin._qq_settings["trusted_groups"] = self.plugin.group_permission_mgr.list_groups() if self.plugin.group_permission_mgr else []
            pre_publish = {
                key: bool(self.plugin._qq_settings.get(key, False))
                for key in (overlay or {})
            }
            payload = dict(self.plugin._qq_settings)
            payload.update(overlay or {})
            saved = await self.plugin.config_store.save(payload)
            for key, value in pre_publish.items():
                saved[key] = value
            self.plugin._qq_settings = saved
            self.plugin.backlog_store = self.plugin._create_backlog_store_from_settings(self.plugin._qq_settings)
            return True
        except Exception as e:
            self.plugin.logger.error(f"持久化 QQ 配置失败: {e}")
            return False

    def apply_runtime_settings(self, settings: dict[str, Any]) -> None:
        self.plugin._normal_relay_probability = float(settings.get("normal_relay_probability", 0.1) or 0.1)
        self.plugin._truth_reply_probability = float(settings.get("open_reply_probability", settings.get("truth_reply_probability", 0.1)) or 0.1)
        self.plugin._max_concurrent_messages = max(1, int(settings.get("max_concurrent_messages", 3) or 3))
        self.plugin._message_concurrency = __import__("asyncio").Semaphore(self.plugin._max_concurrent_messages)
        self.plugin._ai_connect_timeout_seconds = max(1.0, float(settings.get("ai_connect_timeout_seconds", 10.0) or 10.0))
        self.plugin._ai_turn_timeout_seconds = max(5.0, float(settings.get("ai_turn_timeout_seconds", 60.0) or 60.0))
        self.plugin._handler_shutdown_timeout_seconds = max(1.0, float(settings.get("handler_shutdown_timeout_seconds", 10.0) or 10.0))
        self.plugin._backlog_summary_threshold = max(1, int(settings.get("backlog_summary_threshold", 10) or 10))
        self.plugin._backlog_notify_cooldown_seconds = max(60, int(settings.get("backlog_notify_cooldown_seconds", 900) or 900))
        self.plugin._backlog_issue_notify_threshold = max(1, int(settings.get("backlog_issue_notify_threshold", 1) or 1))
        self.plugin._sticker_cooldown_messages = max(0, int(settings.get("sticker_cooldown_messages", 5) or 5))
        # 猫娘动态注意力策略配置
        self.plugin._strategy_mode = self.plugin.config_store._normalize_strategy_mode(settings.get("strategy_mode"))
        self._enforce_attention_for_dynamic_mode()
        # 前端日志：显示当前连接配置（token 脱敏），方便用户排查浏览器自动回填等问题
        url = str(settings.get("onebot_url") or "").strip()
        masked = self.plugin._mask_token(str(settings.get("token") or ""))
        mode = str(settings.get("qq_connection_mode") or "napcat").strip()
        self.plugin._emit_log("INFO", f"连接模式: {mode} | 监听地址: {url or '(未配置)'} | Token: {masked}{' (空)' if not settings.get('token') else ''} | 策略: {self.plugin._strategy_mode}")

    def _enforce_attention_for_dynamic_mode(self) -> None:
        """neko_dynamic 模式下强制启用多群注意力，确保磁盘配置与运行时一致。"""
        strategy_mode = self.plugin.config_store._normalize_strategy_mode(
            self.plugin._qq_settings.get("strategy_mode")
        )
        if strategy_mode == "neko_dynamic":
            self.plugin._qq_settings["enable_group_attention"] = True

    def rebuild_permission_managers(self, config: dict[str, Any]) -> None:
        self.plugin.permission_mgr = PermissionManager(config.get("trusted_users", []))
        self.plugin.group_permission_mgr = GroupPermissionManager(config.get("trusted_groups", []))
        self.plugin._refresh_admin_qq()

    async def save_settings(self, **kwargs: Any) -> dict[str, Any]:
        """Serialize the whole settings transaction.

        Two overlapping RPCs would otherwise mutate the shared settings
        dict, then race on the disk write: the loser's fields (connection
        url, token, reply mode, probabilities) are silently dropped when
        the winner replaces the dict, and the consent rollback cannot
        reason about a `before` value another request already changed."""
        async with self._consent_transaction_lock:
            return await self._save_settings_locked(**kwargs)

    async def _save_settings_locked(self, **kwargs: Any) -> dict[str, Any]:
        onebot_url = kwargs.get("onebot_url")
        token = kwargs.get("token")
        napcat_directory = kwargs.get("napcat_directory")
        show_napcat_window = kwargs.get("show_napcat_window")
        reply_mode = kwargs.get("reply_mode")
        show_onboarding = kwargs.get("show_onboarding")
        guide_step_napcat_done = kwargs.get("guide_step_napcat_done")
        guide_step_config_done = kwargs.get("guide_step_config_done")
        guide_step_runtime_done = kwargs.get("guide_step_runtime_done")
        normal_relay_probability = kwargs.get("normal_relay_probability")
        truth_reply_probability = kwargs.get("truth_reply_probability")
        backlog_labels = kwargs.get("backlog_labels")

        if onebot_url is not None:
            self.plugin._qq_settings["onebot_url"] = str(onebot_url or "").strip()
            self.plugin._emit_log("INFO", f"反向 WS 监听地址已更新: {self.plugin._qq_settings['onebot_url'] or '(空)'}")
        if token is not None:
            self.plugin._qq_settings["token"] = str(token or "")
            masked = self.plugin._mask_token(self.plugin._qq_settings["token"])
            self.plugin._emit_log("INFO", f"Token 已更新: {masked}{' (空)' if not self.plugin._qq_settings['token'] else ''}")
        qq_connection_mode = kwargs.get("qq_connection_mode")
        qq_open_app_id = kwargs.get("qq_open_app_id")
        qq_open_client_secret = kwargs.get("qq_open_client_secret")
        if qq_connection_mode is not None:
            self.plugin._qq_settings["qq_connection_mode"] = str(qq_connection_mode or "napcat").strip()
            self.plugin._emit_log("INFO", f"连接模式已切换: {self.plugin._qq_settings['qq_connection_mode']}")
        if qq_open_app_id is not None:
            self.plugin._qq_settings["qq_open_app_id"] = str(qq_open_app_id or "").strip()
        if qq_open_client_secret is not None:
            self.plugin._qq_settings["qq_open_client_secret"] = str(qq_open_client_secret or "").strip()
        if napcat_directory is not None:
            self.plugin._qq_settings["napcat_directory"] = str(napcat_directory or "").strip()
        if show_napcat_window is not None:
            self.plugin._qq_settings["show_napcat_window"] = bool(show_napcat_window)
        if reply_mode is not None:
            self.plugin._qq_settings["reply_mode"] = self.plugin.config_store.normalize_reply_mode(reply_mode)
            self.plugin._emit_log("INFO", f"回复模式已切换: {self.plugin._qq_settings['reply_mode']}")
        if show_onboarding is not None:
            self.plugin._qq_settings["show_onboarding"] = bool(show_onboarding)
        if guide_step_napcat_done is not None:
            self.plugin._qq_settings["guide_step_napcat_done"] = bool(guide_step_napcat_done)
        if guide_step_config_done is not None:
            self.plugin._qq_settings["guide_step_config_done"] = bool(guide_step_config_done)
        if guide_step_runtime_done is not None:
            self.plugin._qq_settings["guide_step_runtime_done"] = bool(guide_step_runtime_done)
        if normal_relay_probability is not None:
            value = float(normal_relay_probability)
            if value < 0.0 or value > 1.0:
                raise ValueError("normal_relay_probability 必须在 0 到 1 之间")
            self.plugin._qq_settings["normal_relay_probability"] = value
            self.plugin._normal_relay_probability = value
        if truth_reply_probability is not None:
            value = float(truth_reply_probability)
            if value < 0.0 or value > 1.0:
                raise ValueError("truth_reply_probability 必须在 0 到 1 之间")
            self.plugin._qq_settings["open_reply_probability"] = value
            self.plugin._qq_settings["truth_reply_probability"] = value
            self.plugin._truth_reply_probability = value
        if backlog_labels is not None:
            self.plugin._qq_settings["backlog_labels"] = self.plugin.config_store.normalize_backlog_labels(backlog_labels)
        proactive_silence_seconds = kwargs.get("proactive_silence_seconds")
        if proactive_silence_seconds is not None:
            self.plugin._qq_settings["proactive_silence_seconds"] = max(0, int(proactive_silence_seconds))
        sticker_cooldown_messages = kwargs.get("sticker_cooldown_messages")
        if sticker_cooldown_messages is not None:
            self.plugin._qq_settings["sticker_cooldown_messages"] = max(0, int(sticker_cooldown_messages))
            self.plugin._sticker_cooldown_messages = max(0, int(sticker_cooldown_messages))
        group_attention_decay_per_second = kwargs.get("group_attention_decay_per_second")
        if group_attention_decay_per_second is not None:
            self.plugin._qq_settings["group_attention_decay_per_second"] = max(0.001, float(group_attention_decay_per_second))
        group_attention_message_recovery = kwargs.get("group_attention_message_recovery")
        if group_attention_message_recovery is not None:
            self.plugin._qq_settings["group_attention_message_recovery"] = max(0.0, float(group_attention_message_recovery))
        group_attention_reply_penalty = kwargs.get("group_attention_reply_penalty")
        if group_attention_reply_penalty is not None:
            self.plugin._qq_settings["group_attention_reply_penalty"] = max(0.0, float(group_attention_reply_penalty))
        group_attention_keyword_boost_scale = kwargs.get("group_attention_keyword_boost_scale")
        if group_attention_keyword_boost_scale is not None:
            self.plugin._qq_settings["group_attention_keyword_boost_scale"] = max(0.1, float(group_attention_keyword_boost_scale))
        group_attention_focus_lock_seconds = kwargs.get("group_attention_focus_lock_seconds")
        if group_attention_focus_lock_seconds is not None:
            self.plugin._qq_settings["group_attention_focus_lock_seconds"] = max(0, int(group_attention_focus_lock_seconds))
        group_attention_max_score = kwargs.get("group_attention_max_score")
        if group_attention_max_score is not None:
            self.plugin._qq_settings["group_attention_max_score"] = max(1.0, float(group_attention_max_score))
        group_attention_focus_threshold = kwargs.get("group_attention_focus_threshold")
        if group_attention_focus_threshold is not None:
            self.plugin._qq_settings["group_attention_focus_threshold"] = max(0.1, float(group_attention_focus_threshold))
        group_attention_min_threshold = kwargs.get("group_attention_min_threshold")
        if group_attention_min_threshold is not None:
            self.plugin._qq_settings["group_attention_min_threshold"] = max(0.0, float(group_attention_min_threshold))
        group_attention_message_gain = kwargs.get("group_attention_message_gain")
        if group_attention_message_gain is not None:
            self.plugin._qq_settings["group_attention_message_gain"] = max(0.0, float(group_attention_message_gain))
        group_attention_focus_cooldown_seconds = kwargs.get("group_attention_focus_cooldown_seconds")
        if group_attention_focus_cooldown_seconds is not None:
            self.plugin._qq_settings["group_attention_focus_cooldown_seconds"] = max(10, int(group_attention_focus_cooldown_seconds))
        group_attention_focus_rise_seconds = kwargs.get("group_attention_focus_rise_seconds")
        if group_attention_focus_rise_seconds is not None:
            self.plugin._qq_settings["group_attention_focus_rise_seconds"] = max(0, int(group_attention_focus_rise_seconds))
        icebreaker_cold_threshold = kwargs.get("icebreaker_cold_threshold")
        if icebreaker_cold_threshold is not None:
            self.plugin._qq_settings["icebreaker_cold_threshold"] = max(0, int(icebreaker_cold_threshold))
        retroactive_review_max_messages = kwargs.get("retroactive_review_max_messages")
        if retroactive_review_max_messages is not None:
            self.plugin._qq_settings["retroactive_review_max_messages"] = max(1, int(retroactive_review_max_messages))
        retroactive_review_max_reply = kwargs.get("retroactive_review_max_reply")
        if retroactive_review_max_reply is not None:
            self.plugin._qq_settings["retroactive_review_max_reply"] = max(1, int(retroactive_review_max_reply))
        group_memory_before = bool(
            self.plugin._qq_settings.get("group_memory_enabled", False)
        )
        member_memory_before = bool(
            self.plugin._qq_settings.get("group_member_memory_enabled", False)
        )
        participant_memory_before = bool(
            self.plugin._qq_settings.get(
                "private_participant_memory_enabled", False,
            )
        )
        cross_group_before = bool(
            self.plugin._qq_settings.get("allow_cross_group_context", False)
        )
        # 授权方向不对称：关掉立刻生效（多关一会儿只是保守），打开必须等
        # 写盘成功——消息处理不取设置事务锁，写盘期间到达的轮次会照新开关
        # 读 scoped/跨群记忆并把回复**发出去**，而回滚只能清本地状态，收不
        # 回已经说出去的话（跨群更是连会话清理都没有）。
        deferred_opt_ins: dict[str, bool] = {}
        for key in (
            "group_memory_enabled",
            "group_member_memory_enabled",
            "private_participant_memory_enabled",
            "allow_cross_group_context",
        ):
            value = kwargs.get(key)
            if value is None:
                continue
            live = bool(self.plugin._qq_settings.get(key, False))
            if key == "group_member_memory_enabled":
                # 子开关在父开关关着时授权不了任何采集（收集侧与接收边界
                # 读的都是 member and group），所以"是否已经生效"要与父
                # 开关取与。否则一条残留的 group_member_memory_enabled=
                # true 会让"这次真的在开成员记忆"看起来像没变化，跳过延迟
                # 发布、也跳过与父开关同批发布。
                live = live and bool(
                    self.plugin._qq_settings.get("group_memory_enabled", False)
                )
            if bool(value) and not live:
                deferred_opt_ins[key] = True
                continue
            self.plugin._qq_settings[key] = bool(value)
        self._clamp_member_to_group(deferred_opt_ins)
        group_memory_after = bool(
            self.plugin._qq_settings.get("group_memory_enabled", False)
        )
        member_memory_after = bool(
            self.plugin._qq_settings.get("group_member_memory_enabled", False)
        )
        member_turning_off = member_memory_before and not member_memory_after
        if member_turning_off:
            # 同步打标：并发的 idle/discard finalizer 在后台结算任务拿到
            # 锁之前跑到时，凭标记照常冲 bucket（finalize 侧配合读取）。
            for ud in list(getattr(self.plugin, "_user_sessions", {}).values()):
                if ud.get("is_group") and ud.get("group_member_memory_messages"):
                    # 快照分离：OFF 时代的 bucket 挪进 pending 槽。快速
                    # re-enable 后新授权轮写全新的活 bucket，迟到的结算
                    # 任务只消费快照，绝不吞新轮。
                    if ud.get("member_flush_in_progress"):
                        # 有冲刷在飞：**别碰**活 bucket。上限触发的排空冲的
                        # 就是这个映射本身，这里把它 pop 走等于把在途请求
                        # 的载荷复制一份——那次成功后只弹走旧映射，复制件
                        # 随后被当成新一代重交，同一批消息进两次。改为记一
                        # 个待办，等冲刷结束时把**剩下的**（失败的 + 期间
                        # 新写的）快照出来。
                        ud["member_snapshot_due"] = True
                        ud["pending_member_settle"] = True
                        continue
                    fresh_buckets = ud.pop("group_member_memory_messages")
                    fresh_labels = ud.pop("group_member_memory_labels", {})
                    pending = ud.setdefault("pending_settle_buckets", {})
                    for sender, msgs in fresh_buckets.items():
                        # OFF→ON→OFF 连续切换时旧快照可能还没被结算：合并
                        # 而非覆盖，先前授权的轮次不得被孤儿化。
                        pending.setdefault(sender, []).extend(msgs)
                    ud.setdefault("pending_settle_labels", {}).update(fresh_labels)
                    ud["pending_member_settle"] = True
        participant_memory_after = bool(
            self.plugin._qq_settings.get(
                "private_participant_memory_enabled", False,
            )
        )
        participant_markers_created: list[
            tuple[dict[str, Any], int]
        ] = []
        participant_settle_needed = False
        if participant_memory_before and not participant_memory_after:
            # 关闭立即生效（与其余 consent 键同向不对称）：同步盖章后交
            # 后台任务按 cutoff 结算既有 participant 会话。ON 方向在
            # _publish_consent_opt_ins（写盘成功后）处理。
            participant_markers_created = (
                self._stamp_participant_memory_transition(enabled_after=False)
            )
            participant_settle_needed = True
        if group_memory_before != group_memory_after:
            self._stamp_group_memory_transition(enabled_after=group_memory_after)
        if member_turning_off or group_memory_after != group_memory_before:
            # 记忆开关转变必须同步既有群会话（对偶私聊权限切换的
            # _invalidate_private_session）。单协程顺序执行保证次序：
            # member 结算必须先于群 invalidate——UI 关群记忆会联动取消
            # member 勾选，若群 finalize 先跑，member 开关已 OFF 使 bucket
            # 被替换成空映射随会话拆除丢弃。放后台跑，settings 保存不被
            # per-group 结算（digest 分批 + 成员并发，仍可达数十秒）拖住。
            self._spawn_group_memory_sync_task(
                self._sync_memory_transitions(
                    settle_members=member_turning_off,
                    group_transition=group_memory_after != group_memory_before,
                    group_enabled_after=group_memory_after,
                )
            )
        # 猫娘动态策略配置
        strategy_mode = kwargs.get("strategy_mode")
        if strategy_mode is not None:
            self.plugin._qq_settings["strategy_mode"] = self.plugin.config_store._normalize_strategy_mode(strategy_mode)
            self.plugin._strategy_mode = self.plugin._qq_settings["strategy_mode"]
            self.plugin._emit_log("INFO", f"策略模式已切换: {self.plugin._strategy_mode}")
        self._enforce_attention_for_dynamic_mode()
        self.plugin._qq_settings.pop("guide_step_settings_done", None)
        self.plugin._ensure_qq_client_initialized()
        # 落盘成功之前，opt-in 对处理链不可见（上面已把它们扣下）。
        success = await self._persist_with_consent_rollback(
            deferred_opt_ins=deferred_opt_ins,
            group_memory_before=group_memory_before,
            group_memory_after=group_memory_after,
            member_memory_before=member_memory_before,
            member_memory_after=member_memory_after,
            cross_group_before=cross_group_before,
            cross_group_after=(
                bool(self.plugin._qq_settings.get("allow_cross_group_context", False))
                if cross_group_before is not None else None
            ),
            participant_memory_before=participant_memory_before,
            participant_memory_after=participant_memory_after,
            participant_markers_created=participant_markers_created,
        )
        if success and participant_settle_needed:
            # Do not race a failed-write rollback against this task: rollback
            # removes the marker/cutoff, while a failed settlement needs both
            # to remain retryable by discard and shutdown flush paths.
            self._spawn_group_memory_sync_task(
                self._settle_participant_sessions_on_disable()
            )
        if deferred_opt_ins:
            if success:
                self._publish_consent_opt_ins(deferred_opt_ins)
            else:
                self.plugin._emit_log(
                    "WARNING",
                    "记忆开关开启未能写盘，已放弃本次开启（运行时保持关闭）",
                )
        if self.plugin.attention_service:
            self.plugin.attention_service.cleanup_stale_cache()
        if success:
            self.plugin._emit_log("INFO", "设置已保存到磁盘" + (" (需重启自动回复以应用新连接)" if self.plugin._running else ""))
        if self.plugin.qq_client:
            self.plugin.qq_client.onebot_url = self.plugin._qq_settings.get("onebot_url", self.plugin.qq_client.onebot_url)
            self.plugin.qq_client.token = self.plugin._qq_settings.get("token", self.plugin.qq_client.token)
        if onebot_url is not None or token is not None or napcat_directory is not None or show_napcat_window is not None or qq_connection_mode is not None or qq_open_app_id is not None or qq_open_client_secret is not None:
            self.plugin._startup_error = None
        return {
            "persisted": success,
            "reconnect_required": bool(self.plugin._running),
        }
