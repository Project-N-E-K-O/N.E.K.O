"""The dsh driver, against a process that speaks ACP the way dsh does.

No dsh is needed: ``fake_dsh.py`` stands in for it, started by the driver as
dsh would be. The model, where a test needs one, is ``fake_openai_server``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from nekoclaw.dsh import profile
from nekoclaw.dsh.driver import SESSION_FILE, DshLaunch, DshRuntime
from nekoclaw.dsh.profile import DshUnavailable
from nekoclaw.runtime.host import HostCallbacks
from nekoclaw.runtime.permissions import classify, default_permission_decision
from nekoclaw.runtime.protocol import PROCESS_EXITED_ERROR, GatewaySpec, ModelConfig, TaskAgentSpec
from tests.unit.nekoclaw.fake_openai_server import FakeOpenAIServer, sse_finish, sse_text, sse_usage
from tests.unit.nekoclaw.fakes import Recorder
from tests.unit.nekoclaw.fakes import make_spec as _make_spec

pytestmark = pytest.mark.integration_serial

FAKE = Path(__file__).with_name("fake_dsh.py")


def make_spec(tmp_path: Path, session_id: str = "s1", **overrides: Any) -> TaskAgentSpec:
    """A spec whose model has an address, as the host's always has; nothing listens there."""
    overrides.setdefault("model", ModelConfig(model="fake-model", base_url="http://127.0.0.1:9/v1", api_key="test-key"))
    return _make_spec(tmp_path, session_id, **overrides)


class Dsh:
    """A driver whose "dsh" is the fake, with what the fake was asked."""

    def __init__(self, tmp_path: Path, host: Any = None, *, idle: float | None = None, **environment: str) -> None:
        self.home = tmp_path / "dsh-home"
        self.events = Recorder()
        self.launches = 0

        def launch() -> DshLaunch:
            self.launches += 1
            self.home.mkdir(parents=True, exist_ok=True)
            return DshLaunch(executable=Path(sys.executable), home=self.home, leading=(str(FAKE),))

        self.runtime = DshRuntime(
            self.events,
            host,
            launch=launch,
            idle_exit_seconds=idle,
            # The fake reaches the driver's endpoint on the loopback address.
            environment={**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost", **environment},
        )

    def record(self, kind: str | None = None) -> list[dict[str, Any]]:
        try:
            lines = (self.home / "fake-record.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows = [json.loads(line) for line in lines if line.strip()]
        return [row for row in rows if kind is None or row["kind"] == kind]

    async def started(self, spec: TaskAgentSpec) -> dict[str, Any]:
        position = len(self.events.events)
        await self.runtime.start(spec)
        return await self.events.wait_for(
            lambda event: event["type"] in ("started", "error") and event["session_id"] == spec.session_id, start=position
        )

    async def closed(self, session_id: str) -> dict[str, Any]:
        """Close a session and return the event that announces its end; ``close`` itself does not wait for it."""
        position = len(self.events.events)
        await self.runtime.close(session_id)
        return await self.events.wait_for(
            lambda event: event["type"] == "ended" and event["reason"] == "closed" and event["session_id"] == session_id,
            start=position,
        )

    async def run(self, session_id: str, text: str, timeout: float = 20.0) -> dict[str, Any]:
        """Give a session work and return the event that ended it."""
        position = len(self.events.events)
        await self.runtime.prompt(session_id, text)
        return await self.events.wait_ended(session_id, timeout, start=position)


@pytest.fixture
async def dsh(tmp_path: Path):
    made: list[Dsh] = []

    def make(host: Any = None, **options: Any) -> Dsh:
        made.append(Dsh(tmp_path, host, **options))
        return made[-1]

    yield make
    for item in made:
        await item.runtime.shutdown()


async def _seen_through(tasks: list[asyncio.Future[Any]]) -> None:
    """Let a test's tasks finish, a bounded while, whatever became of the test; what is left is cancelled.

    Their outcome is looked at, so that a failure of theirs is not left
    unread; the test itself looks at what it expects of them.
    """
    if tasks:
        await asyncio.wait(tasks, timeout=10)
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _until_started_on(event: threading.Event, starting: asyncio.Future[Any], events: Recorder) -> None:
    """Wait, a bounded while, for a start to reach the point a test holds it at; fail when it ended first."""
    for _attempt in range(1000):
        if event.is_set():
            return
        if starting.done():
            raise AssertionError(f"the start was over before it got there: {events.events}")
        await asyncio.sleep(0.01)
    raise AssertionError("the start never got there")


def _texts(events: Recorder, session_id: str = "s1") -> str:
    return "".join(event["delta"] for event in events.of("text", session_id))


# ---------------------------------------------------------------------------
# A session's life
# ---------------------------------------------------------------------------


async def test_a_session_starts_takes_work_and_ends(tmp_path: Path, dsh) -> None:
    driver = dsh()
    spec = make_spec(tmp_path)

    started = await driver.started(spec)
    assert started["type"] == "started" and started["resumed"] is False
    ended = await driver.run("s1", "hello")

    assert ended["reason"] == "completed" and ended["inputs"] == 1
    assert ended["text"] == "echo: hello" and _texts(driver.events) == "echo: hello"
    assert [event["seq"] for event in driver.events.events] == list(range(len(driver.events.events)))
    # Started the way dsh is: its ACP profile, our patch, a session in the working directory.
    initialize = driver.record("initialize")[0]
    assert initialize["argv"][:3] == ["--profile", "acp", "--patch"]
    assert Path(initialize["argv"][3]).parent == profile.config_dir(driver.home)
    created = driver.record("session/new")[0]
    assert created["cwd"] == spec.workdir
    assert driver.record("session/set_config_option")[0]["value"] == profile.model_option(0)
    # Which dsh session belongs to the commission is kept in its session directory.
    kept = json.loads(Path(spec.session_dir, SESSION_FILE).read_text(encoding="utf-8"))
    assert kept["session"] == created["session"] and kept["workdir"] == spec.workdir


@pytest.mark.skipif(sys.platform != "win32", reason="the drive matters on Windows only")
async def test_the_process_is_started_on_drive_c(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path))
    assert Path(driver.record("initialize")[0]["cwd"]).drive.upper() == "C:"


async def test_what_the_host_tells_a_commission_heads_its_first_message_only(tmp_path: Path, dsh) -> None:
    driver = dsh()
    spec = make_spec(tmp_path, instructions="Commission: tidy the desk.", reply_language="Japanese")
    await driver.started(spec)

    await driver.run("s1", "first")
    await driver.run("s1", "second")

    first, second = (row["text"] for row in driver.record("session/prompt"))
    assert first.startswith("[From the host, for this commission]\nCommission: tidy the desk.")
    assert "in Japanese." in first and first.endswith("[Your work]\nfirst")
    assert second == "second"


async def test_a_session_without_instructions_gets_its_work_as_it_is(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path))
    await driver.run("s1", "just this")
    assert driver.record("session/prompt")[0]["text"] == "just this"


async def test_text_added_during_a_turn_is_the_next_turn_and_the_run_ends_once(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path))

    await driver.runtime.prompt("s1", "!sleep 0.4\n!say one")
    await asyncio.sleep(0.1)
    await driver.runtime.steer("s1", "two")
    await driver.runtime.follow_up("s1", "three")
    ended = await driver.events.wait_ended("s1")

    # dsh takes no text in the middle of a turn: both wait for it to end and go in together.
    assert [row["text"] for row in driver.record("session/prompt")] == ["!sleep 0.4\n!say one", "two\n\nthree"]
    assert ended["reason"] == "completed" and ended["inputs"] == 3
    assert len(driver.events.of("ended")) == 1
    assert _texts(driver.events) == "one\n\necho: two\n\nthree"
    assert ended["text"] == "echo: two\n\nthree"


async def test_a_steer_to_an_idle_session_starts_a_run(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path))
    await driver.run("s1", "first")

    await driver.runtime.steer("s1", "wake up")
    ended = await driver.events.wait_for(lambda event: event["type"] == "ended" and event["inputs"] == 2)

    assert ended["text"] == "echo: wake up"


async def test_a_prompt_during_a_run_is_refused(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path))
    await driver.runtime.prompt("s1", "!sleep 5")
    await asyncio.sleep(0.1)

    await driver.runtime.prompt("s1", "another")
    refused = await driver.events.wait_for(lambda event: event["type"] == "error")

    assert refused["command"] == "prompt" and "in progress" in refused["message"]
    assert [row["text"] for row in driver.record("session/prompt")] == ["!sleep 5"]


async def test_abort_stops_the_turn_and_drops_what_was_queued(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path))
    await driver.runtime.prompt("s1", "!sleep 30\n!say never")
    await asyncio.sleep(0.1)
    await driver.runtime.steer("s1", "queued")

    await driver.runtime.abort("s1")
    ended = await driver.events.wait_ended("s1")

    assert ended["reason"] == "aborted"
    assert driver.record("session/cancel")
    assert len(driver.record("session/prompt")) == 1 and _texts(driver.events) == ""
    # The session is still there and takes new work.
    again = await driver.run("s1", "after")
    assert again["reason"] == "completed" and again["text"] == "echo: after"


