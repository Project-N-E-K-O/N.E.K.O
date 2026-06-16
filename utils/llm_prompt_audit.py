# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""LLM prompt audit log (debug tool).

Purpose: write every complete request body sent to the LLM (messages, model,
max_completion_tokens, etc.) + the tiktoken token count of each message to a local
jsonl, for manual/scripted analysis of whether each component's budget share is
reasonable.

Enabling (either being truthy turns it on):
    1) set config.LLM_PROMPT_AUDIT_ENABLED to True in source (suited for shipping debug builds to users)
    2) set the environment variable NEKO_LLM_PROMPT_AUDIT=1 (suited for temporary use during development)

Output:
    logs/llm_prompt_audit/YYYY-MM-DD.jsonl
    One JSON per line; the messages[*].text field contains the **full original text**
    of text-type parts (untruncated); non-text parts like image/audio/video are
    replaced with an "[<type>]" placeholder so base64 doesn't blow up the log +
    leak user screenshots.

Never enable by default in production — the log contains full prompt text, which is privacy-sensitive data.
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

from config import LLM_PROMPT_AUDIT_ENABLED

_ENABLED = (
    LLM_PROMPT_AUDIT_ENABLED
    or os.environ.get("NEKO_LLM_PROMPT_AUDIT", "").lower() in ("1", "true", "yes")
)
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
    """Flatten message content to plain text for token counting.

    Whitelist strategy: only text-type parts (``text`` / ``input_text`` /
    ``output_text``) land verbatim; every other type is replaced with an
    ``[<type>]`` placeholder.

    Why not a blacklist — this repo actually uses at least 5 shapes of "image part":

    * classic OpenAI: ``{"type": "image_url", "image_url": {...}}``
    * Anthropic style: ``{"type": "image", "source": {"type": "base64", ...}}``
    * new Anthropic: ``{"type": "input_image", ...}``
    * plugin schema: ``{"type": "image", "data": bytes, "mime": str}``
    * our own adapter: ``{"type": "image", "image_url": "..."}``

    Plus ``audio`` / ``video`` / multimodal types possibly added later — any part
    not in the whitelist is treated as potentially containing binary/base64 and
    uniformly replaced with the ``[<type>]`` placeholder. This avoids writing user
    screenshots into the jsonl verbatim, and keeps the function contract "flatten
    to plain text for token counting" self-consistent (binary was never text
    tokens anyway).
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
            else:
                # 见函数 docstring：非 text 类一律占位，不 json.dumps，
                # 不试图细分图片/音频/视频——白名单比黑名单安全。
                out.append(f"[{ptype or 'unknown'}]")
        return "\n".join(out)
    if isinstance(content, dict):
        # 镜像 list 分支：上游偶尔直接传单个 part dict（不是 list 包裹）。
        ptype = content.get("type")
        if ptype in ("text", "input_text", "output_text"):
            return str(content.get("text") or "")
        return f"[{ptype or 'unknown'}]"
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
            f"{_LOG_DIR.resolve()} "
            "(config.LLM_PROMPT_AUDIT_ENABLED or NEKO_LLM_PROMPT_AUDIT=1)",
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

    field_name/field_value: the token-limit field actually written into the request
    body (max_tokens vs max_completion_tokens) and its value.
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
            per_message.append({
                "idx": idx,
                "role": role,
                "tokens": tok,
                "chars": len(text),
                "text": text,
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
