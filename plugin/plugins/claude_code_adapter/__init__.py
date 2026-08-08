"""Claude Code Adapter Plugin

通过 @llm_tool 将 Claude Code CLI 注册为猫娘可调用的工具集。

猫娘可以通过以下工具调用 Claude Code 开发项目：
- claude_code_submit: 提交 Claude Code 任务到后台异步执行
- claude_code_poll: 查询任务状态和结果
- claude_code_cancel: 取消正在运行的任务
- claude_code_check_health: 检查 Claude CLI 是否可用
- claude_code_list_sessions: 列出所有会话
- claude_code_clear_session: 清除会话记录
- claude_code_get_config: 获取当前适配器配置

异步任务系统：
- 采用异步提交 + 轮询模式，避免 main_server 的 300 秒超时限制
- 任务在后台执行，猫娘可以自主控制查询频率
- 支持最多 2 个并发任务，结果保留 10 分钟

设计参考：
- Paperclip `claude-local` 适配器的执行流程
- N.E.K.O `neko_claudian` 插件的跨平台 spawn 工具
- N.E.K.O `mcp_adapter` 插件的 Plugin 范式
- `outputs/02-Python实现Claude-Code适配器完整方案.md` 的数据结构设计
"""

from __future__ import annotations

import os
from typing import Any, Optional

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    llm_tool,
    Ok,
    Err,
    SdkError,
)

from .models import AdapterConfig, ExecuteResult
from .errors import (
    ClassifiedError,
    is_retryable,
)
from .executor import (
    ClaudeCLIExecutor,
    build_cli_invocation,
    detect_claude_cli,
)
from .parser import ClaudeOutputParser
from .session import SessionManager, compute_prompt_signature
from .task_manager import TaskManager


# ---------------------------------------------------------------------------
# 插件主类
# ---------------------------------------------------------------------------


