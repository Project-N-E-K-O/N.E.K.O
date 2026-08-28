from __future__ import annotations

import hashlib
import json
import sqlite3
import threading

import pytest

from knowledge.store import KnowledgeStore
from knowledge.pack_jobs import _pack_payload, _prepare_job
from knowledge.pack_jobs import (
    KnowledgeJobRegistryError,
    cancel_pack_job,
    discard_degraded_pack_job,
    process_pack_jobs,
)
from knowledge.packs import validate_pack
from knowledge.prebuilt_index import (
    PREBUILT_DIMENSIONS,
    PREBUILT_MODEL_ID,
    build_prebuilt_index_artifacts,
)
from knowledge.chunking import derive_knowledge_chunks
from knowledge.service import KnowledgeService
from knowledge.subscriptions import canonical_pack_bytes


@pytest.mark.asyncio
async def test_process_pack_jobs_lists_state_off_the_event_loop(tmp_path, monkeypatch):
    import knowledge.pack_jobs as module

    service = KnowledgeService.from_root(tmp_path)
    job_id = "finished-0123456789ab"
    (tmp_path / ".staging" / job_id).mkdir(parents=True)
    event_loop_thread = threading.get_ident()
    list_threads: list[int] = []
    cleanup_threads: list[int] = []

    def tracked_list(_root):
        list_threads.append(threading.get_ident())
        return ({"job_id": job_id, "state": "active", "created_at": 1},)

    def tracked_cleanup(_job_dir):
        cleanup_threads.append(threading.get_ident())

    monkeypatch.setattr(module, "list_pack_jobs", tracked_list)
    monkeypatch.setattr(module, "_cleanup_payload", tracked_cleanup)

    result = await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)

    assert result["state"] == "no_work"
    assert list_threads and all(thread_id != event_loop_thread for thread_id in list_threads)
    assert cleanup_threads and all(
        thread_id != event_loop_thread for thread_id in cleanup_threads
    )


def _pack(
    *,
    title: str = "Staged phrase",
    pack_id: str = "staged-fixture",
    content: str = "A staged entry body.",
):
    return validate_pack(
        {
            "schema_version": 1,
            "pack_id": pack_id,
            "material_type": "knowledge",
            "source": {"name": "Fixture", "homepage": "", "license": "CC0"},
            "entries": [
                {
                    "title": title,
                    "terms": {"alias": [], "recognition": []},
                    "tags": [],
                    "summary": "A staged entry",
                    "content": content,
                }
            ],
        }
    )


def _prebuilt(pack):
    raw = canonical_pack_bytes(_pack_payload(pack))
    chunks = tuple(
        chunk
        for entry in pack.entries
        for chunk in derive_knowledge_chunks(
            entry,
            entry_key=f"{entry.source_tag}:{entry.title}",
        )
    )
    row = bytes.fromhex("003c") + b"\0" * ((PREBUILT_DIMENSIONS - 1) * 2)
    artifacts = build_prebuilt_index_artifacts(
        raw,
        [
            {
                "chunk_id": chunk.chunk_id,
                "content_hash": chunk.content_hash,
                "model_id": PREBUILT_MODEL_ID,
                "dimensions": PREBUILT_DIMENSIONS,
                "embedding": row,
            }
            for chunk in chunks
        ],
    )
    subscription = {
        "provider": "plugin-market",
        "provider_package_id": "7",
        "remote_id": f"knowledge/{pack.pack_id}",
        "version": "1.0.0",
        "channel": "stable",
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "material_type": pack.material_type,
        "index_manifest_sha256": artifacts.manifest_sha256,
        "vectors_sha256": artifacts.vectors_sha256,
        "trust": "trusted_market",
    }
    return artifacts, subscription


def test_prebuilt_verification_resumes_from_persisted_state(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    pack = _pack()
    artifacts, subscription = _prebuilt(pack)
    job = service.stage_pack(
        pack,
        subscription=subscription,
        index_manifest=artifacts.manifest,
        vectors=artifacts.vectors,
    )
    job_dir = tmp_path / ".staging" / str(job["job_id"])

    first = _prepare_job(job_dir)
    resumed = _prepare_job(job_dir)

    assert first["state"] == "verifying_index"
    assert resumed["state"] == "verifying_index"
    assert resumed["index_validation"] == "accepted"
    assert KnowledgeStore(job_dir / "knowledge.db").chunk_status()["chunks_ready"] == 1


def test_staging_rejects_subscription_material_type_mismatch_without_side_effects(
    tmp_path,
):
    service = KnowledgeService.from_root(tmp_path)
    pack = _pack()
    artifacts, subscription = _prebuilt(pack)

    with pytest.raises(ValueError, match="material_type mismatch"):
        service.stage_pack(
            pack,
            subscription={**subscription, "material_type": "corpus"},
            index_manifest=artifacts.manifest,
            vectors=artifacts.vectors,
        )

    assert not (tmp_path / ".staging").exists()
    assert not service.database_path().exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ("missing", "invalid", "replacement"))
async def test_staged_subscription_must_match_immutable_job_identity(
    tmp_path,
    mutation,
):
    service = KnowledgeService.from_root(tmp_path)
    pack = _pack()
    artifacts, subscription = _prebuilt(pack)
    job = service.stage_pack(
        pack,
        subscription=subscription,
        index_manifest=artifacts.manifest,
        vectors=artifacts.vectors,
    )
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    subscription_path = job_dir / "subscription.json"
    if mutation == "missing":
        subscription_path.unlink()
    elif mutation == "invalid":
        subscription_path.write_text("{", encoding="utf-8")
    else:
        replacement = json.loads(subscription_path.read_text(encoding="utf-8"))
        replacement["version"] = "2.0.0"
        subscription_path.write_text(json.dumps(replacement), encoding="utf-8")

    result = await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)
    listed = service.list_pack_jobs()[0]

    assert result["state"] == "degraded"
    assert listed["state"] == "degraded"
    assert listed["reason"] == "job_subscription_identity_mismatch"
    assert job_dir.is_dir()
    assert service.list_packs() == ()


