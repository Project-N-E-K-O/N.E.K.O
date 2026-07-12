"""neko_warthunder —— War Thunder 猫娘副驾驶（M1 框架）。

只读 8112 数据层遥测，把连续数据转成分立战斗事件，按场景仲裁后让猫娘提醒/陪伴。
M1 = 框架链路（轮询 + BattleState + 安全门 + 唯一出口 + 常驻上下文 + dry_run）。
M2 接入 Scenario(D-B1) / Detector(D-B3) / Arbiter(D-B4) 后才真正产出事件。

实现路线见 docs/实现计划-codex.md。
"""

from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from typing import Any

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    message,
    ui,
    Ok,
    Err,
    SdkError,
)

from .adapters.data_layer_process import DataLayerProcessManager
from .adapters.identity_client import identity_summary_from_combat, set_identity as request_set_identity
from .adapters.neko_dispatcher import NekoDispatcher
from .adapters.runtime_timeline import RuntimeTimeline, arbiter_chain_to_observe_records
from .adapters.telemetry_client import TelemetryClient
from .core.arbiter import Arbiter
from .core.contracts import BattleEvent, BattleState, WtConfig
from .core.instructions import WT_CONTEXT_INSTRUCTIONS, WT_RESTORE_INSTRUCTIONS
from .core.safety_guard import SafetyGuard
from .core.scenario import ScenarioResolver
from .detectors._base import DetectorEngine
from .detectors.condition.flight_safety import build_condition_detectors
from .detectors.discrete.lifecycle import build_discrete_detectors

_CONFIG_SECTION = "neko_warthunder"
_RUNTIME_STATE_FILENAME = ".runtime_state.json"
_DIALOGUE_INTRUSION_PRESETS: dict[str, tuple[float, float]] = {
    "no_interrupt": (60.0, 30.0),
    "critical_only": (60.0, 30.0),
    "allow_interrupt": (0.0, 0.0),
}
_DIALOGUE_INTRUSION_ALIASES = {
    "avoid_interrupt": "no_interrupt",
    "protect_chat": "critical_only",
    "balanced": "critical_only",
    "immediate": "allow_interrupt",
}
_DEFERRED_HUD_NOTICE_CODES = frozenset({"powertrain_failure"})
_BLOCKED_FREE_TEXT_SOURCES = {
    "awards": ("free_text_awards", ("awards", "feed")),
    "combat_feed": ("free_text_combat_feed", ("combat", "feed")),
    "hud_notices": ("free_text_hud_notices", ("hud_notices", "feed")),
    "hudmsg": ("free_text_hudmsg", ("hudmsg",)),
    "hud_events": ("free_text_hud_events", ("hud_events",)),
}


