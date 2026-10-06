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

"""Transcript upload journal / chunked upload / queued reports (visit design §4.6 report, §4.7, PR-09a)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import time
from pathlib import Path

import httpx
import pytest

from main_logic.visit import local_chars
from main_logic.visit.recovery import visit_spool_recovery
from main_routers.visit_router import accounts
from main_routers.visit_router import credentials as cr
from main_routers.visit_router import transcript_upload as tu
from tests.unit.visit_memory_test_helpers import FakeMemoryServer, vid
from tests.unit.visit_servers_fake import BASE, FakeServers

OWN = "a" * 24
OTHER = "b" * 24
CHAR_UID = "c" * 32
V1 = vid(1)


@pytest.fixture
def servers(tmp_path, monkeypatch):
    tu._reset_for_tests()
    fake = FakeServers()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(cr, "get_external_http_client", lambda: client)
    state = {"account": "u1"}

    async def session():
        if state["account"] is None:
            raise cr.VisitLoginRequired()
        return cr._ServersSession(base_url=BASE, access_token="bearer-x", client_id="c1", account=state["account"])

    async def local_account():
        return state["account"]

    monkeypatch.setattr(cr, "_servers_session", session)
    monkeypatch.setattr(accounts, "local_account", local_account)
    monkeypatch.setattr(accounts, "config_dir_provider", lambda: tmp_path)
    monkeypatch.setattr(tu, "config_dir_provider", lambda: tmp_path)
    monkeypatch.setattr(tu, "is_live", lambda _v: False)

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(tu, "_sleep", no_sleep)
    (tmp_path / "visit_accounts.json").write_text(json.dumps({"accounts": {"u1": OWN, "u2": OTHER}}),
                                                  encoding="utf-8")
    yield fake, state
    tu._reset_for_tests()


def _spool(tmp_path: Path) -> Path:
    return tmp_path / "visit_spool"


async def _journal(tmp_path, visit_id=V1, *, role="host", started_at=1000.0) -> tu.UploadJournal:
    journal = tu.UploadJournal(tmp_path, visit_id)
    await journal.open(role=role, own_visit_uid=OWN, own_char_uid=CHAR_UID, transport="trtc",
                       started_at=started_at, app_version="1.2")
    return journal


async def _say(journal, lp, text="hi", *, side="host", speaker="own_cat", ts=None, truncated=False):
    await journal.append_line(lp=lp, side=side, speaker=speaker, ts=ts if ts is not None else 1000.0 + lp,
                              text=text, truncated=truncated)


def _crash(journal) -> None:
    """kill -9 between records: nothing sealed, the writer's queue reached the OS, the fd dies with the process."""
    journal._executor.shutdown(wait=True)
    journal._close_sync()


def _sealed(tmp_path, visit_id=V1) -> dict:
    return json.loads((_spool(tmp_path) / f"{visit_id}.upload.json").read_text(encoding="utf-8"))


def _write_sealed(tmp_path, doc, visit_id=V1):
    path = _spool(tmp_path) / f"{visit_id}.upload.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


async def _recover(tmp_path, monkeypatch, submit=None):
    async def readable():
        return None

    async def names():
        return ["A"]

    async def resolve(_uid):
        return "A"

    async def chips(*_a, **_k):
        return True

    monkeypatch.setattr(local_chars, "ensure_characters_readable", readable)
    return await visit_spool_recovery(
        chips, tu.upload_visit_transcript, config_dir=tmp_path, is_live=lambda _v: False,
        resolve_char_name=resolve, list_char_names=names, submit_report=submit or tu.submit_queued_report,
        client=FakeMemoryServer().client(),
    )


# ── 流水与封存 ─────────────────────────────────────────────────────────


