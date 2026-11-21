# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0

import asyncio

import pytest

from nekoclaw.host import call_audit
from nekoclaw.host.execution_audit import ExecutionAudit
from nekoclaw.host.ledger import Ledger


@pytest.mark.asyncio
async def test_parallel_receipts_are_bound_to_their_commission_and_session(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    audit = ExecutionAudit(ledger)
    try:
        work, _ = await ledger.create(lanlan_name="Lan", title="test", dispatch_text="test", source="dispatch")

        async def permit(record):
            return {"allow": True}

        async def deliver(record):
            async with call_audit.call(ledger, work.id, "transfer_content", "session:receiver"):
                return True

        def start(target):
            return audit.begin(work.id, "session-a", "runtime", target, permit, deliver, lambda: True)

        first, second = await asyncio.gather(start("read"), start("grep"))
        assert not await audit.finish(work.id, "session-a", first["receipt"], "completed", kind="dsh_native")
        assert not await audit.finish(work.id, "session-b", first["receipt"], "completed", kind="runtime")
        assert not await audit.finish("another-commission", "session-a", second["receipt"], "completed", kind="runtime")
        assert await audit.finish(work.id, "session-a", second["receipt"], "completed", kind="runtime")
        assert not await audit.finish(work.id, "session-a", second["receipt"], "completed", kind="runtime")
        assert not await audit.finish(work.id, "session-a", first["receipt"], "cancelled", kind="runtime")
        rows = await ledger.run(lambda db: call_audit.read_trace(db, work.id, now=ledger._clock()))
        assert [(r["target"], r["status"]) for r in rows if r["kind"] == "runtime"] == [
            ("read", "cancelled"), ("grep", "completed")
        ]
        assert [r["parent_call_id"] for r in rows if r["kind"] == "transfer_content"] == [second["receipt"]]
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_capacity_precedes_permission_and_run_end_prevents_delivery(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "ledger.db")
    audit = ExecutionAudit(ledger)
    permitted = []
    delivered = []

    async def permit(record):
        permitted.append(record.call_id)
        return {"allow": True}

    async def deliver(record):
        delivered.append(record.call_id)
        return True

    try:
        work, _ = await ledger.create(lanlan_name="Lan", title="fixture", dispatch_text="", source="dispatch")
        reply = await audit.begin(work.id, "session", "runtime", "read", permit, deliver, lambda: True)
        await audit.end_run(work.id)
        assert not await audit.finish(work.id, "session", reply["receipt"], "completed", kind="runtime")
        rows = await ledger.run(lambda db: call_audit.read_trace(db, work.id, now=ledger._clock()))
        assert rows[-1]["status"] == "cancelled"
        monkeypatch.setattr(call_audit, "BUDGET_BYTES", call_audit.RECORD_BYTES)
        with pytest.raises(call_audit.AuditFull):
            await audit.begin(work.id, "session", "runtime", "read", permit, deliver, lambda: True)
        assert permitted == [reply["receipt"]] and not delivered
    finally:
        await ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("host_error", [False, True])
async def test_blocked_result_keeps_error_text_and_progress_in_the_executor(tmp_path, host_error):
    from nekoclaw.runtime.execution import managed_tool
    from nekoclaw.runtime.host import HostCallbacks
    from nekoclaw.runtime.tools.base import ToolError, define_tool

    messages = []
    progress = []

    async def action(session, payload):
        messages.append(payload)
        if payload["phase"] == "begin":
            return {"allow": True, "receipt": "fixture"}
        if host_error and payload["phase"] == "finish":
            raise RuntimeError("private host diagnostic")
        return {"allow": False}

    async def execute(arguments, call):
        call.progress("secret-progress")
        raise ToolError("secret-error")

    schema = {"name": "bash", "description": "fixture", "parameters": {"type": "object", "properties": {}}}
    tool = managed_tool(define_tool(schema, execute), HostCallbacks(execution_action=action), "s", tmp_path)
    with pytest.raises(ToolError, match="could not be delivered") as error:
        await tool.execute("id", {}, None, progress.append)
    assert str(error.value) == "The tool result could not be delivered."
    assert "secret" not in str(error.value) and "secret" not in str(messages)
    assert not progress
    assert messages[-1] == {"phase": "finish", "receipt": "fixture", "status": "failed"}


def test_late_spill_writer_cannot_create_a_plain_file_after_close(tmp_path):
    from nekoclaw.runtime.tools.base import SpillStore
    from nekoclaw.runtime.tools.procs import OutputBuffer
    from nekoclaw.runtime.tools.protected_outputs import OutputScope

    store = SpillStore(tmp_path)
    reference = store.reserve("stdout", scope=OutputScope("receipt"))
    assert reference is not None
    store.close()
    buffer = OutputBuffer(1, lambda: reference, protected=store.protected)
    buffer.feed(b"fixture long output")
    buffer.close()
    assert not reference.exists()
    assert not list(tmp_path.iterdir())


def test_private_outputs_share_one_file_and_keep_interleaved_bytes_separate(tmp_path, monkeypatch):
    from nekoclaw.runtime.tools.base import SpillStore
    from nekoclaw.runtime.tools.protected_outputs import OutputScope

    monkeypatch.chdir(tmp_path)
    store = SpillStore(type(tmp_path)("out"))
    try:
        outputs = []
        for n in range(100):
            key = store.reserve("data", scope=OutputScope(str(n)))
            assert key.is_absolute()
            output = store.protected[key]
            output.write(f"start-{n}".encode())
            outputs.append(output)
        for n, output in enumerate(outputs):
            output.write(f"-end-{n}".encode())
        assert len({id(output.spool.file) for output in outputs}) == 1
        for n, output in enumerate(outputs):
            with output.reader() as reader:
                expected = f"start-{n}-end-{n}".encode()
                assert reader.peek(len(expected)) == expected
                assert reader.read() == expected
    finally:
        store.close()


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32", "gbk"])
def test_oversize_encoded_text_is_searchable_by_keyword(tmp_path, encoding):
    import threading
    import time
    import regex
    from nekoclaw.runtime.tools import fs, search
    from nekoclaw.runtime.tools.base import ToolError

    document = tmp_path / "source.txt"
    content = "header\n" * 2000 + "会议纪要：这是保存的会议记录。\n" * 8000 + "needle 议定\n"
    document.write_text(content, encoding=encoding)
    with pytest.raises(ToolError, match="Use grep"):
        fs.read_file(document, offset=1, limit=1)
    matches = search.grep_files(document, regex.compile("议定"), None, tmp_path,
                                search._Budget(threading.Event(), time.monotonic() + 15),
                                case_insensitive_globs=False)
    assert len(matches) == 1 and matches[0].line == "needle 议定"


def test_long_combined_question_includes_all_choices():
    from nekoclaw.host.reports import render_question
    from config.nekoclaw_settings import QUESTION_TEXT_MAX_TOKENS
    from utils.tokenize import count_tokens

    question = {"kind": "permission", "category": "outside_read", "options": ["once", "session", "deny"],
                "detail": {"tool": "read", "path": "path/" * 900, "content_to": "model/" * 1000}}
    rendered = render_question("work " * 600, question, "en")
    short = render_question("work", {**question, "detail": {"path": "file", "content_to": "model"}}, "en")
    assert count_tokens(rendered) <= QUESTION_TEXT_MAX_TOKENS
    assert rendered.endswith(short.split("model")[-1])


def test_utf8_code_point_split_across_grep_chunks_keeps_later_matches(tmp_path, monkeypatch):
    import threading
    import time
    import regex
    from nekoclaw.runtime.tools import search

    limit = search.GREP_LINE_MAX_BYTES
    document = tmp_path / "minutes.txt"
    document.write_bytes(b"a" * limit + "猫咪\n批准议定\n".encode())
    attempts = []
    def detect(sample):
        attempts.append(True)
        return "gbk"
    monkeypatch.setattr(search, "detect_legacy_encoding", detect)
    matches = search.grep_files(document, regex.compile("议定"), None, tmp_path,
                                search._Budget(threading.Event(), time.monotonic() + 15),
                                case_insensitive_globs=False)
    assert not attempts and [m.line for m in matches] == ["批准议定"]


def test_bad_byte_in_large_utf8_log_preserves_later_utf8_lines(tmp_path, monkeypatch):
    import threading
    import time
    import regex
    from nekoclaw.runtime.tools import search

    document = tmp_path / "log.txt"
    document.write_bytes("header\n".encode() * 9000 + b"division \x92\n" + "café résumé\n".encode())
    monkeypatch.setattr(search, "detect_legacy_encoding", lambda sample: "cp1252")
    matches = search.grep_files(document, regex.compile("café"), None, tmp_path,
                                search._Budget(threading.Event(), time.monotonic() + 15),
                                case_insensitive_globs=False)
    assert [m.line for m in matches] == ["café résumé"]


def test_established_gbk_is_used_for_a_line_also_valid_as_utf8(tmp_path):
    import threading
    import time
    import regex
    from nekoclaw.runtime.tools import search

    document = tmp_path / "routes.txt"
    document.write_bytes(("配置说明：这是一个使用国标编码保存的文本文件。\n" * 2000 + "route 路\n").encode("gbk"))
    matches = search.grep_files(document, regex.compile("路"), None, tmp_path,
                                search._Budget(threading.Event(), time.monotonic() + 15),
                                case_insensitive_globs=False)
    assert [m.line for m in matches] == ["route 路"]
