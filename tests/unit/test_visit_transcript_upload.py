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
    scheduled = []
    # 回调失败会排后台重试（另有测试覆盖）；这里只看补录本身，不让后台 worker 抢着上传
    monkeypatch.setattr(tu, "schedule_visit_retry", lambda visit_id, **_k: scheduled.append(visit_id))
    journal = await _journal(tmp_path)
    await _say(journal, 1)
    await journal.seal("wrap_up")
    state["account"] = "u2"
    assert await tu.upload_visit_transcript(V1, _sealed(tmp_path)) is False
    assert fake.count("/api/visit/transcripts") == 0 and scheduled == [V1]
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



async def test_all_parts_without_complete_is_not_done(tmp_path, servers):
    fake, _ = servers
    fake.transcript_mode = "never_complete"
    sealed = _write_sealed(tmp_path, _big_doc())
    assert (await tu.retry_visit_once(V1)).pending is True and sealed.exists()
    sent = fake.count("/api/visit/transcripts")
    fake.transcript_mode = "ok"
    assert (await tu.retry_visit_once(V1)).pending is False and not sealed.exists()
    # 落盘进度里全部块已受理：下次重试直接重发末块换 complete 回执，不重传整组
    assert fake.count("/api/visit/transcripts") == sent + 1



async def test_a_401_on_the_transcript_asks_to_sign_in_for_a_waiting_report(tmp_path, servers):
    fake, _ = servers
    fake.transcript_mode = "401"
    _write_sealed(tmp_path, _big_doc())
    await tu.queue_report(tmp_path, _report_doc(include_transcript=True))
    outcome = await tu.retry_visit_once(V1, manual=True)
    assert outcome.pending is True and outcome.login_required is True
    assert fake.count("/api/visit/reports") == 0


async def test_the_recovery_upload_waits_for_a_running_round(tmp_path, servers):
    fake, _ = servers
    sealed = _write_sealed(tmp_path, _big_doc())
    doc = json.loads(sealed.read_text(encoding="utf-8"))
    lock = tu.visit_lock(V1)
    await lock.acquire()
    try:
        task = asyncio.ensure_future(tu.upload_visit_transcript(V1, doc))
        await asyncio.sleep(0.05)
        assert not task.done() and fake.count("/api/visit/transcripts") == 0     # 等另一轮结束
    finally:
        lock.release()
    assert await asyncio.wait_for(task, 5) is True



async def test_a_manual_retry_leaves_another_accounts_report_alone(tmp_path, servers):
    fake, _ = servers
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False, own_account="u2"))
    outcome = await tu.retry_visit_once(V1, manual=True, owner="u1")
    assert fake.count("/api/visit/reports") == 0 and outcome.pending is False
    assert await tu.load_report(tmp_path, V1) is not None



async def test_a_worker_keeps_going_when_an_accepted_report_cannot_be_deleted(tmp_path, servers, monkeypatch):
    real_delete = tu.delete_report
    calls = []

    async def locked(config_dir, visit_id):
        calls.append(visit_id)
        if len(calls) == 1:
            raise PermissionError("file in use")
        return await real_delete(config_dir, visit_id)

    monkeypatch.setattr(tu, "delete_report", locked)
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    assert (await tu.retry_visit_once(V1)).pending is True       # 受理了但文件还在：不能让 worker 退出
    assert (await tu.retry_visit_once(V1)).pending is False
    assert await tu.load_report(tmp_path, V1) is None



async def test_an_uploaded_spool_that_cannot_be_deleted_keeps_the_round_going(tmp_path, servers, monkeypatch):
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    real_unlink = Path.unlink
    calls = []

    def busy(self, missing_ok=False):
        if self == sealed and not calls:
            calls.append(self)
            raise PermissionError("in use")
        return real_unlink(self, missing_ok)

    monkeypatch.setattr(Path, "unlink", busy)
    assert (await tu.retry_visit_once(V1)).pending is True and sealed.exists()
    assert (await tu.retry_visit_once(V1)).pending is False and not sealed.exists()



async def test_a_rejected_spool_that_cannot_be_deleted_is_cleaned_up_later(tmp_path, servers, monkeypatch):
    fake, _ = servers
    fake.transcript_mode = "parts"
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    real_unlink = Path.unlink
    calls = []

    def busy(self, missing_ok=False):
        if self == sealed and not calls:
            calls.append(self)
            raise PermissionError("in use")
        return real_unlink(self, missing_ok)

    monkeypatch.setattr(Path, "unlink", busy)
    assert (await tu.retry_visit_once(V1)).pending is True and sealed.exists()
    assert (await tu.retry_visit_once(V1)).pending is False and not sealed.exists()



