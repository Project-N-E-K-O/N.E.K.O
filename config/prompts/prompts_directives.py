# -*- coding: utf-8 -*-
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

"""
Centralized prompts + templates for user **negative-intent / avoidance directives**.
Two related but distinct tools live here:

(1) **Ban-topic extraction (with term)**: ``DIRECTIVE_PATTERNS`` regex templates for
    7 locales + ``extract_directives()``. Matches imperative "verb + object"
    structures; the capture group yields the topic directly. On a hit,
    ``memory.user_directives`` persists it for 3 days (TTL:
    ``USER_DIRECTIVE_TTL_SECONDS``); on the next ``_build_initial_prompt`` startup the
    active terms are injected into the system prompt so the model avoids them.

(2) **Negative-intent keyword scan (boolean)**: ``NEGATIVE_KEYWORDS_I18N`` +
    ``scan_negative_keywords()``. A frozenset substring scan; a hit means "the user
    wants to end the current topic" (covering both the *explicit avoidance* and the
    *annoyance* families). Downstream, the evidence system
    (``app/memory_server._amaybe_trigger_negative_keyword_hook``) asynchronously runs
    one LLM target check (``NEGATIVE_TARGET_CHECK_PROMPT``) deciding which fact gets
    the disputation signal.

Motivation
----------
Users occasionally say explicitly "别再提 X / 不要叫我 X / stop saying X /
その話はもう" — all explicit ban-topic directives. The current-session LLM sees the
original message and needs no help here; but by the **next session restart**
(archive / cold start / reconnect) that message has long been compressed away and the
model steps on the same landmine again.

Where it lands: run the regex extraction at the user_utterance entry point → on hit →
write to ``memory/{name}/user_directives.json`` (3-day TTL, storage handled by
``memory/user_directives.py``). The next ``_build_initial_prompt`` renders the active
entries into a block appended to the end of the system prompt.

Convention: prefer false positives
----------------------------------
- All locale templates run **in parallel**, independent of language detection
  (mixed Chinese/English speech is common)
- Captured terms only get a light trim (strip surrounding punctuation + particles),
  no semantic validation
- A term is stored only when its length ∈ [2, 40]; out-of-range terms are dropped
- The regexes only cover directives **with a concrete object** (ban_topic).
  Object-less "闭嘴/换话题/shut up" is already visible to the LLM in context and is a
  poor fit for persistence, so it is **not** extracted
- Cost of a false positive = the user says it once more; model cost = one extra
  system-prompt line; cost of a miss = the user gets offended again. Hence the bias
  toward leniency.
- ⚠️ The zh templates are the one exception, and only against *Japanese* input.
  They carry both scripts in one pattern, and Traditional ``別`` is the same
  codepoint as the Japanese kanji while ``提 / 講 / 談 / 討論`` are shared outright —
  so "特別講演について話しましょう。" is structurally a zh hit. There the false
  positive is not "one extra line": it pollutes a Japanese user's directive store
  systematically, for three days at a time. Two guards keep that closed; see the
  comment above ``_PATTERNS_RAW``.

ban-topic regex vs. negative-keyword scan
-----------------------------------------
- The regex can capture the term directly (imperative structure is clear); it feeds
  the user_directives persistence
- The substring scan only decides "is there negative intent" and captures no term;
  it is the fast pre-filter for evidence (LLM re-checks the target on a hit) and also
  covers the "annoyed" family ("烦死", "annoying" — no term, not a directive, but
  still a negative signal)
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import re
from typing import List, Tuple

from config.prompts.prompts_sys import _loc


# 抓到 term 后剥两端的字符：标点 + 各语言语气助词 / 修饰小尾巴。
# 全在尾部 strip，不影响中间内容。
_TRIM_TRAIL = (
    # ASCII / CJK 标点 / 空白
    " \t\n\r"
    ".,!?;:\"'`()[]{}<>"
    "。！？，；：、…—·"
    "“”‘’（）【】《》「」『』"
)
# 各语言的句末助词 / 语气词（出现在 term 尾部时一并剥掉）。
#
# ⚠️ 按 locale 分开，不是一张全局表：CJK 助词在不同语言里是**同一个码位的不同
# 词**。``唄`` 在中文是 ``呗`` 的繁体语气词，在日文是"歌"（子守唄 = 摇篮曲）；
# 拿中文那套去剥日文 term，``子守唄`` 会被削成 ``子守``（codex P2）。``了`` 同理
# （日文 完了 / 終了）。所以哪个 locale 的模板命中，就只剥哪个 locale 的助词。
_TRIM_TRAIL_TOKENS_BY_LOCALE: dict[str, Tuple[str, ...]] = {
    "zh": (
        "了", "啊", "呀", "吧", "嘛", "哦", "呗", "啦", "呢", "嘞", "诶",
        # zh-TW / 台湾口语。``唄`` 是 ``呗`` 的繁体；喔/囉/唷/齁 在简体语料里少见
        # 但台湾日常极常用——不补的话 "別再提工作喔" 存下来的 term 是 "工作喔"。
        "喔", "囉", "啰", "唄", "唷", "齁",
    ),
    "ja": ("ね", "よ", "わ", "の", "って", "なんて", "という"),
    "ko": ("요", "은", "는", "이", "가", "을", "를", "에", "에서"),
    # ru / pt 鲜见词尾 particle
}

# 与 locale 无关的尾巴：ASCII 词，字形上不可能和别的语言撞。中英混说很常见
# （"别提 my ex please"），所以这些对每个 locale 都剥。
_TRIM_TRAIL_TOKENS_ANY = ("please", "porfa", "porfavor")


def _norm_lang(lang: str) -> str:
    """Normalize a lang code (``zh-CN`` → ``zh``, ``pt-BR`` → ``pt``, etc.).

    The render functions in this module resolve templates by exact dict key; if the
    upstream passes ``user_language`` through unchanged (with a region suffix),
    everything falls into the English fallback — a user-visible regression.
    Normalizing once at the boundary is more robust than requiring every caller to
    normalize first.

    Strategy: prefer ``config._runtime.normalize_language_code`` (the app registers
    ``utils.language_utils.normalize_language_code`` at startup, which understands
    Steam literals like ``schinese`` → ``zh``; unknown languages map to ``en`` —
    render functions fall back to English); when the resolver is unbound, degrade to
    a local split fallback.

    ⚠️ This helper serves the i18n **template rendering** path (unknown → en). If you
    need an "unknown → Chinese" fallback (e.g. the contract of
    ``scan_negative_keywords``), do not reuse this helper; write a local strip — see
    that function's implementation.
    """
    if not lang:
        return 'en'
    try:
        from config._runtime import normalize_language_code as _nlc
        out = _nlc(lang, format='short') or lang
    except Exception:
        out = lang
    # Defensive split: resolver 未绑定（partial entrypoint / 测试直跑）时
    # ``_nlc`` 会**原样**返回输入；这里手动剥 region 后缀，保 zh-CN → zh
    # 这种基础归一化在测试环境也能工作。已是短码则 split 是 no-op。
    if '-' in out or '_' in out:
        out = out.split('-', 1)[0].split('_', 1)[0]
    return out or 'en'


def _trim_term(term: str, locale: str = "") -> str:
    """Trim a term: strip trailing particles/modifiers first, then surrounding punctuation + whitespace.

    ``locale`` selects which language's particle list applies. Passing nothing keeps
    the old cross-locale behaviour for the ASCII tails only — a CJK tail is only
    stripped when the caller says which template matched, because the same
    codepoint is a different word per language (``唄``: Chinese particle, Japanese
    "song"). Callers inside this module always pass it.
    """
    if not term:
        return ""
    family = (locale or "").split("-", 1)[0].split("_", 1)[0].lower()
    tokens = _TRIM_TRAIL_TOKENS_ANY + _TRIM_TRAIL_TOKENS_BY_LOCALE.get(family, ())
    s = term.strip()
    changed = True
    # 反复剥尾词，直到稳定（"了啊吧" 这种连续助词）
    while changed:
        changed = False
        for tok in tokens:
            if s.endswith(tok) and len(s) > len(tok):
                s = s[: -len(tok)].rstrip()
                changed = True
        # 同时剥两端标点
        new_s = s.strip(_TRIM_TRAIL)
        if new_s != s:
            s = new_s
            changed = True
    return s.strip()


# ---------------------------------------------------------------------------
# 正则模板：(locale, kind, compiled_pattern, capture_group_index)
#
# 每条 pattern 必须有一个 capture group 给 term。
# kind 目前只有 ``ban_topic``（带 term）；将来若加 ``rename_request`` 等
# 在此扩展。
# ---------------------------------------------------------------------------

# 各 locale 内的"动词块"（说/提/talk about/言う/...）由各 locale 自己列。
# pattern 全部 re.compile 以 IGNORECASE / UNICODE 跑。

# ---------------------------------------------------------------------------
# zh 的两道守卫：复合词左界 + 假名
# ---------------------------------------------------------------------------
# 繁体不另开一套 pattern，直接写进同一条（``[别別]`` / ``[说說]`` / ``讨论|討論``）。
# 同一个句法结构维护两份 regex，改一侧忘另一侧是迟早的事（#2655 里同类漂移出现
# 过四次）。代价：命中记录里的 locale 一律是 ``zh``——那个字段只做诊断，不查表。
#
# 但 ``別`` 与日文是同一码位（``說`` 不是，日文写作 ``説`` U+8AAC），而
# ``提 / 講 / 談 / 討論`` 本来就是中日共用字，所以补繁体等于把日文输入拉进 zh
# 模板的射程。下面两道守卫各管一维：

# (1) 左界：``X别`` 是复合词词尾而非祈使否定。
# ⚠️ 这一维**不可能枚举干净**——"这个别提了" 与 "个别说法"、"这部分别提了" 与
# "分别说明" 在字面上完全同形，中文分词层面就是歧义。所以这里只收零反例的四组
# （简繁各一个字形）：特别 / 性别 / 区别 / 级别 后面接 说/提/讲/谈 在中日文里都
# 极常见（"他特别提到你的名字" 今天就会被抓成 ban_topic），而 "X特|别提" 这种
# 切法在中文里不存在。
# 其余（个别 / 分别 / 告别 / 类别…）保持既有的宽松口径：它们各自都有真实反例，
# 收紧会把 "工作这个别提了" 这类主用例整片打死——宁可留既有误报。
#
# ⚠️ 这道守卫**只用在模板 1**（否定词 + 动词 + 宾语）。模板 2 / 4 的 ``别`` 前面
# 是被捕获的**话题本身**，话题正好以这些字结尾时（"模特别提了。"、"这种可能性别
# 提了。"）守卫会把整条指令吃掉——而模板 2/4 要求动词后面**紧跟**终结符，本来就
# 很难被复合词命中（残留只有 "他特别提了。" 这种退化形，term 是 2 字垃圾，与本
# PR 之前同）。模板 1 相反：复合词后面接的是句子剩余部分，误报必然发生（codex P2）。
#
# ``个/個`` 能收进来正是因为守卫已经收窄到模板 1：``工作这个别提了`` 这个设计上的
# 主用例走模板 2，不受影响；而模板 1 里 "这个别提工作了" 这种切法在中文里不成立。
# 收了它才挡得住 ``個別提案書。`` 这类**纯汉字**日文（没有假名，(2) 的守卫够不着），
# 顺带修掉简体既有的 ``个别说法不太准确。``（codex P2）。
_BIE_COMPOUND_LEFT = "特性区區级級个個"
_ZH_BIE = f"(?<![{_BIE_COMPOUND_LEFT}])[别別]"

# ``休`` 反过来：作否定词是文言用法（"休提当年勇"），现代聊天里基本不出现；而
# 退休 / 午休 / 调休 / 日文 休講 全是复合词。所以只在词首认它，不做字符枚举。
_ZH_XIU = r"(?<!\w)休"

_ZH_NEG = f"(?:{_ZH_BIE}|不要|不许|不許|不准|莫|{_ZH_XIU}|甭)"

# 模板 1 里 term 与终结符之间允许出现的句末助词。与 ``_TRIM_TRAIL_TOKENS`` 的 zh
# 段成对：这里放行、那里剥掉，少一边 term 就带着助词存进去。
_ZH_FINAL_PARTICLES = "(?:\\s*(?:了|啊|呀|嘛|哦|呗|吧|啦|呢|喔|囉|啰|唄|唷|齁))"

# (2) 假名。⚠️ 不能简单地"命中区间有假名就丢"——被 ban 的**对象本身**经常是日文
# 专有名词（"别再提ドラえもん"、"別叫我お兄ちゃん"），那种句子结构是中文的，假名
# 只是话题名，丢掉等于把用户明确说过的偏好扔了（codex P2）。
# 所以要求三个条件同时成立才判为"这是日文句子"：
_KANA_RE = re.compile(r"[぀-ゟ゠-ヿｦ-ﾟ]")

# (2a) 中文的正面证据：这些字形/组合在现代日文里不存在——简体字形、与日文新字体
# 分道的繁体字形（說/説、這、關/関、沒/没、稱/称）、以及日文不会出现的组合
# （叫我 / 不想 …）。命中区间里出现任意一个，这句就是中文，假名是话题名。
# ⚠️ 不收 ``不要``：它本身是日文词（ふよう），"不要提出書類" 这种会被当成中文证据。
_ZH_EVIDENCE_RE = re.compile(
    r"[别说讲谈讨论关这话题愿懒没许称为說這關沒稱]|叫我|喊我|管我叫|不想|懶得|不願"
)

# (2b) 日文的句法证据：助词 / 助动词 / 敬体词尾。日文**句子**几乎必然出现，而一个
# 被 ban 的专有名词基本不会（ドラえもん / お兄ちゃん 都不含）。判据打在 term 上而
# 不是整段——触发词那一侧本来就是中日共用汉字，看它没有信息量。
# 日文的功能词是**闭集**（不像中文复合词那一维），所以这里按格助词 / 系助词 /
# 助动词 / 接续助词分类列全，不是照着手头语料凑几个（codex P2：只列
# ``のにをはがでと`` 时 "個別提案ください。"、"地域別講座へ申込。" 还是会漏）。
# ⚠️ 唯独不收 ``も``：它出现在 ``ドラえもん`` 里，收了就把上面刚救回来的用例
# 又打回去。``から`` 同类风险（からくりサーカス），但它作为句中助词太常见，留下。
_JA_GRAMMAR_RE = re.compile(
    "|".join((
        # 助动词 / 敬体词尾
        "です", "でした", "ます", "ました", "ましょう", "ません", "ください",
        "である", "らしい", "そうです",
        # 接续 / 复合助词
        "について", "に関して", "という", "ながら", "ので", "けど", "たら",
        # 格助词・系助词・副助词（多字优先，单字放最后的字符组里）
        "から", "まで", "より", "など", "だけ", "でも", "しか", "ばかり",
        "[のにをはがでとへ]",
    ))
)


def _is_japanese_sentence_match(span: str, term: str) -> bool:
    """Is this zh-template hit actually a Japanese sentence caught by shared kanji?

    ``別`` is the same codepoint in Japanese and ``提 / 講 / 談 / 討論`` are shared
    kanji, so "個別提案をお願いします。" is structurally a zh hit whose "topic" is
    just the tail of a cut-in-half Japanese sentence. Suppressing those is worth a
    little recall — a bogus term sits in the user's directive store for three days
    and gets injected into every system prompt.

    What must NOT be suppressed is a Chinese sentence whose *ban target* happens to
    be Japanese ("别再提ドラえもん"). Hence the three-way test; see the comments on
    the regexes above.

    Residual: a Traditional trigger + a shared verb + a title that itself carries
    Japanese particles ("別提君の名は。") is indistinguishable from Japanese by any
    local rule and stays suppressed.
    """  # noqa: DOCSTRING_CJK
    # 快速退出：没假名就不可能是日文句子。(2b) 的判据本身全是假名、且 term 是 span
    # 的子串，所以这一行不是独立条件，只是省掉后面两次 regex——热路径每条用户消息
    # 每条模板都会走到。
    if not _KANA_RE.search(span):
        return False
    if _ZH_EVIDENCE_RE.search(span):
        return False
    return bool(_JA_GRAMMAR_RE.search(term))

_PATTERNS_RAW: List[Tuple[str, str, str]] = [
    # ---------- zh ----------
    # 别/不要/不许/不准 + （再）+ 动词 + 对象
    # terminator 不放 ``\s``：zh 句子里中英混说时（"别叫我 John Smith"）lazy
    # ``(.{1,40}?)`` 会在第一个空格切断成 "John"。让终结符必须是标点 / EOL /
    # 句末助词，多词 NP 才能被完整捕获（codex P2）。
    # 繁体 ``不準`` 不收：它是"不准确"的意思，"測量不準說明有問題" 会被抓成
    # ban_topic；"不允许" 这个义项繁体本来就写 ``不准``，已在表内。
    ("zh", "ban_topic",
     _ZH_NEG + r"\s*(?:再)?\s*"
     # ⚠️ 复合动词必须排在它的单字前缀**之前**。本模板的宾语是 ``(.{1,40}?)``——
     # 什么都能吃，所以 ``提`` 先匹配成功之后正则**不会**再回溯去试 ``提起``，
     # "别提起工作。" 就存成了 ``起工作``（既有缺陷，简繁两侧都有；模板 2/4 要求
     # 动词后紧跟终结符，失败会回溯，所以没这个问题）。
     r"(?:提起|提及|讲到|講到|聊到|谈起|談起|谈到|談到|说起|說起|说到|說到|"
     r"讨论|討論|管我叫|称呼我为?|稱呼我為?|喊我|叫我|"
     r"说|說|提|聊|讲|講|谈|談|扯)\s*"
     r"(.{1,40}?)" + _ZH_FINAL_PARTICLES + r"?(?:[，。！？；,.!?;]|\s*$)"),
    # X + 这个? + 别(再)+ 提
    # ``关于 X 就别提了`` 归模板 4 管。本模板不排掉它的话，同一句会同时产出这里的
    # "关于股票就" 和模板 4 的 "股票" 两条 term——前者是垃圾却照样占一个 active
    # 名额、往 system prompt 里注三天（codex P2；简繁两侧都有，既有缺陷）。
    # 两道排除缺一不可：前缀不能**以** 关于 开头，后一个 lookahead 挡住"从 关于 的
    # 第二个字起匹配"（否则退化成 "于股票"）。lookahead 里带 lookbehind 是为了只挡
    # ``关|于`` 这一个切点——写成 ``(?<![关關])`` 会把 "有关工作别提了" 一起打死。
    # ⚠️ 只挡开头，不是 tempered token 挡"前缀里任意位置含 关于"：书名 / 片名里带
    # 关于 是正常的（"电影《关于爱》别提了。"），挡整段会把整条指令打没（codex P2）。
    # 代价是 ``关于`` 前面还有别的字时（"我觉得关于股票就别再讲了"）仍会多产出一条
    # 长 term——那是既有行为，不是本 PR 引入的。
    # 尾部的 ``的 / 的事 / 这个 / 就`` 与模板 4 对齐，让前缀停在真正的话题上；``的``
    # 单独可选是因为 "減肥的這件事別再說了。" 这种自然说法里它和指示词是分开的。
    ("zh", "ban_topic",
     r"(?!关于|關於)(?!(?<=关)于)(?!(?<=關)於)(.{1,30}?)\s*"
     r"(?:的事)?\s*(?:的)?\s*(?:这个|這個|这事|這事|这话题|這話題|这件事|這件事)?\s*"
     r"(?:就)?\s*[别別]\s*(?:再)?\s*"
     r"(?:提了|提起|提及|说|說|提|聊|讲|講)\s*(?:了)?(?:[，。！？；,.!?;\s]|$)"),
    # 不想/不愿 + 聊/讨论 + X — 同上：terminator 不要 \s，否则多词 NP 被切
    ("zh", "ban_topic",
     r"(?:我)?\s*(?:不想|不愿意|不願意|不愿|不願|懒得|懶得|没心情|沒心情)\s*(?:再)?\s*"
     r"(?:说|說|提|聊|讲|講|谈|談|讨论|討論)\s*(.{1,40}?)(?:\s*(?:了|的事))?(?:[，。！？；,.!?;]|\s*$)"),
    # 关于 X + 别(再)+ 说
    ("zh", "ban_topic",
     r"(?:关于|關於)\s*(.{1,30}?)\s*(?:的事)?\s*(?:的)?\s*"
     r"(?:这个|這個|这事|這事|这话题|這話題|这件事|這件事)?\s*(?:就)?\s*[别別]\s*(?:再)?\s*"
     r"(?:说|說|提|聊|讲|講)\s*(?:了)?(?:[，。！？；,.!?;\s]|$)"),

    # ---------- en ----------
    # stop/don't/quit + verb + (about|saying) + X
    # ``X`` 是英文 NP，常带空格（"my ex"、"the weather"）。terminator 用
    # filler-word / 标点 / 句尾，避免 lazy ``.{1,40}?`` 在 X 内的第一个空格就
    # 切断成 "my"。
    ("en", "ban_topic",
     r"(?:please\s+)?(?:stop|quit|don'?t|do\s+not|no\s+more)\s+"
     r"(?:talking\s+about|talk\s+about|saying|say|mentioning|mention|"
     r"bringing\s+up|bring\s+up|going\s+on\s+about|"
     r"calling\s+me\s+a|calling\s+me|call\s+me\s+a|call\s+me)\s+"
     r"(.{1,40}?)"
     r"(?:\s+(?:again|anymore|any\s+more|please|ever|already|now|"
     r"forever|today|tonight|right\s+now|in\s+(?:front|public))"
     r"|[,.!?;]|$)"),
    # X + is off limits / off the table / not a topic
    ("en", "ban_topic",
     r"(.{1,30}?)\s+is\s+(?:off[\s\-]?limits|off\s+the\s+table|a\s+(?:no[\s\-]?go|forbidden)\s+topic)"
     r"(?:[\s,.!?;]|$)"),
    # I don't want to talk/hear about X
    # X 是 NP 可能含空格（"my ex girlfriend"）。terminator 用 filler-word /
    # 标点 / 句尾，否则 lazy ``.{1,40}?`` 在第一个空格就切断成 "my"（codex P1）。
    ("en", "ban_topic",
     r"i\s+(?:don'?t|do\s+not|really\s+don'?t)\s+(?:want\s+to|wanna)\s+"
     r"(?:talk|hear|discuss|think)\s+(?:about|of)\s+(.{1,40}?)"
     r"(?:\s+(?:anymore|any\s+more|again|ever|already|right\s+now|today|tonight|please)"
     r"|[,.!?;]|$)"),
    # drop the X / leave X alone (subject)
    ("en", "ban_topic",
     r"(?:drop|leave\s+alone)\s+(?:the\s+|that\s+)?(.{1,30}?)\s+"
     r"(?:topic|subject|thing|stuff|already)(?:[\s,.!?;]|$)"),

    # ---------- ja ----------
    # X + のこと/について + は + もう + 言わないで/やめて/しないで
    ("ja", "ban_topic",
     r"(.{1,40}?)\s*(?:のこと|の話|について|に関して|っていう話)\s*"
     r"(?:は)?\s*(?:もう|二度と|これ以上)?\s*"
     r"(?:言わないで|話さないで|しないで|やめて|止めて|よして|聞きたくない|触れないで)"),
    # もう + X + (の話) + (は) + 嫌だ/聞きたくない
    ("ja", "ban_topic",
     r"もう\s*(.{1,40}?)\s*(?:のこと|の話)?\s*(?:は)?\s*"
     r"(?:嫌|いや|聞きたくない|話したくない|やめて)"),
    # X + って + 呼ばないで / 言わないで
    ("ja", "ban_topic",
     r"(.{1,30}?)\s*(?:って|とは|なんて)\s*"
     r"(?:呼ばないで|言わないで|呼ぶな|言うな)"),

    # ---------- ko ----------
    # X + (에 대해|얘기|이야기) + (는)? + 그만 / 하지 마 / 꺼내지 마
    ("ko", "ban_topic",
     r"(.{1,40}?)\s*(?:에\s*대해서?|얘기|이야기|소리|말)\s*(?:는|은)?\s*"
     r"(?:그만|하지\s*마(?:세요|십시오)?|꺼내지\s*마(?:세요)?|관두|치워)"),
    # 다시는 + X + 말하지 마 / 꺼내지 마
    ("ko", "ban_topic",
     r"(?:다시는|두\s*번\s*다시|이제)\s*(.{1,40}?)\s*"
     r"(?:말하지|꺼내지|언급하지)\s*마(?:세요|십시오)?"),
    # X + (이|가)? + 듣기 싫다 / 짜증나
    ("ko", "ban_topic",
     r"(.{1,30}?)\s*(?:이|가)?\s*(?:듣기\s*싫|말하기\s*싫|짜증나|지긋지긋)"),

    # ---------- ru ----------
    # не говори / хватит про / прекрати + (preposition)? + X
    # 介词 "про / о / об / обо" 出现在动词后 + term 前，必须先 consume 才能
    # 让 (.{1,40}?) 捕获到实际话题；否则贪心地把介词当 term。
    # term 用 en 同款 filler-word terminator，支持 "моей бывшей" 这类多词短语。
    ("ru", "ban_topic",
     r"(?:не\s+(?:говори|упоминай|повторяй|произноси|обсуждай|называй\s+меня)|"
     r"хватит\s+(?:говорить|обсуждать|упоминать)|"
     r"перестань\s+(?:говорить|обсуждать|упоминать|называть\s+меня)|"
     r"прекрати\s+(?:говорить|обсуждать|упоминать|называть\s+меня))\s+"
     r"(?:про\s+|обо?\s+|о\s+)?"  # 可选介词
     r"(.{1,40}?)"
     r"(?:\s+(?:больше|никогда|пожалуйста|снова|опять|вообще|сегодня)"
     r"|[,.!?;]|$)"),
    # о X + больше + не говори
    ("ru", "ban_topic",
     r"(?:обо|об|о)\s+(.{1,30}?)\s+больше\s+не\s+(?:говори|упоминай)"),
    # я не хочу + (говорить|слышать) + о X — 同 en 的 filler-word terminator，
    # 支持 "моей бывшей" 这种多词短语。
    ("ru", "ban_topic",
     r"я\s+не\s+хочу\s+(?:говорить|слышать|обсуждать)\s+(?:обо|об|о)\s+(.{1,40}?)"
     r"(?:\s+(?:больше|никогда|пожалуйста|снова|опять|вообще|сегодня)"
     r"|[,.!?;]|$)"),

    # ---------- es ----------
    # no hables / no menciones / deja de hablar + (de|sobre) + X
    ("es", "ban_topic",
     r"(?:no\s+(?:hables|menciones|digas|sigas\s+hablando|me\s+llames)|"
     r"deja\s+de\s+(?:hablar|mencionar|llamarme)|"
     r"para\s+de\s+(?:hablar|mencionar))\s+"
     r"(?:de|sobre|acerca\s+de)?\s*(.{1,40}?)"
     r"(?:\s+(?:más|nunca|jamás|otra\s+vez|de\s+nuevo|por\s+favor|porfa|hoy|ahora)"
     r"|[,.!?;]|$)"),
    # no quiero + (oír|hablar|saber) + (de|nada de) + X — 同 en/ru
    ("es", "ban_topic",
     r"no\s+quiero\s+(?:oír|hablar|saber|escuchar)\s+(?:nada\s+)?(?:de|sobre)\s+"
     r"(.{1,40}?)"
     r"(?:\s+(?:más|nunca|jamás|otra\s+vez|de\s+nuevo|por\s+favor|porfa|hoy|ahora)"
     r"|[,.!?;]|$)"),

    # ---------- pt ----------
    # não fale / não mencione / pare de falar + (de|sobre) + X
    ("pt", "ban_topic",
     r"(?:não\s+(?:fale|mencione|diga|continue\s+falando|me\s+chame)|"
     r"pare\s+de\s+(?:falar|mencionar|me\s+chamar)|"
     r"deix[ea]\s+de\s+(?:falar|mencionar))\s+"  # deixe de / deixa de（codex P2）
     r"(?:de|sobre|a\s+respeito\s+de)?\s*(.{1,40}?)"
     r"(?:\s+(?:mais|nunca|jamais|de\s+novo|outra\s+vez|por\s+favor|hoje|agora)"
     r"|[,.!?;]|$)"),
    # não quero + (ouvir|falar|saber) + (de|sobre|nada de) + X — 同 en/ru
    ("pt", "ban_topic",
     r"não\s+quero\s+(?:ouvir|falar|saber|escutar)\s+(?:nada\s+)?(?:de|sobre)\s+"
     r"(.{1,40}?)"
     r"(?:\s+(?:mais|nunca|jamais|de\s+novo|outra\s+vez|por\s+favor|hoje|agora)"
     r"|[,.!?;]|$)"),
]


# 编译期一次性 compile，运行时直接复用。
DIRECTIVE_PATTERNS: List[Tuple[str, str, "re.Pattern[str]"]] = [
    (locale, kind, re.compile(raw, re.IGNORECASE | re.UNICODE))
    for locale, kind, raw in _PATTERNS_RAW
]


def extract_directives(text: str) -> List[Tuple[str, str, str]]:
    """Run every locale × kind template over a user text; returns ``[(locale, kind, term)]``.

    - All templates are tried **in parallel**, with no upfront language detection
    - On a hit the term is cleaned by ``_trim_term``; its length must be ∈ [2, 40]
    - Each ``(kind, term_lower)`` is kept only once in the result list (keeping the
      first matching locale; duplicate storage is deduped again by
      ``UserDirectivesManager.record``)

    The repetition is deliberate: with upstream mixed-language input one sentence may
    hit patterns from multiple locales; deduping here avoids one sentence producing 5
    records, while **different** terms from the same sentence ("别提小明和小红") are
    still each recorded — provided the template can split out two matches.
    """  # noqa: DOCSTRING_CJK
    if not text:
        return []
    seen: set[tuple[str, str]] = set()
    out: List[Tuple[str, str, str]] = []
    for locale, kind, pat in DIRECTIVE_PATTERNS:
        zh_family = locale.startswith("zh")
        for m in pat.finditer(text):
            try:
                term_raw = m.group(1)
            except IndexError:
                continue
            term = _trim_term(term_raw, locale)
            if not (2 <= len(term) <= 40):
                continue
            # zh 模板与日文共用 別/提/講/談/討論 这些汉字，日文句子会被抓成
            # ban_topic（见 _is_japanese_sentence_match）。只对 zh 生效——ja 模板
            # 本身要求假名，套上去会把自己全部否掉。
            if zh_family and _is_japanese_sentence_match(m.group(0), term):
                continue
            key = (kind, term.casefold())
            if key in seen:
                continue
            seen.add(key)
            out.append((locale, kind, term))
    return out


# ---------------------------------------------------------------------------
# 下一轮会话注入用的 system prompt 片段
# ---------------------------------------------------------------------------
# 历史的"用户最近表示不想聊"列表会被拼成 ``- {term1}\n- {term2}\n``，再用
# 各 locale 的模板包一层 header / footer。两个槽位：
#   {items}     —— bullet list
#   {n}         —— 条数（少数语言语法需要单复数）
#
# 渲染层：UserDirectivesManager.render_prompt_block(lanlan_name, lang)。

USER_DIRECTIVES_PROMPT_BLOCK = {
    'zh': (
        "\n\n[用户最近明确表示过不想聊或不喜欢被提到以下内容（共{n}项）]\n"
        "{items}\n"
        "请在本次会话里主动避开这些话题或称呼，除非用户自己重新提起。"
    ),
    'en': (
        "\n\n[The user recently asked not to discuss or be referred to as the "
        "following ({n} item(s))]\n"
        "{items}\n"
        "Please actively steer clear of these topics or labels in this session, "
        "unless the user brings them up again."
    ),
    'ja': (
        "\n\n[最近、ユーザーが話したくない・呼ばれたくないと明示した内容（{n}件）]\n"
        "{items}\n"
        "今回のセッションでは、ユーザー自身が再び話題にしない限り、"
        "これらの話題や呼び方を能動的に避けてください。"
    ),
    'ko': (
        "\n\n[사용자가 최근에 언급하지 말거나 그렇게 부르지 말라고 명확히 요청한 항목 ({n}개)]\n"
        "{items}\n"
        "이번 세션에서는 사용자가 직접 다시 꺼내지 않는 한, "
        "이러한 화제나 호칭을 적극적으로 피해 주세요."
    ),
    'ru': (
        "\n\n[Пользователь недавно явно просил не обсуждать или не называть "
        "следующее ({n} шт.)]\n"
        "{items}\n"
        "В этой сессии активно избегайте этих тем и обращений, "
        "если пользователь сам к ним не вернётся."
    ),
    'es': (
        "\n\n[El usuario pidió explícitamente no hablar de o no ser llamado/a "
        "con lo siguiente ({n} elemento(s))]\n"
        "{items}\n"
        "Evita activamente estos temas o etiquetas en esta sesión, "
        "salvo que el propio usuario los vuelva a sacar."
    ),
    'pt': (
        "\n\n[O usuário pediu explicitamente para não falar sobre ou ser "
        "chamado(a) pelo seguinte ({n} item(ns))]\n"
        "{items}\n"
        "Evite ativamente esses tópicos ou rótulos nesta sessão, "
        "a menos que o próprio usuário volte a mencioná-los."
    ),
}


def render_directives_block(terms: List[str], lang: str) -> str:
    """Render the active term list into a system-prompt block (with leading newlines).

    Empty list → returns "" (callers concat directly, no emptiness check needed).
    ``lang`` accepts full locales (``zh-CN`` etc.), normalized internally to a short code.
    """
    if not terms:
        return ""
    short = _norm_lang(lang)
    template = USER_DIRECTIVES_PROMPT_BLOCK.get(short) or USER_DIRECTIVES_PROMPT_BLOCK['en']
    items = "\n".join(f"- {t}" for t in terms)
    return template.format(items=items, n=len(terms))


# ---------------------------------------------------------------------------
# 防复读（anti-repeat）— 注入"最近高频 topic 词"提示
# ---------------------------------------------------------------------------
# 来源：``memory.anti_repeat.AntiRepeatCorpus.top_recent_topics``。注入位置同
# ``USER_DIRECTIVES_PROMPT_BLOCK`` —— ``_build_initial_prompt`` 末尾、ban list
# 之后。proactive 与 regular reply 共用：proactive 还会被 BM25 总分阈值
# 拦截（regen / drop），regular 只靠这段 prompt 软约束。
#
# 这段的语气和 ban list 不一样：ban list 是"用户明确说过别提"，必须强约束；
# 这里只是"你最近聊过这些，换些角度更好"，建议性的，不要太重，否则把 LLM
# 引导成话题切换疯子。

RECENT_TOPIC_HINT_PROMPT_BLOCK = {
    'zh': (
        "\n\n[最近几轮你已经聊过的话题（{n}项）]\n"
        "{items}\n"
        "如果还没必要，尽量换个角度或换个话题，避免连续围绕同一主题打转。"
    ),
    'en': (
        "\n\n[Topics you've already touched on in the last few turns ({n})]\n"
        "{items}\n"
        "Unless still relevant, try a fresh angle or a new topic rather than "
        "circling back to the same one."
    ),
    'ja': (
        "\n\n[最近のターンで既に触れた話題（{n}件）]\n"
        "{items}\n"
        "まだ必要でなければ、同じ話題を繰り返さず、別の切り口や新しい話題に"
        "切り替えてみてください。"
    ),
    'ko': (
        "\n\n[최근 몇 턴 동안 이미 다룬 화제 ({n}개)]\n"
        "{items}\n"
        "꼭 필요하지 않다면 같은 주제를 맴돌지 말고 다른 각도나 새로운 화제로"
        "전환해 보세요."
    ),
    'ru': (
        "\n\n[Темы, которые вы уже затронули за последние ходы ({n} шт.)]\n"
        "{items}\n"
        "Если в этом нет необходимости, попробуйте новый ракурс или другую "
        "тему, не кружите вокруг одной и той же."
    ),
    'es': (
        "\n\n[Temas que ya tocaste en los últimos turnos ({n} elemento(s))]\n"
        "{items}\n"
        "Salvo que sea necesario, prueba un ángulo distinto o un tema nuevo "
        "en lugar de volver al mismo."
    ),
    'pt': (
        "\n\n[Tópicos que você já abordou nos últimos turnos ({n} item(ns))]\n"
        "{items}\n"
        "A menos que ainda seja relevante, tente um ângulo novo ou outro "
        "tópico em vez de voltar ao mesmo."
    ),
}


def render_recent_topics_block(terms: List[str], lang: str) -> str:
    """Render the "recent topic terms" list into a system-prompt fragment; empty list → ""."""
    if not terms:
        return ""
    short = _norm_lang(lang)
    template = RECENT_TOPIC_HINT_PROMPT_BLOCK.get(short) or RECENT_TOPIC_HINT_PROMPT_BLOCK['en']
    items = "\n".join(f"- {t}" for t in terms)
    return template.format(items=items, n=len(terms))


# ---------------------------------------------------------------------------
# Proactive regen 指令 — 给重 sample 用
# ---------------------------------------------------------------------------
# 当 BM25 总分超 REGEN_THRESHOLD 时，``main_routers/system_router`` 在第二次
# Phase 2 LLM 调用前注入这段，告诉 LLM 哪些 term 必须避开。
#
# ⚠️ 措辞刻意做成"结构化指令 + 显式反复述约束"：早期版本是一句散文式祈使
# （"换一个完全不同的角度或主题"），弱模型在超长上下文末尾收到后，容易把指令
# 原文/规划脚手架当成正文吐出来（线上见过 "完全不同的角度或主题"、"括号、Emoji"
# 这类泄漏）。现在每条都：(1) 用方括号小标题标明这是改写要求而非对话；(2) 末尾
# 明确"不要复述/解释本要求、不要输出标签化回复以外的任何东西"。注入侧还会把
# BEGIN 触发句放在最后（见 system_router），使指令本身不是模型看到的最后一句。
# 占位符：{terms} 要避开的词；{master_name} 搭话对象。

PROACTIVE_REGEN_AVOID_INSTRUCTION = {
    'zh': (
        "【改写要求】这些词和话题最近已经聊得太多，本次必须避开：{terms}。"
        "换个角度或换个话题，直接写一句全新的搭话。"
        "输出严格遵守上面的格式：第一行写来源标签，第二行起只写要对{master_name}说的原话；"
        "如果想不出新角度，就只输出 [PASS]。"
        "不要复述或解释本要求，不要输出任何思考过程、清单或标签化回复以外的内容。"
    ),
    'en': (
        "[Rewrite] These words and topics have been used too much recently and MUST be "
        "avoided: {terms}. Pick a different angle or topic and write one brand-new line. "
        "Keep strictly to the format above: the first line is the source tag, then write "
        "only the actual words you'd say to {master_name}; if you have no fresh angle, output "
        "only [PASS]. Do NOT restate or explain this instruction, and do NOT output any "
        "reasoning, lists, or anything other than the tagged reply."
    ),
    'ja': (
        "【書き直し】次の語と話題は最近使いすぎているので必ず避けてください：{terms}。"
        "切り口か話題を変えて、新しい一言を書いてください。"
        "出力は上の形式を厳守：1行目に来源タグ、その後は{master_name}に実際に言う言葉だけ。"
        "新しい切り口が思いつかなければ [PASS] だけを出力。"
        "この指示を復唱・説明せず、思考過程やリスト、タグ付き発言以外のものを出力しないこと。"
    ),
    'ko': (
        "【다시 쓰기】다음 단어와 화제는 최근에 너무 많이 다뤘으니 반드시 피하세요: {terms}. "
        "관점이나 화제를 바꿔 완전히 새로운 한마디를 쓰세요. "
        "출력은 위 형식을 엄격히 따르세요: 첫 줄은 출처 태그, 이후에는 {master_name}에게 실제로 "
        "할 말만 쓰세요; 새 관점이 없으면 [PASS]만 출력하세요. 이 지시를 되풀이하거나 설명하지 "
        "말고, 사고 과정·목록·태그 외의 어떤 것도 출력하지 마세요."
    ),
    'ru': (
        "[Перепиши] Эти слова и темы в последнее время используются слишком часто, их "
        "обязательно нужно избегать: {terms}. Выбери другой угол или тему и напиши одну "
        "совершенно новую реплику. Строго соблюдай формат выше: первая строка — тег источника, "
        "далее — только сами слова, которые ты скажешь {master_name}; если нового угла нет, "
        "выведи только [PASS]. Не пересказывай и не объясняй эту инструкцию, не выводи "
        "рассуждения, списки или что-либо кроме реплики с тегом."
    ),
    'es': (
        "[Reescribe] Estas palabras y temas se han usado demasiado últimamente y DEBES "
        "evitarlos: {terms}. Elige otro ángulo o tema y escribe una frase totalmente nueva. "
        "Respeta estrictamente el formato de arriba: la primera línea es la etiqueta de "
        "fuente, luego escribe solo lo que le dirías a {master_name}; si no tienes un ángulo "
        "nuevo, responde solo [PASS]. No repitas ni expliques esta instrucción, y no muestres "
        "razonamientos, listas ni nada que no sea la respuesta con etiqueta."
    ),
    'pt': (
        "[Reescreva] Estas palavras e temas foram usados demais recentemente e você DEVE "
        "evitá-los: {terms}. Escolha outro ângulo ou tema e escreva uma fala totalmente nova. "
        "Siga estritamente o formato acima: a primeira linha é a etiqueta de fonte, depois "
        "escreva apenas o que você diria a {master_name}; se não tiver um ângulo novo, "
        "responda apenas [PASS]. Não repita nem explique esta instrução, e não exiba "
        "raciocínio, listas ou qualquer coisa além da resposta com etiqueta."
    ),
}


# render_regen_avoid_instruction 缺省称呼（master_name 未传时的中性占位）。
# 不用"主人/master"等物化称呼（见项目约定）。
_DEFAULT_ADDRESSEE = {
    "zh": "对方",
    "en": "them",
    "ja": "相手",
    "ko": "상대",
    "ru": "собеседника",
    "es": "la otra persona",
    "pt": "a outra pessoa",
}


def render_regen_avoid_instruction(terms: List[str], lang: str, master_name: str = "") -> str:
    """Render the "avoid X / Y" instruction used for regen. Empty list → "".

    ``master_name`` writes "who this is said to" into the instruction; when missing,
    degrades to a neutral placeholder to avoid KeyError.
    """
    if not terms:
        return ""
    short = _norm_lang(lang)
    template = PROACTIVE_REGEN_AVOID_INSTRUCTION.get(short) or PROACTIVE_REGEN_AVOID_INSTRUCTION['en']
    # 每个词单独括起来，让模型清楚哪些是要避开的离散词（CJK 用「」，其余用双引号），
    # 再用各 locale 的列表分隔符拼接。
    lq, rq = ("「", "」") if short in ("zh", "ja") else ('"', '"')
    sep = "、" if short in ("zh", "ja") else ", "
    quoted_terms = sep.join(f"{lq}{t}{rq}" for t in terms)
    return template.format(
        terms=quoted_terms,
        master_name=master_name or _DEFAULT_ADDRESSEE.get(short, "them"),
    )


# ---------------------------------------------------------------------------
# Proactive 格式纠正指令 — 初稿没按格式输出时自救用
# ---------------------------------------------------------------------------
# 初稿没解析到合法来源标签时（弱化模型常把人设 Format/约束块当正文吐出来，
# 如 "No Markdown: Yes."），system_router 注入这段再生成一次，把模型拽回
# "第一行写来源标签、其后正文" 的格式；与 BEGIN 触发句一起放进 Human turn
# （末尾仍是中性触发句）。占位符：{master_name} 搭话对象。

PROACTIVE_FORMAT_FIX_INSTRUCTION = {
    'zh': (
        "【格式纠正】上一次的输出没有按规定格式，把格式要求当成正文吐了出来。"
        "请重写：第一行只写一个来源标签（按上面输出格式段列出的来源标签选，"
        "如 [CHAT]、[WEB]、[MUSIC]、[MEME]），第二行起只写要对{master_name}说的话本身；"
        "没什么新鲜的可说就只输出 [PASS]。"
        "不要复述或解释任何规则，不要输出清单或思考过程，标签和正文以外的内容一律不要输出。"
    ),
    'en': (
        "[Format fix] Your last output didn't follow the required format — it spat out the "
        "rules as if they were the message. Rewrite it: the first line is a single source tag "
        "(choose from the source tags listed in the output-format section above, e.g. [CHAT], "
        "[WEB], [MUSIC], [MEME]), then from the next line write only the actual words you'd say "
        "to {master_name}; if you have nothing fresh to say, output only [PASS]. Do NOT restate "
        "or explain any rule, do NOT output lists or reasoning, and output nothing other than "
        "the tag and the message."
    ),
    'ja': (
        "【書式修正】前回の出力は指定の書式に従わず、ルールをそのまま本文として出してしまいました。"
        "書き直してください：1行目に来源タグを1つだけ（上の出力形式に挙げられたタグから選ぶ。"
        "例：[CHAT]・[WEB]・[MUSIC]・[MEME]）、2行目以降は{master_name}に実際に言う言葉だけ。"
        "新しく言うことがなければ [PASS] だけを出力。"
        "ルールを復唱・説明せず、リストや思考過程を出さず、タグと本文以外は何も出力しないこと。"
    ),
    'ko': (
        "【형식 교정】지난 출력이 규정된 형식을 따르지 않고 규칙을 본문처럼 뱉어냈습니다. "
        "다시 쓰세요: 첫 줄에는 출처 태그 하나만(위 출력 형식에 나열된 태그 중 선택, 예: [CHAT]·"
        "[WEB]·[MUSIC]·[MEME]), 이후 줄부터는 {master_name}에게 실제로 할 말만. 새로 할 말이 "
        "없으면 [PASS]만 출력. 규칙을 되풀이하거나 설명하지 말고, 목록·사고 과정을 출력하지 "
        "말며, 태그와 본문 외에는 아무것도 출력하지 마세요."
    ),
    'ru': (
        "[Исправь формат] Прошлый вывод не соответствовал формату — ты выдал правила, как "
        "будто это сообщение. Перепиши: первая строка — один тег источника (выбери из тегов, "
        "перечисленных в разделе формата вывода выше, напр. [CHAT], [WEB], [MUSIC], [MEME]), "
        "далее со следующей строки — только сами слова, которые ты скажешь {master_name}; если "
        "нового сказать нечего, выведи только [PASS]. Не пересказывай и не объясняй правила, не "
        "выводи списки или рассуждения и не выводи ничего, кроме тега и сообщения."
    ),
    'es': (
        "[Corrige el formato] Tu última salida no siguió el formato requerido: soltó las reglas "
        "como si fueran el mensaje. Reescríbela: la primera línea es una sola etiqueta de fuente "
        "(elige entre las etiquetas listadas en la sección de formato de salida de arriba, p. ej. "
        "[CHAT], [WEB], [MUSIC], [MEME]), luego desde la línea siguiente escribe solo lo que le "
        "dirías a {master_name}; si no tienes nada nuevo que decir, responde solo [PASS]. No "
        "repitas ni expliques ninguna regla, no muestres listas ni razonamientos, y no muestres "
        "nada más que la etiqueta y el mensaje."
    ),
    'pt': (
        "[Corrija o formato] Sua última saída não seguiu o formato exigido — cuspiu as regras "
        "como se fossem a mensagem. Reescreva: a primeira linha é uma única etiqueta de fonte "
        "(escolha entre as etiquetas listadas na seção de formato de saída acima, p. ex. [CHAT], "
        "[WEB], [MUSIC], [MEME]), depois, a partir da linha seguinte, escreva apenas o que você "
        "diria a {master_name}; se não tiver nada novo a dizer, responda apenas [PASS]. Não "
        "repita nem explique nenhuma regra, não exiba listas ou raciocínio, e não exiba nada "
        "além da etiqueta e da mensagem."
    ),
}


def render_format_fix_instruction(lang: str, master_name: str = "") -> str:
    """Render the "format fix" self-rescue instruction. ``master_name`` defaults to a neutral placeholder."""
    short = _norm_lang(lang)
    template = PROACTIVE_FORMAT_FIX_INSTRUCTION.get(short) or PROACTIVE_FORMAT_FIX_INSTRUCTION['en']
    return template.format(master_name=master_name or _DEFAULT_ADDRESSEE.get(short, "them"))


# =====================================================================
# ======= Negative-keyword target check (RFC §3.4.5 Layer 2) ==========
# =====================================================================
# 职责：用户说"别提了 / 换个话题"这类话命中本地关键词后，派一次小 LLM 调
# 用决定"用户到底是在说哪条？还是只是泛化情绪？"。水印："======以上为".
#
# 历史位置：从 ``prompts_memory.py`` 迁过来——negative-intent prompt + 关键词
# 与本模块的 ban-topic regex/抽取 是同一类输入（"用户的负面 / 回避指令"），
# 集中在一处便于以后维护词表 / prompt 一致性。
# evidence 系统的接入点保持原样（``app/memory_server._amaybe_trigger_negative_keyword_hook``）。

NEGATIVE_TARGET_CHECK_PROMPT = {
    "zh": """你是一个用户回避意图判定专家。

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

