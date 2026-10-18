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
    # 其余步骤照做（各步互不连累），标记保留（变异：失败也删标记必红）；按名字清名册那一步
    # 做完了，记 roster_done（变异：不记必红）
    assert not (spool_dir / f"{vid(1)}.state.json").exists()
    assert _peers(tmp_path)["pending_retire"] == [{**item, "roster_done": True}]
    assert await lc.is_name_retiring(tmp_path, "A") and not await lc.is_name_retiring(tmp_path, "B")
    # 之后这个名字归了别的角色（云快照导入不经过名字占用判定），它写下的条目不能被重试再清掉
    # （变异：重试照样按名字清名册必红）
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


# ── 名字占用（退役 / 改名标记） ─────────────────────────────────────────


async def test_name_marker_state_covers_both_markers(tmp_path):
    assert await lc.name_marker_state(tmp_path, "A") == lc.NAME_FREE
    assert not (tmp_path / "visit_peers.json").exists()
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "A2", "uid": CHAR_UID_A})
    await add_roster_marker_item(tmp_path, "pending_retire", lc.retire_item("Gone", CHAR_UID_B))
    # 变异：只看退役标记必红（改名的两个名字都算占用）
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
            # 启动恢复恰好在两次读之间清掉位置、把队首挪进来
            await subjects.clear_roster_marker(config_dir, "pending_rename", slot)
            await subjects.promote_roster_marker(config_dir, "pending_rename", "pending_rename_queue")
        return value

    monkeypatch.setattr(lc, "read_roster_marker", racing)
    # 变异：分开读三个标记必红（读到旧位置 + 已空的队列，把排队中的 A 判成空闲）
    assert await lc.name_marker_state(tmp_path, "A") == lc.NAME_RENAMING


async def test_name_marker_state_of_an_unreadable_roster_is_unknown_not_held(tmp_path):
    (tmp_path / "visit_peers.json").write_text("{broken", encoding="utf-8")
    assert await lc.name_marker_state(tmp_path, "A") == lc.NAME_UNREADABLE


# ── 删除时先对账本角色的改名 ──────────────────────────────────────────


def _counting_loader(names, uid_of):
    calls = []

    async def load():
        calls.append(True)
        return set(names), dict(uid_of)

    return load, calls


async def test_delete_settles_its_own_pending_rename_before_retiring(tmp_path):
    roster = await _visit_data(tmp_path)
    # A -> A2 已提交、迁移没做完；现在删 A2
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "A2", "uid": CHAR_UID_A})
    load, _calls = _counting_loader({"A2", "B"}, {"A2": CHAR_UID_A, "B": CHAR_UID_B})
    item = await lc.begin_retire(tmp_path, "A2", CHAR_UID_A, load_names=load)
    assert item == lc.retire_item("A2", CHAR_UID_A)
    # 迁移先补完：旧名 A 下不再有条目，退役按 A2 就能全部清掉（变异：不先对账必红）
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
    # 名册里的迁移（先于 spool）已幂等做了，标记仍在：重启对账会补完
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
    # 旧名 A 已被另一个角色占用：两边的条目分不开，挡住删除只会让两个角色互相卡死
    load, _calls = _counting_loader({"A", "A2"}, {"A": CHAR_UID_B, "A2": CHAR_UID_A})
    assert await lc.begin_retire(tmp_path, "A2", CHAR_UID_A, load_names=load) == lc.retire_item("A2", CHAR_UID_A)
    assert _peers(tmp_path)["pending_rename"] == marker
    # A2 一删，标记就按「已删除」清掉
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


# ── 云快照导入删掉的角色 ──────────────────────────────────────────────


def test_import_removal_without_visit_data_writes_nothing(tmp_path):
    assert lc.record_removed_characters_sync(tmp_path, [{"name": "A", "character_uid": CHAR_UID_A}]) == 0
    assert not (tmp_path / "visit_peers.json").exists()


