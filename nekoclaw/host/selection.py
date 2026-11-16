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

"""Which plugin entries are visible, and the local half of the two-stage filter.

Ported from ``brain/plugin_filter.py`` and the selection helpers of
``brain/task_executor.py``; the rules are unchanged on purpose, because plugin
authors were promised that how an entry gets selected stays the same
(RFC #3683). ``tests/unit/nekoclaw/test_contract_plugin_selection.py`` runs the
same expectations against both implementations.

Everything here is local and makes no model call:

* visibility: ``passive`` plugins and entries switched off through
  ``agent_auto`` / ``agent_exposed`` / ``llm_exposed`` / ``agent_hidden``;
* the description lines the fine selection is shown;
* keyword patterns and the BM25 text match of the first stage.

``token_set`` and ``similarity`` are new: they compare a dispatched sentence
with the commissions on the ledger and are not part of plugin selection.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

BM25_TOP_K = 10
KEYWORD_MARK = " [KEYWORD MATCH]"
_DEFAULT_ENTRY = {"id": "run", "description": "Default plugin entry"}
_VISIBILITY_OFF_KEYS = ("agent_auto", "agent_exposed", "llm_exposed")

# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


def is_entry_hidden(entry: Any) -> bool:
    """Return True when an entry is switched off for automatic routing."""
    if not isinstance(entry, dict):
        return False
    meta = entry.get("metadata")
    if not isinstance(meta, dict):
        return False
    for key in _VISIBILITY_OFF_KEYS:
        if key in meta and meta.get(key) is False:
            return True
    return meta.get("agent_hidden") is True


def visible_entries(plugin: Any) -> list[dict[str, Any]]:
    """Return the entries of a plugin that automatic routing may use.

    A plugin without an ``entries`` field has the one default entry ``run``.
    """
    entries = plugin.get("entries") if isinstance(plugin, dict) else getattr(plugin, "entries", None)
    if entries is None:
        return [dict(_DEFAULT_ENTRY)]
    if not isinstance(entries, list):
        return []
    return [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("id") and not is_entry_hidden(entry)
    ]


def routable_plugins(plugins: Any) -> list[dict[str, Any]]:
    """Return the plugins selection works on: not passive, with a visible entry."""
    result: list[dict[str, Any]] = []
    try:
        iterable = plugins.values() if isinstance(plugins, dict) else plugins
        for plugin in iterable or ():
            if not isinstance(plugin, dict) or plugin.get("passive"):
                continue
            if visible_entries(plugin):
                result.append(plugin)
    except TypeError:
        return []
    return result


def find_entry(plugins: Iterable[Any], plugin_id: str, entry_id: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(plugin, entry)``; the entry is None when it is not a visible one."""
    for plugin in plugins:
        if not isinstance(plugin, dict) or plugin.get("id") != plugin_id:
            continue
        for entry in visible_entries(plugin):
            if entry.get("id") == entry_id:
                return plugin, entry
        return plugin, None
    return None, None


# ---------------------------------------------------------------------------
# Description lines for the fine selection
# ---------------------------------------------------------------------------


def _schema_hint(entry: dict[str, Any]) -> str:
    schema = entry.get("input_schema")
    if not isinstance(schema, dict):
        return ""
    props = schema.get("properties", {})
    if not isinstance(props, dict) or not props:
        return ""
    fields = []
    for name, definition in list(props.items())[:8]:
        field_type = definition.get("type", "any") if isinstance(definition, dict) else "any"
        enum_hint = ""
        if isinstance(definition, dict):
            enum_values = definition.get("enum")
            if isinstance(enum_values, list) and enum_values:
                inner = "|".join(str(value) for value in enum_values[:12])
                # When cut, say how many legal values are not listed, so the
                # twelve shown are not read as the complete set.
                if len(enum_values) > 12:
                    enum_hint = f" enum=[{inner}|... +{len(enum_values) - 12} more]"
                else:
                    enum_hint = f" enum=[{inner}]"
        fields.append(f"{name}:{field_type}{enum_hint}")
    required = schema.get("required", [])
    required_hint = f" required={required}" if required else ""
    return f" args({', '.join(fields)}{required_hint})"


