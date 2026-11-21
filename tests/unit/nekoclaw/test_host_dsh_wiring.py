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

Except for tests exercising the pinned runtime, the in-process runtime stands in for dsh's driver, which
has tests of its own (``test_dsh_driver.py``); what is tested here is the
host's side: the choice, the ledger, the fallback, the permission requests
only dsh makes, the removal of dsh, and the cleaning of old sessions.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nekoclaw.dsh import DshUnavailable
from nekoclaw.host.paths import DSH_SESSION_NAME, TRANSCRIPT_NAME
from nekoclaw.host.runtimes import RUNTIME_BUILTIN, RUNTIME_DSH
from nekoclaw.host.task_agents import _Handed, _Unsaid, _begun_in, _has_transcript
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


@pytest.mark.parametrize("runtime_name", [RUNTIME_BUILTIN, RUNTIME_DSH])
@pytest.mark.parametrize(("choice", "expected"), [("session", 1), ("once", 3)])
async def test_three_different_commands_in_one_directory(runtime_name, choice, expected, tmp_path):
    from nekoclaw.dsh import profile
    from nekoclaw.dsh.driver import DshRuntime
    from nekoclaw.host import call_audit
    from tests.unit.nekoclaw.fake_openai_server import FakeOpenAIServer, sse_finish, sse_text, sse_tool_call

    binary = os.getenv("NEKOCLAW_TEST_DSH_BINARY")
    if runtime_name == RUNTIME_DSH and not binary:
        pytest.skip("Set NEKOCLAW_TEST_DSH_BINARY to run the stock dsh test")

    def factory(on_event, callbacks, installation):
        return DshRuntime(
            on_event, callbacks, launch=lambda: profile.DshLaunch(home=tmp_path / "dsh", executable=Path(binary)),
        )

    shell = "pwsh" if os.name == "nt" else "bash"
    commands = [f"echo consent-{i}" for i in range(3)]
    replies = iter(
        [sse_tool_call(shell, {"command": command, "description": "Print the fixture"}, f"call_{i}"),
         sse_finish("tool_calls")]
        for i, command in enumerate(commands)
    )
    questions = []

    async def answer(commission, ask):
        questions.append(ask)
        return choice

    with FakeOpenAIServer(lambda _: next(replies, [sse_text("Done"), sse_finish()])) as model:
        async def model_config():
            return dict(model="fixture", base_url=model.base_url, api_key="local", auto_extra_body=False)

        host = make_host(
            tmp_path, task_agent_model=model_config, decision_llm=FakeDecisionLLM(), plugins_enabled=lambda: False,
            dsh=Installation(installed=runtime_name == RUNTIME_DSH), dsh_runtime_factory=factory,
        )
        try:
            host.permissions.set_asker(answer)
            commission = await _dispatch(host, "Run three fixture commands")
            await wait_until(lambda: len(model.requests) >= 4, timeout=90, what="delivery of the three command results")
            for i, req in enumerate(model.requests[1:4]):
                assert f"consent-{i}" in str(req["messages"][-1])
            assert len(questions) == expected and all(q.category == "run_command" for q in questions)
            assert all(q.detail["content_to"] and "session" in q.options for q in questions)
            if runtime_name == RUNTIME_DSH:
                assert all(q.options == ("session", "once", "deny") for q in questions)
            audit = await host.ledger.run(lambda db: call_audit.read_trace(db, commission, now=host.ledger._clock()))
            operations = [row for row in audit if row["target"] == shell]
            assert len(operations) == 3 and len({row["scope_ref"] for row in operations}) == 3
            if choice == "session":
                assert operations[0]["grant_id"] and len({row["grant_id"] for row in operations}) == 1
            delivered = {row["parent_call_id"] for row in audit if row["kind"] == "transfer_content"}
            assert {row["call_id"] for row in operations} <= delivered
        finally:
            await host.shutdown()


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


@pytest.mark.parametrize(
    ("enabled", "runtime", "native", "text", "has_browser"),
    [
        (False, "builtin", False, "Open the browser", False),
        (True, "builtin", False, "Open the browser", True),
        (True, "builtin", False, "Explain the browser", False),
        (True, "auto", False, "Open the browser", False),
        (True, "auto", True, "Open the browser", True),
    ],
)
async def test_dispatch_only_mounts_devices_on_a_supported_related_route(
    hosts, tmp_path, monkeypatch, enabled, runtime, native, text, has_browser
):
    from config import nekoclaw_settings
    from nekoclaw.builtin.provider import BuiltinToolProvider

    monkeypatch.setattr(nekoclaw_settings, "FIRST_TURN_DEVICE_TOOLS", enabled)
    if native:
        monkeypatch.setattr(Dsh, "supports_device_schemas", lambda self, model: True, raising=False)
    llm = ScriptedLLM([_report("ready", "done")])
    host = _host(hosts, tmp_path, llm, Installation(preference=runtime),
                 builtins=BuiltinToolProvider(state=lambda group: (True, "")))
    await wait_status(host, await _dispatch(host, text), "done")
    names = {tool["function"]["name"] for tool in llm.overrides[0]["tools"]}
    assert ("browser_navigate_page" in names) == has_browser
    assert "computer" not in names and {"capability_call", "capability_search"} <= names


