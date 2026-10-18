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
import threading
import unicodedata
from typing import List, Tuple

from config.prompts._locale import normalize_prompt_locale, prompt_locale_fallback_key
from config.prompts.prompts_sys import _loc


# Strip characters around extracted terms: punctuation plus language-specific particles / small modifiers.
# Strip only at the end, preserving internal content.
_TRIM_TRAIL = (
    # ASCII / CJK punctuation / whitespace.
    " \t\n\r"
    ".,!?;:\"'`()[]{}<>"
    "。！？，；：、…—·"
    # Paired with _ZH_BRACKET_PAIRS: both ends of every bracket treated as a topic delimiter must strip here,
    # or brackets persist with terms (〔重要，紧急〕). Tests pin this invariant.
    "“”‘’（）【】《》「」『』〈〉〔〕［］〖〗"
)
# Language-specific final particles / discourse particles, stripped from term endings.
#
# Separate by locale rather than globally: CJK particles can be **different words sharing the same
# code point**. 唄 is traditional Chinese 呗 but Japanese song (子守唄 = lullaby);
# Chinese stripping truncates Japanese 子守唄 to 子守 (codex P2). Likewise 了
# in Japanese 完了 / 終了. Strip only particles belonging to the matching template's locale.
_TRIM_TRAIL_TOKENS_BY_LOCALE: dict[str, Tuple[str, ...]] = {
    "zh": (
        "了", "啊", "呀", "吧", "嘛", "哦", "呗", "啦", "呢", "嘞", "诶",
        # zh-TW / Taiwan colloquialisms are rare in simplified corpora but common in Taiwan; without them,
        # 別再提工作喔 persists 工作喔 as its term.
        # Omit these characters **entirely**, including regex acceptance groups:
        #   唄 (traditional 呗) means song in Japanese, so banning a Japanese song title
        #     (别再提花の唄了。) would truncate it to 花の;
        #   耶 / 捏 / 囉 / 啰 **also commonly end words** (坎耶 / 拿捏 /
        #     揉捏 / 嘍囉 / 喽啰). 别再提精准拿捏。 truncates to nonword 精准拿,
        #     and 别再提小喽啰。 to 小喽 (parent handled the latter correctly; this PR regressed
        #     both scripts together, codex P2).
        # Choose by cost direction: inclusion cleans common phrasing (工作耶->工作),
        # but truncates rare topics into **nonwords**; omission adds a character to common phrasing (工作耶), with
        # real topics fully intact for the model. Prefer extra characters over missing ones.
        # 囉 is even more frequent than 耶 in Taiwan (好囉 / 走囉), initially making removal difficult. But frequency
        # is not this criterion's independent variable: it determines extra-character frequency, not the cost of truncating
        # rare topics into nonwords. The same comment already made this choice for 耶 / 捏.
        "喔", "唷", "齁", "欸", "誒", "咧", "喲",
    ),
    "ja": ("ね", "よ", "わ", "の", "って", "なんて", "という"),
    "ko": ("요", "은", "는", "이", "가", "을", "를", "에", "에서"),
}

_HAN_RE = re.compile(r"[一-鿿㐀-䶿]")
# No Han means no shared code points with Han and no cross-locale collision. Discover automatically rather than list manually:
# future Kana / Hangul / Cyrillic particle tables enter this set automatically.
_SCRIPT_DISJOINT_FAMILIES = tuple(
    fam
    for fam, toks in _TRIM_TRAIL_TOKENS_BY_LOCALE.items()
    if not any(_HAN_RE.search(tok) for tok in toks)
)

# Rhetorical endings after final particles (工作了好嗎): regex's optional particle group accepts one,
# leaving the rest in terms for trim's iterative removal.
#
# Separate table, stripped **only without brackets in terms**: these phrases also end many work titles
# (《我們好不好》/《最近你好嗎》), and brackets strip before particles. Unconditional stripping
# makes 别再提电影《我们好不好》。 persist 电影《我们, truncating titles into nonwords.
# （codex P2）。
#
# This dimension has a clean closed criterion: **whether original terms contained bracket characters**. Brackets imply quoted
# proper names whose 好不好 endings belong to names; unbracketed forms express the speaker's rhetorical tone.
# Conversely phrases that might end work titles form an open set no enumeration exhausts.
# Include bare 吗 / 嗎 too: 別再提工作嗎？ persists 工作嗎; 不要再說工作了嗎？
# persists 工作了嗎, unable even to strip 了 after stopping at 嗎. Like the multicharacter
# phrases above, quotation criteria protect them, preserving 《你可以吗》 intact (codex P2).
_TAIL_INTERROGATIVES_BY_LOCALE: dict[str, Tuple[str, ...]] = {
    "zh": ("好吗", "好嗎", "好不好", "可以吗", "可以嗎", "行吗", "行嗎", "吗", "嗎"),
}

# Locales without their own CJK tables (en / ru / es / pt) fall back to the **zh + ja** union:
# mixed-language input is explicitly supported ("stop talking about 前女友了" / "stop saying
# 仕事ね"). Terms may be wholly Chinese or Japanese; omission persists particles unchanged (codex P2).
# Locale separation prevents zh/ja/ko **mutual** contamination: each uses its own table when matching, unaffected here.
# List only zh here: _trim_term unconditionally adds Kana / Hangul tables (see
# _SCRIPT_DISJOINT_FAMILIES); listing them here duplicates data that can drift.
_TRIM_TRAIL_FALLBACK_LOCALES = ("zh",)

# Locale-independent endings: ASCII words cannot collide with other scripts. Chinese/English mixing is common
# (别提 my ex please), so strip them across all locales.
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
    # Defensive split: when the resolver is unbound (partial entry points / direct tests),
    # _nlc returns input **unchanged**; manually strip region suffixes here, preserving basic zh-CN -> zh
    # normalization in tests too. Already-short codes make split a no-op.
    if '-' in out or '_' in out:
        out = out.split('-', 1)[0].split('_', 1)[0]
    return out or 'en'


# Storable term-length range. _trim_term also needs the minimum: stripping below minimum discards the entire
# instruction, worse than retaining an ambiguous final character (see _trim_term).
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
    # Use public config.prompts._locale normalization, never independently strip region suffixes:
    # tests/unit/test_prompt_locale_normalizer.py explicitly forbids handwritten locale matching outside _locale.py;
    # six handwritten copies caused esperanto to look Spanish and Steam codes to fall back to English.
    family = normalize_prompt_locale(
        locale, default="", simplified="zh", keep_traditional=False,
    )
    families = (
        (family,)
        if family in _TRIM_TRAIL_TOKENS_BY_LOCALE
        else _TRIM_TRAIL_FALLBACK_LOCALES
    )
    # Every locale includes **Han-free** particle tables, automatically discovered rather than named.
    #
    # Separation resolves shared-code-point CJK ambiguity (唄 as Chinese particle / Japanese song, 了 in
    # Japanese 完了/終了), possible only for **Han**. Hangul/Kana share no Han code points,
    # safely applying across locales. Using only matching locales' tables would make:
    #   别再提전남친은。 persist 전남친은 (base 전남친)
    #   别再提仕事ね。 persist 仕事ね (base 仕事)
    # Both are codex P2. Locale identifies **the matched template**, not topic language.
    families = tuple(dict.fromkeys(families + _SCRIPT_DISJOINT_FAMILIES))
    cjk = tuple(
        tok for fam in families for tok in _TRIM_TRAIL_TOKENS_BY_LOCALE[fam]
    )
    # Rhetorical endings **do not follow fallback**: 好吗 / 吗 express **sentence-level** Chinese questioning,
    # whereas en/es/ru/pt syntax already delimits objects; in stop saying 你好吗,
    # the entire 你好吗 is banned, and stripping would persist 你好 (codex P2).
    # Particles need fallback because mixed-language terms are often wholly Chinese; rhetorical phrases do not.
    interrogatives = _TAIL_INTERROGATIVES_BY_LOCALE.get(family, ())
    # Quotation criteria apply to **all** CJK endings, not just multicharacter rhetorical phrases: single characters also belong to
    # names (《想見你喔》 / 《就是愛唷》, codex P2).
    # Gate ASCII please / porfa too. Initially considered irrelevant to CJK work titles,
    # but English titles contain them: 《Never Please》 truncates to Never (parent intact),
    # and case-insensitive stripping affects Please / PLEASE too (codex P2).
    # Gate only **inside quotes**: please in 别再提工作 please。 lies outside and still strips.
    gated = cjk + interrogatives + _TRIM_TRAIL_TOKENS_ANY
    # **Try longer first**: short tokens often suffix long ones (吗 within 好吗); stripping shorter first
    # bypasses the longer-token decision.
    # ASCII endings now belong to gated tokens; do not add them separately again, making removal from stripping
    # tables a no-op whose mutations can never fail (mutation testing).
    tokens = sorted(gated, key=len, reverse=True)
    # Minimum length protects only **ambiguous** endings: 耶 / 捏 / 咧 also end common words (坎耶 /
    # 拿捏 / 好咧). Stripping below storable length discards the instruction, worse than retaining them. ASCII please /
    # porfa, Kana, and Hangul cannot belong to Chinese words; minimum protection would
    # persist 別再提錢please。 as 錢please, whereas parent strips then discards by length
    # (codex P2). Criterion: only tokens containing Han are ambiguous.
    ambiguous = frozenset(tok for tok in gated if _HAN_RE.search(tok))
    original = term.strip()
    quoted_until = _zh_quoted_span_end(original)
    s = original
    # Number of trailing characters stripped. Criteria need ending positions in **original terms**, while s repeatedly shrinks
    # from both ends. Only right-side shortening affects absolute indices, so accumulate that side alone.
    right_removed = 0
    changed = True
    # Endings blocked by minimum length also block their **suffixes**: in 别提钱好吗？, 好吗
    # is blocked because only 钱 remains; subsequent 吗 would truncate it into nonword 钱好 (codex P2).
    floor_blocked: set[str] = set()
    # Repeatedly strip endings until stable, handling consecutive particles such as 了啊吧.
    while changed:
        changed = False
        for tok in tokens:
            # ASCII case criterion: **strip all-lowercase or all-uppercase only, never initial-capital**.
            # Uppercase in 別再提工作 PLEASE。 emphasizes the same politeness word and should strip (codex P2
            # requested case-insensitivity); unconditional case-insensitivity also truncates **initial-capital** title words,
            # making Never Please is off limits. persist Never instead of parent's intact title
            # (reverse codex P2). Initial capitals signal name components in English.
            # Same tradeoff as CJK: prefer an extra word over losing half a name.
            #
            # Compute s.lower() **inside the loop**: this for shortens s;
            # computing once outside compares the second token against stale text and consumes another character
            # (電影《你好》續集好嗎 becomes 電影《你好》續, caught in self-testing).
            tail = s[-len(tok):]
            if not (s.endswith(tok) or (tail.lower() == tok and tail.isupper())):
                continue
            if any(tok != blocked and blocked.endswith(tok) for blocked in floor_blocked):
                continue
            trimmed = s[: -len(tok)]
            shorter = trimmed.rstrip()
            # Never strip endings that are the entire topic: please in stop saying please / no menciones
            # porfa is itself banned, so stripping loses the instruction
            # (codex P2). Independent of ambiguity: empty strings retain no value.
            if not shorter:
                continue
            # Below minimum means discarding the whole instruction; retain **ambiguous** final characters instead (codex P2).
            if len(shorter) < _TERM_MIN_LEN and tok in ambiguous:
                floor_blocked.add(tok)
                continue
            # Particles/rhetorical phrases also end many titles, so strip only **outside quotes**.
            # Check whether their original-term start passes the last bracketed span's closing position:
            #   《最近你好嗎》 -> 好嗎 is inside; stripping truncates the title;
            #   《你好》好嗎 -> after the closer, sentence-level tone that should strip;
            #   電影《你好》續集好嗎 -> likewise after the closer despite intervening modifiers.
            # The former proxy required stripped prefixes to end exactly in closers, misclassifying the third row
            # (codex P2) because intervening 續集 defeats it.
            tail_start = len(original) - right_removed - len(tok)
            if tok in gated and tail_start < quoted_until:
                continue
            right_removed += len(s) - len(shorter)
            s = shorter
            changed = True
            # After stripping, **rescan from the beginning**. Tokens are length-sorted only on iteration
            # entry: stripping final 呢 **exposes** longer 好吗 already passed by the cursor,
            # so its suffix 吗 matches next, persisting nonword
            # 工作好 (我不想聊工作好吗呢？; parent 工作好吗, codex P2).
            # Minimum-length gate floor_blocked guards the other end and cannot replace this.
            break
        # Strip punctuation from both ends together.
        new_s, right_delta = _strip_trail(s)
        if new_s != s:
            right_removed += right_delta
            s = new_s
            changed = True
    return s.strip()


def _strip_trail(s: str) -> Tuple[str, int]:
    """Strip punctuation off both ends; returns the result and the right-side count.

    Strip brackets only when **truly enclosing the whole term**. Unconditional strip-set inclusion truncates closing
    brackets when titles merely suffix terms: 别再提电影〈你好〉。 persists 电影〈你好
    (parent intact; 〈〉〔〕［］〖〗 were newly added to _TRIM_TRAIL in this PR,
    codex P2). Require **pairing**, not simply bracket-character membership:
      · A pair encloses the whole term: remove it (《你好》 -> 你好);
      · Final closer has its opener still inside term: preserve it (电影〈你好〉);
      · Final closer is unmatched: strip normally (你好》 -> 你好).
    Also correct 《》 【】 「」 together, which parent truncated in 电影《你好》 too.
    Leaving half unchanged creates divergent same-kind tables and inconsistent old/new bracket behavior.
    """  # noqa: DOCSTRING_CJK
    right = 0
    while True:
        # Handle only **asymmetric** pairs. Symmetric quotes (" ) are absent from _ZH_COUNTERPART, so
        # generic strip below already removes both ends. Another branch here is dead code; exhaustive
        # testing of 40,320 inputs found no observable difference.
        if len(s) >= 2 and _ZH_COUNTERPART.get(s[0]) == s[-1]:
            s = s[1:-1]
            right += 1
            continue
        # Strip **one** character then recompute: removing one changes pairing.
        # Bracket criteria ask whether **this position** is paired, not whether a counterpart exists anywhere,
        # which is too coarse: 《 in 电影《你好》续集》 already paired with the earlier 》,
        # leaving the final closer surplus, stripped by parent (codex P2).
        unmatched = _zh_unmatched_delims(s)
        if s and s[-1] in _TRIM_TRAIL and (
            s[-1] not in _ZH_COUNTERPART or len(s) - 1 in unmatched
        ):
            s = s[:-1]
            right += 1
            continue
        if s and s[0] in _TRIM_TRAIL and (
            s[0] not in _ZH_COUNTERPART or 0 in unmatched
        ):
            s = s[1:]
            continue
        return s, right


def _zh_unmatched_delims(s: str) -> set:
    """Indices of bracket characters that never paired up, scanned in order."""
    stack: List[int] = []
    unmatched: set = set()
    for index, char in enumerate(s):
        if char in _ZH_CLOSE_FOR_OPEN:
            stack.append(index)
        elif char in _ZH_COUNTERPART:
            if stack and _ZH_CLOSE_FOR_OPEN[s[stack[-1]]] == char:
                stack.pop()
            else:
                unmatched.add(index)
    unmatched.update(stack)
    return unmatched


# ---------------------------------------------------------------------------
# Regex templates: (locale, kind, compiled_pattern, capture_group_index).
#
# Every pattern requires a term capture group.
# kind currently supports only ban_topic with a term; extend here for future rename_request,
# etc.
# ---------------------------------------------------------------------------

# Each locale defines its own verb block (说/提/talk about/言う/...).
# All patterns compile with IGNORECASE / UNICODE.

# ---------------------------------------------------------------------------
# Two zh guards: compound left boundaries + Kana.
# ---------------------------------------------------------------------------
# Traditional variants share patterns directly ([别別] / [说說] / 讨论|討論), without separate regex sets.
# Maintaining duplicate regexes for one structure inevitably causes one-sided omissions, as happened four times
# in #2655. Cost: match-record locale is always zh, used diagnostically rather than for table lookup.
#
# But 別 shares a Japanese code point (說 does not; Japanese uses 説 U+8AAC), and
# 提 / 講 / 談 / 討論 are shared, so adding traditional forms extends zh templates
# into Japanese input. Two guards below handle separate dimensions:

# (1) Left boundary: X别 ends a compound rather than expressing imperative negation.
# This **cannot be exhaustively enumerated**: 这个别提了 vs 个别说法 and 这部分别提了 vs
# 分别说明 are literally identical, inherently ambiguous Chinese segmentation. Include only four groups with zero counterexamples,
# paired across scripts: 特别 / 性别 / 区别 / 级别 followed by 说/提/讲/谈 commonly occur in Chinese/Japanese
# (他特别提到你的名字 currently extracts as ban_topic), while X特|别提
# segmentation does not exist in Chinese.
# Others (个别 / 分别 / 告别 / 类别…) retain existing permissiveness, each having genuine counterexamples;
# tightening loses main uses such as 工作这个别提了, so retain existing false positives.
#
# This guard applies **only to template 1** (negation + verb + object). In templates 2/4, 别 follows
# the captured **topic itself**; topics ending in these characters (模特别提了。 / 这种可能性别
# 提了。) would lose whole instructions. Templates 2/4 require terminators **immediately** after verbs, making compound
# matches rare (residual 他特别提了。 yields a two-character junk term, matching pre-PR
# behavior). Template 1 instead consumes the rest of sentences after compounds, guaranteeing false positives (codex P2).
#
# 个/個 is safe precisely after narrowing to template 1: main use 工作这个别提了
# uses template 2, unaffected; template-1 segmentation of 这个别提工作了 is unnatural Chinese.
# Including it blocks **pure-Kanji** Japanese 個別提案書。, with no Kana for guard (2),
# also fixing existing simplified 个别说法不太准确。 (codex P2).
_BIE_COMPOUND_LEFT = "特性区區级級个個"
_ZH_BIE = f"(?<![{_BIE_COMPOUND_LEFT}])[别別]"

