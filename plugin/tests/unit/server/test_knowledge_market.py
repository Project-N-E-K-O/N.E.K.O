"""``/market/knowledge/*`` subscriptions: the plugin server's market worker.

Main's knowledge proxy and the market API are replaced by fakes; the tests pin
the bounds kept from the #2951 review: download slots vs. tracked workers,
registry reconciliation of pruned jobs, busy answers that stay retryable, and
the short / backed-off polling of an unsubscribe whose outcome is unknown.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from plugin.server.routes import knowledge_market as km
from plugin.server.routes.market_bridge import get_bridge_token


pytestmark = pytest.mark.plugin_unit

ARTIFACT = b'{"schema_version":1}'
SHA = hashlib.sha256(ARTIFACT).hexdigest()


def _descriptor(package_id: int = 42, pack_id: str = "market-memes", version: str = "1.0.0"):
    return km.KnowledgeVersionDescriptor.model_validate(
        {
            "protocol_version": 1,
            "package_id": package_id,
            "remote_id": f"knowledge/{pack_id}",
            "pack_id": pack_id,
            "material_type": "knowledge",
            "version": version,
            "channel": "stable",
            "artifacts": {
                "knowledge": {
                    "url": f"https://github.com/o/r/releases/download/v1/{pack_id}.neko-knowledge.json",
                    "sha256": SHA,
                    "bytes": len(ARTIFACT),
                },
                # v1 ignores prebuilt index artifacts a descriptor may list.
                "index_manifest": {"url": "https://github.com/x.json", "sha256": SHA, "bytes": 1},
            },
        }
    )


def _installed(pack_id: str = "market-memes", package_id: str = "42", sha: str = SHA) -> dict:
    return {
        "pack_id": pack_id,
        "subscription": {
            "provider": "plugin-market",
            "provider_package_id": package_id,
            "remote_id": f"knowledge/{pack_id}",
            "pack_id": pack_id,
            "version": "1.0.0",
            "channel": "stable",
            "artifact_sha256": sha,
            "material_type": "knowledge",
        },
    }


class FakeMain:
    """Main's /api/public-knowledge proxy: per (method, path) replies, in order."""

    def __init__(self) -> None:
        self.replies: dict[tuple[str, str], list[Any]] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def on(self, method: str, path: str, *replies: Any) -> None:
        self.replies[(method, path)] = list(replies)

    async def __call__(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((method, path, kwargs))
        queue = self.replies.get((method, path))
        if not queue:
            raise AssertionError(f"unexpected Main call {method} {path}")
        reply = queue[0] if len(queue) == 1 else queue.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            reply = await reply()
        return reply

    def paths(self) -> list[str]:
        return [f"{method} {path}" for method, path, _ in self.calls]


@pytest.fixture
def env(monkeypatch):
    for state in (km._tasks, km._task_workers, km._installations, km._active_package_tasks, km._unsubscribing, km._background):
        state.clear()
    main = FakeMain()
    sleeps: list[float] = []
    tagged: list[tuple[str, float]] = []
    reports: list[tuple[str, int]] = []

    async def fast_sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 4))
        tagged.append((asyncio.current_task().get_name(), round(seconds, 4)))
        await asyncio.sleep(0)

    async def fetch(request):
        return _descriptor(request.package_id, request.pack_id, request.version)

    async def download(descriptor):
        return ARTIFACT

    async def report_subscribe(descriptor, result):
        reports.append(("subscribe", descriptor.package_id))

    async def report_unsubscribe(package_id, *, timeout=10.0):
        reports.append(("unsubscribe", package_id))
        return True

    # No staged imports unless a test says otherwise (every unsubscribe looks).
    main.on("GET", "packs/jobs", {"ok": True, "jobs": []})
    monkeypatch.setattr(km, "_main_call", main)
    monkeypatch.setattr(km, "_sleep", fast_sleep)
    monkeypatch.setattr(km, "_fetch_version_descriptor", fetch)
    monkeypatch.setattr(km, "_download_verified_artifact", download)
    monkeypatch.setattr(km, "_report_subscription_best_effort", report_subscribe)
    monkeypatch.setattr(km, "_report_unsubscribe_best_effort", report_unsubscribe)
    app = FastAPI()
    app.include_router(km.router)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")
    yield {"http": http, "main": main, "sleeps": sleeps, "tagged": tagged, "reports": reports}
    for worker in list(km._task_workers.values()) + list(km._unsubscribing.values()):
        worker.cancel()


