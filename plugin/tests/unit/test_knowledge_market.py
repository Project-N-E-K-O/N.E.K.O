from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest
from fastapi import HTTPException

from knowledge.api import canonical_pack_bytes
from plugin.server.routes import knowledge_market as module


@pytest.fixture(autouse=True)
async def _clear_task_registries():
    module._tasks.clear()
    module._task_workers.clear()
    module._installation_mutations.clear()
    module._active_package_tasks.clear()
    module._unsubscribing_package_ids.clear()
    module._unsubscribe_settlements.clear()
    yield
    pending = tuple({
        *module._task_workers.values(),
        *module._installation_mutations.values(),
        *module._unsubscribe_settlements.values(),
    })
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    module._tasks.clear()
    module._task_workers.clear()
    module._installation_mutations.clear()
    module._active_package_tasks.clear()
    module._unsubscribing_package_ids.clear()
    module._unsubscribe_settlements.clear()


@pytest.mark.asyncio
async def test_installation_mutation_callback_logs_task_failure(monkeypatch):
    messages = []

    class _Logger:
        def error(self, *args, **kwargs):
            messages.append((args, kwargs))

    monkeypatch.setattr(module, "logger", _Logger())
    failure = RuntimeError("fixture failure")
    completed = asyncio.get_running_loop().create_future()
    completed.set_exception(failure)
    module._installation_mutations["failed-task"] = completed
    module._tasks["failed-task"] = {}

    module._installation_mutation_done("failed-task", completed)

    assert module._installation_mutations == {}
    assert len(messages) == 1
    assert messages[0][0] == ("knowledge installation mutation failed",)
    assert messages[0][1]["exc_info"][1] is failure
    assert module._tasks["failed-task"]["_installation_outcome"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected"),
    (("success", "accepted"), ("rejected", "rejected"), ("cancelled", "cancelled")),
)
async def test_installation_mutation_callback_does_not_log_expected_outcome(
    monkeypatch,
    outcome,
    expected,
):
    class _Logger:
        def error(self, *_args, **_kwargs):
            pytest.fail("expected outcome must not log")

    monkeypatch.setattr(module, "logger", _Logger())
    completed = asyncio.get_running_loop().create_future()
    if outcome == "success":
        completed.set_result({"ok": True})
    elif outcome == "rejected":
        completed.set_result({"ok": False, "reason": "fixture"})
    else:
        completed.cancel()

    module._tasks["expected-task"] = {}
    module._installation_mutation_done("expected-task", completed)
    assert module._tasks["expected-task"]["_installation_outcome"] == expected


def _pack():
    return {
        "schema_version": 1,
        "pack_id": "fixture-pack",
        "material_type": "knowledge",
        "source": {"name": "Fixture", "homepage": "", "license": "CC0-1.0"},
        "entries": [
            {
                "title": "fixture",
                "terms": {"alias": [], "recognition": []},
                "tags": [],
                "summary": "",
                "content": "fixture content",
            }
        ],
    }


def test_cleanup_bounds_terminal_tasks_without_removing_active(monkeypatch):
    monkeypatch.setattr(module, "_TASK_TTL_SECONDS", 10**9)
    now = module.time.time()
    for index in range(201):
        task_id = f"terminal-{index:03d}"
        module._tasks[task_id] = {
            "created_at": now + index,
            "completed_at": now + index + 1,
        }
    module._tasks["active"] = {
        "created_at": 10_000.0,
        "completed_at": None,
    }

    module._cleanup_tasks()

    assert len(module._tasks) == module._TASK_MAX_ENTRIES
    assert "active" in module._tasks
    assert "terminal-000" not in module._tasks
    assert "terminal-001" not in module._tasks
    assert "terminal-200" in module._tasks


def test_market_package_id_is_bounded_to_the_persisted_ascii_contract():
    assert module.KnowledgeSubscribeRequest(
        package_id=module.PROVIDER_PACKAGE_ID_MAX,
        version="1.0.0",
        pack_id="fixture-pack",
    ).package_id == module.PROVIDER_PACKAGE_ID_MAX
    with pytest.raises(ValueError):
        module.KnowledgeSubscribeRequest(
            package_id=module.PROVIDER_PACKAGE_ID_MAX + 1,
            version="1.0.0",
            pack_id="fixture-pack",
        )


