"""Claude Code Adapter — 数据模型。

定义适配器运行所需的配置、会话、执行结果等数据结构。
所有结构都是纯 dataclass / pydantic，不依赖 SDK 内部类型，
方便单元测试和未来扩展。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# 适配器配置
# ---------------------------------------------------------------------------


@dataclass
class AdapterConfig:
    """适配器运行时配置。

    字段含义与 Paperclip `claude-local` 适配器对齐，
    并补充 N.E.K.O 插件所需的额外选项。
    """

    command: str = ""
    """Claude CLI 可执行文件路径。空字符串表示自动检测。"""

    model: str = ""
    """默认模型 ID。空字符串表示使用 CLI 默认值。"""

    effort: str = ""
    """推理努力级别："" | "low" | "medium" | "high"。"""

    max_turns_per_run: int = 0
    """单次执行最大轮次。0 = 使用 CLI 默认值。"""

    dangerously_skip_permissions: bool = True
    """跳过权限提示。仅用于受信任的本地开发场景。"""

    timeout_sec: int = 300
    """单次执行超时（秒）。main_server 上限 300s。"""

    cwd: str = ""
    """默认工作目录。空字符串表示使用插件进程 cwd。"""

    append_system_prompt_file: str = ""
    """附加系统提示文件路径。空字符串表示不附加。"""

    skills_dir: str = ""
    """技能目录。空字符串表示不注入 --add-dir。"""

    max_retries: int = 1
    """失败后自动重试新会话的次数。"""

    @classmethod
    def from_config_dict(cls, data: dict[str, Any]) -> "AdapterConfig":
        """从 plugin.toml 的 [claude_code] 节构造配置。

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

        return cls(
            command=_str("command"),
            model=_str("model"),
            effort=_str("effort"),
            max_turns_per_run=_int("max_turns_per_run", 0),
            dangerously_skip_permissions=_bool("dangerously_skip_permissions", True),
            timeout_sec=_int("timeout_sec", 300) or 300,
            cwd=_str("cwd"),
            append_system_prompt_file=_str("append_system_prompt_file"),
            skills_dir=_str("skills_dir"),
            max_retries=_int("max_retries", 1),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "model": self.model,
            "effort": self.effort,
            "max_turns_per_run": self.max_turns_per_run,
            "dangerously_skip_permissions": self.dangerously_skip_permissions,
            "timeout_sec": self.timeout_sec,
            "cwd": self.cwd,
            "append_system_prompt_file": self.append_system_prompt_file,
            "skills_dir": self.skills_dir,
            "max_retries": self.max_retries,
        }


# ---------------------------------------------------------------------------
# 会话记录
# ---------------------------------------------------------------------------


@dataclass
class SessionRecord:
    """单条会话记录。

    用于跨调用恢复 Claude Code 会话。会话 ID 由 Claude CLI
    在首次执行时返回（system/init 事件），后续调用通过
    `--resume <session_id>` 复用上下文。
    """

    session_id: str
    """Claude CLI 分配的会话 UUID。"""

    cwd: str
    """会话绑定的工作目录。恢复时必须匹配。"""

    prompt_signature: str
    """提示包签名（system prompt + skills 目录的哈希）。

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
class AssistantMessage:
    """单条 assistant 消息（来自流式输出）。"""

    text: str = ""
    """消息文本内容。"""

    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    """工具调用列表（如果消息包含 tool_use 块）。"""

    raw: dict[str, Any] = field(default_factory=dict)
    """原始事件 payload，用于调试。"""


@dataclass
class ExecuteResult:
    """一次 Claude Code 执行的完整结果。"""

    session_id: str = ""
    """本次执行使用的会话 ID（可能是新创建或恢复的）。"""

    is_new_session: bool = False
    """是否是新创建的会话（True）还是恢复的旧会话（False）。"""

    messages: list[AssistantMessage] = field(default_factory=list)
    """assistant 消息列表，按时间顺序。"""

    final_text: str = ""
    """最后一条 assistant 消息的文本（便于 LLM 直接消费）。"""

    total_cost_usd: float = 0.0
    """本次执行的总费用（美元）。"""

    duration_ms: int = 0
    """本次执行耗时（毫秒）。"""

    num_turns: int = 0
    """本次执行的轮次数。"""

    raw_result: dict[str, Any] = field(default_factory=dict)
    """result 事件的原始 payload。"""

    error_kind: str = ""
    """错误分类（空字符串表示成功）。参见 errors.py。"""

    error_message: str = ""
    """错误详情。"""

    @property
    def is_error(self) -> bool:
        return bool(self.error_kind)

    def to_llm_payload(self) -> dict[str, Any]:
        """构造返回给 LLM 的精简 payload。

        包含最终文本、会话 ID、费用、轮次等关键信息，
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
            }
        return {
            "output": self.final_text,
            "is_error": False,
            "session_id": self.session_id,
            "is_new_session": self.is_new_session,
            "num_turns": self.num_turns,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "duration_ms": self.duration_ms,
            "message_count": len(self.messages),
        }


# ---------------------------------------------------------------------------
# CLI 参数构建选项
# ---------------------------------------------------------------------------


@dataclass
class CLIInvocation:
    """一次 Claude CLI 调用的完整参数。"""

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
        }


# ---------------------------------------------------------------------------
# 工具调用参数（LLM 可见）
# ---------------------------------------------------------------------------


EffortLevel = Literal["", "low", "medium", "high"]
"""推理努力级别。空字符串表示使用配置默认值。"""
