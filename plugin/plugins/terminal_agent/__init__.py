"""
Terminal Agent Plugin - 终端风格的 Agent 启动器

功能：
1. 提供终端风格的 WebUI 界面
2. 支持输入命令启动不同的 Agent（claude, copilot 等）
3. 将终端输出发送给猫娘，让猫娘可以获取终端信息
4. 支持命令历史记录
"""

import asyncio
import time
from typing import Any, Dict, List

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    ui,
    tr,
    Ok,
    Err,
    SdkError,
)


class TerminalState:
    def __init__(self):
        self.history: List[Dict[str, str]] = []
        self.current_agent: str = ""
        self.is_running: bool = False


@neko_plugin
class TerminalAgentPlugin(NekoPluginBase):
    """终端 Agent 插件"""

    MAX_HISTORY = 50

    def __init__(self, ctx):
        super().__init__(ctx)
        self._state = TerminalState()
        self._history_lock = asyncio.Lock()

    async def _load_history(self):
        try:
            result = await self.store.get("terminal_history", [])
            if hasattr(result, "value"):
                history = result.value
            else:
                history = result
            if isinstance(history, list):
                self._state.history = history[-self.MAX_HISTORY:]
        except Exception as e:
            self.ctx.logger.debug(f"Failed to load history: {e}")

    async def _save_history(self):
        try:
            await self.store.set("terminal_history", self._state.history[-self.MAX_HISTORY:])
        except Exception as e:
            self.ctx.logger.debug(f"Failed to save history: {e}")

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        await self._load_history()
        self.ctx.logger.info("TerminalAgentPlugin started")
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        await self._save_history()
        self.ctx.logger.info("TerminalAgentPlugin stopped")
        return Ok({"status": "stopped"})

    @ui.context(id="terminal")
    async def get_terminal_context(self) -> Dict[str, Any]:
        return {
            "history": self._state.history,
            "current_agent": self._state.current_agent,
            "is_running": self._state.is_running,
        }

    @ui.action(
        label=tr("actions.execute.label", default="Execute"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="execute_command",
        name=tr("entries.execute.name", default="Execute Command"),
        description=tr("entries.execute.description", default="Execute a terminal command"),
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": tr("fields.command", default="Command to execute"),
                }
            },
            "required": ["command"],
        },
    )
    async def execute_command(self, command: str, **_):
        if not command or not command.strip():
            return Err(SdkError("Command cannot be empty"))

        command = command.strip()

        # clear 在 append 之前短路，否则清空后又会落一条输出，终端永远清不空
        if command.lower() == "clear":
            async with self._history_lock:
                self._state.history = []
                await self._save_history()
            return Ok({"command": command, "response": "Terminal cleared"})

        async with self._history_lock:
            self._state.history.append({
                "type": "input",
                "content": command,
                "timestamp": time.time(),
            })
            self._state.history = self._state.history[-self.MAX_HISTORY:]

        response = await self._process_command(command)

        async with self._history_lock:
            self._state.history.append({
                "type": "output",
                "content": response,
                "timestamp": time.time(),
            })
            self._state.history = self._state.history[-self.MAX_HISTORY:]
            await self._save_history()

        self._send_to_catgirl(command, response)

        return Ok({"command": command, "response": response})

    async def _process_command(self, command: str) -> str:
        command_lower = command.lower()
        
        if command_lower == "claude":
            self._state.current_agent = "claude"
            self._state.is_running = True
            return "🚀 Starting Claude Agent...\nClaude is now ready to help you with tasks!"
        
        elif command_lower == "copilot":
            self._state.current_agent = "copilot"
            self._state.is_running = True
            return "🚀 Starting GitHub Copilot...\nCopilot is now ready to assist with coding!"
        
        elif command_lower == "stop":
            agent = self._state.current_agent or "agent"
            self._state.current_agent = ""
            self._state.is_running = False
            return f"⏹️ Stopped {agent}"
        
        elif command_lower == "status":
            if self._state.is_running and self._state.current_agent:
                return f"✅ {self._state.current_agent} is running"
            return "❌ No agent is running"
        
        elif command_lower == "help":
            return """
Available commands:
  claude      - Start Claude Agent
  copilot     - Start GitHub Copilot
  stop        - Stop current agent
  status      - Check agent status
  help        - Show this help message
  clear       - Clear terminal history
            """.strip()
        
        else:
            return f"Unknown command: {command}\nType 'help' for available commands"

    def _send_to_catgirl(self, command: str, response: str):
        """将终端信息发送给猫娘"""
        message = f"终端命令执行：\n> {command}\n{response}"
        
        self.push_message(
            visibility=[],
            ai_behavior="read",
            parts=[
                {
                    "type": "text",
                    "text": message,
                }
            ],
            source="terminal_agent",
            priority=3,
        )

    @ui.action(
        label=tr("actions.clear.label", default="Clear"),
        refresh_context=True,
    )
    @plugin_entry(
        id="clear_terminal",
        name=tr("entries.clear.name", default="Clear Terminal"),
        description=tr("entries.clear.description", default="Clear terminal history"),
    )
    async def clear_terminal(self, **_):
        async with self._history_lock:
            self._state.history = []
            await self._save_history()
        return Ok({"status": "cleared"})