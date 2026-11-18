"""Deterministic paragraph chunking shared by built-in knowledge domains."""

import math
import re

TARGET_CHARS = 900
MAX_CHARS = 1_200
OVERLAP_CHARS = 120
MAX_CHUNKS_PER_ENTRY = 96
MAX_EMBED_CHARS = 2_000
MAX_HEADER_CHARS = 600

_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;])|(?<=[.])\s+")


def _windows(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text] if text else []
    count = math.ceil((len(text) - OVERLAP_CHARS) / (MAX_CHARS - OVERLAP_CHARS))
    size = math.ceil((len(text) + (count - 1) * OVERLAP_CHARS) / count)
    stride = size - OVERLAP_CHARS
    return [text[i * stride:i * stride + size] for i in range(count)]


def source_windows(paragraph: str) -> list[str]:
    """Split an existing source paragraph into exact, overlapping substrings.

    Personal documents already provide paragraph locations. Unlike sentence
    reflow, exact windows preserve every space and punctuation character so a
    package can verify that each advertised excerpt occurs in its source.
    The caller owns the total document/chunk limit; no tail is dropped.
    """
    return _windows(paragraph)


def _pieces(paragraph: str) -> list[str]:
    paragraph = paragraph.strip()
    if len(paragraph) <= MAX_CHARS:
        return [paragraph] if paragraph else []
    pieces: list[str] = []
    current = ""
    for sentence in (s.strip() for s in _SENTENCE_RE.split(paragraph)):
        if not sentence:
            continue
        if len(sentence) > MAX_CHARS:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(_windows(sentence))
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > MAX_CHARS:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def chunk_bodies(content: str) -> list[str]:
    """The content part of each chunk, in order, covering all of ``content``.

    Paragraph-aware chunking is used while it fits in ``MAX_CHUNKS_PER_ENTRY``;
    an entry fragmented beyond that is cut into that many even windows
    instead, so no part of an entry is ever dropped.
    """
    bodies = _bodies(content)
    if len(bodies) <= MAX_CHUNKS_PER_ENTRY:
        return bodies
    text = content.strip()
    size = math.ceil(len(text) / MAX_CHUNKS_PER_ENTRY)
    return [text[i:i + size] for i in range(0, len(text), size)]


def _bodies(content: str) -> list[str]:
    bodies: list[str] = []
    current = ""
    for paragraph in _PARAGRAPH_RE.split(content):
        for piece in _pieces(paragraph):
            candidate = f"{current}\n\n{piece}".strip()
            if current and (len(candidate) > MAX_CHARS or len(current) >= TARGET_CHARS):
                bodies.append(current)
                current = piece
            else:
                current = candidate
    if current:
        bodies.append(current)
    return bodies
