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
"""Internal memory-browser CRUD, search, star-map, lineage, and snapshot routes."""

from typing import Literal
from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from utils.language_utils import get_global_language_full, normalize_language_code
from utils.cloudsave_runtime import MaintenanceModeError
from .runtime import app
from contextlib import contextmanager

from . import routes as _routes

class _BrowserPersonaEditRequest(BaseModel):
    character_name: str
    entity: str
    entry_id: str
    text: str


class _BrowserPersonaFlagsRequest(BaseModel):
    character_name: str
    entity: str
    entry_id: str
    user_pinned: bool | None = None
    suppress: bool | None = None


class _BrowserPersonaArchiveRequest(BaseModel):
    character_name: str
    entity: str
    entry_id: str


class _BrowserPersonaRestoreRequest(BaseModel):
    character_name: str
    entity: str
    entry_id: str
    version_index: int
    expected_text: str | None = None
    expected_replaced_at: str | None = None


class _BrowserCorrectionResolveRequest(BaseModel):
    character_name: str
    correction_index: int | None = None
    correction_id: str | None = None
    expected_old_text: str | None = None
    expected_new_text: str | None = None
    expected_entity: str | None = None
    expected_scope: str | None = None
    expected_queued_at: str | None = None
    action: Literal["keep_new", "keep_old", "merge", "keep_both"]
    merged_text: str | None = None


class _BrowserReflectionReviewRequest(BaseModel):
    character_name: str
    reflection_id: str
    decision: Literal["confirm", "edit", "forget"]
    edited_text: str | None = None


class _BrowserFactEditRequest(BaseModel):
    character_name: str
    fact_id: str
    text: str


class _BrowserFactArchiveRequest(BaseModel):
    character_name: str
    fact_id: str


class _BrowserDuplicateMergeRequest(BaseModel):
    character_name: str
    kind: Literal["persona", "reflection", "fact"]
    entity: str
    keep_id: str
    drop_id: str
    merged_text: str


class _BrowserDirectiveRequest(BaseModel):
    character_name: str
    term: str
    language: str | None = None


class _BrowserImprintNoteRequest(BaseModel):
    language: str | None = None


class _BrowserSearchRequest(BaseModel):
    query: str
    top_k: int = 20


