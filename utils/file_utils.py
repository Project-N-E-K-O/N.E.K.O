from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

# ── LLM JSON tolerance ─────────────────────────��────────────────────────
# LLM 经常返回带有格式瑕疵的 JSON（无引号 key、尾逗号、Python 字面值等）。
# 先尝试标准解析，失败后逐步修补再试。
_UNQUOTED_KEY_RE = re.compile(r'(?<=[{,])\s*([A-Za-z_]\w*)\s*:')

# 合法 JSON 值起始字符：`"` (string) / `{` (object) / `[` (array) /
# `-` 或数字 (number) / `t` `f` `n` (true/false/null)。
_VALUE_START_CHARS = frozenset('"{[-tfn0123456789')

# Unicode 类别白名单 —— 只剥这两类视作幻觉污染：
#   Lo: Other Letter，含 CJK / 韩文 / 日文 / 阿拉伯文 等（实测污染源，如 `결`）
#   So: Other Symbol，主要是 emoji
# 故意排除 Sm (Math Symbol，含 `−` U+2212 / `＋` U+FF0B 等)、Pd (Dash)、
# Nd (含全角数字 `０`-`９`、阿拉伯数字 `٠` 等) 等可能是 Unicode 数字前缀的类别 ——
# 删掉它们会把 `[1,−2]` → `[1,2]` 这种 silent numeric corruption。
_POLLUTION_UNICODE_CATEGORIES = frozenset({'Lo', 'So'})


def _is_likely_pollution_char(c: str) -> bool:
    """非 ASCII 且属 Other Letter (CJK/etc.) 或 Other Symbol (emoji) 类别。"""
    if ord(c) <= 127:
        return False
    return unicodedata.category(c) in _POLLUTION_UNICODE_CATEGORIES


def _strip_stray_chars_between_tokens(s: str) -> str:
    """Strip 1–2 hallucinated chars between `,`/`[` and the next value start.

    Stateful scanner — only acts outside of quoted strings (with backslash escape
    handling). 仅剥**非 ASCII Letter / emoji**（LLM 实测幻觉污染源）；ASCII 字符
    与 Unicode 数字符号 / 标点 / dash / 全角数字一律放行，避免把
    `+5`、`.5`、`e3`、`−2`（U+2212）、`＋5`（U+FF0B）等半合法值前缀静默改坏。
    剥不掉就让 json.loads 自己抛 JSONDecodeError 走 fallback。

    Best-effort 最少破坏：从 k=1 起递增到 2，第一个能让 lookahead 命中
    合法值起始的 k 立刻停 —— 不贪。
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
        # 跳过 separator 后的空白，从最少 (k=1) 开始尝试，越少越好
        j = i
        while j < n and s[j].isspace():
            j += 1
        for k in (1, 2):
            if j + k > n:
                break
            # 第 k 个字符必须是 pollution；否则再大的 k 也只会更糟
            if not _is_likely_pollution_char(s[j + k - 1]):
                break
            if j + k < n and s[j + k] in _VALUE_START_CHARS:
                out.append(s[i:j])  # 保留空白
                i = j + k
                break
    return ''.join(out)


def _try_json_loads(s: str) -> tuple[Any, bool]:
    try:
        return json.loads(s), True
    except json.JSONDecodeError:
        return None, False


def robust_json_loads(raw: str) -> Any:
    """json.loads with fallback for common LLM JSON quirks.

    原始输入若能直接 parse，无条件返回原结果（绝不预先 transform）。否则按
    fallback pipeline 逐步修补 —— 每步 transform 后立即 try parse，能 parse
    即停，避免后续步骤（尤其是 scanner）在不必要时动文本。

    Handles: unquoted keys, trailing commas, ``{{ }}``, Python ``True/False/None``,
    single-quoted strings (including mixed-quote scenarios), and stray hallucinated
    chars between structural tokens (e.g. ``,결{`` → ``,{``).
    """
    parsed, ok = _try_json_loads(raw)
    if ok:
        return parsed

    def _normalize_quotes(s: str) -> str:
        if '"' not in s:
            return s.replace("'", '"')
        # 混合引号：逐步替换单引号 key/value
        s = re.sub(r"'([^']*?)'\s*:", r'"\1":', s)           # key
        s = re.sub(r":\s*'([^']*?)'", r': "\1"', s)         # value
        s = re.sub(r"'\s*([,\]\}])", r'"\1', s)              # 数组尾
        s = re.sub(r"([,\[\{])\s*'", r'\1"', s)              # 数组头
        return s

    transforms = (
        # {{ }} → { }  (LLM 模仿 prompt 模板转义)
        lambda s: s.replace("{{", "{").replace("}}", "}"),
        # Python 字面值 → JSON
        lambda s: s.replace("True", "true").replace("False", "false").replace("None", "null"),
        # 尾逗号
        lambda s: re.sub(r',\s*([}\]])', r'\1', s),
        # 无引号 key:  {key: "v"} → {"key": "v"}
        lambda s: _UNQUOTED_KEY_RE.sub(r' "\1":', s),
        # 单引号 → 双引号
        _normalize_quotes,
        # 最后才动：清掉 `,결{` 类结构 token 间幻觉污染
        _strip_stray_chars_between_tokens,
    )
    s = raw
    for transform in transforms:
        s = transform(s)
        parsed, ok = _try_json_loads(s)
        if ok:
            return parsed
    return json.loads(s)  # 让最终错误带完整上下文抛出


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
