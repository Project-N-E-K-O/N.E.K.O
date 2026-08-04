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

from config.prompts._locale import normalize_prompt_locale, prompt_locale_fallback_key
from config.prompts.prompts_sys import _loc


# 抓到 term 后剥两端的字符：标点 + 各语言语气助词 / 修饰小尾巴。
# 全在尾部 strip，不影响中间内容。
_TRIM_TRAIL = (
    # ASCII / CJK 标点 / 空白
    " \t\n\r"
    ".,!?;:\"'`()[]{}<>"
    "。！？，；：、…—·"
    # ⚠️ 与 _ZH_BRACKET_PAIRS 成对：凡是被当作话题分隔符的括号，两端都必须在这里
    # 剥掉，否则 term 会带着括号存进去（`〔重要，紧急〕`）。有测试钉这条不变量。
    "“”‘’（）【】《》「」『』〈〉〔〕［］〖〗"
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
        # zh-TW / 台湾口语：简体语料里少见但台湾日常极常用——不补的话
        # "別再提工作喔" 存下来的 term 是 "工作喔"。
        # ⚠️ 三个字**整个不收**，正则放行组里也没有：
        #   ``唄``（``呗`` 的繁体）在日文里是"歌"，ban 一个日文歌名
        #     （"别再提花の唄了。"）会被削成 "花の"；
        #   ``耶`` / ``捏`` 是这批里唯二**同时也是常见词尾字**的（坎耶 / 拿捏 /
        #     揉捏），"别再提精准拿捏。" 会被削成非词 "精准拿"。
        # 判据是代价方向：收了它们，常见说法能拿到干净的 term（"工作耶"→"工作"），
        # 但罕见话题被腰斩成**非词**；不收，常见说法多带一个字（"工作耶"）——term
        # 里仍然完整含着真话题，模型对得上。宁可多一个字，不可少一个字。
        "喔", "囉", "啰", "唷", "齁", "欸", "誒", "咧", "喲",
    ),
    "ja": ("ね", "よ", "わ", "の", "って", "なんて", "という"),
    "ko": ("요", "은", "는", "이", "가", "을", "를", "에", "에서"),
}

_HAN_RE = re.compile(r"[一-鿿㐀-䶿]")
# 不含汉字 = 与汉字不共码位 = 任何 locale 带上它都不会撞。自动发现而不是手点名单：
# 以后加一张新的假名 / 谚文 / 西里尔助词表，它会自动进这个集合。
_SCRIPT_DISJOINT_FAMILIES = tuple(
    fam
    for fam, toks in _TRIM_TRAIL_TOKENS_BY_LOCALE.items()
    if not any(_HAN_RE.search(tok) for tok in toks)
)

# 反问尾巴：跟在句末助词后面（"工作了好嗎"），正则的可选助词组只放行一个，
# 剩下的会并进 term，靠 trim 的循环逐层剥掉。
#
# ⚠️ 单列一张表、且**只在 term 不带括号时**才剥。这几个短语同时也是大量作品名的
# 结尾（《我們好不好》/《最近你好嗎》），而剥括号发生在剥语气词之前——不设条件的
# 话 "别再提电影《我们好不好》。" 存下来的是 "电影《我们"，把标题腰斩成非词
# （codex P2）。
#
# 判据这一维是干净闭集：**原始 term 里有没有出现过括号字符**。带括号 = 里面是被
# 引用的专名，专名尾部的 "好不好" 是名字的一部分；不带括号才是说话人的反问语气。
# 反过来"哪些短语可能是作品名结尾"是开集，枚举不干净。
# ⚠️ 裸的 ``吗 / 嗎`` 也要收：``別再提工作嗎？`` 存成 ``工作嗎``、``不要再說工作了嗎？``
# 存成 ``工作了嗎``（后者连 ``了`` 都剥不掉，因为剥到 ``嗎`` 就停了）。它们和上面那批
# 多字短语一样受引号判据保护，``《你可以吗》`` 不会被腰斩（codex P2）。
_TAIL_INTERROGATIVES_BY_LOCALE: dict[str, Tuple[str, ...]] = {
    "zh": ("好吗", "好嗎", "好不好", "可以吗", "可以嗎", "行吗", "行嗎", "吗", "嗎"),
}

# ⚠️ 没有自己 CJK 助词表的 locale（en / ru / es / pt）回落到 **zh + ja** 的并集：
# 混合语言是这个模块明确支持的路径（"stop talking about 前女友了" / "stop saying
# 仕事ね"），term 整段可能是中文也可能是日文，不剥就把助词原样存进去（codex P2）。
# 分表要隔离的是 zh/ja/ko **互相**污染——它们命中时用自己那张表，不受这里影响。
# ⚠️ 这里只列 zh：假名 / 谚文那两张表由 _trim_term 无条件追加（见
# _SCRIPT_DISJOINT_FAMILIES），列在这儿反而是一份会漂移的冗余。
_TRIM_TRAIL_FALLBACK_LOCALES = ("zh",)

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


# 可存储 term 的长度区间。``_trim_term`` 也要知道下限：把 term 剥到低于下限，结果
# 是整条指令被丢弃，那还不如把有歧义的那个尾字留着（见 _trim_term）。
_TERM_MIN_LEN = 2
_TERM_MAX_LEN = 40


