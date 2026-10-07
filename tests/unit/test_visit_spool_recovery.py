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
    # 补录总会先严格检查角色配置；测试里不碰真实运行时根目录的 characters.json
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


# ── 启动清理与上传 ────────────────────────────────────────────────────


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
    assert [v for v, _ in reports.calls] == [uploaded]       # 转录还没传上去的那场先不提交举报
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


# ── 崩溃、关机兜底与芯片 ──────────────────────────────────────────────


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
    assert {name for name, _ in server.requests} == {"scoped_history"}   # 只有串门区 digest
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
    # 两步写入可能还在退避等待：照常经 bind 重放预览块（显示「写入中」），新连接看得到进度
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


# ── 撤销日志重放与改名对账 ────────────────────────────────────────────


async def test_revocation_log_merges_new_subjects_and_replays_after_outage(tmp_path):
    await seed_roster(tmp_path)
    server = FakeMemoryServer()
    server.fail_always.add("scoped_forget")
    first = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                peer_uid=PEER_X, client=server.client())
    assert not first.done
    (log,) = await RevocationLog.list_all_open(tmp_path)
    assert not any(step.startswith("forget:") for step in log["done_steps"])
    # 期间同一个人又带另一只猫来串门：名册多了一只对方猫娘
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


# ── 不在启动链路上 ────────────────────────────────────────────────────


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


# ── 评审第一轮 ────────────────────────────────────────────────────────


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
    # 清除日志卡住期间角色 A 改名为 C，改名迁移还没做完就崩溃
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


# ── 评审第三轮 ────────────────────────────────────────────────────────


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
    # 转录已被受理：本地删不掉上传文件也不挡它排队的举报
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
        started["A"] = True              # 锁外检查之后、拿到锁之前开场的一场
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
        # 普通类而非生成器：只有显式 __aexit__ 才算放锁，垃圾回收不会替我们放
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


# ── 评审第九轮 ────────────────────────────────────────────────────────


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
    # Servers 按 visit_id + role 幂等：照常上传，举报也照常提交
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
    assert [visit_id for visit_id, _ in failing.calls] == [v]          # 第一轮举报提交失败
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(), submit_report=reports)
    assert [visit_id for visit_id, _ in reports.calls] == [v]           # 下一轮照样能重试
    assert not (reports_dir / f"{v}.json").exists()


@pytest.mark.parametrize("source", ["state", "spool_header"])
async def test_stream_header_without_owner_takes_the_visits_own_account(tmp_path, source):
    v = vid(52)
    other = "c" * 24
    # 两种来源各自单独出现：state 那组不写转录，转录头行那组删掉 state.json
    await make_visit(tmp_path, v, [ln(0)], own_uid=other, last_summary_done=True,
                     write_jsonl=source == "spool_header")
    if source == "spool_header":
        (_spool_dir(tmp_path) / f"{v}.state.json").unlink()
    header = _header(v)
    header.pop("own_visit_uid")          # 设计稿较早的上传头定义没有这个字段
    _write_stream(tmp_path, v, [header])
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, doc), = uploads.calls
    # 补回占房账号，上传回调才能在该账号登录时传上去
    assert visit_id == v and doc["own_visit_uid"] == other


async def test_spool_header_of_another_visit_is_not_used_as_owner(tmp_path):
    v = vid(53)
    await make_visit(tmp_path, v, [ln(0)], own_uid="c" * 24, last_summary_done=True)
    spool_dir = _spool_dir(tmp_path)
    (spool_dir / f"{v}.state.json").unlink()
    jsonl = spool_dir / f"{v}.jsonl"
    lines = jsonl.read_text(encoding="utf-8").splitlines(keepends=True)
    misplaced = json.loads(lines[0])
    misplaced["visit_id"] = vid(54)                  # 错放 / 复制来的别的场次的转录
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
    header.pop("own_visit_uid")          # 设计稿较早的上传头定义没有这个字段
    _write_stream(tmp_path, v, [header, {"kind": "line", "lp": 0, "side": "host", "from": "own_cat",
                                         "ts": 1001.0, "text": "a", "truncated": False}])
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, doc), = uploads.calls
    assert visit_id == v and doc["own_visit_uid"] is None
    assert [line["text"] for line in doc["request"]["lines"]] == ["a"]
    assert (_spool_dir(tmp_path) / f"{v}.upload.json").exists()     # 唯一副本保留着


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
    # 新旧名字都在配置里：改名无法判定，清除与逐场补录都要等
    report = await _recover(tmp_path, server, list_char_names=_names("A", "C"))
    assert not report.forgets_clean and server.calls("scoped_forget") == []
    assert len(await RevocationLog.list_all_open(tmp_path)) == 1
    assert (await spool.read_state())["finalized"] is None


# ── 评审第十一轮 ──────────────────────────────────────────────────────


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
    # 哨兵展开与日志重放：每次按 uid 解析名字、每次清除请求都在守卫里（改名迁移插不进来）
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
    # 别场的上传文件不算数：从流水重新封存，流水不会被当成「已封存的残留」删掉
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
    # 转录行坏了的上传文件不算数：从完整的流水重新封存
    assert visit_id == v and [line["text"] for line in doc["request"]["lines"]] == ["a"]


# ── 评审第十二轮 ──────────────────────────────────────────────────────


async def test_sentinel_with_unresolvable_character_is_kept(tmp_path):
    from main_logic.visit.forget import ClearingSentinels
    from main_logic.visit.forget_runner import replay_forgets

    await seed_roster(tmp_path)
    sentinel = await ClearingSentinels(tmp_path).create(own_uid=OWN_A, scope="chars",
                                                       own_char_uids=[CHAR_UID_A])

    async def unresolved(_uid):
        return None        # 角色配置一时读不出：被替换成默认值，uid 解析不出名字

    clean = await replay_forgets(tmp_path, resolve_char_name=unresolved,
                                 client=FakeMemoryServer().client())
    assert clean is False
    # 范围没展开就不能当作「没人要清」删掉哨兵
    assert [d["op_id"] for d in await ClearingSentinels(tmp_path).list_open()] == [sentinel["op_id"]]


