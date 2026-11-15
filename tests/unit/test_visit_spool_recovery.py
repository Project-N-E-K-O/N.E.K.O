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

"""Startup recovery of visit files (visit design PR-08, section 3.7.3 item 7)."""

from __future__ import annotations

import json

import pytest
import os
import re
import time
from pathlib import Path

from main_logic.visit.forget import RevocationLog
from main_logic.visit.forget_runner import forget_person
from main_logic.visit.recovery import visit_spool_recovery
from main_logic.visit.subjects import PeerRoster, derive_pair_id, derive_peer_char_id
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
    resolver,
    seed_roster,
    vid,
)

PAIR = derive_pair_id(OWN_A, PEER_X)
REPO = Path(__file__).resolve().parents[2]


class Chips:
    def __init__(self, delivered: bool = False):
        self.calls: list[tuple[str, str, str | None]] = []
        self.delivered = delivered

    async def __call__(self, visit_id, *, own_char, status):
        self.calls.append((visit_id, own_char, status))
        return self.delivered


class Uploads:
    def __init__(self, ok: bool = True):
        self.calls: list[tuple[str, dict]] = []
        self.ok = ok

    async def __call__(self, visit_id, doc):
        self.calls.append((visit_id, doc))
        return self.ok


class Reports(Uploads):
    pass


class LLM:
    def __init__(self):
        self.calls = 0

    async def __call__(self, prompt):
        self.calls += 1
        return "上次聊了天气。"


@pytest.fixture(autouse=True)
def _characters_config_readable(monkeypatch):
    # Backfill strictly checks character configuration first; tests never touch runtime-root characters.json.
    from main_logic.visit import local_chars

    async def readable():
        return None

    monkeypatch.setattr(local_chars, "ensure_characters_readable", readable)


async def _recover(tmp_path, server=None, **kw):
    kw.setdefault("render_chips", Chips())
    render = kw.pop("render_chips")
    kw.setdefault("is_live", lambda _visit_id: False)
    return await visit_spool_recovery(
        render, kw.pop("upload_transcript", None), config_dir=tmp_path,
        resolve_char_name=kw.pop("resolve_char_name", resolver()),
        list_char_names=kw.pop("list_char_names", _names("A", "B")),
        client=(server or FakeMemoryServer()).client(), **kw,
    )


def _names(*names):
    async def names_():
        return list(names)

    return names_


def _spool_dir(tmp_path) -> Path:
    return tmp_path / "visit_spool"


def _write_stream(tmp_path, visit_id, records):
    path = _spool_dir(tmp_path) / f"{visit_id}.upload.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(json.dumps(r, ensure_ascii=False).encode() + b"\n" for r in records))
    return path


def _sealed(visit_id):
    """A well-formed ``.upload.json`` of ``visit_id`` (recovery deletes stale streams only next to one)."""
    usage = {"duration_s": 1, "llm_input_tokens": 0, "llm_output_tokens": 0, "tts_requests": 0, "tts_chars": 0}
    return {"v": 1, "own_visit_uid": OWN_A, "own_char_uid": CHAR_UID_A, "transport": "livekit", "request": {
        "visit_id": visit_id, "role": "host", "started_at": 1000.0, "ended_at": 1001.0,
        "finalized_reason": "crash", "usage": usage, "lines": [], "anomalies": 0, "app_version": "",
    }}


def _header(visit_id, role="host"):
    return {"kind": "header", "visit_id": visit_id, "role": role, "own_visit_uid": OWN_A,
            "started_at": 1000.0, "own_char_uid": CHAR_UID_A, "app_version": "0.8", "transport": "livekit"}


# ── Startup cleanup and upload ──


async def test_startup_cleanup_only_deletes_outboxes(tmp_path):
    await seed_roster(tmp_path)
    v = vid(1)
    await make_visit(tmp_path, v, [ln(0)], last_summary_done=True)
    d = _spool_dir(tmp_path)
    (d / f"{v}.outbox.jsonl").write_text("x", encoding="utf-8")
    (d / f"{v}.upload.json").write_text(json.dumps(_sealed(v)), encoding="utf-8")
    await _recover(tmp_path)
    assert not (d / f"{v}.outbox.jsonl").exists()
    assert (d / f"{v}.state.json").exists() and (d / f"{v}.upload.json").exists()


async def test_pending_upload_files_are_retried_once_each(tmp_path):
    await make_visit(tmp_path, vid(1), [], memory_enabled=False, last_summary_done=True)
    d = _spool_dir(tmp_path)
    for n in (1, 2):
        (d / f"{vid(n)}.upload.json").write_text(json.dumps(_sealed(vid(n))),
                                                 encoding="utf-8")
    uploads = Uploads()
    await _recover(tmp_path, upload_transcript=uploads)
    assert sorted(v for v, _doc in uploads.calls) == [vid(1), vid(2)]
    assert not list(d.glob("*.upload.json"))


async def test_crashed_visit_is_uploaded_from_its_stream(tmp_path):
    v = vid(3)
    _write_stream(tmp_path, v, [
        _header(v),
        {"kind": "line", "lp": 1, "side": "guest", "from": "peer_cat", "ts": 1005.0, "text": "b", "truncated": False},
        {"kind": "line", "lp": 0, "side": "host", "from": "own_cat", "ts": 1001.0, "text": "a", "truncated": False},
        {"kind": "usage", "ts": 1006.0, "d": {"llm_input_tokens": 10, "llm_output_tokens": 3}},
        {"kind": "usage", "ts": 1007.0, "d": {"llm_input_tokens": 5, "tts_requests": 1, "tts_chars": 7}},
        {"kind": "anomaly", "ts": 1008.0},
        {"kind": "anomaly", "ts": 1009.5},
    ])
    uploads = Uploads()
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, doc), = uploads.calls
    req = doc["request"]
    assert visit_id == v and doc["own_visit_uid"] == OWN_A
    assert (req["visit_id"], req["role"], req["started_at"], req["app_version"]) == (v, "host", 1000.0, "0.8")
    assert req["usage"] == {"duration_s": 9, "llm_input_tokens": 15, "llm_output_tokens": 3,
                            "tts_requests": 1, "tts_chars": 7}
    assert req["anomalies"] == 2 and req["ended_at"] == 1009.5
    assert req["finalized_reason"] == "crash"
    assert [line["text"] for line in req["lines"]] == ["a", "b"]
    assert not list(_spool_dir(tmp_path).glob(f"{v}.upload*"))


async def test_stream_without_header_is_dropped_not_uploaded(tmp_path, caplog):
    v = vid(4)
    _write_stream(tmp_path, v, [{"kind": "line", "lp": 0, "side": "host", "from": "own_cat",
                                 "ts": 1.0, "text": "a", "truncated": False}])
    uploads = Uploads()
    await _recover(tmp_path, upload_transcript=uploads)
    assert uploads.calls == []
    assert not (_spool_dir(tmp_path) / f"{v}.upload.jsonl").exists()


async def test_finalized_but_unsealed_stream_is_uploaded_exactly_once(tmp_path):
    v, live = vid(5), vid(6)
    await make_visit(tmp_path, v, [], memory_enabled=False, finalized="wrap_up", last_summary_done=True)
    _write_stream(tmp_path, v, [_header(v), {"kind": "line", "lp": 0, "side": "host", "from": "own_cat",
                                             "ts": 1001.0, "text": "a", "truncated": False}])
    live_stream = _write_stream(tmp_path, live, [_header(live)])
    uploads = Uploads()
    await _recover(tmp_path, upload_transcript=uploads, is_live=lambda visit_id: visit_id == live)
    assert [vid_ for vid_, _ in uploads.calls] == [v]
    assert uploads.calls[0][1]["request"]["finalized_reason"] == "wrap_up"
    assert not list(_spool_dir(tmp_path).glob(f"{v}.upload*"))
    assert live_stream.exists() and live_stream.read_bytes()
    await _recover(tmp_path, upload_transcript=uploads, is_live=lambda visit_id: visit_id == live)
    assert len(uploads.calls) == 1


async def test_failed_upload_keeps_the_file(tmp_path):
    v = vid(7)
    _write_stream(tmp_path, v, [_header(v)])
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    assert (_spool_dir(tmp_path) / f"{v}.upload.json").exists()
    assert uploads.calls[0][1]["request"]["usage"]["duration_s"] == 0


async def test_reports_follow_their_upload_and_are_also_scanned_alone(tmp_path):
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    pending, uploaded = vid(8), vid(9)
    (d / f"{pending}.upload.json").write_text(json.dumps(_sealed(pending)), encoding="utf-8")
    for v in (pending, uploaded):
        (reports_dir / f"{v}.json").write_text(json.dumps({"visit_id": v, "reason": "spam"}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    assert [v for v, _ in reports.calls] == [uploaded]       # Do not submit reports before that visit's transcript is uploaded.
    assert (reports_dir / f"{pending}.json").exists() and not (reports_dir / f"{uploaded}.json").exists()
    reports.calls.clear()
    await _recover(tmp_path, upload_transcript=Uploads(ok=True), submit_report=reports)
    assert [v for v, _ in reports.calls] == [pending]
    assert not list(reports_dir.glob("*.json"))


async def test_size_sweep_keeps_pending_uploads(tmp_path):
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    big = d / f"{vid(10)}.upload.json"
    big.write_text(json.dumps({**_sealed(vid(10)), "pad": " " * (21 * 1024 * 1024)}), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    assert big.exists()


# ── Crash/shutdown fallback and chips ──


async def test_crash_marks_finalized_shows_chip_and_writes_no_private_memory(tmp_path):
    await seed_roster(tmp_path)
    v = vid(11)
    spool = await make_visit(tmp_path, v, [ln(0, "你好"), ln(1, "嗨", "peer_human")], finalized=None)
    server = FakeMemoryServer()
    chips = Chips(delivered=False)
    report = await _recover(tmp_path, server, render_chips=chips, summary_llm=LLM())
    state = await spool.read_state()
    assert state["finalized"] == "crash" and report.crashed == [v]
    assert state["debrief_chip_pending"] is True and state["debrief_choice"] is None
    assert chips.calls == [(v, "A", "interrupted")]
    assert {name for name, _ in server.requests} == {"scoped_history"}   # Visit-section digest only.
    assert state["digested_through_lp"] == 1
    assert state["last_summary_done"] is True


async def test_chip_flag_stays_until_a_choice_is_made(tmp_path):
    await seed_roster(tmp_path)
    spool = await make_visit(tmp_path, vid(12), [ln(0, "你好")], finalized=None)
    await _recover(tmp_path, render_chips=Chips(delivered=True))
    assert (await spool.read_state())["debrief_chip_pending"] is True
    await _recover(tmp_path, render_chips=Chips(delivered=True))
    assert (await spool.read_state())["debrief_chip_pending"] is True


async def test_memory_off_crash_shows_no_chip(tmp_path):
    spool = await make_visit(tmp_path, vid(13), [], memory_enabled=False, finalized=None)
    chips = Chips()
    await _recover(tmp_path, render_chips=chips, summary_llm=LLM())
    state = await spool.read_state()
    assert state["finalized"] == "crash" and state["debrief_chip_pending"] is False
    assert chips.calls == [] and state["last_summary_done"] is True


async def test_old_shutdown_without_choice_becomes_ask_later_only_with_lines(tmp_path):
    await seed_roster(tmp_path)
    with_lines = await make_visit(tmp_path, vid(14), [ln(0, "你好")], finalized="shutdown")
    no_memory = await make_visit(tmp_path, vid(15), [], memory_enabled=False, finalized="shutdown")
    chips = Chips()
    await _recover(tmp_path, render_chips=chips)
    a = await with_lines.read_state()
    assert a["debrief_choice"] == "ask_later" and a["debrief_chip_pending"] is True
    b = await no_memory.read_state()
    assert b["debrief_choice"] is None and b["debrief_chip_pending"] is False
    assert [c[0] for c in chips.calls] == [vid(14)]


async def test_generating_diary_only_replays_chips(tmp_path):
    await seed_roster(tmp_path)
    spool = await make_visit(tmp_path, vid(16), [ln(0, "你好")], debrief_choice="generating:diary",
                             last_summary_done=True)
    server = FakeMemoryServer()
    llm = LLM()
    await _recover(tmp_path, server, summary_llm=llm)
    state = await spool.read_state()
    assert state["debrief_chip_pending"] is True and state["debrief_choice"] == "generating:diary"
    assert llm.calls == 0
    assert all(name == "scoped_history" for name, _ in server.requests)


async def test_committing_diary_is_resumed_through_the_injected_writer(tmp_path):
    await seed_roster(tmp_path)
    writes = {"facts": True, "cache": False, "facts_written": 1, "facts_unconfirmed": False,
              "cache_unconfirmed": False, "facts_inflight": False, "cache_inflight": False}
    spool = await make_visit(tmp_path, vid(17), [ln(0, "你好")], debrief_choice="committing:diary",
                             debrief_pending={"diary": "日记", "facts": ["f"]}, debrief_writes=writes,
                             last_summary_done=True)
    resumed = []

    async def resume(spool_, state):
        resumed.append((spool_.visit_id, state["debrief_writes"]["cache"]))

    chips = Chips(delivered=False)
    await _recover(tmp_path, resume_diary_commit=resume, render_chips=chips)
    assert resumed == [(vid(17), False)]
    # Two-step writes may still be backing off: replay preview blocks via bind, showing writing progress to new connections.
    assert (await spool.read_state())["debrief_chip_pending"] is True
    assert chips.calls == [(vid(17), "A", None)]


async def test_flag_disappears_with_the_spool_after_seven_days(tmp_path):
    await seed_roster(tmp_path)
    spool = await make_visit(tmp_path, vid(18), [ln(0, "你好")], finalized="crash",
                             debrief_choice="ask_later", debrief_chip_pending=True)
    old = time.time() - 8 * 86400
    for path in _spool_dir(tmp_path).iterdir():
        os.utime(path, (old, old))
    chips = Chips()
    await _recover(tmp_path, render_chips=chips)
    assert await spool.read_state() is None and chips.calls == []


async def test_lagging_digest_and_summary_are_completed(tmp_path):
    await seed_roster(tmp_path)
    spool = await make_visit(tmp_path, vid(19), [ln(0, "你好"), ln(1, "嗨", "peer_cat")])
    llm = LLM()
    report = await _recover(tmp_path, summary_llm=llm)
    state = await spool.read_state()
    assert report.digests == {vid(19): True} and report.summaries == {vid(19): True}
    assert state["digested_through_lp"] == 1 and state["last_summary_done"] is True
    assert llm.calls == 1
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    assert (await roster.get_last_summary(PEER_X, "A"))["text"] == "上次聊了天气。"
    await _recover(tmp_path, summary_llm=llm)
    assert llm.calls == 1


async def test_memory_server_down_keeps_everything_for_next_start(tmp_path):
    await seed_roster(tmp_path)
    spool = await make_visit(tmp_path, vid(20), [ln(0, "你好")])
    server = FakeMemoryServer()
    server.fail_always.add("scoped_history")
    report = await _recover(tmp_path, server)
    assert report.digests == {vid(20): False}
    assert (await spool.read_state())["digested_through_lp"] == -1
    assert spool.jsonl_path.exists()


async def test_background_entry_runs_the_commits(tmp_path):
    await seed_roster(tmp_path)
    await make_visit(tmp_path, vid(21), [ln(0, "你好")])
    spawned = []

    async def spawn(own_char_uid, factory):
        spawned.append(own_char_uid)
        return await factory()

    await _recover(tmp_path, spawn_background=spawn, summary_llm=LLM())
    assert spawned == [CHAR_UID_A, CHAR_UID_A]


# ── Revocation-journal replay and rename reconciliation ──


async def test_revocation_log_merges_new_subjects_and_replays_after_outage(tmp_path):
    await seed_roster(tmp_path)
    server = FakeMemoryServer()
    server.fail_always.add("scoped_forget")
    first = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                peer_uid=PEER_X, client=server.client())
    assert not first.done
    (log,) = await RevocationLog.list_all_open(tmp_path)
    assert not any(step.startswith("forget:") for step in log["done_steps"])
    # Meanwhile the same person visits with another cat, adding another peer character to the roster.
    await seed_roster(tmp_path, tag=TAG_Y)
    second = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                 peer_uid=PEER_X, client=server.client())
    assert not second.done
    (merged,) = await RevocationLog.list_all_open(tmp_path)
    cat_y = derive_peer_char_id(PEER_X, TAG_Y)
    assert any(s["subject_id"].endswith(cat_y) for s in merged["subjects"])
    assert merged["done_steps"][:1] == ["clear_last_summary"]
    server.fail_always.clear()
    server.requests.clear()
    roster = PeerRoster(tmp_path, own_uid=OWN_A)
    removed_after = []
    real_remove = PeerRoster.remove_char

    async def spy_remove(self, peer_uid, own_char):
        removed_after.append(len(server.calls("scoped_forget")))
        return await real_remove(self, peer_uid, own_char)

    PeerRoster.remove_char = spy_remove
    try:
        report = await _recover(tmp_path, server)
    finally:
        PeerRoster.remove_char = real_remove
    assert report.forgets_clean
    forgets = [c["subject"]["subject_id"] for c in server.calls("scoped_forget")]
    assert len(forgets) == len(set(forgets)) == 4
    assert removed_after == [4]
    assert await RevocationLog.list_all_open(tmp_path) == []
    assert not list((tmp_path / "visit_revocations").glob("*.json"))
    assert await roster.get_peer(PEER_X) is None


async def test_rename_reconciliation_moves_roster_and_spools(tmp_path):
    await seed_roster(tmp_path)
    spool = await make_visit(tmp_path, vid(22), [ln(0)], last_summary_done=True)
    peers_path = tmp_path / "visit_peers.json"
    data = json.loads(peers_path.read_text(encoding="utf-8"))
    data["pending_rename"] = {"old": "A", "new": "C"}
    peers_path.write_text(json.dumps(data), encoding="utf-8")
    report = await _recover(tmp_path, list_char_names=_names("C", "B"),
                            resolve_char_name=resolver({CHAR_UID_A: "C"}))
    assert report.renamed
    data = json.loads(peers_path.read_text(encoding="utf-8"))
    assert "pending_rename" not in data
    assert set(data["accounts"][OWN_A]["peers"][PEER_X]["by_char"]) == {"C"}
    assert (await spool.read_state())["own_char"] == "C"


async def test_rename_that_never_took_effect_is_rolled_back(tmp_path):
    await seed_roster(tmp_path)
    spool = await make_visit(tmp_path, vid(23), [ln(0)], own_char="C", last_summary_done=True)
    peers_path = tmp_path / "visit_peers.json"
    data = json.loads(peers_path.read_text(encoding="utf-8"))
    data["pending_rename"] = {"old": "A", "new": "C"}
    peers_path.write_text(json.dumps(data), encoding="utf-8")
    await _recover(tmp_path, list_char_names=_names("A", "B"))
    assert (await spool.read_state())["own_char"] == "A"
    assert "pending_rename" not in json.loads(peers_path.read_text(encoding="utf-8"))


# ── Outside the startup chain ──


def test_recovery_is_only_ever_started_as_a_background_task():
    pattern = re.compile(r"visit_spool_recovery")
    for path in (REPO / "app" / "main_server").glob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if pattern.search(line) and not line.lstrip().startswith(("#", "from ", "import ")):
                assert "create_task(" in line, f"{path.name}: {line.strip()}"


async def test_spool_is_untouched_for_live_visits(tmp_path):
    await seed_roster(tmp_path)
    v = vid(24)
    spool = await make_visit(tmp_path, v, [ln(0)], finalized=None)
    (_spool_dir(tmp_path) / f"{v}.outbox.jsonl").write_text("x", encoding="utf-8")
    chips = Chips()
    await _recover(tmp_path, render_chips=chips, is_live=lambda visit_id: visit_id == v)
    assert (await spool.read_state())["finalized"] is None and chips.calls == []
    assert (_spool_dir(tmp_path) / f"{v}.outbox.jsonl").exists()


# ── Review round 1 ──


async def test_sentinel_survives_when_its_scope_cannot_be_expanded(tmp_path):
    from main_logic.visit.forget import ClearingSentinels

    await seed_roster(tmp_path)
    sentinel = await ClearingSentinels(tmp_path).create(own_uid=OWN_A, scope="chars",
                                                        own_char_uids=[CHAR_UID_A])
    (tmp_path / "visit_peers.json").write_text("{broken", encoding="utf-8")
    report = await _recover(tmp_path)
    assert not report.forgets_clean
    assert [d["op_id"] for d in await ClearingSentinels(tmp_path).list_open()] == [sentinel["op_id"]]


async def test_pending_rename_is_reconciled_before_forget_replay(tmp_path):
    roster = await seed_roster(tmp_path)
    await roster.set_last_summary(PEER_X, "A", visit_id=vid(30), ended_at=1.0, text="要清掉", pair_id=PAIR)
    server = FakeMemoryServer()
    server.fail_always.add("scoped_forget")
    await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                        peer_uid=PEER_X, client=server.client())
    # While clearing journals hangs, character A renames to C and crashes before migration completes.
    await roster.set_last_summary(PEER_X, "A", visit_id=vid(31), ended_at=2.0, text="又写回", pair_id=PAIR)
    peers_path = tmp_path / "visit_peers.json"
    data = json.loads(peers_path.read_text(encoding="utf-8"))
    data["pending_rename"] = {"old": "A", "new": "C"}
    peers_path.write_text(json.dumps(data), encoding="utf-8")
    server.fail_always.clear()
    report = await _recover(tmp_path, server, list_char_names=_names("C", "B"),
                            resolve_char_name=resolver({CHAR_UID_A: "C"}))
    assert report.renamed and report.forgets_clean
    assert await roster.get_peer(PEER_X) is None


