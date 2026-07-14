#!/usr/bin/env python3
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

"""Mine deterministic natural-expression candidates from explicit JSONL input.

This is a maintainer-only offline analysis tool. It never discovers input files,
calls a model or network service, edits the runtime rule table, or activates a
candidate. Its output is a review artifact whose schema is intentionally
incompatible with ``config.prompts.prompts_slop.SLOP_RULES``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence, TypeVar

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA_VERSION = "natural-expression-candidates/v1"
ARTIFACT_TYPE = "maintainer_review_candidates"
DEFAULT_THRESHOLD = 3
DEFAULT_WORD_NGRAM_MIN = 2
DEFAULT_WORD_NGRAM_MAX = 5
DEFAULT_CJK_NGRAM_MIN = 4
DEFAULT_CJK_NGRAM_MAX = 8
DEFAULT_MIN_LENGTH = 4

_LANGUAGE_ALIASES = {
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "es": "es",
    "es-es": "es",
    "es-mx": "es",
    "pt": "pt",
    "pt-br": "pt",
    "pt-pt": "pt",
    "ru": "ru",
    "ru-ru": "ru",
    "ja": "ja",
    "ja-jp": "ja",
    "ko": "ko",
    "ko-kr": "ko",
    "zh": "zh",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
    "zh-tw": "zh-TW",
    "zh-hant": "zh-TW",
}
_WHITESPACE_LANGUAGES = frozenset({"en", "es", "pt", "ru"})
_TEXT_BOUNDARY_RE = re.compile(r"[\r\n.!?。！？；;:：,，、]+")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>()]+", re.IGNORECASE)
_TEMPLATE_RE = re.compile(
    r"\{\{[^{}\r\n]*\}\}|\$\{[^{}\r\n]*\}|<%[^%\r\n]*%>|"
    r"<[^<>\r\n]{1,80}>|\[[A-Z][A-Z0-9_-]{1,63}\]"
)


class CandidateMinerError(ValueError):
    """A safe, content-free error suitable for CLI output."""


@dataclass(frozen=True)
class MiningConfig:
    """Deterministic mining parameters recorded in the output artifact."""

    threshold: int = DEFAULT_THRESHOLD
    word_ngram_min: int = DEFAULT_WORD_NGRAM_MIN
    word_ngram_max: int = DEFAULT_WORD_NGRAM_MAX
    cjk_ngram_min: int = DEFAULT_CJK_NGRAM_MIN
    cjk_ngram_max: int = DEFAULT_CJK_NGRAM_MAX
    min_length: int = DEFAULT_MIN_LENGTH
    exclude_covered: bool = False

    def validate(self) -> None:
        for name in (
            "threshold",
            "word_ngram_min",
            "word_ngram_max",
            "cjk_ngram_min",
            "cjk_ngram_max",
            "min_length",
        ):
            if getattr(self, name) < 1:
                raise CandidateMinerError(f"{name} must be at least 1")
        if self.word_ngram_min > self.word_ngram_max:
            raise CandidateMinerError("word_ngram_min cannot exceed word_ngram_max")
        if self.cjk_ngram_min > self.cjk_ngram_max:
            raise CandidateMinerError("cjk_ngram_min cannot exceed cjk_ngram_max")


@dataclass(frozen=True)
class SourceMessage:
    """The only source data retained during mining."""

    language: str
    content: str
    source_line: int


@dataclass(frozen=True)
class _CandidateOccurrence:
    normalized: str
    phrase: str
    coverage_text: str
    start: int
    end: int


@dataclass
class _CandidateStats:
    occurrence_count: int
    source_lines: set[int]
    phrases: set[str]
    occurrences: list[_CandidateOccurrence]


def normalize_language(raw: str) -> str:
    """Normalize an explicit locale tag without guessing from message text."""
    if not isinstance(raw, str) or not raw.strip():
        raise CandidateMinerError("language must be a non-empty string")
    normalized = raw.strip().replace("_", "-").casefold()
    try:
        return _LANGUAGE_ALIASES[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(set(_LANGUAGE_ALIASES.values())))
        raise CandidateMinerError(
            f"unsupported language tag; supported languages: {supported}"
        ) from exc


def read_jsonl(
    input_path: Path,
    *,
    language_override: str | None = None,
) -> tuple[list[SourceMessage], int]:
    """Read the documented JSONL contract and retain assistant text only."""
    if not input_path.is_file():
        raise CandidateMinerError(f"input file does not exist: {input_path}")
    override = normalize_language(language_override) if language_override else None
    messages: list[SourceMessage] = []
    record_count = 0

    try:
        handle = input_path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise CandidateMinerError(f"unable to open input file: {input_path}") from exc

    with handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise CandidateMinerError(f"line {line_number}: invalid JSON") from exc
            if not isinstance(record, dict):
                raise CandidateMinerError(
                    f"line {line_number}: each JSONL record must be an object"
                )
            record_count += 1

            role = record.get("role")
            content = record.get("content")
            if not isinstance(role, str) or not role:
                raise CandidateMinerError(
                    f"line {line_number}: role must be a non-empty string"
                )
            if not isinstance(content, str):
                raise CandidateMinerError(
                    f"line {line_number}: content must be a string"
                )
            conversation_id = record.get("conversation_id")
            if conversation_id is not None and not isinstance(conversation_id, str):
                raise CandidateMinerError(
                    f"line {line_number}: conversation_id must be a string when present"
                )
            if role != "assistant":
                continue

            raw_language = override or record.get("lang")
            if raw_language is None:
                raise CandidateMinerError(
                    f"line {line_number}: assistant records require lang or --language"
                )
            language = override or normalize_language(raw_language)
            messages.append(
                SourceMessage(
                    language=language,
                    content=content,
                    source_line=line_number,
                )
            )

    return messages, record_count


def _fenced_code_spans(text: str) -> list[tuple[int, int]]:
    """Return Markdown fenced-code spans, including an unclosed final fence."""
    spans: list[tuple[int, int]] = []
    fence_start: int | None = None
    fence_char = ""
    fence_len = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip(" \t")
        indent = len(line) - len(stripped)
        if indent <= 3:
            if fence_start is None:
                opening = re.match(r"(`{3,}|~{3,})", stripped)
                if opening:
                    marker = opening.group(1)
                    fence_start = offset
                    fence_char = marker[0]
                    fence_len = len(marker)
            else:
                closing = re.match(
                    rf"{re.escape(fence_char)}{{{fence_len},}}[ \t]*(?:\r?\n)?\Z",
                    stripped,
                )
                if closing:
                    spans.append((fence_start, offset + len(line)))
                    fence_start = None
                    fence_char = ""
                    fence_len = 0
        offset += len(line)
    if fence_start is not None:
        spans.append((fence_start, len(text)))
    return spans


def _inline_code_spans(
    text: str,
    fenced_spans: Sequence[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Return same-line backtick spans outside fenced blocks."""
    spans: list[tuple[int, int]] = []
    index = 0
    fence_index = 0
    while index < len(text):
        while fence_index < len(fenced_spans) and fenced_spans[fence_index][1] <= index:
            fence_index += 1
        if (
            fence_index < len(fenced_spans)
            and fenced_spans[fence_index][0] <= index < fenced_spans[fence_index][1]
        ):
            index = fenced_spans[fence_index][1]
            continue
        if text[index] != "`":
            index += 1
            continue

        run_end = index + 1
        while run_end < len(text) and text[run_end] == "`":
            run_end += 1
        delimiter = text[index:run_end]
        newline = text.find("\n", run_end)
        line_end = len(text) if newline < 0 else newline
        closing = text.find(delimiter, run_end, line_end)
        end = line_end if closing < 0 else closing + len(delimiter)
        spans.append((index, end))
        index = max(end, run_end)
    return spans


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Return merged spans for code, URLs, and obvious template placeholders."""
    spans = _runtime_protected_spans(text)
    spans.extend(match.span() for match in _TEMPLATE_RE.finditer(text))
    return _merge_spans(spans)


def _runtime_protected_spans(text: str) -> list[tuple[int, int]]:
    """Mirror the runtime filter's fenced-code, inline-code, and URL spans."""
    fenced = _fenced_code_spans(text)
    spans = fenced + _inline_code_spans(text, fenced)
    spans.extend(match.span() for match in _URL_RE.finditer(text))
    return _merge_spans(spans)


