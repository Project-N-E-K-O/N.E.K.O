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

# Speech synthesis reads these symbols aloud (hash, vertical bar, percent sign, etc.); remove them before enqueueing:
# - emoji, decorative symbols, arrows, modifiers, etc. in ``\p{S}``
# - additional technical symbols from ``\p{P}`` that do not represent sentence pauses (# @ & * _ | / \ ~ 〜 etc.)
# Sentence punctuation (。，、！？；：.!?,;: … · and quotes) remains, preserving pauses and intonation.
# Retain symbols whose removal changes meaning and that TTS already reads correctly:
# - Currency symbols (``\p{Sc}``): "$100" and "€20" read as one hundred dollars and twenty euros;
# - Percent signs % ％: "50%" reads as fifty percent; removing it yields fifty;
# - Common operators + − × ÷ = ≠ ≈ ± ≤ ≥: equations must remain speakable;
# - Temperature / degree symbols ℃ ℉ ° ("22°C" also uses °), plus CJK compatibility units
#   (U+3380–33FF, e.g. ㎡ ㎏ ㎞);
# - Minus and comparison signs (convert <= >= != to ≤ ≥ ≠ first; < > between operands) and
#   # in names such as C#: see ``_kept_symbols``.
# Remove variation selectors, zero-width joiners, and enclosing keycaps following symbols in compound emoji
# (❤️, 👩‍💻, 1️⃣), or invisible characters would reach TTS. Remove only immediately after symbols,
# preserving zero-width joiners used normally in scripts such as Devanagari.
_TTS_KEPT_SYMBOLS = "℃℉°+＋−×÷=＝≠≈±≤≥%％\u3380-\u33ff"
_TTS_MUTED_SYMBOL_CLASS = (
    r"(?![" + _TTS_KEPT_SYMBOLS + r"])(?!\p{Sc})["
    r"\p{S}"
    r"#＃@＠&＆\*＊_＿\-－﹣~～〜`｀\|｜\\/／＼"
    r"\^＾"
    r"<>＜＞«»‹›"
    r"•●○◆◇★☆※§¶†‡"
    r"]"
)
# Variation selectors and enclosing keycaps affect display only (they also follow the 1 in 1️⃣); remove them even alone.
_TTS_MUTED_SYMBOL_RE = regex.compile(
    r"(?:" + _TTS_MUTED_SYMBOL_CLASS + r")"
    r"(?:" + _TTS_MUTED_SYMBOL_CLASS + r"|[\u200d\ufe0e\ufe0f\u20e3])*"
    r"|[\ufe0e\ufe0f\u20e3]+"
)

# When streaming splits compound emoji ("👩", "‍💻"), the second chunk's leading joiner / variation selector
# no longer follows a symbol, so the regex above cannot remove it. The caller remembers whether the previous chunk
# ended in a removed symbol, then uses the following two functions.
# Recognize only trailing emoji symbols (\p{So}, excluding retained units) or joiners: a zero-width joiner immediately
# after removed symbols like % or # belongs to normal text (e.g. split Devanagari क्%ष), not emoji residue to remove.
_TTS_TRAILING_EMOJI_RE = regex.compile(
    r"(?:(?![" + _TTS_KEPT_SYMBOLS + r"])\p{So}|[\u200d\ufe0e\ufe0f\u20e3])\Z")
_TTS_LEADING_JOINERS_RE = regex.compile(r"\A[\u200d\ufe0e\ufe0f\u20e3]+")


def tts_chunk_ends_in_emoji(text: str) -> bool:
    """Whether ``text`` ends in an emoji symbol or joiner the TTS filter drops."""
    return bool(text) and _TTS_TRAILING_EMOJI_RE.search(text) is not None


def strip_leading_emoji_joiners(text: str) -> str:
    """Drop joiners / variation selectors left over from a split emoji."""
    return _TTS_LEADING_JOINERS_RE.sub("", text) if text else text


# Scripts without space-delimited words (Han, Kana, Thai, etc.): do not insert spaces after removing symbols between characters.
_TTS_UNSPACED_SCRIPT_RE = regex.compile(
    r"[\p{Han}\p{Hiragana}\p{Katakana}\p{Thai}\p{Lao}\p{Khmer}\p{Myanmar}]"
)
_TTS_WORD_CHAR_RE = regex.compile(r"[\p{L}\p{N}\p{M}]")
# Percentages such as "50%": % next to a removed symbol counts as an operand like digits. "50%-60%"
# reads as "50% 60%", without treating - as a minus sign or joining into "50%60%".
_TTS_PERCENT_SIGNS = frozenset("%％")


def is_tts_word_char(char: str) -> bool:
    """Whether a removed symbol next to ``char`` should leave a space behind.

    Letters and digits of space-delimited scripts (Latin, Cyrillic, Greek,
    Hangul, Devanagari, ...) qualify; scripts written without spaces between
    words (Han, kana, Thai, ...) do not.
    """
    return (
        len(char) == 1
        # Include combining marks: Devanagari words often end with vowel signs (ौ), which are not isalnum.
        and _TTS_WORD_CHAR_RE.match(char) is not None
        and _TTS_UNSPACED_SCRIPT_RE.match(char) is None
    )


def is_tts_operand_char(char: str) -> bool:
    """``is_tts_word_char``, also counting a percent sign as part of its number.

    For the character before a removed symbol: "50%-60%" is a range between
    two numbers, spoken "50% 60%", not "50%60%" or a negative 60.
    """
    return is_tts_word_char(char) or char in _TTS_PERCENT_SIGNS


