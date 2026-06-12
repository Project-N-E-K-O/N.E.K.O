# Ported from claudian/src/core/providers/ProviderRegistry.ts
# Original author: Claudian contributors
# License: MIT

"""
Provider registry — Register and manage providers.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Registry for providers.

    Ported from ProviderRegistry.ts
    """

    _providers: Dict[str, Dict[str, Any]] = {}
    _capabilities: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def register(cls, provider_id: str, config: Dict[str, Any]) -> None:
        """Register a provider."""
        cls._providers[provider_id] = config
        cls._capabilities[provider_id] = config.get("capabilities", {})

    @classmethod
    def get_provider(cls, provider_id: str) -> Optional[Dict[str, Any]]:
        """Get a provider by ID."""
        return cls._providers.get(provider_id)

    @classmethod
    def get_capabilities(cls, provider_id: str) -> Dict[str, Any]:
        """Get capabilities for a provider."""
        return cls._capabilities.get(provider_id, {})

    @classmethod
    def get_all_providers(cls) -> List[str]:
        """Get all registered provider IDs."""
        return list(cls._providers.keys())

    @classmethod
    def has_provider(cls, provider_id: str) -> bool:
        """Check if a provider is registered."""
        return provider_id in cls._providers

    @classmethod
    def get_conversation_history_service(cls, provider_id: str) -> Any:
        """Get conversation history service for a provider."""
        provider = cls._providers.get(provider_id)
        if provider:
            return provider.get("conversationHistoryService")
        return None
