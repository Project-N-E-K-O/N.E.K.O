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
        if self.plugin._running:
            return Ok({"status": "already_running"})
        # 确保连接类型与当前配置一致
        expected = str((self.plugin._qq_settings or {}).get("qq_connection_mode", "napcat") or "napcat").strip()
        is_napcat = expected == "napcat"
        if self.plugin.qq_client and getattr(self.plugin.qq_client, 'needs_attention', True) != is_napcat:
            # 模式不匹配 → 断开旧连接，重建
            try: await self.plugin.qq_client.disconnect()
            except Exception: pass
            self.plugin.qq_client = None
        self.plugin._ensure_qq_client_initialized()
        if not self.plugin.qq_client:
            return Err(SdkError(f"NOT_INITIALIZED: {self.plugin.i18n.t('errors.qq_client_not_initialized', default='QQ 客户端未初始化')}"))
        try:
            self.plugin._emit_log("INFO", f"正在连接 {'NapCat' if is_napcat else 'QQ 开放平台'}...")
            await self.plugin.qq_client.connect()
            if self.plugin.attention_service and self.plugin.qq_client.needs_attention:
                await self.plugin.attention_service.start_decay_loop()
            self.plugin._emit_log("INFO", "已连接，启动消息处理循环")
            if self.plugin.attention_gate_service:
                await self.plugin.attention_gate_service.start_proactive_loop()
            self.plugin._startup_error = None
            self.plugin._running = True
            self.plugin._message_task = asyncio.create_task(self.plugin._process_messages())
            return Ok({"status": "started"})
        except Exception as e:
            self.plugin._emit_log("ERROR", f"启动失败: {e}")
            startup_error = self.plugin.napcat_service.get_startup_error()
            if not startup_error:
                startup_error = str(e)
            self.plugin._startup_error = startup_error
            self.plugin.logger.exception("Failed to start auto reply")
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
        pending_tasks = (
            list(getattr(self.plugin, "_group_memory_sync_tasks", ()) or ())
            + list(getattr(self.plugin, "_prompt_change_discard_tasks", ()) or ())
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