@pytest.mark.parametrize(("dispatch", "note", "expected", "focus", "retry"), [
    ("Summarize the report.", "Open the browser.", {"browser_click"}, True, False),
    ("Summarize the report.", "Open the browser.", {"browser_click"}, True, True),
    ("Take a desktop screenshot.", "Open the browser.", {"computer", "browser_click"}, True, False),
    ("Open the browser.", "Take a desktop screenshot.", {"computer", "browser_click"}, False, False),
    ("Summarize the report.", _Handed("Open the browser.", ()), set(), None, False),
    ("Summarize the report.", _Unsaid("Take a desktop screenshot."), set(), None, False),
])
async def test_queued_instructions_choose_the_opening_tools_and_click_target(
    hosts, tmp_path, monkeypatch, dispatch, note, expected, focus, retry,
):
    from config import nekoclaw_settings
    from nekoclaw.builtin.provider import BuiltinToolProvider

    monkeypatch.setattr(nekoclaw_settings, "FIRST_TURN_DEVICE_TOOLS", True)
    release = asyncio.Event()
    busy = ScriptedLLM([[release.wait, *_report("ready", "done")]])
    next_model = ScriptedLLM([_report("ready", "done")])
    specs = []

    def pick(spec):
        specs.append(spec)
        return busy if len(specs) == 1 else next_model

    host = _host(hosts, tmp_path, pick, Installation(preference="builtin"), max_task_agents=1,
                 builtins=BuiltinToolProvider(state=lambda group: (True, "")))
    monkeypatch.setattr(host.task_agents, "_sees_images", AsyncMock(return_value=True))
    await _dispatch(host, "Wait for data.")
    await wait_until(lambda: bool(busy.requests))
    queued = await _dispatch(host, dispatch)
    await host.brain.settle()
    assert host.task_agents.queued == [queued]
    assert await host.task_agents.tell(queued, note)
    if retry:
        # A fresh opening takes the same user sentence from the carried inbox.
        make_spec = host.task_agents._spec

        async def fail_once(*args, **kwargs):
            monkeypatch.setattr(host.task_agents, "_spec", make_spec)
            raise RuntimeError("opening interrupted")

        monkeypatch.setattr(host.task_agents, "_spec", fail_once)
    release.set()
    if retry:
        await wait_status(host, queued, "failed")
        assert await host.task_agents.tell(queued, "Please continue.")
    await wait_status(host, queued, "done")
    names = {tool["function"]["name"] for tool in next_model.overrides[0]["tools"]}
    assert names.intersection({"browser_click", "computer"}) == expected
    gateway = specs[-1].gateway
    assert gateway.opening_browser_context is focus
    assert ("opening_browser_context" in gateway.model_dump()) == (focus is not None)
    ending = note + "\n\nPlease continue." if retry else note
    assert next_model.requests[0][-1]["content"].endswith(ending)