@app.get(
    "/internal/memory/browser/overview/{lanlan_name}",
    dependencies=[Depends(_routes._refuse_browsers)],
)
async def browser_memory_overview(lanlan_name: str):
    """Return read-only Imprint, editable long-term memory groups, and 6-axis health diagnostics."""
    name = _routes.validate_lanlan_name(lanlan_name)
    from memory.analytics import abuild_memory_overview
    try:
        data = await abuild_memory_overview(
            name,
            memory_dir=_routes.runtime._config_manager.memory_dir,
        )
        return {"success": True, **data}
    except Exception as exc:
        _routes.logger.exception("[MemoryBrowser] overview failed for %s", name)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.post(
    "/internal/memory/browser/imprint_note/{lanlan_name}",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_generate_imprint_note(
    lanlan_name: str,
    request: _BrowserImprintNoteRequest,
):
    """Manually trigger warm relationship summary/commentary/blessing generation and cache it."""
    name = _routes.validate_lanlan_name(lanlan_name)
    from memory.analytics import agenerate_imprint_note
    try:
        note = await agenerate_imprint_note(
            name,
            language=request.language,
            config_manager=_routes.runtime._config_manager,
        )
        return {"success": True, "note": note}
    except MaintenanceModeError:
        raise
    except Exception as exc:
        _routes.logger.warning("[MemoryBrowser] imprint note failed for %s: %s", name, exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.post(
    "/internal/memory/browser/search/{lanlan_name}",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_search_memory(lanlan_name: str, request: _BrowserSearchRequest):
    """Search across persona entries + hybrid recall (facts & reflections)."""
    name = _routes.validate_lanlan_name(lanlan_name)
    query = str(request.query or "").strip()
    if not query:
        return {"success": True, "results": []}
    top_k = max(1, min(int(request.top_k or 20), 50))
    results: list[dict] = []
    q_low = query.casefold()

    # 1. Direct read-only persona match (never calls aensure_persona so search cannot write persona.json).
    try:
        from pathlib import Path
        from memory.analytics.imprint import _read_json_safe
        persona_path = Path(_routes.runtime._config_manager.memory_dir) / name / "persona.json"
        persona = await _routes.asyncio.to_thread(_read_json_safe, persona_path, {})
        if isinstance(persona, dict):
            for entity_key in ("master", "relationship", "neko"):
                section = persona.get(entity_key)
                if not isinstance(section, dict):
                    continue
                for entry in section.get("facts", []):
                    if not isinstance(entry, dict):
                        continue
                    text = str(entry.get("text") or "").strip()
                    if not text or q_low not in text.casefold():
                        continue
                    is_protected = bool(entry.get("protected")) or entry.get("source") == "character_card"
                    results.append({
                        "kind": "persona",
                        "id": str(entry.get("id") or ""),
                        "entity": entity_key,
                        "text": text,
                        "protected": is_protected,
                        "user_pinned": bool(entry.get("user_pinned")),
                        "suppress": bool(entry.get("suppress")),
                        "permanent_suppression": bool(entry.get("permanent_suppression")),
                        "created_at": entry.get("created_at") or entry.get("promoted_at"),
                        "subject_kind": str(entry.get("subject_kind") or ""),
                        "subject_id": str(entry.get("subject_id") or ""),
                        "scope": str(entry.get("scope") or ""),
                        "score": 1.0,
                    })
    except Exception as exc:
        _routes.logger.debug("[MemoryBrowser] persona search error: %s", exc)

    # 2. Hybrid recall over reflections + facts (BM25 + local ONNX vectors when available).
    try:
        from memory.hybrid_recall import hybrid_recall
        recalled_payload = await hybrid_recall(
            lanlan_name=name,
            query=query,
            fact_store=_routes.runtime.fact_store,
            reflection_engine=_routes.runtime.reflection_engine,
            config_manager=_routes.runtime._config_manager,
        )
        recalled_items = (
            recalled_payload.get("results")
            if isinstance(recalled_payload, dict)
            else recalled_payload
        ) or []
        from memory.scopes import LEGACY_PRIVATE_SCOPE

        seen_ids = {(r["kind"], r["id"]) for r in results if r.get("id")}
        for item in recalled_items:
            if not isinstance(item, dict):
                continue
            tier = str(item.get("tier") or item.get("type") or item.get("kind") or "fact")
            kind = "reflection" if "reflection" in tier else "fact"
            is_archived_hit = "archive" in tier or bool(item.get("archived_at"))
            iid = str(item.get("id") or "")
            if iid and (kind, iid) in seen_ids:
                continue
            seen_ids.add((kind, iid))
            subj_kind = str(item.get("subject_kind") or "")
            subj_id = str(item.get("subject_id") or "")
            raw_scope = str(item.get("scope") or "")
            if raw_scope == LEGACY_PRIVATE_SCOPE and not subj_kind and not subj_id:
                raw_scope = ""
            results.append({
                "kind": kind,
                "id": iid,
                "entity": str(item.get("entity") or "master"),
                "text": str(item.get("text") or "").strip(),
                "status": item.get("status"),
                "archived": is_archived_hit,
                "protected": False,
                "created_at": item.get("created_at"),
                "subject_kind": subj_kind,
                "subject_id": subj_id,
                "scope": raw_scope,
                "score": float(item.get("score") or 0.0),
            })
    except Exception as exc:
        _routes.logger.debug("[MemoryBrowser] hybrid recall fallback to substring: %s", exc)
        # Fallback substring search across reflections & facts if hybrid_recall isn't ready.
        if _routes.runtime.reflection_engine is not None:
            try:
                refls = await _routes.runtime.reflection_engine.aload_reflections(name, include_archived=False)
                for r in refls:
                    if not isinstance(r, dict):
                        continue
                    text = str(r.get("text") or "").strip()
                    if text and q_low in text.casefold():
                        results.append({
                            "kind": "reflection",
                            "id": str(r.get("id") or ""),
                            "entity": str(r.get("entity") or "master"),
                            "text": text,
                            "status": r.get("status"),
                            "created_at": r.get("created_at"),
                            "subject_kind": str(r.get("subject_kind") or ""),
                            "subject_id": str(r.get("subject_id") or ""),
                            "scope": str(r.get("scope") or ""),
                            "score": 0.8,
                        })
            except Exception as refl_exc:
                _routes.logger.debug("[MemoryBrowser] reflection fallback search skipped: %s", refl_exc)
        if _routes.runtime.fact_store is not None:
            try:
                facts = await _routes.runtime.fact_store.aload_facts(name)
                for f in facts:
                    if not isinstance(f, dict):
                        continue
                    text = str(f.get("text") or "").strip()
                    if text and q_low in text.casefold():
                        results.append({
                            "kind": "fact",
                            "id": str(f.get("id") or ""),
                            "entity": str(f.get("entity") or "master"),
                            "text": text,
                            "created_at": f.get("created_at"),
                            "subject_kind": str(f.get("subject_kind") or ""),
                            "subject_id": str(f.get("subject_id") or ""),
                            "scope": str(f.get("scope") or ""),
                            "score": 0.7,
                        })
            except Exception as fact_exc:
                _routes.logger.debug("[MemoryBrowser] fact fallback search skipped: %s", fact_exc)

    return {"success": True, "results": results[:top_k]}


def _queue_long_term_capture(name: str, *, force: bool = False) -> None:
    """Let multi-device sync publish what a Memory Browser write just saved."""
    try:
        from .long_term_sync import queue_capture
        queue_capture(name, force=force)
    except Exception as exc:
        _routes.logger.debug("[MemoryBrowser] sync capture not queued for %s: %s", name, exc)


@contextmanager
def _admit_browser_character_write(name: str):
    """Register body-addressed browser mutations in the character release-draining ledger."""
    token = _routes.runtime._begin_character_request(name)
    if token is None:
        raise HTTPException(status_code=409, detail="character release in progress")
    try:
        yield
    finally:
        _routes.runtime._end_character_request(name, token)


@app.post(
    "/internal/memory/browser/persona/edit",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_edit_persona(request: _BrowserPersonaEditRequest):
    name = _routes.validate_lanlan_name(request.character_name)
    if _routes.runtime.persona_manager is None:
        raise HTTPException(status_code=503, detail="PersonaManager not ready")
    with _admit_browser_character_write(name):
        try:
            updated = await _routes.runtime.persona_manager.aedit_persona_entry(
                name, request.entity, request.entry_id, request.text,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if updated is None:
        raise HTTPException(status_code=404, detail="Persona entry not found")
    _routes._queue_long_term_capture(name)
    return {"success": True, "entry": updated}


@app.post(
    "/internal/memory/browser/persona/flags",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_set_persona_flags(request: _BrowserPersonaFlagsRequest):
    name = _routes.validate_lanlan_name(request.character_name)
    if _routes.runtime.persona_manager is None:
        raise HTTPException(status_code=503, detail="PersonaManager not ready")
    with _admit_browser_character_write(name):
        try:
            updated = await _routes.runtime.persona_manager.aset_persona_flags(
                name,
                request.entity,
                request.entry_id,
                user_pinned=request.user_pinned,
                suppress=request.suppress,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
    if updated is None:
        raise HTTPException(status_code=404, detail="Persona entry not found")
    _routes._queue_long_term_capture(name)
    return {"success": True, "entry": updated}


@app.post(
    "/internal/memory/browser/persona/archive",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_archive_persona(request: _BrowserPersonaArchiveRequest):
    name = _routes.validate_lanlan_name(request.character_name)
    if _routes.runtime.persona_manager is None:
        raise HTTPException(status_code=503, detail="PersonaManager not ready")
    with _admit_browser_character_write(name):
        ok = await _routes.runtime.persona_manager.aarchive_persona_entry(
            name, request.entity, request.entry_id,
        )
    if not ok:
        raise HTTPException(status_code=404, detail="Persona entry not found or protected")
    _routes._queue_long_term_capture(name)
    return {"success": True}


@app.post(
    "/internal/memory/browser/persona/restore_version",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_restore_persona_version(request: _BrowserPersonaRestoreRequest):
    name = _routes.validate_lanlan_name(request.character_name)
    if _routes.runtime.persona_manager is None:
        raise HTTPException(status_code=503, detail="PersonaManager not ready")
    with _admit_browser_character_write(name):
        try:
            updated = await _routes.runtime.persona_manager.arestore_persona_version(
                name,
                request.entity,
                request.entry_id,
                request.version_index,
                expected_text=request.expected_text,
                expected_replaced_at=request.expected_replaced_at,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except (IndexError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    if updated is None:
        raise HTTPException(status_code=404, detail="Persona entry not found")
    _routes._queue_long_term_capture(name)
    return {"success": True, "entry": updated}


@app.post(
    "/internal/memory/browser/correction/resolve",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_resolve_correction(request: _BrowserCorrectionResolveRequest):
    name = _routes.validate_lanlan_name(request.character_name)
    if _routes.runtime.persona_manager is None:
        raise HTTPException(status_code=503, detail="PersonaManager not ready")
    with _admit_browser_character_write(name):
        try:
            res = await _routes.runtime.persona_manager.aresolve_correction_manual(
                name,
                request.correction_index,
                request.action,
                merged_text=request.merged_text,
                correction_id=request.correction_id,
                expected_old_text=request.expected_old_text,
                expected_new_text=request.expected_new_text,
                expected_entity=request.expected_entity,
                expected_scope=request.expected_scope,
                expected_queued_at=request.expected_queued_at,
            )
        except (IndexError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    _routes._queue_long_term_capture(name)
    return {"success": True, **res}


@app.post(
    "/internal/memory/browser/reflection/review",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_review_reflection(request: _BrowserReflectionReviewRequest):
    name = _routes.validate_lanlan_name(request.character_name)
    if _routes.runtime.reflection_engine is None:
        raise HTTPException(status_code=503, detail="ReflectionEngine not ready")
    with _admit_browser_character_write(name):
        try:
            res = await _routes.runtime.reflection_engine.amanual_review_reflection(
                name,
                request.reflection_id,
                request.decision,
                edited_text=request.edited_text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if res is None:
        raise HTTPException(status_code=404, detail="Reflection not found")
    _routes._queue_long_term_capture(name)
    return {"success": True, **res}


@app.post(
    "/internal/memory/browser/fact/edit",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_edit_fact(request: _BrowserFactEditRequest):
    name = _routes.validate_lanlan_name(request.character_name)
    if _routes.runtime.fact_store is None:
        raise HTTPException(status_code=503, detail="FactStore not ready")
    with _admit_browser_character_write(name):
        try:
            updated = await _routes.runtime.fact_store.aedit_fact_manual(
                name, request.fact_id, request.text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if updated is None:
        raise HTTPException(status_code=404, detail="Fact not found")
    _routes._queue_long_term_capture(name)
    return {"success": True, "fact": updated}


@app.post(
    "/internal/memory/browser/fact/archive",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_archive_fact(request: _BrowserFactArchiveRequest):
    name = _routes.validate_lanlan_name(request.character_name)
    if _routes.runtime.fact_store is None:
        raise HTTPException(status_code=503, detail="FactStore not ready")
    with _admit_browser_character_write(name):
        try:
            ok = await _routes.runtime.fact_store.aarchive_fact_manual(name, request.fact_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if not ok:
        raise HTTPException(status_code=404, detail="Fact not found")
    _routes._queue_long_term_capture(name)
    return {"success": True}


@app.post(
    "/internal/memory/browser/duplicate/merge",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_merge_duplicate(request: _BrowserDuplicateMergeRequest):
    name = _routes.validate_lanlan_name(request.character_name)
    merged = str(request.merged_text or "").strip()
    if not merged or len(merged) > 1000:
        raise HTTPException(status_code=400, detail="merged_text must be 1..1000 chars")
    if request.keep_id == request.drop_id:
        raise HTTPException(status_code=400, detail="keep_id and drop_id must differ")

    async def _take_pre_merge_snapshot() -> None:
        try:
            from memory.analytics import acreate_snapshot
            await acreate_snapshot(
                name,
                label="Before duplicate merge",
                reason="pre_merge",
                config_manager=_routes.runtime._config_manager,
                persona_manager=_routes.runtime.persona_manager,
                fact_store=_routes.runtime.fact_store,
                reflection_engine=_routes.runtime.reflection_engine,
            )
        except MaintenanceModeError:
            raise
        except Exception as exc:
            _routes.logger.debug("[MemoryBrowser] pre-merge snapshot skipped for %s: %s", name, exc)

    with _admit_browser_character_write(name):
        if request.kind == "persona":
            if _routes.runtime.persona_manager is None:
                raise HTTPException(status_code=503, detail="PersonaManager not ready")
            # Fast pre-check before taking a snapshot; atomic re-validation runs under manager lock.
            persona_view = await _routes.runtime.persona_manager.aensure_persona(name)
            section = persona_view.get(request.entity) if isinstance(persona_view, dict) else None
            facts_list = section.get("facts", []) if isinstance(section, dict) else []
            by_id = {
                str(e.get("id") or ""): e
                for e in facts_list
                if isinstance(e, dict) and e.get("id")
            }
            keep_obj = by_id.get(request.keep_id)
            drop_obj = by_id.get(request.drop_id)
            if keep_obj is None:
                raise HTTPException(status_code=404, detail="keep_id not found")
            if drop_obj is None:
                raise HTTPException(status_code=404, detail="drop_id not found")
            if (
                keep_obj.get("protected") or keep_obj.get("source") == "character_card"
                or drop_obj.get("protected") or drop_obj.get("source") == "character_card"
            ):
                raise HTTPException(status_code=403, detail="Character card entries are protected")

            await _take_pre_merge_snapshot()
            try:
                updated = await _routes.runtime.persona_manager.amerge_persona_entries(
                    name, request.entity, request.keep_id, request.drop_id, merged,
                )
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            _routes._queue_long_term_capture(name)
            return {"success": True, "kept": updated, "dropped": True}

        if request.kind == "fact":
            if _routes.runtime.fact_store is None:
                raise HTTPException(status_code=503, detail="FactStore not ready")
            facts = await _routes.runtime.fact_store.aload_facts(name)
            fact_ids = {str(f.get("id") or "") for f in facts if isinstance(f, dict)}
            if request.keep_id not in fact_ids:
                raise HTTPException(status_code=404, detail="keep_id not found")
            if request.drop_id not in fact_ids:
                raise HTTPException(status_code=404, detail="drop_id not found")

            await _take_pre_merge_snapshot()
            try:
                updated = await _routes.runtime.fact_store.amerge_facts_manual(
                    name, request.keep_id, request.drop_id, merged,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            _routes._queue_long_term_capture(name)
            return {"success": True, "kept": updated, "dropped": True}

        if _routes.runtime.reflection_engine is None:
            raise HTTPException(status_code=503, detail="ReflectionEngine not ready")
        # Include pending, confirmed, AND promoted reflections (matching overview duplicate pairs).
        refls = await _routes.runtime.reflection_engine._aload_reflections_full(name)
        eligible_ids = {
            str(r.get("id") or "")
            for r in refls
            if isinstance(r, dict) and str(r.get("status") or "pending") not in ("archived", "merged", "denied")
        }
        if request.keep_id not in eligible_ids:
            raise HTTPException(status_code=404, detail="keep_id not found")
        if request.drop_id not in eligible_ids:
            raise HTTPException(status_code=404, detail="drop_id not found")

        await _take_pre_merge_snapshot()
        try:
            updated = await _routes.runtime.reflection_engine.amerge_reflections(
                name, request.keep_id, request.drop_id, merged,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        _routes._queue_long_term_capture(name)
        return {"success": True, "kept": updated, "dropped": True}


@app.post(
    "/internal/memory/browser/directive/add",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_add_directive(request: _BrowserDirectiveRequest):
    from memory.user_directives import _normalize_term, get_user_directives_manager
    name = _routes.validate_lanlan_name(request.character_name)
    if _normalize_term(request.term) is None:
        raise HTTPException(status_code=400, detail="Directive term must be 2..40 characters")
    with _admit_browser_character_write(name):
        _routes.assert_cloudsave_writable(
            _routes.runtime._config_manager,
            operation="save",
            target=f"memory/{name}/user_directives.json",
        )
        locale = normalize_language_code(request.language or get_global_language_full(), format="short")
        created = await get_user_directives_manager().arecord(
            name,
            locale=locale,
            kind="ban_topic",
            term=request.term,
            source="manual",
        )
    if not created:
        raise HTTPException(
            status_code=409,
            detail="Directive could not be retained because the directive list is at capacity with newer entries",
        )
    _routes._queue_long_term_capture(name)
    return {"success": True, "directive": created}


@app.post(
    "/internal/memory/browser/directive/remove",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_remove_directive(request: _BrowserDirectiveRequest):
    from memory.user_directives import get_user_directives_manager
    name = _routes.validate_lanlan_name(request.character_name)
    with _admit_browser_character_write(name):
        _routes.assert_cloudsave_writable(
            _routes.runtime._config_manager,
            operation="save",
            target=f"memory/{name}/user_directives.json",
        )
        removed = await get_user_directives_manager().aremove(name, request.term)
    if not removed:
        raise HTTPException(status_code=404, detail="Directive not found")
    _routes._queue_long_term_capture(name)
    return {"success": True}


class _BrowserStarMapLabelRequest(BaseModel):
    language: str | None = None


class _BrowserRelatedMemoriesRequest(BaseModel):
    kind: Literal["persona", "reflection", "fact"]
    entry_id: str
    text: str = ""
    top_k: int = 4
    subject_kind: str | None = None
    subject_id: str | None = None
    scope: str | None = None


@app.get(
    "/internal/memory/browser/star_map/{lanlan_name}",
    dependencies=[Depends(_routes._refuse_browsers)],
)
async def browser_memory_star_map(lanlan_name: str):
    """Return read-only 2D PCA Memory Star Map + high-dim clusters + cached theme labels."""
    name = _routes.validate_lanlan_name(lanlan_name)
    from memory.analytics import abuild_star_map
    try:
        data = await abuild_star_map(
            name,
            memory_dir=_routes.runtime._config_manager.memory_dir,
        )
        return {"success": True, **data}
    except Exception as exc:
        _routes.logger.exception("[MemoryBrowser] star_map failed for %s", name)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.post(
    "/internal/memory/browser/star_map/label_clusters/{lanlan_name}",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_label_star_map_clusters(
    lanlan_name: str,
    request: _BrowserStarMapLabelRequest,
):
    """Manually trigger summary-tier theme labeling for Star Map clusters and cache it."""
    name = _routes.validate_lanlan_name(lanlan_name)
    from memory.analytics import alabel_star_map_clusters
    from memory.analytics.star_map import StarMapLabelModelNotConfigured
    try:
        res = await alabel_star_map_clusters(
            name,
            language=request.language,
            config_manager=_routes.runtime._config_manager,
            force=True,
        )
        return {"success": True, **res}
    except MaintenanceModeError:
        raise
    except StarMapLabelModelNotConfigured:
        return JSONResponse(
            {"success": False, "error": "summary_model_not_configured"}, status_code=409,
        )
    except Exception as exc:
        _routes.logger.warning("[MemoryBrowser] star_map label failed for %s: %s", name, exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.get(
    "/internal/memory/browser/lineage/{lanlan_name}",
    dependencies=[Depends(_routes._refuse_browsers)],
)
async def browser_memory_lineage(
    lanlan_name: str,
    kind: Literal["persona", "reflection", "fact"],
    entry_id: str,
):
    """Return 3-step lineage story (conversations -> facts -> impression) for one memory."""
    name = _routes.validate_lanlan_name(lanlan_name)
    from memory.analytics import abuild_memory_lineage_story
    try:
        story = await abuild_memory_lineage_story(
            name,
            kind=kind,
            entry_id=entry_id,
            memory_dir=_routes.runtime._config_manager.memory_dir,
        )
        if story is None:
            raise HTTPException(status_code=404, detail="Memory entry not found")
        return {"success": True, **story}
    except HTTPException:
        raise
    except Exception as exc:
        _routes.logger.exception("[MemoryBrowser] lineage failed for %s", name)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.post(
    "/internal/memory/browser/related/{lanlan_name}",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_related_memories(
    lanlan_name: str,
    request: _BrowserRelatedMemoriesRequest,
):
    """Find closely related memories for cascade review after editing/forgetting an entry."""
    name = _routes.validate_lanlan_name(lanlan_name)
    from memory.analytics import afind_related_memories
    try:
        items = await afind_related_memories(
            name,
            kind=request.kind,
            entry_id=request.entry_id,
            text=request.text,
            top_k=request.top_k,
            memory_dir=_routes.runtime._config_manager.memory_dir,
            subject_kind=request.subject_kind,
            subject_id=request.subject_id,
            scope=request.scope,
        )
        return {"success": True, "related": items}
    except Exception as exc:
        _routes.logger.exception("[MemoryBrowser] related memories failed for %s", name)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


class _BrowserSnapshotCreateRequest(BaseModel):
    label: str = ""
    reason: str = "manual"


class _BrowserSnapshotRestoreRequest(BaseModel):
    snapshot_id: str


class _BrowserDeepConflictRequest(BaseModel):
    language: str | None = None


@app.get(
    "/internal/memory/browser/snapshots/{lanlan_name}",
    dependencies=[Depends(_routes._refuse_browsers)],
)
async def browser_list_snapshots(lanlan_name: str):
    """List per-character safety snapshots (newest first)."""
    name = _routes.validate_lanlan_name(lanlan_name)
    from memory.analytics import alist_snapshots
    try:
        items = await alist_snapshots(name, memory_dir=_routes.runtime._config_manager.memory_dir)
        return {"success": True, "snapshots": items}
    except Exception as exc:
        _routes.logger.exception("[MemoryBrowser] list_snapshots failed for %s", name)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.post(
    "/internal/memory/browser/snapshots/create/{lanlan_name}",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_create_snapshot(
    lanlan_name: str,
    request: _BrowserSnapshotCreateRequest,
):
    """Create a per-character safety snapshot."""
    name = _routes.validate_lanlan_name(lanlan_name)
    from memory.analytics import acreate_snapshot
    try:
        snap = await acreate_snapshot(
            name,
            label=request.label,
            reason=request.reason,
            config_manager=_routes.runtime._config_manager,
            persona_manager=_routes.runtime.persona_manager,
            fact_store=_routes.runtime.fact_store,
            reflection_engine=_routes.runtime.reflection_engine,
        )
        return {"success": True, "snapshot": snap}
    except MaintenanceModeError:
        raise
    except Exception as exc:
        _routes.logger.exception("[MemoryBrowser] create_snapshot failed for %s", name)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.post(
    "/internal/memory/browser/snapshots/restore/{lanlan_name}",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_restore_snapshot(
    lanlan_name: str,
    request: _BrowserSnapshotRestoreRequest,
):
    """Restore a safety snapshot under manager locks with automatic pre-restore backup."""
    name = _routes.validate_lanlan_name(lanlan_name)
    from memory.analytics import arestore_snapshot
    from .long_term_sync import _views_locked
    if (
        _routes.runtime.persona_manager is None
        or _routes.runtime.fact_store is None
        or _routes.runtime.reflection_engine is None
        or _routes.runtime.event_log is None
    ):
        raise HTTPException(status_code=503, detail="Memory managers not ready")
    try:
        result = await arestore_snapshot(
            name,
            request.snapshot_id,
            config_manager=_routes.runtime._config_manager,
            persona_manager=_routes.runtime.persona_manager,
            fact_store=_routes.runtime.fact_store,
            reflection_engine=_routes.runtime.reflection_engine,
            event_log=_routes.runtime.event_log,
            views_lock=_views_locked,
        )
    except MaintenanceModeError:
        raise
    except ValueError as exc:
        if str(exc) == "character_release_in_progress":
            raise HTTPException(status_code=409, detail="character release in progress")
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        _routes.logger.exception("[MemoryBrowser] restore_snapshot failed for %s", name)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
    _routes._queue_long_term_capture(name, force=True)
    return {"success": True, **result}


@app.get(
    "/internal/memory/browser/export/{lanlan_name}",
    dependencies=[Depends(_routes._refuse_browsers)],
)
async def browser_export_memory_zip(
    lanlan_name: str,
    mode: Literal["keepsake", "diagnostics"] = "keepsake",
):
    """Build and download a keepsake or redacted diagnostics ZIP archive."""
    name = _routes.validate_lanlan_name(lanlan_name)
    from urllib.parse import quote
    from memory.analytics import abuild_memory_export_zip
    try:
        zip_bytes, filename = await abuild_memory_export_zip(
            name,
            mode=mode,
            memory_dir=_routes.runtime._config_manager.memory_dir,
        )
        ascii_fallback = f"memory_{mode}.zip"
        headers = {
            "Content-Disposition": (
                f'attachment; filename="{ascii_fallback}"; '
                f"filename*=UTF-8''{quote(filename)}"
            ),
            "X-Memory-Export-Filename": quote(filename),
        }
        return Response(content=zip_bytes, media_type="application/zip", headers=headers)
    except Exception as exc:
        _routes.logger.exception("[MemoryBrowser] export zip failed for %s", name)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.get(
    "/internal/memory/browser/lineage_graph/{lanlan_name}",
    dependencies=[Depends(_routes._refuse_browsers)],
)
async def browser_full_lineage_graph(lanlan_name: str):
    """Return Developer Mode 5-lane causal DAG (message -> memo -> fact -> reflection -> persona)."""
    name = _routes.validate_lanlan_name(lanlan_name)
    from memory.analytics import abuild_full_lineage_graph
    try:
        graph = await abuild_full_lineage_graph(
            name,
            memory_dir=_routes.runtime._config_manager.memory_dir,
        )
        return {"success": True, **graph}
    except Exception as exc:
        _routes.logger.exception("[MemoryBrowser] lineage_graph failed for %s", name)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.post(
    "/internal/memory/browser/deep_conflicts/{lanlan_name}",
    dependencies=[Depends(_routes._refuse_browsers), Depends(_routes._require_json_content_type)],
)
async def browser_deep_conflict_check(
    lanlan_name: str,
    request: _BrowserDeepConflictRequest,
):
    """Run manual summary-tier semantic conflict/duplicate check (Developer Mode)."""
    name = _routes.validate_lanlan_name(lanlan_name)
    from memory.analytics import arun_deep_conflict_check
    try:
        result = await arun_deep_conflict_check(
            name,
            language=request.language,
            config_manager=_routes.runtime._config_manager,
        )
        return {"success": True, **result}
    except Exception as exc:
        _routes.logger.warning("[MemoryBrowser] deep_conflict_check failed for %s: %s", name, exc)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)