async def test_invalid_sealed_upload_is_not_uploaded_when_resealing_fails(tmp_path):
    v = vid(58)
    # 流水没有头行：重封得到 None（按损坏处理）
    _write_stream(tmp_path, v, [{"kind": "line", "lp": 0, "side": "host", "from": "own_cat",
                                 "ts": 1001.0, "text": "a", "truncated": False}])
    foreign = {"v": 1, "own_visit_uid": OWN_A, "request": {"visit_id": vid(59), "role": "host",
                                                           "started_at": 1.0, "ended_at": 2.0,
                                                           "usage": {}, "lines": []}}
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(foreign), encoding="utf-8")
    uploads = Uploads(ok=True)
    await _recover(tmp_path, upload_transcript=uploads)
    # 已知是别场的文件不能交给上传回调；流水也坏了（转录无法恢复）就同损坏流水一样删掉它，
    # 不留到下一轮再交上去
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
        # 没有流水可重封：转录已无法恢复，删掉坏文件，举报照常提交
        assert not (d / f"{v}.upload.json").exists()
        assert [visit_id for visit_id, _ in reports.calls] == [v]
    else:
        # 流水还在、只是这轮重封失败：坏文件留着等下次重封，举报不能先交
        assert reports.calls == []


async def test_forget_all_retry_resumes_logs_of_people_already_removed_from_the_roster(tmp_path):
    from main_logic.visit.forget_runner import forget_all

    await seed_roster(tmp_path)
    server = FakeMemoryServer()
    calls = {"n": 0}

    async def flaky_void(_record):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("spool busy")     # remove_char 已做完，之后的 void_pending 失败

    first = await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A},
                             client=server.client(), void_pending=flaky_void)
    assert first.done is False
    assert await PeerRoster(tmp_path, own_uid=OWN_A).peers_of_char("A") == []
    retry = await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A},
                             client=server.client(), void_pending=flaky_void)
    # 名册里已经没有这个人：重试照样续跑他那份开着的日志，跑完才算完成
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
    # 名册与记忆按账号分区：A 账号的清除不挡 B 账号同一角色下的同一对端
    assert not await memory_bridge.forget_in_progress(tmp_path, CHAR_UID_A, PEER_X, own_uid=OWN_B)


async def test_forget_all_with_an_unresolvable_character_is_not_reported_done(tmp_path):
    from main_logic.visit.forget import ClearingSentinels
    from main_logic.visit.forget_runner import CharacterUnresolved, forget_all

    await seed_roster(tmp_path)

    async def unreadable_config(_uid):
        return None          # 角色配置一时读不出：uid 解析不出名字

    with pytest.raises(CharacterUnresolved):
        await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A},
                         client=FakeMemoryServer().client(), resolve_char_name=unreadable_config)
    # 什么都没写，也没有报「清除成功」：调用方回可重试的错误
    assert await ClearingSentinels(tmp_path).list_open() == []
    assert await PeerRoster(tmp_path, own_uid=OWN_A).peers_of_char("A") == [PEER_X]


@pytest.mark.parametrize("scope", ["person", "all"])
async def test_forget_uses_the_name_resolved_under_the_lifecycle_guard(tmp_path, scope):
    import contextlib

    from main_logic.visit.forget_runner import forget_all

    await seed_roster(tmp_path, own_char="B")          # 拿到守卫之前角色已从 A 改名为 B
    held = {"in": False}

    @contextlib.asynccontextmanager
    async def guard(_uids):
        held["in"] = True
        yield
        held["in"] = False

    async def current_name(uid):
        assert held["in"]                              # 在守卫里重新解析
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
    # 清的是改名后 B 名下的条目，而不是旧名 A 下的空条目
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
    # 结构不完整的上传文件不能顶替完整的流水：从流水重封
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
    # 复查剩余日志与删除哨兵都在该哨兵的守卫里：端点复用哨兵插不进这段
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
    # 信封与流水对不上：从流水重封，信封以流水为准
    assert visit_id == v and uploaded["own_visit_uid"] == OWN_A
    assert [line["text"] for line in uploaded["request"]["lines"]] == ["a"]


async def test_sealed_upload_with_a_malformed_owner_and_no_stream_is_not_uploaded(tmp_path):
    v = vid(64)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{v}.upload.json").write_text(json.dumps({**_sealed(v), "own_visit_uid": 7}), encoding="utf-8")
    uploads = Uploads(ok=True)
    await _recover(tmp_path, upload_transcript=uploads)
    # 占房账号坏了：任何账号都传不出去，没有流水可重封就按损坏处理
    assert uploads.calls == [] and not (d / f"{v}.upload.json").exists()


async def test_rename_is_not_reconciled_from_a_partially_readable_character_config(tmp_path, monkeypatch):
    from main_logic.visit import local_chars

    await seed_roster(tmp_path)
    peers_path = tmp_path / "visit_peers.json"
    data = json.loads(peers_path.read_text(encoding="utf-8"))
    data["pending_rename"] = {"old": "A", "new": "C"}
    peers_path.write_text(json.dumps(data), encoding="utf-8")

    async def partial():
        return {"A": CHAR_UID_A}            # 新名字 C 的条目坏了，被常规加载静默滤掉

    async def damaged():
        raise local_chars.CharactersUnreadable("character entry 'C' cannot be enumerated")

    monkeypatch.setattr(local_chars, "load_local_characters", partial)
    monkeypatch.setattr(local_chars, "ensure_characters_readable", damaged)
    report = await visit_spool_recovery(
        Chips(), None, config_dir=tmp_path, resolve_char_name=resolver(),
        client=FakeMemoryServer().client(), is_live=lambda _visit_id: False,
    )
    # 配置有坏条目就不对账：不把数据迁回旧名、标记留着等下次
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
    # 两次尝试之间新建了角色 B：哨兵的角色集合变了，没能复用上一次的那个
    retry = await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A, "B": CHAR_UID_B},
                             client=server.client())
    assert retry.done is True
    # 上一次留下的、范围被这次完全覆盖的旧哨兵一并删掉，不再挡准入
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
    data["pending_rename"] = {"old": "A", "new": "C"}     # 补录对账之后又有改名崩在半路
    peers_path.write_text(json.dumps(data), encoding="utf-8")
    clean = await replay_forgets(tmp_path, resolve_char_name=resolver({CHAR_UID_A: "C"}),
                                 client=server.client())
    # 守卫内看到未对账的改名：不按新名重放、不关日志，留到下次
    assert clean is False and server.calls("scoped_forget") == []
    assert len(await RevocationLog.list_all_open(tmp_path)) == 1


