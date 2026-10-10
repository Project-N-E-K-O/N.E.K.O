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

"""Render retrieved cards as one fenced reference block for the model.

Every piece of pack text that enters the block - title included - goes
through ``strip_chat_markup`` (to a fixed point) and ``neutralize_fence``, so
a card can neither speak as a chat role nor close the fence early. Titles and
source fields are also forced onto one line. Lengths are budgeted in tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from config.prompts.prompts_knowledge import (
    PUBLIC_KNOWLEDGE_BLOCK_BEGIN,
    PUBLIC_KNOWLEDGE_BLOCK_END,
    PUBLIC_KNOWLEDGE_BLOCK_NOTE,
    PUBLIC_KNOWLEDGE_CARD_LABELS,
)
from config.prompts.prompts_sys import _loc, normalize_sys_prompt_locale
from utils.tokenize import truncate_to_tokens

from .text import neutralize_fence, single_line, strip_chat_markup


SUMMARY_TOKENS = 120
CONTENT_TOKENS = 360
TITLE_CHARS = 120
SOURCE_CHARS = 120


@dataclass(frozen=True, slots=True)
class RenderCard:
    title: str
    material_type: str
    summary: str
    content: str
    source_name: str
    source_license: str


def _safe(value: str) -> str:
    return neutralize_fence(strip_chat_markup(value))


def _safe_line(value: str, max_chars: int) -> str:
    return single_line(_safe(value), max_chars=max_chars)


def _safe_block(value: str, max_tokens: int) -> str:
    text = truncate_to_tokens(_safe(value).strip(), max_tokens)
    # Truncation can split a markup token in half, and joining lines can
    # create a fresh ``===`` run; clean the final form once more.
    return _safe(text).replace("\n", " ").strip()


def render_reference_block(cards: Sequence[RenderCard], *, language: str | None) -> str:
    """Return the fenced block, or ``""`` when there is nothing to show."""
    if not cards:
        return ""
    lang = normalize_sys_prompt_locale(language)
    labels = _loc(PUBLIC_KNOWLEDGE_CARD_LABELS, lang)
    lines = [_loc(PUBLIC_KNOWLEDGE_BLOCK_BEGIN, lang), _loc(PUBLIC_KNOWLEDGE_BLOCK_NOTE, lang)]
    for card in cards:
        kind = labels.get(card.material_type, labels["knowledge"])
        lines.append("")
        lines.append(f"- [{_safe_line(card.title, TITLE_CHARS)}] ({kind})")
        summary = _safe_block(card.summary, SUMMARY_TOKENS) if card.summary else ""
        if summary:
            lines.append(f"  {labels['summary']}: {summary}")
        content = _safe_block(card.content, CONTENT_TOKENS)
        if content:
            lines.append(f"  {labels['content']}: {content}")
        source = _safe_line(card.source_name, SOURCE_CHARS)
        license_text = _safe_line(card.source_license, SOURCE_CHARS)
        provenance = f"  {labels['source']}: {source}"
        if license_text:
            provenance += f" | {labels['license']}: {license_text}"
        lines.append(provenance)
    lines.append(_loc(PUBLIC_KNOWLEDGE_BLOCK_END, lang))
    return "\n".join(lines)