TOKEN = {"token": get_bridge_token()}


async def _subscribe(http, package_id: int = 42, pack_id: str = "market-memes") -> str:
    response = await http.post(
        "/market/knowledge/subscribe",
        params=TOKEN,
        json={"package_id": package_id, "version": "1.0.0", "channel": "stable", "pack_id": pack_id},
    )
    assert response.status_code == 200, response.text
    return response.json()["task_id"]


async def _finished(http, task_id: str) -> dict:
    for _ in range(500):
        task = (await http.get(f"/market/knowledge/tasks/{task_id}", params=TOKEN)).json()
        if task["status"] in ("completed", "failed", "cancelled"):
            return task
        await asyncio.sleep(0.005)
    raise AssertionError(f"task stuck: {task}")


# ── subscribe ───────────────────────────────────────────────────────


async def test_requires_the_bridge_token(env):
    response = await env["http"].post(
        "/market/knowledge/subscribe",
        params={"token": "wrong"},
        json={"package_id": 1, "version": "1", "channel": "stable", "pack_id": "ab"},
    )
    assert response.status_code == 403


async def test_subscription_installs_through_main_and_waits_for_activation(env):
    main = env["main"]
    slots_while_indexing: list[int] = []

    async def queued():
        slots_while_indexing.append(km._subscription_slots_in_use())
        return {"ok": True, "jobs": [{"job_id": "j1", "state": "building"}]}

    main.on("POST", "subscriptions/apply", {"ok": True, "job_id": "j1", "state": "queued", "pack_id": "market-memes"})
    main.on("GET", "packs/jobs", queued, {"ok": True, "jobs": [{"job_id": "j1", "state": "active"}]})
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["status"] == "completed", task
    assert task["result"]["activation"]["state"] == "active"
    # Staged: the worker gave its download slot back while waiting.
    assert slots_while_indexing == [0]
    _method, _path, kwargs = main.calls[0]
    subscription = kwargs["data"]["subscription"]
    assert '"provider":"plugin-market"' in subscription and f'"artifact_sha256":"{SHA}"' in subscription
    assert kwargs["files"]["pack"][1] == ARTIFACT
    assert env["reports"] == [("subscribe", 42)]


async def test_pruned_job_is_reconciled_with_the_installed_registry(env):
    main = env["main"]
    main.on("POST", "subscriptions/apply", {"ok": True, "job_id": "j1", "state": "queued"})
    main.on("GET", "packs/jobs", {"ok": True, "jobs": []})
    main.on("GET", "packs", {"ok": True, "packs": [_installed()]})
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["status"] == "completed"
    assert task["result"]["activation"]["reconciled"] == "registry"


async def test_pruned_job_with_another_artifact_installed_is_a_failure(env):
    main = env["main"]
    main.on("POST", "subscriptions/apply", {"ok": True, "job_id": "j1", "state": "queued"})
    main.on("GET", "packs/jobs", {"ok": True, "jobs": []})
    main.on("GET", "packs", {"ok": True, "packs": [_installed(sha="0" * 64)]})
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["status"] == "failed" and task["error_code"] == "job_not_found"


async def test_pruned_job_with_the_pack_now_owned_by_another_package_is_a_failure(env):
    main = env["main"]
    main.on("POST", "subscriptions/apply", {"ok": True, "job_id": "j1", "state": "queued"})
    main.on("GET", "packs/jobs", {"ok": True, "jobs": []})
    # Same pack id and bytes, but installed by another market package.
    main.on("GET", "packs", {"ok": True, "packs": [_installed(package_id="99")]})
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["status"] == "failed" and task["error_code"] == "job_not_found"


async def test_pruned_job_with_only_the_previous_release_installed_is_a_failure(env):
    main = env["main"]
    main.on("POST", "subscriptions/apply", {"ok": True, "job_id": "j1", "state": "queued"})
    main.on("GET", "packs/jobs", {"ok": True, "jobs": []})
    # Same package, pack and bytes, but the record still names the previous release.
    previous = _installed()
    previous["subscription"] = {**previous["subscription"], "version": "0.9.0"}
    main.on("GET", "packs", {"ok": True, "packs": [previous]})
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["status"] == "failed" and task["error_code"] == "job_not_found"