async def test_a_settled_leftover_is_deleted_without_signing_in_again(tmp_path, servers, monkeypatch):
    fake, _ = servers
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    real_unlink = Path.unlink
    calls = []

    def busy(self, missing_ok=False):
        if self == sealed and not calls:
            calls.append(self)
            raise PermissionError("in use")
        return real_unlink(self, missing_ok)

    monkeypatch.setattr(Path, "unlink", busy)
    assert (await tu.retry_visit_once(V1)).pending is True and sealed.exists()
    uploads = fake.count("/api/visit/transcripts")

    async def signed_out():
        raise cr.VisitLoginRequired()

    monkeypatch.setattr(cr, "_servers_session", signed_out)          # 用户这时登出了
    assert (await tu.retry_visit_once(V1)).pending is False and not sealed.exists()
    assert fake.count("/api/visit/transcripts") == uploads             # 只删文件，不重传



async def test_an_expired_spool_that_cannot_be_deleted_is_cleaned_up_later(tmp_path, servers, monkeypatch):
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    old = time.time() - 30 * 86400
    os.utime(sealed, (old, old))
    real_unlink = Path.unlink
    calls = []

    def busy(self, missing_ok=False):
        if self == sealed and not calls:
            calls.append(self)
            raise PermissionError("in use")
        return real_unlink(self, missing_ok)

    monkeypatch.setattr(Path, "unlink", busy)
    assert (await tu.retry_visit_once(V1)).pending is True and sealed.exists()
    assert (await tu.retry_visit_once(V1)).pending is False and not sealed.exists()


async def test_a_sealed_file_of_another_version_is_not_uploaded_directly(tmp_path, servers):
    fake, _ = servers
    doc = _big_doc(4, 10)
    doc["v"] = 99
    sealed = _write_sealed(tmp_path, doc)
    outcome = await tu.attempt_upload(V1, config_dir=tmp_path)
    assert outcome.pending is True and outcome.retryable is False
    assert fake.count("/api/visit/transcripts") == 0 and sealed.exists()


async def test_a_transcript_free_report_goes_out_despite_upload_errors(tmp_path, servers, monkeypatch):
    fake, _ = servers
    _write_sealed(tmp_path, _big_doc(4, 10))
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))

    async def disk_full(*_a, **_k):
        raise OSError("no space left")

    monkeypatch.setattr(tu, "attempt_upload", disk_full)
    outcome = await tu.retry_visit_once(V1)
    assert fake.count("/api/visit/reports") == 1 and await tu.load_report(tmp_path, V1) is None
    assert outcome.pending is True                       # 转录还没传，worker 继续


async def test_an_unrecorded_rejection_keeps_the_round_pending(tmp_path, servers, monkeypatch):
    fake, _ = servers
    fake.report_mode = "404"
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    original = tu._set_rejected_sync
    calls = []

    def broken_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk full")
        return original(*args, **kwargs)

    monkeypatch.setattr(tu, "_set_rejected_sync", broken_once)
    assert (await tu.retry_visit_once(V1)).pending is True
    assert (await tu.retry_visit_once(V1)).pending is False
    assert (await tu.load_report(tmp_path, V1))["rejected"] == "unknown_visit"



async def test_an_unreadable_report_does_not_count_as_a_recorded_rejection(tmp_path, servers, monkeypatch):
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    original = tu._load_json

    def locked(path):
        if path.name == f"{V1}.json":
            raise PermissionError("in use")
        return original(path)

    monkeypatch.setattr(tu, "_load_json", locked)
    assert await tu.rejection_recorded(tmp_path, V1) is False
    monkeypatch.setattr(tu, "_load_json", original)
    assert await tu.rejection_recorded(tmp_path, V1) is False          # 读得到、但没有标记
    await tu.set_report_rejected(tmp_path, V1, "unknown_visit")
    assert await tu.rejection_recorded(tmp_path, V1) is True
    await tu.delete_report(tmp_path, V1)
    assert await tu.rejection_recorded(tmp_path, V1) is True           # 已不在队列



async def test_a_round_stays_pending_while_the_report_file_cannot_be_read(tmp_path, servers, monkeypatch):
    fake, _ = servers
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    original = tu._load_json
    locked = [True]

    def maybe_locked(path):
        if locked[0] and path.name == f"{V1}.json":
            raise PermissionError("in use")
        return original(path)

    monkeypatch.setattr(tu, "_load_json", maybe_locked)
    assert (await tu.retry_visit_once(V1)).pending is True and fake.count("/api/visit/reports") == 0
    locked[0] = False
    assert (await tu.retry_visit_once(V1)).pending is False and fake.count("/api/visit/reports") == 1



