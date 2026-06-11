# -*- coding: utf-8 -*-
"""Unit tests for memory.embeddings_fallback — the import-time stub
that keeps the memory pipeline alive when ``memory/embeddings.py`` is
quarantined by antivirus.

These tests don't require numpy / onnxruntime / tokenizers, so they run
on every developer workstation regardless of the embedding model bundle
state — that matters because the whole point of the fallback is to keep
working when the heavyweight deps are unreachable.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import sys

import pytest


@pytest.fixture
def fallback():
    """Return a freshly-imported fallback module per test, so the
    per-process ``_warn_once`` cache doesn't leak across cases."""
    if "memory.embeddings_fallback" in sys.modules:
        del sys.modules["memory.embeddings_fallback"]
    mod = importlib.import_module("memory.embeddings_fallback")
    yield mod


def test_disabled_service_reports_unavailable(fallback):
    svc = fallback.get_embedding_service()
    assert svc.is_available() is False
    assert svc.is_disabled() is True
    assert svc.disable_reason() == "embeddings_module_missing"
    assert svc.model_id() is None
    assert svc.dim() is None
    assert svc.quantization() is None
    assert svc.ram_gb() is None
    assert svc.has_vnni() is False


def test_disabled_service_embed_methods_return_safe_values(fallback):
    svc = fallback.get_embedding_service()
    assert asyncio.run(svc.embed("hello")) is None
    assert asyncio.run(svc.embed_batch(["a", "b", "c"])) == [None, None, None]
    assert asyncio.run(svc.embed_batch([])) == []
    # request_load() must report failure so any cold-start poller exits
    # cleanly instead of spinning expecting eventual READY.
    assert asyncio.run(svc.request_load()) is False


def test_cache_stubs_round_trip_safely(fallback):
    entry = {
        "embedding": "AAAA",
        "embedding_text_sha256": "abc",
        "embedding_model_id": "any",
    }
    fallback.clear_embedding_fields(entry)
    assert entry == {
        "embedding": None,
        "embedding_text_sha256": None,
        "embedding_model_id": None,
    }
    # Non-dict input is tolerated — mirrors the real impl's guard so a
    # stray legacy row shape can't crash the caller.
    fallback.clear_embedding_fields("not-a-dict")  # type: ignore[arg-type]

    # stamp is a no-op; nothing should change on the entry.
    fallback.stamp_embedding_fields(entry, [0.1, 0.2], "hello", "id")
    assert entry["embedding"] is None
    assert entry["embedding_text_sha256"] is None

    assert fallback.is_cached_embedding_valid(entry, "hello", "id") is False
    assert fallback.is_cached_embedding_valid({}, "", None) is False


def test_decode_helpers_always_inert(fallback):
    assert fallback.decode_embedding(None) is None
    assert fallback.decode_embedding("AAAA") is None
    assert fallback.decode_embedding([0.1, 0.2]) is None
    assert fallback.cosine_similarity("a", "b") == 0.0
    assert fallback.parse_dim_from_model_id("foo-256d-int8") is None
    assert fallback.parse_dim_from_model_id(None) is None


def test_warn_once_emits_at_most_once_per_consumer(fallback, caplog):
    """A consumer module that falls back should log exactly one
    warning per consumer-module name — repeated reloads must not
    spam the log."""
    with caplog.at_level(logging.WARNING, logger=fallback.logger.name):
        fallback._warn_once("memory.embedding_worker")
        fallback._warn_once("memory.embedding_worker")  # duplicate
        fallback._warn_once("memory.fact_dedup")        # new consumer

    msgs = [r.getMessage() for r in caplog.records]
    worker_hits = [m for m in msgs if "memory.embedding_worker falling back" in m]
    dedup_hits = [m for m in msgs if "memory.fact_dedup falling back" in m]
    assert len(worker_hits) == 1, msgs
    assert len(dedup_hits) == 1, msgs
    # All warnings carry the AV-quarantine triage hint so operators
    # know where to look without grepping the source tree.
    assert all("antivirus quarantine" in m for m in worker_hits + dedup_hits)


def test_top_level_consumers_import_when_embeddings_missing():
    """If ``memory.embeddings`` blows up at import time the four
    top-level consumers MUST still be importable. We simulate the
    quarantine by pinning ``sys.modules['memory.embeddings'] = None``
    — Python's import system treats that sentinel as a definitive
    "module already failed to import", which is exactly what happens
    after antivirus deletes the .py from disk and Python's cache
    sees the prior ImportError.

    We deliberately don't use pytest's ``monkeypatch.setitem`` here:
    its undo runs *after* this function returns, and would re-clobber
    the snapshot we restore in ``finally`` (the undo sees the snapshot
    cache, not the original test entry state). Hand-rolling the dance
    keeps the restore order under our control so the rest of the suite
    sees the cache it expected.
    """
    saved = {k: sys.modules[k] for k in list(sys.modules) if k.startswith("memory")}
    try:
        for k in saved:
            del sys.modules[k]
        sys.modules["memory.embeddings"] = None  # type: ignore[assignment]

        from memory import embedding_worker, fact_dedup, recall, refine

        svc = embedding_worker.get_embedding_service()
        assert svc.is_available() is False
        assert svc.disable_reason() == "embeddings_module_missing"

        assert fact_dedup.cosine_similarity("a", "b") == 0.0
        assert recall.decode_embedding("anything") is None
        assert refine.parse_dim_from_model_id("local-text-retrieval-v1-256d-int8") is None
    finally:
        # Drop the four consumer modules we just imported under the
        # poisoned cache — they hold references to the fallback stubs,
        # not the real ones, and would shadow the originals if any
        # later test in the same process did ``from memory.X import Y``.
        for k in list(sys.modules):
            if k.startswith("memory"):
                del sys.modules[k]
        sys.modules.update(saved)
