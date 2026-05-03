from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Callable

# ── LLM JSON tolerance ─────────────────────────��────────────────────────
# LLM 经常返回带有格式瑕疵的 JSON（无引号 key、尾逗号、Python 字面值等）。
# 先尝试标准解析，失败后逐步修补再试。
_UNQUOTED_KEY_RE = re.compile(r'(?<=[{,])\s*([A-Za-z_]\w*)\s*:')
# Python 字面量 → JSON。用 word boundary 避免误改 `TrueValue` / `NoneType` 等
# 含字面量子串的标识符（裸 `.replace` 会把 key 名静默篡改成完全不同的字符串）。
_PY_LITERAL_RE = re.compile(r'(?<!\w)(True|False|None)(?!\w)')
_PY_LITERAL_MAP = {'True': 'true', 'False': 'false', 'None': 'null'}

# 合法 JSON 值起始字符：`"` (string) / `{` (object) / `[` (array) /
# `-` 或数字 (number) / `t` `f` `n` (true/false/null)。
_VALUE_START_CHARS = frozenset('"{[-tfn0123456789')

# Unicode 类别白名单 —— 只剥这两类视作幻觉污染 base 字符：
#   Lo: Other Letter，含 CJK / 韩文 / 日文 / 阿拉伯文 等（实测污染源，如 `결`）
#   So: Other Symbol，主要是 emoji
# 故意排除 Sm (Math Symbol，含 `−` U+2212 / `＋` U+FF0B 等)、Pd (Dash)、
# Nd (含全角数字 `０`-`９`、阿拉伯数字 `٠` 等) 等可能是 Unicode 数字前缀的类别 ——
# 删掉它们会把 `[1,−2]` → `[1,2]` 这种 silent numeric corruption。
_POLLUTION_UNICODE_CATEGORIES = frozenset({'Lo', 'So'})

# Combining marks / format chars，附属于前一个 base 字符（grapheme cluster 的一部分）。
# 例：`❤️` = U+2764 (So) + U+FE0F (Mn variation selector)；
#     `🧑‍💻` = U+1F9D1 (So) + U+200D (Cf ZWJ) + U+1F4BB (So)。
_GRAPHEME_EXTEND_CATEGORIES = frozenset({'Mn', 'Me', 'Mc', 'Cf'})


def _is_likely_pollution_char(c: str) -> bool:
    """非 ASCII 且属 Other Letter (CJK/etc.) 或 Other Symbol (emoji) 类别。"""
    if ord(c) <= 127:
        return False
    return unicodedata.category(c) in _POLLUTION_UNICODE_CATEGORIES


_ZWJ = '‍'


def _consume_pollution_grapheme(s: str, i: int) -> int:
    """尝试消费一个污染 grapheme cluster，返回结束位置。

    如果 ``s[i]`` 是 pollution base char (Lo/So)，连同后续 combining marks 与
    ZWJ 等扩展字符一起视作一个 cluster。ZWJ 后若紧跟另一个 pollution base，则
    继续并入同一 cluster（emoji 复合体如 ``🧑‍💻`` = PERSON + ZWJ + COMPUTER）。
    不是 pollution 则返回 i 不变。
    """
    n = len(s)
    if i >= n or not _is_likely_pollution_char(s[i]):
        return i
    end = i + 1
    while True:
        # 吃掉 combining marks / ZWJ / format chars
        while end < n and unicodedata.category(s[end]) in _GRAPHEME_EXTEND_CATEGORIES:
            end += 1
        # ZWJ 后若紧跟新的 pollution base，并入同一 cluster 继续
        if (
            end < n
            and end >= 2
            and s[end - 1] == _ZWJ
            and _is_likely_pollution_char(s[end])
        ):
            end += 1
            continue
        break
    return end


