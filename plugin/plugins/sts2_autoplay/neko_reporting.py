from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Any, Awaitable, Dict, Optional


class NekoReportingMixin:
    def _report_full_step(self, context: Dict[str, Any], *, decision_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        hp_raw = {k: raw_state.get(k) for k in raw_state if "hp" in k.lower() or "health" in k.lower()}
        self.logger.info(f"[sts2_autoplay][hp-debug] raw_state hp fields: {hp_raw}")
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}

        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        hand_summary = [
            {
                "name": str(card.get("name") or card.get("card_name") or ""),
                "playable": bool(card.get("playable")),
                "cost": self._safe_int(card.get("cost")),
            }
            for card in hand
            if isinstance(card, dict)
        ]

        potions = run.get("potions") if isinstance(run.get("potions"), list) else []
        potions_summary = [
            {
                "name": str(potion.get("name") or ""),
                "can_use": bool(potion.get("can_use")),
                "can_discard": bool(potion.get("can_discard")),
            }
            for potion in potions
            if isinstance(potion, dict)
        ]

        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        enemies_summary = []
        for enemy in enemies:
            if not isinstance(enemy, dict):
                continue
            intent = enemy.get("intent") if isinstance(enemy.get("intent"), dict) else {}
            enemies_summary.append({
                "name": str(enemy.get("name") or ""),
                "hp": self._safe_int(enemy.get("hp")),
                "max_hp": self._safe_int(enemy.get("max_hp")),
                "block": self._safe_int(enemy.get("block")),
                "intent": str(intent.get("type") or "") if isinstance(intent, dict) else str(enemy.get("intent") or ""),
                "intent_value": self._safe_int(intent.get("value") if isinstance(intent, dict) else enemy.get("intent_value")),
                "buffs": [
                    {"id": str(b.get("id") or ""), "name": str(b.get("name") or ""), "stacks": self._safe_int(b.get("stacks"))}
                    for b in (enemy.get("buffs") if isinstance(enemy.get("buffs"), list) else [])
                    if isinstance(b, dict)
                ],
                "debuffs": [
                    {"id": str(b.get("id") or ""), "name": str(b.get("name") or ""), "stacks": self._safe_int(b.get("stacks"))}
                    for b in (enemy.get("debuffs") if isinstance(enemy.get("debuffs"), list) else [])
                    if isinstance(b, dict)
                ],
            })

        character_strategy = self._configured_character_strategy()
        strategy_constraints = self._load_strategy_constraints(character_strategy)
        tactical_summary = self._combat_analyzer.build_tactical_summary(combat, lambda s: strategy_constraints, character_strategy)

        llm_reasoning = {}
        if isinstance(decision_result, dict):
            llm_reasoning = {
                "situation_summary": decision_result.get("situation_summary", ""),
                "primary_goal": decision_result.get("primary_goal", ""),
                "candidate_actions": decision_result.get("candidate_actions", []),
                "chosen_action": decision_result.get("chosen_action", ""),
                "reason": decision_result.get("reason", ""),
            }

        return {
            "step": self._step_count,
            "screen": snapshot.get("screen", "unknown"),
            "floor": snapshot.get("floor", 0),
            "act": snapshot.get("act", 0),
            "player_hp": player.get("hp") or run.get("current_hp") or run.get("hp"),
            "max_hp": player.get("max_hp") or run.get("max_hp"),
            "gold": run.get("gold"),
            "in_combat": snapshot.get("in_combat", False),
            "turn": combat.get("turn") or raw_state.get("turn", 1),
            "energy": player.get("energy") if player else combat.get("player_energy"),
            "block": self._combat_player_block(combat),
            "hand": hand_summary,
            "potions": potions_summary,
            "enemies": enemies_summary,
            "tactical_summary": tactical_summary,
            "llm_reasoning": llm_reasoning,
            "decision_source": self._last_action,
            "last_action": self._last_action,
            "current_mode": self._configured_mode(),
            "current_strategy": self._configured_character_strategy(),
            "neko_guidance_injected": self._last_neko_guidance_count > 0,
            "neko_guidance_used": self._last_neko_guidance_used,
            "neko_guidance_used_count": self._last_neko_guidance_count,
            "neko_guidance_pending": len(self._neko_guidance_queue),
        }

    def _drain_neko_guidance(self) -> list[Dict[str, Any]]:
        drained = []
        while self._neko_guidance_queue:
            guidance = self._neko_guidance_queue.popleft()
            drained.append(guidance)
        return drained

    async def _push_neko_report(self, step_result: Dict[str, Any]) -> None:
        notifier = self._frontend_notifier
        if notifier is None:
            return
        report = self._report_full_step(
            {"snapshot": self._snapshot},
            decision_result=self._last_llm_reasoning,
        )
        hand_names = [c.get("name", "?") for c in report.get("hand", []) if isinstance(c, dict)]
        enemy_summaries = []
        for enemy in report.get("enemies", []):
            if isinstance(enemy, dict):
                intent_value = enemy.get("intent_value")
                intent = f"{enemy.get('intent','?')}{intent_value if intent_value not in (None, 0, '') else ''}"
                enemy_summaries.append(f"{enemy.get('name','?')} {enemy.get('hp','?')}/{enemy.get('max_hp','?')} {intent}")
        enemies_str = "; ".join(enemy_summaries) if enemy_summaries else "无"
        llm_reasoning = report.get("llm_reasoning") if isinstance(report.get("llm_reasoning"), dict) else {}
        chosen_action = str(llm_reasoning.get("chosen_action") or report.get("last_action") or "未知动作")
        decision_reason = str(llm_reasoning.get("reason") or "")[:160]
        tactical_summary = report.get("tactical_summary") if isinstance(report.get("tactical_summary"), dict) else {}
        tactical_brief = {
            "atk": tactical_summary.get("incoming_attack_total"),
            "need_block": tactical_summary.get("remaining_block_needed"),
            "lethal": tactical_summary.get("should_prioritize_lethal"),
            "def": tactical_summary.get("should_prioritize_defense"),
            "target": tactical_summary.get("recommended_target_index"),
        }
        task_context = self._semi_auto_task if isinstance(self._semi_auto_task, dict) else None
        task_brief = None
        if isinstance(task_context, dict):
            task_brief = {
                "objective": task_context.get("objective"),
                "stop_condition": task_context.get("stop_condition"),
                "status": task_context.get("status", "running"),
            }
        live_commentary = self._build_neko_live_commentary(
            report=report,
            hand_names=hand_names,
            enemies_str=enemies_str,
            chosen_action=chosen_action,
            decision_reason=decision_reason,
            tactical_brief=tactical_brief,
        )
        commentary_text = str(live_commentary.get("text") or "")
        content = (
            f"尖塔观察#{report['step']} Act{report['act']}F{report['floor']} {report['screen']} "
            f"HP{report['player_hp']}/{report['max_hp']}；AI={chosen_action}；"
            f"因={decision_reason or '无'}；手牌={','.join(hand_names) if hand_names else '无'}；"
            f"敌={enemies_str}；战术={json.dumps(tactical_brief, ensure_ascii=False, separators=(',', ':'))}。"
            f"猫娘实况={commentary_text or '本次保持安静陪伴'}。"
            "规则：过程观察，非完成；仅基于数据短评，可沉默；要干预请调用指导/暂停/恢复/停止入口。"
        )
        description = f"尖塔观察#{report['step']}"
        compact_report = {
            "step": report.get("step"),
            "screen": report.get("screen"),
            "floor": report.get("floor"),
            "act": report.get("act"),
            "hp": [report.get("player_hp"), report.get("max_hp")],
            "energy": report.get("energy"),
            "block": report.get("block"),
            "hand": report.get("hand", []),
            "enemies": report.get("enemies", []),
            "tactical": tactical_brief,
            "chosen_action": chosen_action,
            "reason": decision_reason,
            "mode": report.get("current_mode"),
            "strategy": report.get("current_strategy"),
            "guidance_used": report.get("neko_guidance_used"),
            "guidance_pending": report.get("neko_guidance_pending"),
        }
        metadata = {
            "plugin_id": "sts2_autoplay",
            "event_type": "neko_report",
            "observation_only": True,
            "not_task_completion": True,
            "report": compact_report,
            "neko_context": {
                "rules": "过程观察≠完成；仅按report短评，可沉默；勿编造；干预用sts2_send_neko_guidance，控制用pause/resume/stop。",
                "commentary_allowed": True,
                "commentary_scope": "brief_comment_or_silence_from_report_only",
                "mood": live_commentary.get("mood"),
                "urgency": live_commentary.get("urgency"),
                "commentary_style": live_commentary.get("style"),
                "should_speak": live_commentary.get("should_speak"),
                "task": task_brief,
            },
            "live_commentary": live_commentary,
            "task": task_brief,
        }
        try:
            priority = self._safe_int(live_commentary.get("priority"), 5) if live_commentary.get("should_speak") else 5
            maybe_awaitable = notifier(content=content, description=description, metadata=metadata, priority=priority)
            if isinstance(maybe_awaitable, Awaitable):
                await maybe_awaitable
        except Exception as exc:
            self.logger.warning(f"neko report push failed: {exc}")

    def _build_neko_live_commentary(self, *, report: Dict[str, Any], hand_names: list[str], enemies_str: str, chosen_action: str, decision_reason: str, tactical_brief: Dict[str, Any]) -> Dict[str, Any]:
        enabled = bool(self._cfg.get("neko_commentary_enabled", True))
        hp = self._safe_int(report.get("player_hp"), 0)
        max_hp = max(1, self._safe_int(report.get("max_hp"), 1))
        hp_ratio = hp / max_hp
        incoming_attack = self._safe_int(tactical_brief.get("atk"), 0)
        remaining_block = self._safe_int(tactical_brief.get("need_block"), 0)
        lethal = bool(tactical_brief.get("lethal"))
        should_defend = bool(tactical_brief.get("def"))
        screen = str(report.get("screen") or "unknown")
        in_combat = bool(report.get("in_combat"))
        floor = self._safe_int(report.get("floor"), -1)
        scene = "general"
        mood = "陪伴"
        urgency = "low"
        priority = 5
        interrupt = False
        action_hint = self._humanize_action_for_commentary(chosen_action)
        event_scene = self._detect_neko_event_commentary_scene(report=report, chosen_action=chosen_action)

        if in_combat and hp_ratio <= max(0.0, min(1.0, float(self._cfg.get("neko_desperate_hp_threshold", 0.2) or 0.2))):
            scene = "critical_hp"
            mood = "担心但镇定"
            urgency = "critical"
            priority = 9
            interrupt = True
        elif in_combat and hp_ratio <= max(0.0, min(1.0, float(self._cfg.get("neko_auto_low_hp_threshold", 0.3) or 0.3))):
            scene = "low_hp"
            mood = "关心"
            urgency = "high"
            priority = 8
        elif in_combat and lethal:
            scene = "lethal"
            mood = "兴奋"
            urgency = "high"
            priority = 8
        elif in_combat and incoming_attack > 0 and remaining_block > 0:
            scene = "incoming_attack"
            mood = "提醒"
            urgency = "medium" if incoming_attack < 20 else "high"
            priority = 7 if incoming_attack >= 20 else 6
        elif in_combat and should_defend:
            scene = "defense"
            mood = "谨慎"
            urgency = "medium"
            priority = 6
        elif in_combat:
            scene = "combat"
            mood = "专注"
            urgency = "low"
        elif event_scene:
            scene = event_scene
            if scene == "combat_end":
                mood = "开心"
                priority = 7
            elif scene == "key_relic":
                mood = "认真"
                priority = 7
            elif scene == "route_chosen":
                mood = "从容"
                priority = 6
        elif screen in {"card_reward", "reward", "combat_reward"}:
            scene = "reward"
            mood = "开心"
        elif screen in {"shop", "rest", "event", "map"}:
            scene = screen
            mood = "陪伴"

        style = self._commentary_style_for_strategy()
        hand_text = "、".join(hand_names[:3]) if hand_names else "当前手牌"
        reason_text = f"，理由是{decision_reason}" if decision_reason else ""
        text = self._render_neko_commentary_template(
            scene,
            prefix=style["prefix"],
            suffix=style["suffix"],
            tone=style["tone"],
            hp=hp,
            max_hp=max_hp,
            incoming_attack=incoming_attack,
            remaining_block=remaining_block,
            hand_text=hand_text,
            action_hint=action_hint,
            reason_text=reason_text,
            enemies=enemies_str,
        )

        should_speak = enabled and self._should_emit_neko_commentary(scene=scene, urgency=urgency)
        if should_speak:
            now = time.time()
            self._last_neko_commentary_at = now
            self._last_neko_commentary_scene = scene
            if event_scene:
                self._last_neko_event_scene = event_scene
                self._last_neko_event_floor = floor
        else:
            text = ""
        return {
            "enabled": enabled,
            "should_speak": should_speak,
            "text": text,
            "scene": scene,
            "mood": mood,
            "urgency": urgency,
            "style": f"猫娘实时陪伴短评，简短、温柔、只基于战况数据；当前角色倾向={style['tone']}",
            "tone": style["tone"],
            "character_strategy": self._configured_character_strategy(),
            "priority": priority,
            "tts": should_speak,
            "interrupt": interrupt and should_speak,
            "cooldown_seconds": float(self._cfg.get("neko_commentary_min_interval_seconds", 4) or 4),
            "source": "sts2_autoplay_live_commentary",
            "action_hint": action_hint,
            "enemies": enemies_str,
        }

    def _commentary_style_for_strategy(self) -> Dict[str, str]:
        strategy = self._configured_character_strategy()
        style = self._COMMENTARY_STYLES.get(strategy) or self._COMMENTARY_STYLES[self._DEFAULT_CHARACTER_STRATEGY]
        return {"tone": str(style.get("tone") or "温柔"), "prefix": str(style.get("prefix") or "我看了看"), "suffix": str(style.get("suffix") or "喵")}

    def _render_neko_commentary_template(self, scene: str, **values: Any) -> str:
        templates = self._COMMENTARY_TEMPLATES.get(scene) or self._COMMENTARY_TEMPLATES["general"]
        return random.choice(templates).format(**values)

    def _detect_neko_event_commentary_scene(self, *, report: Dict[str, Any], chosen_action: str) -> Optional[str]:
        screen = str(report.get("screen") or "unknown").lower()
        action = str(chosen_action or "").lower()
        floor = self._safe_int(report.get("floor"), -1)
        previous_scene = self._last_neko_commentary_scene
        combat_scenes = {"combat", "critical_hp", "low_hp", "lethal", "incoming_attack", "defense"}
        signature_floor = floor if floor >= 0 else -1

        if (screen in {"reward", "card_reward", "combat_reward"} or "claim_reward" in action) and previous_scene in combat_scenes:
            if not (self._last_neko_event_scene == "combat_end" and self._last_neko_event_floor == signature_floor):
                return "combat_end"
        if screen in {"treasure", "relic_reward"} or "relic" in action:
            if not (self._last_neko_event_scene == "key_relic" and self._last_neko_event_floor == signature_floor):
                return "key_relic"
        if "choose_map_node" in action or "map" in action or "proceed" in action:
            if not (self._last_neko_event_scene == "route_chosen" and self._last_neko_event_floor == signature_floor):
                return "route_chosen"
        return None

    def _should_emit_neko_commentary(self, *, scene: str, urgency: str) -> bool:
        if not bool(self._cfg.get("neko_commentary_enabled", True)):
            return False
        critical_always = bool(self._cfg.get("neko_critical_commentary_always", True))
        if critical_always and urgency in {"critical", "high"}:
            return True
        now = time.time()
        min_interval = max(0.0, float(self._cfg.get("neko_commentary_min_interval_seconds", 4) or 4))
        if now - self._last_neko_commentary_at < min_interval and scene == self._last_neko_commentary_scene:
            return False
        probability = self._clamp_probability(self._cfg.get("neko_commentary_probability", 0.65))
        return random.random() <= probability

    def _humanize_action_for_commentary(self, action: str) -> str:
        text = str(action or "继续观察")
        lowered = text.lower()
        if "end_turn" in lowered or "end turn" in lowered:
            return "结束回合"
        if "play_card" in lowered:
            return "打出关键牌"
        if "choose" in lowered or "pick" in lowered:
            return "做出选择"
        if "potion" in lowered:
            return "使用药水"
        if "map" in lowered or "proceed" in lowered:
            return "推进路线"
        return text[:40]

    async def _maybe_emit_frontend_message(self, *, event_type: str, snapshot: Optional[Dict[str, Any]] = None, action: Optional[str] = None, detail: str = "", priority: int = 5, force: bool = False) -> None:
        notifier = self._frontend_notifier
        if notifier is None:
            return
        if not bool(self._cfg.get("llm_frontend_output_enabled", False)):
            return
        probability = self._clamp_probability(self._cfg.get("llm_frontend_output_probability", 0.15))
        if not force and probability <= 0.0:
            return
        if not force and random.random() > probability:
            return
        snapshot_data = snapshot if isinstance(snapshot, dict) else {}
        screen = str(snapshot_data.get("screen") or "unknown")
        floor = snapshot_data.get("floor") or 0
        act = snapshot_data.get("act") or 0
        if event_type == "action":
            action_name = str(action or "unknown")
            content = "我刚帮你出了一步啦。"
            description = "我帮你出了一步"
        elif event_type == "error":
            content = "刚刚出牌的时候好像卡了一下，我先停下来等你看一眼。"
            description = "尖塔操作遇到了一点问题"
            action_name = str(action or "")
        else:
            return
        metadata = {
            "plugin_id": "sts2_autoplay",
            "event_type": event_type,
            "action": action_name,
            "screen": screen,
            "floor": floor,
            "act": act,
        }
        try:
            maybe_awaitable = notifier(content=content, description=description, metadata=metadata, priority=priority)
            if isinstance(maybe_awaitable, Awaitable):
                await maybe_awaitable
        except Exception as exc:
            self.logger.warning(f"frontend notification failed: {exc}")

    def _clamp_probability(self, value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception:
            return 0.0

    def _card_name_for_prepared_action(self, prepared: Dict[str, Any]) -> str:
        context = prepared.get("context") if isinstance(prepared.get("context"), dict) else {}
        combat = self._combat_state(context)
        kwargs = prepared.get("kwargs") if isinstance(prepared.get("kwargs"), dict) else {}
        card_index = self._safe_int(kwargs.get("card_index"), -1)
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        for card in hand:
            if not isinstance(card, dict):
                continue
            if self._safe_int(card.get("index"), -2) == card_index:
                return str(card.get("name") or card.get("card_name") or card.get("id") or f"card#{card_index}")
        action = prepared.get("action") if isinstance(prepared.get("action"), dict) else {}
        raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
        return str(action.get("label") or raw.get("label") or raw.get("name") or f"card#{card_index}")

    def _reason_for_card_action(self, reasoning: Optional[Dict[str, Any]], card_name: str) -> str:
        if isinstance(reasoning, dict):
            reason = str(reasoning.get("reason") or reasoning.get("primary_goal") or "").strip()
            if reason:
                return reason
        return f"根据当前玩家状态、手牌、敌人血量和敌人意图，选择打出 {card_name}。"

    def _card_task_report(self, snapshot: Dict[str, Any], *, card_name: str, reason: str, objective: Optional[str]) -> Dict[str, Any]:
        report = self._report_full_step({"snapshot": snapshot}, decision_result=self._last_llm_reasoning)
        report["card_task"] = {
            "objective": objective or "让猫娘推荐一张牌",
            "card_name": card_name,
            "reason": reason,
        }
        return report

    async def _notify_neko_card_task_event(self, event: str, *, objective: Optional[str], snapshot: Dict[str, Any], prepared: Optional[Dict[str, Any]] = None, reasoning: Optional[Dict[str, Any]] = None, card_name: Optional[str] = None, reason: str = "") -> None:
        notifier = self._frontend_notifier
        if notifier is None:
            return
        active_card = card_name or (self._card_name_for_prepared_action(prepared) if isinstance(prepared, dict) else "未知卡牌")
        active_reason = reason or self._reason_for_card_action(reasoning, active_card)
        report = self._card_task_report(snapshot if isinstance(snapshot, dict) else {}, card_name=active_card, reason=active_reason, objective=objective)
        act = report.get("act", 0)
        floor = report.get("floor", 0)
        screen = report.get("screen", "unknown")
        hp = f"{report.get('player_hp')}/{report.get('max_hp')}"
        if event == "recommended":
            content = f"尖塔出牌建议：Act{act}F{floor} {screen} HP{hp}；建议打《{active_card}》；因：{active_reason}。插件不会自动出牌。"
            description = f"尖塔建议打出 {active_card}"
            message_type = "proactive_notification"
            priority = 8
        elif event == "planned":
            content = f"尖塔单卡计划：Act{act}F{floor} {screen} HP{hp}；将打《{active_card}》；因：{active_reason}。插件会立即执行。"
            description = f"尖塔将打出 {active_card}"
            message_type = "proactive_notification"
            priority = 8
        elif event == "completed":
            content = f"尖塔单卡完成：已打《{active_card}》；Act{act}F{floor} {screen} HP{hp}。"
            description = f"尖塔已打出 {active_card}"
            message_type = "neko_observation"
            priority = 6
        elif event == "failed":
            content = f"尖塔单卡未执行：{active_reason or '当前无法选择并打出卡牌'}。"
            description = "尖塔选牌任务未执行"
            message_type = "proactive_notification"
            priority = 7
        else:
            return
        try:
            maybe_awaitable = notifier(
                content=content,
                description=description,
                message_type=message_type,
                metadata={
                    "plugin_id": "sts2_autoplay",
                    "event_type": f"neko_card_task_{event}",
                    "card_name": active_card,
                    "reason": active_reason,
                    "objective": objective,
                    "report": {"act": act, "floor": floor, "screen": screen, "hp": hp, "card_name": active_card, "reason": active_reason},
                    "screen": screen,
                    "floor": floor,
                    "act": act,
                },
                priority=priority,
            )
            if isinstance(maybe_awaitable, Awaitable):
                await maybe_awaitable
        except Exception as exc:
            self.logger.warning(f"neko card task notify failed: {exc}")

    async def _notify_neko_task_event(self, event: str, *, task: Optional[Dict[str, Any]] = None, reason: str = "") -> None:
        notifier = self._frontend_notifier
        if notifier is None:
            return
        active_task = task if isinstance(task, dict) else self._semi_auto_task
        snapshot = self._snapshot if isinstance(self._snapshot, dict) else {}
        floor = snapshot.get("floor", 0)
        act = snapshot.get("act", 0)
        screen = snapshot.get("screen", "unknown")
        objective = active_task.get("objective") if isinstance(active_task, dict) else "帮用户处理当前关卡"
        stop_reason = reason or (active_task.get("stop_reason") if isinstance(active_task, dict) else "") or "用户请求"
        if event == "started":
            content = f"尖塔半自动开始：目标={objective}；Act{act}F{floor} {screen}。过程观察≠完成，只有 completed/stopped/paused 才算状态变化。"
            description = "尖塔半自动任务开始"
            message_type = "neko_observation"
            priority = 6
        elif event == "completed":
            content = f"尖塔半自动完成：目标={objective}；Act{act}F{floor} {screen}。可告知用户本次授权任务已结束。"
            description = "尖塔半自动任务完成"
            message_type = "proactive_notification"
            priority = 8
        elif event == "paused":
            content = f"尖塔半自动已暂停：原因={stop_reason}；目标={objective}；Act{act}F{floor} {screen}。请主程序告知用户，等待继续/停止/新指令。"
            description = "尖塔半自动已暂停"
            message_type = "proactive_notification"
            priority = 8
        elif event == "stopped":
            content = f"尖塔半自动已停止：原因={stop_reason}；目标={objective}；Act{act}F{floor} {screen}。请主程序告知用户本次托管已终止。"
            description = "尖塔半自动已停止"
            message_type = "proactive_notification"
            priority = 8
        elif event == "error":
            content = f"尖塔半自动异常停止：原因={stop_reason}；目标={objective}；Act{act}F{floor} {screen}。请主程序告知用户并等待后续指令。"
            description = "尖塔半自动异常停止"
            message_type = "proactive_notification"
            priority = 9
        else:
            return
        try:
            maybe_awaitable = notifier(
                content=content,
                description=description,
                message_type=message_type,
                metadata={
                    "plugin_id": "sts2_autoplay",
                    "event_type": f"semi_auto_task_{event}",
                    "message_type": message_type,
                    "task": active_task,
                    "reason": stop_reason if event in {"paused", "stopped", "error"} else reason,
                    "screen": screen,
                    "floor": floor,
                    "act": act,
                    "requires_user_attention": event in {"paused", "stopped", "error"},
                },
                priority=priority,
            )
            if isinstance(maybe_awaitable, Awaitable):
                await maybe_awaitable
        except Exception as exc:
            self.logger.warning(f"semi-auto task notify failed: {exc}")

    def _assess_neko_autonomous_action(self, prev_screen: Optional[str]) -> Optional[Dict[str, Any]]:
        snapshot = self._snapshot
        if not snapshot:
            return None
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        screen = snapshot.get("screen") or prev_screen or ""
        floor = self._safe_int(snapshot.get("floor"))
        act = self._safe_int(snapshot.get("act") or 1)
        current_hp = self._safe_int(player.get("hp") or raw_state.get("current_hp") or run.get("current_hp") or run.get("hp"))
        max_hp = self._safe_int(player.get("max_hp") or raw_state.get("max_hp") or run.get("max_hp") or 1)
        if max_hp <= 0:
            max_hp = 1
        hp_ratio = current_hp / max_hp

        low_hp_threshold = max(0.0, min(1.0, float(self._cfg.get("neko_auto_low_hp_threshold", 0.3))))
        if hp_ratio <= low_hp_threshold and self._autoplay_state == "running":
            return {"action": "pause", "reason": "low_hp", "hp_ratio": round(hp_ratio, 2)}

        boss_floors_by_act = {1: 12, 2: 19, 3: 35}
        boss_floor = boss_floors_by_act.get(act, 12)
        is_boss_screen = screen == "combat" and floor >= boss_floor
        was_boss_screen = prev_screen == "combat" and floor >= boss_floor if prev_screen else False
        incoming_attack = self._safe_int(combat.get("enemy_intent", {}).get("value") if isinstance(combat.get("enemy_intent"), dict) else 0)
        if incoming_attack <= 0:
            enemy_intent_value = 0
            for enemy in (combat.get("enemies") if isinstance(combat.get("enemies"), list) else []):
                if isinstance(enemy, dict):
                    intent = enemy.get("intent") if isinstance(enemy.get("intent"), dict) else {}
                    enemy_intent_value = self._safe_int(intent.get("value") if isinstance(intent, dict) else enemy.get("intent_value") or 0)
                    if enemy_intent_value > 0:
                        break
            incoming_attack = enemy_intent_value

        player_block = self._combat_player_block(combat)
        remaining_damage = max(0, incoming_attack - player_block)
        dangerous_attack_threshold = self._safe_int(self._cfg.get("neko_auto_dangerous_attack_threshold", 20))
        if is_boss_screen and not was_boss_screen and self._autoplay_state == "running":
            return {"action": "slow_down", "reason": "boss_combat", "floor": floor}
        if (incoming_attack >= dangerous_attack_threshold and remaining_damage > 0 and screen == "combat" and self._autoplay_state == "running"):
            return {"action": "slow_down", "reason": "dangerous_combat", "incoming_attack": incoming_attack, "remaining_damage": remaining_damage}

        safe_hp_threshold = max(0.0, min(1.0, float(self._cfg.get("neko_auto_safe_hp_threshold", 0.5))))
        resume_after_low_hp = bool(self._cfg.get("neko_auto_resume_after_low_hp", True))
        if resume_after_low_hp and self._autoplay_state == "paused" and self._paused and hp_ratio >= safe_hp_threshold:
            return {"action": "resume", "reason": "hp_recovered", "hp_ratio": round(hp_ratio, 2)}

        return None

    async def _execute_autonomous_action(self, action: Dict[str, Any]) -> None:
        action_type = action.get("action")
        reason = action.get("reason")
        self.logger.info(f"[sts2_autoplay][neko-auto] autonomous action: {action_type} reason={reason}")
        notifier = self._frontend_notifier
        screen = self._snapshot.get("screen", "unknown") if self._snapshot else "unknown"
        floor = self._snapshot.get("floor", 0) if self._snapshot else 0
        act = self._snapshot.get("act", 1) if self._snapshot else 1
        turn = 1
        if self._snapshot:
            raw_state = self._snapshot.get("raw_state") if isinstance(self._snapshot.get("raw_state"), dict) else {}
            run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
            combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
            turn = self._safe_int(combat.get("turn") or raw_state.get("turn") or 1)
            act = self._safe_int(self._snapshot.get("act") or run.get("act") or act)
        reason_labels = {
            "low_hp": "血量过低",
            "boss_combat": "Boss 战斗",
            "dangerous_combat": "危险战斗",
            "hp_recovered": "血量已恢复",
        }
        reason_label = reason_labels.get(str(reason), str(reason or "未知原因"))
        hp_ratio = action.get("hp_ratio")
        hp_text = f"，当前血量约 {round(float(hp_ratio) * 100)}%" if isinstance(hp_ratio, (int, float)) else ""
        messages = {
            "pause": f"尖塔自动运行已暂停：检测到{reason_label}{hp_text}，需要用户确认后再继续。（Act{act} Floor{floor} {screen} 回合{turn}）",
            "slow_down": f"尖塔自动运行已减速：检测到{reason_label}，等待用户关注。（Act{act} Floor{floor} {screen} 回合{turn}）",
            "resume": "尖塔自动运行已自主恢复：血量已回到安全线。",
        }
        if action_type == "pause":
            self._paused = True
            if self._autoplay_state == "running":
                self._autoplay_state = "paused"
            self._emit_status()
        elif action_type == "slow_down":
            original_interval = self._cfg.get("action_interval_seconds", 1.5)
            self._cfg["_neko_auto_saved_action_interval"] = original_interval
            self._cfg["action_interval_seconds"] = max(original_interval, 3.0)
            self.logger.info(f"[sts2_autoplay][neko-auto] slow_down: interval {original_interval} -> 3.0")
        elif action_type == "resume":
            self._paused = False
            self._autoplay_state = "running"
            saved_interval = self._cfg.get("_neko_auto_saved_action_interval")
            if saved_interval is not None:
                self._cfg["action_interval_seconds"] = float(saved_interval)
                self._cfg.pop("_neko_auto_saved_action_interval", None)
                self.logger.info(f"[sts2_autoplay][neko-auto] resume: interval restored to {saved_interval}")
            self._emit_status()
        if notifier is not None and action_type in messages:
            detail = messages[action_type]
            try:
                maybe_awaitable = notifier(
                    content=f"[sts2_autoplay][neko-auto] {detail}",
                    description=f"尖塔自动运行{ {'pause': '已暂停', 'slow_down': '已减速', 'resume': '已恢复'}.get(str(action_type), str(action_type)) }",
                    message_type="proactive_notification",
                    metadata={
                        "plugin_id": "sts2_autoplay",
                        "event_type": "neko_autonomous_action",
                        "message_type": "proactive_notification",
                        "action": action_type,
                        "reason": reason,
                        "reason_label": reason_label,
                        "hp_ratio": hp_ratio,
                        "screen": screen,
                        "floor": floor,
                        "act": act,
                        "turn": turn,
                        "requires_user_attention": action_type in {"pause", "slow_down"},
                    },
                    priority=9 if reason == "low_hp" else 7,
                )
                if isinstance(maybe_awaitable, Awaitable):
                    await maybe_awaitable
            except Exception as exc:
                self.logger.warning(f"neko autonomous action notify failed: {exc}")
