"""Neko Roast runtime assembly."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path
from typing import Any

from ..adapters.bili_auth_service import BiliAuthService
from ..adapters.neko_dispatcher import NekoDispatcher
from ..modules._base import ReservedModule
from ..modules.active_engagement import ActiveEngagementModule
from ..modules.avatar_roast import AvatarRoastModule
from ..modules.bili_identity import BiliIdentityModule
from ..modules.bili_live_ingest import BiliLiveIngestModule
from ..modules.danmaku_response import DanmakuResponseModule
from ..modules.developer_sandbox import DeveloperSandboxModule
from ..modules.live_events import LiveEventsModule
from ..modules.viewer_profile import ViewerProfileModule
from ..modules.warmup_hosting import WarmupHostingModule
from ..stores.audit_store import AuditStore
from ..stores.avatar_cache import AvatarCache
from ..stores.credential_store import CredentialStore
from ..stores.viewer_store import ViewerStore
from .contracts import InteractionResult, PipelineStep, RoastConfig, ViewerEvent, ViewerProfile, parse_room_id
from .event_bus import EventBus
from . import active_topic_rules, live_status as live_status_rules, recent_context
from .active_topic_selector import ActiveTopicSelector
from .live_hosting_director import LiveHostingDirector
from .live_content import active_engagement_fallback_topic_candidates
from .instructions import (
    NEKO_ROAST_CONTEXT_INSTRUCTIONS,
    NEKO_ROAST_DEVELOPER_ANNOUNCEMENT,
    NEKO_ROAST_DEVELOPER_INSTRUCTIONS,
    NEKO_ROAST_DEVELOPER_RESTORE_INSTRUCTIONS,
    NEKO_ROAST_RESTORE_INSTRUCTIONS,
)
from .module_registry import ModuleRegistry
from .permission_gate import PermissionGate
from .pipeline import RoastPipeline
from .safety_guard import SafetyGuard


class RoastRuntime:
    # host 配置持久化预算（秒）：超过即放弃等待（配置已内存生效），避免被 host 的写竞争
    # （update_own_config 偶发卡满 10s）拖垮 update_config / connect 等 action。
    _CONFIG_PERSIST_BUDGET_SECONDS = 4.0
    _LIVE_STATE_ENGAGED_SECONDS = 60.0
    _LIVE_STATE_IDLE_SECONDS = 120.0
    _IDLE_HOSTING_CHECK_INTERVAL_SECONDS = 5.0
    _IDLE_HOSTING_MIN_INTERVAL_SECONDS = 120.0
    _IDLE_HOSTING_FAILURE_LIMIT = 3
    _IDLE_HOSTING_STREAK_FOR_ACTIVE_TAKEOVER = 2
    _SOLO_WARMUP_TIMEOUT_SECONDS = 45.0
    _ACTIVE_ENGAGEMENT_AFTER_DANMAKU_INTERVAL_SECONDS = 75.0
    _ACTIVE_ENGAGEMENT_RECENT_DANMAKU_TOPIC_MAX_AGE_SECONDS = 360.0
    _ACTIVE_ENGAGEMENT_IDLE_GRACE_SECONDS = 25.0

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.config = RoastConfig()
        self.audit = AuditStore(limit=100)
        self.avatar_cache = AvatarCache()
        self.viewer_store = ViewerStore(plugin, self.audit, lambda: self.config.viewer_store_dir)
        self.permission_gate = PermissionGate(self.config)
        self.safety_guard = SafetyGuard(self.config, self.audit)
        self.dispatcher = NekoDispatcher(plugin)
        self.event_bus = EventBus(self.audit)  # 传 audit：handler 失败按 owner 归属记账
        # P5 登录态：加密凭据 store + 扫码登录服务（注入 store 的加载/保存/重载回调）。
        self.credential_store = CredentialStore(plugin, self.audit)
        self.bili_credential: Any = None  # 缓存的 bilibili_api.Credential（已登录则非 None）
        self.bili_auth = BiliAuthService(
            logger=getattr(plugin, "logger", None),
            credential_provider=self.credential_store.build_credential,
            credential_saver=self.credential_store.save,
            credential_reloader=self.reload_credential,
        )
        self.registry = ModuleRegistry()
        self.recent_results: deque[dict[str, Any]] = deque(maxlen=self.config.recent_limit)
        self.recent_sandbox_results: deque[dict[str, Any]] = deque(maxlen=self.config.recent_limit)
        self.live_connection_state = "disconnected"
        self.instructions_injected = False
        self.developer_instructions_injected = False
        self._config_last_persist_at: float = 0.0
        self._config_last_error: str = ""
        # 串行化插件自身的配置写，避免并发 update_config 内存 apply 互踩 / 叠加持久化。
        # 懒初始化，避免构造时无运行 loop。
        self._idle_hosting_task: asyncio.Task[Any] | None = None
        self._idle_hosting_last_attempt_at: float = 0.0
        self._idle_hosting_consecutive_failures: int = 0
        self._idle_hosting_sleep = asyncio.sleep
        self._idle_hosting_now = time.monotonic
        self._live_state_now = time.monotonic
        self._live_listener_started_at: float = 0.0
        self._idle_hosting_recent_beat_keys: deque[str] = deque(maxlen=10)
        self._idle_hosting_recent_beat_axes: deque[str] = deque(maxlen=5)
        self._idle_hosting_recent_beat_titles: deque[str] = deque(maxlen=10)
        self._idle_hosting_recent_reply_affordances: deque[str] = deque(maxlen=5)
        self._idle_hosting_beat_index: int = 0
        self._recent_host_material_families: deque[str] = deque(maxlen=12)
        self._active_engagement_last_attempt_at: float = 0.0
        self._active_engagement_now = time.monotonic
        self._active_engagement_topic_fetcher: Any = None
        self._active_engagement_topic_cache: list[dict[str, Any]] = []
        self._active_engagement_topic_cache_at: float = 0.0
        self._active_engagement_recent_topic_keys: deque[str] = deque(maxlen=12)
        self._active_engagement_recent_topic_titles: deque[str] = deque(maxlen=8)
        self._active_engagement_recent_topic_sources: deque[str] = deque(maxlen=6)
        self._active_engagement_recent_fun_axes: deque[str] = deque(maxlen=6)
        self._active_engagement_recent_shapes: deque[str] = deque(maxlen=6)
        self._active_engagement_recent_intents: deque[str] = deque(maxlen=6)
        self._active_engagement_recent_reply_affordances: deque[str] = deque(maxlen=6)
        self._active_engagement_recent_topic_skip_reason: str = ""
        self._active_engagement_shape_guard_reason: str = ""
        self._active_engagement_shape_index: int = 0
        self.live_hosting_director = LiveHostingDirector(self)
        self.active_topic_selector = ActiveTopicSelector(self)
        self._config_lock: asyncio.Lock | None = None

        self.bili_live_ingest = BiliLiveIngestModule()
        self.bili_identity = BiliIdentityModule()
        self.viewer_profile = ViewerProfileModule()
        self.avatar_roast = AvatarRoastModule()
        self.danmaku_response = DanmakuResponseModule()
        self.active_engagement = ActiveEngagementModule()
        self.warmup_hosting = WarmupHostingModule()
        self.developer_sandbox = DeveloperSandboxModule()
        self.live_events = LiveEventsModule()
        self.pipeline = RoastPipeline(self)
        self.plugin_dir = Path(__file__).resolve().parents[1]

        for module in (
            self.bili_live_ingest,
            self.bili_identity,
            self.viewer_profile,
            self.avatar_roast,
            self.danmaku_response,
            self.active_engagement,
            self.warmup_hosting,
            self.developer_sandbox,
            self.live_events,
            ReservedModule("bili_dm_ingest", "B站私信输入"),
            ReservedModule("contribution_rank", "贡献值"),
            ReservedModule("watch_time", "观看时长"),
            ReservedModule("bili_read_tools", "B站读取工具"),
            ReservedModule("bili_write_tools", "B站写入工具"),
            ReservedModule("automation_ops", "自动化操作"),
        ):
            self.registry.register(module)

    async def start(self) -> None:
        await self.reload_config()
        await self.reload_credential()  # 载入此前已加密保存的 B站 登录凭据（若有）
        await self.registry.setup_all(self)
        self._start_idle_hosting_loop()
        self.audit.record("runtime_start", "neko_roast runtime ready")

    async def reload_credential(self) -> None:
        """从 store 重建缓存的 B站 Credential；无凭据 / 失败则置 None（不抛）。"""
        try:
            self.bili_credential = await self.credential_store.build_credential()
        except Exception:
            self.bili_credential = None

    async def bili_login(self) -> dict[str, Any]:
        """生成扫码登录二维码（或回报已登录）。"""
        return await self.bili_auth.login()

    async def bili_login_check(self) -> dict[str, Any]:
        """轮询扫码状态；DONE 时加密保存凭据并热重载。"""
        return await self.bili_auth.login_check()

    async def bili_login_status(self) -> dict[str, Any]:
        """检查当前登录态（无凭据时不调 SDK，直接 logged_in=False）。"""
        return await self.bili_auth.check_credential()

    async def bili_logout(self) -> dict[str, Any]:
        """本地注销：删除加密凭据 + 密钥文件，清空缓存（不吊销服务端 token）。"""
        removed = await self.credential_store.delete()
        self.bili_credential = None
        self.audit.record("bili_logout", "logged out (local credential removed)", detail={"files": removed})
        return {"logged_out": True, "removed": removed, "logged_in": False}

    async def stop(self) -> None:
        await self._stop_idle_hosting_loop()
        await self.restore_developer_instructions()
        await self.restore_instructions()
        await self.registry.teardown_all()
        self.audit.record("runtime_stop", "neko_roast runtime stopped")

    async def inject_instructions(self, *, force: bool = False) -> str:
        if self.instructions_injected and not force:
            return "already_injected"
        try:
            output = await self.dispatcher.push_context_instructions(NEKO_ROAST_CONTEXT_INSTRUCTIONS)
        except Exception as exc:
            self.instructions_injected = False
            message = str(exc).strip() or f"instruction_inject_failed: {type(exc).__name__}"
            self.audit.record("instructions_inject_failed", message, level="warning")
            return message
        self.instructions_injected = True
        self.audit.record("instructions_injected", output, detail={"source": "neko_roast"})
        return output

    async def sync_live_instructions(self, *, force: bool = False) -> str:
        if self.config.live_enabled:
            return await self.inject_instructions(force=force)
        return await self.restore_instructions()

    async def sync_developer_mode(self, *, announce: bool = False) -> str:
        if self.config.developer_tools_enabled:
            result = await self.inject_developer_instructions()
            if announce:
                announcement = await self.announce_developer_mode()
                return f"{result}; {announcement}"
            return result
        return await self.restore_developer_instructions()

    async def inject_developer_instructions(self, *, force: bool = False) -> str:
        if self.developer_instructions_injected and not force:
            return "developer_already_injected"
        try:
            output = await self.dispatcher.push_developer_instructions(NEKO_ROAST_DEVELOPER_INSTRUCTIONS)
        except Exception as exc:
            self.developer_instructions_injected = False
            message = str(exc).strip() or f"developer_instruction_inject_failed: {type(exc).__name__}"
            self.audit.record("developer_instructions_inject_failed", message, level="warning")
            return message
        self.developer_instructions_injected = True
        self.audit.record("developer_instructions_injected", output, detail={"source": "neko_roast"})
        return output

    async def restore_developer_instructions(self) -> str:
        if not self.developer_instructions_injected:
            return "developer_not_injected"
        try:
            output = await self.dispatcher.push_developer_restore(NEKO_ROAST_DEVELOPER_RESTORE_INSTRUCTIONS)
        except Exception as exc:
            message = str(exc).strip() or f"developer_instruction_restore_failed: {type(exc).__name__}"
            self.audit.record("developer_instructions_restore_failed", message, level="warning")
            return message
        self.developer_instructions_injected = False
        self.audit.record("developer_instructions_restored", output, detail={"source": "neko_roast"})
        return output

    async def announce_developer_mode(self) -> str:
        try:
            output = await self.dispatcher.push_developer_announcement(NEKO_ROAST_DEVELOPER_ANNOUNCEMENT)
        except Exception as exc:
            message = str(exc).strip() or f"developer_mode_announce_failed: {type(exc).__name__}"
            self.audit.record("developer_mode_announce_failed", message, level="warning")
            return message
        self.audit.record("developer_mode_announced", output, detail={"source": "neko_roast"})
        return output

    async def restore_instructions(self) -> str:
        if not self.instructions_injected:
            return "not_injected"
        try:
            output = await self.dispatcher.push_context_restore(NEKO_ROAST_RESTORE_INSTRUCTIONS)
        except Exception as exc:
            message = str(exc).strip() or f"instruction_restore_failed: {type(exc).__name__}"
            self.audit.record("instructions_restore_failed", message, level="warning")
            return message
        self.instructions_injected = False
        self.audit.record("instructions_restored", output, detail={"source": "neko_roast"})
        return output

    async def reload_config(self) -> RoastConfig:
        data: dict[str, Any] = {}
        try:
            dumped = await self.plugin.config.dump(timeout=5.0)
            if isinstance(dumped, dict):
                data = dumped.get("neko_roast", {}) if isinstance(dumped.get("neko_roast"), dict) else {}
        except Exception as exc:
            self.audit.record("config_load_failed", f"config load failed: {type(exc).__name__}", level="warning")
        return self._activate_config(RoastConfig.from_mapping(data))

    def _activate_config(self, config: RoastConfig) -> RoastConfig:
        self.config = config
        self.audit.set_limit(max(50, self.config.recent_limit * 4))
        self.recent_results = deque(self.recent_results, maxlen=self.config.recent_limit)
        self.recent_sandbox_results = deque(self.recent_sandbox_results, maxlen=self.config.recent_limit)
        self.permission_gate.update(self.config)
        self.safety_guard.update(self.config)
        if self.config.live_room_id <= 0:
            self.live_connection_state = "disconnected"
        self.safety_guard.set_connected(self.live_connection_state == "connected")
        return self.config

    def _get_config_lock(self) -> asyncio.Lock:
        if self._config_lock is None:
            self._config_lock = asyncio.Lock()
        return self._config_lock

    async def update_config(self, updates: dict[str, Any]) -> RoastConfig:
        allowed = set(RoastConfig.__dataclass_fields__.keys())
        clean = {key: value for key, value in updates.items() if key in allowed}
        if not clean:
            return self.config
        # 房号支持直接粘直播间链接：进 config / 持久化前归一成数字（链接→房号），保证落盘是 int。
        if "live_room_id" in clean:
            clean["live_room_id"] = parse_room_id(clean["live_room_id"])
        # 配置写竞争根治（插件侧）：先内存生效，再尽力持久化。
        # host 的 update_own_config 在「只重后端不重前端」等场景会卡满写竞争；旧实现先 await
        # 持久化再 apply，被 host 的 10s entry 超时连内存兜底都来不及跑，导致 update_config /
        # connect 直接 500。现在反过来：runtime 行为以内存为准、即时生效；持久化降级为带预算的
        # 尽力而为，超时/失败都不回滚、不阻塞。lock 串行化插件自身并发写，避免内存 apply 互踩。
        old_room_id = int(self.config.live_room_id or 0)
        was_listening = bool(self.bili_live_ingest.is_listening())
        async with self._get_config_lock():
            data = self.config.to_dict()
            data.update(clean)
            self._activate_config(RoastConfig.from_mapping(data))
            if "live_enabled" in clean:
                await self.sync_live_instructions(force=True)
            if "developer_tools_enabled" in clean:
                await self.sync_developer_mode(announce=False)
            await self._persist_config_best_effort(clean)
        await self._reconcile_live_listener_after_config(clean, old_room_id=old_room_id, was_listening=was_listening)
        return self.config

    async def _reconcile_live_listener_after_config(
        self,
        clean: dict[str, Any],
        *,
        old_room_id: int,
        was_listening: bool,
    ) -> None:
        if not was_listening:
            return
        room_changed = "live_room_id" in clean and int(self.config.live_room_id or 0) != old_room_id
        disabled = "live_enabled" in clean and not bool(self.config.live_enabled)
        if not room_changed and not disabled:
            return
        if not self.config.live_enabled:
            self.live_connection_state = "disconnected"
            self.safety_guard.set_connected(False)
            return
        if disabled or self.config.live_room_id <= 0:
            await self._stop_live_listener(mark_disabled=True)
            return
        started = await self._start_live_listener(int(self.config.live_room_id))
        self.audit.record(
            "live_reconnected" if started else "live_reconnect_failed",
            "danmaku listener restarted for room change" if started else "failed to restart danmaku listener for room change",
            level="info" if started else "warning",
            detail={"room_id": self.config.live_room_id, "previous_room_id": old_room_id},
        )

    async def _start_live_listener(self, room_id: int) -> bool:
        started = await self.bili_live_ingest.start_listening(room_id)
        if started:
            self.pipeline.clear_dry_run_session_state()
            self._live_listener_started_at = float(self._live_state_now())
            self._idle_hosting_consecutive_failures = 0
        self.live_connection_state = "connected" if started else "disconnected"
        self.config.live_enabled = bool(started)
        self.safety_guard.set_connected(started)
        return started

    async def _stop_live_listener(self, *, mark_disabled: bool) -> None:
        await self.bili_live_ingest.stop_listening()
        self.live_events.reset()
        if mark_disabled:
            self.config.live_enabled = False
            await self.restore_instructions()
        self.live_connection_state = "disconnected"
        self._live_listener_started_at = 0.0
        self.safety_guard.set_connected(False)

    async def _persist_config_best_effort(self, clean: dict[str, Any]) -> None:
        """尽力持久化：带预算超时；超时/失败只记 audit，绝不回滚已生效的内存配置。"""
        try:
            await asyncio.wait_for(
                self._persist_config_update(clean),
                timeout=self._CONFIG_PERSIST_BUDGET_SECONDS,
            )
            self._config_last_persist_at = time.time()
            self._config_last_error = ""
        except asyncio.TimeoutError:
            self._config_last_error = "config_persist_timeout"
            self.audit.record(
                "config_persist_timeout",
                f"config persistence exceeded {self._CONFIG_PERSIST_BUDGET_SECONDS}s budget; "
                "runtime config already applied in memory",
                level="warning",
            )
        except Exception as exc:
            self._config_last_error = f"config_persist_failed:{type(exc).__name__}"
            self.audit.record(
                "config_persist_failed",
                f"config persistence failed, using runtime config: {type(exc).__name__}",
                level="warning",
            )

    async def _persist_config_update(self, clean: dict[str, Any]) -> None:
        update_own_config = getattr(getattr(self.plugin, "ctx", None), "update_own_config", None)
        if callable(update_own_config):
            await update_own_config({"neko_roast": clean}, timeout=10.0)
            return

        config_api = getattr(self.plugin, "config", None)
        ensure_active = getattr(config_api, "profile_ensure_active", None)
        if callable(ensure_active):
            await ensure_active("default", {"neko_roast": clean}, timeout=10.0)
        update = getattr(config_api, "update", None)
        if not callable(update):
            raise RuntimeError("plugin config update API is unavailable")
        try:
            await update({"neko_roast": clean})
        except ValueError as exc:
            if "no active profile" not in str(exc):
                raise
            raise RuntimeError("plugin config update requires an active profile") from exc

    def record_result(self, result: InteractionResult) -> None:
        if result.event.source == "developer_sandbox":
            payload = result.to_sandbox_dict()
            self.recent_sandbox_results.append(payload)
            self.event_bus.emit("sandbox_result", payload)
            return
        payload = result.to_public_dict()
        payload["response_module"] = self._route_from_result(payload)
        payload["event_signal"] = self._event_signal_from_result(payload)
        self._expose_request_metadata(payload)
        if str(payload.get("status") or "") == "pushed":
            spent_output = self._spent_output_text(payload)
            spent_families = self._spent_output_families(spent_output)
            if spent_families:
                payload["spent_output_family"] = ",".join(spent_families)
        self.recent_results.append(payload)
        self.event_bus.emit("result", payload)

    @staticmethod
    def _expose_request_metadata(payload: dict[str, Any]) -> None:
        request = payload.get("request")
        metadata = request.get("metadata") if isinstance(request, dict) else None
        if not isinstance(metadata, dict):
            return
        for key in ("danmaku_profile", "danmaku_reply_target", "danmaku_reply_shape"):
            value = str(metadata.get(key) or "").strip()
            if value:
                payload[key] = value

    async def handle_live_payload(self, payload: dict[str, Any]) -> InteractionResult:
        event = self.bili_live_ingest.normalize(payload)
        signal_event_type = str(event.raw.get("event_type") or "").strip().lower() if isinstance(event.raw, dict) else ""
        if signal_event_type in {"gift", "guard", "super_chat", "sc"}:
            return self._record_live_signal_only_skip(event, signal_event_type)
        return await self.pipeline.handle_event(event)

    def _record_live_signal_only_skip(self, event: ViewerEvent, event_type: str) -> InteractionResult:
        normalized = "super_chat" if event_type == "sc" else event_type
        reason = f"live_event_signal.unsupported_{normalized}"
        result = InteractionResult(
            accepted=False,
            status="skipped",
            event=event,
            reason=reason,
            steps=[PipelineStep(self._signal_route_for_event_type(normalized), "skipped", reason)],
        )
        self.audit.record("live_event_signal_only", reason, level="info", detail={"event_type": normalized, "uid": event.uid})
        self.record_result(result)
        return result

    async def lookup_live_room(self, room_id: Any) -> dict[str, Any]:
        status = await self.bili_live_ingest.lookup_room_status(parse_room_id(room_id))
        level = "info" if status.ok else "warning"
        self.audit.record(
            "live_room_lookup",
            status.message or "live room looked up",
            level=level,
            detail={"room_id": status.room_id, "ok": status.ok, "live_status": status.live_status},
        )
        return status.to_dict()

    async def handle_sandbox_target(self, **kwargs: Any) -> InteractionResult:
        self._require_developer_mode()
        event = self.developer_sandbox.parse_target(**kwargs)
        return await self.pipeline.handle_event(event)

    async def lookup_bili_user(self, **kwargs: Any) -> dict[str, Any]:
        self._require_developer_mode()
        event = self.developer_sandbox.parse_target(**kwargs, use_presets=False)
        if not event.uid:
            raise ValueError("uid or Bilibili space URL is required")
        identity = await self.bili_identity.resolve(event)
        profile = ViewerProfile(uid=identity.uid, nickname=identity.nickname, avatar_url=identity.avatar_url)
        identity_payload = identity.to_public_dict()
        identity_payload["avatar_preview_url"] = ""
        identity_payload["avatar_preview_data_url"] = ""
        self.audit.record("developer_lookup", "bili user looked up", detail={"uid": identity.uid, "fetched": identity.fetched})
        return {
            "event": event.to_dict(),
            "identity": identity_payload,
            "profile": profile.to_dict(),
        }

    def clear_sandbox_data(self) -> dict[str, Any]:
        cleared_records = len(self.recent_sandbox_results)
        self.recent_sandbox_results.clear()
        cleared_preview_files = 0
        preview_dir = self.plugin_dir / "static" / "avatar-preview"
        if preview_dir.is_dir():
            for path in preview_dir.iterdir():
                if not path.is_file():
                    continue
                try:
                    path.unlink()
                    cleared_preview_files += 1
                except OSError:
                    self.audit.record("sandbox_preview_clear_failed", path.name, level="warning")
        self.audit.record(
            "sandbox_clear",
            "sandbox runtime data cleared",
            detail={"records": cleared_records, "preview_files": cleared_preview_files},
        )
        return {"records": cleared_records, "preview_files": cleared_preview_files}

    def _require_developer_mode(self) -> None:
        if not self.config.developer_tools_enabled:
            raise PermissionError("developer mode is disabled")

    async def handle_manual_event(self, **kwargs: Any) -> InteractionResult:
        event = ViewerEvent(
            uid=str(kwargs.get("uid") or "").strip(),
            nickname=str(kwargs.get("nickname") or "").strip(),
            avatar_url=str(kwargs.get("avatar_url") or "").strip(),
            danmaku_text=str(kwargs.get("danmaku_text") or "").strip(),
            target_lanlan=str(kwargs.get("target_lanlan") or kwargs.get("lanlan_name") or "").strip(),
            source="manual_live_simulation",
            live_mode=self.config.live_mode,
            raw=dict(kwargs),
        )
        return await self.pipeline.handle_event(event)

    async def trigger_idle_hosting(self) -> InteractionResult:
        return await self.live_hosting_director.trigger_idle_hosting()

    async def maybe_trigger_idle_hosting(self) -> InteractionResult | None:
        return await self.live_hosting_director.maybe_trigger_idle_hosting()

    def _idle_hosting_event(self, live_state: dict[str, Any]) -> ViewerEvent:
        return self.live_hosting_director.idle_hosting_event(live_state)

    def _next_idle_hosting_beat(self) -> dict[str, Any]:
        return self.live_hosting_director.next_idle_hosting_beat()

    @staticmethod
    def _idle_hosting_beat_candidates() -> list[dict[str, Any]]:
        return LiveHostingDirector.idle_hosting_beat_candidates()

    def _idle_hosting_preferred_stage(self) -> str:
        return self.live_hosting_director.idle_hosting_preferred_stage()

    def _idle_hosting_stage_ordered_candidates(
        self,
        candidates: list[dict[str, Any]],
        preferred_stage: str,
    ) -> list[dict[str, Any]]:
        return self.live_hosting_director.idle_hosting_stage_ordered_candidates(candidates, preferred_stage)

    @staticmethod
    def _idle_hosting_material_stage(material: dict[str, Any] | None) -> str:
        return LiveHostingDirector.idle_hosting_material_stage(material)

    def _is_similar_idle_hosting_beat_title(self, title: str) -> bool:
        return self.live_hosting_director.is_similar_idle_hosting_beat_title(title)

    def _record_idle_hosting_skip(self, event: ViewerEvent, reason: str) -> InteractionResult:
        return self.live_hosting_director.record_idle_hosting_skip(event, reason)

    async def trigger_warmup_hosting(self) -> InteractionResult:
        return await self.live_hosting_director.trigger_warmup_hosting()

    async def maybe_trigger_warmup_hosting(self) -> InteractionResult | None:
        return await self.live_hosting_director.maybe_trigger_warmup_hosting()

    def _warmup_hosting_event(self, live_state: dict[str, Any]) -> ViewerEvent:
        return self.live_hosting_director.warmup_hosting_event(live_state)

    def _record_warmup_hosting_skip(self, event: ViewerEvent, reason: str) -> InteractionResult:
        return self.live_hosting_director.record_warmup_hosting_skip(event, reason)

    async def trigger_active_engagement(self) -> InteractionResult:
        live_connection = self.live_connection_snapshot()
        live_status = self.live_status_summary(live_connection)
        health_rows = self.runtime_health_rows()
        live_state = self.live_state_summary(live_status, health_rows)
        active_status = self.active_engagement_status(live_status, live_state)
        skip_event = self._active_engagement_basic_event(live_state)

        if self.config.live_mode != "solo_stream":
            return self._record_active_engagement_skip(skip_event, "active_engagement.not_solo_stream")
        state = str(live_state.get("state") or "")
        if state == "paused":
            return self._record_active_engagement_skip(skip_event, "active_engagement.paused")
        if state == "blocked":
            return self._record_active_engagement_skip(skip_event, "active_engagement.blocked")
        if state != "quiet" and not (state == "idle" and bool(active_status.get("candidate"))):
            return self._record_active_engagement_skip(skip_event, "active_engagement.not_quiet")
        if not bool(active_status.get("candidate")):
            return self._record_active_engagement_skip(skip_event, "active_engagement.not_candidate")
        event = await self._active_engagement_event(live_state)
        return await self.pipeline.handle_event(event)

    async def maybe_trigger_active_engagement(self) -> InteractionResult | None:
        now = float(self._active_engagement_now())
        if now - self._active_engagement_last_attempt_at < self._active_engagement_min_interval_seconds():
            return None
        live_connection = self.live_connection_snapshot()
        live_status = self.live_status_summary(live_connection)
        health_rows = self.runtime_health_rows()
        live_state = self.live_state_summary(live_status, health_rows)
        active_status = self.active_engagement_status(live_status, live_state)
        if not bool(active_status.get("eligible")):
            return None

        self._active_engagement_last_attempt_at = now
        return await self.trigger_active_engagement()

    async def _active_engagement_event(self, live_state: dict[str, Any]) -> ViewerEvent:
        topic_material = await self._select_active_engagement_topic()
        event = self._active_engagement_basic_event(live_state)
        event.raw["topic_material"] = topic_material
        return event

    def _active_engagement_basic_event(self, live_state: dict[str, Any]) -> ViewerEvent:
        return ViewerEvent(
            uid="__neko_active__",
            nickname="NEKO",
            danmaku_text="",
            source="active_engagement",
            live_mode=self.config.live_mode,
            raw={
                "trigger": "manual_active_engagement",
                "live_state": dict(live_state),
            },
        )

    async def _select_active_engagement_topic(self) -> dict[str, Any]:
        return await self.active_topic_selector.select_topic()

    def _choose_active_engagement_candidate(
        self,
        candidates: list[dict[str, Any]],
        *,
        avoid_recent_fun_axis: bool,
        avoid_recent_family: bool,
        allow_similar_title: bool = False,
    ) -> dict[str, Any] | None:
        return self.active_topic_selector.choose_candidate(
            candidates,
            avoid_recent_fun_axis=avoid_recent_fun_axis,
            avoid_recent_family=avoid_recent_family,
            allow_similar_title=allow_similar_title,
        )

    @staticmethod
    def _active_engagement_fallback_topic_candidates() -> list[dict[str, Any]]:
        return active_engagement_fallback_topic_candidates()

    @staticmethod
    def _active_engagement_topic_pack(material: dict[str, Any] | None) -> str:
        return ActiveTopicSelector.topic_pack(material)

    async def _active_engagement_topic_candidates(self) -> list[dict[str, Any]]:
        return await self.active_topic_selector.topic_candidates()

    async def _bili_trending_topic_candidates(self) -> list[dict[str, Any]]:
        return await self.active_topic_selector.bili_trending_topic_candidates()

    def _recent_danmaku_topic_candidates(self) -> list[dict[str, Any]]:
        return self.active_topic_selector.recent_danmaku_topic_candidates()

    @staticmethod
    def _is_meaningful_active_topic_text(text: str) -> bool:
        return active_topic_rules._is_meaningful_active_topic_text(text)

    @staticmethod
    def _active_topic_filter_reason(text: str) -> str:
        return active_topic_rules._active_topic_filter_reason(text)

    @staticmethod
    def _is_direct_neko_request_or_ack(dense_lowered: str) -> bool:
        return active_topic_rules._is_direct_neko_request_or_ack(dense_lowered)

    @staticmethod
    def _is_untargeted_request_or_reaction(dense_lowered: str) -> bool:
        return active_topic_rules._is_untargeted_request_or_reaction(dense_lowered)

    @staticmethod
    def _is_untargeted_request(dense_lowered: str) -> bool:
        return active_topic_rules._is_untargeted_request(dense_lowered)

    @staticmethod
    def _is_reaction_only(dense_lowered: str) -> bool:
        return active_topic_rules._is_reaction_only(dense_lowered)

    @staticmethod
    def _is_live_test_or_runtime_feedback(dense_lowered: str) -> bool:
        return active_topic_rules._is_live_test_or_runtime_feedback(dense_lowered)

    def _next_active_engagement_shape(self) -> str:
        return self.active_topic_selector.next_shape()

    def _active_engagement_guarded_shape(self, shape: str) -> str:
        return self.active_topic_selector.guarded_shape(shape)

    @staticmethod
    def _has_active_engagement_streak(values: deque[str], value: str, count: int) -> bool:
        return active_topic_rules._has_active_engagement_streak(values, value, count)

    @staticmethod
    def _is_similar_active_topic_title(title: str, recent_titles: deque[str]) -> bool:
        return active_topic_rules._is_similar_active_topic_title(title, recent_titles)

    @staticmethod
    def _host_material_family(material: dict[str, Any] | None) -> str:
        return active_topic_rules._host_material_family(material)

    @staticmethod
    def _active_topic_material_profile(title: str) -> dict[str, str]:
        return active_topic_rules._active_topic_material_profile(title)

    @staticmethod
    def _is_viewer_to_viewer_mention_text(text: str) -> bool:
        return active_topic_rules._is_viewer_to_viewer_mention_text(text)

    @staticmethod
    def _is_neko_mention_target(name: str, lowered_aliases: set[str]) -> bool:
        return active_topic_rules._is_neko_mention_target(name, lowered_aliases)

    @staticmethod
    def _active_engagement_hook_text(shape: str, title: str) -> str:
        return active_topic_rules._active_engagement_hook_text(shape, title)

    @staticmethod
    def _active_engagement_pattern_text(shape: str) -> str:
        return active_topic_rules._active_engagement_pattern_text(shape)

    @staticmethod
    def _active_engagement_hint_text(shape: str) -> str:
        return active_topic_rules._active_engagement_hint_text(shape)

    @staticmethod
    def _active_engagement_intent_text(shape: str) -> str:
        return active_topic_rules._active_engagement_intent_text(shape)

    @staticmethod
    def _active_engagement_fun_axis_text(shape: str) -> str:
        return active_topic_rules._active_engagement_fun_axis_text(shape)

    @staticmethod
    def _active_engagement_reply_affordance_text(shape: str) -> str:
        return active_topic_rules._active_engagement_reply_affordance_text(shape)

    def _record_active_engagement_skip(self, event: ViewerEvent, reason: str) -> InteractionResult:
        result = InteractionResult(
            accepted=False,
            status="skipped",
            event=event,
            reason=reason,
            steps=[PipelineStep("active_engagement_gate", "skipped", reason)],
        )
        self.audit.record("active_engagement_skipped", reason, level="info", detail={"mode": self.config.live_mode})
        self.record_result(result)
        return result

    def _start_idle_hosting_loop(self) -> None:
        self.live_hosting_director.start_loop()

    async def _stop_idle_hosting_loop(self) -> None:
        await self.live_hosting_director.stop_loop()

    async def _idle_hosting_loop(self) -> None:
        await self.live_hosting_director.idle_hosting_loop()

    async def dashboard_state(self) -> dict[str, Any]:
        profiles = await self.viewer_store.recent_profiles(self.config.recent_limit)
        storage = self.viewer_store.storage_status()
        live_connection = self.live_connection_snapshot()
        live_status = self.live_status_summary(live_connection)
        health_rows = self.runtime_health_rows()
        live_state = self.live_state_summary(live_status, health_rows)
        idle_hosting_status = self.idle_hosting_status(live_state)
        active_engagement_status = self.active_engagement_status(live_status, live_state)
        live_director_status = self.live_director_status(live_status, live_state, idle_hosting_status, active_engagement_status)
        solo_test_readiness = self.solo_test_readiness(live_status, live_state, live_director_status, profile_count=len(profiles))
        return {
            "config": self.config.to_dict(),
            "live_connection": live_connection,
            "live_status": live_status,
            "live_state": live_state,
            "idle_hosting_status": idle_hosting_status,
            "active_engagement_status": active_engagement_status,
            "live_director_status": live_director_status,
            "solo_test_readiness": solo_test_readiness,
            "speech_explanation": self.speech_explanation(live_status, live_state),
            # 观众档案改走本地 JSON（不依赖宿主 PluginStore，见 docs/devlog.md）。
            # store_enabled 保留旧字段名兼容面板，现指"档案目录是否可写=能否持久化"。
            "store_enabled": bool(storage.get("writable")),
            "viewer_store": storage,
            "modules": self.registry.snapshot(),
            "safety": self.safety_guard.snapshot(),
            "recent_profiles": profiles,
            "recent_results": list(reversed(self.recent_results)),
            "recent_sandbox_results": list(reversed(self.recent_sandbox_results)),
            "recent_audit": self.audit.recent(self.config.recent_limit),
            "avatar_cache": self.avatar_cache.status(),
            "health_rows": health_rows,
            "actions": self.dashboard_actions(),
        }

    def live_status_summary(self, live_connection: dict[str, Any] | None = None) -> dict[str, Any]:
        connection = live_connection or self.live_connection_snapshot()
        return live_status_rules.live_status_summary(
            config=self.config,
            live_connection=connection,
            safety_status=self.safety_guard.status(),
            cooldown_remaining=round(float(self.safety_guard.output_cooldown_remaining()), 1),
            output_channel=self.dispatcher.output_channel_status(),
        )

    def live_state_summary(
        self,
        live_status: dict[str, Any] | None = None,
        health_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        status = live_status or self.live_status_summary()
        rows = health_rows if health_rows is not None else self.runtime_health_rows()
        engaged_threshold, idle_threshold = self._live_state_threshold_seconds()
        return live_status_rules.live_state_summary(
            config=self.config,
            live_status=status,
            health_rows=rows,
            recent_results=self.recent_results,
            warmup_observed=self._has_recent_response_module("warmup_hosting"),
            warmup_elapsed=self._solo_warmup_elapsed_seconds(),
            engaged_threshold=engaged_threshold,
            idle_threshold=idle_threshold,
            warmup_timeout_seconds=self._solo_warmup_timeout_seconds(),
            iso_age_fn=self._iso_age_sec,
        )

    def idle_hosting_status(self, live_state: dict[str, Any] | None = None) -> dict[str, Any]:
        state = live_state or self.live_state_summary()
        return live_status_rules.idle_hosting_status(
            live_state=state,
            now=float(self._idle_hosting_now()),
            last_attempt_at=float(self._idle_hosting_last_attempt_at or 0.0),
            min_interval_seconds=self._idle_hosting_min_interval_seconds(),
            consecutive_failures=int(self._idle_hosting_consecutive_failures),
            failure_limit=self._IDLE_HOSTING_FAILURE_LIMIT,
        )

    def active_engagement_status(
        self,
        live_status: dict[str, Any] | None = None,
        live_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = live_status or self.live_status_summary()
        state = live_state or self.live_state_summary(status)
        idle_takeover_streak = self._recent_actual_route_streak_since_viewer_activity("idle_hosting")
        return live_status_rules.active_engagement_status(
            config=self.config,
            live_status=status,
            live_state=state,
            now=float(self._active_engagement_now()),
            last_attempt_at=float(self._active_engagement_last_attempt_at or 0.0),
            min_interval_seconds=self._active_engagement_min_interval_seconds(),
            recent_danmaku_output_age=self._recent_live_danmaku_output_age_sec(),
            recent_danmaku_wait_seconds=self._active_engagement_after_danmaku_interval_seconds(),
            idle_hosting_wait_remaining=self._idle_hosting_wait_remaining_for_quiet_state(state),
            idle_grace_seconds=self._active_engagement_idle_grace_seconds(),
            idle_takeover_streak=(
                idle_takeover_streak
                if idle_takeover_streak >= self._IDLE_HOSTING_STREAK_FOR_ACTIVE_TAKEOVER
                else 0
            ),
        )

    def live_director_status(
        self,
        live_status: dict[str, Any] | None = None,
        live_state: dict[str, Any] | None = None,
        idle_hosting_status: dict[str, Any] | None = None,
        active_engagement_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = live_status or self.live_status_summary()
        state = live_state or self.live_state_summary(status)
        idle_status = idle_hosting_status or self.idle_hosting_status(state)
        active_status = active_engagement_status or self.active_engagement_status(status, state)
        return live_status_rules.live_director_status(
            config=self.config,
            live_status=status,
            live_state=state,
            idle_hosting_status=idle_status,
            active_engagement_status=active_status,
        )

    def solo_test_readiness(
        self,
        live_status: dict[str, Any] | None = None,
        live_state: dict[str, Any] | None = None,
        live_director_status: dict[str, Any] | None = None,
        profile_count: int = 0,
    ) -> dict[str, Any]:
        status = live_status or self.live_status_summary()
        state = live_state or self.live_state_summary(status)
        director = live_director_status or self.live_director_status(status, state)
        return live_status_rules.solo_test_readiness(
            config=self.config,
            live_status=status,
            live_state=state,
            live_director_status=director,
            profile_count=profile_count,
            warmup_observed=self._has_recent_response_module("warmup_hosting"),
        )

    def _has_recent_response_module(self, module_id: str) -> bool:
        target = str(module_id)
        for result in reversed(self.recent_results):
            if not isinstance(result, dict):
                continue
            if str(result.get("status") or "") not in {"pushed", "dry_run"}:
                continue
            if self._route_from_result(result) == target:
                return True
        return False

    def _recent_actual_route_streak_since_viewer_activity(self, module_id: str) -> int:
        target = str(module_id)
        streak = 0
        for result in reversed(self.recent_results):
            if not isinstance(result, dict):
                continue
            event = result.get("event") if isinstance(result.get("event"), dict) else {}
            if str(event.get("source") or "") == "live_danmaku":
                return streak
            if str(result.get("status") or "") not in {"pushed", "dry_run"}:
                continue
            if self._route_from_result(result) == target:
                streak += 1
                continue
            if streak > 0:
                return streak
        return streak

    def _active_engagement_min_interval_seconds(self) -> float:
        return live_status_rules.active_engagement_min_interval_seconds(self.config)

    def _active_engagement_after_danmaku_interval_seconds(self) -> float:
        return live_status_rules.active_engagement_after_danmaku_interval_seconds(self.config)

    def _active_engagement_idle_grace_seconds(self) -> float:
        return live_status_rules.active_engagement_idle_grace_seconds(
            self.config,
            float(self._ACTIVE_ENGAGEMENT_IDLE_GRACE_SECONDS),
        )

    def _idle_hosting_wait_remaining_for_quiet_state(self, live_state: dict[str, Any]) -> float | None:
        return live_status_rules.idle_hosting_wait_remaining_for_quiet_state(
            live_state,
            idle_threshold_fallback=self._live_state_threshold_seconds()[1],
        )

    def _idle_hosting_min_interval_seconds(self) -> float:
        return live_status_rules.idle_hosting_min_interval_seconds(self.config)

    def _solo_warmup_elapsed_seconds(self) -> float | None:
        if self._live_listener_started_at <= 0:
            return None
        return max(0.0, float(self._live_state_now()) - float(self._live_listener_started_at))

    def _solo_warmup_timeout_seconds(self) -> float:
        return live_status_rules.solo_warmup_timeout_seconds(
            self.config,
            float(self._SOLO_WARMUP_TIMEOUT_SECONDS),
        )

    def _live_state_threshold_seconds(self) -> tuple[float, float]:
        return live_status_rules.live_state_threshold_seconds(
            self.config,
            float(self._LIVE_STATE_ENGAGED_SECONDS),
            float(self._LIVE_STATE_IDLE_SECONDS),
        )

    def speech_explanation(
        self,
        live_status: dict[str, Any] | None = None,
        live_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = live_status or self.live_status_summary()
        state = live_state or self.live_state_summary(status)
        return live_status_rules.speech_explanation(
            live_status=status,
            live_state=state,
            latest_result=self.recent_results[-1] if self.recent_results else None,
            iso_age_fn=self._iso_age_sec,
        )

    def recent_interaction_context(self, *, limit: int = 3) -> list[str]:
        lines: list[str] = []
        for result in reversed(self.recent_results):
            if not isinstance(result, dict):
                continue
            if str(result.get("status") or "") not in {"pushed", "dry_run"}:
                continue
            event = result.get("event") if isinstance(result.get("event"), dict) else {}
            source = str(event.get("source") or "unknown")
            route = self._route_from_result(result)
            if source == "idle_hosting":
                beat_shape = str(event.get("host_beat_shape") or "").strip()
                beat_family = str(event.get("host_beat_family") or "").strip()
                beat_axis = str(event.get("host_beat_fun_axis") or "").strip()
                beat_column = str(event.get("host_beat_live_column") or "").strip()
                beat_stage = str(event.get("host_beat_idle_stage") or "").strip()
                beat_title = str(event.get("host_beat_title") or "").strip()
                beat_reply = str(event.get("host_beat_reply_affordance") or "").strip()
                beat_bits = " ".join(bit for bit in (beat_stage, beat_column, beat_shape, beat_family, beat_axis) if bit)
                if beat_title:
                    beat_bits = f"{beat_bits} - {self._compact_context_text(beat_title, limit=50)}".strip()
                if beat_reply:
                    beat_bits = f"{beat_bits} / reply: {self._compact_context_text(beat_reply, limit=60)}".strip()
                line = f"{route} / idle_hosting: {beat_bits or 'solo quiet-room host beat'}"
            elif source == "warmup_hosting":
                line = f"{route} / warmup_hosting: solo opening host beat"
            elif source == "active_engagement":
                topic_source = str(event.get("topic_source") or "").strip()
                topic_shape = str(event.get("topic_shape") or "").strip()
                topic_intent = str(event.get("topic_intent") or "").strip()
                topic_family = str(event.get("topic_family") or "").strip()
                topic_axis = str(event.get("topic_fun_axis") or "").strip()
                topic_column = str(event.get("topic_live_column") or "").strip()
                topic_pack = str(event.get("topic_pack") or "").strip()
                topic_title = str(event.get("topic_title") or "").strip()
                topic_bits = " ".join(
                    bit
                    for bit in (topic_pack, topic_column, topic_source, topic_shape, topic_intent, topic_family, topic_axis)
                    if bit
                )
                if topic_title:
                    topic_bits = f"{topic_bits} - {self._compact_context_text(topic_title, limit=50)}".strip()
                topic_reply = str(event.get("topic_reply_affordance") or "").strip()
                if topic_reply:
                    topic_bits = f"{topic_bits} / reply: {self._compact_context_text(topic_reply, limit=60)}".strip()
                line = f"{route} / active_engagement: {topic_bits or 'solo engagement beat'}"
            else:
                identity = result.get("identity") if isinstance(result.get("identity"), dict) else {}
                who = str(identity.get("nickname") or event.get("nickname") or event.get("uid") or "viewer")
                text = str(event.get("danmaku_text") or "").strip()
                line = f"{route} / {source} from {who}"
                if text:
                    line += f": {self._compact_context_text(text)}"
            output = self._spent_output_text(result)
            if output:
                families = self._spent_output_families(output)
                if families:
                    line += f" / spent_output_family={','.join(families)}"
                line += f" / NEKO already said: {self._compact_context_text(output, limit=60)}"
            lines.append(line)
            if len(lines) >= max(1, int(limit)):
                break
        return lines

    def viewer_session_context(self, uid: str, *, limit: int = 2) -> list[str]:
        target_uid = str(uid or "").strip()
        if not target_uid:
            return []
        lines: list[str] = []
        for result in reversed(self.recent_results):
            if not isinstance(result, dict):
                continue
            if str(result.get("status") or "") not in {"pushed", "dry_run"}:
                continue
            event = result.get("event") if isinstance(result.get("event"), dict) else {}
            if str(event.get("uid") or "").strip() != target_uid:
                continue
            text = str(event.get("danmaku_text") or "").strip()
            route = self._route_from_result(result)
            output = self._spent_output_text(result)
            if not text and not output:
                continue
            line = f"{route}: {self._compact_context_text(text, limit=60)}" if text else route
            if output:
                families = self._spent_output_families(output)
                if families:
                    line += f" / spent_output_family={','.join(families)}"
                line += f" / NEKO already said: {self._compact_context_text(output, limit=50)}"
            lines.append(line)
            if len(lines) >= max(1, int(limit)):
                break
        return lines

    @staticmethod
    def _spent_output_text(result: dict[str, Any]) -> str:
        return recent_context.spent_output_text(result)

    @staticmethod
    def _spent_output_families(output: str) -> list[str]:
        return recent_context.spent_output_families(output)

    def _recent_spent_output_families(self, *, limit: int = 12) -> set[str]:
        families: set[str] = set()
        seen_results = 0
        for result in reversed(self.recent_results):
            if not isinstance(result, dict):
                continue
            if str(result.get("status") or "") != "pushed":
                continue
            raw_family = str(result.get("spent_output_family") or "").strip()
            if not raw_family:
                raw_family = ",".join(self._spent_output_families(self._spent_output_text(result)))
            if not raw_family:
                continue
            seen_results += 1
            families.update(part.strip() for part in raw_family.split(",") if part.strip())
            if seen_results >= max(1, int(limit)):
                break
        return families

    @staticmethod
    def _compact_context_text(value: str, *, limit: int = 80) -> str:
        return recent_context.compact_context_text(value, limit=limit)

    @staticmethod
    def _route_from_result(result: dict[str, Any]) -> str:
        return recent_context.route_from_result(result)

    @staticmethod
    def _signal_route_for_event_type(event_type: str) -> str:
        return recent_context.signal_route_for_event_type(event_type)

    @staticmethod
    def _event_signal_from_result(result: dict[str, Any]) -> str:
        return recent_context.event_signal_from_result(result)

    def _recent_live_danmaku_output_age_sec(self) -> float | None:
        return live_status_rules.recent_live_danmaku_output_age_sec(self.recent_results, self._iso_age_sec)

    def _last_viewer_activity_age_sec(self, rows: list[dict[str, Any]]) -> float | None:
        return live_status_rules.last_viewer_activity_age_sec(rows, self.recent_results, self._iso_age_sec)

    def _last_output_age_sec(self, rows: list[dict[str, Any]]) -> float | None:
        return live_status_rules.last_output_age_sec(rows, self.recent_results, self._iso_age_sec)

    def _recent_live_danmaku_event_age_sec(self) -> float | None:
        return live_status_rules.recent_live_danmaku_event_age_sec(self.recent_results, self._iso_age_sec)

    @staticmethod
    def _age_sec(timestamp: Any) -> float | None:
        return live_status_rules.age_sec(timestamp)

    @staticmethod
    def _iso_age_sec(value: Any) -> float | None:
        return live_status_rules.iso_age_sec(value)

    @staticmethod
    def _status_from_outcome(outcome: str) -> str:
        if outcome == "failed":
            return "failed"
        if outcome == "skipped":
            return "blocked"
        if outcome in {"dry_run", "pushed", "ok"}:
            return "healthy"
        return "idle"

    @staticmethod
    def _module_status(module: Any) -> dict[str, Any]:
        status = getattr(module, "status", None)
        if not callable(status):
            return {}
        try:
            data = status()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def runtime_health_rows(self) -> list[dict[str, Any]]:
        ingest = self._module_status(self.bili_live_ingest)
        event_bus = self._module_status(self.event_bus)
        selection = self._module_status(self.live_events)
        latest = self.recent_results[-1] if self.recent_results else {}
        latest_status = str(latest.get("status") or "") if isinstance(latest, dict) else ""
        latest_reason = str(latest.get("reason") or "") if isinstance(latest, dict) else ""
        latest_age = self._iso_age_sec(latest.get("created_at")) if isinstance(latest, dict) else None
        latest_latency = latest.get("response_latency_ms") if isinstance(latest, dict) else None
        steps = latest.get("steps") if isinstance(latest, dict) else []
        dispatcher_step = None
        if isinstance(steps, list):
            dispatcher_step = next(
                (step for step in reversed(steps) if isinstance(step, dict) and step.get("id") == "neko_dispatcher"),
                None,
            )
        dispatcher_outcome = latest_status if dispatcher_step else ""
        safety_state = self.safety_guard.status()
        config_status = "failed" if self._config_last_error else ("healthy" if self._config_last_persist_at else "idle")
        output_channel = self.dispatcher.output_channel_status()
        output_channel_ready = bool(output_channel.get("ready"))
        output_channel_reason = str(output_channel.get("reason") or "")
        output_channel_detail = str(output_channel.get("detail") or "")
        return [
            {
                "id": "live_ingest",
                "stage": "ingest",
                "status": "healthy" if ingest.get("last_event_at") else "idle",
                "age_sec": self._age_sec(ingest.get("last_event_at")),
                "last_outcome": ingest.get("last_event_type", ""),
            },
            {
                "id": "event_bus",
                "stage": "event_bus",
                "status": "healthy" if event_bus.get("publish_count") else "idle",
                "count": int(event_bus.get("publish_count") or 0),
                "age_sec": self._age_sec(event_bus.get("last_publish_at")),
                "last_outcome": event_bus.get("last_event_type", ""),
            },
            {
                "id": "selection",
                "stage": "selection",
                "status": "healthy" if selection.get("last_decision_at") else "idle",
                "count": int(selection.get("last_candidate_count") or 0),
                "age_sec": self._age_sec(selection.get("last_decision_at")),
                "last_outcome": selection.get("last_selected_type", ""),
            },
            {
                "id": "pipeline",
                "stage": "pipeline",
                "status": self._status_from_outcome(latest_status),
                "age_sec": latest_age,
                "last_outcome": latest_status,
                "last_skip_reason": latest_reason if latest_status in {"dry_run", "skipped", "failed"} else "",
                "last_latency_ms": latest_latency,
            },
            {
                "id": "safety_guard",
                "stage": "safety_guard",
                "status": "healthy" if safety_state == "running" else ("degraded" if safety_state == "degraded" else "blocked"),
                "current_state": safety_state,
                "cooldown_remaining": round(float(self.safety_guard.output_cooldown_remaining()), 1),
            },
            {
                "id": "dispatcher",
                "stage": "dispatcher",
                "status": "blocked" if not output_channel_ready else self._status_from_outcome(dispatcher_outcome),
                "age_sec": latest_age if dispatcher_step else None,
                "last_outcome": dispatcher_outcome,
                "last_skip_reason": (
                    output_channel_reason or "output_channel_unavailable"
                    if not output_channel_ready
                    else latest_reason if dispatcher_outcome in {"dry_run", "skipped", "failed"} else ""
                ),
                "last_latency_ms": latest_latency if dispatcher_step else None,
                "output_channel_ready": output_channel_ready,
                "output_channel_detail": output_channel_detail,
            },
            {
                "id": "config_store",
                "stage": "config_store",
                "status": config_status,
                "age_sec": self._age_sec(self._config_last_persist_at),
                "last_error": self._config_last_error,
            },
        ]

    def dashboard_actions(self) -> list[dict[str, str]]:
        action_ids = [
            "update_config",
            "pick_folder",  # 面板「档案存储」卡的「浏览…」调它弹原生选目录框；必须在此暴露给 surface 才不被 403
            "set_live_room",
            "lookup_live_room",
            "connect_live_room",
            "disconnect_live_room",
            "pause_roast",
            "resume_roast",
            "clear_queue",
            "trigger_idle_hosting",
            "trigger_warmup_hosting",
            "trigger_active_engagement",
            "submit_viewer_event",
            "clear_sandbox_data",
            "clear_viewer_profiles",
            "bili_login",
            "bili_login_check",
            "bili_login_status",
            "bili_logout",
        ]
        return [{"id": action_id, "entry_id": action_id} for action_id in action_ids]

    def pause(self) -> None:
        self.safety_guard.pause("manual pause from control panel")

    def resume(self) -> None:
        self.safety_guard.resume()

    def clear_queue(self) -> None:
        self.safety_guard.clear_queue()
        self.audit.record("queue_clear", "queue cleared")

    async def clear_viewer_profiles(self) -> dict[str, Any]:
        self._require_developer_mode()
        result = await self.viewer_store.clear_profiles()
        self.pipeline.clear_dry_run_session_state()
        self.audit.record("viewer_profiles_clear", "viewer profiles cleared", detail=result)
        return result

    def live_connection_snapshot(self) -> dict[str, Any]:
        if self.bili_live_ingest.is_listening():
            ls = self.bili_live_ingest.listener_state()
            state = str(ls.get("state") or "disconnected")
            viewer = int(ls.get("viewer_count") or 0)
        else:
            state = self.live_connection_state
            viewer = 0
        connected = state in ("receiving", "connected")
        return {
            "room_id": self.config.live_room_id,
            "state": state,
            "connected": connected,
            "listening": connected and self.config.live_enabled,
            "viewer_count": viewer,
        }

    async def set_live_room(self, room_id: Any) -> RoastConfig:
        room_id = parse_room_id(room_id)
        if room_id <= 0:
            raise ValueError("room_id must be positive")
        old_room_id = self.config.live_room_id
        config = await self.update_config({"live_room_id": room_id})
        if old_room_id != room_id and not self.bili_live_ingest.is_listening():
            self.live_connection_state = "disconnected"
            self.safety_guard.set_connected(False)
        self.audit.record("live_room_set", "live room updated", detail={"room_id": room_id})
        return config

    async def connect_live_room(self, room_id: Any = 0) -> dict[str, Any]:
        target_room_id = parse_room_id(room_id) or int(self.config.live_room_id or 0)
        if target_room_id <= 0:
            raise ValueError("room_id must be configured before connecting")
        if target_room_id != self.config.live_room_id:
            await self.set_live_room(target_room_id)
            if self.bili_live_ingest.is_listening() and int(self.config.live_room_id or 0) == target_room_id:
                return self.live_connection_snapshot()
        self.config.live_enabled = True  # 内存即时生效（gate/safety 共享同一 config 对象），避免配置写竞争拖垮连接
        started = await self._start_live_listener(target_room_id)
        await self.sync_live_instructions()
        self.audit.record(
            "live_connected" if started else "live_connect_failed",
            "danmaku listener started" if started else "failed to start danmaku listener",
            level="info" if started else "warning",
            detail={"room_id": target_room_id},
        )
        return self.live_connection_snapshot()

    async def disconnect_live_room(self) -> dict[str, Any]:
        await self._stop_live_listener(mark_disabled=True)
        self.audit.record("live_disconnected", "live ingest marked disconnected", detail={"room_id": self.config.live_room_id})
        return self.live_connection_snapshot()
