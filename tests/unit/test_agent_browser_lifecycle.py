"""The browser switch of the agent process, from enabling it to shutting down.

No browser comes with the application: the browser tools drive one the user
has, started when a task agent first calls a browser tool. The agent process
only says whether the tools can be used (a browser is found, the agent model
answers) and closes the browser again when the switch goes off, when the
model stops answering and when the process shuts down.

Nothing here looks for, starts or closes a real browser: the search
(``find_browsers``) and the provider's ``close_browser`` are fakes, and the
browser settings are read from a directory of the test's own.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

capabilities = importlib.import_module("app.agent_server.capabilities")
api_routes = importlib.import_module("app.agent_server.api_routes")
api_runtime = importlib.import_module("app.agent_server.api_runtime")
nekoclaw_builtins = importlib.import_module("app.agent_server.nekoclaw_builtins")


pytestmark = pytest.mark.unit

GATE_READY = {"ready": True, "reasons": [], "is_free_version": False}
GATE_NOT_CONFIGURED = {"ready": False, "reasons": ["AGENT_ENDPOINT_NOT_CONFIGURED"], "is_free_version": False}


class Probe:
    """Stands in for ``AgentModelProbe``: answers the probe as told."""

    def __init__(self, *, init_ok: bool = True, last_error: str | None = None, result: Any = (True, "")) -> None:
        self.init_ok = init_ok
        self.last_error = last_error
        self.result = result
        self.probes = 0

    def check_connectivity(self, **_options: Any) -> Any:
        self.probes += 1
        if isinstance(self.result, BaseException):
            raise self.result
        # As the real probe does: what it found stays written on it, whatever
        # the caller then makes of the answer.
        passed = bool(self.result[0] if isinstance(self.result, tuple) else self.result)
        self.init_ok = passed
        self.last_error = None if passed else "the model did not answer"
        return self.result


class Browser:
    """Stands in for the browser the tools started: counts how often it is closed."""

    def __init__(self) -> None:
        self.closes = 0
        self.close_started = asyncio.Event()
        # Set to make closing take until the test lets it finish.
        self.hold: asyncio.Event | None = None
        self.fails = False

    async def close(self) -> None:
        self.closes += 1
        self.close_started.set()
        if self.hold is not None:
            await self.hold.wait()
        if self.fails:
            raise RuntimeError("the browser does not answer")


class Search:
    """Stands in for the search for an installed browser."""

    def __init__(self) -> None:
        self.found: Any = [SimpleNamespace(kind="chrome", name="Chrome", executable="/opt/chrome")]
        self.searches = 0

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        self.searches += 1
        if isinstance(self.found, BaseException):
            raise self.found
        return self.found


@pytest.fixture
def modules(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The process's switches and caches, put back as they were after the test."""
    state = capabilities._shared.Modules
    monkeypatch.setattr(state, "analyzer_enabled", True)
    monkeypatch.setattr(
        state,
        "agent_flags",
        {"computer_use_enabled": False, "browser_use_enabled": False, "user_plugin_enabled": False},
    )
    monkeypatch.setattr(
        state,
        "capability_cache",
        {
            "computer_use": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
            "browser_use": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
            "user_plugin": {"ready": True, "reason": ""},
        },
    )
    monkeypatch.setattr(state, "notification", None)
    monkeypatch.setattr(state, "model_probe", Probe())
    monkeypatch.setattr(state, "browser_use_lifecycle_seq", state.browser_use_lifecycle_seq)
    monkeypatch.setattr(state, "state_revision", state.state_revision)
    # A lock of this test's own event loop.
    monkeypatch.setattr(capabilities, "_llm_check_lock", asyncio.Lock())
    monkeypatch.setattr(nekoclaw_builtins, "_in_use", {})

    async def ignore_status_update(*_args: Any, **_kwargs: Any) -> None:
        return None

    # Sending the status would also take the notification the test wants to read.
    monkeypatch.setattr(api_routes, "_emit_agent_status_update", ignore_status_update)
    monkeypatch.setattr(capabilities, "_emit_agent_status_update", ignore_status_update)
    monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: dict(GATE_READY))
    return state


@pytest.fixture
def search(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Search:
    fake = Search()
    monkeypatch.setattr("nekoclaw.builtin.browser.launcher.find_browsers", fake)
    # No browser is named in the settings unless the test names one.
    fake.settings_root = tmp_path / "settings" / "nekoclaw"
    monkeypatch.setattr("nekoclaw.builtin.browser.settings._root", lambda: fake.settings_root)
    monkeypatch.setattr(capabilities, "_browser_search", None)
    return fake


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch) -> Browser:
    fake = Browser()
    monkeypatch.setattr(nekoclaw_builtins.provider, "close_browser", fake.close)
    # No browser tools are built by anything here; a test can tell if they were.
    monkeypatch.setattr(nekoclaw_builtins.provider, "_browser", None)
    return fake


