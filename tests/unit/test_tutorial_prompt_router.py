import importlib
import pytest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from utils.autostart_prompt_state import (
    AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
    load_autostart_prompt_state,
)
from utils.tutorial_prompt_state import (
    MIN_PROMPT_FOREGROUND_MS,
    load_tutorial_prompt_state,
)

system_router_module = importlib.import_module("main_routers.system_router")
_DEFAULT_AUTOSTART_TOKEN = object()


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Override the repo-level autouse fixture: router tests do not need it."""
    yield


class DummyConfig:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.config_dir = self.root / "config"
        self.memory_dir = self.root / "memory"
        self.chara_dir = self.root / "character_cards"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.chara_dir.mkdir(parents=True, exist_ok=True)

    def get_config_path(self, filename):
        return self.config_dir / filename


@pytest.fixture
def tutorial_prompt_client(tmp_path, monkeypatch):
    config = DummyConfig(tmp_path)
    monkeypatch.setattr(system_router_module, "get_config_manager", lambda: config)

    app = FastAPI()
    app.include_router(system_router_module.router)

    with TestClient(app) as client:
        yield client, config


def _mutation_headers(*, origin: str | None = "http://testserver", referer: str | None = None, token=_DEFAULT_AUTOSTART_TOKEN):
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    if referer is not None:
        headers["Referer"] = referer
    if token is _DEFAULT_AUTOSTART_TOKEN:
        token = system_router_module.AUTOSTART_CSRF_TOKEN
    if token is not None:
        headers["X-CSRF-Token"] = token
    return headers


_autostart_mutation_headers = _mutation_headers


@pytest.mark.unit
def test_heartbeat_route_returns_prompt_token(tutorial_prompt_client):
    client, _config = tutorial_prompt_client

    response = client.post(
        "/api/tutorial-prompt/heartbeat",
        headers=_mutation_headers(),
        json={
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["should_prompt"] is True
    assert body["prompt_token"]


@pytest.mark.unit
def test_shown_route_acknowledges_first_display(tutorial_prompt_client):
    client, config = tutorial_prompt_client
    heartbeat = client.post(
        "/api/tutorial-prompt/heartbeat",
        headers=_mutation_headers(),
        json={
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
        },
    ).json()

    response = client.post(
        "/api/tutorial-prompt/shown",
        headers=_mutation_headers(),
        json={
            "prompt_token": heartbeat["prompt_token"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["already_acknowledged"] is False

    state = load_tutorial_prompt_state(config)
    assert state["shown_count"] == 1
    assert state["status"] == "prompted"


@pytest.mark.unit
def test_shown_route_is_idempotent_for_repeated_ack(tutorial_prompt_client):
    client, config = tutorial_prompt_client
    heartbeat = client.post(
        "/api/tutorial-prompt/heartbeat",
        headers=_mutation_headers(),
        json={
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
        },
    ).json()
    token = heartbeat["prompt_token"]

    first = client.post(
        "/api/tutorial-prompt/shown",
        headers=_mutation_headers(),
        json={"prompt_token": token},
    )
    second = client.post(
        "/api/tutorial-prompt/shown",
        headers=_mutation_headers(),
        json={"prompt_token": token},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["already_acknowledged"] is True

    state = load_tutorial_prompt_state(config)
    assert state["shown_count"] == 1


@pytest.mark.unit
def test_decision_route_backfills_missing_shown_ack(tutorial_prompt_client):
    client, config = tutorial_prompt_client
    heartbeat = client.post(
        "/api/tutorial-prompt/heartbeat",
        headers=_mutation_headers(),
        json={
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
        },
    ).json()

    response = client.post(
        "/api/tutorial-prompt/decision",
        headers=_mutation_headers(),
        json={
            "decision": "later",
            "prompt_token": heartbeat["prompt_token"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"]["status"] == "deferred"

    state = load_tutorial_prompt_state(config)
    assert state["shown_count"] == 1
    assert state["active_prompt_token"] == ""


@pytest.mark.unit
def test_later_route_enters_cooldown(tutorial_prompt_client):
    client, _config = tutorial_prompt_client
    heartbeat = client.post(
        "/api/tutorial-prompt/heartbeat",
        headers=_mutation_headers(),
        json={
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
        },
    ).json()

    decision = client.post(
        "/api/tutorial-prompt/decision",
        headers=_mutation_headers(),
        json={
            "decision": "later",
            "prompt_token": heartbeat["prompt_token"],
        },
    )

    assert decision.status_code == 200
    assert decision.json()["state"]["status"] == "deferred"

    blocked = client.post(
        "/api/tutorial-prompt/heartbeat",
        headers=_mutation_headers(),
        json={
            "foreground_ms_delta": 0,
        },
    )

    assert blocked.status_code == 200
    assert blocked.json()["should_prompt"] is False
    assert blocked.json()["prompt_reason"] == "cooldown_active"


@pytest.mark.unit
def test_never_route_persists_never_remind_state(tutorial_prompt_client):
    client, _config = tutorial_prompt_client
    heartbeat = client.post(
        "/api/tutorial-prompt/heartbeat",
        headers=_mutation_headers(),
        json={
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
        },
    ).json()

    decision = client.post(
        "/api/tutorial-prompt/decision",
        headers=_mutation_headers(),
        json={
            "decision": "never",
            "prompt_token": heartbeat["prompt_token"],
        },
    )

    assert decision.status_code == 200
    assert decision.json()["state"]["status"] == "never"

    blocked = client.post(
        "/api/tutorial-prompt/heartbeat",
        headers=_mutation_headers(),
        json={
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
        },
    )

    assert blocked.status_code == 200
    assert blocked.json()["should_prompt"] is False
    assert blocked.json()["prompt_reason"] == "never_remind"


@pytest.mark.unit
def test_accept_started_route_persists_started_state(tutorial_prompt_client):
    client, config = tutorial_prompt_client
    heartbeat = client.post(
        "/api/tutorial-prompt/heartbeat",
        headers=_mutation_headers(),
        json={
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
        },
    ).json()

    decision = client.post(
        "/api/tutorial-prompt/decision",
        headers=_mutation_headers(),
        json={
            "decision": "accept",
            "result": "started",
            "prompt_token": heartbeat["prompt_token"],
        },
    )

    assert decision.status_code == 200
    assert decision.json()["state"]["status"] == "started"

    state = load_tutorial_prompt_state(config)
    assert state["started_at"] > 0
    assert state["started_via_prompt"] is True

    blocked = client.post(
        "/api/tutorial-prompt/heartbeat",
        headers=_mutation_headers(),
        json={
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
        },
    )

    assert blocked.status_code == 200
    assert blocked.json()["should_prompt"] is False
    assert blocked.json()["prompt_reason"] == "tutorial_started"


@pytest.mark.unit
def test_decision_route_requires_prompt_token(tutorial_prompt_client):
    client, _config = tutorial_prompt_client

    response = client.post(
        "/api/tutorial-prompt/decision",
        headers=_mutation_headers(),
        json={
            "decision": "accept",
            "result": "accepted",
        },
    )

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "invalid prompt_token" in response.json()["error"]


@pytest.mark.unit
def test_manual_tutorial_started_route_persists_started_state(tutorial_prompt_client):
    client, config = tutorial_prompt_client

    response = client.post(
        "/api/tutorial-prompt/tutorial-started",
        headers=_mutation_headers(),
        json={
            "page": "home",
            "source": "manual",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["ignored"] is False
    assert body["state"]["status"] == "started"
    assert body["tutorial_run_token"]

    state = load_tutorial_prompt_state(config)
    assert state["started_at"] > 0
    assert state["manual_home_tutorial_viewed"] is True
    assert state["active_tutorial_run_token"] == body["tutorial_run_token"]


@pytest.mark.unit
def test_prompt_tutorial_started_route_requires_valid_prompt_token(tutorial_prompt_client):
    client, config = tutorial_prompt_client
    heartbeat = client.post(
        "/api/tutorial-prompt/heartbeat",
        headers=_mutation_headers(),
        json={
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
        },
    ).json()

    response = client.post(
        "/api/tutorial-prompt/tutorial-started",
        headers=_mutation_headers(),
        json={
            "page": "home",
            "source": "idle_prompt",
            "prompt_token": heartbeat["prompt_token"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"]["status"] == "started"
    assert body["tutorial_run_token"]

    state = load_tutorial_prompt_state(config)
    assert state["accepted_at"] > 0
    assert state["started_via_prompt"] is True
    assert state["active_tutorial_run_token"] == body["tutorial_run_token"]


@pytest.mark.unit
def test_tutorial_completed_route_persists_completion_state(tutorial_prompt_client):
    client, config = tutorial_prompt_client
    started = client.post(
        "/api/tutorial-prompt/tutorial-started",
        headers=_mutation_headers(),
        json={
            "page": "home",
            "source": "manual",
        },
    )
    tutorial_run_token = started.json()["tutorial_run_token"]

    response = client.post(
        "/api/tutorial-prompt/tutorial-completed",
        headers=_mutation_headers(),
        json={
            "page": "home",
            "source": "manual",
            "tutorial_run_token": tutorial_run_token,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["ignored"] is False
    assert body["state"]["status"] == "completed"

    state = load_tutorial_prompt_state(config)
    assert state["completed_at"] > 0
    assert state["home_tutorial_completed"] is True
    assert state["active_tutorial_run_token"] == ""


@pytest.mark.unit
def test_state_route_hides_internal_prompt_tokens(tutorial_prompt_client):
    client, _config = tutorial_prompt_client
    client.post(
        "/api/tutorial-prompt/heartbeat",
        headers=_mutation_headers(),
        json={
            "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
        },
    )

    response = client.get("/api/tutorial-prompt/state")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "active_prompt_token" not in body["state"]
    assert "active_prompt_issued_at" not in body["state"]
    assert "last_acknowledged_prompt_token" not in body["state"]
    assert "active_tutorial_run_token" not in body["state"]
    assert "active_tutorial_run_source" not in body["state"]
    assert "active_tutorial_run_started_at" not in body["state"]


@pytest.mark.unit
def test_invalid_prompt_token_returns_400(tutorial_prompt_client):
    client, _config = tutorial_prompt_client

    shown = client.post(
        "/api/tutorial-prompt/shown",
        headers=_mutation_headers(),
        json={
            "prompt_token": "not-a-real-token",
        },
    )
    decision = client.post(
        "/api/tutorial-prompt/decision",
        headers=_mutation_headers(),
        json={
            "decision": "later",
            "prompt_token": "not-a-real-token",
        },
    )

    assert shown.status_code == 400
    assert shown.json()["ok"] is False
    assert "invalid prompt_token" in shown.json()["error"]

    assert decision.status_code == 400
    assert decision.json()["ok"] is False
    assert "invalid prompt_token" in decision.json()["error"]


@pytest.mark.unit
def test_invalid_tutorial_run_token_returns_400(tutorial_prompt_client):
    client, _config = tutorial_prompt_client
    client.post(
        "/api/tutorial-prompt/tutorial-started",
        headers=_mutation_headers(),
        json={
            "page": "home",
            "source": "manual",
        },
    )

    response = client.post(
        "/api/tutorial-prompt/tutorial-completed",
        headers=_mutation_headers(),
        json={
            "page": "home",
            "source": "manual",
            "tutorial_run_token": "not-a-real-run-token",
        },
    )

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "invalid tutorial_run_token" in response.json()["error"]


@pytest.mark.unit
def test_invalid_tutorial_source_returns_400(tutorial_prompt_client):
    client, _config = tutorial_prompt_client

    started = client.post(
        "/api/tutorial-prompt/tutorial-started",
        headers=_mutation_headers(),
        json={
            "page": "home",
            "source": "unexpected",
        },
    )

    assert started.status_code == 400
    assert started.json()["ok"] is False
    assert "invalid source" in started.json()["error"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/tutorial-prompt/heartbeat", {"foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS}),
        ("/api/tutorial-prompt/shown", {"prompt_token": "not-a-real-token"}),
        ("/api/tutorial-prompt/decision", {"decision": "later", "prompt_token": "not-a-real-token"}),
        ("/api/tutorial-prompt/tutorial-started", {"page": "home", "source": "manual"}),
        (
            "/api/tutorial-prompt/tutorial-completed",
            {"page": "home", "source": "manual", "tutorial_run_token": "not-a-real-run-token"},
        ),
    ],
)
def test_tutorial_mutation_routes_require_csrf_headers(tutorial_prompt_client, path, payload):
    client, _config = tutorial_prompt_client

    response = client.post(path, json=payload)

    assert response.status_code == 403
    assert response.json()["ok"] is False
    assert response.json()["error_code"] == "csrf_validation_failed"


@pytest.mark.unit
def test_tutorial_started_route_without_csrf_does_not_persist_state(tutorial_prompt_client):
    client, config = tutorial_prompt_client

    response = client.post("/api/tutorial-prompt/tutorial-started")

    assert response.status_code == 403
    assert response.json()["ok"] is False
    assert response.json()["error_code"] == "csrf_validation_failed"

    state = load_tutorial_prompt_state(config)
    assert state["started_at"] == 0
    assert state["status"] == "observing"


@pytest.mark.unit
def test_autostart_heartbeat_route_returns_prompt_token(tutorial_prompt_client):
    client, _config = tutorial_prompt_client

    response = client.post(
        "/api/autostart-prompt/heartbeat",
        headers=_autostart_mutation_headers(),
        json={
            "foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["should_prompt"] is True
    assert body["prompt_mode"] == "autostart"
    assert body["prompt_token"]


@pytest.mark.unit
def test_autostart_heartbeat_route_requires_csrf_headers(tutorial_prompt_client):
    client, _config = tutorial_prompt_client

    response = client.post("/api/autostart-prompt/heartbeat", json={
        "foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
    })

    assert response.status_code == 403
    assert response.json()["ok"] is False
    assert response.json()["error_code"] == "csrf_validation_failed"


@pytest.mark.unit
def test_autostart_heartbeat_accepts_normalized_allowed_origins(tutorial_prompt_client, monkeypatch):
    client, _config = tutorial_prompt_client
    monkeypatch.setattr(
        system_router_module,
        "AUTOSTART_ALLOWED_ORIGINS",
        ("HTTPS://LOCALHOST:48911/settings/", "", None),
    )

    response = client.post(
        "/api/autostart-prompt/heartbeat",
        headers=_autostart_mutation_headers(origin="https://localhost:48911"),
        json={
            "foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.unit
def test_autostart_decision_route_persists_completed_state(tutorial_prompt_client):
    client, config = tutorial_prompt_client
    heartbeat = client.post(
        "/api/autostart-prompt/heartbeat",
        headers=_autostart_mutation_headers(),
        json={
            "foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
        },
    ).json()

    response = client.post(
        "/api/autostart-prompt/decision",
        headers=_autostart_mutation_headers(),
        json={
            "decision": "accept",
            "result": "enabled",
            "prompt_token": heartbeat["prompt_token"],
        },
    )

    assert response.status_code == 200
    assert response.json()["state"]["status"] == "completed"
    assert response.json()["state"]["autostart_enabled"] is True

    state = load_autostart_prompt_state(config)
    assert state["started_at"] > 0
    assert state["autostart_enabled"] is True
    assert state["started_via_prompt"] is True


@pytest.mark.unit
def test_autostart_decision_route_treats_pending_approval_as_started(tutorial_prompt_client):
    client, config = tutorial_prompt_client
    heartbeat = client.post(
        "/api/autostart-prompt/heartbeat",
        headers=_autostart_mutation_headers(),
        json={
            "foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
        },
    ).json()

    response = client.post(
        "/api/autostart-prompt/decision",
        headers=_autostart_mutation_headers(),
        json={
            "decision": "accept",
            "result": "approval_pending",
            "prompt_token": heartbeat["prompt_token"],
        },
    )

    assert response.status_code == 200
    assert response.json()["state"]["status"] == "started"
    assert response.json()["state"]["autostart_enabled"] is False

    state = load_autostart_prompt_state(config)
    assert state["started_at"] > 0
    assert state["started_via_prompt"] is True
    assert state["autostart_enabled"] is False
    assert state["funnel_counts"]["failed"] == 0


@pytest.mark.unit
def test_autostart_state_route_reports_autostart_mode(tutorial_prompt_client):
    client, _config = tutorial_prompt_client

    response = client.get("/api/autostart-prompt/state")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["prompt_mode"] == "autostart"
    assert response.json()["state"]["autostart_enabled"] is False
