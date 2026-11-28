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
"""Repetition insight and habitual phrase endpoints for memory_router."""

import asyncio
import re
from typing import Literal, Optional
from urllib.parse import quote
from pydantic import BaseModel, Field
from utils.character_name import PROFILE_NAME_MAX_UNITS, validate_character_name
from utils.character_memory import is_legacy_vector_store_dir, iter_character_memory_roots
from utils.cloudsave_runtime import MaintenanceModeError
from fastapi.responses import JSONResponse

from main_routers import memory_router as _mr
from ._shared import (
    REPETITION_INSIGHT_LANGUAGES,
    logger,
    router,
)
from .paths import (
    _configured_character_key,
    _default_memory_dir_escapes_root,
    _insight_selectable_name,
    extract_catgirl_name_from_recent_filename,
    iter_recent_memory_files,
)

class RepetitionInsightsRequest(BaseModel):
    character_name: str
    language: str
    assistant_message_limit: int = Field(default=100, ge=3, le=100)
    # Kept only for compatibility with older clients. Message-scoped reports
    # no longer use this value.
    effect_days: Literal[7, 30, 90] = 30


class RepetitionEffectsResetRequest(BaseModel):
    character_name: str


def _empty_repetition_effects(days: int) -> dict:
    return {
        "schema_version": "anti-repeat-effects/v1",
        "source_available": False,
        "started_at": 0.0,
        "period_days": days,
        "totals": {
            "soft_hint_injected": 0,
            "phrase_hint_injected": 0,
            "detected": 0,
            "regen_triggered": 0,
            "regen_guard_passed": 0,
            "blocked_delivery": 0,
            "break_reminder_suppressed": 0,
            "abandoned_user_interaction": 0,
            "unattributed": 0,
        },
        "reason_counts": {
            "bm25": 0,
            "literal_similarity": 0,
            "unanswered_repeat": 0,
        },
        "bm25": {
            "pair_count": 0,
            "average_before": 0.0,
            "average_after": 0.0,
            "reduction_ratio": 0.0,
        },
        "patterns": [],
    }


def _empty_message_scoped_repetition_effects(limit: int) -> dict:
    effects = _empty_repetition_effects(30)
    effects.pop("period_days", None)
    effects.update(
        {
            "scope_type": "assistant_messages",
            "assistant_message_limit": limit,
            "linked_message_count": 0,
        }
    )
    return effects


def _is_safe_containment_phrase(language: str, phrase: str) -> bool:
    compact = re.sub(r"\s+", "", phrase)
    if language in {"ja", "ko", "zh-CN", "zh-TW"}:
        return len(compact) >= 4
    return len(phrase) >= 4 and len(phrase.split()) >= 2


def _is_runtime_detector_signature(language: str, phrase: str, reasons: object) -> bool:
    if not isinstance(reasons, dict) or not any(
        int(reasons.get(reason, 0)) > 0 for reason in ("bm25", "unanswered_repeat")
    ):
        return False
    compact = re.sub(r"\s+", "", phrase)
    if language in {"ja", "ko", "zh-CN", "zh-TW"}:
        return len(compact) in {2, 3}
    return len(phrase) >= 2 and len(phrase.split()) == 1


def _repetition_association_language(language: str) -> str:
    """Use one comparison key for legacy Simplified Chinese effect records."""
    return "zh-CN" if language in {"zh", "zh-CN"} else language


def _phrases_contain_each_other(language: str, left: str, right: str) -> bool:
    left_tokens = left.split()
    right_tokens = right.split()
    use_token_boundaries = language in {"en", "es", "pt", "ru"} or (
        language == "ko" and len(left_tokens) > 1 and len(right_tokens) > 1
    )
    if not use_token_boundaries:
        return left in right or right in left

    shorter, longer = sorted((left_tokens, right_tokens), key=len)
    width = len(shorter)
    return any(
        longer[start : start + width] == shorter
        for start in range(len(longer) - width + 1)
    )


