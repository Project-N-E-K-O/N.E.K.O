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
"""Visit fact write routes and theater memory story/forget/retract routes."""

import hashlib
import json
from fastapi import HTTPException
from pydantic import BaseModel, Field
from memory.message_sources import is_theater_memory_message, theater_memory_episode_key
from utils.llm_client import message_metadata
from memory.recent import _positive_metadata_int, is_retracted_theater_episode, restored_theater_history
from .runtime import app

from . import routes as _routes

class TheaterMemoryForgetRequest(BaseModel):
    story_id: str = Field(min_length=1, max_length=256)


class TheaterEpisodeRetractRequest(BaseModel):
    story_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    archive_through_revision: int = Field(ge=0)
    # Identify the archive attempts to fence against late /cache writes.
    archive_request_id: str = Field(default="", max_length=160)
    archive_attempt: int = Field(default=0, ge=0)


def _theater_story_event_id(lanlan_name: str, message) -> str:
    """Always use the same bounded event ID for a story in the time index."""  # noqa: DOCSTRING_CJK

    story_id, _ = theater_memory_episode_key(message)
    digest = hashlib.sha256(
        f"{lanlan_name}\x1f{story_id}".encode("utf-8")
    ).hexdigest()
    return f"theater-story-{digest}"


def _theater_index_events(lanlan_name: str, messages: list) -> dict[str, tuple[str, list]]:
    """Build story-aggregated time-index events from all theater memories in recent."""  # noqa: DOCSTRING_CJK

    grouped: dict[str, list] = {}
    for message in messages:
        # When forgetting one scenario, other unmigrated legacy bodies remain in recent; rebuilding must preserve them.
        if not is_theater_memory_message(message):
            continue
        story_id, _ = theater_memory_episode_key(message)
        if story_id:
            grouped.setdefault(story_id, []).append(message)
    return {
        story_id: (
            _theater_story_event_id(lanlan_name, story_messages[-1]),
            story_messages,
        )
        for story_id, story_messages in grouped.items()
    }


def _theater_memory_render_state(history: list):
    """Select the latest state per Session and latest playthrough per Story."""  # noqa: DOCSTRING_CJK

    latest_by_episode = {
        theater_memory_episode_key(message): message_metadata(message)
        for message in history
        if is_theater_memory_message(message)
    }
    latest_episode_by_story: dict[str, tuple[str, str]] = {}
    latest_rank_by_story: dict[str, tuple[int, int]] = {}
    for position, (episode_key, metadata) in enumerate(latest_by_episode.items()):
        story_id = episode_key[0]
        rank = (_positive_metadata_int(metadata.get("run_index")), position)
        if rank >= latest_rank_by_story.get(story_id, (-1, -1)):
            latest_rank_by_story[story_id] = rank
            latest_episode_by_story[story_id] = episode_key
    return latest_by_episode, latest_episode_by_story


def _iter_theater_rendered_history_unbounded(history: list, *, lang: str, name: str, master: str):
    """Yield ``(message, capsule_text)`` for the prompt renderings of recent history.

    Each theater Session renders once, at its first position, from its latest
    metadata; later messages of the same Session are skipped. Only the latest
    run of a Story carries the story-wide run count and endings seen. Ordinary
    messages come back with ``capsule_text=None`` for the caller to render.
    """
    latest_by_episode, latest_episode_by_story = _theater_memory_render_state(history)
    rendered_episodes: set[tuple[str, str]] = set()
    for message in history:
        if not is_theater_memory_message(message):
            yield message, None
            continue
        episode_key = theater_memory_episode_key(message)
        if episode_key in rendered_episodes:
            continue
        rendered_episodes.add(episode_key)
        metadata = latest_by_episode[episode_key]
        is_latest_story_run = latest_episode_by_story.get(episode_key[0]) == episode_key
        ending_titles = metadata.get("ending_titles_seen")
        yield message, _routes.get_theater_memory_context(
            lang,
            name=name,
            master=master,
            title=str(metadata.get("story_title") or ""),
            status=str(metadata.get("episode_status") or "paused"),
            ending=str(metadata.get("ending_title") or ""),
            summary=str(
                metadata.get("episode_summary")
                or metadata.get("ending_summary")
                or ""
            ),
            run_index=_positive_metadata_int(metadata.get("run_index")),
            story_run_count=(
                _positive_metadata_int(metadata.get("story_run_count"))
                if is_latest_story_run
                else 0
            ),
            ending_titles=(
                ending_titles
                if is_latest_story_run and isinstance(ending_titles, list)
                else []
            ),
        )


