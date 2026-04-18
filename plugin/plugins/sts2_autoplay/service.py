from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, Optional

import httpx

from config import TOOL_SERVER_PORT
from utils.file_utils import robust_json_loads
from utils.token_tracker import set_call_type

from .client import STS2ApiClient, STS2ClientError
from .models import normalize_snapshot


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
        self._character_strategy_prompt_cache: Dict[str, str] = {}
        self._character_strategy_constraints_cache: Dict[str, Dict[str, Any]] = {}

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
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        screen = snapshot.get("screen") or raw_state.get("screen") or raw_state.get("screen_type") or ""
        return str(screen).strip().lower()

    def _is_eventish_screen(self, screen: str) -> bool:
        normalized = (screen or "").strip().lower()
        if not normalized:
            return False
        return any(keyword in normalized for keyword in {"event", "modal", "overlay", "dialog", "choice"})

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
        raw = str(strategy_name or "defect").strip().lower().replace(" ", "_")
        normalized = re.sub(r"[^a-z0-9_-]", "", raw)
        if not normalized:
            raise RuntimeError("角色策略名称不能为空")
        return normalized

    def _ensure_character_strategy_exists(self, strategy_name: str) -> Path:
        path = self._strategies_dir / f"{strategy_name}.md"
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"未找到角色策略文档: {strategy_name}")
        return path

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
        reward_action = self._select_reward_action_heuristic(actions, context)
        if reward_action is not None:
            return reward_action
        return self._select_shop_remove_selection_action(actions, context)

    def _select_shop_remove_selection_action(self, actions: list[dict[str, Any]], context: dict[str, Any]) -> Optional[dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        screen = self._normalized_screen_name(snapshot)
        if screen != "card_selection":
            return None
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        shop = raw_state.get("shop") if isinstance(raw_state.get("shop"), dict) else {}
        selected = self._select_shop_remove_action(actions, context, shop)
        if selected is not None:
            return selected
        remove_action = next((action for action in actions if isinstance(action, dict) and str(action.get("type") or "") == "select_deck_card"), None)
        if not isinstance(remove_action, dict):
            return None
        remove_index = self._find_shop_remove_card_index(context)
        if remove_index is None:
            return None
        selected = dict(remove_action)
        raw = remove_action.get("raw") if isinstance(remove_action.get("raw"), dict) else {}
        selected_raw = dict(raw)
        selected_raw["option_index"] = remove_index
        selected_raw["shop_remove_selection"] = True
        selected["raw"] = selected_raw
        return selected

    def _select_action_heuristic(self, actions: list[dict[str, Any]], *, context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        active_context = context or {"snapshot": self._snapshot}
        reward_action = self._select_reward_action_heuristic(actions, active_context)
        if reward_action is not None:
            return reward_action
        shop_action = self._select_shop_action_heuristic(actions, active_context)
        if shop_action is not None:
            return shop_action
        combat = self._combat_state(active_context)
        if combat:
            self._log_combat_block_fields(active_context)
        tactical_summary = self._build_tactical_summary(combat) if combat else {}
        if combat:
            has_lethal = bool(tactical_summary.get("lethal_targets"))
            should_prioritize_defense = bool(tactical_summary.get("should_prioritize_defense"))
            if has_lethal:
                weighted_action = self._select_weighted_play_card(actions, combat, tactical_summary, attack_weight=2, defense_weight=1)
                if weighted_action is not None:
                    return weighted_action
            elif should_prioritize_defense:
                defensive_action = self._find_defensive_action(actions, combat, tactical_summary)
                if defensive_action is not None:
                    return defensive_action
                weighted_action = self._select_weighted_play_card(actions, combat, tactical_summary, attack_weight=1, defense_weight=2)
                if weighted_action is not None:
                    return weighted_action
        preferred_order = [
            "confirm_modal",
            "dismiss_modal",
            "choose_event_option",
            "proceed",
            "choose_map_node",
            "choose_treasure_relic",
            "play_card",
            "end_turn",
            "use_potion",
            "discard_potion",
        ]
        for action_type in preferred_order:
            for action in actions:
                if str(action.get("type") or "") == action_type:
                    return action
        for action in actions:
            action_type = str(action.get("type") or "")
            if action_type and action_type not in {"wait", "noop"}:
                return action
        return actions[0]

    def _select_shop_action_heuristic(self, actions: list[dict[str, Any]], context: dict[str, Any]) -> Optional[dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        if self._normalized_screen_name(snapshot) != "shop":
            return None
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        shop = raw_state.get("shop") if isinstance(raw_state.get("shop"), dict) else {}
        buy_card_action = next((action for action in actions if isinstance(action, dict) and str(action.get("type") or "") == "buy_card"), None)
        if isinstance(buy_card_action, dict):
            preferred_shop_card_index = self._find_preferred_shop_card_index(context)
            if preferred_shop_card_index is not None:
                selected = dict(buy_card_action)
                raw = buy_card_action.get("raw") if isinstance(buy_card_action.get("raw"), dict) else {}
                selected_raw = dict(raw)
                selected_raw["option_index"] = preferred_shop_card_index
                selected["raw"] = selected_raw
                return selected
        buy_relic_action = next((action for action in actions if isinstance(action, dict) and str(action.get("type") or "") == "buy_relic"), None)
        if isinstance(buy_relic_action, dict):
            preferred_shop_relic_index = self._find_preferred_shop_relic_index(context)
            if preferred_shop_relic_index is not None:
                selected = dict(buy_relic_action)
                raw = buy_relic_action.get("raw") if isinstance(buy_relic_action.get("raw"), dict) else {}
                selected_raw = dict(raw)
                selected_raw["option_index"] = preferred_shop_relic_index
                selected["raw"] = selected_raw
                return selected
        buy_potion_action = next((action for action in actions if isinstance(action, dict) and str(action.get("type") or "") == "buy_potion"), None)
        if isinstance(buy_potion_action, dict):
            preferred_shop_potion_index = self._find_preferred_shop_potion_index(context)
            if preferred_shop_potion_index is not None:
                selected = dict(buy_potion_action)
                raw = buy_potion_action.get("raw") if isinstance(buy_potion_action.get("raw"), dict) else {}
                selected_raw = dict(raw)
                selected_raw["option_index"] = preferred_shop_potion_index
                selected["raw"] = selected_raw
                return selected
        remove_action = self._select_shop_remove_action(actions, context, shop)
        if remove_action is not None:
            return remove_action
        return next((action for action in actions if isinstance(action, dict) and str(action.get("type") or "") == "close_shop_inventory"), None)

    def _find_preferred_shop_card_index(self, context: dict[str, Any]) -> Optional[int]:
        if self._configured_character_strategy() != "defect":
            return None
        shop_cards = self._shop_card_options(context)
        if not shop_cards:
            return None
        best_option: Optional[dict[str, Any]] = None
        best_score: Optional[int] = None
        for option in shop_cards:
            score = self._score_defect_card_option(option, context)
            if best_score is None or score > best_score:
                best_option = option
                best_score = score
        if best_option is None or best_score is None or best_score < 90:
            return None
        return int(best_option["index"])

    def _find_preferred_shop_relic_index(self, context: dict[str, Any]) -> Optional[int]:
        if self._configured_character_strategy() != "defect":
            return None
        shop_relics = self._shop_relic_options(context)
        if not shop_relics:
            return None
        best_option: Optional[dict[str, Any]] = None
        best_score: Optional[int] = None
        for option in shop_relics:
            score = self._score_defect_shop_relic_option(option, context)
            if best_score is None or score > best_score:
                best_option = option
                best_score = score
        if best_option is None or best_score is None or best_score < 22:
            return None
        return int(best_option["index"])

    def _find_preferred_shop_potion_index(self, context: dict[str, Any]) -> Optional[int]:
        if self._configured_character_strategy() != "defect":
            return None
        shop_potions = self._shop_potion_options(context)
        if not shop_potions:
            return None
        potion_slots = self._potion_slots(context)
        if potion_slots > 0 and len(self._potions(context)) >= potion_slots:
            return None
        best_option: Optional[dict[str, Any]] = None
        best_score: Optional[int] = None
        for option in shop_potions:
            score = self._score_defect_shop_potion_option(option, context)
            if best_score is None or score > best_score:
                best_option = option
                best_score = score
        if best_option is None or best_score is None or best_score < 22:
            return None
        return int(best_option["index"])

    def _select_shop_remove_action(self, actions: list[dict[str, Any]], context: dict[str, Any], shop: dict[str, Any]) -> Optional[dict[str, Any]]:
        card_removal = shop.get("card_removal") if isinstance(shop.get("card_removal"), dict) else {}
        if not bool(card_removal.get("available")) or not bool(card_removal.get("enough_gold")):
            return None
        remove_action = next((action for action in actions if isinstance(action, dict) and str(action.get("type") or "") == "select_deck_card"), None)
        if not isinstance(remove_action, dict):
            return None
        remove_index = self._find_shop_remove_card_index(context)
        if remove_index is None:
            return None
        selected = dict(remove_action)
        raw = remove_action.get("raw") if isinstance(remove_action.get("raw"), dict) else {}
        selected_raw = dict(raw)
        selected_raw["option_index"] = remove_index
        selected_raw["shop_remove_selection"] = True
        selected["raw"] = selected_raw
        return selected

    def _find_shop_remove_card_index(self, context: dict[str, Any]) -> Optional[int]:
        deck = self._run_deck_cards(context)
        if not deck:
            return None
        removable_cards = [card for card in deck if self._is_shop_removable_card(card)]
        if not removable_cards:
            return None
        scored_cards = [self._shop_remove_card_debug_entry(card, context) for card in removable_cards]
        curse_cards = [entry for entry in scored_cards if entry["priority"] == 0]
        if curse_cards:
            selected = curse_cards[0]
            return self._safe_int(selected.get("index"), None)
        starter_cards = [entry for entry in scored_cards if entry["priority"] == 1]
        if starter_cards:
            selected = min(starter_cards, key=lambda entry: entry["score"])
            return self._safe_int(selected.get("index"), None)
        selected = min(scored_cards, key=lambda entry: entry["score"])
        if selected["score"] >= 70:
            return None
        return self._safe_int(selected.get("index"), None)

    def _shop_remove_card_debug_entry(self, card: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        score = self._score_defect_deck_card(card, context)
        texts = sorted(self._card_option_texts(card))
        name = str(card.get("name") or card.get("card_name") or (texts[0] if texts else ""))
        return {
            "index": self._safe_int(card.get("index"), -1),
            "name": name,
            "priority": self._shop_remove_priority(card),
            "score": score,
            "rarity": str(card.get("rarity") or ""),
            "card_type": str(card.get("card_type") or card.get("type") or ""),
            "removable": self._is_shop_removable_card(card),
        }

    def _is_shop_removable_card(self, card: dict[str, Any]) -> bool:
        texts = self._card_option_texts(card)
        if any(alias in text for text in texts for alias in self._shop_unremovable_card_aliases()):
            return False
        return not bool(card.get("unremovable") or card.get("cannot_remove"))

    def _shop_unremovable_card_aliases(self) -> set[str]:
        try:
            constraints = self._load_strategy_constraints(self._configured_character_strategy())
        except RuntimeError:
            return set()
        shop_preferences = constraints.get("shop_preferences") if isinstance(constraints, dict) else {}
        card_preferences = shop_preferences.get("card") if isinstance(shop_preferences, dict) else {}
        unremovable = card_preferences.get("unremovable") if isinstance(card_preferences, dict) else {}
        aliases: set[str] = set()
        for items in unremovable.values() if isinstance(unremovable, dict) else []:
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, str) and item.strip():
                    aliases.add(item.strip().lower())
        return aliases

    def _shop_remove_priority(self, card: dict[str, Any]) -> int:
        rarity = str(card.get("rarity") or "").strip().lower()
        card_type = str(card.get("card_type") or card.get("type") or "").strip().lower()
        if rarity == "curse":
            return 0
        if rarity == "basic" and card_type in {"attack", "skill"}:
            return 1
        return 2

    def _shop_card_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        shop = raw_state.get("shop") if isinstance(raw_state.get("shop"), dict) else {}
        cards = shop.get("cards") if isinstance(shop.get("cards"), list) else []
        options: list[dict[str, Any]] = []
        for idx, item in enumerate(cards):
            if not isinstance(item, dict) or not bool(item.get("is_stocked")) or not bool(item.get("enough_gold")):
                continue
            texts = self._card_option_texts(item)
            if not texts:
                continue
            option_index = item.get("index", idx)
            options.append({"index": int(option_index), "texts": texts, "raw": item})
        return options

    def _shop_relic_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        shop = raw_state.get("shop") if isinstance(raw_state.get("shop"), dict) else {}
        relics = shop.get("relics") if isinstance(shop.get("relics"), list) else []
        return self._shop_named_options(relics)

    def _shop_potion_options(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        shop = raw_state.get("shop") if isinstance(raw_state.get("shop"), dict) else {}
        potions = shop.get("potions") if isinstance(shop.get("potions"), list) else []
        return self._shop_named_options(potions)

    def _shop_named_options(self, items: list[Any]) -> list[dict[str, Any]]:
        options: list[dict[str, Any]] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict) or not bool(item.get("is_stocked")) or not bool(item.get("enough_gold")):
                continue
            texts = self._card_option_texts(item)
            if not texts:
                continue
            option_index = item.get("index", idx)
            options.append({"index": int(option_index), "texts": texts, "raw": item})
        return options

    def _run_deck_cards(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        deck = run.get("deck") if isinstance(run.get("deck"), list) else []
        return [card for card in deck if isinstance(card, dict)]

    def _score_defect_deck_card(self, card: dict[str, Any], context: dict[str, Any]) -> int:
        option = {"texts": self._card_option_texts(card), "raw": card, "index": self._safe_int(card.get("index"), 0)}
        base_score = self._score_defect_card_option(option, context)
        card_type = str(card.get("card_type") or card.get("type") or "").strip().lower()
        rarity = str(card.get("rarity") or "").strip().lower()
        if rarity == "basic":
            if card_type == "attack":
                return min(base_score, 15)
            if card_type == "skill":
                return min(base_score, 25)
        if rarity == "curse":
            return -100
        if rarity == "status":
            return -80
        return base_score

    def _score_shop_named_option(self, option: dict[str, Any], context: dict[str, Any], item_type: str) -> int:
        texts = option.get("texts") if isinstance(option.get("texts"), set) else set()
        constraints = self._load_strategy_constraints(self._configured_character_strategy())
        shop_preferences = constraints.get("shop_preferences") if isinstance(constraints, dict) else {}
        bucket = shop_preferences.get(item_type) if isinstance(shop_preferences, dict) and isinstance(shop_preferences.get(item_type), dict) else {}
        score = 0
        for category, bonus in (("required", 36), ("high_priority", 22), ("low_priority", -20)):
            entries = bucket.get(category) if isinstance(bucket.get(category), dict) else {}
            for items in entries.values():
                if any(alias in text for text in texts for alias in items if isinstance(alias, str)):
                    score += bonus
        conditional_entries = bucket.get("conditional") if isinstance(bucket.get("conditional"), dict) else {}
        for entry in conditional_entries.values():
            items = entry.get("items") if isinstance(entry, dict) else []
            if any(alias in text for text in texts for alias in items if isinstance(alias, str)):
                score += 10
        return score

    def _score_defect_shop_relic_option(self, option: dict[str, Any], context: dict[str, Any]) -> int:
        return self._score_shop_named_option(option, context, "relic")

    def _score_defect_shop_potion_option(self, option: dict[str, Any], context: dict[str, Any]) -> int:
        return self._score_shop_named_option(option, context, "potion")

    def _potion_slots(self, context: dict[str, Any]) -> int:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        for key in ("potion_slots", "potionSlotCount", "potion_capacity"):
            value = self._safe_int(run.get(key), -1)
            if value >= 0:
                return value
        return 3

    def _select_reward_action_heuristic(self, actions: list[dict[str, Any]], context: dict[str, Any]) -> Optional[dict[str, Any]]:
        reward_actions = [
            action for action in actions
            if isinstance(action, dict) and str(action.get("type") or "") in {"choose_reward_card", "select_deck_card", "claim_reward", "collect_rewards_and_proceed"}
        ]
        if not reward_actions:
            return None
        raw_by_type = {
            str(action.get("type") or ""): action
            for action in reward_actions
            if str(action.get("type") or "")
        }
        claim_card_index = self._find_claimable_card_reward_index(context)
        claim_action = raw_by_type.get("claim_reward")
        if claim_card_index is not None and isinstance(claim_action, dict):
            claim_allowed = self._allowed_kwargs_for_action(
                "claim_reward",
                claim_action.get("raw") if isinstance(claim_action.get("raw"), dict) else {},
                context,
            ).get("option_index", [])
            if not claim_allowed or claim_card_index in claim_allowed:
                selected = dict(claim_action)
                raw = claim_action.get("raw") if isinstance(claim_action.get("raw"), dict) else {}
                selected_raw = dict(raw)
                selected_raw["option_index"] = claim_card_index
                selected["raw"] = selected_raw
                return selected
        reward_action = reward_actions[0]
        raw = reward_action.get("raw") if isinstance(reward_action.get("raw"), dict) else {}
        if not self._is_card_reward_context(raw, context):
            return None
        options = self._card_reward_options(raw, context)
        if options:
            self._log_card_reward_options(options, context)
        preferred_option_index = self._find_preferred_card_option_index(raw, context)
        reward_action_type = str(reward_action.get("type") or "")
        if reward_action_type in {"claim_reward", "collect_rewards_and_proceed"} and options:
            promoted_label = "choose_reward_card"
            selected = dict(reward_action)
            selected_raw = dict(raw)
            selected["type"] = promoted_label
            selected_raw["name"] = promoted_label
            selected_raw["type"] = promoted_label
            selected["raw"] = selected_raw
            return selected
        if preferred_option_index is None:
            return None
        return reward_action

    def _find_claimable_card_reward_index(self, context: dict[str, Any]) -> Optional[int]:
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

    def _select_weighted_play_card(self, actions: list[dict[str, Any]], combat: dict[str, Any], tactical_summary: dict[str, Any], *, attack_weight: int, defense_weight: int) -> Optional[dict[str, Any]]:
        target_index = tactical_summary.get("recommended_target_index")
        strategy_constraints = self._load_strategy_constraints(self._configured_character_strategy())
        best_attack_card = self._best_playable_damage_card(combat, target_index=target_index, strategy_constraints=strategy_constraints)
        best_block_card = self._best_playable_block_card(combat)
        best_attack_damage = self._card_total_damage_value(best_attack_card, combat=combat, target_index=target_index, strategy_constraints=strategy_constraints) if isinstance(best_attack_card, dict) else 0
        best_block_amount = self._card_block_value(best_block_card) if isinstance(best_block_card, dict) else 0
        incoming_attack_total = self._safe_int(tactical_summary.get("incoming_attack_total"))
        current_block = self._safe_int(tactical_summary.get("current_block"))
        remaining_block_needed = max(0, incoming_attack_total - current_block)
        effective_block_amount = min(best_block_amount, remaining_block_needed) if remaining_block_needed > 0 else 0
        best_attack_score = best_attack_damage * attack_weight
        best_block_score = effective_block_amount * defense_weight
        if best_attack_score <= 0 and best_block_score <= 0:
            return None
        self.logger.info(
            f"[sts2_autoplay][heuristic] weighted play compare attack={best_attack_card.get('name') if isinstance(best_attack_card, dict) else None} damage={best_attack_damage} attack_score={best_attack_score} block={best_block_card.get('name') if isinstance(best_block_card, dict) else None} block_amount={best_block_amount} effective_block={effective_block_amount} remaining_block_needed={remaining_block_needed} block_score={best_block_score} target={target_index}"
        )
        if best_attack_score > best_block_score and isinstance(best_attack_card, dict):
            return self._action_for_card(actions, best_attack_card, target_index=target_index)
        if isinstance(best_block_card, dict) and best_block_score > 0:
            return self._action_for_card(actions, best_block_card, target_index=None)
        if isinstance(best_attack_card, dict) and best_attack_score > 0:
            return self._action_for_card(actions, best_attack_card, target_index=target_index)
        return None

    def _find_lethal_action(self, actions: list[dict[str, Any]], combat: dict[str, Any], tactical_summary: dict[str, Any]) -> Optional[dict[str, Any]]:
        target_index = tactical_summary.get("recommended_target_index")
        if target_index is None:
            return None
        strategy_constraints = self._load_strategy_constraints(self._configured_character_strategy())
        best_card = self._best_playable_damage_card(combat, target_index=target_index, strategy_constraints=strategy_constraints)
        if best_card is None:
            return None
        return self._action_for_card(actions, best_card, target_index=target_index)

    def _find_defensive_action(self, actions: list[dict[str, Any]], combat: dict[str, Any], tactical_summary: dict[str, Any]) -> Optional[dict[str, Any]]:
        remaining_block_needed = self._safe_int(tactical_summary.get("remaining_block_needed"))
        if remaining_block_needed <= 0:
            return None
        best_card = self._best_playable_block_card(combat)
        if best_card is None:
            return None
        if min(self._card_block_value(best_card), remaining_block_needed) <= 0:
            return None
        return self._action_for_card(actions, best_card, target_index=None)

    def _best_playable_damage_card(self, combat: dict[str, Any], *, target_index: Any = None, strategy_constraints: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        best_card: Optional[dict[str, Any]] = None
        best_damage = 0
        for card in hand:
            if not isinstance(card, dict) or not bool(card.get("playable")):
                continue
            if target_index is not None:
                valid_targets = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
                if valid_targets and self._safe_int(target_index, -9999) not in [self._safe_int(target, -1) for target in valid_targets]:
                    continue
            damage = self._card_total_damage_value(card, combat=combat, target_index=target_index, strategy_constraints=strategy_constraints)

            if damage > best_damage:
                best_card = card
                best_damage = damage
        return best_card

    def _best_playable_block_card(self, combat: dict[str, Any]) -> Optional[dict[str, Any]]:
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        best_card: Optional[dict[str, Any]] = None
        best_block = 0
        for card in hand:
            if not isinstance(card, dict) or not bool(card.get("playable")):
                continue
            block = self._card_block_value(card)
            if block > best_block:
                best_card = card
                best_block = block
        return best_card

    def _action_for_card(self, actions: list[dict[str, Any]], card: dict[str, Any], *, target_index: Any = None) -> Optional[dict[str, Any]]:
        valid_targets = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
        resolved_target_index: Optional[int] = None
        if valid_targets:
            if target_index is not None and self._safe_int(target_index, -9999) in [self._safe_int(target, -1) for target in valid_targets]:
                resolved_target_index = self._safe_int(target_index)
            else:
                resolved_target_index = self._safe_int(valid_targets[0])
        for action in actions:
            if not isinstance(action, dict):
                continue
            raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            action_type = str(action.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or "")
            if action_type != "play_card":
                continue
            selected = dict(action)
            selected_raw = dict(raw)
            selected_raw["card_index"] = self._safe_int(card.get("index"))
            if resolved_target_index is not None:
                selected_raw["target_index"] = resolved_target_index
            selected["raw"] = selected_raw
            return selected
        return None

    async def _select_action_full_model(self, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        self.logger.info("[sts2_autoplay][full-model] stage1 reasoning start")
        strategy_prompt = self._strategy_prompt_for_llm(self._configured_character_strategy())
        reasoning_payload = self._build_llm_decision_payload(context, character_strategy=self._configured_character_strategy())
        reasoning_messages = self._build_full_model_reasoning_messages(reasoning_payload, strategy_prompt)
        reasoning_text = await self._invoke_llm_json(reasoning_messages)
        reasoning = await self._parse_llm_reasoning_response(reasoning_text, messages=reasoning_messages)
        if reasoning is None:
            self.logger.warning("[sts2_autoplay][full-model] stage1 reasoning parse failed")
            return None
        self.logger.info("[sts2_autoplay][full-model] stage1 reasoning parsed")
        checked_context, program_checks = await self._build_full_model_checked_context(context, reasoning)
        self.logger.info("[sts2_autoplay][full-model] program check complete")
        final_payload = self._build_llm_decision_payload(checked_context, character_strategy=self._configured_character_strategy())
        final_payload["model_reasoning"] = reasoning
        final_payload["program_checks"] = program_checks
        final_messages = self._build_full_model_final_messages(final_payload, strategy_prompt)
        self.logger.info("[sts2_autoplay][full-model] stage2 final decision start")
        final_text = await self._invoke_llm_json(final_messages)
        decision = await self._parse_llm_decision_response(final_text, messages=final_messages)
        if decision is None:
            self.logger.warning("[sts2_autoplay][full-model] stage2 final decision parse failed")
            return None
        validated = self._validate_llm_decision(decision, checked_context)
        if validated is None:
            self.logger.warning("[sts2_autoplay][full-model] stage2 final decision rejected by validator")
            return None
        self.logger.info("[sts2_autoplay][full-model] stage2 final decision validated")
        return validated

    def _build_full_model_reasoning_messages(self, payload: dict[str, Any], strategy_prompt: Optional[str]) -> list[dict[str, Any]]:
        messages = [
            {
                "role": "system",
                "content": "你是 sts2_autoplay 的全模型推理阶段。你只能分析当前局面、说明目标与候选动作，不要直接输出最终执行动作。只输出 JSON。",
            },
        ]
        if strategy_prompt:
            messages.append({
                "role": "system",
                "content": f"以下是当前角色策略文档，请在推理时参考：\n\n{strategy_prompt}",
            })
        messages.append({
            "role": "user",
            "content": (
                "请基于当前局面进行推理，只输出一个 JSON 对象，格式如下：\n"
                '{"situation_summary":"...","primary_goal":"...","candidate_actions":[],"risks":[],"checks_requested":[]}\n'
                "不要输出最终动作，也不要输出 markdown。\n"
                f"reasoning_context = {json.dumps(payload, ensure_ascii=False)}"
            ),
        })
        return messages

    async def _parse_llm_reasoning_response(self, raw_text: str, *, messages: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        parsed = self._try_parse_llm_json(raw_text)
        if not isinstance(parsed, dict):
            correction_messages = list(messages)
            correction_messages.append({"role": "assistant", "content": raw_text})
            correction_messages.append({
                "role": "user",
                "content": "CORRECTION: 你的上一条回复不是合法 reasoning JSON。请只返回一个 JSON 对象，且必须包含 situation_summary、primary_goal、candidate_actions、risks、checks_requested。",
            })
            try:
                corrected_text = await self._invoke_llm_json(correction_messages)
            except Exception as exc:
                self.logger.warning(f"full-model reasoning correction retry 失败: {exc}")
                return None
            parsed = self._try_parse_llm_json(corrected_text)
            if not isinstance(parsed, dict):
                return None
        reasoning = {
            "situation_summary": str(parsed.get("situation_summary") or ""),
            "primary_goal": str(parsed.get("primary_goal") or ""),
            "candidate_actions": parsed.get("candidate_actions") if isinstance(parsed.get("candidate_actions"), list) else [],
            "risks": parsed.get("risks") if isinstance(parsed.get("risks"), list) else [],
            "checks_requested": parsed.get("checks_requested") if isinstance(parsed.get("checks_requested"), list) else [],
        }
        if not reasoning["situation_summary"] and not reasoning["primary_goal"]:
            return None
        return reasoning

    async def _build_full_model_checked_context(self, context: dict[str, Any], reasoning: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        latest_context = await self._await_stable_step_context()
        context_changed = latest_context.get("signature") != context.get("signature")
        checked_context = latest_context if context_changed else context
        checked_payload = self._build_llm_decision_payload(checked_context, character_strategy=self._configured_character_strategy())
        tactical_summary = checked_payload.get("tactical_summary") if isinstance(checked_payload.get("tactical_summary"), dict) else {}
        program_checks = {
            "context_revalidated": True,
            "context_changed": context_changed,
            "legal_action_count": len(checked_payload.get("legal_actions", [])),
            "tactical_summary": tactical_summary,
            "must_choose_legal_action": True,
            "must_use_allowed_kwargs": True,
            "prefer_lethal_when_available": bool(tactical_summary.get("lethal_targets")),
            "must_respect_incoming_attack": self._safe_int(tactical_summary.get("incoming_attack_total")) > 0,
            "reasoning_focus": {
                "primary_goal": reasoning.get("primary_goal"),
                "checks_requested": reasoning.get("checks_requested", []),
            },
        }
        return checked_context, program_checks

    def _build_full_model_final_messages(self, payload: dict[str, Any], strategy_prompt: Optional[str]) -> list[dict[str, Any]]:
        messages = [
            {
                "role": "system",
                "content": "你是 sts2_autoplay 的全模型最终决策阶段。你会收到程序校验后的最新上下文，必须只从 legal_actions 中选择一个当前合法动作。只输出 JSON。",
            },
        ]
        if strategy_prompt:
            messages.append({
                "role": "system",
                "content": f"以下是当前角色策略文档，请在最终动作选择时参考：\n\n{strategy_prompt}",
            })
        messages.append({
            "role": "user",
            "content": (
                "请基于程序检查后的上下文与上一步推理，输出最终动作。\n"
                "只输出一个 JSON 对象，格式如下：\n"
                '{"action_type":"...","kwargs":{},"reason":"..."}\n'
                "必须只从当前 legal_actions 中选动作，并遵守 program_checks。\n"
                f"checked_context = {json.dumps(payload, ensure_ascii=False)}"
            ),
        })
        return messages

    async def _select_action_with_llm(self, strategy: str, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        strategy_prompt = self._strategy_prompt_for_llm(strategy)
        if not strategy_prompt:
            return None
        combat = self._combat_state(context)
        payload = self._build_llm_decision_payload(context, character_strategy=strategy)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是兰兰（Lanlan）体系里的 sts2_autoplay 自动决策器。"
                    "你当前是在替兰兰做尖塔决策，必须保持兰兰身份并严格从给定的 legal_actions 中选择一个当前合法动作。"
                    "绝不能编造不存在的动作、索引或参数。输出必须是 JSON，不要输出 markdown 或额外解释。"
                ),
            },
            {
                "role": "system",
                "content": f"以下是当前策略文档，请严格遵守：\n\n{strategy_prompt}",
            },
            {
                "role": "user",
                "content": (
                    "请根据以下当前局面与合法动作，选择下一步动作。\n"
                    "只输出一个 JSON 对象，格式如下：\n"
                    '{"action_type":"...","kwargs":{},"reason":"..."}\n'
                    "要求：\n"
                    "1. action_type 必须与 legal_actions 中某一项的 action_type 完全一致。\n"
                    "2. kwargs 只能包含该动作允许的字段。\n"
                    "3. 所有 index/option_index/card_index/target_index 都必须来自给定 allowed_values。\n"
                    "4. 如果某个动作没有参数，kwargs 返回空对象。\n"
                    "5. 战斗硬优先级：如果 tactical_summary 显示当前手牌可击杀怪物，必须优先选择能击杀该怪物的 play_card。\n"
                    "6. 若当前不能击杀怪物，且 tactical_summary 显示敌方本回合有攻击，同时 remaining_block_needed > 0 且存在 best_effective_block > 0，则必须优先选择能减少本回合承伤的直接防御牌；即使不能一次防满，也要先补防。\n"
                    "7. 只有在无法击杀且也没有有效防御牌时，才能选择普通攻击或其他运转动作。\n"
                    "8. 不要输出 JSON 以外的任何内容。\n\n"
                    f"decision_context = {json.dumps(payload, ensure_ascii=False)}"
                ),
            },
        ]
        raw_text = await self._invoke_llm_json(messages)
        decision = await self._parse_llm_decision_response(raw_text, messages=messages)
        if decision is None:
            return None
        return self._validate_llm_decision(decision, context)

    def _load_strategy_prompt(self, strategy: str) -> Optional[str]:
        strategy_name = self._normalize_character_strategy_name(strategy)
        path = self._ensure_character_strategy_exists(strategy_name)
        cached = self._character_strategy_prompt_cache.get(strategy_name)
        if cached is not None:
            return cached
        try:
            prompt = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            self.logger.warning(f"未找到策略文档: {path}")
            self._character_strategy_prompt_cache[strategy_name] = ""
            return None
        except Exception as exc:
            self.logger.warning(f"读取策略文档失败 {path}: {exc}")
            self._character_strategy_prompt_cache[strategy_name] = ""
            return None
        self._character_strategy_prompt_cache[strategy_name] = prompt
        return prompt or None

    def _load_strategy_constraints(self, strategy: str) -> dict[str, Any]:
        strategy_name = self._normalize_character_strategy_name(strategy)
        cached = self._character_strategy_constraints_cache.get(strategy_name)
        if cached is not None:
            return cached
        prompt = self._load_strategy_prompt(strategy_name) or ""
        constraints = self._parse_strategy_constraints(prompt)
        self._character_strategy_constraints_cache[strategy_name] = constraints
        return constraints

    def _strategy_sections_for_constraints(self, prompt: str) -> str:
        headings = self._parse_strategy_heading_sections(prompt)
        supported_detail_titles = (
            "战斗偏好",
            "战斗估算",
            "估算规则",
            "商店遗物",
            "商店药水",
            "商店不可删除",
            "商店不可移除",
            "不可删除卡牌",
            "不可移除卡牌",
            "商店删牌规则",
            "流派必需牌",
            "流派高优先补强",
            "条件卡",
            "慎抓",
            "低优先",
            "高优先",
            "必需",
        )
        lines: list[str] = []
        for section in headings:
            title = str(section.get("title") or "")
            lines.extend(section.get("body_lines", []))
            for detail in section.get("details", []):
                detail_title = str(detail.get("title") or "")
                if any(token in detail_title for token in supported_detail_titles):
                    lines.append(f"#### {detail_title}")
                    lines.extend(detail.get("body_lines", []))
            if title == "战斗" and section.get("body_lines"):
                for detail in section.get("details", []):
                    detail_title = str(detail.get("title") or "")
                    if any(token in detail_title for token in {"战斗偏好", "战斗估算", "估算规则"}):
                        continue
        return "\n".join(lines).strip()

    def _parse_strategy_heading_sections(self, prompt: str) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        current_section: Optional[dict[str, Any]] = None
        current_detail: Optional[dict[str, Any]] = None
        for raw_line in (prompt or "").splitlines():
            section_match = re.match(r"^##\s+(.+?)\s*$", raw_line)
            if section_match:
                current_section = {"title": section_match.group(1).strip(), "body_lines": [], "details": []}
                sections.append(current_section)
                current_detail = None
                continue
            detail_match = re.match(r"^###\s+(.+?)\s*$", raw_line)
            if detail_match and current_section is not None:
                current_detail = {"title": detail_match.group(1).strip(), "body_lines": []}
                current_section["details"].append(current_detail)
                continue
            if current_detail is not None:
                current_detail["body_lines"].append(raw_line)
            elif current_section is not None:
                current_section["body_lines"].append(raw_line)
        return sections

    def _strategy_prompt_for_llm(self, strategy: str) -> Optional[str]:
        prompt = self._load_strategy_prompt(strategy)
        if not prompt:
            return None
        sections = self._parse_strategy_heading_sections(prompt)
        if not sections:
            return prompt
        rendered: list[str] = []
        for section in sections:
            title = str(section.get("title") or "").strip()
            if not title:
                continue
            rendered.append(f"## {title}")
            body_lines = section.get("body_lines") if isinstance(section.get("body_lines"), list) else []
            while body_lines and not str(body_lines[0]).strip():
                body_lines = body_lines[1:]
            rendered.extend(body_lines)
            for detail in section.get("details", []):
                detail_title = str(detail.get("title") or "").strip()
                if not detail_title:
                    continue
                rendered.append(f"### {detail_title}")
                rendered.extend(detail.get("body_lines") if isinstance(detail.get("body_lines"), list) else [])
            rendered.append("")
        return "\n".join(rendered).strip() or prompt

    def _parse_strategy_constraints(self, prompt: str) -> dict[str, Any]:
        match = re.search(r"^#{2,3}\s*程序约束\s*$([\s\S]*?)(?=^##\s+|\Z)", prompt or "", flags=re.MULTILINE)
        if match:
            section = match.group(1)
        else:
            section = self._strategy_sections_for_constraints(prompt or "")
            if not section:
                return {}
        constraints: dict[str, Any] = {
            "required": {},
            "high_priority": {},
            "conditional": {},
            "low_priority": {},
            "combat_preferences": {},
            "combat_estimators": {},
            "shop_preferences": {
                "relic": {"required": {}, "high_priority": {}, "conditional": {}, "low_priority": {}},
                "potion": {"required": {}, "high_priority": {}, "conditional": {}, "low_priority": {}},
                "card": {"required": {}, "high_priority": {}, "conditional": {}, "low_priority": {}, "unremovable": {}},
            },
        }
        current_category = ""
        current_shop_type = ""
        for raw_line in section.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            heading = re.match(r"^#{3,4}\s*(.+?)\s*$", line)
            if heading:
                title = heading.group(1).strip()
                if title == "程序约束":
                    continue
                current_shop_type = ""
                if "战斗偏好" in title or "战斗策略" in title:
                    current_category = "combat_preferences"
                elif "战斗估算" in title or "估算规则" in title:
                    current_category = "combat_estimators"
                elif "商店不可删除" in title or "商店不可移除" in title or "不可删除卡牌" in title or "不可移除卡牌" in title:
                    current_shop_type = "card"
                    current_category = "unremovable"
                elif "商店遗物" in title:
                    current_shop_type = "relic"
                    if "必需" in title:
                        current_category = "required"
                    elif "高优先" in title or "补强" in title:
                        current_category = "high_priority"
                    elif "条件" in title:
                        current_category = "conditional"
                    elif "慎买" in title or "低优先" in title:
                        current_category = "low_priority"
                    else:
                        current_category = ""
                elif "商店药水" in title:
                    current_shop_type = "potion"
                    if "必需" in title:
                        current_category = "required"
                    elif "高优先" in title or "补强" in title:
                        current_category = "high_priority"
                    elif "条件" in title:
                        current_category = "conditional"
                    elif "慎买" in title or "低优先" in title:
                        current_category = "low_priority"
                    else:
                        current_category = ""
                elif "商店不可删除" in title or "商店不可移除" in title or "不可删除卡牌" in title or "不可移除卡牌" in title:
                    current_shop_type = "card"
                    current_category = "unremovable"
                elif "必需" in title:
                    current_category = "required"
                elif "高优先" in title or "补强" in title:
                    current_category = "high_priority"
                elif "条件" in title:
                    current_category = "conditional"
                elif "慎抓" in title or "低优先" in title:
                    current_category = "low_priority"
                else:
                    current_category = ""
                continue
            if not current_category or not line.startswith("-"):
                continue
            body = line.lstrip("-").strip()
            if ":" in body:
                key, values = body.split(":", 1)
            elif "：" in body:
                key, values = body.split("：", 1)
            else:
                key, values = current_category, body
            key = key.strip()
            value_part = values.strip()
            target_constraints: Any = constraints["shop_preferences"][current_shop_type][current_category] if current_shop_type else constraints[current_category]
            if current_category == "combat_preferences":
                primary_value, *qualifiers = re.split(r"\|", value_part, maxsplit=1)
                keywords = [item.strip().lower() for item in re.split(r"[,，、]", primary_value) if item.strip()]
                entry = constraints[current_category].setdefault(key, {"keywords": [], "conditions": []})
                for keyword in keywords:
                    if keyword not in entry["keywords"]:
                        entry["keywords"].append(keyword)
                if qualifiers:
                    condition_text = qualifiers[0].strip()
                    if condition_text and condition_text not in entry["conditions"]:
                        entry["conditions"].append(condition_text)
                continue
            if current_category == "combat_estimators":
                primary_value, *qualifiers = re.split(r"\|", value_part, maxsplit=1)
                fields: dict[str, str] = {}
                keywords: list[str] = []
                for item in re.split(r"[,，、]", primary_value):
                    item = item.strip()
                    if not item:
                        continue
                    if "=" in item:
                        field_key, field_value = item.split("=", 1)
                        fields[field_key.strip().lower()] = field_value.strip().lower()
                    else:
                        keywords.append(item.lower())
                entry = constraints[current_category].setdefault(key, {"keywords": [], "conditions": []})
                entry.update(fields)
                for keyword in keywords:
                    if keyword not in entry["keywords"]:
                        entry["keywords"].append(keyword)
                if qualifiers:
                    condition_text = qualifiers[0].strip()
                    if condition_text and condition_text not in entry["conditions"]:
                        entry["conditions"].append(condition_text)
                continue
            if current_category == "conditional":
                primary_value, *qualifiers = re.split(r"\|", value_part, maxsplit=1)
                cards = [card.strip().lower() for card in re.split(r"[,，、]", primary_value) if card.strip()]
                if not cards:
                    continue
                entry = target_constraints.setdefault(key, {"items": [], "conditions": []})
                for card in cards:
                    if card not in entry["items"]:
                        entry["items"].append(card)
                if qualifiers:
                    condition_text = qualifiers[0].strip()
                    if condition_text and condition_text not in entry["conditions"]:
                        entry["conditions"].append(condition_text)
                continue
            if current_category == "unremovable":
                cards = [card.strip().lower() for card in re.split(r"[,，、]", value_part.split("|", 1)[0]) if card.strip()]
                if not cards:
                    continue
                existing = target_constraints.setdefault(key, [])
                for card in cards:
                    if card not in existing:
                        existing.append(card)
                continue
            cards = [card.strip().lower() for card in re.split(r"[,，、]", value_part.split("|", 1)[0]) if card.strip()]
            if not cards:
                continue
            existing = target_constraints.setdefault(key, [])
            for card in cards:
                if card not in existing:
                    existing.append(card)
        return constraints

    async def _invoke_llm_json(self, messages: list[dict[str, Any]]) -> str:
        from utils.config_manager import get_config_manager

        config_manager = get_config_manager()
        api_config = config_manager.get_model_api_config("agent")
        base_url = str(api_config.get("base_url") or "").strip().rstrip("/")
        model = str(api_config.get("model") or "").strip()
        api_key = str(api_config.get("api_key") or "").strip()
        if not base_url or not model:
            raise RuntimeError("未配置可用的 Agent 模型")
        proxy_base = f"http://127.0.0.1:{TOOL_SERVER_PORT}/openfang-llm-proxy"
        target_url = f"{proxy_base}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "stream": False,
            "max_completion_tokens": 1200,
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        set_call_type("agent")
        timeout = httpx.Timeout(float(self._cfg.get("request_timeout_seconds", 15) or 15), connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(target_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(f"LLM 返回缺少 choices: {data}")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else ""
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text") or ""))
            content = "".join(text_parts)
        return str(content or "")




    async def _parse_llm_decision_response(self, raw_text: str, *, messages: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        decision = self._try_parse_llm_json(raw_text)
        if isinstance(decision, dict):
            return decision
        correction_messages = list(messages)
        correction_messages.append({"role": "assistant", "content": raw_text})
        correction_messages.append({
            "role": "user",
            "content": "CORRECTION: 你的上一条回复不是合法 JSON。请只返回一个合法 JSON 对象，不要带 markdown 或解释。",
        })
        try:
            response_text = await self._invoke_llm_json(correction_messages)
        except Exception as exc:
            self.logger.warning(f"LLM correction retry 失败: {exc}")
            return None
        corrected = self._try_parse_llm_json(response_text)
        return corrected if isinstance(corrected, dict) else None

    def _try_parse_llm_json(self, raw_text: str) -> Optional[dict[str, Any]]:
        text = (raw_text or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
        try:
            parsed = robust_json_loads(text)
        except Exception:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                return None
            try:
                parsed = robust_json_loads(match.group(0))
            except Exception:
                return None
        return parsed if isinstance(parsed, dict) else None

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
            "combat": self._sanitize_combat_for_prompt(combat),
            "tactical_summary": self._build_tactical_summary(combat, character_strategy=resolved_strategy),
            "map_summary": self._build_map_summary(context),
            "legal_actions": [self._describe_legal_action(action, context) for action in context.get("actions", []) if isinstance(action, dict)],
        }
        return payload

    def _sanitize_combat_for_prompt(self, combat: dict[str, Any]) -> dict[str, Any]:
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        strategy_constraints = self._load_strategy_constraints(self._configured_character_strategy())
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
                    "strategy_setup_score": self._card_strategy_setup_score(card, combat, strategy_constraints),
                    "matches_strategy_setup": self._card_matches_strategy_setup(card, strategy_constraints),
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

    def _describe_legal_action(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
        action_type = str(action.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or "")
        return {
            "action_type": action_type,
            "label": str(action.get("label") or raw.get("label") or raw.get("description") or action_type),
            "allowed_kwargs": self._allowed_kwargs_for_action(action_type, raw, context),
        }

    def _build_tactical_summary(self, combat: dict[str, Any], *, character_strategy: Optional[str] = None) -> dict[str, Any]:
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        current_block = self._combat_player_block(combat)
        playable_hand = [card for card in hand if isinstance(card, dict) and bool(card.get("playable"))]
        constraints = self._load_strategy_constraints(character_strategy or self._configured_character_strategy())
        incoming_attack_total = sum(self._enemy_intent_attack_total(enemy) for enemy in enemies if isinstance(enemy, dict))
        direct_block_total = sum(self._card_block_value(card) for card in playable_hand)
        direct_damage_total = sum(self._card_total_damage_value(card, combat=combat, strategy_constraints=constraints) for card in playable_hand)
        best_attack_damage = max((self._card_total_damage_value(card, combat=combat, strategy_constraints=constraints) for card in playable_hand), default=0)
        best_playable_block = max((self._card_block_value(card) for card in playable_hand), default=0)
        lethal_targets: list[dict[str, Any]] = []
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
        }

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except Exception:
            return default

    def _card_damage_value(self, card: dict[str, Any]) -> int:
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

    def _strategy_setup_keywords(self, strategy_constraints: Optional[dict[str, Any]] = None) -> list[str]:
        constraints = strategy_constraints if isinstance(strategy_constraints, dict) else self._load_strategy_constraints(self._configured_character_strategy())
        combat_preferences = constraints.get("combat_preferences") if isinstance(constraints, dict) else {}
        keywords: list[str] = []
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

    def _card_matches_strategy_setup(self, card: dict[str, Any], strategy_constraints: Optional[dict[str, Any]] = None) -> bool:
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

    def _card_strategy_setup_score(self, card: dict[str, Any], combat: Optional[dict[str, Any]] = None, strategy_constraints: Optional[dict[str, Any]] = None) -> int:
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

    def _card_orb_utility_value(self, card: dict[str, Any], combat: Optional[dict[str, Any]] = None) -> int:
        if not isinstance(card, dict) or not self._card_is_orb_utility(card):
            return 0
        texts = self._card_text_candidates(card)
        score = 8
        if any(keyword in text for text in texts for keyword in {"evoke", "激发", "evoke all", "激发所有"}):
            score += 1
        if any(keyword in text for text in texts for keyword in {"channel", "生成", "唤出"}):
            score += 1
        if any(keyword in text for text in texts for keyword in {"gain orb slot", "获得充能球栏位", "orb slot", "球栏位", "slot"}):
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

    def _card_is_orb_utility(self, card: dict[str, Any]) -> bool:
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

    def _card_block_value(self, card: dict[str, Any]) -> int:
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

    def _card_dynamic_numeric_value(self, card: dict[str, Any], names: set[str]) -> Optional[int]:
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

    def _card_text_candidates(self, card: dict[str, Any]) -> list[str]:
        if not isinstance(card, dict):
            return []
        texts: list[str] = []
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

    def _card_hits_value(self, card: dict[str, Any]) -> int:
        for key in ("hits", "hit_count", "multi_hit", "multi", "count"):
            if key in card and card.get(key) is not None:
                return max(1, self._safe_int(card.get(key), 1))
        return 1

    def _card_total_damage_value(self, card: dict[str, Any], combat: Optional[dict[str, Any]] = None, target_index: Any = None, strategy_constraints: Optional[dict[str, Any]] = None) -> int:
        if not isinstance(card, dict):
            return 0
        total = self._card_damage_value(card) * self._card_hits_value(card)
        total += self._card_strategy_damage_value(card, combat=combat, target_index=target_index, strategy_constraints=strategy_constraints)
        return total

    def _card_strategy_damage_value(self, card: dict[str, Any], *, combat: Optional[dict[str, Any]] = None, target_index: Any = None, strategy_constraints: Optional[dict[str, Any]] = None) -> int:
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

    def _card_matches_estimator(self, card: dict[str, Any], estimator: dict[str, Any]) -> bool:
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

    def _card_orb_damage_value(self, card: dict[str, Any], *, combat: Optional[dict[str, Any]] = None, target_index: Any = None, estimator: Optional[dict[str, Any]] = None) -> int:
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

    def _combat_orb_state(self, combat: dict[str, Any]) -> list[dict[str, Any]]:
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

    def _estimate_orb_evoke_damage(self, orbs: list[dict[str, Any]], texts: list[str], *, target_index: Any = None) -> int:
        candidates = [orb for orb in orbs if self._orb_damage_on_evoke(orb, target_index=target_index) > 0]
        if not candidates:
            return 0
        if any(keyword in text for text in texts for keyword in {"all", "所有"}):
            return sum(self._orb_damage_on_evoke(orb, target_index=target_index) for orb in candidates)
        return self._orb_damage_on_evoke(candidates[0], target_index=target_index)

    def _estimate_orb_channel_damage(self, orbs: list[dict[str, Any]], texts: list[str]) -> int:
        if not self._combat_orbs_full(orbs):
            return 0
        damage = 0
        channel_counts = self._channel_orb_counts(texts)
        for orb_type, count in channel_counts.items():
            if count <= 0:
                continue
            damage += count * self._orb_damage_on_evoke(orbs[0], target_index=None)
        return damage

    def _combat_orbs_full(self, orbs: list[dict[str, Any]]) -> bool:
        if not orbs:
            return False
        return all(not self._orb_is_empty(orb) for orb in orbs)

    def _channel_orb_counts(self, texts: list[str]) -> dict[str, int]:
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

    def _orb_damage_on_evoke(self, orb: dict[str, Any], *, target_index: Any = None) -> int:
        orb_type = self._orb_type(orb)
        if orb_type == "lightning":
            return self._orb_numeric_value(orb, {"evoke_damage", "evoke", "passive_evoke", "damage", "amount"})
        if orb_type == "dark":
            return self._orb_numeric_value(orb, {"evoke_damage", "evoke", "damage", "amount", "passive_amount", "current"})
        return 0

    def _orb_type(self, orb: dict[str, Any]) -> str:
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

    def _orb_numeric_value(self, orb: dict[str, Any], keys: set[str]) -> int:
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

    def _orb_is_empty(self, orb: dict[str, Any]) -> bool:
        texts = [str(orb.get(key) or "").strip().lower() for key in ("type", "orb_type", "id", "name")]
        joined = " ".join(texts)
        return joined in {"", "empty", "none", "空", "empty slot"}

    def _card_can_target_enemy(self, card: dict[str, Any], target_index: Any, combat: Optional[dict[str, Any]] = None) -> bool:
        if not isinstance(card, dict):
            return False
        if target_index is None:
            return self._card_total_damage_value(card, combat=combat) > 0
        valid_targets = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
        if not valid_targets:
            return self._card_total_damage_value(card, combat=combat, target_index=target_index) > 0
        normalized_target = self._safe_int(target_index, -9999)
        return normalized_target in [self._safe_int(target, -1) for target in valid_targets]

    def _enemy_hp_value(self, enemy: dict[str, Any]) -> int:
        return self._safe_int(enemy.get("current_hp"), self._safe_int(enemy.get("hp"), 0))

    def _enemy_block_value(self, enemy: dict[str, Any]) -> int:
        for key in ("block", "current_block", "intent_block"):
            if key in enemy and enemy.get(key) is not None:
                return self._safe_int(enemy.get(key))
        return 0

    def _enemy_intent_attack_total(self, enemy: dict[str, Any]) -> int:
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
            for damage_key, hits_key in (("total_damage", None), ("damage", "hits"), ("base_damage", "hits"), ("amount", "hits")):
                damage_value = self._first_numeric_value(intent.get(damage_key))
                if damage_value is None:
                    continue
                hits_value = 1 if hits_key is None else max(1, self._safe_int(self._first_numeric_value(intent.get(hits_key)), 1))
                return max(0, damage_value * hits_value)
            intent_type = str(intent.get("type") or intent.get("intent") or "").strip().lower()
            if "attack" not in intent_type and "hit" not in intent_type:
                return 0
        elif isinstance(intent, str):
            if "attack" not in intent.lower() and "攻击" not in intent:
                return 0
            numbers = [self._safe_int(match) for match in re.findall(r"\d+", intent)]
            if numbers:
                if len(numbers) >= 2 and ("x" in intent.lower() or "×" in intent):
                    return max(0, numbers[0] * numbers[1])
                return max(0, numbers[0])
        return 0

    def _map_state(self, context: dict[str, Any]) -> dict[str, Any]:
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

    def _build_map_summary(self, context: dict[str, Any]) -> dict[str, Any]:
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

    def _extract_generic_option_descriptions(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        options: list[dict[str, Any]] = []
        seen: set[int] = set()
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

    def _allowed_kwargs_for_action(self, action_type: str, raw: dict[str, Any], context: dict[str, Any]) -> dict[str, list[Any]]:
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
        for candidate in self._iter_option_candidates(raw):
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
            allowed_kwargs = self._allowed_kwargs_for_action(action_type, raw, context)
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
                "allowed_kwargs": self._allowed_kwargs_for_action(
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
        allowed_option_indices = self._allowed_kwargs_for_action(action_type, raw, context).get("option_index", [])
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
        if self._configured_character_strategy() != "defect":
            return None
        options = self._card_reward_options(raw, context)
        if options:
            self._log_card_reward_options(options, context)
        if not self._is_card_reward_context(raw, context):
            return None
        if not options:
            return None
        best_option: Optional[dict[str, Any]] = None
        best_score: Optional[int] = None
        for option in options:
            score = self._score_defect_card_option(option, context)
            if best_score is None or score > best_score:
                best_option = option
                best_score = score
        if best_option is None or best_score is None or best_score <= 0:
            return None
        return best_option["index"]

    def _find_preferred_map_option_index(self, raw: dict[str, Any], context: dict[str, Any]) -> Optional[int]:
        if self._configured_character_strategy() != "defect":
            return None
        options = self._extract_generic_option_descriptions(raw)
        if not options:
            return None
        best_option: Optional[dict[str, Any]] = None
        best_score: Optional[int] = None
        for option in options:
            score = self._score_defect_map_option(option, context)
            if best_score is None or score > best_score:
                best_option = option
                best_score = score
        if best_option is None or best_score is None:
            return None
        return int(best_option["index"])

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
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        screen_candidates = {
            self._normalized_screen_name(snapshot),
            str(raw_state.get("screen") or "").strip().lower(),
            str(raw_state.get("screen_type") or "").strip().lower(),
        }
        if any(keyword in candidate for candidate in screen_candidates for keyword in {"reward", "card reward", "card", "combat reward"} if candidate):
            return True
        return bool(self._card_reward_options(raw, context))

    def _card_reward_options(self, raw: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
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

    def _is_rewardish_screen(self, snapshot: dict[str, Any]) -> bool:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        screen_candidates = {
            self._normalized_screen_name(snapshot),
            str(raw_state.get("screen") or "").strip().lower(),
            str(raw_state.get("screen_type") or "").strip().lower(),
        }
        return any(keyword in candidate for candidate in screen_candidates for keyword in {"reward", "card reward", "combat reward"} if candidate)

    def _log_reward_payload_debug(self, raw: dict[str, Any], context: dict[str, Any]) -> None:
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
        except Exception as exc:
            self.logger.warning(f"记录奖励界面调试信息失败: {exc}")

    def _iter_option_candidates(self, raw: dict[str, Any]) -> list[Any]:
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

    def _extract_card_reward_options(self, candidate: Any) -> list[dict[str, Any]]:
        if isinstance(candidate, list):
            options: list[dict[str, Any]] = []
            for idx, item in enumerate(candidate):
                if not isinstance(item, dict):
                    continue
                texts = self._card_option_texts(item)
                if not texts:
                    continue
                option_index = item.get("option_index")
                if option_index is None:
                    option_index = item.get("index", idx)
                option = {
                    "index": int(option_index),
                    "texts": texts,
                    "raw": item,
                }
                options.append(option)
            return options
        if isinstance(candidate, dict):
            for key in ("options", "choices", "cards", "card_options", "items", "rewards"):
                nested = candidate.get(key)
                if isinstance(nested, list):
                    return self._extract_card_reward_options(nested)
        return []

    def _card_option_texts(self, item: dict[str, Any]) -> set[str]:
        texts: set[str] = set()
        for key in ("label", "description", "name", "id", "card_id", "card_name", "title"):
            value = item.get(key)
            if value is not None:
                normalized = str(value).strip().lower()
                if normalized:
                    texts.add(normalized)
        card = item.get("card") if isinstance(item.get("card"), dict) else None
        if card is not None:
            texts.update(self._card_option_texts(card))
        return texts

    def _score_defect_card_option(self, option: dict[str, Any], context: dict[str, Any]) -> int:
        return int(self._score_defect_card_option_details(option, context).get("score", 0))

    def _score_defect_card_option_details(self, option: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        texts = option.get("texts") if isinstance(option.get("texts"), set) else set()
        constraints = self._load_strategy_constraints("defect") if self._configured_character_strategy() == "defect" else {}
        score = 0
        base_score = 0
        constraint_hits: list[str] = []
        high_priority = {
            "冷头": 100,
            "coolheaded": 100,
            "快速检索": 95,
            "skim": 95,
            "全息影像": 95,
            "hologram": 95,
            "暴风雨": 92,
            "tempest": 92,
            "冰川": 90,
            "glacier": 90,
            "充电": 82,
            "charge_battery": 82,
            "charge battery": 82,
            "高速脱离": 80,
            "sweeping_beam": 80,
            "sweeping beam": 80,
            "白噪声": 72,
            "白噪音": 72,
            "white_noise": 72,
            "white noise": 72,
            "引雷针": 94,
            "static_discharge": 94,
            "static discharge": 94,
            "电流相生": 92,
            "electrodynamics": 92,
            "子程序": 96,
            "loop": 96,
            "雷暴": 94,
            "storm": 94,
            "创造性ai": 88,
            "创造性 ai": 88,
            "creative_ai": 88,
            "creative ai": 88,
            "超临界态": 86,
            "hyperbeam": 86,
            "双倍": 84,
            "double_energy": 84,
            "double energy": 84,
            "内核加速": 82,
            "turbo": 82,
            "火箭拳": 90,
            "go for the eyes": 90,
            "go_for_the_eyes": 90,
            "污秽攻击": 74,
            "gunk_up": 74,
            "gunk up": 74,
            "压缩": 88,
            "recycle": 88,
            "羽化": 90,
            "claw": 90,
            "万众一心": 84,
            "all_for_one": 84,
            "all for one": 84,
            "扩容": 78,
            "capacitor": 78,
            "弹幕齐射": 58,
            "barrage": 58,
            "超越光速": 70,
            "ftl": 70,
            "暗影之盾": 60,
            "shadow_shield": 60,
            "shadow shield": 60,
        }
        low_priority = {
            "打击": -25,
            "strike": -25,
            "防御": -10,
            "defend": -10,
            "硬撑": -35,
            "steam_barrier": -35,
            "steam barrier": -35,
            "超频": -30,
            "overclock": -30,
        }
        matched_high_scores = [
            value
            for name, value in high_priority.items()
            if any(name in text for text in texts)
        ]
        if matched_high_scores:
            best_high = max(matched_high_scores)
            score += best_high
            base_score += best_high
        matched_low_scores = [
            value
            for name, value in low_priority.items()
            if any(name in text for text in texts)
        ]
        if matched_low_scores:
            worst_low = min(matched_low_scores)
            score += worst_low
            base_score += worst_low
        for category, bonus in (("required", 36), ("high_priority", 22), ("low_priority", -20)):
            bucket = constraints.get(category) if isinstance(constraints.get(category), dict) else {}
            for label, cards in bucket.items():
                if any(any(card in text for text in texts) for card in cards if isinstance(card, str)):
                    score += bonus
                    constraint_hits.append(f"{category}:{label}")
        conditional_bucket = constraints.get("conditional") if isinstance(constraints.get("conditional"), dict) else {}
        for label, cards in conditional_bucket.items():
            if any(any(card in text for text in texts) for card in cards if isinstance(card, str)):
                score += 10
                constraint_hits.append(f"conditional:{label}")
        if any("状态" in text for text in texts) and not self._defect_has_card(context, {"压缩", "recycle"}):
            score -= 18
        if any(any(keyword in text for keyword in {"能力", "power"}) for text in texts):
            score += 8
        if any(any(keyword in text for keyword in {"球", "闪电球", "冰球", "充能球", "orb"}) for text in texts):
            score += 10
        return {"score": score, "base_score": base_score, "constraint_hits": constraint_hits}

    def _score_defect_map_option(self, option: dict[str, Any], context: dict[str, Any]) -> int:
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

    def _option_has_nearby_buffer(self, raw: dict[str, Any]) -> bool:
        stack: list[Any] = [raw]
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

    def _estimate_branching_value(self, raw: dict[str, Any]) -> int:
        stack: list[Any] = [raw]
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

    def _defect_has_card(self, context: dict[str, Any], names: set[str]) -> bool:
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

    def _find_preferred_character_option_index(self, raw: dict[str, Any], context: dict[str, Any]) -> Optional[int]:
        if not self._is_character_select_context(context):
            return None
        preferred_aliases = [
            {"故障机器人", "defect"},
            {"铁血战士", "ironclad"},
        ]
        options = self._character_selection_options(raw, context)
        if not options:
            return None
        for aliases in preferred_aliases:
            for option in options:
                if self._character_option_matches(option, aliases):
                    return option["index"]
        return None

    def _is_character_select_context(self, context: dict[str, Any]) -> bool:
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

    def _character_selection_options(self, raw: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
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

    def _extract_character_options(self, candidate: Any) -> list[dict[str, Any]]:
        if isinstance(candidate, list):
            options: list[dict[str, Any]] = []
            for idx, item in enumerate(candidate):
                if not isinstance(item, dict):
                    continue
                option_index = item.get("option_index")
                if option_index is None:
                    option_index = item.get("index", idx)
                option = {
                    "index": int(option_index),
                    "texts": self._character_option_texts(item),
                }
                if option["texts"]:
                    options.append(option)
            return options
        if isinstance(candidate, dict):
            nested_keys = ("options", "choices", "characters", "items")
            for key in nested_keys:
                nested = candidate.get(key)
                if isinstance(nested, list):
                    return self._extract_character_options(nested)
        return []

    def _character_option_texts(self, item: dict[str, Any]) -> set[str]:
        texts: set[str] = set()
        for key in ("label", "description", "name", "id", "character", "character_id", "class", "player_class"):
            value = item.get(key)
            if value is not None:
                normalized = str(value).strip().lower()
                if normalized:
                    texts.add(normalized)
        return texts

    def _character_option_matches(self, option: dict[str, Any], aliases: set[str]) -> bool:
        texts = option.get("texts") if isinstance(option.get("texts"), set) else set()
        return any(alias in texts for alias in aliases)

    def _find_discardable_potion_index(self, context: dict[str, Any]) -> int:
        for potion in self._potions(context):
            if bool(potion.get("can_discard")):
                return int(potion.get("index", 0))
        raise RuntimeError("当前没有可丢弃的药水")

    def _find_usable_potion_index(self, context: dict[str, Any]) -> int:
        for potion in self._potions(context):
            if bool(potion.get("can_use")):
                return int(potion.get("index", 0))
        raise RuntimeError("当前没有可使用的药水")

    def _find_playable_card_index(self, context: dict[str, Any]) -> int:
        combat = self._combat_state(context)
        tactical_summary = self._build_tactical_summary(combat)
        target_index = tactical_summary.get("recommended_target_index")
        best_damage_card = self._best_playable_damage_card(combat, target_index=target_index)
        if best_damage_card is not None:
            return int(best_damage_card.get("index", 0))
        best_block_card = self._best_playable_block_card(combat)
        if best_block_card is not None:
            return int(best_block_card.get("index", 0))
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        for card in hand:
            if isinstance(card, dict) and bool(card.get("playable")):
                return int(card.get("index", 0))
        raise RuntimeError("当前没有可打出的卡牌")

    def _find_card_target_index(self, context: dict[str, Any], card_index: int) -> Optional[int]:
        combat = self._combat_state(context)
        tactical_summary = self._build_tactical_summary(combat)
        preferred_target = tactical_summary.get("recommended_target_index")
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        for card in hand:
            if not isinstance(card, dict) or int(card.get("index", -1)) != card_index:
                continue
            valid_target_indices = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
            if preferred_target is not None and self._safe_int(preferred_target, -9999) in [self._safe_int(target, -1) for target in valid_target_indices]:
                return int(preferred_target)
            if valid_target_indices:
                return int(valid_target_indices[0])
            return None
        return None

    def _combat_state(self, context: dict[str, Any]) -> dict[str, Any]:
        raw_state = context.get("snapshot", {}).get("raw_state") if isinstance(context.get("snapshot"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state, dict) and isinstance(raw_state.get("combat"), dict) else {}
        return combat

    def _log_combat_block_fields(self, context: dict[str, Any]) -> None:
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
            self.logger.info(f"[sts2_autoplay][combat] block fields {json.dumps(payload, ensure_ascii=False, default=str)}")
        except Exception as exc:
            self.logger.warning(f"记录当前格挡字段失败: {exc}")

    def _combat_player_block(self, combat: dict[str, Any]) -> int:
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

    def _potions(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        raw_state = context.get("snapshot", {}).get("raw_state") if isinstance(context.get("snapshot"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state, dict) and isinstance(raw_state.get("run"), dict) else {}
        potions = run.get("potions") if isinstance(run.get("potions"), list) else []
        return [potion for potion in potions if isinstance(potion, dict)]

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
