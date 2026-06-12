# Ported from claudian/src/features/chat/controllers/ConversationController.ts
# Original author: Claudian contributors
# License: MIT

"""
ConversationController — Manages conversation lifecycle.

Handles creating, switching, saving, and deleting conversations.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ConversationControllerDeps:
    """Dependencies for ConversationController."""
    state: Any  # ChatState instance
    renderer: Any  # MessageRenderer instance
    stream_controller: Any  # StreamController instance
    get_agent_service: Callable[[], Optional[Any]]
    get_plugin: Callable[[], Any]
    generate_id: Callable[[], str]


class ConversationController:
    """Manages conversation lifecycle.

    Ported from claudian/src/features/chat/controllers/ConversationController.ts
    """

    def __init__(self, deps: ConversationControllerDeps):
        self._deps = deps

    @property
    def state(self) -> Any:
        return self._deps.state

    @property
    def plugin(self) -> Any:
        return self._deps.get_plugin()

    # ============================================
    # Conversation Creation
    # ============================================

    async def create_new(self) -> str:
        """Create a new conversation.

        Returns the new conversation ID.
        """
        state = self.state

        # Reset streaming state
        self._deps.stream_controller.reset_streaming_state()

        # Reset state for new conversation
        state.reset_for_new_conversation()

        # Create conversation in plugin
        conversation_id = self._deps.generate_id()
        state.current_conversation_id = conversation_id

        # Create conversation object
        conversation = {
            "id": conversation_id,
            "providerId": "claude",
            "title": "New Conversation",
            "createdAt": time.time(),
            "updatedAt": time.time(),
            "messages": [],
        }

        # Store in plugin
        self.plugin.create_conversation(conversation)

        logger.info(f"Created new conversation: {conversation_id}")
        return conversation_id

    # ============================================
    # Conversation Switching
    # ============================================

    async def switch_to(self, conversation_id: str) -> None:
        """Switch to an existing conversation."""
        state = self.state

        if state.current_conversation_id == conversation_id:
            return

        state.is_switching_conversation = True

        try:
            # Save current conversation
            if state.current_conversation_id:
                await self.save()

            # Load new conversation
            conversation = self.plugin.get_conversation(conversation_id)
            if not conversation:
                logger.error(f"Conversation not found: {conversation_id}")
                return

            # Reset streaming state
            self._deps.stream_controller.reset_streaming_state()

            # Update state
            state.current_conversation_id = conversation_id
            state.messages = conversation.get("messages", [])
            state.usage = conversation.get("usage")

            # Render messages
            self._deps.renderer.clear_messages()
            for msg in state.messages:
                self._deps.renderer.add_message(msg)

            logger.info(f"Switched to conversation: {conversation_id}")

        finally:
            state.is_switching_conversation = False

    # ============================================
    # Conversation Saving
    # ============================================

    async def save(self, force: bool = False, extras: Optional[Dict[str, Any]] = None) -> None:
        """Save the current conversation."""
        state = self.state

        if not state.current_conversation_id:
            return

        if not force and not state.has_pending_conversation_save:
            return

        # Build conversation data
        conversation_data = {
            "id": state.current_conversation_id,
            "messages": state.get_persisted_messages(),
            "updatedAt": time.time(),
            "usage": state.usage,
        }

        if extras:
            conversation_data.update(extras)

        # Save to plugin
        self.plugin.update_conversation(state.current_conversation_id, conversation_data)
        state.has_pending_conversation_save = False

        logger.debug(f"Saved conversation: {state.current_conversation_id}")

    # ============================================
    # Conversation Deletion
    # ============================================

    async def delete(self, conversation_id: str) -> None:
        """Delete a conversation."""
        state = self.state

        # If deleting current conversation, create new one
        if state.current_conversation_id == conversation_id:
            await self.create_new()

        # Delete from plugin
        self.plugin.delete_conversation(conversation_id)
        logger.info(f"Deleted conversation: {conversation_id}")

    # ============================================
    # Conversation Renaming
    # ============================================

    async def rename(self, conversation_id: str, new_title: str) -> None:
        """Rename a conversation."""
        self.plugin.rename_conversation(conversation_id, new_title)
        logger.info(f"Renamed conversation {conversation_id} to: {new_title}")

    # ============================================
    # History Management
    # ============================================

    def get_conversation_list(self) -> List[Dict[str, Any]]:
        """Get list of all conversations."""
        return self.plugin.get_conversation_list()

    def update_history_dropdown(self) -> None:
        """Update the history dropdown UI."""
        # In full version, this would update the dropdown
        pass

    # ============================================
    # Title Generation
    # ============================================

    def generate_fallback_title(self, content: str) -> str:
        """Generate a fallback title from content."""
        # Take first 50 characters
        title = content[:50].strip()
        if len(content) > 50:
            title += "..."
        return title or "New Conversation"

    async def trigger_title_generation(self, conversation_id: str, content: str) -> None:
        """Trigger AI title generation for a conversation."""
        # Set fallback title first
        fallback_title = self.generate_fallback_title(content)
        await self.rename(conversation_id, fallback_title)

        # In full version, this would trigger async AI title generation
        logger.info(f"Title generation triggered for {conversation_id}")
