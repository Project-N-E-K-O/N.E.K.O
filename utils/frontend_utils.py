# Copyright (c) 2024 Alibaba Inc (authors: Xiang Lyu, Zhihao Du)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
import regex
import os
import logging
import locale
from datetime import datetime

from utils.cjk import (
    CJK_REGEX_CHAR_CLASS,
    count_chinese_chars,
    count_hangul_chars,
    count_kana_chars,
)

# Unicode regexes (compiled once). `regex` package is needed for `\p{L}`
# class — standard `re` only supports the ASCII letter shorthand.
# - _CJK_STRIP: replace any CJK char with a space so subsequent word
#   matching only finds non-CJK letter runs. Range comes from utils.cjk
#   so it stays in sync with is_cjk_char / count_cjk_chars.
# - _NON_CJK_WORD: any maximal run of Unicode "letter" chars in any
#   script (Latin, Cyrillic, Arabic, Greek, Hebrew, Thai, Devanagari, …).
#   Excludes digits/punctuation/spaces by virtue of `\p{L}+`.
_CJK_STRIP = regex.compile(f"[{CJK_REGEX_CHAR_CLASS}]")
_NON_CJK_WORD = regex.compile(r'\p{L}+')


bracket_patterns = [re.compile(r'\(.*?\)'),
                   re.compile('（.*?）')]

# whether contain chinese character
def contains_chinese(text):
    return count_chinese_chars(text) > 0


# replace special symbol
def replace_corner_mark(text):
    text = text.replace('²', '平方')
    text = text.replace('³', '立方')
    return text

def estimate_speech_time(text, unit_duration=0.2):
    # Per-class duration coefficients (heuristic, not corpus-calibrated):
    #   - Chinese hanzi: 1.5 units/char (polysyllabic, slower TTS)
    #   - Japanese kana: 1.0 units/char (mono-syllabic)
    #   - Korean Hangul: 1.0 units/char (one syllable per syllable block)
    #   - Other letter words (Latin, Cyrillic, Arabic, Greek, Hebrew, Thai,
    #     Devanagari, …): 1.5 units/word (rough syllable average for
    #     Romance/Germanic/Slavic prose; Arabic+Hebrew skew shorter but
    #     1.5 is conservative — over-estimating duration is fine for fence
    #     callers, who'd rather cut early than let TTS run long)
    chinese_units = count_chinese_chars(text) * 1.5
    japanese_units = count_kana_chars(text) * 1.0
    korean_units = count_hangul_chars(text) * 1.0

    # Strip CJK first so word matching doesn't collapse CJK runs into a
    # single "word" (the `\p{L}` class includes Han / Kana / Hangul).
    non_cjk_text = _CJK_STRIP.sub(' ', text)
    other_units = len(_NON_CJK_WORD.findall(non_cjk_text)) * 1.5

    total_units = chinese_units + japanese_units + korean_units + other_units
    estimated_seconds = total_units * unit_duration

    return estimated_seconds

# remove meaningless symbol
def remove_bracket(text):
    for p in bracket_patterns:
        text = p.sub('', text)
    text = text.replace('【', '').replace('】', '')
    text = text.replace('《', '').replace('》', '')
    text = text.replace('`', '').replace('`', '')
    text = text.replace("——", " ")
    text = text.replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    return text

def count_words_and_chars(text: str) -> int:
    """
    统计混合文本长度：中文字符计1、英文单词计1
    """
    if not text:
        return 0
    count = count_chinese_chars(text)
    text_without_chinese = re.sub(r'[一-鿿]', ' ', text)
    english_words = [w for w in text_without_chinese.split() if w.strip()]
    count += len(english_words)
    return count



# split paragrah logic：
# 1. per sentence max len token_max_n, min len token_min_n, merge if last sentence len less than merge_len
# 2. cal sentence len according to lang
# 3. split sentence according to punctuation
# 4. 返回（要处理的文本，剩余buffer）
def split_paragraph(text: str, force_process=False, lang="zh", token_min_n=2.5, comma_split=True):
    def calc_utt_length(_text: str):
        return estimate_speech_time(_text)

    if lang == "zh":
        pounc = ['。', '？', '！', '；', '：', '、', '.', '?', '!', ';']
    else:
        pounc = ['.', '?', '!', ';', ':']
    if comma_split:
        pounc.extend(['，', ','])

    st = 0
    utts = []
    for i, c in enumerate(text):
        if c in pounc:
            if len(text[st: i]) > 0:
                utts.append(text[st: i+1])
            if i + 1 < len(text) and text[i + 1] in ['"', '”']:
                tmp = utts.pop(-1)
                utts.append(tmp + text[i + 1])
                st = i + 2
            else:
                st = i + 1

    if len(utts) == 0: # 没有一个标点
        if force_process:
            return text, ""
        else:
            return "", text
    elif calc_utt_length(utts[-1]) > token_min_n: #如果最后一个utt长度达标
        # print(f"💼后端进行切割：|| {''.join(utts)} || {text[st:]}")
        return ''.join(utts), text[st:]
    elif len(utts)==1: #如果长度不达标，但没有其他utt
        if force_process:
            return text, ""
        else:
            return "", text
    else:
        # print(f"💼后端进行切割：|| {''.join(utts[:-1])} || {utts[-1] + text[st:]}")
        return ''.join(utts[:-1]), utts[-1] + text[st:]

