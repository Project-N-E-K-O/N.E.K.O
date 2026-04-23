# -*- coding: utf-8 -*-
"""
Unit tests for memory archive (PR-2 / RFC §3.5).

Coverage:
- Decay math snapshots (effective_reinforcement / effective_disputation
  parametrized over age × value × half-life)
- Sub-zero accumulation: never resets when score recovers (§3.5.3
  "归档更积极")
- protected=True exemption: never accumulates / never archives
- Sharded append: > ARCHIVE_FILE_MAX_ENTRIES rolls a new shard
- Legacy flat reflections_archive.json migration: per-day bucketing,
  uuid8 suffix uniqueness, sentinel created, flat file deleted
- aarchive_reflection: emits EVT_REFLECTION_STATE_CHANGED to=archived;
  10 replays of the event leave the view consistent
- aarchive_persona_entry: emits EVT_PERSONA_FACT_ADDED with
  archive_shard_path; replay-stable
- Sweep-loop sub_zero increment: drives a reflection's score below 0
  for EVIDENCE_ARCHIVE_DAYS simulated days → archive happens
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import (
    ARCHIVE_FILE_MAX_ENTRIES,
    EVIDENCE_ARCHIVE_DAYS,
    EVIDENCE_DISP_HALF_LIFE_DAYS,
    EVIDENCE_REIN_HALF_LIFE_DAYS,
)


# ── shared fixtures (mirroring tests/unit/test_evidence_apply_signal.py) ──


def _mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    return cm


def _install(tmpdir: str):
    from memory.event_log import EventLog
    from memory.evidence_handlers import register_evidence_handlers
    from memory.facts import FactStore
    from memory.event_log import Reconciler
    from memory.persona import PersonaManager
    from memory.reflection import ReflectionEngine

    cm = _mock_cm(tmpdir)
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.facts.get_config_manager", return_value=cm), \
         patch("memory.persona.get_config_manager", return_value=cm), \
         patch("memory.reflection.get_config_manager", return_value=cm):
        event_log = EventLog()
        event_log._config_manager = cm
        fs = FactStore()
        fs._config_manager = cm
        pm = PersonaManager(event_log=event_log)
        pm._config_manager = cm
        re = ReflectionEngine(fs, pm, event_log=event_log)
        re._config_manager = cm
        rec = Reconciler(event_log)
        register_evidence_handlers(rec, pm, re)
    return event_log, fs, pm, re, rec, cm


# ── decay math snapshot (parametrized) ──────────────────────────────


@pytest.mark.parametrize("age_days,value,half_life,expected", [
    # Fresh signal — no decay
    (0.0, 2.0, 30.0, 2.0),
    # One half-life
    (30.0, 2.0, 30.0, 1.0),
    # Two half-lives
    (60.0, 2.0, 30.0, 0.5),
    # Disputation slower
    (180.0, 2.0, 180.0, 1.0),
    (90.0, 1.0, 180.0, 0.5 ** 0.5),  # ~0.707
])
def test_effective_value_decay_snapshots(age_days, value, half_life, expected):
    """Direct math: 0.5 ** (age/half_life) * value, tolerance 1e-6."""
    fixed_now = datetime(2026, 4, 23, 12, 0, 0)
    past = fixed_now - timedelta(days=age_days)

    if half_life == EVIDENCE_REIN_HALF_LIFE_DAYS or half_life == 30.0:
        from memory.evidence import effective_reinforcement
        # Patch the module constant to the test's chosen half_life
        with patch("memory.evidence.EVIDENCE_REIN_HALF_LIFE_DAYS", half_life):
            entry = {
                "reinforcement": value,
                "rein_last_signal_at": past.isoformat(),
            }
            actual = effective_reinforcement(entry, fixed_now)
    else:
        from memory.evidence import effective_disputation
        with patch("memory.evidence.EVIDENCE_DISP_HALF_LIFE_DAYS", half_life):
            entry = {
                "disputation": value,
                "disp_last_signal_at": past.isoformat(),
            }
            actual = effective_disputation(entry, fixed_now)
    assert actual == pytest.approx(expected, abs=1e-6)


# ── sub-zero accumulation semantics ─────────────────────────────────


def test_sub_zero_persists_through_score_oscillation():
    """RFC §3.5.3 "归档更积极": sub_zero_days never resets even when
    score climbs back to >= 0. Exhaustively walks negative→positive→
    negative to lock the invariant."""
    from memory.evidence import maybe_mark_sub_zero
    base = datetime(2026, 4, 23, 12, 0, 0)
    entry = {
        "reinforcement": 0.0,
        "disputation": 2.0,
        "rein_last_signal_at": None,
        "disp_last_signal_at": base.isoformat(),
        "sub_zero_days": 0,
        "sub_zero_last_increment_date": None,
    }
    # Day 0: score < 0 → +1
    assert maybe_mark_sub_zero(entry, base) is True
    assert entry["sub_zero_days"] == 1

    # Day 1: still negative, still bumps
    day1 = base + timedelta(days=1)
    entry["disp_last_signal_at"] = day1.isoformat()
    assert maybe_mark_sub_zero(entry, day1) is True
    assert entry["sub_zero_days"] == 2

    # Day 2: user reinforces → score positive — NO bump, NO reset
    day2 = base + timedelta(days=2)
    entry["reinforcement"] = 5.0
    entry["rein_last_signal_at"] = day2.isoformat()
    assert maybe_mark_sub_zero(entry, day2) is False
    assert entry["sub_zero_days"] == 2  # preserved

    # Day 3: another negative wave — bumps to 3 (resumes from 2, not 0)
    day3 = base + timedelta(days=3)
    entry["reinforcement"] = 0.0
    entry["disputation"] = 5.0
    entry["disp_last_signal_at"] = day3.isoformat()
    assert maybe_mark_sub_zero(entry, day3) is True
    assert entry["sub_zero_days"] == 3


def test_sub_zero_protected_never_accumulates():
    """RFC §3.5.7: protected=True is total exemption. Even with massive
    disputation and aged-out reinforcement, sub_zero_days stays 0."""
    from memory.evidence import maybe_mark_sub_zero
    base = datetime(2026, 4, 23, 12, 0, 0)
    entry = {
        "protected": True,
        "reinforcement": 0.0,
        "disputation": 100.0,
        "disp_last_signal_at": base.isoformat(),
        "sub_zero_days": 0,
    }
    for d in range(EVIDENCE_ARCHIVE_DAYS + 5):
        assert maybe_mark_sub_zero(entry, base + timedelta(days=d)) is False
    assert entry["sub_zero_days"] == 0


# ── shard size cap ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shard_overflow_creates_new_file(tmp_path):
    """Append > ARCHIVE_FILE_MAX_ENTRIES → second shard file appears."""
    from memory.archive_shards import aappend_to_shard, _list_shard_files

    archive_dir = str(tmp_path / "reflection_archive")
    fixed_now = datetime(2026, 4, 23, 12, 0, 0)

    # Round 1: fill exactly to the cap → one shard
    first_batch = [{"id": f"r{i}", "text": "x", "archived_at": fixed_now.isoformat()}
                   for i in range(ARCHIVE_FILE_MAX_ENTRIES)]
    path1 = await aappend_to_shard(archive_dir, first_batch, now=fixed_now)
    shards = _list_shard_files(archive_dir)
    assert len(shards) == 1
    assert os.path.basename(path1) == shards[0][0]
    with open(path1, encoding="utf-8") as f:
        assert len(json.load(f)) == ARCHIVE_FILE_MAX_ENTRIES

    # Round 2: one more entry → must spill into a NEW shard
    overflow_entry = [{"id": "rN", "text": "y", "archived_at": fixed_now.isoformat()}]
    path2 = await aappend_to_shard(archive_dir, overflow_entry, now=fixed_now)
    shards = _list_shard_files(archive_dir)
    assert len(shards) == 2, f"expected 2 shards, got {[s[0] for s in shards]}"
    # uuid8 suffixes must differ
    suffixes = [u for _, _, u in shards]
    assert len(set(suffixes)) == 2, f"uuid8 collision: {suffixes}"
    assert path2 != path1
    with open(path2, encoding="utf-8") as f:
        assert len(json.load(f)) == 1


@pytest.mark.asyncio
async def test_shard_append_rolls_multiple_shards_for_huge_batch(tmp_path):
    """A single call with > ARCHIVE_FILE_MAX_ENTRIES entries spills
    correctly across as many shards as needed (no truncation)."""
    from memory.archive_shards import aappend_to_shard, _list_shard_files

    archive_dir = str(tmp_path / "reflection_archive")
    fixed_now = datetime(2026, 4, 23, 12, 0, 0)
    n = ARCHIVE_FILE_MAX_ENTRIES * 2 + 7
    batch = [{"id": f"r{i}", "text": "x"} for i in range(n)]
    await aappend_to_shard(archive_dir, batch, now=fixed_now)
    shards = _list_shard_files(archive_dir)
    assert len(shards) == 3
    total = 0
    for fn, _, _ in shards:
        with open(os.path.join(archive_dir, fn), encoding="utf-8") as f:
            total += len(json.load(f))
    assert total == n


# ── legacy flat-file migration ──────────────────────────────────────


def test_legacy_flat_archive_migration_buckets_by_date(tmp_path):
    """Three different `archived_at` dates → three buckets, distinct
    uuid8 suffixes, sentinel written, flat file deleted."""
    from memory.archive_shards import (
        MIGRATION_SENTINEL_FILENAME,
        _list_shard_files,
        migrate_flat_archive_to_shards_sync,
    )

    flat_path = str(tmp_path / "reflections_archive.json")
    archive_dir = str(tmp_path / "reflection_archive")

    entries = [
        {"id": f"r{i}", "text": "old",
         "archived_at": f"2026-04-{20 + (i % 3):02d}T10:00:00"}
        for i in range(7)
    ]
    with open(flat_path, "w", encoding="utf-8") as f:
        json.dump(entries, f)

    migrated, n_entries, n_shards = migrate_flat_archive_to_shards_sync(
        flat_path, archive_dir,
    )
    assert migrated is True
    assert n_entries == 7
    # 7 entries across 3 dates, well under MAX_ENTRIES → 3 shards
    assert n_shards == 3

    shards = _list_shard_files(archive_dir)
    dates = sorted({d for _, d, _ in shards})
    assert dates == ["2026-04-20", "2026-04-21", "2026-04-22"]
    # uuid8 uniqueness across all shards
    uuids = [u for _, _, u in shards]
    assert len(set(uuids)) == len(uuids), f"uuid8 collision: {uuids}"

    # Sentinel + flat-file deletion
    assert os.path.exists(os.path.join(archive_dir, MIGRATION_SENTINEL_FILENAME))
    assert not os.path.exists(flat_path)


def test_legacy_flat_archive_migration_idempotent(tmp_path):
    """Re-running after success is a no-op (sentinel guard)."""
    from memory.archive_shards import migrate_flat_archive_to_shards_sync

    flat_path = str(tmp_path / "reflections_archive.json")
    archive_dir = str(tmp_path / "reflection_archive")
    entries = [{"id": "r0", "text": "x", "archived_at": "2026-04-22T10:00:00"}]
    with open(flat_path, "w", encoding="utf-8") as f:
        json.dump(entries, f)

    migrate_flat_archive_to_shards_sync(flat_path, archive_dir)
    # Recreate the flat file (simulate operator confusion / partial restore);
    # sentinel still guards us — no migration runs.
    with open(flat_path, "w", encoding="utf-8") as f:
        json.dump(entries, f)

    migrated, _, _ = migrate_flat_archive_to_shards_sync(flat_path, archive_dir)
    assert migrated is False


def test_legacy_flat_archive_migration_no_flat_file_is_noop(tmp_path):
    from memory.archive_shards import migrate_flat_archive_to_shards_sync

    archive_dir = str(tmp_path / "reflection_archive")
    flat_path = str(tmp_path / "reflections_archive.json")
    migrated, n_entries, n_shards = migrate_flat_archive_to_shards_sync(
        flat_path, archive_dir,
    )
    assert (migrated, n_entries, n_shards) == (False, 0, 0)


def test_legacy_flat_archive_migration_overflow_still_splits(tmp_path):
    """One date with > MAX_ENTRIES entries → split into multiple shards
    sharing the same date prefix but distinct uuid8s."""
    from memory.archive_shards import (
        _list_shard_files,
        migrate_flat_archive_to_shards_sync,
    )

    flat_path = str(tmp_path / "reflections_archive.json")
    archive_dir = str(tmp_path / "reflection_archive")
    n = ARCHIVE_FILE_MAX_ENTRIES + 50
    entries = [
        {"id": f"r{i}", "text": "x", "archived_at": "2026-04-22T10:00:00"}
        for i in range(n)
    ]
    with open(flat_path, "w", encoding="utf-8") as f:
        json.dump(entries, f)

    migrated, n_entries, n_shards = migrate_flat_archive_to_shards_sync(
        flat_path, archive_dir,
    )
    assert migrated is True and n_entries == n and n_shards == 2
    shards = _list_shard_files(archive_dir)
    dates = {d for _, d, _ in shards}
    assert dates == {"2026-04-22"}
    uuids = [u for _, _, u in shards]
    assert len(set(uuids)) == 2


# ── archive emits correct event + replay-stable ─────────────────────


@pytest.mark.asyncio
async def test_aarchive_reflection_emits_state_changed_to_archived(tmp_path):
    from memory.event_log import EVT_REFLECTION_STATE_CHANGED
    _ev, _fs, _pm, re, _rec, _cm = _install(str(tmp_path))
    rid = "ref_arc"
    seed = [{
        "id": rid, "text": "test reflection", "entity": "master",
        "status": "confirmed", "source_fact_ids": [],
        "created_at": "2026-04-22T10:00:00",
        "feedback": None, "next_eligible_at": "2026-04-22T10:00:00",
    }]
    await re.asave_reflections("小天", seed)

    ok = await re.aarchive_reflection("小天", rid)
    assert ok is True

    # View: entry removed from main file
    remaining = await re.aload_reflections("小天", include_archived=True)
    assert all(r.get("id") != rid for r in remaining)

    # Shard exists with the entry + archived_at + archive_shard_path
    archive_dir = re._reflections_archive_dir("小天")
    shard_files = [f for f in os.listdir(archive_dir) if f.endswith(".json")]
    assert len(shard_files) == 1
    with open(os.path.join(archive_dir, shard_files[0]), encoding="utf-8") as f:
        shard_data = json.load(f)
    assert len(shard_data) == 1
    archived = shard_data[0]
    assert archived["id"] == rid
    assert archived["status"] == "archived"
    assert archived["archived_at"] is not None
    assert archived["archive_shard_path"] == shard_files[0]

    # Event log: exactly one state_changed event with to='archived'
    events = _ev.read_since("小天", None)
    state_evts = [e for e in events if e["type"] == EVT_REFLECTION_STATE_CHANGED]
    assert len(state_evts) == 1
    payload = state_evts[0]["payload"]
    assert payload["reflection_id"] == rid
    assert payload["from"] == "confirmed"
    assert payload["to"] == "archived"
    assert payload["archive_shard_path"] == shard_files[0]


@pytest.mark.asyncio
async def test_aarchive_reflection_protected_skipped(tmp_path):
    """RFC §3.5.7: protected reflections never archive."""
    _ev, _fs, _pm, re, _rec, _cm = _install(str(tmp_path))
    rid = "ref_protected"
    seed = [{
        "id": rid, "text": "x", "entity": "master", "status": "confirmed",
        "source_fact_ids": [], "created_at": "2026-04-22T10:00:00",
        "feedback": None, "next_eligible_at": "2026-04-22T10:00:00",
        "protected": True,
    }]
    await re.asave_reflections("小天", seed)
    ok = await re.aarchive_reflection("小天", rid)
    assert ok is False
    # Nothing written, nothing archived
    remaining = await re.aload_reflections("小天", include_archived=True)
    assert any(r.get("id") == rid for r in remaining)


@pytest.mark.asyncio
async def test_aarchive_reflection_replay_stable(tmp_path):
    """Re-applying the state_changed handler 10 times leaves the view
    consistent (entry stays out of active list, idempotent)."""
    from memory.evidence_handlers import make_reflection_archive_handler
    _ev, _fs, _pm, re, _rec, _cm = _install(str(tmp_path))
    rid = "ref_replay"
    await re.asave_reflections("小天", [{
        "id": rid, "text": "x", "entity": "master", "status": "confirmed",
        "source_fact_ids": [], "created_at": "2026-04-22T10:00:00",
        "feedback": None, "next_eligible_at": "2026-04-22T10:00:00",
    }])
    await re.aarchive_reflection("小天", rid)

    handler = make_reflection_archive_handler(re)
    payload = {"reflection_id": rid, "from": "confirmed", "to": "archived",
               "archive_shard_path": "ignored"}
    # First replay: no-op (already removed by aarchive_reflection's save).
    # 10 replays must NOT re-introduce the entry.
    for _ in range(10):
        changed = handler("小天", payload)
        assert changed is False
    remaining = await re.aload_reflections("小天", include_archived=True)
    assert all(r.get("id") != rid for r in remaining)


@pytest.mark.asyncio
async def test_aarchive_persona_entry_emits_fact_added_with_shard_path(tmp_path):
    from memory.event_log import EVT_PERSONA_FACT_ADDED
    _ev, _fs, pm, _re, _rec, _cm = _install(str(tmp_path))

    # Bootstrap a persona with one mutable entry under 'master'.
    persona = await pm.aensure_persona("小天")
    persona.setdefault("master", {}).setdefault("facts", []).append({
        "id": "manual_test1", "text": "user likes cats",
        "source": "manual", "source_id": None,
        "protected": False,
    })
    await pm.asave_persona("小天", persona)

    ok = await pm.aarchive_persona_entry("小天", "master", "manual_test1")
    assert ok is True

    # View: removed from facts
    persona = await pm.aensure_persona("小天")
    facts = persona.get("master", {}).get("facts", [])
    assert all(f.get("id") != "manual_test1" for f in facts)

    # Shard file with the entry
    archive_dir = pm._persona_archive_dir("小天")
    shard_files = [f for f in os.listdir(archive_dir) if f.endswith(".json")]
    assert len(shard_files) == 1
    with open(os.path.join(archive_dir, shard_files[0]), encoding="utf-8") as f:
        shard_data = json.load(f)
    assert any(e.get("id") == "manual_test1" for e in shard_data)
    archived = next(e for e in shard_data if e.get("id") == "manual_test1")
    assert archived["archived_at"] is not None
    assert archived["archive_shard_path"] == shard_files[0]

    # Event with archive_shard_path
    events = _ev.read_since("小天", None)
    pa_evts = [e for e in events if e["type"] == EVT_PERSONA_FACT_ADDED]
    assert len(pa_evts) == 1
    payload = pa_evts[0]["payload"]
    assert payload["entity_key"] == "master"
    assert payload["entry_id"] == "manual_test1"
    assert payload["archive_shard_path"] == shard_files[0]
    assert payload["archived_at"] is not None


@pytest.mark.asyncio
async def test_aarchive_persona_entry_protected_skipped(tmp_path):
    _ev, _fs, pm, _re, _rec, _cm = _install(str(tmp_path))
    persona = await pm.aensure_persona("小天")
    persona.setdefault("master", {}).setdefault("facts", []).append({
        "id": "card_protected", "text": "x",
        "source": "character_card", "protected": True,
    })
    await pm.asave_persona("小天", persona)

    ok = await pm.aarchive_persona_entry("小天", "master", "card_protected")
    assert ok is False
    persona = await pm.aensure_persona("小天")
    assert any(
        f.get("id") == "card_protected"
        for f in persona.get("master", {}).get("facts", [])
    )


# ── sweep loop end-to-end ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_sub_zero_increment_reaches_archive_threshold(tmp_path):
    """Drive a reflection's score below 0 for EVIDENCE_ARCHIVE_DAYS
    simulated days via aincrement_sub_zero, then verify aarchive_reflection
    moves it. End-to-end stand-in for `_periodic_archive_sweep_loop`
    minus the asyncio.sleep + per-character iteration boilerplate."""
    _ev, _fs, _pm, re, _rec, _cm = _install(str(tmp_path))
    rid = "ref_sweep"
    base = datetime(2026, 4, 23, 12, 0, 0)
    # Seed with disputation high enough to keep score < 0 indefinitely.
    seed = [{
        "id": rid, "text": "test", "entity": "master",
        "status": "confirmed", "source_fact_ids": [],
        "created_at": base.isoformat(),
        "feedback": None, "next_eligible_at": base.isoformat(),
        "reinforcement": 0.0,
        "disputation": 5.0,
        "rein_last_signal_at": None,
        "disp_last_signal_at": base.isoformat(),
        "sub_zero_days": 0,
        "sub_zero_last_increment_date": None,
    }]
    await re.asave_reflections("小天", seed)

    # Tick once per simulated day for EVIDENCE_ARCHIVE_DAYS days.
    for d in range(EVIDENCE_ARCHIVE_DAYS):
        result = await re.aincrement_sub_zero(
            "小天", rid, base + timedelta(days=d),
        )
        assert result == d + 1, f"day {d}: expected count={d + 1}, got {result}"

    # After EVIDENCE_ARCHIVE_DAYS increments → counter at threshold → archive.
    refls = await re._aload_reflections_full("小天")
    target = next(r for r in refls if r["id"] == rid)
    assert target["sub_zero_days"] == EVIDENCE_ARCHIVE_DAYS

    archived_ok = await re.aarchive_reflection("小天", rid)
    assert archived_ok is True

    # Active view no longer contains it
    remaining = await re.aload_reflections("小天", include_archived=True)
    assert all(r.get("id") != rid for r in remaining)
    # Shard contains it
    archive_dir = re._reflections_archive_dir("小天")
    shard_files = [f for f in os.listdir(archive_dir) if f.endswith(".json")]
    found = False
    for fn in shard_files:
        with open(os.path.join(archive_dir, fn), encoding="utf-8") as f:
            data = json.load(f)
        if any(e.get("id") == rid for e in data):
            found = True
            break
    assert found


@pytest.mark.asyncio
async def test_aincrement_sub_zero_debounces_same_day(tmp_path):
    """Calling aincrement_sub_zero twice on the same day → second call
    returns None (debounce inside maybe_mark_sub_zero)."""
    _ev, _fs, _pm, re, _rec, _cm = _install(str(tmp_path))
    rid = "ref_dbnc"
    base = datetime(2026, 4, 23, 12, 0, 0)
    await re.asave_reflections("小天", [{
        "id": rid, "text": "x", "entity": "master", "status": "confirmed",
        "source_fact_ids": [], "created_at": base.isoformat(),
        "feedback": None, "next_eligible_at": base.isoformat(),
        "reinforcement": 0.0, "disputation": 5.0,
        "disp_last_signal_at": base.isoformat(),
    }])
    first = await re.aincrement_sub_zero("小天", rid, base)
    second = await re.aincrement_sub_zero("小天", rid, base)
    assert first == 1
    assert second is None


@pytest.mark.asyncio
async def test_aincrement_sub_zero_protected_returns_none(tmp_path):
    _ev, _fs, _pm, re, _rec, _cm = _install(str(tmp_path))
    rid = "ref_prot_inc"
    base = datetime(2026, 4, 23, 12, 0, 0)
    await re.asave_reflections("小天", [{
        "id": rid, "text": "x", "entity": "master", "status": "confirmed",
        "source_fact_ids": [], "created_at": base.isoformat(),
        "feedback": None, "next_eligible_at": base.isoformat(),
        "reinforcement": 0.0, "disputation": 100.0,
        "disp_last_signal_at": base.isoformat(),
        "protected": True,
    }])
    result = await re.aincrement_sub_zero("小天", rid, base)
    assert result is None


# ── integration with sharded save_reflections (age-based archival) ──


@pytest.mark.asyncio
async def test_age_based_archival_writes_to_shards_not_flat_file(tmp_path):
    """Existing age-based archival path (promoted/denied >30d) now lands
    in shards, not the legacy flat file. Verifies the refactor of
    save_reflections / asave_reflections kept the behavior intact."""
    from memory.reflection import _REFLECTION_ARCHIVE_DAYS

    _ev, _fs, _pm, re, _rec, _cm = _install(str(tmp_path))
    cutoff = datetime.now() - timedelta(days=_REFLECTION_ARCHIVE_DAYS + 1)
    # Seed an old promoted reflection on disk
    old = {
        "id": "ref_old", "text": "x", "entity": "master",
        "status": "promoted", "source_fact_ids": [],
        "created_at": cutoff.isoformat(),
        "promoted_at": cutoff.isoformat(),
        "feedback": None, "next_eligible_at": cutoff.isoformat(),
    }
    # Direct file write to simulate persisted history
    refl_path = re._reflections_path("小天")
    os.makedirs(os.path.dirname(refl_path), exist_ok=True)
    with open(refl_path, "w", encoding="utf-8") as f:
        json.dump([old], f)

    # Save with empty active list → triggers age-based archival path
    await re.asave_reflections("小天", [])

    # Active view: empty
    with open(refl_path, encoding="utf-8") as f:
        assert json.load(f) == []
    # Legacy flat file should NOT be created
    assert not os.path.exists(re._reflections_legacy_archive_path("小天"))
    # Sharded archive dir contains the entry
    archive_dir = re._reflections_archive_dir("小天")
    shard_files = [f for f in os.listdir(archive_dir) if f.endswith(".json")]
    assert len(shard_files) == 1
    with open(os.path.join(archive_dir, shard_files[0]), encoding="utf-8") as f:
        data = json.load(f)
    assert any(e.get("id") == "ref_old" for e in data)
