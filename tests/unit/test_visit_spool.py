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

"""Tests for the per-visit spool and its canonical ``state.json``."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import time

import pytest

import config.visit_settings as visit_settings
from main_logic.visit import spool as spool_mod
from main_logic.visit.subjects import derive_pair_id, derive_peer_char_id
from main_logic.visit.spool import (
    LINE_SPEAKERS,
    SpoolLineTooLarge,
    SpoolStateError,
    VisitSpool,
    encode_spool_line,
    is_digestable,
    new_debrief_writes,
    new_state,
    validate_header,
    validate_state,
)

DAY = 86400.0
NOW = time.time()


def vid(n: int) -> str:
    return f"visit{n:017d}"


PAIR1 = derive_pair_id("own_a", "peer1")
PAIR2 = derive_pair_id("own_a", "peer2")


def header(visit_id: str, *, own_char="A", own_char_uid="uid_a", pair_id=PAIR1,
           peer_uid="peer1", peer_char_id=None) -> dict:
    if peer_char_id is None:
        peer_char_id = derive_peer_char_id(peer_uid, "f" * 32)
    return {
        "v": 1,
        "visit_id": visit_id,
        "role": "host",
        "own_uid": "own_a",
        "own_char": own_char,
        "own_char_uid": own_char_uid,
        "pair_id": pair_id,
        "peer_uid": peer_uid,
        "peer_char_id": peer_char_id,
        "peer_char_tag": "f" * 32,
        "started_at": NOW,
        "lang": "zh-CN",
    }


def line(lp: int, text: str = "hello", speaker: str = "own_cat") -> dict:
    return {"lp": lp, "side": "host", "ts": NOW + lp, "from": speaker, "text": text}


def state_for(*, own_char="A", own_char_uid="uid_a", pair_id=PAIR1, peer_uid="peer1",
              memory_enabled=True) -> dict:
    return new_state(
        own_uid="own_a", own_char=own_char, own_char_uid=own_char_uid, pair_id=pair_id,
        peer_uid=peer_uid, peer_char_id=derive_peer_char_id(peer_uid, "f" * 32),
        memory_enabled=memory_enabled,
    )


def settled(state: dict) -> dict:
    state = dict(state)
    state["digest_writes"] = {
        "0": {"requested_at": NOW, "through_lp": 9, "group": {"0": True},
              "segments": {"0": True}},
    }
    state["digested_through_lp"] = 9
    state["digest_runs"] = 1
    state["last_summary_done"] = True
    return state


async def open_spool(tmp_path, visit_id, **kw) -> VisitSpool:
    sp = VisitSpool(tmp_path, visit_id)
    await sp.open(header(visit_id, **kw), now=NOW)
    return sp


# ── Writing and reading back ──


async def test_spool_lives_under_config_dir(tmp_path):
    sp = VisitSpool(tmp_path, vid(1))
    base = (tmp_path / "visit_spool").resolve()
    assert sp.jsonl_path == base / f"{vid(1)}.jsonl"
    assert sp.state_path == base / f"{vid(1)}.state.json"
    assert "memory" not in sp.jsonl_path.relative_to(tmp_path.resolve()).parts


async def test_roundtrip_header_and_lines(tmp_path):
    sp = await open_spool(tmp_path, vid(1))
    for i, who in enumerate(LINE_SPEAKERS):
        await sp.append(line(i + 1, f"line {i}", who))
    await sp.close()
    got = await sp.read_back()
    assert got.header == header(vid(1))
    assert [entry["text"] for entry in got.lines] == [f"line {i}" for i in range(4)]
    assert got.dropped_lines == 0


async def test_quotes_and_backslashes_roundtrip_byte_exact(tmp_path):
    text = ('"\\' * 2048)
    assert len(text.encode("utf-8")) == 4096
    sp = await open_spool(tmp_path, vid(1))
    await sp.append(line(1, text))
    await sp.close()
    got = await sp.read_back()
    assert got.lines[0]["text"].encode("utf-8") == text.encode("utf-8")


async def test_oversized_line_raises_instead_of_truncating(tmp_path):
    sp = await open_spool(tmp_path, vid(1))
    big = dict(line(1), ln="x" * (visit_settings.VISIT_SPOOL_LINE_MAX_BYTES + 1))
    with pytest.raises(SpoolLineTooLarge):
        await sp.append(big)
    with pytest.raises(ValueError):
        await sp.append(line(2, "x" * 4097))
    await sp.close()
    got = await sp.read_back()
    assert got.lines == []


async def test_unknown_speaker_rejected(tmp_path):
    sp = await open_spool(tmp_path, vid(1))
    with pytest.raises(ValueError):
        await sp.append(line(1, speaker="narrator"))
    await sp.close()


async def test_crash_partial_tail_is_dropped(tmp_path):
    sp = await open_spool(tmp_path, vid(1))
    for i in range(5):
        await sp.append(line(i + 1, f"t{i}"))
    await sp.close()
    # Simulate kill -9: the final line is only half written.
    data = sp.jsonl_path.read_bytes()
    sp.jsonl_path.write_bytes(data[: len(data) - 7])
    got = await VisitSpool(tmp_path, vid(1)).read_back()
    assert [entry["text"] for entry in got.lines] == ["t0", "t1", "t2", "t3"]
    assert got.dropped_lines == 1
    assert got.header["visit_id"] == vid(1)


async def test_concurrent_appends_keep_call_order(tmp_path, monkeypatch):
    sp = await open_spool(tmp_path, vid(1))
    original = VisitSpool._write_all
    calls = {"n": 0}

    def slow_first_writes(fd, data):
        # Deliberately slow the earlier write: only a single writer thread can preserve call order on disk.
        calls["n"] += 1
        if calls["n"] <= 5:
            time.sleep(0.02 * (6 - calls["n"]))
        original(fd, data)

    monkeypatch.setattr(VisitSpool, "_write_all", staticmethod(slow_first_writes))
    await asyncio.gather(*(sp.append(line(i, f"n{i}")) for i in range(200)))
    monkeypatch.setattr(VisitSpool, "_write_all", staticmethod(original))
    await sp.close()
    got = await sp.read_back()
    assert [entry["lp"] for entry in got.lines] == list(range(200))


async def test_open_refuses_existing_spool(tmp_path):
    sp = await open_spool(tmp_path, vid(1))
    await sp.close()
    with pytest.raises(FileExistsError):
        await VisitSpool(tmp_path, vid(1)).open(header(vid(1)), now=NOW)


async def test_open_rejects_header_for_other_visit(tmp_path):
    with pytest.raises(ValueError):
        await VisitSpool(tmp_path, vid(1)).open(header(vid(2)), now=NOW)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
async def test_files_are_owner_only(tmp_path):
    sp = await open_spool(tmp_path, vid(1))
    await sp.close()
    await sp.write_state(state_for())
    for path in (sp.jsonl_path, sp.state_path):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


async def test_fsync_cadence_is_30_seconds(tmp_path):
    sp = await open_spool(tmp_path, vid(1))
    assert not sp.fsync_due(NOW + 100)  # No new rows.
    await sp.append(line(1))
    assert not sp.fsync_due(NOW + 29.9)
    assert sp.fsync_due(NOW + 30)
    await sp.fsync(NOW + 30)
    assert not sp.fsync_due(NOW + 45)
    await sp.append(line(2))
    assert not sp.fsync_due(NOW + 59.9)
    assert sp.fsync_due(NOW + 60)
    await sp.close()
    assert not sp.fsync_due(NOW + 1000)


# ── state.json canonical schema ──


CANONICAL_FIELDS = {
    # visit_id binds the visit and must match the filename when read (replaced / copied state.json is untrusted).
    "visit_id",
    # own_uid: local-side community account partition (derive person-level subjects for crash recovery; do not erase on 'clear this person').
    "own_uid", "own_char", "own_char_uid", "pair_id", "peer_uid", "peer_char_id",
    "digested_through_lp", "digest_runs", "finalized", "debrief_choice",
    "debrief_pending", "debrief_writes", "debrief_retry", "debrief_commit_error",
    "debrief_chip_pending",
    "last_summary_done", "memory_enabled", "digest_writes",
}


async def test_marking_the_debrief_pending_never_replaces_a_newer_choice(tmp_path):
    sp = VisitSpool(tmp_path, vid(8))
    await sp.write_state(state_for())
    await sp.mark_forget()                          # The chip appears first; the user already chose 'do not remember'.
    state = await sp.mark_debrief_pending()                   # Late 'pending choice' marker.
    assert state["debrief_choice"] == "forget" and state["debrief_chip_pending"] is False
    sp2 = VisitSpool(tmp_path, vid(9))
    await sp2.write_state(state_for())
    state = await sp2.mark_debrief_pending()                  # No choice yet: mark 'later' normally, with the chip pending.
    assert state["debrief_choice"] == "ask_later" and state["debrief_chip_pending"] is True


async def test_delete_peer_fields_keeps_own_account(tmp_path):
    sp = VisitSpool(tmp_path, vid(7))
    await sp.write_state(state_for())
    await sp.delete_peer_fields()
    state = await sp.read_state()
    assert state["own_uid"] == "own_a"
    assert state["peer_uid"] is None and state["pair_id"] is None


async def test_state_field_set_is_exactly_canonical(tmp_path):
    sp = VisitSpool(tmp_path, vid(1))
    await sp.write_state(state_for())
    on_disk = json.loads(sp.state_path.read_text(encoding="utf-8"))
    assert set(on_disk) == CANONICAL_FIELDS
    assert on_disk["debrief_writes"] == {
        "facts": False, "cache": False, "facts_written": 0, "facts_unconfirmed": False,
        "cache_unconfirmed": False, "facts_inflight": False, "cache_inflight": False,
    }
    assert on_disk["debrief_retry"] is None and on_disk["debrief_commit_error"] is None
    assert on_disk["debrief_pending"] is None


@pytest.mark.parametrize("missing", sorted(CANONICAL_FIELDS))
async def test_state_missing_field_rejected(tmp_path, missing):
    state = state_for()
    del state[missing]
    with pytest.raises(SpoolStateError):
        await VisitSpool(tmp_path, vid(1)).write_state(state)


async def test_state_extra_field_rejected(tmp_path):
    state = dict(state_for(), reports=[])
    with pytest.raises(SpoolStateError):
        await VisitSpool(tmp_path, vid(1)).write_state(state)


@pytest.mark.parametrize("choice", [None, "ask_later", "diary", "forget", "abandoned"])
async def test_state_allowed_choices(tmp_path, choice):
    state = dict(state_for(), debrief_choice=choice)
    if choice == "diary":
        state["debrief_writes"] = dict(new_debrief_writes(), facts=True, cache=True)
    await VisitSpool(tmp_path, vid(1)).write_state(state)


@pytest.mark.parametrize("choice", ["preview", "maybe", "committing", ""])
async def test_state_choice_outside_enum_rejected(tmp_path, choice):
    with pytest.raises(SpoolStateError):
        await VisitSpool(tmp_path, vid(1)).write_state(dict(state_for(), debrief_choice=choice))


async def test_committing_requires_persisted_pending(tmp_path):
    sp = VisitSpool(tmp_path, vid(1))
    with pytest.raises(SpoolStateError):
        await sp.write_state(dict(state_for(), debrief_choice="committing:diary"))
    with pytest.raises(SpoolStateError):
        await sp.write_state(dict(
            state_for(), debrief_choice="committing:diary",
            debrief_pending={"diary": "", "facts": []},
        ))
    await sp.write_state(dict(
        state_for(), debrief_choice="committing:diary",
        debrief_pending={"diary": "today", "facts": ["f1"]},
    ))


async def test_generating_requires_empty_pending(tmp_path):
    sp = VisitSpool(tmp_path, vid(1))
    await sp.write_state(dict(state_for(), debrief_choice="generating:diary"))
    with pytest.raises(SpoolStateError):
        await sp.write_state(dict(
            state_for(), debrief_choice="generating:diary",
            debrief_pending={"diary": "x", "facts": []},
        ))


async def test_read_state_validates(tmp_path):
    sp = VisitSpool(tmp_path, vid(1))
    assert await sp.read_state() is None
    sp.state_path.parent.mkdir(parents=True)
    sp.state_path.write_text(
        json.dumps(dict(state_for(), debrief_choice="bogus")), encoding="utf-8"
    )
    with pytest.raises(SpoolStateError):
        await sp.read_state()


# ── is_digestable ──


def test_is_digestable_reads_only_the_frozen_visit_flag(monkeypatch):
    lines = [line(i, speaker=who) for i, who in enumerate(LINE_SPEAKERS)]
    on = state_for(memory_enabled=True)
    off = state_for(memory_enabled=False)
    # Reverse the current configuration after opening; this visit's decision remains unchanged.
    monkeypatch.setattr(visit_settings, "VISIT_MEMORY_DEFAULT", False)
    if hasattr(spool_mod, "VISIT_MEMORY_DEFAULT"):
        monkeypatch.setattr(spool_mod, "VISIT_MEMORY_DEFAULT", False)
    assert [entry for entry in lines if is_digestable(on)] == lines
    monkeypatch.setattr(visit_settings, "VISIT_MEMORY_DEFAULT", True)
    if hasattr(spool_mod, "VISIT_MEMORY_DEFAULT"):
        monkeypatch.setattr(spool_mod, "VISIT_MEMORY_DEFAULT", True)
    assert [entry for entry in lines if is_digestable(off)] == []


# ── forget / delete_peer_fields ──


async def test_forget_deletes_spool_when_region_digest_done(tmp_path):
    sp = await open_spool(tmp_path, vid(1))
    await sp.append(line(1))
    await sp.close()
    await sp.write_state(settled(state_for()))
    assert await sp.mark_forget() is True
    assert not sp.jsonl_path.exists()
    state = await sp.read_state()
    assert state["debrief_choice"] == "forget"
    # Keep state.json (sweep removes it after 7 days).
    assert sp.state_path.exists()


async def test_forget_keeps_spool_until_region_digest_done(tmp_path):
    sp = await open_spool(tmp_path, vid(1))
    await sp.append(line(1))
    await sp.close()
    pending = state_for()
    pending["digest_writes"] = {
        "0": {"requested_at": NOW, "through_lp": 1, "group": {"0": True},
              "segments": {"0": False}},
    }
    pending["last_summary_done"] = True
    await sp.write_state(pending)
    assert await sp.mark_forget() is False
    assert sp.jsonl_path.exists()
    assert (await sp.read_state())["debrief_choice"] == "forget"
    # Do not delete if the summary is unfinished either.
    await sp.update_state(
        digest_writes={"0": {"requested_at": NOW, "through_lp": 1, "group": {"0": True},
                             "segments": {"0": True}}},
        last_summary_done=False,
    )
    assert await sp.delete_if_settled() is False
    assert sp.jsonl_path.exists()
    assert await sp.delete_if_settled() is False      # This round is not yet counted in digest_runs.
    await sp.update_state(last_summary_done=True, digest_runs=1, digested_through_lp=1)
    assert await sp.delete_if_settled() is True
    assert not sp.jsonl_path.exists()


async def test_forget_refused_after_diary_commit_started(tmp_path):
    sp = VisitSpool(tmp_path, vid(1))
    await sp.write_state(dict(
        state_for(), debrief_choice="committing:diary",
        debrief_pending={"diary": "d", "facts": []},
    ))
    with pytest.raises(SpoolStateError):
        await sp.mark_forget()


async def test_delete_peer_fields_wipes_state_and_header(tmp_path):
    sp = await open_spool(tmp_path, vid(1))
    await sp.append(line(1, "kept"))
    await sp.close()
    await sp.write_state(state_for())
    await sp.delete_peer_fields()
    state = await sp.read_state()
    assert state["peer_uid"] is None and state["pair_id"] is None
    assert state["peer_char_id"] is None
    assert state["own_char"] == "A"
    got = await sp.read_back()
    assert got.header["peer_uid"] is None and got.header["pair_id"] is None
    assert got.header["own_char_uid"] == "uid_a"
    assert [entry["text"] for entry in got.lines] == ["kept"]
    await sp.delete_peer_fields()  # Idempotent.


async def test_delete_peer_fields_refuses_open_writer(tmp_path):
    sp = await open_spool(tmp_path, vid(1))
    with pytest.raises(RuntimeError):
        await sp.delete_peer_fields()
    await sp.close()


async def test_find_visits_for_pairs_matches_own_char_and_pair(tmp_path):
    a = VisitSpool(tmp_path, vid(1))
    await a.write_state(state_for())
    b = VisitSpool(tmp_path, vid(2))
    await b.write_state(state_for(pair_id=PAIR2, peer_uid="peer2"))
    c = VisitSpool(tmp_path, vid(3))
    await c.write_state(state_for(own_char_uid="uid_b"))
    found = await VisitSpool.find_visits_for_pairs(tmp_path, "uid_a", [PAIR1])
    assert found == [vid(1)]


# ── retire / rename ──


async def test_retire_char_only_touches_that_character(tmp_path):
    for n, uid in ((1, "uid_b"), (2, "uid_b"), (3, "uid_a")):
        sp = await open_spool(tmp_path, vid(n), own_char_uid=uid,
                              own_char="B" if uid == "uid_b" else "A")
        await sp.close()
        await sp.write_state(state_for(own_char_uid=uid,
                                       own_char="B" if uid == "uid_b" else "A"))
    spool_dir = tmp_path / "visit_spool"
    for n in (1, 3):
        (spool_dir / f"{vid(n)}.upload.json").write_text("{}", encoding="utf-8")
    retired = await VisitSpool.retire_char(tmp_path, "uid_b")
    assert sorted(retired) == [vid(1), vid(2)]
    assert not (spool_dir / f"{vid(1)}.jsonl").exists()
    assert not (spool_dir / f"{vid(2)}.state.json").exists()
    assert (spool_dir / f"{vid(1)}.upload.json").exists()
    assert (spool_dir / f"{vid(3)}.jsonl").exists()
    assert (spool_dir / f"{vid(3)}.state.json").exists()


async def test_retire_char_legacy_name_fallback(tmp_path):
    spool_dir = tmp_path / "visit_spool"
    spool_dir.mkdir()
    legacy = header(vid(1), own_char="B")
    del legacy["own_char_uid"]
    (spool_dir / f"{vid(1)}.jsonl").write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    assert await VisitSpool.retire_char(tmp_path, "uid_b") == []
    assert await VisitSpool.retire_char(tmp_path, "uid_b", legacy_name="B") == [vid(1)]


async def test_rename_own_char_rewrites_header_and_state(tmp_path):
    for n, name in ((1, "old"), (2, "old"), (3, "other")):
        sp = await open_spool(tmp_path, vid(n), own_char=name)
        await sp.append(line(1, f"body{n}"))
        await sp.close()
        await sp.write_state(state_for(own_char=name))
    # Visit with only state.json remaining (.jsonl deleted, chip pending delivery).
    only_state = VisitSpool(tmp_path, vid(4))
    await only_state.write_state(dict(state_for(own_char="old"), debrief_chip_pending=True))

    renamed = await VisitSpool.rename_own_char(tmp_path, "old", "new")
    assert sorted(renamed) == [vid(1), vid(2), vid(4)]
    for n in (1, 2):
        sp = VisitSpool(tmp_path, vid(n))
        got = await sp.read_back()
        assert got.header["own_char"] == "new"
        assert [entry["text"] for entry in got.lines] == [f"body{n}"]
        assert (await sp.read_state())["own_char"] == "new"
    assert (await only_state.read_state())["own_char"] == "new"
    other = VisitSpool(tmp_path, vid(3))
    assert (await other.read_back()).header["own_char"] == "other"
    assert (await other.read_state())["own_char"] == "other"
    # Idempotent and rerunnable.
    assert await VisitSpool.rename_own_char(tmp_path, "old", "new") == []


async def test_rename_own_char_ignores_other_characters_visits_in_flight(tmp_path):
    from main_logic.visit.spool import SpoolBusy

    done = await open_spool(tmp_path, vid(1), own_char="old")
    await done.append(line(1, "body"))
    await done.close()
    await done.write_state(state_for(own_char="old"))
    # Rename runs at runtime: another character's active visit (open spool) must not block migration (mutation: busy-first must fail).
    live_other = await open_spool(tmp_path, vid(2), own_char="other")
    try:
        assert await VisitSpool.rename_own_char(tmp_path, "old", "new") == [vid(1)]
        assert (await done.read_state())["own_char"] == "new"
        # The renamed character's own visit remains open: still report busy after renaming other visits.
        live_own = await open_spool(tmp_path, vid(3), own_char="new")
        try:
            with pytest.raises(SpoolBusy):
                await VisitSpool.rename_own_char(tmp_path, "new", "newer")
            assert (await done.read_state())["own_char"] == "newer"
        finally:
            await live_own.close()
    finally:
        await live_other.close()


async def test_rename_own_char_treats_a_spool_still_being_opened_as_busy(tmp_path):
    from main_logic.visit import spool as spool_mod
    from main_logic.visit.spool import SpoolBusy

    done = await open_spool(tmp_path, vid(1), own_char="old")
    await done.append(line(1, "body"))
    await done.close()
    await done.write_state(state_for(own_char="old"))
    # Another visit is just registered with file created but header unwritten (_open_sync writes after unlocking): unknown owner, report busy as still writing,
    # not unreadable (the unreadable mutation must fail); rename other visits first as usual.
    opening = spool_mod.visit_path(spool_mod._spool_dir(tmp_path), vid(2), spool_mod.SPOOL_SUFFIX)
    opening.write_bytes(b"")
    key = spool_mod._spool_key(opening)
    with spool_mod._OPEN_SPOOLS_LOCK:
        spool_mod._OPEN_SPOOLS.add(key)
    try:
        with pytest.raises(SpoolBusy):
            await VisitSpool.rename_own_char(tmp_path, "old", "new")
        assert (await done.read_state())["own_char"] == "new"
    finally:
        with spool_mod._OPEN_SPOOLS_LOCK:
            spool_mod._OPEN_SPOOLS.discard(key)


# ── sweep ──


def _age(path, days: float) -> None:
    ts = NOW - days * DAY
    os.utime(path, (ts, ts))


async def test_sweep_deletes_files_older_than_seven_days(tmp_path):
    old = VisitSpool(tmp_path, vid(1))
    await old.write_state(state_for())
    fresh = VisitSpool(tmp_path, vid(2))
    await fresh.write_state(state_for())
    _age(old.state_path, 7.5)
    _age(fresh.state_path, 6.5)
    deleted = await VisitSpool.sweep(tmp_path, NOW)
    assert deleted == [old.state_path]
    assert fresh.state_path.exists()


async def test_sweep_over_cap_keeps_unsettled_and_pending_uploads(tmp_path):
    spool_dir = tmp_path / "visit_spool"
    # Valid 25 MB memory-enabled visit, not recovered after crash (unsettled).
    crashed = VisitSpool(tmp_path, vid(1))
    await crashed.write_state(state_for())
    spool_dir.joinpath(f"{vid(1)}.jsonl").write_bytes(b"x" * (25 * 1024 * 1024))
    # Transcript pending upload from 3 days ago.
    upload = spool_dir / f"{vid(2)}.upload.json"
    upload.write_bytes(b"u" * (2 * 1024 * 1024))
    _age(upload, 3)
    upload_lines = spool_dir / f"{vid(2)}.upload.jsonl"
    upload_lines.write_bytes(b"l" * 1024)
    # Old settled visit.
    done = VisitSpool(tmp_path, vid(3))
    await done.write_state(dict(settled(state_for()), debrief_choice="diary",
                                debrief_writes=dict(new_debrief_writes(), facts=True, cache=True)))
    _age(done.state_path, 2)

    deleted = await VisitSpool.sweep(tmp_path, NOW)
    assert spool_dir.joinpath(f"{vid(1)}.jsonl").exists()
    assert crashed.state_path.exists()
    assert upload.exists() and upload_lines.exists()
    assert deleted == [done.state_path]


async def test_sweep_over_cap_reclaims_settled_visits_oldest_first(tmp_path):
    spool_dir = tmp_path / "visit_spool"
    for n, days in ((1, 3), (2, 1)):
        sp = VisitSpool(tmp_path, vid(n))
        await sp.write_state(dict(settled(state_for()), debrief_choice="forget"))
        body = spool_dir / f"{vid(n)}.jsonl"
        body.write_bytes(b"x" * (12 * 1024 * 1024))
        _age(body, days)
        _age(sp.state_path, days)
    await VisitSpool.sweep(tmp_path, NOW)
    assert not (spool_dir / f"{vid(1)}.jsonl").exists()
    assert (spool_dir / f"{vid(2)}.jsonl").exists()


async def test_sweep_ignores_foreign_files(tmp_path):
    spool_dir = tmp_path / "visit_spool"
    spool_dir.mkdir()
    foreign = spool_dir / "notes.txt"
    foreign.write_text("keep", encoding="utf-8")
    _age(foreign, 30)
    await VisitSpool.sweep(tmp_path, NOW)
    assert foreign.exists()


async def test_failed_fsync_keeps_the_spool_dirty(tmp_path, monkeypatch):
    sp = await open_spool(tmp_path, vid(1))
    await sp.append(line(1))
    assert sp.fsync_due(NOW + 30)

    def boom(self):
        raise OSError("disk full")

    monkeypatch.setattr(VisitSpool, "_fsync_sync", boom)
    with pytest.raises(OSError):
        await sp.fsync(NOW + 30)
    # Failure leaves it dirty without advancing the cadence: retry immediately on the next tick.
    assert sp.fsync_due(NOW + 30)
    monkeypatch.undo()
    await sp.fsync(NOW + 31)
    assert not sp.fsync_due(NOW + 40)
    await sp.close()


async def test_header_rewrite_refuses_an_in_flight_spool_of_another_instance(tmp_path):
    # Erasure creates a new instance to clear peer fields; reject while the in-flight visit's fd is open and replay after it ends.
    from main_logic.visit.spool import SpoolBusy

    live = await open_spool(tmp_path, vid(3))
    await live.write_state(state_for())
    await live.append(line(1))
    other = VisitSpool(tmp_path, vid(3))
    with pytest.raises(SpoolBusy):
        await other.delete_peer_fields()
    await live.append(line(2))
    await live.close()
    await other.delete_peer_fields()
    contents = await other.read_back()
    assert [ln["lp"] for ln in contents.lines] == [1, 2]
    assert contents.header["peer_uid"] is None


async def test_spool_is_registered_before_its_file_is_opened(tmp_path, monkeypatch):
    # Register before os.open, or a rewriter may replace the file while it is open but not registered.
    from main_logic.visit.spool import is_spool_open

    seen: list[bool] = []
    real_open = os.open

    def spy_open(path, flags, mode=0o777):
        seen.append(is_spool_open_unlocked(path))
        return real_open(path, flags, mode)

    def is_spool_open_unlocked(path):
        return spool_mod._spool_key(path) in spool_mod._OPEN_SPOOLS

    monkeypatch.setattr(spool_mod.os, "open", spy_open)
    sp = await open_spool(tmp_path, vid(4))
    assert seen == [True]
    await sp.close()
    assert not is_spool_open(sp.jsonl_path)


async def test_failed_open_leaves_no_registration(tmp_path):
    from main_logic.visit.spool import is_spool_open

    first = await open_spool(tmp_path, vid(5))
    await first.close()
    again = VisitSpool(tmp_path, vid(5))
    with pytest.raises(FileExistsError):
        await again.open(header(vid(5)), now=NOW)
    assert not is_spool_open(again.jsonl_path)


async def test_cancelled_open_releases_fd_and_registration(tmp_path, monkeypatch):
    # If open is cancelled after the worker starts opening, close the fd and unregister the writer.
    import threading

    from main_logic.visit.spool import is_spool_open

    started = threading.Event()
    release = threading.Event()
    real_open_sync = VisitSpool._open_sync

    def slow_open(self, data):
        fd = real_open_sync(self, data)
        started.set()
        release.wait(5)
        return fd

    monkeypatch.setattr(VisitSpool, "_open_sync", slow_open)
    sp = VisitSpool(tmp_path, vid(6))
    task = asyncio.create_task(sp.open(header(vid(6)), now=NOW))
    await asyncio.to_thread(started.wait, 5)
    assert is_spool_open(sp.jsonl_path)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    for _ in range(100):
        if not is_spool_open(sp.jsonl_path):
            break
        await asyncio.sleep(0.01)
    assert not is_spool_open(sp.jsonl_path)
    # Delete header-only residue too, allowing the same visit to retry open directly.
    assert not sp.jsonl_path.exists()
    retry = VisitSpool(tmp_path, vid(6))
    await retry.open(header(vid(6)), now=NOW)
    await retry.close()


async def test_forget_keeps_pending_when_the_spool_header_is_malformed(tmp_path):
    from main_logic.visit.spool import SpoolStateUnreadable

    sp = await open_spool(tmp_path, vid(8))
    await sp.write_state(state_for())
    await sp.close()
    data = sp.jsonl_path.read_bytes()
    sp.jsonl_path.write_bytes(b"{broken" + data[data.index(b"\x7d") + 1:])
    with pytest.raises(SpoolStateUnreadable):
        await VisitSpool(tmp_path, vid(8)).delete_peer_fields()


async def test_sweep_keeps_a_half_committed_diary_past_retention(tmp_path):
    # committing:diary is an irreversible partial write: state.json is the sole recovery source, exempt from the 7-day limit.
    sp = VisitSpool(tmp_path, vid(9))
    state = state_for()
    state["debrief_choice"] = "committing:diary"
    state["debrief_pending"] = {"diary": "d", "facts": []}
    state["debrief_writes"] = dict(new_debrief_writes(), facts=True, facts_written=1)
    await sp.write_state(state)
    old = NOW - 30 * 86400
    os.utime(sp.state_path, (old, old))
    other = VisitSpool(tmp_path, vid(10))
    await other.write_state(state_for())
    os.utime(other.state_path, (old, old))
    await VisitSpool.sweep(tmp_path, NOW)
    assert sp.state_path.exists()
    assert not other.state_path.exists()


async def test_sweep_keeps_a_visit_whose_state_is_temporarily_unreadable(tmp_path, monkeypatch):
    sp = VisitSpool(tmp_path, vid(11))
    await sp.write_state(state_for())
    old = NOW - 10 * 86400            # Past the 7-day retention period but within 2× grace.
    os.utime(sp.state_path, (old, old))
    real = spool_mod._read_state_file

    def locked(path):
        if path.name == sp.state_path.name:
            raise PermissionError("locked")
        return real(path)

    monkeypatch.setattr(spool_mod, "_read_state_file", locked)
    await VisitSpool.sweep(tmp_path, NOW)
    assert sp.state_path.exists()
    monkeypatch.undo()
    await VisitSpool.sweep(tmp_path, NOW)
    assert not sp.state_path.exists()


async def test_a_permanently_unreadable_state_is_reclaimed_after_twice_the_retention(
        tmp_path, monkeypatch):
    # Permanently unreadable files must not occupy disk forever: retain at most one extra retention period.
    sp = VisitSpool(tmp_path, vid(12))
    await sp.write_state(state_for())

    def locked(path):
        raise PermissionError("locked")

    monkeypatch.setattr(spool_mod, "_read_state_file", locked)
    ten_days = NOW - 10 * 86400
    os.utime(sp.state_path, (ten_days, ten_days))
    await VisitSpool.sweep(tmp_path, NOW)
    assert sp.state_path.exists()
    fifteen_days = NOW - 15 * 86400
    os.utime(sp.state_path, (fifteen_days, fifteen_days))
    await VisitSpool.sweep(tmp_path, NOW)
    assert not sp.state_path.exists()


async def test_unreadable_state_grace_uses_the_state_files_own_age(tmp_path, monkeypatch):
    # Older .jsonl from the same visit is swept first: grace still uses state.json's own age.
    sp = await open_spool(tmp_path, vid(13))
    await sp.close()
    await sp.write_state(state_for())
    fifteen = NOW - 15 * 86400
    eight = NOW - 8 * 86400
    os.utime(sp.jsonl_path, (fifteen, fifteen))
    os.utime(sp.state_path, (eight, eight))
    real_scan = spool_mod._scan

    def jsonl_first(spool_dir):
        rows = real_scan(spool_dir)
        return sorted(rows, key=lambda r: 0 if r[1] == spool_mod.SPOOL_SUFFIX else 1)

    def locked(path):
        raise PermissionError("locked")

    monkeypatch.setattr(spool_mod, "_scan", jsonl_first)
    monkeypatch.setattr(spool_mod, "_read_state_file", locked)
    await VisitSpool.sweep(tmp_path, NOW)
    assert sp.state_path.exists()
    # Exempt only state.json (recovery source); .jsonl still expires in committing state, including when state is unreadable.
    assert not sp.jsonl_path.exists()


async def test_cancelled_close_still_closes_the_fd(tmp_path, monkeypatch):
    # Cancelling close must not cancel the close task queued after append, or fd and registration leak.
    import threading

    from main_logic.visit.spool import is_spool_open

    sp = await open_spool(tmp_path, vid(14))
    gate = threading.Event()
    real_append = VisitSpool._append_sync

    def slow_append(self, data):
        gate.wait(5)
        return real_append(self, data)

    monkeypatch.setattr(VisitSpool, "_append_sync", slow_append)
    appending = asyncio.create_task(sp.append(line(1)))
    await asyncio.sleep(0.05)
    closing = asyncio.create_task(sp.close())
    await asyncio.sleep(0.05)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    gate.set()
    await appending
    for _ in range(100):
        if not is_spool_open(sp.jsonl_path):
            break
        await asyncio.sleep(0.01)
    assert not is_spool_open(sp.jsonl_path)


@pytest.mark.parametrize("field,value", [("pair_id", 7), ("peer_uid", ""), ("peer_char_id", [])])
def test_header_peer_fields_must_be_strings_or_null(field, value):
    from main_logic.visit.spool import validate_header

    good = header(vid(1))
    validate_header(dict(good, pair_id=None, peer_uid=None, peer_char_id=None, peer_char_tag=None))
    with pytest.raises(ValueError):
        validate_header(dict(good, **{field: value}))


@pytest.mark.parametrize("field", ["pair_id", "peer_uid", "peer_char_id", "peer_char_tag"])
def test_header_peer_fields_must_be_all_set_or_all_null(field):
    # Half-erased peer identity: pair lookup fails, leaving peer_uid impossible to erase.
    from main_logic.visit.spool import validate_header

    with pytest.raises(ValueError):
        validate_header(dict(header(vid(1)), **{field: None}))


@pytest.mark.parametrize("field", ["pair_id", "peer_uid", "peer_char_id"])
def test_state_peer_fields_must_be_all_set_or_all_null(field):
    from main_logic.visit.spool import SpoolStateError, validate_state

    validate_state(dict(state_for(), pair_id=None, peer_uid=None, peer_char_id=None))
    with pytest.raises(SpoolStateError):
        validate_state(dict(state_for(), **{field: None}))


async def test_retire_char_fails_closed_on_unreadable_ownership(tmp_path):
    # Only unreadable state.json remains: do not assume it belongs to another character; retire other readable visits normally.
    from main_logic.visit.spool import SpoolStateUnreadable

    mine = VisitSpool(tmp_path, vid(1))
    await mine.write_state(state_for(own_char_uid="uid_b"))
    broken = VisitSpool(tmp_path, vid(2))
    await broken.write_state(state_for(own_char_uid="uid_b"))
    broken.state_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(SpoolStateUnreadable) as ei:
        await VisitSpool.retire_char(tmp_path, "uid_b")
    assert ei.value.visit_ids == [vid(2)]
    assert not mine.state_path.exists()
    assert broken.state_path.exists()


async def test_rename_fails_closed_on_unreadable_state(tmp_path):
    from main_logic.visit.spool import SpoolStateUnreadable

    ok = VisitSpool(tmp_path, vid(1))
    await ok.write_state(state_for(own_char="old"))
    broken = VisitSpool(tmp_path, vid(2))
    await broken.write_state(state_for(own_char="old"))
    broken.state_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(SpoolStateUnreadable) as ei:
        await VisitSpool.rename_own_char(tmp_path, "old", "new")
    assert ei.value.visit_ids == [vid(2)]
    assert (await ok.read_state())["own_char"] == "new"


async def test_forget_erases_the_peer_char_tag_from_the_header(tmp_path):
    sp = await open_spool(tmp_path, vid(15))
    await sp.close()
    await sp.write_state(state_for())
    await VisitSpool(tmp_path, vid(15)).delete_peer_fields()
    head = (await sp.read_back()).header
    assert head["peer_char_tag"] is None and head["peer_uid"] is None


async def test_a_second_open_of_the_same_visit_keeps_the_first_registration(tmp_path):
    from main_logic.visit.spool import SpoolBusy, is_spool_open

    first = await open_spool(tmp_path, vid(16))
    second = VisitSpool(tmp_path, vid(16))
    with pytest.raises(SpoolBusy):
        await second.open(header(vid(16)), now=NOW)
    assert is_spool_open(first.jsonl_path)
    with pytest.raises(SpoolBusy):
        await VisitSpool(tmp_path, vid(16)).delete_peer_fields()
    await first.close()


@pytest.mark.parametrize("bad", [{"own_char_uid": 7}, {"own_char_uid": ""}, {"own_char": None}])
async def test_retire_char_rejects_malformed_ownership_in_a_legacy_header(tmp_path, bad):
    from main_logic.visit.spool import SpoolStateUnreadable

    spool_dir = tmp_path / "visit_spool"
    spool_dir.mkdir()
    legacy = header(vid(1), own_char="B")
    del legacy["own_char_uid"]
    legacy.update(bad)
    (spool_dir / f"{vid(1)}.jsonl").write_text(json.dumps(legacy) + chr(10), encoding="utf-8")
    with pytest.raises(SpoolStateUnreadable):
        await VisitSpool.retire_char(tmp_path, "uid_b", legacy_name="B")


async def test_cancelled_append_queued_behind_a_write_still_lands(tmp_path, monkeypatch):
    # Cancelling an append queued after an earlier write must not lose the already accepted row.
    import threading

    sp = await open_spool(tmp_path, vid(17))
    gate = threading.Event()
    real_append = VisitSpool._append_sync
    calls = {"n": 0}

    def slow_first(self, data):
        calls["n"] += 1
        if calls["n"] == 1:
            gate.wait(5)
        return real_append(self, data)

    monkeypatch.setattr(VisitSpool, "_append_sync", slow_first)
    first = asyncio.create_task(sp.append(line(1)))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(sp.append(line(2)))
    await asyncio.sleep(0.05)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    gate.set()
    await first
    await sp.close()
    got = await sp.read_back()
    assert [ln["lp"] for ln in got.lines] == [1, 2]


def test_retirement_waits_for_an_in_progress_state_update(tmp_path):
    # state.json read-modify-write updates and retirement share a per-path lock, preventing updates from resurrecting retired files.
    import threading

    from main_logic.visit.subjects import path_lock

    sp = VisitSpool(tmp_path, vid(18))
    asyncio.run(sp.write_state(state_for(own_char_uid="uid_b")))
    held = threading.Event()
    release = threading.Event()

    def updater():
        with path_lock(sp.state_path):
            held.set()
            release.wait(5)
            assert sp.state_path.exists()      # File still exists while the lock is held.

    t = threading.Thread(target=updater)
    t.start()
    held.wait(5)
    done = threading.Event()

    def retire():
        asyncio.run(VisitSpool.retire_char(tmp_path, "uid_b"))
        done.set()

    r = threading.Thread(target=retire)
    r.start()
    assert not done.wait(0.3)                  # Retirement waits for this lock.
    release.set()
    t.join(5)
    r.join(5)
    assert done.is_set() and not sp.state_path.exists()


def test_settled_deletion_waits_for_an_in_progress_header_rewrite(tmp_path):
    # Settled-transcript deletion and header rewriting share a lock, so rewriting cannot restore a deleted transcript.
    import threading

    from main_logic.visit.subjects import path_lock

    async def setup():
        sp = await open_spool(tmp_path, vid(19))
        await sp.close()
        await sp.write_state(dict(settled(state_for()), debrief_choice="forget"))
        return sp

    sp = asyncio.run(setup())
    held = threading.Event()
    release = threading.Event()

    def rewriter():
        with path_lock(sp.jsonl_path):
            held.set()
            release.wait(5)
            assert sp.jsonl_path.exists()

    t = threading.Thread(target=rewriter)
    t.start()
    held.wait(5)
    done = threading.Event()

    def delete():
        asyncio.run(VisitSpool(tmp_path, vid(19)).delete_if_settled())
        done.set()

    d = threading.Thread(target=delete)
    d.start()
    assert not done.wait(0.3)
    release.set()
    t.join(5)
    d.join(5)
    assert done.is_set() and not sp.jsonl_path.exists()


def test_cap_sweep_waits_for_an_in_progress_header_rewrite(tmp_path, monkeypatch):
    # Capacity reclamation and header rewriting share a lock, so rewriting cannot restore a reclaimed transcript.
    import threading

    from main_logic.visit import spool as spool_mod
    from main_logic.visit.subjects import path_lock

    monkeypatch.setattr(spool_mod, "VISIT_SPOOL_DIR_CAP_BYTES", 0)

    async def setup():
        sp = VisitSpool(tmp_path, vid(20))
        await sp.write_state(dict(settled(state_for()), debrief_choice="forget"))
        sp.jsonl_path.write_bytes(b"x" * 1024)
        return sp

    sp = asyncio.run(setup())
    held = threading.Event()
    release = threading.Event()

    def rewriter():
        with path_lock(sp.jsonl_path):
            held.set()
            release.wait(5)
            assert sp.jsonl_path.exists()

    t = threading.Thread(target=rewriter)
    t.start()
    held.wait(5)
    done = threading.Event()

    def sweep():
        asyncio.run(VisitSpool.sweep(tmp_path, NOW))
        done.set()

    s = threading.Thread(target=sweep)
    s.start()
    assert not done.wait(0.3)
    release.set()
    t.join(5)
    s.join(5)
    assert done.is_set() and not sp.jsonl_path.exists()


async def test_cap_sweep_rechecks_settlement_under_the_lock(tmp_path, monkeypatch):
    # State becomes unsettled again after scanning but before locking: recheck under lock and do not delete.
    from main_logic.visit import spool as spool_mod

    monkeypatch.setattr(spool_mod, "VISIT_SPOOL_DIR_CAP_BYTES", 0)
    sp = VisitSpool(tmp_path, vid(21))
    await sp.write_state(dict(settled(state_for()), debrief_choice="forget"))
    sp.jsonl_path.write_bytes(b"x" * 1024)
    real = spool_mod._try_read_state
    calls = {"n": 0}

    def flip(path):
        calls["n"] += 1
        if calls["n"] == 2:
            return state_for()
        return real(path)

    monkeypatch.setattr(spool_mod, "_try_read_state", flip)
    assert await VisitSpool.sweep(tmp_path, NOW) == []
    assert sp.jsonl_path.exists()


def test_state_and_header_pair_id_must_derive_from_the_identities():
    # pair_id mismatches (own_uid, peer_uid): erasing this person cannot find the visit by pair.
    from main_logic.visit.spool import SpoolStateError, validate_header, validate_state

    validate_state(state_for())
    validate_header(header(vid(1)))
    with pytest.raises(SpoolStateError):
        validate_state(state_for(pair_id=PAIR2))
    with pytest.raises(ValueError):
        validate_header(header(vid(1), pair_id=PAIR2))


def test_retention_sweep_waits_for_an_in_progress_header_rewrite(tmp_path):
    # Retention deletion and header rewriting share a lock and recheck within it; do not mistakenly delete a newly replaced file.
    import threading

    from main_logic.visit.subjects import path_lock

    spool_dir = tmp_path / "visit_spool"
    spool_dir.mkdir()
    body = spool_dir / f"{vid(22)}.jsonl"
    body.write_text(json.dumps(header(vid(22))) + chr(10), encoding="utf-8")
    _age(body, 8)
    held = threading.Event()
    release = threading.Event()

    def rewriter():
        with path_lock(body):
            held.set()
            release.wait(5)
            os.utime(body, None)                       # Rewriter atomically replaces with a new file.

    t = threading.Thread(target=rewriter)
    t.start()
    held.wait(5)
    done = threading.Event()

    def sweep():
        asyncio.run(VisitSpool.sweep(tmp_path, NOW))
        done.set()

    s = threading.Thread(target=sweep)
    s.start()
    assert not done.wait(0.3)
    release.set()
    t.join(5)
    s.join(5)
    assert done.is_set() and body.exists()


async def test_cancelled_mark_forget_still_deletes_a_settled_transcript(tmp_path, monkeypatch):
    import threading

    sp = await open_spool(tmp_path, vid(23))
    await sp.close()
    await sp.write_state(settled(state_for()))
    gate = threading.Event()
    real = VisitSpool._update_state_sync

    def slow(self, mutate):
        gate.wait(5)
        return real(self, mutate)

    monkeypatch.setattr(VisitSpool, "_update_state_sync", slow)
    task = asyncio.create_task(sp.mark_forget())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    gate.set()
    for _ in range(200):
        if not sp.jsonl_path.exists():
            break
        await asyncio.sleep(0.01)
    assert not sp.jsonl_path.exists()


@pytest.mark.parametrize("batches", [{"1": True}, {"0": True, "2": True}, {"00": True}, {"01": True, "0": True}])
def test_digest_batch_maps_must_be_contiguous_from_zero(batches):
    # Missing batches make settlement inspect only remaining values, deleting a transcript whose missing batch was never acknowledged.
    from main_logic.visit.spool import validate_state

    state = settled(state_for())
    validate_state(state)
    for part in ("group", "segments"):
        damaged = dict(state)
        record = dict(state["digest_writes"]["0"])
        record[part] = batches
        damaged["digest_writes"] = {"0": record}
        with pytest.raises(SpoolStateError):
            validate_state(damaged)


@pytest.mark.parametrize("runs,count", [
    ({"1": None}, 1), ({"00": None}, 1), ({"0": None, "2": None}, 2), ({"0": None}, 3),
])
def test_digest_run_keys_must_be_contiguous_and_match_the_count(runs, count):
    from main_logic.visit.spool import validate_state

    good = settled(state_for())
    record = good["digest_writes"]["0"]
    damaged = dict(good, digest_writes={k: record for k in runs}, digest_runs=count)
    with pytest.raises(SpoolStateError):
        validate_state(damaged)
    # A final round still running (registered, not counted in digest_runs) is valid.
    validate_state(dict(good, digest_runs=0, digested_through_lp=-1))


@pytest.mark.parametrize("writes", [{"facts": True, "cache": False}, {"facts": False, "cache": True}])
def test_a_half_written_diary_is_neither_valid_nor_settled(writes):
    from main_logic.visit.spool import validate_state, transcript_releasable

    state = dict(settled(state_for()), debrief_choice="diary",
                 debrief_writes=dict(new_debrief_writes(), **writes))
    assert not transcript_releasable(state)
    with pytest.raises(SpoolStateError):
        validate_state(state)


async def test_settled_deletion_skips_a_spool_another_instance_still_writes(tmp_path):
    # New instance has no _fd, but the original still has this visit open: deletion would send later appends to a deleted inode.
    live = await open_spool(tmp_path, vid(24))
    await live.write_state(dict(settled(state_for()), debrief_choice="forget"))
    assert await VisitSpool(tmp_path, vid(24)).delete_if_settled() is False
    assert live.jsonl_path.exists()
    await live.close()
    assert await VisitSpool(tmp_path, vid(24)).delete_if_settled() is True


async def test_cap_sweep_skips_a_settled_spool_still_open_for_appends(tmp_path, monkeypatch):
    monkeypatch.setattr(spool_mod, "VISIT_SPOOL_DIR_CAP_BYTES", 0)
    live = await open_spool(tmp_path, vid(25))
    await live.write_state(dict(settled(state_for()), debrief_choice="forget"))
    await VisitSpool.sweep(tmp_path, NOW)
    assert live.jsonl_path.exists() and live.state_path.exists()   # Skip the entire visit.
    await live.close()
    await VisitSpool.sweep(tmp_path, NOW)
    assert not live.jsonl_path.exists()


@pytest.mark.parametrize("part", ["group", "segments"])
async def test_an_unregistered_batch_map_is_not_settled(tmp_path, part):
    # Run registered before batch splitting: {} does not mean complete, so mark_forget must not delete the transcript.
    from main_logic.visit.spool import region_settled

    sp = await open_spool(tmp_path, vid(26))
    await sp.append(line(1))
    await sp.close()
    state = settled(state_for())
    state["digest_writes"]["0"] = dict(state["digest_writes"]["0"], **{part: {}})
    assert not region_settled(state)
    await sp.write_state(state)
    assert await sp.mark_forget() is False
    assert sp.jsonl_path.exists()


def test_a_run_still_in_progress_is_not_settled():
    from main_logic.visit.spool import region_settled

    assert region_settled(settled(state_for()))
    assert not region_settled(dict(settled(state_for()), digest_runs=0))


async def test_open_requires_the_callers_clock(tmp_path):
    # Without now, a wall-clock started_at origin combined with monotonic driving would prevent fsync for the whole visit.
    sp = VisitSpool(tmp_path, vid(27))
    with pytest.raises(TypeError):
        await sp.open(header(vid(27)))                          # type: ignore[call-arg]
    # Fixed exactly representable clock avoids real monotonic floating-point rounding at the deadline.
    mono = 12345.0
    await sp.open(header(vid(27)), now=mono)
    await sp.append(line(1))
    assert not sp.fsync_due(mono + visit_settings.VISIT_SPOOL_FSYNC_S - 0.5)
    # Expire exactly at the deadline too: (mono + 30) - mono may be 29.999…; compare now >= last + 30 instead.
    assert sp.fsync_due(mono + visit_settings.VISIT_SPOOL_FSYNC_S)
    await sp.close()


async def test_fsync_is_due_exactly_at_the_interval_despite_float_rounding(tmp_path):
    # Monotonic value observed in Windows CI: (1015.187 + 30) - 1015.187 == 29.999999999999886.
    mono = 1015.187
    sp = VisitSpool(tmp_path, vid(28))
    await sp.open(header(vid(28)), now=mono)
    await sp.append(line(1))
    assert sp.fsync_due(mono + visit_settings.VISIT_SPOOL_FSYNC_S)
    await sp.close()


async def test_retire_char_skips_an_open_spool_and_reports_busy(tmp_path):
    from main_logic.visit.spool import SpoolBusy

    live = await open_spool(tmp_path, vid(28), own_char_uid="uid_b", own_char="B")
    await live.write_state(state_for(own_char_uid="uid_b", own_char="B"))
    done = VisitSpool(tmp_path, vid(29))
    await done.write_state(state_for(own_char_uid="uid_b", own_char="B"))
    with pytest.raises(SpoolBusy):
        await VisitSpool.retire_char(tmp_path, "uid_b")
    assert live.jsonl_path.exists() and live.state_path.exists()
    assert not done.state_path.exists()              # Retire other visits normally.
    await live.close()
    assert await VisitSpool.retire_char(tmp_path, "uid_b") == [vid(28)]


async def test_retire_char_keeps_going_when_a_delete_fails(tmp_path, monkeypatch):
    # Windows cannot delete an open file (PermissionError): classify as unreadable without interrupting other visits.
    from main_logic.visit.spool import SpoolStateUnreadable

    locked = VisitSpool(tmp_path, vid(30))
    await locked.write_state(state_for(own_char_uid="uid_b", own_char="B"))
    other = VisitSpool(tmp_path, vid(31))
    await other.write_state(state_for(own_char_uid="uid_b", own_char="B"))
    real = spool_mod._unlink

    def flaky(path):
        if path.name.startswith(vid(30)):
            raise PermissionError("in use")
        return real(path)

    monkeypatch.setattr(spool_mod, "_unlink", flaky)
    with pytest.raises(SpoolStateUnreadable) as ei:
        await VisitSpool.retire_char(tmp_path, "uid_b")
    assert ei.value.visit_ids == [vid(30)]
    assert not other.state_path.exists()


async def test_rename_skips_an_open_spool_and_reports_busy(tmp_path):
    from main_logic.visit.spool import SpoolBusy

    live = await open_spool(tmp_path, vid(32), own_char="old")
    await live.write_state(state_for(own_char="old"))
    idle = VisitSpool(tmp_path, vid(33))
    await idle.write_state(state_for(own_char="old"))
    with pytest.raises(SpoolBusy):
        await VisitSpool.rename_own_char(tmp_path, "old", "new")
    assert (await idle.read_state())["own_char"] == "new"   # Rename other visits normally.
    await live.close()


@pytest.mark.parametrize("change", [
    {"digested_through_lp": 999},                      # Watermark exceeds every completed round.
    {"digested_through_lp": -1},                       # One round completed without advancing watermark.
    {"digest_runs": 0},                                # Watermark exists without completed rounds.
])
def test_digest_watermark_must_match_the_completed_runs(change):
    from main_logic.visit.spool import validate_state

    with pytest.raises(SpoolStateError):
        validate_state(dict(settled(state_for()), **change))


def test_digest_run_watermarks_must_increase():
    from main_logic.visit.spool import validate_state

    good = settled(state_for())
    record = good["digest_writes"]["0"]
    runs = {"0": record, "1": dict(record, through_lp=record["through_lp"])}
    with pytest.raises(SpoolStateError):
        validate_state(dict(good, digest_writes=runs, digest_runs=2))
    runs["1"] = dict(record, through_lp=record["through_lp"] + 5)
    validate_state(dict(good, digest_writes=runs, digest_runs=2,
                        digested_through_lp=record["through_lp"] + 5))


async def test_replay_drops_schema_damaged_rows(tmp_path):
    # Parsable rows with broken schemas count as discarded, not recovered transcript.
    sp = await open_spool(tmp_path, vid(34))
    await sp.append(line(1, "ok"))
    await sp.close()
    bad_rows = [
        {"lp": 2, "side": "host", "ts": NOW, "from": "own_cat"},                    # Missing text.
        {"lp": 3, "side": "host", "ts": NOW, "from": "narrator", "text": "x"},      # Invalid speaker.
        {"lp": "4", "side": "host", "ts": NOW, "from": "own_cat", "text": "x"},     # Invalid lp type.
    ]
    with open(sp.jsonl_path, "ab") as f:
        for row in bad_rows:
            f.write((json.dumps(row) + chr(10)).encode("utf-8"))
        f.write((json.dumps(line(5, "tail")) + chr(10)).encode("utf-8"))
    got = await VisitSpool(tmp_path, vid(34)).read_back()
    assert [ln["text"] for ln in got.lines] == ["ok", "tail"]
    assert got.dropped_lines == len(bad_rows)
    assert got.header is not None


async def test_replay_drops_a_schema_damaged_header(tmp_path):
    spool_dir = tmp_path / "visit_spool"
    spool_dir.mkdir()
    damaged = dict(header(vid(35)))
    del damaged["own_uid"]
    (spool_dir / f"{vid(35)}.jsonl").write_text(
        json.dumps(damaged) + chr(10) + json.dumps(line(1)) + chr(10), encoding="utf-8")
    got = await VisitSpool(tmp_path, vid(35)).read_back()
    assert got.header is None and got.dropped_lines == 1
    assert [ln["lp"] for ln in got.lines] == [1]


async def test_a_deeply_nested_spool_line_is_dropped(tmp_path):
    sp = await open_spool(tmp_path, vid(36))
    await sp.append(line(1, "ok"))
    await sp.close()
    with open(sp.jsonl_path, "ab") as f:
        f.write(("[" * 5000 + chr(10)).encode("utf-8"))
    got = await VisitSpool(tmp_path, vid(36)).read_back()
    assert [ln["text"] for ln in got.lines] == ["ok"] and got.dropped_lines == 1


@pytest.mark.parametrize("lp", [-1, visit_settings.VISIT_LP_MAX + 1])
async def test_spool_lines_reject_out_of_range_lamport_values(tmp_path, lp):
    sp = await open_spool(tmp_path, vid(37))
    with pytest.raises(ValueError):
        await sp.append(line(lp))
    await sp.append(line(1, "ok"))
    await sp.close()
    with open(sp.jsonl_path, "ab") as f:
        f.write((json.dumps(line(lp)) + chr(10)).encode("utf-8"))
    got = await VisitSpool(tmp_path, vid(37)).read_back()
    assert [ln["lp"] for ln in got.lines] == [1] and got.dropped_lines == 1


async def test_a_deeply_nested_state_file_is_treated_as_corrupt(tmp_path):
    # Deep nesting causes json.load RecursionError: treat as corruption and finish the sweep normally.
    sp = VisitSpool(tmp_path, vid(38))
    await sp.write_state(state_for())
    sp.state_path.write_text("[" * 5000, encoding="utf-8")
    with pytest.raises(SpoolStateError):
        await sp.read_state()
    old = NOW - 8 * 86400
    os.utime(sp.state_path, (old, old))
    deleted = await VisitSpool.sweep(tmp_path, NOW)    # Do not propagate RecursionError.
    assert sp.state_path in deleted


async def test_read_back_rejects_a_spool_of_another_visit(tmp_path):
    # Renamed/replaced file: header identifies another visit; never submit that visit's transcript as this one's.
    sp = await open_spool(tmp_path, vid(39))
    await sp.append(line(1, "theirs"))
    await sp.close()
    other = VisitSpool(tmp_path, vid(40))
    other.jsonl_path.write_bytes(sp.jsonl_path.read_bytes())
    got = await other.read_back()
    assert got.header is None and got.lines == [] and got.dropped_lines == 2


@pytest.mark.parametrize("v", [True, 1.0], ids=["true", "float"])
def test_spool_header_version_must_be_the_integer_one(v):
    with pytest.raises(ValueError):
        validate_header(dict(header(vid(41)), v=v))


@pytest.mark.parametrize("side", ["visitor", "", 1])
def test_spool_line_side_must_be_a_protocol_role(side):
    with pytest.raises(ValueError):
        encode_spool_line(dict(line(1), side=side))


def test_digest_watermarks_stay_in_the_lamport_range():
    # No valid row can reach an out-of-range watermark; do not infer settlement and delete the transcript.
    state = settled(state_for())
    state["digest_writes"]["0"]["through_lp"] = visit_settings.VISIT_LP_MAX + 1
    state["digested_through_lp"] = visit_settings.VISIT_LP_MAX + 1
    with pytest.raises(SpoolStateError):
        validate_state(state)


async def test_sweep_continues_past_a_file_it_cannot_delete(tmp_path, monkeypatch):
    # Leave undeletable old files (open / no permission) for the next pass; continue expiry and capacity reclamation elsewhere.
    locked = VisitSpool(tmp_path, vid(42))
    await locked.write_state(state_for())
    old = VisitSpool(tmp_path, vid(43))
    await old.write_state(state_for())
    _age(locked.state_path, 8)
    _age(old.state_path, 8)
    for n, days in ((44, 3), (45, 1)):
        sp = VisitSpool(tmp_path, vid(n))
        await sp.write_state(dict(settled(state_for()), debrief_choice="forget"))
        _age(sp.state_path, days)
    real = spool_mod._unlink
    stuck = {locked.state_path, VisitSpool(tmp_path, vid(44)).state_path}

    def unlink(path):
        if path in stuck:
            raise PermissionError("in use")
        return real(path)

    monkeypatch.setattr(spool_mod, "_unlink", unlink)
    monkeypatch.setattr(spool_mod, "VISIT_SPOOL_DIR_CAP_BYTES", 0)
    deleted = await VisitSpool.sweep(tmp_path, NOW)
    assert old.state_path in deleted                                  # Expiry reclamation was not interrupted.
    assert VisitSpool(tmp_path, vid(45)).state_path in deleted         # Capacity reclamation was not interrupted either.
    assert all(p.exists() for p in stuck) and not set(deleted) & stuck


async def test_a_swapped_state_file_is_not_trusted(tmp_path):
    # Without visit identity in state, replaced files cannot be detected; retirement could delete another character's transcript under wrong ownership.
    from main_logic.visit.spool import SpoolStateUnreadable

    mine = await open_spool(tmp_path, vid(46), own_char_uid="uid_b")
    await mine.append(line(1))
    await mine.close()
    await mine.write_state(state_for(own_char_uid="uid_b"))
    theirs = await open_spool(tmp_path, vid(47), own_char_uid="uid_c")
    await theirs.append(line(1))
    await theirs.close()
    await theirs.write_state(state_for(own_char_uid="uid_c"))
    a, b = mine.state_path.read_bytes(), theirs.state_path.read_bytes()
    mine.state_path.write_bytes(b)
    theirs.state_path.write_bytes(a)
    with pytest.raises(SpoolStateError):
        await mine.read_state()
    with pytest.raises(SpoolStateUnreadable):
        await VisitSpool.retire_char(tmp_path, "uid_b")
    assert mine.jsonl_path.exists() and theirs.jsonl_path.exists()


async def test_write_state_binds_the_document_to_its_visit(tmp_path):
    sp = VisitSpool(tmp_path, vid(48))
    written = await sp.write_state(state_for())
    assert written["visit_id"] == vid(48)
    assert (await sp.read_state())["visit_id"] == vid(48)
    with pytest.raises(SpoolStateError):
        await VisitSpool(tmp_path, vid(49)).write_state(written)



_PENDING = {"diary": "today", "facts": ["f1"]}
_ERROR = {"step": "cache", "status": 422, "at": NOW, "seq": 1}


def failed_state(**kw) -> dict:
    return dict(state_for(), debrief_choice="commit_failed:diary", debrief_pending=_PENDING,
                debrief_commit_error=_ERROR,
                debrief_writes=dict(new_debrief_writes(), facts=True, facts_written=1), **kw)


async def test_commit_failed_requires_pending_and_an_error(tmp_path):
    # Permanent failure: recovery uses only debrief_pending; debrief_commit_error identifies failed steps and replaces blocks by seq.
    sp = VisitSpool(tmp_path, vid(50))
    await sp.write_state(failed_state())
    with pytest.raises(SpoolStateError):
        await sp.write_state(dict(failed_state(), debrief_pending=None))
    with pytest.raises(SpoolStateError):
        await sp.write_state(dict(failed_state(), debrief_commit_error=None))


@pytest.mark.parametrize("change", [
    {"debrief_writes": {"facts": True, "cache": False}},
    {"debrief_writes": dict(new_debrief_writes(), facts_written=-1)},
    {"debrief_writes": dict(new_debrief_writes(), facts_written=True)},
    {"debrief_writes": dict(new_debrief_writes(), cache_inflight=1)},
    {"debrief_retry": {"attempts": 1}},
    {"debrief_retry": {"attempts": -1, "next_at": NOW}},
    {"debrief_retry": {"attempts": 1, "next_at": "soon"}},
    {"debrief_commit_error": dict(_ERROR, step="diary")},
    {"debrief_commit_error": dict(_ERROR, seq=0)},
    {"debrief_commit_error": dict(_ERROR, status=True)},
    {"debrief_commit_error": {"step": "cache", "status": 422}},
], ids=["writes-old-shape", "written-negative", "written-bool", "inflight-int", "retry-missing",
        "retry-negative", "retry-str", "error-step", "error-seq", "error-status", "error-missing"])
def test_debrief_progress_fields_are_validated(change):
    with pytest.raises(SpoolStateError):
        validate_state(dict(failed_state(), **change))


def test_debrief_retry_and_error_accept_their_shapes():
    validate_state(failed_state(debrief_retry={"attempts": 3, "next_at": NOW + 600}))
    validate_state(dict(failed_state(), debrief_choice="committing:diary",
                        debrief_retry={"attempts": 0, "next_at": NOW}))


@pytest.mark.parametrize("choice", ["committing:diary", "commit_failed:diary"])
async def test_sweep_keeps_the_state_of_a_pinned_commit_but_not_its_transcript(tmp_path, choice):
    # Both committing states retain only state.json (recovery source); .jsonl expires normally.
    sp = await open_spool(tmp_path, vid(51))
    await sp.append(line(1))
    await sp.close()
    await sp.write_state(dict(failed_state(), debrief_choice=choice))
    _age(sp.state_path, 30)
    _age(sp.jsonl_path, 30)
    deleted = await VisitSpool.sweep(tmp_path, NOW)
    assert sp.state_path.exists() and sp.jsonl_path in deleted


@pytest.mark.parametrize("choice", ["committing:diary", "commit_failed:diary"])
async def test_cap_sweep_reclaims_the_transcript_of_a_pinned_commit_only(tmp_path, monkeypatch, choice):
    sp = await open_spool(tmp_path, vid(52))
    await sp.append(line(1))
    await sp.close()
    await sp.write_state(dict(settled(failed_state()), debrief_choice=choice))
    monkeypatch.setattr(spool_mod, "VISIT_SPOOL_DIR_CAP_BYTES", 0)
    deleted = await VisitSpool.sweep(tmp_path, NOW)
    assert deleted == [sp.jsonl_path] and sp.state_path.exists()
    # Later sweeps leave state.json alone after it is the only remaining file.
    assert await VisitSpool.sweep(tmp_path, NOW) == [] and sp.state_path.exists()


async def test_a_committing_visit_drops_its_transcript_once_the_region_is_settled(tmp_path):
    # After committing starts, retries use only debrief_pending: delete .jsonl once digest and summary finish.
    sp = await open_spool(tmp_path, vid(53))
    await sp.append(line(1))
    await sp.close()
    await sp.write_state(dict(settled(failed_state()), debrief_choice="committing:diary"))
    assert await sp.delete_if_settled() is True
    assert not sp.jsonl_path.exists() and sp.state_path.exists()


async def test_abandon_is_only_allowed_from_a_permanent_failure(tmp_path):
    sp = VisitSpool(tmp_path, vid(54))
    await sp.write_state(dict(failed_state(), debrief_choice="committing:diary"))
    with pytest.raises(SpoolStateError):
        await sp.mark_forget(final_choice="abandoned")
    with pytest.raises(SpoolStateError):
        await sp.mark_forget()
    await sp.write_state(failed_state(debrief_retry={"attempts": 2, "next_at": NOW}))
    with pytest.raises(SpoolStateError):
        await sp.mark_forget()                              # Failure state permits abandonment only, not switching to 'do not remember'.
    await sp.mark_forget(final_choice="abandoned")
    state = await sp.read_state()
    assert state["debrief_choice"] == "abandoned" and state["debrief_pending"] is None
    assert state["debrief_writes"]["facts"] is True        # Do not undo an already completed write step.
    assert state["debrief_retry"] is None
    await sp.mark_forget(final_choice="abandoned")          # Idempotent.
    with pytest.raises(ValueError):
        await sp.mark_forget(final_choice="diary")


async def test_abandon_before_the_digest_settles_survives_a_restart(tmp_path):
    # Digest unfinished on abandonment: retain transcript for digest; terminal state remains abandoned, even after reloading disk.
    sp = await open_spool(tmp_path, vid(55))
    await sp.append(line(1))
    await sp.close()
    await sp.write_state(failed_state())
    assert await sp.mark_forget(final_choice="abandoned") is False
    assert sp.jsonl_path.exists()
    reopened = VisitSpool(tmp_path, vid(55))
    state = await reopened.read_state()
    assert state["debrief_choice"] == "abandoned" and state["debrief_pending"] is None
    await reopened.write_state(settled(state))
    assert await reopened.delete_if_settled() is True
    assert (await reopened.read_state())["debrief_choice"] == "abandoned"


@pytest.mark.parametrize("choice,final,releases", [
    ("committing:diary", False, True),
    ("commit_failed:diary", False, True),
    ("abandoned", True, True),
    ("forget", True, True),
    ("preview:diary", False, False),
])
def test_final_and_transcript_release_are_separate_predicates(choice, final, releases):
    # A visit having an outcome differs from reclaimable transcript: committing / permanent failure can reclaim transcript without being terminal.
    from main_logic.visit.spool import debrief_final, debrief_releases_transcript

    state = dict(failed_state(), debrief_choice=choice)
    assert debrief_final(state) is final
    assert debrief_releases_transcript(state) is releases


async def test_cap_sweep_keeps_the_state_when_the_transcript_cannot_be_deleted(tmp_path, monkeypatch):
    # Do not delete state before an undeletable transcript, or future passes cannot determine settlement and it occupies capacity forever.
    sp = await open_spool(tmp_path, vid(56))
    await sp.append(line(1))
    await sp.close()
    await sp.write_state(dict(settled(state_for()), debrief_choice="forget"))
    real = spool_mod._unlink

    def unlink(path):
        if path == sp.jsonl_path:
            raise PermissionError("in use")
        return real(path)

    monkeypatch.setattr(spool_mod, "_unlink", unlink)
    monkeypatch.setattr(spool_mod, "VISIT_SPOOL_DIR_CAP_BYTES", 0)
    assert await VisitSpool.sweep(tmp_path, NOW) == []
    assert sp.jsonl_path.exists() and sp.state_path.exists()
    monkeypatch.setattr(spool_mod, "_unlink", real)
    deleted = await VisitSpool.sweep(tmp_path, NOW)             # Reclaim the entire visit on the next pass after unlocking.
    assert set(deleted) == {sp.jsonl_path, sp.state_path}


async def test_a_failed_header_write_removes_the_new_spool(tmp_path, monkeypatch):
    # After header-write failure (temporary errors such as full disk), delete the new file or same-visit O_EXCL retries fail forever.
    from main_logic.visit.spool import is_spool_open

    calls = {"n": 0}
    real = VisitSpool._write_all

    def flaky(fd, data):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(28, "No space left on device")
        return real(fd, data)

    monkeypatch.setattr(VisitSpool, "_write_all", staticmethod(flaky))
    sp = VisitSpool(tmp_path, vid(57))
    with pytest.raises(OSError):
        await sp.open(header(vid(57)), now=NOW)
    assert not sp.jsonl_path.exists() and not is_spool_open(sp.jsonl_path)
    await sp.open(header(vid(57)), now=NOW)                  # Retry succeeds.
    await sp.append(line(1))
    await sp.close()
    assert [ln["lp"] for ln in (await sp.read_back()).lines] == [1]


async def test_one_unstattable_entry_does_not_abort_the_sweep(tmp_path, monkeypatch):
    # Skip only entries whose stat fails; visit listing by name must still include them for erasure / retirement.
    import pathlib

    bad = VisitSpool(tmp_path, vid(58))
    await bad.write_state(state_for())
    old = VisitSpool(tmp_path, vid(59))
    await old.write_state(state_for())
    _age(bad.state_path, 8)
    _age(old.state_path, 8)
    real_stat = pathlib.Path.stat

    def stat(self, *a, **k):
        if self.name == bad.state_path.name:
            raise PermissionError("denied")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "stat", stat)
    deleted = await VisitSpool.sweep(tmp_path, NOW)
    assert deleted == [old.state_path] and os.path.exists(bad.state_path)   # Path.exists uses the patched stat.
    assert vid(58) in VisitSpool._visit_ids(tmp_path / "visit_spool", {spool_mod.STATE_SUFFIX})


async def test_retention_sweep_skips_a_visit_still_open_for_appends(tmp_path):
    # Wall clock jumps forward over 7 days: an open visit appears expired, but deleting its .jsonl would lose later appends.
    live = await open_spool(tmp_path, vid(60))
    await live.append(line(1))
    await live.write_state(state_for())
    later = NOW + 30 * 86400
    assert await VisitSpool.sweep(tmp_path, later) == []
    assert live.jsonl_path.exists() and live.state_path.exists()
    await live.append(line(2))
    await live.close()
    assert [ln["lp"] for ln in (await live.read_back()).lines] == [1, 2]
    deleted = await VisitSpool.sweep(tmp_path, later)          # Expire normally after closing.
    assert set(deleted) == {live.jsonl_path, live.state_path}


def test_header_peer_char_id_must_derive_from_the_tag():
    # Wrong id would be copied into untagged state.json and become undetectable by recovery / debrief.
    good = header(vid(61))
    validate_header(good)
    with pytest.raises(ValueError):
        validate_header(dict(good, peer_char_id="c_" + "9" * 24))
    with pytest.raises(ValueError):
        validate_header(dict(good, peer_char_tag="e" * 32))


async def test_a_failed_close_after_a_failed_header_write_still_cleans_up(tmp_path, monkeypatch):
    # Header-write ENOSPC followed by close EIO: still delete and unregister, propagating the original ENOSPC.
    from main_logic.visit.spool import is_spool_open

    real_write, real_close = VisitSpool._write_all, os.close
    state = {"armed": True}

    def failing_write(fd, data):
        if state["armed"]:
            raise OSError(28, "No space left on device")
        return real_write(fd, data)

    def failing_close(fd):
        real_close(fd)
        if state["armed"]:
            state["armed"] = False
            raise OSError(5, "I/O error")

    monkeypatch.setattr(VisitSpool, "_write_all", staticmethod(failing_write))
    monkeypatch.setattr(spool_mod.os, "close", failing_close)
    sp = VisitSpool(tmp_path, vid(62))
    with pytest.raises(OSError) as ei:
        await sp.open(header(vid(62)), now=NOW)
    assert ei.value.errno == 28
    assert not sp.jsonl_path.exists() and not is_spool_open(sp.jsonl_path)
    await sp.open(header(vid(62)), now=NOW)                  # Retry is not blocked by SpoolBusy.
    await sp.close()


@pytest.mark.parametrize("fsync_fails", [False, True], ids=["close-eio", "fsync-and-close"])
async def test_close_errors_still_unregister_the_spool(tmp_path, monkeypatch, fsync_fails):
    # Unregister even after close (or fsync + close) reports EIO, or later erasure / rename / sweep remains SpoolBusy.
    from main_logic.visit.spool import is_spool_open

    sp = await open_spool(tmp_path, vid(63))
    await sp.append(line(1))
    real_close, real_fsync = os.close, os.fsync
    armed = {"close": True, "fsync": fsync_fails}

    def failing_close(fd):
        real_close(fd)
        if armed["close"]:
            armed["close"] = False
            raise OSError(5, "I/O error on close")

    def failing_fsync(fd):
        if armed["fsync"]:
            armed["fsync"] = False
            raise OSError(28, "No space left on device")
        return real_fsync(fd)

    monkeypatch.setattr(spool_mod.os, "close", failing_close)
    monkeypatch.setattr(spool_mod.os, "fsync", failing_fsync)
    with pytest.raises(OSError) as ei:
        await sp.close()
    assert ei.value.errno == (28 if fsync_fails else 5)      # Propagate fsync's error if it fails first.
    assert not is_spool_open(sp.jsonl_path)
    await VisitSpool(tmp_path, vid(63)).delete_peer_fields()  # No longer SpoolBusy.


async def test_sweep_never_touches_a_live_visit_without_a_memory_spool(tmp_path):
    from main_logic.visit.spool import UPLOAD_JSONL_SUFFIX, visit_path

    live = vid(61)
    stream = visit_path(tmp_path / "visit_spool", live, UPLOAD_JSONL_SUFFIX)
    stream.parent.mkdir(parents=True, exist_ok=True)
    stream.write_bytes(b'{"kind":"header"}' + bytes([10]))           # One header line (ending with newline).
    _age(stream, 8)                                   # Wall clock jumps forward over 7 days: appears expired.
    deleted = await VisitSpool.sweep(tmp_path, NOW, is_live=lambda visit_id: visit_id == live)
    # Memory-disabled in-flight visits have upload logs without registered spools; rely on the caller's live-visit check.
    assert deleted == [] and stream.exists()
    assert await VisitSpool.sweep(tmp_path, NOW) == [stream]


async def test_read_back_parses_off_the_event_loop(tmp_path, monkeypatch):
    import threading

    from main_logic.visit import spool as spool_module

    sp = VisitSpool(tmp_path, vid(62))
    await sp.write_state(state_for())
    real = spool_module._parse_spool_bytes
    threads = []

    def parse(data, visit_id):
        threads.append(threading.current_thread())
        return real(data, visit_id)

    monkeypatch.setattr(spool_module, "_parse_spool_bytes", parse)
    sp.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    sp.jsonl_path.write_bytes(b"")
    await sp.read_back()
    # Per-line decoding and validation of long transcripts must not run on the event-loop thread.
    assert threads and threads[0] is not threading.main_thread()


@pytest.mark.parametrize("membership,ok", [
    ({"group": ["a1"], "segments": ["b1"]}, True),
    ({"group": ["a1", "a2"], "segments": ["b1"]}, False),        # Batch count mismatch.
    ({"group": ["a1"]}, False),                                  # Missing part.
    ({"group": [""], "segments": ["b1"]}, False),                # Empty fingerprint.
    ("x", False),
])
def test_digest_membership_must_match_the_batches(membership, ok):
    from main_logic.visit.spool import validate_state

    state = settled(state_for())
    record = dict(state["digest_writes"]["0"], membership=membership)
    damaged = dict(state, digest_writes={"0": record})
    if ok:
        validate_state(damaged)
    else:
        with pytest.raises(SpoolStateError):
            validate_state(damaged)


@pytest.mark.parametrize("suffix", [".upload.jsonl", ".upload.json"])
async def test_cap_sweep_keeps_a_visit_with_a_pending_upload(tmp_path, monkeypatch, suffix):
    from main_logic.visit import spool as spool_mod

    monkeypatch.setattr(spool_mod, "VISIT_SPOOL_DIR_CAP_BYTES", 0)
    sp = VisitSpool(tmp_path, vid(64))
    await sp.write_state(dict(settled(state_for()), debrief_choice="forget"))
    sp.jsonl_path.write_bytes(b"x" * 1024)
    pending = sp.jsonl_path.with_name(f"{vid(64)}{suffix}")
    pending.write_text(json.dumps({"kind": "header", "visit_id": vid(64)}) + "\n", encoding="utf-8")
    await VisitSpool.sweep(tmp_path, NOW)
    # Pending uploads remain: sealing recovers account identity from state.json / memory spool; upload-only residue needs state.json to verify identity,
    # so capacity reclamation must not delete these first.
    assert sp.state_path.exists() and sp.jsonl_path.exists() and pending.exists()


# ── Erase this person: unreadable / schema-invalid state handling (PR #3293 review) ──


def _schema_invalid(sp: VisitSpool, **changes) -> dict:
    # Parsable but invalid for the current schema (e.g. downgraded app reads state written by a newer version).
    raw = json.loads(sp.state_path.read_text(encoding="utf-8"))
    raw["field_from_a_newer_version"] = {"kept": True}
    raw.update(changes)
    sp.state_path.write_text(json.dumps(raw), encoding="utf-8")
    return raw


def _wipe_header(sp: VisitSpool) -> None:
    def clear(header: dict) -> bool:
        for name in ("peer_uid", "pair_id", "peer_char_id", "peer_char_tag"):
            header[name] = None
        return True

    assert spool_mod._rewrite_header(sp.jsonl_path, clear, strict=True)


async def test_drop_corrupt_state_only_deletes_content_no_version_can_use(tmp_path):
    torn, listed, newer = (VisitSpool(tmp_path, vid(n)) for n in (90, 91, 92))
    for sp in (torn, listed, newer):
        await sp.write_state(state_for())
    torn.state_path.write_text("{torn", encoding="utf-8")
    listed.state_path.write_text("[1, 2]", encoding="utf-8")     # Parsable, but top level is not an object.
    _schema_invalid(newer)
    for sp in (torn, listed, newer):
        await VisitSpool.drop_corrupt_state(tmp_path, sp.visit_id)
    # Non-JSON / non-object is unusable by anyone: delete; unknown schema may be readable by another version: never delete.
    assert not torn.state_path.exists() and not listed.state_path.exists()
    assert newer.state_path.exists()


async def test_a_lone_non_object_state_counts_as_corrupt(tmp_path):
    sp = VisitSpool(tmp_path, vid(93))
    await sp.write_state(state_for())
    sp.state_path.write_text("[1, 2]", encoding="utf-8")
    wiped: list[str] = []
    found = await VisitSpool.find_visits_for_pairs(tmp_path, "uid_a", [PAIR1], corrupt_wiped=wiped)
    # Handle non-object top level like corrupt JSON: report for erasure-path deletion without blocking erasure.
    assert found == [] and wiped == [vid(93)]


async def test_a_lone_schema_invalid_state_that_cannot_be_attributed_is_skipped(tmp_path):
    sp = VisitSpool(tmp_path, vid(94))
    await sp.write_state(state_for())
    raw = _schema_invalid(sp)
    del raw["pair_id"]                                           # Raw fields cannot identify the pair.
    sp.state_path.write_text(json.dumps(raw), encoding="utf-8")
    wiped: list[str] = []
    found = await VisitSpool.find_visits_for_pairs(tmp_path, "uid_a", [PAIR1], corrupt_wiped=wiped)
    # No header and unknown owner: do not delete (another version may read it) or block (which would stall every local erasure).
    assert found == [] and wiped == [] and sp.state_path.exists()


async def test_wipe_rewrites_a_schema_invalid_state_from_its_raw_object(tmp_path):
    sp = await open_spool(tmp_path, vid(95))
    await sp.close()
    await sp.write_state(state_for())
    _schema_invalid(sp)
    # Header identifies the pair, state is from a newer version: find it and erase identity without errors (previously erased header then raised).
    assert await VisitSpool.find_visits_for_pairs(tmp_path, "uid_a", [PAIR1]) == [vid(95)]
    await sp.delete_peer_fields()
    raw = json.loads(sp.state_path.read_text(encoding="utf-8"))
    assert raw["peer_uid"] is None and raw["pair_id"] is None and raw["peer_char_id"] is None
    # Erase peer identity only; preserve fields introduced by newer versions.
    assert raw["field_from_a_newer_version"] == {"kept": True} and raw["own_char_uid"] == "uid_a"
    assert (await sp.read_back()).header["pair_id"] is None
    # No longer blocks later erasure of this or other people.
    assert await VisitSpool.find_visits_for_pairs(tmp_path, "uid_a", [PAIR1, PAIR2]) == []


async def test_replay_finishes_a_schema_invalid_state_left_behind_a_wiped_header(tmp_path):
    sp = await open_spool(tmp_path, vid(96))
    await sp.close()
    await sp.write_state(state_for())
    _schema_invalid(sp)
    _wipe_header(sp)                                             # Crash after erasing header, before rewriting state.
    # Header erased but raw state still identifies the pair: rewrite the raw object during identity erasure instead of blocking forever as unreadable.
    assert await VisitSpool.find_visits_for_pairs(tmp_path, "uid_a", [PAIR1]) == [vid(96)]
    await sp.delete_peer_fields()
    assert json.loads(sp.state_path.read_text(encoding="utf-8"))["pair_id"] is None
    assert await VisitSpool.find_visits_for_pairs(tmp_path, "uid_a", [PAIR1]) == []


async def test_wipe_deletes_a_corrupt_state_in_the_same_pass(tmp_path):
    sp = await open_spool(tmp_path, vid(97))
    await sp.close()
    await sp.write_state(state_for())
    sp.state_path.write_text("{torn", encoding="utf-8")
    assert await VisitSpool.find_visits_for_pairs(tmp_path, "uid_a", [PAIR1]) == [vid(97)]
    await sp.delete_peer_fields()
    # Corrupt JSON is unusable by anyone: delete with any residual peer fields on the first erasure, without waiting for replay.
    assert not sp.state_path.exists()
    assert (await sp.read_back()).header["pair_id"] is None


@pytest.mark.parametrize("schema_invalid", [False, True], ids=["valid", "schema-invalid"])
async def test_wipe_leaves_no_pair_or_person_id_in_the_state(tmp_path, schema_invalid):
    from main_logic.visit.forget import subject_key
    from main_logic.visit.subjects import (
        derive_person_id,
        group_chat_subject,
        group_participant_subject,
        participant_subject,
    )

    sp = VisitSpool(tmp_path, vid(98))
    state = settled(state_for())
    person = derive_person_id("own_a", "peer1")
    subjects = [group_chat_subject(PAIR1), group_participant_subject(PAIR1, state["peer_char_id"]),
                participant_subject(person)]
    state["digest_writes"]["0"]["epochs"] = {subject_key(s): 3 for s in subjects}
    state["digest_writes"]["0"]["plan"] = {"displays": {"peer_cat": "MikaCatName", "peer_human": "BobHumanName"}}
    await sp.write_state(state)
    if schema_invalid:
        _schema_invalid(sp)
    await sp.delete_peer_fields()
    text = sp.state_path.read_text(encoding="utf-8")
    # digest_writes[*].epochs keys contain pair_id and person_id; plan.displays contains the peer's self-reported name:
    # none may remain in state.json after erasure reports completion.
    assert PAIR1 not in text and person not in text and "peer1" not in text
    assert "MikaCatName" not in text and "BobHumanName" not in text
    if not schema_invalid:
        assert (await sp.read_state())["digest_writes"]["0"]["group"] == {"0": True}


async def test_sweep_asks_is_live_on_the_event_loop_only(tmp_path):
    import threading

    sp = VisitSpool(tmp_path, vid(99))
    await sp.write_state(state_for())
    _age(sp.state_path, 8)
    threads = []

    def is_live(visit_id):
        threads.append(threading.current_thread())
        return False

    deleted = await VisitSpool.sweep(tmp_path, NOW, is_live=is_live)
    # is_live reads the event-loop-owned registry: query only on that thread, never the sweep worker.
    assert threads and all(t is threading.main_thread() for t in threads)
    assert deleted == [sp.state_path]


async def test_a_failing_is_live_keeps_that_visit_and_the_sweep_goes_on(tmp_path):
    flaky, other = VisitSpool(tmp_path, vid(100)), VisitSpool(tmp_path, vid(101))
    for sp in (flaky, other):
        await sp.write_state(state_for())
        _age(sp.state_path, 8)

    def is_live(visit_id):
        if visit_id == flaky.visit_id:
            raise RuntimeError("dictionary changed size during iteration")
        return False

    deleted = await VisitSpool.sweep(tmp_path, NOW, is_live=is_live)
    # If live status is unknown, conservatively treat as in-flight and retain; reclaim other visits without aborting the pass.
    assert deleted == [other.state_path] and flaky.state_path.exists()


@pytest.mark.parametrize("field", ["pair_id", "peer_uid", "peer_char_id"])
def test_new_state_requires_a_bound_peer(field):
    kwargs = dict(own_uid="own_a", own_char="A", own_char_uid="uid_a", pair_id=PAIR1, peer_uid="peer1",
                  peer_char_id=derive_peer_char_id("peer1", "f" * 32), memory_enabled=True)
    new_state(**kwargs)
    kwargs[field] = None
    # None peer fields on disk can only mean erased identity: newly created state must bind a peer.
    with pytest.raises(SpoolStateError):
        new_state(**kwargs)


async def test_sweep_modes_split_pending_uploads_from_the_rest(tmp_path):
    sp = VisitSpool(tmp_path, vid(65))
    await sp.write_state(state_for())
    sp.jsonl_path.write_bytes(b"x" * 16)
    upload = sp.jsonl_path.with_name(f"{vid(65)}.upload.json")
    upload.write_text("{}", encoding="utf-8")
    old = NOW - 8 * 86400
    for path in (sp.state_path, sp.jsonl_path, upload):
        os.utime(path, (old, old))
    # defer: let the caller retry pending uploads once; reclaim other expired files normally.
    await VisitSpool.sweep(tmp_path, NOW, uploads="defer")
    assert upload.exists() and not sp.jsonl_path.exists()
    sp.jsonl_path.write_bytes(b"x" * 16)
    os.utime(sp.jsonl_path, (old, old))
    # only: reclaim expired pending-upload files only, leaving other files unchanged.
    deleted = await VisitSpool.sweep(tmp_path, NOW, uploads="only")
    assert [path.name for path in deleted] == [upload.name] and sp.jsonl_path.exists()


async def test_unreclaimable_bytes_counts_what_the_size_sweep_never_deletes(tmp_path):
    spool_dir = tmp_path / "visit_spool"
    # Unsettled (not recovered after crash): count both transcript and state.
    crashed = VisitSpool(tmp_path, vid(1))
    await crashed.write_state(state_for())
    spool_dir.joinpath(f"{vid(1)}.jsonl").write_bytes(b"x" * 1000)
    # Transcript without state: cannot determine settlement, so count it too.
    spool_dir.joinpath(f"{vid(2)}.jsonl").write_bytes(b"y" * 300)
    # Pending transcript: count it along with its visit's settled transcript (capacity reclamation skips visits with pending uploads).
    pending = VisitSpool(tmp_path, vid(3))
    await pending.write_state(dict(settled(state_for()), debrief_choice="forget"))
    spool_dir.joinpath(f"{vid(3)}.jsonl").write_bytes(b"p" * 200)
    spool_dir.joinpath(f"{vid(3)}.upload.json").write_bytes(b"u" * 50)
    # Settled, no pending upload: capacity reclamation deletes it, so exclude it.
    done = VisitSpool(tmp_path, vid(4))
    await done.write_state(dict(settled(state_for()), debrief_choice="forget"))
    spool_dir.joinpath(f"{vid(4)}.jsonl").write_bytes(b"d" * 5000)
    # Outbox log deleted at startup, and in-flight visit has a separate limit: exclude both.
    spool_dir.joinpath(f"{vid(5)}.outbox.jsonl").write_bytes(b"o" * 7000)
    spool_dir.joinpath(f"{vid(6)}.jsonl").write_bytes(b"l" * 9000)

    def size(n, suffix):
        return spool_dir.joinpath(f"{vid(n)}{suffix}").stat().st_size

    expected = (size(1, ".jsonl") + size(1, ".state.json") + 300
                + size(3, ".jsonl") + size(3, ".state.json") + 50)
    assert await VisitSpool.unreclaimable_bytes(tmp_path, is_live=lambda v: v == vid(6)) == expected
    assert await VisitSpool.unreclaimable_bytes(tmp_path) == expected + 9000
    assert await VisitSpool.unreclaimable_bytes(tmp_path / "missing") == 0


async def test_unreclaimable_bytes_keeps_the_pinned_state_of_a_committing_diary(tmp_path):
    spool_dir = tmp_path / "visit_spool"
    pinned = VisitSpool(tmp_path, vid(1))
    await pinned.write_state(dict(settled(state_for()), debrief_choice="committing:diary",
                                  debrief_pending={"diary": "d", "facts": []}))
    spool_dir.joinpath(f"{vid(1)}.jsonl").write_bytes(b"x" * 1000)
    # Capacity reclamation deletes transcript but pins state.json: count only the latter.
    assert await VisitSpool.unreclaimable_bytes(tmp_path) == pinned.state_path.stat().st_size