# 休 reverses this: negation is classical (休提当年勇), rare in modern chat, while
# 退休 / 午休 / 调休 are compounds. Recognize it only at word starts, without character enumeration.
# Also exclude 休講: word-start rules miss sentence-initial 休講だって。 / 休講情報。, and
# this PR's newly added verb 講 opens a Japanese entry into all template 1. Chinese never uses 休講;
# this modern negative usage survives only in 休提 / 休想 (adversarial investigation).
_XIU_COMPOUND_LEFT = "退午调調补補年病公轮輪全双雙不歇罢罷特半"
_ZH_XIU = f"(?<![{_XIU_COMPOUND_LEFT}])休(?!講)"

# Negations form a **closed set**, with _ZH_NEG and three Japanese-evidence regexes below sharing one source.
# With four handwritten copies, adding e.g. 勿 to _ZH_NEG without corresponding evidence kept Chinese
# working but rejected 勿提君の名は。 while identical 莫提君の名は。
# survived, causing within-template asymmetry without a single failing test.
#
# Separate single/multicharacter forms: Japanese 〜別 suffix ambiguity affects single characters only (see _ZH_NEG_VERB_EVIDENCE
# left-boundary comments); 不要 / 不准 cannot be Japanese noun suffixes.
_ZH_NEG_SINGLES = ("别", "別", "莫", "休", "甭")
_ZH_NEG_MULTIS = ("不要", "不许", "不許", "不准")
# Regex single-character forms carry their own compound guards (_ZH_BIE left boundaries / _ZH_XIU (?!講));
# evidence regexes use **bare forms** above with their own left boundaries.
_ZH_NEG_SINGLES_GUARDED = (_ZH_BIE, "莫", _ZH_XIU, "甭")
# Evidence still excludes 講 after 休: Japanese 休講, canceled classes, itself matches negation +
# speech-verb structural evidence. End-to-end unreachable because _ZH_XIU (?!講) prevents
# any zh match, it was once deleted as dead; but **evidence regexes must independently miss Japanese corpora**,
# a stronger and preferable property tested per branch. See _ZH_NEG_UNAMBIGUOUS.
_ZH_NEG = (
    "(?:" + "|".join(_ZH_NEG_SINGLES_GUARDED + _ZH_NEG_MULTIS) + ")"
)

# ---------------------------------------------------------------------------
# Speech-verb table: **generated**, not handwritten.
# ---------------------------------------------------------------------------
# Handwritten lists failed twice: template 1 objects (.{1,40}?) consume anything, so
# once single 提 matches, the remainder succeeds and regex has **no reason** to backtrack into 提起.
# Therefore compound verbs must precede their single-character prefixes.
#
# Do **not** form speech-verb × result-complement products. 到 / 起 / 及 were once consumed
# as complements to make 別提到我前女友。 store 我前女友 rather than 到我前女友.
# But 提／到达时间 and 提到／达时间 are identical strings with grammatical segmentations no local
# rule can distinguish; 别提到达时间。 then stores 达时间, 别聊起点问题。 stores
# 点问题, and 别提及格线的事。 stores 格线的事 (codex P2, simplified regressions too).
# Words beginning 到/起/及 form an open set no enumeration exhausts. Costs are asymmetric: retaining 到
# keeps real topics fully intact for models; consuming a character persists nonwords.
_ZH_SAY_VERBS = ("说", "說", "提", "聊", "讲", "講", "谈", "談", "扯")
# Two-character speech verbs: include only forms **inseparable into single verbs**. 讨 cannot stand alone (别讨政治
# is unnatural), so include 讨论 whole; consuming topic-initial 论 (别讨论文格式。
# -> 文格式) is unavoidable, matching base.
#
# Exclude 谈论 / 談論: 谈 is independently a verb, so compound interpretation is optional,
# with real cost: 别谈论语考试。 truncates to 语考试; 别谈论语。 disappears
# (base 论语考试 / 论语; codex P2). Topics beginning 论 form an open set
# (论语 / 论文 / 论坛 / 论证 / 论政治), the same family as result complements: retaining 论
# preserves full topics; consuming characters persists nonwords.
_ZH_SAY_COMPOUNDS = ("讨论", "討論")
# Speech-verb forms **also present in Japanese** and forming compounds with 別:
#   別提案 (べつていあん, another proposal) / 別講座 / 別談話 / 別討論.
# This is the **actual** criterion distinguishing Chinese imperative 別提 from Japanese compounds: ambiguity belongs to
# **verbs**, not positions. Previous positional divisions first blocked Han left neighbors, then string starts;
# every blocked case let codex expose another (今回は、別提案… / 「別提案…」).
# All remaining forms cannot collide: simplified 说 讲 谈 讨论 are absent from Japanese Kanji; traditional 說 is too
# (Japanese 説, U+8AAC != U+8AAA); 聊 cannot begin Japanese words, and 扯 is absent
# entirely. 別聊 / 別扯 / 別說 can only be Chinese anywhere, so need neither left boundaries
# nor 再, or 別聊君の名は。 / 請別扯君の名は。 lose every match while simplified
# equivalents survive through 别 in _ZH_EVIDENCE_CHARS (codex P2).
# Equality assertions pin this: adding inappropriate characters immediately loses corresponding traditional phrases.
_ZH_SAY_VERBS_JA_SHARED = ("提", "講", "談", "討論")
# The **two-character** subset. Consuming whole compounds leaves Kana-leading terms, beyond
# the one-Kanji-plus-Kana shape criterion; see _is_japanese_sentence_match.
_ZH_COMPOUND_JA_SHARED_VERBS = tuple(
    v for v in _ZH_SAY_VERBS_JA_SHARED if len(v) > 1
)
# Naming verbs: verb + 我 + form of address, distinct from both families above.
_ZH_ADDRESS_VERBS = ("管我叫", "称呼我为?", "稱呼我為?", "喊我", "叫我")
# Reluctant instruction heads (我不想聊X template). Separate constants let bracket-body temper
# recognize them too; negation + verb alone merges 别提价格<预算.我不想聊收入>目标.
# The second instruction goes unrecognized and both merge (codex P2, simplified regression).
_ZH_RELUCTANCE = ("不想", "不愿意", "不願意", "不愿", "不願", "懒得", "懶得",
                  "没心情", "沒心情")
# Preposed-topic templates (X 别提了) have their own trigger table, **narrower** than general speech verbs: 谈 / 談 / 扯 /
# 讨论 / 討論 sound unnatural here (工作别扯了 is marginal; 工作别讨论了 needs an object).
# Separate the constant because hardcoded template forms once matched without evidence recognizing them:
# 君の名は別提起了。 / 君の名は別提及了。 yielded zero matches, while simplified and 再-bearing
# 別再提起了 worked (codex P2). _ZH_EVIDENCE_WORDS now derives from it;
# test_directive_tables pins every accepted form + 了 as Chinese evidence.
_ZH_PREPOSED_SAY_VERBS = ("提起", "提及", "说", "說", "提", "聊", "讲", "講")


def _zh_verb_alternation(*, with_address: bool) -> str:
    """Build the verb alternation, compounds always ahead of their single-char prefix."""
    parts = list(_ZH_SAY_COMPOUNDS)
    if with_address:
        parts += list(_ZH_ADDRESS_VERBS)
    parts += list(_ZH_SAY_VERBS)
    # Atomic groups: placing compounds before single-character prefixes controls first matches, not **backtracking**.
    # In 别提起了。, 提起 matches first but remaining 了 cannot satisfy a two-unit object,
    # so the engine falls back to 提 and persists 起了 as a topic, contradicting this module's
    # explicit exclusion of objectless instructions (codex P2). Atomicity freezes verb choice, making the whole
    # match fail directly, as intended.
    return "(?>" + "|".join(parts) + ")"


_ZH_VERBS_WITH_ADDRESS = _zh_verb_alternation(with_address=True)
_ZH_VERBS_PLAIN = _zh_verb_alternation(with_address=False)

# Template 2/4 endings may contain fillers (就 / 的事 / 这个…), belonging to sentences rather than
# topics. Never casually consume them in regex: lazy prefixes prefer putting the **topic's final
# character** into optional groups, persisting 他的成就别提了。 as 他的成. Preceding-character denylists cannot help:
# words ending 就 form an open set (成就 / 迁就 / 功成名就 / 一蹴而就 / 練就 / 鑄就…), and every omission
# truncates a real topic (codex P2).
#
# Compare **after extraction** instead: if a term equals another term + one filler, it is the same
# topic with an extra tail; discard the longer. This criterion never guesses word boundaries, inspecting only actually extracted
# terms in one sentence. Drop 股票就 because 股票 also appears, but retain 功成名就 because no
# 功成名 term exists.
_ZH_TRAILING_FILLERS = (
    "就", "的事", "的", "这个", "這個", "这事", "這事",
    "这话题", "這話題", "这件事", "這件事",
)

# ---------------------------------------------------------------------------
# Semantically empty terms: persist, but exclude from hard output blocking.
# ---------------------------------------------------------------------------
# 别再讲这个了 grammatically **has** an object and regex extracts 这个 normally,
# a pseudo-object wholly dependent on context and meaningless outside that turn.
#
# Harm is **asymmetric**, the entire reason for this table:
#   · Prompt injection is harmless: - 这个 is unactionable model noise. Extraction
#     behavior is already pinned by extensive tests (script parity and compound-guard main
#     examples use 这件事), outside this change's scope.
#   · Proactive output hard gates instead use **substring matching** (generation._proactive_directive_hits).
#     这个 is extremely frequent Chinese; persisting it drops almost every proactive utterance,
#     silencing the feature until TTL expiry, now up to 30 days after escalation,
#     with no current user interface to view or delete the entry.
#
# Apply criteria on the **consumption side**, preserving complete soft prompt constraints (context makes another noise line
# harmless), while hard blocking skips these terms. Contain harm where it is amplified.
#
# This dimension is a **closed set**, unlike open-ended 就 suffixes: pure references are finite function words,
# not content words. Enumeration works here; _ZH_TRAILING_FILLERS needs post-extraction comparisons.
# Ask what the isolated word denotes: 这个 denotes nothing specific; 加班 denotes overtime.
# Skip only terms **entirely equal** to listed words; 这个项目 has a meaningful head and still blocks.
# This table needs **no zh-TW key**, unlike tables governed by PROMPT_ZH_TW:
# it is not a prompt template rendered through _loc, whose missing zh-TW entries would give traditional users
# English. It is criterion vocabulary; is_semantically_empty_term matches the **union**
# of all locales, never locale-key lookup. Traditional forms (這個 / 那個 / 這件事)
# inline in zh already match traditional users. A separate zh-TW key creates duplicate maintenance,
# inviting one-sided omissions, a drift problem this module has encountered four times
# (see _ZH_NEG's derive-don't-copy comment).
# **Personal and demonstrative pronouns are one dimension**, not two. Isolated references
# 我 / me / 我们, like 这个, denote participants in the speaking situation,
# not topic content. Harm is **greater**: 别再提我了 /
# stop talking about me is a natural use, extracting
# me, which experimentally drops 3/3 ordinary English proactive drafts (Hey, let me know how the build
# went!, etc.). Word boundaries cannot rescue this because me is already a complete word.
# Same P1 family originally motivating it -> favorite protection; escalated TTL
# increases consequences from three days to up to thirty.
_SEMANTICALLY_EMPTY_TERMS_BY_LOCALE: dict[str, frozenset[str]] = {  # noqa: PROMPT_ZH_TW  # Criterion vocabulary, not rendered templates; traditional forms inline in zh.
    "zh": frozenset({
        "这个", "這個", "那个", "那個", "这些", "這些", "那些",
        "这事", "這事", "那事", "这件事", "這件事", "那件事", "那件事",
        "这话题", "這話題", "那话题", "那話題",
        "这个话题", "這個話題", "那个话题", "那個話題",
        "这种事", "這種事", "那种事", "那種事",
        "这样的事", "這樣的事", "这类事", "這類事",
        "刚才的事", "剛才的事", "刚刚的事", "剛剛的事",
        "这一切", "這一切", "那一切",
        # Personal / reflexive / possessive. Single-character 我 / 你 / 他 cannot reach _TERM_MIN_LEN=2,
        # so extraction cannot store them anyway; omit them as noise.
        "我们", "我們", "咱们", "咱們", "你们", "你們",
        "他们", "他們", "她们", "她們", "它们", "它們",
        "自己", "我自己", "你自己", "他自己", "她自己", "自个儿", "自個兒",
        "我的", "你的", "他的", "她的", "我们的", "我們的", "你们的", "你們的",
    }),
    "en": frozenset({
        "this", "that", "these", "those", "it", "them",
        "this thing", "that thing", "this topic", "that topic",
        "this stuff", "that stuff", "this one", "that one",
        "the topic", "the subject", "anything", "everything", "all this",
        # Personal / reflexive / possessive; single-character i cannot reach _TERM_MIN_LEN.
        "me", "you", "we", "us", "him", "her", "he", "she",
        "my", "your", "our", "his", "hers", "its", "their", "theirs",
        "mine", "yours", "ours",
        "myself", "yourself", "yourselves", "ourselves",
        "himself", "herself", "itself", "themselves", "oneself",
    }),
    "ja": frozenset({
        "これ", "それ", "あれ", "この話", "その話", "あの話",
        "この件", "その件", "あの件", "こんな話", "そんな話",
        "この話題", "その話題",
        # Personal / reflexive. Single-Kanji 私 / 僕 / 君 / 俺 cannot reach _TERM_MIN_LEN.
        "わたし", "あたし", "ぼく", "おれ", "あなた", "きみ", "おまえ", "お前",
        "自分", "自分自身", "私たち", "僕たち", "俺たち", "わたしたち",
        "あなたたち", "君たち", "私自身",
    }),
    "ko": frozenset({
        "이거", "그거", "저거", "이것", "그것", "저것",
        "이 얘기", "그 얘기", "이 이야기", "그 이야기",
        "이 일", "그 일", "이 주제", "그 주제",
        # 인칭 / 재귀
        "우리", "우리들", "저희", "너희", "당신", "그들", "그녀",
        "자기", "자신", "자기자신", "제가", "저는",
    }),
    "ru": frozenset({
        "это", "то", "этом", "этому", "об этом", "эту тему", "эта тема",
        # Личные / возвратные / притяжательные
        "мы", "вы", "он", "она", "они", "оно",
        "меня", "тебя", "нас", "вас", "его", "её", "ее", "их",
        "мне", "тебе", "нам", "вам", "себя", "себе",
        "мой", "твой", "наш", "ваш", "свой", "моя", "твоя", "наша", "ваша",
    }),
    "es": frozenset({
        "esto", "eso", "aquello", "este tema", "ese tema", "esta cosa",
        # Personales / reflexivos / posesivos
        "yo", "tú", "tu", "él", "el", "ella", "ellos", "ellas",
        "nosotros", "nosotras", "vosotros", "vosotras", "usted", "ustedes",
        "mí", "mi", "ti", "te", "nos", "su", "sus",
        "mío", "mía", "tuyo", "tuya", "suyo", "suya",
        "mí mismo", "sí mismo", "ti mismo",
    }),
    "pt": frozenset({
        "isso", "isto", "aquilo", "esse tema", "este tema", "essa coisa",
        # Pessoais / reflexivos / possessivos
        "eu", "tu", "ele", "ela", "eles", "elas", "nós", "vós",
        "você", "vocês", "mim", "ti", "si", "me", "te", "nos", "vos",
        "meu", "minha", "teu", "tua", "seu", "sua", "nosso", "nossa",
        "dele", "dela", "si mesmo", "mim mesmo",
    }),
}

# Union across all locales. Like extraction itself, match by **union**, not matched-locale tables:
# mixed-language input is explicitly supported (stop saying 这个), and these words lack
# cross-language homographic ambiguity: every language treats them as references, never meaningful
# content words in another language. _TRIM_TRAIL_TOKENS_BY_LOCALE needs separation because of 唄-style
# same-code-point ambiguity, absent here.
# NFC-normalize during set construction, not only lookup: source literals may be decomposed due to editors/clipboard
# differences, defeating normalized queries. Matching requires both sides in the same form.
_SEMANTICALLY_EMPTY_TERMS = frozenset(
    unicodedata.normalize("NFC", t)
    for terms in _SEMANTICALLY_EMPTY_TERMS_BY_LOCALE.values() for t in terms
)


def term_needs_case_sensitive_match(term: str) -> bool:
    """Whether a term must be matched case-sensitively: a name spelled like a pronoun.

    English writes ``the US`` / the films ``Us`` and ``Her`` with capitals and
    the pronouns ``us`` / ``her`` without, so case is the one local signal that
    separates the two. Same criterion ``_trim_term`` already uses to keep
    ``Never Please`` intact while still stripping a trailing ``please``.

    ⚠️ **Only for terms that actually collide with the table.** Widening this to
    "any capitalized term" costs far more than it buys: ``Work`` (an IME
    capitalizing the first letter, or just a sentence-initial capture) would
    then stop matching ``work`` / ``WORK`` in a draft, a fresh miss on the
    ordinary path — while the collision this exists for is confined to terms
    whose casefold is in ``_SEMANTICALLY_EMPTY_TERMS``. Pinned by
    ``test_matcher_is_case_insensitive``.

    Complementary to ``is_semantically_empty_term`` — for a term whose casefold
    is in the table, exactly one of the two is true (lowercase → exempt from the
    hard gate, capitalized → gated but matched case-sensitively). Terms outside
    the table get False from both and follow the ordinary path.
    """
    normalized = unicodedata.normalize("NFC", term.strip())
    if not any(ch.isupper() for ch in normalized):
        return False
    return normalized.casefold() in _SEMANTICALLY_EMPTY_TERMS


