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

"""The probe of the agent model, and what the keyboard-and-mouse switch makes of it.

``app/agent_server/model_probe.py`` sends one small request to the model in
the ``agent`` slot and classifies a failure. Nothing here reaches a network:
the configuration and the client the probe builds are fakes. How a failure's
text maps to a reason code is covered in ``test_agent_runtime_intent.py``.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

CONFIGURED = {
    "api_key": "sk-test",
    "base_url": "https://api.example.com/v1",
    "model": "test-model",
    "provider_type": "openai",
}


class Client:
    """Stands in for the chat client: answers, or fails, as scripted."""

    def __init__(self, outcomes: list[Any] | None = None) -> None:
        # One entry per request; an exception is raised, anything else returned.
        self.outcomes = list(outcomes or [])
        self.requests: list[dict[str, Any]] = []

    def invoke_raw(self, messages: Any, **options: Any) -> Any:
        self.requests.append({"messages": messages, **options})
        outcome = self.outcomes.pop(0) if self.outcomes else SimpleNamespace(choices=[])
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class Setup:
    """A probe with a configuration that can be changed and clients that are counted."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, config: dict[str, Any]) -> None:
        from app.agent_server import model_probe

        self.module = model_probe
        self.config = dict(config)
        self.built: list[dict[str, Any]] = []
        self.client = Client()
        self.slept: list[float] = []

        def create_chat_llm(**options: Any) -> Client:
            self.built.append(options)
            return self.client

        monkeypatch.setattr(
            "utils.config_manager.get_config_manager",
            lambda *args, **kwargs: SimpleNamespace(get_model_api_config=self.read),
        )
        monkeypatch.setattr("utils.llm_client.create_chat_llm", create_chat_llm)
        # Only the module's own reference: waiting between tries is recorded, not done.
        monkeypatch.setattr(model_probe, "time", SimpleNamespace(sleep=self.slept.append))
        self.probe = model_probe.AgentModelProbe()

    def read(self, slot: str) -> dict[str, Any]:
        assert slot == "agent"
        return dict(self.config)


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> Setup:
    return Setup(monkeypatch, CONFIGURED)


def test_a_new_probe_has_not_passed_yet(setup):
    assert setup.probe.init_ok is False
    assert setup.probe.last_error is None
    assert setup.built == []


@pytest.mark.parametrize("missing", ["base_url", "model"])
def test_a_probe_without_an_address_or_a_model_is_not_configured_and_asks_nothing(setup, missing):
    setup.config[missing] = ""
    setup.probe.init_ok = True

    answer = setup.probe.check_connectivity()

    assert answer == (False, "AGENT_ENDPOINT_NOT_CONFIGURED")
    assert setup.probe.init_ok is False
    assert setup.probe.last_error == "Agent model not configured"
    assert setup.built == [] and setup.client.requests == []


def test_a_probe_that_is_answered_passes(setup):
    from config import LLM_PING_MAX_TOKENS

    setup.probe.last_error = "stale"

    answer = setup.probe.check_connectivity(timeout_s=4.0)

    assert answer == (True, "")
    assert setup.probe.init_ok is True
    assert setup.probe.last_error is None
    # One client for the configured model, which does not retry on its own.
    assert setup.built == [
        {
            "model": "test-model",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-test",
            "timeout": 4.0,
            "max_retries": 0,
            "provider_type": "openai",
        }
    ]
    # One request of a few tokens.
    (request,) = setup.client.requests
    assert request["messages"] == [{"role": "user", "content": "ok"}]
    assert request["max_completion_tokens"] == LLM_PING_MAX_TOKENS
    assert request["timeout"] == 4.0
    assert "temperature" not in request


class _AnthropicMessage:
    content = [SimpleNamespace(type="text", text="ok")]


@pytest.mark.parametrize(
    "answered",
    [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]),
        # An endpoint that speaks another dialect, and one that says nothing at all.
        _AnthropicMessage(),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=""))]),
        None,
    ],
)
def test_whatever_a_reachable_model_answers_passes(setup, answered):
    setup.client.outcomes = [answered]

    assert setup.probe.check_connectivity() == (True, "")
    assert setup.probe.init_ok is True