async def test_text_added_after_a_stop_and_before_the_turn_ended_goes_with_it(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path))
    await driver.runtime.prompt("s1", "!sleep 30")
    await asyncio.sleep(0.1)

    await driver.runtime.abort("s1")
    # dsh has not ended the turn yet.
    await driver.runtime.steer("s1", "too late")
    ended = await driver.events.wait_ended("s1")
    await asyncio.sleep(0.3)

    # As in the built-in runtime: what is queued when a run ends as stopped is dropped.
    assert ended["reason"] == "aborted" and len(driver.events.of("ended")) == 1
    assert len(driver.record("session/prompt")) == 1


async def test_aborting_an_idle_session_says_nothing(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path))
    await driver.runtime.abort("s1")
    await asyncio.sleep(0.2)
    assert driver.events.of("ended") == [] and driver.events.of("error") == []


async def test_a_turn_that_fails_ends_the_run_with_the_reason(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path))
    ended = await driver.run("s1", "!say before\n!fail the model gave up")
    assert ended["reason"] == "error" and "the model gave up" in ended["error"]
    assert ended["inputs"] == 1 and ended["text"] == ""


async def test_close_stops_a_running_turn_and_announces_closed(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path))
    await driver.runtime.prompt("s1", "!sleep 30")
    await asyncio.sleep(0.1)

    await driver.closed("s1")

    ended = driver.events.of("ended")
    assert [event["reason"] for event in ended] == ["closed"] and ended[0]["inputs"] is None
    assert driver.record("session/close") and driver.runtime.session_ids == []
    # Closed sessions are unknown; a second close says so.
    await driver.runtime.close("s1")
    assert driver.events.of("error")[-1]["command"] == "close"


async def test_close_does_not_wait_for_dsh(tmp_path: Path, dsh) -> None:
    driver = dsh(FAKE_DSH_CLOSE="slow")
    await driver.started(make_spec(tmp_path))
    loop = asyncio.get_running_loop()

    began = loop.time()
    await driver.runtime.close("s1")

    # The host calls this where it takes in the events of every session: it returns
    # at once, and the end is announced when dsh has let the session go.
    assert loop.time() - began < 0.3 and driver.events.of("ended") == []
    # Closing it again meanwhile is no error, and it is closed once.
    await driver.runtime.close("s1")
    closed = await driver.events.wait_for(lambda event: event["type"] == "ended")
    await asyncio.sleep(0.2)
    assert closed["reason"] == "closed" and len(driver.events.of("ended")) == 1 and driver.events.of("error") == []


async def test_a_run_stopped_before_its_first_turn_was_sent_ends_as_stopped(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path))

    await driver.runtime.prompt("s1", "!say never")
    await driver.runtime.abort("s1")
    ended = await driver.events.wait_ended("s1")

    assert ended["reason"] == "aborted" and ended["text"] == ""
    assert driver.record("session/prompt") == [] and _texts(driver.events) == ""


async def test_commands_for_an_unknown_session_are_refused(tmp_path: Path, dsh) -> None:
    driver = dsh()
    for command in ("prompt", "steer", "follow_up"):
        await getattr(driver.runtime, command)("nobody", "text")
    await driver.runtime.abort("nobody")
    assert [event["command"] for event in driver.events.of("error")] == ["prompt", "steer", "follow_up", "abort"]
    assert driver.launches == 0


async def test_a_second_start_of_a_session_is_refused(tmp_path: Path, dsh) -> None:
    driver = dsh()
    spec = make_spec(tmp_path)
    await driver.started(spec)
    await driver.runtime.start(spec)
    assert driver.events.of("error")[-1]["command"] == "start"
    assert len(driver.record("session/new")) == 1


# ---------------------------------------------------------------------------
# Resuming, idling, dying
# ---------------------------------------------------------------------------


async def test_a_session_resumes_in_a_new_process_without_being_told_again(tmp_path: Path, dsh) -> None:
    spec = make_spec(tmp_path, instructions="Commission: tidy the desk.")
    first = dsh()
    await first.started(spec)
    await first.run("s1", "begin")
    dsh_session = first.record("session/new")[0]["session"]
    await first.runtime.shutdown()

    second = dsh()
    started = await second.started(spec)
    await second.run("s1", "go on")

    assert started["resumed"] is True
    resumed = second.record("session/resume")
    assert [row["session"] for row in resumed] == [dsh_session] and resumed[0]["pid"] != first.record("session/new")[0]["pid"]
    assert len(second.record("session/new")) == 1
    # What the host tells the commission was said when it began.
    assert second.record("session/prompt")[-1]["text"] == "go on"


async def test_a_session_that_was_closed_before_any_work_reached_it_is_told_what_the_host_tells_it_when_resumed(
    tmp_path: Path, dsh
) -> None:
    spec = make_spec(tmp_path, instructions="Commission: tidy the desk.")
    first = dsh()
    await first.started(spec)
    # The quota ran out before its first request, say: closed with nothing sent.
    await first.runtime.shutdown()
    assert json.loads(Path(spec.session_dir, SESSION_FILE).read_text(encoding="utf-8"))["briefed"] is False

    second = dsh()
    started = await second.started(spec)
    await second.run("s1", "begin")
    await second.run("s1", "go on")

    # dsh has the session, and nothing was said in it: the first message carries the commission.
    assert started["resumed"] is True
    begun, went_on = (row["text"] for row in second.record("session/prompt")[-2:])
    assert begun.startswith("[From the host, for this commission]\nCommission: tidy the desk.") and begun.endswith("begin")
    assert went_on == "go on"
    assert json.loads(Path(spec.session_dir, SESSION_FILE).read_text(encoding="utf-8"))["briefed"] is True


async def test_a_first_message_dsh_did_not_take_is_given_again_with_the_next(tmp_path: Path, dsh) -> None:
    driver = dsh()
    spec = make_spec(tmp_path, instructions="Commission: tidy the desk.")
    await driver.started(spec)

    failed = await driver.run("s1", "!fail the model is down")
    await driver.run("s1", "once more")

    # Told twice does no harm; never told would lose the commission.
    assert failed["reason"] == "error"
    assert all(row["text"].startswith("[From the host, for this commission]") for row in driver.record("session/prompt"))
    await driver.run("s1", "and again")
    assert driver.record("session/prompt")[-1]["text"] == "and again"


async def test_a_session_whose_start_fails_part_way_leaves_no_note_behind(tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch) -> None:
    driver = dsh()
    spec = make_spec(tmp_path)
    monkeypatch.setattr(profile, "model_option", lambda index: "not-a-model-dsh-knows")

    refused = await driver.started(spec)

    # dsh made the session and then refused to set it up: it is closed, and
    # nothing says that the commission has a session in dsh.
    assert refused["type"] == "error" and refused["command"] == "start"
    assert len(driver.record("session/new")) == 1 and not Path(spec.session_dir, SESSION_FILE).exists()
    for _attempt in range(100):
        if driver.record("session/close"):
            break
        await asyncio.sleep(0.05)
    assert driver.record("session/close")