def _aggregate_repetition_associations(associations: list[dict]) -> list[dict]:
    """Fold per-pattern associations into one row per candidate.

    The panel only ever reduces these to four totals plus an "any at all?"
    test (``static/js/memory_browser.js`` at 1208 and 1461); no consumer reads
    the per-pattern fields. Shipping one row per (candidate, pattern) pair made
    the payload the PRODUCT of two capped lists -- 200 candidates against up to
    1920 window patterns -- measured at 5,366 rows / 1.62 MiB at that cap, and
    24,036 rows / 7.94 MiB for a character whose n-grams all contain one
    another. Folding bounds it by the candidate count instead, and every
    displayed number stays identical because the panel was summing anyway.

    Capping the list instead would have been wrong: a truncated array turns
    those sums into silently WRONG totals on the card, which is worse than a
    large payload.

    Nothing becomes unrecoverable. Associations are derived per request from
    the mined candidates and the effects sidecar; no store and no export keeps
    them, so restoring per-pattern detail later is a server-side change and a
    re-run, not a migration.
    """
    folded: dict[tuple[str, str], dict] = {}
    for association in associations:
        key = (association["language"], association["normalized_phrase"])
        row = folded.get(key)
        if row is None:
            row = {
                "normalized_phrase": association["normalized_phrase"],
                "language": association["language"],
                # "exact" wins over "contained": the card's meaning is "the
                # runtime has handled this phrase", and an exact hit is the
                # stronger claim.
                "association_type": association["association_type"],
                "effect_pattern_count": 0,
                "detected_count": 0,
                "regen_triggered_count": 0,
                "regen_guard_passed_count": 0,
                "blocked_count": 0,
                "residual_occurrence_count": association[
                    "residual_occurrence_count"
                ],
                "residual_message_count": association["residual_message_count"],
            }
            folded[key] = row
        if association["association_type"] == "exact":
            row["association_type"] = "exact"
        row["effect_pattern_count"] += 1
        for field in (
            "detected_count",
            "regen_triggered_count",
            "regen_guard_passed_count",
            "blocked_count",
        ):
            row[field] += association[field]
    return list(folded.values())


def _associate_repetition_effects(
    candidates: list,
    patterns: list,
) -> list[dict]:
    associations: list[dict] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        language = candidate.get("language")
        candidate_phrase = candidate.get("normalized_phrase")
        if not isinstance(language, str) or not isinstance(candidate_phrase, str):
            continue
        association_language = _repetition_association_language(language)
        for pattern in patterns:
            if not isinstance(pattern, dict):
                continue
            pattern_language = pattern.get("language")
            if (
                not isinstance(pattern_language, str)
                or _repetition_association_language(pattern_language)
                != association_language
            ):
                continue
            effect_phrase = pattern.get("normalized_phrase")
            if not isinstance(effect_phrase, str):
                continue
            association_type = None
            if effect_phrase == candidate_phrase:
                association_type = "exact"
            elif (
                _is_safe_containment_phrase(language, candidate_phrase)
                and (
                    _is_safe_containment_phrase(language, effect_phrase)
                    or _is_runtime_detector_signature(
                        language,
                        effect_phrase,
                        pattern.get("reasons"),
                    )
                )
                and _phrases_contain_each_other(
                    association_language,
                    candidate_phrase,
                    effect_phrase,
                )
            ):
                association_type = "contained"
            if association_type is None:
                continue
            associations.append(
                {
                    "normalized_phrase": candidate_phrase,
                    "language": language,
                    "effect_normalized_phrase": effect_phrase,
                    "association_type": association_type,
                    "detected_count": int(pattern.get("detected_count", 0)),
                    "regen_triggered_count": int(
                        pattern.get("regen_triggered_count", 0)
                    ),
                    "regen_guard_passed_count": int(
                        pattern.get("regen_guard_passed_count", 0)
                    ),
                    "blocked_count": int(pattern.get("blocked_count", 0)),
                    "residual_occurrence_count": int(
                        candidate.get("occurrence_count", 0)
                    ),
                    "residual_message_count": int(candidate.get("message_count", 0)),
                }
            )
    return associations


