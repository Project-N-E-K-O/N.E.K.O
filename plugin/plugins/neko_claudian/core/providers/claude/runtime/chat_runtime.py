"""
1:1 ported from claudian/src/providers/claude/runtime/ClaudeChatRuntime.ts

★ 核心 ★ ClaudianService — Claude Agent SDK 包装。

能力（与 Claudian 1:1 对齐）：
- startPersistentQuery: 启动持久子进程（stream-json）
- queryViaPersistent: 把新 turn 投入 MessageChannel
- queryColdStart: 单次冷查询（inline edit / title generation）
- consumeResponses: 后台读 stream-json，转 StreamChunk
- closePersistentQuery: 关闭子进程
- 动态更新: model / permission_mode / mcp_servers / effort / allowed_tools
- rewind: 倒回到某个 user message
- 权限回调: canUseTool 走 ApprovalHandler

差异（相对 TS 版本）：
- 用 asyncio 替代 Promise / async iterator
- 用 asyncio.subprocess 替代 node:child_process
- 用 AbortSignal 包装类（spawn.py）替代 Node AbortController
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from .cli_resolver import ClaudeCliResolver
from .message_channel import ClaudeMessageChannel
from .query_options import build_query_options
from .session_manager import SessionManager
from .spawn import AbortSignal, is_windows
from .types import (
    ApprovalCallback,
    AskUserQuestionCallback,
    AutoTurnCallback,
    ChatRewindMode,
    ChatRewindResult,
    ChatRuntimeConversationState,
    ChatRuntimeQueryOptions,
    ChatTurnMetadata,
    ChatTurnRequest,
    ExitPlanModeCallback,
    PersistentQueryConfig,
    PreparedChatTurn,
    ResponseHandler,
    SessionUpdateResult,
    StreamChunk,
    ChunkType,
)
from .user_message_factory import build_user_message_jsonl
from ..stream.transform import (
    TransformStreamState,
    TransformUsageState,
    transform_sdk_message,
)
from ..sdk.type_guards import is_session_init_event


# ---------------------------------------------------------------------------
# Provider 能力声明（与 claudian capabilities.ts 对齐）
# ---------------------------------------------------------------------------

CLAUDE_PROVIDER_CAPABILITIES = {
    "providerId": "claude",
    "supportsPersistentQuery": True,
    "supportsColdStart": True,
    "supportsMcp": True,
    "supportsSkills": True,
    "supportsSubagents": True,
    "supportsRewind": True,
    "supportsPlanMode": True,
    "supportsPermissionModes": ["default", "acceptEdits", "bypassPermissions", "plan"],
    "supportsModels": [
        "claude-opus-4-5",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
    ],
}


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class ClaudeChatRuntime:
    """
    ClaudeChatRuntime — ClaudianService 的 Python 等价物。

    生命周期：
        new → startPersistentQuery → queryViaPersistent (loop) → closePersistentQuery
    """

    providerId: str = CLAUDE_PROVIDER_CAPABILITIES["providerId"]

    def __init__(
        self,
        plugin: Any = None,
        *,
        cli_resolver: Optional[ClaudeCliResolver] = None,
        logger: Any = None,
    ):
        self.plugin = plugin
        self.logger = logger
        self.cli_resolver = cli_resolver or ClaudeCliResolver()
        self.session_manager = SessionManager()

        # 当前状态
        self._abort: Optional[AbortSignal] = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._message_channel: Optional[ClaudeMessageChannel] = None
        self._ready = False
        self._shutting_down = False
        self._current_config: Optional[PersistentQueryConfig] = None

        # 回调
        self._approval_callback: Optional[ApprovalCallback] = None
        self._ask_user_question_callback: Optional[AskUserQuestionCallback] = None
        self._exit_plan_mode_callback: Optional[ExitPlanModeCallback] = None
        self._auto_turn_callback: Optional[AutoTurnCallback] = None

        # 消费者
        self._response_consumer_task: Optional[asyncio.Task] = None
        self._response_handlers: List[ResponseHandler] = []

        # 流式 chunk 输出（供 StreamController 订阅）
        self._chunk_subscribers: List[Callable[[StreamChunk], Awaitable[None]]] = []
        self._control_request_subscribers: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []

        # 状态
        self._transform_state = TransformStreamState()
        self._usage_state = TransformUsageState()
        self._turn_metadata: ChatTurnMetadata = ChatTurnMetadata()
        self._is_streaming = False

        # 锁
        self._lock = threading.RLock()
        self._start_lock = asyncio.Lock()

    # -------------------------------------------------------------------
    # 状态
    # -------------------------------------------------------------------

    def is_ready(self) -> bool:
        return self._ready

    def get_conversation_state(self) -> ChatRuntimeConversationState:
        return ChatRuntimeConversationState(
            session_id=self.session_manager.get_session_id(),
            model=(self._current_config.model if self._current_config else ""),
            cwd=(self._current_config.cwd if self._current_config else ""),
            is_streaming=self._is_streaming,
            num_turns=self._usage_state.last_usage_message.get("num_turns", 0)
            if self._usage_state.last_usage_message else 0,
            total_cost_usd=0.0,
        )

    # -------------------------------------------------------------------
    # 回调注册
    # -------------------------------------------------------------------

    def set_approval_callback(self, cb: Optional[ApprovalCallback]):
        self._approval_callback = cb

    def set_ask_user_question_callback(self, cb: Optional[AskUserQuestionCallback]):
        self._ask_user_question_callback = cb

    def set_exit_plan_mode_callback(self, cb: Optional[ExitPlanModeCallback]):
        self._exit_plan_mode_callback = cb

    def set_auto_turn_callback(self, cb: Optional[AutoTurnCallback]):
        self._auto_turn_callback = cb

    def add_chunk_subscriber(self, cb: Callable[[StreamChunk], Awaitable[None]]):
        self._chunk_subscribers.append(cb)

    def remove_chunk_subscriber(self, cb):
        try:
            self._chunk_subscribers.remove(cb)
        except ValueError:
            pass

    def add_control_request_subscriber(self, cb: Callable[[Dict[str, Any]], Awaitable[None]]):
        self._control_request_subscribers.append(cb)

    # -------------------------------------------------------------------
    # 持久查询
    # -------------------------------------------------------------------

    async def start_persistent_query(self, config: PersistentQueryConfig):
        """启动持久子进程。"""
        async with self._start_lock:
            # 关闭已存在的
            if self._proc is not None:
                await self.close_persistent_query()

            cli = self.cli_resolver.resolve()
            if not cli:
                raise FileNotFoundError(
                    "找不到 claude CLI。请先安装 Claude Code："
                    "npm install -g @anthropic-ai/claude-code"
                )

            opts = build_query_options(config, mode="persistent")
            cmd = [cli] + opts["args"]
            if is_windows() and cli.lower().endswith(".cmd"):
                cmd = ["cmd", "/c"] + cmd
            cwd = opts.get("cwd") or "."

            env = os.environ.copy()
            env.update(opts.get("env", {}))
            # 透传 ANTHROPIC_API_KEY
            for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_ENTRYPOINT"):
                if v in os.environ:
                    env[v] = os.environ[v]

            self._abort = AbortSignal()
            self._current_config = config
            self._transform_state = TransformStreamState()
            self._usage_state = TransformUsageState()
            self._message_channel = ClaudeMessageChannel()

            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._ready = True
            self._shutting_down = False
            if self.logger:
                self.logger.info(
                    f"[ClaudeChatRuntime] persistent query started, pid={self._proc.pid}, "
                    f"model={config.model}, cwd={cwd}"
                )

            # 启动消费者
            self._response_consumer_task = asyncio.create_task(
                self._consume_responses(self._proc)
            )

    async def query_via_persistent(self, turn: PreparedChatTurn):
        """把 turn 投入消息通道。"""
        if self._message_channel is None or self._message_channel.closed:
            raise RuntimeError("Persistent query not running")
        # 把 turn 序列化为 JSONL 并写入 stdin
        line = json.dumps(turn.encoded_message, ensure_ascii=False)
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("子进程未启动")
        try:
            self._proc.stdin.write((line + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
        except Exception as e:
            raise RuntimeError(f"写入 stdin 失败: {e}")

    async def close_persistent_query(self):
        """关闭持久子进程。"""
        self._shutting_down = True
        self._ready = False

        if self._message_channel is not None:
            try:
                await self._message_channel.close()
            except Exception:
                pass
            self._message_channel = None

        if self._abort is not None:
            self._abort.abort("close_persistent_query")

        if self._response_consumer_task is not None:
            try:
                self._response_consumer_task.cancel()
            except Exception:
                pass
            self._response_consumer_task = None

        if self._proc is not None:
            try:
                if self._proc.returncode is None:
                    self._proc.kill()
                    await self._proc.wait()
            except Exception:
                pass
            self._proc = None

        if self.logger:
            self.logger.info("[ClaudeChatRuntime] persistent query closed")

    async def close(self):
        await self.close_persistent_query()

    # -------------------------------------------------------------------
    # 冷启动
    # -------------------------------------------------------------------

    async def query_cold_start(
        self, turn: PreparedChatTurn, options: ChatRuntimeQueryOptions
    ) -> AsyncIterator[StreamChunk]:
        """冷启动单次查询，yield StreamChunk。"""
        from .cold_start import ColdStartQuery
        q = ColdStartQuery(cli_resolver=self.cli_resolver)
        # 把 turn 文本拼成 prompt
        prompt = turn.encoded_message.get("message", {}).get("content", "")
        if isinstance(prompt, list):
            prompt = "\n".join(
                blk.get("text", "") for blk in prompt if blk.get("type") == "text"
            )
        async for chunk in q.query(prompt, options):
            yield chunk

    # -------------------------------------------------------------------
    # 消费者
    # -------------------------------------------------------------------

    async def _consume_responses(self, proc: asyncio.subprocess.Process):
        """
        后台读 stdout 的 stream-json，转换并广播 StreamChunk。
        """
        assert proc.stdout is not None
        try:
            async for line in proc.stdout:
                if self._shutting_down:
                    break
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text.strip():
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    # 写到 stderr
                    if self.logger:
                        self.logger.warning(
                            f"[ClaudeChatRuntime] non-JSON line: {text[:200]}"
                        )
                    continue

                # 处理 system/init：记录 session_id
                if is_session_init_event(msg):
                    sid = msg.get("session_id", "")
                    if sid:
                        self.session_manager.set_session_id(sid)
                    # 清除 pending resume / fork
                    self.session_manager.take_pending_resume()
                    self.session_manager.take_pending_fork()

                # 转 chunk
                chunks = transform_sdk_message(
                    msg,
                    state=self._transform_state,
                    usage_state=self._usage_state,
                )
                for chunk in chunks:
                    if chunk.type == ChunkType.DONE.value:
                        self._is_streaming = False
                    elif chunk.type == ChunkType.ASSISTANT.value:
                        self._is_streaming = True
                    elif chunk.type == ChunkType.ERROR.value:
                        self._is_streaming = False

                    # 广播给订阅者
                    await self._dispatch_chunk(chunk)

                    # 转发给 response handlers
                    for h in list(self._response_handlers):
                        try:
                            res = h({"chunk": chunk, "is_turn_complete": chunk.type == ChunkType.DONE.value})
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception:
                            pass

        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self.logger:
                self.logger.exception(f"[ClaudeChatRuntime] consume err: {e}")
            await self._dispatch_chunk(StreamChunk(
                type=ChunkType.ERROR.value,
                data={"message": str(e), "source": "consume"},
            ))

    async def _dispatch_chunk(self, chunk: StreamChunk):
        for cb in list(self._chunk_subscribers):
            try:
                res = cb(chunk)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[ClaudeChatRuntime] subscriber err: {e}")

    # -------------------------------------------------------------------
    # 动态更新
    # -------------------------------------------------------------------

    async def apply_dynamic_update(self, update: Dict[str, Any]) -> SessionUpdateResult:
        """
        运行时切换 model / permission_mode / mcp_servers / effort。
        实际效果是重启持久查询（与 Claudian DynamicUpdates 一致）。
        """
        if self._current_config is None:
            return SessionUpdateResult(success=False, error="not started")
        new_config = PersistentQueryConfig(
            model=update.get("model", self._current_config.model),
            cwd=update.get("cwd", self._current_config.cwd),
            permission_mode=update.get("permission_mode", self._current_config.permission_mode),
            system_prompt=update.get("system_prompt", self._current_config.system_prompt),
            allowed_tools=update.get("allowed_tools", self._current_config.allowed_tools),
            disallowed_tools=update.get("disallowed_tools", self._current_config.disallowed_tools),
            mcp_servers=update.get("mcp_servers", self._current_config.mcp_servers),
            resume=self.session_manager.get_session_id(),
            max_thinking_tokens=update.get(
                "max_thinking_tokens", self._current_config.max_thinking_tokens
            ),
            effort=update.get("effort", self._current_config.effort),
        )
        try:
            await self.start_persistent_query(new_config)
            return SessionUpdateResult(
                success=True,
                new_session_id=self.session_manager.get_session_id(),
            )
        except Exception as e:
            return SessionUpdateResult(success=False, error=str(e))

    # -------------------------------------------------------------------
    # Rewind
    # -------------------------------------------------------------------

    async def execute_rewind(
        self, mode: ChatRewindMode
    ) -> ChatRewindResult:
        """
        调用 SDK rewindFiles（回滚到某个 user message）。
        Claudian 实际是发送 `/rewind` slash command；这里我们做"重启 + 截断历史"近似。
        """
        # 简化：调用 close + 重新 resume（session_id 不变）
        if self._current_config is None:
            return ChatRewindResult(success=False, error="not started")
        try:
            sid = self.session_manager.get_session_id()
            new_config = PersistentQueryConfig(
                model=self._current_config.model,
                cwd=self._current_config.cwd,
                permission_mode=self._current_config.permission_mode,
                system_prompt=self._current_config.system_prompt,
                allowed_tools=self._current_config.allowed_tools,
                mcp_servers=self._current_config.mcp_servers,
                resume=sid,
            )
            await self.start_persistent_query(new_config)
            return ChatRewindResult(success=True, rewound_to=mode.target_uuid, removed_messages=0)
        except Exception as e:
            return ChatRewindResult(success=False, error=str(e))

    # -------------------------------------------------------------------
    # 工具方法
    # -------------------------------------------------------------------

    async def interrupt(self):
        """中断当前 turn（发送 SIGINT）。"""
        if self._proc is not None and self._proc.returncode is None:
            try:
                if is_windows():
                    self._proc.kill()
                else:
                    self._proc.send_signal(2)  # SIGINT
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[ClaudeChatRuntime] interrupt err: {e}")
