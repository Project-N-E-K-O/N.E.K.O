"""Claude Code Adapter — Claude CLI 子进程执行器。

负责：
1. 检测 Claude CLI 可执行文件（跨平台，处理 Windows .cmd shim）
2. 构建 CLI 参数列表
3. 启动子进程并通过 stdin 传入 prompt
4. 逐行读取 stdout，交给解析器处理
5. 处理超时和进程终止

参考：
- `neko_claudian/core/providers/claude/runtime/spawn.py` 的跨平台 spawn 工具
- Paperclip `claude-local` 适配器的 CLI 参数构建逻辑
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from typing import Any, Optional

from .errors import (
    CLI_NOT_FOUND,
    ClassifiedError,
    TIMEOUT,
    classify_error,
)
from .models import AdapterConfig, CLIInvocation
from .parser import ClaudeOutputParser, ParsedStream


# ---------------------------------------------------------------------------
# 跨平台 CLI 检测
# ---------------------------------------------------------------------------


def is_windows() -> bool:
    return sys.platform == "win32"


def which_cmd(name: str) -> Optional[str]:
    """跨平台 which。"""
    return shutil.which(name)


def find_windows_shim(name: str) -> Optional[str]:
    """Windows 上 `claude` 命令经常是 .cmd shim。

    `shutil.which('claude')` 在 Windows 上默认返回 .exe 而非 .cmd，
    但用户直接调用 `claude` 时会优先走 .cmd。

    解决方法：显式找 .cmd / .bat 版本。
    """
    if not is_windows():
        return which_cmd(name)
    # 优先 .cmd
    for ext in (".cmd", ".bat", ".exe", ""):
        candidate = which_cmd(name + ext) if ext else which_cmd(name)
        if candidate:
            return candidate
    return None


def detect_claude_cli() -> Optional[str]:
    """寻找 `claude` 可执行文件。

    - POSIX: 直接 which claude
    - Windows: 找 .cmd / .bat / .exe
    """
    if is_windows():
        return find_windows_shim("claude")
    return which_cmd("claude")


# ---------------------------------------------------------------------------
# CLI 参数构建
# ---------------------------------------------------------------------------


def build_cli_invocation(
    config: AdapterConfig,
    *,
    prompt: str,
    resume_session_id: str = "",
    cwd: Optional[str] = None,
    model: str = "",
    effort: str = "",
    max_turns: int = 0,
) -> tuple[CLIInvocation, Optional[ClassifiedError]]:
    """构建一次 Claude CLI 调用。

    Returns
    -------
    invocation:
        CLI 调用参数。如果出错，仍返回一个占位 invocation。
    error:
        如果 CLI 未找到或参数非法，返回错误；否则 None。
    """
    # 1. 解析可执行文件路径
    exe_path = config.command or detect_claude_cli() or ""
    if not exe_path:
        placeholder = CLIInvocation(
            cmd=[],
            cwd=cwd or config.cwd or os.getcwd(),
            stdin_data=prompt.encode("utf-8"),
            timeout=float(config.timeout_sec),
        )
        return placeholder, ClassifiedError(
            kind=CLI_NOT_FOUND,
            message="claude CLI not found in PATH. Install Claude Code or set [claude_code].command.",
            retryable=False,
        )

    # 2. 构建参数列表
    # 参考 Paperclip 的参数顺序：
    #   claude --print - --output-format stream-json --verbose
    #           [--resume <uuid>] [--model <m>] [--effort <e>] [--max-turns <n>]
    #           [--dangerously-skip-permissions]
    #           [--append-system-prompt-file <path>] [--add-dir <dir>]
    cmd: list[str] = [exe_path]
    cmd.extend(["--print", "-"])
    cmd.extend(["--output-format", "stream-json"])
    cmd.extend(["--verbose"])

    if resume_session_id:
        cmd.extend(["--resume", resume_session_id])

    # 模型：调用参数 > 配置默认值
    effective_model = model or config.model
    if effective_model:
        cmd.extend(["--model", effective_model])

    # 推理努力级别
    effective_effort = effort or config.effort
    if effective_effort:
        cmd.extend(["--effort", effective_effort])

    # 最大轮次
    effective_max_turns = max_turns if max_turns > 0 else config.max_turns_per_run
    if effective_max_turns > 0:
        cmd.extend(["--max-turns", str(effective_max_turns)])

    # 跳过权限提示
    if config.dangerously_skip_permissions:
        cmd.append("--dangerously-skip-permissions")

    # 附加系统提示文件
    if config.append_system_prompt_file:
        cmd.extend(["--append-system-prompt-file", config.append_system_prompt_file])

    # 技能目录
    if config.skills_dir:
        cmd.extend(["--add-dir", config.skills_dir])

    # 3. 确定工作目录
    effective_cwd = cwd or config.cwd or os.getcwd()

    # 4. 构建 invocation
    invocation = CLIInvocation(
        cmd=cmd,
        cwd=effective_cwd,
        stdin_data=prompt.encode("utf-8"),
        timeout=float(config.timeout_sec),
    )
    return invocation, None


# ---------------------------------------------------------------------------
# 子进程执行器
# ---------------------------------------------------------------------------


class ClaudeCLIExecutor:
    """Claude CLI 子进程执行器。

    封装 asyncio.create_subprocess_exec，提供：
    - 跨平台 spawn（Windows .cmd shim 由 detect_claude_cli 处理）
    - stdin 写入 prompt
    - stdout 逐行读取并交给解析器
    - stderr 收集（用于错误诊断）
    - 超时处理和进程终止
    """

    def __init__(self, config: AdapterConfig, logger: Any = None) -> None:
        self.config = config
        self.logger = logger

    async def execute(
        self,
        invocation: CLIInvocation,
        parser: ClaudeOutputParser,
    ) -> tuple[ParsedStream, Optional[ClassifiedError]]:
        """执行一次 CLI 调用。

        Parameters
        ----------
        invocation:
            CLI 调用参数（由 build_cli_invocation 构建）。
        parser:
            流式输出解析器。每行 stdout 会被喂给 parser.parse_line。

        Returns
        -------
        stream:
            解析后的完整流。
        error:
            如果执行失败（CLI 未找到、超时、子进程异常退出），
            返回分类后的错误；否则 None。
        """
        if not invocation.cmd:
            return parser.finalize(), ClassifiedError(
                kind=CLI_NOT_FOUND,
                message="claude CLI not found",
                retryable=False,
            )

        if self.logger is not None:
            try:
                self.logger.info(
                    "Claude CLI invoke: cmd={} cwd={} stdin_len={} timeout={}",
                    invocation.cmd,
                    invocation.cwd,
                    len(invocation.stdin_data),
                    invocation.timeout,
                )
            except Exception:
                pass

        # 合并环境变量
        env = os.environ.copy()
        env.update(invocation.env_overrides)

        try:
            proc = await asyncio.create_subprocess_exec(
                *invocation.cmd,
                cwd=invocation.cwd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            return parser.finalize(), ClassifiedError(
                kind=CLI_NOT_FOUND,
                message=f"claude CLI not found: {e}",
                retryable=False,
            )
        except Exception as e:
            return parser.finalize(), classify_error(str(e))

        # 收集 stderr
        stderr_lines: list[str] = []

        async def _read_stderr() -> None:
            if proc.stderr is None:
                return
            while True:
                try:
                    # 使用 wait_for 添加超时，防止无限阻塞
                    line = await asyncio.wait_for(proc.stderr.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    # 检查进程是否还在运行
                    if proc.returncode is not None:
                        # 进程已退出，停止读取
                        break
                    continue
                if not line:
                    break
                stderr_lines.append(
                    line.decode("utf-8", errors="replace").rstrip("\r\n")
                )

        # 读取 stdout 并喂给解析器
        async def _read_stdout() -> None:
            if proc.stdout is None:
                return
            while True:
                try:
                    # 使用 wait_for 添加超时，防止无限阻塞
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    # 检查进程是否还在运行
                    if proc.returncode is not None:
                        # 进程已退出，停止读取
                        break
                    continue
                if not line:
                    break
                try:
                    parser.parse_line(line)
                except Exception as e:
                    if self.logger is not None:
                        try:
                            self.logger.warning("Failed to parse stdout line: {}", e)
                        except Exception:
                            pass

        stderr_task = asyncio.create_task(_read_stderr())
        stdout_task = asyncio.create_task(_read_stdout())

        # 写入 stdin 并关闭
        try:
            if proc.stdin is not None:
                proc.stdin.write(invocation.stdin_data)
                await proc.stdin.drain()
                proc.stdin.close()
        except Exception as e:
            if self.logger is not None:
                try:
                    self.logger.warning("Failed to write stdin: {}", e)
                except Exception:
                    pass

        # 等待进程结束（带超时）
        try:
            return_code = await asyncio.wait_for(
                proc.wait(), timeout=invocation.timeout
            )
        except asyncio.TimeoutError:
            # 超时 — 杀死进程
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            # 主动关闭管道，确保读取任务能结束
            try:
                if proc.stdout:
                    proc.stdout.feed_eof()
            except Exception:
                pass
            try:
                if proc.stderr:
                    proc.stderr.feed_eof()
            except Exception:
                pass
            # 等待读取任务结束
            await _drain_tasks(stdout_task, stderr_task, timeout=5.0)
            stderr_text = "\n".join(stderr_lines)
            return parser.finalize(), ClassifiedError(
                kind=TIMEOUT,
                message=f"execution timed out after {invocation.timeout}s. stderr: {stderr_text[:500]}",
                retryable=False,
            )

        # 进程正常退出后，主动关闭管道，防止子进程持有管道导致读取任务无限阻塞
        try:
            if proc.stdout:
                proc.stdout.feed_eof()
        except Exception:
            pass
        try:
            if proc.stderr:
                proc.stderr.feed_eof()
        except Exception:
            pass

        # 等待读取任务完成（带超时，作为额外保护）
        await _drain_tasks(stdout_task, stderr_task, timeout=5.0)

        stream = parser.finalize()

        # 检查返回码和 result 事件
        if return_code != 0:
            stderr_text = "\n".join(stderr_lines)
            # 如果有 result 事件的 is_error，优先用 result 的信息
            if stream.result and stream.result.is_error:
                err = classify_error(
                    stream.result.result_text or stderr_text,
                    return_code=return_code,
                    raw=stream.result.raw,
                )
                return stream, err
            # 否则用 stderr 分类
            err = classify_error(
                stderr_text or f"process exited with code {return_code}",
                return_code=return_code,
            )
            return stream, err

        # 返回码 0 但 result 事件标记为错误
        if stream.result and stream.result.is_error:
            err = classify_error(
                stream.result.result_text,
                return_code=return_code,
                raw=stream.result.raw,
            )
            return stream, err

        # 成功
        return stream, None


async def _drain_tasks(*tasks: asyncio.Task, timeout: Optional[float] = None) -> None:
    """等待所有任务结束，忽略异常。如果指定 timeout，超时后取消未完成的任务。"""
    for task in tasks:
        try:
            if timeout is not None:
                await asyncio.wait_for(task, timeout=timeout)
            else:
                await task
        except (asyncio.TimeoutError, Exception):
            # 超时或异常时取消任务
            if not task.done():
                task.cancel()


__all__ = [
    "is_windows",
    "which_cmd",
    "find_windows_shim",
    "detect_claude_cli",
    "build_cli_invocation",
    "ClaudeCLIExecutor",
]
