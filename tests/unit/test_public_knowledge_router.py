from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from knowledge.api import KnowledgeEntry, KnowledgeStore, open_knowledge
from knowledge.chunking import derive_knowledge_chunks
from knowledge.packs import validate_pack
from knowledge.prebuilt_index import (
    PREBUILT_DIMENSIONS,
    PREBUILT_MODEL_ID,
    build_prebuilt_index_artifacts,
)
from knowledge.subscriptions import canonical_pack_bytes
from knowledge.store import KnowledgeStoreError
from tests.fake_clock import patch_module_clock


@pytest.fixture(autouse=True)
async def _clear_pack_removal_tasks():
    import main_routers.public_knowledge_router as module

    module._pack_removal_tasks.clear()
    yield
    tasks = tuple(module._pack_removal_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    module._pack_removal_tasks.clear()


def _removal_request() -> dict[str, str]:
    return {
        "pack_id": "market-fixture",
        "expected_provider": "plugin-market",
        "expected_provider_package_id": "7",
        "expected_remote_id": "knowledge/market-fixture",
    }


def test_removal_operation_registry_rejects_bool_timestamps(tmp_path):
    from knowledge.removal_operations import (
        KnowledgeRemovalOperationError,
        begin_removal_operation,
        get_removal_operation,
    )

    operation_id = "remove-operation-bool-time-0001"
    begin_removal_operation(tmp_path, operation_id, _removal_request())
    path = tmp_path / "pack-remove-operations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["operations"][operation_id]["updated_at"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        KnowledgeRemovalOperationError,
        match="knowledge_removal_operation_registry_invalid",
    ):
        get_removal_operation(tmp_path, operation_id)


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_removal_operation_registry_rejects_non_finite_timestamps(
    tmp_path, field, constant
):
    from knowledge.removal_operations import (
        KnowledgeRemovalOperationError,
        begin_removal_operation,
        get_removal_operation,
    )

    operation_id = "remove-operation-nan-time-0001"
    begin_removal_operation(tmp_path, operation_id, _removal_request())
    path = tmp_path / "pack-remove-operations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["operations"][operation_id][field] = float(constant.lower().replace("infinity", "inf"))
    # json.dumps writes the bare NaN/Infinity constants that json.loads accepts.
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert constant in path.read_text(encoding="utf-8")

    with pytest.raises(
        KnowledgeRemovalOperationError,
        match="knowledge_removal_operation_registry_invalid",
    ):
        get_removal_operation(tmp_path, operation_id)


def test_removal_operation_registry_bounds_pending_records(monkeypatch, tmp_path):
    import knowledge.removal_operations as operations

    monkeypatch.setattr(operations, "MAX_PENDING_REMOVAL_OPERATIONS", 1)
    now = [100.0]
    patch_module_clock(monkeypatch, operations, time=lambda: now[0])
    first_id = "remove-operation-pending-cap-0001"
    second_id = "remove-operation-pending-cap-0002"
    operations.begin_removal_operation(tmp_path, first_id, _removal_request())

    with pytest.raises(
        operations.KnowledgeRemovalOperationError,
        match="knowledge_removal_operation_registry_full",
    ):
        operations.begin_removal_operation(tmp_path, second_id, _removal_request())

    now[0] += operations.PENDING_REMOVAL_OPERATION_TTL_SECONDS + 1
    operations.begin_removal_operation(tmp_path, second_id, _removal_request())
    assert operations.get_removal_operation(tmp_path, first_id)["result"] == {
        "ok": False,
        "reason": "removal_operation_expired",
    }
    assert operations.get_removal_operation(tmp_path, second_id)["status"] == "pending"


def test_pending_removal_operation_response_is_not_successful():
    import main_routers.public_knowledge_router as module

    response = module._removal_operation_response(
        {
            "operation_id": "remove-operation-pending-0001",
            "status": "pending",
            "result": None,
        }
    )

    assert response == {
        "ok": False,
        "reason": "removal_in_progress",
        "operation_id": "remove-operation-pending-0001",
        "operation_status": "pending",
    }


