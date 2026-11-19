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

from config.prompts.prompts_nekoclaw import USER_LANGUAGE_GUIDANCE
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
    spec = make_spec(tmp_path, instructions="Commission: tidy the desk.")
    await driver.started(spec)

    await driver.run("s1", "first")
    await driver.run("s1", "second")

    first, second = (row["text"] for row in driver.record("session/prompt"))
    assert first.startswith("[From the host, for this commission]\nCommission: tidy the desk.")
    assert first.endswith("[Your work]\nfirst")
    # Which language to write to the user in is named by each commission's
    # opening message; what the session is told once names none.
    assert USER_LANGUAGE_GUIDANCE in first
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
    # Each text is told of as taken once dsh has it: the first after its own turn, the other two after the next.
    kinds = [(event["type"], event.get("inputs")) for event in driver.events.events if event["type"] in ("taken", "text")]
    assert [kind for kind in kinds if kind[0] == "taken"] == [("taken", 1), ("taken", 2), ("taken", 3)]
    # The first between what the two turns said, the others after the second.
    replies = [index for index, kind in enumerate(kinds) if kind[0] == "text"]
    assert len(replies) == 2 and replies[0] < kinds.index(("taken", 1)) < replies[1]
    assert kinds[-2:] == [("taken", 2), ("taken", 3)]
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

    await driver.runtime.prompt("s1", "another", serial=7)
    refused = await driver.events.wait_for(lambda event: event["type"] == "error")

    # Refused under the number it was sent with: the sender may have sent the next by now.
    assert refused["command"] == "prompt" and "in progress" in refused["message"] and refused["serial"] == 7
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
    # Texts are told of by the number they were given under: the one that was dropped
    # leaves its number unused, and what came after it is the third.
    assert [event["inputs"] for event in driver.events.of("taken")] == [1, 3]
    assert again["inputs"] == 3


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
    # dsh did not take it: it is not told of as taken, and may have to be sent again.
    assert driver.events.of("taken") == []


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

    assert started["resumed"] is True and second.runtime.resumed("s1") is True
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
    # Told to the host as well, which opens the commission in it anew.
    assert second.runtime.resumed("s1") is False and second.runtime.resumed("no such session") is False
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
    await driver.run("s1", '!host note_lesson {"title": "t", "content": "c", "category": "lesson", "confidence": "verified"}\n!tool todo_write {"todos": []}')

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
        '!host note_lesson {"title": "t", "content": "c", "category": "lesson", "confidence": "verified"}\n'
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
    # Told of as taken once the turn it was handed over in has run to its end, and not when it was handed over.
    order = [(event["type"], event.get("inputs")) for event in driver.events.events if event["type"] in ("taken", "tool_end")]
    assert order == [("tool_end", None), ("taken", 1), ("taken", 2)]


async def test_what_a_waiting_session_was_handed_in_a_turn_that_is_cut_short_is_not_told_of_as_taken(
    tmp_path: Path, dsh
) -> None:
    host, _calls = _host()
    driver = dsh(host)
    await driver.started(make_spec(tmp_path))

    await driver.runtime.prompt("s1", '!host wait_agent {"timeout_ms": 10000}\n!sleep 30')
    await driver.events.wait_for(lambda event: event["type"] == "tool_start")
    await driver.runtime.steer("s1", "all done")
    await driver.events.wait_for(lambda event: event["type"] == "tool_end")
    await driver.runtime.abort("s1")
    ended = await driver.events.wait_ended("s1")

    # Whether dsh took the result of that call is not known: whoever sent the text may have to send it again.
    assert ended["reason"] == "aborted"
    assert [event["inputs"] for event in driver.events.of("taken")] == [1]


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
    spec = make_spec(tmp_path, instructions="You work for Lan.")
    await driver.started(spec)
    position = len(driver.events.events)
    await driver.runtime.prompt("s1", "Commission 1\ntidy the desk", opening=True)
    await driver.events.wait_ended("s1", start=position)
    await driver.closed("s1")

    # Opened again for the next commission of the session, with other instructions by now.
    again = make_spec(tmp_path, instructions="You work for Lan. Index: three entries.")
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


