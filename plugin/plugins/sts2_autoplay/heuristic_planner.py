from __future__ import annotations

from typing import Any

from .planner_interface import PlannedOperation


class STS2HeuristicPlanner:
    def __init__(self, logger: Any | None = None) -> None:
        self._logger = logger

    def _debug(self, message: str, *args: Any) -> None:
        return

    def plan(self, context: dict[str, Any]) -> PlannedOperation | None:
        classification = context.get("classification") if isinstance(context.get("classification"), dict) else {}
        summary_context = context.get("summary_context") if isinstance(context.get("summary_context"), dict) else {}
        strategy_context = context.get("strategy_context") if isinstance(context.get("strategy_context"), dict) else {}
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        state_name = str(classification.get("state_name") or snapshot.get("screen") or "").strip().lower()
        available_actions = snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else []
        preferences = strategy_context.get("preferences") if isinstance(strategy_context.get("preferences"), dict) else {}
        guidance_override = self._guidance_override(context)

        if state_name == "main_menu":
            action = (
                self._find_action(available_actions, "open_character_select")
                or self._find_action(available_actions, "continue_run")
                or self._find_action(available_actions, "open_timeline")
            )
            if action is None:
                action = self._find_action(available_actions, "choose_timeline_epoch")
            if action is not None:
                kwargs = self._main_menu_kwargs(action, summary_context)
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.95, source="heuristic", reason="main_menu_default")

        if state_name == "character_select":
            action = self._find_action(available_actions, "select_character")
            if action is not None:
                selected_index = self._selected_character_index(summary_context)
                kwargs = {"option_index": selected_index} if selected_index is not None else {}
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.9, source="heuristic", reason="character_select_default")

        if state_name == "event":
            action = self._find_action(available_actions, "choose_event_option")
            if action is not None:
                preferred_option = self._preferred_event_option(summary_context, preferences)
                self._debug(
                    "[sts2_event_plan] options=%s chosen=%s payload=%s",
                    summary_context.get("payload", {}).get("event_options") if isinstance(summary_context.get("payload"), dict) else [],
                    preferred_option,
                    summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {},
                )
                kwargs = {"option_index": preferred_option if preferred_option is not None else 0}
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.8, source="heuristic", reason="event_preference_or_default")

        if state_name == "map":
            action = self._find_action(available_actions, "choose_map_node")
            if action is not None:
                node_index = self._preferred_map_index(summary_context, preferences)
                self._debug(
                    "[sts2_map_plan] nodes=%s chosen=%s payload=%s",
                    summary_context.get("payload", {}).get("travelable_nodes") if isinstance(summary_context.get("payload"), dict) else [],
                    node_index,
                    summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {},
                )
                kwargs = {"option_index": node_index if node_index is not None else 0}
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.82, source="heuristic", reason="map_preference_or_default")

        if state_name in {"reward", "card_selection_reward", "card_selection"}:
            action = self._find_action(available_actions, "choose_reward_card") or self._find_action(available_actions, "claim_reward")
            if action is not None:
                reward_candidates = self._reward_candidates(summary_context)
                preferred_option = self._preferred_reward_option(summary_context, preferences)
                self._debug(
                    "[sts2_reward_plan] cards=%s chosen=%s payload=%s",
                    reward_candidates,
                    preferred_option,
                    summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {},
                )
                kwargs = self._reward_kwargs(summary_context, action, preferred_option)
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.72, source="heuristic", reason="reward_preference_or_default")

        if state_name in {"card_selection_unusefull", "card_selection_delet"}:
            action = self._find_action(available_actions, "select_deck_card")
            if action is not None:
                preferred_option = self._preferred_remove_option(summary_context, preferences)
                self._debug(
                    "[sts2_remove_plan] cards=%s chosen=%s payload=%s",
                    summary_context.get("payload", {}).get("selection_cards") if isinstance(summary_context.get("payload"), dict) else [],
                    preferred_option,
                    summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {},
                )
                kwargs = {"option_index": preferred_option} if preferred_option is not None else {}
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.7, source="heuristic", reason="remove_preference_or_default")

        if state_name in {"shop", "shop_show"}:
            action = self._find_action(available_actions, "buy_card") or self._find_action(available_actions, "open_shop_inventory")
            if action is not None:
                preferred_option = self._preferred_route_option(preferences)
                kwargs = {"option_index": preferred_option} if preferred_option is not None and action["type"] == "buy_card" else {}
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.68, source="heuristic", reason="shop_preference_or_default")

        if state_name == "rest":
            action = self._find_action(available_actions, "choose_rest_option")
            if action is not None:
                preferred_option = self._preferred_route_option(preferences)
                kwargs = {"option_index": preferred_option} if preferred_option is not None else {"option_index": 0}
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.74, source="heuristic", reason="rest_preference_or_default")

        if state_name == "chest":
            action = self._find_action(available_actions, "proceed")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.8, source="heuristic", reason="chest_default")

        if state_name == "combat":
            self._debug(
                "[sts2_combat_plan] incoming=%s block=%s energy=%s guidance=%s hand=%s",
                self._incoming_attack_total(snapshot),
                self._current_block(snapshot),
                self._safe_int(snapshot.get("raw_state", {}).get("combat", {}).get("player", {}).get("energy")) or 0,
                guidance_override,
                [
                    {
                        "index": self._safe_int(card.get("index")),
                        "name": str(card.get("name") or card.get("card_id") or "?"),
                        "damage": self._card_damage(card),
                        "block": self._card_block(card),
                        "cost": self._card_energy_cost(card),
                    }
                    for card in (snapshot.get("raw_state", {}).get("combat", {}).get("hand") if isinstance(snapshot.get("raw_state", {}).get("combat", {}).get("hand"), list) else [])
                    if isinstance(card, dict) and bool(card.get("playable"))
                ],
            )
            sequence = self._plan_combat_turn_sequence(snapshot, available_actions, preferences, guidance_override)
            if sequence:
                self._debug("[sts2_combat_plan] chosen_sequence=%s", sequence)
                first = sequence[0]
                kwargs = {"card_index": first["card_index"]}
                if first.get("target_index") is not None:
                    kwargs["target_index"] = first["target_index"]
                return PlannedOperation(
                    action_type="play_card",
                    kwargs=kwargs,
                    confidence=0.82 if first.get("reason") != "combat_lethal_sequence" else 0.98,
                    source="heuristic",
                    reason=str(first.get("reason") or "combat_sequence"),
                )
            action = self._preferred_combat_action(available_actions, snapshot, preferences, guidance_override)
            if action is not None:
                effective_override = dict(guidance_override)
                if not effective_override.get("prefer_attack") and self._incoming_attack_total(snapshot) > self._current_block(snapshot):
                    effective_override["prefer_defense"] = True
                preferred_card_index = self._preferred_combat_card_index(snapshot, preferences, effective_override)
                kwargs = self._combat_kwargs(snapshot, action, preferred_card_index)
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.65, source="heuristic", reason="combat_preference_or_default")

        return None

    def _find_action(self, actions: list[dict[str, Any]], action_name: str) -> dict[str, Any] | None:
        target = str(action_name or "").strip().lower()
        for action in actions:
            if not isinstance(action, dict):
                continue
            raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            candidate = str(action.get("type") or raw.get("name") or raw.get("action") or "").strip().lower()
            if candidate == target:
                return action
        return None

    def _selected_character_index(self, summary_context: dict[str, Any]) -> int | None:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        selected_character_id = payload.get("selected_character_id")
        if not selected_character_id:
            return None
        raw_state = payload.get("character_select") if isinstance(payload.get("character_select"), dict) else {}
        characters = raw_state.get("characters") if isinstance(raw_state.get("characters"), list) else []
        for character in characters:
            if not isinstance(character, dict):
                continue
            if character.get("character_id") == selected_character_id:
                try:
                    return int(character.get("index"))
                except Exception:
                    return None
        return None

    def _main_menu_kwargs(self, action: dict[str, Any], summary_context: dict[str, Any]) -> dict[str, Any]:
        action_type = str(action.get("type") or "").strip().lower()
        if action_type != "choose_timeline_epoch":
            return {}
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        timeline = payload.get("timeline") if isinstance(payload.get("timeline"), dict) else {}
        slots = timeline.get("slots") if isinstance(timeline.get("slots"), list) else []
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            if not bool(slot.get("is_actionable")):
                continue
            try:
                return {"option_index": int(slot.get("index"))}
            except Exception:
                continue
        return {"option_index": 0}

    def _first_available_map_index(self, summary_context: dict[str, Any]) -> int | None:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        nodes = payload.get("travelable_nodes") if isinstance(payload.get("travelable_nodes"), list) else []
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            if "index" in node:
                try:
                    return int(node.get("index"))
                except Exception:
                    continue
            return index
        return None

    def _preferred_event_option(self, summary_context: dict[str, Any], preferences: dict[str, Any]) -> int | None:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        event_options = payload.get("event_options") if isinstance(payload.get("event_options"), list) else []
        current_hp = self._safe_int(payload.get("current_hp")) or 0
        max_hp = self._safe_int(payload.get("max_hp")) or 0
        gold = self._safe_int(payload.get("gold")) or 0
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 0.0
        guidance_override = self._guidance_override_from_payload(summary_context)
        best_candidate: tuple[int, int] | None = None
        for index, option in enumerate(event_options):
            if not isinstance(option, dict):
                continue
            score = self._score_event_option(option, hp_ratio, gold)
            if guidance_override.get("prefer_defense") and any(token in self._event_option_text(option) for token in ["lose hp", "失去生命", "掉血"]):
                score -= 20
            if guidance_override.get("prefer_attack") and any(token in self._event_option_text(option) for token in ["relic", "遗物", "gold", "金币", "card", "卡牌"]):
                score += 10
            option_index = self._safe_int(option.get("index"))
            if option_index is None:
                option_index = index
            candidate = (-score, option_index)
            if best_candidate is None or candidate < best_candidate:
                best_candidate = candidate
        if best_candidate is not None and best_candidate[0] < 0:
            return best_candidate[1]
        record = preferences.get("record") if isinstance(preferences.get("record"), dict) else {}
        value = record.get("value") if isinstance(record.get("value"), dict) else {}
        preferred = value.get("preferred_option_index")
        return self._safe_int(preferred)

    def _preferred_option_index(self, preferences: dict[str, Any]) -> int | None:
        record = preferences.get("record") if isinstance(preferences.get("record"), dict) else None
        if isinstance(record, dict):
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            preferred = self._safe_int(value.get("preferred_option_index"))
            if preferred is not None:
                return preferred
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        for item in records:
            if not isinstance(item, dict):
                continue
            value = item.get("value") if isinstance(item.get("value"), dict) else {}
            preferred = self._safe_int(value.get("preferred_option_index"))
            if preferred is not None:
                return preferred
        return None

    def _preferred_reward_option(self, summary_context: dict[str, Any], preferences: dict[str, Any]) -> int | None:
        reward_cards = self._reward_candidates(summary_context)
        deck = summary_context.get("payload", {}).get("deck") if isinstance(summary_context.get("payload"), dict) else {}
        archetype_tags = [str(tag).strip().lower() for tag in self._infer_archetype_tags(deck)] if isinstance(deck, dict) else []
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        deck_policy = self._reward_deck_policy(records)
        guidance_override = self._guidance_override_from_payload(summary_context)
        best_candidate: tuple[int, int] | None = None
        for index, card in enumerate(reward_cards if isinstance(reward_cards, list) else []):
            if not isinstance(card, dict):
                continue
            score = self._score_reward_card(card, deck_policy, archetype_tags)
            if guidance_override.get("prefer_defense"):
                if self._card_block(card) > 0:
                    score += 18
                if self._reward_dynamic_value(card, "HpLoss") > 0:
                    score -= 12
            if guidance_override.get("prefer_attack"):
                if self._card_damage(card) > 0:
                    score += 14
            card_index = self._safe_int(card.get("index"))
            if card_index is None:
                card_index = index
            candidate = (-score, card_index)
            if best_candidate is None or candidate < best_candidate:
                best_candidate = candidate
        if best_candidate is not None and best_candidate[0] < 0:
            return best_candidate[1]
        preferred = self._preferred_option_index(preferences)
        if preferred is not None:
            return preferred
        return self._first_record_option(preferences)

    def _reward_candidates(self, summary_context: dict[str, Any]) -> list[dict[str, Any]]:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        reward_cards = payload.get("reward_cards") if isinstance(payload.get("reward_cards"), list) else []
        if reward_cards:
            return reward_cards
        selection_cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
        if selection_cards:
            return selection_cards
        reward = payload.get("reward") if isinstance(payload.get("reward"), dict) else {}
        card_options = reward.get("card_options") if isinstance(reward.get("card_options"), list) else []
        if card_options:
            return card_options
        return []

    def _preferred_remove_option(self, summary_context: dict[str, Any], preferences: dict[str, Any]) -> int | None:
        selection_cards = summary_context.get("payload", {}).get("selection_cards") if isinstance(summary_context.get("payload"), dict) else []
        best_candidate: tuple[int, int] | None = None
        for index, card in enumerate(selection_cards if isinstance(selection_cards, list) else []):
            if not isinstance(card, dict):
                continue
            if self._is_unremovable_card(card):
                continue
            score = self._score_remove_card(card)
            card_index = self._safe_int(card.get("index"))
            if card_index is None:
                card_index = index
            candidate = (score, -card_index)
            if best_candidate is None or candidate < best_candidate:
                best_candidate = candidate
        if best_candidate is not None:
            return -best_candidate[1]
        preferred = self._preferred_option_index(preferences)
        if preferred is not None:
            return preferred
        return self._first_record_option(preferences)

    def _preferred_route_option(self, preferences: dict[str, Any]) -> int | None:
        preferred = self._preferred_option_index(preferences)
        if preferred is not None:
            return preferred
        return self._first_record_option(preferences)

    def _preferred_map_index(self, summary_context: dict[str, Any], preferences: dict[str, Any]) -> int | None:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        travelable_nodes = payload.get("travelable_nodes") if isinstance(payload.get("travelable_nodes"), list) else []
        current_hp = self._safe_int(payload.get("current_hp")) or 0
        max_hp = self._safe_int(payload.get("max_hp")) or 0
        gold = self._safe_int(payload.get("gold")) or 0
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 0.0
        route_policy = self._route_policy(preferences)
        best_candidate: tuple[int, int] | None = None
        for index, node in enumerate(travelable_nodes):
            if not isinstance(node, dict):
                continue
            score = self._score_map_node(node, route_policy, hp_ratio, gold)
            node_index = self._safe_int(node.get("index"))
            if node_index is None:
                node_index = index
            candidate = (-score, node_index)
            if best_candidate is None or candidate < best_candidate:
                best_candidate = candidate
        if best_candidate is not None and best_candidate[0] < 0:
            return best_candidate[1]
        preferred = self._first_record_option(preferences)
        return preferred if preferred is not None else self._first_available_map_index(summary_context)

    def _reward_kwargs(self, summary_context: dict[str, Any], action: dict[str, Any], preferred_option: int | None) -> dict[str, Any]:
        action_type = str(action.get("type") or "").strip().lower()
        if action_type == "claim_reward":
            return {"option_index": preferred_option if preferred_option is not None else 0}
        if action_type == "choose_reward_card":
            return {"option_index": preferred_option if preferred_option is not None else 0}
        return {}

    def _guidance_override(self, context: dict[str, Any]) -> dict[str, bool]:
        summary_context = context.get("summary_context") if isinstance(context.get("summary_context"), dict) else {}
        return self._guidance_override_from_payload(summary_context)

    def _guidance_override_from_payload(self, summary_context: dict[str, Any]) -> dict[str, bool]:
        decision_payload = summary_context.get("decision_payload") if isinstance(summary_context.get("decision_payload"), dict) else {}
        instructions = decision_payload.get("instructions") if isinstance(decision_payload.get("instructions"), list) else []
        guidance_lines = [
            str(item.get("content") or "")
            for item in instructions
            if isinstance(item, dict) and str(item.get("source") or "") == "neko_guidance"
        ]
        merged = " ".join(guidance_lines).lower()
        if not merged:
            guidance = decision_payload.get("guidance") if isinstance(decision_payload.get("guidance"), dict) else {}
            pending = guidance.get("pending") if isinstance(guidance.get("pending"), list) else []
            merged = " ".join(str(item.get("content") or "") for item in pending if isinstance(item, dict)).lower()
        if not merged:
            return {}
        prefer_defense = any(token in merged for token in ["先防", "优先防", "保血", "保命", "别贪", "不要贪", "求稳", "稳一点"])
        prefer_attack = any(token in merged for token in ["优先输出", "抢伤害", "压血", "收掉", "收尾", "斩杀"])
        return {
            "prefer_defense": prefer_defense,
            "prefer_attack": prefer_attack,
            "avoid_greed": any(token in merged for token in ["别贪", "不要贪"]),
            "prefer_lethal": any(token in merged for token in ["斩杀", "收掉", "收尾"]),
            "conservative": prefer_defense or any(token in merged for token in ["求稳", "稳一点"]),
        }

    def _preferred_combat_action(self, actions: list[dict[str, Any]], snapshot: dict[str, Any], preferences: dict[str, Any], guidance_override: dict[str, bool]) -> dict[str, Any] | None:
        play_card = self._find_action(actions, "play_card")
        end_turn = self._find_action(actions, "end_turn")
        if play_card is None:
            return end_turn
        incoming_attack = self._incoming_attack_total(snapshot)
        current_block = self._current_block(snapshot)
        gap = max(0, incoming_attack - current_block)
        if guidance_override.get("prefer_defense") and gap > 0:
            preferred_card_index = self._preferred_combat_card_index(snapshot, preferences, guidance_override)
            if preferred_card_index is not None:
                return play_card
        if guidance_override.get("prefer_attack"):
            if gap >= 6:
                preferred_card_index = self._preferred_combat_card_index(snapshot, preferences, {"prefer_defense": True})
                if preferred_card_index is not None:
                    return play_card
            return play_card
        return play_card or end_turn

    def _preferred_combat_card_index(self, snapshot: dict[str, Any], preferences: dict[str, Any], guidance_override: dict[str, bool] | None = None) -> int | None:
        guidance_override = guidance_override or {}
        preferred = self._first_record_option(preferences)
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        if guidance_override.get("prefer_defense"):
            defensive_index = self._first_matching_playable_card(hand, ["block", "护盾", "格挡", "defend"])
            if defensive_index is not None:
                return defensive_index
        if guidance_override.get("prefer_attack"):
            attack_index = self._first_matching_playable_card(hand, ["attack", "damage", "伤害", "strike"])
            if attack_index is not None:
                return attack_index
        if preferred is not None:
            return preferred
        for card in hand:
            if not isinstance(card, dict):
                continue
            if bool(card.get("playable")):
                return self._safe_int(card.get("index"))
        return None

    def _combat_kwargs(self, snapshot: dict[str, Any], action: dict[str, Any], card_index: int | None) -> dict[str, Any]:
        if action.get("type") != "play_card" or card_index is None:
            return {}
        kwargs: dict[str, Any] = {"card_index": card_index}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        selected_card = next(
            (card for card in hand if isinstance(card, dict) and self._safe_int(card.get("index")) == card_index),
            None,
        )
        if selected_card is not None:
            target_index = self._preferred_target_for_card(selected_card, enemies)
        else:
            target_index = self._preferred_combat_target_index(snapshot, card_index)
        if target_index is not None:
            kwargs["target_index"] = target_index
        return kwargs

    def _preferred_combat_target_index(self, snapshot: dict[str, Any], card_index: int) -> int | None:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        for card in hand:
            if not isinstance(card, dict):
                continue
            if self._safe_int(card.get("index")) != card_index:
                continue
            valid_target_indices = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
            if not valid_target_indices:
                return None
            try:
                return int(valid_target_indices[0])
            except Exception:
                return None
        return None

    def _first_matching_playable_card(self, hand: list[dict[str, Any]], keywords: list[str]) -> int | None:
        normalized_keywords = [keyword.lower() for keyword in keywords]
        for card in hand:
            if not isinstance(card, dict) or not bool(card.get("playable")):
                continue
            haystack = " ".join(
                [
                    str(card.get("name") or ""),
                    str(card.get("id") or ""),
                    str(card.get("description") or ""),
                    str(card.get("effect") or ""),
                    str(card.get("card_type") or ""),
                ]
            ).lower()
            if any(keyword in haystack for keyword in normalized_keywords):
                return self._safe_int(card.get("index"))
        return None

    def _plan_combat_turn_sequence(
        self,
        snapshot: dict[str, Any],
        actions: list[dict[str, Any]],
        preferences: dict[str, Any],
        guidance_override: dict[str, bool],
    ) -> list[dict[str, Any]]:
        play_card = self._find_action(actions, "play_card")
        if play_card is None:
            return []
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        hand = [card for card in (combat.get("hand") if isinstance(combat.get("hand"), list) else []) if isinstance(card, dict) and bool(card.get("playable"))]
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        current_energy = self._safe_int(player.get("energy")) or 0
        current_block = self._safe_int(player.get("block")) or 0
        incoming_attack = self._incoming_attack_total(snapshot)

        lethal_cards = self._find_labeled_lethal_sequence(hand, current_energy, enemies)
        if lethal_cards:
            self._debug("[sts2_combat_plan] lethal_sequence=%s", lethal_cards)
            return lethal_cards

        effective_override = dict(guidance_override)
        if not effective_override.get("prefer_attack") and incoming_attack > current_block:
            effective_override["prefer_defense"] = True

        sequence: list[dict[str, Any]] = []
        remaining_cards = list(hand)
        remaining_energy = current_energy
        simulated_block = current_block
        threshold = 6

        while remaining_cards and remaining_energy > 0:
            gap = max(0, incoming_attack - simulated_block)
            if effective_override.get("prefer_attack"):
                if gap >= threshold:
                    next_card = self._best_defense_card(remaining_cards, remaining_energy)
                    reason = "combat_attack_priority_sequence"
                else:
                    next_card = self._best_attack_card(remaining_cards, remaining_energy) or self._best_defense_card(remaining_cards, remaining_energy)
                    reason = "combat_attack_priority_sequence"
            elif effective_override.get("prefer_defense"):
                if gap > 0:
                    next_card = self._best_defense_card(remaining_cards, remaining_energy)
                    reason = "combat_defense_sequence"
                else:
                    next_card = self._best_attack_card(remaining_cards, remaining_energy)
                    reason = "combat_defense_sequence"
            else:
                next_card = self._best_defense_card(remaining_cards, remaining_energy) if gap > 0 else self._best_attack_card(remaining_cards, remaining_energy)
                reason = "combat_preference_or_default"

            self._debug(
                "[sts2_combat_step] gap=%s energy=%s block=%s mode=%s next=%s",
                gap,
                remaining_energy,
                simulated_block,
                reason,
                {
                    "index": self._safe_int(next_card.get("index")) if isinstance(next_card, dict) else None,
                    "name": str(next_card.get("name") or next_card.get("card_id") or "?") if isinstance(next_card, dict) else None,
                    "damage": self._card_damage(next_card) if isinstance(next_card, dict) else None,
                    "block": self._card_block(next_card) if isinstance(next_card, dict) else None,
                    "cost": self._card_energy_cost(next_card) if isinstance(next_card, dict) else None,
                },
            )

            if next_card is None:
                break
            cost = self._card_energy_cost(next_card)
            if cost > remaining_energy:
                break
            remaining_energy -= cost
            simulated_block += self._card_block(next_card)
            sequence.append(
                {
                    "card_index": self._safe_int(next_card.get("index")) or 0,
                    "target_index": self._preferred_target_for_card(next_card, enemies),
                    "reason": reason,
                }
            )
            remaining_cards = [card for card in remaining_cards if card is not next_card]
            if not effective_override.get("prefer_attack") and simulated_block >= incoming_attack:
                effective_override["prefer_defense"] = False
                effective_override["prefer_attack"] = True

        return sequence

    def _find_labeled_lethal_sequence(self, cards: list[dict[str, Any]], current_energy: int, enemies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for enemy_index, enemy in enumerate(enemies):
            if not isinstance(enemy, dict):
                continue
            enemy_hp = self._safe_int(enemy.get("current_hp") if enemy.get("current_hp") is not None else enemy.get("hp")) or 0
            if enemy_hp <= 0:
                continue
            lethal_cards = self._find_lethal_card_sequence(cards, current_energy, enemy_hp)
            if not lethal_cards:
                continue
            return [
                {
                    "card_index": self._safe_int(card.get("index")) or 0,
                    "target_index": self._lethal_target_index(card, enemy_index),
                    "reason": "combat_lethal_sequence",
                }
                for card in lethal_cards
            ]
        return []

    def _best_defense_card(self, cards: list[dict[str, Any]], current_energy: int) -> dict[str, Any] | None:
        candidates = [card for card in cards if self._card_energy_cost(card) <= current_energy and self._card_block(card) > 0]
        if not candidates:
            return None
        return max(candidates, key=self._card_block)

    def _best_attack_card(self, cards: list[dict[str, Any]], current_energy: int) -> dict[str, Any] | None:
        candidates = [card for card in cards if self._card_energy_cost(card) <= current_energy and self._card_damage(card) > 0]
        if not candidates:
            return None
        return max(candidates, key=self._card_damage)

    def _preferred_target_for_card(self, card: dict[str, Any], enemies: list[dict[str, Any]]) -> int | None:
        valid_target_indices = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
        if not valid_target_indices:
            return None
        damage = self._card_damage(card)
        best_target = None
        best_priority: tuple[int, int, int] | None = None
        for target_index in valid_target_indices:
            try:
                normalized_index = int(target_index)
            except Exception:
                continue
            if normalized_index >= len(enemies) or normalized_index < 0:
                continue
            enemy = enemies[normalized_index]
            if not isinstance(enemy, dict):
                continue
            hp = self._safe_int(enemy.get("current_hp") if enemy.get("current_hp") is not None else enemy.get("hp")) or 0
            incoming = self._safe_int(enemy.get("intent_damage")) or 0
            lethal = 1 if damage > 0 and hp <= damage else 0
            priority = (lethal, incoming, -hp)
            if best_priority is None or priority > best_priority:
                best_priority = priority
                best_target = normalized_index
        return best_target if best_target is not None else self._lethal_target_index(card, 0)

    def _card_block(self, card: dict[str, Any]) -> int:
        for item in card.get("dynamic_values") if isinstance(card.get("dynamic_values"), list) else []:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "").strip().lower() == "block":
                parsed = self._safe_int(item.get("current_value"))
                if parsed is not None:
                    return parsed
        block = self._safe_int(card.get("block"))
        if block is not None:
            return block
        effect_text = " ".join(
            [
                str(card.get("resolved_rules_text") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
            ]
        )
        lowered = effect_text.lower()
        if "block" not in lowered and "护盾" not in effect_text and "格挡" not in effect_text:
            return 0
        for token in effect_text.replace("Gain", " ").replace("Block", " ").split():
            parsed = self._safe_int(token)
            if parsed is not None:
                return parsed
        return 0

    def _is_unremovable_card(self, card: dict[str, Any]) -> bool:
        if bool(card.get("unremovable")) or bool(card.get("cannot_remove")):
            return True
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
            ]
        ).lower()
        return "eternal" in text or "永恒" in text

    def _score_remove_card(self, card: dict[str, Any]) -> int:
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
                str(card.get("card_type") or card.get("type") or ""),
                str(card.get("rarity") or ""),
            ]
        ).lower()
        if any(token in text for token in ["curse", "诅咒"]):
            return -100
        if any(token in text for token in ["status", "状态"]):
            return -90
        if any(token in text for token in ["strike", "打击"]):
            return -60
        if any(token in text for token in ["defend", "防御"]):
            return -50
        if any(token in text for token in ["draw", "skim", "coolheaded", "抽牌", "过牌"]):
            return 60
        if any(token in text for token in ["energy", "turbo", "double energy", "能量"]):
            return 70
        if any(token in text for token in ["focus", "orb", "电球", "充能"]):
            return 65
        return 10

    def _route_policy(self, preferences: dict[str, Any]) -> dict[str, Any]:
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            if str(value.get("instruction_type") or "") != "route_policy":
                continue
            return value
        return {}

    def _score_event_option(self, option: dict[str, Any], hp_ratio: float, gold: int) -> int:
        text = self._event_option_text(option)
        score = 0
        if any(token in text for token in ["lose hp", "hp", "失去生命", "掉血", "流血"]):
            score -= 20 if hp_ratio < 0.6 else 8
        if any(token in text for token in ["gain gold", "gold", "获得金币", "金币"]):
            score += 10 if gold < 150 else 4
        if any(token in text for token in ["relic", "遗物", "获得遗物"]):
            score += 14
        if any(token in text for token in ["card", "卡牌", "获得卡"]):
            score += 8
        if any(token in text for token in ["remove", "删除", "移除"]):
            score += 12
        if any(token in text for token in ["curse", "诅咒"]):
            score -= 18
        return score

    def _event_option_text(self, option: dict[str, Any]) -> str:
        return " ".join(
            [
                str(option.get("label") or ""),
                str(option.get("text") or ""),
                str(option.get("description") or ""),
            ]
        ).lower()

    def _score_map_node(self, node: dict[str, Any], route_policy: dict[str, Any], hp_ratio: float, gold: int) -> int:
        node_type = str(node.get("type") or node.get("node_type") or "").strip().lower()
        weights = route_policy.get("weights") if isinstance(route_policy.get("weights"), dict) else {}
        conditions = route_policy.get("conditions") if isinstance(route_policy.get("conditions"), dict) else {}
        effective_weights = {str(key).strip().lower(): int(value) for key, value in weights.items() if str(key).strip()}
        low_hp = conditions.get("low_hp") if isinstance(conditions.get("low_hp"), dict) else {}
        hp_threshold = low_hp.get("hp_ratio_below")
        try:
            if hp_threshold is not None and hp_ratio < float(hp_threshold):
                for key, value in (low_hp.get("weights") if isinstance(low_hp.get("weights"), dict) else {}).items():
                    effective_weights[str(key).strip().lower()] = int(value)
        except Exception:
            pass
        if not effective_weights:
            effective_weights = {
                "rest": 12,
                "shop": 8 if gold >= 120 else 4,
                "elite": -10 if hp_ratio < 0.6 else -4,
                "monster": 3,
                "event": 4,
                "treasure": 5,
                "question": 4,
            }
        score = int(effective_weights.get(node_type, 0))
        descendants = self._collect_route_descendants(node, depth=int(route_policy.get("lookahead_depth") or 4))
        for descendant in descendants:
            descendant_type = str(descendant.get("type") or descendant.get("node_type") or "").strip().lower()
            score += int(effective_weights.get(descendant_type, 0))
        if gold >= 150 and any(str(item.get("type") or item.get("node_type") or "").strip().lower() == "shop" for item in descendants):
            score += 4
        if hp_ratio < 0.5 and any(str(item.get("type") or item.get("node_type") or "").strip().lower() == "rest" for item in descendants):
            score += 6
        return score

    def _collect_route_descendants(self, node: dict[str, Any], depth: int = 4) -> list[dict[str, Any]]:
        if depth <= 0 or not isinstance(node, dict):
            return []
        collected: list[dict[str, Any]] = []
        children = []
        for key in ("next_nodes", "children", "branches", "paths", "next"):
            value = node.get(key)
            if isinstance(value, list):
                children.extend(item for item in value if isinstance(item, dict))
        for child in children:
            collected.append(child)
            collected.extend(self._collect_route_descendants(child, depth=depth - 1))
        return collected

    def _score_reward_card(self, card: dict[str, Any], deck_policy: dict[str, set[str] | list[dict[str, Any]]], archetype_tags: list[str]) -> int:
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
                str(card.get("card_type") or card.get("type") or ""),
            ]
        ).lower()
        energy_gain = self._reward_dynamic_value(card, "Energy")
        score = 0
        tag_keywords = {
            "draw": ["draw", "抽牌", "skim", "coolheaded"],
            "defense": ["block", "护盾", "格挡", "defend", "charge battery"],
            "energy": ["energy", "能量", "turbo", "double energy"],
            "orb_focus": ["orb", "focus", "电球", "充能", "defragment", "frost"],
            "expensive_attack": ["2 cost", "3 cost", "x cost", "meteor", "heavy blade"],
            "attack": ["damage", "伤害", "attack", "strike"],
            "strength": ["strength", "力量"],
            "self_damage_attack": ["失去", "lose", "hp loss", "hemokinesis", "御血术"],
            "slow_block_only": ["block", "护盾", "格挡"],
        }
        for tag in deck_policy.get("prefer_tags", set()):
            if any(keyword in text for keyword in tag_keywords.get(tag, [tag])):
                score += 40
        for tag in deck_policy.get("avoid_tags", set()):
            if any(keyword in text for keyword in tag_keywords.get(tag, [tag])):
                score -= 40
        for tag in deck_policy.get("archetype_bias", set()):
            if tag in archetype_tags and any(keyword in text for keyword in tag_keywords.get(tag, [tag])):
                score += 20

        score += self._base_reward_score(text, energy_gain)

        branches = deck_policy.get("branches") if isinstance(deck_policy.get("branches"), list) else []
        matched_branch = self._matched_reward_branch(branches, archetype_tags, text)
        if matched_branch is not None:
            prefer_tags = matched_branch.get("prefer_tags") if isinstance(matched_branch.get("prefer_tags"), list) else []
            avoid_tags = matched_branch.get("avoid_tags") if isinstance(matched_branch.get("avoid_tags"), list) else []
            prefer_cards = matched_branch.get("prefer_cards") if isinstance(matched_branch.get("prefer_cards"), list) else []
            avoid_cards = matched_branch.get("avoid_cards") if isinstance(matched_branch.get("avoid_cards"), list) else []
            for tag in prefer_tags:
                if any(keyword in text for keyword in tag_keywords.get(str(tag).strip().lower(), [str(tag).strip().lower()])):
                    score += 35
            for tag in avoid_tags:
                if any(keyword in text for keyword in tag_keywords.get(str(tag).strip().lower(), [str(tag).strip().lower()])):
                    score -= 35
            if any(str(card_name).strip().lower() in text for card_name in prefer_cards):
                score += 50
            if any(str(card_name).strip().lower() in text for card_name in avoid_cards):
                score -= 50
        return score

    def _reward_deck_policy(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        prefer_tags: set[str] = set()
        avoid_tags: set[str] = set()
        archetype_bias: set[str] = set()
        branches: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            if str(value.get("instruction_type") or "") != "deck_policy":
                continue
            prefer_tags.update(str(tag).strip().lower() for tag in (value.get("prefer_tags") if isinstance(value.get("prefer_tags"), list) else []) if str(tag).strip())
            avoid_tags.update(str(tag).strip().lower() for tag in (value.get("avoid_tags") if isinstance(value.get("avoid_tags"), list) else []) if str(tag).strip())
            archetype_bias.update(str(tag).strip().lower() for tag in (value.get("archetype_bias") if isinstance(value.get("archetype_bias"), list) else []) if str(tag).strip())
            if isinstance(value.get("branches"), list):
                branches.extend(item for item in value.get("branches") if isinstance(item, dict))
        return {
            "prefer_tags": prefer_tags,
            "avoid_tags": avoid_tags,
            "archetype_bias": archetype_bias,
            "branches": branches,
        }

    def _base_reward_score(self, text: str, energy_gain: int) -> int:
        score = 0
        if any(token in text for token in ["block", "护盾", "格挡"]):
            score += 20
        if any(token in text for token in ["draw", "抽牌"]):
            score += 18
        if any(token in text for token in ["damage", "伤害", "attack", "攻击"]):
            score += 12
        if any(token in text for token in ["strength", "力量"]):
            score += 10
        if energy_gain > 0 or any(token in text for token in ["获得能量", "gain energy", "恢复能量", "refund energy"]):
            score += 16 + (energy_gain * 4)
        return score

    def _reward_dynamic_value(self, card: dict[str, Any], field_name: str) -> int:
        for item in card.get("dynamic_values") if isinstance(card.get("dynamic_values"), list) else []:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "").strip().lower() == field_name.strip().lower():
                return self._safe_int(item.get("current_value")) or 0
        return 0

    def _matched_reward_branch(self, branches: list[dict[str, Any]], archetype_tags: list[str], text: str) -> dict[str, Any] | None:
        for branch in branches:
            when_has = branch.get("when_has") if isinstance(branch.get("when_has"), list) else []
            if when_has and not any(str(token).strip().lower() in text for token in when_has):
                continue
            branch_archetypes = [str(tag).strip().lower() for tag in (branch.get("archetype_bias") if isinstance(branch.get("archetype_bias"), list) else []) if str(tag).strip()]
            if branch_archetypes and not any(tag in archetype_tags for tag in branch_archetypes):
                continue
            return branch
        return None

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

    def _lethal_combat_operation(self, snapshot: dict[str, Any], actions: list[dict[str, Any]]) -> PlannedOperation | None:
        play_card = self._find_action(actions, "play_card")
        if play_card is None:
            return None
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        current_energy = self._safe_int(combat.get("player", {}).get("energy")) or 0
        playable_cards = [card for card in hand if isinstance(card, dict) and bool(card.get("playable"))]
        for enemy_index, enemy in enumerate(enemies):
            if not isinstance(enemy, dict):
                continue
            enemy_hp = self._safe_int(enemy.get("current_hp") if enemy.get("current_hp") is not None else enemy.get("hp")) or 0
            if enemy_hp <= 0:
                continue
            lethal_cards = self._find_lethal_card_sequence(playable_cards, current_energy, enemy_hp)
            if lethal_cards:
                first = lethal_cards[0]
                kwargs: dict[str, Any] = {"card_index": self._safe_int(first.get("index")) or 0}
                target_index = self._lethal_target_index(first, enemy_index)
                if target_index is not None:
                    kwargs["target_index"] = target_index
                return PlannedOperation(action_type="play_card", kwargs=kwargs, confidence=0.98, source="heuristic", reason="combat_lethal_sequence")
        return None

    def _find_lethal_card_sequence(self, cards: list[dict[str, Any]], current_energy: int, enemy_hp: int) -> list[dict[str, Any]]:
        ordered = sorted(cards, key=self._card_damage, reverse=True)
        chosen: list[dict[str, Any]] = []
        remaining_energy = current_energy
        remaining_hp = enemy_hp
        for card in ordered:
            cost = self._card_energy_cost(card)
            if cost > remaining_energy:
                continue
            damage = self._card_damage(card)
            if damage <= 0:
                continue
            chosen.append(card)
            remaining_energy -= cost
            remaining_hp -= damage
            if remaining_hp <= 0:
                return chosen
        return []

    def _card_damage(self, card: dict[str, Any]) -> int:
        for item in card.get("dynamic_values") if isinstance(card.get("dynamic_values"), list) else []:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "").strip().lower() == "damage":
                parsed = self._safe_int(item.get("current_value"))
                if parsed is not None:
                    return parsed
        damage = self._safe_int(card.get("damage"))
        if damage is not None:
            return damage
        effect_text = " ".join(
            [
                str(card.get("resolved_rules_text") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
            ]
        )
        for token in effect_text.replace("Deal", " ").replace("damage", " ").split():
            parsed = self._safe_int(token)
            if parsed is not None:
                return parsed
        return 0

    def _card_energy_cost(self, card: dict[str, Any]) -> int:
        cost = self._safe_int(card.get("energy_cost"))
        if cost is not None:
            return max(0, cost)
        return 1

    def _lethal_target_index(self, card: dict[str, Any], fallback_target: int) -> int | None:
        valid_target_indices = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
        if not valid_target_indices:
            return None
        if fallback_target in valid_target_indices:
            return fallback_target
        try:
            return int(valid_target_indices[0])
        except Exception:
            return None

    def _incoming_attack_total(self, snapshot: dict[str, Any]) -> int:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        total = 0
        for enemy in enemies:
            if not isinstance(enemy, dict):
                continue
            direct = self._safe_int(enemy.get("intent_damage"))
            if direct is not None:
                total += direct
                continue
            intents = enemy.get("intents") if isinstance(enemy.get("intents"), list) else []
            enemy_total = 0
            for intent in intents:
                if not isinstance(intent, dict):
                    continue
                total_damage = self._safe_int(intent.get("total_damage"))
                if total_damage is not None:
                    enemy_total += total_damage
                    continue
                damage = self._safe_int(intent.get("damage")) or 0
                hits = self._safe_int(intent.get("hits")) or 1
                enemy_total += damage * hits
            total += enemy_total
        return total

    def _current_block(self, snapshot: dict[str, Any]) -> int:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        return self._safe_int(player.get("block")) or 0

    def _first_record_option(self, preferences: dict[str, Any]) -> int | None:
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            preferred = value.get("preferred_option_index")
            normalized = self._safe_int(preferred)
            if normalized is not None:
                return normalized
        return None

    def _safe_int(self, value: Any) -> int | None:
        try:
            if value is None:
                return None
            return int(value)
        except Exception:
            return None


__all__ = ["STS2HeuristicPlanner"]
