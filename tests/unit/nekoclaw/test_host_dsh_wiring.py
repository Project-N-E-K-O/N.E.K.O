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

"""The host with dsh: which runtime a commission runs in, and what the host does for a session in dsh.

No dsh is needed. The in-process runtime stands in for dsh's driver, which
has tests of its own (``test_dsh_driver.py``); what is tested here is the
host's side: the choice, the ledger, the fallback, the permission requests
only dsh makes, the removal of dsh, and the cleaning of old sessions.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nekoclaw.dsh import DshUnavailable
from nekoclaw.host.paths import DSH_SESSION_NAME, TRANSCRIPT_NAME
from nekoclaw.host.runtimes import RUNTIME_BUILTIN, RUNTIME_DSH
from nekoclaw.host.task_agents import _begun_in, _has_transcript
from tests.unit.nekoclaw.fakes import ScriptedLLM, call, finish
from tests.unit.nekoclaw.host_fakes import (
    FakeDecisionLLM,
    make_host,
    outbox,
    scripted_runtime,
    wait_status,
    wait_until,
)


async def model_with_an_address() -> dict[str, Any]:
    """As the host's configured model always has one; nothing listens there."""
    return {"model": "fake-model", "api_key": "test-key", "base_url": "http://127.0.0.1:9/v1"}


class Installation:
    """What the host reads and asks of a dsh installation."""

    def __init__(self, *, installed: bool = True, preference: str = "auto") -> None:
        self.is_installed = installed
        self.preference = preference
        self.forgotten: list[Path] = []
        self.forget: Any = True
        self.removed_with: list[bool] = []
        self.installs = 0

    def installed(self) -> bool:
        return self.is_installed

    def wanted(self) -> bool:
        return self.is_installed and self.preference == "auto"

    def status(self) -> dict[str, Any]:
        return {"installed": self.is_installed, "preference": self.preference}

    def set_preference(self, value: str) -> None:
        if value not in ("auto", "builtin"):
            raise ValueError(value)
        self.preference = value

    def plugins(self) -> list[dict[str, Any]]:
        return [{"name": "a-plugin", "enabled": True}]

    def forget_session(self, session_dir: Path) -> bool:
        self.forgotten.append(Path(session_dir))
        if isinstance(self.forget, Exception):
            raise self.forget
        return bool(self.forget)

    async def install(self, on_progress: Any = None) -> dict[str, Any]:
        del on_progress
        self.installs += 1
        self.is_installed = True
        return self.status()

    async def uninstall(self, *, purge: bool = False) -> dict[str, Any]:
        self.removed_with.append(purge)
        self.is_installed = False
        return self.status()


class Dsh:
    """Stands in for ``DshRuntime``: the in-process runtime, with what the driver adds to the interface."""

    def __init__(self, inner: Any, on_event: Any, behaviour: dict[str, Any]) -> None:
        self.inner = inner
        self._on_event = on_event
        self._behaviour = behaviour
        self.prepared: list[Any] = []
        self.started: list[str] = []
        self.shut_down = False

    async def prepare(self, model: Any) -> None:
        self.prepared.append(model)
        failure = self._behaviour.get("not_ready")
        if failure is not None:
            raise failure

    async def start(self, spec: Any) -> None:
        from nekoclaw.runtime import protocol

        refusal = self._behaviour.get("refuses")
        if refusal is not None:
            # As the driver reports a session dsh did not open.
            self._on_event(protocol.dump(protocol.ErrorEvent(session_id=spec.session_id, command="start", message=refusal)))
            return
        self.started.append(spec.session_id)
        # The driver's note of which of dsh's sessions is this one's.
        Path(spec.session_dir, DSH_SESSION_NAME).write_text(json.dumps({"session": "a-dsh-session"}), encoding="utf-8")
        await self.inner.start(spec)

    @property
    def session_ids(self) -> list[str]:
        """The sessions it holds, as the driver tells them: one it did not open is not among them."""
        return self.inner.core.session_ids

    def resumed(self, session_id: str) -> bool:
        """Whether dsh took up the session it had, as the driver tells it."""
        return session_id in self.session_ids and not self._behaviour.get("log_gone", False)

    async def shutdown(self) -> None:
        self.shut_down = True
        await self.inner.shutdown()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


@pytest.fixture
async def hosts() -> Any:
    made: list[Any] = []
    yield made
    for host in made:
        await host.shutdown()


