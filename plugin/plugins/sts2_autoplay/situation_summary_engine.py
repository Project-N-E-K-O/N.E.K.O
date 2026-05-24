from __future__ import annotations

from typing import Any

from .situation_models import SituationDelta


class STS2SituationSummaryEngine:
    def __init__(self, i18n: Any = None) -> None:
        self._i18n = i18n

    def t(self, key: str, *, default: str = "", **params: Any) -> str:
        if self._i18n is not None:
            return self._i18n.t(key, default=default, **params)
        return default.format(**params) if params and default else (default or key)

    def summarize(self, summary_context: dict[str, Any]) -> dict[str, Any]:
        summary_kind = str(summary_context.get("summary_kind") or "general")
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        formatter = getattr(self, f"_summarize_{summary_kind}", self._summarize_general)
        static_text = formatter(payload)
        action_frame = summary_context.get("action_frame") if isinstance(summary_context.get("action_frame"), dict) else {}
        continuous_delta = summary_context.get("continuous_delta") if isinstance(summary_context.get("continuous_delta"), dict) else {}
        preferred_delta = action_frame.get("delta") if isinstance(action_frame.get("delta"), dict) and action_frame.get("delta") else continuous_delta
        delta_text = str(preferred_delta.get("text") or "") if isinstance(preferred_delta, dict) else ""
        text = static_text if not delta_text else f"{static_text}；{delta_text}"
        return {
            "kind": summary_kind,
            "text": text,
            "payload": payload,
            "before": dict(action_frame.get("before") or {}),
            "after": dict(action_frame.get("after") or {}),
            "delta": dict(preferred_delta or {}),
            "source": str((preferred_delta or {}).get("source") or "snapshot"),
            "static_text": static_text,
            "delta_text": delta_text,
        }

    def compute_delta(self, before: dict[str, Any] | None, after: dict[str, Any] | None, *, source: str) -> dict[str, Any]:
        before_obj = dict(before) if isinstance(before, dict) else {}
        after_obj = dict(after) if isinstance(after, dict) else {}
        if not before_obj or not after_obj:
            return SituationDelta(source=source, text="").as_dict()

        before_player = before_obj.get("player") if isinstance(before_obj.get("player"), dict) else {}
        after_player = after_obj.get("player") if isinstance(after_obj.get("player"), dict) else {}
        before_enemies = before_obj.get("enemies") if isinstance(before_obj.get("enemies"), dict) else {}
        after_enemies = after_obj.get("enemies") if isinstance(after_obj.get("enemies"), dict) else {}
        before_hand = before_obj.get("hand") if isinstance(before_obj.get("hand"), dict) else {}
        after_hand = after_obj.get("hand") if isinstance(after_obj.get("hand"), dict) else {}

        screen_change = {
            "from": before_obj.get("screen"),
            "to": after_obj.get("screen"),
        }
        if screen_change["from"] == screen_change["to"]:
            screen_change = {}

        player_changes = {
            "hp_delta": self._delta(after_player.get("current_hp"), before_player.get("current_hp")),
            "block_delta": self._delta(after_player.get("block"), before_player.get("block")),
            "energy_delta": self._delta(after_player.get("energy"), before_player.get("energy")),
            "gold_delta": self._delta(after_player.get("gold"), before_player.get("gold")),
        }
        enemy_changes = {
            "enemy_count_delta": self._delta(after_enemies.get("count"), before_enemies.get("count")),
            "enemy_total_hp_delta": self._delta(after_enemies.get("total_hp"), before_enemies.get("total_hp")),
            "enemy_attack_total_delta": self._delta(after_enemies.get("attack_total"), before_enemies.get("attack_total")),
        }
        before_names = set(self._string_list(before_hand.get("names")))
        after_names = set(self._string_list(after_hand.get("names")))
        hand_changes = {
            "hand_count_delta": self._delta(after_hand.get("count"), before_hand.get("count")),
            "entered_cards": sorted(after_names - before_names),
            "left_cards": sorted(before_names - after_names),
        }

        notable_events: list[str] = []
        if screen_change:
            notable_events.append(f"screen:{screen_change['from']}->{screen_change['to']}")
        if bool(before_obj.get("in_combat")) and not bool(after_obj.get("in_combat")):
            notable_events.append("combat_ended")
        if not bool(before_obj.get("in_combat")) and bool(after_obj.get("in_combat")):
            notable_events.append("combat_started")

        delta = SituationDelta(
            source=source,
            screen_change=screen_change,
            player_changes=player_changes,
            enemy_changes=enemy_changes,
            hand_changes=hand_changes,
            notable_events=notable_events,
        )
        delta.text = self.render_delta_text(delta.as_dict())
        return delta.as_dict()

    def render_delta_text(self, delta: dict[str, Any]) -> str:
        parts: list[str] = []
        screen_change = delta.get("screen_change") if isinstance(delta.get("screen_change"), dict) else {}
        if screen_change:
            parts.append(self.t("summary.delta.screen_change", default="画面从 {from_screen} 切换到 {to_screen}", from_screen=screen_change.get("from"), to_screen=screen_change.get("to")))
        player_changes = delta.get("player_changes") if isinstance(delta.get("player_changes"), dict) else {}
        enemy_changes = delta.get("enemy_changes") if isinstance(delta.get("enemy_changes"), dict) else {}
        hand_changes = delta.get("hand_changes") if isinstance(delta.get("hand_changes"), dict) else {}
        self._append_signed(parts, "summary.delta.player_hp", "玩家血量 {value}", player_changes.get("hp_delta"))
        self._append_signed(parts, "summary.delta.block", "护盾 {value}", player_changes.get("block_delta"))
        self._append_signed(parts, "summary.delta.energy", "能量 {value}", player_changes.get("energy_delta"))
        self._append_signed(parts, "summary.delta.gold", "金币 {value}", player_changes.get("gold_delta"))
        self._append_signed(parts, "summary.delta.enemy_hp", "敌方总血量 {value}", enemy_changes.get("enemy_total_hp_delta"))
        self._append_signed(parts, "summary.delta.enemy_attack", "敌方总攻击意图 {value}", enemy_changes.get("enemy_attack_total_delta"))
        entered = self._string_list(hand_changes.get("entered_cards"))
        left = self._string_list(hand_changes.get("left_cards"))
        if entered:
            parts.append(self.t("summary.delta.entered_cards", default="新入手牌：{cards}", cards="、".join(entered[:3])))
        if left:
            parts.append(self.t("summary.delta.left_cards", default="离开手牌：{cards}", cards="、".join(left[:3])))
        return "，".join(parts)

    def _summarize_general(self, payload: dict[str, Any]) -> str:
        screen = payload.get("screen") or "unknown"
        actions = self._join_actions(payload.get("available_actions"))
        phase = payload.get("phase") or "unknown"
        return self.t("summary.general", default="当前位于 {screen}，phase={phase}，可执行动作：{actions}。", screen=screen, phase=phase, actions=actions)

    def _summarize_menu(self, payload: dict[str, Any]) -> str:
        selected = payload.get("selected_character_id") or self.t("summary.menu.no_character", default="未选择角色")
        ascension = payload.get("ascension")
        can_embark = payload.get("can_embark")
        actions = self._join_actions(payload.get("available_actions"))
        embark_text = self.t("summary.menu.can_embark", default="可开始") if can_embark else self.t("summary.menu.cannot_embark", default="暂不可开始")
        asc_text = f"，ascension={ascension}" if ascension is not None else ""
        return self.t("summary.menu", default="当前处于菜单/角色选择状态，当前角色：{selected}{asc_text}，{embark_text}，可执行动作：{actions}。", selected=selected, asc_text=asc_text, embark_text=embark_text, actions=actions)

    def _summarize_event(self, payload: dict[str, Any]) -> str:
        event_name = payload.get("event_name") or payload.get("event_id") or self.t("summary.event.unknown", default="未知事件")
        hp = self._hp_text(payload.get("current_hp"), payload.get("max_hp"))
        gold = payload.get("gold")
        actions = self._join_actions(payload.get("available_actions"))
        return self.t("summary.event", default="当前事件：{event_name}，血量 {hp}，金币 {gold}，可执行动作：{actions}。", event_name=event_name, hp=hp, gold=gold, actions=actions)

    def _summarize_map(self, payload: dict[str, Any]) -> str:
        hp = self._hp_text(payload.get("current_hp"), payload.get("max_hp"))
        gold = payload.get("gold")
        nodes = payload.get("travelable_nodes") if isinstance(payload.get("travelable_nodes"), list) else []
        node_count = len(nodes)
        return self.t("summary.map", default="当前地图选择状态，血量 {hp}，金币 {gold}，可前往节点数 {node_count}。", hp=hp, gold=gold, node_count=node_count)

    def _summarize_combat(self, payload: dict[str, Any]) -> str:
        player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
        hand = payload.get("hand") if isinstance(payload.get("hand"), list) else []
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        hp = self._hp_text(player.get("current_hp"), player.get("max_hp"))
        block = player.get("block", 0)
        energy = player.get("energy", 0)
        hand_names = "、".join(str(card.get("name") or card.get("card_id") or "?") for card in hand[:5] if isinstance(card, dict)) or self.t("summary.combat.no_hand", default="无手牌信息")
        enemy_bits = []
        for enemy in enemies[:3]:
            if not isinstance(enemy, dict):
                continue
            name = str(enemy.get("name") or enemy.get("enemy_id") or self.t("summary.combat.enemy_default", default="敌人"))
            current_hp = enemy.get("current_hp") or enemy.get("hp")
            max_hp = enemy.get("max_hp")
            intent = enemy.get("intent") or "unknown"
            enemy_bits.append(self.t("summary.combat.enemy_line", default="{name}({hp}，意图={intent})", name=name, hp=self._hp_text(current_hp, max_hp), intent=intent))
        enemy_text = "；".join(enemy_bits) if enemy_bits else self.t("summary.combat.no_enemy", default="无敌人信息")
        return self.t("summary.combat", default="当前战斗状态，玩家血量 {hp}，能量 {energy}，护盾 {block}，手牌：{hand_names}，敌人：{enemy_text}。", hp=hp, energy=energy, block=block, hand_names=hand_names, enemy_text=enemy_text)

    def _summarize_reward(self, payload: dict[str, Any]) -> str:
        hp = self._hp_text(payload.get("current_hp"), payload.get("max_hp"))
        gold = payload.get("gold")
        actions = self._join_actions(payload.get("available_actions"))
        return self.t("summary.reward", default="当前奖励状态，血量 {hp}，金币 {gold}，可执行动作：{actions}。", hp=hp, gold=gold, actions=actions)

    def _summarize_shop(self, payload: dict[str, Any]) -> str:
        hp = self._hp_text(payload.get("current_hp"), payload.get("max_hp"))
        gold = payload.get("gold")
        return self.t("summary.shop", default="当前商店状态，血量 {hp}，金币 {gold}。", hp=hp, gold=gold)

    def _summarize_rest(self, payload: dict[str, Any]) -> str:
        hp = self._hp_text(payload.get("current_hp"), payload.get("max_hp"))
        actions = self._join_actions(payload.get("available_actions"))
        return self.t("summary.rest", default="当前休息点状态，血量 {hp}，可执行动作：{actions}。", hp=hp, actions=actions)

    def _summarize_selection(self, payload: dict[str, Any]) -> str:
        hp = self._hp_text(payload.get("current_hp"), payload.get("max_hp"))
        actions = self._join_actions(payload.get("available_actions"))
        return self.t("summary.selection", default="当前卡牌选择状态，血量 {hp}，可执行动作：{actions}。", hp=hp, actions=actions)

    def _hp_text(self, current_hp: Any, max_hp: Any) -> str:
        if current_hp is None and max_hp is None:
            return "unknown"
        if max_hp is None:
            return str(current_hp)
        return f"{current_hp}/{max_hp}"

    def _join_actions(self, actions: Any) -> str:
        if not isinstance(actions, list) or not actions:
            return self.t("summary.actions.none", default="无")
        return "、".join(str(action) for action in actions)

    def _delta(self, after: Any, before: Any) -> int:
        after_value = self._safe_int(after)
        before_value = self._safe_int(before)
        return after_value - before_value

    def _safe_int(self, value: Any) -> int:
        try:
            if value is None:
                return 0
            return int(value)
        except Exception:
            return 0

    def _append_signed(self, parts: list[str], key: str, default: str, value: Any) -> None:
        try:
            numeric = int(value)
        except Exception:
            return
        if numeric == 0:
            return
        sign = "+" if numeric > 0 else ""
        parts.append(self.t(key, default=default, value=f"{sign}{numeric}"))

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item)]


__all__ = ["STS2SituationSummaryEngine"]