用户消息里，"别提了 / 不想聊 / 换个话题 / 别再说"这类表达到底指上述哪一条？可能多条、也可能一条都没有（用户只是泛化情绪）。

只能从"观察列表"里选 target_id，不要凭空生成。
target_type 必须是字符串 "reflection" 或 "persona" 之一。

返回合法 JSON（如果用户只是泛化情绪，无明确 target，返回 {"targets": []}）：
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "简短理由"}]}""",
    # 分隔符水印在每一条 locale 里都是同一串简体字面量（与其余 prompt 表同理），
    # 繁中跟着走、不做转换——它是给模型认边界用的锚点，不是给用户看的文案。
    "zh-TW": """你是一個使用者迴避意圖判定專家。

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

使用者訊息裡，「別提了 / 不想聊 / 換個話題 / 別再說」這類表達到底指上述哪一條？可能多條、也可能一條都沒有（使用者只是泛化情緒）。

只能從「觀察列表」裡選 target_id，不要憑空產生。
target_type 必須是字串 "reflection" 或 "persona" 其中之一。

回傳合法 JSON（如果使用者只是泛化情緒，無明確 target，回傳 {"targets": []}）：
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "簡短理由"}]}""",
    "en": """You are a user pushback target analyst.

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

In the user's messages, when they say things like "don't mention / change the topic / stop talking about", which observation(s) above are they referring to? Could be several, or none at all (just a vague mood).