async def test_person_forget_keeps_an_unexpanded_chars_sentinel_of_the_same_character(tmp_path):
    from main_logic.visit.forget import ClearingSentinels

    await seed_roster(tmp_path)
    # 同角色的「清除全部」崩在展开之前：还没有任何日志，它的范围比单人清除大
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
    # 那个人已不在名册里：单人清除还没展开，或已做完却没来得及删哨兵，两种情况分不清
    await ClearingSentinels(tmp_path).create(own_uid=OWN_A, scope="person",
                                             own_char_uids=[CHAR_UID_A], peer_uid=PEER_Y)
    server = FakeMemoryServer()
    outcome = await forget_all(tmp_path, own_uid=OWN_A, chars={"A": CHAR_UID_A}, client=server.client())
    assert outcome.done
    # 「清除全部」替它开日志一并执行（没清过的清掉、清过的幂等再清），之后哨兵随之删除，
    # 不再挡着这个角色的新串门
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
    # 这个人在名册里、这次已一并清掉：他的旧单人哨兵随之删除
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
            # 第 1 次是复用哨兵时的列举、第 2 次是接手范围内单人哨兵时的列举；这之后才出现的
            # 单人清除（并发发起）这次「清除全部」没执行它
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



# ── 用户评审（10-04）──────────────────────────────────────────────────


async def test_rename_marker_of_a_deleted_character_is_dropped(tmp_path):
    await seed_roster(tmp_path)
    peers_path = tmp_path / "visit_peers.json"
    data = json.loads(peers_path.read_text(encoding="utf-8"))
    data["pending_rename"] = {"old": "Q0", "new": "Q1"}       # 两个名字都不在：角色已被删除
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
    await seed_roster(tmp_path)                                # 名册条目还在旧名 A 下
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
    # 改名生效后又新建了一个叫 A 的角色：名册条目只按名字存，迁移会把两个角色的记录混到一起
    report = await _recover_with_chars(tmp_path, monkeypatch, {"A": CHAR_UID_B, "C": CHAR_UID_A},
                                       {CHAR_UID_A: "C", CHAR_UID_B: "A"})
    after = json.loads(peers_path.read_text(encoding="utf-8"))
    assert report.renamed is False and after["pending_rename"]["old"] == "A"
    assert "A" in after["accounts"][OWN_A]["peers"][PEER_X]["by_char"]   # 不迁、不混
    # 只挡这两个名字：别的角色（这里的场次属于新名 A，被跳过）之外的照常补录
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
    # Q0 → Q1 对不上账（旧名被占用），但与角色 B 无关：B 的崩溃场次照常补录
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
    # 之后角色 B 被删除（配置读得出、B 已不在）：重放不再让同一哨兵里的 A 永远「清除中」
    await _recover(tmp_path, server, resolve_char_name=resolver({CHAR_UID_A: "A"}),
                   list_char_names=_names("A"))
    assert await ClearingSentinels(tmp_path).list_open() == []
    # A 的日志跑完关掉；已删角色 B 的日志原样留给退役对账（不丢清除意图），也不再挡哨兵
    remaining = await RevocationLog.list_all_open(tmp_path)
    assert [log["own_char_uid"] for log in remaining] == [CHAR_UID_B]
    from main_logic.visit import memory_bridge
    assert not await memory_bridge.forget_in_progress(tmp_path, CHAR_UID_A, PEER_X, own_uid=OWN_A)


async def test_unrelated_corrupt_state_does_not_block_a_forget(tmp_path):
    await seed_roster(tmp_path)
    other = await make_visit(tmp_path, vid(70), [ln(0)], own_char="B", own_char_uid=CHAR_UID_B)
    other.state_path.write_text("{torn", encoding="utf-8")   # 别的角色一场的 state 坏了，转录头行还在
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    assert outcome.done


async def test_forget_does_not_void_wiped_visits_of_another_account(tmp_path):
    await seed_roster(tmp_path)
    other = await make_visit(tmp_path, vid(71), [ln(0)], own_uid=OWN_B, finalized="wrap_up",
                             debrief_choice="ask_later")
    await other.delete_peer_fields()                           # 别的账号下一场身份已抹的场次
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
    # 第二次（及以后）启动仍弹，且带「意外中断」
    assert chips.calls == [(v, "A", "interrupted")]


async def test_any_finalized_visit_without_a_choice_gets_its_chip(tmp_path):
    v = vid(73)
    spool = await make_visit(tmp_path, v, [ln(0)], finalized="peer_left", last_summary_done=True)
    chips = Chips(delivered=False)
    await _recover(tmp_path, render_chips=chips)
    # 正常收口后、还没来得及记芯片就被杀：补记并弹出
    assert chips.calls == [(v, "A", None)]
    assert (await spool.read_state())["debrief_chip_pending"] is True



async def test_state_only_corrupt_visit_is_dropped_by_the_wipe_not_blocking(tmp_path):
    await seed_roster(tmp_path)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    corrupt = d / f"{vid(74)}.state.json"
    corrupt.write_text("{torn", encoding="utf-8")              # 只剩 state、且坏了：认不出是谁的
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # 不再挡住清除；它若正属于被清的人，删掉本就是清除要做的事
    assert outcome.done is True and not corrupt.exists()


async def test_one_lock_held_state_does_not_hide_the_previous_visit_from_the_handoff(tmp_path, monkeypatch):
    from main_logic.visit import spool as spool_module
    from main_logic.visit.memory_commit import last_summary_handoff

    await seed_roster(tmp_path)
    previous = await make_visit(tmp_path, vid(81), [ln(0)], finalized="wrap_up")
    # 只剩 state.json 的一场（认不出是谁的），一时被占用读不出
    other = await make_visit(tmp_path, vid(82), [], own_char="B", own_char_uid=CHAR_UID_B,
                             memory_enabled=False)
    real_read = spool_module._read_state_file

    def flaky(path):
        if path == other.state_path:
            raise PermissionError("locked")                    # 一场无关的 state 一时读不出
        return real_read(path)

    monkeypatch.setattr(spool_module, "_read_state_file", flaky)
    started = []

    async def start(spool):
        started.append(spool.visit_id)
        return True

    await last_summary_handoff(tmp_path, own_uid=OWN_A, own_char_uid=CHAR_UID_A, peer_uid=PEER_X,
                               start_summary=start, is_live=lambda _v: False)
    # 交接是宽松查找：读不出的跳过，这一对的上一场照常等
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
    # 名单由调用方注入也照样先做严格检查：否则下面会把「解析不出名字」当成「角色已删」
    report = await _recover(tmp_path, server, resolve_char_name=resolver({}), list_char_names=_names())
    assert report.renamed is False and server.calls("scoped_forget") == []
    assert len(await RevocationLog.list_all_open(tmp_path)) == 1