def _merge_spans(spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []

    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        elif end > merged[-1][1]:
            merged[-1] = (merged[-1][0], end)
    return merged


def _unprotected_segments(text: str) -> Iterator[tuple[str, int]]:
    """Yield text and offsets outside protected spans without bridging them."""
    cursor = 0
    for start, end in _protected_spans(text):
        if cursor < start:
            yield text[cursor:start], cursor
        cursor = end
    if cursor < len(text):
        yield text[cursor:], cursor


def _text_segments(text: str, base_offset: int) -> Iterator[tuple[str, int]]:
    """Yield punctuation-bounded mining segments with original-text offsets."""
    cursor = 0
    for match in _TEXT_BOUNDARY_RE.finditer(text):
        if cursor < match.start():
            yield text[cursor : match.start()], base_offset + cursor
        cursor = match.end()
    if cursor < len(text):
        yield text[cursor:], base_offset + cursor


_T = TypeVar("_T")


def _bounded_ngrams(
    values: Sequence[_T],
    minimum: int,
    maximum: int,
) -> Iterator[tuple[_T, ...]]:
    upper = min(maximum, len(values))
    for size in range(minimum, upper + 1):
        for start in range(0, len(values) - size + 1):
            yield tuple(values[start : start + size])


def _is_meaningful(value: str, min_length: int) -> bool:
    compact = "".join(value.split())
    return len(compact) >= min_length and any(char.isalpha() for char in compact)


def _word_tokens(text: str) -> Iterator[tuple[str, int, int]]:
    """Yield NFKC-normalized word tokens with spans in the original text."""
    index = 0
    while index < len(text):
        if not text[index].isalnum() or text[index] == "_":
            index += 1
            continue
        start = index
        index += 1
        while index < len(text):
            char = text[index]
            if char.isalnum() and char != "_":
                index += 1
                continue
            if unicodedata.category(char).startswith("M"):
                index += 1
                continue
            if (
                char in {"'", "\u2019"}
                and index + 1 < len(text)
                and text[index + 1].isalnum()
                and text[index + 1] != "_"
            ):
                index += 1
                continue
            break
        yield unicodedata.normalize("NFKC", text[start:index]), start, index


def _word_candidates(
    text: str,
    config: MiningConfig,
) -> Iterator[_CandidateOccurrence]:
    for unprotected, unprotected_start in _unprotected_segments(text):
        for segment, segment_start in _text_segments(unprotected, unprotected_start):
            token_run: list[tuple[str, int, int]] = []
            for token, start, end in _word_tokens(segment):
                if not any(char.isalpha() for char in token):
                    yield from _word_run_candidates(
                        token_run,
                        config,
                        text,
                    )
                    token_run = []
                    continue
                token_run.append((token, segment_start + start, segment_start + end))
            yield from _word_run_candidates(token_run, config, text)


def _word_run_candidates(
    token_run: Sequence[tuple[str, int, int]],
    config: MiningConfig,
    coverage_text: str,
) -> Iterator[_CandidateOccurrence]:
    for gram in _bounded_ngrams(
        token_run,
        config.word_ngram_min,
        config.word_ngram_max,
    ):
        phrase = " ".join(token for token, _, _ in gram)
        normalized = " ".join(token.casefold() for token, _, _ in gram)
        if _is_meaningful(normalized, config.min_length):
            yield _CandidateOccurrence(
                normalized=normalized,
                phrase=phrase,
                coverage_text=coverage_text,
                start=gram[0][1],
                end=gram[-1][2],
            )


def _is_han(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x3134F
    )


def _is_japanese(char: str) -> bool:
    codepoint = ord(char)
    return (
        _is_han(char)
        or codepoint == 0x3005
        or 0x3031 <= codepoint <= 0x3035
        or codepoint == 0x303B
        or 0x3040 <= codepoint <= 0x30FF
        or 0x31F0 <= codepoint <= 0x31FF
        or 0xFF66 <= codepoint <= 0xFF9D
    )


def _is_hangul(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x1100 <= codepoint <= 0x11FF
        or 0x3130 <= codepoint <= 0x318F
        or 0xA960 <= codepoint <= 0xA97F
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xD7B0 <= codepoint <= 0xD7FF
    )


def _is_hangul_jamo(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x1100 <= codepoint <= 0x11FF
        or 0x3130 <= codepoint <= 0x318F
        or 0xA960 <= codepoint <= 0xA97F
        or 0xD7B0 <= codepoint <= 0xD7FF
    )


def _normalized_characters(text: str) -> Iterator[tuple[str, int, int]]:
    """Yield normalized characters mapped to their original source spans."""
    index = 0
    while index < len(text):
        start = index
        index += 1
        if _is_hangul_jamo(text[start]):
            while index < len(text) and _is_hangul_jamo(text[index]):
                index += 1
        while index < len(text) and (
            unicodedata.category(text[index]).startswith("M")
            or text[index] in {"\uff9e", "\uff9f"}
        ):
            index += 1
        normalized = unicodedata.normalize("NFKC", text[start:index])
        for char in normalized:
            yield char, start, index


def _script_runs(
    text: str,
    predicate,
) -> Iterator[list[tuple[str, int, int]]]:
    run: list[tuple[str, int, int]] = []
    for char, start, end in _normalized_characters(text):
        if predicate(char):
            run.append((char, start, end))
        elif run:
            yield run
            run = []
    if run:
        yield run


def _character_candidates(
    text: str,
    config: MiningConfig,
    predicate,
) -> Iterator[_CandidateOccurrence]:
    for unprotected, unprotected_start in _unprotected_segments(text):
        for segment, segment_start in _text_segments(unprotected, unprotected_start):
            for run in _script_runs(segment, predicate):
                upper = min(config.cjk_ngram_max, len(run))
                for size in range(config.cjk_ngram_min, upper + 1):
                    for start in range(0, len(run) - size + 1):
                        gram = run[start : start + size]
                        phrase = "".join(char for char, _, _ in gram)
                        normalized = phrase.casefold()
                        if _is_meaningful(normalized, config.min_length):
                            yield _CandidateOccurrence(
                                normalized=normalized,
                                phrase=phrase,
                                coverage_text=text,
                                start=segment_start + gram[0][1],
                                end=segment_start + gram[-1][2],
                            )


def _message_candidates(
    message: SourceMessage,
    config: MiningConfig,
) -> Iterator[_CandidateOccurrence]:
    language = message.language
    if language in _WHITESPACE_LANGUAGES:
        yield from _word_candidates(message.content, config)
        return
    if language.startswith("zh"):
        yield from _character_candidates(message.content, config, _is_han)
        return
    if language == "ja":
        yield from _character_candidates(message.content, config, _is_japanese)
        return
    if language == "ko":
        # Korean prose is normally space-delimited, but repeated compounds and
        # onomatopoeia often are not. Keep both families, but do not count an
        # identical single-token occurrence once in each strategy.
        word_candidates = list(_word_candidates(message.content, config))
        overlapping_word_counts = Counter(
            (
                candidate.normalized,
                candidate.coverage_text,
                candidate.start,
                candidate.end,
            )
            for candidate in word_candidates
        )
        yield from word_candidates
        for candidate in _character_candidates(message.content, config, _is_hangul):
            overlap_key = (
                candidate.normalized,
                candidate.coverage_text,
                candidate.start,
                candidate.end,
            )
            if overlapping_word_counts[overlap_key]:
                overlapping_word_counts[overlap_key] -= 1
                continue
            yield candidate
        return
    raise CandidateMinerError(f"unsupported normalized language: {language}")


def _coverage_language(language: str) -> str:
    return "zh" if language in {"zh", "zh-CN"} else language


def _coverage_result(
    language: str,
    occurrences: Sequence[_CandidateOccurrence],
    rules_by_language: Mapping[str, Sequence[Mapping[str, object]]],
) -> tuple[list[str], bool]:
    compiled_rules: list[tuple[str, re.Pattern[str]]] = []
    protected_cache: dict[str, list[tuple[int, int]]] = {}
    match_cache: dict[tuple[str, int], tuple[tuple[int, int], ...]] = {}
    covered: set[str] = set()
    for rule in rules_by_language.get(_coverage_language(language), ()):
        rule_id = rule.get("id")
        pattern = rule.get("find")
        flags = rule.get("flags", 0)
        if not isinstance(rule_id, str) or not isinstance(pattern, str):
            continue
        try:
            compiled = re.compile(pattern, int(flags))
        except (re.error, TypeError, ValueError) as exc:
            raise CandidateMinerError(
                f"existing rule {rule_id} has an invalid pattern"
            ) from exc
        compiled_rules.append((rule_id, compiled))

    all_occurrences_covered = bool(occurrences)
    for occurrence in occurrences:
        occurrence_covered = False
        protected = protected_cache.get(occurrence.coverage_text)
        if protected is None:
            protected = _runtime_protected_spans(occurrence.coverage_text)
            protected_cache[occurrence.coverage_text] = protected
        for rule_index, (rule_id, compiled) in enumerate(compiled_rules):
            cache_key = (occurrence.coverage_text, rule_index)
            match_spans = match_cache.get(cache_key)
            if match_spans is None:
                match_spans = tuple(
                    (match.start(), match.end())
                    for match in compiled.finditer(occurrence.coverage_text)
                    if match.start() != match.end()
                    and not any(
                        match.start() < protected_end and match.end() > protected_start
                        for protected_start, protected_end in protected
                    )
                )
                match_cache[cache_key] = match_spans
            if any(
                start <= occurrence.start and occurrence.end <= end
                for start, end in match_spans
            ):
                covered.add(rule_id)
                occurrence_covered = True
        if not occurrence_covered:
            all_occurrences_covered = False
    return sorted(covered), all_occurrences_covered


def load_current_rules() -> Mapping[str, Sequence[Mapping[str, object]]]:
    """Load the curated runtime table for read-only coverage analysis."""
    try:
        from config.prompts.prompts_slop import SLOP_RULES
    except Exception as exc:
        raise CandidateMinerError("unable to load current SLOP_RULES") from exc
    return SLOP_RULES


def build_report(
    messages: Sequence[SourceMessage],
    *,
    input_record_count: int,
    config: MiningConfig,
    rules_by_language: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
) -> dict[str, object]:
    """Build a deterministic, review-only candidate report."""
    config.validate()
    current_rules = (
        load_current_rules() if rules_by_language is None else rules_by_language
    )
    stats: dict[tuple[str, str], _CandidateStats] = {}

    for message in messages:
        for occurrence in _message_candidates(message, config):
            key = (message.language, occurrence.normalized)
            candidate_stats = stats.get(key)
            if candidate_stats is None:
                candidate_stats = _CandidateStats(0, set(), set(), [])
                stats[key] = candidate_stats
            candidate_stats.occurrence_count += 1
            candidate_stats.source_lines.add(message.source_line)
            candidate_stats.phrases.add(occurrence.phrase)
            candidate_stats.occurrences.append(occurrence)

    candidates: list[dict[str, object]] = []
    for (language, normalized), candidate_stats in stats.items():
        if candidate_stats.occurrence_count < config.threshold:
            continue
        covered_by, all_occurrences_covered = _coverage_result(
            language,
            candidate_stats.occurrences,
            current_rules,
        )
        if config.exclude_covered and all_occurrences_covered:
            continue
        candidates.append(
            {
                "covered_by_rule_ids": covered_by,
                "language": language,
                "message_count": len(candidate_stats.source_lines),
                "normalized_phrase": normalized,
                "occurrence_count": candidate_stats.occurrence_count,
                "phrase": min(
                    candidate_stats.phrases, key=lambda item: (item.casefold(), item)
                ),
                "status": "pending",
            }
        )

    candidates.sort(
        key=lambda item: (
            item["language"],
            -item["message_count"],
            -item["occurrence_count"],
            item["normalized_phrase"],
            item["phrase"],
        )
    )
    language_counts = Counter(message.language for message in messages)

    return {
        "artifact_type": ARTIFACT_TYPE,
        "candidates": candidates,
        "parameters": {
            "cjk_ngram_range": [config.cjk_ngram_min, config.cjk_ngram_max],
            "exclude_covered": config.exclude_covered,
            "min_length": config.min_length,
            "occurrence_threshold": config.threshold,
            "word_ngram_range": [config.word_ngram_min, config.word_ngram_max],
        },
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "assistant_message_count": len(messages),
            "candidate_count": len(candidates),
            "input_record_count": input_record_count,
            "language_counts": dict(sorted(language_counts.items())),
            "languages": sorted(language_counts),
        },
    }