@pytest.fixture
def checks(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The connectivity checks the routes start in the background, recorded instead of run."""
    started: list[dict[str, Any]] = []

    async def record(**options: Any) -> None:
        started.append(options)

    monkeypatch.setattr(api_routes, "_fire_agent_llm_connectivity_check", record)
    return started


@pytest.fixture
def scheduled(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """What a flag request leaves to be done after it has answered, kept unstarted."""
    kept: list[Any] = []

    def capture(coro: Any) -> None:
        kept.append(coro)

    monkeypatch.setattr(api_routes, "_create_tracked_task", capture)
    yield kept
    for coro in kept:
        coro.close()


def _notification(modules: Any) -> dict[str, Any] | None:
    return json.loads(modules.notification) if modules.notification else None


# ---------------------------------------------------------------------------
# Is there a browser on this computer
# ---------------------------------------------------------------------------


def test_a_browser_that_is_found_is_all_the_browser_tools_need(search):
    assert capabilities._browser_dependency_status() == (True, "")


def test_no_browser_on_this_computer_is_said_with_its_own_reason(search):
    search.found = []

    assert capabilities._browser_dependency_status() == (False, "AGENT_BROWSER_NOT_FOUND")


def test_a_search_that_fails_counts_as_no_browser(search):
    search.found = OSError("the registry cannot be read")

    assert capabilities._browser_dependency_status() == (False, "AGENT_BROWSER_NOT_FOUND")


def test_the_answer_of_the_browser_search_is_kept_for_a_moment(search, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(capabilities, "time", SimpleNamespace(monotonic=lambda: now[0]))
    search.found = []

    assert capabilities._browser_dependency_status() == (False, "AGENT_BROWSER_NOT_FOUND")
    # The user installs a browser. The status paths ask again and again;
    # within the moment the answer is kept, the files are not checked again.
    search.found = [SimpleNamespace(kind="edge", name="Edge", executable="/opt/edge")]
    now[0] += capabilities._BROWSER_SEARCH_TTL_SECONDS - 1
    assert capabilities._browser_dependency_status() == (False, "AGENT_BROWSER_NOT_FOUND")
    assert search.searches == 1

    now[0] += 2
    assert capabilities._browser_dependency_status() == (True, "")
    assert search.searches == 2


def _name_in_the_settings(search: Search, executable: Any) -> None:
    search.settings_root.mkdir(parents=True, exist_ok=True)
    (search.settings_root / "browser.json").write_text(
        json.dumps({"mode": "own_profile", "executable": str(executable), "headless": False}),
        encoding="utf-8",
    )


def test_a_browser_named_in_the_settings_counts_although_the_search_finds_none(search, tmp_path):
    # A portable browser, or one installed where the search does not look:
    # the tools would start it, so the switch must not say there is none.
    portable = tmp_path / "portable" / "chrome.exe"
    portable.parent.mkdir()
    portable.write_bytes(b"")
    _name_in_the_settings(search, portable)
    search.found = []

    assert capabilities._browser_dependency_status() == (True, "")


def test_a_browser_named_in_the_settings_that_is_gone_does_not_count(search, tmp_path):
    _name_in_the_settings(search, tmp_path / "portable" / "chrome.exe")
    search.found = []

    assert capabilities._browser_dependency_status() == (False, "AGENT_BROWSER_NOT_FOUND")


async def test_changing_the_browser_settings_looks_for_a_browser_afresh(search, tmp_path, monkeypatch):
    closes: list[dict[str, Any]] = []

    async def close_browser(**options: Any) -> None:
        closes.append(options)

    monkeypatch.setattr(api_runtime, "_close_browser", close_browser)
    monkeypatch.setattr(capabilities._shared.Modules, "_background_tasks", set())
    portable = tmp_path / "portable" / "chrome.exe"
    portable.parent.mkdir()
    portable.write_bytes(b"")
    search.found = []
    assert capabilities._browser_dependency_status() == (False, "AGENT_BROWSER_NOT_FOUND")

    # The answer above would be kept for a while. Naming a browser in the
    # settings must show at once, or the switch stays refused until it lapses.
    await api_runtime.set_browser_settings({"executable": str(portable)})
    await asyncio.gather(*list(capabilities._shared.Modules._background_tasks))

    assert capabilities._browser_dependency_status() == (True, "")
    assert closes == [{"update_capability": False}]


# ---------------------------------------------------------------------------
# GET /browser_use/availability
# ---------------------------------------------------------------------------


async def test_availability_with_the_agent_model_not_configured_does_not_look_for_a_browser(
    modules, search, browser, monkeypatch
):
    monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: dict(GATE_NOT_CONFIGURED))

    status = await api_routes.browser_use_availability()

    assert status == {"ready": False, "reasons": ["AGENT_ENDPOINT_NOT_CONFIGURED"]}
    assert search.searches == 0


async def test_availability_without_a_browser_names_the_missing_browser(modules, search, browser, checks):
    search.found = []

    status = await api_routes.browser_use_availability()

    assert status == {"enabled": True, "ready": False, "reasons": ["AGENT_BROWSER_NOT_FOUND"], "provider": "nekoclaw"}
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_BROWSER_NOT_FOUND"}
    # The model is not what is missing: it is not probed for this.
    assert checks == []


async def test_availability_with_a_model_that_did_not_answer_says_so_and_probes_it_again(
    modules, search, browser, checks
):
    modules.model_probe = Probe(init_ok=False, last_error="Read timeout")

    status = await api_routes.browser_use_availability()
    await asyncio.sleep(0)

    assert status == {"enabled": True, "ready": False, "reasons": ["Read timeout"], "provider": "nekoclaw"}
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_LLM_UNREACHABLE"}
    assert checks == [{}]


async def test_availability_without_a_probe_is_not_ready(modules, search, browser, checks):
    modules.model_probe = None

    status = await api_routes.browser_use_availability()
    await asyncio.sleep(0)

    assert (status["ready"], status["reasons"]) == (False, ["Agent LLM not connected"])
    assert checks == []


async def test_availability_is_ready_with_a_browser_and_a_model_that_answers_and_starts_no_browser(
    modules, search, browser, checks
):
    status = await api_routes.browser_use_availability()
    await asyncio.sleep(0)

    assert status == {"enabled": True, "ready": True, "reasons": [], "provider": "nekoclaw"}
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}
    # Asked on every poll of the settings page: nothing is built, started or probed.
    assert nekoclaw_builtins.provider._browser is None
    assert checks == [] and browser.closes == 0


# ---------------------------------------------------------------------------
# Switching the browser tools on
# ---------------------------------------------------------------------------


async def test_enabling_without_a_browser_is_refused_with_the_reason(modules, search, browser, checks, scheduled):
    search.found = []

    result = await api_routes.set_agent_flags({"browser_use_enabled": True, "_persist_intent": False})

    assert result["success"] is True
    assert result["agent_flags"]["browser_use_enabled"] is False
    assert _notification(modules) == {
        "code": "AGENT_BU_UNAVAILABLE",
        "details": {"reason_code": "AGENT_BROWSER_NOT_FOUND"},
    }
    # The status display reads the reason from here; it was still "pending".
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_BROWSER_NOT_FOUND"}
    assert checks == [] and scheduled == []


async def test_enabling_with_a_browser_and_a_model_that_answers_starts_no_browser(
    modules, search, browser, checks, scheduled
):
    result = await api_routes.set_agent_flags({"browser_use_enabled": True, "_persist_intent": False})

    assert result["agent_flags"]["browser_use_enabled"] is True
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}
    assert modules.notification is None
    # The browser is started by the first browser tool call, not by the switch.
    assert nekoclaw_builtins.provider._browser is None
    assert checks == [] and scheduled == [] and browser.closes == 0


async def test_enabling_before_the_model_has_answered_switches_on_and_probes_it(
    modules, search, browser, checks, scheduled
):
    modules.model_probe = Probe(init_ok=False)

    result = await api_routes.set_agent_flags({"browser_use_enabled": True, "_persist_intent": False})
    await asyncio.sleep(0)

    assert result["agent_flags"]["browser_use_enabled"] is True
    assert _notification(modules) == {"code": "AGENT_BU_ENABLED_CHECKING"}
    # Not ready until the probe has passed.
    assert modules.capability_cache["browser_use"]["ready"] is False
    assert checks == [{}]


async def test_enabling_with_the_agent_model_not_configured_is_refused(
    modules, search, browser, checks, scheduled, monkeypatch
):
    monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: dict(GATE_NOT_CONFIGURED))

    result = await api_routes.set_agent_flags({"browser_use_enabled": True, "_persist_intent": False})

    assert result["agent_flags"]["browser_use_enabled"] is False
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_ENDPOINT_NOT_CONFIGURED"}
    assert search.searches == 0 and scheduled == []


# ---------------------------------------------------------------------------
# Switching them off closes the browser
# ---------------------------------------------------------------------------


async def test_disabling_schedules_the_close_and_answers_without_waiting_for_it(modules, search, browser, scheduled):
    modules.agent_flags["browser_use_enabled"] = True

    result = await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})

    assert result["success"] is True
    assert modules.agent_flags["browser_use_enabled"] is False
    assert browser.closes == 0
    assert len(scheduled) == 1

    assert await scheduled.pop() is None
    assert browser.closes == 1
    # Closing is not what makes the tools unusable: a browser is still there.
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}


async def test_a_gate_that_fails_switches_the_browser_off_and_closes_it(
    modules, search, browser, scheduled, monkeypatch
):
    modules.agent_flags["browser_use_enabled"] = True
    monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: dict(GATE_NOT_CONFIGURED))

    # The request is about the other switch; the browser one goes off with it.
    await api_routes.set_agent_flags({"computer_use_enabled": True, "_persist_intent": False})

    assert modules.agent_flags == {
        "computer_use_enabled": False,
        "browser_use_enabled": False,
        "user_plugin_enabled": False,
    }
    assert len(scheduled) == 1
    await scheduled.pop()
    assert browser.closes == 1
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_ENDPOINT_NOT_CONFIGURED"}


async def test_closing_settles_the_capability_from_the_browser_search_or_the_reason_given(modules, search, browser):
    await capabilities._close_browser()
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}

    await capabilities._close_browser(capability_reason="AGENT_LLM_UNREACHABLE")
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_LLM_UNREACHABLE"}

    # Closed for another reason than the switch (everything was stopped, the
    # settings changed): what the switch shows is left alone.
    await capabilities._close_browser(update_capability=False)
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_LLM_UNREACHABLE"}
    assert browser.closes == 3


async def test_closing_without_a_browser_on_this_computer_says_that(modules, search, browser):
    search.found = []

    await capabilities._close_browser()

    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_BROWSER_NOT_FOUND"}


async def test_a_close_that_fails_still_settles_the_capability(modules, search, browser):
    browser.fails = True

    await capabilities._close_browser(capability_reason="AGENT_LLM_UNREACHABLE")

    assert browser.closes == 1
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_LLM_UNREACHABLE"}


async def test_a_close_of_an_older_switch_setting_leaves_the_capability_alone(modules, search, browser):
    modules.capability_cache["browser_use"] = {"ready": True, "reason": ""}
    modules.browser_use_lifecycle_seq = 7

    # Scheduled when the switch was set for the sixth time; it has been set again since.
    await capabilities._close_browser(capability_reason="AGENT_ENDPOINT_NOT_CONFIGURED", expected_lifecycle_seq=6)
    assert browser.closes == 1
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}

    await capabilities._close_browser(capability_reason="AGENT_ENDPOINT_NOT_CONFIGURED", expected_lifecycle_seq=7)
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_ENDPOINT_NOT_CONFIGURED"}


async def test_a_late_close_does_not_overwrite_a_newer_enable(modules, search, browser, monkeypatch):
    tasks: list[asyncio.Task] = []

    def start(coro: Any) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr(api_routes, "_create_tracked_task", start)
    modules.agent_flags["browser_use_enabled"] = True
    browser.hold = asyncio.Event()
    try:
        # The agent model is found not to be configured: the switch goes off
        # and the browser starts to close, which takes a while. The request
        # itself does not wait for that.
        monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: dict(GATE_NOT_CONFIGURED))
        await asyncio.wait_for(
            api_routes.set_agent_flags({"browser_use_enabled": True, "_persist_intent": False}), 2.0
        )
        await asyncio.wait_for(browser.close_started.wait(), 2.0)
        assert modules.agent_flags["browser_use_enabled"] is False

        # Meanwhile the user fixes the configuration and switches it on again.
        monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: dict(GATE_READY))
        result = await asyncio.wait_for(
            api_routes.set_agent_flags({"browser_use_enabled": True, "_persist_intent": False}), 2.0
        )
        assert result["agent_flags"]["browser_use_enabled"] is True
        assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}

        browser.hold.set()
        await asyncio.gather(*tasks)

        # The close that finished late belongs to the earlier setting.
        assert browser.closes == 1
        assert modules.agent_flags["browser_use_enabled"] is True
        assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}
    finally:
        browser.hold.set()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_a_cancelled_close_settles_nothing_and_the_browser_can_be_closed_again(modules, search, browser):
    modules.capability_cache["browser_use"] = {"ready": True, "reason": ""}
    browser.hold = asyncio.Event()
    closing = asyncio.ensure_future(capabilities._close_browser(capability_reason="AGENT_LLM_UNREACHABLE"))
    await asyncio.wait_for(browser.close_started.wait(), 2.0)

    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing

    # Whoever cancelled it (stopping everything, shutting down) closes again.
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}
    browser.hold.set()
    await capabilities._close_browser(capability_reason="AGENT_LLM_UNREACHABLE")
    assert browser.closes == 2
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_LLM_UNREACHABLE"}


# ---------------------------------------------------------------------------
# The probe of the agent model decides for both groups of tools
# ---------------------------------------------------------------------------


def _keyboard(monkeypatch: pytest.MonkeyPatch, *, ready: bool, reasons: list[str] | None = None) -> list[int]:
    asked: list[int] = []

    def availability() -> dict[str, Any]:
        asked.append(1)
        return {"enabled": True, "ready": ready, "reasons": list(reasons or []), "provider": "nekoclaw", "model": "m"}

    monkeypatch.setattr(capabilities, "_keyboard_mouse_availability", availability)
    return asked


async def test_a_passed_probe_makes_each_group_as_ready_as_its_own_means(modules, search, browser, monkeypatch):
    asked = _keyboard(monkeypatch, ready=True)

    await capabilities._fire_agent_llm_connectivity_check()

    assert modules.model_probe.probes == 1
    assert modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}
    assert asked == [1] and search.searches == 1
    assert nekoclaw_builtins.provider._browser is None


async def test_a_passed_probe_does_not_make_the_browser_ready_without_a_browser(modules, search, browser, monkeypatch):
    _keyboard(monkeypatch, ready=True)
    search.found = []

    await capabilities._fire_agent_llm_connectivity_check()

    assert modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_BROWSER_NOT_FOUND"}


async def test_a_passed_probe_does_not_make_the_keyboard_ready_when_input_cannot_be_sent(
    modules, search, browser, monkeypatch
):
    _keyboard(monkeypatch, ready=False, reasons=["AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"])

    await capabilities._fire_agent_llm_connectivity_check()

    assert modules.capability_cache["computer_use"] == {
        "ready": False,
        "reason": "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE",
    }
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}


async def test_a_failed_probe_switches_both_off_says_why_and_closes_the_browser(modules, search, browser, monkeypatch):
    asked = _keyboard(monkeypatch, ready=True)
    modules.model_probe = Probe(result=(False, "AGENT_API_KEY_INVALID"))
    modules.agent_flags.update(computer_use_enabled=True, browser_use_enabled=True)

    await capabilities._fire_agent_llm_connectivity_check()

    assert modules.agent_flags["computer_use_enabled"] is False
    assert modules.agent_flags["browser_use_enabled"] is False
    assert modules.capability_cache["computer_use"] == {"ready": False, "reason": "AGENT_API_KEY_INVALID"}
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_API_KEY_INVALID"}
    assert _notification(modules) == {
        "code": "AGENT_AUTO_DISABLED_BROWSER",
        "details": {"reason_code": "AGENT_API_KEY_INVALID"},
    }
    assert browser.closes == 1
    # Neither group's own means is looked at once the model does not answer.
    assert asked == [] and search.searches == 0


async def test_a_failed_probe_with_only_the_keyboard_on_closes_no_browser(modules, search, browser, monkeypatch):
    _keyboard(monkeypatch, ready=True)
    modules.model_probe = Probe(result=(False, "AGENT_LLM_UNREACHABLE"))
    modules.agent_flags["computer_use_enabled"] = True

    await capabilities._fire_agent_llm_connectivity_check()

    assert modules.agent_flags["computer_use_enabled"] is False
    assert _notification(modules) == {
        "code": "AGENT_AUTO_DISABLED_COMPUTER",
        "details": {"reason_code": "AGENT_LLM_UNREACHABLE"},
    }
    assert browser.closes == 0


@pytest.mark.parametrize("group", ["keyboard_mouse", "browser"])
@pytest.mark.parametrize("result", [(False, "AGENT_LLM_UNREACHABLE"), RuntimeError("the probe itself broke")])
async def test_a_failed_probe_while_a_task_is_using_the_tools_switches_nothing_off(
    modules, search, browser, monkeypatch, group, result
):
    _keyboard(monkeypatch, ready=True)
    modules.model_probe = Probe(result=result)
    modules.agent_flags.update(computer_use_enabled=True, browser_use_enabled=True)
    ready = {"ready": True, "reason": ""}
    modules.capability_cache.update(computer_use=dict(ready), browser_use=dict(ready))
    # A commission occupies one of the groups: the model is demonstrably
    # answering it, so the probe lost a race.
    nekoclaw_builtins._in_use[group] = "c1"
    assert nekoclaw_builtins.group_in_use(group) is True

    await capabilities._fire_agent_llm_connectivity_check()

    assert modules.model_probe.probes == 1
    assert modules.agent_flags["computer_use_enabled"] is True
    assert modules.agent_flags["browser_use_enabled"] is True
    assert modules.capability_cache["computer_use"] == ready
    assert modules.capability_cache["browser_use"] == ready
    assert modules.notification is None
    assert browser.closes == 0


async def test_a_probe_that_breaks_with_nothing_under_way_switches_both_off(modules, search, browser, monkeypatch):
    _keyboard(monkeypatch, ready=True)
    modules.model_probe = Probe(result=RuntimeError("the probe itself broke"))
    modules.agent_flags.update(computer_use_enabled=True, browser_use_enabled=True)

    await capabilities._fire_agent_llm_connectivity_check()

    assert modules.agent_flags["computer_use_enabled"] is False
    assert modules.agent_flags["browser_use_enabled"] is False
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_LLM_UNREACHABLE"}
    assert _notification(modules) == {"code": "AGENT_LLM_CHECK_ERROR"}
    assert browser.closes == 1


async def test_without_a_probe_neither_group_is_ready(modules, search, browser):
    modules.model_probe = None

    await capabilities._fire_agent_llm_connectivity_check()

    assert modules.capability_cache["computer_use"] == {"ready": False, "reason": "AGENT_CU_MODULE_NOT_LOADED"}
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_CU_MODULE_NOT_LOADED"}


async def test_a_check_asked_for_while_one_is_under_way_is_dropped_unless_it_is_queued(
    modules, search, browser, monkeypatch
):
    _keyboard(monkeypatch, ready=True)
    await capabilities._llm_check_lock.acquire()
    try:
        # A toggle that asks again while a probe is in flight adds nothing.
        await asyncio.wait_for(capabilities._fire_agent_llm_connectivity_check(), 2.0)
        assert modules.model_probe.probes == 0

        # A change that has to show (the master switch went on) waits its turn.
        queued = asyncio.ensure_future(capabilities._fire_agent_llm_connectivity_check(queue=True))
        await asyncio.sleep(0)
        assert not queued.done() and modules.model_probe.probes == 0
    finally:
        capabilities._llm_check_lock.release()
    await asyncio.wait_for(queued, 2.0)

    assert modules.model_probe.probes == 1
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}


# ---------------------------------------------------------------------------
# The master switch and a saved configuration
# ---------------------------------------------------------------------------


async def test_switching_the_master_switch_on_marks_both_pending_and_queues_a_probe(
    modules, search, browser, checks, monkeypatch
):
    refreshed: list[bool] = []
    monkeypatch.setattr(api_routes, "_try_refresh_model_probe", lambda force=False: refreshed.append(force) or True)
    ready = {"ready": True, "reason": ""}
    modules.capability_cache.update(computer_use=dict(ready), browser_use=dict(ready))
    modules.analyzer_enabled = False

    result = await api_routes.agent_command({"command": "set_agent_enabled", "enabled": True, "_persist_intent": False})
    await asyncio.sleep(0)

    assert result["success"] is True and modules.analyzer_enabled is True
    # The probe is rebuilt from the configuration as it is now, and until it
    # has answered neither group counts as ready.
    assert refreshed == [True]
    pending = {"ready": False, "reason": "AGENT_PRECHECK_PENDING"}
    assert modules.capability_cache["computer_use"] == pending
    assert modules.capability_cache["browser_use"] == pending
    assert checks == [{"queue": True}]


async def test_switching_the_master_switch_on_without_a_configured_model_probes_nothing(
    modules, search, browser, checks, monkeypatch
):
    monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: dict(GATE_NOT_CONFIGURED))
    modules.analyzer_enabled = False

    await api_routes.agent_command({"command": "set_agent_enabled", "enabled": True, "_persist_intent": False})
    await asyncio.sleep(0)

    not_configured = {"ready": False, "reason": "AGENT_ENDPOINT_NOT_CONFIGURED"}
    assert modules.capability_cache["computer_use"] == not_configured
    assert modules.capability_cache["browser_use"] == not_configured
    assert checks == []


@pytest.mark.parametrize(
    ("master", "flags", "probed"),
    [
        (True, {"browser_use_enabled": True}, True),
        (True, {"computer_use_enabled": True}, True),
        # Nobody is using the model: saving a setting must not probe it.
        (True, {"user_plugin_enabled": True}, False),
        # The flags keep the user's choice while the master switch is off.
        (False, {"browser_use_enabled": True, "computer_use_enabled": True}, False),
    ],
)
async def test_a_saved_configuration_rebuilds_the_probe_and_probes_only_when_the_tools_are_on(
    modules, checks, monkeypatch, master, flags, probed
):
    refreshed: list[bool] = []
    monkeypatch.setattr(api_routes, "_try_refresh_model_probe", lambda force=False: refreshed.append(force) or True)
    modules.analyzer_enabled = master
    modules.agent_flags.update(flags)

    result = await api_routes.notify_config_changed()
    await asyncio.sleep(0)

    assert result["success"] is True
    assert refreshed == [True]
    assert checks == ([{}] if probed else [])


# ---------------------------------------------------------------------------
# Starting and shutting down
# ---------------------------------------------------------------------------


class _Tracker:
    """Stands in for the token tracker: startup and shutdown must not write usage files."""

    @classmethod
    def get_instance(cls) -> "_Tracker":
        return cls()

    def start_periodic_save(self) -> None:
        pass

    def record_app_start(self, process: str = "") -> None:
        pass

    def save(self) -> None:
        pass


@pytest.fixture
def process(modules, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The parts of the agent process that startup and shutdown reach, each replaced by a record."""
    calls: list[str] = []

    def record(name: str) -> Any:
        async def step(*_args: Any, **_kwargs: Any) -> None:
            calls.append(name)

        return step

    class Bridge:
        def __init__(self, on_session_event: Any = None) -> None:
            pass

        async def start(self) -> None:
            calls.append("bridge")

    monkeypatch.setattr("utils.token_tracker.TokenTracker", _Tracker)
    monkeypatch.setattr("utils.token_tracker.install_hooks", lambda: None)
    monkeypatch.setattr("utils.event_loop_watchdog.start_loop_stall_watchdog_from_env", lambda *args, **kwargs: None)
    monkeypatch.setattr(api_runtime, "_loop_stall_watchdog", None)
    monkeypatch.setenv("NEKO_PLUGIN_HOSTED_BY_AGENT", "false")
    monkeypatch.setattr(modules, "_persistent_tasks", set())
    monkeypatch.setattr(modules, "_background_tasks", set())
    monkeypatch.setattr(modules, "agent_bridge", None)
    monkeypatch.setattr(modules, "throttled_logger", None)
    monkeypatch.setattr(api_runtime, "_settle_region", record("region"))
    monkeypatch.setattr(api_runtime, "_start_embedded_user_plugin_server", record("plugin server"))
    monkeypatch.setattr(api_runtime, "_stop_embedded_user_plugin_server", record("plugin server stopped"))
    monkeypatch.setattr(api_runtime, "_ensure_plugin_lifecycle_stopped", record("plugins stopped"))
    monkeypatch.setattr(api_runtime, "_fire_agent_llm_connectivity_check", record("probe"))
    monkeypatch.setattr(api_runtime, "_emit_agent_status_update", record("status"))
    monkeypatch.setattr(api_runtime, "shutdown_nekoclaw_host", record("host"))
    monkeypatch.setattr(api_runtime, "mount_nekoclaw_host", lambda change_token=None: calls.append("mount"))
    monkeypatch.setattr(api_runtime, "AgentServerEventBridge", Bridge)
    monkeypatch.setattr(
        api_runtime,
        "get_config_manager",
        lambda *args, **kwargs: SimpleNamespace(register_quota_exceeded_notifier=lambda notifier: None),
    )
    return calls


async def test_startup_builds_the_probe_and_neither_looks_for_nor_starts_a_browser(
    modules, process, search, browser, monkeypatch
):
    built: list[Any] = []

    class StartupProbe(Probe):
        def __init__(self) -> None:
            super().__init__(init_ok=False)
            built.append(self)

    monkeypatch.setattr("app.agent_server.model_probe.AgentModelProbe", StartupProbe)
    modules.model_probe = None
    ready = {"ready": True, "reason": ""}
    modules.capability_cache.update(computer_use=dict(ready), browser_use=dict(ready))

    await api_runtime.startup()
    await asyncio.gather(*modules._persistent_tasks)

    assert built == [modules.model_probe]
    # Not ready until the probe, which runs in the background, has answered.
    pending = {"ready": False, "reason": "AGENT_PRECHECK_PENDING"}
    assert modules.capability_cache["computer_use"] == pending
    assert modules.capability_cache["browser_use"] == pending
    assert process.count("probe") == 1 and "mount" in process
    # Startup stays free of file checks and processes: the browser is the
    # business of the first browser tool call.
    assert search.searches == 0
    assert nekoclaw_builtins.provider._browser is None and browser.closes == 0


async def test_shutdown_closes_the_browser_and_lets_go_of_held_keys_after_the_host_has_stopped(
    modules, process, search, browser, monkeypatch
):
    order: list[str] = process

    class Desktop:
        def release_held(self) -> bool:
            order.append("keys released")
            return True

    async def close() -> None:
        order.append("browser closed")

    monkeypatch.setattr(nekoclaw_builtins.provider, "close_browser", close)
    monkeypatch.setattr(nekoclaw_builtins.provider, "_desktop", Desktop())
    modules.capability_cache["browser_use"] = {"ready": False, "reason": "AGENT_LLM_UNREACHABLE"}

    await api_runtime.shutdown()

    # The host's shutdown cancels every call under way; only then is the
    # browser those calls were driving closed.
    assert order.index("host") < order.index("browser closed")
    assert "keys released" in order
    assert order[-1] == "status"
    # What the status sent last says about the browser tools is their own
    # means again, not a browser that happens to be closed.
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}


