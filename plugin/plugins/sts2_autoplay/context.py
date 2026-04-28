from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Set


class GameContextAnalyzer:
    def __init__(self, logger) -> None:
        self.logger = logger

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except Exception:
            return default

    def _normalized_screen_name(self, snapshot: Dict[str, Any]) -> str:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        screen = snapshot.get("screen") or raw_state.get("screen") or raw_state.get("screen_type") or ""
        return str(screen).strip().lower()

    def _is_eventish_screen(self, screen: str) -> bool:
        normalized = (screen or "").strip().lower()
        if not normalized:
            return False
        return any(keyword in normalized for keyword in {"event", "modal", "overlay", "dialog", "choice"})

    def _potions(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw_state = context.get("snapshot", {}).get("raw_state") if isinstance(context.get("snapshot"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state, dict) and isinstance(raw_state.get("run"), dict) else {}
        potions = run.get("potions") if isinstance(run.get("potions"), list) else []
        return [potion for potion in potions if isinstance(potion, dict)]

    def _potion_slots(self, context: Dict[str, Any]) -> int:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        for key in ("potion_slots", "potionSlotCount", "potion_capacity"):
            value = self._safe_int(run.get(key), -1)
            if value >= 0:
                return value
        return 3

    def _map_state(self, context: Dict[str, Any]) -> Dict[str, Any]:
        raw_state = context.get("snapshot", {}).get("raw_state") if isinstance(context.get("snapshot"), dict) else {}
        if not isinstance(raw_state, dict):
            return {}
        for key in ("map", "map_state", "current_map", "pathing"):
            value = raw_state.get(key)
            if isinstance(value, dict):
                return value
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        for key in ("map", "map_state", "current_map", "pathing"):
            value = run.get(key)
            if isinstance(value, dict):
                return value
        return {}

    def _build_map_summary(self, context: Dict[str, Any]) -> Dict[str, Any]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        map_state = self._map_state(context)
        action = next(
            (
                item for item in context.get("actions", [])
                if isinstance(item, dict) and str(item.get("type") or "") == "choose_map_node"
            ),
            None,
        )
        raw_action = action.get("raw") if isinstance(action, dict) and isinstance(action.get("raw"), dict) else {}
        choices = self._extract_generic_option_descriptions(raw_action)
        return {
            "current_hp": raw_state.get("current_hp") or run.get("current_hp") or run.get("hp"),
            "max_hp": raw_state.get("max_hp") or run.get("max_hp"),
            "gold": run.get("gold"),
            "floor": snapshot.get("floor") or raw_state.get("floor") or raw_state.get("act_floor"),
            "act": snapshot.get("act") or raw_state.get("act"),
            "boss": raw_state.get("boss") or run.get("boss"),
            "available_nodes": choices,
            "raw_map": map_state,
        }

    def _extract_generic_option_descriptions(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        options: List[Dict[str, Any]] = []
        seen: Set[int] = set()
        for candidate in self._iter_option_candidates(raw):
            if not isinstance(candidate, list):
                continue
            for idx, item in enumerate(candidate):
                if not isinstance(item, dict):
                    continue
                option_index = item.get("option_index", item.get("index", idx))
                try:
                    normalized_index = int(option_index)
                except Exception:
                    continue
                if normalized_index in seen:
                    continue
                seen.add(normalized_index)
                options.append({
                    "index": normalized_index,
                    "label": str(item.get("label") or item.get("description") or item.get("name") or item.get("id") or normalized_index),
                    "type": item.get("node_type") or item.get("type") or item.get("kind") or item.get("symbol"),
                    "raw": item,
                })
        return options

    def _combat_state(self, context: Dict[str, Any]) -> Dict[str, Any]:
        raw_state = context.get("snapshot", {}).get("raw_state") if isinstance(context.get("snapshot"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state, dict) and isinstance(raw_state.get("combat"), dict) else {}
        return combat

    def _run_deck_cards(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        deck = run.get("deck") if isinstance(run.get("deck"), list) else []
        return [card for card in deck if isinstance(card, dict)]

    def _is_character_select_context(self, context: Dict[str, Any]) -> bool:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        screen = self._normalized_screen_name(snapshot)
        text_candidates = {
            screen,
            str(raw_state.get("screen") or "").strip().lower(),
            str(raw_state.get("screen_type") or "").strip().lower(),
        }
        if any(keyword in candidate for candidate in text_candidates for keyword in {"char", "character", "player select", "select"} if candidate):
            return True
        for action in context.get("actions", []):
            if not isinstance(action, dict):
                continue
            raw_action = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            if self._character_selection_options(raw_action, context):
                return True
        return False

    def _character_selection_options(self, raw: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        for candidate in self._iter_option_candidates(raw):
            options = self._extract_character_options(candidate)
            if options:
                return options
        for action in context.get("actions", []):
            if not isinstance(action, dict):
                continue
            raw_action = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            for candidate in self._iter_option_candidates(raw_action):
                options = self._extract_character_options(candidate)
                if options:
                    return options
        return []

    def _extract_character_options(self, candidate: Any) -> List[Dict[str, Any]]:
        if isinstance(candidate, list):
            options: List[Dict[str, Any]] = []
            for idx, item in enumerate(candidate):
                if not isinstance(item, dict):
                    continue
                option_index = item.get("option_index")
                if option_index is None:
                    option_index = item.get("index", idx)
                try:
                    normalized_index = int(option_index)
                except Exception:
                    continue
                option = {
                    "index": normalized_index,
                    "texts": self._character_option_texts(item),
                }
                if option["texts"]:
                    options.append(option)
            return options
        if isinstance(candidate, dict):
            nested_keys = ("options", "choices", "characters", "items")
            for key in nested_keys:
                nested = candidate.get(key)
                if not isinstance(nested, list):
                    continue
                options = self._extract_character_options(nested)
                if options:
                    return options
        return []

    def _character_option_texts(self, item: Dict[str, Any]) -> Set[str]:
        texts: Set[str] = set()
        for key in ("label", "description", "name", "id", "character", "character_id", "class", "player_class"):
            value = item.get(key)
            if value is not None:
                normalized = str(value).strip().lower()
                if normalized:
                    texts.add(normalized)
        return texts

    def _character_option_matches(self, option: Dict[str, Any], aliases: Set[str]) -> bool:
        texts = option.get("texts") if isinstance(option.get("texts"), set) else set()
        return any(alias in texts for alias in aliases)

    def _is_rewardish_screen(self, snapshot: Dict[str, Any]) -> bool:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        screen_candidates = {
            self._normalized_screen_name(snapshot),
            str(raw_state.get("screen") or "").strip().lower(),
            str(raw_state.get("screen_type") or "").strip().lower(),
        }
        return any(keyword in candidate for candidate in screen_candidates for keyword in {"reward", "card reward", "combat reward"} if candidate)

    def _is_card_reward_context(self, raw: Dict[str, Any], context: Dict[str, Any]) -> bool:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        screen_candidates = {
            self._normalized_screen_name(snapshot),
            str(raw_state.get("screen") or "").strip().lower(),
            str(raw_state.get("screen_type") or "").strip().lower(),
        }
        if any(keyword in candidate for candidate in screen_candidates for keyword in {"reward", "card reward", "combat reward"} if candidate):
            return True
        return bool(self._card_reward_options(raw, context))

    def _card_reward_options(self, raw: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        for candidate in (
            raw_state.get("reward") if isinstance(raw_state.get("reward"), dict) else None,
            raw_state.get("selection") if isinstance(raw_state.get("selection"), dict) else None,
            raw_state.get("agent_view", {}).get("reward") if isinstance(raw_state.get("agent_view"), dict) and isinstance(raw_state.get("agent_view", {}).get("reward"), dict) else None,
            raw_state.get("agent_view", {}).get("selection") if isinstance(raw_state.get("agent_view"), dict) and isinstance(raw_state.get("agent_view", {}).get("selection"), dict) else None,
        ):
            options = self._extract_card_reward_options(candidate)
            if options:
                return options
        for candidate in self._iter_option_candidates(raw):
            options = self._extract_card_reward_options(candidate)
            if options:
                return options
        for action in context.get("actions", []):
            if not isinstance(action, dict):
                continue
            raw_action = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            for candidate in self._iter_option_candidates(raw_action):
                options = self._extract_card_reward_options(candidate)
                if options:
                    return options
        if self._is_rewardish_screen(snapshot):
            self._log_reward_payload_debug(raw, context)
        return []

    def _iter_option_candidates(self, raw: Dict[str, Any]) -> List[Any]:
        return [
            raw,
            raw.get("action") if isinstance(raw.get("action"), dict) else None,
            raw.get("options"),
            raw.get("choices"),
            raw.get("cards"),
            raw.get("card_options"),
            raw.get("items"),
            raw.get("rewards"),
        ]

    def _extract_card_reward_options(self, candidate: Any) -> List[Dict[str, Any]]:
        if isinstance(candidate, list):
            options: List[Dict[str, Any]] = []
            for idx, item in enumerate(candidate):
                if not isinstance(item, dict):
                    continue
                texts = self._card_option_texts(item)
                if not texts:
                    continue
                option_index = item.get("option_index")
                if option_index is None:
                    option_index = item.get("index", idx)
                try:
                    normalized_index = int(option_index)
                except Exception:
                    continue
                option = {
                    "index": normalized_index,
                    "texts": texts,
                    "raw": item,
                }
                options.append(option)
            return options
        if isinstance(candidate, dict):
            for key in ("options", "choices", "cards", "card_options", "items", "rewards"):
                nested = candidate.get(key)
                if not isinstance(nested, list):
                    continue
                options = self._extract_card_reward_options(nested)
                if options:
                    return options
        return []

    def _card_option_texts(self, item: Dict[str, Any]) -> Set[str]:
        texts: Set[str] = set()
        for key in ("label", "description", "name", "id", "card_id", "card_name", "relic_name", "potion_name", "title"):
            value = item.get(key)
            if value is not None:
                normalized = str(value).strip().lower()
                if normalized:
                    texts.add(normalized)
        card = item.get("card") if isinstance(item.get("card"), dict) else None
        if card is not None:
            texts.update(self._card_option_texts(card))
        return texts

    def _log_reward_payload_debug(self, raw: Dict[str, Any], context: Dict[str, Any]) -> None:
        try:
            snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
            raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
            raw_actions = snapshot.get("raw_actions") if isinstance(snapshot.get("raw_actions"), dict) else {}
            action_raws = [
                action.get("raw")
                for action in context.get("actions", [])
                if isinstance(action, dict) and isinstance(action.get("raw"), dict)
            ]
            debug_payload = {
                "screen": snapshot.get("screen"),
                "raw_state_keys": sorted(raw_state.keys()),
                "raw_actions_keys": sorted(raw_actions.keys()),
                "focused_raw": raw,
                "raw_state_reward": raw_state.get("reward"),
                "raw_state_selection": raw_state.get("selection"),
                "raw_state_agent_view": raw_state.get("agent_view"),
                "raw_actions": raw_actions,
                "action_raws": action_raws,
            }
            self.logger.info(f"[sts2_autoplay][reward-options] debug {json.dumps(debug_payload, ensure_ascii=False, default=str)}")
        except Exception as exc:
            self.logger.warning(f"记录奖励界面调试信息失败: {exc}")

    def _find_claimable_card_reward_index(self, context: Dict[str, Any]) -> Optional[int]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        for container in (
            raw_state.get("reward") if isinstance(raw_state.get("reward"), dict) else None,
            raw_state.get("agent_view", {}).get("reward") if isinstance(raw_state.get("agent_view"), dict) and isinstance(raw_state.get("agent_view", {}).get("reward"), dict) else None,
        ):
            if not isinstance(container, dict):
                continue
            if bool(container.get("pending_card_choice")):
                continue
            rewards = container.get("rewards") if isinstance(container.get("rewards"), list) else []
            for idx, reward in enumerate(rewards):
                if not isinstance(reward, dict) or not bool(reward.get("claimable", True)):
                    continue
                reward_type = str(reward.get("reward_type") or "").strip().lower()
                line = str(reward.get("line") or reward.get("description") or "").strip().lower()
                if reward_type == "card" or line.startswith("card:") or "添加到你的牌组" in line:
                    return self._safe_int(reward.get("index", reward.get("i", idx)), idx)
        return None

    def _shop_card_options(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        shop = raw_state.get("shop") if isinstance(raw_state.get("shop"), dict) else {}
        cards = shop.get("cards") if isinstance(shop.get("cards"), list) else []
        options: List[Dict[str, Any]] = []
        for idx, item in enumerate(cards):
            if not isinstance(item, dict) or not bool(item.get("is_stocked")) or not bool(item.get("enough_gold")):
                continue
            texts = self._card_option_texts(item)
            if not texts:
                continue
            option_index = item.get("index", idx)
            try:
                normalized_index = int(option_index)
            except Exception:
                continue
            options.append({"index": normalized_index, "texts": texts, "raw": item})
        return options

    def _shop_relic_options(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        shop = raw_state.get("shop") if isinstance(raw_state.get("shop"), dict) else {}
        relics = shop.get("relics") if isinstance(shop.get("relics"), list) else []
        return self._shop_named_options(relics)

    def _shop_potion_options(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        shop = raw_state.get("shop") if isinstance(raw_state.get("shop"), dict) else {}
        potions = shop.get("potions") if isinstance(shop.get("potions"), list) else []
        return self._shop_named_options(potions)

    def _shop_named_options(self, items: List[Any]) -> List[Dict[str, Any]]:
        options: List[Dict[str, Any]] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict) or not bool(item.get("is_stocked")) or not bool(item.get("enough_gold")):
                continue
            texts = self._card_option_texts(item)
            if not texts:
                continue
            option_index = item.get("index", idx)
            try:
                normalized_index = int(option_index)
            except Exception:
                continue
            options.append({"index": normalized_index, "texts": texts, "raw": item})
        return options

    def _defect_has_card(self, context: Dict[str, Any], names: Set[str]) -> bool:
        raw_state = context.get("snapshot", {}).get("raw_state") if isinstance(context.get("snapshot"), dict) else {}
        for container_key in ("deck", "master_deck", "cards"):
            cards = raw_state.get(container_key)
            if not isinstance(cards, list):
                continue
            for card in cards:
                if not isinstance(card, dict):
                    continue
                card_texts = self._card_option_texts(card)
                if any(any(name in text for text in card_texts) for name in names):
                    return True
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        deck = run.get("deck") if isinstance(run.get("deck"), list) else []
        for card in deck:
            if not isinstance(card, dict):
                continue
            card_texts = self._card_option_texts(card)
            if any(any(name in text for text in card_texts) for name in names):
                return True
        return False

    def _score_defect_map_option(self, option: Dict[str, Any], context: Dict[str, Any]) -> int:
        map_summary = self._build_map_summary(context)
        raw = option.get("raw") if isinstance(option.get("raw"), dict) else {}
        text_blob = " ".join(
            str(value).lower()
            for value in (
                option.get("label"),
                option.get("type"),
                raw.get("label"),
                raw.get("description"),
                raw.get("name"),
                raw.get("id"),
                raw.get("node_type"),
                raw.get("kind"),
                raw.get("symbol"),
            )
            if value is not None
        )
        act = self._safe_int(map_summary.get("act"), 1)
        current_hp = self._safe_int(map_summary.get("current_hp"))
        max_hp = self._safe_int(map_summary.get("max_hp"))
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 0.0
        gold = self._safe_int(map_summary.get("gold"))
        score = 0

        is_elite = any(token in text_blob for token in {"elite", "精英"})
        is_rest = any(token in text_blob for token in {"rest", "campfire", "篝火", "fire"})
        is_shop = any(token in text_blob for token in {"shop", "merchant", "商店"})
        is_event = any(token in text_blob for token in {"event", "question", "unknown", "?", "问号"})
        is_monster = any(token in text_blob for token in {"monster", "enemy", "combat", "battle", "普通怪", "战斗"})

        if act == 1:
            if is_elite:
                score += 34 if hp_ratio >= 0.65 else -20
            if is_monster:
                score += 24
            if is_event:
                score += 8
            if is_rest:
                score += 18 if hp_ratio < 0.55 else 6
            if is_shop:
                score += 22 if gold >= 120 else 8
        elif act == 2:
            if is_elite:
                score += 28 if hp_ratio >= 0.7 else -26
            if is_monster:
                score += 10
            if is_event:
                score += 16
            if is_rest:
                score += 22 if hp_ratio < 0.6 else 10
            if is_shop:
                score += 20 if gold >= 140 else 10
        else:
            if is_elite:
                score += 12 if hp_ratio >= 0.8 else -30
            if is_monster:
                score -= 4
            if is_event:
                score += 24
            if is_rest:
                score += 24 if hp_ratio < 0.75 else 14
            if is_shop:
                score += 18 if gold >= 150 else 8

        branching = self._estimate_branching_value(raw)
        score += branching * 3
        if self._option_has_nearby_buffer(raw):
            score += 8
        if is_elite and not self._option_has_nearby_buffer(raw):
            score -= 10
        return score

    def _option_has_nearby_buffer(self, raw: Dict[str, Any]) -> bool:
        stack: List[Any] = [raw]
        keywords = {"rest", "campfire", "篝火", "shop", "merchant", "商店", "event", "question", "问号"}
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for value in current.values():
                    if isinstance(value, (dict, list)):
                        stack.append(value)
                    elif value is not None:
                        text = str(value).lower()
                        if any(keyword in text for keyword in keywords):
                            return True
            elif isinstance(current, list):
                stack.extend(current)
        return False

    def _estimate_branching_value(self, raw: Dict[str, Any]) -> int:
        stack: List[Any] = [raw]
        best = 0
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for key, value in current.items():
                    lowered = str(key).lower()
                    if lowered in {"next_nodes", "children", "neighbors", "branches", "paths", "next"} and isinstance(value, list):
                        best = max(best, len(value))
                    if isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(current, list):
                stack.extend(current)
        return best