def _trim_term(term: str, locale: str = "") -> str:
    """Trim a term: strip trailing particles/modifiers first, then surrounding punctuation + whitespace.

    ``locale`` selects which language's particle list applies, because the same
    codepoint is a different word per language: ``U+5504`` is a Chinese sentence
    particle and the Japanese word for "song". Locales with no CJK list of their
    own (en / ru / es / pt) fall back to Chinese — mixed-code input carries a
    Chinese tail far more often than any other. ASCII tails are always stripped.

    Stripping never takes a term below ``_TERM_MIN_LEN``. Several particles are
    also ordinary word-final characters (拿捏 / 坎耶 / 好咧), and there is no local
    rule that tells the two readings apart. When the choice is "particle reading →
    term too short → the whole directive is dropped" versus "keep the character",
    keeping it is the only option that preserves anything at all.
    """  # noqa: DOCSTRING_CJK
    if not term:
        return ""
    # 走 config.prompts._locale 的公共归一化，不自己剥 region 后缀：
    # tests/unit/test_prompt_locale_normalizer.py 明确禁止在 _locale.py 之外手写
    # locale 匹配（手写的那六份正是 "esperanto" 被当成西语、Steam 码掉进英文的来源）。
    family = normalize_prompt_locale(
        locale, default="", simplified="zh", keep_traditional=False,
    )
    families = (
        (family,)
        if family in _TRIM_TRAIL_TOKENS_BY_LOCALE
        else _TRIM_TRAIL_FALLBACK_LOCALES
    )
    # ⚠️ **不含汉字**的那些助词表，每个 locale 都带上——自动发现，不点名。
    #
    # 分表是为了拆开 CJK 之间的同码位歧义（``唄`` 中文语气词 / 日文"歌"，``了`` 是
    # 日文 完了/終了 的构词成分），而那种歧义只可能发生在**汉字**上。谚文、假名与
    # 汉字都不共码位，任何 locale 带上它们都不会撞。只带命中 locale 自己那张的话：
    #   ``别再提전남친은。``  存成 ``전남친은``（base 是 ``전남친``）
    #   ``别再提仕事ね。``    存成 ``仕事ね``（base 是 ``仕事``）
    # 都是 codex P2。locale 说的是**哪条模板命中**，不是话题本身是什么语言。
    families = tuple(dict.fromkeys(families + _SCRIPT_DISJOINT_FAMILIES))
    cjk = tuple(
        tok for fam in families for tok in _TRIM_TRAIL_TOKENS_BY_LOCALE[fam]
    )
    interrogatives = tuple(
        tok for fam in families for tok in _TAIL_INTERROGATIVES_BY_LOCALE.get(fam, ())
    )
    # 引号里的语气词判据对**所有** CJK 尾词生效，不只多字反问短语：单字的也一样是
    # 名字的一部分（``《想見你喔》`` / ``《就是愛唷》``，codex P2）。ASCII 的
    # please / porfa 不受这条约束——它们不会出现在 CJK 作品名里。
    gated = cjk + interrogatives
    tokens = _TRIM_TRAIL_TOKENS_ANY + gated
    # ⚠️ 长度下限只保护**有歧义**的那批：``耶 / 捏 / 咧`` 同时也是常见词尾字（坎耶 /
    # 拿捏 / 好咧），剥到存不下等于整条指令被丢，那还不如留着。而 ASCII 的 please /
    # porfa、假名、谚文都不可能是中文词的一部分——对它们套下限的话
    # ``別再提錢please。`` 会存成 ``錢please``，而 parent 是剥掉之后按长度丢弃
    # （codex P2）。判据：token 里含汉字才算有歧义。
    ambiguous = frozenset(tok for tok in gated if _HAN_RE.search(tok))
    original = term.strip()
    quoted_until = _zh_quoted_span_end(original)
    s = original
    # 剥掉的尾部字符数。⚠️ 判据要的是尾词在**原始 term** 里的位置，而 s 会被两端反复
    # 削短；只有右端的削减会影响绝对下标，所以只需累计这一侧。
    right_removed = 0
    changed = True
    # 反复剥尾词，直到稳定（"了啊吧" 这种连续助词）
    while changed:
        changed = False
        for tok in tokens:
            if not s.endswith(tok):
                continue
            trimmed = s[: -len(tok)]
            shorter = trimmed.rstrip()
            # 剥到低于下限 = 整条指令被丢；**有歧义的**尾字宁可留着（codex P2）。
            if len(shorter) < _TERM_MIN_LEN and tok in ambiguous:
                continue
            # 语气词 / 反问短语同时也是大量作品名的结尾，所以只在它**落在引号之外**
            # 时才剥。判据是这个尾词在原 term 里的起点有没有越过最后一段括号的收尾：
            #   ``《最近你好嗎》``        → ``好嗎`` 在括号内，剥了就腰斩标题；
            #   ``《你好》好嗎``          → 越过了收尾括号，是句子级语气，该剥；
            #   ``電影《你好》續集好嗎``  → 同样越过了，中间隔着普通修饰词也一样该剥。
            # ⚠️ 早先用的代理判据是「剥完之后前缀是不是正好以收尾括号结尾」，第三行
            # 就判错（codex P2）——中间隔一个 ``續集`` 它就不认了。
            tail_start = len(original) - right_removed - len(tok)
            if tok in gated and tail_start < quoted_until:
                continue
            right_removed += len(s) - len(shorter)
            s = shorter
            changed = True
        # 同时剥两端标点
        new_s = s.strip(_TRIM_TRAIL)
        if new_s != s:
            right_removed += len(s) - len(s.rstrip(_TRIM_TRAIL))
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
# 退休 / 午休 / 调休 全是复合词。所以只在词首认它，不做字符枚举。
# ⚠️ 再排掉 ``休講``：词首规则拦不住句首的它（"休講だって。" / "休講情報。"），而
# ``講`` 是本 PR 新加的动词，等于给整条模板 1 开了个日文入口。中文没人写 "休講"
# ——这个否定用法在现代中文里只剩 "休提 / 休想"（对抗排查）。
_XIU_COMPOUND_LEFT = "退午调調补補年病公轮輪全双雙不歇罢罷特半"
_ZH_XIU = f"(?<![{_XIU_COMPOUND_LEFT}])休(?!講)"

# ⚠️ 否定词是**闭集**，而且 _ZH_NEG 与下面三条日文守卫的证据正则必须用同一份源。
# 手抄了四份的时候，往 _ZH_NEG 加一个词（比如 ``勿``）而忘了同步证据，中文侧照常
# 工作、但 ``勿提君の名は。`` 会被日文守卫整条吞掉，而同结构的 ``莫提君の名は。``
# 不会——同一模板内否定词之间的行为不对称，且全文件没有一条测试会红。
#
# 单字与多字分开：日文的 ``〜別`` 后缀问题只存在于单字（见 _ZH_NEG_VERB_EVIDENCE
# 的左界注释），多字的 ``不要 / 不准`` 不可能是日文名词后缀。
_ZH_NEG_SINGLES = ("别", "別", "莫", "休", "甭")
_ZH_NEG_MULTIS = ("不要", "不许", "不許", "不准")
# 正则里用的单字形态带各自的复合词守卫（_ZH_BIE 的左界 / _ZH_XIU 的 ``(?!講)``）；
# 证据正则用的是上面那份**裸字形**，它自己另配左界。
_ZH_NEG_SINGLES_GUARDED = (_ZH_BIE, "莫", _ZH_XIU, "甭")
# ⚠️ ``休`` 在证据里仍要排掉 ``講``：``休講``（日文＝停课）本身会命中「否定词 +
# 言说动词」这条结构证据。端到端上它不可达（_ZH_XIU 的 ``(?!講)`` 让 ``休講…`` 根本
# 产不出 zh 匹配），所以一度当死分支删掉过；但**证据正则自己不许在日文语料里命中**
# 是一条更强、也更该守的性质——有测试按分支逐条扫。见 _ZH_NEG_UNAMBIGUOUS。
_ZH_NEG = (
    "(?:" + "|".join(_ZH_NEG_SINGLES_GUARDED + _ZH_NEG_MULTIS) + ")"
)

# ---------------------------------------------------------------------------
# 言说动词表 —— **生成**，不手写
# ---------------------------------------------------------------------------
# ⚠️ 手写清单在这里失败过两次：模板 1 的宾语是 ``(.{1,40}?)``，什么都能吃，所以
# 单字 ``提`` 一旦匹配成功，剩余部分整体成功，正则**没有理由**回溯去试 ``提起``。
# 于是复合动词必须排在它的单字前缀之前。
#
# ⚠️ 这里**不**做「言说动词 × 结果补语」的笛卡尔积。曾经把 ``到 / 起 / 及`` 当补语
# 一并吃掉，理由是 ``別提到我前女友。`` 该存 ``我前女友`` 而不是 ``到我前女友``。
# 但 ``提／到达时间`` 和 ``提到／达时间`` 是同一串字，两种切分都合语法，局部没有任何
# 规则能分开——于是 ``别提到达时间。`` 存成 ``达时间``、``别聊起点问题。`` 存成
# ``点问题``、``别提及格线的事。`` 存成 ``格线的事``（codex P2，简体也回归）。
# 「以 到/起/及 开头的词」是开集，枚不干净；而代价是不对称的：多留一个 ``到``，
# term 里仍然完整含着真话题，模型对得上；吃掉一个字，存进去的是非词。
_ZH_SAY_VERBS = ("说", "說", "提", "聊", "讲", "講", "谈", "談", "扯")
# 双字言说动词。⚠️ 只收**单字拆不开**的那些：``讨`` 在现代汉语里不能单用（"别讨政治"
# 不成话），所以 ``讨论`` 必须整体进表；它吃掉以 ``论`` 开头的话题（"别讨论文格式。"
# → ``文格式``）是没法避免的代价，base 也一样。
#
# ⚠️ ``谈论 / 談論`` **不收**：``谈`` 本身就是独立动词，把 ``谈论`` 当复合词是可选的，
# 而代价是实打实的——``别谈论语考试。`` 被削成 ``语考试``、``别谈论语。`` 整条消失
# （base 分别是 ``论语考试`` / ``论语``；codex P2）。以 ``论`` 开头的话题是开集
# （论语 / 论文 / 论坛 / 论证 / 论政治），跟结果补语那条是同一族问题：多留一个
# ``论`` 话题仍完整，吃掉一个字存进去的是非词。
_ZH_SAY_COMPOUNDS = ("讨论", "討論")
# 称呼类：结构是「动词 + 我 + 称呼」，与上面两族都不同
_ZH_ADDRESS_VERBS = ("管我叫", "称呼我为?", "稱呼我為?", "喊我", "叫我")