@pytest.mark.asyncio
async def test_market_subscription_downloads_verifies_and_hands_off(monkeypatch):
    raw = canonical_pack_bytes(_pack())
    digest = hashlib.sha256(raw).hexdigest()
    request = module.KnowledgeSubscribeRequest(
        package_id=7,
        version="1.0.0",
        channel="stable",
        pack_id="fixture-pack",
    )
    descriptor = module.KnowledgeVersionDescriptor.model_validate(
        {
            "protocol_version": 1,
            "package_id": 7,
            "remote_id": "knowledge/fixture-pack",
            "pack_id": "fixture-pack",
            "material_type": "knowledge",
            "version": "1.0.0",
            "channel": "stable",
            "artifacts": {
                "knowledge": {
                    "url": "https://github.com/example/repo/releases/download/v1/fixture.neko-knowledge.json",
                    "sha256": digest,
                    "bytes": len(raw),
                },
                "index_manifest": None,
                "vectors": None,
            },
        }
    )
    captured = {}

    async def fake_fetch(_request):
        return descriptor

    async def fake_download(_descriptor, **_kwargs):
        return raw

    async def fake_subscription_main(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "job_id": "fixture-job",
            "pack_id": "fixture-pack",
            "state": "queued",
        }

    async def fake_main(method, path, **kwargs):
        captured.update(method=method, path=path, **kwargs)
        if path == "packs/jobs":
            return {
                "ok": True,
                "jobs": [
                    {
                        "job_id": "fixture-job",
                        "state": "active",
                        "retrieval_mode": "hybrid",
                        "indexed_percent": 100.0,
                    }
                ],
            }
        raise AssertionError(path)

    async def no_report(*_args):
        return None

    monkeypatch.setattr(module, "_fetch_version_descriptor", fake_fetch)
    monkeypatch.setattr(module, "_download_verified_artifact", fake_download)
    monkeypatch.setattr(module, "_main_subscription_request", fake_subscription_main)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(module, "_report_subscription_best_effort", no_report)
    module._tasks["fixture"] = {
        "status": "pending",
        "stage": "pending",
        "progress": 0.0,
        "message": "",
        "result": None,
        "error": None,
        "error_code": None,
        "completed_at": None,
    }

    await module._execute_subscription("fixture", request)

    assert module._tasks["fixture"]["status"] == "completed"
    assert captured["pack_raw"] == raw
    assert captured["manifest_raw"] is None
    assert captured["subscription"]["artifact_sha256"] == digest
    assert captured["subscription"]["provider_package_id"] == "7"
    assert captured["subscription"]["material_type"] == "knowledge"
    assert module._tasks["fixture"]["resolved_pack_id"] == "fixture-pack"
    result = module._tasks["fixture"]["result"]
    assert result["activation"]["state"] == "active"


@pytest.mark.asyncio
async def test_market_subscription_rejects_material_type_mismatch(monkeypatch):
    raw = canonical_pack_bytes(_pack())
    descriptor = module.KnowledgeVersionDescriptor.model_validate(
        {
            "protocol_version": 1,
            "package_id": 7,
            "remote_id": "knowledge/fixture-pack",
            "pack_id": "fixture-pack",
            "material_type": "corpus",
            "version": "1.0.0",
            "channel": "stable",
            "artifacts": {
                "knowledge": {
                    "url": "https://github.com/example/repo/releases/download/v1/fixture.neko-knowledge.json",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                },
                "index_manifest": None,
                "vectors": None,
            },
        }
    )

    async def fake_fetch(_request):
        return descriptor

    async def fake_download(_descriptor, **_kwargs):
        return raw

    monkeypatch.setattr(module, "_fetch_version_descriptor", fake_fetch)
    monkeypatch.setattr(module, "_download_verified_artifact", fake_download)
    module._tasks["material-type-mismatch"] = {
        "status": "pending",
        "stage": "pending",
        "progress": 0.0,
        "message": "",
        "result": None,
        "error": None,
        "error_code": None,
        "completed_at": None,
    }

    request = module.KnowledgeSubscribeRequest(
        package_id=7,
        version="1.0.0",
        channel="stable",
        pack_id="fixture-pack",
    )
    await module._execute_subscription("material-type-mismatch", request)

    task = module._tasks["material-type-mismatch"]
    assert task["status"] == "failed"
    assert task["error_code"] == "material_type_mismatch"


@pytest.mark.asyncio
async def test_market_subscription_rejects_hash_mismatch(monkeypatch):
    raw = canonical_pack_bytes(_pack())
    descriptor = module.KnowledgeArtifactDescriptor(
        url="https://github.com/example/repo/releases/download/v1/fixture.neko-knowledge.json",
        sha256="0" * 64,
        bytes=len(raw),
    )

    async def fake_download(_url, **_kwargs):
        return raw

    monkeypatch.setattr(module, "_download_artifact", fake_download)
    with pytest.raises(module._KnowledgeTaskError) as exc_info:
        await module._download_verified_artifact(
            descriptor,
            max_bytes=module.MAX_PACK_BYTES,
            required_suffix=".neko-knowledge.json",
        )
    assert exc_info.value.code == "artifact_hash_mismatch"


@pytest.mark.asyncio
async def test_artifact_download_validates_redirect_before_request(monkeypatch):
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://127.0.0.1/private"},
            request=request,
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    with pytest.raises(module._KnowledgeTaskError) as exc_info:
        await module._download_artifact(
            "https://github.com/example/repo/releases/download/v1/fixture.bin"
        )

    assert exc_info.value.code == "unsafe_artifact_redirect"
    assert requested == [
        "https://github.com/example/repo/releases/download/v1/fixture.bin"
    ]


@pytest.mark.asyncio
async def test_artifact_download_follows_validated_github_redirect(monkeypatch):
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.host == "github.com":
            return httpx.Response(
                302,
                headers={
                    "Location": "https://release-assets.githubusercontent.com/fixture.bin"
                },
                request=request,
            )
        return httpx.Response(200, content=b"verified", request=request)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    result = await module._download_artifact(
        "https://github.com/example/repo/releases/download/v1/fixture.bin"
    )

    assert result == b"verified"
    assert requested == [
        "https://github.com/example/repo/releases/download/v1/fixture.bin",
        "https://release-assets.githubusercontent.com/fixture.bin",
    ]


