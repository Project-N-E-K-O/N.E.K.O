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
"""Rendering methods for the memory manager."""

from __future__ import annotations


import hashlib





from collections import defaultdict

from datetime import datetime

from config import (
    PERSONA_RENDER_MAX_TOKENS,
    REFLECTION_RENDER_MAX_TOKENS,
)

from memory.evidence import evidence_score







from utils.tokenize import acount_tokens, count_tokens, tokenizer_identity


class RenderingMixin:
    @staticmethod
    def _text_fingerprint(text: str) -> str:
        """sha256 hex digest of `text` used as the cache key. Same
        encoding as the `rewrite_text_sha256` payload in amerge_into so
        the two stay consistent if we ever cross-check."""
        return hashlib.sha256((text or '').encode('utf-8')).hexdigest()

    @classmethod
    def _get_cached_token_count(cls, entry: dict, *, writeback: bool = True) -> int:
        """Sync cache-aware token count. Writes `token_count`,
        `token_count_text_sha256` and `token_count_tokenizer` back to
        `entry` on miss when `writeback=True` (the default, for persona
        entries that live in the `_personas` in-memory view and therefore
        benefit from across-render cache reuse).

        Callers should pass `writeback=False` for entries that do not have
        a process-resident view (currently: reflection entries, which are
        always loaded fresh from disk via `aload_reflections`). In that
        mode we still short-circuit on a pre-existing cache hit — that's
        free — but we never pollute the entry dict with fields that
        wouldn't survive the next render anyway.

        Cache hit requires BOTH fingerprints to match:
        - text sha256 (catches text mutation)
        - tokenizer identity (catches tiktoken↔heuristic transition;
          see `utils.tokenize.tokenizer_identity` docstring for the
          motivating scenario — packaging without encoding data file).

        Additionally, `token_count` must coerce cleanly to a non-negative
        int. A hand-edited or corrupted `persona.json` could plant a
        non-numeric or negative value with fingerprints that still happen
        to match (or match after someone also hand-rewrote the sha256
        field) — in which case `int(...)` on the cached value would
        either raise or return garbage and bomb the render. On coercion
        failure we treat it as a cache miss and recompute.
        """
        text = entry.get('text', '') or ''
        if not text:
            return 0
        fp = cls._text_fingerprint(text)
        tid = tokenizer_identity()
        cached_count = cls._coerce_cached_count(entry.get('token_count'))
        if (
            cached_count is not None
            and entry.get('token_count_text_sha256') == fp
            and entry.get('token_count_tokenizer') == tid
        ):
            return cached_count
        n = count_tokens(text)
        if writeback:
            entry['token_count'] = int(n)
            entry['token_count_text_sha256'] = fp
            entry['token_count_tokenizer'] = tid
        return int(n)

    @classmethod
    async def _aget_cached_token_count(cls, entry: dict, *, writeback: bool = True) -> int:
        """Async twin — uses `acount_tokens` (worker-thread tiktoken).
        Write-back semantics match the sync helper (both fingerprints).
        See `_get_cached_token_count` for the `writeback=False` contract
        (used by reflection render path, which has no in-memory view),
        and for the defensive coercion of poisoned `token_count` values
        from a hand-edited or corrupted `persona.json`."""
        text = entry.get('text', '') or ''
        if not text:
            return 0
        fp = cls._text_fingerprint(text)
        tid = tokenizer_identity()
        cached_count = cls._coerce_cached_count(entry.get('token_count'))
        if (
            cached_count is not None
            and entry.get('token_count_text_sha256') == fp
            and entry.get('token_count_tokenizer') == tid
        ):
            return cached_count
        n = await acount_tokens(text)
        if writeback:
            entry['token_count'] = int(n)
            entry['token_count_text_sha256'] = fp
            entry['token_count_tokenizer'] = tid
        return int(n)

    @staticmethod
    def _coerce_cached_count(raw) -> int | None:
        """Validate a `token_count` value loaded from an entry dict.

        Returns the non-negative int when `raw` is coercible and sane;
        returns None (→ force a cache miss) when `raw` is missing,
        non-numeric, a bool, a non-integer float (1.9 would silently
        truncate to 1), `inf` / `nan` (`int(inf)` raises
        `OverflowError`), or negative.

        `bool` is a subclass of `int` in Python, so the explicit
        `isinstance(raw, bool)` reject keeps us from accepting `True`/
        `False` as legitimate cached counts if persona.json was hand-
        edited with boolean-looking garbage."""
        if raw is None or isinstance(raw, bool):
            return None
        if isinstance(raw, float):
            if not raw.is_integer():
                return None
            if raw < 0:
                return None
            return int(raw)
        try:
            value = int(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if value < 0:
            return None
        return value

    @staticmethod
    def _invalidate_token_count_cache(entry: dict) -> None:
        """Explicitly drop the cached count. Called by code paths that
        rewrite `entry['text']` (e.g. `amerge_into`) to avoid the tiny
        window where a concurrent reader sees new text + stale count.
        The fingerprint check would catch it anyway, but explicit
        invalidation is clearer and saves one sha256 compute on the
        next render."""
        entry['token_count'] = None
        entry['token_count_text_sha256'] = None
        entry['token_count_tokenizer'] = None

    @staticmethod
    def _invalidate_embedding_cache(entry: dict) -> None:
        """Drop the cached vector triple alongside the token-count cache.

        Called by every path that rewrites ``entry['text']`` — leaving
        a stale vector pointing at old_text would silently corrupt the
        retrieval candidate set (cosine matches would map to text the
        user never said). Same shape as ``_invalidate_token_count_cache``
        so callers can wipe both caches in two adjacent lines.
        """
        entry['embedding'] = None
        entry['embedding_text_sha256'] = None
        entry['embedding_model_id'] = None

    @classmethod
    def _score_trim_entries(
        cls, entries: list, budget: int, now: datetime,
        *, cache_writeback: bool = True,
    ) -> list:
        """Sync score-trim: sort by (evidence_score, importance) DESC, keep
        entries whose accumulated `count_tokens(text)` ≤ `budget`. Stops at
        the first entry that would push past the cap (lower-score remainder
        is dropped — see §3.6.3).

        `entries` is a list of dicts (no entity tagging — caller sorts/keys
        as needed). Returns the kept subset preserving the score-DESC order.

        `cache_writeback`: default True writes `token_count` fields back
        onto each entry for across-render reuse (persona path — entries
        live in `_personas`). Pass False for reflection entries, which are
        loaded fresh from disk every render and would have no persistent
        view to cache against; writing cache fields there would be
        misleading and pollute reflection.json on the next save.
        """
        sorted_entries = sorted(
            entries,
            key=lambda e: (
                evidence_score(e, now),
                float(e.get('importance', 0) or 0),
            ),
            reverse=True,
        )
        kept = []
        total = 0
        for e in sorted_entries:
            t = cls._get_cached_token_count(e, writeback=cache_writeback)
            if total + t > budget:
                break
            kept.append(e)
            total += t
        return kept

    @classmethod
    async def _ascore_trim_entries(
        cls, entries: list, budget: int, now: datetime,
        *, cache_writeback: bool = True,
    ) -> list:
        """Async twin of `_score_trim_entries`. Identical math; the only
        difference is `acount_tokens` (worker-thread tiktoken). See the
        sync twin for the `cache_writeback` contract."""
        sorted_entries = sorted(
            entries,
            key=lambda e: (
                evidence_score(e, now),
                float(e.get('importance', 0) or 0),
            ),
            reverse=True,
        )
        kept = []
        total = 0
        for e in sorted_entries:
            t = await cls._aget_cached_token_count(e, writeback=cache_writeback)
            if total + t > budget:
                break
            kept.append(e)
            total += t
        return kept

    def _split_persona_for_render(
        self, persona: dict,
    ) -> tuple[list[tuple[str, dict]], dict[str, list[dict]]]:
        """Phase 1 (RFC §3.6.2): split entries into:
          - `protected_entries`: list[(entity_key, entry)] — character_card
            sources, never trimmed (§3.5.7 + §3.6.1).
          - `non_protected_by_entity`: {entity_key: [entry, ...]} — the
            score-trim candidate pool (suppressed entries excluded; they go
            to the dedicated "暂不主动提及" ("not proactively mentioned for
            now") section in compose).
        """  # noqa: DOCSTRING_CJK
        protected_entries: list[tuple[str, dict]] = []
        non_protected_by_entity: dict[str, list[dict]] = defaultdict(list)
        for entity_key, section in persona.items():
            if not isinstance(section, dict):
                continue
            for entry in section.get('facts', []):
                if not isinstance(entry, dict):
                    # Pre-PR-1 schema sometimes stored facts as bare
                    # strings; the legacy render path (`_render_fact_entries`)
                    # used to emit them. Normalize ad-hoc here so they keep
                    # appearing in prompt context until a write touches the
                    # entry and migrates it to dict form via _normalize_entry.
                    if entry:
                        entry = {
                            'text': str(entry),
                            'protected': False,
                            'suppress': False,
                            'reinforcement': 0.0,
                            'disputation': 0.0,
                            'rein_last_signal_at': None,
                            'disp_last_signal_at': None,
                            'sub_zero_days': 0,
                            'user_fact_reinforce_count': 0,
                        }
                        non_protected_by_entity[entity_key].append(entry)
                    continue
                if entry.get('suppress'):
                    # Suppressed entries are rendered in their own section
                    # (compose phase) — they don't compete with protected/
                    # non-protected for budget.
                    continue
                if entry.get('protected'):
                    protected_entries.append((entity_key, entry))
                else:
                    non_protected_by_entity[entity_key].append(entry)
        return protected_entries, dict(non_protected_by_entity)

    @staticmethod
    def _filter_reflections_for_render(
        reflections: list[dict] | None, persona: dict,
        suppressed_text_set: set[str],
    ) -> list[dict]:
        """Drop reflections whose text matches a suppressed persona entry
        (existing semantic — see `_is_suppressed_text` callers below)."""
        if not reflections:
            return []
        out = []
        for r in reflections:
            if not isinstance(r, dict):
                continue
            text = r.get('text', '')
            if not text:
                continue
            if text in suppressed_text_set:
                continue
            out.append(r)
        return out

    def _compose_markdown_from_trimmed(
        self, name: str, persona: dict, name_mapping: dict,
        protected_entries: list[tuple[str, dict]],
        trimmed_non_protected: list[dict],
        non_protected_entity_index: dict[int, str],
        trimmed_pending_reflections: list[dict],
        trimmed_confirmed_reflections: list[dict],
    ) -> str:
        """Phase 3 (RFC §3.6.2): emit markdown sections in stable order.

        Headers: the literal `关于主人` / `关于{ai_name}` / `关系动态` entity
        sections, the two reflection sections, and the suppressed section.
        Within each entity section: protected entries first (deterministic
        order from persona file) then non-protected kept by score-trim,
        preserving the trim-order (which is score DESC).
        """  # noqa: DOCSTRING_CJK
        master_name = name_mapping.get('human', '主人')
        ai_name = name
        _headers = {
            'master': f"关于{master_name}",
            'neko': f"关于{ai_name}",
            'relationship': "关系动态",
        }

        # Suppressed entries always render (small + the whole point is "AI
        # remembers but won't volunteer it"); not budget-counted.
        suppressed_lines: list[str] = []
        for entry in self._collect_all_entries(persona):
            if isinstance(entry, dict) and entry.get('suppress'):
                text = entry.get('text', '')
                if text:
                    suppressed_lines.append(f"- {text}")

        # Group kept entries by entity_key so each section is contiguous.
        # `non_protected_entity_index[id(entry)]` was populated by caller
        # to remember which entity each non-protected entry came from
        # (score-trim sorts globally so we lose that info).
        per_entity: dict[str, list[dict]] = defaultdict(list)
        for ek, entry in protected_entries:
            per_entity[ek].append(entry)
        for entry in trimmed_non_protected:
            ek = non_protected_entity_index.get(id(entry))
            if ek:
                per_entity[ek].append(entry)

        sections: list[str] = []
        # Iterate persona's natural key order so output is stable
        # regardless of which entries got trimmed.
        for entity_key in persona.keys():
            entries = per_entity.get(entity_key)
            if not entries:
                continue
            lines = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                text = entry.get('text', '')
                if text:
                    lines.append(f"- {text}")
            if lines:
                header = _headers.get(entity_key, entity_key)
                sections.append(f"### {header}\n" + "\n".join(lines))

        if trimmed_pending_reflections:
            lines = [f"- {r.get('text', '')}" for r in trimmed_pending_reflections
                     if r.get('text')]
            if lines:
                sections.append(
                    f"### {ai_name}最近的印象（还不太确定）\n" + "\n".join(lines)
                )

        # Split confirmed reflections into active vs past at render time.
        # Past = derived (state/episode 超 TTL) or stored 'past'。Pending
        # reflections不参与 past 拆分（pending 本就是"还不太确定"，自身已
        # 带不确定语义；要么被信号 reinforce 升 confirmed，要么被低分归档，
        # 不需要再叠一层过时降级）。
        from memory.temporal import (
            is_past_for_render as _is_past,
            time_since_label as _time_label,
        )
        now_for_past = datetime.now()
        active_confirmed: list[dict] = []
        past_confirmed: list[dict] = []
        for r in trimmed_confirmed_reflections:
            if not r.get('text'):
                continue
            (past_confirmed if _is_past(r, now=now_for_past) else active_confirmed).append(r)

        if active_confirmed:
            lines = [f"- {r.get('text', '')}" for r in active_confirmed]
            sections.append(
                f"### {ai_name}比较确定的印象\n" + "\n".join(lines)
            )

        if past_confirmed:
            # 过时 block — 用本项目六等号 below/above 对偶分隔符（参见
            # feedback_prompt_delimiters_above_below.md：分隔符内部禁冒号
            # 和破折号）。每条前缀 [X 天前 / X 周前 / X 月前] 由
            # time_since_label 按 0-6d / 7-29d / 30d+ 三档生成。整段按
            # get_global_language() 本地化（Codex review on PR #1316
            # P2 catch：之前硬编码 zh 让非 zh locale 看到中文时间标签）。
            from utils.language_utils import get_global_language
            from config.prompts.prompts_memory import render_past_memory_block
            lang = get_global_language()
            past_lines = []
            for r in past_confirmed:
                anchor = (
                    r.get('event_end_at')
                    or r.get('event_start_at')
                    or r.get('created_at')
                )
                label = _time_label(anchor, now=now_for_past, lang=lang)
                prefix = f"[{label}] " if label else ""
                past_lines.append(f"- {prefix}{r.get('text', '')}")
            sections.append(
                render_past_memory_block(
                    lang=lang,
                    ai_name=ai_name,
                    master_name=master_name,
                    items_text="\n".join(past_lines),
                )
            )

        if suppressed_lines:
            sections.append(
                f"### 暂不主动提及的内容（{ai_name}记得，但最近提到太多次了，不要再主动提起）\n"
                + "\n".join(suppressed_lines)
            )

        return "\n\n".join(sections) if sections else ""

    def _suppressed_text_set(self, persona: dict) -> set[str]:
        out: set[str] = set()
        for entry in self._collect_all_entries(persona):
            if isinstance(entry, dict) and entry.get('suppress'):
                t = entry.get('text', '')
                if t:
                    out.add(t)
        return out

    def _compose_persona_markdown(
        self, name: str, persona: dict, name_mapping: dict,
        pending_reflections: list[dict] | None,
        confirmed_reflections: list[dict] | None,
    ) -> str:
        """Sync 3-phase render path. Used by `render_persona_markdown` and
        any test/migration caller that doesn't have an event loop."""
        now = datetime.now()

        protected_entries, non_protected_by_entity = (
            self._split_persona_for_render(persona)
        )

        # Build entity-index by id() so we can regroup after the (entity-
        # blind) score-trim. Using id() is safe because we never mutate
        # entries during render — they're the same objects throughout.
        non_protected_entity_index: dict[int, str] = {}
        flat_non_protected: list[dict] = []
        for ek, entries in non_protected_by_entity.items():
            for e in entries:
                non_protected_entity_index[id(e)] = ek
                flat_non_protected.append(e)

        trimmed_non_protected = self._score_trim_entries(
            flat_non_protected, PERSONA_RENDER_MAX_TOKENS, now,
        )

        suppressed_text_set = self._suppressed_text_set(persona)
        trimmed_reflections_combined = self._score_trim_entries(
            self._filter_reflections_for_render(
                (pending_reflections or []) + (confirmed_reflections or []),
                persona, suppressed_text_set,
            ),
            REFLECTION_RENDER_MAX_TOKENS, now,
            # Reflections have no `_personas`-style in-memory view — they're
            # always loaded fresh from disk. Writing cache fields onto the
            # transient dicts would be garbage-collected on render exit and
            # could only pollute reflection.json on the next save.
            cache_writeback=False,
        )
        # Preserve the score-DESC order produced by _score_trim_entries.
        # The previous implementation filtered the ORIGINAL source lists by
        # id-membership in `trimmed_reflections_combined`, which lost the
        # sort order and emitted reflections in caller-supplied order. Fix:
        # iterate the already-sorted `trimmed_reflections_combined` and
        # split back into pending/confirmed by source-list membership
        # (CodeRabbit PR #936 round-4 Minor).
        trimmed_pending, trimmed_confirmed = self._partition_trimmed_reflections(
            trimmed_reflections_combined, pending_reflections, suppressed_text_set,
        )

        return self._compose_markdown_from_trimmed(
            name, persona, name_mapping,
            protected_entries, trimmed_non_protected,
            non_protected_entity_index,
            trimmed_pending, trimmed_confirmed,
        )

    @staticmethod
    def _partition_trimmed_reflections(
        trimmed_combined: list[dict],
        pending_source: list[dict] | None,
        suppressed_text_set: set[str],
    ) -> tuple[list[dict], list[dict]]:
        """Split score-sorted combined trim output back into
        (pending, confirmed) while preserving the sort order.

        Membership in `pending_source` decides pending vs confirmed; all
        entries not in `pending_source` are treated as confirmed (matches
        the original construction where the combined list was
        `pending + confirmed`). Suppressed entries are dropped defensively
        (the trim input already filtered them, but keep the guard so the
        render output never leaks suppressed text).
        """
        pending_ids = {id(r) for r in (pending_source or [])}
        trimmed_pending: list[dict] = []
        trimmed_confirmed: list[dict] = []
        for r in trimmed_combined:
            if r.get('text') in suppressed_text_set:
                continue
            if id(r) in pending_ids:
                trimmed_pending.append(r)
            else:
                trimmed_confirmed.append(r)
        return trimmed_pending, trimmed_confirmed

    def render_persona_markdown(self, name: str, pending_reflections: list[dict] | None = None,
                                   confirmed_reflections: list[dict] | None = None) -> str:
        """Render persona as markdown for LLM context injection.

        Suppressed entries are rendered in a separate "暂不主动提及" ("not
        proactively mentioned for now") section, NOT in their original
        sections. suppress has highest priority.
        """  # noqa: DOCSTRING_CJK
        # Refresh suppressions before rendering so expired cooldowns are released
        self.update_suppressions(name)
        persona = self.ensure_persona(name)
        _, _, _, _, name_mapping, _, _, _, _ = self._config_manager.get_character_data()
        return self._compose_persona_markdown(
            name, persona, name_mapping, pending_reflections, confirmed_reflections,
        )

    async def arender_persona_markdown(
        self, name: str,
        pending_reflections: list[dict] | None = None,
        confirmed_reflections: list[dict] | None = None,
    ) -> str:
        """Async 3-phase render path. Production hot path — uses
        `acount_tokens` so the event loop doesn't stall on tiktoken IO."""
        await self.aupdate_suppressions(name)
        persona = await self.aensure_persona(name)
        _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        now = datetime.now()

        protected_entries, non_protected_by_entity = (
            self._split_persona_for_render(persona)
        )

        non_protected_entity_index: dict[int, str] = {}
        flat_non_protected: list[dict] = []
        for ek, entries in non_protected_by_entity.items():
            for e in entries:
                non_protected_entity_index[id(e)] = ek
                flat_non_protected.append(e)

        trimmed_non_protected = await self._ascore_trim_entries(
            flat_non_protected, PERSONA_RENDER_MAX_TOKENS, now,
        )

        suppressed_text_set = self._suppressed_text_set(persona)
        trimmed_reflections_combined = await self._ascore_trim_entries(
            self._filter_reflections_for_render(
                (pending_reflections or []) + (confirmed_reflections or []),
                persona, suppressed_text_set,
            ),
            REFLECTION_RENDER_MAX_TOKENS, now,
            # See sync twin: reflections have no `_personas`-style
            # in-memory view, so we compute fresh every render without
            # writing cache fields back onto the transient dicts.
            cache_writeback=False,
        )
        # Preserve score-DESC order from _ascore_trim_entries — mirror of
        # the sync path fix in _compose_persona_markdown (CodeRabbit PR
        # #936 round-4 Minor).
        trimmed_pending, trimmed_confirmed = self._partition_trimmed_reflections(
            trimmed_reflections_combined, pending_reflections, suppressed_text_set,
        )

        return self._compose_markdown_from_trimmed(
            name, persona, name_mapping,
            protected_entries, trimmed_non_protected,
            non_protected_entity_index,
            trimmed_pending, trimmed_confirmed,
        )

    def _is_suppressed_text(self, persona: dict, text: str) -> bool:
        """Check if a given text matches any suppressed entry."""
        for entry in self._collect_all_entries(persona):
            if isinstance(entry, dict) and entry.get('suppress') and entry.get('text') == text:
                return True
        return False

    @staticmethod
    def _render_fact_entries(entries: list) -> list[str]:
        """Render the fact entry list. Suppressed entries are not rendered here (moved to the dedicated section)."""
        lines = []
        for entry in entries:
            if isinstance(entry, dict):
                if entry.get('suppress'):
                    continue  # suppress 的条目在专用区域渲染
                text = entry.get('text', '')
                if text:
                    lines.append(f"- {text}")
            elif entry:
                lines.append(f"- {entry}")
        return lines