async def test_import_removal_records_one_retire_item_per_character(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    removed = [
        {"name": "A", "character_uid": CHAR_UID_A},
        {"name": "Legacy", "character_uid": None},
        {"name": "", "character_uid": CHAR_UID_B},           # 坏条目跳过
        {"name": "Bad", "character_uid": 7},
    ]
    assert lc.record_removed_characters_sync(tmp_path, removed) == 2
    assert _peers(tmp_path)["pending_retire"] == [lc.retire_item("A", CHAR_UID_A), lc.retire_item("Legacy", None)]
    # 重复导入（上次中途失败）不会重复记
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
    # 导入回滚：A 还在配置里
    report = await _recover(tmp_path, ["A", "B"], {CHAR_UID_A: "A", CHAR_UID_B: "B"}, retire_persona=personas)
    assert report.retired is True and personas.calls == []
    assert (spool_dir / f"{vid(1)}.state.json").exists()
    assert await read_roster_marker(tmp_path, "pending_retire") is None


# ── 改名对账：改到第三个名字 / 目标名还在退役 ─────────────────────────


async def _data_under_both_names(tmp_path):
    """Roster and spool entries of ``CHAR_UID_A`` under both ``A`` and ``A2`` (a migration cut halfway)."""
    roster = await _visit_data(tmp_path)
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="A2")
    await make_visit(tmp_path, vid(3), [ln(1)], own_char="A2", own_char_uid=CHAR_UID_A)
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "A2", "uid": CHAR_UID_A})
    return roster


async def test_a_rename_chained_to_a_third_name_moves_both_old_names(tmp_path):
    roster = await _data_under_both_names(tmp_path)
    # 标记 A -> A2 没对完账时，云快照又把这个 uid 改成 A3（标记位被占，没能另记）
    unsettled = await lc.reconcile_rename(tmp_path, {"A3", "B"}, {"A3": CHAR_UID_A, "B": CHAR_UID_B})
    # 变异：没有第三名字分支必红（永久歧义、两个旧名下的条目没人迁）
    assert unsettled == frozenset()
    assert await roster.get_char_entry(PEER_X, "A") is None and await roster.get_char_entry(PEER_Y, "A2") is None
    assert await roster.get_char_entry(PEER_X, "A3") is not None
    assert await roster.get_char_entry(PEER_Y, "A3") is not None
    assert await _state_char(tmp_path, vid(1)) == "A3" and await _state_char(tmp_path, vid(3)) == "A3"
    assert await _state_char(tmp_path, vid(2)) == "B"
    assert "pending_rename" not in _peers(tmp_path)


async def test_a_chained_rename_whose_old_name_is_reused_stays_ambiguous(tmp_path):
    roster = await _data_under_both_names(tmp_path)
    # 旧名 A 已被另一个角色用上：名册只按名字存，迁走会把它的记录也带走（变异：去掉复用判断必红）
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
    # 先迁进 A2、之后按名字退役 A2 会把迁来的条目一起删：等退役补完（变异：不等必红）
    assert await lc.reconcile_rename(tmp_path, {"A2"}, {"A2": CHAR_UID_A}) == frozenset({"A", "A2"})
    assert await roster.get_char_entry(PEER_X, "A") is not None
    assert await _state_char(tmp_path, vid(1)) == "A"
    assert _peers(tmp_path)["pending_rename"] == marker
    # 退役项与改名无关的名字不挡迁移
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

    # 生产路径按 uid 读配置（名字相同、uid 不同的退役项才分得清）
    monkeypatch.setattr(local_chars, "load_local_characters", configured)
    roster = await _visit_data(tmp_path)
    # 云快照一次导入里：删掉了叫 A2 的旧角色（uid B），又把 uid A 从 A 改成 A2
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="A2")
    await make_visit(tmp_path, vid(3), [ln(1)], own_char="A2", own_char_uid=CHAR_UID_B)
    await add_roster_marker_item(tmp_path, "pending_retire", lc.retire_item("A2", CHAR_UID_B))
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "A2", "uid": CHAR_UID_A})
    report = await _recover(tmp_path, ["A2"], {CHAR_UID_A: "A2"}, retire_persona=Personas(tmp_path),
                            list_char_names=None)
    # 变异：先对账改名必红（要么迁进去的条目被退役删掉，要么改名一直等着）
    assert report.retired is True and report.renamed is True
    assert await roster.get_char_entry(PEER_Y, "A2") is None                 # 旧角色的残留退役了
    assert await roster.get_char_entry(PEER_X, "A2") is not None             # 改名迁来的条目还在
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
    # 变异：启动对账不处理队列必红（B 的条目留在旧名下）
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
    # 位置上的改名永久歧义（新名 X 被另一个角色占着）：队列轮不到
    stuck = {"old": "A", "new": "X", "uid": CHAR_UID_A}
    await set_roster_marker(tmp_path, "pending_rename", stuck)
    await add_roster_marker_item(tmp_path, "pending_rename_queue", {"old": "G", "new": "G2", "uid": "f" * 32})
    await _recover(tmp_path, ["A2", "X"], {}, list_char_names=None)
    # 已删除角色的排队改名不用等位置：直接撤掉，G / G2 不再被占着（变异：不撤必红）
    data = _peers(tmp_path)
    assert data["pending_rename"] == stuck and "pending_rename_queue" not in data
    assert await lc.name_marker_state(tmp_path, "G") == lc.NAME_FREE