def _host(hosts: list[Any], tmp_path: Path, llm: Any, installation: Any, **kwargs: Any) -> Any:
    """A host without plugins, whose dsh is ``installation`` and whose runtimes use scripted models.

    ``host.fake`` holds what the test steers and reads: the dsh runtimes that
    were made, the factory of the built-in runtime, and how dsh behaves.
    """
    pick = llm if callable(llm) else (lambda spec: llm)
    behaviour: dict[str, Any] = {}
    made: list[Dsh] = []
    builtin = scripted_runtime(pick)

    def dsh_factory(on_event: Any, callbacks: Any, installed: Any) -> Any:
        assert installed is installation
        made.append(Dsh(scripted_runtime(pick)(on_event, callbacks), on_event, behaviour))
        return made[-1]

    host = make_host(
        tmp_path,
        None,
        runtime_factory=builtin,
        task_agent_model=model_with_an_address,
        decision_llm=FakeDecisionLLM(),
        dsh=installation,
        dsh_runtime_factory=dsh_factory,
        **kwargs,
    )
    host.fake = SimpleNamespace(dsh=made, builtin=builtin, behaviour=behaviour)
    hosts.append(host)
    return host


async def _dispatch(host: Any, text: str) -> str:
    receipt = await host.dispatch(text, lanlan_name="Lan", lang="en")
    assert receipt.kind == "accepted"
    return receipt.commission_id


def _report(status: str, summary: str) -> list[Any]:
    return [call("report", {"status": status, "summary": summary}), finish("tool_calls")]


async def _fallbacks(host: Any, commission_id: str) -> list[dict[str, Any]]:
    await host.task_agents.settle()
    return [event.payload for event in await host.ledger.events(commission_id) if event.type == "runtime_fallback"]


# ---------------------------------------------------------------------------
# Which runtime
# ---------------------------------------------------------------------------


async def test_a_user_without_dsh_is_served_by_the_built_in_runtime_as_before(hosts, tmp_path):
    from nekoclaw.dsh import DshInstallation

    def never(on_event: Any, callbacks: Any, installed: Any) -> Any:
        raise AssertionError("dsh's driver is not for a user who has no dsh")

    host = make_host(
        tmp_path,
        None,
        runtime_factory=scripted_runtime(lambda spec: ScriptedLLM([_report("ready", "sorted")])),
        task_agent_model=model_with_an_address,
        decision_llm=FakeDecisionLLM(),
        dsh_runtime_factory=never,
    )
    hosts.append(host)

    done = await wait_status(host, await _dispatch(host, "sort the photos"), "done")

    assert (done.route, done.runtime) == ("task_agent", RUNTIME_BUILTIN)
    assert await _fallbacks(host, done.id) == []
    # The host knows where dsh would be, and made nothing there.
    assert isinstance(host.dsh, DshInstallation) and host.dsh.root() == tmp_path / "nekoclaw" / "dsh"
    assert not (tmp_path / "nekoclaw" / "dsh").exists()
    status = await host.dsh_status()
    assert (status["installed"], status["sessions"], status["fallback_reason"]) == (False, 0, None)


async def test_a_commission_runs_in_dsh_once_dsh_is_installed(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([_report("ready", "sorted 12 photos")]), Installation())

    done = await wait_status(host, await _dispatch(host, "sort the photos"), "done")

    assert (done.route, done.runtime) == ("task_agent", RUNTIME_DSH)
    assert done.result_summary == "sorted 12 photos"
    (dsh,) = host.fake.dsh
    # Made ready before it was given the commission; the built-in runtime was not even created.
    assert len(dsh.prepared) == 1 and dsh.started == [done.id]
    assert host.fake.builtin.created == [] and host.task_agents.runtime_started
    assert await _fallbacks(host, done.id) == []
    (report,) = await outbox(host, "task_result")
    assert report.payload["summary"] == "sorted 12 photos"


async def test_a_commission_stays_in_the_built_in_runtime_when_the_user_switched_dsh_off(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([_report("ready", "sorted")]), Installation(preference="builtin"))

    done = await wait_status(host, await _dispatch(host, "sort the photos"), "done")

    assert done.runtime == RUNTIME_BUILTIN and host.fake.dsh == []
    assert await _fallbacks(host, done.id) == []


async def test_a_dsh_that_cannot_be_made_ready_leaves_the_commission_to_the_built_in_runtime(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM([_report("ready", "sorted")]), Installation())
    host.fake.behaviour["not_ready"] = DshUnavailable("dsh did not load the permission guard")

    done = await wait_status(host, await _dispatch(host, "sort the photos"), "done")

    assert done.runtime == RUNTIME_BUILTIN and host.fake.dsh[0].started == []
    # Why is on the commission's record, and told to whoever shows the state of dsh.
    assert await _fallbacks(host, done.id) == [
        {"wanted": RUNTIME_DSH, "runtime": RUNTIME_BUILTIN, "reason": "dsh did not load the permission guard"}
    ]
    assert (await host.dsh_status())["fallback_reason"] == "dsh did not load the permission guard"