def is_semantically_empty_term(term: str) -> bool:
    """Whether a term is a bare referent that means nothing outside its own turn.

    Consumers use this to decide whether a directive term is specific enough to
    hard-block output on. It is deliberately NOT applied at extraction time —
    see the table's comment for why the two sides differ.

    ⚠️ Capitalized terms are never exempt. ``stop talking about US`` (the
    country) and the films ``Us`` / ``Her`` all yield terms whose casefold lands
    on a pronoun in the table; exempting them means the hard gate skips a topic
    the user explicitly banned, which is strictly worse than before the pronoun
    entries existed. The proactive gate pairs this with a case-sensitive match
    for such terms, so ``US`` no longer matches the ``us`` in "Want us to…" —
    fixing only this half would silence proactive chat wholesale instead.

    ⚠️ NFC first. Accented Spanish/Portuguese entries (``él`` / ``mí`` / ``você``)
    can reach the store decomposed (``e`` + combining acute) from an IME or a
    pasted string, which is a different codepoint sequence from the composed
    form written in the table above — the lookup would silently miss and the
    pronoun would go back to hard-blocking every draft. The proactive gate
    normalizes for the same reason (``generation._normalize_for_match``); this
    predicate has to agree with it or the two disagree on the same term.
    """
    normalized = unicodedata.normalize("NFC", term.strip())
    if any(ch.isupper() for ch in normalized):
        return False
    return normalized.casefold() in _SEMANTICALLY_EMPTY_TERMS

# **One unit** allowed in topics. All four zh templates share one constant; never duplicate it.
#
# Explicitly exclude newlines: these captures originally used dot, naturally excluding newlines without DOTALL,
# but negated classes lose that property. Multiline terms swallow newlines plus **the next instruction**
# swallowed together ("别提工作\n别提加班" → term "工作\n别提加班").
#
# Exclude sentence punctuation too, or captures cross intended terminators for longer matches
# (功成名就别提了，功成名别提了。 -> 了，功成名别提). But **punctuation inside title/quotation
# brackets belongs to topics**: the comma in 电影《你好，李焕英》别提了。 belongs to the title; blanket exclusion
# truncates terms to 李焕英 (codex P2). One unit is a nonpunctuation, nonnewline character **or**
# a whole paired bracketed span, permitting punctuation but never newlines inside.
_ZH_BRACKET_PAIRS = (
    ("《", "》"), ("「", "」"), ("『", "』"), ("“", "”"), ("【", "】"),
    ("（", "）"), ("〈", "〉"), ("〔", "〕"), ("［", "］"), ("〖", "〗"),
    # Include ASCII too: "Everything, Everywhere"别提了。 / 电影(Hello, World)别提了。
    # are intact on parent, so omission regresses them (codex P2).
    # Exclude apostrophe ': it occurs inside English words (don't / it's), making pairing meaningless.
    # Include ASCII square brackets too: _TRIM_TRAIL already strips them externally, but absent pair-table
    # entries truncate [Hello, World]别提了。 at its comma to World (codex P2).
    ('"', '"'), ("(", ")"), ("[", "]"), ("{", "}"), ("<", ">"),
)
# Line separators: instructions never cross lines. This table solely defines that criterion; ``\\r\\n`` alone is insufficient.
# Python ``\\s`` also recognizes U+2028 / U+2029 / U+0085 / \\v / \\f / U+001C–U+001F,
# every omitted category permits cross-line matches (codex P2). Bracket bodies, topic classes, and horizontal whitespace derive here.
_ZH_LINE_SEP = r"\r\n\v\f\x1c-\x1f\x85\u2028\u2029"

# Horizontal whitespace, any whitespace excluding line separators. Defined here because _ZH_DIRECTIVE_AHEAD
# needs it before bracket-body construction. See _ZH_HSPACE comments below for the criterion.
_ZH_HSPACE_ONE = r"[^\S" + _ZH_LINE_SEP + r"]"
_ZH_HSPACE = _ZH_HSPACE_ONE + "*"


# **Initial characters** of all negations, for ASCII bracket-body temper (see _zh_bracket_body):
# negation initials inside bracket bodies indicate pairing spans two instructions. Derive from tables,
# automatically tracking negation additions.
_ZH_NEG_FIRST_CHARS = "".join(
    dict.fromkeys(neg[0] for neg in _ZH_NEG_SINGLES + _ZH_NEG_MULTIS)
)


# A **complete** instruction head: negation + optional 再 + speech verb, used for bracket-body temper.
# Require whole instructions, not negation characters. Hardcoded 别別 missed
# 不要 / 不许 / 甭; banning **initials** then overcorrected, reducing 电影(不可思议; 2020)别提了。
# to 2020, likewise 电影(莫名其妙; 2020) / 电影(休闲; 2020) (codex P2,
# three simplified regressions). Ordinary words often start 不 / 莫 / 休 / 别, making characters
# irrelevant; **following speech verbs** actually prove brackets span instructions.
# This also permits 别再提(告别版)。, because 别 precedes 版, not a verb.
# Use **guarded** negations and the **complete** verb table, matching zh templates' grammar,
# or the two disagree:
#   · Bare 别 misclassifies compounds in 电影(个别讨论; 2020)别提了。:
#     个别 + 讨论 looks imperative, loses bracket protection, and falls back to 2020.
#     Templates use _ZH_BIE, excluding preceding 个 / 告, so reuse it here.
#   · Omitting naming verbs (叫我 / 喊我 / 管我叫) conversely merges 别叫我价格<预算.别叫我
#     收入>目标. (codex P2, both simplified regressions).
#   · Negation + verb alone also omits 我不想 / 沒心情 / 懶得 instructions,
#     merging 别提价格<预算.我不想聊收入>目标. (codex P2, simplified regression).
_ZH_DIRECTIVE_AHEAD = (
    "(?:"
    + "(?:" + "|".join(_ZH_NEG_SINGLES_GUARDED + _ZH_NEG_MULTIS) + ")"
    + _ZH_HSPACE + "(?:再)?" + _ZH_HSPACE
    + _ZH_VERBS_WITH_ADDRESS
    + "|"
    + "(?:" + "|".join(_ZH_RELUCTANCE) + ")"
    + _ZH_HSPACE + "(?:再)?" + _ZH_HSPACE + _ZH_VERBS_PLAIN
    + ")"
)


def _is_ascii_delim(char: str) -> bool:
    """Whether a delimiter is ASCII, i.e. doubles as an operator in running text."""
    return char.isascii()


def _zh_bracket_body(lo: str, hi: str) -> str:
    """One bracketed run: bounded body, and symmetric pairs temper the negation."""
    banned = re.escape(hi) + _ZH_LINE_SEP
    # **Asymmetric ASCII** pairs (() [] {} <>) must not pair across sentence punctuation, commonly acting as
    # comparison signs/code fragments in Chinese. Unrelated instructions each containing one become one quotation:
    # 别再提价格<预算。别再提收入>目标。 merges into 价格<预算。别再提收入>目标,
    # whereas parent has two instructions (codex P2).
    # Fullwidth brackets omit this restriction: they introduce quotes, not operators, and real titles do contain
    # periods. Symmetric quotes omit it too, a measured previous decision: excluding punctuation truncates
    # "Everything. Everywhere" and produces template-2/4 nonwords; see lo == hi below.
    ascii_pair = lo != hi and _is_ascii_delim(lo)
    unit = f"[^{banned}]"
    if lo != hi:
        # Recognize **one level** of same-type nesting: regex otherwise closes at the first same-type closer,
        # so 《电影《你好》续集，第二章》别提了。 closes its outer span at the inner 》,
        # losing protection for the comma and persisting only 第二章; 《甲《乙》丙，丁》别提了。 disappears
        # entirely (base preserves both; codex P2). _zh_quoted_span_end depth scanning governs
        # **ending stripping**, not matching itself here.
        #
        # Regex cannot handle arbitrary depth, but titles practically nest one level (《X《Y》Z》).
        # Branches are mutually exclusive because character branches exclude lo / hi, preventing ambiguous backtracking.
        # Nested branches must also exclude punctuation, or engines bypass the restriction by consuming
        # <预算。别再提收入> as a whole nested level (caught in self-testing).
        inner_banned = re.escape(lo) + re.escape(hi) + _ZH_LINE_SEP
        # Exclude **sentence-final** punctuation only; retain semicolons as valid closed ASCII code content
        # (代码{foo;bar}别提了。 truncates to bar, parent 代码{foo;bar;
        # codex P2). 。！？ suffice to block that cross-sentence merging family.
        # Add only to inner_banned. These ASCII pairs are all **asymmetric**, so
        # unit = f"[^{banned}]" above is entirely overwritten in this branch;
        # adding to banned is dead code (mutation testing).
        if ascii_pair:
            # Exclude **fullwidth** sentence punctuation only. ASCII ? ! inside closed titles/code are
            # content: 电影(Who?)别提了。 loses all matches, while parent retains 电影(Who.
            #（codex P2）。
            # Fullwidth exclusion alone is insufficient: temper 别 別 too, using the **same** criterion
            # as symmetric quotes below. A quotation containing a whole instruction necessarily contains negation. Allowing ASCII punctuation
            # merges 别再提价格<预算.别再提收入>目标. into 价格<预算.别再提收入>目标,
            # whereas parent separates them (codex P2, all four {} [] () <> pairs).
            # Temper characters rather than patching another ASCII separator: separator punctuation is open-ended; replacing
            # periods with commas/semicolons still merges. Negation within bodies targets the cause: instructions mistaken
            # for one quotation. Cost affects only titles with **both** negation **and** punctuation inside ASCII brackets;
            # unpunctuated 告别版 still uses single-character branches intact.
            # **Derive** negation characters from tables, never copy. Hardcoded 别別 still merged
            # 不要提价格<预算.不要提收入>目标. / 不许提… / 甭提…
            # (codex P2, three simplified regressions). The same derive-don't-copy lesson,
            # third occurrence in one day.
            inner_banned += "。！？"
        # Temper nested branches too: they are **another path**, making outer character-only
        # lookaheads ineffective. Engines consume <预算.别提收入> as one nested level,
        # merging 别提价格<<预算.别提收入>目标>. again (codex P2,
        # simplified regression), the same site/pitfall as nested punctuation exclusion, encountered twice.
        nested = (
            re.escape(lo)
            + f"(?:(?!{_ZH_DIRECTIVE_AHEAD})[^{inner_banned}])"
            + f"{{0,{_TERM_MAX_LEN}}}{re.escape(hi)}"
        )
        unit = f"(?:(?!{_ZH_DIRECTIVE_AHEAD})[^{inner_banned}]|{nested})"
    if lo == hi:
        # Symmetric pairs, only ASCII quotes, easily mispair: lone quotes commonly denote inch marks /
        # code fragments. Two unrelated sentences each containing one become a quotation:
        # 尺寸5"别提了。尺寸6"别提了。 merges into 尺寸5"别提了。尺寸6, losing both
        # instructions (codex P2). Asymmetric brackets avoid this because 《 cannot be mistaken for a closer.
        #
        # Temper 别/別: quotations containing whole instructions necessarily contain negation. This prevents pairing
        # 尺寸5"别提了。尺寸6"别提了。 / 尺寸5"别提了，尺寸6"别提了。,
        # preserving both instructions. Titles are affected only with negation **and** punctuation; unpunctuated
        # "再别康桥" uses single-character branches intact.
        #
        # Deliberately **retain** punctuation (。！？；) and commas in character classes, a measured choice, not
        # omission. Coderabbit once reported the guard missing; technically true, but implementing it is worse:
        #   · Excluding commas truncates "Everything, Everywhere" to Everything (regression).
        #   · Excluding sentence punctuation reduces 别再提"Everything. Everywhere"。 to Everything,
        #     and 别再提"你好。世界"好吗？ to 你好. Worse, preposed templates produce
        #     **nonwords**: "工作。加班"别提了。 becomes 加班,
        #     关于"工作。加班"就别提了。 becomes 加班"就, both worse than parent.
        #   · Cost direction: retaining punctuation at worst adds a span (我不想聊尺寸5"工作。尺寸6"加班。
        #     stores it all), keeping the real topic intact; exclusion consumes characters into nonwords. Prefer more over less.
        # Use the **same** criterion as ASCII above. Previously only 别別 were blocked,
        # still merging 不要提价格"预算.不要提收入"目标. (codex P2,
        # simplified regression). Both derive from _ZH_DIRECTIVE_AHEAD rather than separate copies.
        unit = f"(?!{_ZH_DIRECTIVE_AHEAD}){unit}"
    # Length must be **bounded**: unbounded * scans to end at every opener looking for closers,
    # making input "《" * 8000 quadratic, measured 2.6s while record_from_text runs synchronously
    # on every user message (codex P2). Use _TERM_MAX_LEN: longer bracketed spans
    # are discarded by final length filtering anyway, losing no storable terms.
    return f"{re.escape(lo)}(?:{unit}){{0,{_TERM_MAX_LEN}}}{re.escape(hi)}"


_ZH_BRACKET_RUN = "(?:" + "|".join(
    _zh_bracket_body(lo, hi) for lo, hi in _ZH_BRACKET_PAIRS
) + ")"

# Two depth-scan tables. Separate **symmetric** pairs, only ASCII quotes: lone quotes
# are ordinary inch marks/emoticons like :(, never unfinished quotations. _zh_bracket_body already
# decided this in its advantage-over-excluding-openers comment. Treating them as hard boundaries here
# contradicts that, retaining 好吗 in 别再提5"屏幕好吗。 because of an inch mark (codex P2).
# Lone asymmetric 《 truly denotes unfinished quotation and still counts.
_ZH_CLOSE_FOR_OPEN = {lo: hi for lo, hi in _ZH_BRACKET_PAIRS if lo != hi}
_ZH_SYMMETRIC_DELIMS = frozenset(lo for lo, hi in _ZH_BRACKET_PAIRS if lo == hi)
# Asymmetric counterparts in both directions, letting _strip_trail identify unpaired positions.
_ZH_COUNTERPART = {
    ch: other
    for lo, hi in _ZH_CLOSE_FOR_OPEN.items()
    for ch, other in ((lo, hi), (hi, lo))
}


def _zh_quoted_span_end(text: str) -> int:
    """Index past the last quoted run: a tail starting there is outside the quotes.

    _trim_term distinguishes sentence particles from title components through this. Requiring prefixes to end
    exactly with closing brackets is insufficient: 電影《你好》續集好嗎 has an intervening
    ordinary modifier, defeating that proxy (codex P2).
    """  # noqa: DOCSTRING_CJK
    # Scan by **depth**, never regex-search the last complete quote: regex pairs
    # outer openers with **inner** closers and misrecords same-type nesting endpoints,
    # treating 《电影《你好吗》续集好吗》 as ending at the inner 》, then stripping final 好吗
    # as sentence tone (codex P2).
    # Ignore lone symmetric quotes (odd count) entirely: inch marks/emoticons, not quotation.
    # Otherwise they hide **all following** brackets: inch marks in 5"屏幕《你好吗》 begin quotes,
    # excluding later 《…》 from scanning and stripping 好吗 as sentence tone, even truncating
    # 《你 (codex P2).
    paired_symmetric = {
        ch for ch in _ZH_SYMMETRIC_DELIMS if text.count(ch) % 2 == 0
    }
    end, stack = _zh_scan_quoted(text, paired_symmetric, frozenset())
    # Lone ASCII openers need more than ignoring unfinished quotations: at stack bottom they prevent tracking
    # **later** valid closers. In 别再提价格<预算《你好吗》。, < makes
    # 《你好吗》 closure useless, stripping 好吗 as sentence tone and truncating the title to 《你,
    # unlike intact parent (codex P2). Rescan them as **ordinary characters**, rather than clearing stacks
    # afterward when positions are already lost.
    if stack and all(_is_ascii_delim(ch) for ch, _pos in stack):
        end, stack = _zh_scan_quoted(
            text, paired_symmetric, frozenset(pos for _ch, pos in stack)
        )
    # Remaining unclosed **asymmetric** openers imply quotations extending through the end (电影《好不好).
    # Lone symmetric quotes do not count; they are inch marks/emoticons (see _ZH_SYMMETRIC_DELIMS).
    # Lone ASCII openers do not count either: fullwidth （ 「 《 【 introduce quotations in Chinese,
    # so unmatched means truncated titles. ASCII < { ( [ commonly denote comparison/code and need not pair.
    # Require whether openers are ASCII, a closed criterion without enumerating operators.
    return len(text) if stack else end


def _zh_scan_quoted(
    text: str, paired_symmetric: set, ignored: frozenset
) -> Tuple[int, List[Tuple[str, int]]]:
    """One depth-aware pass; ``ignored`` holds indices to treat as plain text."""
    end = 0
    stack: List[Tuple[str, int]] = []
    symmetric_open: str | None = None
    for index, char in enumerate(text):
        if index in ignored:
            continue
        if symmetric_open is not None:
            if char == symmetric_open:
                symmetric_open = None
                if not stack:
                    end = index + 1
            continue
        if stack and char == _ZH_CLOSE_FOR_OPEN[stack[-1][0]]:
            stack.pop()
            if not stack:
                end = index + 1
        elif char in _ZH_CLOSE_FOR_OPEN:
            stack.append((char, index))
        elif char in paired_symmetric:
            symmetric_open = char
    return end, stack


_ZH_PLAIN_CHAR = r"[^，。！？；,.!?;" + _ZH_LINE_SEP + r"]"
# Only template 2's **preposed** topics temper bare 关于/關於: 关于 X 就别提了 belongs to
# template 4; prefixes consuming it characterwise make 我觉得关于股票就别再讲了 produce an extra
# 我觉得关于股票就 term.
#
# Never put this in shared single-character branches, forbidding 关于 anywhere in topics and rejecting
# verb-object structures: 别再提关于公司的传闻。 changes from 关于公司的传闻 to
# no match (codex P2). Preposed topics and post-verbal objects differ; guard only the former.
_ZH_PLAIN_CHAR_NO_GUANYU = r"(?!关于|關於)" + _ZH_PLAIN_CHAR