def test_pack_setting_surfaces_registry_recovery(monkeypatch, tmp_path):
    import main_routers.public_knowledge_router as module
    from knowledge.packs import KnowledgePackRecoveryRequiredError

    async def recovery_required(*_args, **_kwargs):
        raise KnowledgePackRecoveryRequiredError("pack_registry_recovery_required")

    monkeypatch.setattr(module, "run_knowledge_writer", recovery_required)
    with _client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/api/public-knowledge/packs/auto-context",
            json={"pack_id": "market-fixture", "enabled": True},
        )

    assert response.json() == {
        "ok": False,
        "reason": "pack_registry_recovery_required",
    }


@pytest.mark.parametrize(
    ("path", "payload"),
    (
        ("/api/public-knowledge/packs/jobs/cancel", {"job_id": "fixture-job"}),
        ("/api/public-knowledge/packs/jobs/discard", {"job_id": "fixture-job"}),
        (
            "/api/public-knowledge/packs/auto-context",
            {"pack_id": "market-fixture", "enabled": True},
        ),
        (
            "/api/public-knowledge/packs/index-policy",
            {"pack_id": "market-fixture", "local_embedding_enabled": True},
        ),
        (
            "/api/public-knowledge/packs/material-type",
            {"pack_id": "market-fixture", "material_type": "knowledge"},
        ),
    ),
)
def test_pack_mutations_surface_writer_shutdown(monkeypatch, tmp_path, path, payload):
    import main_routers.public_knowledge_router as module
    from knowledge.mutation_runtime import KnowledgeMutationAdmissionClosed

    async def admission_closed(*_args, **_kwargs):
        raise KnowledgeMutationAdmissionClosed("knowledge_mutation_stopping")

    monkeypatch.setattr(module, "run_knowledge_writer", admission_closed)
    with _client(monkeypatch, tmp_path) as client:
        response = client.post(path, json=payload)

    assert response.json() == {
        "ok": False,
        "reason": "knowledge_mutation_stopping",
    }


@pytest.mark.asyncio
async def test_resumed_remove_reports_registry_recovery(monkeypatch, tmp_path):
    import main_routers.public_knowledge_router as module
    from knowledge.packs import KnowledgePackRecoveryRequiredError

    async def recovery_required(*_args, **_kwargs):
        raise KnowledgePackRecoveryRequiredError("pack_registry_recovery_required")

    monkeypatch.setattr(module, "run_knowledge_writer", recovery_required)
    result = await module._remove_pack_once(
        SimpleNamespace(
            knowledge_root=tmp_path,
            cancel_and_remove_pack=lambda *_args, **_kwargs: None,
        ),
        _removal_request(),
        resumed=True,
    )

    assert result == {"ok": False, "reason": "pack_registry_recovery_required"}


def _entry(title: str, source: str, *, summary: str = "A compact summary") -> KnowledgeEntry:
    return KnowledgeEntry(
        title=title,
        terms={"alias": (), "recognition": ()},
        tags=(source,),
        summary=summary,
        content="Meaning\n- A typical use",
    )


def _pack(*, pack_id="market-fixture", material_type="knowledge") -> dict:
    return {
        "schema_version": 1,
        "pack_id": pack_id,
        "material_type": material_type,
        "source": {"name": pack_id, "homepage": "", "license": "CC0-1.0"},
        "entries": [
            {
                "title": f"{pack_id} phrase",
                "terms": {"alias": [], "recognition": []},
                "tags": [],
                "summary": "Market meaning",
                "content": "Meaning\n- Market use",
            }
        ],
    }


def _client(monkeypatch, tmp_path) -> TestClient:
    import main_routers.public_knowledge_router as module

    monkeypatch.setattr(
        module,
        "get_config_manager",
        lambda: SimpleNamespace(knowledge_dir=tmp_path),
    )
    monkeypatch.setattr(module, "_validate_mutation", lambda *_args, **_kwargs: None)
    app = FastAPI()
    app.include_router(module.router)
    return TestClient(app)