async def test_a_dsh_that_was_ready_and_opens_no_session_leaves_the_commission_to_the_built_in_runtime(
    hosts, tmp_path
):
    llm = ScriptedLLM([_report("ready", "sorted after all"), _report("ready", "backed up")])
    host = _host(hosts, tmp_path, llm, Installation())
    host.fake.behaviour["refuses"] = "dsh refused the session: no such directory"

    commission_id = await _dispatch(host, "sort the photos")
    done = await wait_status(host, commission_id, "done", "failed")

    # The user is not left with a failed commission to hand in again: nothing
    # of it was in dsh, and the built-in runtime took it at once.
    assert (done.status, done.runtime) == ("done", RUNTIME_BUILTIN) and done.result_summary == "sorted after all"
    assert host.fake.dsh[0].started == [] and len(host.fake.builtin.created) == 1
    # The work was given to it once, and one request of the model was made for it.
    assert len(llm.requests) == 1
    assert await _fallbacks(host, commission_id) == [
        {"wanted": RUNTIME_DSH, "runtime": RUNTIME_BUILTIN, "reason": "dsh refused the session: no such directory"}
    ]
    events = await host.ledger.events(commission_id)
    kinds = [event.type for event in events]
    # dsh's word is on the record, then what was done about it; the commission never failed.
    assert kinds.count("error") == 1 and kinds.index("error") < kinds.index("runtime_fallback") < kinds.index("started")
    assert [event.payload["to"] for event in events if event.type == "status"] == ["working", "done"]
    assert host.task_agents.dsh_error == "dsh refused the session: no such directory"
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")
    # From then on its session was the built-in runtime's: closed there once the work was done.
    assert host.fake.builtin.created[0].core.session_ids == []

    # dsh is not tried again so soon, though it would work by now.
    host.fake.behaviour.clear()
    other = await wait_status(host, await _dispatch(host, "back up the notes"), "done")
    assert other.runtime == RUNTIME_BUILTIN and len(host.fake.dsh[0].prepared) == 1
    (fallback,) = await _fallbacks(host, other.id)
    assert "a moment ago" in fallback["reason"] and "no such directory" in fallback["reason"]


async def test_a_start_the_built_in_runtime_refuses_still_fails_the_commission(hosts, tmp_path):
    # Only dsh's refusal is made up for; a session nothing can open is a failed commission, as before.
    host = _host(hosts, tmp_path, ScriptedLLM([_report("ready", "sorted")]), Installation())
    host.fake.behaviour["refuses"] = "dsh refused the session: no such directory"
    builtin_factory = host.task_agents._runtimes._builtin_factory

    def refusing(on_event, callbacks):
        from nekoclaw.runtime import protocol

        runtime = builtin_factory(on_event, callbacks)

        async def start(spec):
            on_event(protocol.dump(protocol.ErrorEvent(session_id=spec.session_id, command="start", message="no room")))

        runtime.start = start
        return runtime

    host.task_agents._runtimes._builtin_factory = refusing

    failed = await wait_status(host, await _dispatch(host, "sort the photos"), "failed", "done")

    assert failed.status == "failed" and "no room" in failed.status_reason and failed.runtime == RUNTIME_BUILTIN
    # dsh's refusal is on the record all the same.
    assert [item["reason"] for item in await _fallbacks(host, failed.id)] == ["dsh refused the session: no such directory"]


async def test_a_commission_that_began_in_dsh_goes_on_there_whatever_was_switched_since(hosts, tmp_path):
    installation = Installation()
    llm = ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "found it")])
    host = _host(hosts, tmp_path, llm, installation)
    commission_id = await _dispatch(host, "sort the photos")
    await wait_status(host, commission_id, "failed")
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")

    installation.preference = "builtin"
    assert await host.tell(commission_id, "it is on drive D")
    done = await wait_status(host, commission_id, "done")

    # What was said in its session is in dsh, and nowhere else.
    assert done.runtime == RUNTIME_DSH and host.fake.dsh[0].started == [commission_id, commission_id]
    assert host.fake.builtin.created == []
    # A new commission follows the switch.
    llm.add(*_report("ready", "backed up"))
    other = await wait_status(host, await _dispatch(host, "back up the notes"), "done")
    assert other.runtime == RUNTIME_BUILTIN


async def test_a_commission_that_began_in_a_dsh_that_was_removed_starts_over_in_the_built_in_runtime(hosts, tmp_path):
    installation = Installation()
    llm = ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "found it")])
    host = _host(hosts, tmp_path, llm, installation)
    commission_id = await _dispatch(host, "sort the photos")
    await wait_status(host, commission_id, "failed")
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")

    await host.uninstall_dsh()
    assert await host.tell(commission_id, "it is on drive D")
    done = await wait_status(host, commission_id, "done")

    assert done.runtime == RUNTIME_BUILTIN
    assert await _fallbacks(host, commission_id) == [
        {"wanted": RUNTIME_DSH, "runtime": RUNTIME_BUILTIN, "reason": "dsh is no longer installed"}
    ]
    # Nothing of what was said is there: it is told the commission again, then what the user said.
    assert _told_anew(llm.requests[-1], commission_id, "sort the photos", "it is on drive D")


