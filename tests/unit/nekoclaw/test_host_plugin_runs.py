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

"""``PluginRunner`` when the plugin server misbehaves: cancels, polls, closing."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from nekoclaw.host.plugin_runs import MAX_CONSECUTIVE_POLL_ERRORS, PluginCatalog, PluginRunner
from tests.unit.nekoclaw.host_fakes import wait_until
from tests.unit.nekoclaw.plugin_server_fake import PLUGIN_SERVER_URL, FakePluginServer, entry, finish, plugin


@pytest.fixture
def server(monkeypatch: Any) -> FakePluginServer:
    return FakePluginServer.install(monkeypatch)


class Faults:
    """What the wrapped client does instead of reaching the server."""

    def __init__(self) -> None:
        # Raise a connection error for this many cancel requests.
        self.failing_cancels = 0
        self.cancel_requests = 0
        # Answer a run's status with something that is not a run record.
        self.unreadable_polls = False
        self.polls = 0
        # Hold back the answer to POST /runs until this is set.
        self.answer_may_return: asyncio.Event | None = None
        # Called just before the answer to POST /runs is handed back.
        self.on_runs_answer: Any = None


def _runner(server: FakePluginServer, faults: Faults) -> PluginRunner:
    class Client:
        def __init__(self, **kwargs: Any) -> None:
            self._client = server.client(**kwargs)

        async def __aenter__(self) -> Any:
            await self._client.__aenter__()
            return self

        async def __aexit__(self, *exc: Any) -> Any:
            return await self._client.__aexit__(*exc)

        async def post(self, url: str, **kwargs: Any) -> Any:
            if url.endswith("/cancel"):
                faults.cancel_requests += 1
                if faults.failing_cancels > 0:
                    faults.failing_cancels -= 1
                    raise httpx.ConnectError("refused")
            response = await self._client.post(url, **kwargs)
            if url.endswith("/runs") and faults.answer_may_return is not None:
                await faults.answer_may_return.wait()
            if url.endswith("/runs") and faults.on_runs_answer is not None:
                faults.on_runs_answer()
            return response

        async def get(self, url: str, **kwargs: Any) -> Any:
            if "/runs/" in url and not url.endswith("/export"):
                faults.polls += 1
                if faults.unreadable_polls:
                    return httpx.Response(200, content=b"<html>proxy error</html>")
            return await self._client.get(url, **kwargs)

    catalog = PluginCatalog(base_url=PLUGIN_SERVER_URL, client_factory=server.client)
    return PluginRunner(
        catalog,
        base_url=PLUGIN_SERVER_URL,
        client_factory=Client,
        default_entry_timeout=lambda: None,
        poll_interval=0.01,
    )


def _slow_weather(server: FakePluginServer) -> asyncio.Event:
    started = asyncio.Event()

    async def slow(args: dict[str, Any]) -> dict[str, Any]:
        started.set()
        await asyncio.Event().wait()
        return finish({"reply": "never"})

    server.add_plugin(plugin("weather", "Weather lookups", [entry("today", "Today's weather")]), today=slow)
    return started


async def _started_run(server: FakePluginServer, runner: PluginRunner, started: asyncio.Event) -> tuple[Any, str]:
    calling = asyncio.ensure_future(
        runner.run_entry(commission_id="c1", plugin_id="weather", entry_id="today", args={})
    )
    await asyncio.wait_for(started.wait(), 5)
    await wait_until(lambda: runner.active_runs("c1"), what="the run being known to the caller")
    (run,) = server.runs()
    return calling, run.run_id


async def test_a_cancel_that_failed_is_sent_again(server):
    started = _slow_weather(server)
    faults = Faults()
    faults.failing_cancels = 1
    runner = _runner(server, faults)
    calling, run_id = await _started_run(server, runner, started)

    first = await runner.cancel_run(run_id, "stop")
    second = await runner.cancel_run(run_id, "stop")
    third = await runner.cancel_run(run_id, "stop")

    assert (first, second, third) == (False, True, True)
    # Asked again after the failure; not again once the server accepted it.
    assert faults.cancel_requests == 2
    assert server.cancelled == [("weather", run_id)]
    calling.cancel()
    await asyncio.gather(calling, return_exceptions=True)
    await runner.aclose()


async def test_cancels_of_one_run_at_the_same_time_send_one_request(server):
    started = _slow_weather(server)
    faults = Faults()
    runner = _runner(server, faults)
    calling, run_id = await _started_run(server, runner, started)

    answers = await asyncio.gather(runner.cancel_run(run_id), runner.cancel_run(run_id))

    assert answers == [True, True]
    assert faults.cancel_requests == 1
    calling.cancel()
    await asyncio.gather(calling, return_exceptions=True)
    await runner.aclose()


async def test_unreadable_status_answers_end_the_wait(server):
    started = _slow_weather(server)
    faults = Faults()
    runner = _runner(server, faults)
    calling, run_id = await _started_run(server, runner, started)

    faults.unreadable_polls = True
    polls_before = faults.polls
    outcome = await asyncio.wait_for(calling, 5)

    assert outcome.success is False and outcome.status == "failed"
    assert "polling failed" in str(outcome.error)
    assert faults.polls - polls_before == MAX_CONSECUTIVE_POLL_ERRORS
    # Given up on, the run is cancelled rather than left going.
    await wait_until(lambda: server.cancelled == [("weather", run_id)], what="the run being cancelled")
    assert runner.active_runs("c1") == []
    await runner.aclose()


async def test_a_run_given_up_on_stays_known_until_its_cancel_is_accepted(server):
    started = _slow_weather(server)
    faults = Faults()
    faults.failing_cancels = 1
    runner = _runner(server, faults)
    calling, run_id = await _started_run(server, runner, started)

    faults.unreadable_polls = True
    outcome = await asyncio.wait_for(calling, 5)
    await wait_until(lambda: faults.cancel_requests == 1, what="the first cancel request")
    await runner.aclose()

    assert outcome.status == "failed"
    assert server.cancelled == []
    # The cancel did not go through: stopping the commission must still find the run.
    assert runner.active_runs("c1") == [run_id]

    asked = await runner.cancel_commission("c1", "stopped")

    assert asked == 1
    assert server.cancelled == [("weather", run_id)]
    assert runner.active_runs("c1") == []


async def test_closing_waits_for_the_cancel_of_a_run_created_while_closing(server):
    started = _slow_weather(server)
    faults = Faults()
    faults.answer_may_return = asyncio.Event()
    runner = _runner(server, faults)
    calling = asyncio.ensure_future(
        runner.run_entry(commission_id="c1", plugin_id="weather", entry_id="today", args={})
    )
    await asyncio.wait_for(started.wait(), 5)
    (run,) = server.runs()
    calling.cancel()
    await asyncio.gather(calling, return_exceptions=True)

    closing = asyncio.ensure_future(runner.aclose())
    await asyncio.sleep(0)
    faults.answer_may_return.set()
    await asyncio.wait_for(closing, 5)

    # Closed means the cancel of the run the late answer named has been sent.
    assert server.cancelled == [("weather", run.run_id)]


async def test_closing_just_after_a_late_answer_came_back_still_sends_its_cancel(server):
    started = _slow_weather(server)
    faults = Faults()
    faults.answer_may_return = asyncio.Event()
    back: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    faults.on_runs_answer = lambda: back.set_result(None)
    runner = _runner(server, faults)
    calling = asyncio.ensure_future(
        runner.run_entry(commission_id="c1", plugin_id="weather", entry_id="today", args={})
    )
    await asyncio.wait_for(started.wait(), 5)
    (run,) = server.runs()
    calling.cancel()
    await asyncio.gather(calling, return_exceptions=True)
    (request,) = runner._cancel_tasks

    faults.answer_may_return.set()
    _ = await back
    # The request has finished, and what is to run once it has not run yet.
    assert request.done()
    await runner.aclose()

    assert server.cancelled == [("weather", run.run_id)]


async def test_an_id_matching_several_entries_but_for_case_is_refused(server):
    called: list[str] = []

    def handler(name: str) -> Any:
        async def run(args: dict[str, Any]) -> dict[str, Any]:
            called.append(name)
            return finish({"reply": name})

        return run

    server.add_plugin(
        plugin(
            "tools",
            "Tools",
            [
                entry("run", "Run it"),
                entry("RUN", "Run it loudly"),
                entry("Run", "Internal", metadata={"agent_hidden": True}),
            ],
        ),
        run=handler("run"),
        RUN=handler("RUN"),
        Run=handler("Run"),
    )
    runner = _runner(server, Faults())

    outcome = await runner.run_entry(commission_id="c1", plugin_id="tools", entry_id="Run", args={})
    single = await runner.run_entry(commission_id="c1", plugin_id="tools", entry_id="run", args={})

    assert (outcome.accepted, outcome.reason) == (False, "invalid_entry_id")
    assert "ambiguous" in str(outcome.error)
    # The hidden entry of exactly that name was not reached.
    assert called == ["run"]
    assert single.success


async def test_a_run_whose_caller_went_away_stays_known_until_its_cancel_is_accepted(server):
    started = _slow_weather(server)
    faults = Faults()
    faults.failing_cancels = 1
    runner = _runner(server, faults)
    calling, run_id = await _started_run(server, runner, started)

    calling.cancel()
    await asyncio.gather(calling, return_exceptions=True)
    await wait_until(lambda: faults.cancel_requests == 1, what="the first cancel request")
    await runner.aclose()

    assert server.cancelled == []
    # The cancel did not go through: stopping the commission must still find the run.
    assert runner.active_runs("c1") == [run_id]

    asked = await runner.cancel_commission("c1", "stopped")

    assert asked == 1
    assert server.cancelled == [("weather", run_id)]
    assert runner.active_runs("c1") == []


async def test_a_run_whose_caller_went_away_is_dropped_once_its_cancel_is_accepted(server):
    started = _slow_weather(server)
    faults = Faults()
    runner = _runner(server, faults)
    calling, run_id = await _started_run(server, runner, started)

    calling.cancel()
    await asyncio.gather(calling, return_exceptions=True)
    await runner.aclose()

    assert server.cancelled == [("weather", run_id)]
    assert runner.active_runs("c1") == []
    assert await runner.cancel_commission("c1", "stopped") == 0
    assert faults.cancel_requests == 1


async def test_a_run_created_after_its_caller_went_away_stays_known_until_its_cancel_is_accepted(server):
    started = _slow_weather(server)
    faults = Faults()
    faults.answer_may_return = asyncio.Event()
    faults.failing_cancels = 1
    runner = _runner(server, faults)
    calling = asyncio.ensure_future(
        runner.run_entry(commission_id="c1", plugin_id="weather", entry_id="today", args={})
    )
    await asyncio.wait_for(started.wait(), 5)
    (run,) = server.runs()
    calling.cancel()
    await asyncio.gather(calling, return_exceptions=True)

    faults.answer_may_return.set()
    await wait_until(lambda: faults.cancel_requests == 1, what="the first cancel request")
    await runner.aclose()

    assert server.cancelled == []
    # The cancel did not go through: stopping the commission must still find the run.
    assert runner.active_runs("c1") == [run.run_id]

    asked = await runner.cancel_commission("c1", "stopped")

    assert asked == 1
    assert server.cancelled == [("weather", run.run_id)]
    assert runner.active_runs("c1") == []
