"""NEKO Live runtime assembly."""

from __future__ import annotations

import asyncio

from ..adapters.neko_dispatcher import NekoDispatcher
from ..stores.audit_store import AuditStore
from ..stores.avatar_cache import AvatarCache
from ..stores.viewer_store import ViewerStore
from .contracts import LiveConfig
from .event_bus import EventBus
from . import (
    runtime_bili_auth,
    runtime_co_stream_policy,
    runtime_douyin_auth,
    runtime_modules,
    runtime_state,
    runtime_twitch_auth,
)
from .runtime_auth_api import RuntimeAuthApiMixin
from .runtime_config_api import RuntimeConfigApiMixin
from .permission_gate import PermissionGate
from .safety_guard import SafetyGuard
from .active_topic_selector import ActiveTopicSelector
from .live_hosting_director import LiveHostingDirector
from .runtime_active_engagement_api import RuntimeActiveEngagementApiMixin
from .runtime_control_api import RuntimeControlApiMixin
from .runtime_developer_api import RuntimeDeveloperApiMixin
from .runtime_hosting_api import RuntimeHostingApiMixin
from .runtime_instruction_api import RuntimeInstructionApiMixin
from .runtime_live_input_api import RuntimeLiveInputApiMixin
from .runtime_status_api import RuntimeStatusApiMixin


class LiveRuntime(
    RuntimeAuthApiMixin,
    RuntimeInstructionApiMixin,
    RuntimeConfigApiMixin,
    RuntimeLiveInputApiMixin,
    RuntimeDeveloperApiMixin,
    RuntimeControlApiMixin,
    RuntimeHostingApiMixin,
    RuntimeStatusApiMixin,
    RuntimeActiveEngagementApiMixin,
):
    # The host persists plugin config with a small budget; explicit update/connect actions still await persistence.
    _CONFIG_PERSIST_BUDGET_SECONDS = 4.0
    _LIVE_STATE_ENGAGED_SECONDS = 60.0
    _LIVE_STATE_IDLE_SECONDS = 120.0
    _IDLE_HOSTING_CHECK_INTERVAL_SECONDS = 5.0
    _IDLE_HOSTING_MIN_INTERVAL_SECONDS = 120.0
    _IDLE_HOSTING_FAILURE_LIMIT = 3
    _IDLE_HOSTING_STREAK_FOR_ACTIVE_TAKEOVER = 1
    _SOLO_WARMUP_TIMEOUT_SECONDS = 45.0
    _ACTIVE_ENGAGEMENT_AFTER_DANMAKU_INTERVAL_SECONDS = 75.0
    _ACTIVE_ENGAGEMENT_RECENT_DANMAKU_TOPIC_MAX_AGE_SECONDS = 360.0
    _ACTIVE_ENGAGEMENT_IDLE_GRACE_SECONDS = 25.0
    _HOSTING_OUTPUT_COOLDOWN_SECONDS = 30.0

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.config = LiveConfig()
        self.audit = AuditStore(limit=100)
        self.avatar_cache = AvatarCache()
        self.viewer_store = ViewerStore(
            plugin,
            self.audit,
            lambda: self.config.viewer_store_dir,
            memory_enabled_provider=lambda: self.config.viewer_memory_enabled,
        )
        self.permission_gate = PermissionGate(self.config)
        self.safety_guard = SafetyGuard(self.config, self.audit)
        self.dispatcher = NekoDispatcher(plugin)
        self.event_bus = EventBus(self.audit)  # Keep audit-owned subscriber isolation visible from runtime assembly.
        # Bilibili login state: encrypted credential store plus QR-code login service.
        self.credential_store = runtime_bili_auth.create_credential_store(plugin, self.audit)
        self.bili_credential: Any = None  # Cached bilibili_api.Credential; None means not logged in.
        self.bili_auth = runtime_bili_auth.create_auth_service(self, plugin)
        # Douyin v1 uses manual cookie import only; no browser automation or auto-login.
        self.douyin_credential_store = runtime_douyin_auth.create_credential_store(plugin, self.audit)
        self.douyin_credential: dict[str, Any] | None = None
        # Twitch uses official Device Code Flow; device codes remain in-memory.
        self.twitch_credential_store = runtime_twitch_auth.create_credential_store(plugin, self.audit)
        self.twitch_credential: dict[str, Any] | None = None
        self.twitch_auth = runtime_twitch_auth.create_auth_service(self)
        runtime_state.initialize_runtime_state(self)
        runtime_co_stream_policy.initialize_co_stream_policy(self)
        self.live_hosting_director = LiveHostingDirector(self)
        self.active_topic_selector = ActiveTopicSelector(self)

        runtime_modules.assemble_runtime_modules(self)

    async def start(self) -> None:
        self._stopping = False
        await self.reload_config()
        await self.viewer_store.prune_expired_profiles()
        await self.reload_credential()
        await self.reload_douyin_credential()
        await self.reload_twitch_credential()
        await self.registry.setup_all(self)
        self._start_idle_hosting_loop()
        self.audit.record("runtime_start", "neko_live runtime ready")

    async def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        failures: list[str] = []
        steps = (
            ("idle_hosting", self._stop_idle_hosting_loop),
            ("live_listener", lambda: self._stop_live_listener(mark_disabled=False)),
            ("event_bus", self.event_bus.close),
            ("modules", lambda: self.registry.teardown_all(self)),
            ("developer_instructions", lambda: self.restore_developer_instructions(force=True)),
            ("live_instructions", lambda: self.restore_instructions(force=True)),
        )
        for step, operation in steps:
            try:
                await operation()
            except asyncio.CancelledError:
                self._stopping = False
                raise
            except Exception as exc:
                failures.append(step)
                self.audit.record(
                    "runtime_stop_step_failed",
                    f"shutdown step failed: {type(exc).__name__}",
                    level="warning",
                    detail={"step": step},
                )
        self.audit.record(
            "runtime_stop",
            "neko_live runtime stopped",
            level="warning" if failures else "info",
            detail={"failed_steps": failures},
        )