async def test_a_commission_dsh_will_not_take_up_again_starts_over_in_the_built_in_runtime_knowing_what_it_is(
    hosts, tmp_path
):
    llm = ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "found it")])
    host = _host(hosts, tmp_path, llm, Installation())
    commission_id = await _dispatch(host, "sort the photos")
    await wait_status(host, commission_id, "failed")
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")

    host.fake.behaviour["refuses"] = "dsh refused the session: it is busy"
    assert await host.tell(commission_id, "it is on drive D")
    done = await wait_status(host, commission_id, "done")

    assert done.runtime == RUNTIME_BUILTIN
    assert _told_anew(llm.requests[-1], commission_id, "sort the photos", "it is on drive D")


@pytest.mark.parametrize("log_gone", [True, False])
async def test_a_commission_whose_session_dsh_cannot_take_up_is_told_what_it_is_once_more(hosts, tmp_path, log_gone):
    llm = ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "found it")])
    host = _host(hosts, tmp_path, llm, Installation())
    commission_id = await _dispatch(host, "sort the photos")
    await wait_status(host, commission_id, "failed")
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")
    # The note that ties the session to one of dsh's is there. What dsh kept of that one is
    # not (dsh was installed anew, say): asked to take it up, dsh begins another.
    host.fake.behaviour["log_gone"] = log_gone

    assert await host.tell(commission_id, "it is on drive D")
    done = await wait_status(host, commission_id, "done")

    assert done.runtime == RUNTIME_DSH
    # In a session in which nothing was said, the user's words alone would say nothing of
    # what the commission is; where dsh did take it up, that is there to be read.
    assert _told_anew(llm.requests[-1], commission_id, "sort the photos", "it is on drive D") is log_gone
    if not log_gone:
        assert _last_user_message(llm.requests[-1]) == "it is on drive D"


@pytest.mark.parametrize("log_gone", [True, False])
async def test_a_commission_told_once_more_what_it_is_has_the_list_of_its_repository_again(
    hosts, tmp_path, tmp_path_factory, log_gone
):
    # A repository, outside the folder the host keeps its data in.
    root = tmp_path_factory.mktemp("repo")
    (root / ".git").mkdir()
    (root / "AGENTS.md").write_text("a", encoding="utf-8", newline="\n")
    llm = ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "found it")])
    host = _host(hosts, tmp_path, llm, Installation())
    await host.space.touch_project("Budget app", workdir=str(root))
    commission_id = await _dispatch(host, "add an export to the Budget app")
    await wait_status(host, commission_id, "failed")
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")
    assert "- AGENTS.md (1 B)" in _last_user_message(llm.requests[0])
    host.fake.behaviour["log_gone"] = log_gone

    assert await host.tell(commission_id, "it is on drive D")
    done = await wait_status(host, commission_id, "done")

    assert done.runtime == RUNTIME_DSH and done.workdir == str(root)
    # Whether dsh takes its session up is known only once it was started. Where it does not, the
    # commission is opened in a session that holds nothing, and what its repository keeps for agents
    # is listed again; where it does, nothing of that is said a second time.
    assert ("- AGENTS.md (1 B)" in _last_user_message(llm.requests[-1])) is log_gone


async def test_a_commission_whose_session_is_gone_starts_over_knowing_what_it_is(hosts, tmp_path):
    llm = ScriptedLLM([_report("failed", "the folder is missing"), _report("ready", "found it")])
    host = _host(hosts, tmp_path, llm, Installation())
    commission_id = await _dispatch(host, "sort the photos")
    await wait_status(host, commission_id, "failed")
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")
    # What tied it to its session in dsh was removed meanwhile (being old, say).
    Path((await host.ledger.get(commission_id)).session_dir, DSH_SESSION_NAME).unlink()

    assert await host.tell(commission_id, "it is on drive D")
    done = await wait_status(host, commission_id, "done")

    assert done.runtime == RUNTIME_DSH
    assert _told_anew(llm.requests[-1], commission_id, "sort the photos", "it is on drive D")


