from __future__ import annotations

from typing import Any, Optional

from utils.config_manager import get_config_manager

from .pipeline_models import QQInstructionBundle, QQPipelineStageTrace, QQReplyContext
from .prompt_fragment_templates import LONG_TERM_MEMORY_SECTION


class QQReplyContextNode:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def _build_recalled_memory_text(
        self,
        *,
        her_name: str,
        message: str,
        should_use_memory_context: bool,
        attachments: list[dict[str, Any]] | None,
        is_group: bool = False,
        group_id: str | None = None,
        sender_id: str = "",
    ) -> str:
        if not should_use_memory_context:
            return ""
        if self.plugin._should_skip_direct_llm_fallback_for_images(message=message, attachments=attachments):
            return ""
        normalized_message = str(message or "").strip()
        if not normalized_message:
            return ""
        try:
            subjects = None
            if is_group and group_id:
                subjects = [
                    self.plugin.memory_bridge.group_subject(group_id),
                    self.plugin.memory_bridge.group_participant_subject(
                        group_id, sender_id,
                    ),
                ]
            recall_result = await self.plugin.memory_bridge.query_relevant_memory(
                her_name,
                normalized_message,
                subjects=subjects,
            )
            if not recall_result.text:
                return ""
            self.plugin.logger.info(
                "QQ 长期记忆召回完成: hits=%s elapsed=%.0fms",
                recall_result.hit_count,
                recall_result.elapsed_ms,
            )
            return LONG_TERM_MEMORY_SECTION.format(memory_context=recall_result.text)
        except Exception as e:
            self.plugin.logger.warning(f"QQ 长期记忆召回失败: {e}")
            return ""

    async def build(
        self,
        *,
        message: str,
        permission_level: str,
        sender_id: str,
        attachments: list[dict[str, Any]] | None = None,
        is_group: bool = False,
        group_id: str | None = None,
        user_nickname: Optional[str] = None,
        use_memory_context: Optional[bool] = None,
        persist_memory: Optional[bool] = None,
        ephemeral_session: bool = False,
        group_facing: bool = False,
        group_scene_mode: str = "",
        current_message_id: str = "",
        force_reply: bool = False,
    ) -> QQReplyContext:
        traces: list[QQPipelineStageTrace] = []
        config_manager = get_config_manager()
        master_name, her_name, _, catgirl_data, _, lanlan_prompt_map, _, _, _ = config_manager.get_character_data()
        traces.append(
            QQPipelineStageTrace(
                stage="context_character",
                status="loaded",
                metadata={
                    "master_name": master_name,
                    "her_name": her_name,
                },
            )
        )

        custom_nickname = self.plugin.permission_mgr.get_nickname(sender_id) if self.plugin.permission_mgr else None
        # 开放平台：username 为空时，管理员用主人名，普通用户用友好称呼
        if self.plugin.qq_client and not getattr(self.plugin.qq_client, 'needs_attention', True):
            if permission_level == "admin":
                user_nickname = master_name  # 管理员就是主人本人
            elif not user_nickname and not custom_nickname:
                user_nickname = "用户"
        user_title = self.plugin._build_user_title(
            permission_level=permission_level,
            sender_id=sender_id,
            master_name=master_name,
            custom_nickname=custom_nickname,
            user_nickname=user_nickname,
            is_group=is_group,
        )
        traces.append(
            QQPipelineStageTrace(
                stage="context_identity",
                status="resolved",
                metadata={
                    "sender_id": sender_id,
                    "user_title": user_title,
                    "custom_nickname": custom_nickname or "",
                    "user_nickname": user_nickname or "",
                },
            )
        )

        current_character = catgirl_data.get(her_name, {})
        character_prompt = lanlan_prompt_map.get(her_name, self.plugin.i18n.t("prompts.default_ai_assistant", default="你是一个友好的AI助手"))
        character_card_fields = self.plugin._build_character_card_fields(current_character)
        traces.append(
            QQPipelineStageTrace(
                stage="context_character_card",
                status="built",
                metadata={
                    "field_count": len(character_card_fields),
                },
            )
        )

        should_use_memory_context = self.plugin._should_use_memory_context(
            is_group=is_group,
            permission_level=permission_level,
            requested=use_memory_context,
        )
        should_persist_memory = self.plugin._should_persist_memory(
            should_use_memory_context=should_use_memory_context,
            requested=persist_memory,
        )
        traces.append(
            QQPipelineStageTrace(
                stage="context_memory_policy",
                status="resolved",
                metadata={
                    "use_memory_context": should_use_memory_context,
                    "persist_memory": should_persist_memory,
                },
            )
        )

        login_status, login_self_id, login_nickname = self.plugin._normalize_login_identity(await self.plugin._fetch_login_status_payload())
        traces.append(
            QQPipelineStageTrace(
                stage="context_login_identity",
                status="resolved",
                metadata={
                    "login_status": login_status,
                    "login_self_id": login_self_id or "",
                    "login_nickname": login_nickname or "",
                },
            )
        )
        effective_group_scene_mode = group_scene_mode or ("group_collective" if group_facing else ("shared_context" if is_group else ""))
        address_user_by_name = effective_group_scene_mode == "directed_user"
        shared_group_session = is_group and effective_group_scene_mode == "shared_context"
        effective_group_facing = group_facing or effective_group_scene_mode == "group_collective"
        instruction_bundle = await self.plugin._build_qq_session_instructions(
            her_name=her_name,
            master_name=master_name,
            character_prompt=character_prompt,
            character_card_fields=character_card_fields,
            permission_level=permission_level,
            sender_id=sender_id,
            user_title=user_title,
            is_group=is_group,
            group_id=group_id,
            use_memory_context=should_use_memory_context,
            address_user_by_name=address_user_by_name,
            group_facing=effective_group_facing,
            shared_group_session=shared_group_session,
            group_scene_mode=effective_group_scene_mode,
            login_status=login_status,
            login_self_id=login_self_id,
            login_nickname=login_nickname,
        )
        system_prompt = instruction_bundle.system_prompt
        core_memory_text = instruction_bundle.core_memory_text
        memory_context_used = instruction_bundle.memory_context_used
        recalled_memory_text = await self._build_recalled_memory_text(
            her_name=her_name,
            message=message,
            should_use_memory_context=should_use_memory_context,
            attachments=attachments,
            is_group=is_group,
            group_id=group_id,
            sender_id=sender_id,
        )
        recalled_memory_used = bool(recalled_memory_text)
        traces.append(
            QQPipelineStageTrace(
                stage="context_memory_recall",
                status="used" if recalled_memory_used else "skipped",
                metadata={
                    "recalled_memory_used": recalled_memory_used,
                    "recalled_memory_length": len(recalled_memory_text),
                },
            )
        )
        traces.append(
            QQPipelineStageTrace(
                stage="context_prompt_sections",
                status="built",
                metadata={
                    "system_prompt_length": len(system_prompt),
                    "memory_context_used": memory_context_used,
                    "core_memory_length": len(core_memory_text),
                    "scene_mode": instruction_bundle.scene_mode,
                    "group_scene_mode": effective_group_scene_mode,
                },
            )
        )
        prompt_message = self.plugin._build_prompt_message(
            is_group=is_group,
            group_facing=effective_group_facing,
            group_scene_mode=effective_group_scene_mode,
            user_title=user_title,
            sender_id=sender_id,
            group_id=group_id,
            message=message,
            current_message_id=current_message_id,
        )
        traces.append(
            QQPipelineStageTrace(
                stage="context_prompt_message",
                status="built",
                metadata={
                    "prompt_message_length": len(prompt_message),
                    "group_facing": effective_group_facing,
                    "group_scene_mode": effective_group_scene_mode,
                    "is_group": is_group,
                },
            )
        )

        self.plugin._emit_log("INFO", f"[UserMsg] (system {len(system_prompt)}字) {prompt_message[:200]}")

        return QQReplyContext(
            message=message,
            attachments=attachments,
            permission_level=permission_level,
            sender_id=sender_id,
            is_group=is_group,
            group_id=group_id,
            user_nickname=user_nickname,
            use_memory_context=should_use_memory_context,
            persist_memory=should_persist_memory,
            ephemeral_session=ephemeral_session,
            group_facing=effective_group_facing,
            group_scene_mode=effective_group_scene_mode,
            scene_mode=instruction_bundle.scene_mode,
            master_name=master_name,
            her_name=her_name,
            user_title=user_title,
            character_prompt=character_prompt,
            character_card_fields=character_card_fields,
            prompt_message=prompt_message,
            system_prompt=system_prompt,
            memory_context_used=memory_context_used,
            core_memory_text=core_memory_text,
            recalled_memory_text=recalled_memory_text,
            recalled_memory_used=recalled_memory_used,
            login_status=login_status,
            login_self_id=login_self_id,
            login_nickname=login_nickname,
            current_message_id=current_message_id,
            force_reply=force_reply,
            traces=traces,
        )
