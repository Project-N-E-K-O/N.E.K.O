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

"""Whether the keyboard-and-mouse tools can be used, and what the switch makes of it.

That is what is filled in for the agent model (address, model, key) and
whether input can be sent on this computer. The agent model is never sent
anything to find out whether it answers: a model that does not shows when a
task agent first uses it, and the failure is reported with its reason
(``nekopower.host.task_agents``). Nothing here reaches a network: the
configuration is a fake, and building a chat client is recorded and fails
the test.
"""

from __future__ import annotations

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
GATE_READY = {"ready": True, "reasons": [], "is_free_version": False}


class Desktop:
    """Stands in for ``nekopower.builtin.desktop.Desktop``; pyautogui is never imported."""

    def __init__(self, ready: bool = True, reason: str = "") -> None:
        self.ready, self.reason = ready, reason

    def available(self) -> tuple[bool, str]:
        return self.ready, self.reason


class Setup:
    """The agent model's configuration, the check of it, and the chat clients anything builds."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.config = dict(CONFIGURED)
        self.gate = dict(GATE_READY)
        self.clients: list[dict[str, Any]] = []

        def build(**options: Any) -> Any:
            self.clients.append(options)
            return SimpleNamespace()

        async def build_async(**options: Any) -> Any:
            return build(**options)

        manager = SimpleNamespace(get_model_api_config=self.read)
        monkeypatch.setattr("app.agent_server.get_config_manager", lambda *args, **kwargs: manager)
        monkeypatch.setattr("app.agent_server._check_agent_api_gate", lambda: dict(self.gate))
        monkeypatch.setattr("utils.llm_client.create_chat_llm", build)
        monkeypatch.setattr("utils.llm_client.create_chat_llm_async", build_async)

    def read(self, slot: str) -> dict[str, Any]:
        assert slot == "agent"
        return dict(self.config)


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> Setup:
    return Setup(monkeypatch)


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> Desktop:
    from app.agent_server import nekopower_builtins

    fake = Desktop()
    monkeypatch.setattr(nekopower_builtins.provider, "_desktop", fake)
    return fake


# ---------------------------------------------------------------------------
# Whether the keyboard and mouse can be used: the model is set up and input can be sent
# ---------------------------------------------------------------------------


def test_the_keyboard_and_mouse_are_available_when_the_model_is_set_up_and_input_can_be_sent(setup, desktop):
    from app.agent_server import capabilities

    assert capabilities._keyboard_mouse_availability() == {
        "enabled": True,
        "ready": True,
        "reasons": [],
        "provider": "nekopower",
        "model": "test-model",
    }
    # Nothing was sent to the model to find that out.
    assert setup.clients == []


def test_the_keyboard_and_mouse_are_not_available_when_input_cannot_be_sent(setup, desktop):
    from app.agent_server import capabilities

    desktop.ready, desktop.reason = False, "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"

    status = capabilities._keyboard_mouse_availability()

    assert (status["ready"], status["reasons"]) == (False, ["AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"])


def test_both_reasons_are_given_and_the_models_comes_first(setup, desktop):
    from app.agent_server import capabilities

    setup.gate = {"ready": False, "reasons": ["Agent API Key 未配置或不可用"], "is_free_version": False}
    desktop.ready, desktop.reason = False, "AGENT_PYAUTOGUI_NOT_INSTALLED"

    status = capabilities._keyboard_mouse_availability()

    # The first reason is the one the switch shows.
    assert (status["ready"], status["reasons"]) == (False, ["AGENT_KEY_NOT_CONFIGURED", "AGENT_PYAUTOGUI_NOT_INSTALLED"])


@pytest.mark.parametrize(
    ("said", "code"),
    [
        ("", ""),
        ("AGENT_BROWSER_NOT_FOUND", "AGENT_BROWSER_NOT_FOUND"),
        ("Agent 模型未配置", "AGENT_MODEL_NOT_CONFIGURED"),
        ("Agent API URL 未配置", "AGENT_URL_NOT_CONFIGURED"),
        ("Agent API Key 未配置或不可用", "AGENT_KEY_NOT_CONFIGURED"),
        ("pyautogui not installed", "AGENT_PYAUTOGUI_NOT_INSTALLED"),
        ("no usable browser found", "AGENT_BROWSER_NOT_FOUND"),
        ("未发现可用插件", "AGENT_NO_PLUGINS_FOUND"),
        ("user_plugin server responded 500", "AGENT_PLUGIN_SERVER_ERROR"),
        # The configuration check itself failed: the model counts as not set up.
        ("Agent API check failed: disk gone", "AGENT_ENDPOINT_NOT_CONFIGURED"),
    ],
)
def test_the_reasons_a_capability_is_not_ready_are_said_with_codes_that_have_words(said, code):
    from app.agent_server._shared import _capability_reason_code

    assert _capability_reason_code(said) == code


def test_every_code_a_reason_is_said_with_has_its_words_in_every_language():
    from pathlib import Path

    from app.agent_server import _shared

    codes = {
        _shared._capability_reason_code(said)
        for said in ("Agent 模型未配置", "Agent API URL 未配置", "Agent API Key 未配置或不可用", "check failed")
    }
    for locale in sorted(Path("static/locales").glob("*.json")):
        precheck = json.loads(locale.read_text(encoding="utf-8"))["agent"]["precheck"]
        assert codes <= set(precheck), (locale.name, sorted(codes - set(precheck)))


# ---------------------------------------------------------------------------
# The keyboard-and-mouse switch and GET /computer_use/availability
# ---------------------------------------------------------------------------


@pytest.fixture
def switch(setup, desktop, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The route functions, with the state they touch put back afterwards."""
    from app.agent_server import _shared, api_routes

    modules = _shared.Modules

    async def ignore_status_update(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(api_routes, "_check_agent_api_gate", lambda: dict(GATE_READY))
    # Sending the status would also take the notification the test wants to read.
    monkeypatch.setattr(api_routes, "_emit_agent_status_update", ignore_status_update)
    monkeypatch.setattr(
        modules,
        "agent_flags",
        {"computer_use_enabled": False, "browser_use_enabled": False, "user_plugin_enabled": False},
    )
    monkeypatch.setattr(modules, "capability_cache", {"computer_use": {"ready": False, "reason": ""}})
    monkeypatch.setattr(modules, "notification", None)
    monkeypatch.setattr(modules, "state_revision", modules.state_revision)

    def notification() -> Any:
        return json.loads(modules.notification) if modules.notification else None

    return SimpleNamespace(routes=api_routes, notification=notification, modules=modules)


async def test_availability_of_the_keyboard_and_mouse_with_everything_in_place(setup, switch):
    status = await switch.routes.computer_use_availability()

    assert status == {
        "enabled": True,
        "ready": True,
        "reasons": [],
        "provider": "nekopower",
        "model": "test-model",
        # Nothing is known against the model: it is sent the screenshots the tool works from.
        "model_images": {"sees": True},
    }
    assert switch.modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}
    # Asked on every look at the settings: the model is never sent anything for it.
    assert setup.clients == []


