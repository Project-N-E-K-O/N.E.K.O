from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import re

from utils.tokenize import count_tokens


DOCUMENT_DIRECT_MAX_TOKENS = 48_000
DOCUMENT_CHUNKED_MAX_TOKENS = 160_000
DOCUMENT_CHUNK_TARGET_TOKENS = 10_000
DOCUMENT_CHUNK_MAX_TOKENS = 12_000
DOCUMENT_CHUNK_MIN_PREFERRED_TOKENS = 2_000
DOCUMENT_MAX_CHUNKS = 16
DOCUMENT_CHUNK_OVERLAP_MAX_TOKENS = 200

TokenCounter = Callable[[str], int]

_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,2})[ \t]+(.+?)\s*$")
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_TXT_CHAPTER_RE = re.compile(
    r"^(?:"
    r"第[零〇一二三四五六七八九十百千万两\d]+[章节回部篇集]"
    r"|卷[零〇一二三四五六七八九十百千万两\d]+"
    r"|序章|序言|楔子|前言|引言|后记|尾声|附录(?:[零〇一二三四五六七八九十\d]+)?"
    r"|chapter[ \t]+(?:\d+|[ivxlcdm]+)"
    r"|part[ \t]+(?:\d+|[ivxlcdm]+)"
    r")"
    r"(?:[ \t　:：、.．\-—]+.{1,48})?$",
    re.IGNORECASE,
)
_PARAGRAPH_BREAK_RE = re.compile(r"(?:\r?\n[ \t]*\r?\n)+")
_SENTENCE_END_RE = re.compile(
    r"(?:[。！？!?]+[”’\"'」』】）)]*[ \t]*|\.(?=[ \t\r\n]|$)[ \t]*)(?:\r?\n)?"
)


class DocumentChunkingError(ValueError):
    def __init__(self, message: str, *, diagnostic: str = "document_split_failed") -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    index: int
    text: str
    tokens: int
    start_char: int
    end_char: int
    heading_paths: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class _Piece:
    text: str
    heading_path: tuple[str, ...]


@dataclass(slots=True)
class _Draft:
    text: str
    heading_paths: list[tuple[str, ...]]
    tokens: int


