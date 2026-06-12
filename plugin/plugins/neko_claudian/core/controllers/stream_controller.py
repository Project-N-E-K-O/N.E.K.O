# Ported from claudian/src/features/chat/controllers/StreamController.ts
# Original author: Claudian contributors
# License: MIT

"""
StreamController — Manages stream chunk handling and rendering.

Handles text blocks, thinking blocks, tool calls, and subagent rendering.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class ThinkingBlockState:
    """State for a thinking block."""
    content: str = ""
    content_el: Optional[Any] = None
    is_visible: bool = False
    start_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "isVisible": self.is_visible,
        }


@dataclass
class PendingToolCall:
    """A pending tool call awaiting rendering."""
    tool_call: Dict[str, Any] = field(default_factory=dict)
    parent_el: Optional[Any] = None


@dataclass
class StreamControllerDeps:
    """Dependencies for StreamController."""
    state: Any  # ChatState instance
    renderer: Any  # MessageRenderer instance
    subagent_manager: Any  # SubagentManager instance
    get_messages_el: Callable[[], Any]
    get_agent_service: Callable[[], Optional[Any]]
    update_queue_indicator: Callable[[], None]


class StreamController:
    """Manages stream chunk handling and rendering.

    Ported from claudian/src/features/chat/controllers/StreamController.ts
    """

    THINKING_INDICATOR_DELAY = 0.4  # seconds

    def __init__(self, deps: StreamControllerDeps):
        self._deps = deps
        self._pending_text_render: Optional[Any] = None
        self._pending_thinking_render: Optional[Any] = None
        self._pending_tool_output_frames: Dict[str, Any] = {}
        self._lifecycle_subagent_states: Dict[str, Any] = {}
        self._lifecycle_agent_id_to_spawn_id: Dict[str, str] = {}

    @property
    def state(self) -> Any:
        return self._deps.state

    @property
    def renderer(self) -> Any:
        return self._deps.renderer

    # ============================================
    # Stream Chunk Handling
    # ============================================

    async def handle_stream_chunk(self, chunk: Dict[str, Any], msg: Dict[str, Any]) -> None:
        """Handle a stream chunk from Claude.

        This is the main entry point for processing stream events.
        """
        chunk_type = chunk.get("type", "")
        state = self.state

        if chunk_type == "thinking":
            await self._append_thinking(chunk.get("content", ""))

        elif chunk_type == "text":
            if state.current_thinking_state:
                await self.finalize_current_thinking_block(msg)
            msg["content"] = msg.get("content", "") + chunk.get("content", "")
            await self.append_text(chunk.get("content", ""))

        elif chunk_type == "tool_use":
            if state.current_thinking_state:
                await self.finalize_current_thinking_block(msg)
            await self.finalize_current_text_block(msg)
            self._handle_tool_use(chunk, msg)

        elif chunk_type == "tool_result":
            await self._handle_tool_result(chunk, msg)

        elif chunk_type == "tool_output":
            self._handle_tool_output(chunk, msg)

        elif chunk_type == "error":
            await self.append_text(f"\n\n❌ **Error:** {chunk.get('content', '')}")

        elif chunk_type == "notice":
            level = chunk.get("level", "info")
            prefix = "Blocked" if level == "warning" else "Notice"
            await self.append_text(f"\n\n⚠️ **{prefix}:** {chunk.get('content', '')}")

        elif chunk_type == "usage":
            if not state.ignore_usage_updates:
                state.usage = chunk.get("usage")

        elif chunk_type == "done":
            self._flush_pending_tools()

        elif chunk_type == "context_compacted":
            self._flush_pending_tools()
            if state.current_thinking_state:
                await self.finalize_current_thinking_block(msg)
            await self.finalize_current_text_block(msg)
            self._render_compact_boundary()

        elif chunk_type == "subagent_tool_use":
            self._handle_subagent_tool_use(chunk, msg)

        elif chunk_type == "subagent_tool_result":
            self._handle_subagent_tool_result(chunk, msg)

        elif chunk_type == "async_subagent_result":
            await self._handle_async_subagent_result(chunk)

        else:
            logger.debug(f"Unhandled chunk type: {chunk_type}")

    # ============================================
    # Tool Use Handling
    # ============================================

    def _handle_tool_use(self, chunk: Dict[str, Any], msg: Dict[str, Any]) -> None:
        """Handle a tool_use chunk."""
        state = self.state
        tool_id = chunk.get("id", "")
        tool_name = chunk.get("name", "")
        tool_input = chunk.get("input", {})

        # Check if this is an update to an existing tool call
        existing = next(
            (tc for tc in msg.get("tool_calls", []) if tc.get("id") == tool_id),
            None
        )
        if existing:
            # Update existing tool call
            if tool_input:
                existing["input"] = {**existing.get("input", {}), **tool_input}
            return

        # Create new tool call
        tool_call = {
            "id": tool_id,
            "name": tool_name,
            "input": tool_input,
            "status": "running",
            "is_expanded": False,
        }
        msg.setdefault("tool_calls", []).append(tool_call)

        # Add to content blocks
        msg.setdefault("content_blocks", []).append({
            "type": "tool_use",
            "toolId": tool_id,
        })

        # Buffer for rendering
        if state.current_content_el:
            state.pending_tools[tool_id] = PendingToolCall(
                tool_call=tool_call,
                parent_el=state.current_content_el,
            )

    def _handle_tool_result(self, chunk: Dict[str, Any], msg: Dict[str, Any]) -> None:
        """Handle a tool_result chunk."""
        state = self.state
        tool_id = chunk.get("id", "")
        content = chunk.get("content", "")
        is_error = chunk.get("isError", False)

        # Find existing tool call
        tool_call = next(
            (tc for tc in msg.get("tool_calls", []) if tc.get("id") == tool_id),
            None
        )

        if tool_call:
            tool_call["status"] = "error" if is_error else "completed"
            tool_call["result"] = content

            # Update DOM if element exists
            tool_el = state.tool_call_elements.get(tool_id)
            if tool_el:
                self._update_tool_call_result(tool_id, tool_call)

        # Remove from pending if present
        state.pending_tools.pop(tool_id, None)

    def _handle_tool_output(self, chunk: Dict[str, Any], msg: Dict[str, Any]) -> None:
        """Handle a tool_output chunk (streaming tool output)."""
        tool_id = chunk.get("id", "")
        content = chunk.get("content", "")

        # Find existing tool call
        tool_call = next(
            (tc for tc in msg.get("tool_calls", []) if tc.get("id") == tool_id),
            None
        )

        if tool_call:
            tool_call["result"] = tool_call.get("result", "") + content

    def _flush_pending_tools(self) -> None:
        """Flush all pending tool calls by rendering them."""
        state = self.state

        for tool_id, pending in list(state.pending_tools.items()):
            self._render_pending_tool(tool_id, pending)

        state.pending_tools.clear()

    def _render_pending_tool(self, tool_id: str, pending: PendingToolCall) -> None:
        """Render a pending tool call."""
        # In the full version, this would create DOM elements
        # For now, just mark as rendered
        state = self.state
        state.tool_call_elements[tool_id] = True

    def _update_tool_call_result(self, tool_id: str, tool_call: Dict[str, Any]) -> None:
        """Update the tool call result in the DOM."""
        # In the full version, this would update DOM elements
        pass

    # ============================================
    # Subagent Handling
    # ============================================

    def _handle_subagent_tool_use(self, chunk: Dict[str, Any], msg: Dict[str, Any]) -> None:
        """Handle a subagent_tool_use chunk."""
        # Track in tool calls for data completeness
        tool_call = {
            "id": chunk.get("id", ""),
            "name": chunk.get("name", ""),
            "input": chunk.get("input", {}),
            "status": "running",
            "is_expanded": False,
        }
        msg.setdefault("tool_calls", []).append(tool_call)

    def _handle_subagent_tool_result(self, chunk: Dict[str, Any], msg: Dict[str, Any]) -> None:
        """Handle a subagent_tool_result chunk."""
        tool_id = chunk.get("id", "")
        content = chunk.get("content", "")
        is_error = chunk.get("isError", False)

        # Find existing tool call
        tool_call = next(
            (tc for tc in msg.get("tool_calls", []) if tc.get("id") == tool_id),
            None
        )

        if tool_call:
            tool_call["status"] = "error" if is_error else "completed"
            tool_call["result"] = content

    async def _handle_async_subagent_result(self, chunk: Dict[str, Any]) -> None:
        """Handle an async_subagent_result chunk."""
        agent_id = chunk.get("agentId", "")
        status = chunk.get("status", "")
        result = chunk.get("result")

        logger.info(f"Async subagent result: {agent_id} - {status}")

    # ============================================
    # Text Block Management
    # ============================================

    async def append_text(self, text: str) -> None:
        """Append text to the current text block."""
        state = self.state
        if not state.current_content_el:
            return

        self.hide_thinking_indicator()

        if not state.current_text_el:
            state.current_text_content = ""

        state.current_text_content += text

        # In full version, this would schedule DOM rendering
        # For now, we just accumulate the text

    async def finalize_current_text_block(self, msg: Optional[Dict[str, Any]] = None) -> None:
        """Finalize the current text block."""
        state = self.state

        if msg and state.current_text_content:
            msg.setdefault("content_blocks", []).append({
                "type": "text",
                "content": state.current_text_content,
            })

        state.current_text_el = None
        state.current_text_content = ""

    # ============================================
    # Thinking Block Management
    # ============================================

    async def _append_thinking(self, content: str) -> None:
        """Append thinking content."""
        state = self.state
        if not state.current_content_el:
            return

        self.hide_thinking_indicator()

        if not state.current_thinking_state:
            state.current_thinking_state = ThinkingBlockState(
                start_time=time.time()
            )

        state.current_thinking_state.content += content

    async def finalize_current_thinking_block(self, msg: Optional[Dict[str, Any]] = None) -> None:
        """Finalize the current thinking block."""
        state = self.state
        if not state.current_thinking_state:
            return

        thinking_state = state.current_thinking_state
        duration = time.time() - thinking_state.start_time if thinking_state.start_time else 0

        if msg and thinking_state.content:
            msg.setdefault("content_blocks", []).append({
                "type": "thinking",
                "content": thinking_state.content,
                "durationSeconds": duration,
            })

        state.current_thinking_state = None

    # ============================================
    # Thinking Indicator
    # ============================================

    def show_thinking_indicator(self, override_text: Optional[str] = None) -> None:
        """Show the thinking indicator."""
        state = self.state
        if not state.current_content_el:
            return

        # Don't show while thinking block is active
        if state.current_thinking_state:
            return

        # In full version, this would create/update DOM element
        state.thinking_el = True  # Placeholder

    def hide_thinking_indicator(self) -> None:
        """Hide the thinking indicator."""
        state = self.state
        state.thinking_el = None

    # ============================================
    # Compact Boundary
    # ============================================

    def _render_compact_boundary(self) -> None:
        """Render a compact boundary marker."""
        state = self.state
        if not state.current_content_el:
            return

        self.hide_thinking_indicator()
        # In full version, this would create DOM element
        logger.info("Context compacted")

    # ============================================
    # Reset
    # ============================================

    def reset_streaming_state(self) -> None:
        """Reset all streaming state."""
        state = self.state
        self.hide_thinking_indicator()
        state.current_content_el = None
        state.current_text_el = None
        state.current_text_content = ""
        state.current_thinking_state = None
        state.pending_tools.clear()
        state.response_start_time = None