def _prebuilt(pack_payload: dict):
    raw = canonical_pack_bytes(pack_payload)
    pack = validate_pack(pack_payload)
    chunks = tuple(
        chunk
        for entry in pack.entries
        for chunk in derive_knowledge_chunks(
            entry,
            entry_key=f"{entry.source_tag}:{entry.title}",
        )
    )
    vector = np.ones(PREBUILT_DIMENSIONS, dtype="<f2").tobytes()
    artifacts = build_prebuilt_index_artifacts(
        raw,
        tuple(
            {
                "chunk_id": chunk.chunk_id,
                "content_hash": chunk.content_hash,
                "model_id": PREBUILT_MODEL_ID,
                "dimensions": PREBUILT_DIMENSIONS,
                "embedding": vector,
            }
            for chunk in chunks
        ),
    )
    return raw, artifacts


def test_management_api_exposes_one_store(monkeypatch, tmp_path):
    service = open_knowledge(tmp_path)
    store = KnowledgeStore(service.database_path())
    store.upsert(_entry("knowledge fixture", "source:chime"))
    store.upsert(_entry("corpus fixture", "source:corpora"))
    client = _client(monkeypatch, tmp_path)

    status = client.get("/api/public-knowledge/status").json()
    listing = client.get("/api/public-knowledge/entries", params={"limit": 10}).json()
    detail = client.get(
        "/api/public-knowledge/entry",
        params={"source": "corpora", "title": "corpus fixture"},
    ).json()

    assert status["status"]["entries"] == 2
    assert {item["title"] for item in listing["items"]} == {
        "knowledge fixture",
        "corpus fixture",
    }
    assert listing["items"][0]["content_preview"] == "Meaning - A typical use"
    assert detail["entry"]["content"] == "Meaning\n- A typical use"


def test_catalog_source_metadata_loads_once_off_request_thread(monkeypatch, tmp_path):
    import main_routers.public_knowledge_router as module

    service = open_knowledge(tmp_path)
    service.install_pack(validate_pack(_pack(pack_id="source-one")))
    service.install_pack(validate_pack(_pack(pack_id="source-two")))
    thread_ids = {}
    source_calls = 0
    original_get_sources = module.get_sources
    original_entry_payload = module._entry_payload

    def capture_sources(*args, **kwargs):
        nonlocal source_calls
        source_calls += 1
        thread_ids["sources"] = threading.get_ident()
        return original_get_sources(*args, **kwargs)

    def capture_payload(*args, **kwargs):
        thread_ids.setdefault("payload", threading.get_ident())
        return original_entry_payload(*args, **kwargs)

    monkeypatch.setattr(module, "get_sources", capture_sources)
    monkeypatch.setattr(module, "_entry_payload", capture_payload)
    response = _client(monkeypatch, tmp_path).get(
        "/api/public-knowledge/entries",
        params={"limit": 10},
    )

    assert response.status_code == 200
    assert source_calls == 1
    assert thread_ids["sources"] != thread_ids["payload"]
    assert {item["source"]["name"] for item in response.json()["items"]} == {
        "source-one",
        "source-two",
    }


def test_management_api_uses_content_preview_when_summary_is_blank(monkeypatch, tmp_path):
    service = open_knowledge(tmp_path)
    KnowledgeStore(service.database_path()).upsert(
        _entry("blank summary fixture", "source:chime", summary="")
    )
    client = _client(monkeypatch, tmp_path)

    item = client.get("/api/public-knowledge/entries", params={"limit": 1}).json()[
        "items"
    ][0]

    assert item["summary"] == "Meaning - A typical use"
    assert item["content_preview"] == "Meaning - A typical use"


def test_local_pack_validation_runs_off_the_request_event_loop(monkeypatch, tmp_path):
    import main_routers.public_knowledge_router as module

    client = _client(monkeypatch, tmp_path)
    thread_ids = {}
    original_validate = module._validate_local_pack_payload
    original_decode = module._decode_json_object

    def capture_request_thread(*_args, **_kwargs):
        thread_ids["request"] = threading.get_ident()
        return None

    def capture_validation_thread(payload):
        thread_ids["validation"] = threading.get_ident()
        return original_validate(payload)

    def capture_decode_thread(raw):
        thread_ids["decode"] = threading.get_ident()
        return original_decode(raw)

    monkeypatch.setattr(module, "_validate_mutation", capture_request_thread)
    monkeypatch.setattr(
        module,
        "_validate_local_pack_payload",
        capture_validation_thread,
    )
    monkeypatch.setattr(module, "_decode_json_object", capture_decode_thread)

    response = client.post(
        "/api/public-knowledge/packs/import",
        json={"pack": _pack(pack_id="threaded-validation")},
    ).json()

    assert response["ok"] is True
    assert thread_ids["decode"] != thread_ids["request"]
    assert thread_ids["validation"] != thread_ids["request"]


