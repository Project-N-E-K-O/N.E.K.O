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
    # The third forget returns 502: by_char['A'] remains and the log is retained, while the previous summary was removed in step one.
    entry = await roster.get_char_entry(PEER_X, "A")
    assert entry is not None and "last_summary" not in entry
    record = await log.load(rev_id)
    assert record is not None
    assert STEP_REMOVE_CHAR not in record["done_steps"]
    assert record["done_steps"] == [STEP_CLEAR_LAST_SUMMARY] + [
        forget_step_id(s) for s in plan.subjects[:2]
    ]

    # Delete only after replay completes the operation.
    assert await run_revocation(log, rev_id, roster=roster, forget_subject=server.forget, void_pending=_no_void, own_char="A")
    assert await roster.get_char_entry(PEER_X, "A") is None
    assert await log.load(rev_id) is None
    unique = {(s["subject_kind"], s["subject_id"]) for s in server.calls}
    assert unique == {(s["subject_kind"], s["subject_id"]) for s in plan.subjects}
    assert len(unique) == 4
    kinds = sorted(s["subject_kind"] for s in plan.subjects)
    assert kinds == ["group_chat", "group_participant", "group_participant", "participant"]
    # The roster entry is present every time forget runs (remove_char always happens after all forget operations).
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
    # Forget under B should still execute normally.
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

    # The other person later brings another cat: the second forget is merged into the same log.
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
    extra = group_participant_subject(pair, "c_" + "9" * 24)   # The other person brings a new cat
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
    # ScopedMemoryClient.post_forget returns False on failure (does not raise): do not mark it complete
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
    # An unreadable revocation log must not be silently skipped: startup replay and the session-creation gate rely on the list to determine "whether a forget is in progress"
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
    # If a log such as steps:[] is accepted, replay deletes the log without forgetting anything
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
    # If the roster cannot be read, do not plan as if it were empty: that would clear only the person-level subject and miss every pair and the other person's cat
    from main_logic.visit.subjects import RosterCorruptError

    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    roster.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)


async def test_wipe_spool_stays_pending_when_a_state_file_is_unreadable(tmp_path, monkeypatch):
    # Settled sessions often have only state.json left: if it is temporarily unreadable (locked), wipe_spool must not mark the operation complete.
    # A corrupt file cannot be used by anyone; forget can delete it directly (see test_visit_spool_recovery)
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
    # Only .jsonl remains and its header is corrupt: do not treat it as "not this pair" and mark wipe_spool complete
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
    # An extra scope would be forwarded unchanged to /scoped_forget and change the deletion scope
    await _damaged_log(tmp_path, lambda r: r["subjects"][0].__setitem__("scope", "other"))


async def test_unrelated_extra_pair_id_fails_closed(tmp_path):
    # An unrelated extra pair would make wipe_spool erase someone else's session
    await _damaged_log(tmp_path, lambda r: r["pair_ids"].append("q" * 24))


async def test_pairs_must_belong_to_the_logs_own_identities(tmp_path):
    # Replace the pair with someone else's in both pair_ids and subjects: internally consistent, but it does not belong to the person named in this log
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
    # qq:<pair> passes pair validation but /scoped_forget removes QQ memories based on the platform prefix
    def to_qq(record):
        for s in record["subjects"]:
            if s["subject_kind"] == "group_chat":
                s["subject_id"] = "qq:" + s["subject_id"].split(":", 1)[1]
        from main_logic.visit.forget import build_steps
        record["steps"] = build_steps(record["subjects"])
        record["done_steps"] = []

    await _damaged_log(tmp_path, to_qq)


async def test_a_plan_without_the_person_subject_fails_closed(tmp_path):
    # subjects:[] / pair_ids:[] / steps:build_steps([]) is internally consistent, but replay would remove only the roster entry and not forget the memory
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
    # Two cats from the other person share one pair; the log can still pass validation if one group_participant is missing;
    # the roster is still available, and reconciliation before replay restores it, so that memory is also forgotten
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
    assert await log.load(rev_id) is not None          # The log is internally consistent on its own

    server = FakeMemoryServer(roster, PEER_X, "A")
    assert await run_revocation(log, rev_id, roster=roster, forget_subject=server.forget, void_pending=_no_void, own_char="A") is True
    assert dropped in server.calls
    # remove_char still follows every forget operation: the roster entry exists during each forget
    assert all(server.entry_present_at_each_call)
    assert await roster.get_char_entry(PEER_X, "A") is None


