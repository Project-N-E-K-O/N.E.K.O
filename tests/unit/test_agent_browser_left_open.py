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
    assert world.browser.fetch_enabled == [(None, OWN_PATTERNS)]


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


async def test_a_browser_whose_connection_is_lost_is_closed_when_every_task_is_stopped(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    world.browser.disconnect()

    await api_routes.admin_control(dict(END_ALL))

    # Left open it would not be kept from this application's ports: closed, as it always was.
    assert len(world.closed) == 1 and not stage.session.holds_a_browser


# ---------------------------------------------------------------------------
# The user said the browser is not wanted, and then stopped every task
# ---------------------------------------------------------------------------


async def test_switching_the_browser_tools_off_closes_the_browser(stage: Any, world: BrowserWorld) -> None:
    await _on_the_order_form(stage)

    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    await stage.settled()

    assert len(world.closed) == 1 and world.closed[0][0].owned is True
    assert not stage.session.connected and not stage.session.holds_a_browser


async def test_stopping_every_task_right_after_the_browser_tools_were_switched_off_still_closes_the_browser(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)

    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    # The task that closes it has not had a turn yet, and is stopped with everything else in the background.
    (closing,) = stage.state._background_tasks
    assert world.closed == []
    await api_routes.admin_control(dict(END_ALL))

    assert closing.cancelled()
    assert len(world.closed) == 1 and not stage.session.holds_a_browser
    assert stage.killed == []


async def test_stopping_every_task_while_the_switched_off_browser_is_being_closed_closes_it_again(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    world.close_gate = asyncio.Event()

    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    (closing,) = stage.state._background_tasks
    for _ in range(400):
        if world.closed:
            break
        await asyncio.sleep(0.005)
    assert len(world.closed) == 1
    # Half way through: the browser is still driven and connected to, as one that is merely not in use.
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


async def test_saving_the_browser_settings_closes_the_browser(stage: Any, world: BrowserWorld) -> None:
    await _on_the_order_form(stage)

    await api_runtime.set_browser_settings({"headless": True})
    await stage.settled()

    assert len(world.closed) == 1 and not stage.session.holds_a_browser


@pytest.mark.parametrize("how", ["switched off", "switched off with the other switch", "settings saved"])
async def test_the_browser_is_not_one_to_leave_open_from_the_moment_the_user_said_so(
    stage: Any, world: BrowserWorld, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    # What the routes leave to the background is kept from ever having a turn: the user stopping every
    # task at that very moment stops it before it has begun.
    kept: list[Any] = []
    monkeypatch.setattr(api_routes, "_create_tracked_task", kept.append)
    monkeypatch.setattr(api_runtime, "_create_tracked_task", kept.append)
    await _on_the_order_form(stage)
    assert stage.provider.browser_window_stays() is True

    if how == "switched off":
        await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    elif how == "switched off with the other switch":
        # The agent model is no longer configured, and the user asks for the keyboard and mouse.
        monkeypatch.setattr(
            api_routes,
            "_check_agent_api_gate",
            lambda: {"ready": False, "reasons": ["AGENT_ENDPOINT_NOT_CONFIGURED"], "is_free_version": False},
        )
        await api_routes.set_agent_flags({"computer_use_enabled": True, "_persist_intent": False})
    else:
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


@pytest.mark.parametrize("how", ["admin_control", "agent_command"])
async def test_switching_the_master_switch_off_closes_the_browser(stage: Any, world: BrowserWorld, how: str) -> None:
    await _on_the_order_form(stage)

    if how == "admin_control":
        await api_routes.admin_control({"action": "disable_analyzer"})
    else:
        await api_routes.agent_command({"command": "set_agent_enabled", "enabled": False, "_persist_intent": False})

    # It runs through the stopping of every task, with the switch of the browser tools left on.
    assert stage.state.agent_flags["browser_use_enabled"] is True
    assert len(world.closed) == 1 and not stage.session.holds_a_browser
    assert stage.killed == []


async def test_a_browser_that_is_driven_again_after_it_was_closed_is_left_open_like_any_other(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    await stage.settled()
    assert len(world.closed) == 1
    await api_routes.set_agent_flags({"browser_use_enabled": True, "_persist_intent": False})
    await stage.settled()

    await _on_the_order_form(stage)
    await api_routes.admin_control(dict(END_ALL))

    # That it was not wanted once says nothing about the one that was started since.
    assert len(world.launched) == 2 and len(world.closed) == 1 and stage.session.connected


async def test_a_browser_that_could_not_be_closed_when_switched_off_is_killed_when_every_task_is_stopped(
    stage: Any, world: BrowserWorld
) -> None:
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

    # As before: one the user wanted closed does not run on because closing it failed.
    assert stage.killed == [1]


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


async def test_the_window_left_open_for_a_model_that_stopped_answering_is_closed_by_the_master_switch(
    stage: Any, world: BrowserWorld
) -> None:
    await _on_the_order_form(stage)
    stage.state.model_probe = Probe((False, "AGENT_LLM_UNREACHABLE"))
    await capabilities._fire_agent_llm_connectivity_check()

    await api_routes.admin_control({"action": "disable_analyzer"})

    assert len(world.closed) == 1 and not stage.session.holds_a_browser


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