def split_document(
    text: str,
    document_type: str,
    *,
    token_counter: TokenCounter = count_tokens,
    target_tokens: int = DOCUMENT_CHUNK_TARGET_TOKENS,
    max_tokens: int = DOCUMENT_CHUNK_MAX_TOKENS,
    min_preferred_tokens: int = DOCUMENT_CHUNK_MIN_PREFERRED_TOKENS,
    max_chunks: int = DOCUMENT_MAX_CHUNKS,
) -> tuple[DocumentChunk, ...]:
    """Split validated extracted document text without dropping or reordering it.

    Structural boundaries are preferred. Oversized structural sections are split at
    paragraph boundaries, then sentence boundaries. An indivisible over-budget
    sentence is rejected instead of being cut in the middle.
    """

    if not text:
        raise DocumentChunkingError("document is empty")
    if document_type not in {
        "text/plain",
        "text/markdown",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        raise DocumentChunkingError("document type is not supported")
    if not (0 < min_preferred_tokens <= target_tokens <= max_tokens):
        raise ValueError("chunk token budgets must satisfy 0 < min <= target <= max")
    if max_chunks < 1:
        raise ValueError("max_chunks must be positive")

    sections = (
        _markdown_sections(text)
        if document_type == "text/markdown"
        else _txt_sections(text)
    )
    pieces: list[_Piece] = []
    for section in sections:
        pieces.extend(_fit_piece(section, token_counter, max_tokens))

    drafts = _pack_pieces(
        pieces,
        token_counter=token_counter,
        target_tokens=target_tokens,
        max_tokens=max_tokens,
        min_preferred_tokens=min_preferred_tokens,
    )
    _compact_to_limit(drafts, token_counter, max_tokens, max_chunks)
    if len(drafts) > max_chunks:
        raise DocumentChunkingError(
            f"document requires {len(drafts)} chunks; maximum is {max_chunks}"
        )

    chunks: list[DocumentChunk] = []
    offset = 0
    for index, draft in enumerate(drafts):
        end = offset + len(draft.text)
        chunks.append(
            DocumentChunk(
                index=index,
                text=draft.text,
                tokens=draft.tokens,
                start_char=offset,
                end_char=end,
                heading_paths=tuple(dict.fromkeys(draft.heading_paths)),
            )
        )
        offset = end

    if offset != len(text) or "".join(chunk.text for chunk in chunks) != text:
        raise DocumentChunkingError("document split did not preserve the source")
    return tuple(chunks)


def _markdown_sections(text: str) -> list[_Piece]:
    boundaries: list[tuple[int, tuple[str, ...]]] = []
    offset = 0
    level_one = ""
    fence_char = ""
    fence_width = 0

    for line in text.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        fence = _FENCE_RE.match(line_body)
        if fence:
            marker = fence.group(1)
            if not fence_char:
                fence_char, fence_width = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_width:
                fence_char, fence_width = "", 0
            offset += len(line)
            continue

        if not fence_char:
            heading = _MARKDOWN_HEADING_RE.match(line_body)
            if heading:
                level = len(heading.group(1))
                title = _clean_markdown_heading(heading.group(2))
                if level == 1:
                    level_one = title
                    path = (title,)
                else:
                    path = (level_one, title) if level_one else (title,)
                boundaries.append((offset, path))
        offset += len(line)

    return _sections_from_boundaries(text, boundaries)


def _txt_sections(text: str) -> list[_Piece]:
    lines = text.splitlines(keepends=True)
    boundaries: list[tuple[int, tuple[str, ...]]] = []
    offset = 0
    for index, line in enumerate(lines):
        title = line.strip()
        previous_blank = index == 0 or not lines[index - 1].strip()
        next_blank = index == len(lines) - 1 or not lines[index + 1].strip()
        if (
            0 < len(title) <= 64
            and (previous_blank or next_blank)
            and _TXT_CHAPTER_RE.fullmatch(title)
        ):
            boundaries.append((offset, (title,)))
        offset += len(line)
    return _sections_from_boundaries(text, boundaries)


def _clean_markdown_heading(value: str) -> str:
    return re.sub(r"[ \t]+#+[ \t]*$", "", value).strip()


def _sections_from_boundaries(
    text: str, boundaries: Iterable[tuple[int, tuple[str, ...]]]
) -> list[_Piece]:
    points = list(boundaries)
    if not points:
        return [_Piece(text, ())]

    sections: list[_Piece] = []
    if points[0][0] > 0:
        sections.append(_Piece(text[: points[0][0]], ()))
    for index, (start, path) in enumerate(points):
        end = points[index + 1][0] if index + 1 < len(points) else len(text)
        sections.append(_Piece(text[start:end], path))
    return sections


def _fit_piece(
    piece: _Piece, token_counter: TokenCounter, max_tokens: int
) -> list[_Piece]:
    if token_counter(piece.text) <= max_tokens:
        return [piece]
    paragraphs = _split_after_matches(piece.text, _PARAGRAPH_BREAK_RE)
    if len(paragraphs) > 1:
        return _fit_segments(paragraphs, piece.heading_path, token_counter, max_tokens)
    sentences = _split_after_matches(piece.text, _SENTENCE_END_RE)
    if len(sentences) > 1:
        return _fit_segments(sentences, piece.heading_path, token_counter, max_tokens)
    raise DocumentChunkingError(
        "document contains a sentence that exceeds the per-chunk token limit"
    )


def _fit_segments(
    segments: Iterable[str],
    heading_path: tuple[str, ...],
    token_counter: TokenCounter,
    max_tokens: int,
) -> list[_Piece]:
    pieces: list[_Piece] = []
    for segment in segments:
        candidate = _Piece(segment, heading_path)
        pieces.extend(_fit_piece(candidate, token_counter, max_tokens))
    return pieces


def _split_after_matches(value: str, pattern: re.Pattern[str]) -> list[str]:
    ends = [match.end() for match in pattern.finditer(value)]
    if not ends or ends[-1] == len(value) and len(ends) == 1:
        return [value]
    parts: list[str] = []
    start = 0
    for end in ends:
        if end > start:
            parts.append(value[start:end])
            start = end
    if start < len(value):
        parts.append(value[start:])
    return parts or [value]


def _pack_pieces(
    pieces: Iterable[_Piece],
    *,
    token_counter: TokenCounter,
    target_tokens: int,
    max_tokens: int,
    min_preferred_tokens: int,
) -> list[_Draft]:
    drafts: list[_Draft] = []
    current: _Draft | None = None
    for piece in pieces:
        piece_tokens = token_counter(piece.text)
        if piece_tokens > max_tokens:
            raise DocumentChunkingError("document piece exceeds the per-chunk token limit")
        if current is None:
            current = _Draft(piece.text, [piece.heading_path], piece_tokens)
            continue
        combined_text = current.text + piece.text
        combined_tokens = token_counter(combined_text)
        if combined_tokens <= target_tokens or (
            current.tokens < min_preferred_tokens and combined_tokens <= max_tokens
        ):
            current.text = combined_text
            current.heading_paths.append(piece.heading_path)
            current.tokens = combined_tokens
        else:
            drafts.append(current)
            current = _Draft(piece.text, [piece.heading_path], piece_tokens)
    if current is not None:
        drafts.append(current)

    _merge_small_neighbors(drafts, token_counter, max_tokens, min_preferred_tokens)
    return drafts


def _merge_small_neighbors(
    drafts: list[_Draft],
    token_counter: TokenCounter,
    max_tokens: int,
    min_preferred_tokens: int,
) -> None:
    index = len(drafts) - 1
    while index >= 0 and len(drafts) > 1:
        if drafts[index].tokens >= min_preferred_tokens:
            index -= 1
            continue
        candidates = []
        if index > 0:
            candidates.append(index - 1)
        if index + 1 < len(drafts):
            candidates.append(index)
        merged = False
        for left in sorted(candidates, key=lambda item: drafts[item].tokens + drafts[item + 1].tokens):
            if _try_merge(drafts, left, token_counter, max_tokens):
                merged = True
                break
        index = min(index, len(drafts) - 1) if merged else index - 1


def _compact_to_limit(
    drafts: list[_Draft],
    token_counter: TokenCounter,
    max_tokens: int,
    max_chunks: int,
) -> None:
    while len(drafts) > max_chunks:
        candidates: list[tuple[int, int]] = []
        for index in range(len(drafts) - 1):
            combined_tokens = token_counter(drafts[index].text + drafts[index + 1].text)
            if combined_tokens <= max_tokens:
                candidates.append((combined_tokens, index))
        if not candidates:
            return
        _, index = min(candidates)
        _try_merge(drafts, index, token_counter, max_tokens)


def _try_merge(
    drafts: list[_Draft],
    left_index: int,
    token_counter: TokenCounter,
    max_tokens: int,
) -> bool:
    left = drafts[left_index]
    right = drafts[left_index + 1]
    text = left.text + right.text
    tokens = token_counter(text)
    if tokens > max_tokens:
        return False
    left.text = text
    left.heading_paths.extend(right.heading_paths)
    left.tokens = tokens
    del drafts[left_index + 1]
    return True


__all__ = [
    "DOCUMENT_CHUNKED_MAX_TOKENS",
    "DOCUMENT_CHUNK_MAX_TOKENS",
    "DOCUMENT_CHUNK_MIN_PREFERRED_TOKENS",
    "DOCUMENT_CHUNK_OVERLAP_MAX_TOKENS",
    "DOCUMENT_CHUNK_TARGET_TOKENS",
    "DOCUMENT_DIRECT_MAX_TOKENS",
    "DOCUMENT_MAX_CHUNKS",
    "DocumentChunk",
    "DocumentChunkingError",
    "TokenCounter",
    "split_document",
]