_TTS_MINUS_SIGNS = frozenset("-－﹣")
_TTS_HASH_SIGNS = frozenset("#＃")
# Range separator: ~ before the second minus sign in "-10~-5℃".
_TTS_RANGE_SIGNS = frozenset("~～〜")
_TTS_COMPARISON_SIGNS = frozenset("<>＜＞")
_TTS_EQUALS_SIGNS = frozenset("=＝")
# Convert "<=", ">=", "!=" to single comparison symbols first; otherwise removing < > ! leaves "=", reversing meaning.
_TTS_COMPARISON_DIGRAPH_RE = regex.compile(r"[<＜][=＝]|[>＞][=＝]|[!！][=＝]")
_TTS_COMPARISON_DIGRAPHS = {"<": "≤", "＜": "≤", ">": "≥", "＞": "≥", "!": "≠", "！": "≠"}


# A segment containing only emoji, zero-width joiners, and variation selectors ("🌡️").
_TTS_EMOJI_RUN_RE = regex.compile(r"(?:\p{So}|[\u200d\ufe0e\ufe0f\u20e3])+")


def _is_name_hash_context(prev_char: str, prev2: str) -> bool:
    # "C#", "F#", "用C#": # follows a single English letter forming a standalone word.
    # "tag#", "#AI#", "#C#" are hashtags or ordinary words, not names.
    return (
        prev_char.isascii()
        and prev_char.isalpha()
        and not (prev2.isascii() and prev2.isalnum())
        and prev2 not in _TTS_HASH_SIGNS
    )


def _kept_symbols(
    symbol: str, prev_char: str, next_char: str, prev2: str = ""
) -> str | None:
    """What a run of removable symbols keeps, or None to drop it as usual.

    - "C#" / "F#": a "#" right after a single ASCII letter that stands on its
      own (``prev2`` is the character before that letter), not followed by a
      letter or digit, is part of the name ("C#😀 dev" keeps it as well);
      "C#-5" keeps both the "#" and the minus sign. Hashtags ("#AI#") and
      words ("tag#") lose the "#".
    - "-10~-5 C": a range sign followed by a minus before a digit keeps the
      minus with a separating space, so the upper bound stays below zero.
    - A minus sign before a digit, when no letter/digit precedes the run
      ("-5", "x = -3", "~-5°C"), or when only emoji precede it within the run
      ("temp🌡️-5°C"). Between a letter/digit and other removed symbols it is
      a separator, as in "3@-5", spoken "3 5" whether or not it was split;
      a percent sign counts as a digit here ("50%-60%" is "50% 60%").
    """
    head, rest = symbol[0], symbol[1:]
    if head in _TTS_HASH_SIGNS and _is_name_hash_context(prev_char, prev2):
        if rest in _TTS_MINUS_SIGNS and next_char.isdigit():
            return symbol
        if not (next_char.isascii() and next_char.isalnum()):
            return head
    if symbol[-1] in _TTS_MINUS_SIGNS and next_char.isdigit():
        lead = symbol[:-1]
        if (
            lead
            and all(char in _TTS_RANGE_SIGNS for char in lead)
            and is_tts_operand_char(prev_char)
        ):
            return " " + symbol[-1]
        if not is_tts_operand_char(prev_char):
            return symbol[-1]
        if lead and _TTS_EMOJI_RUN_RE.fullmatch(lead):
            return symbol[-1]
    return None


_TTS_SYMBOL_RUN_KEEP = 32


def tts_compact_symbol_run(run: str) -> str:
    """Shorten held symbols without changing how ``_kept_symbols`` judges them.

    Only the first character (a name "#") and whether the rest is all emoji
    matter, so a long run keeps its first character plus one representative
    of the rest; plain truncation could drop the one "@" that decides it.
    """
    if len(run) <= _TTS_SYMBOL_RUN_KEEP:
        return run
    rest = run[1:]
    sample = "\u2605" if _TTS_EMOJI_RUN_RE.fullmatch(rest) else "@"
    return run[0] + sample


def _trailing_symbol_run(text: str):
    run = None
    for run in _TTS_MUTED_SYMBOL_RE.finditer(text or ""):
        pass
    return run if run is not None and run.end() == len(text) else None


def tts_chunk_trailing_minus(text: str, before: str = "") -> str:
    """The minus sign a streamed chunk ends with, or ``""``.

    It is a minus if it would be kept before a digit (see ``_kept_symbols``);
    ``before`` is the last character spoken before this chunk, for a run that
    starts the chunk. The caller re-attaches it when the next chunk starts
    with a digit ("x = -" + "5"); the filter itself cannot see that far.
    """
    run = _trailing_symbol_run(text)
    if run is None or run.group(0)[-1] not in _TTS_MINUS_SIGNS:
        return ""
    context = (before or "") + text[: run.start()]
    kept = _kept_symbols(run.group(0), context[-1:], "0", context[-2:-1])
    return run.group(0)[-1] if kept and kept[-1] in _TTS_MINUS_SIGNS else ""


def tts_chunk_trailing_name_hash(text: str, before: str = "") -> str:
    """The "#" a streamed chunk ends on that may belong to a name like C#.

    That is a run of removable symbols reaching the end of the chunk, starting
    with "#" right after an ASCII letter (``before`` stands in for the letter
    when the run starts the chunk: "C" + "#"). Whether it is a name depends on
    what follows, which only the next chunk shows ("C#" + " dev" keeps it,
    "a#" + "b" does not), so the caller holds it until then.
    """
    run = _trailing_symbol_run(text)
    if run is None or run.group(0)[0] not in _TTS_HASH_SIGNS:
        return ""
    context = (before or "") + text[: run.start()]
    if _is_name_hash_context(context[-1:], context[-2:-1]):
        return run.group(0)[0]
    return ""


def tts_first_unmuted_char(text: str) -> str:
    """The first character of ``text`` that the symbol filter keeps, or ``""``."""
    run = _TTS_MUTED_SYMBOL_RE.match(text or "")
    index = run.end() if run is not None else 0
    return text[index:index + 1] if text else ""


