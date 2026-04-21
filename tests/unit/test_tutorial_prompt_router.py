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
        # Local-mutation guard (introduced by main's autostart-CSRF work) requires
        # both a valid Origin and matching CSRF token on POST endpoints. TestClient
        # does not send Origin by default, so set both for the entire session.
        client.headers.update({
            "Origin": "http://testserver",
            "X-CSRF-Token": system_router_module.AUTOSTART_CSRF_TOKEN,
        })
        yield client, config


@pytest.fixture
def unauthenticated_prompt_client(tmp_path, monkeypatch):
    """专门用来覆盖 CSRF/Origin 守卫负路径的 client，不注入默认 header。"""
    config = DummyConfig(tmp_path)
    monkeypatch.setattr(system_router_module, "get_config_manager", lambda: config)

    app = FastAPI()
    app.include_router(system_router_module.router)

    with TestClient(app) as client:
        yield client, config


@pytest.mark.unit
def test_heartbeat_route_rejects_request_without_csrf_and_origin(unauthenticated_prompt_client):
    """保证 `_validate_local_mutation_request` 守卫在 prompt 写接口上仍然生效 —
    如果未来有人不小心把 CSRF 检查摘掉，默认带 header 的 fixture 不会暴露回归，
    这里用一个独立 client 打一条没 Origin 没 CSRF 的请求做 canary。"""
    client, _config = unauthenticated_prompt_client

    response = client.post("/api/tutorial-prompt/heartbeat", json={})
    assert response.status_code == 403
    body = response.json()
    assert body.get("error_code") == "csrf_validation_failed"


@pytest.mark.unit
def test_heartbeat_route_rejects_request_with_wrong_csrf_token(unauthenticated_prompt_client):
    """Origin 合法但 CSRF token 不匹配：守卫应该仍然拒绝。"""
    client, _config = unauthenticated_prompt_client

    response = client.post(
        "/api/tutorial-prompt/heartbeat",
        json={},
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": "not-the-real-token",
        },
    )
    assert response.status_code == 403
    assert response.json().get("error_code") == "csrf_validation_failed"


