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

"""When the agent process closes the browser the browser tools started, and when it leaves it open.

It is closed when the user says the browser is not wanted: they switch the
browser tools off, switch the master switch off, change the browser
settings, or quit. It is left open, with its window and the connection to
it, when the tasks are stopped (``end_all``) and when the agent model stops
answering and the switch is put off for them: they may want to go on
reading what is on its pages. A browser without a window is closed either
way.

The agent process's own provider drives a fake browser here
(``fake_cdp_browser.py``): the routes, the provider, the browser tools and
the session are the real ones, and nothing looks for, starts or closes a
real browser.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nekoclaw.builtin.browser.tools import BrowserTools
from nekoclaw.host.capabilities import BUILTIN_GROUP_BROWSER
from tests.unit.nekoclaw.fake_cdp_browser import ORDER, THANKS, BrowserWorld, serve_shop

capabilities = importlib.import_module("app.agent_server.capabilities")
api_routes = importlib.import_module("app.agent_server.api_routes")
api_runtime = importlib.import_module("app.agent_server.api_runtime")
nekoclaw_builtins = importlib.import_module("app.agent_server.nekoclaw_builtins")

pytestmark = pytest.mark.unit

OWN = {48911, 48915}
OWN_PATTERNS = ["*://*:48911/*", "*://*:48915/*"]
END_ALL = {"action": "end_all"}


class Probe:
    """Stands in for ``AgentModelProbe``: answers the probe as told."""

    def __init__(self, result: Any = (True, "")) -> None:
        self.init_ok = True
        self.last_error: str | None = None
        self.result = result

    def check_connectivity(self, **_options: Any) -> Any:
        if isinstance(self.result, BaseException):
            raise self.result
        self.init_ok = bool(self.result[0])
        return self.result


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> BrowserWorld:
    made = BrowserWorld(monkeypatch, tmp_path)
    serve_shop(made.browser)
    return made


@pytest.fixture
async def stage(monkeypatch: pytest.MonkeyPatch, world: BrowserWorld, tmp_path: Any) -> Any:
    """The switches on, a model that answers, and the process's provider driving the fake browser."""
    state = capabilities._shared.Modules
    provider = nekoclaw_builtins.provider
    monkeypatch.setattr(state, "analyzer_enabled", True)
    monkeypatch.setattr(
        state, "agent_flags", {"computer_use_enabled": False, "browser_use_enabled": True, "user_plugin_enabled": False}
    )
    monkeypatch.setattr(
        state,
        "capability_cache",
        {
            "computer_use": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
            "browser_use": {"ready": True, "reason": ""},
            "user_plugin": {"ready": True, "reason": ""},
        },
    )
    monkeypatch.setattr(state, "notification", None)
    monkeypatch.setattr(state, "model_probe", Probe())
    monkeypatch.setattr(state, "browser_use_lifecycle_seq", state.browser_use_lifecycle_seq)
    monkeypatch.setattr(state, "state_revision", state.state_revision)
    monkeypatch.setattr(state, "_background_tasks", set())
    monkeypatch.setattr(capabilities, "_llm_check_lock", asyncio.Lock())
    monkeypatch.setattr(nekoclaw_builtins, "_in_use", {})

    async def ignore(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(api_routes, "_emit_agent_status_update", ignore)
    monkeypatch.setattr(capabilities, "_emit_agent_status_update", ignore)
    monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: {"ready": True, "reasons": [], "is_free_version": False})
    # (Saving the browser settings asks it of the configuration on the disk otherwise.)
    monkeypatch.setattr(api_runtime, "_check_agent_api_gate", lambda: {"ready": True, "reasons": [], "is_free_version": False})
    monkeypatch.setattr(api_routes, "_ensure_plugin_lifecycle_stopped", ignore)
    # Whether the keyboard and mouse can be used is asked of the real desktop otherwise.
    monkeypatch.setattr(
        capabilities, "_keyboard_mouse_availability", lambda: {"enabled": True, "ready": False, "reasons": ["AGENT_NO_DISPLAY"]}
    )
    # No task is under way in these tests; stopping them is what the host's own tests cover.
    monkeypatch.setattr(api_routes, "stop_everything", AsyncMock(return_value=0))
    # The search for a browser, and the settings it reads, are this test's own.
    monkeypatch.setattr(
        "nekoclaw.builtin.browser.launcher.find_browsers",
        lambda *args, **kwargs: [SimpleNamespace(kind="chrome", name="Chrome", executable="/opt/chrome")],
    )
    monkeypatch.setattr("nekoclaw.builtin.browser.settings._root", lambda: tmp_path / "settings" / "nekoclaw")
    monkeypatch.setattr(capabilities, "_browser_search", None)

    tools = BrowserTools(world.session(own_ports=lambda: OWN))
    monkeypatch.setattr(provider, "_browser", tools)
    monkeypatch.setattr(provider, "_desktop", None)
    monkeypatch.setattr(provider, "_shut_down", False)
    monkeypatch.setattr(provider, "_killing", set())
    monkeypatch.setattr(provider, "_stopping_browser", 0)
    monkeypatch.setattr(provider, "_last_window", None)
    monkeypatch.setattr(provider, "_running", {})
    # A browser that is to be killed would be a failure of these tests: none comes to that.
    killed: list[int] = []
    monkeypatch.setattr(provider, "kill_browser", lambda: killed.append(1))

    async def browse(name: str = "navigate_page", **arguments: Any) -> Any:
        return await asyncio.wait_for(provider._run_browser(f"browser_{name}", arguments), 10.0)

    async def settled() -> None:
        """Let what the routes left to the background run to its end."""
        for _ in range(3):
            pending = [task for task in state._background_tasks if not task.done()]
            if pending:
                await asyncio.wait(pending, timeout=10.0)
            await asyncio.sleep(0)

    yield SimpleNamespace(state=state, provider=provider, tools=tools, session=tools.session, browse=browse, settled=settled, killed=killed)

    for gate in (world.launch_gate, world.connect_gate, world.close_gate):
        if gate is not None:
            gate.set()
    await settled()
    world.close_error = None
    await tools.aclose()
    for _ in range(5):
        await asyncio.sleep(0)


async def _on_the_order_form(stage: Any) -> None:
    outcome = await stage.browse(url=ORDER)
    assert outcome.success, outcome.text


def _notification(stage: Any) -> dict[str, Any] | None:
    return json.loads(stage.state.notification) if stage.state.notification else None


# ---------------------------------------------------------------------------
# Stopping every task
# ---------------------------------------------------------------------------


async def test_stopping_every_task_leaves_the_browser_window_open(stage: Any, world: BrowserWorld) -> None:
    await _on_the_order_form(stage)
    connection = world.connection
    asked = world.browser.mark()

    result = await api_routes.admin_control(dict(END_ALL))

    assert result == {"success": True, "message": "all tasks terminated and cleared"}
    # Not asked to close, not killed, not let go of.
    assert world.closed == [] and world.browser.close_requests == 0 and stage.killed == []
    assert stage.session.connected and world.connection is connection and not connection.closed
    # Its pages are as they were, for the user to go on reading.
    assert [page.url for page in stage.session.pages()] == [ORDER]
    # Nothing at all was said to the browser.
    assert world.browser.since(asked) == []
    # And no call was kept waiting for a closing that never was.
    assert stage.provider._stopping_browser == 0
    # Whether the tools can be used is as it was.
    assert stage.state.agent_flags["browser_use_enabled"] is True
    assert stage.state.capability_cache["browser_use"] == {"ready": True, "reason": ""}