async def test_a_session_that_is_resumed_asks_the_model_under_the_name_it_had(tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_SESSION_SLOTS", 3)
    driver = dsh()
    specs = {}
    for name in ("s1", "s2", "s3", "s4"):
        (tmp_path / name).mkdir()
        specs[name] = make_spec(tmp_path / name, name)
    def chosen() -> list[str]:
        return [row["value"] for row in driver.record("session/set_config_option")]

    for name in ("s1", "s2", "s3"):
        await driver.started(specs[name])
    await driver.closed("s1")
    await driver.closed("s2")

    # dsh's record of a session names the model of its last request, and it may ask
    # under that name once more: resumed, the session has the name it had, though
    # another one is free and comes first.
    resumed = await driver.started(specs["s2"])
    assert resumed["resumed"] is True
    assert chosen() == [profile.model_option(0), profile.model_option(1), profile.model_option(2), profile.model_option(1)]

    # When another session has that name by now, it gets one that is free, and the note says which.
    await driver.started(specs["s4"])
    await driver.closed("s3")
    again = await driver.started(specs["s1"])
    assert again["resumed"] is True
    assert chosen()[-2:] == [profile.model_option(0), profile.model_option(2)]
    assert json.loads(Path(specs["s1"].session_dir, SESSION_FILE).read_text(encoding="utf-8"))["slot"] == 2


async def test_a_session_dsh_fails_to_resume_is_not_replaced_by_a_new_one(tmp_path: Path, dsh) -> None:
    spec = make_spec(tmp_path)
    first = dsh()
    await first.started(spec)
    await first.run("s1", "begin")
    await first.runtime.shutdown()
    note = Path(spec.session_dir, SESSION_FILE).read_text(encoding="utf-8")

    second = dsh(FAKE_DSH_RESUME="broken")
    refused = await second.started(spec)

    # Something failed inside dsh; that does not say the session is gone. A new one in its
    # place would leave what was said in it behind for good.
    assert refused["type"] == "error" and refused["command"] == "start" and "refused the session" in refused["message"]
    assert len(second.record("session/new")) == 1 and second.runtime.session_ids == []
    assert Path(spec.session_dir, SESSION_FILE).read_text(encoding="utf-8") == note


async def test_a_session_whose_log_is_gone_starts_anew(tmp_path: Path, dsh) -> None:
    spec = make_spec(tmp_path, instructions="Commission: tidy the desk.")
    first = dsh()
    await first.started(spec)
    await first.runtime.shutdown()
    (first.home / "fake-sessions.json").unlink()

    second = dsh()
    started = await second.started(spec)
    await second.run("s1", "begin again")

    assert started["type"] == "started" and started["resumed"] is False
    assert len(second.record("session/new")) == 2
    assert second.record("session/prompt")[-1]["text"].startswith("[From the host, for this commission]")
    kept = json.loads(Path(spec.session_dir, SESSION_FILE).read_text(encoding="utf-8"))
    assert kept["session"] == second.record("session/new")[-1]["session"]
    # The one dsh no longer knows is remembered, for whatever dsh may still hold of it.
    assert kept["previous"] == [second.record("session/new")[0]["session"]]


async def test_a_session_moved_to_another_working_directory_is_not_resumed(tmp_path: Path, dsh) -> None:
    spec = make_spec(tmp_path)
    first = dsh()
    await first.started(spec)
    await first.runtime.shutdown()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    second = dsh()
    started = await second.started(spec.model_copy(update={"workdir": str(elsewhere)}))

    # dsh is not even asked: its session works in the directory it was made for.
    assert started["resumed"] is False and second.record("resume-attempt") == []


async def test_an_idle_process_closes_its_sessions_and_exits(tmp_path: Path, dsh) -> None:
    driver = dsh(idle=0.3)
    await driver.started(make_spec(tmp_path))
    await driver.run("s1", "hello")

    closed = await driver.events.wait_for(lambda event: event["type"] == "ended" and event["reason"] == "closed")
    for _attempt in range(100):
        if not driver.runtime.process_ids and driver.record("exit"):
            break
        await asyncio.sleep(0.05)

    assert closed["session_id"] == "s1" and driver.runtime.process_ids == []
    assert driver.record("session/close") and driver.record("exit")
    # The next start brings a process up again and resumes.
    again = await driver.started(make_spec(tmp_path))
    assert again["resumed"] is True and driver.launches == 2


async def test_a_session_that_arrives_gives_an_idle_process_its_time_again(tmp_path: Path, dsh) -> None:
    driver = dsh(idle=1.0)
    await driver.started(make_spec(tmp_path, "s1"))
    await driver.run("s1", "hello")
    # The process has been idle for most of the time it is allowed.
    await asyncio.sleep(0.85)

    await driver.started(make_spec(tmp_path, "s2"))
    # The host takes a moment to hand a new session its work.
    await asyncio.sleep(0.6)

    assert driver.events.of("ended", "s2") == [] and len(driver.runtime.process_ids) == 1
    ended = await driver.run("s2", "at last")
    assert ended["reason"] == "completed" and ended["text"] == "echo: at last" and driver.launches == 1


async def test_shutdown_waits_for_a_process_that_is_being_ended_for_being_idle(tmp_path: Path, dsh) -> None:
    driver = dsh(idle=0.2, FAKE_DSH_EXIT_DELAY="0.6")
    await driver.started(make_spec(tmp_path))
    await driver.run("s1", "hello")
    # It is being ended for having been idle, and takes its time to exit.
    for _attempt in range(200):
        if driver.runtime._retiring:
            break
        await asyncio.sleep(0.02)
    assert driver.runtime._retiring and driver.runtime.process_ids == []

    await driver.runtime.shutdown()

    # Not left behind: by the time shutting down is over, so is that process.
    assert driver.record("exit") and [event["reason"] for event in driver.events.of("ended")][-1] == "closed"


async def test_a_busy_session_keeps_the_process_alive(tmp_path: Path, dsh) -> None:
    driver = dsh(idle=0.2)
    await driver.started(make_spec(tmp_path))
    ended = await driver.run("s1", "!sleep 1.0\n!say still here")
    assert ended["reason"] == "completed" and ended["text"] == "still here"


async def test_a_process_that_dies_ends_its_sessions(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path, "s1"))
    await driver.started(make_spec(tmp_path, "s2"))

    await driver.runtime.prompt("s1", "!crash")
    await driver.events.wait_for(lambda event: event["type"] == "ended" and event["session_id"] == "s2")

    ended = {event["session_id"]: event for event in driver.events.of("ended")}
    assert set(ended) == {"s1", "s2"}
    assert all(event["reason"] == "error" and event["error"] == PROCESS_EXITED_ERROR for event in ended.values())
    assert driver.runtime.session_ids == [] and driver.runtime.process_ids == []
    # A new session gets a new process.
    assert (await driver.started(make_spec(tmp_path, "s3")))["type"] == "started"


async def test_a_process_that_goes_while_a_session_is_being_started_is_a_refused_start_and_nothing_else(
    tmp_path: Path, dsh
) -> None:
    driver = dsh(FAKE_DSH_NEW="crash")

    refused = await driver.started(make_spec(tmp_path))
    await asyncio.sleep(0.3)

    # As the built-in runtime reports a session it could not open: no end of a session that never began.
    assert refused["type"] == "error" and refused["command"] == "start"
    assert [event["type"] for event in driver.events.events] == ["error"]
    assert driver.runtime.session_ids == [] and driver.runtime.process_ids == []
    assert not Path(tmp_path, "session", SESSION_FILE).exists()