async def test_an_inconsistent_plan_is_refused_before_it_is_written(tmp_path):
    # A log that cannot be read after writing must not be persisted: it cannot be executed and would make global reads fail closed
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
    # memory_server returns 422 every time for unknown kind / empty component / non-canonical escaping; persisting such a log would leave the gate closed forever
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair, _ = await seed(roster, PEER_X, "A", TAG_X)
    plan = await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)
    bogus = make_bogus(pair)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    with pytest.raises(ValueError):
        await log.open(PEER_X, CHAR_UID_A, plan.pair_ids, list(plan.subjects) + [bogus])
    assert not log.path_for(plan.revocation_id).exists()


async def test_void_pending_is_a_required_step_callback(tmp_path):
    # A missing callback must not let void_pending silently mark completion and delete the log
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
    # Marking completion out of order writes non-prefix done_steps, making the whole log unreadable afterward
    from main_logic.visit.forget import STEP_WIPE_SPOOL as _WIPE

    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await seed(roster, PEER_X, "A", TAG_X)
    log = RevocationLog(tmp_path, own_uid=OWN_A)
    plan = await plan_forget_person(roster, PEER_X, "A", CHAR_UID_A)
    rev_id = await log.open_plan(plan)
    with pytest.raises(ValueError):
        await log.mark_done(rev_id, _WIPE)
    await log.mark_done(rev_id, plan.steps[0])
    await log.mark_done(rev_id, plan.steps[0])          # Duplicate record that is already complete: idempotent
    record = await log.load(rev_id)
    assert record["done_steps"] == [plan.steps[0]]
    assert await log.list_open() != []


async def test_forget_planning_reads_the_roster_once(tmp_path, monkeypatch):
    # An upsert between two reads could make subjects and pair_ids come from different snapshots; planning must read only once
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
    # Forget failed midway and the character was renamed before replay: the old name no longer finds the entry, yet the log would be marked complete while the summary remains unchanged
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
    # A deeply nested log or wrong version raises RevocationLogUnreadable like any other unreadable log; do not replay it
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


# ── Forget generations and forget-intent sentinels (PR-08) ───────────────────


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
    # The revocation-log listing does not treat sentinels as logs
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
    server.tombstones = {subject_key(person): 5}         # Local generation was reset, but the server tombstone remains at 5
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=server.client())
    assert outcome.done
    sent = [body for body in server.calls("scoped_forget") if body["subject"]["subject_kind"] == "participant"]
    # Advance to the server fence before incrementing: the emitted generation is greater than the existing tombstone and will not be skipped as an already-erased replay
    assert sent and sent[0]["forget_epoch"] == 6


# ── Invalidation steps: unreadable / schema-invalid state follows the same policy as identity lookup and erasure (PR #3293 review) ──


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
    raw["field_from_a_newer_version"] = 1                       # Parseable, but not valid under the current schema
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
    # A state file for another character has been unreadable for a long time (held by antivirus): the header clearly belongs to someone else, so do not block this forget
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
    {},                                                          # Still identifies this pair
    {"pair_id": None, "peer_uid": None, "peer_char_id": None},   # Identity has been erased
], ids=["names-pair", "wiped"])
async def test_void_stays_pending_on_a_schema_invalid_state_that_may_be_this_persons(tmp_path, changes):
    from main_logic.visit.forget_runner import default_void_pending
    from main_logic.visit.spool import SpoolStateUnreadable

    mine = await _visit(tmp_path, 3)
    _make_schema_invalid(mine, **changes)
    before = mine.state_path.read_bytes()
    # After downgrade, a state written by a newer version is readable but debrief has not been written yet: do not skip it as corrupt (the user could still "save as diary" after upgrading again);
    # if it cannot be invalidated, do not settle this log yet
    with pytest.raises(SpoolStateUnreadable):
        await default_void_pending(tmp_path)(_void_record([derive_pair_id(OWN_A, PEER_X)]))
    assert mine.state_path.read_bytes() == before


@pytest.mark.parametrize("kw,changes", [
    ({"own_char_uid": CHAR_UID_B}, {}),
    ({"own_uid": OWN_B}, {}),
    ({"peer": PEER_Y}, {}),
    ({}, {"debrief_choice": "forget"}),                         # Final result already exists: nothing to invalidate
], ids=["other-char", "other-account", "other-pair", "final-choice"])
async def test_void_skips_a_schema_invalid_state_that_is_clearly_not_voidable(tmp_path, kw, changes):
    from main_logic.visit.forget_runner import default_void_pending

    sp = await _visit(tmp_path, 4, **kw)
    _make_schema_invalid(sp, **changes)
    await default_void_pending(tmp_path)(_void_record([derive_pair_id(OWN_A, PEER_X)]))


