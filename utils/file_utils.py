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
import threading
import time
import unicodedata
from contextlib import suppress
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


# 崩后残留 tmp 的清扫。mkstemp 与 os.replace 之间被硬杀（taskkill、断电、OOM）会留下
# 一个 tmp：没人读它，但会一直攒在用户的 config / memory 目录里。
#
# 按**目录节流**，不是「每个目录一辈子扫一次」。清理型 sweeper 的正确不变量是「隔一段
# 时间再看一眼」而不是「只看一次」：只看一次的话，本次写自己泄漏的 tmp（清扫跑在
# mkstemp 之前）、扫描当时还太年轻的 tmp、以及扫描本身瞬时失败的目录，都会留到进程
# 退出。换成「上次扫描时刻 + 最小间隔」之后这三类自然都被下一个窗口兜住，泄漏存活
# 时间有上界（≤ 年龄门槛 + 间隔），而且不需要撤记账 / CAS / 重试计数这一串补丁。
# 按目录而不是按目标记账：同一目录下每多一个目标就多扫一遍整个目录（archive_shards
# 是一个目录几百个各自独立的目标，cloudsave 的 bindings/<角色>.json 同理），而且
# 「写完就沉底、再也不会被写第二次」的目标留下的残留按目标记账永远扫不到。
#
# 按目录扫就不能靠目标名认自己的 tmp，而「形状 + mtime」证明不了所有权：别的程序、
# 插件或用户放在同一个目录里的旧文件只要撞上同一个形状就会被永久删掉。所以自己的
# tmp 名里嵌一个所有权标记，只清带标记的文件 —— 可证明的所有权，不是概率论。
# install_source 的 `plugins.lock.json.<pid>.<uuid>.tmp` 是同一个思路。代价：本次改动
# 之前的旧版残留没有标记、永远扫不到；宁可漏清，也不删自己证明不了归属的文件。
#
# 年龄门槛防误删。它不能证明 tmp 没有主人（写者理论上能在 mkstemp 之后被冻结很久），
# 所以还有两道兜底：Windows 上活写者的句柄一直开着，unlink 会被拒（实测
# PermissionError winerror=32）—— 物理上抢不走；POSIX 上 unlink 会成功，但后果是那次
# 写的 os.replace 抛 FileNotFoundError，一个诚实的异常而不是静默损坏。
_TMP_OWNER_TAG = "nkatmp"
# 随机段写成 [^.]+ 而不是 [a-z0-9_]+：有了所有权标记之后它的长度和字符集就不重要了，
# 硬编码字符集等于又依赖回 tempfile._RandomNameSequence 的实现细节 —— CPython 哪天往
# 里加个大写字母，自己产的 tmp 就再也不被认领，而且是静默失效。
_STALE_TMP_RE = re.compile(rf"^\.{_TMP_OWNER_TAG}[^.]+\.tmp$")
_STALE_TMP_MIN_AGE_S = 86400.0
_STALE_TMP_SWEEP_INTERVAL_S = 3600.0
# 记账容量上限：cloudsave 的 staging 每次导出都 mkdtemp 出一批新目录（含 per-character
# 子目录），永久留着会随操作次数无界增长。丢掉一条记账最坏只是多扫一次，所以到顶之后
# 直接丢一半最旧的。
_STALE_TMP_MEMO_MAX = 512
_swept_tmp_dirs: dict[str, float] = {}
_swept_tmp_dirs_lock = threading.Lock()
# tmp 名里**不**嵌目标 basename。原来嵌是为了可诊断，但那是多余的：os.replace 失败时
# OSError 自带 filename+filename2，回显本来就是 `'<tmp>' -> '<目标>'`，目标名一直在。
# 不嵌换来两件事：名字长度变成常量（约 19 字节），比改动前的 `.<basename>.<8>.tmp` 严格
# 更短，于是 ENAMETOOLONG 这一类彻底消失 —— 不需要按目标文件系统探 NAME_MAX（eCryptfs
# 这类只允许 143 字节的文件系统上，固定的字节上限照样会算错）；正则也简单一档。


