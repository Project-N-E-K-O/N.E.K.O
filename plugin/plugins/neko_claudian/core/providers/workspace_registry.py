# Ported from claudian/src/core/providers/ProviderWorkspaceRegistry.ts
# Original author: Claudian contributors
# License: MIT

"""
Provider workspace registry.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class ProviderWorkspaceRegistry:
    """Registry for provider workspace services.

    Ported from ProviderWorkspaceRegistry.ts
    """

    _command_catalogs: Dict[str, Any] = {}
    _runtime_command_loaders: Dict[str, Any] = {}
    _tab_warmup_policies: Dict[str, Any] = {}

    @classmethod
    def get_command_catalog(cls, provider_id: str) -> Optional[Any]:
        """Get command catalog for a provider."""
        return cls._command_catalogs.get(provider_id)

    @classmethod
    def set_command_catalog(cls, provider_id: str, catalog: Any) -> None:
        """Set command catalog for a provider."""
        cls._command_catalogs[provider_id] = catalog

    @classmethod
    def get_runtime_command_loader(cls, provider_id: str) -> Optional[Any]:
        """Get runtime command loader for a provider."""
        return cls._runtime_command_loaders.get(provider_id)

    @classmethod
    def set_runtime_command_loader(cls, provider_id: str, loader: Any) -> None:
        """Set runtime command loader for a provider."""
        cls._runtime_command_loaders[provider_id] = loader

    @classmethod
    def get_tab_warmup_policy(cls, provider_id: str) -> Optional[Any]:
        """Get tab warmup policy for a provider."""
        return cls._tab_warmup_policies.get(provider_id)

    @classmethod
    def set_tab_warmup_policy(cls, provider_id: str, policy: Any) -> None:
        """Set tab warmup policy for a provider."""
        cls._tab_warmup_policies[provider_id] = policy