target_id MUST come from "observations" above — do not invent IDs.
target_type MUST be the literal string "reflection" or "persona".

Return valid JSON. If the user is just venting without a specific target, return an object with an empty `targets` array: {"targets": []}. Otherwise:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "short rationale"}]}""",
    "ja": """あなたはユーザーの拒否反応が何を指しているかを判定する専門家です。

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

ユーザーが「その話はいい／話題を変えて／やめて」などと言ったのは、上の観察のうちどれを指していますか？複数の場合もあれば、一つも該当しない場合もあります（単なるムード）。

target_id は必ず上の "観察" から選ぶこと。
target_type は文字列 "reflection" または "persona" のいずれかでなければならない。

有効な JSON で返す。該当なしの場合は targets を空配列に: {"targets": []}。
それ以外:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "短い理由"}]}""",
    "ko": """당신은 사용자의 거부 표현이 무엇을 가리키는지 판정하는 전문가입니다.

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

사용자가 "그 얘기는 그만 / 다른 이야기하자" 같은 표현을 쓸 때, 위 관찰 중 어떤 것을 가리킵니까? 여러 개일 수도, 전혀 없을 수도 있습니다.

target_id는 반드시 위 "관찰"에서 가져오세요.
target_type은 문자열 "reflection" 또는 "persona" 중 하나여야 합니다.

