"""
简化版 Claude Runtime — 直接调用 Claude CLI

这是一个能真正工作的实现，使用 subprocess 调用 claude 命令。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def find_claude_cli() -> Optional[str]:
    """Find Claude CLI executable."""
    # Try 'claude' first
    path = shutil.which("claude")
    if path:
        return path

    # On Windows, try 'claude.cmd'
    if sys.platform == "win32":
        path = shutil.which("claude.cmd")
        if path:
            return path

    return None


@dataclass
class SimpleClaudeRuntime:
    """Simplified Claude runtime that calls claude CLI directly.

    This is a working implementation that uses subprocess to call claude.
    """

    cli_path: Optional[str] = None
    workspace_path: str = ""
    model: str = "claude-sonnet-4-20250514"
    _process: Optional[asyncio.subprocess.Process] = None
    _is_ready: bool = False
    _abort_requested: bool = False

    def __post_init__(self):
        if not self.cli_path:
            self.cli_path = find_claude_cli()
        if not self.workspace_path:
            self.workspace_path = os.getcwd()

    async def initialize(self) -> bool:
        """Initialize the runtime."""
        if not self.cli_path:
            logger.error("Claude CLI not found")
            return False

        self._is_ready = True
        logger.info(f"Claude runtime initialized: {self.cli_path}")
        return True

    def is_ready(self) -> bool:
        """Check if runtime is ready."""
        return self._is_ready

    async def query(
        self,
        prompt: str,
        images: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Execute a query and yield stream chunks.

        This uses `claude -p "prompt" --output-format stream-json` for cold start.
        """
        if not self.cli_path:
            yield {"type": "error", "content": "Claude CLI not found"}
            return

        # Build command
        # 注意：--output-format stream-json 需要 --verbose 选项
        cmd = [
            self.cli_path,
            "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
        ]

        if self.model:
            cmd.extend(["--model", self.model])

        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        # Set environment
        env = os.environ.copy()
        env["CLAUDE_WORKING_DIR"] = self.workspace_path

        logger.info(f"Executing: {' '.join(cmd[:4])}...")

        try:
            # Start process
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=self.workspace_path,
            )

            self._abort_requested = False

            # Read output line by line
            async for line in self._process.stdout:
                if self._abort_requested:
                    self._process.terminate()
                    yield {"type": "error", "content": "Aborted by user"}
                    return

                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue

                try:
                    data = json.loads(line_str)
                    chunk = self._transform_chunk(data)
                    if chunk:
                        yield chunk
                except json.JSONDecodeError:
                    # Not JSON, might be plain text output
                    logger.debug(f"Non-JSON output: {line_str}")

            # Wait for process to complete
            await self._process.wait()

            # Yield done signal
            yield {"type": "done"}

        except asyncio.CancelledError:
            if self._process:
                self._process.terminate()
            yield {"type": "error", "content": "Cancelled"}
        except Exception as e:
            logger.error(f"Query error: {e}")
            yield {"type": "error", "content": str(e)}
        finally:
            self._process = None

    def _transform_chunk(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Transform Claude CLI output to StreamChunk format.

        Claude CLI 输出格式（--output-format stream-json --verbose）：
        - {"type": "system", "subtype": "init", ...} — 初始化
        - {"type": "system", "subtype": "thinking_tokens", ...} — thinking token 统计
        - {"type": "assistant", "message": {...}} — 助手消息
        - {"type": "result", "subtype": "success", "result": "..."} — 最终结果
        """
        msg_type = data.get("type")

        if msg_type == "system":
            subtype = data.get("subtype", "")
            if subtype == "init":
                # 会话初始化
                return {
                    "type": "session_info",
                    "session_id": data.get("session_id"),
                    "model": data.get("model"),
                }
            elif subtype == "thinking_tokens":
                # thinking token 统计，跳过
                return None
            elif subtype in ("hook_started", "hook_response"):
                # Hook 事件，跳过
                return None
            return None

        elif msg_type == "assistant":
            # 助手消息
            message = data.get("message", {})
            content = message.get("content", [])

            chunks = []
            for block in content:
                block_type = block.get("type")
                if block_type == "text":
                    text = block.get("text", "")
                    if text:
                        chunks.append({"type": "text", "content": text})
                elif block_type == "tool_use":
                    chunks.append({
                        "type": "tool_use",
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "input": block.get("input", {}),
                    })
                elif block_type == "thinking":
                    thinking = block.get("thinking", "")
                    if thinking:
                        chunks.append({"type": "thinking", "content": thinking})

            # 返回第一个 chunk（如果有多个，后续的会被忽略）
            return chunks[0] if chunks else None

        elif msg_type == "result":
            # 最终结果
            subtype = data.get("subtype", "")
            if subtype == "success":
                result_text = data.get("result", "")
                if result_text:
                    return {"type": "text", "content": result_text}
            return {"type": "done"}

        elif msg_type == "error":
            return {
                "type": "error",
                "content": data.get("error", data.get("message", "Unknown error")),
            }

        # 忽略其他类型
        return None

    def cancel(self) -> None:
        """Cancel current operation."""
        self._abort_requested = True
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass

    async def cleanup(self) -> None:
        """Clean up resources."""
        self.cancel()
        self._is_ready = False


async def create_simple_runtime(
    workspace_path: str = "",
    model: str = "claude-sonnet-4-20250514",
) -> SimpleClaudeRuntime:
    """Create and initialize a simple Claude runtime."""
    runtime = SimpleClaudeRuntime(
        workspace_path=workspace_path,
        model=model,
    )
    await runtime.initialize()
    return runtime