async def test_a_commission_that_follows_one_up_whose_session_is_in_the_other_runtime_is_told_what_came_of_it(
    hosts, tmp_path
):
    from config.prompts import prompts_nekoclaw_brain as brain_texts

    llm = ScriptedLLM([_report("ready", "sorted 12 photos into D:/Photos/by-month"), _report("ready", "renamed")])
    host = _host(hosts, tmp_path, llm, Installation())
    first = await _dispatch(host, "sort the photos")
    assert (await wait_status(host, first, "done")).runtime == RUNTIME_DSH
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")
    await wait_until(lambda: host.task_agents._held_by == {}, what="the session being let go")

    # What was said is in dsh, and dsh is gone: the built-in runtime takes the session.
    await host.uninstall_dsh()
    second = await _dispatch(host, "also rename the photos by date")
    done = await wait_status(host, second, "done")
    assert (done.session_id, done.continues, done.runtime) == (first, first, RUNTIME_BUILTIN)

    # Nothing of the earlier commission is "above" in this runtime: it is told what came of it.
    opening = _last_user_message(llm.requests[-1])
    assert opening.startswith(f"Commission {second}\nalso rename the photos by date\n")
    assert (
        brain_texts.TASK_AGENT_COMMISSION_CONTINUES_CLEARED.format(
            title="sort the photos", result="sorted 12 photos into D:/Photos/by-month"
        )
        in opening
    )
    assert brain_texts.TASK_AGENT_COMMISSION_CONTINUES.format(title="sort the photos") not in opening


def _told_anew(request: Any, commission_id: str, dispatched: str, said: str) -> bool:
    """Whether a request ends with the commission told again, then what the user said."""
    message = _last_user_message(request)
    return message.startswith(f"Commission {commission_id}\n{dispatched}\n") and message.endswith(f"\n\n{said}")


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    (text,) = [message["content"] for message in messages if message["role"] == "user"][-1:]
    return text if isinstance(text, str) else "".join(part.get("text", "") for part in text)


@pytest.mark.parametrize(
    ("parent_runtime", "preference", "expected"),
    [
        # Where the one that started it runs, whatever the user wants for new commissions.
        (RUNTIME_DSH, "builtin", RUNTIME_DSH),
        (RUNTIME_BUILTIN, "auto", RUNTIME_BUILTIN),
        # A parent that never ran anywhere tells nothing.
        (None, "auto", RUNTIME_DSH),
    ],
)
async def test_a_task_agent_started_by_another_runs_where_that_one_runs(
    hosts, tmp_path, monkeypatch, parent_runtime, preference, expected
):
    from nekoclaw.runtime import ModelConfig

    host = _host(hosts, tmp_path, ScriptedLLM(), Installation(preference=preference))

    async def get(commission_id: str) -> Any:
        assert commission_id == "the-parent"
        return SimpleNamespace(runtime=parent_runtime)

    monkeypatch.setattr(host.ledger, "get", get)
    model = ModelConfig(**await model_with_an_address())

    name, _runtime, note = await host.task_agents._choose_runtime(SimpleNamespace(parent_id="the-parent"), model, None)

    assert (name, note) == (expected, None)


@pytest.mark.parametrize(
    ("recorded", "files", "expected"),
    [
        # The ledger tells which runtime took it up last; the directory, whether a session came of it.
        (RUNTIME_DSH, [DSH_SESSION_NAME], RUNTIME_DSH),
        (RUNTIME_DSH, [], None),
        (RUNTIME_DSH, [TRANSCRIPT_NAME], None),
        (RUNTIME_BUILTIN, [TRANSCRIPT_NAME], RUNTIME_BUILTIN),
        (RUNTIME_BUILTIN, [], None),
        # Began in dsh, started over in the built-in runtime: that is where it is now.
        (RUNTIME_BUILTIN, [TRANSCRIPT_NAME, DSH_SESSION_NAME], RUNTIME_BUILTIN),
        (None, [TRANSCRIPT_NAME, DSH_SESSION_NAME], None),
        ("plugin", [TRANSCRIPT_NAME], None),
    ],
)
def test_where_a_commission_began_is_told_by_the_ledger_and_the_session_directory(tmp_path, recorded, files, expected):
    for name in files:
        (tmp_path / name).write_text("{}", encoding="utf-8")
    assert _begun_in(SimpleNamespace(runtime=recorded), tmp_path) == expected


def test_a_session_that_ran_in_dsh_counts_as_one_in_which_something_was_said(tmp_path):
    assert not _has_transcript(tmp_path)
    (tmp_path / DSH_SESSION_NAME).write_text("{}", encoding="utf-8")
    assert _has_transcript(tmp_path)


# ---------------------------------------------------------------------------
# The permission requests only dsh makes
# ---------------------------------------------------------------------------