def test_incomplete_creation_directory_can_be_explicitly_discarded(
    monkeypatch,
    tmp_path,
):
    orphan = tmp_path / ".staging" / f".creating-{'a' * 32}"
    orphan.mkdir(parents=True)
    client = _client(monkeypatch, tmp_path)

    rejected = client.post(
        "/api/public-knowledge/packs/import",
        json={"pack": _pack(pack_id="blocked-by-orphan")},
    ).json()
    discarded = client.post(
        "/api/public-knowledge/packs/jobs/discard",
        json={"job_id": orphan.name},
    ).json()
    recovered = client.post(
        "/api/public-knowledge/packs/import",
        json={"pack": _pack(pack_id="after-discard")},
    ).json()

    assert rejected == {"ok": False, "reason": "knowledge_job_registry_invalid"}
    assert discarded == {"ok": True, "reason": ""}
    assert not orphan.exists()
    assert recovered["ok"] is True


@pytest.mark.parametrize("error_type", [ValueError, KnowledgeStoreError])
def test_management_api_reports_migration_failure_without_500(
    monkeypatch,
    tmp_path,
    error_type,
):
    import main_routers.public_knowledge_router as module

    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        module,
        "open_knowledge",
        lambda _root: (_ for _ in ()).throw(error_type("migration conflict")),
    )

    status = client.get("/api/public-knowledge/status")
    entries = client.get("/api/public-knowledge/entries")

    assert status.status_code == 200
    assert status.json()["status"]["migration_state"] == "failed"
    assert entries.status_code == 503
    assert entries.json()["detail"]["code"] == "knowledge_unavailable"


def test_management_status_reports_future_schema_without_mutating_it(
    monkeypatch,
    tmp_path,
):
    database_path = tmp_path / "knowledge.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES ('schema_version', '8')"
    )
    connection.commit()
    connection.close()
    before = database_path.read_bytes()
    client = _client(monkeypatch, tmp_path)

    response = client.get("/api/public-knowledge/status")

    assert response.status_code == 200
    status = response.json()["status"]
    assert status["status"] == "degraded"
    assert status["schema_state"] == "too_new"
    assert status["error_code"] == "knowledge_schema_too_new"
    assert status["detected_schema_version"] == 8
    assert status["supported_schema_version"] == 7
    assert database_path.read_bytes() == before


def test_remove_rejects_non_ascii_provider_package_identity(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/public-knowledge/packs/remove",
        json={
            "pack_id": "market-fixture",
            "expected_provider": "plugin-market",
            "expected_provider_package_id": "７",
            "expected_remote_id": "knowledge/market-fixture",
        },
    )

    assert response.json() == {"ok": False, "reason": "invalid_request"}


def test_generic_remove_cannot_delete_a_subscribed_pack(monkeypatch, tmp_path):
    payload = _pack()
    service = open_knowledge(tmp_path)
    service.install_pack(
        validate_pack(payload),
        subscription={
            "provider": "plugin-market",
            "provider_package_id": "7",
            "remote_id": "knowledge/market-fixture",
            "version": "1.0.0",
            "channel": "stable",
            "artifact_sha256": hashlib.sha256(
                canonical_pack_bytes(payload)
            ).hexdigest(),
            "material_type": "knowledge",
            "index_manifest_sha256": "",
            "vectors_sha256": "",
            "trust": "trusted_market",
        },
    )
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/public-knowledge/packs/remove",
        json={"pack_id": "market-fixture"},
    )

    assert response.json() == {
        "ok": False,
        "reason": "subscription_identity_mismatch",
    }
    assert service.list_packs()[0]["pack_id"] == "market-fixture"


