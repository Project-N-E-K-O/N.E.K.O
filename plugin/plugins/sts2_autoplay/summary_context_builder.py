from __future__ import annotations

from time import time
from typing import Any

from .situation_models import SituationSnapshotSummary


class STS2SummaryContextBuilder:
    def build(self, snapshot: dict[str, Any], runtime_state: Any | None = None) -> dict[str, Any]:
        classification = snapshot.get("classification") if isinstance(snapshot.get("classification"), dict) else {}
        summary_kind = str(classification.get("summary_kind") or "general")
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}

        builder = getattr(self, f"_build_{summary_kind}_context", self._build_general_context)
        payload = builder(snapshot, raw_state)
        context = {
            "summary_kind": summary_kind,
            "screen": snapshot.get("screen", "unknown"),
            "classification": classification,
            "payload": payload,
        }
        if runtime_state is not None:
            context["continuous_delta"] = dict(runtime_state.latest_continuous_delta)
            context["action_frame"] = dict(runtime_state.latest_action_frame)
        return context

    def build_snapshot_summary(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        classification = snapshot.get("classification") if isinstance(snapshot.get("classification"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        summary = SituationSnapshotSummary(
            screen=str(snapshot.get("screen") or "unknown"),
            summary_kind=str(classification.get("summary_kind") or "general"),
            floor=self._safe_int(snapshot.get("floor") if snapshot.get("floor") is not None else run.get("floor")),
            act=self._safe_int(snapshot.get("act") if snapshot.get("act") is not None else run.get("act")),
            turn=self._safe_int(raw_state.get("turn") if raw_state.get("turn") is not None else combat.get("turn")),
            in_combat=bool(snapshot.get("in_combat", False) or raw_state.get("in_combat", False)),
            player={
                "current_hp": self._safe_int(player.get("current_hp") if player.get("current_hp") is not None else run.get("current_hp")),
                "max_hp": self._safe_int(player.get("max_hp") if player.get("max_hp") is not None else run.get("max_hp")),
                "block": self._safe_int(player.get("block"), default=0),
                "energy": self._safe_int(player.get("energy"), default=0),
                "gold": self._safe_int(run.get("gold"), default=0),
            },
            hand={
                "count": len(hand),
                "names": [str(card.get("name") or card.get("card_id") or "?") for card in hand if isinstance(card, dict)],
            },
            enemies={
                "count": len([enemy for enemy in enemies if isinstance(enemy, dict)]),
                "total_hp": sum(self._safe_int(enemy.get("current_hp") if enemy.get("current_hp") is not None else enemy.get("hp"), default=0) for enemy in enemies if isinstance(enemy, dict)),
                "attack_total": sum(self._safe_int(enemy.get("intent_damage"), default=0) for enemy in enemies if isinstance(enemy, dict)),
                "names": [str(enemy.get("name") or enemy.get("enemy_id") or "敌人") for enemy in enemies if isinstance(enemy, dict)],
            },
            available_actions=self._available_action_names(snapshot),
            captured_at=float(snapshot.get("polled_at") or time()),
        )
        return summary.as_dict()

    def build_tactical_signals(self, snapshot: dict[str, Any], summary_context: dict[str, Any], strategy_context: dict[str, Any]) -> dict[str, Any]:
        classification = snapshot.get("classification") if isinstance(snapshot.get("classification"), dict) else {}
        state_name = str(classification.get("state_name") or snapshot.get("screen") or "unknown")
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        signals = {
            "state_name": state_name,
            "summary_kind": classification.get("summary_kind"),
            "sync_priority": classification.get("sync_priority"),
            "action_family": classification.get("action_family"),
        }
        if state_name == "combat":
            player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
            enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
            current_hp = self._safe_int(player.get("current_hp"), default=0) or 0
            max_hp = self._safe_int(player.get("max_hp"), default=0) or 0
            block = self._safe_int(player.get("block"), default=0) or 0
            energy = self._safe_int(player.get("energy"), default=0) or 0
            incoming_attack_total = sum(self._safe_int(enemy.get("intent_damage"), default=0) or 0 for enemy in enemies if isinstance(enemy, dict))
            remaining_block_needed = max(0, incoming_attack_total - block)
            signals.update(
                {
                    "incoming_attack_total": incoming_attack_total,
                    "remaining_block_needed": remaining_block_needed,
                    "projected_survival_risk": "high" if current_hp > 0 and remaining_block_needed >= current_hp else ("medium" if remaining_block_needed > 0 else "low"),
                    "energy": energy,
                    "player_hp_ratio": (current_hp / max_hp) if max_hp > 0 else 0.0,
                    "enemy_count": len([enemy for enemy in enemies if isinstance(enemy, dict)]),
                }
            )
        deck = payload.get("deck") if isinstance(payload.get("deck"), dict) else {}
        relics = payload.get("relics") if isinstance(payload.get("relics"), list) else []
        potions = payload.get("potions") if isinstance(payload.get("potions"), list) else []
        if deck:
            signals["deck_card_count"] = self._safe_int(deck.get("card_count"), default=0) or 0
            signals["archetype_tags"] = [tag for tag in self._infer_archetype_tags(deck) if tag]
        if relics:
            signals["relic_names"] = [str(item.get("name") or item.get("relic_id") or "?") for item in relics if isinstance(item, dict)]
        if potions:
            signals["potion_names"] = [str(item.get("name") or item.get("potion_id") or "?") for item in potions if isinstance(item, dict)]


        if state_name in {"reward", "card_selection_reward", "card_selection", "card_selection_unusefull", "card_selection_delet"}:
            reward = payload.get("reward") if isinstance(payload.get("reward"), dict) else {}
            cards = reward.get("cards") if isinstance(reward.get("cards"), list) else []
            selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
            candidate_cards = cards or (selection.get("cards") if isinstance(selection.get("cards"), list) else [])
            signals["reward_option_count"] = len(candidate_cards)
            signals["reward_card_names"] = [
                str(card.get("name") or card.get("card_id") or "?")
                for card in candidate_cards
                if isinstance(card, dict)
            ]
        if state_name in {"shop", "shop_show"}:
            shop = payload.get("shop") if isinstance(payload.get("shop"), dict) else {}
            cards = shop.get("cards") if isinstance(shop.get("cards"), list) else []
            relics = shop.get("relics") if isinstance(shop.get("relics"), list) else []
            signals["shop_card_count"] = len(cards)
            signals["shop_relic_count"] = len(relics)
            signals["shop_card_names"] = [str(card.get("name") or card.get("card_id") or "?") for card in cards if isinstance(card, dict)]
            signals["shop_relic_names"] = [str(relic.get("name") or relic.get("relic_id") or "?") for relic in relics if isinstance(relic, dict)]
        if state_name == "map":
            travelable_nodes = payload.get("travelable_nodes") if isinstance(payload.get("travelable_nodes"), list) else []
            future_nodes = payload.get("future_nodes") if isinstance(payload.get("future_nodes"), list) else []
            signals["travelable_node_count"] = len(travelable_nodes)
            signals["travelable_node_types"] = [str(node.get("type") or node.get("node_type") or "?") for node in travelable_nodes if isinstance(node, dict)]
            signals["future_node_types"] = [str(node.get("type") or node.get("node_type") or "?") for node in future_nodes if isinstance(node, dict)]
        if state_name == "rest":
            rest = payload.get("rest") if isinstance(payload.get("rest"), dict) else {}
            options = rest.get("options") if isinstance(rest.get("options"), list) else []
            signals["rest_option_count"] = len(options)
            signals["rest_option_types"] = [str(option.get("type") or option.get("name") or "?") for option in options if isinstance(option, dict)]
        if state_name == "event":
            event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
            options = event.get("options") if isinstance(event.get("options"), list) else []
            signals["event_option_count"] = len(options)
            signals["event_option_labels"] = [str(option.get("label") or option.get("text") or "?") for option in options if isinstance(option, dict)]
        strategy_name = strategy_context.get("strategy_name") if isinstance(strategy_context, dict) else None
        if strategy_name:
            signals["strategy_name"] = strategy_name
        return signals

    def _build_general_context(self, snapshot: dict[str, Any], raw_state: dict[str, Any]) -> dict[str, Any]:
        session = raw_state.get("session") if isinstance(raw_state.get("session"), dict) else {}
        return {
            "run_id": snapshot.get("run_id"),
            "screen": snapshot.get("screen"),
            "phase": session.get("phase"),
            "available_actions": self._available_action_names(snapshot),
        }

    def _build_menu_context(self, snapshot: dict[str, Any], raw_state: dict[str, Any]) -> dict[str, Any]:
        session = raw_state.get("session") if isinstance(raw_state.get("session"), dict) else {}
        character_select = raw_state.get("character_select") if isinstance(raw_state.get("character_select"), dict) else {}
        timeline = raw_state.get("timeline") if isinstance(raw_state.get("timeline"), dict) else {}
        return {
            "phase": session.get("phase"),
            "available_actions": self._available_action_names(snapshot),
            "selected_character_id": character_select.get("selected_character_id"),
            "can_embark": character_select.get("can_embark"),
            "ascension": character_select.get("ascension"),
            "character_select": character_select,
            "timeline": timeline,
        }

    def _build_event_context(self, snapshot: dict[str, Any], raw_state: dict[str, Any]) -> dict[str, Any]:
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        event = raw_state.get("event") if isinstance(raw_state.get("event"), dict) else {}
        return {
            "floor": run.get("floor"),
            "current_hp": run.get("current_hp"),
            "max_hp": run.get("max_hp"),
            "gold": run.get("gold"),
            "event_id": event.get("event_id") or event.get("id"),
            "event_name": event.get("name") or event.get("title"),
            "event_options": event.get("options") if isinstance(event.get("options"), list) else [],
            "available_actions": self._available_action_names(snapshot),
            "event": event,
        }

    def _build_map_context(self, snapshot: dict[str, Any], raw_state: dict[str, Any]) -> dict[str, Any]:
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        game_map = raw_state.get("map") if isinstance(raw_state.get("map"), dict) else {}
        travelable_nodes = [
            node for node in (game_map.get("nodes") if isinstance(game_map.get("nodes"), list) else [])
            if isinstance(node, dict) and bool(node.get("is_available"))
        ]
        return {
            "floor": run.get("floor"),
            "current_hp": run.get("current_hp"),
            "max_hp": run.get("max_hp"),
            "gold": run.get("gold"),
            "current_node": game_map.get("current_node"),
            "travelable_nodes": travelable_nodes,
            "travelable_node_types": [str(node.get("type") or node.get("node_type") or "?") for node in travelable_nodes if isinstance(node, dict)],
            "travelable_node_indices": [self._safe_int(node.get("index"), default=index) for index, node in enumerate(travelable_nodes) if isinstance(node, dict)],
            "future_nodes": game_map.get("future_nodes") if isinstance(game_map.get("future_nodes"), list) else [],
            "available_actions": self._available_action_names(snapshot),
        }

    def _build_combat_context(self, snapshot: dict[str, Any], raw_state: dict[str, Any]) -> dict[str, Any]:
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        return {
            "turn": raw_state.get("turn") if raw_state.get("turn") is not None else combat.get("turn"),
            "floor": snapshot.get("floor") if snapshot.get("floor") is not None else raw_state.get("run", {}).get("floor"),
            "act": snapshot.get("act") if snapshot.get("act") is not None else raw_state.get("run", {}).get("act"),
            "player": {
                "current_hp": player.get("current_hp") or player.get("hp"),
                "max_hp": player.get("max_hp"),
                "block": player.get("block"),
                "energy": player.get("energy"),
                "powers": player.get("powers") if isinstance(player.get("powers"), list) else [],
                "orbs": player.get("orbs") if isinstance(player.get("orbs"), list) else [],
            },
            "hand": hand,
            "playable_card_summaries": self._card_summaries(hand),
            "enemies": enemies,
            "deck": raw_state.get("deck") if isinstance(raw_state.get("deck"), dict) else {},
            "relics": raw_state.get("relics") if isinstance(raw_state.get("relics"), list) else [],
            "potions": raw_state.get("potions") if isinstance(raw_state.get("potions"), list) else [],
            "available_actions": self._available_action_names(snapshot),
        }

    def _build_reward_context(self, snapshot: dict[str, Any], raw_state: dict[str, Any]) -> dict[str, Any]:
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        reward = raw_state.get("reward") if isinstance(raw_state.get("reward"), dict) else {}
        cards = reward.get("cards") if isinstance(reward.get("cards"), list) else []
        return {
            "floor": run.get("floor"),
            "current_hp": run.get("current_hp"),
            "max_hp": run.get("max_hp"),
            "gold": run.get("gold"),
            "reward": reward,
            "reward_cards": cards,
            "reward_card_names": [str(card.get("name") or card.get("card_id") or "?") for card in cards if isinstance(card, dict)],
            "reward_card_indices": [self._safe_int(card.get("index"), default=index) for index, card in enumerate(cards) if isinstance(card, dict)],
            "deck": raw_state.get("deck") if isinstance(raw_state.get("deck"), dict) else {},
            "relics": raw_state.get("relics") if isinstance(raw_state.get("relics"), list) else [],
            "potions": raw_state.get("potions") if isinstance(raw_state.get("potions"), list) else [],
            "available_actions": self._available_action_names(snapshot),
        }

    def _build_shop_context(self, snapshot: dict[str, Any], raw_state: dict[str, Any]) -> dict[str, Any]:
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        shop = raw_state.get("shop") if isinstance(raw_state.get("shop"), dict) else {}
        cards = shop.get("cards") if isinstance(shop.get("cards"), list) else []
        relics = shop.get("relics") if isinstance(shop.get("relics"), list) else []
        potions = shop.get("potions") if isinstance(shop.get("potions"), list) else []
        return {
            "floor": run.get("floor"),
            "current_hp": run.get("current_hp"),
            "max_hp": run.get("max_hp"),
            "gold": run.get("gold"),
            "shop": shop,
            "shop_cards": cards,
            "shop_card_names": [str(card.get("name") or card.get("card_id") or "?") for card in cards if isinstance(card, dict)],
            "shop_relics": relics,
            "shop_relic_names": [str(relic.get("name") or relic.get("relic_id") or "?") for relic in relics if isinstance(relic, dict)],
            "shop_potions": potions,
            "shop_potion_names": [str(potion.get("name") or potion.get("potion_id") or "?") for potion in potions if isinstance(potion, dict)],
            "available_actions": self._available_action_names(snapshot),
        }

    def _build_rest_context(self, snapshot: dict[str, Any], raw_state: dict[str, Any]) -> dict[str, Any]:
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        rest = raw_state.get("rest") if isinstance(raw_state.get("rest"), dict) else {}
        options = rest.get("options") if isinstance(rest.get("options"), list) else []
        return {
            "floor": run.get("floor"),
            "current_hp": run.get("current_hp"),
            "max_hp": run.get("max_hp"),
            "rest": rest,
            "rest_options": options,
            "rest_option_types": [str(option.get("type") or option.get("name") or "?") for option in options if isinstance(option, dict)],
            "available_actions": self._available_action_names(snapshot),
        }

    def _build_selection_context(self, snapshot: dict[str, Any], raw_state: dict[str, Any]) -> dict[str, Any]:
        selection = raw_state.get("selection") if isinstance(raw_state.get("selection"), dict) else {}
        reward = raw_state.get("reward") if isinstance(raw_state.get("reward"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        selection_cards = selection.get("cards") if isinstance(selection.get("cards"), list) else []
        return {
            "floor": run.get("floor"),
            "current_hp": run.get("current_hp"),
            "max_hp": run.get("max_hp"),
            "selection": selection,
            "selection_cards": selection_cards,
            "selection_card_names": [str(card.get("name") or card.get("card_id") or "?") for card in selection_cards if isinstance(card, dict)],
            "reward": reward,
            "available_actions": self._available_action_names(snapshot),
        }

    def _infer_archetype_tags(self, deck: dict[str, Any]) -> list[str]:
        cards = deck.get("cards") if isinstance(deck.get("cards"), list) else []
        names = [str(card.get("name") or card.get("card_id") or "").lower() for card in cards if isinstance(card, dict)]
        tags: list[str] = []
        if any(keyword in name for name in names for keyword in ("orb", "zap", "coolheaded", "ball lightning", "电")):
            tags.append("orb_focus")
        if any(keyword in name for name in names for keyword in ("block", "defend", "charge battery", "防")):
            tags.append("defense")
        if any(keyword in name for name in names for keyword in ("draw", "skim", "coolheaded", "抽")):
            tags.append("draw")
        if any(keyword in name for name in names for keyword in ("energy", "turbo", "double energy", "能量")):
            tags.append("energy")
        return tags

    def _card_summaries(self, cards: list[Any]) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for card in cards:
            if not isinstance(card, dict):
                continue
            summaries.append(
                {
                    "name": str(card.get("name") or card.get("card_id") or "?"),
                    "energy_cost": self._safe_int(card.get("energy_cost"), default=0),
                    "star_cost": self._safe_int(card.get("star_cost"), default=0),
                    "costs_x": bool(card.get("costs_x")),
                    "star_costs_x": bool(card.get("star_costs_x")),
                    "requires_target": bool(card.get("requires_target")),
                    "playable": bool(card.get("playable", True)),
                    "effect": str(card.get("resolved_rules_text") or card.get("rules_text") or "").strip()[:40],
                }
            )
        return summaries

    def _available_action_names(self, snapshot: dict[str, Any]) -> list[str]:
        actions = snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else []
        names: list[str] = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            action_name = str(action.get("type") or raw.get("name") or raw.get("action") or "").strip()
            if action_name:
                names.append(action_name)
        return names

    def _safe_int(self, value: Any, default: int | None = None) -> int | None:
        try:
            if value is None:
                return default
            return int(value)
        except Exception:
            return default


__all__ = ["STS2SummaryContextBuilder"]