async def test_crashed_visit_of_a_forgotten_person_gets_no_chip(tmp_path):
    await seed_roster(tmp_path)
    server = FakeMemoryServer()
    server.fail_always.add("scoped_forget")
    await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                        peer_uid=PEER_X, client=server.client())
    spool = await make_visit(tmp_path, vid(32), [ln(0, "你好")], finalized=None)
    server.fail_always.clear()
    chips = Chips()
    await _recover(tmp_path, server, render_chips=chips)
    state = await spool.read_state()
    assert state["finalized"] == "crash" and state["debrief_choice"] == "forget"
    assert state["peer_uid"] is None and chips.calls == []


async def test_report_of_a_live_visit_waits_for_its_upload(tmp_path):
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    live = vid(33)
    (d / f"{live}.upload.json").write_text(json.dumps(_sealed(live)), encoding="utf-8")
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{live}.json").write_text(json.dumps({"visit_id": live}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(), submit_report=reports,
                   is_live=lambda visit_id: visit_id == live)
    assert reports.calls == [] and (reports_dir / f"{live}.json").exists()


async def test_stale_stream_next_to_a_sealed_upload_is_not_uploaded_twice(tmp_path):
    v = vid(34)
    _write_stream(tmp_path, v, [_header(v)])
    d = _spool_dir(tmp_path)
    (d / f"{v}.upload.json").write_text(json.dumps(_sealed(v)), encoding="utf-8")
    uploads = Uploads()
    await _recover(tmp_path, upload_transcript=uploads)
    await _recover(tmp_path, upload_transcript=uploads)
    assert [visit_id for visit_id, _ in uploads.calls] == [v]
    assert not list(d.glob(f"{v}.upload*"))


async def test_one_unsealable_stream_does_not_block_the_others(tmp_path, monkeypatch):
    from main_logic.visit import recovery

    bad, good = vid(35), vid(36)
    _write_stream(tmp_path, bad, [_header(bad)])
    _write_stream(tmp_path, good, [_header(good)])
    real_seal = recovery._seal_stream_sync

    def flaky(spool_dir, visit_id, reason, *rest):
        if visit_id == bad:
            raise PermissionError("locked by antivirus")
        return real_seal(spool_dir, visit_id, reason, *rest)

    monkeypatch.setattr(recovery, "_seal_stream_sync", flaky)
    uploads = Uploads()
    await _recover(tmp_path, upload_transcript=uploads)
    assert [visit_id for visit_id, _ in uploads.calls] == [good]
    assert (_spool_dir(tmp_path) / f"{bad}.upload.jsonl").exists()


async def test_forget_all_counts_people_not_logs(tmp_path):
    from main_logic.visit.forget_runner import forget_all

    await seed_roster(tmp_path)
    await seed_roster(tmp_path, own_char="B")
    server = FakeMemoryServer()
    server.fail_always.add("scoped_forget")
    outcome = await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A, "B": "e" * 32},
                               client=server.client())
    assert not outcome.done and outcome.forgotten == 0 and len(outcome.pending_logs) == 2


async def test_corrupt_sealed_upload_is_resealed_from_its_stream(tmp_path):
    v = vid(37)
    _write_stream(tmp_path, v, [_header(v), {"kind": "line", "lp": 0, "side": "host", "from": "own_cat",
                                             "ts": 1001.0, "text": "a", "truncated": False}])
    d = _spool_dir(tmp_path)
    (d / f"{v}.upload.json").write_text("{torn", encoding="utf-8")
    uploads = Uploads()
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, doc), = uploads.calls
    assert visit_id == v and [line["text"] for line in doc["request"]["lines"]] == ["a"]
    assert not list(d.glob(f"{v}.upload*"))


# ── Review round 3 ──


async def test_crash_marked_visit_without_chip_flag_gets_its_chip(tmp_path):
    await seed_roster(tmp_path)
    spool = await make_visit(tmp_path, vid(38), [ln(0, "你好")], finalized="crash")
    chips = Chips()
    await _recover(tmp_path, render_chips=chips)
    assert (await spool.read_state())["debrief_chip_pending"] is True
    assert chips.calls == [(vid(38), "A", "interrupted")]


async def test_crash_marker_and_chip_flag_are_written_together(tmp_path, monkeypatch):
    await seed_roster(tmp_path)
    spool = await make_visit(tmp_path, vid(39), [ln(0, "你好")], finalized=None)
    writes = []
    real_update = type(spool).update_state

    async def spy(self, **changes):
        writes.append(dict(changes))
        return await real_update(self, **changes)

    monkeypatch.setattr(type(spool), "update_state", spy)
    await _recover(tmp_path)
    assert {"finalized": "crash", "debrief_chip_pending": True} in writes


async def test_failing_cleanup_after_upload_does_not_block_the_rest(tmp_path, monkeypatch):
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    stuck, other = vid(40), vid(41)
    for v in (stuck, other):
        (d / f"{v}.upload.json").write_text(json.dumps(_sealed(v)), encoding="utf-8")
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == f"{stuck}.upload.json":
            raise PermissionError("read-only")
        return real_unlink(self, missing_ok=missing_ok)

    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{stuck}.json").write_text(json.dumps({"visit_id": stuck}), encoding="utf-8")
    monkeypatch.setattr(Path, "unlink", unlink)
    uploads = Uploads()
    reports = Reports()
    await _recover(tmp_path, upload_transcript=uploads, submit_report=reports)
    assert sorted(v for v, _ in uploads.calls) == [stuck, other]
    assert not (d / f"{other}.upload.json").exists()
    # Transcript accepted: failure to delete local upload files does not block queued reports.
    assert [v for v, _ in reports.calls] == [stuck]


async def test_forget_rechecks_visit_activity_under_the_admission_lock(tmp_path):
    import asyncio
    import contextlib

    from main_logic.visit.forget import ClearingSentinels
    from main_logic.visit.forget_runner import VisitActive

    await seed_roster(tmp_path)
    started = {"A": False}

    @contextlib.asynccontextmanager
    async def admission(_uid):
        started["A"] = True              # A visit starts after the unlocked check but before lock acquisition.
        yield

    with pytest.raises(VisitActive):
        await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                            peer_uid=PEER_X, client=FakeMemoryServer().client(),
                            admission_lock=admission, is_visit_active=lambda name: started[name])
    assert await ClearingSentinels(tmp_path).list_open() == []
    assert await RevocationLog.list_all_open(tmp_path) == []


async def test_partially_acquired_admission_locks_are_released(tmp_path):
    from main_logic.visit.forget_runner import forget_all

    events: list[str] = []

    class Admission:
        # Ordinary class, not generator: only explicit __aexit__ releases locks; garbage collection cannot release them for us.
        def __init__(self, uid):
            self.uid = uid

        async def __aenter__(self):
            if self.uid == "e" * 32:
                raise RuntimeError("admission store unavailable")
            events.append(f"enter {self.uid[:1]}")

        async def __aexit__(self, *exc):
            events.append(f"exit {self.uid[:1]}")

    with pytest.raises(RuntimeError):
        await forget_all(tmp_path, own_uid=OWN_A, chars={"A": "c" * 32, "B": "e" * 32},
                         client=FakeMemoryServer().client(), admission_lock=Admission)
    assert events == ["enter c", "exit c"]


async def test_schema_damaged_stream_lines_are_dropped_not_fatal(tmp_path):
    bad, good = vid(42), vid(43)
    _write_stream(tmp_path, bad, [
        _header(bad),
        {"kind": "line", "lp": None, "side": "host", "from": "own_cat", "ts": 1.0, "text": "x", "truncated": False},
        {"kind": "line", "lp": 1, "side": "host", "from": "own_cat", "ts": 1.0, "text": "ok", "truncated": False},
        {"kind": "usage", "ts": 10 ** 400, "d": {}},
    ])
    _write_stream(tmp_path, good, [_header(good)])
    uploads = Uploads()
    await _recover(tmp_path, upload_transcript=uploads)
    docs = dict(uploads.calls)
    assert set(docs) == {bad, good}
    assert [line["text"] for line in docs[bad]["request"]["lines"]] == ["ok"]


async def test_seal_validation_errors_only_skip_that_visit(tmp_path, monkeypatch):
    from main_logic.visit import recovery

    bad, good = vid(44), vid(45)
    _write_stream(tmp_path, bad, [_header(bad)])
    _write_stream(tmp_path, good, [_header(good)])
    real_seal = recovery._seal_stream_sync

    def flaky(spool_dir, visit_id, reason, *rest):
        if visit_id == bad:
            raise TypeError("'<' not supported between instances of 'NoneType' and 'int'")
        return real_seal(spool_dir, visit_id, reason, *rest)

    monkeypatch.setattr(recovery, "_seal_stream_sync", flaky)
    uploads = Uploads()
    await _recover(tmp_path, upload_transcript=uploads)
    assert [v for v, _ in uploads.calls] == [good]


async def test_forget_all_keeps_the_sentinel_when_a_peer_record_is_damaged(tmp_path):
    from main_logic.visit.forget import ClearingSentinels
    from main_logic.visit.forget_runner import forget_all
    from main_logic.visit.subjects import RosterCorruptError

    await seed_roster(tmp_path)
    path = tmp_path / "visit_peers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["accounts"][OWN_A]["peers"]["9" * 24] = {"display_name": "x", "by_char": "broken"}
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A},
                         client=FakeMemoryServer().client())
    assert len(await ClearingSentinels(tmp_path).list_open()) == 1


async def test_lifecycle_guard_is_held_for_the_whole_forget(tmp_path):
    import contextlib

    await seed_roster(tmp_path)
    events = []
    server = FakeMemoryServer()
    real_handler = server.handler

    async def handler(request):
        events.append("request")
        return await real_handler(request)

    server.handler = handler

    @contextlib.asynccontextmanager
    async def guard(uids):
        events.append(("enter", tuple(uids)))
        yield
        events.append("exit")

    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=server.client(), lifecycle_guard=guard)
    assert outcome.done
    assert events[0] == ("enter", (CHAR_UID_A,)) and events[-1] == "exit"
    assert "request" in events[1:-1]


# ── Review round 9 ──


async def test_undeletable_stale_stream_does_not_block_upload_or_reports(tmp_path, monkeypatch):
    v = vid(46)
    _write_stream(tmp_path, v, [_header(v)])
    d = _spool_dir(tmp_path)
    (d / f"{v}.upload.json").write_text(json.dumps(_sealed(v)), encoding="utf-8")
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(json.dumps({"visit_id": v}), encoding="utf-8")
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == f"{v}.upload.jsonl":
            raise PermissionError("locked")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    uploads, reports = Uploads(), Reports()
    await _recover(tmp_path, upload_transcript=uploads, submit_report=reports)
    # Servers are idempotent by visit_id + role: upload and submit reports normally.
    assert [visit_id for visit_id, _ in uploads.calls] == [v]
    assert [visit_id for visit_id, _ in reports.calls] == [v]


