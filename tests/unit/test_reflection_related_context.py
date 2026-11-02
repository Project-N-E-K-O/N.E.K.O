# -*- coding: utf-8 -*-
"""Phase A-2 — ReflectionEngine._build_related_context_block fallback +
happy path. Verifies embedding-disabled / empty-pool branches return ""
(so {RELATED_CONTEXT_BLOCK} renders to nothing and prompt stays
identical to pre-change behaviour), and a non-empty recall renders a
watermarked block with the expected trailing \\n\\n separator."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_engine(facts_on_disk: list[dict]):
    """Build a minimal ReflectionEngine with mocked fact_store + cm."""
    from memory.reflection import ReflectionEngine
    fact_store = MagicMock()
    fact_store.aload_facts_full = AsyncMock(return_value=facts_on_disk)
    cm = MagicMock()
    cm.memory_dir = "/tmp/dummy"
    # Patch the manager submodule because it consumes get_config_manager.
    # reflection.py uses `from utils.config_manager import get_config_manager`
    # to bind the name in its own module namespace; patching the source module cannot affect an already-bound
    # local reference (CodeRabbit minor #1392).
    with patch("memory.reflection.manager.get_config_manager", return_value=cm):
        engine = ReflectionEngine(fact_store=fact_store, persona_manager=MagicMock())
    return engine


def _disabled_service():
    svc = MagicMock()
    svc.is_disabled = MagicMock(return_value=True)
    svc.is_available = MagicMock(return_value=False)
    return svc


def _enabled_service():
    svc = MagicMock()
    svc.is_disabled = MagicMock(return_value=False)
    svc.is_available = MagicMock(return_value=True)
    return svc


def _loading_service():
    """INIT/LOADING intermediate states are not sticky-disabled but are not ready.
    _build_related_context_block must treat these as unavailable
    (Codex P2 #1392), or reranker falls back to evidence-only and injects unrelated
    facts into RELATED_CONTEXT_BLOCK."""
    svc = MagicMock()
    svc.is_disabled = MagicMock(return_value=False)
    svc.is_available = MagicMock(return_value=False)
    return svc


@pytest.mark.asyncio
async def test_returns_empty_when_embedding_disabled():
    """Unavailable embedding → omit the entire block, keeping the prompt equivalent to its pre-change form."""
    engine = _make_engine([{"id": "f1", "text": "x", "absorbed": True}])
    with patch("memory.embeddings.get_embedding_service", return_value=_disabled_service()):
        block = await engine._build_related_context_block(
            "小天", [{"id": "f2", "text": "y"}]
        )
    assert block == ""


@pytest.mark.asyncio
async def test_returns_empty_when_unabsorbed_is_empty():
    engine = _make_engine([{"id": "f1", "text": "x", "absorbed": True}])
    with patch("memory.embeddings.get_embedding_service", return_value=_enabled_service()):
        block = await engine._build_related_context_block("小天", [])
    assert block == ""


@pytest.mark.asyncio
async def test_returns_empty_when_no_absorbed_facts():
    """Empty absorbed_pool (all facts are unabsorbed) → empty string."""
    engine = _make_engine([
        {"id": "f1", "text": "x", "absorbed": False, "importance": 5},
    ])
    with patch("memory.embeddings.get_embedding_service", return_value=_enabled_service()):
        block = await engine._build_related_context_block(
            "小天", [{"id": "f2", "text": "y"}]
        )
    assert block == ""


@pytest.mark.asyncio
async def test_happy_path_renders_watermarked_block():
    """Successful recall → a block containing watermark + explanation + trailing \\n\\n."""
    engine = _make_engine([
        {"id": "f1", "text": "absorbed fact text", "absorbed": True, "importance": 7},
    ])
    mock_reranker = MagicMock()
    mock_reranker.aretrieve_per_query_topk = AsyncMock(return_value=[
        {"id": "f1", "text": "absorbed fact text", "importance": 7},
    ])
    with patch("memory.embeddings.get_embedding_service", return_value=_enabled_service()), \
         patch("memory.embeddings.is_cached_embedding_valid", return_value=True), \
         patch("memory.recall.MemoryRecallReranker", return_value=mock_reranker):
        block = await engine._build_related_context_block(
            "小天", [{"id": "f2", "text": "new query"}]
        )
    assert "======以下为相关历史背景======" in block
    assert "- absorbed fact text (importance: 7)" in block
    assert "仅供参考" in block
    assert "======以上为相关历史背景======" in block
    # Trailing \n\n naturally separates RELATED_CONTEXT_BLOCK from the downstream ====以下为事实==== fact watermark
    assert block.endswith("\n\n")


@pytest.mark.asyncio
async def test_happy_path_localizes_traditional_context_instruction():
    engine = _make_engine([
        {"id": "f1", "text": "已吸收事實", "absorbed": True, "importance": 7},
    ])
    mock_reranker = MagicMock()
    mock_reranker.aretrieve_per_query_topk = AsyncMock(return_value=[
        {"id": "f1", "text": "已吸收事實", "importance": 7},
    ])
    with patch("memory.embeddings.get_embedding_service", return_value=_enabled_service()), \
         patch("memory.embeddings.is_cached_embedding_valid", return_value=True), \
         patch("memory.recall.MemoryRecallReranker", return_value=mock_reranker):
        block = await engine._build_related_context_block(
            "小天",
            [{"id": "f2", "text": "新事實"}],
            prompt_lang="zh-TW",
        )

    assert "僅供參考，本輪不要為它們單獨產出 reflection" in block
    assert "仅供参考" not in block


@pytest.mark.asyncio
async def test_returns_empty_when_all_absorbed_facts_lack_valid_embedding():
    """Codex P2 #1392: without valid fact embeddings, reranker falls back to
    evidence_score ordering, but facts lack score fields, injecting nearly random historical facts as
    relevant context. Prefilter and return early when the pool is empty."""
    engine = _make_engine([
        {"id": "f1", "text": "absorbed fact", "absorbed": True, "importance": 5},
    ])
    with patch("memory.embeddings.get_embedding_service", return_value=_enabled_service()), \
         patch("memory.embeddings.is_cached_embedding_valid", return_value=False):
        block = await engine._build_related_context_block(
            "小天", [{"id": "f2", "text": "query"}]
        )
    assert block == ""


@pytest.mark.asyncio
async def test_arbitration_archived_fact_stays_out_of_related_context():
    engine = _make_engine([
        {
            "id": "loser", "text": "rejected", "absorbed": True,
            "importance": 7, "arbitration_archived_at": "2026-08-01T00:00:00",
        },
        {"id": "valid", "text": "valid", "absorbed": True, "importance": 5},
    ])
    reranker = MagicMock()
    reranker.aretrieve_per_query_topk = AsyncMock(return_value=[])
    with patch("memory.embeddings.get_embedding_service", return_value=_enabled_service()), \
         patch("memory.embeddings.is_cached_embedding_valid", return_value=True), \
         patch("memory.recall.MemoryRecallReranker", return_value=reranker):
        assert await engine._build_related_context_block(
            "小天", [{"id": "fresh", "text": "query"}],
        ) == ""
    candidates = reranker.aretrieve_per_query_topk.await_args.args[0]
    assert [row["id"] for row in candidates] == ["valid"]


@pytest.mark.asyncio
async def test_returns_empty_when_embedding_loading_not_ready():
    """INIT/LOADING: is_disabled=False but is_available=False → still return
    empty, or evidence-only reranker fallback injects unrelated facts (Codex P2 #1392)."""
    engine = _make_engine([
        {"id": "f1", "text": "absorbed fact text", "absorbed": True, "importance": 7},
    ])
    with patch("memory.embeddings.get_embedding_service", return_value=_loading_service()):
        block = await engine._build_related_context_block(
            "小天", [{"id": "f2", "text": "new query"}]
        )
    assert block == ""
