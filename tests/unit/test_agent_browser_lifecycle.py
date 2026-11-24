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
nekopower_builtins = importlib.import_module("app.agent_server.nekopower_builtins")


pytestmark = pytest.mark.unit

GATE_READY = {"ready": True, "reasons": [], "is_free_version": False}
GATE_NOT_CONFIGURED = {"ready": False, "reasons": ["AGENT_ENDPOINT_NOT_CONFIGURED"], "is_free_version": False}


class Browser:
    """Stands in for the browser the tools started: counts how often it is closed."""

    def __init__(self) -> None:
        self.closes = 0
        # Whether each of them was to leave a window the user can see as it is.
        self.kept_window: list[bool] = []
        self.close_started = asyncio.Event()
        # Set to make closing take until the test lets it finish.
        self.hold: asyncio.Event | None = None
        self.fails = False

    async def close(self, *, keep_window: bool = False) -> None:
        self.closes += 1
        self.kept_window.append(keep_window)
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


class _EveryTask(set):
    """The process's set of background tasks, remembering every task ever added.

    A task takes itself off the set when it is over; ``ever`` still has it,
    so that how it ended can be looked at however early it ended.
    """

    def __init__(self) -> None:
        super().__init__()
        self.ever: set[asyncio.Task] = set()

    def add(self, item: Any) -> None:
        self.ever.add(item)
        super().add(item)

    def __eq__(self, other: object) -> bool:
        # Compared as the tasks it holds now; ``ever`` is only looked at by ``_background_done``.
        return set.__eq__(self, other)

    __hash__ = None  # type: ignore[assignment]  # a set, so not hashable


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
            "computer_use": {"ready": False, "reason": ""},
            "browser_use": {"ready": False, "reason": ""},
            "user_plugin": {"ready": True, "reason": ""},
        },
    )
    monkeypatch.setattr(state, "notification", None)
    monkeypatch.setattr(state, "browser_use_lifecycle_seq", state.browser_use_lifecycle_seq)
    monkeypatch.setattr(state, "state_revision", state.state_revision)
    monkeypatch.setattr(state, "_background_tasks", _EveryTask())
    monkeypatch.setattr(nekopower_builtins, "_in_use", {})
    # A shutdown sets it for good, on the provider every test of this process shares.
    monkeypatch.setattr(nekopower_builtins.provider, "_shut_down", False)

    async def ignore_status_update(*_args: Any, **_kwargs: Any) -> None:
        return None

    # Sending the status would also take the notification the test wants to read.
    monkeypatch.setattr(api_routes, "_emit_agent_status_update", ignore_status_update)
    monkeypatch.setattr(capabilities, "_emit_agent_status_update", ignore_status_update)
    _gate(monkeypatch, GATE_READY)
    return state


def _gate(monkeypatch: pytest.MonkeyPatch, gate: dict[str, Any]) -> None:
    """What the configuration check says, wherever it is asked."""
    monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: dict(gate))
    monkeypatch.setattr(api_runtime, "_check_agent_api_gate", lambda: dict(gate))
    # Whether the tools can be used asks it through the package.
    monkeypatch.setattr("app.agent_server._check_agent_api_gate", lambda: dict(gate))


@pytest.fixture
def search(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Search:
    fake = Search()
    monkeypatch.setattr("nekopower.builtin.browser.launcher.find_browsers", fake)
    # No browser is named in the settings unless the test names one.
    fake.settings_root = tmp_path / "settings" / "nekopower"
    monkeypatch.setattr("nekopower.builtin.browser.settings._root", lambda: fake.settings_root)
    monkeypatch.setattr(capabilities, "_browser_search", None)
    return fake


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch) -> Browser:
    fake = Browser()
    monkeypatch.setattr(nekopower_builtins.provider, "close_browser", fake.close)
    # No browser tools are built by anything here; a test can tell if they were.
    monkeypatch.setattr(nekopower_builtins.provider, "_browser", None)
    return fake


