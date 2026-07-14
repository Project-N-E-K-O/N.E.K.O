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
    # reflection.py 用 `from utils.config_manager import get_config_manager`
    # 把名字 bind 到本模块 namespace，patch source module 对已 bind 的
    # 局部引用无效（CodeRabbit minor #1392）。
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
    """INIT/LOADING 中间态：未 sticky disable，但还没 ready。
    _build_related_context_block 必须把这视为 unavailable
    （Codex P2 #1392），否则 reranker 会降级 evidence-only 把无关
    fact 塞进 RELATED_CONTEXT_BLOCK。"""
    svc = MagicMock()
    svc.is_disabled = MagicMock(return_value=False)
    svc.is_available = MagicMock(return_value=False)
    return svc


@pytest.mark.asyncio
async def test_returns_empty_when_embedding_disabled():
    """embedding 不可用 → 整块省略，prompt 与改造前等价。"""
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
    """absorbed_pool 为空（所有 fact 都是 unabsorbed）→ 空字符串。"""
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
    """召回到东西 → 输出含 watermark + 说明 + 尾部 \\n\\n 的 block。"""
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
    # 末尾 \n\n 是 RELATED_CONTEXT_BLOCK 与下游 ====以下为事实==== 自然分隔的依据
    assert block.endswith("\n\n")


@pytest.mark.asyncio
async def test_returns_empty_when_all_absorbed_facts_lack_valid_embedding():
    """Codex P2 #1392：fact 没 valid embedding 时 reranker 会 fallback 到
    evidence_score 排序，fact 又没 score 字段 → 注入近随机的历史 fact 当
    相关背景。必须 pre-filter，pool 为空就 early return。"""
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
async def test_returns_empty_when_embedding_loading_not_ready():
    """INIT/LOADING：is_disabled=False 但 is_available=False → 仍要返回
    空，否则 reranker 降级 evidence-only 会注入无关 fact（Codex P2 #1392）。"""
    engine = _make_engine([
        {"id": "f1", "text": "absorbed fact text", "absorbed": True, "importance": 7},
    ])
    with patch("memory.embeddings.get_embedding_service", return_value=_loading_service()):
        block = await engine._build_related_context_block(
            "小天", [{"id": "f2", "text": "new query"}]
        )
    assert block == ""