async def test_a_report_read_failure_keeps_the_round_pending_even_if_it_clears_at_once(tmp_path, servers, monkeypatch):
    fake, _ = servers
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    original = tu._load_json
    calls = []

    def flaky(path):
        if path.name == f"{V1}.json" and not calls:
            calls.append(path)
            raise PermissionError("in use")                # 只失败这一次
        return original(path)

    monkeypatch.setattr(tu, "_load_json", flaky)
    assert (await tu.retry_visit_once(V1)).pending is True
    assert (await tu.retry_visit_once(V1)).pending is False and fake.count("/api/visit/reports") == 1


async def test_a_residual_stream_goes_when_its_sealed_upload_settles(tmp_path, servers):
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    stream = sealed.with_name(f"{V1}.upload.jsonl")
    stream.write_text("{}\n", encoding="utf-8")         # 封存时没删掉的流水
    tu._settled_leftovers.discard(V1)
    await tu.attempt_upload(V1, config_dir=tmp_path)
    assert not sealed.exists() and not stream.exists()


async def test_a_later_report_carries_a_terminal_reason_settled_before_it(tmp_path, servers):
    fake, _ = servers
    fake.transcript_mode = "parts"
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    assert (await tu.attempt_upload(V1, config_dir=tmp_path)).pending is False and not sealed.exists()
    fake.transcript_mode = "ok"
    await tu.queue_report(tmp_path, _report_doc(include_transcript=True))
    assert (await tu.retry_visit_once(V1)).pending is False
    assert fake.reports[0]["transcript_unavailable"] == "parts_out_of_range"



async def test_an_unreadable_file_after_acceptance_keeps_the_round_pending(tmp_path, servers, monkeypatch):
    fake, _ = servers
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    original_load, original_delete = tu._load_json, tu.delete_report
    state = {"reads": 0, "deletes": 0}

    def load(path):
        if path.name == f"{V1}.json":
            state["reads"] += 1
            if state["reads"] == 2:                        # 受理后核对时恰好读不了
                raise PermissionError("in use")
        return original_load(path)

    async def delete(config_dir, visit_id):
        state["deletes"] += 1
        if state["deletes"] == 1:
            raise PermissionError("in use")
        return await original_delete(config_dir, visit_id)

    monkeypatch.setattr(tu, "_load_json", load)
    monkeypatch.setattr(tu, "delete_report", delete)
    assert (await tu.retry_visit_once(V1)).pending is True
    assert (await tu.retry_visit_once(V1)).pending is False and await tu.load_report(tmp_path, V1) is None



async def test_an_orphan_stream_is_resealed_and_uploaded_by_a_retry_round(tmp_path, servers, monkeypatch):
    fake, _ = servers
    journal = await _journal(tmp_path)
    await _say(journal, 1, "first")
    real_write = tu._write_private_json

    def failing(path, doc):
        if Path(path).name.endswith(".upload.json"):
            raise OSError("disk full")
        return real_write(path, doc)

    monkeypatch.setattr(tu, "_write_private_json", failing)
    with pytest.raises(OSError):
        await journal.seal("wrap_up", ended_at=1002.0)
    monkeypatch.setattr(tu, "_write_private_json", real_write)
    stream = _spool(tmp_path) / f"{V1}.upload.jsonl"
    assert stream.exists() and not (_spool(tmp_path) / f"{V1}.upload.json").exists()
    await tu.queue_report(tmp_path, _report_doc(include_transcript=True))
    assert (await tu.retry_visit_once(V1)).pending is False
    assert fake.count("/api/visit/transcripts") >= 1 and fake.count("/api/visit/reports") == 1
    assert not stream.exists()


async def test_a_recovery_report_failure_re_arms_the_background_worker(tmp_path, servers):
    fake, _ = servers
    fake.report_mode = "503"
    doc = _report_doc(include_transcript=False)
    await tu.queue_report(tmp_path, doc)
    assert await tu.submit_queued_report(V1, doc) is False
    assert V1 in tu._workers



async def test_a_stream_still_being_written_is_never_resealed(tmp_path, servers):
    fake, _ = servers
    journal = await _journal(tmp_path)                    # is_live 未接线（默认 False）也不能重封
    await _say(journal, 1, "first")
    await tu.queue_report(tmp_path, _report_doc(include_transcript=True))
    outcome = await tu.attempt_upload(V1, config_dir=tmp_path)
    stream = _spool(tmp_path) / f"{V1}.upload.jsonl"
    assert outcome.pending is True and stream.exists()
    assert not (_spool(tmp_path) / f"{V1}.upload.json").exists() and fake.count("/api/visit/transcripts") == 0
    await journal.seal("wrap_up", ended_at=1002.0)



