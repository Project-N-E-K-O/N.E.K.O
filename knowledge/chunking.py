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

Each entry yields a bounded number of chunks. The text that gets embedded
carries the title (and summary on the first chunk) so a chunk taken out of a
long article still says what it is about. ``text_hash`` fingerprints that text:
a vector is only valid for the exact text it was computed from, which lets a
re-imported pack keep the vectors of unchanged chunks.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .models import KnowledgeEntry


# Share the pure splitter; entry-to-embedding rendering stays in this domain.
from utils.text_chunking import (  # noqa: F401 - retained public constants
    TARGET_CHARS, MAX_CHARS, OVERLAP_CHARS, MAX_CHUNKS_PER_ENTRY,
    MAX_EMBED_CHARS, MAX_HEADER_CHARS, chunk_bodies,
    _bodies, _pieces, _windows,
)


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    chunk_index: int
    embed_text: str
    text_hash: str


def derive_chunks(entry: KnowledgeEntry) -> tuple[KnowledgeChunk, ...]:
    """Split an entry into at most ``MAX_CHUNKS_PER_ENTRY`` embedding inputs."""
    chunks: list[KnowledgeChunk] = []
    for index, body in enumerate(chunk_bodies(entry.content)):
        header = entry.title
        if index == 0 and entry.summary:
            header = f"{entry.title}\n{entry.summary}"
        # The header (title, and the summary on the first chunk) is capped so
        # a long summary cannot push the chunk's own text out of the input.
        embed_text = f"{header[:MAX_HEADER_CHARS]}\n{body}"[:MAX_EMBED_CHARS]
        chunks.append(
            KnowledgeChunk(
                chunk_index=index,
                embed_text=embed_text,
                text_hash=hashlib.sha256(embed_text.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(chunks)
