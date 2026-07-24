from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
import time


class QQRuntimeService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def build_runtime_status(self) -> dict[str, Any]:
        qrcode_path = self.plugin.config_dir / "static" / "cache" / "qrcode.png"
        attention_snapshot = self.plugin.attention_service.get_snapshot() if getattr(self.plugin, "attention_service", None) else {"enabled": False, "focus_group_id": "", "focus_score": 0.0, "groups": []}
        return {
            "plugin_running": True,
            "auto_reply_running": self.plugin._running,
            "onebot_connected": bool(self.plugin.qq_client and self.plugin.qq_client.is_connected()),
            "napcat_managed": self.plugin._manages_napcat_process,
            "napcat_running": bool(self.plugin._napcat_process and self.plugin._napcat_process.returncode is None),
            "onebot_connected": bool(self.plugin.qq_client and self.plugin.qq_client.is_connected()),
            "napcat_pid": int(self.plugin._napcat_process.pid) if self.plugin._napcat_process and self.plugin._napcat_process.returncode is None and self.plugin._napcat_process.pid else None,
            "qrcode_url": f"/plugin/{self.plugin.plugin_id}/ui/cache/qrcode.png" if qrcode_path.is_file() else "",
            "show_napcat_window": bool((self.plugin._qq_settings or {}).get("show_napcat_window", True)),
            "startup_error": str(self.plugin._startup_error or "") or None,
            "attention": attention_snapshot,
            "fatigue": self._build_fatigue_snapshot(),
            "recent_pipeline_traces": self.get_recent_pipeline_traces(),
        }

    def _build_fatigue_snapshot(self) -> dict[str, Any]:
        fatigue = getattr(self.plugin, "fatigue_service", None)
        if not fatigue: return {"available": False}
        import datetime as _dt
        now = _dt.datetime.now()
        sleeping_now = fatigue._sleep_start_at > 0
        return {
            "available": True,
            "total_fatigue": round(fatigue.calculate_fatigue("__global__"), 1),
            "circadian": round(fatigue._circadian_fatigue(), 1),
            "global_load": round(fatigue._global_load_fatigue(), 1),
            "sleeping_groups": len([k for k, v in fatigue._sleeping.items() if v]),
            "awake_groups": len([k for k, v in fatigue._sleeping.items() if not v]),
            "sleep_debt": round(fatigue._sleep_debt_fatigue(), 1),
            "total_sleep_24h_h": round(fatigue._total_sleep_24h(min_duration=600) / 3600, 1),
            "bedtime": f"{int(fatigue._bedtime_hour):02d}:{int((fatigue._bedtime_hour % 1) * 60):02d}",
            "wake_time": f"{int(fatigue._wake_hour):02d}:{int((fatigue._wake_hour % 1) * 60):02d}",
            "sleep_duration_h": round(fatigue._sleep_duration, 1),
            "woken_early": fatigue._woken_early,
            "sleeping_now": sleeping_now,
            "sleep_started_at": _dt.datetime.fromtimestamp(fatigue._sleep_start_at).strftime("%H:%M") if sleeping_now else "",
            "last_sleep_start": _dt.datetime.fromtimestamp(fatigue._last_sleep_start).strftime("%H:%M") if fatigue._last_sleep_start > 0 else "",
            "last_sleep_end": _dt.datetime.fromtimestamp(fatigue._last_sleep_end).strftime("%H:%M") if fatigue._last_sleep_end > 0 else "",
            "time": now.strftime("%Y-%m-%d %H:%M") + " 星期" + "一二三四五六日"[now.weekday()],
        }

    def record_pipeline_outcome(self, *, source: str, request: Any, outcome: Any) -> None:
        traces = [
            {
                "stage": trace.stage,
                "status": trace.status,
                "detail": trace.detail,
                "metadata": trace.metadata,
            }
            for trace in getattr(outcome, "traces", [])
        ]
        relay_plan = getattr(outcome, "relay_plan", None)
        relay_result = getattr(outcome, "relay_result", None)
        delivery_plan = getattr(outcome, "delivery_plan", None)
        delivery_result = getattr(outcome, "delivery_result", None)
        target_state = self._build_pipeline_target_state(
            request=request,
            outcome=outcome,
            relay_plan=relay_plan,
            relay_result=relay_result,
            delivery_plan=delivery_plan,
            delivery_result=delivery_result,
        )
        response_text = str(
            getattr(outcome, "reply_text", None)
            or getattr(relay_result, "relay_text", None)
            or getattr(relay_plan, "relay_text", None)
            or ""
        )
        entry = {
            "timestamp": int(time.time()),
            "source": source,
            "action": getattr(outcome, "action", ""),
            "sender_id": str(getattr(request, "sender_id", "") or ""),
            "is_group": target_state["conversation_scope"] == "group",
            "group_id": target_state["conversation_id"] if target_state["conversation_scope"] == "group" else "",
            "message_text": str(getattr(request, "message_text", "") or ""),
            "reply_text": getattr(outcome, "reply_text", None),
            "response_text": response_text,
            "target_scope": target_state["conversation_scope"],
            "target_id": target_state["conversation_id"],
            "operator_target_id": target_state["operator_target_id"],
            "suppression_reason": str(getattr(request, "suppression_reason", "") or ""),
            **target_state,
            "summary": {},
            "traces": traces,
        }
        entry["summary"] = self._build_entry_summary(entry)
        self._store_recent_trace_entry(entry)

    def record_manual_trace(
        self,
        *,
        source: str,
        action: str,
        sender_id: str,
        conversation_scope: str,
        conversation_id: str,
        message_text: str,
        reply_text: str,
        detail: str,
    ) -> None:
        target_state = self._build_manual_target_state(
            sender_id=sender_id,
            conversation_scope=conversation_scope,
            conversation_id=conversation_id,
        )
        entry = {
            "timestamp": int(time.time()),
            "source": source,
            "action": action,
            "sender_id": str(sender_id or ""),
            "is_group": target_state["conversation_scope"] == "group",
            "group_id": target_state["conversation_id"] if target_state["conversation_scope"] == "group" else "",
            "message_text": str(message_text or ""),
            "reply_text": reply_text,
            "response_text": str(reply_text or ""),
            "target_scope": target_state["conversation_scope"],
            "target_id": target_state["conversation_id"],
            "operator_target_id": target_state["operator_target_id"],
            **target_state,
            "summary": {},
            "traces": [{
                "stage": "manual_reply",
                "status": "sent",
                "detail": detail,
                "metadata": {
                    "reply_length": len(reply_text or ""),
                },
            }],
        }
        entry["summary"] = self._build_entry_summary(entry)
        self._store_recent_trace_entry(entry)

    def _build_pipeline_target_state(
        self,
        *,
        request: Any,
        outcome: Any,
        relay_plan: Any,
        relay_result: Any,
        delivery_plan: Any,
        delivery_result: Any,
    ) -> dict[str, str]:
        actor_id = str(getattr(request, "sender_id", "") or "")
        conversation_scope = "group" if bool(getattr(request, "is_group", False)) else "private"
        conversation_id = str(getattr(request, "group_id", "") or actor_id or "")
        delivery_target_scope = ""
        delivery_target_id = ""
        operator_target_scope = ""
        operator_target_id = ""
        action = str(getattr(outcome, "action", "") or "")
        if action == "reply":
            delivery_target_scope = str(
                getattr(delivery_result, "target_type", "")
                or getattr(delivery_plan, "target_type", "")
                or ""
            )
            delivery_target_id = str(
                getattr(delivery_result, "target_id", "")
                or getattr(delivery_plan, "target_id", "")
                or ""
            )
        elif action == "relay":
            delivery_target_scope = "private" if str(getattr(relay_plan, "target_admin_qq", "") or "") else ""
            delivery_target_id = str(getattr(relay_plan, "target_admin_qq", "") or "")
            operator_target_scope = delivery_target_scope
            operator_target_id = delivery_target_id
        return self._build_target_state(
            actor_id=actor_id,
            conversation_scope=conversation_scope,
            conversation_id=conversation_id,
            delivery_target_scope=delivery_target_scope,
            delivery_target_id=delivery_target_id,
            operator_target_scope=operator_target_scope,
            operator_target_id=operator_target_id,
        )

    def _build_manual_target_state(self, *, sender_id: str, conversation_scope: str, conversation_id: str) -> dict[str, str]:
        normalized_scope = "group" if conversation_scope == "group" else "private"
        normalized_id = str(conversation_id or "")
        return self._build_target_state(
            actor_id=str(sender_id or ""),
            conversation_scope=normalized_scope,
            conversation_id=normalized_id,
            delivery_target_scope=normalized_scope,
            delivery_target_id=normalized_id,
            operator_target_scope="",
            operator_target_id="",
        )

    def _build_target_state(
        self,
        *,
        actor_id: str,
        conversation_scope: str,
        conversation_id: str,
        delivery_target_scope: str,
        delivery_target_id: str,
        operator_target_scope: str,
        operator_target_id: str,
    ) -> dict[str, str]:
        recipient_scope = operator_target_scope or delivery_target_scope or conversation_scope
        recipient_id = operator_target_id or delivery_target_id or conversation_id
        return {
            "origin_scope": conversation_scope,
            "origin_id": conversation_id,
            "actor_id": actor_id,
            "recipient_scope": recipient_scope,
            "recipient_id": recipient_id,
            "conversation_scope": conversation_scope,
            "conversation_id": conversation_id,
            "delivery_target_scope": delivery_target_scope,
            "delivery_target_id": delivery_target_id,
            "operator_target_scope": operator_target_scope,
            "operator_target_id": operator_target_id,
        }

    def _build_entry_summary(self, entry: dict[str, Any]) -> dict[str, Any]:
        traces = list(entry.get("traces") or [])
        source = str(entry.get("source") or "")
        action = str(entry.get("action") or "")
        message_text = str(entry.get("message_text") or "")
        response_text = str(entry.get("response_text") or entry.get("reply_text") or "")
        conversation_scope = str(entry.get("conversation_scope") or entry.get("target_scope") or ("group" if bool(entry.get("is_group", False)) else "private"))
        conversation_id = str(entry.get("conversation_id") or entry.get("target_id") or entry.get("group_id") or entry.get("sender_id") or "")
        delivery_target_scope = str(entry.get("delivery_target_scope") or "")
        delivery_target_id = str(entry.get("delivery_target_id") or "")
        operator_target_scope = str(entry.get("operator_target_scope") or "")
        operator_target_id = str(entry.get("operator_target_id") or "")
        origin_scope = str(entry.get("origin_scope") or conversation_scope)
        origin_id = str(entry.get("origin_id") or conversation_id)
        actor_id = str(entry.get("actor_id") or entry.get("sender_id") or "")
        recipient_scope = str(entry.get("recipient_scope") or operator_target_scope or delivery_target_scope or conversation_scope)
        recipient_id = str(entry.get("recipient_id") or operator_target_id or delivery_target_id or conversation_id)
        return {
            "title": f"{source}:{action}",
            "source": source,
            "action": action,
            "scope": conversation_scope,
            "target_id": conversation_id,
            "origin_scope": origin_scope,
            "origin_id": origin_id,
            "actor_id": actor_id,
            "recipient_scope": recipient_scope,
            "recipient_id": recipient_id,
            "conversation_scope": conversation_scope,
            "conversation_id": conversation_id,
            "delivery_target_scope": delivery_target_scope,
            "delivery_target_id": delivery_target_id,
            "operator_target_scope": operator_target_scope,
            "operator_target_id": operator_target_id,
            "message_preview": message_text[:80],
            "response_preview": response_text[:80],
            "reply_preview": response_text[:80],
            "trace_count": len(traces),
            "last_stage": traces[-1]["stage"] if traces else "",
            "last_status": traces[-1]["status"] if traces else "",
            "result_kind": self._derive_result_kind(action=action, traces=traces),
            "delivery_mode": self._derive_delivery_mode(action=action, delivery_target_id=delivery_target_id),
            "response_length": len(response_text),
            "suppression_reason": str(entry.get("suppression_reason") or ""),
        }

    def _derive_result_kind(self, *, action: str, traces: list[dict[str, Any]]) -> str:
        if action == "ignore":
            return "ignored"
        if not traces:
            return action or "unknown"
        last_trace = traces[-1]
        last_stage = str(last_trace.get("stage") or "")
        last_status = str(last_trace.get("status") or "")
        if action == "relay":
            return "relayed" if last_stage == "relay_delivery" and last_status == "relayed" else "relay_skipped"
        if action == "reply":
            if last_stage == "delivery" and last_status == "delivered":
                return "delivered"
            if last_stage == "delivery" and last_status == "skipped":
                return "reply_skipped"
        return last_status or action or "unknown"

    def _derive_delivery_mode(self, *, action: str, delivery_target_id: str) -> str:
        if action == "relay":
            return "relay"
        if action == "manual_reply":
            return "manual_reply"
        if action == "reply" and delivery_target_id:
            return "reply"
        return "none"

    def _store_recent_trace_entry(self, entry: dict[str, Any]) -> None:
        self.plugin._recent_pipeline_traces = ([entry] + list(self.plugin._recent_pipeline_traces))[:20]

    def get_recent_pipeline_traces(self) -> list[dict[str, Any]]:
        return list(self.plugin._recent_pipeline_traces)

    async def fetch_login_status_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": "offline", "self_id": None, "nickname": None, "last_error": None}
        if not self.plugin.qq_client or not self.plugin.qq_client.is_connected():
            return payload
        try:
            login_info = await self.plugin.qq_client.get_login_info()
            payload["status"] = "online"
            payload["self_id"] = str(login_info.get("user_id") or "") or None
            payload["nickname"] = login_info.get("nickname") or None
            return payload
        except Exception as e:
            payload["status"] = "error"
            payload["last_error"] = str(e)
            return payload

    async def refresh_actual_contacts_cache(self) -> dict[str, Any]:
        if not self.plugin.qq_client:
            raise RuntimeError(self.plugin.i18n.t("errors.qq_client_not_initialized", default="QQ 客户端未初始化"))
        parsed = urlparse(str(self.plugin.qq_client.onebot_url or "").strip())
        if parsed.scheme not in {"ws", "wss"}:
            raise RuntimeError(self.plugin.i18n.t("errors.invalid_onebot_url", default="请先填写合法的 OneBot 地址，必须以 ws:// 或 wss:// 开头"))
        if not parsed.netloc:
            raise RuntimeError(self.plugin.i18n.t("errors.invalid_onebot_url", default="请先填写合法的 OneBot 地址，必须以 ws:// 或 wss:// 开头"))
        if not self.plugin.qq_client.is_connected():
            await self.plugin.qq_client.connect()
        return {
            "friends": await self.plugin.qq_client.get_friend_list(),
            "groups": await self.plugin.qq_client.get_group_list(),
            "refreshed_at": int(time.time()),
        }