def _strip_stray_chars_between_tokens(s: str) -> str:
    """Strip 1–2 hallucinated grapheme clusters between `,`/`[` and the next value.

    Stateful scanner — only acts outside of quoted strings (with backslash escape
    handling). 仅剥**非 ASCII Letter / emoji**（LLM 实测幻觉污染源）；ASCII 字符
    与 Unicode 数字符号 / 标点 / dash / 全角数字一律放行，避免把
    `+5`、`.5`、`e3`、`−2`（U+2212）、`＋5`（U+FF0B）等半合法值前缀静默改坏。
    剥不掉就让 json.loads 自己抛 JSONDecodeError 走 fallback。

    Best-effort 最少破坏：上限 2 个 grapheme cluster，从 k=1 起递增，第一个能让
    lookahead 命中合法值起始的 k 立刻停 —— 不贪。一个 cluster = 1 个 pollution
    base char + 0 或多个后续 combining marks/ZWJ，所以 `❤️`(U+2764+U+FE0F) 或
    `🧑‍💻`(含 ZWJ) 这类 multi-codepoint emoji 也算 1 cluster。
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
        # 跳过 separator 后的空白，从最少 (k=1 cluster) 开始
        j = i
        while j < n and s[j].isspace():
            j += 1
        cur = j
        for _ in range(2):  # 上限 2 个 grapheme cluster
            nxt = _consume_pollution_grapheme(s, cur)
            if nxt == cur:
                break  # 不是 pollution，再大 k 也只会更糟
            cur = nxt
            # 污染段后允许跟若干空白（pretty-printed 输出常见），
            # 再看下一个非空白字符是不是合法值起始
            m = cur
            while m < n and s[m].isspace():
                m += 1
            if m < n and s[m] in _VALUE_START_CHARS:
                out.append(s[i:j])  # 保留 separator 后的空白
                i = cur  # 跳过污染段；后续空白由主循环正常 append
                break
    return ''.join(out)


def _try_json_loads(s: str) -> tuple[Any, bool]:
    try:
        return json.loads(s), True
    except json.JSONDecodeError:
        return None, False


def _apply_outside_strings(s: str, transform: Callable[[str], str]) -> str:
    """Run ``transform`` only on text outside of quoted strings.

    Both ``'...'`` and ``"..."`` are recognized as string boundaries (LLM 常输出
    Python-repr 风格混合引号). Backslash inside strings escapes the next char.
    Inside-string content is preserved bytewise — protects e.g. the literal value
    ``"True"`` from the Python-literal substitution step.
    """
    out: list[str] = []
    buf: list[str] = []  # outside-string segment buffer
    quote: str | None = None
    escape = False

    def _flush_outside() -> None:
        if buf:
            out.append(transform(''.join(buf)))
            buf.clear()

    for c in s:
        if escape:
            out.append(c)
            escape = False
            continue
        if quote is not None:
            out.append(c)
            if c == '\\':
                escape = True
            elif c == quote:
                quote = None
        else:
            if c in ('"', "'"):
                _flush_outside()
                out.append(c)
                quote = c
            else:
                buf.append(c)
    _flush_outside()
    return ''.join(out)


def _normalize_quotes(s: str) -> str:
    """Convert single-quoted strings to double-quoted; preserve inside content.

    段感知：扫一次按 ``'`` / ``"`` 边界切片，仅把 ``'...'`` 段改成 ``"..."``，
    并对内部出现的 ``\\'`` 解转义、对裸 ``"`` 加转义。已经是双引号字符串的段
    一字不动。
    """
    out: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escape = False
    for c in s:
        if escape:
            current.append(c)
            escape = False
            continue
        if quote is not None:
            if c == '\\':
                current.append(c)
                escape = True
            elif c == quote:
                # 字符串结束
                if quote == "'":
                    inner = ''.join(current)
                    # 解 \' → '，保留 \\ 不动；为目标双引号字符串再转义裸 "
                    inner = re.sub(r"\\'", "'", inner)
                    inner = re.sub(r'(?<!\\)"', r'\\"', inner)
                    out.append('"' + inner + '"')
                else:
                    out.append('"' + ''.join(current) + '"')
                current = []
                quote = None
            else:
                current.append(c)
        else:
            if c in ('"', "'"):
                quote = c
                current = []
            else:
                out.append(c)
    if quote is not None:
        # 未闭合 —— 原样吐出（让 json.loads 自己抛错）
        out.append(quote)
        out.append(''.join(current))
    return ''.join(out)


def robust_json_loads(raw: str) -> Any:
    """json.loads with fallback for common LLM JSON quirks.

    原始输入若能直接 parse，无条件返回原结果（绝不预先 transform）。否则按
    fallback pipeline 逐步修补 —— 每步 transform 后立即 try parse，能 parse
    即停，避免后续步骤（尤其是 scanner）在不必要时动文本。

    所有"纯文本替换" transform（Python 字面量、`{{}}`、尾逗号、无引号 key）
    都通过 ``_apply_outside_strings`` 包装，仅在字符串外生效，避免把字符串值
    （如 ``"True"`` / ``"x,]"``）静默改坏。

    Handles: unquoted keys, trailing commas, ``{{ }}``, Python ``True/False/None``,
    single-quoted strings (including mixed-quote scenarios), and stray hallucinated
    chars between structural tokens (e.g. ``,결{`` → ``,{``).
    """
    parsed, ok = _try_json_loads(raw)
    if ok:
        return parsed

    transforms = (
        # {{ }} → { }  (LLM 模仿 prompt 模板转义)；段感知
        lambda s: _apply_outside_strings(
            s, lambda t: t.replace("{{", "{").replace("}}", "}"),
        ),
        # Python 字面值 → JSON；段感知（避免改字符串内的 "True" 等）+
        # word-boundary regex（避免改 `TrueValue` / `NoneType` 这类标识符）
        lambda s: _apply_outside_strings(
            s,
            lambda t: _PY_LITERAL_RE.sub(lambda m: _PY_LITERAL_MAP[m.group(1)], t),
        ),
        # 尾逗号；段感知
        lambda s: _apply_outside_strings(s, lambda t: re.sub(r',\s*([}\]])', r'\1', t)),
        # 无引号 key:  {key: "v"} → {"key": "v"}；段感知
        lambda s: _apply_outside_strings(s, lambda t: _UNQUOTED_KEY_RE.sub(r' "\1":', t)),
        # 单引号 → 双引号；自身已段感知
        _normalize_quotes,
        # 最后才动：清掉 `,결{` 类结构 token 间幻觉污染；自身已双引号感知
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
