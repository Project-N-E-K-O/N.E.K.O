from __future__ import annotations

from typing import Any, Optional

from utils.config_manager import get_config_manager

from .pipeline_models import is_synthetic_source, QQInstructionBundle, QQPipelineStageTrace, QQReplyContext


class QQReplyContextNode:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def _strip_section_if_member_revoked(
        self, system_prompt: str, section_text: str, used_member_subject: bool,
    ) -> tuple[str, bool]:
        """Drop a participant-derived section when member consent is gone.

        The scoped bootstrap section is composed before the recall/login
        awaits; a member opt-out during them must not leave participant-
        derived text in the prompt. Returns the prompt and whether the
        section survived."""
        if not section_text or not used_member_subject:
            return system_prompt, bool(section_text)
        if bool(
            (getattr(self.plugin, "_qq_settings", {}) or {}).get(
                "group_member_memory_enabled", False,
            )
        ):
            return system_prompt, True
        separator = "\n\n"
        for candidate in (
            separator + section_text,
            section_text + separator,
            section_text,
        ):
            if candidate in system_prompt:
                system_prompt = system_prompt.replace(candidate, "", 1)
                break
        self.plugin.logger.info("成员记忆已关闭，核心记忆段在生成前撤除")
        return system_prompt, False

    def _strip_participant_if_revoked(
        self, system_prompt: str, section_text: str, participant_memory: bool,
    ) -> tuple[str, bool]:
        """Drop the participant-derived core section when consent is gone.

        对偶 _strip_section_if_member_revoked：私聊 participant 轮的核心
        记忆段在 login/bootstrap/recall 的 await 窗口里组好，期间开关被
        关掉/回滚时不得把 participant 域内容留在 prompt 里。返回
        (prompt, section_kept)。"""
        if not section_text or not participant_memory:
            return system_prompt, bool(section_text)
        if bool(
            (getattr(self.plugin, "_qq_settings", {}) or {}).get(
                "private_participant_memory_enabled", False,
            )
        ):
            return system_prompt, True
        separator = "\n\n"
        for candidate in (
            separator + section_text,
            section_text + separator,
            section_text,
        ):
            if candidate in system_prompt:
                system_prompt = system_prompt.replace(candidate, "", 1)
                break
        self.plugin.logger.info("私聊成员记忆已关闭，核心记忆段在生成前撤除")
        return system_prompt, False

    def _strip_cross_group_if_revoked(
        self, system_prompt: str, cross_group_section: str,
    ) -> tuple[str, bool]:
        """Returns (prompt, section_kept).

        The caller needs the second value: judging "does this reply depend
        on cross-group consent" from the bundle alone marks a reply that
        never saw the section as cross-group-derived, and a later opt-out
        then discards it for nothing. One judgement, one place.

        The section is composed before the login/bootstrap/recall awaits;
        the switch can be turned off — or rolled back after a failed
        settings write — during them. Generating with the stale prompt
        would expose other groups' content under consent that is not in
        effect."""
        if not cross_group_section:
            return system_prompt, False
        if bool(
            (getattr(self.plugin, "_qq_settings", {}) or {}).get(
                "allow_cross_group_context", False,
            )
        ):
            return system_prompt, True
        separator = "\n\n"
        for candidate in (
            separator + cross_group_section,
            cross_group_section + separator,
            cross_group_section,
        ):
            if candidate in system_prompt:
                system_prompt = system_prompt.replace(candidate, "", 1)
                break
        self.plugin.logger.info("跨群上下文已在生成前撤除（授权已关闭）")
        return system_prompt, False

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
        is_reply_to_bot: bool = False,
        quoted_message_id: str = "",
        mentions_other_user: bool = False,
        mentions_all: bool = False,
        reply_context: str = "",
        force_reply: bool = False,
        source_kind: str = "",
        member_memory_at_receipt: bool | None = None,
        group_speaker_permission_level_at_receipt: str | None = None,
        speaker_channel_at_receipt: str | None = None,
        participant_memory_at_receipt: bool | None = None,
        private_permission_level_at_receipt: str | None = None,
        inherited_consent_snapshot: dict[str, bool] | None = None,
    ) -> QQReplyContext:
        # member 记忆 consent 快照优先取消息接收边界（process_messages 在
        # task 创建前盖章——handler 排队期间 OFF→ON 不得让收到时无授权的
        # 发言被收集）；旁路调用者无消息级快照时至少在 build 第一个 await
        # 之前定格（login/bootstrap/recall 网络调用期间的切换同理）。
        if member_memory_at_receipt is None:
            member_memory_at_receipt = (
                getattr(self.plugin, "_qq_settings", {}) or {}
            ).get("group_member_memory_enabled", False)
        member_memory_snapshot = bool(is_group and member_memory_at_receipt)
        # 私聊 participant 记忆快照（对偶 member）：只对非 admin 私聊轮
        # 生效——admin 私聊仍是 legacy 主人语料，群轮走上面那套。
        if participant_memory_at_receipt is None:
            participant_memory_at_receipt = (
                getattr(self.plugin, "_qq_settings", {}) or {}
            ).get("private_participant_memory_enabled", False)
        receipt_permission = (
            private_permission_level_at_receipt
            if private_permission_level_at_receipt is not None
            else permission_level
        )
        private_memory_mode = None
        if not is_group:
            if receipt_permission == "admin":
                private_memory_mode = "legacy"
            elif participant_memory_at_receipt:
                private_memory_mode = "participant"
        participant_memory_snapshot = private_memory_mode == "participant"
        if (
            not is_group
            and use_memory_context is None
        ):
            # 主路径（dispatcher）不显式传 use/persist：接收边界章在此定格
            # 成显式请求值，prompt_builder 的 None 分支只服务旁路调用者
            # （它读实时配置，与群路径的 None 语义对偶）。
            use_memory_context = private_memory_mode is not None
        # 合成轮（rapid-fire/proactive/buffer 合并）复用首个 pending sender，
        # 但缓冲内容可能混有其他成员的发言——记忆读路径只授权群 subject，
        # 不得注入"名义 sender"的成员记忆（写侧已同样过滤）。
        # member 开关同时门控读：关闭时不得把既有成员记忆召回进群回复，
        # 否则"停止使用成员记忆"只停了写。sender_id 统一 strip：写侧
        # （record_group_member_turn）也 strip，不规范化会让读写落进不同
        # 的 participant subject 桶。
        memory_sender_id = (
            ""
            if (
                is_synthetic_source(source_kind)
                or (is_group and not member_memory_snapshot)
            )
            else str(sender_id or "").strip()
        )
        traces: list[QQPipelineStageTrace] = []
        config_manager = get_config_manager()

        # 登录身份提前取：下面的缓存预检要用它比对（本来就要为 instruction bundle
        # fetch 一次，提前不多花网络调用）。
        login_status, login_self_id, login_nickname = self.plugin._normalize_login_identity(
            await self.plugin._fetch_login_status_payload()
        )

        # context 里的人格（角色名 / character card / system prompt）会随会话一起
        # 冻进 OmniOfflineClient 缓存整场，而下游 ensure_generation_session 只在
        # 新建会话时才等区域落定——等待发生在 context 组装**之后**，等待期间用户
        # 切换角色的话，冻结的就是切换前的人格。所以组装 context 前先落定一次。
        # 只在「将要新建会话」时等：探测循环终身退避重试，长寿会话存在期间
        # in-flight 窗口会反复出现，无条件等会让缓存会话的每条消息都白付最多
        # 1.5s。session key 只由 sender/group/ephemeral 决定（见
        # build_generation_session_key），用入参即可预判；ephemeral 每次都是新
        # 会话，必等。命中的条目还要**登录身份一致**才算数：身份不匹配的条目会被
        # ensure_generation_session 丢弃重建，那条路径实际是「新会话」，跳过等待
        # 会让替换会话用上等待前组装的旧人格。fail-open：与其它插件路径对偶，
        # 探测出错不阻塞回复。
        session_cached = False
        if not ephemeral_session:
            try:
                key = self.plugin._build_session_key(
                    sender_id=sender_id, is_group=is_group, group_id=group_id,
                )
                entry = getattr(self.plugin, "_user_sessions", {}).get(key)
                # 与 ensure_generation_session 共用同一个判据：重建触发不止
                # 登录身份（还有角色切换与抢救失败的粘性标记），只认身份的
                # 预判会对那两类轮次误报「命中缓存」，等待被跳过、人格在等
                # 待窗口里被切换后仍冻进新会话。
                from .session_bootstrap_service import (
                    generation_session_is_reusable,
                )
                # 线路指纹与 bootstrap 的重建触发保持同一组判据（helper
                # docstring 的硬约束）。此处读的是**落定前**的配置快照：
                # 免费线路的区域改写可能让它与会话存的指纹假错配，方向是
                # 安全的——多预测一次"要重建"只是多等一次区域落定。
                _prediction_route = None
                try:
                    _prediction_config = config_manager.get_model_api_config(
                        "conversation",
                    )
                    _prediction_route = (
                        str(_prediction_config.get("base_url") or ""),
                        str(_prediction_config.get("model") or ""),
                    )
                except Exception:
                    _prediction_route = None
                session_cached = generation_session_is_reusable(
                    entry,
                    login_self_id=login_self_id,
                    her_name=config_manager.get_character_data()[1],
                    conversation_route=_prediction_route,
                    is_group=is_group,
                    private_memory_mode=private_memory_mode,
                    permission_level=permission_level,
                )
            except Exception:
                session_cached = False
        if not session_cached:
            try:
                await config_manager.aensure_region_resolved()
            except Exception as _geo_err:
                self.plugin.logger.warning(f"[GeoIP] 区域落定失败，按当前配置组装上下文: {_geo_err}")

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
            is_group=is_group,
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

        # login 身份已在函数开头 fetch（缓存预检要用），此处只记 trace
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
            memory_sender_id=memory_sender_id,
            user_title=user_title,
            is_group=is_group,
            group_id=group_id,
            use_memory_context=should_use_memory_context,
            participant_memory=participant_memory_snapshot,
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
        # 召回只有 recall_memory 工具这一条通道：generation service 按轮挂
        # 工具，由模型自己决定这轮要不要查。构建期一次检索都不发——上下文
        # 构建拿不到"这轮到底需不需要记忆"的信息，在这里查就是每轮无条件付
        # 一次检索（HTTP + prompt token），无论结果用不用得上。
        # recalled_memory_text / used 留空由 execute_recall 在真的读到内容
        # 时回填（主会话空回复时的 direct fallback 读它）。
        traces.append(
            QQPipelineStageTrace(
                stage="context_memory_recall",
                status=(
                    "tool_deferred" if should_use_memory_context else "skipped"
                ),
                metadata={
                    "recalled_memory_used": False,
                    "recalled_memory_length": 0,
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
        # 引用上下文仅注入 LLM prompt，不污染会话历史
        prompt_text = message
        if reply_context:
            prompt_text = reply_context + "\n" + prompt_text
        prompt_message = self.plugin._build_prompt_message(
            is_group=is_group,
            group_facing=effective_group_facing,
            group_scene_mode=effective_group_scene_mode,
            user_title=user_title,
            sender_id=sender_id,
            group_id=group_id,
            message=prompt_text,
            current_message_id=current_message_id,
            is_reply_to_bot=is_reply_to_bot,
            quoted_message_id=quoted_message_id,
            mentions_other_user=mentions_other_user,
            mentions_all=mentions_all,
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

        system_prompt, core_memory_alive = self._strip_section_if_member_revoked(
            system_prompt,
            core_memory_text,
            bool(getattr(instruction_bundle, "used_member_subject", False)),
        )
        if not core_memory_alive:
            core_memory_text = ""
            memory_context_used = False
        if core_memory_text:
            system_prompt, core_memory_alive = self._strip_participant_if_revoked(
                system_prompt, core_memory_text, participant_memory_snapshot,
            )
            if not core_memory_alive:
                core_memory_text = ""
                memory_context_used = False
        system_prompt, cross_group_alive = self._strip_cross_group_if_revoked(
            system_prompt,
            getattr(instruction_bundle, "cross_group_section", ""),
        )
        system_prompt, cross_session_alive = self._strip_cross_group_if_revoked(
            system_prompt,
            getattr(instruction_bundle, "cross_session_section", ""),
        )
        self.plugin._emit_log("INFO", f"[UserMsg] (system {len(system_prompt)}字) {prompt_message[:200]}")

        return QQReplyContext(
            consent_snapshot=dict(inherited_consent_snapshot or {}) or None,
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
            recalled_memory_text="",
            recalled_memory_used=False,
            login_status=login_status,
            login_self_id=login_self_id,
            login_nickname=login_nickname,
            current_message_id=current_message_id,
            force_reply=force_reply,
            source_kind=source_kind,
            member_memory_enabled=member_memory_snapshot,
            group_speaker_permission_level_at_receipt=(
                group_speaker_permission_level_at_receipt
            ),
            speaker_channel_at_receipt=speaker_channel_at_receipt,
            participant_memory_enabled=participant_memory_snapshot,
            private_memory_mode=private_memory_mode,
            private_permission_level_at_receipt=(
                private_permission_level_at_receipt
            ),
            cross_group_section=(
                getattr(instruction_bundle, "cross_group_section", "")
                if cross_group_alive else ""
            ),
            cross_session_section=(
                getattr(instruction_bundle, "cross_session_section", "")
                if cross_session_alive else ""
            ),
            # 只剩 bootstrap 段这一个构建期来源：召回侧的 participant 域是
            # 生成中途才可能读到的，由 execute_recall 命中 member 域时置位。
            used_member_subject=bool(
                core_memory_alive
                and getattr(instruction_bundle, "used_member_subject", False)
            ),
            traces=traces,
        )