async def test_is_live_must_be_given(tmp_path):
    # 补录在后台跑，漏接 is_live 会把在飞场次的流水提前封存、outbox 删掉：必须显式传入
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
    # 乱序的封存文件不能替掉流水：从流水重封，按 (lp, side) 排好
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
        assert held["depth"] == 1                              # 迁移发生在守卫里
        return await real_rename(self, old, new)

    monkeypatch.setattr(PeerRoster, "rename_char", rename)
    report = await _recover_with_chars(tmp_path, monkeypatch, {"C": CHAR_UID_A}, {CHAR_UID_A: "C"},
                                       lifecycle_guard=guard)
    assert report.renamed is True and held["uids"] == [CHAR_UID_A]


async def test_corrupt_state_of_a_wiped_visit_of_this_character_is_dropped(tmp_path):
    await seed_roster(tmp_path)
    wiped = await make_visit(tmp_path, vid(78), [ln(0)], finalized="wrap_up")
    await wiped.delete_peer_fields()                           # 已抹身份的一场（同一角色）
    wiped.state_path.write_text("{torn", encoding="utf-8")     # 之后 state.json 又坏了
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # 谁都用不了的坏 state 不再让这个角色的清除永远卡住，并由抹身份步骤删掉（不留残余身份）
    assert outcome.done and not wiped.state_path.exists()


async def test_lookup_alone_never_deletes_a_corrupt_state(tmp_path):
    from main_logic.visit.spool import VisitSpool
    from main_logic.visit.subjects import derive_pair_id as _pair

    await seed_roster(tmp_path)
    wiped = await make_visit(tmp_path, vid(79), [ln(0)], finalized="wrap_up")
    await wiped.delete_peer_fields()
    wiped.state_path.write_text("{torn", encoding="utf-8")
    found = await VisitSpool.find_visits_for_pairs(tmp_path, CHAR_UID_A, [_pair(OWN_A, PEER_Y)])
    # 查找（开场交接也调用）只跳过、不删
    assert found == [] and wiped.state_path.exists()


async def test_malformed_rename_marker_is_dropped(tmp_path):
    await seed_roster(tmp_path)
    peers_path = _set_rename_marker(tmp_path, "garbled")
    report = await _recover(tmp_path)
    # 没有可对账的信息：记诊断后清掉，不再永久挡住补录与清除
    assert report.renamed is True
    assert "pending_rename" not in json.loads(peers_path.read_text(encoding="utf-8"))


async def test_corrupt_wiped_state_of_another_account_is_left_alone(tmp_path):
    await seed_roster(tmp_path)
    other = await make_visit(tmp_path, vid(80), [ln(0)], own_uid=OWN_B, finalized="wrap_up")
    await other.delete_peer_fields()
    other.state_path.write_text("{torn", encoding="utf-8")
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # 别的账号下的坏 state 不在这次清除范围内：不挡、也不删
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
        table.clear()                                          # 等守卫期间角色被删除
        yield

    monkeypatch.setattr(local_chars, "load_local_characters", load)
    report = await visit_spool_recovery(
        Chips(), None, config_dir=tmp_path, resolve_char_name=resolver({}),
        client=FakeMemoryServer().client(), is_live=lambda _v: False, lifecycle_guard=guard,
    )
    # 守卫内重读到「角色已删」：按删除处理（丢标记、不迁名册），而不是按旧快照正向迁移
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
            # 等守卫期间，标记被另一个角色（B → D）的改名换掉
            data = json.loads(peers_path.read_text(encoding="utf-8"))
            data["pending_rename"] = {"old": "B", "new": "D", "uid": CHAR_UID_B}
            peers_path.write_text(json.dumps(data), encoding="utf-8")
        yield

    report = await _recover_with_chars(tmp_path, monkeypatch, {"C": CHAR_UID_A, "D": CHAR_UID_B},
                                       {CHAR_UID_A: "C", CHAR_UID_B: "D"}, lifecycle_guard=guard)
    # 按新标记重新拿了 B 的守卫再对账（不是拿着 A 的守卫去迁 B）
    assert taken == [[CHAR_UID_A], [CHAR_UID_B]] and report.renamed is True
    by_char = json.loads(peers_path.read_text(encoding="utf-8"))["accounts"][OWN_A]["peers"][PEER_X]["by_char"]
    assert "D" in by_char and "B" not in by_char


def _schema_invalid_state(spool):
    data = json.loads(spool.state_path.read_text(encoding="utf-8"))
    data["field_from_a_newer_version"] = 1                      # 能解析、只是不合当前 schema
    spool.state_path.write_text(json.dumps(data), encoding="utf-8")


async def test_schema_invalid_state_of_another_character_is_skipped_not_deleted(tmp_path):
    await seed_roster(tmp_path)
    other = await make_visit(tmp_path, vid(83), [], own_char="B", own_char_uid=CHAR_UID_B,
                             memory_enabled=False)               # 只剩 state.json
    _schema_invalid_state(other)
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # 原始字段明确属于别的角色：不挡这次清除，也绝不删（别的版本还读得了它）
    assert outcome.done and other.state_path.exists()


async def test_schema_invalid_state_that_may_be_ours_blocks_and_is_kept(tmp_path):
    await seed_roster(tmp_path)
    mine = await make_visit(tmp_path, vid(84), [], memory_enabled=False)   # 角色 A、这一对，只剩 state
    _schema_invalid_state(mine)
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # 可能正是这个人的场次：身份按原始对象抹掉（新版本字段原样保留、不删文件）；debrief 还可作废、
    # 这个版本又作废不了它，清除不记完成，留着等下次
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
        if loads["n"] == 2:
            # 守卫内重读名单期间，标记被另一个角色（B → D）的改名换掉
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
    # 对账前按拿守卫时的标记再核一次：换了就按新标记重新拿 B 的守卫
    assert taken[:2] == [[CHAR_UID_A], [CHAR_UID_B]] and report.renamed is True


async def test_schema_invalid_state_does_not_hide_a_header_that_names_this_pair(tmp_path):
    await seed_roster(tmp_path)
    mine = await make_visit(tmp_path, vid(85), [ln(0)], finalized="wrap_up")   # 头行指认这一对
    data = json.loads(mine.state_path.read_text(encoding="utf-8"))
    data["pair_id"] = derive_pair_id(OWN_A, PEER_Y)            # 不合 schema 的 state 指向别的一对
    data["field_from_a_newer_version"] = 1
    mine.state_path.write_text(json.dumps(data), encoding="utf-8")
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # 头行仍指认这一对：不能只凭 state 的原始字段跳过——身份照样按原始对象抹掉（新版本字段保留），
    # 但这个版本作废不了它的 debrief，清除留着等下次
    raw = json.loads(mine.state_path.read_text(encoding="utf-8"))
    assert outcome.done is False and raw["pair_id"] is None and raw["field_from_a_newer_version"] == 1


