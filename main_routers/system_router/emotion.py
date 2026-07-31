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

"""Emotion analysis: label normalization tables, keyword heuristics and
the /emotion/analysis endpoint.

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import _validate_local_mutation_request, logger, router
import difflib
import math
import re
from fastapi import Request
from utils.llm_client import (
    create_chat_llm_async,
)
from ..shared_state import (
    get_config_manager,
    get_session_manager,
    get_sync_message_queue,
)
from config import (
    EMOTION_ANALYSIS_MAX_TOKENS,
)
from config.prompts.prompts_emotion import (
    get_outward_emotion_analysis_prompt,
    get_emotion_keywords_flat,
    get_angry_attack_patterns_flat,
    get_sad_vulnerable_patterns_flat,
    get_happy_playful_patterns_flat,
    get_heuristic_negation_tokens_flat,
    get_heuristic_tight_negation_tokens_flat,
    get_heuristic_negation_blocklist_flat,
    get_heuristic_contrast_conjunctions_flat,
    get_emotion_label_aliases_flat,
    get_emotion_negation_prefixes_flat,
    get_emotion_negation_words_flat,
    get_emotion_negation_suffixes_flat,
    get_emotion_negation_degree_adverbs_flat,
    get_heuristic_modal_negations_flat,
    get_emotion_keyword_false_friends_flat,
)
from utils.language_utils import detect_prompt_language


# 统一的表情包图源白名单由 utils.meme_fetcher 维护，本文件仅用于引入

# 多语言关键词/别名表统一在 config/prompts/prompts_emotion.py 维护，此处只做扁平索引。
_EMOTION_LABEL_ALIASES = get_emotion_label_aliases_flat()


_EMOTION_CANONICAL_LABELS = ("happy", "sad", "angry", "surprised", "neutral")


_EMOTION_NORMALIZED_ALIAS_LOOKUP = {}


_EMOTION_COMPACT_ALIAS_LOOKUP = {}


for _alias, _canonical in _EMOTION_LABEL_ALIASES.items():
    _normalized_alias = re.sub(r"[\s\-_]+", " ", str(_alias).strip().lower())
    if not _normalized_alias:
        continue
    _EMOTION_NORMALIZED_ALIAS_LOOKUP[_normalized_alias] = _canonical
    _compact_alias = re.sub(r"[\W_]+", "", _normalized_alias, flags=re.UNICODE)
    if _compact_alias and _compact_alias not in _EMOTION_COMPACT_ALIAS_LOOKUP:
        _EMOTION_COMPACT_ALIAS_LOOKUP[_compact_alias] = _canonical


_EMOTION_FUZZY_ALIAS_KEYS = tuple(_EMOTION_NORMALIZED_ALIAS_LOOKUP.keys())


_EMOTION_FUZZY_COMPACT_KEYS = tuple(_EMOTION_COMPACT_ALIAS_LOOKUP.keys())


_ASCII_EMOTION_ALIAS_RE = re.compile(r"^[a-z0-9]+(?:\s+[a-z0-9]+)*$")


# 否定词表同样按语种维护在 config/prompts/prompts_emotion.py，此处只做扁平索引。
_EMOTION_NEGATION_WORDS = frozenset(get_emotion_negation_words_flat())


_EMOTION_NEGATION_PREFIXES = get_emotion_negation_prefixes_flat()


_EMOTION_NEGATION_SUFFIXES = get_emotion_negation_suffixes_flat()


# 保留撇号：否则 `don't` 被切成 `don` + `t`，两半都不在否定词表里，而
# `_is_negated_ascii_match` 正是按 token 回看三个词来判 `don't be sad` 的。
_EMOTION_TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", flags=re.UNICODE)


_NON_WORD_RE = re.compile(r"[\W_]", flags=re.UNICODE)


_EMOTION_NEGATION_COMPACT_PREFIXES = tuple(sorted({
    re.sub(r"[\W_]+", "", str(negation).strip().lower(), flags=re.UNICODE)
    for negation in (*_EMOTION_NEGATION_PREFIXES, *_EMOTION_NEGATION_WORDS)
    if str(negation).strip()
}, key=len, reverse=True))


_EMOTION_NEGATION_COMPACT_SUFFIXES = tuple(sorted({
    re.sub(r"[\W_]+", "", str(negation).strip().lower(), flags=re.UNICODE)
    for negation in _EMOTION_NEGATION_SUFFIXES
    if str(negation).strip()
}, key=len, reverse=True))


_EMOTION_NEGATION_COMPACT_PREFIX_SET = frozenset(_EMOTION_NEGATION_COMPACT_PREFIXES)


_HEURISTIC_MODAL_NEGATIONS = tuple(sorted(
    {
        re.sub(r"[\W_]+", "", str(word).strip().lower(), flags=re.UNICODE)
        for word in get_heuristic_modal_negations_flat()
        if str(word).strip()
    },
    key=len,
    reverse=True,
))


_EMOTION_NEGATION_CONTEXT_WINDOW = max(
    (len(negation) for negation in _EMOTION_NEGATION_COMPACT_PREFIXES),
    default=6,
)


_EMOTION_DEGREE_ADVERBS = tuple(sorted(
    {
        re.sub(r"[\W_]+", "", str(adverb).strip().lower(), flags=re.UNICODE)
        for adverb in get_emotion_negation_degree_adverbs_flat()
        if str(adverb).strip()
    },
    key=len,
    reverse=True,
))


def _strip_degree_adverbs(text):
    """Peel degree adverbs off the end of `text`, longest first, repeatedly.

    The compact negation test compares a punctuation-free window against the
    negation table, so a degree adverb sitting between the negation and the
    emotion word breaks the comparison and the label comes back as the emotion
    itself -- the opposite of what the model said. English needs no such thing:
    its match runs over the last three *tokens*, and a compact CJK window has no
    token boundaries to count.

    Longest first, and repeatedly, because adverbs stack and because a shorter
    entry can be the tail of a longer one -- strip the short one and the
    remainder no longer matches anything.
    """
    # 具体例子：`不怎麼開心` 的窗口是 `不怎麼`，剥掉 `怎麼` 才 endswith `不`；
    # `不是很特別開心` 要连剥 `特別` 和 `很` 两次。
    previous = None
    while text and text != previous:
        previous = text
        for adverb in _EMOTION_DEGREE_ADVERBS:
            if text.endswith(adverb):
                text = text[:-len(adverb)]
                break
    return text


def _looks_like_emotion_compact_candidate(candidate, cutoff):
    if not candidate:
        return False
    if candidate in _EMOTION_COMPACT_ALIAS_LOOKUP:
        return True
    return bool(difflib.get_close_matches(
        candidate,
        _EMOTION_FUZZY_COMPACT_KEYS,
        n=1,
        cutoff=cutoff,
    ))


def _strip_negation_blocklist(text):
    """Drop the words that merely *contain* a negation character.

    Removed rather than blanked: both callers compare against the end of a
    fixed-width window, so leaving spaces behind would push a real negation out
    of that window and the phrase would read as un-negated.
    """
    # e.g. `別特別開心` -- blanking would leave `別  `, which ends in whitespace
    # rather than in the negation that is actually there.
    for phrase in _HEURISTIC_NEGATION_BLOCKLIST:
        if phrase and phrase in text:
            text = text.replace(phrase, '')
    return text


def _alias_after(compact_text, position):
    """Whether any emotion alias appears at or after `position`.

    Scoped to what follows the marker on purpose, so the very word being denied
    cannot count as the later assertion. On today's tables the per-match check in
    the alias scan happens to reach the same answer either way, so no test can
    tell the two apart — the scoping is intent, not a load-bearing optimisation.
    """
    tail = compact_text[position:]
    return any(alias and alias in tail for alias in _EMOTION_COMPACT_ALIAS_LOOKUP)


def _marker_attaches_to_head(head):
    """Whether a postposed negation is denying the emotion word right before it.

    "so sad I can't even cry" is sad: the marker denies the crying, and the
    sadness is the reason it is being mentioned. The fuzzy test alone read the
    whole run before the marker as one misspelt emotion word and answered the
    opposite. So when the head does contain an emotion word, that word has to be
    the thing the marker sits against; a head with none is still handed to the
    fuzzy test, which is what it was for.
    """
    # 上面那句对应的是 `難過哭不出來`，`不出來` 否定的是 `哭` 不是 `難過`。
    present = [alias for alias in _EMOTION_COMPACT_ALIAS_LOOKUP if alias and alias in head]
    return any(head.endswith(alias) for alias in present) if present else True


def _last_clause_cut(head):
    """Index of the last thing in `head` that ends a clause, or -1 for none.

    Punctuation or a contrast conjunction, whichever comes later. Both mark the
    same thing for our purposes: what precedes it is being left behind.
    """
    cut = max((head.rfind(delim) for delim in _HEURISTIC_CLAUSE_DELIMITERS), default=-1)
    for conjunction in _HEURISTIC_CONTRAST_CONJUNCTIONS:
        found = head.rfind(conjunction)
        if found >= 0:
            cut = max(cut, found + len(conjunction) - 1)
    return cut


def _has_negated_emotion_phrase(normalized_text, compact_text, fuzzy_compact_cutoff):
    # Blocklist first, once, for both of the whole-label branches below: `sin
    # duda feliz` is emphatic agreement and the negation inside that fixed phrase
    # is not one. The heuristic already read this table; on the label side only
    # the per-match path did, so the two branches that answer for the whole label
    # went on treating the phrase as a denial.
    sanitized_text = _strip_negation_blocklist(normalized_text)
    sanitized_compact = re.sub(r"[\W_]+", "", sanitized_text, flags=re.UNICODE)
    tokens = [token for token in _EMOTION_TOKEN_RE.findall(sanitized_text) if token]
    # The two branches below answer for the WHOLE label off a single negation at
    # its front, so they may only speak for a label that *is* one clause. Both
    # read `não triste, feliz` as one run -- the negation dropped and the rest
    # glued into `tristefeliz`, which scores close enough to `triste` at the
    # confidence the endpoint passes -- and returned neutral, so the label named
    # the emotion it was asserting and got nothing. Past a clause break the
    # per-match scan is the one that can answer; it looks at each alias on its
    # own. (The postposed loop further down is scoped by `_alias_after` instead,
    # which is sharper: `我笑不起來，其實真的開心不起來` has a comma and still has
    # to be vetoed.)
    single_clause = _last_clause_cut(normalized_text) < 0
    if tokens and single_clause and any(
        token in _EMOTION_NEGATION_WORDS for token in tokens
    ):
        remaining_compact = re.sub(
            r"[\W_]+",
            "",
            "".join(token for token in tokens if token not in _EMOTION_NEGATION_WORDS),
            flags=re.UNICODE,
        )
        if _looks_like_emotion_compact_candidate(remaining_compact, fuzzy_compact_cutoff):
            return True

    for negation in _EMOTION_NEGATION_COMPACT_PREFIXES:
        if not single_clause or not sanitized_compact.startswith(negation):
            continue
        # Same string the `startswith` above tested, not the unstripped one:
        # slicing one by an offset found in the other mixes two coordinate
        # spaces. No input distinguishes them on today's tables -- consistency
        # here is intent, not something a test can hold in place.
        rest = sanitized_compact[len(negation):]
        if len(negation) <= 2 and negation.isascii():
            # Latin negations this short are syllables as often as words -- `ni`
            # turned `nice happy` into neutral at the confidence the endpoint
            # passes, because `cehappy` fuzzy-matches `happy`. Same rule the
            # single CJK character gets: what follows has to be an alias outright.
            if rest in _EMOTION_COMPACT_ALIAS_LOOKUP:
                return True
            continue
        if len(negation) == 1:
            # A single character is as likely to be the first half of a word as a
            # negation, so it only counts when what follows is an alias outright.
            # Fuzzy-matching past it read `非常生氣` as "not 生氣" (`常生氣` scores
            # 0.8 against `生氣`) and answered neutral at the confidence the
            # endpoint actually passes -- the emotion word itself never won.
            if rest in _EMOTION_COMPACT_ALIAS_LOOKUP:
                return True
            continue
        if _looks_like_emotion_compact_candidate(rest, fuzzy_compact_cutoff):
            return True

    for negation in _EMOTION_NEGATION_COMPACT_SUFFIXES:
        # Every occurrence, not just the first: `我笑不起來，其實真的開心不起來`
        # negates its *last* emotion word, and stopping at the first marker read
        # the label as the emotion it actually denies.
        marker_index = compact_text.find(negation)
        while marker_index > 0:
            head = compact_text[:marker_index]
            # This branch answers for the ENTIRE label, so it may only fire when
            # nothing follows the marker: `我難過不起來但很開心` denies the first
            # emotion and asserts the second, and vetoing here would report the
            # denial as the answer. A marker that negates one word among several
            # is handled per match in the alias scan below instead.
            if _marker_attaches_to_head(head) and _looks_like_emotion_compact_candidate(
                head, fuzzy_compact_cutoff
            ) and not _alias_after(compact_text, marker_index + len(negation)):
                return True
            marker_index = compact_text.find(negation, marker_index + 1)

    return False


# 启发式关键词/patterns 全部在 config/prompts/prompts_emotion.py 按语种维护，此处只做扁平化。
_EMOTION_KEYWORDS = get_emotion_keywords_flat()


_SAD_VULNERABLE_PATTERNS = get_sad_vulnerable_patterns_flat()


_ANGRY_ATTACK_PATTERNS = get_angry_attack_patterns_flat()


_HAPPY_PLAYFUL_PATTERNS = get_happy_playful_patterns_flat()


def _normalize_emotion_label(raw_emotion, raw_confidence=None):
    emotion_text = str(raw_emotion or "").strip().lower()
    if not emotion_text:
        return "neutral"
    normalized_text = re.sub(r"[\s\-_]+", " ", emotion_text)
    if normalized_text in _EMOTION_NORMALIZED_ALIAS_LOOKUP:
        return _EMOTION_NORMALIZED_ALIAS_LOOKUP[normalized_text]

    compact_text = re.sub(r"[\W_]+", "", emotion_text, flags=re.UNICODE)
    if compact_text in _EMOTION_COMPACT_ALIAS_LOOKUP:
        return _EMOTION_COMPACT_ALIAS_LOOKUP[compact_text]

    high_confidence = raw_confidence is not None and _coerce_emotion_confidence(raw_confidence, 0.0) >= 0.72
    fuzzy_alias_cutoff = 0.74 if high_confidence else 0.9
    fuzzy_compact_cutoff = 0.72 if high_confidence else 0.88

    if _has_negated_emotion_phrase(normalized_text, compact_text, fuzzy_compact_cutoff):
        return "neutral"

    def _is_negated_ascii_match(match_start):
        # Three tokens of lookback is generous enough to cross a clause: `não
        # triste, feliz` and `not sad but happy` both name the emotion they are
        # asserting *after* the one they deny, and the denial reached forward and
        # cancelled it. So stop at whichever comes last — punctuation or a
        # contrast conjunction. The compact path already scopes this way; the
        # ASCII one never did.
        head = normalized_text[:match_start]
        head = _strip_negation_blocklist(head[_last_clause_cut(head) + 1:])
        prefix_tokens = _EMOTION_TOKEN_RE.findall(head)
        return any(token in _EMOTION_NEGATION_WORDS for token in prefix_tokens[-3:])

    # Where each compact character came from, so a clause boundary can be found in
    # the original text. compact_text has the punctuation removed, which is what
    # made `不是，非常开心` look contiguous.
    compact_origin = [
        index for index, char in enumerate(emotion_text)
        if not _NON_WORD_RE.match(char)
    ]

    def _current_clause(match_start):
        """The compact text before `match_start` that shares its clause.

        Punctuation is gone from compact_text, so a comma earlier in the label
        would otherwise be invisible and the lookback would reach across it.
        """
        prefix = compact_text[max(0, match_start - _EMOTION_NEGATION_CONTEXT_WINDOW):match_start]
        if match_start >= len(compact_origin):
            return prefix
        head = emotion_text[:compact_origin[match_start]]
        cut = max((head.rfind(delim) for delim in _HEURISTIC_CLAUSE_DELIMITERS), default=-1)
        if cut < 0:
            return prefix
        # How many compact characters survive the cut — anything before the
        # delimiter belongs to a different clause.
        kept = sum(1 for origin in compact_origin[:match_start] if origin > cut)
        return prefix[len(prefix) - min(len(prefix), kept):]

    def _is_negated_compact_match(match_start):
        # The blocklist goes first, before anything measures this window: `別` is
        # a negation on its own but only a syllable inside `個別` / `區別`, and the
        # adjacency test below cannot tell them apart -- it read `個別難過` as
        # "don't be sad" and answered neutral.
        prefix = _strip_negation_blocklist(_current_clause(match_start))
        peeled = _strip_degree_adverbs(prefix)
        adverbs = len(prefix) - len(peeled)
        # A negation adjacent to the alias still counts, as long as it reaches
        # back past the intensifiers rather than living inside one: `我不太开心`
        # ends with the negation `不太`, while `我特別開心` only appears to end
        # with `別` because `特別` is an adverb. Peeling first and testing second
        # would lose the former; testing first and peeling never would keep the
        # latter.
        if any(
            len(negation) > adverbs and prefix.endswith(negation)
            for negation in _EMOTION_NEGATION_COMPACT_PREFIXES
        ):
            return True
        # No early-out when nothing was peeled: the test above already ran every
        # negation against the unchanged window, so the two below can only agree.
        # The negation, if there is one, is what peeling uncovers, and it has to
        # really be one: a single character is a coincidence waiting to happen
        # (`分别很开心` peels to `分别`), so it must be the whole of what is left;
        # two or more are specific enough to sit after other text (`我沒有很生氣`).
        return peeled in _EMOTION_NEGATION_COMPACT_PREFIX_SET or any(
            len(negation) > 1 and peeled.endswith(negation)
            for negation in _EMOTION_NEGATION_COMPACT_PREFIXES
        )

    alias_items = sorted(
        _EMOTION_NORMALIZED_ALIAS_LOOKUP.items(),
        key=lambda item: len(item[0]),
        reverse=True
    )
    # Every un-negated match, then one rule to pick between them. Returning on the
    # first alias found made the answer depend on dict order rather than on the
    # text: `中性但開心` and `平靜但生氣` are the same shape yet came back neutral
    # and angry respectively.
    matches: list[tuple[int, int, str]] = []
    for alias, canonical in alias_items:
        if not alias:
            continue
        if _ASCII_EMOTION_ALIAS_RE.match(alias):
            pattern = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
            for match in re.finditer(pattern, normalized_text):
                if not _is_negated_ascii_match(match.start()):
                    # Positions are compared across scripts, so both kinds have to
                    # be in the same space: an ASCII index counts the punctuation
                    # that compact_text drops, and leading punctuation alone was
                    # enough to order a later alias ahead of an earlier one.
                    compact_start = len(
                        _NON_WORD_RE.sub("", normalized_text[:match.start()])
                    )
                    # Same postposed check the compact branch does. A label can
                    # mix scripts -- `sadじゃないけどhappy` denies the ASCII alias
                    # with a Japanese suffix -- and without this the denied half
                    # stayed in the running and won the ranking.
                    if any(
                        compact_text.startswith(marker, compact_start + len(alias))
                        for marker in _EMOTION_NEGATION_COMPACT_SUFFIXES
                    ):
                        continue
                    matches.append((compact_start, -len(alias), canonical))
            continue

        compact_alias = re.sub(r"[\W_]+", "", alias, flags=re.UNICODE)
        if not compact_alias:
            continue
        search_start = 0
        while True:
            match_start = compact_text.find(compact_alias, search_start)
            if match_start < 0:
                break
            if not _is_negated_compact_match(match_start) and not any(
                compact_text.startswith(marker, match_start + len(compact_alias))
                for marker in _EMOTION_NEGATION_COMPACT_SUFFIXES
            ):
                matches.append((match_start, -len(compact_alias), canonical))
            search_start = match_start + len(compact_alias)

    if matches:
        # A named emotion outranks `neutral`: a label saying both (`中性但開心`)
        # is hedging, and the named half is the one worth showing. Within a rank,
        # earliest in the text, longest alias first — so the answer is a property
        # of the label, not of iteration order.
        named = [m for m in matches if m[2] != "neutral"] or matches
        return min(named)[2]

    fuzzy_alias_match = difflib.get_close_matches(
        normalized_text,
        _EMOTION_FUZZY_ALIAS_KEYS,
        n=1,
        cutoff=fuzzy_alias_cutoff
    )
    if fuzzy_alias_match:
        return _EMOTION_NORMALIZED_ALIAS_LOOKUP[fuzzy_alias_match[0]]

    if compact_text:
        fuzzy_compact_match = difflib.get_close_matches(
            compact_text,
            _EMOTION_FUZZY_COMPACT_KEYS,
            n=1,
            cutoff=fuzzy_compact_cutoff
        )
        if fuzzy_compact_match:
            return _EMOTION_COMPACT_ALIAS_LOOKUP[fuzzy_compact_match[0]]

    if high_confidence:
        fuzzy_canonical = difflib.get_close_matches(
            normalized_text,
            _EMOTION_CANONICAL_LABELS,
            n=1,
            cutoff=0.55
        )
        if fuzzy_canonical:
            return fuzzy_canonical[0]

    return "neutral"


def _push_emotion_update(lanlan_name, emotion, confidence):
    sync_message_queue = get_sync_message_queue()
    if lanlan_name and lanlan_name in sync_message_queue:
        sync_message_queue[lanlan_name].put({
            "type": "json",
            "data": {
                "type": "emotion",
                "emotion": emotion,
                "confidence": confidence
            }
        })


def _emotion_response(emotion, confidence):
    return {
        "emotion": emotion,
        "confidence": confidence
    }


def _coerce_emotion_confidence(raw_confidence, default=0.5):
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = float(default)
    if not math.isfinite(confidence):
        confidence = float(default)
    return max(0.0, min(1.0, confidence))


# 启发式打分时的否定回看 token / 转折连词表统一在 config/prompts/prompts_emotion.py 按语种维护。
_HEURISTIC_NEGATION_TOKENS = get_heuristic_negation_tokens_flat()


# The Latin entries in that table carry padding spaces to fake a word boundary,
# which only works on one side: `no ` also matches inside `sino ` / `bueno ` /
# `uno `, so `sino que estoy feliz` came back with no emotion at all. Match those
# on real boundaries instead and leave the CJK entries on the substring path,
# where there are no word boundaries to find.
# The split is by writing system, not by `isascii()`: `não ` and `jamás ` are
# Latin words that merely carry an accent, and leaving them on the substring path
# meant `senão fico feliz` found `não ` inside `senão` and lost the emotion.
# Scripts with no word boundaries to find -- Han, kana, Hangul -- stay on
# substring, where their entries are morpheme fragments rather than words.
_UNBOUNDED_SCRIPT_RE = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]"
)
_HEURISTIC_WORD_NEGATIONS = tuple(
    token for token in _HEURISTIC_NEGATION_TOKENS
    if token.strip() and not _UNBOUNDED_SCRIPT_RE.search(token)
)
_HEURISTIC_ASCII_NEGATION_RE = re.compile(
    r"\b(?:%s)\b" % "|".join(
        re.escape(token.strip())
        for token in sorted(_HEURISTIC_WORD_NEGATIONS, key=len, reverse=True)
    )
)
_HEURISTIC_CJK_NEGATION_TOKENS = tuple(
    token for token in _HEURISTIC_NEGATION_TOKENS
    if _UNBOUNDED_SCRIPT_RE.search(token)
)


_HEURISTIC_TIGHT_NEGATION_TOKENS = get_heuristic_tight_negation_tokens_flat()


_HEURISTIC_NEGATION_BLOCKLIST = get_heuristic_negation_blocklist_flat()


_EMOTION_KEYWORD_FALSE_FRIENDS = get_emotion_keyword_false_friends_flat()


_HEURISTIC_CONTRAST_CONJUNCTIONS = get_heuristic_contrast_conjunctions_flat()


_HEURISTIC_NEGATION_LOOKBACK = 14


# zh 单字否定（`不/没/别/未` 等）假阳率高，必须紧邻情绪词才算真否定，
# 避免 `不错/不思议/不具合` 等非否定词组里的单字误触发。
_HEURISTIC_TIGHT_NEGATION_LOOKBACK = 2


# 子句分隔符：回看窗口越过分隔符后的内容视为另一小句，不再修饰本次命中。
# 避免 "我不是难过，我是生气" 中 `生气` 的回看抓到前一小句的 `不` 而被误判否定。
_HEURISTIC_CLAUSE_DELIMITERS = (
    '.', ',', ';', '!', '?', '\n',
    '，', '。', '；', '！', '？', '、', '：', ':',
)


def _has_heuristic_negation_before(text_value, position):
    if position <= 0:
        return False
    start = max(0, position - _HEURISTIC_NEGATION_LOOKBACK)
    window = text_value[start:position]
    # 1) 窗口越过子句分隔符（标点）的部分丢掉，只看与命中关键词同小句的前文
    last_delim = -1
    for delim in _HEURISTIC_CLAUSE_DELIMITERS:
        idx = window.rfind(delim)
        if idx > last_delim:
            last_delim = idx
    if last_delim >= 0:
        window = window[last_delim + 1:]
    # 2) 句首场景补一个前导空格，统一处理带前导空格的 token（否定 ` no `、连词 ` but `）
    window = ' ' + window
    # 3) 让步/转折连词同样切断否定范围：处理 "not X but Y / 不是 X 而是 Y" 对比句，
    #    避免前半的否定被错误带到后半的情绪关键词。
    last_conj = -1
    for conj in _HEURISTIC_CONTRAST_CONJUNCTIONS:
        idx = window.rfind(conj)
        if idx >= 0:
            end_pos = idx + len(conj)
            if end_pos > last_conj:
                last_conj = end_pos
    if last_conj >= 0:
        window = window[last_conj:]
    # 4) 排除非否定固定搭配（`not only / 不仅 / не только` 等肯定结构里的 not/不/не
    #    并不是真否定）：把这些短语从 window 里替换成等长空白后再做 token 匹配。
    sanitized = _strip_negation_blocklist(window)
    # 5) 多字否定 token（宽 lookback）
    if any(token in sanitized for token in _HEURISTIC_CJK_NEGATION_TOKENS):
        return True
    if _HEURISTIC_ASCII_NEGATION_RE.search(sanitized):
        return True
    # 5.5) 情态复合否定（`不會 / 不算 / 不再 / 未必`）：这些词只有紧贴情绪词时
    #      才是在否定它 —— 放进上面那张宽回看表会否定同一小句里**另一个**谓语
    #      （`我不会唱歌也很开心`）。所以剥掉尾部的程度副词之后，要求它就压在
    #      情绪词前面。
    #      剥前剥后各判一次。剥后是为了 `不會真的開心`；剥前是为了**重叠关键词**：
    #      `有什麼好開心的` 命中的是更长的 `好開心`，窗口只剩 `有什麼` —— 那一段
    #      本身就是否定，而剥掉 `什麼` 把它拆散了，短的那个 `開心` 命中被正确压住、
    #      长的这个反而漏过去，整句读成 happy。
    peeled_window = _strip_degree_adverbs(sanitized)
    if any(
        sanitized.endswith(modal) or peeled_window.endswith(modal)
        for modal in _HEURISTIC_MODAL_NEGATIONS
    ):
        return True
    # 6) zh 单字否定 token：仅在紧邻命中关键词的尾部窗口里才算真否定，
    #    避免 `不错/不思议/不具合` 等非否定词组里的单字误触发整个否定。
    if _HEURISTIC_TIGHT_NEGATION_TOKENS:
        tight_window = sanitized[-_HEURISTIC_TIGHT_NEGATION_LOOKBACK:]
        if any(token in tight_window for token in _HEURISTIC_TIGHT_NEGATION_TOKENS):
            return True
    return False


# 英文 keyword 用 ASCII-only 词边界匹配，避免 `happy` 命中 `unhappy`、`surprised`
# 命中 `unsurprised` 这类反向情绪嵌入。
# 注意：不能用 `\b`，因为 Python regex 默认 Unicode 模式下 CJK 也算 \w，
# 在 mixed-script 文本（如 `好happy啊 / 超annoyed欸`）里 `好` 和 `h` 之间没有
# word boundary，导致英文 keyword 完全失配。改用前后 ASCII 字母断言：
# `(?<![a-zA-Z])keyword(?![a-zA-Z])`，CJK / 标点 / 空白都允许作为边界。
_ASCII_WORD_KEYWORD_RE_CACHE = {}


def _is_ascii_word_keyword(keyword):
    if not keyword:
        return False
    return all(c.isascii() and (c.isalpha() or c in " '") for c in keyword)


def _has_heuristic_negation_after(text_value, position):
    """Whether a postposed negation marker follows the keyword that ended here.

    Chinese negates from behind too — the label parser learned this first, and
    the heuristic reads the same user text. Anchored right after the keyword: a
    marker further along belongs to some later phrase.
    """
    return any(
        text_value.startswith(marker, position)
        for marker in _EMOTION_NEGATION_COMPACT_SUFFIXES
    )


def _count_keyword_hits(text_value, keyword):
    if not keyword or not text_value:
        return 0
    if _is_ascii_word_keyword(keyword):
        pattern = _ASCII_WORD_KEYWORD_RE_CACHE.get(keyword)
        if pattern is None:
            pattern = re.compile(r'(?<![a-zA-Z])' + re.escape(keyword) + r'(?![a-zA-Z])')
            _ASCII_WORD_KEYWORD_RE_CACHE[keyword] = pattern
        hits = 0
        for match in pattern.finditer(text_value):
            if not _has_heuristic_negation_before(text_value, match.start()):
                hits += 1
        return hits
    hits = 0
    search_start = 0
    while True:
        pos = text_value.find(keyword, search_start)
        if pos < 0:
            break
        if not _has_heuristic_negation_before(text_value, pos) and not (
            _has_heuristic_negation_after(text_value, pos + len(keyword))
        ):
            hits += 1
        search_start = pos + len(keyword)
    return hits


def _infer_emotion_from_text(text):
    text_value = str(text or "").lower()
    if not text_value:
        return None, 0

    # Dropped before anything is counted: these read as an emotion keyword but
    # are not one, and no negation is involved for the negation machinery to
    # catch. Removing rather than blanking keeps the lookback windows tight.
    for phrase in _EMOTION_KEYWORD_FALSE_FRIENDS:
        if phrase and phrase in text_value:
            text_value = text_value.replace(phrase, '')
    if not text_value:
        return None, 0

    scores = {key: 0 for key in _EMOTION_KEYWORDS}
    for emotion, keywords in _EMOTION_KEYWORDS.items():
        for keyword in keywords:
            scores[emotion] += _count_keyword_hits(text_value, keyword)

    if "!!" in text_value or "！？" in text_value or "!?" in text_value or "??" in text_value:
        scores["surprised"] += 1

    sad_vulnerable_hits = sum(_count_keyword_hits(text_value, p) for p in _SAD_VULNERABLE_PATTERNS)
    angry_attack_hits = sum(_count_keyword_hits(text_value, p) for p in _ANGRY_ATTACK_PATTERNS)
    happy_playful_hits = sum(_count_keyword_hits(text_value, p) for p in _HAPPY_PLAYFUL_PATTERNS)

    if sad_vulnerable_hits:
        scores["sad"] += sad_vulnerable_hits * 2
    if angry_attack_hits:
        scores["angry"] += angry_attack_hits * 2
    if happy_playful_hits and not sad_vulnerable_hits and not angry_attack_hits:
        # playful patterns（哈哈/嘿嘿/嘻嘻/可爱/好耶 等）大量与 happy keyword 重叠，
        # 重复出现时 keyword 那边已经按命中数累加分数；这里只额外 +1 作为信号 boost，
        # 避免 `haha haha haha / 哈哈哈哈哈` 类 filler 文本被双倍放大触发 override。
        scores["happy"] += 1
    if sad_vulnerable_hits and happy_playful_hits:
        # 撒娇外壳下的委屈/想哭，优先视为 sad 而不是 happy
        scores["sad"] += 1

    best_emotion = None
    best_score = 0
    for emotion, score in scores.items():
        if score > best_score:
            best_emotion = emotion
            best_score = score

    if best_score <= 0:
        return None, 0
    return best_emotion, best_score


def _resolve_emotion_prompt_language(text, lanlan_name=None):
    # detect_language 分不出繁简（都是 zh），所以繁中使用者过去一律拿到简体 prompt。
    # detect_prompt_language 在 zh 这一支上用界面语言细分，其余语种原样短码。
    #
    # 界面语言优先取**该角色 session** 的 user_language：它由前端 i18n 真值设定，
    # 而进程级全局值来自 Steam/系统 locale。两者不一致时（在简体机器上切繁中，或
    # 反过来）全局值两个方向都会给错。取不到 session 才回落到全局。
    return detect_prompt_language(text, ui_language=_session_user_language(lanlan_name))


def _session_user_language(lanlan_name):
    if not lanlan_name:
        return None
    try:
        session = get_session_manager().get(lanlan_name)
    except Exception:
        return None
    return getattr(session, 'user_language', None)


@router.post('/emotion/analysis')
async def emotion_analysis(request: Request):
    """
    Emotion analysis endpoint.
    func:
    - receives text input, calls the configured emotion analysis model, and returns the emotion class and confidence
    - supports overriding the default API key and model name from request parameters for flexibility
    - parses the model response intelligently, tolerating different formats (plain text, markdown code blocks, JSON strings, etc.) for robustness
    - adjusts the emotion class by confidence, setting it to neutral when confidence is low, improving result reliability
    - pushes the result to the monitor system (when lanlan_name is provided) for realtime interaction and display with the frontend
    """
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    try:
        _config_manager = get_config_manager()
        data = await request.json()
        if not data or 'text' not in data:
            return {"error": "请求体中必须包含text字段"}
        
        text = data['text']
        lanlan_name = data.get('lanlan_name')
        if text is None or str(text).strip() == "":
            emotion = "neutral"
            confidence = 0.5
            _push_emotion_update(lanlan_name, emotion, confidence)
            return _emotion_response(emotion, confidence)

        api_key = data.get('api_key')
        model = data.get('model')
        
        # 使用参数或默认配置，使用 .get() 安全获取避免 KeyError
        emotion_config = await _config_manager.aget_model_api_config('emotion')
        emotion_api_key = emotion_config.get('api_key')
        emotion_model = emotion_config.get('model')
        emotion_base_url = emotion_config.get('base_url')
        emotion_provider_type = emotion_config.get('provider_type')
        
        # 优先使用请求参数，其次使用配置
        api_key = api_key or emotion_api_key
        model = model or emotion_model
        
        if not api_key:
            return {"error": "情绪分析模型配置缺失: API密钥未提供且配置中未设置默认密钥"}
        
        if not model:
            return {"error": "情绪分析模型配置缺失: 模型名称未提供且配置中未设置默认模型"}
       
        prompt_lang = _resolve_emotion_prompt_language(text, lanlan_name)

        # 构建请求消息
        messages = [
            {
                "role": "system", 
                "content": get_outward_emotion_analysis_prompt(prompt_lang)
            },
            {
                "role": "user", 
                "content": text
            }
        ]

        from utils.token_tracker import set_call_type
        set_call_type("emotion")

        # 异步调用模型（使用统一工厂，自动处理 extra_body / provider 兼容）
        llm = await create_chat_llm_async(
            model,
            emotion_base_url,
            api_key,
            provider_type=emotion_provider_type,
            temperature=0.3,
            # Gemini 模型可能返回 markdown 格式，需要更多 token
            max_completion_tokens=EMOTION_ANALYSIS_MAX_TOKENS,
            timeout=30,
        )
        async with llm:
            result = await llm.ainvoke(messages)

        # 解析响应
        result_text = result.content.strip()

        # 处理 markdown 代码块格式（Gemini 可能返回 ```json {...} ``` 格式）
        # 首先尝试使用正则表达式提取第一个代码块
        code_block_match = re.search(r"```(?:json)?\s*(.+?)\s*```", result_text, flags=re.S)
        if code_block_match:
            result_text = code_block_match.group(1).strip()
        elif result_text.startswith("```"):
            # 回退到原有的行分割逻辑
            lines = result_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]  # 移除第一行
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]  # 移除最后一行
            result_text = "\n".join(lines).strip()
        
        # 尝试解析JSON响应
        emotion = "neutral"
        confidence = 0.5

        def _apply_degraded_emotion_fallback():
            heuristic_emotion, heuristic_score = _infer_emotion_from_text(text)
            if heuristic_emotion:
                return heuristic_emotion, min(0.62, 0.34 + heuristic_score * 0.1)
            # 当模型结果不可用或缺少足够关键词线索时，回退到 neutral。
            return "neutral", 0.5

        try:
            from utils.file_utils import robust_json_loads
            result = robust_json_loads(result_text)
            if not isinstance(result, dict):
                # 有效 JSON 也可能是 null/[]/"text"，此时复用降级启发式处理。
                emotion, confidence = _apply_degraded_emotion_fallback()
            else:
                # 获取emotion和confidence
                raw_emotion = result.get("emotion", "neutral")
                raw_confidence = result.get("confidence", 0.5)
                emotion = _normalize_emotion_label(raw_emotion, raw_confidence)
                confidence = _coerce_emotion_confidence(raw_confidence)
                decision_source = "model"

                heuristic_emotion, heuristic_score = _infer_emotion_from_text(text)
                if heuristic_emotion:
                    # 强 override：启发式分数较高（≥4）且模型置信度不算很高（<0.8）时
                    # 才推翻模型判断；避免单个吐槽词把模型 happy/neutral 翻成 angry。
                    if heuristic_emotion != emotion and heuristic_score >= 4 and confidence < 0.8:
                        emotion = heuristic_emotion
                        confidence = max(confidence, min(0.86, 0.44 + heuristic_score * 0.07))
                        decision_source = "heuristic_strong_override"
                    elif heuristic_emotion == "sad" and emotion == "happy" and heuristic_score >= 2:
                        emotion = heuristic_emotion
                        confidence = max(confidence, min(0.84, 0.5 + heuristic_score * 0.08))
                        decision_source = "heuristic_sad_override"
                    elif emotion == "neutral" and confidence < 0.6:
                        emotion = heuristic_emotion
                        confidence = max(confidence, min(0.78, 0.42 + heuristic_score * 0.12))
                        decision_source = "heuristic_from_neutral"
                    elif confidence < 0.25:
                        emotion = heuristic_emotion
                        confidence = max(confidence, min(0.65, 0.35 + heuristic_score * 0.1))
                        decision_source = "heuristic_from_low_confidence"

                # 当confidence很低时，自动将emotion设置为neutral，避免误报
                if confidence < 0.2:
                    emotion = "neutral"
                    decision_source = "neutral_fallback"
        except ValueError:
            emotion, confidence = _apply_degraded_emotion_fallback()

        _push_emotion_update(lanlan_name, emotion, confidence)
        return _emotion_response(emotion, confidence)
            
    except Exception as e:
        logger.error(f"情感分析失败: {e}")
        return {
            "error": f"情感分析失败: {str(e)}",
            "emotion": "neutral",
            "confidence": 0.0
        }