@pytest.fixture
def checks(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every chat client built for a model, recorded instead of built.

    Nothing the switches, the availability routes or a saved configuration
    do asks the agent model anything: a client being built is what sending
    it a request would start with.
    """
    built: list[dict[str, Any]] = []

    def record(**options: Any) -> Any:
        built.append(options)
        return SimpleNamespace()

    async def record_async(**options: Any) -> Any:
        return record(**options)

    monkeypatch.setattr("utils.llm_client.create_chat_llm", record)
    monkeypatch.setattr("utils.llm_client.create_chat_llm_async", record_async)
    return built


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


def test_a_search_that_fails_is_not_usable_and_says_the_search_failed(search):
    search.found = OSError("the registry cannot be read")

    # Not usable all the same; but not said as "there is no browser".
    assert capabilities._browser_dependency_status() == (False, "AGENT_BROWSER_CHECK_FAILED")


def test_a_search_that_started_before_the_settings_changed_keeps_nothing(search, monkeypatch):
    found = list(search.found)

    def settings_saved_meanwhile(*_args: Any, **_kwargs: Any) -> Any:
        search.searches += 1
        if search.searches == 1:
            # The browser settings are saved while this search is under way.
            capabilities._forget_browser_search()
        return found

    monkeypatch.setattr("nekopower.builtin.browser.launcher.find_browsers", settings_saved_meanwhile)

    # Answered all the same, but not kept: the next one looks afresh.
    assert capabilities._browser_dependency_status() == (True, "")
    assert capabilities._browser_search is None
    assert capabilities._browser_dependency_status() == (True, "")
    assert search.searches == 2 and capabilities._browser_search is not None


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

    assert status == {"enabled": True, "ready": False, "reasons": ["AGENT_BROWSER_NOT_FOUND"], "provider": "nekopower"}
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_BROWSER_NOT_FOUND"}
    # The model is not what is missing: it is not probed for this.
    assert checks == []


async def test_availability_is_ready_with_a_browser_and_a_model_that_answers_and_starts_no_browser(
    modules, search, browser, checks
):
    status = await api_routes.browser_use_availability()
    await asyncio.sleep(0)

    assert status == {"enabled": True, "ready": True, "reasons": [], "provider": "nekopower"}
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}
    # Asked on every poll of the settings page: nothing is built, started or probed.
    assert nekopower_builtins.provider._browser is None
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
    assert nekopower_builtins.provider._browser is None
    assert checks == [] and scheduled == [] and browser.closes == 0


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

    await capabilities._close_browser(capability_reason="AGENT_ENDPOINT_NOT_CONFIGURED")
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_ENDPOINT_NOT_CONFIGURED"}

    # Closed for another reason than the switch (everything was stopped, the
    # settings changed): what the switch shows is left alone.
    await capabilities._close_browser(update_capability=False)
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_ENDPOINT_NOT_CONFIGURED"}
    assert browser.closes == 3


async def test_closing_with_the_agent_model_not_set_up_says_that_rather_than_the_browser(modules, search, browser, monkeypatch):
    _gate(monkeypatch, GATE_NOT_CONFIGURED)

    await capabilities._close_browser()

    # A browser is found, and still the tools cannot be used: the model is what is missing.
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_ENDPOINT_NOT_CONFIGURED"}


async def test_closing_without_a_browser_on_this_computer_says_that(modules, search, browser):
    search.found = []

    await capabilities._close_browser()

    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_BROWSER_NOT_FOUND"}


async def test_a_close_that_fails_still_settles_the_capability(modules, search, browser):
    browser.fails = True

    await capabilities._close_browser(capability_reason="AGENT_ENDPOINT_NOT_CONFIGURED")

    assert browser.closes == 1
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_ENDPOINT_NOT_CONFIGURED"}


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
    closing = asyncio.ensure_future(capabilities._close_browser(capability_reason="AGENT_ENDPOINT_NOT_CONFIGURED"))
    await asyncio.wait_for(browser.close_started.wait(), 2.0)

    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing

    # Whoever cancelled it (stopping everything, shutting down) closes again.
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}
    browser.hold.set()
    await capabilities._close_browser(capability_reason="AGENT_ENDPOINT_NOT_CONFIGURED")
    assert browser.closes == 2
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_ENDPOINT_NOT_CONFIGURED"}


# ---------------------------------------------------------------------------
# Whether the tools can be used: the configuration and this computer, no model
# ---------------------------------------------------------------------------


async def _background_done(modules: Any, timeout: float = 10.0) -> None:
    """Let what a request left to the background run to its end; one that fails or hangs fails the test."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    # A task that is over takes itself off ``_background_tasks``; the set the
    # ``modules`` fixture puts in its place remembers every task ever added,
    # so one that ended before this was called is looked at as well.
    def every() -> set[asyncio.Task]:
        return set(getattr(modules._background_tasks, "ever", modules._background_tasks))

    seen: set[asyncio.Task] = every()
    while True:
        await asyncio.sleep(0)
        seen |= every()
        pending = [task for task in seen if not task.done()]
        if not pending:
            break
        left = deadline - loop.time()
        _done, still = await asyncio.wait(pending, timeout=max(left, 0.0))
        if still and loop.time() >= deadline:
            for task in still:
                task.cancel()
            await asyncio.gather(*still, return_exceptions=True)
            pytest.fail(f"{len(still)} background task(s) did not finish within {timeout}s")
    for task in seen:
        if not task.cancelled() and task.exception() is not None:
            raise task.exception()


@pytest.mark.parametrize("ended_before_the_wait", [False, True])
async def test_a_background_task_that_fails_fails_the_wait_for_it(modules, ended_before_the_wait):
    async def breaks() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("the status could not be sent")

    task = capabilities._shared._create_tracked_task(breaks())
    if ended_before_the_wait:
        # Over, and off the set, before anything waits for it.
        await asyncio.wait({task})
        await asyncio.sleep(0)
        assert task not in modules._background_tasks

    with pytest.raises(RuntimeError, match="the status could not be sent"):
        await _background_done(modules)
    # It took itself off the set when it ended, and was looked at all the same.
    assert modules._background_tasks == set()


def _keyboard(monkeypatch: pytest.MonkeyPatch, *, ready: bool, reasons: list[str] | None = None) -> list[int]:
    asked: list[int] = []

    def availability() -> dict[str, Any]:
        asked.append(1)
        return {"enabled": True, "ready": ready, "reasons": list(reasons or []), "provider": "nekopower", "model": "m"}

    monkeypatch.setattr(capabilities, "_keyboard_mouse_availability", availability)
    return asked


async def test_both_groups_are_ready_when_the_model_is_set_up_and_their_means_are_there(
    modules, search, browser, checks, monkeypatch
):
    asked = _keyboard(monkeypatch, ready=True)

    await capabilities._refresh_tool_capabilities()

    assert asked == [1]
    assert modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}
    # Nothing was sent to the model to find that out.
    assert checks == []