async def test_schema_invalid_wiped_state_of_another_account_does_not_block(tmp_path):
    await seed_roster(tmp_path)
    other = await make_visit(tmp_path, vid(86), [], own_uid=OWN_B, memory_enabled=False)
    await other.delete_peer_fields()                            # 身份已抹（pair_id 为空）
    data = json.loads(other.state_path.read_text(encoding="utf-8"))
    data["field_from_a_newer_version"] = 1
    other.state_path.write_text(json.dumps(data), encoding="utf-8")
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # 原始字段里的账号是别的账号：不挡这次清除，也不删
    assert outcome.done and other.state_path.exists()


async def test_schema_invalid_state_naming_this_pair_is_not_excluded_by_another_header(tmp_path):
    await seed_roster(tmp_path)
    mine = await make_visit(tmp_path, vid(87), [ln(0)], finalized="wrap_up")
    lines = mine.jsonl_path.read_bytes().splitlines(keepends=True)
    header = json.loads(lines[0])
    # 头行被换成指向别的一对（自洽的合法头行）
    header.update(peer_uid=PEER_Y, pair_id=derive_pair_id(OWN_A, PEER_Y),
                  peer_char_id=derive_peer_char_id(PEER_Y, header["peer_char_tag"]))
    lines[0] = json.dumps(header, ensure_ascii=False).encode() + b"\n"
    mine.jsonl_path.write_bytes(b"".join(lines))
    data = json.loads(mine.state_path.read_text(encoding="utf-8"))
    assert data["pair_id"] == derive_pair_id(OWN_A, PEER_X)    # state 原始字段仍指认这一对
    data["field_from_a_newer_version"] = 1
    mine.state_path.write_text(json.dumps(data), encoding="utf-8")
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # 对端字段还在 state 里：不能凭头行把它排除掉记完成
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
    doc["request"]["lines"] = doc["request"]["lines"][:1]       # 信封一致、结构合法，但少了一行
    doc["request"]["usage"]["llm_input_tokens"] = 0
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(doc), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, uploaded), = uploads.calls
    # 与流水重封出来的不一致：以完整的流水为准重封，不删流水换上缺行的文件
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
    # 与流水一致的封存文件就是正本：删流水、原样上传；state 不在时不把结束原因改成 crash
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
    # 转录上传失败：明确不附转录的举报照常提交，附转录（或没写明）的等转录
    assert [visit_id for visit_id, _ in reports.calls] == ([v] if include_transcript is False else [])


async def test_sealed_normal_end_is_not_resealed_as_crash(tmp_path):
    from main_logic.visit.recovery import build_upload_doc

    await seed_roster(tmp_path)
    v = vid(91)
    await make_visit(tmp_path, v, [ln(0)], finalized=None)      # state 还没写 finalized
    records = _stream_records(v)
    stream = _write_stream(tmp_path, v, records)
    # 正常收口已写出上传文件，删流水、写 state.finalized 之前崩溃
    doc = build_upload_doc(records, visit_id=v, finalized_reason="wrap_up")
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(doc), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, uploaded), = uploads.calls
    # 补录把 state 标成 crash，但上传文件里正常结束的原因原样上传，流水按残留删掉
    assert uploaded == doc and uploaded["request"]["finalized_reason"] == "wrap_up"
    assert not stream.exists()


async def test_sealed_reason_must_match_a_definitive_state_reason(tmp_path):
    from main_logic.visit.recovery import build_upload_doc

    await seed_roster(tmp_path)
    v = vid(92)
    await make_visit(tmp_path, v, [ln(0)], finalized="peer_left")
    records = _stream_records(v)
    _write_stream(tmp_path, v, records)
    doc = build_upload_doc(records, visit_id=v, finalized_reason="wrap_up")   # 与 state 记的不一致
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(doc), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, uploaded), = uploads.calls
    # state 已记确定的结束原因：以它为准从流水重封
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
    # 上传回调写进文件的分片进度不算「与流水不一致」：不重封、不清零
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
    doc["request"]["lines"] = doc["request"]["lines"][:1]      # 封存文件缺行
    sealed.write_text(json.dumps(doc), encoding="utf-8")

    def locked(path):
        raise PermissionError("locked by antivirus")

    monkeypatch.setattr(recovery, "_read_stream", locked)
    uploads = Uploads()
    await _recover(tmp_path, upload_transcript=uploads)
    # 流水在却读不出：比对做不了，两份都留着、这轮不上传，不能把缺行的文件当正本传上去
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
    # 只剩上传文件、没有流水可比：角色 id / 传输方式坏了同样按损坏处理，不交给上传回调
    assert uploads.calls == [] and not (d / f"{v}.upload.json").exists()



async def test_expired_upload_marks_its_queued_report_transcript_unavailable(tmp_path):
    v = vid(97)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps(_sealed(v)), encoding="utf-8")
    old = time.time() - 8 * 86400
    os.utime(sealed, (old, old))                                 # 待传转录已过 7 天
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(json.dumps({"visit_id": v, "include_transcript": True}), encoding="utf-8")
    reports = Reports()
    # 放弃前本次启动再补传一次，仍失败
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    # 转录到期被放弃：举报照常提交，并在举报里记下转录不可用的原因
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
    # 补录的 digest 同样拿到家人称呼：对端自称「妈妈」换成通用标签
    assert "妈妈" not in {seg["speaker_label"] for seg in segments}