async def test_shutdown_finishes_when_the_browser_will_not_close(modules, process, search, browser, monkeypatch):
    browser.fails = True

    await api_runtime.shutdown()

    assert browser.closes >= 1
    assert process[-1] == "status"


# ---------------------------------------------------------------------------
# Closing: the search for a browser is not done on the event loop
# ---------------------------------------------------------------------------


async def test_after_a_close_the_search_for_a_browser_runs_in_a_worker_thread(modules, search, browser, monkeypatch):
    import threading

    loop_thread = threading.current_thread()
    searched_in: list[threading.Thread] = []
    real = capabilities._browser_dependency_status

    def status() -> tuple[bool, str]:
        searched_in.append(threading.current_thread())
        return real()

    monkeypatch.setattr(capabilities, "_browser_dependency_status", status)
    await capabilities._close_browser()
    # It reads the settings file and looks at files (the registry, on Windows).
    assert len(searched_in) == 1 and searched_in[0] is not loop_thread
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}


async def test_a_switch_set_anew_while_the_search_runs_keeps_its_word(modules, search, browser, monkeypatch):
    import threading

    searching, go_on = threading.Event(), threading.Event()

    def slow_status() -> tuple[bool, str]:
        searching.set()
        assert go_on.wait(5.0)
        return False, "AGENT_BROWSER_NOT_FOUND"

    monkeypatch.setattr(capabilities, "_browser_dependency_status", slow_status)
    seq = modules.browser_use_lifecycle_seq
    closing = asyncio.ensure_future(capabilities._close_browser(expected_lifecycle_seq=seq))
    assert await asyncio.to_thread(searching.wait, 5.0)
    # The user switches the browser on again while the files are looked at.
    modules.browser_use_lifecycle_seq = seq + 1
    capabilities._set_capability("browser_use", True, "")
    go_on.set()
    await asyncio.wait_for(closing, 5.0)
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}


