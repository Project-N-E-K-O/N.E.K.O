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
        await run_revocation(log, rev_id, roster=roster, forget_subject=server.forget)
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
    assert await run_revocation(log, rev_id, roster=roster, forget_subject=server.forget)
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
    await run_revocation(log, rev_a, roster=roster, forget_subject=server.forget)
    peer = await roster.get_peer(PEER_X)
    assert peer is not None and set(peer["by_char"]) == {"B"}
    c_b = derive_peer_char_id(PEER_X, TAG_Y)
    assert all(c_b not in s["subject_id"] for s in server.calls)
    # B 下的清除照样能执行。
    plan_b = await plan_forget_person(roster, PEER_X, "B", CHAR_UID_B)
    assert any(c_b in s["subject_id"] for s in plan_b.subjects)
    rev_b = await log.open_plan(plan_b)
    assert rev_b != rev_a
    await run_revocation(log, rev_b, roster=roster, forget_subject=server.forget)
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
        await run_revocation(log, rev_id, roster=roster, forget_subject=server.forget)
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

    await run_revocation(log, rev_id, roster=roster, forget_subject=server.forget)
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
    extra = {"subject_kind": "group_chat", "subject_id": "neko_visit:extra"}
    await log.open(PEER_X, CHAR_UID_A, ["extra"], [extra])
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
        await run_revocation(log_a, rev_a, roster=roster_b, forget_subject=FakeMemoryServer().forget)
    await run_revocation(log_a, rev_a, roster=roster_a, forget_subject=FakeMemoryServer().forget)
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
                         void_pending=void)
    assert (await mine.read_state())["peer_uid"] is None
    assert (await mine.read_state())["pair_id"] is None
    assert (await theirs.read_state())["peer_uid"] == PEER_X
    assert voided == [rev_id]


async def test_mark_done_rejects_unknown_step(tmp_path):
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open(PEER_X, CHAR_UID_A, [], [], own_char="A")
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
        await run_revocation(log, rev_id, roster=roster, forget_subject=failing)
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


async def test_wipe_spool_stays_pending_when_a_state_file_is_unreadable(tmp_path):
    # 已结清的场次常只剩 state.json：它读不出来时 wipe_spool 不能记完成
    from main_logic.visit.spool import SpoolStateUnreadable

    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair_a, cid = await seed(roster, PEER_X, "A", TAG_X)
    sp = VisitSpool(tmp_path, "visit00000000000000009")
    await sp.write_state(new_state(own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                   pair_id=pair_a, peer_uid=PEER_X, peer_char_id=cid,
                                   memory_enabled=True))
    sp.state_path.write_text("{broken", encoding="utf-8")
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    rev_id = await log.open_plan(await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A))
    with pytest.raises(SpoolStateUnreadable):
        await run_revocation(log, rev_id, roster=roster,
                             forget_subject=FakeMemoryServer().forget)
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
