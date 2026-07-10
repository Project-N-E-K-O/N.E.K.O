from __future__ import annotations

from typing import Any

from .pipeline_models import QQDeliveryResult, QQModelResult, QQPipelineStageTrace, QQRelayResult, QQReplyContext, QQReplyDecision, QQReplyOutcome, QQReplyRequest


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
        outcome = self._run_postprocess(context, model_result)
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

        # 处理 poke / img 特殊动作（不走文本 delivery）
        if outcome.action == "poke" and outcome.parsed_poke_user and request.is_group:
            ok = await self.plugin.qq_client.send_group_poke(
                str(request.group_id or ""),
                outcome.parsed_poke_user,
            )
            outcome.delivery_result = QQDeliveryResult(
                delivered=ok,
                target_type="group",
                target_id=str(request.group_id or ""),
                reply_text=None,
            )
            outcome.traces.append(
                QQPipelineStageTrace(
                    stage="delivery",
                    status="delivered" if ok else "skipped",
                    metadata={"action": "poke", "target_user": outcome.parsed_poke_user},
                )
            )
            return outcome
        if outcome.action == "ark" and outcome.parsed_ark and request.is_group:
            ok = await self._send_ark(request, outcome)
            outcome.delivery_result = QQDeliveryResult(delivered=ok, target_type="group", target_id=str(request.group_id or ""), reply_text=None)
            outcome.traces.append(QQPipelineStageTrace(stage="delivery", status="delivered" if ok else "skipped", metadata={"action":"ark","title":outcome.parsed_ark.get("title","")[:50]}))
            return outcome
        outcome.delivery_plan = self._build_delivery_plan(request, outcome)
        outcome.delivery_result = await self._run_delivery(outcome.delivery_plan)

        # 表情包作为文字后的跟发消息（每群每5条消息最多发一次）
        if outcome.parsed_sticker_id and request.is_group:
            gid = str(request.group_id or "")
            since = self.plugin._sticker_since.get(gid) or 0
            threshold = getattr(self.plugin, "_sticker_cooldown_messages", 5)
            if threshold <= 0 or since >= threshold:
                if threshold > 0:
                    self.plugin._sticker_since[gid] = 0
                await self._send_sticker(request, outcome)
            else:
                self.plugin._emit_log("INFO", f"[Sticker] 群 {gid} 距上次表情包仅 {since} 条消息，跳过（需≥{threshold}）")
            outcome.traces.append(
                QQPipelineStageTrace(
                    stage="delivery",
                    status="sticker_follow",
                    metadata={"sticker_id": outcome.parsed_sticker_id},
                )
            )
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
            permission_level=decision.permission_level,
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
            force_reply=request.force_reply,
        )

    async def _run_model(self, context: QQReplyContext) -> QQModelResult:
        return await self.plugin.reply_model_node.generate(context)

    def _run_postprocess(self, context: QQReplyContext, model_result: QQModelResult) -> QQReplyOutcome:
        return self.plugin.reply_postprocess_node.finalize(context, model_result)

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

    async def _run_delivery(self, delivery_plan) -> QQDeliveryResult | None:
        return await self.plugin.reply_delivery_node.deliver(delivery_plan)

    async def _send_sticker(self, request: QQReplyRequest, outcome: QQReplyOutcome) -> bool:
        """发送注册的表情包图片"""
        import json, os
        sticker_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data", "sticker.json",
        )
        try:
            with open(sticker_path, "r", encoding="utf-8") as f:
                sticker_data = json.loads(f.read())
        except Exception:
            self.plugin.logger.warning("[Sticker] sticker.json 读取失败")
            return False
        info = sticker_data.get(outcome.parsed_sticker_id)
        if not isinstance(info, dict):
            self.plugin.logger.warning(f"[Sticker] 未知ID: {outcome.parsed_sticker_id}")
            return False
        img_path = info.get("path", "")
        if not img_path:
            return False
        # 尝试作为本地文件路径发送
        full_path = os.path.join(os.path.dirname(sticker_path), "sticker", img_path)
        if not os.path.exists(full_path):
            # 可能直接存了绝对路径或URL
            full_path = img_path
        msg_id = await self.plugin.qq_client.send_group_image(
            str(request.group_id or ""),
            f"file://{full_path}" if os.path.exists(full_path) else full_path,
        )
        return bool(msg_id)

    async def _run_relay_delivery(self, relay_plan) -> QQRelayResult | None:
        return await self.plugin.reply_relay_node.execute(relay_plan)
