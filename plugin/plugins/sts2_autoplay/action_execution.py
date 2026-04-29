from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Any, Awaitable, Dict, Optional


class ActionExecutionMixin:
    def _describe_legal_action(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
        action_type = str(action.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or "")
        return {
            "action_type": action_type,
            "label": str(action.get("label") or raw.get("label") or raw.get("description") or action_type),
            "allowed_kwargs": self._allowed_kwargs_for_action(action_type, raw, context),
        }

    def build_tactical_summary(self, combat: dict[str, Any], *, character_strategy: Optional[str] = None) -> dict[str, Any]:
        resolved_strategy = character_strategy or self._configured_character_strategy()
        return self._combat_analyzer.build_tactical_summary(combat, lambda s: self._load_strategy_constraints(s or resolved_strategy), resolved_strategy)

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except Exception:
            return default

    def _card_damage_value(self, card: dict[str, Any]) -> int:
        return self._combat_analyzer._card_damage_value(card)

    def _card_block_value(self, card: dict[str, Any]) -> int:
        return self._combat_analyzer._card_block_value(card)

    def _card_hits_value(self, card: dict[str, Any]) -> int:
        return self._combat_analyzer._card_hits_value(card)

    def _card_total_damage_value(self, card: dict[str, Any], combat: Optional[dict[str, Any]] = None, target_index: Any = None, strategy_constraints: Optional[dict[str, Any]] = None) -> int:
        return self._combat_analyzer._card_total_damage_value(card, combat, target_index, strategy_constraints)

    def _card_can_target_enemy(self, card: dict[str, Any], target_index: Any, combat: Optional[dict[str, Any]] = None) -> bool:
        return self._combat_analyzer._card_can_target_enemy(card, target_index, combat)

    def _enemy_hp_value(self, enemy: dict[str, Any]) -> int:
        return self._combat_analyzer._enemy_hp_value(enemy)

    def _enemy_block_value(self, enemy: dict[str, Any]) -> int:
        return self._combat_analyzer._enemy_block_value(enemy)

    def _enemy_intent_attack_total(self, enemy: dict[str, Any]) -> int:
        return self._combat_analyzer._enemy_intent_attack_total(enemy)

    def _combat_state(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._context_analyzer._combat_state(context)

    def _log_combat_block_fields(self, context: dict[str, Any]) -> None:
        self._combat_analyzer._log_combat_block_fields(context)

    def _combat_player_block(self, combat: dict[str, Any]) -> int:
        return self._combat_analyzer._combat_player_block(combat)

    def _potions(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return self._context_analyzer._potions(context)

    def _map_state(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._context_analyzer._map_state(context)

    def _build_map_summary(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._context_analyzer._build_map_summary(context)

    def _extract_generic_option_descriptions(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        return self._context_analyzer._extract_generic_option_descriptions(raw)

    def _allowed_kwargs_for_action(self, action_type: str, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, list[Any]]:
        return self._allowed_kwargs_impl(action_type, raw, context)

    def _allowed_kwargs_impl(self, action_type: str, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, list[Any]]:
        allowed: dict[str, list[Any]] = {}
        if not self._action_requires_index(action_type, raw):
            return allowed
        if action_type == "discard_potion":
            option_indices = [
                self._safe_int(potion.get("index"), None)
                for potion in self._potions(context)
                if bool(potion.get("can_discard"))
            ]
            allowed["option_index"] = [index for index in option_indices if index is not None]
        elif action_type == "use_potion":
            option_indices = [
                self._safe_int(potion.get("index"), None)
                for potion in self._potions(context)
                if bool(potion.get("can_use"))
            ]
            allowed["option_index"] = [index for index in option_indices if index is not None]
        elif action_type == "play_card":
            combat = self._combat_state(context)
            hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
            playable_cards = [card for card in hand if isinstance(card, dict) and bool(card.get("playable"))]
            card_indices = [self._safe_int(card.get("index"), None) for card in playable_cards]
            allowed["card_index"] = [index for index in card_indices if index is not None]
            target_values = sorted({
                target_index
                for card in playable_cards
                for target_index in [
                    self._safe_int(target, None)
                    for target in (card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else [])
                ]
                if target_index is not None
            })
            if target_values:
                allowed["target_index"] = target_values
        elif action_type in {"choose_map_node", "choose_treasure_relic", "choose_event_option", "choose_rest_option", "select_deck_card", "choose_reward_card", "buy_card", "buy_relic", "buy_potion", "claim_reward"}:
            option_indices = self._known_option_indices_for_action(action_type, raw, context)
            if option_indices:
                allowed["option_index"] = option_indices
        else:
            generic_indices = self._extract_generic_option_indices(raw)
            if generic_indices:
                allowed["index"] = generic_indices
        return allowed

    def _action_requires_index(self, action_type: str, raw: dict[str, Any]) -> bool:
        if bool(raw.get("requires_index")):
            return True
        return action_type in {
            "choose_map_node",
            "choose_treasure_relic",
            "choose_event_option",
            "choose_rest_option",
            "select_deck_card",
            "choose_reward_card",
            "buy_card",
            "buy_relic",
            "buy_potion",
            "claim_reward",
            "discard_potion",
            "use_potion",
            "play_card",
        }

    def _known_option_indices_for_action(self, action_type: str, raw: dict[str, Any], context: dict[str, Any]) -> list[int]:
        if action_type == "buy_card":
            option_indices = [option["index"] for option in self._shop_card_options(context)]
        elif action_type == "buy_relic":
            option_indices = [option["index"] for option in self._shop_relic_options(context)]
        elif action_type == "buy_potion":
            option_indices = [option["index"] for option in self._shop_potion_options(context)]
        else:
            option_indices = [option["index"] for option in self._card_reward_options(raw, context)]
            if not option_indices:
                option_indices = [option["index"] for option in self._character_selection_options(raw, context)]
        if not option_indices:
            option_indices = self._extract_generic_option_indices(raw)
        deduped: list[int] = []
        for value in option_indices:
            try:
                normalized_value = int(value)
            except Exception:
                continue
            if normalized_value not in deduped:
                deduped.append(normalized_value)
        return deduped

    def _extract_generic_option_indices(self, raw: dict[str, Any]) -> list[int]:
        indices: list[int] = []
        for candidate in self._context_analyzer._iter_option_candidates(raw):
            if not isinstance(candidate, list):
                continue
            for idx, item in enumerate(candidate):
                if not isinstance(item, dict):
                    continue
                value = item.get("option_index", item.get("index", idx))
                try:
                    indices.append(int(value))
                except Exception:
                    continue
        deduped: list[int] = []
        for value in indices:
            if value not in deduped:
                deduped.append(value)
        return deduped

    def _validate_llm_decision(self, decision: dict[str, Any], context: dict[str, Any]) -> Optional[dict[str, Any]]:
        return self._validate_llm_decision_impl(decision, context)

    def _validate_llm_decision_impl(self, decision: dict[str, Any], context: dict[str, Any]) -> Optional[dict[str, Any]]:
        action_type = str(decision.get("action_type") or "").strip()
        kwargs = decision.get("kwargs")
        if not action_type or not isinstance(kwargs, dict):
            self.logger.warning(f"LLM 决策格式非法: {decision}")
            return None
        for action in context.get("actions", []):
            if not isinstance(action, dict):
                continue
            raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            current_type = str(action.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or "")
            if current_type != action_type:
                continue
            allowed_kwargs = self._allowed_kwargs_impl(action_type, raw, context)
            normalized_kwargs: dict[str, int] = {}
            if any(key not in allowed_kwargs for key in kwargs):
                self.logger.warning(f"LLM 决策包含非法参数: {decision}")
                return None
            for key, values in allowed_kwargs.items():
                if key not in kwargs:
                    continue
                raw_value = kwargs[key]
                if raw_value is None and action_type == "play_card" and key == "target_index":
                    continue
                try:
                    normalized_value = int(raw_value)
                except Exception:
                    self.logger.warning(f"LLM 决策参数类型非法: {decision}")
                    return None
                if values and normalized_value not in values:
                    if action_type in {"choose_reward_card", "select_deck_card"} and key == "option_index":
                        continue
                    self.logger.warning(f"LLM 决策参数越界: {decision}")
                    return None
                normalized_kwargs[key] = normalized_value
            if action_type in {"choose_reward_card", "select_deck_card"} and "option_index" not in normalized_kwargs:
                fallback_kwargs = self._normalize_action_kwargs(action_type, raw, context)
                fallback_option_index = fallback_kwargs.get("option_index")
                allowed_option_indices = allowed_kwargs.get("option_index", [])
                if fallback_option_index is not None and (not allowed_option_indices or int(fallback_option_index) in allowed_option_indices):
                    normalized_kwargs["option_index"] = int(fallback_option_index)
            if self._action_requires_index(action_type, raw) and (not normalized_kwargs or (action_type == "play_card" and "card_index" not in normalized_kwargs)):
                fallback_kwargs = self._normalize_action_kwargs(action_type, raw, context)
                fallback_normalized: dict[str, int] = {}
                for key, value in fallback_kwargs.items():
                    if key not in allowed_kwargs or key in normalized_kwargs:
                        continue
                    try:
                        normalized_value = int(value)
                    except Exception:
                        continue
                    allowed_values = allowed_kwargs.get(key, [])
                    if allowed_values and normalized_value not in allowed_values:
                        continue
                    fallback_normalized[key] = normalized_value
                normalized_kwargs.update(fallback_normalized)
            if action_type == "play_card" and "card_index" not in normalized_kwargs:
                self.logger.warning(f"LLM 决策缺少卡牌索引: {decision}")
                return None
            if action_type == "play_card" and not self._validate_play_card_target_combo(normalized_kwargs, context, decision):
                return None
            validated = dict(action)
            validated_raw = dict(raw)
            validated_raw.update(normalized_kwargs)
            validated["raw"] = validated_raw
            return validated
        self.logger.warning(f"LLM 决策动作不在当前合法动作中: {decision}")
        return None

    def _validate_play_card_target_combo(self, normalized_kwargs: dict[str, int], context: dict[str, Any], decision: dict[str, Any]) -> bool:
        card_index = normalized_kwargs.get("card_index")
        if card_index is None:
            return True

        combat = self._combat_state(context)
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        selected_card = None
        for card in hand:
            if not isinstance(card, dict) or not bool(card.get("playable")):
                continue
            if self._safe_int(card.get("index"), -1) == card_index:
                selected_card = card
                break
        if selected_card is None:
            self.logger.warning(f"LLM 决策卡牌不可打出: {decision}")
            return False

        valid_targets = selected_card.get("valid_target_indices") if isinstance(selected_card.get("valid_target_indices"), list) else []
        normalized_valid_targets: list[int] = []
        for target in valid_targets:
            try:
                normalized_target = int(target)
            except Exception:
                continue
            if normalized_target not in normalized_valid_targets:
                normalized_valid_targets.append(normalized_target)
        if not normalized_valid_targets:
            normalized_kwargs.pop("target_index", None)
            return True

        target_index = normalized_kwargs.get("target_index")
        if target_index is None:
            fallback_target = self._find_card_target_index(context, card_index)
            normalized_fallback_target = self._safe_int(fallback_target, -9999)
            if fallback_target is not None and normalized_fallback_target in normalized_valid_targets:
                normalized_kwargs["target_index"] = normalized_fallback_target
                return True
            self.logger.warning(f"LLM 决策缺少卡牌目标: {decision}")
            return False

        if self._safe_int(target_index, -9999) not in normalized_valid_targets:
            self.logger.warning(f"LLM 决策卡牌目标组合非法: {decision}")
            return False
        normalized_kwargs["target_index"] = self._safe_int(target_index)
        return True

    async def _execute_action(self, prepared: dict[str, Any]) -> Dict[str, Any]:
        client = self._require_client()
        action_type = str(prepared.get("action_type") or "")
        kwargs = prepared.get("kwargs") if isinstance(prepared.get("kwargs"), dict) else {}
        context = prepared.get("context") if isinstance(prepared.get("context"), dict) else {}
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        screen = snapshot.get("screen") or snapshot.get("normalized_screen") or "unknown"
        actions = context.get("actions") if isinstance(context.get("actions"), list) else []
        action_summaries = [
            {
                "type": str(action.get("type") or ""),
                "raw_name": str(action.get("raw", {}).get("name") or "") if isinstance(action.get("raw"), dict) else "",
                "allowed_kwargs": self._allowed_kwargs_impl(
                    str(action.get("type") or ""),
                    action.get("raw") if isinstance(action.get("raw"), dict) else {},
                    context,
                ),
            }
            for action in actions
            if isinstance(action, dict)
        ]
        self.logger.info(
            f"[sts2_autoplay][action] screen={screen} action_type={action_type} kwargs={kwargs} available_actions={action_summaries}"
        )
        result = await client.execute_action(action_type, **kwargs)
        self._last_action = action_type
        self._last_action_at = time.time()
        self._history.appendleft({"type": "action", "time": self._last_action_at, "action": action_type, "result": result, "kwargs": kwargs})
        self._emit_status()
        return {"status": "ok", "message": f"已执行动作: {action_type}", "action": action_type, "result": result}

    def _normalize_action_kwargs(self, action_type: str, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        kwargs = {
            k: v
            for k, v in raw.items()
            if k not in {"type", "name", "label", "description", "requires_target", "requires_index", "shop_remove_selection"}
            and not (k == "action" and isinstance(v, dict))
        }
        if action_type in {"choose_reward_card", "select_deck_card", "skip_reward_cards", "collect_rewards_and_proceed", "claim_reward"}:
            reward_options = self._card_reward_options(raw, context)
            if reward_options:
                self._log_card_reward_options(reward_options, context)
        allowed_option_indices = self._allowed_kwargs_impl(action_type, raw, context).get("option_index", [])
        if action_type in {"choose_reward_card", "select_deck_card"} and not bool(raw.get("shop_remove_selection")):
            shop_remove_index = self._find_shop_remove_card_index_for_selection(context)
            if shop_remove_index is not None:
                kwargs["option_index"] = shop_remove_index
                return kwargs
            preferred_option_index = self._find_preferred_card_option_index(raw, context)
            if preferred_option_index is not None:
                kwargs["option_index"] = preferred_option_index
        if "option_index" not in kwargs and "index" not in kwargs and "card_index" not in kwargs and self._action_requires_index(action_type, raw):
            if action_type == "discard_potion":
                kwargs["option_index"] = self._find_discardable_potion_index(context)
            elif action_type in {"choose_map_node", "choose_treasure_relic", "choose_event_option", "choose_rest_option", "select_deck_card", "choose_reward_card", "buy_card", "buy_relic", "buy_potion", "claim_reward"}:
                preferred_option_index = self._find_preferred_map_option_index(raw, context) if action_type == "choose_map_node" else None
                if preferred_option_index is None and action_type == "claim_reward":
                    preferred_option_index = self._find_claimable_card_reward_index(context)
                if preferred_option_index is None and action_type == "buy_card":
                    preferred_option_index = self._find_preferred_shop_card_index(context)
                if preferred_option_index is None and action_type == "buy_relic":
                    preferred_option_index = self._find_preferred_shop_relic_index(context)
                if preferred_option_index is None and action_type == "buy_potion":
                    preferred_option_index = self._find_preferred_shop_potion_index(context)
                if preferred_option_index is None:
                    preferred_option_index = self._find_preferred_character_option_index(raw, context)
                if preferred_option_index is not None:
                    chosen_option_index = preferred_option_index
                    if allowed_option_indices and int(chosen_option_index) not in allowed_option_indices:
                        chosen_option_index = allowed_option_indices[0]
                    kwargs["option_index"] = chosen_option_index
                elif allowed_option_indices:
                    kwargs["option_index"] = allowed_option_indices[0]
            elif action_type == "use_potion":
                kwargs["option_index"] = self._find_usable_potion_index(context)
            elif action_type == "play_card":
                kwargs["card_index"] = self._find_playable_card_index(context)
                target_index = self._find_card_target_index(context, kwargs["card_index"])
                if target_index is not None:
                    kwargs["target_index"] = target_index
            else:
                generic_indices = self._extract_generic_option_indices(raw)
                if generic_indices:
                    kwargs["index"] = generic_indices[0]
        return kwargs

    def _find_shop_remove_card_index_for_selection(self, context: dict[str, Any]) -> Optional[int]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        if self._normalized_screen_name(snapshot) != "card_selection":
            return None
        if not self._is_shop_remove_selection_context(context):
            return None
        actions = context.get("actions") if isinstance(context.get("actions"), list) else []
        has_select_deck_card = any(
            isinstance(action, dict) and str(action.get("type") or "") == "select_deck_card"
            for action in actions
        )
        if not has_select_deck_card:
            return None
        remove_index = self._find_shop_remove_card_index(context)
        return remove_index

    def _is_shop_remove_selection_context(self, context: dict[str, Any]) -> bool:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        shop = raw_state.get("shop") if isinstance(raw_state.get("shop"), dict) else {}
        card_removal = shop.get("card_removal") if isinstance(shop.get("card_removal"), dict) else {}
        if bool(card_removal.get("available")) and bool(card_removal.get("enough_gold")):
            return True
        return self._last_action == "remove_card_at_shop"

    def _find_preferred_card_option_index(self, raw: dict[str, Any], context: dict[str, Any]) -> Optional[int]:
        return self._heuristic_selector.find_preferred_card_option_index(raw, context, self)

    def _find_preferred_map_option_index(self, raw: dict[str, Any], context: dict[str, Any]) -> Optional[int]:
        return self._heuristic_selector.find_preferred_map_option_index(raw, context, self)

    def _score_strategy_map_option_details(self, option: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return self._heuristic_selector.score_strategy_map_option_details(option, context, self)

    def _log_card_reward_options(self, options: list[dict[str, Any]], context: dict[str, Any]) -> None:
        try:
            scored_options = []
            for option in options:
                if not isinstance(option, dict):
                    continue
                strategy_details = self._score_strategy_card_option_details(option, context)
                defect_details = self._score_defect_card_option_details(option, context) if self._configured_character_strategy() == "defect" else {"score": 0, "constraint_hits": [], "base_score": 0}
                total_score = strategy_details.get("score", 0) + defect_details.get("score", 0)
                scored_options.append({
                    "index": option.get("index"),
                    "texts": sorted(option.get("texts")) if isinstance(option.get("texts"), set) else option.get("texts"),
                    "score": total_score,
                    "strategy_score": strategy_details.get("score", 0),
                    "defect_score": defect_details.get("score", 0),
                    "constraint_hits": strategy_details.get("constraint_hits", []) + defect_details.get("constraint_hits", []),
                    "base_score": defect_details.get("base_score", 0),
                })
            snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
            screen = self._normalized_screen_name(snapshot)
            self.logger.info(
                f"[sts2_autoplay][reward-options] screen={screen} option_count={len(scored_options)} scored_options={scored_options}"
            )
        except Exception as exc:
            self.logger.warning(f"记录卡牌奖励候选失败: {exc}")

    def _is_card_reward_context(self, raw: dict[str, Any], context: dict[str, Any]) -> bool:
        return self._context_analyzer._is_card_reward_context(raw, context)

    def _card_reward_options(self, raw: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        return self._context_analyzer._card_reward_options(raw, context)

    def _is_rewardish_screen(self, snapshot: dict[str, Any]) -> bool:
        return self._context_analyzer._is_rewardish_screen(snapshot)

    def _log_reward_payload_debug(self, raw: dict[str, Any], context: dict[str, Any]) -> None:
        self._context_analyzer._log_reward_payload_debug(raw, context)

    def _iter_option_candidates(self, raw: dict[str, Any]) -> list[Any]:
        return self._context_analyzer._iter_option_candidates(raw)

    def _extract_card_reward_options(self, candidate: Any) -> list[dict[str, Any]]:
        return self._context_analyzer._extract_card_reward_options(candidate)

    def _card_option_texts(self, item: dict[str, Any]) -> set[str]:
        return self._context_analyzer._card_option_texts(item)

    def _score_defect_card_option(self, option: dict[str, Any], context: dict[str, Any]) -> int:
        return self._heuristic_selector.score_defect_card_option(option, context, self)

    def _score_defect_card_option_details(self, option: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return self._heuristic_selector.score_defect_card_option_details(option, context, self)

    def _score_defect_map_option(self, option: dict[str, Any], context: dict[str, Any]) -> int:
        return self._context_analyzer._score_defect_map_option(option, context)

    def _defect_has_card(self, context: dict[str, Any], names: set[str]) -> bool:
        return self._context_analyzer._defect_has_card(context, names)

    def _find_preferred_character_option_index(self, raw: dict[str, Any], context: dict[str, Any]) -> Optional[int]:
        return self._heuristic_selector.find_preferred_character_option_index(raw, context, self)

    def _is_character_select_context(self, context: dict[str, Any]) -> bool:
        return self._context_analyzer._is_character_select_context(context)

    def _character_selection_options(self, raw: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        return self._context_analyzer._character_selection_options(raw, context)

    def _shop_card_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return self._context_analyzer._shop_card_options(context)

    def _shop_relic_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return self._context_analyzer._shop_relic_options(context)

    def _shop_potion_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return self._context_analyzer._shop_potion_options(context)

    def _extract_character_options(self, candidate: Any) -> list[dict[str, Any]]:
        return self._context_analyzer._extract_character_options(candidate)

    def _character_option_texts(self, item: dict[str, Any]) -> set[str]:
        return self._context_analyzer._character_option_texts(item)

    def _character_option_matches(self, option: dict[str, Any], aliases: set[str]) -> bool:
        return self._context_analyzer._character_option_matches(option, aliases)

    def _find_discardable_potion_index(self, context: dict[str, Any]) -> int:
        return self._heuristic_selector.find_discardable_potion_index(context, self)

    def _find_usable_potion_index(self, context: dict[str, Any]) -> int:
        return self._heuristic_selector.find_usable_potion_index(context, self)

    def _find_playable_card_index(self, context: dict[str, Any]) -> int:
        return self._heuristic_selector.find_playable_card_index(context, self, self._combat_analyzer)

    def _find_card_target_index(self, context: dict[str, Any], card_index: int) -> Optional[int]:
        return self._heuristic_selector.find_card_target_index(context, card_index, self, self._combat_analyzer)