def test_a_model_without_a_key_is_probed_with_a_placeholder(setup):
    # A model served on this computer or the local network often has no key.
    setup.config.update(api_key="", base_url="http://127.0.0.1:11434/v1")

    assert setup.probe.check_connectivity() == (True, "")

    assert (setup.built[0]["api_key"], setup.built[0]["base_url"]) == ("EMPTY", "http://127.0.0.1:11434/v1")


def test_a_failed_probe_is_classified_and_remembered(setup):
    setup.client.outcomes = [Exception("Rate limit exceeded: 429")]
    setup.probe.init_ok = True

    answer = setup.probe.check_connectivity(timeout_s=4.0)

    assert answer == (False, "AGENT_QUOTA_EXCEEDED")
    assert setup.probe.init_ok is False
    assert setup.probe.last_error == "Rate limit exceeded: 429"
    # Not tried again unless asked to.
    assert len(setup.client.requests) == 1 and setup.slept == []


def test_a_client_that_cannot_be_built_is_a_failed_probe(setup, monkeypatch):
    def broken(**options: Any) -> Any:
        raise RuntimeError("getaddrinfo failed")

    monkeypatch.setattr("utils.llm_client.create_chat_llm", broken)

    assert setup.probe.check_connectivity() == (False, "AGENT_DNS_NXDOMAIN")
    assert setup.probe.init_ok is False
    assert setup.probe.last_error == "getaddrinfo failed"


def test_a_probe_is_tried_again_as_often_as_asked_and_passes_once_answered(setup):
    setup.client.outcomes = [Exception("Read timeout"), Exception("Read timeout"), SimpleNamespace(choices=[])]

    answer = setup.probe.check_connectivity(_retries=2)

    assert answer == (True, "")
    assert len(setup.client.requests) == 3
    # It waits longer before each further try, and never after the last one.
    assert setup.slept == [3, 6]
    assert (setup.probe.init_ok, setup.probe.last_error) == (True, None)


def test_a_probe_that_keeps_failing_reports_its_last_failure(setup):
    setup.client.outcomes = [Exception("Read timeout"), Exception("401 Unauthorized")]

    answer = setup.probe.check_connectivity(_retries=1)

    assert answer == (False, "AGENT_API_KEY_INVALID")
    assert len(setup.client.requests) == 2
    assert setup.slept == [3]
    assert setup.probe.last_error == "401 Unauthorized"


def test_the_client_is_kept_between_probes_and_rebuilt_when_the_model_configuration_changes(setup):
    setup.probe.check_connectivity()
    setup.probe.check_connectivity()
    # The same address written with a trailing slash is the same model.
    setup.config["base_url"] = "https://api.example.com/v1/"
    setup.probe.check_connectivity()
    assert len(setup.built) == 1
    assert len(setup.client.requests) == 3

    for key, value in (
        ("model", "another-model"),
        ("api_key", "sk-another"),
        ("base_url", "https://other.example.com/v1"),
        ("provider_type", "anthropic"),
    ):
        before = len(setup.built)
        setup.config[key] = value
        setup.probe.check_connectivity()
        setup.probe.check_connectivity()
        assert len(setup.built) == before + 1, key
        assert setup.built[-1][key] == value


def test_the_model_is_available_once_it_is_configured_and_a_probe_has_passed(setup):
    assert setup.probe.is_available() == {"ready": False, "reasons": ["AGENT_NOT_INITIALIZED"], "model": "test-model"}

    setup.probe.check_connectivity()
    assert setup.probe.is_available() == {"ready": True, "reasons": [], "model": "test-model"}

    setup.client.outcomes = [Exception("connection refused")]
    setup.probe.check_connectivity()
    assert setup.probe.is_available() == {"ready": False, "reasons": ["AGENT_NOT_INITIALIZED"], "model": "test-model"}