async def test_pruned_job_reconciliation_waits_out_an_unreadable_registry(env):
    main = env["main"]
    main.on("POST", "subscriptions/apply", {"ok": True, "job_id": "j1", "state": "queued"})
    main.on("GET", "packs/jobs", {"ok": True, "jobs": []})
    main.on(
        "GET",
        "packs",
        km._TaskError("main_server_unavailable", "x", retryable=True),
        {"ok": False, "reason": "knowledge_busy"},
        {"ok": True, "packs": [_installed()]},
    )
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["status"] == "completed"
    assert task["result"]["activation"]["reconciled"] == "registry"


async def test_pruned_job_with_only_a_broken_pack_installed_is_a_failure(env):
    main = env["main"]
    main.on("POST", "subscriptions/apply", {"ok": True, "job_id": "j1", "state": "queued"})
    main.on("GET", "packs/jobs", {"ok": True, "jobs": []})
    broken = {**_installed(), "broken": True}
    main.on("GET", "packs", {"ok": True, "packs": [broken]})
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["status"] == "failed" and task["error_code"] == "job_not_found"


async def test_busy_answers_are_retried_then_succeed(env):
    main = env["main"]
    main.on(
        "POST",
        "subscriptions/apply",
        {"ok": False, "reason": "knowledge_busy"},
        {"ok": False, "reason": "job_in_progress"},
        {"ok": True, "pack_id": "market-memes", "unchanged": True, "state": "active"},
    )
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["status"] == "completed"
    assert env["sleeps"][:2] == [km._BUSY_RETRY_FIRST_SECONDS, km._BUSY_RETRY_FIRST_SECONDS * 2]


async def test_persistent_busy_is_a_retryable_busy_failure_not_an_invalid_pack(env, monkeypatch):
    monkeypatch.setattr(km, "_BUSY_RETRY_WINDOW_SECONDS", 0.0)
    env["main"].on("POST", "subscriptions/apply", {"ok": False, "reason": "knowledge_busy"})
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["status"] == "failed"
    assert task["error_code"] == "knowledge_busy" and task["retryable"] is True


async def test_a_rejected_pack_is_not_retried(env):
    env["main"].on("POST", "subscriptions/apply", {"ok": False, "reason": "artifact_hash_mismatch"})
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["error_code"] == "artifact_hash_mismatch" and task["retryable"] is False
    assert env["main"].paths() == ["POST subscriptions/apply"]


async def test_download_slots_and_tracked_workers_are_capped_separately(env, monkeypatch):
    monkeypatch.setattr(km, "_MAX_ACTIVE_SUBSCRIPTIONS", 2)
    monkeypatch.setattr(km, "_MAX_TRACKED_SUBSCRIPTIONS", 3)
    gate = asyncio.Event()

    async def held_download(descriptor):
        await gate.wait()
        return ARTIFACT

    monkeypatch.setattr(km, "_download_verified_artifact", held_download)
    http = env["http"]
    await _subscribe(http, 1, "pack-one")
    await _subscribe(http, 2, "pack-two")
    full = await http.post(
        "/market/knowledge/subscribe",
        params=TOKEN,
        json={"package_id": 3, "version": "1.0.0", "channel": "stable", "pack_id": "pack-three"},
    )
    assert full.status_code == 429

    # Both staged and only waiting for activation: their slots come back...
    env["main"].on("POST", "subscriptions/apply", {"ok": True, "job_id": "j", "state": "queued"})
    env["main"].on("GET", "packs/jobs", {"ok": True, "jobs": [{"job_id": "j", "state": "building"}]})
    gate.set()
    for _ in range(200):
        if km._subscription_slots_in_use() == 0:
            break
        await asyncio.sleep(0.005)
    assert km._subscription_slots_in_use() == 0
    await _subscribe(http, 3, "pack-three")
    # ...but the number of live workers stays bounded.
    capped = await http.post(
        "/market/knowledge/subscribe",
        params=TOKEN,
        json={"package_id": 4, "version": "1.0.0", "channel": "stable", "pack_id": "pack-four"},
    )
    assert capped.status_code == 429


