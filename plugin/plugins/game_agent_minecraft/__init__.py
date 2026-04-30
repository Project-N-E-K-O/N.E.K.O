"""Minecraft Game Agent plugin.

Bridges a local Minecraft agent server (WebSocket) to the LLM via the
plugin SDK's ``@llm_tool`` decorator. Replaces the previous in-tree
integration that lived in ``main_logic/core.py`` and ``main_logic/
game_agent_client.py`` (commit ``bca0c5f3`` on the abandoned
``feat/game-agent-integration`` branch). Now everything game-agent-
specific is contained inside this directory and can be installed /
removed without patching core code.

Architecture (one paragraph): ``minecraft_task`` is registered as an
LLM tool via :func:`plugin.sdk.plugin.llm_tool`; when the model picks
it, the plugin sends the task text to the agent server over WebSocket
and blocks the handler on an :class:`asyncio.Event` until the
``task_finished`` frame arrives (or a configurable timeout fires).
Screenshots streamed by the agent server are forwarded into the
realtime LLM session via :class:`push_message v2 <push_message>` with
``ai_behavior="read"`` so they enter the model's vision context. A
background task periodically fires a "GAME_SYSTEM" nudge prompt with
the latest log digest and screenshot cache so the model keeps playing
autonomously when the user is silent.
"""
from __future__ import annotations

from typing import Any, Dict

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

from .service import GameAgentService

# JSON Schema reused by the @llm_tool decorator below. Pulled into a
# module-level constant so the plugin's introspection (status entry,
# tests) can reference exactly what the LLM sees.
MINECRAFT_TASK_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": "A concrete, directly executable Minecraft goal in English.",
        },
        "overwrite": {
            "type": "boolean",
            "description": (
                "If True, interrupt the currently running task and start this one. "
                "Default False — sending a new task while one is in flight returns "
                "a 'busy' response without disturbing the in-flight task."
            ),
        },
    },
    "required": ["task"],
}


MINECRAFT_TASK_DESCRIPTION = (
    "Send a task to the Minecraft game system. Use this when you describe a "
    "concrete action you want to perform in Minecraft. The task should be a "
    "single, clear, executable goal in English. Do not propose vague requests "
    "such as: find a good place to build a house. Such vague tasks cannot be "
    "executed. IMPORTANT: Do NOT set overwrite=True unless absolutely necessary."
)


@neko_plugin
class GameAgentMinecraftPlugin(NekoPluginBase):
    """Plugin facade — minimal: lifecycle wiring + tool surface.

    Real logic lives in :class:`GameAgentService`; this class only
    handles the SDK integration boilerplate.
    """

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: Dict[str, Any] = {}
        self._service = GameAgentService(
            logger=self.logger,
            push_message_fn=self.push_message,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = (
            cfg.get("game_agent", {})
            if isinstance(cfg.get("game_agent"), dict)
            else {}
        )
        self._service.configure(self._cfg)
        try:
            await self._service.start()
        except Exception as exc:
            # Plugin construction shouldn't be fatal if the agent server
            # is offline — the WS client has its own reconnect loop, and
            # we want the LLM tool registered regardless so the model
            # can attempt a task and get a clean disconnected error.
            self.logger.warning(
                "[startup] service start raised; continuing — {}: {}",
                type(exc).__name__, exc,
            )
        return Ok({"status": "ready", "result": self._service.get_status()})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        await self._service.stop()
        return Ok({"status": "shutdown"})

    # ------------------------------------------------------------------
    # LLM-callable tool
    # ------------------------------------------------------------------

    @llm_tool(
        name="minecraft_task",
        description=MINECRAFT_TASK_DESCRIPTION,
        parameters=MINECRAFT_TASK_SCHEMA,
        # Set the SDK wrapper's timeout to the maximum the server
        # accepts (300s, see ``ToolRegisterRequest.timeout_seconds``)
        # so the *real* per-call cap is governed by the operator-
        # configured ``[game_agent].task_timeout_seconds``. With a
        # hardcoded 30s here, any user-set ``task_timeout_seconds`` >
        # 30 would be silently truncated by the SDK's outer wrapper
        # before the service could return its structured
        # ``{status: "timeout"}`` shape.
        timeout=300.0,
    )
    async def minecraft_task(self, *, task: str, overwrite: bool = False, **_):
        return await self._service.execute_minecraft_task(
            task=task, overwrite=overwrite,
        )

    # ------------------------------------------------------------------
    # Diagnostic plugin entries (callable from the plugin UI / CLI)
    # ------------------------------------------------------------------

    @plugin_entry(
        id="game_agent_status",
        name="查询游戏代理状态",
        description="查询 Minecraft Agent 连接状态、当前任务和缓存大小。",
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def game_agent_status(self, **_):
        try:
            status = self._service.get_status()
            connected = "connected" if status.get("connected") else "disconnected"
            pending = status.get("pending_task") or "(idle)"
            status["summary"] = (
                f"ws={status.get('ws_url')} | {connected} | task={pending}"
            )
            return Ok(status)
        except Exception as exc:
            return Err(f"{type(exc).__name__}: {exc}")

    @plugin_entry(
        id="game_agent_reload_config",
        name="重载游戏代理配置",
        description=(
            "重新读取 plugin.toml [game_agent] 配置；纯数据项 (timeouts、"
            "intervals、screenshot 开关) 直接生效，ws_url 或重连间隔变更则会"
            "触发 WebSocket 客户端 stop+start 切换到新地址。"
        ),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
    )
    async def game_agent_reload_config(self, **_):
        try:
            cfg = await self.config.dump(timeout=5.0)
            cfg = cfg if isinstance(cfg, dict) else {}
            self._cfg = (
                cfg.get("game_agent", {})
                if isinstance(cfg.get("game_agent"), dict)
                else {}
            )
            transport_restarted = await self._service.reload_config_live(self._cfg)
            summary = (
                "config reloaded with transport restart"
                if transport_restarted
                else "config reloaded (live)"
            )
            return Ok({
                "summary": summary,
                "transport_restarted": transport_restarted,
                "result": self._service.get_status(),
            })
        except Exception as exc:
            raise SdkError(f"reload failed: {type(exc).__name__}: {exc}")