async def test_forget_refuses_names_held_by_a_queued_or_chained_rename(tmp_path):
    from main_logic.visit.forget_runner import RenamePending, _refuse_pending_rename

    await set_roster_marker(tmp_path, "pending_rename", {"old": "X", "new": "Y", "uid": CHAR_UID_B, "chained": ["Z"]})
    await add_roster_marker_item(tmp_path, "pending_rename_queue", {"old": "A", "new": "A2", "uid": CHAR_UID_A})
    # 排在队里的改名同样没对完账：配置已是 A2、条目还在 A 下，清除 A2 会「成功」地什么都没删
    # （变异：只看位置必红）；链式改名记下的名字也算（变异：不看 chained 必红）
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
    # 位置上的改名永久歧义，后面排着另一个角色的 X -> Q（共用 X，得等它）：Q 下还没有条目（都在 X 下）
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "X", "uid": CHAR_UID_A})
    await add_roster_marker_item(tmp_path, "pending_rename_queue", {"old": "X", "new": "Q", "uid": "e" * 32})
    report = await _recover(tmp_path, ["A2", "X", "Q"], {}, list_char_names=None)
    # 变异：只跳过位置上的两个名字必红（Q 的逐场补录会按新名找不到条目、记成已完成）
    assert report.renamed is False
    assert skipped and {"A", "X", "Q"} <= set(skipped[0])


# ── 云快照改名的回滚、链式中间名、已清完名册的退役 ─────────────────────


async def test_a_rolled_back_cloudsave_rename_is_cleared_without_moving_anything_back(tmp_path):
    # 快照要把 uid A 从 A 改成 B、并删掉原来叫 B 的角色；导入回滚：两个角色都还在
    roster = await seed_roster(tmp_path, own_char="A")
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="B")
    await make_visit(tmp_path, vid(3), [ln(1)], own_char="B", own_char_uid=CHAR_UID_B)
    marker = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE}
    await set_roster_marker(tmp_path, "pending_rename", marker)
    assert await lc.reconcile_rename(tmp_path, {"A", "B"}, {"A": CHAR_UID_A, "B": CHAR_UID_B}) == frozenset()
    # 变异：按普通改名反向改写必红（B 的真实条目与场次被搬进 A）
    assert await roster.get_char_entry(PEER_Y, "B") is not None
    assert await roster.get_char_entry(PEER_Y, "A") is None
    assert await _state_char(tmp_path, vid(3)) == "B"
    assert "pending_rename" not in _peers(tmp_path)