async def test_same_request_joins_and_a_different_one_conflicts(env, monkeypatch):
    gate = asyncio.Event()

    async def held_download(descriptor):
        await gate.wait()
        return ARTIFACT

    monkeypatch.setattr(km, "_download_verified_artifact", held_download)
    http = env["http"]
    task_id = await _subscribe(http)
    assert await _subscribe(http) == task_id
    other = await http.post(
        "/market/knowledge/subscribe",
        params=TOKEN,
        json={"package_id": 42, "version": "2.0.0", "channel": "stable", "pack_id": "market-memes"},
    )
    assert other.status_code == 409


# ── unsubscribe ─────────────────────────────────────────────────────


async def _unsubscribe(http, package_id: int = 42, pack_id: str = "market-memes"):
    response = await http.post(
        "/market/knowledge/unsubscribe", params=TOKEN, json={"package_id": package_id, "pack_id": pack_id}
    )
    # The market is told in the background; let that finish before asserting on it.
    await asyncio.gather(*km._background)
    return response


async def test_unsubscribe_removes_only_the_owned_subscription(env):
    main = env["main"]
    main.on("GET", "packs", {"ok": True, "packs": [_installed(), {"pack_id": "local-pack", "subscription": None}]})
    main.on("POST", "packs/remove", {"ok": True, "pack_id": "market-memes", "removed_entries": 3,
                                     "operation_status": "committed"})
    response = await _unsubscribe(env["http"])
    assert response.status_code == 200
    assert response.json()["removed"] is True
    assert response.json()["remote_report"] == "scheduled"
    _method, _path, kwargs = main.calls[-1]
    assert kwargs["json_body"]["expected_subscription"] == {
        "provider": "plugin-market",
        "provider_package_id": "42",
        "remote_id": "knowledge/market-memes",
    }
    assert kwargs["json_body"]["operation_id"]
    assert env["reports"] == [("unsubscribe", 42)]


async def test_unsubscribe_reports_with_its_own_timeout_not_the_request_budget(env, monkeypatch):
    seen: list[float] = []

    async def report(package_id, *, timeout=10.0):
        seen.append(timeout)
        return True

    monkeypatch.setattr(km, "_report_unsubscribe_best_effort", report)
    monkeypatch.setattr(km, "_UNSUBSCRIBE_BUDGET_SECONDS", 0.01)
    main = env["main"]
    main.on("GET", "packs", {"ok": True, "packs": [_installed()]})
    main.on("POST", "packs/remove", {"ok": True, "pack_id": "market-memes", "operation_status": "committed"})
    response = await _unsubscribe(env["http"])
    assert response.status_code == 200
    assert seen == [10.0]  # the report's own default, however little of the budget was left


async def test_a_cancel_after_completion_keeps_the_task_completed(env, monkeypatch):
    reporting = asyncio.Event()

    async def slow_report(descriptor, result):
        reporting.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(km, "_report_subscription_best_effort", slow_report)
    env["main"].on("POST", "subscriptions/apply", {"ok": True, "pack_id": "market-memes", "unchanged": True, "state": "active"})
    task_id = await _subscribe(env["http"])
    await asyncio.wait_for(reporting.wait(), 5)
    worker = km._task_workers[task_id]
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    task = (await env["http"].get(f"/market/knowledge/tasks/{task_id}", params=TOKEN)).json()
    assert task["status"] == "completed"


async def test_resubscribe_waits_until_the_unsubscribe_report_has_landed(env, monkeypatch):
    landed = asyncio.Event()
    release = asyncio.Event()

    async def slow_report(package_id, *, timeout=10.0):
        await release.wait()
        landed.set()
        return True

    monkeypatch.setattr(km, "_report_unsubscribe_best_effort", slow_report)
    main = env["main"]
    main.on("GET", "packs", {"ok": True, "packs": [_installed()]})
    main.on("POST", "packs/remove", {"ok": True, "pack_id": "market-memes", "operation_status": "committed"})
    http = env["http"]
    response = await http.post(
        "/market/knowledge/unsubscribe", params=TOKEN, json={"package_id": 42, "pack_id": "market-memes"}
    )
    assert response.status_code == 200
    body = {"package_id": 42, "version": "1.0.0", "channel": "stable", "pack_id": "market-memes"}
    # The market is still being told about the unsubscribe: a new subscribe could overtake it.
    assert (await http.post("/market/knowledge/subscribe", params=TOKEN, json=body)).status_code == 409
    release.set()
    await asyncio.wait_for(landed.wait(), 5)
    await asyncio.gather(*km._background)
    assert 42 not in km._unsubscribing
    assert (await http.post("/market/knowledge/subscribe", params=TOKEN, json=body)).status_code == 200


