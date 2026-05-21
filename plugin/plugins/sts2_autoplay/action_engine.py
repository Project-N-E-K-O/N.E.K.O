from __future__ import annotations

from typing import Any

from .planner_interface import PlannedOperation


class STS2ActionEngine:
    def __init__(self, i18n: Any = None) -> None:
        self._i18n = i18n

    def t(self, key: str, *, default: str = "", **params: Any) -> str:
        if self._i18n is not None:
            return self._i18n.t(key, default=default, **params)
        return default.format(**params) if params and default else (default or key)

    def validate(self, snapshot: dict[str, Any], operation: dict[str, Any] | PlannedOperation | None) -> dict[str, Any] | None:
        if operation is None:
            return None
        planned = operation.as_dict() if hasattr(operation, "as_dict") else dict(operation)
        action_type = str(planned.get("action_type") or "").strip()
        if not action_type:
            return None

        registry = snapshot.get("action_registry") if isinstance(snapshot.get("action_registry"), list) else []
        action = self._find_action(registry, snapshot, action_type=action_type, action_id=planned.get("action_id"))
        if action is None:
            return None

        kwargs = planned.get("kwargs") if isinstance(planned.get("kwargs"), dict) else {}
        normalized_kwargs = self._normalize_kwargs(action, kwargs)
        return {
            "action_id": action.get("id") or "",
            "action_type": action_type,
            "category": action.get("category") or "general",
            "kwargs": normalized_kwargs,
            "source": planned.get("source") or "unknown",
            "confidence": planned.get("confidence"),
            "reason": planned.get("reason") or "",
            "candidate_actions": list(planned.get("candidate_actions") or []),
            "action": action,
        }

    def validate_with_feedback(self, snapshot: dict[str, Any], operation: dict[str, Any] | PlannedOperation | None) -> tuple[dict[str, Any] | None, str]:
        if operation is None:
            return None, "missing operation"
        planned = operation.as_dict() if hasattr(operation, "as_dict") else dict(operation)
        action_type = str(planned.get("action_type") or "").strip()
        if not action_type:
            return None, "missing action_type"
        registry = snapshot.get("action_registry") if isinstance(snapshot.get("action_registry"), list) else []
        action = self._find_action(registry, snapshot, action_type=action_type, action_id=planned.get("action_id"))
        if action is None:
            return None, f"illegal action: {action_type}"
        kwargs = planned.get("kwargs") if isinstance(planned.get("kwargs"), dict) else {}
        normalized_kwargs = self._normalize_kwargs(action, kwargs)
        validated = {
            "action_id": action.get("id") or "",
            "action_type": action_type,
            "category": action.get("category") or "general",
            "kwargs": normalized_kwargs,
            "source": planned.get("source") or "unknown",
            "confidence": planned.get("confidence"),
            "reason": planned.get("reason") or "",
            "candidate_actions": list(planned.get("candidate_actions") or []),
            "action": action,
        }
        return validated, ""

    async def execute(self, client: Any, snapshot: dict[str, Any], operation: dict[str, Any] | PlannedOperation | None) -> dict[str, Any]:
        validated = self.validate(snapshot, operation)
        if validated is None:
            message = self.t("action_engine.no_legal_action", default="当前没有可执行的合法动作。")
            return {
                "status": "idle",
                "message": message,
                "summary": message,
                "executed": False,
            }
        result = await client.execute_action(validated["action_type"], **validated["kwargs"])
        message = self.t("action_engine.executed_action", default="已执行动作: {action_type}", action_type=validated["action_type"])
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "executed": True,
            "operation": validated,
            "result": result,
        }

    def _find_action(self, registry: list[dict[str, Any]], snapshot: dict[str, Any], *, action_type: str, action_id: Any) -> dict[str, Any] | None:
        normalized_id = str(action_id or "").strip()
        if normalized_id:
            for action in registry:
                if str(action.get("id") or "") == normalized_id:
                    return action
        target = str(action_type or "").strip().lower()
        for action in registry:
            if str(action.get("type") or "").strip().lower() == target:
                return action

        actions = snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else []
        for action in actions:
            if not isinstance(action, dict):
                continue
            raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            candidate = str(action.get("type") or raw.get("name") or raw.get("action") or "").strip().lower()
            if candidate == target:
                return action
        return None

    def _normalize_kwargs(self, action: dict[str, Any], kwargs: dict[str, Any]) -> dict[str, Any]:
        raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
        default_kwargs = action.get("default_kwargs") if isinstance(action.get("default_kwargs"), dict) else {}
        action_type = str(action.get("type") or raw.get("name") or raw.get("action") or "").strip().lower()
        normalized = dict(default_kwargs)
        normalized.update(kwargs)
        if action_type in {"choose_event_option", "choose_map_node", "choose_rest_option", "choose_reward_card", "select_deck_card", "claim_reward", "buy_card", "buy_relic", "buy_potion"}:
            if "option_index" not in normalized and "index" in raw:
                normalized["option_index"] = raw["index"]
        if action_type == "play_card" and "card_index" not in normalized and "card_index" in raw:
            normalized["card_index"] = raw["card_index"]
        if action_type == "play_card":
            target_index = normalized.get("target_index") if "target_index" in normalized else raw.get("target_index")
            if target_index is None:
                fallback_target = self._fallback_target_index(action)
                if fallback_target is not None:
                    normalized["target_index"] = fallback_target
            elif "target_index" not in normalized:
                normalized["target_index"] = target_index
        return normalized

    def _fallback_target_index(self, action: dict[str, Any]) -> int | None:
        raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
        card = raw.get("card") if isinstance(raw.get("card"), dict) else {}
        valid_target_indices = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
        if not valid_target_indices:
            return None
        try:
            return int(valid_target_indices[0])
        except Exception:
            return None


__all__ = ["STS2ActionEngine"]
