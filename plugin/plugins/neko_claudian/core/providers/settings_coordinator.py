# Ported from claudian/src/core/providers/ProviderSettingsCoordinator.ts
# Original author: Claudian contributors
# License: MIT

"""
Provider settings coordinator.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ProviderSettingsCoordinator:
    """Coordinate provider settings.

    Ported from ProviderSettingsCoordinator.ts
    """

    @classmethod
    def get_provider_settings_snapshot(
        cls,
        settings: Any,
        provider_id: str,
    ) -> Dict[str, Any]:
        """Get a snapshot of provider settings."""
        if hasattr(settings, 'provider_configs'):
            configs = settings.provider_configs
            if isinstance(configs, dict):
                return configs.get(provider_id, {})

        if isinstance(settings, dict):
            configs = settings.get("providerConfigs", {})
            return configs.get(provider_id, {})

        return {}

    @classmethod
    def get_model(cls, settings: Any, provider_id: str) -> str:
        """Get the model for a provider."""
        provider_settings = cls.get_provider_settings_snapshot(settings, provider_id)
        return provider_settings.get("model", "claude-sonnet-4-20250514")

    @classmethod
    def get_effort_level(cls, settings: Any, provider_id: str) -> str:
        """Get the effort level for a provider."""
        provider_settings = cls.get_provider_settings_snapshot(settings, provider_id)
        return provider_settings.get("effortLevel", "high")