def serialize_report(report: Mapping[str, object]) -> str:
    """Serialize with stable key ordering and a single trailing newline."""
    return (
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def write_report(output_path: Path, report: Mapping[str, object]) -> None:
    """Atomically write a report using stable LF newlines."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(serialize_report(report))
        os.replace(temporary_name, output_path)
    except OSError as exc:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                # Do not mask the primary report write failure with cleanup failure.
                pass
        raise CandidateMinerError(
            f"unable to write output file: {output_path}"
        ) from exc


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Mine pending natural-expression candidates from an explicitly provided "
            "local JSONL file. No rules are generated, modified, or activated."
        )
    )
    parser.add_argument("--input", required=True, type=Path, help="input JSONL file")
    parser.add_argument("--output", required=True, type=Path, help="review JSON file")
    parser.add_argument(
        "--language",
        help="explicit language/locale for every assistant record; overrides record lang",
    )
    parser.add_argument(
        "--threshold",
        type=_positive_int,
        default=DEFAULT_THRESHOLD,
        help=f"minimum occurrence count (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--word-ngram-min",
        type=_positive_int,
        default=DEFAULT_WORD_NGRAM_MIN,
    )
    parser.add_argument(
        "--word-ngram-max",
        type=_positive_int,
        default=DEFAULT_WORD_NGRAM_MAX,
    )
    parser.add_argument(
        "--cjk-ngram-min",
        type=_positive_int,
        default=DEFAULT_CJK_NGRAM_MIN,
    )
    parser.add_argument(
        "--cjk-ngram-max",
        type=_positive_int,
        default=DEFAULT_CJK_NGRAM_MAX,
    )
    parser.add_argument(
        "--min-length",
        type=_positive_int,
        default=DEFAULT_MIN_LENGTH,
        help=f"minimum non-space character length (default: {DEFAULT_MIN_LENGTH})",
    )
    parser.add_argument(
        "--exclude-covered",
        action="store_true",
        help="omit candidates matched by a current curated SLOP_RULES pattern",
    )
    parser.add_argument(
        "--debug-candidates",
        action="store_true",
        help="explicitly print candidate phrases; may expose assistant text",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    if input_path == output_path:
        parser.error("--input and --output must be different files")

    config = MiningConfig(
        threshold=args.threshold,
        word_ngram_min=args.word_ngram_min,
        word_ngram_max=args.word_ngram_max,
        cjk_ngram_min=args.cjk_ngram_min,
        cjk_ngram_max=args.cjk_ngram_max,
        min_length=args.min_length,
        exclude_covered=args.exclude_covered,
    )
    try:
        messages, record_count = read_jsonl(
            input_path,
            language_override=args.language,
        )
        report = build_report(
            messages,
            input_record_count=record_count,
            config=config,
        )
        write_report(output_path, report)
    except CandidateMinerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    summary = report["summary"]
    languages = ", ".join(summary["languages"]) or "none"
    print(f"input: {input_path}")
    print(f"output: {output_path}")
    print(
        "assistant_messages="
        f"{summary['assistant_message_count']} candidates={summary['candidate_count']} "
        f"languages={languages}"
    )
    if args.debug_candidates:
        for candidate in report["candidates"]:
            print(f"[debug candidate] {candidate['language']}: {candidate['phrase']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