# remove blank between chinese character
def replace_blank(text: str):
    """保留两侧都是"非空格 ASCII 字符"的空格，其余 ASCII 空格一律去掉。

    用于处理 Gemini Live output transcript 之类把中文词中间插入空格、
    以及中英交界处的 ASCII 空格场景——这些空格会让 TTS 把中文读断。

    边界字符（i==0 或 i==末尾）没有对应侧的邻居，一律按"非 ASCII/空格"处理
    直接丢弃，避免 Python 负索引或 IndexError。
    """
    n = len(text)
    out_str = []
    for i, c in enumerate(text):
        if c == " ":
            left = text[i - 1] if i > 0 else ""
            right = text[i + 1] if i + 1 < n else ""
            if (left and left.isascii() and left != " "
                    and right and right.isascii() and right != " "):
                out_str.append(c)
        else:
            out_str.append(c)
    return "".join(out_str)


# "Glue-to-adjacent" 字符范围：这些脚本里出现的 ASCII 空格几乎一定是 tokenizer
# artifact（Gemini Live 把中文 token 切开的那种），不是语义分词。
# 刻意不包含：Hangul（韩语用空格分词）、Cyrillic / Arabic / Thai / Devanagari 等。
_CJK_GLUE_RANGES = (
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
)


def _is_cjk_glue_char(c: str) -> bool:
    if not c:
        return False
    cp = ord(c)
    for lo, hi in _CJK_GLUE_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def drop_cjk_boundary_spaces(text: str) -> str:
    """去掉至少一侧是 CJK 汉字 / 日文假名的 ASCII 空格（含连续空格串）。

    专治 Gemini Live 这类 realtime 后端在输出转录里把中文 token 切开
    （"你 好 世 界"）的 artifact。相比 :func:`replace_blank`：

    - 前者只在两侧均为 ASCII 非空格字符时保留空格，会误伤 Korean /
      Cyrillic / Arabic / Thai 等"非 ASCII 但靠空格分词"的脚本
      （"안녕하세요 여러분" → "안녕하세요여러분"）。
    - 本函数只在 CJK 汉字/假名邻接的情况下删空格，其余场景一律保留。

    判邻居时会**跳过连续空格**找到最近的非空格字符，这样
    ``"你好   世界"`` 整段 3 个空格都会被删掉，而不是只删掉最外侧两个。
    """
    n = len(text)
    out = []
    for i, c in enumerate(text):
        if c == " ":
            # 向左跳过连续空格找最近非空格
            j = i - 1
            while j >= 0 and text[j] == " ":
                j -= 1
            left = text[j] if j >= 0 else ""
            # 向右跳过连续空格找最近非空格
            j = i + 1
            while j < n and text[j] == " ":
                j += 1
            right = text[j] if j < n else ""
            if _is_cjk_glue_char(left) or _is_cjk_glue_char(right):
                continue
        out.append(c)
    return "".join(out)


class TtsStreamNormalizer:
    """跨 chunk 安全的 TTS 文本规范化器。

    Gemini Live 等 realtime 后端的 output transcript 会在中文 token 之间
    插入 ASCII 空格（"你 好 世 界"），MiniMax / CosyVoice 等 streaming
    TTS 会把这些断开的中文读成顿挫的短片段。该 normalizer 用
    :func:`drop_cjk_boundary_spaces` 去除 CJK 邻接的 ASCII 空格，同时
    针对 streaming 场景做了两项关键处理：

    1. 尾部空格**延后决策**：chunk 末尾的 ASCII 空格暂存到下一个 chunk
       出现时，再结合后一个字符判断是否保留。
    2. 左侧上下文**跨 chunk 继承**：用上一次 emit 出的最后一个非空格字符
       作为下个 chunk 首位空格的"左邻居"，避免在 chunk 边界误判。

    刻意**不碰**非 CJK 脚本（Korean / Cyrillic / Arabic / Thai 等）的空格，
    它们靠 ASCII 空格做分词，删掉会让 TTS 彻底读不对。每个新的 TTS 轮次
    （speech_id 切换）必须调用 :meth:`reset`。
    """

    __slots__ = ("_last_nonspace", "_pending_spaces")

    def __init__(self):
        self._last_nonspace = ""
        self._pending_spaces = ""

    def reset(self) -> None:
        """清空状态。新 speech_id 或中断时调用。"""
        self._last_nonspace = ""
        self._pending_spaces = ""

    def feed(self, chunk: str) -> str:
        """输入一个新 chunk，返回当前可安全 emit 的已规范化文本。"""
        if not chunk:
            return ""

        work = self._pending_spaces + chunk

        # 尾部 ASCII 空格暂存，等下一个 chunk 的首字符决定去留
        stripped = work.rstrip(" ")
        self._pending_spaces = work[len(stripped):]
        if not stripped:
            return ""

        # 用上次 emit 的末位非空格字符当左邻居；非空格保证
        # drop_cjk_boundary_spaces 不会丢掉 prefix 本身，可以用长度精确剥离。
        prefix = self._last_nonspace
        filtered = drop_cjk_boundary_spaces(prefix + stripped)
        if prefix and filtered.startswith(prefix):
            filtered = filtered[len(prefix):]

        for c in reversed(filtered):
            if c != " ":
                self._last_nonspace = c
                break

        return filtered

    def flush(self) -> str:
        """轮次结束收尾：丢弃悬挂的尾部空格并清空状态。"""
        self._pending_spaces = ""
        self._last_nonspace = ""
        return ""


