"""Claude Code Adapter — 错误分类与处理。

参考 Paperclip 的错误分类策略，将 Claude CLI 的各种失败
归类为可被适配器自动处理的类别。

错误分类：
- auth_required: 需要登录（Claude CLI 未认证）
- max_turns: 达到最大轮次限制
- poisoned_session: 会话已损坏，需要新会话重试
- unknown_session: 会话 ID 不存在或已过期
- image_error: 图片处理错误（通常是临时性的）
- transient_upstream: 上游服务临时不可用
- cli_not_found: Claude CLI 未安装或不在 PATH
- timeout: 执行超时
- aborted: 用户/系统主动中止
- unknown: 未分类错误
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# 错误分类常量
AUTH_REQUIRED = "auth_required"
MAX_TURNS = "max_turns"
POISONED_SESSION = "poisoned_session"
UNKNOWN_SESSION = "unknown_session"
IMAGE_ERROR = "image_error"
TRANSIENT_UPSTREAM = "transient_upstream"
CLI_NOT_FOUND = "cli_not_found"
TIMEOUT = "timeout"
ABORTED = "aborted"
UNKNOWN = "unknown"


# 可重试的错误类别（自动新建会话重试）
RETRYABLE_ERRORS = frozenset(
    {
        POISONED_SESSION,
        UNKNOWN_SESSION,
        IMAGE_ERROR,
        TRANSIENT_UPSTREAM,
    }
)


# ---------------------------------------------------------------------------
# 错误模式匹配（基于 Paperclip 的实践）
# ---------------------------------------------------------------------------

_AUTH_PATTERNS = [
    re.compile(r"not\s+logged\s+in", re.IGNORECASE),
    re.compile(r"authentication\s+required", re.IGNORECASE),
    re.compile(r"invalid\s+api\s+key", re.IGNORECASE),
    re.compile(r"please\s+run\s+.*claude\s+login", re.IGNORECASE),
    re.compile(r"401\s+unauthorized", re.IGNORECASE),
]

_MAX_TURNS_PATTERNS = [
    re.compile(r"max\s+turns?\s+reached", re.IGNORECASE),
    re.compile(r"maximum\s+turns?\s+exceeded", re.IGNORECASE),
    re.compile(r"turn\s+limit\s+reached", re.IGNORECASE),
]

_POISONED_SESSION_PATTERNS = [
    re.compile(r"session\s+is\s+poisoned", re.IGNORECASE),
    re.compile(r"poisoned\s+session", re.IGNORECASE),
    re.compile(r"session\s+corrupt", re.IGNORECASE),
]

_UNKNOWN_SESSION_PATTERNS = [
    re.compile(r"unknown\s+session", re.IGNORECASE),
    re.compile(r"session\s+not\s+found", re.IGNORECASE),
    re.compile(r"session\s+expired", re.IGNORECASE),
    re.compile(r"invalid\s+session\s+id", re.IGNORECASE),
    re.compile(r"no\s+session\s+with\s+id", re.IGNORECASE),
]

_IMAGE_ERROR_PATTERNS = [
    re.compile(r"image\s+processing\s+error", re.IGNORECASE),
    re.compile(r"failed\s+to\s+process\s+image", re.IGNORECASE),
    re.compile(r"unsupported\s+image\s+format", re.IGNORECASE),
]

_TRANSIENT_UPSTREAM_PATTERNS = [
    re.compile(r"rate\s+limit", re.IGNORECASE),
    re.compile(r"429\s+too\s+many\s+requests", re.IGNORECASE),
    re.compile(r"503\s+service\s+unavailable", re.IGNORECASE),
    re.compile(r"502\s+bad\s+gateway", re.IGNORECASE),
    re.compile(r"connection\s+reset", re.IGNORECASE),
    re.compile(r"upstream\s+error", re.IGNORECASE),
    re.compile(r"overloaded", re.IGNORECASE),
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

    raw: dict[str, Any] | None = None
    """原始 payload（如果有）。"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "retryable": self.retryable,
        }


def _match_any(text: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)


def classify_error(
    message: str,
    *,
    return_code: int | None = None,
    raw: dict[str, Any] | None = None,
) -> ClassifiedError:
    """将错误消息分类。

    Parameters
    ----------
    message:
        错误消息文本（通常是 stderr 或 result 事件的错误字段）。
    return_code:
        子进程返回码（可选）。
    raw:
        原始事件 payload（可选）。
    """
    text = message or ""

    # CLI 未找到（返回码 127 或特定消息）
    if return_code == 127 or _match_any(
        text, [re.compile(r"command\s+not\s+found", re.IGNORECASE)]
    ):
        return ClassifiedError(
            kind=CLI_NOT_FOUND,
            message=text or "claude CLI not found",
            retryable=False,
            raw=raw,
        )

    # 超时（返回码 -1 通常表示被 kill）
    if return_code == -1 or _match_any(
        text, [re.compile(r"timed?\s*out", re.IGNORECASE)]
    ):
        return ClassifiedError(
            kind=TIMEOUT,
            message=text or "execution timed out",
            retryable=False,
            raw=raw,
        )

    # 认证错误
    if _match_any(text, _AUTH_PATTERNS):
        return ClassifiedError(
            kind=AUTH_REQUIRED,
            message=text,
            retryable=False,
            raw=raw,
        )

    # 最大轮次
    if _match_any(text, _MAX_TURNS_PATTERNS):
        return ClassifiedError(
            kind=MAX_TURNS,
            message=text,
            retryable=False,
            raw=raw,
        )

    # 会话相关错误（可重试）
    if _match_any(text, _POISONED_SESSION_PATTERNS):
        return ClassifiedError(
            kind=POISONED_SESSION,
            message=text,
            retryable=True,
            raw=raw,
        )

    if _match_any(text, _UNKNOWN_SESSION_PATTERNS):
        return ClassifiedError(
            kind=UNKNOWN_SESSION,
            message=text,
            retryable=True,
            raw=raw,
        )

    # 图片错误（可重试）
    if _match_any(text, _IMAGE_ERROR_PATTERNS):
        return ClassifiedError(
            kind=IMAGE_ERROR,
            message=text,
            retryable=True,
            raw=raw,
        )

    # 上游临时错误（可重试）
    if _match_any(text, _TRANSIENT_UPSTREAM_PATTERNS):
        return ClassifiedError(
            kind=TRANSIENT_UPSTREAM,
            message=text,
            retryable=True,
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
    "MAX_TURNS",
    "POISONED_SESSION",
    "UNKNOWN_SESSION",
    "IMAGE_ERROR",
    "TRANSIENT_UPSTREAM",
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
]