유효한 JSON으로 반환하세요. 해당 없음이면 targets를 빈 배열로: {"targets": []}.
그 외:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "짧은 이유"}]}""",
    "ru": """Вы эксперт по определению цели пользовательского отказа от темы.

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

Когда пользователь говорит "хватит об этом / сменим тему / не надо об этом", к каким из перечисленных наблюдений это относится? Может быть несколько или ни одного (просто эмоция).

target_id ДОЛЖЕН быть из "наблюдений" выше.
target_type ДОЛЖЕН быть строкой "reflection" или "persona".

Верните валидный JSON. Если конкретной цели нет — объект с пустым массивом `targets`: {"targets": []}. В противном случае:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "короткое обоснование"}]}""",
    "es": """Eres especialista en determinar el objetivo de una reacción de rechazo del usuario.

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

Cuando el usuario dice cosas como "no lo menciones / cambia de tema / deja de hablar de eso", ¿a cuál(es) de las observaciones de arriba se refiere? Puede ser varias o ninguna (solo un estado de ánimo general).

target_id DEBE venir de la "lista de observaciones" de arriba; no inventes IDs.
target_type DEBE ser literalmente "reflection" o "persona".

Devuelve JSON válido. Si no hay objetivo específico, devuelve {"targets": []}. Si lo hay:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "razón breve"}]}""",
    "pt": """Você é especialista em determinar o alvo de uma reação de recusa do usuário.