@pytest.mark.asyncio
async def test_subscriptions_are_single_flight_and_globally_bounded(monkeypatch):
    release = asyncio.Event()

    async def blocked(_task_id, _payload):
        await release.wait()

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_execute_subscription", blocked)

    first = await module.subscribe_knowledge_package(
        module.KnowledgeSubscribeRequest(
            package_id=1, version="1.0.0", pack_id="fixture-pack"
        ),
        token="fixture",
    )
    duplicate = await module.subscribe_knowledge_package(
        module.KnowledgeSubscribeRequest(
            package_id=1, version="1.0.0", pack_id="fixture-pack"
        ),
        token="fixture",
    )
    assert duplicate["task_id"] == first["task_id"]

    for package_id in (2, 3, 4):
        await module.subscribe_knowledge_package(
            module.KnowledgeSubscribeRequest(
                package_id=package_id,
                version="1.0.0",
                pack_id=f"fixture-pack-{package_id}",
            ),
            token="fixture",
        )
    with pytest.raises(HTTPException) as busy:
        await module.subscribe_knowledge_package(
            module.KnowledgeSubscribeRequest(
                package_id=5, version="1.0.0", pack_id="fixture-pack-5"
            ),
            token="fixture",
        )
    assert busy.value.status_code == 429
    assert len(module._task_workers) == 4

    release.set()
    await asyncio.gather(*tuple(module._task_workers.values()))
    await asyncio.sleep(0)
    assert module._task_workers == {}


def _installed_subscription(*, package_id: str = "7", legacy: bool = False):
    subscription = {
        "provider": "plugin-market",
        "remote_id": "knowledge/fixture-pack",
        "version": "1.0.0",
        "channel": "stable",
        "artifact_sha256": "a" * 64,
        "material_type": "knowledge",
    }
    if not legacy:
        subscription["provider_package_id"] = package_id
    return {"pack_id": "fixture-pack", "subscription": subscription}


async def _matching_fixture_descriptor(request):
    assert request == module.KnowledgeSubscribeRequest(
        package_id=7,
        version="1.0.0",
        channel="stable",
        pack_id="fixture-pack",
    )
    return module.KnowledgeVersionDescriptor.model_validate(
        {
            "protocol_version": 1,
            "package_id": 7,
            "remote_id": "knowledge/fixture-pack",
            "pack_id": "fixture-pack",
            "material_type": "knowledge",
            "version": "1.0.0",
            "channel": "stable",
            "artifacts": {
                "knowledge": {
                    "url": "https://github.com/example/repo/releases/download/v1/fixture.neko-knowledge.json",
                    "sha256": "a" * 64,
                    "bytes": 1,
                }
            },
        }
    )