# ， briefly entered preposed-topic character classes to make 工作，还是别提了。 produce a long
# 工作，还是 span instead of function word 还是. Reverted, since it overturns the **previously settled**
# no-cross-clause criterion: 算了，工作别提了。 would store 算了，工作. The two directions
# are structurally identical (X，Y别提了), differing only lexically in which half is the topic; no structural criterion exists.
# Having chosen the right half, do not reverse repeatedly within one PR.
# Post-pause 就 (_ZH_PAUSE_THEN_JIU) is orthogonal and unaffected.

# Accept only **horizontal** whitespace after pauses. \s consumes newlines too,
# ``别再提，\n工作正常。`` stores the **next line** as its object,
# unlike parent's complete miss (codex P2). Same reason as subject spacing: instructions never cross lines.
# Require **any whitespace except newlines**, not selected characters. Enumeration excludes pasted NBSP /
# U+202F / U+2009 from webpages/mobile inputs, making 别<NBSP>再
# <NBSP>提工作。 lose all matches, unlike parent's whitespace quantifier (codex P2).
# Newlines alone must be excluded because instructions never cross lines; express a negative criterion.
# Exclude **all** line separators, not only CR/LF. Python whitespace also recognizes
# U+2028 / U+2029 / U+0085 / vertical tab / form feed / U+001C–U+001F, so CR/LF-only exclusion
# still lets 別再提<U+2028>案をお願いします。 span visual lines (codex P2).

# The whole unit must be **atomic**, preventing ReDoS. Character branches also match 《, making
# 《a》 match either one bracketed unit or individual characters. Repeating this ambiguity in
# {2,30}? creates exponential backtracking: 别提 + thirty 《a》 spans takes 1.3s on
# a path handling **every user message** (codex P1).
#
# Atomic groups (?>…) in Python 3.11+ freeze branch choices at each position, eliminating
# ambiguity. Better than excluding openers from character branches: lone " / ( as inch marks /
# emoticons :( still enter topics as ordinary characters, never hard boundaries.
# ASCII periods/commas between word characters are **inside identifiers**, not sentence punctuation:
# Python 3.11别提了。 / example.com别提了。 / 价格1,000元别提了。 previously
# stored only 11 / com / 000元, while base preserved all three (codex P2).
#
# Both-side criteria are **negative** (not whitespace/punctuation), never enumeration of word characters.
# Repeated enumeration fixes still missed cases: [0-9A-Za-z] excludes café.com / Дом.ру /
# fullwidth １,０００ / IDN 例子.测试; replacing with \w still misses **combining marks**,
# truncating NFD café.com (e + U+0301) and Devanagari देवनागरी.com to com,
# because Python \w excludes Mn/Mc (codex P2 third round).
#
# Negative criteria cover everything: periods/commas are internal whenever **neither side is whitespace or punctuation**.
# Fullwidth commas have tighter rules below. Exclude fullwidth 。 entirely: no identifiers
# use it, and it is the most common sentence terminator.
# Sentence-final periods precede whitespace/end, failing right lookahead requiring a character.
#
# Widening to \w does **not** merge instructions: 别提工作.别提加班. remains two.
# Objects are lazy and terminators already include ASCII periods, so engines first try shorter matches,
# terminating normally; only invalid short matches consume periods into topics. Verified experimentally.
# Permit **whitespace** after ASCII commas: Hello, World is common. Unchanged non-whitespace
# lookaheads reject the comma as internal, restarting after it; Hello, World别提了。
# stores World, 关于Hello, World就别提了。 stores nonword World就 (both intact on
# parent; codex P2). Omitting whitespace creates asymmetry because Hello,World
# works. Forbid left whitespace: ' , x' denotes clause pauses, not identifiers.
# This does **not** overturn preposed topics never crossing clauses, which governs fullwidth ，. ASCII
# commas already cross on parent/current (算了,工作别提了。 -> 算了,工作).
# Use **bounded** [ \t]? instead of \s*: this zero-width lookahead cannot partition whitespace,
# but template-2/4 guards search literal \s*, so unbounded spelling blurs their criterion.
# One space covers Hello, World; additional spaces retain previous behavior.
# Permit whitespace **only** after commas, never periods. Period + space marks English sentence boundaries; accepting it makes That was bad. Work
# 别提了。 consume the preceding sentence into its topic. Same reason for excluding 。！？ from _ZH_PAUSE_CHARS:
# they end **sentences**. Initially applying one shortcut to the whole [.,] class
# manufactured inconsistency (coderabbit). Unspaced v1.2 remains covered by existing criteria.
# Maximum abbreviation letter count. Previously **three hardcoded** branches for 1–3 letters excluded four-letter
# Prof. / Capt., turning Prof. X别提了。 from parent's Prof. X into
# **zero matches** (one-character X fails minimum), while 关于Capt. America就别提了。
# persists nonword America就 (codex P2, simplified regression too).
# This requires **fixed-length branch enumeration**, since Python lookbehind forbids variable length, but enumerate
# bounded letter **counts**, not open-ended abbreviation words. Six covers Approx.;
# higher bounds increasingly merge uppercase sentence-final words (别再提Winter. Snow来了。) into topics.
# Cost direction is settled: retaining extra characters preserves real topics, while losing characters
# creates nonwords.
# Whitespace allowed after internal punctuation. Previously only **one** space, claiming it covered
# Hello, World sufficiently, disproven by titles pasted from webpages/PDFs commonly containing
# two spaces: Dr.  Who别提了。 reduces to Who; Prof.  X别提了。 disappears entirely;
# 关于Hello,  World就别提了。 persists nonword World就 (codex P2, three simplified regressions).
# Still **bounded** ({1,8}): this is a per-message hot path, and template-2/4 whitespace guards
# detect unbounded forms; * / + would blur their criterion.
_ZH_IDENT_GAP = _ZH_HSPACE_ONE + "{1,8}"
# Comma whitespace is **optional**, preserving unspaced Hello,World. Express
# {0,8}, never append ? to _ZH_IDENT_GAP: {1,8}? is a **lazy quantifier**,
# not optional {1,8}, immediately failing all Hello,World forms (caught after editing).
_ZH_IDENT_GAP_OPT = _ZH_HSPACE_ONE + "{0,8}"
_ZH_ABBREV_MAX_LETTERS = 6
_ZH_ABBREV_PERIOD = "".join(
    f"|(?<![A-Za-z](?-i:[A-Z])(?-i:[a-z]{{{tail}}}))"
    f"(?<=(?-i:[A-Z])(?-i:[a-z]{{{tail}}}))"
    + r"\.(?=" + _ZH_IDENT_GAP + r"(?-i:[A-Z]))"
    for tail in range(_ZH_ABBREV_MAX_LETTERS)
)
_ZH_IDENT_PUNCT = (
    r"(?<=[^\s，。！？；,.!?;])\.(?=[^\s，。！？；,.!?;])"
    # Abbreviation periods **permit spaces**: Dr. Who / Mr. Robot / U.S. Army
    # were treated as boundaries, restarting topics afterward and storing Who (parent intact; codex P2).
    # Also block real sentence boundaries: That was bad. Work别提了。 should store Work (coderabbit).
    # Require **abbreviation shape**: initial uppercase and bounded letter count; see _ZH_ABBREV_PERIOD:
    #   U. (1) / Dr. Mr. (2) / Mrs. Ave. (3) match;
    #   lowercase-initial bad. and letter-prefixed ABad. do not.
    # Separate three fixed-length branches because Python lookbehind forbids variable length.
    # Word starts require no preceding ASCII letter, not \b: 于 and D in 关于Dr. Who
    # are both Unicode word characters without a boundary, making \b fail.
    # Keep (?-i:...): whole-template IGNORECASE makes bare [A-Z] match lowercase too,
    # degrading criteria to any period followed by space + letter (caught in self-testing).
    # Use _ZH_HSPACE_ONE, not [ \t], recognizing pasted Dr.<NBSP>Who
    # and sharing horizontal-whitespace criteria with other sites (codex P2).
    + _ZH_ABBREV_PERIOD
    + r"|(?<=[^\s，。！？；,.!?;]),(?=" + _ZH_IDENT_GAP_OPT + r"[^\s，。！？；,.!?;])"
    # Permit fullwidth commas **between digits only**. Common Chinese clause separators otherwise let preposed topics
    # cross clauses (算了，别提工作。 persists 算了，工作); their genuine identifier
    # usage is thousands grouping (价格1，000元), requiring digits on both sides without broader rules.
    r"|(?<=[0-9０-９])，(?=[0-9０-９])"
)

_ZH_TOPIC_CHAR = f"(?>{_ZH_BRACKET_RUN}|{_ZH_IDENT_PUNCT}|{_ZH_PLAIN_CHAR})"
_ZH_TOPIC_CHAR_NO_GUANYU = (
    f"(?>{_ZH_BRACKET_RUN}|{_ZH_IDENT_PUNCT}|{_ZH_PLAIN_CHAR_NO_GUANYU})"
)


def _zh_topic(minimum: int, maximum: int, *, block_guanyu: bool = False) -> str:
    """Topic capture body: ``minimum`` units, except a single bracketed run counts.

    An entire bracketed span is **one** unit, so {2,30} rejects standalone
    《你好，李焕英》别提了。 with only one unit. It contains ≥3 characters and would survive
    length filtering, so separately permit bracket-leading forms (codex P2).

    block_guanyu serves template 2 preposed topics only; see _ZH_PLAIN_CHAR_NO_GUANYU.
    Temper only the **first** unit, never every unit. Global temper truncates or loses topics **internally**
    containing 关于: 这部关于爱的电影别提了。 stores only
    爱的电影; 电影关于爱别提了。 loses all matches (both intact on parent; codex P2).
    Guard prefixes consuming sentence-initial 关于 characterwise, possible only at the **start**.
    """  # noqa: DOCSTRING_CJK
    unit = _ZH_TOPIC_CHAR
    first = _ZH_TOPIC_CHAR_NO_GUANYU if block_guanyu else unit
    return (
        f"(?:{_ZH_BRACKET_RUN}{unit}{{0,{maximum - 1}}}?"
        f"|{first}{unit}{{{minimum - 1},{maximum - 1}}}?)"
    )

# Template-1 final particles allowed between terms and terminators. Paired with _TRIM_TRAIL_TOKENS' zh
# section: accept here, strip there; omitting either persists attached particles.
# Omit 唄 entirely from regex and trim: traditional 呗 means song in Japanese
# (子守唄 / 花の唄). Trim inclusion truncates ja terms; regex-only inclusion also truncates
# zh: 别再提花の唄。 persists 花の (two codex P2 rounds). 呗 is northern
# colloquial speech, not Taiwan 唄 usage, making the cost unjustified. Likewise omit 囉 / 啰
# entirely; see _TRIM_TRAIL_TOKENS_BY_LOCALE comments on 嘍囉 / 喽啰.
# This and trim are **two** handwritten lists, prone to drift. Equality is unnecessary: particles omitted
# here enter terms and trim removes them equivalently. But **present here, absent in trim** guarantees
# bugs, leaving particles forever. test_directive_tables pins this subset invariant.
# Particles already present on parent, separately defined for _ZH_OBJECTLESS_AHEAD (see its comments):
# objectless criteria accept only these; new Taiwan particles would wrongly reject 别再提好咧。.
# Define particles **once** as a string, deriving regex and Chinese-only evidence. Separate handwritten
# copies once drifted: regex consumed 啊 齁 欸 誒 while evidence omitted them,
# losing 別提君の名は啊。 entirely although the 吧 variant worked
# (codex P2). See _ZH_ZH_ONLY_FINAL_PARTICLES.
_ZH_BASE_FINAL_PARTICLE_CHARS = "了啊呀嘛哦呗吧啦呢"
_ZH_EXTRA_FINAL_PARTICLE_CHARS = "喔唷齁欸誒咧喲"
_ZH_FINAL_PARTICLE_CHARS = (
    _ZH_BASE_FINAL_PARTICLE_CHARS + _ZH_EXTRA_FINAL_PARTICLE_CHARS
)
# Accept only **horizontal** whitespace before particles. \s* reaches next-line particles after complete
# triggers, persisting the previous line: 別再提工作 followed by 吧。 stores 工作
# (codex P2). Eighth no-cross-line case, sharing criteria with seven others.
_ZH_BASE_FINAL_PARTICLES = (
    "(?:" + _ZH_HSPACE + "[" + _ZH_BASE_FINAL_PARTICLE_CHARS + "])"
)
_ZH_FINAL_PARTICLES = "(?:" + _ZH_HSPACE + "[" + _ZH_FINAL_PARTICLE_CHARS + "])"

# Verb-object pause punctuation arises from typing/ASR (别再提，工作。 / 别叫我，"笨蛋"。).
# Parent consumed it via .{1,40}? then trimmed. Excluding punctuation from topic
# units in this PR loses whole instructions (codex P2). Keep separators **outside captures**, absent from terms.
# Place before _ZH_OBJECTLESS_AHEAD: its lookahead interprets punctuation immediately after verbs
# as no object, rejecting 别再提，工作。 unless separators are consumed first.
# Include 、 ： too. Initially considered unnecessary because topic classes accept them as ordinary
# characters subsequently trimmed, so 别再提、工作。 still yields 工作. But short topics
# with **ambiguous endings** fail: 别提：好咧。 captures ：好, punctuation satisfying
# two-unit minimum while optional particles consume 咧; trim leaves one-character 好 and discards it,
# unlike parent's 好咧 (codex P2). Exclude separators from captures so they cannot satisfy minimums.
# Internal \s* must also be atomic, between two (?>\s*) groups; otherwise they
# repartition the same whitespace and fail template-2/4 guards immediately.
# Include semicolons too, excluded from topic classes as instruction terminators but previously absent
# here, making 别再提；工作。 / 工作；别提了。 miss entirely while parent stores
# 工作 (codex P2). This dimension is closed: only clause punctuation in this module's own
# terminator table fits here. Deliberately exclude periods/questions/exclamations because they end **sentences**,
# preventing cross-sentence binding.
# Derive both constants from one string; duplicated tables inevitably drift.
_ZH_PAUSE_CHARS = "，、：；,:;"
_ZH_TOPIC_SEPARATOR = f"(?:[{_ZH_PAUSE_CHARS}]" + f"(?>{_ZH_HSPACE}))?"
# Template 2 only: consume 就 only **after explicit pauses**.
# Template 2 covers all X别提了, including 成就 / 迁就 / 功成名就, so
# cannot unconditionally include (?:就)? like template 4. Pause punctuation provides **hard word boundaries**: 就 in 工作，就别提
# 了。 cannot end the preceding word. Keep 就 inside separator branches, protecting both cases:
# consume nothing without pauses, but avoid zero matches with pauses, exposed when removing (?:就)?.
# Pauses can follow 就 too (工作，这件事，就，别提了。). Accepting only the earlier pause
# restarts after the first comma and persists filler 这件事, unlike parent's
# 工作，这件事，就, at least containing the real topic (codex P2).
_ZH_PAUSE_THEN_JIU = (
    f"(?:[{_ZH_PAUSE_CHARS}]" + f"(?>{_ZH_HSPACE})"
    + r"(?:就)?" + f"(?>{_ZH_HSPACE})"
    + f"(?:[{_ZH_PAUSE_CHARS}]" + f"(?>{_ZH_HSPACE}))?)?"
)

# Objectless lookahead: if only **one character + final particles** separates verbs from punctuation, that character is a
# result complement (说完 / 提上 / 聊死), not an object. Exclude the whole instruction, as this module's
# documentation explicitly requires for objectless commands.
#
# This gate is **independent of object minimum**, never replaced by lowering minimum to one. Minimum one lets
# lazy objects + optional particles prioritize treating topic-final characters as particles, reducing 别提钱的事。 to 钱,
# then discarding below minimum. Likewise 别再提好咧。, both base defects fixed here.
# Minimum two preserves them; this lookahead separately prevents 别说完了。 fabricating object 完了.
#
# Require the **count one**, not enumerated complements. Listing only 到/起/及 produced
# 别说完了。 -> 完了; 别提上了。 -> 上了; 别聊死了。 ->
# 死了 (codex P2 reported 完; measurements found 光/够/死/上 too). Result complements form an open set
# no enumeration exhausts. Parent already rejects every one-character object through length filtering, so counting
# covers all and matches parent exactly.
#
# Accept only parent particles (_ZH_BASE_FINAL_PARTICLES); new Taiwan colloquialisms
# classify 别再提好咧。 as 好 + 咧 and reject it, unlike parent's
# 好咧. These characters also end common words, motivating the extra-character preference.
#
# Never include 的事 either: 别提钱的事。 becomes 钱 + 的事 and gets rejected.
_ZH_OBJECTLESS_AHEAD = (
    "(?!"
    + r"\s*" + _ZH_PLAIN_CHAR + "?"
    + _ZH_BASE_FINAL_PARTICLES + "{0,3}"
    + r"\s*(?:[，。！？；,.!?;]|$)"
    + ")"
)

# (2) Kana: never simply drop matches containing Kana; banned **objects themselves** are often Japanese
# proper names (别再提ドラえもん / 別叫我お兄ちゃん). Structure is Chinese, Kana only the topic,
# so dropping loses explicit user preferences (codex P2).
# Therefore all three conditions must hold before classifying Japanese sentences:
_KANA_RE = re.compile(r"[぀-ゟ゠-ヿｦ-ﾟ]")
# Han, including shared Chinese/Japanese ranges, matching (?<=[一-鿿]) in _JA_GRAMMAR_RE;
# define the range once.
_HAN_RE = re.compile(r"[一-鿿]")

