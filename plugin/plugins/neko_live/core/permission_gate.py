"""Mode and safety gates for viewer interaction modules."""

from __future__ import annotations

from .contracts import LiveConfig, TriggerSource


class PermissionGate:
    def __init__(self, config: LiveConfig) -> None:
        self.config = config

    def update(self, config: LiveConfig) -> None:
        self.config = config

    def allows_source(self, source: TriggerSource) -> tuple[bool, str]:
        if source == "developer_sandbox":
            if not self.config.developer_tools_enabled:
                return False, "developer tools are disabled"
            return True, ""
        if source == "manual_live_simulation":
            if not self.config.developer_tools_enabled:
                return False, "developer tools are disabled"
            if not self.config.live_enabled:
                return False, "live roast is disabled"
            return True, ""
        if source in {"live_danmaku", "idle_hosting", "active_engagement", "warmup_hosting"}:
            if not self.config.live_enabled:
                return False, "live roast is disabled"
            return True, ""
        return False, f"unsupported source: {source}"
