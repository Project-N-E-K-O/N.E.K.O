# -*- coding: utf-8 -*-
"""Tests for agent_router's remote-backend mutation block.

When ``NEKO_ACTIVITY_TRACKER_REMOTE`` / ``ACTIVITY_TRACKER_REMOTE`` is set,
agent mutation endpoints (``/flags``, ``/command``, ``/admin/control``,
``/tasks/{task_id}/cancel``) must short-circuit with HTTP 501 rather
than forwarding to a localhost tool_server — in remote deployments the
"computer" that computer_use would control is the *server's*, not the
user's, so silently driving it is actively dangerous.

Parity target: ``main_routers/system_router`` already returns 501 with
the same env override on ``/api/screenshot`` and
``/api/screenshot/interactive``. This file is the agent-router half of
that contract. See PR description + issue #1023 for the threat-model
discussion.

Scope note: a stricter CSRF + Origin guard against DNS-rebinding-style
attacks on local backends is deferred to a follow-up — it needs the
~15 frontend agent fetch sites to start sending ``X-CSRF-Token`` first.
This file covers only the remote-mode block.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import agent_router as agent_router_module


# Endpoints that MUST short-circuit in remote-backend mode. Body shape
# is deliberately minimal — we never reach the parsing path, so anything
# JSON-shaped works. Each entry: (path, body).
_REMOTE_BLOCKED_ENDPOINTS = [
    ("/api/agent/flags", {"flags": {}}),
    ("/api/agent/command", {"command": "set_agent_enabled", "enabled": True}),
    ("/api/agent/admin/control", {}),
    ("/api/agent/tasks/abc-123/cancel", None),
]


def _build_client():
    app = FastAPI()
    app.include_router(agent_router_module.router)
    return TestClient(app)


def _clear_remote_env(monkeypatch):
    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)


@pytest.mark.unit
@pytest.mark.parametrize(
    "env_name", ["NEKO_ACTIVITY_TRACKER_REMOTE", "ACTIVITY_TRACKER_REMOTE"]
)
@pytest.mark.parametrize("env_value", ["1", "true", "TRUE", "yes", "on"])
@pytest.mark.parametrize("path,body", _REMOTE_BLOCKED_ENDPOINTS)
def test_agent_mutation_endpoints_blocked_in_remote_mode(
    monkeypatch, env_name, env_value, path, body
):
    """Setting the remote env var must short-circuit before any work runs."""
    _clear_remote_env(monkeypatch)
    monkeypatch.setenv(env_name, env_value)
    client = _build_client()

    resp = client.post(path, json=body) if body is not None else client.post(path)

    assert resp.status_code == 501, (
        f"{path} did not short-circuit in remote mode "
        f"({env_name}={env_value}): got {resp.status_code} {resp.text!r}"
    )
    payload = resp.json()
    assert payload.get("success") is False
    assert "NEKO_ACTIVITY_TRACKER_REMOTE" in payload.get("error", ""), (
        "error message must name the env var so operators can grep for it"
    )


@pytest.mark.unit
@pytest.mark.parametrize("path,body", _REMOTE_BLOCKED_ENDPOINTS)
def test_agent_mutation_endpoints_not_blocked_when_env_unset(
    monkeypatch, path, body
):
    """Without the env var, the remote-block path must not fire.

    The endpoint may still fail downstream (no shared_state, no
    tool_server) — that's fine; we're only asserting we don't see the
    remote-mode 501 envelope. This guards against the env check
    drifting toward "always on".
    """
    _clear_remote_env(monkeypatch)
    client = _build_client()

    resp = client.post(path, json=body) if body is not None else client.post(path)

    if resp.status_code == 501:
        error = resp.json().get("error", "")
        assert "NEKO_ACTIVITY_TRACKER_REMOTE" not in error, (
            f"{path} short-circuited as remote-blocked even with env unset: "
            f"{resp.text!r}"
        )


@pytest.mark.unit
@pytest.mark.parametrize("env_value", ["", "0", "false", "no", "off", "anything-else"])
def test_remote_backend_block_helper_falsy(monkeypatch, env_value):
    """Falsy/garbage env values must not trip the block helper.

    Mirrors ``test_is_remote_backend_deployment_falsy`` in the
    screenshot-router suite so the two share an invariant: both
    consumers agree on what "remote" means.
    """
    _clear_remote_env(monkeypatch)
    if env_value:
        monkeypatch.setenv("NEKO_ACTIVITY_TRACKER_REMOTE", env_value)
    assert agent_router_module._remote_backend_block() is None


@pytest.mark.unit
def test_remote_backend_block_helper_truthy_returns_501(monkeypatch):
    """When the env is truthy, the helper returns a 501 JSONResponse."""
    _clear_remote_env(monkeypatch)
    monkeypatch.setenv("NEKO_ACTIVITY_TRACKER_REMOTE", "1")
    resp = agent_router_module._remote_backend_block()
    assert resp is not None
    assert resp.status_code == 501


@pytest.mark.unit
def test_remote_block_alias_reuses_system_signals_helper():
    """``agent_router.is_remote_backend_deployment`` is the same callable
    as ``main_logic.activity.system_signals.is_remote_backend_deployment``.

    The whole point of consolidating the env check was to eliminate the
    drifted-duplicate risk between the activity collector and the
    routers. This pins the invariant — if someone shadows the import
    with a local copy, this test fails loudly.
    """
    from main_logic.activity.system_signals import (
        is_remote_backend_deployment as canonical,
    )

    assert agent_router_module.is_remote_backend_deployment is canonical


@pytest.mark.unit
def test_native_capture_preflight_is_local_only_and_reports_helper(monkeypatch):
    client = _build_client()
    monkeypatch.setenv("NEKO_ACTIVITY_TRACKER_REMOTE", "1")
    assert client.get("/api/agent/computer-use/native-capture-available").status_code == 501

    _clear_remote_env(monkeypatch)
    monkeypatch.setattr(agent_router_module, "native_wayland_capture_available", lambda: True)
    response = client.get("/api/agent/computer-use/native-capture-available")
    assert response.status_code == 200
    assert response.json() == {"success": True, "available": True}


@pytest.mark.asyncio
async def test_the_switch_puts_the_plugins_on_for_the_session_as_the_agent_process_does(monkeypatch):
    """The main server's copy of the switches follows the same rule: on brings the plugins on, off takes all off."""
    import httpx

    from main_routers import agent_router

    class Manager:
        def __init__(self) -> None:
            self.agent_flags: dict = {}

        def update_agent_flags(self, flags: dict) -> None:
            self.agent_flags.update(flags)

    class Config:
        def get_character_data(self):
            return (None, "Lan", None, None, None, None, None, None, None)

        def is_agent_free(self) -> bool:
            return False

    manager = Manager()
    sent: list[dict] = []

    def agent(request: httpx.Request) -> httpx.Response:
        import json

        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(agent))
    monkeypatch.setattr(agent_router, "_remote_backend_block", lambda: None)
    monkeypatch.setattr(agent_router, "get_session_manager", lambda: {"Lan": manager})
    monkeypatch.setattr(agent_router, "get_config_manager", lambda: Config())
    monkeypatch.setattr(agent_router, "_get_http_client", lambda: client)

    class Asked:
        def __init__(self, body: dict) -> None:
            self.body = body

        async def json(self) -> dict:
            return self.body

    await agent_router.post_agent_command(Asked({"command": "set_agent_enabled", "enabled": True}))
    assert manager.agent_flags == {"agent_enabled": True, "user_plugin_enabled": True}
    assert sent[-1]["command"] == "set_agent_enabled" and sent[-1]["lanlan_name"] == "Lan"

    await agent_router.post_agent_command(Asked({"command": "set_agent_enabled", "enabled": False}))
    assert manager.agent_flags == {
        "agent_enabled": False, "computer_use_enabled": False, "browser_use_enabled": False, "user_plugin_enabled": False,
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_a_switch_that_never_reached_the_agent_process_leaves_the_session_as_it_was(monkeypatch):
    """The main server writes its copy of the switches before it sends the command on: not sent, they go back."""
    import httpx

    from main_routers import agent_router

    class Manager:
        def __init__(self) -> None:
            self.agent_flags: dict = {"agent_enabled": False, "user_plugin_enabled": False}

        def update_agent_flags(self, flags: dict) -> None:
            self.agent_flags.update(flags)

    class Config:
        def get_character_data(self):
            return (None, "Lan", None, None, None, None, None, None, None)

        def is_agent_free(self) -> bool:
            return False

    manager = Manager()

    def agent(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(agent))
    monkeypatch.setattr(agent_router, "_remote_backend_block", lambda: None)
    monkeypatch.setattr(agent_router, "get_session_manager", lambda: {"Lan": manager})
    monkeypatch.setattr(agent_router, "get_config_manager", lambda: Config())
    monkeypatch.setattr(agent_router, "_get_http_client", lambda: client)

    class Asked:
        async def json(self) -> dict:
            return {"command": "set_agent_enabled", "enabled": True}

    answer = await agent_router.post_agent_command(Asked())
    assert answer.status_code == 502
    assert manager.agent_flags == {"agent_enabled": False, "user_plugin_enabled": False}


@pytest.mark.asyncio
async def test_taking_a_switch_back_leaves_what_another_request_wrote_meanwhile(monkeypatch):
    """Only what this request wrote goes back, and only where it still says so."""
    import httpx

    from main_routers import agent_router

    class Manager:
        def __init__(self) -> None:
            self.agent_flags: dict = {"agent_enabled": True, "browser_use_enabled": False, "computer_use_enabled": False}

        def update_agent_flags(self, flags: dict) -> None:
            self.agent_flags.update(flags)

    class Config:
        def get_character_data(self):
            return (None, "Lan", None, None, None, None, None, None, None)

        def is_agent_free(self) -> bool:
            return False

    manager = Manager()

    def agent(request: httpx.Request) -> httpx.Response:
        # While this one is on its way, another request switched the keyboard and mouse on, and was taken.
        manager.update_agent_flags({"computer_use_enabled": True})
        return httpx.Response(500, json={"success": False})

    client = httpx.AsyncClient(transport=httpx.MockTransport(agent))
    monkeypatch.setattr(agent_router, "_remote_backend_block", lambda: None)
    monkeypatch.setattr(agent_router, "get_session_manager", lambda: {"Lan": manager})
    monkeypatch.setattr(agent_router, "get_config_manager", lambda: Config())
    monkeypatch.setattr(agent_router, "_get_http_client", lambda: client)

    class Asked:
        async def json(self) -> dict:
            return {"command": "set_flag", "key": "browser_use_enabled", "value": True}

    answer = await agent_router.post_agent_command(Asked())
    assert answer.status_code == 502
    assert manager.agent_flags == {"agent_enabled": True, "browser_use_enabled": False, "computer_use_enabled": True}


@pytest.mark.asyncio
async def test_two_switches_that_never_reached_the_agent_process_leave_the_session_as_it_was(monkeypatch):
    """One command at a time: the second does not take the first one's unconfirmed value for what was there."""
    import asyncio

    import httpx

    from main_routers import agent_router

    class Manager:
        def __init__(self) -> None:
            self.agent_flags: dict = {"agent_enabled": True, "browser_use_enabled": False}

        def update_agent_flags(self, flags: dict) -> None:
            self.agent_flags.update(flags)

    class Config:
        def get_character_data(self):
            return (None, "Lan", None, None, None, None, None, None, None)

        def is_agent_free(self) -> bool:
            return False

    manager = Manager()

    async def agent(request: httpx.Request) -> httpx.Response:
        # Not reached; the other request gets its turn meanwhile.
        await asyncio.sleep(0.02)
        raise httpx.ConnectError("refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(agent))
    monkeypatch.setattr(agent_router, "_remote_backend_block", lambda: None)
    monkeypatch.setattr(agent_router, "get_session_manager", lambda: {"Lan": manager})
    monkeypatch.setattr(agent_router, "get_config_manager", lambda: Config())
    monkeypatch.setattr(agent_router, "_get_http_client", lambda: client)

    class Asked:
        def __init__(self, value: bool) -> None:
            self.value = value

        async def json(self) -> dict:
            return {"command": "set_flag", "key": "browser_use_enabled", "value": self.value}

    on, off = await asyncio.gather(
        agent_router.post_agent_command(Asked(True)), agent_router.post_agent_command(Asked(False)),
    )
    assert on.status_code == 502 and off.status_code == 502
    assert manager.agent_flags == {"agent_enabled": True, "browser_use_enabled": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("fails_first", [False, True])
async def test_a_switch_another_command_was_answered_for_is_not_taken_back_by_one_that_failed(monkeypatch, fails_first):
    """Two commands write the same value; one is taken, the other is not, in either order: it stays."""
    import asyncio

    import httpx

    from main_routers import agent_router

    class Manager:
        def __init__(self) -> None:
            self.agent_flags: dict = {"agent_enabled": True, "browser_use_enabled": False}

        def update_agent_flags(self, flags: dict) -> None:
            self.agent_flags.update(flags)

    class Config:
        def get_character_data(self):
            return (None, "Lan", None, None, None, None, None, None, None)

        def is_agent_free(self) -> bool:
            return False

    manager = Manager()
    calls = 0

    async def agent(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        mine = calls
        # Both have written by the time either is answered; the first one is not taken, the second one is.
        await asyncio.sleep(0.05 if (mine == 1) != fails_first else 0.01)
        if mine == 1:
            return httpx.Response(500, json={"success": False})
        return httpx.Response(200, json={"success": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(agent))
    monkeypatch.setattr(agent_router, "_remote_backend_block", lambda: None)
    monkeypatch.setattr(agent_router, "get_session_manager", lambda: {"Lan": manager})
    monkeypatch.setattr(agent_router, "get_config_manager", lambda: Config())
    monkeypatch.setattr(agent_router, "_get_http_client", lambda: client)

    class Asked:
        async def json(self) -> dict:
            return {"command": "set_flag", "key": "browser_use_enabled", "value": True}

    first, second = await asyncio.gather(
        agent_router.post_agent_command(Asked()), agent_router.post_agent_command(Asked()),
    )
    assert first.status_code == 502 and second["success"] is True
    assert manager.agent_flags == {"agent_enabled": True, "browser_use_enabled": True}
    # Nothing is kept of commands that are over.
    assert agent_router._unconfirmed._switches == {}


@pytest.mark.asyncio
async def test_a_command_that_failed_does_not_undo_what_the_flags_route_wrote_meanwhile(monkeypatch):
    """The browser was on; a command to switch it off is under way; the flags route switches it off and is taken."""
    import asyncio

    import httpx

    from main_routers import agent_router

    class Manager:
        def __init__(self) -> None:
            self.agent_flags: dict = {"agent_enabled": True, "browser_use_enabled": True}

        def update_agent_flags(self, flags: dict) -> None:
            self.agent_flags.update(flags)

    class Config:
        def get_character_data(self):
            return (None, "Lan", None, None, None, None, None, None, None)

        async def aget_character_data(self):
            return self.get_character_data()

        def is_agent_free(self) -> bool:
            return False

    manager = Manager()

    async def agent(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/agent/command"):
            await asyncio.sleep(0.05)
            return httpx.Response(500, json={"success": False})
        return httpx.Response(200, json={"success": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(agent))
    monkeypatch.setattr(agent_router, "_remote_backend_block", lambda: None)
    monkeypatch.setattr(agent_router, "get_session_manager", lambda: {"Lan": manager})
    monkeypatch.setattr(agent_router, "get_config_manager", lambda: Config())
    monkeypatch.setattr(agent_router, "_get_http_client", lambda: client)

    class Asked:
        def __init__(self, body: dict) -> None:
            self.body = body

        async def json(self) -> dict:
            return self.body

    async def by_flags() -> object:
        await asyncio.sleep(0.01)
        return await agent_router.update_agent_flags(Asked({"flags": {"browser_use_enabled": False}}))

    command, flags = await asyncio.gather(
        agent_router.post_agent_command(Asked({"command": "set_flag", "key": "browser_use_enabled", "value": False})),
        by_flags(),
    )
    assert command.status_code == 502 and flags["success"] is True
    assert manager.agent_flags["browser_use_enabled"] is False
    assert agent_router._unconfirmed._switches == {}


def _router_under_test(monkeypatch, manager, agent):
    """Put the command route on a stand-in agent process and one session manager; returns the module."""
    import httpx

    from main_routers import agent_router

    class Config:
        def get_character_data(self):
            return (None, "Lan", None, None, None, None, None, None, None)

        def is_agent_free(self) -> bool:
            return False

    client = httpx.AsyncClient(transport=httpx.MockTransport(agent))
    monkeypatch.setattr(agent_router, "_remote_backend_block", lambda: None)
    monkeypatch.setattr(agent_router, "get_session_manager", lambda: {"Lan": manager})
    monkeypatch.setattr(agent_router, "get_config_manager", lambda: Config())
    monkeypatch.setattr(agent_router, "_get_http_client", lambda: client)
    return agent_router


class _Switches:
    def __init__(self, **flags: bool) -> None:
        self.agent_flags: dict = dict(flags)

    def update_agent_flags(self, flags: dict) -> None:
        self.agent_flags.update(flags)


class _Flag:
    def __init__(self, value: bool) -> None:
        self.value = value

    async def json(self) -> dict:
        return {"command": "set_flag", "key": "browser_use_enabled", "value": self.value}


@pytest.mark.asyncio
async def test_a_command_that_failed_while_another_was_under_way_leaves_the_switch_to_the_other(monkeypatch):
    """On is asked for, then off; off is not taken first, on is taken after it: the switch is on."""
    import asyncio

    import httpx

    manager = _Switches(agent_enabled=True, browser_use_enabled=False)

    async def agent(request: httpx.Request) -> httpx.Response:
        import json

        if json.loads(request.content)["value"] is True:
            await asyncio.sleep(0.05)
            return httpx.Response(200, json={"success": True})
        await asyncio.sleep(0.01)
        raise httpx.ConnectError("refused", request=request)

    router = _router_under_test(monkeypatch, manager, agent)
    on, off = await asyncio.gather(router.post_agent_command(_Flag(True)), router.post_agent_command(_Flag(False)))
    assert on["success"] is True and off.status_code == 502
    assert manager.agent_flags["browser_use_enabled"] is True
    assert router._unconfirmed._switches == {}


@pytest.mark.asyncio
async def test_a_command_whose_answer_never_came_does_not_stand_in_the_way_of_the_next(monkeypatch):
    """On times out (it may have been taken, and stays); off is then refused a connection and goes back to on."""
    import httpx

    manager = _Switches(agent_enabled=True, browser_use_enabled=False)
    answers = [httpx.ReadTimeout("no answer"), httpx.ConnectError("refused")]

    def agent(request: httpx.Request) -> httpx.Response:
        raise answers.pop(0)

    router = _router_under_test(monkeypatch, manager, agent)
    unanswered = await router.post_agent_command(_Flag(True))
    assert unanswered.status_code == 502
    assert manager.agent_flags["browser_use_enabled"] is True
    assert router._unconfirmed._switches == {}

    refused = await router.post_agent_command(_Flag(False))
    assert refused.status_code == 502
    assert manager.agent_flags["browser_use_enabled"] is True
    assert router._unconfirmed._switches == {}


class _Asked:
    def __init__(self, body: dict) -> None:
        self.body = body

    async def json(self) -> dict:
        return self.body


@pytest.mark.asyncio
@pytest.mark.parametrize("refused_after", [0.04, 0.12])
async def test_a_switch_says_what_was_written_last_among_the_writes_that_did_not_fail(monkeypatch, refused_after):
    """Three at once: on is under way, off is taken, another off is not taken, before or after on is answered: off stands."""
    import asyncio
    import json

    import httpx

    manager = _Switches(agent_enabled=True, browser_use_enabled=False)

    async def agent(request: httpx.Request) -> httpx.Response:
        asked = json.loads(request.content)
        if asked["value"] is True:
            # The first: taken, and answered last.
            await asyncio.sleep(0.08)
            return httpx.Response(200, json={"success": True})
        if asked.get("request_id") == "taken":
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"success": True})
        await asyncio.sleep(refused_after)
        raise httpx.ConnectError("refused", request=request)

    router = _router_under_test(monkeypatch, manager, agent)

    def flag(value: bool, request_id: str) -> _Asked:
        return _Asked({"command": "set_flag", "key": "browser_use_enabled", "value": value, "request_id": request_id})

    on, off, refused = await asyncio.gather(
        router.post_agent_command(flag(True, "first")),
        router.post_agent_command(flag(False, "taken")),
        router.post_agent_command(flag(False, "refused")),
    )
    assert on["success"] is True and off["success"] is True and refused.status_code == 502
    assert manager.agent_flags["browser_use_enabled"] is False
    assert router._unconfirmed._switches == {}


@pytest.mark.asyncio
async def test_what_the_flags_route_wrote_stands_over_commands_that_were_under_way_before_it(monkeypatch):
    """On and off are under way, the flags route writes off and is taken, the off command is refused: off stands."""
    import asyncio
    import json

    import httpx

    manager = _Switches(agent_enabled=True, browser_use_enabled=False)

    async def agent(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/agent/flags"):
            return httpx.Response(200, json={"success": True})
        if json.loads(request.content)["value"] is True:
            await asyncio.sleep(0.08)
            return httpx.Response(200, json={"success": True})
        await asyncio.sleep(0.04)
        raise httpx.ConnectError("refused", request=request)

    router = _router_under_test(monkeypatch, manager, agent)

    class Config:
        def get_character_data(self):
            return (None, "Lan", None, None, None, None, None, None, None)

        async def aget_character_data(self):
            return self.get_character_data()

        def is_agent_free(self) -> bool:
            return False

    monkeypatch.setattr(router, "get_config_manager", lambda: Config())

    async def by_flags() -> object:
        await asyncio.sleep(0.01)
        return await router.update_agent_flags(_Asked({"flags": {"browser_use_enabled": False}}))

    on, refused, flags = await asyncio.gather(
        router.post_agent_command(_Flag(True)), router.post_agent_command(_Flag(False)), by_flags(),
    )
    assert on["success"] is True and refused.status_code == 502 and flags["success"] is True
    assert manager.agent_flags["browser_use_enabled"] is False
    assert router._unconfirmed._switches == {}
