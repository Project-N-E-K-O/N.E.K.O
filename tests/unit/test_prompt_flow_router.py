import asyncio
import importlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import AUTOSTART_CSRF_TOKEN
from utils.autostart_prompt_state import (
    AUTOSTART_MIN_PROMPT_FOREGROUND_MS,
    load_autostart_prompt_state,
)

system_router_module = importlib.import_module("main_routers.system_router.prompt_flows")


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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_serialization_happens_before_an_executor_worker_is_allocated(
    monkeypatch,
):
    """Concurrent state calls must not consume workers while waiting on one RLock."""
    entered = asyncio.Event()
    release = asyncio.Event()
    active = 0
    max_active = 0
    submissions = 0

    async def _fake_to_thread(func, *args, **kwargs):
        nonlocal active, max_active, submissions
        submissions += 1
        active += 1
        max_active = max(max_active, active)
        entered.set()
        await release.wait()
        active -= 1
        return func(*args, **kwargs)

    monkeypatch.setattr(system_router_module.asyncio, "to_thread", _fake_to_thread)
    lock = asyncio.Lock()
    first = asyncio.create_task(
        system_router_module._run_serialized_in_worker(lock, lambda: "first")
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    second = asyncio.create_task(
        system_router_module._run_serialized_in_worker(lock, lambda: "second")
    )
    await asyncio.sleep(0)

    assert submissions == 1, "the waiter reached the executor before its turn"
    release.set()
    assert await asyncio.gather(first, second) == ["first", "second"]
    assert max_active == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_release_the_submit_lock_early(
    monkeypatch,
):
    """Cancellation cannot let a retry allocate a worker behind live work."""
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()
    submissions = 0

    async def _fake_to_thread(func, *args, **kwargs):
        nonlocal submissions
        submissions += 1
        if submissions == 1:
            first_entered.set()
            await release_first.wait()
        else:
            second_entered.set()
        return func(*args, **kwargs)

    monkeypatch.setattr(system_router_module.asyncio, "to_thread", _fake_to_thread)
    lock = asyncio.Lock()
    first = asyncio.create_task(
        system_router_module._run_serialized_in_worker(lock, lambda: "first")
    )
    await asyncio.wait_for(first_entered.wait(), timeout=1)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await first

    second = asyncio.create_task(
        system_router_module._run_serialized_in_worker(lock, lambda: "second")
    )
    await asyncio.sleep(0)
    assert submissions == 1
    assert not second_entered.is_set()

    release_first.set()
    await asyncio.wait_for(second_entered.wait(), timeout=1)
    assert await second == "second"
    assert submissions == 2


@pytest.fixture
def prompt_flow_client(tmp_path, monkeypatch):
    config = DummyConfig(tmp_path)
    monkeypatch.setattr(system_router_module, "get_config_manager", lambda: config)

    app = FastAPI()
    app.include_router(system_router_module.router)

    with TestClient(app) as client:
        client.headers.update({
            "Origin": "http://testserver",
            "X-CSRF-Token": AUTOSTART_CSRF_TOKEN,
        })
        yield client, config


@pytest.mark.unit
def test_yui_guide_handoff_token_is_backend_authoritative_and_single_use(prompt_flow_client):
    client, _config = prompt_flow_client

    created = client.post("/api/yui-guide/handoff/create", json={
        "target_page": "memory_browser",
        "target_path": "/memory_browser",
        "resume_scene": "memory_browser",
        "source_page": "home",
        "source_path": "/",
    })
    assert created.status_code == 200
    token = created.json()["token"]
    assert token["authority"] == "server"
    assert token["signature"]
    assert token["consumed"] is False

    consumed = client.post("/api/yui-guide/handoff/consume", json={
        "token": token["token"],
        "signature": token["signature"],
        "expected_page": "memory_browser",
        "consumer_id": "unit-test-consumer",
    })
    assert consumed.status_code == 200
    consumed_token = consumed.json()["token"]
    assert consumed_token["consumed"] is True
    assert consumed_token["consumed_by"] == "unit-test-consumer"

    replay = client.post("/api/yui-guide/handoff/consume", json={
        "token": token["token"],
        "signature": token["signature"],
        "expected_page": "memory_browser",
        "consumer_id": "unit-test-consumer",
    })
    assert replay.status_code == 409
    assert replay.json()["error_code"] == "handoff_token_consumed"


@pytest.mark.unit
def test_yui_guide_handoff_consume_requires_expected_page(prompt_flow_client):
    client, _config = prompt_flow_client

    created = client.post("/api/yui-guide/handoff/create", json={
        "target_page": "memory_browser",
        "target_path": "/memory_browser",
        "resume_scene": "memory_browser",
        "source_page": "home",
        "source_path": "/",
    })
    assert created.status_code == 200
    token = created.json()["token"]

    consumed = client.post("/api/yui-guide/handoff/consume", json={
        "token": token["token"],
        "signature": token["signature"],
        "consumer_id": "unit-test-consumer",
    })
    assert consumed.status_code == 400
    assert consumed.json()["error_code"] == "invalid_expected_page"


@pytest.mark.unit
def test_autostart_heartbeat_route_returns_prompt_token(prompt_flow_client):
    client, _config = prompt_flow_client

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
def test_autostart_decision_route_persists_completed_state(prompt_flow_client):
    client, config = prompt_flow_client
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
def test_autostart_state_route_reports_autostart_mode(prompt_flow_client):
    client, _config = prompt_flow_client

    response = client.get("/api/autostart-prompt/state")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["prompt_mode"] == "autostart"
    assert response.json()["state"]["autostart_enabled"] is False


@pytest.mark.unit
def test_seven_day_tutorial_put_requires_matching_revision(prompt_flow_client):
    client, _config = prompt_flow_client
    first = client.put("/api/seven-day-tutorial/state", json={
        "expectedRevision": 0,
        "state": {
            "completedRounds": [1],
            "updatedAt": "2026-07-01T00:00:00.000Z",
        },
    })
    assert first.status_code == 200
    assert first.json()["revision"] == 1

    stale = client.put("/api/seven-day-tutorial/state", json={
        "expectedRevision": 0,
        "state": {
            "completedRounds": [],
            "updatedAt": "2026-06-30T00:00:00.000Z",
        },
    })
    assert stale.status_code == 409
    assert stale.json()["error_code"] == "seven_day_tutorial_revision_conflict"
    assert stale.json()["revision"] == 1
    assert stale.json()["state"]["completedRounds"] == [1]


@pytest.mark.unit
def test_seven_day_tutorial_put_rejects_missing_local_mutation_credentials(
    prompt_flow_client,
):
    client, _config = prompt_flow_client
    client.headers.pop("Origin")
    client.headers.pop("X-CSRF-Token")

    response = client.put("/api/seven-day-tutorial/state", json={
        "expectedRevision": 0,
        "state": {"completedRounds": [1]},
    })

    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_waiter_cancelled_before_submission_leaves_the_queue(monkeypatch):
    """Only a running worker is worth keeping; a queued one must not linger."""
    # 一次慢文件操作 + 客户端反复超时重试，会在锁上攒出一条无界队列。那些子任务里
    # 没有任何不可取消的东西 —— 保住它们只会在客户端早就走了之后再做一次陈旧的写，
    # 并且挡住还活着的请求。
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    submissions = 0
    ran = []

    async def _fake_to_thread(func, *args, **kwargs):
        nonlocal submissions
        submissions += 1
        if submissions == 1:
            first_entered.set()
            await release_first.wait()
        return func(*args, **kwargs)

    monkeypatch.setattr(system_router_module.asyncio, "to_thread", _fake_to_thread)
    lock = asyncio.Lock()

    first = asyncio.create_task(
        system_router_module._run_serialized_in_worker(lock, lambda: "first")
    )
    await asyncio.wait_for(first_entered.wait(), timeout=1)

    # 这一个还排在锁上，从没进过 worker。
    queued = asyncio.create_task(
        system_router_module._run_serialized_in_worker(
            lock, lambda: ran.append("queued") or "queued",
        )
    )
    await asyncio.sleep(0)
    assert submissions == 1

    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await queued

    # 放行第一个。被取消的那个绝不该在这之后还去拿锁、占 worker、做一次陈旧的写。
    release_first.set()
    assert await asyncio.wait_for(first, timeout=1) == "first"
    for _ in range(5):
        await asyncio.sleep(0)
    assert submissions == 1, "被取消的等待者事后仍然占用了一个 executor worker"
    assert ran == [], "客户端早就走了，这次陈旧的写还是执行了"