def _zh_verb_alternation(*, with_address: bool) -> str:
    """Build the verb alternation, compounds always ahead of their single-char prefix."""
    parts = list(_ZH_SAY_COMPOUNDS)
    if with_address:
        parts += list(_ZH_ADDRESS_VERBS)
    parts += list(_ZH_SAY_VERBS)
    # ⚠️ 原子组：复合动词排在单字前缀之前只解决了"谁先匹配"，解决不了**回溯**。
    # ``别提起了。`` 里 ``提起`` 先中，但后面只剩 ``了`` 凑不够两个单位的宾语，
    # 引擎就退回 ``提``、把 ``起了`` 当成话题存下来——而这个模块的 docstring 明确
    # 说不抽无宾语的指令（codex P2）。原子化之后动词一旦选定就不再回头，整条匹配
    # 直接失败，正是想要的结果。
    return "(?>" + "|".join(parts) + ")"


_ZH_VERBS_WITH_ADDRESS = _zh_verb_alternation(with_address=True)
_ZH_VERBS_PLAIN = _zh_verb_alternation(with_address=False)

# 模板 2 / 4 尾部会跟一个填充词（``就`` / ``的事`` / ``这个``…），它属于句子而不属于
# 话题。⚠️ 这个不能在正则里"顺手吃掉"：前缀是 lazy 的，正则会优先把**话题的最后一个
# 字**塞进可选组——"他的成就别提了。" 存成 "他的成"。用左界字符黑名单挡也不行，
# ``就`` 作为词尾是开集（成就 / 迁就 / 功成名就 / 一蹴而就 / 練就 / 鑄就…），漏一个
# 就腰斩一个真实话题（codex P2）。
#
# 改成在**抽完之后**比对：一条 term 如果正好等于"另一条 term + 一个填充词"，那它就是
# 同一个话题多带了个尾巴，丢掉长的。这条判据不猜词边界，只看同一句话里实际抽出了
# 什么——"股票就" 会因为 "股票" 也在结果里而被丢，"功成名就" 因为没有 "功成名"
# 这条 term 而保留。
_ZH_TRAILING_FILLERS = (
    "就", "的事", "的", "这个", "這個", "这事", "這事",
    "这话题", "這話題", "这件事", "這件事",
)

# 话题里允许出现的**一个单位**。四条 zh 模板共用一份，别再各写各的。
#
# ⚠️ 换行必须**显式**排除：原先这些捕获组写的是 ``.``，在没有 DOTALL 时天然不匹配
# 换行；换成负字符类之后这个性质就没了，多行消息里 term 会把换行连同**下一条指令**
# 一起吞掉（"别提工作\n别提加班" → term "工作\n别提加班"）。
#
# ⚠️ 句读也要排除，否则捕获会跨过本该收尾的标点去够更长的匹配
# （"功成名就别提了，功成名别提了。" → "了，功成名别提"）。但**书名号 / 引号里的
# 标点属于话题本身**：`电影《你好，李焕英》别提了。` 的逗号在片名里，一刀切排除会
# 把 term 截成 "李焕英"（codex P2）。所以一个"单位"是「非句读非换行的单字」**或**
# 「一整段配对括起来的内容」——括号内不限标点，但不许跨行。
_ZH_BRACKET_PAIRS = (
    ("《", "》"), ("「", "」"), ("『", "』"), ("“", "”"), ("【", "】"),
    ("（", "）"), ("〈", "〉"), ("〔", "〕"), ("［", "］"), ("〖", "〗"),
    # ASCII 也要收：``"Everything, Everywhere"别提了。`` / ``电影(Hello, World)别提了。``
    # 在 parent 上是完整的，不收就成了回归（codex P2）。
    # ⚠️ 不收单引号 ``'``：英文里它是词内撇号（don't / it's），配对没有意义。
    # ⚠️ ASCII 方括号也要收：``_TRIM_TRAIL`` 本来就把它们当两端分隔符剥，却没进配对
    # 表，于是 ``[Hello, World]别提了。`` 在逗号处被截成 ``World``（codex P2）。
    ('"', '"'), ("(", ")"), ("[", "]"),
)
def _zh_bracket_body(lo: str, hi: str) -> str:
    """One bracketed run: bounded body, and symmetric pairs never span a sentence."""
    banned = re.escape(hi) + "\\r\\n"
    unit = f"[^{banned}]"
    if lo == hi:
        # ⚠️ 对称的一对（只有 ASCII ``"``）不能跨句读配对。孤立的双引号很常见——
        # 英寸号、代码片段——两个不相干的句子各带一个就会被当成一整段引文：
        # ``尺寸5"别提了。尺寸6"别提了。`` 被并成一条 ``尺寸5"别提了。尺寸6``，两条
        # 指令全丢（codex P2）。非对称括号没有这个问题（``《`` 不会被误当收尾）。
        banned += "。！？；.!?;"
        # ⚠️ 逗号也是本模块的指令终结符，但**不能**跟着一起排除：真作品名里带逗号的
        # 不少（``"Everything, Everywhere"``），排掉就是另一条回归。改成 temper 掉
        # ``别/別``——引文里包住一整条指令必然要带否定词，而作品名带否定词的同时又
        # 带标点才会受影响（不带标点的 ``"再别康桥"`` 走单字分支，照样完整）。
        # 这样 ``尺寸5"别提了，尺寸6"别提了。`` 配不上对，两条指令都留下（codex P2）。
        unit = f"(?![别別]){unit}"
    # ⚠️ 长度必须**有界**：无界的 ``*`` 在每个开括号处都会扫到串尾去找收尾，
    # ``"《" * 8000`` 这种输入就是二次方——实测 2.6 秒，而 record_from_text 是在
    # 用户每条消息上同步跑的（codex P2）。上界取 _TERM_MAX_LEN：比它长的括号段
    # 无论如何都会被末尾的长度过滤丢掉，收紧不损失任何能存下来的 term。
    return f"{re.escape(lo)}(?:{unit}){{0,{_TERM_MAX_LEN}}}{re.escape(hi)}"


_ZH_BRACKET_RUN = "(?:" + "|".join(
    _zh_bracket_body(lo, hi) for lo, hi in _ZH_BRACKET_PAIRS
) + ")"

_ZH_BRACKET_RUN_RE = re.compile(_ZH_BRACKET_RUN)
# ⚠️ **对称**的那一对（只有 ASCII ``"``）不算「未闭合的开括号」：落单的双引号是
# 英寸号、颜文字 ``:(`` 这类普通字符，_zh_bracket_body 已经这么决定过一次
# （"比把开括号排除出单字分支更好的地方"那段）。这里再把它当硬边界就是自相矛盾——
# ``别再提5"屏幕好吗。`` 会因为那个英寸号被判成「整段都在引号内」而留着 ``好吗``
# （codex P2）。非对称的 ``《`` 落单时确实是没写完的引文，仍然算。
_ZH_BRACKET_OPEN_CHARS = frozenset(
    lo for lo, hi in _ZH_BRACKET_PAIRS if lo != hi
)
_ZH_CLOSE_FOR_OPEN = {lo: hi for lo, hi in _ZH_BRACKET_PAIRS if lo != hi}
_ZH_SYMMETRIC_DELIMS = frozenset(lo for lo, hi in _ZH_BRACKET_PAIRS if lo == hi)


