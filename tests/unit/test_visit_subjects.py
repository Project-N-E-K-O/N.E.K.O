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

"""Tests for visit identity derivations, recall subjects and the peer roster."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from main_logic.visit.subjects import (
    VISIT_MEMORY_PLATFORM,
    PeerRoster,
    RosterCorruptError,
    derive_pair_id,
    derive_peer_char_id,
    derive_person_id,
    derive_short_code,
    derive_vid,
    resolve_visit_recall_subjects,
)
from memory.scopes import MemorySubject

OWN_A = "a" * 24
OWN_B = "b" * 24
PEER_X = "1" * 24
PEER_Y = "2" * 24
TAG_1 = "f" * 32
TAG_2 = "e" * 32


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── 派生 ──


def test_pair_id_is_symmetric_and_pinned():
    assert derive_pair_id(OWN_A, PEER_X) == derive_pair_id(PEER_X, OWN_A)
    lo, hi = sorted([OWN_A, PEER_X])
    assert derive_pair_id(OWN_A, PEER_X) == _sha(f"{lo}|{hi}")[:24]
    assert len(derive_pair_id(OWN_A, PEER_X)) == 24


def test_person_id_is_directional_and_bound_to_own_account():
    pid = derive_person_id(OWN_A, PEER_X)
    assert pid == "p_" + _sha(f"{OWN_A}|{PEER_X}")[:24]
    assert pid != derive_person_id(OWN_B, PEER_X)
    assert pid != derive_person_id(PEER_X, OWN_A)


def test_peer_char_id_is_fixed_length_26():
    cid = derive_peer_char_id(PEER_X, TAG_1)
    assert cid == "c_" + _sha(f"{PEER_X}|{TAG_1}")[:24]
    assert len(cid) == 26


def test_vid_is_26_chars_in_trtc_charset():
    for role in ("host", "guest"):
        vid = derive_vid(role, PEER_X, "V" * 22)
        assert len(vid) == 26
        assert re.fullmatch(r"[a-zA-Z0-9_-]+", vid)
        assert vid == role[0] + "_" + _sha(f"{PEER_X}|{'V' * 22}")[:24]
    with pytest.raises(ValueError):
        derive_vid("visitor", PEER_X, "V" * 22)


def test_short_code_is_first_six_upper():
    assert derive_short_code("abcdef0123456789abcdef01") == "ABCDEF"


@pytest.mark.parametrize("bad", ["", None, 5])
def test_derivations_reject_missing_input(bad):
    with pytest.raises(ValueError):
        derive_pair_id(OWN_A, bad)
    with pytest.raises(ValueError):
        derive_peer_char_id(PEER_X, bad)


# ── resolve_visit_recall_subjects ──


def test_recall_subjects_order_and_shape():
    subjects = resolve_visit_recall_subjects(
        {"own_uid": OWN_A, "peer_uid": PEER_X, "peer_char_tag": TAG_1}
    )
    pair = derive_pair_id(OWN_A, PEER_X)
    cid = derive_peer_char_id(PEER_X, TAG_1)
    pid = derive_person_id(OWN_A, PEER_X)
    assert subjects == [
        {"subject_kind": "group_chat", "subject_id": f"{VISIT_MEMORY_PLATFORM}:{pair}"},
        {"subject_kind": "group_participant",
         "subject_id": f"{VISIT_MEMORY_PLATFORM}:{pair}:{cid}"},
        {"subject_kind": "participant", "subject_id": f"{VISIT_MEMORY_PLATFORM}:{pid}"},
    ]
    # 第三个必须是人级 participant，而不是 group_participant。
    assert subjects[2]["subject_kind"] == "participant"


def test_recall_subjects_match_memory_server_request_parsing():
    for wire in resolve_visit_recall_subjects(
        {"own_uid": OWN_A, "peer_uid": PEER_X, "peer_char_tag": TAG_1}
    ):
        # memory_server 的 MemorySubjectRequest.to_domain 用 create(kind, id, scope=None)。
        subject = MemorySubject.create(wire["subject_kind"], wire["subject_id"])
        assert subject.scope == f"{wire['subject_kind']}:{wire['subject_id']}"


@pytest.mark.parametrize(
    "state",
    [
        {"own_uid": OWN_A, "peer_uid": PEER_X},
        {"own_uid": OWN_A, "peer_uid": PEER_X, "peer_char_tag": ""},
        {"peer_uid": PEER_X, "peer_char_tag": TAG_1},
        {"own_uid": OWN_A, "peer_char_tag": TAG_1},
        {},
    ],
)
def test_recall_subjects_missing_input_is_empty(state):
    assert resolve_visit_recall_subjects(state) == []


def test_recall_subjects_accept_precomputed_peer_char_id():
    cid = derive_peer_char_id(PEER_X, TAG_1)
    a = resolve_visit_recall_subjects({"own_uid": OWN_A, "peer_uid": PEER_X, "peer_char_id": cid})
    b = resolve_visit_recall_subjects(
        {"own_uid": OWN_A, "peer_uid": PEER_X, "peer_char_tag": TAG_1}
    )
    assert a == b


# ── 名册 ──


async def _upsert(roster, peer, own_char, *, tag=TAG_1, now=100.0):
    pair = derive_pair_id(roster.own_uid, peer)
    cid = derive_peer_char_id(peer, tag)
    await roster.upsert(
        peer, own_char, pair_id=pair, peer_char_id=cid, char_tag=tag,
        char_display_name="Mimi", display_name="Alice", now=now,
    )
    return pair, cid


async def test_roster_upsert_and_remove(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair, cid = await _upsert(roster, PEER_X, "A")
    peer = await roster.get_peer(PEER_X)
    assert peer["short_code"] == PEER_X[:6].upper()
    assert peer["by_char"]["A"]["pairs"] == [pair]
    assert peer["by_char"]["A"]["chars"][cid]["char_tag"] == TAG_1
    # 重复 upsert 不重复登记 pair。
    await _upsert(roster, PEER_X, "A", now=200.0)
    peer = await roster.get_peer(PEER_X)
    assert peer["by_char"]["A"]["pairs"] == [pair]
    assert peer["first_seen"] == 100.0 and peer["last_seen"] == 200.0
    assert await roster.remove_char(PEER_X, "A") is True
    assert await roster.get_peer(PEER_X) is None
    assert await roster.remove_char(PEER_X, "A") is False


async def test_roster_is_partitioned_by_own_account(tmp_path):
    roster_a = PeerRoster(tmp_path, own_uid=OWN_A)
    pair, _cid = await _upsert(roster_a, PEER_X, "A")
    assert await roster_a.set_last_summary(
        PEER_X, "A", visit_id="V" * 22, ended_at=10.0, text="hi", pair_id=pair
    )
    roster_b = PeerRoster(tmp_path, own_uid=OWN_B)
    assert await roster_b.get_peer(PEER_X) is None
    assert await roster_b.list_peers() == {}
    assert await roster_b.get_last_summary(PEER_X, "A") is None
    assert await roster_b.get_char_entry(PEER_X, "A") is None
    subjects_b = await roster_b.expand_subjects(PEER_X, "A")
    assert all(pair not in s["subject_id"] for s in subjects_b)
    # B 写入自己的分区不影响 A。
    await _upsert(roster_b, PEER_Y, "A")
    roster_a2 = PeerRoster(tmp_path, own_uid=OWN_A)
    assert set(await roster_a2.list_peers()) == {PEER_X}
    assert (await roster_a2.get_last_summary(PEER_X, "A"))["text"] == "hi"
    data = json.loads((tmp_path / "visit_peers.json").read_text(encoding="utf-8"))
    assert set(data["accounts"]) == {OWN_A, OWN_B}


async def test_roster_separates_local_characters(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await _upsert(roster, PEER_X, "A")
    await _upsert(roster, PEER_X, "B")
    peer = await roster.get_peer(PEER_X)
    assert set(peer["by_char"]) == {"A", "B"}
    before_b = peer["by_char"]["B"]
    await roster.remove_char(PEER_X, "A")
    peer = await roster.get_peer(PEER_X)
    assert peer is not None
    assert set(peer["by_char"]) == {"B"}
    assert peer["by_char"]["B"] == before_b
    await roster.remove_char(PEER_X, "B")
    assert await roster.get_peer(PEER_X) is None


async def test_rename_char_moves_every_peer_and_is_reversible(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair_x, _ = await _upsert(roster, PEER_X, "A")
    await _upsert(roster, PEER_Y, "A")
    await _upsert(roster, PEER_Y, "B")
    await roster.set_last_summary(
        PEER_X, "A", visit_id="V" * 22, ended_at=5.0, text="sum", pair_id=pair_x
    )
    other = PeerRoster(tmp_path, own_uid=OWN_B)
    await _upsert(other, PEER_X, "A")
    snapshot = json.loads((tmp_path / "visit_peers.json").read_text(encoding="utf-8"))

    assert await roster.rename_char("A", "A2") == 3
    for r in (roster, other):
        for uid, peer in (await r.list_peers()).items():
            assert "A" not in peer["by_char"], uid
    assert set((await roster.get_peer(PEER_X))["by_char"]) == {"A2"}
    assert set((await roster.get_peer(PEER_Y))["by_char"]) == {"A2", "B"}
    assert (await roster.get_last_summary(PEER_X, "A2"))["text"] == "sum"
    assert await roster.get_last_summary(PEER_X, "A") is None
    # 幂等：再跑一次不动。
    assert await roster.rename_char("A", "A2") == 0

    await roster.rename_char("A2", "A")
    restored = json.loads((tmp_path / "visit_peers.json").read_text(encoding="utf-8"))
    assert restored == snapshot


async def test_expand_subjects_covers_every_peer_cat_of_one_character(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair, c_x = await _upsert(roster, PEER_X, "A", tag=TAG_1)
    _, c_y = await _upsert(roster, PEER_X, "A", tag=TAG_2)
    await _upsert(roster, PEER_X, "B", tag="d" * 32)
    subjects = await roster.expand_subjects(PEER_X, "A")
    pid = derive_person_id(OWN_A, PEER_X)
    assert subjects == [
        {"subject_kind": "group_chat", "subject_id": f"neko_visit:{pair}"},
        {"subject_kind": "group_participant", "subject_id": f"neko_visit:{pair}:{c_x}"},
        {"subject_kind": "group_participant", "subject_id": f"neko_visit:{pair}:{c_y}"},
        {"subject_kind": "participant", "subject_id": f"neko_visit:{pid}"},
    ]
    c_b = derive_peer_char_id(PEER_X, "d" * 32)
    assert all(c_b not in s["subject_id"] for s in subjects)


async def test_expand_subjects_merges_in_flight_visit(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair = derive_pair_id(OWN_A, PEER_X)
    cid = derive_peer_char_id(PEER_X, TAG_1)
    subjects = await roster.expand_subjects(PEER_X, "A", current=(pair, cid))
    assert [s["subject_kind"] for s in subjects] == [
        "group_chat", "group_participant", "participant",
    ]
    # 名册里已有同一对时不重复。
    await _upsert(roster, PEER_X, "A")
    again = await roster.expand_subjects(PEER_X, "A", current=(pair, cid))
    assert again == subjects


async def test_last_summary_follows_roster_entry(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair, _ = await _upsert(roster, PEER_X, "A")
    await _upsert(roster, PEER_X, "B")
    await _upsert(roster, PEER_Y, "A")
    assert await roster.set_last_summary(
        PEER_X, "A", visit_id="V" * 22, ended_at=50.0, text="went well", pair_id=pair
    )
    got = await roster.get_last_summary(PEER_X, "A")
    assert got == {"visit_id": "V" * 22, "ended_at": 50.0, "text": "went well"}
    assert await roster.get_last_summary(PEER_X, "B") is None
    assert await roster.get_last_summary(PEER_X, "C") is None
    assert await roster.get_last_summary(PEER_Y, "A") is None
    await roster.rename_char("A", "A2")
    assert await roster.get_last_summary(PEER_X, "A2") == got
    await roster.remove_char(PEER_X, "A2")
    assert await roster.get_last_summary(PEER_X, "A2") is None


async def test_set_last_summary_never_creates_entries(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair = derive_pair_id(OWN_A, PEER_X)
    assert not await roster.set_last_summary(
        PEER_X, "A", visit_id="V" * 22, ended_at=1.0, text="t", pair_id=pair
    )
    assert await roster.get_peer(PEER_X) is None
    await _upsert(roster, PEER_X, "A")
    other_pair = derive_pair_id(OWN_A, PEER_Y)
    assert not await roster.set_last_summary(
        PEER_X, "A", visit_id="V" * 22, ended_at=1.0, text="t", pair_id=other_pair
    )
    assert not await roster.set_last_summary(
        PEER_X, "B", visit_id="V" * 22, ended_at=1.0, text="t", pair_id=pair
    )
    peer = await roster.get_peer(PEER_X)
    assert set(peer["by_char"]) == {"A"}
    assert "last_summary" not in peer["by_char"]["A"]


async def test_set_last_summary_keeps_later_one(tmp_path):
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    pair, _ = await _upsert(roster, PEER_X, "A")
    assert await roster.set_last_summary(
        PEER_X, "A", visit_id="N" * 22, ended_at=200.0, text="newer", pair_id=pair
    )
    assert not await roster.set_last_summary(
        PEER_X, "A", visit_id="O" * 22, ended_at=100.0, text="older", pair_id=pair
    )
    assert (await roster.get_last_summary(PEER_X, "A"))["text"] == "newer"


async def test_unknown_top_level_keys_are_preserved(tmp_path):
    path = tmp_path / "visit_peers.json"
    path.write_text(
        json.dumps({"pending_rename": {"old": "A", "new": "A2"}, "accounts": {}}),
        encoding="utf-8",
    )
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    await _upsert(roster, PEER_X, "A")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["pending_rename"] == {"old": "A", "new": "A2"}


async def test_corrupt_roster_refuses_writes_but_reads_degrade(tmp_path):
    path = tmp_path / "visit_peers.json"
    path.write_text("{not json", encoding="utf-8")
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    assert await roster.get_last_summary(PEER_X, "A") is None
    with pytest.raises(RosterCorruptError):
        await _upsert(roster, PEER_X, "A")
    assert path.read_text(encoding="utf-8") == "{not json"