async def test_seal_writes_the_upload_doc_then_deletes_the_stream(tmp_path, servers, monkeypatch):
    journal = await _journal(tmp_path)
    await _say(journal, 2, "second", side="guest", speaker="peer_cat")
    await _say(journal, 1, "first")
    journal.note_usage({"llm_input_tokens": 100, "llm_output_tokens": 20}, ts=1002.1)
    journal.note_usage({"tts_requests": 1}, ts=1002.2)
    journal.note_usage({"tts_chars": 7}, ts=1002.3)
    order = []
    real_write, real_unlink = tu._write_private_json, Path.unlink

    def write(path, doc):
        order.append(("write", Path(path).name))
        real_write(path, doc)

    def unlink(self, *a, **k):
        order.append(("unlink", self.name))
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(tu, "_write_private_json", write)
    monkeypatch.setattr(Path, "unlink", unlink)
    doc = await journal.seal("wrap_up", ended_at=1002.3)
    monkeypatch.undo()
    assert order[:2] == [("write", f"{V1}.upload.json"), ("unlink", f"{V1}.upload.jsonl")]
    assert not (_spool(tmp_path) / f"{V1}.upload.jsonl").exists()
    sealed = _sealed(tmp_path)
    assert sealed == doc and sealed["own_visit_uid"] == OWN and sealed["own_char_uid"] == CHAR_UID
    request = sealed["request"]
    assert [line["text"] for line in request["lines"]] == ["first", "second"]
    assert request["finalized_reason"] == "wrap_up" and request["role"] == "host"
    assert request["usage"] == {"duration_s": 2, "llm_input_tokens": 100, "llm_output_tokens": 20,
                                "tts_requests": 1, "tts_chars": 7}
    if os.name != "nt":
        assert stat.S_IMODE((_spool(tmp_path) / f"{V1}.upload.json").stat().st_mode) == 0o600


async def test_seal_counts_the_quiet_tail_up_to_finalize(tmp_path, servers):
    journal = await _journal(tmp_path)
    await _say(journal, 1, "hi", ts=1003.0)
    request = (await journal.seal("wrap_up", ended_at=1060.0))["request"]
    # 最后一句之后安静了一分钟才收尾：时长算到收尾时刻，不是最后一条记录
    assert request["ended_at"] == 1060.0 and request["usage"]["duration_s"] == 60


async def test_seal_never_moves_the_end_before_the_last_record(tmp_path, servers):
    journal = await _journal(tmp_path)
    await _say(journal, 1, "hi", ts=1003.0)
    request = (await journal.seal("wrap_up", ended_at=1001.0))["request"]
    assert request["ended_at"] == 1003.0 and request["usage"]["duration_s"] == 3


async def test_usage_after_the_seal_is_dropped(tmp_path, servers):
    journal = await _journal(tmp_path)
    await journal.seal("route_end")
    journal.note_usage({"tts_chars": 50})
    journal.note_anomaly()
    assert not (_spool(tmp_path) / f"{V1}.upload.jsonl").exists()
    assert _sealed(tmp_path)["request"]["usage"]["tts_chars"] == 0