async def _until(check: Any, what: str, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        assert asyncio.get_running_loop().time() < deadline, f"{what} did not happen within {timeout}s"
        await asyncio.sleep(0.02)


async def test_a_call_of_a_run_that_is_over_is_handed_nothing_of_the_next(tmp_path: Path, dsh) -> None:
    host, _calls = _host()
    driver = dsh(host)
    await driver.started(make_spec(tmp_path))
    runtime = driver.runtime
    session = runtime._sessions["s1"]
    run = runtime._run
    # The fake keeps the second run's turn open until this is written: what is steered in
    # meanwhile arrives during that turn, however long the machine takes over it.
    go = tmp_path / "go"
    second = f"!until {go}\n!say went on"

    let_run = asyncio.Event()
    runs: list[Any] = []

    async def a_moment_later(session: Any) -> None:
        # Which goes on first when a text arrives for a session with no run under way, the
        # run it starts or a call that was waiting, is up to the loop. Here the call does:
        # the next run waits until what the call was handed has been looked at.
        runs.append(session)
        if len(runs) > 1:
            await let_run.wait()
        await run(session)

    runtime._run = a_moment_later  # type: ignore[method-assign]

    await runtime.prompt("s1", "!sleep 30")
    await _until(lambda: len(driver.record("session/prompt")) == 1, "the first run being with dsh")
    await runtime.abort("s1")
    # A call of that run which dsh has not called off: it goes on waiting for what arrives.
    stale = asyncio.ensure_future(runtime._take_input(session, 30.0))
    try:
        await asyncio.sleep(0)
        ended = await driver.events.wait_ended("s1")
        assert ended["reason"] == "aborted" and not stale.done()

        position = len(driver.events.events)
        await runtime.prompt("s1", second)
        # What arrives now starts the next run: the call of the one that is over is handed
        # nothing, though it is the first to see it.
        assert await asyncio.wait_for(stale, 5.0) == []
        assert [str(queued) for queued in session.queue] == [second]
        let_run.set()
        await _until(lambda: len(driver.record("session/prompt")) == 2, "the second run being with dsh")
        await runtime.steer("s1", "a note for the run under way")
        go.write_text("", encoding="utf-8")
        ended = await driver.events.wait_ended("s1", start=position)
    finally:
        let_run.set()
        stale.cancel()
        go.write_text("", encoding="utf-8")

    # So it goes to dsh as a turn of its own, and is told of as taken once dsh has that.
    prompts = driver.record("session/prompt")
    assert len(prompts) == 3 and "went on" in json.dumps(prompts[1])
    assert "a note for the run under way" in json.dumps(prompts[2])
    assert ended["reason"] == "completed" and ended["inputs"] == 3
    assert [event["inputs"] for event in driver.events.events[position:] if event["type"] == "taken"] == [2, 3]


async def _handed_to_a_call(
    tmp_path: Path, dsh: Any, then: str = "!say went on", before: str = ""
) -> tuple[Any, Any, Path, list[Any]]:
    """A session whose turn is held open, and a note that a call of that turn was handed; with whom to tell how its answer fared.

    ``then`` is what the turn does once it is let go, ``before`` what it does first.
    """
    from nekoclaw.dsh.host_tools import ON_ANSWERED

    host, _calls = _host()
    driver = dsh(host)
    await driver.started(make_spec(tmp_path))
    runtime = driver.runtime
    session = runtime._sessions["s1"]
    go = tmp_path / "go"

    await runtime.prompt("s1", f"{before}!until {go}\n{then}")
    await _until(lambda: len(driver.record("session/prompt")) == 1, "the turn being with dsh")
    await runtime.steer("s1", "a note that arrives during the turn")
    # A call of the turn takes it, as whoever serves the call has it carried out.
    told: list[Any] = []
    marked = ON_ANSWERED.set(told)
    try:
        assert await runtime._take_input(session, 1.0) == ["a note that arrives during the turn"]
    finally:
        ON_ANSWERED.reset(marked)
    assert session.queue == [] and session.awaited == [2] and len(told) == 1
    return driver, session, go, told


def _taken(driver: Any) -> list[int]:
    return [event["inputs"] for event in driver.events.of("taken")]


def _sent_again(driver: Any, ended: dict[str, Any]) -> None:
    """The note went to dsh as a turn of its own, and was told of as taken once dsh had that."""
    prompts = driver.record("session/prompt")
    assert len(prompts) == 2 and "a note that arrives during the turn" in json.dumps(prompts[1])
    assert ended["reason"] == "completed" and ended["inputs"] == 2
    taken = [(index, event["inputs"]) for index, event in enumerate(driver.events.events) if event["type"] == "taken"]
    assert [number for _index, number in taken] == [1, 2]
    said = [index for index, event in enumerate(driver.events.events) if event["type"] == "text"]
    assert said and said[0] < taken[1][0]


async def test_what_a_call_was_handed_is_queued_again_when_its_answer_does_not_reach_dsh(tmp_path: Path, dsh) -> None:
    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh)
    try:
        # dsh hangs up on that one call before it has the answer, its turn going on.
        told[0](False)
        # Not in dsh's conversation: it is not told of as taken, and waits to be sent.
        assert session.awaited == [] and [str(text) for text in session.queue] == ["a note that arrives during the turn"]
        assert _taken(driver) == []
    finally:
        go.write_text("", encoding="utf-8")

    _sent_again(driver, await driver.events.wait_ended("s1"))


