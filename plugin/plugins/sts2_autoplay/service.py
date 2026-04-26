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

    @property
    def _strategies_dir(self) -> Path:
        return Path(__file__).with_name("strategies")

    async def startup(self, cfg: Dict[str, Any]) -> None:
        self._cfg = dict(cfg)
        self._cfg["mode"] = self._configured_mode()
        self._cfg["character_strategy"] = self._normalize_character_strategy_name(self._cfg.get("character_strategy", "defect"))
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
        return {"status": "connected", "message": f"STS2-Agent 已连接: {client.base_url}", "health": data}

    async def refresh_state(self) -> Dict[str, Any]:
        context = await self._fetch_step_context(publish=True, record_history=True)
        return {"status": "ok", "message": f"已刷新状态，screen={self._snapshot.get('screen')}", "snapshot": context["snapshot"]}

    async def get_status(self) -> Dict[str, Any]:
        current_mode = self._configured_mode()
        current_character_strategy = self._configured_character_strategy()
        return {
            "server": {"state": self._server_state, "base_url": self._cfg.get("base_url", "http://127.0.0.1:8080")},
            "autoplay": {
                "state": self._autoplay_state,
                "mode": current_mode,
                "mode_label": self._display_mode_name(current_mode),
                "character_strategy": current_character_strategy,
                "strategy": current_mode,
                "strategy_label": self._display_mode_name(current_mode),
                "paused": self._paused,
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
        return {"status": "ok", "message": "当前快照", "snapshot": self._snapshot}

    async def step_once(self) -> Dict[str, Any]:
        async with self._step_lock:
            return await self._step_once_locked()

    async def _step_once_locked(self) -> Dict[str, Any]:
        context = await self._await_stable_step_context()
        actions = context["actions"]
        if not actions:
            snapshot = context["snapshot"]
            return {"status": "idle", "message": "当前没有可执行动作", "snapshot": snapshot}
        action = await self._select_action(context)
        prepared = self._prepare_action_request(action, context)
        revalidated = await self._revalidate_prepared_action(prepared, context)
        if revalidated is None:
            context = await self._await_stable_step_context()
            actions = context["actions"]
            if not actions:
                snapshot = context["snapshot"]
                return {"status": "idle", "message": "当前没有可执行动作", "snapshot": snapshot}
            action = await self._select_action(context)
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

    async def start_autoplay(self) -> Dict[str, Any]:
        if self._autoplay_task and not self._autoplay_task.done():
            return {"status": "running", "message": "尖塔已在运行"}
        self._paused = False
        self._autoplay_state = "running"
        self._autoplay_task = asyncio.create_task(self._autoplay_loop())
        self._emit_status()
        return {"status": "running", "message": "尖塔已启动"}

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
        return {"status": "ok", "message": f"最近 {len(items)} 条历史", "history": items}

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
        return self._normalize_character_strategy_name(self._cfg.get("character_strategy", "defect"))

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
        try:
            while not self._shutdown:
                if self._paused:
                    await asyncio.sleep(0.2)
                    continue
                result = await self.step_once()
                if result.get("status") == "idle":
                    await asyncio.sleep(max(0.2, float(self._cfg.get("poll_interval_active_seconds", 1) or 1)))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._autoplay_state = "error"
            self._last_error = str(exc)
            await self._maybe_emit_frontend_message(event_type="error", detail=str(exc), snapshot=self._snapshot, priority=7, force=True)
            self._emit_status()

    async def _select_action(self, context: dict[str, Any]) -> dict[str, Any]:
        mode = self._configured_mode()
        actions = context.get("actions") if isinstance(context.get("actions"), list) else []
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
                "player_hp": raw_state.get("current_hp") or run.get("current_hp") or run.get("hp"),
                "max_hp": raw_state.get("max_hp") or run.get("max_hp"),
                "gold": run.get("gold"),
                "energy": combat.get("player_energy"),
            },
            "combat": self._combat_analyzer.sanitize_combat_for_prompt(combat, lambda s: self._load_strategy_constraints(s or self._configured_character_strategy())),
            "tactical_summary": self._combat_analyzer.build_tactical_summary(combat, lambda s: self._load_strategy_constraints(s or self._configured_character_strategy())),
            "map_summary": self._context_analyzer._build_map_summary(context),
            "legal_actions": [self._describe_legal_action(action, context) for action in context.get("actions", []) if isinstance(action, dict)],
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
        return self._combat_analyzer.build_tactical_summary(combat, lambda s: self._load_strategy_constraints(s or self._configured_character_strategy()), character_strategy)

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

    def _log_card_reward_options(self, options: list[dict[str, Any]], context: dict[str, Any]) -> None:
        try:
            scored_options = []
            for option in options:
                if not isinstance(option, dict):
                    continue
                details = self._score_defect_card_option_details(option, context)
                scored_options.append({
                    "index": option.get("index"),
                    "texts": sorted(option.get("texts")) if isinstance(option.get("texts"), set) else option.get("texts"),
                    "score": details["score"],
                    "constraint_hits": details["constraint_hits"],
                    "base_score": details["base_score"],
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
                },
                "run": {"screen": self._snapshot.get("screen", "unknown"), "floor": self._snapshot.get("floor", 0), "available_action_count": self._snapshot.get("available_action_count", 0)},
                "decision": {"last_action": self._last_action, "last_error": self._last_error},
            })
        except Exception:
            pass