async def test_empty_or_negative_usage_writes_nothing(tmp_path, servers):
    journal = await _journal(tmp_path)
    journal.note_usage({"tts_chars": 0})
    journal.note_usage({"tts_chars": -3, "llm_input_tokens": True})
    await _say(journal, 1)
    lines = (_spool(tmp_path) / f"{V1}.upload.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(raw)["kind"] for raw in lines] == ["header", "line"]


async def test_telemetry_counters_carry_no_visit_id(tmp_path, servers, monkeypatch):
    seen = []
    monkeypatch.setattr(tu, "counter", lambda name, value=1, **dims: seen.append((name, dims)))
    monkeypatch.setattr(tu, "histogram", lambda name, value, **dims: seen.append((name, dims)))
    journal = await _journal(tmp_path)
    journal.note_usage({"llm_input_tokens": 5, "tts_chars": 3})
    await journal.seal("wrap_up")
    assert {name for name, _ in seen} >= {"visit_llm_input_tokens", "visit_tts_chars", "visit_duration_s"}
    assert all(V1 not in json.dumps(dims) and "visit_id" not in dims for _, dims in seen)


# ── 崩溃补录（与 PR-08 补录接上）────────────────────────────────────────


async def test_crashed_visit_is_uploaded_once_from_its_stream(tmp_path, servers, monkeypatch):
    fake, _ = servers
    journal = await _journal(tmp_path)
    for lp in range(1, 6):
        await _say(journal, lp, f"line {lp}")
    journal.note_usage({"llm_input_tokens": 10})
    journal.note_usage({"llm_input_tokens": 5, "llm_output_tokens": 3})
    for _ in range(3):
        journal.note_anomaly(ts=1010.0)
    await _say(journal, 6, "last", ts=1020.0)
    _crash(journal)
    report = await _recover(tmp_path, monkeypatch)
    assert report.uploads == {V1: True} and fake.count("/api/visit/transcripts") == 1
    body = json.loads(fake.requests[-1].content)
    assert body["finalized_reason"] == "crash" and body["role"] == "host" and body["started_at"] == 1000.0
    assert body["ended_at"] == 1020.0 and body["anomalies"] == 3 and len(body["lines"]) == 6
    assert body["usage"]["llm_input_tokens"] == 15 and body["usage"]["llm_output_tokens"] == 3
    assert not list(_spool(tmp_path).glob(f"{V1}.upload*"))


async def test_header_only_crash_is_still_uploaded(tmp_path, servers, monkeypatch):
    fake, _ = servers
    journal = await _journal(tmp_path, started_at=1234.0)
    _crash(journal)
    await _recover(tmp_path, monkeypatch)
    body = json.loads(fake.requests[-1].content)
    assert body["ended_at"] == body["started_at"] == 1234.0 and body["lines"] == []
    assert all(v == 0 for v in body["usage"].values())


async def test_crash_before_any_usage_uploads_zero_usage(tmp_path, servers, monkeypatch):
    fake, _ = servers
    journal = await _journal(tmp_path)
    await _say(journal, 1, "peer says hi", side="guest", speaker="peer_cat")
    _crash(journal)
    await _recover(tmp_path, monkeypatch)
    body = json.loads(fake.requests[-1].content)
    assert [line["text"] for line in body["lines"]] == ["peer says hi"]
    assert all(v == 0 for k, v in body["usage"].items() if k != "duration_s")


async def test_upload_is_held_while_another_account_is_signed_in(tmp_path, servers, monkeypatch):
    fake, state = servers
    journal = await _journal(tmp_path)
    await _say(journal, 1)
    await journal.seal("wrap_up")
    state["account"] = "u2"
    assert await tu.upload_visit_transcript(V1, _sealed(tmp_path)) is False
    assert fake.count("/api/visit/transcripts") == 0
    await _recover(tmp_path, monkeypatch)
    assert (_spool(tmp_path) / f"{V1}.upload.json").exists()
    state["account"] = "u1"
    await _recover(tmp_path, monkeypatch)
    assert fake.count("/api/visit/transcripts") == 1 and not (_spool(tmp_path) / f"{V1}.upload.json").exists()


async def test_logged_out_keeps_the_file(tmp_path, servers):
    _fake, state = servers
    journal = await _journal(tmp_path)
    await journal.seal("wrap_up")
    state["account"] = None
    assert await tu.upload_visit_transcript(V1, _sealed(tmp_path)) is False


# ── 分块上传 ───────────────────────────────────────────────────────────


def _big_doc(n_lines=160, size=4096) -> dict:
    lines = []
    for lp in range(1, n_lines + 1):
        side = "host" if lp % 2 else "guest"
        lines.append({"lp": lp, "side": side, "from": "own_cat" if side == "host" else "peer_cat",
                      "ts": 1000.0 + lp, "text": '"' * size, "truncated": False})
    return {"v": 1, "own_visit_uid": OWN, "own_char_uid": CHAR_UID, "transport": "trtc", "request": {
        "visit_id": V1, "role": "host", "started_at": 1000.0, "ended_at": 2000.0, "finalized_reason": "wrap_up",
        "usage": {"duration_s": 1000, "llm_input_tokens": 1, "llm_output_tokens": 1, "tts_requests": 1,
                  "tts_chars": 1},
        "lines": lines, "anomalies": 0, "app_version": "1.2",
    }}


async def test_large_transcript_is_split_and_reassembles(tmp_path, servers):
    fake, _ = servers
    doc = _big_doc()
    assert len(tu._encode_body(doc["request"])) > tu.VISIT_UPLOAD_CHUNK_BYTES
    _write_sealed(tmp_path, doc)
    assert await tu.upload_visit_transcript(V1, doc) is True
    sizes = [len(r.content) for r in fake.requests if r.url.path == "/api/visit/transcripts"]
    assert len(sizes) > 1 and all(s <= tu.VISIT_UPLOAD_CHUNK_BYTES for s in sizes)
    assert fake.complete[(V1, "host")] == doc["request"]["lines"]


async def test_too_large_doubles_parts_and_resends_the_whole_group(tmp_path, servers):
    fake, _ = servers
    fake.limit = 300 * 1024
    doc = _big_doc()
    _write_sealed(tmp_path, doc)
    round_ = await tu.retry_visit_once(V1)
    assert round_.pending is False
    assert not (_spool(tmp_path) / f"{V1}.upload.json").exists()
    assert fake.complete[(V1, "host")] == doc["request"]["lines"]
    bodies = [json.loads(r.content) for r in fake.requests if r.url.path == "/api/visit/transcripts"
              and len(r.content) <= fake.limit]
    # 512 KiB 规划出 4 块（每块约 330 KiB），300 KiB 上限下 413 一次、翻倍成 8 块整组重传
    assert max(b["parts"] for b in bodies) == 8 and fake.count("/api/visit/transcripts") <= 12


async def test_regrouping_does_not_collide_with_accepted_chunks(tmp_path, servers):
    fake, _ = servers
    doc = _big_doc()
    _write_sealed(tmp_path, doc)
    # 第一代 parts=2 的块 0 已受理，块 1 超限：新一代 parts=4 的四块都不应被判 duplicate
    fake.limit = 10 * 1024 * 1024
    request = doc["request"]
    first = tu.chunk_body(request, 0, 2)
    fake.handler(httpx.Request("POST", f"{BASE}/api/visit/transcripts", content=tu._encode_body(first)))
    doc["parts"], doc["accepted_parts"] = 2, [0]
    _write_sealed(tmp_path, doc)
    fake.limit = len(tu._encode_body(tu.chunk_body(request, 1, 2))) - 1
    assert await tu.upload_visit_transcript(V1, doc) is True
    later = [json.loads(r.content) for r in fake.requests[1:] if r.url.path == "/api/visit/transcripts"
             and len(r.content) <= fake.limit]
    # 新一代从块 0 起整组按序重传（清空了上一代的已受理集合），不靠 Servers 回报纠正
    assert [b["part"] for b in later if b["parts"] == 4] == [0, 1, 2, 3]
    assert fake.complete[(V1, "host")] == request["lines"]


async def test_restart_resends_only_the_missing_chunks(tmp_path, servers):
    fake, _ = servers
    doc = _big_doc()
    _write_sealed(tmp_path, doc)
    fake.fail_parts = {1}
    assert await tu.upload_visit_transcript(V1, doc) is False
    progress = _sealed(tmp_path)
    parts = progress["parts"]
    assert parts > 2 and progress["accepted_parts"] == [0]
    sent_before = fake.count("/api/visit/transcripts")
    # 「重启」：从磁盘读回分片进度
    assert await tu.upload_visit_transcript(V1, _sealed(tmp_path)) is True
    resent = [json.loads(r.content)["part"] for r in fake.requests[sent_before:]]
    assert resent == list(range(1, parts))


async def test_chunk_progress_does_not_extend_the_file_age(tmp_path, servers):
    fake, _ = servers
    doc = _big_doc()
    path = _write_sealed(tmp_path, doc)
    old = time.time() - 3 * 86400
    os.utime(path, (old, old))
    fake.fail_parts = {1}
    await tu.upload_visit_transcript(V1, doc)
    assert abs(path.stat().st_mtime - old) < 1


@pytest.mark.parametrize("mode,code", [
    ("budget", "transcript_budget_exceeded"), ("parts", "parts_out_of_range"),
    ("not_started_final", "visit_not_started"),
])
async def test_terminal_rejections_stop_and_release_the_report(tmp_path, servers, mode, code):
    fake, _ = servers
    fake.transcript_mode = mode
    doc = _big_doc(4, 10)
    _write_sealed(tmp_path, doc)
    await tu.queue_report(tmp_path, _report_doc())
    round_ = await tu.retry_visit_once(V1)
    assert round_.pending is False and not (_spool(tmp_path) / f"{V1}.upload.json").exists()
    assert fake.reports and fake.reports[0]["transcript_unavailable"] == code
    assert not (tmp_path / "visit_reports" / f"{V1}.json").exists()


@pytest.mark.parametrize("mode", ["503", "not_started", "429"])
async def test_retryable_failures_keep_the_file(tmp_path, servers, mode):
    fake, _ = servers
    fake.transcript_mode = mode
    _write_sealed(tmp_path, _big_doc(4, 10))
    round_ = await tu.retry_visit_once(V1)
    assert round_.pending is True and (_spool(tmp_path) / f"{V1}.upload.json").exists()
    if mode == "429":
        assert round_.retry_after_s == 77


async def test_duplicate_counts_as_uploaded(tmp_path, servers):
    fake, _ = servers
    doc = _big_doc(4, 10)
    fake.complete[(V1, "host")] = doc["request"]["lines"]
    _write_sealed(tmp_path, doc)
    assert (await tu.retry_visit_once(V1)).pending is False
    assert not (_spool(tmp_path) / f"{V1}.upload.json").exists()


async def test_upload_given_up_after_seven_days(tmp_path, servers, caplog):
    fake, _ = servers
    path = _write_sealed(tmp_path, _big_doc(4, 10))
    old = time.time() - 8 * 86400
    os.utime(path, (old, old))
    fake.transcript_mode = "503"
    await tu.queue_report(tmp_path, _report_doc())
    with caplog.at_level(logging.WARNING):
        await tu.retry_visit_once(V1)
    assert not path.exists() and "upload_expired" in caplog.text
    assert fake.reports[0]["transcript_unavailable"] == "expired"


async def test_logs_never_carry_the_transcript_text(tmp_path, servers, caplog):
    fake, _ = servers
    fake.transcript_mode = "503"
    journal = await _journal(tmp_path)
    await _say(journal, 1, "SECRET-LINE-TEXT")
    await journal.seal("wrap_up")
    with caplog.at_level(logging.DEBUG):
        await tu.retry_visit_once(V1)
        fake.transcript_mode = "budget"
        await tu.retry_visit_once(V1)
    assert "SECRET-LINE-TEXT" not in caplog.text and "bearer-x" not in caplog.text


async def test_background_retry_runs_until_delivered(tmp_path, servers):
    fake, _ = servers
    fake.transcript_mode = "503"
    _write_sealed(tmp_path, _big_doc(4, 10))
    calls = []

    async def sleep(seconds):
        calls.append(seconds)
        if len(calls) == 2:
            fake.transcript_mode = "ok"

    tu._sleep = sleep
    await tu.schedule_visit_retry(V1)
    assert calls == list(tu.VISIT_UPLOAD_RETRY_BACKOFF_S[:2])
    assert not (_spool(tmp_path) / f"{V1}.upload.json").exists()


async def test_background_retry_waits_for_a_live_visit(tmp_path, servers, monkeypatch):
    fake, _ = servers
    _write_sealed(tmp_path, _big_doc(4, 10))
    monkeypatch.setattr(tu, "is_live", lambda _v: True)
    await tu.schedule_visit_retry(V1)
    assert fake.count("/api/visit/transcripts") == 0


# ── 举报队列（单元）────────────────────────────────────────────────────


def _report_doc(**over) -> dict:
    doc = {"visit_id": V1, "own_visit_uid": OWN, "own_account": "u1", "reason": "harassment", "note": "n",
           "include_transcript": True, "anomalies": 2, "app_version": "1.2", "queued_at": time.time()}
    doc.update(over)
    return doc


async def test_report_request_rebuilt_from_file_matches(tmp_path, servers):
    fake, _ = servers
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    assert (await tu.retry_visit_once(V1)).pending is False
    assert fake.reports == [{"visit_id": V1, "reason": "harassment", "include_transcript": False,
                             "anomalies": 2, "app_version": "1.2", "note": "n"}]
    assert "peer_uid" not in json.dumps(fake.reports)


async def test_report_waits_for_its_transcript_then_goes(tmp_path, servers):
    fake, _ = servers
    fake.transcript_mode = "429"
    _write_sealed(tmp_path, _big_doc(4, 10))
    await tu.queue_report(tmp_path, _report_doc())
    assert (await tu.retry_visit_once(V1)).pending is True
    assert fake.count("/api/visit/reports") == 0
    fake.transcript_mode = "ok"
    assert (await tu.retry_visit_once(V1)).pending is False
    assert fake.transcript_seen_at_report == [True]


async def test_report_without_transcript_ignores_the_upload_gate(tmp_path, servers):
    fake, _ = servers
    fake.transcript_mode = "503"
    _write_sealed(tmp_path, _big_doc(4, 10))
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    await tu.retry_visit_once(V1)
    assert fake.count("/api/visit/reports") == 1
    assert not (tmp_path / "visit_reports" / f"{V1}.json").exists()


async def test_queued_report_is_resubmitted_at_startup(tmp_path, servers, monkeypatch):
    fake, _ = servers
    fake.report_mode = "503"
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    assert (await tu.retry_visit_once(V1)).pending is True
    fake.report_mode = "ok"
    report = await _recover(tmp_path, monkeypatch)
    assert report.reports == {V1: True} and not (tmp_path / "visit_reports" / f"{V1}.json").exists()


async def test_queued_report_of_an_unknown_visit_is_kept_for_the_user(tmp_path, servers):
    fake, _ = servers
    fake.report_mode = "404"
    path = tmp_path / "visit_reports" / f"{V1}.json"
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    # 只有受理或用户放弃才删：被拒的留着、标记、不再自动重提
    assert (await tu.retry_visit_once(V1)).pending is False
    assert json.loads(path.read_text(encoding="utf-8"))["rejected"] == "unknown_visit"
    sent = fake.count("/api/visit/reports")
    assert (await tu.retry_visit_once(V1)).pending is False and fake.count("/api/visit/reports") == sent
    assert await tu.submit_queued_report(V1, await tu.load_report(tmp_path, V1)) is False
    assert fake.count("/api/visit/reports") == sent
    assert (await tu.list_queued_reports(tmp_path, "u1"))[0]["rejected"] == "unknown_visit"
    # 用户点「重试」：照发；这回网络错误 → 回到普通排队，后台接着重提
    fake.report_mode = "503"
    assert (await tu.retry_visit_once(V1, manual=True)).pending is True
    assert "rejected" not in json.loads(path.read_text(encoding="utf-8"))
    fake.report_mode = "ok"
    assert (await tu.retry_visit_once(V1)).pending is False and not path.exists()


async def test_recovery_callback_marks_an_unknown_visit_and_keeps_the_file(tmp_path, servers):
    fake, _ = servers
    fake.report_mode = "404"
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    assert await tu.submit_queued_report(V1, await tu.load_report(tmp_path, V1)) is False
    assert (await tu.load_report(tmp_path, V1))["rejected"] == "unknown_visit"


async def test_recovery_callback_skips_a_report_that_was_replaced(tmp_path, servers):
    fake, _ = servers
    stale = _report_doc(include_transcript=False, queued_at=1000.0)
    # 补录读到的是旧的那份；文件此刻已是另一份（放弃后重新排的）：不发、不删、不标记
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False, own_account="u2", own_visit_uid=OTHER))
    assert await tu.submit_queued_report(V1, stale) is False
    assert fake.count("/api/visit/reports") == 0
    assert (await tu.load_report(tmp_path, V1))["own_account"] == "u2"