@pytest.mark.asyncio
async def test_local_staged_job_cannot_gain_subscription_metadata(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    _artifacts, subscription = _prebuilt(_pack())
    (job_dir / "subscription.json").write_text(
        json.dumps(subscription),
        encoding="utf-8",
    )

    result = await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)
    listed = service.list_pack_jobs()[0]

    assert result["state"] == "degraded"
    assert listed["reason"] == "job_subscription_identity_mismatch"
    assert service.list_packs() == ()


def test_activation_rechecks_staged_subscription_identity(tmp_path):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    pack = _pack()
    artifacts, subscription = _prebuilt(pack)
    job = service.stage_pack(
        pack,
        subscription=subscription,
        index_manifest=artifacts.manifest,
        vectors=artifacts.vectors,
    )
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    prepared = _prepare_job(job_dir)
    assert prepared["index_validation"] == "accepted"
    subscription_path = job_dir / "subscription.json"
    replacement = json.loads(subscription_path.read_text(encoding="utf-8"))
    replacement["provider_package_id"] = "8"
    subscription_path.write_text(json.dumps(replacement), encoding="utf-8")

    activated = pack_jobs._activate_job(
        service,
        job_dir,
        prepared,
        mode="hybrid",
    )

    assert activated["state"] == "degraded"
    assert activated["reason"] == "job_subscription_identity_mismatch"
    assert service.list_packs() == ()


@pytest.mark.asyncio
async def test_staged_pack_is_hidden_until_bm25_activation(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())

    assert job["state"] == "queued"
    assert job["material_type"] == "knowledge"
    assert service.search("Staged phrase", limit=1) == []
    assert service.list_packs() == ()

    result = await process_pack_jobs(
        service,
        batch_size=4,
        ready_vector_chunks=0,
    )

    assert result["state"] == "ready_bm25"
    assert service.search("Staged phrase", limit=1)
    assert service.list_packs()[0]["retrieval_mode"] == "bm25"
    assert service.list_pack_jobs()[0]["state"] == "active"


@pytest.mark.parametrize("invalid_state", ([], {}, False, 1, None, "unknown"))
def test_malformed_job_state_is_quarantined_before_control_flow(
    tmp_path,
    invalid_state,
):
    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    state_path = job_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["state"] = invalid_state
    state_path.write_text(json.dumps(state), encoding="utf-8")

    listed = service.list_pack_jobs()[0]
    status = service.get_status()

    assert listed["state"] == "degraded"
    assert listed["reason"] == "invalid_job_state"
    assert status["pack_job_registry_state"] == "invalid"
    assert job_dir.is_dir()


@pytest.mark.asyncio
async def test_active_state_requires_a_matching_commit_receipt(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    state_path = job_dir / "state.json"
    identity = json.loads((job_dir / "identity.json").read_text(encoding="utf-8"))
    activation = {
        "schema_version": 1,
        "job_id": identity["job_id"],
        "pack_id": identity["pack_id"],
        "pack_sha256": identity["pack_sha256"],
        "has_subscription": identity["has_subscription"],
        "subscription_sha256": identity["subscription_sha256"],
        "retrieval_mode": "bm25",
    }
    (job_dir / "activation.json").write_text(
        json.dumps(activation),
        encoding="utf-8",
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(state="active", retrieval_mode="bm25")
    state_path.write_text(json.dumps(state), encoding="utf-8")

    listed = service.list_pack_jobs()[0]
    processed = await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)

    assert listed["state"] == "degraded"
    assert listed["reason"] == "active_job_commit_unverified"
    assert processed["state"] == "no_work"
    assert service.list_packs() == ()
    assert job_dir.is_dir()
    assert not (tmp_path / "activation-commits.json").exists()


@pytest.mark.asyncio
async def test_active_receipt_survives_normal_pack_removal(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    service.stage_pack(_pack())
    await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)

    assert service.list_pack_jobs()[0]["state"] == "active"
    assert (tmp_path / "activation-commits.json").is_file()
    service.remove_pack("staged-fixture")

    assert service.list_pack_jobs()[0]["state"] == "active"
    assert service.list_packs() == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (("pack_sha256", "0" * 64), ("retrieval_mode", [])),
)
async def test_active_receipt_rejects_identity_tampering(
    tmp_path,
    field,
    invalid_value,
):
    service = KnowledgeService.from_root(tmp_path)
    service.stage_pack(_pack())
    await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)
    job_dir = next((tmp_path / ".staging").iterdir())
    activation_path = job_dir / "activation.json"
    activation = json.loads(activation_path.read_text(encoding="utf-8"))
    activation[field] = invalid_value
    activation_path.write_text(json.dumps(activation), encoding="utf-8")

    listed = service.list_pack_jobs()[0]

    assert listed["state"] == "degraded"
    assert listed["reason"] == "active_job_commit_unverified"
    assert job_dir.is_dir()


@pytest.mark.asyncio
async def test_active_job_rejects_tampered_external_commit_record(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    service.stage_pack(_pack())
    await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)
    commits_path = tmp_path / "activation-commits.json"
    payload = json.loads(commits_path.read_text(encoding="utf-8"))
    commit = next(iter(payload["commits"].values()))
    commit["retrieval_mode"] = []
    commits_path.write_text(json.dumps(payload), encoding="utf-8")

    listed = service.list_pack_jobs()[0]

    assert listed["state"] == "degraded"
    assert listed["reason"] == "active_job_commit_unverified"


