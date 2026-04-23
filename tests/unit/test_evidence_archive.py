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


@pytest.mark.parametrize("func,age_days,value,half_life,expected", [
    # Reinforcement — fresh signal, no decay
    ("rein", 0.0, 2.0, 30.0, 2.0),
    # Reinforcement — one half-life
    ("rein", 30.0, 2.0, 30.0, 1.0),
    # Reinforcement — two half-lives
    ("rein", 60.0, 2.0, 30.0, 0.5),
    # Disputation — one half-life (slower; default 180d)
    ("disp", 180.0, 2.0, 180.0, 1.0),
    # Disputation — half a half-life
    ("disp", 90.0, 1.0, 180.0, 0.5 ** 0.5),  # ~0.707
])
def test_effective_value_decay_snapshots(func, age_days, value, half_life, expected):
    """Direct math: 0.5 ** (age/half_life) * value, tolerance 1e-6.

    `func` ("rein" | "disp") explicitly dispatches to the right helper
    so the test doesn't rely on numeric-equality probes against the
    module constants (coderabbit nit, PR #934).
    """
    fixed_now = datetime(2026, 4, 23, 12, 0, 0)
    past = fixed_now - timedelta(days=age_days)

    if func == "rein":
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


# ── shard selection by capacity (chatgpt-codex review #934) ─────────


@pytest.mark.asyncio
async def test_shard_picker_fills_earlier_same_day_shard_first(tmp_path):
    """When today already has multiple shards and the lexically-LAST one
    is full but an earlier one still has room, append must coalesce into
    the earlier shard rather than rolling a brand-new third shard.

    Regression for chatgpt-codex P1 (PR #934): the old `_pick_shard_path`
    only checked `todays[-1]`, so a full lex-last + non-full lex-earlier
    state caused unbounded shard proliferation."""
    from memory.archive_shards import (
        _list_shard_files,
        _pick_shard_path_for_today,
        aappend_to_shard,
    )

    archive_dir = str(tmp_path / "reflection_archive")
    fixed_now = datetime(2026, 4, 23, 12, 0, 0)
    today = fixed_now.date().isoformat()

    # Manually craft two same-day shards: "earlier" (lex-smaller uuid8 = "0")
    # holds a few entries with capacity left; "later" (lex-larger uuid8 = "f")
    # is full. Real production filenames use random uuid8 — here we force the
    # lex order so the test is deterministic.
    os.makedirs(archive_dir, exist_ok=True)
    earlier_fn = f"{today}_00000000.json"
    later_fn = f"{today}_ffffffff.json"
    earlier_entries = [{"id": f"early{i}", "text": "x"} for i in range(3)]
    later_entries = [
        {"id": f"late{i}", "text": "x"} for i in range(ARCHIVE_FILE_MAX_ENTRIES)
    ]
    with open(os.path.join(archive_dir, earlier_fn), "w", encoding="utf-8") as f:
        json.dump(earlier_entries, f)
    with open(os.path.join(archive_dir, later_fn), "w", encoding="utf-8") as f:
        json.dump(later_entries, f)

    # Direct picker check: must return the earlier (non-full) shard, not
    # roll a fresh one and not return the full lex-last shard.
    sizes = {earlier_fn: len(earlier_entries), later_fn: len(later_entries)}
    picked = _pick_shard_path_for_today(archive_dir, today, sizes)
    assert os.path.basename(picked) == earlier_fn, (
        f"expected coalesce into {earlier_fn}, got {os.path.basename(picked)}"
    )

    # End-to-end: appending one entry should land in the earlier shard;
    # total shard count stays at 2, no new file created.
    new_entry = [{"id": "added", "text": "y"}]
    await aappend_to_shard(archive_dir, new_entry, now=fixed_now)
    shards = _list_shard_files(archive_dir)
    assert len(shards) == 2, (
        f"expected exactly 2 shards (no proliferation), got "
        f"{[s[0] for s in shards]}"
    )
    with open(os.path.join(archive_dir, earlier_fn), encoding="utf-8") as f:
        earlier_data = json.load(f)
    assert any(e.get("id") == "added" for e in earlier_data), (
        "new entry should have landed in the earlier shard"
    )
    with open(os.path.join(archive_dir, later_fn), encoding="utf-8") as f:
        later_data = json.load(f)
    assert all(e.get("id") != "added" for e in later_data), (
        "full lex-last shard must not have been touched"
    )


@pytest.mark.asyncio
async def test_shard_picker_rolls_new_shard_when_all_today_full(tmp_path):
    """When every same-day shard is full, picker must mint a fresh
    uuid8 shard (not return one of the full ones)."""
    from memory.archive_shards import (
        _list_shard_files,
        aappend_to_shard,
    )

    archive_dir = str(tmp_path / "reflection_archive")
    fixed_now = datetime(2026, 4, 23, 12, 0, 0)
    today = fixed_now.date().isoformat()
    os.makedirs(archive_dir, exist_ok=True)

    # Two pre-existing same-day shards, both saturated.
    full_a_fn = f"{today}_00000000.json"
    full_b_fn = f"{today}_aaaaaaaa.json"
    saturated = [{"id": f"x{i}"} for i in range(ARCHIVE_FILE_MAX_ENTRIES)]
    for fn in (full_a_fn, full_b_fn):
        with open(os.path.join(archive_dir, fn), "w", encoding="utf-8") as f:
            json.dump(saturated, f)

    await aappend_to_shard(archive_dir, [{"id": "spillover"}], now=fixed_now)
    shards = _list_shard_files(archive_dir)
    assert len(shards) == 3, f"expected 3 shards, got {[s[0] for s in shards]}"
    new_fn = next(
        s[0] for s in shards if s[0] not in {full_a_fn, full_b_fn}
    )
    with open(os.path.join(archive_dir, new_fn), encoding="utf-8") as f:
        assert json.load(f) == [{"id": "spillover"}]


# ── stamper callback applies metadata pre-write (chatgpt-codex P2) ──