# (2a) Positive Chinese evidence: forms/combinations absent from modern Japanese, including simplified forms and traditional
# forms differing from shinjitai (說/説, 這, 關/関, 沒/没, 稱/称), plus combinations absent from Japanese
# (叫我 / 不想…). Any occurrence in a match proves Chinese, with Kana naming the topic.
# Exclude 不要: itself Japanese ふよう, making 不要提出書類 incorrectly count as Chinese evidence.
# Verify Japanese shinjitai individually before additions: 没 and 称 were wrongly admitted, although they **are**
# standard Japanese forms (没収 / 名称), not simplified-Chinese-only 沒 / 稱 variants. Errors short-circuit
# the entire guard: 地域別講座の名称を確認します。 becomes Chinese due to 称,
# bypassing particle checks altogether (codex P2). Each following form differs from Japanese:
# 别->別 说->説 讲->講 谈->談 讨->討 论->論 关->関 话->話 题->題 愿->願 懒->懶 许->許
# 为->為; 這 / 甭 are absent from Japanese; 說 / 關 / 沒 / 稱 are divergent traditional forms.
#
# Single characters do not cover all _ZH_NEG negations: 不准 / 莫 / 休 / 不要 have none
# in the class above (traditional 許 in 不許 is absent too). 不准提君の名は。 becomes Japanese
# and is dropped, unlike working parent behavior (codex P2; 不许 survives only because 许 happens
# to belong, sheer coincidence).
#
# Add **structure**, never shared Kanji: negation + optional 再 + speech verb uses two
# closed sets whose adjacency itself is Chinese syntax; Japanese never puts 不准提 together.
# Adding 准 / 莫 / 休 instead short-circuits guards, as 没 / 称 did over two rounds.
#
# Both boundaries are essential, **measured directly** by this file's Japanese corpus:
#
# (a) Left boundary: Japanese 別 is a categorizing suffix after 地域 / 部門 / 商品 / 年齢 /
#     性, followed by nouns. 地域別提案をお願いします。 satisfies negation + speech verb,
#     misclassifying as Chinese and persisting 案をお願いします. Likewise 地域別談話でも可。.
#     Initially only 講 was suspected (休講 / 特別講座), but corpora proved 提 / 談 collide too.
#     Nouns preceding 別 form an open set; _BIE_COMPOUND_LEFT cannot cover 地域別.
#     But **Japanese 別 always follows Kanji**, while Chinese negations follow sentence starts/pronouns/punctuation.
#     Require preceding characters neither Han nor Kana, a closed criterion without enumerating nouns.
#     Kana checks arrived after missing a round: 別 in カテゴリ別提案書。 / テーマ別討論スレ
#     follows Katakana, escaping Han-only exclusion.
#
# (b) Right boundary: Japanese 休講, canceled classes, is valid word-initially; exclude it independently,
#     matching _ZH_XIU (?!講).
#
# Remaining residual: Japanese starting 不要提案… (不要 = ふよう) may misclassify. Normally written
# 不要な提案 and requiring all other zh-template structure, this cost is acceptable.
# Left boundaries apply **only to 别/別**: Japanese 〜別 ambiguity is exclusive; 莫 / 休 / 甭
# are not Japanese noun suffixes. Applying to the whole family blocks ordinary Chinese subjects:
# 我莫再提君の名は。 / 她莫再提地域の話。 work on parent but become wholly rejected
# by the Japanese guard with left boundaries (codex P2).
_ZH_NEG_JA_AMBIGUOUS = ("别", "別")
# Left neighbors of Japanese 〜別: **do not enumerate**. Han-only missed Katakana,
# Kana additions missed Latin/digits/closers, then β escaped
# (Greek, モデルβ別提案). Character sets are open; enumeration always misses something (three codex P2 rounds).
#
# Use **negative criteria**: Chinese instruction negations always **begin clauses**, so left neighbors are only start,
# whitespace, or clause punctuation. Anything else (letters in any script, digits, closers) indicates 別 attaches
# after a word, the Japanese 〜別 suffix.
#
# Deliberately exclude closing brackets as clause starts: 「地域」別提案 is a Japanese label. **Openers**
# conversely start clauses (「別提君の名は。」 / （別提君の名は。）), since Japanese 〜別
# cannot immediately follow openers. Exclusion invalidates traditional instructions written in quotes,
# while simplified “别提…” survives through single-character evidence, creating script asymmetry.
# （codex P2）。
# Accept **asymmetric** openers only, deriving from _ZH_CLOSE_FOR_OPEN with existing role separation. Symmetric
# " / ' serve both roles; in "地域"別提案 they close, so admitting them dismantles the guard.
# Pronouns (你 妳 您 咱 请 們) pass through _ZH_SUBJECT_BEFORE_NEG rather than here.
_ZH_CLAUSE_START_LEFT = r"\s，。！？；、：,.!?;" + re.escape(
    "".join(sorted(_ZH_CLOSE_FOR_OPEN))
)
_ZH_NEG_UNAMBIGUOUS = tuple(
    "休(?!講)" if neg == "休" else neg
    for neg in _ZH_NEG_SINGLES
    if neg not in _ZH_NEG_JA_AMBIGUOUS
)
# Structural evidence has three branches based on **verb ambiguity**, never negation position.
#
# Three positional rounds let codex expose the next missing case each time: first only Han left neighbors,
# missing Kana / Latin / β; clause-start left neighbors then missed string-initial 別提案; requiring 再 only at
# string start still missed 今回は、別提案… and 「別提案…」, since Japanese compound nouns can begin
# after clause boundaries too. Position is not the criterion.
#
# Actual criterion: can 別 form a **Japanese compound noun** with the following verb? Only four
# _ZH_SAY_VERBS_JA_SHARED forms do (別提案 / 別講座 / 別談話 / 別討論); remaining verbs
# 聊 扯 说 說 讲 谈 cannot. Therefore:
#   (a) Unambiguous negations (莫 / 休 / 甭) are not Japanese noun suffixes, imposing no verb restrictions;
#   (b) Ambiguous 别 別 + **Japanese-absent** verbs disambiguate themselves, without restrictions
#       (請 in 請別扯君の名は。 cannot enter subject allowlists because it is Japanese Kanji,
#       so left-boundary paths cannot work; rely on verbs, codex P2);
#   (c) Ambiguous negation + **shared** verbs require both clause starts and 再.
#       別再提案 is invalid Japanese (requires 別の再提案), while 地域別再提案
#       is blocked by left boundaries.
#
# Cost of (c): 算了，別提君の名は。 / 「別提君の名は。」 return to zero matches.
# This is the residual long documented in _is_japanese_sentence_match:
# traditional triggers + shared verbs + titles with Japanese particles, locally indistinguishable. Parent also
# misses these, so this adds no coverage for that case rather than regressing.
_ZH_ZH_ONLY_VERBS = tuple(
    v for v in _ZH_SAY_COMPOUNDS + _ZH_SAY_VERBS if v not in _ZH_SAY_VERBS_JA_SHARED
)
# Subjects/honorifics permitted before single negations form a closed class pinned by equality. Adding
# any Japanese Kanji opens the guard's left boundary; 俺 is both a northern Chinese subject and common Japanese Kanji,
# admitting 俺別提案をお願いします。 as persisted Chinese instructions.
# 們 is a traditional plural suffix (我們 / 你們 / 咱們); Japanese uses neither 們 nor 们,
# so following 別 cannot be Japanese 〜別. Without it, traditional users saying
# 我們別再提君の名は。 lose the whole instruction while simplified 我们别… survives through 们 evidence
# elsewhere (codex P2).
_ZH_SUBJECT_CHARS = "你妳您咱请們"

# Ambiguous verbs' left boundaries additionally admit **subjects/honorifics**. 再 already disambiguates (別再提
# is invalid Japanese); remaining left checks only block 〜別 suffixes such as 地域別再提案,
# impossible after subjects/honorifics. Exclusion loses 請別再提君の名は。 while
# equivalent simplified 请别再提… works (codex P2).
# Permit 請 only here, **never** _ZH_SUBJECT_CHARS: Japanese Kanji would open
# an unguarded path for 請別提案をお願いします。 without 再.
_ZH_POLITE_BEFORE_NEG = _ZH_SUBJECT_CHARS + "請"
# The character left of 別 cannot begin a clause, shaping Japanese 〜別 suffix left context. Shares
# the lookbehind's **same table**, inverted for Python-side guards (see
# _is_japanese_sentence_match's 〜別 + compound noun + bare Kana case).
# Exclude _ZH_POLITE_BEFORE_NEG: subject/honorific evidence already returned successfully earlier.
_ZH_NON_CLAUSE_START_RE = re.compile(f"[^{_ZH_CLAUSE_START_LEFT}]")
_ZH_NEG_VERB_EVIDENCE = (
    "(?:"
    + "(?:"
    + "|".join(_ZH_NEG_UNAMBIGUOUS)
    + f"){_ZH_HSPACE}(?:再)?{_ZH_HSPACE}(?:"
    + "|".join(_ZH_SAY_COMPOUNDS + _ZH_SAY_VERBS)
    + ")"
    + "|(?:"
    + "|".join(_ZH_NEG_JA_AMBIGUOUS)
    + f"){_ZH_HSPACE}(?:再)?{_ZH_HSPACE}(?:"
    + "|".join(_ZH_ZH_ONLY_VERBS)
    + ")"
    + f"|(?<![^{_ZH_CLAUSE_START_LEFT}{_ZH_POLITE_BEFORE_NEG}])(?:"
    + "|".join(_ZH_NEG_JA_AMBIGUOUS)
    + f"){_ZH_HSPACE}再{_ZH_HSPACE}(?:"
    + "|".join(_ZH_SAY_VERBS_JA_SHARED)
    + ")"
    + ")"
)
# Multicharacter 不要 / 不许 / 不許 / 不准 need no left boundary, never Japanese noun suffixes;
# the boundary above serves single 別 only. Applying it blocks ordinary Chinese subjects,
# losing 我不要提君の名は。 / 你不准提君の名は。 that work on parent (codex P2).
_ZH_MULTI_NEG_EVIDENCE = (
    "(?:" + "|".join(_ZH_NEG_MULTIS) + f"){_ZH_HSPACE}(?:再)?{_ZH_HSPACE}(?:"
    + "|".join(_ZH_SAY_COMPOUNDS + _ZH_SAY_VERBS)
    + ")"
)
# Include only Kanji **entirely absent from Japanese**: 你 妳 您
# 咱 请, whose following 別 cannot be the Japanese 〜別 suffix.
# Deliberately exclude 我 / 他 / 請, Japanese Kanji admitting 他別提案をお願いします。
# (measured). Prefer one missed classification over persisting Japanese fragments as directives.
_ZH_SUBJECT_BEFORE_NEG = (
    "(?<=[" + _ZH_SUBJECT_CHARS + "])(?:"
    + "|".join(_ZH_NEG_SINGLES)
    + f"){_ZH_HSPACE}(?:再)?{_ZH_HSPACE}(?:"
    + "|".join(_ZH_SAY_COMPOUNDS + _ZH_SAY_VERBS)
    + ")"
)
# Subjects may be separated from negations by spaces (你 別提君の名は。, common mixed-language typing). The lookbehind
# above sees one adjacent character, and callers slice only one preceding character, so spaces
# lose entire traditional instructions. Simplified equivalents survive through 别's single-character
# evidence in _ZH_EVIDENCE_CHARS, another script asymmetry (codex P2).
# Add a separate **consuming** pattern, since fixed-length lookbehind cannot contain \s*, widening left context only for it.
# Character-class evidence still sees one adjacent character; wider context leaks previous-sentence Chinese and short-circuits guards.
# Bound whitespace to {1,4}: this is a per-message hot path.
# Accept only **horizontal** whitespace. \s consumes newlines, letting allowlisted characters at prior-line
# endings disable the next Japanese line: ``你\n別提案をお願いします。`` would
# yield 案をお願いします (codex P2). Subjects cannot cross lines to their predicates.
_ZH_SUBJECT_LEFT_MAX = 5
_ZH_SUBJECT_GAP = _ZH_HSPACE_ONE + "{1,4}"
_ZH_SUBJECT_ACROSS_SPACE = re.compile(
    "[" + _ZH_SUBJECT_CHARS + "]" + _ZH_SUBJECT_GAP + "(?:"
    + "|".join(_ZH_NEG_SINGLES)
    + f"){_ZH_HSPACE}(?:再)?{_ZH_HSPACE}(?:"
    + "|".join(_ZH_SAY_COMPOUNDS + _ZH_SAY_VERBS)
    + ")"
)
# Include whole 没心情: 没 is standard Japanese (没収), excluded from character classes, but these three
# characters together are Chinese-only. Otherwise 我没心情聊君の名は。 fails while traditional
# 沒心情 survives accidentally through 沒 evidence, causing within-template asymmetry (codex P2).
# Chinese-**only** final particles provide evidence too, outside capture groups (吧 in 別提君の名は吧。).
# When topics contain Japanese particles, these alone prove Chinese instructions; exclusion
# loses traditional matches while simplified equivalents survive through 别 evidence (codex P2).
# Japanese never uses these characters, avoiding 没 / 称-style guard short circuits.
# **Derive** from _ZH_FINAL_PARTICLE_CHARS, no more copies. Separate handwritten lists gave regex
# 啊 齁 欸 誒 absent here, losing 別提君の名は啊。 / 別提君の名は齁。 entirely,
# while simplified and 吧 variants worked (codex P2). Equality assertions pin synchronized tables.
# Remove 了 separately: common Japanese Kanji in 終了 / 了解 would open evidence paths for
# Japanese-only 別提案終了。. _ZH_EVIDENCE_WORDS handles Chinese 了 through
# verb + 了 combinations, two characters Japanese cannot compose that way.
# 囉 啰 are **additional**: omitted from regex particles due to 嘍囉 / 喽啰 (see comments there),
# hence captured inside terms, but still valid Chinese evidence.
_ZH_ZH_ONLY_FINAL_PARTICLES = "".join(
    c for c in _ZH_FINAL_PARTICLE_CHARS if c != "了"
) + "囉啰"

_ZH_EVIDENCE_CHARS = "别说讲谈讨论关这话题愿懒许为甭說這關沒稱" + _ZH_ZH_ONLY_FINAL_PARTICLES
_ZH_EVIDENCE_WORDS = ("叫我", "喊我", "管我叫", "不想", "懶得", "不願", "没心情",
    # Template 1 includes _ZH_ADDRESS_VERBS too, while only 叫我 / 喊我 / 管我叫 appear above.
    # 称 is standard Japanese (名称) and excluded, but whole 称呼我 is Chinese-only;
    # omission loses 不要称呼我「君の名は」。 (codex P2).
    "称呼我", "稱呼我")
# **Sentence-final** 了 is not used as a Japanese discourse particle. Without it,
# 別提君の名は了。 fails while simplified equivalents survive through 别 evidence (codex P2).
#
# Constrain **both sides**; right-only anchoring opens a much larger hole than it fixes:
#   · **Right anchors sentence ends**, where 了 as a discourse particle always occurs.
#   · **Left must not be Han**: 了 in 終了 / 完了 / 修了 belongs to common Japanese
#     Sino-Japanese words. Right-only anchoring makes 地域別提案は終了。 persist nonword 案は終,
#     and 別提案の受付は終了。 persist 案の受付は終; seven of eight Japanese cases failed,
#     rescuing one Chinese case only. This was the initial implementation, reverted after measurement.
# Therefore inspect **full matched spans**, not blanked instructions, since the left character belongs to payload.
# Read **one** character only, bounded like _ZH_NEG_VERB_EVIDENCE's boundary; never treat arbitrary payload
# Chinese as evidence, explicitly forbidden by two P2 rounds.
_ZH_TERMINAL_LE_EVIDENCE = r"(?<![一-鿿])了(?=[，。！？；,.!?;]|\s*$)"
_ZH_TERMINAL_LE_RE = re.compile(_ZH_TERMINAL_LE_EVIDENCE)
# Speech verb + 了 is Chinese, impossible Japanese, where 了 occurs only in Sino-Japanese words
# such as 終了 / 了解, never aspect markers. This rescues 君の名は別提了。.
# **Derive** from three verb tables. Handwritten 提了 說了 讲了 講了 missed preposed
# trigger forms 提起 / 提及 on the evidence side, losing 君の名は別提起了。
# while simplified worked (codex P2). Derivation automatically synchronizes future additions
# to any verb table.
_ZH_VERB_LE_EVIDENCE = tuple(
    verb + "了"
    for verb in dict.fromkeys(
        _ZH_SAY_COMPOUNDS + _ZH_SAY_VERBS + _ZH_PREPOSED_SAY_VERBS
    )
)
_ZH_EVIDENCE_RE = re.compile(
    "|".join(
        (f"[{_ZH_EVIDENCE_CHARS}]",)
        + _ZH_EVIDENCE_WORDS
        + _ZH_VERB_LE_EVIDENCE
        + (_ZH_NEG_VERB_EVIDENCE, _ZH_MULTI_NEG_EVIDENCE, _ZH_SUBJECT_BEFORE_NEG)
    )
)