async def test_a_process_that_goes_while_the_note_of_a_new_session_is_written_is_a_refused_start(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = dsh()
    write = DshRuntime._note

    async def note_once_dsh_is_gone(self: DshRuntime, session: Any) -> None:
        await asyncio.to_thread(session.process.connection.kill)
        for _attempt in range(200):
            if not self.session_ids:
                break
            await asyncio.sleep(0.02)
        await write(self, session)

    monkeypatch.setattr(DshRuntime, "_note", note_once_dsh_is_gone)
    answer = await driver.started(make_spec(tmp_path))
    await asyncio.sleep(0.2)

    # The session went with the process, which said nothing of it: the start
    # failed, and no "started" points the host at a session that is not there.
    assert answer["type"] == "error" and answer["command"] == "start"
    assert [event["type"] for event in driver.events.events] == ["error"]
    assert driver.runtime.session_ids == []


async def test_a_start_that_failed_late_does_not_take_back_what_a_later_one_of_the_same_id_holds(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, _calls = _host()
    driver = dsh(host)
    write = DshRuntime._note
    later: list[dict[str, Any]] = []
    once: list[bool] = []

    async def note_once_dsh_is_gone(self: DshRuntime, session: Any) -> None:
        if not once:
            once.append(True)
            await asyncio.to_thread(session.process.connection.kill)
            for _attempt in range(200):
                if not self.session_ids:
                    break
                await asyncio.sleep(0.02)
            # Gone with its process, unannounced: a start of the same id is not refused meanwhile.
            later.append(await driver.started(make_spec(tmp_path / "again", "s1")))
        await write(self, session)

    (tmp_path / "again").mkdir()
    monkeypatch.setattr(DshRuntime, "_note", note_once_dsh_is_gone)
    await driver.runtime.start(make_spec(tmp_path))

    # The second started; then the first is reported as having failed.
    answers = [event["type"] for event in driver.events.events if event["type"] in ("started", "error")]
    assert answers == ["started", "error"] and later[0]["type"] == "started"
    # The first, given up after the second began, leaves the second its tools and its token.
    assert driver.runtime.session_ids == ["s1"] and driver.runtime.endpoint._by_session.get("s1") is not None
    await driver.run("s1", '!host report {"status": "ready", "summary": "done"}')
    (result,) = [row["text"] for row in driver.record("service-call")]
    assert result.startswith("report delivered (ready).")


async def test_a_report_from_a_session_that_is_gone_is_not_taken_for_the_next_ones(tmp_path: Path, dsh) -> None:
    driver = dsh()
    spec = make_spec(tmp_path)
    await driver.started(spec)
    gone = driver.runtime._sessions["s1"]
    await driver.closed("s1")
    await driver.started(spec)

    # A call the closed session made, finishing late: under its id is another session now.
    driver.runtime._on_report(gone, {"status": "ready", "summary": "from the one before"})
    driver.runtime._on_report(driver.runtime._sessions["s1"], {"status": "progress", "summary": "its own"})

    assert [event["summary"] for event in driver.events.of("report")] == ["its own"]


async def test_a_note_being_written_when_shutdown_gives_its_start_up_is_written_before_shutdown_returns(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nekoclaw.dsh import driver as driver_module

    monkeypatch.setattr(driver_module, "_SHUTDOWN_OPENING_WAIT_SECONDS", 0.2)
    writing, go_on = threading.Event(), threading.Event()
    written: list[bool] = []
    write = driver_module._write_session_file

    def slow_write(*args: Any, **kwargs: Any) -> None:
        writing.set()
        go_on.wait(10)
        write(*args, **kwargs)
        written.append(True)

    monkeypatch.setattr(driver_module, "_write_session_file", slow_write)
    driver = dsh()
    starting = asyncio.ensure_future(driver.runtime.start(make_spec(tmp_path)))
    tasks: list[asyncio.Future[Any]] = [starting]
    try:
        await _until_started_on(writing, starting, driver.events)
        shutting = asyncio.ensure_future(driver.runtime.shutdown())
        tasks.append(shutting)
        await asyncio.sleep(0.5)
        # A note written after the start was over could replace the next one's.
        assert not shutting.done()
    finally:
        go_on.set()
        await _seen_through(tasks)
    shutting.result()
    starting.result()

    assert written == [True]
    assert [event["command"] for event in driver.events.of("error", "s1")] == ["start"]


async def test_two_starts_that_waited_for_the_same_close_start_the_session_once(tmp_path: Path, dsh) -> None:
    driver = dsh(FAKE_DSH_CLOSE="slow")
    spec = make_spec(tmp_path)
    await driver.started(spec)
    await driver.runtime.close("s1")
    position = len(driver.events.events)

    await asyncio.gather(driver.runtime.start(spec), driver.runtime.start(spec))

    answers = [event for event in driver.events.events[position:] if event["type"] in ("started", "error")]
    assert sorted(event["type"] for event in answers) == ["error", "started"]
    assert next(event for event in answers if event["type"] == "error")["message"] == 'session "s1" already exists'
    assert driver.runtime.session_ids == ["s1"] and len(driver.record("session/resume")) == 1


async def test_shutdown_gives_up_a_start_that_takes_too_long_and_nothing_starts_after_it(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nekoclaw.dsh import driver as driver_module

    monkeypatch.setattr(driver_module, "_SHUTDOWN_OPENING_WAIT_SECONDS", 0.2)
    reading, release = threading.Event(), threading.Event()
    read = driver_module._read_session_file

    def slow_read(directory: str) -> Any:
        reading.set()
        release.wait(10)
        return read(directory)

    monkeypatch.setattr(driver_module, "_read_session_file", slow_read)
    driver = dsh()
    starting = asyncio.ensure_future(driver.runtime.start(make_spec(tmp_path)))
    try:
        await _until_started_on(reading, starting, driver.events)
        await asyncio.wait_for(driver.runtime.shutdown(), 5)
        # The start was over before shutting down went on: what it said came first,
        # and the session was not taken for one to close.
        refused = driver.events.of("error", "s1")
    finally:
        release.set()
        await _seen_through([starting])
    starting.result()
    await asyncio.sleep(0.2)

    # Given up, and not made after the runtime shut down: no session, and no dsh process.
    assert [event["command"] for event in refused] == ["start"] and "shutting down" in refused[0]["message"]
    assert [event["type"] for event in driver.events.events] == ["error"]
    assert driver.launches == 0 and driver.runtime.process_ids == []


async def test_a_shutdown_that_is_cancelled_still_ends_the_process(tmp_path: Path, dsh) -> None:
    driver = dsh(FAKE_DSH_CLOSE="slow")
    await driver.started(make_spec(tmp_path))

    shutting = asyncio.ensure_future(driver.runtime.shutdown())
    await asyncio.sleep(0.15)
    shutting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutting

    # Nobody else would end it: the session is still closed and the process told to go.
    for _attempt in range(100):
        if driver.record("exit"):
            break
        await asyncio.sleep(0.05)
    assert driver.record("exit") and [event["reason"] for event in driver.events.of("ended")] == ["closed"]


async def test_a_process_that_goes_while_a_session_is_being_closed_ends_it_once(tmp_path: Path, dsh) -> None:
    driver = dsh(FAKE_DSH_CLOSE="crash")
    await driver.started(make_spec(tmp_path))

    await driver.closed("s1")
    await asyncio.sleep(0.2)

    # The close announces the end; the process going away does not announce another.
    assert [event["reason"] for event in driver.events.of("ended")] == ["closed"]
    assert driver.runtime.session_ids == [] and driver.runtime.process_ids == []


async def test_a_start_while_the_runtime_shuts_down_is_refused(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path, "s1"))

    shutting = asyncio.ensure_future(driver.runtime.shutdown())
    await asyncio.sleep(0)
    await driver.runtime.start(make_spec(tmp_path, "s2"))
    await shutting

    refused = driver.events.of("error", "s2")
    assert [event["command"] for event in refused] == ["start"] and "shutting down" in refused[0]["message"]
    assert driver.events.of("started", "s2") == []
    assert driver.runtime.session_ids == [] and driver.runtime.process_ids == []


async def test_shutdown_closes_everything_and_the_runtime_can_be_used_again(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path, "s1"))
    await driver.started(make_spec(tmp_path, "s2"))
    await driver.runtime.prompt("s1", "!sleep 30")
    await asyncio.sleep(0.1)

    await driver.runtime.shutdown()

    assert sorted(event["session_id"] for event in driver.events.of("ended") if event["reason"] == "closed") == ["s1", "s2"]
    assert driver.runtime.process_ids == [] and driver.runtime.endpoint.port is None
    assert (await driver.started(make_spec(tmp_path, "s3")))["type"] == "started"


# ---------------------------------------------------------------------------
# Starts that are refused
# ---------------------------------------------------------------------------


async def test_a_dsh_that_is_not_installed_is_a_refused_start(tmp_path: Path) -> None:
    events = Recorder()

    def launch() -> DshLaunch:
        raise DshUnavailable("dsh is not installed")

    runtime = DshRuntime(events, launch=launch)
    await runtime.start(make_spec(tmp_path))
    assert [(event["type"], event["command"], event["message"]) for event in events.events] == [
        ("error", "start", "dsh is not installed")
    ]
    assert runtime.session_ids == []
    await runtime.shutdown()


async def test_a_dsh_whose_guard_did_not_load_is_not_used(tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_GUARD_READY_SECONDS", 0.5)
    driver = dsh(FAKE_DSH_GUARD="off")

    answer = await driver.started(make_spec(tmp_path))

    # Without the guard dsh's tools would act without asking.
    assert answer["type"] == "error" and answer["command"] == "start" and "permission guard" in answer["message"]
    assert driver.record("session/new") == [] and driver.runtime.process_ids == []
    assert driver.runtime.session_ids == []


async def test_a_dsh_that_cannot_resume_sessions_is_not_used(tmp_path: Path, dsh) -> None:
    driver = dsh(FAKE_DSH_NO_RESUME="1")
    answer = await driver.started(make_spec(tmp_path))
    assert answer["type"] == "error" and "resume" in answer["message"]
    assert driver.runtime.process_ids == []


async def test_dsh_can_be_made_ready_before_a_session_is_given_to_it(tmp_path: Path, dsh) -> None:
    driver = dsh()
    spec = make_spec(tmp_path)

    await driver.runtime.prepare(spec.model)

    # The process is up and the guard confirmed; no session exists yet, and the start uses that process.
    assert len(driver.runtime.process_ids) == 1 and driver.launches == 1 and driver.record("session/new") == []
    await driver.runtime.prepare(spec.model)
    assert (await driver.started(spec))["type"] == "started" and driver.launches == 1


async def test_a_dsh_that_cannot_be_made_ready_says_why(tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_GUARD_READY_SECONDS", 0.5)
    driver = dsh(FAKE_DSH_GUARD="off")
    with pytest.raises(DshUnavailable, match="permission guard"):
        await driver.runtime.prepare(make_spec(tmp_path).model)
    assert driver.runtime.process_ids == []
    with pytest.raises(DshUnavailable, match="Anthropic"):
        await driver.runtime.prepare(ModelConfig(model="c", base_url="https://x.invalid", provider_type="anthropic"))
    # Nothing was announced: no session was asked for.
    assert driver.events.events == []


async def test_a_program_that_cannot_be_started_is_a_refused_start(tmp_path: Path) -> None:
    events = Recorder()
    runtime = DshRuntime(events, launch=lambda: DshLaunch(executable=tmp_path / "no-such-program", home=tmp_path / "home"))
    await runtime.start(make_spec(tmp_path))
    assert events.events[0]["type"] == "error" and "could not be started" in events.events[0]["message"]
    # The token made for the process that did not come up opens nothing.
    assert runtime.endpoint._process_tokens == set()
    await runtime.shutdown()


async def test_a_model_reached_through_the_anthropic_interface_is_not_started_on_dsh(tmp_path: Path, dsh) -> None:
    driver = dsh()
    model = ModelConfig(model="claude", base_url="https://example.invalid/v1", api_key="k", provider_type="anthropic")
    answer = await driver.started(make_spec(tmp_path, model=model))
    assert answer["type"] == "error" and answer["command"] == "start" and "Anthropic" in answer["message"]
    assert driver.launches == 0


async def test_a_model_without_an_address_is_not_started_on_dsh(tmp_path: Path, dsh) -> None:
    driver = dsh()
    answer = await driver.started(make_spec(tmp_path, model=ModelConfig(model="m", api_key="k")))
    assert answer["type"] == "error" and "no address" in answer["message"]
    assert driver.launches == 0


async def test_a_service_dsh_cannot_reach_fails_the_start_and_frees_its_model_name(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import nekoclaw_settings

    # One session at a time, so a name that was not given back would show.
    monkeypatch.setattr(nekoclaw_settings, "DSH_SESSION_SLOTS", 1)
    driver = dsh()
    unreachable = GatewaySpec(url="http://127.0.0.1:9/mcp", token="t")

    answer = await driver.started(make_spec(tmp_path, "s1", gateway=unreachable))

    assert answer["type"] == "error" and "refused the session" in answer["message"]
    assert driver.runtime.session_ids == []
    assert (await driver.started(make_spec(tmp_path, "s2")))["type"] == "started"
    # And with that one held, there is none for a third.
    full = await driver.started(make_spec(tmp_path, "s3"))
    assert full["type"] == "error" and "as many sessions as it can" in full["message"]
    await driver.closed("s2")
    assert (await driver.started(make_spec(tmp_path, "s3")))["type"] == "started"


@pytest.mark.parametrize(("close", "given_again"), [("slow", True), ("refuse", False)])
async def test_the_model_name_of_a_session_given_up_part_way_is_given_again_once_dsh_let_it_go(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch, close: str, given_again: bool
) -> None:
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_SESSION_SLOTS", 1)
    driver = dsh(FAKE_DSH_CLOSE=close)
    model_option = profile.model_option
    monkeypatch.setattr(profile, "model_option", lambda index: "not-a-model-dsh-knows")
    assert (await driver.started(make_spec(tmp_path, "s1")))["type"] == "error"
    monkeypatch.setattr(profile, "model_option", model_option)

    # dsh made that session and has not let go of it yet: what it may still ask
    # of the model under that name must not be taken for another session's.
    held = await driver.started(make_spec(tmp_path, "s2"))
    assert held["type"] == "error" and "as many sessions as it can" in held["message"]
    for _attempt in range(100):
        if not driver.runtime._closing_quietly:
            break
        await asyncio.sleep(0.05)

    # Once dsh confirmed the close, the name is free; when it did not, it stays out of use.
    assert (await driver.started(make_spec(tmp_path, "s3")))["type"] == ("started" if given_again else "error")


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


async def test_the_host_decides_with_the_built_in_runtimes_classification(tmp_path: Path, dsh) -> None:
    asked: list[dict[str, Any]] = []

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        asked.append({"session": session_id, **payload})
        return {"allow": payload["category"] != "outside_write", "reason": "no"}

    driver = dsh(HostCallbacks(decide_permission=decide))
    spec = make_spec(tmp_path)
    await driver.started(spec)
    inside = str(Path(spec.workdir, "note.txt"))
    outside = str(tmp_path / "outside.txt")
    calls = [
        ("write", {"file_path": inside, "content": "x"}),
        ("write", {"file_path": outside, "content": "x"}),
        ("read", {"file_path": outside}),
        ("pwsh", {"description": "list", "command": "dir", "workdir": str(tmp_path)}),
        ("todo_write", {"todos": []}),
    ]

    await driver.run("s1", "\n".join(f"!tool {name} {json.dumps(arguments)}" for name, arguments in calls))

    expected = []
    for name, arguments in calls[:4]:
        request = classify(name, arguments, Path(spec.workdir))
        expected.append({"session": "s1", **request.to_json(), "default": default_permission_decision(request)})
    assert asked == expected
    assert [payload["category"] for payload in asked] == ["workdir_files", "outside_write", "outside_read", "run_command"]
    # What the host said is what dsh did; a tool the host does not rule on is not asked about.
    assert [(row["name"], row["outcome"]) for row in driver.record("tool")] == [
        ("write", "allowed"),
        ("write", "rejected"),
        ("read", "allowed"),
        ("pwsh", "allowed"),
        ("todo_write", "allowed"),
    ]
    ends = driver.events.of("tool_end")
    assert [event["is_error"] for event in ends] == [False, True, False, False, False]
    starts = driver.events.of("tool_start")
    assert [(event["tool_name"], event["args"]) for event in starts] == calls
    assert [event["tool_call_id"] for event in starts] == [event["tool_call_id"] for event in ends]


async def test_a_gateway_tool_is_ruled_on_by_the_host_before_the_call_is_made(tmp_path: Path, dsh) -> None:
    asked: list[dict[str, Any]] = []

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        del session_id
        asked.append(payload)
        return {"allow": payload["arguments"].get("name") != "keyboard_type"}

    with _Service() as gateway:
        driver = dsh(HostCallbacks(decide_permission=decide))
        spec = make_spec(tmp_path, gateway=GatewaySpec(url=gateway.url, token="t"))
        await driver.started(spec)
        allowed = {"name": "browser_navigate", "arguments": {"url": "https://example.com"}}
        refused = {"name": "keyboard_type", "arguments": {"text": "x"}}

        await driver.run("s1", f"!gateway capability_call {json.dumps(allowed)}\n!gateway capability_call {json.dumps(refused)}")

        # dsh ends a call of such a tool after a minute; whoever has to be asked is asked before it.
        assert asked == [
            {"tool": "capability_call", "category": "gateway", "arguments": arguments, "workdir": spec.workdir, "default": "allow"}
            for arguments in (allowed, refused)
        ]
        # The call the host refused never reached the gateway.
        assert gateway.calls == [{"name": "capability_call", "arguments": allowed}]
        assert [row["failed"] for row in driver.record("service-call")] == [False, True]


async def test_a_tool_a_plugin_brought_is_asked_about_under_the_category_for_everything_else(tmp_path: Path, dsh) -> None:
    asked: list[dict[str, Any]] = []

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        del session_id
        asked.append(payload)
        return {"allow": payload["tool"] != "plugin_delete_everything"}

    driver = dsh(HostCallbacks(decide_permission=decide))
    spec = make_spec(tmp_path)
    await driver.started(spec)

    await driver.run("s1", '!tool plugin_weather {"city": "Oslo"}\n!tool plugin_delete_everything {}')

    assert asked == [
        {"tool": "plugin_weather", "category": "other", "arguments": {"city": "Oslo"}, "workdir": spec.workdir, "default": "allow"},
        {"tool": "plugin_delete_everything", "category": "other", "arguments": {}, "workdir": spec.workdir, "default": "allow"},
    ]
    assert [(row["name"], row["outcome"]) for row in driver.record("tool")] == [
        ("plugin_weather", "allowed"),
        ("plugin_delete_everything", "rejected"),
    ]


async def test_the_hosts_own_tools_are_not_asked_about(tmp_path: Path, dsh) -> None:
    asked: list[dict[str, Any]] = []

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        asked.append(payload)
        return {"allow": False}

    async def lesson(session_id: str, payload: dict[str, Any]) -> None:
        return None

    driver = dsh(HostCallbacks(decide_permission=decide, note_lesson=lesson))
    await driver.started(make_spec(tmp_path))
    await driver.run("s1", '!host note_lesson {"title": "t", "content": "c", "category": "lesson"}\n!tool todo_write {"todos": []}')

    assert asked == []
    assert driver.record("service-call")[0]["asked"] is False and not driver.record("service-call")[0]["failed"]
    assert driver.record("tool")[0]["outcome"] == "allowed"


async def test_what_dsh_saved_of_a_long_result_is_read_without_asking(tmp_path: Path, dsh) -> None:
    asked: list[dict[str, Any]] = []

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        del session_id
        asked.append(payload)
        return {"allow": payload["default"] == "allow"}

    driver = dsh(HostCallbacks(decide_permission=decide))
    await driver.started(make_spec(tmp_path))
    own = profile.session_spill_dir(driver.home, driver.record("session/new")[0]["session"]) / "0a1b2c3d4e5f-pwsh.txt"
    # What another session's tools returned: what was said in another commission is asked about.
    others = profile.session_spill_dir(driver.home, "another-session-of-dsh") / "0a1b2c3d4e5f-pwsh.txt"

    await driver.run(
        "s1",
        "\n".join(
            [
                f"!tool read {json.dumps({'file_path': str(own)})}",
                f"!tool write {json.dumps({'file_path': str(own), 'content': 'x'})}",
                f"!tool read {json.dumps({'file_path': str(others)})}",
                f"!tool grep {json.dumps({'pattern': 'x', 'path': str(profile.spill_dir(driver.home))})}",
            ]
        ),
    )

    assert [payload["category"] for payload in asked] == ["workdir_files", "outside_write", "outside_read", "outside_read"]
    assert [row["outcome"] for row in driver.record("tool")] == ["allowed", "rejected", "rejected", "rejected"]


async def test_without_a_host_to_ask_the_default_applies_and_asking_is_refused(tmp_path: Path, dsh) -> None:
    driver = dsh()
    spec = make_spec(tmp_path)
    await driver.started(spec)
    inside = json.dumps({"file_path": str(Path(spec.workdir, "note.txt")), "content": "x"})

    await driver.run("s1", f'!tool write {inside}\n!tool pwsh {{"description": "d", "command": "dir"}}')

    assert [(row["name"], row["outcome"]) for row in driver.record("tool")] == [("write", "allowed"), ("pwsh", "rejected")]


async def test_a_failing_approver_fails_closed(tmp_path: Path, dsh) -> None:
    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("the settings cannot be read")

    driver = dsh(HostCallbacks(decide_permission=decide))
    spec = make_spec(tmp_path)
    await driver.started(spec)
    await driver.run("s1", f"!tool write {json.dumps({'file_path': str(Path(spec.workdir, 'a.txt')), 'content': 'x'})}")
    assert driver.record("tool")[0]["outcome"] == "rejected"


async def test_a_call_that_does_not_say_what_it_touches_is_refused_and_nobody_is_asked(tmp_path: Path, dsh) -> None:
    asked: list[dict[str, Any]] = []

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        asked.append(payload)
        return {"allow": True}

    driver = dsh(HostCallbacks(decide_permission=decide))
    await driver.started(make_spec(tmp_path))
    calls = [
        # No path, no command, or not what the tool takes: nothing says it stays in the working directory.
        ("write", {"content": "x"}),
        ("edit", {"file_path": "", "old_string": "a", "new_string": "b"}),
        ("read", {"file_path": 7}),
        ("read_image", {}),
        ("pwsh", {"description": "d"}),
        ("bash", {"command": "ls", "workdir": ["/"]}),
        ("grep", {"pattern": "x", "path": 3}),
        ("write", ["a.txt", "x"]),
        ("pwsh", "dir"),
        # A search without a path is one of the working directory.
        ("glob", {"pattern": "*.txt"}),
    ]

    await driver.run("s1", "\n".join(f"!tool {name} {json.dumps(arguments)}" for name, arguments in calls))

    assert [row["outcome"] for row in driver.record("tool")] == ["rejected"] * 9 + ["allowed"]
    assert [(payload["tool"], payload["category"]) for payload in asked] == [("glob", "workdir_files")]


async def test_a_search_of_the_hosts_is_asked_about_before_it_is_called(tmp_path: Path, dsh) -> None:
    asked: list[dict[str, Any]] = []
    searched: list[dict[str, Any]] = []

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        asked.append(payload)
        return {"allow": "refuse" not in payload["arguments"]["queries"][0]}

    async def search(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        searched.append(payload)
        return {"results": [{"query": payload["queries"][0], "answer": "found", "sources": []}]}

    driver = dsh(HostCallbacks(decide_permission=decide, web_search=search))
    spec = make_spec(tmp_path)
    await driver.started(spec)

    await driver.run("s1", '!host web_search {"queries": ["cats"]}\n!host web_search {"queries": ["refuse me"]}')

    # dsh gives a call less than a minute: what the host may have to ask the user about
    # the search, it is asked before the call, where it can take the time it needs.
    assert asked == [
        {"tool": "web_search", "category": "host_tool", "arguments": {"queries": [query]}, "workdir": spec.workdir, "default": "allow"}
        for query in ("cats", "refuse me")
    ]
    assert searched == [{"queries": ["cats"]}]
    assert [(row["asked"], row["failed"]) for row in driver.record("service-call")] == [(True, False), (True, True)]


async def test_only_an_explicit_approval_allows(tmp_path: Path, dsh) -> None:
    replies: list[Any] = [{}, {"allow": "yes"}, {"allow": 1}, {"allow": None}, {"allow": True}]

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return replies.pop(0)

    driver = dsh(HostCallbacks(decide_permission=decide))
    await driver.started(make_spec(tmp_path))
    command = '!tool pwsh {"description": "d", "command": "dir"}'

    await driver.run("s1", "\n".join([command] * 5))

    assert [row["outcome"] for row in driver.record("tool")] == ["rejected"] * 4 + ["allowed"]


async def test_a_question_about_a_call_that_is_over_is_refused(tmp_path: Path, dsh) -> None:
    asked: list[dict[str, Any]] = []

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        asked.append(payload)
        return {"allow": True}

    driver = dsh(HostCallbacks(decide_permission=decide))
    await driver.started(make_spec(tmp_path))

    # A tool the host would be asked about, and would allow.
    await driver.run("s1", "!ask-finished plugin_weather")

    # What it was called with is not kept once it is over, and nothing is allowed on the strength of it.
    assert [row["outcome"] for row in driver.record("finished-ask")] == ["rejected"] and asked == []


async def test_a_question_about_a_call_that_was_never_announced_is_refused(tmp_path: Path, dsh) -> None:
    asked: list[dict[str, Any]] = []

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        asked.append(payload)
        return {"allow": True}

    driver = dsh(HostCallbacks(decide_permission=decide))
    await driver.started(make_spec(tmp_path))

    await driver.run("s1", "!ask-unknown")

    # Nothing is known of what an approval would allow: refused, and nobody is asked.
    assert [row["outcome"] for row in driver.record("unknown-ask")] == ["rejected"] and asked == []


async def test_a_question_to_the_user_ends_with_the_turn_that_is_stopped(tmp_path: Path, dsh) -> None:
    waiting = asyncio.Event()
    cancelled = asyncio.Event()

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        waiting.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {"allow": True}

    driver = dsh(HostCallbacks(decide_permission=decide))
    await driver.started(make_spec(tmp_path))
    await driver.runtime.prompt("s1", '!tool pwsh {"description": "d", "command": "dir"}\n!say never')
    await asyncio.wait_for(waiting.wait(), 10)

    await driver.runtime.abort("s1")
    ended = await driver.events.wait_ended("s1")

    assert ended["reason"] == "aborted"
    await asyncio.wait_for(cancelled.wait(), 5)
    assert driver.record("tool")[0]["outcome"] != "allowed"
    # The question is answered as the protocol asks of a stopped turn: cancelled, not refused.
    for _attempt in range(100):
        if driver.record("late-answer"):
            break
        await asyncio.sleep(0.05)
    assert [row["result"] for row in driver.record("late-answer")] == [{"outcome": {"outcome": "cancelled"}}]


# ---------------------------------------------------------------------------
# The host's tools
# ---------------------------------------------------------------------------


def _host(**extra: Any) -> tuple[HostCallbacks, list[tuple[str, dict[str, Any]]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    def callback(name: str, reply: dict[str, Any] | None = None):
        async def call(session_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
            del session_id
            calls.append((name, payload))
            return reply

        return call

    callbacks = HostCallbacks(
        ask_user=callback("ask_user", {"answers": []}),
        report=callback("report", {"message": "noted"}),
        note_lesson=callback("note_lesson"),
        web_search=callback("web_search", {"results": [{"query": "q", "answer": "found it", "sources": []}]}),
        subagent_start=callback("subagent_start", {"name": "agent-1"}),
        subagent_list=callback("subagent_list", {"agents": [{"name": "lead", "status": "working"}]}),
        subagent_send=callback("subagent_send"),
        subagent_interrupt=callback("subagent_interrupt"),
        **extra,
    )
    return callbacks, calls


async def test_the_hosts_tools_reach_dsh_as_a_service_and_keep_their_names_in_events(tmp_path: Path, dsh) -> None:
    host, calls = _host()
    driver = dsh(host)

    started = await driver.started(make_spec(tmp_path))
    await driver.run(
        "s1",
        '!host report {"status": "progress", "summary": "half way"}\n'
        '!host note_lesson {"title": "t", "content": "c", "category": "lesson"}\n'
        '!host web_search {"queries": ["q"]}',
    )

    offered = [
        "interrupt_agent",
        "list_agents",
        "note_lesson",
        "report",
        "send_message",
        "subagent",
        "wait_agent",
        "web_fetch",
        "web_search",
    ]
    # No question tool: dsh ends a call after a minute, a person takes longer.
    assert started["tools"] == offered and "ask_user_question" not in offered
    assert driver.record("session/new")[0]["services"] == {"host": offered}
    assert [name for name, _payload in calls] == ["report", "note_lesson", "web_search"]
    assert calls[0][1] == {"status": "progress", "summary": "half way", "details": None, "delivery": None}
    report = driver.events.of("report")[0]
    assert (report["status"], report["summary"]) == ("progress", "half way")
    assert [event["tool_name"] for event in driver.events.of("tool_start")] == ["report", "note_lesson", "web_search"]
    results = [row["text"] for row in driver.record("service-call")]
    assert results[0] == "report delivered (progress). noted" and results[1] == "lesson submitted."
    assert "found it" in results[2]


async def test_a_session_started_by_another_is_not_offered_the_subagent_tool(tmp_path: Path, dsh) -> None:
    from config.prompts.prompts_nekoclaw_dsh import DSH_SUBAGENT_SECTION

    host, _calls = _host()
    driver = dsh(host)
    (tmp_path / "lead").mkdir()
    lead = await driver.started(make_spec(tmp_path / "lead", "lead", instructions="Commission: tidy the desk."))
    started = await driver.started(
        make_spec(tmp_path, top_level=False, disabled_tools=["subagent"], instructions="Commission: the drawers.")
    )
    assert "subagent" in lead["tools"]
    assert "subagent" not in started["tools"] and "wait_agent" in started["tools"]

    await driver.run("lead", "go")
    await driver.run("s1", "go")

    # Told of starting another task agent where it can, and nowhere else.
    first = {row["session"]: row["text"] for row in driver.record("session/prompt")}
    assert DSH_SUBAGENT_SECTION in first[driver.record("session/new")[0]["session"]]
    assert "mcp__host__subagent" not in first[driver.record("session/new")[1]["session"]]


async def test_a_host_without_callbacks_hands_dsh_the_one_tool_that_needs_none(tmp_path: Path, dsh) -> None:
    driver = dsh()
    started = await driver.started(make_spec(tmp_path))
    assert started["tools"] == ["web_fetch"] and driver.record("session/new")[0]["services"] == {"host": ["web_fetch"]}


async def test_a_page_is_fetched_by_the_hosts_tool_which_reaches_no_local_address(tmp_path: Path, dsh) -> None:
    driver = dsh()
    await driver.started(make_spec(tmp_path))

    await driver.run("s1", '!host web_fetch {"url": "http://127.0.0.1:48911/"}')

    (call,) = driver.record("service-call")
    assert call["failed"] and "is not a public address" in call["text"]
    # Carried out by the host, so nobody is asked, and the event names it as the built-in runtime does.
    assert call["asked"] is False
    assert [event["tool_name"] for event in driver.events.of("tool_start")] == ["web_fetch"]


async def test_a_tool_the_host_refuses_fails_the_call(tmp_path: Path, dsh) -> None:
    async def report(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("the ledger is closed")

    driver = dsh(HostCallbacks(report=report))
    await driver.started(make_spec(tmp_path))
    await driver.run("s1", '!host report {"status": "ready", "summary": "done"}\n!host report {"status": "nonsense", "summary": "x"}')

    first, second = driver.record("service-call")
    assert first["failed"] and "the ledger is closed" in first["text"]
    assert second["failed"] and "status must be one of" in second["text"]
    assert driver.events.of("report") == []


async def test_a_waiting_session_is_handed_what_arrives_as_the_result_of_its_wait(tmp_path: Path, dsh) -> None:
    host, _calls = _host()
    driver = dsh(host)
    await driver.started(make_spec(tmp_path))

    await driver.runtime.prompt("s1", '!host wait_agent {"timeout_ms": 10000}\n!say went on')
    await driver.events.wait_for(lambda event: event["type"] == "tool_start")
    await driver.runtime.steer("s1", "[child task agent agent-1 finished: completed]\nall done")
    ended = await driver.events.wait_ended("s1")

    result = driver.record("service-call")[0]["text"]
    assert result == '{"timedOut":false}\n\n[child task agent agent-1 finished: completed]\nall done'
    # Handed over in the turn: it is not sent again as a turn of its own.
    assert len(driver.record("session/prompt")) == 1
    assert ended["reason"] == "completed" and ended["inputs"] == 2 and ended["text"] == "went on"


async def test_a_wait_that_nothing_arrives_for_times_out_inside_dshs_limit(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_HOST_TOOL_SECONDS", 5.4)
    host, _calls = _host()
    driver = dsh(host)
    await driver.started(make_spec(tmp_path))

    # Asked to wait an hour; dsh would fail the call after its minute.
    ended = await driver.run("s1", '!host wait_agent {"timeout_ms": 3600000}')

    assert driver.record("service-call")[0]["text"] == '{"timedOut":true}'
    assert ended["inputs"] == 1


async def test_a_call_that_takes_longer_than_dsh_allows_is_given_up(tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_HOST_TOOL_SECONDS", 0.3)
    stopped = asyncio.Event()

    async def search(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            stopped.set()
            raise
        return {"results": []}

    driver = dsh(HostCallbacks(web_search=search))
    await driver.started(make_spec(tmp_path))
    await driver.run("s1", '!host web_search {"queries": ["q"]}')

    call = driver.record("service-call")[0]
    assert call["failed"] and "given up" in call["text"]
    await asyncio.wait_for(stopped.wait(), 5)


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


async def test_model_requests_go_to_each_sessions_own_model_and_report_its_usage(tmp_path: Path, dsh) -> None:
    def respond(body: dict[str, Any]) -> list[dict[str, Any]]:
        return [sse_text(f"from {body['model']}"), sse_finish(), sse_usage(1200 if body["model"] == "model-a" else 70, 9)]

    with FakeOpenAIServer(respond) as server:
        driver = dsh()
        model_a = ModelConfig(model="model-a", base_url=server.base_url, api_key="key-a", extra_body={"enable_thinking": False})
        model_b = ModelConfig(model="model-b", base_url=server.base_url, api_key="key-b", auto_extra_body=False)
        await driver.started(make_spec(tmp_path, "s1", model=model_a))
        await driver.started(make_spec(tmp_path, "s2", model=model_b))

        ended_b = await driver.run("s2", "!model hello b")
        ended_a = await driver.run("s1", "!model hello a")

        # dsh knows the two by model names of the driver's; the real ones are put in on the way.
        assert [row["value"] for row in driver.record("session/set_config_option")] == [
            profile.model_option(0),
            profile.model_option(1),
        ]
        assert [(body["model"], headers["authorization"]) for body, headers in zip(server.requests, server.headers)] == [
            ("model-b", "Bearer key-b"),
            ("model-a", "Bearer key-a"),
        ]
        assert server.requests[1]["enable_thinking"] is False and "enable_thinking" not in server.requests[0]
        assert ended_a["text"] == "from model-a" and ended_b["text"] == "from model-b"
        usage = {event["session_id"]: event for event in driver.events.of("usage")}
        assert (usage["s1"]["input"], usage["s1"]["output"], usage["s1"]["context_tokens"]) == (1200, 9, 1209)
        assert (usage["s2"]["input"], usage["s2"]["total_tokens"]) == (70, 79)
        assert usage["s1"]["context_window"] == model_a.context_window
        # What dsh is configured with names neither a model's key nor its address.
        written = "".join(path.read_text(encoding="utf-8") for path in profile.config_dir(driver.home).iterdir())
        assert "key-a" not in written and "key-b" not in written and server.base_url not in written


async def test_two_dsh_processes_keep_their_sessions_model_requests_apart(tmp_path: Path, dsh) -> None:
    def respond(body: dict[str, Any]) -> list[dict[str, Any]]:
        return [sse_text(f"from {body['model']}"), sse_finish(), sse_usage(100 if body["model"] == "model-a" else 7, 1)]

    with FakeOpenAIServer(respond) as server:
        driver = dsh()
        # Another window is another dsh process, and each numbers its sessions' model names from the start.
        model_a = ModelConfig(model="model-a", base_url=server.base_url, api_key="key-a", context_window=64_000)
        model_b = ModelConfig(model="model-b", base_url=server.base_url, api_key="key-b", context_window=128_000)
        await driver.started(make_spec(tmp_path, "s1", model=model_a))
        await driver.started(make_spec(tmp_path, "s2", model=model_b))
        assert len(driver.runtime.process_ids) == 2
        assert [row["value"] for row in driver.record("session/set_config_option")] == [profile.model_option(0)] * 2

        ended_a = await driver.run("s1", "!model hello a")
        ended_b = await driver.run("s2", "!model hello b")
        again_a = await driver.run("s1", "!model hello again")

        # Each session's requests reached its own model with its own key, and were counted for it.
        assert (ended_a["text"], ended_b["text"], again_a["text"]) == ("from model-a", "from model-b", "from model-a")
        assert [headers["authorization"] for headers in server.headers] == ["Bearer key-a", "Bearer key-b", "Bearer key-a"]
        assert [(event["session_id"], event["input"]) for event in driver.events.of("usage")] == [("s1", 100), ("s2", 7), ("s1", 100)]

        # The one that is closed takes nothing of the other's with it.
        await driver.closed("s2")
        assert (await driver.run("s1", "!model still here"))["text"] == "from model-a"


async def test_a_slot_dsh_did_not_let_go_of_is_given_to_no_other_session(tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_SESSION_SLOTS", 2)
    driver = dsh(FAKE_DSH_CLOSE="refuse")
    await driver.started(make_spec(tmp_path, "s1"))
    endpoint = driver.runtime.endpoint
    (process_token,) = endpoint._process_tokens

    await driver.closed("s1")
    await driver.started(make_spec(tmp_path, "s2"))
    full = await driver.started(make_spec(tmp_path, "s3"))

    # dsh may still hold the first session and go on asking the model under its name:
    # that name is given to no other session of the process, and finds nobody.
    assert driver.record("close-refused")
    assert [row["value"] for row in driver.record("session/set_config_option")] == [profile.model_option(0), profile.model_option(1)]
    assert full["type"] == "error" and "as many sessions as it can" in full["message"]
    async with httpx.AsyncClient(trust_env=False) as client:
        stale = await client.post(
            f"{endpoint.model_url}/chat/completions",
            headers={"Authorization": f"Bearer {process_token}"},
            json={"model": profile.slot_model(0), "messages": []},
        )
    assert stale.status_code == 404


async def test_the_request_after_a_closing_report_is_not_sent_to_the_model(tmp_path: Path, dsh) -> None:
    def respond(body: dict[str, Any]) -> list[dict[str, Any]]:
        return [sse_text("the model"), sse_finish(), sse_usage(50, 5)]

    with FakeOpenAIServer(respond) as server:
        host, _calls = _host()
        driver = dsh(host)
        await driver.started(make_spec(tmp_path, model=ModelConfig(model="m", base_url=server.base_url, api_key="k")))

        ended = await driver.run("s1", '!say all done\n!model-after-report {"status": "ready", "summary": "done"}')
        assert server.requests == [] and driver.events.of("usage") == []
        # The word put in the model's place ends dsh's turn; it is nothing the
        # task agent said, and no event tells of it.
        assert _texts(driver.events) == "all done" and ended["text"] == "all done"
        assert "Reported." in driver.record("model")[0]["text"]

        # After a report the commission goes on from, the model is asked.
        for status in ("progress", "waiting", "blocked"):
            await driver.run("s1", '!model-after-report {"status": "%s", "summary": "half"}' % status)
        assert len(server.requests) == 3 and len(driver.events.of("usage")) == 3
        assert _texts(driver.events) == "all done" + "the model" * 3


async def test_a_session_that_was_closed_can_no_longer_reach_the_model_or_the_hosts_tools(tmp_path: Path, dsh) -> None:
    import httpx

    host, _calls = _host()
    driver = dsh(host)
    await driver.started(make_spec(tmp_path, model=ModelConfig(model="m", base_url="http://127.0.0.1:9/v1", api_key="k")))
    endpoint = driver.runtime.endpoint
    model_url, tools_url = f"{endpoint.model_url}/chat/completions", endpoint.tools_url
    session_token = endpoint._by_session["s1"]
    (process_token,) = endpoint._process_tokens
    await driver.closed("s1")

    async with httpx.AsyncClient(trust_env=False) as client:
        model = await client.post(
            model_url,
            headers={"Authorization": f"Bearer {process_token}"},
            json={"model": profile.slot_model(0), "messages": []},
        )
        tools = await client.post(
            tools_url,
            headers={"Authorization": f"Bearer {session_token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        nobody = await client.post(model_url, json={"model": profile.slot_model(0), "messages": []})

    assert model.status_code == 404 and tools.status_code == 401 and nobody.status_code == 401


# ---------------------------------------------------------------------------
# The gateway
# ---------------------------------------------------------------------------


class _Service:
    """A minimal MCP service over HTTP, as the host's gateway is one."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.tokens: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                del format, args

            def do_POST(self) -> None:  # noqa: N802
                message = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))
                owner.tokens.append(self.headers.get("authorization", ""))
                method = message.get("method")
                if method == "initialize":
                    result: Any = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "g"}}
                elif method == "tools/list":
                    result = {"tools": [{"name": "capability_call", "description": "d", "inputSchema": {"type": "object"}}]}
                elif method == "tools/call":
                    owner.calls.append(message["params"])
                    result = {"content": [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}], "isError": False}
                else:
                    self._send({"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32601, "message": "unknown"}})
                    return
                self._send({"jsonrpc": "2.0", "id": message.get("id"), "result": result})

            def _send(self, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/mcp"

    def __enter__(self) -> "_Service":
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(5)


async def test_the_gateway_is_handed_to_dsh_with_the_sessions_token(tmp_path: Path, dsh) -> None:
    with _Service() as gateway:
        driver = dsh()
        spec = make_spec(tmp_path, gateway=GatewaySpec(url=gateway.url, token="commission-token"))
        await driver.started(spec)

        await driver.run("s1", '!gateway capability_call {"name": "browser_navigate", "arguments": {"url": "https://example.com"}}')

        # Next to the host's own service, which here holds the one tool that needs no host.
        assert driver.record("session/new")[0]["services"] == {"host": ["web_fetch"], "gateway": ["capability_call"]}
        assert set(gateway.tokens) == {"Bearer commission-token"}
        assert gateway.calls == [{"name": "capability_call", "arguments": {"name": "browser_navigate", "arguments": {"url": "https://example.com"}}}]
        # In events the tool has the name the gateway gives it, as with the built-in runtime.
        assert driver.events.of("tool_start")[0]["tool_name"] == "capability_call"
        assert driver.events.of("tool_end")[0]["text"] == "first\nsecond"


# ---------------------------------------------------------------------------
# Sessions that take several commissions
# ---------------------------------------------------------------------------


async def test_a_session_that_goes_on_with_another_commission_is_given_its_opening_message_and_nothing_else(
    tmp_path: Path, dsh
) -> None:
    driver = dsh()
    spec = make_spec(tmp_path, instructions="You work for Lan.", reply_language="English")
    await driver.started(spec)
    position = len(driver.events.events)
    await driver.runtime.prompt("s1", "Commission 1\ntidy the desk", opening=True)
    await driver.events.wait_ended("s1", start=position)
    await driver.closed("s1")

    # Opened again for the next commission of the session, with other instructions by now.
    again = make_spec(tmp_path, instructions="You work for Lan. Index: three entries.", reply_language="English")
    started = await driver.started(again)
    position = len(driver.events.events)
    await driver.runtime.prompt("s1", "Commission 2\nwater the plants", opening=True)
    await driver.events.wait_ended("s1", start=position)

    assert started["resumed"] is True
    said = [row["text"] for row in driver.record("session/prompt")]
    # What the host tells the session was said once, when it began; a commission is the message that opens it.
    assert "You work for Lan." in said[0] and said[0].rstrip().endswith("Commission 1\ntidy the desk")
    assert said[1] == "Commission 2\nwater the plants"


def test_the_shape_of_a_dsh_process_tells_where_its_conversations_are_summed_up_from() -> None:
    from nekoclaw.dsh.driver import _shape_tag, shape_of

    preset = shape_of(ModelConfig(model="m", api_key="k", context_window=200_000))
    told = shape_of(ModelConfig(model="m", api_key="k", context_window=200_000, compact_at=170_000))
    earlier = shape_of(ModelConfig(model="m", api_key="k", context_window=200_000, compact_at=150_000))

    assert preset.compact_ratio == told.compact_ratio == 0.85 and earlier.compact_ratio == 0.75
    # A process is started for one shape: sessions that differ in this do not share one.
    assert _shape_tag(preset) == _shape_tag(told) != _shape_tag(earlier)


async def test_a_window_a_sessions_model_endpoint_states_is_told_to_the_host_once(tmp_path: Path, dsh) -> None:
    driver = dsh()
    model = ModelConfig(model="model-a", base_url="http://model.test/v1", api_key="k", context_window=200_000)
    await driver.started(make_spec(tmp_path, "s1", model=model))

    # As the forwarder hands it on when the endpoint refused a request as too long.
    driver.runtime._on_window("s1", model, 32_768, "The maximum context length is 32768 tokens.")
    driver.runtime._on_window("s1", model, 32_768, "The maximum context length is 32768 tokens.")
    # As large as the session's own, or larger: nothing to learn. Of a session that is gone: nobody to tell.
    driver.runtime._on_window("s1", model, 200_000, "maximum context length is 200000 tokens")
    driver.runtime._on_window("s1", model, 400_000, "maximum context length is 400000 tokens")
    driver.runtime._on_window("gone", model, 8_000, "maximum context length is 8000 tokens")
    # From a request that outlasted its session: under the id there is another by now, with
    # another model or another endpoint, and the window is not about that one's.
    other = ModelConfig(model="model-b", base_url="http://model.test/v1", api_key="k", context_window=200_000)
    elsewhere = ModelConfig(model="model-a", base_url="http://other.test/v1", api_key="k", context_window=200_000)
    driver.runtime._on_window("s1", other, 16_000, "maximum context length is 16000 tokens")
    driver.runtime._on_window("s1", elsewhere, 16_000, "maximum context length is 16000 tokens")
    # Smaller than a session can be managed for: the smallest that can.
    driver.runtime._on_window("s1", model, 2_048, "maximum context length is 2048 tokens")

    told = driver.events.of("model_capability")
    assert [(event["session_id"], event["context_window"], event["supports_images"]) for event in told] == [
        ("s1", 32_768, None),
        ("s1", 4_000, None),
    ]
    assert "maximum context length is 32768" in told[0]["reason"]