def _muted_symbol_replacement(match, before: str = "") -> str:
    # Replace symbols between letters / digits with spaces rather than deleting directly:
    # deleting separators in "3~5", "9/28", "well-known", "по-русски" yields "35", "928", "wellknown", "порусски",
    # pronounced as different numbers / words. Do not add spaces in scripts such as Han and Kana that do not delimit words with spaces.
    # Streaming may split exactly at a symbol, so treat chunk boundaries as having a letter/digit on the other side.
    text = match.string
    start, end = match.span()
    symbol = match.group(0)
    context = (before or "") + text[:start]
    prev_char = context[-1:]
    next_char = text[end] if end < len(text) else ""
    # Retain # in C# and minus signs ("C#", "-5℃", "🌡️-5°C"); see _kept_symbols.
    kept = _kept_symbols(symbol, prev_char, next_char, context[-2:-1])
    if kept is not None:
        return kept
    if symbol in _TTS_COMPARISON_SIGNS:
        # "3<5", "a > b", "=>": < > between operands or adjacent to equals signs are comparison operators.
        left = context.rstrip(" ")[-1:]
        right = text[end:].lstrip(" ")[:1]
        if (
            prev_char in _TTS_EQUALS_SIGNS
            or next_char in _TTS_EQUALS_SIGNS
            or (left.isalnum() and right.isalnum())
        ):
            return symbol
    prev_ok = is_tts_operand_char(prev_char) if prev_char else True
    next_ok = is_tts_word_char(next_char) if next_char else True
    if prev_ok and next_ok and (is_tts_operand_char(prev_char) or is_tts_word_char(next_char)):
        return " "
    return ""


def strip_tts_muted_symbols(text: str, before: str = "") -> str:
    """Remove symbols a TTS engine would read aloud, keeping prose punctuation.

    Letters, digits, whitespace and sentence punctuation survive, as do
    currency signs, common arithmetic operators, temperature / degree / CJK
    units, a minus sign before a number and the "#" of names like C#. A symbol between two letters/digits of a
    space-delimited script becomes a space so the neighbours are not read as
    one number or word. Safe for streaming chunks: the chunk's own
    leading/trailing whitespace is left alone. ``before`` holds the last
    characters spoken before a streamed chunk (two are enough), so a symbol
    at the chunk's start is judged as it would be unsplit ("3" + "-5" is a
    range, not a minus; " C" + "#" is a name).
    """
    if not text:
        return text
    text = _TTS_COMPARISON_DIGRAPH_RE.sub(
        lambda match: _TTS_COMPARISON_DIGRAPHS[match.group(0)[0]], text
    )
    cleaned = _TTS_MUTED_SYMBOL_RE.sub(
        lambda match: _muted_symbol_replacement(match, before), text
    )
    # Collapse consecutive spaces left by symbol removal without changing original spaces at chunk boundaries.
    return regex.sub(r" {2,}", " ", cleaned)

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
    Measure mixed text length: one per Chinese character and one per English word.
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
# 4. Return (text to process, remaining buffer).
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

    if len(utts) == 0: # No punctuation.
        if force_process:
            return text, ""
        else:
            return "", text
    elif calc_utt_length(utts[-1]) > token_min_n: # If the final utterance meets the length requirement.
        # print(f"💼Backend splitting: || {''.join(utts)} || {text[st:]}")
        return ''.join(utts), text[st:]
    elif len(utts)==1: # If it is too short but there are no other utterances.
        if force_process:
            return text, ""
        else:
            return "", text
    else:
        # print(f"💼Backend splitting: || {''.join(utts[:-1])} || {utts[-1] + text[st:]}")
        return ''.join(utts[:-1]), utts[-1] + text[st:]

