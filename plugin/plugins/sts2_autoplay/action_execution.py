from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

JsonObject = dict[str, Any]
IntArgs = dict[str, int]

_INDEX_ACTION_TYPES = frozenset({
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
})
_OPTION_INDEX_ACTION_TYPES = frozenset({
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
})
_REWARD_CARD_ACTION_TYPES = frozenset({"choose_reward_card", "select_deck_card"})
_REWARDISH_ACTION_TYPES = frozenset({
    "choose_reward_card",
    "select_deck_card",
    "skip_reward_cards",
    "collect_rewards_and_proceed",
    "claim_reward",
})
_RAW_ACTION_METADATA_KEYS = frozenset({
    "type",
    "name",
    "label",
    "description",
    "requires_target",
    "requires_index",
    "shop_remove_selection",
})


@dataclass(frozen=True)
class LegalActionDescription:
    action_type: str
    label: str
    allowed_kwargs: dict[str, list[int]]

    def as_dict(self) -> JsonObject:
        return {
            "action_type": self.action_type,
            "label": self.label,
            "allowed_kwargs": self.allowed_kwargs,
        }


def _mapping_or_empty(value: Any) -> JsonObject:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_or_empty(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _dedupe_ints(values: Iterable[Any]) -> list[int]:
    return list(dict.fromkeys(value for value in (_to_int(item) for item in values) if value is not None))


def _first_allowed_or_preferred(preferred: Optional[int], allowed: Sequence[int]) -> Optional[int]:
    if preferred is not None and (not allowed or preferred in allowed):
        return preferred
    return allowed[0] if allowed else None


def _action_type_from(action: Mapping[str, Any]) -> str:
    raw = _mapping_or_empty(action.get("raw"))
    return str(action.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or "").strip()


def _action_label_from(action: Mapping[str, Any], action_type: str) -> str:
    raw = _mapping_or_empty(action.get("raw"))
    return str(action.get("label") or raw.get("label") or raw.get("description") or action_type)


def _is_valid_kwarg_name(key: Any, allowed_kwargs: Mapping[str, Sequence[int]]) -> bool:
    return isinstance(key, str) and key in allowed_kwargs


class ActionExecutionMixin:
    def _describe_legal_action(self, action: JsonObject, context: JsonObject) -> JsonObject:
        raw = _mapping_or_empty(action.get("raw"))
        action_type = _action_type_from(action)
        return LegalActionDescription(
            action_type=action_type,
            label=_action_label_from(action, action_type),
            allowed_kwargs=self._allowed_kwargs_for_action(action_type, raw, context),
        ).as_dict()

    def build_tactical_summary(self, combat: dict[str, Any], *, character_strategy: Optional[str] = None) -> dict[str, Any]:
        resolved_strategy = character_strategy or self._configured_character_strategy()
        if hasattr(self, "_safe_strategy_constraints"):
            strategy_constraints = self._safe_strategy_constraints(resolved_strategy)
        else:
            try:
                strategy_constraints = self._load_strategy_constraints(resolved_strategy)
            except RuntimeError as exc:
                self.logger.warning(f"加载策略约束失败，使用空战术约束: strategy={resolved_strategy}, error={exc}")
                strategy_constraints = {}
        return self._combat_analyzer.build_tactical_summary(combat, lambda _strategy: strategy_constraints, resolved_strategy)

    def _safe_int(self, value: Any, default: int = 0) -> int:
        normalized = _to_int(value)
        return normalized if normalized is not None else default

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

    def _allowed_kwargs_for_action(self, action_type: str, raw: JsonObject, context: JsonObject) -> dict[str, list[int]]:
        return self._allowed_kwargs_impl(action_type.strip(), raw, context)

    def _allowed_kwargs_impl(self, action_type: str, raw: JsonObject, context: JsonObject) -> dict[str, list[int]]:
        if not self._action_requires_index(action_type, raw):
            return {}

        if action_type in {"discard_potion", "use_potion"}:
            flag_name = "can_discard" if action_type == "discard_potion" else "can_use"
            return {
                "option_index": _dedupe_ints(
                    potion.get("index")
                    for potion in self._potions(context)
                    if isinstance(potion, Mapping) and bool(potion.get(flag_name))
                )
            }

        if action_type == "play_card":
            combat = self._combat_state(context)
            playable_cards = [
                card
                for card in _list_or_empty(combat.get("hand"))
                if isinstance(card, Mapping) and bool(card.get("playable"))
            ]
            allowed = {"card_index": _dedupe_ints(card.get("index") for card in playable_cards)}
            target_values = sorted(_dedupe_ints(
                target
                for card in playable_cards
                for target in _list_or_empty(card.get("valid_target_indices"))
            ))
            return {**allowed, **({"target_index": target_values} if target_values else {})}

        if action_type in _OPTION_INDEX_ACTION_TYPES:
            option_indices = self._known_option_indices_for_action(action_type, raw, context)
            return {"option_index": option_indices} if option_indices else {}

        generic_indices = self._extract_generic_option_indices(raw)
        return {"index": generic_indices} if generic_indices else {}

    def _action_requires_index(self, action_type: str, raw: JsonObject) -> bool:
        return bool(raw.get("requires_index")) or action_type in _INDEX_ACTION_TYPES

    def _known_option_indices_for_action(self, action_type: str, raw: JsonObject, context: JsonObject) -> list[int]:
        option_sources = {
            "buy_card": lambda: self._shop_card_options(context),
            "buy_relic": lambda: self._shop_relic_options(context),
            "buy_potion": lambda: self._shop_potion_options(context),
        }
        options = option_sources.get(action_type, lambda: self._card_reward_options(raw, context))()
        option_indices = _dedupe_ints(option.get("index") for option in options if isinstance(option, Mapping))

        if not option_indices:
            character_options = self._character_selection_options(raw, context)
            option_indices = _dedupe_ints(option.get("index") for option in character_options if isinstance(option, Mapping))

        return option_indices or self._extract_generic_option_indices(raw)

    def _extract_generic_option_indices(self, raw: JsonObject) -> list[int]:
        return _dedupe_ints(
            item.get("option_index", item.get("index", index))
            for candidate in self._context_analyzer._iter_option_candidates(raw)
            if isinstance(candidate, list)
            for index, item in enumerate(candidate)
            if isinstance(item, Mapping)
        )

    def _validate_llm_decision(self, decision: JsonObject, context: JsonObject) -> Optional[JsonObject]:
        return self._validate_llm_decision_impl(decision, context)

    def _validate_llm_decision_impl(self, decision: JsonObject, context: JsonObject) -> Optional[JsonObject]:
        action_type = str(decision.get("action_type") or "").strip()
        kwargs = _mapping_or_empty(decision.get("kwargs"))
        if not action_type or not isinstance(decision.get("kwargs"), Mapping):
            self.logger.warning(f"LLM 决策格式非法: {decision}")
            return None

        actions = [action for action in _list_or_empty(context.get("actions")) if isinstance(action, Mapping)]
        matching_action = next((action for action in actions if _action_type_from(action) == action_type), None)
        if matching_action is None:
            self.logger.warning(f"LLM 决策动作不在当前合法动作中: {decision}")
            return None

        raw = _mapping_or_empty(matching_action.get("raw"))
        allowed_kwargs = self._allowed_kwargs_impl(action_type, raw, context)
        if any(not _is_valid_kwarg_name(key, allowed_kwargs) for key in kwargs):
            self.logger.warning(f"LLM 决策包含非法参数: {decision}")
            return None

        normalized_kwargs = self._normalize_decision_kwargs(
            action_type=action_type,
            raw=raw,
            context=context,
            decision=decision,
            kwargs=kwargs,
            allowed_kwargs=allowed_kwargs,
        )
        if normalized_kwargs is None:
            return None

        if action_type == "play_card" and not self._validate_play_card_target_combo(normalized_kwargs, context, decision):
            return None

        validated = dict(matching_action)
        validated["raw"] = {**raw, **normalized_kwargs}
        return validated

    def _normalize_decision_kwargs(
        self,
        *,
        action_type: str,
        raw: JsonObject,
        context: JsonObject,
        decision: JsonObject,
        kwargs: JsonObject,
        allowed_kwargs: dict[str, list[int]],
    ) -> Optional[IntArgs]:
        normalized_kwargs: IntArgs = {}

        for key, values in allowed_kwargs.items():
            if key not in kwargs:
                continue
            raw_value = kwargs[key]
            if raw_value is None and action_type == "play_card" and key == "target_index":
                continue
            normalized_value = _to_int(raw_value)
            if normalized_value is None:
                self.logger.warning(f"LLM 决策参数类型非法: {decision}")
                return None
            if values and normalized_value not in values:
                if action_type in _REWARD_CARD_ACTION_TYPES and key == "option_index":
                    continue
                self.logger.warning(f"LLM 决策参数越界: {decision}")
                return None
            normalized_kwargs[key] = normalized_value

        normalized_kwargs.update(
            self._fallback_normalized_kwargs(
                action_type=action_type,
                raw=raw,
                context=context,
                current=normalized_kwargs,
                allowed_kwargs=allowed_kwargs,
            )
        )

        if action_type == "play_card" and "card_index" not in normalized_kwargs:
            self.logger.warning(f"LLM 决策缺少卡牌索引: {decision}")
            return None

        return normalized_kwargs

    def _fallback_normalized_kwargs(
        self,
        *,
        action_type: str,
        raw: JsonObject,
        context: JsonObject,
        current: IntArgs,
        allowed_kwargs: dict[str, list[int]],
    ) -> IntArgs:
        if not self._action_requires_index(action_type, raw):
            return {}
        if current and not (action_type == "play_card" and "card_index" not in current):
            return {}

        fallback_kwargs = self._normalize_action_kwargs(action_type, raw, context)
        return {
            key: normalized_value
            for key, value in fallback_kwargs.items()
            if key in allowed_kwargs and key not in current
            for normalized_value in [_to_int(value)]
            if normalized_value is not None and (not allowed_kwargs.get(key) or normalized_value in allowed_kwargs[key])
        }

    def _validate_play_card_target_combo(self, normalized_kwargs: IntArgs, context: JsonObject, decision: JsonObject) -> bool:
        card_index = normalized_kwargs.get("card_index")
        if card_index is None:
            return True

        combat = self._combat_state(context)
        playable_cards = [
            card
            for card in _list_or_empty(combat.get("hand"))
            if isinstance(card, Mapping) and bool(card.get("playable"))
        ]
        selected_card = next((card for card in playable_cards if _to_int(card.get("index")) == card_index), None)
        if selected_card is None:
            self.logger.warning(f"LLM 决策卡牌不可打出: {decision}")
            return False

        valid_targets = _dedupe_ints(_list_or_empty(selected_card.get("valid_target_indices")))
        if not valid_targets:
            normalized_kwargs.pop("target_index", None)
            return True

        target_index = normalized_kwargs.get("target_index")
        if target_index is None:
            fallback_target = _to_int(self._find_card_target_index(context, card_index))
            if fallback_target in valid_targets:
                normalized_kwargs["target_index"] = fallback_target
                return True
            self.logger.warning(f"LLM 决策缺少卡牌目标: {decision}")
            return False

        if target_index not in valid_targets:
            self.logger.warning(f"LLM 决策卡牌目标组合非法: {decision}")
            return False
        return True

    async def _execute_action(self, prepared: JsonObject) -> JsonObject:
        client = self._require_client()
        action_type = str(prepared.get("action_type") or "").strip()
        if not action_type:
            return {"status": "error", "message": "缺少动作类型", "action": ""}

        kwargs = _mapping_or_empty(prepared.get("kwargs"))
        context = _mapping_or_empty(prepared.get("context"))
        snapshot = _mapping_or_empty(context.get("snapshot"))
        screen = snapshot.get("screen") or snapshot.get("normalized_screen") or "unknown"
        actions = [action for action in _list_or_empty(context.get("actions")) if isinstance(action, Mapping)]
        action_summaries = [self._describe_legal_action(dict(action), context) for action in actions]

        self.logger.info(
            f"[sts2_autoplay][action] screen={screen} action_type={action_type} kwargs={kwargs} available_actions={action_summaries}"
        )

        try:
            result = await client.execute_action(action_type, **kwargs)
        except (TimeoutError, OSError, RuntimeError) as exc:
            self.logger.warning(f"执行尖塔动作失败: action_type={action_type}, kwargs={kwargs}, error={exc}")
            return {"status": "error", "message": f"执行动作失败: {exc}", "action": action_type}

        now = time.time()
        self._last_action = action_type
        self._last_action_at = now
        self._history.appendleft({"type": "action", "time": now, "action": action_type, "result": result, "kwargs": dict(kwargs)})
        self._emit_status()
        return {"status": "ok", "message": f"已执行动作: {action_type}", "action": action_type, "result": result}

    def _normalize_action_kwargs(self, action_type: str, raw: JsonObject, context: JsonObject) -> JsonObject:
        kwargs = {
            key: value
            for key, value in raw.items()
            if key not in _RAW_ACTION_METADATA_KEYS and not (key == "action" and isinstance(value, Mapping))
        }

        if action_type in _REWARDISH_ACTION_TYPES:
            reward_options = self._card_reward_options(raw, context)
            if reward_options:
                self._log_card_reward_options(reward_options, context)

        allowed_option_indices = self._allowed_kwargs_impl(action_type, raw, context).get("option_index", [])
        preferred_option_index = self._preferred_option_index_for_action(action_type, raw, context)
        chosen_option_index = _first_allowed_or_preferred(preferred_option_index, allowed_option_indices)
        has_explicit_index = "option_index" in kwargs or "index" in kwargs or "card_index" in kwargs
        uses_potion_heuristic = action_type in {"discard_potion", "use_potion"}

        if chosen_option_index is not None and not has_explicit_index and not uses_potion_heuristic:
            kwargs["option_index"] = chosen_option_index
            return kwargs

        if not has_explicit_index and self._action_requires_index(action_type, raw):
            if action_type == "discard_potion":
                kwargs["option_index"] = self._find_discardable_potion_index(context)
            elif action_type == "use_potion":
                kwargs["option_index"] = self._find_usable_potion_index(context)
            elif action_type == "play_card":
                card_index = self._find_playable_card_index(context)
                kwargs["card_index"] = card_index
                target_index = self._find_card_target_index(context, card_index)
                if target_index is not None:
                    kwargs["target_index"] = target_index
            else:
                generic_indices = self._extract_generic_option_indices(raw)
                if generic_indices:
                    kwargs["index"] = generic_indices[0]
        return kwargs

    def _preferred_option_index_for_action(self, action_type: str, raw: JsonObject, context: JsonObject) -> Optional[int]:
        if action_type in _REWARD_CARD_ACTION_TYPES and bool(raw.get("shop_remove_selection")):
            return self._find_shop_remove_card_index_for_selection(context)
        if action_type in _REWARD_CARD_ACTION_TYPES:
            return self._find_preferred_card_option_index(raw, context)
        if action_type == "choose_map_node":
            return self._find_preferred_map_option_index(raw, context)
        if action_type == "claim_reward":
            return self._find_claimable_card_reward_index(context)
        if action_type == "buy_card":
            return self._find_preferred_shop_card_index(context)
        if action_type == "buy_relic":
            return self._find_preferred_shop_relic_index(context)
        if action_type == "buy_potion":
            return self._find_preferred_shop_potion_index(context)
        if action_type in _OPTION_INDEX_ACTION_TYPES:
            return self._find_preferred_character_option_index(raw, context)
        return None

    def _find_shop_remove_card_index_for_selection(self, context: dict[str, Any]) -> Optional[int]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        if self._normalized_screen_name(snapshot) != "card_selection":
            return None
        if not self._is_shop_remove_selection_context(context):
            return None
        actions = context.get("actions") if isinstance(context.get("actions"), list) else []
        has_select_deck_card = any(
            isinstance(action, Mapping) and _action_type_from(action) == "select_deck_card"
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