def test_activation_commit_history_is_bounded_and_keeps_current_job(
    tmp_path,
    monkeypatch,
):
    import knowledge.pack_jobs as pack_jobs
    from tests.fake_clock import patch_module_clock

    patch_module_clock(monkeypatch, pack_jobs, time=lambda: 1)
    for index in range(1, pack_jobs.MAX_TERMINAL_JOB_DIRECTORIES + 1):
        pack_jobs._record_activation_commit(
            tmp_path,
            {
                "schema_version": 1,
                "job_id": f"fixture-{index:012x}",
                "pack_id": "fixture",
                "pack_sha256": "0" * 64,
                "has_subscription": False,
                "subscription_sha256": "",
                "retrieval_mode": "bm25",
            },
        )
    current_job_id = "fixture-000000000000"
    pack_jobs._record_activation_commit(
        tmp_path,
        {
            "schema_version": 1,
            "job_id": current_job_id,
            "pack_id": "fixture",
            "pack_sha256": "0" * 64,
            "has_subscription": False,
            "subscription_sha256": "",
            "retrieval_mode": "bm25",
        },
    )

    payload = json.loads(
        (tmp_path / "activation-commits.json").read_text(encoding="utf-8")
    )

    assert len(payload["commits"]) == pack_jobs.MAX_TERMINAL_JOB_DIRECTORIES
    assert current_job_id in payload["commits"]
    assert "fixture-000000000001" not in payload["commits"]


@pytest.mark.asyncio
async def test_pack_update_keeps_old_source_until_new_job_activates(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    service.install_pack(_pack(title="Old phrase"))
    service.stage_pack(_pack(title="New phrase"))

    assert service.search("Old phrase", limit=1)
    assert service.search("New phrase", limit=1) == []

    await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)

    assert service.search("Old phrase", limit=1) == []
    assert service.search("New phrase", limit=1)


@pytest.mark.asyncio
async def test_ready_vectors_are_transferred_during_hybrid_activation(
    tmp_path,
):
    service = KnowledgeService.from_root(tmp_path)
    pack = _pack()
    artifacts, subscription = _prebuilt(pack)
    service.stage_pack(
        pack,
        subscription=subscription,
        index_manifest=artifacts.manifest,
        vectors=artifacts.vectors,
    )

    result = await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)
    status = KnowledgeStore(service.database_path()).chunk_status()

    assert result["state"] == "ready_hybrid"
    assert status["chunks_ready"] == status["chunks_total"] == 1
    assert service.list_packs()[0]["retrieval_mode"] == "hybrid"


def test_cancelled_job_never_becomes_visible(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())

    assert cancel_pack_job(tmp_path, str(job["job_id"])) is True
    assert cancel_pack_job(tmp_path, str(job["job_id"])) is False
    assert service.list_pack_jobs()[0]["state"] == "cancelled"
    assert not (tmp_path / ".staging" / str(job["job_id"]) / "pack.json").exists()
    assert service.search("Staged phrase", limit=1) == []


def test_cancel_and_remove_reports_staged_only_success(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    service.stage_pack(_pack())

    result = service.cancel_and_remove_pack("staged-fixture")

    assert result == {
        "removed_pack": False,
        "removed_entries": 0,
        "cancelled_jobs": 1,
    }
    assert service.list_pack_jobs()[0]["state"] == "cancelled"


def test_market_cancel_and_remove_preserves_same_named_local_pack(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    pack = _pack()
    service.install_pack(pack)
    service.stage_pack(pack)

    with pytest.raises(PermissionError, match="identity"):
        service.cancel_and_remove_pack(
            "staged-fixture",
            expected_provider="plugin-market",
            expected_provider_package_id="7",
            expected_remote_id="knowledge/staged-fixture",
        )

    assert service.list_packs()[0]["pack_id"] == "staged-fixture"
    assert service.list_pack_jobs()[0]["state"] == "cancelled"


@pytest.mark.asyncio
async def test_remove_pack_cancels_its_staged_replacement_before_activation(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    service.install_pack(_pack(title="Installed phrase"))
    job = service.stage_pack(_pack(title="Replacement phrase"))

    assert service.remove_pack("staged-fixture") == 1
    result = await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)

    assert result["state"] == "no_work"
    assert service.list_pack_jobs()[0]["state"] == "cancelled"
    assert service.list_packs() == ()
    assert service.search("Replacement phrase") == []


def test_pack_chunk_budget_is_enforced(monkeypatch):
    import knowledge.packs as packs

    monkeypatch.setattr(packs, "MAX_PACK_PROJECTED_CHUNKS", 0)
    with pytest.raises(ValueError, match="too many chunks"):
        _pack()


def test_community_entry_budget_counts_pending_packs(tmp_path, monkeypatch):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    monkeypatch.setattr(pack_jobs, "MAX_COMMUNITY_ENTRIES", 1)
    job = service.stage_pack(_pack(pack_id="first-pack"))
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    persisted = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))

    assert not set(pack_jobs.IDENTITY_CAPACITY_FIELDS).intersection(persisted)
    assert service.list_pack_jobs()[0]["entries_total"] == 1

    with pytest.raises(ValueError, match="too many entries"):
        service.stage_pack(_pack(pack_id="second-pack"))