async def test_a_chained_rename_moves_the_entries_under_every_intermediate_name(tmp_path):
    roster = await seed_roster(tmp_path, own_char="A")
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="C")
    await make_visit(tmp_path, vid(3), [ln(1)], own_char="C", own_char_uid=CHAR_UID_A)
    # A -> B 没对完账期间又叫过 C、现在叫 D：C 下串门留下的条目同样是它的（变异：只迁 old / new 必红）
    await set_roster_marker(tmp_path, "pending_rename",
                            {"old": "A", "new": "B", "uid": CHAR_UID_A, "chained": ["C", "D"]})
    assert await lc.reconcile_rename(tmp_path, {"D"}, {"D": CHAR_UID_A}) == frozenset()
    assert await roster.get_char_entry(PEER_X, "D") is not None
    assert await roster.get_char_entry(PEER_Y, "D") is not None and await roster.get_char_entry(PEER_Y, "C") is None
    assert await _state_char(tmp_path, vid(3)) == "D"


async def test_a_rename_does_not_wait_for_a_retirement_whose_roster_step_is_done(tmp_path):
    roster = await _visit_data(tmp_path)
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "A2", "uid": CHAR_UID_A})
    # 退役 A2 时按名字清名册那一步已做完，只剩按 uid 的步骤（比如人设被锁）补不完：
    # 迁进来的条目不会再被清，改名不必等（变异：照样等必红）
    await add_roster_marker_item(tmp_path, "pending_retire", {**lc.retire_item("A2", CHAR_UID_B), "roster_done": True})
    assert await lc.reconcile_rename(tmp_path, {"A2"}, {"A2": CHAR_UID_A}) == frozenset()
    assert await roster.get_char_entry(PEER_X, "A2") is not None