def test_the_ports_the_browser_is_kept_from_are_those_of_every_service_of_this_application(monkeypatch):
    import config
    from nekoclaw.host import mount

    monkeypatch.setattr(mount, "peek_host", lambda: None)
    services = {
        config.MAIN_SERVER_PORT,
        config.MEMORY_SERVER_PORT,
        config.MONITOR_SERVER_PORT,
        config.COMMENTER_SERVER_PORT,
        config.TOOL_SERVER_PORT,
        config.USER_PLUGIN_SERVER_PORT,
    }
    assert nekoclaw_builtins._own_ports() == services
    # The capability gateway picks its port when it starts to listen.
    monkeypatch.setattr(mount, "peek_host", lambda: SimpleNamespace(gateway=SimpleNamespace(port=51234)))
    assert nekoclaw_builtins._own_ports() == services | {51234}
    monkeypatch.setattr(mount, "peek_host", lambda: SimpleNamespace(gateway=SimpleNamespace(port=None)))
    assert nekoclaw_builtins._own_ports() == services
    # They are what the provider of this process hands to the browser it starts.
    assert nekoclaw_builtins.provider._own_ports is nekoclaw_builtins._own_ports


async def test_shutdown_kills_a_browser_that_does_not_close_in_the_time_it_has(
    modules, process, search, browser, monkeypatch
):
    import threading

    import config.nekoclaw_settings as settings

    killed_in: list[threading.Thread] = []
    monkeypatch.setattr(settings, "BROWSER_CLOSE_AT_SHUTDOWN_SECONDS", 0.05)
    monkeypatch.setattr(nekoclaw_builtins.provider, "kill_browser", lambda: killed_in.append(threading.current_thread()))
    # The browser is asked to close and takes its time: longer than the process has left.
    browser.hold = asyncio.Event()

    await asyncio.wait_for(api_runtime.shutdown(), 10.0)

    # Killed, and not on the event loop (killing looks for processes and waits for them).
    assert len(killed_in) == 1 and killed_in[0] is not threading.current_thread()
    assert process[-1] == "status"
    # And not asked to close a second time after that: it would be waited for all over again.
    assert browser.closes == 1
    browser.hold.set()