async def test_recovery_callback_deletes_under_the_visit_lock(tmp_path, servers):
    import asyncio

    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    doc = await tu.load_report(tmp_path, V1)
    lock = tu.visit_lock(V1)
    await lock.acquire()
    task = asyncio.create_task(tu.submit_queued_report(V1, doc))
    try:
        await asyncio.sleep(0.05)
        assert not task.done()                      # 等端点的放弃 / 重试先做完
    finally:
        lock.release()
    assert await asyncio.wait_for(task, 5) is True
    assert await tu.load_report(tmp_path, V1) is None


async def test_recovery_does_not_delete_a_report_queued_after_the_one_it_sent(tmp_path, servers, monkeypatch):
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False, queued_at=1000.0))

    async def accepted_then_replaced(visit_id, doc):
        # Servers 受理了这一份；补录随后删除之前，同一场又排进了另一份
        await tu.delete_report(tmp_path, visit_id)
        await tu.queue_report(tmp_path, _report_doc(include_transcript=False, queued_at=2000.0))
        return True

    await _recover(tmp_path, monkeypatch, submit=accepted_then_replaced)
    assert (await tu.load_report(tmp_path, V1))["queued_at"] == 2000.0


async def test_rejected_mark_only_lands_on_the_same_report(tmp_path, servers):
    first = _report_doc(include_transcript=False, queued_at=1000.0)
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False, queued_at=2000.0))
    await tu.set_report_rejected(tmp_path, V1, "unknown_visit", expect=first)
    assert "rejected" not in await tu.load_report(tmp_path, V1)


