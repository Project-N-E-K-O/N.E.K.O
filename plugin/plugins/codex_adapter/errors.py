"""Codex Adapter — 错误分类与处理。

参考 Paperclip `codex-local` 适配器的错误分类策略
（packages/adapters/codex-local/src/server/parse.ts），
将 Codex CLI 的各种失败归类为可被适配器自动处理的类别。

错误分类：
- auth_required: 需要登录（Codex CLI 未认证 / auth.json 缺失）
- unknown_session: 会话 thread_id 不存在或已过期（可重试）
- transient_upstream: 上游服务临时不可用（rate limit / high demand / remote compaction）
- usage_limit: 用量限制（可重试，有 retry_not_before 时间）
- cli_not_found: Codex CLI 未安装或不在 PATH
- timeout: 执行超时
- aborted: 用户/系统主动中止
- unknown: 未分类错误
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


# 错误分类常量
AUTH_REQUIRED = "auth_required"
UNKNOWN_SESSION = "unknown_session"
TRANSIENT_UPSTREAM = "transient_upstream"
USAGE_LIMIT = "usage_limit"
CLI_NOT_FOUND = "cli_not_found"
TIMEOUT = "timeout"
ABORTED = "aborted"
UNKNOWN = "unknown"


# 可重试的错误类别（自动新建会话重试）
RETRYABLE_ERRORS = frozenset(
    {
        UNKNOWN_SESSION,
        TRANSIENT_UPSTREAM,
        USAGE_LIMIT,
    }
)


# ---------------------------------------------------------------------------
# 错误模式匹配（基于 Paperclip codex-local/src/server/parse.ts）
# ---------------------------------------------------------------------------

# 未知会话错误（来自 paperclip isCodexUnknownSessionError）
_UNKNOWN_SESSION_RE = re.compile(
    r"unknown\s+(?:session|thread)|"
    r"session\s+.*\s+not\s+found|"
    r"thread\s+.*\s+not\s+found|"
    r"conversation\s+.*\s+not\s+found|"
    r"missing\s+rollout\s+path\s+for\s+thread|"
    r"state\s+db\s+missing\s+rollout\s+path|"
    r"state\s+db\s+returned\s+stale\s+rollout\s+path|"
    r"no\s+rollout\s+found\s+for\s+thread\s+id",
    re.IGNORECASE,
)

# 瞬态上游错误（来自 paperclip CODEX_TRANSIENT_UPSTREAM_RE）
_TRANSIENT_UPSTREAM_RE = re.compile(
    r"(?:we(?:'|’)re\s+currently\s+experiencing\s+high\s+demand|"
    r"temporary\s+errors|"
    r"rate[-\s]?limit(?:ed)?|"
    r"too\s+many\s+requests|"
    r"\b429\b|"
    r"server\s+overloaded|"
    r"service\s+unavailable|"
    r"try\s+again\s+later)",
    re.IGNORECASE,
)

# 远程压缩任务（来自 paperclip CODEX_REMOTE_COMPACTION_RE）
_REMOTE_COMPACTION_RE = re.compile(r"remote\s+compact\s+task", re.IGNORECASE)

# 用量限制（来自 paperclip CODEX_USAGE_LIMIT_RE）
# 捕获重试时间，如 "5:00 p.m. EDT" 或 "5:00 p.m. (EDT)"
_USAGE_LIMIT_RE = re.compile(
    r"you(?:'|’)ve\s+hit\s+your\s+usage\s+limit\s+for\s+.+\.\s+"
    r"switch\s+to\s+another\s+model\s+now,\s*"
    r"or\s+try\s+again\s+at\s+([^.!\n]+)(?:[.!]|\n|$)",
    re.IGNORECASE,
)

# 认证错误
_AUTH_PATTERNS = [
    re.compile(r"not\s+logged\s+in", re.IGNORECASE),
    re.compile(r"authentication\s+required", re.IGNORECASE),
    re.compile(r"invalid\s+api\s+key", re.IGNORECASE),
    re.compile(r"please\s+run\s+.*codex\s+login", re.IGNORECASE),
    re.compile(r"401\s+unauthorized", re.IGNORECASE),
    re.compile(r"OPENAI_API_KEY\s+is\s+(?:not\s+set|missing|required)", re.IGNORECASE),
    re.compile(r"auth\.json\s+(?:not\s+found|missing)", re.IGNORECASE),
]


@dataclass
class ClassifiedError:
    """分类后的错误。"""

    kind: str
    """错误分类常量。"""

    message: str
    """原始错误消息。"""

    retryable: bool
    """是否可重试（自动新建会话重试）。"""

    retry_not_before: str = ""
    """瞬态错误的重试时间（ISO 格式字符串，空表示无限制）。"""

    raw: dict[str, Any] | None = None
    """原始 payload（如果有）。"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "retryable": self.retryable,
            "retry_not_before": self.retry_not_before,
        }


def _build_haystack(*parts: str) -> str:
    """合并多个文本片段为搜索用的 haystack。"""
    return "\n".join(p for p in parts if p)


def is_unknown_session_error(stdout: str, stderr: str) -> bool:
    """检测是否为未知会话错误（来自 paperclip isCodexUnknownSessionError）。"""
    haystack = _build_haystack(stdout, stderr)
    return bool(_UNKNOWN_SESSION_RE.search(haystack))


def extract_retry_not_before(
    stdout: str,
    stderr: str,
    error_message: str,
    now: datetime | None = None,
) -> str:
    """提取用量限制的重试时间（来自 paperclip extractCodexRetryNotBefore）。

    返回 ISO 格式字符串，空字符串表示未找到。
    """
    haystack = _build_haystack(error_message, stdout, stderr)
    match = _USAGE_LIMIT_RE.search(haystack)
    if not match:
        return ""

    clock_text = (match.group(1) or "").strip()
    retry_dt = _parse_local_clock_time(clock_text, now or datetime.now())
    return retry_dt.isoformat() if retry_dt else ""


