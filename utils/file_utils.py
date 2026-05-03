from __future__ import annotations

import asyncio
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

# 合法 JSON 值起始字符：`"` (string) / `{` (object) / `[` (array) /
# `-` 或数字 (number) / `t` `f` `n` (true/false/null)。
# `.` 严格 JSON 中不是合法 number 起始 (.5 非法)，但 LLM 偶尔会写出来；
# 算作"疑似数字起始"放进集合，scanner 不主动删它 —— 让 json.loads 自己抛错，
# 避免把 `[1,.5]` 静默清成 `[1,5]` 这种数值 silent corruption。
_VALUE_START_CHARS = frozenset('"{[-.tfn0123456789')


def _strip_stray_chars_between_tokens(s: str) -> str:
    """Strip 1–3 hallucinated chars between `,`/`[` and the next value start.

    Stateful scanner — only acts outside of quoted strings (with backslash escape
    handling) so legitimate string contents containing ``,abc{`` patterns are
    untouched. Lookahead accepts any valid JSON value-start char (not just
    ``{`` / ``[``), so cases like ``[1,결2]`` or ``["a",결"b"]`` also recover.
    """
    out: list[str] = []
    i = 0
    n = len(s)
    in_string = False
    escape = False
    while i < n:
        c = s[i]
        if in_string:
            out.append(c)
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        out.append(c)
        i += 1
        if c not in ',[':
            continue
        # 跳过 separator 后的空白，再看接下来是不是合法值起始
        j = i
        while j < n and s[j].isspace():
            j += 1
        if j >= n or s[j] in _VALUE_START_CHARS:
            continue
        # 1–3 字符内若能落到合法值起始，视作幻觉污染，删掉中间这段
        for k in range(1, 4):
            if j + k < n and s[j + k] in _VALUE_START_CHARS:
                out.append(s[i:j])  # 保留空白
                i = j + k
                break
    return ''.join(out)


def robust_json_loads(raw: str) -> Any:
    """json.loads with fallback for common LLM JSON quirks.

    Handles: unquoted keys, trailing commas, ``{{ }}``, Python ``True/False/None``,
    single-quoted strings (including mixed-quote scenarios), and stray hallucinated
    characters between structural tokens (e.g. ``,결{`` → ``,{``).
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
    # LLM 偶尔在结构 token 之间塞 1–3 个幻觉字符（实例：`,결{` 应是 `,{`）。
    # 用有状态扫描器避免误伤合法 string 内容里出现的同形 pattern。
    s = _strip_stray_chars_between_tokens(s)
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


def read_json(path: str | os.PathLike[str], *, encoding: str = "utf-8") -> Any:
    with open(path, "r", encoding=encoding) as f:
        return json.load(f)


async def atomic_write_text_async(
    path: str | os.PathLike[str],
    content: str,
    *,
    encoding: str = "utf-8",
) -> None:
    await asyncio.to_thread(atomic_write_text, path, content, encoding=encoding)


async def atomic_write_json_async(
    path: str | os.PathLike[str],
    data: Any,
    *,
    encoding: str = "utf-8",
    ensure_ascii: bool = False,
    indent: int | None = 2,
    **json_kwargs: Any,
) -> None:
    await asyncio.to_thread(
        atomic_write_json,
        path,
        data,
        encoding=encoding,
        ensure_ascii=ensure_ascii,
        indent=indent,
        **json_kwargs,
    )


async def read_json_async(
    path: str | os.PathLike[str],
    *,
    encoding: str = "utf-8",
) -> Any:
    return await asyncio.to_thread(read_json, path, encoding=encoding)