async def test_shutdown_does_not_kill_a_browser_that_closes_in_time(modules, process, search, browser, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(nekoclaw_builtins.provider, "kill_browser", lambda: killed.append(1))
    import config.nekoclaw_settings as settings

    assert settings.BROWSER_CLOSE_AT_SHUTDOWN_SECONDS == 4.0
    # Within what the launcher gives the process (8 seconds), with time left to kill in.
    assert settings.BROWSER_CLOSE_AT_SHUTDOWN_SECONDS < settings.BROWSER_KILL_AT_SHUTDOWN_SECONDS == 5.0
    monkeypatch.setattr(settings, "BROWSER_KILL_AT_SHUTDOWN_SECONDS", 0.2)

    await api_runtime.shutdown()

    # Asked once, by the step for it; the keys are let go of without asking it again.
    assert browser.closes == 1 and killed == []
    # Nor later: what was set to kill it at the last is called off once it is closed.
    await asyncio.sleep(0.5)
    assert killed == []


async def test_shutdown_kills_the_browser_though_an_earlier_step_takes_all_the_time_there_is(
    modules, process, search, browser, monkeypatch
):
    import threading

    killed_in: list[threading.Thread] = []
    stuck = asyncio.Event()

    async def host_that_will_not_stop() -> None:
        await stuck.wait()

    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "BROWSER_KILL_AT_SHUTDOWN_SECONDS", 0.05)
    monkeypatch.setattr(api_runtime, "shutdown_nekoclaw_host", host_that_will_not_stop)
    monkeypatch.setattr(nekoclaw_builtins.provider, "kill_browser", lambda: killed_in.append(threading.current_thread()))

    shutting_down = asyncio.ensure_future(api_runtime.shutdown())
    try:
        deadline = asyncio.get_running_loop().time() + 5.0
        while not killed_in and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        # The step that closes the browser was never reached (the launcher would end the
        # process about now), and the browser is killed all the same, off the event loop.
        assert browser.closes == 0 and not shutting_down.done()
        assert len(killed_in) == 1 and killed_in[0] is not threading.current_thread()
    finally:
        stuck.set()
    await asyncio.wait_for(shutting_down, 10.0)
    assert process[-1] == "status"
    # With no time left when the shutdown got to the browser, it was not asked to close at all
    # (that would be waited for past the time the launcher gives); it is killed, once more to be sure.
    assert browser.closes == 0 and len(killed_in) == 2