async def test_a_keyboard_check_that_breaks_still_lets_the_browser_be_said(modules, search, browser, checks, monkeypatch):
    def broken() -> dict[str, Any]:
        raise RuntimeError("the display driver went away")

    monkeypatch.setattr(capabilities, "_keyboard_mouse_availability", broken)
    modules.capability_cache["computer_use"] = {"ready": True, "reason": ""}

    await capabilities._refresh_tool_capabilities()

    # Not usable, and why is said with a code that has its words.
    assert modules.capability_cache["computer_use"] == {"ready": False, "reason": "AGENT_INPUT_CHECK_FAILED"}
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}


async def test_a_browser_search_that_breaks_says_so(modules, search, browser, checks, monkeypatch):
    _keyboard(monkeypatch, ready=True)
    monkeypatch.setattr(capabilities, "_browser_dependency_status", lambda: (_ for _ in ()).throw(RuntimeError("the disk went away")))

    await capabilities._refresh_tool_capabilities()

    assert modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_BROWSER_CHECK_FAILED"}


async def test_a_keyboard_switched_off_while_an_earlier_request_waited_for_its_check_stays_off(
    modules, search, browser, checks, monkeypatch
):
    import threading

    release = threading.Event()

    def slow_keyboard() -> dict[str, Any]:
        release.wait(5.0)
        return {"enabled": True, "ready": True, "reasons": [], "provider": "nekopower", "model": "m"}

    monkeypatch.setattr(api_routes, "_keyboard_mouse_availability", slow_keyboard)
    monkeypatch.setattr(modules, "computer_use_switch_seq", modules.computer_use_switch_seq)
    switching_on = asyncio.ensure_future(
        api_routes.set_agent_flags({"computer_use_enabled": True, "_persist_intent": False})
    )
    try:
        await asyncio.sleep(0.05)
        assert not switching_on.done()
        # Switched off meanwhile.
        await api_routes.set_agent_flags({"computer_use_enabled": False, "_persist_intent": False})
    finally:
        release.set()
    await asyncio.wait_for(switching_on, 5.0)

    assert modules.agent_flags["computer_use_enabled"] is False
    assert modules.notification is None


async def test_a_refresh_that_started_earlier_and_finishes_later_writes_nothing_over_the_later_one(
    modules, search, browser, checks, monkeypatch
):
    import threading

    first_asked = threading.Event()
    release_first = threading.Event()
    calls: list[int] = []

    def keyboard() -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            # The configuration as it was before it was saved: no key yet.
            first_asked.set()
            release_first.wait(5.0)
            return {"enabled": True, "ready": False, "reasons": ["AGENT_KEY_NOT_CONFIGURED"]}
        return {"enabled": True, "ready": True, "reasons": []}

    monkeypatch.setattr(capabilities, "_keyboard_mouse_availability", keyboard)
    earlier = asyncio.ensure_future(capabilities._refresh_tool_capabilities())
    try:
        await asyncio.wait_for(asyncio.to_thread(first_asked.wait, 5.0), 6.0)
        # The key was saved meanwhile: a later refresh finds the tools usable.
        await capabilities._refresh_tool_capabilities()
        assert modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}
    finally:
        release_first.set()
    await asyncio.wait_for(earlier, 5.0)

    assert modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}


async def test_a_browser_check_that_started_earlier_and_finishes_later_writes_nothing_over_the_later_one(
    modules, search, browser, checks, monkeypatch
):
    import threading

    first_asked = threading.Event()
    release_first = threading.Event()
    calls: list[int] = []

    def browser_check() -> tuple[bool, str]:
        calls.append(1)
        if len(calls) == 1:
            # Before a browser was named in the settings: none found.
            first_asked.set()
            release_first.wait(5.0)
            return False, "AGENT_BROWSER_NOT_FOUND"
        return True, ""

    _keyboard(monkeypatch, ready=True)
    monkeypatch.setattr(capabilities, "_browser_availability", browser_check)
    earlier = asyncio.ensure_future(capabilities._refresh_tool_capabilities())
    try:
        await asyncio.wait_for(asyncio.to_thread(first_asked.wait, 5.0), 6.0)
        await capabilities._refresh_tool_capabilities()
        assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}
    finally:
        release_first.set()
    await asyncio.wait_for(earlier, 5.0)

    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}


