# -*- coding: utf-8 -*-
"""
make_archive_copy_check / shard_entry_index_sync: which archive events the
journal compaction must keep because the journal may hold the only copy.

Archival appends to the shard AFTER the event has advanced the sentinel and
callers only log a failed append, so the replay self-heal never runs for
it. Reflection and persona are checked by one table; every case here runs
for both sides.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from memory.event_log import EVT_PERSONA_FACT_ADDED, EVT_REFLECTION_STATE_CHANGED

T1 = "2026-09-01T10:00:00.000001"
T2 = "2026-10-01T10:00:00.000002"


def _reflection_event(entry_id: str, snapshot: dict | None, *, shard: str = "2026-09-01_aaaaaaaa.json",
                      to: str = "archived") -> dict:
    payload = {"reflection_id": entry_id, "from": "confirmed", "to": to,
               "archive_shard_path": shard}
    if snapshot is not None:
        payload["entry_snapshot"] = snapshot
    return {"event_id": f"ev-{entry_id}", "type": EVT_REFLECTION_STATE_CHANGED,
            "ts": T1, "payload": payload}


def _persona_event(entry_id: str, snapshot: dict | None, *, shard: str | None = "2026-09-01_aaaaaaaa.json",
                   to: str = "archived") -> dict:
    payload = {"entity_key": "master", "entry_id": entry_id, "text": "t", "source": "manual"}
    if shard is not None and to == "archived":
        payload["archive_shard_path"] = shard
    if snapshot is not None:
        payload["entry_snapshot"] = snapshot
    return {"event_id": f"ev-{entry_id}", "type": EVT_PERSONA_FACT_ADDED,
            "ts": T1, "payload": payload}


SIDES = [
    pytest.param(("reflection_archive", _reflection_event), id="reflection"),
    pytest.param(("persona_archive", _persona_event), id="persona"),
]
OTHER = {"reflection_archive": "persona_archive", "persona_archive": "reflection_archive"}


def _entry(entry_id: str, archived_at: str | None = T1, **extra) -> dict:
    entry = {"id": entry_id, "text": f"text of {entry_id}", **extra}
    if archived_at is not None:
        entry["archived_at"] = archived_at
    return entry


def _write_shard(char_dir: str, dirname: str, filename: str, content) -> None:
    d = os.path.join(char_dir, dirname)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, filename), "w", encoding="utf-8") as f:
        if isinstance(content, str):
            f.write(content)
        else:
            json.dump(content, f)


def _check(char_dir: str):
    from memory.evidence_handlers import make_archive_copy_check

    return make_archive_copy_check(char_dir)


@pytest.mark.parametrize("side", SIDES)
def test_copy_in_the_named_shard_can_go(tmp_path, side):
    dirname, make_event = side
    char_dir = str(tmp_path)
    _write_shard(char_dir, dirname, "2026-09-01_aaaaaaaa.json", [_entry("x")])
    assert _check(char_dir)(make_event("x", _entry("x"))) is False


@pytest.mark.parametrize("side", SIDES)
def test_copy_in_another_shard_of_the_same_dir_can_go(tmp_path, side):
    """Overflow can land an append in a different same-day shard."""
    dirname, make_event = side
    char_dir = str(tmp_path)
    _write_shard(char_dir, dirname, "2026-09-01_aaaaaaaa.json", [_entry("other")])
    _write_shard(char_dir, dirname, "2026-09-01_bbbbbbbb.json", [_entry("x")])
    assert _check(char_dir)(make_event("x", _entry("x"))) is False


@pytest.mark.parametrize("side", SIDES)
def test_same_id_with_another_archived_at_is_kept(tmp_path, side):
    """H1: restore -> re-archive -> lost shard append. The shards still hold
    the FIRST archival of the id; the newer one exists only in the journal."""
    dirname, make_event = side
    char_dir = str(tmp_path)
    _write_shard(char_dir, dirname, "2026-09-01_aaaaaaaa.json", [_entry("x", T1)])
    check = _check(char_dir)
    assert check(make_event("x", _entry("x", T1))) is False
    assert check(make_event("x", _entry("x", T2, text="newer text"))) is True


@pytest.mark.parametrize("side", SIDES)
def test_without_archived_at_the_whole_entry_is_compared(tmp_path, side):
    dirname, make_event = side
    char_dir = str(tmp_path)
    _write_shard(char_dir, dirname, "2026-09-01_aaaaaaaa.json",
                 [_entry("x", None, score=1)])
    check = _check(char_dir)
    assert check(make_event("x", _entry("x", None, score=1))) is False
    assert check(make_event("x", _entry("x", None, score=2))) is True


@pytest.mark.parametrize("side", SIDES)
def test_entry_missing_from_every_shard_is_kept(tmp_path, side):
    dirname, make_event = side
    char_dir = str(tmp_path)
    _write_shard(char_dir, dirname, "2026-09-01_aaaaaaaa.json", [_entry("other")])
    assert _check(char_dir)(make_event("x", _entry("x"))) is True


@pytest.mark.parametrize("side", SIDES)
def test_missing_archive_dir_keeps_the_event_and_is_not_created(tmp_path, side):
    dirname, make_event = side
    char_dir = str(tmp_path)
    assert _check(char_dir)(make_event("x", _entry("x"))) is True
    assert not os.path.exists(os.path.join(char_dir, dirname))
    assert not os.path.exists(os.path.join(char_dir, OTHER[dirname]))


@pytest.mark.parametrize("bad", ["{not json", '{"a": 1}'])
@pytest.mark.parametrize("side", SIDES)
def test_unreadable_shard_keeps_every_archive_event_of_that_side_only(tmp_path, side, bad):
    dirname, make_event = side
    char_dir = str(tmp_path)
    _write_shard(char_dir, dirname, "2026-09-01_aaaaaaaa.json", [_entry("x")])
    _write_shard(char_dir, dirname, "2026-09-02_cccccccc.json", bad)
    # The other side is healthy and must not be affected.
    _write_shard(char_dir, OTHER[dirname], "2026-09-01_aaaaaaaa.json", [_entry("y")])
    other_event = (_persona_event if dirname == "reflection_archive" else _reflection_event)
    check = _check(char_dir)
    assert check(make_event("x", _entry("x"))) is True
    assert check(other_event("y", _entry("y"))) is False


@pytest.mark.parametrize("side", SIDES)
def test_event_without_snapshot_follows_normal_rules(tmp_path, side):
    _, make_event = side
    assert _check(str(tmp_path))(make_event("x", None)) is False


@pytest.mark.parametrize("side", SIDES)
def test_restore_events_are_not_archive_events(tmp_path, side):
    """Restore events carry entry_snapshot too, but they are not archivals."""
    _, make_event = side
    assert _check(str(tmp_path))(make_event("x", _entry("x"), to="confirmed")) is False


@pytest.mark.parametrize("side", SIDES)
def test_other_event_types_follow_normal_rules(tmp_path, side):
    _, make_event = side
    event = make_event("x", _entry("x"))
    event["type"] = "reflection.evidence_updated"
    assert _check(str(tmp_path))(event) is False


@pytest.mark.parametrize("side", SIDES)
def test_each_side_is_indexed_at_most_once(tmp_path, side):
    dirname, make_event = side
    char_dir = str(tmp_path)
    _write_shard(char_dir, dirname, "2026-09-01_aaaaaaaa.json", [_entry("x")])
    from memory import archive_shards

    real = archive_shards.shard_entry_index_sync
    with patch.object(archive_shards, "shard_entry_index_sync", side_effect=real) as spy:
        check = _check(char_dir)
        for _ in range(5):
            check(make_event("x", _entry("x")))
            check(make_event("z", _entry("z")))
    assert spy.call_count == 1


def test_shard_index_ignores_the_migration_sentinel_and_foreign_files(tmp_path):
    from memory.archive_shards import (
        MIGRATION_SENTINEL_FILENAME,
        archive_copy_fingerprint,
        shard_entry_index_sync,
    )

    d = str(tmp_path / "reflection_archive")
    os.makedirs(d)
    for name, content in [
        (MIGRATION_SENTINEL_FILENAME, "{not json"),
        ("notes.json", "{not json"),
        ("2026-09-01_zzzzzzzz.json", "{not json"),
        ("2026-09-01_aaaaaaaa.json.tmp", "{not json"),
        ("2026-09-01_aaaaaaaa.json", json.dumps([_entry("x"), "junk", {"no_id": 1}])),
    ]:
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(content)

    index = shard_entry_index_sync(d)
    assert index == {"x": frozenset({archive_copy_fingerprint(_entry("x"))})}


def test_shard_index_of_a_missing_dir_is_empty_and_creates_nothing(tmp_path):
    from memory.archive_shards import shard_entry_index_sync

    d = str(tmp_path / "persona_archive")
    assert shard_entry_index_sync(d) == {}
    assert not os.path.exists(d)


def test_archive_dir_names_match_the_manager_getters(tmp_path):
    """The check builds paths without the getters (they makedirs); the names
    must still be the ones the managers write to."""
    from unittest.mock import MagicMock

    from memory.archive_shards import PERSONA_ARCHIVE_DIRNAME, REFLECTION_ARCHIVE_DIRNAME
    from memory.persona.persistence import PersistenceMixin as PersonaPersistence
    from memory.reflection.persistence import PersistenceMixin as ReflectionPersistence

    holder = MagicMock()
    holder._config_manager.memory_dir = str(tmp_path)
    assert ReflectionPersistence._reflections_archive_dir(holder, "c") == \
        os.path.join(str(tmp_path), "c", REFLECTION_ARCHIVE_DIRNAME)
    assert PersonaPersistence._persona_archive_dir(holder, "c") == \
        os.path.join(str(tmp_path), "c", PERSONA_ARCHIVE_DIRNAME)