@pytest.mark.parametrize("backend", ["builtin", "auto"], ids=["builtin", "dsh"])
@pytest.mark.parametrize(("follow_up", "compacted", "expected"), [
    ("点击登录按钮", False, {"browser_click"}), ("继续", False, {"browser_click"}),
    ("继续", "while_queued", {"browser_click"}),
    ("按下回车", False, {"browser_click", "computer"}), ("按下回车", True, {"computer"}),
    ("继续", True, set()), ("列出浏览器标签页", True, {"browser_click"}),
    ("点击登录按钮", True, {"browser_click"}), ("click login", True, {"browser_click"}),
    ("截桌面", True, {"computer"}), ("Click the Start menu", True, {"computer"}),
    ("Switch to Notepad, then click OK", True, {"computer"}),
    (_Handed("点击登录按钮", ()), True, set()),
])
async def test_relaunch_uses_current_device_intent_after_a_summary(
    hosts, tmp_path, monkeypatch, backend, follow_up, compacted, expected
):
    from nekoclaw.builtin.provider import BuiltinToolProvider

    llm = ScriptedLLM([_report("blocked", "waiting for login"), _report("ready", "done")])
    monkeypatch.setattr(Dsh, "supports_device_schemas", lambda self, model: True, raising=False)
    host = _host(hosts, tmp_path, llm, Installation(preference=backend),
                 builtins=BuiltinToolProvider(state=lambda group: (True, "")))
    monkeypatch.setattr(host.task_agents, "_sees_images", AsyncMock(return_value=True))
    commission_id = await _dispatch(host, "打开浏览器")
    await wait_status(host, commission_id, "blocked")
    await wait_until(lambda: host.task_agents.running == [], what="the blocked session being released")
    if compacted:
        commission = await host.ledger.get(commission_id)
        # Both the active runtime's compacted event and a host idle summary
        # save this timestamp on the persistent session.
        # A previous commission may finish a summary while this one waits to open.
        summary_at = commission.created_at if compacted == "while_queued" else host.ledger._clock()
        await host.ledger.update_session(commission.session_id, compacted_at=summary_at)
    assert await host.task_agents.tell(commission_id, follow_up)
    await wait_status(host, commission_id, "done")
    assert len(llm.overrides) == 2
    assert bool(host.fake.dsh) == (backend == "auto")
    for request, devices in zip(llm.overrides, [{"browser_click"}, expected], strict=True):
        names = {tool["function"]["name"] for tool in request["tools"]}
        assert names.intersection({"browser_click", "computer"}) == devices


# ---------------------------------------------------------------------------
# Which runtime
# ---------------------------------------------------------------------------


async def test_dsh_compaction_can_confirm_the_opening_before_the_first_model_reply(hosts, tmp_path, monkeypatch):
    async def prompt(driver, session_id, text, **kwargs):
        if kwargs.get("opening"):
            driver._on_event({"type": "compacted", "session_id": session_id, "count": None, "tokens_before": None})
        await driver.inner.prompt(session_id, text, **kwargs)

    monkeypatch.setattr(Dsh, "prompt", prompt, raising=False)
    host = _host(hosts, tmp_path, ScriptedLLM([_report("ready", "done")]), Installation())
    commission_id = await _dispatch(host, "Open the browser")
    await wait_status(host, commission_id, "done")
    kinds = [event.type for event in await host.ledger.events(commission_id)]
    assert kinds.count("opened") == 1
    assert kinds.index("opened") < kinds.index("compacted")


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