@neko_plugin
class ClaudeCodeAdapterPlugin(NekoPluginBase):
    """Claude Code 适配器插件。

    通过 @llm_tool 装饰器将 Claude Code CLI 的能力暴露给猫娘 LLM。
    猫娘可以在对话中调用这些工具，让 Claude Code 执行具体的编码任务。
    """

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        # 文件日志器（与 web_search 插件一致的模式）
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger

        # 运行时状态
        self._config: AdapterConfig = AdapterConfig()
        self._executor: Optional[ClaudeCLIExecutor] = None
        self._session_mgr: Optional[SessionManager] = None
        self._task_mgr: Optional[TaskManager] = None
        self._ready: bool = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def startup(self, **_) -> Any:
        """启动：加载配置、初始化执行器和会话管理器。"""
        try:
            # 1. 加载 plugin.toml 的 [claude_code] 节
            cfg_dict = await self._load_config_section("claude_code")
            self._config = AdapterConfig.from_config_dict(cfg_dict)

            # 2. 确保 PluginStore 已启用（基类在 __init__ 中已创建 self.store，
            #    但需要根据 plugin.toml 的 [plugin.store] enabled 决定是否可用）
            if not self.store.enabled:
                self.store.enabled = True
                self.logger.info("PluginStore auto-enabled for session persistence")

            # 3. 初始化会话管理器（使用基类的 self.store）
            self._session_mgr = SessionManager(self.store, logger=self.logger)
            await self._session_mgr.load()

            # 4. 初始化执行器
            self._executor = ClaudeCLIExecutor(self._config, logger=self.logger)

            # 5. 初始化任务管理器
            self._task_mgr = TaskManager(self._executor, self._config, logger=self.logger)
            await self._task_mgr.start()
            self.logger.info("TaskManager initialized and started")

            # 6. 检测 Claude CLI 是否可用
            cli_path = self._config.command or detect_claude_cli()
            cli_available = bool(cli_path)

            self._ready = True
            self.logger.info(
                "ClaudeCodeAdapter started: cli_available={} model={!r} cwd={!r}",
                cli_available,
                self._config.model,
                self._config.cwd or os.getcwd(),
            )

            return Ok(
                {
                    "status": "ready",
                    "cli_available": cli_available,
                    "cli_path": cli_path or "",
                    "model": self._config.model,
                    "sessions_loaded": len(self._session_mgr._sessions)
                    if self._session_mgr
                    else 0,
                }
            )
        except Exception as e:
            self.logger.exception("ClaudeCodeAdapter startup failed")
            return Err(SdkError(f"startup failed: {e}"))

    @lifecycle(id="shutdown")
    async def shutdown(self, **_) -> Any:
        """关闭：释放资源。"""
        self._ready = False
        if self._task_mgr:
            await self._task_mgr.stop()
            self.logger.info("TaskManager stopped")
        self.logger.info("ClaudeCodeAdapter shutdown")
        return Ok({"status": "shutdown"})

    # ------------------------------------------------------------------
    # 配置加载辅助
    # ------------------------------------------------------------------

    async def _load_config_section(self, section: str) -> dict[str, Any]:
        """从 plugin.toml 加载指定节。

        SDK 的 self.config.dump() 返回整个 plugin.toml 的字典。
        """
        try:
            cfg = await self.config.dump(timeout=5.0)
            if isinstance(cfg, dict):
                section_data = cfg.get(section)
                if isinstance(section_data, dict):
                    return section_data
        except Exception as e:
            self.logger.warning("Failed to load config section {}: {}", section, e)
        return {}

    def _ensure_ready(self) -> Optional[Any]:
        """检查插件是否就绪。返回 None 表示就绪，否则返回 Err。"""
        if not self._ready:
            return Err(
                SdkError(
                    "Claude Code Adapter not ready (startup not completed or failed)"
                )
            )
        if self._executor is None or self._session_mgr is None:
            return Err(SdkError("Claude Code Adapter internal state invalid"))
        return None

    # ------------------------------------------------------------------
    # 核心执行逻辑（内部方法，被 @llm_tool 方法调用）
    # ------------------------------------------------------------------

    async def _execute_with_retry(
        self,
        prompt: str,
        *,
        cwd: str = "",
        model: str = "",
        effort: str = "",
        max_turns: int = 0,
    ) -> ExecuteResult:
        """执行 Claude Code 任务，支持自动重试。

        重试策略：
        - 首次执行尝试恢复会话（如果存在可恢复的会话）
        - 如果失败且错误可重试，新建会话重试
        - 最多重试 self._config.max_retries 次
        """
        assert self._executor is not None and self._session_mgr is not None

        effective_cwd = cwd or self._config.cwd or os.getcwd()
        signature = compute_prompt_signature(
            system_prompt_file=self._config.append_system_prompt_file,
            skills_dir=self._config.skills_dir,
        )

        # 尝试恢复会话
        resume_record = await self._session_mgr.find_resumable(effective_cwd, signature)
        resume_id = resume_record.session_id if resume_record else ""

        max_attempts = 1 + max(0, self._config.max_retries)
        last_error: Optional[ClassifiedError] = None

        for attempt in range(max_attempts):
            is_retry = attempt > 0
            if is_retry:
                # 重试时清空 resume_id，新建会话
                resume_id = ""
                self.logger.info(
                    "Retrying with new session (attempt {}/{}): prev_error={}",
                    attempt + 1,
                    max_attempts,
                    last_error.kind if last_error else "unknown",
                )

            # 构建 CLI 调用
            invocation, build_err = build_cli_invocation(
                self._config,
                prompt=prompt,
                resume_session_id=resume_id,
                cwd=effective_cwd,
                model=model,
                effort=effort,
                max_turns=max_turns,
            )
            if build_err is not None:
                last_error = build_err
                # CLI 未找到 — 不可重试
                if not is_retryable(build_err.kind):
                    return ExecuteResult(
                        error_kind=build_err.kind,
                        error_message=build_err.message,
                    )
                continue

            # 执行
            parser = ClaudeOutputParser()
            stream, exec_err = await self._executor.execute(invocation, parser)

            # 成功
            if exec_err is None:
                session_id = stream.session_id
                is_new_session = not bool(resume_id)

                # 更新会话记录
                if session_id:
                    try:
                        await self._session_mgr.upsert(
                            session_id, effective_cwd, signature
                        )
                        await self._session_mgr.touch(
                            session_id,
                            turn_count=stream.result.num_turns if stream.result else 0,
                        )
                    except Exception as e:
                        self.logger.warning("Failed to update session record: {}", e)

                # 构造结果
                result = ExecuteResult(
                    session_id=session_id,
                    is_new_session=is_new_session,
                    messages=stream.messages,
                    final_text=stream.final_text,
                    total_cost_usd=stream.result.total_cost_usd
                    if stream.result
                    else 0.0,
                    duration_ms=stream.result.duration_ms if stream.result else 0,
                    num_turns=stream.result.num_turns if stream.result else 0,
                    raw_result=stream.result.raw if stream.result else {},
                )
                return result

            # 失败 — 记录错误并判断是否重试
            last_error = exec_err

            # 标记会话错误
            if resume_id:
                try:
                    await self._session_mgr.mark_error(resume_id, exec_err.kind)
                except Exception:
                    pass

            # 不可重试 — 立即返回
            if not is_retryable(exec_err.kind):
                return ExecuteResult(
                    session_id=resume_id,
                    error_kind=exec_err.kind,
                    error_message=exec_err.message,
                    final_text=stream.final_text,
                    duration_ms=stream.result.duration_ms if stream.result else 0,
                )

            # 可重试 — 继续下一轮
            continue

        # 所有重试都失败
        return ExecuteResult(
            error_kind=last_error.kind if last_error else "unknown",
            error_message=last_error.message if last_error else "all retries exhausted",
        )

    # ==================================================================
    # LLM 工具集（@llm_tool 装饰器注册）
    # ==================================================================

    @llm_tool(
        name="claude_code_submit",
        description=(
            "提交 Claude Code 编码任务到后台异步执行。Claude Code 是一个强大的 AI 编码助手，"
            "可以读写文件、运行命令、调试代码、写测试、查文档等。\n\n"
            "适用场景：\n"
            "- 写新功能、新文件\n"
            "- 修改现有代码、修 bug\n"
            "- 运行测试、构建项目\n"
            "- 代码审查、重构\n"
            "- 查阅项目文档、理解代码结构\n\n"
            "使用流程：\n"
            "1. 调用 claude_code_submit 提交任务，立即返回 task_id\n"
            "2. 调用 claude_code_poll 查询任务状态和结果\n"
            "3. 如需取消，调用 claude_code_cancel\n"
            "4. 如果任务中断，可以使用返回的 session_id 恢复会话\n\n"
            "参数说明：\n"
            "- prompt: 详细描述要让 Claude Code 做什么。要具体、清晰，包含必要的上下文。\n"
            "- cwd: 工作目录（项目根目录的绝对路径）。同一目录的调用会自动复用会话上下文。\n"
            "- model: 模型 ID（可选）。留空使用适配器默认配置。\n"
            "- effort: 推理努力级别（可选）：'low' / 'medium' / 'high'。留空使用默认配置。\n"
            "- max_turns: 最大轮次（可选，0=使用默认值）。\n"
            "- session_id: 恢复之前的会话 ID（可选）。如果提供，Claude Code 会在该会话上下文中继续工作。\n\n"
            "返回：包含 task_id、status 和 session_id 的字典。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "要交给 Claude Code 执行的任务描述。要具体、清晰，包含必要的项目上下文。",
                },
                "cwd": {
                    "type": "string",
                    "description": "工作目录（项目根目录的绝对路径）。同一目录的调用会自动复用会话上下文。留空使用适配器默认配置。",
                },
                "model": {
                    "type": "string",
                    "description": "模型 ID（可选）。例如 'sonnet' / 'opus'。留空使用默认配置。",
                },
                "effort": {
                    "type": "string",
                    "description": "推理努力级别（可选）：'low' / 'medium' / 'high'。留空使用默认配置。",
                    "enum": ["", "low", "medium", "high"],
                },
                "max_turns": {
                    "type": "integer",
                    "description": "最大轮次（可选，0=使用默认值）。",
                },
                "session_id": {
                    "type": "string",
                    "description": "恢复之前的会话 ID（可选）。如果提供，Claude Code 会在该会话上下文中继续工作。用于任务中断后恢复。",
                },
            },
            "required": ["prompt"],
        },
        timeout=10.0,
    )
    async def claude_code_submit(
        self,
        prompt: str = "",
        cwd: str = "",
        model: str = "",
        effort: str = "",
        max_turns: int = 0,
        session_id: str = "",
        **_,
    ) -> dict[str, Any]:
        """提交 Claude Code 任务到后台异步执行。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready

        if not prompt or not prompt.strip():
            return Err(SdkError("prompt 不能为空"))

        if self._task_mgr is None:
            return Err(SdkError("TaskManager not initialized"))

        try:
            record = await self._task_mgr.submit(
                prompt=prompt,
                cwd=cwd,
                model=model,
                effort=effort,
                max_turns=max_turns,
                resume_session_id=session_id,
            )
            return Ok({
                "task_id": record.task_id,
                "status": record.status.value,
                "session_id": session_id,
                "message": "任务已提交，请使用 claude_code_poll 查询结果",
            })
        except Exception as e:
            self.logger.exception("claude_code_submit failed")
            return Err(SdkError(f"提交任务失败: {e}"))

    @llm_tool(
        name="claude_code_poll",
        description=(
            "查询 Claude Code 异步任务的执行状态和结果。\n\n"
            "使用流程：\n"
            "1. 调用 claude_code_submit 提交任务，获取 task_id\n"
            "2. 调用 claude_code_poll 查询任务状态\n"
            "3. 当 status 为 'done' 时，结果在 result 字段中\n"
            "4. 当 status 为 'error' 时，错误信息在 error 字段中\n\n"
            "参数说明：\n"
            "- task_id: 任务 ID（从 claude_code_submit 返回）\n\n"
            "返回：包含 task_id、status、result/error 的字典。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID（从 claude_code_submit 返回）。",
                },
            },
            "required": ["task_id"],
        },
        timeout=10.0,
    )
    async def claude_code_poll(
        self,
        task_id: str = "",
        **_,
    ) -> dict[str, Any]:
        """查询 Claude Code 任务状态。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready

        if not task_id or not task_id.strip():
            return Err(SdkError("task_id 不能为空"))

        if self._task_mgr is None:
            return Err(SdkError("TaskManager not initialized"))

        try:
            result = await self._task_mgr.poll(task_id)
            if "error" in result and result["error"].startswith("Task not found"):
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            self.logger.exception("claude_code_poll failed")
            return Err(SdkError(f"查询任务失败: {e}"))

    @llm_tool(
        name="claude_code_cancel",
        description=(
            "取消正在运行的 Claude Code 异步任务。\n\n"
            "适用场景：\n"
            "- 任务执行时间过长，想提前终止\n"
            "- 提交了错误的任务，需要取消\n"
            "- 需要释放资源\n\n"
            "参数说明：\n"
            "- task_id: 任务 ID（从 claude_code_submit 返回）\n\n"
            "返回：包含 task_id 和 status 的字典。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID（从 claude_code_submit 返回）。",
                },
            },
            "required": ["task_id"],
        },
        timeout=10.0,
    )
    async def claude_code_cancel(
        self,
        task_id: str = "",
        **_,
    ) -> dict[str, Any]:
        """取消 Claude Code 任务。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready

        if not task_id or not task_id.strip():
            return Err(SdkError("task_id 不能为空"))

        if self._task_mgr is None:
            return Err(SdkError("TaskManager not initialized"))

        try:
            result = await self._task_mgr.cancel(task_id)
            if "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            self.logger.exception("claude_code_cancel failed")
            return Err(SdkError(f"取消任务失败: {e}"))

    @llm_tool(
        name="claude_code_check_health",
        description=(
            "检查 Claude Code CLI 是否可用。返回 CLI 路径、版本信息和适配器状态。\n\n"
            "适用场景：\n"
            "- 在调用 claude_code_execute 之前确认环境就绪\n"
            "- 诊断 Claude Code 相关问题\n"
            "- 检查适配器配置是否正确\n\n"
            "返回：包含 cli_available、cli_path、config 等信息的字典。"
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        timeout=10.0,
    )
    async def claude_code_check_health(self, **_) -> dict[str, Any]:
        """检查 Claude CLI 健康状态。"""
        try:
            cli_path = self._config.command or detect_claude_cli()
            cli_available = bool(cli_path)

            # 尝试获取版本（执行 claude --version）
            version = ""
            if cli_available:
                try:
                    import asyncio as _asyncio

                    proc = await _asyncio.create_subprocess_exec(
                        cli_path,
                        "--version",
                        stdout=_asyncio.subprocess.PIPE,
                        stderr=_asyncio.subprocess.PIPE,
                    )
                    try:
                        stdout, _ = await _asyncio.wait_for(
                            proc.communicate(), timeout=5.0
                        )
                        version = stdout.decode("utf-8", errors="replace").strip()
                    except _asyncio.TimeoutError:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                except Exception as e:
                    version = f"(version check failed: {e})"

            return Ok(
                {
                    "cli_available": cli_available,
                    "cli_path": cli_path or "",
                    "version": version,
                    "ready": self._ready,
                    "config": self._config.to_dict(),
                    "sessions_count": len(self._session_mgr._sessions)
                    if self._session_mgr
                    else 0,
                }
            )
        except Exception as e:
            self.logger.exception("claude_code_check_health failed")
            return Err(SdkError(f"健康检查失败: {e}"))

    @llm_tool(
        name="claude_code_list_sessions",
        description=(
            "列出所有 Claude Code 会话记录。会话按最近使用时间降序排列。\n\n"
            "适用场景：\n"
            "- 查看当前有哪些活跃的 Claude Code 会话\n"
            "- 了解每个会话的工作目录、轮次数、最后错误\n"
            "- 决定是否需要清除某个会话\n\n"
            "返回：包含 sessions 列表的字典，每个会话含 session_id、cwd、turn_count 等字段。"
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        timeout=10.0,
    )
    async def claude_code_list_sessions(self, **_) -> dict[str, Any]:
        """列出所有会话。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready

        try:
            assert self._session_mgr is not None
            records = await self._session_mgr.list_all()
            return Ok(
                {
                    "count": len(records),
                    "sessions": [r.to_dict() for r in records],
                }
            )
        except Exception as e:
            self.logger.exception("claude_code_list_sessions failed")
            return Err(SdkError(f"列出会话失败: {e}"))

    @llm_tool(
        name="claude_code_clear_session",
        description=(
            "清除 Claude Code 会话记录。\n\n"
            "适用场景：\n"
            "- 会话上下文混乱，想从干净状态开始\n"
            "- 切换到不同的项目分支后清理旧上下文\n"
            "- 会话报错后强制重置\n\n"
            "参数：\n"
            "- cwd: 要清除的工作目录（绝对路径）。留空则清除所有会话。\n\n"
            "返回：包含 cleared_count 的字典。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "cwd": {
                    "type": "string",
                    "description": "要清除的工作目录（绝对路径）。留空则清除所有会话。",
                },
            },
        },
        timeout=10.0,
    )
    async def claude_code_clear_session(self, cwd: str = "", **_) -> dict[str, Any]:
        """清除会话记录。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready

        try:
            assert self._session_mgr is not None
            target = cwd or None
            count = await self._session_mgr.clear(target)
            self.logger.info("Cleared {} session(s) (cwd={!r})", count, target)
            return Ok(
                {
                    "cleared_count": count,
                    "cwd": target or "(all)",
                }
            )
        except Exception as e:
            self.logger.exception("claude_code_clear_session failed")
            return Err(SdkError(f"清除会话失败: {e}"))

    @llm_tool(
        name="claude_code_get_config",
        description=(
            "获取 Claude Code 适配器的当前配置。\n\n"
            "适用场景：\n"
            "- 了解默认模型、超时、工作目录等设置\n"
            "- 诊断配置问题\n"
            "- 在调用 claude_code_execute 前确认参数默认值\n\n"
            "返回：包含完整适配器配置的字典。"
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        timeout=5.0,
    )
    async def claude_code_get_config(self, **_) -> dict[str, Any]:
        """获取适配器配置。"""
        try:
            return Ok(
                {
                    "config": self._config.to_dict(),
                    "ready": self._ready,
                    "default_cwd": self._config.cwd or os.getcwd(),
                }
            )
        except Exception as e:
            return Err(SdkError(f"获取配置失败: {e}"))

    # ==================================================================
    # 插件入口（供 UI / 其他插件调用，非 LLM 工具）
    # ==================================================================

    @plugin_entry(
        id="submit_task",
        name="提交 Claude Code 异步任务",
        description="提交 Claude Code 任务到后台异步执行（供 UI/其他插件调用）。",
        llm_result_fields=["task_id", "status"],
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "任务描述"},
                "cwd": {"type": "string", "description": "工作目录"},
                "model": {"type": "string", "description": "模型 ID"},
                "effort": {"type": "string", "description": "推理努力级别"},
                "max_turns": {"type": "integer", "description": "最大轮次"},
            },
            "required": ["prompt"],
        },
    )
    async def submit_task_entry(
        self,
        prompt: str = "",
        cwd: str = "",
        model: str = "",
        effort: str = "",
        max_turns: int = 0,
        **_,
    ) -> Any:
        """插件入口（提交异步任务，供 UI/其他插件调用）。"""
        not_ready = self._ensure_ready()
        if not_ready is not None:
            return not_ready

        if not prompt or not prompt.strip():
            return Err(SdkError("prompt 不能为空"))

        if self._task_mgr is None:
            return Err(SdkError("TaskManager not initialized"))

        try:
            record = await self._task_mgr.submit(
                prompt=prompt,
                cwd=cwd,
                model=model,
                effort=effort,
                max_turns=max_turns,
            )
            return Ok({
                "task_id": record.task_id,
                "status": record.status.value,
            })
        except Exception as e:
            self.logger.exception("submit_task_entry failed")
            return Err(SdkError(f"提交任务失败: {e}"))


__all__ = ["ClaudeCodeAdapterPlugin"]
