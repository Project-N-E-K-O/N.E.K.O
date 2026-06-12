"""
1:1 ported from claudian/src/providers/claude/runtime/ClaudeQueryOptionsBuilder.ts

构造 Agent SDK 的 `claude` CLI 命令行参数。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .types import PersistentQueryConfig, ChatRuntimeQueryOptions


# ---------------------------------------------------------------------------
# 公共参数序列化辅助
# ---------------------------------------------------------------------------

def _json_or_str(v: Any) -> str:
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def build_query_options(
    config: PersistentQueryConfig,
    *,
    mode: str = "persistent",  # "persistent" | "cold"
) -> Dict[str, Any]:
    """
    把 PersistentQueryConfig 序列化为 CLI 参数。
    与 claudian ClaudeQueryOptionsBuilder.buildPersistentQueryOptions 对齐。

    Returns:
        {"args": [...], "env": {...}, "cwd": "..."}
    """
    args: List[str] = []

    # 输出格式（关键：stream-json 才能流式）
    args.extend([
        "--output-format", "stream-json",
        "--verbose",  # 必须 verbose 才能在 stream-json 下输出 system init
    ])

    # 输入格式
    if mode == "persistent":
        args.extend(["--input-format", "stream-json"])

    # 模型
    if config.model:
        args.extend(["--model", config.model])

    # 工作目录
    cwd = config.cwd or "."

    # 权限模式
    if config.permission_mode:
        args.extend(["--permission-mode", config.permission_mode])

    # allowed / disallowed tools
    if config.allowed_tools:
        args.extend(["--allowedTools", ",".join(config.allowed_tools)])
    if config.disallowed_tools:
        args.extend(["--disallowedTools", ",".join(config.disallowed_tools)])

    # 思考
    if config.max_thinking_tokens is not None:
        args.extend(["--max-thinking-tokens", str(config.max_thinking_tokens)])

    # resume / fork
    if config.resume:
        args.extend(["--resume", config.resume])
    if config.extra.get("fork_session"):
        args.extend(["--fork-session"])

    # MCP servers（JSON 字符串）
    if config.mcp_servers:
        args.extend(["--mcp-config", _json_or_str(config.mcp_servers)])

    # System prompt
    if config.system_prompt:
        args.extend(["--append-system-prompt", config.system_prompt])

    # Effort
    if config.effort:
        args.extend(["--effort", config.effort])

    env: Dict[str, str] = {}
    return {"args": args, "env": env, "cwd": cwd}


def build_cold_start_args(
    prompt: str,
    options: ChatRuntimeQueryOptions,
) -> Dict[str, Any]:
    """
    构造冷启动（一次性 claude -p）参数。
    """
    args: List[str] = ["-p", prompt]
    args.extend(["--output-format", "stream-json", "--verbose"])

    if options.model:
        args.extend(["--model", options.model])
    if options.permission_mode:
        args.extend(["--permission-mode", options.permission_mode])
    if options.cwd:
        pass  # cwd 在 create_subprocess_exec 中指定
    if options.system_prompt:
        args.extend(["--append-system-prompt", options.system_prompt])
    if options.allowed_tools:
        args.extend(["--allowedTools", ",".join(options.allowed_tools)])
    if options.mcp_servers:
        args.extend(["--mcp-config", _json_or_str(options.mcp_servers)])
    if options.resume:
        args.extend(["--resume", options.resume])
    if options.fork_session:
        args.extend(["--fork-session"])
    if options.max_thinking_tokens is not None:
        args.extend(["--max-thinking-tokens", str(options.max_thinking_tokens)])

    return {
        "args": args,
        "env": {},
        "cwd": options.cwd or ".",
    }


# ---------------------------------------------------------------------------
# Context 类型（与 Claudian QueryOptionsContext 对齐）
# ---------------------------------------------------------------------------

class ColdStartQueryContext:
    """冷启动上下文。"""


class PersistentQueryContext:
    """持久查询上下文。"""


class QueryOptionsContext:
    """通用查询选项上下文。"""