def test_mismatched_removal_does_not_cancel_the_packs_active_job(
    monkeypatch, tmp_path
):
    import knowledge.pack_jobs as pack_jobs

    payload = _pack()
    service = open_knowledge(tmp_path)
    service.install_pack(
        validate_pack(payload),
        subscription={
            "provider": "plugin-market",
            "provider_package_id": "7",
            "remote_id": "knowledge/market-fixture",
            "version": "1.0.0",
            "channel": "stable",
            "artifact_sha256": hashlib.sha256(
                canonical_pack_bytes(payload)
            ).hexdigest(),
            "material_type": "knowledge",
            "index_manifest_sha256": "",
            "vectors_sha256": "",
            "trust": "trusted_market",
        },
    )
    cancelled = []
    monkeypatch.setattr(
        pack_jobs,
        "list_pack_jobs",
        lambda _root: [
            {"job_id": "update-job", "pack_id": "market-fixture", "state": "staged"}
        ],
    )
    monkeypatch.setattr(
        pack_jobs,
        "cancel_pack_job",
        lambda _root, job_id: cancelled.append(job_id) or True,
    )

    with pytest.raises(PermissionError, match="identity does not match"):
        service.cancel_and_remove_pack(
            "market-fixture",
            expected_provider="plugin-market",
            expected_provider_package_id="8",
            expected_remote_id="knowledge/market-fixture",
        )

    assert cancelled == []
    assert service.list_packs()[0]["pack_id"] == "market-fixture"


@pytest.mark.asyncio
async def test_removal_operation_survives_request_cancellation_and_is_queryable(
    monkeypatch,
    tmp_path,
):
    import main_routers.public_knowledge_router as module
    from knowledge.removal_operations import get_removal_operation

    operation_id = "remove-operation-fixture-0001"
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    service = SimpleNamespace(
        knowledge_root=tmp_path,
        cancel_and_remove_pack=lambda *_args, **_kwargs: None,
    )

    class _Request:
        async def json(self):
            return {
                "pack_id": "market-fixture",
                "expected_provider": "plugin-market",
                "expected_provider_package_id": "7",
                "expected_remote_id": "knowledge/market-fixture",
                "removal_operation_id": operation_id,
            }

    async def fake_writer(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"removed_pack": True, "removed_entries": 1, "cancelled_jobs": 0}

    async def fake_service():
        return service

    monkeypatch.setattr(module, "_validate_mutation", lambda *_args: None)
    monkeypatch.setattr(module, "_service_async", fake_service)
    monkeypatch.setattr(module, "run_knowledge_writer", fake_writer)

    caller = asyncio.create_task(module.remove_public_knowledge_pack(_Request()))
    await started.wait()
    operation_task = module._pack_removal_tasks[operation_id]
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    assert get_removal_operation(tmp_path, operation_id)["status"] == "pending"
    assert not operation_task.done()
    release.set()
    completed = await operation_task

    assert completed["operation_status"] == "committed"
    assert get_removal_operation(tmp_path, operation_id)["status"] == "committed"
    repeated = await module.remove_public_knowledge_pack(_Request())
    assert repeated["operation_status"] == "committed"
    assert calls == 1