def test_a_model_that_is_no_longer_configured_is_not_available_even_after_a_passed_probe(setup):
    setup.probe.check_connectivity()
    setup.config["model"] = ""

    assert setup.probe.is_available() == {"ready": False, "reasons": ["AGENT_ENDPOINT_NOT_CONFIGURED"], "model": ""}


# ---------------------------------------------------------------------------
# Rebuilding the probe after the configuration was saved
# ---------------------------------------------------------------------------


@pytest.fixture
def modules(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.agent_server import _shared

    monkeypatch.setattr(_shared.Modules, "model_probe", None)
    return _shared.Modules


def test_refreshing_keeps_a_probe_that_passed_unless_forced(setup, modules):
    from app.agent_server import capabilities

    passed = SimpleNamespace(init_ok=True)
    modules.model_probe = passed

    assert capabilities._try_refresh_model_probe() is True
    assert modules.model_probe is passed

    assert capabilities._try_refresh_model_probe(force=True) is True
    assert modules.model_probe is not passed
    assert isinstance(modules.model_probe, setup.module.AgentModelProbe)
    # Rebuilding asks nothing of the model: the new probe has not passed yet.
    assert modules.model_probe.init_ok is False and setup.client.requests == []


@pytest.mark.parametrize("current", [None, SimpleNamespace(init_ok=False)])
def test_refreshing_builds_a_probe_when_there_is_none_or_it_has_not_passed(setup, modules, current):
    from app.agent_server import capabilities

    modules.model_probe = current

    assert capabilities._try_refresh_model_probe() is True
    assert isinstance(modules.model_probe, setup.module.AgentModelProbe)


def test_a_probe_that_cannot_be_rebuilt_leaves_the_one_there_is(setup, modules, monkeypatch):
    from app.agent_server import capabilities

    def broken() -> Any:
        raise RuntimeError("configuration unreadable")

    earlier = SimpleNamespace(init_ok=True)
    modules.model_probe = earlier
    monkeypatch.setattr(capabilities, "AgentModelProbe", broken)

    assert capabilities._try_refresh_model_probe(force=True) is False
    assert modules.model_probe is earlier


# ---------------------------------------------------------------------------
# Whether the keyboard and mouse can be used: the model and input both have to work
# ---------------------------------------------------------------------------


class Desktop:
    """Stands in for ``nekoclaw.builtin.desktop.Desktop``; pyautogui is never imported."""

    def __init__(self, ready: bool = True, reason: str = "") -> None:
        self.ready, self.reason = ready, reason

    def available(self) -> tuple[bool, str]:
        return self.ready, self.reason


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> Desktop:
    from app.agent_server import nekoclaw_builtins

    fake = Desktop()
    monkeypatch.setattr(nekoclaw_builtins.provider, "_desktop", fake)
    return fake


def test_the_keyboard_and_mouse_are_available_when_the_model_answers_and_input_can_be_sent(setup, modules, desktop):
    from app.agent_server import capabilities

    setup.probe.check_connectivity()
    modules.model_probe = setup.probe

    assert capabilities._keyboard_mouse_availability() == {
        "enabled": True,
        "ready": True,
        "reasons": [],
        "provider": "nekoclaw",
        "model": "test-model",
    }


def test_the_keyboard_and_mouse_are_not_available_before_the_model_has_answered(setup, modules, desktop):
    from app.agent_server import capabilities

    modules.model_probe = setup.probe

    status = capabilities._keyboard_mouse_availability()

    assert (status["ready"], status["reasons"]) == (False, ["AGENT_NOT_INITIALIZED"])


def test_the_keyboard_and_mouse_are_not_available_when_input_cannot_be_sent(setup, modules, desktop):
    from app.agent_server import capabilities

    setup.probe.check_connectivity()
    modules.model_probe = setup.probe
    desktop.ready, desktop.reason = False, "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"

    status = capabilities._keyboard_mouse_availability()

    assert (status["ready"], status["reasons"]) == (False, ["AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"])


def test_both_reasons_are_given_and_the_models_comes_first(setup, modules, desktop):
    from app.agent_server import capabilities

    modules.model_probe = setup.probe
    desktop.ready, desktop.reason = False, "AGENT_PYAUTOGUI_NOT_INSTALLED"

    status = capabilities._keyboard_mouse_availability()

    # The first reason is the one the switch shows.
    assert (status["ready"], status["reasons"]) == (False, ["AGENT_NOT_INITIALIZED", "AGENT_PYAUTOGUI_NOT_INSTALLED"])


def test_without_a_probe_the_keyboard_and_mouse_are_not_available(modules, desktop):
    from app.agent_server import capabilities

    status = capabilities._keyboard_mouse_availability()

    assert (status["ready"], status["reasons"], status["model"]) == (False, ["AGENT_CU_MODULE_NOT_LOADED"], "")


# ---------------------------------------------------------------------------
# The keyboard-and-mouse switch and GET /computer_use/availability
# ---------------------------------------------------------------------------

GATE_READY = {"ready": True, "reasons": [], "is_free_version": False}


@pytest.fixture
def switch(setup, modules, desktop, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The route functions, with the probes they start in the background recorded instead of run."""
    from app.agent_server import api_routes

    checks: list[dict[str, Any]] = []

    async def record(**options: Any) -> None:
        checks.append(options)

    async def ignore_status_update(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: dict(GATE_READY))
    monkeypatch.setattr(api_routes, "_fire_agent_llm_connectivity_check", record)
    # Sending the status would also take the notification the test wants to read.
    monkeypatch.setattr(api_routes, "_emit_agent_status_update", ignore_status_update)
    monkeypatch.setattr(
        modules,
        "agent_flags",
        {"computer_use_enabled": False, "browser_use_enabled": False, "user_plugin_enabled": False},
    )
    monkeypatch.setattr(modules, "capability_cache", {"computer_use": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"}})
    monkeypatch.setattr(modules, "notification", None)
    monkeypatch.setattr(modules, "state_revision", modules.state_revision)
    modules.model_probe = setup.probe

    def notification() -> Any:
        return json.loads(modules.notification) if modules.notification else None

    return SimpleNamespace(routes=api_routes, checks=checks, notification=notification, modules=modules)


async def _settled() -> None:
    """Let what a route started with ``ensure_future`` run."""
    await asyncio.sleep(0)


async def test_availability_of_the_keyboard_and_mouse_with_everything_in_place(setup, switch):
    setup.probe.check_connectivity()

    status = await switch.routes.computer_use_availability()
    await _settled()

    assert status == {
        "enabled": True,
        "ready": True,
        "reasons": [],
        "provider": "nekoclaw",
        "model": "test-model",
        # Nothing is known against the model: it is sent the screenshots the tool works from.
        "model_images": {"sees": True},
    }
    assert switch.modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}
    # The model has answered: asking again does not probe it again.
    assert switch.checks == [] and len(setup.client.requests) == 1


async def test_availability_with_the_agent_model_not_configured_says_only_that(setup, switch, monkeypatch):
    monkeypatch.setattr(
        switch.routes, "_check_agent_api_gate", lambda: {"ready": False, "reasons": ["AGENT_ENDPOINT_NOT_CONFIGURED"]}
    )

    status = await switch.routes.computer_use_availability()
    await _settled()

    assert status == {"ready": False, "reasons": ["AGENT_ENDPOINT_NOT_CONFIGURED"]}
    assert switch.checks == []


async def test_availability_before_the_model_has_answered_starts_a_probe_and_is_not_ready(setup, switch):
    status = await switch.routes.computer_use_availability()
    await _settled()

    assert (status["ready"], status["reasons"]) == (False, ["AGENT_NOT_INITIALIZED"])
    assert switch.modules.capability_cache["computer_use"] == {"ready": False, "reason": "AGENT_NOT_INITIALIZED"}
    assert switch.checks == [{}]


async def test_availability_switches_the_keyboard_off_when_input_can_no_longer_be_sent(setup, switch, desktop):
    setup.probe.check_connectivity()
    switch.modules.agent_flags["computer_use_enabled"] = True
    desktop.ready, desktop.reason = False, "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"

    status = await switch.routes.computer_use_availability()

    assert status["ready"] is False
    assert switch.modules.agent_flags["computer_use_enabled"] is False
    assert switch.notification() == {
        "code": "AGENT_CU_CAPABILITY_LOST",
        "details": {"reason_code": "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"},
    }


async def test_availability_without_a_probe_that_can_be_built_is_refused_and_switches_off(setup, switch, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(switch.routes, "_try_refresh_model_probe", lambda force=False: False)
    switch.modules.model_probe = None
    switch.modules.agent_flags["computer_use_enabled"] = True

    with pytest.raises(HTTPException) as raised:
        await switch.routes.computer_use_availability()
    await _settled()

    assert raised.value.status_code == 503
    assert switch.modules.agent_flags["computer_use_enabled"] is False
    assert switch.notification() == {"code": "AGENT_CU_AUTO_CLOSED"}


async def test_availability_builds_the_probe_when_there_is_none(setup, switch):
    switch.modules.model_probe = None

    status = await switch.routes.computer_use_availability()
    await _settled()

    assert isinstance(switch.modules.model_probe, setup.module.AgentModelProbe)
    assert (status["ready"], status["reasons"]) == (False, ["AGENT_NOT_INITIALIZED"])
    assert switch.checks


async def test_switching_the_keyboard_on_with_everything_in_place(setup, switch):
    setup.probe.check_connectivity()

    result = await switch.routes.set_agent_flags({"computer_use_enabled": True, "_persist_intent": False})
    await _settled()

    assert result["agent_flags"]["computer_use_enabled"] is True
    assert switch.modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}
    assert switch.notification() is None and switch.checks == []


async def test_switching_the_keyboard_on_before_the_model_has_answered_switches_on_and_probes_it(setup, switch):
    result = await switch.routes.set_agent_flags({"computer_use_enabled": True, "_persist_intent": False})
    await _settled()

    assert result["agent_flags"]["computer_use_enabled"] is True
    assert switch.notification() == {"code": "AGENT_CU_ENABLED_CHECKING"}
    assert switch.checks == [{}]


async def test_switching_the_keyboard_on_when_the_check_itself_breaks_is_refused(setup, switch, desktop, monkeypatch):
    def broken() -> Any:
        raise RuntimeError("the display went away")

    setup.probe.check_connectivity()
    monkeypatch.setattr(desktop, "available", broken)

    result = await switch.routes.set_agent_flags({"computer_use_enabled": True, "_persist_intent": False})

    assert result["agent_flags"]["computer_use_enabled"] is False
    assert switch.notification() == {"code": "AGENT_CU_ENABLE_FAILED", "details": {"error": "the display went away"}}


async def test_switching_the_keyboard_off_needs_no_check(setup, switch, desktop, monkeypatch):
    def unexpected() -> Any:
        raise AssertionError("input is not looked at to switch off")

    monkeypatch.setattr(desktop, "available", unexpected)
    switch.modules.agent_flags["computer_use_enabled"] = True

    result = await switch.routes.set_agent_flags({"computer_use_enabled": False, "_persist_intent": False})
    await _settled()

    assert result["agent_flags"]["computer_use_enabled"] is False
    assert switch.notification() is None and switch.checks == []


async def test_availability_says_why_the_model_is_sent_no_pictures(setup, switch, monkeypatch, tmp_path):
    from nekoclaw.host import mount
    from nekoclaw.host.model_capabilities import ModelCapabilityStore

    config = setup.probe.model_config()
    store = ModelCapabilityStore(tmp_path / "model_capabilities.json")
    assert await store.note_images_refused(
        config.get("base_url"), config.get("model"), "This model does not support image input."
    )
    monkeypatch.setattr(mount, "peek_host", lambda: SimpleNamespace(model_capabilities=store))
    setup.probe.check_connectivity()

    status = await switch.routes.computer_use_availability()
    await _settled()

    # The tool is switched on and ready, and still not given to a task agent: this says why.
    told = status["model_images"]
    assert (told["sees"], told["source"], told["reason"]) == (False, "refused", "This model does not support image input.")
    assert told["lapses_at"] > told["noted_at"] > 0