def build_desc_lines(plugins: Any) -> list[str]:
    """Return one line per plugin: id, description and its visible entries."""
    lines: list[str] = []
    try:
        iterable = plugins.values() if isinstance(plugins, dict) else plugins
        for plugin in iterable or ():
            if not isinstance(plugin, dict):
                continue
            plugin_id = plugin.get("id")
            if not plugin_id:
                continue
            entry_parts: list[str] = []
            for entry in visible_entries(plugin):
                try:
                    entry_id = entry.get("id")
                    if not entry_id:
                        continue
                    description = entry.get("description", "")
                    part = f"{entry_id}: {description}" if description else str(entry_id)
                    try:
                        part += _schema_hint(entry)
                    except Exception:  # noqa: BLE001 - a malformed schema only loses its hint
                        pass
                    entry_parts.append(part)
                except Exception:  # noqa: BLE001 - one malformed entry does not hide the others
                    continue
            if not entry_parts:
                continue
            lines.append(f"- {plugin_id}: {plugin.get('description', '')} | entries: [{'; '.join(entry_parts)}]")
    except Exception:  # noqa: BLE001 - a malformed catalog yields the lines built so far
        pass
    return lines


def annotate_keyword_hits(plugins_desc: str, keyword_hit_ids: Sequence[str]) -> str:
    """Mark the description lines of plugins whose keywords matched."""
    for plugin_id in keyword_hit_ids:
        plugins_desc = plugins_desc.replace(f"- {plugin_id}:", f"- {plugin_id}{KEYWORD_MARK}:")
    return plugins_desc


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------


def match_keywords(text: str, keywords: Sequence[str]) -> bool:
    """Return whether any keyword pattern matches ``text``, ignoring case.

    Patterns are regular expressions; one that does not compile is matched as
    plain text.
    """
    for pattern in keywords:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        except re.error:
            if pattern.lower() in text.lower():
                return True
    return False


def keyword_hits(text: str, plugins: Sequence[dict[str, Any]]) -> list[str]:
    """Return the ids of the plugins whose ``keywords`` match ``text``."""
    hits: list[str] = []
    for plugin in plugins:
        keywords = plugin.get("keywords", [])
        plugin_id = plugin.get("id", "")
        if isinstance(keywords, list) and keywords and plugin_id and match_keywords(text, keywords):
            hits.append(plugin_id)
    return hits


# ---------------------------------------------------------------------------
# BM25 text match
# ---------------------------------------------------------------------------

