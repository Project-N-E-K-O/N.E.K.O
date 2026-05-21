from __future__ import annotations

import re
from typing import Any


class STS2PreferenceExtractor:
    def extract(self, text: str, *, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        raw_text = str(text or "").strip()
        if not raw_text:
            return None
        lowered = raw_text.lower()
        context = context if isinstance(context, dict) else {}
        screen = str(context.get("screen") or "").strip().lower()

        if screen == "event" or any(token in lowered for token in ["事件", "event"]):
            event_id = self._context_value(context, "event_id")
            if event_id:
                return {
                    "domain": "event_overrides",
                    "key": str(event_id),
                    "value": {"instruction": raw_text},
                }

        if screen == "combat" or any(token in lowered for token in ["战斗", "打牌", "出牌", "保血", "斩杀", "护盾", "能量"]):
            enemy_id = self._primary_enemy_id(context)
            if enemy_id:
                return {
                    "domain": "enemy_overrides",
                    "key": str(enemy_id),
                    "value": {"instruction": raw_text},
                }
            return {
                "domain": "combat_preferences",
                "key": self._generic_key(context, fallback="combat_default"),
                "value": {"instruction": raw_text},
            }

        if screen in {"reward", "card_selection_reward"} or any(token in lowered for token in ["奖励", "reward", "拿卡", "选牌"]):
            return {
                "domain": "card_reward_preferences",
                "key": self._generic_key(context, fallback="reward_default"),
                "value": {"instruction": raw_text},
            }

        if screen in {"card_selection_delet", "card_selection_unusefull", "selection", "card_selection"} or any(token in lowered for token in ["删牌", "删除", "移除", "消耗", "弃牌"]):
            return {
                "domain": "card_remove_preferences",
                "key": self._generic_key(context, fallback="remove_default"),
                "value": {"instruction": raw_text},
            }

        if screen in {"map", "shop", "shop_show", "rest", "chest"} or any(token in lowered for token in ["路线", "地图", "商店", "休息", "宝箱", "精英"]):
            return {
                "domain": "route_preferences",
                "key": self._generic_key(context, fallback="route_default"),
                "value": {"instruction": raw_text},
            }

        return None

    def _primary_enemy_id(self, context: dict[str, Any]) -> Any:
        payload = context.get("payload") if isinstance(context.get("payload"), dict) else {}
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        for enemy in enemies:
            if not isinstance(enemy, dict):
                continue
            enemy_id = enemy.get("enemy_id") or enemy.get("id") or enemy.get("name")
            if enemy_id:
                return enemy_id
        return None


    def _generic_key(self, context: dict[str, Any], *, fallback: str) -> str:
        screen = str(context.get("screen") or "").strip().lower()
        floor = context.get("floor")
        if floor is not None:
            return f"{screen or fallback}:floor:{floor}"
        return screen or fallback

    def _context_value(self, context: dict[str, Any], key: str) -> Any:
        if key in context:
            return context.get(key)
        payload = context.get("payload") if isinstance(context.get("payload"), dict) else {}
        if key in payload:
            return payload.get(key)
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        if key in event:
            return event.get(key)
        return None


__all__ = ["STS2PreferenceExtractor"]
