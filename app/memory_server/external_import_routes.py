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
"""External markdown memory import routes and progress tracking."""

from datetime import datetime
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from utils.language_utils import is_supported_language_code, language_context, normalize_language_code
from memory.external_markdown_import import MAX_ENTRIES, MAX_ENTRY_CHARS
from memory.persona.fusion import ExternalMemoryImportTooLargeError
from .runtime import app

from . import routes as _routes

class ExternalMemoryImportRequest(BaseModel):
    character_name: str
    source_format: str
    imported_files: list[str]
    candidates: list[dict]
    warning_count: int = 0
    language: str | None = None
    render_language: str | None = None


# ── External import progress registry (polled by the Molili frontend progress bar) ──────────
# character → {stage, persona_total/done, daily_total/done, finished, ...}.
# Written by _import_external_markdown (register / fuse / finish) and by
# aimport_external_daily's on_progress callback (once per batch). One small entry per character, no eviction;
# read and written on the single-threaded event loop, so no lock is needed. Each import owns its own entry
# object and only edits that one: an overlapping request for the same character (a retry after a frontend
# timeout) re-registers over it, and the older request finishing first never marks the newer entry finished or writes counts into it.
_IMPORT_PROGRESS: dict[str, dict] = {}


@app.get("/internal/memory/import_progress/{character_name}")
async def get_import_progress(character_name: str):
    """External import progress for the Molili import progress bar.

    Pure in-memory counter read: no IO."""
    name = _routes.validate_lanlan_name(character_name)
    entry = _IMPORT_PROGRESS.get(name)
    if entry is None:
        return {'success': True, 'active': False}
    return {'success': True, 'active': not bool(entry.get('finished')), **entry}


@app.post("/internal/memory/import_external_markdown")
async def import_external_markdown(request: ExternalMemoryImportRequest):
    """Join the per-character admission ledger, then run the import.

    This endpoint is body-addressed, so the publication guard middleware cannot
    see which character it writes to. Its facts path reaches
    ``fact_store`` → ``aindex_fact`` → that character's SQLite index, and one
    persona fusion can run for minutes, so it has to register here explicitly —
    otherwise release sees no active request and can dispose underneath it.
    """
    name = _routes.validate_lanlan_name(request.character_name)
    context_token = _routes.runtime._begin_character_request(name)
    if context_token is None:
        return JSONResponse(
            {"status": "cancelled", "message": "character release in progress"},
            status_code=409,
        )
    progress: dict = {}
    try:
        return await _import_external_markdown(request, progress)
    finally:
        _routes.runtime._end_character_request(name, context_token)
        progress['finished'] = True