class TtsBracketStripper:
    """跨 chunk 安全的 TTS 括号剥离器。

    括号内容连同括号本身**整体不读**（含所有半角/全角括号类型，包含
    嵌套）。流式输入下一句 ``她（笑）说`` 可能被切成 ``她（``、``笑``、
    ``）说`` 三个 chunk，本类用 ``depth`` 计数维持嵌套状态，跨 chunk
    不丢失。

    每个新 TTS 轮次（``speech_id`` 切换）必须调 :meth:`reset`，否则
    上一轮悬挂的 open bracket 会把新一轮的开头一段静音掉。
    :meth:`flush` 在 turn end 前调一次，把残留的未闭合括号 depth 直接
    清零（半个 ``（thinking...`` 不读比读半个更安全）。

    设计上和 markdown 剥离解耦：``[`` ``]`` 的"剥外壳保内容"语义留给
    :class:`TtsMarkdownStripper` 处理（markdown 链接 ``[文本](URL)``
    需要保留 ``文本``）。本类把 ``[`` ``]`` 视作整段不读的括号，因此
    ``TtsMarkdownStripper`` 必须串在前面，先把链接等剥成纯文本再交给
    本类，否则链接文本会被吃掉。
    """

    # 半角 + 全角 + 各类引用括号；刻意不含 `{` `｛` `}` `｝`
    # （编程语境会出现，朗读时反而希望保留）。
    _OPEN = frozenset("(（[［【〈〔《「『")
    _CLOSE = frozenset(")）]］】〉〕》」』")

    __slots__ = ("_depth",)

    def __init__(self):
        self._depth = 0

    def reset(self) -> None:
        """清空 depth。新 speech_id 或中断时调。"""
        self._depth = 0

    def feed(self, chunk: str) -> str:
        """逐字符扫描，返回当前可 emit 的（深度为 0 之外的）文本。"""
        if not chunk:
            return ""
        out = []
        depth = self._depth
        for c in chunk:
            if c in self._OPEN:
                depth += 1
            elif c in self._CLOSE:
                if depth > 0:
                    depth -= 1
                else:
                    # 落单的 close 括号：当成普通标点 emit，避免把
                    # ``50)`` 这种数学/列表写法的 ``)`` 整个吃掉。
                    out.append(c)
            elif depth == 0:
                out.append(c)
            # else: depth > 0，正在括号里，整个丢
        self._depth = depth
        return "".join(out)

    def flush(self) -> str:
        """轮次收尾：清零 depth，返回 ``""``。

        未闭合括号的悬挂内容已经在 feed 阶段被丢弃，这里只需重置状态。
        """
        self._depth = 0
        return ""


