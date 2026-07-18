"""为单次小剧场回合采集私有模型返回，不接入生产指标或公开投影。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


# ContextVar 会随 asyncio Task 隔离，同一进程中的不同 Session 回合不会共享采集列表。
_ACTIVE_MODEL_RETURN_RECORDS: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "theater_active_model_return_records",
    default=None,
)


@contextmanager
def capture_model_returns() -> Iterator[list[dict[str, Any]]]:
    """建立当前回合的模型返回容器，并在退出时恢复此前异步上下文。"""  # noqa: DOCSTRING_CJK
    records: list[dict[str, Any]] = []
    token = _ACTIVE_MODEL_RETURN_RECORDS.set(records)
    try:
        yield records
    finally:
        _ACTIVE_MODEL_RETURN_RECORDS.reset(token)


def record_model_return(
    *,
    call_type: str,
    surface: str,
    status: str,
    model: str,
    provider_type: str,
    content: Any = "",
    error_type: str = "",
) -> None:
    """把一次模型传输结果追加到当前回合；没有采集上下文时保持无副作用。"""  # noqa: DOCSTRING_CJK
    records = _ACTIVE_MODEL_RETURN_RECORDS.get()
    if records is None:
        return
    # 模型正文按调用入口实际读取的 content 保存；非字符串供应商结构用稳定文本表示，确保 Session 可序列化。
    raw_content = content if isinstance(content, str) else str(content or "")
    records.append(
        {
            "call_index": len(records),
            "call_type": str(call_type or ""),
            "surface": str(surface or ""),
            "status": str(status or ""),
            "model": str(model or ""),
            "provider_type": str(provider_type or ""),
            "content": raw_content,
            "error_type": str(error_type or ""),
            "recorded_at": time.time_ns() // 1_000_000,
        }
    )
