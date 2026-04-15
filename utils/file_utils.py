from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

# ── LLM JSON tolerance ─────────────────────────��────────────────────────
# LLM 经常返回带有格式瑕疵的 JSON（无引号 key、尾逗号、Python 字面值等）。
# 先尝试标准解析，失败后逐步修补再试。
_UNQUOTED_KEY_RE = re.compile(r'(?<=[{,])\s*([A-Za-z_]\w*)\s*:')


def robust_json_loads(raw: str) -> Any:
    """json.loads with fallback for common LLM JSON quirks.

    Handles: unquoted keys, trailing commas, ``{{ }}``, Python ``True/False/None``,
    single-quoted strings (including mixed-quote scenarios).
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    s = raw
    # {{ }} → { }  (LLM 模仿 prompt 模板转义)
    s = s.replace("{{", "{").replace("}}", "}")
    # Python 字面值 → JSON
    s = s.replace("True", "true").replace("False", "false").replace("None", "null")
    # 尾逗号
    s = re.sub(r',\s*([}\]])', r'\1', s)
    # 无引号 key:  {key: "v"} → {"key": "v"}
    s = _UNQUOTED_KEY_RE.sub(r' "\1":', s)
    # 单引号 → 双引号
    if '"' not in s:
        s = s.replace("'", '"')
    else:
        # 混合引号：逐步替换单引号 key/value
        s = re.sub(r"'([^']*?)'\s*:", r'"\1":', s)           # key
        s = re.sub(r":\s*'([^']*?)'", r': "\1"', s)         # value
        s = re.sub(r"'\s*([,\]\}])", r'"\1', s)              # 数组尾
        s = re.sub(r"([,\[\{])\s*'", r'\1"', s)              # 数组头
    return json.loads(s)


def atomic_write_text(path: str | os.PathLike[str], content: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace a text file in the same directory."""
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=str(target_path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding=encoding) as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, target_path)
    except Exception:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(
    path: str | os.PathLike[str],
    data: Any,
    *,
    encoding: str = "utf-8",
    ensure_ascii: bool = False,
    indent: int | None = 2,
    **json_kwargs: Any,
) -> None:
    """Serialize JSON and atomically replace the destination file."""
    content = json.dumps(
        data,
        ensure_ascii=ensure_ascii,
        indent=indent,
        **json_kwargs,
    )
    atomic_write_text(path, content, encoding=encoding)
