from __future__ import annotations

import random
from typing import Any

from .pipeline_models import QQReplyDecision, QQReplyRequest


class QQReplyDecisionNode:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def decide(self, request: QQReplyRequest) -> QQReplyDecision:
        if request.force_reply:
            permission_level = str(request.permission_level_override or ("open" if request.is_group else "trusted"))
            return QQReplyDecision(action="reply", permission_level=permission_level)
        if request.permission_level_override:
            return QQReplyDecision(action="reply", permission_level=str(request.permission_level_override))
        if request.is_group:
            return self._decide_group(request)
        return self._decide_private(request)

    def _attention_state(self, group_id: str) -> dict[str, Any]:
        service = getattr(self.plugin, "attention_service", None)
        if not service:
            return {"enabled": False, "group_score": 0.0, "focus_group_id": "", "focus_score": 0.0, "focus_reason": "", "multiplier": 1.0}
        snapshot = service.get_snapshot()
        group_state = service.get_state(group_id)
        multiplier = service.get_group_multiplier(group_id)
        return {
            "enabled": bool(snapshot.get("enabled", False)),
            "group_score": float(getattr(group_state, "attention_score", 0.0) or 0.0),
            "focus_group_id": str(snapshot.get("focus_group_id") or ""),
            "focus_score": float(snapshot.get("focus_score") or 0.0),
            "focus_reason": str(snapshot.get("focus_reason") or ""),
            "multiplier": float(multiplier or 1.0),
        }

    def _decision_kwargs(self, request: QQReplyRequest, group_level: str, attention: dict[str, Any]) -> dict[str, Any]:
        return {
            "permission_level": group_level,
            "attention_enabled": attention["enabled"],
            "attention_score": attention["group_score"],
            "attention_focus_group_id": attention["focus_group_id"],
            "attention_focus_score": attention["focus_score"],
            "attention_multiplier": attention["multiplier"],
        }

    def _decide_private(self, request: QQReplyRequest) -> QQReplyDecision:
        # 开放平台：全部回复，但保留实际权限级别（管理员=主人、其他=用户）
        if self.plugin.qq_client and not self.plugin.qq_client.needs_attention:
            real_level = self.plugin.permission_mgr.get_permission_level(request.sender_id) if self.plugin.permission_mgr else "none"
            return QQReplyDecision(action="reply", permission_level=real_level if real_level != "none" else "open")
        permission_level = self.plugin.permission_mgr.get_permission_level(request.sender_id) if self.plugin.permission_mgr else "none"
        if permission_level == "none":
            return QQReplyDecision(action="ignore", permission_level=permission_level)
        if permission_level == "normal":
            relay_probability = self.plugin.permission_mgr.get_normal_relay_probability(request.sender_id) if self.plugin.permission_mgr else None
            return QQReplyDecision(action="relay", permission_level=permission_level, relay_probability=relay_probability)
        return QQReplyDecision(action="reply", permission_level=permission_level)

    def _decide_group(self, request: QQReplyRequest) -> QQReplyDecision:
        group_id = str(request.group_id or "").strip()
        attention = self._attention_state(group_id)

        # 猫娘动态主策略：注意力门控已在 dispatcher 层处理，此处补充群权限门控
        strategy_mode = getattr(self.plugin, "_strategy_mode", "neko_dynamic")
        if strategy_mode == "neko_dynamic":
            # 即使注意力门控已放行，仍需校验群是否在权限列表中（阻止未配置/已移除的群绕过）
            group_level = self.plugin.group_permission_mgr.get_group_level(group_id) if self.plugin.group_permission_mgr else "none"
            if group_level == "none":
                return QQReplyDecision(action="ignore", **self._decision_kwargs(request, group_level, attention), attention_gate_reason="permission_none")
            if group_level == "normal":
                relay_probability = self.plugin.group_permission_mgr.get_normal_relay_probability(group_id) if self.plugin.group_permission_mgr else None
                return QQReplyDecision(action="relay", relay_probability=relay_probability, **self._decision_kwargs(request, group_level, attention), attention_gate_reason="relay")
            kwargs = self._decision_kwargs(request, group_level, attention)
            kwargs["attention_gate_reason"] = "attention_gate"
            return QQReplyDecision(action="reply", **kwargs)

        # N.E.K.O 退级策略：原有完整权限门控
        group_level = self.plugin.group_permission_mgr.get_group_level(group_id) if self.plugin.group_permission_mgr else "none"
        if group_level == "none":
            return QQReplyDecision(action="ignore", **self._decision_kwargs(request, group_level, attention), attention_gate_reason="permission_none")
        if group_level == "normal":
            relay_probability = self.plugin.group_permission_mgr.get_normal_relay_probability(group_id) if self.plugin.group_permission_mgr else None
            return QQReplyDecision(action="relay", relay_probability=relay_probability, **self._decision_kwargs(request, group_level, attention), attention_gate_reason="relay")
        if attention["enabled"] and group_id and attention["focus_group_id"] and attention["focus_group_id"] != group_id and attention["multiplier"] <= 0.0 and not request.is_at_bot:
            return QQReplyDecision(action="ignore", **self._decision_kwargs(request, group_level, attention), attention_gate_reason="attention_focus_other_group")
        if group_level == "trusted" and not request.is_at_bot:
            if attention["enabled"] and attention["multiplier"] < 0.9:
                return QQReplyDecision(action="ignore", **self._decision_kwargs(request, group_level, attention), attention_gate_reason="attention_not_focused")
            return QQReplyDecision(action="ignore", **self._decision_kwargs(request, group_level, attention), attention_gate_reason="trusted_no_at")
        if group_level == "open" and not request.is_at_bot:
            if request.suppression_reason:
                return QQReplyDecision(action="ignore", **self._decision_kwargs(request, group_level, attention), attention_gate_reason=request.suppression_reason)
            reply_probability = self.plugin.group_permission_mgr.get_open_reply_probability(group_id) if self.plugin.group_permission_mgr else None
            effective_reply_probability = self.plugin._truth_reply_probability if reply_probability is None else reply_probability
            effective_reply_probability *= max(0.0, float(attention["multiplier"] or 1.0))
            if effective_reply_probability <= 0.0 or random.random() >= effective_reply_probability:
                return QQReplyDecision(action="ignore", **self._decision_kwargs(request, group_level, attention), attention_gate_reason="probability_gate")
            return QQReplyDecision(action="reply", **self._decision_kwargs(request, group_level, attention), attention_gate_reason="probability_pass")
        return QQReplyDecision(action="reply", **self._decision_kwargs(request, group_level, attention), attention_gate_reason="at_bot_or_explicit")