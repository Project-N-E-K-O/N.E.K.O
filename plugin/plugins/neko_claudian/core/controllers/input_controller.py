# Ported from claudian/src/features/chat/controllers/InputController.ts
# Original author: Claudian contributors
# License: MIT

"""
InputController — Manages user input, message sending, and approval dialogs.

Handles message sending, queue management, built-in commands, and approval prompts.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class QueuedMessage:
    """A message queued for sending while streaming."""
    content: str = ""
    images: List[Dict[str, Any]] = field(default_factory=list)
    turn_request: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "images": self.images,
            "turnRequest": self.turn_request,
        }


@dataclass
class ChatTurnRequest:
    """Request for a chat turn."""
    text: str = ""
    images: Optional[List[Dict[str, Any]]] = None
    current_note_path: Optional[str] = None
    editor_selection: Optional[Dict[str, Any]] = None
    browser_selection: Optional[Dict[str, Any]] = None
    canvas_selection: Optional[Dict[str, Any]] = None
    external_context_paths: Optional[List[str]] = None
    enabled_mcp_servers: Optional[Set[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"text": self.text}
        if self.images:
            out["images"] = self.images
        if self.current_note_path:
            out["currentNotePath"] = self.current_note_path
        if self.editor_selection:
            out["editorSelection"] = self.editor_selection
        if self.browser_selection:
            out["browserSelection"] = self.browser_selection
        if self.canvas_selection:
            out["canvasSelection"] = self.canvas_selection
        if self.external_context_paths:
            out["externalContextPaths"] = self.external_context_paths
        if self.enabled_mcp_servers:
            out["enabledMcpServers"] = list(self.enabled_mcp_servers)
        return out


@dataclass
class InputControllerDeps:
    """Dependencies for InputController."""
    state: Any  # ChatState instance
    renderer: Any  # MessageRenderer instance
    stream_controller: Any  # StreamController instance
    conversation_controller: Any  # ConversationController instance
    get_input_value: Callable[[], str]
    set_input_value: Callable[[str], None]
    get_messages_el: Callable[[], Any]
    generate_id: Callable[[], str]
    get_agent_service: Callable[[], Any]
    get_subagent_manager: Callable[[], Any]
    get_file_context_manager: Callable[[], Optional[Any]]
    get_image_context_manager: Callable[[], Optional[Any]]
    get_mcp_server_selector: Callable[[], Optional[Any]]
    get_external_context_selector: Callable[[], Optional[Any]]
    get_status_panel: Callable[[], Optional[Any]]
    on_fork_all: Optional[Callable[[], Awaitable[None]]] = None


class InputController:
    """Manages user input and message sending.

    Ported from claudian/src/features/chat/controllers/InputController.ts
    """

    def __init__(self, deps: InputControllerDeps):
        self._deps = deps
        self._pending_approval_inline: Optional[Any] = None
        self._pending_ask_inline: Optional[Any] = None
        self._pending_exit_plan_mode_inline: Optional[Any] = None
        self._pending_plan_approval: Optional[Any] = None
        self._active_resume_dropdown: Optional[Any] = None
        self._input_container_hide_depth: int = 0
        self._steer_in_flight: bool = False
        self._pending_steer_message: Optional[QueuedMessage] = None
        self._active_streaming_assistant_message: Optional[Any] = None

    @property
    def state(self) -> Any:
        return self._deps.state

    @property
    def renderer(self) -> Any:
        return self._deps.renderer

    @property
    def stream_controller(self) -> Any:
        return self._deps.stream_controller

    # ============================================
    # Message Sending
    # ============================================

    async def send_message(self, options: Optional[Dict[str, Any]] = None) -> None:
        """Send a message to Claude.

        This is the main entry point for sending messages.
        """
        state = self.state

        # Don't send during conversation creation/switching
        if state.is_creating_conversation or state.is_switching_conversation:
            return

        content = options.get("content") if options else None
        if content is None:
            content = self._deps.get_input_value().strip()

        images = options.get("images") if options else None
        has_images = images and len(images) > 0

        if not content and not has_images:
            return

        # Check for built-in commands
        if content.startswith("/"):
            await self._execute_built_in_command(content)
            return

        # If streaming, queue the message
        if state.is_streaming:
            queued = QueuedMessage(content=content, images=images or [])
            state.queued_message = self._merge_queued_messages(state.queued_message, queued)
            self._deps.set_input_value("")
            self.update_queue_indicator()
            return

        # Clear input and set streaming state
        self._deps.set_input_value("")
        state.is_streaming = True
        state.cancel_requested = False
        state.ignore_usage_updates = False
        state.bump_stream_generation()

        # Build turn request
        turn_request = self._build_turn_request(content, images)

        # Create user message
        user_msg = {
            "id": self._deps.generate_id(),
            "role": "user",
            "content": content,
            "timestamp": time.time(),
            "images": images,
        }
        state.add_message(user_msg)

        # Create assistant message placeholder
        assistant_msg = {
            "id": self._deps.generate_id(),
            "role": "assistant",
            "content": "",
            "timestamp": time.time(),
            "tool_calls": [],
        }
        state.add_message(assistant_msg)
        self._active_streaming_assistant_message = assistant_msg

        # Get agent service and execute query
        agent_service = self._deps.get_agent_service()
        if not agent_service:
            logger.error("Agent service not available")
            state.is_streaming = False
            return

        try:
            # Execute query
            async for chunk in agent_service.query(turn_request):
                if state.cancel_requested:
                    break

                await self.stream_controller.handle_stream_chunk(chunk, assistant_msg)

        except Exception as e:
            logger.error(f"Error during query: {e}")
            await self.stream_controller.append_text(f"\n\n**Error:** {str(e)}")
        finally:
            state.is_streaming = False
            state.cancel_requested = False
            self._active_streaming_assistant_message = None
            self.stream_controller.hide_thinking_indicator()

            # Process queued message
            if state.queued_message:
                self._process_queued_message()

    def _build_turn_request(self, content: str, images: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Build a turn request from content and context."""
        file_context = self._deps.get_file_context_manager()
        mcp_selector = self._deps.get_mcp_server_selector()
        external_context = self._deps.get_external_context_selector()

        request: Dict[str, Any] = {"text": content}

        if images:
            request["images"] = images

        # Add external context paths
        if external_context:
            paths = external_context.get_external_contexts()
            if paths:
                request["externalContextPaths"] = paths

        # Add enabled MCP servers
        if mcp_selector:
            servers = mcp_selector.get_enabled_servers()
            if servers:
                request["enabledMcpServers"] = list(servers)

        return request

    # ============================================
    # Queue Management
    # ============================================

    def update_queue_indicator(self) -> None:
        """Update the queue indicator UI."""
        state = self.state
        if not state.queued_message:
            return

        # This would update DOM in the frontend version
        logger.debug(f"Queue indicator: {state.queued_message.content[:40]}...")

    def clear_queued_message(self) -> None:
        """Clear the queued message."""
        self.state.queued_message = None
        self.update_queue_indicator()

    def _process_queued_message(self) -> None:
        """Process the queued message after streaming completes."""
        state = self.state
        if not state.queued_message:
            return

        queued = state.queued_message
        state.queued_message = None
        self.update_queue_indicator()

        # Send the queued message
        import asyncio
        asyncio.create_task(self.send_message({
            "content": queued.content,
            "images": queued.images,
        }))

    def _merge_queued_messages(
        self,
        existing: Optional[QueuedMessage],
        incoming: QueuedMessage
    ) -> QueuedMessage:
        """Merge two queued messages."""
        if not existing:
            return QueuedMessage(
                content=incoming.content,
                images=list(incoming.images),
                turn_request=incoming.turn_request,
            )

        # Merge content
        merged_content = f"{existing.content}\n{incoming.content}" if existing.content else incoming.content
        merged_images = list(existing.images) + list(incoming.images)

        return QueuedMessage(content=merged_content, images=merged_images)

    # ============================================
    # Streaming Control
    # ============================================

    def cancel_streaming(self) -> None:
        """Cancel the current streaming operation."""
        state = self.state
        if not state.is_streaming:
            return

        state.cancel_requested = True
        agent_service = self._deps.get_agent_service()
        if agent_service:
            agent_service.cancel()
        self.stream_controller.hide_thinking_indicator()

    # ============================================
    # Built-in Commands
    # ============================================

    async def _execute_built_in_command(self, content: str) -> None:
        """Execute a built-in slash command."""
        parts = content.strip().split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if command == "/clear":
            await self._deps.conversation_controller.create_new()
        elif command == "/new":
            await self._deps.conversation_controller.create_new()
        elif command == "/add-dir":
            external_context = self._deps.get_external_context_selector()
            if external_context:
                result = external_context.add_external_context(args)
                if result.get("success"):
                    logger.info(f"Added external context: {result.get('normalizedPath')}")
                else:
                    logger.warning(result.get("error", "Failed to add external context"))
        elif command == "/resume":
            self._show_resume_dropdown()
        elif command == "/fork":
            if self._deps.on_fork_all:
                await self._deps.on_fork_all()
        else:
            logger.warning(f"Unknown command: {command}")

    def _show_resume_dropdown(self) -> None:
        """Show the resume session dropdown."""
        # This would show a dropdown in the frontend version
        logger.info("Resume dropdown not yet implemented in backend")

    # ============================================
    # Approval Dialogs
    # ============================================

    async def handle_approval_request(
        self,
        tool_name: str,
        input_data: Dict[str, Any],
        description: str,
        options: Optional[Dict[str, Any]] = None
    ) -> str:
        """Handle an approval request for a tool.

        Returns: "allow", "allow-always", "deny", or "cancel"
        """
        # In backend, we'll use a callback or auto-approve
        logger.info(f"Approval request: {tool_name} - {description}")

        # For now, return "allow" - in production this would show UI
        return "allow"

    async def handle_ask_user_question(
        self,
        input_data: Dict[str, Any],
        signal: Optional[Any] = None
    ) -> Optional[Dict[str, Any]]:
        """Handle an AskUserQuestion tool.

        Returns: answers dict or None if cancelled
        """
        logger.info(f"Ask user question: {input_data}")
        # In backend, we'll return None (no answer)
        return None

    async def handle_exit_plan_mode(
        self,
        input_data: Dict[str, Any],
        signal: Optional[Any] = None
    ) -> Optional[Dict[str, Any]]:
        """Handle ExitPlanMode tool.

        Returns: decision dict or None if cancelled
        """
        logger.info(f"Exit plan mode: {input_data}")
        # In backend, auto-approve
        return {"type": "approve"}

    def dismiss_pending_approval(self) -> None:
        """Dismiss any pending approval prompt."""
        self._pending_approval_inline = None
        self._pending_ask_inline = None
        self._pending_exit_plan_mode_inline = None
        self._pending_plan_approval = None

    # ============================================
    # Instruction Mode
    # ============================================

    async def handle_instruction_submit(self, raw_instruction: str) -> None:
        """Handle instruction mode submission."""
        logger.info(f"Instruction submit: {raw_instruction}")
        # This would trigger instruction refinement in the full version