def test_corrupt_job_state_is_quarantined_and_still_counts_capacity(
    tmp_path,
    monkeypatch,
):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack(pack_id="first-pack"))
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    assert (job_dir / "identity.json").is_file()
    assert not tuple((tmp_path / ".staging").glob(".creating-*"))
    (job_dir / "state.json").write_text("{", encoding="utf-8")

    listed = service.list_pack_jobs()[0]
    assert listed["state"] == "degraded"
    assert listed["reason"] == "invalid_job_state"
    assert listed["entries_total"] == 1
    assert service.get_status()["pack_job_registry_state"] == "invalid"
    monkeypatch.setattr(pack_jobs, "MAX_COMMUNITY_ENTRIES", 1)

    with pytest.raises(ValueError, match="too many entries"):
        service.stage_pack(_pack(pack_id="second-pack"))


def test_staged_chunk_total_must_match_identity(tmp_path, monkeypatch):
    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    original_chunk_status = KnowledgeStore.chunk_status

    def mismatched_chunk_status(store):
        status = original_chunk_status(store)
        if store.database_path.parent == job_dir:
            status["chunks_total"] = int(status["chunks_total"]) + 1
        return status

    monkeypatch.setattr(KnowledgeStore, "chunk_status", mismatched_chunk_status)

    state = _prepare_job(job_dir)

    assert state["state"] == "degraded"
    assert state["reason"] == "job_capacity_identity_mismatch"
    persisted = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
    assert "chunks_total" not in persisted


@pytest.mark.parametrize(
    "mutation",
    ("entries", "content_bytes", "chunks", "pack_id", "same_capacity_content"),
)
def test_staged_artifact_must_match_immutable_capacity_identity(
    tmp_path,
    mutation,
):
    content = "A" * 1_000 if mutation == "chunks" else "A staged entry body."
    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack(content=content))
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    artifact_path = job_dir / "pack.neko-knowledge.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if mutation == "entries":
        extra = {**artifact["entries"][0], "title": "A second staged entry"}
        artifact["entries"].append(extra)
    elif mutation == "content_bytes":
        artifact["entries"][0]["content"] += "!"
    elif mutation == "chunks":
        # Preserve entries and UTF-8 byte count while crossing a chunk boundary.
        artifact["entries"][0]["content"] = "A" * 900 + "\n\n" + "B" * 98
    elif mutation == "pack_id":
        artifact["pack_id"] = "different-fixture"
    else:
        artifact["entries"][0]["content"] = "B staged entry body."
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    state = _prepare_job(job_dir)

    assert state["state"] == "degraded"
    assert state["reason"] == "job_capacity_identity_mismatch"
    assert not (job_dir / "knowledge.db").exists()
    assert service.list_packs() == ()


def test_activation_rechecks_staged_artifact_capacity_identity(tmp_path):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    prepared = _prepare_job(job_dir)
    assert prepared["state"] == "verifying_index"
    artifact_path = job_dir / "pack.neko-knowledge.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["entries"][0]["content"] += "tampered"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    activated = pack_jobs._activate_job(
        service,
        job_dir,
        prepared,
        mode="bm25",
    )

    assert activated["state"] == "degraded"
    assert activated["reason"] == "job_capacity_identity_mismatch"
    assert service.list_packs() == ()


