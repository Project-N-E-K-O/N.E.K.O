from __future__ import annotations

import asyncio
import re
from typing import Any

from .pipeline_models import QQDeliveryResult, QQModelResult, QQPipelineStageTrace, QQRelayResult, QQReplyContext, QQReplyDecision, QQReplyOutcome, QQReplyRequest, delivered_blocks_text
from .reply_buffer_service import QQReplyBufferService


class QQReplyPipelineRunner:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def run(self, request: QQReplyRequest) -> QQReplyOutcome:
        # 群聊消息计数器（用于表情包间隔控制）
        if request.is_group:
            gid = str(request.group_id or "")
            self.plugin._sticker_since[gid] = (self.plugin._sticker_since.get(gid) or 0) + 1
        decision = self._run_decision(request)
        decision_trace = QQPipelineStageTrace(
            stage="decision",
            status=decision.action,
            metadata={
                "permission_level": decision.permission_level,
                "is_group": request.is_group,
                "group_id": str(request.group_id or ""),
                "sender_id": request.sender_id,
                "group_scene_mode": request.group_scene_mode,
                "suppression_reason": request.suppression_reason,
                "quoted_message_id": request.quoted_message_id,
                "mentioned_user_ids": list(request.mentioned_user_ids or []),
                "attention_enabled": decision.attention_enabled,
                "attention_score": decision.attention_score,
                "attention_focus_group_id": decision.attention_focus_group_id,
                "attention_focus_score": decision.attention_focus_score,
                "attention_multiplier": decision.attention_multiplier,
                "attention_gate_reason": decision.attention_gate_reason,
            },
        )
        if decision.action == "ignore":
            return QQReplyOutcome(action="ignore", traces=[decision_trace])
        if decision.action == "relay":
            return await self._run_relay(request, decision, decision_trace)

        context = await self._run_context(request, decision)
        model_result = await self._run_model(context)
        outcome = await self._run_postprocess(context, model_result)
        outcome.history_ai_row = model_result.history_ai_row
        outcome.traces.extend([
            decision_trace,
            *context.traces,
            QQPipelineStageTrace(
                stage="context",
                status="built",
                metadata={
                    "permission_level": context.permission_level,
                    "is_group": context.is_group,
                    "group_id": str(context.group_id or ""),
                    "memory_context_used": context.memory_context_used,
                    "persist_memory": context.persist_memory,
                    "scene_mode": context.scene_mode,
                    "group_scene_mode": context.group_scene_mode,
                    "core_memory_length": len(context.core_memory_text),
                    "recalled_memory_length": len(context.recalled_memory_text),
                },
            ),
            *model_result.traces,
            QQPipelineStageTrace(
                stage="model",
                status=model_result.source,
                metadata={
                    "used_fallback": model_result.used_fallback,
                    "timed_out": model_result.timed_out,
                    "allow_fallback": model_result.allow_fallback,
                    "fallback_reason": model_result.fallback_reason,
                    "reply_length": len(model_result.reply_text or ""),
                },
            ),
            QQPipelineStageTrace(
                stage="postprocess",
                status="default" if outcome.used_default_message else ("reply" if outcome.reply_text else "empty"),
                metadata={
                    "reply_length": len(outcome.reply_text or ""),
                    "used_default_message": outcome.used_default_message,
                },
            ),
        ])

        # poke/sticker/record/ark 已统一为 <msg> 块，由 reply_delivery_node 处理
        outcome.delivery_plan = self._build_delivery_plan(request, outcome)
        outcome.delivery_result = await self._run_delivery(outcome.delivery_plan, request, outcome, context=context)
        outcome.traces.append(
            QQPipelineStageTrace(
                stage="delivery",
                status="delivered" if outcome.delivery_result and outcome.delivery_result.delivered else "skipped",
                metadata={
                    "target_type": getattr(outcome.delivery_plan, "target_type", ""),
                    "target_id": getattr(outcome.delivery_plan, "target_id", ""),
                    "reply_message_id": getattr(outcome.delivery_plan, "reply_message_id", ""),
                    "at_user_id": getattr(outcome.delivery_plan, "at_user_id", ""),
                },
            )
        )
        return outcome

    def _run_decision(self, request: QQReplyRequest) -> QQReplyDecision:
        return self.plugin.reply_decision_node.decide(request)

    async def _run_relay(self, request: QQReplyRequest, decision: QQReplyDecision, decision_trace: QQPipelineStageTrace) -> QQReplyOutcome:
        outcome = QQReplyOutcome(action="relay", traces=[decision_trace])
        outcome.relay_plan = self.plugin.reply_relay_node.build_plan(
            message_text=request.message_text,
            sender_id=request.sender_id,
            source_type="group" if request.is_group else "private",
            source_id=request.group_id or request.sender_id,
            relay_probability=decision.relay_probability,
        )
        outcome.traces.append(
            QQPipelineStageTrace(
                stage="relay_plan",
                status="built" if outcome.relay_plan else "skipped",
                metadata={
                    "source_type": "group" if request.is_group else "private",
                    "source_id": str(request.group_id or request.sender_id),
                    "relay_probability": decision.relay_probability,
                },
            )
        )
        outcome.relay_result = await self._run_relay_delivery(outcome.relay_plan)
        outcome.traces.append(
            QQPipelineStageTrace(
                stage="relay_delivery",
                status="relayed" if outcome.relay_result and outcome.relay_result.relayed else "skipped",
                metadata={
                    "source_type": getattr(outcome.relay_plan, "source_type", ""),
                    "source_id": getattr(outcome.relay_plan, "source_id", ""),
                },
            )
        )
        return outcome

    async def _run_context(self, request: QQReplyRequest, decision: QQReplyDecision) -> QQReplyContext:
        return await self.plugin.reply_context_node.build(
            message=request.message_text,
            permission_level=(
                getattr(request, "private_permission_level_at_receipt", None)
                if (
                    not request.is_group
                    and getattr(
                        request, "private_permission_level_at_receipt", None,
                    ) is not None
                )
                else decision.permission_level
            ),
            sender_id=request.sender_id,
            attachments=request.attachments,
            is_group=request.is_group,
            group_id=request.group_id,
            user_nickname=request.user_nickname,
            use_memory_context=request.use_memory_context,
            persist_memory=request.persist_memory,
            ephemeral_session=request.ephemeral_session,
            group_facing=request.group_facing,
            group_scene_mode=request.group_scene_mode,
            current_message_id=request.current_message_id,
            is_reply_to_bot=getattr(request, "is_reply_to_bot", False),
            quoted_message_id=getattr(request, "quoted_message_id", "") or "",
            mentions_other_user=getattr(request, "mentions_other_user", False),
            mentions_all=getattr(request, "mentions_all", False),
            reply_context=getattr(request, "reply_context", "") or "",
            force_reply=request.force_reply,
            source_kind=getattr(request, "source_kind", ""),
            member_memory_at_receipt=getattr(
                request, "member_memory_at_receipt", None,
            ),
            group_speaker_permission_level_at_receipt=getattr(
                request, "group_speaker_permission_level_at_receipt", None,
            ),
            participant_memory_at_receipt=getattr(
                request, "participant_memory_at_receipt", None,
            ),
            private_permission_level_at_receipt=getattr(
                request, "private_permission_level_at_receipt", None,
            ),
            inherited_consent_snapshot=getattr(
                request, "inherited_consent_snapshot", None,
            ),
        )

    async def _run_model(self, context: QQReplyContext) -> QQModelResult:
        return await self.plugin.reply_model_node.generate(context)

    async def _run_postprocess(self, context: QQReplyContext, model_result: QQModelResult) -> QQReplyOutcome:
        return await self.plugin.reply_postprocess_node.finalize(context, model_result)

    def _build_delivery_plan(self, request: QQReplyRequest, outcome: QQReplyOutcome):
        return self.plugin.reply_postprocess_node.build_delivery_plan(request, outcome)

    async def _send_ark(self, request: QQReplyRequest, outcome: QQReplyOutcome) -> bool:
        """发送 Ark 卡片消息"""
        ark = outcome.parsed_ark
        title = ark.get("title", "")
        desc = ark.get("desc", "")
        pic = ark.get("pic", "")
        btn = ark.get("btn", "")
        url = ark.get("url", "")
        body_text = ark.get("_body", "")

        # 构建 ark payload
        ark_obj: dict[str, Any] = {"msg_type": 10}
        if title:
            ark_obj["ark"] = {
                "template_id": 37,
                "kv": [
                    {"key": "#PROMPT#", "value": body_text or title},
                    {"key": "#TITLE#", "value": title},
                    {"key": "#DESC#", "value": desc or body_text},
                ]
            }
            if pic:
                ark_obj["ark"]["kv"].append({"key": "#IMGPATH#", "value": pic})
        else:
            ark_obj["ark"] = {
                "template_id": 23,
                "kv": [
                    {"key": "#TITLE#", "value": body_text or "卡片"},
                    {"key": "#DESC#", "value": desc},
                ]
            }
            if pic:
                ark_obj["ark"]["kv"].append({"key": "#IMG#", "value": pic})

        if btn:
            ark_obj["ark"]["kv"].append({"key": "#SUBTITLE#", "value": btn})
        if url:
            ark_obj["ark"]["kv"].append({"key": "#URL#", "value": url})

        from .qq_open_plat import QQOpenPlatformConnection
        if not isinstance(self.plugin.qq_client, QQOpenPlatformConnection):
            # NapCat / OneBot 不支持 Ark 卡片，降级为文本发送
            fallback = body_text or title or desc or ""
            if fallback:
                await self.plugin._deliver_group_reply(
                    str(request.group_id or ""),
                    fallback,
                    reply_message_id="",
                    at_user_id="",
                    fallback_to_text_on_voice_failure=True,
                )
                return True
            return False
        try:
            await self.plugin.qq_client._ensure_token()
            r = await self.plugin.qq_client._http.post(
                f"{self.plugin.qq_client._API_BASE}/v2/groups/{request.group_id}/messages",
                json=ark_obj,
                headers=self.plugin.qq_client._auth_headers(),
            )
            data = r.json()
            return bool(data.get("id"))
        except Exception as e:
            self.plugin.logger.warning(f"[Ark] 发送失败: {e}")
            return False

    async def _run_delivery(self, delivery_plan, request: QQReplyRequest = None, outcome: QQReplyOutcome = None, context=None) -> QQDeliveryResult | None:
        if (
            request is not None
            and outcome is not None
            and self._primary_row_superseded(outcome, delivery_plan)
        ):
            # 主会话产出了非空文本、清洗后为空（例如整条都是思考标签），
            # 于是改发默认回复：那条 raw ai 行已经躺在共享历史里且永远不会
            # 被发出去。used_default_message 让它绕过了所有未投递打标——
            # 下一次 digest 会把用户从没看到的内容（含隐藏推理）入库。
            self.plugin.session_memory_service.record_tail_undelivered_ai_row(
                self.plugin._build_session_key(
                    sender_id=request.sender_id,
                    is_group=request.is_group,
                    group_id=request.group_id if request.is_group else None,
                ),
                outcome.history_ai_row,
            )

        # 情绪/标记：内部状态，先于缓冲/冷却/交付更新
        if outcome and outcome.feeling:
            if delivery_plan and delivery_plan.target_type == "group":
                group = delivery_plan.target_id
            elif request and getattr(request, "is_group", False):
                group = getattr(request, "group_id", "") or ""
            else:
                group = ""
            if group and self.plugin.attention_service:
                await self.plugin.attention_service.set_emotion(group, outcome.feeling)

        # 缓冲内部调用的请求（buffer_delayed/rapid_fire_flush/proactive_speech）不再次走缓冲
        skip_buffer = request and getattr(request, 'source_kind', '') in ('buffer_delayed', 'rapid_fire_flush', 'proactive_speech')
        if not skip_buffer and self.plugin.reply_buffer_service and request and delivery_plan and delivery_plan.blocks:
            # 从 LLM 原始输出提取 <wait> 标签（在 _parse_blocks 之前已保存）
            raw = getattr(outcome, "wait_directive_text", None)
            if raw is None:
                raw = (outcome.raw_reply_text if outcome else "") or ""
                structural_pre_tool = str(
                    getattr(outcome, "pre_tool_text", "") or ""
                )
                if structural_pre_tool and raw.startswith(structural_pre_tool):
                    raw = raw[len(structural_pre_tool):]
            raw = str(raw or "")
            # postprocess 直接携带 sanitizer 后、真实 tool 边界之后的最终段；
            # 因此 hidden/literal pre-tool 内的 <wait> 都不能成为 buffer 指令。
            clean, wait_sec = QQReplyBufferService.extract_wait_seconds(raw)
            # 默认等待加随机抖动（±40%），避免每次都一样
            if wait_sec == QQReplyBufferService.DEFAULT_WAIT_SECONDS:
                import random
                wait_sec = max(1.5, wait_sec * random.uniform(0.6, 1.4))
            # 私聊默认等更久（对方在讲故事/连续输出）
            first_text = delivery_plan.blocks[0].text if delivery_plan.blocks else ""
            visible_text = delivered_blocks_text(delivery_plan.blocks)
            # 检查是否有实际内容（text/record/sticker/poke/emoji 任一非空即有效）
            has_content = any(
                b.text or b.record or b.sticker or b.poke or b.emoji
                for b in (delivery_plan.blocks or [])
            )
            # clean 可能含 <msg></msg>，去标签后再判空
            clean_stripped = re.sub(r"<[^>]+>", "", clean).strip() if clean else ""
            if not has_content and not clean_stripped:
                # LLM 决定不回复（<msg></msg>），跳过缓冲
                from .pipeline_models import QQDeliveryResult
                return QQDeliveryResult(delivered=False, target_type=delivery_plan.target_type, target_id=delivery_plan.target_id, reply_text=None)
            session_key = self.plugin._build_session_key(
                sender_id=request.sender_id,
                is_group=request.is_group,
                group_id=request.group_id,
            )
            self.plugin._emit_log("DEBUG", f"[Buffer] 调度延迟回复: key={session_key} wait={wait_sec:.1f}s text={first_text[:30]}")
            # 转发消息的子条数计入缓冲
            fwd_count = int(getattr(request, 'forward_sub_count', 0) or 0)
            await self.plugin.reply_buffer_service.schedule_reply(
                session_key=session_key,
                # reply_text 是缓冲汇总的语义输入；首块可能只是 pre-tool，
                # 必须把最终 XML 块也带上，不能让汇总只看到“我查一下”。
                reply_text=visible_text or clean or "",
                raw_text=clean or first_text or "",
                blocks=delivery_plan.blocks,
                wait_seconds=wait_sec,
                sender_id=request.sender_id,
                is_group=request.is_group,
                group_id=request.group_id or "",
                extra_count=fwd_count,
                history_backed=not bool(
                    (
                        getattr(outcome, "used_fallback", False)
                        or getattr(outcome, "used_default_message", False)
                    ) if outcome else False
                ),
                mention_context=context,
                used_fallback_reply=bool(
                    (
                        getattr(outcome, "used_fallback", False)
                        or self._primary_row_superseded(outcome, delivery_plan)
                    ) if outcome else False
                ),
                private_permission_level_at_receipt=getattr(
                    context, "private_permission_level_at_receipt", None,
                ),
                first_user_materialized=bool(
                    (
                        getattr(self.plugin, "_user_sessions", {}) or {}
                    ).get(session_key, {}).get("human_row_materialized", False)
                ),
                consent_snapshot=(
                    # 私聊也可能有依赖（跨群开关打开时的会话清单段），
                    # 按 is_group 分流会让那条路径没有可撤的授权。空 dict
                    # 是"本轮无依赖"这个结论，不能折成 None（那是"还没有
                    # 结论"，会去采样当前开关）。
                    self._generation_consent_snapshot(context)
                    if context is not None else None
                ),
                consented=bool(
                    # Use the resolved policy for private participant turns as
                    # well as groups. Otherwise every private input looks
                    # consented and OFF-era text can re-enter memory through a
                    # later synthetic buffer summary.
                    getattr(context, "persist_memory", None)
                    if context is not None
                    and getattr(context, "persist_memory", None) is not None
                    else getattr(request, "persist_memory", None)
                ),
            )
            from .pipeline_models import QQDeliveryResult
            return QQDeliveryResult(delivered=True, target_type=delivery_plan.target_type, target_id=delivery_plan.target_id, reply_text=first_text)

        direct_session_key = None
        direct_ai_row = None
        if (
            request is not None
            and outcome is not None
            and outcome.history_ai_row is not None
            and not getattr(outcome, "used_fallback", False)
            and not getattr(outcome, "used_default_message", False)
            and not self._primary_row_superseded(outcome, delivery_plan)
        ):
            direct_session_key = self.plugin._build_session_key(
                sender_id=request.sender_id,
                is_group=request.is_group,
                group_id=request.group_id if request.is_group else None,
            )
            direct_ai_row = outcome.history_ai_row
            # The history row exists before network delivery. Fence it before
            # the await so an opt-out settlement cannot persist a reply whose
            # send later fails.
            self.plugin.session_memory_service.record_provisional_ai_row(
                direct_session_key, direct_ai_row,
            )

        if context is not None and self._consent_revoked_before_send(context):
            # 直投没有 buffer 的撤销闸：生成后复检到真正发出去之间还有
            # 后处理（XML 修复等再等一次 LLM）与计划构建，这段窗口里关掉
            # 开关的话，带着已撤销记忆的回复照样会发出去。
            self.plugin.logger.warning("发送前记忆授权已撤销，取消本轮投递")
            if (
                request is not None
                and outcome is not None
                and not getattr(outcome, "used_fallback", False)
                and not getattr(outcome, "used_default_message", False)
            ):
                self.plugin.session_memory_service.record_tail_undelivered_ai_row(
                    self.plugin._build_session_key(
                        sender_id=request.sender_id,
                        is_group=request.is_group,
                        group_id=request.group_id if request.is_group else None,
                    ),
                    outcome.history_ai_row,
                )
            from .pipeline_models import QQDeliveryResult
            return QQDeliveryResult(
                delivered=False, target_type=delivery_plan.target_type,
                target_id=delivery_plan.target_id, reply_text=None,
            )

        def _mark_tail_undelivered() -> None:
            if (
                request is not None
                and outcome is not None
                and not getattr(outcome, "used_fallback", False)
                and not getattr(outcome, "used_default_message", False)
            ):
                self.plugin.session_memory_service.record_tail_undelivered_ai_row(
                    self.plugin._build_session_key(
                        sender_id=request.sender_id,
                        is_group=request.is_group,
                        group_id=request.group_id if request.is_group else None,
                    ),
                    outcome.history_ai_row,
                )

        try:
            result = await self.plugin.reply_delivery_node.deliver(
                delivery_plan,
                consent_gate=(
                    (lambda: self._consent_revoked_before_send(context))
                    if context is not None else None
                ),
            )
        except asyncio.CancelledError:
            # 取消（stop_runtime 会显式取消所有 handler task）走的是
            # BaseException，不会被下面的 except Exception 接住：用户可能
            # 一个字没收到、也可能只收到前半条，而 ai 行已经躺在共享历史
            # 里——不打标的话关机结算会把它当已投递入库。
            _mark_tail_undelivered()
            raise
        except Exception:
            # NapCat 传输失败以异常上浮：history-backed 回复的 ai 行已在
            # 共享历史里，先按投递失败记入排除名单再传播异常，否则下一次
            # digest 会把没发出去的回复入库。
            _mark_tail_undelivered()
            raise
        if direct_ai_row is not None:
            self.plugin.session_memory_service.settle_provisional_ai_row(
                direct_session_key, direct_ai_row,
                delivered=bool(result is not None and result.delivered),
            )
        if (
            result is not None
            and not getattr(result, "delivered", False)
            and request is not None
            and outcome is not None
            and not getattr(outcome, "used_fallback", False)
            and not getattr(outcome, "used_default_message", False)
        ):
            # 直投失败（合成轮/无 buffer 的 history-backed 回复）：ai 行已
            # 躺在共享历史里，不记名单的话下一次 digest/finalize 会把没
            # 发出去的回复提取成持久记忆。失败即定局，直接进排除名单。
            session_key = self.plugin._build_session_key(
                sender_id=request.sender_id,
                is_group=request.is_group,
                group_id=request.group_id if request.is_group else None,
            )
            self.plugin.session_memory_service.record_tail_undelivered_ai_row(
                session_key, outcome.history_ai_row,
            )
        if (
            result is not None
            and getattr(result, "delivered", False)
            and context is not None
            and outcome is not None
            and outcome.reply_text
        ):
            # 整条计划的正文：outcome.reply_text 只有首块，后续块里披露
            # 的事实既进不了历史（digest 少半条），也记不到 mention（永远
            # 到不了 suppression）。
            delivered_text = (
                delivered_blocks_text(delivery_plan.blocks) or outcome.reply_text
            )
            if getattr(outcome, "used_fallback", False) or (
                self._primary_row_superseded(outcome, delivery_plan)
            ):
                # fallback / 默认回复都没有对应的历史 ai 行（默认回复那条
                # raw 行刚被标成未投递）：确认投递后补上真正说出去的话，
                # 否则群 digest 只会存下半边对话。
                self.plugin.reply_generation_service.append_fallback_ai_row(
                    context, delivered_text,
                )
            # mention 计数绑定实际投递（非 buffer 直投与合成轮都走这里；
            # buffer 路径由 _deliver_after_wait 在真投递后补记）。
            await self.plugin.reply_generation_service.record_scoped_mentions_on_delivery(
                context, delivered_text,
            )
        return result

    @staticmethod
    def _primary_row_superseded(outcome, delivery_plan) -> bool:
        """True when the ai row this turn wrote is not what went out.

        Two shapes: a default reply that replaced a nonempty primary answer,
        and a block carrying both <text> and <record> — delivery sends the
        record and continues, so that text reaches nobody while it sits in
        the history row."""
        if getattr(outcome, "used_fallback", False):
            return False  # fallback turns have no history row of their own
        if getattr(outcome, "used_default_message", False):
            return bool(str(getattr(outcome, "raw_reply_text", "") or "").strip())
        return any(
            str(getattr(block, "record", "") or "").strip()
            and str(getattr(block, "text", "") or "").strip()
            for block in (getattr(delivery_plan, "blocks", None) or [])
        )

    def _consent_revoked_before_send(self, context) -> bool:
        """True when a switch this reply's prompt relied on went off since
        generation — same judgement the buffer applies before a delayed
        send, so the unbuffered direct path is not the weak link."""
        permission_snapshot = getattr(
            context, "private_permission_level_at_receipt", None,
        )
        permission_mgr = getattr(self.plugin, "permission_mgr", None)
        if (
            not getattr(context, "is_group", False)
            and permission_snapshot is not None
            and permission_mgr is not None
            and permission_mgr.get_permission_level(context.sender_id)
            != permission_snapshot
        ):
            return True
        snapshot = getattr(context, "consent_snapshot", None)
        if not snapshot:
            return False
        settings = getattr(self.plugin, "_qq_settings", {}) or {}
        return any(
            was_enabled and not settings.get(key, False)
            for key, was_enabled in snapshot.items()
        )

    def _generation_consent_snapshot(self, context) -> dict:
        """本轮回复实际消费掉的授权，取自生成时刻而非此刻。

        后处理（XML 修复等）会再等一次 LLM，这中间关掉的开关如果在这里
        重新采样，缓冲的撤销检查就是 false 比 false——被撤销的记忆内容
        照样随延迟投递发出去。生成路径拿不到快照时（合成/轻量 context）
        才退回读当前设置。"""
        snapshot = getattr(context, "consent_snapshot", None)
        if snapshot is not None:
            # 空 dict 也是结论（本轮没有任何记忆依赖），不是"没结论"。
            return dict(snapshot)
        settings = getattr(self.plugin, "_qq_settings", {}) or {}
        return {
            key: bool(settings.get(key, False))
            for key in (
                "group_memory_enabled",
                "group_member_memory_enabled",
                "private_participant_memory_enabled",
                "allow_cross_group_context",
            )
        }

    def _resolve_sticker_path(self, sticker_id: str) -> str:
        """解析表情包 ID 到文件路径。"""
        import json, os
        sticker_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data", "sticker.json",
        )
        try:
            with open(sticker_path, "r", encoding="utf-8") as f:
                sticker_data = json.loads(f.read())
        except Exception:
            return ""
        info = sticker_data.get(sticker_id)
        if not isinstance(info, dict):
            return ""
        img_path = info.get("path", "")
        if not img_path:
            return ""
        full_path = os.path.join(os.path.dirname(sticker_path), "sticker", img_path)
        if os.path.exists(full_path):
            return f"file://{full_path}"
        return img_path

    async def _run_relay_delivery(self, relay_plan) -> QQRelayResult | None:
        return await self.plugin.reply_relay_node.execute(relay_plan)
