"""TEMPORARY: 临时 LLM prompt 审计日志（测完即删）。

目的：把每一次发给 LLM 的完整请求体（messages、model、max_completion_tokens 等）
+ 各 message 的 tiktoken token 数写到本地 jsonl，配合人工/脚本分析各 component
budget 占比是否合理。

启用方式：
    NEKO_LLM_PROMPT_AUDIT=1 ./run.sh

输出：
    logs/llm_prompt_audit/YYYY-MM-DD.jsonl  （每行一条 JSON）

删除方式：
    1. 删除本文件
    2. utils/llm_client.py 里删除 record_llm_request 调用
    3. 删除 logs/llm_prompt_audit/

不要在生产环境启用。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ENABLED = os.environ.get("NEKO_LLM_PROMPT_AUDIT", "").lower() in ("1", "true", "yes")
_LOG_DIR = Path("logs/llm_prompt_audit")
_LOCK = threading.Lock()
_PRINTED_BANNER = False


def is_enabled() -> bool:
    return _ENABLED


def _ensure_dir() -> Path:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def _today_path() -> Path:
    name = datetime.now().strftime("%Y-%m-%d") + ".jsonl"
    return _ensure_dir() / name


def _content_to_text(content: Any) -> str:
    """Flatten OpenAI message content to plain text for token counting.

    Handles str, list[{"type": "text", "text": "..."}], dict, etc.
    For multimodal image_url parts, omits the base64 (we don't want to count
    image bytes as text tokens).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                out.append(str(part))
                continue
            ptype = part.get("type")
            if ptype in ("text", "input_text", "output_text"):
                out.append(str(part.get("text") or ""))
            elif ptype in ("image_url", "input_image"):
                out.append("[image]")
            else:
                out.append(json.dumps(part, ensure_ascii=False)[:200])
        return "\n".join(out)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return str(content) if content is not None else ""


def _safe_count_tokens(text: str) -> int:
    try:
        from utils.tokenize import count_tokens
        return count_tokens(text)
    except Exception:
        return max(1, len(text) // 4) if text else 0


def _safe_call_type() -> str:
    try:
        from utils.token_tracker import _current_call_type  # type: ignore
        return _current_call_type.get() or "unknown"
    except Exception:
        return "unknown"


def record_llm_request(
    *,
    model: str,
    base_url: str | None,
    params: dict[str, Any],
    field_name: str | None,
    field_value: int | None,
) -> None:
    """Log one LLM request body.

    field_name/field_value: 实际写进请求体的 token 限制字段（max_tokens vs
    max_completion_tokens）以及对应数值。
    """
    if not _ENABLED:
        return

    global _PRINTED_BANNER
    if not _PRINTED_BANNER:
        _PRINTED_BANNER = True
        try:
            print(
                "[LLM_PROMPT_AUDIT] enabled — writing to "
                f"{_LOG_DIR.resolve()} (NEKO_LLM_PROMPT_AUDIT=1)",
                flush=True,
            )
        except Exception:
            pass

    try:
        messages = params.get("messages") or []
        per_message: list[dict[str, Any]] = []
        total = 0
        by_role: dict[str, int] = {}
        for idx, m in enumerate(messages):
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "unknown")
            text = _content_to_text(m.get("content"))
            tok = _safe_count_tokens(text)
            preview = text[:160]
            per_message.append({
                "idx": idx,
                "role": role,
                "tokens": tok,
                "chars": len(text),
                "preview": preview,
            })
            total += tok
            by_role[role] = by_role.get(role, 0) + tok

        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "monotonic_ns": time.monotonic_ns(),
            "call_type": _safe_call_type(),
            "model": model,
            "base_url": base_url,
            "stream": bool(params.get("stream")),
            "limit_field": field_name,
            "limit_value": field_value,
            "tokens_total": total,
            "tokens_by_role": by_role,
            "messages": per_message,
        }
        line = json.dumps(record, ensure_ascii=False)
        with _LOCK:
            with _today_path().open("a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")
    except Exception as e:
        # 审计永远不能影响主流程
        try:
            print(f"[LLM_PROMPT_AUDIT] record failed: {e}", flush=True)
        except Exception:
            pass
