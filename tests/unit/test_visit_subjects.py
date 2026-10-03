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


async def test_expand_subjects_reads_the_roster_strictly(tmp_path):
    from main_logic.visit.subjects import RosterCorruptError

    roster = PeerRoster(tmp_path, own_uid="own_a")
    roster.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await roster.expand_subjects("peer_x", "A")
    with pytest.raises(RosterCorruptError):
        await roster.get_char_entry("peer_x", "A", strict=True)
    assert await roster.get_char_entry("peer_x", "A") is None   # 展示用途仍宽松


@pytest.mark.parametrize("damage", [
    lambda d: d.__setitem__("accounts", []),
    lambda d: d["accounts"].__setitem__("own_a", "x"),
    lambda d: d["accounts"]["own_a"].__setitem__("peers", 3),
    lambda d: d["accounts"]["own_a"]["peers"].__setitem__("peer_x", []),
    lambda d: d["accounts"]["own_a"]["peers"]["peer_x"].__setitem__("by_char", "x"),
    lambda d: d["accounts"]["own_a"]["peers"]["peer_x"]["by_char"].__setitem__("A", 1),
    lambda d: d["accounts"]["own_a"]["peers"]["peer_x"]["by_char"]["A"].__setitem__("pairs", "p"),
    lambda d: d["accounts"]["own_a"]["peers"]["peer_x"]["by_char"]["A"].__setitem__("chars", []),
])
async def test_strict_reads_reject_a_damaged_roster_structure(tmp_path, damage):
    # JSON 合法但结构坏了：严格读不能把它当成「没有条目」
    import json as _json

    from main_logic.visit.subjects import RosterCorruptError

    roster = PeerRoster(tmp_path, own_uid="own_a")
    await roster.upsert("peer_x", "A", pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                        char_tag="f" * 32, char_display_name="cat", now=1.0)
    data = _json.loads(roster.path.read_text(encoding="utf-8"))
    damage(data)
    roster.path.write_text(_json.dumps(data), encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await roster.expand_subjects("peer_x", "A")
    with pytest.raises(RosterCorruptError):
        await roster.get_char_entry("peer_x", "A", strict=True)


async def test_strict_reads_treat_missing_keys_as_absent(tmp_path):
    roster = PeerRoster(tmp_path, own_uid="own_a")
    await roster.upsert("peer_x", "A", pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                        char_tag="f" * 32, char_display_name="cat", now=1.0)
    assert await roster.get_char_entry("peer_y", "A", strict=True) is None
    assert await roster.get_char_entry("peer_x", "B", strict=True) is None
    other = PeerRoster(tmp_path, own_uid="own_b")
    assert await other.get_char_entry("peer_x", "A", strict=True) is None


@pytest.mark.parametrize("damage", [
    lambda e: e.__setitem__("pairs", ["p" * 24, 7]),
    lambda e: e.__setitem__("pairs", ["p" * 24, ""]),
    lambda e: e["chars"].__setitem__("", {}),
])
async def test_strict_reads_reject_damaged_pair_or_char_ids(tmp_path, damage):
    # 坏元素不能被静默过滤：那会让清除计划漏掉对应的 pair / 对方猫娘
    import json as _json

    from main_logic.visit.subjects import RosterCorruptError

    roster = PeerRoster(tmp_path, own_uid="own_a")
    await roster.upsert("peer_x", "A", pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                        char_tag="f" * 32, char_display_name="cat", now=1.0)
    data = _json.loads(roster.path.read_text(encoding="utf-8"))
    damage(data["accounts"]["own_a"]["peers"]["peer_x"]["by_char"]["A"])
    roster.path.write_text(_json.dumps(data), encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await roster.expand_subjects("peer_x", "A")


@pytest.mark.parametrize("damage", [
    lambda d: d["accounts"]["own_a"]["peers"].__setitem__("peer_x", []),
    lambda d: d["accounts"]["own_a"]["peers"]["peer_x"].__setitem__("by_char", []),
    lambda d: d["accounts"]["own_a"].__setitem__("peers", "x"),
    lambda d: d["accounts"]["own_a"]["peers"]["peer_x"].pop("by_char"),
])
async def test_remove_char_fails_closed_on_a_damaged_target(tmp_path, damage):
    import json as _json

    from main_logic.visit.subjects import RosterCorruptError

    roster = PeerRoster(tmp_path, own_uid="own_a")
    await roster.upsert("peer_x", "A", pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                        char_tag="f" * 32, char_display_name="cat", now=1.0)
    data = _json.loads(roster.path.read_text(encoding="utf-8"))
    damage(data)
    roster.path.write_text(_json.dumps(data), encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await roster.remove_char("peer_x", "A")


async def test_remove_char_on_an_absent_entry_is_a_no_op(tmp_path):
    roster = PeerRoster(tmp_path, own_uid="own_a")
    assert await roster.remove_char("peer_x", "A") is False
    await roster.upsert("peer_x", "A", pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                        char_tag="f" * 32, char_display_name="cat", now=1.0)
    assert await roster.remove_char("peer_x", "B") is False
    assert await roster.remove_char("peer_y", "A") is False


@pytest.mark.parametrize("damage", [
    lambda d: d.__setitem__("accounts", []),
    lambda d: d["accounts"].__setitem__("own_a", 5),
    lambda d: d["accounts"]["own_a"].__setitem__("peers", "x"),
])
async def test_upsert_refuses_to_rebuild_damaged_containers(tmp_path, damage):
    # 类型坏了的容器不能被重建成 {}：下一次原子写会把可恢复的数据永久冲掉
    import json as _json

    from main_logic.visit.subjects import RosterCorruptError

    roster = PeerRoster(tmp_path, own_uid="own_a")
    await roster.upsert("peer_x", "A", pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                        char_tag="f" * 32, char_display_name="cat", now=1.0)
    data = _json.loads(roster.path.read_text(encoding="utf-8"))
    damage(data)
    before = _json.dumps(data)
    roster.path.write_text(before, encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await roster.upsert("peer_y", "A", pair_id=derive_pair_id("own_a", "peer_y"), peer_char_id="c_" + "2" * 24,
                            char_tag="e" * 32, char_display_name="cat", now=2.0)
    assert roster.path.read_text(encoding="utf-8") == before


async def test_upsert_refuses_a_malformed_existing_peer_row(tmp_path):
    import json as _json

    from main_logic.visit.subjects import RosterCorruptError

    roster = PeerRoster(tmp_path, own_uid="own_a")
    await roster.upsert("peer_x", "A", pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                        char_tag="f" * 32, char_display_name="cat", now=1.0)
    data = _json.loads(roster.path.read_text(encoding="utf-8"))
    data["accounts"]["own_a"]["peers"]["peer_x"] = ["damaged"]
    roster.path.write_text(_json.dumps(data), encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await roster.upsert("peer_x", "A", pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                            char_tag="f" * 32, char_display_name="cat", now=2.0)


@pytest.mark.parametrize("damage", [
    lambda d: d.__setitem__("accounts", []),
    lambda d: d["accounts"].__setitem__("other", 3),
    lambda d: d["accounts"].__setitem__("other", {"peers": "x"}),
    lambda d: d["accounts"]["own_a"]["peers"].__setitem__("peer_z", {"no": "by_char"}),
])
async def test_rename_char_fails_on_any_malformed_partition(tmp_path, damage):
    import json as _json

    from main_logic.visit.subjects import RosterCorruptError

    roster = PeerRoster(tmp_path, own_uid="own_a")
    await roster.upsert("peer_x", "A", pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                        char_tag="f" * 32, char_display_name="cat", now=1.0)
    data = _json.loads(roster.path.read_text(encoding="utf-8"))
    damage(data)
    roster.path.write_text(_json.dumps(data), encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await roster.rename_char("A", "A2")


async def test_upsert_refuses_a_malformed_pairs_list(tmp_path):
    import json as _json

    from main_logic.visit.subjects import RosterCorruptError

    roster = PeerRoster(tmp_path, own_uid="own_a")
    await roster.upsert("peer_x", "A", pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                        char_tag="f" * 32, char_display_name="cat", now=1.0)
    data = _json.loads(roster.path.read_text(encoding="utf-8"))
    data["accounts"]["own_a"]["peers"]["peer_x"]["by_char"]["A"]["pairs"] = "p" * 24
    before = _json.dumps(data)
    roster.path.write_text(before, encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await roster.upsert("peer_x", "A", pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                            char_tag="f" * 32, char_display_name="cat", now=2.0)
    assert roster.path.read_text(encoding="utf-8") == before


async def test_rename_char_refuses_a_malformed_target_entry(tmp_path):
    import json as _json

    from main_logic.visit.subjects import RosterCorruptError

    roster = PeerRoster(tmp_path, own_uid="own_a")
    await roster.upsert("peer_x", "A", pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                        char_tag="f" * 32, char_display_name="cat", now=1.0)
    data = _json.loads(roster.path.read_text(encoding="utf-8"))
    data["accounts"]["own_a"]["peers"]["peer_x"]["by_char"]["A2"] = "damaged"
    before = _json.dumps(data)
    roster.path.write_text(before, encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await roster.rename_char("A", "A2")
    assert roster.path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("side,damage", [
    ("A", {"pairs": "p" * 24}),
    ("A2", {"pairs": "q" * 24}),
    ("A", {"pairs": [7]}),
    ("A2", {"chars": []}),
    ("A", {"chars": {"c_" + "1" * 24: "damaged"}}),
    ("A2", {"chars": {"c_" + "1" * 24: 3}}),
    ("A", {"last_summary": "damaged"}),
    ("A2", {"last_summary": ["damaged"]}),
    ("A", {"chars": {"c_" + "1" * 24: {"last_seen": "9"}}}),
    ("A2", {"chars": {"c_" + "1" * 24: {"last_seen": float("nan")}}}),
    ("A", {"last_summary": {"visit_id": "v", "ended_at": "10", "text": "t"}}),
    ("A2", {"last_summary": {"visit_id": "v", "ended_at": True, "text": "t"}}),
])
async def test_rename_char_refuses_malformed_nested_data(tmp_path, side, damage):
    # 两边都是 object 但嵌套数据坏了：合并会把字符串 pairs 拆成单个字符写回去
    import json as _json

    from main_logic.visit.subjects import RosterCorruptError

    roster = PeerRoster(tmp_path, own_uid="own_a")
    for char in ("A", "A2"):
        await roster.upsert("peer_x", char, pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                            char_tag="f" * 32, char_display_name="cat", now=1.0)
    data = _json.loads(roster.path.read_text(encoding="utf-8"))
    data["accounts"]["own_a"]["peers"]["peer_x"]["by_char"][side].update(damage)
    before = _json.dumps(data)
    roster.path.write_text(before, encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await roster.rename_char("A", "A2")
    assert roster.path.read_text(encoding="utf-8") == before


def test_path_locks_are_shared_while_held_and_released_when_idle(tmp_path):
    import gc

    from main_logic.visit import subjects as subjects_mod
    from main_logic.visit.subjects import path_lock

    target = tmp_path / "x.json"
    key = str(target.resolve())
    with path_lock(target) as held:
        assert path_lock(target) is held           # 持有期间拿到的是同一把
        assert path_lock(target).locked()
    del held
    gc.collect()
    assert key not in subjects_mod._PATH_LOCKS     # 闲置后登记自动释放


@pytest.mark.parametrize("damage", [
    {"chars": {"c_" + "1" * 24: "damaged"}},
    {"chars": {"c_" + "1" * 24: 3}},
    {"pairs": ["p" * 24, 7]},
])
async def test_upsert_refuses_malformed_existing_character_records(tmp_path, damage):
    import json as _json

    from main_logic.visit.subjects import RosterCorruptError

    roster = PeerRoster(tmp_path, own_uid="own_a")
    await roster.upsert("peer_x", "A", pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                        char_tag="f" * 32, char_display_name="cat", now=1.0)
    data = _json.loads(roster.path.read_text(encoding="utf-8"))
    data["accounts"]["own_a"]["peers"]["peer_x"]["by_char"]["A"].update(damage)
    before = _json.dumps(data)
    roster.path.write_text(before, encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await roster.upsert("peer_x", "A", pair_id=derive_pair_id("own_a", "peer_x"), peer_char_id="c_" + "1" * 24,
                            char_tag="f" * 32, char_display_name="cat", now=2.0)
    assert roster.path.read_text(encoding="utf-8") == before


async def test_upsert_refuses_a_pair_id_not_derived_from_the_pair(tmp_path):
    # 名册里存了异值 pair，之后的撤销计划会被身份绑定校验拒绝，这个人就清除不了
    roster = PeerRoster(tmp_path, own_uid="own_a")
    with pytest.raises(ValueError):
        await roster.upsert("peer_x", "A", pair_id="p" * 24, peer_char_id="c_" + "1" * 24,
                            char_tag="f" * 32, char_display_name="cat", now=1.0)
    assert not roster.path.exists()
