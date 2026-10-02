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
from main_logic.visit.spool import (
    LINE_SPEAKERS,
    SpoolLineTooLarge,
    SpoolStateError,
    VisitSpool,
    is_digestable,
    new_state,
)

DAY = 86400.0
NOW = time.time()


def vid(n: int) -> str:
    return f"visit{n:017d}"


def header(visit_id: str, *, own_char="A", own_char_uid="uid_a", pair_id="pair1",
           peer_uid="peer1", peer_char_id="c_peer") -> dict:
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


def state_for(*, own_char="A", own_char_uid="uid_a", pair_id="pair1", peer_uid="peer1",
              memory_enabled=True) -> dict:
    return new_state(
        own_uid="own_a", own_char=own_char, own_char_uid=own_char_uid, pair_id=pair_id,
        peer_uid=peer_uid, peer_char_id="c_peer", memory_enabled=memory_enabled,
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


# ── 写入与读回 ──


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
    # 模拟 kill -9：最后一行只写了一半。
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
        # 先提交的写入故意更慢：只有单写线程能保证落盘顺序仍是调用顺序。
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
    assert not sp.fsync_due(NOW + 100)  # 没有新行
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
    # own_uid：按社区账号分区的本侧账号（崩溃补录派生人级主体用，不随「清除这个人」抹除）
    "own_uid", "own_char", "own_char_uid", "pair_id", "peer_uid", "peer_char_id",
    "digested_through_lp", "digest_runs", "finalized", "debrief_choice",
    "debrief_pending", "debrief_writes", "debrief_chip_pending",
    "last_summary_done", "memory_enabled", "digest_writes",
}


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
    assert on_disk["debrief_writes"] == {"facts": False, "cache": False}
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


@pytest.mark.parametrize("choice", [None, "ask_later", "diary", "forget"])
async def test_state_allowed_choices(tmp_path, choice):
    await VisitSpool(tmp_path, vid(1)).write_state(dict(state_for(), debrief_choice=choice))


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
    # 开场后把当前配置改成相反值：本场判定不变。
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
    # state.json 留着（7 天由 sweep 清）。
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
    # 摘要没做完也不删。
    await sp.update_state(
        digest_writes={"0": {"requested_at": NOW, "through_lp": 1, "group": {"0": True},
                             "segments": {"0": True}}},
        last_summary_done=False,
    )
    assert await sp.delete_if_settled() is False
    assert sp.jsonl_path.exists()
    await sp.update_state(last_summary_done=True)
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
    await sp.delete_peer_fields()  # 幂等


async def test_delete_peer_fields_refuses_open_writer(tmp_path):
    sp = await open_spool(tmp_path, vid(1))
    with pytest.raises(RuntimeError):
        await sp.delete_peer_fields()
    await sp.close()


async def test_find_visits_for_pairs_matches_own_char_and_pair(tmp_path):
    a = VisitSpool(tmp_path, vid(1))
    await a.write_state(state_for(pair_id="pair1"))
    b = VisitSpool(tmp_path, vid(2))
    await b.write_state(state_for(pair_id="pair_other_account"))
    c = VisitSpool(tmp_path, vid(3))
    await c.write_state(state_for(own_char_uid="uid_b", pair_id="pair1"))
    found = await VisitSpool.find_visits_for_pairs(tmp_path, "uid_a", ["pair1"])
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
    # 只剩 state.json 的场次（.jsonl 已删、芯片待投递）。
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
    # 幂等可重跑。
    assert await VisitSpool.rename_own_char(tmp_path, "old", "new") == []


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
    # 一场合法的 25 MB 记忆开启场次崩溃后未补录（未结清）。
    crashed = VisitSpool(tmp_path, vid(1))
    await crashed.write_state(state_for())
    spool_dir.joinpath(f"{vid(1)}.jsonl").write_bytes(b"x" * (25 * 1024 * 1024))
    # 一份 3 天前仍待上传的转录。
    upload = spool_dir / f"{vid(2)}.upload.json"
    upload.write_bytes(b"u" * (2 * 1024 * 1024))
    _age(upload, 3)
    upload_lines = spool_dir / f"{vid(2)}.upload.jsonl"
    upload_lines.write_bytes(b"l" * 1024)
    # 一场已结清的旧场次。
    done = VisitSpool(tmp_path, vid(3))
    await done.write_state(dict(settled(state_for()), debrief_choice="diary"))
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
    # 失败后仍然是脏的、节拍不前移：下一次 tick 立刻重试
    assert sp.fsync_due(NOW + 30)
    monkeypatch.undo()
    await sp.fsync(NOW + 31)
    assert not sp.fsync_due(NOW + 40)
    await sp.close()


async def test_header_rewrite_refuses_an_in_flight_spool_of_another_instance(tmp_path):
    # 清除执行器会新建实例去抹 peer 字段；在飞那场的 fd 还开着时必须拒绝，等结束后重放
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
    # 登记必须先于 os.open：否则改写方可能在「已打开、未登记」的窗口里替换掉文件
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
    # open 被取消时 worker 已在打开文件：fd 要关掉、在写登记要撤销
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
    os.remove(sp.jsonl_path)   # fd 已关：Windows 上能删掉


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
    # committing:diary 是不可撤回的半截写入：state.json 是补写的唯一依据，不受 7 天约束
    sp = VisitSpool(tmp_path, vid(9))
    state = state_for()
    state["debrief_choice"] = "committing:diary"
    state["debrief_pending"] = {"diary": "d", "facts": []}
    state["debrief_writes"] = {"facts": True, "cache": False}
    await sp.write_state(state)
    old = NOW - 30 * 86400
    os.utime(sp.state_path, (old, old))
    other = VisitSpool(tmp_path, vid(10))
    await other.write_state(state_for())
    os.utime(other.state_path, (old, old))
    await VisitSpool.sweep(tmp_path, NOW)
    assert sp.state_path.exists()
    assert not other.state_path.exists()
