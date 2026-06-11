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