@pytest.mark.parametrize("complete", [True, False], ids=["success", "empty-summary"])
async def test_pinned_dsh_summary_is_recorded_and_a_sleeping_commission_wakes_without_old_devices(
    tmp_path, monkeypatch, complete,
):
    from config import nekoclaw_settings
    from nekoclaw.builtin.provider import BuiltinToolProvider
    from nekoclaw.dsh import profile
    from nekoclaw.dsh.driver import DshRuntime
    from nekoclaw.host.capabilities import BuiltinOutcome
    from tests.unit.nekoclaw.fake_openai_server import FakeOpenAIServer, sse_finish, sse_text, sse_tool_call, sse_usage

    executable = os.getenv("NEKOCLAW_TEST_DSH_BINARY")
    if not executable:
        pytest.skip("Set NEKOCLAW_TEST_DSH_BINARY to run the pinned dsh sleep/wake test")
    monkeypatch.setattr(nekoclaw_settings, "FIRST_TURN_DEVICE_TOOLS", True)
    original_patch = profile.build_patch

    def short_history(**kwargs):
        patch = original_patch(**kwargs)
        next(row for row in patch if row.get("id") == "compaction-basic")["config"]["retainTokens"] = 1
        return patch

    monkeypatch.setattr(profile, "build_patch", short_history)
    phase = "before_rest"
    seen, usage, operated = [], [], []
    epochs = set()
    browser = "mcp__gateway__browser_navigate_page"
    search = "mcp__gateway__capability_search"
    call_tool = "mcp__gateway__capability_call"

    def reply(body):
        names = {tool["function"]["name"] for tool in body["tools"]}
        summary = "Output only the checkpoint text" in json.dumps(body["messages"][-1])
        ordinal = sum(p == phase and not checkpoint for p, checkpoint, _ in seen)
        seen.append((phase, summary, names))
        tokens = 100
        if summary:
            chunks = [sse_text("Checkpoint: private fixture text."), sse_finish()] if complete else [sse_finish()]
        elif phase == "before_rest" and ordinal == 0:
            chunks = [sse_text("Synthetic history. " * 4000),
                      sse_tool_call("mcp__host__schedule_wake", {"delay_seconds": 3600, "note": "look again"}),
                      sse_finish("tool_calls")]
            tokens = 31_000
        elif phase == "before_rest" and ordinal == 1:
            chunks = [sse_tool_call(search, {"query": "unrelated fixture capability"}, "local-second"), sse_finish("tool_calls")]
        elif phase == "cold" and ordinal == 0:
            chunks = [sse_tool_call("mcp__host__wait_agent", {"timeout_ms": 20000}), sse_finish("tool_calls")]
        elif phase == "cold" and ordinal == 1:
            chunks = [sse_tool_call(search, {"group": "browser"}, "local-search"), sse_finish("tool_calls")]
        elif phase == "cold" and ordinal == 2:
            chunks = [sse_tool_call(call_tool, {"name": "browser_list_pages", "arguments": {}}, "local-call"), sse_finish("tool_calls")]
        elif phase == "cold" and ordinal == 3:
            chunks = [sse_tool_call("mcp__host__schedule_wake", {"delay_seconds": 3600, "note": "look again"}, "second-sleep"),
                      sse_finish("tool_calls")]
        else:
            chunks = [sse_text("Done."), sse_finish()]
        usage.append((tokens, 10))
        return [*chunks, sse_usage(tokens, 10)]

    with FakeOpenAIServer(reply) as model:
        async def model_config():
            return dict(model="fixture", base_url=model.base_url, api_key="loopback-only", auto_extra_body=False,
                        supports_images=True, context_window=32_000, max_output_tokens=1024)

        def factory(on_event, callbacks, installation):
            launch = profile.DshLaunch(home=tmp_path / "dsh", executable=Path(executable))
            runtime = DshRuntime(on_event, callbacks, launch=lambda: launch)
            original_view = runtime._endpoint._device_view

            async def repeated_receipt(process, session, epoch, refresh, *args):
                result = await original_view(process, session, epoch, refresh, *args)
                if epoch and result is not None and refresh:
                    # A retry of the same successful receipt must not produce a second SQLite event.
                    epochs.add((phase, session, epoch))
                    await original_view(process, session, epoch, refresh, *args)
                return result

            runtime._endpoint._device_view = repeated_receipt
            return runtime

        provider = BuiltinToolProvider(state=lambda group: (True, ""))

        async def run_fake(capability, *, commission_id, **unused):
            operated.append((capability.tool_name, commission_id))
            return BuiltinOutcome(True, "fixture page list")

        monkeypatch.setattr(provider, "run", run_fake)

        host = make_host(tmp_path, task_agent_model=model_config, decision_llm=FakeDecisionLLM(),
                         builtins=provider,
                         plugins_enabled=lambda: False, dsh=Installation(), dsh_runtime_factory=factory)
        try:
            await host.permission_store.set_mode("browser", "allow")
            commission_id = await _dispatch(host, "打开浏览器")

            async def resting():
                row = await host.ledger.get(commission_id)
                return row.resting and not host.task_agents.running

            await wait_until(resting, timeout=45, what="the dsh commission closing its session to rest")
            ordinary = [names for _, summary, names in seen if not summary]
            assert browser in ordinary[0] and (browser not in ordinary[-1]) == complete
            events = await host.ledger.events(commission_id)
            checkpoints = [e for e in events if e.type == "compacted"]
            assert any(summary for _, summary, _ in seen)
            assert len(checkpoints) == len(epochs)
            assert bool(checkpoints) == complete
            for checkpoint in checkpoints:
                assert set(checkpoint.payload) == {"seq", "count", "tokens_before"}
                assert checkpoint.payload["count"] is None and checkpoint.payload["tokens_before"] is None
            row = await host.ledger.get(commission_id)
            session = await host.ledger.get_session(row.session_id)
            assert (row.requests, row.tokens_in, row.tokens_out) == (
                len(usage), sum(i for i, _ in usage), sum(o for _, o in usage),
            )
            if not complete:
                assert session.compacted_at is None
                return
            assert session.compacted_at >= checkpoints[-1].ts

            phase = "cold"
            assert await host.task_agents.tell(commission_id, "继续写总结")
            await wait_until(lambda: any(p == "cold" and not s for p, s, _ in seen), timeout=45, what="the ordinary wake request")
            # The ordinary wake registered only discovery/call in this MCP scope.
            # A later browser instruction uses those tools until the session next opens.
            assert await host.task_agents.tell(commission_id, "打开浏览器")
            await wait_until(resting, timeout=45, what="the second rest")
            cold = [(names, request) for (p, s, names), request in zip(seen, model.requests) if p == "cold" and not s]
            assert all(browser not in names and {search, call_tool} <= names for names, _ in cold)
            assert any("打开浏览器" in str(message) for message in cold[1][1]["messages"][-2:])
            assert "browser_navigate_page" in str(cold[2][1]["messages"][-1])
            assert "fixture page list" in str(cold[3][1]["messages"][-1])
            assert operated == [("browser_list_pages", commission_id)]

            phase = "explicit"
            assert await host.task_agents.tell(commission_id, "打开浏览器")
            await wait_until(lambda: any(p == "explicit" and not s for p, s, _ in seen), timeout=45, what="the explicit wake")
            assert browser in next(names for p, s, names in seen if p == "explicit" and not s)
        finally:
            await host.shutdown()


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