@pytest.mark.asyncio
async def test_unsubscribe_uses_persisted_provider_identity(monkeypatch):
    calls = []

    async def fake_main(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return {"ok": True, "packs": [_installed_subscription()]}
        return {
            "ok": True,
            "removed_pack": True,
            "removed_entries": 1,
            "cancelled_jobs": 0,
        }

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(
        module, "_fetch_version_descriptor", _matching_fixture_descriptor
    )
    monkeypatch.setattr(
        module,
        "_report_unsubscribe_best_effort",
        lambda _package_id: asyncio.sleep(0),
    )

    result = await module.unsubscribe_knowledge_package(
        module.KnowledgeUnsubscribeRequest(package_id=7, pack_id="fixture-pack"),
        token="fixture",
    )

    assert result["ok"] is True
    method, path, kwargs = calls[-1]
    assert (method, path) == ("POST", "packs/remove")
    assert kwargs["json"] == {
        "pack_id": "fixture-pack",
        "expected_provider": "plugin-market",
        "expected_provider_package_id": "7",
        "expected_remote_id": "knowledge/fixture-pack",
    }
    # Removal runs on what is left of the shared settlement budget, never on a
    # fresh full timeout that could outlive the Main Server proxy.
    assert 0 < kwargs["timeout"] <= module._UNSUBSCRIBE_TOTAL_BUDGET_SECONDS


@pytest.mark.asyncio
async def test_unsubscribe_rejects_tampered_persisted_package_identity(monkeypatch):
    main_calls = []

    async def fake_main(method, path, **kwargs):
        main_calls.append((method, path, kwargs))
        assert (method, path) == ("GET", "packs")
        return {
            "ok": True,
            "packs": [_installed_subscription(package_id="8")],
        }

    async def descriptor_for_other_package(request):
        assert request.package_id == 8
        return module.KnowledgeVersionDescriptor.model_validate(
            {
                "protocol_version": 1,
                "package_id": 8,
                "remote_id": "knowledge/other-pack",
                "pack_id": "other-pack",
                "material_type": "knowledge",
                "version": "1.0.0",
                "channel": "stable",
                "artifacts": {
                    "knowledge": {
                        "url": "https://github.com/example/repo/releases/download/v1/other.neko-knowledge.json",
                        "sha256": "b" * 64,
                        "bytes": 1,
                    }
                },
            }
        )

    async def unexpected_report(_package_id):
        pytest.fail("unverified ownership must not be reported remotely")

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(
        module, "_fetch_version_descriptor", descriptor_for_other_package
    )
    monkeypatch.setattr(module, "_report_unsubscribe_best_effort", unexpected_report)

    with pytest.raises(HTTPException) as rejected:
        await module.unsubscribe_knowledge_package(
            module.KnowledgeUnsubscribeRequest(
                package_id=8,
                pack_id="fixture-pack",
            ),
            token="fixture",
        )

    assert rejected.value.detail["code"] == "subscription_ownership_unverifiable"
    assert [call[:2] for call in main_calls] == [("GET", "packs")]


@pytest.mark.asyncio
async def test_unsubscribe_preserves_not_found_without_settled_installation(
    monkeypatch,
):
    async def fake_main(method, _path, **_kwargs):
        if method == "GET":
            return {"ok": True, "packs": [_installed_subscription()]}
        return {"ok": False, "reason": "not_found"}

    async def unexpected_report(_package_id):
        pytest.fail("failed unsubscribe must not be reported remotely")

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(
        module, "_fetch_version_descriptor", _matching_fixture_descriptor
    )
    monkeypatch.setattr(module, "_report_unsubscribe_best_effort", unexpected_report)

    with pytest.raises(HTTPException) as rejected:
        await module.unsubscribe_knowledge_package(
            module.KnowledgeUnsubscribeRequest(
                package_id=7,
                pack_id="fixture-pack",
            ),
            token="fixture",
        )

    assert rejected.value.status_code == 409
    assert rejected.value.detail["code"] == "not_found"


@pytest.mark.asyncio
async def test_unsubscribe_never_uses_claimed_pack_id_as_authority(monkeypatch):
    async def fake_main(method, _path, **_kwargs):
        assert method == "GET"
        return {
            "ok": True,
            "packs": [
                {"pack_id": "local-pack", "subscription": None},
                _installed_subscription(),
            ],
        }

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_main_request", fake_main)

    with pytest.raises(HTTPException) as mismatch:
        await module.unsubscribe_knowledge_package(
            module.KnowledgeUnsubscribeRequest(package_id=7, pack_id="local-pack"),
            token="fixture",
        )

    assert mismatch.value.detail["code"] == "subscription_identity_mismatch"


@pytest.mark.asyncio
async def test_legacy_unsubscribe_fails_closed_when_market_is_unavailable(monkeypatch):
    async def fake_main(method, _path, **_kwargs):
        assert method == "GET"
        return {"ok": True, "packs": [_installed_subscription(legacy=True)]}

    async def unavailable(_request):
        raise module._KnowledgeTaskError("catalog_resolution_failed", "offline")

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(module, "_fetch_version_descriptor", unavailable)

    with pytest.raises(HTTPException) as rejected:
        await module.unsubscribe_knowledge_package(
            module.KnowledgeUnsubscribeRequest(package_id=7, pack_id="fixture-pack"),
            token="fixture",
        )

    assert rejected.value.detail["code"] == "subscription_ownership_unverifiable"


@pytest.mark.asyncio
async def test_legacy_unsubscribe_uses_matching_trusted_descriptor(monkeypatch):
    calls = []

    async def fake_main(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return {"ok": True, "packs": [_installed_subscription(legacy=True)]}
        return {"ok": True, "removed_pack": True, "removed_entries": 1}

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(
        module, "_fetch_version_descriptor", _matching_fixture_descriptor
    )
    monkeypatch.setattr(
        module,
        "_report_unsubscribe_best_effort",
        lambda _package_id: asyncio.sleep(0),
    )

    result = await module.unsubscribe_knowledge_package(
        module.KnowledgeUnsubscribeRequest(package_id=7, pack_id="fixture-pack"),
        token="fixture",
    )

    assert result["ok"] is True
    assert calls[-1][0:2] == ("POST", "packs/remove")


@pytest.mark.asyncio
async def test_unsubscribe_cancels_active_worker_and_records_terminal_state(monkeypatch):
    started = asyncio.Event()

    async def blocked(task_id, _payload):
        task = module._tasks[task_id]
        task["resolved_pack_id"] = "fixture-pack"
        task["resolved_remote_id"] = "knowledge/fixture-pack"
        task["stage"] = "installing"
        started.set()
        await asyncio.Event().wait()

    async def fake_main(method, path, **_kwargs):
        assert (method, path) == ("POST", "packs/remove")
        return {
            "ok": True,
            "removed_pack": False,
            "removed_entries": 0,
            "cancelled_jobs": 1,
        }

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_execute_subscription", blocked)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(
        module,
        "_report_unsubscribe_best_effort",
        lambda _package_id: asyncio.sleep(0),
    )
    created = await module.subscribe_knowledge_package(
        module.KnowledgeSubscribeRequest(
            package_id=7, version="1.0.0", pack_id="fixture-pack"
        ),
        token="fixture",
    )
    await started.wait()

    result = await module.unsubscribe_knowledge_package(
        module.KnowledgeUnsubscribeRequest(package_id=7, pack_id="fixture-pack"),
        token="fixture",
    )

    task = module._tasks[created["task_id"]]
    assert result["cancelled_jobs"] == 1
    assert task["status"] == task["stage"] == "cancelled"
    assert task["error_code"] == "cancelled_by_unsubscribe"
    assert task["completed_at"] is not None
    assert module._task_workers == {}
    assert module._active_package_tasks == {}


@pytest.mark.asyncio
async def test_unsubscribe_waits_for_installation_mutation_before_remove(monkeypatch):
    task_id = "installing-task"
    apply_started = asyncio.Event()
    allow_apply = asyncio.Event()
    apply_finished = asyncio.Event()
    order: list[str] = []

    async def installation_mutation():
        apply_started.set()
        await allow_apply.wait()
        order.append("apply")
        apply_finished.set()
        return {"ok": True, "job_id": "fixture-pack-0123456789ab"}

    mutation = asyncio.create_task(installation_mutation())
    module._installation_mutations[task_id] = mutation
    mutation.add_done_callback(
        lambda completed: module._installation_mutation_done(task_id, completed)
    )

    async def subscription_worker():
        await asyncio.shield(mutation)

    worker = asyncio.create_task(subscription_worker())
    task = {
        "task_id": task_id,
        "package_id": 7,
        "requested_pack_id": "fixture-pack",
        "resolved_pack_id": "fixture-pack",
        "resolved_remote_id": "knowledge/fixture-pack",
        "status": "installing",
        "stage": "installing",
        "created_at": 1.0,
        "completed_at": None,
    }
    module._tasks[task_id] = task
    module._task_workers[task_id] = worker
    module._active_package_tasks[7] = task_id
    worker.add_done_callback(
        lambda completed: module._subscription_done(task_id, 7, completed)
    )

    async def fake_main(method, path, **_kwargs):
        assert apply_finished.is_set()
        order.append("remove")
        assert (method, path) == ("POST", "packs/remove")
        return {
            "ok": True,
            "removed_pack": False,
            "removed_entries": 0,
            "cancelled_jobs": 1,
        }

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(
        module,
        "_report_unsubscribe_best_effort",
        lambda _package_id: asyncio.sleep(0),
    )
    await apply_started.wait()
    unsubscribe = asyncio.create_task(
        module.unsubscribe_knowledge_package(
            module.KnowledgeUnsubscribeRequest(
                package_id=7,
                pack_id="fixture-pack",
            ),
            token="fixture",
        )
    )
    await asyncio.sleep(0)

    assert not unsubscribe.done()
    assert order == []
    allow_apply.set()
    result = await unsubscribe

    assert result["cancelled_jobs"] == 1
    assert order == ["apply", "remove"]
    assert module._installation_mutations == {}


@pytest.mark.asyncio
async def test_cancelled_unsubscribe_retains_guard_until_installation_settles(
    monkeypatch,
):
    task_id = "cancelled-unsubscribe"
    apply_started = asyncio.Event()
    allow_apply = asyncio.Event()
    worker_cancelled = asyncio.Event()
    order: list[str] = []

    async def installation_mutation():
        apply_started.set()
        await allow_apply.wait()
        order.append("apply")
        return {"ok": True, "job_id": "fixture-pack-0123456789ab"}

    mutation = asyncio.create_task(installation_mutation())
    module._installation_mutations[task_id] = mutation
    mutation.add_done_callback(
        lambda completed: module._installation_mutation_done(task_id, completed)
    )

    async def subscription_worker():
        try:
            await asyncio.shield(mutation)
        except asyncio.CancelledError:
            worker_cancelled.set()
            raise

    worker = asyncio.create_task(subscription_worker())
    module._tasks[task_id] = {
        "task_id": task_id,
        "package_id": 7,
        "requested_pack_id": "fixture-pack",
        "resolved_pack_id": "fixture-pack",
        "resolved_remote_id": "knowledge/fixture-pack",
        "status": "installing",
        "stage": "installing",
        "created_at": 1.0,
        "completed_at": None,
    }
    module._task_workers[task_id] = worker
    module._active_package_tasks[7] = task_id
    worker.add_done_callback(
        lambda completed: module._subscription_done(task_id, 7, completed)
    )

    async def fake_main(method, path, **_kwargs):
        order.append("remove")
        assert (method, path) == ("POST", "packs/remove")
        return {
            "ok": True,
            "removed_pack": True,
            "removed_entries": 1,
            "cancelled_jobs": 0,
        }

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(
        module,
        "_report_unsubscribe_best_effort",
        lambda _package_id: asyncio.sleep(0),
    )
    await apply_started.wait()
    request = module.KnowledgeUnsubscribeRequest(
        package_id=7,
        pack_id="fixture-pack",
    )
    unsubscribe = asyncio.create_task(
        module.unsubscribe_knowledge_package(request, token="fixture")
    )
    await worker_cancelled.wait()
    settlement = module._unsubscribe_settlements[7]

    unsubscribe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await unsubscribe

    assert module._unsubscribing_package_ids == {7}
    assert module._unsubscribe_settlements == {7: settlement}
    with pytest.raises(HTTPException) as conflict:
        await module.subscribe_knowledge_package(
            module.KnowledgeSubscribeRequest(
                package_id=7,
                version="2.0.0",
                pack_id="fixture-pack",
            ),
            token="fixture",
        )
    assert conflict.value.status_code == 409

    allow_apply.set()
    result = await settlement
    await asyncio.sleep(0)

    assert result["removed_pack"] is True
    assert order == ["apply", "remove"]
    assert module._unsubscribing_package_ids == set()
    assert module._unsubscribe_settlements == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_outcome", ("rejected", "failed"))
async def test_unsubscribe_reconciles_installation_without_durable_state(
    monkeypatch,
    mutation_outcome,
):
    task_id = f"installing-{mutation_outcome}"
    apply_started = asyncio.Event()
    allow_apply = asyncio.Event()

    async def installation_mutation():
        apply_started.set()
        await allow_apply.wait()
        if mutation_outcome == "failed":
            raise RuntimeError("fixture connection failure")
        return {"ok": False, "reason": "invalid_pack"}

    mutation = asyncio.create_task(installation_mutation())
    module._installation_mutations[task_id] = mutation
    mutation.add_done_callback(
        lambda completed: module._installation_mutation_done(task_id, completed)
    )

    async def subscription_worker():
        await asyncio.shield(mutation)

    worker = asyncio.create_task(subscription_worker())
    task = {
        "task_id": task_id,
        "package_id": 7,
        "requested_pack_id": "fixture-pack",
        "resolved_pack_id": "fixture-pack",
        "resolved_remote_id": "knowledge/fixture-pack",
        "status": "installing",
        "stage": "installing",
        "created_at": 1.0,
        "completed_at": None,
    }
    module._tasks[task_id] = task
    module._task_workers[task_id] = worker
    module._active_package_tasks[7] = task_id
    worker.add_done_callback(
        lambda completed: module._subscription_done(task_id, 7, completed)
    )
    main_calls = []

    async def fake_main(method, path, **_kwargs):
        main_calls.append((method, path))
        assert mutation_outcome == "failed"
        return {"ok": False, "reason": "not_found"}

    reports = []

    async def report(package_id):
        reports.append(package_id)

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(module, "_report_unsubscribe_best_effort", report)
    await apply_started.wait()
    unsubscribe = asyncio.create_task(
        module.unsubscribe_knowledge_package(
            module.KnowledgeUnsubscribeRequest(
                package_id=7,
                pack_id="fixture-pack",
            ),
            token="fixture",
        )
    )
    await asyncio.sleep(0)
    allow_apply.set()

    result = await unsubscribe

    assert result == {
        "ok": True,
        "cancelled": True,
        "removed": False,
        "removed_pack": False,
        "removed_entries": 0,
        "cancelled_jobs": 0,
    }
    assert main_calls == (
        [] if mutation_outcome == "rejected" else [("POST", "packs/remove")]
    )
    assert reports == [7]
    assert task["preinstall_cancelled"] is True


@pytest.mark.asyncio
async def test_unsubscribe_preinstall_cancellation_is_idempotent_success(monkeypatch):
    started = asyncio.Event()

    async def blocked(_task_id, _payload):
        started.set()
        await asyncio.Event().wait()

    async def unexpected_main(*_args, **_kwargs):
        raise AssertionError("pre-install cancellation must not inspect local artifacts")

    reports = []

    async def report(package_id):
        reports.append(package_id)

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_execute_subscription", blocked)
    monkeypatch.setattr(module, "_main_request", unexpected_main)
    monkeypatch.setattr(module, "_report_unsubscribe_best_effort", report)
    await module.subscribe_knowledge_package(
        module.KnowledgeSubscribeRequest(
            package_id=7,
            version="1.0.0",
            pack_id="fixture-pack",
        ),
        token="fixture",
    )
    await started.wait()
    request = module.KnowledgeUnsubscribeRequest(
        package_id=7,
        pack_id="fixture-pack",
    )

    first = await module.unsubscribe_knowledge_package(request, token="fixture")
    second = await module.unsubscribe_knowledge_package(request, token="fixture")

    assert first == second == {
        "ok": True,
        "cancelled": True,
        "removed": False,
        "removed_pack": False,
        "removed_entries": 0,
        "cancelled_jobs": 0,
    }
    assert reports == [7, 7]


@pytest.mark.asyncio
async def test_unsubscribe_old_cancellation_does_not_override_new_subscription(
    monkeypatch,
):
    module._tasks["old-cancel"] = {
        "task_id": "old-cancel",
        "package_id": 7,
        "requested_pack_id": "fixture-pack",
        "preinstall_cancelled": True,
        "created_at": 1.0,
        "completed_at": 2.0,
    }
    module._tasks["new-completed"] = {
        "task_id": "new-completed",
        "package_id": 7,
        "requested_pack_id": "fixture-pack",
        "resolved_pack_id": "fixture-pack",
        "resolved_remote_id": "knowledge/fixture-pack",
        "status": "completed",
        "stage": "completed",
        "created_at": 3.0,
        "completed_at": 4.0,
    }
    calls = []

    async def fake_main(method, path, **kwargs):
        calls.append((method, path, kwargs))
        assert (method, path) == ("POST", "packs/remove")
        return {
            "ok": True,
            "removed_pack": True,
            "removed_entries": 1,
            "cancelled_jobs": 0,
        }

    reports = []

    async def report(package_id):
        reports.append(package_id)

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(module, "_report_unsubscribe_best_effort", report)

    result = await module.unsubscribe_knowledge_package(
        module.KnowledgeUnsubscribeRequest(
            package_id=7,
            pack_id="fixture-pack",
        ),
        token="fixture",
    )

    assert result["removed_pack"] is True
    assert len(calls) == 1
    method, path, kwargs = calls[0]
    assert (method, path) == ("POST", "packs/remove")
    assert kwargs["json"] == {
        "pack_id": "fixture-pack",
        "expected_provider": "plugin-market",
        "expected_provider_package_id": "7",
        "expected_remote_id": "knowledge/fixture-pack",
    }
    assert 0 < kwargs["timeout"] <= module._UNSUBSCRIBE_TOTAL_BUDGET_SECONDS
    assert reports == [7]


@pytest.mark.asyncio
async def test_unsubscribe_corrupt_latest_task_timestamp_uses_durable_registry(
    monkeypatch,
):
    module._tasks["old-cancel"] = {
        "task_id": "old-cancel",
        "package_id": 7,
        "requested_pack_id": "fixture-pack",
        "preinstall_cancelled": True,
        "created_at": 1.0,
        "completed_at": 2.0,
    }
    module._tasks["corrupt-latest"] = {
        "task_id": "corrupt-latest",
        "package_id": 7,
        "requested_pack_id": "fixture-pack",
        "preinstall_cancelled": True,
        "created_at": "not-a-time",
        "completed_at": 4.0,
    }
    calls = []

    async def fake_main(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return {"ok": True, "packs": [_installed_subscription()]}
        return {
            "ok": True,
            "removed_pack": True,
            "removed_entries": 1,
            "cancelled_jobs": 0,
        }

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(
        module, "_fetch_version_descriptor", _matching_fixture_descriptor
    )
    monkeypatch.setattr(
        module,
        "_report_unsubscribe_best_effort",
        lambda _package_id: asyncio.sleep(0),
    )

    result = await module.unsubscribe_knowledge_package(
        module.KnowledgeUnsubscribeRequest(
            package_id=7,
            pack_id="fixture-pack",
        ),
        token="fixture",
    )

    assert result["removed_pack"] is True
    assert [call[:2] for call in calls] == [
        ("GET", "packs"),
        ("POST", "packs/remove"),
    ]


@pytest.mark.asyncio
async def test_unsubscribe_out_of_order_task_times_use_durable_registry(monkeypatch):
    module._tasks["newer-time-first"] = {
        "task_id": "newer-time-first",
        "package_id": 7,
        "requested_pack_id": "fixture-pack",
        "preinstall_cancelled": True,
        "created_at": 5.0,
    }
    module._tasks["older-time-last"] = {
        "task_id": "older-time-last",
        "package_id": 7,
        "requested_pack_id": "fixture-pack",
        "preinstall_cancelled": True,
        "created_at": 3.0,
    }
    calls = []

    async def fake_main(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return {"ok": True, "packs": [_installed_subscription()]}
        return {"ok": True, "removed_pack": True, "removed_entries": 1}

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(
        module, "_fetch_version_descriptor", _matching_fixture_descriptor
    )
    monkeypatch.setattr(
        module,
        "_report_unsubscribe_best_effort",
        lambda _package_id: asyncio.sleep(0),
    )

    result = await module.unsubscribe_knowledge_package(
        module.KnowledgeUnsubscribeRequest(
            package_id=7,
            pack_id="fixture-pack",
        ),
        token="fixture",
    )

    assert result["removed_pack"] is True
    assert [call[:2] for call in calls] == [
        ("GET", "packs"),
        ("POST", "packs/remove"),
    ]


@pytest.mark.asyncio
async def test_unsubscribe_does_not_cancel_wrong_preinstall_identity(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked(_task_id, _payload):
        started.set()
        await release.wait()

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_execute_subscription", blocked)
    await module.subscribe_knowledge_package(
        module.KnowledgeSubscribeRequest(
            package_id=7,
            version="1.0.0",
            pack_id="fixture-pack",
        ),
        token="fixture",
    )
    await started.wait()

    with pytest.raises(HTTPException) as mismatch:
        await module.unsubscribe_knowledge_package(
            module.KnowledgeUnsubscribeRequest(
                package_id=7,
                pack_id="other-pack",
            ),
            token="fixture",
        )

    assert mismatch.value.detail["code"] == "subscription_identity_mismatch"
    assert len(module._task_workers) == 1
    release.set()
    await asyncio.gather(*tuple(module._task_workers.values()))


@pytest.mark.asyncio
async def test_subscribe_conflicts_while_unsubscribe_is_reserved(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fake_main(method, _path, **_kwargs):
        if method == "GET":
            entered.set()
            await release.wait()
            return {"ok": True, "packs": [_installed_subscription()]}
        return {"ok": True, "removed_pack": True, "removed_entries": 1}

    monkeypatch.setattr(module, "_verify_bridge_token", lambda _token: None)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(
        module, "_fetch_version_descriptor", _matching_fixture_descriptor
    )
    monkeypatch.setattr(
        module,
        "_report_unsubscribe_best_effort",
        lambda _package_id: asyncio.sleep(0),
    )
    removing = asyncio.create_task(
        module.unsubscribe_knowledge_package(
            module.KnowledgeUnsubscribeRequest(
                package_id=7,
                pack_id="fixture-pack",
            ),
            token="fixture",
        )
    )
    await entered.wait()

    with pytest.raises(HTTPException) as conflict:
        await module.subscribe_knowledge_package(
            module.KnowledgeSubscribeRequest(
                package_id=7, version="1.0.0", pack_id="fixture-pack"
            ),
            token="fixture",
        )

    assert conflict.value.status_code == 409
    release.set()
    await removing
    assert module._unsubscribing_package_ids == set()


@pytest.mark.asyncio
async def test_artifact_download_enforces_total_wall_clock_timeout(monkeypatch):
    class EndlessSlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.005)
                yield b"x"

        async def aclose(self):
            return None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=EndlessSlowStream(), request=request)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(module, "_ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    with pytest.raises(module._KnowledgeTaskError) as exc_info:
        await module._download_artifact(
            "https://github.com/example/repo/releases/download/v1/slow.bin",
            max_bytes=1024,
        )

    assert exc_info.value.code == "download_timeout"


@pytest.mark.asyncio
async def test_verified_artifact_maps_invalid_initial_url():
    descriptor = module.KnowledgeArtifactDescriptor(
        url="https://example.invalid/fixture.neko-knowledge.json",
        sha256="0" * 64,
        bytes=1,
    )

    with pytest.raises(module._KnowledgeTaskError) as exc_info:
        await module._download_verified_artifact(
            descriptor,
            max_bytes=module.MAX_PACK_BYTES,
            required_suffix=".neko-knowledge.json",
        )

    assert exc_info.value.code == "unsafe_artifact_url"


@pytest.mark.asyncio
async def test_market_task_waits_for_staged_pack_activation(monkeypatch):
    calls = 0
    task = {"stage": "installing", "progress": 0.75, "message": ""}

    async def fake_main(_method, _path, **_kwargs):
        nonlocal calls
        calls += 1
        state = "embedding" if calls == 1 else "active"
        return {
            "ok": True,
            "jobs": [
                {
                    "job_id": "fixture-job",
                    "state": state,
                    "indexed_percent": 50.0 if state == "embedding" else 100.0,
                }
            ],
        }

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)

    result = await module._wait_for_pack_job(
        task,
        job_id="fixture-job",
    )

    assert calls == 2
    assert task["stage"] == "indexing"
    assert task["progress"] == pytest.approx(0.895)
    assert result["state"] == "active"


@pytest.mark.asyncio
async def test_market_task_retries_transient_job_poll_failure(monkeypatch):
    calls = 0
    task = {"stage": "installing", "progress": 0.75, "message": ""}

    async def fake_main(_method, _path, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise module._KnowledgeTaskError(
                "main_server_unavailable",
                "Main Server unavailable",
            )
        return {
            "ok": True,
            "jobs": [{"job_id": "fixture-job", "state": "active"}],
        }

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)

    result = await module._wait_for_pack_job(task, job_id="fixture-job")

    assert calls == 2
    assert result["state"] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "content", "expected_code"),
    (
        (404, b'{"detail":"missing"}', "main_server_rejected"),
        (200, b"{", "main_server_invalid_response"),
    ),
)
async def test_main_request_classifies_permanent_poll_failures(
    monkeypatch,
    status_code,
    content,
    expected_code,
):
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            content=content,
            request=request,
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    with pytest.raises(module._KnowledgeTaskError) as exc_info:
        await module._main_request("GET", "packs/jobs")

    assert exc_info.value.code == expected_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code",
    ("main_server_rejected", "main_server_invalid_response"),
)
async def test_market_task_does_not_retry_permanent_poll_failure(
    monkeypatch,
    error_code,
):
    calls = 0
    task = {"stage": "installing", "progress": 0.75, "message": ""}

    async def fake_main(_method, _path, **_kwargs):
        nonlocal calls
        calls += 1
        raise module._KnowledgeTaskError(error_code, "permanent failure")

    async def unexpected_sleep(_seconds):
        pytest.fail("permanent poll failures must not be retried")

    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(module.asyncio, "sleep", unexpected_sleep)

    with pytest.raises(module._KnowledgeTaskError) as exc_info:
        await module._wait_for_pack_job(task, job_id="fixture-job")

    assert calls == 1
    assert exc_info.value.code == error_code