async def test_a_browser_switched_off_while_an_earlier_request_looked_for_a_browser_stays_off(
    modules, search, browser, checks, monkeypatch, scheduled
):
    import threading

    release = threading.Event()

    def slow_search() -> tuple[bool, str]:
        release.wait(5.0)
        return True, ""

    monkeypatch.setattr(api_routes, "_browser_dependency_status", slow_search)
    switching_on = asyncio.ensure_future(
        api_routes.set_agent_flags({"browser_use_enabled": True, "_persist_intent": False})
    )
    try:
        await asyncio.sleep(0.05)
        assert not switching_on.done()
        await api_routes.set_agent_flags({"browser_use_enabled": False, "_persist_intent": False})
    finally:
        release.set()
    await asyncio.wait_for(switching_on, 5.0)

    assert modules.agent_flags["browser_use_enabled"] is False
    # The earlier request, overtaken, wrote nothing: neither the switch nor a notice.
    assert modules.notification is None


async def test_an_older_check_that_ends_first_does_not_keep_a_later_refresh_from_writing(
    modules, search, browser, checks, monkeypatch
):
    import threading

    older_asked = threading.Event()
    release_older = threading.Event()
    newer_asked = threading.Event()
    release_newer = threading.Event()
    calls: list[int] = []

    def keyboard() -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            # An availability route, from the configuration before it was saved.
            older_asked.set()
            release_older.wait(5.0)
            return {"enabled": True, "ready": False, "reasons": ["AGENT_KEY_NOT_CONFIGURED"]}
        # The refresh that saving the configuration started.
        newer_asked.set()
        release_newer.wait(5.0)
        return {"enabled": True, "ready": True, "reasons": []}

    monkeypatch.setattr(api_routes, "_keyboard_mouse_availability", keyboard)
    monkeypatch.setattr(capabilities, "_keyboard_mouse_availability", keyboard)

    async def no_pictures_asked() -> dict[str, Any]:
        return {"sees": True}

    monkeypatch.setattr(api_routes, "_model_images_status", no_pictures_asked)
    older = asyncio.ensure_future(api_routes.computer_use_availability())
    newer: Any = None
    try:
        await asyncio.wait_for(asyncio.to_thread(older_asked.wait, 5.0), 6.0)
        newer = asyncio.ensure_future(capabilities._refresh_tool_capabilities())
        await asyncio.wait_for(asyncio.to_thread(newer_asked.wait, 5.0), 6.0)
        # The older one ends first and writes what it found...
        release_older.set()
        await asyncio.wait_for(older, 5.0)
        assert modules.capability_cache["computer_use"]["reason"] == "AGENT_KEY_NOT_CONFIGURED"
    finally:
        release_older.set()
        release_newer.set()
    # ...and the later refresh still writes what the new configuration says.
    await asyncio.wait_for(newer, 5.0)

    assert modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}


async def test_what_is_written_while_a_refresh_checks_is_not_written_over(modules, search, browser, checks, monkeypatch):
    import threading

    asked = threading.Event()
    release = threading.Event()

    def slow_keyboard() -> dict[str, Any]:
        asked.set()
        release.wait(5.0)
        return {"enabled": True, "ready": True, "reasons": []}

    monkeypatch.setattr(capabilities, "_keyboard_mouse_availability", slow_keyboard)
    refreshing = asyncio.ensure_future(capabilities._refresh_tool_capabilities())
    try:
        await asyncio.wait_for(asyncio.to_thread(asked.wait, 5.0), 6.0)
        # Meanwhile a route finds input cannot be sent any more.
        capabilities._set_capability("computer_use", False, "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE")
    finally:
        release.set()
    await asyncio.wait_for(refreshing, 5.0)

    assert modules.capability_cache["computer_use"] == {"ready": False, "reason": "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"}


