from __future__ import annotations

import random
import time
from typing import Any

from plugin.sdk.plugin import Err, Ok, SdkError

from .pipeline_models import QQRelayPlan
from .targets import QQAutoReplyValidationError


class QQRelayService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    async def handle_normal_relay(
        self,
        message_text: str,
        sender_id: str,
        source_type: str,
        source_id: str,
        relay_probability: float | None = None,
    ):
        relay_plan = self.plugin.reply_relay_node.build_plan(
            message_text=message_text,
            sender_id=sender_id,
            source_type=source_type,
            source_id=source_id,
            relay_probability=relay_probability,
        )
        relay_result = await self.execute_relay_plan(relay_plan)
        return relay_result

    async def execute_relay_plan(self, relay_plan: QQRelayPlan | None) -> bool:
        if not relay_plan:
            return False
        if random.random() >= relay_plan.relay_probability:
            return False
        self.plugin._relay_backlog_items = ([{
            "id": f"{relay_plan.source_type}:{relay_plan.source_id}:{relay_plan.sender_id}:{int(time.time() * 1000)}",
            "source_type": relay_plan.source_type,
            "target_id": relay_plan.source_id,
            "sender_id": relay_plan.sender_id,
            "target_label": f"QQ群 {relay_plan.source_id}" if relay_plan.source_type == "group" else f"私聊 {relay_plan.source_id}",
            "original_message": relay_plan.original_message,
            "relay_preview": relay_plan.relay_text,
            "timestamp": int(time.time()),
        }] + list(self.plugin._relay_backlog_items))[:50]
        await self.plugin._deliver_private_reply(
            relay_plan.target_admin_qq,
            relay_plan.relay_text,
            fallback_to_text_on_voice_failure=True,
        )
        return True

    async def send_backlog_reply_direct(
        self,
        *,
        source_type: str,
        target_id: str,
        original_message: str,
        reply_text: str,
        sender_id: str = "",
        message_id: str = "",
    ):
        try:
            self.plugin._ensure_qq_client_connected()
            normalized_source_type = str(source_type or "").strip().lower()
            normalized_target_id = str(target_id or "").strip()
            normalized_original_message = self.plugin._validate_outbound_message(original_message)
            normalized_reply_text = self.plugin._validate_outbound_message(reply_text)
            normalized_message_id = str(message_id or "").strip()
            if normalized_source_type not in {"group", "private"}:
                return Err(SdkError("INVALID_SOURCE_TYPE: source_type 必须是 group 或 private"))
            if not normalized_target_id:
                return Err(SdkError("INVALID_TARGET: target_id 不能为空"))
            if normalized_source_type == "group":
                await self.plugin._deliver_group_reply(
                    normalized_target_id,
                    normalized_reply_text,
                    reply_message_id=normalized_message_id,
                    at_user_id=str(sender_id or ""),
                    fallback_to_text_on_voice_failure=False,
                )
                self._remove_relay_backlog_item(
                    source_type=normalized_source_type,
                    target_id=normalized_target_id,
                    sender_id=str(sender_id or ""),
                    original_message=normalized_original_message,
                )
                await self.plugin.backlog_store.mark_group_reviewed(normalized_target_id)
            else:
                await self.plugin._deliver_private_reply(
                    normalized_target_id,
                    normalized_reply_text,
                    fallback_to_text_on_voice_failure=False,
                )
            self._remove_relay_backlog_item(
                source_type=normalized_source_type,
                target_id=normalized_target_id,
                sender_id=str(sender_id or ""),
                original_message=normalized_original_message,
            )
            self.plugin.runtime_service.record_manual_trace(
                source="backlog_manual_reply",
                action="manual_reply",
                sender_id=str(sender_id or ""),
                conversation_scope=normalized_source_type,
                conversation_id=normalized_target_id,
                message_text=normalized_original_message,
                reply_text=normalized_reply_text,
                detail=f"{normalized_source_type}:{normalized_target_id}",
            )
            return Ok({"status": "sent", "source_type": normalized_source_type, "target_id": normalized_target_id})
        except QQAutoReplyValidationError as e:
            return Err(SdkError(f"INVALID_ARGUMENT: {str(e)}"))
        except RuntimeError as e:
            return Err(SdkError(f"NOT_READY: {self.plugin.i18n.t('errors.proactive_not_ready', default='{error}', error=str(e))}"))
        except Exception as e:
            self.plugin.logger.exception("Failed to send direct backlog reply")
            return Err(SdkError(f"SEND_FAILED: {self.plugin.i18n.t('errors.proactive_send_failed', default='{error}', error=str(e))}"))

    def _remove_relay_backlog_item(self, *, source_type: str, target_id: str, sender_id: str, original_message: str) -> None:
        self.plugin._relay_backlog_items = [
            item for item in self.plugin._relay_backlog_items
            if not (
                str(item.get("source_type") or "") == source_type
                and str(item.get("target_id") or "") == target_id
                and str(item.get("sender_id") or "") == sender_id
                and str(item.get("original_message") or "") == original_message
            )
        ]
