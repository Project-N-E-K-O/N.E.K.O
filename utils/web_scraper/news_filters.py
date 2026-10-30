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

"""Content filters for overseas proactive news (Twitter/X and Google Trends).

- Tragic news: trend terms or headlines mentioning death, violent crime,
  terror, or disasters (word lists live in ``config.prompts.prompts_proactive``).
- Gambling links: news items hosted on sportsbook / casino sites.
- Junk headlines: "headlines" that are just an image or document file name.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from urllib.parse import urlparse

# Host fragments of gambling sites seen in, or likely in, trending news links.
_GAMBLING_HOST_FRAGMENTS = (
    "sportsbook",
    "casino",
    "fanduel",
    "draftkings",
    "betmgm",
    "bet365",
    "betway",
    "betfair",
    "bovada",
    "pointsbet",
    "williamhill",
    "pokerstars",
    "1xbet",
    "stake.com",
)
_FILE_NAME_TITLE_RE = re.compile(r"\.(?:jpe?g|png|gif|webp|svg|bmp|pdf)$", re.IGNORECASE)


def _is_cjk(ch: str) -> bool:
    return unicodedata.name(ch, "").startswith(("CJK", "HIRAGANA", "KATAKANA", "HANGUL"))


def _is_word_term(term: str) -> bool:
    """Latin/Cyrillic terms match on word boundaries; CJK terms as substrings."""
    return not any(_is_cjk(ch) for ch in term)


@lru_cache(maxsize=1)
def _blocked_terms_pattern() -> re.Pattern[str]:
    from config.prompts.prompts_proactive import OVERSEAS_NEWS_BLOCKED_TERMS

    parts: list[str] = []
    for terms in OVERSEAS_NEWS_BLOCKED_TERMS.values():
        for raw in terms:
            prefix = raw.endswith("*")
            term = raw.rstrip("*").strip().lower()
            if not term:
                continue
            escaped = re.escape(term).replace(r"\ ", r"\s+")
            if _is_word_term(term):
                parts.append(rf"(?<!\w){escaped}" + ("" if prefix else r"(?!\w)"))
            else:
                parts.append(escaped)
    return re.compile("|".join(sorted(set(parts), key=len, reverse=True)), re.IGNORECASE)


def is_tragic_news_text(text: str | None) -> bool:
    """True when a trend term or headline mentions death, violence, or disaster."""
    value = str(text or "")
    return bool(value) and _blocked_terms_pattern().search(value.lower()) is not None


def is_gambling_url(url: str | None) -> bool:
    """True when a link points at a sportsbook / casino host."""
    host = (urlparse(str(url or "")).hostname or "").lower()
    return any(fragment in host for fragment in _GAMBLING_HOST_FRAGMENTS)


def is_junk_headline(title: str | None) -> bool:
    """True when a "headline" is only a file name, e.g. ``photo-006.jpg``."""
    return bool(_FILE_NAME_TITLE_RE.search(str(title or "").strip()))