async def test_void_skips_a_corrupt_state(tmp_path):
    from main_logic.visit.forget_runner import default_void_pending

    sp = await _visit(tmp_path, 5)
    sp.state_path.write_text("[1, 2]", encoding="utf-8")         # Parseable but not an object: unusable by anyone
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
    # Invalidate readable sessions normally; do not defer them to the next replay just because they appear later
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
    broken.write_text("{torn", encoding="utf-8")                    # Another pair's log is corrupt
    server = FakeMemoryServer()
    clean = await replay_forgets(tmp_path, resolve_char_name=resolver(), client=server.client())
    # Keep the corrupt file and mark it incomplete; replay readable logs normally without letting the corrupt one block them
    assert clean is False and broken.exists()
    assert not (tmp_path / "visit_revocations" / f"{rev_id}.json").exists()
    assert server.calls("scoped_forget")


async def test_quarantine_rereads_under_the_file_lock_and_spares_a_record_fixed_meanwhile(tmp_path):
    # Use the same file lock as writes: an entry that was unreadable during directory listing but successfully written by the time the lock is acquired must not be moved
    import asyncio
    import threading

    from main_logic.visit.forget import ClearingSentinels, quarantine_unreadable_records
    from main_logic.visit.subjects import path_lock

    store = ClearingSentinels(tmp_path)
    doc = await store.create(own_uid=OWN_A, scope="chars", own_char_uids=[CHAR_UID_A])
    path = store.path_for(doc["op_id"])
    good = path.read_text(encoding="utf-8")
    path.write_text("{torn", encoding="utf-8")
    held, release = threading.Event(), threading.Event()

    def writer():
        with path_lock(path):
            held.set()
            release.wait(5)
            path.write_text(good, encoding="utf-8")

    thread = threading.Thread(target=writer)
    thread.start()
    held.wait(5)
    task = asyncio.ensure_future(quarantine_unreadable_records(tmp_path))
    await asyncio.sleep(0.05)
    assert not task.done()
    release.set()
    moved, failed = await task
    thread.join(5)
    assert (moved, failed) == ([], 0)
    assert path.read_text(encoding="utf-8") == good
    assert await store.list_open() == [doc]


async def test_quarantine_rereads_once_after_a_transient_os_error(tmp_path, monkeypatch):
    # A single transient read error (lock contention / EIO) must not cause readable logs and forget operations to be abandoned; quarantine entries that remain unreadable
    from main_logic.visit.forget import ClearingSentinels, quarantine_unreadable_records

    monkeypatch.setattr("main_logic.visit.forget._QUARANTINE_REREAD_DELAY_S", 0)
    store = ClearingSentinels(tmp_path)
    doc = await store.create(own_uid=OWN_A, scope="chars", own_char_uids=[CHAR_UID_A])
    real_read = ClearingSentinels._read.__func__
    calls = []

    def flaky(cls, path, op_id):
        calls.append(op_id)
        if len(calls) == 1:
            raise PermissionError("sharing violation")
        return real_read(cls, path, op_id)

    monkeypatch.setattr(ClearingSentinels, "_read", classmethod(flaky))
    assert await quarantine_unreadable_records(tmp_path) == ([], 0)
    assert len(calls) == 2 and await store.list_open() == [doc]

    def broken(cls, path, op_id):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(ClearingSentinels, "_read", classmethod(broken))
    moved, failed = await quarantine_unreadable_records(tmp_path)
    assert failed == 0 and [(m.kind, m.error) for m in moved] == [("clearing_sentinel", "OSError")]
    assert not store.path_for(doc["op_id"]).exists()


async def test_quarantine_counts_a_symlink_loop_as_failed(tmp_path, monkeypatch):
    from main_logic.visit.forget import quarantine_unreadable_records

    directory = tmp_path / "visit_revocations"
    directory.mkdir()
    (directory / f"{'0' * 32}.json").write_text("{torn", encoding="utf-8")

    def looping(_path):
        raise RuntimeError("Symlink loop")

    monkeypatch.setattr("main_logic.visit.forget.path_lock", looping)
    assert await quarantine_unreadable_records(tmp_path) == ([], 1)
    assert (directory / f"{'0' * 32}.json").exists()