async def test_pending_preview_is_replayed_and_crash_keeps_its_status(tmp_path):
    await seed_roster(tmp_path)
    preview = await make_visit(tmp_path, vid(47), [ln(0)], debrief_choice="preview:diary",
                               debrief_pending={"diary": "d", "facts": []}, last_summary_done=True)
    await make_visit(tmp_path, vid(48), [ln(0)], finalized="crash",
                               debrief_choice="ask_later", debrief_chip_pending=True, last_summary_done=True)
    chips = Chips()
    await _recover(tmp_path, render_chips=chips)
    assert (await preview.read_state())["debrief_chip_pending"] is True
    assert sorted(chips.calls) == [(vid(47), "A", None), (vid(48), "A", "interrupted")]


async def test_retried_forget_reuses_the_open_sentinel(tmp_path):
    from main_logic.visit.forget import ClearingSentinels

    await seed_roster(tmp_path)
    server = FakeMemoryServer()
    server.fail_always.add("scoped_forget")
    first = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                peer_uid=PEER_X, client=server.client())
    assert not first.done and len(await ClearingSentinels(tmp_path).list_open()) == 1
    server.fail_always.clear()
    second = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                 peer_uid=PEER_X, client=server.client())
    assert second.done
    assert await ClearingSentinels(tmp_path).list_open() == []



async def test_forget_all_rejects_an_account_without_peers(tmp_path):
    from main_logic.visit.forget import ClearingSentinels
    from main_logic.visit.forget_runner import forget_all
    from main_logic.visit.subjects import RosterCorruptError

    (tmp_path / "visit_peers.json").write_text(json.dumps({"accounts": {OWN_A: {}}}), encoding="utf-8")
    with pytest.raises(RosterCorruptError):
        await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A}, client=FakeMemoryServer().client())
    assert len(await ClearingSentinels(tmp_path).list_open()) == 1



async def test_report_retry_is_not_stuck_behind_an_undeletable_stream(tmp_path, monkeypatch):
    v = vid(49)
    _write_stream(tmp_path, v, [_header(v)])
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(json.dumps({"visit_id": v}), encoding="utf-8")
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == f"{v}.upload.jsonl":
            raise PermissionError("locked")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    failing = Reports(ok=False)
    await _recover(tmp_path, upload_transcript=Uploads(), submit_report=failing)
    assert [visit_id for visit_id, _ in failing.calls] == [v]          # First report submission fails.
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(), submit_report=reports)
    assert [visit_id for visit_id, _ in reports.calls] == [v]           # Next pass still retries.
    assert not (reports_dir / f"{v}.json").exists()


@pytest.mark.parametrize("source", ["state", "spool_header"])
async def test_stream_header_without_owner_takes_the_visits_own_account(tmp_path, source):
    v = vid(52)
    other = "c" * 24
    # Sources occur independently: state-based cases omit transcripts; transcript-header cases remove state.json.
    await make_visit(tmp_path, v, [ln(0)], own_uid=other, last_summary_done=True,
                     write_jsonl=source == "spool_header")
    if source == "spool_header":
        (_spool_dir(tmp_path) / f"{v}.state.json").unlink()
    header = _header(v)
    header.pop("own_visit_uid")          # Earlier upload-header designs lacked this field.
    _write_stream(tmp_path, v, [header])
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, doc), = uploads.calls
    # Restore occupying-account identity so upload callbacks send when that account logs in.
    assert visit_id == v and doc["own_visit_uid"] == other


async def test_spool_header_of_another_visit_is_not_used_as_owner(tmp_path):
    v = vid(53)
    await make_visit(tmp_path, v, [ln(0)], own_uid="c" * 24, last_summary_done=True)
    spool_dir = _spool_dir(tmp_path)
    (spool_dir / f"{v}.state.json").unlink()
    jsonl = spool_dir / f"{v}.jsonl"
    lines = jsonl.read_text(encoding="utf-8").splitlines(keepends=True)
    misplaced = json.loads(lines[0])
    misplaced["visit_id"] = vid(54)                  # Transcript misplaced/copied from another visit.
    lines[0] = json.dumps(misplaced) + chr(10)
    jsonl.write_text("".join(lines), encoding="utf-8", newline="")
    header = _header(v)
    header.pop("own_visit_uid")
    _write_stream(tmp_path, v, [header])
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, doc), = uploads.calls
    assert visit_id == v and doc["own_visit_uid"] is None


async def test_stream_header_without_owner_is_still_sealed_not_deleted(tmp_path):
    v = vid(50)
    header = _header(v)
    header.pop("own_visit_uid")          # Earlier upload-header designs lacked this field.
    _write_stream(tmp_path, v, [header, {"kind": "line", "lp": 0, "side": "host", "from": "own_cat",
                                         "ts": 1001.0, "text": "a", "truncated": False}])
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, doc), = uploads.calls
    assert visit_id == v and doc["own_visit_uid"] is None
    assert [line["text"] for line in doc["request"]["lines"]] == ["a"]
    assert (_spool_dir(tmp_path) / f"{v}.upload.json").exists()     # Preserve the only copy.


async def test_unresolved_rename_defers_forget_replay_and_visit_recovery(tmp_path):
    await seed_roster(tmp_path)
    server = FakeMemoryServer()
    server.fail_always.add("scoped_forget")
    await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                        peer_uid=PEER_X, client=server.client())
    spool = await make_visit(tmp_path, vid(51), [ln(0)], finalized=None)
    peers_path = tmp_path / "visit_peers.json"
    data = json.loads(peers_path.read_text(encoding="utf-8"))
    data["pending_rename"] = {"old": "A", "new": "C"}
    peers_path.write_text(json.dumps(data), encoding="utf-8")
    server.fail_always.clear()
    server.requests.clear()
    # Both old/new names exist in configuration: rename is indeterminate, so clearing and per-visit backfill must wait.
    report = await _recover(tmp_path, server, list_char_names=_names("A", "C"))
    assert not report.forgets_clean and server.calls("scoped_forget") == []
    assert len(await RevocationLog.list_all_open(tmp_path)) == 1
    assert (await spool.read_state())["finalized"] is None


# ── Review round 11 ──


async def test_forget_replay_holds_the_lifecycle_guard_around_resolve_and_execute(tmp_path):
    import contextlib

    await seed_roster(tmp_path)
    server = FakeMemoryServer()
    server.fail_always.add("scoped_forget")
    await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                        peer_uid=PEER_X, client=server.client())
    server.fail_always.clear()
    events = []
    real_resolve = resolver()

    async def resolve(uid):
        events.append("resolve")
        return await real_resolve(uid)

    @contextlib.asynccontextmanager
    async def guard(uids):
        events.append(("enter", tuple(uids)))
        yield
        events.append("exit")

    real_handler = server.handler

    async def handler(request):
        if "scoped_forget" in str(request.url):
            events.append("forget")
        return await real_handler(request)

    server.handler = handler
    report = await _recover(tmp_path, server, resolve_char_name=resolve, lifecycle_guard=guard)
    assert report.forgets_clean
    # Sentinel expansion/journal replay resolve names by uid each time, with every clear guarded against intervening rename migration.
    depth = 0
    for event in events:
        if isinstance(event, tuple):
            depth += 1
        elif event == "exit":
            depth -= 1
        else:
            assert depth == 1, events
    assert "forget" in events


async def test_sealed_upload_of_another_visit_is_resealed_from_the_stream(tmp_path):
    v = vid(55)
    _write_stream(tmp_path, v, [_header(v), {"kind": "line", "lp": 0, "side": "host", "from": "own_cat",
                                             "ts": 1001.0, "text": "a", "truncated": False}])
    d = _spool_dir(tmp_path)
    foreign = {"v": 1, "own_visit_uid": OWN_A, "request": {"visit_id": vid(56), "role": "host",
                                                           "started_at": 1.0, "ended_at": 2.0,
                                                           "usage": {}, "lines": []}}
    (d / f"{v}.upload.json").write_text(json.dumps(foreign), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, doc), = uploads.calls
    # Other visits' uploads are invalid: reseal from streams, never deleting those streams as already-sealed remnants.
    assert visit_id == v and doc["request"]["visit_id"] == v
    assert [line["text"] for line in doc["request"]["lines"]] == ["a"]


async def test_sealed_upload_with_broken_lines_is_resealed_from_the_stream(tmp_path):
    v = vid(57)
    _write_stream(tmp_path, v, [_header(v), {"kind": "line", "lp": 0, "side": "host", "from": "own_cat",
                                             "ts": 1001.0, "text": "a", "truncated": False}])
    broken = {"v": 1, "own_visit_uid": OWN_A, "request": {"visit_id": v, "role": "host",
                                                          "started_at": 1.0, "ended_at": 2.0, "usage": {},
                                                          "lines": [{"lp": None, "text": 3}]}}
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(broken), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, doc), = uploads.calls
    # Uploads with corrupt transcript rows are invalid: reseal from complete streams.
    assert visit_id == v and [line["text"] for line in doc["request"]["lines"]] == ["a"]


# ── Review round 12 ──


async def test_sentinel_with_unresolvable_character_is_kept(tmp_path):
    from main_logic.visit.forget import ClearingSentinels
    from main_logic.visit.forget_runner import replay_forgets

    await seed_roster(tmp_path)
    sentinel = await ClearingSentinels(tmp_path).create(own_uid=OWN_A, scope="chars",
                                                       own_char_uids=[CHAR_UID_A])

    async def unresolved(_uid):
        return None        # Character configuration temporarily unreadable and replaced with defaults; uid cannot resolve names.

    clean = await replay_forgets(tmp_path, resolve_char_name=unresolved,
                                 client=FakeMemoryServer().client())
    assert clean is False
    # Unexpanded scope cannot mean nobody needs clearing and justify sentinel deletion.
    assert [d["op_id"] for d in await ClearingSentinels(tmp_path).list_open()] == [sentinel["op_id"]]


async def test_invalid_sealed_upload_is_not_uploaded_when_resealing_fails(tmp_path):
    v = vid(58)
    # Stream lacks a header: resealing returns None, treated as corrupt.
    _write_stream(tmp_path, v, [{"kind": "line", "lp": 0, "side": "host", "from": "own_cat",
                                 "ts": 1001.0, "text": "a", "truncated": False}])
    foreign = {"v": 1, "own_visit_uid": OWN_A, "request": {"visit_id": vid(59), "role": "host",
                                                           "started_at": 1.0, "ended_at": 2.0,
                                                           "usage": {}, "lines": []}}
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(foreign), encoding="utf-8")
    uploads = Uploads(ok=True)
    await _recover(tmp_path, upload_transcript=uploads)
    # Never give known-other-visit files to upload callbacks. If streams are corrupt too and transcripts unrecoverable, delete the files like corrupt streams,
    # never retaining them for next-pass upload.
    assert uploads.calls == []
    assert not (_spool_dir(tmp_path) / f"{v}.upload.json").exists()


@pytest.mark.parametrize("stream", ["missing", "missing-foreign", "unsealable"])
async def test_invalid_sealed_upload_never_reaches_upload_and_reports_follow_the_transcript(
    tmp_path, monkeypatch, stream,
):
    from main_logic.visit import recovery

    v = vid(60)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    body = json.dumps(_sealed(vid(61))) if stream == "missing-foreign" else "{torn"
    (d / f"{v}.upload.json").write_text(body, encoding="utf-8")
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(json.dumps({"visit_id": v}), encoding="utf-8")
    if stream == "unsealable":
        _write_stream(tmp_path, v, [_header(v)])

        def broken(*_args, **_kwargs):
            raise OSError("disk error")

        monkeypatch.setattr(recovery, "_seal_stream_sync", broken)
    uploads, reports = Uploads(ok=True), Reports()
    await _recover(tmp_path, upload_transcript=uploads, submit_report=reports)
    assert uploads.calls == []
    if stream != "unsealable":
        # No stream to reseal: transcript unrecoverable, delete corrupt files and submit reports normally.
        assert not (d / f"{v}.upload.json").exists()
        assert [visit_id for visit_id, _ in reports.calls] == [v]
    else:
        # Stream remains but resealing failed this pass: retain corrupt files for retry and hold reports.
        assert reports.calls == []


async def test_forget_all_retry_resumes_logs_of_people_already_removed_from_the_roster(tmp_path):
    from main_logic.visit.forget_runner import forget_all

    await seed_roster(tmp_path)
    server = FakeMemoryServer()
    calls = {"n": 0}

    async def flaky_void(_record):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("spool busy")     # remove_char completed, then void_pending failed.

    first = await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A},
                             client=server.client(), void_pending=flaky_void)
    assert first.done is False
    assert await PeerRoster(tmp_path, own_uid=OWN_A).peers_of_char("A") == []
    retry = await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A},
                             client=server.client(), void_pending=flaky_void)
    # Person already absent from roster: retry still resumes their open journal; completion requires finishing it.
    assert retry.done is True and calls["n"] == 2
    assert await RevocationLog.list_all_open(tmp_path) == []


@pytest.mark.parametrize("record", ["sentinel", "log"])
async def test_forget_in_progress_is_scoped_to_the_account(tmp_path, record):
    from main_logic.visit import memory_bridge
    from main_logic.visit.forget import ClearingSentinels
    from main_logic.visit.forget_runner import open_person_log

    if record == "sentinel":
        await ClearingSentinels(tmp_path).create(own_uid=OWN_A, scope="chars", own_char_uids=[CHAR_UID_A])
    else:
        await seed_roster(tmp_path)
        await open_person_log(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                              peer_uid=PEER_X)
    assert await memory_bridge.forget_in_progress(tmp_path, CHAR_UID_A, PEER_X, own_uid=OWN_A)
    # Rosters/memory are account-partitioned: account A clearing must not block account B's same peer under the same character.
    assert not await memory_bridge.forget_in_progress(tmp_path, CHAR_UID_A, PEER_X, own_uid=OWN_B)


async def test_forget_all_with_an_unresolvable_character_is_not_reported_done(tmp_path):
    from main_logic.visit.forget import ClearingSentinels
    from main_logic.visit.forget_runner import CharacterUnresolved, forget_all

    await seed_roster(tmp_path)

    async def unreadable_config(_uid):
        return None          # Character configuration temporarily unreadable; uid cannot resolve names.

    with pytest.raises(CharacterUnresolved):
        await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A},
                         client=FakeMemoryServer().client(), resolve_char_name=unreadable_config)
    # Nothing written and no false success: caller receives a retryable error.
    assert await ClearingSentinels(tmp_path).list_open() == []
    assert await PeerRoster(tmp_path, own_uid=OWN_A).peers_of_char("A") == [PEER_X]


@pytest.mark.parametrize("scope", ["person", "all"])
async def test_forget_uses_the_name_resolved_under_the_lifecycle_guard(tmp_path, scope):
    import contextlib

    from main_logic.visit.forget_runner import forget_all

    await seed_roster(tmp_path, own_char="B")          # Character renamed A->B before acquiring the guard.
    held = {"in": False}

    @contextlib.asynccontextmanager
    async def guard(_uids):
        held["in"] = True
        yield
        held["in"] = False

    async def current_name(uid):
        assert held["in"]                              # Resolve again under the guard.
        return "B" if uid == CHAR_UID_A else None

    server = FakeMemoryServer()
    names = []
    real_handler = server.handler

    async def handler(request):
        if request.url.path.endswith("/scoped_forget"):
            names.append(request.url.path.rsplit("/", 2)[-2])
        return await real_handler(request)

    server.handler = handler
    if scope == "person":
        outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                      peer_uid=PEER_X, client=server.client(), lifecycle_guard=guard,
                                      resolve_char_name=current_name)
    else:
        outcome = await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A},
                                   client=server.client(), lifecycle_guard=guard,
                                   resolve_char_name=current_name)
    assert outcome.done
    # Clear renamed B's entries, not empty entries under old A.
    assert await PeerRoster(tmp_path, own_uid=OWN_A).get_char_entry(PEER_X, "B") is None
    assert names and set(names) == {"B"}



