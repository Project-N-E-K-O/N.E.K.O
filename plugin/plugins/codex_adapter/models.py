"""Codex Adapter — 数据模型。

定义适配器运行所需的配置、会话、执行结果等数据结构。
所有结构都是纯 dataclass，不依赖 SDK 内部类型，
方便单元测试和未来扩展。

字段含义与 Paperclip `codex-local` 适配器对齐，
并补充 N.E.K.O 插件所需的额外选项。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# 适配器配置
# ---------------------------------------------------------------------------


# Codex CLI 已知模型列表（来自 paperclip codex-local/src/index.ts）
CODEX_KNOWN_MODELS: tuple[str, ...] = (
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "o3",
    "o3-mini",
    "o4-mini",
    "codex-mini-latest",
)

# 支持快速模式的已知模型（来自 paperclip codex-local/src/index.ts）
CODEX_FAST_MODE_SUPPORTED_MODELS: tuple[str, ...] = ("gpt-5.5", "gpt-5.4")

# 默认模型（来自 paperclip codex-local/src/index.ts）
DEFAULT_CODEX_MODEL = "gpt-5.3-codex"


def is_fast_mode_supported(model: str) -> bool:
    """判断模型是否支持快速模式。

    与 paperclip 的 isCodexLocalFastModeSupported 行为一致：
    - 空模型（让 CLI 选默认值）→ 支持
    - 手动指定的未知模型 ID → 支持（让 CLI 自行拒绝）
    - 已知模型 → 仅 gpt-5.5 / gpt-5.4 支持
    """
    normalized = model.strip() if isinstance(model, str) else ""
    if not normalized:
        return True
    if normalized not in CODEX_KNOWN_MODELS:
        return True
    return normalized in CODEX_FAST_MODE_SUPPORTED_MODELS


@dataclass
class AdapterConfig:
    """适配器运行时配置。

    字段含义与 Paperclip `codex-local` 适配器对齐，
    并补充 N.E.K.O 插件所需的额外选项。
    """

    command: str = ""
    """Codex CLI 可执行文件路径。空字符串表示自动检测。"""

    model: str = ""
    """默认模型 ID。空字符串表示使用 CLI 默认值（通常为 gpt-5.3-codex）。"""

    model_reasoning_effort: str = ""
    """推理努力级别："" | "minimal" | "low" | "medium" | "high" | "xhigh"。"""

    search: bool = False
    """启用 Web 搜索（--search 参数）。"""

    fast_mode: bool = False
    """启用快速模式。仅对 gpt-5.5 / gpt-5.4 及手动模型 ID 生效。"""

    dangerously_bypass_approvals_and_sandbox: bool = True
    """绕过审批和沙箱。仅用于受信任的本地开发场景。"""

    timeout_sec: int = 300
    """单次执行超时（秒）。main_server LLM 工具上限 300s。"""

    cwd: str = ""
    """默认工作目录。空字符串表示使用插件进程 cwd。"""

    instructions_file_path: str = ""
    """指令文件路径（markdown）。内容会前置到 stdin 提示。"""

    codex_home: str = ""
    """CODEX_HOME 目录路径。空字符串表示使用共享 ~/.codex。"""

    openai_api_key: str = ""
    """OPENAI_API_KEY（可选）。设置后写入 CODEX_HOME/auth.json。"""

    max_retries: int = 1
    """失败后自动重试新会话的次数。"""

    extra_args: list[str] = field(default_factory=list)
    """额外 CLI 参数。"""

    @classmethod
    def from_config_dict(cls, data: dict[str, Any]) -> "AdapterConfig":
        """从 plugin.toml 的 [codex] 节构造配置。

        缺失字段使用默认值；类型不匹配时回退到默认值。
        """
        if not isinstance(data, dict):
            return cls()

        def _str(key: str, default: str = "") -> str:
            v = data.get(key, default)
            return v if isinstance(v, str) and v else default

        def _int(key: str, default: int = 0) -> int:
            v = data.get(key, default)
            try:
                return int(v) if v is not None else default
            except (TypeError, ValueError):
                return default

        def _bool(key: str, default: bool = True) -> bool:
            v = data.get(key, default)
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "on")
            return default

        # extra_args 在 toml 中是 JSON 数组字符串
        extra_args_raw = data.get("extra_args", "[]")
        extra_args: list[str] = []
        if isinstance(extra_args_raw, list):
            extra_args = [str(a) for a in extra_args_raw if isinstance(a, (str, int, float))]
        elif isinstance(extra_args_raw, str):
            try:
                parsed = json.loads(extra_args_raw)
                if isinstance(parsed, list):
                    extra_args = [str(a) for a in parsed if isinstance(a, (str, int, float))]
            except (json.JSONDecodeError, TypeError):
                pass

        return cls(
            command=_str("command"),
            model=_str("model"),
            model_reasoning_effort=_str("model_reasoning_effort"),
            search=_bool("search", False),
            fast_mode=_bool("fast_mode", False),
            dangerously_bypass_approvals_and_sandbox=_bool(
                "dangerously_bypass_approvals_and_sandbox", True
            ),
            timeout_sec=_int("timeout_sec", 300) or 300,
            cwd=_str("cwd"),
            instructions_file_path=_str("instructions_file_path"),
            codex_home=_str("codex_home"),
            openai_api_key=_str("openai_api_key"),
            max_retries=_int("max_retries", 1),
            extra_args=extra_args,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "model": self.model,
            "model_reasoning_effort": self.model_reasoning_effort,
            "search": self.search,
            "fast_mode": self.fast_mode,
            "dangerously_bypass_approvals_and_sandbox": self.dangerously_bypass_approvals_and_sandbox,
            "timeout_sec": self.timeout_sec,
            "cwd": self.cwd,
            "instructions_file_path": self.instructions_file_path,
            "codex_home": self.codex_home,
            "openai_api_key": "***" if self.openai_api_key else "",
            "max_retries": self.max_retries,
            "extra_args": list(self.extra_args),
        }


# ---------------------------------------------------------------------------
# 会话记录
# ---------------------------------------------------------------------------


@dataclass
class SessionRecord:
    """单条会话记录。

    用于跨调用恢复 Codex 会话。会话 ID（thread_id）由 Codex CLI
    在首次执行时返回（thread.started 事件），后续调用通过
    `resume <thread_id> -` 复用上下文。
    """

    session_id: str
    """Codex CLI 分配的会话 thread_id。"""

    cwd: str
    """会话绑定的工作目录。恢复时必须匹配。"""

    prompt_signature: str
    """提示包签名（instructions 文件 + codex_home 的哈希）。

    用于检测提示包变化，变化时放弃旧会话。
    """

    created_at: float
    """会话首次创建的 monotonic 时间戳。"""

    last_used_at: float
    """会话最近一次成功使用的时间戳。"""

    turn_count: int = 0
    """会话累计执行的轮次数。"""

    last_error: str = ""
    """最近一次错误分类（空字符串表示无错误）。"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "cwd": self.cwd,
            "prompt_signature": self.prompt_signature,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "turn_count": self.turn_count,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionRecord":
        return cls(
            session_id=str(data.get("session_id", "")),
            cwd=str(data.get("cwd", "")),
            prompt_signature=str(data.get("prompt_signature", "")),
            created_at=float(data.get("created_at", 0.0)),
            last_used_at=float(data.get("last_used_at", 0.0)),
            turn_count=int(data.get("turn_count", 0)),
            last_error=str(data.get("last_error", "")),
        )