async def test_an_ambiguous_chained_rename_reports_every_name_it_holds(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    marker = {"old": "A", "new": "B", "uid": CHAR_UID_A, "chained": ["C"]}
    await set_roster_marker(tmp_path, "pending_rename", marker)
    # 角色现在叫 C，A 被别的角色复用：永久歧义。覆盖下载 C 也得答「请先删除其中一个」
    # （变异：只返回 old / new 必红）
    unsettled = await lc.reconcile_rename(tmp_path, {"A", "C"}, {"A": CHAR_UID_B, "C": CHAR_UID_A})
    assert unsettled == frozenset({"A", "B", "C"})
    assert _peers(tmp_path)["pending_rename"] == marker


async def test_chained_names_of_a_rolled_back_import_are_ignored(tmp_path):
    roster = await _visit_data(tmp_path)
    # X -> A 没对完账；一次云快照导入想把它改成 C（记进 chained）、把 C 原来的主人删掉，但回滚了：
    # 它还叫 A、C 仍归另一个角色。chained 的 C 不再算它的（变异：照算必红，永久歧义）
    await set_roster_marker(tmp_path, "pending_rename",
                            {"old": "X", "new": "A", "uid": CHAR_UID_A, "chained": ["C"]})
    assert await lc.reconcile_rename(tmp_path, {"A", "C"}, {"A": CHAR_UID_A, "C": CHAR_UID_B}) == frozenset()
    assert await roster.get_char_entry(PEER_X, "A") is not None
    assert "pending_rename" not in _peers(tmp_path)


# ── 云快照改名迁了一半、退役已清完名册时的歧义判断、退役重放之后的名单 ─────────────


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

    # 正向迁移：名册已挪到 B，改写场次时失败，标记留着
    monkeypatch.setattr(VisitSpool, "rename_own_char", busy)
    with pytest.raises(OSError):
        await lc.reconcile_rename(tmp_path, {"B"}, {"B": CHAR_UID_A})
    monkeypatch.setattr(VisitSpool, "rename_own_char", original)
    assert await roster.get_char_entry(PEER_X, "B") is not None
    assert _peers(tmp_path)["pending_rename"] == {**marker, "migrated": True}
    # 之后又一次导入把它改回 A：已经迁过一部分，不能当成「导入回滚、什么都没迁」直接撤标记
    # （变异：不记 migrated 必红，A 的条目留在 B 下给以后叫 B 的角色继承）
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
    # 按名字清名册那一步做完了：改名不再等它，歧义判断也不能再当成「还在退役、稍后再试」
    # （变异：照算必红，永久歧义一直答 VISIT_DATA_BUSY）
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
        # 退役重放期间一次云下载把 uid E 以第三个名字 R 恢复了回来
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
    # 恢复回来的角色的排队改名不能按重放之前的旧名单当成「已删」撤掉：照常对账，P 下的条目迁到
    # 它现在的名字 R（变异：不重读必红，条目留在 P 下给以后叫 P 的角色继承）
    assert await roster.get_char_entry(PEER_Y, "R") is not None
    assert await roster.get_char_entry(PEER_Y, "P") is None
    assert bool(held) is with_lock


# ── 队列：坏掉的排队项、位置卡住时其它改名照常对账 ─────────────────────


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
    # 缺 old / new 的排队项没什么可对账的：记诊断后清掉，其余照常对账（变异：不清必红，
    # 每次启动都跳过清除重放与补录、清除永远被拒）
    assert report.renamed is True
    assert "pending_rename_queue" not in _peers(tmp_path)
    await _refuse_pending_rename(tmp_path, ["A"])


async def test_a_queue_that_is_not_a_list_is_dropped(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    data = _peers(tmp_path)
    data["pending_rename_queue"] = {"old": "A", "new": "B"}
    (tmp_path / "visit_peers.json").write_text(json.dumps(data), encoding="utf-8")
    # 变异：不清非列表的队列必红（挪进位置时抛 RosterCorruptError，永远解不开）
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
    # 位置那笔永久歧义（X 被别的角色占着）：挪到队尾，P -> Q 照常迁完（变异：位置卡住就停必红）
    assert await lc.reconcile_renames(tmp_path, set(uid_of), uid_of) == frozenset({"A", "X"})
    assert await roster.get_char_entry(PEER_Y, "Q") is not None and await roster.get_char_entry(PEER_Y, "P") is None
    assert await roster.get_char_entry(PEER_X, "A") is not None
    data = _peers(tmp_path)
    assert data["pending_rename"] == stuck and "pending_rename_queue" not in data


async def test_a_queued_rename_sharing_a_name_with_a_pending_one_waits_behind_it(tmp_path):
    roster = await seed_roster(tmp_path, own_char="B")
    # 链式：uid A 从 B 改到 C 卡住（C 被别的角色占着、永久歧义），之后 uid E 从 D 改到 B。
    # B 下的条目还是 uid A 的：E 的改名先迁会把两个角色的条目混到一起（变异：不看共用名字必红）
    stuck = {"old": "B", "new": "C", "uid": CHAR_UID_A}
    await set_roster_marker(tmp_path, "pending_rename", stuck)
    await add_roster_marker_item(tmp_path, "pending_rename_queue", {"old": "D", "new": "B", "uid": "e" * 32})
    uid_of = {"A2": CHAR_UID_A, "C": CHAR_UID_B, "B": "e" * 32}
    assert await lc.reconcile_renames(tmp_path, set(uid_of), uid_of) == frozenset({"B", "C"})
    data = _peers(tmp_path)
    # 两笔都留着、原来的顺序不变
    assert data["pending_rename"] == stuck
    assert data["pending_rename_queue"] == [{"old": "D", "new": "B", "uid": "e" * 32}]
    assert await roster.get_char_entry(PEER_X, "B") is not None


async def test_dropping_malformed_queued_renames_keeps_the_well_formed_ones(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    good = {"old": "G", "new": "A", "uid": CHAR_UID_A}
    data = _peers(tmp_path)
    data["pending_rename_queue"] = [{"old": "A"}, good, "junk"]
    (tmp_path / "visit_peers.json").write_text(json.dumps(data), encoding="utf-8")
    # 变异：不清必红（位置一直被占着时它们永远轮不到，名字读不出、清除与补录一直推迟）
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

    # 角色现在叫 C：名册已从 A 挪进 C，改写场次时失败，标记留着并记下已开始迁
    monkeypatch.setattr(VisitSpool, "rename_own_char", busy)
    with pytest.raises(OSError):
        await lc.reconcile_rename(tmp_path, {"C"}, {"C": CHAR_UID_A})
    monkeypatch.setattr(VisitSpool, "rename_own_char", original)
    assert await roster.get_char_entry(PEER_X, "C") is not None
    assert _peers(tmp_path)["pending_rename"] == {**marker, "migrated": True}
    # 之后它又回到旧名 A：chained 的 C 不能当成回滚留下的名字忽略，挪进去的条目要一起迁回
    # （变异：照样忽略 chained 必红 / 只把新名迁回必红）
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
    # 一项写不进去不能中断整轮重放：后面的照常补（变异：不逐项接住必红）
    assert await lc.replay_retires(tmp_path, _loader({"B"}, {"B": CHAR_UID_B}), retire_persona=None) is False
    assert settled == [second]


async def test_a_rename_waiting_for_the_retirement_of_its_third_name_reports_that_name(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "B", "uid": CHAR_UID_A})
    # 角色现在叫 C，C 下还有已删除角色的残留没退役完：改名在等，不是歧义
    await add_roster_marker_item(tmp_path, "pending_retire", lc.retire_item("C", CHAR_UID_B))
    unsettled = await lc.reconcile_rename(tmp_path, {"C"}, {"C": CHAR_UID_A})
    # 等的目标名一起报出去，名字占用据此答「稍后再试」（变异：只报 old / new 必红，会被当成永久歧义）
    assert unsettled == frozenset({"A", "B", "C"})
    assert await lc.names_retiring(tmp_path, unsettled) is True


async def test_renaming_a_character_settles_its_queued_rename_first(tmp_path):
    roster = await seed_roster(tmp_path, own_char="A")
    await seed_roster(tmp_path, peer_uid=PEER_Y, tag=TAG_Y, own_char="P")
    # 位置上是另一个角色对得完的 P -> Q，这个角色（现在叫 B）的 A -> B 排在队里
    await set_roster_marker(tmp_path, "pending_rename", {"old": "P", "new": "Q", "uid": "e" * 32})
    await add_roster_marker_item(tmp_path, "pending_rename_queue", {"old": "A", "new": "B", "uid": CHAR_UID_A})
    uid_of = {"B": CHAR_UID_A, "Q": "e" * 32}
    marker = await lc.begin_rename(tmp_path, "B", "C", CHAR_UID_A, names=set(uid_of), uid_of=uid_of)
    # 排队那笔先迁完，再记这次改名（变异：只对账位置必红，A -> B 留在队里只占着旧的两个名字）
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
    # 这个角色的 A -> B 永久歧义（A 被别的角色占着）：B -> C 不能越过它记下（变异：只对账位置必红）
    uid_of = {"A": CHAR_UID_B, "B": CHAR_UID_A, "Q": "e" * 32}
    with pytest.raises(lc.RenamePendingElsewhere):
        await lc.begin_rename(tmp_path, "B", "C", CHAR_UID_A, names=set(uid_of), uid_of=uid_of)
    assert _peers(tmp_path)["pending_rename"] == queued


async def test_renaming_without_a_queued_rename_of_its_own_is_unchanged(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    other = {"old": "P", "new": "Q", "uid": "e" * 32}
    await add_roster_marker_item(tmp_path, "pending_rename_queue", other)
    # 队里只有别的角色的改名：照旧直接记这次改名，不碰队列
    marker = await lc.begin_rename(tmp_path, "A", "A2", CHAR_UID_A, names={"A"}, uid_of={"A": CHAR_UID_A})
    assert marker == {"old": "A", "new": "A2", "uid": CHAR_UID_A}
    assert _peers(tmp_path)["pending_rename_queue"] == [other]


# ── 快照交给新角色的名字：原角色的改名对完账之前不能按名字删 / 迁 ─────────────


async def test_deleting_the_fresh_holder_of_a_handed_over_name_settles_the_rename_first(tmp_path):
    roster = await seed_roster(tmp_path, own_char="A")
    # 快照把 uid A 从 A 改成 B，并把 A 交给本机原来没有的 uid B（handed_over）：A 下的条目还是 uid A 的
    handed = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE, "handed_over": ["A"]}
    await set_roster_marker(tmp_path, "pending_rename", handed)
    load = _loader({"A", "B"}, {"A": CHAR_UID_B, "B": CHAR_UID_A})
    # 删 uid B 前先把那笔改名对完：条目迁到 B，之后按名字退役 A 不会删到它们（变异：不先对账必红）
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
    # 对不完账就拒绝删除（409），不写退役项（变异：不先对账必红，A 下 uid A 的条目会被按名字删掉）
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
    # uid B（A 的新主人）改名 A -> C：先把交出 A 的那笔迁完，A 下 uid A 的条目归 B，再记这次改名
    # （变异：不先对账必红，之后迁移会把它们带到 C）
    marker = await lc.begin_rename(tmp_path, "A", "C", CHAR_UID_B, names=set(uid_of), uid_of=uid_of)
    assert marker == {"old": "A", "new": "C", "uid": CHAR_UID_B}
    assert await roster.get_char_entry(PEER_X, "B") is not None and await roster.get_char_entry(PEER_X, "A") is None
    assert "pending_rename_queue" not in _peers(tmp_path)


async def test_deleting_the_fresh_holder_goes_ahead_when_the_handing_rename_is_ambiguous_for_good(tmp_path):
    roster = await seed_roster(tmp_path, own_char="A")
    # uid A 的 A -> B 交出了 A（给 uid B）、现在叫 C；B 又被别的角色（uid e）占着：永久歧义
    handed = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE,
              "handed_over": ["A"], "chained": ["C"]}
    await set_roster_marker(tmp_path, "pending_rename", handed)
    uid_of = {"A": CHAR_UID_B, "B": "e" * 32, "C": CHAR_UID_A}
    # 删 uid B 不能被永远挡住（提示里可能正让用户删它）；但不按名字清 A，uid A 的条目留着
    # （变异：照样拒绝必红 / 不记 roster_done 必红）
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
    # 改名会把 uid A 留在 A 下的条目一起迁走：仍然拒绝（删除可以照常进行）
    with pytest.raises(lc.RenamePendingElsewhere):
        await lc.begin_rename(tmp_path, "A", "D", CHAR_UID_B, names=set(uid_of), uid_of=uid_of)
    assert _peers(tmp_path)["pending_rename"] == handed


async def test_reconcile_renames_can_leave_deleted_characters_queued_renames_alone(tmp_path, monkeypatch):
    await seed_roster(tmp_path, own_char="A")
    dropped = []

    async def drop(*_a, **_k):
        dropped.append(True)

    monkeypatch.setattr(lc, "drop_deleted_queued_renames", drop)
    # 启动恢复已在锁内按新名单撤过：这里不再按（可能过时的）名单撤一次（变异：照样撤必红）
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
    # 锁外不再用同一份名单撤第二次（变异：照样撤必红）
    assert seen == [False]


async def test_deleting_the_fresh_holder_waits_while_the_handing_rename_waits_for_a_retirement(tmp_path):
    await seed_roster(tmp_path, own_char="A")
    handed = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE, "handed_over": ["A"]}
    await set_roster_marker(tmp_path, "pending_rename", handed)
    # 交出 A 的那笔改名在等 B 下旧角色的退役：迟早对得完，不是永久歧义，删除仍要等（409）
    # （变异：在等退役也当成歧义放行必红）
    await add_roster_marker_item(tmp_path, "pending_retire", lc.retire_item("B", "e" * 32))
    uid_of = {"A": CHAR_UID_B, "B": CHAR_UID_A}
    with pytest.raises(lc.RenamePendingElsewhere):
        await lc.begin_retire(tmp_path, "A", CHAR_UID_B, load_names=_loader(set(uid_of), uid_of))