class TtsMarkdownStripper:
    """跨 chunk 安全的 markdown 剥离器（best effort）。

    剥外壳保内容：``**X**`` → ``X``、``[X](url)`` → ``X`` 等。
    覆盖以下模式（顺序 = 优先级）：

    1. 三反引号 fence ``` ```lang\\nbody``` ``` → 整段丢（朗读代码无意义）
    2. 行内 fence ``` ``X`` ``` → ``X``
    3. 图片 ``![alt](url)`` → 整段丢（含 alt）
    4. 链接 ``[text](url)`` → ``text``
    5. ``**X**`` / ``__X__`` → ``X``
    6. ``*X*`` / ``_X_`` → ``X``（``_`` 严格要求两侧非字母数字，避开变量名）
    7. ``~~X~~`` → ``X``
    8. 行首 ``#``/``##``/...``/``>``/``-``/``*``/``\\d+.`` 列表/标题/引用前缀 → 删

    流式策略：用 ``_safe_split`` 找出"从最早未配对的 marker 起到 buf 末"的位置，
    那段 hold 成 pending 等下个 chunk；前面的 emit。pending 上限 ``_MAX_PENDING``
    防止模型半天不闭合一直憋着——超限就强制 emit + reset，避免内存膨胀。
    :meth:`flush` 在 turn end 前调一次，把 pending strip 一遍并把残留的孤立
    marker 字符（``*`` ``_`` ``~`` ``\\``` ``[`` ``]`` ``(`` ``)``）删掉再 emit。

    刻意 best effort：嵌套 emphasis、跨 fence 的复杂场景不保证完美——LLM
    很少这么写，过度工程化得不偿失。完全不会"吃"用户内容（坏情况只是漏剥）。
    """

    __slots__ = ("_pending",)

    # pending 字节上限，防止模型不闭合时无限累加。超限直接 emit + reset。
    _MAX_PENDING = 256

    # 多字符 marker（必须先于单字符匹配，否则 ``**`` 会被算成两个 ``*``）。
    _MULTI_MARKERS = ("```", "**", "__", "~~")
    _SINGLE_MARKERS = ("*", "_", "~", "`")

    # _strip 用的完整正则模式（顺序敏感）
    _PATTERNS = (
        # 多行 fence（含语言标签）整段删
        (re.compile(r"```[^\n]*\n[\s\S]*?```"), ""),
        # 行内 fence 保留内容
        (re.compile(r"```([^`\n]*?)```"), r"\1"),
        # 图片整段删（包括 alt 文本，避免朗读 "alt-text"）
        (re.compile(r"!\[[^\]]*?\]\([^)]*?\)"), ""),
        # 链接保留 text，丢 url
        (re.compile(r"\[([^\]]+?)\]\([^)]*?\)"), r"\1"),
        # bold（先于 italic 处理，避免 ``**X**`` 被当成两个 ``*X*``）
        (re.compile(r"\*\*([^*\n]+?)\*\*"), r"\1"),
        (re.compile(r"__([^_\n]+?)__"), r"\1"),
        # italic
        (re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)"), r"\1"),
        # ``_`` 两侧必须非字母数字，否则会吃掉 ``foo_bar`` 这种变量名
        (re.compile(r"(?<![A-Za-z0-9_])_([^_\n]+?)_(?![A-Za-z0-9_])"), r"\1"),
        # strikethrough
        (re.compile(r"~~([^~\n]+?)~~"), r"\1"),
        # inline code
        (re.compile(r"`+([^`\n]+?)`+"), r"\1"),
        # 行首 marker（heading / blockquote / list）
        (re.compile(r"(?m)^[ \t]*(?:#{1,6}|>+|[-*+]|\d+\.)[ \t]+"), ""),
    )

    # flush 兜底：删掉残留的孤立 marker 字符
    _DANGLING_RE = re.compile(r"[*_~`\[\]()]+")

    def __init__(self):
        self._pending = ""

    def reset(self) -> None:
        """清空 pending。新 speech_id 或中断时调。"""
        self._pending = ""

    def feed(self, chunk: str) -> str:
        """喂入新 chunk，返回当前可安全 emit 的已剥离文本。"""
        if not chunk:
            return ""
        buf = self._pending + chunk

        # pending 撑满兜底：模型一直不闭合时强制 emit + reset，避免内存膨胀
        if len(buf) > self._MAX_PENDING:
            out = self._strip(buf)
            self._pending = ""
            return out

        split = self._safe_split(buf)
        emit = buf[:split]
        self._pending = buf[split:]

        if not emit:
            return ""
        return self._strip(emit)

    def flush(self) -> str:
        """轮次收尾：strip pending，再删掉残留的孤立 marker 字符后 emit。"""
        if not self._pending:
            return ""
        out = self._strip(self._pending)
        out = self._DANGLING_RE.sub("", out)
        self._pending = ""
        return out

    @classmethod
    def _safe_split(cls, buf: str) -> int:
        """找出 buf 中"从最早未配对 marker 到末尾"的起点。

        前面的部分是"已经能确定不会被后续 chunk 改变"的，可以直接 emit；
        从这个位置起到 buf 末是 pending，等更多 chunk 决定如何 strip。
        """
        split = len(buf)
        work = list(buf)

        # 多字符 marker：parity 检查。配对的 black-out，避免单字符再次计入。
        for marker in cls._MULTI_MARKERS:
            mlen = len(marker)
            positions = []
            i = 0
            current = "".join(work)
            while True:
                j = current.find(marker, i)
                if j < 0:
                    break
                positions.append(j)
                i = j + mlen
            if not positions:
                continue
            if len(positions) % 2:
                # 末尾一个未配对，从它起 hold
                split = min(split, positions[-1])
                paired = positions[:-1]
            else:
                paired = positions
            for p in paired:
                for k in range(mlen):
                    if 0 <= p + k < len(work):
                        work[p + k] = "\0"

        # 单字符 marker（已 black-out 多字符配对的视图）
        work_str = "".join(work)
        for marker in cls._SINGLE_MARKERS:
            if marker == "_":
                # ``_`` 两侧紧贴 alnum 时属于 identifier（``foo_bar``），不当 italic marker。
                # 与 _strip 的 italic underscore 正则的 lookbehind/lookahead 保持一致。
                positions = []
                for i, c in enumerate(work_str):
                    if c != "_":
                        continue
                    left_alnum = i > 0 and (work_str[i - 1].isalnum() or work_str[i - 1] == "_")
                    right_alnum = (
                        i + 1 < len(work_str)
                        and (work_str[i + 1].isalnum() or work_str[i + 1] == "_")
                    )
                    if left_alnum and right_alnum:
                        continue  # identifier 内的 _，不算 marker
                    positions.append(i)
            else:
                positions = [i for i, c in enumerate(work_str) if c == marker]
            if len(positions) % 2:
                split = min(split, positions[-1])

        # ``[ ]`` 配对
        stack = []
        for i, c in enumerate(work_str):
            if c == "[":
                stack.append(i)
            elif c == "]" and stack:
                stack.pop()
        if stack:
            split = min(split, stack[0])

        # ``](`` 后未见 ``)`` —— 链接 URL 还没写完
        idx = 0
        while True:
            j = work_str.find("](", idx)
            if j < 0:
                break
            close = work_str.find(")", j + 2)
            if close < 0:
                bracket_pos = work_str.rfind("[", 0, j)
                if bracket_pos >= 0:
                    split = min(split, bracket_pos)
                break
            idx = close + 1

        return max(0, split)

    @classmethod
    def _strip(cls, text: str) -> str:
        for pat, repl in cls._PATTERNS:
            text = pat.sub(repl, text)
        return text