async def test_a_stream_is_registered_as_open_before_it_appears_on_disk(tmp_path, servers, monkeypatch):
    seen = []
    real_open = tu.UploadJournal._open_sync

    def watch(self, data):
        seen.append(self.visit_id in tu._open_streams)    # 建文件那一刻已登记
        return real_open(self, data)

    monkeypatch.setattr(tu.UploadJournal, "_open_sync", watch)
    journal = await _journal(tmp_path)
    assert seen == [True] and V1 in tu._open_streams
    await journal.seal("wrap_up", ended_at=1001.0)
    assert V1 not in tu._open_streams

    def broken(self, data):
        raise OSError("disk full")

    monkeypatch.setattr(tu.UploadJournal, "_open_sync", broken)
    with pytest.raises(OSError):
        await _journal(tmp_path, vid(2))
    assert vid(2) not in tu._open_streams                # 没打开成功就撤销登记



async def test_a_recovery_upload_failure_re_arms_the_background_worker(tmp_path, servers):
    fake, _ = servers
    fake.transcript_mode = "503"
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    doc = json.loads(sealed.read_text(encoding="utf-8"))
    assert await tu.upload_visit_transcript(V1, doc) is False
    assert V1 in tu._workers


async def test_a_cancelled_open_cleans_up_after_the_worker(tmp_path, servers, monkeypatch):
    import threading

    started, release = threading.Event(), threading.Event()
    real_open = tu.UploadJournal._open_sync

    def slow(self, data):
        started.set()
        release.wait(5)
        return real_open(self, data)

    monkeypatch.setattr(tu.UploadJournal, "_open_sync", slow)
    task = asyncio.ensure_future(_journal(tmp_path))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0.05)
    assert V1 in tu._open_streams                         # 线程还在建文件：登记不能先撤
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert V1 not in tu._open_streams
    assert not (_spool(tmp_path) / f"{V1}.upload.jsonl").exists()



async def test_a_second_cancel_while_waiting_still_cleans_up(tmp_path, servers, monkeypatch):
    import threading

    started, release = threading.Event(), threading.Event()
    real_open = tu.UploadJournal._open_sync

    def slow(self, data):
        started.set()
        release.wait(5)
        return real_open(self, data)

    monkeypatch.setattr(tu.UploadJournal, "_open_sync", slow)
    task = asyncio.ensure_future(_journal(tmp_path))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0.05)
    task.cancel()                                          # 等线程期间又被取消一次
    await asyncio.sleep(0.05)
    assert V1 in tu._open_streams
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert V1 not in tu._open_streams
    assert not (_spool(tmp_path) / f"{V1}.upload.jsonl").exists()



async def test_a_rejected_spool_left_after_acceptance_is_cleaned_up_later(tmp_path, servers, monkeypatch):
    fake, _ = servers
    scheduled = []
    # 只看轮次本身：不起后台 worker（夹具里等待是空操作，它会抢在断言前把文件删掉）
    monkeypatch.setattr(tu, "schedule_visit_retry", lambda visit_id, **_k: scheduled.append(visit_id))
    fake.transcript_mode = "parts"
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    await tu.queue_report(tmp_path, _report_doc())
    original_mark = tu._mark_unavailable_sync

    def broken(*_a, **_k):
        raise OSError("disk full")                    # 原因记不进举报：封存文件带 rejected 留着

    monkeypatch.setattr(tu, "_mark_unavailable_sync", broken)
    real_unlink = Path.unlink
    calls = []

    def busy(self, missing_ok=False):
        if self == sealed and not calls:
            calls.append(self)
            raise PermissionError("in use")
        return real_unlink(self, missing_ok)

    monkeypatch.setattr(Path, "unlink", busy)
    outcome = await tu.retry_visit_once(V1)
    assert await tu.load_report(tmp_path, V1) is None and sealed.exists() and outcome.pending is True
    assert scheduled == [V1]                                # 也排上了后台清理
    monkeypatch.setattr(tu, "_mark_unavailable_sync", original_mark)
    assert (await tu.retry_visit_once(V1)).pending is False and not sealed.exists()



async def test_a_manual_retry_keeps_going_when_the_rejection_marker_cannot_be_cleared(tmp_path, servers, monkeypatch):
    fake, _ = servers
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    await tu.set_report_rejected(tmp_path, V1, "unknown_visit")
    fake.report_mode = "503"
    original = tu._set_rejected_sync
    calls = []

    def broken_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk full")
        return original(*args, **kwargs)

    monkeypatch.setattr(tu, "_set_rejected_sync", broken_once)
    assert (await tu.retry_visit_once(V1, manual=True)).pending is True
    assert (await tu.load_report(tmp_path, V1))["rejected"] == "unknown_visit"     # 标记没清掉
    fake.report_mode = "ok"
    assert (await tu.retry_visit_once(V1)).pending is False                      # 后台轮次照样提交
    assert await tu.load_report(tmp_path, V1) is None