async def test_report_is_not_marked_unavailable_while_another_upload_copy_remains(tmp_path):
    from main_logic.visit.recovery import build_upload_doc

    v = vid(99)
    records = _stream_records(v)
    stream = _write_stream(tmp_path, v, records)
    sealed = _spool_dir(tmp_path) / f"{v}.upload.json"
    sealed.write_text(json.dumps(build_upload_doc(records, visit_id=v, finalized_reason="wrap_up")), encoding="utf-8")
    old = time.time() - 8 * 86400
    os.utime(stream, (old, old))                                 # 只有流水过期被删，封存文件还新
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(json.dumps({"visit_id": v, "include_transcript": True}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(), submit_report=reports)
    # 封存文件本轮照常传上去：举报不能带着「转录不可用」的诊断
    (visit_id, doc), = reports.calls
    assert not stream.exists() and "transcript_unavailable" not in doc


@pytest.mark.parametrize("damage", ["sealed_only", "stream_only"])
async def test_corrupt_upload_marks_its_queued_report_transcript_unavailable(tmp_path, damage):
    v = vid(100)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    if damage == "sealed_only":
        (d / f"{v}.upload.json").write_text("{torn", encoding="utf-8")       # 只剩坏掉的封存文件
    else:
        _write_stream(tmp_path, v, [{"kind": "line"}])                       # 流水没有头行：损坏
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(json.dumps({"visit_id": v, "include_transcript": True}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(), submit_report=reports)
    # 转录损坏、再也传不上去：举报照常提交，并记下转录不可用的原因
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
    # 内容是别的场次：交上去会举报错的人，受理后还会删掉原本要交的这份。不交也不删，改名隔离，
    # 让这场的位置空出来（留在原位会一直挡住这场之后的举报）
    assert reports.calls == [] and not path.exists()
    assert (reports_dir / f"{v}.json.mismatch").read_text(encoding="utf-8") == body
    # 位置空出来之后，这场新排队的举报照常提交；更早隔离的那份不被覆盖
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
        return "parts_out_of_range"                              # 终态拒收：文件可以删，但转录到不了 Servers

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
    state_path.write_text(json.dumps(data), encoding="utf-8")     # 超出浮点范围的整数
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    # 坏 state 那场按读不出处理；不能让 OverflowError 中断整轮，其余场次照常补传
    assert {visit_id for visit_id, _ in uploads.calls} >= {good}


def test_discarded_records_do_not_move_the_upload_end_time():
    from main_logic.visit.recovery import build_upload_doc

    v = vid(106)
    records = _stream_records(v) + [
        {"kind": "line", "lp": 9, "ts": 99999.0},                     # 坏行：会被丢弃
        {"kind": "mystery", "ts": 88888.0},                           # 不认识的记录
        {"kind": "usage", "ts": 77777.0, "d": {"llm_input_tokens": "bad"}},   # 用量记录里没有一项有效
    ]
    doc = build_upload_doc(records, visit_id=v, finalized_reason="wrap_up")
    # 被丢弃的记录带的时间戳不能挪动结束时间与时长
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
    # 举报文件写不进标记：提交的那份照样带上，Servers 才知道要求附带的转录已经没了
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
        # 分片上传把进度写回文件再回 False（回调契约允许）
        sealed.write_text(json.dumps({**doc, "parts_done": 2}), encoding="utf-8")
        return False

    await _recover(tmp_path, upload_transcript=multipart)
    # 保留期按 mtime 算：重试不能把 7 天期限往后推，否则失败的上传永远不过期
    assert abs(sealed.stat().st_mtime - old) < 2 and json.loads(sealed.read_text(encoding="utf-8"))["parts_done"] == 2


# ── 评审：在飞口径 / 摘要登记 / 上传信封 / 举报隔离 ──────────────────────────


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
        # runtime 已从 is_live 注销，但记忆 spool 的 writer 还开着：流水还在追加写
        await _recover(tmp_path, upload_transcript=uploads, submit_report=reports)
    finally:
        with spool_mod._OPEN_SPOOLS_LOCK:
            spool_mod._OPEN_SPOOLS.discard(key)
    # 与逐场补录同一口径：不封存流水、不删 outbox，排队的举报也等它
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
        # 摘要生成期间同一对新开场：交接要能在登记表里看到这个任务，等它而不是另起一次
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
    # 两个时间戳各自有限，差值溢出成 inf：时长记 0，不能抛 OverflowError
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
    # 比对出错不能冒出去中断整轮：只推迟这一场（两份都留着），其余场次照常上传
    assert [visit_id for visit_id, _ in uploads.calls] == [good]
    assert (_spool_dir(tmp_path) / f"{bad}.upload.jsonl").exists()
    assert (_spool_dir(tmp_path) / f"{bad}.upload.json").exists()


async def test_resealing_a_mismatched_normal_end_keeps_its_reason(tmp_path):
    from main_logic.visit.recovery import build_upload_doc

    await seed_roster(tmp_path)
    v = vid(115)
    await make_visit(tmp_path, v, [ln(0)], finalized=None)      # state 还没写 finalized，补录标成 crash
    records = _stream_records(v)
    _write_stream(tmp_path, v, records)
    doc = build_upload_doc(records, visit_id=v, finalized_reason="wrap_up")
    doc["request"]["lines"] = doc["request"]["lines"][:1]         # 格式合法，但与流水不一致
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(doc), encoding="utf-8")
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, uploaded), = uploads.calls
    # 从流水重封：转录以流水为准，结束原因沿用文件里正常收口记的，不退回 crash
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
    header.pop("own_char_uid")           # 较早的上传头布局没有这个字段
    _write_stream(tmp_path, v, [header])
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, doc), = uploads.calls
    # 补回角色 id：不能封出一份随即被当成坏文件删掉的上传文件
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
    # 角色 id 哪儿都补不回来：写文件前就挡下，流水留着下次再封（不是封了再删）
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
    # 传输方式没有别处可补：按流水损坏处理，在写上传文件之前判定，不封了再删
    assert uploads.calls == [] and not stream.exists()
    assert f"{v}.upload.json" not in written
    (visit_id, doc), = reports.calls
    assert doc["transcript_unavailable"] == "corrupt" and not report_path.exists()


async def test_sealed_upload_of_another_character_is_not_uploaded(tmp_path):
    v = vid(119)
    await make_visit(tmp_path, v, [], memory_enabled=False, last_summary_done=True)   # state 记 CHAR_UID_A
    d = _spool_dir(tmp_path)
    (d / f"{v}.upload.json").write_text(json.dumps({**_sealed(v), "own_char_uid": CHAR_UID_B}), encoding="utf-8")
    uploads = Uploads()
    await _recover(tmp_path, upload_transcript=uploads)
    # 只剩上传文件：角色 id 与本场 state.json 不一致，不能拿错的角色身份上传
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
    # 新版本写的完好文件（降级后）：不当损坏删，留着待处理；附转录的举报继续等它
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
        # 运行时的举报处理拿着同一把锁：标记要等它
        assert not task.done() and "transcript_unavailable" not in json.loads(path.read_text(encoding="utf-8"))
        path.unlink()                    # 锁内：实时重试受理后删掉 / 用户放弃
    finally:
        lock.release()
    await task
    # 已受理 / 放弃的举报不能被标记写回来、随后再交一次
    assert not path.exists()