def is_only_punctuation(text):
    # Regular expression: Match strings that consist only of punctuation marks or are empty.
    punctuation_pattern = r'^[\p{P}\p{S}]*$'
    return bool(regex.fullmatch(punctuation_pattern, text))


def calculate_text_similarity(text1: str, text2: str) -> float:
    """
    计算两段文本的相似度（使用字符级 trigram 的 Jaccard 相似度）。
    返回 0.0 到 1.0 之间的值。
    """
    if not text1 or not text2:
        return 0.0
    
    # 生成字符级 trigrams
    def get_trigrams(text: str) -> set:
        text = text.lower().strip()
        if len(text) < 3:
            return {text}
        return {text[i:i+3] for i in range(len(text) - 2)}
    
    trigrams1 = get_trigrams(text1)
    trigrams2 = get_trigrams(text2)
    
    if not trigrams1 or not trigrams2:
        return 0.0
    
    intersection = len(trigrams1 & trigrams2)
    union = len(trigrams1 | trigrams2)
    
    return intersection / union if union > 0 else 0.0


def find_models():
    """
    递归扫描 'static' 文件夹、用户文档下的 'live2d' 文件夹、Steam创意工坊目录和用户mod路径，
    查找所有包含 '.model3.json' 文件的子目录。
    """
    from utils.config_manager import get_config_manager
    
    found_models = []
    search_dirs = []
    
    # 添加static目录
    static_dir = 'static'
    if os.path.exists(static_dir):
        search_dirs.append(('static', static_dir, '/static'))
    else:
        logging.warning(f"警告：static文件夹路径不存在: {static_dir}")
    
    # 添加用户文档目录下的live2d文件夹
    # CFA (反勒索防护) 感知：如果原始 Documents 不可写但可读，
    # 从原始路径读取模型（/user_live2d），可写回退路径作为辅助（/user_live2d_local）
    try:
        config_mgr = get_config_manager()
        config_mgr.ensure_live2d_directory()
        readable_live2d = config_mgr.readable_live2d_dir

        def _norm(path: str) -> str:
            return os.path.normcase(os.path.normpath(path))

        writable_live2d = str(config_mgr.live2d_dir)
        readable_live2d_str = str(readable_live2d) if readable_live2d else ""

        for live2d_root in config_mgr.get_live2d_lookup_roots(prefer_writable=True):
            live2d_root_str = str(live2d_root)
            if not os.path.exists(live2d_root_str):
                continue

            if readable_live2d_str and _norm(live2d_root_str) == _norm(writable_live2d) and _norm(writable_live2d) != _norm(readable_live2d_str):
                # CFA 场景的可写回退目录（优先）
                search_dirs.append(('documents_local', live2d_root_str, '/user_live2d_local'))
            elif readable_live2d_str and _norm(live2d_root_str) == _norm(readable_live2d_str):
                # CFA 场景的只读原始目录（回退）
                search_dirs.append(('documents_legacy', live2d_root_str, '/user_live2d'))
            else:
                search_dirs.append(('documents', live2d_root_str, '/user_live2d'))
    except Exception as e:
        logging.warning(f"无法访问用户文档live2d目录: {e}")
    
    # 添加Steam创意工坊目录
    workshop_search_dir = _resolve_workshop_search_dir()
    if workshop_search_dir and os.path.exists(workshop_search_dir):
        search_dirs.append(('workshop', workshop_search_dir, '/workshop'))
    
    # 遍历所有搜索目录
    for source, search_root_dir, url_prefix in search_dirs:
        try:
            # os.walk会遍历指定的根目录下的所有文件夹和文件
            for root, dirs, files in os.walk(search_root_dir):
                for file in files:
                    if file.endswith('.model3.json'):
                        # 获取模型名称 (使用其所在的文件夹名，更加直观)
                        folder_name = os.path.basename(root)
                        
                        # 使用文件夹名作为模型名称和显示名称
                        display_name = folder_name
                        model_name = folder_name
                        
                        # 构建可被浏览器访问的URL路径
                        # 1. 计算文件相对于 search_root_dir 的路径
                        relative_path = os.path.relpath(os.path.join(root, file), search_root_dir)
                        # 2. 将本地路径分隔符 (如'\\') 替换为URL分隔符 ('/')
                        model_path = relative_path.replace(os.path.sep, '/')
                        
                        # 如果模型名称已存在，添加来源后缀以区分
                        existing_names = [m["name"] for m in found_models]
                        final_name = model_name
                        if model_name in existing_names:
                            final_name = f"{model_name}_{source}"
                            # 如果加后缀后还是重复，再加个数字后缀
                            counter = 1
                            while final_name in existing_names:
                                final_name = f"{model_name}_{source}_{counter}"
                                counter += 1
                            # 同时更新display_name以区分
                            display_name = f"{display_name} ({source})"
                        
                        model_entry = {
                            "name": final_name,
                            "display_name": display_name,
                            "path": f"{url_prefix}/{model_path}",
                            "source": source
                        }
                        
                        if source == 'workshop':
                            path_parts = model_path.split('/')
                            if path_parts and path_parts[0].isdigit():
                                model_entry["item_id"] = path_parts[0]
                        
                        found_models.append(model_entry)
                        
                        # 优化：一旦在某个目录找到模型json，就无需再继续深入该目录的子目录
                        dirs[:] = []
                        break
        except Exception as e:
            logging.error(f"搜索目录 {search_root_dir} 时出错: {e}")
                
    return found_models