def _iter_theater_rendered_history(history: list, *, lang: str, name: str, master: str):
    """Apply a separate theater prompt allowance without dropping ordinary text."""
    from memory.theater_budget import THEATER_MEMORY_BUDGET_TOKENS
    from utils.tokenize import count_tokens

    rendered = list(_iter_theater_rendered_history_unbounded(
        history, lang=lang, name=name, master=master,
    ))
    selected = set()
    texts = []
    for index in range(len(rendered) - 1, -1, -1):
        _, capsule_text = rendered[index]
        if capsule_text is None:
            selected.add(index)
            continue
        candidate = [capsule_text, *texts]
        if count_tokens("\n".join(candidate) + "\n") <= THEATER_MEMORY_BUDGET_TOKENS:
            selected.add(index)
            texts = candidate
    for index, entry in enumerate(rendered):
        if index in selected:
            yield entry


_VISIT_FACT_IMPORTANCE = 4


class VisitFactItem(BaseModel):
    text: str = Field(min_length=1, max_length=60)


class VisitFactsRequest(BaseModel):
    visit_id: str = Field(pattern=r"^[A-Za-z0-9_-]{22}$")
    facts: list[VisitFactItem] = Field(default_factory=list, max_length=3)


@app.post("/internal/memory/{lanlan_name}/visit_facts")
async def write_visit_facts(lanlan_name: str, req: VisitFactsRequest):
    """Write the (at most three) facts of one visit's diary into the private fact pool.

    Idempotent per ``visit_id`` (key ``visit-facts:{visit_id}``): a ``done``
    key answers the ``written`` count of the first write. The server stamps
    every new fact ``source='ai_disclosure'``, ``importance=4``,
    ``absorbed=True``, ``origin='neko_visit'`` and ``visit_id`` (callers
    cannot override them), so reflection never synthesizes them while recall
    still finds them; only exact dedup applies (a semantic near-duplicate
    would go to arbitration, whose ``replace`` could archive the user's own
    fact in favour of a visit fact). A key left
    ``pending`` (crash after the facts were saved) is settled by counting the
    visit's facts in the active and archived pools, read strictly: either
    file unreadable answers 503 and writes nothing; the facts still missing
    are then written (rows already there are skipped by their effect key)
    and ``written`` counts both. An empty list is a no-op
    that still records the key.
    """
    from . import idempotency

    lanlan_name = _routes.validate_lanlan_name(lanlan_name)
    if _routes.runtime.fact_store is None:
        raise HTTPException(
            status_code=503,
            detail="memory_server not fully initialized (limited mode or startup incomplete)",
        )
    texts = [item.text.strip() for item in req.facts]
    if any(not text for text in texts):
        raise HTTPException(status_code=422, detail="each fact text must contain 1..60 characters")
    key = f"visit-facts:{req.visit_id}"
    facts_hash = hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode("utf-8")).hexdigest()
    unavailable = HTTPException(status_code=503, detail="visit facts not confirmed; retry later")
    async with idempotency.key_lock(lanlan_name, key):
        try:
            record = await idempotency.read_key(lanlan_name, key)
        except (idempotency.IdempotencyStateError, OSError) as exc:
            raise unavailable from exc
        if record is not None and record.get("kind") != "visit_facts":
            raise HTTPException(status_code=422, detail="idempotency key belongs to another endpoint")
        stored_hash = record.get("content_hash") if record is not None else None
        if stored_hash is not None and stored_hash != facts_hash:
            # One fact set per visit (client retries same persisted set): changed content is caller error, never report it written.
            raise HTTPException(status_code=422, detail="visit_id reused with different facts")
        def _counter(value: object) -> int | None:
            # Missing fields are malformed too: our terminal records always have both counts; without them never trust prior-written claims.
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            return value

        written_n = _counter(record.get("written")) if record is not None else None
        deduped_n = _counter(record.get("deduped")) if record is not None else None
        if (record is not None and record.get("state") in idempotency.TERMINAL_KEY_STATES
                and isinstance(stored_hash, str) and stored_hash
                and written_n is not None and deduped_n is not None
                # Our terminal records always satisfy written + deduped == batch fact count; mismatch is malformed too.
                and written_n + deduped_n == len(texts)):
            return {"ok": True, "written": written_n, "deduped": deduped_n, "duplicate": True}
        # Missing content hash or malformed counts: infer from this visit's fact-pool rows as for absent records, filling only gaps.
        try:
            rows = await _routes.runtime.fact_store.aload_facts_strict(
                lanlan_name, include_archive=True, include_cold_stubs=True,
            )
        except RuntimeError as exc:
            # Never guess on read failure: permissive active-pool loading caches an empty list, whose writeback would erase existing facts.
            _routes.logger.warning(f"[MemoryServer] visit_facts {lanlan_name}: fact pool unreadable: {exc}")
            raise unavailable from exc
        mine = [row for row in rows if row.get("origin") == "neko_visit" and row.get("visit_id") == req.visit_id]
        already = len(mine)
        known_effects = {row.get("effect_key") for row in mine}
        # Compared by wording hash: a cold row is only a stub, without its text.
        from memory.cold_store import text_sha256
        expected = {idempotency.effect_key_for(key, i): text_sha256(text) for i, text in enumerate(texts)}
        if any(expected.get(row.get("effect_key"))
               != (row["text_sha256"] if "text_sha256" in row else text_sha256(row.get("text")))
               for row in mine):
            # Reach fact-pool recovery only for missing/malformed key records: if this visit's rows differ from current request text,
            # current content cannot be reported written (same handling as content_hash mismatch).
            raise HTTPException(status_code=422, detail="visit_id reused with different facts")
        try:
            # Some rows persisted before interruption preceding done (or later deletion/restore removed rows): replay with effect_key,
            # skip existing rows by effect_key and fill only gaps; written reflects actual new row count.
            await idempotency.update_key(
                lanlan_name, key,
                idempotency.transition(idempotency.KEY_STATE_PENDING, kind="visit_facts", content_hash=facts_hash),
            )
            created: list[dict] = []
            if texts:
                extracted = [
                    {
                        "text": text,
                        "importance": _VISIT_FACT_IMPORTANCE,
                        "source": "ai_disclosure",
                        "entity": "neko",
                    }
                    for text in texts
                ]
                created = await _routes.runtime.fact_store._apersist_new_facts(
                    lanlan_name,
                    extracted,
                    default_source="ai_disclosure",
                    # Exact deduplication only: semantic near-duplicates trigger arbitration, whose replace could archive the user's own facts
                    # and substitute a neighbor's visit facts.
                    semantic_dedup=False,
                    effect_keys=[idempotency.effect_key_for(key, i) for i in range(len(extracted))],
                    visit_origin={"visit_id": req.visit_id},
                )
            # Returned rows include existing effect_key replays too; count only truly new writes.
            written = already + sum(1 for row in created if row.get("effect_key") not in known_effects)
            deduped = max(len(texts) - written, 0)
            await idempotency.update_key(
                lanlan_name, key,
                _routes._done_repairing(written=written, deduped=deduped, kind="visit_facts", content_hash=facts_hash),
            )
        except Exception as exc:  # noqa: BLE001 - Unconfirmed: keep pending; 503 tells caller to retry with the same visit_id.
            _routes.logger.warning(f"[MemoryServer] visit_facts {lanlan_name}: not confirmed: {exc!r}")
            raise unavailable from exc
    body = {"ok": True, "written": written, "deduped": deduped}
    if already:
        body["duplicate"] = True
    return body


