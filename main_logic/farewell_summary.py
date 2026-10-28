"""Read-only memory summary for the desktop exit farewell card.

Served by ``GET /api/memory/farewell-summary``. The desktop main process calls
it with a 1.2 s timeout while the tray quit is in progress, so everything here
is local and read-only: no LLM, no sync, no file writes, no remote facts URL.
Missing sources drop their field instead of failing the whole response.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from main_logic import card_forge_facts as forge

logger = logging.getLogger("neko.farewell_summary")

# Memories newer than this are not "old" enough for a flashback; the desktop
# drops them too, so skipping them here keeps the pick useful.
FLASHBACK_MIN_AGE_DAYS = 7
# Prefer memories at least this important; fall back to any when none qualify.
FLASHBACK_PREFERRED_IMPORTANCE = 5
# The desktop shows at most 60 characters; keep the payload small anyway.
FLASHBACK_TEXT_MAX_CHARS = 200


def _local_memory_date(item: dict[str, Any]) -> date | None:
    """Local calendar date a memory happened or was recorded on.

    Facts store naive local ISO timestamps (``datetime.now().isoformat()``);
    aware values are converted to local time. ``card_forge_facts`` reads naive
    values as UTC for ordering, which would shift the displayed day here.
    """
    for field in ("event_start_at", "created_at"):
        value = item.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                return datetime.fromtimestamp(float(value)).date()
            except (OSError, OverflowError, ValueError):
                continue
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.date()
    return None


def _flashback_candidate(item: Any) -> bool:
    """Same shareability rules as the community card pool (``/api/card-drop/facts``)."""
    if not isinstance(item, dict):
        return False
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        return False
    if item.get("private") is True or item.get("redacted") is True:
        return False
    if item.get("origin") in forge._UNSHAREABLE_ORIGINS:
        return False
    if forge._looks_like_machine_log(item):
        return False
    return True


def _archive_row_eligible(item: dict[str, Any]) -> bool:
    return not (
        item.get("subject_archived_at")
        or item.get("arbitration_archived_at")
        or item.get("arbitration_reason")
    )


def select_flashback(
    raw: list[Any],
    raw_archive: list[Any],
    *,
    today: date | None = None,
    rng: random.Random | None = None,
) -> dict[str, str] | None:
    """Pick one shareable, old, preferably important memory, or ``None``.

    Active rows win identity overlaps with archive rows. Candidates are sorted
    oldest first and one is drawn from the oldest quarter, weighted by
    importance, so repeated calls can return different memories.
    """
    today = today or date.today()
    rng = rng or random
    # Every active row shadows its archived copies, whatever its own state:
    # a private or redacted active row must keep its archived copy out.
    active_ids: set[forge._FactKey] = set()
    active_hashes: set[str] = set()
    # Only accepted candidates claim an identity among themselves, so an
    # ineligible duplicate (e.g. a machine-log copy) never hides a valid one.
    seen_ids: set[forge._FactKey] = set()
    seen_hashes: set[str] = set()
    candidates: list[tuple[date, dict[str, Any], forge._FactKey]] = []
    for collection, is_archive in ((raw, False), (raw_archive, True)):
        for item in collection:
            # Visit facts are about the neighbour's home: like the card-drop
            # pool, they must not hide an identical ordinary archived fact.
            if not isinstance(item, dict) or item.get("origin") in forge._UNSHAREABLE_ORIGINS:
                continue
            if "text" in item and not isinstance(item["text"], str):
                continue
            fact_key, _, hash_aliases = forge._fact_identity(item)
            if fact_key is None:
                continue
            if not is_archive:
                active_ids.add(fact_key)
                active_hashes.update(hash_aliases)
            elif fact_key in active_ids or hash_aliases & active_hashes:
                continue
            if fact_key in seen_ids or hash_aliases & seen_hashes:
                continue
            if not _flashback_candidate(item):
                continue
            if is_archive and not _archive_row_eligible(item):
                continue
            memory_date = _local_memory_date(item)
            if memory_date is None or (today - memory_date).days < FLASHBACK_MIN_AGE_DAYS:
                continue
            seen_ids.add(fact_key)
            seen_hashes.update(hash_aliases)
            candidates.append((memory_date, item, fact_key))

    if not candidates:
        return None
    important = [
        entry for entry in candidates
        if forge._safe_importance(entry[1].get("importance")) >= FLASHBACK_PREFERRED_IMPORTANCE
    ]
    pool = important or candidates
    pool.sort(key=lambda entry: (entry[0], -forge._importance_weight(entry[1])))
    oldest = pool[: max(1, len(pool) // 4)]
    weights = [forge._importance_weight(entry[1]) for entry in oldest]
    memory_date, item, fact_key = rng.choices(oldest, weights=weights, k=1)[0]
    text = " ".join(item["text"].split())
    if len(text) > FLASHBACK_TEXT_MAX_CHARS:
        text = text[: FLASHBACK_TEXT_MAX_CHARS - 1] + "…"
    return {
        # Plain string IDs pass through; other identities become an opaque
        # stable ``[A-Za-z0-9_:-]`` token, which is what the desktop keeps.
        "id": forge._fact_wire_id(fact_key),
        "text": text,
        "date": memory_date.isoformat(),
    }


def _time_index_db_path(config_manager: Any, lanlan_name: str, memory_dir: Path | None) -> Path | None:
    """Mirror ``TimeIndexedMemory._resolve_expected_db_path`` without creating directories."""
    try:
        character_data = config_manager.get_character_data()
        time_store = character_data[6] if len(character_data) > 6 else {}
    except Exception:
        time_store = {}
    # Key presence, like the writer: a registered empty path is not readable.
    if isinstance(time_store, dict) and lanlan_name in time_store:
        value = time_store[lanlan_name]
        return Path(str(value)) if value else None
    # The writer uses config_manager.memory_dir, not the NEKO_MEMORY_DIR
    # override that card_forge_facts honours for facts.
    configured = getattr(config_manager, "memory_dir", None)
    base = Path(str(configured)) if configured else memory_dir
    if base is None:
        return None
    return base / lanlan_name / "time_indexed.db"


def count_user_turns(db_path: Path) -> int | None:
    """Number of user messages in the time index (one per conversation turn).

    Opened read-only through a SQLite URI so a missing or locked database is
    never created or blocked on. Returns ``None`` when it cannot be counted.
    """
    if not db_path.is_file():
        return None
    from config import TIME_ORIGINAL_TABLE_NAME

    # as_uri() percent-encodes "#", "?" and "%" so they stay part of the filename.
    uri = Path(os.path.abspath(db_path)).as_uri()
    try:
        conn = sqlite3.connect(f"{uri}?mode=ro", uri=True, timeout=0.3)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {TIME_ORIGINAL_TABLE_NAME} WHERE "
            # CASE, not AND: SQLite does not promise short-circuit evaluation,
            # and json_extract raises on a malformed legacy row.
            "CASE WHEN json_valid(message) THEN json_extract(message, '$.type') END = 'human'"
        ).fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error as exc:
        logger.debug("farewell-summary: time index count failed: %s", type(exc).__name__)
        return None
    finally:
        conn.close()


async def build_farewell_summary() -> dict[str, Any]:
    """Summary of the active character's memories.

    Without an active character only ``flashback: null`` is returned: the
    desktop omits counts that are missing, which reads better than zeros.
    """
    from utils.config_manager import get_config_manager

    context = await forge.resolve_active_neko_context()
    if not context.lanlan_name or context.facts_path is None:
        return {"flashback": None}

    facts_path = context.facts_path
    raw, raw_archive = await asyncio.gather(
        asyncio.to_thread(forge._load_facts_json, facts_path),
        asyncio.to_thread(forge._load_facts_json, facts_path.with_name("facts_archive.json")),
    )
    memory_count, _, _ = await asyncio.to_thread(forge._memory_identity_stats, raw, raw_archive)
    flashback = await asyncio.to_thread(select_flashback, raw, raw_archive)

    payload: dict[str, Any] = {"memory_count": memory_count}
    # get_character_data() can stat or reload the config file: keep it off the event loop.
    db_path = await asyncio.to_thread(
        _time_index_db_path, get_config_manager(), context.lanlan_name, context.memory_dir,
    )
    conversation_count = await asyncio.to_thread(count_user_turns, db_path) if db_path else None
    if conversation_count is not None:
        payload["conversation_count"] = conversation_count
    payload["flashback"] = flashback
    return payload