@pytest.mark.parametrize("breakage", ["no_reason", "bad_usage", "bad_anomalies", "no_app_version"])
async def test_sealed_upload_missing_required_fields_is_resealed(tmp_path, breakage):
    v = vid(62)
    _write_stream(tmp_path, v, [_header(v), {"kind": "line", "lp": 0, "side": "host", "from": "own_cat",
                                             "ts": 1001.0, "text": "a", "truncated": False}])
    doc = _sealed(v)
    request = doc["request"]
    if breakage == "no_reason":
        del request["finalized_reason"]
    elif breakage == "bad_usage":
        request["usage"]["tts_chars"] = "many"
    elif breakage == "bad_anomalies":
        request["anomalies"] = -1
    else:
        del request["app_version"]
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(doc), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, uploaded), = uploads.calls
    # Structurally incomplete uploads cannot replace complete streams: reseal from streams.
    assert visit_id == v and [line["text"] for line in uploaded["request"]["lines"]] == ["a"]


async def test_replay_removes_sentinels_only_under_their_lifecycle_guard(tmp_path, monkeypatch):
    import contextlib

    from main_logic.visit.forget import ClearingSentinels
    from main_logic.visit.forget_runner import replay_forgets

    await seed_roster(tmp_path)
    await ClearingSentinels(tmp_path).create(own_uid=OWN_A, scope="chars", own_char_uids=[CHAR_UID_A])
    depth = {"n": 0}
    removed = []

    @contextlib.asynccontextmanager
    async def guard(_uids):
        depth["n"] += 1
        yield
        depth["n"] -= 1

    real_remove = ClearingSentinels.remove

    async def remove(self, op_id):
        removed.append(depth["n"])
        return await real_remove(self, op_id)

    monkeypatch.setattr(ClearingSentinels, "remove", remove)
    clean = await replay_forgets(tmp_path, resolve_char_name=resolver(), client=FakeMemoryServer().client(),
                                 lifecycle_guard=guard)
    assert clean is True
    # Rechecking remaining journals and deleting sentinels both hold the sentinel guard, preventing endpoint sentinel reuse from intervening.
    assert removed == [1]



@pytest.mark.parametrize("envelope", [{"own_visit_uid": 7}, {"own_visit_uid": "c" * 24},
                                      {"own_char_uid": "x"}, {"transport": "other"}],
                         ids=["owner_type", "owner_differs", "char_differs", "transport_differs"])
async def test_sealed_upload_with_a_damaged_envelope_is_resealed(tmp_path, envelope):
    v = vid(63)
    header = _header(v)
    _write_stream(tmp_path, v, [header, {"kind": "line", "lp": 0, "side": "host", "from": "own_cat",
                                         "ts": 1001.0, "text": "a", "truncated": False}])
    doc = _sealed(v)
    doc.update({"own_char_uid": header.get("own_char_uid"), "transport": header.get("transport")})
    doc.update(envelope)
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(doc), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, uploaded), = uploads.calls
    # Envelope/stream mismatch: reseal from streams, whose envelope is authoritative.
    assert visit_id == v and uploaded["own_visit_uid"] == OWN_A
    assert [line["text"] for line in uploaded["request"]["lines"]] == ["a"]


async def test_sealed_upload_with_a_malformed_owner_and_no_stream_is_not_uploaded(tmp_path):
    v = vid(64)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{v}.upload.json").write_text(json.dumps({**_sealed(v), "own_visit_uid": 7}), encoding="utf-8")
    uploads = Uploads(ok=True)
    await _recover(tmp_path, upload_transcript=uploads)
    # Corrupt occupying-account identity means no account can upload; without streams for resealing, treat as corrupt.
    assert uploads.calls == [] and not (d / f"{v}.upload.json").exists()


async def test_rename_is_not_reconciled_from_a_partially_readable_character_config(tmp_path, monkeypatch):
    from main_logic.visit import local_chars

    await seed_roster(tmp_path)
    peers_path = tmp_path / "visit_peers.json"
    data = json.loads(peers_path.read_text(encoding="utf-8"))
    data["pending_rename"] = {"old": "A", "new": "C"}
    peers_path.write_text(json.dumps(data), encoding="utf-8")

    async def partial():
        return {"A": CHAR_UID_A}            # New C entry is corrupt and silently filtered by normal loading.

    async def damaged():
        raise local_chars.CharactersUnreadable("character entry 'C' cannot be enumerated")

    monkeypatch.setattr(local_chars, "load_local_characters", partial)
    monkeypatch.setattr(local_chars, "ensure_characters_readable", damaged)
    report = await visit_spool_recovery(
        Chips(), None, config_dir=tmp_path, resolve_char_name=resolver(),
        client=FakeMemoryServer().client(), is_live=lambda _visit_id: False,
    )
    # Corrupt configuration entries prevent reconciliation: do not migrate data to old names; retain markers for retry.
    assert report.renamed is False
    assert json.loads(peers_path.read_text(encoding="utf-8"))["pending_rename"] == {"old": "A", "new": "C"}


async def test_forget_all_retry_with_a_new_character_removes_the_earlier_sentinel(tmp_path):
    from main_logic.visit.forget import ClearingSentinels
    from main_logic.visit.forget_runner import forget_all

    await seed_roster(tmp_path)
    server = FakeMemoryServer()
    server.fail_always.add("scoped_forget")
    first = await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A}, client=server.client())
    assert first.done is False and len(await ClearingSentinels(tmp_path).list_open()) == 1
    server.fail_always.clear()
    # Character B created between attempts changes sentinel character sets, preventing reuse of the previous sentinel.
    retry = await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A, "B": CHAR_UID_B},
                             client=server.client())
    assert retry.done is True
    # Delete prior sentinels fully covered by this scope too, removing admission blocks.
    assert await ClearingSentinels(tmp_path).list_open() == []


async def test_replay_defers_logs_while_a_rename_is_pending(tmp_path):
    from main_logic.visit.forget_runner import replay_forgets

    await seed_roster(tmp_path)
    server = FakeMemoryServer()
    server.fail_always.add("scoped_forget")
    await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                        peer_uid=PEER_X, client=server.client())
    server.fail_always.clear()
    server.requests.clear()
    peers_path = tmp_path / "visit_peers.json"
    data = json.loads(peers_path.read_text(encoding="utf-8"))
    data["pending_rename"] = {"old": "A", "new": "C"}     # Another rename crashes midway after backfill reconciliation.
    peers_path.write_text(json.dumps(data), encoding="utf-8")
    clean = await replay_forgets(tmp_path, resolve_char_name=resolver({CHAR_UID_A: "C"}),
                                 client=server.client())
    # Guarded detection of unreconciled rename: do not replay under new names or close journals; defer to next pass.
    assert clean is False and server.calls("scoped_forget") == []
    assert len(await RevocationLog.list_all_open(tmp_path)) == 1


async def test_person_forget_keeps_an_unexpanded_chars_sentinel_of_the_same_character(tmp_path):
    from main_logic.visit.forget import ClearingSentinels

    await seed_roster(tmp_path)
    # Same-character clear-all crashes before expansion: no journals exist yet, and scope exceeds single-person clearing.
    pending_all = await ClearingSentinels(tmp_path).create(own_uid=OWN_A, scope="chars",
                                                          own_char_uids=[CHAR_UID_A])
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    assert outcome.done
    assert [d["op_id"] for d in await ClearingSentinels(tmp_path).list_open()] == [pending_all["op_id"]]


async def test_forget_all_executes_a_pending_person_sentinel_for_someone_not_in_the_roster(tmp_path):
    from main_logic.visit.forget import ClearingSentinels
    from main_logic.visit.forget_runner import forget_all
    from main_logic.visit.subjects import derive_person_id, participant_subject

    await seed_roster(tmp_path)
    # Person absent from roster: cannot distinguish unexpanded single-person clearing from completed clearing before sentinel deletion.
    await ClearingSentinels(tmp_path).create(own_uid=OWN_A, scope="person",
                                             own_char_uids=[CHAR_UID_A], peer_uid=PEER_Y)
    server = FakeMemoryServer()
    outcome = await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A}, client=server.client())
    assert outcome.done
    # Clear-all opens and executes their journal too, clearing unprocessed cases and idempotently repeating completed ones, then deleting sentinels,
    # unblocking new visits for this character.
    person = participant_subject(derive_person_id(OWN_A, PEER_Y))
    assert person in [call["subject"] for call in server.calls("scoped_forget")]
    assert await ClearingSentinels(tmp_path).list_open() == []


async def test_forget_all_removes_a_person_sentinel_it_actually_executed(tmp_path):
    from main_logic.visit.forget import ClearingSentinels
    from main_logic.visit.forget_runner import forget_all

    await seed_roster(tmp_path)
    await ClearingSentinels(tmp_path).create(own_uid=OWN_A, scope="person",
                                             own_char_uids=[CHAR_UID_A], peer_uid=PEER_X)
    outcome = await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A},
                               client=FakeMemoryServer().client())
    # Person still rostered and cleared in this pass: delete their old single-person sentinel too.
    assert outcome.done and await ClearingSentinels(tmp_path).list_open() == []


async def test_person_sentinel_appearing_after_execution_is_not_removed(tmp_path, monkeypatch):
    from main_logic.visit.forget import ClearingSentinels
    from main_logic.visit.forget_runner import forget_all

    await seed_roster(tmp_path)
    real_list = ClearingSentinels.list_open
    created = {}
    calls = {"n": 0}

    async def list_open(self):
        docs = await real_list(self)
        calls["n"] += 1
        if calls["n"] == 2:
            # First listing reuses sentinels; second takes over in-scope single-person sentinels. Concurrent single-person
            # clearing appearing afterward was not executed by this clear-all.
            created["doc"] = await ClearingSentinels(tmp_path).create(
                own_uid=OWN_A, scope="person", own_char_uids=[CHAR_UID_A], peer_uid=PEER_Y,
            )
        return docs

    monkeypatch.setattr(ClearingSentinels, "list_open", list_open)
    outcome = await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A},
                               client=FakeMemoryServer().client())
    assert outcome.done
    monkeypatch.setattr(ClearingSentinels, "list_open", real_list)
    assert [d["op_id"] for d in await ClearingSentinels(tmp_path).list_open()] == [created["doc"]["op_id"]]



# ── User review (10-04) ──


async def test_rename_marker_of_a_deleted_character_is_dropped(tmp_path):
    await seed_roster(tmp_path)
    peers_path = tmp_path / "visit_peers.json"
    data = json.loads(peers_path.read_text(encoding="utf-8"))
    data["pending_rename"] = {"old": "Q0", "new": "Q1"}       # Both names absent: character deleted.
    peers_path.write_text(json.dumps(data), encoding="utf-8")
    report = await _recover(tmp_path)
    assert report.renamed is True
    assert "pending_rename" not in json.loads(peers_path.read_text(encoding="utf-8"))


async def _recover_with_chars(tmp_path, monkeypatch, chars, resolve, **kw):
    from main_logic.visit import local_chars

    async def load():
        return dict(chars)

    monkeypatch.setattr(local_chars, "load_local_characters", load)
    return await visit_spool_recovery(
        kw.pop("render_chips", Chips()), None, config_dir=tmp_path, resolve_char_name=resolver(resolve),
        client=FakeMemoryServer().client(), is_live=lambda _visit_id: False, **kw,
    )


def _set_rename_marker(tmp_path, marker):
    peers_path = tmp_path / "visit_peers.json"
    data = json.loads(peers_path.read_text(encoding="utf-8"))
    data["pending_rename"] = marker
    peers_path.write_text(json.dumps(data), encoding="utf-8")
    return peers_path


async def test_rename_marker_with_uid_moves_forward_by_the_uid(tmp_path, monkeypatch):
    await seed_roster(tmp_path)                                # Roster entries remain under old A.
    peers_path = _set_rename_marker(tmp_path, {"old": "A", "new": "C", "uid": CHAR_UID_A})
    report = await _recover_with_chars(tmp_path, monkeypatch, {"C": CHAR_UID_A, "B": CHAR_UID_B},
                                       {CHAR_UID_A: "C", CHAR_UID_B: "B"})
    assert report.renamed is True
    after = json.loads(peers_path.read_text(encoding="utf-8"))
    assert "pending_rename" not in after
    by_char = after["accounts"][OWN_A]["peers"][PEER_X]["by_char"]
    assert "C" in by_char and "A" not in by_char


async def test_rename_whose_old_name_was_reused_is_kept_and_only_blocks_those_names(tmp_path, monkeypatch):
    await seed_roster(tmp_path)
    peers_path = _set_rename_marker(tmp_path, {"old": "A", "new": "C", "uid": CHAR_UID_A})
    other = await make_visit(tmp_path, vid(75), [ln(0)], own_char="B", own_char_uid=CHAR_UID_B,
                             finalized=None, last_summary_done=True)
    # After rename, a new character A is created: name-only roster storage would mix two characters' records during migration.
    report = await _recover_with_chars(tmp_path, monkeypatch, {"A": CHAR_UID_B, "C": CHAR_UID_A},
                                       {CHAR_UID_A: "C", CHAR_UID_B: "A"})
    after = json.loads(peers_path.read_text(encoding="utf-8"))
    assert report.renamed is False and after["pending_rename"]["old"] == "A"
    assert "A" in after["accounts"][OWN_A]["peers"][PEER_X]["by_char"]   # Neither migrate nor mix.
    # Block only those two names; backfill other characters normally, skipping this visit under newly created A.
    assert (await other.read_state())["finalized"] is None


async def test_ambiguous_rename_does_not_defer_other_characters(tmp_path, monkeypatch):
    await seed_roster(tmp_path)
    _set_rename_marker(tmp_path, {"old": "Q0", "new": "Q1", "uid": "9" * 32})
    _ = await _recover_with_chars(tmp_path, monkeypatch, {"Q0": "8" * 32, "Q1": "9" * 32, "B": CHAR_UID_B},
                                  {CHAR_UID_B: "B", "8" * 32: "Q0", "9" * 32: "Q1"})
    spool = await make_visit(tmp_path, vid(76), [ln(0)], own_char="B", own_char_uid=CHAR_UID_B,
                             finalized=None, last_summary_done=True)
    await _recover_with_chars(tmp_path, monkeypatch, {"Q0": "8" * 32, "Q1": "9" * 32, "B": CHAR_UID_B},
                              {CHAR_UID_B: "B", "8" * 32: "Q0", "9" * 32: "Q1"})
    # Q0->Q1 cannot reconcile because the old name is occupied, unrelated to B; backfill B's crashed visit normally.
    assert (await spool.read_state())["finalized"] == "crash"


async def test_forget_halfway_then_character_deleted_does_not_wedge_the_others(tmp_path):
    from main_logic.visit.forget import ClearingSentinels
    from main_logic.visit.forget_runner import forget_all

    await seed_roster(tmp_path)
    await seed_roster(tmp_path, own_char="B")
    server = FakeMemoryServer()
    server.fail_always.add("scoped_forget")
    first = await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A, "B": CHAR_UID_B},
                             client=server.client())
    assert first.done is False
    server.fail_always.clear()
    # B is later deleted, confirmed by readable configuration: replay must not leave A in the same sentinel clearing forever.
    await _recover(tmp_path, server, resolve_char_name=resolver({CHAR_UID_A: "A"}),
                   list_char_names=_names("A"))
    assert await ClearingSentinels(tmp_path).list_open() == []
    # Finish/close A's journal; preserve deleted B's for retirement reconciliation without losing clear intent or blocking sentinels.
    remaining = await RevocationLog.list_all_open(tmp_path)
    assert [log["own_char_uid"] for log in remaining] == [CHAR_UID_B]
    from main_logic.visit import memory_bridge
    assert not await memory_bridge.forget_in_progress(tmp_path, CHAR_UID_A, PEER_X, own_uid=OWN_A)


async def test_unrelated_corrupt_state_does_not_block_a_forget(tmp_path):
    await seed_roster(tmp_path)
    other = await make_visit(tmp_path, vid(70), [ln(0)], own_char="B", own_char_uid=CHAR_UID_B)
    other.state_path.write_text("{torn", encoding="utf-8")   # Another character's state is corrupt but transcript headers remain.
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    assert outcome.done