@pytest.mark.parametrize("group", ["keyboard_mouse", "browser"])
async def test_a_switch_on_that_waited_goes_by_a_newer_check_the_saved_configuration_started(
    modules, search, browser, checks, monkeypatch, scheduled, group
):
    import threading

    asked = threading.Event()
    release = threading.Event()
    flag, capability = (
        ("computer_use_enabled", "computer_use") if group == "keyboard_mouse" else ("browser_use_enabled", "browser_use")
    )

    def slow_keyboard() -> dict[str, Any]:
        asked.set()
        release.wait(5.0)
        return {"enabled": True, "ready": True, "reasons": []}

    def slow_search() -> tuple[bool, str]:
        asked.set()
        release.wait(5.0)
        return True, ""

    if group == "keyboard_mouse":
        monkeypatch.setattr(api_routes, "_keyboard_mouse_availability", slow_keyboard)
    else:
        monkeypatch.setattr(api_routes, "_browser_dependency_status", slow_search)
    switching_on = asyncio.ensure_future(api_routes.set_agent_flags({flag: True, "_persist_intent": False}))
    try:
        await asyncio.wait_for(asyncio.to_thread(asked.wait, 5.0), 6.0)
        # The key is taken out of the configuration meanwhile: the refresh
        # that saving starts says the tools cannot be used.
        _gate(monkeypatch, {"ready": False, "reasons": ["Agent API Key 未配置或不可用"], "is_free_version": False})
        monkeypatch.setattr(capabilities, "_keyboard_mouse_availability", lambda: {"ready": False, "reasons": ["AGENT_KEY_NOT_CONFIGURED"]})
        await capabilities._refresh_tool_capabilities()
    finally:
        release.set()
    await asyncio.wait_for(switching_on, 5.0)

    # The earlier answer ("ready") is older than the refresh's: the switch stays off and says why.
    assert modules.agent_flags[flag] is False
    assert modules.capability_cache[capability] == {"ready": False, "reason": "AGENT_KEY_NOT_CONFIGURED"}
    code = "AGENT_CU_UNAVAILABLE" if group == "keyboard_mouse" else "AGENT_BU_UNAVAILABLE"
    assert json.loads(modules.notification) == {"code": code, "details": {"reason_code": "AGENT_KEY_NOT_CONFIGURED"}}


async def test_an_older_availability_check_switches_nothing_off_over_a_newer_enable(
    modules, search, browser, checks, monkeypatch
):
    import threading

    asked = threading.Event()
    release = threading.Event()

    def stale_check() -> dict[str, Any]:
        asked.set()
        release.wait(5.0)
        return {"enabled": True, "ready": False, "reasons": ["AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"]}

    async def no_pictures_asked() -> dict[str, Any]:
        return {"sees": True}

    monkeypatch.setattr(api_routes, "_keyboard_mouse_availability", stale_check)
    monkeypatch.setattr(api_routes, "_model_images_status", no_pictures_asked)
    older = asyncio.ensure_future(api_routes.computer_use_availability())
    try:
        await asyncio.wait_for(asyncio.to_thread(asked.wait, 5.0), 6.0)
        # Switched on meanwhile, from a check that started later and found input works.
        modules.agent_flags["computer_use_enabled"] = True
        api_routes._set_capability("computer_use", True, "")
    finally:
        release.set()
    status = await asyncio.wait_for(older, 5.0)

    assert modules.agent_flags["computer_use_enabled"] is True
    assert modules.notification is None
    assert modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}
    # What it answers is the newer word too.
    assert (status["ready"], status["reasons"]) == (True, [])


@pytest.mark.parametrize("group", ["keyboard_mouse", "browser"])
async def test_a_switch_on_waiting_for_its_check_is_not_held_back_by_the_master_switch_coming_on(
    modules, search, browser, checks, monkeypatch, scheduled, group
):
    import threading

    asked = threading.Event()
    release = threading.Event()
    flag, capability = (
        ("computer_use_enabled", "computer_use") if group == "keyboard_mouse" else ("browser_use_enabled", "browser_use")
    )

    def slow_keyboard() -> dict[str, Any]:
        asked.set()
        release.wait(5.0)
        return {"enabled": True, "ready": True, "reasons": []}

    def slow_search() -> tuple[bool, str]:
        asked.set()
        release.wait(5.0)
        return True, ""

    if group == "keyboard_mouse":
        monkeypatch.setattr(api_routes, "_keyboard_mouse_availability", slow_keyboard)
    else:
        monkeypatch.setattr(api_routes, "_browser_dependency_status", slow_search)
    # The background refresh the master switch starts is kept from running here.
    monkeypatch.setattr(api_routes, "_create_tracked_task", lambda coro: coro.close())
    modules.analyzer_enabled = False
    switching_on = asyncio.ensure_future(api_routes.set_agent_flags({flag: True, "_persist_intent": False}))
    try:
        await asyncio.wait_for(asyncio.to_thread(asked.wait, 5.0), 6.0)
        # The master switch comes on meanwhile: "not checked yet" for both.
        await api_routes.agent_command({"command": "set_agent_enabled", "enabled": True, "_persist_intent": False})
        assert modules.capability_cache[capability] == {"ready": False, "reason": ""}
    finally:
        release.set()
    await asyncio.wait_for(switching_on, 5.0)

    # Its own check answered "usable": that stands, and the switch is on.
    assert modules.agent_flags[flag] is True
    assert modules.capability_cache[capability] == {"ready": True, "reason": ""}
    # Nothing says it could not be switched on (the plugins coming on with the master switch say their own).
    assert (_notification(modules) or {}).get("code") not in ("AGENT_CU_UNAVAILABLE", "AGENT_BU_UNAVAILABLE")


async def test_without_a_browser_only_the_browser_tools_are_not_ready(modules, search, browser, checks, monkeypatch):
    _keyboard(monkeypatch, ready=True)
    search.found = []

    await capabilities._refresh_tool_capabilities()

    assert modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_BROWSER_NOT_FOUND"}