async def test_the_browser_that_was_left_open_stays_kept_from_the_ports_of_this_application(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)

    await api_routes.admin_control(dict(END_ALL))

    # The page the task agent left there goes on running, with nobody looking: a request of its own to
    # one of the ports is still held for this process, and failed.
    held = world.browser.hold_request("http://127.0.0.1:48911/api/config")
    for _ in range(200):
        if world.browser.fetch_answers:
            break
        await asyncio.sleep(0.005)
    assert world.browser.fetch_answers == [(held, "BlockedByClient", None)]
    # For the browser as a whole, as from its start.
    assert world.browser.fetch_enabled[0] == (None, OWN_PATTERNS)


async def test_the_next_task_drives_the_browser_that_was_left_open(stage: Any, world: BrowserWorld) -> None:
    await _on_the_order_form(stage)
    connection = world.connection
    # What every task that ends does (the host tells the provider when the browser is given back).
    await stage.provider.released(BUILTIN_GROUP_BROWSER, "commission-1")
    await api_routes.admin_control(dict(END_ALL))

    # The uids the stopped task was given are not the next one's to act on.
    stale = await stage.browse("click", uid="1_4")
    assert not stale.success and "No snapshot found" in stale.text
    outcome = await stage.browse(url=THANKS)

    assert outcome.success, outcome.text
    # The same browser, over the same connection: none was started, none connected to anew.
    assert len(world.launched) == 1 and len(world.connected) == 1 and world.connection is connection
    assert [page.url for page in stage.session.pages()] == [THANKS]


async def test_stopping_every_task_twice_leaves_the_window_open_both_times(stage: Any, world: BrowserWorld) -> None:
    await _on_the_order_form(stage)

    await api_routes.admin_control(dict(END_ALL))
    await api_routes.admin_control(dict(END_ALL))

    assert world.closed == [] and stage.session.connected and stage.killed == []


async def test_stopping_every_task_before_any_browser_was_started_starts_none(stage: Any, world: BrowserWorld) -> None:
    result = await api_routes.admin_control(dict(END_ALL))

    assert result["success"] is True
    assert world.launched == [] and world.connected == [] and world.closed == []


async def test_a_browser_without_a_window_is_closed_when_every_task_is_stopped(stage: Any, world: BrowserWorld) -> None:
    world.settings(headless=True)
    await _on_the_order_form(stage)

    await api_routes.admin_control(dict(END_ALL))

    # Nobody could see it, let alone close it by hand: left open it would run on unseen.
    assert len(world.closed) == 1 and world.closed[0][0].owned is True
    assert not stage.session.connected and not stage.session.holds_a_browser
    assert stage.killed == []


@pytest.mark.parametrize("headless", [False, True])
async def test_a_browser_found_running_is_left_open_or_closed_by_what_it_says_of_itself(
    stage: Any, world: BrowserWorld, headless: bool
) -> None:
    # Left by an earlier run of the application, with the settings of then.
    world.found_running = True
    world.browser.headless = headless
    await _on_the_order_form(stage)

    await api_routes.admin_control(dict(END_ALL))

    assert (len(world.closed) == 1) is headless
    assert stage.session.connected is not headless


async def test_a_browser_found_running_that_does_not_say_whether_it_has_a_window_is_left_open(
    stage: Any, world: BrowserWorld
) -> None:
    world.found_running = True
    world.browser.headless = True
    world.browser.refuse("Browser.getVersion", "'Browser.getVersion' wasn't found", -32601)
    await _on_the_order_form(stage)

    await api_routes.admin_control(dict(END_ALL))

    # Taken for one with a window: a window taken from the user is the worse mistake.
    assert world.closed == [] and stage.session.connected


async def test_the_users_own_browser_is_let_go_of_when_every_task_is_stopped(stage: Any, world: BrowserWorld) -> None:
    world.settings(mode="attach")
    mail = world.browser.add_page("https://mail.example/inbox", "Inbox")
    await _on_the_order_form(stage)
    connection = world.connection

    await api_routes.admin_control(dict(END_ALL))

    # As before: theirs is never closed, and never stays connected to once the work is stopped.
    assert connection.closed and not stage.session.connected
    assert [browser.owned for browser, _connection in world.closed] == [False]
    assert world.browser.close_requests == 0 and mail.target_id in world.browser.pages