async def test_unsubscribe_refuses_a_pack_it_does_not_own(env):
    main = env["main"]
    main.on("GET", "packs", {"ok": True, "packs": [_installed()]})
    assert (await _unsubscribe(env["http"], pack_id="someone-else")).status_code == 409
    assert (await _unsubscribe(env["http"], package_id=7)).status_code == 404
    assert "POST packs/remove" not in main.paths()


async def test_busy_removal_is_reported_as_retryable_busy(env):
    main = env["main"]
    main.on("GET", "packs", {"ok": True, "packs": [_installed()]})
    main.on("POST", "packs/remove", {"ok": False, "reason": "knowledge_busy"})
    response = await _unsubscribe(env["http"])
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "knowledge_busy", "retryable": True}


async def test_a_definite_refusal_is_busy_at_once_not_an_unknown_outcome(env):
    main = env["main"]
    main.on("GET", "packs", {"ok": True, "packs": [_installed()]})
    # Sent by the Memory Server itself (e.g. its registry is unreadable).
    main.on("POST", "packs/remove", {"ok": False, "reason": "knowledge_unavailable"})
    response = await _unsubscribe(env["http"])
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "knowledge_busy", "retryable": True}
    assert "GET packs/remove/status" not in main.paths()
    assert 42 not in km._unsubscribing


@pytest.mark.parametrize(
    "status, body, reason",
    [
        (503, {"ok": False, "reason": "knowledge_unavailable"}, "memory_server_unreachable"),
        (504, {"ok": False, "reason": "knowledge_timeout"}, "memory_server_unreachable"),
        (200, {"ok": False, "reason": "knowledge_unavailable"}, "knowledge_unavailable"),
        (503, {"ok": False, "reason": "knowledge_starting"}, "knowledge_starting"),
        (403, {"detail": "csrf"}, "http_403"),
        # Storage-limited startup (Main's or the Memory Server's gate): retried as starting.
        (409, {"ok": False, "error_code": "storage_startup_blocked"}, "knowledge_starting"),
    ],
)
async def test_main_hop_failures_are_told_apart_from_memory_server_answers(monkeypatch, status, body, reason):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)))
    monkeypatch.setattr(km, "_client", lambda: client)
    payload = await km._main_call("POST", "packs/remove", json_body={"pack_id": "x1"})
    assert payload["reason"] == reason
    await client.aclose()


async def test_unknown_removal_is_confirmed_quickly_then_drained_with_backoff(env, monkeypatch):
    monkeypatch.setattr(km, "_UNSUBSCRIBE_BUDGET_SECONDS", 0.05)
    main = env["main"]
    main.on("GET", "packs", {"ok": True, "packs": [_installed()]})
    main.on("POST", "packs/remove", km._TaskError("main_server_unavailable", "x", retryable=True))
    drain_polls = 0

    async def status():
        nonlocal drain_polls
        if not asyncio.current_task().get_name().startswith("market-knowledge-remove-drain"):
            return {"ok": False, "reason": "removal_in_progress", "operation_status": "pending"}
        drain_polls += 1
        if drain_polls < 10:
            return {"ok": False, "reason": "removal_in_progress", "operation_status": "pending"}
        return {"ok": True, "pack_id": "market-memes", "operation_status": "committed"}

    main.on("GET", "packs/remove/status", status)
    response = await _unsubscribe(env["http"])
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "removal_pending"
    # The drain holds the package and keeps confirming.
    drain = km._unsubscribing[42]
    await asyncio.wait_for(drain, 5)
    sync_sleeps = [s for name, s in env["tagged"] if name.startswith("market-knowledge-unsubscribe")]
    drain_sleeps = [s for name, s in env["tagged"] if name.startswith("market-knowledge-remove-drain")]
    # While the request waits: a short fixed interval (cut to what is left).
    assert sync_sleeps and max(sync_sleeps) <= km._REMOVAL_STATUS_POLL_SECONDS
    expected = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert drain_sleeps[: len(expected)] == expected
    assert drain.result()["operation_status"] == "committed"
    assert env["reports"] == [("unsubscribe", 42)]
    assert 42 not in km._unsubscribing