async def test_manual_retry_without_a_sent_request_keeps_the_rejection(tmp_path, servers):
    fake, state = servers
    fake.report_mode = "404"
    path = tmp_path / "visit_reports" / f"{V1}.json"
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    await tu.retry_visit_once(V1)
    state["account"] = None                         # 登录失效：请求根本没发出
    assert (await tu.retry_visit_once(V1, manual=True)).pending is False
    assert json.loads(path.read_text(encoding="utf-8"))["rejected"] == "unknown_visit"


async def test_anomaly_count_outlives_the_uploaded_transcript(tmp_path, servers):
    journal = await _journal(tmp_path)
    journal.note_anomaly(ts=1001.0)
    journal.note_anomaly(ts=1001.5)
    await journal.seal("wrap_up")
    assert await tu.visit_anomalies(tmp_path, V1) == 2
    await tu.retry_visit_once(V1)
    assert not (_spool(tmp_path) / f"{V1}.upload.json").exists()
    assert await tu.visit_anomalies(tmp_path, V1) == 2


async def test_queued_report_of_another_account_waits(tmp_path, servers):
    fake, state = servers
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    state["account"] = "u2"
    assert (await tu.retry_visit_once(V1)).pending is True and fake.count("/api/visit/reports") == 0


async def test_old_queued_reports_are_flagged_but_never_dropped(tmp_path, servers):
    fake, _ = servers
    fake.report_mode = "503"
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False, queued_at=time.time() - 8 * 86400))
    await tu.retry_visit_once(V1)
    rows = await tu.list_queued_reports(tmp_path, "u1")
    assert await tu.list_queued_reports(tmp_path, "u2") == []
    assert rows[0]["visit_id"] == V1 and rows[0]["stale"] is True
    assert (tmp_path / "visit_reports" / f"{V1}.json").exists()