def _sweep_stale_tmp_if_due(target_path: Path) -> None:
    """Best-effort removal of abandoned temp files in this target's directory. Never fatal."""
    parent = str(target_path.parent)
    now = time.monotonic()
    with _swept_tmp_dirs_lock:
        last = _swept_tmp_dirs.get(parent)
        if last is not None and now - last < _STALE_TMP_SWEEP_INTERVAL_S:
            return
        # 先记账再扫：并发的两个首写只有一个会真扫，另一个直接跳过；扫描失败也照样
        # 记账，下一个间隔到了自然重试，不需要单独的重试计数。
        _swept_tmp_dirs[parent] = now
        if len(_swept_tmp_dirs) > _STALE_TMP_MEMO_MAX:
            oldest = sorted(_swept_tmp_dirs, key=_swept_tmp_dirs.__getitem__)
            for stale in oldest[: _STALE_TMP_MEMO_MAX // 2]:
                _swept_tmp_dirs.pop(stale, None)

    try:
        entries = list(os.scandir(parent))
    except OSError:
        return

    cutoff = time.time() - _STALE_TMP_MIN_AGE_S
    for entry in entries:
        if not _STALE_TMP_RE.match(entry.name):
            continue
        with suppress(OSError):
            if entry.stat().st_mtime < cutoff:
                os.unlink(entry.path)


def _reset_tmp_sweep_state_after_fork() -> None:
    """Re-create the sweep lock in a forked child and drop inherited bookkeeping."""
    # app/main_server/__init__.py:56 选的是 fork 启动方式，而 fork 只复制调用它的那
    # 一个线程：如果别的线程正持着这把锁，子进程继承到的就是一把永远锁着的 mutex，
    # 子进程里任何一次落盘都会死锁。记账也一并清掉——子进程是新进程，本来就该重扫。
    global _swept_tmp_dirs_lock
    _swept_tmp_dirs_lock = threading.Lock()
    _swept_tmp_dirs.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_tmp_sweep_state_after_fork)


# Windows 上 os.replace 会因为「目标此刻正被别的句柄打开」而失败，抛 PermissionError，
# winerror 是 5（ACCESS_DENIED）或 32（SHARING_VIOLATION）。制造这个窗口的不只是杀软扫描
# 和资源管理器预览 —— 本进程自己就够了：落盘常跑在 to_thread 的工作线程里，同一时刻另一
# 个线程（或测试）正 open() 读同一个文件，replace 就被拒。POSIX 的 rename 不受读者影响，
# 这段窗口是 Windows 独有的，而且几乎总是毫秒级：对方句柄一关就没了。
#
# 所以这里退避重试。它不会掩盖真实错误，四条理由：
#   * 判据是 OS 给的错误码，不是消息文本猜测。POSIX 的 OSError 根本没有 winerror 属性，
#     getattr 取到 None，整段在非 Windows 上恒等于「直接抛」。
#   * 别的错误码一次都不重试：磁盘满、路径过长、跨卷、目标是目录，第一次就原样抛出。
#   * 最后一次尝试写在循环外，所以重试用尽后抛出的就是那次真实的 os.replace 异常，带着
#     完整的 filename/filename2，不是一个被包装过的「重试失败」。
#   * 上界是固定的（5 次退避，累计 155ms）。目标要是被永久占着（只读、被别的进程长期
#     持有），行为和改动前完全一样是抛错，只是晚 155ms —— 拿这点延迟换掉绝大多数
#     毫秒级窗口造成的偶发写失败。
#
# 但退避绝不在事件循环上跑。本模块是全仓库共用的落盘原语，而仓库里有二十多处在
# `async def` 里裸调同步的 atomic_write_*（其中 memory/anti_repeat.py 与
# memory/user_directives.py 是 per-turn 的，跟音频同在一条循环上）。在那些地方睡
# 155ms 就是掐音频。而且这不只是量级问题：scripts/check_async_blocking.py 把
# `time.sleep` 明文列进 RISKY_ATTR_PAIRS，本来就禁止它出现在 async 可达路径上 ——
# 该守卫此刻不报，只是因为它文档里写明的 depth-1 限制看不到这里（sleep 在
# atomic_write_text → _replace_with_busy_retry 的深度 2），不是这段代码合规。
#
# 所以有运行中的事件循环时，第一次 busy 就抛：上环调用者拿到的**恰好是改动前的
# 行为**，一个字节的回归都没有。而制造这个 flake 的落盘全部跑在 to_thread 的工作
# 线程里（那里没有 running loop），完整退避原样保留 —— 修复的收益一分不少。
#
# 代价要说清楚：那二十多处上环调用点因此继续吃不到这份保护，撞上占用时照旧丢一次
# 写（它们的调用方多半是 `except Exception: logger.warning`，所以丢得是静默的）。
# 这不该靠原语在循环上偷偷睡来补 —— 正确的收口是把那些调用点改成
# atomic_write_json_async（已存在，仓库里已有 77 处在用），那是独立的一份工作。
_REPLACE_BUSY_WINERRORS = frozenset({5, 32})
_REPLACE_RETRY_BACKOFF_S = (0.005, 0.01, 0.02, 0.04, 0.08)