async def test_the_end_of_a_turn_waits_to_hear_whether_the_answer_of_its_call_reached_dsh(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh)
    settled = driver.runtime._answers_settled
    waiting = asyncio.Event()

    async def looking(session: Any) -> None:
        waiting.set()
        await settled(session)

    monkeypatch.setattr(driver.runtime, "_answers_settled", looking)
    # dsh ends the turn first; that it hung up on the call is seen a moment later.
    go.write_text("", encoding="utf-8")
    await asyncio.wait_for(waiting.wait(), 10.0)
    assert _taken(driver) == [1]
    told[0](False)

    # Not told of as taken on the strength of the turn having ended: queued again, and sent.
    _sent_again(driver, await driver.events.wait_ended("s1"))


async def test_a_text_whose_answer_went_nowhere_is_not_told_of_as_taken_though_its_turn_is_being_stopped(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh)
    settled = driver.runtime._answers_settled
    waiting = asyncio.Event()

    async def looking(session: Any) -> None:
        waiting.set()
        await settled(session)

    monkeypatch.setattr(driver.runtime, "_answers_settled", looking)
    go.write_text("", encoding="utf-8")
    await asyncio.wait_for(waiting.wait(), 10.0)
    # The turn is stopped while its end waits to hear of the answer; then it is heard that the
    # answer went nowhere.
    await driver.runtime.abort("s1")
    told[0](False)
    await driver.events.wait_ended("s1")

    # Not queued again (the stop sees to what it was handed), and not told of as taken either:
    # it never was in dsh's conversation.
    assert _taken(driver) == [1] and session.queue == []
    assert len(driver.record("session/prompt")) == 1


async def test_a_text_whose_answer_got_to_dsh_is_told_of_as_taken_with_the_end_of_its_turn(tmp_path: Path, dsh) -> None:
    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh)
    told[0](True)
    assert session.awaited == [2] and session.queue == []
    go.write_text("", encoding="utf-8")
    ended = await driver.events.wait_ended("s1")

    # In dsh's conversation with the answer: told of as the turn ends, and not sent again.
    assert ended["reason"] == "completed" and ended["inputs"] == 2 and ended["open_inputs"] == []
    assert _taken(driver) == [1, 2] and len(driver.record("session/prompt")) == 1


@pytest.mark.parametrize("reached", [False, True])
async def test_a_text_whose_answer_is_heard_of_after_its_turn_ended_is_dealt_with_then(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch, reached: bool
) -> None:
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_ANSWER_SETTLE_SECONDS", 0.05)
    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh)
    go.write_text("", encoding="utf-8")
    ended = await driver.events.wait_ended("s1")

    # Whether dsh has it is not known as the turn ends: it is not told of as taken, and the
    # end of the run names it as still open.
    assert ended["reason"] == "completed" and _taken(driver) == [1] and ended["open_inputs"] == [2]
    assert session.run is None and len(session.unsettled) == 1
    mark = len(driver.events.events)
    # Heard of only now, with the run over and the session still open.
    told[0](reached)
    assert not session.unsettled
    if reached:
        # It got there after all: told of as taken now, as of the run that is over, and not sent again.
        assert _taken(driver) == [1, 2] and session.run is None
        assert [event["late"] for event in driver.events.of("taken")] == [False, True]
        # And of which run: the one whose end was told of with that number.
        run = driver.events.of("taken")[-1]["run"]
        assert run is not None and run == ended["run"]
        assert len(driver.record("session/prompt")) == 1
        return
    # It did not: nobody else would give the session the text while it stays open, so it is
    # queued again and goes to dsh as a turn of its own.
    _sent_again(driver, await driver.events.wait_ended("s1", start=mark))


async def test_what_a_call_of_a_run_that_was_stopped_was_handed_is_not_queued_again(tmp_path: Path, dsh) -> None:
    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh)
    try:
        # The run is stopped; that the answer of its call went nowhere is heard of after that.
        await driver.runtime.abort("s1")
        ended = await driver.events.wait_ended("s1")
        assert ended["reason"] == "aborted" and not session.unsettled
        told[0](False)
        # What it was handed went with the stop: not queued again, and no run started for it.
        assert session.queue == [] and session.run is None
        assert len(driver.record("session/prompt")) == 1 and _taken(driver) == [1]
    finally:
        go.write_text("", encoding="utf-8")