@pytest.mark.unit
def test_heartbeat_route_prompts_immediately_on_first_open(tutorial_prompt_client):
    client, _config = tutorial_prompt_client

    response = client.post("/api/tutorial-prompt/heartbeat", json={
        "foreground_ms_delta": 0,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["should_prompt"] is True
    assert body["prompt_reason"] == "first_open"
    assert body["prompt_token"]


@pytest.mark.unit
def test_shown_route_acknowledges_first_display(tutorial_prompt_client):
    client, config = tutorial_prompt_client
    heartbeat = client.post("/api/tutorial-prompt/heartbeat", json={
        "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
    }).json()

    response = client.post("/api/tutorial-prompt/shown", json={
        "prompt_token": heartbeat["prompt_token"],
    })

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
    heartbeat = client.post("/api/tutorial-prompt/heartbeat", json={
        "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
    }).json()
    token = heartbeat["prompt_token"]

    first = client.post("/api/tutorial-prompt/shown", json={"prompt_token": token})
    second = client.post("/api/tutorial-prompt/shown", json={"prompt_token": token})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["already_acknowledged"] is True

    state = load_tutorial_prompt_state(config)
    assert state["shown_count"] == 1


@pytest.mark.unit
def test_decision_route_backfills_missing_shown_ack(tutorial_prompt_client):
    client, config = tutorial_prompt_client
    heartbeat = client.post("/api/tutorial-prompt/heartbeat", json={
        "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
    }).json()

    response = client.post("/api/tutorial-prompt/decision", json={
        "decision": "later",
        "prompt_token": heartbeat["prompt_token"],
    })

    assert response.status_code == 200
    body = response.json()
    assert body["state"]["status"] == "deferred"

    state = load_tutorial_prompt_state(config)
    assert state["shown_count"] == 1
    assert state["active_prompt_token"] == ""


@pytest.mark.unit
def test_later_route_enters_cooldown(tutorial_prompt_client):
    client, _config = tutorial_prompt_client
    heartbeat = client.post("/api/tutorial-prompt/heartbeat", json={
        "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
    }).json()

    decision = client.post("/api/tutorial-prompt/decision", json={
        "decision": "later",
        "prompt_token": heartbeat["prompt_token"],
    })

    assert decision.status_code == 200
    assert decision.json()["state"]["status"] == "deferred"

    blocked = client.post("/api/tutorial-prompt/heartbeat", json={
        "foreground_ms_delta": 0,
    })

    assert blocked.status_code == 200
    assert blocked.json()["should_prompt"] is False
    assert blocked.json()["prompt_reason"] == "cooldown_active"


@pytest.mark.unit
def test_never_route_persists_never_remind_state(tutorial_prompt_client):
    client, _config = tutorial_prompt_client
    heartbeat = client.post("/api/tutorial-prompt/heartbeat", json={
        "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
    }).json()

    decision = client.post("/api/tutorial-prompt/decision", json={
        "decision": "never",
        "prompt_token": heartbeat["prompt_token"],
    })

    assert decision.status_code == 200
    assert decision.json()["state"]["status"] == "never"

    blocked = client.post("/api/tutorial-prompt/heartbeat", json={
        "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
    })

    assert blocked.status_code == 200
    assert blocked.json()["should_prompt"] is False
    assert blocked.json()["prompt_reason"] == "never_remind"


@pytest.mark.unit
def test_prompt_tutorial_started_route_persists_started_state_after_accept_decision(tutorial_prompt_client):
    client, config = tutorial_prompt_client
    heartbeat = client.post("/api/tutorial-prompt/heartbeat", json={
        "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
    }).json()

    decision = client.post("/api/tutorial-prompt/decision", json={
        "decision": "accept",
        "result": "accepted",
        "prompt_token": heartbeat["prompt_token"],
    })

    assert decision.status_code == 200
    assert decision.json()["state"]["started_at"] == 0
    assert decision.json()["state"]["started_via_prompt"] is True

    started = client.post("/api/tutorial-prompt/tutorial-started", json={
        "page": "home",
        "source": "idle_prompt",
        "prompt_token": heartbeat["prompt_token"],
    })

    assert started.status_code == 200
    assert started.json()["state"]["status"] == "started"

    state = load_tutorial_prompt_state(config)
    assert state["started_at"] > 0
    assert state["started_via_prompt"] is True

    blocked = client.post("/api/tutorial-prompt/heartbeat", json={
        "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
    })

    assert blocked.status_code == 200
    assert blocked.json()["should_prompt"] is False
    assert blocked.json()["prompt_reason"] == "tutorial_started"


@pytest.mark.unit
def test_decision_route_requires_prompt_token(tutorial_prompt_client):
    client, _config = tutorial_prompt_client

    response = client.post("/api/tutorial-prompt/decision", json={
        "decision": "accept",
        "result": "accepted",
    })

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "invalid prompt_token" in response.json()["error"]


@pytest.mark.unit
def test_manual_tutorial_started_route_persists_started_state(tutorial_prompt_client):
    client, config = tutorial_prompt_client

    response = client.post("/api/tutorial-prompt/tutorial-started", json={
        "page": "home",
        "source": "manual",
    })

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
    heartbeat = client.post("/api/tutorial-prompt/heartbeat", json={
        "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
    }).json()

    response = client.post("/api/tutorial-prompt/tutorial-started", json={
        "page": "home",
        "source": "idle_prompt",
        "prompt_token": heartbeat["prompt_token"],
    })

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
    started = client.post("/api/tutorial-prompt/tutorial-started", json={
        "page": "home",
        "source": "manual",
    })
    tutorial_run_token = started.json()["tutorial_run_token"]

    response = client.post("/api/tutorial-prompt/tutorial-completed", json={
        "page": "home",
        "source": "manual",
        "tutorial_run_token": tutorial_run_token,
    })

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
    client.post("/api/tutorial-prompt/heartbeat", json={
        "foreground_ms_delta": MIN_PROMPT_FOREGROUND_MS,
    })

    response = client.get("/api/tutorial-prompt/state")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["state"]["home_tutorial_completed"] is False
    assert body["state"]["manual_home_tutorial_viewed"] is False
    assert body["state"]["user_cohort"] == "new"
    assert body["state"]["chat_turns"] == 0
    assert body["state"]["voice_sessions"] == 0
    assert "active_prompt_token" not in body["state"]
    assert "active_prompt_issued_at" not in body["state"]
    assert "last_acknowledged_prompt_token" not in body["state"]
    assert "active_tutorial_run_token" not in body["state"]
    assert "active_tutorial_run_source" not in body["state"]
    assert "active_tutorial_run_started_at" not in body["state"]


@pytest.mark.unit
def test_invalid_prompt_token_returns_400(tutorial_prompt_client):
    client, _config = tutorial_prompt_client

    shown = client.post("/api/tutorial-prompt/shown", json={
        "prompt_token": "not-a-real-token",
    })
    decision = client.post("/api/tutorial-prompt/decision", json={
        "decision": "later",
        "prompt_token": "not-a-real-token",
    })

    assert shown.status_code == 400
    assert shown.json()["ok"] is False
    assert "invalid prompt_token" in shown.json()["error"]

    assert decision.status_code == 400
    assert decision.json()["ok"] is False
    assert "invalid prompt_token" in decision.json()["error"]


@pytest.mark.unit
def test_invalid_tutorial_run_token_returns_400(tutorial_prompt_client):
    client, _config = tutorial_prompt_client
    client.post("/api/tutorial-prompt/tutorial-started", json={
        "page": "home",
        "source": "manual",
    })

    response = client.post("/api/tutorial-prompt/tutorial-completed", json={
        "page": "home",
        "source": "manual",
        "tutorial_run_token": "not-a-real-run-token",
    })

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert "invalid tutorial_run_token" in response.json()["error"]


@pytest.mark.unit
def test_invalid_tutorial_source_returns_400(tutorial_prompt_client):
    client, _config = tutorial_prompt_client

    started = client.post("/api/tutorial-prompt/tutorial-started", json={
        "page": "home",
        "source": "unexpected",
    })

    assert started.status_code == 400
    assert started.json()["ok"] is False
    assert "invalid source" in started.json()["error"]


@pytest.mark.unit
def test_autostart_heartbeat_route_returns_prompt_token(tutorial_prompt_client):
    client, _config = tutorial_prompt_client

    response = client.post("/api/autostart-prompt/heartbeat", json={
        "foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["should_prompt"] is True
    assert body["prompt_mode"] == "autostart"
    assert body["prompt_token"]


@pytest.mark.unit
def test_autostart_decision_route_persists_completed_state(tutorial_prompt_client):
    client, config = tutorial_prompt_client
    heartbeat = client.post("/api/autostart-prompt/heartbeat", json={
        "foreground_ms_delta": AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
    }).json()

    response = client.post("/api/autostart-prompt/decision", json={
        "decision": "accept",
        "result": "enabled",
        "prompt_token": heartbeat["prompt_token"],
    })

    assert response.status_code == 200
    assert response.json()["state"]["status"] == "completed"
    assert response.json()["state"]["autostart_enabled"] is True

    state = load_autostart_prompt_state(config)
    assert state["started_at"] > 0
    assert state["autostart_enabled"] is True
    assert state["started_via_prompt"] is True


@pytest.mark.unit
def test_autostart_state_route_reports_autostart_mode(tutorial_prompt_client):
    client, _config = tutorial_prompt_client

    response = client.get("/api/autostart-prompt/state")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["prompt_mode"] == "autostart"
    assert response.json()["state"]["autostart_enabled"] is False
