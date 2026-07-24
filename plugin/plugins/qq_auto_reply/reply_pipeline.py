from __future__ import annotations

import re
from typing import Any

from .pipeline_models import QQDeliveryResult, QQModelResult, QQPipelineStageTrace, QQRelayResult, QQReplyContext, QQReplyDecision, QQReplyOutcome, QQReplyRequest
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
        outcome.delivery_result = await self._run_delivery(outcome.delivery_plan, request, outcome)
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
        # 汇总日志：完整回复链条
        stages = []
        for t in outcome.traces:
            if t.stage == "decision":
                stages.append(f"决策={t.status}" + (f"({t.metadata.get('suppression_reason')})" if t.metadata.get("suppression_reason") else ""))
            elif t.stage == "model":
                stages.append(f"模型={t.status}" + (f"(fallback={t.metadata.get('fallback_reason')})" if t.metadata.get("fallback_reason") else ""))
            elif t.stage == "postprocess":
                stages.append(f"后处理={t.status}")
            elif t.stage == "delivery":
                stages.append(f"交付={t.status}")
        scope = f"群{request.group_id}" if request.is_group else f"私聊{request.sender_id}"
        reply_preview = (outcome.reply_text or "")[:50]
        self.plugin._emit_log("INFO", f"[链路] {scope} | {' → '.join(stages)} | 回复={reply_preview}")
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
            is_at_bot=request.is_at_bot,
            mentions_all=request.mentions_all,
            reply_context=request.reply_context,
        )

    async def _run_model(self, context: QQReplyContext) -> QQModelResult:
        return await self.plugin.reply_model_node.generate(context)

    async def _run_postprocess(self, context: QQReplyContext, model_result: QQModelResult) -> QQReplyOutcome:
        return await self.plugin.reply_postprocess_node.finalize(context, model_result)

    def _build_delivery_plan(self, request: QQReplyRequest, outcome: QQReplyOutcome):
        return self.plugin.reply_postprocess_node.build_delivery_plan(request, outcome)

    async def _run_delivery(self, delivery_plan, request: QQReplyRequest = None, outcome: QQReplyOutcome = None) -> QQDeliveryResult | None:
        # 缓冲内部调用的请求（buffer_delayed/rapid_fire_flush/proactive_speech）不再次走缓冲
        source = getattr(request, 'source_kind', '') if request else ''
        skip_buffer = request and (
            source in ('buffer_delayed', 'rapid_fire_flush', 'proactive_speech', 'retroactive_review', 'incoming_group', 'incoming_private')
            or request.is_at_bot or request.force_reply
        )
        # 情绪/标记：内部状态，先于缓冲/冷却/交付更新
        if outcome:
            if outcome.feeling:
                group = delivery_plan.target_id if delivery_plan.target_type == "group" else ""
                if group and self.plugin.attention_service:
                    self.plugin.attention_service.set_emotion(group, outcome.feeling)
            if outcome.forward_mark and request and request.is_group:
                mk = f"group:{str(request.group_id or '').strip()}"
                sessions = getattr(self.plugin, "_user_sessions", {}) or {}
                s = sessions.get(mk)
                if isinstance(s, dict):
                    ses = s.get("session")
                    if ses and hasattr(ses, "_conversation_history"):
                        s["_forward_mark"] = len(getattr(ses, "_conversation_history", []) or [])
                        self.plugin._emit_log("DEBUG", f"[Mark] 转发起点已标记 group={request.group_id} pos={s['_forward_mark']}")

        if not skip_buffer and self.plugin.reply_buffer_service and request and delivery_plan and delivery_plan.blocks:
            first_text = delivery_plan.blocks[0].text if delivery_plan.blocks else ""
            has_content = any(
                b.text or b.record or b.sticker or b.poke
                or b.rps or b.dice
                or (b.contact_type and b.contact_id)
                or b.music_type
                or b.mface_id
                or b.file_path
                or b.json_data
                or b.keyboard
                or b.ark
                for b in (delivery_plan.blocks or [])
            )
            if not has_content:
                # 空回复（如 <msg></msg>）→ 取消对应缓冲桶，避免 _flush 空等 30 次
                # 必须校验 bucket_id，防止旧 pipeline 的空回复误删已被替换的新桶
                buf_sid = request.group_id if request.is_group else request.sender_id
                session_key = self.plugin._build_session_key(sender_id=buf_sid, is_group=request.is_group, group_id=request.group_id)
                expected_bucket_id = getattr(request, 'buffer_bucket_id', 0) if request else 0
                if expected_bucket_id:
                    svc = self.plugin.reply_buffer_service
                    p = svc._pending.get(session_key)
                    if p is None or p.bucket_id != expected_bucket_id:
                        p = svc._detached.get(svc._detached_key(session_key, expected_bucket_id))
                    if p is None or p.bucket_id != expected_bucket_id:
                        self.plugin._emit_log("DEBUG", f"[Buffer] 空回复但桶已过期 key={session_key} expected_id={expected_bucket_id} actual_id={getattr(p, 'bucket_id', 0)} → 忽略")
                        from .pipeline_models import QQDeliveryResult
                        return QQDeliveryResult(delivered=False, target_type=delivery_plan.target_type, target_id=delivery_plan.target_id, reply_text=None)
                    if p.task and not p.task.done():
                        p.task.cancel()
                    svc._pending.pop(session_key, None)
                    svc._detached.pop(svc._detached_key(session_key, expected_bucket_id), None)
                else:
                    p = self.plugin.reply_buffer_service._pending.get(session_key)
                    if p and p.task and not p.task.done():
                        p.task.cancel()
                    self.plugin.reply_buffer_service._pending.pop(session_key, None)
                self.plugin._emit_log("DEBUG", f"[Buffer] 空回复，取消缓冲 key={session_key}")
                from .pipeline_models import QQDeliveryResult
                return QQDeliveryResult(delivered=False, target_type=delivery_plan.target_type, target_id=delivery_plan.target_id, reply_text=None)
        # 不进缓冲 → 直接交付（单条消息回复不再缓存，由桶定时汇总独立生成）
        reason = "skip" if skip_buffer else ("group" if request and request.is_group else "no_buffer_svc")
        self.plugin._emit_log("DEBUG", f"[Delivery] 直接发送 source={source} reason={reason}")

        # 群聊冷却：同一群非强制消息至少间隔 5 秒
        is_force = request and (request.is_at_bot or request.force_reply)
        is_skip_cool = source in ('retroactive_review', 'proactive_speech', 'buffer_delayed', 'rapid_fire_flush')
        if delivery_plan.target_type == "group" and not is_force and not is_skip_cool:
            now_ts = __import__("time").time()
            if not hasattr(self.plugin, "_last_group_reply_at"):
                self.plugin._last_group_reply_at: dict[str, float] = {}
            last = self.plugin._last_group_reply_at.get(delivery_plan.target_id, 0)
            if now_ts - last < 5.0:
                self.plugin._emit_log("DEBUG", f"[Cooldown] 群{delivery_plan.target_id} 冷却中 ({now_ts - last:.1f}s)，跳过")
                return QQDeliveryResult(delivered=False, target_type=delivery_plan.target_type, target_id=delivery_plan.target_id, reply_text=None)
            self.plugin._last_group_reply_at[delivery_plan.target_id] = now_ts

        # Emoji reaction on the incoming message
        emoji_id = (outcome.emoji_reaction_id if outcome else "") or ""
        current_msg_id = (request.current_message_id if request else "") or ""
        if emoji_id and current_msg_id and self.plugin.qq_client and self.plugin.qq_client.needs_attention:
            try:
                await self.plugin.qq_client.set_msg_emoji_like(current_msg_id, emoji_id)
                self.plugin._emit_log("INFO", f"[Emoji] reaction: msg={current_msg_id} emoji={emoji_id}")
            except Exception as e:
                self.plugin._emit_log("WARN", f"[Emoji] reaction failed: {e}")

        # Forward message: to="QQ号" → 私聊转发；to="群号" 或留空 → 群内转发
        fw = (outcome.forward_content if outcome else "") or ""
        fw_target = (outcome.forward_target if outcome else "") or ""
        if fw and self.plugin.qq_client and self.plugin.qq_client.needs_attention:
            # LLM 正文当引言，后拼接原文转发
            intro_nodes = self._build_forward_nodes(fw)
            count = outcome.forward_count if outcome and outcome.forward_count else 20
            history_nodes = self._build_history_forward_nodes(request, count)
            fw_nodes = intro_nodes + history_nodes
            if fw_nodes:
                try:
                    if fw_target:
                        await self.plugin.qq_client.send_private_forward_msg(fw_target, fw_nodes)
                        self.plugin._emit_log("INFO", f"[Forward] →私聊{fw_target}: {len(fw_nodes)}条(含{len(history_nodes)}条原文)")
                    elif request and request.is_group:
                        await self.plugin.qq_client.send_group_forward_msg(str(request.group_id or ""), fw_nodes)
                        self.plugin._emit_log("INFO", f"[Forward] →群{request.group_id}: {len(fw_nodes)}条")
                    elif request:
                        await self.plugin.qq_client.send_private_forward_msg(request.sender_id or "", fw_nodes)
                        self.plugin._emit_log("INFO", f"[Forward] →私聊: {len(fw_nodes)}条")
                except Exception as e:
                    self.plugin._emit_log("WARN", f"[Forward] failed: {e}")

        return await self.plugin.reply_delivery_node.deliver(delivery_plan)

    @staticmethod
    def _build_forward_nodes(forward_content: str) -> list[dict[str, Any]]:
        import re
        nodes = []
        for line in forward_content.strip().split("\n"):
            line = line.strip()
            if not line: continue
            m = re.match(r"\[?([^\]]*?)\]?\s*:\s*(.*)", line)
            if m: name, content = m.group(1).strip(), m.group(2).strip()
            else: name, content = "猫娘", line
            if content:
                nodes.append({"type":"node","data":{"name":name or "猫娘","uin":"0","content":content}})
        return nodes

    def _build_history_forward_nodes(self, request: QQReplyRequest, count: int = 20) -> list[dict[str, Any]]:
        """从会话历史中取标记位之后的消息作为转发原文。无标记则取最近 N 条。"""
        nodes: list[dict[str, Any]] = []
        if not request or not request.is_group:
            return nodes
        session_key = f"group:{str(request.group_id or '').strip()}"
        sessions = getattr(self.plugin, "_user_sessions", {}) or {}
        s = sessions.get(session_key)
        if not isinstance(s, dict):
            return nodes
        session = s.get("session")
        if not session or not hasattr(session, "_conversation_history"):
            return nodes
        history = getattr(session, "_conversation_history", []) or []
        if not history:
            return nodes
        # 有标记 → 从标记位开始取；无标记 → 取最近 N 条
        mark = s.get("_forward_mark", 0)
        start = max(0, mark) if mark else max(0, len(history) - count)
        # 转发后清除标记
        s["_forward_mark"] = 0
        for msg in history[start:]:
            role = getattr(msg, "role", "") if hasattr(msg, "role") else msg.get("role", "")
            content = getattr(msg, "content", "") if hasattr(msg, "content") else msg.get("content", "")
            if role in ("user", "assistant") and content:
                name = "猫娘" if role == "assistant" else (request.user_nickname or "群友")
                nodes.append({"type": "node", "data": {"name": name, "uin": "0", "content": str(content)[:500]}})
        return nodes

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