# remove blank between chinese character
def replace_blank(text: str):
    """Retain spaces with non-space ASCII characters on both sides; remove all other ASCII spaces.

    Handles Gemini Live output transcripts inserting spaces within Chinese words
    or at Chinese/English boundaries, where spaces break Chinese speech in TTS.

    Boundary characters (i==0 or i==last) lack a neighbor on one side; always treat it as non-ASCII/space
    and discard, avoiding Python negative indexing or IndexError.
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


# "Glue-to-adjacent" character ranges: ASCII spaces in these scripts are almost certainly tokenizer
# artifacts (as Gemini Live splits Chinese tokens), rather than semantic word separators.
# Intentionally exclude Hangul (Korean separates words with spaces), Cyrillic / Arabic / Thai / Devanagari, etc.
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
    """Remove ASCII spaces, including consecutive runs, adjacent to CJK Han / Japanese Kana on at least one side.

    Targets artifacts from realtime backends such as Gemini Live splitting Chinese tokens
    ("你 好 世 界") in output transcripts. Compared with :func:`replace_blank`:

    - That function retains spaces only between non-space ASCII characters, damaging Korean /
      Cyrillic / Arabic / Thai scripts that are non-ASCII but separate words with spaces
      ("안녕하세요 여러분" -> "안녕하세요여러분").
    - This function removes spaces only next to CJK Han/Kana, preserving all others.

    Neighbor detection **skips consecutive spaces** to the nearest non-space character, ensuring
    all three spaces in ``"你好   世界"`` are removed rather than only the outer two.
    """  # noqa: DOCSTRING_CJK
    n = len(text)
    out = []
    for i, c in enumerate(text):
        if c == " ":
            # Skip consecutive spaces leftward to find the nearest non-space character.
            j = i - 1
            while j >= 0 and text[j] == " ":
                j -= 1
            left = text[j] if j >= 0 else ""
            # Skip consecutive spaces rightward to find the nearest non-space character.
            j = i + 1
            while j < n and text[j] == " ":
                j += 1
            right = text[j] if j < n else ""
            if _is_cjk_glue_char(left) or _is_cjk_glue_char(right):
                continue
        out.append(c)
    return "".join(out)


class TtsStreamNormalizer:
    """TTS text normalizer safe across chunk boundaries.

    Realtime backends such as Gemini Live insert ASCII spaces between Chinese tokens
    ("你 好 世 界") in output transcripts; streaming TTS such as MiniMax / CosyVoice
    reads these separated characters as choppy fragments. This normalizer uses
    :func:`drop_cjk_boundary_spaces` to remove CJK-adjacent ASCII spaces while
    handling two crucial streaming cases:

    1. **Defer trailing-space decisions**: buffer trailing ASCII spaces until the next chunk
       arrives, then decide retention using its following character.
    2. **Carry left context across chunks**: use the last emitted non-space character
       as the next chunk's leading space's left neighbor, avoiding boundary misclassification.

    Deliberately leave spaces in non-CJK scripts (Korean / Cyrillic / Arabic / Thai, etc.) untouched:
    they delimit words with ASCII spaces, and removal breaks TTS. Call :meth:`reset` for every new TTS turn
    (speech_id change).
    """  # noqa: DOCSTRING_CJK

    __slots__ = ("_last_nonspace", "_pending_spaces")

    def __init__(self):
        self._last_nonspace = ""
        self._pending_spaces = ""

    def reset(self) -> None:
        """Clear state. Call on a new speech_id or interruption."""
        self._last_nonspace = ""
        self._pending_spaces = ""

    def feed(self, chunk: str) -> str:
        """Feed a new chunk and return normalized text currently safe to emit."""
        if not chunk:
            return ""

        work = self._pending_spaces + chunk

        # Buffer trailing ASCII spaces; the next chunk's first character determines whether to retain them.
        stripped = work.rstrip(" ")
        self._pending_spaces = work[len(stripped):]
        if not stripped:
            return ""

        # Use the last emitted non-space character as the left neighbor; being non-space ensures
        # drop_cjk_boundary_spaces retains the prefix itself, allowing exact removal by length.
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
        """Finish the turn: discard pending trailing spaces and clear state."""
        self._pending_spaces = ""
        self._last_nonspace = ""
        return ""


class TtsBracketStripper:
    """TTS bracket stripper safe across chunk boundaries.

    **Do not speak entire bracketed segments**, including brackets (common half/fullwidth types,
    nested brackets included, except title brackets ``《》``). Streaming ``她（笑）说`` may split into ``她（``, ``笑``,
    and ``）说``; this class maintains nesting with ``depth`` across chunk
    boundaries.

    Call :meth:`reset` on each new TTS turn (``speech_id`` change), or an
    unmatched opener from the previous turn can silence the beginning of the new turn.
    Call :meth:`flush` once before turn end to zero remaining unclosed bracket depth
    (omitting a partial ``（thinking...`` is safer than speaking it).

    Decoupled from Markdown stripping: retaining content while removing ``[`` ``]`` belongs to
    :class:`TtsMarkdownStripper` (Markdown links ``[文本](URL)``
    must retain ``文本``). This class treats ``[`` ``]`` as brackets whose entire content is silent, so
    ``TtsMarkdownStripper`` must precede it, converting links etc. to plain text before this
    class; otherwise link text disappears.
    """  # noqa: DOCSTRING_CJK

    # Halfwidth / fullwidth / quotation brackets map to paired closers. Intentionally omit `{` `｛`
    # `}` `｝` (useful to retain for spoken programming contexts), as well as title brackets
    # `《` `》` (book/song titles should reach TTS).
    _PAIRS = {
        "(": ")",
        "（": "）",
        "[": "]",
        "［": "］",
        "【": "】",
        "〈": "〉",
        "〔": "〕",
        "「": "」",
        "『": "』",
    }
    _OPEN = frozenset(_PAIRS.keys())
    _CLOSE = frozenset(_PAIRS.values())

    __slots__ = ("_stack",)

    def __init__(self):
        self._stack = []

    def reset(self) -> None:
        """Clear the opener stack. Call on a new speech_id or interruption."""
        self._stack.clear()

    def feed(self, chunk: str) -> str:
        """Scan character by character and return currently emittable text (empty while the opener stack is nonempty).

        The opener stack replaces simple depth counting with type-pair validation: pop only
        when a closer matches the top opener; otherwise emit literally at depth 0
        or discard with bracketed content at depth > 0. Thus ``]`` in ``（旁白]继续``
        cannot incorrectly close ``（`` and prematurely end bracketed state.
        """  # noqa: DOCSTRING_CJK
        if not chunk:
            return ""
        out = []
        stack = self._stack
        for c in chunk:
            if c in self._OPEN:
                stack.append(self._PAIRS[c])
            elif c in self._CLOSE:
                if stack and stack[-1] == c:
                    stack.pop()
                elif not stack:
                    # Emit unmatched closing brackets as ordinary punctuation, avoiding loss of
                    # the ) in mathematical/list notation such as ``50)``.
                    out.append(c)
                # else: inside brackets but mismatching the top opener (``（旁白]``),
                # discard as a punctuation character within the bracketed context.
            elif not stack:
                out.append(c)
            # else: inside brackets; discard entirely.
        return "".join(out)

    def flush(self) -> str:
        """Finish the turn: clear the stack and return ``""``.

        Unclosed bracket content was already discarded during feed; only reset state here.
        """
        self._stack.clear()
        return ""


class TtsMarkdownStripper:
    """Best-effort Markdown stripper safe across chunk boundaries.

    Remove markup and retain content: ``**X**`` -> ``X``, ``[X](url)`` -> ``X``, etc.
    Covers these patterns in priority order:

    1. Triple-backtick fence ``` ```lang\\nbody``` ``` -> discard entirely (speaking code is pointless)
    2. Inline fence ``` ``X`` ``` -> ``X``
    3. Image ``![alt](url)`` -> discard entirely, including alt
    4. Link ``[text](url)`` -> ``text``
    5. ``**X**`` / ``__X__`` -> ``X``
    6. ``*X*`` / ``_X_`` -> ``X`` (``_`` strictly requires non-alphanumeric neighbors, avoiding variable names)
    7. ``~~X~~`` -> ``X``
    8. Line-leading ``#``/``##``/...``/``>``/``-``/``*``/``\\d+.`` list/heading/quote prefixes -> remove

    Streaming: ``_safe_split`` locates the earliest unpaired marker through buf's end;
    hold that segment pending the next chunk and emit the preceding segment. ``_MAX_PENDING``
    prevents indefinite buffering when the model never closes markup; force emit + reset above the limit to avoid memory growth.
    Call :meth:`flush` once before turn end, stripping pending content and removing remaining isolated
    marker characters (``*`` ``_`` ``~`` ``\\``` ``[`` ``]`` ``(`` ``)``) before emitting.

    Deliberately best-effort: nested emphasis and complex cross-fence cases are not guaranteed perfect; LLMs
    rarely produce them, and overengineering is not worthwhile. Never swallows user content (worst case: unstripped markup).
    """

    __slots__ = ("_pending",)

    # Pending byte limit prevents unbounded accumulation when the model never closes a marker; emit + reset on overflow.
    _MAX_PENDING = 256

    # Multicharacter markers must match before single characters, or ``**`` becomes two ``*`` markers.
    _MULTI_MARKERS = ("```", "**", "__", "~~")
    _SINGLE_MARKERS = ("*", "_", "~", "`")

    # Complete regex pattern used by _strip (order matters).
    _PATTERNS = (
        # Remove entire multiline fences, including language tags.
        (re.compile(r"```[^\n]*\n[\s\S]*?```"), ""),
        # Retain inline-fence content.
        (re.compile(r"```([^`\n]*?)```"), r"\1"),
        # Remove whole images, including alt text, to avoid reading alt-text aloud.
        (re.compile(r"!\[[^\]]*?\]\([^)]*?\)"), ""),
        # Retain link text and discard the URL.
        (re.compile(r"\[([^\]]+?)\]\([^)]*?\)"), r"\1"),
        # Process bold before italic, preventing ``**X**`` from becoming two ``*X*`` matches.
        (re.compile(r"\*\*([^*\n]+?)\*\*"), r"\1"),
        (re.compile(r"__([^_\n]+?)__"), r"\1"),
        # italic
        (re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)"), r"\1"),
        # ``_`` must have non-alphanumeric neighbors on both sides, avoiding variable names such as ``foo_bar``.
        (re.compile(r"(?<![A-Za-z0-9_])_([^_\n]+?)_(?![A-Za-z0-9_])"), r"\1"),
        # strikethrough
        (re.compile(r"~~([^~\n]+?)~~"), r"\1"),
        # inline code
        (re.compile(r"`+([^`\n]+?)`+"), r"\1"),
        # Line-leading markers (heading / blockquote / list).
        (re.compile(r"(?m)^[ \t]*(?:#{1,6}|>+|[-*+]|\d+\.)[ \t]+"), ""),
    )

    # Flush fallback: remove leftover isolated marker characters.
    _DANGLING_RE = re.compile(r"[*_~`\[\]()]+")
    # Remove bracket characters only: leftover brackets would make TtsBracketStripper treat them as openers and swallow following text.
    _DANGLING_BRACKET_RE = re.compile(r"[\[\]()]+")

    def __init__(self):
        self._pending = ""

    def reset(self) -> None:
        """Clear pending. Call on a new speech_id or interruption."""
        self._pending = ""

    def feed(self, chunk: str) -> str:
        """Feed a new chunk and return stripped text currently safe to emit."""
        if not chunk:
            return ""
        buf = self._pending + chunk

        # Pending-capacity fallback: force emit + reset if the model never closes the marker, avoiding memory growth.
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

    def flush(self, *, keep_symbol_markers: bool = False) -> str:
        """Turn end: strip the pending text, drop dangling markers, emit it.

        With ``keep_symbol_markers=True`` only bracket markers are dropped;
        dangling ``*``, ``_``, ``~`` and backticks are left for the downstream
        ``strip_tts_muted_symbols``, which knows how the previous chunk ended.
        """
        # "3", "~5": deleting ~ here joins them as "35"; leave it to symbol filtering to insert a space.
        if not self._pending:
            return ""
        out = self._strip(self._pending)
        dangling = self._DANGLING_BRACKET_RE if keep_symbol_markers else self._DANGLING_RE
        out = dangling.sub("", out)
        self._pending = ""
        return out

    @classmethod
    def _safe_split(cls, buf: str) -> int:
        """Find the start of the segment from the earliest unpaired marker to buf's end.

        The preceding segment is confirmed immune to future chunks and can be emitted directly;
        the segment from this position to buf's end remains pending until more chunks determine stripping.
        """
        split = len(buf)
        work = list(buf)

        # Multicharacter markers: check parity and black out pairs to prevent recounting individual characters.
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
                # Hold from the final unpaired marker.
                split = min(split, positions[-1])
                paired = positions[:-1]
            else:
                paired = positions
            for p in paired:
                for k in range(mlen):
                    if 0 <= p + k < len(work):
                        work[p + k] = "\0"

        # Single-character markers (view with paired multicharacter markers blacked out).
        work_str = "".join(work)
        for marker in cls._SINGLE_MARKERS:
            if marker == "_":
                # ``_`` immediately between ASCII alphanumeric characters belongs to an identifier (``foo_bar``), not italic markup.
                # Boundary detection must exactly match _strip's italic-underscore regex:
                # it uses ``[A-Za-z0-9_]`` (ASCII-only), so CJK / Cyrillic and other
                # Unicode letters do not count as word boundaries. Switching here to
                # ``str.isalnum()`` (including CJK) would misclassify the opening _ in ``你_好_``
                # as an identifier during splitting and skip it; after emitting the whole segment, _strip
                # would recognize markup again, leaving inconsistent semantics and unstripped emphasis.
                positions = []
                for i, c in enumerate(work_str):
                    if c != "_":
                        continue
                    left_alnum = i > 0 and cls._is_ascii_word_char(work_str[i - 1])
                    right_alnum = (
                        i + 1 < len(work_str)
                        and cls._is_ascii_word_char(work_str[i + 1])
                    )
                    if left_alnum and right_alnum:
                        continue  # Underscores inside identifiers are not markers.
                    positions.append(i)
            else:
                positions = [i for i, c in enumerate(work_str) if c == marker]
            if len(positions) % 2:
                split = min(split, positions[-1])

        # ``[ ... ]`` link detection: hold pending from the earliest [ not yet confirmed to be a non-link.
        # A non-( character after ] is required to confirm a non-link; otherwise
        # chunk1 = ``...[docs]`` / chunk2 = ``(url)`` lets the previous chunk
        # emit [docs] prematurely to the downstream bracket stripper, which swallows the whole ordinary
        # bracketed segment, preventing docs from ever being spoken. Also hold when ]( exists but ) has not
        # arrived, as the link URL is incomplete.
        positions = []
        i = 0
        while i < len(work_str):
            c = work_str[i]
            if c == "[":
                positions.append(i)
                i += 1
            elif c == "]" and positions:
                if i + 1 >= len(work_str):
                    break  # ``]`` ends buf; the next chunk may start with ``(``, so hold.
                if work_str[i + 1] == "(":
                    close = work_str.find(")", i + 2)
                    if close < 0:
                        break  # ``](url`` is not closed yet: hold from ``[``.
                    positions.pop()
                    i = close + 1
                else:
                    # ``]X`` with X != ``(``: confirmed non-link, settled.
                    positions.pop()
                    i += 1
            else:
                i += 1
        if positions:
            split = min(split, positions[0])

        return max(0, split)

    @classmethod
    def _strip(cls, text: str) -> str:
        for pat, repl in cls._PATTERNS:
            text = pat.sub(repl, text)
        return text

    @staticmethod
    def _is_ascii_word_char(c: str) -> bool:
        """Test ASCII ``[A-Za-z0-9_]`` characters, deliberately excluding CJK / other Unicode letters.

        Must match the semantics of ``[A-Za-z0-9_]`` in _PATTERNS' italic-underscore regex;
        do not replace with ``str.isalnum()``, which includes CJK / Cyrillic, etc.
        """
        return ("a" <= c <= "z") or ("A" <= c <= "Z") or ("0" <= c <= "9") or c == "_"


def is_only_punctuation(text):
    # Regular expression: Match strings that consist only of punctuation marks or are empty.
    punctuation_pattern = r'^[\p{P}\p{S}]*$'
    return bool(regex.fullmatch(punctuation_pattern, text))


def calculate_text_similarity(text1: str, text2: str) -> float:
    """
    Calculate text similarity using character-trigram Jaccard similarity.
    Return a value between 0.0 and 1.0.
    """
    if not text1 or not text2:
        return 0.0
    
    # Generate character-level trigrams.
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
    Recursively scan 'static', 'live2d' under user Documents, Steam Workshop directories, and user mod paths
    for subdirectories containing '.model3.json' files.
    """
    from utils.config_manager import get_config_manager
    
    found_models = []
    search_dirs = []
    
    # Add the static directory.
    static_dir = 'static'
    if os.path.exists(static_dir):
        search_dirs.append(('static', static_dir, '/static'))
    else:
        logging.warning(f"警告：static文件夹路径不存在: {static_dir}")
    
    # Add the live2d folder under the user's Documents directory.
    # CFA (Controlled Folder Access) awareness: if original Documents is readable but not writable,
    # read models from the original path (/user_live2d), using the writable fallback as an auxiliary (/user_live2d_local).
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
                # Writable fallback directory for CFA scenarios (preferred).
                search_dirs.append(('documents_local', live2d_root_str, '/user_live2d_local'))
            elif readable_live2d_str and _norm(live2d_root_str) == _norm(readable_live2d_str):
                # Read-only original directory for CFA scenarios (fallback).
                search_dirs.append(('documents_legacy', live2d_root_str, '/user_live2d'))
            else:
                search_dirs.append(('documents', live2d_root_str, '/user_live2d'))
    except Exception as e:
        logging.warning(f"无法访问用户文档live2d目录: {e}")
    
    # Add the Steam Workshop directory.
    workshop_search_dir = _resolve_workshop_search_dir()
    if workshop_search_dir and os.path.exists(workshop_search_dir):
        search_dirs.append(('workshop', workshop_search_dir, '/workshop'))
    
    # Traverse all search directories.
    for source, search_root_dir, url_prefix in search_dirs:
        try:
            # os.walk traverses all directories and files under the specified root.
            for root, dirs, files in os.walk(search_root_dir):
                for file in files:
                    if file.endswith('.model3.json'):
                        # Get the model name from its containing folder for clarity.
                        folder_name = os.path.basename(root)
                        
                        # Use the folder name as both model name and display name.
                        display_name = folder_name
                        model_name = folder_name
                        
                        # Build a browser-accessible URL path.
                        # 1. Calculate the path relative to search_root_dir.
                        relative_path = os.path.relpath(os.path.join(root, file), search_root_dir)
                        # 2. Replace local path separators (e.g. '\\') with URL separators ('/').
                        model_path = relative_path.replace(os.path.sep, '/')
                        
                        # Add a source suffix to distinguish an existing model name.
                        existing_names = [m["name"] for m in found_models]
                        final_name = model_name
                        if model_name in existing_names:
                            final_name = f"{model_name}_{source}"
                            # If the suffixed name still duplicates another, add a numeric suffix.
                            counter = 1
                            while final_name in existing_names:
                                final_name = f"{model_name}_{source}_{counter}"
                                counter += 1
                            # Update display_name too for distinction.
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
                        
                        # Optimization: after finding model JSON in a directory, do not descend further into its subdirectories.
                        dirs[:] = []
                        break
        except Exception as e:
            logging.error(f"搜索目录 {search_root_dir} 时出错: {e}")
                
    return found_models

def _is_within(base: str, target: str) -> bool:
    """
    Check whether target is inside base, preventing path traversal.
    
    On Windows, os.path.commonpath raises ValueError when base and target reside on different drives.
    Catch it and return False to handle cross-drive cases safely.
    
    Args:
        base: Base path (directory)
        target: Target path to check
        
    Returns:
        True if target is inside base, otherwise False (including different drives)
    """
    try:
        return os.path.commonpath([target, base]) == base
    except ValueError:
        # Different drives or other incomparable cases.
        return False


def is_user_imported_model(model_path: str, config_manager=None) -> bool:
    """
    Check whether a model path is under the user-imported model directory.
    
    Verify that the model is user-imported rather than a system/Workshop model,
    for permission checks on deletion, configuration saving, etc.
    
    Args:
        model_path: Model directory path (string)
        config_manager: Configuration manager instance; obtain from get_config_manager() if None
        
    Returns:
        True if inside the user-import directory, otherwise False (including exceptions)
    """
    try:
        if config_manager is None:
            from utils.config_manager import get_config_manager
            config_manager = get_config_manager()
        
        config_manager.ensure_live2d_directory()
        user_live2d_dir = os.path.realpath(str(config_manager.live2d_dir))
        model_path_real = os.path.realpath(model_path)
        
        # Safely check paths with _is_within, handling different drives.
        return _is_within(user_live2d_dir, model_path_real)
    except Exception:
        # Any exception returns False: not a user-imported model.
        return False


def _resolve_workshop_search_dir() -> str:
    """
    Get Workshop search directories.
    
    Priority: user_mod_folder (configuration) > Steam runtime path > user_workshop_folder (cache file) > default_workshop_folder (configuration) > default workshop directory
    """
    from utils.config_manager import get_workshop_path
    workshop_path = get_workshop_path()
    if workshop_path and os.path.exists(workshop_path):
        return workshop_path
    return None


def find_model_directory(model_name: str):
    """
    Find a model directory, preferring user Documents, then Workshop, then static.
    Return an (actual path, URL prefix) tuple.
    """
    from utils.config_manager import get_config_manager
    
    # Validate model names to prevent path traversal.
    # Allow common characters: letters, digits, underscores, CJK characters, hyphens, spaces, half/fullwidth parentheses, dots, commas, etc.
    # Reject path separators / \ and path traversal ..
    if not model_name or not model_name.strip():
        logging.warning("模型名称为空")
        return (None, None)
    if '..' in model_name or '/' in model_name or '\\' in model_name:
        model_name_safe = repr(model_name) if len(model_name) <= 100 else repr(model_name[:100]) + '...'
        logging.warning(f"模型名称包含非法路径字符: {model_name_safe}")
        return (None, None)
    
    WORKSHOP_SEARCH_DIR = _resolve_workshop_search_dir()
    
    # Define the allowed base directories.
    allowed_base_dirs = []

    # Get the readable live2d directory for CFA scenarios (possibly None).
    readable_live2d = None
    try:
        config_mgr = get_config_manager()
        readable_live2d = config_mgr.readable_live2d_dir
    except Exception:
        pass

    # Live2D lookup: prefer the writable runtime directory, falling back to the read-only legacy directory.
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

    # Then try Workshop directories.
    try:
        if WORKSHOP_SEARCH_DIR and os.path.exists(WORKSHOP_SEARCH_DIR):
            workshop_search_real = os.path.realpath(WORKSHOP_SEARCH_DIR)
            # Direct match when model name equals folder name.
            workshop_model_dir = os.path.join(WORKSHOP_SEARCH_DIR, model_name)
            if os.path.exists(workshop_model_dir):
                workshop_model_dir_real = os.path.realpath(workshop_model_dir)
                if os.path.commonpath([workshop_model_dir_real, workshop_search_real]) == workshop_search_real:
                    return (workshop_model_dir, '/workshop')
            
            # Recursively search all Workshop subfolders, handling Steam Workshop's item-ID folder names.
            for item_id in os.listdir(WORKSHOP_SEARCH_DIR):
                item_path = os.path.join(WORKSHOP_SEARCH_DIR, item_id)
                item_path_real = os.path.realpath(item_path)
                if os.path.isdir(item_path_real):
                    # Check whether a subfolder contains a folder matching the model name.
                    potential_model_path = os.path.join(item_path, model_name)
                    if os.path.exists(potential_model_path):
                        potential_model_path_real = os.path.realpath(potential_model_path)
                        if os.path.commonpath([potential_model_path_real, workshop_search_real]) == workshop_search_real:
                            return (potential_model_path, '/workshop')
                    
                    # Check whether the subfolder itself is a model directory containing .model3.json.
                    for file in os.listdir(item_path):
                        if file.endswith('.model3.json'):
                            # Extract the model name without its suffix.
                            potential_model_name = os.path.splitext(os.path.splitext(file)[0])[0]
                            if potential_model_name == model_name:
                                if os.path.commonpath([item_path_real, workshop_search_real]) == workshop_search_real:
                                    return (item_path, '/workshop')
    except Exception as e:
        logging.warning(f"检查创意工坊目录模型时出错: {e}")
    
    # Then try the user mod path.
    try:
        config_mgr = get_config_manager()
        user_mods_path = config_mgr.get_workshop_path()
        if user_mods_path and os.path.exists(user_mods_path):
            user_mods_path_real = os.path.realpath(user_mods_path)
            # Direct match when model name equals folder name.
            user_mod_model_dir = os.path.join(user_mods_path, model_name)
            if os.path.exists(user_mod_model_dir):
                user_mod_model_dir_real = os.path.realpath(user_mod_model_dir)
                if os.path.commonpath([user_mod_model_dir_real, user_mods_path_real]) == user_mods_path_real:
                    return (user_mod_model_dir, '/user_mods')
            
            # Recursively search all subfolders in the user mod directory.
            for mod_folder in os.listdir(user_mods_path):
                mod_path = os.path.join(user_mods_path, mod_folder)
                mod_path_real = os.path.realpath(mod_path)
                if os.path.isdir(mod_path_real):
                    # Check whether a subfolder contains a folder matching the model name.
                    potential_model_path = os.path.join(mod_path, model_name)
                    if os.path.exists(potential_model_path):
                        potential_model_path_real = os.path.realpath(potential_model_path)
                        if os.path.commonpath([potential_model_path_real, user_mods_path_real]) == user_mods_path_real:
                            return (potential_model_path, '/user_mods')
                    
                    # Check whether the subfolder itself is a model directory containing .model3.json.
                    for file in os.listdir(mod_path):
                        if file.endswith('.model3.json'):
                            # Extract the model name without its suffix.
                            potential_model_name = os.path.splitext(os.path.splitext(file)[0])[0]
                            if potential_model_name == model_name:
                                if os.path.commonpath([mod_path_real, user_mods_path_real]) == user_mods_path_real:
                                    return (mod_path, '/user_mods')
    except Exception as e:
        logging.warning(f"检查用户mod目录模型时出错: {e}")
    
    # Finally try the static directory.
    static_dir = 'static'
    static_dir_real = os.path.realpath(static_dir)
    static_model_dir = os.path.join(static_dir, model_name)
    if os.path.exists(static_model_dir):
        static_model_dir_real = os.path.realpath(static_model_dir)
        if os.path.commonpath([static_model_dir_real, static_dir_real]) == static_dir_real:
            return (static_model_dir, '/static')
    
    # Return None if none exists.
    return (None, None)

def find_workshop_item_by_id(item_id: str) -> tuple:
    """
    Find a Steam Workshop item folder by item ID.
    
    Args:
        item_id: Steam Workshop item ID
        
    Returns:
        (item path, URL prefix) tuple; return defaults even if not found
    """
    try:
        workshop_dir = _resolve_workshop_search_dir()
        
        # Use the default static directory if the path is empty or absent.
        if not workshop_dir or not os.path.exists(workshop_dir):
            logging.warning(f"创意工坊目录不存在或无效: {workshop_dir}，使用默认路径")
            default_path = os.path.join("static", item_id)
            return (default_path, '/static')
        
        # Search directly using the item ID as folder name.
        item_path = os.path.join(workshop_dir, item_id)
        if os.path.isdir(item_path):
            # Check for a .model3.json file.
            has_model_file = any(file.endswith('.model3.json') for file in os.listdir(item_path))
            if has_model_file:
                return (item_path, '/workshop')
            
            # Check subfolders for model files.
            for subdir in os.listdir(item_path):
                subdir_path = os.path.join(item_path, subdir)
                if os.path.isdir(subdir_path):
                    # Check subfolders for model files.
                    if any(file.endswith('.model3.json') for file in os.listdir(subdir_path)):
                        return (item_path, '/workshop')
        
        # Return the default path if no matching folder is found.
        default_path = os.path.join(workshop_dir, item_id)
        return (default_path, '/workshop')
    except Exception as e:
        logging.error(f"查找创意工坊物品ID {item_id} 时出错: {e}")
        # Return the default path on error.
        default_path = os.path.join("static", item_id)
        return (default_path, '/static')


def find_model_by_workshop_item_id(item_id: str) -> str:
    """
    Find a model configuration file URL by item ID.
    
    Args:
        item_id: Steam Workshop item ID
        
    Returns:
        Model configuration URL path, or None if not found
    """
    try:
        # Find the item folder through find_workshop_item_by_id.
        item_result = find_workshop_item_by_id(item_id)
        if not item_result:
            logging.warning(f"未找到创意工坊物品ID: {item_id}")
            return None
        
        model_dir, url_prefix = item_result
        
        # Find the .model3.json file
        model_files = []
        for root, _, files in os.walk(model_dir):
            for file in files:
                if file.endswith('.model3.json'):
                    # Calculate the relative path.
                    relative_path = os.path.relpath(os.path.join(root, file), model_dir)
                    model_files.append(os.path.normpath(relative_path).replace('\\', '/'))
        
        if model_files:
            # Prefer a model file with the same name as the folder.
            folder_name = os.path.basename(model_dir)
            for model_file in model_files:
                if model_file.endswith(f"{folder_name}.model3.json"):
                    return f"{url_prefix}/{item_id}/{model_file}"
            # Otherwise return the first model file found.
            return f"{url_prefix}/{item_id}/{model_files[0]}"
        
        logging.warning(f"创意工坊物品 {item_id} 中未找到模型配置文件")
        return None
    except Exception as e:
        logging.error(f"根据创意工坊物品ID {item_id} 查找模型时出错: {e}")
        return None


def find_model_config_file(model_name: str) -> str:
    """
    Find a .model3.json configuration file in a model directory.
    Return an accessible URL path.
    """
    model_dir, url_prefix = find_model_directory(model_name)
    
    if not model_dir or not os.path.exists(model_dir):
        # If no model directory is found, return None or an empty string rather than a default path.
        return None
    
    # Find the .model3.json file
    for file in os.listdir(model_dir):
        if file.endswith('.model3.json'):
            return f"{url_prefix}/{model_name}/{file}"
    
    # Return the default path if nothing is found.
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
