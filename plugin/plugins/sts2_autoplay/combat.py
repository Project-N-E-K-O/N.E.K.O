from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


class CombatAnalyzer:
    def __init__(self, logger) -> None:
        self.logger = logger

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except Exception:
            return default

    def _combat_state(self, context: Dict[str, Any]) -> Dict[str, Any]:
        raw_state = context.get("snapshot", {}).get("raw_state") if isinstance(context.get("snapshot"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state, dict) and isinstance(raw_state.get("combat"), dict) else {}
        return combat

    def _combat_player_block(self, combat: Dict[str, Any]) -> int:
        if not isinstance(combat, dict):
            return 0
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        return self._safe_int(
            combat.get("player_block"),
            self._safe_int(
                combat.get("current_block"),
                self._safe_int(
                    combat.get("block"),
                    self._safe_int(player.get("block"), self._safe_int(player.get("current_block"), 0)),
                ),
            ),
        )

    def _enemy_hp_value(self, enemy: Dict[str, Any]) -> int:
        return self._safe_int(enemy.get("current_hp"), self._safe_int(enemy.get("hp"), 0))

    def _enemy_block_value(self, enemy: Dict[str, Any]) -> int:
        for key in ("block", "current_block", "intent_block"):
            if key in enemy and enemy.get(key) is not None:
                return self._safe_int(enemy.get(key))
        return 0

    def _enemy_intent_attack_total(self, enemy: Dict[str, Any]) -> int:
        intents = enemy.get("intents") if isinstance(enemy.get("intents"), list) else []
        total = 0
        for item in intents:
            if not isinstance(item, dict):
                continue
            intent_type = str(item.get("intent_type") or item.get("type") or item.get("intent") or "").strip().lower()
            if intent_type and "attack" not in intent_type and "攻击" not in intent_type:
                continue
            total_damage = self._first_numeric_value(item.get("total_damage"))
            if total_damage is not None:
                total += max(0, total_damage)
                continue
            damage = self._first_numeric_value(item.get("damage"))
            if damage is None:
                damage = self._first_numeric_value(item.get("base_damage"))
            if damage is not None:
                hits = max(1, self._safe_int(self._first_numeric_value(item.get("hits")), 1))
                total += max(0, damage * hits)
                continue
            label = str(item.get("label") or "")
            label_numbers = [self._safe_int(match) for match in re.findall(r"\d+", label)]
            if label_numbers:
                if len(label_numbers) >= 2 and ("x" in label.lower() or "×" in label):
                    total += max(0, label_numbers[0] * label_numbers[1])
                else:
                    total += max(0, label_numbers[0])
        if total > 0:
            return total

        intent = enemy.get("intent")
        if isinstance(intent, dict):
            intent_type = str(intent.get("type") or intent.get("intent") or "").strip().lower()
            if intent_type and "attack" not in intent_type and "hit" not in intent_type and "攻击" not in intent_type:
                return 0
            for damage_key, hits_key in (("total_damage", None), ("damage", "hits"), ("base_damage", "hits"), ("amount", "hits")):
                damage_value = self._first_numeric_value(intent.get(damage_key))
                if damage_value is None:
                    continue
                hits_value = 1 if hits_key is None else max(1, self._safe_int(self._first_numeric_value(intent.get(hits_key)), 1))
                return max(0, damage_value * hits_value)
        elif isinstance(intent, str):
            if "attack" not in intent.lower() and "攻击" not in intent:
                return 0
            numbers = [self._safe_int(match) for match in re.findall(r"\d+", intent)]
            if numbers:
                if len(numbers) >= 2 and ("x" in intent.lower() or "×" in intent):
                    return max(0, numbers[0] * numbers[1])
                return max(0, numbers[0])
        return 0

    def _card_text_candidates(self, card: Dict[str, Any]) -> List[str]:
        if not isinstance(card, dict):
            return []
        texts: List[str] = []
        for key in ("description", "desc", "text", "body", "effect", "rules", "rules_text", "resolved_rules_text"):
            value = card.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    texts.append(stripped.lower())
            elif isinstance(value, list):
                for item in value:
                    if item is not None:
                        stripped = str(item).strip()
                        if stripped:
                            texts.append(stripped.lower())
        return texts

    def _first_numeric_value(self, value: Any) -> Optional[int]:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            match = re.search(r"-?\d+", value)
            return self._safe_int(match.group(0)) if match else None
        if isinstance(value, dict):
            for key in ("value", "amount", "current", "base", "total", "damage", "block"):
                nested = self._first_numeric_value(value.get(key))
                if nested is not None:
                    return nested
        return None

    def _card_hits_value(self, card: Dict[str, Any]) -> int:
        for key in ("hits", "hit_count", "multi_hit", "multi", "count"):
            if key in card and card.get(key) is not None:
                return max(1, self._safe_int(card.get(key), 1))
        return 1

    def _card_damage_value(self, card: Dict[str, Any]) -> int:
        if not isinstance(card, dict):
            return 0
        if self._card_is_orb_utility(card):
            return 0
        dynamic_value = self._card_dynamic_numeric_value(card, {"damage"})
        if dynamic_value is not None:
            return max(0, dynamic_value)
        for key in ("damage", "current_damage", "base_damage", "attack", "value"):
            value = self._first_numeric_value(card.get(key))
            if value is not None:
                return max(0, value)
        descriptions = self._card_text_candidates(card)
        if any(keyword in text for text in descriptions for keyword in {"造成", "damage", "伤害"}):
            for text in descriptions:
                value = self._first_numeric_value(text)
                if value is not None and value > 0:
                    return value
        return 0

    def _card_block_value(self, card: Dict[str, Any]) -> int:
        if not isinstance(card, dict):
            return 0
        dynamic_value = self._card_dynamic_numeric_value(card, {"block", "格挡"})
        if dynamic_value is not None:
            return max(0, dynamic_value)
        for key in ("block", "current_block", "base_block", "defense", "shield"):
            value = self._first_numeric_value(card.get(key))
            if value is not None:
                return max(0, value)
        descriptions = self._card_text_candidates(card)
        if any(keyword in text for text in descriptions for keyword in {"获得格挡", "gain block", "格挡"}):
            for text in descriptions:
                value = self._first_numeric_value(text)
                if value is not None and value > 0:
                    return value
        return 0

    def _card_dynamic_numeric_value(self, card: Dict[str, Any], names: set) -> Optional[int]:
        dynamic_values = card.get("dynamic_values") if isinstance(card.get("dynamic_values"), list) else []
        normalized_names = {name.strip().lower() for name in names}
        for item in dynamic_values:
            if not isinstance(item, dict):
                continue
            raw_name = str(item.get("name") or "").strip().lower()
            if raw_name not in normalized_names:
                continue
            for key in ("current_value", "enchanted_value", "base_value", "value"):
                value = self._first_numeric_value(item.get(key))
                if value is not None:
                    return value
        return None

    def _card_is_orb_utility(self, card: Dict[str, Any]) -> bool:
        if not isinstance(card, dict):
            return False
        card_type = str(card.get("type") or card.get("card_type") or "").strip().lower()
        if card_type not in {"skill", "技能"}:
            return False
        texts = self._card_text_candidates(card)
        orb_keywords = {"生成", "channel", "唤出", "球", "orb", "lightning", "frost", "dark", "plasma", "闪电球", "冰球", "黑暗球", "等离子球", "充能球", "激发", "evoke", "球栏位", "orb slot"}
        attack_keywords = {"造成", "伤害", "damage", "攻击", "attack", "hits"}
        has_orb_signal = any(keyword in text for text in texts for keyword in orb_keywords)
        has_attack_text = any(keyword in text for text in texts for keyword in attack_keywords)
        return has_orb_signal and not has_attack_text

    def _card_can_target_enemy(self, card: Dict[str, Any], target_index: Any, combat: Optional[Dict[str, Any]] = None) -> bool:
        if not isinstance(card, dict):
            return False
        if target_index is None:
            return self._card_total_damage_value(card, combat=combat) > 0
        valid_targets = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
        if not valid_targets:
            return self._card_total_damage_value(card, combat=combat, target_index=target_index) > 0
        normalized_target = self._safe_int(target_index, -9999)
        return normalized_target in [self._safe_int(target, -1) for target in valid_targets]

    def _card_total_damage_value(self, card: Dict[str, Any], combat: Optional[Dict[str, Any]] = None, target_index: Any = None, strategy_constraints: Optional[Dict[str, Any]] = None) -> int:
        if not isinstance(card, dict):
            return 0
        total = self._card_damage_value(card) * self._card_hits_value(card)
        total += self._card_strategy_damage_value(card, combat=combat, target_index=target_index, strategy_constraints=strategy_constraints)
        return total

    def _card_strategy_damage_value(self, card: Dict[str, Any], *, combat: Optional[Dict[str, Any]] = None, target_index: Any = None, strategy_constraints: Optional[Dict[str, Any]] = None) -> int:
        estimators = strategy_constraints.get("combat_estimators") if isinstance(strategy_constraints, dict) else {}
        if not isinstance(estimators, dict):
            return 0
        damage = 0
        for name, entry in estimators.items():
            if not isinstance(entry, dict):
                continue
            if not self._card_matches_estimator(card, entry):
                continue
            source = str(entry.get("source") or "").strip().lower()
            if source == "orb_evoke_and_channel":
                damage += self._card_orb_damage_value(card, combat=combat, target_index=target_index, estimator=entry)
        return damage

    def _card_matches_estimator(self, card: Dict[str, Any], estimator: Dict[str, Any]) -> bool:
        keywords = estimator.get("keywords") if isinstance(estimator.get("keywords"), list) else []
        if not keywords:
            return False
        texts = self._card_text_candidates(card)
        texts.extend(
            str(value).strip().lower()
            for value in (card.get("name"), card.get("id"), card.get("card_id"), card.get("type"), card.get("card_type"))
            if value is not None and str(value).strip()
        )
        return any(str(keyword).strip().lower() in text for keyword in keywords for text in texts)

    def _card_orb_damage_value(self, card: Dict[str, Any], *, combat: Optional[Dict[str, Any]] = None, target_index: Any = None, estimator: Optional[Dict[str, Any]] = None) -> int:
        if not isinstance(card, dict) or not isinstance(combat, dict):
            return 0
        texts = self._card_text_candidates(card)
        if not texts:
            return 0
        keywords = estimator.get("keywords") if isinstance(estimator, dict) and isinstance(estimator.get("keywords"), list) else []
        if keywords and not any(str(keyword).strip().lower() in text for keyword in keywords for text in texts):
            return 0
        orb_state = self._combat_orb_state(combat)
        if not orb_state:
            return 0
        damage = 0
        if any(keyword in text for text in texts for keyword in {"evoke", "激发"}):
            damage += self._estimate_orb_evoke_damage(orb_state, texts, target_index=target_index)
        if any(keyword in text for text in texts for keyword in {"channel", "生成", "唤出"}):
            damage += self._estimate_orb_channel_damage(orb_state, texts)
        return damage

    def _combat_orb_state(self, combat: Dict[str, Any]) -> List[Dict[str, Any]]:
        for key in ("orbs", "orb_slots", "player_orbs"):
            value = combat.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        for key in ("orbs", "orb_slots", "player_orbs"):
            value = player.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def _estimate_orb_evoke_damage(self, orbs: List[Dict[str, Any]], texts: List[str], *, target_index: Any = None) -> int:
        candidates = [orb for orb in orbs if self._orb_damage_on_evoke(orb, target_index=target_index) > 0]
        if not candidates:
            return 0
        if any(keyword in text for text in texts for keyword in {"all", "所有"}):
            return sum(self._orb_damage_on_evoke(orb, target_index=target_index) for orb in candidates)
        return self._orb_damage_on_evoke(candidates[0], target_index=target_index)

    def _estimate_orb_channel_damage(self, orbs: List[Dict[str, Any]], texts: List[str]) -> int:
        if not self._combat_orbs_full(orbs):
            return 0
        damage = 0
        channel_counts = self._channel_orb_counts(texts)
        for orb_type, count in channel_counts.items():
            if count <= 0:
                continue
            damage += count * self._orb_damage_on_evoke(orbs[0], target_index=None)
        return damage

    def _combat_orbs_full(self, orbs: List[Dict[str, Any]]) -> bool:
        if not orbs:
            return False
        return all(not self._orb_is_empty(orb) for orb in orbs)

    def _channel_orb_counts(self, texts: List[str]) -> Dict[str, int]:
        counts = {"lightning": 0, "dark": 0, "plasma": 0, "frost": 0, "generic": 0}
        orb_keywords_by_type = {
            "lightning": {"lightning", "闪电球"},
            "dark": {"dark", "黑暗球"},
            "plasma": {"plasma", "等离子球"},
            "frost": {"frost", "冰球"},
        }
        channel_phrases = ["channel", "生成", "唤出"]
        orb_phrases = ["orb", "球", "充能球"]
        for text in texts:
            lowered = text.lower()
            for phrase in channel_phrases:
                if phrase not in lowered:
                    continue
                tail = lowered.split(phrase, 1)[1].strip()
                if not tail:
                    continue
                multiplier = 1
                match = re.match(r"\s*(\d+)", tail)
                if match:
                    multiplier = max(1, self._safe_int(match.group(1), 1))
                    tail = tail[match.end():].strip()
                matched = False
                for orb_type, keywords in orb_keywords_by_type.items():
                    if any(keyword in tail for keyword in keywords):
                        counts[orb_type] += multiplier
                        matched = True
                        break
                if matched:
                    break
                if any(keyword in tail for keyword in orb_phrases):
                    counts["generic"] += multiplier
                    break
        return counts

    def _orb_damage_on_evoke(self, orb: Dict[str, Any], *, target_index: Any = None) -> int:
        orb_type = self._orb_type(orb)
        if orb_type == "lightning":
            return self._orb_numeric_value(orb, {"evoke_damage", "evoke", "passive_evoke", "damage", "amount"})
        if orb_type == "dark":
            return self._orb_numeric_value(orb, {"evoke_damage", "evoke", "damage", "amount", "passive_amount", "current"})
        return 0

    def _orb_type(self, orb: Dict[str, Any]) -> str:
        texts = [
            str(orb.get(key) or "").strip().lower()
            for key in ("type", "orb_type", "id", "name")
        ]
        joined = " ".join(texts)
        if any(keyword in joined for keyword in {"lightning", "闪电"}):
            return "lightning"
        if any(keyword in joined for keyword in {"dark", "黑暗"}):
            return "dark"
        if any(keyword in joined for keyword in {"frost", "冰"}):
            return "frost"
        if any(keyword in joined for keyword in {"plasma", "等离子"}):
            return "plasma"
        return ""

    def _orb_numeric_value(self, orb: Dict[str, Any], keys: set) -> int:
        for key in keys:
            value = self._first_numeric_value(orb.get(key))
            if value is not None:
                return max(0, value)
        dynamic_values = orb.get("dynamic_values") if isinstance(orb.get("dynamic_values"), list) else []
        normalized_keys = {key.strip().lower() for key in keys}
        for item in dynamic_values:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip().lower()
            if name not in normalized_keys:
                continue
            for field in ("current_value", "enchanted_value", "base_value", "value"):
                value = self._first_numeric_value(item.get(field))
                if value is not None:
                    return max(0, value)
        return 0

    def _orb_is_empty(self, orb: Dict[str, Any]) -> bool:
        texts = [str(orb.get(key) or "").strip().lower() for key in ("type", "orb_type", "id", "name")]
        joined = " ".join(texts)
        return joined in {"", "empty", "none", "空", "empty slot"}

    def _log_combat_block_fields(self, context: Dict[str, Any]) -> None:
        try:
            snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
            raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
            combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
            player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
            agent_view = raw_state.get("agent_view") if isinstance(raw_state.get("agent_view"), dict) else {}
            agent_view_player = agent_view.get("player") if isinstance(agent_view.get("player"), dict) else {}
            payload = {
                "combat.player_block": combat.get("player_block"),
                "combat.block": combat.get("block"),
                "combat.current_block": combat.get("current_block"),
                "combat.player": player,
                "raw_state.block": raw_state.get("block"),
                "raw_state.current_block": raw_state.get("current_block"),
                "raw_state.player": raw_state.get("player") if isinstance(raw_state.get("player"), dict) else raw_state.get("player"),
                "agent_view.player": agent_view_player,
            }
            import json
            self.logger.info(f"[sts2_autoplay][combat] block fields {json.dumps(payload, ensure_ascii=False, default=str)}")
        except Exception as exc:
            self.logger.warning(f"记录当前格挡字段失败: {exc}")

    def build_tactical_summary(self, combat: Dict[str, Any], strategy_constraints_loader, character_strategy: Optional[str] = None) -> Dict[str, Any]:
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        current_block = self._combat_player_block(combat)
        playable_hand = [card for card in hand if isinstance(card, dict) and bool(card.get("playable"))]
        constraints = strategy_constraints_loader(character_strategy)
        combat_preferences = constraints.get("combat_preferences") if isinstance(constraints, dict) and isinstance(constraints.get("combat_preferences"), dict) else {}
        combat_estimators = constraints.get("combat_estimators") if isinstance(constraints, dict) and isinstance(constraints.get("combat_estimators"), dict) else {}
        incoming_attack_total = sum(self._enemy_intent_attack_total(enemy) for enemy in enemies if isinstance(enemy, dict))
        direct_block_total = sum(self._card_block_value(card) for card in playable_hand)
        direct_damage_total = sum(self._card_total_damage_value(card, combat=combat, strategy_constraints=constraints) for card in playable_hand)
        best_attack_damage = max((self._card_total_damage_value(card, combat=combat, strategy_constraints=constraints) for card in playable_hand), default=0)
        best_playable_block = max((self._card_block_value(card) for card in playable_hand), default=0)
        lethal_targets: List[Dict[str, Any]] = []
        for enemy in enemies:
            if not isinstance(enemy, dict):
                continue
            effective_hp = self._enemy_hp_value(enemy) + self._enemy_block_value(enemy)
            if effective_hp <= 0:
                continue
            target_index = enemy.get("index")
            best_targeted_damage = max(
                (
                    self._card_total_damage_value(card, combat=combat, target_index=target_index, strategy_constraints=constraints)
                    for card in playable_hand
                    if self._card_can_target_enemy(card, target_index, combat=combat)
                ),
                default=0,
            )
            if best_targeted_damage >= effective_hp:
                lethal_targets.append({
                    "index": target_index,
                    "name": enemy.get("name") or enemy.get("id"),
                    "effective_hp": effective_hp,
                    "intent_attack": self._enemy_intent_attack_total(enemy),
                    "best_targeted_damage": best_targeted_damage,
                })
        lethal_targets.sort(key=lambda item: (-self._safe_int(item.get("intent_attack")), self._safe_int(item.get("effective_hp"), 9999), self._safe_int(item.get("index"), 9999)))
        recommended_target_index = lethal_targets[0].get("index") if lethal_targets else None
        remaining_block_needed = max(0, incoming_attack_total - current_block)
        best_effective_block = min(best_playable_block, remaining_block_needed)
        should_prioritize_defense = incoming_attack_total > current_block and best_effective_block > 0
        return {
            "character_strategy": character_strategy,
            "current_block": current_block,
            "incoming_attack_total": incoming_attack_total,
            "remaining_block_needed": remaining_block_needed,
            "direct_block_total": direct_block_total,
            "direct_damage_total": direct_damage_total,
            "best_attack_damage": best_attack_damage,
            "best_effective_block": best_effective_block,
            "can_full_block": current_block + direct_block_total >= incoming_attack_total if incoming_attack_total > 0 else True,
            "should_prioritize_defense": should_prioritize_defense,
            "lethal_targets": lethal_targets,
            "recommended_target_index": recommended_target_index,
            "should_prioritize_lethal": bool(lethal_targets),
            "strategy_preferences": {
                label: {
                    "keywords": entry.get("keywords", []),
                    "conditions": entry.get("conditions", []),
                }
                for label, entry in combat_preferences.items()
                if isinstance(entry, dict)
            },
            "strategy_estimators": {
                label: {
                    "keywords": entry.get("keywords", []),
                    "conditions": entry.get("conditions", []),
                }
                for label, entry in combat_estimators.items()
                if isinstance(entry, dict)
            },
        }

    def sanitize_combat_for_prompt(self, combat: Dict[str, Any], strategy_constraints_loader, character_strategy: Optional[str] = None) -> Dict[str, Any]:
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        constraints = strategy_constraints_loader(character_strategy)
        return {
            "turn": combat.get("turn"),
            "turn_count": combat.get("turn_count"),
            "player_energy": combat.get("player_energy"),
            "player_block": self._combat_player_block(combat),
            "end_turn_available": combat.get("end_turn_available"),
            "hand": [
                {
                    "index": card.get("index"),
                    "name": card.get("name") or card.get("id"),
                    "id": card.get("id"),
                    "type": card.get("type") or card.get("card_type"),
                    "cost": card.get("cost"),
                    "damage": self._card_damage_value(card),
                    "block": self._card_block_value(card),
                    "hits": self._card_hits_value(card),
                    "strategy_setup_score": self._card_strategy_setup_score(card, combat, constraints),
                    "matches_strategy_setup": self._card_matches_strategy_setup(card, constraints),
                    "playable": bool(card.get("playable")),
                    "valid_target_indices": card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else [],
                }
                for card in hand[:12]
                if isinstance(card, dict)
            ],
            "enemies": [
                {
                    "index": enemy.get("index"),
                    "name": enemy.get("name") or enemy.get("id"),
                    "hp": self._enemy_hp_value(enemy),
                    "block": self._enemy_block_value(enemy),
                    "intent": enemy.get("intent"),
                    "intent_attack": self._enemy_intent_attack_total(enemy),
                }
                for enemy in enemies[:6]
                if isinstance(enemy, dict)
            ],
        }

    def _strategy_setup_keywords(self, strategy_constraints: Optional[Dict[str, Any]] = None) -> List[str]:
        constraints = strategy_constraints if isinstance(strategy_constraints, dict) else {}
        combat_preferences = constraints.get("combat_preferences") if isinstance(constraints, dict) else {}
        keywords: List[str] = []
        if not isinstance(combat_preferences, dict):
            return keywords
        for entry in combat_preferences.values():
            if not isinstance(entry, dict):
                continue
            for keyword in entry.get("keywords", []):
                normalized = str(keyword).strip().lower()
                if normalized and normalized not in keywords:
                    keywords.append(normalized)
        return keywords

    def _card_matches_strategy_setup(self, card: Dict[str, Any], strategy_constraints: Optional[Dict[str, Any]] = None) -> bool:
        if not isinstance(card, dict):
            return False
        keywords = self._strategy_setup_keywords(strategy_constraints)
        if not keywords:
            return False
        texts = self._card_text_candidates(card)
        searchable_parts = list(texts)
        searchable_parts.extend(
            str(value).strip().lower()
            for value in (card.get("name"), card.get("id"), card.get("card_id"), card.get("type"), card.get("card_type"))
            if value is not None and str(value).strip()
        )
        return any(keyword in part for part in searchable_parts for keyword in keywords)

    def _card_strategy_setup_score(self, card: Dict[str, Any], combat: Optional[Dict[str, Any]] = None, strategy_constraints: Optional[Dict[str, Any]] = None) -> int:
        if not isinstance(card, dict) or not self._card_matches_strategy_setup(card, strategy_constraints):
            return 0
        texts = self._card_text_candidates(card)
        score = 7
        if any(keyword in text for text in texts for keyword in {"gain block", "格挡", "block"}):
            score += 1
        if any(keyword in text for text in texts for keyword in {"draw", "抽", "检索", "retain", "保留"}):
            score += 1
        cost = self._safe_int(card.get("cost"), 0)
        if cost <= 0:
            score += 1
        elif cost >= 2:
            score -= 1
        if isinstance(combat, dict):
            enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
            if any(self._enemy_intent_attack_total(enemy) > 0 for enemy in enemies if isinstance(enemy, dict)):
                score += 1
        return max(score, 0)

    def _best_playable_damage_card(self, combat: Dict[str, Any], *, target_index: Any = None, strategy_constraints: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        playable = [c for c in hand if isinstance(c, dict) and bool(c.get("playable"))]
        if not playable:
            return None
        best: Optional[Dict[str, Any]] = None
        best_damage = 0
        for card in playable:
            dmg = self._card_total_damage_value(card, combat=combat, target_index=target_index, strategy_constraints=strategy_constraints)
            if dmg > best_damage:
                best_damage = dmg
                best = card
        return best

    def _best_playable_block_card(self, combat: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        playable = [c for c in hand if isinstance(c, dict) and bool(c.get("playable"))]
        if not playable:
            return None
        best: Optional[Dict[str, Any]] = None
        best_block = 0
        for card in playable:
            blk = self._card_block_value(card)
            if blk > best_block:
                best_block = blk
                best = card
        return best
