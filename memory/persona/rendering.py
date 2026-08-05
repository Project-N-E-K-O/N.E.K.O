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

from typing import NamedTuple

from config import (
    PERSONA_RENDER_MAX_TOKENS,
    PERSONA_RENDER_PROTECTED_MAX_ENTRIES,
    PERSONA_RENDER_SUPPRESSED_MAX_ENTRIES,
    REFLECTION_RENDER_MAX_TOKENS,
    SCOPED_RENDER_ENTRY_MARKUP_TOKENS,
    SCOPED_RENDER_SUBJECT_MIN_TOKENS,
    SCOPED_RENDER_TOTAL_MAX_TOKENS,
)

from memory.evidence import evidence_score

from ._shared import logger

from utils.tokenize import acount_tokens, count_tokens, tokenizer_identity

class _RenderPrep(NamedTuple):
    """Everything the sync and async render paths derive identically.

    Both paths used to inline the same eight statements; the async one is
    the production hot path and the sync one is what tests and migrations
    reach for, so a fix applied to only one of them silently missed
    whichever half the reviewer wasn't looking at. Only the token-counting
    step genuinely differs between them, so everything before and after it
    lives here (built once by ``_prepare_render``, consumed by
    ``_compose_from_prep``).

    ``subject_slots`` is the allocation order for scoped rendering: one
    entry per authorized PARTICIPANT, in the order the CALLER supplied, plus
    a trailing ``None`` slot when legacy-private rows are also allowed in.
    Empty means legacy mode — one shared pool, pre-existing behaviour.

    ``marker_aliases`` maps every member marker of a participant onto that
    participant's primary marker. It is empty (identity) unless the caller
    supplied participant groups, so every pre-existing caller renders exactly
    as before. With it, one person holding several accounts gets ONE budget
    slot, ONE ``### `` heading and ONE subject_id — rather than N slots whose
    headings would all print the same id.
    """

    persona_view: dict
    protected_entries: list
    non_protected_entity_index: dict
    flat_non_protected: list
    reflections: list
    suppressed_text_set: set
    subject_slots: tuple
    marker_aliases: dict = {}

