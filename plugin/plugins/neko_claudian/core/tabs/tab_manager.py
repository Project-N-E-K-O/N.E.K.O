# Ported from claudian/src/features/chat/tabs/TabManager.ts
# Original author: Claudian contributors
# License: MIT

"""
TabManager — Coordinates multiple chat tabs.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Set

from .tab import (
    activate_tab,
    create_tab,
    deactivate_tab,
    destroy_tab,
    get_tab_title,
    initialize_tab_controllers,
    initialize_tab_service,
    initialize_tab_ui,
    setup_service_callbacks,
    wire_tab_input_events,
)
from .types import (
    DEFAULT_MAX_TABS,
    MAX_TABS,
    MIN_TABS,
    ForkContext,
    PersistedTabManagerState,
    PersistedTabState,
    TabBarItem,
    TabData,
    TabId,
    TabManagerCallbacks,
)

logger = logging.getLogger(__name__)


class TabManager:
    """Coordinates multiple chat tabs.

    Ported from claudian/src/features/chat/tabs/TabManager.ts
    """

    def __init__(
        self,
        plugin: Any,
        container_el: Any = None,
        view: Any = None,
        callbacks: Optional[TabManagerCallbacks] = None,
    ):
        self._plugin = plugin
        self._container_el = container_el
        self._view = view
        self._callbacks = callbacks or TabManagerCallbacks()
        self._tabs: Dict[TabId, TabData] = {}
        self._active_tab_id: Optional[TabId] = None
        self._is_switching_tab = False
        self._is_restoring_state = False

    def _get_max_tabs(self) -> int:
        """Get the maximum number of tabs from settings."""
        settings = getattr(self._plugin, 'settings', None)
        max_tabs = getattr(settings, 'max_tabs', DEFAULT_MAX_TABS) if settings else DEFAULT_MAX_TABS
        return max(MIN_TABS, min(MAX_TABS, max_tabs))

    # ============================================
    # Tab Lifecycle
    # ============================================

    async def create_tab(
        self,
        conversation_id: Optional[str] = None,
        tab_id: Optional[TabId] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Optional[TabData]:
        """Create a new tab.

        Returns the created tab, or None if max tabs reached.
        """
        max_tabs = self._get_max_tabs()
        if len(self._tabs) >= max_tabs:
            return None

        options = options or {}
        activate = options.get("activate", True)
        draft_model = options.get("draft_model")

        # Get conversation if specified
        conversation = None
        if conversation_id:
            conversation = self._plugin.get_conversation_sync(conversation_id)

        # Inherit provider from active tab
        active_tab = self.get_active_tab()
        default_provider_id = None
        if not conversation and active_tab:
            default_provider_id = active_tab.provider_id

        # Create tab
        tab = create_tab(
            plugin=self._plugin,
            container_el=self._container_el,
            conversation=conversation,
            tab_id=tab_id,
            draft_model=draft_model,
            default_provider_id=default_provider_id,
        )

        # Initialize UI and controllers
        initialize_tab_ui(tab, self._plugin)
        initialize_tab_controllers(tab, self._plugin, self._view)
        wire_tab_input_events(tab, self._plugin)

        self._tabs[tab.id] = tab
        self._callbacks.on_tab_created(tab)

        if not self._is_restoring_state and (activate or not self._active_tab_id):
            await self.switch_to_tab(tab.id)

        return tab

    async def switch_to_tab(self, tab_id: TabId) -> None:
        """Switch to a different tab."""
        tab = self._tabs.get(tab_id)
        if not tab:
            return

        if self._is_switching_tab:
            return

        self._is_switching_tab = True
        previous_tab_id = self._active_tab_id

        try:
            # Deactivate current tab
            if previous_tab_id and previous_tab_id != tab_id:
                current_tab = self._tabs.get(previous_tab_id)
                if current_tab:
                    deactivate_tab(current_tab)

            # Activate new tab
            self._active_tab_id = tab_id
            activate_tab(tab)

            self._callbacks.on_tab_switched(previous_tab_id, tab_id)

        finally:
            self._is_switching_tab = False

    async def close_tab(self, tab_id: TabId, force: bool = False) -> bool:
        """Close a tab.

        Returns True if the tab was closed.
        """
        tab = self._tabs.get(tab_id)
        if not tab:
            return False

        # Don't close if streaming unless forced
        if tab.state and tab.state.is_streaming and not force:
            return False

        # Don't close last empty tab
        if len(self._tabs) == 1 and not tab.conversation_id:
            if tab.state and len(tab.state.messages) == 0:
                return False

        # Save conversation before closing
        conv_controller = tab.controllers.get("conversation_controller")
        if conv_controller:
            await conv_controller.save()

        # Get tab order for fallback
        tab_ids = list(self._tabs.keys())
        closing_index = tab_ids.index(tab_id)

        # Destroy tab
        await destroy_tab(tab)
        del self._tabs[tab_id]
        self._callbacks.on_tab_closed(tab_id)

        # Switch to another tab if we closed the active one
        if self._active_tab_id == tab_id:
            self._active_tab_id = None

            if self._tabs:
                # Fallback: prefer previous tab
                fallback_id = (
                    tab_ids[closing_index - 1]
                    if closing_index > 0
                    else tab_ids[1] if len(tab_ids) > 1 else None
                )
                if fallback_id and fallback_id in self._tabs:
                    await self.switch_to_tab(fallback_id)
            else:
                # Create replacement tab
                await self.create_tab()

        return True

    # ============================================
    # Tab Queries
    # ============================================

    def get_active_tab(self) -> Optional[TabData]:
        """Get the currently active tab."""
        return self._tabs.get(self._active_tab_id) if self._active_tab_id else None

    def get_active_tab_id(self) -> Optional[TabId]:
        """Get the active tab ID."""
        return self._active_tab_id

    def get_tab(self, tab_id: TabId) -> Optional[TabData]:
        """Get a tab by ID."""
        return self._tabs.get(tab_id)

    def get_all_tabs(self) -> List[TabData]:
        """Get all tabs."""
        return list(self._tabs.values())

    def get_tab_count(self) -> int:
        """Get the number of tabs."""
        return len(self._tabs)

    def can_create_tab(self) -> bool:
        """Check if more tabs can be created."""
        return len(self._tabs) < self._get_max_tabs()

    # ============================================
    # Tab Bar Data
    # ============================================

    def get_tab_bar_items(self) -> List[TabBarItem]:
        """Get data for rendering the tab bar."""
        items = []
        for index, tab in enumerate(self._tabs.values(), 1):
            items.append(TabBarItem(
                id=tab.id,
                index=index,
                title=get_tab_title(tab, self._plugin),
                provider_id=tab.provider_id,
                is_active=tab.id == self._active_tab_id,
                is_streaming=tab.state.is_streaming if tab.state else False,
                needs_attention=tab.state.needs_attention if tab.state else False,
                can_close=len(self._tabs) > 1,
            ))
        return items

    # ============================================
    # Conversation Management
    # ============================================

    async def open_conversation(
        self,
        conversation_id: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Open a conversation in a tab."""
        options = options or {}
        prefer_new_tab = options.get("prefer_new_tab", False)

        # Check if already open
        for tab in self._tabs.values():
            if tab.conversation_id == conversation_id:
                await self.switch_to_tab(tab.id)
                return

        # Open in new tab or current tab
        if prefer_new_tab and self.can_create_tab():
            await self.create_tab(conversation_id)
        else:
            active_tab = self.get_active_tab()
            if active_tab:
                conv_controller = active_tab.controllers.get("conversation_controller")
                if conv_controller:
                    await conv_controller.switch_to(conversation_id)

    async def create_new_conversation(self) -> None:
        """Create a new conversation in the active tab."""
        active_tab = self.get_active_tab()
        if active_tab:
            conv_controller = active_tab.controllers.get("conversation_controller")
            if conv_controller:
                await conv_controller.create_new()

    # ============================================
    # Fork
    # ============================================

    async def fork_to_new_tab(self, context: ForkContext) -> Optional[TabData]:
        """Fork a conversation to a new tab."""
        if not self.can_create_tab():
            return None

        # Create conversation
        conversation = self._plugin.create_conversation({
            "providerId": context.provider_id,
        })

        # Update with fork data
        self._plugin.update_conversation(conversation["id"], {
            "messages": context.messages,
            "title": f"Fork: {context.source_title}" if context.source_title else "Fork",
        })

        return await self.create_tab(conversation["id"])

    # ============================================
    # Persistence
    # ============================================

    def get_persisted_state(self) -> PersistedTabManagerState:
        """Get state to persist."""
        open_tabs = []
        for tab in self._tabs.values():
            open_tabs.append(PersistedTabState(
                tab_id=tab.id,
                conversation_id=tab.conversation_id,
                draft_model=tab.draft_model,
            ))

        return PersistedTabManagerState(
            open_tabs=open_tabs,
            active_tab_id=self._active_tab_id,
        )

    async def restore_state(self, state: PersistedTabManagerState) -> None:
        """Restore state from persisted data."""
        self._is_restoring_state = True
        try:
            for tab_state in state.open_tabs:
                try:
                    await self.create_tab(
                        tab_state.conversation_id,
                        tab_state.tab_id,
                        {"activate": False, "draft_model": tab_state.draft_model},
                    )
                except Exception:
                    continue
        finally:
            self._is_restoring_state = False

        # Switch to previously active tab
        if state.active_tab_id and state.active_tab_id in self._tabs:
            await self.switch_to_tab(state.active_tab_id)
        elif self._tabs:
            await self.switch_to_tab(list(self._tabs.keys())[0])

        # Create default tab if none restored
        if not self._tabs:
            await self.create_tab()

    # ============================================
    # Broadcast
    # ============================================

    async def broadcast_to_all_tabs(self, fn: Callable) -> None:
        """Broadcast a function call to all tab runtimes."""
        import asyncio
        tasks = []
        for tab in self._tabs.values():
            if tab.service and tab.service_initialized:
                tasks.append(fn(tab.service))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ============================================
    # Cleanup
    # ============================================

    async def destroy(self) -> None:
        """Destroy all tabs and clean up."""
        import asyncio

        # Save all conversations
        save_tasks = []
        for tab in self._tabs.values():
            conv_controller = tab.controllers.get("conversation_controller")
            if conv_controller:
                save_tasks.append(conv_controller.save())
        if save_tasks:
            await asyncio.gather(*save_tasks, return_exceptions=True)

        # Destroy all tabs
        destroy_tasks = [destroy_tab(tab) for tab in self._tabs.values()]
        if destroy_tasks:
            await asyncio.gather(*destroy_tasks, return_exceptions=True)

        self._tabs.clear()
        self._active_tab_id = None