async def test_forget_does_not_void_wiped_visits_of_another_account(tmp_path):
    await seed_roster(tmp_path)
    other = await make_visit(tmp_path, vid(71), [ln(0)], own_uid=OWN_B, finalized="wrap_up",
                             debrief_choice="ask_later")
    await other.delete_peer_fields()                           # Another account's later identity-erased visit.
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    assert outcome.done
    assert (await other.read_state())["debrief_choice"] == "ask_later"


async def test_crash_chip_is_replayed_on_every_start(tmp_path):
    v = vid(72)
    await make_visit(tmp_path, v, [ln(0)], finalized="crash", debrief_chip_pending=True,
                     last_summary_done=True)
    chips = Chips(delivered=False)
    await _recover(tmp_path, render_chips=chips)
    # Show again on second/subsequent startups, labeled unexpectedly interrupted.
    assert chips.calls == [(v, "A", "interrupted")]


async def test_any_finalized_visit_without_a_choice_gets_its_chip(tmp_path):
    v = vid(73)
    spool = await make_visit(tmp_path, v, [ln(0)], finalized="peer_left", last_summary_done=True)
    chips = Chips(delivered=False)
    await _recover(tmp_path, render_chips=chips)
    # Killed after normal finalization but before recording chips: backfill and display.
    assert chips.calls == [(v, "A", None)]
    assert (await spool.read_state())["debrief_chip_pending"] is True



async def test_state_only_corrupt_visit_is_dropped_by_the_wipe_not_blocking(tmp_path):
    await seed_roster(tmp_path)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    corrupt = d / f"{vid(74)}.state.json"
    corrupt.write_text("{torn", encoding="utf-8")              # Only corrupt state remains, with unknown ownership.
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # Stop blocking clearing; deleting it is appropriate if it belongs to the cleared person.
    assert outcome.done is True and not corrupt.exists()


async def test_one_lock_held_state_does_not_hide_the_previous_visit_from_the_handoff(tmp_path, monkeypatch):
    from main_logic.visit import spool as spool_module
    from main_logic.visit.memory_commit import last_summary_handoff

    await seed_roster(tmp_path)
    previous = await make_visit(tmp_path, vid(81), [ln(0)], finalized="wrap_up")
    # Visit with only state.json, unknown owner, temporarily locked/unreadable.
    other = await make_visit(tmp_path, vid(82), [], own_char="B", own_char_uid=CHAR_UID_B,
                             memory_enabled=False)
    real_read = spool_module._read_state_file

    def flaky(path):
        if path == other.state_path:
            raise PermissionError("locked")                    # Unrelated visit's state temporarily unreadable.
        return real_read(path)

    monkeypatch.setattr(spool_module, "_read_state_file", flaky)
    started = []

    async def start(spool):
        started.append(spool.visit_id)
        return True

    await last_summary_handoff(tmp_path, own_uid=OWN_A, own_char_uid=CHAR_UID_A, peer_uid=PEER_X,
                               start_summary=start, is_live=lambda _v: False)
    # Handoff uses permissive lookup: skip unreadable entries and still wait for this pair's previous visit.
    assert started == [previous.visit_id]


async def test_injected_names_still_get_the_strict_character_check(tmp_path, monkeypatch):
    from main_logic.visit import local_chars

    await seed_roster(tmp_path)
    server = FakeMemoryServer()
    server.fail_always.add("scoped_forget")
    await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                        peer_uid=PEER_X, client=server.client())
    server.fail_always.clear()
    server.requests.clear()

    async def damaged():
        raise local_chars.CharactersUnreadable("character entry cannot be enumerated")

    monkeypatch.setattr(local_chars, "ensure_characters_readable", damaged)
    # Even caller-injected lists require strict checks first, or unresolved names below would be mistaken for deleted characters.
    report = await _recover(tmp_path, server, resolve_char_name=resolver({}), list_char_names=_names())
    assert report.renamed is False and server.calls("scoped_forget") == []
    assert len(await RevocationLog.list_all_open(tmp_path)) == 1


async def test_is_live_must_be_given(tmp_path):
    # Background backfill must explicitly receive is_live; omission prematurely seals in-flight streams and deletes outboxes.
    with pytest.raises(TypeError):
        await visit_spool_recovery(Chips(), None, config_dir=tmp_path)



async def test_sealed_upload_with_out_of_order_lines_is_resealed(tmp_path):
    v = vid(77)
    line = {"kind": "line", "side": "host", "from": "own_cat", "ts": 1001.0, "truncated": False}
    _write_stream(tmp_path, v, [_header(v), {**line, "lp": 0, "text": "a"}, {**line, "lp": 1, "text": "b"}])
    doc = _sealed(v)
    doc["request"]["lines"] = [
        {"lp": 1, "side": "host", "from": "own_cat", "ts": 1001.0, "text": "b", "truncated": False},
        {"lp": 0, "side": "host", "from": "own_cat", "ts": 1001.0, "text": "a", "truncated": False},
    ]
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(doc), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (_, uploaded), = uploads.calls
    # Unordered sealed files cannot replace streams: reseal from streams sorted by (lp, side).
    assert [entry["text"] for entry in uploaded["request"]["lines"]] == ["a", "b"]


async def test_rename_reconciliation_holds_the_lifecycle_guard(tmp_path, monkeypatch):
    import contextlib

    await seed_roster(tmp_path)
    _set_rename_marker(tmp_path, {"old": "A", "new": "C", "uid": CHAR_UID_A})
    held = {"uids": None, "depth": 0}
    real_rename = PeerRoster.rename_char

    @contextlib.asynccontextmanager
    async def guard(uids):
        held["depth"] += 1
        held["uids"] = list(uids)
        yield
        held["depth"] -= 1

    async def rename(self, old, new):
        assert held["depth"] == 1                              # Migration occurs under the guard.
        return await real_rename(self, old, new)

    monkeypatch.setattr(PeerRoster, "rename_char", rename)
    report = await _recover_with_chars(tmp_path, monkeypatch, {"C": CHAR_UID_A}, {CHAR_UID_A: "C"},
                                       lifecycle_guard=guard)
    assert report.renamed is True and held["uids"] == [CHAR_UID_A]


async def test_corrupt_state_of_a_wiped_visit_of_this_character_is_dropped(tmp_path):
    await seed_roster(tmp_path)
    wiped = await make_visit(tmp_path, vid(78), [ln(0)], finalized="wrap_up")
    await wiped.delete_peer_fields()                           # Identity-erased visit for this character.
    wiped.state_path.write_text("{torn", encoding="utf-8")     # state.json becomes corrupt afterward.
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # Unusable corrupt state no longer blocks character clearing forever; identity erasure deletes it, leaving no residual identity.
    assert outcome.done and not wiped.state_path.exists()


async def test_lookup_alone_never_deletes_a_corrupt_state(tmp_path):
    from main_logic.visit.spool import VisitSpool
    from main_logic.visit.subjects import derive_pair_id as _pair

    await seed_roster(tmp_path)
    wiped = await make_visit(tmp_path, vid(79), [ln(0)], finalized="wrap_up")
    await wiped.delete_peer_fields()
    wiped.state_path.write_text("{torn", encoding="utf-8")
    found = await VisitSpool.find_visits_for_pairs(tmp_path, CHAR_UID_A, [_pair(OWN_A, PEER_Y)])
    # Lookup, also used by opening handoff, skips without deleting.
    assert found == [] and wiped.state_path.exists()


async def test_malformed_rename_marker_is_dropped(tmp_path):
    await seed_roster(tmp_path)
    peers_path = _set_rename_marker(tmp_path, "garbled")
    report = await _recover(tmp_path)
    # No reconcilable information: record diagnostics and clear, no longer blocking backfill/clearing permanently.
    assert report.renamed is True
    assert "pending_rename" not in json.loads(peers_path.read_text(encoding="utf-8"))


async def test_corrupt_wiped_state_of_another_account_is_left_alone(tmp_path):
    await seed_roster(tmp_path)
    other = await make_visit(tmp_path, vid(80), [ln(0)], own_uid=OWN_B, finalized="wrap_up")
    await other.delete_peer_fields()
    other.state_path.write_text("{torn", encoding="utf-8")
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # Another account's corrupt state is outside scope: neither block nor delete.
    assert outcome.done and other.state_path.exists()


async def test_rename_reconciliation_reloads_names_under_the_guard(tmp_path, monkeypatch):
    import contextlib

    from main_logic.visit import local_chars

    await seed_roster(tmp_path)
    _set_rename_marker(tmp_path, {"old": "A", "new": "C", "uid": CHAR_UID_A})
    table = {"C": CHAR_UID_A}

    async def load():
        return dict(table)

    @contextlib.asynccontextmanager
    async def guard(_uids):
        table.clear()                                          # Character deleted while awaiting guard.
        yield

    monkeypatch.setattr(local_chars, "load_local_characters", load)
    report = await visit_spool_recovery(
        Chips(), None, config_dir=tmp_path, resolve_char_name=resolver({}),
        client=FakeMemoryServer().client(), is_live=lambda _v: False, lifecycle_guard=guard,
    )
    # Guarded reread finds deletion: discard markers without roster migration, never migrate forward from stale snapshots.
    assert report.renamed is True
    after = json.loads((tmp_path / "visit_peers.json").read_text(encoding="utf-8"))
    assert "A" in after["accounts"][OWN_A]["peers"][PEER_X]["by_char"]


async def test_rename_guard_is_retaken_when_the_marker_changes_meanwhile(tmp_path, monkeypatch):
    import contextlib

    await seed_roster(tmp_path)
    await seed_roster(tmp_path, own_char="B")
    peers_path = _set_rename_marker(tmp_path, {"old": "A", "new": "C", "uid": CHAR_UID_A})
    taken = []

    @contextlib.asynccontextmanager
    async def guard(uids):
        taken.append(list(uids))
        if len(taken) == 1:
            # While awaiting guard, another character's B->D rename replaces the marker.
            data = json.loads(peers_path.read_text(encoding="utf-8"))
            data["pending_rename"] = {"old": "B", "new": "D", "uid": CHAR_UID_B}
            peers_path.write_text(json.dumps(data), encoding="utf-8")
        yield

    report = await _recover_with_chars(tmp_path, monkeypatch, {"C": CHAR_UID_A, "D": CHAR_UID_B},
                                       {CHAR_UID_A: "C", CHAR_UID_B: "D"}, lifecycle_guard=guard)
    # Reacquire B's guard for the new marker before reconciliation, never migrate B while holding A's guard.
    assert taken == [[CHAR_UID_A], [CHAR_UID_B]] and report.renamed is True
    by_char = json.loads(peers_path.read_text(encoding="utf-8"))["accounts"][OWN_A]["peers"][PEER_X]["by_char"]
    assert "D" in by_char and "B" not in by_char


def _schema_invalid_state(spool):
    data = json.loads(spool.state_path.read_text(encoding="utf-8"))
    data["field_from_a_newer_version"] = 1                      # Parseable but incompatible with current schema.
    spool.state_path.write_text(json.dumps(data), encoding="utf-8")


async def test_schema_invalid_state_of_another_character_is_skipped_not_deleted(tmp_path):
    await seed_roster(tmp_path)
    other = await make_visit(tmp_path, vid(83), [], own_char="B", own_char_uid=CHAR_UID_B,
                             memory_enabled=False)               # Only state.json remains.
    _schema_invalid_state(other)
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # Raw fields explicitly identify another character: neither block clearing nor delete; other versions can still read it.
    assert outcome.done and other.state_path.exists()


async def test_schema_invalid_state_that_may_be_ours_blocks_and_is_kept(tmp_path):
    await seed_roster(tmp_path)
    mine = await make_visit(tmp_path, vid(84), [], memory_enabled=False)   # Character A, this pair, state only.
    _schema_invalid_state(mine)
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # Potentially this person's visit: erase identity in raw objects, preserving new-version fields/files. Debrief still needs invalidation,
    # unsupported by this version, so do not mark clearing complete; retain for retry.
    raw = json.loads(mine.state_path.read_text(encoding="utf-8"))
    assert outcome.done is False and raw["pair_id"] is None and raw["peer_uid"] is None
    assert raw["field_from_a_newer_version"] == 1


async def test_rename_marker_swapped_while_reloading_names_retakes_the_guard(tmp_path, monkeypatch):
    import contextlib

    from main_logic.visit import local_chars

    await seed_roster(tmp_path)
    await seed_roster(tmp_path, own_char="B")
    peers_path = _set_rename_marker(tmp_path, {"old": "A", "new": "C", "uid": CHAR_UID_A})
    taken = []
    loads = {"n": 0}

    async def load():
        loads["n"] += 1
        if loads["n"] == 3:
            # During guarded roster reread (first startup read, then second after retirement replay), another B->D rename replaces the marker.
            data = json.loads(peers_path.read_text(encoding="utf-8"))
            data["pending_rename"] = {"old": "B", "new": "D", "uid": CHAR_UID_B}
            peers_path.write_text(json.dumps(data), encoding="utf-8")
        return {"C": CHAR_UID_A, "D": CHAR_UID_B}

    @contextlib.asynccontextmanager
    async def guard(uids):
        taken.append(list(uids))
        yield

    monkeypatch.setattr(local_chars, "load_local_characters", load)
    report = await visit_spool_recovery(
        Chips(), None, config_dir=tmp_path, resolve_char_name=resolver({CHAR_UID_A: "C", CHAR_UID_B: "D"}),
        client=FakeMemoryServer().client(), is_live=lambda _v: False, lifecycle_guard=guard,
    )
    # Recheck the acquisition-time marker before reconciliation; changed markers require reacquiring B's guard.
    assert taken[:2] == [[CHAR_UID_A], [CHAR_UID_B]] and report.renamed is True


async def test_schema_invalid_state_does_not_hide_a_header_that_names_this_pair(tmp_path):
    await seed_roster(tmp_path)
    mine = await make_visit(tmp_path, vid(85), [ln(0)], finalized="wrap_up")   # Header identifies this pair.
    data = json.loads(mine.state_path.read_text(encoding="utf-8"))
    data["pair_id"] = derive_pair_id(OWN_A, PEER_Y)            # Schema-incompatible state points to another pair.
    data["field_from_a_newer_version"] = 1
    mine.state_path.write_text(json.dumps(data), encoding="utf-8")
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # Header still identifies this pair; raw state alone cannot justify skipping. Erase identity in raw objects, preserving new-version fields,
    # but defer clearing because this version cannot invalidate its debrief.
    raw = json.loads(mine.state_path.read_text(encoding="utf-8"))
    assert outcome.done is False and raw["pair_id"] is None and raw["field_from_a_newer_version"] == 1


async def test_schema_invalid_wiped_state_of_another_account_does_not_block(tmp_path):
    await seed_roster(tmp_path)
    other = await make_visit(tmp_path, vid(86), [], own_uid=OWN_B, memory_enabled=False)
    await other.delete_peer_fields()                            # Identity erased, pair_id empty.
    data = json.loads(other.state_path.read_text(encoding="utf-8"))
    data["field_from_a_newer_version"] = 1
    other.state_path.write_text(json.dumps(data), encoding="utf-8")
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # Raw account belongs elsewhere: neither block clearing nor delete.
    assert outcome.done and other.state_path.exists()


async def test_schema_invalid_state_naming_this_pair_is_not_excluded_by_another_header(tmp_path):
    await seed_roster(tmp_path)
    mine = await make_visit(tmp_path, vid(87), [ln(0)], finalized="wrap_up")
    lines = mine.jsonl_path.read_bytes().splitlines(keepends=True)
    header = json.loads(lines[0])
    # Header replaced with a self-consistent valid header for another pair.
    header.update(peer_uid=PEER_Y, pair_id=derive_pair_id(OWN_A, PEER_Y),
                  peer_char_id=derive_peer_char_id(PEER_Y, header["peer_char_tag"]))
    lines[0] = json.dumps(header, ensure_ascii=False).encode() + b"\n"
    mine.jsonl_path.write_bytes(b"".join(lines))
    data = json.loads(mine.state_path.read_text(encoding="utf-8"))
    assert data["pair_id"] == derive_pair_id(OWN_A, PEER_X)    # Raw state still identifies this pair.
    data["field_from_a_newer_version"] = 1
    mine.state_path.write_text(json.dumps(data), encoding="utf-8")
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # Peer fields remain in state: header alone cannot exclude it and finalize clearing.
    assert outcome.done is False