def _zh_quoted_span_end(text: str) -> int:
    """Index past the last quoted run: a tail starting there is outside the quotes.

    ⚠️ ``_trim_term`` 拿它判「这个语气词是句子的还是作品名的一部分」。用「前缀是不是
    正好以收尾括号结尾」当代理判据是不够的——``電影《你好》續集好嗎`` 中间隔了一个
    普通修饰词就判错（codex P2）。
    """  # noqa: DOCSTRING_CJK
    # ⚠️ 按**深度**扫，不能拿 _ZH_BRACKET_RUN_RE 找「最后一段完整引文」：正则会把
    # 外层的开括号跟**内层**的收尾配成一对，同种括号嵌套时就记错了收尾位置——
    # ``《电影《你好吗》续集好吗》`` 会被当成到内层 ``》`` 为止，末尾的 ``好吗`` 被
    # 当句子级语气剥掉（codex P2）。
    end = 0
    stack: List[str] = []
    symmetric_open: str | None = None
    for index, char in enumerate(text):
        if symmetric_open is not None:
            if char == symmetric_open:
                symmetric_open = None
                if not stack:
                    end = index + 1
            continue
        if stack and char == _ZH_CLOSE_FOR_OPEN[stack[-1]]:
            stack.pop()
            if not stack:
                end = index + 1
        elif char in _ZH_CLOSE_FOR_OPEN:
            stack.append(char)
        elif char in _ZH_SYMMETRIC_DELIMS:
            symmetric_open = char
    # 还剩没闭合的**非对称**开括号 = 有一段引文一直延伸到末尾（``电影《好不好``）。
    # ⚠️ 落单的对称引号不算——它是英寸号 / 颜文字，见 _ZH_BRACKET_OPEN_CHARS 的注释。
    if stack:
        return len(text)
    return end


_ZH_PLAIN_CHAR = r"[^，。！？；,.!?;\r\n]"
# ⚠️ 只有模板 2 的**前置**话题 temper 掉裸的 ``关于/關於``：``关于 X 就别提了`` 归
# 模板 4 管，前缀能逐字吃过它的话，"我觉得关于股票就别再讲了" 会多产出一条
# ``我觉得关于股票就``。
#
# ⚠️ 这条**不能**放进共用的单字分支。放进去就变成"话题里任何位置都不许出现关于"，
# 把动宾结构的宾语一起毙了：``别再提关于公司的传闻。`` 从 ``关于公司的传闻`` 变成
# 完全不命中（codex P2）。前置话题与动词后宾语是两种结构，守卫只属于前者。
_ZH_PLAIN_CHAR_NO_GUANYU = r"(?!关于|關於)" + _ZH_PLAIN_CHAR

# ⚠️⚠️ 整个"单位"必须是**原子**的，否则是一条 ReDoS。单字分支也能匹配 ``《``，于是
# ``《a》`` 既可以被括号分支整体吃掉、也可以被单字分支逐字吃掉——这个歧义放进
# ``{2,30}?`` 的重复里就是指数级回溯：``别提`` + 30 段 ``《a》`` 要跑 1.3 秒，而
# 这条路径是**每条用户消息**都会走的（codex P1）。
#
# 原子组 ``(?>…)``（Python 3.11+）让"这个位置选哪个分支"一旦定下就不再回头，歧义
# 消失。比"把开括号排除出单字分支"更好的地方：落单的 ``"`` / ``(`` （英寸号、颜
# 文字 ``:(``）仍然能被当成普通字吃进话题，不会变成硬边界。
# ⚠️ ASCII 的 ``.`` / ``,`` 夹在词字符中间时是**标识符内部**的，不是句读：
# ``Python 3.11别提了。`` / ``example.com别提了。`` / ``价格1,000元别提了。`` 上一版
# 分别只存下 ``11`` / ``com`` / ``000元``，base 三条都是完整的（codex P2）。
#
# ⚠️ 两侧的判据是**否定式**（不是空白、不是句读），不是「列出哪些字算词字符」。
# 列举法修了一轮又一轮都还有漏：``[0-9A-Za-z]`` 漏掉 ``café.com`` / ``Дом.ру`` /
# 全角 ``１,０００`` / IDN ``例子.测试``；换成 ``\w`` 之后**组合符号**仍然漏——NFD
# 分解形的 ``café.com``（e + U+0301）和天城文 ``देवनागरी.com`` 照样被截成 ``com``，
# 因为 Python 的 ``\w`` 不含 Mn/Mc 类（codex P2 第三轮）。
#
# 否定式一次覆盖全部：``.`` / ``,`` 只要**两边都不是空白也不是句读**，它就在
# 词内部。全角逗号另配一条更紧的规则，见下面。⚠️ 全角句号 ``。`` 完全不收——没有
# 标识符用它，而它是最常见的句子终结符。
# 句尾的 ``.`` 后面是空白或串尾，右侧那条前视要求必须有字符，所以不满足。
#
# ⚠️ 放宽到 ``\w`` **不会**把两条指令并成一条：``别提工作.别提加班.`` 仍然是两条。
# 宾语是 lazy 的，而终结符分支里本来就有 ASCII ``.``——引擎先试短的那条，`.` 照样
# 结束一条指令；只有当短的凑不出合法匹配时才会把 ``.`` 吃进话题。实测确认过。
_ZH_IDENT_PUNCT = (
    r"(?<=[^\s，。！？；,.!?;])[.,](?=[^\s，。！？；,.!?;])"
    # ⚠️ 全角逗号只在**数字之间**放行。它是中文最常见的分句符，无条件当词内字符
    # 会让前置话题跨小句（``算了，别提工作。`` 存成 ``算了，工作``）；而它真正的
    # 标识符用途就是千分位 ``价格1，000元``——限定两侧是数字就够，不需要更宽。
    r"|(?<=[0-9０-９])，(?=[0-9０-９])"
)

_ZH_TOPIC_CHAR = f"(?>{_ZH_BRACKET_RUN}|{_ZH_IDENT_PUNCT}|{_ZH_PLAIN_CHAR})"
_ZH_TOPIC_CHAR_NO_GUANYU = (
    f"(?>{_ZH_BRACKET_RUN}|{_ZH_IDENT_PUNCT}|{_ZH_PLAIN_CHAR_NO_GUANYU})"
)


def _zh_topic(minimum: int, maximum: int, *, block_guanyu: bool = False) -> str:
    """Topic capture body: ``minimum`` units, except a single bracketed run counts.

    ⚠️ 一整段括起来的内容算**一个**单位，所以 ``{2,30}`` 会把独立成句的
    ``《你好，李焕英》别提了。`` 卡掉（只有 1 个单位）。但它本身就 ≥3 个字符，
    长度闸根本不会丢它——所以单独放行"以一段括号开头"的形态（codex P2）。

    ``block_guanyu`` 只给模板 2 的前置话题用，见 _ZH_PLAIN_CHAR_NO_GUANYU。
    """  # noqa: DOCSTRING_CJK
    unit = _ZH_TOPIC_CHAR_NO_GUANYU if block_guanyu else _ZH_TOPIC_CHAR
    return (
        f"(?:{_ZH_BRACKET_RUN}{unit}{{0,{maximum - 1}}}?"
        f"|{unit}{{{minimum},{maximum}}}?)"
    )