@pytest.mark.asyncio
async def test_market_task_stops_polling_when_staged_job_is_degraded(monkeypatch):
    task = {"stage": "installing", "progress": 0.75, "message": ""}

    async def fake_main(_method, _path, **_kwargs):
        return {
            "ok": True,
            "jobs": [{"job_id": "fixture-job", "state": "degraded"}],
        }

    monkeypatch.setattr(module, "_main_request", fake_main)

    with pytest.raises(module._KnowledgeTaskError) as exc_info:
        await module._wait_for_pack_job(task, job_id="fixture-job")

    assert exc_info.value.code == "job_degraded"


def test_connect_timeout_never_outlives_the_request_budget(monkeypatch):
    """httpx treats connect independently of the overall timeout.

    With a deadline-derived budget a fixed connect=2.0 would let the request keep
    connecting after the budget it was given is already gone, which is exactly
    what the shared deadline exists to prevent.
    """
    import httpx

    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    class _FakeClient:
        def __init__(self, *, timeout, trust_env):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def request(self, *_args, **_kwargs):
            return _FakeResponse()

    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeClient)

    asyncio.run(module._main_request("POST", "packs/remove", json={}, timeout=0.5))
    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect <= 0.5

    # A generous budget still gets the ordinary connect ceiling, not the whole
    # request timeout.
    asyncio.run(module._main_request("POST", "packs/remove", json={}, timeout=40.0))
    assert captured["timeout"].connect == module._CONNECT_TIMEOUT_SECONDS


def test_unsubscribe_reports_busy_when_the_budget_is_already_spent(monkeypatch):
    """An exhausted budget must fail as a budget failure, not a transport error."""
    calls: list[tuple] = []

    async def _fake_main_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True}

    monkeypatch.setattr(module, "_main_request", _fake_main_request)
    async def _no_active_subscription(*_args, **_kwargs):
        return None

    async def _owned(*_args, **_kwargs):
        return "fixture-pack", "knowledge/fixture-pack"

    monkeypatch.setattr(module, "_cancel_active_subscription", _no_active_subscription)
    monkeypatch.setattr(module, "_resolve_owned_subscription", _owned)

    payload = module.KnowledgeUnsubscribeRequest(package_id=7, pack_id="fixture-pack")
    loop = asyncio.new_event_loop()
    try:
        past_deadline = loop.time() - 1.0
        with pytest.raises(HTTPException) as excinfo:
            loop.run_until_complete(
                module._settle_knowledge_unsubscribe(payload, deadline=past_deadline)
            )
    finally:
        loop.close()

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["code"] == "knowledge_installation_busy"
    assert calls == [], "no removal request may be sent once the budget is gone"
