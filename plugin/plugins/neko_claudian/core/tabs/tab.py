# Ported from claudian/src/features/chat/tabs/Tab.ts
# Original author: Claudian contributors
# License: MIT

"""
Tab — Individual tab implementation.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from .types import TabData, TabId

logger = logging.getLogger(__name__)


def create_tab(
    plugin: Any,
    container_el: Any,
    conversation: Optional[Dict[str, Any]] = None,
    tab_id: Optional[TabId] = None,
    draft_model: Optional[str] = None,
    default_provider_id: Optional[str] = None,
    on_streaming_changed: Optional[Callable[[bool], None]] = None,
    on_title_changed: Optional[Callable[[str], None]] = None,
    on_attention_changed: Optional[Callable[[bool], None]] = None,
    on_conversation_id_changed: Optional[Callable[[Optional[str]], None]] = None,
) -> TabData:
    """Create a new tab.

    Ported from claudian/src/features/chat/tabs/Tab.ts createTab.
    """
    from ..state.chat_state import ChatState

    tab_id = tab_id or f"tab-{int(time.time() * 1000)}"
    provider_id = default_provider_id or "claude"

    # Create chat state
    state = ChatState()

    tab = TabData(
        id=tab_id,
        conversation_id=conversation.get("id") if conversation else None,
        provider_id=provider_id,
        draft_model=draft_model,
        lifecycle_state="blank",
        state=state,
        service=None,
        service_initialized=False,
        controllers={},
        ui={},
    )

    logger.info(f"Created tab: {tab_id}")
    return tab


def activate_tab(tab: TabData) -> None:
    """Activate a tab.

    Ported from claudian/src/features/chat/tabs/Tab.ts activateTab.
    """
    tab.lifecycle_state = "active"
    logger.debug(f"Activated tab: {tab.id}")


def deactivate_tab(tab: TabData) -> None:
    """Deactivate a tab.

    Ported from claudian/src/features/chat/tabs/Tab.ts deactivateTab.
    """
    tab.lifecycle_state = "inactive"
    logger.debug(f"Deactivated tab: {tab.id}")


async def destroy_tab(tab: TabData) -> None:
    """Destroy a tab and clean up resources.

    Ported from claudian/src/features/chat/tabs/Tab.ts destroyTab.
    """
    # Clean up service
    if tab.service:
        tab.service.cleanup()
        tab.service = None

    # Clean up controllers
    tab.controllers.clear()
    tab.ui.clear()

    logger.info(f"Destroyed tab: {tab.id}")


def get_tab_title(tab: TabData, plugin: Any) -> str:
    """Get the display title for a tab.

    Ported from claudian/src/features/chat/tabs/Tab.ts getTabTitle.
    """
    if tab.conversation_id:
        conversation = plugin.get_conversation_sync(tab.conversation_id)
        if conversation:
            return conversation.get("title", "Untitled")

    if tab.draft_model:
        return f"Draft ({tab.draft_model})"

    return "New Chat"


def initialize_tab_ui(
    tab: TabData,
    plugin: Any,
    config: Optional[Dict[str, Any]] = None,
) -> None:
    """Initialize UI components for a tab.

    Ported from claudian/src/features/chat/tabs/Tab.ts initializeTabUI.
    """
    # This would initialize UI components in the full version
    tab.ui = {
        "initialized": True,
        "config": config,
    }


def initialize_tab_controllers(
    tab: TabData,
    plugin: Any,
    view: Any,
    on_fork: Optional[Callable] = None,
    on_open_conversation: Optional[Callable] = None,
    get_provider_catalog_config: Optional[Callable] = None,
) -> None:
    """Initialize controllers for a tab.

    Ported from claudian/src/features/chat/tabs/Tab.ts initializeTabControllers.
    """
    from ..controllers.conversation_controller import ConversationController, ConversationControllerDeps
    from ..controllers.input_controller import InputController, InputControllerDeps
    from ..controllers.stream_controller import StreamController, StreamControllerDeps

    # Create stream controller
    stream_deps = StreamControllerDeps(
        state=tab.state,
        renderer=None,  # Would be set in full version
        subagent_manager=None,  # Would be set in full version
        get_messages_el=lambda: None,
        get_agent_service=lambda: tab.service,
        update_queue_indicator=lambda: None,
    )
    stream_controller = StreamController(stream_deps)

    # Create conversation controller
    conv_deps = ConversationControllerDeps(
        state=tab.state,
        renderer=None,
        stream_controller=stream_controller,
        get_agent_service=lambda: tab.service,
        get_plugin=lambda: plugin,
        generate_id=lambda: f"conv-{int(time.time() * 1000)}",
    )
    conversation_controller = ConversationController(conv_deps)

    # Create input controller
    input_deps = InputControllerDeps(
        state=tab.state,
        renderer=None,
        stream_controller=stream_controller,
        conversation_controller=conversation_controller,
        get_input_value=lambda: "",
        set_input_value=lambda v: None,
        get_messages_el=lambda: None,
        generate_id=lambda: f"msg-{int(time.time() * 1000)}",
        get_agent_service=lambda: tab.service,
        get_subagent_manager=lambda: None,
        get_file_context_manager=lambda: None,
        get_image_context_manager=lambda: None,
        get_mcp_server_selector=lambda: None,
        get_external_context_selector=lambda: None,
        get_status_panel=lambda: None,
        on_fork_all=on_fork,
    )
    input_controller = InputController(input_deps)

    tab.controllers = {
        "stream_controller": stream_controller,
        "conversation_controller": conversation_controller,
        "input_controller": input_controller,
    }


def initialize_tab_service(
    tab: TabData,
    plugin: Any,
    conversation: Optional[Dict[str, Any]] = None,
) -> None:
    """Initialize the service (runtime) for a tab.

    Ported from claudian/src/features/chat/tabs/Tab.ts initializeTabService.
    """
    # This would create the actual runtime in the full version
    tab.service_initialized = True
    logger.info(f"Initialized service for tab: {tab.id}")


def setup_service_callbacks(tab: TabData, plugin: Any) -> None:
    """Setup callbacks for the tab service.

    Ported from claudian/src/features/chat/tabs/Tab.ts setupServiceCallbacks.
    """
    # This would set up callbacks in the full version
    pass


def wire_tab_input_events(tab: TabData, plugin: Any) -> None:
    """Wire input event handlers for a tab.

    Ported from claudian/src/features/chat/tabs/Tab.ts wireTabInputEvents.
    """
    # This would wire events in the full version
    pass
