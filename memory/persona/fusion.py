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
"""External-memory LLM fusion for persona import.

WHY THIS EXISTS
---------------
When persona is rendered into the system prompt there is a **strict token
ceiling** (``PERSONA_RENDER_MAX_TOKENS``, shared by all non-protected entries in
one pool -- a scoped group render gives each subject its own pool instead, but
external import only ever writes the legacy private corpus, so the single-pool
figure is the one that binds here). But OpenClaw / Hermes ``USER.md`` /
``SOUL.md`` are dozens of lines of
free-form Markdown -- appending them entry-by-entry after exact dedup (as the old
``aimport_external_facts`` did) would quickly overflow the persona pool and crowd
out the impressions the character has naturally accumulated in conversation. So
``USER.md`` / ``SOUL.md`` must first go through one LLM fusion (summarise / merge /
dedupe / disambiguate / rank by importance) that compresses the content into the
per-entity budget (``EXTERNAL_IMPORT_PERSONA_{NEKO,MASTER}_MAX_TOKENS``) before it
is persisted as non-protected persona entries.

LOCK DISCIPLINE
---------------
Fusion calls an LLM (tens of seconds). The persona per-character lock is **also**
held while ``/new_dialog`` assembles a live prompt, so the LLM must never run
inside the lock -- otherwise the character cannot reply during fusion. This
follows the three-phase shape of ``promotion_merge``:
  Phase 1 (locked)  : snapshot the external-import bucket + folded-fingerprint idempotency check
  Phase 2 (unlocked): fuse the existing bucket + the new candidates (LLM merges/dedupes across sources)
  Phase 3 (locked)  : CAS on the folded set, then evict + rewrite the bucket, atomic save

IDEMPOTENCY & MULTI-SOURCE ACCUMULATION
---------------------------------------
LLM fusion is non-deterministic; re-importing the same workspace must not grow the
pool with every import. Each import computes a stable fingerprint over its own
candidate set, and every produced entry carries a ``folded_fingerprints`` set
naming the source versions already folded into the bucket. On re-import:

* fingerprint already in the folded set -> strict idempotent no-op (LLM not called);
* new fingerprint -> **fold**: re-fuse (existing bucket entries + new candidates)
  into a fresh digest, so a second source (e.g. OpenClaw then Hermes) accumulates
  instead of clobbering, and cross-source duplicates are merged by the same fusion
  LLM. The digest is trimmed back to the per-entity budget, so accumulation stays
  bounded.

A merged digest loses per-source attribution, so re-importing an *edited* version of
an already-folded source cannot precisely retract its old contribution -- the fusion
LLM reconciles overlaps, but a contradictory edit may leave residue (an accepted
trade-off). Everything is keyed on ``source == 'external_import'`` only -- protected
(character card) / conversational accumulation / reflection entries are never folded
or evicted. Phase 3 re-checks the folded set (CAS) so a concurrent import cannot be
silently clobbered.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime

from config import (
    EXTERNAL_IMPORT_FUSION_BREADCRUMB_MAX_TOKENS,
    EXTERNAL_IMPORT_FUSION_ENTRY_MAX_TOKENS,
    EXTERNAL_IMPORT_FUSION_INPUT_MAX_TOKENS,
    EXTERNAL_IMPORT_PERSONA_MASTER_MAX_TOKENS,
    EXTERNAL_IMPORT_PERSONA_NEKO_MAX_TOKENS,
    MEMORY_LLM_HARD_TIMEOUT_SECONDS,
)
from config.prompts.prompts_memory import (
    get_persona_fusion_entity_label,
    get_persona_fusion_prompt,
)
from memory.evidence import initial_reinforcement_from_importance
from utils.file_utils import robust_json_loads
from utils.language_utils import (
    detect_prompt_language_with_ascii_fallback,
    get_global_language_full,
)
from utils.token_tracker import set_call_type
from utils.tokenize import count_tokens, truncate_to_tokens

from ._shared import logger

# Only master (USER.md) / neko (SOUL.md) use external fusion. relationship does not produce a persona
# branch (see classification in memory/external_markdown_import.py); skip it unless listed here.
_ENTITY_BUDGET = {
    "master": EXTERNAL_IMPORT_PERSONA_MASTER_MAX_TOKENS,
    "neko": EXTERNAL_IMPORT_PERSONA_NEKO_MAX_TOKENS,
}

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _detect_fusion_prompt_language(text: str) -> str:
    return detect_prompt_language_with_ascii_fallback(
        text,
        ui_language=get_global_language_full(),
    )


class ExternalMemoryFusionError(RuntimeError):
    """Terminal failure of the fusion LLM (call failed / parse failed / produced 0 entries).

    The caller **must not** fall back to per-entry appends (that would bypass the
    token budget and overflow the pool) -- the original import material is kept so
    the user can retry; retries are idempotent (same fingerprint -> skip / new
    fingerprint -> re-fold). Dual to ``FactExtractionFailed``: it distinguishes a
    "retriable terminal failure" from "succeeded but empty".
    """


class ExternalMemoryImportTooLargeError(ExternalMemoryFusionError):
    """Non-retryable: the candidate set exceeds the single-fusion input budget.

    Distinct from a transient ``ExternalMemoryFusionError`` — retrying the same
    oversized workspace just fails again (no fingerprint is recorded), so the
    caller surfaces a "split the workspace" message rather than "retry to finish".
    """


class ExternalFusionMixin:
    """PersonaManager mixin: fuse external-import material via an LLM, then persist into persona.

    Dual to ``RefinementMixin`` / ``CorrectionsMixin`` -- all three are persona LLM
    write paths, each living in its own mixin file.
    """

    async def afuse_external_facts(
        self, name: str, entity: str, candidates: list[dict], source_format: str,
    ) -> dict:
        """Fold one entity's external-import candidates into persona (3-phase).

        The existing external-import bucket and the new candidates are fused
        together, so a second source accumulates (and cross-source duplicates
        merge) instead of clobbering the first -- see the module IDEMPOTENCY note.

        Returns ``{'added': int, 'skipped': int, 'fused': bool}`` (``added`` is the
        size of the rewritten bucket). Raises ``ExternalMemoryFusionError`` on
        terminal LLM / parse failure.
        """
        entity = str(entity or "master")
        budget = _ENTITY_BUDGET.get(entity)
        cands = [
            c for c in (candidates or [])
            if isinstance(c, dict) and str(c.get("text") or "").strip()
        ]
        if budget is None or not cands:
            return {"added": 0, "skipped": len(cands), "fused": False}

        fingerprint = self._fusion_fingerprint(cands)

        # ── Phase 1 (locked): snapshot the external-import bucket + folded-fingerprint idempotency check ──
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            existing_external = [
                f for f in self._get_section_facts(persona, entity)
                if isinstance(f, dict) and f.get("source") == "external_import"
            ]
            base_folded = self._collect_folded_fingerprints(existing_external)
            if fingerprint in base_folded:
                # This source was already folded into the bucket in this version → strict idempotent no-op; do not call the LLM
                return {"added": 0, "skipped": len(cands), "fused": False}
            existing_texts = [
                str(f.get("text") or "").strip()
                for f in existing_external
                if str(f.get("text") or "").strip()
            ]

        # ── Phase 2 (unlocked): fuse "existing bucket + new candidates" (takes tens of seconds; never hold the lock) ──
        # Put the existing bucket first: _allm_call_fusion truncates from the **end** to fit the token limit, keeping accumulated multi-source
        # digests at the front so they are not truncated — otherwise a huge new import could push old sources out of the fusion input,
        # and Phase 3 would then clear the whole bucket and rewrite it from LLM output that saw only the new source, silently erasing old sources (Codex
        # P2). A per-entity budget caps the existing bucket (≤budget≪input limit), leaving room for new candidates;
        # in a true overflow, truncation affects the tail of this new material, not accumulated state.
        fold_input = [{"text": t} for t in existing_texts] + list(cands)
        fused = await self._allm_call_fusion(name, entity, fold_input, budget)
        if fused is None:
            raise ExternalMemoryFusionError(f"persona fusion LLM failed: {name}/{entity}")
        fused = self._trim_fused_to_budget(fused, budget)
        if not fused:
            # LLM succeeded but fusion produced zero entries — treat this as a terminal failure that can be retried, not as silent user-data loss
            raise ExternalMemoryFusionError(
                f"persona fusion produced no entries: {name}/{entity}"
            )

        # ── Phase 3 (locked): CAS the folded set + remove old bucket + write new digest, then save atomically ──
        imported_at = datetime.now().astimezone().isoformat()
        new_folded = sorted(base_folded | {fingerprint})
        source_files = sorted(
            {str(c.get("source_file") or "") for c in cands if c.get("source_file")}
        )
        metadata = {
            "format": source_format,
            "files": source_files,
            "section": "fused",
            "fusion_fingerprint": fingerprint,
            "folded_fingerprints": new_folded,
            "imported_at": imported_at,
            "fused": True,
        }
        source_id = f"{source_format}:fusion:{entity}"
        async with self._get_alock(name):
            persona = await self._aensure_persona_locked(name)
            section_facts = self._get_section_facts(persona, entity)
            current_external = [
                f for f in section_facts
                if isinstance(f, dict) and f.get("source") == "external_import"
            ]
            # CAS: Phase 2 fused against a base_folded snapshot. If a concurrent import changes the external bucket
            # (the folded set changes), this result is stale (it would lose the other import's additions) → raise a retryable error,
            # never overwrite silently. Re-importing the same source still uses the idempotent skip above.
            if self._collect_folded_fingerprints(current_external) != base_folded:
                raise ExternalMemoryFusionError(
                    f"persona fusion base changed under concurrent import: {name}/{entity}"
                )
            # First filter candidates through the guard; leave the old bucket **untouched**: discard conflicts with the character card and send conflicts with
            # non-character-card persona facts to the correction queue. Compare with "non-external-import" entries (the old bucket will be replaced and is not part of
            # the decision); reuse the same lightweight guard as aadd_fact (Codex P2).
            non_external = [
                f for f in section_facts
                if not (isinstance(f, dict) and f.get("source") == "external_import")
            ]
            stop_names = await self._aget_entity_stop_names(name)
            survivors: list[dict] = []
            for item in fused:
                # Compare against "non-external-import entries + accepted survivors" — prevent two mutually
                # contradictory entries in the same fusion batch from both being accepted and rendered together (Codex P2).
                code, old_text = self._evaluate_fact_contradiction(
                    name, item["text"], non_external + survivors, stop_names,
                )
                if code == self.FACT_REJECTED_CARD:
                    continue
                if code == self.FACT_QUEUED_CORRECTION:
                    # Already under the lock → use the _locked version; let the LLM decide, rather than rendering two opposing statements together.
                    await self._aqueue_correction_locked(name, old_text, item["text"], entity)
                    continue
                survivors.append(item)
            if not survivors:
                # If the character card rejects every fusion result or all enter the correction queue, preserve the old bucket unchanged, do not clear it, and do not update
                # the fingerprint (otherwise a second import that fully conflicts could erase the already imported digest) (Codex P2).
                return {"added": 0, "skipped": len(cands), "fused": False}
            # Replace only if there are survivors: remove the old bucket + write the new digest, then save atomically (the old bucket has been folded into the new
            # digest; protected data / conversation accumulation / reflection remain untouched).
            section_facts[:] = non_external
            added = 0
            for item in survivors:
                entry = self._build_fact_entry(item["text"], "external_import", source_id)
                entry["reinforcement"] = initial_reinforcement_from_importance(item["importance"])
                entry["external_import"] = dict(metadata)
                section_facts.append(entry)
                added += 1
            await self.asave_persona(name, persona)
        return {"added": added, "skipped": 0, "fused": True}

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _fusion_fingerprint(candidates: list[dict]) -> str:
        """Stable, order-independent fingerprint over the entity's candidate texts."""
        norm = sorted(
            " ".join(str(c.get("text") or "").casefold().split()) for c in candidates
        )
        return hashlib.sha256("\n".join(norm).encode("utf-8")).hexdigest()

    @staticmethod
    def _collect_folded_fingerprints(entries: list[dict]) -> set[str]:
        """Union of source fingerprints already folded into these external entries.

        Reads each entry's ``external_import.folded_fingerprints`` list, falling
        back to the legacy single ``fusion_fingerprint`` for entries written before
        multi-source folding. Empty set for a fresh (never-imported) bucket.
        """
        folded: set[str] = set()
        for f in entries:
            if not isinstance(f, dict):
                continue
            meta = f.get("external_import") or {}
            fps = meta.get("folded_fingerprints")
            if isinstance(fps, list):
                folded.update(str(x) for x in fps if x)
            legacy = meta.get("fusion_fingerprint")
            if legacy:
                folded.add(str(legacy))
        return folded

    async def _allm_call_fusion(
        self, name: str, entity: str, candidates: list[dict], budget: int,
    ) -> list[dict] | None:
        """Run the fusion LLM (UNLOCKED). Returns parsed ``[{text, importance}]`` or None.

        None = call/parse failure (caller raises ExternalMemoryFusionError). Mirrors
        the reflection promote-merge LLM shape: correction tier + thinking + single
        shot (max_retries=0) + robust JSON parse.
        """
        from memory.thinking_llm import ainvoke_thinking

        # names (avoid objectifying people: if master has no name, use a neutral placeholder instead of rendering's "主人" fallback)
        try:
            _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        except Exception:
            name_mapping = {}
        ai_name = name
        master_name = (name_mapping or {}).get("human") or "用户"

        lines = []
        for idx, cand in enumerate(candidates, 1):
            # Truncate breadcrumbs separately by token limit so many long candidate titles cannot consume the input pool and push out later items
            # Candidate body text (tail truncation → later memories are permanently lost) (Greptile P1).
            section = truncate_to_tokens(
                str(cand.get("source_section") or "").strip(),
                EXTERNAL_IMPORT_FUSION_BREADCRUMB_MAX_TOKENS,
            )
            text = str(cand.get("text") or "").strip()
            prefix = f"{section}: " if section and section.casefold() not in text.casefold() else ""
            lines.append(f"{idx}. {prefix}{text}")
        cand_text = "\n".join(lines)
        locale_text = "\n".join(
            str(cand.get("text") or "").strip()
            for cand in candidates
        )
        lang = _detect_fusion_prompt_language(locale_text)
        entity_label = get_persona_fusion_entity_label(entity, lang)
        if count_tokens(cand_text) > EXTERNAL_IMPORT_FUSION_INPUT_MAX_TOKENS:
            # If candidates exceed the single-call fusion input pool, the tail will be truncated but the whole-batch fingerprint will still be recorded as folded, permanently losing later memories.
            # Prefer a retryable error (→ external_import_partial, prompting the user to split the
            # workspace) over silent data loss (Greptile P1). Lossless batch fusion is future work.
            raise ExternalMemoryImportTooLargeError(
                f"external import too large for a single fusion pass: {name}/{entity}"
            )

        prompt = get_persona_fusion_prompt(lang).format(
            AI_NAME=ai_name,
            MASTER_NAME=master_name,
            ENTITY_LABEL=entity_label,
            TOKEN_BUDGET=budget,
            CANDIDATES=cand_text,
        )

        set_call_type("persona_external_fusion")
        # Client construction (including reading correction-model config) can also raise (e.g. invalid configuration); handle it
        # like ainvoke and return None, or it bypasses ExternalMemoryFusionError — for the second
        # entity (after the first has been persisted), the endpoint would return generic 500 instead of external_import_partial,
        # losing the partial metadata needed by the frontend to retry (Codex P2).
        try:
            api_config = await self._config_manager.aget_model_api_config("correction")
        except Exception as exc:
            logger.warning(f"[PersonaFusion] {name}/{entity} 融合 LLM 构造失败: {exc}")
            return None
        try:
            # Explicitly enable thinking; token budget and fallback are in memory.thinking_llm.
            # noqa rationale: cand_text was already truncated_to_tokens to EXTERNAL_IMPORT_FUSION_INPUT_MAX_TOKENS
            resp, _ = await ainvoke_thinking(  # noqa: LLM_INPUT_BUDGET
                api_config, prompt,
                timeout=MEMORY_LLM_HARD_TIMEOUT_SECONDS,
                call_label=f"{name}/{entity} persona_external_fusion",
            )
        except Exception as exc:
            # Any error during construction or invocation returns None: otherwise the exception bypasses ExternalMemoryFusionError
            # and the endpoint returns generic 500 instead of external_import_partial, losing the partial metadata needed for frontend retry
            # (Codex P2). The helper swallows failures when disabling, so the result is not overwritten.
            logger.warning(f"[PersonaFusion] {name}/{entity} 融合 LLM 调用失败: {exc}")
            return None

        raw = resp.content if hasattr(resp, "content") else str(resp)
        return self._parse_fusion_response(raw)

    @staticmethod
    def _parse_fusion_response(raw: str) -> list[dict] | None:
        """Parse LLM output → ``[{text: str, importance: int(1..10)}]`` or None."""
        if not isinstance(raw, str):
            return None
        stripped = _JSON_FENCE_RE.sub("", raw.strip()).strip()
        try:
            data = robust_json_loads(stripped)
        except Exception:
            return None
        if not isinstance(data, list):
            return None
        out: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            try:
                importance = int(item.get("importance", 5))
            except (TypeError, ValueError):
                importance = 5
            out.append({"text": text, "importance": max(1, min(10, importance))})
        return out

    @staticmethod
    def _trim_fused_to_budget(fused: list[dict], budget: int) -> list[dict]:
        """Sort by importance desc, per-entry soft-cap, greedily accumulate to budget.

        Whole-entry greedy (stop as soon as total+t>budget), matching the rendering
        layer's _ascore_trim_entries. Keeps at least 1 entry so boundary cases don't
        drop the whole batch. Deduplicates by normalized text (keeping the highest
        importance, since sorted desc) so an LLM that repeats a line can't mint two
        entries sharing one timestamp+text-hash id (Codex P2).
        """
        ordered = sorted(fused, key=lambda x: x.get("importance", 5), reverse=True)
        kept: list[dict] = []
        seen: set[str] = set()
        total = 0
        for item in ordered:
            text = truncate_to_tokens(item["text"], EXTERNAL_IMPORT_FUSION_ENTRY_MAX_TOKENS)
            if not text:
                continue
            norm = " ".join(text.casefold().split())
            if norm in seen:
                continue
            t = count_tokens(text)
            if kept and total + t > budget:
                break
            kept.append({"text": text, "importance": item["importance"]})
            seen.add(norm)
            total += t
        return kept