async def test_report_deleted_between_read_and_write_is_not_recreated(tmp_path, monkeypatch):
    from main_logic.visit import recovery

    v = vid(122)
    path = _queue_report(tmp_path, v, include_transcript=True)
    real_load = recovery._load_json

    def load_then_deleted(p):
        doc = real_load(p)
        if Path(p) == path:
            path.unlink()                # 不走这把锁的删除方在读完之后删掉了它
        return doc

    monkeypatch.setattr(recovery, "_load_json", load_then_deleted)
    await recovery._mark_report_transcript_unavailable(tmp_path, v, "expired", recovery.RecoveryReport())
    assert not path.exists()


async def test_unavailable_marker_is_not_written_onto_another_visits_report(tmp_path):
    v, other = vid(123), vid(124)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    (d / f"{v}.upload.json").write_text("{torn", encoding="utf-8")       # 这场的转录损坏
    path = _queue_report(tmp_path, v)
    body = json.dumps({"visit_id": other, "include_transcript": True})
    path.write_text(body, encoding="utf-8")                               # 文件里却是别场的举报
    await _recover(tmp_path, upload_transcript=Uploads(), submit_report=Reports())
    # 这场转录的不可用原因不能盖到别场的举报上
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
    # 终态拒收与上传成功分开记：uploads 的 True 只表示转录到了 Servers
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
    # 删不掉：文件里记下拒收，保留期不因此后推
    assert json.loads(sealed.read_text(encoding="utf-8"))["rejected"] == "parts_out_of_range"
    assert abs(sealed.stat().st_mtime - old) < 2
    report_path = _queue_report(tmp_path, v, include_transcript=True)
    reports = Reports()
    report = await _recover(tmp_path, upload_transcript=reject, submit_report=reports)
    # 下次启动不再整份重传、再被拒一次：直接删文件，举报带着拒收原因提交
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
    # 原因没记进举报、本轮举报也没交出去：上传文件是终态拒收唯一持久的记录，不能先删
    assert sealed.exists() and json.loads(sealed.read_text(encoding="utf-8"))["rejected"] == "parts_out_of_range"
    monkeypatch.setattr(recovery_mod, "_write_private_json", real_write)
    reports = Reports()
    await _recover(tmp_path, upload_transcript=reject, submit_report=reports)
    # 下次启动：不再重传，先把原因记进举报、删上传文件，举报带着原因交上去
    (visit_id, doc), = reports.calls
    assert uploads == [v] and not sealed.exists() and doc["transcript_unavailable"] == "parts_out_of_range"


async def test_expired_upload_gets_one_more_attempt_before_it_is_given_up(tmp_path):
    v = vid(110)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps(_sealed(v)), encoding="utf-8")
    old = time.time() - 8 * 86400
    os.utime(sealed, (old, old))                                 # 8 天没开 app：本次启动之前一次都没试过
    uploads = Uploads(ok=True)
    await _recover(tmp_path, upload_transcript=uploads)
    # 「自结束起 7 天仍失败才放弃」：放弃之前本次启动先补传一次，传上去了就不丢
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
    # 再试一次仍失败：放弃这份转录，排队的举报本轮就带着原因交上去
    (visit_id, doc), = reports.calls
    assert len(uploads.calls) == 1 and not sealed.exists() and doc["transcript_unavailable"] == "expired"


async def test_recovery_digest_protects_other_local_cat_names_in_peer_labels(tmp_path):
    await seed_roster(tmp_path, peer_display="B")                # 对端把自己叫成本机另一只猫的名字
    await make_visit(tmp_path, vid(112), [ln(i, f"line {i}", ("own_cat", "peer_cat", "peer_human", "own_human")[i % 4])
                                          for i in range(8)])
    server = FakeMemoryServer()
    await _recover(tmp_path, server)
    segments = server.calls("scoped_history")[1]["segments"]
    # 补录的 digest 同样拿到本机角色名单：对端不能顶替成本机另一只猫
    assert "B" not in {seg["speaker_label"] for seg in segments}


async def test_expired_stream_keeps_its_age_and_finalized_reason_through_the_extra_attempt(tmp_path):
    v = vid(113)
    spool = await make_visit(tmp_path, v, [], memory_enabled=False, finalized="wrap_up", last_summary_done=True)
    stream = _write_stream(tmp_path, v, _stream_records(v))
    old = time.time() - 8 * 86400
    for path in (stream, spool.state_path):
        os.utime(path, (old, old))                              # 8 天没开 app：流水和 state 都过期了
    uploads = Uploads(ok=False)
    await _recover(tmp_path, upload_transcript=uploads)
    (visit_id, doc), = uploads.calls
    # 补传之前 state.json 不能先被按龄删掉：正常结束的场次照样按 wrap_up 封存，不变成 crash
    assert doc["request"]["finalized_reason"] == "wrap_up"
    # 封出来的文件接着流水的年龄算：再试一次仍失败就在本轮放弃，不再多留 7 天
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
    # 没有上传回调：一次都没试过，不能就此放弃转录，举报也继续等它
    assert sealed.exists() and reports.calls == []
    older = time.time() - 15 * 86400
    os.utime(sealed, (older, older))
    await _recover(tmp_path, upload_transcript=None, submit_report=reports)
    # 但最多再留一个保留期：超过 2 倍保留期照样按到期放弃，附转录的举报带着原因交出去
    (visit_id, doc), = reports.calls
    assert not sealed.exists() and doc["transcript_unavailable"] == "expired"


async def test_sealed_upload_of_another_account_is_not_uploaded(tmp_path):
    v = vid(115)
    await make_visit(tmp_path, v, [], memory_enabled=False, finalized="wrap_up", last_summary_done=True)
    d = _spool_dir(tmp_path)
    (d / f"{v}.upload.json").write_text(json.dumps({**_sealed(v), "own_visit_uid": OWN_B}), encoding="utf-8")
    uploads = Uploads(ok=True)
    await _recover(tmp_path, upload_transcript=uploads)
    # 角色 id 一样也不够：账号对不上的文件交上去就是用错的账号上传
    assert uploads.calls == []


async def test_ownerless_sealed_upload_takes_the_visits_account(tmp_path):
    v = vid(116)
    await make_visit(tmp_path, v, [], memory_enabled=False, finalized="wrap_up", last_summary_done=True)
    d = _spool_dir(tmp_path)
    (d / f"{v}.upload.json").write_text(json.dumps({**_sealed(v), "own_visit_uid": None}), encoding="utf-8")
    uploads = Uploads(ok=True)
    await _recover(tmp_path, upload_transcript=uploads)
    # 旧版本封出来的无主文件：用本场 state.json 的账号补上，上传回调才选得中登录账号
    (visit_id, doc), = uploads.calls
    assert doc["own_visit_uid"] == OWN_A