def test_removal_status_reports_unknown_operation(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.get(
        "/api/public-knowledge/packs/remove/status",
        params={"operation_id": "remove-operation-missing-0001"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "status": "unknown",
        "operation_id": "remove-operation-missing-0001",
    }


@pytest.mark.asyncio
async def test_pending_removal_status_resumes_the_same_operation_after_restart(
    monkeypatch,
    tmp_path,
):
    import main_routers.public_knowledge_router as module
    from knowledge.removal_operations import (
        begin_removal_operation,
        get_removal_operation,
    )

    operation_id = "remove-operation-restart-0001"
    operation_request = {
        "pack_id": "market-fixture",
        "expected_provider": "plugin-market",
        "expected_provider_package_id": "7",
        "expected_remote_id": "knowledge/market-fixture",
    }
    begin_removal_operation(tmp_path, operation_id, operation_request)
    calls = 0
    service = SimpleNamespace(
        knowledge_root=tmp_path,
        cancel_and_remove_pack=lambda *_args, **_kwargs: None,
    )

    async def fake_service():
        return service

    async def fake_writer(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"removed_pack": True, "removed_entries": 1, "cancelled_jobs": 0}

    monkeypatch.setattr(module, "_service_async", fake_service)
    monkeypatch.setattr(module, "run_knowledge_writer", fake_writer)

    status = await module.get_public_knowledge_pack_removal_status(operation_id)
    assert status["operation_status"] == "pending"
    recovery = module._pack_removal_tasks[operation_id]
    await recovery

    assert get_removal_operation(tmp_path, operation_id)["status"] == "committed"
    assert calls == 1


@pytest.mark.asyncio
async def test_expired_pending_status_does_not_resume_removal(monkeypatch, tmp_path):
    import knowledge.removal_operations as operations
    import main_routers.public_knowledge_router as module

    now = [100.0]
    patch_module_clock(monkeypatch, operations, time=lambda: now[0])
    operation_id = "remove-operation-expired-0001"
    operations.begin_removal_operation(tmp_path, operation_id, _removal_request())
    now[0] += operations.PENDING_REMOVAL_OPERATION_TTL_SECONDS + 1

    async def fake_service():
        return SimpleNamespace(knowledge_root=tmp_path)

    async def unexpected_remove(*_args, **_kwargs):
        pytest.fail("an expired removal operation must not be resumed")

    monkeypatch.setattr(module, "_service_async", fake_service)
    monkeypatch.setattr(module, "_remove_pack_once", unexpected_remove)

    status = await module.get_public_knowledge_pack_removal_status(operation_id)

    assert status == {
        "ok": False,
        "reason": "removal_operation_expired",
        "operation_id": operation_id,
        "operation_status": "failed",
    }
    assert operation_id not in module._pack_removal_tasks
    assert operations.get_removal_operation(tmp_path, operation_id)["status"] == "failed"


def test_entry_disable_contract_has_no_collection(monkeypatch, tmp_path):
    service = open_knowledge(tmp_path)
    KnowledgeStore(service.database_path()).upsert(
        _entry("disabled fixture", "source:chime")
    )
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/public-knowledge/entry/disabled",
        json={
            "source": "chime",
            "title": "disabled fixture",
            "disabled": True,
        },
    ).json()

    assert response["ok"] is True
    item = client.get(
        "/api/public-knowledge/entries",
        params={"query": "disabled fixture"},
    ).json()["items"][0]
    assert item["disabled"] is True


def test_raw_subscription_v1_stages_without_index(monkeypatch, tmp_path):
    pack = _pack()
    raw = canonical_pack_bytes(pack)
    digest = hashlib.sha256(raw).hexdigest()
    subscription = {
        "provider": "plugin-market",
        "provider_package_id": "7",
        "remote_id": "knowledge/market-fixture",
        "version": "1.0.0",
        "channel": "stable",
        "artifact_sha256": digest,
        "material_type": "knowledge",
        "index_manifest_sha256": "",
        "vectors_sha256": "",
        "trust": "trusted_market",
    }
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/public-knowledge/subscriptions/apply",
        data={"protocol_version": "1", "subscription": json.dumps(subscription)},
        files={"pack": ("pack.neko-knowledge.json", raw, "application/json")},
    ).json()

    assert response["ok"] is True
    assert response["state"] == "queued"
    assert client.get("/api/public-knowledge/packs").json()["packs"] == []
    assert (
        client.get("/api/public-knowledge/packs/jobs").json()["jobs"][0][
            "material_type"
        ]
        == "knowledge"
    )


def test_new_market_subscription_requires_provider_package_identity(
    monkeypatch,
    tmp_path,
):
    pack = _pack()
    raw = canonical_pack_bytes(pack)
    subscription = {
        "provider": "plugin-market",
        "remote_id": "knowledge/market-fixture",
        "version": "1.0.0",
        "channel": "stable",
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "material_type": "knowledge",
        "index_manifest_sha256": "",
        "vectors_sha256": "",
        "trust": "trusted_market",
    }
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/public-knowledge/subscriptions/apply",
        data={"protocol_version": "1", "subscription": json.dumps(subscription)},
        files={"pack": ("pack.neko-knowledge.json", raw, "application/json")},
    ).json()

    assert response == {"ok": False, "reason": "invalid_subscription_identity"}