async def test_subscribe_is_refused_while_a_removal_is_draining(env, monkeypatch):
    monkeypatch.setattr(km, "_UNSUBSCRIBE_BUDGET_SECONDS", 0.02)
    gate = asyncio.Event()

    async def held_status():
        await gate.wait()
        return {"ok": True, "operation_status": "committed"}

    main = env["main"]
    main.on("GET", "packs", {"ok": True, "packs": [_installed()]})
    # Main gave up on the Memory Server (what _main_call reports for its 504).
    main.on("POST", "packs/remove", {"ok": False, "reason": "memory_server_unreachable"})
    main.on("GET", "packs/remove/status", {"ok": False, "operation_status": "unknown"})
    assert (await _unsubscribe(env["http"])).status_code == 503
    main.on("GET", "packs/remove/status", held_status)
    response = await env["http"].post(
        "/market/knowledge/subscribe",
        params=TOKEN,
        json={"package_id": 42, "version": "1.0.0", "channel": "stable", "pack_id": "market-memes"},
    )
    assert response.status_code == 409
    gate.set()


async def test_unsubscribe_cancels_an_install_that_has_not_landed(env, monkeypatch):
    gate = asyncio.Event()

    async def held_download(descriptor):
        await gate.wait()
        return ARTIFACT

    monkeypatch.setattr(km, "_download_verified_artifact", held_download)
    main = env["main"]
    main.on("GET", "packs", {"ok": True, "packs": []})
    main.on("GET", "packs/jobs", {"ok": True, "jobs": []})
    task_id = await _subscribe(env["http"])
    await asyncio.sleep(0.01)
    response = await _unsubscribe(env["http"])
    assert response.status_code == 200
    assert response.json()["cancelled"] is True and response.json()["removed"] is False
    task = (await env["http"].get(f"/market/knowledge/tasks/{task_id}", params=TOKEN)).json()
    assert task["status"] == "cancelled"
    assert "POST subscriptions/apply" not in main.paths()


def _job(job_id: str, state: str, package_id: str = "42") -> dict:
    return {"job_id": job_id, "state": state, "subscription": _installed(package_id=package_id)["subscription"]}


async def test_unsubscribe_cancels_a_staged_import_before_removing(env):
    main = env["main"]
    state = {"j1": "queued"}

    async def jobs():
        return {"ok": True, "jobs": [_job("j1", state["j1"]), _job("other", "queued", package_id="7")]}

    async def cancel():
        state["j1"] = "cancelled"
        return {"ok": True, "job_id": "j1"}

    main.on("POST", "subscriptions/apply", {"ok": True, "job_id": "j1", "state": "queued"})
    main.on("GET", "packs/jobs", jobs)
    main.on("POST", "packs/jobs/cancel", cancel)
    task_id = await _subscribe(env["http"])
    for _ in range(200):
        if km._tasks[task_id].get("job_id"):
            break
        await asyncio.sleep(0.005)
    main.on("GET", "packs", {"ok": True, "packs": []})
    response = await _unsubscribe(env["http"])
    assert response.status_code == 200 and response.json()["cancelled"] is True
    cancels = [kwargs["json_body"]["job_id"] for method, path, kwargs in main.calls if path == "packs/jobs/cancel"]
    # Only this package's import, never another package's.
    assert cancels == ["j1"]


async def test_unsubscribe_after_a_restart_still_cancels_a_staged_import(env):
    """No worker is remembered (the plugin server restarted), but the import is staged."""
    main = env["main"]
    state = {"j9": "queued"}

    async def jobs():
        return {"ok": True, "jobs": [_job("j9", state["j9"])]}

    async def cancel():
        state["j9"] = "cancelled"
        return {"ok": True, "job_id": "j9"}

    main.on("GET", "packs/jobs", jobs)
    main.on("POST", "packs/jobs/cancel", cancel)
    main.on("GET", "packs", {"ok": True, "packs": []})
    response = await _unsubscribe(env["http"])
    assert response.status_code == 200 and response.json()["cancelled"] is True
    assert "POST packs/jobs/cancel" in main.paths()
    # A staged import of this package under another pack id is not this caller's to cancel.
    state["j9"] = "queued"
    assert (await _unsubscribe(env["http"], pack_id="another-pack")).status_code == 409


