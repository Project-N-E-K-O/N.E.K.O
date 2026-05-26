from __future__ import annotations

from hashlib import sha1
from typing import Any


class STS2ActionRegistry:
    def build(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        actions = snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else []
        registered: list[dict[str, Any]] = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            action_type = str(action.get("type") or raw.get("name") or raw.get("action") or "unknown").strip()
            kwargs = self._default_kwargs(raw)
            category = self._category_for(action_type)
            action_id = self._fingerprint(action_type, raw)
            registered.append(
                {
                    "id": action_id,
                    "type": action_type,
                    "category": category,
                    "label": str(action.get("label") or action_type),
                    "raw": raw,
                    "default_kwargs": kwargs,
                    "requires_index": bool(raw.get("requires_index")),
                    "requires_target": bool(raw.get("requires_target")),
                }
            )
        return registered

    def find_by_type(self, registry: list[dict[str, Any]], action_type: str) -> dict[str, Any] | None:
        target = str(action_type or "").strip().lower()
        for action in registry:
            if str(action.get("type") or "").strip().lower() == target:
                return action
        return None

    def find_by_id(self, registry: list[dict[str, Any]], action_id: str) -> dict[str, Any] | None:
        target = str(action_id or "").strip()
        for action in registry:
            if str(action.get("id") or "") == target:
                return action
        return None

    def _category_for(self, action_type: str) -> str:
        normalized = str(action_type or "").strip().lower()
        if normalized == "play_card":
            return "combat"
        if normalized == "end_turn":
            return "combat_end"
        if normalized in {"choose_event_option"}:
            return "event"
        if normalized in {"choose_map_node"}:
            return "map"
        if normalized in {"choose_reward_card", "claim_reward"}:
            return "reward"
        if normalized in {"select_deck_card"}:
            return "selection"
        if normalized in {"buy_card", "buy_relic", "buy_potion", "open_shop_inventory"}:
            return "shop"
        if normalized in {"choose_rest_option"}:
            return "rest"
        if normalized in {"proceed", "confirm", "continue"}:
            return "proceed"
        return "general"

    def _default_kwargs(self, raw: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if "index" in raw:
            kwargs["option_index"] = raw["index"]
        if "card_index" in raw:
            kwargs["card_index"] = raw["card_index"]
        if "target_index" in raw:
            kwargs["target_index"] = raw["target_index"]
        return kwargs

    def _fingerprint(self, action_type: str, raw: dict[str, Any]) -> str:
        payload = f"{action_type}|{raw.get('index')}|{raw.get('card_index')}|{raw.get('target_index')}|{raw.get('name')}"
        return sha1(payload.encode("utf-8")).hexdigest()[:12]


__all__ = ["STS2ActionRegistry"]
