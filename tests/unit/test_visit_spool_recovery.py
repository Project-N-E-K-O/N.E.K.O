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

    def flaky(spool_dir, visit_id, reason, owner=None):
        if visit_id == bad:
            raise PermissionError("locked by antivirus")
        return real_seal(spool_dir, visit_id, reason, owner)

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

    def flaky(spool_dir, visit_id, reason, owner=None):
        if visit_id == bad:
            raise TypeError("'<' not supported between instances of 'NoneType' and 'int'")
        return real_seal(spool_dir, visit_id, reason, owner)

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



async def test_corrupt_state_that_may_be_this_persons_still_blocks_the_wipe(tmp_path):
    await seed_roster(tmp_path)
    d = _spool_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vid(74)}.state.json").write_text("{torn", encoding="utf-8")   # 没有头行可认：分不清是谁的
    outcome = await forget_person(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X, client=FakeMemoryServer().client())
    # 宁可不结清也不留下对端身份：日志留着等下次
    assert outcome.done is False


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