@router.post('/repetition_insights')
async def repetition_insights(request: RepetitionInsightsRequest):
    """Run an explicit, local-only review of persisted assistant text."""
    # The same cap the INTERNAL analysis route enforces. Without it an
    # over-long name passed here, failed there with 400, and got remapped
    # to "local memory analysis unavailable" -- a 503 that sends the user
    # hunting a memory-server fault that does not exist.
    validation = validate_character_name(
        request.character_name,
        allow_dots=True,
        max_units=PROFILE_NAME_MAX_UNITS,
    )
    if not validation.ok and validation.code != "reserved_route_name":
        return JSONResponse(
            {"success": False, "error": "invalid character name"},
            status_code=422,
        )
    character_name = validation.normalized
    if request.language not in REPETITION_INSIGHT_LANGUAGES:
        return JSONResponse(
            {"success": False, "error": "unsupported analysis language"},
            status_code=422,
        )

    try:
        from config import MEMORY_SERVER_PORT
        from utils.config_manager import get_config_manager
        from utils.internal_http_client import get_internal_http_client

        config_manager = get_config_manager()
        characters = await config_manager.aload_characters()
        configured_characters = (
            characters.get("猫娘", {}) if isinstance(characters, dict) else {}
        )
        # The configured KEY, not the normalized request name: they differ
        # when a hand-edited characters.json carries padding, and reading
        # the normalized form there lands on an unrelated orphan directory.
        configured_key = _configured_character_key(
            configured_characters, character_name
        )
        if configured_key is None and not _mr.character_memory_exists(
            config_manager, character_name
        ):
            return JSONResponse(
                {"success": False, "error": "character not found"},
                status_code=404,
            )
        if configured_key is not None:
            character_name = configured_key
        if _default_memory_dir_escapes_root(config_manager, character_name):
            # Reported as absent rather than as a link: what is on the
            # far end is not this panel's to describe either.
            return JSONResponse(
                {"success": False, "error": "character not found"},
                status_code=404,
            )

        response = await get_internal_http_client().post(
            "http://127.0.0.1:"
            f"{MEMORY_SERVER_PORT}/internal/memory/"
            f"{quote(character_name, safe='')}/repetition_insights",
            json={
                "language": request.language,
                "assistant_message_limit": request.assistant_message_limit,
            },
            timeout=30.0,
        )
        if response.status_code != 200:
            status_code = response.status_code
            if status_code not in {404, 422, 503}:
                status_code = 503
            return JSONResponse(
                {"success": False, "error": "local memory analysis unavailable"},
                status_code=status_code,
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid local memory analysis response")
        response_ids = payload.pop("_anti_repeat_response_ids", None)
        message_scoped = isinstance(response_ids, list)
        # The local budget can narrow the window, so the requested limit is what
        # the user asked for while `analyzed_message_count` is what was actually
        # mined. The effect scope must be labelled with the latter, or the panel
        # says "the latest 100 replies" over an aggregate covering ten.
        payload_summary = payload.get("summary")
        analyzed_limit = request.assistant_message_limit
        if isinstance(payload_summary, dict):
            analyzed = payload_summary.get("analyzed_message_count")
            if isinstance(analyzed, int) and analyzed > 0:
                analyzed_limit = analyzed
        effects = (
            _empty_message_scoped_repetition_effects(analyzed_limit)
            if message_scoped
            else _empty_repetition_effects(request.effect_days)
        )
        try:
            from memory.anti_repeat_effects import get_anti_repeat_effect_store

            effect_store = get_anti_repeat_effect_store()
            if message_scoped:
                queried_effects = await asyncio.to_thread(
                    effect_store.query_effects_for_responses,
                    character_name,
                    response_ids,
                    analyzed_limit,
                )
            else:
                queried_effects = await asyncio.to_thread(
                    effect_store.query_effects,
                    character_name,
                    request.effect_days,
                )
            if isinstance(queried_effects, dict):
                effects = queried_effects
            else:
                effects["query_failed"] = True
        except Exception as exc:
            effects["query_failed"] = True
            logger.warning(
                "Local anti-repeat effects unavailable for %s: %s",
                character_name,
                type(exc).__name__,
            )
        candidates = payload.get("candidates")
        patterns = effects.get("patterns")
        payload["effectiveness"] = effects
        payload["associations"] = _aggregate_repetition_associations(
            _associate_repetition_effects(
                candidates if isinstance(candidates, list) else [],
                patterns if isinstance(patterns, list) else [],
            )
        )
        return payload
    except Exception as exc:
        logger.warning(
            "Local repetition analysis unavailable for %s: %s",
            character_name,
            type(exc).__name__,
        )
        return JSONResponse(
            {"success": False, "error": "local memory analysis unavailable"},
            status_code=503,
        )


@router.post('/repetition_effects/reset')
async def reset_repetition_effects(request: RepetitionEffectsResetRequest):
    """Clear only local anti-repeat aggregates for one existing character."""
    validation = validate_character_name(request.character_name, allow_dots=True)
    if not validation.ok and validation.code != "reserved_route_name":
        return JSONResponse(
            {"success": False, "error": "invalid character name"},
            status_code=422,
        )
    character_name = validation.normalized
    try:
        from memory.anti_repeat_effects import get_anti_repeat_effect_store
        from utils.config_manager import get_config_manager

        config_manager = get_config_manager()
        characters = await config_manager.aload_characters()
        configured_characters = (
            characters.get("猫娘", {}) if isinstance(characters, dict) else {}
        )
        # The configured KEY, not the normalized request name: they differ
        # when a hand-edited characters.json carries padding, and reading
        # the normalized form there lands on an unrelated orphan directory.
        configured_key = _configured_character_key(
            configured_characters, character_name
        )
        if configured_key is None and not _mr.character_memory_exists(
            config_manager, character_name
        ):
            return JSONResponse(
                {"success": False, "error": "character not found"},
                status_code=404,
            )
        if configured_key is not None:
            character_name = configured_key
        await asyncio.to_thread(
            get_anti_repeat_effect_store().clear_effects,
            character_name,
        )
        logger.info("Cleared anti-repeat effects for character=%s", character_name)
        return {
            "success": True,
            "character_name": character_name,
            "cleared": True,
        }
    except Exception as exc:
        logger.warning(
            "Could not clear anti-repeat effects for %s: %s",
            character_name,
            type(exc).__name__,
        )
        return JSONResponse(
            {"success": False, "error": "local anti-repeat effects unavailable"},
            status_code=503,
        )


class HabitualPhraseOverrideRequest(BaseModel):
    character_name: str
    phrase: str = Field(min_length=1, max_length=200)
    action: Literal["allow", "suppress", "clear"]
    ui_language: Optional[str] = Field(default=None, max_length=32)


class HabitualPhraseRecomputeRequest(BaseModel):
    character_name: str
    ui_language: Optional[str] = Field(default=None, max_length=32)


async def _ahabitual_phrase_language(
    config_manager, character_name: str, ui_language: Optional[str] = None,
) -> str:
    """The language the next session start most likely checks the list against.

    The same order the chat client resolves its conversation language in:
    the character's stored language (``prompt_locale.json``, which the
    recompute also mines in), else the client's own UI language, which the
    memory browser passes along; the server's global language only when
    neither is known. It is still a prediction: a session opened from a
    window in another language checks against that one.
    """
    from utils.language_utils import (
        get_global_language_full,
        is_supported_language_code,
        normalize_language_code,
    )

    reader = getattr(config_manager, "_read_durable_prompt_locale", None)
    if callable(reader):
        try:
            selected = await asyncio.to_thread(reader, character_name)
        except OSError:
            selected = None
        if isinstance(selected, str) and selected:
            return selected
    if is_supported_language_code(ui_language):
        return normalize_language_code(str(ui_language), format="full")
    return get_global_language_full()


async def _habitual_phrase_panel(
    config_manager, character_name: str, ui_language: Optional[str] = None,
) -> dict:
    """What the memory browser shows for one character's avoid list."""
    from config import (
        HABITUAL_PHRASE_CANDIDATE_POOL,
        HABITUAL_PHRASE_MAX_AGE_SECONDS,
        HABITUAL_PHRASE_MAX_ITEMS,
        HABITUAL_PHRASE_MIN_SESSIONS,
        HABITUAL_PHRASE_PROMPT_MAX_TOKENS,
        HABITUAL_PHRASE_REFRESH_INTERVAL_SECONDS,
        HABITUAL_PHRASE_SESSION_GAP_SECONDS,
    )
    from memory.habitual_phrases import get_habitual_phrase_store
    from utils.slop_filter import is_slop_filter_enabled

    described = await get_habitual_phrase_store().adescribe(
        character_name,
        await _ahabitual_phrase_language(config_manager, character_name, ui_language),
    )
    try:
        async with _mr._memory_toggle_write_lock:
            review_enabled = await asyncio.to_thread(
                _mr._load_memory_toggle_enabled, config_manager, 'powerful_memory_enabled'
            )
    except Exception:
        review_enabled = True
    computed_at = described.get("computed_at")
    return {
        "success": True,
        "character_name": character_name,
        "switches": {
            "natural_expression": bool(await asyncio.to_thread(is_slop_filter_enabled)),
            "review": bool(review_enabled),
        },
        "rules": {
            "min_sessions": HABITUAL_PHRASE_MIN_SESSIONS,
            "session_gap_minutes": round(HABITUAL_PHRASE_SESSION_GAP_SECONDS / 60),
            "max_items": HABITUAL_PHRASE_MAX_ITEMS,
            "candidate_pool": HABITUAL_PHRASE_CANDIDATE_POOL,
            "prompt_max_tokens": HABITUAL_PHRASE_PROMPT_MAX_TOKENS,
            "refresh_interval_hours": round(HABITUAL_PHRASE_REFRESH_INTERVAL_SECONDS / 3600),
            "max_age_days": round(HABITUAL_PHRASE_MAX_AGE_SECONDS / 86400),
        },
        "next_refresh_at": (
            computed_at + HABITUAL_PHRASE_REFRESH_INTERVAL_SECONDS
            if isinstance(computed_at, (int, float))
            else None
        ),
        **described,
    }


@router.get('/habitual_phrases')
async def get_habitual_phrases(character_name: str, ui_language: Optional[str] = None):
    """The avoid list as the next session start will use it, with the reasons."""
    try:
        resolved, error = await _mr._resolve_memory_character(character_name)
        if error is not None:
            return error
        config_manager, name = resolved
        return await _habitual_phrase_panel(config_manager, name, ui_language)
    except Exception as exc:
        logger.warning("Habitual phrase panel unavailable: %s", type(exc).__name__)
        return JSONResponse(
            {"success": False, "error": "habitual phrases unavailable"}, status_code=503,
        )


@router.post('/habitual_phrases/override')
async def override_habitual_phrase(request: HabitualPhraseOverrideRequest):
    """Allow a phrase, suppress one the review kept, or clear the user's decision."""
    try:
        resolved, error = await _mr._resolve_memory_character(request.character_name)
        if error is not None:
            return error
        config_manager, name = resolved
        from memory.habitual_phrases import get_habitual_phrase_store

        try:
            await get_habitual_phrase_store().aupdate_override(
                name, request.phrase, request.action,
            )
        except ValueError:
            return JSONResponse(
                {"success": False, "error": "invalid phrase"}, status_code=422,
            )
        logger.info("Habitual phrase override saved for %s: %s", name, request.action)
        return await _habitual_phrase_panel(config_manager, name, request.ui_language)
    except MaintenanceModeError:
        return JSONResponse(
            {"success": False, "error": "memory is being restored; try again shortly"},
            status_code=503,
        )
    except Exception as exc:
        logger.warning("Habitual phrase override failed: %s", type(exc).__name__)
        return JSONResponse(
            {"success": False, "error": "habitual phrases unavailable"}, status_code=503,
        )


@router.post('/habitual_phrases/recompute')
async def recompute_habitual_phrases(request: HabitualPhraseRecomputeRequest):
    """Recompute one character's avoid list now, then return the refreshed panel."""
    try:
        resolved, error = await _mr._resolve_memory_character(request.character_name)
        if error is not None:
            return error
        config_manager, name = resolved
        from config import MEMORY_SERVER_PORT
        from utils.internal_http_client import get_internal_http_client

        # The review may take the model's whole hard timeout (110 s).
        response = await get_internal_http_client().post(
            f"http://127.0.0.1:{MEMORY_SERVER_PORT}/internal/memory/"
            f"{quote(name, safe='')}/habitual_phrases/recompute",
            timeout=130.0,
        )
        if response.status_code == 409:
            return JSONResponse(
                {"success": False, "error": "already recomputing"}, status_code=409,
            )
        if response.status_code != 200:
            return JSONResponse(
                {"success": False, "error": "recompute unavailable"},
                status_code=503 if response.status_code not in {404, 422} else response.status_code,
            )
        return await _habitual_phrase_panel(config_manager, name, request.ui_language)
    except Exception as exc:
        logger.warning("Habitual phrase recompute failed: %s", type(exc).__name__)
        return JSONResponse(
            {"success": False, "error": "recompute unavailable"}, status_code=503,
        )


@router.get('/insight_characters')
async def get_insight_characters():
    """List the identities the repetition-insights route will accept.

    The panel selector used to be built from the recent-memory file list,
    which only knows characters that have a ``recent.json``. A configured
    character, or one restored from a cloud snapshot carrying time-indexed
    history without the optional recent file, was therefore missing from the
    panel even though the analysis route supports it.

    Reuses that route's own admission rule -- configured OR has character
    memory on disk -- so the two cannot drift into offering a name the route
    rejects, or hiding one it accepts.
    """
    from utils.config_manager import get_config_manager

    config_manager = get_config_manager()
    characters = await config_manager.aload_characters()
    configured = (
        characters.get("猫娘", {}) if isinstance(characters, dict) else {}
    )
    names = {
        selectable
        for name in configured
        if isinstance(name, str) and name
        for selectable in (_insight_selectable_name(name),)
        if selectable
    }

    # Raw, not selectable: this is compared against directory names on disk,
    # which carry the character name as configured.
    configured_names = {
        name for name in configured if isinstance(name, str) and name
    }

    # Enumerate through the same roots the predicate reads, so a root added
    # there cannot silently become invisible here.
    candidates: set[str] = set()
    for base_dir in iter_character_memory_roots(config_manager):
        if not base_dir.exists():
            continue
        for child in base_dir.iterdir():
            # A REAL directory. ``Path.is_dir()`` follows links, so a
            # symlink in the memory root was offered as a character and
            # ``character_memory_exists`` confirmed it -- the panel would
            # then read, render and export assistant-shaped rows from
            # whatever database the link points at, outside the memory root
            # entirely. Measured with a link to a sibling directory.
            #
            # Fixed HERE rather than in the shared reader on purpose:
            # ``_resolve_expected_db_path`` honours ``time_store``, which
            # exists so a character CAN register a database outside
            # memory_dir, and a blanket containment check there would break
            # that. What is new on this branch is enumerating the root and
            # offering what it finds, so that is what learns to be careful.
            #
            # And a REAL character directory, not merely a namesake. A legacy
            # "semantic_memory_Alice/" vector store is one of the paths
            # ``character_memory_exists`` checks for a character of that
            # name, so it confirms itself: the selector offered
            # "semantic_memory_Alice", and analysing it read
            # memory/semantic_memory_Alice/time_indexed.db rather than
            # Alice's, reporting no history for a character that has plenty.
            #
            # Configured characters keep their own path below and are not
            # subject to this -- an empty configured character is still hers.
            #
            # And not another character's vector store. A legacy
            # "semantic_memory_Alice/" is one of the paths
            # ``character_memory_exists`` checks for a character of that
            # name, so it confirms itself: the selector offered
            # "semantic_memory_Alice", and analysing it read that vector
            # store rather than Alice's history. Only the ENUMERATING side
            # can tell the difference, because only it can see the owner.
            if (
                child.is_dir()
                and not child.is_symlink()
                and not is_legacy_vector_store_dir(base_dir, child.name)
            ):
                candidates.add(child.name)
            # No legacy decoding here. The flat layout was retired in
            # 2026-03 together with the startup migration that replaces it,
            # and that migration now covers unconfigured owners as well --
            # so by the time this runs, a legacy root file has already
            # become memory/<name>/ and the directory branch above sees it.
            # Decoding here made the READ path carry a second layout, and
            # got it wrong: "time_indexed_Carol.db-wal" decoded to a
            # character named "Carol.db-wal", which the existence check then
            # confirmed, so the panel offered it.
        for logical_name in iter_recent_memory_files(base_dir):
            candidate = extract_catgirl_name_from_recent_filename(logical_name)
            if candidate:
                candidates.add(candidate)

    for candidate in candidates - names:
        selectable = _insight_selectable_name(candidate)
        if selectable and _mr.character_memory_exists(config_manager, selectable):
            names.add(selectable)

    return {"characters": sorted(names)}