@pytest.mark.asyncio
async def test_aappend_to_shard_stamper_runs_before_write(tmp_path):
    """The `stamper` callback must mutate each chunk BEFORE serialization
    so the on-disk record carries the stamped fields. Regression for
    chatgpt-codex P2 / coderabbit Major (PR #934)."""
    from memory.archive_shards import aappend_to_shard

    archive_dir = str(tmp_path / "ref_arc")
    fixed_now = datetime(2026, 4, 23, 12, 0, 0)
    entries = [{"id": f"r{i}", "text": "x"} for i in range(3)]

    def _stamp(chunk, basename):
        for e in chunk:
            e["archived_at"] = fixed_now.isoformat()
            e["archive_shard_path"] = basename

    path = await aappend_to_shard(
        archive_dir, list(entries), now=fixed_now, stamper=_stamp,
    )
    with open(path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert len(on_disk) == 3
    for e in on_disk:
        assert e["archived_at"] == fixed_now.isoformat()
        assert e["archive_shard_path"] == os.path.basename(path)


@pytest.mark.asyncio
async def test_aappend_to_shard_stamper_uses_correct_path_per_chunk_on_overflow(
    tmp_path,
):
    """When one batch overflows into multiple shards, each entry's
    stamped `archive_shard_path` must match the SHARD IT WAS WRITTEN TO,
    not just the last shard the call touched. Regression for chatgpt-
    codex P2 (PR #934)."""
    from memory.archive_shards import _list_shard_files, aappend_to_shard

    archive_dir = str(tmp_path / "ref_arc")
    fixed_now = datetime(2026, 4, 23, 12, 0, 0)
    n = ARCHIVE_FILE_MAX_ENTRIES + 5
    entries = [{"id": f"r{i}", "text": "x"} for i in range(n)]

    def _stamp(chunk, basename):
        for e in chunk:
            e["archive_shard_path"] = basename

    await aappend_to_shard(
        archive_dir, list(entries), now=fixed_now, stamper=_stamp,
    )
    shards = _list_shard_files(archive_dir)
    assert len(shards) == 2
    # Map every on-disk entry to the shard it lives in; verify the stamped
    # field matches the actual filename (not the "last shard touched").
    for fn, _, _ in shards:
        with open(os.path.join(archive_dir, fn), encoding="utf-8") as f:
            data = json.load(f)
        for e in data:
            assert e["archive_shard_path"] == fn, (
                f"entry {e.get('id')} stamped with "
                f"{e['archive_shard_path']!r} but lives in {fn!r}"
            )


def test_append_to_shard_sync_stamper_uses_correct_path_per_chunk_on_overflow(
    tmp_path,
):
    """Sync twin of the async overflow-stamper test — keeps the symmetry
    invariant (CLAUDE.md "对偶性是硬性要求")."""
    from memory.archive_shards import _list_shard_files, append_to_shard_sync

    archive_dir = str(tmp_path / "ref_arc")
    fixed_now = datetime(2026, 4, 23, 12, 0, 0)
    n = ARCHIVE_FILE_MAX_ENTRIES + 3
    entries = [{"id": f"r{i}", "text": "x"} for i in range(n)]

    def _stamp(chunk, basename):
        for e in chunk:
            e["archive_shard_path"] = basename

    append_to_shard_sync(
        archive_dir, list(entries), now=fixed_now, stamper=_stamp,
    )
    shards = _list_shard_files(archive_dir)
    assert len(shards) == 2
    for fn, _, _ in shards:
        with open(os.path.join(archive_dir, fn), encoding="utf-8") as f:
            data = json.load(f)
        for e in data:
            assert e["archive_shard_path"] == fn


@pytest.mark.asyncio
async def test_age_based_archival_records_have_metadata_on_disk(tmp_path):
    """End-to-end regression: after `asave_reflections` archives an aged
    entry into a shard, the ON-DISK shard JSON must include both
    `archived_at` and `archive_shard_path`. Previously these were only
    set on the in-memory list AFTER the disk write, so the on-disk record
    was missing them. Regression for chatgpt-codex P2 + coderabbit
    Major (PR #934)."""
    from memory.reflection import _REFLECTION_ARCHIVE_DAYS

    _ev, _fs, _pm, re, _rec, _cm = _install(str(tmp_path))
    cutoff = datetime.now() - timedelta(days=_REFLECTION_ARCHIVE_DAYS + 1)
    old = {
        "id": "ref_meta_old", "text": "x", "entity": "master",
        "status": "promoted", "source_fact_ids": [],
        "created_at": cutoff.isoformat(),
        "promoted_at": cutoff.isoformat(),
        "feedback": None, "next_eligible_at": cutoff.isoformat(),
    }
    refl_path = re._reflections_path("小天")
    os.makedirs(os.path.dirname(refl_path), exist_ok=True)
    with open(refl_path, "w", encoding="utf-8") as f:
        json.dump([old], f)

    await re.asave_reflections("小天", [])

    archive_dir = re._reflections_archive_dir("小天")
    shard_files = [f for f in os.listdir(archive_dir) if f.endswith(".json")]
    assert len(shard_files) == 1
    shard_fn = shard_files[0]
    with open(os.path.join(archive_dir, shard_fn), encoding="utf-8") as f:
        data = json.load(f)
    archived = next(e for e in data if e.get("id") == "ref_meta_old")
    assert archived.get("archived_at"), (
        "on-disk record must have archived_at timestamp"
    )
    assert archived.get("archive_shard_path") == shard_fn, (
        "on-disk record must have archive_shard_path matching its own filename"
    )


def test_age_based_archival_records_have_metadata_on_disk_sync(tmp_path):
    """Sync twin — keeps symmetry between save_reflections /
    asave_reflections (CLAUDE.md 对偶性)."""
    from memory.reflection import _REFLECTION_ARCHIVE_DAYS

    _ev, _fs, _pm, re, _rec, _cm = _install(str(tmp_path))
    cutoff = datetime.now() - timedelta(days=_REFLECTION_ARCHIVE_DAYS + 1)
    old = {
        "id": "ref_meta_old_sync", "text": "x", "entity": "master",
        "status": "denied", "source_fact_ids": [],
        "created_at": cutoff.isoformat(),
        "denied_at": cutoff.isoformat(),
        "feedback": None, "next_eligible_at": cutoff.isoformat(),
    }
    refl_path = re._reflections_path("小天")
    os.makedirs(os.path.dirname(refl_path), exist_ok=True)
    with open(refl_path, "w", encoding="utf-8") as f:
        json.dump([old], f)

    re.save_reflections("小天", [])

    archive_dir = re._reflections_archive_dir("小天")
    shard_files = [f for f in os.listdir(archive_dir) if f.endswith(".json")]
    assert len(shard_files) == 1
    shard_fn = shard_files[0]
    with open(os.path.join(archive_dir, shard_fn), encoding="utf-8") as f:
        data = json.load(f)
    archived = next(e for e in data if e.get("id") == "ref_meta_old_sync")
    assert archived.get("archived_at")
    assert archived.get("archive_shard_path") == shard_fn


@pytest.mark.asyncio
async def test_age_based_archival_overflow_each_chunk_has_correct_path(
    tmp_path,
):
    """Archive a batch large enough to overflow into 2 shards; every
    on-disk entry must carry the `archive_shard_path` of the shard it
    actually lives in (NOT just the last shard touched). Regression for
    chatgpt-codex P2 (PR #934)."""
    from memory.reflection import _REFLECTION_ARCHIVE_DAYS

    _ev, _fs, _pm, re, _rec, _cm = _install(str(tmp_path))
    cutoff = datetime.now() - timedelta(days=_REFLECTION_ARCHIVE_DAYS + 1)
    n = ARCHIVE_FILE_MAX_ENTRIES + 7
    olds = [
        {
            "id": f"ref_overflow_{i}", "text": "x", "entity": "master",
            "status": "promoted", "source_fact_ids": [],
            "created_at": cutoff.isoformat(),
            "promoted_at": cutoff.isoformat(),
            "feedback": None, "next_eligible_at": cutoff.isoformat(),
        }
        for i in range(n)
    ]
    refl_path = re._reflections_path("小天")
    os.makedirs(os.path.dirname(refl_path), exist_ok=True)
    with open(refl_path, "w", encoding="utf-8") as f:
        json.dump(olds, f)

    await re.asave_reflections("小天", [])

    archive_dir = re._reflections_archive_dir("小天")
    shard_files = sorted(
        f for f in os.listdir(archive_dir) if f.endswith(".json")
    )
    assert len(shard_files) == 2
    total_seen = 0
    for fn in shard_files:
        with open(os.path.join(archive_dir, fn), encoding="utf-8") as f:
            data = json.load(f)
        for e in data:
            assert e.get("archive_shard_path") == fn, (
                f"entry {e.get('id')} stamped {e.get('archive_shard_path')!r} "
                f"but lives in {fn!r}"
            )
            assert e.get("archived_at")
            total_seen += 1
    assert total_seen == n


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


# ── PR #934 round-2 regressions ─────────────────────────────────────


@pytest.mark.asyncio
async def test_aappend_to_shard_corrupt_shard_left_untouched(tmp_path):
    """Round-2 Major #1: a corrupt same-day shard must NOT be picked +
    overwritten. Prior bug: `_aread_shard` returned [] on
    JSONDecodeError → picker saw size=0 → reuse → atomic write replaced
    the corrupt original, losing salvageable content.

    Fix: `_aread_shard` raises `ShardCorruptError`; both probe + pick
    paths catch it and treat the shard as full.
    """
    from memory.archive_shards import (
        _list_shard_files,
        aappend_to_shard,
    )

    archive_dir = str(tmp_path / "ref_arc")
    fixed_now = datetime(2026, 4, 23, 12, 0, 0)
    today = fixed_now.date().isoformat()
    os.makedirs(archive_dir, exist_ok=True)

    # Plant a non-JSON corrupt shard with today's date prefix.
    corrupt_fn = f"{today}_dead0001.json"
    corrupt_path = os.path.join(archive_dir, corrupt_fn)
    corrupt_payload = "this is not JSON at all { unbalanced ["
    with open(corrupt_path, "w", encoding="utf-8") as f:
        f.write(corrupt_payload)

    # Append a new entry → must NOT touch the corrupt file.
    new_entry = [{"id": "fresh", "text": "y"}]
    written_path = await aappend_to_shard(
        archive_dir, new_entry, now=fixed_now,
    )

    # Corrupt file unchanged on disk
    with open(corrupt_path, encoding="utf-8") as f:
        assert f.read() == corrupt_payload, (
            "corrupt shard must not have been overwritten"
        )

    # The new entry landed in a DIFFERENT shard
    assert os.path.basename(written_path) != corrupt_fn
    with open(written_path, encoding="utf-8") as f:
        new_shard_data = json.load(f)
    assert any(e.get("id") == "fresh" for e in new_shard_data)

    # Now there are two shards: the corrupt one + the new one
    shards = _list_shard_files(archive_dir)
    assert len(shards) == 2
    assert any(s[0] == corrupt_fn for s in shards)


@pytest.mark.asyncio
async def test_aincrement_sub_zero_keeps_cache_clean_on_save_failure(tmp_path):
    """Round-2 Major #2: if `arecord_and_save` (or its inner
    `assert_cloudsave_writable`) raises, the cached entry must NOT have
    been mutated by `maybe_mark_sub_zero`. Prior bug mutated in-place
    BEFORE record_and_save → orphan increment with no event log row.

    Fix: probe on a staged copy; only the `_sync_mutate` callback
    (which runs inside record_and_save AFTER append succeeds) touches
    the live cache.
    """
    from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED
    _ev, _fs, _pm, re, _rec, _cm = _install(str(tmp_path))

    rid = "ref_cache_safety"
    base = datetime(2026, 4, 23, 12, 0, 0)
    seed = [{
        "id": rid, "text": "x", "entity": "master", "status": "confirmed",
        "source_fact_ids": [], "created_at": base.isoformat(),
        "feedback": None, "next_eligible_at": base.isoformat(),
        "reinforcement": 0.0,
        "disputation": 5.0,
        "rein_last_signal_at": None,
        "disp_last_signal_at": base.isoformat(),
        "sub_zero_days": 0,
        "sub_zero_last_increment_date": None,
    }]
    await re.asave_reflections("小天", seed)

    # Force record_and_save to raise on the next call by patching the
    # per-character event-log lock-bound writer. We patch
    # `EventLog.record_and_save` (the sync core) so the async hop
    # raises before any save happens.
    real_record = re._event_log.record_and_save

    def _boom(*a, **kw):
        raise RuntimeError("simulated cloudsave failure")

    re._event_log.record_and_save = _boom
    try:
        with pytest.raises(RuntimeError, match="simulated cloudsave failure"):
            await re.aincrement_sub_zero("小天", rid, base)
    finally:
        re._event_log.record_and_save = real_record

    # Cached entry must be UNCHANGED — no orphan sub_zero increment.
    refls = await re._aload_reflections_full("小天")
    target = next(r for r in refls if r["id"] == rid)
    assert target["sub_zero_days"] == 0, (
        "cache leaked an increment despite save failure (round-2 Major #2 regression)"
    )
    assert target["sub_zero_last_increment_date"] is None

    # And no EVIDENCE_UPDATED event was recorded for this entry.
    events = _ev.read_since("小天", None)
    matching = [
        e for e in events
        if e["type"] == EVT_REFLECTION_EVIDENCE_UPDATED
        and e["payload"].get("reflection_id") == rid
    ]
    assert matching == [], (
        f"unexpected event(s) recorded despite save failure: {matching}"
    )

    # Sanity: a NEXT call (with the writer restored) must succeed and
    # advance to 1, proving the entry wasn't somehow already-debounced.
    new_count = await re.aincrement_sub_zero("小天", rid, base)
    assert new_count == 1


@pytest.mark.asyncio
async def test_archive_handler_self_heals_missing_shard(tmp_path):
    """Round-2 Major #3: if `aarchive_reflection`'s shard append raises
    AFTER record_and_save committed (entry removed from active view),
    the next reconciler replay must re-create the shard from
    `entry_snapshot` in the event payload — otherwise the entry is
    pure data loss.

    Symmetric for persona archive handler.
    """
    from unittest.mock import patch
    from memory.event_log import (
        EVT_PERSONA_FACT_ADDED,
        EVT_REFLECTION_STATE_CHANGED,
    )
    from memory.evidence_handlers import (
        make_persona_archive_handler,
        make_reflection_archive_handler,
    )

    # ── reflection half ──────────────────────────────────────────────
    _ev, _fs, pm, re, _rec, _cm = _install(str(tmp_path))
    rid = "ref_selfheal"
    seed = [{
        "id": rid, "text": "lost-then-found", "entity": "master",
        "status": "confirmed", "source_fact_ids": [],
        "created_at": "2026-04-22T10:00:00",
        "feedback": None, "next_eligible_at": "2026-04-22T10:00:00",
    }]
    await re.asave_reflections("小天", seed)

    # Patch `aappend_to_shard` at its source module — both
    # reflection.py and persona.py do a function-local import, so this
    # is the symbol they actually resolve at call-time. Simulates a
    # crash between record_and_save and the shard append. Active view
    # has already lost the entry by the time this raises.
    async def _shard_boom(*a, **kw):
        raise RuntimeError("simulated shard write failure")

    with patch("memory.archive_shards.aappend_to_shard", _shard_boom):
        with pytest.raises(RuntimeError, match="simulated shard write failure"):
            await re.aarchive_reflection("小天", rid)

    # Active view: entry already removed (record_and_save ran)
    remaining = await re.aload_reflections("小天", include_archived=True)
    assert all(r.get("id") != rid for r in remaining)
    # Shard dir: empty (the append never ran)
    archive_dir = re._reflections_archive_dir("小天")
    if os.path.exists(archive_dir):
        for fn in os.listdir(archive_dir):
            if fn.endswith(".json"):
                with open(os.path.join(archive_dir, fn), encoding="utf-8") as f:
                    data = json.load(f)
                assert all(e.get("id") != rid for e in data), (
                    "shard already contains the entry — test setup broken"
                )

    # Replay the state_changed event via the handler — must self-heal.
    events = _ev.read_since("小天", None)
    archive_evt = next(
        e for e in events
        if e["type"] == EVT_REFLECTION_STATE_CHANGED
        and e["payload"].get("reflection_id") == rid
    )
    assert archive_evt["payload"].get("entry_snapshot"), (
        "event payload missing entry_snapshot — handler can't self-heal"
    )

    handler = make_reflection_archive_handler(re)
    changed = handler("小天", archive_evt["payload"])
    assert changed is True, "self-heal must report a change"

    # Shard now contains the entry under the basename from payload
    shard_basename = archive_evt["payload"]["archive_shard_path"]
    shard_path = os.path.join(archive_dir, shard_basename)
    assert os.path.exists(shard_path), "self-healed shard file missing"
    with open(shard_path, encoding="utf-8") as f:
        shard_data = json.load(f)
    assert any(e.get("id") == rid for e in shard_data)

    # Replaying again is a no-op (idempotent)
    changed2 = handler("小天", archive_evt["payload"])
    assert changed2 is False
    with open(shard_path, encoding="utf-8") as f:
        shard_data2 = json.load(f)
    assert len(shard_data2) == len(shard_data), (
        "replay must NOT duplicate the entry"
    )

    # ── persona half (symmetric) ─────────────────────────────────────
    persona = await pm.aensure_persona("小天")
    persona.setdefault("master", {}).setdefault("facts", []).append({
        "id": "p_selfheal", "text": "p-lost-then-found",
        "source": "manual", "source_id": None,
        "protected": False,
    })
    await pm.asave_persona("小天", persona)

    with patch("memory.archive_shards.aappend_to_shard", _shard_boom):
        with pytest.raises(RuntimeError, match="simulated shard write failure"):
            await pm.aarchive_persona_entry("小天", "master", "p_selfheal")

    persona = await pm.aensure_persona("小天")
    facts = persona.get("master", {}).get("facts", [])
    assert all(f.get("id") != "p_selfheal" for f in facts), (
        "active view should have lost the entry already"
    )

    p_events = _ev.read_since("小天", None)
    p_archive_evt = next(
        e for e in p_events
        if e["type"] == EVT_PERSONA_FACT_ADDED
        and e["payload"].get("entry_id") == "p_selfheal"
    )
    p_handler = make_persona_archive_handler(pm)
    p_changed = p_handler("小天", p_archive_evt["payload"])
    assert p_changed is True

    p_archive_dir = pm._persona_archive_dir("小天")
    p_shard_path = os.path.join(
        p_archive_dir, p_archive_evt["payload"]["archive_shard_path"],
    )
    assert os.path.exists(p_shard_path)
    with open(p_shard_path, encoding="utf-8") as f:
        p_shard_data = json.load(f)
    assert any(e.get("id") == "p_selfheal" for e in p_shard_data)

    # Idempotent replay
    assert p_handler("小天", p_archive_evt["payload"]) is False


# ── PR #934 round-3 regressions ─────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_handler_corrupt_shard_keeps_active_view(tmp_path):
    """Round-3 Major: when the named target shard is corrupt, the
    archive handler MUST NOT remove the entry from the active view.

    Prior bug: ``ensure_entry_in_named_shard_sync`` returned False on
    corruption (same as "already present"), the handler treated it as
    "shard ok, skip", and proceeded to drop the entry from the active
    view. If this replay was the very recovery from a "event committed
    but shard never written" crash window, the entry would now be
    gone from BOTH the view AND every shard — pure data loss.

    Fix: the helper raises ``ShardCorruptError``; both reflection and
    persona handlers catch it, log, leave the active view untouched,
    and return False. Operator can repair the shard and the next
    reconciler boot will retry.
    """
    from unittest.mock import patch
    from memory.archive_shards import ShardCorruptError
    from memory.event_log import (
        EVT_PERSONA_FACT_ADDED,
        EVT_REFLECTION_STATE_CHANGED,
    )
    from memory.evidence_handlers import (
        make_persona_archive_handler,
        make_reflection_archive_handler,
    )

    # ── reflection half ──────────────────────────────────────────────
    _ev, _fs, pm, re, _rec, _cm = _install(str(tmp_path))
    rid = "ref_corrupt_target"
    seed = [{
        "id": rid, "text": "must-survive", "entity": "master",
        "status": "confirmed", "source_fact_ids": [],
        "created_at": "2026-04-22T10:00:00",
        "feedback": None, "next_eligible_at": "2026-04-22T10:00:00",
    }]
    await re.asave_reflections("小天", seed)

    # Crash between record_and_save and the shard append (round-2
    # setup) — entry leaves the active view, shard never receives it.
    async def _shard_boom(*a, **kw):
        raise RuntimeError("simulated shard write failure")

    with patch("memory.archive_shards.aappend_to_shard", _shard_boom):
        with pytest.raises(RuntimeError, match="simulated shard write failure"):
            await re.aarchive_reflection("小天", rid)

    # Sanity: entry already removed from active view by record_and_save.
    remaining = await re.aload_reflections("小天", include_archived=True)
    assert all(r.get("id") != rid for r in remaining)

    # The archive handler reuses the active view as its "view to
    # mutate" — but the round-3 invariant is about a DIFFERENT replay
    # path: an entry the handler is about to drop from the view. To
    # exercise the corrupt-shard-aborts-view-mutation contract we need
    # an event whose `reflection_id` IS still in the view. Re-seed
    # that scenario directly: place the entry back in the active view
    # (simulating an operator recovery / snapshot restore that left
    # the on-disk state inconsistent), then replay the archive event
    # against a corrupt named shard.
    re_seed = list(remaining) + [seed[0]]
    await re.asave_reflections("小天", re_seed)
    after_reseed = await re.aload_reflections("小天", include_archived=True)
    assert any(r.get("id") == rid for r in after_reseed), (
        "test setup: re-seeded entry must be back in active view"
    )

    # Plant a corrupt file at the exact basename the event references.
    events = _ev.read_since("小天", None)
    archive_evt = next(
        e for e in events
        if e["type"] == EVT_REFLECTION_STATE_CHANGED
        and e["payload"].get("reflection_id") == rid
    )
    shard_basename = archive_evt["payload"]["archive_shard_path"]
    archive_dir = re._reflections_archive_dir("小天")
    os.makedirs(archive_dir, exist_ok=True)
    corrupt_shard_path = os.path.join(archive_dir, shard_basename)
    corrupt_payload = "{ broken json [["
    with open(corrupt_shard_path, "w", encoding="utf-8") as f:
        f.write(corrupt_payload)

    # Replay → handler must abort (return False), NOT touch view.
    handler = make_reflection_archive_handler(re)
    changed = handler("小天", archive_evt["payload"])
    assert changed is False, (
        "handler must report no change when target shard is corrupt"
    )

    # Active view intact.
    after_replay = await re.aload_reflections("小天", include_archived=True)
    assert any(r.get("id") == rid for r in after_replay), (
        "entry must remain in active view when shard is corrupt "
        "(round-3 Major regression)"
    )

    # Corrupt shard untouched on disk.
    with open(corrupt_shard_path, encoding="utf-8") as f:
        assert f.read() == corrupt_payload, (
            "corrupt shard must NOT be overwritten by self-heal"
        )

    # Direct check: helper raises ShardCorruptError (not returns False).
    from memory.archive_shards import ensure_entry_in_named_shard_sync
    snapshot = archive_evt["payload"]["entry_snapshot"]
    with pytest.raises(ShardCorruptError):
        ensure_entry_in_named_shard_sync(
            archive_dir, shard_basename, snapshot,
        )

    # ── persona half (symmetric) ─────────────────────────────────────
    persona = await pm.aensure_persona("小天")
    persona.setdefault("master", {}).setdefault("facts", []).append({
        "id": "p_corrupt_target", "text": "p-must-survive",
        "source": "manual", "source_id": None,
        "protected": False,
    })
    await pm.asave_persona("小天", persona)

    with patch("memory.archive_shards.aappend_to_shard", _shard_boom):
        with pytest.raises(RuntimeError, match="simulated shard write failure"):
            await pm.aarchive_persona_entry("小天", "master", "p_corrupt_target")

    # Re-seed the persona entry so the handler has something to drop.
    persona = await pm.aensure_persona("小天")
    persona.setdefault("master", {}).setdefault("facts", []).append({
        "id": "p_corrupt_target", "text": "p-must-survive",
        "source": "manual", "source_id": None,
        "protected": False,
    })
    await pm.asave_persona("小天", persona)

    p_events = _ev.read_since("小天", None)
    p_archive_evt = next(
        e for e in p_events
        if e["type"] == EVT_PERSONA_FACT_ADDED
        and e["payload"].get("entry_id") == "p_corrupt_target"
    )
    p_shard_basename = p_archive_evt["payload"]["archive_shard_path"]
    p_archive_dir = pm._persona_archive_dir("小天")
    os.makedirs(p_archive_dir, exist_ok=True)
    p_corrupt_path = os.path.join(p_archive_dir, p_shard_basename)
    with open(p_corrupt_path, "w", encoding="utf-8") as f:
        f.write(corrupt_payload)

    p_handler = make_persona_archive_handler(pm)
    p_changed = p_handler("小天", p_archive_evt["payload"])
    assert p_changed is False, (
        "persona handler must report no change when target shard is corrupt"
    )

    persona_after = await pm.aensure_persona("小天")
    facts_after = persona_after.get("master", {}).get("facts", [])
    assert any(f.get("id") == "p_corrupt_target" for f in facts_after), (
        "persona entry must remain in active view when shard is corrupt "
        "(round-3 Major regression)"
    )
    with open(p_corrupt_path, encoding="utf-8") as f:
        assert f.read() == corrupt_payload, (
            "corrupt persona shard must NOT be overwritten"
        )


def test_archive_stamper_cross_day_retry_realigns_archived_at(tmp_path):
    """Round-3 Minor: when shard write fails on day-1 and
    save_reflections rolls the entries back into main, a day-2 retry
    must restamp BOTH ``archived_at`` and ``archive_shard_path`` with
    day-2's values. Earlier ``setdefault('archived_at', now_iso)`` left
    yesterday's timestamp glued to a today's-shard basename — the two
    fields disagreed on the date.

    This test exercises ``_make_archive_stamper`` directly (the bug is
    pure stamper semantics; mocking the full save retry across days
    requires monkeypatching ``datetime.now`` inside both reflection.py
    and archive_shards.py which is brittle).
    """
    from memory.reflection import ReflectionEngine

    day1_iso = "2026-04-22T10:00:00"
    day2_iso = "2026-04-23T10:00:00"
    day1_basename = "2026-04-22_aaaa1111.json"
    day2_basename = "2026-04-23_bbbb2222.json"

    entry = {"id": "e1", "text": "x"}

    # Day-1 stamp (would have been written but shard append "failed";
    # entry rolled back into main with stamps attached).
    stamper_day1 = ReflectionEngine._make_archive_stamper(day1_iso)
    stamper_day1([entry], day1_basename)
    assert entry["archived_at"] == day1_iso
    assert entry["archive_shard_path"] == day1_basename

    # Day-2 retry: a NEW stamper restamps the same dict object.
    stamper_day2 = ReflectionEngine._make_archive_stamper(day2_iso)
    stamper_day2([entry], day2_basename)
    assert entry["archived_at"] == day2_iso, (
        "archived_at must reflect day-2 retry (round-3 Minor regression: "
        "setdefault stuck at day-1)"
    )
    assert entry["archive_shard_path"] == day2_basename
    # Internal consistency: both fields agree on the date prefix.
    assert entry["archived_at"].startswith("2026-04-23")
    assert entry["archive_shard_path"].startswith("2026-04-23")


def _persona_cache_safety_initial(entry_id: str, initial_text: str) -> dict:
    return {
        "master": {
            "facts": [{
                "id": entry_id,
                "text": initial_text,
                "reinforcement": 1.0,
                "disputation": 0.0,
                "rein_last_signal_at": None,
                "disp_last_signal_at": None,
                "sub_zero_days": 0,
                "user_fact_reinforce_count": 0,
                "source": "test",
                "merged_from_ids": [],
                "protected": False,
            }],
        },
    }


@pytest.mark.asyncio
async def test_persona_sync_save_evicts_cache_on_atomic_write_failure(tmp_path):
    """Round-5 Major #1 + Round-6 Major #3: if the save step inside
    `_sync_save_persona_view` fails AFTER `_sync_mutate_view` already
    mutated the cached persona (record_and_save contract:
    mutate-then-save), the in-memory cache would be left polluted with
    the merged state while disk still sits at pre-event. Subsequent
    in-process reads would serve the stale/polluted state.

    Fix: `_sync_save_persona_view` wraps the WHOLE save step
    (cloudsave gate + atomic_write_json) in a try/except that evicts
    the dirty cache entry on any failure. Next `_aensure_persona_locked`
    reloads from disk (pre-event state). The event is already appended
    to the log (append runs before mutate per event_log.record_and_save),
    so reconciler replay on next boot restores the mutation correctly.

    Round-6 Major #3 tightening: the bomb MUST land on the SECOND
    persona.json write — that is, on EVT_PERSONA_ENTRY_UPDATED's save.
    `amerge_into` emits EVT_PERSONA_EVIDENCE_UPDATED FIRST as a no-op
    mutate (RFC §3.9.6 + round-4 fix); its `_sync_mutate_view` is a
    pass so even if its save fails the cache would NOT be polluted.
    The pollution window is event-2 (entry_updated) where
    `_sync_mutate_entry` rewrites text/evidence/merged_from_ids in
    place on the cached dict. Bombing on the FIRST save would never
    exercise the actual round-5 fix — just walk the no-op path.

    Covers all persona paths that go through the helper:
    aapply_signal, amerge_into (2 events), aincrement_sub_zero,
    aarchive_persona_entry. The shared helper makes the guarantee
    uniform across the others.
    """
    _ev, _fs, pm, _re, _rec, _cm = _install(str(tmp_path))

    entry_id = "p_cache_safety"
    initial_text = "original — pre-merge"
    await pm.asave_persona(
        "小天", _persona_cache_safety_initial(entry_id, initial_text),
    )
    # Prime the in-memory cache by loading once.
    before = await pm.aget_persona("小天")
    assert before["master"]["facts"][0]["text"] == initial_text

    # Round-6 Major #3: bomb on the SECOND persona.json save (the
    # entry_updated save, which is the one whose mutate ACTUALLY
    # touched the cached dict). The first save is for evidence_updated
    # whose mutate is a documented no-op (round-4 crash-safety fix);
    # bombing it would not exercise the round-5 cache-eviction window.
    from memory.persona import atomic_write_json as _real_atomic

    persona_write_count = {"n": 0}

    def _boom_on_second_persona_write(path, *args, **kwargs):
        if str(path).endswith("persona.json"):
            persona_write_count["n"] += 1
            if persona_write_count["n"] >= 2:
                raise RuntimeError(
                    "simulated persona.json write failure on entry_updated"
                )
        return _real_atomic(path, *args, **kwargs)

    with patch("memory.persona.atomic_write_json",
               side_effect=_boom_on_second_persona_write):
        with pytest.raises(RuntimeError, match="simulated persona.json"):
            await pm.amerge_into(
                "小天", entry_id, "merged — post-failure",
                reflection_evidence={
                    'reinforcement': 3.0, 'disputation': 0.0,
                },
                source_reflection_id="ref_boom", merged_from_ids=["ref_boom"],
            )

    # Sanity: we hit the entry_updated save (not a stray write count).
    assert persona_write_count["n"] == 2, (
        f"expected 2 persona.json writes (evidence_updated then "
        f"entry_updated); got {persona_write_count['n']} — the bomb may "
        f"have fired on the wrong event, missing the pollution window"
    )

    # Cache must have been EVICTED — subsequent read re-loads from disk.
    # The first save (evidence_updated, no-op mutate) succeeded; its
    # event-driven evidence values DID land on disk. The second save
    # (entry_updated, in-place mutate of text + merged_from_ids)
    # failed — the cache must NOT carry that polluted text/sentinel.
    after = await pm.aget_persona("小天")
    entry_after = after["master"]["facts"][0]
    assert entry_after["text"] == initial_text, (
        f"cache leaked merged text despite entry_updated save failure "
        f"(round-5 Major #1 + round-6 Major #3 regression): "
        f"got {entry_after['text']!r}"
    )
    assert entry_after["merged_from_ids"] == [], (
        "cache leaked merged_from_ids despite entry_updated save failure "
        "— this is the idempotency sentinel; if leaked the retry would "
        "no-op and lose the merge entirely"
    )

    # Sanity: a retry with atomic_write restored must now succeed,
    # proving the entry wasn't somehow already-merged in memory.
    r = await pm.amerge_into(
        "小天", entry_id, "merged — retry succeeds",
        reflection_evidence={'reinforcement': 3.0, 'disputation': 0.0},
        source_reflection_id="ref_boom", merged_from_ids=["ref_boom"],
    )
    assert r == "merged"
    final = await pm.aget_persona("小天")
    assert final["master"]["facts"][0]["text"] == "merged — retry succeeds"


@pytest.mark.asyncio
async def test_persona_sync_save_evicts_cache_on_cloudsave_gate_raise(tmp_path):
    """Round-6 Major #1: the cache-eviction try/except must also wrap
    `assert_cloudsave_writable`, not just `atomic_write_json`. If
    cloudsave flips to read-only mid-flight (e.g. quota hit between
    the mutate and the save), the gate raises BEFORE the atomic_write —
    but `_sync_mutate_entry` has already polluted the cached dict. The
    old try-block scope only caught atomic_write failures, so the
    gate-raise path would leak the polluted cache.

    Symmetric assertion to the atomic_write test above, exercising the
    second persona.json save (entry_updated) for the same reason: the
    pollution window is event-2 in `amerge_into`.
    """
    _ev, _fs, pm, _re, _rec, _cm = _install(str(tmp_path))

    entry_id = "p_cache_safety_cs"
    initial_text = "original — pre-merge (cloudsave path)"
    await pm.asave_persona(
        "小天", _persona_cache_safety_initial(entry_id, initial_text),
    )
    before = await pm.aget_persona("小天")
    assert before["master"]["facts"][0]["text"] == initial_text

    # Bomb the cloudsave gate on the SECOND call only (let the
    # evidence_updated save through; round-6 Major #3 tightening).
    gate_call_count = {"n": 0}

    def _boom_on_second_cloudsave(_cm, *, operation: str, target: str):
        if target.endswith("persona.json") and operation == "save":
            gate_call_count["n"] += 1
            if gate_call_count["n"] >= 2:
                raise RuntimeError(
                    "simulated cloudsave readonly on entry_updated save"
                )

    with patch("memory.persona.assert_cloudsave_writable",
               side_effect=_boom_on_second_cloudsave):
        with pytest.raises(RuntimeError, match="simulated cloudsave"):
            await pm.amerge_into(
                "小天", entry_id, "merged — cloudsave failed",
                reflection_evidence={
                    'reinforcement': 3.0, 'disputation': 0.0,
                },
                source_reflection_id="ref_cs", merged_from_ids=["ref_cs"],
            )

    assert gate_call_count["n"] == 2, (
        f"expected 2 cloudsave gate calls (evidence_updated then "
        f"entry_updated); got {gate_call_count['n']} — the bomb may "
        f"have fired on the wrong event"
    )

    # Cache must have been EVICTED — same invariant as the atomic_write
    # variant: cloudsave-gate raise on the polluting save must not
    # leave merged text / sentinel in memory.
    after = await pm.aget_persona("小天")
    entry_after = after["master"]["facts"][0]
    assert entry_after["text"] == initial_text, (
        f"cache leaked merged text despite cloudsave-gate raise on "
        f"entry_updated (round-6 Major #1 regression): "
        f"got {entry_after['text']!r}"
    )
    assert entry_after["merged_from_ids"] == [], (
        "cache leaked merged_from_ids despite cloudsave-gate raise — "
        "the eviction try/except must wrap assert_cloudsave_writable, "
        "not just atomic_write_json"
    )


# ── Round-7 Major: extend eviction-on-save-failure to the public
# `save_persona` / `asave_persona` paths (used by add_fact,
# ensure_persona's character-card sync, and other non-event writers).
# Round-5/6 only fixed `_sync_save_persona_view` (event-sourced path);
# the public save methods kept the legacy "cache-then-save" order, so
# any save-step failure left `self._personas[name]` polluted with
# state that never landed on disk.


def _persona_round7_baseline() -> dict:
    """Baseline persona that fits the `aget_persona` reload path."""
    return {
        "master": {
            "facts": [{
                "id": "p_round7_baseline",
                "text": "baseline disk text",
                "reinforcement": 0.5,
                "disputation": 0.0,
                "rein_last_signal_at": None,
                "disp_last_signal_at": None,
                "sub_zero_days": 0,
                "user_fact_reinforce_count": 0,
                "source": "test",
                "merged_from_ids": [],
                "protected": False,
            }],
        },
    }


def _persona_round7_polluted() -> dict:
    """The dict the caller WANTS to save — must NOT remain in cache
    after a save-step failure."""
    return {
        "master": {
            "facts": [{
                "id": "p_round7_baseline",
                "text": "polluted in-memory text",
                "reinforcement": 9.0,
                "disputation": 0.0,
                "rein_last_signal_at": None,
                "disp_last_signal_at": None,
                "sub_zero_days": 0,
                "user_fact_reinforce_count": 0,
                "source": "test",
                "merged_from_ids": ["polluted_ref"],
                "protected": False,
            }],
        },
    }


def test_save_persona_evicts_cache_on_atomic_write_failure(tmp_path):
    """Round-7 Major: sync `save_persona` must evict polluted cache
    when `atomic_write_json` raises. Otherwise `add_fact()` /
    `ensure_persona()`'s character-card sync would leave stale state
    in memory until restart.
    """
    _ev, _fs, pm, _re, _rec, _cm = _install(str(tmp_path))

    # Persist a clean baseline to disk first.
    pm.save_persona("小天", _persona_round7_baseline())
    assert pm.ensure_persona("小天")["master"]["facts"][0]["text"] == \
        "baseline disk text"

    from memory.persona import atomic_write_json as _real_atomic

    def _boom(path, *args, **kwargs):
        if str(path).endswith("persona.json"):
            raise RuntimeError("simulated atomic_write_json failure")
        return _real_atomic(path, *args, **kwargs)

    with patch("memory.persona.atomic_write_json", side_effect=_boom):
        with pytest.raises(RuntimeError, match="simulated atomic_write_json"):
            pm.save_persona("小天", _persona_round7_polluted())

    # Cache MUST be evicted — next ensure re-reads disk (still baseline).
    after = pm.ensure_persona("小天")
    entry_after = after["master"]["facts"][0]
    assert entry_after["text"] == "baseline disk text", (
        f"cache leaked polluted text despite atomic_write failure "
        f"(round-7 Major regression on save_persona): "
        f"got {entry_after['text']!r}"
    )
    assert entry_after["merged_from_ids"] == [], (
        "cache leaked merged_from_ids despite save failure on save_persona"
    )


def test_save_persona_evicts_cache_on_cloudsave_gate_raise(tmp_path):
    """Round-7 Major: sync `save_persona` must also evict cache when
    the cloudsave gate raises (e.g. cloudsave flipped to read-only
    between cache assignment and atomic_write). Symmetric to the
    atomic_write variant above.
    """
    _ev, _fs, pm, _re, _rec, _cm = _install(str(tmp_path))

    pm.save_persona("小天", _persona_round7_baseline())
    pm.ensure_persona("小天")  # prime cache

    def _boom_gate(_cm, *, operation: str, target: str):
        if target.endswith("persona.json") and operation == "save":
            raise RuntimeError("simulated cloudsave readonly")

    with patch("memory.persona.assert_cloudsave_writable",
               side_effect=_boom_gate):
        with pytest.raises(RuntimeError, match="simulated cloudsave"):
            pm.save_persona("小天", _persona_round7_polluted())

    after = pm.ensure_persona("小天")
    entry_after = after["master"]["facts"][0]
    assert entry_after["text"] == "baseline disk text", (
        f"cache leaked polluted text despite cloudsave-gate raise on "
        f"save_persona (round-7 Major regression): "
        f"got {entry_after['text']!r}"
    )


@pytest.mark.asyncio
async def test_asave_persona_evicts_cache_on_atomic_write_failure(tmp_path):
    """Round-7 Major (async twin): `asave_persona` must evict polluted
    cache when `atomic_write_json_async` raises. Covers the path used
    by `aadd_fact` and `_aensure_persona_locked`'s character-card sync.
    """
    _ev, _fs, pm, _re, _rec, _cm = _install(str(tmp_path))

    await pm.asave_persona("小天", _persona_round7_baseline())
    before = await pm.aget_persona("小天")
    assert before["master"]["facts"][0]["text"] == "baseline disk text"

    from memory.persona import atomic_write_json_async as _real_atomic_a

    async def _boom_async(path, *args, **kwargs):
        if str(path).endswith("persona.json"):
            raise RuntimeError("simulated atomic_write_json_async failure")
        return await _real_atomic_a(path, *args, **kwargs)

    with patch("memory.persona.atomic_write_json_async", side_effect=_boom_async):
        with pytest.raises(RuntimeError, match="simulated atomic_write_json_async"):
            await pm.asave_persona("小天", _persona_round7_polluted())

    after = await pm.aget_persona("小天")
    entry_after = after["master"]["facts"][0]
    assert entry_after["text"] == "baseline disk text", (
        f"cache leaked polluted text despite atomic_write_async failure "
        f"on asave_persona (round-7 Major regression): "
        f"got {entry_after['text']!r}"
    )
    assert entry_after["merged_from_ids"] == [], (
        "cache leaked merged_from_ids despite save failure on asave_persona"
    )


@pytest.mark.asyncio
async def test_asave_persona_evicts_cache_on_cloudsave_gate_raise(tmp_path):
    """Round-7 Major (async twin): `asave_persona` must evict cache
    when the cloudsave gate raises before `atomic_write_json_async`.
    """
    _ev, _fs, pm, _re, _rec, _cm = _install(str(tmp_path))

    await pm.asave_persona("小天", _persona_round7_baseline())
    await pm.aget_persona("小天")  # prime cache

    def _boom_gate(_cm, *, operation: str, target: str):
        if target.endswith("persona.json") and operation == "save":
            raise RuntimeError("simulated cloudsave readonly (async)")

    with patch("memory.persona.assert_cloudsave_writable",
               side_effect=_boom_gate):
        with pytest.raises(RuntimeError, match="simulated cloudsave"):
            await pm.asave_persona("小天", _persona_round7_polluted())

    after = await pm.aget_persona("小天")
    entry_after = after["master"]["facts"][0]
    assert entry_after["text"] == "baseline disk text", (
        f"cache leaked polluted text despite cloudsave-gate raise on "
        f"asave_persona (round-7 Major regression): "
        f"got {entry_after['text']!r}"
    )
