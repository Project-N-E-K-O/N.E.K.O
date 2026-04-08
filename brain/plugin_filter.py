# -*- coding: utf-8 -*-
"""
Two-stage plugin filtering for agent assessment.

Stage 1 (coarse — only when plugin descriptions > 4000 chars):
  - BM25 text matching: select plugins with token overlap to user intent
  - LLM coarse screening: pick semantically relevant plugins by id + short_description
  - Keyword hit: plugins whose configured regex keywords match the user text
  → Union of all three goes to Stage 2

Stage 2 (fine — always runs):
  - Full LLM assessment with complete plugin descriptions (current behavior)
  - Keyword-hit plugins are specially annotated so the LLM is aware of them
"""

import math
import re
from collections import Counter
from typing import Any, Dict, List, Tuple

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Agent")

# ---------------------------------------------------------------------------
#  Tokenizer (lightweight, supports CJK + Latin)
# ---------------------------------------------------------------------------

_CJK_RANGES = (
    "\u4e00-\u9fff"     # CJK Unified Ideographs
    "\u3400-\u4dbf"     # CJK Extension A
    "\uf900-\ufaff"     # CJK Compatibility Ideographs
)
_TOKEN_RE = re.compile(
    rf"[{_CJK_RANGES}]|[a-zA-Z0-9_]+",
    re.UNICODE,
)


def _tokenize(text: str) -> List[str]:
    """Split text into tokens: individual CJK characters + Latin words."""
    return [t.lower() for t in _TOKEN_RE.findall(text)]


# ---------------------------------------------------------------------------
#  BM25 (simplified, single-query)
# ---------------------------------------------------------------------------

class _BM25:
    """Minimal BM25 scorer for a small document corpus."""

    def __init__(self, documents: List[List[str]], *, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = len(documents)
        self.doc_freqs: List[Counter] = []
        self.doc_lens: List[int] = []
        self.idf: Dict[str, float] = {}

        df: Counter = Counter()
        for doc in documents:
            freqs = Counter(doc)
            self.doc_freqs.append(freqs)
            self.doc_lens.append(len(doc))
            for token in freqs:
                df[token] += 1

        self.avg_dl = sum(self.doc_lens) / max(self.corpus_size, 1)

        for token, freq in df.items():
            # IDF with smoothing
            self.idf[token] = math.log(
                (self.corpus_size - freq + 0.5) / (freq + 0.5) + 1.0
            )

    def score(self, query_tokens: List[str]) -> List[float]:
        """Return BM25 scores for each document given query tokens."""
        scores = [0.0] * self.corpus_size
        for token in query_tokens:
            idf = self.idf.get(token, 0.0)
            if idf <= 0:
                continue
            for i, (freqs, dl) in enumerate(zip(self.doc_freqs, self.doc_lens, strict=True)):
                tf = freqs.get(token, 0)
                if tf == 0:
                    continue
                norm_tf = (tf * (self.k1 + 1)) / (
                    tf + self.k1 * (1 - self.b + self.b * dl / max(self.avg_dl, 1))
                )
                scores[i] += idf * norm_tf
        return scores


# ---------------------------------------------------------------------------
#  Keyword matching
# ---------------------------------------------------------------------------

def _match_keywords(text: str, keywords: List[str]) -> bool:
    """Check if any keyword regex matches the text."""
    for pattern in keywords:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        except re.error:
            # Invalid regex — treat as literal
            if pattern.lower() in text.lower():
                return True
    return False


# ---------------------------------------------------------------------------
#  Public API: two-stage filter
# ---------------------------------------------------------------------------

def build_plugin_desc_text(plugin: Dict[str, Any]) -> str:
    """Build a full text description string for a single plugin (for BM25 corpus)."""
    pid = plugin.get("id", "")
    name = plugin.get("name", "")
    desc = plugin.get("description", "")
    short = plugin.get("short_description", "")
    entries = plugin.get("entries", [])
    parts = [pid, name, desc, short]
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict):
                parts.append(e.get("id", ""))
                parts.append(e.get("description", ""))
    return " ".join(str(p) for p in parts if p)


def stage1_filter(
    user_text: str,
    plugins: List[Dict[str, Any]],
    *,
    bm25_top_k: int = 10,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Stage 1: coarse filter using BM25 + keyword matching.

    Returns:
        (filtered_plugins, keyword_hit_ids)
        - filtered_plugins: plugins that passed stage 1 (for stage 2 LLM)
        - keyword_hit_ids: plugin IDs that matched via keywords (for annotation)
    """
    if not plugins:
        return [], []

    query_tokens = _tokenize(user_text)

    # BM25 scoring
    doc_tokens = [_tokenize(build_plugin_desc_text(p)) for p in plugins]
    bm25 = _BM25(doc_tokens)
    scores = bm25.score(query_tokens)

    # Get top-K by BM25
    indexed_scores = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    bm25_indices = set()
    for idx, score in indexed_scores[:bm25_top_k]:
        if score > 0:
            bm25_indices.add(idx)

    # Keyword matching
    keyword_hit_ids: List[str] = []
    keyword_indices = set()
    for i, p in enumerate(plugins):
        kws = p.get("keywords", [])
        if isinstance(kws, list) and kws and _match_keywords(user_text, kws):
            keyword_indices.add(i)
            pid = p.get("id", "")
            if pid:
                keyword_hit_ids.append(pid)

    # Union
    selected_indices = bm25_indices | keyword_indices
    filtered = [plugins[i] for i in sorted(selected_indices)]

    logger.debug(
        "[PluginFilter] Stage1: %d/%d plugins selected (bm25=%d, keyword=%d)",
        len(filtered), len(plugins), len(bm25_indices), len(keyword_indices),
    )

    return filtered, keyword_hit_ids


def build_coarse_screening_prompt(plugins: List[Dict[str, Any]], user_text: str) -> str:
    """Build a prompt for LLM coarse screening (stage 1 supplement).

    Uses only plugin id + short_description to keep the prompt small.
    """
    lines = []
    for p in plugins:
        pid = p.get("id", "unknown")
        short = p.get("short_description") or p.get("description", "")
        if len(short) > 200:
            short = short[:200] + "..."
        lines.append(f"- {pid}: {short}")
    plugin_list = "\n".join(lines)

    return (
        f"Given the user's request, pick ALL plugin IDs that MIGHT be relevant.\n"
        f"User request: {user_text}\n\n"
        f"Available plugins:\n{plugin_list}\n\n"
        f"Return ONLY a JSON array of plugin IDs, e.g. [\"web_search\", \"memo_reminder\"]. "
        f"If none are relevant, return []. No explanation."
    )


def annotate_keyword_hits(
    plugins_desc: str,
    keyword_hit_ids: List[str],
) -> str:
    """Annotate the plugin description text with keyword-hit markers."""
    if not keyword_hit_ids:
        return plugins_desc
    for pid in keyword_hit_ids:
        # Add a marker after the plugin ID line
        plugins_desc = plugins_desc.replace(
            f"- {pid}:",
            f"- {pid} [KEYWORD MATCH]:",
        )
    return plugins_desc


def generate_short_description_prompt(plugin_id: str, full_description: str) -> str:
    """Build a prompt to generate a short_description from a full description."""
    return (
        f"Generate a concise summary (under 300 characters, English) for this plugin.\n"
        f"Plugin ID: {plugin_id}\n"
        f"Full description: {full_description}\n\n"
        f"Return ONLY the summary text, nothing else."
    )
