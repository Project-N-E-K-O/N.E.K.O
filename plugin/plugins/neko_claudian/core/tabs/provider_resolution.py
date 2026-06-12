# Ported from claudian/src/features/chat/tabs/providerResolution.ts
# Original author: Claudian contributors
# License: MIT

"""
Provider resolution for tabs.
"""

from __future__ import annotations

from typing import Any, Optional


def get_tab_provider_id(tab: Any, plugin: Any) -> str:
    """Get the provider ID for a tab.

    Ported from claudian/src/features/chat/tabs/providerResolution.ts getTabProviderId.
    """
    # Check tab's service first
    if hasattr(tab, 'service') and tab.service:
        provider_id = getattr(tab.service, 'provider_id', None)
        if provider_id:
            return provider_id

    # Check tab's provider_id
    if hasattr(tab, 'provider_id') and tab.provider_id:
        return tab.provider_id

    # Check conversation
    if hasattr(tab, 'conversation_id') and tab.conversation_id:
        conversation = plugin.get_conversation_sync(tab.conversation_id)
        if conversation:
            return conversation.get("providerId", "claude")

    # Default
    return "claude"