def test_the_last_resort_of_a_shutdown_does_not_fail_when_the_browser_cannot_be_killed(monkeypatch):
    def cannot() -> None:
        raise OSError("access denied")

    monkeypatch.setattr(nekoclaw_builtins.provider, "kill_browser", cannot)
    # On a thread of its own, where nothing would hear of it.
    api_runtime._kill_browser_at_last()


# ---------------------------------------------------------------------------
# A probe that lost a race takes nothing from the task under way, whoever reads it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("group", ["keyboard_mouse", "browser"])
async def test_asking_for_availability_after_a_probe_lost_a_race_takes_nothing_from_the_task_under_way(
    modules, search, browser, checks, monkeypatch, group
):
    modules.model_probe = Probe(result=(False, "AGENT_LLM_RATE_LIMITED"))
    modules.agent_flags.update(computer_use_enabled=True, browser_use_enabled=True)
    ready = {"ready": True, "reason": ""}
    modules.capability_cache.update(computer_use=dict(ready), browser_use=dict(ready))
    nekoclaw_builtins._in_use[group] = "c1"
    await capabilities._fire_agent_llm_connectivity_check()
    # The check let it pass, but what the probe found stays written on the probe.
    assert modules.model_probe.init_ok is False

    def keyboard() -> dict[str, Any]:
        return {"enabled": True, "ready": False, "reasons": ["the model did not answer"], "provider": "nekoclaw", "model": "m"}

    async def images() -> dict[str, Any]:
        return {"sees": True}

    monkeypatch.setattr(api_routes, "_keyboard_mouse_availability", keyboard)
    monkeypatch.setattr(api_routes, "_model_images_status", images)

    keyboard_status = await api_routes.computer_use_availability()
    browser_status = await api_routes.browser_use_availability()
    await asyncio.sleep(0)

    # What was found is answered as it is, and the model is asked again ...
    assert keyboard_status["ready"] is False and browser_status["ready"] is False
    assert len(checks) == 2
    # ... but the task that is being answered by that very model keeps its tools.
    assert modules.agent_flags["computer_use_enabled"] is True
    assert modules.agent_flags["browser_use_enabled"] is True
    assert modules.capability_cache["computer_use"] == ready
    assert modules.capability_cache["browser_use"] == ready
    assert modules.notification is None

    # With the task over, the same answers are taken at their word.
    nekoclaw_builtins._in_use.clear()
    await api_routes.computer_use_availability()
    await api_routes.browser_use_availability()
    assert modules.agent_flags["computer_use_enabled"] is False
    lost = {"ready": False, "reason": "AGENT_LLM_UNREACHABLE"}
    assert modules.capability_cache["computer_use"] == lost
    assert modules.capability_cache["browser_use"] == lost
    assert _notification(modules)["code"] == "AGENT_CU_CAPABILITY_LOST"