async def test_input_that_cannot_be_sent_leaves_only_the_keyboard_not_ready(modules, search, browser, checks, monkeypatch):
    _keyboard(monkeypatch, ready=False, reasons=["AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"])

    await capabilities._refresh_tool_capabilities()

    assert modules.capability_cache["computer_use"] == {"ready": False, "reason": "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"}
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}


@pytest.mark.parametrize(
    ("missing", "code"),
    [
        # The wordings of ``ConfigManager.is_agent_api_ready``.
        ("Agent 模型未配置", "AGENT_MODEL_NOT_CONFIGURED"),
        ("Agent API URL 未配置", "AGENT_URL_NOT_CONFIGURED"),
        ("Agent API Key 未配置或不可用", "AGENT_KEY_NOT_CONFIGURED"),
        # The check itself failed: the model counts as not set up.
        ("Agent API check failed: disk gone", "AGENT_CONFIG_CHECK_FAILED"),
    ],
)
async def test_a_model_not_set_up_leaves_neither_group_ready_and_says_what_is_missing(
    modules, search, browser, checks, monkeypatch, missing, code
):
    from app.agent_server import nekopower_builtins as builtins

    _gate(monkeypatch, {"ready": False, "reasons": [missing], "is_free_version": False})
    monkeypatch.setattr(builtins.provider, "_desktop", SimpleNamespace(available=lambda: (True, "")))

    await capabilities._refresh_tool_capabilities()

    assert modules.capability_cache["computer_use"] == {"ready": False, "reason": code}
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": code}
    # Nobody looks for a browser for a model that is not there.
    assert search.searches == 0 and checks == []


# ---------------------------------------------------------------------------
# The master switch and a saved configuration
# ---------------------------------------------------------------------------


async def test_switching_the_master_switch_on_says_at_once_whether_the_tools_can_be_used(
    modules, search, browser, checks, monkeypatch
):
    _keyboard(monkeypatch, ready=True)
    search.found = []
    modules.analyzer_enabled = False

    result = await api_routes.agent_command({"command": "set_agent_enabled", "enabled": True, "_persist_intent": False})
    await _background_done(modules)

    assert result["success"] is True and modules.analyzer_enabled is True
    # Worked out right after, from what is filled in and what is on this computer.
    assert modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_BROWSER_NOT_FOUND"}
    # The agent model was asked nothing.
    assert checks == []


async def test_switching_the_master_switch_on_waits_for_nothing_so_a_later_request_is_not_overtaken(
    modules, search, browser, checks, monkeypatch
):
    import threading

    release = threading.Event()

    def slow_keyboard() -> dict[str, Any]:
        # Importing pyautogui the first time can take a while.
        release.wait(5.0)
        return {"enabled": True, "ready": True, "reasons": [], "provider": "nekopower", "model": "m"}

    async def started_ok(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(capabilities, "_keyboard_mouse_availability", slow_keyboard)
    monkeypatch.setattr(api_routes, "_ensure_plugin_lifecycle_started", started_ok)
    monkeypatch.setattr(api_routes, "_ensure_plugin_lifecycle_stopped", started_ok)
    monkeypatch.setattr(modules, "user_plugin_lifecycle_seq", modules.user_plugin_lifecycle_seq)
    modules.analyzer_enabled = False
    try:
        # The answer does not wait for the tools to be worked out.
        await asyncio.wait_for(
            api_routes.agent_command({"command": "set_agent_enabled", "enabled": True, "_persist_intent": False}), 2.0
        )
        assert modules.agent_flags["user_plugin_enabled"] is True
        # Until the check is done the tools show nothing kept from before.
        assert modules.capability_cache["computer_use"] == {"ready": False, "reason": ""}
        assert modules.capability_cache["browser_use"] == {"ready": False, "reason": ""}
        # The plugins are switched off while that is still under way: it stays off.
        await api_routes.set_agent_flags({"user_plugin_enabled": False, "_persist_intent": False})
    finally:
        release.set()
    await _background_done(modules)

    assert modules.agent_flags["user_plugin_enabled"] is False
    assert modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}


async def test_switching_the_master_switch_on_without_a_configured_model_says_so(modules, search, browser, checks, monkeypatch):
    _gate(monkeypatch, GATE_NOT_CONFIGURED)
    modules.analyzer_enabled = False

    await api_routes.agent_command({"command": "set_agent_enabled", "enabled": True, "_persist_intent": False})
    await _background_done(modules)

    not_configured = {"ready": False, "reason": "AGENT_ENDPOINT_NOT_CONFIGURED"}
    assert modules.capability_cache["computer_use"] == not_configured
    assert modules.capability_cache["browser_use"] == not_configured
    assert checks == [] and search.searches == 0


async def test_a_saved_configuration_says_anew_whether_the_tools_can_be_used(modules, search, browser, checks, monkeypatch):
    _keyboard(monkeypatch, ready=True)
    modules.capability_cache.update(
        computer_use={"ready": False, "reason": "AGENT_KEY_NOT_CONFIGURED"},
        browser_use={"ready": False, "reason": "AGENT_KEY_NOT_CONFIGURED"},
    )

    result = await api_routes.notify_config_changed()
    # Worked out in the background: the main server does not wait for it.
    await _background_done(modules)

    assert result["success"] is True
    # The key was filled in: the tools can be used, and the model was asked nothing.
    assert modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}
    assert checks == []


