# -*- coding: utf-8 -*-
"""Schema-level tests for the embedding cache fields on persona /
reflection / fact entries.

Covers two contracts the rest of P2 relies on:

  1. New entries default the embedding triple to None — they're
     visible to the warmup worker as "needs embedding" without any
     migration step.
  2. The persona ``replace`` branch (resolve_corrections) clears the
     embedding triple alongside the existing token_count cache, so a
     text rewrite never leaves a stale vector pointing at the old text.

The first contract is tested directly on the normalize functions; the
second is an end-to-end test through resolve_corrections, mirroring
test_persona_version_history.py's mock-LLM pattern from PR #941."""
from __future__ import annotations

import json

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.persona import PersonaManager
from memory.reflection import ReflectionEngine


# ── normalize-time defaults ─────────────────────────────────────────


def test_persona_normalize_entry_seeds_embedding_fields_as_none():
    """A fresh persona entry must read None on all three embedding fields
    so the warmup worker picks it up on its next sweep."""
    entry = PersonaManager._normalize_entry("主人喜欢猫")
    assert entry["embedding"] is None
    assert entry["embedding_text_sha256"] is None
    assert entry["embedding_model_id"] is None
    # text + version_history coexist with the embedding fields without
    # collision — defensive against a future refactor that consolidates
    # cache fields into a sub-dict.
    assert entry["text"] == "主人喜欢猫"
    assert entry["version_history"] == []


def test_persona_normalize_entry_preserves_existing_embedding_payload():
    """If a dict already carries an embedding triple (e.g. loaded from
    disk), normalize must NOT clobber it — that's the warmup worker's
    cache hit path."""
    raw = {
        "text": "x",
        "embedding": [0.1, 0.2, 0.3],
        "embedding_text_sha256": "deadbeef",
        "embedding_model_id": "jina-v5-nano-128d-int8",
    }
    entry = PersonaManager._normalize_entry(raw)
    assert entry["embedding"] == [0.1, 0.2, 0.3]
    assert entry["embedding_text_sha256"] == "deadbeef"
    assert entry["embedding_model_id"] == "jina-v5-nano-128d-int8"


def test_reflection_normalize_seeds_embedding_fields_as_none():
    raw = {"id": "r1", "text": "test reflection"}
    out = ReflectionEngine._normalize_reflection(raw)
    assert out["embedding"] is None
    assert out["embedding_text_sha256"] is None
    assert out["embedding_model_id"] is None


def test_reflection_normalize_preserves_existing_embedding():
    raw = {
        "id": "r1",
        "text": "t",
        "embedding": [0.5, 0.5],
        "embedding_text_sha256": "abc",
        "embedding_model_id": "jina-v5-nano-256d-fp32",
    }
    out = ReflectionEngine._normalize_reflection(raw)
    assert out["embedding"] == [0.5, 0.5]
    assert out["embedding_text_sha256"] == "abc"
    assert out["embedding_model_id"] == "jina-v5-nano-256d-fp32"


# ── replace branch invalidates the embedding cache ──────────────────


def _mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "小天", {}, {}, {"human": "主人", "system": "SYS"}, {}, {}, {}, {},
    ))
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk-fake",
    })
    return cm


def _install_pm(tmpdir: str):
    from memory.event_log import EventLog

    cm = _mock_cm(tmpdir)
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.persona.get_config_manager", return_value=cm):
        event_log = EventLog()
        event_log._config_manager = cm
        pm = PersonaManager(event_log=event_log)
        pm._config_manager = cm
    return pm


def _make_llm_mock(payload):
    resp = MagicMock()
    resp.content = json.dumps(payload)

    async def _ainvoke(*_a, **_k):
        return resp

    async def _aclose():
        return None

    llm = MagicMock()
    llm.ainvoke = _ainvoke
    llm.aclose = _aclose
    return llm


async def _seed_master_fact(pm, name: str, text: str, **overrides):
    """Mirror of the helper in test_persona_version_history — appends
    a fact via internal API and returns the on-disk-normalized dict."""
    persona = await pm.aensure_persona(name)
    entry = pm._normalize_entry(text)
    entry.update(overrides)
    pm._get_section_facts(persona, "master").append(entry)
    await pm.asave_persona(name, persona)
    persona = await pm.aensure_persona(name)
    return next(
        e for e in pm._get_section_facts(persona, "master")
        if isinstance(e, dict) and e.get("text") == text
    )


@pytest.mark.asyncio
async def test_replace_invalidates_embedding_cache(tmp_path):
    """Mirrors PR #941's token_count-invalidation test: when text
    changes via the replace branch, the embedding triple MUST be
    cleared so the next worker sweep re-embeds the new text."""
    pm = _install_pm(str(tmp_path))
    seeded = await _seed_master_fact(
        pm, "小天", "主人住在东京",
        embedding=[0.1] * 128,
        embedding_text_sha256="cafef00d" * 8,
        embedding_model_id="jina-v5-nano-128d-int8",
    )
    # Sanity: the seed actually round-tripped to disk with the cache
    # populated, so the assertion below proves invalidation, not a
    # missing seed.
    assert seeded["embedding"] is not None

    await pm._aqueue_correction("小天", "主人住在东京", "主人住在大阪", "master")
    fake_llm = _make_llm_mock([
        {"index": 0, "action": "replace", "text": "主人住在大阪"},
    ])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    persona = await pm.aensure_persona("小天")
    target = next(
        e for e in pm._get_section_facts(persona, "master")
        if e.get("text") == "主人住在大阪"
    )
    assert target["embedding"] is None
    assert target["embedding_text_sha256"] is None
    assert target["embedding_model_id"] is None
    # And the version-history field still records the prior text — the
    # embedding wipe must NOT also wipe the chain. Same scope contract
    # as the token_count invalidation test in PR #941.
    history = target.get("version_history") or []
    assert history and history[0]["text"] == "主人住在东京"


@pytest.mark.asyncio
async def test_replace_preserves_embedding_when_replace_branch_not_taken(tmp_path):
    """The keep_both branch doesn't touch the existing entry, so its
    embedding cache must survive intact (callers rely on this so a
    'these aren't actually contradictory' decision keeps the warm
    embedding)."""
    pm = _install_pm(str(tmp_path))
    seeded = await _seed_master_fact(
        pm, "小天", "主人喜欢猫",
        embedding=[0.5] * 128,
        embedding_text_sha256="0123abcd" * 8,
        embedding_model_id="jina-v5-nano-128d-int8",
    )
    original_embedding = list(seeded["embedding"])

    await pm._aqueue_correction("小天", "主人喜欢猫", "主人最近养了一只狗", "master")
    fake_llm = _make_llm_mock([
        {"index": 0, "action": "keep_both"},
    ])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    persona = await pm.aensure_persona("小天")
    cat_entry = next(
        e for e in pm._get_section_facts(persona, "master")
        if e.get("text") == "主人喜欢猫"
    )
    assert cat_entry["embedding"] == original_embedding
    assert cat_entry["embedding_model_id"] == "jina-v5-nano-128d-int8"