async def test_a_gateway_call_in_dsh_is_ruled_on_before_it_is_made_and_then_let_through(hosts, tmp_path, monkeypatch):
    host = _host(hosts, tmp_path, ScriptedLLM(), Installation())
    cleared: list[tuple[str, str, dict[str, Any]]] = []

    async def clear_call(commission_id: str, name: str, arguments: dict[str, Any]) -> None:
        cleared.append((commission_id, name, arguments))

    async def never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the gateway rules on its own tools")

    monkeypatch.setattr(host.gateway, "clear_call", clear_call)
    monkeypatch.setattr(host.permissions, "check", never)
    arguments = {"name": "browser_navigate", "arguments": {"url": "https://example.com"}}

    reply = await host.task_agents._cb_permission(
        "c1", {"tool": "capability_call", "category": "gateway", "arguments": arguments, "workdir": "w", "default": "allow"}
    )

    # Let through whatever came of it: the gateway answers the call with what was decided.
    assert reply["allow"] is True and cleared == [("c1", "capability_call", arguments)]
    # Arguments that are no object are none.
    await host.task_agents._cb_permission("c1", {"tool": "capability_search", "category": "gateway", "arguments": "x"})
    assert cleared[-1] == ("c1", "capability_search", {})


async def _working(host: Any) -> str:
    """Hand in a commission and return its id once its task agent is at work."""
    commission_id = await _dispatch(host, "look it up")
    await wait_status(host, commission_id, "working")
    await wait_until(lambda: commission_id in host.task_agents.running, what="the session")
    return commission_id


def _waiting_llm() -> tuple[ScriptedLLM, Any]:
    import asyncio

    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    return ScriptedLLM([[wait, *_report("ready", "looked it up")]]), release


@pytest.mark.parametrize(("answer", "refused"), [("once", False), ("deny", True)])
async def test_a_search_in_dsh_is_ruled_on_before_it_is_called_and_not_again(hosts, tmp_path, answer, refused):
    llm, release = _waiting_llm()
    host = _host(hosts, tmp_path, llm, Installation())
    await host.permission_store.set_mode("plugin_entry", "always_ask")
    asked: list[Any] = []

    async def asker(commission_id: str, ask: Any) -> str:
        asked.append((commission_id, ask.category, ask.detail.get("tool")))
        return answer

    host.permissions.set_asker(asker)
    commission_id = await _working(host)

    # Before the call, where dsh waits as long as the user takes.
    reply = await host.task_agents._cb_permission(
        commission_id, {"tool": "web_search", "category": "host_tool", "arguments": {"queries": ["cats"]}, "workdir": "w", "default": "allow"}
    )
    assert reply["allow"] is True and asked == [(commission_id, "plugin_entry", "web_search")]

    # The call does not ask a second time: it goes on, or is answered with the refusal.
    with pytest.raises(RuntimeError) as failure:
        await host.task_agents._cb_web_search(commission_id, {"queries": ["cats"]})
    assert len(asked) == 1
    assert ("the user declined" in str(failure.value)) == refused
    denied = [event for event in await host.ledger.events(commission_id) if event.type == "capability_denied"]
    assert len(denied) == (1 if refused else 0)

    # That was for the one call: the next one is ruled on by itself.
    with pytest.raises(RuntimeError):
        await host.task_agents._cb_web_search(commission_id, {"queries": ["dogs"]})
    assert len(asked) == 2
    release.set()
    await wait_status(host, commission_id, "done")


async def test_a_ruling_on_a_search_is_only_for_what_the_user_was_asked_about(hosts, tmp_path):
    llm, release = _waiting_llm()
    host = _host(hosts, tmp_path, llm, Installation())
    await host.permission_store.set_mode("plugin_entry", "always_ask")
    asked: list[Any] = []

    async def asker(commission_id: str, ask: Any) -> str:
        asked.append(commission_id)
        return "once"

    host.permissions.set_asker(asker)
    commission_id = await _working(host)
    await host.task_agents._cb_permission(
        commission_id, {"tool": "web_search", "category": "host_tool", "arguments": {"queries": ["cats"]}, "workdir": "w", "default": "allow"}
    )
    assert len(asked) == 1

    # That call never came (dsh dropped it); a search for something else is
    # not what the user allowed once, and they are asked about it.
    with pytest.raises(RuntimeError):
        await host.task_agents._cb_web_search(commission_id, {"queries": ["dogs"]})
    assert len(asked) == 2

    # The same search as the search tool hands it on (a query given twice, once) is what was allowed.
    await host.task_agents._cb_permission(
        commission_id, {"tool": "web_search", "category": "host_tool", "arguments": {"queries": ["cats", "cats"]}, "workdir": "w", "default": "allow"}
    )
    with pytest.raises(RuntimeError):
        await host.task_agents._cb_web_search(commission_id, {"queries": ["cats"]})
    assert len(asked) == 3
    release.set()
    await wait_status(host, commission_id, "done")