# (2b) Japanese syntactic evidence: particles / auxiliaries / polite endings almost inevitably occur in Japanese **sentences**,
# rarely banned proper nouns (ドラえもん / お兄ちゃん contain none). Inspect terms rather than whole spans;
# triggers already share Chinese/Japanese Kanji and provide no information.
# Japanese function words form a **closed set**, unlike Chinese compounds, so fully list by case/binding
# particles, auxiliaries, and conjunctive particles, rather than assembling a few corpus-specific examples (codex P2:
# のにをはがでと alone still misses 個別提案ください。 / 地域別講座へ申込。).
# Exclude も alone: occurs in ドラえもん, rejecting cases just rescued above.
# から has similar risk (からくりサーカス), but is too common as an internal particle to omit.
# Japanese sentence-end right boundary includes more than punctuation: quoted/bracketed whole sentences remain sentences,
# and closers count. In 「別提案あり」 / （別提案なし） / 「別提案だ」, predicates
# directly precede closers; punctuation-only checks miss all three (codex P2). Derive closers from
# _ZH_CLOSE_FOR_OPEN, including symmetric quotes, which can only close here.
_JA_SENTENCE_END = "[" + re.escape(
    "。、！？" + "".join(sorted(set(_ZH_CLOSE_FOR_OPEN.values()) | _ZH_SYMMETRIC_DELIMS))
) + r"\s]|$"
# Japanese final particles. Putting them into the single-character class rejects ドラえもん cases, the reason
# も was excluded; Kanji-stem + sentence-end boundaries make them safe.
# Extract constants because **サ変 endings can take another final particle**: after して / する in
# 別提案してね。 / 別提案するな。 (codex P2). Both sites must share one source to prevent drift.
_JA_FINAL_PARTICLES = "か|も|ね|よ|わ|な|さ|ぞ|ぜ|じゃん|かな|かも|だろ"
_JA_GRAMMAR_RE = re.compile(
    "|".join((
        # Auxiliaries / polite endings.
        # **Kanji spellings** of ください / できる are common too; Kana-only tables
        # miss half, persisting 別提案下さい。 / 別提案出来る。 for three days
        # (codex P2, simplified too). Complete existing entries' orthography without broadening criteria.
        # Add only inflected forms **with okurigana** (下さい / 出来る / 出来ない / 出来た), never bare
        # 出来, which would reject Japanese noun 出来高 and Chinese 提出来.
        "です", "でした", "ます", "ました", "ましょう", "ません",
        "ください", "下さい",
        "である", "らしい", "そうです",
        # Conjunctive / compound particles.
        "について", "に関して", "という", "ながら", "ので", "けど", "たら",
        # Colloquial copulas / final particles: accept only **multicharacter** forms. Bare だ / ちゃ occur
        # in proper names (お兄ちゃん), rejecting the cases rescued above.
        "だね", "だよ", "だな", "だっけ", "でしょ", "かな", "かも", "じゃない",
        "らしい", "みたい",
        # Predicate forms: past / obligation / passive / progressive, etc. (codex P2).
        "だった", "だって", "だろう", "すべき", "される", "された", "している",
        "します", "しない", "できる", "出来る", "出来ない", "出来た",
        "しよう", "ている", "ておく", "てある",
        # Case / binding / adverbial particles, longer first and single characters in the final class.
        "から", "まで", "より", "など", "だけ", "でも", "しか", "ばかり",
        "[のにをはがでとへ]",
        # Existential predicates: telegraphic Japanese (別提案あり。 / 別提案なし。) contains only these
        # two Kana, beyond every entry above, so 案あり persists as Chinese (codex P2).
        # Constrain both sides, or false rejection follows:
        #   · **Right sentence-end anchor**: あり prefixes ありがとう, so without it
        #     别再提ありがとう。 is rejected entirely. Predicate usage always ends Japanese sentences.
        #   · **Left requires Han**: predicate usage follows Sino-Japanese stems (案なし / 案あり);
        #     after Kana it belongs to words (おもてなし). Right-only anchoring loses traditional
        #     別提おもてなし。 while simplified equivalents work (codex P2).
        f"(?<=[一-鿿])(?:あり|なし)(?={_JA_SENTENCE_END})",
        # Bare copula だ. Colloquial copulas above accept only multicharacter forms due to proper names
        # such as だんご三兄弟; **sentence-end anchors** remove that concern (だ in だんご
        # precedes ん). Omission persists 別提案だ。 as 案だ (codex P2).
        # Like あり|なし, require **Kanji stems**. Japanese だ predicates follow Sino-Japanese stems
        # (案だ / 座だ / 話だ); after Kana it belongs to words. Right-only anchoring loses traditional
        # 別提ただ。 / 別提まだ。 while simplified equivalents work (codex P2).
        f"(?<=[一-鿿])だ(?={_JA_SENTENCE_END})",
        # サ変 **base/past** forms: includes します / しない /
        # しよう above but missed common する / した, persisting 別提案した。 as
        # 案した (codex P2). Both boundaries match あり|なし|だ: sentence-end right,
        # Kanji-stem left, preserving Kana words such as あした / きのうした.
        # Include common サ変 endings して / したい / しろ / せよ too:
        # sentence-final predicates (別提案して。 persists 案して, codex P2). Same boundaries;
        # そ in 別提そして。 is Kana, failing left checks and preserving そして.
        f"(?<=[一-鿿])(?:する|した|して|したい|しろ|せよ)"
        f"(?:{_JA_FINAL_PARTICLES})?(?={_JA_SENTENCE_END})",
        # Final particles: も / か / ね **alone** in the single-character class reject
        # ドラえもん cases, why も was excluded; Kanji-stem + sentence-end
        # boundaries make it safe because も in ドラえもん follows Kana and is not final.
        # Omission persists 別提案か？ / 今回は、別提案も。 / 「別講座じゃん。」 as
        # 案か / 案も / 座じゃん (codex P2).
        f"(?<=[一-鿿])(?:{_JA_FINAL_PARTICLES})(?={_JA_SENTENCE_END})",
        # Appearance/hearsay そう. Former literal そう？ embedded **punctuation**
        # in markers while punctuation always falls **outside** captures as instruction termination,
        # making the branch dead for zh: 別提案そう？ still persists 案そう. Automatic discovery
        # found unreachability before codex supplied the fix. Match the four criteria above: sentence-end right,
        # Kanji-stem left. そ in 別提ドラえもんそう？ follows Kana, preserving it.
        f"(?<=[一-鿿])そう(?={_JA_SENTENCE_END})",
    ))
)


