"""
猫娘 Claudian (Neko Claudian) v1.0.0

完整移植 Obsidian Claudian 插件的所有 Claude Code 相关功能到 N.E.K.O 猫娘项目。

核心目标：
1. 猫娘插件前端 UI 可以调用 Claude Code 干活
2. 猫娘 LLM 可通过 plugin_entry / push_message 向前端 UI 输入框注入文本、
   静默发送消息给 Claude Code、点击发送按钮、读取 Claude 输出

本文件是插件主入口，定义：
- @neko_plugin 主类 NekoClaudianPlugin
- 异步 HTTP server (aiohttp, 端口 48930) 承载 SSE / API
- register_static_ui() 暴露主 Web UI
- 5 个猫娘专用 plugin_entry: neko_inject_text / neko_send_silently /
  neko_click_send / push_claude_reply / neko_observe_stream

References (1:1 ported from claudian):
- claudian/src/main.ts
- claudian/src/providers/claude/runtime/ClaudeChatRuntime.ts
- claudian/src/features/chat/ClaudianView.ts
- claudian/src/features/chat/controllers/InputController.ts
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    timer_interval,
    Ok,
    Err,
    SdkError,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_DEFAULT_HTTP_PORT = 48930
_UI_PATH = "/neko_claudian/ui"
_API_PREFIX = "/neko_claudian/api"

# Claudian 风格的消息类型（与 StreamChunk.type 对齐）
MSG_TYPE_USER = "user"
MSG_TYPE_ASSISTANT = "assistant"
MSG_TYPE_SYSTEM = "system"
MSG_TYPE_RESULT = "result"
MSG_TYPE_STREAM_EVENT = "stream_event"
MSG_TYPE_TOOL_USE = "tool_use"
MSG_TYPE_TOOL_RESULT = "tool_result"
MSG_TYPE_THINKING = "thinking"
MSG_TYPE_TEXT = "text"
MSG_TYPE_ERROR = "error"
MSG_TYPE_USAGE = "usage"
MSG_TYPE_CONTROL_REQUEST = "control_request"  # 权限请求 / 计划模式 / ask_user
MSG_TYPE_CONTROL_RESPONSE = "control_response"  # 权限决策
MSG_TYPE_REWIND = "rewind"
MSG_TYPE_SESSION_INFO = "session_info"

# Permission modes（与 Claudian 1:1 对齐）
PERMISSION_MODES = ("default", "acceptEdits", "bypassPermissions", "plan")

# 角色 LLM 注入专用事件类型
NEKO_EVENT_INJECT_TEXT = "neko_inject_text"
NEKO_EVENT_CLICK_SEND = "neko_click_send"
NEKO_EVENT_OBSERVE = "neko_observe"


# ---------------------------------------------------------------------------
# 工具：跨平台资源路径
# ---------------------------------------------------------------------------

def _plugin_root() -> Path:
    """插件根目录（plugin.toml 所在）"""
    return Path(__file__).resolve().parent


def _static_dir() -> Path:
    return _plugin_root() / "static"


def _data_dir() -> Path:
    return _plugin_root() / "data"


# ---------------------------------------------------------------------------
# 主插件类
# ---------------------------------------------------------------------------

@neko_plugin
class NekoClaudianPlugin(NekoPluginBase):
    """
    猫娘 Claudian 插件主类。

    关键能力（与 Obsidian Claudian 1:1 对齐）：
    - 启动后台 aiohttp HTTP server 端口 48930，承载 SSE 流 + REST API
    - 调用 register_static_ui("static") 暴露主 Web UI
      → http://127.0.0.1:48916/plugin/neko_claudian/ui/
    - 管理多 Tab (TabManager)、会话状态 (ChatState)
    - 启动 Claude CLI 持久子进程 (ClaudeChatRuntime)
    - 提供 5 个猫娘 LLM 注入入口

    状态机：
        init → startup → ready → (running) → shutdown → stopped
    """

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self._plugin_id = getattr(ctx, "plugin_id", "neko_claudian")
        self._http_port = _DEFAULT_HTTP_PORT
        self._http_server: Optional["_HttpServer"] = None

        # Claudian 1:1 核心组件（V3-V5 阶段陆续初始化）
        self._tab_manager: Any = None
        self._chat_state: Any = None
        self._input_controller: Any = None
        self._stream_controller: Any = None
        self._claude_runtime: Any = None
        self._claude_channel: Any = None
        self._session_manager: Any = None
        self._settings: Any = None
        self._storage: Any = None
        self._mcp_manager: Any = None
        self._agent_manager: Any = None
        self._skill_manager: Any = None
        self._slash_command_manager: Any = None
        self._approval_manager: Any = None

        # 工作目录
        self._workspace_path: Optional[Path] = None

        # 锁与关闭信号
        self._lock = threading.RLock()
        self._shutting_down = False

        # 猫娘 LLM 注入事件订阅者
        self._sse_subscribers: Dict[str, List[asyncio.Queue]] = {}

        self.logger.info(
            f"[neko_claudian] __init__ ok; root={_plugin_root()}, "
            f"http_port={self._http_port}, static_dir={_static_dir()}"
        )

    # -----------------------------------------------------------------------
    # 生命周期
    # -----------------------------------------------------------------------

    @lifecycle(id="startup")
    async def startup(self, **_):
        """
        启动流程（与 Claudian onload 对齐）：
        1. 初始化数据目录
        2. 初始化 Settings / Storage
        3. 初始化 TabManager / ChatState / Controllers
        4. 启动 ClaudeChatRuntime（持久查询）
        5. 启动 HTTP server (aiohttp)
        6. 注册静态 UI
        7. 报告就绪
        """
        try:
            self.logger.info("[neko_claudian] startup begin")

            # 1) 数据目录
            _data_dir().mkdir(parents=True, exist_ok=True)
            self._workspace_path = _data_dir() / "workspace"
            self._workspace_path.mkdir(parents=True, exist_ok=True)

            # 2) 加载配置（V9 阶段实现；V2 阶段先放占位 dict）
            self._settings = _load_initial_settings(self._workspace_path)

            # 3) Storage
            self._storage = _JsonStorage(self._data_dir() / "storage")

            # 4) 初始化 Claude Runtime
            try:
                from .core.providers.claude.runtime.simple_runtime import create_simple_runtime
                self._claude_runtime = await create_simple_runtime(
                    workspace_path=str(self._workspace_path),
                    model=self._settings.get("model", "claude-sonnet-4-20250514"),
                )
                self.logger.info("[neko_claudian] Claude runtime initialized")
            except Exception as e:
                self.logger.warning(f"[neko_claudian] Claude runtime init failed: {e}")
                self._claude_runtime = None

            # 5) 其他组件（后续阶段）
            self._tab_manager = None
            self._chat_state = None
            self._input_controller = None
            self._stream_controller = None
            self._claude_channel = None
            self._session_manager = None
            self._mcp_manager = None
            self._agent_manager = None
            self._skill_manager = None
            self._slash_command_manager = None
            self._approval_manager = None

            # 会话存储
            self._conversations: Dict[str, Dict[str, Any]] = {}
            self._current_conversation_id: Optional[str] = None
            self._messages: List[Dict[str, Any]] = []

            # 6) 启动 HTTP server
            self._http_server = _HttpServer(self)
            await self._http_server.start(self._http_port)

            # 7) 注册静态 UI
            if _static_dir().exists():
                self.register_static_ui("static")
            else:
                self.logger.warning(
                    f"[neko_claudian] static dir not found: {_static_dir()}"
                )

            self.logger.info(
                f"[neko_claudian] startup ok; ui_url="
                f"http://127.0.0.1:48916/plugin/{self._plugin_id}/ui/, "
                f"http_port={self._http_port}"
            )
            return Ok({
                "status": "ready",
                "ui_url": f"http://127.0.0.1:48916/plugin/{self._plugin_id}/ui/",
                "http_port": self._http_port,
                "version": "1.0.0",
            })
        except Exception as e:
            self.logger.exception(f"[neko_claudian] startup failed: {e}")
            return Err(SdkError(f"startup failed: {e}"))

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        """
        关闭流程（与 Claudian onunload 对齐）：
        1. 标记 shutting_down
        2. 关闭 HTTP server（断 SSE）
        3. 关闭 Claude 子进程
        4. 关闭 MCP servers
        5. 持久化 Settings / Storage
        """
        with self._lock:
            if self._shutting_down:
                return Ok({"status": "already_stopped"})
            self._shutting_down = True

        self.logger.info("[neko_claudian] shutdown begin")
        try:
            # 关闭 HTTP server
            if self._http_server is not None:
                await self._http_server.stop()
                self._http_server = None

            # 关闭 Claude runtime（V3 阶段实现 closePersistentQuery）
            if self._claude_runtime is not None and hasattr(self._claude_runtime, "close"):
                try:
                    await self._claude_runtime.close()
                except Exception as e:
                    self.logger.warning(f"[neko_claudian] close runtime err: {e}")
            self._claude_runtime = None
            self._claude_channel = None

            # 关闭 MCP
            if self._mcp_manager is not None and hasattr(self._mcp_manager, "close"):
                try:
                    await self._mcp_manager.close()
                except Exception as e:
                    self.logger.warning(f"[neko_claudian] close mcp err: {e}")

            self.logger.info("[neko_claudian] shutdown ok")
            return Ok({"status": "stopped"})
        except Exception as e:
            self.logger.exception(f"[neko_claudian] shutdown err: {e}")
            return Err(SdkError(f"shutdown err: {e}"))

    # -----------------------------------------------------------------------
    # 状态查询 / 健康检查
    # -----------------------------------------------------------------------

    @plugin_entry(
        id="get_status",
        name="获取插件状态",
        description="返回 neko_claudian 插件运行状态、UI URL、HTTP 端口、子进程信息",
    )
    def get_status(self, **_):
        """健康检查入口（V2 阶段提供，后续阶段扩展更多字段）"""
        runtime_status = "not_initialized"
        if self._claude_runtime is not None and hasattr(self._claude_runtime, "is_ready"):
            runtime_status = (
                "ready" if self._claude_runtime.is_ready() else "starting"
            )
        tab_count = 0
        if self._tab_manager is not None and hasattr(self._tab_manager, "tabs"):
            try:
                tab_count = len(self._tab_manager.tabs)
            except Exception:
                tab_count = 0
        return Ok({
            "status": "stopped" if self._shutting_down else "running",
            "ui_url": f"http://127.0.0.1:48916/plugin/{self._plugin_id}/ui/",
            "http_port": self._http_port,
            "claude_runtime": runtime_status,
            "tab_count": tab_count,
            "version": "1.0.0",
        })

    # -----------------------------------------------------------------------
    # 猫娘专用注入入口（V10 完整实现；V2 提供骨架）
    # -----------------------------------------------------------------------

    async def _broadcast_sse(self, tab_id: Optional[str], event: dict):
        """通过 HTTP server 的 SSE 通道广播事件给前端。"""
        if self._http_server is None:
            return
        await self._http_server.broadcast(tab_id, event)

    @plugin_entry(
        id="neko_inject_text",
        name="猫娘注入文本到输入框",
        description="猫娘 LLM 调用此入口，把文本插入到前端 UI 对话框（不发送）",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要注入的文本"},
                "tab_id": {"type": "string", "description": "目标 tab（缺省=当前）"},
            },
            "required": ["text"],
        },
    )
    async def neko_inject_text(self, text: str, tab_id: str = None, **_):
        """
        把文本塞进前端 UI 的输入框。猫娘 LLM 调试 / 预览用。
        对应 Claudian InputController.setDraft 的"模拟"路径。
        """
        if not text:
            return Err(SdkError("text 不能为空"))
        await self._broadcast_sse(tab_id, {
            "type": NEKO_EVENT_INJECT_TEXT,
            "text": text,
            "tab_id": tab_id,
        })
        return Ok({"injected": True, "length": len(text), "tab_id": tab_id})

    @plugin_entry(
        id="neko_send_silently",
        name="猫娘静默发送消息给 Claude Code",
        description="猫娘 LLM 调用此入口，直接把消息发给 Claude Code，跳过前端 UI",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要发送的消息"},
                "tab_id": {"type": "string", "description": "目标 tab（缺省=当前）"},
            },
            "required": ["text"],
        },
    )
    async def neko_send_silently(self, text: str, tab_id: str = None, **_):
        """
        直接调用 InputController.send_message。
        与 Claudian InputController.sendMessage 路径一致。
        """
        if not text:
            return Err(SdkError("text 不能为空"))
        if self._input_controller is None:
            return Err(SdkError(
                "InputController 尚未初始化（V4 阶段后可用）"
            ))
        try:
            return await self._input_controller.send_message(
                text=text, tab_id=tab_id, _silent=True
            )
        except Exception as e:
            self.logger.exception(f"[neko_claudian] neko_send_silently err: {e}")
            return Err(SdkError(f"send failed: {e}"))

    @plugin_entry(
        id="neko_click_send",
        name="猫娘模拟点击发送按钮",
        description="通过 SSE 通知前端点击发送按钮，触发前端 InputController 的发送流程",
        input_schema={
            "type": "object",
            "properties": {
                "tab_id": {"type": "string", "description": "目标 tab（缺省=当前）"},
            },
        },
    )
    async def neko_click_send(self, tab_id: str = None, **_):
        await self._broadcast_sse(tab_id, {
            "type": NEKO_EVENT_CLICK_SEND,
            "tab_id": tab_id,
        })
        return Ok({"clicked": True, "tab_id": tab_id})

    @plugin_entry(
        id="push_claude_reply",
        name="把 Claude 的回复推给猫娘",
        description="把 Claude 的最终回复（合并后）通过 push_message 推给主系统 / 猫娘 LLM",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Claude 的回复文本"},
                "tab_id": {"type": "string", "description": "来源 tab（可选）"},
                "visibility": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["chat", "hud"]},
                    "description": "显示渠道（chat / hud）",
                },
            },
            "required": ["text"],
        },
    )
    async def push_claude_reply(
        self,
        text: str,
        tab_id: str = None,
        visibility: Optional[List[str]] = None,
        **_,
    ):
        if not text:
            return Err(SdkError("text 不能为空"))
        if visibility is None:
            visibility = ["chat", "hud"]
        try:
            self.push_message(
                visibility=visibility,
                ai_behavior="read",
                parts=[{"type": "text", "text": text}],
                source="neko_claudian",
                metadata={"tab_id": tab_id, "plugin": "neko_claudian"},
            )
            return Ok({"pushed": True, "length": len(text), "tab_id": tab_id})
        except Exception as e:
            self.logger.exception(f"[neko_claudian] push_claude_reply err: {e}")
            return Err(SdkError(f"push failed: {e}"))

    @plugin_entry(
        id="neko_observe_stream",
        name="猫娘订阅某个 Tab 的 Claude 流",
        description="订阅某个 Tab 的 Claude 流式事件（旁听）",
        input_schema={
            "type": "object",
            "properties": {
                "tab_id": {"type": "string", "description": "要订阅的 tab"},
                "max_events": {"type": "integer", "default": 50},
            },
        },
    )
    async def neko_observe_stream(
        self, tab_id: str = None, max_events: int = 50, **_
    ):
        """
        抓取 tab_id 对应 tab 的流式事件队列，max_events 个或 5 秒超时。
        用于猫娘 LLM 旁听 Claude 工作过程。
        """
        if self._http_server is None:
            return Err(SdkError("http server 未启动"))
        events: List[dict] = []
        try:
            events = await self._http_server.drain_events(tab_id, max_events)
        except Exception as e:
            self.logger.warning(f"[neko_claudian] observe err: {e}")
        return Ok({"events": events, "count": len(events), "tab_id": tab_id})


# ---------------------------------------------------------------------------
# 占位实现（V2 阶段：先让插件能跑起来）
# ---------------------------------------------------------------------------

def _load_initial_settings(workspace: Path) -> dict:
    """
    V2 占位：从 settings.json 加载（V9 阶段替换为完整 Settings 类）。
    与 Claudian defaultSettings 对齐的关键字段。
    """
    settings_file = _plugin_root() / "settings.json"
    defaults = {
        "model": "claude-sonnet-4-5",
        "permissionMode": "default",
        "maxThinkingTokens": 8000,
        "cwd": str(workspace),
        "enableMcp": True,
        "enableSkills": True,
        "enableSubagents": True,
        "enableRewind": True,
        "enablePlanMode": True,
        "allowedTools": [],
        "mcpServers": {},
        "agents": [],
        "skills": [],
        "slashCommands": [],
        "permissionRules": [],
    }
    if settings_file.exists():
        try:
            overrides = json.loads(settings_file.read_text(encoding="utf-8"))
            defaults.update(overrides)
        except Exception:
            pass
    return defaults


class _JsonStorage:
    """
    简易 JSON 存储（V6 阶段替换为完整 StorageService）。
    仿 Claudian SharedStorageService。
    """

    def __init__(self, base: Path):
        self.base = base
        self.base.mkdir(parents=True, exist_ok=True)

    def _file(self, name: str) -> Path:
        return self.base / f"{name}.json"

    async def get(self, name: str, default: Any = None) -> Any:
        f = self._file(name)
        if not f.exists():
            return default
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return default

    async def set(self, name: str, value: Any) -> None:
        f = self._file(name)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(f)


# ---------------------------------------------------------------------------
# HTTP server (aiohttp)
# ---------------------------------------------------------------------------

class _HttpServer:
    """
    独立 aiohttp HTTP server，端口 48930。

    路由（仿 Claudian 后端接口 + N.E.K.O catgirl 扩展）：
      GET  /neko_claudian/api/health
      GET  /neko_claudian/api/status
      GET  /neko_claudian/api/tabs
      POST /neko_claudian/api/tab/new
      POST /neko_claudian/api/tab/close
      POST /neko_claudian/api/tab/switch
      GET  /neko_claudian/api/tab/{tab_id}/messages
      POST /neko_claudian/api/send                 (InputController.send)
      GET  /neko_claudian/api/stream/{tab_id}      (SSE StreamController)
      POST /neko_claudian/api/permission          (control_request 决策)
      POST /neko_claudian/api/rewind
      POST /neko_claudian/api/stop
      GET  /neko_claudian/api/sessions
      GET  /neko_claudian/api/history
      GET  /neko_claudian/api/settings
      POST /neko_claudian/api/settings
      GET  /neko_claudian/api/mcp/servers
      POST /neko_claudian/api/mcp/test
      GET  /neko_claudian/api/agents
      GET  /neko_claudian/api/skills
      GET  /neko_claudian/api/commands
      GET  /neko_claudian/api/i18n/{lang}
      GET  /neko_claudian/api/i18n
      GET  /neko_claudian/api/catgirl/health
    """

    def __init__(self, plugin: "NekoClaudianPlugin"):
        self.plugin = plugin
        self.app: Optional[Any] = None
        self.runner: Optional[Any] = None
        self.site: Optional[Any] = None
        self._port: int = 0
        # SSE 订阅者 {tab_id_or_global: list[asyncio.Queue]}
        self._subs: Dict[str, List[asyncio.Queue]] = {"*": []}
        self._lock = asyncio.Lock()

    async def start(self, port: int):
        try:
            from aiohttp import web
        except ImportError:
            self.plugin.logger.error(
                "[neko_claudian] aiohttp 未安装；HTTP server 不可用。"
                "请 pip install aiohttp"
            )
            raise

        self.app = web.Application()
        self._register_routes(self.app)
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", port)
        await self.site.start()
        self._port = port
        self.plugin.logger.info(
            f"[neko_claudian] http server listening on 127.0.0.1:{port}"
        )

    async def stop(self):
        try:
            if self.site is not None:
                await self.site.stop()
                self.site = None
            if self.runner is not None:
                await self.runner.cleanup()
                self.runner = None
            self.app = None
        except Exception as e:
            self.plugin.logger.warning(f"[neko_claudian] http stop err: {e}")

    # -------------------------------------------------------------------
    # 路由
    # -------------------------------------------------------------------

    def _register_routes(self, app):
        from aiohttp import web

        # 基础
        app.router.add_get(_API_PREFIX + "/health", self._handle_health)
        app.router.add_get(_API_PREFIX + "/status", self._handle_status)
        app.router.add_get(_API_PREFIX + "/catgirl/health", self._handle_health)

        # Tab
        app.router.add_get(_API_PREFIX + "/tabs", self._handle_tabs)
        app.router.add_post(_API_PREFIX + "/tab/new", self._handle_tab_new)
        app.router.add_post(_API_PREFIX + "/tab/close", self._handle_tab_close)
        app.router.add_post(_API_PREFIX + "/tab/switch", self._handle_tab_switch)
        app.router.add_get(
            _API_PREFIX + "/tab/{tab_id}/messages",
            self._handle_tab_messages,
        )

        # 消息 / 流
        app.router.add_post(_API_PREFIX + "/send", self._handle_send)
        app.router.add_get(
            _API_PREFIX + "/stream/{tab_id}", self._handle_stream
        )
        app.router.add_post(_API_PREFIX + "/permission", self._handle_permission)
        app.router.add_post(_API_PREFIX + "/rewind", self._handle_rewind)
        app.router.add_post(_API_PREFIX + "/stop", self._handle_stop)
        app.router.add_post(_API_PREFIX + "/cancel", self._handle_cancel)

        # 会话 / 历史
        app.router.add_get(_API_PREFIX + "/sessions", self._handle_sessions)
        app.router.add_get(_API_PREFIX + "/history", self._handle_history)

        # 设置
        app.router.add_get(_API_PREFIX + "/settings", self._handle_get_settings)
        app.router.add_post(_API_PREFIX + "/settings", self._handle_set_settings)

        # MCP
        app.router.add_get(_API_PREFIX + "/mcp/servers", self._handle_mcp_servers)
        app.router.add_post(_API_PREFIX + "/mcp/test", self._handle_mcp_test)

        # Agent / Skills / Commands
        app.router.add_get(_API_PREFIX + "/agents", self._handle_agents)
        app.router.add_get(_API_PREFIX + "/skills", self._handle_skills)
        app.router.add_get(_API_PREFIX + "/commands", self._handle_commands)

        # Conversations
        app.router.add_get(_API_PREFIX + "/conversations", self._handle_conversations)
        app.router.add_post(_API_PREFIX + "/conversation/new", self._handle_conversation_new)

        # i18n
        app.router.add_get(_API_PREFIX + "/i18n", self._handle_i18n_list)
        app.router.add_get(
            _API_PREFIX + "/i18n/{lang}", self._handle_i18n_lang
        )

    # -------------------------------------------------------------------
    # SSE 广播
    # -------------------------------------------------------------------

    async def broadcast(self, tab_id: Optional[str], event: dict):
        """广播事件给所有订阅者。"""
        if not self._subs:
            return
        async with self._lock:
            targets: List[asyncio.Queue] = []
            targets.extend(self._subs.get("*", []))
            if tab_id:
                targets.extend(self._subs.get(tab_id, []))
        for q in targets:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
            except Exception:
                pass

    async def drain_events(
        self, tab_id: Optional[str], max_events: int
    ) -> List[dict]:
        """猫娘 neko_observe_stream 用：抓最多 max_events 个事件。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=1024)
        key = tab_id or "*"
        async with self._lock:
            self._subs.setdefault(key, []).append(q)
        events: List[dict] = []
        try:
            for _ in range(max_events):
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=0.5)
                    events.append(ev)
                except asyncio.TimeoutError:
                    break
        finally:
            async with self._lock:
                lst = self._subs.get(key, [])
                if q in lst:
                    lst.remove(q)
        return events

    # -------------------------------------------------------------------
    # 处理器（V2 阶段全部返回占位；后续阶段填充真实逻辑）
    # -------------------------------------------------------------------

    async def _handle_health(self, request):
        from aiohttp import web
        return web.json_response({
            "ok": True,
            "plugin": "neko_claudian",
            "version": "1.0.0",
            "port": self._port,
        })

    async def _handle_status(self, request):
        from aiohttp import web
        # 直接调 plugin 的 get_status
        res = self.plugin.get_status()
        if isinstance(res, Ok):
            return web.json_response(res.value)
        return web.json_response({"error": str(res.error)}, status=500)

    async def _handle_tabs(self, request):
        from aiohttp import web
        # 返回简单的单 tab 结构
        return web.json_response({
            "tabs": [{
                "id": "default",
                "title": "新对话",
                "isActive": True,
            }],
            "current_tab_id": "default",
        })

    async def _handle_tab_new(self, request):
        from aiohttp import web
        return web.json_response({
            "ok": False,
            "error": "TabManager 尚未初始化（V5 阶段后可用）",
        }, status=501)

    async def _handle_tab_close(self, request):
        from aiohttp import web
        return web.json_response({
            "ok": False,
            "error": "TabManager 尚未初始化（V5 阶段后可用）",
        }, status=501)

    async def _handle_tab_switch(self, request):
        from aiohttp import web
        return web.json_response({
            "ok": False,
            "error": "TabManager 尚未初始化（V5 阶段后可用）",
        }, status=501)

    async def _handle_tab_messages(self, request):
        from aiohttp import web
        tab_id = request.match_info.get("tab_id", "default")
        return web.json_response({
            "messages": self.plugin._messages,
            "tab_id": tab_id,
        })

    async def _handle_send(self, request):
        """处理消息发送请求 — 调用 Claude CLI 并通过 SSE 流式返回结果。"""
        from aiohttp import web
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        text = (data.get("text") or "").strip()
        tab_id = data.get("tab_id")

        if not text:
            return web.json_response({"error": "text empty"}, status=400)

        # 检查 runtime
        if self.plugin._claude_runtime is None:
            return web.json_response({
                "ok": False,
                "error": "Claude runtime 未初始化，请检查 Claude CLI 是否安装",
            }, status=500)

        # 添加用户消息到历史
        user_msg = {
            "id": f"msg-{len(self.plugin._messages)}",
            "role": "user",
            "content": text,
            "timestamp": asyncio.get_event_loop().time(),
        }
        self.plugin._messages.append(user_msg)

        # 广播用户消息
        await self.broadcast(tab_id, {
            "type": "user_message",
            "message": user_msg,
        })

        # 异步执行 Claude 查询
        asyncio.create_task(self._execute_claude_query(text, tab_id))

        return web.json_response({"ok": True, "message": user_msg})

    async def _execute_claude_query(self, text: str, tab_id: Optional[str] = None):
        """执行 Claude 查询并通过 SSE 广播结果。"""
        try:
            # 广播开始
            await self.broadcast(tab_id, {"type": "stream_start"})

            # 调用 Claude
            async for chunk in self.plugin._claude_runtime.query(text):
                # 广播每个 chunk
                await self.broadcast(tab_id, chunk)

                # 如果是完成或错误，停止
                if chunk.get("type") in ("done", "error"):
                    break

            # 广播结束
            await self.broadcast(tab_id, {"type": "stream_end"})

        except Exception as e:
            self.plugin.logger.error(f"[neko_claudian] query error: {e}")
            await self.broadcast(tab_id, {
                "type": "error",
                "content": str(e),
            })

    async def _handle_stream(self, request):
        """SSE 流：订阅 tab_id 的所有事件，转发给浏览器。"""
        from aiohttp import web
        tab_id = request.match_info.get("tab_id", "*")
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)

        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        key = tab_id if tab_id != "*" else "*"
        async with self._lock:
            self._subs.setdefault(key, []).append(q)
        self.plugin.logger.info(
            f"[neko_claudian] sse client connected: tab={tab_id}"
        )
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    payload = f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    await resp.write(payload.encode("utf-8"))
                except asyncio.TimeoutError:
                    # 15s 心跳
                    await resp.write(b": ping\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            async with self._lock:
                lst = self._subs.get(key, [])
                if q in lst:
                    lst.remove(q)
            self.plugin.logger.info(
                f"[neko_claudian] sse client disconnected: tab={tab_id}"
            )
        return resp

    async def _handle_permission(self, request):
        from aiohttp import web
        return web.json_response({
            "ok": False,
            "error": "ApprovalManager 尚未初始化（V6 阶段后可用）",
        }, status=501)

    async def _handle_rewind(self, request):
        from aiohttp import web
        return web.json_response({
            "ok": False,
            "error": "RewindService 尚未初始化（V4 阶段后可用）",
        }, status=501)

    async def _handle_stop(self, request):
        from aiohttp import web
        # 取消当前 Claude 查询
        if self.plugin._claude_runtime:
            self.plugin._claude_runtime.cancel()
        return web.json_response({"ok": True, "stopped": True})

    async def _handle_cancel(self, request):
        from aiohttp import web
        # 取消当前 Claude 查询
        if self.plugin._claude_runtime:
            self.plugin._claude_runtime.cancel()
        return web.json_response({"ok": True, "cancelled": True})

    async def _handle_sessions(self, request):
        from aiohttp import web
        sessions = list(self.plugin._conversations.values())
        return web.json_response({"sessions": sessions})

    async def _handle_conversations(self, request):
        from aiohttp import web
        conversations = list(self.plugin._conversations.values())
        return web.json_response({"conversations": conversations})

    async def _handle_conversation_new(self, request):
        from aiohttp import web
        conv_id = f"conv-{len(self.plugin._conversations)}"
        conversation = {
            "id": conv_id,
            "title": "新对话",
            "createdAt": asyncio.get_event_loop().time(),
            "messages": [],
        }
        self.plugin._conversations[conv_id] = conversation
        self.plugin._current_conversation_id = conv_id
        self.plugin._messages = []
        return web.json_response({"ok": True, "conversation": conversation})

    async def _handle_history(self, request):
        from aiohttp import web
        return web.json_response({"history": []})

    async def _handle_get_settings(self, request):
        from aiohttp import web
        return web.json_response(self.plugin._settings or {})

    async def _handle_set_settings(self, request):
        from aiohttp import web
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        if self.plugin._settings is None:
            self.plugin._settings = {}
        self.plugin._settings.update(data)
        return web.json_response({"ok": True})

    async def _handle_mcp_servers(self, request):
        from aiohttp import web
        return web.json_response({"servers": []})

    async def _handle_mcp_test(self, request):
        from aiohttp import web
        return web.json_response({
            "ok": False,
            "error": "McpServerManager 尚未初始化（V6 阶段后可用）",
        }, status=501)

    async def _handle_agents(self, request):
        from aiohttp import web
        return web.json_response({"agents": []})

    async def _handle_skills(self, request):
        from aiohttp import web
        return web.json_response({"skills": []})

    async def _handle_commands(self, request):
        from aiohttp import web
        return web.json_response({"commands": []})

    async def _handle_i18n_list(self, request):
        from aiohttp import web
        return web.json_response({
            "languages": [
                "en", "zh-CN", "zh-TW", "ja", "ko", "fr", "de",
                "es", "pt", "ru",
            ],
        })

    async def _handle_i18n_lang(self, request):
        from aiohttp import web
        lang = request.match_info.get("lang", "en")
        path = _static_dir() / "i18n" / f"{lang}.json"
        if not path.exists():
            return web.json_response({"error": "lang not found"}, status=404)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return web.json_response(data)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