def test_subscription_rejects_pre_release_protocol(monkeypatch, tmp_path):
    pack = _pack()
    raw = canonical_pack_bytes(pack)
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/public-knowledge/subscriptions/apply",
        data={"protocol_version": "3", "subscription": "{}"},
        files={"pack": ("pack.neko-knowledge.json", raw, "application/json")},
    ).json()

    assert response == {"ok": False, "reason": "unsupported_protocol"}


def test_subscription_v1_stages_verified_sidecars(monkeypatch, tmp_path):
    import main_routers.public_knowledge_router as module

    pack = _pack(pack_id="indexed-fixture")
    raw, artifacts = _prebuilt(pack)
    subscription = {
        "provider": "plugin-market",
        "provider_package_id": "7",
        "remote_id": "knowledge/indexed-fixture",
        "version": "1.0.0",
        "channel": "stable",
        "artifact_sha256": artifacts.pack_sha256,
        "material_type": "knowledge",
        "index_manifest_sha256": artifacts.manifest_sha256,
        "vectors_sha256": artifacts.vectors_sha256,
        "trust": "trusted_market",
    }
    client = _client(monkeypatch, tmp_path)
    thread_ids = {}
    original_validate = module.validate_prebuilt_index

    def capture_request_thread(*_args, **_kwargs):
        thread_ids["request"] = threading.get_ident()
        return None

    def capture_prebuilt_thread(*args, **kwargs):
        thread_ids["prebuilt"] = threading.get_ident()
        return original_validate(*args, **kwargs)

    monkeypatch.setattr(module, "_validate_mutation", capture_request_thread)
    monkeypatch.setattr(module, "validate_prebuilt_index", capture_prebuilt_thread)

    response = client.post(
        "/api/public-knowledge/subscriptions/apply",
        data={"protocol_version": "1", "subscription": json.dumps(subscription)},
        files={
            "pack": ("pack.neko-knowledge.json", raw, "application/json"),
            "index_manifest": (
                "pack.neko-knowledge.index.json",
                artifacts.manifest,
                "application/json",
            ),
            "vectors": (
                "pack.neko-knowledge.vectors.f16",
                artifacts.vectors,
                "application/octet-stream",
            ),
        },
    ).json()

    assert response["ok"] is True
    job_root = tmp_path / ".staging" / response["job_id"]
    assert (job_root / "pack.neko-knowledge.index.json").is_file()
    assert (job_root / "pack.neko-knowledge.vectors.f16").is_file()
    assert thread_ids["prebuilt"] != thread_ids["request"]


def test_subscription_rejects_market_material_type_mismatch(
    monkeypatch, tmp_path
):
    pack = _pack(pack_id="indexed-type-mismatch", material_type="knowledge")
    raw, artifacts = _prebuilt(pack)
    subscription = {
        "provider": "plugin-market",
        "provider_package_id": "7",
        "remote_id": "knowledge/indexed-type-mismatch",
        "version": "1.0.0",
        "channel": "stable",
        "artifact_sha256": artifacts.pack_sha256,
        "material_type": "corpus",
        "index_manifest_sha256": artifacts.manifest_sha256,
        "vectors_sha256": artifacts.vectors_sha256,
        "trust": "trusted_market",
    }
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/public-knowledge/subscriptions/apply",
        data={"protocol_version": "1", "subscription": json.dumps(subscription)},
        files={"pack": ("pack.neko-knowledge.json", raw, "application/json")},
    ).json()

    assert response == {"ok": False, "reason": "material_type_mismatch"}


def test_material_type_endpoint_controls_auto_context(monkeypatch, tmp_path):
    service = open_knowledge(tmp_path)
    service.install_pack(validate_pack(_pack(pack_id="classification-fixture")))
    client = _client(monkeypatch, tmp_path)

    changed = client.post(
        "/api/public-knowledge/packs/material-type",
        json={"pack_id": "classification-fixture", "material_type": "corpus"},
    ).json()
    toggle = client.post(
        "/api/public-knowledge/packs/auto-context",
        json={"pack_id": "classification-fixture", "enabled": True},
    ).json()

    assert changed == {"ok": True, "material_type_override": "corpus"}
    assert toggle == {"ok": True, "auto_context": True}
    assert (
        client.get("/api/public-knowledge/packs").json()["packs"][0][
            "effective_material_type"
        ]
        == "corpus"
    )