_CJK_RANGES = "一-鿿㐀-䶿豈-﫿"
_TOKEN_RE = re.compile(rf"[{_CJK_RANGES}]|[a-zA-Z0-9_]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Split into single CJK characters and lower-cased Latin words."""
    return [token.lower() for token in _TOKEN_RE.findall(text)]


class _BM25:
    """BM25 scores of one query over a small set of documents."""

    def __init__(self, documents: list[list[str]], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.corpus_size = len(documents)
        self.doc_freqs: list[Counter[str]] = []
        self.doc_lens: list[int] = []
        self.idf: dict[str, float] = {}

        document_frequency: Counter[str] = Counter()
        for document in documents:
            frequencies = Counter(document)
            self.doc_freqs.append(frequencies)
            self.doc_lens.append(len(document))
            for token in frequencies:
                document_frequency[token] += 1

        self.avg_dl = sum(self.doc_lens) / max(self.corpus_size, 1)
        for token, frequency in document_frequency.items():
            self.idf[token] = math.log((self.corpus_size - frequency + 0.5) / (frequency + 0.5) + 1.0)

    def score(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * self.corpus_size
        for token in query_tokens:
            idf = self.idf.get(token, 0.0)
            if idf <= 0:
                continue
            for index, (frequencies, length) in enumerate(zip(self.doc_freqs, self.doc_lens, strict=True)):
                tf = frequencies.get(token, 0)
                if tf == 0:
                    continue
                norm_tf = (tf * (self.k1 + 1)) / (
                    tf + self.k1 * (1 - self.b + self.b * length / max(self.avg_dl, 1))
                )
                scores[index] += idf * norm_tf
        return scores


def plugin_corpus_text(plugin: dict[str, Any]) -> str:
    """Return the text of one plugin that the BM25 match reads."""
    parts = [
        plugin.get("id", ""),
        plugin.get("name", ""),
        plugin.get("description", ""),
        plugin.get("short_description", ""),
    ]
    entries = plugin.get("entries", [])
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                parts.append(entry.get("id", ""))
                parts.append(entry.get("description", ""))
    return " ".join(str(part) for part in parts if part)


def bm25_scores(text: str, documents: Sequence[str]) -> list[float]:
    """Return the BM25 score of every document for ``text``."""
    if not documents:
        return []
    return _BM25([tokenize(document) for document in documents]).score(tokenize(text))


def stage1_filter(
    user_text: str,
    plugins: Sequence[dict[str, Any]],
    *,
    bm25_top_k: int = BM25_TOP_K,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return the plugins the local first stage keeps, and the keyword hits.

    Kept are the ``bm25_top_k`` best text matches with a score above zero and
    every plugin whose keywords match, in catalog order.
    """
    if not plugins:
        return [], []

    scores = bm25_scores(user_text, [plugin_corpus_text(plugin) for plugin in plugins])
    ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
    bm25_indices = {index for index, score in ranked[:bm25_top_k] if score > 0}

    keyword_hit_ids: list[str] = []
    keyword_indices: set[int] = set()
    for index, plugin in enumerate(plugins):
        keywords = plugin.get("keywords", [])
        if isinstance(keywords, list) and keywords and match_keywords(user_text, keywords):
            keyword_indices.add(index)
            plugin_id = plugin.get("id", "")
            if plugin_id:
                keyword_hit_ids.append(plugin_id)

    selected = bm25_indices | keyword_indices
    return [plugins[index] for index in sorted(selected)], keyword_hit_ids


# ---------------------------------------------------------------------------
# Comparing sentences (ledger matching, not plugin selection)
# ---------------------------------------------------------------------------


# Scripts written without spaces between words, or whose words carry endings
# (Hangul particles): compared as overlapping two-character pieces. CJK
# ideographs, Hiragana, Katakana (with the half-width forms) and Hangul.
_PAIRED_RANGES = _CJK_RANGES + "぀-ゟ゠-ヿㇰ-ㇿｦ-ﾟ가-힯ᄀ-ᇿ"
# Separate from ``_TOKEN_RE`` on purpose: plugin selection keeps its own
# tokenizer unchanged, while this one also reads Cyrillic, accented Latin and
# the scripts above.
_COMPARE_TOKEN_RE = re.compile(rf"[{_PAIRED_RANGES}]|(?:(?![{_PAIRED_RANGES}])\w)+")
_PAIRED_CHAR_RE = re.compile(rf"[{_PAIRED_RANGES}]")


def token_set(text: str) -> frozenset[str]:
    """Return the tokens used to compare two sentences.

    Words of any alphabet as they are (lower-cased); CJK, kana and Hangul text
    as overlapping two-character pieces within each unbroken run. Single
    characters of these scripts are too common to tell two short requests
    apart.
    """
    tokens: set[str] = set()
    run: list[str] = []

    def flush() -> None:
        if len(run) == 1:
            tokens.add(run[0])
        for first, second in zip(run, run[1:], strict=False):
            tokens.add(first + second)
        run.clear()

    end = -1
    for match in _COMPARE_TOKEN_RE.finditer(text):
        token = match.group(0)
        if _PAIRED_CHAR_RE.fullmatch(token):
            if match.start() != end:
                # A space or punctuation in between ends the run.
                flush()
            run.append(token)
        else:
            flush()
            tokens.add(token.lower())
        end = match.end()
    flush()
    return frozenset(tokens)


def similarity(first: str, second: str) -> float:
    """Return the Jaccard similarity of two sentences' token sets (0 to 1)."""
    left, right = token_set(first), token_set(second)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
