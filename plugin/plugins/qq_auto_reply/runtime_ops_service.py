from __future__ import annotations

import asyncio
from typing import Any

from plugin.sdk.plugin import Err, Ok, SdkError

from .pipeline_models import QQReplyRequest
from .targets import QQAutoReplyValidationError


class QQRuntimeOpsService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def start_auto_reply(self):
        housekeeping = getattr(self.plugin, "_session_housekeeping_task", None)
        if housekeeping is None or housekeeping.done():
            # 交互式 stop 会取消 housekeeping；重启必须把它拉起来，否则
            # idle flush/attention decay 全部停摆直到进程重启。覆盖引用前
            # 先读取旧任务的异常，避免"exception never retrieved"且让
            # 失败可观测。
            if housekeeping is not None and housekeeping.done():
                if housekeeping.cancelled():
                    pass
                else:
                    exc = housekeeping.exception()
                    if exc is not None:
                        self.plugin.logger.error(
                            f"housekeeping 循环曾异常退出: {exc}"
                        )
            # 只登记"需要重建"，真正创建推迟到连接成功之后：连接失败时
            # _running=False 且没有消息任务，后续 stop_auto_reply 走
            # not_running 早退，永远不会取消它——idle flush / attention
            # decay 会在"已停止"状态下继续跑。
            needs_housekeeping = True
        else:
            needs_housekeeping = False
        if self.plugin._running:
            return Ok({"status": "already_running"})
        # 确保连接类型与当前配置一致
        expected = str((self.plugin._qq_settings or {}).get("qq_connection_mode", "napcat") or "napcat").strip()
        is_napcat = expected in ("napcat", "napcat_forward")
        # 优先用 mode 精确比较（napcat 与 napcat_forward 的 needs_attention
        # 相同，启发式分不出 reverse↔forward 的方向切换）；旧客户端/桩没有
        # mode 属性时回落到 needs_attention 启发式，保持历史行为。
        client = self.plugin.qq_client
        if client is not None:
            client_mode = getattr(client, "mode", None)
            if client_mode is not None:
                mismatch = client_mode != expected
            else:
                mismatch = getattr(client, "needs_attention", True) != is_napcat
        else:
            mismatch = False
        if mismatch:
            # 模式不匹配 → 断开旧连接，重建
            try: await self.plugin.qq_client.disconnect()
            except Exception: pass
            self.plugin.qq_client = None
        self.plugin._ensure_qq_client_initialized()
        if not self.plugin.qq_client:
            return Err(SdkError(f"NOT_INITIALIZED: {self.plugin.i18n.t('errors.qq_client_not_initialized', default='QQ 客户端未初始化')}"))
        label = {
            "napcat": "OneBot(反向)",
            "napcat_forward": "OneBot(正向)",
            "open_platform": "QQ 开放平台",
        }.get(expected, "OneBot")
        try:
            self.plugin._emit_log("INFO", f"正在连接 {label}...")
            await self.plugin.qq_client.connect()
            if self.plugin.attention_service and self.plugin.qq_client.needs_attention:
                await self.plugin.attention_service.start_decay_loop()
            self.plugin._emit_log("INFO", "已连接，启动消息处理循环")
            if self.plugin.attention_gate_service:
                await self.plugin.attention_gate_service.start_proactive_loop()
            self.plugin._startup_error = None
            self.plugin._running = True
            getattr(self.plugin, "_spawn_push_ui_event", lambda *a, **k: None)("status")  # 运行状态翻转 → SSE 通知前端
            self.plugin._message_task = asyncio.create_task(self.plugin._process_messages())
            # 连上了才登记这个通道的标识符语义：登记的是「现在跑着的 wire
            # format」，而改配置到重连之间可能隔着任意长的时间。模式在这里
            # 定死传进去（`expected` 就是本次实际用来建连接的那个），不让
            # 协程自己回头再读一次配置。
            #
            # 必须在 `_running = True` 之后、且自带 try：它是一次登记，本身
            # 带退避重试，任何情况下都不该把一次**已经连上**的启动打进
            # except 分支——那会让插件报「启动失败」而连接其实还在。
            try:
                self.plugin.settings_service.ensure_identity_scope_declared(
                    expected,
                )
            except Exception:
                self.plugin.logger.warning("身份作用域登记未能启动", exc_info=True)
            if needs_housekeeping:
                self.plugin._session_housekeeping_task = asyncio.create_task(
                    self.plugin._session_housekeeping_loop()
                )
            # 启动自动回复**不检查 NapCat 链路**：正向拨出由后台 _forward_receive_loop
            # 退避重试，连接状态由状态指示灯/SSE 体现，start 只管把管线拉起来。
            return Ok({"status": "started"})
        except Exception as e:
            self.plugin._emit_log("ERROR", f"启动失败: {e}")
            startup_error = self.plugin.napcat_service.get_startup_error()
            if not startup_error:
                startup_error = str(e)
            self.plugin._startup_error = startup_error
            self.plugin.logger.exception("Failed to start auto reply")
            # 反向 serve 失败等真实异常走这里；正向 connect() 不 raise（后台重试）。
            return Err(SdkError(
                f"START_ERROR: {self.plugin.i18n.t('errors.start_connect_failed', default='反向 WS 服务器已启动 ({url})，但没有 NapCat 客户端连接: {error}', url=self.plugin.qq_client.onebot_url, error=startup_error)}"
            ))

    async def stop_auto_reply(self):
        if not self.plugin._running and not self.plugin._message_task:
            return Ok({"status": "not_running"})
        await self.stop_runtime(stop_napcat=False)
        return Ok({"status": "stopped"})

    async def stop_runtime(self, *, stop_napcat: bool):
        self.plugin._running = False
        getattr(self.plugin, "_spawn_push_ui_event", lambda *a, **k: None)("status")  # 运行状态翻转 → SSE 通知前端
        if self.plugin.attention_service:
            await self.plugin.attention_service.stop_decay_loop()
        if self.plugin.attention_gate_service:
            await self.plugin.attention_gate_service.stop_proactive_loop()
        housekeeping = getattr(self.plugin, "_session_housekeeping_task", None)
        if housekeeping:
            # housekeeping 循环可能正处在 idle finalize 内：先取消并等它
            # 退出，否则下面判定无 straggler、清锁表后，旧 finalizer 会与
            # 重启后的新 handler 各持一把锁并发改写同一会话。被打断的
            # settle 走 fail-closed（缓冲保留、下次重试）。
            housekeeping.cancel()
            try:
                await housekeeping
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                # 循环早先因普通异常死亡：await 会重抛——不能让它跳过
                # 后续的 handler 取消 / 断连 / 锁表决策。
                self.plugin.logger.error(f"housekeeping 循环异常退出: {exc}")
            self.plugin._session_housekeeping_task = None
        if self.plugin._message_task:
            self.plugin._message_task.cancel()
            try:
                await self.plugin._message_task
            except asyncio.CancelledError:
                pass
            self.plugin._message_task = None
        if self.plugin._handler_tasks:
            handler_tasks = list(self.plugin._handler_tasks)
            for task in handler_tasks:
                task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*handler_tasks, return_exceptions=True),
                    timeout=self.plugin._handler_shutdown_timeout_seconds,
                )
            except asyncio.TimeoutError:
                self.plugin.logger.warning(f"Timed out waiting for {len(handler_tasks)} message handler tasks to stop")
            self.plugin._handler_tasks.clear()
        if self.plugin.qq_client:
            await self.plugin.qq_client.disconnect()
        if stop_napcat:
            await self.plugin.napcat_service.stop_managed_napcat()
        # 清锁表前 join 隐私关键的后台任务（开关转变结算 + prompt 变更
        # discard，限 1s）：否则 stop→立刻 start 会给同一会话建新锁，旧
        # 任务与新 handler 并发改写、甚至中途弹掉活跃会话。
        gate = getattr(self.plugin, "attention_gate_service", None)
        buffer_service = getattr(self.plugin, "reply_buffer_service", None)
        buffer_tasks = []
        if buffer_service is not None:
            # 延迟投递不该活过 stop：client 已断开，任务醒来只能发送失败，
            # 或在重启后把停机前的陈旧回复送/并进新运行。显式取消并定局
            # （草稿保持未投递、解除游标屏障、清 pending 表），join 列表
            # 仍等这些任务把 cancellation 走完。
            for key in list(getattr(buffer_service, "_pending", {}) or {}):
                task = buffer_service.cancel_pending(
                    key,
                    (getattr(self.plugin, "_user_sessions", {}) or {}).get(key),
                )
                if task is not None:
                    buffer_tasks.append(task)
        pending_tasks = (
            list(getattr(self.plugin, "_group_memory_sync_tasks", ()) or ())
            + list(getattr(self.plugin, "_prompt_change_discard_tasks", ()) or ())
            + list(getattr(gate, "_digest_tasks", ()) or ())
            + list(getattr(gate, "_retro_tasks", ()) or ())
            + buffer_tasks
        )
        stragglers: set = set()
        if pending_tasks:
            # asyncio.wait 不取消未完成任务（wait_for(gather) 超时会取消，
            # 等于把结算杀在半路）；straggler 继续跑完自己的锁临界区。
            _done, stragglers = await asyncio.wait(pending_tasks, timeout=1.0)
            for finished in _done:
                # 消费异常：不取出的话失败静默（仅事件循环析构时告警）。
                # 已取消的任务 exception() 会抛 CancelledError——先跳过。
                if finished.cancelled():
                    self.plugin.logger.warning("记忆同步任务被外部取消")
                    continue
                exc = finished.exception()
                if exc is not None:
                    self.plugin.logger.error(f"记忆同步任务异常结束: {exc}")
        if stragglers:
            # 有任务仍在锁内：不清锁表——清了之后新 handler 会为同一
            # 会话铸新锁与旧任务并发。留旧表让新旧共用同一把锁。
            self.plugin.logger.warning(
                f"停止时仍有 {len(stragglers)} 个记忆同步任务未完成，"
                f"保留会话锁表以维持隔离"
            )
        else:
            self.plugin._session_locks.clear()