async def test_unrecordable_rejection_keeps_the_upload_and_still_reaches_the_report(tmp_path, servers, monkeypatch):
    fake, _ = servers
    fake.transcript_mode = "parts"
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    await tu.queue_report(tmp_path, _report_doc())
    fake.report_mode = "503"

    def broken(*_a, **_k):
        raise OSError("disk full")

    original = tu._mark_unavailable_sync
    monkeypatch.setattr(tu, "_mark_unavailable_sync", broken)
    await tu.retry_visit_once(V1)
    # 原因写不进举报：上传文件留着并记上 rejected，不能删掉这唯一的持久记录
    assert sealed.exists() and json.loads(sealed.read_text(encoding="utf-8"))["rejected"] == "parts_out_of_range"
    uploads = fake.count("/api/visit/transcripts")
    fake.report_mode = "ok"
    assert (await tu.retry_visit_once(V1)).pending is False
    assert fake.reports[0]["transcript_unavailable"] == "parts_out_of_range"
    assert fake.count("/api/visit/transcripts") == uploads          # 已拒收的不再整份重传
    # 举报已受理：留着的拒收文件再没用处，当场删掉，不占待上传容量等到下次启动
    assert not sealed.exists()
    monkeypatch.setattr(tu, "_mark_unavailable_sync", original)


