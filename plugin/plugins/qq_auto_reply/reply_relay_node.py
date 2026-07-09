from __future__ import annotations

from typing import Any

from .pipeline_models import QQRelayPlan, QQRelayResult


class QQReplyRelayNode:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def build_plan(
        self,
        *,
        message_text: str,
        sender_id: str,
        source_type: str,
        source_id: str,
        relay_probability: float | None,
    ) -> QQRelayPlan | None:
        if not self.plugin.qq_client or not self.plugin._admin_qq or sender_id == self.plugin._admin_qq:
            return None
        effective_probability = self.plugin._normal_relay_probability if relay_probability is None else float(relay_probability)
        if effective_probability <= 0.0:
            return None
        sanitized_message = self.plugin._sanitize_message_text(message_text)
        if source_type == "group":
            relay_text = f"[QQ群转发] 群 {source_id} / 用户 {sender_id}: {sanitized_message}"
        else:
            relay_text = f"[QQ私聊转发] 来自 {sender_id}: {sanitized_message}"
        return QQRelayPlan(
            source_type=source_type,
            source_id=str(source_id or ""),
            sender_id=str(sender_id or ""),
            original_message=sanitized_message,
            relay_text=relay_text,
            relay_probability=effective_probability,
            target_admin_qq=self.plugin._admin_qq,
        )

    async def execute(self, plan: QQRelayPlan | None) -> QQRelayResult | None:
        if not plan:
            return None
        relayed = await self.plugin.relay_service.execute_relay_plan(plan)
        return QQRelayResult(
            relayed=relayed,
            source_type=plan.source_type,
            source_id=plan.source_id,
            sender_id=plan.sender_id,
            relay_text=plan.relay_text if relayed else None,
        )