async def test_availability_with_the_agent_model_not_configured_says_only_that(setup, switch, monkeypatch):
    monkeypatch.setattr(
        switch.routes, "_check_agent_api_gate", lambda: {"ready": False, "reasons": ["AGENT_ENDPOINT_NOT_CONFIGURED"]}
    )

    status = await switch.routes.computer_use_availability()

    assert status == {"ready": False, "reasons": ["AGENT_ENDPOINT_NOT_CONFIGURED"]}
    assert setup.clients == []


async def test_availability_switches_the_keyboard_off_when_input_can_no_longer_be_sent(setup, switch, desktop):
    switch.modules.agent_flags["computer_use_enabled"] = True
    desktop.ready, desktop.reason = False, "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"

    status = await switch.routes.computer_use_availability()

    assert status["ready"] is False
    assert switch.modules.agent_flags["computer_use_enabled"] is False
    assert switch.notification() == {
        "code": "AGENT_CU_CAPABILITY_LOST",
        "details": {"reason_code": "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"},
    }


async def test_switching_the_keyboard_on_with_everything_in_place_takes_at_once(setup, switch):
    result = await switch.routes.set_agent_flags({"computer_use_enabled": True, "_persist_intent": False})

    # On at once: there is no check of the model to wait for.
    assert result["agent_flags"]["computer_use_enabled"] is True
    assert switch.modules.capability_cache["computer_use"] == {"ready": True, "reason": ""}
    assert switch.notification() is None and setup.clients == []


async def test_switching_the_keyboard_on_with_a_wrong_key_takes_all_the_same(setup, switch):
    # Whether the key is right only shows when a task agent uses the model:
    # it is reported then, with what the provider said.
    setup.config["api_key"] = "sk-wrong"

    result = await switch.routes.set_agent_flags({"computer_use_enabled": True, "_persist_intent": False})

    assert result["agent_flags"]["computer_use_enabled"] is True
    assert setup.clients == []


async def test_switching_the_keyboard_on_without_input_is_refused_with_the_reason(setup, switch, desktop):
    desktop.ready, desktop.reason = False, "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"

    result = await switch.routes.set_agent_flags({"computer_use_enabled": True, "_persist_intent": False})

    assert result["agent_flags"]["computer_use_enabled"] is False
    assert switch.notification() == {
        "code": "AGENT_CU_UNAVAILABLE",
        "details": {"reason_code": "AGENT_PYAUTOGUI_DISPLAY_UNAVAILABLE"},
    }


async def test_switching_the_keyboard_on_when_the_check_itself_breaks_is_refused(setup, switch, desktop, monkeypatch):
    def broken() -> Any:
        raise RuntimeError("the display went away")

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

    assert result["agent_flags"]["computer_use_enabled"] is False
    assert switch.notification() is None


async def test_availability_says_why_the_model_is_sent_no_pictures(setup, switch, monkeypatch, tmp_path):
    from nekopower.host import mount
    from nekopower.host.model_capabilities import ModelCapabilityStore

    store = ModelCapabilityStore(tmp_path / "model_capabilities.json")
    assert await store.note_images_refused(
        CONFIGURED["base_url"], CONFIGURED["model"], "This model does not support image input."
    )
    monkeypatch.setattr(mount, "peek_host", lambda: SimpleNamespace(model_capabilities=store))

    status = await switch.routes.computer_use_availability()

    # The tool is switched on and ready, and still not given to a task agent: this says why.
    told = status["model_images"]
    assert (told["sees"], told["source"], told["reason"]) == (False, "refused", "This model does not support image input.")
    assert told["lapses_at"] > told["noted_at"] > 0