async def test_expired_state_is_kept_for_checking_a_lone_ownerless_upload(tmp_path):
    v = vid(117)
    spool = await make_visit(tmp_path, v, [], memory_enabled=False, finalized="wrap_up", last_summary_done=True)
    sealed = _spool_dir(tmp_path) / f"{v}.upload.json"
    sealed.write_text(json.dumps({**_sealed(v), "own_visit_uid": None}), encoding="utf-8")
    old = time.time() - 8 * 86400
    for path in (sealed, spool.state_path):
        os.utime(path, (old, old))                              # 无主上传文件与 state.json 都过期了
    uploads = Uploads(ok=True)
    await _recover(tmp_path, upload_transcript=uploads)
    # 第一遍回收不能先删 state.json：只剩上传文件时要拿它核对身份、给无主文件补账号
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
        assert "transcript_unavailable" not in doc        # 共用电脑上另一账号的举报不带这份转录的原因
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
        raise OSError("in use")                              # 核对归属时读不了举报

    monkeypatch.setattr(recovery, "_mark_report_sync", unreadable)

    async def reject(_visit_id, _doc):
        return "parts_out_of_range"

    reports = Reports()
    await _recover(tmp_path, upload_transcript=reject, submit_report=reports)
    assert any(visit_id == v for visit_id, _doc in reports.calls), "目标举报必须提交"
    for _visit_id, doc in reports.calls:
        assert "transcript_unavailable" not in doc          # 提交前复核：属于另一账号的举报不带原因



@pytest.mark.parametrize("damage", ["sealed_only", "stream_only"])
async def test_a_corrupt_upload_leaves_another_accounts_report_unmarked(tmp_path, damage):
    v = vid(106)
    await make_visit(tmp_path, v, [ln(0)], own_uid=OWN_A)
    d = _spool_dir(tmp_path)
    if damage == "sealed_only":
        (d / f"{v}.upload.json").write_text("{torn", encoding="utf-8")
    else:
        _write_stream(tmp_path, v, [{"kind": "line"}])                       # 流水没有头行：损坏
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    other = "f" * 24
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": other}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(), submit_report=reports)
    # 损坏的是 OWN_A 那一侧的转录：另一账号排的举报不带这个原因
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
            raise PermissionError("in use")                   # Windows 共享冲突
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
    # 到期放弃的是 OWN_A 那一侧的转录：另一账号排的举报不带这个原因
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
    # 只剩流水：后台会从流水重封；旁边有坏封存文件：后台重封不了，不白排一个立刻退出的重试
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
    # 待传的是 OWN_A 那一侧的转录：另一账号的附转录举报不等它
    assert [visit_id for visit_id, _doc in reports.calls] == [v]



async def test_the_report_gate_reads_the_owner_from_the_upload_when_state_is_gone(tmp_path):
    v = vid(111)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    newer = {**_sealed(v), "v": 99, "own_visit_uid": OWN_A}           # 新版本写的、本版本留着不动
    (d / f"{v}.upload.json").write_text(json.dumps(newer), encoding="utf-8")
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": "f" * 24}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    # 没有 state.json：归属从封存文件里取，另一账号的附转录举报不等这份
    assert [visit_id for visit_id, _doc in reports.calls] == [v]



async def test_the_report_gate_prefers_the_owner_named_by_the_upload_itself(tmp_path):
    v = vid(112)
    await make_visit(tmp_path, v, [ln(0)], own_uid=OWN_B)                   # state.json 记的是 B
    newer = {**_sealed(v), "v": 99, "own_visit_uid": OWN_A}                 # 留着的新版本转录是 A 的
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(newer), encoding="utf-8")
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": OWN_A}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    # 待传的是 A 自己那一侧的转录：A 的附转录举报照样等它，不能按 state.json 的 B 放行
    assert reports.calls == []



async def test_the_report_gate_ignores_the_owner_of_a_file_from_another_visit(tmp_path, monkeypatch):
    from main_logic.visit import recovery

    v = vid(113)
    await make_visit(tmp_path, v, [ln(0)], own_uid=OWN_B)
    wrong = {**_sealed(vid(114)), "own_visit_uid": OWN_A}                    # 别场的文件，写着 A
    (_spool_dir(tmp_path) / f"{v}.upload.json").write_text(json.dumps(wrong), encoding="utf-8")

    async def undeletable(_spool_dir, _visit_id):
        return False                                                    # 一时删不掉，留着待处理

    monkeypatch.setattr(recovery, "_drop_corrupt_sealed", undeletable)
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": OWN_B}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    # 别场文件里写的 A 不算：按 state.json 的 B，B 的附转录举报照旧等
    assert reports.calls == []



async def test_an_expired_self_contained_upload_keeps_its_owner_for_the_report(tmp_path):
    v = vid(115)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True)
    sealed = d / f"{v}.upload.json"
    sealed.write_text(json.dumps(_sealed(v)), encoding="utf-8")       # 自带 A 的归属，没有 state.json
    old = time.time() - 8 * 86400
    os.utime(sealed, (old, old))
    reports_dir = tmp_path / "visit_reports"
    reports_dir.mkdir()
    (reports_dir / f"{v}.json").write_text(
        json.dumps({"visit_id": v, "include_transcript": True, "own_visit_uid": "f" * 24}), encoding="utf-8")
    reports = Reports()
    await _recover(tmp_path, upload_transcript=Uploads(ok=False), submit_report=reports)
    # 到期放弃的是 A 的转录（归属在删之前取）：另一账号的举报不带这个原因
    assert not sealed.exists()
    assert [visit_id for visit_id, _doc in reports.calls] == [v]
    assert all("transcript_unavailable" not in doc for _visit_id, doc in reports.calls)



async def test_the_report_gate_uses_the_state_owner_for_a_rejected_current_file(tmp_path, monkeypatch):
    from main_logic.visit import recovery

    v = vid(116)
    await make_visit(tmp_path, v, [ln(0)], own_uid=OWN_B)                   # state.json 记的是 B
    conflicting = {**_sealed(v), "own_visit_uid": OWN_A}                     # 本场格式，却写着 A
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
    # 与 state.json 对不上的文件被拒：按 state 的 B 算，B 的附转录举报照旧等，不当作别人的转录放行
    assert reports.calls == []