@pytest.mark.parametrize("changed", ["switch", "permission"])
async def test_a_search_allowed_before_its_call_is_refused_when_the_settings_forbid_it_since(
    hosts, tmp_path, monkeypatch, changed
):
    llm, release = _waiting_llm()
    host = _host(hosts, tmp_path, llm, Installation())
    await host.permission_store.set_mode("plugin_entry", "always_ask")
    asked: list[Any] = []

    async def asker(commission_id: str, ask: Any) -> str:
        asked.append(commission_id)
        return "once"

    host.permissions.set_asker(asker)
    commission_id = await _working(host)
    await host.task_agents._cb_permission(
        commission_id, {"tool": "web_search", "category": "host_tool", "arguments": {"queries": ["cats"]}, "workdir": "w", "default": "allow"}
    )

    # Switched off, or denied, after the user allowed it and before the call came.
    if changed == "switch":
        monkeypatch.setattr(host.task_agents._registry, "plugins_enabled", lambda: False)
    else:
        await host.permission_store.set_mode("plugin_entry", "deny")
    with pytest.raises(RuntimeError) as failure:
        await host.task_agents._cb_web_search(commission_id, {"queries": ["cats"]})

    expected = "plugins are switched off" if changed == "switch" else "the permission settings forbid this"
    assert expected in str(failure.value) and len(asked) == 1
    release.set()
    await wait_status(host, commission_id, "done")


async def test_a_ruling_on_a_search_does_not_wait_for_its_call_for_ever_or_outlive_the_commission(
    hosts, tmp_path, monkeypatch
):
    import asyncio

    from config import nekoclaw_settings

    monkeypatch.setattr(nekoclaw_settings, "GATEWAY_CLEARED_CALL_SECONDS", 0.05)
    llm, release = _waiting_llm()
    host = _host(hosts, tmp_path, llm, Installation())
    await host.permission_store.set_mode("plugin_entry", "always_ask")
    asked: list[str] = []

    async def asker(commission_id: str, ask: Any) -> str:
        asked.append(commission_id)
        return "once"

    host.permissions.set_asker(asker)
    commission_id = await _working(host)
    request = {"tool": "web_search", "category": "host_tool", "arguments": {"queries": ["cats"]}}

    await host.task_agents._cb_permission(commission_id, request)
    await asyncio.sleep(0.2)
    with pytest.raises(RuntimeError):
        await host.task_agents._cb_web_search(commission_id, {"queries": ["cats"]})
    # Too long ago to be about this call: it is asked about like any other.
    assert len(asked) == 2

    # Another of the host's tools is none the host rules on ahead; nothing is asked or kept for it.
    assert (await host.task_agents._cb_permission(commission_id, {"tool": "report", "category": "host_tool"}))["allow"] is True
    assert len(asked) == 2

    await host.task_agents._cb_permission(commission_id, request)
    assert commission_id in host.task_agents._search_rulings
    release.set()
    await wait_status(host, commission_id, "done")
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")
    assert host.task_agents._search_rulings == {}
    # A commission that is over is asked nothing.
    await host.task_agents._cb_permission(commission_id, request)
    assert len(asked) == 3 and host.task_agents._search_rulings == {}


async def test_a_tool_a_dsh_plugin_brought_follows_the_setting_for_everything_else(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM(), Installation())
    payload = {"tool": "plugin_weather", "category": "other", "arguments": {"city": "Oslo"}, "workdir": "w", "default": "allow"}

    assert (await host.task_agents._cb_permission("c1", payload))["allow"] is True

    await host.permission_store.set_mode("other", "deny")
    refused = await host.task_agents._cb_permission("c1", payload)
    assert refused["allow"] is False and "settings" in refused["reason"]


# ---------------------------------------------------------------------------
# Removing dsh, shutting down, old sessions
# ---------------------------------------------------------------------------


async def test_dsh_is_neither_removed_nor_installed_anew_while_a_commission_is_in_it(hosts, tmp_path):
    import asyncio

    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    installation = Installation()
    host = _host(hosts, tmp_path, ScriptedLLM([[wait, *_report("ready", "sorted")]]), installation)
    commission_id = await _dispatch(host, "sort the photos")
    await wait_status(host, commission_id, "working")
    await wait_until(lambda: host.task_agents.sessions_in(RUNTIME_DSH) == [commission_id], what="the session in dsh")
    assert (await host.dsh_status())["sessions"] == 1

    with pytest.raises(DshUnavailable, match="in use"):
        await host.uninstall_dsh()
    # Installed already: nothing to do, and nothing is ended for it.
    assert (await host.install_dsh())["installed"] is True
    # Another version is (the one in use changed): it is not put in place under the commission.
    installation.is_installed = False
    with pytest.raises(DshUnavailable, match="in use"):
        await host.install_dsh()
    installation.is_installed = True
    assert installation.installs == 0 and not host.fake.dsh[0].shut_down

    release.set()
    await wait_status(host, commission_id, "done")
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")
    status = await host.uninstall_dsh(purge=True)

    # Its process is ended first: the program cannot be removed while it runs.
    assert host.fake.dsh[0].shut_down and installation.removed_with == [True]
    assert (status["installed"], status["sessions"]) == (False, 0)
    assert host.task_agents.sessions_in(RUNTIME_BUILTIN) == []


