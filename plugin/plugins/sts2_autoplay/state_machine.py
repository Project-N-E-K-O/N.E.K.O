from __future__ import annotations

from typing import Any


class STS2StateMachine:
    _MENU_SCREENS = {"main_menu", "character_select", "timeline"}
    _RUN_NAVIGATION_SCREENS = {"event", "map", "reward", "shop", "shop_show", "rest", "chest"}
    _SELECTION_SCREENS = {
        "selection",
        "card_selection",
        "card_selection_unusefull",
        "card_selection_reward",
        "card_selection_delet",
    }
    _TERMINAL_SCREENS = {"game_over", "victory", "defeat"}

    def classify(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        screen = self._normalized_screen(snapshot)
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        agent_view = raw_state.get("agent_view") if isinstance(raw_state.get("agent_view"), dict) else {}
        session = raw_state.get("session") if isinstance(raw_state.get("session"), dict) else {}
        phase = str(session.get("phase") or agent_view.get("session", {}).get("phase") or "").strip().lower() if isinstance(agent_view.get("session"), dict) else str(session.get("phase") or "").strip().lower()
        in_combat = bool(snapshot.get("in_combat", False) or raw_state.get("in_combat", False))
        available_actions = snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else []
        action_names = [
            str(action.get("type") or action.get("raw", {}).get("name") or "").strip().lower()
            for action in available_actions
            if isinstance(action, dict)
        ]

        screen_class = self._screen_class(screen=screen, phase=phase, in_combat=in_combat)
        action_family = self._action_family(available_actions)
        execution_profile = self._execution_profile(screen_class=screen_class, action_names=action_names)
        sync_priority = self._sync_priority(screen_class=screen_class, state_name=screen, in_combat=in_combat)
        summary_kind = self._summary_kind(screen_class=screen_class, state_name=screen)

        return {
            "state_name": screen,
            "screen_class": screen_class,
            "action_family": action_family,
            "execution_profile": execution_profile,
            "sync_priority": sync_priority,
            "summary_kind": summary_kind,
            "phase": phase or None,
            "in_combat": in_combat,
            "available_action_names": action_names,
        }

    def _normalized_screen(self, snapshot: dict[str, Any]) -> str:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        agent_view = raw_state.get("agent_view") if isinstance(raw_state.get("agent_view"), dict) else {}
        screen = snapshot.get("screen") or agent_view.get("screen") or raw_state.get("screen") or raw_state.get("screen_type") or "unknown"
        return str(screen).strip().lower()

    def _screen_class(self, *, screen: str, phase: str, in_combat: bool) -> str:
        if in_combat or screen == "combat":
            return "combat"
        if screen in self._MENU_SCREENS:
            return "menu"
        if screen in self._RUN_NAVIGATION_SCREENS:
            if screen == "reward":
                return "reward"
            if screen in {"shop", "shop_show"}:
                return "shop"
            if screen == "rest":
                return "rest"
            return "run_navigation"
        if screen in self._SELECTION_SCREENS:
            return "selection"
        if screen in self._TERMINAL_SCREENS:
            return "terminal"
        if screen == "modal":
            return "modal"
        if phase == "menu":
            return "menu"
        if phase == "run":
            return "run_navigation"
        return "unknown"

    def _action_family(self, actions: list[dict[str, Any]]) -> str:
        if not actions:
            return "none"
        requires_index = False
        requires_target = False
        indexless = False
        for action in actions:
            if not isinstance(action, dict):
                continue
            raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            action_requires_index = bool(raw.get("requires_index"))
            action_requires_target = bool(raw.get("requires_target"))
            requires_index = requires_index or action_requires_index
            requires_target = requires_target or action_requires_target
            if not action_requires_index and not action_requires_target:
                indexless = True
        if requires_target:
            return "targeted"
        if requires_index and indexless:
            return "mixed"
        if requires_index:
            return "indexed"
        return "indexless"

    def _execution_profile(self, *, screen_class: str, action_names: list[str]) -> str:
        if screen_class in {"unknown", "terminal"}:
            return "wait_only"
        if any(name in {"play_card", "choose_event_option", "choose_map_node", "choose_reward_card", "select_deck_card"} for name in action_names):
            return "confirm_needed"
        return "safe_auto"

    def _sync_priority(self, *, screen_class: str, state_name: str, in_combat: bool) -> str:
        if screen_class in {"reward", "selection", "shop", "rest"}:
            return "high"
        if screen_class == "run_navigation" and state_name in {"event", "map"}:
            return "high"
        if screen_class == "combat":
            return "high" if in_combat else "medium"
        if screen_class == "menu":
            return "low"
        return "low"

    def _summary_kind(self, *, screen_class: str, state_name: str) -> str:
        if screen_class == "combat":
            return "combat"
        if state_name == "event":
            return "event"
        if state_name == "map":
            return "map"
        if screen_class == "reward":
            return "reward"
        if screen_class == "shop":
            return "shop"
        if screen_class == "rest":
            return "rest"
        if screen_class == "selection":
            return "selection"
        if screen_class == "menu":
            return "menu"
        return "general"


__all__ = ["STS2StateMachine"]