async def test_a_configuration_saved_while_the_master_switch_is_off_changes_nothing(
    modules, search, browser, checks, monkeypatch
):
    asked = _keyboard(monkeypatch, ready=True)
    modules.analyzer_enabled = False
    before = {name: dict(state) for name, state in modules.capability_cache.items()}

    await api_routes.notify_config_changed()

    assert modules.capability_cache == before and asked == [] and search.searches == 0 and checks == []


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
    monkeypatch.setattr(api_runtime, "_emit_agent_status_update", record("status"))
    monkeypatch.setattr(api_runtime, "shutdown_nekopower_host", record("host"))
    monkeypatch.setattr(api_runtime, "mount_nekopower_host", lambda change_token=None: calls.append("mount"))
    monkeypatch.setattr(api_runtime, "AgentServerEventBridge", Bridge)
    monkeypatch.setattr(
        api_runtime,
        "get_config_manager",
        lambda *args, **kwargs: SimpleNamespace(register_quota_exceeded_notifier=lambda notifier: None),
    )
    return calls


async def test_startup_asks_the_model_nothing_and_neither_looks_for_nor_starts_a_browser(
    modules, process, search, browser, checks, monkeypatch
):
    ready = {"ready": True, "reason": ""}
    modules.capability_cache.update(computer_use=dict(ready), browser_use=dict(ready))

    await api_runtime.startup()
    await asyncio.gather(*modules._persistent_tasks)
    # Whatever startup set going in the background has had its turn too.
    for _ in range(5):
        await asyncio.sleep(0)

    # No model client is built at start, so no request goes to the model.
    assert checks == []
    # Not ready, with no reason: the master switch works that out when it comes on.
    not_worked_out = {"ready": False, "reason": ""}
    assert modules.capability_cache["computer_use"] == not_worked_out
    assert modules.capability_cache["browser_use"] == not_worked_out
    assert "mount" in process
    # Startup stays free of file checks and processes: the browser is the
    # business of the first browser tool call.
    assert search.searches == 0
    assert nekopower_builtins.provider._browser is None and browser.closes == 0


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

    monkeypatch.setattr(nekopower_builtins.provider, "close_browser", close)
    monkeypatch.setattr(nekopower_builtins.provider, "_desktop", Desktop())
    modules.capability_cache["browser_use"] = {"ready": False, "reason": "AGENT_ENDPOINT_NOT_CONFIGURED"}

    await api_runtime.shutdown()

    # The host's shutdown cancels every call under way; only then is the
    # browser those calls were driving closed.
    assert order.index("host") < order.index("browser closed")
    assert "keys released" in order
    assert order[-1] == "status"
    # What the status sent last says about the browser tools is their own
    # means again, not a browser that happens to be closed.
    assert modules.capability_cache["browser_use"] == {"ready": True, "reason": ""}


async def test_from_the_start_of_a_shutdown_no_call_of_the_built_in_tools_is_carried_out(
    modules, process, search, browser, monkeypatch
):
    from nekopower.host.capabilities import BUILTIN_GROUP_BROWSER

    seen: list[Any] = []
    real_host_shutdown = api_runtime.shutdown_nekopower_host

    async def host_shutdown() -> None:
        # The first step of the shutdown: a task agent whose runtime is not stopped yet calls a tool now.
        seen.append(nekopower_builtins.provider._ready(BUILTIN_GROUP_BROWSER))
        await real_host_shutdown()

    monkeypatch.setattr(api_runtime, "shutdown_nekopower_host", host_shutdown)
    monkeypatch.setattr(nekopower_builtins.provider, "_state", lambda group: (True, ""))
    assert nekopower_builtins.provider._ready(BUILTIN_GROUP_BROWSER) == (True, "")

    await api_runtime.shutdown()

    # Refused already then, well before the browser is closed: it would start another, with nobody left to close it.
    assert seen == [(False, "shutting down with the application")]
    assert browser.closes == 1


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
    from nekopower.host import mount

    monkeypatch.setattr(mount, "peek_host", lambda: None)
    services = {
        config.MAIN_SERVER_PORT,
        config.MEMORY_SERVER_PORT,
        config.MONITOR_SERVER_PORT,
        config.COMMENTER_SERVER_PORT,
        config.TOOL_SERVER_PORT,
        config.USER_PLUGIN_SERVER_PORT,
    }
    assert nekopower_builtins._own_ports() == services
    # The capability gateway picks its port when it starts to listen.
    monkeypatch.setattr(mount, "peek_host", lambda: SimpleNamespace(gateway=SimpleNamespace(port=51234)))
    assert nekopower_builtins._own_ports() == services | {51234}
    monkeypatch.setattr(mount, "peek_host", lambda: SimpleNamespace(gateway=SimpleNamespace(port=None)))
    assert nekopower_builtins._own_ports() == services
    # They are what the provider of this process hands to the browser it starts.
    assert nekopower_builtins.provider._own_ports is nekopower_builtins._own_ports