async def test_the_switch_and_the_plugins_are_read_and_set_through_the_host(hosts, tmp_path):
    installation = Installation()
    host = _host(hosts, tmp_path, ScriptedLLM(), installation)

    assert (await host.set_dsh_preference("builtin"))["preference"] == "builtin" and installation.preference == "builtin"
    with pytest.raises(ValueError):
        await host.set_dsh_preference("whatever")
    assert await host.dsh_plugins() == [{"name": "a-plugin", "enabled": True}]
    # Installed already: nothing is done.
    assert (await host.install_dsh())["installed"] is True and installation.installs == 0
    installation.is_installed = False
    assert (await host.install_dsh())["installed"] is True and installation.installs == 1


async def test_shutting_the_host_down_ends_every_runtime_that_was_started(hosts, tmp_path):
    installation = Installation()
    llm = ScriptedLLM([_report("ready", "one"), _report("ready", "two")])
    host = _host(hosts, tmp_path, llm, installation)
    await wait_status(host, await _dispatch(host, "sort the photos"), "done")
    installation.preference = "builtin"
    await wait_status(host, await _dispatch(host, "back up the notes"), "done")
    (builtin,) = host.fake.builtin.created
    stopped: list[str] = []
    shutdown = builtin.shutdown

    async def recorded() -> None:
        stopped.append("builtin")
        await shutdown()

    builtin.shutdown = recorded

    await host.shutdown()

    assert host.fake.dsh[0].shut_down and stopped == ["builtin"] and not host.task_agents.runtime_started


@pytest.mark.parametrize(
    ("transcript", "forget", "expected"),
    [
        (True, True, "removed"),
        (True, False, "removed"),
        # Nothing of the built-in runtime's, and dsh held its session.
        (False, True, "removed"),
        (False, False, "nothing"),
        (False, OSError("held open"), "not_removed"),
        (True, OSError("held open"), "not_removed"),
    ],
)
async def test_cleaning_an_old_session_removes_what_dsh_kept_of_it_too(hosts, tmp_path, transcript, forget, expected):
    installation = Installation()
    installation.forget = forget
    host = _host(hosts, tmp_path, ScriptedLLM(), installation)
    directory = tmp_path / "sessions" / "c1"
    directory.mkdir(parents=True)
    if transcript:
        (directory / TRANSCRIPT_NAME).write_text("{}", encoding="utf-8")

    assert host.space_keeper._clean(directory, "c1") == expected

    assert installation.forgotten == [directory] and not (directory / TRANSCRIPT_NAME).exists()


async def test_the_session_of_an_old_commission_that_ran_in_dsh_is_cleaned_in_dsh(hosts, tmp_path):
    import time

    installation = Installation()
    host = _host(hosts, tmp_path, ScriptedLLM([_report("ready", "sorted")]), installation)
    done = await wait_status(host, await _dispatch(host, "sort the photos"), "done")
    await wait_until(lambda: host.task_agents.running == [], what="the session being released")
    # And let go of by the commission that held it, which takes two more looks at the ledger
    # once it has left the runtime: until then the session counts as in use, and its files
    # are not removed (the tidying comes by again, as it would here).
    await wait_until(lambda: done.session_id not in host.task_agents._held_by, what="the session being let go")
    session_dir = Path(done.session_dir)
    assert (session_dir / DSH_SESSION_NAME).is_file()

    # As the tidying does it for a commission that ended long enough ago.
    assert await host.space_keeper._clean_one(done.session_id, time.time() + 60) == "removed"

    assert installation.forgotten == [session_dir] and not (session_dir / TRANSCRIPT_NAME).exists()
    # Its working directory is never removed.
    assert Path(done.workdir).is_dir()


async def test_a_directory_that_is_not_a_sessions_is_not_asked_about_in_dsh(hosts, tmp_path):
    installation = Installation()
    host = _host(hosts, tmp_path, ScriptedLLM(), installation)
    elsewhere = tmp_path / "somewhere" / "c1"
    elsewhere.mkdir(parents=True)
    misnamed = tmp_path / "sessions" / "c2"
    misnamed.mkdir(parents=True)

    assert host.space_keeper._clean(elsewhere, "c1") == "nothing"
    assert host.space_keeper._clean(misnamed, "c1") == "nothing"
    assert installation.forgotten == []