def _stream_records(v):
    return [_header(v)] + [
        {"kind": "line", "lp": i, "side": "host", "from": "own_cat", "ts": 1001.0 + i,
         "text": f"t{i}", "truncated": False}
        for i in range(2)
    ] + [{"kind": "usage", "d": {"llm_input_tokens": 5}}]


async def test_sealed_upload_missing_content_is_resealed_from_the_stream(tmp_path):
    from main_logic.visit.recovery import build_upload_doc

    v = vid(88)
    records = _stream_records(v)
    _write_stream(tmp_path, v, records)
    doc = build_upload_doc(records, visit_id=v, finalized_reason=None)
    doc["request"]["lines"] = doc["request"]["lines"][:1]       # Matching envelope and valid structure, but missing one row.
    doc["request"]["usage"]["llm_input_tokens"] = 0
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(doc), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, uploaded), = uploads.calls
    # Differs from resealed streams: complete streams are authoritative; never delete them in favor of incomplete files.
    assert [line["text"] for line in uploaded["request"]["lines"]] == ["t0", "t1"]
    assert uploaded["request"]["usage"]["llm_input_tokens"] == 5


async def test_matching_sealed_upload_keeps_its_reason_when_state_is_gone(tmp_path):
    from main_logic.visit.recovery import build_upload_doc

    v = vid(89)
    records = _stream_records(v)
    stream = _write_stream(tmp_path, v, records)
    doc = build_upload_doc(records, visit_id=v, finalized_reason="wrap_up")
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(doc), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, uploaded), = uploads.calls
    # Matching sealed files are canonical: delete streams and upload unchanged; absent state must not change ending reasons to crash.
    assert uploaded == doc and not stream.exists()


@pytest.mark.parametrize("include_transcript", [False, True, None])
async def test_report_without_transcript_is_not_held_by_a_pending_upload(tmp_path, include_transcript):
    v = vid(90)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    (d / f"{v}.upload.json").write_text(json.dumps(_sealed(v)), encoding="utf-8")
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    queued = {"visit_id": v}
    if include_transcript is not None:
        queued["include_transcript"] = include_transcript
    (reports_dir / f"{v}.json").write_text(json.dumps(queued), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    # Transcript upload fails: submit explicitly transcript-free reports normally; attached/unspecified reports await transcripts.
    assert [visit_id for visit_id, _ in reports.calls] == ([v] if include_transcript is False else [])


async def test_sealed_normal_end_is_not_resealed_as_crash(tmp_path):
    from main_logic.visit.recovery import build_upload_doc

    await seed_roster(tmp_path)
    v = vid(91)
    await make_visit(tmp_path, v, [ln(0)], finalized=None)      # State has not recorded finalized.
    records = _stream_records(v)
    stream = _write_stream(tmp_path, v, records)
    # Normal finalization wrote uploads, then crashed before stream deletion/state.finalized.
    doc = build_upload_doc(records, visit_id=v, finalized_reason="wrap_up")
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(doc), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, uploaded), = uploads.calls
    # Backfill marks state crash, but upload the file's original normal ending and remove residual streams.
    assert uploaded == doc and uploaded["request"]["finalized_reason"] == "wrap_up"
    assert not stream.exists()


async def test_sealed_reason_must_match_a_definitive_state_reason(tmp_path):
    from main_logic.visit.recovery import build_upload_doc

    await seed_roster(tmp_path)
    v = vid(92)
    await make_visit(tmp_path, v, [ln(0)], finalized="peer_left")
    records = _stream_records(v)
    _write_stream(tmp_path, v, records)
    doc = build_upload_doc(records, visit_id=v, finalized_reason="wrap_up")   # Inconsistent with state.
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(doc), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, uploaded), = uploads.calls
    # State records a definite ending reason: use it to reseal streams.
    assert uploaded["request"]["finalized_reason"] == "peer_left"


async def test_upload_progress_survives_an_undeletable_stale_stream(tmp_path, monkeypatch):
    from main_logic.visit.recovery import build_upload_doc

    v = vid(93)
    records = _stream_records(v)
    stream = _write_stream(tmp_path, v, records)
    sealed = _spool_dir(tmp_path) / f"{v}.upload.json"
    doc = build_upload_doc(records, visit_id=v, finalized_reason="wrap_up")
    sealed.write_text(json.dumps({**doc, "chunk_progress": {"next": 3}}), encoding="utf-8")
    real_unlink = Path.unlink

    def stubborn(self, missing_ok=False):
        if self == stream:
            raise PermissionError("locked by antivirus")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", stubborn)
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, uploaded), = uploads.calls
    # Upload callback's persisted chunk progress does not mismatch streams: never reseal/reset progress.
    assert uploaded["chunk_progress"] == {"next": 3}
    assert json.loads(sealed.read_text(encoding="utf-8"))["chunk_progress"] == {"next": 3}


async def test_unreadable_stream_next_to_a_sealed_upload_keeps_both(tmp_path, monkeypatch):
    from main_logic.visit import recovery
    from main_logic.visit.recovery import build_upload_doc

    v = vid(94)
    records = _stream_records(v)
    stream = _write_stream(tmp_path, v, records)
    sealed = _spool_dir(tmp_path) / f"{v}.upload.json"
    doc = build_upload_doc(records, visit_id=v, finalized_reason="wrap_up")
    doc["request"]["lines"] = doc["request"]["lines"][:1]      # Sealed file lacks rows.
    sealed.write_text(json.dumps(doc), encoding="utf-8")

    def locked(path):
        raise PermissionError("locked by antivirus")

    monkeypatch.setattr(recovery, "_read_stream", locked)
    uploads = Uploads()
    await _recover(tmp_path, upload_transcript=uploads)
    # Existing stream unreadable: comparison impossible; preserve both and defer upload rather than send incomplete files as canonical.
    assert uploads.calls == [] and stream.exists() and sealed.exists()


@pytest.mark.parametrize("envelope", [{"own_char_uid": None}, {"own_char_uid": {"x": 1}},
                                      {"transport": {"x": 1}}, {"transport": ["trtc"]}, {"transport": None}],
                         ids=["char_null", "char_object", "transport_object", "transport_list", "transport_null"])
async def test_sealed_upload_with_a_bad_envelope_and_no_stream_is_not_uploaded(tmp_path, envelope):
    v = vid(95)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{v}.upload.json").write_text(json.dumps({**_sealed(v), **envelope}), encoding="utf-8")
    uploads = Uploads(ok=True)
    await _recover(tmp_path, upload_transcript=uploads)
    # Upload file only, no comparison stream: corrupt character ID/transport also means corruption, never pass to callbacks.
    assert uploads.calls == [] and not (d / f"{v}.upload.json").exists()



async def test_expired_upload_marks_its_queued_report_transcript_unavailable(tmp_path):
    v = vid(97)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps(_sealed(v)), encoding="utf-8")
    old = time.time() - 8 * 86400
    os.utime(sealed, (old, old))                                 # Pending transcript older than seven days.
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(json.dumps({"visit_id": v, "include_transcript": True}), encoding="utf-8")
    reports = Reports()
    # Retry once this startup before abandonment; still fails.
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    # Transcript expires/abandoned: submit reports normally with transcript-unavailability reasons.
    (visit_id, doc), = reports.calls
    assert not sealed.exists() and doc["transcript_unavailable"] == "expired"
    assert doc["include_transcript"] is True


async def test_recovery_digest_protects_family_names_in_peer_labels(tmp_path):
    await seed_roster(tmp_path, peer_display="妈妈")
    await make_visit(tmp_path, vid(98), [ln(i, f"line {i}", ("own_cat", "peer_cat", "peer_human", "own_human")[i % 4])
                                         for i in range(8)])
    server = FakeMemoryServer()
    await _recover(tmp_path, server, family_names=["妈妈"])
    segments = server.calls("scoped_history")[1]["segments"]
    # Backfilled digests also receive family addresses: replace peer self-description 妈妈 with generic labels.
    assert "妈妈" not in {seg["speaker_label"] for seg in segments}


async def test_report_is_not_marked_unavailable_while_another_upload_copy_remains(tmp_path):
    from main_logic.visit.recovery import build_upload_doc

    v = vid(99)
    records = _stream_records(v)
    stream = _write_stream(tmp_path, v, records)
    sealed = _spool_dir(tmp_path) / f"{v}.upload.json"
    sealed.write_text(json.dumps(build_upload_doc(records, visit_id=v, finalized_reason="wrap_up")), encoding="utf-8")
    old = time.time() - 8 * 86400
    os.utime(stream, (old, old))                                 # Only streams expired/deleted; sealed files are fresh.
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(json.dumps({"visit_id": v, "include_transcript": True}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(), submit_report=reports)
    # Upload sealed files normally this pass; reports must not include transcript-unavailable diagnostics.
    (visit_id, doc), = reports.calls
    assert not stream.exists() and "transcript_unavailable" not in doc


@pytest.mark.parametrize("damage", ["sealed_only", "stream_only"])
async def test_corrupt_upload_marks_its_queued_report_transcript_unavailable(tmp_path, damage):
    v = vid(100)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    if damage == "sealed_only":
        (d / f"{v}.upload.json").write_text("{torn", encoding="utf-8")       # Only corrupt sealed files remain.
    else:
        _write_stream(tmp_path, v, [{"kind": "line"}])                       # Stream lacks headers: corrupt.
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(json.dumps({"visit_id": v, "include_transcript": True}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(), submit_report=reports)
    # Transcript corrupt and permanently unuploadable: submit reports normally with reasons.
    (visit_id, doc), = reports.calls
    assert doc["transcript_unavailable"] == "corrupt" and not list(d.glob(f"{v}.upload*"))


async def test_queued_report_of_another_visit_is_moved_aside_not_submitted(tmp_path):
    v, other = vid(101), vid(102)
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    path = reports_dir / f"{v}.json"
    body = json.dumps({"visit_id": other, "include_transcript": False})
    path.write_text(body, encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, submit_report=reports)
    # Content belongs to another visit: uploading reports the wrong person, then acceptance deletes the intended report. Neither submit nor delete; quarantine by rename,
    # freeing this visit's slot, which otherwise blocks later reports indefinitely.
    assert reports.calls == [] and not path.exists()
    assert (reports_dir / f"{v}.json.mismatch").read_text(encoding="utf-8") == body
    # Newly queued reports submit after the slot opens; preserve earlier quarantined copies without overwriting.
    path.write_text(json.dumps({"visit_id": other}), encoding="utf-8")
    await _recover(tmp_path, submit_report=reports)
    assert (reports_dir / f"{v}.json.mismatch").read_text(encoding="utf-8") == body
    assert (reports_dir / f"{v}.json.1.mismatch").exists()
    path.write_text(json.dumps({"visit_id": v}), encoding="utf-8")
    await _recover(tmp_path, submit_report=reports)
    assert [visit_id for visit_id, _ in reports.calls] == [v] and not path.exists()


async def test_terminal_upload_rejection_marks_the_queued_report(tmp_path):
    v = vid(103)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps(_sealed(v)), encoding="utf-8")
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(json.dumps({"visit_id": v, "include_transcript": True}), encoding="utf-8")

    async def reject(_visit_id, _doc):
        return "parts_out_of_range"                              # Terminal rejection allows file deletion, but transcripts never reach Servers.

    reports = Reports()
    await _recover(tmp_path, upload_transcript=reject, submit_report=reports)
    (visit_id, doc), = reports.calls
    assert not sealed.exists() and doc["transcript_unavailable"] == "parts_out_of_range"


async def test_oversized_state_number_does_not_abort_upload_recovery(tmp_path):
    bad, good = vid(104), vid(105)
    for v in (bad, good):
        await make_visit(tmp_path, v, [ln(0)], finalized="wrap_up")
        _write_stream(tmp_path, v, _stream_records(v))
    state_path = _spool_dir(tmp_path) / f"{bad}.state.json"
    data = json.loads(state_path.read_text(encoding="utf-8"))
    data["digest_writes"] = {"0": {"requested_at": 10 ** 400, "through_lp": 0, "group": {}, "segments": {}}}
    state_path.write_text(json.dumps(data), encoding="utf-8")     # Integer exceeds floating-point range.
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    # Treat corrupt visit state as unreadable; OverflowError must not abort the pass, and other visits upload normally.
    assert {visit_id for visit_id, _ in uploads.calls} >= {good}


def test_discarded_records_do_not_move_the_upload_end_time():
    from main_logic.visit.recovery import build_upload_doc

    v = vid(106)
    records = _stream_records(v) + [
        {"kind": "line", "lp": 9, "ts": 99999.0},                     # Corrupt row, discarded.
        {"kind": "mystery", "ts": 88888.0},                           # Unrecognized record.
        {"kind": "usage", "ts": 77777.0, "d": {"llm_input_tokens": "bad"}},   # No valid usage records.
    ]
    doc = build_upload_doc(records, visit_id=v, finalized_reason="wrap_up")
    # Discarded records' timestamps cannot shift ending times/durations.
    assert doc["request"]["ended_at"] == 1002.0
    assert doc["request"]["usage"]["duration_s"] == 2


async def test_report_carries_the_unavailable_marker_even_when_the_file_cannot_be_rewritten(tmp_path, monkeypatch):
    from main_logic.visit import recovery as recovery_mod

    v = vid(107)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    (d / f"{v}.upload.json").write_text(json.dumps(_sealed(v)), encoding="utf-8")
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(json.dumps({"visit_id": v, "include_transcript": True}), encoding="utf-8")
    real_write = recovery_mod._write_private_json

    def disk_full(path, data):
        if Path(path).parent == reports_dir:
            raise OSError("no space left on device")
        return real_write(path, data)

    monkeypatch.setattr(recovery_mod, "_write_private_json", disk_full)

    async def reject(_visit_id, _doc):
        return "parts_out_of_range"

    reports = Reports()
    await _recover(tmp_path, upload_transcript=reject, submit_report=reports)
    # Cannot persist report markers: submitted payload still includes them so Servers knows requested transcripts are gone.
    (visit_id, doc), = reports.calls
    assert doc["transcript_unavailable"] == "parts_out_of_range" and doc["include_transcript"] is True


async def test_failed_upload_progress_rewrite_does_not_extend_the_retention(tmp_path):
    v = vid(108)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps(_sealed(v)), encoding="utf-8")
    old = time.time() - 6 * 86400
    os.utime(sealed, (old, old))

    async def multipart(_visit_id, doc):
        # Chunk uploads persist progress then return False, permitted by callback contracts.
        sealed.write_text(json.dumps({**doc, "parts_done": 2}), encoding="utf-8")
        return False

    await _recover(tmp_path, upload_transcript=multipart)
    # Retention uses mtime: retries cannot extend seven-day deadlines, or failed uploads never expire.
    assert abs(sealed.stat().st_mtime - old) < 2 and json.loads(sealed.read_text(encoding="utf-8"))["parts_done"] == 2


# ── Review: in-flight criteria / summary registration / upload envelopes / report quarantine ──


def _queue_report(tmp_path, visit_id, **fields):
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir(exist_ok=True)
    path = reports_dir / f"{visit_id}.json"
    path.write_text(json.dumps({"visit_id": visit_id, **fields}), encoding="utf-8")
    return path


async def test_spool_still_open_for_appends_counts_as_in_flight(tmp_path):
    from main_logic.visit import spool as spool_mod

    v = vid(110)
    stream = _write_stream(tmp_path, v, _stream_records(v))
    outbox = _spool_dir(tmp_path) / f"{v}.outbox.jsonl"
    outbox.write_text("x", encoding="utf-8")
    report_path = _queue_report(tmp_path, v, include_transcript=True)
    key = spool_mod._spool_key(_spool_dir(tmp_path) / f"{v}.jsonl")
    with spool_mod._OPEN_SPOOLS_LOCK:
        spool_mod._OPEN_SPOOLS.add(key)
    try:
        uploads, reports = Uploads(), Reports()
        # Runtime unregistered is_live, but memory-spool writer remains open and streams still append.
        await _recover(tmp_path, upload_transcript=uploads, submit_report=reports)
    finally:
        with spool_mod._OPEN_SPOOLS_LOCK:
            spool_mod._OPEN_SPOOLS.discard(key)
    # Match per-visit backfill: do not seal streams or delete outbox; queued reports wait too.
    assert uploads.calls == [] and reports.calls == []
    assert stream.exists() and outbox.exists() and report_path.exists()
    assert not (_spool_dir(tmp_path) / f"{v}.upload.json").exists()