async def test_backlog_counts_pending_upload_bytes(tmp_path, servers, monkeypatch):
    _write_sealed(tmp_path, _big_doc(4, 10))
    assert await tu.upload_backlog_full(tmp_path) is False
    monkeypatch.setattr(tu, "VISIT_UPLOAD_PENDING_CAP_BYTES", 10)
    assert await tu.upload_backlog_full(tmp_path) is True


def test_chunk_bounds_follow_the_contract():
    n, parts = 7, 3
    spans = [tu.chunk_bounds(n, parts, k) for k in range(parts)]
    assert spans == [(0, 3), (3, 5), (5, 7)]
    assert spans[0][0] == 0 and spans[-1][1] == n



@pytest.mark.parametrize("mode", ["bogus204", "html200"])
async def test_an_uncontracted_2xx_is_not_a_receipt(tmp_path, servers, mode):
    fake, _ = servers
    fake.transcript_mode = mode
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    assert (await tu.retry_visit_once(V1)).pending is True
    assert sealed.exists()


@pytest.mark.parametrize("mode", ["no_parts", "complete_no_parts"])
async def test_chunk_receipts_without_accepted_parts_are_not_trusted(tmp_path, servers, mode):
    fake, _ = servers
    fake.transcript_mode = mode
    sealed = _write_sealed(tmp_path, _big_doc())        # >512 KiB：分块上传
    assert (await tu.retry_visit_once(V1)).pending is True
    doc = json.loads(sealed.read_text(encoding="utf-8"))
    assert not doc.get("accepted_parts")                 # 没替 Servers 认定任何块