async def test_a_recovered_owner_is_written_before_the_upload_is_deferred(tmp_path, servers):
    fake, state = servers
    doc = _big_doc(4, 10)
    doc.pop("own_visit_uid")                             # 老版本文件：没记归属
    sealed = _write_sealed(tmp_path, doc)
    state["account"] = "u2"                               # 现在登录的是别的账号
    assert await tu.upload_visit_transcript(V1, {**doc, "own_visit_uid": OWN}) is False
    assert json.loads(sealed.read_text(encoding="utf-8"))["own_visit_uid"] == OWN
    state["account"] = "u1"
    assert (await tu.retry_visit_once(V1)).pending is False and not sealed.exists()



async def test_a_pending_manual_retry_ends_when_servers_rejects_again(tmp_path, servers, monkeypatch):
    fake, _ = servers
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    await tu.set_report_rejected(tmp_path, V1, "unknown_visit")
    fake.report_mode = "503"
    original = tu._set_rejected_sync
    calls = []

    def broken_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk full")
        return original(*args, **kwargs)

    monkeypatch.setattr(tu, "_set_rejected_sync", broken_once)
    await tu.retry_visit_once(V1, manual=True)
    assert V1 in tu._manual_retries
    fake.report_mode = "404"                               # 后台那一轮又被拒
    await tu.retry_visit_once(V1)
    assert V1 not in tu._manual_retries
    sent = fake.count("/api/visit/reports")
    assert (await tu.retry_visit_once(V1)).pending is False   # 不再自动重提，等用户决定
    assert fake.count("/api/visit/reports") == sent


async def test_a_pending_manual_retry_does_not_carry_over_to_a_new_report(tmp_path, servers):
    fake, _ = servers
    old = _report_doc(include_transcript=False, queued_at=1.0)
    await tu.queue_report(tmp_path, old)
    tu._manual_retries[V1] = dict(old)
    await tu.delete_report(tmp_path, V1)                    # 用户放弃了那一份
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False, queued_at=2.0))
    await tu.set_report_rejected(tmp_path, V1, "unknown_visit")
    assert (await tu.retry_visit_once(V1)).pending is False
    assert fake.count("/api/visit/reports") == 0 and V1 not in tu._manual_retries



async def test_a_pending_manual_retry_survives_an_unreadable_round(tmp_path, servers, monkeypatch):
    doc = _report_doc(include_transcript=False)
    await tu.queue_report(tmp_path, doc)
    tu._manual_retries[V1] = dict(doc)
    real_read = tu._read_report

    async def unreadable(_config_dir, _visit_id):
        return None, True

    monkeypatch.setattr(tu, "_read_report", unreadable)
    assert (await tu.retry_visit_once(V1)).pending is True
    assert V1 in tu._manual_retries                         # 读不了时不作废
    monkeypatch.setattr(tu, "_read_report", real_read)



async def test_a_manual_retry_that_cannot_read_the_report_is_remembered(tmp_path, servers, monkeypatch):
    fake, _ = servers
    doc = _report_doc(include_transcript=False)
    await tu.queue_report(tmp_path, doc)
    await tu.set_report_rejected(tmp_path, V1, "unknown_visit")
    real_read = tu._read_report
    locked = [True]

    async def maybe_unreadable(config_dir, visit_id):
        if locked[0]:
            return None, True
        return await real_read(config_dir, visit_id)

    monkeypatch.setattr(tu, "_read_report", maybe_unreadable)
    assert (await tu.retry_visit_once(V1, manual=True, owner="u1", expect=doc)).pending is True
    locked[0] = False
    assert (await tu.retry_visit_once(V1)).pending is False          # 后台照手动重试提交
    assert fake.count("/api/visit/reports") == 1 and await tu.load_report(tmp_path, V1) is None



async def test_accepting_a_report_drops_the_rejected_spool_and_its_residual_stream(tmp_path, servers, monkeypatch):
    fake, _ = servers
    fake.transcript_mode = "parts"
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    stream = sealed.with_name(f"{V1}.upload.jsonl")
    await tu.queue_report(tmp_path, _report_doc())

    def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(tu, "_mark_unavailable_sync", broken)
    monkeypatch.setattr(tu, "schedule_visit_retry", lambda *_a, **_k: None)
    fake.report_mode = "503"
    await tu.retry_visit_once(V1)                         # 拒收：原因记不进举报，封存文件带 rejected 留着
    stream.write_text("{}\n", encoding="utf-8")          # 封存时没删掉的流水
    fake.report_mode = "ok"
    assert (await tu.retry_visit_once(V1)).pending is False
    assert not sealed.exists() and not stream.exists()