async def test_recovery_summary_is_registered_for_the_opening_handoff(tmp_path):
    from main_logic.visit import memory_commit

    await seed_roster(tmp_path)
    v = vid(111)
    await make_visit(tmp_path, v, [ln(0, "你好"), ln(1, "嗨", "peer_cat")])
    seen = []

    async def llm(_prompt):
        # Same pair opens another visit during digest generation: handoff must find/wait for the registered task, not start another.
        seen.append(v in memory_commit._SUMMARY_TASKS)
        return "上次聊了天气。"

    async def spawn(_own_char_uid, factory):
        return await factory()

    report = await _recover(tmp_path, summary_llm=llm, spawn_background=spawn)
    assert report.summaries == {v: True} and seen == [True]
    assert v not in memory_commit._SUMMARY_TASKS


def test_overflowing_upload_duration_is_recorded_as_zero():
    from main_logic.visit.recovery import build_upload_doc

    v = vid(112)
    header = {**_header(v), "started_at": -1.7e308}
    records = [header, {"kind": "line", "lp": 0, "side": "host", "from": "own_cat", "ts": 1.7e308,
                        "text": "a", "truncated": False}]
    # Individually finite timestamps subtract to inf: record zero duration without OverflowError.
    doc = build_upload_doc(records, visit_id=v, finalized_reason=None)
    assert doc["request"]["usage"]["duration_s"] == 0


@pytest.mark.parametrize("error", [OverflowError, ValueError, TypeError])
async def test_stream_comparison_errors_only_defer_that_visit(tmp_path, monkeypatch, error):
    from main_logic.visit import recovery
    from main_logic.visit.recovery import build_upload_doc

    bad, good = vid(113), vid(114)
    for v in (bad, good):
        records = _stream_records(v)
        _write_stream(tmp_path, v, records)
        (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(
            json.dumps(build_upload_doc(records, visit_id=v, finalized_reason="wrap_up")), encoding="utf-8")
    real = recovery._stream_doc_sync

    def broken(spool_dir, visit_id, *rest):
        if visit_id == bad:
            raise error("cannot rebuild")
        return real(spool_dir, visit_id, *rest)

    monkeypatch.setattr(recovery, "_stream_doc_sync", broken)
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    # Comparison failures defer only this visit with both copies retained, never abort the whole pass or other uploads.
    assert [visit_id for visit_id, _ in uploads.calls] == [good]
    assert (_spool_dir(tmp_path) / f"{bad}.upload.jsonl").exists()
    assert (_spool_dir(tmp_path) / f"{bad}.upload.json").exists()


async def test_resealing_a_mismatched_normal_end_keeps_its_reason(tmp_path):
    from main_logic.visit.recovery import build_upload_doc

    await seed_roster(tmp_path)
    v = vid(115)
    await make_visit(tmp_path, v, [ln(0)], finalized=None)      # State not finalized; backfill marks crash.
    records = _stream_records(v)
    _write_stream(tmp_path, v, records)
    doc = build_upload_doc(records, visit_id=v, finalized_reason="wrap_up")
    doc["request"]["lines"] = doc["request"]["lines"][:1]         # Valid format but inconsistent with streams.
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(doc), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, uploaded), = uploads.calls
    # Reseal using streams for transcripts while retaining file-recorded normal ending reasons, never reverting to crash.
    assert [line["text"] for line in uploaded["request"]["lines"]] == ["t0", "t1"]
    assert uploaded["request"]["finalized_reason"] == "wrap_up"


@pytest.mark.parametrize("source", ["state", "spool_header"])
async def test_stream_header_without_char_uid_takes_the_visits_own(tmp_path, source):
    v = vid(116)
    await make_visit(tmp_path, v, [ln(0)], own_char_uid=CHAR_UID_B, last_summary_done=True,
                     write_jsonl=source == "spool_header")
    if source == "spool_header":
        (_spool_dir(tmp_path) / f"{v}.state.json").unlink()
    header = _header(v)
    header.pop("own_char_uid")           # Earlier upload-header layouts lacked this field.
    _write_stream(tmp_path, v, [header])
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, doc), = uploads.calls
    # Restore character IDs so freshly sealed uploads are not immediately classified corrupt and deleted.
    assert visit_id == v and doc["own_char_uid"] == CHAR_UID_B
    assert (_spool_dir(tmp_path) / f"{v}.upload.json").exists()


async def test_stream_header_without_char_uid_anywhere_keeps_the_stream(tmp_path, monkeypatch):
    from main_logic.visit import recovery

    v = vid(117)
    header = _header(v)
    header.pop("own_char_uid")
    stream = _write_stream(tmp_path, v, [header])
    written = []
    real_write = recovery._write_private_json

    def record(path, data):
        written.append(Path(path).name)
        return real_write(path, data)

    monkeypatch.setattr(recovery, "_write_private_json", record)
    uploads = Uploads()
    await _recover(tmp_path, upload_transcript=uploads)
    # Character ID unavailable everywhere: reject before writing, retaining streams for future resealing, never seal then delete.
    assert uploads.calls == [] and written == [] and stream.exists()
    assert not (_spool_dir(tmp_path) / f"{v}.upload.json").exists()


@pytest.mark.parametrize("transport", [None, 7, ""], ids=["null", "number", "empty"])
async def test_stream_header_with_a_broken_transport_is_corrupt_before_sealing(tmp_path, monkeypatch, transport):
    from main_logic.visit import recovery

    v = vid(118)
    stream = _write_stream(tmp_path, v, [{**_header(v), "transport": transport}])
    report_path = _queue_report(tmp_path, v, include_transcript=True)
    written = []
    real_write = recovery._write_private_json

    def record(path, data):
        written.append(Path(path).name)
        return real_write(path, data)

    monkeypatch.setattr(recovery, "_write_private_json", record)
    uploads, reports = Uploads(), Reports()
    await _recover(tmp_path, upload_transcript=uploads, submit_report=reports)
    # Transport unrecoverable elsewhere: classify stream corruption before writing uploads, never seal then delete.
    assert uploads.calls == [] and not stream.exists()
    assert f"{v}.upload.json" not in written
    (visit_id, doc), = reports.calls
    assert doc["transcript_unavailable"] == "corrupt" and not report_path.exists()


async def test_sealed_upload_of_another_character_is_not_uploaded(tmp_path):
    v = vid(119)
    await make_visit(tmp_path, v, [], memory_enabled=False, last_summary_done=True)   # State records CHAR_UID_A.
    d = _spool_dir(tmp_path)
    (d / f"{v}.upload.json").write_text(json.dumps({**_sealed(v), "own_char_uid": CHAR_UID_B}), encoding="utf-8")
    uploads = Uploads()
    await _recover(tmp_path, upload_transcript=uploads)
    # Upload file only: character ID mismatches this visit's state.json, so never upload as the wrong character.
    assert uploads.calls == [] and not (d / f"{v}.upload.json").exists()


@pytest.mark.parametrize("change", [{"v": 2}, {"transport": "webrtc"}], ids=["newer_version", "new_transport"])
async def test_sealed_upload_of_another_version_is_kept_not_deleted(tmp_path, change):
    v = vid(120)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps({**_sealed(v), **change}), encoding="utf-8")
    report_path = _queue_report(tmp_path, v, include_transcript=True)
    uploads, reports = Uploads(), Reports()
    await _recover(tmp_path, upload_transcript=uploads, submit_report=reports)
    # Intact newer-version files after downgrade are pending, not corrupt/deletable; attached-transcript reports keep waiting.
    assert uploads.calls == [] and sealed.exists()
    assert reports.calls == [] and "transcript_unavailable" not in json.loads(report_path.read_text(encoding="utf-8"))


async def test_report_marker_is_written_under_the_report_lock(tmp_path):
    import asyncio

    from main_logic.visit import recovery
    from main_logic.visit.subjects import path_lock

    v = vid(121)
    path = _queue_report(tmp_path, v, include_transcript=True)
    lock = path_lock(path)
    lock.acquire()
    try:
        task = asyncio.create_task(recovery._mark_report_transcript_unavailable(
            tmp_path, v, "expired", recovery.RecoveryReport()))
        await asyncio.sleep(0.3)
        # Runtime report processing holds the same lock; markers must wait.
        assert not task.done() and "transcript_unavailable" not in json.loads(path.read_text(encoding="utf-8"))
        path.unlink()                    # Under lock: realtime retry accepts/deletes or user abandons.
    finally:
        lock.release()
    await task
    # Accepted/abandoned reports must not be recreated by marker writes and resubmitted.
    assert not path.exists()


async def test_report_deleted_between_read_and_write_is_not_recreated(tmp_path, monkeypatch):
    from main_logic.visit import recovery

    v = vid(122)
    path = _queue_report(tmp_path, v, include_transcript=True)
    real_load = recovery._load_json

    def load_then_deleted(p):
        doc = real_load(p)
        if Path(p) == path:
            path.unlink()                # An unlocked remover deletes after reading.
        return doc

    monkeypatch.setattr(recovery, "_load_json", load_then_deleted)
    await recovery._mark_report_transcript_unavailable(tmp_path, v, "expired", recovery.RecoveryReport())
    assert not path.exists()


async def test_unavailable_marker_is_not_written_onto_another_visits_report(tmp_path):
    v, other = vid(123), vid(124)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    (d / f"{v}.upload.json").write_text("{torn", encoding="utf-8")       # This visit's transcript corrupt.
    path = _queue_report(tmp_path, v)
    body = json.dumps({"visit_id": other, "include_transcript": True})
    path.write_text(body, encoding="utf-8")                               # File contains another visit's report.
    await _recover(tmp_path, upload_transcript=Uploads(), submit_report=Reports())
    # Never attach this transcript's unavailability reason to another visit's report.
    quarantined = tmp_path / "visit_reports" / f"{v}.json.mismatch"
    assert quarantined.read_text(encoding="utf-8") == body


async def test_terminal_rejection_is_not_counted_as_uploaded(tmp_path):
    v = vid(125)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    (d / f"{v}.upload.json").write_text(json.dumps(_sealed(v)), encoding="utf-8")

    async def reject(_visit_id, _doc):
        return "parts_out_of_range"

    report = await _recover(tmp_path, upload_transcript=reject)
    # Distinguish terminal rejection from upload success: uploads=True means transcripts reached Servers only.
    assert v not in report.uploads and report.rejected == {v: "parts_out_of_range"}


async def test_rejected_upload_that_cannot_be_deleted_is_not_uploaded_again(tmp_path, monkeypatch):
    v = vid(126)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps(_sealed(v)), encoding="utf-8")
    old = time.time() - 3 * 86400
    os.utime(sealed, (old, old))
    calls = []

    async def reject(visit_id, _doc):
        calls.append(visit_id)
        return "parts_out_of_range"

    real_unlink = Path.unlink

    def stubborn(self, missing_ok=False):
        if self == sealed:
            raise PermissionError("locked by antivirus")
        return real_unlink(self, missing_ok=missing_ok)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", stubborn)
        await _recover(tmp_path, upload_transcript=reject)
    # Cannot delete: persist rejection without extending retention.
    assert json.loads(sealed.read_text(encoding="utf-8"))["rejected"] == "parts_out_of_range"
    assert abs(sealed.stat().st_mtime - old) < 2
    report_path = _queue_report(tmp_path, v, include_transcript=True)
    reports = Reports()
    report = await _recover(tmp_path, upload_transcript=reject, submit_report=reports)
    # Next startup never reuploads just to be rejected again; delete directly and submit reports with rejection reasons.
    assert calls == [v] and not sealed.exists() and report.rejected == {v: "parts_out_of_range"}
    (visit_id, doc), = reports.calls
    assert doc["transcript_unavailable"] == "parts_out_of_range" and not report_path.exists()


async def test_terminal_rejection_keeps_the_upload_until_the_report_records_it(tmp_path, monkeypatch):
    from main_logic.visit import recovery as recovery_mod

    v = vid(109)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps(_sealed(v)), encoding="utf-8")
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    report_path = reports_dir / f"{v}.json"
    report_path.write_text(json.dumps({"visit_id": v, "include_transcript": True}), encoding="utf-8")
    real_write = recovery_mod._write_private_json

    def report_disk_full(path, data):
        if Path(path).parent == reports_dir:
            raise OSError("no space left on device")
        return real_write(path, data)

    monkeypatch.setattr(recovery_mod, "_write_private_json", report_disk_full)
    uploads = []

    async def reject(visit_id, _doc):
        uploads.append(visit_id)
        return "parts_out_of_range"

    await _recover(tmp_path, upload_transcript=reject, submit_report=Reports(ok=False))
    # Reason absent from reports and report submission failed: upload file alone durably records terminal rejection; preserve it first.
    assert sealed.exists() and json.loads(sealed.read_text(encoding="utf-8"))["rejected"] == "parts_out_of_range"
    monkeypatch.setattr(recovery_mod, "_write_private_json", real_write)
    reports = Reports()
    await _recover(tmp_path, upload_transcript=reject, submit_report=reports)
    # Next startup: skip reupload, persist report reasons before deleting upload files, then submit reports carrying reasons.
    (visit_id, doc), = reports.calls
    assert uploads == [v] and not sealed.exists() and doc["transcript_unavailable"] == "parts_out_of_range"


async def test_expired_upload_gets_one_more_attempt_before_it_is_given_up(tmp_path):
    v = vid(110)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps(_sealed(v)), encoding="utf-8")
    old = time.time() - 8 * 86400
    os.utime(sealed, (old, old))                                 # App unopened for eight days; no prior upload attempts this startup.
    uploads = Uploads(ok=True)
    await _recover(tmp_path, upload_transcript=uploads)
    # Abandon only after failures for seven days since ending: retry once this startup first, retaining successful uploads.
    assert [visit_id for visit_id, _ in uploads.calls] == [v] and not sealed.exists()


async def test_expired_upload_that_still_fails_releases_its_report_in_the_same_pass(tmp_path):
    v = vid(111)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps(_sealed(v)), encoding="utf-8")
    old = time.time() - 8 * 86400
    os.utime(sealed, (old, old))
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(json.dumps({"visit_id": v, "include_transcript": True}), encoding="utf-8")
    uploads = Uploads(ok=False)
    reports = Reports()
    await _recover(tmp_path, upload_transcript=uploads, submit_report=reports)
    # Retry still fails: abandon transcript and submit queued reports with reasons this pass.
    (visit_id, doc), = reports.calls
    assert len(uploads.calls) == 1 and not sealed.exists() and doc["transcript_unavailable"] == "expired"


async def test_recovery_digest_protects_other_local_cat_names_in_peer_labels(tmp_path):
    await seed_roster(tmp_path, peer_display="B")                # Peer calls themselves another local cat's name.
    await make_visit(tmp_path, vid(112), [ln(i, f"line {i}", ("own_cat", "peer_cat", "peer_human", "own_human")[i % 4])
                                          for i in range(8)])
    server = FakeMemoryServer()
    await _recover(tmp_path, server)
    segments = server.calls("scoped_history")[1]["segments"]
    # Backfilled digests receive local-character rosters too, preventing peer impersonation of another local cat.
    assert "B" not in {seg["speaker_label"] for seg in segments}


async def test_expired_stream_keeps_its_age_and_finalized_reason_through_the_extra_attempt(tmp_path):
    v = vid(113)
    spool = await make_visit(tmp_path, v, [], memory_enabled=False, finalized="wrap_up", last_summary_done=True)
    stream = _write_stream(tmp_path, v, _stream_records(v))
    old = time.time() - 8 * 86400
    for path in (stream, spool.state_path):
        os.utime(path, (old, old))                              # App unopened eight days: stream/state expired.
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, doc), = uploads.calls
    # Do not age-delete state.json before retry; normally ended visits still seal as wrap_up, never crash.
    assert doc["request"]["finalized_reason"] == "wrap_up"
    # Sealed files inherit stream age: failed retry abandons this pass, not after another seven days.
    assert not (_spool_dir(tmp_path) / f"{v}.upload.json").exists()