async def test_an_answer_that_went_nowhere_begins_no_turn_while_the_runtime_shuts_down(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_ANSWER_SETTLE_SECONDS", 0.05)
    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh)
    go.write_text("", encoding="utf-8")
    await driver.events.wait_ended("s1")
    assert len(session.unsettled) == 1
    # Heard of while the runtime is shutting down: no turn is begun behind its back.
    driver.runtime._shutdowns += 1
    try:
        told[0](False)
        assert session.queue == [] and session.run is None
        assert len(driver.record("session/prompt")) == 1
    finally:
        driver.runtime._shutdowns -= 1


async def test_what_a_turn_that_failed_left_open_is_not_waited_for_at_the_end_of_the_next(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_ANSWER_SETTLE_SECONDS", 30.0)
    # A turn that is cut off by an error, the answer of one of its calls still on its way.
    driver, session, go, _told = await _handed_to_a_call(tmp_path, dsh, then="!fail the model gave out")
    go.write_text("", encoding="utf-8")
    ended = await driver.events.wait_ended("s1")
    assert ended["reason"] == "error" and _taken(driver) == []
    mark = len(driver.events.events)

    await driver.runtime.prompt("s1", "!say hello")
    ended = await driver.events.wait_ended("s1", timeout=10.0, start=mark)

    # The next run does not sit out the wait for it, nor tell of what that call was handed as
    # taken by a turn of its own: it is dealt with when it is heard of.
    assert ended["reason"] == "completed" and _taken(driver) == [3]
    assert len(session.unsettled) == 1 and session.answering == []


@pytest.mark.parametrize("reached", [False, True])
async def test_a_text_whose_answer_is_heard_of_after_its_turn_failed_is_dealt_with_then(
    tmp_path: Path, dsh, reached: bool
) -> None:
    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh, then="!fail the model gave out")
    go.write_text("", encoding="utf-8")
    ended = await driver.events.wait_ended("s1")
    assert ended["reason"] == "error" and _taken(driver) == [] and len(session.unsettled) == 1
    # (Open with it is what the turn began with: whether dsh had begun the turn is not known.)
    assert ended["open_inputs"] == [1, 2]
    mark = len(driver.events.events)
    # Heard of only now, with the run over and the session still open.
    told[0](reached)
    assert not session.unsettled
    if reached:
        # In dsh's conversation after all: told of as taken, as of the run that failed, and
        # not sent again. With what the turn began with, which dsh had begun as this shows.
        late = [(event["inputs"], event["late"], event["run"]) for event in driver.events.of("taken")]
        assert late == [(1, True, ended["run"]), (2, True, ended["run"])]
        assert session.run is None and len(driver.record("session/prompt")) == 1
        return
    # It is not: queued again, and it goes to dsh as a turn of its own.
    again = await driver.events.wait_ended("s1", start=mark)
    prompts = driver.record("session/prompt")
    assert len(prompts) == 2 and "a note that arrives during the turn" in json.dumps(prompts[1])
    assert again["reason"] == "completed" and _taken(driver) == [2]


async def test_a_text_that_a_later_call_of_its_turn_got_to_dsh_is_told_of_as_taken(tmp_path: Path, dsh) -> None:
    from nekoclaw.dsh.host_tools import ON_ANSWERED

    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh)
    try:
        # The answer of the call went nowhere, and the text waits again. The next call of the
        # same turn is handed it, and that answer gets to dsh.
        told[0](False)
        again: list[Any] = []
        marked = ON_ANSWERED.set(again)
        try:
            assert await driver.runtime._take_input(session, 1.0) == ["a note that arrives during the turn"]
        finally:
            ON_ANSWERED.reset(marked)
        again[0](True)
    finally:
        go.write_text("", encoding="utf-8")
    ended = await driver.events.wait_ended("s1")

    # In dsh's conversation with the second answer: told of as the turn ends, whatever became
    # of the first, and not sent again.
    assert ended["reason"] == "completed" and ended["inputs"] == 2
    assert _taken(driver) == [1, 2] and len(driver.record("session/prompt")) == 1


