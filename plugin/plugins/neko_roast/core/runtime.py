"""Neko Roast runtime assembly."""

from __future__ import annotations

from typing import Any

from ..adapters.neko_dispatcher import NekoDispatcher
from ..stores.audit_store import AuditStore
from ..stores.avatar_cache import AvatarCache
from ..stores.viewer_store import ViewerStore
from .contracts import RoastConfig
from .event_bus import EventBus
from . import runtime_bili_auth, runtime_douyin_auth, runtime_modules, runtime_state
from .runtime_auth_api import RuntimeAuthApiMixin
from .runtime_config_api import RuntimeConfigApiMixin
from .permission_gate import PermissionGate
from .safety_guard import SafetyGuard

try:
    from .runtime_control_api import RuntimeControlApiMixin
except ImportError:
    class RuntimeControlApiMixin:
        pass

try:
    from .runtime_developer_api import RuntimeDeveloperApiMixin
except ImportError:
    class RuntimeDeveloperApiMixin:
        pass

try:
    from .runtime_instruction_api import RuntimeInstructionApiMixin
except ImportError:
    class RuntimeInstructionApiMixin:
        pass

try:
    from .runtime_status_api import RuntimeStatusApiMixin
except ImportError:
    class RuntimeStatusApiMixin:
        def live_status_summary(self) -> dict[str, Any]:
            return {"summary": "test_only", "reason": "status_api_unavailable"}

try:
    from .runtime_active_engagement_api import RuntimeActiveEngagementApiMixin
except ImportError:
    class RuntimeActiveEngagementApiMixin:
        pass

try:
    from .runtime_hosting_api import RuntimeHostingApiMixin
except ImportError:
    class RuntimeHostingApiMixin:
        def _start_idle_hosting_loop(self) -> None:
            return None

        async def _stop_idle_hosting_loop(self) -> None:
            return None

try:
    from .runtime_live_input_api import RuntimeLiveInputApiMixin
except ImportError:
    class RuntimeLiveInputApiMixin:
        pass

try:
    from .active_topic_selector import ActiveTopicSelector
except ImportError:
    class ActiveTopicSelector:
        def __init__(self, runtime: Any) -> None:
            self.runtime = runtime

try:
    from .live_hosting_director import LiveHostingDirector
except ImportError:
    class LiveHostingDirector:
        def __init__(self, runtime: Any) -> None:
            self.runtime = runtime


class RoastRuntime(
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
        self.config = RoastConfig()
        self.audit = AuditStore(limit=100)
        self.avatar_cache = AvatarCache()
        self.viewer_store = ViewerStore(plugin, self.audit, lambda: self.config.viewer_store_dir)
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
        runtime_state.initialize_runtime_state(self)
        self.live_hosting_director = LiveHostingDirector(self)
        self.active_topic_selector = ActiveTopicSelector(self)

        runtime_modules.assemble_runtime_modules(self)

    async def start(self) -> None:
        await self.reload_config()
        await self.reload_credential()
        await self.reload_douyin_credential()
        await self.registry.setup_all(self)
        self._start_idle_hosting_loop()
        self.audit.record("runtime_start", "neko_roast runtime ready")

    async def stop(self) -> None:
        await self._stop_idle_hosting_loop()
        await self.restore_developer_instructions(force=True)
        await self.restore_instructions(force=True)
        await self.registry.teardown_all()
        self.audit.record("runtime_stop", "neko_roast runtime stopped")