async def test_expired_upload_is_kept_when_there_is_no_uploader(tmp_path):
    v = vid(114)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps(_sealed(v)), encoding="utf-8")
    old = time.time() - 8 * 86400
    os.utime(sealed, (old, old))
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(json.dumps({"visit_id": v, "include_transcript": True}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=None, submit_report=reports)
    # No upload callback means no attempts; retain transcripts and keep reports waiting.
    assert sealed.exists() and reports.calls == []
    older = time.time() - 15 * 86400
    os.utime(sealed, (older, older))
    await _recover(tmp_path, upload_transcript=None, submit_report=reports)
    # Retain at most one more period: after twice retention, abandon and submit attached-transcript reports with reasons.
    (visit_id, doc), = reports.calls
    assert not sealed.exists() and doc["transcript_unavailable"] == "expired"


async def test_sealed_upload_of_another_account_is_not_uploaded(tmp_path):
    v = vid(115)
    await make_visit(tmp_path, v, [], memory_enabled=False, finalized="wrap_up", last_summary_done=True)
    d = _spool_dir(tmp_path)
    (d / f"{v}.upload.json").write_text(json.dumps({**_sealed(v), "own_visit_uid": OWN_B}), encoding="utf-8")
    uploads = Uploads(ok=True)
    await _recover(tmp_path, upload_transcript=uploads)
    # Matching character IDs are insufficient: account mismatches upload under wrong accounts.
    assert uploads.calls == []


async def test_ownerless_sealed_upload_takes_the_visits_account(tmp_path):
    v = vid(116)
    await make_visit(tmp_path, v, [], memory_enabled=False, finalized="wrap_up", last_summary_done=True)
    d = _spool_dir(tmp_path)
    (d / f"{v}.upload.json").write_text(json.dumps({**_sealed(v), "own_visit_uid": None}), encoding="utf-8")
    uploads = Uploads(ok=True)
    await _recover(tmp_path, upload_transcript=uploads)
    # Ownerless legacy uploads inherit this visit's state.json account so callbacks can choose logged-in accounts.
    (visit_id, doc), = uploads.calls
    assert doc["own_visit_uid"] == OWN_A


async def test_expired_state_is_kept_for_checking_a_lone_ownerless_upload(tmp_path):
    v = vid(117)
    spool = await make_visit(tmp_path, v, [], memory_enabled=False, finalized="wrap_up", last_summary_done=True)
    sealed = _spool_dir(tmp_path) / f"{v}.upload.json"
    sealed.write_text(json.dumps({**_sealed(v), "own_visit_uid": None}), encoding="utf-8")
    old = time.time() - 8 * 86400
    for path in (sealed, spool.state_path):
        os.utime(path, (old, old))                              # Ownerless uploads and state.json both expired.
    uploads = Uploads(ok=True)
    await _recover(tmp_path, upload_transcript=uploads)
    # First cleanup must retain state.json for ownership verification and account backfill when only uploads remain.
    (visit_id, doc), = uploads.calls
    assert doc["own_visit_uid"] == OWN_A


async def test_terminal_rejection_leaves_another_accounts_report_unmarked(tmp_path):
    v = vid(104)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    sealed_doc = _sealed(v)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps(sealed_doc), encoding="utf-8")
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    other = "f" * 24
    assert sealed_doc.get("own_visit_uid") and sealed_doc["own_visit_uid"] != other
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": other}), encoding="utf-8")

    async def reject(_visit_id, _doc):
        return "parts_out_of_range"

    reports = Reports()
    await _recover(tmp_path, upload_transcript=reject, submit_report=reports)
    for _visit_id, doc in reports.calls:
        assert "transcript_unavailable" not in doc        # Another shared-computer account's report omits this transcript's reason.
    on_disk = json.loads((reports_dir / f"{v}.json").read_text(encoding="utf-8")) \
        if (reports_dir / f"{v}.json").exists() else {}
    assert "transcript_unavailable" not in on_disk



async def test_an_unverified_reason_is_not_attached_to_another_accounts_report(tmp_path, monkeypatch):
    from main_logic.visit import recovery

    v = vid(105)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    (d / f"{v}.upload.json").write_text(json.dumps(_sealed(v)), encoding="utf-8")
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    other = "f" * 24
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": other}), encoding="utf-8")
    def unreadable(*_a, **_k):
        raise OSError("in use")                              # Report unreadable during ownership verification.

    monkeypatch.setattr(recovery, "_mark_report_sync", unreadable)

    async def reject(_visit_id, _doc):
        return "parts_out_of_range"

    reports = Reports()
    await _recover(tmp_path, upload_transcript=reject, submit_report=reports)
    assert any(visit_id == v for visit_id, _doc in reports.calls), "目标举报必须提交"
    for _visit_id, doc in reports.calls:
        assert "transcript_unavailable" not in doc          # Presubmission recheck: another account's report carries no reason.



@pytest.mark.parametrize("damage", ["sealed_only", "stream_only"])
async def test_a_corrupt_upload_leaves_another_accounts_report_unmarked(tmp_path, damage):
    v = vid(106)
    await make_visit(tmp_path, v, [ln(0)], own_uid=OWN_A)
    d = _spool_dir(tmp_path)
    if damage == "sealed_only":
        (d / f"{v}.upload.json").write_text("{torn", encoding="utf-8")
    else:
        _write_stream(tmp_path, v, [{"kind": "line"}])                       # Stream lacks headers: corrupt.
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    other = "f" * 24
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": other}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(), submit_report=reports)
    # OWN_A-side transcript corrupt; another account's report omits its reason.
    for _visit_id, doc in reports.calls:
        assert "transcript_unavailable" not in doc
    if (reports_dir / f"{v}.json").exists():
        assert "transcript_unavailable" not in json.loads((reports_dir / f"{v}.json").read_text(encoding="utf-8"))



async def test_a_transiently_unreadable_upload_rearms_the_background_retry(tmp_path, monkeypatch):
    from main_logic.visit import recovery

    v = vid(107)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    (d / f"{v}.upload.json").write_text(json.dumps(_sealed(v)), encoding="utf-8")
    real_load = recovery._load_json

    def locked(path):
        if path.name == f"{v}.upload.json":
            raise PermissionError("in use")                   # Windows sharing conflict.
        return real_load(path)

    monkeypatch.setattr(recovery, "_load_json", locked)
    armed = []
    pending = await recovery._upload_pending(
        tmp_path, live=lambda _v: False, upload_transcript=Uploads(), submit_report=None,
        report=recovery.RecoveryReport(), retry_later=armed.append,
    )
    assert v in pending and armed == [v]


async def test_an_expired_upload_leaves_another_accounts_report_unmarked(tmp_path):
    v = vid(108)
    await make_visit(tmp_path, v, [ln(0)], own_uid=OWN_A)
    d = _spool_dir(tmp_path)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps(_sealed(v)), encoding="utf-8")
    old = time.time() - 8 * 86400
    os.utime(sealed, (old, old))
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": "f" * 24}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    # OWN_A-side transcript expires; another account's report omits its reason.
    assert not sealed.exists()
    assert any(visit_id == v for visit_id, _doc in reports.calls)
    for _visit_id, doc in reports.calls:
        assert "transcript_unavailable" not in doc


@pytest.mark.parametrize("beside", ["nothing", "corrupt_sealed"])
async def test_a_transient_seal_failure_rearms_the_retry_only_for_a_lone_stream(tmp_path, monkeypatch, beside):
    from main_logic.visit import recovery

    v = vid(109)
    _write_stream(tmp_path, v, _stream_records(v))
    if beside == "corrupt_sealed":
        (_spool_dir(tmp_path) / f"{v}.upload.json").write_text("{torn", encoding="utf-8")

    def locked(*_a, **_k):
        raise PermissionError("in use")

    monkeypatch.setattr(recovery, "_seal_stream_sync", locked)
    armed = []
    pending = await recovery._upload_pending(
        tmp_path, live=lambda _v: False, upload_transcript=Uploads(), submit_report=None,
        report=recovery.RecoveryReport(), retry_later=armed.append,
    )
    # Stream only: background reseals; corrupt neighboring sealed files prevent background resealing, so never queue doomed immediate-exit retries.
    assert v in pending and armed == ([v] if beside == "nothing" else [])


async def test_another_accounts_pending_upload_does_not_hold_back_a_report(tmp_path):
    v = vid(110)
    await make_visit(tmp_path, v, [ln(0)], own_uid=OWN_A)
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(_sealed(v)), encoding="utf-8")
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": "f" * 24}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    # OWN_A-side transcript pending: another account's attached-transcript report does not wait.
    assert [visit_id for visit_id, _doc in reports.calls] == [v]



async def test_the_report_gate_reads_the_owner_from_the_upload_when_state_is_gone(tmp_path):
    v = vid(111)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    newer = {**_sealed(v), "v": 99, "own_visit_uid": OWN_A}           # Written by newer versions; preserve unchanged here.
    (d / f"{v}.upload.json").write_text(json.dumps(newer), encoding="utf-8")
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": "f" * 24}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    # No state.json: derive ownership from sealed files; another account's attached-transcript report does not wait.
    assert [visit_id for visit_id, _doc in reports.calls] == [v]



async def test_the_report_gate_prefers_the_owner_named_by_the_upload_itself(tmp_path):
    v = vid(112)
    await make_visit(tmp_path, v, [ln(0)], own_uid=OWN_B)                   # state.json records B.
    newer = {**_sealed(v), "v": 99, "own_visit_uid": OWN_A}                 # Retained newer-version transcript belongs to A.
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(newer), encoding="utf-8")
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": OWN_A}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    # A-side transcript pending: A's attached-transcript reports still wait, never released based on state.json's B.
    assert reports.calls == []



async def test_the_report_gate_ignores_the_owner_of_a_file_from_another_visit(tmp_path, monkeypatch):
    from main_logic.visit import recovery

    v = vid(113)
    await make_visit(tmp_path, v, [ln(0)], own_uid=OWN_B)
    wrong = {**_sealed(vid(114)), "own_visit_uid": OWN_A}                    # Another visit's file names A.
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(wrong), encoding="utf-8")

    async def undeletable(_spool_dir, _visit_id):
        return False                                                    # Temporarily undeletable; retain pending.

    monkeypatch.setattr(recovery, "_drop_corrupt_sealed", undeletable)
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": OWN_B}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    # A in another visit's file does not count: use state.json's B, whose attached-transcript report still waits.
    assert reports.calls == []



async def test_an_expired_self_contained_upload_keeps_its_owner_for_the_report(tmp_path):
    v = vid(115)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps(_sealed(v)), encoding="utf-8")       # File owns A, without state.json.
    old = time.time() - 8 * 86400
    os.utime(sealed, (old, old))
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": "f" * 24}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    # A's transcript expires, ownership read before deletion: another account's reports omit reasons.
    assert not sealed.exists()
    assert [visit_id for visit_id, _doc in reports.calls] == [v]
    assert all("transcript_unavailable" not in doc for _visit_id, doc in reports.calls)



async def test_the_report_gate_uses_the_state_owner_for_a_rejected_current_file(tmp_path, monkeypatch):
    from main_logic.visit import recovery

    v = vid(116)
    await make_visit(tmp_path, v, [ln(0)], own_uid=OWN_B)                   # state.json records B.
    conflicting = {**_sealed(v), "own_visit_uid": OWN_A}                     # This visit's format but names A.
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(conflicting), encoding="utf-8")

    async def undeletable(_spool_dir, _visit_id):
        return False

    monkeypatch.setattr(recovery, "_drop_corrupt_sealed", undeletable)
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": OWN_B}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    # File mismatching state.json is rejected: use B's state ownership and keep B's reports waiting, never release as another person's transcript.
    assert reports.calls == []



async def test_a_transiently_unreadable_queued_report_rearms_the_retry(tmp_path, monkeypatch):
    from main_logic.visit import recovery

    v = vid(117)
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": False, "own_visit_uid": OWN_A}), encoding="utf-8")
    real_load = recovery._load_json

    def locked(path):
        if path.parent.name == "visit_reports":
            raise PermissionError("in use")
        return real_load(path)

    monkeypatch.setattr(recovery, "_load_json", locked)
    armed, reports = [], Reports()
    await recovery._submit_reports(tmp_path, skip=set(), submit_report=reports,
                                   report=recovery.RecoveryReport(), retry_later=armed.append)
    # Report-only visits without upload tasks: temporary unreadability triggers background handling, not next-startup waiting.
    assert reports.calls == [] and armed == [v]



async def test_an_upload_callback_failing_before_its_own_scheduling_rearms_the_retry(tmp_path):
    from main_logic.visit import recovery

    v = vid(118)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    (d / f"{v}.upload.json").write_text(json.dumps(_sealed(v)), encoding="utf-8")

    async def setup_failed(_visit_id, _doc):
        raise PermissionError("progress file in use")                   # Callback fails before scheduling its own retry.

    armed = []
    pending = await recovery._upload_pending(
        tmp_path, live=lambda _v: False, upload_transcript=setup_failed, submit_report=None,
        report=recovery.RecoveryReport(), retry_later=armed.append,
    )
    assert v in pending and armed == [v]



async def test_a_lone_streams_header_names_the_pending_owner(tmp_path):
    from main_logic.visit import recovery

    v = vid(119)
    _write_stream(tmp_path, v, _stream_records(v))                       # No state.json or sealed files.
    assert await recovery._pending_upload_owner(tmp_path, v) == OWN_A
    other = vid(120)
    _write_stream(tmp_path, other, [_header(v)])                         # Header identifies another visit.
    assert await recovery._pending_upload_owner(tmp_path, other) is None



async def test_recovery_does_not_mark_a_transcript_free_report(tmp_path):
    from main_logic.visit import recovery

    v = vid(121)
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    path = reports_dir / f"{v}.json"
    path.write_text(json.dumps({"visit_id": v, "include_transcript": False, "own_visit_uid": OWN_A}),
                    encoding="utf-8")
    report = recovery.RecoveryReport()
    await recovery._mark_report_transcript_unavailable(tmp_path, v, "expired", report)
    assert "transcript_unavailable" not in json.loads(path.read_text(encoding="utf-8"))
    assert v not in report.transcript_unavailable



async def test_a_corrupt_streams_header_owner_guards_another_accounts_report(tmp_path, monkeypatch):
    from main_logic.visit import recovery

    v = vid(122)
    _write_stream(tmp_path, v, [{**_header(v), "transport": 42}, {"kind": "line"}])   # Valid header ownership, corrupt remainder.
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": "f" * 24}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(), submit_report=reports)
    # Without state.json, use stream-header OWN_A ownership; another account's reports omit corrupt reasons.
    for _visit_id, doc in reports.calls:
        assert "transcript_unavailable" not in doc


def test_a_transcript_line_that_cannot_be_written_is_corrupt():
    from main_logic.visit import recovery

    line = {"lp": 1, "side": "host", "from": "own_cat", "ts": 1.0, "text": "ok", "truncated": False}
    assert recovery._valid_line(line) is True
    assert recovery._valid_line({**line, "text": "x" + chr(0xD800)}) is False


async def test_crashed_memory_on_visit_without_lines_is_abandoned_not_retried(tmp_path):
    from main_logic.visit.spool import debrief_final, region_settled

    await seed_roster(tmp_path)
    spool = await make_visit(tmp_path, vid(41), [], write_jsonl=False, finalized=None)
    chips = Chips()
    llm = LLM()
    report = await _recover(tmp_path, render_chips=chips, summary_llm=llm)
    state = await spool.read_state()
    assert report.crashed == [vid(41)] and chips.calls == [] and llm.calls == 0
    assert state["debrief_choice"] == "abandoned"
    assert state["digest_writes"]["0"]["abandoned"] == "no_transcript"
    assert region_settled(state) and debrief_final(state)
    # Next startup sees terminal state, never redisplaying chips or rewriting state.json.
    before = spool.state_path.stat().st_mtime_ns
    await _recover(tmp_path, render_chips=chips, summary_llm=llm)
    assert chips.calls == [] and spool.state_path.stat().st_mtime_ns == before