@pytest.mark.parametrize("reached", [False, True])
async def test_what_a_run_that_was_stopped_as_it_ended_left_open_is_not_queued_again_later(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch, reached: bool
) -> None:
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_ANSWER_SETTLE_SECONDS", 0.2)
    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh)
    settled = driver.runtime._answers_settled
    waiting = asyncio.Event()

    async def looking(session: Any) -> None:
        waiting.set()
        await settled(session)

    monkeypatch.setattr(driver.runtime, "_answers_settled", looking)
    go.write_text("", encoding="utf-8")
    await asyncio.wait_for(waiting.wait(), 10.0)
    # The run is stopped while its end waits to hear of the answer, and that wait runs out.
    await driver.runtime.abort("s1")
    ended = await driver.events.wait_ended("s1")
    assert ended["reason"] == "aborted" and len(session.unsettled) == 1
    # (Named as open by the end of the run though it was stopped: it may still be said to
    # have got there.)
    assert ended["open_inputs"] == [2]
    # Heard of only now, with the session not being stopped any more.
    told[0](reached)

    # What the call was handed went with the stop: where the answer went nowhere, the text is
    # not queued again, and no turn is begun for it. (Where it got there, the text is in
    # dsh's conversation, and is told of as taken by the run that was stopped.)
    assert not session.unsettled and session.queue == [] and session.run is None
    assert len(driver.record("session/prompt")) == 1
    assert [(event["inputs"], event["late"]) for event in driver.events.of("taken")] == (
        [(1, False), (2, True)] if reached else [(1, False)]
    )


@pytest.mark.parametrize("during", [False, True])
async def test_what_a_run_that_ended_left_open_is_queued_again_though_a_later_run_was_stopped(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch, during: bool
) -> None:
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_ANSWER_SETTLE_SECONDS", 0.05)
    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh)
    go.write_text("", encoding="utf-8")
    ended = await driver.events.wait_ended("s1")
    assert ended["reason"] == "completed" and len(session.unsettled) == 1
    mark = len(driver.events.events)
    # The next run is stopped, the answer of the earlier run's call still on its way.
    await driver.runtime.prompt("s1", f"!until {tmp_path / 'never'}")
    await _until(lambda: len(driver.record("session/prompt")) == 2, "the next turn being with dsh")
    await driver.runtime.abort("s1")
    if during:
        # It went nowhere, which is heard of while the stop is still under way: kept until
        # the stop is through, and not lost with what the run being stopped was handed.
        told[0](False)
        assert session.aborting and session.queue == [] and len(session.kept_back) == 1
    stopped = await driver.events.wait_ended("s1", start=mark)
    assert stopped["reason"] == "aborted"
    if not during:
        # Or it is heard of once the stop is through.
        assert len(session.unsettled) == 1
        told[0](False)

    # The run it was of was not the one that was stopped: its text is queued again, and goes
    # to dsh as a turn of its own.
    await _until(lambda: len(driver.record("session/prompt")) == 3, "the text going to dsh again")
    await driver.events.wait_for(
        lambda event: event["type"] == "ended" and event["reason"] == "completed", start=mark
    )
    assert "a note that arrives during the turn" in json.dumps(driver.record("session/prompt")[2])
    assert not session.kept_back and not session.unsettled


@pytest.mark.parametrize("known", [False, True])
async def test_what_a_turn_that_failed_as_it_was_being_stopped_was_handed_goes_with_the_stop(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch, known: bool
) -> None:
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_ANSWER_SETTLE_SECONDS", 0.05)
    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh, before="!stopped-fails\n")
    try:
        if known:
            # The answer got to dsh.
            told[0](True)
        # Then the run is stopped, which dsh answers with an error, and not by saying that
        # the turn was cancelled.
        await driver.runtime.abort("s1")
        ended = await driver.events.wait_ended("s1")
    finally:
        go.write_text("", encoding="utf-8")

    # A stop all the same: what the turn's calls were handed is not told of as taken on the
    # strength of an answer that dsh may have dropped with the turn, and none of it is open.
    assert ended["reason"] == "error" and ended["open_inputs"] == []
    assert _taken(driver) == [] and not session.unsettled and session.awaited == []
    # Nor is an answer of it that is still on its way taken for one of the next run.
    mark = len(driver.events.events)
    await driver.runtime.prompt("s1", "!say hello")
    again = await driver.events.wait_ended("s1", start=mark)
    assert again["reason"] == "completed" and again["open_inputs"] == [] and _taken(driver) == [3]


