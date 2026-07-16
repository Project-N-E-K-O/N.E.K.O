from __future__ import annotations

import os
from hashlib import sha1
from random import random
from time import time
from typing import Any, Callable

from .action_engine import STS2ActionEngine
from .action_registry import STS2ActionRegistry
from .candidate_generator import STS2CandidateGenerator
from .catgirl_bridge import STS2CatgirlBridge
from .companion_evaluator import STS2CompanionEvaluator
from .heuristic_planner import STS2HeuristicPlanner
from .instructions import normalize_guidance_instruction
from .loop_runner import STS2LoopRunner
from .mode_controller import STS2ModeController
from .neko_interface import STS2NekoInterface
from .preference_extractors import STS2PreferenceExtractor
from .preference_store import STS2PreferenceStore
from .runtime_state import STS2RuntimeState
from .state_machine import STS2StateMachine
from .strategy_repository import STS2StrategyRepository
from .summary_context_builder import STS2SummaryContextBuilder
from .situation_summary_engine import STS2SituationSummaryEngine
from .transport_client import STS2TransportClient


class STS2AutoplayService:
    def __init__(self, logger: Any, status_reporter: Callable[[dict[str, Any]], None], frontend_notifier: Callable[..., Any] | None = None, *, sdk_bus: Any = None, sdk_ctx: Any = None, i18n: Any = None) -> None:
        self.logger = logger
        self._report_status = status_reporter
        self._frontend_notifier = frontend_notifier
        self._sdk_bus = sdk_bus
        self._sdk_ctx = sdk_ctx
        self._i18n = i18n
        self._client: STS2TransportClient | None = None
        self._cfg: dict[str, Any] = {}
        self._state = STS2RuntimeState()
        self._state_machine = STS2StateMachine()
        self._summary_builder = STS2SummaryContextBuilder()
        self._summary_engine = STS2SituationSummaryEngine(self._i18n)
        self._companion_evaluator = STS2CompanionEvaluator(self._i18n)
        self._catgirl_bridge = STS2CatgirlBridge(i18n=self._i18n, source_id="sts2_autoplay")
        self._preference_store = STS2PreferenceStore()
        self._preference_extractor = STS2PreferenceExtractor()
        self._strategy_repository = STS2StrategyRepository(
            logger,
            self._preference_store,
            default_strategy="defect",
        )
        self._mode_controller = STS2ModeController("program")
        self._planner = STS2HeuristicPlanner(logger)
        self._candidate_generator = STS2CandidateGenerator(self._planner)
        self._action_registry = STS2ActionRegistry()
        self._action_engine = STS2ActionEngine(self._i18n)
        self._loop_runner = STS2LoopRunner(self)
        self.neko = STS2NekoInterface(self)

    def t(self, key: str, *, default: str = "", **params: Any) -> str:
        if self._i18n is not None:
            return self._i18n.t(key, default=default, **params)
        return default.format(**params) if params and default else (default or key)

    async def startup(self, cfg: dict[str, Any]) -> dict[str, Any]:
        try:
            self.logger.info("[sts2_code_version] 20260521_companion_eval_debug")
        except Exception:
            pass
        self._cfg = dict(cfg)
        base_url = self._resolve_base_url()
        self._state.base_url = base_url
        self._apply_control_mode("program")
        self._client = STS2TransportClient(
            base_url=base_url,
            connect_timeout=float(self._cfg.get("connect_timeout_seconds", 5) or 5),
            request_timeout=float(self._cfg.get("request_timeout_seconds", 15) or 15),
        )
        startup_result = {"connected": False, "companion_mode_enabled": False}

        try:
            await self.health_check()
            companion_enabled = bool(self._cfg.get("companion_mode_enabled", self._cfg.get("neko_commentary_enabled", True)))
            if companion_enabled:
                self.set_companion_mode(True)
                await self.refresh_state(trigger_sync=True)
                startup_result["companion_mode_enabled"] = True
            else:
                self._sync_background_polling()
                await self.refresh_state(trigger_sync=True)
            startup_result["connected"] = True
            if bool(self._cfg.get("autoplay_on_start", False)) and not self._state.standby:
                self.start_autoplay()
        except Exception as exc:
            self._state.transport_state = "disconnected"
            self._state.last_error = str(exc)
            self._state.consecutive_errors += 1
            self._emit_status()
            return startup_result
        return startup_result

    def _resolve_base_url(self) -> str:
        env_base_url = str(os.environ.get("STS2_API_BASE_URL") or "").strip()
        if env_base_url:
            return env_base_url.rstrip("/")

        env_port = str(os.environ.get("STS2_API_PORT") or "").strip()
        if env_port:
            try:
                port = int(env_port)
            except ValueError:
                port = 0
            if 0 < port <= 65535:
                return f"http://127.0.0.1:{port}"

        configured = str(self._cfg.get("base_url") or self._state.base_url).strip()
        return configured.rstrip("/") if configured else self._state.base_url.rstrip("/")

    async def shutdown(self) -> None:
        await self._loop_runner.stop_background()
        if self._client is not None:
            await self._client.close()
            self._client = None
        self._state.transport_state = "disconnected"
        self._state.autoplay_state = "idle"
        self._emit_status()

    async def health_check(self) -> dict[str, Any]:
        client = self._require_client()
        health = await client.health()
        self._state.transport_state = "connected"
        self._state.last_error = ""
        self._state.touch_success()
        self._emit_status()
        message = self.t("status.connected", default="STS2-Agent 已连接: {base_url}", base_url=self._state.base_url)
        return {"status": "connected", "message": message, "summary": message, "health": health}

    async def refresh_state(self, *, trigger_sync: bool = False) -> dict[str, Any]:
        previous_snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        tick_result = await self._loop_runner.tick()
        self._state.raw_state = tick_result["raw_state"]
        self._state.raw_actions = tick_result["raw_actions"]
        self._state.snapshot = tick_result["snapshot"]
        player_operation_observation = self._observe_companion_player_operation(previous_snapshot, self._state.snapshot)
        if player_operation_observation:
            self._state.latest_player_operation_observation = dict(player_operation_observation)
            self._state.remember_companion_player_op(player_operation_observation)
        else:
            self._state.latest_player_operation_observation = {}
        self._cfg["character_strategy"] = self._strategy_repository.strategy_for_snapshot(self._state.snapshot)
        self._state.transport_state = "connected"
        self._state.last_error = ""
        self._state.touch_poll()
        self._state.touch_success()
        self._remember_snapshot_metadata()
        self._update_run_intent()
        if trigger_sync:
            self._deliver_catgirl_sync(self._state.snapshot)
        self._emit_status()
        message = self.t("status.refreshed", default="已刷新状态，screen={screen}", screen=self._state.snapshot.get("screen", "unknown"))
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "snapshot": self._state.snapshot,
        }

    def _rebuild_companion_snapshot(self, snapshot: dict[str, Any]) -> None:
        if not isinstance(snapshot, dict):
            return
        classification = snapshot.get("classification") if isinstance(snapshot.get("classification"), dict) else self._state_machine.classify(snapshot)
        snapshot["classification"] = classification
        summary_context = self._summary_builder.build(snapshot, runtime_state=self._state)
        snapshot["summary_context"] = summary_context
        situation_summary = self._summary_engine.summarize(summary_context)
        snapshot["situation_summary"] = situation_summary
        strategy_context = snapshot.get("strategy_context") if isinstance(snapshot.get("strategy_context"), dict) else {}
        companion_evaluation = self._companion_evaluator.evaluate(
            summary_context=summary_context,
            situation_summary=situation_summary,
            strategy_context=strategy_context,
            runtime_state=self._state,
        )
        action_frame = self._state.latest_action_frame if isinstance(self._state.latest_action_frame, dict) else {}
        if action_frame:
            companion_evaluation["action_frame"] = dict(action_frame)
        snapshot["companion_evaluation"] = companion_evaluation
        try:
            self.logger.info(
                "[sts2_companion_eval] trigger=%s should_comment=%s turn_key=%s scene_key=%s eval_key=%s summary_kind=%s",
                companion_evaluation.get("trigger"),
                companion_evaluation.get("should_comment"),
                companion_evaluation.get("turn_key"),
                companion_evaluation.get("scene_key"),
                companion_evaluation.get("evaluation_key"),
                companion_evaluation.get("summary_kind"),
            )
        except Exception:
            pass
        snapshot["catgirl_sync"] = self._catgirl_bridge.build_sync_packet(snapshot, standby=self._state.standby)

    async def run_autoplay_step(self) -> dict[str, Any]:
        if self._state.standby:
            message = self.t("autoplay.standby_blocked", default="当前处于 standby 模式，不执行动作。")
            return {"status": "idle", "message": message, "summary": message}
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        if self._should_rebuild_operation(snapshot):
            await self.refresh_state(trigger_sync=True)
            snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        operation = snapshot.get("agent_operation") if isinstance(snapshot.get("agent_operation"), dict) else None
        if operation is None:
            message = self.t("autoplay.no_planned_operation", default="当前没有可执行的规划动作。")
            return {"status": "idle", "message": message, "summary": message}
        result = await self.execute_operation(operation)
        if result.get("status") == "ok":
            self._state.step_count += 1
        return result


    async def execute_operation(self, operation: dict[str, Any] | None) -> dict[str, Any]:
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        before_summary = self._summary_builder.build_snapshot_summary(snapshot) if snapshot else {}
        client = self._require_client()
        result = await self._action_engine.execute(client, snapshot, operation)
        if result.get("status") == "ok":
            self._state.touch_action()
            op = result.get("operation") if isinstance(result.get("operation"), dict) else {}
            self._state.last_decision_source = str(op.get("source") or "")
            self._state.last_decision_reason = str(op.get("reason") or "")
            self._consume_guidance(op)
            self._remember_plugin_action_marker(op, snapshot)
            action_seed = {
                "action_type": str(op.get("action_type") or ""),
                "action_kwargs": dict(op.get("kwargs") if isinstance(op.get("kwargs"), dict) else {}),
                "decision_source": self._state.last_decision_source,
                "decision_reason": self._state.last_decision_reason,
                "created_at": time(),
            }
            self._state.latest_action_frame = dict(action_seed)
            await self.refresh_state(trigger_sync=True)
            after_snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
            after_summary = self._summary_builder.build_snapshot_summary(after_snapshot) if after_snapshot else {}
            action_delta = self._summary_engine.compute_delta(before_summary, after_summary, source="action_paired")
            action_frame = {
                "before": dict(before_summary),
                "after": dict(after_summary),
                "delta": dict(action_delta),
                **action_seed,
                "step_count": self._state.step_count + 1,
            }
            self._state.remember_action_frame(action_frame)
            self._state.remember_decision_memory(
                {
                    "screen": str(after_snapshot.get("screen") or before_summary.get("screen") or "unknown"),
                    "action_type": action_frame["action_type"],
                    "decision_reason": action_frame["decision_reason"],
                    "decision_source": action_frame["decision_source"],
                    "delta": dict(action_delta),
                    "step_count": action_frame["step_count"],
                }
            )
            summary_context = self._summary_builder.build(after_snapshot, runtime_state=self._state)
            self._state.snapshot["summary_context"] = summary_context
            self._state.snapshot["situation_summary"] = self._summary_engine.summarize(summary_context)
        return result

    async def get_status(self) -> dict[str, Any]:
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        classification = snapshot.get("classification") if isinstance(snapshot.get("classification"), dict) else {}
        summary_context = snapshot.get("summary_context") if isinstance(snapshot.get("summary_context"), dict) else {}
        strategy_context = snapshot.get("strategy_context") if isinstance(snapshot.get("strategy_context"), dict) else {}
        mode = snapshot.get("mode") if isinstance(snapshot.get("mode"), dict) else self._mode_controller.describe(self._state.control_mode)
        planned_operation = snapshot.get("planned_operation") if isinstance(snapshot.get("planned_operation"), dict) else None
        agent_operation = snapshot.get("agent_operation") if isinstance(snapshot.get("agent_operation"), dict) else None
        executable_operation = snapshot.get("executable_operation") if isinstance(snapshot.get("executable_operation"), dict) else None
        agent_packet = snapshot.get("agent_packet") if isinstance(snapshot.get("agent_packet"), dict) else {}
        situation_summary = snapshot.get("situation_summary") if isinstance(snapshot.get("situation_summary"), dict) else {}
        catgirl_sync = snapshot.get("catgirl_sync") if isinstance(snapshot.get("catgirl_sync"), dict) else {}
        summary = (
            f"transport={self._state.transport_state} | "
            f"screen={snapshot.get('screen', 'unknown')} | "
            f"class={classification.get('screen_class', 'unknown')} | "
            f"mode={mode.get('mode', 'unknown')} | "
            f"autoplay={self._state.autoplay_state}"
        )
        return {
            "summary": summary,
            "message": summary,
            "server": {
                "state": self._state.transport_state,
                "base_url": self._state.base_url,
                "last_error": self._state.last_error,
                "last_success_at": self._state.last_success_at,
                "last_poll_at": self._state.last_poll_at,
                "consecutive_errors": self._state.consecutive_errors,
            },
            "run": {
                "screen": snapshot.get("screen", "unknown"),
                "floor": snapshot.get("floor", 0),
                "act": snapshot.get("act", 0),
                "in_combat": snapshot.get("in_combat", False),
                "step_count": self._state.step_count,
                "snapshot_signature": self._state.snapshot_signature,
            },
            "autoplay": {
                "state": self._state.autoplay_state,
                "standby": self._state.standby,
                "control_mode": self._state.control_mode,
                "pause_reason": self._state.pause_reason,
                "stop_reason": self._state.stop_reason,
                "is_polling": self._loop_runner.is_polling(),
                "is_autoplaying": self._loop_runner.is_autoplaying(),
                "last_action_at": self._state.last_action_at,
                "guidance_generation": self._state.guidance_generation,
                "pending_guidance": list(self._state.pending_guidance),
                "interrupt_requested": self._state.interrupt_requested,
                "interrupt_reason": self._state.interrupt_reason,
            },
            "companion_mode": {
                "enabled": bool(self._cfg.get("companion_mode_enabled", self._cfg.get("neko_commentary_enabled", True))),
                "reporting_enabled": bool(self._cfg.get("neko_reporting_enabled", True)),
                "commentary_enabled": bool(self._cfg.get("neko_commentary_enabled", True)),
                "commentary_probability": float(self._cfg.get("neko_commentary_probability", 0.65) or 0.65),
                "critical_commentary_always": bool(self._cfg.get("neko_critical_commentary_always", True)),
                "latest_player_operation_observation": dict(self._state.latest_player_operation_observation),
            },
            "classification": classification,
            "summary_context": summary_context,
            "strategy_context": strategy_context,
            "mode": mode,
            "planned_operation": planned_operation,
            "agent_operation": agent_operation,
            "executable_operation": executable_operation,
            "agent_packet": agent_packet,
            "situation_summary": situation_summary,
            "catgirl_sync": catgirl_sync,
            "preference_domains": list(self._preference_store.export_all().keys()),
            "recent_deliveries": list(self._state.recent_deliveries),
            "latest_sync_packet": dict(self._state.latest_sync_packet),
        }

    async def get_snapshot(self) -> dict[str, Any]:
        if not self._state.snapshot:
            await self.refresh_state(trigger_sync=True)
        snapshot = self._state.snapshot
        screen = snapshot.get("screen", "unknown") if isinstance(snapshot, dict) else "unknown"
        message = self.t("status.snapshot", default="当前快照：screen={screen}", screen=screen)
        return {"status": "ok", "message": message, "summary": message, "snapshot": snapshot}

    async def execute_planned_operation(self) -> dict[str, Any]:
        return await self.run_autoplay_step()

    def start_autoplay(self) -> dict[str, Any]:
        if self._state.standby:
            self._state.autoplay_state = "standby"
            message = self.t("autoplay.start_blocked_standby", default="当前处于 standby 模式，无法启动自动运行。")
            return {"status": "idle", "message": message, "summary": message}
        self._state.autoplay_state = "running"
        self._state.pause_reason = ""
        self._state.stop_reason = ""
        self._sync_background_polling()
        self._loop_runner.start_autoplay()
        self._emit_status()
        message = self.t("autoplay.started", default="已启动尖塔自动运行。")
        return {"status": "ok", "message": message, "summary": message}

    def pause_autoplay(self, reason: str = "user") -> dict[str, Any]:
        self._state.autoplay_state = "paused"
        self._state.pause_reason = reason
        self._sync_background_polling()
        self._emit_status()
        message = self.t("autoplay.paused", default="已暂停尖塔自动运行。")
        return {"status": "ok", "message": message, "summary": message, "pause_reason": reason}

    def resume_autoplay(self) -> dict[str, Any]:
        if self._state.standby:
            message = self.t("autoplay.resume_blocked_standby", default="当前处于 standby 模式，不能恢复自动运行。")
            return {"status": "idle", "message": message, "summary": message}
        self._state.autoplay_state = "running"
        self._state.pause_reason = ""
        self._sync_background_polling()
        self._loop_runner.start_autoplay()
        self._emit_status()
        message = self.t("autoplay.resumed", default="已恢复尖塔自动运行。")
        return {"status": "ok", "message": message, "summary": message}

    def stop_autoplay(self, reason: str = "manual") -> dict[str, Any]:
        self._state.autoplay_state = "standby" if self._state.standby else "idle"
        self._state.stop_reason = reason
        self._state.pause_reason = ""
        self._sync_background_polling()
        self._emit_status()
        message = self.t("autoplay.stopped", default="已停止尖塔自动运行。")
        return {"status": "ok", "message": message, "summary": message, "stop_reason": reason}

    def set_standby(self, standby: bool) -> dict[str, Any]:
        normalized = self._apply_control_mode("standby" if standby else "program")
        return self._mode_controller.describe(normalized) | {"mode": normalized}

    def set_companion_mode(self, enabled: bool) -> dict[str, Any]:
        if not enabled and self._loop_runner.is_polling() and not self._loop_runner.is_autoplaying() and hasattr(self.logger, "info"):
            try:
                self.logger.info("[sts2_companion] disabling companion while polling active; keeping state refresh path quiet during teardown")
            except Exception:
                pass
        self._cfg["companion_mode_enabled"] = bool(enabled)
        self._cfg["neko_reporting_enabled"] = bool(enabled)
        self._cfg["neko_commentary_enabled"] = bool(enabled)
        if not enabled:
            self._state.latest_player_operation_observation = {}
            self._state.last_companion_scene_key = ""
            self._state.last_companion_turn_key = ""
            self._state.last_companion_evaluation_key = ""
            self._state.last_companion_combat_comment_key = ""
            self._state.last_companion_player_op_fingerprint = ""
            self._state.latest_sync_packet = {}
        message = self.t("companion.enabled", default="已开启陪玩模式。") if enabled else self.t("companion.disabled", default="已关闭陪玩模式。")
        self._emit_status()
        if enabled:
            self._sync_background_polling()
            self._push_companion_message()
        else:
            self._sync_background_polling()
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "enabled": bool(enabled),
            "reporting_enabled": bool(self._cfg.get("neko_reporting_enabled", False)),
            "commentary_enabled": bool(self._cfg.get("neko_commentary_enabled", False)),
        }

    async def apply_user_override_safely(self, instruction: str, *, source: str = "user") -> dict[str, Any]:
        was_running = self._state.autoplay_state == "running"
        was_paused = self._state.autoplay_state == "paused"
        pause_result: dict[str, Any] | None = None
        if was_running:
            pause_result = self.pause_autoplay(reason="apply_user_override")
            if pause_result.get("status") != "ok":
                return pause_result
        result = await self.neko.extract_and_upsert_preference(instruction, source=source)
        status = str(result.get("status") or "")
        if status != "ok":
            if was_running:
                message = str(result.get("message") or result.get("summary") or "策略更新失败。")
                result["message"] = message + " 自动游玩已暂停，请确认后再手动恢复。"
                result["summary"] = result["message"]
                result["autoplay_paused"] = True
            return result
        if was_running:
            message = str(result.get("message") or result.get("summary") or "策略已更新。")
            result["message"] = message + " 自动游玩已先暂停；如果要继续，请手动恢复自动游玩。"
            result["summary"] = result["message"]
            result["autoplay_paused"] = True
            result["pause_reason"] = pause_result.get("pause_reason") if isinstance(pause_result, dict) else "apply_user_override"
            return result
        if was_paused:
            message = str(result.get("message") or result.get("summary") or "策略已更新。")
            result["message"] = message + " 当前自动游玩仍处于暂停状态。"
            result["summary"] = result["message"]
            result["autoplay_paused"] = True
        return result

    def _require_client(self) -> STS2TransportClient:
        if self._client is None:
            raise RuntimeError(self.t("errors.client_not_started", default="STS2 client 未启动"))
        return self._client

    def _cfg_poll_interval(self, *, active: bool) -> float:
        key = "poll_interval_active_seconds" if active else "poll_interval_idle_seconds"
        return float(self._cfg.get(key, 1 if active else 3) or (1 if active else 3))

    def _cfg_action_interval(self) -> float:
        return float(self._cfg.get("action_interval_seconds", 1.5) or 1.5)

    def _cfg_companion_push_probability(self) -> float:
        try:
            value = float(self._cfg.get("companion_push_probability", 0.2) or 0.2)
        except Exception:
            return 0.2
        return min(1.0, max(0.0, value))

    def _cfg_autoplay_push_probability(self) -> float:
        try:
            value = float(self._cfg.get("autoplay_push_probability", 0.35) or 0.35)
        except Exception:
            return 0.35
        return min(1.0, max(0.0, value))

    def _should_allow_push_by_probability(self, *, companion_mode: bool) -> bool:
        probability = self._cfg_companion_push_probability() if companion_mode else self._cfg_autoplay_push_probability()
        if probability >= 1.0:
            return True
        if probability <= 0.0:
            return False
        return random() < probability

    def _apply_control_mode(self, mode: str) -> str:
        normalized = self._mode_controller.normalize(mode)
        previous_standby = self._state.standby
        self._state.control_mode = normalized
        if self._state.standby:
            self._state.autoplay_state = "standby"
        elif previous_standby and self._state.autoplay_state == "standby":
            self._state.autoplay_state = "idle"
        if previous_standby != self._state.standby:
            self._state.interrupt_requested = True
            self._state.interrupt_reason = "mode_change"
        return normalized

    def _queue_guidance(self, content: str, *, source: str = "neko") -> dict[str, Any]:
        self._state.guidance_generation += 1
        guidance = normalize_guidance_instruction(
            content,
            source=source,
            guidance_type="soft_guidance",
        )
        guidance["id"] = f"guidance-{self._state.guidance_generation}"
        guidance["generation"] = self._state.guidance_generation
        guidance["origin"]["generation"] = self._state.guidance_generation
        self._state.pending_guidance.append(guidance)
        self._state.pending_guidance = self._state.pending_guidance[-20:]
        self._state.interrupt_requested = True
        self._state.interrupt_reason = "guidance"
        return guidance

    def _consume_guidance(self, operation: dict[str, Any] | None) -> None:
        if not isinstance(operation, dict):
            return
        consumed_generation = int(operation.get("consumed_guidance_generation") or 0)
        consumed_ids = {
            str(item)
            for item in (operation.get("consumed_guidance_ids") if isinstance(operation.get("consumed_guidance_ids"), list) else [])
            if str(item)
        }
        if consumed_generation <= 0 and not consumed_ids:
            return
        self._state.last_consumed_guidance_generation = max(self._state.last_consumed_guidance_generation, consumed_generation)
        self._state.pending_guidance = [
            item for item in self._state.pending_guidance
            if int(item.get("generation") or 0) > self._state.last_consumed_guidance_generation and str(item.get("id") or "") not in consumed_ids
        ]
        self._state.interrupt_requested = False
        self._state.interrupt_reason = ""

    def _should_rebuild_operation(self, snapshot: dict[str, Any]) -> bool:
        if self._state.interrupt_requested:
            return True
        operation = snapshot.get("agent_operation") if isinstance(snapshot.get("agent_operation"), dict) else None
        if operation is None:
            return True
        decision_epoch = int(operation.get("decision_epoch") or 0)
        return decision_epoch < self._state.guidance_generation

    def _should_deliver_sync(self, catgirl_sync: dict[str, Any]) -> bool:
        fingerprint = str(catgirl_sync.get("fingerprint") or "")
        min_interval = float(catgirl_sync.get("min_interval_seconds") or 0.0)
        force = bool(catgirl_sync.get("force"))
        payload = catgirl_sync.get("payload") if isinstance(catgirl_sync.get("payload"), dict) else {}
        screen = str(payload.get("screen") or "")
        summary_kind = str(payload.get("summary_kind") or "")
        if force:
            try:
                self.logger.info(
                    "[sts2_push_debug] should_deliver_sync allow: force=true fingerprint=%s screen=%s summary_kind=%s",
                    fingerprint,
                    screen,
                    summary_kind,
                )
            except Exception:
                pass
            return True
        if self._state.interrupt_requested:
            try:
                self.logger.info(
                    "[sts2_push_debug] should_deliver_sync allow: interrupt_requested=true reason=%s fingerprint=%s",
                    self._state.interrupt_reason,
                    fingerprint,
                )
            except Exception:
                pass
            return True
        if fingerprint and fingerprint == self._state.last_sync_fingerprint:
            now = time()
            elapsed = now - self._state.last_sync_at if self._state.last_sync_at else None
            if self._state.last_sync_at and elapsed < min_interval:
                self._state.sync_repeat_count += 1
                try:
                    self.logger.info(
                        "[sts2_push_debug] should_deliver_sync deny: duplicate fingerprint=%s elapsed=%.3f min_interval=%.3f repeat_count=%s last_screen=%s last_summary_kind=%s",
                        fingerprint,
                        elapsed,
                        min_interval,
                        self._state.sync_repeat_count,
                        self._state.last_sync_screen,
                        self._state.last_sync_summary_kind,
                    )
                except Exception:
                    pass
                return False
            try:
                self.logger.info(
                    "[sts2_push_debug] should_deliver_sync allow: duplicate fingerprint outside interval fingerprint=%s elapsed=%s min_interval=%.3f",
                    fingerprint,
                    f"{elapsed:.3f}" if elapsed is not None else "none",
                    min_interval,
                )
            except Exception:
                pass
        if screen != self._state.last_sync_screen or summary_kind != self._state.last_sync_summary_kind:
            try:
                self.logger.info(
                    "[sts2_push_debug] should_deliver_sync allow: scene_changed fingerprint=%s screen=%s->%s summary_kind=%s->%s",
                    fingerprint,
                    self._state.last_sync_screen,
                    screen,
                    self._state.last_sync_summary_kind,
                    summary_kind,
                )
            except Exception:
                pass
            return True
        try:
            self.logger.info(
                "[sts2_push_debug] should_deliver_sync allow: default fingerprint=%s screen=%s summary_kind=%s last_fingerprint=%s",
                fingerprint,
                screen,
                summary_kind,
                self._state.last_sync_fingerprint,
            )
        except Exception:
            pass
        return True

    def _mark_loop_error(self, exc: Exception) -> None:
        self._state.transport_state = "error"
        self._state.last_error = str(exc)
        self._state.consecutive_errors += 1
        self._state.autoplay_state = "error" if self._state.autoplay_state == "running" else self._state.autoplay_state
        self._emit_status()

    def _companion_mode_active(self) -> bool:
        return bool(self._cfg.get("companion_mode_enabled", self._cfg.get("neko_commentary_enabled", True))) and bool(self._cfg.get("neko_commentary_enabled", True))

    def _should_keep_polling(self) -> bool:
        return bool(self._cfg.get("companion_mode_enabled", False)) or self._state.autoplay_state in {"running", "paused"}

    def _sync_background_polling(self) -> None:
        if self._should_keep_polling():
            self._loop_runner.start_background()
            return
        self._loop_runner.stop_background_sync()

    def _remember_plugin_action_marker(self, operation: dict[str, Any], snapshot: dict[str, Any]) -> None:
        action_type = str(operation.get("action_type") or "")
        if not action_type:
            self._state.last_plugin_action_fingerprint = ""
            return
        screen = str(snapshot.get("screen") or "unknown")
        floor = snapshot.get("floor")
        act = snapshot.get("act")
        turn = self._snapshot_turn(snapshot)
        action_kwargs = operation.get("kwargs") if isinstance(operation.get("kwargs"), dict) else {}
        payload = f"{action_type}|{screen}|{floor}|{act}|{turn}|{sorted(action_kwargs.items())}"
        self._state.last_plugin_action_fingerprint = sha1(payload.encode("utf-8")).hexdigest()[:16]

    def _observe_companion_player_operation(self, previous_snapshot: dict[str, Any], current_snapshot: dict[str, Any]) -> dict[str, Any] | None:
        if not self._companion_mode_active():
            return None
        if not isinstance(previous_snapshot, dict) or not previous_snapshot:
            return None
        if not isinstance(current_snapshot, dict) or not current_snapshot:
            return None

        previous_summary = self._summary_builder.build_snapshot_summary(previous_snapshot)
        current_summary = self._summary_builder.build_snapshot_summary(current_snapshot)
        delta = self._summary_engine.compute_delta(previous_summary, current_summary, source="player_operation")
        event_type = self._classify_player_operation(previous_snapshot, current_snapshot, previous_summary, current_summary, delta)
        if not event_type:
            return None

        observation = self._build_player_operation_observation(
            event_type=event_type,
            previous_snapshot=previous_snapshot,
            current_snapshot=current_snapshot,
            previous_summary=previous_summary,
            current_summary=current_summary,
            delta=delta,
        )
        if observation is None:
            return None
        if self._is_recent_plugin_action(observation):
            return None
        if not self._should_emit_player_operation(observation):
            return None
        return observation

    def _classify_player_operation(
        self,
        previous_snapshot: dict[str, Any],
        current_snapshot: dict[str, Any],
        previous_summary: dict[str, Any],
        current_summary: dict[str, Any],
        delta: dict[str, Any],
    ) -> str:
        previous_screen = str(previous_snapshot.get("screen") or "unknown")
        current_screen = str(current_snapshot.get("screen") or "unknown")
        previous_floor = self._safe_int(previous_snapshot.get("floor") if previous_snapshot.get("floor") is not None else previous_summary.get("floor"))
        current_floor = self._safe_int(current_snapshot.get("floor") if current_snapshot.get("floor") is not None else current_summary.get("floor"))
        previous_act = self._safe_int(previous_snapshot.get("act") if previous_snapshot.get("act") is not None else previous_summary.get("act"))
        current_act = self._safe_int(current_snapshot.get("act") if current_snapshot.get("act") is not None else current_summary.get("act"))
        previous_turn = self._safe_int(previous_summary.get("turn"))
        current_turn = self._safe_int(current_summary.get("turn"))
        previous_in_combat = bool(previous_summary.get("in_combat"))
        current_in_combat = bool(current_summary.get("in_combat"))

        if current_floor > previous_floor or current_act > previous_act:
            return "run_progressed"
        if not previous_in_combat and current_in_combat:
            return "combat_started"
        if previous_in_combat and not current_in_combat:
            return "combat_ended"
        if previous_screen != current_screen:
            target_class = str(current_snapshot.get("classification", {}).get("screen_class") if isinstance(current_snapshot.get("classification"), dict) else "")
            if target_class in {"reward", "selection", "shop", "rest"} or current_screen in {"event", "map", "shop", "rest", "reward"}:
                return "choice_committed"
            return "screen_transition"
        if current_in_combat and current_turn > previous_turn:
            return "combat_turn_advanced"
        if current_in_combat and self._combat_state_changed(delta):
            return "player_card_or_action_committed"
        if self._choice_state_changed(previous_snapshot, current_snapshot):
            return "choice_committed"
        return ""

    def _build_player_operation_observation(
        self,
        *,
        event_type: str,
        previous_snapshot: dict[str, Any],
        current_snapshot: dict[str, Any],
        previous_summary: dict[str, Any],
        current_summary: dict[str, Any],
        delta: dict[str, Any],
    ) -> dict[str, Any] | None:
        current_screen = str(current_snapshot.get("screen") or "unknown")
        floor = self._safe_int(current_snapshot.get("floor") if current_snapshot.get("floor") is not None else current_summary.get("floor"))
        act = self._safe_int(current_snapshot.get("act") if current_snapshot.get("act") is not None else current_summary.get("act"))
        turn = self._safe_int(current_summary.get("turn"))
        scene_key = f"{act}:{floor}:{current_screen}:{turn if event_type.startswith('combat') else 0}"
        summary = self._render_player_operation_summary(event_type, previous_snapshot, current_snapshot, delta)
        if not summary:
            return None
        delta_text = str(delta.get("text") or "").strip()
        fingerprint_payload = f"{event_type}|{current_screen}|{floor}|{act}|{turn}|{summary}|{delta_text}"
        fingerprint = sha1(fingerprint_payload.encode("utf-8")).hexdigest()[:16]
        return {
            "event_type": event_type,
            "screen": current_screen,
            "floor": floor,
            "act": act,
            "turn": turn,
            "scene_key": scene_key,
            "summary": summary,
            "delta_text": delta_text,
            "fingerprint": fingerprint,
            "captured_at": time(),
            "should_comment": True,
            "source": "state_observer",
        }

    def _render_player_operation_summary(self, event_type: str, previous_snapshot: dict[str, Any], current_snapshot: dict[str, Any], delta: dict[str, Any]) -> str:
        previous_screen = str(previous_snapshot.get("screen") or "unknown")
        current_screen = str(current_snapshot.get("screen") or "unknown")
        delta_text = str(delta.get("text") or "").strip()
        if event_type == "combat_started":
            return self.t("companion.player_operation.combat_started", default="玩家进入了新的战斗。")
        if event_type == "combat_ended":
            return self.t("companion.player_operation.combat_ended", default="玩家刚结束这场战斗，局面已经切到后续结算。")
        if event_type == "combat_turn_advanced":
            return self.t("companion.player_operation.combat_turn_advanced", default="玩家已经推进到新的战斗回合。")
        if event_type == "player_card_or_action_committed":
            return delta_text or self.t("companion.player_operation.action_committed", default="玩家刚在战斗中完成了一步操作。")
        if event_type == "choice_committed":
            return self.t("companion.player_operation.choice_committed", default="玩家刚完成了一个关键选择，画面从 {previous_screen} 进入 {current_screen}。", previous_screen=previous_screen, current_screen=current_screen)
        if event_type == "run_progressed":
            return self.t("companion.player_operation.run_progressed", default="玩家推进了当前流程，楼层或章节发生了变化。")
        if event_type == "screen_transition":
            return self.t("companion.player_operation.screen_transition", default="玩家把画面从 {previous_screen} 切换到了 {current_screen}。", previous_screen=previous_screen, current_screen=current_screen)
        return delta_text

    def _is_recent_plugin_action(self, observation: dict[str, Any]) -> bool:
        if not self._state.last_action_at:
            return False
        if time() - self._state.last_action_at > 2.5:
            return False
        last_action_frame = self._state.latest_action_frame if isinstance(self._state.latest_action_frame, dict) else {}
        before = last_action_frame.get("before") if isinstance(last_action_frame.get("before"), dict) else {}
        after = last_action_frame.get("after") if isinstance(last_action_frame.get("after"), dict) else {}
        action_type = str(last_action_frame.get("action_type") or "")
        screen = str(observation.get("screen") or "")
        turn = self._safe_int(observation.get("turn"))
        observation_event = str(observation.get("event_type") or "")
        if observation_event == "player_card_or_action_committed" and action_type == "play_card":
            return False
        if screen and screen in {str(before.get("screen") or ""), str(after.get("screen") or "")}:
            if turn and turn in {self._safe_int(before.get("turn")), self._safe_int(after.get("turn"))}:
                return True
            if observation_event in {"combat_ended", "choice_committed", "screen_transition"}:
                return True
        return False

    def _should_emit_player_operation(self, observation: dict[str, Any]) -> bool:
        fingerprint = str(observation.get("fingerprint") or "")
        if not fingerprint:
            return False
        if fingerprint == self._state.last_companion_player_op_fingerprint:
            min_interval = 0.0 if str(observation.get("event_type") or "") in {"combat_ended", "choice_committed", "run_progressed"} else 4.0
            if self._state.last_companion_player_op_at and time() - self._state.last_companion_player_op_at < min_interval:
                return False
        return True

    def _combat_state_changed(self, delta: dict[str, Any]) -> bool:
        player_changes = delta.get("player_changes") if isinstance(delta.get("player_changes"), dict) else {}
        enemy_changes = delta.get("enemy_changes") if isinstance(delta.get("enemy_changes"), dict) else {}
        hand_changes = delta.get("hand_changes") if isinstance(delta.get("hand_changes"), dict) else {}
        return any(
            [
                self._safe_int(player_changes.get("energy_delta")) != 0,
                self._safe_int(player_changes.get("block_delta")) != 0,
                self._safe_int(enemy_changes.get("enemy_total_hp_delta")) != 0,
                self._safe_int(hand_changes.get("hand_count_delta")) != 0,
                bool(hand_changes.get("left_cards")),
            ]
        )

    def _choice_state_changed(self, previous_snapshot: dict[str, Any], current_snapshot: dict[str, Any]) -> bool:
        previous_actions = previous_snapshot.get("available_actions") if isinstance(previous_snapshot.get("available_actions"), list) else []
        current_actions = current_snapshot.get("available_actions") if isinstance(current_snapshot.get("available_actions"), list) else []
        previous_names = {str(action.get("type") or "") for action in previous_actions if isinstance(action, dict)}
        current_names = {str(action.get("type") or "") for action in current_actions if isinstance(action, dict)}
        if previous_names == current_names:
            return False
        interesting = {"choose_reward_card", "claim_reward", "choose_event_option", "choose_map_node", "choose_rest_option", "buy_card", "buy_relic", "buy_potion"}
        return bool(previous_names & interesting or current_names & interesting)

    def _snapshot_turn(self, snapshot: dict[str, Any]) -> int:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        return self._safe_int(raw_state.get("turn") if raw_state.get("turn") is not None else combat.get("turn"))

    def _safe_int(self, value: Any) -> int:
        try:
            if value is None:
                return 0
            return int(value)
        except Exception:
            return 0

    def _update_run_intent(self) -> None:
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        summary_context = snapshot.get("summary_context") if isinstance(snapshot.get("summary_context"), dict) else {}
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        strategy_context = snapshot.get("strategy_context") if isinstance(snapshot.get("strategy_context"), dict) else {}
        hp = payload.get("current_hp")
        max_hp = payload.get("max_hp")
        hp_ratio = 0.0
        try:
            if max_hp not in (None, 0):
                hp_ratio = float(hp or 0) / float(max_hp)
        except Exception:
            hp_ratio = 0.0
        run_intent = {
            "strategy_name": strategy_context.get("strategy_name"),
            "screen": snapshot.get("screen"),
            "screen_class": snapshot.get("classification", {}).get("screen_class") if isinstance(snapshot.get("classification"), dict) else None,
            "hp_ratio": hp_ratio,
            "risk_posture": "preserve_hp" if hp_ratio < 0.45 else "growth",
            "floor": snapshot.get("floor"),
            "act": snapshot.get("act"),
        }
        self._state.run_intent = run_intent

    def _remember_snapshot_metadata(self) -> None:
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        payload = f"{snapshot.get('screen')}|{snapshot.get('floor')}|{snapshot.get('act')}|{snapshot.get('in_combat')}|{snapshot.get('available_action_count')}"
        self._state.snapshot_signature = sha1(payload.encode("utf-8")).hexdigest()[:12]
        situation_summary = snapshot.get("situation_summary") if isinstance(snapshot.get("situation_summary"), dict) else {}
        self._state.last_summary = str(situation_summary.get("text") or "")
        planned = snapshot.get("planned_operation") if isinstance(snapshot.get("planned_operation"), dict) else {}
        self._state.last_planner_type = str(planned.get("source") or "")
        agent_operation = snapshot.get("agent_operation") if isinstance(snapshot.get("agent_operation"), dict) else {}
        self._state.last_decision_source = str(agent_operation.get("source") or self._state.last_decision_source)
        self._state.last_decision_reason = str(agent_operation.get("reason") or self._state.last_decision_reason)
        self._state.last_decision_generation = int(agent_operation.get("decision_epoch") or self._state.last_decision_generation)

    def _deliver_catgirl_sync(self, snapshot: dict[str, Any]) -> None:
        catgirl_sync = snapshot.get("catgirl_sync") if isinstance(snapshot.get("catgirl_sync"), dict) else {}
        payload = catgirl_sync.get("payload") if isinstance(catgirl_sync.get("payload"), dict) else {}
        companion_evaluation = payload.get("companion_evaluation") if isinstance(payload.get("companion_evaluation"), dict) else {}
        player_operation_observation = payload.get("player_operation_observation") if isinstance(payload.get("player_operation_observation"), dict) else {}
        try:
            self.logger.info(
                "[sts2_push_debug] deliver_catgirl_sync should_sync=%s should_comment=%s force=%s fingerprint=%s last_fingerprint=%s min_interval=%s payload_keys=%s summary_kind=%s sync_priority=%s companion_trigger=%s player_op=%s queue_only=%s ai_behavior=%s message_len=%s",
                catgirl_sync.get("should_sync"),
                companion_evaluation.get("should_comment") if companion_evaluation else catgirl_sync.get("should_comment"),
                catgirl_sync.get("force"),
                catgirl_sync.get("fingerprint"),
                self._state.last_sync_fingerprint,
                catgirl_sync.get("min_interval_seconds"),
                sorted(payload.keys()) if isinstance(payload, dict) else [],
                payload.get("summary_kind") if isinstance(payload, dict) else None,
                payload.get("sync_priority") if isinstance(payload, dict) else None,
                payload.get("companion_trigger") if isinstance(payload, dict) else None,
                player_operation_observation.get("event_type") if player_operation_observation else None,
                payload.get("queue_only") if isinstance(payload, dict) else None,
                payload.get("ai_behavior") if isinstance(payload, dict) else None,
                len(str(payload.get("message") or payload.get("summary") or "")),
            )
        except Exception:
            pass
        if not payload:
            return
        self._state.latest_sync_packet = dict(payload)
        notifier = self._frontend_notifier
        if not bool(catgirl_sync.get("should_sync")):
            if str(companion_evaluation.get("trigger") or payload.get("trigger") or "") == "combat_turn":
                try:
                    self.logger.info(
                        "[sts2_combat_turn_path] return=should_sync_false turn_key=%s last_turn_key=%s",
                        companion_evaluation.get("turn_key"),
                        self._state.last_companion_turn_key,
                    )
                except Exception:
                    pass
            try:
                self.logger.info(
                    "[sts2_push_debug] deliver_catgirl_sync skipped: should_sync false screen=%s summary_kind=%s sync_priority=%s",
                    payload.get("screen"),
                    payload.get("summary_kind"),
                    payload.get("sync_priority"),
                )
            except Exception:
                pass
            return
        if companion_evaluation:
            if str(companion_evaluation.get("trigger") or "") == "combat_turn":
                try:
                    self.logger.info(
                        "[sts2_combat_turn_path] companion_eval trigger=%s should_comment=%s turn_key=%s last_turn_key=%s",
                        companion_evaluation.get("trigger"),
                        companion_evaluation.get("should_comment"),
                        companion_evaluation.get("turn_key"),
                        self._state.last_companion_turn_key,
                    )
                except Exception:
                    pass
            if str(companion_evaluation.get("trigger") or "") == "combat_turn":
                try:
                    self.logger.info(
                        "[sts2_combat_turn_gate] should_comment=%s turn_key=%s last_turn_key=%s scene_key=%s eval_key=%s commentary=%s",
                        companion_evaluation.get("should_comment"),
                        companion_evaluation.get("turn_key"),
                        self._state.last_companion_turn_key,
                        companion_evaluation.get("scene_key"),
                        companion_evaluation.get("evaluation_key"),
                        str(companion_evaluation.get("commentary") or "")[:120],
                    )
                except Exception:
                    pass
            if not bool(companion_evaluation.get("should_comment", True)):
                if str(companion_evaluation.get("trigger") or "") == "combat_turn":
                    try:
                        self.logger.info(
                            "[sts2_combat_turn_path] return=should_comment_false turn_key=%s last_turn_key=%s eval_key=%s",
                            companion_evaluation.get("turn_key"),
                            self._state.last_companion_turn_key,
                            companion_evaluation.get("evaluation_key"),
                        )
                    except Exception:
                        pass
                try:
                    self.logger.info(
                        "[sts2_push_debug] deliver_catgirl_sync skipped: companion should_comment false trigger=%s turn_key=%s scene_key=%s evaluation_key=%s commentary_len=%s",
                        companion_evaluation.get("trigger"),
                        companion_evaluation.get("turn_key"),
                        companion_evaluation.get("scene_key"),
                        companion_evaluation.get("evaluation_key"),
                        len(str(companion_evaluation.get("commentary") or "")),
                    )
                except Exception:
                    pass
                return
        if not self._should_deliver_sync(catgirl_sync):
            if str(companion_evaluation.get("trigger") or payload.get("trigger") or "") == "combat_turn":
                try:
                    self.logger.info(
                        "[sts2_combat_turn_path] return=should_deliver_sync_false turn_key=%s last_turn_key=%s fingerprint=%s",
                        companion_evaluation.get("turn_key"),
                        self._state.last_companion_turn_key,
                        catgirl_sync.get("fingerprint"),
                    )
                except Exception:
                    pass
            print("[sts2_companion_sync:skip] reason=dedup_or_interval_gate")
            try:
                self.logger.info(
                    "[sts2_push_debug] deliver_catgirl_sync skipped: _should_deliver_sync false fingerprint=%s last_fingerprint=%s last_sync_at=%s repeat_count=%s",
                    catgirl_sync.get("fingerprint"),
                    self._state.last_sync_fingerprint,
                    self._state.last_sync_at,
                    self._state.sync_repeat_count,
                )
            except Exception:
                pass
            return
        if not bool(catgirl_sync.get("force")) and not self._should_allow_push_by_probability(companion_mode=self._companion_mode_active()):
            try:
                self.logger.info(
                    "[sts2_push_debug] deliver_catgirl_sync skipped: probability_gate companion_mode=%s autoplay_state=%s companion_probability=%.3f autoplay_probability=%.3f",
                    self._companion_mode_active(),
                    self._state.autoplay_state,
                    self._cfg_companion_push_probability(),
                    self._cfg_autoplay_push_probability(),
                )
            except Exception:
                pass
            return
        if notifier is None:
            if str(companion_evaluation.get("trigger") or payload.get("trigger") or "") == "combat_turn":
                try:
                    self.logger.info(
                        "[sts2_combat_turn_path] return=frontend_notifier_missing turn_key=%s last_turn_key=%s",
                        companion_evaluation.get("turn_key"),
                        self._state.last_companion_turn_key,
                    )
                except Exception:
                    pass
            print("[sts2_companion_sync:skip] reason=frontend_notifier_missing")
            try:
                self.logger.info("[sts2_push_debug] deliver_catgirl_sync skipped: frontend_notifier missing")
            except Exception:
                pass
            return
        ai_behavior = str(payload.get("ai_behavior") or "respond")
        if self._companion_mode_active() and ai_behavior == "read":
            ai_behavior = "respond"
        push_scene_key = f"{payload.get('screen')}|{payload.get('summary_kind')}|{payload.get('trigger')}"
        push_reason = str(catgirl_sync.get("reason") or "")
        notifier(
            content=self._host_reply_text(str(payload.get("message") or payload.get("summary") or self.t("sync.default", default="尖塔局势已同步。"))),
            description="STS2 catgirl sync",
            metadata={
                "kind": "catgirl_sync",
                "screen": payload.get("screen"),
                "summary_kind": payload.get("summary_kind"),
                "trigger": payload.get("trigger"),
                "strategy": dict(payload.get("strategy") if isinstance(payload.get("strategy"), dict) else {}),
                "player_operation": dict(payload.get("player_operation") if isinstance(payload.get("player_operation"), dict) else {}),
                "player": dict(payload.get("player") if isinstance(payload.get("player"), dict) else {}),
                "enemies": list(payload.get("enemies") if isinstance(payload.get("enemies"), list) else []),
                "cards": list(payload.get("cards") if isinstance(payload.get("cards"), list) else []),
            },
            priority=4,
            message_type="sts2_catgirl_sync",
            visibility=[],
            ai_behavior=ai_behavior,
        )
        trigger = str(companion_evaluation.get("trigger") or payload.get("trigger") or "")
        turn_key = str(companion_evaluation.get("turn_key") or "")
        scene_key = str(companion_evaluation.get("scene_key") or "")
        evaluation_key = str(companion_evaluation.get("evaluation_key") or "")
        if trigger == "combat_turn":
            try:
                self.logger.info(
                    "[sts2_combat_turn_commit] before trigger=%s turn_key=%s last_turn_key=%s eval_key=%s",
                    trigger,
                    turn_key,
                    self._state.last_companion_turn_key,
                    evaluation_key,
                )
            except Exception:
                pass
        if trigger == "combat_turn" and turn_key:
            self._state.last_companion_turn_key = turn_key
            self._state.last_companion_combat_comment_key = turn_key
            self._state.last_companion_scene_key = ""
            self._state.last_companion_evaluation_key = evaluation_key
            try:
                self.logger.info(
                    "[sts2_combat_turn_commit] after trigger=%s turn_key=%s last_turn_key=%s eval_key=%s",
                    trigger,
                    turn_key,
                    self._state.last_companion_turn_key,
                    self._state.last_companion_evaluation_key,
                )
            except Exception:
                pass
        elif trigger == "scene_entry" and scene_key:
            self._state.last_companion_scene_key = scene_key
            self._state.last_companion_evaluation_key = evaluation_key
        self._state.touch_sync()
        self._state.last_sync_fingerprint = str(catgirl_sync.get("fingerprint") or "")
        self._state.last_sync_screen = str(payload.get("screen") or "")
        self._state.last_sync_summary_kind = str(payload.get("summary_kind") or "")
        self._state.last_sync_reason = str(catgirl_sync.get("reason") or "")
        self._state.last_push_scene_key = push_scene_key
        self._state.last_push_reason = push_reason
        self._state.last_push_step_count = self._state.step_count
        self._state.last_push_at = time()
        self._state.sync_repeat_count = 0
        self._state.remember_delivery(
            {
                "kind": "catgirl_sync",
                "screen": payload.get("screen"),
                "summary_kind": payload.get("summary_kind"),
                "synced_at": time(),
            }
        )

    def _host_reply_text(self, text: str, *, limit: int = 30) -> str:
        normalized = " ".join(str(text or "").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3].rstrip() + "..."

    def _push_companion_message(self) -> None:
        if not bool(self._cfg.get("companion_mode_enabled", False)):
            return
        notifier = self._frontend_notifier
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        companion_evaluation = snapshot.get("companion_evaluation") if isinstance(snapshot.get("companion_evaluation"), dict) else {}
        if companion_evaluation and not bool(companion_evaluation.get("should_comment", True)):
            return
        strategy_context = snapshot.get("strategy_context") if isinstance(snapshot.get("strategy_context"), dict) else {}
        strategy_name = str(strategy_context.get("strategy_name") or companion_evaluation.get("strategy_name") or self.t("companion.current_strategy", default="当前策略"))
        commentary = str(companion_evaluation.get("commentary") or "").strip()
        if not commentary:
            commentary = self.t("companion.enabled_default_commentary", default="陪玩已开启，我会给出简短建议。")
        content = self._host_reply_text(
            self.t("companion.enabled_announcement", default="陪玩模式已开启。{strategy_name}：{commentary}", strategy_name=strategy_name, commentary=commentary)
        )
        if notifier is not None:
            notifier(
                content=content,
                description="STS2 companion mode enabled",
                metadata={
                    "kind": "companion_mode_enabled",
                    "delivery_semantics": "passive",
                    "strategy_name": strategy_name,
                    "companion_evaluation": companion_evaluation,
                },
                priority=5,
                message_type="sts2_companion_mode_enabled",
                visibility=["chat"],
                ai_behavior="respond",
            )

    def _emit_status(self) -> None:
        try:
            self._report_status({
                "source": "sts2_autoplay",
                "transport_state": self._state.transport_state,
                "last_error": self._state.last_error,
                "snapshot": self._state.snapshot,
                "standby": self._state.standby,
                "autoplay_state": self._state.autoplay_state,
                "step_count": self._state.step_count,
            })
        except Exception:
            pass
