from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Any, Awaitable, Dict, Optional


class DecisioningMixin:
    def _first_present(self, *values: Any, default: Any = None) -> Any:
        for value in values:
            if value is not None:
                return value
        return default

    def _safe_strategy_constraints(self, strategy: Optional[str] = None) -> dict[str, Any]:
        active_strategy = strategy or self._configured_character_strategy()
        try:
            constraints = self._load_strategy_constraints(active_strategy)
        except RuntimeError as exc:
            self.logger.warning(f"加载策略约束失败，使用空约束: strategy={active_strategy}, error={exc}")
            return {}
        return constraints if isinstance(constraints, dict) else {}

    def _is_desperate_situation(self, context: dict[str, Any]) -> bool:
        if not bool(self._cfg.get("neko_desperate_enabled", True)):
            return False
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        current_hp = self._safe_int(self._first_present(player.get("hp"), raw_state.get("current_hp"), run.get("current_hp"), run.get("hp")))
        max_hp = self._safe_int(self._first_present(player.get("max_hp"), raw_state.get("max_hp"), run.get("max_hp"), default=1))
        if max_hp <= 0:
            max_hp = 1
        hp_ratio = current_hp / max_hp
        desperate_hp_threshold = max(0.0, min(1.0, float(self._cfg.get("neko_desperate_hp_threshold", 0.2))))
        if hp_ratio > desperate_hp_threshold:
            return False
        incoming_attack = sum(
            self._enemy_intent_attack_total(enemy)
            for enemy in (combat.get("enemies") if isinstance(combat.get("enemies"), list) else [])
            if isinstance(enemy, dict)
        )
        player_block = self._combat_player_block(combat)
        remaining = max(0, incoming_attack - player_block)
        if remaining > 0 and current_hp <= remaining:
            return True
        return hp_ratio <= desperate_hp_threshold

    def _select_desperate_action(self, actions: list[dict[str, Any]], context: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not self._is_desperate_situation(context):
            return None
        combat = self._combat_state(context)
        if not combat:
            return None
        play_card_actions = [a for a in actions if isinstance(a, dict) and str(a.get("type") or "") == "play_card"]
        if not play_card_actions:
            return None
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        playable_cards = [c for c in hand if isinstance(c, dict) and bool(c.get("playable"))]
        character_strategy = self._configured_character_strategy()
        strategy_constraints = self._safe_strategy_constraints(character_strategy)
        tactical_summary = self._combat_analyzer.build_tactical_summary(combat, lambda _strategy: strategy_constraints, character_strategy)
        if not bool(tactical_summary.get("should_prioritize_lethal")):
            defensive_action = self._find_defensive_action(actions, combat, tactical_summary)
            if defensive_action is not None:
                self.logger.info("[sts2_autoplay][desperate] selected defensive action before non-lethal damage")
                return defensive_action
            block_card = self._best_playable_block_card(combat)
            if block_card is not None:
                action = self._action_for_card(play_card_actions, block_card)
                if action is not None:
                    self.logger.info(f"[sts2_autoplay][desperate] selected block card={block_card.get('name')} before non-lethal damage")
                    return action
        attack_cards = []
        for card in playable_cards:
            card_type = str(card.get("card_type") or card.get("type") or "").strip().lower()
            if card_type in {"attack", "skill"}:
                damage = self._combat_analyzer._card_total_damage_value(card, combat, strategy_constraints=strategy_constraints)
                if card_type == "attack" or (card_type == "skill" and damage > 0):
                    attack_cards.append((card, damage))
        attack_cards.sort(key=lambda x: x[1], reverse=True)
        target_index = self._safe_int(tactical_summary.get("recommended_target_index"), None)
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        prioritize_lethal = bool(tactical_summary.get("should_prioritize_lethal"))
        for card, _ in attack_cards:
            valid_targets = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
            normalized_valid_targets: list[int] = []
            for target in valid_targets:
                try:
                    normalized_target = int(target)
                except Exception:
                    continue
                if normalized_target not in normalized_valid_targets:
                    normalized_valid_targets.append(normalized_target)
            resolved_target = None
            if normalized_valid_targets:
                if target_index is not None and target_index in normalized_valid_targets:
                    resolved_target = target_index
                else:
                    resolved_target = normalized_valid_targets[0]
            damage = self._combat_analyzer._card_total_damage_value(card, combat, target_index=resolved_target, strategy_constraints=strategy_constraints)
            if prioritize_lethal:
                target_enemy = next(
                    (enemy for enemy in enemies if isinstance(enemy, dict) and resolved_target is not None and self._safe_int(enemy.get("index"), None) == resolved_target),
                    None,
                )
                if target_enemy is None and len(enemies) == 1 and isinstance(enemies[0], dict):
                    target_enemy = enemies[0]
                if target_enemy is None:
                    continue
                effective_hp = self._enemy_hp_value(target_enemy) + self._enemy_block_value(target_enemy)
                if damage < effective_hp:
                    continue
            action = self._action_for_card(play_card_actions, card, target_index=resolved_target)
            if action is not None:
                self.logger.info(f"[sts2_autoplay][desperate] selected attack card={card.get('name')} damage={damage} target={resolved_target}")
                return action
        defensive_action = self._find_defensive_action(actions, combat, tactical_summary)
        if defensive_action is not None:
            return defensive_action
        block_card = self._best_playable_block_card(combat)
        if block_card is not None:
            action = self._action_for_card(play_card_actions, block_card)
            if action is not None:
                self.logger.info(f"[sts2_autoplay][desperate] selected block card={block_card.get('name')} after no lethal target")
                return action
        return None

    def _detect_card_synergy_type(self, card: dict[str, Any], combat: dict[str, Any]) -> str:
        card_type = str(card.get("card_type") or card.get("type") or "").strip().lower()
        name = str(card.get("name") or card.get("card_name") or "").lower()
        desc = str(card.get("description") or card.get("desc") or card.get("card_description") or "").lower()
        label = str(card.get("label") or "").lower()
        text_blob = f"{name} {desc} {label}"
        card_id = str(card.get("id") or card.get("card_id") or "").lower()

        if any(k in text_blob for k in {"weaken", "虚弱", "weak"}):
            return "weaken"
        if any(k in text_blob for k in {"vulnerable", "易伤", "vuln"}):
            return "vulnerable"
        if any(k in text_blob for k in {"inflame", "strength_up", "strength", "力量"}):
            return "strength_boost"
        if any(k in text_blob for k in {"metallicize", "金属化", "护甲每回合"}):
            return "block_boost"
        if any(k in text_blob for k in {"draw", "抽卡", "skim", "coolheaded", "手牌", "卡片"}):
            return "draw"
        if any(k in text_blob for k in {"channel", "zap", "lightning", "frost", "dark", "冰球", "闪电球", "dark orb", "orb", "充能球"}):
            return "orb_channel"
        if any(k in text_blob for k in {"dualcast", "多重施法", "evoke", "激发"}):
            return "orb_evoke"
        if card_type == "attack" or "strike" in card_id or "strike" in name:
            return "attack"
        if any(k in text_blob for k in {"block", "防御", "护甲"}):
            return "block"
        if card_type == "skill":
            return "utility"
        if card_type == "power":
            return "power"
        if any(k in text_blob for k in {"end_turn", "结束回合"}):
            return "end_turn"
        return card_type if card_type else "attack"

    def _calc_synergy_boost(self, card: dict[str, Any], active_state: dict[str, Any], combat: dict[str, Any], strategy_constraints) -> float:
        synergy_type = self._detect_card_synergy_type(card, combat)
        boost = 0.0
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        enemy_vulnerable = False
        enemy_weak = False
        player_str = active_state.get("str_stacks", 0)
        for enemy in enemies:
            if not isinstance(enemy, dict):
                continue
            buffs = enemy.get("buffs") if isinstance(enemy.get("buffs"), list) else []
            debuffs = enemy.get("debuffs") if isinstance(enemy.get("debuffs"), list) else []
            for b in buffs + debuffs:
                if not isinstance(b, dict):
                    continue
                effect_id = str(b.get("id") or b.get("name") or "").lower()
                if effect_id in {"vulnerable", "易伤"}:
                    enemy_vulnerable = True
                if effect_id in {"weak", "虚弱", "弱化"}:
                    enemy_weak = True
        simulated_vulnerable = self._safe_int(active_state.get("vulnerable_stacks"), 0) > 0
        simulated_weak = self._safe_int(active_state.get("weaken_stacks"), 0) > 0
        if synergy_type == "attack" and (enemy_vulnerable or simulated_vulnerable):
            boost += 0.50
        elif synergy_type == "weaken" and (enemy_weak or simulated_weak):
            boost += 0.25
        elif synergy_type == "vulnerable" and (enemy_vulnerable or simulated_vulnerable):
            boost += 0.50
        elif synergy_type == "strength_boost":
            boost += player_str * 0.1
        return boost

    def _calc_weaken_defense_synergy(self, active_state: dict[str, Any], combat: dict[str, Any]) -> float:
        incoming = sum(
            self._enemy_intent_attack_total(enemy)
            for enemy in (combat.get("enemies") if isinstance(combat.get("enemies"), list) else [])
            if isinstance(enemy, dict)
        )
        current_block = self._safe_int(active_state.get("block"), 0)
        remaining_needed = max(0, incoming - current_block)
        if remaining_needed <= 0:
            return 0.0
        prevented_damage = max(1, int(incoming * 0.25))
        return float(min(remaining_needed, prevented_damage))

    def _calc_setup_synergy(self, setup_card: dict[str, Any], remaining_cards: list[dict[str, Any]], combat: dict[str, Any], active_state: dict[str, Any], strategy_constraints) -> float:
        synergy_type = self._detect_card_synergy_type(setup_card, combat)
        total_synergy = 0.0
        if synergy_type in {"weaken", "vulnerable", "strength_boost", "block_boost"}:
            sim_state = dict(active_state)
            self._apply_setup_effect(setup_card, sim_state, combat)
            block_bonus = self._safe_int(sim_state.get("block_boost"), 0)
            if synergy_type == "weaken":
                total_synergy += self._calc_weaken_defense_synergy(active_state, combat) * 2.0
            for rem_card in remaining_cards:
                rem_type = self._detect_card_synergy_type(rem_card, combat)
                if rem_type == "attack" or (rem_type == "orb_evoke" and "lightning" in str(rem_card.get("name") or "").lower()):
                    boost = self._calc_synergy_boost(rem_card, sim_state, combat, strategy_constraints)
                    dmg = self._combat_analyzer._card_total_damage_value(rem_card, combat, strategy_constraints=strategy_constraints)
                    total_synergy += dmg * boost
                elif rem_type == "block" and block_bonus > 0:
                    total_synergy += min(block_bonus, self._combat_analyzer._card_block_value(rem_card))
        elif synergy_type in {"draw", "orb_channel"}:
            extra_energy = 1
            extra_damage = extra_energy * 10
            total_synergy += extra_damage * 0.5
        return total_synergy

    def _apply_setup_effect(self, card: dict[str, Any], state: dict[str, Any], combat: dict[str, Any]) -> None:
        texts = set(str(card.get(k) or "").lower() for k in ("name", "label", "description") if card.get(k))
        text_blob = " ".join(texts)
        if any(k in text_blob for k in {"weaken", "虚弱", "弱化"}):
            state["weaken_stacks"] = state.get("weaken_stacks", 0) + 1
        if any(k in text_blob for k in {"vulnerable", "易伤"}):
            state["vulnerable_stacks"] = state.get("vulnerable_stacks", 0) + 1
        if any(k in text_blob for k in {"inflame", "力量", "strength"}):
            state["str_stacks"] = state.get("str_stacks", 0) + 2
        if any(k in text_blob for k in {"metallicize", "金属化", "护甲每回合"}):
            state["block_boost"] = state.get("block_boost", 0) + 3

    def _calc_marginal_benefit(self, card: dict[str, Any], state: dict[str, Any], combat: dict[str, Any], tactical: dict[str, Any], strategy_constraints, remaining_cards: Optional[list[dict[str, Any]]] = None) -> float:
        synergy_type = self._detect_card_synergy_type(card, combat)
        energy_cost = self._safe_int(card.get("cost"), 0)
        total_energy = self._safe_int(combat.get("player_energy"), 3)
        if energy_cost > state.get("energy", total_energy):
            return -999999.0
        target_index = tactical.get("recommended_target_index")
        incoming = tactical.get("incoming_attack_total", 0)
        current_block = state.get("block", 0)
        remaining_needed = max(0, incoming - current_block)
        block = self._combat_analyzer._card_block_value(card)
        damage = self._combat_analyzer._card_total_damage_value(card, combat, target_index=target_index, strategy_constraints=strategy_constraints)
        orbs = self._combat_orbs(combat)
        orb_damage = self._combat_analyzer._card_orb_damage_value(card, combat=combat, target_index=target_index) if orbs else 0
        total_damage = damage + orb_damage
        synergy_boost = self._calc_synergy_boost(card, state, combat, strategy_constraints)
        benefit = 0.0
        if synergy_type in {"weaken", "vulnerable", "strength_boost", "block_boost"}:
            benefit = self._calc_setup_synergy(card, remaining_cards or [], combat, state, strategy_constraints) * 0.5
        elif synergy_type == "attack" and total_damage > 0:
            benefit = total_damage * (1.0 + synergy_boost) * 2.0
        elif synergy_type == "block" and block > 0:
            if remaining_needed > 0:
                if block >= remaining_needed:
                    benefit = block * 3.0 + 400.0
                else:
                    benefit = block * 2.0
        elif synergy_type == "orb_channel":
            benefit = 5.0
        elif synergy_type == "orb_evoke":
            benefit = total_damage * 1.5 if total_damage > 0 else 3.0
        elif synergy_type == "draw":
            benefit = 15.0
        benefit -= energy_cost * 15.0
        if synergy_type == "end_turn":
            benefit = 1.0
        return benefit

    def _select_maximize_benefit_action(self, actions: list[dict[str, Any]], context: dict[str, Any]) -> Optional[dict[str, Any]]:
        combat = self._combat_state(context)
        if not combat:
            return None
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        playable_cards = [c for c in hand if isinstance(c, dict) and bool(c.get("playable"))]
        if not playable_cards:
            return None
        play_card_actions = [a for a in actions if isinstance(a, dict) and str(a.get("type") or "") == "play_card"]
        if not play_card_actions:
            return None
        character_strategy = self._configured_character_strategy()
        strategy_constraints = self._safe_strategy_constraints(character_strategy)
        tactical = self._combat_analyzer.build_tactical_summary(combat, lambda s: strategy_constraints, character_strategy)
        remaining_energy = self._safe_int(combat.get("player_energy"), 3)
        active_state: dict[str, Any] = {
            "energy": remaining_energy,
            "str_stacks": 0,
            "weaken_stacks": 0,
            "vulnerable_stacks": 0,
            "block": self._combat_analyzer._combat_player_block(combat),
            "block_boost": 0,
        }
        remaining = list(playable_cards)
        best_sequence: list[tuple[dict[str, Any], Optional[int]]] = []
        sim_energy = remaining_energy
        while remaining:
            best_card = None
            best_benefit = -999999.0
            best_target = None
            best_idx = -1
            for idx, card in enumerate(remaining):
                followup_cards = [candidate for candidate_index, candidate in enumerate(remaining) if candidate_index != idx]
                benefit = self._calc_marginal_benefit(card, active_state, combat, tactical, strategy_constraints, remaining_cards=followup_cards)
                if benefit > best_benefit:
                    best_benefit = benefit
                    best_card = card
                    best_idx = idx
                    synergy_type = self._detect_card_synergy_type(card, combat)
                    target = self._safe_int(tactical.get("recommended_target_index"))
                    valid_targets = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
                    if valid_targets:
                        if target is not None and target in [self._safe_int(t) for t in valid_targets]:
                            best_target = target
                        else:
                            best_target = self._safe_int(valid_targets[0])
                    else:
                        best_target = None
            if best_card is None or best_benefit < -999990.0 or (best_sequence and best_benefit <= 0):
                break
            action = self._action_for_card(play_card_actions, best_card, target_index=best_target)
            if action is None:
                remaining.pop(best_idx)
                continue
            best_sequence.append((best_card, best_target))
            remaining_energy_cost = self._safe_int(best_card.get("cost"), 0)
            sim_energy -= remaining_energy_cost
            active_state["energy"] = sim_energy
            active_state["block"] = active_state.get("block", 0) + self._combat_analyzer._card_block_value(best_card)
            self._apply_setup_effect(best_card, active_state, combat)
            remaining.pop(best_idx)
        if not best_sequence:
            return None
        first_card, first_target = best_sequence[0]
        chosen_action = self._action_for_card(play_card_actions, first_card, target_index=first_target)
        self.logger.info(
            f"[sts2_autoplay][maximize] energy={remaining_energy} sequence={[(c[0].get('name'), c[1]) for c in best_sequence]} "
            f"lethal:{bool(tactical.get('should_prioritize_lethal'))} def:{bool(tactical.get('should_prioritize_defense'))} "
            f"incoming:{tactical.get('incoming_attack_total')} block:{active_state.get('block', 0)} "
            f"str:{active_state.get('str_stacks', 0)} weak:{active_state.get('weaken_stacks', 0)} vuln:{active_state.get('vulnerable_stacks', 0)}"
        )
        return chosen_action

    async def _select_action(self, context: dict[str, Any]) -> dict[str, Any]:
        mode = self._configured_mode()
        guidance_list = list(self._neko_guidance_queue)
        guidance_text = "\n".join(f"- {g['content']}" for g in guidance_list) if guidance_list else None
        decision_context = {**context, "neko_guidance": guidance_text} if guidance_text else context
        actions = decision_context.get("actions") if isinstance(decision_context.get("actions"), list) else []
        desperate_action = self._select_desperate_action(actions, decision_context)
        if desperate_action is not None:
            self._log_action_decision("desperate-mode", desperate_action, decision_context)
            return desperate_action
        if bool(self._cfg.get("neko_maximize_enabled", True)):
            maximize_action = self._select_maximize_benefit_action(actions, decision_context)
            if maximize_action is not None:
                self._log_action_decision("maximize-benefit", maximize_action, decision_context)
                return maximize_action
        preemptive_action = self._select_preemptive_program_action(actions, decision_context)
        if preemptive_action is not None:
            self._log_action_decision(f"{mode}-program-preflight", preemptive_action, decision_context)
            return preemptive_action
        if mode == "full-program":
            action = self._select_action_heuristic(actions, context=decision_context)
            self._log_action_decision("heuristic", action, decision_context)
            return action
        if mode == "half-program":
            try:
                action = await self._select_action_with_llm(self._configured_character_strategy(), decision_context)
                if action is not None:
                    self._drain_neko_guidance()
                    self._last_neko_guidance_used = guidance_text or ""
                    self._last_neko_guidance_count = len(guidance_list)
                    self._log_action_decision("half-program-llm", action, decision_context)
                    return action
            except Exception as exc:
                self.logger.warning(f"半程序模式决策失败，回退全程序: {exc}")
            action = self._select_action_heuristic(actions, context=decision_context)
            self._log_action_decision("half-program-heuristic-fallback", action, decision_context)
            return action
        if mode == "full-model":
            try:
                action = await self._select_action_full_model(decision_context)
                if action is not None:
                    self._drain_neko_guidance()
                    self._last_neko_guidance_used = guidance_text or ""
                    self._last_neko_guidance_count = len(guidance_list)
                    self._log_action_decision("full-model", action, decision_context)
                    return action
            except Exception as exc:
                self.logger.warning(f"全模型模式决策失败，回退半程序: {exc}")
            try:
                action = await self._select_action_with_llm(self._configured_character_strategy(), decision_context)
                if action is not None:
                    self._drain_neko_guidance()
                    self._last_neko_guidance_used = guidance_text or ""
                    self._last_neko_guidance_count = len(guidance_list)
                    self._log_action_decision("full-model-half-program-fallback", action, decision_context)
                    return action
            except Exception as exc:
                self.logger.warning(f"全模型回退半程序失败，继续回退全程序: {exc}")
            action = self._select_action_heuristic(actions, context=decision_context)
            self._log_action_decision("full-model-heuristic-fallback", action, decision_context)
            return action
        action = self._select_action_heuristic(actions, context=decision_context)
        self._log_action_decision("heuristic", action, decision_context)
        return action

    async def _select_action_with_reasoning(self, context: dict[str, Any]) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
        mode = self._configured_mode()
        actions = context.get("actions") if isinstance(context.get("actions"), list) else []
        desperate_action = self._select_desperate_action(actions, context)
        if desperate_action is not None:
            self._log_action_decision("desperate-mode", desperate_action, context)
            return desperate_action, None
        if bool(self._cfg.get("neko_maximize_enabled", True)):
            maximize_action = self._select_maximize_benefit_action(actions, context)
            if maximize_action is not None:
                self._log_action_decision("maximize-benefit", maximize_action, context)
                return maximize_action, None
        preemptive_action = self._select_preemptive_program_action(actions, context)
        if preemptive_action is not None:
            self._log_action_decision(f"{mode}-program-preflight", preemptive_action, context)
            return preemptive_action, None
        if mode == "full-program":
            action = self._select_action_heuristic(actions, context=context)
            self._log_action_decision("heuristic", action, context)
            return action, None
        guidance_list = list(self._neko_guidance_queue)
        guidance_text = "\n".join(f"- {g['content']}" for g in guidance_list) if guidance_list else None
        if guidance_text:
            context["neko_guidance"] = guidance_text
        if mode == "half-program":
            try:
                result = await self._select_action_with_llm_and_reasoning(self._configured_character_strategy(), context, neko_guidance=guidance_text)
                if result is not None:
                    action, reasoning = result
                    self._drain_neko_guidance()
                    self._last_neko_guidance_used = guidance_text or ""
                    self._last_neko_guidance_count = len(guidance_list)
                    self._log_action_decision("half-program-llm", action, context)
                    return action, reasoning
            except Exception as exc:
                self.logger.warning(f"半程序模式决策失败，回退全程序: {exc}")
            action = self._select_action_heuristic(actions, context=context)
            self._log_action_decision("half-program-heuristic-fallback", action, context)
            return action, None
        if mode == "full-model":
            try:
                result = await self._select_action_full_model_and_reasoning(context, neko_guidance=guidance_text)
                if result is not None:
                    action, reasoning = result
                    self._drain_neko_guidance()
                    self._last_neko_guidance_used = guidance_text or ""
                    self._last_neko_guidance_count = len(guidance_list)
                    self._log_action_decision("full-model", action, context)
                    return action, reasoning
            except Exception as exc:
                self.logger.warning(f"全模型模式决策失败，回退半程序: {exc}")
            try:
                result = await self._select_action_with_llm_and_reasoning(self._configured_character_strategy(), context, neko_guidance=guidance_text)
                if result is not None:
                    action, reasoning = result
                    self._drain_neko_guidance()
                    self._last_neko_guidance_used = guidance_text or ""
                    self._last_neko_guidance_count = len(guidance_list)
                    self._log_action_decision("full-model-half-program-fallback", action, context)
                    return action, reasoning
            except Exception as exc:
                self.logger.warning(f"全模型回退半程序失败，继续回退全程序: {exc}")
            action = self._select_action_heuristic(actions, context=context)
            self._log_action_decision("full-model-heuristic-fallback", action, context)
            return action, None
        action = self._select_action_heuristic(actions, context=context)
        self._log_action_decision("heuristic", action, context)
        return action, None

    def _select_preemptive_program_action(self, actions: list[dict[str, Any]], context: dict[str, Any]) -> Optional[dict[str, Any]]:
        return self._heuristic_selector.select_preemptive_program_action(actions, context, self)

    def _select_action_heuristic(self, actions: list[dict[str, Any]], *, context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        active_context = context or {"snapshot": self._snapshot}
        return self._heuristic_selector.select_action_heuristic(actions, active_context, self, self._context_analyzer, self._combat_analyzer)

    def _select_shop_remove_selection_action(self, actions: list[dict[str, Any]], context: dict[str, Any]) -> Optional[dict[str, Any]]:
        return self._heuristic_selector.select_shop_remove_selection_action(actions, context, self, self._context_analyzer)

    def _select_shop_action_heuristic(self, actions: list[dict[str, Any]], context: dict[str, Any]) -> Optional[dict[str, Any]]:
        return self._heuristic_selector.select_shop_action_heuristic(actions, context, self)

    def _find_preferred_shop_card_index(self, context: dict[str, Any]) -> Optional[int]:
        return self._heuristic_selector.find_preferred_shop_card_index(context, self)

    def _find_preferred_shop_relic_index(self, context: dict[str, Any]) -> Optional[int]:
        return self._heuristic_selector.find_preferred_shop_relic_index(context, self)

    def _find_preferred_shop_potion_index(self, context: dict[str, Any]) -> Optional[int]:
        return self._heuristic_selector.find_preferred_shop_potion_index(context, self)

    def _select_shop_remove_action(self, actions: list[dict[str, Any]], context: dict[str, Any], shop: dict[str, Any]) -> Optional[dict[str, Any]]:
        return self._heuristic_selector.select_shop_remove_action(actions, context, shop, self)

    def _find_shop_remove_card_index(self, context: dict[str, Any]) -> Optional[int]:
        return self._heuristic_selector.find_shop_remove_card_index(context, self)

    def _shop_remove_card_debug_entry(self, card: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return self._heuristic_selector.shop_remove_card_debug_entry(card, context, self)

    def _is_shop_removable_card(self, card: dict[str, Any]) -> bool:
        return self._heuristic_selector.is_shop_removable_card(card, self)

    def _shop_unremovable_card_aliases(self) -> set[str]:
        return self._heuristic_selector.shop_unremovable_card_aliases(self)

    def _shop_remove_priority(self, card: dict[str, Any]) -> int:
        return self._heuristic_selector.shop_remove_priority(card, self)

    def _shop_card_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return self._context_analyzer._shop_card_options(context)

    def _shop_relic_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return self._context_analyzer._shop_relic_options(context)

    def _shop_potion_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return self._context_analyzer._shop_potion_options(context)

    def _run_deck_cards(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        return self._context_analyzer._run_deck_cards(context)

    def _score_defect_deck_card(self, card: dict[str, Any], context: dict[str, Any]) -> int:
        return self._heuristic_selector.score_defect_deck_card(card, context, self)

    def _score_defect_shop_relic_option(self, option: dict[str, Any], context: dict[str, Any]) -> int:
        return self._heuristic_selector.score_shop_named_option(option, context, "relic", self)

    def _score_defect_shop_potion_option(self, option: dict[str, Any], context: dict[str, Any]) -> int:
        return self._heuristic_selector.score_shop_named_option(option, context, "potion", self)

    def _score_shop_named_option(self, option: dict[str, Any], context: dict[str, Any], item_type: str) -> int:
        return self._heuristic_selector.score_shop_named_option(option, context, item_type, self)

    def _score_shop_named_option_details(self, option: dict[str, Any], context: dict[str, Any], item_type: str) -> dict[str, Any]:
        return self._heuristic_selector.score_shop_named_option_details(option, context, item_type, self)

    def _score_strategy_card_option_details(self, option: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return self._heuristic_selector.score_strategy_card_option_details(option, context, self)

    def _potion_slots(self, context: dict[str, Any]) -> int:
        return self._context_analyzer._potion_slots(context)

    def _select_reward_action_heuristic(self, actions: list[dict[str, Any]], context: dict[str, Any]) -> Optional[dict[str, Any]]:
        return self._heuristic_selector.select_reward_action_heuristic(actions, context, self)

    def _find_claimable_card_reward_index(self, context: dict[str, Any]) -> Optional[int]:
        return self._context_analyzer._find_claimable_card_reward_index(context)

    def _select_weighted_play_card(self, actions: list[dict[str, Any]], combat: dict[str, Any], tactical_summary: dict[str, Any], *, attack_weight: int, defense_weight: int) -> Optional[dict[str, Any]]:
        return self._heuristic_selector.select_weighted_play_card(actions, combat, tactical_summary, attack_weight=attack_weight, defense_weight=defense_weight, selector_methods=self, combat_analyzer=self._combat_analyzer)

    def _find_defensive_action(self, actions: list[dict[str, Any]], combat: dict[str, Any], tactical_summary: dict[str, Any]) -> Optional[dict[str, Any]]:
        return self._heuristic_selector.find_defensive_action(actions, combat, tactical_summary, self, self._combat_analyzer)

    def _best_playable_damage_card(self, combat: dict[str, Any], *, target_index: Any = None, strategy_constraints=None) -> Optional[dict[str, Any]]:
        resolved_constraints = strategy_constraints if strategy_constraints is not None else self._safe_strategy_constraints(self._configured_character_strategy())
        return self._combat_analyzer._best_playable_damage_card(combat, target_index=target_index, strategy_constraints=resolved_constraints)

    def _best_playable_block_card(self, combat: dict[str, Any]) -> Optional[dict[str, Any]]:
        return self._combat_analyzer._best_playable_block_card(combat)

    def _action_for_card(self, actions: list[dict[str, Any]], card: dict[str, Any], *, target_index: Any = None) -> Optional[dict[str, Any]]:
        return self._heuristic_selector.action_for_card(actions, card, target_index, self)

    async def _select_action_full_model(self, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        return await self._llm_strategy.select_action_full_model(
            context=context,
            cfg=self._cfg,
            configured_character_strategy=self._configured_character_strategy,
            strategy_prompt_for_llm=self._strategy_prompt_for_llm,
            build_llm_decision_payload=self._build_llm_decision_payload,
            build_full_model_reasoning_messages=self._llm_strategy.build_full_model_reasoning_messages,
            build_full_model_checked_context=self._llm_strategy.build_full_model_checked_context,
            build_full_model_final_messages=self._llm_strategy.build_full_model_final_messages,
            parse_llm_reasoning_response=self._llm_strategy.parse_llm_reasoning_response,
            parse_llm_decision_response=self._llm_strategy.parse_llm_decision_response,
            validate_llm_decision=self._validate_llm_decision,
            invoke_llm_json=self._llm_strategy.invoke_llm_json,
            try_parse_llm_json=self._llm_strategy.try_parse_llm_json,
            await_stable_step_context=self._await_stable_step_context,
            llm_methods=self._llm_strategy,
        )

    def _build_full_model_reasoning_messages(self, payload: dict[str, Any], strategy_prompt: Optional[str]) -> list[dict[str, Any]]:
        return self._llm_strategy.build_full_model_reasoning_messages(payload, strategy_prompt)

    def _build_full_model_final_messages(self, payload: dict[str, Any], strategy_prompt: Optional[str]) -> list[dict[str, Any]]:
        return self._llm_strategy.build_full_model_final_messages(payload, strategy_prompt)

    async def _select_action_with_llm(self, strategy: str, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        return await self._llm_strategy.select_action_with_llm(
            strategy=strategy,
            context=context,
            cfg=self._cfg,
            strategy_prompt_for_llm=self._strategy_prompt_for_llm,
            build_llm_decision_payload=self._build_llm_decision_payload,
            invoke_llm_json=self._llm_strategy.invoke_llm_json,
            parse_llm_decision_response=self._llm_strategy.parse_llm_decision_response,
            validate_llm_decision=self._validate_llm_decision,
            llm_methods=self._llm_strategy,
        )

    async def _select_action_with_llm_and_reasoning(self, strategy: str, context: dict[str, Any], neko_guidance: Optional[str] = None) -> Optional[tuple[dict[str, Any], Optional[dict[str, Any]]]]:
        if neko_guidance:
            context["neko_guidance"] = neko_guidance
        return await self._llm_strategy.select_action_with_llm_and_reasoning(
            strategy=strategy,
            context=context,
            cfg=self._cfg,
            strategy_prompt_for_llm=self._strategy_prompt_for_llm,
            build_llm_decision_payload=self._build_llm_decision_payload,
            invoke_llm_json=self._llm_strategy.invoke_llm_json,
            parse_llm_decision_response=self._llm_strategy.parse_llm_decision_response,
            validate_llm_decision=self._validate_llm_decision,
            llm_methods=self._llm_strategy,
        )

    async def _select_action_full_model_and_reasoning(self, context: dict[str, Any], neko_guidance: Optional[str] = None) -> Optional[tuple[dict[str, Any], Optional[dict[str, Any]]]]:
        if neko_guidance:
            context["neko_guidance"] = neko_guidance
        return await self._llm_strategy.select_action_full_model_and_reasoning(
            context=context,
            cfg=self._cfg,
            configured_character_strategy=self._configured_character_strategy,
            strategy_prompt_for_llm=self._strategy_prompt_for_llm,
            build_llm_decision_payload=self._build_llm_decision_payload,
            build_full_model_reasoning_messages=self._llm_strategy.build_full_model_reasoning_messages,
            build_full_model_checked_context=self._llm_strategy.build_full_model_checked_context,
            build_full_model_final_messages=self._llm_strategy.build_full_model_final_messages,
            parse_llm_reasoning_response=self._llm_strategy.parse_llm_reasoning_response,
            parse_llm_decision_response=self._llm_strategy.parse_llm_decision_response,
            validate_llm_decision=self._validate_llm_decision,
            invoke_llm_json=self._llm_strategy.invoke_llm_json,
            try_parse_llm_json=self._llm_strategy.try_parse_llm_json,
            await_stable_step_context=self._await_stable_step_context,
            llm_methods=self._llm_strategy,
        )

    def _load_strategy_prompt(self, strategy: str) -> Optional[str]:
        return self._parser._load_strategy_prompt(strategy)

    def _load_strategy_constraints(self, strategy: str) -> dict[str, Any]:
        return self._parser._load_strategy_constraints(strategy)

    def _strategy_prompt_for_llm(self, strategy: str) -> Optional[str]:
        return self._parser._strategy_prompt_for_llm(strategy)

    async def _invoke_llm_json(self, messages: list[dict[str, Any]]) -> str:
        return await self._llm_strategy.invoke_llm_json(messages, self._cfg)

    def _try_parse_llm_json(self, raw_text: str) -> Optional[dict[str, Any]]:
        return self._llm_strategy.try_parse_llm_json(raw_text)

    async def _parse_llm_decision_response(self, raw_text: str, *, messages: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        return await self._llm_strategy.parse_llm_decision_response(raw_text, messages, self._cfg, self._llm_strategy)

    async def _parse_llm_reasoning_response(self, raw_text: str, *, messages: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        return await self._llm_strategy.parse_llm_reasoning_response(raw_text, messages, self._llm_strategy)

    def _build_llm_decision_payload(self, context: dict[str, Any], *, character_strategy: Optional[str] = None) -> dict[str, Any]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        resolved_strategy = character_strategy or self._configured_character_strategy()
        strategy_constraints = self._safe_strategy_constraints(resolved_strategy)
        payload = {
            "mode": self._configured_mode(),
            "character_strategy": resolved_strategy,
            "strategy_constraints": strategy_constraints,
            "snapshot": {
                "screen": snapshot.get("screen"),
                "floor": snapshot.get("floor"),
                "act": snapshot.get("act"),
                "in_combat": snapshot.get("in_combat"),
                "character": snapshot.get("character"),
                "turn": self._first_present(combat.get("turn"), raw_state.get("turn")),
                "player_hp": self._first_present(player.get("hp"), run.get("current_hp"), run.get("hp")),
                "max_hp": self._first_present(player.get("max_hp"), run.get("max_hp")),
                "gold": run.get("gold"),
                "energy": player.get("energy"),
            },
            "combat": self._combat_analyzer.sanitize_combat_for_prompt(combat, lambda _strategy: strategy_constraints, resolved_strategy),
            "tactical_summary": self._combat_analyzer.build_tactical_summary(combat, lambda _strategy: strategy_constraints, resolved_strategy),
            "map_summary": self._context_analyzer._build_map_summary(context),
            "legal_actions": [self._describe_legal_action(action, context) for action in context.get("actions", []) if isinstance(action, dict)],
            "neko_guidance": context.get("neko_guidance"),
        }
        return payload