# 模板 1 里 term 与终结符之间允许出现的句末助词。与 ``_TRIM_TRAIL_TOKENS`` 的 zh
# 段成对：这里放行、那里剥掉，少一边 term 就带着助词存进去。
# ⚠️ ``唄`` 整个不收（正则和 trim 都不收）。它是 ``呗`` 的繁体，但在日文里是"歌"
# （子守唄 / 花の唄）。留在 trim 表里会削掉 ja 模板的 term，只留在这里也一样会削掉
# zh 模板的 term——"别再提花の唄。" 存成 "花の"（codex P2 两轮）。而 ``呗`` 本来
# 就是北方口语词、台湾并不说 ``唄``，为它承担这个代价不划算。
# parent 就有的那一批。⚠️ 单列出来是给 _ZH_OBJECTLESS_AHEAD 用的，见那里的注释：
# 无宾语判据只认这一批，认了本 PR 新加的那批就会把 ``别再提好咧。`` 一起毙掉。
_ZH_BASE_FINAL_PARTICLES = "(?:\\s*(?:了|啊|呀|嘛|哦|呗|吧|啦|呢))"
_ZH_FINAL_PARTICLES = (
    "(?:\\s*(?:了|啊|呀|嘛|哦|呗|吧|啦|呢|喔|囉|啰|唷|齁|欸|誒|咧|喲))"
)

# 动词和宾语之间的停顿标点：打字和 ASR 都会产生（``别再提，工作。`` / ``别叫我，"笨蛋"。``）。
# parent 靠 ``.{1,40}?`` 把它吃进话题、再由 _trim_term 剥掉；本 PR 把句读排除出话题
# 单位之后，这类指令整条 0 命中（codex P2）。放在**捕获组之外**，不进 term。
# ⚠️ 必须排在 _ZH_OBJECTLESS_AHEAD **之前**：那道前视认「动词之后直接是句读」
# ＝没有宾语，分隔符没先吃掉的话 ``别再提，工作。`` 会被它整条否掉。
# ⚠️ ``、 ：`` 也要收。一度以为它们不用收——它们确实不在话题字符类的排除表里，会被当
# 普通字吃进话题再由 trim 剥掉，``别再提、工作。`` 靠这条也能出 ``工作``。但话题很短
# 且以**有歧义的尾字**结尾时就不成立了：``别提：好咧。`` 的话题变成 ``：好``（标点凑
# 满了两个单位的下限），``咧`` 被可选助词组吃掉，trim 完只剩一个字 ``好`` 被丢弃——
# parent 存的是 ``好咧``（codex P2）。把它们挡在捕获组外面，就不会去凑下限。
_ZH_TOPIC_SEPARATOR = r"(?:[，、：,:]\s*)?"

# 无宾语指令的前视：动词之后到句读之间，如果只剩**一个字 + 句末助词**，那个字是
# 结果补语（说完 / 提上 / 聊死）而不是宾语，整条不该抽——本模块的 docstring 明确说
# 不抽无宾语的指令。
#
# ⚠️ 这条是**独立于宾语下限**的一道闸，不能靠把下限降到 1 来代替。下限 1 会让
# lazy 宾语 + 可选助词组优先把话题末字当助词：``别提钱的事。`` 的宾语退化成 ``钱``、
# 撞长度下限后整条消失，``别再提好咧。`` 同理（这两条正是本 PR 修掉的 base 缺陷）。
# 下限 2 保住它们，这条前视单独负责 ``别说完了。`` 不被造出 ``完了`` 这种宾语。
#
# ⚠️ 判据是**「一个字」这个数量**，不是「哪些字是补语」。一度只列了 到/起/及，
# 于是 ``别说完了。`` → ``完了``、``别提上了。`` → ``上了``、``别聊死了。`` →
# ``死了``（codex P2 报了 完，实测 光/够/死/上 一样中招）。汉语的结果补语是个开集，
# 枚不干净；而「宾语只有一个字」这件事 parent 本来就一律丢弃（长度过滤），所以按
# 数量判既覆盖全、又与 parent 完全等价。
#
# ⚠️ 助词只认 parent 就有的那批（_ZH_BASE_FINAL_PARTICLES）。认上本 PR 新加的
# 台湾口语助词，``别再提好咧。`` 会被判成「好 + 咧」而整条毙掉——而 parent 存的是
# ``好咧``。那批字同时也是常见词尾字，正是当初决定「宁可多一个字」的那批。
#
# ⚠️ 也不能把 ``的事`` 放进来：``别提钱的事。`` 会变成「钱 + 的事」被毙掉。
_ZH_OBJECTLESS_AHEAD = (
    "(?!"
    + r"\s*" + _ZH_PLAIN_CHAR + "?"
    + _ZH_BASE_FINAL_PARTICLES + "{0,3}"
    + r"\s*(?:[，。！？；,.!?;]|$)"
    + ")"
)

# (2) 假名。⚠️ 不能简单地"命中区间有假名就丢"——被 ban 的**对象本身**经常是日文
# 专有名词（"别再提ドラえもん"、"別叫我お兄ちゃん"），那种句子结构是中文的，假名
# 只是话题名，丢掉等于把用户明确说过的偏好扔了（codex P2）。
# 所以要求三个条件同时成立才判为"这是日文句子"：
_KANA_RE = re.compile(r"[぀-ゟ゠-ヿｦ-ﾟ]")