async def test_an_unidentified_dsh_plugin_asks_each_time_unless_the_user_configured_it(hosts, tmp_path):
    host = _host(hosts, tmp_path, ScriptedLLM(), Installation())
    payload = {"tool": "plugin_weather", "category": "other", "arguments": {"city": "Oslo"}, "workdir": "w", "default": "allow"}
    asked = []

    async def once(commission_id, question):
        asked.append(question.options)
        return "once"

    host.permissions.set_asker(once)
    for _ in range(2):
        assert (await host.task_agents._cb_permission("c1", payload))["allow"] is True
    assert asked == [("once", "deny")] * 2

    await host.permission_store.set_mode("other", "allow")
    assert (await host.task_agents._cb_permission("c1", payload))["allow"] is True
    await host.permission_store.set_mode("other", "deny")
    refused = await host.task_agents._cb_permission("c1", payload)
    assert refused["allow"] is False and "settings" in refused["reason"]
    assert len(asked) == 2


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


async def test_selected_dsh_stays_in_use_during_gateway_setup(hosts, tmp_path, monkeypatch):
    import asyncio

    host = _host(hosts, tmp_path, ScriptedLLM([_report("ready", "done")]), Installation())
    setting_up, resume = asyncio.Event(), asyncio.Event()
    start = host.gateway.start

    async def wait_for_setup():
        setting_up.set()
        await resume.wait()
        return await start()

    monkeypatch.setattr(host.gateway, "start", wait_for_setup)
    dispatch = asyncio.create_task(_dispatch(host, "Open the browser"))
    try:
        await asyncio.wait_for(setting_up.wait(), 5)
        with pytest.raises(DshUnavailable, match="in use"):
            await host.uninstall_dsh()
        assert not host.fake.dsh[0].shut_down
    finally:
        resume.set()
        commission_id = await dispatch
    await wait_status(host, commission_id, "done")


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


@pytest.mark.parametrize(("mode", "choice", "expected"), [("auto", "once", 0), ("first_ask", "session", 1), ("always_ask", "once", 3)])
async def test_stock_job_list_uses_the_other_tool_policy(tmp_path, mode, choice, expected):
    from nekoclaw.dsh import profile
    from nekoclaw.dsh.driver import DshRuntime
    from tests.unit.nekoclaw.fake_openai_server import FakeOpenAIServer, sse_finish, sse_text, sse_tool_call

    binary = os.getenv("NEKOCLAW_TEST_DSH_BINARY")
    if not binary:
        pytest.skip("Set NEKOCLAW_TEST_DSH_BINARY to run the stock dsh test")
    def factory(on_event, callbacks, installation):
        return DshRuntime(on_event, callbacks, launch=lambda: profile.DshLaunch(home=tmp_path / "dsh", executable=Path(binary)))

    replies = iter([[sse_tool_call("job_list", {}, f"list{i}"), sse_finish("tool_calls")] for i in range(3)])
    questions = []
    async def answer(_, ask):
        questions.append(ask)
        return choice
    with FakeOpenAIServer(lambda _: next(replies, [sse_text("Done"), sse_finish()])) as model:
        async def model_config():
            return dict(model="fixture", base_url=model.base_url, api_key="local", auto_extra_body=False)
        host = make_host(tmp_path, task_agent_model=model_config, decision_llm=FakeDecisionLLM(),
                         plugins_enabled=lambda: False, dsh=Installation(installed=True), dsh_runtime_factory=factory)
        try:
            await host.permission_store.set_mode("other", mode)
            host.permissions.set_asker(answer)
            await _dispatch(host, "Check the fixture jobs")
            await wait_until(lambda: len(model.requests) >= 4, timeout=90, what="all three stock job reads")
            assert len(questions) == expected
            assert all(ask.category == "other" and "always" not in ask.options for ask in questions)
            for i, req in enumerate(model.requests[1:4]):
                message = next(m for m in req["messages"] if m.get("tool_call_id") == f"list{i}")
                assert "no background jobs" in message["content"].lower()
        finally:
            await host.shutdown()