async def test_chained_names_appended_after_the_current_one_by_a_rolled_back_import_are_ignored(tmp_path):
    roster = await _visit_data(tmp_path)
    # X -> A 没对完账、角色已在 D（一次导入记下的 chained）；又一次导入想把它从 D 改到 C、
    # 删掉 C 原来的主人，但回滚了：它还在 D，C 仍归另一个角色。C 不再算它的
    # （变异：照算必红，永久歧义、名字一直被挡）
    await set_roster_marker(tmp_path, "pending_rename",
                            {"old": "X", "new": "A", "uid": CHAR_UID_A, "chained": ["D", "C"]})
    uid_of = {"D": CHAR_UID_A, "C": CHAR_UID_B}
    assert await lc.reconcile_rename(tmp_path, set(uid_of), uid_of) == frozenset()
    assert await roster.get_char_entry(PEER_X, "D") is not None
    assert "pending_rename" not in _peers(tmp_path)



# ── 准入：交出名字的改名迁完之前不串门 ─────────────────────────────


async def test_handover_blocks_visit_only_while_the_handing_rename_can_still_settle(tmp_path, monkeypatch):
    roster = await seed_roster(tmp_path, own_char="A")
    handed = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE, "handed_over": ["A"]}
    await set_roster_marker(tmp_path, "pending_rename", handed)
    uid_of = {"A": CHAR_UID_B, "B": CHAR_UID_A}
    load = _loader(set(uid_of), uid_of)
    # 没有交给这个角色的名字：不拦（变异：不看 uid / 名字必红）
    assert await lc.handover_blocks_visit(tmp_path, "B", CHAR_UID_A, load) is False
    original = VisitSpool.rename_own_char

    async def busy(*_a, **_k):
        raise OSError("spool locked")

    monkeypatch.setattr(VisitSpool, "rename_own_char", busy)
    # 对不完账：拦住（变异：一律放行必红）
    assert await lc.handover_blocks_visit(tmp_path, "A", CHAR_UID_B, load) is True
    monkeypatch.setattr(VisitSpool, "rename_own_char", original)
    # 对得完：先迁完再放行，A 下原角色的条目归到 B（变异：不先对账必红）
    assert await lc.handover_blocks_visit(tmp_path, "A", CHAR_UID_B, load) is False
    assert await roster.get_char_entry(PEER_X, "B") is not None
    assert "pending_rename" not in _peers(tmp_path)