def _is_within(base: str, target: str) -> bool:
    """
    检查 target 路径是否在 base 路径内（用于路径遍历防护）
    
    在 Windows 上，如果 base 和 target 位于不同驱动器，os.path.commonpath 会抛出 ValueError。
    此函数捕获该异常并返回 False，安全地处理跨驱动器的情况。
    
    Args:
        base: 基础路径（目录）
        target: 目标路径（要检查的路径）
        
    Returns:
        True 如果 target 在 base 内，False 否则（包括跨驱动器的情况）
    """
    try:
        return os.path.commonpath([target, base]) == base
    except ValueError:
        # 跨驱动器或其他无法比较的情况
        return False


def is_user_imported_model(model_path: str, config_manager=None) -> bool:
    """
    检查模型路径是否在用户导入的模型目录下
    
    用于验证模型是否属于用户导入的模型（而非系统模型或创意工坊模型），
    以便进行权限检查（如删除、保存配置等操作）。
    
    Args:
        model_path: 模型目录的路径（字符串）
        config_manager: 配置管理器实例。如果为 None，会从 get_config_manager() 获取
        
    Returns:
        True 如果模型在用户导入目录下，False 否则（包括异常情况）
    """
    try:
        if config_manager is None:
            from utils.config_manager import get_config_manager
            config_manager = get_config_manager()
        
        config_manager.ensure_live2d_directory()
        user_live2d_dir = os.path.realpath(str(config_manager.live2d_dir))
        model_path_real = os.path.realpath(model_path)
        
        # 使用 _is_within 来安全地检查路径（处理跨驱动器情况）
        return _is_within(user_live2d_dir, model_path_real)
    except Exception:
        # 任何异常都返回 False，表示不是用户导入的模型
        return False


def _resolve_workshop_search_dir() -> str:
    """
    获取创意工坊搜索目录
    
    优先级: user_mod_folder(配置) > Steam运行时路径 > user_workshop_folder(缓存文件) > default_workshop_folder(配置) > 默认workshop目录
    """
    from utils.config_manager import get_workshop_path
    workshop_path = get_workshop_path()
    if workshop_path and os.path.exists(workshop_path):
        return workshop_path
    return None


