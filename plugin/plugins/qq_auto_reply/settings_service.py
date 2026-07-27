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

    def _rollback_unpersisted_memory_toggles(
        self, persisted: bool, *,
        group_memory_before: bool, group_memory_after: bool,
        member_memory_before: bool, member_memory_after: bool,
    ) -> None:
        """落盘失败时回滚记忆 consent 开关：重启会回到旧值，运行时若继续
        按新值收集，等于在"未成功保存的授权"下入库。回滚运行时政策并按
        反向转变重新盖章+结算（与用户手动切回等价，标记模型天然支持连续
        切换）。member 单独回滚：OFF 回滚（开失败）下新收集的活 bucket 在
        finalize 被空映射替换、按 fail-closed 丢弃；ON 回滚（关失败）已
        分离的快照由结算任务照常入库。"""
        if persisted:
            return
        if group_memory_before != group_memory_after:
            self.plugin._qq_settings["group_memory_enabled"] = group_memory_before
            self.plugin._qq_settings["group_member_memory_enabled"] = member_memory_before
            self._stamp_group_memory_transition(enabled_after=group_memory_before)
            self._spawn_group_memory_sync_task(
                self._sync_memory_transitions(
                    settle_members=False,
                    group_transition=True,
                    group_enabled_after=group_memory_before,
                )
            )
            self.plugin._emit_log(
                "WARNING",
                "群记忆开关变更未能写盘，已回滚运行时策略（保持磁盘与内存一致）",
            )
        elif member_memory_before != member_memory_after:
            self.plugin._qq_settings["group_member_memory_enabled"] = member_memory_before
            self.plugin._emit_log(
                "WARNING",
                "成员记忆开关变更未能写盘，已回滚运行时策略",
            )

    async def _sync_memory_transitions(
        self, *, settle_members: bool, group_transition: bool,
        group_enabled_after: bool,
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
                )

    def _spawn_group_memory_sync_task(self, coro) -> None:
        """Run a privacy-critical session-sync coroutine in the background.

        The event loop holds tasks weakly; without a strong reference the
        settle/cleanup could be garbage-collected mid-flight."""
        task = asyncio.create_task(coro)
        sync_tasks = getattr(self.plugin, "_group_memory_sync_tasks", None)
        if sync_tasks is None:
            sync_tasks = set()
            self.plugin._group_memory_sync_tasks = sync_tasks
        sync_tasks.add(task)
        task.add_done_callback(sync_tasks.discard)

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

    async def persist_business_config(self) -> bool:
        try:
            self.plugin._qq_settings["trusted_users"] = self.plugin.permission_mgr.list_users() if self.plugin.permission_mgr else []
            self.plugin._qq_settings["trusted_groups"] = self.plugin.group_permission_mgr.list_groups() if self.plugin.group_permission_mgr else []
            self.plugin._qq_settings = await self.plugin.config_store.save(self.plugin._qq_settings)
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
        for key in (
            "group_memory_enabled",
            "group_member_memory_enabled",
            "allow_cross_group_context",
        ):
            value = kwargs.get(key)
            if value is not None:
                self.plugin._qq_settings[key] = bool(value)
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
                    fresh_buckets = ud.pop("group_member_memory_messages")
                    fresh_labels = ud.pop("group_member_memory_labels", {})
                    pending = ud.setdefault("pending_settle_buckets", {})
                    for sender, msgs in fresh_buckets.items():
                        # OFF→ON→OFF 连续切换时旧快照可能还没被结算：合并
                        # 而非覆盖，先前授权的轮次不得被孤儿化。
                        pending.setdefault(sender, []).extend(msgs)
                    ud.setdefault("pending_settle_labels", {}).update(fresh_labels)
                    ud["pending_member_settle"] = True
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
        success = await self.persist_business_config()
        self._rollback_unpersisted_memory_toggles(
            success,
            group_memory_before=group_memory_before,
            group_memory_after=group_memory_after,
            member_memory_before=member_memory_before,
            member_memory_after=member_memory_after,
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
