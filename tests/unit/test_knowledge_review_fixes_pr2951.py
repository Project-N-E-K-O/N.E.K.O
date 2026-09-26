"""Review follow-ups on the public-knowledge PR: routing, status, artifacts, root."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from knowledge.api import open_knowledge
from knowledge.catalog_overrides import get_catalog_override_path
from knowledge.store import KnowledgeStoreError


def _pack(pack_id: str, title: str, *, material_type: str = "knowledge") -> dict:
    return {
        "schema_version": 1,
        "pack_id": pack_id,
        "material_type": material_type,
        "source": {"name": pack_id, "homepage": "", "license": "CC0"},
        "entries": [
            {
                "title": title,
                "terms": {"alias": [], "recognition": []},
                "tags": [],
                "summary": f"summary of {title}",
                "content": f"content of {title}",
            }
        ],
    }


def _supports_symlink(tmp_path: Path) -> bool:
    target = tmp_path / "_link_probe_target"
    target.mkdir()
    try:
        (tmp_path / "_link_probe").symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    return True


# --- routing: a failed load must not be published as a clean snapshot ----------


def test_routing_stays_dirty_when_override_load_fails(tmp_path):
    from knowledge.routing import KnowledgeRoutingState, RoutingConfig
    from knowledge.retrieval import KNOWLEDGE_MATCH_POLICY

    from knowledge.packs import validate_pack

    service = open_knowledge(tmp_path)
    service.install_pack(validate_pack(_pack("routed", "永动机")))

    database_path = service.database_path()
    state = KnowledgeRoutingState(
        RoutingConfig(database_path=database_path, policy=KNOWLEDGE_MATCH_POLICY)
    )

    # Healthy load first: routing finds the term.
    state.refresh()
    healthy_snapshot = state._snapshot
    assert state.match("聊聊永动机") is not None

    # Corrupt the override file and force a reload.
    override = get_catalog_override_path(database_path)
    override.write_text("{ not json", encoding="utf-8")
    state.mark_database_dirty(database_path)
    state.refresh()

    # The failed load must NOT have been cached as clean, otherwise repairing
    # the file would never take effect (repair does not bump the generation).
    assert state._dirty is True
    assert state._snapshot is healthy_snapshot
    assert state.match("聊聊永动机") is not None

    override.unlink()
    state.refresh()
    assert state._dirty is False
    assert state.match("聊聊永动机") is not None


def test_safe_load_records_distinguishes_failure_from_empty(tmp_path):
    from knowledge import routing as routing_module
    from knowledge.retrieval import KNOWLEDGE_MATCH_POLICY
    from knowledge.routing import RoutingConfig, _safe_load_records

    service = open_knowledge(tmp_path)
    config = RoutingConfig(
        database_path=service.database_path(), policy=KNOWLEDGE_MATCH_POLICY
    )

    # Empty but healthy database -> () , not None.
    assert _safe_load_records(config) == ()

    def _boom(_config):
        raise ValueError("catalog override is unreadable or invalid")

    original = routing_module._load_records
    routing_module._load_records = _boom
    try:
        assert _safe_load_records(config) is None
    finally:
        routing_module._load_records = original


def test_routing_failure_backoff_keeps_last_good_without_retry_storm(
    tmp_path,
    monkeypatch,
):
    from knowledge import routing as routing_module
    from knowledge.packs import validate_pack
    from knowledge.retrieval import KNOWLEDGE_MATCH_POLICY
    from knowledge.routing import KnowledgeRoutingState, RoutingConfig

    service = open_knowledge(tmp_path)
    service.install_pack(validate_pack(_pack("routed", "永动机")))
    state = KnowledgeRoutingState(
        RoutingConfig(
            database_path=service.database_path(),
            policy=KNOWLEDGE_MATCH_POLICY,
        )
    )
    state.refresh()
    calls = 0

    def _failed(_config):
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(routing_module, "_safe_load_records", _failed)
    state.mark_database_dirty(service.database_path())
    state.refresh()

    for _ in range(100):
        assert state.match("永动机") is not None
    assert calls == 1


def test_visibility_tombstone_filters_last_good_after_failed_refresh(tmp_path):
    from knowledge.packs import validate_pack
    from knowledge.retrieval import KNOWLEDGE_MATCH_POLICY
    from knowledge.routing import KnowledgeRoutingState, RoutingConfig

    service = open_knowledge(tmp_path)
    service.install_pack(validate_pack(_pack("routed", "永动机")))
    database_path = service.database_path()
    state = KnowledgeRoutingState(
        RoutingConfig(database_path=database_path, policy=KNOWLEDGE_MATCH_POLICY)
    )
    state.refresh()
    assert state.match("永动机") is not None

    get_catalog_override_path(database_path).write_text("{broken", encoding="utf-8")
    state.mark_database_dirty(
        database_path,
        hidden_source_tags=("source:community.routed",),
    )
    state.refresh()

    assert state._dirty is True
    assert state.match("永动机") is None


def test_load_routing_entries_rejects_malformed_row_instead_of_skipping(tmp_path):
    from knowledge.packs import validate_pack

    service = open_knowledge(tmp_path)
    service.install_pack(validate_pack(_pack("routed", "永动机")))
    store = service._store()
    with store._connection(writable=True) as connection:
        connection.execute("UPDATE entries SET tags = '{broken'")

    with pytest.raises((KnowledgeStoreError, ValueError, json.JSONDecodeError)):
        store.load_routing_entries()


# --- status: malformed tags must degrade, not report ready with zero counts ----


def test_status_degrades_when_source_counts_cannot_be_computed(tmp_path, monkeypatch):
    from knowledge.packs import validate_pack

    service = open_knowledge(tmp_path)
    service.install_pack(validate_pack(_pack("counted", "词条")))

    healthy = service.get_status()
    assert healthy["integrity_ok"] is True
    assert healthy["entries"] == 1

    from knowledge.store import KnowledgeStore

    def _raise_when_strict(self, *, strict: bool = False):
        if strict:
            raise KnowledgeStoreError("malformed tags json")
        return ()

    monkeypatch.setattr(KnowledgeStore, "count_by_source_tags", _raise_when_strict)

    degraded = service.get_status()
    assert degraded["integrity_ok"] is False
    assert degraded["error_code"] == "knowledge_database_unavailable"


# --- staged artifacts: bounded, non-redirecting reads -------------------------


def test_staged_pack_artifact_rejects_a_symlink(tmp_path):
    from knowledge.pack_jobs import PACK_ARTIFACT_NAME, _load_job_pack

    if not _supports_symlink(tmp_path):
        pytest.skip("symlink creation is not permitted in this environment")

    external = tmp_path / "external.json"
    external.write_text(json.dumps(_pack("linked", "外部")), encoding="utf-8")

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    os.symlink(external, job_dir / PACK_ARTIFACT_NAME)

    # Match the refusal, not its wording: the reader has already been
    # rewritten once (regular-file check -> reparse-point check) and pinning
    # the exact sentence made this test fail on a strictly better guard.
    with pytest.raises(ValueError, match=r"regular file|reparse"):
        _load_job_pack(job_dir)


def test_staged_pack_artifact_rejects_an_oversized_file(tmp_path, monkeypatch):
    from knowledge import pack_jobs
    from knowledge.pack_jobs import PACK_ARTIFACT_NAME, _load_job_pack

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / PACK_ARTIFACT_NAME).write_text(
        json.dumps(_pack("big", "大包")), encoding="utf-8"
    )

    monkeypatch.setattr(
        pack_jobs, "_staged_artifact_limits", lambda: {PACK_ARTIFACT_NAME: 8}
    )
    with pytest.raises(ValueError, match="exceeds"):
        _load_job_pack(job_dir)


def test_staged_pack_artifact_still_loads_a_normal_file(tmp_path):
    from knowledge.pack_jobs import PACK_ARTIFACT_NAME, _load_job_pack

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / PACK_ARTIFACT_NAME).write_text(
        json.dumps(_pack("plain", "普通包")), encoding="utf-8"
    )

    assert _load_job_pack(job_dir).pack_id == "plain"


# --- live root: destructive mutation must refuse a redirected root ------------


def test_pack_removal_refuses_a_linked_knowledge_root(tmp_path):
    from knowledge.packs import validate_pack

    real_root = tmp_path / "real"
    real_root.mkdir()
    service = open_knowledge(real_root)
    service.install_pack(validate_pack(_pack("removable", "可删词条")))

    if not _supports_symlink(tmp_path):
        pytest.skip("symlink creation is not permitted in this environment")

    linked_root = tmp_path / "linked"
    os.symlink(real_root, linked_root, target_is_directory=True)

    redirected = open_knowledge(linked_root)
    with pytest.raises(KnowledgeStoreError):
        redirected.cancel_and_remove_pack("removable")

    # The pack in the real store is untouched.
    assert [p["pack_id"] for p in service.list_packs()] == ["removable"]

    # And removal through the real root still works.
    result = service.cancel_and_remove_pack("removable")
    assert result["removed_pack"] is True
    assert service.list_packs() == ()


def test_legacy_staged_pack_json_is_bounded_too(tmp_path, monkeypatch):
    """The legacy `pack.json` fallback is just as attacker-mutable as the new one.

    The first version of the fix bounded only the canonical artifact, so removing
    it and dropping an oversized/symlinked `pack.json` in its place walked
    straight back into the unbounded read.
    """
    from knowledge import pack_jobs
    from knowledge.pack_jobs import LEGACY_PACK_ARTIFACT_NAME, _load_job_pack

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / LEGACY_PACK_ARTIFACT_NAME).write_text(
        json.dumps(_pack("legacy", "旧包")), encoding="utf-8"
    )

    # Normal legacy file still loads.
    assert _load_job_pack(job_dir).pack_id == "legacy"

    monkeypatch.setattr(
        pack_jobs, "_staged_artifact_limits", lambda: {LEGACY_PACK_ARTIFACT_NAME: 8}
    )
    with pytest.raises(ValueError, match="exceeds"):
        _load_job_pack(job_dir)


def test_legacy_staged_pack_json_rejects_a_symlink(tmp_path):
    from knowledge.pack_jobs import LEGACY_PACK_ARTIFACT_NAME, _load_job_pack

    if not _supports_symlink(tmp_path):
        pytest.skip("symlink creation is not permitted in this environment")

    external = tmp_path / "external.json"
    external.write_text(json.dumps(_pack("linked-legacy", "外部旧包")), encoding="utf-8")

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    os.symlink(external, job_dir / LEGACY_PACK_ARTIFACT_NAME)

    # Match the refusal, not its wording: the reader has already been
    # rewritten once (regular-file check -> reparse-point check) and pinning
    # the exact sentence made this test fail on a strictly better guard.
    with pytest.raises(ValueError, match=r"regular file|reparse"):
        _load_job_pack(job_dir)


def test_staged_artifact_read_is_bound_to_the_validated_descriptor(
    tmp_path, monkeypatch
):
    """Validation and read must share one descriptor, not two path lookups.

    Checking the path and then calling read_bytes() re-opens by name, so a writer
    that swaps the artifact in between defeats both the link refusal and the size
    cap. The swap is fired after the platform-specific opener has validated its
    handle but before the shared descriptor reader consumes it. A second path
    lookup would pick up the decoy; the original descriptor must not.
    """
    from knowledge.pack_jobs import PACK_ARTIFACT_NAME, _load_job_pack

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    target = job_dir / PACK_ARTIFACT_NAME
    target.write_bytes(json.dumps(_pack("original", "原包")).encode("utf-8"))

    decoy = tmp_path / "decoy.json"
    decoy.write_bytes(json.dumps(_pack("swapped", "掉包")).encode("utf-8"))

    import knowledge.pack_jobs as pack_jobs

    real_read = pack_jobs._read_bounded_descriptor
    swapped = {"done": False, "possible": True}

    def swap_then_read(fd, *, max_bytes):
        if not swapped["done"]:
            swapped["done"] = True
            try:
                os.replace(decoy, target)
            except OSError:
                # Windows refuses to replace a file that is currently open.
                swapped["possible"] = False
        return real_read(fd, max_bytes=max_bytes)

    monkeypatch.setattr(pack_jobs, "_read_bounded_descriptor", swap_then_read)
    pack = _load_job_pack(job_dir)

    assert swapped["done"], "artifact was not consumed through the shared descriptor"
    if not swapped["possible"]:
        pytest.skip("this platform cannot swap an open file; descriptor use verified")
    assert pack.pack_id == "original"


def test_staged_artifact_reader_never_reopens_by_path():
    """Structural dual: the fix is 'do not look the path up a second time'."""
    import ast
    import inspect
    import textwrap

    from knowledge.pack_jobs import (
        _read_bounded_descriptor,
        _read_bounded_staged_file,
    )

    source = "\n".join(
        textwrap.dedent(inspect.getsource(function))
        for function in (_read_bounded_staged_file, _read_bounded_descriptor)
    )
    tree = ast.parse(source)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "read_bytes" not in called and "read_text" not in called, (
        "re-opening the artifact by path after validating it reintroduces the "
        "swap window the descriptor was meant to close"
    )
    assert {"open", "fstat", "read", "close"} <= called, (
        f"expected the descriptor-based read to survive; saw {sorted(called)}"
    )


# --- live-root guard must cover every write path, not just removal ------------


def test_every_live_write_path_refuses_a_linked_root(tmp_path):
    """Removal was guarded first; the other writers reach the same files.

    Derived from the class rather than a hand-written list, so a new mutating
    method is covered the moment it is added.
    """
    from knowledge.packs import validate_pack

    real_root = tmp_path / "real"
    real_root.mkdir()
    service = open_knowledge(real_root)
    service.install_pack(validate_pack(_pack("guarded", "词条")))

    if not _supports_symlink(tmp_path):
        pytest.skip("symlink creation is not permitted in this environment")

    linked_root = tmp_path / "linked"
    os.symlink(real_root, linked_root, target_is_directory=True)
    redirected = open_knowledge(linked_root)

    attempts = {
        "set_pack_auto_context": lambda s: s.set_pack_auto_context(
            "guarded", enabled=True
        ),
        "set_pack_index_policy": lambda s: s.set_pack_index_policy(
            "guarded", local_embedding_enabled=True
        ),
        "set_pack_material_type_override": lambda s: s.set_pack_material_type_override(
            "guarded", material_type="corpus"
        ),
        "set_entry_disabled": lambda s: s.set_entry_disabled(
            source_tag="source:community.guarded", title="词条", disabled=True
        ),
        "cancel_and_remove_pack": lambda s: s.cancel_and_remove_pack("guarded"),
    }
    for name, call in attempts.items():
        with pytest.raises(KnowledgeStoreError):
            call(redirected)

    # The real store is untouched and still writable through its own root.
    assert [p["pack_id"] for p in service.list_packs()] == ["guarded"]
    service.set_pack_auto_context("guarded", enabled=True)


def test_live_root_guard_rejects_a_redirected_ancestor(tmp_path):
    """The leaf may be a real directory while an ancestor redirects the subtree.

    resolve() used to be called only to prove existence — its result was
    discarded, so this case slipped through and the guard would have been copied
    to six call sites still carrying the hole.
    """
    from knowledge.pack_jobs import trusted_live_root

    if not _supports_symlink(tmp_path):
        pytest.skip("symlink creation is not permitted in this environment")

    real_parent = tmp_path / "real_parent"
    (real_parent / "knowledge").mkdir(parents=True)
    assert trusted_live_root(real_parent / "knowledge") is not None

    linked_parent = tmp_path / "linked_parent"
    os.symlink(real_parent, linked_parent, target_is_directory=True)
    # The leaf itself is a genuine directory; only the parent redirects.
    assert trusted_live_root(linked_parent / "knowledge") is None


# --- staging metadata shares the artifact bound --------------------------------


def test_staging_metadata_is_bounded(tmp_path, monkeypatch):
    from knowledge import pack_jobs
    from knowledge.pack_jobs import _read_json_result

    path = tmp_path / "state.json"
    path.write_text(json.dumps({"state": "queued"}), encoding="utf-8")
    assert _read_json_result(path).state == "valid"

    monkeypatch.setattr(pack_jobs, "MAX_STAGED_METADATA_BYTES", 4)
    # Deterministic refusal, so it is "invalid" rather than the transient
    # "unreadable" that would be retried.
    assert _read_json_result(path).state == "invalid"


def test_missing_staging_metadata_stays_missing(tmp_path):
    """Absence must not be folded into 'invalid' by the link check."""
    from knowledge.pack_jobs import _read_json_result

    assert _read_json_result(tmp_path / "absent.json").state == "missing"


def test_staging_metadata_rejects_a_symlink(tmp_path):
    from knowledge.pack_jobs import _read_json_result

    if not _supports_symlink(tmp_path):
        pytest.skip("symlink creation is not permitted in this environment")

    external = tmp_path / "external.json"
    external.write_text(json.dumps({"state": "queued"}), encoding="utf-8")
    link = tmp_path / "state.json"
    os.symlink(external, link)

    assert _read_json_result(link).state == "invalid"


# --- evaluator must not calibrate on entries production will not serve ---------


def _insert_ready_vector(database_path, *, model_id: str, dimensions: int) -> None:
    """Give one entry a ready vector, the way the indexer eventually would."""
    import sqlite3

    import numpy as np

    vector = np.zeros(dimensions, dtype="<f2")
    vector[0] = np.float16(1.0)
    with sqlite3.connect(database_path) as connection:
        # install_pack already derived the chunk rows; give one a ready vector.
        row = connection.execute(
            "SELECT chunk_id FROM knowledge_chunks LIMIT 1"
        ).fetchone()
        assert row is not None, "expected install_pack to derive a chunk"
        connection.execute(
            "UPDATE knowledge_chunks SET embedding_model_id=?, "
            "embedding_dimensions=?, embedding=?, embedding_status='ready' "
            "WHERE chunk_id=?",
            (model_id, dimensions, vector.tobytes(), row[0]),
        )
        connection.commit()


def test_evaluator_rejects_unresolved_community_vectors(tmp_path):
    """Production filters community sources by the installed-pack registry.

    A ready vector whose pack is no longer registered would inflate recall and
    calibrate the similarity threshold against entries asearch() never returns,
    so the evaluator must refuse the corpus instead of silently scoring it.
    """
    import importlib

    from knowledge.packs import get_pack_registry_path, validate_pack

    evaluator = importlib.import_module("scripts.evaluate_knowledge_hybrid_retrieval")

    service = open_knowledge(tmp_path)
    service.install_pack(validate_pack(_pack("orphan", "孤儿词条")))
    database_path = service.database_path()

    model_id, dimensions = "test-model", 8
    _insert_ready_vector(database_path, model_id=model_id, dimensions=dimensions)

    # Sanity: with the registry intact the corpus loads.
    vectors, rows, stats = evaluator._load_vectors(
        database_path, model_id=model_id, dimensions=dimensions
    )
    assert len(vectors) == 1
    assert rows[0]["source_tag"] == "source:community.orphan"
    assert stats["pack_registry_state"] == "ready"

    # Now the pack registry disappears while the ready vector stays behind —
    # production would stop serving that source entirely.
    get_pack_registry_path(database_path).unlink()

    with pytest.raises(evaluator.EvaluationUnavailable) as excinfo:
        evaluator._load_vectors(
            database_path, model_id=model_id, dimensions=dimensions
        )
    assert "unresolved_community_source" in str(excinfo.value)


def test_oversized_metadata_is_refused_without_reading_it(tmp_path, monkeypatch):
    """fstat refuses up front; the post-read length check is only the backstop.

    Both are deliberate, but they are not interchangeable: without the fstat
    check the file still gets pulled into memory up to the cap before being
    rejected, which is the cost the cap exists to avoid.
    """
    from knowledge import pack_jobs

    path = tmp_path / "state.json"
    path.write_text(json.dumps({"state": "queued", "pad": "x" * 4000}), encoding="utf-8")

    reads: list[int] = []
    real_read = os.read

    def counting_read(fd, size):
        chunk = real_read(fd, size)
        reads.append(len(chunk))
        return chunk

    monkeypatch.setattr(os, "read", counting_read)
    monkeypatch.setattr(pack_jobs, "MAX_STAGED_METADATA_BYTES", 16)

    assert pack_jobs._read_json_result(path).state == "invalid"
    assert reads == [], "an oversized file must be refused before it is read"


# --- model tool must not stall the reply --------------------------------------


def test_model_tool_lookup_carries_a_deadline(monkeypatch):
    """Every retrieval entry point needs a ceiling; this one had none.

    A locked database, a mutation lock held by pack recovery, or busy embedding
    work would otherwise make the tool call wait indefinitely — and since text
    mode now has lookup too, that stalls the reply in both modes.
    """
    import asyncio

    import main_logic.knowledge_context as knowledge_tool

    seen: dict[str, object] = {}

    async def _capture(arguments, *, language, deadline_monotonic=None):
        seen["deadline"] = deadline_monotonic
        return ""

    monkeypatch.setattr(knowledge_tool, "handle_public_knowledge_call", _capture)

    class _Registry:
        def __init__(self):
            self.tool = None

        def register(self, definition, replace=False):
            self.tool = definition

    registry = _Registry()
    knowledge_tool.register_public_knowledge_tool(
        registry, language="zh", lookup_enabled=True
    )
    asyncio.run(registry.tool.handler({"query": "永动机"}))

    assert seen["deadline"] is not None, "tool lookup ran with no deadline"


def test_disconnected_upload_is_not_replayed_as_a_whole_body():
    """A body cut short by a disconnect must not be handed over as complete."""
    import asyncio
    import io as _io

    from utils.asgi_body_limit import InboundBodySizeLimitMiddleware

    spool = _io.BytesIO(b"partial multipart prefix")
    replay = InboundBodySizeLimitMiddleware._replay_receive(spool, disconnected=True)

    first = asyncio.run(replay())
    assert first["type"] == "http.disconnect", (
        "a truncated body was replayed as if the request had completed"
    )


@pytest.mark.parametrize(
    "bad_arguments", [None, [], ["query"], "永动机", 0, {"query": "ok"}]
)
def test_model_tool_survives_malformed_arguments(monkeypatch, bad_arguments):
    """A malformed tool payload must become an empty query, not a failed call.

    The sample-only branch spreads the payload (`{**payload, "mode": ...}`),
    which raises TypeError on a non-empty list or string — bypassing the
    coercion handle_public_knowledge_call already does for itself.
    """
    import asyncio

    import main_logic.knowledge_context as knowledge_tool

    async def _capture(arguments, *, language, deadline_monotonic=None):
        assert isinstance(arguments, dict)
        return ""

    monkeypatch.setattr(knowledge_tool, "handle_public_knowledge_call", _capture)

    class _Registry:
        def __init__(self):
            self.tool = None

        def register(self, definition, replace=False):
            self.tool = definition

    for lookup_enabled in (True, False):
        registry = _Registry()
        knowledge_tool.register_public_knowledge_tool(
            registry, language="zh", lookup_enabled=lookup_enabled
        )
        asyncio.run(registry.tool.handler(bad_arguments))
