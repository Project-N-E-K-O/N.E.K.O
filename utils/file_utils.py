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
    """Non-ASCII and in the Other Letter (CJK/etc.) or Other Symbol (emoji) category."""
    if ord(c) <= 127:
        return False
    return unicodedata.category(c) in _POLLUTION_UNICODE_CATEGORIES


_ZWJ = '‍'


def _consume_pollution_grapheme(s: str, i: int) -> int:
    """Try to consume one pollution grapheme cluster, returning the end position.

    If ``s[i]`` is a pollution base char (Lo/So), treat it together with subsequent
    combining marks and extenders like ZWJ as one cluster. If a ZWJ is directly followed
    by another pollution base, merge it into the same cluster (emoji compounds like
    ``🧑‍💻`` = PERSON + ZWJ + COMPUTER). Returns i unchanged when not pollution.
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
    handling). Strips only **non-ASCII Letters / emoji** (the hallucination pollution
    sources observed from LLMs); ASCII chars and Unicode numeric symbols / punctuation /
    dashes / fullwidth digits always pass through, avoiding silently corrupting
    half-legitimate value prefixes like `+5`, `.5`, `e3`, `−2` (U+2212), `＋5` (U+FF0B).
    If stripping doesn't help, let json.loads raise JSONDecodeError and take the fallback.

    Best-effort, least destruction: capped at 2 grapheme clusters, increasing from k=1;
    the first k whose lookahead hits a legal value start stops immediately — no greed.
    One cluster = 1 pollution base char + 0+ subsequent combining marks/ZWJ, so
    multi-codepoint emoji like `❤️` (U+2764+U+FE0F) or `🧑‍💻` (with ZWJ) also count as 1 cluster.
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


def _looks_like_structural_close(s: str, pos: int) -> bool:
    """Whether an in-string ``"`` at index ``pos-1`` is a real string terminator.

    Look at the first non-whitespace char from ``pos``:
      - ``:`` / ``}`` / ``]`` / end-of-input → strong structural signal → close.
      - ``,`` → ambiguous (commas appear in prose). Only a real close when the token
        *after* the comma looks like a valid next key/value head (``_VALUE_START_CHARS``,
        which already includes ``"``); otherwise the ``"`` is content.
      - anything else → content quote (not a close).
    """
    n = len(s)
    j = pos
    while j < n and s[j].isspace():
        j += 1
    if j >= n:
        return True  # EOF —— 最后一个字符串的合法收尾
    c = s[j]
    if c in ':}]':
        return True
    if c == ',':
        k = j + 1
        while k < n and s[k].isspace():
            k += 1
        # `,` 后必须紧跟下一个合法 key/value 起始才算真的分隔符；否则偏向当内容
        return k < n and s[k] in _VALUE_START_CHARS
    return False


def _escape_inner_quotes(s: str) -> str:
    """Escape unescaped double-quotes that appear *inside* JSON string values.

    Some fast LLMs (notably Qwen) write Chinese prose with literal English double
    quotes around quoted speech / terms and forget to escape them, e.g.
    ``{"content": "他对我说"晚安"然后走了"}`` —— ``json.loads`` then treats the first
    inner ``"`` as the string terminator and chokes.

    Scan char by char. Inside a string, when an unescaped ``"`` is hit, decide whether
    it terminates the string (``_looks_like_structural_close``) or is stray content; if
    content, rewrite it to ``\\"``. The bias is deliberately toward *escaping*: a wrong
    "this is content" guess merely fails to parse and json.loads raises with full
    context (no regression vs. today), whereas closing too eagerly would yield
    valid-but-wrong JSON —— the silent corruption this module avoids everywhere else.
    This is the last, most aggressive transform in the fallback pipeline; it only runs
    after every cheaper repair has failed.

    Known best-effort limitations (consciously accepted —— this repair targets the
    common Qwen single-/multi-field prose case and trades a little adversarial-corner
    safety for that coverage; only ever runs on input that already fails strict parse):
      1. Comma boundary is fundamentally ambiguous. A legitimate multi-field object like
         ``{"summary": "他说"晚安"了", "reason": "..."}`` is structurally *identical* to
         an adversarial ``{"content": "User wrote "x", "y": "z""}``. Repairing the first
         (needs the comma to close) necessarily mis-splits the second. There is no local
         signal to tell them apart, so the latter is silently mis-repaired.
      2. Adjacent quoted tokens with a missing separator (``["x" "y"]``, ``{"a" "b": 1}``)
         get merged into one string rather than left to fail —— missing *commas/colons*
         are out of scope for an inner-quote repair, but this transform incidentally
         "fixes" them wrongly.
      3. Earlier text transforms (Python-literal / ``{{}}``) run *before* this one
         (they must, and this one must run after quote-normalization + unquoted-key, or
         its lookahead misreads bare keys), so a stray ``True`` / ``{{x}}`` *inside* an
         unescaped-inner-quote string can be rewritten before the string is made whole,
         e.g. ``{"a":"he said "True" today"}`` → ``...said "true"...``.
    """  # noqa: DOCSTRING_CJK
    out: list[str] = []
    i = 0
    n = len(s)
    in_string = False
    escape = False
    while i < n:
        c = s[i]
        if not in_string:
            out.append(c)
            if c == '"':
                in_string = True
            i += 1
            continue
        if escape:
            out.append(c)
            escape = False
            i += 1
            continue
        if c == '\\':
            out.append(c)
            escape = True
            i += 1
            continue
        if c != '"':
            out.append(c)
            i += 1
            continue
        # 字符串内未转义的 `"`：闭合 or 内容？
        if _looks_like_structural_close(s, i + 1):
            out.append(c)
            in_string = False
        else:
            out.append('\\"')
        i += 1
    return ''.join(out)


def _insert_missing_structural_commas(s: str) -> str:
    """Insert a missing comma between two adjacent JSON containers.

    LLMs frequently drop the separator between array / object elements, e.g.
    ``[{"role": "user", ...} {"role": "ai", ...}]`` — ``json.loads`` then raises
    ``Expecting ',' delimiter`` at the second ``{``. This was the single most
    common unhandled failure observed in the memory-review (``correction`` model)
    path, whose output is exactly an array of ``{role, content}`` objects.

    Stateful scan (string + backslash aware so braces inside string values are
    ignored). Whenever a structural close ``}`` / ``]`` is immediately followed —
    only whitespace between — by a structural open ``{`` / ``[`` outside any
    string, insert a ``,`` between them.

    Deliberately scoped to the **close → open** boundary only: ``}{`` / ``}[`` /
    ``]{`` / ``][`` are never valid JSON, so inserting a comma there cannot
    silently corrupt a legitimate document (on valid input this is a no-op). The
    riskier siblings — a missing comma after a *string* value (``"a" "b"``, where
    the leading ``"`` might be an inner content quote rather than a real
    terminator) — are left to fail loudly, consistent with this module's bias
    against silent corruption.
    """
    out: list[str] = []
    i = 0
    n = len(s)
    in_string = False
    escape = False
    while i < n:
        c = s[i]
        out.append(c)
        if in_string:
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
            i += 1
            continue
        if c in '}]':
            # 向前看：跳过空白后若紧跟另一个容器起始（{ 或 [）→ 缺分隔逗号
            j = i + 1
            while j < n and s[j].isspace():
                j += 1
            if j < n and s[j] in '{[':
                out.append(',')
        i += 1
    return ''.join(out)


def _try_json_loads(s: str) -> tuple[Any, bool]:
    try:
        return json.loads(s), True
    except json.JSONDecodeError:
        return None, False


def _apply_outside_strings(s: str, transform: Callable[[str], str]) -> str:
    """Run ``transform`` only on text outside of quoted strings.

    Both ``'...'`` and ``"..."`` are recognized as string boundaries (LLMs often emit
    Python-repr style mixed quotes). Backslash inside strings escapes the next char.
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

    Segment-aware: one scan slices by ``'`` / ``"`` boundaries, rewriting only ``'...'``
    segments into ``"..."``, unescaping inner ``\\'`` and escaping bare ``"``. Segments
    that are already double-quoted strings are left byte-for-byte untouched.
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


# 故障指纹：1+ 个字面量换行类 escape + 一个 `---` 分隔符行 + 1+ 个字面量
# 换行类 escape。匹配到此处时，把这一段 over-escape 的 divider 区域替换成
# 规范 `\n\n---\n\n`——只动 divider 本身，**不碰**字符串里其它地方的字面量
# escape。这样即使同字段里同时存在合法的 ``C:\new_folder`` / regex / 代码
# 片段，它们的 ``\n`` / ``\t`` 字面量也不会被误改。
_OVERESCAPED_DIVIDER_RE = re.compile(
    r'(?:\\r\\n|\\r|\\n)+[ \t]*-{3,}[ \t]*(?:\\r\\n|\\r|\\n)+'
)


def _normalize_overescaped_newlines(obj: Any) -> Any:
    """When the LLM escapes ``\\n`` once more in the JSON source, the parsed string holds a
    literal backslash-n (2 chars) instead of a real newline. This replaces only the
    **over-escaped ``---`` divider regions** with a canonical ``\\n\\n---\\n\\n`` —
    literal escapes elsewhere in the same string (Windows paths, regex, code snippets,
    tool args, etc.) are left untouched.

    Trade-off: if the body / older segments contain further literal paragraph dividers,
    this function leaves them alone — keeping literals is safer than silently rewriting
    legitimate data; at worst the UI shows a few literal ``\\n``.
    """
    if isinstance(obj, str):
        return _OVERESCAPED_DIVIDER_RE.sub('\n\n---\n\n', obj)
    if isinstance(obj, dict):
        return {k: _normalize_overescaped_newlines(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_overescaped_newlines(v) for v in obj]
    return obj


def robust_json_loads(raw: str) -> Any:
    """json.loads with fallback for common LLM JSON quirks.

    If the raw input parses directly, the original result is returned unconditionally.
    Otherwise patch step by step along the fallback pipeline — try parsing right after
    each transform and stop as soon as it parses, so later steps (especially the
    scanner) never touch the text unnecessarily.

    All "pure text replacement" transforms (Python literals, `{{}}`, trailing commas,
    unquoted keys) are wrapped by ``_apply_outside_strings`` and only apply outside
    strings, avoiding silently corrupting string values (like ``"True"`` / ``"x,]"``).

    After a successful parse, a ``_normalize_overescaped_newlines`` post-pass also runs:
    when a string value carries the "over-escaped ``---`` divider fingerprint" — i.e. 1+
    literal newline-ish escapes (``\\n`` / ``\\r\\n`` / ``\\r``) hugging a ``---`` line —
    that region is replaced with a canonical ``\\n\\n---\\n\\n``. Literal escapes
    elsewhere in the same string (Windows paths, regex, code snippets, etc.) are left
    untouched.

    Handles: unquoted keys, trailing commas, ``{{ }}``, Python ``True/False/None``,
    single-quoted strings (including mixed-quote scenarios), stray hallucinated
    chars between structural tokens (e.g. ``,결{`` → ``,{``), missing commas
    between adjacent containers (e.g. ``}{`` → ``},{``), unescaped English
    double-quotes inside string values (e.g. ``"他说"晚安"走了"``), and over-escaped
    ``---`` memo dividers in string values.
    """  # noqa: DOCSTRING_CJK
    parsed, ok = _try_json_loads(raw)
    if ok:
        return _normalize_overescaped_newlines(parsed)

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
        # 清掉 `,결{` 类结构 token 间幻觉污染；自身已双引号感知
        _strip_stray_chars_between_tokens,
        # 转义字符串值内未转义的英文双引号（qwen 等模型常犯）
        _escape_inner_quotes,
        # 容器元素间缺逗号 `}{`→`},{`；自身串感知，仅补结构 close→open（零歧义）。
        # 必须排在 _escape_inner_quotes **之后**：未转义内引号会翻转串解析奇偶，
        # 让内容里的字面 `}{` 被误判为结构边界而插入逗号（静默篡改）。先把内引号
        # 转义干净，串边界才稳，本步的 close→open 判定才可信（Codex P2）。
        _insert_missing_structural_commas,
    )
    s = raw
    for transform in transforms:
        s = transform(s)
        parsed, ok = _try_json_loads(s)
        if ok:
            return _normalize_overescaped_newlines(parsed)
    return _normalize_overescaped_newlines(json.loads(s))  # 让最终错误带完整上下文抛出


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