@app.get("/internal/memory/{lanlan_name}/theater/stories")
async def list_theater_memory_stories(lanlan_name: str):
    """Expose saved public summaries for management after a package is deleted."""
    lanlan_name = _routes.validate_lanlan_name(lanlan_name)
    characters = await _routes.runtime._config_manager.aload_characters()
    if lanlan_name not in characters.get("猫娘", {}):
        raise HTTPException(status_code=404, detail="character_not_found")
    history = await _routes.runtime.recent_history_manager.aget_recent_history(lanlan_name)
    latest, _ = _theater_memory_render_state(history)
    stories = {}
    for (story_id, _), metadata in latest.items():
        if not story_id:
            continue
        story = stories.setdefault(story_id, {"story_id": story_id, "title": "", "memory_summaries": []})
        story["title"] = str(metadata.get("story_title") or story["title"] or story_id)
        summary = str(metadata.get("episode_summary") or metadata.get("ending_summary") or "").strip()
        if summary:
            story["memory_summaries"].append(summary)
    return {"ok": True, "stories": list(stories.values())}


async def _drop_theater_memory_reindexed(
    lanlan_name: str,
    current: list,
    should_drop,
    drop_recent,
    operation: str,
    remaining=None,
):
    """Remove theater capsules from the time index, then from recent.

    The caller holds the character's settle lock and passes the recent history
    it read under it. The recallable index goes first, so a failed recent write
    still leaves the original summary in place. When ``drop_recent`` fails the
    index is rebuilt from what recent actually holds: the drop may have been
    partly persisted, and rolling back to ``current`` would put removed
    capsules back into the recallable index.
    """

    if remaining is None:
        remaining = [message for message in current if not should_drop(message)]
    reconcile_result = await _routes.runtime.time_manager.areconcile_theater_conversations(
        _theater_index_events(lanlan_name, remaining),
        lanlan_name,
    )
    try:
        removed_recent = await drop_recent()
    except Exception:
        try:
            try:
                actual = await _routes.runtime.recent_history_manager.aget_recent_history(
                    lanlan_name,
                )
            except Exception:
                _routes.logger.exception(
                    "[MemoryServer] %s: recent re-read failed; restoring index from snapshot",
                    operation,
                )
                actual = current
            await _routes.runtime.time_manager.areconcile_theater_conversations(
                _theater_index_events(lanlan_name, actual),
                lanlan_name,
            )
        except Exception:
            _routes.logger.exception("[MemoryServer] %s: time index rollback failed", operation)
        raise
    return removed_recent, reconcile_result


