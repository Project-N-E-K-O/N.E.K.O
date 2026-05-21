from __future__ import annotations

from typing import Any

from .heuristic_planner import STS2HeuristicPlanner


class STS2CompanionEvaluator:
    def __init__(self, i18n: Any) -> None:
        self._i18n = i18n
        self._planner = STS2HeuristicPlanner()

    def t(self, key: str, *, default: str = "", **params: Any) -> str:
        if self._i18n is not None:
            return self._i18n.t(key, default=default, **params)
        return default.format(**params) if params and default else (default or key)

    def evaluate(
        self,
        *,
        summary_context: dict[str, Any],
        situation_summary: dict[str, Any],
        strategy_context: dict[str, Any],
        runtime_state: Any | None = None,
    ) -> dict[str, Any]:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        summary_kind = str(situation_summary.get("kind") or summary_context.get("summary_kind") or "general")
        static_text = str(situation_summary.get("static_text") or situation_summary.get("text") or "")
        strategy_name = str(strategy_context.get("strategy_name") or "unknown")
        directives = strategy_context.get("strategy_directives") if isinstance(strategy_context.get("strategy_directives"), dict) else {}
        player_operation_observation = dict(getattr(runtime_state, "latest_player_operation_observation", {}) or {}) if runtime_state is not None else {}

        risk_level = self._risk_level(summary_kind=summary_kind, payload=payload)
        focus = self._focus(summary_kind=summary_kind, payload=payload)
        trigger, turn_key, scene_key, evaluation_key = self._trigger_state(
            summary_kind=summary_kind,
            payload=payload,
            runtime_state=runtime_state,
            player_operation_observation=player_operation_observation,
        )
        suggestion = self._suggestion(summary_kind=summary_kind, payload=payload, directives=directives, trigger=trigger, player_operation_observation=player_operation_observation)
        evaluation = self._evaluation(summary_kind=summary_kind, risk_level=risk_level, focus=focus, strategy_name=strategy_name, trigger=trigger, player_operation_observation=player_operation_observation)
        reminder = self._reminder(summary_kind=summary_kind, payload=payload, directives=directives, trigger=trigger)
        should_comment = self._should_comment(
            trigger=trigger,
            turn_key=turn_key,
            scene_key=scene_key,
            evaluation_key=evaluation_key,
            runtime_state=runtime_state,
            player_operation_observation=player_operation_observation,
        )

        monster_intel = self._monster_intel(summary_kind=summary_kind, payload=payload)
        pieces = [piece for piece in [monster_intel, suggestion] if piece]
        commentary = "；".join(pieces)
        if len(commentary) > 220:
            commentary = commentary[:217].rstrip("；， ") + "..."

        primary_message = commentary
        primary_message = self._with_card_cost(primary_message, payload=payload)
        final_should_comment = bool(primary_message) and should_comment

        if runtime_state is not None and hasattr(runtime_state, "last_companion_evaluation_key"):
            try:
                runtime_state.last_companion_evaluation_key = str(evaluation_key or runtime_state.last_companion_evaluation_key)
            except Exception:
                pass

        return {
            "strategy_name": strategy_name,
            "summary_kind": summary_kind,
            "risk_level": risk_level,
            "focus": focus,
            "evaluation": evaluation,
            "reminder": reminder,
            "suggestion": suggestion,
            "commentary": commentary,
            "primary_message": primary_message,
            "should_comment": final_should_comment,
            "trigger": trigger,
            "turn_key": turn_key,
            "scene_key": scene_key,
            "evaluation_key": evaluation_key,
            "source": "strategy_companion",
            "player_operation_observation": dict(player_operation_observation),
        }

    def _risk_level(self, *, summary_kind: str, payload: dict[str, Any]) -> str:
        if summary_kind == "combat":
            player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
            enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
            current_hp = self._safe_int(player.get("current_hp"))
            max_hp = max(1, self._safe_int(player.get("max_hp"), default=1))
            block = self._safe_int(player.get("block"))
            incoming = sum(self._safe_int(enemy.get("intent_damage")) for enemy in enemies if isinstance(enemy, dict))
            hp_ratio = current_hp / max_hp if max_hp > 0 else 0.0
            if incoming - block >= current_hp or hp_ratio <= 0.25:
                return "high"
            if incoming > block or hp_ratio <= 0.45:
                return "medium"
            return "low"
        if summary_kind in {"event", "reward", "selection", "shop", "rest", "map"}:
            return "medium"
        return "low"

    def _focus(self, *, summary_kind: str, payload: dict[str, Any]) -> str:
        if summary_kind == "combat":
            return self.t("companion.focus.combat", default="先看生存、格挡与怪物意图")
        if summary_kind == "reward":
            return self.t("companion.focus.reward", default="优先看是否贴合当前构筑方向")
        if summary_kind == "selection":
            return self.t("companion.focus.selection", default="优先看长期精简与构筑质量")
        if summary_kind == "map":
            return self.t("companion.focus.map", default="优先看路线风险与成长窗口")
        if summary_kind == "event":
            return self.t("companion.focus.event", default="优先看代价与长期收益")
        if summary_kind == "shop":
            return self.t("companion.focus.shop", default="优先看金币效率与删牌机会")
        if summary_kind == "rest":
            return self.t("companion.focus.rest", default="优先看保血还是升级更值")
        return self.t("companion.focus.general", default="优先看当前局势和下一步选择")

    def _evaluation(self, *, summary_kind: str, risk_level: str, focus: str, strategy_name: str, trigger: str, player_operation_observation: dict[str, Any]) -> str:
        if player_operation_observation:
            observation_summary = str(player_operation_observation.get("summary") or "").strip()
            if observation_summary:
                return self.t("companion.eval.player_operation", default="玩家刚完成操作：{summary}。", summary=observation_summary)
        prefix = {
            "high": self.t("companion.eval.high", default="当前局势偏危险"),
            "medium": self.t("companion.eval.medium", default="当前局势需要仔细取舍"),
            "low": self.t("companion.eval.low", default="当前局势相对平稳"),
        }.get(risk_level, self.t("companion.eval.default", default="当前局势需要观察"))
        template = self.t("companion.eval.template", default="{prefix}，按 {strategy_name} 策略应当 {focus}。", prefix=prefix, strategy_name=strategy_name, focus=focus)
        if trigger == "combat_turn":
            return self.t("companion.eval.combat_turn", default="回合更新：{template}", template=template)
        if trigger == "scene_entry":
            return self.t("companion.eval.scene_entry", default="新局面提示：{template}", template=template)
        if trigger == "post_action_eval":
            return self.t("companion.eval.post_action", default="这一步之后看，{template}", template=template)
        return template

    def _monster_intel(self, *, summary_kind: str, payload: dict[str, Any]) -> str:
        if summary_kind != "combat":
            return ""
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        parts: list[str] = []
        for enemy in enemies[:2]:
            if not isinstance(enemy, dict):
                continue
            name = str(enemy.get("name") or enemy.get("enemy_id") or "敌人")
            intent = str(enemy.get("intent") or enemy.get("move_id") or "")
            intent_damage = self._safe_int(enemy.get("intent_damage"))
            if intent_damage > 0:
                parts.append(f"{name}{intent_damage}伤")
            elif intent:
                parts.append(f"{name}{intent}")
            else:
                parts.append(name)
        return "；".join(parts)

    def _combat_tactical_advice(self, payload: dict[str, Any]) -> str:
        player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        block = self._safe_int(player.get("block"))
        incoming = sum(self._safe_int(enemy.get("intent_damage")) for enemy in enemies if isinstance(enemy, dict))
        enemy_intents = [str(enemy.get("intent") or enemy.get("move_id") or "") for enemy in enemies if isinstance(enemy, dict)]
        if any(intent in {"SHRINKER_MOVE", "CHARGE_UP_MOVE", "ENERGY_ORB_MOVE"} for intent in enemy_intents):
            return self.t("companion.suggestion.combat_intent_window", default="这回合可抓机会输出。")
        if incoming > block:
            return self.t("companion.suggestion.combat_defense", default="建议优先防御或找减伤线。")
        return self.t("companion.suggestion.combat_value", default="建议优先寻找高收益出牌线。")

    def _combat_reason_text(self, payload: dict[str, Any], selected_card: dict[str, Any], target_name: str) -> str:
        player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        current_hp = self._safe_int(player.get("current_hp"))
        block = self._safe_int(player.get("block"))
        incoming = sum(self._safe_int(enemy.get("intent_damage")) for enemy in enemies if isinstance(enemy, dict))
        remaining_block_needed = max(0, incoming - block)
        damage = self._card_damage(selected_card)
        card_block = self._card_block(selected_card)
        if target_name and damage > 0:
            target = next(
                (enemy for enemy in enemies if isinstance(enemy, dict) and str(enemy.get("name") or enemy.get("enemy_id") or "").strip() == target_name),
                None,
            )
            target_hp = self._safe_int(target.get("current_hp") if isinstance(target, dict) else None)
            if target_hp is not None and target_hp > 0 and damage >= target_hp:
                return self.t(
                    "companion.suggestion.combat_reason.lethal",
                    default="这张牌预计能打出 {damage} 点伤害，足以先收掉【{target_name}】。",
                    damage=damage,
                    target_name=target_name,
                )
        if card_block > 0 and incoming > block:
            return self.t(
                "companion.suggestion.combat_reason.block_gap",
                default="敌方这回合大约会打出 {incoming} 点伤害，目前还差 {gap} 点防御，这张牌更稳。",
                incoming=incoming,
                gap=remaining_block_needed,
            )
        if damage > 0 and target_name:
            return self.t(
                "companion.suggestion.combat_reason.target_pressure",
                default="这张牌能先把伤害压到【{target_name}】身上，推进当前交换。",
                target_name=target_name,
            )
        if damage > 0:
            return self.t(
                "companion.suggestion.combat_reason.damage_value",
                default="这张牌当前能提供更直接的输出收益。",
            )
        if card_block > 0:
            return self.t(
                "companion.suggestion.combat_reason.block_value",
                default="这张牌能先把防御垫起来，让这一回合更安全。",
            )
        return self.t(
            "companion.suggestion.combat_reason.generic",
            default="它更符合当前血量、能量、手牌和敌人意图。",
        )

    def _suggestion(self, *, summary_kind: str, payload: dict[str, Any], directives: dict[str, Any], trigger: str, player_operation_observation: dict[str, Any]) -> str:
        if player_operation_observation:
            event_type = str(player_operation_observation.get("event_type") or "")
            if event_type == "combat_ended":
                return self.t("companion.suggestion.player_operation.combat_ended", default="建议先看奖励、遗物或后续路线，别急着无脑继续。")
            if event_type == "choice_committed":
                return self.t("companion.suggestion.player_operation.choice_committed", default="建议顺着这次选择继续观察局势，确认是否贴合当前构筑方向。")
            if event_type == "combat_turn_advanced":
                return self.t("companion.suggestion.player_operation.turn_advanced", default="新回合已经开始，建议先看敌方意图、能量和可打出的牌。")
            if event_type == "player_card_or_action_committed":
                return self.t("companion.suggestion.player_operation.action_committed", default="这步已经落下，建议接着看伤害交换、护盾变化和下一张牌的收益。")
        if summary_kind == "combat":
            return self._combat_voice(payload)
        if summary_kind == "reward":
            reward_cards = payload.get("reward_cards") if isinstance(payload.get("reward_cards"), list) else []
            reward_card_names = payload.get("reward_card_names") if isinstance(payload.get("reward_card_names"), list) else []
            preferred = directives.get("prefer") if isinstance(directives.get("prefer"), list) else []
            preferred_index = self._preferred_reward_index(payload, directives)
            if preferred:
                base = self.t("companion.suggestion.reward_preferred", default="建议优先贴合策略偏好的奖励：{preferred}。", preferred=preferred[0])
            elif preferred_index is not None and 0 <= preferred_index < len(reward_cards):
                reward_card = reward_cards[preferred_index] if isinstance(reward_cards[preferred_index], dict) else {}
                reward_name = str(reward_card.get("name") or reward_card.get("card_id") or "")
                if reward_name:
                    base = self.t("companion.suggestion.reward_named", default="当前奖励里可以重点看：{card_name}。", card_name=reward_name)
                elif preferred_index < len(reward_card_names):
                    base = self.t("companion.suggestion.reward_named", default="当前奖励里可以重点看：{card_name}。", card_name=reward_card_names[preferred_index])
                else:
                    base = self.t("companion.suggestion.reward_default", default="建议优先选择能补当前构筑短板的奖励。")
            else:
                base = self.t("companion.suggestion.reward_default", default="建议优先选择能补当前构筑短板的奖励。")
            return self._post_action_wrap(base, trigger)
        if summary_kind == "selection":
            selection_cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
            selection_card_names = payload.get("selection_card_names") if isinstance(payload.get("selection_card_names"), list) else []
            preferred_index = self._preferred_selection_index(payload, directives)
            if preferred_index is not None and 0 <= preferred_index < len(selection_cards):
                selection_card = selection_cards[preferred_index] if isinstance(selection_cards[preferred_index], dict) else {}
                card_name = str(selection_card.get("name") or selection_card.get("card_id") or "")
                if card_name:
                    base = self.t("companion.suggestion.selection_named", default="当前可处理的卡里，可以优先看：{card_name}。", card_name=card_name)
                elif preferred_index < len(selection_card_names):
                    base = self.t("companion.suggestion.selection_named", default="当前可处理的卡里，可以优先看：{card_name}。", card_name=selection_card_names[preferred_index])
                else:
                    base = self.t("companion.suggestion.selection", default="建议优先精简低价值卡，保持牌组质量。")
            else:
                base = self.t("companion.suggestion.selection", default="建议优先精简低价值卡，保持牌组质量。")
            return self._post_action_wrap(base, trigger)
        if summary_kind == "map":
            travelable_nodes = payload.get("travelable_nodes") if isinstance(payload.get("travelable_nodes"), list) else []
            preferred_index = self._preferred_map_index(payload, directives)
            if preferred_index is not None:
                preferred_node = next(
                    (
                        node for node in travelable_nodes
                        if isinstance(node, dict) and self._safe_int(node.get("index"), default=-1) == preferred_index
                    ),
                    None,
                )
                node_type = str(preferred_node.get("type") or preferred_node.get("node_type") or "") if isinstance(preferred_node, dict) else ""
                if node_type:
                    base = self.t("companion.suggestion.map_named", default="当前可走路线里，可以优先考虑：{node_type}。", node_type=node_type)
                else:
                    base = self.t("companion.suggestion.map", default="建议优先选择更稳的成长路线，不要无意义贪战。")
            else:
                base = self.t("companion.suggestion.map", default="建议优先选择更稳的成长路线，不要无意义贪战。")
            return self._post_action_wrap(base, trigger)
        if summary_kind == "event":
            event_name = str(payload.get("event_name") or payload.get("event_id") or "")
            if event_name:
                base = self.t("companion.suggestion.event_named", default="当前事件 {event_name}，建议优先看代价可控且长期收益更高的选项。", event_name=event_name)
            else:
                base = self.t("companion.suggestion.event", default="建议优先选择代价可控、长期收益更高的选项。")
            return self._post_action_wrap(base, trigger)
        if summary_kind == "shop":
            shop_cards = payload.get("shop_cards") if isinstance(payload.get("shop_cards"), list) else []
            shop_relics = payload.get("shop_relics") if isinstance(payload.get("shop_relics"), list) else []
            shop_card_names = payload.get("shop_card_names") if isinstance(payload.get("shop_card_names"), list) else []
            shop_relic_names = payload.get("shop_relic_names") if isinstance(payload.get("shop_relic_names"), list) else []
            preferred_card_index = self._preferred_shop_card_index(payload, directives)
            preferred_relic_index = self._preferred_shop_relic_index(payload, directives)
            if preferred_card_index is not None and 0 <= preferred_card_index < len(shop_cards):
                shop_card = shop_cards[preferred_card_index] if isinstance(shop_cards[preferred_card_index], dict) else {}
                item_name = str(shop_card.get("name") or shop_card.get("card_id") or "")
                if item_name:
                    base = self.t("companion.suggestion.shop_card_named", default="商店里可以先看看：{item_name}。", item_name=item_name)
                elif preferred_card_index < len(shop_card_names):
                    base = self.t("companion.suggestion.shop_card_named", default="商店里可以先看看：{item_name}。", item_name=shop_card_names[preferred_card_index])
                else:
                    base = self.t("companion.suggestion.shop", default="建议优先考虑高价值购买或删牌，而不是随手消费。")
            elif preferred_relic_index is not None and 0 <= preferred_relic_index < len(shop_relics):
                shop_relic = shop_relics[preferred_relic_index] if isinstance(shop_relics[preferred_relic_index], dict) else {}
                item_name = str(shop_relic.get("name") or shop_relic.get("relic_id") or "")
                if item_name:
                    base = self.t("companion.suggestion.shop_relic_named", default="商店里可以先看看遗物：{item_name}。", item_name=item_name)
                elif preferred_relic_index < len(shop_relic_names):
                    base = self.t("companion.suggestion.shop_relic_named", default="商店里可以先看看遗物：{item_name}。", item_name=shop_relic_names[preferred_relic_index])
                else:
                    base = self.t("companion.suggestion.shop", default="建议优先考虑高价值购买或删牌，而不是随手消费。")
            else:
                base = self.t("companion.suggestion.shop", default="建议优先考虑高价值购买或删牌，而不是随手消费。")
            return self._post_action_wrap(base, trigger)
        if summary_kind == "rest":
            return self._post_action_wrap(self.t("companion.suggestion.rest", default="建议结合当前血量决定是休息还是升级。"), trigger)
        return self._post_action_wrap(self.t("companion.suggestion.general", default="建议先看当前局势，再决定是否执行动作。"), trigger)

    def _combat_voice(self, payload: dict[str, Any]) -> str:
        player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        current_hp = self._safe_int(player.get("current_hp"))
        max_hp = max(1, self._safe_int(player.get("max_hp"), default=1))
        block = self._safe_int(player.get("block"))
        incoming = sum(self._safe_int(enemy.get("intent_damage")) for enemy in enemies if isinstance(enemy, dict))
        hp_ratio = current_hp / max_hp if max_hp > 0 else 0.0
        if incoming > block or hp_ratio <= 0.45:
            return self._combat_tactical_advice(payload)
        enemy_intel = self._monster_intel(summary_kind="combat", payload=payload)
        if enemy_intel:
            return self.t("companion.commentary.combat_pressure", default="这波怪的意图一点都不客气：{enemy_intel}。", enemy_intel=enemy_intel)
        if hp_ratio <= 0.65:
            return self.t("companion.commentary.combat_low_hp", default="这血线看着就不太适合贪刀，先稳一稳。")
        return self.t("companion.commentary.combat_general", default="这手牌还能打，但别太上头。")

    def _combat_heuristic_suggestion(self, payload: dict[str, Any], directives: dict[str, Any]) -> str:
        summary_context = {
            "payload": {
                "decision_payload": {"guidance": {"pending": []}, "instructions": []},
            }
        }
        snapshot = {
            "raw_state": {
                "combat": {
                    "player": dict(payload.get("player") if isinstance(payload.get("player"), dict) else {}),
                    "hand": self._combat_hand_from_payload(payload),
                    "enemies": list(payload.get("enemies") if isinstance(payload.get("enemies"), list) else []),
                }
            }
        }
        preferences = self._combat_preferences(directives)
        guidance_override = self._planner._guidance_override_from_payload(summary_context)
        preferred_card_index = self._planner._preferred_combat_card_index(snapshot, preferences, guidance_override)
        hand = snapshot["raw_state"]["combat"]["hand"]
        if preferred_card_index is None:
            return ""
        selected_card = next(
            (
                card for card in hand
                if isinstance(card, dict) and self._safe_int(card.get("index")) == preferred_card_index
            ),
            None,
        )
        if not isinstance(selected_card, dict):
            return ""
        card_name = str(selected_card.get("name") or selected_card.get("card_id") or "").strip()
        if not card_name:
            return ""
        enemies = snapshot["raw_state"]["combat"]["enemies"]
        target_index = self._planner._preferred_target_for_card(selected_card, enemies)
        target_name = ""
        if target_index is not None and 0 <= target_index < len(enemies):
            enemy = enemies[target_index]
            if isinstance(enemy, dict):
                target_name = str(enemy.get("name") or enemy.get("enemy_id") or "").strip()
        reason = self._combat_reason_text(payload, selected_card, target_name)
        if target_name:
            return self.t(
                "companion.suggestion.combat_named_with_target",
                default="建议这回合先出 {card_name}，目标是【{target_name}】。理由：{reason}",
                card_name=card_name,
                target_name=target_name,
                reason=reason,
            )
        return self.t(
            "companion.suggestion.combat_named_without_target",
            default="建议这回合先出 {card_name}。理由：{reason}",
            card_name=card_name,
            reason=reason,
        )

    def _combat_hand_from_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        cards = payload.get("playable_card_summaries") if isinstance(payload.get("playable_card_summaries"), list) else []
        hand: list[dict[str, Any]] = []
        for index, card in enumerate(cards):
            if not isinstance(card, dict):
                continue
            hand.append(
                {
                    "index": index,
                    "name": card.get("name"),
                    "card_id": card.get("name"),
                    "energy_cost": card.get("energy_cost"),
                    "star_cost": card.get("star_cost"),
                    "costs_x": card.get("costs_x"),
                    "star_costs_x": card.get("star_costs_x"),
                    "playable": bool(card.get("playable", True)),
                    "effect": card.get("effect"),
                    "description": card.get("effect"),
                    "requires_target": card.get("requires_target"),
                    "valid_target_indices": list(range(len(payload.get("enemies") if isinstance(payload.get("enemies"), list) else []))) if card.get("requires_target") else [],
                }
            )
        return hand

    def _combat_preferences(self, directives: dict[str, Any]) -> dict[str, Any]:
        preferred = directives.get("preferred_option_index")
        if preferred is None:
            return {"records": []}
        return {"records": [{"value": {"preferred_option_index": preferred}}]}

    def _shop_card_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "shop_cards": list(payload.get("shop_cards") if isinstance(payload.get("shop_cards"), list) else []),
            "gold": payload.get("gold"),
        }

    def _shop_relic_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "shop_relics": list(payload.get("shop_relics") if isinstance(payload.get("shop_relics"), list) else []),
            "gold": payload.get("gold"),
        }

    def _preferred_reward_index(self, payload: dict[str, Any], directives: dict[str, Any]) -> int | None:
        summary_context = {
            "payload": {
                "reward_cards": list(payload.get("reward_cards") if isinstance(payload.get("reward_cards"), list) else []),
                "selection_cards": list(payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []),
                "deck": dict(payload.get("deck") if isinstance(payload.get("deck"), dict) else {}),
                "decision_payload": {
                    "guidance": {"pending": []},
                    "instructions": [],
                },
            }
        }
        if directives.get("preferred_option_index") is not None:
            preferred = int(directives["preferred_option_index"])
            return preferred
        return self._planner._preferred_reward_option(summary_context, {"records": []})

    def _preferred_selection_index(self, payload: dict[str, Any], directives: dict[str, Any]) -> int | None:
        preferred = directives.get("preferred_option_index")
        if preferred is not None:
            return self._safe_int(preferred)
        summary_context = {
            "payload": {
                "selection_cards": list(payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []),
            }
        }
        return self._planner._preferred_remove_option(summary_context, {"records": []})

    def _preferred_map_index(self, payload: dict[str, Any], directives: dict[str, Any]) -> int | None:
        summary_context = {
            "payload": {
                "travelable_nodes": list(payload.get("travelable_nodes") if isinstance(payload.get("travelable_nodes"), list) else []),
                "current_hp": payload.get("current_hp"),
                "max_hp": payload.get("max_hp"),
                "gold": payload.get("gold"),
            }
        }
        preferences = {"records": []}
        if directives.get("preferred_option_index") is not None:
            preferences = {"records": [{"value": {"preferred_option_index": directives["preferred_option_index"]}}]}
        return self._planner._preferred_map_index(summary_context, preferences)

    def _preferred_shop_card_index(self, payload: dict[str, Any], directives: dict[str, Any]) -> int | None:
        summary_context = {
            "payload": {
                "reward_cards": list(payload.get("shop_cards") if isinstance(payload.get("shop_cards"), list) else []),
                "deck": dict(payload.get("deck") if isinstance(payload.get("deck"), dict) else {}),
                "decision_payload": {
                    "guidance": {"pending": []},
                    "instructions": [],
                },
            }
        }
        if directives.get("preferred_option_index") is not None:
            return self._safe_int(directives.get("preferred_option_index"))
        return self._planner._preferred_reward_option(summary_context, {"records": []})

    def _preferred_shop_relic_index(self, payload: dict[str, Any], directives: dict[str, Any]) -> int | None:
        preferred = directives.get("preferred_option_index")
        if preferred is not None:
            return self._safe_int(preferred)
        relics = payload.get("shop_relics") if isinstance(payload.get("shop_relics"), list) else []
        if len(relics) == 1:
            return 0
        return None

    def _reminder(self, *, summary_kind: str, payload: dict[str, Any], directives: dict[str, Any], trigger: str) -> str:
        must = directives.get("must") if isinstance(directives.get("must"), list) else []
        if summary_kind == "combat":
            player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
            current_hp = self._safe_int(player.get("current_hp"))
            max_hp = max(1, self._safe_int(player.get("max_hp"), default=1))
            if max_hp > 0 and current_hp / max_hp <= 0.3:
                return self.t("companion.reminder.low_hp", default="提醒：当前血量偏低，别为了贪输出吃不必要伤害。")
        if must:
            return self.t("companion.reminder.must", default="提醒：当前策略明确强调 {must}。", must=must[0])
        return ""

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except Exception:
            return default

    def _trigger_state(self, *, summary_kind: str, payload: dict[str, Any], runtime_state: Any | None, player_operation_observation: dict[str, Any]) -> tuple[str, str, str, str]:
        screen = str(payload.get("screen") or summary_kind or "unknown")
        floor = self._safe_int(payload.get("floor") or 0)
        act = self._safe_int(payload.get("act") or 0)
        turn_value = payload.get("turn") if "turn" in payload else payload.get("turn_count")
        turn_key = f"{act}:{floor}:{screen}:{turn_value}" if summary_kind == "combat" and turn_value is not None else ""
        scene_key = f"{act}:{floor}:{screen}"

        if player_operation_observation:
            fingerprint = str(player_operation_observation.get("fingerprint") or scene_key)
            return "player_operation", turn_key, scene_key, fingerprint
        if summary_kind == "combat" and turn_key:
            return "combat_turn", turn_key, scene_key, turn_key
        if summary_kind in {"event", "map", "shop", "reward", "selection", "rest"}:
            return "scene_entry", turn_key, scene_key, scene_key
        return "general", turn_key, scene_key, ""

    def _should_comment(self, *, trigger: str, turn_key: str, scene_key: str, evaluation_key: str, runtime_state: Any | None, player_operation_observation: dict[str, Any]) -> bool:
        if runtime_state is None:
            return True
        if trigger == "combat_turn" and turn_key:
            return turn_key != getattr(runtime_state, "last_companion_turn_key", "")
        if trigger == "scene_entry" and scene_key:
            return scene_key != getattr(runtime_state, "last_companion_scene_key", "")
        if trigger == "player_operation" and player_operation_observation:
            return False
        return True

    def _with_card_cost(self, text: str, *, payload: dict[str, Any]) -> str:
        base = str(text or "").strip()
        if not base:
            return base
        cards = payload.get("playable_card_summaries") if isinstance(payload.get("playable_card_summaries"), list) else []
        if not cards:
            return base
        first = cards[0] if isinstance(cards[0], dict) else {}
        name = str(first.get("name") or "").strip()
        if not name:
            return base
        if bool(first.get("costs_x")):
            cost_text = "X费"
        else:
            cost_text = f"{self._safe_int(first.get('energy_cost'), default=0)}费"
        star_cost = self._safe_int(first.get("star_cost"), default=0)
        if bool(first.get("star_costs_x")):
            cost_text += "，耗星X"
        elif star_cost and star_cost > 0:
            cost_text += f"，耗星{star_cost}"
        return f"{base} 可考虑 {name}（{cost_text}）。"

    def _post_action_wrap(self, text: str, trigger: str) -> str:
        if not text:
            return text
        if trigger == "post_action_eval":
            return self.t("companion.suggestion.post_action", default="从这一步结果看，{text}", text=text)
        return text


__all__ = ["STS2CompanionEvaluator"]