# ---------------------------------------------------------------------------
# 执行结果
# ---------------------------------------------------------------------------


@dataclass
class UsageSummary:
    """Token 使用量统计。

    Codex 的 turn.completed 事件可能多次出现，需要累加。
    """

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass
class ExecuteResult:
    """一次 Codex 执行的完整结果。

    与 Claude Code 适配器的 ExecuteResult 对齐，但移除了 cost_usd
    （Codex CLI 不提供成本数据），并增加了 token 使用量。
    """

    session_id: str = ""
    """本次执行使用的会话 thread_id（可能是新创建或恢复的）。"""

    is_new_session: bool = False
    """是否是新创建的会话（True）还是恢复的旧会话（False）。"""

    final_text: str = ""
    """最后一条 agent_message 的文本（便于 LLM 直接消费）。"""

    usage: UsageSummary = field(default_factory=UsageSummary)
    """Token 使用量统计。"""

    duration_ms: int = 0
    """本次执行耗时（毫秒）。"""

    error_kind: str = ""
    """错误分类（空字符串表示成功）。参见 errors.py。"""

    error_message: str = ""
    """错误详情。"""

    retry_not_before: str = ""
    """瞬态错误的重试时间（ISO 格式字符串，空表示无限制）。"""

    raw_events: list[dict[str, Any]] = field(default_factory=list)
    """原始事件列表（用于调试，默认不返回给 LLM）。"""

    @property
    def is_error(self) -> bool:
        return bool(self.error_kind)

    def to_llm_payload(self) -> dict[str, Any]:
        """构造返回给 LLM 的精简 payload。

        包含最终文本、会话 ID、token 使用量等关键信息，
        不包含完整的原始事件流（避免上下文爆炸）。
        """
        if self.is_error:
            return {
                "output": self.final_text or self.error_message,
                "is_error": True,
                "error": self.error_message,
                "error_kind": self.error_kind,
                "session_id": self.session_id,
                "duration_ms": self.duration_ms,
                "retry_not_before": self.retry_not_before,
            }
        return {
            "output": self.final_text,
            "is_error": False,
            "session_id": self.session_id,
            "is_new_session": self.is_new_session,
            "usage": self.usage.to_dict(),
            "duration_ms": self.duration_ms,
        }


# ---------------------------------------------------------------------------
# CLI 参数构建选项
# ---------------------------------------------------------------------------


@dataclass
class CLIInvocation:
    """一次 Codex CLI 调用的完整参数。"""

    cmd: list[str]
    """命令行参数列表（含可执行文件路径）。"""

    cwd: str
    """工作目录。"""

    stdin_data: bytes
    """标准输入数据（prompt）。"""

    timeout: float
    """超时（秒）。"""

    env_overrides: dict[str, str] = field(default_factory=dict)
    """环境变量覆盖。"""

    def to_log_dict(self) -> dict[str, Any]:
        """构造日志友好的字典（不包含 stdin 内容）。"""
        return {
            "cmd": self.cmd,
            "cwd": self.cwd,
            "stdin_len": len(self.stdin_data),
            "timeout": self.timeout,
            "env_keys": list(self.env_overrides.keys()),
        }


# ---------------------------------------------------------------------------
# 工具调用参数（LLM 可见）
# ---------------------------------------------------------------------------


ReasoningEffortLevel = Literal["", "minimal", "low", "medium", "high", "xhigh"]
"""推理努力级别。空字符串表示使用配置默认值。"""


__all__ = [
    "CODEX_KNOWN_MODELS",
    "CODEX_FAST_MODE_SUPPORTED_MODELS",
    "DEFAULT_CODEX_MODEL",
    "is_fast_mode_supported",
    "AdapterConfig",
    "SessionRecord",
    "UsageSummary",
    "ExecuteResult",
    "CLIInvocation",
    "ReasoningEffortLevel",
]