def find_model_directory(model_name: str):
    """
    查找模型目录，优先在用户文档目录，其次在创意工坊目录，最后在static目录
    返回 (实际路径, URL前缀) 元组
    """
    from utils.config_manager import get_config_manager
    
    # 验证模型名称，防止路径遍历攻击
    # 允许：字母、数字、下划线、中日韩字符、连字符、空格、括号（半角和全角）、点、逗号等常见字符
    # 拒绝：路径分隔符 / \ 和路径遍历 ..
    if not model_name or not model_name.strip():
        logging.warning("模型名称为空")
        return (None, None)
    if '..' in model_name or '/' in model_name or '\\' in model_name:
        model_name_safe = repr(model_name) if len(model_name) <= 100 else repr(model_name[:100]) + '...'
        logging.warning(f"模型名称包含非法路径字符: {model_name_safe}")
        return (None, None)
    
    WORKSHOP_SEARCH_DIR = _resolve_workshop_search_dir()
    
    # 定义允许的基础目录列表
    allowed_base_dirs = []

    # 获取 CFA 场景下的可读 live2d 目录（可能为 None）
    readable_live2d = None
    try:
        config_mgr = get_config_manager()
        readable_live2d = config_mgr.readable_live2d_dir
    except Exception:
        pass

    # Live2D 路径查找：优先可写运行时目录，回退只读 legacy 目录
    try:
        config_mgr = get_config_manager()
        writable_live2d = os.path.normcase(os.path.normpath(str(config_mgr.live2d_dir)))
        readable_live2d_norm = (
            os.path.normcase(os.path.normpath(str(readable_live2d)))
            if readable_live2d else ""
        )
        for live2d_root in config_mgr.get_live2d_lookup_roots(prefer_writable=True):
            docs_model_dir = live2d_root / model_name
            if not docs_model_dir.exists():
                continue
            docs_model_dir_real = os.path.realpath(docs_model_dir)
            docs_live2d_dir_real = os.path.realpath(live2d_root)
            if os.path.commonpath([docs_model_dir_real, docs_live2d_dir_real]) != docs_live2d_dir_real:
                continue

            live2d_root_norm = os.path.normcase(os.path.normpath(str(live2d_root)))
            if readable_live2d_norm and live2d_root_norm == writable_live2d and writable_live2d != readable_live2d_norm:
                return (str(docs_model_dir), '/user_live2d_local')
            return (str(docs_model_dir), '/user_live2d')
    except Exception as e:
        logging.warning(f"检查文档目录模型时出错: {e}")

    # 然后尝试创意工坊目录
    try:
        if WORKSHOP_SEARCH_DIR and os.path.exists(WORKSHOP_SEARCH_DIR):
            workshop_search_real = os.path.realpath(WORKSHOP_SEARCH_DIR)
            # 直接匹配（如果模型名称恰好与文件夹名相同）
            workshop_model_dir = os.path.join(WORKSHOP_SEARCH_DIR, model_name)
            if os.path.exists(workshop_model_dir):
                workshop_model_dir_real = os.path.realpath(workshop_model_dir)
                if os.path.commonpath([workshop_model_dir_real, workshop_search_real]) == workshop_search_real:
                    return (workshop_model_dir, '/workshop')
            
            # 递归搜索创意工坊目录下的所有子文件夹（处理Steam工坊使用物品ID命名的情况）
            for item_id in os.listdir(WORKSHOP_SEARCH_DIR):
                item_path = os.path.join(WORKSHOP_SEARCH_DIR, item_id)
                item_path_real = os.path.realpath(item_path)
                if os.path.isdir(item_path_real):
                    # 检查子文件夹中是否包含与模型名称匹配的文件夹
                    potential_model_path = os.path.join(item_path, model_name)
                    if os.path.exists(potential_model_path):
                        potential_model_path_real = os.path.realpath(potential_model_path)
                        if os.path.commonpath([potential_model_path_real, workshop_search_real]) == workshop_search_real:
                            return (potential_model_path, '/workshop')
                    
                    # 检查子文件夹本身是否就是模型目录（包含.model3.json文件）
                    for file in os.listdir(item_path):
                        if file.endswith('.model3.json'):
                            # 提取模型名称（不带后缀）
                            potential_model_name = os.path.splitext(os.path.splitext(file)[0])[0]
                            if potential_model_name == model_name:
                                if os.path.commonpath([item_path_real, workshop_search_real]) == workshop_search_real:
                                    return (item_path, '/workshop')
    except Exception as e:
        logging.warning(f"检查创意工坊目录模型时出错: {e}")
    
    # 然后尝试用户mod路径
    try:
        config_mgr = get_config_manager()
        user_mods_path = config_mgr.get_workshop_path()
        if user_mods_path and os.path.exists(user_mods_path):
            user_mods_path_real = os.path.realpath(user_mods_path)
            # 直接匹配（如果模型名称恰好与文件夹名相同）
            user_mod_model_dir = os.path.join(user_mods_path, model_name)
            if os.path.exists(user_mod_model_dir):
                user_mod_model_dir_real = os.path.realpath(user_mod_model_dir)
                if os.path.commonpath([user_mod_model_dir_real, user_mods_path_real]) == user_mods_path_real:
                    return (user_mod_model_dir, '/user_mods')
            
            # 递归搜索用户mod目录下的所有子文件夹
            for mod_folder in os.listdir(user_mods_path):
                mod_path = os.path.join(user_mods_path, mod_folder)
                mod_path_real = os.path.realpath(mod_path)
                if os.path.isdir(mod_path_real):
                    # 检查子文件夹中是否包含与模型名称匹配的文件夹
                    potential_model_path = os.path.join(mod_path, model_name)
                    if os.path.exists(potential_model_path):
                        potential_model_path_real = os.path.realpath(potential_model_path)
                        if os.path.commonpath([potential_model_path_real, user_mods_path_real]) == user_mods_path_real:
                            return (potential_model_path, '/user_mods')
                    
                    # 检查子文件夹本身是否就是模型目录（包含.model3.json文件）
                    for file in os.listdir(mod_path):
                        if file.endswith('.model3.json'):
                            # 提取模型名称（不带后缀）
                            potential_model_name = os.path.splitext(os.path.splitext(file)[0])[0]
                            if potential_model_name == model_name:
                                if os.path.commonpath([mod_path_real, user_mods_path_real]) == user_mods_path_real:
                                    return (mod_path, '/user_mods')
    except Exception as e:
        logging.warning(f"检查用户mod目录模型时出错: {e}")
    
    # 最后尝试static目录
    static_dir = 'static'
    static_dir_real = os.path.realpath(static_dir)
    static_model_dir = os.path.join(static_dir, model_name)
    if os.path.exists(static_model_dir):
        static_model_dir_real = os.path.realpath(static_model_dir)
        if os.path.commonpath([static_model_dir_real, static_dir_real]) == static_dir_real:
            return (static_model_dir, '/static')
    
    # 如果都不存在，返回None
    return (None, None)