======以下为用户最近消息======
{USER_MESSAGES}
======以上为用户最近消息======

======以下为系统正在维护的观察列表======
{OBSERVATIONS}
======以上为观察列表======

Quando o usuário diz coisas como "não mencione / muda de assunto / pare de falar disso", a qual(is) observação(ões) acima ele se refere? Pode ser várias ou nenhuma (apenas um humor geral).

target_id DEVE vir da "lista de observações" acima; não invente IDs.
target_type DEVE ser literalmente "reflection" ou "persona".

Retorne JSON válido. Se não houver alvo específico, retorne {"targets": []}. Caso contrário:
{"targets": [{"target_type": "reflection",
              "target_id": "...",
              "reason": "motivo breve"}]}""",
}


def get_negative_target_check_prompt(lang: str = "zh") -> str:
    return _loc(NEGATIVE_TARGET_CHECK_PROMPT, lang)


# =====================================================================
# ======= Negative-keyword scanning (RFC §3.4.5 Layer 1) ==============
# =====================================================================
# 本地确定性 frozenset 扫描；命中后异步派发 Layer 2 LLM 判定。
# 目标语义：用户希望 AI 闭嘴 / 回避特定话题（包含"嫌烦"族，因为这类词用在
# 话题语境时基本都意味着"想结束这个话题"）。**不收纯情绪词**（焦虑/崩溃/
# 难受/失望/痛苦…）——它们经常单独出现而无回避意图，会触发无用 LLM 调用。
# 单字也避免（"烦"会被"麻烦你"/"麻烦了"误命中），双字以上更稳。
#
# zh 与 zh-TW 是**两块独立词表**，不是同一份的两种写法：这里拿词条去撞用户实际
# 打出来的字，繁简是不同码位，简体词条对繁中输入是 0 命中。两块逐条对应（含繁
# 简同形的那几条，照抄以便一侧改动时对照）。scan_negative_keywords 对整个 zh 系
# 扫两块的并集——见该函数的 docstring。
NEGATIVE_KEYWORDS_I18N: dict[str, frozenset[str]] = {
    "zh": frozenset(
        [
            # 显式回避型
            "别说了",
            "别再说",
            "不要再说",
            "不要说",
            "别提了",
            "别提",
            "别再提",
            "不要再提",
            "不想提",
            "不想再提",
            "不想说",
            "不想说了",
            "不想再说",
            "别讲",
            "别再讲",
            "不要讲",
            "不要再讲",
            "别聊",
            "别聊这个",
            "不要聊",
            "不想聊",
            "换个话题",
            "换话题",
            "聊点别的",
            "说点别的",
            "这个不用说了",
            "闭嘴",
            "别问了",
            "不要问了",
            # 嫌烦型（暗含"想结束此话题"）
            "烦死",
            "烦人",
            "好烦",
            "真烦",
            "烦透",
            "心烦",
            "讨厌",
            "真讨厌",
            "受不了",
            "无语",
            "真无语",
        ]
    ),
    "zh-TW": frozenset(
        [
            # 顯式迴避型
            "別說了",
            "別再說",
            "不要再說",
            "不要說",
            "別提了",
            "別提",
            "別再提",
            "不要再提",
            "不想提",
            "不想再提",
            "不想說",
            "不想說了",
            "不想再說",
            "別講",
            "別再講",
            "不要講",
            "不要再講",
            "別聊",
            "別聊這個",
            "不要聊",
            "不想聊",
            "換個話題",
            "換話題",
            "聊點別的",
            "說點別的",
            "這個不用說了",
            "閉嘴",
            "別問了",
            "不要問了",
            # 嫌煩型（暗含「想結束此話題」）
            "煩死",
            "煩人",
            "好煩",
            "真煩",
            "煩透",
            "心煩",
            "討厭",
            "真討厭",
            "受不了",
            "無語",
            "真無語",
        ]
    ),
    "en": frozenset(
        [
            # Explicit avoidance
            "stop talking about",
            "don't mention",
            "do not mention",
            "change the topic",
            "change the subject",
            "let's not discuss",
            "let's not talk about",
            "drop the subject",
            "drop it",
            "not this again",
            "shut up",
            "let it go",
            "move on",
            "enough of this",
            # Annoyance (implies "end this topic")
            # `hate` must stay multi-word — bare "hate" is a substring of common
            # words like "whatever" and would fire false positives every turn.
            "i hate",
            "hate this",
            "hate that",
            "hate it",
            "hate when",
            "annoying",
            "annoyed",
            "frustrating",
            "frustrated",
            "sick of",
        ]
    ),
    "ja": frozenset(
        [
            # 明示的な回避
            "その話は",
            "その話はもう",
            "その話やめ",
            "やめて",
            "話題を変えて",
            "別の話",
            "他の話",
            "言わないで",
            "黙って",
            # うんざり系（話題を終わらせたい含意）
            "もう嫌",
            "イライラ",
            "うざい",
            "しつこい",
        ]
    ),
    "ko": frozenset(
        [
            # 명시적 회피
            "그만하자",
            "그 얘기는 그만",
            "다른 이야기",
            "다른 얘기",
            "다른 얘기 하자",
            "말하지 마",
            "닥쳐",
            # 짜증 계열 (화제 종료 함의)
            "짜증",
            "싫어",
            "지긋지긋",
        ]
    ),
    "ru": frozenset(
        [
            # Явное избегание
            "хватит об этом",
            "сменим тему",
            "не говори об этом",
            "другая тема",
            "не надо об этом",
            "замолчи",
            "отстань",
            "хватит",
            # Раздражение (подразумевает «закроем тему»)
            "раздражает",
            "надоело",
            "достало",
        ]
    ),
    "es": frozenset(
        [
            "no hables",
            "no quiero hablar",
            "no quiero hablar de eso",
            "cambia de tema",
            "hablemos de otra cosa",
            "déjalo",
            "basta",
            "no lo menciones",
            "no sigas",
        ]
    ),
    "pt": frozenset(
        [
            "não fale",
            "não quero falar",
            "não quero falar disso",
            "mude de assunto",
            "vamos falar de outra coisa",
            "deixa pra lá",
            "chega",
            "não mencione isso",
            "não continue",
        ]
    ),
}


# 扫描侧的中文并集：预算一次存成常量。scan_negative_keywords 是每条用户消息都
# 跑的热路径（post_turn 每轮 × user_msgs 条数），写成函数里现 union 会每条消息
# 重建一个 80 元素 frozenset。
_ZH_SCAN_KEYWORDS: frozenset[str] = (
    NEGATIVE_KEYWORDS_I18N["zh"] | NEGATIVE_KEYWORDS_I18N["zh-TW"]
)


def scan_negative_keywords(message: str, lang: str = "zh") -> bool:
    """Fast path: case-insensitive substring scan against NEGATIVE_KEYWORDS_I18N.

    Returns True if the message contains any negation keyword for the given
    language; if lang is unknown, falls back to the Chinese union.

    ⚠️ Does NOT go through ``_norm_lang`` — that helper serves i18n template
    rendering, where unknown languages map to ``en`` (English is the lingua franca;
    defaulting template rendering to English is reasonable). This function's
    contract is "treat unrecognizable language as a Chinese user" (codex P2 /
    scan-only policy), a different policy from the render path. So only minimal
    normalization happens here: strip the region suffix (``en-US`` → ``en`` /
    ``zh-CN`` → ``zh``) and leave unrecognized short codes to the Chinese fallback.

    ⚠️ The whole Chinese family scans Simplified plus Traditional, not one script.
    Stripping the region suffix is what makes that necessary: by the time ``short``
    is computed below, ``zh-TW`` is indistinguishable from ``zh-CN``, so there is no
    ``zh-TW`` key left to look up and a per-locale lookup would leave that table as
    unreachable data. Scanning both is also right on its own terms — users mix
    scripts (pasting Simplified content into a Traditional UI, typing Traditional
    with a Simplified IME) — and this module prefers false positives: a miss means
    the model keeps stepping on the same landmine, while a false hit costs one
    cheap-tier background LLM call that comes back with no target.
    """
    if not message:
        return False
    # 只剥 region 后缀（zh-CN/zh_CN/en-US/pt-BR ...），保留契约："未知 → zh"。
    # 同时 strip 前后空白 + lower 大小写——上游若传 ``EN-US`` 或 ``" en-US "``，
    # split 后是 ``EN`` / `` en``，dict key 都是小写无空白会 miss → 错落 zh
    # 兜底（CodeRabbit Minor）。
    short = (lang or "").strip().lower().split('-', 1)[0].split('_', 1)[0]
    kws = NEGATIVE_KEYWORDS_I18N.get(short)
    # 判据必须是**归一化之后**的 short == "zh"：上一行已经把 region 剥掉了，
    # 任何在这里去看原始 lang 有没有 "tw" 的写法都是恒假分支。zh / zh-CN /
    # zh-TW / zh-Hant 以及全部未知语言都走并集，等于把"未知 → 当中文用户"
    # 这条既有契约原样扩到两套字形上。
    if kws is None or short == "zh":
        kws = _ZH_SCAN_KEYWORDS
    lower = message.lower()
    for kw in kws:
        if kw.lower() in lower:
            return True
    return False