async def test_an_inherited_terminal_reason_is_written_into_the_queued_report(tmp_path, servers, monkeypatch):
    fake, _ = servers
    fake.transcript_mode = "parts"
    _write_sealed(tmp_path, _big_doc(4, 10))
    await tu.attempt_upload(V1, config_dir=tmp_path)       # 转录先于举报结清
    fake.report_mode = "503"
    monkeypatch.setattr(tu, "schedule_visit_retry", lambda *_a, **_k: None)
    await tu.queue_report(tmp_path, _report_doc(include_transcript=True))
    await tu.retry_visit_once(V1)
    assert (await tu.load_report(tmp_path, V1))["transcript_unavailable"] == "parts_out_of_range"


async def test_a_recovery_rejection_marker_failure_re_arms_the_worker(tmp_path, servers, monkeypatch):
    fake, _ = servers
    fake.report_mode = "404"
    doc = _report_doc(include_transcript=False)
    await tu.queue_report(tmp_path, doc)
    scheduled = []
    monkeypatch.setattr(tu, "schedule_visit_retry", lambda visit_id, **_k: scheduled.append(visit_id))

    def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(tu, "_set_rejected_sync", broken)
    assert await tu.submit_queued_report(V1, doc) is False
    assert scheduled == [V1]



async def test_the_rejected_marker_outlives_a_stream_that_cannot_be_deleted(tmp_path, servers, monkeypatch):
    spool = _spool(tmp_path)
    spool.mkdir(parents=True, exist_ok=True)
    sealed = spool / f"{V1}.upload.json"
    stream = spool / f"{V1}.upload.jsonl"
    sealed.write_text(json.dumps({"rejected": "parts_out_of_range"}), encoding="utf-8")
    stream.write_text("{}" + chr(10), encoding="utf-8")
    real_unlink = Path.unlink

    def busy(self, missing_ok=False):
        if self == stream:
            raise PermissionError("in use")
        return real_unlink(self, missing_ok)

    monkeypatch.setattr(Path, "unlink", busy)
    assert tu._drop_rejected_sealed_sync(tmp_path, V1) is False
    assert sealed.exists() and json.loads(sealed.read_text(encoding="utf-8"))["rejected"]   # 标记还在


async def test_an_unmarked_terminal_spool_is_dropped_once_the_report_is_accepted(tmp_path, servers, monkeypatch):
    fake, _ = servers
    fake.transcript_mode = "parts"
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    await tu.queue_report(tmp_path, _report_doc())

    def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(tu, "_mark_unavailable_sync", broken)      # 原因写不进举报
    monkeypatch.setattr(tu, "_mark_sealed_rejected_sync", broken)  # 封存文件上的标记也写不成
    assert (await tu.retry_visit_once(V1)).pending is False
    assert fake.reports[0]["transcript_unavailable"] == "parts_out_of_range"
    assert not sealed.exists()                                      # 进程内知道它已终态：照样清掉


async def test_another_accounts_transcript_does_not_gate_a_report(tmp_path, servers):
    fake, _ = servers
    doc = _big_doc(4, 10)
    doc["own_visit_uid"] = "b" * 24                                 # 共用电脑：这份转录是另一账号那一侧的
    _write_sealed(tmp_path, doc)
    await tu.queue_report(tmp_path, _report_doc(include_transcript=True))
    await tu.retry_visit_once(V1)
    assert fake.count("/api/visit/reports") == 1 and "transcript_unavailable" not in fake.reports[0]


async def test_an_aged_transcript_is_tried_once_before_it_expires(tmp_path, servers):
    fake, _ = servers
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    old = time.time() - 30 * 86400
    os.utime(sealed, (old, old))
    outcome = await tu.attempt_upload(V1, config_dir=tmp_path)
    assert outcome.pending is False and outcome.unavailable is None    # 试传成功，没有按过期丢掉
    assert fake.count("/api/visit/transcripts") >= 1 and not sealed.exists()


async def test_an_aged_transcript_that_still_fails_expires(tmp_path, servers):
    fake, _ = servers
    fake.transcript_mode = "503"
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    old = time.time() - 30 * 86400
    os.utime(sealed, (old, old))
    await tu.attempt_upload(V1, config_dir=tmp_path)
    assert fake.count("/api/visit/transcripts") >= 1 and not sealed.exists()   # 试过一次仍失败：按过期结清