@app.post("/internal/memory/{lanlan_name}/theater/forget")
async def forget_theater_memory(
    lanlan_name: str,
    request: TheaterMemoryForgetRequest,
):
    """Idempotently delete the specified story's hot memory and time index."""  # noqa: DOCSTRING_CJK

    lanlan_name = _routes.validate_lanlan_name(lanlan_name)
    story_id = request.story_id.strip()
    if not story_id:
        raise HTTPException(status_code=422, detail="story_id_required")
    try:
        async with _routes.runtime._get_settle_lock(lanlan_name):
            # An archive request the theater timed out on may still land after
            # this forget. Record the story tombstone first, under the settle lock
            # /cache checks it under, so such a late write is dropped even when
            # the rest of this forget fails and is retried.
            # Every forget issues a fresh marker; the theater attaches it only to
            # archive requests issued after it recorded this forget, so no write
            # sent before the forget can carry it (no clock is compared).
            forget_marker = await _routes.runtime.recent_history_manager.record_theater_story_forget(
                lanlan_name,
                story_id,
            )
            current = await _routes.runtime.recent_history_manager.aget_recent_history(
                lanlan_name,
            )
            removed_recent, reconcile_result = await _drop_theater_memory_reindexed(
                lanlan_name,
                current,
                lambda message: (
                    is_theater_memory_message(message)
                    and str(message_metadata(message).get("story_id") or "") == story_id
                ),
                lambda: _routes.runtime.recent_history_manager.forget_theater_story(
                    story_id,
                    lanlan_name,
                ),
                "theater story forget",
            )
        return {
            "ok": True,
            "removed_recent": removed_recent,
            "removed_time_index": int(reconcile_result.get("removed") or 0),
            "forget_marker": forget_marker,
        }
    except Exception as exc:
        _routes.logger.error(
            "[MemoryServer] 删除 %s 的剧本 %s 记忆失败: %s",
            lanlan_name,
            story_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="theater_memory_forget_failed",
        ) from exc


@app.post("/internal/memory/{lanlan_name}/theater/retract")
async def retract_theater_episode(
    lanlan_name: str,
    request: TheaterEpisodeRetractRequest,
):
    """Idempotently remove the episode capsule of one declined theater archive.

    The theater may time out while this server still commits the archive; when
    the player then declines the archive, the theater calls this to take back
    exactly that range's capsule (matched by story, session and through-revision).
    With ``archive_request_id`` it also leaves a persistent tombstone so a /cache
    write of any attempt up to ``archive_attempt`` that is still in flight is
    dropped instead of resurrecting the declined summary.
    """

    lanlan_name = _routes.validate_lanlan_name(lanlan_name)
    story_id = request.story_id.strip()
    session_id = request.session_id.strip()
    through = int(request.archive_through_revision)
    if not story_id or not session_id:
        raise HTTPException(status_code=422, detail="theater_episode_identity_required")

    def is_target(message) -> bool:
        return is_retracted_theater_episode(message, story_id, session_id, through)

    archive_request_id = request.archive_request_id.strip()
    try:
        async with _routes.runtime._get_settle_lock(lanlan_name):
            if archive_request_id:
                # The archive request may still be in flight (the theater only timed
                # out). Record the tombstone first, under the settle lock /cache
                # checks it under, so a late write of these attempts is dropped even
                # when there is nothing to remove yet.
                await _routes.runtime.recent_history_manager.record_theater_retraction(
                    lanlan_name,
                    story_id=story_id,
                    session_id=session_id,
                    archive_through_revision=through,
                    archive_request_id=archive_request_id,
                    archive_attempt=request.archive_attempt,
                )
            current = await _routes.runtime.recent_history_manager.aget_recent_history(
                lanlan_name,
            )
            if not any(is_target(message) for message in current):
                return {"ok": True, "removed_recent": 0, "removed_time_index": 0}
            removed_recent, reconcile_result = await _drop_theater_memory_reindexed(
                lanlan_name,
                current,
                is_target,
                lambda: _routes.runtime.recent_history_manager.retract_theater_episode(
                    story_id,
                    session_id,
                    through,
                    lanlan_name,
                ),
                "theater episode retract",
                remaining=restored_theater_history(current, story_id, session_id, through),
            )
        return {
            "ok": True,
            "removed_recent": removed_recent,
            "removed_time_index": int(reconcile_result.get("removed") or 0),
        }
    except Exception as exc:
        _routes.logger.error(
            "[MemoryServer] retracting theater episode %s/%s for %s failed: %s",
            story_id,
            session_id,
            lanlan_name,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="theater_memory_retract_failed",
        ) from exc
