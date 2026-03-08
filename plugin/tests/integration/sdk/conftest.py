from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SdkIntegrationCtx:
    """Shared SDK integration test context with sync+async capability."""

    config_data: dict[str, Any]
    plugin_id: str = "sdk-integration"

    @staticmethod
    def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in updates.items():
            current = merged.get(key)
            if isinstance(current, dict) and isinstance(value, dict):
                merged[key] = SdkIntegrationCtx._deep_merge(current, value)
            else:
                merged[key] = value
        return merged

    # ---- plugin.sdk.plugins (sync) ----
    def query_plugins(self, filters: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
        _ = (filters, timeout)
        return {"plugins": [{"plugin_id": "demo"}, {"plugin_id": "sdk-integration"}]}

    def trigger_plugin_event(
        self,
        *,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        _ = (timeout,)
        # echo contract for integration assertions
        return {
            "target_plugin_id": target_plugin_id,
            "event_type": event_type,
            "event_id": event_id,
            "params": params,
        }

    # ---- plugin.sdk.plugins (async-native) ----
    async def query_plugins_async(self, filters: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
        return self.query_plugins(filters, timeout)

    async def trigger_plugin_event_async(
        self,
        *,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        return self.trigger_plugin_event(
            target_plugin_id=target_plugin_id,
            event_type=event_type,
            event_id=event_id,
            params=params,
            timeout=timeout,
        )

    # ---- plugin.sdk.config ----
    async def get_own_config(self, timeout: float = 5.0) -> dict[str, Any]:
        _ = timeout
        return {"config": self.config_data}

    async def update_own_config(self, updates: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        _ = timeout
        self.config_data = self._deep_merge(self.config_data, updates)
        return {"config": self.config_data}