async def test_a_report_200_without_its_receipt_stays_queued(tmp_path, servers):
    fake, _ = servers
    fake.report_mode = "bogus200"
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    assert (await tu.retry_visit_once(V1)).pending is True
    assert await tu.load_report(tmp_path, V1) is not None



async def test_a_rate_limited_report_waits_the_servers_delay(tmp_path, servers):
    fake, _ = servers
    fake.report_mode = "429"
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    outcome = await tu.retry_visit_once(V1)
    assert outcome.pending is True and outcome.retry_after_s == 5


async def test_a_round_reports_an_expired_login(tmp_path, servers, monkeypatch):
    async def expired():
        raise cr.VisitLoginRequired()

    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    monkeypatch.setattr(cr, "_servers_session", expired)
    assert (await tu.retry_visit_once(V1, manual=True)).login_required is True


async def test_a_scheduled_retry_after_an_attempt_waits_first(tmp_path, servers, monkeypatch):
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(tu, "_sleep", sleep)
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    await tu.schedule_visit_retry(V1, initial_delay_s=7)
    assert slept and slept[0] == 7



async def test_a_longer_delay_pushes_back_a_waiting_worker(tmp_path, servers, monkeypatch):
    fake, _ = servers
    fake.report_mode = "503"
    slept = []
    first_wait = asyncio.Event()

    async def sleep(seconds):
        slept.append(round(seconds))
        if len(slept) == 1:
            first_wait.set()
            await asyncio.sleep(0.05)               # 后台任务正在等第一段

    monkeypatch.setattr(tu, "_sleep", sleep)
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    task = tu.schedule_visit_retry(V1, initial_delay_s=10)
    await first_wait.wait()
    tu.schedule_visit_retry(V1, initial_delay_s=600)    # 手动重试拿到了更长的 retry_after
    fake.report_mode = "ok"
    await asyncio.wait_for(task, 5)
    assert slept[0] == 10 and slept[1] >= 590             # 先按旧的等，被推后后再按新的等完才重试
    assert fake.count("/api/visit/reports") == 1