def _parse_local_clock_time(clock_text: str, now: datetime) -> datetime | None:
    """解析时间文本（如 '5:00 p.m. EDT' 或 '5:00 p.m. (EDT)'）。

    简化版实现：不处理时区（paperclip 的完整实现使用 Intl.DateTimeFormat
    做时区转换，Python 标准库做不了，这里只解析本地时间）。
    """
    if not clock_text:
        return None

    # 匹配 "5:00 p.m." / "5 p.m." / "17:00" 等格式
    match = re.match(
        r"^(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?",
        clock_text,
        re.IGNORECASE,
    )
    if not match:
        # 尝试 24 小时制
        match_24 = re.match(r"^(\d{1,2}):(\d{2})", clock_text)
        if not match_24:
            return None
        hour = int(match_24.group(1))
        minute = int(match_24.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
    else:
        hour = int(match.group(1))
        minute = int(match.group(2) or "0")
        period = (match.group(3) or "").lower()

        if not (1 <= hour <= 12 and 0 <= minute <= 59):
            return None

        # 转换为 24 小时制
        if period == "p" and hour != 12:
            hour += 12
        elif period == "a" and hour == 12:
            hour = 0

    retry_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if retry_time <= now:
        retry_time += timedelta(days=1)
    return retry_time


def is_transient_upstream_error(
    stdout: str,
    stderr: str,
    error_message: str,
) -> bool:
    """检测是否为瞬态上游错误（来自 paperclip isCodexTransientUpstreamError）。

    判定逻辑：
    1. 有用量限制重试时间 → 一定是瞬态错误
    2. 匹配基础瞬态模式（rate limit / 429 / high demand / ...）
    3. 且是 remote compaction 或 high demand 类型
    """
    # 有用量限制重试时间 → 一定是瞬态错误
    if extract_retry_not_before(stdout, stderr, error_message):
        return True

    haystack = _build_haystack(error_message, stdout, stderr)
    if not _TRANSIENT_UPSTREAM_RE.search(haystack):
        return False

    # 必须是 remote compaction 或 high demand / temporary errors
    return bool(
        _REMOTE_COMPACTION_RE.search(haystack)
        or re.search(r"high\s+demand|temporary\s+errors", haystack, re.IGNORECASE)
    )


def classify_error(
    message: str,
    *,
    stdout: str = "",
    stderr: str = "",
    return_code: int | None = None,
    raw: dict[str, Any] | None = None,
) -> ClassifiedError:
    """将错误消息分类。

    Parameters
    ----------
    message:
        错误消息文本（通常是 stderr 或 error 事件的 message 字段）。
    stdout:
        子进程 stdout（用于检测未知会话和瞬态错误）。
    stderr:
        子进程 stderr。
    return_code:
        子进程返回码（可选）。
    raw:
        原始事件 payload（可选）。
    """
    text = message or ""
    haystack = _build_haystack(text, stdout, stderr)

    # CLI 未找到（返回码 127 或特定消息）
    if return_code == 127 or re.search(r"command\s+not\s+found|no\s+such\s+file", haystack, re.IGNORECASE):
        return ClassifiedError(
            kind=CLI_NOT_FOUND,
            message=text or "codex CLI not found",
            retryable=False,
            raw=raw,
        )

    # 超时（返回码 -1 通常表示被 kill）
    if return_code == -1 or re.search(r"timed?\s*out", haystack, re.IGNORECASE):
        return ClassifiedError(
            kind=TIMEOUT,
            message=text or "execution timed out",
            retryable=False,
            raw=raw,
        )

    # 认证错误
    if any(p.search(haystack) for p in _AUTH_PATTERNS):
        return ClassifiedError(
            kind=AUTH_REQUIRED,
            message=text,
            retryable=False,
            raw=raw,
        )

    # 未知会话错误（可重试）
    if is_unknown_session_error(stdout, stderr):
        return ClassifiedError(
            kind=UNKNOWN_SESSION,
            message=text or "unknown session/thread",
            retryable=True,
            raw=raw,
        )

    # 瞬态上游错误（可重试）
    if is_transient_upstream_error(stdout, stderr, text):
        retry_not_before = extract_retry_not_before(stdout, stderr, text)
        return ClassifiedError(
            kind=TRANSIENT_UPSTREAM,
            message=text,
            retryable=True,
            retry_not_before=retry_not_before,
            raw=raw,
        )

    # 用量限制（可重试，有 retry_not_before）
    retry_not_before = extract_retry_not_before(stdout, stderr, text)
    if retry_not_before:
        return ClassifiedError(
            kind=USAGE_LIMIT,
            message=text,
            retryable=True,
            retry_not_before=retry_not_before,
            raw=raw,
        )

    # 未分类
    return ClassifiedError(
        kind=UNKNOWN,
        message=text or f"unknown error (rc={return_code})",
        retryable=False,
        raw=raw,
    )


def is_retryable(kind: str) -> bool:
    """判断错误类别是否可重试。"""
    return kind in RETRYABLE_ERRORS


__all__ = [
    # 常量
    "AUTH_REQUIRED",
    "UNKNOWN_SESSION",
    "TRANSIENT_UPSTREAM",
    "USAGE_LIMIT",
    "CLI_NOT_FOUND",
    "TIMEOUT",
    "ABORTED",
    "UNKNOWN",
    "RETRYABLE_ERRORS",
    # 类
    "ClassifiedError",
    # 函数
    "classify_error",
    "is_retryable",
    "is_unknown_session_error",
    "is_transient_upstream_error",
    "extract_retry_not_before",
]
