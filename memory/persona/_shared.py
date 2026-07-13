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
"""Shared persona constants and text matching helpers."""

from __future__ import annotations

import re

from memory.stop_names import strip_stop_names
from utils.logger_config import get_module_logger


logger = get_module_logger("memory.persona", "Memory")

SUPPRESS_MENTION_LIMIT = 2
SUPPRESS_WINDOW_HOURS = 5
SUPPRESS_COOLDOWN_HOURS = 5
SIMILARITY_THRESHOLD = 0.6
AUTO_CONFIRM_DAYS = 3

_SPLIT_RE = re.compile(
    r'[，。、！？；：\u201c\u201d\u2018\u2019（）()\[\]{}<>《》【】\s,.!?;:\-\u2014\u2026\xb7\u3000]+'
)


def _extract_keywords(text: str, stop_names: list[str] | None = None) -> set[str]:
    """Extract keywords and n-grams from CJK and Latin text."""
    if stop_names:
        text = strip_stop_names(text, stop_names)
    segments = _SPLIT_RE.split(text)
    keywords: set[str] = set()

    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        cjk_count = sum(
            1 for ch in seg
            if '\u4e00' <= ch <= '\u9fff'
            or '\u3040' <= ch <= '\u30ff'
            or '\uac00' <= ch <= '\ud7af'
        )
        if cjk_count > len(seg) / 2:
            for n in (2, 3):
                for i in range(len(seg) - n + 1):
                    keywords.add(seg[i:i + n])
        elif len(seg) >= 2:
            keywords.add(seg)

    return keywords


def _is_mentioned(
    fact_text: str,
    response_text: str,
    stop_names: list[str] | None = None,
) -> bool:
    """Return whether a response mentions a persona fact."""
    if not fact_text or not response_text:
        return False
    keywords = _extract_keywords(fact_text, stop_names=stop_names)
    if not keywords:
        return False
    haystack = (
        strip_stop_names(response_text, stop_names)
        if stop_names
        else response_text
    )
    return any(keyword in haystack for keyword in keywords)