async def _left_open_by_a_run_that_goes_on(
    tmp_path: Path, dsh: Any, monkeypatch: pytest.MonkeyPatch, before: str = ""
) -> tuple[Any, Any, Path, list[Any]]:
    """A run in its second turn, the answer of a call of its first turn still on its way; with whom to tell how it fared.

    ``before`` is what the second turn does first.
    """
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_ANSWER_SETTLE_SECONDS", 0.05)
    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh)
    on = tmp_path / "on"
    # More is said meanwhile: the run goes on with a second turn once the first is over.
    await driver.runtime.steer("s1", f"{before}!until {on}\n!say more")
    go.write_text("", encoding="utf-8")
    await _until(lambda: len(driver.record("session/prompt")) == 2, "the second turn being with dsh")
    assert len(session.unsettled) == 1 and _taken(driver) == [1]
    return driver, session, on, told


async def test_a_text_whose_answer_is_heard_of_while_its_run_goes_on_is_told_of_as_any_other(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, session, on, told = await _left_open_by_a_run_that_goes_on(tmp_path, dsh, monkeypatch)
    try:
        # It got there. The run is still under way: its end is yet to be announced, and is
        # what tells of the text; said to be of a run that is over, there would be no such run.
        told[0](True)
        said = [(event["inputs"], event["late"], event.get("run")) for event in driver.events.of("taken")]
        assert said == [(1, False, None), (2, False, None)]
    finally:
        on.write_text("", encoding="utf-8")
    ended = await driver.events.wait_ended("s1")

    assert ended["reason"] == "completed" and ended["inputs"] == 3
    assert _taken(driver) == [1, 2, 3] and not session.unsettled


async def test_what_the_run_being_stopped_left_open_itself_is_not_kept_until_the_stop_is_through(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, session, on, told = await _left_open_by_a_run_that_goes_on(tmp_path, dsh, monkeypatch)
    try:
        await driver.runtime.abort("s1")
        # It went nowhere, which is heard of while the stop is under way. It is of the run
        # that is being stopped: it goes with the stop.
        told[0](False)
        assert session.kept_back == []
        ended = await driver.events.wait_ended("s1")
    finally:
        on.write_text("", encoding="utf-8")

    assert ended["reason"] == "aborted" and session.queue == [] and session.run is None
    assert len(driver.record("session/prompt")) == 2 and 2 not in _taken(driver)


@pytest.mark.parametrize("early", [False, True])
async def test_what_a_turn_that_failed_began_with_is_told_of_once_when_an_answer_shows_dsh_had_begun_it(
    tmp_path: Path, dsh, early: bool
) -> None:
    from nekoclaw.dsh.host_tools import ON_ANSWERED

    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh, then="!fail the model gave out")
    try:
        # A second call of the turn is handed another note.
        await driver.runtime.steer("s1", "another note")
        other: list[Any] = []
        marked = ON_ANSWERED.set(other)
        try:
            assert await driver.runtime._take_input(session, 1.0) == ["another note"]
        finally:
            ON_ANSWERED.reset(marked)
        if early:
            # The answer of the first got to dsh before the turn failed.
            told[0](True)
    finally:
        go.write_text("", encoding="utf-8")
    ended = await driver.events.wait_ended("s1")
    assert ended["reason"] == "error"

    def said() -> list[tuple[int, bool]]:
        return [(event["inputs"], event["late"]) for event in driver.events.of("taken")]

    if early:
        # dsh had begun the turn: what it began with is told of with the first note, and
        # only the second note is open. That its answer got there too says nothing more of
        # what the turn began with.
        assert said() == [(1, False), (2, False)] and ended["open_inputs"] == [3]
        other[0](True)
        assert said() == [(1, False), (2, False), (3, True)]
        return
    # Neither answer is known of as the turn fails: what it began with is open with the notes.
    assert said() == [] and ended["open_inputs"] == [1, 2, 3]
    # Whichever answer gets there first shows that dsh had begun the turn; the other does not
    # say so once more.
    other[0](True)
    assert said() == [(1, True), (3, True)]
    told[0](True)
    assert said() == [(1, True), (3, True), (2, True)]


async def _handed_nothing(tmp_path: Path, dsh: Any, how: str, then: str) -> tuple[Any, Any, Path, list[Any]]:
    """A session whose turn is held open, and a call of that turn that was handed nothing; with whom to tell how its answer fared.

    ``how``: "woken" with nothing left for it (another call took what arrived, say), or
    "waited out" (its time ran out). ``then`` is what the turn does once it is let go.
    """
    from nekoclaw.dsh.host_tools import ON_ANSWERED

    host, _calls = _host()
    driver = dsh(host)
    await driver.started(make_spec(tmp_path))
    runtime = driver.runtime
    session = runtime._sessions["s1"]
    go = tmp_path / "go"
    await runtime.prompt("s1", f"!until {go}\n{then}")
    await _until(lambda: len(driver.record("session/prompt")) == 1, "the turn being with dsh")
    told: list[Any] = []
    marked = ON_ANSWERED.set(told)
    try:
        if how == "woken":
            waiting = asyncio.ensure_future(runtime._take_input(session, 5.0))
            await _until(lambda: not session.arrived.is_set(), "the call waiting")
            session.arrived.set()
            assert await waiting == []
        else:
            assert await runtime._take_input(session, 0.01) == []
    finally:
        ON_ANSWERED.reset(marked)
    # Heard of like any other answer, though it carries no text.
    assert len(told) == 1 and session.awaited == []
    return driver, session, go, told


@pytest.mark.parametrize("how", ["woken", "waited out"])
async def test_an_answer_with_no_text_in_it_that_went_nowhere_queues_nothing_again(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "DSH_ANSWER_SETTLE_SECONDS", 0.05)
    driver, session, go, told = await _handed_nothing(tmp_path, dsh, how, "!say went on")
    go.write_text("", encoding="utf-8")
    ended = await driver.events.wait_ended("s1")
    # How it fared is not known as the turn ends; there is no text of it to name as open.
    assert ended["reason"] == "completed" and ended["open_inputs"] == [] and len(session.unsettled) == 1

    # It went nowhere, which is heard of with the run over. There is nothing to queue again:
    # no run is begun for it (one would end at once with nothing done, and say so).
    told[0](False)
    await asyncio.sleep(0.2)
    assert not session.unsettled and session.queue == [] and session.run is None
    assert len(driver.events.of("ended")) == 1 and len(driver.record("session/prompt")) == 1


@pytest.mark.parametrize("how", ["woken", "waited out"])
@pytest.mark.parametrize("fared", ["early", "late", "nowhere"])
async def test_an_answer_with_no_text_in_it_shows_as_well_that_dsh_had_begun_a_turn_that_failed(
    tmp_path: Path, dsh, fared: str, how: str
) -> None:
    driver, session, go, told = await _handed_nothing(tmp_path, dsh, how, "!fail the model gave out")
    try:
        if fared == "early":
            # Its answer got to dsh before the turn failed.
            told[0](True)
    finally:
        go.write_text("", encoding="utf-8")
    ended = await driver.events.wait_ended("s1")
    assert ended["reason"] == "error"

    def said() -> list[tuple[int, bool]]:
        return [(event["inputs"], event["late"]) for event in driver.events.of("taken")]

    if fared == "early":
        # dsh made a call during the turn and had its answer: it had begun the turn, and
        # what the turn began with is in its conversation, though the call carried no text.
        assert said() == [(1, False)] and ended["open_inputs"] == []
        return
    # Not known as the turn fails: what the turn began with is open.
    assert said() == [] and ended["open_inputs"] == [1]
    told[0](fared == "late")
    await asyncio.sleep(0.2)
    # Heard after the turn failed that the answer got there, the same holds. Heard that it
    # went nowhere, it shows nothing: what the turn began with is not told of, and no run is
    # begun for an answer that carried no text.
    assert said() == ([(1, True)] if fared == "late" else [])
    assert not session.unsettled and session.run is None
    assert len(driver.events.of("ended")) == 1 and len(driver.record("session/prompt")) == 1


async def test_what_a_run_left_open_whose_stop_dsh_answered_with_an_error_is_not_queued_again(
    tmp_path: Path, dsh, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, session, on, told = await _left_open_by_a_run_that_goes_on(
        tmp_path, dsh, monkeypatch, before="!stopped-fails\n"
    )
    try:
        # The run is stopped in its second turn, which dsh answers with an error and not by
        # saying that the turn was cancelled; and more is said to it before that is heard.
        await driver.runtime.abort("s1")
        await driver.runtime.steer("s1", "said after the stop")
        ended = await driver.events.wait_ended("s1")
    finally:
        on.write_text("", encoding="utf-8")

    # A run that was stopped all the same. What was queued is dropped with it, and no turn
    # is begun for it;
    assert ended["reason"] == "error" and ended["open_inputs"] == [2]
    assert session.queue == [] and session.run is None
    # and what its first turn left open is not queued again when it is heard, after the
    # stop, that the answer went nowhere.
    told[0](False)
    assert not session.unsettled and session.queue == [] and session.run is None
    assert len(driver.record("session/prompt")) == 2


@pytest.mark.parametrize("reached", [False, True])
async def test_a_text_whose_answer_was_heard_of_before_its_turn_failed_is_dealt_with_as_it_fared(
    tmp_path: Path, dsh, reached: bool
) -> None:
    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh, then="!fail the model gave out")
    try:
        told[0](reached)
    finally:
        go.write_text("", encoding="utf-8")
    ended = await driver.events.wait_ended("s1")
    assert ended["reason"] == "error" and not session.unsettled and ended["open_inputs"] == []
    if reached:
        # In dsh's conversation, though its turn failed after that: told of as taken before
        # the end of the run is, once, and not sent again. With what the turn began with:
        # a call of the turn was answered, so dsh had not refused the turn at the door.
        events = driver.events.events
        said = [(index, event["inputs"], event["late"]) for index, event in enumerate(events) if event["type"] == "taken"]
        assert [item[1:] for item in said] == [(1, False), (2, False)] and said[-1][0] < events.index(ended)
        assert session.run is None and len(driver.record("session/prompt")) == 1
        # Nor is it told of once more with the end of the next run.
        mark = len(events)
        await driver.runtime.prompt("s1", "!say hello")
        await driver.events.wait_ended("s1", start=mark)
        assert _taken(driver) == [1, 2, 3]
        return
    # It went nowhere: queued again as that was heard of, and sent as a turn of its own once
    # the run that failed is over.
    await driver.events.wait_for(lambda event: event["type"] == "ended" and event["reason"] == "completed")
    prompts = driver.record("session/prompt")
    assert len(prompts) == 2 and "a note that arrives during the turn" in json.dumps(prompts[1])
    assert _taken(driver) == [2]


async def test_what_two_calls_were_handed_is_queued_again_in_the_order_it_was_given(tmp_path: Path, dsh) -> None:
    from nekoclaw.dsh.driver import _Given

    driver, session, go, told = await _handed_to_a_call(tmp_path, dsh)
    try:
        # A second call of the turn was handed the next text; and one more waits.
        later, waiting = _Given("a second note", 3), _Given("a third note", 4)
        session.given = 4
        session.queue = [waiting]
        # Both answers went nowhere. Whichever is heard of first, what is queued again is
        # in the order it was given: put in front one after the other, the later of the
        # two would end up ahead of the earlier.
        told[0](False)
        assert [text.number for text in session.queue] == [2, 4]
        driver.runtime._give_back(session, [later], session.runs)
        assert [text.number for text in session.queue] == [2, 3, 4]
    finally:
        session.queue = [text for text in session.queue if text.number == 2]
        go.write_text("", encoding="utf-8")

    _sent_again(driver, await driver.events.wait_ended("s1"))


async def test_a_call_of_a_turn_that_is_over_is_handed_nothing_though_the_run_goes_on(tmp_path: Path, dsh) -> None:
    # dsh answered the turn's request, and the run is writing things down before it sends the
    # next turn; a call of the turn that is over, which dsh has not called off, is still
    # waiting. What arrives meanwhile is the next turn's, and that call is handed nothing.
    host, _calls = _host()
    driver = dsh(host)
    await driver.started(make_spec(tmp_path))
    runtime = driver.runtime
    session = runtime._sessions["s1"]
    go = tmp_path / "go"
    settled, hold = asyncio.Event(), asyncio.Event()
    answers_settled = runtime._answers_settled

    async def held_after_the_turn(session: Any) -> None:
        await answers_settled(session)
        settled.set()
        await hold.wait()

    runtime._answers_settled = held_after_the_turn  # type: ignore[method-assign]
    await runtime.prompt("s1", f"!until {go}\n!say first")
    await _until(lambda: len(driver.record("session/prompt")) == 1, "the first turn being with dsh")
    # A call of that turn: it waits for what arrives.
    stale = asyncio.ensure_future(runtime._take_input(session, 30.0))
    try:
        await asyncio.sleep(0)
        go.write_text("", encoding="utf-8")
        await asyncio.wait_for(settled.wait(), 10.0)
        assert not stale.done()
        await runtime.steer("s1", "a note for the next turn")
        assert await asyncio.wait_for(stale, 5.0) == []
        assert [str(queued) for queued in session.queue] == ["a note for the next turn"]
        # Nor is a call of that turn that comes in only now, with the note queued already.
        assert await asyncio.wait_for(runtime._take_input(session, 30.0), 5.0) == []
        assert [str(queued) for queued in session.queue] == ["a note for the next turn"]
    finally:
        hold.set()
        stale.cancel()
    ended = await driver.events.wait_ended("s1")
    prompts = driver.record("session/prompt")
    assert len(prompts) == 2 and "a note for the next turn" in json.dumps(prompts[1])
    assert ended["reason"] == "completed" and ended["inputs"] == 2
