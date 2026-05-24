from __future__ import annotations

from typing import Any

from .strategy_parser import STS2StrategyParser


class STS2StrategyRepository:
    _CHARACTER_ID_TO_STRATEGY = {
        "IRONCLAD": "ironclad",
        "THE_IRONCLAD": "ironclad",
        "DEFECT": "defect",
        "THE_DEFECT": "defect",
        "SILENT": "silent_hunter",
        "THE_SILENT": "silent_hunter",
        "SILENT_HUNTER": "silent_hunter",
        "NECROBINDER": "necrobinder",
        "REGENT": "regent",
    }

    def __init__(self, logger: Any, preference_store: Any, *, default_strategy: str = "defect") -> None:
        self.logger = logger
        self._preference_store = preference_store
        self._default_strategy = default_strategy
        self._parser = STS2StrategyParser(logger)

    def build_context(self, snapshot: dict[str, Any], *, strategy_name: str | None = None) -> dict[str, Any]:
        classification = snapshot.get("classification") if isinstance(snapshot.get("classification"), dict) else {}
        summary_context = snapshot.get("summary_context") if isinstance(snapshot.get("summary_context"), dict) else {}
        screen = str(snapshot.get("screen") or "unknown").strip().lower()
        strategy = self._resolve_strategy_name(strategy_name or self.strategy_for_snapshot(snapshot))
        scene_name = self._scene_name_for_screen(screen)
        event_override = self._event_override(summary_context)
        enemy_override = self._enemy_override(summary_context)
        strategy_prompt = self._parser.load_prompt(strategy, scene_name)
        state_strategy_prompt = self._parser.prompt_for_state(strategy, screen)
        strategy_constraints = self._parser.load_constraints(strategy, scene_name)
        preferences = self._preferences_for_screen(screen, summary_context)
        strategy_directives = self._build_strategy_directives(screen, strategy_constraints, preferences)
        override_text = self._override_text(event_override, enemy_override)
        if override_text:
            strategy_prompt = f"{strategy_prompt}\n\n## 用户指点覆盖\n{override_text}" if strategy_prompt else f"## 用户指点覆盖\n{override_text}"
            state_strategy_prompt = f"{state_strategy_prompt}\n\n## 用户指点覆盖\n{override_text}" if state_strategy_prompt else f"## 用户指点覆盖\n{override_text}"
        return {
            "strategy_name": strategy,
            "screen": screen,
            "scene_name": scene_name,
            "screen_class": classification.get("screen_class", "unknown"),
            "strategy_prompt": strategy_prompt,
            "state_strategy_prompt": state_strategy_prompt,
            "strategy_constraints": strategy_constraints,
            "strategy_directives": strategy_directives,
            "preferences": preferences,
            "event_override": event_override,
            "enemy_override": enemy_override,
        }

    def _build_strategy_directives(
        self,
        screen: str,
        strategy_constraints: dict[str, Any],
        preferences: dict[str, Any],
    ) -> dict[str, Any]:
        directives: dict[str, Any] = {
            "must": [],
            "prefer": [],
            "avoid": [],
            "screen_specific_rules": {},
        }
        for key in ("must", "prefer", "avoid"):
            raw_items = strategy_constraints.get(key) if isinstance(strategy_constraints, dict) else None
            if isinstance(raw_items, list):
                directives[key] = [str(item).strip() for item in raw_items if str(item).strip()]
        if isinstance(strategy_constraints, dict):
            screen_rules = strategy_constraints.get(screen)
            if isinstance(screen_rules, dict):
                directives["screen_specific_rules"] = dict(screen_rules)
        preferred_option_index = self._preferred_option_index(preferences)
        if preferred_option_index is not None:
            directives["preferred_option_index"] = preferred_option_index
        return directives

    def _preferred_option_index(self, preferences: dict[str, Any]) -> int | None:
        record = preferences.get("record") if isinstance(preferences.get("record"), dict) else None
        if isinstance(record, dict):
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            preferred = value.get("preferred_option_index")
            try:
                if preferred is not None:
                    return int(preferred)
            except Exception:
                return None
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        for item in records:
            if not isinstance(item, dict):
                continue
            value = item.get("value") if isinstance(item.get("value"), dict) else {}
            preferred = value.get("preferred_option_index")
            try:
                if preferred is not None:
                    return int(preferred)
            except Exception:
                continue
        return None

    def strategy_for_snapshot(self, snapshot: dict[str, Any]) -> str:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        character_id = run.get("character_id") or snapshot.get("character") or raw_state.get("character_id")
        if character_id is None:
            return self._resolve_strategy_name(self._default_strategy)
        normalized_id = str(character_id).strip().upper()
        strategy = self._CHARACTER_ID_TO_STRATEGY.get(normalized_id, self._default_strategy)
        return self._resolve_strategy_name(strategy)

    def _resolve_strategy_name(self, strategy_name: Any) -> str:
        normalized = self._parser.normalize_strategy_name(strategy_name or self._default_strategy)
        available = set(self._parser.available_strategies())
        if normalized in available:
            return normalized
        fallback = self._parser.normalize_strategy_name(self._default_strategy)
        if fallback in available:
            return fallback
        if available:
            if normalized:
                self.logger.warning(f"策略 {normalized} 不存在，回退到 {fallback if fallback in available else sorted(available)[0]}")
            return fallback if fallback in available else sorted(available)[0]
        return normalized or "defect"

    def _scene_name_for_screen(self, screen: str) -> str:
        scene_map = {
            "combat": "combat",
            "event": "event",
            "map": "map",
            "reward": "reward",
            "card_selection_reward": "reward",
            "selection": "reward",
            "card_selection": "reward",
            "card_selection_unusefull": "remove",
            "card_selection_delet": "remove",
            "shop": "shop",
            "shop_show": "shop",
            "rest": "remove",
            "chest": "reward",
        }
        return scene_map.get(str(screen or "").strip().lower(), "combat")

    def _preferences_for_screen(self, screen: str, summary_context: dict[str, Any]) -> dict[str, Any]:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        if screen == "event":
            event_id = payload.get("event_id")
            return {
                "domain": "event_preferences",
                "record": self._safe_get("event_preferences", event_id),
            }
        if screen in {"reward", "card_selection_reward"}:
            return {
                "domain": "card_reward_preferences",
                "records": self._preference_store.list_domain("card_reward_preferences"),
            }
        if screen in {"card_selection_delet", "card_selection_unusefull", "selection", "card_selection"}:
            return {
                "domain": "card_remove_preferences",
                "records": self._preference_store.list_domain("card_remove_preferences"),
            }
        if screen in {"map", "shop", "shop_show", "rest", "chest"}:
            return {
                "domain": "route_preferences",
                "records": self._preference_store.list_domain("route_preferences"),
            }
        if screen == "combat":
            return {
                "domain": "combat_preferences",
                "records": self._preference_store.list_domain("combat_preferences"),
            }
        return {"domain": None, "records": []}

    def _event_override(self, summary_context: dict[str, Any]) -> dict[str, Any] | None:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        event_id = payload.get("event_id")
        return self._safe_get("event_overrides", event_id)

    def _enemy_override(self, summary_context: dict[str, Any]) -> dict[str, Any] | None:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        for enemy in enemies:
            if not isinstance(enemy, dict):
                continue
            enemy_id = enemy.get("enemy_id") or enemy.get("id") or enemy.get("name")
            record = self._safe_get("enemy_overrides", enemy_id)
            if record is not None:
                return record
        return None

    def _override_text(self, event_override: dict[str, Any] | None, enemy_override: dict[str, Any] | None) -> str:
        for record in (event_override, enemy_override):
            if not isinstance(record, dict):
                continue
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            instruction = str(value.get("instruction") or "").strip()
            if instruction:
                return instruction
        return ""

    def _safe_get(self, domain: str, key: Any) -> dict[str, Any] | None:
        if key in (None, ""):
            return None
        try:
            return self._preference_store.get(domain, str(key))
        except Exception:
            return None


__all__ = ["STS2StrategyRepository"]
