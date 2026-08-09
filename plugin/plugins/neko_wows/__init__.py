"""World of Warships companion plugin.

Reads telemetry from a local `8111_for_wows` service, turns consecutive frames
into discrete battle events, arbitrates one call-out per round, and hands it to
the character to word herself.

Read-only by construction: it polls an HTTP service, never touches game memory
and never sends input to the game.

Pipeline:

    transport -> SchemaAdapter -> WowsSnapshot -> FactBuilder
              -> DetectorRegistry -> TacticPolicy -> Arbiter
              -> PromptRouter -> OutputPort

`dry_run` defaults to off so battle call-outs reach the character. Turn it on
from the panel when you only want to inspect the event chain without speaking.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    message,
    neko_plugin,
    plugin_entry,
    ui,
    unwrap_or,
)

from .adapters.neko_dispatcher import ContextInjector, NekoDispatcher
from .adapters.runtime_timeline import (
    STAGE_ARBITER,
    STAGE_DELIVERY,
    STAGE_DETECT,
    STAGE_DOCUMENTS,
    STAGE_FRAME,
    STAGE_PROMPTS,
    STAGE_SERVICE,
    STAGE_SHIP_CATALOG,
    RuntimeTimeline,
)
from .adapters.schema_adapter import (
    UnexpectedServiceIdentity,
    UnsupportedApiVersion,
    WowsSchemaAdapter,
)
from .adapters.service_manager import WowsServiceManager
from .adapters.transport import CursorGate, RawFrame, TelemetryTransport
from .detectors._base import DetectorRegistry
from .detectors.geometry import build_geometry_detectors
from .detectors.lifecycle import build_lifecycle_detectors
from .detectors.survival import build_survival_detectors
from .detectors.targeting import build_targeting_detectors
from .detectors.threat import build_threat_detectors
from .domain.catalog import DAMAGE_MILESTONE, LOW_HEALTH, spec_for
from .domain.contracts import (
    ALL_CATEGORIES,
    ALL_CHANNEL_MODES,
    ALL_INTRUSION_MODES,
    ALL_LANES,
    ALL_OFFICIAL_API_REGIONS,
    INTRUSION_CRITICAL_ONLY,
    LANE_URGENT,
    OFFICIAL_API_REGION_ASIA,
    NullTacticsRepository,
    TacticQuery,
    WowsConfig,
)
from .domain.facts import FactBuilder
from .domain.snapshot import STATUS_ENDED
from .knowledge.importer import DocumentImporter, DocumentRejected
from .knowledge.retrieval import WowsTacticsRepository
from .knowledge.store import KnowledgeStore
from .presentation.instructions import (
    DEFAULT_BUNDLE,
    MAX_SECTION_CHARS,
    WOWS_RESTORE_INSTRUCTIONS,
    PromptBundle,
    PromptRejected,
    bundle_from_revision,
    context_instructions,
    validate_sections,
)
from .presentation.prompt_router import PromptProfile, WowsPromptRouter
from .policy.arbiter import Arbiter, REASON_CHOSEN
from .policy.tactic_policy import AdviceCandidate, WowsTacticPolicy
from .ship_data.context import BattleShipContextManager, ContextObservation
from .ship_data.official_api import (
    OFFICIAL_LANGUAGES,
    OfficialWowsApiClient,
    official_error,
)
from .ship_data.resolver import ShipResolver
from .ship_data.store import ShipCatalogStore
from .vision.store import ShotStore
from .vision.tool import ScreenshotService, facts_to_telemetry

CONFIG_SECTION = "neko_wows"
KNOWLEDGE_DB_NAME = "tactical_knowledge.db"
SCREENSHOT_DIR_NAME = "screenshots"

STORE_CHANNEL_MODE = "channel_mode"
STORE_INTRUSION_SETTINGS = "intrusion_settings"
STORE_INTRUSION_MODE = "dialogue_intrusion_mode"
STORE_QUIET_WINDOW = "user_chat_quiet_window_seconds"
STORE_DISABLED_CATEGORIES = "disabled_categories"
STORE_DISABLED_LANES = "disabled_lanes"
STORE_OFFICIAL_API_SETTINGS = "official_api_settings"
STORE_CONNECTION_SETTINGS = "connection_settings"
STORE_SCREENSHOT_SETTINGS = "screenshot_settings"

# Config keys that describe *where* the data comes from. Changing any of them
# needs an explicit reconnect: silently tearing down a live link mid-battle would
# lose the detector baselines the user is currently relying on.
_CONNECTION_KEYS = (
    "service_url",
    "service_source_dir",
    "game_dir",
    "service_auto_start",
)

OFFICIAL_SHIP_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "ship": {
            "type": "string",
            "description": "精确舰名、目录别名或十进制官方 ship ID。",
        },
        "configuration": {
            "type": "string",
            "enum": ["top"],
            "default": "top",
            "description": "首期仅支持顶配参考。",
        },
        "language": {
            "type": "string",
            "description": "可选官方 API 语言代码；省略时使用插件配置。",
        },
    },
    "required": ["ship"],
    "additionalProperties": False,
}


@neko_plugin
class NekoWowsPlugin(NekoPluginBase):
    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.cfg = WowsConfig()
        self._state_lock = threading.RLock()
        # Preference actions await durable storage. Serialize those transactions
        # without holding the transport-thread pipeline lock across an await.
        self._preference_lock = asyncio.Lock()
        # Serializes one whole frame against configuration changes and pipeline
        # resets. No frame may observe a mixture of old detectors and new output
        # policy, especially while dry-run is being changed.
        self._pipeline_lock = threading.RLock()

        self.timeline = RuntimeTimeline(self.cfg.observability_max_events)
        self.service = WowsServiceManager(
            self.cfg, logger=self.logger, log_dir=self.data_path("service_logs"))
        self.adapter = WowsSchemaAdapter()
        self.gate = CursorGate()
        self.facts = FactBuilder(self.cfg)
        self.registry = self._build_registry()
        self.policy = WowsTacticPolicy(self.cfg)
        self.arbiter = Arbiter(self.cfg)
        self.router = WowsPromptRouter(self.cfg)
        self.dispatcher = NekoDispatcher(self, self.cfg, logger=self.logger)
        self.context_injector = ContextInjector(self, logger=self.logger)
        self.ship_catalog_store = ShipCatalogStore(
            self.data_path("ship_catalog"), logger=self.logger)
        self.ship_context = BattleShipContextManager(
            self,
            self.ship_catalog_store,
            self.cfg,
            logger=self.logger,
        )
        self.official_api = OfficialWowsApiClient(
            application_id=self.cfg.official_api_application_id,
            region=self.cfg.official_api_region,
            timeout_seconds=self.cfg.official_api_timeout_seconds,
            cache_ttl_seconds=self.cfg.official_api_cache_ttl_seconds,
        )
        self.shots = ShotStore(
            self.data_path(SCREENSHOT_DIR_NAME),
            self.cfg.screenshot_retain_count,
            logger=self.logger,
        )
        self.screenshots = ScreenshotService(
            self.cfg, self.shots, self._telemetry_snapshot, logger=self.logger)

        # A plain sqlite3 store rather than the SDK's async `self.db`: retrieval
        # happens inside `_evaluate` on the transport thread, which cannot await.
        self.knowledge = KnowledgeStore(
            self.data_path(KNOWLEDGE_DB_NAME), logger=self.logger)
        self.importer = DocumentImporter(self.knowledge, self.cfg)
        self.tactics: Any = NullTacticsRepository()

        self.transport = TelemetryTransport(
            self.cfg, self._on_frame, logger=self.logger,
            on_stall=self._supervise_service)

        self._previous: tuple | None = None
        self._latest: tuple | None = None
        self._frames_seen = 0
        self._events_seen = 0
        self._reconnect_required = False
        self._running = False
        self._prompt_bundle = DEFAULT_BUNDLE
        self._last_candidate = None
        self._service_signature: tuple[str, str] | None = None
        self._blocked_signature: tuple[tuple[str, tuple[str, ...]], ...] = ()

    # ------------------------------------------------------------------ 配置
    def _build_registry(self) -> DetectorRegistry:
        return DetectorRegistry((
            *build_lifecycle_detectors(self.cfg),
            *build_survival_detectors(self.cfg),
            *build_threat_detectors(self.cfg),
            *build_geometry_detectors(self.cfg),
            *build_targeting_detectors(self.cfg),
        ))

    async def _reload_config(self, *, force_dry_run: bool = False) -> WowsConfig:
        async with self._preference_lock:
            try:
                raw = await self.config.dump()
            except Exception as exc:
                self.logger.warning(f"config read failed, keeping dry_run on: {exc}")
                raw = {}
            section = raw.get(CONFIG_SECTION) if isinstance(raw, dict) else None
            cfg = WowsConfig.from_mapping(
                section if isinstance(section, dict) else None)

            # Panel-set preferences win over the TOML defaults; `dry_run` is
            # deliberately not among them (session-only via the panel).
            await self._apply_stored_preferences(cfg)
            with self._pipeline_lock:
                # Startup takes `dry_run` from TOML and screenshot settings from
                # TOML+store. Hot reload keeps the panel's session choices for
                # dry-run and the screenshot switch.
                if not force_dry_run:
                    cfg.dry_run = bool(self.cfg.dry_run)
                    cfg.screenshot_enabled = bool(self.cfg.screenshot_enabled)
                self._apply_config(cfg)
            return cfg

    async def _apply_stored_preferences(self, cfg: WowsConfig) -> None:
        mode = await self._stored(STORE_CHANNEL_MODE)
        if mode in ALL_CHANNEL_MODES:
            cfg.channel_mode = mode
        intrusion_settings = await self._stored(STORE_INTRUSION_SETTINGS)
        atomic_intrusion = (
            isinstance(intrusion_settings, dict)
            and intrusion_settings.get("mode") in ALL_INTRUSION_MODES
            and isinstance(
                intrusion_settings.get("quiet_window_seconds"), (int, float))
            and not isinstance(
                intrusion_settings.get("quiet_window_seconds"), bool)
        )
        if atomic_intrusion:
            cfg.dialogue_intrusion_mode = intrusion_settings["mode"]
            cfg.user_chat_quiet_window_seconds = max(
                0.0, min(1800.0, float(
                    intrusion_settings["quiet_window_seconds"])))
        else:
            # Read the old split keys so upgrades preserve existing preferences.
            intrusion = await self._stored(STORE_INTRUSION_MODE)
            if intrusion in ALL_INTRUSION_MODES:
                cfg.dialogue_intrusion_mode = intrusion
            quiet = await self._stored(STORE_QUIET_WINDOW)
            if isinstance(quiet, (int, float)) and not isinstance(quiet, bool):
                cfg.user_chat_quiet_window_seconds = max(
                    0.0, min(1800.0, float(quiet)))
        categories = await self._stored(STORE_DISABLED_CATEGORIES)
        if isinstance(categories, list):
            cfg.disabled_categories = tuple(
                value for value in categories if value in ALL_CATEGORIES)
        lanes = await self._stored(STORE_DISABLED_LANES)
        if isinstance(lanes, list):
            cfg.disabled_lanes = tuple(
                value for value in lanes if value in ALL_LANES)
        official = await self._stored(STORE_OFFICIAL_API_SETTINGS)
        if isinstance(official, dict):
            if isinstance(official.get("enabled"), bool):
                cfg.official_api_enabled = official["enabled"]
            region = official.get("region")
            if isinstance(region, str) and region in ALL_OFFICIAL_API_REGIONS:
                cfg.official_api_region = region
            if isinstance(official.get("application_id"), str):
                cfg.official_api_application_id = official["application_id"].strip()
        connection = await self._stored(STORE_CONNECTION_SETTINGS)
        if isinstance(connection, dict):
            url = connection.get("service_url")
            if isinstance(url, str) and url.strip():
                cfg.service_url = url.strip().rstrip("/")
            if isinstance(connection.get("service_source_dir"), str):
                cfg.service_source_dir = connection["service_source_dir"].strip()
            if isinstance(connection.get("game_dir"), str):
                cfg.game_dir = connection["game_dir"].strip()
        screenshot = await self._stored(STORE_SCREENSHOT_SETTINGS)
        if isinstance(screenshot, dict):
            if isinstance(screenshot.get("enabled"), bool):
                cfg.screenshot_enabled = screenshot["enabled"]
            interval = screenshot.get("min_interval_seconds")
            if isinstance(interval, (int, float)) and not isinstance(interval, bool):
                cfg.screenshot_min_interval_seconds = max(
                    0.0, min(600.0, float(interval)))
            retain = screenshot.get("retain_count")
            if isinstance(retain, (int, float)) and not isinstance(retain, bool):
                cfg.screenshot_retain_count = int(max(1, min(100, float(retain))))

    async def _stored(self, key: str):
        try:
            return unwrap_or(await self.store.get(key), None)
        except Exception:
            return None

    def _apply_config(self, cfg: WowsConfig) -> None:
        with self._pipeline_lock:
            with self._state_lock:
                thresholds_changed = (
                    self._detection_signature(cfg)
                    != self._detection_signature(self.cfg)
                )
                self.cfg = cfg
            self.timeline.resize(cfg.observability_max_events)
            self.service.apply_config(cfg)
            self.policy.apply_config(cfg)
            self.arbiter.apply_config(cfg)
            self.router.apply_config(cfg)
            self.dispatcher.apply_config(cfg)
            self.ship_context.apply_config(cfg)
            self.official_api.apply_config(cfg)
            self.screenshots.apply_config(cfg)
            self.transport.apply_config(cfg)
            self.importer.apply_config(cfg)
            if isinstance(self.tactics, WowsTacticsRepository):
                self.tactics.apply_config(cfg)
            self.facts = FactBuilder(cfg)
            if thresholds_changed:
                # Detector latches were computed against the old thresholds;
                # keeping them would mix two rule sets in one battle.
                self.registry = self._build_registry()
                self._blocked_signature = ()

    def _telemetry_snapshot(self) -> dict[str, Any]:
        """The exact numbers to pair with a screenshot.

        Read under the state lock because the transport thread rewrites
        ``_latest`` on every frame, and a tool call arrives on the host's
        event loop.
        """
        with self._state_lock:
            latest = self._latest
        if latest is None:
            return {"in_battle": False}
        _snapshot, facts = latest
        return facts_to_telemetry(facts)

    @staticmethod
    def _detection_signature(cfg: WowsConfig) -> tuple:
        return (
            cfg.low_health_ratios,
            cfg.rapid_damage_ratio,
            cfg.rapid_damage_window_seconds,
            cfg.enemy_close_range_m,
            cfg.threat_scan_range_m,
            cfg.multi_direction_spread_deg,
            cfg.isolation_ally_range_m,
            cfg.isolation_enemy_range_m,
            cfg.boundary_margin_m,
            cfg.broadside_angle_deg,
            cfg.low_hp_target_ratio,
            cfg.damage_milestone_step,
            cfg.outnumbered_margin,
        )

    # ------------------------------------------------------------- 文档与提示词
    def _open_knowledge(self) -> bool:
        """Open the document store and adopt the active prompt revision.

        A failure here is not fatal: documents and custom prompts are both
        optional, so the plugin falls back to no reference text and the built-in
        instructions rather than refusing to start.
        """
        try:
            self.knowledge.open()
        except Exception as exc:
            self.logger.warning(f"tactical document store unavailable: {exc}")
            self.tactics = NullTacticsRepository()
            with self._state_lock:
                self._prompt_bundle = DEFAULT_BUNDLE
            return False

        self.tactics = WowsTacticsRepository(self.knowledge, self.cfg)
        try:
            bundle = bundle_from_revision(self.knowledge.get_active_revision())
        except Exception as exc:
            self.logger.warning(f"prompt revision unreadable, using defaults: {exc}")
            bundle = DEFAULT_BUNDLE
        with self._state_lock:
            self._prompt_bundle = bundle
        return True

    # ------------------------------------------------------------------ 生命周期
    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self._reload_config(force_dry_run=True)
        if not cfg.enabled:
            return Err(SdkError("neko_wows is disabled in configuration"))

        knowledge_ready = await asyncio.to_thread(self._open_knowledge)

        # Health probing and process launch can block for seconds; keep them off
        # the host event loop.
        status = await asyncio.to_thread(self.service.start_if_needed)
        self._record_service(status)

        transport_started = self._activate_transport(status)

        self.logger.info(
            f"neko_wows started (dry_run={cfg.dry_run}, url={cfg.service_url}, "
            f"service={status.mode})"
        )
        return Ok({
            "status": "running" if transport_started else "blocked",
            "dry_run": cfg.dry_run,
            "channel_mode": cfg.channel_mode,
            "service": status.as_dict(),
            "documents_ready": knowledge_ready,
            "prompt_revision": self._prompt_bundle.revision_id,
        })

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        with self._state_lock:
            self._running = False
        self.transport.stop()
        status = self.service.stop()
        self.context_injector.restore(
            WOWS_RESTORE_INSTRUCTIONS, dry_run=self.cfg.dry_run)
        self.ship_context.reset("shutdown")
        # Frames of the user's screen do not outlive the plugin.
        self.shots.clear()
        self.knowledge.close()
        self.logger.info("neko_wows shutdown")
        return Ok({"status": "shutdown", "service": status.as_dict()})

    @message(id="chat_quiet_window", source="chat")
    def on_chat_message(self, **_):
        """Start the quiet window. Only the timing is kept, never the text."""
        with self._pipeline_lock:
            self.arbiter.note_user_activity(time.monotonic())
        return Ok({"status": "observed"})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_):
        before = self._connection_signature()
        cfg = await self._reload_config()
        after = self._connection_signature()
        if before != after:
            with self._state_lock:
                # A conflict-blocked startup is not "running", but it still
                # needs an explicit reconnect after the user fixes the source.
                self._reconnect_required = (
                    self._running or self._reconnect_required)
        return Ok({
            "status": "reloaded",
            "dry_run": cfg.dry_run,
            "channel_mode": cfg.channel_mode,
            "reconnect_required": self._reconnect_required,
        })

    def _connection_signature(self) -> tuple:
        return tuple(getattr(self.cfg, key) for key in _CONNECTION_KEYS)

    def _record_service(self, status, *, only_on_change: bool = False) -> None:
        signature = (status.mode, status.detail)
        with self._state_lock:
            unchanged = signature == self._service_signature
            self._service_signature = signature
        if only_on_change and unchanged:
            return
        self.timeline.record(
            STAGE_SERVICE, status.mode, reason=status.detail,
            detail=status.as_dict())

    def _activate_transport(self, status) -> bool:
        """Start polling unless health positively identified a foreign service."""
        if not status.transport_allowed:
            with self._state_lock:
                self._running = False
                self._reconnect_required = True
            return False
        self.transport.start()
        with self._state_lock:
            self._running = True
            self._reconnect_required = False
        return True

    def _supervise_service(self) -> None:
        """Runs on a worker thread when the transport stops receiving frames.

        An offline service keeps failing every poll, so only a state change is
        worth a timeline entry -- otherwise the ring would fill with identical
        rows and push out the events the user is trying to read.
        """
        status = self.service.supervise()
        self._record_service(status, only_on_change=True)

    # ------------------------------------------------------------------ 主链路
    def _on_frame(self, frame: RawFrame) -> None:
        """Runs on the transport thread, once per raw payload."""
        try:
            snapshot = self.adapter.parse(
                frame.payload,
                transport=frame.transport,
                epoch=frame.epoch,
                received_at=frame.received_at,
            )
        except (UnexpectedServiceIdentity, UnsupportedApiVersion) as exc:
            self.timeline.record(STAGE_FRAME, "rejected", reason=str(exc))
            return
        except Exception as exc:
            self.timeline.record(
                STAGE_FRAME, "rejected", reason=f"parse failed: {type(exc).__name__}")
            return

        accepted, drop_reason = self.gate.accept(snapshot, frame.epoch)
        if not accepted:
            self.timeline.record(
                STAGE_FRAME, "dropped", seq=snapshot.seq,
                battle_id=snapshot.battle_id, reason=drop_reason)
            return

        with self._state_lock:
            self._frames_seen += 1
        try:
            self._evaluate(snapshot)
        except Exception as exc:  # pragma: no cover - keep the stream alive
            self.logger.exception(f"pipeline error on seq {snapshot.seq}: {exc}")
            self.timeline.record(
                STAGE_FRAME, "error", seq=snapshot.seq,
                reason=f"{type(exc).__name__}: {exc}")

    def _evaluate(self, snapshot) -> None:
        with self._pipeline_lock:
            try:
                self._evaluate_locked(snapshot)
            finally:
                # End-of-battle call-outs are built and delivered first; then the
                # scene instruction is removed so the next battle can inject it
                # afresh. Cleanup is allowed even if dry-run was just re-enabled.
                if snapshot.status == STATUS_ENDED:
                    self.context_injector.restore(
                        WOWS_RESTORE_INSTRUCTIONS, dry_run=self.cfg.dry_run)
                    self.ship_context.reset("battle_end")

    def _evaluate_locked(self, snapshot) -> None:
        cfg = self.cfg
        facts = self.facts.build(snapshot)
        current = (snapshot, facts)

        with self._state_lock:
            previous = self._previous
            self._previous = current
            self._latest = current
            # Read once per frame, so a prompt revision swap can only take
            # effect between frames and never mid-request.
            bundle = self._prompt_bundle

        result = self.registry.feed(previous, current, cfg=cfg)
        if result.identity_reset:
            self.arbiter.reset_battle(snapshot.battle_id)
            self._blocked_signature = ()
            self.timeline.record(
                STAGE_DETECT, "reset", seq=snapshot.seq,
                battle_id=snapshot.battle_id,
                reason="instanceId/battleId changed")

        if snapshot.is_live:
            self.context_injector.push(
                context_instructions(
                    screenshot_enabled=bool(cfg.screenshot_enabled)),
                dry_run=cfg.dry_run,
            )
            try:
                catalog_observation = self.ship_context.observe(
                    snapshot, dry_run=cfg.dry_run)
            except Exception as exc:
                self.timeline.record(
                    STAGE_SHIP_CATALOG,
                    "error",
                    seq=snapshot.seq,
                    battle_id=snapshot.battle_id,
                    reason=f"observation failed: {type(exc).__name__}",
                )
            else:
                self._record_ship_catalog_observation(
                    snapshot, catalog_observation)

        blocked_signature = tuple(
            (entry.detector, tuple(entry.missing)) for entry in result.blocked)
        if blocked_signature != self._blocked_signature:
            previously_blocked = bool(self._blocked_signature)
            self._blocked_signature = blocked_signature
            if blocked_signature:
                self.timeline.record(
                    STAGE_DETECT, "blocked", seq=snapshot.seq,
                    battle_id=snapshot.battle_id,
                    reason="missing required capabilities",
                    detail={
                        entry.detector: list(entry.missing)
                        for entry in result.blocked
                    },
                )
            elif previously_blocked:
                self.timeline.record(
                    STAGE_DETECT, "recovered", seq=snapshot.seq,
                    battle_id=snapshot.battle_id,
                    reason="required capabilities available again",
                )
        if not result.events:
            # Ordinary evaluated frames are intentionally silent. At telemetry
            # rates they otherwise evict the decisions and failures a user needs
            # the timeline to explain.
            if result.reason == "baseline" and not result.blocked:
                self.timeline.record(
                    STAGE_DETECT, "baseline", seq=snapshot.seq,
                    battle_id=snapshot.battle_id,
                    detail={"status": snapshot.status,
                            "transport": snapshot.transport})
            return

        with self._state_lock:
            self._events_seen += len(result.events)
        self.timeline.record(
            STAGE_DETECT, "events", seq=snapshot.seq,
            battle_id=snapshot.battle_id,
            detail={"events": [event.event_id for event in result.events]})

        candidates = self.policy.expand(result.events, facts)
        decision = self.arbiter.decide(candidates, facts.at)
        for step in decision.chain:
            self.timeline.record(
                STAGE_ARBITER, step.outcome, seq=snapshot.seq,
                battle_id=snapshot.battle_id, event_id=step.event_id,
                reason=step.detail)
        if decision.chosen is None:
            return

        chosen = decision.chosen
        with self._state_lock:
            self._last_candidate = chosen
        profile = PromptProfile(
            channel_mode=cfg.channel_mode,
            dry_run=cfg.dry_run,
            bundle=bundle,
            screenshot_enabled=bool(cfg.screenshot_enabled),
        )
        excerpts = self._reference_for(chosen, snapshot)
        request = self.router.build(chosen, profile, excerpts)
        outcome = self.dispatcher.deliver(request)
        self.arbiter.commit(chosen, facts.at, outcome_reason=outcome.reason)

        self.timeline.record(
            STAGE_DELIVERY, outcome.reason, seq=snapshot.seq,
            battle_id=snapshot.battle_id, event_id=chosen.event_id,
            detail={
                "lane": chosen.lane,
                "severity": chosen.severity,
                "host_calls": outcome.host_calls,
                "prompt_revision": bundle.revision_id,
                "excerpts": [excerpt.title for excerpt in excerpts],
                # In dry-run this is the full prompt the host would have seen.
                "preview": request.text if cfg.dry_run else "",
            },
        )

    def _reference_for(self, candidate, snapshot) -> tuple:
        """Look up reference text; never let the document layer break a call-out."""
        query = TacticQuery(
            summary=candidate.summary,
            event_id=candidate.event_id,
            map_name=snapshot.map_name,
            ship_name=snapshot.own_ship_name,
            ship_class=snapshot.own_ship_type,
            game_mode=snapshot.game_mode or snapshot.battle_type,
            topics=(candidate.summary,),
        )
        try:
            return tuple(self.tactics.search(query, limit=3, budget=0))
        except Exception as exc:
            self.logger.warning(f"tactical lookup failed: {type(exc).__name__}: {exc}")
            return ()

    def _record_ship_catalog_observation(
        self,
        snapshot,
        observation: ContextObservation,
    ) -> None:
        for event in observation.events:
            detail = dict(event.detail)
            reason = ""
            if isinstance(detail.get("reason"), str):
                reason = detail.pop("reason")[:200]
            self.timeline.record(
                STAGE_SHIP_CATALOG,
                event.outcome,
                seq=snapshot.seq,
                battle_id=snapshot.battle_id,
                reason=reason,
                detail=detail,
            )
        if observation.error and not observation.events:
            self.timeline.record(
                STAGE_SHIP_CATALOG,
                "error",
                seq=snapshot.seq,
                battle_id=snapshot.battle_id,
                reason=observation.error,
            )

    # ------------------------------------------------------------------ 面板数据
    def _dashboard_payload(self) -> dict[str, Any]:
        with self._state_lock:
            cfg = self.cfg
            latest = self._latest
            frames = self._frames_seen
            events = self._events_seen
            running = self._running
            reconnect_required = self._reconnect_required
            bundle = self._prompt_bundle

        service = self.service.snapshot()
        snapshot_view: dict[str, Any] = {}
        if latest is not None:
            snapshot, facts = latest
            snapshot_view = {
                "instance_id": snapshot.instance_id,
                "seq": snapshot.seq,
                "battle_id": snapshot.battle_id,
                "status": snapshot.status,
                "legacy": snapshot.legacy,
                "api_version": snapshot.api_version,
                "transport": snapshot.transport,
                "active": snapshot.active,
                "battle_type": snapshot.battle_type,
                "game_mode": snapshot.game_mode,
                "map_name": snapshot.map_name,
                "availability": dict(snapshot.availability),
                "unsupported": sorted(
                    name for name, supported in snapshot.capabilities.items()
                    if not supported
                ),
                "own_hp_ratio": facts.own_hp_ratio,
                "allies_alive": facts.alive_allies,
                "enemies_alive": facts.alive_enemies,
                "nearest_enemy_m": (
                    round(facts.nearest_enemy.distance_m)
                    if facts.nearest_enemy else None
                ),
            }

        return {
            "running": running,
            "runtime_now": time.monotonic(),
            "config": {
                "dry_run": cfg.dry_run,
                "channel_mode": cfg.channel_mode,
                "service_url": cfg.service_url,
                "service_source_dir": cfg.service_source_dir,
                "game_dir": cfg.game_dir,
                "urgent_ttl_seconds": cfg.urgent_ttl_seconds,
                "urgent_min_gap_seconds": cfg.urgent_min_gap_seconds,
                "normal_ttl_seconds": cfg.normal_ttl_seconds,
                "normal_min_gap_seconds": cfg.normal_min_gap_seconds,
                "dialogue_intrusion_mode": cfg.dialogue_intrusion_mode,
                "user_chat_quiet_window_seconds": cfg.user_chat_quiet_window_seconds,
                "disabled_categories": list(cfg.disabled_categories),
                "disabled_lanes": list(cfg.disabled_lanes),
            },
            "reconnect_required": reconnect_required,
            "service": service.as_dict(),
            "transport": self.transport.stats(),
            "cursor": self.gate.as_dict(),
            "snapshot": snapshot_view,
            "counters": {"frames": frames, "events": events},
            "arbiter": self.arbiter.stats(),
            "dispatcher": self.dispatcher.stats(),
            "context_injected": self.context_injector.injected,
            "screenshot": self.screenshots.status(),
            "ship_catalog": self._ship_catalog_payload(),
            "documents": self._documents_payload(),
            "prompts": self._prompts_payload(bundle),
            "categories": list(ALL_CATEGORIES),
            "lanes": list(ALL_LANES),
            "timeline": self.timeline.recent(60),
            "mod_hint": _mod_hint(service, snapshot_view),
        }

    def _ship_catalog_payload(self) -> dict[str, Any]:
        payload = dict(self.ship_context.stats())
        stats_method = getattr(getattr(self, "official_api", None), "stats", None)
        official_stats = stats_method() if callable(stats_method) else {}
        payload["official_tool"] = {
            "enabled": bool(self.cfg.official_api_enabled),
            "region": self.cfg.official_api_region,
            "key_configured": bool(self.cfg.official_api_application_id),
            "cache_entries": official_stats.get("cache_entries", 0),
            "cache_hits": official_stats.get("cache_hits", 0),
            "cache_misses": official_stats.get("cache_misses", 0),
        }
        return payload

    def _documents_payload(self) -> dict[str, Any]:
        cfg = self.cfg
        quotas = {
            "max_documents": cfg.tactics_max_documents,
            "max_total_bytes": cfg.tactics_max_total_bytes,
            "max_file_bytes": cfg.tactics_max_file_bytes,
            "index_chunk_cap": cfg.tactics_index_chunk_cap,
            "chunk_chars": cfg.tactics_chunk_chars,
            "chunk_overlap": cfg.tactics_chunk_overlap,
            "min_term_hits": cfg.tactics_min_term_hits,
            "tag_weight": cfg.tactics_tag_weight,
        }
        try:
            stats = self.knowledge.stats()
            documents = self.knowledge.list_documents()
        except Exception as exc:
            return {"available": False, "error": str(exc), "quotas": quotas,
                    "items": [], "stats": {}}
        diagnostics = getattr(self.tactics, "diagnostics", None)
        return {
            "available": True,
            "quotas": quotas,
            "stats": stats,
            "items": documents,
            # Ranking degrades past the index cap; say so instead of letting it
            # look like retrieval silently stopped working.
            "index_truncated": stats["indexed_chunks"] < stats["chunks"],
            "last_search": diagnostics.as_dict() if diagnostics is not None else {},
        }

    def _prompts_payload(self, bundle) -> dict[str, Any]:
        try:
            revisions = self.knowledge.list_revisions()
        except Exception:
            revisions = []
        return {
            "active_revision": bundle.revision_id,
            "is_builtin": bundle.is_builtin,
            "sections": bundle.sections(),
            "max_section_chars": MAX_SECTION_CHARS,
            "revisions_kept": self.cfg.prompt_revisions_kept,
            "revisions": revisions,
        }

    @ui.context(id="dashboard", title="战舰世界猫娘陪玩")
    async def dashboard_context(self):
        return self._dashboard_payload()

    # ------------------------------------------------------------------ 动作
    @ui.action(id="set_dry_run", label="设置 dry_run", tone="primary",
               group="runtime", order=10, refresh_context=True)
    @plugin_entry(
        id="set_dry_run",
        name="设置 dry_run",
        description="开/关 dry_run。开=只跑链路不真投给猫娘。",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "boolean", "default": True}},
        },
    )
    async def set_dry_run(self, value: bool = True, **_):
        with self._pipeline_lock:
            was_dry_run = bool(self.cfg.dry_run)
            next_dry_run = bool(value)
            if not was_dry_run and next_dry_run:
                self.context_injector.restore(
                    WOWS_RESTORE_INSTRUCTIONS, dry_run=True)
            self.cfg.dry_run = next_dry_run
            if was_dry_run and not next_dry_run:
                # Shadow cooldowns were accumulated against output nobody heard,
                # and detectors need a fresh baseline before real call-outs.
                self.arbiter.clear_shadow_state()
                self.registry.reset()
                self.dispatcher.reset_counters()
                self._blocked_signature = ()
                with self._state_lock:
                    self._previous = None
                self.timeline.record(
                    STAGE_DELIVERY, "output_enabled",
                    reason="shadow cooldowns cleared and detectors re-baselined")
        # Session-only: panel toggles do not persist; restart reloads TOML.
        return Ok({"dry_run": self.cfg.dry_run, "session_only": True})

    @ui.action(id="set_channel_mode", label="设置提示词通道", tone="primary",
               group="runtime", order=20, refresh_context=True)
    @plugin_entry(
        id="set_channel_mode",
        name="设置提示词通道",
        description="dual=紧急/常规各用一套 overlay；single=只用完整 base。仅影响措辞，不改优先级与 TTL。",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": list(ALL_CHANNEL_MODES),
                         "default": "dual"},
            },
        },
    )
    async def set_channel_mode(self, mode: str = "dual", **_):
        if mode not in ALL_CHANNEL_MODES:
            return Err(SdkError(f"unknown channel mode: {mode!r}"))
        async with self._preference_lock:
            error = await self._persist(STORE_CHANNEL_MODE, mode)
            if error is not None:
                return Err(error)
            with self._pipeline_lock:
                self.cfg.channel_mode = mode
                self.router.apply_config(self.cfg)
            return Ok({"channel_mode": mode})

    @ui.action(id="pause", label="急停", tone="danger",
               group="runtime", order=30, refresh_context=True)
    @plugin_entry(id="pause", name="急停", description="立即停止所有播报输出。")
    async def pause(self, **_):
        with self._pipeline_lock:
            self.dispatcher.pause("manual")
            self.arbiter.pause()
            self.timeline.record(STAGE_DELIVERY, "paused", reason="manual")
        return Ok({"paused": True})

    @ui.action(id="resume", label="恢复", tone="success",
               group="runtime", order=40, refresh_context=True)
    @plugin_entry(id="resume", name="恢复",
                  description="恢复播报并清空失败计数。")
    async def resume(self, **_):
        with self._pipeline_lock:
            self.dispatcher.resume()
            self.arbiter.resume()
            self.service.resume()
            self.timeline.record(STAGE_DELIVERY, "resumed", reason="manual")
        return Ok({"paused": False})

    @ui.action(id="set_connection", label="数据源配置", tone="primary",
               group="diagnostics", order=45, refresh_context=True)
    @plugin_entry(
        id="set_connection",
        name="设置数据源",
        description=(
            "持久化 service_url / 服务源码目录 / 游戏目录。"
            "改动后需重连才生效；reconnect=true 时保存后立即重连。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "service_url": {"type": "string"},
                "service_source_dir": {"type": "string"},
                "game_dir": {"type": "string"},
                "reconnect": {"type": "boolean", "default": False},
            },
            "required": ["service_url"],
        },
    )
    async def set_connection(
        self,
        service_url: str = "",
        service_source_dir: str = "",
        game_dir: str = "",
        reconnect: bool = False,
        **_,
    ):
        url = str(service_url or "").strip().rstrip("/")
        if not url:
            return Err(SdkError("service_url must not be empty"))
        source_dir = str(service_source_dir or "").strip()
        game = str(game_dir or "").strip()
        payload = {
            "service_url": url,
            "service_source_dir": source_dir,
            "game_dir": game,
        }
        async with self._preference_lock:
            before = self._connection_signature()
            error = await self._persist(STORE_CONNECTION_SETTINGS, payload)
            if error is not None:
                return Err(error)
            with self._pipeline_lock:
                self.cfg.service_url = url
                self.cfg.service_source_dir = source_dir
                self.cfg.game_dir = game
                self.service.apply_config(self.cfg)
                self.transport.apply_config(self.cfg)
            after = self._connection_signature()
            changed = before != after
            if changed:
                with self._state_lock:
                    self._reconnect_required = True
        if reconnect:
            reconnected = await self.reconnect()
            if reconnected.is_err():
                return reconnected
            return Ok({
                "service_url": url,
                "service_source_dir": source_dir,
                "game_dir": game,
                "changed": changed,
                "reconnect_required": bool(self._reconnect_required),
                "reconnected": True,
                "transport_started": reconnected.unwrap().get("transport_started"),
            })
        return Ok({
            "service_url": url,
            "service_source_dir": source_dir,
            "game_dir": game,
            "changed": changed,
            "reconnect_required": bool(self._reconnect_required),
            "reconnected": False,
        })

    @ui.action(id="reconnect", label="重连数据源", tone="primary",
               group="diagnostics", order=50, refresh_context=True)
    @plugin_entry(
        id="reconnect",
        name="重连数据源",
        description="按当前配置重新探测并连接遥测服务。改过 URL/端口/目录后需要执行一次。",
    )
    async def reconnect(self, **_):
        self.transport.stop()
        status = await asyncio.to_thread(self.service.start_if_needed)
        with self._pipeline_lock:
            self.gate.reset()
            self.registry.reset()
            self.ship_context.reset("reconnect")
            self._blocked_signature = ()
            with self._state_lock:
                self._previous = None
        connected = self._activate_transport(status)
        self._record_service(status)
        return Ok({"service": status.as_dict(), "transport_started": connected})

    @ui.action(id="clear_timeline", label="清空时间线", tone="info",
               group="diagnostics", order=60, refresh_context=True)
    @plugin_entry(id="clear_timeline", name="清空时间线",
                  description="清空面板上的链路记录。")
    async def clear_timeline(self, **_):
        self.timeline.clear()
        return Ok({"cleared": True})

    # ------------------------------------------------------------------ 截屏
    @ui.action(id="set_official_api", label="官方查询配置", tone="primary",
               group="diagnostics", order=65, refresh_context=True)
    @plugin_entry(
        id="set_official_api",
        name="设置官方查询",
        description=(
            "开关 Wargaming 官网舰船查询工具，并持久化 Application ID 与区服。"
            "面板不会回传明文 key；留空保存时保留已有 key，clear_application_id "
            "为真时清除。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "default": False},
                "region": {
                    "type": "string",
                    "enum": list(ALL_OFFICIAL_API_REGIONS),
                    "default": OFFICIAL_API_REGION_ASIA,
                },
                "application_id": {"type": "string"},
                "clear_application_id": {"type": "boolean", "default": False},
            },
        },
    )
    async def set_official_api(
        self,
        enabled: bool = False,
        region: str = OFFICIAL_API_REGION_ASIA,
        application_id: str | None = None,
        clear_application_id: bool = False,
        **_,
    ):
        if region not in ALL_OFFICIAL_API_REGIONS:
            return Err(SdkError(f"unknown official API region: {region!r}"))
        next_enabled = bool(enabled)
        next_region = region
        if clear_application_id:
            next_application_id = ""
        elif isinstance(application_id, str):
            next_application_id = application_id.strip()
        else:
            next_application_id = self.cfg.official_api_application_id
        payload = {
            "enabled": next_enabled,
            "region": next_region,
            "application_id": next_application_id,
        }
        async with self._preference_lock:
            error = await self._persist(STORE_OFFICIAL_API_SETTINGS, payload)
            if error is not None:
                return Err(error)
            with self._pipeline_lock:
                self.cfg.official_api_enabled = next_enabled
                self.cfg.official_api_region = next_region
                self.cfg.official_api_application_id = next_application_id
                self.official_api.apply_config(self.cfg)
            return Ok({
                "enabled": next_enabled,
                "region": next_region,
                "key_configured": bool(next_application_id),
                "cleared": bool(clear_application_id),
            })

    @ui.action(id="set_screenshot_enabled", label="主动截屏", tone="warning",
               group="diagnostics", order=70, refresh_context=True)
    @plugin_entry(
        id="set_screenshot_enabled",
        name="开关主动截屏",
        description=(
            "允许猫娘自己截取战舰世界画面判断战局。会截屏、把 JPEG 写进插件"
            "数据目录、并把画面发给模型厂商，默认关闭。"
        ),
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "boolean", "default": False}},
        },
    )
    async def set_screenshot_enabled(self, value: bool = False, **_):
        enabled = bool(value)
        async with self._preference_lock:
            payload = {
                "enabled": enabled,
                "min_interval_seconds": float(
                    self.cfg.screenshot_min_interval_seconds),
                "retain_count": int(self.cfg.screenshot_retain_count),
            }
            error = await self._persist(STORE_SCREENSHOT_SETTINGS, payload)
            if error is not None:
                return Err(error)
            with self._pipeline_lock:
                self.cfg.screenshot_enabled = enabled
                self.screenshots.apply_config(self.cfg)
                if not enabled:
                    # Turning it off deletes the frames too: leaving screenshots
                    # of the user's desktop on disk after they revoked permission
                    # would be the wrong reading of "off".
                    removed = self.shots.clear()
                else:
                    removed = 0
            return Ok({
                "screenshot_enabled": enabled,
                "cleared_shots": removed,
            })

    @ui.action(id="set_screenshot_settings", label="截屏参数", tone="primary",
               group="diagnostics", order=75, refresh_context=True)
    @plugin_entry(
        id="set_screenshot_settings",
        name="设置截屏参数",
        description="持久化最短截屏间隔与保留张数（与开关一并写入 store）。",
        input_schema={
            "type": "object",
            "properties": {
                "min_interval_seconds": {
                    "type": "number", "default": 15.0, "minimum": 0, "maximum": 600,
                },
                "retain_count": {
                    "type": "integer", "default": 20, "minimum": 1, "maximum": 100,
                },
            },
        },
    )
    async def set_screenshot_settings(
        self,
        min_interval_seconds: float = 15.0,
        retain_count: int = 20,
        **_,
    ):
        interval = max(0.0, min(600.0, float(min_interval_seconds)))
        retain = int(max(1, min(100, float(retain_count))))
        payload = {
            "enabled": bool(self.cfg.screenshot_enabled),
            "min_interval_seconds": interval,
            "retain_count": retain,
        }
        async with self._preference_lock:
            error = await self._persist(STORE_SCREENSHOT_SETTINGS, payload)
            if error is not None:
                return Err(error)
            with self._pipeline_lock:
                self.cfg.screenshot_min_interval_seconds = interval
                self.cfg.screenshot_retain_count = retain
                self.screenshots.apply_config(self.cfg)
            return Ok(payload)

    @ui.action(id="capture_screenshot_now", label="截一张看看", tone="info",
               group="diagnostics", order=80, refresh_context=True)
    @plugin_entry(
        id="capture_screenshot_now",
        name="立即截图",
        description="手动截一张，用于真机校准：确认定位到的是游戏窗口、画面拿得到。",
    )
    async def capture_screenshot_now(self, **_):
        result = await asyncio.to_thread(self.screenshots.look)
        output = result.get("output", {})
        if not output.get("ok"):
            return Err(SdkError(f"截图失败：{output.get('reason', 'unknown')}"))
        # The panel wants the metadata, never the pixels — the frame is on
        # disk and the dashboard payload lists it.
        return Ok({
            "shot_id": output.get("shot_id"),
            "source": output.get("source"),
            "window_title": output.get("window_title"),
        })

    # ------------------------------------------------------------------ 文档
    @ui.action(id="pick_documents", label="选择文件导入", tone="primary",
               group="documents", order=110, refresh_context=True)
    @plugin_entry(
        id="pick_documents",
        name="选择文件导入",
        description="打开系统文件选择器，导入 UTF-8 的 Markdown / TXT 战术资料。",
    )
    async def pick_documents(self, **_):
        paths = await asyncio.to_thread(_ask_for_documents)
        if not paths:
            return Ok({"status": "cancelled", "counts": {}, "results": []})
        # Reading and chunking can take a while for large files; keep it off the
        # host event loop.
        summary = await asyncio.to_thread(self.importer.import_paths, paths)
        self.timeline.record(
            STAGE_DOCUMENTS, "imported", reason="file picker",
            detail=summary.get("counts", {}))
        return Ok(summary)

    @ui.action(id="import_document_text", label="粘贴文本导入", tone="primary",
               group="documents", order=120, refresh_context=True)
    @plugin_entry(
        id="import_document_text",
        name="粘贴文本导入",
        description="直接粘贴 Markdown / 文本导入。全屏游戏下原生文件对话框可能被挡住，这条always可用。",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "default": ""},
                "content": {"type": "string", "default": ""},
            },
        },
    )
    async def import_document_text(self, title: str = "", content: str = "", **_):
        if not str(content or "").strip():
            return Err(SdkError("内容是空的"))
        try:
            result = await asyncio.to_thread(
                self.importer.import_text, title or "pasted", content)
        except DocumentRejected as exc:
            return Err(SdkError(str(exc)))
        self.timeline.record(
            STAGE_DOCUMENTS, result.get("status", "imported"),
            reason=result.get("title", ""))
        return Ok(result)

    @ui.action(id="delete_document", label="删除文档", tone="danger",
               group="documents", order=130, refresh_context=True)
    @plugin_entry(
        id="delete_document",
        name="删除文档",
        description="按 doc_id 删除一份已导入的战术资料。",
        input_schema={
            "type": "object",
            "properties": {"doc_id": {"type": "string"}},
            "required": ["doc_id"],
        },
    )
    async def delete_document(self, doc_id: str = "", **_):
        if not str(doc_id or "").strip():
            return Err(SdkError("缺少 doc_id"))
        removed = await asyncio.to_thread(
            self.knowledge.delete_document,
            doc_id,
            index_chunk_cap=self.cfg.tactics_index_chunk_cap,
        )
        if not removed:
            return Err(SdkError("没有这份文档"))
        self.timeline.record(STAGE_DOCUMENTS, "deleted", reason=doc_id)
        return Ok({"deleted": doc_id})

    @ui.action(id="clear_documents", label="清空文档库", tone="danger",
               group="documents", order=140, confirm="确认清空所有已导入的战术资料？",
               refresh_context=True)
    @plugin_entry(id="clear_documents", name="清空文档库",
                  description="删除所有已导入的战术资料与索引。")
    async def clear_documents(self, **_):
        removed = await asyncio.to_thread(self.knowledge.clear_documents)
        self.timeline.record(STAGE_DOCUMENTS, "cleared", reason=f"{removed} 份")
        return Ok({"removed": removed})

    # ------------------------------------------------------------------ 提示词
    @ui.action(id="save_prompt_revision", label="保存并启用", tone="primary",
               group="prompts", order=210, refresh_context=True)
    @plugin_entry(
        id="save_prompt_revision",
        name="保存提示词修订",
        description="整包校验三段提示词，保存为新版本并在下一帧生效。",
        input_schema={
            "type": "object",
            "properties": {
                "base": {"type": "string"},
                "urgent": {"type": "string"},
                "normal": {"type": "string"},
                "note": {"type": "string", "default": ""},
            },
            "required": ["base", "urgent", "normal"],
        },
    )
    async def save_prompt_revision(
        self, base: str = "", urgent: str = "", normal: str = "",
        note: str = "", **_,
    ):
        try:
            sections = validate_sections(base, urgent, normal)
        except PromptRejected as exc:
            # Rejected whole: the currently active revision is untouched.
            return Err(SdkError(str(exc)))
        revision = await asyncio.to_thread(
            self.knowledge.save_revision,
            base=sections[0], urgent=sections[1], normal=sections[2],
            note=str(note or ""), keep=self.cfg.prompt_revisions_kept,
        )
        self._adopt_revision(revision)
        return Ok({"revision_id": revision["revision_id"]})

    @ui.action(id="activate_prompt_revision", label="回滚到该版本", tone="info",
               group="prompts", order=220, refresh_context=True)
    @plugin_entry(
        id="activate_prompt_revision",
        name="启用提示词修订",
        description="把某个历史版本设为生效版本。",
        input_schema={
            "type": "object",
            "properties": {"revision_id": {"type": "string"}},
            "required": ["revision_id"],
        },
    )
    async def activate_prompt_revision(self, revision_id: str = "", **_):
        revision = await asyncio.to_thread(
            self.knowledge.activate_revision, str(revision_id or ""))
        if revision is None:
            return Err(SdkError("没有这个版本"))
        self._adopt_revision(revision)
        return Ok({"revision_id": revision["revision_id"]})

    @ui.action(id="reset_prompts", label="恢复内置提示词", tone="warning",
               group="prompts", order=230, confirm="确认删除所有自定义提示词版本？",
               refresh_context=True)
    @plugin_entry(id="reset_prompts", name="恢复内置提示词",
                  description="删除所有自定义版本，回到内置提示词。")
    async def reset_prompts(self, **_):
        await asyncio.to_thread(self.knowledge.reset_revisions)
        with self._state_lock:
            self._prompt_bundle = DEFAULT_BUNDLE
        self.timeline.record(
            STAGE_PROMPTS, "reset", reason="back to the built-in bundle")
        return Ok({"active_revision": DEFAULT_BUNDLE.revision_id})

    @ui.action(id="preview_prompt", label="本地预览", tone="info",
               group="prompts", order=240, refresh_context=False)
    @plugin_entry(
        id="preview_prompt",
        name="预览提示词",
        description="用最近一次真实候选（或内置样例）本地组装完整提示词。不经过投递，不进消息流。",
        input_schema={
            "type": "object",
            "properties": {
                "base": {"type": "string", "default": ""},
                "urgent": {"type": "string", "default": ""},
                "normal": {"type": "string", "default": ""},
                "lane": {"type": "string", "enum": list(ALL_LANES),
                         "default": "urgent"},
            },
        },
    )
    async def preview_prompt(
        self, base: str = "", urgent: str = "", normal: str = "",
        lane: str = LANE_URGENT, **_,
    ):
        with self._state_lock:
            bundle = self._prompt_bundle
            candidate = self._last_candidate
        if base or urgent or normal:
            try:
                sections = validate_sections(
                    base or bundle.base, urgent or bundle.urgent,
                    normal or bundle.normal)
            except PromptRejected as exc:
                return Err(SdkError(str(exc)))
            bundle = PromptBundle(
                revision_id=f"{bundle.revision_id}+draft",
                base=sections[0], urgent=sections[1], normal=sections[2],
            )

        target_lane = lane if lane in ALL_LANES else LANE_URGENT
        if candidate is None or candidate.lane != target_lane:
            candidate = _sample_candidate(target_lane, self.cfg)

        # Straight to the router: the dispatcher is never involved, so a preview
        # cannot become a message no matter what the output settings are.
        request = self.router.build(
            candidate,
            PromptProfile(
                channel_mode=self.cfg.channel_mode,
                dry_run=True,
                bundle=bundle,
                screenshot_enabled=bool(self.cfg.screenshot_enabled),
            ),
            (),
        )
        return Ok({
            "revision_id": bundle.revision_id,
            "lane": target_lane,
            "event_id": candidate.event_id,
            "sample": candidate.event_id != getattr(
                self._last_candidate, "event_id", None),
            "text": request.text,
        })

    # ------------------------------------------------------------------ 偏好
    @ui.action(id="set_intrusion_mode", label="设置插话策略", tone="primary",
               group="preferences", order=310, refresh_context=True)
    @plugin_entry(
        id="set_intrusion_mode",
        name="设置插话策略",
        description="决定战斗播报是否打断当前对话。宿主自己的活跃门始终生效，这一层是更长的可调窗口。",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": list(ALL_INTRUSION_MODES),
                         "default": "critical_only"},
                "quiet_window_seconds": {"type": "number", "default": 60.0},
            },
        },
    )
    async def set_intrusion_mode(
        self, mode: str = INTRUSION_CRITICAL_ONLY,
        quiet_window_seconds: float = 60.0, **_,
    ):
        if mode not in ALL_INTRUSION_MODES:
            return Err(SdkError(f"unknown intrusion mode: {mode!r}"))
        window = max(0.0, min(1800.0, float(quiet_window_seconds)))
        async with self._preference_lock:
            error = await self._persist(STORE_INTRUSION_SETTINGS, {
                "mode": mode,
                "quiet_window_seconds": window,
            })
            if error is not None:
                return Err(error)
            with self._pipeline_lock:
                self.cfg.dialogue_intrusion_mode = mode
                self.cfg.user_chat_quiet_window_seconds = window
                self.arbiter.apply_config(self.cfg)
            return Ok({"mode": mode, "quiet_window_seconds": window})

    @ui.action(id="set_category_enabled", label="设置事件类别", tone="primary",
               group="preferences", order=320, refresh_context=True)
    @plugin_entry(
        id="set_category_enabled",
        name="开关事件类别",
        description="关掉某一类事件。关掉后它在候选阶段就被拦下，不占队列也不占冷却。",
        input_schema={
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": list(ALL_CATEGORIES)},
                "enabled": {"type": "boolean", "default": True},
            },
            "required": ["category"],
        },
    )
    async def set_category_enabled(
        self, category: str = "", enabled: bool = True, **_,
    ):
        if category not in ALL_CATEGORIES:
            return Err(SdkError(f"unknown category: {category!r}"))
        async with self._preference_lock:
            disabled = set(self.cfg.disabled_categories)
            disabled.discard(category) if enabled else disabled.add(category)
            next_disabled = tuple(
                name for name in ALL_CATEGORIES if name in disabled)
            error = await self._persist(
                STORE_DISABLED_CATEGORIES, list(next_disabled))
            if error is not None:
                return Err(error)
            with self._pipeline_lock:
                self.cfg.disabled_categories = next_disabled
                self.policy.apply_config(self.cfg)
            return Ok({"disabled_categories": list(next_disabled)})

    @ui.action(id="set_lane_enabled", label="设置通道开关", tone="primary",
               group="preferences", order=330, refresh_context=True)
    @plugin_entry(
        id="set_lane_enabled",
        name="开关播报通道",
        description="关掉紧急或常规通道的全部播报。",
        input_schema={
            "type": "object",
            "properties": {
                "lane": {"type": "string", "enum": list(ALL_LANES)},
                "enabled": {"type": "boolean", "default": True},
            },
            "required": ["lane"],
        },
    )
    async def set_lane_enabled(self, lane: str = "", enabled: bool = True, **_):
        if lane not in ALL_LANES:
            return Err(SdkError(f"unknown lane: {lane!r}"))
        async with self._preference_lock:
            disabled = set(self.cfg.disabled_lanes)
            disabled.discard(lane) if enabled else disabled.add(lane)
            next_disabled = tuple(
                name for name in ALL_LANES if name in disabled)
            error = await self._persist(
                STORE_DISABLED_LANES, list(next_disabled))
            if error is not None:
                return Err(error)
            with self._pipeline_lock:
                self.cfg.disabled_lanes = next_disabled
                self.policy.apply_config(self.cfg)
            return Ok({"disabled_lanes": list(next_disabled)})

    @ui.action(id="set_lane_timing", label="设置通道时序", tone="primary",
               group="preferences", order=340, refresh_context=True)
    @plugin_entry(
        id="set_lane_timing",
        name="设置通道时序",
        description="覆盖某个通道的 TTL 与最短间隔。仅本次运行有效，重启回到配置值。",
        input_schema={
            "type": "object",
            "properties": {
                "lane": {"type": "string", "enum": list(ALL_LANES)},
                "ttl_seconds": {"type": "number"},
                "min_gap_seconds": {"type": "number"},
            },
            "required": ["lane"],
        },
    )
    async def set_lane_timing(
        self, lane: str = "", ttl_seconds: float | None = None,
        min_gap_seconds: float | None = None, **_,
    ):
        if lane not in ALL_LANES:
            return Err(SdkError(f"unknown lane: {lane!r}"))
        async with self._preference_lock:
            with self._pipeline_lock:
                if ttl_seconds is not None:
                    value = max(1.0, min(600.0, float(ttl_seconds)))
                    if lane == LANE_URGENT:
                        self.cfg.urgent_ttl_seconds = value
                    else:
                        self.cfg.normal_ttl_seconds = value
                if min_gap_seconds is not None:
                    value = max(0.0, min(3600.0, float(min_gap_seconds)))
                    if lane == LANE_URGENT:
                        self.cfg.urgent_min_gap_seconds = value
                    else:
                        self.cfg.normal_min_gap_seconds = value
                self.policy.apply_config(self.cfg)
                self.arbiter.apply_config(self.cfg)
                result = {
                    "lane": lane,
                    "ttl_seconds": self.cfg.ttl_for(lane),
                    "min_gap_seconds": self.cfg.min_gap_for(lane),
                    "session_only": True,
                }
            return Ok(result)

    @plugin_entry(id="status", name="状态",
                  description="查看连接、战局与安全状态。")
    async def status(self, **_):
        payload = self._dashboard_payload()
        # The timeline is large and the panel already renders it.
        payload.pop("timeline", None)
        return Ok(payload)

    @llm_tool(
        name="wows_query_ship_official",
        description=(
            "显式查询 World of Warships 官方 API 的指定舰船顶配参数。"
            "仅在需要核对官方在线数据时调用；日常战局参数由离线目录提供。"
        ),
        parameters=OFFICIAL_SHIP_TOOL_SCHEMA,
        timeout=35.0,
    )
    async def wows_query_ship_official(
        self,
        ship: str,
        configuration: str = "top",
        language: str | None = None,
        **_,
    ) -> dict[str, Any]:
        cfg = self.cfg
        if not cfg.official_api_enabled:
            return official_error("disabled")
        if not cfg.official_api_application_id:
            return official_error("missing_application_id")
        if configuration != "top":
            return official_error("invalid_configuration")

        selected_language = (
            language.strip().casefold()
            if isinstance(language, str) and language.strip()
            else cfg.official_api_language
        )
        if selected_language not in OFFICIAL_LANGUAGES:
            return official_error("invalid_language")

        value = ship.strip() if isinstance(ship, str) else ""
        if not value:
            return official_error("ship_not_found")
        if value.isdecimal():
            ship_id = int(value)
        else:
            catalog = None
            try:
                catalog = self.ship_catalog_store.snapshot()
                if getattr(catalog, "meta", None) is None:
                    return official_error("catalog_unavailable")
                resolution = ShipResolver(catalog).resolve(value)
                if not resolution.resolved or resolution.ship is None:
                    return official_error("ship_not_found")
                ship_id = resolution.ship.ship_id
            except Exception:
                return official_error("catalog_unavailable")
            finally:
                if catalog is not None:
                    try:
                        catalog.close()
                    except Exception:
                        pass

        return await asyncio.to_thread(
            self.official_api.query_ship_id,
            ship_id,
            configuration=configuration,
            language=selected_language,
        )

    # ------------------------------------------------------------------ 看战场
    @llm_tool(
        name="wows_look_at_battle",
        description=(
            "截取当前战舰世界画面看一眼战局。主动截屏开启时，每次发言前都要先调"
            "用本工具；读图时先看小地图再看主画面。"
            "返回画面解读加上精确遥测（血量、存活数、最近敌舰方位距离）。"
            "有最短间隔，冷却中或失败时不要卡住，按已有事实开口；别连着调。"
        ),
        parameters={"type": "object", "properties": {}},
        timeout=40.0,
    )
    async def wows_look_at_battle(self, **_) -> dict[str, Any]:
        # Capture blocks on window enumeration and a desktop grab; keep both
        # off the host event loop.
        return await asyncio.to_thread(self.screenshots.look)

    @llm_tool(
        name="wows_recall_screenshot",
        description=(
            "重新查看之前截过的某张战场画面。截图只在产生它的那一轮可见，"
            "之后要再看就用这个，传当时返回的 shot_id。只保留最近若干张，"
            "太旧的会失效。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "shot_id": {
                    "type": "string",
                    "description": "之前 wows_look_at_battle 返回的句柄，形如 shot_7。",
                },
            },
            "required": ["shot_id"],
            "additionalProperties": False,
        },
        timeout=20.0,
    )
    async def wows_recall_screenshot(self, shot_id: str = "", **_) -> dict[str, Any]:
        return await asyncio.to_thread(self.screenshots.recall, shot_id)

    # ------------------------------------------------------------------ 内部
    def _adopt_revision(self, revision: dict[str, Any]) -> None:
        bundle = bundle_from_revision(revision)
        with self._state_lock:
            self._prompt_bundle = bundle
        self.timeline.record(
            STAGE_PROMPTS, "activated", reason=bundle.revision_id)

    async def _persist(self, key: str, value: Any) -> SdkError | None:
        try:
            result = await self.store.set(key, value)
        except Exception as exc:
            self.logger.warning(f"failed to persist {key}: {exc}")
            return SdkError(f"failed to persist preference: {key}")
        if isinstance(result, Err):
            self.logger.warning(f"failed to persist {key}: {result.error}")
            return SdkError(f"failed to persist preference: {key}")
        return None


def _ask_for_documents() -> list[str]:
    """Open a native multi-select file dialog. Returns [] when unavailable.

    There is no SDK API for this, so the plugin process opens the dialog itself,
    the way `neko_live` and `qq_auto_reply` do. A fullscreen game can hide it,
    which is exactly why the paste-text action exists as well.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return []
    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askopenfilenames(
            title="选择战术资料（Markdown / TXT）",
            filetypes=[("Markdown / 文本", "*.md *.markdown *.txt"),
                       ("所有文件", "*.*")],
        )
        return [str(path) for path in (selected or ())]
    except Exception:
        return []
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def _sample_candidate(lane: str, cfg) -> AdviceCandidate:
    """A stand-in candidate so the lab works before any battle has happened."""
    event_id = LOW_HEALTH if lane == LANE_URGENT else DAMAGE_MILESTONE
    spec = spec_for(event_id)
    return AdviceCandidate(
        event_id=event_id,
        lane=spec.lane,
        priority=spec.priority,
        severity=70,
        at=0.0,
        seq=0,
        battle_id="sample",
        summary=spec.summary,
        detail={"hp_ratio": 0.18, "threshold": 0.2, "nearest_enemy_m": 6200}
        if lane == LANE_URGENT
        else {"damage_inflicted": 102000, "milestone": 100000},
        context={"own_hp_ratio": 0.18, "allies_alive": 4, "enemies_alive": 6},
        expires_at=cfg.ttl_for(spec.lane),
    )


def _mod_hint(service, snapshot_view: dict[str, Any]) -> str:
    """One actionable sentence about why there is no battle data.

    The in-game collector is installed by the user; the plugin can only observe
    the symptoms and say which of them it sees.
    """
    if service.mode == "conflict":
        return "conflict"
    if not service.health.reachable:
        return "unreachable"
    if not snapshot_view:
        return "no_snapshot"
    status = snapshot_view.get("status")
    if status == "waiting":
        return "waiting"
    if status == "stale":
        return "stale"
    if snapshot_view.get("legacy"):
        return "legacy"
    return ""


__all__ = ["NekoWowsPlugin"]