async def test_a_terminal_record_never_drops_another_accounts_transcript(tmp_path, servers, monkeypatch):
    fake, _ = servers
    doc = _big_doc(4, 10)
    doc["own_visit_uid"] = "b" * 24                         # 另一账号那一侧、尚未上传的转录
    sealed = _write_sealed(tmp_path, doc)
    tu.remember_terminal_reason(V1, "parts_out_of_range", OWN)  # 本账号那一侧早先终态结清
    monkeypatch.setattr(tu, "schedule_visit_retry", lambda *_a, **_k: None)
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False))
    await tu.retry_visit_once(V1)
    assert fake.count("/api/visit/reports") == 1 and sealed.exists()



async def test_another_accounts_report_is_not_marked_with_this_transcripts_reason(tmp_path, servers):
    fake, _ = servers
    fake.transcript_mode = "parts"
    _write_sealed(tmp_path, _big_doc(4, 10))                       # 本账号（OWN）这一侧的转录
    await tu.queue_report(tmp_path, _report_doc(include_transcript=True, own_visit_uid="b" * 24, own_account="u2"))
    await tu.attempt_upload(V1, config_dir=tmp_path)
    assert "transcript_unavailable" not in (await tu.load_report(tmp_path, V1))


async def test_a_recovery_success_still_arms_a_cleanup_check(tmp_path, servers, monkeypatch):
    sealed = _write_sealed(tmp_path, _big_doc(4, 10))
    scheduled = []
    monkeypatch.setattr(tu, "schedule_visit_retry", lambda visit_id, **_k: scheduled.append(visit_id))
    assert await tu.upload_visit_transcript(V1, json.loads(sealed.read_text(encoding="utf-8"))) is True
    assert scheduled == [V1]


async def test_a_recovered_owner_that_cannot_be_written_is_kept_for_the_worker(tmp_path, servers, monkeypatch):
    fake, state = servers
    doc = _big_doc(4, 10)
    doc.pop("own_visit_uid")
    sealed = _write_sealed(tmp_path, doc)
    monkeypatch.setattr(tu, "schedule_visit_retry", lambda *_a, **_k: None)
    real_persist = tu._persist_owner_sync
    calls = []

    def flaky(path, owner):
        calls.append(owner)
        if len(calls) == 1:
            raise OSError("disk full")
        return real_persist(path, owner)

    monkeypatch.setattr(tu, "_persist_owner_sync", flaky)
    state["account"] = "u2"
    assert await tu.upload_visit_transcript(V1, {**doc, "own_visit_uid": OWN}) is False
    assert V1 in tu._pending_owners
    state["account"] = "u1"
    assert (await tu.retry_visit_once(V1)).pending is False and not sealed.exists()
    assert V1 not in tu._pending_owners


async def test_a_report_with_an_unknown_owner_still_gets_the_reason(tmp_path, servers):
    fake, _ = servers
    fake.transcript_mode = "parts"
    _write_sealed(tmp_path, _big_doc(4, 10))
    await tu.queue_report(tmp_path, _report_doc(include_transcript=True, own_visit_uid=None))
    outcome = await tu.attempt_upload(V1, config_dir=tmp_path)
    report = await tu.load_report(tmp_path, V1)
    assert report["transcript_unavailable"] == "parts_out_of_range"
    assert outcome.for_report(report).pending is False and outcome.for_report(report) is outcome



def test_an_unknown_owner_never_counts_as_the_same_owner(tmp_path):
    spool = _spool(tmp_path)
    spool.mkdir(parents=True, exist_ok=True)
    sealed = spool / f"{V1}.upload.json"
    sealed.write_text(json.dumps({"v": 1, "request": {}}), encoding="utf-8")     # 老文件：无归属、无拒收标记
    assert tu._drop_rejected_sealed_sync(tmp_path, V1, ("corrupt", None)) is True
    assert sealed.exists()



async def test_a_manual_retry_does_not_submit_a_replacement_report(tmp_path, servers):
    fake, _ = servers
    clicked = _report_doc(include_transcript=False, queued_at=1.0)
    await tu.queue_report(tmp_path, _report_doc(include_transcript=False, queued_at=2.0))    # 已被换成新的一份
    await tu.retry_visit_once(V1, manual=True, owner="u1", expect=clicked)
    assert fake.count("/api/visit/reports") == 0


async def test_a_corrupt_orphan_stream_keeps_its_owner(tmp_path, servers, monkeypatch):
    async def corrupt(_config_dir, _visit_id):
        return "corrupt", OWN

    monkeypatch.setattr(tu, "reseal_orphan_stream", corrupt)
    stream = _spool(tmp_path) / f"{V1}.upload.jsonl"
    stream.parent.mkdir(parents=True, exist_ok=True)
    stream.write_text("garbage", encoding="utf-8")
    await tu.queue_report(tmp_path, _report_doc(include_transcript=True, own_visit_uid="b" * 24, own_account="u2"))
    outcome = await tu.attempt_upload(V1, config_dir=tmp_path)
    assert outcome.owner == OWN and tu._terminal_reasons[V1] == ("corrupt", OWN)
    assert "transcript_unavailable" not in (await tu.load_report(tmp_path, V1))   # 另一账号的举报不被记上


