"""Deterministic system-owned chunk derivation for knowledge entries."""

from __future__ import annotations

import re
import math
from dataclasses import dataclass
from hashlib import sha256


CHUNKER_VERSION = 1
EMBEDDING_INPUT_VERSION = 1
TARGET_CHARS = 900
MAX_CHARS = 1_200
OVERLAP_CHARS = 120
MAX_CHUNKS_PER_ENTRY = 32
MAX_EMBEDDING_CHARS = 4_096

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;\.])\s+|(?<=[。！？!?；;])")


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    chunk_id: str
    chunk_index: int
    heading: str
    chunk_text: str
    content_hash: str
    embedding_text: str


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _split_long_text(text: str) -> list[str]:
    text = text.strip()
    if len(text) <= MAX_CHARS:
        return [text] if text else []
    sentences = [value.strip() for value in _SENTENCE_RE.split(text) if value.strip()]
    if len(sentences) <= 1:
        return _sliding_windows(text)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > MAX_CHARS:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(_sliding_windows(sentence))
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


def _sliding_windows(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text] if text else []
    window_count = math.ceil(
        (len(text) - OVERLAP_CHARS) / (MAX_CHARS - OVERLAP_CHARS)
    )
    window_size = math.ceil(
        (len(text) + (window_count - 1) * OVERLAP_CHARS) / window_count
    )
    stride = window_size - OVERLAP_CHARS
    return [
        text[index * stride:min(index * stride + window_size, len(text))]
        for index in range(window_count)
    ]


def _sections(content: str) -> list[tuple[str, str]]:
    heading = ""
    sections: list[tuple[str, str]] = []
    paragraphs: list[str] = []

    def flush() -> None:
        if paragraphs:
            sections.append((heading, "\n\n".join(paragraphs)))
            paragraphs.clear()

    for block in re.split(r"\n\s*\n", str(content or "")):
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        match = _HEADING_RE.match(lines[0])
        if match:
            flush()
            heading = _clean(match.group(1))
            remainder = "\n".join(lines[1:]).strip()
            if remainder:
                paragraphs.append(remainder)
        else:
            paragraphs.append(block)
    flush()
    return sections or [("", str(content or "").strip())]


def _with_overlap(previous: str, current: str) -> str:
    if not previous or not current or len(current) >= MAX_CHARS:
        return current
    overlap = previous[-OVERLAP_CHARS:].lstrip()
    if overlap and current.startswith(overlap):
        return current
    room = MAX_CHARS - len(current) - 2
    if room <= 0:
        return current
    overlap = overlap[-room:]
    return f"{overlap}\n\n{current}" if overlap else current


def _chunk_bodies(content: str) -> list[tuple[str, str]]:
    bodies: list[tuple[str, str]] = []
    for heading, section in _sections(content):
        pieces: list[str] = []
        current = ""
        for paragraph in re.split(r"\n\s*\n", section):
            for piece in _split_long_text(paragraph):
                candidate = f"{current}\n\n{piece}".strip()
                if current and (len(candidate) > MAX_CHARS or len(current) >= TARGET_CHARS):
                    pieces.append(current)
                    current = piece
                else:
                    current = candidate
        if current:
            pieces.append(current)
        previous = ""
        for piece in pieces:
            body = _with_overlap(previous, piece)
            bodies.append((heading, body))
            previous = piece
            if len(bodies) >= MAX_CHUNKS_PER_ENTRY:
                return bodies
    return bodies


def derive_knowledge_chunks(entry, *, entry_key: str) -> tuple[KnowledgeChunk, ...]:
    """Derive bounded chunks; every returned field is system-owned."""
    occurrences: dict[str, int] = {}
    chunks: list[KnowledgeChunk] = []
    for index, (heading, body) in enumerate(_chunk_bodies(entry.content)):
        embedding_text = knowledge_embedding_text(entry, heading=heading, chunk_text=body)
        content_hash = sha256(embedding_text.encode("utf-8")).hexdigest()
        occurrence = occurrences.get(content_hash, 0)
        occurrences[content_hash] = occurrence + 1
        identity = f"{entry_key}\0{content_hash}\0{occurrence}"
        chunk_id = sha256(identity.encode("utf-8")).hexdigest()
        chunks.append(KnowledgeChunk(
            chunk_id=chunk_id,
            chunk_index=index,
            heading=_clean(heading),
            chunk_text=body.strip(),
            content_hash=content_hash,
            embedding_text=embedding_text,
        ))
    return tuple(chunks)


def knowledge_embedding_text(entry, *, heading: str, chunk_text: str) -> str:
    """Build the exact text whose fingerprint owns a chunk vector."""
    aliases = " | ".join(
        _clean(value) for value in entry.terms.get("alias", ()) if _clean(value)
    )[:500]
    recognition = " | ".join(
        _clean(value) for value in entry.terms.get("recognition", ()) if _clean(value)
    )[:500]
    parts = [
        "Document:",
        f"Title: {_clean(entry.title)[:500]}",
        f"Aliases: {aliases}" if aliases else "",
        f"Recognition: {recognition}" if recognition else "",
        f"Summary: {_clean(entry.summary)[:800]}" if _clean(entry.summary) else "",
        f"Heading: {_clean(heading)[:300]}" if _clean(heading) else "",
        f"Content: {str(chunk_text or '').strip()}",
    ]
    return "\n".join(value for value in parts if value)[:MAX_EMBEDDING_CHARS]


def knowledge_query_embedding_text(query: object) -> str:
    """Build the query-side text for the versioned embedding input contract."""
    return f"Query: {_clean(query)}"