async def test_a_browser_whose_windows_the_user_closed_is_closed_when_every_task_is_stopped(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    # The user closes its last window, and the browser runs on in the background: still connected to,
    # with no window for anyone to look at or to close.
    for page in list(world.browser.pages.values()):
        world.browser.remove_page(page)
    assert stage.session.connected and stage.provider.browser_window_stays() is False

    await api_routes.admin_control(dict(END_ALL))

    assert len(world.closed) == 1 and not stage.session.holds_a_browser


async def test_a_model_that_stops_answering_closes_a_browser_whose_windows_the_user_closed(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    for page in list(world.browser.pages.values()):
        world.browser.remove_page(page)
    stage.state.model_probe = Probe((False, "AGENT_LLM_UNREACHABLE"))

    await capabilities._fire_agent_llm_connectivity_check()

    assert len(world.closed) == 1 and not stage.session.holds_a_browser


async def test_a_browser_whose_connection_is_lost_is_closed_when_every_task_is_stopped(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    world.browser.disconnect()

    await api_routes.admin_control(dict(END_ALL))

    # Left open it would not be kept from this application's ports: closed, as it always was.
    assert len(world.closed) == 1 and not stage.session.holds_a_browser


# ---------------------------------------------------------------------------
# The user switched the browser tools, or the master switch, off
# ---------------------------------------------------------------------------


async def _switch_the_master_off(how: str) -> None:
    if how == "admin_control":
        await api_routes.admin_control({"action": "disable_analyzer"})
    else:
        await api_routes.agent_command({"command": "set_agent_enabled", "enabled": False, "_persist_intent": False})


async def test_switching_the_browser_tools_off_leaves_the_browser_window_open(stage: Any, world: BrowserWorld) -> None:
    await _on_the_order_form(stage)
    connection = world.connection
    asked = world.browser.mark()

    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    await stage.settled()

    # Not asked to close, not killed, not let go of: the user may go on reading what is in it.
    assert world.closed == [] and world.browser.close_requests == 0 and stage.killed == []
    assert stage.session.connected and world.connection is connection and not connection.closed
    assert [page.url for page in stage.session.pages()] == [ORDER]
    # Nothing at all was said to the browser.
    assert world.browser.since(asked) == []
    # And nothing is done in it for a task agent while the tools are off.
    assert stage.state.agent_flags["browser_use_enabled"] is False
    refused = await stage.browse(url=THANKS)
    assert not refused.success and "browser control is" in refused.text
    assert [page.url for page in stage.session.pages()] == [ORDER]


async def test_the_window_left_open_when_the_tools_were_switched_off_stays_kept_from_the_ports_of_this_application(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)

    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    await stage.settled()

    held = world.browser.hold_request("http://127.0.0.1:48911/api/config")
    for _ in range(200):
        if world.browser.fetch_answers:
            break
        await asyncio.sleep(0.005)
    assert world.browser.fetch_answers == [(held, "BlockedByClient", None)]


async def test_switching_the_browser_tools_off_closes_a_browser_without_a_window(stage: Any, world: BrowserWorld) -> None:
    world.settings(headless=True)
    await _on_the_order_form(stage)

    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    await stage.settled()

    # Nobody could see it, let alone close it by hand.
    assert len(world.closed) == 1 and world.closed[0][0].owned is True
    assert not stage.session.connected and not stage.session.holds_a_browser


async def test_switching_the_browser_tools_off_closes_a_browser_whose_windows_the_user_closed(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    for page in list(world.browser.pages.values()):
        world.browser.remove_page(page)

    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    await stage.settled()

    assert len(world.closed) == 1 and not stage.session.holds_a_browser


async def test_switching_the_browser_tools_off_lets_go_of_the_users_own_browser(stage: Any, world: BrowserWorld) -> None:
    world.settings(mode="attach")
    await _on_the_order_form(stage)
    connection = world.connection

    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    await stage.settled()

    # As before: theirs is never closed, and never stays connected to once the tools are off.
    assert connection.closed and not stage.session.connected
    assert [browser.owned for browser, _connection in world.closed] == [False]
    assert world.browser.close_requests == 0


async def test_switching_the_tools_on_again_drives_the_window_that_was_left_open(stage: Any, world: BrowserWorld) -> None:
    await _on_the_order_form(stage)
    connection = world.connection
    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    await stage.settled()

    await api_routes.set_agent_flags({"browser_use_enabled": True, "_persist_intent": False})
    await stage.settled()
    outcome = await stage.browse(url=THANKS)

    assert outcome.success, outcome.text
    # The same browser, over the same connection: none was started, none connected to anew.
    assert len(world.launched) == 1 and len(world.connected) == 1 and world.connection is connection
    assert world.closed == []


async def test_stopping_every_task_right_after_the_browser_tools_were_switched_off_leaves_the_window_open(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)

    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    # What looks at the browser for the switch has not had a turn yet, and is stopped with everything
    # else in the background.
    (looking,) = stage.state._background_tasks
    await api_routes.admin_control(dict(END_ALL))

    assert looking.cancelled()
    assert world.closed == [] and stage.session.connected and stage.killed == []


async def test_stopping_every_task_right_after_a_browser_without_a_window_was_switched_off_closes_it(
    stage: Any, world: BrowserWorld
) -> None:
    world.settings(headless=True)
    await _on_the_order_form(stage)

    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    (closing,) = stage.state._background_tasks
    assert world.closed == []
    await api_routes.admin_control(dict(END_ALL))

    assert closing.cancelled()
    assert len(world.closed) == 1 and not stage.session.holds_a_browser
    assert stage.killed == []


async def test_stopping_every_task_while_a_switched_off_browser_without_a_window_is_being_closed_closes_it_again(
    stage: Any, world: BrowserWorld
) -> None:
    world.settings(headless=True)
    await _on_the_order_form(stage)
    world.close_gate = asyncio.Event()

    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    (closing,) = stage.state._background_tasks
    for _ in range(400):
        if world.closed:
            break
        await asyncio.sleep(0.005)
    assert len(world.closed) == 1
    # Half way through: the browser is still driven and connected to.
    stopping = asyncio.ensure_future(api_routes.admin_control(dict(END_ALL)))
    for _ in range(400):
        if len(world.closed) == 2:
            break
        await asyncio.sleep(0.005)
    world.close_gate.set()
    await asyncio.wait_for(stopping, 10.0)

    assert closing.cancelled()
    # The same browser, asked to close once more by the stopping itself.
    assert len(world.closed) == 2 and world.closed[0][0] is world.closed[1][0]
    assert not stage.session.holds_a_browser and stage.killed == []


@pytest.mark.parametrize("how", ["switched off", "switched off with the other switch"])
async def test_a_switch_put_off_says_nothing_against_the_browser_window(
    stage: Any, world: BrowserWorld, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    # What the routes leave to the background is kept from ever having a turn: the user stopping every
    # task at that very moment stops it before it has begun.
    kept: list[Any] = []
    monkeypatch.setattr(api_routes, "_create_tracked_task", kept.append)
    await _on_the_order_form(stage)

    if how == "switched off":
        await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    else:
        # The agent model is no longer configured, and the user asks for the keyboard and mouse.
        monkeypatch.setattr(
            api_routes,
            "_check_agent_api_gate",
            lambda: {"ready": False, "reasons": ["AGENT_ENDPOINT_NOT_CONFIGURED"], "is_free_version": False},
        )
        await api_routes.set_agent_flags({"computer_use_enabled": True, "_persist_intent": False})

    try:
        assert len(kept) == 1 and stage.state.agent_flags["browser_use_enabled"] is False
        assert stage.provider.browser_window_stays() is True
        await api_routes.admin_control(dict(END_ALL))
        assert world.closed == [] and stage.session.connected
    finally:
        for coro in kept:
            coro.close()


@pytest.mark.parametrize("how", ["admin_control", "agent_command"])
async def test_switching_the_master_switch_off_leaves_the_browser_window_open(
    stage: Any, world: BrowserWorld, how: str
) -> None:
    await _on_the_order_form(stage)
    connection = world.connection

    await _switch_the_master_off(how)

    # It runs through the stopping of every task, with the switch of the browser tools left on.
    assert stage.state.analyzer_enabled is False and stage.state.agent_flags["browser_use_enabled"] is True
    assert world.closed == [] and stage.killed == []
    assert stage.session.connected and world.connection is connection
    assert [page.url for page in stage.session.pages()] == [ORDER]


@pytest.mark.parametrize("how", ["admin_control", "agent_command"])
async def test_switching_the_master_switch_off_closes_a_browser_without_a_window(
    stage: Any, world: BrowserWorld, how: str
) -> None:
    world.settings(headless=True)
    await _on_the_order_form(stage)

    await _switch_the_master_off(how)

    assert len(world.closed) == 1 and not stage.session.holds_a_browser
    assert stage.killed == []


async def test_a_browser_that_a_call_was_starting_when_the_tools_were_switched_off_is_left_open_once_it_is_up(
    stage: Any, world: BrowserWorld
) -> None:
    # A call of a task is connecting to the browser it started.
    world.connect_gate = asyncio.Event()
    calling = asyncio.ensure_future(stage.browse(url=ORDER))
    for _ in range(400):
        if world.connected:
            break
        await asyncio.sleep(0.005)
    assert world.connected and not stage.session.connected

    # The user switches the browser tools off. There is no window to speak of yet: what the call
    # leaves is what counts, and it is not told to give the start up.
    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    await asyncio.sleep(0.05)
    assert world.closed == []
    world.connect_gate.set()
    outcome = await calling
    await stage.settled()

    assert outcome.success, outcome.text
    assert world.closed == [] and stage.session.connected
    assert [page.url for page in stage.session.pages()] == [ORDER]


async def test_a_browser_without_a_window_that_could_not_be_closed_when_switched_off_is_killed_when_every_task_is_stopped(
    stage: Any, world: BrowserWorld
) -> None:
    world.settings(headless=True)
    await _on_the_order_form(stage)
    world.close_error = RuntimeError("the process would not die")
    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    await stage.settled()
    assert stage.session.holds_a_browser and stage.killed == []

    await api_routes.admin_control(dict(END_ALL))
    for _ in range(400):
        if stage.killed:
            break
        await asyncio.sleep(0.005)

    # As before: one that was to be closed does not run on because closing it failed.
    assert stage.killed == [1]


# ---------------------------------------------------------------------------
# The user changed the browser settings
# ---------------------------------------------------------------------------


async def test_saving_the_browser_settings_closes_the_browser(stage: Any, world: BrowserWorld) -> None:
    await _on_the_order_form(stage)

    await api_runtime.set_browser_settings({"headless": True})
    await stage.settled()

    # It was started with the settings before, and the new ones only take with the next one.
    assert len(world.closed) == 1 and not stage.session.holds_a_browser


@pytest.mark.parametrize("payload", [{}, {"headless": False}, {"mode": "own_profile", "executable": None}])
async def test_saving_the_browser_settings_as_they_were_leaves_the_browser_alone(
    stage: Any, world: BrowserWorld, payload: dict[str, Any]
) -> None:
    await _on_the_order_form(stage)
    asked = world.browser.mark()

    result = await api_runtime.set_browser_settings(dict(payload))
    # Nothing was left to the background, and nothing said against the browser.
    assert stage.state._background_tasks == set()
    await stage.settled()

    assert result["success"] is True
    assert result["settings"] == {"mode": "own_profile", "executable": None, "headless": False}
    assert world.closed == [] and stage.session.connected and world.browser.since(asked) == []
    assert stage.provider.browser_window_stays() is True


async def test_the_browser_settings_are_read_changed_and_saved_by_one_request_at_a_time(
    stage: Any, world: BrowserWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nekoclaw.builtin.browser.settings import BrowserSettingsStore

    held: list[bool] = []
    load, save = BrowserSettingsStore.load, BrowserSettingsStore.save

    def loading(self: Any) -> Any:
        held.append(api_runtime._browser_settings_lock.locked())
        return load(self)

    def saving(self: Any, settings: Any) -> None:
        held.append(api_runtime._browser_settings_lock.locked())
        save(self, settings)

    monkeypatch.setattr(BrowserSettingsStore, "load", loading)
    monkeypatch.setattr(BrowserSettingsStore, "save", saving)

    await api_runtime.set_browser_settings({"headless": True})
    await stage.settled()

    # What the save is compared with cannot be replaced by another request in between.
    assert held[:2] == [True, True]
    assert not api_runtime._browser_settings_lock.locked()


async def test_saving_the_browser_settings_a_second_time_does_not_close_the_browser_that_was_started_since(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    await api_runtime.set_browser_settings({"headless": True})
    await stage.settled()
    assert len(world.closed) == 1
    await _on_the_order_form(stage)

    # The page saves what it shows once more.
    await api_runtime.set_browser_settings({"headless": True})
    await stage.settled()

    assert len(world.launched) == 2 and len(world.closed) == 1 and stage.session.connected


async def test_stopping_every_task_right_after_the_browser_settings_were_saved_still_closes_the_browser(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    # Closing it takes a while.
    world.close_gate = asyncio.Event()

    # The switch stays on: only the settings changed, and they apply to the next browser that is started.
    await api_runtime.set_browser_settings({"headless": True})
    (closing,) = stage.state._background_tasks
    assert not closing.done() and stage.state.agent_flags["browser_use_enabled"] is True
    # Half way through, not merely begun: the browser was asked to close, once.
    for _ in range(400):
        if world.closed:
            break
        await asyncio.sleep(0.005)
    assert len(world.closed) == 1
    stopping = asyncio.ensure_future(api_routes.admin_control(dict(END_ALL)))
    for _ in range(400):
        if len(world.closed) == 2:
            break
        await asyncio.sleep(0.005)
    world.close_gate.set()
    await asyncio.wait_for(stopping, 10.0)

    assert closing.cancelled()
    # Left open, the next task would go on driving a browser that is not the one the user asked for.
    assert len(world.closed) == 2 and world.closed[0][0] is world.closed[1][0]
    assert not stage.session.holds_a_browser and stage.killed == []


async def test_the_browser_is_not_one_to_leave_open_from_the_moment_its_settings_were_changed(
    stage: Any, world: BrowserWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    # What the route leaves to the background is kept from ever having a turn: the user stopping every
    # task at that very moment stops it before it has begun.
    kept: list[Any] = []
    monkeypatch.setattr(api_runtime, "_create_tracked_task", kept.append)
    await _on_the_order_form(stage)
    assert stage.provider.browser_window_stays() is True

    await api_runtime.set_browser_settings({"headless": True})

    try:
        assert len(kept) == 1
        # Nothing was done to the browser yet, and it is already not one that stopping the tasks leaves open.
        assert world.closed == [] and stage.session.connected
        assert stage.provider.browser_window_stays() is False
        await api_routes.admin_control(dict(END_ALL))
        assert len(world.closed) == 1 and not stage.session.holds_a_browser
    finally:
        for coro in kept:
            coro.close()


async def test_switching_the_tools_off_does_not_leave_open_a_browser_whose_settings_were_changed(
    stage: Any, world: BrowserWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept: list[Any] = []
    monkeypatch.setattr(api_runtime, "_create_tracked_task", kept.append)
    await _on_the_order_form(stage)
    await api_runtime.set_browser_settings({"headless": True})

    try:
        # The closing for the settings never had a turn; the switch is put off next.
        await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
        await stage.settled()
        assert len(world.closed) == 1 and not stage.session.holds_a_browser
    finally:
        for coro in kept:
            coro.close()


async def test_a_browser_that_is_driven_again_after_it_was_closed_is_left_open_like_any_other(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    await api_runtime.set_browser_settings({"headless": True})
    await stage.settled()
    assert len(world.closed) == 1

    await _on_the_order_form(stage)
    await api_routes.admin_control(dict(END_ALL))

    # That the one before was not wanted says nothing about the one that was started since. (This
    # one has a window: the fake browser reads settings of its own, not the ones saved above.)
    assert len(world.launched) == 2 and len(world.closed) == 1 and stage.session.connected


# ---------------------------------------------------------------------------
# The agent model stopped answering, and the switch was put off for the user
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "reason", "notification"),
    [
        (
            (False, "AGENT_API_KEY_INVALID"),
            "AGENT_API_KEY_INVALID",
            {"code": "AGENT_AUTO_DISABLED_BROWSER", "details": {"reason_code": "AGENT_API_KEY_INVALID"}},
        ),
        (RuntimeError("the probe itself broke"), "AGENT_LLM_UNREACHABLE", {"code": "AGENT_LLM_CHECK_ERROR"}),
    ],
)
async def test_a_model_that_stops_answering_switches_the_tools_off_and_leaves_the_browser_window_open(
    stage: Any, world: BrowserWorld, result: Any, reason: str, notification: dict[str, Any]
) -> None:
    await _on_the_order_form(stage)
    stage.state.model_probe = Probe(result)

    await capabilities._fire_agent_llm_connectivity_check()

    # Everything that tells the user and keeps the tools from being called is as it always was.
    assert stage.state.agent_flags["browser_use_enabled"] is False
    assert stage.state.capability_cache["browser_use"] == {"ready": False, "reason": reason}
    assert _notification(stage) == notification
    refused = await stage.browse(url=THANKS)
    assert not refused.success and "browser control is" in refused.text
    # The window is not taken from them: they did not say the browser is not wanted.
    assert world.closed == [] and stage.session.connected and stage.killed == []
    assert [page.url for page in stage.session.pages()] == [ORDER]


async def test_a_model_that_stops_answering_closes_a_browser_without_a_window(stage: Any, world: BrowserWorld) -> None:
    world.settings(headless=True)
    await _on_the_order_form(stage)
    stage.state.model_probe = Probe((False, "AGENT_LLM_UNREACHABLE"))

    await capabilities._fire_agent_llm_connectivity_check()

    assert len(world.closed) == 1 and not stage.session.holds_a_browser
    assert stage.state.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_LLM_UNREACHABLE"}


async def test_the_window_left_open_for_a_model_that_stopped_answering_survives_the_stopping_of_every_task(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    stage.state.model_probe = Probe((False, "AGENT_LLM_UNREACHABLE"))
    await capabilities._fire_agent_llm_connectivity_check()
    assert stage.state.agent_flags["browser_use_enabled"] is False

    # The switch is off, and it was not the user who put it off.
    await api_routes.admin_control(dict(END_ALL))

    assert world.closed == [] and stage.session.connected


async def test_the_window_left_open_for_a_model_that_stopped_answering_survives_the_master_switch(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    stage.state.model_probe = Probe((False, "AGENT_LLM_UNREACHABLE"))
    await capabilities._fire_agent_llm_connectivity_check()

    await api_routes.admin_control({"action": "disable_analyzer"})

    assert world.closed == [] and stage.session.connected and stage.killed == []


async def test_switching_the_tools_on_again_after_the_model_is_back_drives_the_window_that_was_left_open(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    stage.state.model_probe = Probe((False, "AGENT_LLM_UNREACHABLE"))
    await capabilities._fire_agent_llm_connectivity_check()
    stage.state.model_probe = Probe()

    await api_routes.set_agent_flags({"browser_use_enabled": True, "_persist_intent": False})
    await stage.settled()
    outcome = await stage.browse(url=THANKS)

    assert outcome.success, outcome.text
    assert len(world.launched) == 1 and len(world.connected) == 1 and world.closed == []


# ---------------------------------------------------------------------------
# Closing it as such
# ---------------------------------------------------------------------------


async def test_closing_with_the_window_to_be_left_closes_nothing_and_still_says_why_the_tools_are_off(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)

    await capabilities._close_browser(capability_reason="AGENT_LLM_UNREACHABLE", leave_window=True)

    assert world.closed == [] and stage.session.connected
    assert stage.state.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_LLM_UNREACHABLE"}


async def test_closing_without_saying_so_closes_the_window(stage: Any, world: BrowserWorld) -> None:
    await _on_the_order_form(stage)

    # What quitting the application does.
    await capabilities._close_browser()

    assert len(world.closed) == 1 and not stage.session.holds_a_browser


async def test_closing_with_the_window_to_be_left_closes_one_that_is_no_longer_wanted(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    stage.provider.browser_no_longer_wanted()

    await capabilities._close_browser(capability_reason="AGENT_LLM_UNREACHABLE", leave_window=True)

    assert len(world.closed) == 1 and not stage.session.holds_a_browser


# ---------------------------------------------------------------------------
# The user closed the browser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("then", ["nothing", "every task stopped", "tools switched off and on", "master switch off and on"])
async def test_a_browser_the_user_closed_is_one_to_ask_about_whatever_was_stopped_or_switched_since(
    stage: Any, world: BrowserWorld, then: str
) -> None:
    await _on_the_order_form(stage)
    assert stage.provider.browser_closed_by_user() is False
    world.browser.disconnect()

    if then == "every task stopped":
        await api_routes.admin_control(dict(END_ALL))
    elif then == "tools switched off and on":
        await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
        await stage.settled()
        await api_routes.set_agent_flags({"browser_use_enabled": True, "_persist_intent": False})
        await stage.settled()
    elif then == "master switch off and on":
        await api_routes.admin_control({"action": "disable_analyzer"})
        await api_routes.admin_control({"action": "enable_analyzer"})
    if then != "nothing":
        # What was left of it was cleared away.
        assert len(world.closed) == 1 and not stage.session.holds_a_browser

    assert stage.provider.browser_closed_by_user() is True
    refused = await stage.browse(url=THANKS)
    assert not refused.success and refused.text.startswith("The browser was closed by the user.")
    assert len(world.launched) == 1 and len(world.connected) == 1

    agreement = stage.provider.browser_reopen_agreed()
    # Agreed to for one call: every other one is refused as before.
    assert agreement is not None and stage.provider.browser_closed_by_user() is True
    assert not (await stage.browse(url=THANKS)).success
    # As the host carries it out: by the capability, with the agreement the provider gave for it.
    (navigate,) = [item for item in stage.provider.available() if item.tool_name == "browser_navigate_page"]
    outcome = await asyncio.wait_for(
        stage.provider.run(navigate, commission_id="commission-1", arguments={"url": THANKS}, reopen_agreement=agreement),
        10.0,
    )
    assert outcome.success, outcome.text
    assert stage.provider.browser_closed_by_user() is False


async def test_a_browser_closed_for_changed_settings_is_started_again_without_asking(stage: Any, world: BrowserWorld) -> None:
    await _on_the_order_form(stage)
    await api_runtime.set_browser_settings({"headless": True})
    await stage.settled()
    assert len(world.closed) == 1

    assert stage.provider.browser_closed_by_user() is False
    outcome = await stage.browse(url=THANKS)
    assert outcome.success and len(world.launched) == 2


async def test_the_provider_ends_the_agreement_it_is_handed_back_and_no_other(stage: Any, world: BrowserWorld) -> None:
    await _on_the_order_form(stage)
    world.browser.disconnect()
    first = stage.provider.browser_reopen_agreed()
    second = stage.provider.browser_reopen_agreed()
    assert first is not None and second not in (None, first)

    # The first call is over before it got to the browser.
    stage.provider.browser_reopen_agreement_over(first)

    late = await asyncio.wait_for(stage.provider._run_browser("browser_navigate_page", {"url": THANKS}, first), 10.0)
    assert not late.success and late.text.startswith("The browser was closed by the user.")
    assert len(world.connected) == 1
    outcome = await asyncio.wait_for(stage.provider._run_browser("browser_navigate_page", {"url": THANKS}, second), 10.0)
    assert outcome.success, outcome.text


@pytest.mark.parametrize("how", ["admin_control", "agent_command"])
async def test_switching_the_master_switch_off_drops_what_was_kept_of_the_main_dialogue_for_task_agents(
    stage: Any, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    from nekoclaw.host import conversation

    kept = conversation.RecentConversation()
    monkeypatch.setattr(conversation, "_recent", kept)
    assert kept.note("Lan", conversation.ROLE_USER, "sort the photos please")
    assert [turn.text for turn in kept.recent("Lan")] == ["sort the photos please"]

    await _switch_the_master_off(how)

    # Then and there, with no message of the main dialogue coming by: nothing from before the
    # switch is handed to a task agent when it is switched on again.
    assert stage.state.analyzer_enabled is False
    assert kept.recent("Lan") == []


# ---------------------------------------------------------------------------
# The last capability switch went off: NekoClaw is off as with the master switch off
# ---------------------------------------------------------------------------


def _kept_for_task_agents(monkeypatch: pytest.MonkeyPatch) -> Any:
    from nekoclaw.host import conversation

    kept = conversation.RecentConversation()
    monkeypatch.setattr(conversation, "_recent", kept)
    assert kept.note("Lan", conversation.ROLE_USER, "sort the photos please")
    assert [turn.text for turn in kept.recent("Lan")] == ["sort the photos please"]
    return kept


async def test_switching_the_last_capability_switch_off_drops_what_was_kept_of_the_main_dialogue_for_task_agents(
    stage: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = _kept_for_task_agents(monkeypatch)

    # Only the browser tools are on (the fixture): with them off NekoClaw is off, whatever the
    # master switch says.
    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    await stage.settled()

    # Then and there, with no message of the main dialogue coming by: nothing from before is
    # handed to a task agent when a switch is put on again.
    assert stage.state.analyzer_enabled is True
    assert kept.recent("Lan") == []


async def test_switching_one_of_two_capability_switches_off_keeps_what_was_kept_for_task_agents(
    stage: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = _kept_for_task_agents(monkeypatch)
    stage.state.agent_flags["user_plugin_enabled"] = True

    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    await stage.settled()

    # NekoClaw is still on: the commission that is dispatched next is handed what was said.
    assert stage.state.agent_flags["browser_use_enabled"] is False
    assert [turn.text for turn in kept.recent("Lan")] == ["sort the photos please"]


@pytest.mark.parametrize("result", [(False, "AGENT_API_KEY_INVALID"), RuntimeError("the probe itself broke")])
async def test_a_model_that_stops_answering_drops_what_was_kept_when_it_puts_the_last_switch_off(
    stage: Any, monkeypatch: pytest.MonkeyPatch, result: Any
) -> None:
    kept = _kept_for_task_agents(monkeypatch)
    stage.state.model_probe = Probe(result)

    await capabilities._fire_agent_llm_connectivity_check()

    # Put off for the user, and the last one on: off is off.
    assert stage.state.agent_flags["browser_use_enabled"] is False
    assert kept.recent("Lan") == []


@pytest.mark.parametrize("found", ["no probe", "not usable"])
async def test_the_keyboard_and_mouse_found_unusable_drops_what_was_kept_when_theirs_was_the_last_switch_on(
    stage: Any, monkeypatch: pytest.MonkeyPatch, found: str
) -> None:
    from fastapi import HTTPException

    kept = _kept_for_task_agents(monkeypatch)
    stage.state.agent_flags.update({"computer_use_enabled": True, "browser_use_enabled": False})

    async def nothing(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(api_routes, "_fire_agent_llm_connectivity_check", nothing)
    monkeypatch.setattr(api_routes, "_model_images_status", nothing)
    monkeypatch.setattr(api_routes, "_try_refresh_model_probe", lambda **_options: False)
    monkeypatch.setattr(
        api_routes, "_keyboard_mouse_availability", lambda: {"enabled": True, "ready": False, "reasons": ["AGENT_NO_DISPLAY"]}
    )
    if found == "no probe":
        stage.state.model_probe = None
        with pytest.raises(HTTPException):
            await api_routes.computer_use_availability()
    else:
        await api_routes.computer_use_availability()
    for _ in range(3):
        await asyncio.sleep(0)

    assert stage.state.agent_flags["computer_use_enabled"] is False
    assert kept.recent("Lan") == []


async def test_user_plugins_that_do_not_come_up_drop_what_was_kept_when_theirs_was_the_last_switch_on(
    stage: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage.state.agent_flags.update({"browser_use_enabled": False})
    monkeypatch.setattr(stage.state, "user_plugin_lifecycle_seq", stage.state.user_plugin_lifecycle_seq)
    monkeypatch.setattr(stage.state, "_persistent_tasks", set())
    started = asyncio.Event()

    async def does_not_come_up(**_options: Any) -> bool:
        await started.wait()
        return False

    monkeypatch.setattr(api_routes, "_ensure_plugin_lifecycle_started", does_not_come_up)

    await api_routes.set_agent_flags({"user_plugin_enabled": True, "_persist_intent": False})
    # On while it is found out whether they come up, and something is said meanwhile.
    assert stage.state.agent_flags["user_plugin_enabled"] is True
    kept = _kept_for_task_agents(monkeypatch)
    started.set()
    pending = list(stage.state._persistent_tasks)
    assert pending
    await asyncio.wait(pending, timeout=10.0)

    assert stage.state.agent_flags["user_plugin_enabled"] is False
    assert kept.recent("Lan") == []


class _AsItWas:
    """Says what was kept for task agents at the moment something is waited for."""

    def __init__(self, kept: Any, answer: Any = None) -> None:
        self.kept, self.answer = kept, answer
        self.seen: list[list[str]] = []

    def now(self) -> None:
        self.seen.append([turn.text for turn in self.kept.recent("Lan")])

    async def waited_for(self, *_args: Any, **_kwargs: Any) -> Any:
        self.now()
        return self.answer

    def asked(self, *_args: Any, **_kwargs: Any) -> Any:
        self.now()
        return self.answer


@pytest.mark.parametrize("result", [(False, "AGENT_API_KEY_INVALID"), RuntimeError("the probe itself broke")])
async def test_what_was_kept_is_gone_before_the_browser_of_a_model_that_stopped_answering_is_waited_for(
    stage: Any, monkeypatch: pytest.MonkeyPatch, result: Any
) -> None:
    kept = _kept_for_task_agents(monkeypatch)
    stage.state.model_probe = Probe(result)
    as_it_was = _AsItWas(kept)
    monkeypatch.setattr(capabilities, "_close_browser", as_it_was.waited_for)

    await capabilities._fire_agent_llm_connectivity_check()

    # Dropped with the switch, and not after the browser is seen to: the switch may be on again by
    # then, and what was said before it went off would be kept.
    assert as_it_was.seen == [[]]
    assert kept.recent("Lan") == []


async def test_what_was_kept_is_gone_before_the_plugins_that_were_not_found_are_stopped(
    stage: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage.state.agent_flags.update({"browser_use_enabled": False})
    monkeypatch.setattr(stage.state, "user_plugin_lifecycle_seq", stage.state.user_plugin_lifecycle_seq)
    monkeypatch.setattr(stage.state, "_persistent_tasks", set())
    go = asyncio.Event()

    async def comes_up(**_options: Any) -> bool:
        await go.wait()
        return True

    async def none_there() -> dict[str, Any]:
        return {"status": "empty", "empty_responses": 3}

    monkeypatch.setattr(api_routes, "_ensure_plugin_lifecycle_started", comes_up)
    monkeypatch.setattr(api_routes, "_check_user_plugin_registry_readiness", none_there)

    await api_routes.set_agent_flags({"user_plugin_enabled": True, "_persist_intent": False})
    kept = _kept_for_task_agents(monkeypatch)
    as_it_was = _AsItWas(kept)
    monkeypatch.setattr(api_routes, "_ensure_plugin_lifecycle_stopped", as_it_was.waited_for)
    go.set()
    pending = list(stage.state._persistent_tasks)
    assert pending
    await asyncio.wait(pending, timeout=10.0)

    assert stage.state.agent_flags["user_plugin_enabled"] is False
    assert as_it_was.seen == [[]]
    assert kept.recent("Lan") == []


async def test_what_was_kept_is_gone_before_a_switch_that_takes_the_place_of_the_last_one_is_looked_into(
    stage: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = _kept_for_task_agents(monkeypatch)
    stage.state.agent_flags.update({"computer_use_enabled": True, "browser_use_enabled": False})
    as_it_was = _AsItWas(kept, answer=(True, ""))
    monkeypatch.setattr(api_routes, "_browser_dependency_status", as_it_was.asked)

    # The keyboard and mouse go off, and the browser tools are to come on: whether they can is
    # found out in a thread, and no switch is on meanwhile.
    await api_routes.set_agent_flags(
        {"computer_use_enabled": False, "browser_use_enabled": True, "_persist_intent": False}
    )
    await stage.settled()

    assert stage.state.agent_flags["browser_use_enabled"] is True
    assert as_it_was.seen == [[]]
    assert kept.recent("Lan") == []


_CAPABILITY_SWITCHES = {"computer_use_enabled", "browser_use_enabled", "user_plugin_enabled"}
# (A lambda is part of what it stands in: what it sets, that one sets.)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _own_nodes(function: ast.AST) -> list[ast.AST]:
    """The nodes of a function, without those of the functions defined inside it."""
    found: list[ast.AST] = []
    todo = list(ast.iter_child_nodes(function))
    while todo:
        node = todo.pop()
        found.append(node)
        if not isinstance(node, _FUNCTIONS):
            todo.extend(ast.iter_child_nodes(node))
    return found


def _scopes(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """Every function of a module by itself, and what the module does as it is loaded."""
    return [
        (getattr(scope, "name", "<module>"), scope)
        for scope in ast.walk(tree)
        if isinstance(scope, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _is_true(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_the_switches(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "agent_flags"


def _each_of(target: ast.AST) -> list[ast.AST]:
    """What a target stands for: each of them by itself, where it takes a row apart."""
    if isinstance(target, (ast.Tuple, ast.List)):
        return [inner for element in target.elts for inner in _each_of(element)]
    if isinstance(target, ast.Starred):
        return _each_of(target.value)
    return [target]


def _set_by(node: ast.AST) -> list[ast.AST]:
    """What a node sets, or takes out, as it stands there (a row that is taken apart is one of them)."""
    if isinstance(node, (ast.Assign, ast.Delete)):
        return list(node.targets)
    if isinstance(node, ast.AnnAssign):
        # (A type said for it and nothing more: nothing is set.)
        return [node.target] if node.value is not None else []
    if isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor, ast.comprehension)):
        return [node.target]
    if isinstance(node, ast.withitem) and node.optional_vars is not None:
        return [node.optional_vars]
    return []


def _puts_a_capability_switch_off(node: ast.AST) -> bool:
    """Whether a statement may put a capability switch off.

    One of them set to anything but True, or taken out; and the whole
    table of them changed at once, or put in the place of the one
    there is: whatever that sets them to, it is not looked at here.
    Nor is it where a switch is set as one of a row that is taken
    apart, or by what a loop or a ``with`` hands it.
    """
    for whole in _set_by(node):
        for target in _each_of(whole):
            if _is_the_switches(target):
                return True
            if (
                isinstance(target, ast.Subscript)
                and _is_the_switches(target.value)
                and not (isinstance(target.slice, ast.Constant) and target.slice.value not in _CAPABILITY_SWITCHES)
                # (Put on, as it stands there, with a type said for it or without: not what
                # toggles it, or sets it by something. A row that is taken apart is no True.)
                and not (isinstance(node, (ast.Assign, ast.AnnAssign)) and _is_true(node.value))
            ):
                return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and _is_the_switches(node.func.value):
        return node.func.attr in {"update", "clear", "pop", "popitem", "setdefault"}
    return False


def _puts_the_master_switch_off(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Assign)
        and not _is_true(node.value)
        and any(isinstance(target, ast.Attribute) and target.attr == "analyzer_enabled" for target in node.targets)
    )


_LEAVES_OR_WAITS = (
    ast.Await, ast.AsyncWith, ast.AsyncFor, ast.Return, ast.Raise, ast.Yield, ast.YieldFrom, ast.Break, ast.Continue
)


def _leaves_or_waits(statement: ast.AST) -> bool:
    """Whether a statement waits for something, or leaves the block it stands in.

    By what it does itself: what a function defined in it would do
    when it is called is not looked at. A row made by waiting for
    each of its parts waits as well.
    """
    if isinstance(statement, _FUNCTIONS):
        return False
    for inner in [statement, *_own_nodes(statement)]:
        if isinstance(inner, _LEAVES_OR_WAITS):
            return True
        if isinstance(inner, ast.comprehension) and inner.is_async:
            return True
    return False


def _master_switch_offs(function: ast.AST) -> list[bool]:
    """For every place a function puts the master switch off: whether what was kept is dropped right after it.

    Right after it: by a statement of the same block that follows
    it, before anything in between waits for something or leaves
    the block. One place that does so does not stand for another
    that does not.
    """
    found: list[tuple[int, bool]] = []
    for node in [function, *_own_nodes(function)]:
        for block in ("body", "orelse", "finalbody"):
            statements = getattr(node, block, None)
            if not isinstance(statements, list):
                continue
            for index, statement in enumerate(statements):
                if not _puts_the_master_switch_off(statement):
                    continue
                dropped = False
                for later in statements[index + 1 :]:
                    if isinstance(later, ast.Expr) and _called(later.value) == "forget_turns_for_task_agents":
                        dropped = True
                        break
                    if _leaves_or_waits(later):
                        break
                found.append((statement.lineno, dropped))
    return [dropped for _line, dropped in sorted(found)]


def _called(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    return getattr(node.func, "id", None) or getattr(node.func, "attr", None)


def test_a_switch_is_put_off_though_what_was_kept_could_not_be_dropped_and_that_is_seen(
    stage: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = capabilities._shared
    said: list[str] = []

    def cannot() -> None:
        raise RuntimeError("what was kept cannot be got at")

    monkeypatch.setattr(api_runtime, "forget_turns_if_nekoclaw_is_off", cannot)
    monkeypatch.setattr(shared.logger, "warning", lambda message, *args, **kwargs: said.append(str(message)))

    shared._put_switch_off("browser_use_enabled")

    # The switch is off all the same. And it does not go by unseen: what was said before the
    # switch went off would be handed to a task agent once NekoClaw is on again.
    assert stage.state.agent_flags["browser_use_enabled"] is False
    assert len(said) == 1 and "the messages kept for task agents are not dropped" in said[0]


@pytest.mark.parametrize(
    ("statement", "puts_one_off"),
    [
        ('Modules.agent_flags["browser_use_enabled"] = False', True),
        ('_shared.Modules.agent_flags["computer_use_enabled"] = not ready', True),
        ("Modules.agent_flags[key] = False", True),
        ("Modules.agent_flags[key] = value", True),
        ('Modules.agent_flags["browser_use_enabled"] ^= True', True),
        ('Modules.agent_flags["browser_use_enabled"] &= enabled', True),
        ('del Modules.agent_flags["user_plugin_enabled"]', True),
        # The whole table, in the place of the one there is or changed at once.
        ('Modules.agent_flags = {"computer_use_enabled": False, "browser_use_enabled": False}', True),
        ("Modules.agent_flags = dict(flags)", True),
        ("Modules.agent_flags: dict = {}", True),
        ("Modules.agent_flags.update(browser_use_enabled=False)", True),
        ("Modules.agent_flags.clear()", True),
        ('Modules.agent_flags.pop("user_plugin_enabled", None)', True),
        # As one of a row that is taken apart (whatever each of them gets), and by a loop or a ``with``.
        ('Modules.agent_flags["browser_use_enabled"], reason = False, ""', True),
        ('[Modules.agent_flags["browser_use_enabled"], reason] = False, ""', True),
        ('reason, (Modules.agent_flags[key], ready) = "", (False, False)', True),
        ('first, *Modules.agent_flags["browser_use_enabled"] = values', True),
        ('Modules.agent_flags["browser_use_enabled"], reason = True, ""', True),
        ("Modules.agent_flags, reason = {}, None", True),
        ('for Modules.agent_flags["browser_use_enabled"] in (False,): pass', True),
        ('with opened() as Modules.agent_flags["browser_use_enabled"]: pass', True),
        ('seen = [None for Modules.agent_flags["browser_use_enabled"] in (False,)]', True),
        ('Modules.agent_flags["something_else"], reason = False, ""', False),
        ('on, reason = Modules.agent_flags["browser_use_enabled"], ""', False),
        ('on = other = Modules.agent_flags["browser_use_enabled"] = True', False),
        # In a lambda: part of the function it stands in.
        ('(lambda: [None for Modules.agent_flags["browser_use_enabled"] in (False,)])()', True),
        ('off = lambda: Modules.agent_flags.update(browser_use_enabled=False)', True),
        ('on = lambda: Modules.agent_flags.get("browser_use_enabled")', False),
        # A type said for it, and nothing set.
        ('Modules.agent_flags["browser_use_enabled"]: bool', False),
        ("Modules.agent_flags: dict", False),
        # Put on, read, or another switch than the three.
        ('Modules.agent_flags["browser_use_enabled"] = True', False),
        ('Modules.agent_flags["browser_use_enabled"]: bool = True', False),
        ('Modules.agent_flags["browser_use_enabled"]: bool = False', True),
        ('on = Modules.agent_flags.get("browser_use_enabled", False)', False),
        ("old_flags = dict(Modules.agent_flags)", False),
        ('Modules.agent_flags["something_else"] = False', False),
        ("Modules.analyzer_enabled = False", False),
    ],
)
def test_what_puts_a_capability_switch_off_is_told_from_what_does_not(statement: str, puts_one_off: bool) -> None:
    function = ast.parse(f"def changes():\n    {statement}\n").body[0]
    assert any(_puts_a_capability_switch_off(node) for node in _own_nodes(function)) is puts_one_off


def test_what_a_module_does_as_it_is_loaded_and_what_stands_in_a_class_is_looked_at_as_well() -> None:
    tree = ast.parse(
        'Modules.agent_flags["browser_use_enabled"] = False\n'
        "def reads():\n"
        '    return Modules.agent_flags.get("browser_use_enabled")\n'
        "class Kept:\n"
        '    off = lambda: Modules.agent_flags.pop("user_plugin_enabled", None)\n'
        "    def puts_one_off(self):\n"
        '        del Modules.agent_flags["computer_use_enabled"]\n'
    )
    found = [
        (scope, sum(_puts_a_capability_switch_off(node) for node in _own_nodes(function)))
        for scope, function in _scopes(tree)
    ]
    # What stands in the module and in the class (a lambda among it) is the module's; each
    # function is looked at by itself, and not once more as part of what it stands in.
    assert sorted(found) == [("<module>", 2), ("puts_one_off", 1), ("reads", 0)]


def test_every_place_that_puts_the_master_switch_off_is_looked_at_by_itself() -> None:
    function = ast.parse(
        "async def control(action):\n"
        "    if action == 'off':\n"
        "        Modules.analyzer_enabled = False\n"
        "        Modules.analyzer_profile = {}\n"
        "        forget_turns_for_task_agents()\n"
        "        await stop_everything()\n"
        "    elif action == 'off_at_once':\n"
        "        Modules.analyzer_enabled = False\n"
        "        return {}\n"
        "    elif action == 'off_and_wait':\n"
        "        Modules.analyzer_enabled = False\n"
        "        await stop_everything()\n"
        "        forget_turns_for_task_agents()\n"
        "    elif action == 'off_under_a_lock':\n"
        "        Modules.analyzer_enabled = False\n"
        "        async with lock:\n"
        "            pass\n"
        "        forget_turns_for_task_agents()\n"
        "    elif action == 'off_and_read':\n"
        "        Modules.analyzer_enabled = False\n"
        "        rows = [row async for row in source()]\n"
        "        forget_turns_for_task_agents()\n"
        "    elif action == 'off_with_something_for_later':\n"
        "        Modules.analyzer_enabled = False\n"
        "        async def later():\n"
        "            await stop_everything()\n"
        "            return {}\n"
        "        forget_turns_for_task_agents()\n"
        "    else:\n"
        "        Modules.analyzer_enabled = True\n"
    ).body[0]
    # The first drops what was kept right after it. The second leaves without, and the third,
    # the fourth and the fifth wait for something first: none of them is covered by the first,
    # nor by what comes later. The last only defines something that would wait when it is
    # called, which is not waiting.
    assert _master_switch_offs(function) == [True, False, False, False, False, True]


def test_a_capability_switch_is_put_off_in_one_place_which_looks_whether_nekoclaw_is_off_now() -> None:
    from pathlib import Path

    capability: list[str] = []
    master: list[tuple[str, list[bool]]] = []
    for path in sorted(Path(api_routes.__file__).parent.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for scope, function in _scopes(tree):
            nodes = _own_nodes(function)
            name = f"{path.name}:{scope}"
            if any(_puts_a_capability_switch_off(node) for node in nodes):
                capability.append(name)
                # In one go: the switch, and what was kept, with nothing waited for in between.
                assert isinstance(function, ast.FunctionDef), name
                assert not any(isinstance(node, (ast.Await, ast.Yield, ast.YieldFrom)) for node in nodes), name
                assert "forget_turns_if_nekoclaw_is_off" in {_called(node) for node in nodes}, name
            if any(_puts_the_master_switch_off(node) for node in nodes):
                # Each place by itself: one that drops what was kept does not stand for another,
                # in the same function or in one of the same name elsewhere.
                master.append((name, _master_switch_offs(function)))

    # With the master switch off, or the last capability switch, NekoClaw is off, and what was kept
    # of the main dialogue for task agents goes with the switch. A capability switch goes off by
    # the user's hand and by itself, in a dozen places: all of them by the one function that
    # drops what was kept in the same breath.
    assert capability == ["_shared.py:_put_switch_off"]
    assert sorted(master) == [("api_routes.py:admin_control", [True]), ("api_routes.py:agent_command", [True])]
