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

"""Tests for local "forget this person": revocation logs, plans and replay.

Endpoint-level cases (409 admission gates, the forget sentinel, ``forget_all``
crash replay through the runtime, a fake memory_server over HTTP) belong to
PR-08; here the memory_server forget is an injected async callback.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from main_logic.visit.forget import (
    STEP_CLEAR_LAST_SUMMARY,
    STEP_REMOVE_CHAR,
    STEP_VOID_PENDING,
    STEP_WIPE_SPOOL,
    RevocationLog,
    forget_step_id,
    plan_forget_person,
    revocation_id,
    run_revocation,
)
from main_logic.visit.spool import VisitSpool, new_state
from main_logic.visit.subjects import (
    PeerRoster,
    derive_pair_id,
    derive_peer_char_id,
    derive_person_id,
)

OWN_A = "a" * 24
OWN_B = "b" * 24
PEER_X = "1" * 24
PEER_Y = "2" * 24
TAG_X = "f" * 32
TAG_Y = "e" * 32
CHAR_UID_A = "charuid_a"
CHAR_UID_B = "charuid_b"


async def _no_void(record: dict) -> None:
    """Nothing staged for this person in these tests."""


class Upstream502(RuntimeError):
    pass


class FakeMemoryServer:
    """Records ``scoped_forget`` calls; can fail the n-th call once."""

    def __init__(self, roster=None, peer_uid=None, own_char=None, fail_on_call=None):
        self.calls: list[dict] = []
        self.fail_on_call = fail_on_call
        self.roster = roster
        self.peer_uid = peer_uid
        self.own_char = own_char
        self.entry_present_at_each_call: list[bool] = []

    async def forget(self, subject: dict) -> bool:
        if self.roster is not None:
            entry = await self.roster.get_char_entry(self.peer_uid, self.own_char)
            self.entry_present_at_each_call.append(entry is not None)
        self.calls.append(subject)
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            self.fail_on_call = None
            raise Upstream502("502 from memory_server")
        return True


async def seed(roster: PeerRoster, peer: str, own_char: str, tag: str, now=100.0):
    pair = derive_pair_id(roster.own_uid, peer)
    cid = derive_peer_char_id(peer, tag)
    await roster.upsert(peer, own_char, pair_id=pair, peer_char_id=cid, char_tag=tag,
                        char_display_name="cat", now=now)
    return pair, cid


def test_revocation_id_formula():
    rid = revocation_id(OWN_A, PEER_X, CHAR_UID_A)
    raw = f"{OWN_A}|{PEER_X}|{CHAR_UID_A}".encode("utf-8")
    assert rid == hashlib.sha256(raw).hexdigest()[:32]
    assert rid != revocation_id(OWN_B, PEER_X, CHAR_UID_A)
    assert rid != revocation_id(OWN_A, PEER_X, CHAR_UID_B)


async def test_plan_covers_every_peer_cat_and_orders_steps(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair, c_x = await seed(roster, PEER_X, "A", TAG_X)
    _, c_y = await seed(roster, PEER_X, "A", TAG_Y)
    plan = await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)
    pid = derive_person_id(OWN_A, PEER_X)
    assert plan.pair_ids == (pair,)
    assert list(plan.subjects) == [
        {"subject_kind": "group_chat", "subject_id": f"neko_visit:{pair}"},
        {"subject_kind": "group_participant", "subject_id": f"neko_visit:{pair}:{c_x}"},
        {"subject_kind": "group_participant", "subject_id": f"neko_visit:{pair}:{c_y}"},
        {"subject_kind": "participant", "subject_id": f"neko_visit:{pid}"},
    ]
    assert plan.steps[0] == STEP_CLEAR_LAST_SUMMARY
    assert plan.steps[-3:] == (STEP_REMOVE_CHAR, STEP_WIPE_SPOOL, STEP_VOID_PENDING)
    forgets = [s for s in plan.steps if s.startswith("forget:")]
    assert forgets == [forget_step_id(s) for s in plan.subjects]
    assert plan.revocation_id == revocation_id(OWN_A, PEER_X, CHAR_UID_A)


async def test_plan_merges_in_flight_visit(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair = derive_pair_id(OWN_A, PEER_X)
    cid = derive_peer_char_id(PEER_X, TAG_X)
    plan = await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A, current=(pair, cid))
    assert plan.pair_ids == (pair,)
    assert len(plan.subjects) == 3


async def test_two_cats_forgotten_and_remove_char_waits_for_all_forgets(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair, _ = await seed(roster, PEER_X, "A", TAG_X)
    await seed(roster, PEER_X, "A", TAG_Y)
    await roster.set_last_summary(PEER_X, "A", visit_id="V" * 22, ended_at=1.0,
                                  text="summary", pair_id=pair)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    plan = await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)
    rev_id = await log.open_plan(plan)
    server = FakeMemoryServer(roster, PEER_X, "A", fail_on_call=3)

    with pytest.raises(Upstream502):
        await run_revocation(log, rev_id, roster=roster, forget_subject=server.forget, void_pending=_no_void, own_char="A")
    # 第 3 个 forget 502：by_char['A'] 仍在、日志保留，上次摘要已在第一步删掉。
    entry = await roster.get_char_entry(PEER_X, "A")
    assert entry is not None and "last_summary" not in entry
    record = await log.load(rev_id)
    assert record is not None
    assert STEP_REMOVE_CHAR not in record["done_steps"]
    assert record["done_steps"] == [STEP_CLEAR_LAST_SUMMARY] + [
        forget_step_id(s) for s in plan.subjects[:2]
    ]

    # 重放补完后才删。
    assert await run_revocation(log, rev_id, roster=roster, forget_subject=server.forget, void_pending=_no_void, own_char="A")
    assert await roster.get_char_entry(PEER_X, "A") is None
    assert await log.load(rev_id) is None
    unique = {(s["subject_kind"], s["subject_id"]) for s in server.calls}
    assert unique == {(s["subject_kind"], s["subject_id"]) for s in plan.subjects}
    assert len(unique) == 4
    kinds = sorted(s["subject_kind"] for s in plan.subjects)
    assert kinds == ["group_chat", "group_participant", "group_participant", "participant"]
    # 每次 forget 发生时名册条目都还在（remove_char 一定排在全部 forget 之后）。
    assert all(server.entry_present_at_each_call)


async def test_forget_under_one_character_keeps_the_other(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    await seed(roster, PEER_X, "B", TAG_Y)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    plan_a = await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)
    rev_a = await log.open_plan(plan_a)
    server = FakeMemoryServer()
    await run_revocation(log, rev_a, roster=roster, forget_subject=server.forget, void_pending=_no_void, own_char="A")
    peer = await roster.get_peer(PEER_X)
    assert peer is not None and set(peer["by_char"]) == {"B"}
    c_b = derive_peer_char_id(PEER_X, TAG_Y)
    assert all(c_b not in s["subject_id"] for s in server.calls)
    # B 下的清除照样能执行。
    plan_b = await plan_forget_person(roster, PEER_X, "B", CHAR_UID_B)
    assert any(c_b in s["subject_id"] for s in plan_b.subjects)
    rev_b = await log.open_plan(plan_b)
    assert rev_b != rev_a
    await run_revocation(log, rev_b, roster=roster, forget_subject=server.forget, void_pending=_no_void, own_char="B")
    assert await roster.get_peer(PEER_X) is None


async def test_repeated_forget_reuses_the_same_log(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    plan = await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)
    first = await log.open_plan(plan, now=1.0)
    second = await log.open_plan(plan, now=2.0)
    assert first == second
    files = list((tmp_path / "visit_revocations").iterdir())
    assert [f.name for f in files] == [f"{first}.json"]
    assert (await log.load(first))["requested_at"] == 1.0


async def test_reopen_merges_new_subjects_and_keeps_done_steps(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair, _ = await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    plan1 = await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)
    rev_id = await log.open_plan(plan1)
    server = FakeMemoryServer(fail_on_call=2)
    with pytest.raises(Upstream502):
        await run_revocation(log, rev_id, roster=roster, forget_subject=server.forget, void_pending=_no_void, own_char="A")
    done_before = (await log.load(rev_id))["done_steps"]
    assert forget_step_id(plan1.subjects[0]) in done_before

    # 对方后来又带了另一只猫：第二次清除合并进同一份日志。
    _, c_y = await seed(roster, PEER_X, "A", TAG_Y)
    plan2 = await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)
    assert await log.open_plan(plan2) == rev_id
    record = await log.load(rev_id)
    assert record["done_steps"] == done_before
    new_step = forget_step_id(
        {"subject_kind": "group_participant", "subject_id": f"neko_visit:{pair}:{c_y}"}
    )
    assert new_step in record["steps"] and new_step not in record["done_steps"]
    forgets = [i for i, s in enumerate(record["steps"]) if s.startswith("forget:")]
    assert record["steps"].index(STEP_REMOVE_CHAR) > max(forgets)

    await run_revocation(log, rev_id, roster=roster, forget_subject=server.forget, void_pending=_no_void, own_char="A")
    assert await roster.get_char_entry(PEER_X, "A") is None
    assert await log.load(rev_id) is None


async def test_reopen_rearms_local_steps_already_done(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    plan = await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)
    rev_id = await log.open_plan(plan)
    for step in plan.steps[:-1]:
        await log.mark_done(rev_id, step)
    from main_logic.visit.subjects import group_participant_subject

    pair = derive_pair_id(OWN_A, PEER_X)
    extra = group_participant_subject(pair, "c_" + "9" * 24)   # 对方新带来的一只猫
    await log.open(PEER_X, CHAR_UID_A, [pair], [extra])
    record = await log.load(rev_id)
    assert STEP_REMOVE_CHAR not in record["done_steps"]
    assert STEP_WIPE_SPOOL not in record["done_steps"]
    assert forget_step_id(plan.subjects[0]) in record["done_steps"]
    assert record["steps"].index(STEP_REMOVE_CHAR) > record["steps"].index(
        forget_step_id(extra)
    )


async def test_logs_are_partitioned_by_own_account(tmp_path):
    roster_a = PeerRoster(tmp_path, own_uid=OWN_A)
    roster_b = PeerRoster(tmp_path, own_uid=OWN_B)
    await seed(roster_a, PEER_X, "A", TAG_X)
    await seed(roster_b, PEER_X, "A", TAG_X)
    log_a = RevocationLog(tmp_path, own_uid=OWN_A)
    log_b = RevocationLog(tmp_path, own_uid=OWN_B)
    rev_a = await log_a.open_plan(await plan_forget_person(roster_a, PEER_X, "A", CHAR_UID_A))
    rev_b = await log_b.open_plan(await plan_forget_person(roster_b, PEER_X, "A", CHAR_UID_A))
    assert rev_a != rev_b
    assert (await log_a.load(rev_a))["own_uid"] == OWN_A
    assert [r["id"] for r in await log_a.list_open()] == [rev_a]
    assert {r["id"] for r in await RevocationLog.list_all_open(tmp_path)} == {rev_a, rev_b}
    with pytest.raises(ValueError):
        await run_revocation(log_a, rev_a, roster=roster_b, forget_subject=FakeMemoryServer().forget, void_pending=_no_void, own_char="A")
    await run_revocation(log_a, rev_a, roster=roster_a, forget_subject=FakeMemoryServer().forget, void_pending=_no_void, own_char="A")
    assert await roster_a.get_peer(PEER_X) is None
    assert await roster_b.get_peer(PEER_X) is not None


async def test_wipe_spool_step_clears_only_this_accounts_visits(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair_a, cid = await seed(roster, PEER_X, "A", TAG_X)
    pair_b = derive_pair_id(OWN_B, PEER_X)
    mine = VisitSpool(tmp_path, "visit00000000000000001")
    await mine.write_state(new_state(own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A, pair_id=pair_a,
                                     peer_uid=PEER_X, peer_char_id=cid, memory_enabled=True))
    theirs = VisitSpool(tmp_path, "visit00000000000000002")
    await theirs.write_state(new_state(own_uid=OWN_B, own_char="A", own_char_uid=CHAR_UID_A, pair_id=pair_b,
                                       peer_uid=PEER_X, peer_char_id=cid, memory_enabled=True))
    voided: list[str] = []

    async def void(record):
        voided.append(record["id"])

    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    await run_revocation(log, rev_id, roster=roster, forget_subject=FakeMemoryServer().forget,
                         void_pending=void, own_char="A")
    assert (await mine.read_state())["peer_uid"] is None
    assert (await mine.read_state())["pair_id"] is None
    assert (await theirs.read_state())["peer_uid"] == PEER_X
    assert voided == [rev_id]


async def test_mark_done_rejects_unknown_step(tmp_path):
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    person = {"subject_kind": "participant",
              "subject_id": f"neko_visit:{derive_person_id(OWN_A, PEER_X)}"}
    rev_id = await log.open(PEER_X, CHAR_UID_A, [], [person], own_char="A")
    with pytest.raises(ValueError):
        await log.mark_done(rev_id, "forget:group_chat:nope")


async def test_schema_invalid_log_also_fails_closed(tmp_path):
    from main_logic.visit.forget import RevocationLogUnreadable

    directory = tmp_path / "visit_revocations"
    directory.mkdir()
    bogus = revocation_id(OWN_A, PEER_Y, CHAR_UID_A)
    (directory / f"{bogus}.json").write_text(json.dumps({"id": "x"}), encoding="utf-8")
    with pytest.raises(RevocationLogUnreadable):
        await RevocationLog.list_all_open(tmp_path)


async def test_unconfirmed_forget_is_not_recorded_and_log_is_kept(tmp_path):
    # ScopedMemoryClient.post_forget 失败时返回 False（不抛异常）：不能记完成
    from main_logic.visit.forget import ForgetStepFailed

    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    plan = await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)
    rev_id = await log.open_plan(plan)
    calls: list[dict] = []

    async def failing(subject: dict) -> bool:
        calls.append(subject)
        return False

    with pytest.raises(ForgetStepFailed):
        await run_revocation(log, rev_id, roster=roster, forget_subject=failing, void_pending=_no_void, own_char="A")
    record = await log.load(rev_id)
    assert record is not None
    assert not any(step.startswith("forget:") for step in record["done_steps"])
    assert await roster.get_char_entry(PEER_X, "A") is not None
    assert len(calls) == 1


async def test_unreadable_log_fails_closed_instead_of_disappearing(tmp_path):
    # 读不出的撤销日志不能被静默跳过：补录与建房闸都靠列表判断「有没有清除在进行」
    from main_logic.visit.forget import RevocationLogUnreadable, revocation_id

    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    broken = revocation_id(OWN_B, PEER_X, CHAR_UID_A)
    (log.path_for(rev_id).parent / f"{broken}.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(RevocationLogUnreadable) as ei:
        await RevocationLog.list_all_open(tmp_path)
    assert ei.value.ids == [broken]
    with pytest.raises(RevocationLogUnreadable):
        await log.list_open()


@pytest.mark.parametrize("corrupt", [
    {"steps": []},
    {"done_steps": ["forget:participant:neko_visit:nobody"]},
    {"subjects": []},
    {"pair_ids": []},
    {"pair_ids": [7]},
    {"done_steps": ["wipe_spool"]},
    {"done_steps": ["clear_last_summary", "clear_last_summary"]},
])
async def test_parseable_but_inconsistent_log_fails_closed(tmp_path, corrupt):
    # steps:[] 之类的日志若被放行，重放会什么都不清就删掉日志
    from main_logic.visit.forget import RevocationLogUnreadable

    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    path = log.path_for(rev_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(corrupt)
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(RevocationLogUnreadable):
        await log.list_open()
    with pytest.raises(ValueError):
        await log.load(rev_id)


async def test_forget_planning_refuses_an_unreadable_roster(tmp_path):
    # 名册读不出来时不能当空表规划：那会只清人级主体、漏掉全部 pair 与对方猫娘
    from main_logic.visit.subjects import RosterCorruptError

    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    roster.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)


async def test_wipe_spool_stays_pending_when_a_state_file_is_unreadable(tmp_path, monkeypatch):
    # 已结清的场次常只剩 state.json：它一时读不出（被占用）时 wipe_spool 不能记完成。
    # 内容损坏的那种谁都用不了，由清除直接删掉（见 test_visit_spool_recovery）
    from main_logic.visit import spool as spool_module
    from main_logic.visit.spool import SpoolStateUnreadable

    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair_a, cid = await seed(roster, PEER_X, "A", TAG_X)
    sp = VisitSpool(tmp_path, "visit00000000000000009")
    await sp.write_state(new_state(own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                   pair_id=pair_a, peer_uid=PEER_X, peer_char_id=cid,
                                   memory_enabled=True))
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    real_read = spool_module._read_state_file

    def locked(path):
        if path == sp.state_path:
            raise PermissionError("locked by another process")
        return real_read(path)

    monkeypatch.setattr(spool_module, "_read_state_file", locked)
    with pytest.raises(SpoolStateUnreadable):
        await run_revocation(log, rev_id, roster=roster,
                             forget_subject=FakeMemoryServer().forget, void_pending=_no_void, own_char="A")
    record = await log.load(rev_id)
    assert record is not None and "wipe_spool" not in record["done_steps"]


async def test_a_malformed_extra_pair_id_also_fails_closed(tmp_path):
    from main_logic.visit.forget import RevocationLogUnreadable

    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    path = log.path_for(rev_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["pair_ids"] = record["pair_ids"] + [7]
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(RevocationLogUnreadable):
        await log.list_open()


async def test_discovery_fails_closed_on_a_malformed_jsonl_header(tmp_path):
    # 只剩 .jsonl 且头行坏了：不能当作「不是这一对」而让 wipe_spool 记完成
    from main_logic.visit.spool import SpoolStateUnreadable

    spool_dir = tmp_path / "visit_spool"
    spool_dir.mkdir()
    (spool_dir / "visit00000000000000077.jsonl").write_bytes(b'{"v":1,"visit_id":"trunc')
    with pytest.raises(SpoolStateUnreadable):
        await VisitSpool.find_visits_for_pairs(tmp_path, CHAR_UID_A, ["p" * 24])


async def test_discovery_rejects_a_schema_damaged_header(tmp_path):
    from main_logic.visit.spool import SpoolStateUnreadable

    spool_dir = tmp_path / "visit_spool"
    spool_dir.mkdir()
    (spool_dir / "visit00000000000000078.jsonl").write_bytes(b"{}" + bytes([10]))
    with pytest.raises(SpoolStateUnreadable):
        await VisitSpool.find_visits_for_pairs(tmp_path, CHAR_UID_A, ["p" * 24])


async def test_discovery_rejects_a_header_with_a_damaged_pair_id(tmp_path):
    from main_logic.visit.spool import SpoolStateUnreadable

    spool_dir = tmp_path / "visit_spool"
    spool_dir.mkdir()
    head = {"v": 1, "visit_id": "visit00000000000000079", "role": "host", "own_uid": OWN_A,
            "own_char": "A", "own_char_uid": CHAR_UID_A, "pair_id": 7, "peer_uid": PEER_X,
            "peer_char_id": None, "peer_char_tag": None, "started_at": 1.0, "lang": "zh"}
    (spool_dir / "visit00000000000000079.jsonl").write_bytes(
        json.dumps(head).encode("utf-8") + bytes([10]))
    with pytest.raises(SpoolStateUnreadable):
        await VisitSpool.find_visits_for_pairs(tmp_path, CHAR_UID_A, ["p" * 24])


async def _damaged_log(tmp_path, mutate):
    from main_logic.visit.forget import RevocationLogUnreadable

    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    path = log.path_for(rev_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    mutate(record)
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(RevocationLogUnreadable):
        await log.list_open()


async def test_subject_with_an_extra_scope_field_fails_closed(tmp_path):
    # 多出的 scope 会被原样转发给 /scoped_forget，换掉删除作用域
    await _damaged_log(tmp_path, lambda r: r["subjects"][0].__setitem__("scope", "other"))


async def test_unrelated_extra_pair_id_fails_closed(tmp_path):
    # 多出的无关 pair 会让 wipe_spool 去抹别人的场次
    await _damaged_log(tmp_path, lambda r: r["pair_ids"].append("q" * 24))


async def test_pairs_must_belong_to_the_logs_own_identities(tmp_path):
    # 把 pair 在 pair_ids 与 subjects 两处一起换成别人的：自洽但不属于这条日志的人
    other = derive_pair_id(OWN_A, PEER_Y)

    def swap(record):
        mine = record["pair_ids"][0]
        record["pair_ids"] = [other]
        for s in record["subjects"]:
            s["subject_id"] = s["subject_id"].replace(mine, other)
        from main_logic.visit.forget import build_steps
        record["steps"] = build_steps(record["subjects"])
        record["done_steps"] = []

    await _damaged_log(tmp_path, swap)


async def test_participant_must_be_the_logs_own_peer(tmp_path):
    from main_logic.visit.subjects import derive_person_id, participant_subject

    def swap(record):
        stranger = participant_subject(derive_person_id(OWN_A, PEER_Y))
        record["subjects"] = [stranger if s["subject_kind"] == "participant" else s
                              for s in record["subjects"]]
        from main_logic.visit.forget import build_steps
        record["steps"] = build_steps(record["subjects"])
        record["done_steps"] = []

    await _damaged_log(tmp_path, swap)


async def test_subjects_must_stay_on_the_visit_platform(tmp_path):
    # qq:<pair> 能通过 pair 校验，但会被 /scoped_forget 按平台前缀删掉 QQ 记忆
    def to_qq(record):
        for s in record["subjects"]:
            if s["subject_kind"] == "group_chat":
                s["subject_id"] = "qq:" + s["subject_id"].split(":", 1)[1]
        from main_logic.visit.forget import build_steps
        record["steps"] = build_steps(record["subjects"])
        record["done_steps"] = []

    await _damaged_log(tmp_path, to_qq)


async def test_a_plan_without_the_person_subject_fails_closed(tmp_path):
    # subjects:[] / pair_ids:[] / steps:build_steps([]) 内部自洽，但重放只删名册不清记忆
    def empty(record):
        from main_logic.visit.forget import build_steps
        record["subjects"] = []
        record["pair_ids"] = []
        record["steps"] = build_steps([])
        record["done_steps"] = []

    await _damaged_log(tmp_path, empty)


async def test_a_recorded_pair_without_its_group_subject_fails_closed(tmp_path):
    def drop_group(record):
        record["subjects"] = [s for s in record["subjects"] if s["subject_kind"] != "group_chat"]
        from main_logic.visit.forget import build_steps
        record["steps"] = build_steps(record["subjects"])
        record["done_steps"] = []

    await _damaged_log(tmp_path, drop_group)


async def test_a_recorded_pair_without_participant_subjects_fails_closed(tmp_path):
    def drop_participants(record):
        record["subjects"] = [s for s in record["subjects"]
                              if s["subject_kind"] != "group_participant"]
        from main_logic.visit.forget import build_steps
        record["steps"] = build_steps(record["subjects"])
        record["done_steps"] = []

    await _damaged_log(tmp_path, drop_participants)


async def test_replay_restores_a_participant_subject_dropped_from_the_log(tmp_path):
    # 同一 pair 有两只对方猫娘，日志丢了其中一只的 group_participant 仍能通过校验；
    # 名册还在，重放前对账把它并回来，那份记忆照样被清
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair, c_x = await seed(roster, PEER_X, "A", TAG_X)
    _, c_y = await seed(roster, PEER_X, "A", TAG_Y)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    path = log.path_for(rev_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    dropped = {"subject_kind": "group_participant", "subject_id": f"neko_visit:{pair}:{c_y}"}
    record["subjects"] = [s for s in record["subjects"] if s != dropped]
    from main_logic.visit.forget import build_steps
    record["steps"] = build_steps(record["subjects"])
    path.write_text(json.dumps(record), encoding="utf-8")
    assert await log.load(rev_id) is not None          # 单看日志是自洽的

    server = FakeMemoryServer(roster, PEER_X, "A")
    assert await run_revocation(log, rev_id, roster=roster, forget_subject=server.forget, void_pending=_no_void, own_char="A") is True
    assert dropped in server.calls
    # remove_char 仍在所有 forget 之后：每次 forget 时名册条目都还在
    assert all(server.entry_present_at_each_call)
    assert await roster.get_char_entry(PEER_X, "A") is None


async def test_an_inconsistent_plan_is_refused_before_it_is_written(tmp_path):
    # 写进去就读不出的日志不能落盘：既执行不了，又会让全局读取 fail closed
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    foreign = derive_pair_id(OWN_B, PEER_X)
    pid = derive_person_id(OWN_A, PEER_X)
    with pytest.raises(ValueError):
        await log.open(PEER_X, CHAR_UID_A, [foreign], [
            {"subject_kind": "group_chat", "subject_id": f"neko_visit:{foreign}"},
            {"subject_kind": "participant", "subject_id": f"neko_visit:{pid}"},
        ])
    assert not log.path_for(revocation_id(OWN_A, PEER_X, CHAR_UID_A)).exists()
    assert await log.list_open() == []


async def test_an_inconsistent_merge_leaves_the_existing_log_untouched(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    before = log.path_for(rev_id).read_text(encoding="utf-8")
    foreign = derive_pair_id(OWN_B, PEER_X)
    with pytest.raises(ValueError):
        await log.open(PEER_X, CHAR_UID_A, [foreign], [
            {"subject_kind": "group_chat", "subject_id": f"neko_visit:{foreign}"},
        ])
    assert log.path_for(rev_id).read_text(encoding="utf-8") == before


@pytest.mark.parametrize("make_bogus", [
    lambda pair: {"subject_kind": "bogus", "subject_id": "neko_visit:x"},
    lambda pair: {"subject_kind": "bogus", "subject_id": f"neko_visit:{pair}"},
    lambda pair: {"subject_kind": "group_participant", "subject_id": "neko_visit::"},
    lambda pair: {"subject_kind": "group_participant", "subject_id": f"neko_visit:{pair}: "},
    lambda pair: {"subject_kind": "group_participant", "subject_id": f"neko_visit:{pair}:%"},
    lambda pair: {"subject_kind": "group_chat", "subject_id": "neko_visit::"},
], ids=["kind", "kind-own-pair", "empty", "blank-speaker", "unescaped", "chat-empty"])
async def test_noncanonical_subjects_are_refused_before_persistence(tmp_path, make_bogus):
    # memory_server 对未知 kind / 空分量 / 非规范转义每次都 422：这样的日志落盘就永远关不掉
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair, _ = await seed(roster, PEER_X, "A", TAG_X)
    plan = await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)
    bogus = make_bogus(pair)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    with pytest.raises(ValueError):
        await log.open(PEER_X, CHAR_UID_A, plan.pair_ids, list(plan.subjects) + [bogus])
    assert not log.path_for(plan.revocation_id).exists()


async def test_void_pending_is_a_required_step_callback(tmp_path):
    # 缺省回调不能让 void_pending 静默记完成、日志被删
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    with pytest.raises(TypeError):
        await run_revocation(log, rev_id, roster=roster,
                             forget_subject=FakeMemoryServer().forget, own_char="A")  # type: ignore[call-arg]
    assert await log.load(rev_id) is not None


async def test_close_refuses_a_log_with_pending_steps(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    with pytest.raises(ValueError):
        await log.close(rev_id)
    assert await log.load(rev_id) is not None
    assert await run_revocation(log, rev_id, roster=roster,
                                forget_subject=FakeMemoryServer().forget,
                                void_pending=_no_void, own_char="A") is True
    assert await log.load(rev_id) is None


async def test_mark_done_refuses_out_of_order_steps(tmp_path):
    # 乱序记完成会写出非前缀 done_steps，之后整份日志都读不出来
    from main_logic.visit.forget import STEP_WIPE_SPOOL as _WIPE

    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    plan = await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)
    rev_id = await log.open_plan(plan)
    with pytest.raises(ValueError):
        await log.mark_done(rev_id, _WIPE)
    await log.mark_done(rev_id, plan.steps[0])
    await log.mark_done(rev_id, plan.steps[0])          # 已完成的重复记录：幂等
    record = await log.load(rev_id)
    assert record["done_steps"] == [plan.steps[0]]
    assert await log.list_open() != []


async def test_forget_planning_reads_the_roster_once(tmp_path, monkeypatch):
    # 两次读之间的 upsert 会让 subjects 与 pair_ids 来自不同快照：规划只能读一次
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    reads = {"n": 0}
    real = roster._read

    def counting(fn, strict=False):
        reads["n"] += 1
        return real(fn, strict)

    monkeypatch.setattr(roster, "_read", counting)
    plan = await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)
    assert reads["n"] == 1
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    assert await log.open_plan(plan) == plan.revocation_id


async def test_replay_after_a_rename_uses_the_current_name(tmp_path):
    # 清除中途失败、重放前角色改了名：按旧名找不到条目却「成功」关日志，摘要原文留下
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair, _ = await seed(roster, PEER_X, "A", TAG_X)
    await roster.set_last_summary(PEER_X, "A", visit_id="V" * 22, ended_at=1.0,
                                  text="they talked about X", pair_id=pair)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    server = FakeMemoryServer(fail_on_call=1)
    with pytest.raises(Upstream502):
        await run_revocation(log, rev_id, roster=roster, forget_subject=server.forget,
                             void_pending=_no_void, own_char="A")
    assert await roster.rename_char("A", "B") == 1
    with pytest.raises(TypeError):
        await run_revocation(log, rev_id, roster=roster,                 # type: ignore[call-arg]
                             forget_subject=server.forget, void_pending=_no_void)
    assert await run_revocation(log, rev_id, roster=roster, forget_subject=server.forget,
                                void_pending=_no_void, own_char="B") is True
    assert await roster.get_char_entry(PEER_X, "B") is None
    assert await log.load(rev_id) is None


@pytest.mark.parametrize("body", ["[" * 5000, None], ids=["deep", "version"])
async def test_deep_or_wrong_version_logs_fail_closed(tmp_path, body):
    # 深层嵌套 / 版本不对的日志：与其他读不出的日志一样抛 RevocationLogUnreadable，不重放
    from main_logic.visit.forget import RevocationLogUnreadable

    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    path = log.path_for(rev_id)
    if body is None:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for v in (2, True, None):
            if v is None:
                doc.pop("v", None)
            else:
                doc["v"] = v
            path.write_text(json.dumps(doc), encoding="utf-8")
            with pytest.raises(ValueError):
                await log.load(rev_id)
            with pytest.raises(RevocationLogUnreadable):
                await log.list_open()
        return
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        await log.load(rev_id)
    with pytest.raises(RevocationLogUnreadable):
        await log.list_open()
    with pytest.raises(RevocationLogUnreadable):
        await RevocationLog.list_all_open(tmp_path)


# ── 清除代数与清除意图哨兵（PR-08）────────────────────────────────────


async def test_forget_epoch_is_bumped_and_persisted_before_each_scoped_forget(tmp_path):
    from main_logic.visit.forget import ForgetEpochs

    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    seen = []

    async def forget(subject):
        seen.append((await ForgetEpochs(tmp_path).get([subject]))[f"{subject['subject_kind']}:{subject['subject_id']}"])
        return True

    assert await run_revocation(log, rev_id, roster=roster, forget_subject=forget,
                                void_pending=_no_void, own_char="A")
    assert seen == [1, 1, 1]


async def test_unreadable_epochs_file_fails_the_step_and_keeps_the_log(tmp_path):
    from main_logic.visit.forget import ForgetEpochsUnreadable

    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    (tmp_path / "visit_forget_epochs.json").write_text("{not json", encoding="utf-8")
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    server = FakeMemoryServer()
    with pytest.raises(ForgetEpochsUnreadable):
        await run_revocation(log, rev_id, roster=roster, forget_subject=server.forget,
                             void_pending=_no_void, own_char="A")
    assert server.calls == []
    assert await log.load(rev_id) is not None


async def test_clearing_sentinels_roundtrip_and_fail_closed(tmp_path):
    from main_logic.visit.forget import ClearingSentinels, RevocationLogUnreadable, sentinel_covers

    store = ClearingSentinels(tmp_path)
    person = await store.create(own_uid=OWN_A, scope="person", own_char_uids=[CHAR_UID_A], peer_uid=PEER_X)
    chars = await store.create(own_uid=OWN_A, scope="chars", own_char_uids=[CHAR_UID_B, CHAR_UID_A])
    listed = await store.list_open()
    assert {d["op_id"] for d in listed} == {person["op_id"], chars["op_id"]}
    assert sentinel_covers(person, CHAR_UID_A, PEER_X) and not sentinel_covers(person, CHAR_UID_A, PEER_Y)
    assert sentinel_covers(chars, CHAR_UID_B, PEER_Y) and not sentinel_covers(person, CHAR_UID_B)
    # 撤销日志的列表不把哨兵当日志
    assert await RevocationLog.list_all_open(tmp_path) == []
    removed = await store.remove(person["op_id"])
    removed_again = await store.remove(person["op_id"])
    assert removed is True and removed_again is False
    (tmp_path / "visit_revocations" / f"clearing-{'0' * 32}.json").write_text("[]", encoding="utf-8")
    with pytest.raises(RevocationLogUnreadable):
        await store.list_open()
    with pytest.raises(ValueError):
        await store.create(own_uid=OWN_A, scope="person", own_char_uids=[CHAR_UID_A])


async def test_forget_after_a_local_epoch_reset_goes_above_the_server_fence(tmp_path):
    from main_logic.visit.forget import subject_key
    from main_logic.visit.forget_runner import forget_person
    from tests.unit.visit_memory_test_helpers import FakeMemoryServer, OWN_A, PEER_X, CHAR_UID_A, seed_roster

    await seed_roster(tmp_path)
    roster_subjects = await PeerRoster(tmp_path, own_uid=OWN_A).expand_subjects(PEER_X, "A")
    person = next(s for s in roster_subjects if s["subject_kind"] == "participant")
    server = FakeMemoryServer()
    server.tombstones = {subject_key(person): 5}         # 本地代数被重置，服务端墓碑还在 5
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=server.client())
    assert outcome.done
    sent = [body for body in server.calls("scoped_forget") if body["subject"]["subject_kind"] == "participant"]
    # 先抬到服务端围栏再加 1：发出的代数高于已有墓碑，不会被当成已擦过的重放跳过
    assert sent and sent[0]["forget_epoch"] == 6


# ── 作废步骤：读不出 / 不合 schema 的 state 与抹身份查找同一口径（PR #3293 评审）──


def _void_record(pairs) -> dict:
    return {"own_uid": OWN_A, "own_char_uid": CHAR_UID_A, "pair_ids": list(pairs)}


async def _visit(tmp_path, n: int, *, own_uid=OWN_A, own_char_uid=CHAR_UID_A, peer=PEER_X,
                 header: bool = False) -> VisitSpool:
    sp = VisitSpool(tmp_path, f"visit{n:017d}")
    pair = derive_pair_id(own_uid, peer)
    cid = derive_peer_char_id(peer, TAG_X)
    if header:
        await sp.open({
            "v": 1, "visit_id": sp.visit_id, "role": "host", "own_uid": own_uid, "own_char": "A",
            "own_char_uid": own_char_uid, "pair_id": pair, "peer_uid": peer, "peer_char_id": cid,
            "peer_char_tag": TAG_X, "started_at": 100.0, "lang": "zh-CN",
        }, now=100.0)
        await sp.close()
    state = new_state(own_uid=own_uid, own_char="A", own_char_uid=own_char_uid, pair_id=pair,
                      peer_uid=peer, peer_char_id=cid, memory_enabled=True)
    await sp.write_state(dict(state, debrief_choice="ask_later"))
    return sp


def _make_schema_invalid(sp: VisitSpool, **changes) -> None:
    raw = json.loads(sp.state_path.read_text(encoding="utf-8"))
    raw["field_from_a_newer_version"] = 1                       # 能解析、只是不合当前 schema
    raw.update(changes)
    sp.state_path.write_text(json.dumps(raw), encoding="utf-8")


def _lock_state(monkeypatch, sp: VisitSpool) -> None:
    from main_logic.visit import spool as spool_module

    real_read = spool_module._read_state_file

    def locked(path):
        if path == sp.state_path:
            raise PermissionError("locked by another process")
        return real_read(path)

    monkeypatch.setattr(spool_module, "_read_state_file", locked)


async def test_void_skips_a_locked_state_whose_header_belongs_to_another_character(tmp_path, monkeypatch):
    from main_logic.visit.forget_runner import default_void_pending

    other = await _visit(tmp_path, 1, own_char_uid=CHAR_UID_B, header=True)
    _lock_state(monkeypatch, other)
    # 别的角色的一场 state 长期读不出（被杀毒软件占用）：头行明确属于别人，不挡这次清除
    await default_void_pending(tmp_path)(_void_record([derive_pair_id(OWN_A, PEER_X)]))


@pytest.mark.parametrize("header", [False, True], ids=["state-only", "header-names-pair"])
async def test_void_stays_pending_on_a_locked_state_it_cannot_attribute(tmp_path, monkeypatch, header):
    from main_logic.visit.forget_runner import default_void_pending
    from main_logic.visit.spool import SpoolStateUnreadable

    mine = await _visit(tmp_path, 2, header=header)
    _lock_state(monkeypatch, mine)
    with pytest.raises(SpoolStateUnreadable):
        await default_void_pending(tmp_path)(_void_record([derive_pair_id(OWN_A, PEER_X)]))


@pytest.mark.parametrize("changes", [
    {},                                                          # 仍指认这一对
    {"pair_id": None, "peer_uid": None, "peer_char_id": None},   # 身份已被抹掉
], ids=["names-pair", "wiped"])
async def test_void_stays_pending_on_a_schema_invalid_state_that_may_be_this_persons(tmp_path, changes):
    from main_logic.visit.forget_runner import default_void_pending
    from main_logic.visit.spool import SpoolStateUnreadable

    mine = await _visit(tmp_path, 3)
    _make_schema_invalid(mine, **changes)
    before = mine.state_path.read_bytes()
    # 降级后读到新版本写的 state、debrief 还没写：不能当坏文件跳过（升级回去后仍能「记成日记」），
    # 作废不了就先不结清这份日志
    with pytest.raises(SpoolStateUnreadable):
        await default_void_pending(tmp_path)(_void_record([derive_pair_id(OWN_A, PEER_X)]))
    assert mine.state_path.read_bytes() == before


@pytest.mark.parametrize("kw,changes", [
    ({"own_char_uid": CHAR_UID_B}, {}),
    ({"own_uid": OWN_B}, {}),
    ({"peer": PEER_Y}, {}),
    ({}, {"debrief_choice": "forget"}),                         # 已有最终结果：没什么可作废
], ids=["other-char", "other-account", "other-pair", "final-choice"])
async def test_void_skips_a_schema_invalid_state_that_is_clearly_not_voidable(tmp_path, kw, changes):
    from main_logic.visit.forget_runner import default_void_pending

    sp = await _visit(tmp_path, 4, **kw)
    _make_schema_invalid(sp, **changes)
    await default_void_pending(tmp_path)(_void_record([derive_pair_id(OWN_A, PEER_X)]))


async def test_void_skips_a_corrupt_state(tmp_path):
    from main_logic.visit.forget_runner import default_void_pending

    sp = await _visit(tmp_path, 5)
    sp.state_path.write_text("[1, 2]", encoding="utf-8")         # 能解析但不是对象：谁都用不了
    await default_void_pending(tmp_path)(_void_record([derive_pair_id(OWN_A, PEER_X)]))


async def test_void_finishes_readable_visits_before_reporting_an_unreadable_one(tmp_path, monkeypatch):
    from main_logic.visit.forget_runner import default_void_pending
    from main_logic.visit.spool import SpoolStateUnreadable

    locked = await _visit(tmp_path, 6)
    readable = await _visit(tmp_path, 7)
    _lock_state(monkeypatch, locked)
    with pytest.raises(SpoolStateUnreadable) as ei:
        await default_void_pending(tmp_path)(_void_record([derive_pair_id(OWN_A, PEER_X)]))
    assert ei.value.visit_ids == [locked.visit_id]
    # 读得出的场次照常作废，不因为排在后面而等到下一次重放
    assert (await readable.read_state())["debrief_choice"] == "forget"


async def test_replay_goes_on_past_an_unreadable_revocation_log(tmp_path):
    from main_logic.visit.forget_runner import open_person_log, replay_forgets
    from tests.unit.visit_memory_test_helpers import (
        CHAR_UID_A, OWN_A, PEER_X, FakeMemoryServer, resolver, seed_roster,
    )

    await seed_roster(tmp_path)
    rev_id = await open_person_log(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                   peer_uid=PEER_X)
    broken = tmp_path / "visit_revocations" / ("f" * 32 + ".json")
    broken.write_text("{torn", encoding="utf-8")                    # 别的一对的日志坏了
    server = FakeMemoryServer()
    clean = await replay_forgets(tmp_path, resolve_char_name=resolver(), client=server.client())
    # 坏的那份留着、记为未完成；读得出的日志照常重放完，不能被它一起卡住
    assert clean is False and broken.exists()
    assert not (tmp_path / "visit_revocations" / f"{rev_id}.json").exists()
    assert server.calls("scoped_forget")