class RenderingMixin:
    @staticmethod
    def _persona_view_for_subjects(
        persona: dict,
        subjects=None,
        *,
        include_legacy_private: bool | None = None,
    ) -> dict:
        """Return a shallow, scope-authorized persona view for rendering."""
        from memory.scopes import (
            SCOPED_PERSONA_PREFIX,
            normalize_subjects,
            persona_subject_from_section,
            subject_from_entry,
        )
        allowed = normalize_subjects(subjects)
        if include_legacy_private is None:
            include_legacy_private = not allowed
        allowed_keys = {(subject.key, subject.scope) for subject in allowed}
        view: dict = {}
        for section_key, section in persona.items():
            if not isinstance(section, dict):
                continue
            scoped_subject = persona_subject_from_section(section_key, section)
            if scoped_subject is None:
                if isinstance(section_key, str) and section_key.startswith(SCOPED_PERSONA_PREFIX):
                    # A malformed scoped section is never reclassified as
                    # legacy-private; fail closed so corrupt metadata cannot leak.
                    continue
                if include_legacy_private:
                    view[section_key] = section
                continue
            # 逐条授权而非按 section metadata 整段放行：section key 只含
            # kind:subject_id 不含 scope，同 kind/id 不同自定义 scope 的两个
            # 隔离域共享同一个 section，metadata 是"最后写入者"的 scope——
            # 按它放行会把 A 域条目渲染给 B（泄漏），按它拒绝会让 A 自己的
            # 条目随最后写入者隐身（对称翻转）。entry 写入时都带 subject 戳，
            # 以戳为准；无戳/损坏条目 fail-closed 掉队。metadata 仅保留上面
            # persona_subject_from_section 的损坏检查职责。
            if not allowed_keys:
                continue
            from memory.scopes import filter_entries_for_subjects

            facts = section.get('facts')
            if isinstance(facts, list):
                scoped_facts = filter_entries_for_subjects(
                    facts, allowed, include_legacy_private=False,
                )
                if not scoped_facts:
                    continue
                filtered = dict(section)
                filtered['facts'] = scoped_facts
                rendered_subjects = {
                    (entry_subject.key, entry_subject.scope)
                    for entry in scoped_facts
                    if (entry_subject := subject_from_entry(entry)) is not None
                }
                if rendered_subjects != {
                    (scoped_subject.key, scoped_subject.scope),
                }:
                    # Section metadata belongs to the last writer, while this
                    # view is authorized entry-by-entry. Do not carry that
                    # writer's label into another scope's rendered header (or
                    # into a mixed-scope header where no single label applies).
                    filtered.pop('display_name', None)
                view[section_key] = filtered
            elif (scoped_subject.key, scoped_subject.scope) in allowed_keys:
                view[section_key] = section
        return view

    @staticmethod
    def _renderable_text(entry) -> str:
        """The text this entry will actually contribute, or `""`.

        One definition, used by every place that counts or budgets
        entries, because "does this entry produce a line" was being
        answered three slightly different ways and each answer had its own
        hole: bucket truthiness charged markup for blanks, `if text:`
        accepted whitespace-only strings, and a bare `.strip()` crashed on
        a non-string.

        `str()` coerce is deliberate and load-bearing: facts.json /
        reflections.json round-trip through JSON, so in principle `text`
        is a string — but manual edits, pre-PR-1 leftovers and migration
        bugs all produce truthy non-strings (an epoch int is the classic).
        The compose path has always formatted those fine; a filter that
        raises on them would take the whole render down with it, and with
        it `/scoped_context` and `/new_dialog`. Same reasoning as the
        coerce in `main_logic/core/tool_calling.py`.
        """
        if not isinstance(entry, dict):
            return ""
        return str(entry.get('text') or '').strip()

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
        # Coerce, do not assume: a truthy non-string `text` (epoch int,
        # stray list) otherwise blows up in `_text_fingerprint` and takes
        # the whole render — /scoped_context and /new_dialog — with it.
        # No strip: this has to match what compose formats.
        text = str(entry.get('text') or '')
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
        # Coerce, do not assume: a truthy non-string `text` (epoch int,
        # stray list) otherwise blows up in `_text_fingerprint` and takes
        # the whole render — /scoped_context and /new_dialog — with it.
        # No strip: this has to match what compose formats.
        text = str(entry.get('text') or '')
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

    @staticmethod
    def _score_trim_sort(entries: list, now: datetime) -> list:
        """The (evidence_score, importance) DESC ordering both twins use."""
        return sorted(
            entries,
            key=lambda e: (
                evidence_score(e, now),
                float(e.get('importance', 0) or 0),
            ),
            reverse=True,
        )

    @classmethod
    def _score_trim_entries(
        cls, entries: list, budget: int, now: datetime,
        *, cache_writeback: bool = True, per_entry_overhead: int = 0,
        gate_budget: int | None = None,
    ) -> tuple[list, int]:
        """Sync score-trim: sort by (evidence_score, importance) DESC, keep
        entries whose accumulated cost stays within `budget`.

        An entry that does not fit is SKIPPED, not treated as a stop sign.
        The loop used to `break` there, which turned "the top-ranked entry
        is longer than the whole budget" into "the entire section
        disappears" — not a shortened persona, an absent one. A single
        over-long merged entry at rank 1 was enough to do it, and the
        lower-ranked entries that would have fitted never got a look.

        `entries` is a list of dicts (no entity tagging — caller sorts/keys
        as needed). Returns `(kept, tokens_used)`, the kept subset
        preserving the score-DESC order plus the tokens it consumed — the
        per-subject allocator needs the usage to hand what is left of the
        overall gate to the next subject.

        Two budgets, because the caller has two different ceilings and
        they are denominated differently:

        - `budget` is the per-subject pool, measured in entry TEXT. Same
          meaning in every mode, scoped or legacy, so one constant does
          not mean two things.
        - `gate_budget` (optional) is what is left of the scoped overall
          gate, measured in RENDERED tokens — text plus
          `per_entry_overhead`, the `- ` bullet and its newline that
          composition adds. With short facts that markup is most of the
          line, so a gate counting text alone is not a bound at all.

        An entry is taken only if it fits BOTH. Charging markup against
        the pool instead (the previous shape) quietly shrank every scoped
        subject's allowance below the number the constant advertises;
        selecting on text and charging markup afterwards (the shape before
        that) let one pool hand the next one tokens the gate no longer
        had. Legacy passes neither and is bit-for-bit unchanged.

        Returns the RENDERED cost, since that is what the gate is debited
        by; the pool is per-subject and reset each time.

        `cache_writeback`: default True writes `token_count` fields back
        onto each entry for across-render reuse (persona path — entries
        live in `_personas`). Pass False for reflection entries, which are
        loaded fresh from disk every render and would have no persistent
        view to cache against; writing cache fields there would be
        misleading and pollute reflection.json on the next save.
        """
        kept = []
        text_total = 0
        rendered_total = 0
        for e in cls._score_trim_sort(entries, now):
            text_cost = cls._get_cached_token_count(
                e, writeback=cache_writeback,
            )
            rendered_cost = text_cost + per_entry_overhead
            if text_total + text_cost > budget:
                continue
            if gate_budget is not None and rendered_total + rendered_cost > gate_budget:
                continue
            kept.append(e)
            text_total += text_cost
            rendered_total += rendered_cost
        return kept, rendered_total

    @classmethod
    async def _ascore_trim_entries(
        cls, entries: list, budget: int, now: datetime,
        *, cache_writeback: bool = True, per_entry_overhead: int = 0,
        gate_budget: int | None = None,
    ) -> tuple[list, int]:
        """Async twin of `_score_trim_entries`. Identical math; the only
        difference is `acount_tokens` (worker-thread tiktoken). See the
        sync twin for the skip-don't-stop rule, the two budgets,
        `per_entry_overhead` and the `cache_writeback` contract."""
        kept = []
        text_total = 0
        rendered_total = 0
        for e in cls._score_trim_sort(entries, now):
            text_cost = await cls._aget_cached_token_count(
                e, writeback=cache_writeback,
            )
            rendered_cost = text_cost + per_entry_overhead
            if text_total + text_cost > budget:
                continue
            if gate_budget is not None and rendered_total + rendered_cost > gate_budget:
                continue
            kept.append(e)
            text_total += text_cost
            rendered_total += rendered_cost
        return kept, rendered_total

    @classmethod
    def _split_persona_for_render(
        cls, persona: dict,
    ) -> tuple[list[tuple[str, dict]], dict[str, list[dict]]]:
        """Phase 1 (RFC §3.6.2): split entries into:
          - `protected_entries`: list[(entity_key, entry)] — character_card
            sources, never trimmed (§3.5.7 + §3.6.1).
          - `non_protected_by_entity`: {entity_key: [entry, ...]} — the
            score-trim candidate pool (suppressed entries excluded; they go
            to the dedicated "暂不主动提及" ("not proactively mentioned for
            now") section in compose).

        Protected entries deliberately bypass the token budget (trimming a
        character-card line is a personality break, which is worse than
        losing a memory), but "exempt from the budget" must not mean
        "unbounded": a bulk card import or a runaway migration could plant
        hundreds. They get a count cap instead, and going over it is
        logged rather than swallowed.
        """  # noqa: DOCSTRING_CJK
        protected_entries: list[tuple[str, dict]] = []
        non_protected_by_entity: dict[str, list[dict]] = defaultdict(list)
        for entity_key, section in persona.items():
            if not isinstance(section, dict):
                continue
            for entry in section.get('facts', []):
                if not isinstance(entry, dict):
                    # Pre-PR-1 schema sometimes stored facts as bare
                    # strings; the legacy render path used to emit them.
                    # Normalize ad-hoc here so they keep appearing in
                    # prompt context until a write touches the entry and
                    # migrates it to dict form via _normalize_entry.
                    # Blank check matches the dict path's _renderable_text
                    # (strip, not truthiness): a whitespace-only bare
                    # string is truthy, and promoting it would sail past
                    # the blank gate below and render an empty bullet.
                    # _renderable_text itself returns "" for every
                    # non-dict, so the string must be judged before the
                    # promotion, not through that helper. isinstance 限定
                    # str：裸 str() 会把 None/False/0 变成非空文本
                    # "None"/"False"/"0" 渲染出来，而 legacy 数据形态
                    # 只有裸字符串这一种。
                    if isinstance(entry, str) and entry.strip():
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
                if not cls._renderable_text(entry):
                    # Emits no line, so it must not occupy budget either.
                    # A blank placeholder used to cost a full markup
                    # charge against the scoped gate and could push the
                    # next subject under the floor while producing
                    # nothing at all.
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

    @classmethod
    def _cap_protected_entries(cls, protected_entries: list) -> list:
        """Trim the protected list to its count cap, loudly.

        Applied AFTER skipped subjects are filtered out, not at split
        time. Capping first spends the allowance on entries that a later
        allocator decision throws away, and the ones it displaced cannot
        be recovered — a bulk card import on a subject that never renders
        would silently take another subject's character-card lines with
        it.

        Blank entries never reach here: `_split_persona_for_render` is the
        sole producer of the `protected_entries` this is called with, and
        it already drops them at the `_renderable_text` check. This used to
        repeat that filter, which read like a second line of defence but
        was unreachable — the reason it matters is that the guard for the
        blank rule then had no anchor in the layer that actually enforces
        it (see `test_blank_protected_entries_do_not_spend_the_count_cap`).
        """
        if len(protected_entries) <= PERSONA_RENDER_PROTECTED_MAX_ENTRIES:
            return protected_entries
        logger.warning(
            f"[Persona] protected 条目 {len(protected_entries)} 条超过渲染"
            f"上限 {PERSONA_RENDER_PROTECTED_MAX_ENTRIES}，尾部 "
            f"{len(protected_entries) - PERSONA_RENDER_PROTECTED_MAX_ENTRIES}"
            f" 条本轮不渲染（protected 不吃 token 预算，只能按条数封顶）"
        )
        return protected_entries[:PERSONA_RENDER_PROTECTED_MAX_ENTRIES]

    @classmethod
    def _filter_reflections_for_render(
        cls,
        reflections: list[dict] | None, persona: dict,
        suppressed_text_set: set[str],
        subjects=None,
        include_legacy_private: bool | None = None,
    ) -> list[dict]:
        """Drop reflections whose text matches a suppressed persona entry
        (existing semantic — see `_is_suppressed_text` callers below).

        Blank-rejection goes through `_renderable_text`, the same single
        definition the persona split and the suppressed section use (those
        two plus this one are its only callers — the protected cap used to
        be a fourth, until this PR deleted that repeat as unreachable).
        This branch used to run its own `if not text:`, which
        accepts a whitespace-only string: the reflection then entered the
        bucket, was charged text + markup against the overall gate, and
        composed into an empty `- ` bullet. Paying the gate for a line that
        says nothing is exactly what can push the next subject under
        `SCOPED_RENDER_SUBJECT_MIN_TOKENS`.

        Only the blank decision moves to the shared definition. The
        suppression match still compares the RAW text, because
        `_suppressed_text_set` is built from raw text and
        `_partition_trimmed_reflections` matches against it the same way —
        stripping on one side of that comparison and not the other is how
        the two spellings would start disagreeing about which reflections
        are suppressed.
        """
        if not reflections:
            return []
        from memory.scopes import filter_entries_for_subjects
        out = []
        for r in filter_entries_for_subjects(
            reflections,
            subjects,
            include_legacy_private=include_legacy_private,
        ):
            if not isinstance(r, dict):
                continue
            if not cls._renderable_text(r):
                continue
            if r.get('text', '') in suppressed_text_set:
                continue
            out.append(r)
        return out

    @staticmethod
    def _renders_scoped_only(subjects=None, include_legacy_private=None) -> bool:
        """True when this render may only show scoped subjects.

        Same derivation as `filter_entries_for_subjects` /
        `_persona_view_for_subjects`, kept in one place so the rendered
        prose can't disagree with what the filters actually let through:
        subjects supplied and legacy-private rows excluded."""
        from memory.scopes import normalize_subjects

        allowed = normalize_subjects(subjects)
        if include_legacy_private is None:
            include_legacy_private = not allowed
        return bool(allowed) and not include_legacy_private

    def _compose_markdown_from_trimmed(
        self, name: str, persona: dict, name_mapping: dict,
        protected_entries: list[tuple[str, dict]],
        trimmed_non_protected: list[dict],
        non_protected_entity_index: dict[int, str],
        trimmed_pending_reflections: list[dict],
        trimmed_confirmed_reflections: list[dict],
        *,
        scoped_only: bool = False,
        skipped: set | None = None,
        marker_aliases: dict | None = None,
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
        from config.prompts.prompts_memory import get_persona_section_header
        from utils.language_utils import get_global_language_full
        render_lang = get_global_language_full()
        _headers = {
            section: get_persona_section_header(
                section,
                render_lang,
                ai_name=ai_name,
                master_name=master_name,
            )
            for section in ('master', 'neko', 'relationship')
        }

        # Suppressed entries always render (the whole point is "AI
        # remembers but won't volunteer it", and a half-listed do-not-
        # mention list is worse than none); not budget-counted. Capped by
        # COUNT so "exempt from the token budget" can't become "unbounded"
        # — a long suppression cooldown on a chatty character otherwise
        # grows this section without any ceiling at all.
        suppressed_lines: list[str] = []
        suppressed_total = 0
        seen_suppressed: set[str] = set()
        for entry in self._collect_all_entries(persona):
            if isinstance(entry, dict) and entry.get('suppress'):
                if self._entry_is_skipped(entry, skipped):
                    # 该 subject 整段被跳过了，它的免预算段也不能单独露出来。
                    continue
                text = self._renderable_text(entry)
                if text:
                    if text in seen_suppressed:
                        # 同一条事实可能同时存在于群和多个成员 subject 下，
                        # 而这一段是不分 subject 的统一标题。逐份计数会让
                        # 重复项占满条数上限，把真正另一条「别主动提」挤出
                        # prompt——模型于是主动提起了那件事。
                        continue
                    seen_suppressed.add(text)
                    suppressed_total += 1
                    if len(suppressed_lines) < PERSONA_RENDER_SUPPRESSED_MAX_ENTRIES:
                        suppressed_lines.append(f"- {text}")
        if suppressed_total > PERSONA_RENDER_SUPPRESSED_MAX_ENTRIES:
            logger.warning(
                f"[Persona] suppressed 条目 {suppressed_total} 条超过渲染上限 "
                f"{PERSONA_RENDER_SUPPRESSED_MAX_ENTRIES}，尾部 "
                f"{suppressed_total - PERSONA_RENDER_SUPPRESSED_MAX_ENTRIES} 条"
                f"本轮不渲染（suppressed 不吃 token 预算，只能按条数封顶）"
            )

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

        # Fold sections that belong to the same PARTICIPANT into one heading.
        # Without this a person with two accounts gets two ``### `` blocks whose
        # ids are both rewritten to the primary — the model would read the same
        # id twice and take it for two people.
        section_group = self._section_group_keys(persona, marker_aliases)
        sections: list[str] = []
        emitted_groups: set = set()
        # Iterate persona's natural key order so output is stable
        # regardless of which entries got trimmed.
        for entity_key in persona.keys():
            group_key = section_group.get(entity_key, entity_key)
            if group_key in emitted_groups:
                continue
            member_keys = [
                key for key in persona.keys()
                if section_group.get(key, key) == group_key
            ]
            entries: list = []
            for key in member_keys:
                entries.extend(per_entity.get(key) or ())
            if not entries:
                continue
            emitted_groups.add(group_key)
            lines = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                # 与拆分侧同一空白判据（#2578 修了 reflection 那半，这里是
                # persona 半）：裸 truthiness 会把 '   ' 渲成空 bullet。
                if self._renderable_text(entry):
                    lines.append(f"- {entry.get('text', '')}")
            if lines:
                # The heading always names the PRIMARY section of the group —
                # for a single-section group that is the section itself, so
                # nothing changes for every pre-existing caller.
                primary_key = self._primary_section_key(
                    persona, member_keys, marker_aliases,
                )
                section_meta = persona.get(primary_key, {})
                subject_kind = section_meta.get('subject_kind')
                subject_id = section_meta.get('subject_id')
                if subject_kind in (
                    'group_chat', 'participant', 'group_participant',
                ):
                    from config.prompts.prompts_memory import (
                        get_scoped_persona_section_header,
                    )
                    from memory.facts import FactStore
                    # display_name 是不可信用户输入（群名/群名片），路由入口
                    # 已中和过一次，这里再过一次——渲染是唯一把它拼进 prompt
                    # 的地方，而 persona.json 可被手改（与 speaker_label 的
                    # 双侧中和同一道理，#2605）。中和后为空按无名回退。
                    display_name = FactStore.sanitize_speaker_label(
                        self._group_display_name(
                            persona, member_keys, primary_key,
                        ),
                    )
                    header = get_scoped_persona_section_header(
                        subject_kind, subject_id, render_lang,
                        display_name=display_name or None,
                    )
                else:
                    header = _headers.get(primary_key, primary_key)
                sections.append(f"### {header}\n" + "\n".join(lines))

        if trimmed_pending_reflections:
            lines = [f"- {r.get('text', '')}" for r in trimmed_pending_reflections
                     if r.get('text')]
            if lines:
                sections.append(
                    "### "
                    + get_persona_section_header(
                        "pending_reflections",
                        render_lang,
                        ai_name=ai_name,
                        master_name=master_name,
                    )
                    + "\n"
                    + "\n".join(lines)
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
                "### "
                + get_persona_section_header(
                    "confirmed_reflections",
                    render_lang,
                    ai_name=ai_name,
                    master_name=master_name,
                )
                + "\n"
                + "\n".join(lines)
            )

        if past_confirmed:
            # 过时 block — 用本项目六等号 below/above 对偶分隔符（参见
            # feedback_prompt_delimiters_above_below.md：分隔符内部禁冒号
            # 和破折号）。每条前缀 [X 天前 / X 周前 / X 月前] 由
            # time_since_label 按 0-6d / 7-29d / 30d+ 三档生成。整段按
            # get_global_language_full() 本地化（Codex review on PR #1316
            # P2 catch：之前硬编码 zh 让非 zh locale 看到中文时间标签）。
            from config.prompts.prompts_memory import render_past_memory_block
            lang = render_lang
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
                    # 群/成员 subject 的渲染里点名私聊对象是双重错误：名字
                    # 泄漏进群 prompt，指令对象也不是群里的人。
                    scoped_only=scoped_only,
                )
            )

        if suppressed_lines:
            sections.append(
                "### "
                + get_persona_section_header(
                    "suppressed",
                    render_lang,
                    ai_name=ai_name,
                    master_name=master_name,
                )
                + "\n"
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

    # ------------------------------------------------------------------
    # Budget allocation across subjects (§3.6 + group-memory PR-2)
    # ------------------------------------------------------------------

    @staticmethod
    def _subject_render_slots(
        subjects=None, include_legacy_private: bool | None = None,
        participant_groups=None,
    ) -> tuple:
        """Allocation order for a scoped render, or `()` for legacy mode.

        The order is the CALLER's, verbatim. The plugin currently sends
        `[group, current speaker]` and a later PR widens it to
        `[group, current speaker, the last three other speakers]`; deciding
        here who matters would silently override the only layer that knows.

        With `participant_groups` supplied, one slot per PARTICIPANT rather
        than per subject: a person's several accounts share one budget, one
        heading and one id. Folding can only ever shrink the slot list, so
        every wire-level `1..8` check still holds.

        A trailing `None` slot carries legacy-private rows whenever the
        caller opted them in alongside subjects — without it those rows
        would be filtered INTO the view and then dropped by an allocator
        that has no bucket for them.
        """
        from memory.scopes import normalize_subjects

        allowed = normalize_subjects(subjects)
        if not allowed:
            # Legacy: one shared pool, exactly as before scoped memory.
            return ()
        if include_legacy_private is None:
            include_legacy_private = not allowed
        if participant_groups:
            slots = [group.primary for group in participant_groups]
        else:
            slots = list(allowed)
        if include_legacy_private:
            slots.append(None)
        return tuple(slots)

    @staticmethod
    def _section_group_keys(persona: dict, marker_aliases: dict | None) -> dict:
        """`entity_key -> rendering group key`. Identity without aliases."""
        if not marker_aliases:
            return {}
        from memory.scopes import persona_subject_from_section

        mapping: dict = {}
        for section_key, section in persona.items():
            if not isinstance(section, dict):
                continue
            subject = persona_subject_from_section(section_key, section)
            if subject is None:
                continue
            alias = marker_aliases.get((subject.key, subject.scope))
            if alias is not None:
                mapping[section_key] = alias
        return mapping

    @staticmethod
    def _primary_section_key(
        persona: dict, member_keys: list, marker_aliases: dict | None,
    ) -> str:
        """The section whose subject IS the participant's primary marker."""
        if len(member_keys) == 1 or not marker_aliases:
            return member_keys[0]
        from memory.scopes import persona_subject_from_section

        for key in member_keys:
            section = persona.get(key)
            if not isinstance(section, dict):
                continue
            subject = persona_subject_from_section(key, section)
            if subject is None:
                continue
            marker = (subject.key, subject.scope)
            if marker_aliases.get(marker) == marker:
                return key
        return member_keys[0]

    @staticmethod
    def _group_display_name(
        persona: dict, member_keys: list, primary_key: str,
    ) -> str | None:
        """Name for a folded heading.

        Primary's name wins; otherwise the group's single non-empty name; two
        different names mean NO name — the same discipline
        ``_persona_view_for_subjects`` already applies when it drops
        ``display_name`` for a mixed-scope section rather than printing one
        writer's label over another's rows.
        """
        primary_name = str(
            (persona.get(primary_key) or {}).get('display_name') or ''
        ).strip()
        if primary_name:
            return primary_name
        names = {
            name for name in (
                str(
                    (persona.get(key) or {}).get('display_name') or ''
                ).strip()
                for key in member_keys
            ) if name
        }
        return next(iter(names)) if len(names) == 1 else None

    @staticmethod
    def _subject_marker_aliases(participant_groups=None) -> dict:
        """`member marker -> primary marker` for every supplied participant.

        One-to-one by construction: the resolver folds at request level so no
        two groups share a participant, and it never truncates a group's
        member set, so no marker can belong to two groups.
        """
        aliases: dict = {}
        for group in participant_groups or ():
            primary_marker = (group.primary.key, group.primary.scope)
            for member in group.members:
                aliases[(member.key, member.scope)] = primary_marker
        return aliases

    @staticmethod
    def _subject_bucket_marker(subject, aliases: dict | None = None):
        """`(key, scope)` for a subject slot; `None` for the legacy slot.

        Mirrors what `filter_entries_for_subjects` matches on, so an entry
        lands in the same bucket the authorization check used — then folds
        through `aliases` so every account of one person shares a bucket.
        """
        if subject is None:
            return None
        marker = (subject.key, subject.scope)
        return (aliases or {}).get(marker, marker)

    @classmethod
    def _bucket_entries_by_subject(cls, entries, aliases: dict | None = None) -> dict:
        """Group already-authorized entries by the stamp on the entry.

        Bucketing on the entry's own subject rather than on its persona
        section key matters: a section key is only `kind:subject_id`, so
        two subjects that share a kind/id but sit in different custom
        scopes share one section. Keying by section would merge their
        budgets back together — the very thing this split exists to stop.
        """
        from memory.scopes import subject_from_entry

        buckets: dict = defaultdict(list)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            # Through the shared helper, not a second copy of the same
            # expression: `_log_unslotted_buckets` builds its `known` set
            # from that helper, so two spellings that drift apart would
            # make every entry look unslotted (or hide a real drop).
            marker = cls._subject_bucket_marker(
                subject_from_entry(entry), aliases,
            )
            buckets[marker].append(entry)
        return buckets

    @classmethod
    def _log_skipped_subject(cls, subject, available: int) -> None:
        label = "legacy" if subject is None else subject.key
        logger.warning(
            f"[Persona] scoped 渲染总闸剩余 {available} tok，低于单 subject 下限 "
            f"{SCOPED_RENDER_SUBJECT_MIN_TOKENS}，subject {label} 整段跳过"
            f"（半截的人设比缺席更糟）"
        )

    @classmethod
    def _log_unslotted_buckets(
        cls, slots: tuple, *bucket_maps, aliases: dict | None = None,
    ) -> None:
        """Loudly report entries that passed authorization but got no slot.

        Should be unreachable: everything in the view matched one of the
        caller's subjects. If it ever fires, memories are vanishing between
        the filter and the allocator, and a silent drop is exactly the kind
        of thing that only surfaces as "the character forgot things".
        """
        known = {cls._subject_bucket_marker(s, aliases) for s in slots}
        for buckets in bucket_maps:
            for marker, entries in buckets.items():
                if marker not in known and entries:
                    logger.warning(
                        f"[Persona] scoped 渲染有 {len(entries)} 条已授权条目"
                        f"没有对应 subject 槽位（marker={marker}），本轮丢失"
                    )

    @classmethod
    def _sort_kept_reflections(cls, kept: list, now: datetime) -> list:
        """Restore the global `(evidence_score, importance)` DESC order the
        reflection sections are read in.

        Per-subject allocation hands back one trimmed list per slot and the
        allocator concatenates them, so the flat list comes out ordered by
        the CALLER's subject order first and by score only within a slot.
        Allocation order is deliberately the caller's — that is the whole
        contract of `_trim_scoped_by_subject` — but the reflection sections
        render as a single flat bullet list under one heading, with no
        subject boundary the model can see. Leaving the concatenation order
        in place puts a low-confidence impression of the group above a
        high-confidence one about the person actually being replied to, and
        the model reads position as confidence.

        Sort only: everything here already survived its slot's trim, so
        re-running the trim would be re-charging a budget that is spent.
        The legacy path never needed this — it trims one global pool and
        comes out sorted already.
        """
        return cls._score_trim_sort(kept, now)

    @classmethod
    def _trim_scoped_by_subject(
        cls, prep: _RenderPrep, now: datetime,
    ) -> tuple[list, list, set]:
        """Sync per-subject allocation under the overall scoped gate.

        Each subject gets its own `PERSONA_RENDER_MAX_TOKENS` /
        `REFLECTION_RENDER_MAX_TOKENS` instead of every subject in the
        render fighting over one shared pair, and the sum is held down by
        `SCOPED_RENDER_TOTAL_MAX_TOKENS`. Whatever a subject leaves unspent
        rolls forward to the next one, in the caller's order — no subject
        kind gets a reserved slice or otherwise jumps the queue.
        """
        aliases = prep.marker_aliases
        persona_buckets = cls._bucket_entries_by_subject(
            prep.flat_non_protected, aliases,
        )
        reflection_buckets = cls._bucket_entries_by_subject(
            prep.reflections, aliases,
        )
        kept_persona: list = []
        kept_reflections: list = []
        skipped: set = set()
        remaining = SCOPED_RENDER_TOTAL_MAX_TOKENS
        for subject in prep.subject_slots:
            marker = cls._subject_bucket_marker(subject, aliases)
            # Pure caller order: whatever the gate still holds, in the
            # order the caller listed. A subject that wants priority is
            # listed earlier — that is the whole contract, and it is the
            # one every caller already follows (group first). An earlier
            # attempt gave group subjects a reserved slice so they could
            # outrank members ahead of them; it was dead code for every
            # shipped and planned caller and each of its interactions
            # (multiple groups, empty groups, exempt-only groups, unspent
            # slices flowing to later slots) was its own way to invert the
            # order it was meant to protect. Deleted rather than patched
            # a sixth time.
            available = max(0, remaining)
            if available < SCOPED_RENDER_SUBJECT_MIN_TOKENS:
                if persona_buckets.get(marker) or reflection_buckets.get(marker):
                    # Has budgeted content it cannot afford → drop it whole,
                    # exempt sections included, rather than emit a fragment.
                    cls._log_skipped_subject(subject, available)
                    skipped.add(marker)
                # Nothing budgeted to drop: whatever exempt sections this
                # slot has cost the gate nothing and still render.
                continue
            persona_kept, persona_used = cls._score_trim_entries(
                persona_buckets.get(marker, ()),
                PERSONA_RENDER_MAX_TOKENS, now,
                per_entry_overhead=SCOPED_RENDER_ENTRY_MARKUP_TOKENS,
                gate_budget=available,
            )
            # Both counters move by the CHARGED cost. Subtracting only the
            # text from `available` would let the reflection pool spend
            # tokens the gate has already run out of.
            remaining -= persona_used
            available = max(0, available - persona_used)
            reflection_kept, reflection_used = cls._score_trim_entries(
                reflection_buckets.get(marker, ()),
                REFLECTION_RENDER_MAX_TOKENS, now,
                cache_writeback=False,
                per_entry_overhead=SCOPED_RENDER_ENTRY_MARKUP_TOKENS,
                gate_budget=available,
            )
            remaining -= reflection_used
            kept_persona.extend(persona_kept)
            kept_reflections.extend(reflection_kept)
        cls._log_unslotted_buckets(
            prep.subject_slots, persona_buckets, reflection_buckets,
            aliases=aliases,
        )
        return kept_persona, cls._sort_kept_reflections(kept_reflections, now), skipped

    @classmethod
    async def _atrim_scoped_by_subject(
        cls, prep: _RenderPrep, now: datetime,
    ) -> tuple[list, list, set]:
        """Async twin of `_trim_scoped_by_subject` — same allocation, only
        the token counter differs (worker-thread tiktoken)."""
        aliases = prep.marker_aliases
        persona_buckets = cls._bucket_entries_by_subject(
            prep.flat_non_protected, aliases,
        )
        reflection_buckets = cls._bucket_entries_by_subject(
            prep.reflections, aliases,
        )
        kept_persona: list = []
        kept_reflections: list = []
        skipped: set = set()
        remaining = SCOPED_RENDER_TOTAL_MAX_TOKENS
        for subject in prep.subject_slots:
            marker = cls._subject_bucket_marker(subject, aliases)
            # Pure caller order: whatever the gate still holds, in the
            # order the caller listed. A subject that wants priority is
            # listed earlier — that is the whole contract, and it is the
            # one every caller already follows (group first). An earlier
            # attempt gave group subjects a reserved slice so they could
            # outrank members ahead of them; it was dead code for every
            # shipped and planned caller and each of its interactions
            # (multiple groups, empty groups, exempt-only groups, unspent
            # slices flowing to later slots) was its own way to invert the
            # order it was meant to protect. Deleted rather than patched
            # a sixth time.
            available = max(0, remaining)
            if available < SCOPED_RENDER_SUBJECT_MIN_TOKENS:
                if persona_buckets.get(marker) or reflection_buckets.get(marker):
                    # Has budgeted content it cannot afford → drop it whole,
                    # exempt sections included, rather than emit a fragment.
                    cls._log_skipped_subject(subject, available)
                    skipped.add(marker)
                # Nothing budgeted to drop: whatever exempt sections this
                # slot has cost the gate nothing and still render.
                continue
            persona_kept, persona_used = await cls._ascore_trim_entries(
                persona_buckets.get(marker, ()),
                PERSONA_RENDER_MAX_TOKENS, now,
                per_entry_overhead=SCOPED_RENDER_ENTRY_MARKUP_TOKENS,
                gate_budget=available,
            )
            # Both counters move by the CHARGED cost. Subtracting only the
            # text from `available` would let the reflection pool spend
            # tokens the gate has already run out of.
            remaining -= persona_used
            available = max(0, available - persona_used)
            reflection_kept, reflection_used = await cls._ascore_trim_entries(
                reflection_buckets.get(marker, ()),
                REFLECTION_RENDER_MAX_TOKENS, now,
                cache_writeback=False,
                per_entry_overhead=SCOPED_RENDER_ENTRY_MARKUP_TOKENS,
                gate_budget=available,
            )
            remaining -= reflection_used
            kept_persona.extend(persona_kept)
            kept_reflections.extend(reflection_kept)
        cls._log_unslotted_buckets(
            prep.subject_slots, persona_buckets, reflection_buckets,
            aliases=aliases,
        )
        return kept_persona, cls._sort_kept_reflections(kept_reflections, now), skipped

    def _prepare_render(
        self, persona: dict,
        pending_reflections: list[dict] | None,
        confirmed_reflections: list[dict] | None,
        subjects=None,
        include_legacy_private: bool | None = None,
        participant_groups=None,
    ) -> _RenderPrep:
        """Phase 1+2 shared by both render paths — see `_RenderPrep`."""
        persona_view = self._persona_view_for_subjects(
            persona, subjects, include_legacy_private=include_legacy_private,
        )
        protected_entries, non_protected_by_entity = (
            self._split_persona_for_render(persona_view)
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
        suppressed_text_set = self._suppressed_text_set(persona_view)
        reflections = self._filter_reflections_for_render(
            (pending_reflections or []) + (confirmed_reflections or []),
            persona_view, suppressed_text_set,
            subjects,
            include_legacy_private,
        )
        return _RenderPrep(
            persona_view=persona_view,
            protected_entries=protected_entries,
            non_protected_entity_index=non_protected_entity_index,
            flat_non_protected=flat_non_protected,
            reflections=reflections,
            suppressed_text_set=suppressed_text_set,
            subject_slots=self._subject_render_slots(
                subjects, include_legacy_private, participant_groups,
            ),
            marker_aliases=self._subject_marker_aliases(participant_groups),
        )

    def _compose_from_prep(
        self, name: str, prep: _RenderPrep, name_mapping: dict,
        trimmed_non_protected: list[dict],
        trimmed_reflections: list[dict],
        pending_reflections: list[dict] | None,
        subjects=None,
        include_legacy_private: bool | None = None,
        skipped: set | None = None,
    ) -> str:
        """Phase 3 shared by both render paths.

        `skipped` holds the subjects the allocator dropped whole. Their
        budget-exempt sections have to go too: protected and suppressed
        entries never pass through the trim, so without this they would
        still render and the subject would come out as the two-line
        fragment that `SCOPED_RENDER_SUBJECT_MIN_TOKENS` exists to avoid.
        """
        # Preserve the score-DESC order produced by the trim. The original
        # implementation filtered the SOURCE lists by id-membership, which
        # lost the sort order and emitted reflections in caller-supplied
        # order (CodeRabbit PR #936 round-4 Minor).
        trimmed_pending, trimmed_confirmed = self._partition_trimmed_reflections(
            trimmed_reflections, pending_reflections, prep.suppressed_text_set,
        )
        protected_entries = self._cap_protected_entries([
            (entity_key, entry) for entity_key, entry in prep.protected_entries
            if not self._entry_is_skipped(entry, skipped, prep.marker_aliases)
        ])
        return self._compose_markdown_from_trimmed(
            name, prep.persona_view, name_mapping,
            protected_entries, trimmed_non_protected,
            prep.non_protected_entity_index,
            trimmed_pending, trimmed_confirmed,
            scoped_only=self._renders_scoped_only(
                subjects, include_legacy_private,
            ),
            skipped=skipped,
            marker_aliases=prep.marker_aliases,
        )

    @classmethod
    def _entry_is_skipped(
        cls, entry, skipped: set | None, aliases: dict | None = None,
    ) -> bool:
        """True when `entry` belongs to a participant the allocator dropped."""
        if not skipped:
            return False
        from memory.scopes import subject_from_entry

        return cls._subject_bucket_marker(
            subject_from_entry(entry), aliases,
        ) in skipped

    def _compose_persona_markdown(
        self, name: str, persona: dict, name_mapping: dict,
        pending_reflections: list[dict] | None,
        confirmed_reflections: list[dict] | None,
        subjects=None,
        include_legacy_private: bool | None = None,
        participant_groups=None,
    ) -> str:
        """Sync 3-phase render path. Used by `render_persona_markdown` and
        any test/migration caller that doesn't have an event loop."""
        now = datetime.now()
        prep = self._prepare_render(
            persona, pending_reflections, confirmed_reflections,
            subjects, include_legacy_private, participant_groups,
        )
        skipped: set = set()
        if prep.subject_slots:
            trimmed_non_protected, trimmed_reflections, skipped = (
                self._trim_scoped_by_subject(prep, now)
            )
        else:
            trimmed_non_protected, _ = self._score_trim_entries(
                prep.flat_non_protected, PERSONA_RENDER_MAX_TOKENS, now,
            )
            trimmed_reflections, _ = self._score_trim_entries(
                prep.reflections, REFLECTION_RENDER_MAX_TOKENS, now,
                # Reflections have no `_personas`-style in-memory view —
                # they're always loaded fresh from disk. Writing cache
                # fields onto the transient dicts would be collected on
                # render exit and could only pollute reflection.json on
                # the next save.
                cache_writeback=False,
            )
        return self._compose_from_prep(
            name, prep, name_mapping,
            trimmed_non_protected, trimmed_reflections,
            pending_reflections, subjects, include_legacy_private,
            skipped=skipped,
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
                                   confirmed_reflections: list[dict] | None = None,
                                   *, subjects=None,
                                   include_legacy_private: bool | None = None,
                                   participant_groups=None) -> str:
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
            subjects, include_legacy_private, participant_groups,
        )

    async def arender_persona_markdown(
        self, name: str,
        pending_reflections: list[dict] | None = None,
        confirmed_reflections: list[dict] | None = None,
        *,
        subjects=None,
        include_legacy_private: bool | None = None,
        participant_groups=None,
    ) -> str:
        """Async 3-phase render path. Production hot path — uses
        `acount_tokens` so the event loop doesn't stall on tiktoken IO.

        Structurally the twin of `_compose_persona_markdown`: everything
        that is not token counting lives in `_prepare_render` /
        `_compose_from_prep`, so the two paths cannot drift on the parts
        that have nothing to do with sync-vs-async."""
        await self.aupdate_suppressions(name)
        persona = await self.aensure_persona(name)
        _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        now = datetime.now()
        prep = self._prepare_render(
            persona, pending_reflections, confirmed_reflections,
            subjects, include_legacy_private, participant_groups,
        )
        skipped: set = set()
        if prep.subject_slots:
            trimmed_non_protected, trimmed_reflections, skipped = (
                await self._atrim_scoped_by_subject(prep, now)
            )
        else:
            trimmed_non_protected, _ = await self._ascore_trim_entries(
                prep.flat_non_protected, PERSONA_RENDER_MAX_TOKENS, now,
            )
            trimmed_reflections, _ = await self._ascore_trim_entries(
                prep.reflections, REFLECTION_RENDER_MAX_TOKENS, now,
                # See sync twin: reflections have no `_personas`-style
                # in-memory view, so we compute fresh every render without
                # writing cache fields back onto the transient dicts.
                cache_writeback=False,
            )
        return self._compose_from_prep(
            name, prep, name_mapping,
            trimmed_non_protected, trimmed_reflections,
            pending_reflections, subjects, include_legacy_private,
            skipped=skipped,
        )

    def _is_suppressed_text(self, persona: dict, text: str) -> bool:
        """Check if a given text matches any suppressed entry."""
        for entry in self._collect_all_entries(persona):
            if isinstance(entry, dict) and entry.get('suppress') and entry.get('text') == text:
                return True
        return False