async def test_a_model_that_answers_is_not_excused_by_a_task_under_way(modules, search, browser, checks, monkeypatch):
    # The keyboard cannot be used for a reason of its own (no input can be sent), not because of the model.
    modules.model_probe = Probe(init_ok=True)
    modules.agent_flags.update(computer_use_enabled=True)
    modules.capability_cache["computer_use"] = {"ready": True, "reason": ""}
    nekoclaw_builtins._in_use["browser"] = "c1"

    def keyboard() -> dict[str, Any]:
        return {"enabled": True, "ready": False, "reasons": ["AGENT_CU_INPUT_UNAVAILABLE"], "provider": "nekoclaw", "model": "m"}

    async def images() -> dict[str, Any]:
        return {"sees": True}

    monkeypatch.setattr(api_routes, "_keyboard_mouse_availability", keyboard)
    monkeypatch.setattr(api_routes, "_model_images_status", images)

    await api_routes.computer_use_availability()

    assert modules.agent_flags["computer_use_enabled"] is False
    assert modules.capability_cache["computer_use"] == {"ready": False, "reason": "AGENT_CU_INPUT_UNAVAILABLE"}


def test_a_probe_has_lost_a_race_only_when_it_failed_and_a_task_is_using_the_tools(modules):
    modules.model_probe = Probe(init_ok=False)
    assert capabilities._probe_lost_a_race() is False
    nekoclaw_builtins._in_use["keyboard_mouse"] = "c1"
    assert capabilities._probe_lost_a_race() is True
    nekoclaw_builtins._in_use.clear()
    nekoclaw_builtins._in_use["browser"] = "c2"
    assert capabilities._probe_lost_a_race() is True
    modules.model_probe = Probe(init_ok=True)
    assert capabilities._probe_lost_a_race() is False
    modules.model_probe = None
    assert capabilities._probe_lost_a_race() is False


