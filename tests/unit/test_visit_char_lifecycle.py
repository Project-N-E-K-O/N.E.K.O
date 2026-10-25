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

"""Visit data across a character rename / delete: markers, migration and retirement (OD-13, PR-09b)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import pytest

from main_logic.visit import char_lifecycle as lc
from main_logic.visit.recovery import visit_spool_recovery
from main_logic.visit.spool import VisitSpool
from main_logic.visit.subjects import (
    PeerRoster,
    RosterCorruptError,
    add_roster_marker_item,
    read_roster_marker,
    remove_roster_marker_item,
    set_roster_marker,
)
from tests.unit.visit_memory_test_helpers import (
    CHAR_UID_A,
    CHAR_UID_B,
    OWN_A,
    OWN_B,
    PEER_X,
    PEER_Y,
    TAG_Y,
    FakeMemoryServer,
    ln,
    make_visit,
    seed_roster,
    vid,
)


def _peers(tmp_path) -> dict:
    return json.loads((tmp_path / "visit_peers.json").read_text(encoding="utf-8"))


def _loader(names, uid_of):
    async def load():
        return set(names), dict(uid_of) if uid_of is not None else None

    return load


async def _state_char(tmp_path, visit_id) -> str:
    return (await VisitSpool(tmp_path, visit_id).read_state())["own_char"]


async def _header_char(tmp_path, visit_id) -> str:
    return (await VisitSpool(tmp_path, visit_id).read_header())["own_char"]


class Personas:
    def __init__(self, tmp_path: Path, fail: bool = False):
        self.dir = tmp_path / "visit_persona"
        self.calls: list[str] = []
        self.fail = fail

    def seed(self, uid: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{uid}.json"
        path.write_text("{}", encoding="utf-8")
        return path

    async def __call__(self, uid: str) -> bool:
        self.calls.append(uid)
        if self.fail:
            raise OSError("persona locked")
        path = self.dir / f"{uid}.json"
        existed = path.exists()
        path.unlink(missing_ok=True)
        return existed


# ── Marker primitives ──────────────────────────────────────────────────────────


async def test_set_marker_only_when_absent_and_keeps_partitions(tmp_path):
    await seed_roster(tmp_path)
    before = _peers(tmp_path)["accounts"]
    assert await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "B"}) == (True, None)
    # Existing marker: return without overwriting; a new rename must not displace an unreconciled earlier rename.
    assert await set_roster_marker(tmp_path, "pending_rename", {"old": "C", "new": "D"}) == (
        False, {"old": "A", "new": "B"})
    data = _peers(tmp_path)
    assert data["pending_rename"] == {"old": "A", "new": "B"} and data["accounts"] == before


async def test_a_null_marker_is_a_free_slot_not_a_successful_write(tmp_path):
    (tmp_path / "visit_peers.json").write_text(json.dumps({"pending_rename": None}), encoding="utf-8")
    # Mutation must fail: treating null as an existing marker collides with successful-write return / occupies the slot forever.
    assert await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "B"}) == (True, None)
    assert _peers(tmp_path)["pending_rename"] == {"old": "A", "new": "B"}


async def test_retire_marker_items_are_added_and_removed_one_by_one(tmp_path):
    a, b = lc.retire_item("A", CHAR_UID_A), lc.retire_item("B", CHAR_UID_B)
    assert await add_roster_marker_item(tmp_path, "pending_retire", a)
    assert not await add_roster_marker_item(tmp_path, "pending_retire", a)
    assert await add_roster_marker_item(tmp_path, "pending_retire", b)
    assert await remove_roster_marker_item(tmp_path, "pending_retire", a)
    assert _peers(tmp_path)["pending_retire"] == [b]
    assert await remove_roster_marker_item(tmp_path, "pending_retire", b)
    assert "pending_retire" not in _peers(tmp_path)
    assert not await remove_roster_marker_item(tmp_path, "pending_retire", b)


async def test_retire_marker_that_is_not_a_list_is_roster_damage(tmp_path):
    (tmp_path / "visit_peers.json").write_text(json.dumps({"pending_retire": {"name": "A"}}), encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await add_roster_marker_item(tmp_path, "pending_retire", lc.retire_item("B", CHAR_UID_B))


async def test_roster_retire_char_walks_every_account(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    await seed_roster(tmp_path, own_char="B")
    await seed_roster(tmp_path, own_uid=OWN_B, own_char="A")
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="A")
    assert await PeerRoster(tmp_path, own_uid="x").retire_char("A") == 3
    accounts = _peers(tmp_path)["accounts"]
    # Retain this person's entries under other local characters; delete a peer row entirely if only this character remains.
    assert set(accounts[OWN_A]["peers"][PEER_X]["by_char"]) == {"B"}
    assert PEER_Y not in accounts[OWN_A]["peers"]
    assert accounts[OWN_B]["peers"] == {}
    assert await PeerRoster(tmp_path, own_uid="x").retire_char("A") == 0


async def test_roster_retire_char_fails_closed_on_a_damaged_partition(tmp_path):
    (tmp_path / "visit_peers.json").write_text(
        json.dumps({"accounts": {OWN_A: {"peers": {PEER_X: {"by_char": []}}}}}), encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await PeerRoster(tmp_path, own_uid="x").retire_char("A")


# ── Rename ──────────────────────────────────────────────────────────────


async def test_rename_without_any_visit_data_writes_nothing(tmp_path):
    assert await lc.begin_rename(tmp_path, "A", "A2", CHAR_UID_A, names={"A"}, uid_of={"A": CHAR_UID_A}) is None
    assert not (tmp_path / "visit_peers.json").exists()


async def _visit_data(tmp_path):
    roster = await seed_roster(tmp_path, own_char="A")
    pair = (await roster.get_char_entry(PEER_X, "A"))["pairs"][0]
    await roster.set_last_summary(PEER_X, "A", visit_id=vid(1), ended_at=5.0, text="聊了天气", pair_id=pair)
    await make_visit(tmp_path, vid(1), [ln(1)])
    await make_visit(tmp_path, vid(2), [ln(1)], own_char="B", own_char_uid=CHAR_UID_B)
    return roster


async def test_committed_rename_moves_roster_and_spools_then_clears_the_marker(tmp_path):
    roster = await _visit_data(tmp_path)
    marker = await lc.begin_rename(tmp_path, "A", "A2", CHAR_UID_A, names={"A", "B"},
                                   uid_of={"A": CHAR_UID_A, "B": CHAR_UID_B})
    assert marker == {"old": "A", "new": "A2", "uid": CHAR_UID_A}
    assert _peers(tmp_path)["pending_rename"] == marker
    # Transaction commits: this uid is now named A2 in configuration.
    assert await lc.settle_rename(tmp_path, marker, _loader({"A2", "B"}, {"A2": CHAR_UID_A, "B": CHAR_UID_B}))
    assert await roster.get_char_entry(PEER_X, "A") is None
    assert (await roster.get_last_summary(PEER_X, "A2"))["text"] == "聊了天气"
    assert await _header_char(tmp_path, vid(1)) == "A2" and await _state_char(tmp_path, vid(1)) == "A2"
    assert await _state_char(tmp_path, vid(2)) == "B"
    assert "pending_rename" not in _peers(tmp_path)


async def test_rolled_back_rename_moves_nothing_and_clears_the_marker(tmp_path):
    roster = await _visit_data(tmp_path)
    marker = await lc.begin_rename(tmp_path, "A", "A2", CHAR_UID_A, names={"A"}, uid_of={"A": CHAR_UID_A})
    assert await lc.settle_rename(tmp_path, marker, _loader({"A"}, {"A": CHAR_UID_A}))
    assert await roster.get_char_entry(PEER_X, "A") is not None
    assert await _state_char(tmp_path, vid(1)) == "A"
    assert "pending_rename" not in _peers(tmp_path)


async def test_rolled_back_rename_never_claims_residue_under_the_new_name(tmp_path):
    roster = await _visit_data(tmp_path)
    # New name has old data from a deleted character (characters deleted before this PR were not retired).
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="A2")
    await make_visit(tmp_path, vid(3), [ln(1)], own_char="A2", own_char_uid=CHAR_UID_B)
    marker = await lc.begin_rename(tmp_path, "A", "A2", CHAR_UID_A, names={"A"}, uid_of={"A": CHAR_UID_A})
    assert await lc.settle_rename(tmp_path, marker, _loader({"A"}, {"A": CHAR_UID_A}))
    # Mutation must fail: ordinary reverse rewriting on rollback would wrongly assign residue to A.
    assert await roster.get_char_entry(PEER_Y, "A") is None
    assert await roster.get_char_entry(PEER_Y, "A2") is not None
    assert await _state_char(tmp_path, vid(3)) == "A2"
    assert "pending_rename" not in _peers(tmp_path)


async def test_failed_migration_keeps_the_marker_for_startup_recovery(tmp_path, monkeypatch):
    roster = await _visit_data(tmp_path)
    marker = await lc.begin_rename(tmp_path, "A", "A2", CHAR_UID_A, names={"A"}, uid_of={"A": CHAR_UID_A})
    original = VisitSpool.rename_own_char

    async def busy(*_a, **_k):
        raise OSError("spool locked")

    monkeypatch.setattr(VisitSpool, "rename_own_char", busy)
    load = _loader({"A2"}, {"A2": CHAR_UID_A})
    assert not await lc.settle_rename(tmp_path, marker, load)
    assert _peers(tmp_path)["pending_rename"] == marker
    monkeypatch.setattr(VisitSpool, "rename_own_char", original)
    # Startup reconciliation completes using the same rules.
    assert await lc.reconcile_rename(tmp_path, {"A2"}, {"A2": CHAR_UID_A}) == frozenset()
    assert await roster.get_char_entry(PEER_X, "A2") is not None
    assert await _state_char(tmp_path, vid(1)) == "A2"
    assert "pending_rename" not in _peers(tmp_path)


async def test_an_earlier_marker_is_reconciled_before_the_next_rename(tmp_path):
    roster = await _visit_data(tmp_path)
    # Previous A -> A2 committed, but migration is incomplete.
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "A2", "uid": CHAR_UID_A})
    names, uid_of = {"A2", "B"}, {"A2": CHAR_UID_A, "B": CHAR_UID_B}
    marker = await lc.begin_rename(tmp_path, "B", "B2", CHAR_UID_B, names=names, uid_of=uid_of)
    assert marker == {"old": "B", "new": "B2", "uid": CHAR_UID_B}
    assert await roster.get_char_entry(PEER_X, "A2") is not None
    assert await _state_char(tmp_path, vid(1)) == "A2"


async def test_an_unresolvable_earlier_marker_refuses_the_rename(tmp_path):
    await _visit_data(tmp_path)
    stale = {"old": "A", "new": "A2"}
    await set_roster_marker(tmp_path, "pending_rename", stale)
    # Both names exist: direction is ambiguous; retain the old marker and reject this rename (overwriting the old marker mutation must fail).
    with pytest.raises(lc.RenamePendingElsewhere):
        await lc.begin_rename(tmp_path, "B", "B2", CHAR_UID_B, names={"A", "A2", "B"}, uid_of=None)
    assert _peers(tmp_path)["pending_rename"] == stale


# ── Deletion retirement ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("target", ["visit_peers.json", "persona"])
async def test_unstattable_visit_files_count_as_visit_data(tmp_path, monkeypatch, target):
    import os

    blocked = tmp_path / ("visit_peers.json" if target != "persona" else f"visit_persona/{CHAR_UID_A}.json")
    real_stat = os.stat

    def stat(path, *a, **k):
        if Path(path) == blocked:
            raise PermissionError("access denied")
        return real_stat(path, *a, **k)

    monkeypatch.setattr(lc.os, "stat", stat)
    # Failed stat must not mean no visit data: still write marker (here roster is unreadable; marker write reports corruption and transaction rejects).
    assert await lc.has_visit_data(tmp_path, CHAR_UID_A) is True


async def test_delete_without_any_visit_data_writes_nothing(tmp_path):
    assert await lc.begin_retire(tmp_path, "A", CHAR_UID_A) is None
    assert not (tmp_path / "visit_peers.json").exists()


async def test_persona_alone_counts_as_visit_data(tmp_path):
    personas = Personas(tmp_path)
    personas.seed(CHAR_UID_A)
    item = await lc.begin_retire(tmp_path, "A", CHAR_UID_A)
    assert item == {"name": "A", "character_uid": CHAR_UID_A}
    assert await lc.settle_retire(tmp_path, item, names=set(), uid_of={}, retire_persona=personas)
    assert personas.calls == [CHAR_UID_A] and not (personas.dir / f"{CHAR_UID_A}.json").exists()
    assert "pending_retire" not in _peers(tmp_path)


async def _retire_fixture(tmp_path):
    await _visit_data(tmp_path)
    await seed_roster(tmp_path, own_uid=OWN_B, own_char="A")
    spool_dir = tmp_path / "visit_spool"
    upload = spool_dir / f"{vid(1)}.upload.json"
    upload.write_text("{}", encoding="utf-8")
    reports = tmp_path / "visit_reports"
    reports.mkdir()
    (reports / f"{vid(1)}.json").write_text("{}", encoding="utf-8")
    personas = Personas(tmp_path)
    personas.seed(CHAR_UID_A)
    personas.seed(CHAR_UID_B)
    return spool_dir, upload, reports, personas


async def test_committed_delete_retires_spools_roster_and_persona(tmp_path):
    spool_dir, upload, reports, personas = await _retire_fixture(tmp_path)
    item = await lc.begin_retire(tmp_path, "A", CHAR_UID_A)
    assert _peers(tmp_path)["pending_retire"] == [item]
    assert await lc.settle_retire(tmp_path, item, names={"B"}, uid_of={"B": CHAR_UID_B},
                                  retire_persona=personas)
    assert not (spool_dir / f"{vid(1)}.jsonl").exists() and not (spool_dir / f"{vid(1)}.state.json").exists()
    assert (spool_dir / f"{vid(2)}.state.json").exists()                  # Leave other characters' visits unchanged.
    assert upload.exists() and (reports / f"{vid(1)}.json").exists()     # Preserve billing and report evidence.
    data = _peers(tmp_path)
    assert PEER_X not in data["accounts"][OWN_A]["peers"]                # Delete a peer row entirely if only A remains.
    assert data["accounts"][OWN_B]["peers"] == {}
    assert personas.calls == [CHAR_UID_A] and (personas.dir / f"{CHAR_UID_B}.json").exists()
    assert "pending_retire" not in data


async def test_rolled_back_delete_only_drops_the_marker(tmp_path):
    spool_dir, _upload, _reports, personas = await _retire_fixture(tmp_path)
    item = await lc.begin_retire(tmp_path, "A", CHAR_UID_A)
    # Deletion rolls back: uid remains configured (mutation must fail if retired without checking config).
    assert await lc.settle_retire(tmp_path, item, names={"A", "B"},
                                  uid_of={"A": CHAR_UID_A, "B": CHAR_UID_B}, retire_persona=personas)
    assert (spool_dir / f"{vid(1)}.state.json").exists()
    assert PEER_X in _peers(tmp_path)["accounts"][OWN_A]["peers"]
    assert personas.calls == []
    assert "pending_retire" not in _peers(tmp_path)


async def test_failed_retirement_keeps_the_item_and_replay_finishes_it(tmp_path):
    spool_dir, _upload, _reports, personas = await _retire_fixture(tmp_path)
    item = await lc.begin_retire(tmp_path, "A", CHAR_UID_A)
    personas.fail = True
    assert not await lc.settle_retire(tmp_path, item, names={"B"}, uid_of={"B": CHAR_UID_B},
                                      retire_persona=personas)
    # Continue other independent steps and retain marker (removing marker despite failure must fail); name-based roster cleanup
    # finished, so record roster_done (omitting it must fail).
    assert not (spool_dir / f"{vid(1)}.state.json").exists()
    assert _peers(tmp_path)["pending_retire"] == [{**item, "roster_done": True}]
    assert await lc.is_name_retiring(tmp_path, "A") and not await lc.is_name_retiring(tmp_path, "B")
    # Another character later claims this name (cloud snapshot import bypasses name checks); retries must not erase its entries.
    # (Mutation must fail if retries repeat name-based roster cleanup.)
    newcomer = await seed_roster(tmp_path, own_uid=OWN_B, peer_uid=PEER_Y, tag=TAG_Y, own_char="A")
    assert not await lc.replay_retires(tmp_path, _loader({"B"}, {"B": CHAR_UID_B}), retire_persona=personas)
    assert await newcomer.get_char_entry(PEER_Y, "A") is not None
    personas.fail = False
    assert await lc.replay_retires(tmp_path, _loader({"B"}, {"B": CHAR_UID_B}), retire_persona=personas)
    assert "pending_retire" not in _peers(tmp_path)
    assert not await lc.is_name_retiring(tmp_path, "A")


async def test_one_failing_retirement_step_does_not_stop_the_others(tmp_path):
    spool_dir, _upload, _reports, personas = await _retire_fixture(tmp_path)
    item = await lc.begin_retire(tmp_path, "A", CHAR_UID_A)
    data = _peers(tmp_path)
    data["accounts"][OWN_B]["peers"][PEER_X]["by_char"] = ["damaged"]
    (tmp_path / "visit_peers.json").write_text(json.dumps(data), encoding="utf-8")
    # Corrupt roster partition: this step fails and marker stays, but visits/persona still retire (stopping on first error must fail).
    assert not await lc.settle_retire(tmp_path, item, names={"B"}, uid_of={"B": CHAR_UID_B},
                                      retire_persona=personas)
    assert not (spool_dir / f"{vid(1)}.state.json").exists()
    assert personas.calls == [CHAR_UID_A]
    assert _peers(tmp_path)["pending_retire"] == [item]


def _legacy_spool(tmp_path, visit_id, own_char):
    """A spool of an older version: header only, no ``own_char_uid``."""
    path = tmp_path / "visit_spool" / f"{visit_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps({"v": 1, "visit_id": visit_id, "own_char": own_char}).encode() + b"\n")
    return path


@pytest.mark.parametrize("name_reused", [False, True])
async def test_retirement_matches_by_uid_and_legacy_files_by_name(tmp_path, name_reused):
    await _visit_data(tmp_path)
    # Visits sharing the name but with different uid belong to another character; legacy visits without own_char_uid use the name.
    await make_visit(tmp_path, vid(3), [ln(1)], own_char="A", own_char_uid=CHAR_UID_B)
    legacy = _legacy_spool(tmp_path, vid(4), "A")
    item = await lc.begin_retire(tmp_path, "A", CHAR_UID_A)
    names = {"A", "B"} if name_reused else {"B"}
    uid_of = {"A": "e" * 32, "B": CHAR_UID_B} if name_reused else {"B": CHAR_UID_B}
    assert await lc.settle_retire(tmp_path, item, names=names, uid_of=uid_of, retire_persona=None)
    spool_dir = tmp_path / "visit_spool"
    assert not (spool_dir / f"{vid(1)}.state.json").exists()
    assert (spool_dir / f"{vid(3)}.state.json").exists()
    # Do not fall back to name when a new character owns it (legacy visit ownership is ambiguous).
    assert legacy.exists() is name_reused


async def test_malformed_retire_items_are_dropped(tmp_path):
    (tmp_path / "visit_peers.json").write_text(
        json.dumps({"pending_retire": [{"name": ""}, "A", {"name": "B", "character_uid": CHAR_UID_B}]}),
        encoding="utf-8")
    personas = Personas(tmp_path)
    assert await lc.replay_retires(tmp_path, _loader(set(), {}), retire_persona=personas)
    assert "pending_retire" not in _peers(tmp_path)
    assert personas.calls == [CHAR_UID_B]


async def test_replay_settles_each_item_under_the_config_lock(tmp_path):
    await _retire_fixture(tmp_path)
    await lc.begin_retire(tmp_path, "A", CHAR_UID_A)
    lock = asyncio.Lock()
    seen: list[bool] = []

    async def persona(uid):
        seen.append(lock.locked())

    assert await lc.replay_retires(tmp_path, _loader({"B"}, {"B": CHAR_UID_B}), retire_persona=persona,
                                   config_lock=lambda: lock)
    # Mutation must fail if check / retirement skips locking (races with deletion's marker-written, config-uncommitted interval).
    assert seen == [True]


async def test_replay_skips_an_item_settled_while_waiting_for_the_lock(tmp_path):
    await _retire_fixture(tmp_path)
    item = await lc.begin_retire(tmp_path, "A", CHAR_UID_A)
    lock = asyncio.Lock()
    calls: list[str] = []

    async def persona(uid):
        calls.append(uid)

    await lock.acquire()
    replay = asyncio.ensure_future(lc.replay_retires(
        tmp_path, _loader({"B"}, {"B": CHAR_UID_B}), retire_persona=persona, config_lock=lambda: lock))
    await asyncio.sleep(0.02)
    # While waiting for the lock, another path handled this entry and created a same-name character already visiting.
    await remove_roster_marker_item(tmp_path, "pending_retire", item)
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="A")
    lock.release()
    assert await replay is True
    # Mutation must fail if retirement uses the pre-lock snapshot (it would delete the new character's roster entries).
    assert await PeerRoster(tmp_path, own_uid=OWN_A).get_char_entry(PEER_Y, "A") is not None
    assert calls == []


async def test_unreadable_roster_does_not_block_creating_a_character(tmp_path):
    (tmp_path / "visit_peers.json").write_text("{broken", encoding="utf-8")
    assert not await lc.is_name_retiring(tmp_path, "A")
    with pytest.raises(RosterCorruptError):
        await lc.begin_retire(tmp_path, "A", CHAR_UID_A)


# ── Startup reconciliation ──────────────────────────────────────────────────────────


@pytest.fixture
def _readable(monkeypatch):
    from main_logic.visit import local_chars

    async def readable():
        return None

    monkeypatch.setattr(local_chars, "ensure_characters_readable", readable)


async def _recover(tmp_path, names, mapping, **kw):
    async def chips(*_a, **_k):
        return False

    async def list_names():
        return list(names)

    async def resolve(uid):
        return mapping.get(uid)

    kw.setdefault("list_char_names", list_names)
    return await visit_spool_recovery(
        chips, None, config_dir=tmp_path, is_live=lambda _v: False, resolve_char_name=resolve,
        client=FakeMemoryServer().client(), **kw,
    )


async def test_startup_recovery_finishes_a_pending_retirement(tmp_path, _readable):
    spool_dir, _upload, _reports, personas = await _retire_fixture(tmp_path)
    await add_roster_marker_item(tmp_path, "pending_retire", lc.retire_item("A", CHAR_UID_A))
    report = await _recover(tmp_path, ["B"], {CHAR_UID_B: "B"}, retire_persona=personas)
    assert report.retired is True
    assert not (spool_dir / f"{vid(1)}.state.json").exists()
    assert PEER_X not in _peers(tmp_path)["accounts"][OWN_A]["peers"]
    assert personas.calls == [CHAR_UID_A]
    assert await read_roster_marker(tmp_path, "pending_retire") is None


async def test_startup_recovery_keeps_a_retirement_that_still_fails(tmp_path, _readable):
    await _retire_fixture(tmp_path)
    item = lc.retire_item("A", CHAR_UID_A)
    await add_roster_marker_item(tmp_path, "pending_retire", item)
    report = await _recover(tmp_path, ["B"], {CHAR_UID_B: "B"}, retire_persona=Personas(tmp_path, fail=True))
    assert report.retired is False
    assert await read_roster_marker(tmp_path, "pending_retire") == [{**item, "roster_done": True}]


async def test_startup_recovery_without_markers_reports_clean(tmp_path, _readable):
    await make_visit(tmp_path, vid(1), [ln(1)])
    report = await _recover(tmp_path, ["A"], {CHAR_UID_A: "A"})
    assert report.retired is True and report.renamed is True
    assert not (tmp_path / "visit_peers.json").exists()


# ── Name reservation (retirement / rename markers) ─────────────────────────────────────────


async def test_name_marker_state_covers_both_markers(tmp_path):
    assert await lc.name_marker_state(tmp_path, "A") == lc.NAME_FREE
    assert not (tmp_path / "visit_peers.json").exists()
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "A2", "uid": CHAR_UID_A})
    await add_roster_marker_item(tmp_path, "pending_retire", lc.retire_item("Gone", CHAR_UID_B))
    # Mutation must fail if only retirement markers count (both names in rename markers are reserved).
    assert await lc.name_marker_state(tmp_path, "A") == lc.NAME_RENAMING
    assert await lc.name_marker_state(tmp_path, "A2") == lc.NAME_RENAMING
    assert await lc.name_marker_state(tmp_path, "Gone") == lc.NAME_RETIRING
    assert await lc.name_marker_state(tmp_path, "B") == lc.NAME_FREE


async def test_name_marker_state_reads_every_marker_from_one_state_of_the_roster(tmp_path, monkeypatch):
    from main_logic.visit import subjects

    slot = {"old": "X", "new": "Y", "uid": CHAR_UID_B}
    queued = {"old": "A", "new": "A2", "uid": CHAR_UID_A}
    await set_roster_marker(tmp_path, "pending_rename", slot)
    await add_roster_marker_item(tmp_path, "pending_rename_queue", queued)
    real = subjects.read_roster_marker

    async def racing(config_dir, key):
        value = await real(config_dir, key)
        if key == "pending_rename":
            # Startup recovery clears the slot and promotes the queue head between two reads.
            await subjects.clear_roster_marker(config_dir, "pending_rename", slot)
            await subjects.promote_roster_marker(config_dir, "pending_rename", "pending_rename_queue")
        return value

    monkeypatch.setattr(lc, "read_roster_marker", racing)
    # Mutation must fail if three markers are read separately (old slot + now-empty queue wrongly marks queued A as free).
    assert await lc.name_marker_state(tmp_path, "A") == lc.NAME_RENAMING


async def test_name_marker_state_of_an_unreadable_roster_is_unknown_not_held(tmp_path):
    (tmp_path / "visit_peers.json").write_text("{broken", encoding="utf-8")
    assert await lc.name_marker_state(tmp_path, "A") == lc.NAME_UNREADABLE


# ── Reconcile this character's rename before deletion ──────────────────────────────────────────


def _counting_loader(names, uid_of):
    calls = []

    async def load():
        calls.append(True)
        return set(names), dict(uid_of)

    return load, calls


async def test_delete_settles_its_own_pending_rename_before_retiring(tmp_path):
    roster = await _visit_data(tmp_path)
    # A -> A2 committed with incomplete migration; now delete A2.
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "A2", "uid": CHAR_UID_A})
    load, _calls = _counting_loader({"A2", "B"}, {"A2": CHAR_UID_A, "B": CHAR_UID_B})
    item = await lc.begin_retire(tmp_path, "A2", CHAR_UID_A, load_names=load)
    assert item == lc.retire_item("A2", CHAR_UID_A)
    # Complete migration first: no entries remain under old A, so retirement by A2 clears all (skipping reconciliation must fail).
    assert await roster.get_char_entry(PEER_X, "A") is None
    assert await roster.get_char_entry(PEER_X, "A2") is not None
    assert "pending_rename" not in _peers(tmp_path)
    assert await lc.settle_retire(tmp_path, item, names={"B"}, uid_of={"B": CHAR_UID_B},
                                  retire_persona=Personas(tmp_path))
    assert await roster.get_char_entry(PEER_X, "A") is None
    assert await roster.get_char_entry(PEER_X, "A2") is None


async def test_delete_is_refused_while_its_own_rename_cannot_be_settled(tmp_path, monkeypatch):
    roster = await _visit_data(tmp_path)
    marker = {"old": "A", "new": "A2", "uid": CHAR_UID_A}
    await set_roster_marker(tmp_path, "pending_rename", marker)

    async def busy(*_a, **_k):
        raise OSError("spool locked")

    monkeypatch.setattr(VisitSpool, "rename_own_char", busy)
    load, _calls = _counting_loader({"A2"}, {"A2": CHAR_UID_A})
    with pytest.raises(lc.RenamePendingElsewhere):
        await lc.begin_retire(tmp_path, "A2", CHAR_UID_A, load_names=load)
    assert _peers(tmp_path)["pending_rename"] == marker
    assert "pending_retire" not in _peers(tmp_path)
    # Roster migration (before spool) completed idempotently, but marker remains; restart reconciliation completes it.
    assert await roster.get_char_entry(PEER_X, "A2") is not None


async def test_delete_is_refused_when_its_own_rename_marker_is_unreadable(tmp_path):
    await _visit_data(tmp_path)
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "A2", "uid": CHAR_UID_A})

    async def broken():
        raise OSError("characters.json locked")

    with pytest.raises(lc.RenamePendingElsewhere):
        await lc.begin_retire(tmp_path, "A2", CHAR_UID_A, load_names=broken)


async def test_delete_goes_ahead_when_its_rename_is_ambiguous_for_good(tmp_path):
    await _visit_data(tmp_path)
    marker = {"old": "A", "new": "A2", "uid": CHAR_UID_A}
    await set_roster_marker(tmp_path, "pending_rename", marker)
    # Another character now owns old A: entries cannot be separated; blocking deletion would deadlock both characters.
    load, _calls = _counting_loader({"A", "A2"}, {"A": CHAR_UID_B, "A2": CHAR_UID_A})
    assert await lc.begin_retire(tmp_path, "A2", CHAR_UID_A, load_names=load) == lc.retire_item("A2", CHAR_UID_A)
    assert _peers(tmp_path)["pending_rename"] == marker
    # Once A2 is deleted, clear its marker as deleted.
    assert await lc.reconcile_rename(tmp_path, {"A"}, {"A": CHAR_UID_B}) == frozenset()


async def test_delete_of_another_character_ignores_the_pending_rename(tmp_path):
    await _visit_data(tmp_path)
    marker = {"old": "A", "new": "A2", "uid": CHAR_UID_A}
    await set_roster_marker(tmp_path, "pending_rename", marker)
    load, calls = _counting_loader({"A2", "B"}, {"A2": CHAR_UID_A, "B": CHAR_UID_B})
    assert await lc.begin_retire(tmp_path, "B", CHAR_UID_B, load_names=load) == lc.retire_item("B", CHAR_UID_B)
    assert calls == [] and _peers(tmp_path)["pending_rename"] == marker


async def test_a_rename_marker_without_uid_involves_the_character_by_name(tmp_path):
    await _visit_data(tmp_path)
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "A2"})
    load, calls = _counting_loader({"A2"}, {"A2": CHAR_UID_A})
    await lc.begin_retire(tmp_path, "A2", CHAR_UID_A, load_names=load)
    assert calls == [True] and "pending_rename" not in _peers(tmp_path)


# ── Characters deleted by cloud snapshot import ──────────────────────────────────────────────


def test_import_removal_without_visit_data_writes_nothing(tmp_path):
    assert lc.record_removed_characters_sync(tmp_path, [{"name": "A", "character_uid": CHAR_UID_A}]) == 0
    assert not (tmp_path / "visit_peers.json").exists()


async def test_import_removal_records_one_retire_item_per_character(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    removed = [
        {"name": "A", "character_uid": CHAR_UID_A},
        {"name": "Legacy", "character_uid": None},
        {"name": "", "character_uid": CHAR_UID_B},           # Skip invalid entries.
        {"name": "Bad", "character_uid": 7},
    ]
    assert lc.record_removed_characters_sync(tmp_path, removed) == 2
    assert _peers(tmp_path)["pending_retire"] == [lc.retire_item("A", CHAR_UID_A), lc.retire_item("Legacy", None)]
    # Repeated import after prior partial failure must not record duplicates.
    assert lc.record_removed_characters_sync(tmp_path, removed[:1]) == 0
    assert len(_peers(tmp_path)["pending_retire"]) == 2


async def test_import_removal_is_retired_by_startup_recovery(tmp_path, _readable):
    spool_dir, _upload, _reports, personas = await _retire_fixture(tmp_path)
    lc.record_removed_characters_sync(tmp_path, [{"name": "A", "character_uid": CHAR_UID_A}])
    report = await _recover(tmp_path, ["B"], {CHAR_UID_B: "B"}, retire_persona=personas)
    assert report.retired is True
    assert not (spool_dir / f"{vid(1)}.state.json").exists()
    assert PEER_X not in _peers(tmp_path)["accounts"][OWN_A]["peers"]
    assert personas.calls == [CHAR_UID_A]
    assert await read_roster_marker(tmp_path, "pending_retire") is None


async def test_import_removal_that_rolled_back_is_only_dropped(tmp_path, _readable):
    spool_dir, _upload, _reports, personas = await _retire_fixture(tmp_path)
    lc.record_removed_characters_sync(tmp_path, [{"name": "A", "character_uid": CHAR_UID_A}])
    # Import rolls back: A remains configured.
    report = await _recover(tmp_path, ["A", "B"], {CHAR_UID_A: "A", CHAR_UID_B: "B"}, retire_persona=personas)
    assert report.retired is True and personas.calls == []
    assert (spool_dir / f"{vid(1)}.state.json").exists()
    assert await read_roster_marker(tmp_path, "pending_retire") is None


# ── Rename reconciliation: third name / target still retiring ─────────────────────────


async def _data_under_both_names(tmp_path):
    """Roster and spool entries of ``CHAR_UID_A`` under both ``A`` and ``A2`` (a migration cut halfway)."""
    roster = await _visit_data(tmp_path)
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="A2")
    await make_visit(tmp_path, vid(3), [ln(1)], own_char="A2", own_char_uid=CHAR_UID_A)
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "A2", "uid": CHAR_UID_A})
    return roster


async def test_a_rename_chained_to_a_third_name_moves_both_old_names(tmp_path):
    roster = await _data_under_both_names(tmp_path)
    # Before A -> A2 reconciliation finishes, cloud snapshot renames the uid to A3 (occupied marker slot prevented another record).
    unsettled = await lc.reconcile_rename(tmp_path, {"A3", "B"}, {"A3": CHAR_UID_A, "B": CHAR_UID_B})
    # Mutation must fail without third-name handling (permanent ambiguity; entries under both old names never migrate).
    assert unsettled == frozenset()
    assert await roster.get_char_entry(PEER_X, "A") is None and await roster.get_char_entry(PEER_Y, "A2") is None
    assert await roster.get_char_entry(PEER_X, "A3") is not None
    assert await roster.get_char_entry(PEER_Y, "A3") is not None
    assert await _state_char(tmp_path, vid(1)) == "A3" and await _state_char(tmp_path, vid(3)) == "A3"
    assert await _state_char(tmp_path, vid(2)) == "B"
    assert "pending_rename" not in _peers(tmp_path)


async def test_a_chained_rename_whose_old_name_is_reused_stays_ambiguous(tmp_path):
    roster = await _data_under_both_names(tmp_path)
    # Another character reused old A: roster stores only names, so migrating would steal its entries (removing reuse check must fail).
    unsettled = await lc.reconcile_rename(tmp_path, {"A3", "A"}, {"A3": CHAR_UID_A, "A": CHAR_UID_B})
    assert unsettled == frozenset({"A", "A2"})
    assert await roster.get_char_entry(PEER_X, "A") is not None
    assert await roster.get_char_entry(PEER_X, "A3") is None
    assert _peers(tmp_path)["pending_rename"] == {"old": "A", "new": "A2", "uid": CHAR_UID_A}


async def test_a_rename_waits_while_its_target_name_is_still_retiring(tmp_path):
    roster = await _visit_data(tmp_path)
    marker = {"old": "A", "new": "A2", "uid": CHAR_UID_A}
    await set_roster_marker(tmp_path, "pending_rename", marker)
    await add_roster_marker_item(tmp_path, "pending_retire", lc.retire_item("A2", CHAR_UID_B))
    # Migrating into A2 before its name-based retirement would erase migrated entries: wait for retirement (not waiting must fail).
    assert await lc.reconcile_rename(tmp_path, {"A2"}, {"A2": CHAR_UID_A}) == frozenset({"A", "A2"})
    assert await roster.get_char_entry(PEER_X, "A") is not None
    assert await _state_char(tmp_path, vid(1)) == "A"
    assert _peers(tmp_path)["pending_rename"] == marker
    # Retiring names unrelated to the rename do not block migration.
    await remove_roster_marker_item(tmp_path, "pending_retire", lc.retire_item("A2", CHAR_UID_B))
    await add_roster_marker_item(tmp_path, "pending_retire", lc.retire_item("Gone", CHAR_UID_B))
    assert await lc.reconcile_rename(tmp_path, {"A2"}, {"A2": CHAR_UID_A}) == frozenset()
    assert await roster.get_char_entry(PEER_X, "A2") is not None


async def test_startup_recovery_retires_the_target_name_before_the_rename_moves_into_it(
    tmp_path, _readable, monkeypatch,
):
    from main_logic.visit import local_chars

    async def configured():
        return {"A2": CHAR_UID_A}

    # Production reads config by uid (distinguishes same-name retirement entries with different uid).
    monkeypatch.setattr(local_chars, "load_local_characters", configured)
    roster = await _visit_data(tmp_path)
    # One cloud snapshot import deletes old character A2 (uid B) and renames uid A from A to A2.
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="A2")
    await make_visit(tmp_path, vid(3), [ln(1)], own_char="A2", own_char_uid=CHAR_UID_B)
    await add_roster_marker_item(tmp_path, "pending_retire", lc.retire_item("A2", CHAR_UID_B))
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "A2", "uid": CHAR_UID_A})
    report = await _recover(tmp_path, ["A2"], {CHAR_UID_A: "A2"}, retire_persona=Personas(tmp_path),
                            list_char_names=None)
    # Mutation must fail if rename reconciles first (migrated entries are retired or rename waits forever).
    assert report.retired is True and report.renamed is True
    assert await roster.get_char_entry(PEER_Y, "A2") is None                 # Old character's residue retired.
    assert await roster.get_char_entry(PEER_X, "A2") is not None             # Entries migrated by rename remain.
    assert await _state_char(tmp_path, vid(1)) == "A2"
    assert not (tmp_path / "visit_spool" / f"{vid(3)}.state.json").exists()
    data = _peers(tmp_path)
    assert "pending_retire" not in data and "pending_rename" not in data


async def test_startup_recovery_drains_the_queued_renames_of_a_cloudsave_import(tmp_path, _readable, monkeypatch):
    from main_logic.visit import local_chars

    async def configured():
        return {"A2": CHAR_UID_A, "B2": CHAR_UID_B}

    monkeypatch.setattr(local_chars, "load_local_characters", configured)
    roster = await seed_roster(tmp_path, own_char="A")
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="B")
    await make_visit(tmp_path, vid(2), [ln(1)], own_char="B", own_char_uid=CHAR_UID_B)
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "A2", "uid": CHAR_UID_A})
    gone = {"old": "G", "new": "G2", "uid": "f" * 32}
    await add_roster_marker_item(tmp_path, "pending_rename_queue", gone)
    await add_roster_marker_item(tmp_path, "pending_rename_queue", {"old": "B", "new": "B2", "uid": CHAR_UID_B})
    report = await _recover(tmp_path, ["A2", "B2"], {}, list_char_names=None)
    # Mutation must fail if startup ignores the queue (B's entries stay under its old name).
    assert report.renamed is True
    assert await roster.get_char_entry(PEER_X, "A2") is not None
    assert await roster.get_char_entry(PEER_Y, "B2") is not None and await roster.get_char_entry(PEER_Y, "B") is None
    assert await _state_char(tmp_path, vid(2)) == "B2"
    data = _peers(tmp_path)
    assert "pending_rename" not in data and "pending_rename_queue" not in data


async def test_startup_recovery_drops_a_deleted_characters_queued_rename_behind_a_stuck_slot(
    tmp_path, _readable, monkeypatch,
):
    from main_logic.visit import local_chars

    async def configured():
        return {"A2": CHAR_UID_A, "X": CHAR_UID_B}

    monkeypatch.setattr(local_chars, "load_local_characters", configured)
    await seed_roster(tmp_path, own_char="A")
    # Active-slot rename is permanently ambiguous (another character owns new X): queued entries never get their turn.
    stuck = {"old": "A", "new": "X", "uid": CHAR_UID_A}
    await set_roster_marker(tmp_path, "pending_rename", stuck)
    await add_roster_marker_item(tmp_path, "pending_rename_queue", {"old": "G", "new": "G2", "uid": "f" * 32})
    await _recover(tmp_path, ["A2", "X"], {}, list_char_names=None)
    # Queued rename for a deleted character need not wait: remove directly and release G / G2 (not removing must fail).
    data = _peers(tmp_path)
    assert data["pending_rename"] == stuck and "pending_rename_queue" not in data
    assert await lc.name_marker_state(tmp_path, "G") == lc.NAME_FREE


async def test_forget_refuses_names_held_by_a_queued_or_chained_rename(tmp_path):
    from main_logic.visit.forget_runner import RenamePending, _refuse_pending_rename

    await set_roster_marker(tmp_path, "pending_rename", {"old": "X", "new": "Y", "uid": CHAR_UID_B, "chained": ["Z"]})
    await add_roster_marker_item(tmp_path, "pending_rename_queue", {"old": "A", "new": "A2", "uid": CHAR_UID_A})
    # Queued rename is also unreconciled: config says A2, entries remain under A, so erasing A2 would 'succeed' without deleting anything.
    # (Checking only the active slot must fail); include chained names too (ignoring chained must fail).
    for name in ("A2", "A", "Z", "Y"):
        with pytest.raises(RenamePending):
            await _refuse_pending_rename(tmp_path, [name])
    await _refuse_pending_rename(tmp_path, ["B"])


async def test_startup_recovery_skips_the_names_of_renames_still_queued(tmp_path, _readable, monkeypatch):
    from main_logic.visit import local_chars, recovery

    async def configured():
        return {"A2": CHAR_UID_A, "X": CHAR_UID_B, "Q": "e" * 32}

    monkeypatch.setattr(local_chars, "load_local_characters", configured)
    skipped = []

    async def recover_visit(_visit_id, *, skip_names, **_kw):
        skipped.append(skip_names)

    monkeypatch.setattr(recovery, "_recover_visit", recover_visit)
    await make_visit(tmp_path, vid(1), [ln(1)])
    # Active-slot rename is permanently ambiguous; another character's X -> Q waits behind it (shared X requires waiting): Q has no entries yet, all are under X.
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "X", "uid": CHAR_UID_A})
    await add_roster_marker_item(tmp_path, "pending_rename_queue", {"old": "X", "new": "Q", "uid": "e" * 32})
    report = await _recover(tmp_path, ["A2", "X", "Q"], {}, list_char_names=None)
    # Mutation must fail if only active-slot names are skipped (Q's per-visit recovery finds nothing under the new name and marks complete).
    assert report.renamed is False
    assert skipped and {"A", "X", "Q"} <= set(skipped[0])


# ── Cloud snapshot rename rollback, intermediate chained names, retirement with roster cleanup complete ─────────────────────


async def test_a_rolled_back_cloudsave_rename_is_cleared_without_moving_anything_back(tmp_path):
    # Snapshot would rename uid A from A to B and delete original B; import rolls back, leaving both characters.
    roster = await seed_roster(tmp_path, own_char="A")
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="B")
    await make_visit(tmp_path, vid(3), [ln(1)], own_char="B", own_char_uid=CHAR_UID_B)
    marker = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE}
    await set_roster_marker(tmp_path, "pending_rename", marker)
    assert await lc.reconcile_rename(tmp_path, {"A", "B"}, {"A": CHAR_UID_A, "B": CHAR_UID_B}) == frozenset()
    # Mutation must fail if ordinary reverse rename runs (B's real entries and visits move into A).
    assert await roster.get_char_entry(PEER_Y, "B") is not None
    assert await roster.get_char_entry(PEER_Y, "A") is None
    assert await _state_char(tmp_path, vid(3)) == "B"
    assert "pending_rename" not in _peers(tmp_path)


async def test_a_chained_rename_moves_the_entries_under_every_intermediate_name(tmp_path):
    roster = await seed_roster(tmp_path, own_char="A")
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="C")
    await make_visit(tmp_path, vid(3), [ln(1)], own_char="C", own_char_uid=CHAR_UID_A)
    # Before A -> B reconciles, character also used C and is now D: entries created while named C also belong to it (migrating only old/new must fail).
    await set_roster_marker(tmp_path, "pending_rename",
                            {"old": "A", "new": "B", "uid": CHAR_UID_A, "chained": ["C", "D"]})
    assert await lc.reconcile_rename(tmp_path, {"D"}, {"D": CHAR_UID_A}) == frozenset()
    assert await roster.get_char_entry(PEER_X, "D") is not None
    assert await roster.get_char_entry(PEER_Y, "D") is not None and await roster.get_char_entry(PEER_Y, "C") is None
    assert await _state_char(tmp_path, vid(3)) == "D"


async def test_a_rename_does_not_wait_for_a_retirement_whose_roster_step_is_done(tmp_path):
    roster = await _visit_data(tmp_path)
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "A2", "uid": CHAR_UID_A})
    # Retiring A2 completed name-based roster cleanup; only uid-based steps remain (e.g. persona locked):
    # migrated entries will not be cleared again, so rename need not wait (waiting anyway must fail).
    await add_roster_marker_item(tmp_path, "pending_retire", {**lc.retire_item("A2", CHAR_UID_B), "roster_done": True})
    assert await lc.reconcile_rename(tmp_path, {"A2"}, {"A2": CHAR_UID_A}) == frozenset()
    assert await roster.get_char_entry(PEER_X, "A2") is not None


async def test_an_ambiguous_chained_rename_reports_every_name_it_holds(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    marker = {"old": "A", "new": "B", "uid": CHAR_UID_A, "chained": ["C"]}
    await set_roster_marker(tmp_path, "pending_rename", marker)
    # Character now named C, A reused by someone else: permanent ambiguity. Overwriting cloud download of C must still ask to delete one character first.
    # (Returning only old/new must fail.)
    unsettled = await lc.reconcile_rename(tmp_path, {"A", "C"}, {"A": CHAR_UID_B, "C": CHAR_UID_A})
    assert unsettled == frozenset({"A", "B", "C"})
    assert _peers(tmp_path)["pending_rename"] == marker


async def test_chained_names_of_a_rolled_back_import_are_ignored(tmp_path):
    roster = await _visit_data(tmp_path)
    # X -> A unreconciled; cloud import attempts rename to C (recorded in chained), deleting C's owner, but rolls back:
    # still named A, C still belongs to another character. Chained C no longer belongs to it (counting it must fail with permanent ambiguity).
    await set_roster_marker(tmp_path, "pending_rename",
                            {"old": "X", "new": "A", "uid": CHAR_UID_A, "chained": ["C"]})
    assert await lc.reconcile_rename(tmp_path, {"A", "C"}, {"A": CHAR_UID_A, "C": CHAR_UID_B}) == frozenset()
    assert await roster.get_char_entry(PEER_X, "A") is not None
    assert "pending_rename" not in _peers(tmp_path)


# ── Partly migrated cloud rename, ambiguity after roster retirement, roster reread after retirement replay ─────────────


async def test_a_half_migrated_cloudsave_rename_is_moved_back_when_the_character_returns_to_the_old_name(
    tmp_path, monkeypatch,
):
    roster = await seed_roster(tmp_path, own_char="A")
    await make_visit(tmp_path, vid(1), [ln(1)], own_char="A", own_char_uid=CHAR_UID_A)
    marker = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE}
    await set_roster_marker(tmp_path, "pending_rename", marker)
    original = VisitSpool.rename_own_char

    async def busy(*_a, **_k):
        raise OSError("spool locked")

    # Forward migration moved roster to B, then failed rewriting visits; retain marker.
    monkeypatch.setattr(VisitSpool, "rename_own_char", busy)
    with pytest.raises(OSError):
        await lc.reconcile_rename(tmp_path, {"B"}, {"B": CHAR_UID_A})
    monkeypatch.setattr(VisitSpool, "rename_own_char", original)
    assert await roster.get_char_entry(PEER_X, "B") is not None
    assert _peers(tmp_path)["pending_rename"] == {**marker, "migrated": True}
    # Later import renames back to A: migration already partly ran; cannot simply drop marker as 'import rollback, nothing migrated'.
    # (Not recording migrated must fail, leaving A's entries under B for a future character named B.)
    assert await lc.reconcile_rename(tmp_path, {"A"}, {"A": CHAR_UID_A}) == frozenset()
    assert await roster.get_char_entry(PEER_X, "A") is not None
    assert await roster.get_char_entry(PEER_X, "B") is None
    assert await _state_char(tmp_path, vid(1)) == "A"
    assert "pending_rename" not in _peers(tmp_path)


async def test_a_retirement_whose_roster_step_is_done_does_not_count_as_retiring(tmp_path):
    await _visit_data(tmp_path)
    item = lc.retire_item("A2", CHAR_UID_B)
    await add_roster_marker_item(tmp_path, "pending_retire", item)
    assert await lc.names_retiring(tmp_path, {"A2"}) is True
    # Name-based roster cleanup completed: rename no longer waits, and ambiguity checks must not report 'still retiring, retry later'.
    # (Counting it anyway must fail, returning VISIT_DATA_BUSY forever for permanent ambiguity.)
    await remove_roster_marker_item(tmp_path, "pending_retire", item)
    await add_roster_marker_item(tmp_path, "pending_retire", {**item, "roster_done": True})
    assert await lc.names_retiring(tmp_path, {"A2"}) is False


@pytest.mark.parametrize("with_lock", [True, False])
async def test_startup_recovery_rereads_the_characters_after_replaying_retirements(
    tmp_path, _readable, monkeypatch, with_lock,
):
    from main_logic.visit import local_chars

    restored = {"A2": CHAR_UID_A, "X": CHAR_UID_B}
    calls = []

    async def configured():
        calls.append(None)
        # Cloud download restores uid E under a third name R during retirement replay.
        return dict(restored) if len(calls) == 1 else {**restored, "R": "e" * 32}

    lock = asyncio.Lock()
    held = []

    @contextlib.asynccontextmanager
    async def config_lock():
        async with lock:
            held.append(None)
            yield

    monkeypatch.setattr(local_chars, "load_local_characters", configured)
    await seed_roster(tmp_path, own_char="A")
    roster = await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="P")
    stuck = {"old": "A", "new": "X", "uid": CHAR_UID_A}
    await set_roster_marker(tmp_path, "pending_rename", stuck)
    queued = {"old": "P", "new": "Q", "uid": "e" * 32}
    await add_roster_marker_item(tmp_path, "pending_rename_queue", queued)
    await _recover(tmp_path, ["A2", "X"], {}, list_char_names=None, config_lock=config_lock if with_lock else None)
    # Restored character's queued rename must not be discarded as deleted using the pre-replay roster: reconcile normally, moving entries under P
    # to current name R (not rereading must fail, leaving entries under P for a future character named P).
    assert await roster.get_char_entry(PEER_Y, "R") is not None
    assert await roster.get_char_entry(PEER_Y, "P") is None
    assert bool(held) is with_lock


# ── Queue: corrupt entries, reconcile other renames while active slot stalls ─────────────────────


async def test_malformed_queued_renames_are_dropped_and_stop_blocking(tmp_path, _readable, monkeypatch):
    from main_logic.visit import local_chars
    from main_logic.visit.forget_runner import _refuse_pending_rename

    async def configured():
        return {"A": CHAR_UID_A}

    monkeypatch.setattr(local_chars, "load_local_characters", configured)
    await seed_roster(tmp_path, own_char="A")
    good = {"old": "G", "new": "A", "uid": CHAR_UID_A}
    data = _peers(tmp_path)
    data["pending_rename_queue"] = [{"old": "A"}, good, "junk"]
    (tmp_path / "visit_peers.json").write_text(json.dumps(data), encoding="utf-8")
    report = await _recover(tmp_path, ["A"], {CHAR_UID_A: "A"}, list_char_names=None)
    # Queued entries missing old/new cannot reconcile: record diagnostics, remove, and continue (not removing must fail,
    # skipping erasure replay/recovery every startup and permanently rejecting erasure).
    assert report.renamed is True
    assert "pending_rename_queue" not in _peers(tmp_path)
    await _refuse_pending_rename(tmp_path, ["A"])


async def test_a_queue_that_is_not_a_list_is_dropped(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    data = _peers(tmp_path)
    data["pending_rename_queue"] = {"old": "A", "new": "B"}
    (tmp_path / "visit_peers.json").write_text(json.dumps(data), encoding="utf-8")
    # Mutation must fail if non-list queue is retained (promotion raises RosterCorruptError forever).
    assert await lc.reconcile_renames(tmp_path, {"A"}, {"A": CHAR_UID_A}) == frozenset()
    assert "pending_rename_queue" not in _peers(tmp_path)


async def test_an_unrelated_queued_rename_settles_behind_an_ambiguous_slot(tmp_path):
    roster = await seed_roster(tmp_path, own_char="A")
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="P")
    stuck = {"old": "A", "new": "X", "uid": CHAR_UID_A}
    await set_roster_marker(tmp_path, "pending_rename", stuck)
    queued = {"old": "P", "new": "Q", "uid": "e" * 32}
    await add_roster_marker_item(tmp_path, "pending_rename_queue", queued)
    uid_of = {"A2": CHAR_UID_A, "X": CHAR_UID_B, "Q": "e" * 32}
    # Active rename permanently ambiguous (another character owns X): rotate to queue tail and finish P -> Q normally (stopping at stalled slot must fail).
    assert await lc.reconcile_renames(tmp_path, set(uid_of), uid_of) == frozenset({"A", "X"})
    assert await roster.get_char_entry(PEER_Y, "Q") is not None and await roster.get_char_entry(PEER_Y, "P") is None
    assert await roster.get_char_entry(PEER_X, "A") is not None
    data = _peers(tmp_path)
    assert data["pending_rename"] == stuck and "pending_rename_queue" not in data


async def test_a_queued_rename_sharing_a_name_with_a_pending_one_waits_behind_it(tmp_path):
    roster = await seed_roster(tmp_path, own_char="B")
    # Chain: uid A's B -> C stalls (C belongs to another character, permanent ambiguity), then uid E renames D -> B.
    # B's entries still belong to uid A; migrating E first mixes characters' entries (ignoring shared names must fail).
    stuck = {"old": "B", "new": "C", "uid": CHAR_UID_A}
    await set_roster_marker(tmp_path, "pending_rename", stuck)
    await add_roster_marker_item(tmp_path, "pending_rename_queue", {"old": "D", "new": "B", "uid": "e" * 32})
    uid_of = {"A2": CHAR_UID_A, "C": CHAR_UID_B, "B": "e" * 32}
    assert await lc.reconcile_renames(tmp_path, set(uid_of), uid_of) == frozenset({"B", "C"})
    data = _peers(tmp_path)
    # Retain both entries in original order.
    assert data["pending_rename"] == stuck
    assert data["pending_rename_queue"] == [{"old": "D", "new": "B", "uid": "e" * 32}]
    assert await roster.get_char_entry(PEER_X, "B") is not None


async def test_dropping_malformed_queued_renames_keeps_the_well_formed_ones(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    good = {"old": "G", "new": "A", "uid": CHAR_UID_A}
    data = _peers(tmp_path)
    data["pending_rename_queue"] = [{"old": "A"}, good, "junk"]
    (tmp_path / "visit_peers.json").write_text(json.dumps(data), encoding="utf-8")
    # Mutation must fail if not cleared (occupied slot prevents their turn forever; unreadable names defer erasure and recovery indefinitely).
    await lc.drop_malformed_queued_renames(tmp_path)
    assert _peers(tmp_path)["pending_rename_queue"] == [good]


async def test_a_partially_migrated_chained_rename_moves_every_name_back(tmp_path, monkeypatch):
    roster = await seed_roster(tmp_path, own_char="A")
    await make_visit(tmp_path, vid(1), [ln(1)], own_char="A", own_char_uid=CHAR_UID_A)
    marker = {"old": "A", "new": "B", "uid": CHAR_UID_A, "chained": ["C"]}
    await set_roster_marker(tmp_path, "pending_rename", marker)
    original = VisitSpool.rename_own_char

    async def busy(*_a, **_k):
        raise OSError("spool locked")

    # Character now named C: roster moved A -> C, visit rewrite failed; marker remains with migration-started recorded.
    monkeypatch.setattr(VisitSpool, "rename_own_char", busy)
    with pytest.raises(OSError):
        await lc.reconcile_rename(tmp_path, {"C"}, {"C": CHAR_UID_A})
    monkeypatch.setattr(VisitSpool, "rename_own_char", original)
    assert await roster.get_char_entry(PEER_X, "C") is not None
    assert _peers(tmp_path)["pending_rename"] == {**marker, "migrated": True}
    # Later returns to old A: chained C cannot be ignored as rollback residue; migrate its moved entries back too.
    # (Ignoring chained or migrating only the new name back must fail.)
    assert await lc.reconcile_rename(tmp_path, {"A"}, {"A": CHAR_UID_A}) == frozenset()
    assert await roster.get_char_entry(PEER_X, "A") is not None
    assert await roster.get_char_entry(PEER_X, "C") is None
    assert await _state_char(tmp_path, vid(1)) == "A"
    assert "pending_rename" not in _peers(tmp_path)


async def test_replay_goes_on_after_one_item_fails_to_write(tmp_path, monkeypatch):
    await _retire_fixture(tmp_path)
    first = lc.retire_item("A", CHAR_UID_A)
    second = lc.retire_item("Z", "f" * 32)
    await add_roster_marker_item(tmp_path, "pending_retire", first)
    await add_roster_marker_item(tmp_path, "pending_retire", second)
    settled = []

    async def settle(_config_dir, item, **_kw):
        if item == first:
            raise OSError("disk full")
        settled.append(item)
        return True

    monkeypatch.setattr(lc, "settle_retire", settle)
    # Failure to persist one entry must not abort replay; continue subsequent entries (not catching per entry must fail).
    assert await lc.replay_retires(tmp_path, _loader({"B"}, {"B": CHAR_UID_B}), retire_persona=None) is False
    assert settled == [second]


async def test_a_rename_waiting_for_the_retirement_of_its_third_name_reports_that_name(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "B", "uid": CHAR_UID_A})
    # Character now named C, with residue of a deleted character still retiring under C: rename is waiting, not ambiguous.
    await add_roster_marker_item(tmp_path, "pending_retire", lc.retire_item("C", CHAR_UID_B))
    unsettled = await lc.reconcile_rename(tmp_path, {"C"}, {"C": CHAR_UID_A})
    # Report waiting target too so name checks answer 'retry later' (only old/new must fail by misclassifying permanent ambiguity).
    assert unsettled == frozenset({"A", "B", "C"})
    assert await lc.names_retiring(tmp_path, unsettled) is True


async def test_renaming_a_character_settles_its_queued_rename_first(tmp_path):
    roster = await seed_roster(tmp_path, own_char="A")
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="P")
    # Active slot has another character's reconcilable P -> Q; this character (now B) has A -> B queued.
    await set_roster_marker(tmp_path, "pending_rename", {"old": "P", "new": "Q", "uid": "e" * 32})
    await add_roster_marker_item(tmp_path, "pending_rename_queue", {"old": "A", "new": "B", "uid": CHAR_UID_A})
    uid_of = {"B": CHAR_UID_A, "Q": "e" * 32}
    marker = await lc.begin_rename(tmp_path, "B", "C", CHAR_UID_A, names=set(uid_of), uid_of=uid_of)
    # Finish queued migration before recording this rename (checking only active slot must fail, leaving A -> B queued with only its two old names reserved).
    assert marker == {"old": "B", "new": "C", "uid": CHAR_UID_A}
    assert await roster.get_char_entry(PEER_X, "B") is not None and await roster.get_char_entry(PEER_X, "A") is None
    data = _peers(tmp_path)
    assert data["pending_rename"] == marker and "pending_rename_queue" not in data


async def test_renaming_a_character_whose_queued_rename_stays_pending_is_refused(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="P")
    await set_roster_marker(tmp_path, "pending_rename", {"old": "P", "new": "Q", "uid": "e" * 32})
    queued = {"old": "A", "new": "B", "uid": CHAR_UID_A}
    await add_roster_marker_item(tmp_path, "pending_rename_queue", queued)
    # This character's A -> B is permanently ambiguous (another character owns A); B -> C cannot bypass it (active-slot-only reconciliation must fail).
    uid_of = {"A": CHAR_UID_B, "B": CHAR_UID_A, "Q": "e" * 32}
    with pytest.raises(lc.RenamePendingElsewhere):
        await lc.begin_rename(tmp_path, "B", "C", CHAR_UID_A, names=set(uid_of), uid_of=uid_of)
    assert _peers(tmp_path)["pending_rename"] == queued


async def test_renaming_without_a_queued_rename_of_its_own_is_unchanged(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    other = {"old": "P", "new": "Q", "uid": "e" * 32}
    await add_roster_marker_item(tmp_path, "pending_rename_queue", other)
    # Queue contains only other characters' renames: record this rename directly without touching the queue.
    marker = await lc.begin_rename(tmp_path, "A", "A2", CHAR_UID_A, names={"A"}, uid_of={"A": CHAR_UID_A})
    assert marker == {"old": "A", "new": "A2", "uid": CHAR_UID_A}
    assert _peers(tmp_path)["pending_rename_queue"] == [other]


# ── Names handed to new characters by snapshot: no name-based deletion / migration before the original rename reconciles ─────────────


async def test_deleting_the_fresh_holder_of_a_handed_over_name_settles_the_rename_first(tmp_path):
    roster = await seed_roster(tmp_path, own_char="A")
    # Snapshot renames uid A from A to B and hands A to previously absent uid B (handed_over): entries under A still belong to uid A.
    handed = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE, "handed_over": ["A"]}
    await set_roster_marker(tmp_path, "pending_rename", handed)
    load = _loader({"A", "B"}, {"A": CHAR_UID_B, "B": CHAR_UID_A})
    # Reconcile that rename before deleting uid B: entries move to B, so name-based A retirement cannot erase them (skipping reconciliation must fail).
    await lc.begin_retire(tmp_path, "A", CHAR_UID_B, load_names=load)
    assert await roster.get_char_entry(PEER_X, "B") is not None
    assert await roster.get_char_entry(PEER_X, "A") is None
    assert "pending_rename" not in _peers(tmp_path)


async def test_deleting_the_fresh_holder_is_refused_while_the_handing_rename_cannot_settle(tmp_path, monkeypatch):
    await seed_roster(tmp_path, own_char="A")
    handed = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE, "handed_over": ["A"]}
    await set_roster_marker(tmp_path, "pending_rename", handed)

    async def busy(*_a, **_k):
        raise OSError("spool locked")

    monkeypatch.setattr(VisitSpool, "rename_own_char", busy)
    load = _loader({"A", "B"}, {"A": CHAR_UID_B, "B": CHAR_UID_A})
    # Reject deletion with 409 if reconciliation fails, without recording retirement (skipping reconciliation must fail by deleting uid A's entries under A).
    with pytest.raises(lc.RenamePendingElsewhere):
        await lc.begin_retire(tmp_path, "A", CHAR_UID_B, load_names=load)
    assert "pending_retire" not in _peers(tmp_path)


async def test_renaming_the_fresh_holder_of_a_handed_over_name_settles_the_rename_first(tmp_path):
    roster = await seed_roster(tmp_path, own_char="A")
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="P")
    await set_roster_marker(tmp_path, "pending_rename", {"old": "P", "new": "Q", "uid": "e" * 32})
    handed = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE, "handed_over": ["A"]}
    await add_roster_marker_item(tmp_path, "pending_rename_queue", handed)
    uid_of = {"A": CHAR_UID_B, "B": CHAR_UID_A, "Q": "e" * 32}
    # uid B (A's new owner) renames A -> C: first finish migration that handed over A, moving uid A's entries to B, then record this rename.
    # (Skipping reconciliation must fail because later migration would take those entries to C.)
    marker = await lc.begin_rename(tmp_path, "A", "C", CHAR_UID_B, names=set(uid_of), uid_of=uid_of)
    assert marker == {"old": "A", "new": "C", "uid": CHAR_UID_B}
    assert await roster.get_char_entry(PEER_X, "B") is not None and await roster.get_char_entry(PEER_X, "A") is None
    assert "pending_rename_queue" not in _peers(tmp_path)


async def test_deleting_the_fresh_holder_goes_ahead_when_the_handing_rename_is_ambiguous_for_good(tmp_path):
    roster = await seed_roster(tmp_path, own_char="A")
    # uid A's A -> B handed A to uid B and it is now C; another character uid e owns B: permanent ambiguity.
    handed = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE,
              "handed_over": ["A"], "chained": ["C"]}
    await set_roster_marker(tmp_path, "pending_rename", handed)
    uid_of = {"A": CHAR_UID_B, "B": "e" * 32, "C": CHAR_UID_A}
    # Deleting uid B must not block forever (the prompt may ask users to delete it); skip name-based A cleanup and retain uid A's entries.
    # (Still rejecting or omitting roster_done must fail.)
    item = await lc.begin_retire(tmp_path, "A", CHAR_UID_B, load_names=_loader(set(uid_of), uid_of))
    assert item == {**lc.retire_item("A", CHAR_UID_B), "roster_done": True}
    remaining = {name: uid for name, uid in uid_of.items() if name != "A"}
    assert await lc.settle_retire(tmp_path, item, names=set(remaining), uid_of=remaining, retire_persona=None)
    assert await roster.get_char_entry(PEER_X, "A") is not None
    assert _peers(tmp_path)["pending_rename"] == handed


async def test_renaming_the_fresh_holder_stays_refused_when_the_handing_rename_is_ambiguous(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    handed = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE,
              "handed_over": ["A"], "chained": ["C"]}
    await set_roster_marker(tmp_path, "pending_rename", handed)
    uid_of = {"A": CHAR_UID_B, "B": "e" * 32, "C": CHAR_UID_A}
    # Rename would migrate uid A's entries under A too: still reject it (deletion remains allowed).
    with pytest.raises(lc.RenamePendingElsewhere):
        await lc.begin_rename(tmp_path, "A", "D", CHAR_UID_B, names=set(uid_of), uid_of=uid_of)
    assert _peers(tmp_path)["pending_rename"] == handed


async def test_reconcile_renames_can_leave_deleted_characters_queued_renames_alone(tmp_path, monkeypatch):
    await seed_roster(tmp_path, own_char="A")
    dropped = []

    async def drop(*_a, **_k):
        dropped.append(True)

    monkeypatch.setattr(lc, "drop_deleted_queued_renames", drop)
    # Startup recovery already removed entries under lock using a fresh roster; do not repeat with a possibly stale roster (repeating must fail).
    await lc.reconcile_renames(tmp_path, {"A"}, {"A": CHAR_UID_A}, drop_deleted=False)
    assert dropped == []
    await lc.reconcile_renames(tmp_path, {"A"}, {"A": CHAR_UID_A})
    assert dropped == [True]


async def test_startup_recovery_drops_deleted_queued_renames_only_under_the_lock(tmp_path, _readable, monkeypatch):
    from main_logic.visit import local_chars, recovery

    async def configured():
        return {"A2": CHAR_UID_A}

    seen = []

    async def reconcile(*_a, **kw):
        seen.append(kw.get("drop_deleted", True))
        return frozenset()

    monkeypatch.setattr(local_chars, "load_local_characters", configured)
    monkeypatch.setattr(recovery, "reconcile_renames", reconcile)
    await seed_roster(tmp_path, own_char="A")
    await _recover(tmp_path, ["A2"], {}, list_char_names=None)
    # Do not remove again outside the lock using the same roster (repeating must fail).
    assert seen == [False]


async def test_deleting_the_fresh_holder_waits_while_the_handing_rename_waits_for_a_retirement(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    handed = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE, "handed_over": ["A"]}
    await set_roster_marker(tmp_path, "pending_rename", handed)
    # The rename handing over A waits for the old character under B to retire: eventually reconcilable, not permanent ambiguity; deletion must still wait (409).
    # (Allowing retirement waits as ambiguity must fail.)
    await add_roster_marker_item(tmp_path, "pending_retire", lc.retire_item("B", "e" * 32))
    uid_of = {"A": CHAR_UID_B, "B": CHAR_UID_A}
    with pytest.raises(lc.RenamePendingElsewhere):
        await lc.begin_retire(tmp_path, "A", CHAR_UID_B, load_names=_loader(set(uid_of), uid_of))


async def test_chained_names_appended_after_the_current_one_by_a_rolled_back_import_are_ignored(tmp_path):
    roster = await _visit_data(tmp_path)
    # X -> A unreconciled, character now D (recorded in chained by an import); another import tries D -> C,
    # deleting C's original owner, but rolls back: it remains D and another character still owns C, so C no longer belongs to it.
    # (Counting C must fail by creating permanent ambiguity and blocking the name forever.)
    await set_roster_marker(tmp_path, "pending_rename",
                            {"old": "X", "new": "A", "uid": CHAR_UID_A, "chained": ["D", "C"]})
    uid_of = {"D": CHAR_UID_A, "C": CHAR_UID_B}
    assert await lc.reconcile_rename(tmp_path, set(uid_of), uid_of) == frozenset()
    assert await roster.get_char_entry(PEER_X, "D") is not None
    assert "pending_rename" not in _peers(tmp_path)



# ── Admission: no visits until the rename handing over this name finishes migration ─────────────────────────────


async def test_handover_blocks_visit_only_while_the_handing_rename_can_still_settle(tmp_path, monkeypatch):
    roster = await seed_roster(tmp_path, own_char="A")
    handed = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE, "handed_over": ["A"]}
    await set_roster_marker(tmp_path, "pending_rename", handed)
    uid_of = {"A": CHAR_UID_B, "B": CHAR_UID_A}
    load = _loader(set(uid_of), uid_of)
    # No name handed to this character: allow (ignoring uid / name must fail).
    assert await lc.handover_blocks_visit(tmp_path, "B", CHAR_UID_A, load) is False
    original = VisitSpool.rename_own_char

    async def busy(*_a, **_k):
        raise OSError("spool locked")

    monkeypatch.setattr(VisitSpool, "rename_own_char", busy)
    # Cannot reconcile: block (unconditional allowance must fail).
    assert await lc.handover_blocks_visit(tmp_path, "A", CHAR_UID_B, load) is True
    monkeypatch.setattr(VisitSpool, "rename_own_char", original)
    # Can reconcile: finish migration before allowing; original character's A entries move to B (skipping reconciliation must fail).
    assert await lc.handover_blocks_visit(tmp_path, "A", CHAR_UID_B, load) is False
    assert await roster.get_char_entry(PEER_X, "B") is not None
    assert "pending_rename" not in _peers(tmp_path)


async def test_handover_keeps_blocking_visits_while_the_handing_rename_is_ambiguous(tmp_path):
    roster = await seed_roster(tmp_path, own_char="A")
    handed = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE,
              "handed_over": ["A"], "chained": ["C"]}
    await set_roster_marker(tmp_path, "pending_rename", handed)
    uid_of = {"A": CHAR_UID_B, "B": "e" * 32, "C": CHAR_UID_A}
    # Rename handing over A is ambiguous (uid e owns B): still block. Ambiguity can clear; deleting uid e lets reconciliation move
    # all A entries to uid A, including any uid B writes made meanwhile (allowing on ambiguity must fail).
    assert await lc.handover_blocks_visit(tmp_path, "A", CHAR_UID_B, _loader(set(uid_of), uid_of)) is True
    assert _peers(tmp_path)["pending_rename"] == handed
    # After deleting uid e, ambiguity clears: move uid A entries to C before allowing.
    del uid_of["B"]
    assert await lc.handover_blocks_visit(tmp_path, "A", CHAR_UID_B, _loader(set(uid_of), uid_of)) is False
    assert await roster.get_char_entry(PEER_X, "C") is not None and await roster.get_char_entry(PEER_X, "A") is None
    assert "pending_rename" not in _peers(tmp_path)


async def test_handover_check_lets_an_unreadable_roster_through(tmp_path):
    (tmp_path / "visit_peers.json").write_text("{broken", encoding="utf-8")

    async def unread():
        raise AssertionError("not read")

    # Unreadable roster: allow like name reservation handling, without reading character config.
    assert await lc.handover_blocks_visit(tmp_path, "A", CHAR_UID_B, unread) is False
