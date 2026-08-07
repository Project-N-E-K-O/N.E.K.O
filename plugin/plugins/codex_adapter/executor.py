"""Codex Adapter — Codex CLI 子进程执行器。

负责：
1. 检测 Codex CLI 可执行文件（跨平台，处理 Windows .cmd shim）
2. 构建 CLI 参数列表（参考 paperclip codex-args.ts）
3. 启动子进程并通过 stdin 传入 prompt
4. 逐行读取 stdout，交给解析器处理
5. 处理超时和进程终止

参考：
- `paperclip/packages/adapters/codex-local/src/server/codex-args.ts` 的参数构建
- `N.E.K.O/plugin/plugins/claude_code_adapter/executor.py` 的子进程管理
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from typing import Any, Optional

from .codex_home import build_codex_home_env, resolve_effective_codex_home
from .errors import (
    CLI_NOT_FOUND,
    ClassifiedError,
    TIMEOUT,
    classify_error,
)
from .models import (
    AdapterConfig,
    CLIInvocation,
    is_fast_mode_supported,
)
from .parser import CodexOutputParser, ParsedCodexStream


# ---------------------------------------------------------------------------
# 跨平台 CLI 检测
# ---------------------------------------------------------------------------


def is_windows() -> bool:
    return sys.platform == "win32"


def which_cmd(name: str) -> Optional[str]:
    """跨平台 which。"""
    return shutil.which(name)


def find_windows_shim(name: str) -> Optional[str]:
    """Windows 上 ``codex`` 命令经常是 .cmd / .bat shim。

    ``shutil.which('codex')`` 在 Windows 上默认返回 .exe 而非 .cmd，
    但用户直接调用 ``codex`` 时会优先走 .cmd。

    解决方法：显式找 .cmd / .bat / .exe 版本。
    """
    if not is_windows():
        return which_cmd(name)
    for ext in (".cmd", ".bat", ".exe", ""):
        candidate = which_cmd(name + ext) if ext else which_cmd(name)
        if candidate:
            return candidate
    return None


def detect_codex_cli() -> Optional[str]:
    """寻找 ``codex`` 可执行文件。

    - POSIX: 直接 which codex
    - Windows: 找 .cmd / .bat / .exe
    """
    if is_windows():
        return find_windows_shim("codex")
    return which_cmd("codex")


# ---------------------------------------------------------------------------
# CLI 参数构建
# ---------------------------------------------------------------------------


def build_cli_invocation(
    config: AdapterConfig,
    *,
    prompt: str,
    resume_thread_id: str = "",
    cwd: Optional[str] = None,
    model: str = "",
    model_reasoning_effort: str = "",
    search: Optional[bool] = None,
    fast_mode: Optional[bool] = None,
    skip_git_repo_check: bool = False,
) -> tuple[CLIInvocation, Optional[ClassifiedError]]:
    """构建一次 Codex CLI 调用。

    参数构建顺序参考 paperclip codex-args.ts 的 ``buildCodexExecArgs``::

        codex exec --json
                   [--skip-git-repo-check]
                   [--search]
                   [--dangerously-bypass-approvals-and-sandbox]
                   [--model <m>]
                   [-c model_reasoning_effort="..."]
                   [-c service_tier="fast"] [-c features.fast_mode=true]
                   [<extra_args>...]
                   [resume <thread_id>] -

    Returns
    -------
    invocation:
        CLI 调用参数。如果出错，仍返回一个占位 invocation。
    error:
        如果 CLI 未找到或参数非法，返回错误；否则 None。
    """
    # 1. 解析可执行文件路径
    exe_path = config.command or detect_codex_cli() or ""
    if not exe_path:
        placeholder = CLIInvocation(
            cmd=[],
            cwd=cwd or config.cwd or os.getcwd(),
            stdin_data=prompt.encode("utf-8"),
            timeout=float(config.timeout_sec),
        )
        return placeholder, ClassifiedError(
            kind=CLI_NOT_FOUND,
            message=(
                "codex CLI not found in PATH. Install OpenAI Codex CLI "
                "or set [codex].command in plugin.toml."
            ),
            retryable=False,
        )

    # 2. 解析调用参数（调用参数 > 配置默认值）
    effective_model = (model or config.model or "").strip()
    effective_effort = (model_reasoning_effort or config.model_reasoning_effort or "").strip()
    effective_search = config.search if search is None else bool(search)
    effective_fast_mode = config.fast_mode if fast_mode is None else bool(fast_mode)

    # 快速模式仅对支持的模型生效（来自 paperclip isCodexLocalFastModeSupported）
    fast_mode_applied = effective_fast_mode and is_fast_mode_supported(effective_model)

    # 3. 构建参数列表（顺序参考 paperclip codex-args.ts）
    cmd: list[str] = [exe_path]

    # --search 在 paperclip 中是 unshift 到最前面（在 exec 之前）
    if effective_search:
        cmd.append("--search")

    cmd.extend(["exec", "--json"])

    if skip_git_repo_check:
        cmd.append("--skip-git-repo-check")

    if config.dangerously_bypass_approvals_and_sandbox:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")

    if effective_model:
        cmd.extend(["--model", effective_model])

    if effective_effort:
        # paperclip: -c model_reasoning_effort=JSON.stringify(value)
        # JSON.stringify("high") = "\"high\"" → 传入 "high"
        cmd.extend(["-c", f"model_reasoning_effort={json.dumps(effective_effort)}"])

    if fast_mode_applied:
        cmd.extend(["-c", 'service_tier="fast"', "-c", "features.fast_mode=true"])

    # 额外参数
    if config.extra_args:
        cmd.extend(config.extra_args)

    # 会话恢复或新会话
    if resume_thread_id:
        cmd.extend(["resume", resume_thread_id, "-"])
    else:
        cmd.append("-")

    # 4. 确定工作目录
    effective_cwd = cwd or config.cwd or os.getcwd()

    # 5. 构建环境变量（CODEX_HOME）
    effective_codex_home = resolve_effective_codex_home(config.codex_home)
    env_overrides = build_codex_home_env(effective_codex_home)

    # 6. 处理 instructions_file_path：读取文件内容并前置到 prompt
    final_prompt = prompt
    instructions_path = (config.instructions_file_path or "").strip()
    if instructions_path:
        try:
            with open(instructions_path, "r", encoding="utf-8") as f:
                instructions_content = f.read()
            # 前置指令文件内容，后接用户 prompt
            final_prompt = f"{instructions_content}\n\n---\n\nUser Task:\n{prompt}"
        except Exception as e:
            # 读取失败时记录日志并降级使用原始 prompt
            import logging
            logging.getLogger(__name__).warning(
                "Failed to read instructions_file_path %s: %s", instructions_path, e
            )

    # 7. 构建 invocation
    invocation = CLIInvocation(
        cmd=cmd,
        cwd=effective_cwd,
        stdin_data=final_prompt.encode("utf-8"),
        timeout=float(config.timeout_sec),
        env_overrides=env_overrides,
    )
    return invocation, None


# ---------------------------------------------------------------------------
# 子进程执行器
# ---------------------------------------------------------------------------


class CodexCLIExecutor:
    """Codex CLI 子进程执行器。

    封装 ``asyncio.create_subprocess_exec``，提供：
    - 跨平台 spawn（Windows .cmd shim 由 ``detect_codex_cli`` 处理）
    - stdin 写入 prompt
    - stdout 逐行读取并交给解析器
    - stderr 收集（用于错误诊断）
    - 超时处理和进程终止

    所有 IO 操作都是异步的，符合 N.E.K.O 的 ruff ASYNC 规则。
    """

    def __init__(self, config: AdapterConfig, logger: Any = None) -> None:
        self.config = config
        self.logger = logger

    async def execute(
        self,
        invocation: CLIInvocation,
        parser: CodexOutputParser,
    ) -> tuple[ParsedCodexStream, Optional[ClassifiedError]]:
        """执行一次 CLI 调用。

        Parameters
        ----------
        invocation:
            CLI 调用参数（由 ``build_cli_invocation`` 构建）。
        parser:
            流式输出解析器。每行 stdout 会被喂给 ``parser.parse_line``。

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
                message="codex CLI not found",
                retryable=False,
            )

        if self.logger is not None:
            try:
                self.logger.info(
                    "Codex CLI invoke: cmd=%s cwd=%s stdin_len=%d timeout=%s",
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
                message=f"codex CLI not found: {e}",
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
                line = await proc.stderr.readline()
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
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    parser.parse_line(line)
                except Exception as e:
                    if self.logger is not None:
                        try:
                            self.logger.warning("Failed to parse stdout line: %s", e)
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
                    self.logger.warning("Failed to write stdin: %s", e)
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
            # 等待读取任务结束
            await _drain_tasks(stdout_task, stderr_task)
            stderr_text = "\n".join(stderr_lines)
            return parser.finalize(), ClassifiedError(
                kind=TIMEOUT,
                message=(
                    f"execution timed out after {invocation.timeout}s. "
                    f"stderr: {stderr_text[:500]}"
                ),
                retryable=False,
            )

        # 等待读取任务完成
        await _drain_tasks(stdout_task, stderr_task)

        stream = parser.finalize()

        # 检查返回码和错误事件
        stderr_text = "\n".join(stderr_lines)

        # 优先使用流中的错误事件（turn.failed / error）
        if stream.is_error:
            error_msg = stream.error_message or stderr_text or (
                f"process exited with code {return_code}"
            )
            err = classify_error(
                error_msg,
                stdout=_stream_to_text(stream),
                stderr=stderr_text,
                return_code=return_code,
            )
            return stream, err

        # 返回码非零但没有错误事件
        if return_code != 0:
            err = classify_error(
                stderr_text or f"process exited with code {return_code}",
                stderr=stderr_text,
                return_code=return_code,
            )
            return stream, err

        # 成功
        return stream, None


def _stream_to_text(stream: ParsedCodexStream) -> str:
    """将解析后的流转换为纯文本（用于错误分类的 haystack）。"""
    parts: list[str] = []
    if stream.thread_started and stream.thread_started.thread_id:
        parts.append(f"thread_id={stream.thread_started.thread_id}")
    for msg in stream.agent_messages:
        if msg.text:
            parts.append(msg.text)
    if stream.error_message:
        parts.append(stream.error_message)
    return "\n".join(parts)


async def _drain_tasks(*tasks: asyncio.Task) -> None:
    """等待所有任务结束，忽略异常。"""
    for task in tasks:
        try:
            await task
        except Exception:
            pass


__all__ = [
    "is_windows",
    "which_cmd",
    "find_windows_shim",
    "detect_codex_cli",
    "build_cli_invocation",
    "CodexCLIExecutor",
]