# (2a) 中文的正面证据：这些字形/组合在现代日文里不存在——简体字形、与日文新字体
# 分道的繁体字形（說/説、這、關/関、沒/没、稱/称）、以及日文不会出现的组合
# （叫我 / 不想 …）。命中区间里出现任意一个，这句就是中文，假名是话题名。
# ⚠️ 不收 ``不要``：它本身是日文词（ふよう），"不要提出書類" 这种会被当成中文证据。
# ⚠️ 收字之前必须逐个核对日文新字体：``没`` 和 ``称`` 曾经被误收——它们**就是**
# 日文的标准字形（没収 / 名称），而不是 ``沒`` / ``稱`` 的简体专用形。收错的代价
# 是把守卫整个短路掉：``地域別講座の名称を確認します。`` 因为含 ``称`` 被判成中文，
# 助词判据根本没机会跑（codex P2）。下面每个字都对应一个不同的日文字形：
# 别→別 说→説 讲→講 谈→談 讨→討 论→論 关→関 话→話 题→題 愿→願 懒→懶 许→許
# 为→為；這 / 甭 日文没有；說 / 關 / 沒 / 稱 是与日文分道的繁体形。
#
# ⚠️ 光靠单字覆盖不到 _ZH_NEG 的全部否定词：``不准 / 莫 / 休 / 不要`` 一个字都不在
# 上面的字类里（``不許`` 的繁体 ``許`` 也不在），于是 ``不准提君の名は。`` 被判成日文
# 句子、整条丢掉——parent 上它是好的（codex P2；``不许`` 侥幸活着只因为 ``许`` 恰好
# 在字类里，纯属巧合）。
#
# 补法是**结构**而不是再往字类里塞共用汉字：「否定词 + 可选的再 + 言说动词」这两维
# 都是闭集，且两者相邻这件事本身就是中文句法——日文不会出现 ``不准提`` 这种相邻。
# 往字类里加 ``准 / 莫 / 休`` 反而会短路守卫（``没`` / ``称`` 就是这么错过两轮的）。
#
# ⚠️⚠️ 两道边界都要，这是**实测踩到的**（本文件的 ja 语料直接抓出来的）：
#
# (a) 左界：``別`` 在日文里是后缀「按…分」——地域別 / 部門別 / 商品別 / 年齢別 /
#     性別，后面接名词。``地域別提案をお願いします。`` 于是满足「否定 + 言说动词」，
#     整句被判成中文、存下 ``案をお願いします``。``地域別談話でも可。`` 同理。
#     一度以为只有 ``講`` 会撞（休講 / 特別講座），语料证明 ``提`` / ``談`` 一样撞。
#     ⚠️「哪些名词后面能接 別」是开集（_BIE_COMPOUND_LEFT 那张表拦不住 地域別），
#     但**日文里 別 永远贴在汉字后面**，而中文的否定词前面是句首、代词或标点。
#     所以判据取「前一个字符不是汉字、也不是假名」——闭集，且不依赖枚举名词。
#     ⚠️ 假名那一半是漏过一轮才补的：``カテゴリ別提案書。`` / ``テーマ別討論スレ``
#     的 ``別`` 前面是片假名不是汉字，只挡汉字的话照样漏。
#
# (b) 右界：``休講``（＝停课）是词首也成立的日文，单独排掉，与 _ZH_XIU 的
#     ``(?!講)`` 对齐。
#
# 剩下的残余：日文以 ``不要提案…`` 开头（不要＝ふよう）仍会误判。日文一般写
# ``不要な提案``，且要同时撞上 zh 模板的其余结构，代价上接受。
# ⚠️ 左界**只给 别/別**：日文的 ``〜別`` 后缀歧义是这一个字形独有的，``莫 / 休 / 甭``
# 都不是日文的名词后缀。套给全族的话，正常的中文主语会把它们一起挡掉——
# ``我莫再提君の名は。`` / ``她莫再提地域の話。`` 在 parent 上是好的，套上左界之后整条
# 被日文守卫吞掉（codex P2）。
_ZH_NEG_JA_AMBIGUOUS = ("别", "別")
_ZH_NEG_UNAMBIGUOUS = tuple(
    "休(?!講)" if neg == "休" else neg
    for neg in _ZH_NEG_SINGLES
    if neg not in _ZH_NEG_JA_AMBIGUOUS
)
_ZH_NEG_VERB_EVIDENCE = (
    "(?:"
    + r"(?<![一-鿿぀-ゟ゠-ヿｦ-ﾟ])(?:"
    + "|".join(_ZH_NEG_JA_AMBIGUOUS)
    + ")|"
    + "|".join(_ZH_NEG_UNAMBIGUOUS)
    + r")\s*(?:再)?\s*(?:"
    + "|".join(_ZH_SAY_COMPOUNDS + _ZH_SAY_VERBS)
    + ")"
)
# 多字否定词不需要左界：``不要 / 不许 / 不許 / 不准`` 都不可能是日文的名词后缀，
# 上面那条左界只为单字的 ``別`` 而设。带上左界反而把正常的中文主语挡在外面——
# ``我不要提君の名は。`` / ``你不准提君の名は。`` 在 parent 上都是好的（codex P2）。
_ZH_MULTI_NEG_EVIDENCE = (
    "(?:" + "|".join(_ZH_NEG_MULTIS) + r")\s*(?:再)?\s*(?:"
    + "|".join(_ZH_SAY_COMPOUNDS + _ZH_SAY_VERBS)
    + ")"
)
# 单字否定词前面允许出现的主语 / 敬语。⚠️ 这张字类是闭集，有相等断言钉着：往里加
# 任何一个日文汉字就是在守卫的左界上开洞（``俺`` 是北方口语主语、同时是日文常用汉字，
# 加进来 ``俺別提案をお願いします。`` 就会被当成中文指令存下来）。
# ⚠️ ``們`` 是繁体复数后缀（我們 / 你們 / 咱們），日文既不用 ``們`` 也不用 ``们``，
# 所以它后面的 ``別`` 同样不可能是日文的 ``〜別`` 后缀。不收的话繁中用户说
# ``我們別再提君の名は。`` 会被整条丢掉，而简体 ``我们别…`` 因为 ``们`` 在别处有
# 证据而正常（codex P2）。
_ZH_SUBJECT_CHARS = "你妳您咱请們"
# ⚠️ 只收**日文里根本没有的**汉字：``你 妳 您
# 咱 请`` 都不是日文汉字，所以它们后面的 ``別`` 不可能是日文的 ``〜別`` 后缀。
# ``我 / 他 / 請`` 刻意不收——它们是日文汉字，``他別提案をお願いします。`` 这类句子
# 会被放行进来（实测过）。宁可漏判一次，不可把日文残片存进指令表。
_ZH_SUBJECT_BEFORE_NEG = (
    "(?<=[" + _ZH_SUBJECT_CHARS + "])(?:"
    + "|".join(_ZH_NEG_SINGLES)
    + r")\s*(?:再)?\s*(?:"
    + "|".join(_ZH_SAY_COMPOUNDS + _ZH_SAY_VERBS)
    + ")"
)
_ZH_EVIDENCE_CHARS = "别说讲谈讨论关这话题愿懒许为甭說這關沒稱"
# ⚠️ ``没心情`` 要整条收：``没`` 是日文标准字形（没収），不能进上面的字类，但三个字
# 连在一起是中文独有的。不收的话 ``我没心情聊君の名は。`` 整条被吞，而繁体的
# ``沒心情`` 因为 ``沒`` 在字类里侥幸活着——同一模板内的行为不对称（codex P2）。
_ZH_EVIDENCE_WORDS = ("叫我", "喊我", "管我叫", "不想", "懶得", "不願", "没心情",
    # ⚠️ 模板 1 也收 _ZH_ADDRESS_VERBS，但只有 叫我 / 喊我 / 管我叫 在上面这批里。
    # ``称`` 是日文标准字形（名称）不能进字类，但 ``称呼我`` 三个字连在一起是中文
    # 独有的。不收的话 ``不要称呼我「君の名は」。`` 整条被吞（codex P2）。
    "称呼我", "稱呼我")
_ZH_EVIDENCE_RE = re.compile(
    "|".join(
        (f"[{_ZH_EVIDENCE_CHARS}]",)
        + _ZH_EVIDENCE_WORDS
        + (_ZH_NEG_VERB_EVIDENCE, _ZH_MULTI_NEG_EVIDENCE, _ZH_SUBJECT_BEFORE_NEG)
    )
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
        # 口语系 copula / 终助词。⚠️ 只收**多字**形式：裸 ``だ`` / ``ちゃ`` 会出现
        # 在专有名词里（お兄ちゃん），收了就把上面救回来的用例又打回去。
        "だね", "だよ", "だな", "だっけ", "でしょ", "かな", "かも", "じゃない",
        "らしい", "みたい", "そう？",
        # 过去 / 义务 / 被动 / 进行 等谓语形式（codex P2）
        "だった", "だって", "だろう", "すべき", "される", "された", "している",
        "します", "しない", "できる", "しよう", "ている", "ておく", "てある",
        # 格助词・系助词・副助词（多字优先，单字放最后的字符组里）
        "から", "まで", "より", "など", "だけ", "でも", "しか", "ばかり",
        "[のにをはがでとへ]",
    ))
)