class QQProactiveMessageService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def send_private_message(self, *, target: str, message: str):
        try:
            self.plugin._ensure_qq_client_connected()
            resolved_qq, matched_nickname = self.plugin._resolve_private_message_target(target)
            prompt_message = self.plugin._validate_outbound_message(message)
            permission_level = "admin" if resolved_qq == self.plugin._admin_qq else (self.plugin.permission_mgr.get_permission_level(resolved_qq) if self.plugin.permission_mgr else "trusted")
            if permission_level == "none":
                permission_level = "trusted"
            request = QQReplyRequest(
                message_text=prompt_message,
                sender_id=resolved_qq,
                is_group=False,
                user_nickname=matched_nickname,
                use_memory_context=permission_level == "admin",
                persist_memory=False,
                ephemeral_session=True,
                fallback_to_text_on_voice_failure=False,
                permission_level_override=permission_level,
                force_reply=True,
                source_kind="proactive_private",
            )
            outcome = await self.plugin.reply_pipeline.run(request)
            if not outcome.reply_text:
                return Err(SdkError(f"GENERATE_FAILED: {self.plugin.i18n.t('errors.proactive_private_generate_failed', default='AI 未生成可发送的私聊内容')}"))
            self.plugin.runtime_service.record_pipeline_outcome(source=request.source_kind, request=request, outcome=outcome)
            return Ok({
                "status": "sent",
                "target": str(target or "").strip(),
                "resolved_qq": resolved_qq,
                "resolved_nickname": matched_nickname,
                "message_prompt": prompt_message,
                "generated_message": outcome.reply_text,
                "pipeline_traces": [
                    {
                        "stage": trace.stage,
                        "status": trace.status,
                        "detail": trace.detail,
                        "metadata": trace.metadata,
                    }
                    for trace in outcome.traces
                ],
            })
        except QQAutoReplyValidationError as e:
            code = e.code
            message_text = str(e)
            if code in ("NICKNAME_NOT_FOUND", "NICKNAME_AMBIGUOUS"):
                return Err(SdkError(f"{code}: {message_text}"))
            if code == "INVALID_TARGET":
                return Err(SdkError(f"INVALID_TARGET: {self.plugin.i18n.t('errors.proactive_invalid_target', default=message_text)}"))
            if code == "INVALID_MESSAGE":
                return Err(SdkError(f"INVALID_MESSAGE: {self.plugin.i18n.t('errors.proactive_invalid_message', default=message_text)}"))
            return Err(SdkError(f"INVALID_TARGET: {message_text}"))
        except RuntimeError as e:
            return Err(SdkError(f"NOT_READY: {self.plugin.i18n.t('errors.proactive_not_ready', default='{error}', error=str(e))}"))
        except Exception as e:
            self.plugin.logger.exception("Failed to send proactive private QQ message")
            return Err(SdkError(f"SEND_FAILED: {self.plugin.i18n.t('errors.proactive_send_failed', default='{error}', error=str(e))}"))

    async def send_group_message(self, *, group_id: str, message: str):
        try:
            self.plugin._ensure_qq_client_connected()
            normalized_group_id = self.plugin._validate_group_id(group_id)
            prompt_message = self.plugin._validate_outbound_message(message)
            request = QQReplyRequest(
                message_text=prompt_message,
                sender_id=self.plugin._admin_qq or "0",
                is_group=True,
                group_id=normalized_group_id,
                use_memory_context=False,
                persist_memory=False,
                ephemeral_session=True,
                group_facing=True,
                group_scene_mode="group_collective",
                fallback_to_text_on_voice_failure=False,
                permission_level_override="open",
                force_reply=True,
                source_kind="proactive_group",
            )
            outcome = await self.plugin.reply_pipeline.run(request)
            if not outcome.reply_text:
                return Err(SdkError(f"GENERATE_FAILED: {self.plugin.i18n.t('errors.proactive_group_generate_failed', default='AI 未生成可发送的群聊内容')}"))
            self.plugin.runtime_service.record_pipeline_outcome(source=request.source_kind, request=request, outcome=outcome)
            return Ok({
                "status": "sent",
                "group_id": normalized_group_id,
                "message_prompt": prompt_message,
                "generated_message": outcome.reply_text,
                "pipeline_traces": [
                    {
                        "stage": trace.stage,
                        "status": trace.status,
                        "detail": trace.detail,
                        "metadata": trace.metadata,
                    }
                    for trace in outcome.traces
                ],
            })
        except QQAutoReplyValidationError as e:
            code = e.code
            message_text = str(e)
            if code == "INVALID_GROUP_ID":
                return Err(SdkError(f"INVALID_GROUP_ID: {self.plugin.i18n.t('errors.proactive_invalid_group_id', default=message_text)}"))
            if code == "INVALID_MESSAGE":
                return Err(SdkError(f"INVALID_MESSAGE: {self.plugin.i18n.t('errors.proactive_invalid_message', default=message_text)}"))
            return Err(SdkError(f"INVALID_GROUP_ID: {message_text}"))
        except RuntimeError as e:
            return Err(SdkError(f"NOT_READY: {self.plugin.i18n.t('errors.proactive_not_ready', default='{error}', error=str(e))}"))
        except Exception as e:
            self.plugin.logger.exception("Failed to send proactive group QQ message")
            return Err(SdkError(f"SEND_FAILED: {self.plugin.i18n.t('errors.proactive_send_failed', default='{error}', error=str(e))}"))
