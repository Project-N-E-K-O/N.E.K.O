from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, Optional

from .client import STS2ApiClient, STS2ClientError
from .combat import CombatAnalyzer
from .context import GameContextAnalyzer
from .llm_strategy import LLMStrategy
from .models import normalize_snapshot
from .parser import StrategyParser
from .strategy import HeuristicSelector


class STS2AutoplayService:
    def __init__(self, logger, status_reporter: Callable[[dict[str, Any]], None], frontend_notifier: Optional[Callable[..., Any]] = None) -> None:
        self.logger = logger
        self._report_status = status_reporter
        self._frontend_notifier = frontend_notifier
        self._client: Optional[STS2ApiClient] = None
        self._cfg: Dict[str, Any] = {}
        self._snapshot: Dict[str, Any] = {}
        self._history: Deque[Dict[str, Any]] = deque(maxlen=100)
        self._poll_task: Optional[asyncio.Task] = None
        self._autoplay_task: Optional[asyncio.Task] = None
        self._shutdown = False
        self._paused = False
        self._server_state = "disconnected"
        self._autoplay_state = "disabled"
        self._last_error = ""
        self._last_action = ""
        self._last_poll_at = 0.0
        self._last_action_at = 0.0
        self._consecutive_errors = 0
        self._step_lock = asyncio.Lock()
        self._parser = StrategyParser(logger)
        self._combat_analyzer = CombatAnalyzer(logger)
        self._context_analyzer = GameContextAnalyzer(logger)
        self._heuristic_selector = HeuristicSelector(logger)
        self._llm_strategy = LLMStrategy(logger)
        self._neko_guidance_queue: Deque[Dict[str, Any]] = deque(maxlen=50)
        self._step_count = 0
        self._last_llm_reasoning: Optional[Dict[str, Any]] = None
        self._last_neko_guidance_used = ""
        self._last_neko_guidance_count = 0
        self._semi_auto_task: Optional[Dict[str, Any]] = None
        self._last_task_report_step = -1

    _MODE_ALIASES = {
        "full-program": "full-program",
        "full_program": "full-program",
        "program": "full-program",
        "全程序": "full-program",
        "全程序模式": "full-program",
        "half-program": "half-program",
        "half_program": "half-program",
        "半程序": "half-program",
        "半程序模式": "half-program",
        "full-model": "full-model",
        "full_model": "full-model",
        "model": "full-model",
        "模型": "full-model",
        "全模型": "full-model",
        "全模型模式": "full-model",
    }
    _MODE_LABELS = {
        "full-program": "全程序",
        "half-program": "半程序",
        "full-model": "全模型",
    }

    _DEFAULT_CHARACTER_STRATEGY = "defect"

    @property
    def _strategies_dir(self) -> Path:
        return Path(__file__).with_name("strategies")

    async def startup(self, cfg: Dict[str, Any]) -> None:
        self._cfg = dict(cfg)
        self._cfg["mode"] = self._configured_mode()
        self._cfg["character_strategy"] = self._resolve_startup_character_strategy(self._cfg.get("character_strategy"))
        self._shutdown = False
        self._paused = False
        self._autoplay_state = "idle"
        self._client = STS2ApiClient(
            base_url=str(self._cfg.get("base_url") or "http://127.0.0.1:8080"),
            connect_timeout=float(self._cfg.get("connect_timeout_seconds", 5) or 5),
            request_timeout=float(self._cfg.get("request_timeout_seconds", 15) or 15),
        )
        try:
            await self.health_check()
        except Exception:
            pass
        self._poll_task = asyncio.create_task(self._poll_loop())
        if bool(self._cfg.get("autoplay_on_start", False)):
            await self.start_autoplay()

    async def shutdown(self) -> None:
        self._shutdown = True
        await self.stop_autoplay()
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._poll_task = None
        if self._client is not None:
            try:
                await self._client.close()
            except RuntimeError as exc:
                if "Event loop is closed" not in str(exc):
                    raise
                self.logger.warning("[sts2_autoplay] shutdown skipped client close because event loop is already closed")
            self._client = None
        self._server_state = "disconnected"
        self._emit_status()

    async def health_check(self) -> Dict[str, Any]:
        client = self._require_client()
        data = await client.health()
        self._server_state = "connected"
        self._last_error = ""
        self._emit_status()
        message = f"STS2-Agent 已连接: {client.base_url}"
        return {"status": "connected", "message": message, "summary": message, "health": data}

    async def refresh_state(self) -> Dict[str, Any]:
        context = await self._fetch_step_context(publish=True, record_history=True)
        message = f"已刷新状态，screen={self._snapshot.get('screen')}"
        return {"status": "ok", "message": message, "summary": message, "snapshot": context["snapshot"]}

    async def get_status(self) -> Dict[str, Any]:
        current_mode = self._configured_mode()
        current_character_strategy = self._configured_character_strategy()
        summary = (
            f"尖塔服务={self._server_state}，自动游玩={self._autoplay_state}，"
            f"screen={self._snapshot.get('screen', 'unknown')}，floor={self._snapshot.get('floor', 0)}"
        )
        return {
            "summary": summary,
            "message": summary,
            "server": {"state": self._server_state, "base_url": self._cfg.get("base_url", "http://127.0.0.1:8080")},
            "autoplay": {
                "state": self._autoplay_state,
                "mode": current_mode,
                "mode_label": self._display_mode_name(current_mode),
                "character_strategy": current_character_strategy,
                "strategy": current_mode,
                "strategy_label": self._display_mode_name(current_mode),
                "paused": self._paused,
                "task": self._semi_auto_task,
            },
            "run": {
                "screen": self._snapshot.get("screen", "unknown"),
                "floor": self._snapshot.get("floor", 0),
                "act": self._snapshot.get("act", 0),
                "in_combat": self._snapshot.get("in_combat", False),
                "available_action_count": self._snapshot.get("available_action_count", 0),
            },
            "decision": {"last_action": self._last_action, "last_error": self._last_error},
            "timestamps": {"last_poll_at": self._last_poll_at, "last_action_at": self._last_action_at},
        }

    async def get_snapshot(self) -> Dict[str, Any]:
        if not self._snapshot:
            await self.refresh_state()
        summary = (
            f"当前快照：screen={self._snapshot.get('screen', 'unknown')}，"
            f"floor={self._snapshot.get('floor', 0)}，"
            f"可用动作={self._snapshot.get('available_action_count', 0)}"
        )
        return {"status": "ok", "message": summary, "summary": summary, "snapshot": self._snapshot}

    async def step_once(self) -> Dict[str, Any]:
        async with self._step_lock:
            return await self._step_once_locked()

    async def recommend_one_card_by_neko(self, objective: Optional[str] = None) -> Dict[str, Any]:
        async with self._step_lock:
            context = await self._await_stable_step_context()
            snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
            play_card_actions = []
            for action in (context.get("actions") if isinstance(context.get("actions"), list) else []):
                if not isinstance(action, dict):
                    continue
                raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
                action_type = str(action.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or "")
                if action_type == "play_card":
                    play_card_actions.append(action)
            if not play_card_actions:
                await self._notify_neko_card_task_event(
                    "failed",
                    objective=objective,
                    snapshot=snapshot,
                    reason="当前没有可推荐的出牌动作",
                )
                return {"status": "idle", "message": "当前没有可推荐的出牌动作", "summary": "当前没有可推荐的出牌动作", "snapshot": snapshot}
            card_context = dict(context)
            card_context["actions"] = play_card_actions
            guidance = (
                f"用户只是想要出牌建议，不是授权自动出牌。用户目标：{objective or '请根据当前玩家、手牌和敌人状态推荐最合适的一张牌'}。"
                "本次只能推荐一个 play_card，不要选择结束回合、奖励、地图或其他非出牌动作。"
                "只给建议和理由，禁止执行任何动作；请根据玩家血量/格挡/能量、手牌、敌人血量和意图说明为什么推荐这张牌。"
            )
            action, llm_reasoning = await self._select_action_with_reasoning({**card_context, "neko_guidance": guidance})
            self._last_llm_reasoning = llm_reasoning
            prepared = self._prepare_action_request(action, card_context)
            if prepared.get("action_type") != "play_card":
                return {"status": "idle", "message": "决策结果不是出牌动作，已取消", "summary": "决策结果不是出牌动作，已取消", "snapshot": snapshot}
            card_name = self._card_name_for_prepared_action(prepared)
            reason = self._reason_for_card_action(llm_reasoning, card_name)
            await self._notify_neko_card_task_event(
                "recommended",
                objective=objective,
                snapshot=snapshot,
                prepared=prepared,
                reasoning=llm_reasoning,
                card_name=card_name,
                reason=reason,
            )
            message = f"建议打出 {card_name}。理由：{reason}"
            return {
                "status": "recommended",
                "message": message,
                "summary": message,
                "card_name": card_name,
                "reason": reason,
                "snapshot": snapshot,
                "executed": False,
                "action": prepared,
            }

    async def play_one_card_by_neko(self, objective: Optional[str] = None) -> Dict[str, Any]:
        async with self._step_lock:
            context = await self._await_stable_step_context()
            snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
            play_card_actions = []
            for action in (context.get("actions") if isinstance(context.get("actions"), list) else []):
                if not isinstance(action, dict):
                    continue
                raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
                action_type = str(action.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or "")
                if action_type == "play_card":
                    play_card_actions.append(action)
            if not play_card_actions:
                await self._notify_neko_card_task_event(
                    "failed",
                    objective=objective,
                    snapshot=snapshot,
                    reason="当前没有可打出的卡牌动作",
                )
                return {"status": "idle", "message": "当前没有可打出的卡牌", "summary": "当前没有可打出的卡牌", "snapshot": snapshot}
            card_context = dict(context)
            card_context["actions"] = play_card_actions
            guidance = (
                f"用户明确授权猫娘选择一张卡牌打出。用户目标：{objective or '请根据当前玩家、手牌和敌人状态选择最合适的一张牌并打出'}。"
                "本次只能选择 play_card，不要选择结束回合、奖励、地图或其他非出牌动作。"
                "请根据玩家血量/格挡/能量、手牌、敌人血量和意图说明为什么要打出这张牌。"
            )
            action, llm_reasoning = await self._select_action_with_reasoning({**card_context, "neko_guidance": guidance})
            self._last_llm_reasoning = llm_reasoning
            prepared = self._prepare_action_request(action, card_context)
            if prepared.get("action_type") != "play_card":
                return {"status": "idle", "message": "决策结果不是出牌动作，已取消", "summary": "决策结果不是出牌动作，已取消", "snapshot": snapshot}
            card_name = self._card_name_for_prepared_action(prepared)
            reason = self._reason_for_card_action(llm_reasoning, card_name)
            await self._notify_neko_card_task_event(
                "planned",
                objective=objective,
                snapshot=snapshot,
                prepared=prepared,
                reasoning=llm_reasoning,
                card_name=card_name,
                reason=reason,
            )
            revalidated = await self._revalidate_prepared_action(prepared, card_context)
            if revalidated is None:
                await self._notify_neko_card_task_event(
                    "failed",
                    objective=objective,
                    snapshot=snapshot,
                    prepared=prepared,
                    card_name=card_name,
                    reason="准备执行前局面变化，原卡牌动作已不可用",
                )
                return {"status": "stale", "message": "局面已变化，原卡牌动作不可执行", "summary": "局面已变化，原卡牌动作不可执行", "snapshot": snapshot}
            result = await self._execute_action(prepared)
            await self._await_action_interval()
            settled_context = await self._await_post_action_settle(card_context, prepared)
            self._publish_snapshot(settled_context["snapshot"], record_history=True)
            await self._notify_neko_card_task_event(
                "completed",
                objective=objective,
                snapshot=settled_context["snapshot"],
                prepared=prepared,
                reasoning=llm_reasoning,
                card_name=card_name,
                reason=reason,
            )
            message = f"已按猫娘选择打出 {card_name}。理由：{reason}"
            return {**result, "message": message, "summary": message, "card_name": card_name, "reason": reason, "snapshot": settled_context["snapshot"], "executed": True}

    async def _step_once_locked(self) -> Dict[str, Any]:
        context = await self._await_stable_step_context()
        actions = context["actions"]
        if not actions:
            snapshot = context["snapshot"]
            return {"status": "idle", "message": "当前没有可执行动作", "snapshot": snapshot}
        action, llm_reasoning = await self._select_action_with_reasoning(context)
        self._last_llm_reasoning = llm_reasoning
        prepared = self._prepare_action_request(action, context)
        revalidated = await self._revalidate_prepared_action(prepared, context)
        if revalidated is None:
            context = await self._await_stable_step_context()
            actions = context["actions"]
            if not actions:
                snapshot = context["snapshot"]
                return {"status": "idle", "message": "当前没有可执行动作", "snapshot": snapshot}
            action, llm_reasoning = await self._select_action_with_reasoning(context)
            self._last_llm_reasoning = llm_reasoning
            prepared = self._prepare_action_request(action, context)
        result = await self._execute_action(prepared)
        await self._maybe_emit_frontend_message(
            event_type="action",
            action=prepared.get("action_type"),
            snapshot=context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {},
            detail=result.get("message") or "",
        )
        await self._await_action_interval()
        settled_context = await self._await_post_action_settle(context, prepared)
        self._publish_snapshot(settled_context["snapshot"], record_history=True)
        return {**result, "snapshot": settled_context["snapshot"]}

    def _publish_snapshot(self, snapshot: Dict[str, Any], *, record_history: bool) -> Dict[str, Any]:
        self._snapshot = snapshot
        self._server_state = "connected"
        self._last_error = ""
        self._last_poll_at = time.time()
        if record_history:
            self._history.appendleft({
                "type": "snapshot",
                "time": self._last_poll_at,
                "screen": self._snapshot.get("screen"),
                "available_actions": self._snapshot.get("available_action_count", 0),
            })
        self._emit_status()
        return snapshot

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
        content = (
            f"尖塔观察#{report['step']} Act{report['act']}F{report['floor']} {report['screen']} "
            f"HP{report['player_hp']}/{report['max_hp']}；AI={chosen_action}；"
            f"因={decision_reason or '无'}；手牌={','.join(hand_names) if hand_names else '无'}；"
            f"敌={enemies_str}；战术={json.dumps(tactical_brief, ensure_ascii=False, separators=(',', ':'))}。"
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
                "task": task_brief,
            },
            "task": task_brief,
        }
        try:
            maybe_awaitable = notifier(content=content, description=description, metadata=metadata, priority=5)
            if isinstance(maybe_awaitable, Awaitable):
                await maybe_awaitable
        except Exception as exc:
            self.logger.warning(f"neko report push failed: {exc}")

    async def _fetch_step_context(self, *, publish: bool = False, record_history: bool = False) -> Dict[str, Any]:
        client = self._require_client()
        state_payload = await client.get_state()
        actions_payload = await client.get_available_actions()
        snapshot = normalize_snapshot(state_payload, actions_payload)
        if publish:
            self._publish_snapshot(snapshot, record_history=record_history)
        return {
            "snapshot": snapshot,
            "actions": snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else [],
            "signature": self._snapshot_signature(snapshot),
            "action_signature": self._action_signature(snapshot),
            "state_signature": self._state_signature(snapshot),
            "captured_at": time.time(),
        }

    def _snapshot_signature(self, snapshot: dict[str, Any]) -> tuple[Any, ...]:
        return (
            snapshot.get("screen"),
            snapshot.get("floor"),
            snapshot.get("act"),
            bool(snapshot.get("in_combat", False)),
            snapshot.get("available_action_count", 0),
            self._action_signature(snapshot),
            self._state_signature(snapshot),
        )

    def _action_signature(self, snapshot: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
        actions = snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else []
        return tuple(self._action_fingerprint(action) for action in actions if isinstance(action, dict))

    def _action_fingerprint(self, action: dict[str, Any]) -> tuple[Any, ...]:
        raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
        return (
            str(action.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or ""),
            raw.get("option_index"),
            raw.get("index"),
            raw.get("card_index"),
            raw.get("target_index"),
            raw.get("name"),
        )

    def _state_signature(self, snapshot: dict[str, Any]) -> tuple[Any, ...]:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        potions = run.get("potions") if isinstance(run.get("potions"), list) else []
        hand_signature = tuple(
            (
                card.get("index"),
                card.get("uuid"),
                card.get("id"),
                card.get("name"),
                bool(card.get("playable")),
                tuple(card.get("valid_target_indices")) if isinstance(card.get("valid_target_indices"), list) else (),
            )
            for card in hand
            if isinstance(card, dict)
        )
        potion_signature = tuple(
            (
                potion.get("index"),
                potion.get("id"),
                potion.get("name"),
                bool(potion.get("can_use")),
                bool(potion.get("can_discard")),
            )
            for potion in potions
            if isinstance(potion, dict)
        )
        return (
            raw_state.get("screen"),
            raw_state.get("screen_type"),
            raw_state.get("floor"),
            raw_state.get("act_floor"),
            raw_state.get("act"),
            raw_state.get("turn"),
            raw_state.get("turn_count"),
            raw_state.get("phase"),
            bool(raw_state.get("in_combat", False)),
            combat.get("turn"),
            combat.get("turn_count"),
            combat.get("player_energy"),
            combat.get("end_turn_available"),
            hand_signature,
            potion_signature,
        )

    def _is_actionable_context(self, context: dict[str, Any]) -> bool:
        return bool(context["actions"])

    def _is_transitional_context(self, context: dict[str, Any]) -> bool:
        snapshot = context["snapshot"]
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        screen = self._normalized_screen_name(snapshot)
        in_combat = bool(snapshot.get("in_combat", False) or raw_state.get("in_combat", False))
        if context["actions"]:
            return False
        if in_combat:
            return screen == "combat"
        return self._is_eventish_screen(screen)

    def _normalized_screen_name(self, snapshot: dict[str, Any]) -> str:
        return self._context_analyzer._normalized_screen_name(snapshot)

    def _is_eventish_screen(self, screen: str) -> bool:
        return self._context_analyzer._is_eventish_screen(screen)

    async def _await_stable_step_context(self) -> Dict[str, Any]:
        attempts = max(2, int(self._cfg.get("stable_state_attempts", 4) or 4))
        delay = max(0.1, float(self._cfg.get("poll_interval_active_seconds", 1) or 1) / 2)
        previous: Optional[Dict[str, Any]] = None
        last_context: Optional[Dict[str, Any]] = None
        for attempt in range(attempts):
            context = await self._fetch_step_context(publish=(attempt == 0), record_history=(attempt == 0))
            last_context = context
            if previous is not None and context["signature"] == previous["signature"]:
                return context
            if self._is_actionable_context(context) and not self._is_transitional_context(context):
                return context
            if not self._is_transitional_context(context) and attempt == attempts - 1:
                return context
            previous = context
            if attempt < attempts - 1:
                await asyncio.sleep(delay)
        return last_context or await self._fetch_step_context(publish=True, record_history=True)

    def _prepare_action_request(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
        raw_action = raw.get("action")
        action_type = str(action.get("type") or raw.get("type") or (raw_action if isinstance(raw_action, str) else ""))
        template_raw = dict(raw)
        if action_type in {"choose_reward_card", "select_deck_card"}:
            template_raw.pop("option_index", None)
        kwargs = self._normalize_action_kwargs(action_type, template_raw, context)
        prepared = {
            "action": action,
            "action_type": action_type,
            "kwargs": kwargs,
            "fingerprint": self._action_fingerprint(action),
            "context_signature": context["signature"],
            "context": context,
        }
        self._log_prepared_action(prepared, context)
        return prepared

    def _log_action_decision(self, source: str, action: dict[str, Any], context: dict[str, Any]) -> None:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        screen = snapshot.get("screen") or snapshot.get("normalized_screen") or "unknown"
        self.logger.info(
            f"[sts2_autoplay][decision] source={source} screen={screen} action={self._summarize_action(action, context)} available_actions={self._summarize_actions(context)}"
        )

    def _log_prepared_action(self, prepared: dict[str, Any], context: dict[str, Any]) -> None:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        screen = snapshot.get("screen") or snapshot.get("normalized_screen") or "unknown"
        self.logger.info(
            f"[sts2_autoplay][prepared] screen={screen} prepared={{'action_type': {prepared.get('action_type')!r}, 'kwargs': {prepared.get('kwargs')!r}, 'fingerprint': {prepared.get('fingerprint')!r}}} action={self._summarize_action(prepared.get('action'), context)} available_actions={self._summarize_actions(context)}"
        )

    def _summarize_action(self, action: Any, context: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(action, dict):
            return {}
        raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
        action_type = str(action.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or "")
        return {
            "type": action_type,
            "label": action.get("label") or raw.get("label") or raw.get("description") or raw.get("name") or "",
            "raw": {
                key: value
                for key, value in raw.items()
                if key in {"name", "type", "option_index", "index", "card_index", "target_index", "requires_index", "requires_target"}
            },
            "allowed_kwargs": self._allowed_kwargs_for_action(action_type, raw, context),
        }

    def _summarize_actions(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        actions = context.get("actions") if isinstance(context.get("actions"), list) else []
        return [self._summarize_action(action, context) for action in actions if isinstance(action, dict)]

    async def _revalidate_prepared_action(self, prepared: dict[str, Any], context: dict[str, Any]) -> Optional[dict[str, Any]]:
        actions = context["actions"]
        if not any(self._action_fingerprint(action) == prepared["fingerprint"] for action in actions if isinstance(action, dict)):
            return None
        latest = await self._fetch_step_context()
        if any(self._action_fingerprint(action) == prepared["fingerprint"] for action in latest["actions"] if isinstance(action, dict)):
            return prepared
        return None

    async def _await_action_interval(self) -> None:
        delay = max(0.0, float(self._cfg.get("action_interval_seconds", 0.5) or 0.5))
        if delay > 0:
            await asyncio.sleep(delay)

    async def _await_post_action_settle(self, before_context: dict[str, Any], prepared: dict[str, Any]) -> Dict[str, Any]:
        attempts = max(2, int(self._cfg.get("post_action_settle_attempts", 6) or 6))
        delay = max(0.1, float(self._cfg.get("post_action_delay_seconds", 0.5) or 0.5))
        last_context = before_context
        for attempt in range(attempts):
            if attempt > 0:
                await asyncio.sleep(delay)
            context = await self._fetch_step_context()
            last_context = context
            if context["signature"] != before_context["signature"]:
                if not self._is_transitional_context(context):
                    return context
                continue
            if not any(self._action_fingerprint(action) == prepared["fingerprint"] for action in context["actions"] if isinstance(action, dict)):
                return context
        return last_context

    async def start_autoplay(self, objective: Optional[str] = None, stop_condition: str = "current_floor") -> Dict[str, Any]:
        if objective or bool(self._cfg.get("semi_auto_autoplay", True)):
            self._semi_auto_task = self._build_semi_auto_task(objective=objective, stop_condition=stop_condition)
            await self._notify_neko_task_event("started")
        if self._autoplay_task and not self._autoplay_task.done():
            return {"status": "running", "message": "尖塔半自动任务已在运行", "task": self._semi_auto_task}
        self._paused = False
        self._autoplay_state = "running"
        self._autoplay_task = asyncio.create_task(self._autoplay_loop())
        self._emit_status()
        return {"status": "running", "message": "尖塔半自动任务已启动", "task": self._semi_auto_task}

    async def pause_autoplay(self) -> Dict[str, Any]:
        self._paused = True
        if self._autoplay_state == "running":
            self._autoplay_state = "paused"
        self._emit_status()
        return {"status": "paused", "message": "尖塔已暂停"}

    async def resume_autoplay(self) -> Dict[str, Any]:
        if self._autoplay_task is None or self._autoplay_task.done():
            return await self.start_autoplay()
        self._paused = False
        self._autoplay_state = "running"
        self._emit_status()
        return {"status": "running", "message": "尖塔已恢复"}

    async def stop_autoplay(self) -> Dict[str, Any]:
        self._paused = False
        self._semi_auto_task = None
        if self._autoplay_task is not None:
            self._autoplay_task.cancel()
            try:
                await self._autoplay_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._autoplay_task = None
        self._autoplay_state = "idle"
        self._emit_status()
        return {"status": "idle", "message": "尖塔已停止"}

    async def get_history(self, limit: int = 20) -> Dict[str, Any]:
        limit = max(1, min(100, int(limit or 20)))
        items = list(self._history)[:limit]
        message = f"最近 {len(items)} 条历史"
        return {"status": "ok", "message": message, "summary": message, "history": items}

    async def send_neko_guidance(self, guidance: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(guidance, dict):
            return {"status": "error", "message": "guidance 必须是字典"}
        if not guidance.get("content"):
            return {"status": "error", "message": "guidance.content 不能为空"}
        max_queue = int(self._cfg.get("neko_guidance_max_queue", 50) or 50)
        if len(self._neko_guidance_queue) >= max_queue:
            self._neko_guidance_queue.popleft()
        self._neko_guidance_queue.append({
            "content": str(guidance.get("content", "")),
            "step": int(guidance.get("step", self._step_count)),
            "type": str(guidance.get("type", "soft_guidance")),
            "received_at": time.time(),
        })
        return {"status": "ok", "message": "猫娘指导已入队", "queue_size": len(self._neko_guidance_queue)}

    async def set_mode(self, mode: str) -> Dict[str, Any]:
        normalized_mode = self._normalize_mode_name(mode)
        if normalized_mode not in {"full-program", "half-program", "full-model"}:
            raise RuntimeError(f"暂不支持尖塔模式: {mode}")
        self._cfg["mode"] = normalized_mode
        self._emit_status()
        return {
            "status": "ok",
            "message": f"尖塔模式已切换为 {self._display_mode_name(normalized_mode)}",
            "mode": normalized_mode,
            "mode_label": self._display_mode_name(normalized_mode),
        }

    async def set_character_strategy(self, character_strategy: str) -> Dict[str, Any]:
        normalized_strategy = self._normalize_character_strategy_name(character_strategy)
        self._ensure_character_strategy_exists(normalized_strategy)
        self._cfg["character_strategy"] = normalized_strategy
        self._emit_status()
        return {
            "status": "ok",
            "message": f"角色策略已切换为 {normalized_strategy}",
            "character_strategy": normalized_strategy,
        }

    async def set_speed(self, *, action_interval_seconds: Optional[float] = None, post_action_delay_seconds: Optional[float] = None, poll_interval_active_seconds: Optional[float] = None) -> Dict[str, Any]:
        if action_interval_seconds is not None:
            self._cfg["action_interval_seconds"] = max(0.0, float(action_interval_seconds))
        if post_action_delay_seconds is not None:
            self._cfg["post_action_delay_seconds"] = max(0.0, float(post_action_delay_seconds))
        if poll_interval_active_seconds is not None:
            self._cfg["poll_interval_active_seconds"] = max(0.1, float(poll_interval_active_seconds))
        return {
            "status": "ok",
            "message": "速度设置已更新",
            "action_interval_seconds": self._cfg.get("action_interval_seconds"),
            "post_action_delay_seconds": self._cfg.get("post_action_delay_seconds"),
            "poll_interval_active_seconds": self._cfg.get("poll_interval_active_seconds"),
        }

    def _configured_mode(self) -> str:
        return self._normalize_mode_name(self._cfg.get("mode", self._cfg.get("strategy", "half-program")))

    def _configured_character_strategy(self) -> str:
        return self._normalize_character_strategy_name(self._cfg.get("character_strategy", self._DEFAULT_CHARACTER_STRATEGY))

    def _resolve_startup_character_strategy(self, strategy_name: Any) -> str:
        raw = "" if strategy_name is None else str(strategy_name).strip()
        if not raw:
            default_strategy = self._DEFAULT_CHARACTER_STRATEGY
            self._ensure_character_strategy_exists(default_strategy)
            self.logger.warning(f"[sts2_autoplay] character_strategy 为空，使用安全默认策略: {default_strategy}")
            return default_strategy
        normalized_strategy = self._normalize_character_strategy_name(raw)
        try:
            self._ensure_character_strategy_exists(normalized_strategy)
        except RuntimeError as exc:
            available = ", ".join(self._parser._available_character_strategies()) or "无"
            raise RuntimeError(
                "角色策略配置无效: "
                f"原始值={strategy_name!r}，归一化={normalized_strategy!r}；"
                f"可用策略: {available}；详情: {exc}"
            ) from exc
        return normalized_strategy

    def _normalize_mode_name(self, mode: Any) -> str:
        raw = str(mode or "half-program").strip().lower()
        return self._MODE_ALIASES.get(raw, raw)

    def _display_mode_name(self, mode: Any) -> str:
        normalized = self._normalize_mode_name(mode)
        return self._MODE_LABELS.get(normalized, normalized)

    def _normalize_character_strategy_name(self, strategy_name: Any) -> str:
        return self._parser._normalize_character_strategy_name(strategy_name)

    def _ensure_character_strategy_exists(self, strategy_name: str) -> Path:
        return self._parser._ensure_character_strategy_exists(strategy_name)

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

    async def _poll_loop(self) -> None:
        while not self._shutdown:
            try:
                await self.refresh_state()
                self._consecutive_errors = 0
            except Exception as exc:
                self._consecutive_errors += 1
                self._server_state = "degraded" if self._consecutive_errors < int(self._cfg.get("max_consecutive_errors", 3) or 3) else "disconnected"
                self._last_error = str(exc)
                self._emit_status()
            interval = float(self._cfg.get("poll_interval_active_seconds", 1) if self._autoplay_state == "running" else self._cfg.get("poll_interval_idle_seconds", 3))
            await asyncio.sleep(max(0.1, interval))

    async def _autoplay_loop(self) -> None:
        prev_screen = None
        try:
            while not self._shutdown:
                if self._paused:
                    await asyncio.sleep(0.2)
                    continue
                result = await self.step_once()
                self._step_count += 1
                if result.get("status") == "idle":
                    await asyncio.sleep(max(0.2, float(self._cfg.get("poll_interval_active_seconds", 1) or 1)))
                report_interval = max(1, int(self._cfg.get("neko_report_interval_steps", 1) or 1))
                should_report = self._step_count - self._last_task_report_step >= report_interval
                if bool(self._cfg.get("neko_reporting_enabled", False)) and should_report:
                    await self._push_neko_report(result)
                    self._last_task_report_step = self._step_count
                if self._is_semi_auto_task_complete():
                    await self._complete_semi_auto_task()
                    break
                autonomous = self._assess_neko_autonomous_action(prev_screen)
                if autonomous:
                    await self._execute_autonomous_action(autonomous)
                prev_screen = self._snapshot.get("screen") if self._snapshot else None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._autoplay_state = "error"
            self._last_error = str(exc)
            await self._maybe_emit_frontend_message(event_type="error", detail=str(exc), snapshot=self._snapshot, priority=7, force=True)
            self._emit_status()

    def _build_semi_auto_task(self, *, objective: Optional[str], stop_condition: str) -> Dict[str, Any]:
        snapshot = self._snapshot if isinstance(self._snapshot, dict) else {}
        normalized_stop = str(stop_condition or "current_floor").strip() or "current_floor"
        return {
            "mode": "semi_auto",
            "objective": str(objective or "用户请求猫娘帮忙处理当前关卡").strip(),
            "stop_condition": normalized_stop,
            "started_at": time.time(),
            "start_step": self._step_count,
            "start_screen": snapshot.get("screen"),
            "start_floor": self._safe_int(snapshot.get("floor")),
            "start_act": self._safe_int(snapshot.get("act") or 1),
            "status": "running",
        }

    def _is_semi_auto_task_complete(self) -> bool:
        task = self._semi_auto_task
        snapshot = self._snapshot if isinstance(self._snapshot, dict) else {}
        if not isinstance(task, dict) or not snapshot:
            return False
        stop_condition = str(task.get("stop_condition") or "current_floor")
        start_floor = self._safe_int(task.get("start_floor"))
        current_floor = self._safe_int(snapshot.get("floor"))
        screen = self._normalized_screen_name(snapshot)
        in_combat = bool(snapshot.get("in_combat", False))
        if stop_condition in {"manual", "none"}:
            return False
        if stop_condition in {"combat", "current_combat"}:
            return bool(task.get("start_screen") == "combat" and not in_combat and screen != "combat")
        if current_floor > start_floor:
            return True
        if task.get("start_screen") == "combat" and not in_combat and screen in {"reward", "map", "event", "shop", "rest", "treasure"}:
            return True
        return False

    async def _complete_semi_auto_task(self) -> None:
        task = self._semi_auto_task
        if not isinstance(task, dict):
            return
        task = dict(task)
        task["status"] = "completed"
        task["completed_at"] = time.time()
        task["completed_step"] = self._step_count
        self._semi_auto_task = None
        await self._notify_neko_task_event("completed", task=task)
        self._paused = False
        self._autoplay_state = "idle"
        self._emit_status()

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

    async def _notify_neko_task_event(self, event: str, *, task: Optional[Dict[str, Any]] = None) -> None:
        notifier = self._frontend_notifier
        if notifier is None:
            return
        active_task = task if isinstance(task, dict) else self._semi_auto_task
        snapshot = self._snapshot if isinstance(self._snapshot, dict) else {}
        floor = snapshot.get("floor", 0)
        act = snapshot.get("act", 0)
        screen = snapshot.get("screen", "unknown")
        objective = active_task.get("objective") if isinstance(active_task, dict) else "帮用户处理当前关卡"
        if event == "started":
            content = f"尖塔半自动开始：目标={objective}；Act{act}F{floor} {screen}。过程观察≠完成，只有 completed 才算结束。"
            description = "尖塔半自动任务开始"
            message_type = "neko_observation"
            priority = 6
        elif event == "completed":
            content = f"尖塔半自动完成：目标={objective}；Act{act}F{floor} {screen}。可告知用户本次授权任务已结束。"
            description = "尖塔半自动任务完成"
            message_type = "proactive_notification"
            priority = 8
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
                    "screen": screen,
                    "floor": floor,
                    "act": act,
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
        messages = {
            "pause": f"系统检测到危险（{reason}），已自主暂停以等待指令。（Act{act} Floor{floor} {screen} 回合{turn}）",
            "slow_down": f"系统检测到危险战斗（{reason}），已减速以等待指令。（Act{act} Floor{floor} {screen} 回合{turn}）",
            "resume": "系统已自主恢复运行。",
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
                    description=f"系统自主动作: {action_type}",
                    metadata={"plugin_id": "sts2_autoplay", "event_type": "neko_autonomous_action", "action": action_type, "reason": reason, "screen": screen},
                    priority=7,
                )
                if isinstance(maybe_awaitable, Awaitable):
                    await maybe_awaitable
            except Exception as exc:
                self.logger.warning(f"neko autonomous action notify failed: {exc}")

    def _is_desperate_situation(self, context: dict[str, Any]) -> bool:
        if not bool(self._cfg.get("neko_desperate_enabled", True)):
            return False
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        current_hp = self._safe_int(player.get("hp") or raw_state.get("current_hp") or run.get("current_hp") or run.get("hp"))
        max_hp = self._safe_int(player.get("max_hp") or raw_state.get("max_hp") or run.get("max_hp") or 1)
        if max_hp <= 0:
            max_hp = 1
        hp_ratio = current_hp / max_hp
        desperate_hp_threshold = max(0.0, min(1.0, float(self._cfg.get("neko_desperate_hp_threshold", 0.2))))
        if hp_ratio > desperate_hp_threshold:
            return False
        incoming_attack = 0
        for enemy in (combat.get("enemies") if isinstance(combat.get("enemies"), list) else []):
            if not isinstance(enemy, dict):
                continue
            intent = enemy.get("intent") if isinstance(enemy.get("intent"), dict) else {}
            val = self._safe_int(intent.get("value") if isinstance(intent, dict) else enemy.get("intent_value") or 0)
            if val > incoming_attack:
                incoming_attack = val
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
        strategy_constraints = self._load_strategy_constraints(self._configured_character_strategy())
        attack_cards = []
        for card in playable_cards:
            card_type = str(card.get("card_type") or card.get("type") or "").strip().lower()
            if card_type in {"attack", "skill"}:
                damage = self._combat_analyzer._card_total_damage_value(card, combat, strategy_constraints=strategy_constraints)
                if card_type == "attack" or (card_type == "skill" and damage > 0):
                    attack_cards.append((card, damage))
        attack_cards.sort(key=lambda x: x[1], reverse=True)
        target_index = self._safe_int(combat.get("recommended_target_index"))
        for card, _ in attack_cards:
            valid_targets = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
            if valid_targets:
                resolved_target = None
                if target_index is not None and target_index in [self._safe_int(t) for t in valid_targets]:
                    resolved_target = target_index
                else:
                    resolved_target = self._safe_int(valid_targets[0])
                action = self._action_for_card(play_card_actions, card, resolved_target)
                if action is not None:
                    self.logger.info(f"[sts2_autoplay][desperate] selected attack card={card.get('name')} damage={self._combat_analyzer._card_total_damage_value(card, combat, target_index=resolved_target, strategy_constraints=strategy_constraints)} target={resolved_target}")
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
        if any(k in text_blob for k in {"inflame", "strength_up", "充能", "strength", "力量"}):
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
            for b in buffs:
                if isinstance(b, dict) and str(b.get("id") or "").lower() in {"vulnerable", "易伤"}:
                    enemy_vulnerable = True
            for b in debuffs:
                if isinstance(b, dict) and str(b.get("id") or "").lower() in {"weak", "虚弱", "弱化"}:
                    enemy_weak = True
        if synergy_type == "weaken" and enemy_vulnerable:
            boost += 0.25
        elif synergy_type == "vulnerable" and enemy_weak:
            boost += 0.50
        elif synergy_type == "strength_boost":
            boost += player_str * 0.1
        return boost

    def _calc_setup_synergy(self, setup_card: dict[str, Any], remaining_cards: list[dict[str, Any]], combat: dict[str, Any], active_state: dict[str, Any], strategy_constraints) -> float:
        synergy_type = self._detect_card_synergy_type(setup_card, combat)
        total_synergy = 0.0
        if synergy_type in {"weaken", "vulnerable", "strength_boost", "block_boost"}:
            sim_state = dict(active_state)
            self._apply_setup_effect(setup_card, sim_state, combat)
            for rem_card in remaining_cards:
                rem_type = self._detect_card_synergy_type(rem_card, combat)
                if rem_type == "attack" or (rem_type == "orb_evoke" and "lightning" in str(rem_card.get("name") or "").lower()):
                    boost = self._calc_synergy_boost(rem_card, sim_state, combat, strategy_constraints)
                    dmg = self._combat_analyzer._card_total_damage_value(rem_card, combat, strategy_constraints=strategy_constraints)
                    total_synergy += dmg * boost
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

    def _calc_marginal_benefit(self, card: dict[str, Any], state: dict[str, Any], combat: dict[str, Any], tactical: dict[str, Any], strategy_constraints) -> float:
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
        if synergy_type in {"weaken", "vulnerable", "strength_boost"}:
            benefit = self._calc_setup_synergy(card, [], combat, state, strategy_constraints) * 0.5
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
        strategy_constraints = self._load_strategy_constraints(character_strategy)
        tactical = self._combat_analyzer.build_tactical_summary(combat, lambda s: strategy_constraints, character_strategy)
        remaining_energy = self._safe_int(combat.get("player_energy"), 3)
        active_state: dict[str, Any] = {
            "energy": remaining_energy,
            "str_stacks": 0,
            "weaken_stacks": 0,
            "vulnerable_stacks": 0,
            "block": self._combat_analyzer._combat_player_block(combat),
        }
        remaining = list(playable_cards)
        best_sequence: list[tuple[dict[str, Any], Optional[int]]] = []
        sim_energy = remaining_energy
        while remaining and sim_energy > 0:
            best_card = None
            best_benefit = -999999.0
            best_target = None
            best_idx = -1
            for idx, card in enumerate(remaining):
                benefit = self._calc_marginal_benefit(card, active_state, combat, tactical, strategy_constraints)
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
            if best_card is None or best_benefit < -999990.0:
                break
            action = self._action_for_card(play_card_actions, best_card, best_target)
            if action is None:
                remaining.pop(best_idx)
                continue
            best_sequence.append((best_card, best_target))
            remaining_energy_cost = self._safe_int(best_card.get("cost"), 0)
            sim_energy -= remaining_energy_cost
            active_state["energy"] = sim_energy
            self._apply_setup_effect(best_card, active_state, combat)
            remaining.pop(best_idx)
            if remaining_energy_cost > 0 and sim_energy <= 0:
                break
        if not best_sequence:
            return None
        first_card, first_target = best_sequence[0]
        chosen_action = self._action_for_card(play_card_actions, first_card, first_target)
        self.logger.info(
            f"[sts2_autoplay][maximize] energy={remaining_energy} sequence={[(c[0].get('name'), c[1]) for c in best_sequence]} "
            f"lethal:{bool(tactical.get('should_prioritize_lethal'))} def:{bool(tactical.get('should_prioritize_defense'))} "
            f"incoming:{tactical.get('incoming_attack_total')} block:{active_state.get('block', 0)} "
            f"str:{active_state.get('str_stacks', 0)} weak:{active_state.get('weaken_stacks', 0)} vuln:{active_state.get('vulnerable_stacks', 0)}"
        )
        return chosen_action

    async def _select_action(self, context: dict[str, Any]) -> dict[str, Any]:
        mode = self._configured_mode()
        actions = context.get("actions") if isinstance(context.get("actions"), list) else []
        desperate_action = self._select_desperate_action(actions, context)
        if desperate_action is not None:
            self._log_action_decision("desperate-mode", desperate_action, context)
            return desperate_action
        if bool(self._cfg.get("neko_maximize_enabled", True)):
            maximize_action = self._select_maximize_benefit_action(actions, context)
            if maximize_action is not None:
                self._log_action_decision("maximize-benefit", maximize_action, context)
                return maximize_action
        preemptive_action = self._select_preemptive_program_action(actions, context)
        if preemptive_action is not None:
            self._log_action_decision(f"{mode}-program-preflight", preemptive_action, context)
            return preemptive_action
        if mode == "full-program":
            action = self._select_action_heuristic(actions, context=context)
            self._log_action_decision("heuristic", action, context)
            return action
        if mode == "half-program":
            try:
                action = await self._select_action_with_llm(self._configured_character_strategy(), context)
                if action is not None:
                    self._log_action_decision("half-program-llm", action, context)
                    return action
            except Exception as exc:
                self.logger.warning(f"半程序模式决策失败，回退全程序: {exc}")
            action = self._select_action_heuristic(actions, context=context)
            self._log_action_decision("half-program-heuristic-fallback", action, context)
            return action
        if mode == "full-model":
            try:
                action = await self._select_action_full_model(context)
                if action is not None:
                    self._log_action_decision("full-model", action, context)
                    return action
            except Exception as exc:
                self.logger.warning(f"全模型模式决策失败，回退半程序: {exc}")
            try:
                action = await self._select_action_with_llm(self._configured_character_strategy(), context)
                if action is not None:
                    self._log_action_decision("full-model-half-program-fallback", action, context)
                    return action
            except Exception as exc:
                self.logger.warning(f"全模型回退半程序失败，继续回退全程序: {exc}")
            action = self._select_action_heuristic(actions, context=context)
            self._log_action_decision("full-model-heuristic-fallback", action, context)
            return action
        action = self._select_action_heuristic(actions, context=context)
        self._log_action_decision("heuristic", action, context)
        return action

    async def _select_action_with_reasoning(self, context: dict[str, Any]) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
        mode = self._configured_mode()
        actions = context.get("actions") if isinstance(context.get("actions"), list) else []
        preemptive_action = self._select_preemptive_program_action(actions, context)
        if preemptive_action is not None:
            self._log_action_decision(f"{mode}-program-preflight", preemptive_action, context)
            return preemptive_action, None
        if mode == "full-program":
            action = self._select_action_heuristic(actions, context=context)
            self._log_action_decision("heuristic", action, context)
            return action, None
        guidance_list = self._drain_neko_guidance()
        guidance_text = "\n".join(f"- {g['content']}" for g in guidance_list) if guidance_list else None
        self._last_neko_guidance_used = guidance_text or ""
        self._last_neko_guidance_count = len(guidance_list)
        if guidance_text:
            context["neko_guidance"] = guidance_text
        if mode == "half-program":
            try:
                result = await self._select_action_with_llm_and_reasoning(self._configured_character_strategy(), context, neko_guidance=guidance_text)
                if result is not None:
                    action, reasoning = result
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
                    self._log_action_decision("full-model", action, context)
                    return action, reasoning
            except Exception as exc:
                self.logger.warning(f"全模型模式决策失败，回退半程序: {exc}")
            try:
                result = await self._select_action_with_llm_and_reasoning(self._configured_character_strategy(), context, neko_guidance=guidance_text)
                if result is not None:
                    action, reasoning = result
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
        if strategy_constraints is None:
            strategy_constraints = self._load_strategy_constraints(self._configured_character_strategy())
        return self._combat_analyzer._best_playable_damage_card(combat, target_index=target_index, strategy_constraints=strategy_constraints)

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
        payload = {
            "mode": self._configured_mode(),
            "character_strategy": resolved_strategy,
            "strategy_constraints": self._load_strategy_constraints(resolved_strategy),
            "snapshot": {
                "screen": snapshot.get("screen"),
                "floor": snapshot.get("floor"),
                "act": snapshot.get("act"),
                "in_combat": snapshot.get("in_combat"),
                "character": snapshot.get("character"),
                "turn": combat.get("turn") or raw_state.get("turn"),
                "player_hp": player.get("hp") or run.get("current_hp") or run.get("hp"),
                "max_hp": player.get("max_hp") or run.get("max_hp"),
                "gold": run.get("gold"),
                "energy": player.get("energy"),
            },
            "combat": self._combat_analyzer.sanitize_combat_for_prompt(combat, lambda s: self._load_strategy_constraints(s or resolved_strategy), resolved_strategy),
            "tactical_summary": self._combat_analyzer.build_tactical_summary(combat, lambda s: self._load_strategy_constraints(s or resolved_strategy), resolved_strategy),
            "map_summary": self._context_analyzer._build_map_summary(context),
            "legal_actions": [self._describe_legal_action(action, context) for action in context.get("actions", []) if isinstance(action, dict)],
            "neko_guidance": context.get("neko_guidance"),
        }
        return payload

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
            allowed["option_index"] = [int(potion.get("index", 0)) for potion in self._potions(context) if bool(potion.get("can_discard"))]
        elif action_type == "use_potion":
            allowed["option_index"] = [int(potion.get("index", 0)) for potion in self._potions(context) if bool(potion.get("can_use"))]
        elif action_type == "play_card":
            combat = self._combat_state(context)
            hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
            playable_cards = [card for card in hand if isinstance(card, dict) and bool(card.get("playable"))]
            allowed["card_index"] = [int(card.get("index", 0)) for card in playable_cards]
            target_values = sorted({int(target) for card in playable_cards for target in (card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else [])})
            if target_values:
                allowed["target_index"] = target_values
        elif action_type in {"choose_map_node", "choose_treasure_relic", "choose_event_option", "choose_rest_option", "select_deck_card", "choose_reward_card", "buy_card", "buy_relic", "buy_potion", "claim_reward"}:
            option_indices = [option["index"] for option in self._card_reward_options(raw, context)]
            if not option_indices:
                option_indices = [option["index"] for option in self._character_selection_options(raw, context)]
            if not option_indices:
                option_indices = self._extract_generic_option_indices(raw)
            if option_indices:
                allowed["option_index"] = option_indices
        else:
            allowed["index"] = [0]
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

    def _extract_generic_option_indices(self, raw: dict[str, Any]) -> list[int]:
        indices: list[int] = []
        if bool(raw.get("requires_index")):
            indices.append(0)
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
            if self._action_requires_index(action_type, raw) and not normalized_kwargs:
                fallback_kwargs = self._normalize_action_kwargs(action_type, raw, context)
                normalized_kwargs = {
                    key: int(value)
                    for key, value in fallback_kwargs.items()
                    if key in allowed_kwargs
                }
            validated = dict(action)
            validated_raw = dict(raw)
            reward_option_index = normalized_kwargs.get("option_index") if action_type in {"choose_reward_card", "select_deck_card"} else None
            if reward_option_index is not None:
                validated_raw.pop("option_index", None)
            validated_raw.update(normalized_kwargs)
            if reward_option_index is not None:
                validated_raw.pop("option_index", None)
            validated["raw"] = validated_raw
            return validated
        self.logger.warning(f"LLM 决策动作不在当前合法动作中: {decision}")
        return None

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
                if preferred_option_index is None:
                    preferred_option_index = self._find_preferred_character_option_index(raw, context)
                chosen_option_index = preferred_option_index if preferred_option_index is not None else 0
                if allowed_option_indices and int(chosen_option_index) not in allowed_option_indices:
                    chosen_option_index = allowed_option_indices[0]
                kwargs["option_index"] = chosen_option_index
            elif action_type == "use_potion":
                kwargs["option_index"] = self._find_usable_potion_index(context)
            elif action_type == "play_card":
                kwargs["card_index"] = self._find_playable_card_index(context)
                target_index = self._find_card_target_index(context, kwargs["card_index"])
                if target_index is not None:
                    kwargs["target_index"] = target_index
            else:
                kwargs["index"] = 0
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

    def _require_client(self) -> STS2ApiClient:
        if self._client is None:
            raise RuntimeError("STS2 客户端未初始化")
        return self._client

    def _emit_status(self) -> None:
        try:
            current_mode = self._configured_mode()
            current_character_strategy = self._configured_character_strategy()
            self._report_status({
                "server": {"state": self._server_state, "base_url": self._cfg.get("base_url", "http://127.0.0.1:8080")},
                "autoplay": {
                    "state": self._autoplay_state,
                    "mode": current_mode,
                    "mode_label": self._display_mode_name(current_mode),
                    "character_strategy": current_character_strategy,
                    "strategy": current_mode,
                    "strategy_label": self._display_mode_name(current_mode),
                    "task": self._semi_auto_task,
                },
                "run": {"screen": self._snapshot.get("screen", "unknown"), "floor": self._snapshot.get("floor", 0), "available_action_count": self._snapshot.get("available_action_count", 0)},
                "decision": {"last_action": self._last_action, "last_error": self._last_error},
            })
        except Exception:
            pass