@neko_plugin
class NekoWarthunderPlugin(NekoPluginBase):
    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        try:
            self.logger = self.enable_file_logging(log_level="INFO")
        except Exception:  # noqa: BLE001 — 文件日志不可用时退回 ctx.logger
            self.logger = ctx.logger

        self.cfg = WtConfig()
        self._plugin_root = Path(__file__).resolve().parent
        self._runtime_state_path = self._plugin_root / _RUNTIME_STATE_FILENAME
        self.data_layer_manager = DataLayerProcessManager(self.cfg, plugin_root=self._plugin_root)
        self.client = TelemetryClient(self.cfg.data_layer_url, self.cfg.http_timeout_seconds)
        self.safety = SafetyGuard(self.cfg)
        self.timeline = RuntimeTimeline(
            observability_enabled=self.cfg.observability_enabled,
            max_events=self.cfg.observability_max_events,
            include_prompt_preview=self.cfg.observability_include_prompt_preview,
        )
        self.dispatcher = NekoDispatcher(self, timeline=self.timeline)
        self.resolver = ScenarioResolver()
        self.arbiter = Arbiter(self.safety)
        self.engine = self._build_engine()

        self.state = BattleState()
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._instructions_injected = False
        self._status_report_min_interval_seconds = 2.0
        self._last_status_report_at = 0.0
        self._last_status_report_snapshot: dict[str, Any] | None = None
        self._deferred_hud_notice_ids: set[int] = set()
        self._blocked_free_text_sources_seen: set[str] = set()
        self._takeoff_radio_altitude_grace_active = False
        self._last_user_chat_at = 0.0
        self._last_battle_respond_at = 0.0

    # ------------------------------------------------------------------ 配置
    async def _reload_config(self) -> None:
        data: dict[str, Any] = {}
        try:
            dumped = await self.config.dump(timeout=5.0)
            if isinstance(dumped, dict) and isinstance(dumped.get(_CONFIG_SECTION), dict):
                data = dumped[_CONFIG_SECTION]
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"config load failed, using defaults: {type(exc).__name__}")
        runtime_state = self._load_runtime_state()
        if not str(data.get("player_name") or "").strip():
            saved_player_name = str(runtime_state.get("player_name") or "").strip()
            if saved_player_name:
                data["player_name"] = saved_player_name
        saved_dialogue_mode = self._normalize_dialogue_intrusion_mode(runtime_state.get("dialogue_intrusion_mode"))
        if saved_dialogue_mode in _DIALOGUE_INTRUSION_PRESETS:
            user_window, battle_window = _DIALOGUE_INTRUSION_PRESETS[saved_dialogue_mode]
            data["dialogue_intrusion_mode"] = saved_dialogue_mode
            data["user_chat_quiet_window_seconds"] = user_window
            data["battle_output_quiet_window_seconds"] = battle_window
        self._apply_config(WtConfig.from_mapping(data))

    def _apply_config(self, cfg: WtConfig) -> None:
        prev_player = self.cfg.player_name
        self.cfg = cfg
        self.data_layer_manager.configure(cfg)
        self.client = TelemetryClient(cfg.data_layer_url, cfg.http_timeout_seconds)
        self.safety.update(cfg)
        self.timeline.configure(
            observability_enabled=cfg.observability_enabled,
            max_events=cfg.observability_max_events,
            include_prompt_preview=cfg.observability_include_prompt_preview,
        )
        # 仅 player_name 变才重建检测器：否则 dry_run 等配置切换会清零 FSM/_last_id，
        # 导致 combat.feed 里的历史击杀被当新事件重放（Bugbot 反馈）。
        if cfg.player_name != prev_player:
            self.engine = self._build_engine()

    def _build_engine(self) -> DetectorEngine:
        detectors = list(build_condition_detectors()) + list(build_discrete_detectors(self.cfg.player_name))
        return DetectorEngine(detectors)

    # --------------------------------------------------------------- 生命周期
    @lifecycle(id="startup")
    async def startup(self, **_):
        await self._reload_config()
        data_layer_status = self.data_layer_manager.start_if_needed()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="wt-poll")
        self._thread.start()
        self.logger.info(
            f"neko_warthunder started (dry_run={self.cfg.dry_run}, url={self.cfg.data_layer_url}, "
            f"data_layer={data_layer_status.get('mode')})"
        )
        identity_result = self._restore_identity_to_data_layer()
        return Ok(
            {
                "status": "running",
                "dry_run": self.cfg.dry_run,
                "data_layer": data_layer_status,
                "identity": identity_result,
            }
        )

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        data_layer_status = self.data_layer_manager.stop()
        if self._instructions_injected:
            self.dispatcher.push_context(WT_RESTORE_INSTRUCTIONS)
            self._instructions_injected = False
        self.logger.info("neko_warthunder shutdown")
        return Ok({"status": "shutdown", "data_layer": data_layer_status})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        await self._reload_config()
        return Ok({"status": "reloaded", "dry_run": self.cfg.dry_run})

    @message(id="chat_quiet_window", source="chat")
    def on_chat_message(self, **_):
        self._last_user_chat_at = time.time()
        if self.timeline:
            self.timeline.record_stage(
                stage="chat_observed",
                outcome="observed",
                reason="user_chat_quiet_window_started",
                kind="chat",
                source="chat",
                safe_summary="chat/observed",
            )
        return Ok({"status": "observed"})

    async def _persist_identity_name(self, name: str) -> dict[str, Any]:
        persisted_name = str(name or "").strip()
        try:
            self._save_runtime_state({"player_name": persisted_name})
            self._apply_config(WtConfig.from_mapping({**self.cfg.to_dict(), "player_name": persisted_name}))
            return {"ok": True, "player_name": persisted_name}
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"identity local persist failed: {type(exc).__name__}")
            return {"ok": False, "error": f"local persist failed: {type(exc).__name__}"}

    def _load_runtime_state(self) -> dict[str, Any]:
        path = getattr(self, "_runtime_state_path", Path(__file__).resolve().parent / _RUNTIME_STATE_FILENAME)
        try:
            if not path.exists():
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"runtime state load failed: {type(exc).__name__}")
            return {}
        return data if isinstance(data, dict) else {}

    def _save_runtime_state(self, patch: dict[str, Any]) -> None:
        path = getattr(self, "_runtime_state_path", Path(__file__).resolve().parent / _RUNTIME_STATE_FILENAME)
        current = self._load_runtime_state()
        current.update(patch)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _normalize_dialogue_intrusion_mode(value: Any) -> str:
        mode = str(value or "").strip()
        return _DIALOGUE_INTRUSION_ALIASES.get(mode, mode)

    def _dialogue_intrusion_mode(self) -> str:
        configured = self._normalize_dialogue_intrusion_mode(getattr(self.cfg, "dialogue_intrusion_mode", ""))
        if configured in _DIALOGUE_INTRUSION_PRESETS:
            return configured
        user_window = float(getattr(self.cfg, "user_chat_quiet_window_seconds", 0.0) or 0.0)
        battle_window = float(getattr(self.cfg, "battle_output_quiet_window_seconds", 0.0) or 0.0)
        for mode, preset in _DIALOGUE_INTRUSION_PRESETS.items():
            if (user_window, battle_window) == preset:
                return mode
        return "custom"

    def _restore_identity_to_data_layer(self) -> dict[str, Any]:
        name = str(self.cfg.player_name or "").strip()
        if not name:
            return {"ok": True, "restored": False}
        result = request_set_identity(
            self.cfg.data_layer_url,
            self.cfg.http_timeout_seconds,
            name=name,
            clear=False,
        )
        restored = bool(result.get("ok"))
        if restored:
            with self._state_lock:
                combat = dict(self.state.combat or {})
                for key in ("requested", "self", "player_name"):
                    if key in result:
                        combat[key] = result.get(key)
                self.state.combat = combat
        else:
            self.logger.warning("identity restore to data layer failed")
        return {"ok": restored, "restored": restored, "player_name": name if restored else ""}

    # ------------------------------------------------------------------ 轮询
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001 — 轮询异常隔离，不杀循环
                self.logger.warning(f"tick error: {type(exc).__name__}: {exc}")
                self.safety.record_failure()
            self._stop.wait(self.cfg.poll_interval_seconds)

    def _tick(self) -> None:
        if not self.cfg.enabled:
            return
        new_state = self.client.poll()
        self.timeline.mark_tick()
        with self._state_lock:
            prev = self.state
            self.state = new_state
        self._sync_game_context(prev, new_state)
        self._evaluate(prev, new_state)
        self._report()

    @staticmethod
    def _game_context_should_be_active(s: BattleState) -> bool:
        return bool(s.connected and str(s.conn_state or "").lower() != "offline")

    def _sync_game_context(self, prev: BattleState, cur: BattleState) -> None:
        was_active = self._game_context_should_be_active(prev)
        is_active = self._game_context_should_be_active(cur)
        if is_active and not self._instructions_injected:
            self.dispatcher.push_context(WT_CONTEXT_INSTRUCTIONS)
            self._instructions_injected = True
            self.timeline.record_stage(
                stage="game_context_entered",
                outcome="entered",
                reason="telemetry_online",
                connected=cur.connected,
                conn_state=cur.conn_state,
                in_battle=cur.in_battle,
                safe_summary="war_thunder_context/entered",
            )
            return
        if (not is_active) and self._instructions_injected and was_active:
            self.dispatcher.push_context(WT_RESTORE_INSTRUCTIONS)
            self._instructions_injected = False
            self.timeline.record_stage(
                stage="game_context_exited",
                outcome="exited",
                reason="telemetry_offline",
                connected=cur.connected,
                conn_state=cur.conn_state,
                in_battle=cur.in_battle,
                safe_summary="war_thunder_context/exited",
            )

    def _evaluate(self, prev: BattleState, cur: BattleState) -> None:
        """Scenario(D-B1) + Detector(D-B3) → 候选 → Arbiter(D-B4) → dispatcher。"""
        now = time.time()
        cur.scenario = self.resolver.resolve(cur, now, self.cfg.spawn_grace_seconds)
        candidates = self.engine.feed(prev, cur)
        if cur.replay:
            self.timeline.record_stage(
                stage="detector_suppressed",
                outcome="suppressed",
                reason="replay",
                scenario=cur.scenario,
                in_battle=cur.in_battle,
                replay=True,
                dry_run=self.cfg.dry_run,
                safe_summary="replay telemetry suppressed",
            )
            self.timeline.record_decision(
                event_id=None,
                stage="detector_suppressed",
                outcome="suppressed",
                reason="replay",
                scenario=cur.scenario,
                safety_status=self.safety.status(),
                dry_run=self.cfg.dry_run,
            )
            return
        self._record_blocked_free_text_sources(cur)
        self._record_deferred_hud_notices(cur)
        candidates = self._suppress_takeoff_grace(candidates, cur, now)
        candidates = self._annotate_runtime_context(candidates, cur, now)
        for candidate in candidates:
            self.timeline.record_stage(
                stage="detector_candidate",
                outcome="candidate",
                reason="detected",
                event_id=candidate.event_id,
                edge=candidate.edge,
                level=candidate.level,
                priority=candidate.priority,
                scenario=cur.scenario,
                in_battle=cur.in_battle,
                replay=cur.replay,
                safe_summary=f"{candidate.event_id}/{candidate.edge}/{candidate.level}",
            )
        chosen, chain = self.arbiter.decide(candidates, cur.scenario, now)
        for record in arbiter_chain_to_observe_records(chain, scenario=cur.scenario):
            self.timeline.record_stage(**record)
            self.timeline.record_decision(
                event_id=record.get("event_id"),
                stage=record.get("stage", "arbiter_dropped"),
                outcome=record.get("outcome", "dropped"),
                reason=record.get("reason", "unknown"),
                scenario=cur.scenario,
                safety_status=self.safety.status(),
                dry_run=self.cfg.dry_run,
            )
        if candidates or chosen is not None:
            self.logger.info(f"[arbiter] scenario={cur.scenario} chain={chain}")
        if chosen is not None:
            try:
                result = self.dispatcher.push_event(chosen, dry_run=self.cfg.dry_run)
                self.logger.info(f"[output] {result}")
            except Exception as exc:  # noqa: BLE001 — 投递失败计入安全门，不杀循环
                self.logger.warning(f"dispatch failed: {type(exc).__name__}: {exc}")
                self.safety.record_failure(now)

    def _annotate_runtime_context(
        self,
        candidates: list[BattleEvent],
        cur: BattleState,
        now: float,
    ) -> list[BattleEvent]:
        stress_reasons = sorted(self.resolver.current_stress_reasons(now))
        if not stress_reasons:
            return candidates
        annotated: list[BattleEvent] = []
        for candidate in candidates:
            if candidate.event_id != "you_killed":
                annotated.append(candidate)
                continue
            payload = dict(candidate.payload)
            payload.setdefault("domain", cur.domain)
            payload["stress_reasons"] = stress_reasons
            payload["scenario_at_detect"] = cur.scenario
            annotated.append(
                BattleEvent(
                    candidate.event_id,
                    edge=candidate.edge,
                    payload=payload,
                    ts=candidate.ts,
                    level=candidate.level,
                )
            )
        return annotated

    def _record_blocked_free_text_sources(self, cur: BattleState) -> None:
        if not cur.in_battle:
            self._blocked_free_text_sources_seen.clear()
            return

        seen = getattr(self, "_blocked_free_text_sources_seen", None)
        if seen is None:
            seen = set()
            self._blocked_free_text_sources_seen = seen

        for source, (event_id, path) in _BLOCKED_FREE_TEXT_SOURCES.items():
            if source in seen:
                continue
            count = self._free_text_source_count(cur, path)
            if count <= 0:
                continue
            seen.add(source)
            self.timeline.record_stage(
                stage="detector_suppressed",
                outcome="suppressed",
                reason="free_text_blocked",
                event_id=event_id,
                scenario=cur.scenario,
                in_battle=cur.in_battle,
                replay=cur.replay,
                dry_run=self.cfg.dry_run,
                source=source,
                safe_summary=f"free_text/{source}/blocked/{count}",
            )
            self.timeline.record_decision(
                event_id=event_id,
                stage="detector_suppressed",
                outcome="suppressed",
                reason="free_text_blocked",
                scenario=cur.scenario,
                safety_status=self.safety.status(),
                dry_run=self.cfg.dry_run,
            )

    @staticmethod
    def _free_text_source_count(cur: BattleState, path: tuple[str, ...]) -> int:
        value: Any = cur.raw
        for key in path:
            if not isinstance(value, dict):
                return 0
            value = value.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, dict):
            nested = value.get("feed")
            if isinstance(nested, list):
                return len(nested)
            return 1 if value else 0
        if isinstance(value, str):
            return 1 if value.strip() else 0
        return 0

    def _record_deferred_hud_notices(self, cur: BattleState) -> None:
        if not cur.is_alive():
            return
        seen_ids = getattr(self, "_deferred_hud_notice_ids", None)
        if seen_ids is None:
            seen_ids = set()
            self._deferred_hud_notice_ids = seen_ids

        for item in cur.hud_notices:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "")
            if code not in _DEFERRED_HUD_NOTICE_CODES:
                continue
            try:
                notice_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if notice_id in seen_ids:
                continue
            seen_ids.add(notice_id)
            level = "critical" if str(item.get("level") or item.get("severity") or "").lower() == "critical" else "warning"
            self.timeline.record_stage(
                stage="detector_suppressed",
                outcome="suppressed",
                reason="deferred_hud_notice",
                event_id=code,
                level=level,
                scenario=cur.scenario,
                in_battle=cur.in_battle,
                replay=cur.replay,
                dry_run=self.cfg.dry_run,
                safe_summary=f"hud_notice/{code}/deferred",
            )
            self.timeline.record_decision(
                event_id=code,
                stage="detector_suppressed",
                outcome="suppressed",
                reason="deferred_hud_notice",
                scenario=cur.scenario,
                safety_status=self.safety.status(),
                dry_run=self.cfg.dry_run,
            )

    def _takeoff_radio_altitude_grace_active_for(self, cur: BattleState, now: float) -> bool:
        radio_alt = cur.radio_altitude_m
        if (cur.domain or "").lower() != "air":
            self._takeoff_radio_altitude_grace_active = False
            return False
        if radio_alt is None or not cur.in_battle or not cur.vehicle_valid or cur.dead:
            self._takeoff_radio_altitude_grace_active = False
            return False

        enter_m = float(getattr(self.cfg, "takeoff_radio_altitude_enter_m", 10.0) or 0.0)
        exit_m = float(getattr(self.cfg, "takeoff_radio_altitude_exit_m", 40.0) or 0.0)
        if exit_m < enter_m:
            exit_m = enter_m

        active = bool(getattr(self, "_takeoff_radio_altitude_grace_active", False))
        if active:
            if radio_alt >= exit_m:
                self._takeoff_radio_altitude_grace_active = False
            return self._takeoff_radio_altitude_grace_active

        grace = float(getattr(self.cfg, "takeoff_low_alt_grace_seconds", 0.0) or 0.0)
        elapsed = self.resolver.seconds_since_spawn(now)
        if grace > 0 and elapsed is not None and elapsed < grace and radio_alt <= enter_m:
            self._takeoff_radio_altitude_grace_active = True
        return self._takeoff_radio_altitude_grace_active

    @staticmethod
    def _takeoff_gear_down_or_moving(cur: BattleState) -> bool:
        raw = cur.raw if isinstance(cur.raw, dict) else {}
        indicators = raw.get("indicators") if isinstance(raw.get("indicators"), dict) else {}
        gear_state = indicators.get("gear_state")
        if gear_state in {"down", "moving"}:
            return True
        for key in ("gears", "gear_pct", "gear, %"):
            try:
                value = indicators.get(key)
                if value is not None and float(value) > 0.5:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def _suppress_takeoff_grace(
        self,
        candidates: list[BattleEvent],
        cur: BattleState,
        now: float,
    ) -> list[BattleEvent]:
        if (cur.domain or "").lower() != "air":
            self._takeoff_radio_altitude_grace_active = False
            return candidates
        grace = float(getattr(self.cfg, "takeoff_low_alt_grace_seconds", 0.0) or 0.0)
        radio_grace_active = self._takeoff_radio_altitude_grace_active_for(cur, now)
        if grace <= 0 and not radio_grace_active:
            return candidates
        if not cur.in_battle or not cur.vehicle_valid or cur.dead:
            return candidates
        elapsed = self.resolver.seconds_since_spawn(now)
        time_grace_active = elapsed is not None and elapsed < grace
        runway_grace_active = time_grace_active and self._takeoff_gear_down_or_moving(cur)
        if not time_grace_active and not radio_grace_active:
            return candidates

        kept: list[BattleEvent] = []
        for candidate in candidates:
            suppress_low_alt = candidate.event_id == "low_alt_danger" and (time_grace_active or radio_grace_active)
            suppress_overspeed = candidate.event_id == "overspeed" and (radio_grace_active or runway_grace_active)
            if not (suppress_low_alt or suppress_overspeed):
                kept.append(candidate)
                continue
            if suppress_low_alt:
                reason = "takeoff_low_alt_grace"
            elif radio_grace_active:
                reason = "takeoff_radio_altitude_grace"
            else:
                reason = "takeoff_runway_grace"
            self.timeline.record_stage(
                stage="detector_suppressed",
                outcome="suppressed",
                reason=reason,
                event_id=candidate.event_id,
                edge=candidate.edge,
                level=candidate.level,
                priority=candidate.priority,
                scenario=cur.scenario,
                in_battle=cur.in_battle,
                replay=cur.replay,
                dry_run=self.cfg.dry_run,
                safe_summary=f"{candidate.event_id}/{candidate.edge}/{candidate.level}",
            )
            self.timeline.record_decision(
                event_id=candidate.event_id,
                stage="detector_suppressed",
                outcome="suppressed",
                reason=reason,
                scenario=cur.scenario,
                safety_status=self.safety.status(),
                dry_run=self.cfg.dry_run,
            )
        return kept

    def _status_report_snapshot(self, s: BattleState) -> dict[str, Any]:
        return {
            "connected": s.connected,
            "conn_state": s.conn_state,
            "in_battle": s.in_battle,
            "scenario": s.scenario,
            "level": s.level,
            "dry_run": self.cfg.dry_run,
            "game_context_active": bool(getattr(self, "_instructions_injected", False)),
            "safety": self.safety.status(),
        }

    def _report(self, now: float | None = None) -> None:
        with self._state_lock:
            s = self.state
        snapshot = self._status_report_snapshot(s)
        now = time.monotonic() if now is None else now
        if (
            snapshot == self._last_status_report_snapshot
            and now - self._last_status_report_at < self._status_report_min_interval_seconds
        ):
            return
        try:
            self.report_status(snapshot)
            self._last_status_report_snapshot = snapshot
            self._last_status_report_at = now
        except Exception:  # noqa: BLE001
            self._last_status_report_at = now
            pass

    def _telemetry_snapshot(self, s: BattleState) -> dict[str, Any]:
        return {
            "age_seconds": s.age_seconds,
            "ias_kmh": s.ias_kmh,
            "mach": s.mach,
            "altitude_m": s.altitude_m,
            "radio_altitude_m": s.radio_altitude_m,
            "climb_ms": s.climb_ms,
            "fuel_fraction": s.fuel_fraction,
            "level": s.level,
            "flags": dict(sorted((s.flags or {}).items())),
        }

    def _takeoff_protection_snapshot(self, s: BattleState) -> dict[str, Any]:
        radio_altitude_m = s.radio_altitude_m
        is_air = (s.domain or "").lower() == "air"
        radio_active = is_air and bool(getattr(self, "_takeoff_radio_altitude_grace_active", False))
        resolver = getattr(self, "resolver", None)
        elapsed = resolver.seconds_since_spawn(time.time()) if resolver is not None else None
        time_active = (
            is_air
            and elapsed is not None
            and elapsed < float(getattr(self.cfg, "takeoff_low_alt_grace_seconds", 0.0) or 0.0)
            and s.in_battle
            and s.vehicle_valid
            and not s.dead
        )
        runway_active = time_active and self._takeoff_gear_down_or_moving(s)
        active = radio_active or runway_active
        suppresses = ["low_alt_danger"] if time_active or radio_active else []
        if radio_active or runway_active:
            suppresses.append("overspeed")
        return {
            "active": active,
            "radio_altitude_m": radio_altitude_m,
            "radio_altitude_available": radio_altitude_m is not None,
            "runway_grace_active": runway_active,
            "gear_down_or_moving": self._takeoff_gear_down_or_moving(s),
            "enter_m": self.cfg.takeoff_radio_altitude_enter_m,
            "exit_m": self.cfg.takeoff_radio_altitude_exit_m,
            "low_alt_grace_seconds": self.cfg.takeoff_low_alt_grace_seconds,
            "suppresses": suppresses,
        }

    def _awareness_snapshot(self, s: BattleState) -> dict[str, Any]:
        events = [item for item in s.proximity_events if isinstance(item, dict)]
        latest = events[-1] if events else {}
        situation = s.situation if isinstance(s.situation, dict) else {}
        ground_targets = [item for item in situation.get("ground_targets", []) if isinstance(item, dict)]
        nearest_ground_target = ground_targets[0] if ground_targets else {}
        return {
            "proximity_event_count": len(events),
            "latest_proximity": {
                "kind": latest.get("kind"),
                "target_type": latest.get("type"),
                "category": latest.get("category"),
                "is_air": latest.get("is_air"),
                "distance_m": latest.get("distance_m"),
                "compass": latest.get("compass"),
                "clock": latest.get("clock"),
            }
            if latest
            else None,
            "situation": {
                "has_player": situation.get("has_player"),
                "enemy_count": situation.get("enemy_count"),
                "ally_count": situation.get("ally_count"),
                "air_threat_count": situation.get("air_threat_count"),
                "ground_target_count": len(ground_targets),
            },
            "nearest_ground_target": {
                "kind": nearest_ground_target.get("kind"),
                "grid": nearest_ground_target.get("grid"),
                "distance_m": nearest_ground_target.get("distance_m"),
                "bearing_deg": nearest_ground_target.get("bearing_deg"),
                "relative_deg": nearest_ground_target.get("relative_deg"),
            }
            if nearest_ground_target
            else None,
        }

    def _dashboard_payload(self, s: BattleState) -> dict[str, Any]:
        identity = identity_summary_from_combat(s.combat)
        saved_player_name = str(self.cfg.player_name or "").strip()
        if saved_player_name:
            identity["saved_player_name"] = saved_player_name
            identity["player_name"] = identity.get("player_name") or saved_player_name
        return {
            "enabled": self.cfg.enabled,
            "dry_run": self.cfg.dry_run,
            "connected": s.connected,
            "conn_state": s.conn_state,
            "in_battle": s.in_battle,
            "game_context_active": bool(getattr(self, "_instructions_injected", False)),
            "dead": s.dead,
            "domain": s.domain,
            "domain_label": s.domain_label,
            "vehicle_type": s.vehicle_type,
            "profile_matched": s.profile_matched,
            "profile_source": s.profile_source,
            "profile_family": s.profile_family,
            "scenario": s.scenario,
            "level": s.level,
            "identity": identity,
            "data_layer": self.data_layer_manager.snapshot(),
            "telemetry": self._telemetry_snapshot(s),
            "takeoff_protection": self._takeoff_protection_snapshot(s),
            "output_policy": {
                "v2_live_verified_real_output_enabled": self.cfg.v2_live_verified_real_output_enabled,
                "v2_live_evidence_gated_events": ["enemy_on_six", "tailing_risk", "ground_target_nearby"],
                "dialogue_intrusion_mode": self._dialogue_intrusion_mode(),
                "user_chat_quiet_window_seconds": self.cfg.user_chat_quiet_window_seconds,
                "battle_output_quiet_window_seconds": self.cfg.battle_output_quiet_window_seconds,
                "critical_bypass_quiet_window": self._dialogue_intrusion_mode() != "no_interrupt",
            },
            "awareness": self._awareness_snapshot(s),
            "safety": self.safety.snapshot(),
            "observe": self.timeline.snapshot(),
        }

    # -------------------------------------------------------------- Hosted UI
    @ui.context(id="dashboard", title="战雷猫娘副驾驶")
    async def dashboard_context(self):
        with self._state_lock:
            s = self.state
        return self._dashboard_payload(s)

    # ------------------------------------------------------------------ 动作
    @ui.action(id="set_dry_run", label="设置 dry_run", tone="primary", group="runtime", order=10, refresh_context=True)
    @plugin_entry(
        id="set_dry_run",
        name="设置 dry_run",
        description="开/关 dry_run（开=只跑链路不真投给猫娘）。",
        input_schema={"type": "object", "properties": {"value": {"type": "boolean", "default": True}}},
    )
    async def set_dry_run(self, value: bool = True, **_):
        self.cfg.dry_run = bool(value)
        return Ok({"dry_run": self.cfg.dry_run})

    @ui.action(id="set_dialogue_intrusion_mode", label="设置插话策略", tone="primary", group="runtime", order=15, refresh_context=True)
    @plugin_entry(
        id="set_dialogue_intrusion_mode",
        name="设置插话策略",
        description="选择战斗播报是否打断当前对话。死亡和 critical 安全告警始终允许穿透。",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["no_interrupt", "critical_only", "allow_interrupt"],
                    "default": "critical_only",
                }
            },
        },
    )
    async def set_dialogue_intrusion_mode(self, mode: str = "critical_only", **_):
        selected = self._normalize_dialogue_intrusion_mode(mode)
        if selected not in _DIALOGUE_INTRUSION_PRESETS:
            return Err(SdkError("unknown dialogue intrusion mode"))
        user_window, battle_window = _DIALOGUE_INTRUSION_PRESETS[selected]
        self.cfg.dialogue_intrusion_mode = selected
        self.cfg.user_chat_quiet_window_seconds = user_window
        self.cfg.battle_output_quiet_window_seconds = battle_window
        self._save_runtime_state(
            {
                "dialogue_intrusion_mode": selected,
                "user_chat_quiet_window_seconds": user_window,
                "battle_output_quiet_window_seconds": battle_window,
            }
        )
        return Ok(
            {
                "mode": selected,
                "user_chat_quiet_window_seconds": user_window,
                "battle_output_quiet_window_seconds": battle_window,
                "critical_bypass_quiet_window": selected != "no_interrupt",
            }
        )

    @ui.action(id="pause", label="急停", tone="danger", group="runtime", order=20, refresh_context=True)
    @plugin_entry(id="pause", name="急停", description="暂停所有提醒输出。")
    async def pause(self, **_):
        self.safety.pause()
        return Ok({"safety": self.safety.status()})

    @ui.action(id="resume", label="恢复", tone="success", group="runtime", order=30, refresh_context=True)
    @plugin_entry(id="resume", name="恢复", description="恢复提醒输出并清空安全计数。")
    async def resume(self, **_):
        self.safety.resume()
        return Ok({"safety": self.safety.status()})

    @ui.action(id="test_say", label="测试开口", tone="info", group="diagnostics", order=40, refresh_context=False)
    @plugin_entry(
        id="test_say",
        name="测试开口",
        description="在 dry_run=false 且未暂停时推一条测试消息给猫娘，验证 push 链路。",
        input_schema={"type": "object", "properties": {"text": {"type": "string", "default": "副驾驶测试：能听到我吗？"}}},
    )
    async def test_say(self, text: str = "副驾驶测试：能听到我吗？", **_):
        if self.cfg.dry_run:
            if getattr(self, "timeline", None):
                self.timeline.record_stage(
                    stage="test_say_blocked",
                    outcome="blocked",
                    reason="dry_run",
                    kind="test_say",
                    ai_behavior="respond",
                    pushed=False,
                    dry_run=True,
                    safe_summary="test_say/dry_run",
                )
            return Ok({"pushed": False, "blocked": "dry_run", "text": str(text)})
        if self.safety.stopped:
            if getattr(self, "timeline", None):
                self.timeline.record_stage(
                    stage="test_say_blocked",
                    outcome="blocked",
                    reason=self.safety.status(),
                    kind="test_say",
                    ai_behavior="respond",
                    pushed=False,
                    dry_run=False,
                    safe_summary=f"test_say/{self.safety.status()}",
                )
            return Ok({"pushed": False, "blocked": self.safety.status(), "text": str(text)})
        try:
            self.push_message(
                source="neko_warthunder",
                visibility=[],
                ai_behavior="respond",
                parts=[{"type": "text", "text": str(text)}],
                priority=5,
                metadata={"plugin": "neko_warthunder", "kind": "test"},
            )
            if getattr(self, "timeline", None):
                self.timeline.record_stage(
                    stage="test_say_pushed",
                    outcome="pushed",
                    reason="push_message_accepted",
                    kind="test_say",
                    ai_behavior="respond",
                    pushed=True,
                    dry_run=False,
                    safe_summary="test_say/respond",
                )
            return Ok({"pushed": True, "text": str(text)})
        except Exception as exc:  # noqa: BLE001
            if getattr(self, "timeline", None):
                self.timeline.record_stage(
                    stage="test_say_failed",
                    outcome="failed",
                    reason=type(exc).__name__,
                    kind="test_say",
                    ai_behavior="respond",
                    pushed=False,
                    dry_run=False,
                )
            return Err(SdkError(f"test_say push failed: {exc}"))

    @ui.action(id="set_identity", label="设置玩家名", tone="primary", group="runtime", order=50, refresh_context=True)
    @plugin_entry(
        id="set_identity",
        name="设置玩家名",
        description="通过数据层 /api/identity 设置或清除本局自己的玩家名，用于 kill/death 归属。",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "default": ""},
                "clear": {"type": "boolean", "default": False},
            },
        },
    )
    async def set_identity(self, name: str = "", clear: bool = False, **_):
        requested_name = "" if clear else str(name or "").strip()
        result = request_set_identity(
            self.cfg.data_layer_url,
            self.cfg.http_timeout_seconds,
            name=requested_name,
            clear=bool(clear),
        )
        if result.get("ok"):
            persist_result = await self._persist_identity_name(requested_name)
            result["persisted"] = persist_result.get("ok", False)
            if persist_result.get("error"):
                result["persist_error"] = persist_result.get("error")
            with self._state_lock:
                combat = dict(self.state.combat or {})
                for key in ("requested", "self", "player_name"):
                    if key in result:
                        combat[key] = result.get(key)
                self.state.combat = combat
        return Ok({"identity": result})

    @plugin_entry(id="status", name="状态", description="查看当前连接/场景/安全状态。")
    def status(self, **_):
        with self._state_lock:
            s = self.state
        return Ok(self._dashboard_payload(s))