async def test_handover_keeps_blocking_visits_while_the_handing_rename_is_ambiguous(tmp_path):
    roster = await seed_roster(tmp_path, own_char="A")
    handed = {"old": "A", "new": "B", "uid": CHAR_UID_A, "source": lc.CLOUDSAVE_SOURCE,
              "handed_over": ["A"], "chained": ["C"]}
    await set_roster_marker(tmp_path, "pending_rename", handed)
    uid_of = {"A": CHAR_UID_B, "B": "e" * 32, "C": CHAR_UID_A}
    # 交出 A 的那笔改名歧义（B 被 uid e 占着）：照拦。歧义并不永久——uid e 被删后对账会把
    # A 下的条目全迁给 uid A，这期间 uid B 串门写下的也会被带走（变异：歧义时放行必红）
    assert await lc.handover_blocks_visit(tmp_path, "A", CHAR_UID_B, _loader(set(uid_of), uid_of)) is True
    assert _peers(tmp_path)["pending_rename"] == handed
    # uid e 删掉后歧义解开：先把 uid A 的条目迁到 C，再放行
    del uid_of["B"]
    assert await lc.handover_blocks_visit(tmp_path, "A", CHAR_UID_B, _loader(set(uid_of), uid_of)) is False
    assert await roster.get_char_entry(PEER_X, "C") is not None and await roster.get_char_entry(PEER_X, "A") is None
    assert "pending_rename" not in _peers(tmp_path)


async def test_handover_check_lets_an_unreadable_roster_through(tmp_path):
    (tmp_path / "visit_peers.json").write_text("{broken", encoding="utf-8")

    async def unread():
        raise AssertionError("not read")

    # 名册读不出：与名字占用一样放行，不读角色配置
    assert await lc.handover_blocks_visit(tmp_path, "A", CHAR_UID_B, unread) is False