def _is_japanese_sentence_match(
    span: str, term: str, before: str = "", directive: str | None = None,
    stem: str = "", compound_verb: bool = False,
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
    # Prepend one left character before searching: _ZH_NEG_VERB_EVIDENCE requires no Han before
    # negation, while spans **start at negation**. Searching spans alone leaves lookbehind blind,
    # classifying 地域別提案をお願いします。 as Chinese (caught directly by this file's ja corpus).
    #
    # Search Chinese evidence only in **instructions**, excluding payload: Japanese sentences naturally mention Chinese titles;
    # 這 in 世代別講座で中国映画這就是愛について話します。 short-circuits guards and
    # persists Japanese fragments (two codex P2 rounds, first quoted, then unquoted).
    # Caller-supplied span already blanks capture groups with equal-length replacement.
    # **Equal-length** blanking is essential: deletion joins payload-surrounding characters, fabricating multicharacter evidence
    # (叫+我 / 不+想 / 懶+得 / 不+願 / 喊+我 all measured).
    payload = span if directive is None else directive
    if _ZH_EVIDENCE_RE.search(before[-1:] + payload):
        return False
    # Final 了: inspect **full matched spans**, see _ZH_TERMINAL_LE_EVIDENCE.
    if _ZH_TERMINAL_LE_RE.search(span):
        return False
    # Spaced subjects/negations: see _ZH_SUBJECT_ACROSS_SPACE.
    if _ZH_SUBJECT_ACROSS_SPACE.search(before + payload):
        return False
    # Kana **immediately preceding** matched spans means Japanese productive 〜別 suffixes (カテゴリ別 /
    # ジャンル別 / テーマ別), not Chinese imperative 別. Chinese 別 does not directly
    # follow Kana. Since this inspects outside spans, particle-free terms are blocked too
    # (ジャンル別討論スレ yields スレ, beyond (2b)).
    if before and _KANA_RE.search(before[-1]):
        return True
    # Fast exit: no Kana means no Japanese sentence. (2b) itself contains only Kana and terms are span
    # substrings, so this is not independent logic, just saving a regex call on every template
    # for every user message's hot path.
    if not _KANA_RE.search(span):
        return False
    # Japanese 〜別 + compound noun + bare Kana noun: 地域別提案スレ /
    # A別講座スレ. Triggers consume 別提, leaving 案スレ: 案 completes Japanese
    # 提案 while スレ is a bare noun without particles, beyond every criterion
    # below. Nonwords then persist as Chinese for three days (codex P2). Parent lacked this case, introduced
    # by admitting traditional 別.
    #
    # Require both **left context + term shape**:
    #   · A preceding character outside clause boundaries: 〜別 is a suffix requiring labels (地域 / A /
    #     カテゴリ). Reaching here already means ambiguous negation, shared verb, and no Chinese
    #     evidence, so no need to check verbs again.
    #   · Exactly **one Kanji** followed by Kana. Japanese compounds form an open set (提案 / 提出 /
    #     提供 / 提示 / 提携 / 講座 / 講義…), impossible to exhaust. The closed shape one Kanji + Kana
    #     denotes the compound's second half plus Kana tail.
    # Cost affects only **one Han + Kana** titles (動漫別提蘭ちゃん。).
    # 動畫別提ドラえもん。 (Kana-initial term), 遊戲別提初音ミク。 /
    # 別提美少女戰士セーラームーン。 (multiple Han initials) remain preserved, exactly the cases
    # this guard has protected since its first round.
    # With **two-character** shared verbs (討論), the entire Japanese word enters triggers,
    # leaving Kana-initial terms: 地域別討論スレ。 yields スレ, never satisfying
    # shape criteria and still persisting nonwords (codex P2). Restore the stem before checking shape.
    # Restore **only** two-character verbs: doing so for 提 / 講 / 談 turns
    # 提ドラえもん in 動畫別提ドラえもん。 into one-Kanji-plus-Kana too,
    # rejecting what this guard has always protected (tried; measured regression).
    shaped = stem + term if compound_verb else term
    if (
        before
        and _ZH_NON_CLAUSE_START_RE.match(before[-1])
        and _HAN_RE.match(shaped[:1])
        and _KANA_RE.match(shaped[1:2])
    ):
        return True
    # Include **the Kanji consumed by triggers**: あり|なし / だ / する|した /
    # final particles all require left Sino-Japanese stems, exactly verb-final characters.
    # 別討論あり。 consumes whole 討論, leaving あり with impossible left checks,
    # persisting あり / なし / した as Chinese for three days, while structurally identical
    # 別提案あり。 with term 案あり is blocked (codex P2).
    # Prepend **one** Kanji only, and only when truly Han: more would feed whole triggers into criteria,
    # uninformative because triggers share Chinese/Japanese Kanji. One character cannot fabricate
    # Kana matches: all four criteria need Kanji immediately followed by Kana, while 別提ドラえもん。
    # / 別提おもてなし。 still have Kana before Kana after prepending, remaining unmatched (measured).
    return bool(_JA_GRAMMAR_RE.search(stem + term))

# Every template's **topic captures must forbid leading whitespace**: non-zh uses
# ((?!\s).{1,N}?); zh template 2 places the same (?!\s) at capture start, and template 3's leading
# whitespace follows 我 only.
# This bounds whitespace-only/long-whitespace timing. Topic classes (dot / zh character branches) also consume
# spaces, making **every** whitespace position a candidate start. Each expands lazy captures from one
# to N, repeatedly letting following \s* scan to end: O(n²), constant N. Before fixing,
# extract_directives(" " * 3000) measured 3.6s (7.8s on another machine), on a path
# run synchronously on every user message without input length limits. Zero-width checks make whitespace starts fail
# immediately, reducing identical input below 1ms and doubling time only when n doubles.
# Matching changes only for whitespace-leading segmentations:
#   · With actual topic text, whitespace-starting and first-nonspace-starting matches share **the same ending**,
#     yielding identical trimmed terms while shifting match starts rightward;
#   · Exceptions have **minimum unit counts** (zh template 2 requires two): leading spaces could occupy a unit,
#     making " 钱这事别提了" capture " 钱", trim to one character, and fail minimum. Now start at
#     钱 and capture 钱这事, matching the unspaced sentence;
#   · Whitespace-only topics (first space in " 짜증나 듣기 싫어", or
#     trailing space in "no hables de ") previously **occupied** matches before terms trimmed empty and were discarded,
#     changing results with one extra space. They now match versions without those spaces.
#   · zh template 2 tempers 关于 only in its first unit; leading spaces previously bypassed it,
#     making " 关于工作别提了" additionally store 关于工作, now blocked too.
# tests/unit/test_directive_regex_whitespace.py pins these changes.
_PATTERNS_RAW: List[Tuple[str, str, str]] = [
    # ---------- zh ----------
    # 别/不要/不许/不准 + optional 再 + verb + object.
    # Exclude \s from terminators: in mixed-language zh (别叫我 John Smith), lazy
    # .{1,40}? otherwise cuts at the first space to John. Require punctuation / EOL /
    # final particles to capture multiword noun phrases fully (codex P2).
    # Exclude traditional 不準, meaning inaccurate; 測量不準說明有問題 would become
    # ban_topic. Traditional prohibition already uses 不准, present in the table.
    ("zh", "ban_topic",
     _ZH_NEG + f"{_ZH_HSPACE}(?:再)?{_ZH_HSPACE}"
     # See _zh_verb_alternation: compounds precede single-character prefixes. Templates 2/4 require
     # immediate post-verb terminators and backtrack on failure, avoiding this issue.
     # Verb-object whitespace must also be horizontal, or the next line attaches as an object after complete triggers:
     # 別再提 followed by 案をお願いします。 persists 案をお願いします (codex P2).
     # Same criterion as trigger interiors, subjects, and pauses: instructions never cross lines.
     + _ZH_VERBS_WITH_ADDRESS + _ZH_HSPACE
     # Object minimum is two, not one: optional particles + lazy objects prioritize treating topic-final characters
     # as particles (别再提拿捏。 -> object 拿, particle 捏), then reject the one-character term,
     # losing whole instructions. Single-character objects necessarily fail term length anyway; raising minimum only helps.
     + _ZH_TOPIC_SEPARATOR
     # Place (?!\s) before objectless lookahead, as in template 3: every space returned by post-verb whitespace
     # lets lookahead scan remaining spaces again, making "别提" + " " * 8000 + "x" quadratic at 72ms.
     + r"(?!\s)"
     + _ZH_OBJECTLESS_AHEAD
     + "(" + _zh_topic(2, 40) + r")" + _ZH_FINAL_PARTICLES + r"?(?:[，。！？；,.!?;]|\s*$)"),
    # X + optional 这个 + 别 + optional 再 + 提.
    # Template 4 handles 关于 X 就别提了. Without excluding it here, one sentence produces
    # 关于股票就 plus template-4 股票, junk still occupying active
    # slots and entering system prompts for three days (codex P2; existing defect in both scripts).
    # Both exclusions matter: prefixes cannot **start with** 关于, and the next lookahead blocks starts at
    # its second character, preventing 于股票. Nested lookbehind blocks only the
    # 关|于 split; (?<![关關]) would wrongly reject 有关工作别提了.
    # Guard only beginnings, never temper 关于 anywhere inside prefixes: titles naturally contain
    # it (电影《关于爱》别提了。), so whole-span blocking loses instructions (codex P2).
    # Cost: extra characters before 关于 (我觉得关于股票就别再讲了) still produce another
    # long term, existing behavior predating this PR.
    # Align trailing 的 / 的事 / 这个 / 就 with template 4 to stop prefixes at actual topics; 的
    # is independently optional because natural 減肥的這件事別再說了。 separates it from demonstratives.
    ("zh", "ban_topic",
     # Prefix minimum two, not one: three optional fillers + lazy prefixes place topic-final
     # characters into fillers, shrinking single-character subjects (钱的事别提了。) to one character below
     # 2 <= len(term), losing instructions. Minimum two forces longer
     # prefixes; single-character prefixes necessarily fail term length anyway, so raising it only helps.
     # Bind 的 to demonstratives, never independently optional, which truncates 目的 in 目的这个别提了。 to
     # 目 (adversarial investigation). Never consume final 就 in regex; see _ZH_TRAILING_FILLERS.
     # These \s* runs must be **atomic**: topic character branches also match spaces, letting topics
     # and every following \s* arbitrarily partition one run, exploding segmentation combinations. Measured
     # extract_directives(" " * 60) costs 0.42s on a synchronous per-user-message
     # path; one whitespace-only message can stall it (codex P1). Atomicity restores parent's complexity.
     # Merge a formerly duplicated \s*\s* too.
     # Post-verb \s*(?:了)? must not be atomic: the following terminator class includes
     # \s, so atomic consumption would take spaces meant to terminate.
     #
     # This template **preserves** 的事: without anchors like 关于, it belongs to topics themselves.
     # 我们的事别提了。 would truncate to 我们, 前女友的事别提了。 to
     # 前女友 (both intact on base; codex P2). Storing 我们 tells models to avoid users
     # themselves rather than their matter, reversing cost direction. Template 4 retains it, anchored there by initial 关于.
     # Capture-leading (?!\s) forbids whitespace starts; see _PATTERNS_RAW. Atomicity above controls
     # where to split; this controls where to start. Both are required.
     r"(?!(?<=关)于)(?!(?<=關)於)((?!\s)"
     # Topic-trigger whitespace must also be horizontal; otherwise prior lines become preposed topics:
     # 工作正常 followed by 別提了。 persists 工作正常 (codex P2). Same criterion
     # as the other four sites: instructions never cross lines.
     + _zh_topic(2, 30, block_guanyu=True) + r")" + f"(?>{_ZH_HSPACE})"
     # Pauses also occur between preposed topics and triggers (工作，別提了。). Topic classes exclude
     # ， but whitespace-only handling here lost whole matches, while parent stored 工作
     # (codex P2). Reuse post-verbal object separators, plus post-pause 就.
     + _ZH_PAUSE_THEN_JIU
     + r"(?:的?(?:这个|這個|这事|這事|这话题|這話題|这件事|這件事))?"
     + f"(?>{_ZH_HSPACE})"
     # Pauses also occur **after fillers**: 工作，这件事，别提了。 is natural;
     # accepting punctuation only before fillers restarts regex after the first comma, storing
     # 这件事 (codex P2). Likewise accept 就 in both positions.
     + _ZH_PAUSE_THEN_JIU
     + f"[别別](?>{_ZH_HSPACE})(?:再)?(?>{_ZH_HSPACE})"
     # Terminator whitespace must be horizontal too: line-end triggers treat newlines as completed instructions,
     # binding prior lines as topics. 工作正常別提 followed by 下一句。 stores
     # 工作正常 (codex P2). Same-line spaces still terminate (工作别提 然后…).
     # See _ZH_PREPOSED_SAY_VERBS for derived trigger/evidence tables; never hardcode here.
     # Compounds precede single prefixes (提起 / 提及 before 提), already ordered in constants.
     + r"(?:提了|" + "|".join(_ZH_PREPOSED_SAY_VERBS) + r")"
     # Whitespace before 了 is horizontal too; horizontal terminators cannot precede line separators,
     # or 工作正常別提 followed by space/newline and 下一句。 still crosses lines (codex P2).
     + _ZH_HSPACE + r"(?:了)?(?:[，。！？；,.!?;]|"
     + _ZH_HSPACE_ONE + f"(?!{_ZH_HSPACE}[{_ZH_LINE_SEP}])" + r"|$)"),
    # 不想/不愿 + 聊/讨论 + X: likewise exclude \s terminators or multiword noun phrases truncate.
    # Leading whitespace follows 我 only (我 不想聊…). Previously optional (?:我)? preceded
    # horizontal whitespace, incorporating initial spaces without 我, making every whitespace position a
    # start scanning to the end (" " * 6000 costs 0.3s, quadratic). See _PATTERNS_RAW.
    ("zh", "ban_topic",
     r"(?:我" + _ZH_HSPACE + r")?"
     + r"(?:" + "|".join(_ZH_RELUCTANCE) + r")"
     + _ZH_HSPACE + r"(?:再)?" + _ZH_HSPACE
     + _ZH_VERBS_PLAIN
     + _ZH_HSPACE
     + _ZH_TOPIC_SEPARATOR
     # This template **also preserves** 的事, removed separately in templates 1/2/4 for the same reason:
     # possession plus nominalization can belong to names. 我沒心情聊我們的事。 would persist
     # 我們, directing avoidance at users rather than the matter (codex P2). Retain 了 as pure tone.
     # Place whitespace-start exclusion (?!\s) **before** objectless lookahead, which scans remaining
     # whitespace at every position. Placing it afterward rescans for every returned post-verb space,
     # keeping "我不想聊" + " " * n + "x" quadratic. Both are zero-width at one position, so order preserves matches.
     + r"(?!\s)"
     + _ZH_OBJECTLESS_AHEAD
     # Whitespace before 了 is horizontal too, sharing no-cross-line criteria with final particles.
     # Not a codex report: expanding structural guards from before captures to **whole templates**
     # automatically discovered this tenth case.
     + r"(" + _zh_topic(2, 40) + r")(?:" + _ZH_HSPACE + r"了)?"
     + r"(?:[，。！？；,.!?;]|\s*$)"),
    # 关于 X + 别 + optional 再 + 说.
    ("zh", "ban_topic",
     # Only this template retains (?:就)?, anchored by initial 关于. Explicit 关于 X 就别…
     # limits truncation risk to 关于 + 就-ending words (关于功成名就别提了,
     # extremely rare). Template 2 lacks anchors and covers every X别提了, including 成就 / 迁就 /
     # 功成名就, so consumes nothing there and leaves _drop_filler_suffixed_terms to handle it.
     # Every pre-trigger \s* must be atomic, like template 2: topic character branches match
     # spaces and partition them with following \s*. Last round fixed template 2 but missed this,
     # making "关于" + " " * 80 cost 3s (codex P1 second round).
     # This template **also preserves** 的事, already removed in template 2 for the same reason: possession
     # plus nominalization can belong to names. 关于我们的事别提了。 persists 我们,
     # 关于我前女友的事就别提了。 persists 我前女友 (codex P2). Worse, that shorter
     # term makes _drop_filler_suffixed_terms delete template 2's **correct** 我们的事
     # as shorter term + filler, leaving only truncation.
     #
     # **Retain** 这个 / 这话题 / 这件事 demonstratives, unambiguously filler
     # (关于减肥这话题就别说了。 has topic 减肥). Retain (?:就)? too;
     # removal makes 关于股票就别提了。 / 关于工作就别提了。 degrade into junk containing 就
     # (both options measured).
     r"(?:关于|關於)" + f"(?>{_ZH_HSPACE})"
     # Pauses also follow **topic introducers** (关于，工作，就别提了。, common in transcription/
     # typing). Only this template handles 就, since template 2 removed (?:就)? for
     # 成就 / 迁就. Omitting pauses loses the whole sentence beyond template 2 rescue;
     # parent used template 2's (?:就)? to store 工作 (codex P2).
     + _ZH_TOPIC_SEPARATOR
     # Likewise never join 關於工作 and 別提了。 across a newline.
     + r"(" + _zh_topic(2, 30) + r")" + f"(?>{_ZH_HSPACE})"
     # Like template 2, keep pause punctuation outside captures in 關於工作，就別提了。.
     + _ZH_TOPIC_SEPARATOR
     + r"(?:的?(?:这个|這個|这事|這事|这话题|這話題|这件事|這件事))?"
     + f"(?>{_ZH_HSPACE})"
     # Like template 2, pauses may follow both fillers and 就.
     + _ZH_TOPIC_SEPARATOR
     + r"(?:就)?" + f"(?>{_ZH_HSPACE})"
     + _ZH_TOPIC_SEPARATOR
     + f"[别別](?>{_ZH_HSPACE})(?:再)?(?>{_ZH_HSPACE})"
     # Like template 2, terminator whitespace is horizontal only.
     # Trigger tables **share template 2's source**. Hardcoded forms here made 關於工作就別提起了。
     # miss the dedicated template, fall back to generic preposed topics, and persist filler (工作就),
     # while 關於工作就別提了。 worked (codex P2, simplified too).
     # My no-hardcoded-trigger guard **missed** this case by checking only template 2,
     # a typical checklist omission. It now traverses all templates.
     + r"(?:" + "|".join(_ZH_PREPOSED_SAY_VERBS) + r")"
     + _ZH_HSPACE + r"(?:了)?(?:[，。！？；,.!?;]|"
     + _ZH_HSPACE_ONE + f"(?!{_ZH_HSPACE}[{_ZH_LINE_SEP}])" + r"|$)"),

    # ---------- en ----------
    # stop/don't/quit + verb + (about|saying) + X
    # X is an English noun phrase often containing spaces (my ex / the weather). Use filler-word /
    # punctuation / sentence-end terminators, preventing lazy .{1,40}? cutting at its first space
    # to my.
    ("en", "ban_topic",
     r"(?:please\s+)?(?:stop|quit|don'?t|do\s+not|no\s+more)\s+"
     r"(?:talking\s+about|talk\s+about|saying|say|mentioning|mention|"
     r"bringing\s+up|bring\s+up|going\s+on\s+about|"
     r"calling\s+me\s+a|calling\s+me|call\s+me\s+a|call\s+me)\s+"
     r"((?!\s).{1,40}?)"
     r"(?:\s+(?:again|anymore|any\s+more|please|ever|already|now|"
     r"forever|today|tonight|right\s+now|in\s+(?:front|public))"
     r"|[,.!?;]|$)"),
    # X + is off limits / off the table / not a topic
    # Capture-final (?<!\s): lazy captures start at every nonspace prefix character, and each extension
    # into later whitespace makes \s+ scan the remainder ("no hables" + " " * 8000 costs 12–22ms).
    # Whitespace-ending captures are futile: \s+ consumes remaining spaces anyway, leaving is at the same position,
    # equivalent to shorter captures tried first by laziness. This criterion therefore preserves matches.
    ("en", "ban_topic",
     r"((?!\s).{1,30}?(?<!\s))\s+is\s+(?:off[\s\-]?limits|off\s+the\s+table|a\s+(?:no[\s\-]?go|forbidden)\s+topic)"
     r"(?:[\s,.!?;]|$)"),
    # I don't want to talk/hear about X
    # X is a noun phrase possibly containing spaces (my ex girlfriend). Use filler-word /
    # punctuation / sentence-end terminators, or lazy .{1,40}? cuts at the first space to my (codex P1).
    ("en", "ban_topic",
     r"i\s+(?:don'?t|do\s+not|really\s+don'?t)\s+(?:want\s+to|wanna)\s+"
     r"(?:talk|hear|discuss|think)\s+(?:about|of)\s+((?!\s).{1,40}?)"
     r"(?:\s+(?:anymore|any\s+more|again|ever|already|right\s+now|today|tonight|please)"
     r"|[,.!?;]|$)"),
    # drop the X / leave X alone (subject)
    ("en", "ban_topic",
     r"(?:drop|leave\s+alone)\s+(?:the\s+|that\s+)?((?!\s).{1,30}?)\s+"
     r"(?:topic|subject|thing|stuff|already)(?:[\s,.!?;]|$)"),

    # ---------- ja ----------
    # All post-capture ja/ko \s* use atomic (?>\s*), like zh:
    # two \s* separated only by optional groups ((?:は)? / (?:이|가)?) can
    # arbitrarily partition whitespace, multiplied by lazy captures consuming spaces, creating combinatorial explosion.
    # Before fixing, ko template 3 measured 1.2s for " " * 320; ja template 2 measured
    # 142s for "もう" + " " * 320 + "x".
    # Separate from _PATTERNS_RAW's whitespace-start exclusion: that blocks starts within whitespace, but
    # content-leading topics ("x" + " " * n) still partition later whitespace, requiring this fix.
    # Atomize even lone \s* (ja template 3 / ko template 2): no partitioning, but non-atomic
    # failure returns spaces individually and retries triggers, wasting another scan.
    # Atomicity preserves matches: everything immediately following these \s* (optional groups/triggers) cannot start
    # with whitespace, so every successful partition equals consuming all current whitespace with each \s*,
    # already the first greedy attempt. Atomicity merely removes doomed retries.
    # Keep pre-capture もう\s* / 이제\s* unchanged: once captures forbid whitespace starts,
    # each returned-space retry immediately fails at the first capture character, without further cost.
    # X + のこと/について + は + もう + 言わないで/やめて/しないで.
    ("ja", "ban_topic",
     r"((?!\s).{1,40}?)(?>\s*)(?:のこと|の話|について|に関して|っていう話)(?>\s*)"
     r"(?:は)?(?>\s*)(?:もう|二度と|これ以上)?(?>\s*)"
     r"(?:言わないで|話さないで|しないで|やめて|止めて|よして|聞きたくない|触れないで)"),
    # もう + X + optional の話 + optional は + 嫌だ/聞きたくない.
    ("ja", "ban_topic",
     r"もう\s*((?!\s).{1,40}?)(?>\s*)(?:のこと|の話)?(?>\s*)(?:は)?(?>\s*)"
     r"(?:嫌|いや|聞きたくない|話したくない|やめて)"),
    # X + って + 呼ばないで / 言わないで.
    ("ja", "ban_topic",
     r"((?!\s).{1,30}?)(?>\s*)(?:って|とは|なんて)(?>\s*)"
     r"(?:呼ばないで|言わないで|呼ぶな|言うな)"),

    # ---------- ko ----------
    # All topic-surrounding \s* in templates 1/3 are **atomic**, like zh 2/4: lazy
    # .{1,N}? topics consume spaces, while \s*(?:이|가)?\s*, two \s* around an optional
    # group, arbitrarily repartitions them and makes segmentation cubic. Template 3 measured
    # 7s for extract_directives(" " * 480); template 1's " " * 39 + "말" + " " * 480
    # measured 1.6s on this per-message synchronous path, without pre-record truncation or stripping.
    # Atomicity preserves matches: literals immediately following these \s* (particles/triggers) **cannot begin
    # with whitespace**, requiring all spaces consumed in successful matches anyway.
    # ko lacks \s* directly adjacent to terminator classes containing \s, the reason zh template 2's
    # post-verb \s*(?:了)? cannot be atomic. Watch for this when adding ko terminators.
    # Trigger-internal 하지\s*마 / 듣기\s*싫 have literal neighbors and cannot repartition, so remain unchanged.
    # Template 2's post-capture \s* is atomic too, despite no partitioning, avoiding returned-space trigger retries
    # (see ja introduction). All three topics forbid whitespace starts with (?!\s), as in _PATTERNS_RAW.
    # X + (에 대해|얘기|이야기) + (는)? + 그만 / 하지 마 / 꺼내지 마
    ("ko", "ban_topic",
     r"((?!\s).{1,40}?)(?>\s*)(?:에\s*대해서?|얘기|이야기|소리|말)(?>\s*)(?:는|은)?(?>\s*)"
     r"(?:그만|하지\s*마(?:세요|십시오)?|꺼내지\s*마(?:세요)?|관두|치워)"),
    # 다시는 + X + 말하지 마 / 꺼내지 마
    ("ko", "ban_topic",
     r"(?:다시는|두\s*번\s*다시|이제)\s*((?!\s).{1,40}?)(?>\s*)"
     r"(?:말하지|꺼내지|언급하지)(?>\s*)마(?:세요|십시오)?"),
    # X + (이|가)? + 듣기 싫다 / 짜증나
    # Never revert either (?>\s*) to \s*: after content-leading topics, they partition later whitespace
    # cubically (see ja introduction).
    ("ko", "ban_topic",
     r"((?!\s).{1,30}?)(?>\s*)(?:이|가)?(?>\s*)(?:듣기\s*싫|말하기\s*싫|짜증나|지긋지긋)"),

    # ---------- ru ----------
    # не говори / хватит про / прекрати + (preposition)? + X
    # Russian prepositions про / о / об / обо follow verbs and precede terms; consume them first so
    # (.{1,40}?) captures actual topics instead of greedily treating prepositions as terms.
    # Terms use English-style filler-word terminators, supporting multiword моей бывшей.
    ("ru", "ban_topic",
     r"(?:не\s+(?:говори|упоминай|повторяй|произноси|обсуждай|называй\s+меня)|"
     r"хватит\s+(?:говорить|обсуждать|упоминать)|"
     r"перестань\s+(?:говорить|обсуждать|упоминать|называть\s+меня)|"
     r"прекрати\s+(?:говорить|обсуждать|упоминать|называть\s+меня))\s+"
     r"(?:про\s+|обо?\s+|о\s+)?"  # Optional preposition.
     r"((?!\s).{1,40}?)"
     r"(?:\s+(?:больше|никогда|пожалуйста|снова|опять|вообще|сегодня)"
     r"|[,.!?;]|$)"),
    # о X + больше + не говори
    ("ru", "ban_topic",
     r"(?:обо|об|о)\s+((?!\s).{1,30}?)\s+больше\s+не\s+(?:говори|упоминай)"),
    # я не хочу + говорить|слышать + о X uses English-style filler-word terminators,
    # supporting multiword моей бывшей.
    ("ru", "ban_topic",
     r"я\s+не\s+хочу\s+(?:говорить|слышать|обсуждать)\s+(?:обо|об|о)\s+((?!\s).{1,40}?)"
     r"(?:\s+(?:больше|никогда|пожалуйста|снова|опять|вообще|сегодня)"
     r"|[,.!?;]|$)"),

    # ---------- es ----------
    # no hables / no menciones / deja de hablar + (de|sobre) + X
    # Post-trigger \s+ and pre-capture \s* are both atomic: absent prepositions leave an optional
    # group between them and n partitions of one run. Whitespace-start exclusion removes whitespace-topic
    # fast exits for "no hables" + " " * n, trying every partition (n=8000 costs 0.33s). Preposition groups stay non-atomic,
    # letting no hables de fall back to de as its topic. Same for Portuguese template 1.
    ("es", "ban_topic",
     r"(?:no\s+(?:hables|menciones|digas|sigas\s+hablando|me\s+llames)|"
     r"deja\s+de\s+(?:hablar|mencionar|llamarme)|"
     r"para\s+de\s+(?:hablar|mencionar))(?>\s+)"
     r"(?:de|sobre|acerca\s+de)?(?>\s*)((?!\s).{1,40}?)"
     r"(?:\s+(?:más|nunca|jamás|otra\s+vez|de\s+nuevo|por\s+favor|porfa|hoy|ahora)"
     r"|[,.!?;]|$)"),
    # no quiero + oír|hablar|saber + de|nada de + X, like en/ru.
    ("es", "ban_topic",
     r"no\s+quiero\s+(?:oír|hablar|saber|escuchar)\s+(?:nada\s+)?(?:de|sobre)\s+"
     r"((?!\s).{1,40}?)"
     r"(?:\s+(?:más|nunca|jamás|otra\s+vez|de\s+nuevo|por\s+favor|porfa|hoy|ahora)"
     r"|[,.!?;]|$)"),

    # ---------- pt ----------
    # não fale / não mencione / pare de falar + (de|sobre) + X
    ("pt", "ban_topic",
     r"(?:não\s+(?:fale|mencione|diga|continue\s+falando|me\s+chame)|"
     r"pare\s+de\s+(?:falar|mencionar|me\s+chamar)|"
     r"deix[ea]\s+de\s+(?:falar|mencionar))(?>\s+)"  # deixe de / deixa de（codex P2）
     r"(?:de|sobre|a\s+respeito\s+de)?(?>\s*)((?!\s).{1,40}?)"
     r"(?:\s+(?:mais|nunca|jamais|de\s+novo|outra\s+vez|por\s+favor|hoje|agora)"
     r"|[,.!?;]|$)"),
    # não quero + ouvir|falar|saber + de|sobre|nada de + X, like en/ru.
    ("pt", "ban_topic",
     r"não\s+quero\s+(?:ouvir|falar|saber|escutar)\s+(?:nada\s+)?(?:de|sobre)\s+"
     r"((?!\s).{1,40}?)"
     r"(?:\s+(?:mais|nunca|jamais|de\s+novo|outra\s+vez|por\s+favor|hoje|agora)"
     r"|[,.!?;]|$)"),
]


# Compile lazily, not on import.
#
# Four of 21 templates contain about 51 KB regex source each, 209 KB total; module-level compilation measured
# 294–298ms. This module belongs to memory_server's eager import chain,
# （app/__init__.py -> app/runtime_bindings.py -> memory.user_directives），
# itself the first imported application in merged mode. uvicorn awaits
# lifespan.startup() before create_server(), so all this time occurs **before a port exists**,
# giving users connection-refused rather than slow responses.
#
# Directive extraction needs it only after users speak. Compile on first access and register
# in utils/module_warmup.py so background threads compile after service readiness,
# avoiding first-extraction waits, matching LLM SDK treatment.
_DIRECTIVE_PATTERNS_CACHE: List[Tuple[str, str, "re.Pattern[str]"]] | None = None
_DIRECTIVE_PATTERNS_LOCK = threading.Lock()


def _directive_patterns() -> List[Tuple[str, str, "re.Pattern[str]"]]:
    global _DIRECTIVE_PATTERNS_CACHE

    cached = _DIRECTIVE_PATTERNS_CACHE
    if cached is None:
        with _DIRECTIVE_PATTERNS_LOCK:
            if _DIRECTIVE_PATTERNS_CACHE is None:
                # Build the full list before assignment: other threads see None or a complete
                # list, never partial compilation.
                _DIRECTIVE_PATTERNS_CACHE = [
                    (locale, kind, re.compile(raw, re.IGNORECASE | re.UNICODE))
                    for locale, kind, raw in _PATTERNS_RAW
                ]
            cached = _DIRECTIVE_PATTERNS_CACHE
    return cached


def __getattr__(name: str) -> object:
    # Preserve public DIRECTIVE_PATTERNS used by tests/external callers; charge compilation cost
    # only on access. PEP 562 also applies to from ... import X.
    if name == "DIRECTIVE_PATTERNS":
        return _directive_patterns()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    # Deduplicate **after** _drop_filler_suffixed_terms: filtering uses match spans to identify
    # alternative segmentations of one instruction, while deduplication discards duplicate terms and spans together.
    # In 股票别提了。关于股票就别提了。, the second 股票 shares the first's name and disappears,
    # leaving filtering only the first **nonoverlapping** span, so 股票就 escapes (codex P2).
    out: List[Tuple[str, str, str]] = []
    spans: List[Tuple[int, int]] = []
    for locale, kind, pat in _directive_patterns():
        # Likewise use shared fallback-family checks rather than handwritten startswith("zh").
        zh_family = prompt_locale_fallback_key(locale) == "zh"
        # Never directly finditer: Japanese guard rejection consumes whole spans, hiding embedded
        # **real instructions**. 地域別提案をお願いします 别再提工作。
        # loses all matches while parent still finds 工作 (codex P2). Manually advance
        # cursors: valid matches skip to end, rejected matches advance one character from their starts and rescan.
        # Backtrack only **rejected** matches; normal matches skip whole spans, or one instruction repeats
        # extraction with O(n²) cost.
        pos = 0
        while pos <= len(text):
            m = pat.search(text, pos)
            if m is None:
                break
            pos = m.end() if m.end() > m.start() else m.start() + 1
            try:
                term_raw = m.group(1)
            except IndexError:
                continue
            term = _trim_term(term_raw, locale)
            if not (_TERM_MIN_LEN <= len(term) <= _TERM_MAX_LEN):
                continue
            # zh shares 別/提/講/談/討論 with Japanese, so sentences can become
            # ban_topic (see _is_japanese_sentence_match). Apply to zh only: ja templates themselves
            # require Kana and would reject all their own matches.
            # Slice **one** character only: guards read before[-1:]. Whole-prefix slicing copies full text per match,
            # becoming quadratic with tens of thousands of instructions (60,000 take 2.5s versus base's
            # 1.2s; codex P2).
            # Inspect **untrimmed** captures: Kana particle tables now apply across locales,
            # stripping だね from 地域別講座だね。 before guards inspect terms,
            # removing Japanese grammar markers and misclassifying Chinese (encountered adding Kana fallback).
            # Instruction portion = matched span with captured topics blanked by equal-length spaces, preserving positions
            # and preventing surrounding-character concatenation. Payload Chinese is not evidence.
            span = m.group(0)
            payload_lo = m.start(1) - m.start()
            payload_hi = m.end(1) - m.start()
            directive_only = (
                span[:payload_lo] + " " * (payload_hi - payload_lo) + span[payload_hi:]
            )
            # Only **evidence** inspects instructions; Kana/Japanese-grammar criteria still inspect full matches.
            # Passing blanked spans removes Kana too, classifying 地域別提案をお願いします。 as Chinese
            # because Kana is absent (encountered implementing this).
            # Character **immediately left** of capture, inside matched spans, i.e. trigger-final character. Japanese
            # Kanji-stem criteria need it; see _is_japanese_sentence_match's end.
            stem = span[payload_lo - 1: payload_lo] if payload_lo else ""
            if zh_family and _is_japanese_sentence_match(
                span, m.group(1),
                text[max(0, m.start() - _ZH_SUBJECT_LEFT_MAX): m.start()],
                directive=directive_only,
                stem=stem if _HAN_RE.match(stem) else "",
                compound_verb=span[:payload_lo].endswith(
                    _ZH_COMPOUND_JA_SHARED_VERBS
                ),
            ):
                # Rescan **after match start**, recovering embedded real instructions.
                pos = m.start() + 1
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
    # Only filler-ending terms can be suppressed. Most messages contain none;
    # prefiltering reduces O(n²) overlap scanning to O(n), where n is matches in one message, reaching
    # hundreds in pasted chat histories (codex P2).
    suspects = [
        index
        for index, (_locale, _kind, term) in enumerate(hits)
        if any(term.endswith(filler) for filler in _ZH_TRAILING_FILLERS)
    ]
    if not suspects:
        return hits
    suspect_set = set(suspects)

    # Filtering suspects alone is insufficient: topics genuinely ending in fillers (thousands of 成就别提了。)
    # make every match suspect, returning full rescans to O(n²): 4,000 cost 1.25s on
    # synchronous user-message processing (codex P2).
    #
    # Match lengths are bounded by templates' {,40}-style limits, so buckets by start
    # restrict overlap candidates to the same and neighboring buckets, making lookup O(1).
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
        # Compare only same-kind terms **overlapping this match**, indicating two segmentations of one instruction.
        rivals = {
            hits[other_index][2]
            for other_index in neighbours
            if other_index != index
            and hits[other_index][1] == kind
            and _overlaps(spans[index], spans[other_index])
        }
        if not rivals:
            return False
        # Fillers stack (前女友的事就); strip layerwise and discard if any layer matches a competitor.
        seen_forms = {term}
        frontier = [term]
        while frontier:
            current = frontier.pop()
            for filler in _ZH_TRAILING_FILLERS:
                if not current.endswith(filler) or len(current) <= len(filler):
                    continue
                shorter = current[: -len(filler)]
                # Normalize surrounding brackets/punctuation before comparing after stripping, since fillers often follow
                # closers. Generic segmentation of 关于《你好，李焕英》就别提了。 yields
                # 你好，李焕英》就; stripping 就 leaves 你好，李焕英》. Extra
                # 》 prevents matching dedicated 你好，李焕英, persisting malformed output for three days
                # (codex P2). Use the same pre-persistence _TRIM_TRAIL normalization.
                # Apply **the same _trim_term** after stripping, not punctuation only: competitors
                # are already trimmed while intermediates retain particles. In 關於전남친은就別提了。,
                # removing 就 yields 전남친은, unequal to already-trimmed 전남친,
                # persisting malformed output for three days (codex P2).
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
# System-prompt fragment injected into the next conversation.
# ---------------------------------------------------------------------------
# Recent user topic-avoidance history is joined as ``- {term1}\n- {term2}\n``, then
# locale templates wrap it in headers/footers. Two placeholders:
#   {items}     —— bullet list
#   {n} -> entry count, supporting singular/plural grammar in some languages.
#
# Rendering: UserDirectivesManager.render_prompt_block(lanlan_name, lang).

USER_DIRECTIVES_PROMPT_BLOCK = {
    'zh': (
        "\n\n[用户最近明确表示过不想聊或不喜欢被提到以下内容（共{n}项）]\n"
        "{items}\n"
        "请在本次会话里主动避开这些话题或称呼，除非用户自己重新提起。"
    ),
    'zh-TW': (
        "\n\n[使用者最近明確表示過不想聊、或不喜歡被提到以下內容（共{n}項）]\n"
        "{items}\n"
        "請在這次的對話裡主動避開這些話題或稱呼，除非使用者自己重新提起。"
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
# Anti-repeat: inject recent high-frequency topic-word guidance.
# ---------------------------------------------------------------------------
# Source: memory.anti_repeat.AntiRepeatCorpus.top_recent_topics. Inject like
# USER_DIRECTIVES_PROMPT_BLOCK, at _build_initial_prompt's end after the ban list.
# Shared by proactive and regular replies: proactive also uses BM25 total-score thresholds
# to regenerate/drop, while regular relies only on this soft prompt constraint.
#
# Tone differs from bans: explicit user prohibitions require strong constraints;
# this merely recommends new angles after recent topics, so avoid excessive weight that makes LLMs
# switch topics compulsively.

RECENT_TOPIC_HINT_PROMPT_BLOCK = {
    'zh': (
        "\n\n[最近几轮你已经聊过的话题（{n}项）]\n"
        "{items}\n"
        "如果还没必要，尽量换个角度或换个话题，避免连续围绕同一主题打转。"
    ),
    'zh-TW': (
        "\n\n[最近幾輪你已經聊過的話題（{n}項）]\n"
        "{items}\n"
        "如果還沒必要，盡量換個角度或換個話題，避免一直繞著同一個主題打轉。"
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
# Proactive regeneration instructions for resampling.
# ---------------------------------------------------------------------------
# When BM25 total exceeds REGEN_THRESHOLD, main_routers/system_router injects
# this before the second Phase-2 LLM call, specifying terms to avoid.
#
# Deliberately structured instructions with explicit non-repetition constraints. Earlier prose imperatives
# (switch to a completely different angle/topic) at long-context ends made weak models
# emit instructions/planning scaffolds as dialogue (observed leaks: 完全不同的角度或主题 / 括号、Emoji).
# Each now (1) labels itself as rewriting instructions, not dialogue, with bracketed headings; (2) ends
# explicitly forbidding repeating/explaining instructions or output beyond tagged replies. Injection also
# puts BEGIN triggers last (see system_router), so instructions are not the model's final sentence.
# Placeholders: {terms}, words to avoid; {master_name}, conversation partner.

PROACTIVE_REGEN_AVOID_INSTRUCTION = {
    'zh': (
        "【改写要求】这些词和话题最近已经聊得太多，本次必须避开：{terms}。"
        "换个角度或换个话题，直接写一句全新的搭话。"
        "输出严格遵守上面的格式：第一行写来源标签，第二行起只写要对{master_name}说的原话；"
        "如果想不出新角度，就只输出 [PASS]。"
        "不要复述或解释本要求，不要输出任何思考过程、清单或标签化回复以外的内容。"
    ),
    'zh-TW': (
        "【改寫要求】這些詞和話題最近已經聊得太多，這次必須避開：{terms}。"
        "換個角度或換個話題，直接寫一句全新的搭話。"
        "輸出嚴格遵守上面的格式：第一行寫來源標籤，第二行起只寫要對{master_name}說的原話；"
        "如果想不出新角度，就只輸出 [PASS]。"
        "不要複述或解釋這項要求，不要輸出任何思考過程、清單或標籤化回覆以外的內容。"
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


# Default address for render_regen_avoid_instruction, neutral when master_name is omitted.
# Avoid objectifying labels such as 主人/master, per project conventions.
_DEFAULT_ADDRESSEE = {
    "zh": "对方",
    "zh-TW": "對方",
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
    # Bracket words individually, clearly separating avoided terms (「」 for CJK, double quotes elsewhere),
    # then join using locale-specific list separators.
    lq, rq = ("「", "」") if short in ("zh", "ja") else ('"', '"')
    sep = "、" if short in ("zh", "ja") else ", "
    quoted_terms = sep.join(f"{lq}{t}{rq}" for t in terms)
    return template.format(
        terms=quoted_terms,
        master_name=master_name or _DEFAULT_ADDRESSEE.get(short, "them"),
    )


# ---------------------------------------------------------------------------
# Proactive format-correction instructions to recover malformed first drafts.
# ---------------------------------------------------------------------------
# If drafts lack valid source tags, weak models may emit persona Format/constraints as dialogue,
# e.g. "No Markdown: Yes.". system_router injects this and regenerates once, restoring
# source-tag-first-line/body-after format; place with BEGIN triggers inside Human turns,
# still ending in neutral triggers. Placeholder: {master_name}, conversation partner.

PROACTIVE_FORMAT_FIX_INSTRUCTION = {
    'zh': (
        "【格式纠正】上一次的输出没有按规定格式，把格式要求当成正文吐了出来。"
        "请重写：第一行只写一个来源标签（按上面输出格式段列出的来源标签选，"
        "如 [CHAT]、[WEB]、[MUSIC]、[MEME]），第二行起只写要对{master_name}说的话本身；"
        "没什么新鲜的可说就只输出 [PASS]。"
        "不要复述或解释任何规则，不要输出清单或思考过程，标签和正文以外的内容一律不要输出。"
    ),
    'zh-TW': (
        "【格式修正】上一次的輸出沒有照規定的格式，把格式要求當成正文吐了出來。"
        "請重寫：第一行只寫一個來源標籤（照上面輸出格式那段列出的來源標籤挑，"
        "例如 [CHAT]、[WEB]、[MUSIC]、[MEME]），第二行起只寫要對{master_name}說的話本身；"
        "沒什麼新鮮的可以講就只輸出 [PASS]。"
        "不要複述或解釋任何規則，不要輸出清單或思考過程，標籤和正文以外的內容一律不要輸出。"
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
# Purpose: after local keywords match user phrases such as 别提了 / 换个话题, invoke a small LLM
# once to identify which topic the user means versus general emotion. Watermark: "======以上为".
#
# Previously in prompts_memory.py: negative-intent prompts/keywords and this module's
# ban-topic regex/extraction handle the same negative/avoidance instructions,
# centralized for future vocabulary/prompt consistency.
# Evidence-system integration remains app/memory_server._amaybe_trigger_negative_keyword_hook.

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
    # All locales share the same literal simplified-Chinese separator watermark, like other prompt tables;
    # traditional follows unchanged: a model boundary anchor, not user-facing wording.
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
# Deterministic local frozenset scan, asynchronously dispatching Layer-2 LLM judgment after hits.
# Intended meaning: users want AI silence / topic avoidance, including annoyance phrases because topic-context
# usage usually requests ending topics. **Exclude pure emotion words** (焦虑/崩溃/
# 难受/失望/痛苦…): often isolated without avoidance intent, causing useless LLM calls.
# Avoid single characters too: 烦 falsely matches 麻烦你/麻烦了; two-plus characters are steadier.
#
# zh and zh-TW have **independent vocabularies**, not spellings of one list: entries match actual
# typed characters, whose code points differ. Simplified entries yield zero traditional matches; pair entries including traditional
# forms identical across scripts, copied to expose one-sided edits). scan_negative_keywords scans their union
# for the whole zh family; see its documentation.
NEGATIVE_KEYWORDS_I18N: dict[str, frozenset[str]] = {
    "zh": frozenset(
        [
            # Explicit avoidance.
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
            # Annoyance, implicitly wanting the topic to end.
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
            # Explicit avoidance.
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
            # Annoyance, implicitly wanting the topic to end.
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
            # Explicit avoidance.
            "その話は",
            "その話はもう",
            "その話やめ",
            "やめて",
            "話題を変えて",
            "別の話",
            "他の話",
            "言わないで",
            "黙って",
            # Annoyance, implicitly wanting the topic to end.
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


# Chinese scan union: compute once as a constant. scan_negative_keywords runs on every user message
# (each post_turn × user_msgs), and function-local union rebuilds an 80-element
# frozenset per message.
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
    # Strip region suffixes only (zh-CN/zh_CN/en-US/pt-BR...), preserving unknown -> zh fallback.
    # Also trim surrounding whitespace and lowercase: upstream EN-US or " en-US " yields
    # EN / ' en' after splitting, missing lowercase whitespace-free keys and wrongly falling back to zh
    # (CodeRabbit Minor).
    short = (lang or "").strip().lower().split('-', 1)[0].split('_', 1)[0]
    kws = NEGATIVE_KEYWORDS_I18N.get(short)
    # Require **normalized** short == "zh": the preceding line already removed regions,
    # making raw-lang tw checks here permanently false. zh / zh-CN /
    # zh-TW / zh-Hant and all unknown languages use the union, extending the existing unknown-as-Chinese
    # contract unchanged across both scripts.
    if kws is None or short == "zh":
        kws = _ZH_SCAN_KEYWORDS
    lower = message.lower()
    for kw in kws:
        if kw.lower() in lower:
            return True
    return False


# from ... import * bypasses __getattr__. Without __all__, Python enumerates module globals,
# making lazy DIRECTIVE_PATTERNS **silently disappear** from wildcard imports, then causing downstream
# NameError (codex). Explicit __all__ restores it: import * uses getattr
# for each listed name, triggering lazy access normally.
#
# Derive other names from actual current public globals rather than handwritten lists. Historical behavior
# without __all__ exports every non-underscore module name; manual lists silently narrow that
# surface. Keep this at file end so globals() is complete.
__all__ = sorted(
    {name for name in globals() if not name.startswith("_")} | {"DIRECTIVE_PATTERNS"}
)
