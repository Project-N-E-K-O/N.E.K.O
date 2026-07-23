from __future__ import annotations

import asyncio
from typing import Any, Optional

from utils.llm_client import SystemMessage, create_chat_llm_async
from utils.token_tracker import set_call_type

from .pipeline_models import QQInstructionBundle, QQModelResult, QQPipelineStageTrace, QQReplyContext


class QQReplyGenerationService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def generate_reply_fallback_direct_llm(
        self,
        *,
        context: QQReplyContext,
    ) -> Optional[str]:
        try:
            from utils.config_manager import get_config_manager

            if self.plugin._should_skip_direct_llm_fallback_for_images(message=context.message, attachments=context.attachments):
                self.plugin.logger.warning("QQ 图片消息跳过纯文本 fallback，避免假装已看图")
                return None
            model_config = get_config_manager().get_model_api_config("conversation")
            base_url = str(model_config.get("base_url") or "").strip()
            model = str(model_config.get("model") or "").strip()
            api_key = str(model_config.get("api_key") or "").strip()
            if not base_url or not model:
                self.plugin.logger.warning("Fallback 生成跳过：agent 模型未配置")
                return None
            llm = await create_chat_llm_async(
                model=model,
                base_url=base_url,
                api_key=api_key,
                max_completion_tokens=120,
                timeout=float(self.plugin._ai_turn_timeout_seconds or 60.0) + 0.5,
                provider_type=model_config.get("provider_type"),
            )
            try:
                set_call_type("conversation")
                response = await llm.ainvoke([
                    {"role": "system", "content": self._compose_turn_instructions(context.system_prompt, context.recalled_memory_text)},
                    {"role": "user", "content": context.prompt_message},
                ])
                fallback_reply = getattr(response, "content", "") or ""
                if fallback_reply:
                    self.plugin.logger.info(f"Fallback 直连 LLM 生成成功 (length: {len(fallback_reply)})")
                    return fallback_reply
                self.plugin.logger.warning("Fallback 直连 LLM 未生成内容")
                return None
            finally:
                aclose = getattr(llm, "aclose", None)
                if callable(aclose):
                    try:
                        await aclose()
                    except Exception:
                        pass
        except Exception as e:
            self.plugin.logger.warning(f"Fallback 直连 LLM 生成失败: {e}")
            return None

    async def generate_fallback_from_context(self, context: QQReplyContext) -> Optional[str]:
        return await self.generate_reply_fallback_direct_llm(context=context)

    async def run_primary_session_call(self, context: QQReplyContext) -> QQModelResult:
        session_key = self.plugin.session_runtime_service.build_generation_session_key(context)
        stage_trace = QQPipelineStageTrace(
            stage="model_primary",
            status="started",
            metadata={
                "session_key": session_key,
                "is_group": context.is_group,
                "group_id": str(context.group_id or ""),
                "ephemeral_session": context.ephemeral_session,
                "group_scene_mode": context.group_scene_mode,
            },
        )
        try:
            user_data = await self.plugin.session_bootstrap_service.ensure_generation_session(context, session_key)
            if not user_data:
                stage_trace.status = "no_session"
                return QQModelResult(reply_text=None, source="none", traces=[stage_trace])

            user_session, reply_chunks = self.plugin.session_runtime_service.prime_generation_session_state(
                user_data,
                session_key=session_key,
                context=context,
            )

            ai_reply = await self._run_session_generation(
                context=context,
                session_key=session_key,
                user_data=user_data,
                user_session=user_session,
                reply_chunks=reply_chunks,
            )
            stage_trace.metadata["recalled_memory_used"] = context.recalled_memory_used
            stage_trace.metadata["recalled_memory_length"] = len(context.recalled_memory_text)
            if not ai_reply:
                self.plugin.logger.warning("AI 未生成回复，准备进入 fallback")
                stage_trace.status = "empty"
                stage_trace.metadata["reply_length"] = 0
                return QQModelResult(reply_text=None, source="session", allow_fallback=True, traces=[stage_trace])

            await self._sync_memory_after_success(session_key=session_key, user_data=user_data, context=context)
            self.plugin.logger.info(f"AI 生成回复完成 (会话: {session_key}, length: {len(ai_reply)})")
            stage_trace.status = "success"
            stage_trace.metadata["reply_length"] = len(ai_reply)
            return QQModelResult(reply_text=ai_reply, source="session", traces=[stage_trace])

        except asyncio.TimeoutError:
            self.plugin.logger.warning(f"会话 {session_key} 处理超时，关闭并丢弃该会话")
            await self.plugin.session_runtime_service.discard_session(session_key, reason="generation_timeout")
            stage_trace.status = "timeout"
            return QQModelResult(reply_text=None, source="session", timed_out=True, traces=[stage_trace])
        except Exception as e:
            self.plugin.logger.exception(f"AI 生成回复失败: {e}")
            stage_trace.status = "error"
            stage_trace.detail = str(e)
            return QQModelResult(reply_text=None, source="none", traces=[stage_trace])
        finally:
            if context.ephemeral_session:
                await self.plugin.session_runtime_service.discard_session(session_key, reason="ephemeral_cleanup")

    def _compose_turn_instructions(self, system_prompt: str, recalled_memory_text: str) -> str:
        return "\n\n".join(part for part in [system_prompt, recalled_memory_text] if part)

    async def _run_session_generation(
        self,
        *,
        context: QQReplyContext,
        session_key: str,
        user_data: dict[str, Any],
        user_session: Any,
        reply_chunks: list[str],
    ) -> str | None:
        async with user_data["lock"]:
            reply_chunks.clear()

            queued_images = await self.plugin._queue_attachment_images(user_session, context.attachments)
            self.plugin.logger.info(f"发送消息到 AI (会话: {session_key}, length: {len(context.prompt_message)}, images: {queued_images})")
            restore_session_prompt = self._apply_turn_memory_context(user_session, context.system_prompt, context.recalled_memory_text)
            try:
                await asyncio.wait_for(
                    user_session.stream_text(context.prompt_message),
                    timeout=self.plugin._ai_turn_timeout_seconds,
                )

                completed = await self.plugin._wait_session_response_complete(user_session)
                if not completed:
                    self.plugin.logger.warning(f"会话 {session_key} 响应超时，关闭并丢弃该会话")
                    await self.plugin.session_runtime_service.discard_session(session_key, reason="session_timeout")
                    raise asyncio.TimeoutError
            finally:
                restore_session_prompt()

            return "".join(reply_chunks)

    def _apply_turn_memory_context(self, user_session: Any, system_prompt: str, recalled_memory_text: str):
        if not recalled_memory_text:
            return lambda: None
        conversation_history = getattr(user_session, "_conversation_history", None)
        if not conversation_history or not isinstance(conversation_history[0], SystemMessage):
            return lambda: None
        original_system_message = conversation_history[0]
        original_instructions = getattr(user_session, "_instructions", original_system_message.content)
        enhanced_instructions = self._compose_turn_instructions(system_prompt, recalled_memory_text)
        conversation_history[0] = SystemMessage(content=enhanced_instructions)
        user_session._instructions = enhanced_instructions

        def restore() -> None:
            current_history = getattr(user_session, "_conversation_history", None)
            if current_history and current_history[0] is not original_system_message:
                current_history[0] = original_system_message
            user_session._instructions = original_instructions

        return restore

    async def _sync_memory_after_success(
        self,
        *,
        session_key: str,
        user_data: dict[str, Any],
        context: QQReplyContext,
    ) -> None:
        if user_data.get("memory_enabled"):
            try:
                self.plugin.session_memory_service.record_group_member_turn(
                    user_data, context,
                )
                count = await self.plugin._cache_session_delta(session_key, user_data)
                if count:
                    self.plugin.logger.info(f"[管理员] 成功同步 {count} 条消息到 Memory Server (会话: {session_key})")
            except Exception as e:
                self.plugin.logger.error(f"记忆同步失败: {e}")
            return

        if user_data.get("memory_context_used"):
            self.plugin.logger.info(f"[临时发送] 已使用记忆上下文但跳过记忆同步 (会话: {session_key})")
            return
        if context.is_group:
            self.plugin.logger.info(f"[群聊] 跳过记忆同步 (群: {context.group_id}, 用户: {context.sender_id})")
            return
        self.plugin.logger.info(f"[非管理员] 跳过记忆同步 (用户: {context.sender_id}, 权限: {context.permission_level})")

    async def generate_from_context(self, context: QQReplyContext) -> QQModelResult:
        if not context.is_group and context.permission_level not in ["admin", "trusted"]:
            return QQModelResult(reply_text=None, source="none")

        primary_result = await self.run_primary_session_call(context)
        if not primary_result.allow_fallback:
            return primary_result

        fallback_reply = await self.generate_fallback_from_context(context)
        if fallback_reply:
            primary_result.traces.append(
                QQPipelineStageTrace(
                    stage="model_fallback",
                    status="success",
                    metadata={"reply_length": len(fallback_reply), "group_scene_mode": context.group_scene_mode},
                )
            )
            return QQModelResult(reply_text=fallback_reply, source="direct_llm_fallback", used_fallback=True, traces=primary_result.traces)
        primary_result.traces.append(
            QQPipelineStageTrace(
                stage="model_fallback",
                status="empty",
                metadata={"reply_length": 0, "group_scene_mode": context.group_scene_mode},
            )
        )
        return QQModelResult(reply_text=None, source="none", used_fallback=True, traces=primary_result.traces)

    async def generate_reply(
        self,
        message: str,
        permission_level: str,
        sender_id: str,
        attachments: list[dict[str, Any]] | None = None,
        is_group: bool = False,
        group_id: str = None,
        user_nickname: Optional[str] = None,
        use_memory_context: Optional[bool] = None,
        persist_memory: Optional[bool] = None,
        ephemeral_session: bool = False,
        group_facing: bool = False,
        group_scene_mode: str = "",
    ) -> Optional[str]:
        context = await self.plugin.reply_context_node.build(
            message=message,
            permission_level=permission_level,
            sender_id=sender_id,
            attachments=attachments,
            is_group=is_group,
            group_id=group_id,
            user_nickname=user_nickname,
            use_memory_context=use_memory_context,
            persist_memory=persist_memory,
            ephemeral_session=ephemeral_session,
            group_facing=group_facing,
            group_scene_mode=group_scene_mode,
        )
        model_result = await self.generate_from_context(context)
        outcome = self.plugin.reply_postprocess_node.finalize(context, model_result)
        return outcome.reply_text
