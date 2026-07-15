from __future__ import annotations

from typing import Any

from .pipeline_models import QQDeliveryPlan, QQDeliveryResult


class QQReplyDeliveryNode:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def deliver(self, plan: QQDeliveryPlan | None) -> QQDeliveryResult | None:
        if not plan or not plan.reply_text:
            return None
        if plan.target_type == "group":
            await self.plugin._deliver_group_reply(
                plan.target_id,
                plan.reply_text,
                reply_message_id=plan.reply_message_id,
                at_user_id=plan.at_user_id,
                keyboard=plan.keyboard,
                fallback_to_text_on_voice_failure=plan.fallback_to_text_on_voice_failure,
            )
        else:
            await self.plugin._deliver_private_reply(
                plan.target_id,
                plan.reply_text,
                fallback_to_text_on_voice_failure=plan.fallback_to_text_on_voice_failure,
            )
        return QQDeliveryResult(
            delivered=True,
            target_type=plan.target_type,
            target_id=plan.target_id,
            reply_text=plan.reply_text,
        )