async def test_unsubscribe_waits_for_an_import_that_is_not_a_job_yet(env):
    """After a restart, an apply may still be in the Memory Server's admission."""
    main = env["main"]
    incoming = _installed()["subscription"]
    replies = iter(
        [
            {"ok": True, "jobs": [], "admitting": [incoming]},
            {"ok": True, "jobs": [], "admitting": [incoming]},
            {"ok": True, "jobs": [_job("late", "queued")], "admitting": []},
        ]
    )
    state = {"cancelled": False}

    async def jobs():
        if state["cancelled"]:
            return {"ok": True, "jobs": [_job("late", "cancelled")], "admitting": []}
        return next(replies, {"ok": True, "jobs": [_job("late", "queued")], "admitting": []})

    async def cancel():
        state["cancelled"] = True
        return {"ok": True, "job_id": "late"}

    main.on("GET", "packs/jobs", jobs)
    main.on("POST", "packs/jobs/cancel", cancel)
    main.on("GET", "packs", {"ok": True, "packs": []})
    response = await _unsubscribe(env["http"])
    assert response.status_code == 200 and response.json()["cancelled"] is True
    assert state["cancelled"] is True


async def test_unsubscribe_cancels_an_import_whose_install_answer_was_lost(env):
    main = env["main"]
    # The install request reached Main and staged a job, but no answer came back.
    main.on("POST", "subscriptions/apply", km._TaskError("main_server_unavailable", "x", retryable=True))
    task_id = await _subscribe(env["http"])
    for _ in range(200):
        if km._tasks[task_id]["stage"] == "installing" and env["sleeps"]:
            break
        await asyncio.sleep(0.005)
    main.on(
        "GET",
        "packs/jobs",
        {"ok": True, "jobs": [_job("lost", "queued")]},
        {"ok": True, "jobs": [_job("lost", "cancelled")]},
    )
    main.on("POST", "packs/jobs/cancel", {"ok": True, "job_id": "lost"})
    main.on("GET", "packs", {"ok": True, "packs": []})
    response = await _unsubscribe(env["http"])
    assert response.status_code == 200 and response.json()["cancelled"] is True
    assert "POST packs/jobs/cancel" in main.paths()


# ── download safety ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url, final, allowed",
    [
        ("https://github.com/o/r/releases/download/v1/p.neko-knowledge.json", True, True),
        ("https://github.com/o/r/releases/download/v1/p.zip", True, False),
        ("http://github.com/o/r/p.neko-knowledge.json", True, False),
        ("https://evil.example/p.neko-knowledge.json", True, False),
        ("https://user:pw@github.com/p.neko-knowledge.json", True, False),
        ("https://objects.githubusercontent.com/some/cdn/path", False, True),
        ("https://127.0.0.1/p.neko-knowledge.json", False, False),
    ],
)
def test_artifact_hosts_are_allowlisted(url, final, allowed):
    assert km._artifact_url_allowed(url, final=final) is allowed


