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
    SCOPED_RENDER_GROUP_RESERVED_TOKENS,
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
    entry per authorized subject, in the order the CALLER supplied, plus a
    trailing ``None`` slot when legacy-private rows are also allowed in.
    Empty means legacy mode — one shared pool, pre-existing behaviour.
    """

    persona_view: dict
    protected_entries: list
    non_protected_entity_index: dict
    flat_non_protected: list
    reflections: list
    suppressed_text_set: set
    subject_slots: tuple


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
                view[section_key] = filtered
            elif (scoped_subject.key, scoped_subject.scope) in allowed_keys:
                view[section_key] = section
        return view

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
        *, cache_writeback: bool = True,
    ) -> tuple[list, int]:
        """Sync score-trim: sort by (evidence_score, importance) DESC, keep
        entries whose accumulated `count_tokens(text)` ≤ `budget`.

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

        `cache_writeback`: default True writes `token_count` fields back
        onto each entry for across-render reuse (persona path — entries
        live in `_personas`). Pass False for reflection entries, which are
        loaded fresh from disk every render and would have no persistent
        view to cache against; writing cache fields there would be
        misleading and pollute reflection.json on the next save.
        """
        kept = []
        total = 0
        for e in cls._score_trim_sort(entries, now):
            t = cls._get_cached_token_count(e, writeback=cache_writeback)
            if total + t > budget:
                continue
            kept.append(e)
            total += t
        return kept, total

    @classmethod
    async def _ascore_trim_entries(
        cls, entries: list, budget: int, now: datetime,
        *, cache_writeback: bool = True,
    ) -> tuple[list, int]:
        """Async twin of `_score_trim_entries`. Identical math; the only
        difference is `acount_tokens` (worker-thread tiktoken). See the
        sync twin for the skip-don't-stop rule, the `(kept, tokens_used)`
        return and the `cache_writeback` contract."""
        kept = []
        total = 0
        for e in cls._score_trim_sort(entries, now):
            t = await cls._aget_cached_token_count(e, writeback=cache_writeback)
            if total + t > budget:
                continue
            kept.append(e)
            total += t
        return kept, total

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
        if len(protected_entries) > PERSONA_RENDER_PROTECTED_MAX_ENTRIES:
            logger.warning(
                f"[Persona] protected 条目 {len(protected_entries)} 条超过渲染"
                f"上限 {PERSONA_RENDER_PROTECTED_MAX_ENTRIES}，尾部 "
                f"{len(protected_entries) - PERSONA_RENDER_PROTECTED_MAX_ENTRIES}"
                f" 条本轮不渲染（protected 不吃 token 预算，只能按条数封顶）"
            )
            protected_entries = protected_entries[
                :PERSONA_RENDER_PROTECTED_MAX_ENTRIES
            ]
        return protected_entries, dict(non_protected_by_entity)

    @staticmethod
    def _filter_reflections_for_render(
        reflections: list[dict] | None, persona: dict,
        suppressed_text_set: set[str],
        subjects=None,
        include_legacy_private: bool | None = None,
    ) -> list[dict]:
        """Drop reflections whose text matches a suppressed persona entry
        (existing semantic — see `_is_suppressed_text` callers below)."""
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
            text = r.get('text', '')
            if not text:
                continue
            if text in suppressed_text_set:
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

        # Suppressed entries always render (the whole point is "AI
        # remembers but won't volunteer it", and a half-listed do-not-
        # mention list is worse than none); not budget-counted. Capped by
        # COUNT so "exempt from the token budget" can't become "unbounded"
        # — a long suppression cooldown on a chatty character otherwise
        # grows this section without any ceiling at all.
        suppressed_lines: list[str] = []
        suppressed_total = 0
        for entry in self._collect_all_entries(persona):
            if isinstance(entry, dict) and entry.get('suppress'):
                text = entry.get('text', '')
                if text:
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
                section_meta = persona.get(entity_key, {})
                subject_kind = section_meta.get('subject_kind')
                subject_id = section_meta.get('subject_id')
                if subject_kind in (
                    'group_chat', 'participant', 'group_participant',
                ):
                    from config.prompts.prompts_memory import (
                        get_scoped_persona_section_header,
                    )
                    from utils.language_utils import get_global_language
                    header = get_scoped_persona_section_header(
                        subject_kind, subject_id, get_global_language(),
                    )
                else:
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
                    # 群/成员 subject 的渲染里点名私聊对象是双重错误：名字
                    # 泄漏进群 prompt，指令对象也不是群里的人。
                    scoped_only=scoped_only,
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

    # ------------------------------------------------------------------
    # Budget allocation across subjects (§3.6 + group-memory PR-2)
    # ------------------------------------------------------------------

    @staticmethod
    def _subject_render_slots(
        subjects=None, include_legacy_private: bool | None = None,
    ) -> tuple:
        """Allocation order for a scoped render, or `()` for legacy mode.

        The order is the CALLER's, verbatim. The plugin currently sends
        `[group, current speaker]` and a later PR widens it to
        `[group, current speaker, the last three other speakers]`; deciding
        here who matters would silently override the only layer that knows.

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
        slots = list(allowed)
        if include_legacy_private:
            slots.append(None)
        return tuple(slots)

    @staticmethod
    def _subject_bucket_marker(subject):
        """`(key, scope)` for a subject slot; `None` for the legacy slot.

        Mirrors what `filter_entries_for_subjects` matches on, so an entry
        lands in the same bucket the authorization check used.
        """
        return None if subject is None else (subject.key, subject.scope)

    @classmethod
    def _bucket_entries_by_subject(cls, entries) -> dict:
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
            marker = cls._subject_bucket_marker(subject_from_entry(entry))
            buckets[marker].append(entry)
        return buckets

    @staticmethod
    def _subject_available_budget(slots: tuple, index: int, remaining: int) -> int:
        """What slot `index` may spend out of the overall `remaining` gate.

        Every group subject still queued behind this one keeps a reserved
        slice out of reach. The group's persona is the context every member
        of the conversation shares, so it is the worst thing to lose to
        whoever happened to be listed first — and the caller's order is not
        ours to reshuffle (see `_subject_render_slots`).

        One reserve PER remaining group, not one flat reserve: with two
        groups in the same render, a single flat slice covers only the last
        of them, and the earlier group ends up donating to the later one
        and starving itself — the exact outcome the reserve exists to
        prevent.

        A group never reserves against another group. Groups are peers;
        between peers the caller's order decides, and the reserve exists
        only to stop MEMBERS from eating a group that is queued behind
        them. Charging a group for the groups after it inverts the very
        thing it protects: with five groups in one render the first one
        would owe four reserves, come out with nothing, and the request
        would render its LAST four subjects instead of its first four.
        """
        from memory.scopes import SUBJECT_GROUP_CHAT

        current = slots[index] if index < len(slots) else None
        if current is not None and current.kind == SUBJECT_GROUP_CHAT:
            return max(0, remaining)
        groups_ahead = sum(
            1 for later in slots[index + 1:]
            if later is not None and later.kind == SUBJECT_GROUP_CHAT
        )
        reserved = groups_ahead * SCOPED_RENDER_GROUP_RESERVED_TOKENS
        return max(0, remaining - reserved)

    @classmethod
    def _log_skipped_subject(cls, subject, remaining: int, available: int) -> None:
        # Both numbers, because they diverge exactly when this line matters
        # most: with a group queued behind, `available` is the gate minus
        # the reserve. Reporting only that as "总闸剩余" sends whoever is
        # debugging a vanished persona to raise the wrong constant — the
        # gate is not what is holding them back, the reserve is.
        label = "legacy" if subject is None else subject.key
        logger.warning(
            f"[Persona] scoped 渲染总闸剩余 {remaining} tok，扣掉群保底后可用 "
            f"{available} tok，低于单 subject 下限 "
            f"{SCOPED_RENDER_SUBJECT_MIN_TOKENS}，subject {label} 整段跳过"
            f"（半截的人设比缺席更糟）"
        )

    @classmethod
    def _log_unslotted_buckets(cls, slots: tuple, *bucket_maps) -> None:
        """Loudly report entries that passed authorization but got no slot.

        Should be unreachable: everything in the view matched one of the
        caller's subjects. If it ever fires, memories are vanishing between
        the filter and the allocator, and a silent drop is exactly the kind
        of thing that only surfaces as "the character forgot things".
        """
        known = {cls._subject_bucket_marker(s) for s in slots}
        for buckets in bucket_maps:
            for marker, entries in buckets.items():
                if marker not in known and entries:
                    logger.warning(
                        f"[Persona] scoped 渲染有 {len(entries)} 条已授权条目"
                        f"没有对应 subject 槽位（marker={marker}），本轮丢失"
                    )

    @classmethod
    def _trim_scoped_by_subject(
        cls, prep: _RenderPrep, now: datetime,
    ) -> tuple[list, list]:
        """Sync per-subject allocation under the overall scoped gate.

        Each subject gets its own `PERSONA_RENDER_MAX_TOKENS` /
        `REFLECTION_RENDER_MAX_TOKENS` instead of every subject in the
        render fighting over one shared pair, and the sum is held down by
        `SCOPED_RENDER_TOTAL_MAX_TOKENS`. Whatever a subject leaves unspent
        rolls forward to the next one.
        """
        persona_buckets = cls._bucket_entries_by_subject(prep.flat_non_protected)
        reflection_buckets = cls._bucket_entries_by_subject(prep.reflections)
        kept_persona: list = []
        kept_reflections: list = []
        remaining = SCOPED_RENDER_TOTAL_MAX_TOKENS
        for index, subject in enumerate(prep.subject_slots):
            marker = cls._subject_bucket_marker(subject)
            available = cls._subject_available_budget(
                prep.subject_slots, index, remaining,
            )
            if available < SCOPED_RENDER_SUBJECT_MIN_TOKENS:
                cls._log_skipped_subject(subject, remaining, available)
                continue
            persona_kept, persona_used = cls._score_trim_entries(
                persona_buckets.get(marker, ()),
                min(PERSONA_RENDER_MAX_TOKENS, available), now,
            )
            remaining -= persona_used
            available -= persona_used
            reflection_kept, reflection_used = cls._score_trim_entries(
                reflection_buckets.get(marker, ()),
                min(REFLECTION_RENDER_MAX_TOKENS, available), now,
                cache_writeback=False,
            )
            remaining -= reflection_used
            kept_persona.extend(persona_kept)
            kept_reflections.extend(reflection_kept)
        cls._log_unslotted_buckets(
            prep.subject_slots, persona_buckets, reflection_buckets,
        )
        return kept_persona, kept_reflections

    @classmethod
    async def _atrim_scoped_by_subject(
        cls, prep: _RenderPrep, now: datetime,
    ) -> tuple[list, list]:
        """Async twin of `_trim_scoped_by_subject` — same allocation, only
        the token counter differs (worker-thread tiktoken)."""
        persona_buckets = cls._bucket_entries_by_subject(prep.flat_non_protected)
        reflection_buckets = cls._bucket_entries_by_subject(prep.reflections)
        kept_persona: list = []
        kept_reflections: list = []
        remaining = SCOPED_RENDER_TOTAL_MAX_TOKENS
        for index, subject in enumerate(prep.subject_slots):
            marker = cls._subject_bucket_marker(subject)
            available = cls._subject_available_budget(
                prep.subject_slots, index, remaining,
            )
            if available < SCOPED_RENDER_SUBJECT_MIN_TOKENS:
                cls._log_skipped_subject(subject, remaining, available)
                continue
            persona_kept, persona_used = await cls._ascore_trim_entries(
                persona_buckets.get(marker, ()),
                min(PERSONA_RENDER_MAX_TOKENS, available), now,
            )
            remaining -= persona_used
            available -= persona_used
            reflection_kept, reflection_used = await cls._ascore_trim_entries(
                reflection_buckets.get(marker, ()),
                min(REFLECTION_RENDER_MAX_TOKENS, available), now,
                cache_writeback=False,
            )
            remaining -= reflection_used
            kept_persona.extend(persona_kept)
            kept_reflections.extend(reflection_kept)
        cls._log_unslotted_buckets(
            prep.subject_slots, persona_buckets, reflection_buckets,
        )
        return kept_persona, kept_reflections

    def _prepare_render(
        self, persona: dict,
        pending_reflections: list[dict] | None,
        confirmed_reflections: list[dict] | None,
        subjects=None,
        include_legacy_private: bool | None = None,
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
                subjects, include_legacy_private,
            ),
        )

    def _compose_from_prep(
        self, name: str, prep: _RenderPrep, name_mapping: dict,
        trimmed_non_protected: list[dict],
        trimmed_reflections: list[dict],
        pending_reflections: list[dict] | None,
        subjects=None,
        include_legacy_private: bool | None = None,
    ) -> str:
        """Phase 3 shared by both render paths."""
        # Preserve the score-DESC order produced by the trim. The original
        # implementation filtered the SOURCE lists by id-membership, which
        # lost the sort order and emitted reflections in caller-supplied
        # order (CodeRabbit PR #936 round-4 Minor).
        trimmed_pending, trimmed_confirmed = self._partition_trimmed_reflections(
            trimmed_reflections, pending_reflections, prep.suppressed_text_set,
        )
        return self._compose_markdown_from_trimmed(
            name, prep.persona_view, name_mapping,
            prep.protected_entries, trimmed_non_protected,
            prep.non_protected_entity_index,
            trimmed_pending, trimmed_confirmed,
            scoped_only=self._renders_scoped_only(
                subjects, include_legacy_private,
            ),
        )

    def _compose_persona_markdown(
        self, name: str, persona: dict, name_mapping: dict,
        pending_reflections: list[dict] | None,
        confirmed_reflections: list[dict] | None,
        subjects=None,
        include_legacy_private: bool | None = None,
    ) -> str:
        """Sync 3-phase render path. Used by `render_persona_markdown` and
        any test/migration caller that doesn't have an event loop."""
        now = datetime.now()
        prep = self._prepare_render(
            persona, pending_reflections, confirmed_reflections,
            subjects, include_legacy_private,
        )
        if prep.subject_slots:
            trimmed_non_protected, trimmed_reflections = (
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
                                   include_legacy_private: bool | None = None) -> str:
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
            subjects, include_legacy_private,
        )

    async def arender_persona_markdown(
        self, name: str,
        pending_reflections: list[dict] | None = None,
        confirmed_reflections: list[dict] | None = None,
        *,
        subjects=None,
        include_legacy_private: bool | None = None,
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
            subjects, include_legacy_private,
        )
        if prep.subject_slots:
            trimmed_non_protected, trimmed_reflections = (
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