# ---------------------------------------------------------------------------
# Saving the browser settings says at once whether the browser tools can be used
# ---------------------------------------------------------------------------


async def _save_browser_settings(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], *, gate: dict[str, Any] = GATE_READY
) -> list[str]:
    told: list[str] = []
    monkeypatch.setattr(api_runtime, "_check_agent_api_gate", lambda: dict(gate))

    async def close_browser(**options: Any) -> None:
        return None

    async def status_update(*_args: Any, **_kwargs: Any) -> None:
        told.append("status")

    monkeypatch.setattr(api_runtime, "_close_browser", close_browser)
    monkeypatch.setattr(api_runtime, "_emit_agent_status_update", status_update)
    monkeypatch.setattr(capabilities._shared.Modules, "_background_tasks", set())
    await api_runtime.set_browser_settings(payload)
    await asyncio.gather(*list(capabilities._shared.Modules._background_tasks))
    return told


async def test_naming_a_browser_in_the_settings_makes_the_browser_tools_usable_at_once(
    modules, search, tmp_path, monkeypatch
):
    portable = tmp_path / "portable" / "chrome.exe"
    portable.parent.mkdir()
    portable.write_bytes(b"")
    search.found = []
    modules.model_probe = Probe(init_ok=True)
    modules.capability_cache["browser_use"] = {"ready": False, "reason": "AGENT_BROWSER_NOT_FOUND"}
    revision = modules.state_revision

    told = await _save_browser_settings(monkeypatch, {"executable": str(portable)})

    # Left as it was, the tools would stay refused although the switch is on and a browser is named.
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}
    assert told == ["status"] and modules.state_revision > revision

    # Taking the browser away again shows as quickly.
    told = await _save_browser_settings(monkeypatch, {"executable": ""})
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_BROWSER_NOT_FOUND"}
    assert told == ["status"]


@pytest.mark.parametrize("probe", [None, Probe(init_ok=False, last_error="Read timeout")], ids=["no probe", "no answer yet"])
async def test_saving_the_browser_settings_does_not_speak_for_a_model_that_has_not_answered(
    modules, search, tmp_path, monkeypatch, probe
):
    portable = tmp_path / "portable" / "chrome.exe"
    portable.parent.mkdir()
    portable.write_bytes(b"")
    search.found = []
    modules.model_probe = probe
    pending = {"ready": False, "reason": "AGENT_PRECHECK_PENDING"}
    modules.capability_cache["browser_use"] = dict(pending)

    told = await _save_browser_settings(monkeypatch, {"executable": str(portable)})

    # A browser alone does not make the tools usable: the check that follows the model's answer says.
    assert modules.capability_cache["browser_use"] == pending and told == []


# ---------------------------------------------------------------------------
# Shutdown: a browser that answered in time but could not be closed
# ---------------------------------------------------------------------------


async def test_shutdown_kills_a_browser_that_was_asked_in_time_and_could_not_be_closed(
    modules, process, search, browser, monkeypatch
):
    killed: list[int] = []
    monkeypatch.setattr(nekoclaw_builtins.provider, "kill_browser", lambda: killed.append(1))
    # Closing it failed: the session keeps it to be closed the next time, and there is none.
    monkeypatch.setattr(nekoclaw_builtins.provider, "browser_unclosed", lambda: True)

    await asyncio.wait_for(api_runtime.shutdown(), 10.0)

    assert browser.closes == 1 and killed == [1]
    assert process[-1] == "status"


async def test_saving_the_browser_settings_does_not_speak_for_a_model_that_is_no_longer_configured(
    modules, search, tmp_path, monkeypatch
):
    portable = tmp_path / "portable" / "chrome.exe"
    portable.parent.mkdir()
    portable.write_bytes(b"")
    search.found = []
    # The address of the model was taken away; the probe of the one before still says it answered.
    modules.model_probe = Probe(init_ok=True)
    not_configured = {"ready": False, "reason": "AGENT_ENDPOINT_NOT_CONFIGURED"}
    modules.capability_cache["browser_use"] = dict(not_configured)

    told = await _save_browser_settings(monkeypatch, {"executable": str(portable)}, gate=GATE_NOT_CONFIGURED)

    assert modules.capability_cache["browser_use"] == not_configured and told == []
