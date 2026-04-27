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

import functools
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
        # 镜像 list 分支的类型分流：上游偶尔直接传单个 part dict（不是
        # list 包裹），不能 json.dumps(整个 content)——否则 image_url
        # base64 会原样落盘，既泄露用户截图也撑爆 jsonl。
        ptype = content.get("type")
        if ptype in ("text", "input_text", "output_text"):
            return str(content.get("text") or "")
        if ptype in ("image_url", "input_image"):
            return "[image]"
        try:
            return json.dumps(content, ensure_ascii=False)[:200]
        except Exception:
            return str(content)[:200]
    return str(content) if content is not None else ""


def _safe_count_tokens(text: str) -> int:
    try:
        from utils.tokenize import count_tokens
        return count_tokens(text)
    except Exception:
        # Self-contained fallback when `utils.tokenize` itself fails to
        # import (otherwise count_tokens already has its own heuristic).
        # Mirror tokenize._count_tokens_heuristic 的口径（CJK 1.5 /
        # 其他 0.25，向上取整），不直接 import utils.cjk 以保证此分支
        # 始终可用。
        if not text:
            return 0
        cjk = sum(
            1 for ch in text
            if ("一" <= ch <= "鿿")
            or ("぀" <= ch <= "ヿ")
            or ("가" <= ch <= "힯")
        )
        non_cjk = len(text) - cjk
        # 1.5 * cjk + 0.25 * non_cjk = (6 * cjk + non_cjk) / 4，
        # +3 // 4 等价向上取整。
        return max(1, (cjk * 6 + non_cjk + 3) // 4)


def _safe_call_type() -> str:
    try:
        from utils.token_tracker import _current_call_type  # type: ignore
        return _current_call_type.get() or "unknown"
    except Exception:
        return "unknown"


@functools.cache
def _print_banner_once() -> None:
    """Print the audit-enabled banner exactly once per process. Using
    ``functools.cache`` instead of a module-level boolean sentinel
    sidesteps static-analysis "global variable not used" false positives
    while keeping identical print-once semantics."""
    try:
        print(
            "[LLM_PROMPT_AUDIT] enabled — writing to "
            f"{_LOG_DIR.resolve()} (NEKO_LLM_PROMPT_AUDIT=1)",
            flush=True,
        )
    except Exception:
        # Banner print failures are intentionally ignored: stdout closed /
        # encoding error etc. must not abort the audit record itself, let
        # alone the main LLM call.
        pass


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

    _print_banner_once()

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
        # 审计永远不能影响主流程：jsonl 写盘 / token 计数 / encoding
        # 任意一步失败都吞掉，main LLM call 必须能继续。
        try:
            print(f"[LLM_PROMPT_AUDIT] record failed: {e}", flush=True)
        except Exception:
            # 连 print 都失败（stdout 关闭等极端情况）→ 也吞掉。
            # 这是临时调试模块，丢失一条审计记录不影响任何业务正确性。
            pass