def _is_japanese_sentence_match(
    span: str, term: str, before: str = "", directive: str | None = None,
) -> bool:
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
    # ⚠️ 把左边一个字符接上再搜：``_ZH_NEG_VERB_EVIDENCE`` 的判据是「否定词前面不是
    # 汉字」，而 span 恰好**从否定词开头**，只搜 span 的话那条 lookbehind 永远落空，
    # ``地域別提案をお願いします。`` 会被当成中文（本文件的 ja 语料直接抓出来的）。
    #
    # ⚠️ 中文证据只在**指令部分**搜，不看载荷：日文句子里出现中文片名是正常的，
    # ``世代別講座で中国映画這就是愛について話します。`` 里的 ``這`` 会把整条守卫短路
    # 掉，把日文句子的残片存进指令表（codex P2 两轮——先是加了引号的，后是没加的）。
    # 调用方传进来的 ``span`` 已经把捕获组等长挖空。
    # ⚠️ 必须**等长**挖空：直接删掉的话载荷两侧的字会被拼到一起，凭空造出多字证据
    # （叫+我 / 不+想 / 懶+得 / 不+願 / 喊+我 都实测过）。
    if _ZH_EVIDENCE_RE.search(before[-1:] + (span if directive is None else directive)):
        return False
    # 命中区间**左边紧挨着**假名 → 触发词是日文能产的 ``〜別`` 后缀（カテゴリ別 /
    # ジャンル別 / テーマ別），不是中文的祈使 ``別``。中文句子里 ``別`` 前面不会
    # 直接贴假名。这一条打的是 span 之外的字符，所以 term 本身不含助词也拦得住
    # （"ジャンル別討論スレ" 的 term 是 ``スレ``，(2b) 够不着）。
    if before and _KANA_RE.search(before[-1]):
        return True
    # 快速退出：没假名就不可能是日文句子。(2b) 的判据本身全是假名、且 term 是 span
    # 的子串，所以这一行不是独立条件，只是省掉一次 regex——热路径每条用户消息
    # 每条模板都会走到。
    if not _KANA_RE.search(span):
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
     # 动词表见 _zh_verb_alternation：复合动词必须排在单字前缀之前（模板 2/4 要求
     # 动词后紧跟终结符，失败会回溯，所以没这个问题）。
     + _ZH_VERBS_WITH_ADDRESS + r"\s*"
     # ⚠️ 宾语下限是 2 不是 1：可选助词组 + lazy 宾语会让正则优先把话题的最后一个字
     # 当成助词（"别再提拿捏。" → 宾语 "拿"、助词 "捏"），削到 1 字后撞长度下限、
     # 整条指令消失。1 字宾语本来也只能产出 1 字 term 必被丢，抬下限只赚不亏。
     + _ZH_TOPIC_SEPARATOR
     + _ZH_OBJECTLESS_AHEAD
     + "(" + _zh_topic(2, 40) + r")" + _ZH_FINAL_PARTICLES + r"?(?:[，。！？；,.!?;]|\s*$)"),
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
     # ⚠️ 前缀下限是 2 不是 1：三个可选填充组 + lazy 前缀会让正则优先把话题的最后
     # 一个字塞进填充组，主语只有一个字时（"钱的事别提了。"）前缀被削成 1 字、撞上
     # ``2 <= len(term)`` 的下限，整条指令消失。下限提到 2 之后正则会改选更长的
     # 前缀；1 字前缀本来也只能产出 1 字 term、必然被丢，所以抬下限只赚不亏。
     # ``的`` 绑在指示词里、不单独可选：单独可选会把 "目的这个别提了。" 的 目的 切成
     # 目（对抗排查）。句尾的 ``就`` 不在正则里吃，见 _ZH_TRAILING_FILLERS。
     # ⚠️ 下面这一串 ``\s*`` 必须**原子化**：前置话题的单字分支也匹配空格，于是话题
     # 和后面每个 ``\s*`` 能任意瓜分同一串空白，把「在哪切」变成组合爆炸。实测
     # ``extract_directives(" " * 60)`` 要 0.42 秒，而这条路径是每条用户消息同步跑
     # 的——发一条纯空白消息就能卡住（codex P1）。原子化之后回到 parent 的量级。
     # ⚠️ 原来还有一处重复的 ``\s*\s*``，一并合掉。
     # ⚠️ 动词**之后**那个 ``\s*(?:了)?`` 不能原子化——它后面的终结符字符类里含
     # ``\s``，原子化会把本该当终结符的那个空格吃掉。
     r"(?!(?<=关)于)(?!(?<=關)於)("
     + _zh_topic(2, 30, block_guanyu=True) + r")(?>\s*)"
     r"(?:的事)?(?>\s*)(?:的?(?:这个|這個|这事|這事|这话题|這話題|这件事|這件事))?(?>\s*)"
     r"[别別](?>\s*)(?:再)?(?>\s*)"
     r"(?:提了|提起|提及|说|說|提|聊|讲|講)\s*(?:了)?(?:[，。！？；,.!?;\s]|$)"),
    # 不想/不愿 + 聊/讨论 + X — 同上：terminator 不要 \s，否则多词 NP 被切
    ("zh", "ban_topic",
     r"(?:我)?\s*(?:不想|不愿意|不願意|不愿|不願|懒得|懶得|没心情|沒心情)\s*(?:再)?\s*"
     + _ZH_VERBS_PLAIN
     + r"\s*"
     + _ZH_TOPIC_SEPARATOR
     + _ZH_OBJECTLESS_AHEAD
     + r"(" + _zh_topic(2, 40) + r")(?:\s*(?:了|的事))?(?:[，。！？；,.!?;]|\s*$)"),
    # 关于 X + 别(再)+ 说
    ("zh", "ban_topic",
     # ⚠️ 只有本模板保留 ``(?:就)?``：它由句首的 ``关于`` 锚定，"关于 X 就别…" 的
     # 结构是显式的，被腰斩的风险仅限于 ``关于`` + 就尾词（"关于功成名就别提了"，
     # 极罕见）。模板 2 没有这个锚，覆盖的是全部 "X别提了" 句子——成就 / 迁就 /
     # 功成名就 都住在那里，所以那边一个字都不吃，交给 _drop_filler_suffixed_terms。
     # ⚠️ 触发词之前的每个 ``\s*`` 都要原子化，理由同模板 2：话题的单字分支也匹配
     # 空格，能和后面每个 ``\s*`` 任意瓜分同一串空白。上一轮只改了模板 2、漏了这条，
     # ``"关于" + " " * 80`` 要 3 秒（codex P1 第二轮）。
     r"(?:关于|關於)(?>\s*)(" + _zh_topic(2, 30) + r")(?>\s*)(?:的事)?(?>\s*)"
     r"(?:的?(?:这个|這個|这事|這事|这话题|這話題|这件事|這件事))?(?>\s*)(?:就)?"
     r"(?>\s*)[别別](?>\s*)(?:再)?(?>\s*)"
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
    # ⚠️ 去重必须放在 _drop_filler_suffixed_terms **之后**：填充词过滤靠命中区间
    # 认「同一条指令的两种切法」，而去重会把重复 term 连同它的区间一起扔掉。
    # "股票别提了。关于股票就别提了。" 里第二条指令的 ``股票`` 因为和第一条同名被去掉，
    # 过滤器就只看得到第一条那个**不重叠**的区间，于是 ``股票就`` 逃过一劫（codex P2）。
    out: List[Tuple[str, str, str]] = []
    spans: List[Tuple[int, int]] = []
    for locale, kind, pat in DIRECTIVE_PATTERNS:
        # 同上：不手写 startswith("zh")，走公共的 fallback-family 判定。
        zh_family = prompt_locale_fallback_key(locale) == "zh"
        for m in pat.finditer(text):
            try:
                term_raw = m.group(1)
            except IndexError:
                continue
            term = _trim_term(term_raw, locale)
            if not (_TERM_MIN_LEN <= len(term) <= _TERM_MAX_LEN):
                continue
            # zh 模板与日文共用 別/提/講/談/討論 这些汉字，日文句子会被抓成
            # ban_topic（见 _is_japanese_sentence_match）。只对 zh 生效——ja 模板
            # 本身要求假名，套上去会把自己全部否掉。
            # ⚠️ 只切**一个**字符：守卫读的就是 before[-1:]，切整段前缀等于每条命中
            # 复制一次全文——一条消息里几万条指令时是二次方（60000 条 2.5 秒，base
            # 1.2 秒；codex P2）。
            # ⚠️ 判据要看**未 trim** 的捕获：假名助词表现在对所有 locale 生效，
            # ``地域別講座だね。`` 的 ``だね`` 会在 trim 里被剥掉，等守卫拿到 term 时
            # 日文语法标记已经没了，整句反被判成中文（补假名回落时踩到的）。
            # 指令部分 = 命中区间去掉被捕获的话题（等长空格填充，保住相对位置、也
            # 避免两侧的字被拼到一起）。载荷里的中文不该当中文证据。
            span = m.group(0)
            payload_lo = m.start(1) - m.start()
            payload_hi = m.end(1) - m.start()
            directive_only = (
                span[:payload_lo] + " " * (payload_hi - payload_lo) + span[payload_hi:]
            )
            # ⚠️ 只有**证据**看指令部分；假名和日文语法那两条判据仍看完整命中区间。
            # 传挖空后的串进去会把假名一起挖掉，``地域別提案をお願いします。`` 会因为
            # 「没有假名」被判成中文（补这条时踩到的）。
            if zh_family and _is_japanese_sentence_match(
                span, m.group(1), text[max(0, m.start() - 1): m.start()],
                directive=directive_only,
            ):
                continue
            out.append((locale, kind, term))
            spans.append((m.start(), m.end()))
    seen: set[tuple[str, str]] = set()
    deduped: List[Tuple[str, str, str]] = []
    for locale, kind, term in _drop_filler_suffixed_terms(out, spans):
        key = (kind, term.casefold())
        if key in seen:
            continue
        seen.add(key)
        deduped.append((locale, kind, term))
    return deduped


def _drop_filler_suffixed_terms(
    hits: List[Tuple[str, str, str]],
    spans: List[Tuple[int, int]] | None = None,
) -> List[Tuple[str, str, str]]:
    """Drop a term that is just another extracted term plus a trailing filler word.

    "關於股票就別再講了" matches two templates: the generic one stops at ``股票就``
    (the adverb ``就`` belongs to the sentence, not the topic) and the dedicated
    ``关于`` one yields ``股票``. Both would be persisted and injected for three days.

    Swallowing the filler inside the regex is the obvious fix and it is wrong: the
    prefix is lazy, so the engine prefers to feed it the topic's **last character**
    instead — "他的成就别提了。" comes out as ``他的成``. A left-edge character
    blocklist cannot rescue that either, because words ending in ``就`` are an open
    set (成就 / 迁就 / 功成名就 / 一蹴而就 / 練就 …) and one omission truncates a real
    topic.

    Comparing after the fact needs no word-boundary guess at all: ``股票就`` goes
    because ``股票`` was also extracted from the same message, while ``功成名就``
    stays because ``功成名`` never was.

    ⚠️ The comparison is restricted to **overlapping** matches, i.e. the two
    templates firing on the *same* directive. Without that,
    "功成名就别提了，功成名别提了。" — two separate directives that happen to differ
    by a ``就`` — would lose the first one (codex P2). ``spans`` carries that
    provenance positionally alongside ``hits``; omit it and nothing is suppressed,
    which is the safe direction.
    """  # noqa: DOCSTRING_CJK
    if len(hits) < 2 or not spans or len(spans) != len(hits):
        return hits
    # 只有"末尾正好是一个填充词"的 term 才可能被抑制。绝大多数消息里一条都没有，
    # 先筛一遍就把 O(n²) 的重叠扫描降到 O(n)——n 是同一条消息里的命中数，粘贴一大段
    # 聊天记录时可以到几百（codex P2）。
    suspects = [
        index
        for index, (_locale, _kind, term) in enumerate(hits)
        if any(term.endswith(filler) for filler in _ZH_TRAILING_FILLERS)
    ]
    if not suspects:
        return hits
    suspect_set = set(suspects)

    # ⚠️ 光筛 suspects 不够：话题本身就以填充词结尾时（``成就别提了。`` 重复几千遍）
    # 每条命中都是 suspect，逐条再扫全表又变回 O(n²)——4000 条要 1.25 秒，而这条
    # 路径是同步跑在用户消息上的（codex P2）。
    #
    # 命中区间的长度有上界（模板本身有 {,40} 之类的限制），所以按起点分桶之后，
    # 可能与某条命中重叠的邻居只会落在它自己和左右几个桶里，查找变成 O(1)。
    _BUCKET = 128
    _span_buckets: dict[int, List[int]] = {}
    for other_index, (start, end) in enumerate(spans):
        for bucket in range(start // _BUCKET, end // _BUCKET + 1):
            _span_buckets.setdefault(bucket, []).append(other_index)

    def _overlaps(a: Tuple[int, int], b: Tuple[int, int]) -> bool:
        return a[0] < b[1] and b[0] < a[1]

    def _is_redundant(index: int) -> bool:
        locale, kind, term = hits[index]
        start, end = spans[index]
        neighbours = {
            other_index
            for bucket in range(start // _BUCKET, end // _BUCKET + 1)
            for other_index in _span_buckets.get(bucket, ())
        }
        # 只跟"命中区间和自己重叠"的同类 term 比 —— 那才是同一条指令的两种切法。
        rivals = {
            hits[other_index][2]
            for other_index in neighbours
            if other_index != index
            and hits[other_index][1] == kind
            and _overlaps(spans[index], spans[other_index])
        }
        if not rivals:
            return False
        # 填充词会叠（"前女友的事就"），所以逐层剥，任何一层撞上对手就丢。
        seen_forms = {term}
        frontier = [term]
        while frontier:
            current = frontier.pop()
            for filler in _ZH_TRAILING_FILLERS:
                if not current.endswith(filler) or len(current) <= len(filler):
                    continue
                shorter = current[: -len(filler)]
                # ⚠️ 剥完要把两端的括号 / 标点也归一化再比：填充词前面常常正好是一个
                # 收尾括号。``关于《你好，李焕英》就别提了。`` 的通用切法是
                # ``你好，李焕英》就``，剥掉 ``就`` 得到 ``你好，李焕英》``——多一个
                # ``》`` 就跟专用切法的 ``你好，李焕英`` 对不上，畸形的那条照样存三天
                # （codex P2）。归一化用的就是 term 落库前走的同一套 _TRIM_TRAIL。
                # ⚠️ 剥完要走**同一套 _trim_term** 再比，不能只剥标点：对手那条
                # 是落库前 trim 过的，而中间形态还带着助词。``關於전남친은就別提了。``
                # 里剥掉 ``就`` 得到 ``전남친은``，跟已经 trim 成 ``전남친`` 的对手
                # 对不上，畸形的那条照样存三天（codex P2）。
                forms = {shorter, _trim_term(shorter, locale)}
                for form in forms:
                    if form in rivals:
                        return True
                    if form and form not in seen_forms:
                        seen_forms.add(form)
                        frontier.append(form)
        return False

    return [
        hit
        for index, hit in enumerate(hits)
        if index not in suspect_set or not _is_redundant(index)
    ]


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