async def test_an_aged_broken_sealed_file_is_resealed_from_its_stream_first(tmp_path, servers, monkeypatch):
    fake, _ = servers
    sealed = _spool(tmp_path) / f"{V1}.upload.json"
    sealed.parent.mkdir(parents=True, exist_ok=True)
    sealed.write_text("{broken", encoding="utf-8")
    old = time.time() - 30 * 86400
    os.utime(sealed, (old, old))
    (_spool(tmp_path) / f"{V1}.upload.jsonl").write_text("{}", encoding="utf-8")
    good = _big_doc(4, 10)

    async def reseal(_config_dir, _visit_id):
        sealed.write_text(json.dumps(good), encoding="utf-8")
        os.utime(sealed, (old, old))
        return "sealed", OWN

    monkeypatch.setattr(tu, "reseal_orphan_stream", reseal)
    outcome = await tu.attempt_upload(V1, config_dir=tmp_path)
    assert fake.count("/api/visit/transcripts") >= 1 and outcome.unavailable is None   # 重封后试传，没按过期丢掉



async def test_a_failed_reseal_keeps_an_aged_broken_file_and_its_stream(tmp_path, servers, monkeypatch):
    sealed = _spool(tmp_path) / f"{V1}.upload.json"
    sealed.parent.mkdir(parents=True, exist_ok=True)
    sealed.write_text("{broken", encoding="utf-8")
    old = time.time() - 30 * 86400
    os.utime(sealed, (old, old))
    stream = _spool(tmp_path) / f"{V1}.upload.jsonl"
    stream.write_text("{}", encoding="utf-8")

    async def failed(_config_dir, _visit_id):
        return "failed", None

    monkeypatch.setattr(tu, "reseal_orphan_stream", failed)
    outcome = await tu.attempt_upload(V1, config_dir=tmp_path)
    assert outcome.pending is True and outcome.retryable is True
    assert sealed.exists() and stream.exists()             # 没有按过期删掉



async def test_anomaly_counts_of_another_accounts_side_are_not_used(tmp_path, servers):
    doc = _big_doc(4, 10)
    doc["request"]["anomalies"] = 3
    _write_sealed(tmp_path, doc)                               # OWN 那一侧的转录
    assert await tu.visit_anomalies(tmp_path, V1, OWN) == 3
    assert await tu.visit_anomalies(tmp_path, V1, "b" * 24) == 0
    assert await tu.visit_anomalies(tmp_path, V1, None) == 3   # 举报归属未知：照用
    tu.remember_anomalies(vid(2), 5, OWN)
    assert await tu.visit_anomalies(tmp_path, vid(2), "b" * 24) == 0
    assert await tu.visit_anomalies(tmp_path, vid(2), OWN) == 5



async def test_a_transient_sealed_read_failure_stays_retryable(tmp_path, servers, monkeypatch):
    _write_sealed(tmp_path, _big_doc(4, 10))
    original = tu._load_json

    def locked(path):
        if path.name.endswith(".upload.json"):
            raise PermissionError("in use")
        return original(path)

    monkeypatch.setattr(tu, "_load_json", locked)
    outcome = await tu.attempt_upload(V1, config_dir=tmp_path)
    assert outcome.pending is True and outcome.retryable is True


async def test_a_failed_reseal_keeps_the_owner_from_the_stream(tmp_path, servers, monkeypatch):
    sealed = _spool(tmp_path) / f"{V1}.upload.json"
    sealed.parent.mkdir(parents=True, exist_ok=True)
    sealed.write_text("{broken", encoding="utf-8")
    old = time.time() - 30 * 86400
    os.utime(sealed, (old, old))
    (_spool(tmp_path) / f"{V1}.upload.jsonl").write_text("{}", encoding="utf-8")

    async def failed(_config_dir, _visit_id):
        return "failed", OWN

    monkeypatch.setattr(tu, "reseal_orphan_stream", failed)
    assert (await tu.attempt_upload(V1, config_dir=tmp_path)).owner == OWN


async def test_a_deeply_nested_stream_header_does_not_break_the_anomaly_count(tmp_path, servers):
    stream = _spool(tmp_path) / f"{V1}.upload.jsonl"
    stream.parent.mkdir(parents=True, exist_ok=True)
    stream.write_text("[" * 100000 + "\n" + '{"kind":"anomaly"}\n', encoding="utf-8")
    assert await tu.visit_anomalies(tmp_path, V1, OWN) == 1