def running_on_event_loop() -> bool:
    """Whether this thread is currently inside a running asyncio event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _replace_with_busy_retry(temp_path: str, target_path: Path) -> None:
    """Replace the target, briefly retrying Windows' "target is busy" errors."""
    for delay in _REPLACE_RETRY_BACKOFF_S:
        try:
            os.replace(temp_path, target_path)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in _REPLACE_BUSY_WINERRORS:
                raise
            # 只在真的撞上 busy 之后才问「我是不是在循环上」：happy path 一条
            # 指令都不多。
            if running_on_event_loop():
                raise
        time.sleep(delay)
    os.replace(temp_path, target_path)


def atomic_write_text(path: str | os.PathLike[str], content: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace a text file in the same directory."""
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _sweep_stale_tmp_if_due(target_path)

    fd, temp_path = tempfile.mkstemp(
        # 前缀里带所有权标记：清扫器靠它证明这个 tmp 是本模块产的，而不是靠猜形状。
        prefix=f".{_TMP_OWNER_TAG}",
        suffix=".tmp",
        dir=str(target_path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding=encoding) as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        _replace_with_busy_retry(temp_path, target_path)
    except BaseException:
        # BaseException 而不是 Exception：Ctrl-C / SystemExit 落在 write/fsync 上很常见，
        # 只收 Exception 的话 tmp 直接留盘（要等到下一个清扫窗口 + 24h 才清）。
        # install_source/manager.py 的 _atomic_write 用的也是 BaseException，保持对偶。
        # 清理失败不许盖掉真正的失败原因。只吞 FileNotFoundError 时有一个具体的坑：
        # 目标被别的句柄占着（杀软扫描、资源管理器预览）会让 os.replace 抛
        # PermissionError，而紧随其后的 os.remove 往往被同一个原因拒掉，于是调用方
        # 看到的是 remove 的异常、真实原因退到 __context__ 里去了，同时 tmp 还是留盘。
        # 删不掉就成了残留：清扫跑在本次写之前，所以清不了这一个。它会被这个目录的
        # 下一个清扫窗口兜住（这正是把「只扫一次」换成「按间隔节流」的原因之一）。
        with suppress(OSError):
            os.remove(temp_path)
        raise


def atomic_write_bytes(path: str | os.PathLike[str], content: bytes) -> None:
    """Atomically replace a binary file in the same directory."""
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _sweep_stale_tmp_if_due(target_path)

    fd, temp_path = tempfile.mkstemp(
        prefix=f".{_TMP_OWNER_TAG}",
        suffix=".tmp",
        dir=str(target_path.parent),
    )

    try:
        with os.fdopen(fd, "wb") as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        _replace_with_busy_retry(temp_path, target_path)
    except BaseException:
        with suppress(OSError):
            os.remove(temp_path)
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


def read_json_tolerating_replace(
    path: str | os.PathLike[str],
    *,
    encoding: str = "utf-8",
) -> Any:
    """``read_json`` that rides out a concurrent ``os.replace`` on Windows.

    The write side already backs off when the target is busy; this is the same
    window seen from the reader. Without it a caller that swallows exceptions
    turns a transient sharing violation into "the file is unreadable" and falls
    back to defaults, which is far worse than waiting a few milliseconds.

    Only the Windows "target is busy" codes are retried, the budget is the same
    bounded one as the write side, and the final attempt re-raises the real
    error rather than a wrapped one.
    """
    for delay in _REPLACE_RETRY_BACKOFF_S:
        try:
            return read_json(path, encoding=encoding)
        except OSError as exc:
            if getattr(exc, "winerror", None) not in _REPLACE_BUSY_WINERRORS:
                raise
            # 与写入侧同一条铁律：绝不在事件循环上 sleep。读路径尤其容易踩 ——
            # get_workshop_path() 这类同步读就挂在 async handler 上。上环调用者
            # 拿到的是「第一次就抛」，由调用方决定怎么降级（见
            # ConfigManager.load_workshop_config 的 last-known-good 回落）。
            if running_on_event_loop():
                raise
        time.sleep(delay)
    return read_json(path, encoding=encoding)


def read_bytes_tolerating_replace(path: str | os.PathLike[str]) -> bytes:
    """Read one byte snapshot while tolerating a concurrent Windows replace."""

    target_path = Path(path)
    for delay in _REPLACE_RETRY_BACKOFF_S:
        try:
            return target_path.read_bytes()
        except OSError as exc:
            if getattr(exc, "winerror", None) not in _REPLACE_BUSY_WINERRORS:
                raise
            if running_on_event_loop():
                raise
        time.sleep(delay)
    return target_path.read_bytes()


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