async def test_shutdown_kills_a_browser_that_does_not_close_in_the_time_it_has(
    modules, process, search, browser, monkeypatch
):
    import threading

    import config.nekopower_settings as settings

    killed_in: list[threading.Thread] = []
    monkeypatch.setattr(settings, "BROWSER_CLOSE_AT_SHUTDOWN_SECONDS", 0.05)
    monkeypatch.setattr(nekopower_builtins.provider, "kill_browser", lambda: killed_in.append(threading.current_thread()))
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
    monkeypatch.setattr(nekopower_builtins.provider, "kill_browser", lambda: killed.append(1))
    import config.nekopower_settings as settings

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

    import config.nekopower_settings as settings

    monkeypatch.setattr(settings, "BROWSER_KILL_AT_SHUTDOWN_SECONDS", 0.05)
    monkeypatch.setattr(api_runtime, "shutdown_nekopower_host", host_that_will_not_stop)
    monkeypatch.setattr(nekopower_builtins.provider, "kill_browser", lambda: killed_in.append(threading.current_thread()))

    shutting_down = asyncio.ensure_future(api_runtime.shutdown())
    try:
        deadline = asyncio.get_running_loop().time() + 5.0
        while not killed_in and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        # The step that closes the browser was never reached (the launcher would end the
        # process about now), and the browser is killed all the same, off the event loop.
        assert browser.closes == 0 and not shutting_down.done()
        assert len(killed_in) == 1 and killed_in[0] is not threading.current_thread()
        # Well past that time by the clock the shutdown reads as well: it moves in steps
        # (of 15 ms on Windows) and may lag the timer that just fired by a few thousandths.
        await asyncio.sleep(0.1)
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

    monkeypatch.setattr(nekopower_builtins.provider, "kill_browser", cannot)
    # On a thread of its own, where nothing would hear of it.
    api_runtime._kill_browser_at_last()


# ---------------------------------------------------------------------------
# Saving the browser settings says at once whether the browser tools can be used
# ---------------------------------------------------------------------------


async def _save_browser_settings(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], *, gate: dict[str, Any] = GATE_READY
) -> list[str]:
    told: list[str] = []
    _gate(monkeypatch, gate)

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


# ---------------------------------------------------------------------------
# Shutdown: a browser that answered in time but could not be closed
# ---------------------------------------------------------------------------


async def test_shutdown_kills_a_browser_that_was_asked_in_time_and_could_not_be_closed(
    modules, process, search, browser, monkeypatch
):
    killed: list[int] = []
    monkeypatch.setattr(nekopower_builtins.provider, "kill_browser", lambda: killed.append(1))
    # Closing it failed: the session keeps it to be closed the next time, and there is none.
    monkeypatch.setattr(nekopower_builtins.provider, "browser_unclosed", lambda: True)

    await asyncio.wait_for(api_runtime.shutdown(), 10.0)

    assert browser.closes == 1 and killed == [1]
    assert process[-1] == "status"


async def test_saving_the_browser_settings_without_a_configured_model_says_that_rather_than_the_browser(
    modules, search, tmp_path, monkeypatch
):
    portable = tmp_path / "portable" / "chrome.exe"
    portable.parent.mkdir()
    portable.write_bytes(b"")
    search.found = []
    modules.capability_cache["browser_use"] = {"ready": True, "reason": ""}

    told = await _save_browser_settings(monkeypatch, {"executable": str(portable)}, gate=GATE_NOT_CONFIGURED)

    # A browser was named, and still the tools cannot be used: the model is what is missing.
    assert modules.capability_cache["browser_use"] == {"ready": False, "reason": "AGENT_ENDPOINT_NOT_CONFIGURED"}
    assert told == ["status"]


async def test_saving_the_browser_settings_reads_the_configuration_off_the_event_loop(modules, search, tmp_path, monkeypatch):
    import threading

    asked_in: list[threading.Thread] = []

    def gate() -> dict[str, Any]:
        asked_in.append(threading.current_thread())
        return dict(GATE_READY)

    async def close_browser(**options: Any) -> None:
        return None

    async def status_update(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(api_runtime, "_check_agent_api_gate", gate)
    monkeypatch.setattr("app.agent_server._check_agent_api_gate", gate)
    monkeypatch.setattr(api_runtime, "_close_browser", close_browser)
    monkeypatch.setattr(api_runtime, "_emit_agent_status_update", status_update)
    monkeypatch.setattr(capabilities._shared.Modules, "_background_tasks", set())

    await api_runtime.set_browser_settings({"headless": True})
    await asyncio.gather(*list(capabilities._shared.Modules._background_tasks))

    # It reads files: on the event loop, a slow disk would hold up every other request meanwhile.
    assert len(asked_in) == 1 and asked_in[0] is not threading.current_thread()
