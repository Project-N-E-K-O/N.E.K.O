"""A familiar session end to end with a scripted model: commands, events, transcript."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nekoclaw.runtime import session as session_module
from nekoclaw.runtime.host import HostCallbacks
from nekoclaw.runtime.loop.session import CustomEntry, SessionState
from nekoclaw.runtime.loop.session.jsonl import entry_to_json_line
from nekoclaw.runtime.session import (
    TRANSCRIPT_FILE,
    SessionError,
    SessionRecorder,
    TranscriptStorage,
)
from tests.unit.nekoclaw.fakes import (
    ScriptedLLM,
    allow_all,
    call,
    finish,
    make_spec,
    open_session,
    say,
    think,
    usage,
)


def _roles(request: list[dict]) -> list[str]:
    return [message["role"] for message in request]


async def test_prompt_runs_a_tool_and_reports_every_step(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    (Path(spec.workdir) / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    llm = ScriptedLLM(
        [
            [say("Reading "), say("it."), call("read", {"file_path": "notes.txt"}), finish("tool_calls"), usage(100, 7)],
            [say("Done."), finish(), usage(140, 3)],
        ]
    )
    session, events = await open_session(spec, llm)
    try:
        started = events.of("started")[0]
        assert started["resumed"] is False
        assert {"read", "write", "edit", "glob", "grep", "web_fetch", "todo_write"} <= set(started["tools"])
        # Host-backed tools and read_image are withheld without a host or image support.
        assert not {"report", "ask_user_question", "web_search", "subagent", "read_image"} & set(started["tools"])

        session.prompt("What is in notes.txt?")
        ended = await events.wait_ended()

        assert ended["reason"] == "completed"
        assert ended["text"] == "Done."
        assert "".join(event["delta"] for event in events.of("text")) == "Reading it.Done."
        tool_start = events.of("tool_start")[0]
        assert tool_start["tool_name"] == "read"
        assert tool_start["args"] == {"file_path": "notes.txt"}
        tool_end = events.of("tool_end")[0]
        assert tool_end["is_error"] is False
        assert "1: alpha\n2: beta" in tool_end["text"]
        assert [event["context_tokens"] for event in events.of("usage")] == [107, 143]
        assert [event["seq"] for event in events.events] == list(range(1, len(events.events) + 1))

        # The second request carries the tool call and its result back to the model.
        second = llm.requests[1]
        assert second[0]["role"] == "system"
        assert _roles(second)[-2:] == ["assistant", "tool"]
        assert second[-2]["tool_calls"][0]["function"]["name"] == "read"
        assert "1: alpha" in second[-1]["content"]
        # Changing facts ride in a message, not in the system prompt.
        assert second[1]["role"] == "user" and second[1]["content"].startswith("<runtime_context>")
        assert second[2] == {"role": "user", "content": "What is in notes.txt?"}
        assert llm.requests[0][0]["content"] == second[0]["content"]
    finally:
        await session.close()

    lines = (Path(spec.session_dir) / TRANSCRIPT_FILE).read_text(encoding="utf-8").splitlines()
    types = [json.loads(line)["type"] for line in lines]
    assert types[0] == "session_info"
    assert types.count("message") == 4  # prompt, assistant, tool result, assistant
    assert events.events[-1]["type"] == "ended" and events.events[-1]["reason"] == "closed"


async def test_prompt_is_rejected_while_running_and_abort_stops_the_stream(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def hang() -> None:
        await release.wait()

    llm = ScriptedLLM([[say("thinking"), hang], [say("second run"), finish()]])
    session, events = await open_session(make_spec(tmp_path), llm)
    try:
        session.prompt("go")
        await events.wait_for(lambda event: event["type"] == "text")
        with pytest.raises(SessionError):
            session.prompt("again")

        assert session.abort() is True
        ended = await events.wait_ended()
        assert ended["reason"] == "aborted"
        assert ended["stop_reason"] == "aborted"
        # The model stream was closed rather than left to finish.
        assert llm.closed_streams == 1
        assert not session.running
        assert session.abort() is False

        mark = len(events.events)
        session.prompt("go again")
        ended = await events.wait_ended(start=mark)
        assert ended["reason"] == "completed"
        assert ended["text"] == "second run"
        # The aborted partial answer stays in history; the new prompt follows it.
        assert _roles(llm.requests[1])[-1] == "user"
    finally:
        release.set()
        await session.close()


async def test_steer_reaches_the_model_before_its_next_request(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    holder: dict[str, object] = {}

    async def steer_now() -> None:
        holder["session"].steer("Also check the tests.")  # type: ignore[union-attr]

    llm = ScriptedLLM(
        [
            [steer_now, call("todo_write", {"todos": [{"content": "look", "status": "in_progress"}]}), finish("tool_calls")],
            [say("ok"), finish()],
        ]
    )
    session, events = await open_session(spec, llm)
    holder["session"] = session
    try:
        session.prompt("Start.")
        await events.wait_ended()
        second = llm.requests[1]
        # Order: ... assistant tool call, its result, then the steered text.
        assert _roles(second)[-3:] == ["assistant", "tool", "user"]
        assert second[-1]["content"] == "Also check the tests."
        assert len(llm.requests) == 2
    finally:
        await session.close()


async def test_follow_up_waits_until_the_run_has_nothing_left(tmp_path: Path) -> None:
    holder: dict[str, object] = {}

    async def queue_follow_up() -> None:
        holder["session"].follow_up("Then summarize.")  # type: ignore[union-attr]

    llm = ScriptedLLM(
        [
            [queue_follow_up, call("todo_write", {"todos": [{"content": "a", "status": "completed"}]}), finish("tool_calls")],
            [say("first part done"), finish()],
            [say("summary"), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm)
    holder["session"] = session
    try:
        session.prompt("Do the first part.")
        ended = await events.wait_ended()
        assert ended["text"] == "summary"
        assert len(events.of("ended")) == 1
        # The follow-up was not in the request that followed the tool call ...
        assert all(message.get("content") != "Then summarize." for message in llm.requests[1])
        # ... it arrived only after the model had finished that work.
        third = llm.requests[2]
        assert third[-1] == {"role": "user", "content": "Then summarize."}
        assert third[-2]["content"] == "first part done"
    finally:
        await session.close()


async def test_steer_and_follow_up_start_a_run_on_an_idle_session(tmp_path: Path) -> None:
    llm = ScriptedLLM([[say("one"), finish()], [say("two"), finish()]])
    session, events = await open_session(make_spec(tmp_path), llm)
    try:
        session.steer("first")
        await events.wait_ended()
        mark = len(events.events)
        session.follow_up("second")
        ended = await events.wait_ended(start=mark)
        assert ended["text"] == "two"
        assert llm.requests[1][-1]["content"] == "second"
    finally:
        await session.close()


async def test_reasoning_and_tool_signature_return_to_the_model_unchanged(tmp_path: Path) -> None:
    signature = {"google": {"thought_signature": "c2lnbmF0dXJl"}}
    llm = ScriptedLLM(
        [
            [
                think("The user wants the list. "),
                think("Use glob."),
                call("glob", {"pattern": "*.md"}, extra_content=signature),
                finish("tool_calls"),
            ],
            [think("No files."), say("None found."), finish()],
            [say("ok"), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm)
    try:
        session.prompt("List markdown files.")
        await events.wait_ended()
        assistant = llm.requests[1][-2]
        assert assistant["reasoning_content"] == "The user wants the list. Use glob."
        assert assistant["tool_calls"][0]["extra_content"] == signature
        # Reasoning text is not part of the visible text stream.
        assert "".join(event["delta"] for event in events.of("text")) == "None found."

        mark = len(events.events)
        session.prompt("Thanks.")
        await events.wait_ended(start=mark)
        # A turn without tool calls sends no reasoning back.
        final_turn = [m for m in llm.requests[2] if m["role"] == "assistant"][-1]
        assert final_turn == {"role": "assistant", "content": "None found."}
    finally:
        await session.close()


async def test_a_session_continues_from_its_transcript(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    first = ScriptedLLM([[say("Noted: the code word is heron."), finish()]])
    session, events = await open_session(spec, first)
    session.prompt("Remember the code word heron.")
    await events.wait_ended()
    await session.close()

    second = ScriptedLLM([[say("heron"), finish()]])
    session, events = await open_session(spec, second)
    try:
        assert events.of("started")[0]["resumed"] is True
        session.prompt("What was the code word?")
        await events.wait_ended()
        contents = [message["content"] for message in second.requests[0]]
        assert "Remember the code word heron." in contents
        assert "Noted: the code word is heron." in contents
        assert contents[-1] == "What was the code word?"
    finally:
        await session.close()


async def test_a_transcript_cut_off_mid_entry_can_still_be_resumed(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    first = ScriptedLLM([[say("Noted: the code word is heron."), finish()]])
    session, events = await open_session(spec, first)
    session.prompt("Remember the code word heron.")
    await events.wait_ended()
    await session.close()

    transcript = Path(spec.session_dir) / TRANSCRIPT_FILE
    intact = transcript.read_bytes()
    assert intact.endswith(b"\n")
    # The process died while it was appending the next entry.
    transcript.write_bytes(intact + b'{"type":"message","id":"torn","message":{"role":"us')

    second = ScriptedLLM([[say("heron"), finish()]])
    session, events = await open_session(spec, second)
    try:
        assert events.of("started")[0]["resumed"] is True
        session.prompt("What was the code word?")
        await events.wait_ended()
        contents = [message["content"] for message in second.requests[0]]
        assert "Remember the code word heron." in contents
        assert "Noted: the code word is heron." in contents
    finally:
        await session.close()
    # The fragment is gone, nothing saved before it was touched, and what was
    # appended afterwards is whole lines again.
    after = transcript.read_bytes()
    assert after.startswith(intact) and len(after) > len(intact)
    lines = after.split(b"\n")
    assert lines[-1] == b""
    assert all(isinstance(json.loads(line), dict) for line in lines[:-1])


def test_only_an_incomplete_last_line_is_dropped(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    storage = TranscriptStorage(path)
    assert storage.drop_torn_tail() is False  # no file yet

    path.write_bytes(b"")
    assert storage.drop_torn_tail() is False
    path.write_bytes(b'{"a": 1}\n{"b": 2}\n')
    assert storage.drop_torn_tail() is False

    # A whole entry that only lacks its newline is kept.
    path.write_bytes(b'{"a": 1}\n{"b": 2}')
    assert storage.drop_torn_tail() is True
    assert path.read_bytes() == b'{"a": 1}\n{"b": 2}\n'

    # A fragment is removed, also when it is cut inside a character.
    path.write_bytes(b'{"a": 1}\n{"b": "' + "猫".encode("utf-8")[:2])
    assert storage.drop_torn_tail() is True
    assert path.read_bytes() == b'{"a": 1}\n'
    path.write_bytes(b'{"only": ')
    assert storage.drop_torn_tail() is True
    assert path.read_bytes() == b""


async def test_a_failed_transcript_write_is_retried_and_leaves_no_dangling_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "APPEND_RETRY_DELAYS", (0.01, 0.01))
    path = tmp_path / "transcript.jsonl"
    recorder = SessionRecorder(path)
    assert await recorder.read_all() == []
    append = recorder._storage.append
    # What goes wrong at each attempt to write an entry, in order.
    plan = {
        "b-cut-short": ["half written"],
        "c-not-synced": ["written, then failed"],
        "e-never": ["nothing written"] * 9,
    }
    attempts: list[str] = []

    async def flaky(entry: CustomEntry) -> None:
        label = entry.data["label"]
        attempts.append(label)
        trouble = plan[label].pop(0) if plan.get(label) else None
        if trouble == "nothing written":
            raise OSError("disk full")
        if trouble == "half written":
            line = entry_to_json_line(entry).encode("utf-8")
            with path.open("ab") as file:
                file.write(line[: len(line) // 2])
            raise OSError("disk full")
        await append(entry)
        if trouble == "written, then failed":
            raise OSError("could not sync")

    monkeypatch.setattr(recorder._storage, "append", flaky)
    for label in ("a", "b-cut-short", "c-not-synced", "d", "e-never", "f", "g"):
        recorder.link(CustomEntry(namespace="test", data={"label": label}))
    await recorder.close()

    saved = await SessionRecorder(path).read_all()
    labels = [entry.data["label"] for entry in saved]
    # The half-written entry was cleaned up and written again in its place. The
    # one that reached the file before its write failed is there once, not twice.
    assert labels == ["a", "b-cut-short", "c-not-synced", "d", "f", "g"]
    assert attempts[:5] == ["a", "b-cut-short", "b-cut-short", "c-not-synced", "d"]
    # The entry that never could be written was given up after three attempts.
    assert attempts.count("e-never") == 3
    by_label = {entry.data["label"]: entry for entry in saved}
    # Its successor hangs off the last entry that did reach the disk.
    assert by_label["f"].parent_id == by_label["d"].id
    assert by_label["g"].parent_id == by_label["f"].id
    # So no parent is missing, no id is there twice, and the transcript loads.
    SessionState.from_entries(saved)


async def test_an_abort_right_after_prompt_stops_the_run_before_it_starts(tmp_path: Path) -> None:
    llm = ScriptedLLM([[say("second answer"), finish()]])
    session, events = await open_session(make_spec(tmp_path), llm)
    try:
        session.prompt("do something")
        # Same step of the event loop: the run's task has not run yet.
        assert session.abort() is True
        ended = await events.wait_ended()
        assert ended["reason"] == "aborted"
        assert llm.requests == []

        # The session is usable afterwards.
        mark = len(events.events)
        session.prompt("again")
        again = await events.wait_ended(start=mark)
        assert (again["reason"], again["text"]) == ("completed", "second answer")
    finally:
        await session.close()


async def test_a_relative_session_dir_still_gives_the_model_paths_it_can_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    spec = make_spec(tmp_path).model_copy(update={"session_dir": "sessions/relative"})
    for number in range(130):
        (Path(spec.workdir) / f"f{number:03d}.txt").write_text("x", encoding="utf-8")
    llm = ScriptedLLM(
        [[call("glob", {"pattern": "*.txt"}), finish("tool_calls")], [say("listed"), finish()]]
    )
    session, events = await open_session(spec, llm)
    try:
        session.prompt("list the files")
        await events.wait_ended()
        text = events.of("tool_end")[0]["text"]
        saved = Path(text.split("Full sorted result stored at: ", 1)[1].split(". Use read", 1)[0])
        # The path is absolute, so `read` finds the file although it resolves
        # relative paths against the working directory.
        assert saved.is_absolute() and saved.is_file()
        assert saved.parent == Path.cwd() / "sessions" / "relative" / "outputs"
    finally:
        await session.close()


async def test_start_fails_cleanly_for_a_missing_workdir(tmp_path: Path) -> None:
    spec = make_spec(tmp_path).model_copy(update={"workdir": str(tmp_path / "missing")})
    with pytest.raises(SessionError, match="is not a directory"):
        await open_session(spec, ScriptedLLM())


async def test_report_tool_ends_the_turn_and_emits_a_report_event(tmp_path: Path) -> None:
    received: list[dict] = []

    async def report(session_id: str, payload: dict) -> dict:
        received.append({"session": session_id, **payload})
        return {"message": "The user will be told."}

    llm = ScriptedLLM(
        [
            [call("report", {"status": "progress", "summary": "Half way."}), finish("tool_calls")],
            [call("report", {"status": "ready", "summary": "All done.", "delivery": "proactive"}, "call-2"), finish("tool_calls")],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm, HostCallbacks(report=report))
    try:
        assert "report" in events.of("started")[0]["tools"]
        session.prompt("Do it.")
        ended = await events.wait_ended()
        assert ended["reason"] == "completed"
        # `progress` let the run continue; `ready` ended it without another request.
        assert len(llm.requests) == 2
        reports = events.of("report")
        assert [(r["status"], r["summary"], r["delivery"]) for r in reports] == [
            ("progress", "Half way.", None),
            ("ready", "All done.", "proactive"),
        ]
        assert received[1]["session"] == "s1" and received[1]["status"] == "ready"
        assert events.of("tool_end")[0]["text"] == "report delivered (progress). The user will be told."
    finally:
        await session.close()


async def test_permission_gate_blocks_what_needs_approval_without_an_approver(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    outside = tmp_path / "outside.txt"
    llm = ScriptedLLM(
        [
            [
                call("write", {"file_path": "inside.txt", "content": "ok"}, "c1", index=0),
                call("write", {"file_path": str(outside), "content": "no"}, "c2", index=1),
                finish("tool_calls"),
            ],
            [say("done"), finish()],
        ]
    )
    session, events = await open_session(spec, llm)
    try:
        session.prompt("write both")
        await events.wait_ended()
        inside, blocked = events.of("tool_end")
        assert inside["is_error"] is False
        assert (Path(spec.workdir) / "inside.txt").read_text(encoding="utf-8") == "ok"
        assert blocked["is_error"] is True
        assert "needs the user's approval" in blocked["text"]
        assert not outside.exists()
    finally:
        await session.close()


async def test_host_decides_permissions_when_it_offers_to(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    outside = tmp_path / "outside.txt"
    asked: list[dict] = []

    async def decide(session_id: str, payload: dict) -> dict:
        del session_id
        asked.append(payload)
        return {"allow": True}

    llm = ScriptedLLM(
        [[call("write", {"file_path": str(outside), "content": "yes"}), finish("tool_calls")], [say("done"), finish()]]
    )
    session, events = await open_session(spec, llm, HostCallbacks(decide_permission=decide))
    try:
        session.prompt("write outside")
        await events.wait_ended()
        assert outside.read_text(encoding="utf-8") == "yes"
        assert asked == [
            {
                "tool": "write",
                "category": "outside_write",
                "path": str(outside),
                "command": None,
                "cwd": None,
                "workdir": spec.workdir,
                "default": "ask",
            }
        ]
    finally:
        await session.close()


async def test_abort_cancels_a_pending_host_question(tmp_path: Path) -> None:
    asked = asyncio.Event()
    cancelled = asyncio.Event()

    async def ask_user(session_id: str, payload: dict) -> dict:
        del session_id, payload
        asked.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {}

    llm = ScriptedLLM(
        [[call("ask_user_question", {"questions": [{"id": "q", "question": "Which?"}]}), finish("tool_calls")]]
    )
    session, events = await open_session(make_spec(tmp_path), llm, HostCallbacks(ask_user=ask_user))
    try:
        session.prompt("ask")
        await asyncio.wait_for(asked.wait(), 10)
        session.abort()
        ended = await events.wait_ended()
        assert ended["reason"] == "aborted"
        await asyncio.wait_for(cancelled.wait(), 10)
    finally:
        await session.close()


async def test_allow_all_helper_is_a_valid_permission_callback() -> None:
    assert await allow_all()("s", {}) == {"allow": True}
