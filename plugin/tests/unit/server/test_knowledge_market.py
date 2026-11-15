"""``/market/knowledge/*`` subscriptions: the plugin server's market routes.

Main's knowledge proxy and the market API are replaced by fakes. The tests pin
what this server still owns: downloading and handing the artifact over (the
task ends once it is staged), bounded resending of busy or unanswered
requests, one removal per unsubscribe that names the releases known when it
starts, refusing a subscribe of the package meanwhile, and the download and
pairing rules kept from the #2951 review.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException

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


def _subscription(pack_id: str = "market-memes", package_id: str = "42", version: str = "1.0.0",
                  sha: str = SHA) -> dict:
    return {
        "provider": "plugin-market",
        "provider_package_id": package_id,
        "remote_id": f"knowledge/{pack_id}",
        "pack_id": pack_id,
        "version": version,
        "channel": "stable",
        "artifact_sha256": sha,
        "material_type": "knowledge",
    }


def _installed(pack_id: str = "market-memes", **fields) -> dict:
    return {"pack_id": pack_id, "broken": False, "subscription": _subscription(pack_id, **fields)}


def _import(state: str, pack_id: str = "market-memes", **fields) -> dict:
    return {"job_id": "j-" + state, "pack_id": pack_id, "state": state, "reason": "",
            "subscription": _subscription(pack_id, **fields)}


def _state(installed=(), imports=()) -> dict:
    return {"ok": True, "installed": list(installed), "imports": list(imports)}


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

    def bodies(self, path: str) -> list[Any]:
        return [kwargs.get("json_body") for _method, called, kwargs in self.calls if called == path]


@pytest.fixture
def env(monkeypatch):
    for state in (
        km._tasks, km._task_workers, km._applies, km._active_package_tasks,
        km._unsubscribing, km._background, km._mirror_cache, km._reports,
    ):
        state.clear()
    main = FakeMain()
    sleeps: list[float] = []
    reports: list[tuple[str, int, Any]] = []

    async def fast_sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 4))
        await asyncio.sleep(0)

    async def fetch(request):
        return _descriptor(request.package_id, request.pack_id, request.version)

    async def download(descriptor):
        return ARTIFACT

    async def report_subscribe(descriptor, result):
        reports.append(("subscribe", descriptor.package_id, result.get("job_id")))

    async def report_unsubscribe(package_id, *, timeout=10.0):
        reports.append(("unsubscribe", package_id, timeout))
        return True

    main.on("GET", "subscriptions", _state())
    monkeypatch.setattr(km, "_main_call", main)
    monkeypatch.setattr(km, "_sleep", fast_sleep)
    monkeypatch.setattr(km, "_fetch_version_descriptor", fetch)
    monkeypatch.setattr(km, "_download_verified_artifact", download)
    monkeypatch.setattr(km, "_report_subscription_best_effort", report_subscribe)
    monkeypatch.setattr(km, "_report_unsubscribe_best_effort", report_unsubscribe)
    app = FastAPI()
    app.include_router(km.router)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")
    yield {"http": http, "main": main, "sleeps": sleeps, "reports": reports}
    for worker in [*km._task_workers.values(), *km._unsubscribing.values(), *km._applies.values(), *km._background]:
        worker.cancel()


TOKEN = {"token": get_bridge_token()}


async def _subscribe(http, package_id: int = 42, pack_id: str = "market-memes", version: str = "1.0.0") -> str:
    response = await http.post(
        "/market/knowledge/subscribe",
        params=TOKEN,
        json={"package_id": package_id, "version": version, "channel": "stable", "pack_id": pack_id},
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


async def _unsubscribe(http, package_id: int = 42, pack_id: str = "market-memes"):
    return await http.post(
        "/market/knowledge/unsubscribe", params=TOKEN, json={"package_id": package_id, "pack_id": pack_id}
    )


async def _until(predicate, message: str) -> None:
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(message)


STAGED = {"ok": True, "job_id": "j1", "state": "queued", "pack_id": "market-memes", "ready": True}


# ── subscribe ───────────────────────────────────────────────────────


async def test_requires_the_bridge_token(env):
    response = await env["http"].post(
        "/market/knowledge/subscribe",
        params={"token": "wrong"},
        json={"package_id": 1, "version": "1", "channel": "stable", "pack_id": "ab"},
    )
    assert response.status_code == 403


async def test_a_subscription_ends_once_the_memory_server_staged_it(env):
    main = env["main"]
    main.on("POST", "subscriptions/apply", STAGED)
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["status"] == "completed" and task["stage"] == "staged", task
    assert task["result"] == {"pack_id": "market-memes", "job_id": "j1", "state": "queued"}
    # Handed over and done: no waiting for activation, no polling.
    assert main.paths() == ["POST subscriptions/apply"]
    _method, _path, kwargs = main.calls[0]
    subscription = kwargs["data"]["subscription"]
    assert '"provider":"plugin-market"' in subscription and f'"artifact_sha256":"{SHA}"' in subscription
    assert kwargs["files"]["pack"][1] == ARTIFACT
    await _until(lambda: env["reports"], "no report")
    assert env["reports"] == [("subscribe", 42, "j1")]
    assert km._task_workers == {} and km._active_package_tasks == {}


async def test_busy_and_unanswered_applies_are_sent_again(env):
    main = env["main"]
    main.on(
        "POST",
        "subscriptions/apply",
        {"ok": False, "reason": "knowledge_busy"},
        {"ok": False, "reason": "memory_server_unreachable"},
        km._TaskError("main_server_unavailable", "x", retryable=True),
        # The repeat of an apply that was staged: answered with that job.
        {**STAGED, "existing": True},
    )
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["status"] == "completed"
    assert env["sleeps"] == list(km._APPLY_RETRY_DELAYS[:3])
    bodies = [kwargs["data"] for _m, path, kwargs in main.calls if path == "subscriptions/apply"]
    assert len(bodies) == 4 and all(body == bodies[0] for body in bodies)


async def test_persistent_busy_is_a_bounded_retryable_failure_not_an_invalid_pack(env):
    env["main"].on("POST", "subscriptions/apply", {"ok": False, "reason": "knowledge_busy"})
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["status"] == "failed"
    assert task["error_code"] == "knowledge_busy" and task["retryable"] is True
    assert env["main"].paths().count("POST subscriptions/apply") == len(km._APPLY_RETRY_DELAYS) + 1


async def test_apply_retries_also_stop_at_the_window(env, monkeypatch):
    monkeypatch.setattr(km, "_APPLY_RETRY_WINDOW_SECONDS", 0.0)
    env["main"].on("POST", "subscriptions/apply", {"ok": False, "reason": "knowledge_starting"})
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["error_code"] == "knowledge_busy" and task["retryable"] is True
    assert env["main"].paths() == ["POST subscriptions/apply"]


async def test_a_rejected_pack_is_not_retried(env):
    env["main"].on("POST", "subscriptions/apply", {"ok": False, "reason": "artifact_hash_mismatch"})
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["error_code"] == "artifact_hash_mismatch" and task["retryable"] is False
    assert env["main"].paths() == ["POST subscriptions/apply"]


async def test_an_import_cancelled_by_a_removal_fails_the_task(env):
    env["main"].on("POST", "subscriptions/apply", {**STAGED, "state": "cancelled"})
    task = await _finished(env["http"], await _subscribe(env["http"]))
    assert task["status"] == "failed" and task["error_code"] == "job_cancelled"


async def test_live_workers_are_capped(env, monkeypatch):
    monkeypatch.setattr(km, "_MAX_ACTIVE_SUBSCRIPTIONS", 2)
    gate = asyncio.Event()

    async def blocked_download(descriptor):
        await gate.wait()
        return ARTIFACT

    monkeypatch.setattr(km, "_download_verified_artifact", blocked_download)
    env["main"].on("POST", "subscriptions/apply", STAGED)
    first = await _subscribe(env["http"], 1, "pack-one")
    await _subscribe(env["http"], 2, "pack-two")
    refused = await env["http"].post(
        "/market/knowledge/subscribe",
        params=TOKEN,
        json={"package_id": 3, "version": "1.0.0", "channel": "stable", "pack_id": "pack-three"},
    )
    assert refused.status_code == 429
    gate.set()
    await _finished(env["http"], first)
    await _until(lambda: not km._task_workers, "workers still alive")
    await _subscribe(env["http"], 3, "pack-three")


async def test_same_request_joins_and_a_different_one_conflicts(env, monkeypatch):
    gate = asyncio.Event()

    async def blocked_download(descriptor):
        await gate.wait()
        return ARTIFACT

    monkeypatch.setattr(km, "_download_verified_artifact", blocked_download)
    env["main"].on("POST", "subscriptions/apply", STAGED)
    task_id = await _subscribe(env["http"])
    assert await _subscribe(env["http"]) == task_id
    other = await env["http"].post(
        "/market/knowledge/subscribe",
        params=TOKEN,
        json={"package_id": 42, "version": "2.0.0", "channel": "stable", "pack_id": "market-memes"},
    )
    assert other.status_code == 409
    gate.set()
    assert (await _finished(env["http"], task_id))["status"] == "completed"


async def test_subscriptions_show_installed_imports_and_tasks(env):
    main = env["main"]
    failed = {**_import("failed", package_id="43", pack_id="other-pack"), "reason": "capacity_entries"}
    local = {"pack_id": "local-pack", "broken": False, "subscription": None}
    main.on("GET", "subscriptions", _state([{**_installed(), "broken": True}, local], [failed]))
    main.on("POST", "subscriptions/apply", {"ok": False, "reason": "artifact_hash_mismatch"})
    await _finished(env["http"], await _subscribe(env["http"], 7, "seventh-pack"))
    body = (await env["http"].get("/market/knowledge/subscriptions", params=TOKEN)).json()
    assert [pack["pack_id"] for pack in body["subscriptions"]] == ["market-memes"]
    assert body["subscriptions"][0]["broken"] is True
    assert body["imports"] == [failed]
    (task,) = body["tasks"]
    assert task["package_id"] == 7 and task["status"] == "failed" and task["error_code"] == "artifact_hash_mismatch"


# ── unsubscribe ─────────────────────────────────────────────────────


def _removal(*releases: tuple[str, str]) -> dict:
    return {
        "protocol_version": 1,
        "pack_id": "market-memes",
        "expected_subscription": {
            "provider": "plugin-market", "provider_package_id": "42", "remote_id": "knowledge/market-memes",
        },
        "releases": [{"version": version, "artifact_sha256": sha} for version, sha in sorted(releases)],
    }


REMOVED = {"ok": True, "pack_id": "market-memes", "removed": True, "removed_entries": 3}


async def test_unsubscribe_sends_one_removal_naming_the_releases_known_now(env):
    main = env["main"]
    newer = "b" * 64
    main.on(
        "GET",
        "subscriptions",
        _state(
            [_installed(), _installed("someone-else", package_id="99")],
            [
                _import("queued", version="2.0.0", sha=newer),
                # Finished imports are not on their way any more.
                _import("failed", version="0.9.0", sha="c" * 64),
            ],
        ),
    )
    main.on("POST", "subscriptions/remove", REMOVED)
    response = await _unsubscribe(env["http"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["removed"] is True and body["removed_entries"] == 3 and body["remote_report"] == "scheduled"
    assert main.paths() == ["GET subscriptions", "POST subscriptions/remove"]
    assert main.bodies("subscriptions/remove") == [_removal(("1.0.0", SHA), ("2.0.0", newer))]
    await _until(lambda: env["reports"], "no report")
    # Reported in the background with the report's own timeout, not the request's.
    assert env["reports"] == [("unsubscribe", 42, 10.0)]
    assert 42 not in km._unsubscribing


async def test_unsubscribe_of_a_package_not_here_still_clears_the_market(env):
    env["main"].on("GET", "subscriptions", _state([_installed("someone-else", package_id="99")]))
    response = await _unsubscribe(env["http"])
    assert response.status_code == 200
    assert response.json()["kept"] == "not_installed" and response.json()["removed"] is False
    assert env["main"].paths() == ["GET subscriptions"]
    await _until(lambda: env["reports"], "no report")
    assert env["reports"][0][:2] == ("unsubscribe", 42)


async def test_unsubscribe_that_finds_a_later_release_does_not_tell_the_market(env):
    main = env["main"]
    main.on("GET", "subscriptions", _state([_installed()]))
    main.on("POST", "subscriptions/remove", {"ok": True, "removed": False, "kept": "other_release"})
    body = (await _unsubscribe(env["http"])).json()
    assert body["kept"] == "other_release" and body["remote_report"] == "skipped"
    await asyncio.sleep(0.02)
    assert env["reports"] == []


async def test_busy_and_unanswered_removals_send_the_same_request_again(env):
    main = env["main"]
    main.on("GET", "subscriptions", _state([_installed()]))
    main.on(
        "POST",
        "subscriptions/remove",
        {"ok": False, "reason": "knowledge_busy"},
        km._TaskError("main_server_unavailable", "x", retryable=True),
        {"ok": False, "reason": "memory_server_unreachable"},
        # Storage-limited startup, as _main_call maps it.
        {"ok": False, "reason": "knowledge_starting"},
        REMOVED,
    )
    response = await _unsubscribe(env["http"])
    assert response.status_code == 200 and response.json()["removed"] is True
    bodies = main.bodies("subscriptions/remove")
    assert len(bodies) == 5 and all(body == _removal(("1.0.0", SHA)) for body in bodies)
    assert env["sleeps"] == list(km._REMOVE_RETRY_DELAYS)


async def test_persistent_busy_removal_is_a_bounded_retryable_busy(env):
    main = env["main"]
    main.on("GET", "subscriptions", _state([_installed()]))
    main.on("POST", "subscriptions/remove", {"ok": False, "reason": "knowledge_busy"})
    response = await _unsubscribe(env["http"])
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "knowledge_busy", "retryable": True}
    assert len(main.bodies("subscriptions/remove")) == len(km._REMOVE_RETRY_DELAYS) + 1
    # No background follow-up: the package is free again at once.
    assert 42 not in km._unsubscribing and not km._background
    assert env["reports"] == []


async def test_removal_retries_stop_at_the_request_budget(env, monkeypatch):
    monkeypatch.setattr(km, "_UNSUBSCRIBE_BUDGET_SECONDS", 0.3)
    main = env["main"]
    main.on("GET", "subscriptions", _state([_installed()]))
    main.on("POST", "subscriptions/remove", {"ok": False, "reason": "knowledge_busy"})
    response = await _unsubscribe(env["http"])
    assert response.status_code == 503
    # 0.5 s, the first pause, no longer fits in what is left.
    assert len(main.bodies("subscriptions/remove")) == 1


async def test_a_definite_refusal_is_not_sent_again(env):
    main = env["main"]
    main.on("GET", "subscriptions", _state([_installed()]))
    main.on("POST", "subscriptions/remove", {"ok": False, "reason": "subscription_identity_mismatch"})
    response = await _unsubscribe(env["http"])
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "subscription_identity_mismatch"}
    assert len(main.bodies("subscriptions/remove")) == 1


async def test_unsubscribe_refuses_a_package_installed_as_another_pack(env):
    env["main"].on("GET", "subscriptions", _state([_installed("someone-else", package_id="42")]))
    response = await _unsubscribe(env["http"])
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "subscription_identity_mismatch"}
    assert env["main"].paths() == ["GET subscriptions"]


async def test_unreadable_state_is_busy(env):
    env["main"].on("GET", "subscriptions", km._TaskError("main_server_unavailable", "x", retryable=True))
    response = await _unsubscribe(env["http"])
    assert response.status_code == 503 and response.json()["detail"]["retryable"] is True


async def test_subscribe_is_refused_while_an_unsubscribe_runs(env):
    main = env["main"]
    gate = asyncio.Event()

    async def slow_removal():
        await gate.wait()
        return REMOVED

    main.on("GET", "subscriptions", _state([_installed()]))
    main.on("POST", "subscriptions/remove", slow_removal)
    main.on("POST", "subscriptions/apply", STAGED)
    unsubscribing = asyncio.create_task(_unsubscribe(env["http"]))
    await _until(lambda: "POST subscriptions/remove" in main.paths(), "removal not sent")
    refused = await env["http"].post(
        "/market/knowledge/subscribe",
        params=TOKEN,
        json={"package_id": 42, "version": "1.0.0", "channel": "stable", "pack_id": "market-memes"},
    )
    assert refused.status_code == 409
    assert (await _unsubscribe(env["http"])).status_code == 409
    gate.set()
    assert (await unsubscribing).status_code == 200
    await _subscribe(env["http"])


async def test_unsubscribe_cancels_a_worker_that_is_still_downloading(env, monkeypatch):
    started = asyncio.Event()

    async def endless_download(descriptor):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(km, "_download_verified_artifact", endless_download)
    task_id = await _subscribe(env["http"])
    await started.wait()
    response = await _unsubscribe(env["http"])
    assert response.status_code == 200 and response.json()["kept"] == "not_installed"
    task = await _finished(env["http"], task_id)
    assert task["status"] == "cancelled" and task["error_code"] == "cancelled_by_unsubscribe"
    assert "POST subscriptions/apply" not in env["main"].paths()


async def test_unsubscribe_waits_for_an_apply_already_sent_and_names_its_release(env):
    main = env["main"]
    gate = asyncio.Event()

    async def slow_apply():
        await gate.wait()
        return STAGED

    main.on("POST", "subscriptions/apply", slow_apply)
    # Whatever the apply staged is not listed yet: the release must come from the task.
    main.on("GET", "subscriptions", _state())
    main.on("POST", "subscriptions/remove", {"ok": True, "removed": False, "kept": "not_installed"})
    task_id = await _subscribe(env["http"])
    await _until(lambda: "POST subscriptions/apply" in main.paths(), "apply not sent")
    unsubscribing = asyncio.create_task(_unsubscribe(env["http"]))
    await asyncio.sleep(0.05)
    # The removal must not overtake the apply.
    assert main.paths() == ["POST subscriptions/apply"]
    gate.set()
    response = await unsubscribing
    assert response.status_code == 200
    assert main.paths() == ["POST subscriptions/apply", "GET subscriptions", "POST subscriptions/remove"]
    assert main.bodies("subscriptions/remove") == [_removal(("1.0.0", SHA))]
    assert (await _finished(env["http"], task_id))["status"] == "cancelled"


async def test_an_apply_that_outlasts_the_budget_keeps_holding_its_package(env, monkeypatch):
    monkeypatch.setattr(km, "_UNSUBSCRIBE_BUDGET_SECONDS", 0.1)
    main = env["main"]
    gate = asyncio.Event()

    async def stuck_apply():
        await gate.wait()
        return STAGED

    main.on("POST", "subscriptions/apply", stuck_apply)
    main.on("GET", "subscriptions", _state())
    main.on("POST", "subscriptions/remove", {"ok": True, "removed": False, "kept": "not_installed"})
    await _subscribe(env["http"])
    await _until(lambda: "POST subscriptions/apply" in main.paths(), "apply not sent")
    response = await _unsubscribe(env["http"])
    assert response.status_code == 503 and response.json()["detail"]["retryable"] is True
    assert "POST subscriptions/remove" not in main.paths()
    # Its worker is gone, but the apply still holds the package: a retry must
    # not answer "not installed" and a new subscribe must not start meanwhile.
    again = await _unsubscribe(env["http"])
    assert again.status_code == 503
    assert (await env["http"].post(
        "/market/knowledge/subscribe",
        params=TOKEN,
        json={"package_id": 42, "version": "1.0.0", "channel": "stable", "pack_id": "market-memes"},
    )).status_code == 409
    assert "GET subscriptions" not in main.paths()
    gate.set()
    await _until(lambda: not km._applies, "apply still running")
    # Settled: what it staged is now the Memory Server's to report, and the
    # next unsubscribe removes it.
    main.on("GET", "subscriptions", _state(imports=[_import("queued")]))
    assert (await _unsubscribe(env["http"])).status_code == 200
    assert main.bodies("subscriptions/remove") == [_removal(("1.0.0", SHA))]
    assert km._active_package_tasks == {}


async def test_unsubscribe_refuses_a_pack_its_worker_did_not_resolve(env, monkeypatch):
    gate = asyncio.Event()

    async def blocked_download(descriptor):
        await gate.wait()
        return ARTIFACT

    monkeypatch.setattr(km, "_download_verified_artifact", blocked_download)
    await _subscribe(env["http"])
    response = await _unsubscribe(env["http"], pack_id="other-pack")
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "subscription_identity_mismatch"}
    gate.set()


async def test_reports_of_a_package_reach_the_market_in_order(env, monkeypatch):
    main = env["main"]
    gate = asyncio.Event()
    sent: list[str] = []

    async def slow_subscribe_report(descriptor, result):
        await gate.wait()  # e.g. refreshing the OAuth token
        sent.append("subscribe")

    async def unsubscribe_report(package_id, *, timeout=10.0):
        sent.append("unsubscribe")
        return True

    monkeypatch.setattr(km, "_report_subscription_best_effort", slow_subscribe_report)
    monkeypatch.setattr(km, "_report_unsubscribe_best_effort", unsubscribe_report)
    main.on("POST", "subscriptions/apply", STAGED)
    main.on("GET", "subscriptions", _state(imports=[_import("queued")]))
    main.on("POST", "subscriptions/remove", REMOVED)
    await _finished(env["http"], await _subscribe(env["http"]))
    assert (await _unsubscribe(env["http"])).status_code == 200
    await asyncio.sleep(0.02)
    assert sent == []  # the unsubscribe report waits for the subscribe report
    gate.set()
    await _until(lambda: len(sent) == 2, "reports not sent")
    assert sent == ["subscribe", "unsubscribe"]
    await _until(lambda: not km._reports, "report chain not cleared")


def test_a_task_whose_apply_still_runs_is_never_pruned(env, monkeypatch):
    monkeypatch.setattr(km, "_TASK_MAX_ENTRIES", 0)
    monkeypatch.setattr(km, "_TASK_TTL_SECONDS", 0)
    km._tasks["t1"] = {"task_id": "t1", "package_id": 42, "created_at": 0.0, "completed_at": 1.0}
    km._tasks["t2"] = {"task_id": "t2", "package_id": 43, "created_at": 0.0, "completed_at": 1.0}
    km._applies["t1"] = object()  # an apply left running by a cancelled worker
    km._cleanup_tasks()
    assert list(km._tasks) == ["t1"]
    km._applies.clear()


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
    payload = await km._main_call("POST", "subscriptions/remove", json_body={"pack_id": "x1"})
    assert payload["reason"] == reason
    await client.aclose()


# ── download ────────────────────────────────────────────────────────


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


RELEASE = "https://github.com/o/r/releases/download/v1/p.neko-knowledge.json"


def test_mirror_hosts_of_the_plugin_install_path_are_allowlisted():
    assert km._artifact_url_allowed(f"https://gh-proxy.com/{RELEASE}", final=True)
    assert not km._artifact_url_allowed(f"https://gh-proxy.evil.example/{RELEASE}", final=True)


async def test_download_sources_follow_the_speed_test(monkeypatch):
    async def measured():
        return (
            {"id": "gh-proxy-com", "url": "https://gh-proxy.com/", "available": True, "latency_ms": 50},
            {"id": "github-direct", "url": "https://github.com/", "available": True, "latency_ms": 200},
            {"id": "gh-proxy-org", "url": "https://gh-proxy.org/", "available": False, "latency_ms": None},
        )

    monkeypatch.setattr(km, "_measured_github_sources", measured)
    sources = await km._artifact_sources(RELEASE)
    assert sources[:2] == [f"https://gh-proxy.com/{RELEASE}", RELEASE]
    # Every allowlisted mirror is still tried, unmeasured ones after the measured.
    assert set(sources) == {RELEASE, *(f"{prefix}{RELEASE}" for prefix in km._GITHUB_MIRROR_PREFIXES)}
    assert len(sources) == len(set(sources))
    async def unmeasured():
        return ()

    # No usable test: GitHub itself right after the first mirror, not last.
    monkeypatch.setattr(km, "_measured_github_sources", unmeasured)
    fallback = await km._artifact_sources(RELEASE)
    assert fallback[:2] == [f"{km._GITHUB_MIRROR_PREFIXES[0]}{RELEASE}", RELEASE]
    assert set(fallback) == set(sources)
    # Not a GitHub Release asset: downloaded as published only.
    assert await km._artifact_sources("https://objects.githubusercontent.com/x.neko-knowledge.json") == [
        "https://objects.githubusercontent.com/x.neko-knowledge.json"
    ]


async def test_concurrent_workers_share_one_mirror_measurement(monkeypatch):
    km._mirror_cache.clear()
    calls: list[int] = []

    async def measure():
        calls.append(1)
        await asyncio.sleep(0.05)
        return ({"id": "github-direct", "url": "https://github.com/", "available": True, "latency_ms": 10},)

    monkeypatch.setattr(km, "_measure_github_proxy_sources", measure)
    results = await asyncio.gather(*(km._measured_github_sources() for _ in range(4)))
    assert len(calls) == 1
    assert all(result == results[0] for result in results)
    await km._measured_github_sources()  # still fresh: no new measurement
    assert len(calls) == 1
    km._mirror_cache.clear()


async def test_download_falls_through_failing_and_tampering_sources(monkeypatch):
    sources = [f"https://gh-proxy.com/{RELEASE}", f"https://gh-proxy.org/{RELEASE}", RELEASE]
    tried: list[str] = []

    async def fake_sources(url):
        return list(sources)

    async def fake_download(url, httpx_module):
        tried.append(url)
        if url == sources[0]:
            raise httpx.ConnectError("unreachable")
        if url == sources[1]:
            return ARTIFACT.replace(b"1", b"2")  # same size, other bytes
        return ARTIFACT

    monkeypatch.setattr(km, "_artifact_sources", fake_sources)
    monkeypatch.setattr(km, "_download", fake_download)
    descriptor = km.KnowledgeArtifactDescriptor(url=RELEASE, sha256=SHA, bytes=len(ARTIFACT))
    assert await km._download_verified_artifact(descriptor) == ARTIFACT
    assert tried == sources

    async def always_tampered(url, httpx_module):
        return ARTIFACT.replace(b"1", b"2")

    monkeypatch.setattr(km, "_download", always_tampered)
    with pytest.raises(km._TaskError) as excinfo:
        await km._download_verified_artifact(descriptor)
    assert excinfo.value.code == "artifact_hash_mismatch"


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


def _descriptor_client(monkeypatch, replies: list[int], seen: list[str | None]):
    """Serve the version descriptor through a mock transport, one status per request."""
    body = _descriptor().model_dump(mode="json")

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        status = replies.pop(0)
        return httpx.Response(status, json=body if status == 200 else {"detail": "x"})

    real_client = httpx.AsyncClient

    class Fake:
        AsyncClient = staticmethod(lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
        Timeout = httpx.Timeout
        HTTPError = httpx.HTTPError

    async def fake_httpx():
        return Fake

    monkeypatch.setattr(km, "ensure_httpx", fake_httpx)


def _tokens(monkeypatch, *, refreshed: str | None, refresh_fails: bool = False):
    calls: list[dict] = []

    async def ensure(**kwargs):
        calls.append(kwargs)
        if kwargs.get("force_refresh"):
            if refresh_fails:
                raise HTTPException(status_code=502, detail="auth service unavailable")
            return {"access_token": refreshed} if refreshed else None
        return {"access_token": "stale"}

    monkeypatch.setattr(km, "_ensure_valid_oauth_token", ensure)
    return calls


REQUEST = km.KnowledgeSubscribeRequest(package_id=42, version="1.0.0", channel="stable", pack_id="market-memes")


async def test_a_revoked_login_is_refreshed_once_for_the_descriptor(monkeypatch):
    seen: list[str | None] = []
    _descriptor_client(monkeypatch, [401, 200], seen)
    calls = _tokens(monkeypatch, refreshed="fresh")
    descriptor = await km._fetch_version_descriptor(REQUEST)
    assert descriptor.pack_id == "market-memes"
    assert seen == ["Bearer stale", "Bearer fresh"]
    assert calls[-1] == {"force_refresh": True, "rejected_access_token": "stale"}


async def test_without_a_fresh_login_the_descriptor_is_asked_for_anonymously(monkeypatch):
    seen: list[str | None] = []
    _descriptor_client(monkeypatch, [401, 200], seen)
    _tokens(monkeypatch, refreshed=None)
    assert (await km._fetch_version_descriptor(REQUEST)).package_id == 42
    assert seen == ["Bearer stale", None]


async def test_a_failing_refresh_still_asks_for_the_descriptor_anonymously(monkeypatch):
    seen: list[str | None] = []
    _descriptor_client(monkeypatch, [401, 200], seen)
    _tokens(monkeypatch, refreshed=None, refresh_fails=True)
    assert (await km._fetch_version_descriptor(REQUEST)).package_id == 42
    assert seen == ["Bearer stale", None]


async def test_a_descriptor_that_needs_a_login_says_so(monkeypatch):
    seen: list[str | None] = []
    _descriptor_client(monkeypatch, [401, 401], seen)
    _tokens(monkeypatch, refreshed=None)
    with pytest.raises(km._TaskError) as excinfo:
        await km._fetch_version_descriptor(REQUEST)
    assert excinfo.value.code == "market_auth_required" and excinfo.value.retryable is True
    assert len(seen) == 2  # one retry, not a loop


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
