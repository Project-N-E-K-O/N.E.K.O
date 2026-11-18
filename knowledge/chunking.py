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

"""Deterministic chunking of entries into embedding inputs.

Each entry yields deterministic chunks under the caller's capacity limits. The text that gets embedded
carries the title (and summary on the first chunk) so a chunk taken out of a
long article still says what it is about. ``text_hash`` fingerprints that text:
a vector is only valid for the exact text it was computed from, which lets a
re-imported pack keep the vectors of unchanged chunks.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

from .models import KnowledgeEntry

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

    Parsed documents already provide paragraph locations. Unlike sentence
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


def chunk_bodies(content: str, *, fit=None) -> list[str]:
    """The content part of each chunk, in order, covering all of ``content``.

    Paragraph-aware chunking is used while it fits in ``MAX_CHUNKS_PER_ENTRY``;
    an entry fragmented beyond that is cut into that many even windows
    instead. With ``fit``, every full input is checked and subdivided without
    the legacy count cap; the pack owner's total chunk capacity is authoritative.
    """
    bodies = _bodies(content)
    if fit is not None:
        return [part for body in bodies for part in _fit_windows(body, fit)]
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


def _fit_windows(text: str, fit) -> list[str]:
    """Split exact substrings until every complete embedding input fits."""
    parts = []
    start = 0
    while start < len(text):
        low, high, accepted = 1, len(text) - start, 0
        while low <= high:
            length = (low + high) // 2
            if fit(text[start:start + length]):
                accepted, low = length, length + 1
            else:
                high = length - 1
        if not accepted:
            raise ValueError("embedding_input_too_large")
        parts.append(text[start:start + accepted])
        start += accepted
    return parts


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    chunk_index: int
    embed_text: str
    text_hash: str


def derive_chunks(entry: KnowledgeEntry, *, fit=None) -> tuple[KnowledgeChunk, ...]:
    """Keep legacy hashes by default, or fit full inputs without dropping tails.

    The optional callback sees title and first-chunk summary as well as the
    body. Token-aware callers run it off-loop and enforce their global capacity.
    """
    chunks: list[KnowledgeChunk] = []
    pending = list(chunk_bodies(entry.content) if fit is None else _bodies(entry.content))
    while pending:
        body = pending.pop(0)
        header = entry.title
        if not chunks and entry.summary:
            header = f"{entry.title}\n{entry.summary}"
        prefix = header[:MAX_HEADER_CHARS] + "\n"
        if fit is not None:
            def fits(value):
                return len(prefix + value) <= MAX_EMBED_CHARS and fit(prefix + value)

            parts = _fit_windows(body, fits)
            body, pending = parts[0], parts[1:] + pending
            embed_text = prefix + body
        else:
            # Existing v1 packs retain their exact cache identity. Imported or
            # newly exported data opts into fit and never uses this tail slice.
            embed_text = (prefix + body)[:MAX_EMBED_CHARS]
        chunks.append(KnowledgeChunk(chunk_index=len(chunks), embed_text=embed_text,
                                     text_hash=hashlib.sha256(embed_text.encode("utf-8")).hexdigest()))
    return tuple(chunks)