def find_workshop_item_by_id(item_id: str) -> tuple:
    """
    根据物品ID查找Steam创意工坊物品文件夹
    
    Args:
        item_id: Steam创意工坊物品ID
        
    Returns:
        (物品路径, URL前缀) 元组，即使找不到也会返回默认值
    """
    try:
        workshop_dir = _resolve_workshop_search_dir()
        
        # 如果路径不存在或为空，使用默认的static目录
        if not workshop_dir or not os.path.exists(workshop_dir):
            logging.warning(f"创意工坊目录不存在或无效: {workshop_dir}，使用默认路径")
            default_path = os.path.join("static", item_id)
            return (default_path, '/static')
        
        # 直接使用物品ID作为文件夹名查找
        item_path = os.path.join(workshop_dir, item_id)
        if os.path.isdir(item_path):
            # 检查是否包含.model3.json文件
            has_model_file = any(file.endswith('.model3.json') for file in os.listdir(item_path))
            if has_model_file:
                return (item_path, '/workshop')
            
            # 检查子文件夹中是否有模型文件
            for subdir in os.listdir(item_path):
                subdir_path = os.path.join(item_path, subdir)
                if os.path.isdir(subdir_path):
                    # 检查子文件夹中是否有模型文件
                    if any(file.endswith('.model3.json') for file in os.listdir(subdir_path)):
                        return (item_path, '/workshop')
        
        # 如果找不到匹配的文件夹，返回默认路径
        default_path = os.path.join(workshop_dir, item_id)
        return (default_path, '/workshop')
    except Exception as e:
        logging.error(f"查找创意工坊物品ID {item_id} 时出错: {e}")
        # 出错时返回默认路径
        default_path = os.path.join("static", item_id)
        return (default_path, '/static')


def find_model_by_workshop_item_id(item_id: str) -> str:
    """
    根据物品ID查找模型配置文件URL
    
    Args:
        item_id: Steam创意工坊物品ID
        
    Returns:
        模型配置文件的URL路径，如果找不到返回None
    """
    try:
        # 使用find_workshop_item_by_id查找物品文件夹
        item_result = find_workshop_item_by_id(item_id)
        if not item_result:
            logging.warning(f"未找到创意工坊物品ID: {item_id}")
            return None
        
        model_dir, url_prefix = item_result
        
        # 查找.model3.json文件
        model_files = []
        for root, _, files in os.walk(model_dir):
            for file in files:
                if file.endswith('.model3.json'):
                    # 计算相对路径
                    relative_path = os.path.relpath(os.path.join(root, file), model_dir)
                    model_files.append(os.path.normpath(relative_path).replace('\\', '/'))
        
        if model_files:
            # 优先返回与文件夹同名的模型文件
            folder_name = os.path.basename(model_dir)
            for model_file in model_files:
                if model_file.endswith(f"{folder_name}.model3.json"):
                    return f"{url_prefix}/{item_id}/{model_file}"
            # 否则返回第一个找到的模型文件
            return f"{url_prefix}/{item_id}/{model_files[0]}"
        
        logging.warning(f"创意工坊物品 {item_id} 中未找到模型配置文件")
        return None
    except Exception as e:
        logging.error(f"根据创意工坊物品ID {item_id} 查找模型时出错: {e}")
        return None


def find_model_config_file(model_name: str) -> str:
    """
    在模型目录中查找.model3.json配置文件
    返回可访问的URL路径
    """
    model_dir, url_prefix = find_model_directory(model_name)
    
    if not model_dir or not os.path.exists(model_dir):
        # 如果找不到模型目录，返回 None 或空字符串，而不是默认路径
        return None
    
    # 查找.model3.json文件
    for file in os.listdir(model_dir):
        if file.endswith('.model3.json'):
            return f"{url_prefix}/{model_name}/{file}"
    
    # 如果没找到，返回默认路径
    return f"{url_prefix}/{model_name}/{model_name}.model3.json"

def get_timestamp():
    """Generate formatted timestamp like: Sunday, December 14, 2025 at 12:27 PM"""
    try:
        old_locale = locale.getlocale(locale.LC_TIME)
        try:
            locale.setlocale(locale.LC_TIME, 'en_US.UTF-8')
        except locale.Error:
            try:
                locale.setlocale(locale.LC_TIME, 'English_United States.1252')
            except locale.Error:
                pass
        now = datetime.now()
        timestamp = now.strftime("%A, %B %d, %Y at %I:%M %p")
        try:
            locale.setlocale(locale.LC_TIME, old_locale)
        except: # noqa
            pass
        return timestamp
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M")
