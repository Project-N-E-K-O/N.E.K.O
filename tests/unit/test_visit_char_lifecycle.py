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


# ── 标记原语 ──────────────────────────────────────────────────────────


async def test_set_marker_only_when_absent_and_keeps_partitions(tmp_path):
    await seed_roster(tmp_path)
    before = _peers(tmp_path)["accounts"]
    assert await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "B"}) == (True, None)
    # 已有标记：不覆盖，返回现有值（上一笔改名没对完账不能被下一笔冲掉）
    assert await set_roster_marker(tmp_path, "pending_rename", {"old": "C", "new": "D"}) == (
        False, {"old": "A", "new": "B"})
    data = _peers(tmp_path)
    assert data["pending_rename"] == {"old": "A", "new": "B"} and data["accounts"] == before


async def test_a_null_marker_is_a_free_slot_not_a_successful_write(tmp_path):
    (tmp_path / "visit_peers.json").write_text(json.dumps({"pending_rename": None}), encoding="utf-8")
    # 变异：把 null 当「已有标记」→ 返回值与写成功撞车 / 永远占位，必红
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
    # 同一个人在别的本机角色下的条目留着；只剩这个角色的 peer 整条删掉
    assert set(accounts[OWN_A]["peers"][PEER_X]["by_char"]) == {"B"}
    assert PEER_Y not in accounts[OWN_A]["peers"]
    assert accounts[OWN_B]["peers"] == {}
    assert await PeerRoster(tmp_path, own_uid="x").retire_char("A") == 0


async def test_roster_retire_char_fails_closed_on_a_damaged_partition(tmp_path):
    (tmp_path / "visit_peers.json").write_text(
        json.dumps({"accounts": {OWN_A: {"peers": {PEER_X: {"by_char": []}}}}}), encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await PeerRoster(tmp_path, own_uid="x").retire_char("A")


# ── 改名 ──────────────────────────────────────────────────────────────


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
    # 事务提交：配置里这个 uid 现在叫 A2
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
    # 新名下有已删除角色留下的旧数据（本 PR 之前删除的角色不会退役）
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="A2")
    await make_visit(tmp_path, vid(3), [ln(1)], own_char="A2", own_char_uid=CHAR_UID_B)
    marker = await lc.begin_rename(tmp_path, "A", "A2", CHAR_UID_A, names={"A"}, uid_of={"A": CHAR_UID_A})
    assert await lc.settle_rename(tmp_path, marker, _loader({"A"}, {"A": CHAR_UID_A}))
    # 变异：回滚时照常反向改写必红（残留会被错挂到 A 名下）
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
    # 启动对账跑同一套规则补完
    assert await lc.reconcile_rename(tmp_path, {"A2"}, {"A2": CHAR_UID_A}) == frozenset()
    assert await roster.get_char_entry(PEER_X, "A2") is not None
    assert await _state_char(tmp_path, vid(1)) == "A2"
    assert "pending_rename" not in _peers(tmp_path)


async def test_an_earlier_marker_is_reconciled_before_the_next_rename(tmp_path):
    roster = await _visit_data(tmp_path)
    # 上一次 A -> A2 已提交、迁移没做完
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
    # 两个名字都在：分不清方向，旧标记保留、这次改名被拒（变异：直接覆盖旧标记必红）
    with pytest.raises(lc.RenamePendingElsewhere):
        await lc.begin_rename(tmp_path, "B", "B2", CHAR_UID_B, names={"A", "A2", "B"}, uid_of=None)
    assert _peers(tmp_path)["pending_rename"] == stale


