from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Set


class HeuristicSelector:
    def __init__(self, logger) -> None:
        self.logger = logger

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except Exception:
            return default

    def select_preemptive_program_action(self, actions: List[Dict[str, Any]], context: Dict[str, Any], selector_methods) -> Optional[Dict[str, Any]]:
        reward_action = selector_methods._select_reward_action_heuristic(actions, context)
        if reward_action is not None:
            return reward_action
        return selector_methods._select_shop_remove_selection_action(actions, context)

    def select_shop_remove_selection_action(self, actions: List[Dict[str, Any]], context: Dict[str, Any], selector_methods, analyzer_methods) -> Optional[Dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        screen = selector_methods._normalized_screen_name(snapshot)
        if screen != "card_selection":
            return None
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        shop = raw_state.get("shop") if isinstance(raw_state.get("shop"), dict) else {}
        selected = selector_methods._select_shop_remove_action(actions, context, shop)
        if selected is not None:
            return selected
        remove_action = next((action for action in actions if isinstance(action, dict) and str(action.get("type") or "") == "select_deck_card"), None)
        if not isinstance(remove_action, dict):
            return None
        remove_index = selector_methods._find_shop_remove_card_index(context)
        if remove_index is None:
            return None
        selected = dict(remove_action)
        raw = remove_action.get("raw") if isinstance(remove_action.get("raw"), dict) else {}
        selected_raw = dict(raw)
        selected_raw["option_index"] = remove_index
        selected_raw["shop_remove_selection"] = True
        selected["raw"] = selected_raw
        return selected

    def select_action_heuristic(self, actions: List[Dict[str, Any]], context: Dict[str, Any], selector_methods, analyzer_methods, combat_analyzer) -> Dict[str, Any]:
        reward_action = selector_methods._select_reward_action_heuristic(actions, context)
        if reward_action is not None:
            return reward_action
        shop_action = selector_methods._select_shop_action_heuristic(actions, context)
        if shop_action is not None:
            return shop_action
        combat = analyzer_methods._combat_state(context)
        if combat:
            selector_methods._log_combat_block_fields(context)
        tactical_summary = combat_analyzer.build_tactical_summary(combat, selector_methods._load_strategy_constraints) if combat else {}
        if combat:
            has_lethal = bool(tactical_summary.get("lethal_targets"))
            should_prioritize_defense = bool(tactical_summary.get("should_prioritize_defense"))
            if has_lethal:
                weighted_action = selector_methods._select_weighted_play_card(actions, combat, tactical_summary, attack_weight=2, defense_weight=1)
                if weighted_action is not None:
                    return weighted_action
            elif should_prioritize_defense:
                defensive_action = selector_methods._find_defensive_action(actions, combat, tactical_summary)
                if defensive_action is not None:
                    return defensive_action
                weighted_action = selector_methods._select_weighted_play_card(actions, combat, tactical_summary, attack_weight=1, defense_weight=2)
                if weighted_action is not None:
                    return weighted_action
        preferred_order = [
            "confirm_modal",
            "dismiss_modal",
            "choose_event_option",
            "proceed",
            "choose_map_node",
            "choose_treasure_relic",
            "play_card",
            "end_turn",
            "use_potion",
            "discard_potion",
        ]
        for action_type in preferred_order:
            for action in actions:
                if str(action.get("type") or "") == action_type:
                    return action
        for action in actions:
            action_type = str(action.get("type") or "")
            if action_type and action_type not in {"wait", "noop"}:
                return action
        return actions[0]

    def select_shop_action_heuristic(self, actions: List[Dict[str, Any]], context: Dict[str, Any], selector_methods) -> Optional[Dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        if selector_methods._normalized_screen_name(snapshot) != "shop":
            return None
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        shop = raw_state.get("shop") if isinstance(raw_state.get("shop"), dict) else {}
        buy_card_action = next((action for action in actions if isinstance(action, dict) and str(action.get("type") or "") == "buy_card"), None)
        if isinstance(buy_card_action, dict):
            preferred_shop_card_index = selector_methods._find_preferred_shop_card_index(context)
            if preferred_shop_card_index is not None:
                selected = dict(buy_card_action)
                raw = buy_card_action.get("raw") if isinstance(buy_card_action.get("raw"), dict) else {}
                selected_raw = dict(raw)
                selected_raw["option_index"] = preferred_shop_card_index
                selected["raw"] = selected_raw
                return selected
        buy_relic_action = next((action for action in actions if isinstance(action, dict) and str(action.get("type") or "") == "buy_relic"), None)
        if isinstance(buy_relic_action, dict):
            preferred_shop_relic_index = selector_methods._find_preferred_shop_relic_index(context)
            if preferred_shop_relic_index is not None:
                selected = dict(buy_relic_action)
                raw = buy_relic_action.get("raw") if isinstance(buy_relic_action.get("raw"), dict) else {}
                selected_raw = dict(raw)
                selected_raw["option_index"] = preferred_shop_relic_index
                selected["raw"] = selected_raw
                return selected
        buy_potion_action = next((action for action in actions if isinstance(action, dict) and str(action.get("type") or "") == "buy_potion"), None)
        if isinstance(buy_potion_action, dict):
            preferred_shop_potion_index = selector_methods._find_preferred_shop_potion_index(context)
            if preferred_shop_potion_index is not None:
                selected = dict(buy_potion_action)
                raw = buy_potion_action.get("raw") if isinstance(buy_potion_action.get("raw"), dict) else {}
                selected_raw = dict(raw)
                selected_raw["option_index"] = preferred_shop_potion_index
                selected["raw"] = selected_raw
                return selected
        remove_action = selector_methods._select_shop_remove_action(actions, context, shop)
        if remove_action is not None:
            return remove_action
        return next((action for action in actions if isinstance(action, dict) and str(action.get("type") or "") == "close_shop_inventory"), None)

    def select_weighted_play_card(self, actions: List[Dict[str, Any]], combat: Dict[str, Any], tactical_summary: Dict[str, Any], *, attack_weight: int, defense_weight: int, selector_methods, combat_analyzer) -> Optional[Dict[str, Any]]:
        target_index = tactical_summary.get("recommended_target_index")
        strategy_constraints = selector_methods._load_strategy_constraints(selector_methods._configured_character_strategy())
        best_attack_card = combat_analyzer._best_playable_damage_card(combat, target_index=target_index, strategy_constraints=strategy_constraints)
        best_block_card = combat_analyzer._best_playable_block_card(combat)
        best_attack_damage = combat_analyzer._card_total_damage_value(best_attack_card, combat=combat, target_index=target_index, strategy_constraints=strategy_constraints) if isinstance(best_attack_card, dict) else 0
        best_block_amount = combat_analyzer._card_block_value(best_block_card) if isinstance(best_block_card, dict) else 0
        incoming_attack_total = selector_methods._safe_int(tactical_summary.get("incoming_attack_total"))
        current_block = selector_methods._safe_int(tactical_summary.get("current_block"))
        remaining_block_needed = max(0, incoming_attack_total - current_block)
        effective_block_amount = min(best_block_amount, remaining_block_needed) if remaining_block_needed > 0 else 0
        best_attack_score = best_attack_damage * attack_weight
        best_block_score = effective_block_amount * defense_weight
        if best_attack_score <= 0 and best_block_score <= 0:
            return None
        self.logger.info(
            f"[sts2_autoplay][heuristic] weighted play compare attack={best_attack_card.get('name') if isinstance(best_attack_card, dict) else None} damage={best_attack_damage} attack_score={best_attack_score} block={best_block_card.get('name') if isinstance(best_block_card, dict) else None} block_amount={best_block_amount} effective_block={effective_block_amount} remaining_block_needed={remaining_block_needed} block_score={best_block_score} target={target_index}"
        )
        if best_attack_score > best_block_score and isinstance(best_attack_card, dict):
            return selector_methods._action_for_card(actions, best_attack_card, target_index=target_index)
        if isinstance(best_block_card, dict) and best_block_score > 0:
            return selector_methods._action_for_card(actions, best_block_card, target_index=None)
        if isinstance(best_attack_card, dict) and best_attack_score > 0:
            return selector_methods._action_for_card(actions, best_attack_card, target_index=target_index)
        return None

    def find_defensive_action(self, actions: List[Dict[str, Any]], combat: Dict[str, Any], tactical_summary: Dict[str, Any], selector_methods, combat_analyzer) -> Optional[Dict[str, Any]]:
        remaining_block_needed = selector_methods._safe_int(tactical_summary.get("remaining_block_needed"))
        if remaining_block_needed <= 0:
            return None
        best_card = combat_analyzer._best_playable_block_card(combat)
        if best_card is None:
            return None
        if min(combat_analyzer._card_block_value(best_card), remaining_block_needed) <= 0:
            return None
        return selector_methods._action_for_card(actions, best_card, target_index=None)

    def select_reward_action_heuristic(self, actions: List[Dict[str, Any]], context: Dict[str, Any], selector_methods) -> Optional[Dict[str, Any]]:
        reward_actions = [
            action for action in actions
            if isinstance(action, dict) and str(action.get("type") or "") in {"choose_reward_card", "select_deck_card", "claim_reward", "collect_rewards_and_proceed"}
        ]
        if not reward_actions:
            return None
        raw_by_type = {
            str(action.get("type") or ""): action
            for action in reward_actions
            if str(action.get("type") or "")
        }
        claim_card_index = selector_methods._find_claimable_card_reward_index(context)
        claim_action = raw_by_type.get("claim_reward")
        if claim_card_index is not None and isinstance(claim_action, dict):
            claim_allowed = selector_methods._allowed_kwargs_for_action(
                "claim_reward",
                claim_action.get("raw") if isinstance(claim_action.get("raw"), dict) else {},
                context,
            ).get("option_index", [])
            if not claim_allowed or claim_card_index in claim_allowed:
                selected = dict(claim_action)
                raw = claim_action.get("raw") if isinstance(claim_action.get("raw"), dict) else {}
                selected_raw = dict(raw)
                selected_raw["option_index"] = claim_card_index
                selected["raw"] = selected_raw
                return selected
        reward_action = reward_actions[0]
        raw = reward_action.get("raw") if isinstance(reward_action.get("raw"), dict) else {}
        if not selector_methods._is_card_reward_context(raw, context):
            return None
        options = selector_methods._card_reward_options(raw, context)
        if options:
            selector_methods._log_card_reward_options(options, context)
        preferred_option_index = selector_methods._find_preferred_card_option_index(raw, context)
        reward_action_type = str(reward_action.get("type") or "")
        if reward_action_type in {"claim_reward", "collect_rewards_and_proceed"} and options:
            promoted_label = "choose_reward_card"
            selected = dict(reward_action)
            selected_raw = dict(raw)
            selected["type"] = promoted_label
            selected_raw["name"] = promoted_label
            selected_raw["type"] = promoted_label
            selected["raw"] = selected_raw
            return selected
        if preferred_option_index is None:
            return None
        return reward_action

    def select_shop_remove_action(self, actions: List[Dict[str, Any]], context: Dict[str, Any], shop: Dict[str, Any], selector_methods) -> Optional[Dict[str, Any]]:
        card_removal = shop.get("card_removal") if isinstance(shop.get("card_removal"), dict) else {}
        if not bool(card_removal.get("available")) or not bool(card_removal.get("enough_gold")):
            return None
        remove_action = next((action for action in actions if isinstance(action, dict) and str(action.get("type") or "") == "select_deck_card"), None)
        if not isinstance(remove_action, dict):
            return None
        remove_index = selector_methods._find_shop_remove_card_index(context)
        if remove_index is None:
            return None
        selected = dict(remove_action)
        raw = remove_action.get("raw") if isinstance(remove_action.get("raw"), dict) else {}
        selected_raw = dict(raw)
        selected_raw["option_index"] = remove_index
        selected_raw["shop_remove_selection"] = True
        selected["raw"] = selected_raw
        return selected

    def find_preferred_shop_card_index(self, context: Dict[str, Any], selector_methods) -> Optional[int]:
        if selector_methods._configured_character_strategy() != "defect":
            return None
        shop_cards = selector_methods._shop_card_options(context)
        if not shop_cards:
            return None
        best_option: Optional[Dict[str, Any]] = None
        best_score: Optional[int] = None
        for option in shop_cards:
            score = selector_methods._score_defect_card_option(option, context)
            if best_score is None or score > best_score:
                best_option = option
                best_score = score
        if best_option is None or best_score is None or best_score < 90:
            return None
        return int(best_option["index"])

    def find_preferred_shop_relic_index(self, context: Dict[str, Any], selector_methods) -> Optional[int]:
        if selector_methods._configured_character_strategy() != "defect":
            return None
        shop_relics = selector_methods._shop_relic_options(context)
        if not shop_relics:
            return None
        best_option: Optional[Dict[str, Any]] = None
        best_score: Optional[int] = None
        for option in shop_relics:
            score = selector_methods._score_defect_shop_relic_option(option, context)
            if best_score is None or score > best_score:
                best_option = option
                best_score = score
        if best_option is None or best_score is None or best_score < 22:
            return None
        return int(best_option["index"])

    def find_preferred_shop_potion_index(self, context: Dict[str, Any], selector_methods) -> Optional[int]:
        if selector_methods._configured_character_strategy() != "defect":
            return None
        shop_potions = selector_methods._shop_potion_options(context)
        if not shop_potions:
            return None
        potion_slots = selector_methods._potion_slots(context)
        if potion_slots > 0 and len(selector_methods._potions(context)) >= potion_slots:
            return None
        best_option: Optional[Dict[str, Any]] = None
        best_score: Optional[int] = None
        for option in shop_potions:
            score = selector_methods._score_defect_shop_potion_option(option, context)
            if best_score is None or score > best_score:
                best_option = option
                best_score = score
        if best_option is None or best_score is None or best_score < 22:
            return None
        return int(best_option["index"])

    def find_shop_remove_card_index(self, context: Dict[str, Any], selector_methods) -> Optional[int]:
        deck = selector_methods._run_deck_cards(context)
        if not deck:
            return None
        removable_cards = [card for card in deck if selector_methods._is_shop_removable_card(card)]
        if not removable_cards:
            return None
        scored_cards = [selector_methods._shop_remove_card_debug_entry(card, context) for card in removable_cards]
        curse_cards = [entry for entry in scored_cards if entry["priority"] == 0]
        if curse_cards:
            selected = curse_cards[0]
            return selector_methods._safe_int(selected.get("index"), None)
        starter_cards = [entry for entry in scored_cards if entry["priority"] == 1]
        if starter_cards:
            selected = min(starter_cards, key=lambda entry: entry["score"])
            return selector_methods._safe_int(selected.get("index"), None)
        selected = min(scored_cards, key=lambda entry: entry["score"])
        if selected["score"] >= 70:
            return None
        return selector_methods._safe_int(selected.get("index"), None)

    def shop_remove_card_debug_entry(self, card: Dict[str, Any], context: Dict[str, Any], selector_methods) -> Dict[str, Any]:
        score = selector_methods._score_defect_deck_card(card, context)
        texts = sorted(selector_methods._card_option_texts(card))
        name = str(card.get("name") or card.get("card_name") or (texts[0] if texts else ""))
        return {
            "index": selector_methods._safe_int(card.get("index"), -1),
            "name": name,
            "priority": selector_methods._shop_remove_priority(card),
            "score": score,
            "rarity": str(card.get("rarity") or ""),
            "card_type": str(card.get("card_type") or card.get("type") or ""),
            "removable": selector_methods._is_shop_removable_card(card),
        }

    def is_shop_removable_card(self, card: Dict[str, Any], selector_methods) -> bool:
        texts = selector_methods._card_option_texts(card)
        if any(alias in text for text in texts for alias in selector_methods._shop_unremovable_card_aliases()):
            return False
        return not bool(card.get("unremovable") or card.get("cannot_remove"))

    def shop_unremovable_card_aliases(self, selector_methods) -> Set[str]:
        try:
            constraints = selector_methods._load_strategy_constraints(selector_methods._configured_character_strategy())
        except RuntimeError:
            return set()
        shop_preferences = constraints.get("shop_preferences") if isinstance(constraints, dict) else {}
        card_preferences = shop_preferences.get("card") if isinstance(shop_preferences, dict) else {}
        unremovable = card_preferences.get("unremovable") if isinstance(card_preferences, dict) else {}
        aliases: Set[str] = set()
        for items in unremovable.values() if isinstance(unremovable, dict) else []:
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, str) and item.strip():
                    aliases.add(item.strip().lower())
        return aliases

    def shop_remove_priority(self, card: Dict[str, Any], selector_methods) -> int:
        rarity = str(card.get("rarity") or "").strip().lower()
        card_type = str(card.get("card_type") or card.get("type") or "").strip().lower()
        if rarity == "curse":
            return 0
        if rarity == "basic" and card_type in {"attack", "skill"}:
            return 1
        return 2

    def score_defect_deck_card(self, card: Dict[str, Any], context: Dict[str, Any], selector_methods) -> int:
        option = {"texts": selector_methods._card_option_texts(card), "raw": card, "index": selector_methods._safe_int(card.get("index"), 0)}
        base_score = selector_methods._score_defect_card_option(option, context)
        card_type = str(card.get("card_type") or card.get("type") or "").strip().lower()
        rarity = str(card.get("rarity") or "").strip().lower()
        if rarity == "basic":
            if card_type == "attack":
                return min(base_score, 15)
            if card_type == "skill":
                return min(base_score, 25)
        if rarity == "curse":
            return -100
        if rarity == "status":
            return -80
        return base_score

    def score_shop_named_option(self, option: Dict[str, Any], context: Dict[str, Any], item_type: str, selector_methods) -> int:
        texts = option.get("texts") if isinstance(option.get("texts"), set) else set()
        constraints = selector_methods._load_strategy_constraints(selector_methods._configured_character_strategy())
        shop_preferences = constraints.get("shop_preferences") if isinstance(constraints, dict) else {}
        bucket = shop_preferences.get(item_type) if isinstance(shop_preferences, dict) and isinstance(shop_preferences.get(item_type), dict) else {}
        score = 0
        for category, bonus in (("required", 36), ("high_priority", 22), ("low_priority", -20)):
            entries = bucket.get(category) if isinstance(bucket.get(category), dict) else {}
            for items in entries.values():
                if any(alias in text for text in texts for alias in items if isinstance(alias, str)):
                    score += bonus
        conditional_entries = bucket.get("conditional") if isinstance(bucket.get("conditional"), dict) else {}
        for entry in conditional_entries.values():
            items = entry.get("items") if isinstance(entry, dict) else []
            if any(alias in text for text in texts for alias in items if isinstance(alias, str)):
                score += 10
        return score

    def find_preferred_card_option_index(self, raw: Dict[str, Any], context: Dict[str, Any], selector_methods) -> Optional[int]:
        if selector_methods._configured_character_strategy() != "defect":
            return None
        options = selector_methods._card_reward_options(raw, context)
        if options:
            selector_methods._log_card_reward_options(options, context)
        if not selector_methods._is_card_reward_context(raw, context):
            return None
        if not options:
            return None
        best_option: Optional[Dict[str, Any]] = None
        best_score: Optional[int] = None
        for option in options:
            score = selector_methods._score_defect_card_option(option, context)
            if best_score is None or score > best_score:
                best_option = option
                best_score = score
        if best_option is None or best_score is None or best_score <= 0:
            return None
        return best_option["index"]

    def find_preferred_map_option_index(self, raw: Dict[str, Any], context: Dict[str, Any], selector_methods) -> Optional[int]:
        if selector_methods._configured_character_strategy() != "defect":
            return None
        options = selector_methods._extract_generic_option_descriptions(raw)
        if not options:
            return None
        best_option: Optional[Dict[str, Any]] = None
        best_score: Optional[int] = None
        for option in options:
            score = selector_methods._score_defect_map_option(option, context)
            if best_score is None or score > best_score:
                best_option = option
                best_score = score
        if best_option is None or best_score is None:
            return None
        return int(best_option["index"])

    def score_defect_card_option(self, option: Dict[str, Any], context: Dict[str, Any], selector_methods) -> int:
        return selector_methods._score_defect_card_option_details(option, context).get("score", 0)

    def score_defect_card_option_details(self, option: Dict[str, Any], context: Dict[str, Any], selector_methods) -> Dict[str, Any]:
        texts = option.get("texts") if isinstance(option.get("texts"), set) else set()
        constraints = selector_methods._load_strategy_constraints("defect") if selector_methods._configured_character_strategy() == "defect" else {}
        score = 0
        base_score = 0
        constraint_hits: List[str] = []
        high_priority = {
            "冷头": 100,
            "coolheaded": 100,
            "快速检索": 95,
            "skim": 95,
            "全息影像": 95,
            "hologram": 95,
            "暴风雨": 92,
            "tempest": 92,
            "冰川": 90,
            "glacier": 90,
            "充电": 82,
            "charge_battery": 82,
            "charge battery": 82,
            "高速脱离": 80,
            "sweeping_beam": 80,
            "sweeping beam": 80,
            "白噪声": 72,
            "白噪音": 72,
            "white_noise": 72,
            "white noise": 72,
            "引雷针": 94,
            "static_discharge": 94,
            "static discharge": 94,
            "电流相生": 92,
            "electrodynamics": 92,
            "子程序": 96,
            "loop": 96,
            "雷暴": 94,
            "storm": 94,
            "创造性ai": 88,
            "创造性 ai": 88,
            "creative_ai": 88,
            "creative ai": 88,
            "超临界态": 86,
            "hyperbeam": 86,
            "双倍": 84,
            "double_energy": 84,
            "double energy": 84,
            "内核加速": 82,
            "turbo": 82,
            "火箭拳": 90,
            "go for the eyes": 90,
            "go_for_the_eyes": 90,
            "污秽攻击": 74,
            "gunk up": 74,
            "gunk up": 74,
            "压缩": 88,
            "recycle": 88,
            "羽化": 90,
            "claw": 90,
            "万众一心": 84,
            "all_for_one": 84,
            "all for one": 84,
            "扩容": 78,
            "capacitor": 78,
            "弹幕齐射": 58,
            "barrage": 58,
            "超越光速": 70,
            "ftl": 70,
            "暗影之盾": 60,
            "shadow_shield": 60,
            "shadow shield": 60,
        }
        low_priority = {
            "打击": -25,
            "strike": -25,
            "防御": -10,
            "defend": -10,
            "硬撑": -35,
            "steam_barrier": -35,
            "steam barrier": -35,
            "超频": -30,
            "overclock": -30,
        }
        matched_high_scores = [
            value
            for name, value in high_priority.items()
            if any(name in text for text in texts)
        ]
        if matched_high_scores:
            best_high = max(matched_high_scores)
            score += best_high
            base_score += best_high
        matched_low_scores = [
            value
            for name, value in low_priority.items()
            if any(name in text for text in texts)
        ]
        if matched_low_scores:
            worst_low = min(matched_low_scores)
            score += worst_low
            base_score += worst_low
        for category, bonus in (("required", 36), ("high_priority", 22), ("low_priority", -20)):
            bucket = constraints.get(category) if isinstance(constraints.get(category), dict) else {}
            for label, cards in bucket.items():
                if any(any(card in text for text in texts) for card in cards if isinstance(card, str)):
                    score += bonus
                    constraint_hits.append(f"{category}:{label}")
        conditional_bucket = constraints.get("conditional") if isinstance(constraints.get("conditional"), dict) else {}
        for label, cards in conditional_bucket.items():
            if any(any(card in text for text in texts) for card in cards if isinstance(card, str)):
                score += 10
                constraint_hits.append(f"conditional:{label}")
        if any("状态" in text for text in texts) and not selector_methods._defect_has_card(context, {"压缩", "recycle"}):
            score -= 18
        if any(any(keyword in text for keyword in {"能力", "power"}) for text in texts):
            score += 8
        if any(any(keyword in text for keyword in {"球", "闪电球", "冰球", "充能球", "orb"}) for text in texts):
            score += 10
        return {"score": score, "base_score": base_score, "constraint_hits": constraint_hits}

    def find_preferred_character_option_index(self, raw: Dict[str, Any], context: Dict[str, Any], selector_methods) -> Optional[int]:
        if not selector_methods._is_character_select_context(context):
            return None
        preferred_aliases = [
            {"故障机器人", "defect"},
            {"铁血战士", "ironclad"},
        ]
        options = selector_methods._character_selection_options(raw, context)
        if not options:
            return None
        for aliases in preferred_aliases:
            for option in options:
                if selector_methods._character_option_matches(option, aliases):
                    return option["index"]
        return None

    def find_discardable_potion_index(self, context: Dict[str, Any], selector_methods) -> int:
        for potion in selector_methods._potions(context):
            if bool(potion.get("can_discard")):
                return int(potion.get("index", 0))
        raise RuntimeError("当前没有可丢弃的药水")

    def find_usable_potion_index(self, context: Dict[str, Any], selector_methods) -> int:
        for potion in selector_methods._potions(context):
            if bool(potion.get("can_use")):
                return int(potion.get("index", 0))
        raise RuntimeError("当前没有可使用的药水")

    def find_playable_card_index(self, context: Dict[str, Any], selector_methods, combat_analyzer) -> int:
        combat = selector_methods._combat_state(context)
        tactical_summary = combat_analyzer.build_tactical_summary(combat, selector_methods._load_strategy_constraints)
        target_index = tactical_summary.get("recommended_target_index")
        best_damage_card = combat_analyzer._best_playable_damage_card(combat, target_index=target_index)
        if best_damage_card is not None:
            return int(best_damage_card.get("index", 0))
        best_block_card = combat_analyzer._best_playable_block_card(combat)
        if best_block_card is not None:
            return int(best_block_card.get("index", 0))
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        for card in hand:
            if isinstance(card, dict) and bool(card.get("playable")):
                return int(card.get("index", 0))
        raise RuntimeError("当前没有可打出的卡牌")

    def find_card_target_index(self, context: Dict[str, Any], card_index: int, selector_methods, combat_analyzer) -> Optional[int]:
        combat = selector_methods._combat_state(context)
        tactical_summary = combat_analyzer.build_tactical_summary(combat, selector_methods._load_strategy_constraints)
        preferred_target = tactical_summary.get("recommended_target_index")
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        for card in hand:
            if not isinstance(card, dict) or int(card.get("index", -1)) != card_index:
                continue
            valid_target_indices = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
            if preferred_target is not None and selector_methods._safe_int(preferred_target, -9999) in [selector_methods._safe_int(target, -1) for target in valid_target_indices]:
                return int(preferred_target)
            if valid_target_indices:
                return int(valid_target_indices[0])
            return None
        return None

    def action_for_card(self, actions: List[Dict[str, Any]], card: Dict[str, Any], target_index: Any, selector_methods) -> Optional[Dict[str, Any]]:
        valid_targets = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
        resolved_target_index: Optional[int] = None
        if valid_targets:
            if target_index is not None and selector_methods._safe_int(target_index, -9999) in [selector_methods._safe_int(target, -1) for target in valid_targets]:
                resolved_target_index = selector_methods._safe_int(target_index)
            else:
                resolved_target_index = selector_methods._safe_int(valid_targets[0])
        for action in actions:
            if not isinstance(action, dict):
                continue
            raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            action_type = str(action.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or "")
            if action_type != "play_card":
                continue
            selected = dict(action)
            selected_raw = dict(raw)
            selected_raw["card_index"] = selector_methods._safe_int(card.get("index"))
            if resolved_target_index is not None:
                selected_raw["target_index"] = resolved_target_index
            selected["raw"] = selected_raw
            return selected
        return None