@pytest.mark.parametrize("field", ("created_at", "updated_at"))
@pytest.mark.parametrize("value", ("not-a-time", -1, 1.5, True))
def test_invalid_job_timestamps_are_quarantined(tmp_path, field, value):
    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    state_path = tmp_path / ".staging" / str(job["job_id"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state[field] = value
    state_path.write_text(json.dumps(state), encoding="utf-8")

    listed = service.list_pack_jobs()[0]

    assert listed["state"] == "degraded"
    assert listed["reason"] == "invalid_job_timestamps"
    assert service.get_status()["pack_job_registry_state"] == "invalid"


@pytest.mark.parametrize("value", (True, 1.5))
def test_invalid_identity_timestamp_cannot_supply_state_fallback(tmp_path, value):
    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    identity_path = job_dir / "identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["created_at"] = value
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    state_path = job_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.pop("created_at")
    state_path.write_text(json.dumps(state), encoding="utf-8")

    listed = service.list_pack_jobs()[0]

    assert listed["state"] == "degraded"
    assert listed["reason"] == "invalid_job_identity"
    assert service.get_status()["pack_job_registry_state"] == "invalid"


def test_job_id_is_cryptographically_scoped_to_immutable_pack_id(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack(pack_id="original-pack"))
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    for name in ("identity.json", "state.json"):
        path = job_dir / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["pack_id"] = "different-pack"
        path.write_text(json.dumps(payload), encoding="utf-8")

    listed = service.list_pack_jobs()[0]

    assert listed["state"] == "degraded"
    assert listed["reason"] == "invalid_job_identity"
    assert listed["orphan"] is True
    with pytest.raises(
        KnowledgeJobRegistryError,
        match="knowledge_job_registry_invalid",
    ):
        service.stage_pack(_pack(pack_id="next-pack"))


@pytest.mark.asyncio
async def test_quarantined_job_is_not_processed_or_cleaned(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    (job_dir / "state.json").unlink()

    result = await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)

    assert result["state"] == "no_work"
    assert job_dir.is_dir()
    assert service.list_pack_jobs()[0]["reason"] == "missing_job_state"
    assert service.list_packs() == ()


def test_incomplete_creation_directory_requires_explicit_discard(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    orphan = tmp_path / ".staging" / f".creating-{'a' * 32}"
    orphan.mkdir(parents=True)
    (orphan / "partial").write_bytes(b"partial")

    with pytest.raises(
        KnowledgeJobRegistryError,
        match="knowledge_job_registry_invalid",
    ):
        service.stage_pack(_pack())

    listed = service.list_pack_jobs()[0]
    assert listed["state"] == "degraded"
    assert listed["orphan"] is True
    assert listed["reason"] == "incomplete_job_creation"
    assert cancel_pack_job(tmp_path, listed["job_id"]) is False
    assert discard_degraded_pack_job(tmp_path, listed["job_id"]) is True
    assert not orphan.exists()
    assert service.stage_pack(_pack(pack_id="after-discard"))["state"] == "queued"


def test_discard_only_removes_degraded_jobs(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_id = str(job["job_id"])
    job_dir = tmp_path / ".staging" / job_id

    assert discard_degraded_pack_job(tmp_path, job_id) is False
    (job_dir / "state.json").write_text("[]", encoding="utf-8")
    assert discard_degraded_pack_job(tmp_path, "../outside") is False
    assert discard_degraded_pack_job(tmp_path, job_id) is True
    assert not job_dir.exists()


def test_first_stage_creates_missing_trusted_knowledge_root(tmp_path):
    knowledge_root = tmp_path / "new-knowledge-root"
    service = KnowledgeService.from_root(knowledge_root)

    assert not knowledge_root.exists()
    assert service.list_pack_jobs() == ()
    assert not knowledge_root.exists()

    job = service.stage_pack(_pack())

    assert knowledge_root.is_dir()
    assert (knowledge_root / ".staging" / str(job["job_id"])).is_dir()


def test_stage_rejects_reparse_knowledge_root_before_writing_locks(
    tmp_path,
    monkeypatch,
):
    import knowledge.pack_jobs as pack_jobs

    knowledge_root = tmp_path / "knowledge-root"
    knowledge_root.mkdir()
    service = KnowledgeService.from_root(knowledge_root)
    original_check = pack_jobs._is_link_or_reparse
    monkeypatch.setattr(
        pack_jobs,
        "_is_link_or_reparse",
        lambda path: path == knowledge_root or original_check(path),
    )

    with pytest.raises(
        KnowledgeJobRegistryError,
        match="knowledge_job_registry_path_invalid",
    ):
        service.stage_pack(_pack())

    assert tuple(knowledge_root.iterdir()) == ()


def test_read_mutations_reject_reparse_knowledge_root_before_lock(
    tmp_path,
    monkeypatch,
):
    import knowledge.pack_jobs as pack_jobs

    original_check = pack_jobs._is_link_or_reparse
    monkeypatch.setattr(
        pack_jobs,
        "_is_link_or_reparse",
        lambda path: path == tmp_path or original_check(path),
    )

    def unexpected_lock(_root):
        pytest.fail("untrusted knowledge root must be rejected before locking")

    monkeypatch.setattr(pack_jobs, "_jobs_registry_lock", unexpected_lock)

    assert pack_jobs.list_pack_jobs(tmp_path) == ()
    assert cancel_pack_job(tmp_path, "fixture-0123456789ab") is False
    assert discard_degraded_pack_job(tmp_path, "fixture-0123456789ab") is False


def test_staging_root_link_is_rejected_without_touching_external_files(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    job_id = "external-0123456789ab"
    job_dir = outside / job_id
    job_dir.mkdir()
    identity = {
        "job_id": job_id,
        "pack_id": "external",
        "created_at": 1,
        "entries_total": 1,
        "chunks_total": 1,
        "content_bytes": 1,
    }
    (job_dir / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
    (job_dir / "state.json").write_text(
        json.dumps({**identity, "state": "queued", "updated_at": 1}),
        encoding="utf-8",
    )
    sentinel = job_dir / "sentinel"
    sentinel.write_bytes(b"external-data")
    try:
        (tmp_path / ".staging").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    before = {
        path.relative_to(outside): path.read_bytes()
        for path in outside.rglob("*")
        if path.is_file()
    }

    assert service.list_pack_jobs() == ()
    assert cancel_pack_job(tmp_path, job_id) is False
    assert discard_degraded_pack_job(tmp_path, job_id) is False
    with pytest.raises(
        KnowledgeJobRegistryError,
        match="knowledge_job_registry_path_invalid",
    ):
        service.stage_pack(_pack())

    after = {
        path.relative_to(outside): path.read_bytes()
        for path in outside.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert sentinel.read_bytes() == b"external-data"


def test_staging_root_reparse_marker_is_rejected(tmp_path, monkeypatch):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    jobs_root = tmp_path / ".staging"
    jobs_root.mkdir()
    sentinel = jobs_root / "sentinel"
    sentinel.write_bytes(b"unchanged")
    original_check = pack_jobs._is_link_or_reparse

    monkeypatch.setattr(
        pack_jobs,
        "_is_link_or_reparse",
        lambda path: path == jobs_root or original_check(path),
    )

    assert service.list_pack_jobs() == ()
    assert cancel_pack_job(tmp_path, "external-0123456789ab") is False
    assert discard_degraded_pack_job(
        tmp_path,
        "external-0123456789ab",
    ) is False
    with pytest.raises(
        KnowledgeJobRegistryError,
        match="knowledge_job_registry_path_invalid",
    ):
        service.stage_pack(_pack())
    assert sentinel.read_bytes() == b"unchanged"
    assert not (tmp_path / ".knowledge-job-registry.mutation.lock").exists()
    assert not tuple(tmp_path.glob(".knowledge-pack-operation-*.mutation.lock"))


def test_reparse_job_directory_is_not_listed_cancelled_or_discarded(
    tmp_path,
    monkeypatch,
):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_id = str(job["job_id"])
    job_dir = tmp_path / ".staging" / job_id
    state_before = (job_dir / "state.json").read_bytes()
    original_check = pack_jobs._is_link_or_reparse

    monkeypatch.setattr(
        pack_jobs,
        "_is_link_or_reparse",
        lambda path: path == job_dir or original_check(path),
    )

    assert service.list_pack_jobs() == ()
    assert cancel_pack_job(tmp_path, job_id) is False
    assert discard_degraded_pack_job(tmp_path, job_id) is False
    assert (job_dir / "state.json").read_bytes() == state_before


@pytest.mark.parametrize("restart_boundary", (False, True))
def test_staged_database_symlink_is_rejected_without_touching_target(
    tmp_path,
    restart_boundary,
):
    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    database_path = job_dir / "knowledge.db"
    if restart_boundary:
        assert _prepare_job(job_dir)["state"] == "verifying_index"
        database_path.unlink()
    outside_database = tmp_path / "outside.db"
    KnowledgeStore(outside_database).upsert(
        _pack(title="Outside sentinel").entries[0]
    )
    try:
        database_path.symlink_to(outside_database)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")

    state = _prepare_job(job_dir)

    assert state["state"] == "degraded"
    assert state["reason"] == "knowledge_staging_database_invalid"
    assert KnowledgeStore(outside_database).count() == 1
    assert KnowledgeStore(outside_database).get_entry(
        "source:community.staged-fixture",
        "Outside sentinel",
    ) is not None


@pytest.mark.parametrize(
    "database_name",
    (
        "knowledge.db",
        "knowledge.db-wal",
        "knowledge.db-shm",
        "knowledge.db-journal",
    ),
)
def test_staged_database_file_family_rejects_reparse_markers(
    tmp_path,
    monkeypatch,
    database_name,
):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    marked_path = job_dir / database_name
    marked_path.write_bytes(b"sentinel")
    original_check = pack_jobs._is_link_or_reparse
    monkeypatch.setattr(
        pack_jobs,
        "_is_link_or_reparse",
        lambda path: path == marked_path or original_check(path),
    )

    state = _prepare_job(job_dir)

    assert state["state"] == "degraded"
    assert state["reason"] == "knowledge_staging_database_invalid"
    assert marked_path.read_bytes() == b"sentinel"


def test_cancel_revalidates_staging_root_after_registry_lock(tmp_path, monkeypatch):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_id = str(job["job_id"])
    job_dir = tmp_path / ".staging" / job_id
    state_before = (job_dir / "state.json").read_bytes()
    original_validate = pack_jobs._validated_jobs_root
    calls = 0

    def invalidate_after_first_check(knowledge_root):
        nonlocal calls
        calls += 1
        if calls > 1:
            return None
        return original_validate(knowledge_root)

    monkeypatch.setattr(
        pack_jobs,
        "_validated_jobs_root",
        invalidate_after_first_check,
    )

    assert cancel_pack_job(tmp_path, job_id) is False
    assert calls >= 2
    assert (job_dir / "state.json").read_bytes() == state_before


@pytest.mark.asyncio
async def test_async_state_update_does_not_revive_cancelled_job(tmp_path):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_id = str(job["job_id"])
    job_dir = tmp_path / ".staging" / job_id

    assert cancel_pack_job(tmp_path, job_id) is True
    updated = await pack_jobs._write_state_async(
        job_dir,
        state="failed",
        retrieval_mode="none",
        reason="late_worker_failure",
    )

    assert updated["state"] == "cancelled"
    assert service.list_pack_jobs()[0]["state"] == "cancelled"


@pytest.mark.asyncio
async def test_failed_state_race_observes_concurrent_cancel(tmp_path, monkeypatch):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_id = str(job["job_id"])
    original_update = pack_jobs._write_state_async

    def fail_activation(*_args, **_kwargs):
        raise OSError("activation unavailable")

    async def cancel_before_failed_update(job_dir, **changes):
        assert cancel_pack_job(tmp_path, job_id) is True
        return await original_update(job_dir, **changes)

    monkeypatch.setattr(pack_jobs, "_activate_job", fail_activation)
    monkeypatch.setattr(pack_jobs, "_write_state_async", cancel_before_failed_update)

    result = await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)

    assert result["state"] == "cancelled"
    assert service.list_pack_jobs()[0]["state"] == "cancelled"


@pytest.mark.parametrize(
    "unsafe_job_id",
    (
        ".",
        "..",
        "../outside",
        "pack-0123456789AF",
        "pack-0123456789ab/child",
        "pack-0123456789ab\\child",
        ".creating-crashed",
        f".creating-{'a' * 31}",
        f".creating-{'A' * 32}",
    ),
)
def test_discard_rejects_non_generated_job_ids_without_touching_root(
    tmp_path,
    unsafe_job_id,
):
    database_path = tmp_path / "knowledge.db"
    registry_path = tmp_path / "packs.json"
    database_path.write_bytes(b"database")
    registry_path.write_bytes(b"registry")

    assert discard_degraded_pack_job(tmp_path, unsafe_job_id) is False
    assert database_path.read_bytes() == b"database"
    assert registry_path.read_bytes() == b"registry"


def test_terminal_job_history_is_pruned_by_count_without_deleting_degraded(
    tmp_path,
    monkeypatch,
):
    import knowledge.pack_jobs as pack_jobs

    jobs_root = tmp_path / ".staging"
    jobs_root.mkdir()
    terminal_job_ids = []
    for index in range(3):
        pack_id = f"pack-{index}"
        job_id = f"{pack_id}-{index:012x}"
        terminal_job_ids.append(job_id)
        job_dir = jobs_root / job_id
        job_dir.mkdir()
        identity = {
            "job_id": job_id,
            "pack_id": pack_id,
            "created_at": index + 1,
            "entries_total": 1,
            "chunks_total": 1,
            "content_bytes": 1,
            "pack_sha256": "0" * 64,
            "has_subscription": False,
            "subscription_sha256": "",
        }
        (job_dir / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
        (job_dir / "state.json").write_text(
            json.dumps({**identity, "state": "cancelled", "updated_at": index + 1}),
            encoding="utf-8",
        )
    degraded = jobs_root / "degraded-job"
    degraded.mkdir()
    (degraded / "state.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(pack_jobs, "MAX_TERMINAL_JOB_DIRECTORIES", 2)
    monkeypatch.setattr(pack_jobs, "TERMINAL_JOB_TTL_SECONDS", 10**12)

    listed = pack_jobs.list_pack_jobs(tmp_path)

    assert not (jobs_root / terminal_job_ids[0]).exists()
    assert (jobs_root / terminal_job_ids[1]).is_dir()
    assert (jobs_root / terminal_job_ids[2]).is_dir()
    assert degraded.is_dir()
    assert {item["job_id"] for item in listed} == {
        terminal_job_ids[1],
        terminal_job_ids[2],
        "degraded-job",
    }


def test_job_is_only_listed_after_atomic_publication(tmp_path, monkeypatch):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    listed = []
    original_write = pack_jobs.atomic_write_bytes

    def paused_write(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return original_write(*args, **kwargs)

    monkeypatch.setattr(pack_jobs, "atomic_write_bytes", paused_write)
    stage_thread = threading.Thread(target=lambda: service.stage_pack(_pack()))
    list_thread = threading.Thread(target=lambda: listed.extend(service.list_pack_jobs()))
    stage_thread.start()
    assert entered.wait(timeout=3)
    list_thread.start()
    assert list_thread.is_alive()
    release.set()
    stage_thread.join(timeout=3)
    list_thread.join(timeout=3)

    assert not stage_thread.is_alive()
    assert not list_thread.is_alive()
    assert [job["state"] for job in listed] == ["queued"]


@pytest.mark.asyncio
async def test_job_without_identity_is_only_quarantined_and_discarded(tmp_path):
    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack())
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    (job_dir / "identity.json").unlink()

    listed = service.list_pack_jobs()[0]
    result = await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)

    assert listed["state"] == "degraded"
    assert listed["reason"] == "invalid_job_identity"
    assert listed["orphan"] is True
    assert result["state"] == "no_work"
    assert cancel_pack_job(tmp_path, str(job["job_id"])) is False
    assert service.list_packs() == ()
    assert discard_degraded_pack_job(tmp_path, str(job["job_id"])) is True


@pytest.mark.parametrize(
    ("field", "limit_name"),
    (
        ("entries_total", "MAX_COMMUNITY_ENTRIES"),
        ("chunks_total", "MAX_COMMUNITY_CHUNKS"),
        ("content_bytes", "MAX_COMMUNITY_CONTENT_BYTES"),
    ),
)
@pytest.mark.parametrize("value", (0, -1, True, 1.5, 2))
def test_state_capacity_cannot_override_identity(
    tmp_path,
    monkeypatch,
    field,
    limit_name,
    value,
):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    job = service.stage_pack(_pack(pack_id="first-pack"))
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    identity = json.loads((job_dir / "identity.json").read_text(encoding="utf-8"))
    state_path = job_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state[field] = value
    state_path.write_text(json.dumps(state), encoding="utf-8")

    listed = service.list_pack_jobs()[0]

    assert listed["state"] == "degraded"
    assert listed["reason"] == "job_capacity_identity_mismatch"
    assert listed[field] == identity[field]
    monkeypatch.setattr(pack_jobs, limit_name, identity[field])
    with pytest.raises(ValueError, match="community knowledge"):
        service.stage_pack(_pack(pack_id="second-pack"))


def test_community_budget_allows_replacing_the_active_pack(tmp_path, monkeypatch):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    pack = _pack()
    service.install_pack(pack)
    monkeypatch.setattr(pack_jobs, "MAX_COMMUNITY_ENTRIES", 1)

    job = service.stage_pack(pack)

    assert job["state"] == "queued"


def test_pending_replacement_does_not_double_count_active_pack(tmp_path, monkeypatch):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    service.install_pack(_pack(pack_id="first-pack"))
    service.stage_pack(_pack(title="Updated phrase", pack_id="first-pack"))
    monkeypatch.setattr(pack_jobs, "MAX_COMMUNITY_ENTRIES", 2)

    job = service.stage_pack(_pack(pack_id="second-pack"))

    assert job["state"] == "queued"


def test_capacity_admission_fails_closed_without_publishing_job(tmp_path, monkeypatch):
    from knowledge.store import KnowledgeStoreError

    service = KnowledgeService.from_root(tmp_path)

    def unavailable_usage(_store, *, source_tag="", strict=False):
        del source_tag
        assert strict is True
        raise KnowledgeStoreError("database locked")

    monkeypatch.setattr(KnowledgeStore, "community_usage", unavailable_usage)

    with pytest.raises(
        KnowledgeJobRegistryError,
        match="knowledge_capacity_unavailable",
    ):
        service.stage_pack(_pack())

    jobs_root = tmp_path / ".staging"
    assert not jobs_root.exists() or not any(
        path.is_dir() for path in jobs_root.iterdir()
    )


@pytest.mark.asyncio
async def test_vector_budget_activates_pack_as_bm25_without_loading_model(
    tmp_path,
    monkeypatch,
):
    service = KnowledgeService.from_root(tmp_path)
    pack = _pack()
    artifacts, subscription = _prebuilt(pack)
    service.stage_pack(
        pack,
        subscription=subscription,
        index_manifest=artifacts.manifest,
        vectors=artifacts.vectors,
    )
    monkeypatch.setattr(
        "knowledge.pack_jobs.MAX_READY_VECTOR_CHUNKS",
        0,
    )

    result = await process_pack_jobs(
        service,
        batch_size=4,
        ready_vector_chunks=20_000,
    )
    job = service.list_pack_jobs()[0]

    assert result["state"] == "ready_bm25"
    assert job["state"] == "active"
    assert job["retrieval_mode"] == "bm25"
    assert job["index_fallback_reason"] == "vector_budget_exceeded"


@pytest.mark.asyncio
async def test_vector_budget_recounts_live_vectors_at_activation(
    tmp_path,
    monkeypatch,
):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    pack = _pack()
    artifacts, subscription = _prebuilt(pack)
    service.stage_pack(
        pack,
        subscription=subscription,
        index_manifest=artifacts.manifest,
        vectors=artifacts.vectors,
    )
    monkeypatch.setattr(pack_jobs, "MAX_READY_VECTOR_CHUNKS", 1)
    monkeypatch.setattr(
        pack_jobs,
        "_live_ready_capacity_snapshot",
        lambda _service, _pack_id: (1, 0),
    )

    result = await process_pack_jobs(
        service,
        batch_size=4,
        ready_vector_chunks=0,
    )

    assert result["state"] == "ready_bm25"
    assert service.list_packs()[0]["retrieval_mode"] == "bm25"


@pytest.mark.asyncio
async def test_missing_staged_vector_database_cannot_activate_as_hybrid(
    tmp_path,
    monkeypatch,
):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    pack = _pack()
    artifacts, subscription = _prebuilt(pack)
    job = service.stage_pack(
        pack,
        subscription=subscription,
        index_manifest=artifacts.manifest,
        vectors=artifacts.vectors,
    )
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    original_prepare = pack_jobs._prepare_job

    def prepare_then_remove_database(selected_job_dir):
        prepared = original_prepare(selected_job_dir)
        assert prepared["index_validation"] == "accepted"
        (selected_job_dir / "knowledge.db").unlink()
        return prepared

    monkeypatch.setattr(pack_jobs, "_prepare_job", prepare_then_remove_database)

    result = await process_pack_jobs(
        service,
        batch_size=4,
        ready_vector_chunks=0,
    )

    assert result["state"] == "degraded"
    assert service.list_packs() == ()
    listed = service.list_pack_jobs()[0]
    assert listed["retrieval_mode"] == "none"
    assert listed["reason"] == "knowledge_staging_database_invalid"


@pytest.mark.asyncio
async def test_tampered_staged_vector_bytes_cannot_activate_as_hybrid(
    tmp_path,
    monkeypatch,
):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    pack = _pack()
    artifacts, subscription = _prebuilt(pack)
    job = service.stage_pack(
        pack,
        subscription=subscription,
        index_manifest=artifacts.manifest,
        vectors=artifacts.vectors,
    )
    job_dir = tmp_path / ".staging" / str(job["job_id"])
    original_prepare = pack_jobs._prepare_job

    def prepare_then_replace_vector(selected_job_dir):
        prepared = original_prepare(selected_job_dir)
        assert prepared["index_validation"] == "accepted"
        replacement = bytes.fromhex("0040") * PREBUILT_DIMENSIONS
        with sqlite3.connect(selected_job_dir / "knowledge.db") as connection:
            connection.execute(
                "UPDATE knowledge_chunks SET embedding=?",
                (replacement,),
            )
        return prepared

    monkeypatch.setattr(pack_jobs, "_prepare_job", prepare_then_replace_vector)

    result = await process_pack_jobs(
        service,
        batch_size=4,
        ready_vector_chunks=0,
    )

    assert result["state"] == "failed"
    assert service.list_packs() == ()
    assert service.list_pack_jobs()[0]["retrieval_mode"] == "none"


@pytest.mark.asyncio
async def test_vector_budget_subtracts_replaced_pack_vectors(tmp_path, monkeypatch):
    import knowledge.pack_jobs as pack_jobs

    service = KnowledgeService.from_root(tmp_path)
    pack = _pack(title="Old phrase")
    artifacts, subscription = _prebuilt(pack)
    service.stage_pack(
        pack,
        subscription=subscription,
        index_manifest=artifacts.manifest,
        vectors=artifacts.vectors,
    )
    assert (
        await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)
    )["state"] == "ready_hybrid"

    replacement = _pack(title="New phrase")
    artifacts, subscription = _prebuilt(replacement)
    service.stage_pack(
        replacement,
        subscription=subscription,
        index_manifest=artifacts.manifest,
        vectors=artifacts.vectors,
    )
    monkeypatch.setattr(pack_jobs, "MAX_READY_VECTOR_CHUNKS", 1)

    result = await process_pack_jobs(
        service,
        batch_size=4,
        ready_vector_chunks=1,
    )

    assert result["state"] == "ready_hybrid"
    assert service.search("Old phrase", limit=1) == []
    assert service.search("New phrase", limit=1)


@pytest.mark.asyncio
async def test_raw_pack_activation_never_loads_the_embedding_model(
    tmp_path,
    monkeypatch,
):
    service = KnowledgeService.from_root(tmp_path)
    service.stage_pack(_pack())
    monkeypatch.setattr(
        "utils.local_embedding_runtime.get_local_embedding_service",
        lambda: pytest.fail("raw-only packs must not load the embedding model"),
    )
    result = await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)
    assert result["state"] == "ready_bm25"
    assert service.list_packs()[0]["local_embedding_enabled"] is False


@pytest.mark.asyncio
async def test_routing_refresh_failure_does_not_relabel_committed_pack(
    tmp_path, monkeypatch
):
    service = KnowledgeService.from_root(tmp_path)
    service.stage_pack(_pack())

    def fail_refresh(*_args, **_kwargs):
        raise OSError("refresh unavailable")

    monkeypatch.setattr(service, "refresh_routing_index", fail_refresh)
    result = await process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)

    assert result["state"] == "ready_bm25"
    assert service.list_pack_jobs()[0]["state"] == "active"
    assert service.list_packs()[0]["pack_id"] == "staged-fixture"