async def _import_external_markdown(
    request: ExternalMemoryImportRequest,
    progress: dict | None = None,
):
    """Persist already-previewed OpenClaw/Hermes entries via live managers.

    The persona and facts persistence paths are **asymmetric**, because their
    downstream budgets differ:

    - **facts** take the ``_apersist_new_facts(semantic_dedup=False)`` pure-append
      path -- the facts pool has no hard token ceiling for system-prompt rendering;
      entries are recalled on demand at retrieval time, so keeping each one is fine.
    - **persona** must first go through one LLM fusion via ``afuse_external_facts``.
      When persona is rendered into the system prompt, all non-protected entries
      compete for a single **strict token ceiling**; ``USER.md`` / ``SOUL.md`` are
      dozens of lines of free-form Markdown, and appending them verbatim would
      quickly overflow that pool and crowd out the impressions the character has
      naturally accumulated in conversation. Fusion summarises / merges / dedupes
      the material and truncates it to the per-entity budget before persisting.
      Candidates are grouped by entity (master / neko) and fused separately.

    On fusion failure (``ExternalMemoryFusionError``) there is **no fallback** to
    per-entry appends (that would bypass the budget and overflow the pool) -- the
    user's material is kept and ``external_import_partial`` is returned so the
    frontend can retry; retries are idempotent (same fingerprint -> skip the whole
    batch / changed -> replace-then-fuse).
    """
    name = _routes.validate_lanlan_name(request.character_name)
    if request.source_format not in {"openclaw", "hermes", "molili"}:
        raise HTTPException(status_code=400, detail="Invalid source_format")
    if not request.candidates or len(request.candidates) > MAX_ENTRIES:
        raise HTTPException(status_code=400, detail="Invalid candidate count")
    if _routes.runtime.fact_store is None or _routes.runtime.persona_manager is None:
        raise HTTPException(status_code=503, detail="Memory components are not ready")
    _routes.assert_cloudsave_writable(
        _routes.runtime._config_manager,
        operation="import",
        target=f"memory/{name}/external-markdown",
    )

    explicit_language = None
    locale_admission_order = None
    if is_supported_language_code(request.language):
        explicit_language = normalize_language_code(request.language, format='full')
        locale_admission_order = (
            _routes.locale_state.allocate_character_prompt_locale_order(name)
        )

    imported_at = datetime.now().astimezone().isoformat()
    # Group persona candidates by entity (master / neko) for separate LLM fusion; MEMORY.md facts
    # append directly, while daily diaries (with event_date) undergo LLM fact extraction.
    persona_candidates_by_entity: dict[str, list[dict]] = {}
    extracted_facts: list[dict] = []       # MEMORY.md → deterministic append-only.
    daily_candidates: list[dict] = []      # Daily diary → LLM fact extraction.
    for candidate in request.candidates:
        if not isinstance(candidate, dict):
            raise HTTPException(status_code=400, detail="Invalid candidate")
        text = str(candidate.get("text") or "").strip()
        entity = str(candidate.get("entity") or "master")
        target = candidate.get("target")
        source_file = str(candidate.get("source_file") or "")
        if (
            not text or len(text) > MAX_ENTRY_CHARS
            or entity not in {"master", "neko", "relationship"}
            or target not in {"persona", "facts"}
            or not source_file
        ):
            raise HTTPException(status_code=400, detail="Invalid candidate fields")
        source_section = str(candidate.get("source_section") or "")
        event_date = candidate.get("event_date")
        if target == "persona":
            # Pass complete provenance (source_file / source_section / event_date) to fusion:
            # source_section structures the fusion prompt; source_file enters Phase 3 persisted metadata;
            # afuse_external_facts computes fingerprints from candidate text internally (idempotent reimport).
            persona_candidates_by_entity.setdefault(entity, []).append({
                "text": text,
                "entity": entity,
                "source_file": source_file,
                "source_section": source_section,
                "event_date": event_date,
            })
        elif event_date:
            # Daily diaries (memory/·memories/YYYY-MM-DD.md) are prose, not appended item by item;
            # aimport_external_daily performs per-day LLM fact extraction (see its docstring).
            daily_candidates.append({
                "text": text,
                "source_file": source_file,
                "source_section": source_section,
                "event_date": event_date,
            })
        else:
            # MEMORY.md is already a fact list: deterministic append-only.
            extracted_facts.append({
                "text": text,
                "entity": entity,
                "importance": 7,
                "source": "user_observation",
                "_external_import": {
                    "format": request.source_format,
                    "file": source_file,
                    "section": source_section,
                    "event_date": event_date,
                    "imported_at": imported_at,
                },
            })

    if explicit_language is not None:
        locale_order = await _routes.asyncio.to_thread(
            _routes.locale_state.reserve_character_prompt_locale_order,
            name,
            order=locale_admission_order,
        )
        await _routes.asyncio.to_thread(
            _routes.locale_state.record_character_prompt_locale,
            name,
            explicit_language,
            order=locale_order,
        )

    # ── Persona stage: concurrent per-entity LLM fusion (no append-only downgrade; see endpoint docstring) ──
    # Concurrency safety: afuse_external_facts Phase 1/3 serialize reads/writes under one character lock;
    # each entity rewrites only its own section (CAS also checks only that entity's fingerprint set).
    # Slow Phase 2 (LLM) is unlocked: only LLM round trips run concurrently; persistence is mutually exclusive.
    persona_entities = list(persona_candidates_by_entity.items())

    # Progress registry: persona counts per entity; daily counts come from the on_progress callback
    if progress is None:
        progress = {}
    _IMPORT_PROGRESS[name] = progress
    progress.update({
        'stage': 'persona',
        'persona_total': len(persona_entities),
        'persona_done': 0,
        'daily_total': 0,
        'daily_done': 0,
        'finished': False,
        'started_at': imported_at,
    })
    # Browser imports deliberately omit ``language``. Resolve the durable
    # character preference at execution time so a preference changed while the
    # user was reading/confirming the preview cannot be overwritten by a stale
    # frontend snapshot. This value is for prompt rendering only; the persistence
    # block above remains reserved for explicit API callers.
    memory_language = await _routes._resolve_foreground_memory_language(
        name,
        explicit_language,
        render_language=request.render_language,
    )
    with language_context(memory_language):
        async def _fuse_and_mark(entity: str, entity_candidates: list[dict]):
            outcome = await _routes.runtime.persona_manager.afuse_external_facts(
                name, entity, entity_candidates, request.source_format,
            )
            progress['persona_done'] = progress.get('persona_done', 0) + 1
            return outcome

        fusion_outcomes = await _routes.asyncio.gather(
            *(
                _fuse_and_mark(entity, entity_candidates)
                for entity, entity_candidates in persona_entities
            ),
            return_exceptions=True,
        )
    added_persona = sum(r["added"] for r in fusion_outcomes if isinstance(r, dict))
    skipped_persona = sum(r["skipped"] for r in fusion_outcomes if isinstance(r, dict))
    fusion_errors = [r for r in fusion_outcomes if isinstance(r, BaseException)]
    if fusion_errors:
        for exc in (e for e in fusion_errors if not isinstance(e, ExternalMemoryImportTooLargeError)):
            _routes.logger.error(
                "External Markdown import: persona fusion failed: character=%s",
                name, exc_info=exc,
            )
        if all(isinstance(e, ExternalMemoryImportTooLargeError) for e in fusion_errors):
            # All failures deterministically too large: candidates exceed the fusion input pool; identical retries must fail again.
            # (no fingerprint recorded) → return non-retryable too_large, prompting frontend to split the workspace.
            _routes.logger.warning(
                "External Markdown import: persona too large for single fusion: character=%s added_persona=%s",
                name,
                added_persona,
            )
            return JSONResponse(
                status_code=413,
                content={
                    "detail": "External memory import is too large for a single fusion pass",
                    "error_code": "external_import_too_large",
                    "partial_import": {
                        "character_name": name,
                        "added_persona": added_persona,
                        "added_facts": 0,
                    },
                },
            )
        # Retryable failures (terminal fusion failure ExternalMemoryFusionError / asave_persona crash,
        # etc., possibly mixed with too_large) → return partial for idempotent retries: successful entities
        # skip by fingerprint, transient failures converge, and remaining too_large naturally surfaces as 413.
        # Never fall back to per-entry append and overflow the persona pool.
        _routes.logger.error(
            "External Markdown import: persona stage failed: character=%s added_persona=%s",
            name,
            added_persona,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "External memory import was only partially completed",
                "error_code": "external_import_partial",
                "partial_import": {
                    "character_name": name,
                    "added_persona": added_persona,
                    "added_facts": 0,
                },
            },
        )

    try:
        new_facts = await _routes.runtime.fact_store._apersist_new_facts(
            name,
            extracted_facts,
            default_source="user_observation",
            semantic_dedup=False,
        )
    except Exception:
        _routes.logger.exception(
            "External Markdown import stopped after persona persistence: character=%s added_persona=%s",
            name,
            added_persona,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "External memory import was only partially completed",
                "error_code": "external_import_partial",
                "partial_import": {
                    "character_name": name,
                    "added_persona": added_persona,
                    "added_facts": 0,
                },
            },
        )
    memory_added = len(new_facts)

    # Daily diary → per-day best-effort LLM fact extraction (see aimport_external_daily).
    # Daily failure does not roll back persisted persona / MEMORY.md facts; systemic exceptions return partial.
    daily_added = 0
    if daily_candidates:
        try:
            with language_context(memory_language):
                def _on_daily_progress(update: dict) -> None:
                    progress.update(update)
                    progress['stage'] = 'daily'

                daily_result = await _routes.runtime.fact_store.aimport_external_daily(
                    name, daily_candidates, request.source_format, imported_at,
                    on_progress=_on_daily_progress,
                )
        except ExternalMemoryImportTooLargeError as exc:
            # Deterministic excess (a single day alone exceeds the per-import call cap): identical
            # retries must exceed again, so return too_large to guide splitting. Many days that each
            # fit do not land here; they resume in segments instead (see deferred_days below).
            _routes.logger.warning(
                "External Markdown import: daily too large: character=%s detail=%s",
                name, exc,
            )
            return JSONResponse(
                status_code=413,
                content={
                    "detail": str(exc),
                    "error_code": "external_import_too_large",
                    "partial_import": {
                        "character_name": name,
                        "added_persona": added_persona,
                        "added_facts": memory_added,
                    },
                },
            )
        except Exception:
            _routes.logger.exception(
                "External Markdown import: daily extraction failed after persona+memory: "
                "character=%s added_persona=%s memory_facts=%s",
                name, added_persona, memory_added,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "External memory import was only partially completed",
                    "error_code": "external_import_partial",
                    "partial_import": {
                        "character_name": name,
                        "added_persona": added_persona,
                        "added_facts": memory_added,
                    },
                },
            )
        daily_added = daily_result["added"]
        failed_days = daily_result["failed_days"]
        deferred_days = daily_result.get("deferred_days", 0)
        oversized_days = daily_result.get("oversized_days", 0)
        if failed_days or deferred_days:
            # Failed or deferred (segmented resume) diary days forbid success (the client would treat
            # the import as complete, permanently losing the remaining days with no retry signal,
            # Greptile P1); return a retryable partial. Retries converge: persona skips idempotently by
            # fingerprint; SHA/FTS5 deduplication blocks MEMORY.md and already extracted daily facts;
            # finished days skip by daily fingerprint; only failed and deferred days actually extract.
            _routes.logger.warning(
                "External Markdown import: daily journals incomplete: character=%s "
                "failed=%s deferred=%s",
                name, failed_days, deferred_days,
            )
            detail_parts = []
            if failed_days:
                detail_parts.append(f"{failed_days} daily journal(s) failed extraction")
            if deferred_days:
                detail_parts.append(
                    f"{deferred_days} daily journal(s) deferred to the next import"
                )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "; ".join(detail_parts) + "; retry to finish",
                    "error_code": "external_import_partial",
                    "partial_import": {
                        "character_name": name,
                        "added_persona": added_persona,
                        "added_facts": memory_added + daily_added,
                        "failed_days": failed_days,
                        "remaining_days": deferred_days,
                        "oversized_days": oversized_days,
                    },
                },
            )
        if oversized_days:
            # Every other day is imported and only days that alone exceed the cap remain: retrying cannot progress -> too_large
            # guides splitting, carrying the counts persisted by this import (the frontend broadcasts memory_edited from them).
            _routes.logger.warning(
                "External Markdown import: %s daily journal(s) alone exceed the call cap: "
                "character=%s",
                oversized_days, name,
            )
            return JSONResponse(
                status_code=413,
                content={
                    "detail": (
                        f"{oversized_days} daily journal(s) each need more extraction "
                        "calls than one import allows; split those days"
                    ),
                    "error_code": "external_import_too_large",
                    "partial_import": {
                        "character_name": name,
                        "added_persona": added_persona,
                        "added_facts": memory_added + daily_added,
                        "oversized_days": oversized_days,
                    },
                },
            )

    added_facts = memory_added + daily_added
    skipped_facts = len(extracted_facts) - memory_added
    return {
        "status": "success",
        "character_name": name,
        "source_format": request.source_format,
        "imported_files": request.imported_files,
        "added_persona": added_persona,
        "added_facts": added_facts,
        "skipped_duplicates": skipped_persona + skipped_facts,
        "warning_count": max(0, request.warning_count),
    }