async def test_redirect_off_the_allowlist_is_refused(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1:48911/secret"})

    real_client = httpx.AsyncClient

    class Fake:
        AsyncClient = staticmethod(lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
        Timeout = httpx.Timeout

    with pytest.raises(km._TaskError) as excinfo:
        await km._download("https://github.com/o/r/releases/download/v1/p.neko-knowledge.json", Fake)
    assert excinfo.value.code == "unsafe_artifact_redirect"


# ── wiring ──────────────────────────────────────────────────────────


def test_subscription_routes_come_before_the_bridge_catch_all():
    from plugin.server.http_app import build_plugin_server_app
    from tests.fastapi_routes import effective_path, iter_routes

    paths = [effective_path(route) for route in iter_routes(build_plugin_server_app().routes)]
    assert paths.index("/market/knowledge/subscribe") < paths.index("/market/knowledge/{path:path}")
    assert paths.index("/market/knowledge/subscriptions") < paths.index("/market/knowledge/{path:path}")


@pytest.mark.parametrize(
    "origin, status",
    [
        ("http://127.0.0.1:48911", 200),  # manager served by Main (packaged)
        ("http://127.0.0.1:48916", 200),  # manager served by this server, or via the Vite proxy
        # Never trusted: the Vite proxy rewrites the Origin of what it forwards,
        # so a request still carrying 5173 came from some other page on that port.
        ("http://localhost:5173", 403),
        ("http://127.0.0.1:3000", 403),  # another local page
        ("https://market.example.com", 403),
    ],
)
def test_pair_code_origin_gate_and_main_port(monkeypatch, origin, status):
    from fastapi.testclient import TestClient

    from plugin.server.routes import market_bridge

    monkeypatch.setattr(market_bridge, "_main_server_port", lambda: 48911)
    app = FastAPI()
    app.include_router(market_bridge.router)
    with TestClient(app, base_url="http://127.0.0.1:48916", client=("127.0.0.1", 50000)) as client:
        response = client.post("/market/pair-code", headers={"Origin": origin})
    assert response.status_code == status
    if status == 200:
        # Main's port whichever server the manager page reached (decision A).
        assert response.json()["port"] == 48911
        assert response.json()["one_time_code"]


EXPECTED = {"pack_id": "market-memes", "provider_package_id": "42", "remote_id": "knowledge/market-memes"}


async def test_a_forgotten_removal_whose_subscription_is_gone_counts_as_committed(env):
    """The Memory Server restarted after committing: it no longer knows the id, the registry does."""
    main = env["main"]
    main.on("GET", "packs/remove/status", {"ok": False, "reason": "unknown_operation", "operation_status": "unknown"})
    main.on("GET", "packs", {"ok": True, "packs": [{"pack_id": "local-pack", "subscription": None}]})
    result = await km._removal_status("op-forgotten-00001", EXPECTED, timeout=1.0)
    assert result["operation_status"] == "committed" and result["ok"] is True
    # Still installed: genuinely unknown (it may not have arrived yet).
    main.on("GET", "packs", {"ok": True, "packs": [_installed()]})
    assert (await km._removal_status("op-forgotten-00001", EXPECTED, timeout=1.0))["operation_status"] == "unknown"
    # A failed read is no answer at all.
    main.on("GET", "packs", {"ok": False, "reason": "memory_server_unreachable"})
    assert (await km._removal_status("op-forgotten-00001", EXPECTED, timeout=1.0))["operation_status"] == "unreachable"
    main.on("GET", "packs/remove/status", km._TaskError("main_server_unavailable", "x", retryable=True))
    assert (await km._removal_status("op-forgotten-00001", EXPECTED, timeout=1.0))["operation_status"] == "unreachable"


async def test_drain_outlasts_unanswered_readings_and_reports_the_commit(env, monkeypatch):
    monkeypatch.setattr(km, "_REMOVAL_UNKNOWN_GIVE_UP_SECONDS", 0)  # already past give-up
    main = env["main"]
    unreachable = km._TaskError("main_server_unavailable", "x", retryable=True)
    main.on("GET", "packs/remove/status", *([unreachable] * 8), {"ok": True, "operation_status": "committed"})
    km._unsubscribing[42] = asyncio.current_task()
    km._start_removal_drain(42, "op-drain-unreach-01", EXPECTED, asyncio.get_running_loop().time())
    drain = km._unsubscribing[42]
    result = await asyncio.wait_for(drain, 5)
    assert result["operation_status"] == "committed"
    assert env["reports"] == [("unsubscribe", 42)]


async def test_drain_stops_when_the_removal_never_arrived(env, monkeypatch):
    monkeypatch.setattr(km, "_REMOVAL_UNKNOWN_GIVE_UP_SECONDS", 0)
    main = env["main"]
    main.on("GET", "packs/remove/status", {"ok": False, "reason": "unknown_operation", "operation_status": "unknown"})
    main.on("GET", "packs", {"ok": True, "packs": [_installed()]})
    km._unsubscribing[42] = asyncio.current_task()
    km._start_removal_drain(42, "op-never-arrived-01", EXPECTED, asyncio.get_running_loop().time())
    drain = km._unsubscribing[42]
    result = await asyncio.wait_for(drain, 5)
    assert result["operation_status"] == "unknown"
    assert env["reports"] == []
    assert 42 not in km._unsubscribing


async def test_confirmation_polls_at_a_fixed_interval_unless_draining(env):
    pending = {"ok": False, "operation_status": "pending"}
    env["main"].on("GET", "packs/remove/status", pending)
    loop = asyncio.get_running_loop()

    await km._confirm_removal("op-sync-000000001", EXPECTED, deadline=loop.time() + 0.5)
    waiting = list(env["sleeps"])
    assert len(waiting) >= 3
    # The request is waiting: every full interval is the short one.
    assert waiting[:3] == [km._REMOVAL_STATUS_POLL_SECONDS] * 3