# ── 删除退役 ──────────────────────────────────────────────────────────


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
    # stat 不了不能当「没有串门数据」：照常走标记（这里名册本身读不出，写标记报损坏、事务拒绝）
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
    assert (spool_dir / f"{vid(2)}.state.json").exists()                  # 别的角色的场次不动
    assert upload.exists() and (reports / f"{vid(1)}.json").exists()     # 账单与举报证据保留
    data = _peers(tmp_path)
    assert PEER_X not in data["accounts"][OWN_A]["peers"]                # 只剩 A 的 peer 整条删
    assert data["accounts"][OWN_B]["peers"] == {}
    assert personas.calls == [CHAR_UID_A] and (personas.dir / f"{CHAR_UID_B}.json").exists()
    assert "pending_retire" not in data


async def test_rolled_back_delete_only_drops_the_marker(tmp_path):
    spool_dir, _upload, _reports, personas = await _retire_fixture(tmp_path)
    item = await lc.begin_retire(tmp_path, "A", CHAR_UID_A)
    # 删除回滚：这个 uid 还在配置里（变异：不看配置直接退役必红）
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
    # 其余步骤照做（各步互不连累），标记保留（变异：失败也删标记必红）
    assert not (spool_dir / f"{vid(1)}.state.json").exists()
    assert _peers(tmp_path)["pending_retire"] == [item]
    assert await lc.is_name_retiring(tmp_path, "A") and not await lc.is_name_retiring(tmp_path, "B")
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
    # 名册分区坏了：这一步失败、标记保留，但场次与人设照样退役（变异：遇错即停必红）
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
    # 同名、不同 uid 的场次不是这个角色的；不带 own_char_uid 的旧场次按名字认
    await make_visit(tmp_path, vid(3), [ln(1)], own_char="A", own_char_uid=CHAR_UID_B)
    legacy = _legacy_spool(tmp_path, vid(4), "A")
    item = await lc.begin_retire(tmp_path, "A", CHAR_UID_A)
    names = {"A", "B"} if name_reused else {"B"}
    uid_of = {"A": "e" * 32, "B": CHAR_UID_B} if name_reused else {"B": CHAR_UID_B}
    assert await lc.settle_retire(tmp_path, item, names=names, uid_of=uid_of, retire_persona=None)
    spool_dir = tmp_path / "visit_spool"
    assert not (spool_dir / f"{vid(1)}.state.json").exists()
    assert (spool_dir / f"{vid(3)}.state.json").exists()
    # 名字已被新角色占用时不按名字回退（分不清是谁的旧场次）
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
    # 变异：不拿锁判定 / 退役必红（会和删除事务的「标记已写、配置未提交」窗口交错）
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
    # 等锁期间这一项已被别处处理掉，同名新角色也建好并开始串门
    await remove_roster_marker_item(tmp_path, "pending_retire", item)
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="A")
    lock.release()
    assert await replay is True
    # 变异：拿着等锁前的快照照样退役必红（会删掉新角色的名册条目）
    assert await PeerRoster(tmp_path, own_uid=OWN_A).get_char_entry(PEER_Y, "A") is not None
    assert calls == []


async def test_unreadable_roster_does_not_block_creating_a_character(tmp_path):
    (tmp_path / "visit_peers.json").write_text("{broken", encoding="utf-8")
    assert not await lc.is_name_retiring(tmp_path, "A")
    with pytest.raises(RosterCorruptError):
        await lc.begin_retire(tmp_path, "A", CHAR_UID_A)


# ── 启动对账 ──────────────────────────────────────────────────────────


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

    return await visit_spool_recovery(
        chips, None, config_dir=tmp_path, is_live=lambda _v: False, resolve_char_name=resolve,
        list_char_names=list_names, client=FakeMemoryServer().client(), **kw,
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
    assert await read_roster_marker(tmp_path, "pending_retire") == [item]


async def test_startup_recovery_without_markers_reports_clean(tmp_path, _readable):
    await make_visit(tmp_path, vid(1), [ln(1)])
    report = await _recover(tmp_path, ["A"], {CHAR_UID_A: "A"})
    assert report.retired is True and report.renamed is True
    assert not (tmp_path / "visit_peers.json").exists()
