# -*- coding: utf-8 -*-
"""Unit tests for fact version chain (RFC memory-enhancements §2).

When resolve_corrections decides `replace`, the old text must be
preserved in `version_history` on the replacing entry, chained across
multiple corrections so temporal context (e.g., 主人以前住东京 → 搬到大阪)
stays traceable. `replace` is an in-place edit: id / source / evidence
counters must survive the rewrite — only `text` and the history tail
change."""
from __future__ import annotations

import json

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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


def _install(tmpdir: str):
    from memory.event_log import EventLog
    from memory.persona import PersonaManager

    cm = _mock_cm(tmpdir)
    with patch("memory.event_log.get_config_manager", return_value=cm), \
         patch("memory.persona.get_config_manager", return_value=cm):
        event_log = EventLog()
        event_log._config_manager = cm
        pm = PersonaManager(event_log=event_log)
        pm._config_manager = cm
    return pm, cm


def _make_llm_mock(payload: list[dict]):
    """Fake chat-llm whose ainvoke yields `payload` as JSON."""
    resp = MagicMock()
    resp.content = json.dumps(payload)

    async def _ainvoke(*_args, **_kwargs):
        return resp

    async def _aclose():
        return None

    llm = MagicMock()
    llm.ainvoke = _ainvoke
    llm.aclose = _aclose
    return llm


async def _seed_master_fact(pm, name: str, text: str, **overrides) -> dict:
    """Append a fact to `name`'s master section via the public API and
    return the stored dict so the test can mutate/inspect it."""
    persona = await pm.aensure_persona(name)
    entry = pm._normalize_entry(text)
    entry.update(overrides)
    pm._get_section_facts(persona, "master").append(entry)
    await pm.asave_persona(name, persona)
    # Re-read so callers see the on-disk normalized copy.
    persona = await pm.aensure_persona(name)
    return next(
        e for e in pm._get_section_facts(persona, "master")
        if isinstance(e, dict) and e.get("text") == text
    )


@pytest.mark.asyncio
async def test_replace_records_old_text_in_version_history(tmp_path):
    """replace 动作必须把旧文本压进 version_history，新 entry 的 text 是 merged。"""
    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(pm, "小天", "主人住在东京")

    await pm._aqueue_correction("小天", "主人住在东京", "主人住在大阪", "master")
    fake_llm = _make_llm_mock([{
        "index": 0, "action": "replace", "text": "主人后来搬到了大阪",
    }])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        resolved = await pm.resolve_corrections("小天")
    assert resolved == 1

    persona = await pm.aensure_persona("小天")
    master_facts = pm._get_section_facts(persona, "master")
    target = next(e for e in master_facts if e.get("text") == "主人后来搬到了大阪")
    history = target.get("version_history") or []
    assert len(history) == 1
    assert history[0]["text"] == "主人住在东京"
    assert history[0]["reason"] == "correction"
    assert history[0]["replaced_at"]


@pytest.mark.asyncio
async def test_replace_chains_history_across_multiple_corrections(tmp_path):
    """连续两次 replace：history 应保留全链路，不覆盖。"""
    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(pm, "小天", "主人住在东京")

    await pm._aqueue_correction("小天", "主人住在东京", "主人住在大阪", "master")
    fake_llm = _make_llm_mock([{"index": 0, "action": "replace", "text": "主人住在大阪"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    await pm._aqueue_correction("小天", "主人住在大阪", "主人住在福冈", "master")
    fake_llm = _make_llm_mock([{"index": 0, "action": "replace", "text": "主人住在福冈"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    persona = await pm.aensure_persona("小天")
    master_facts = pm._get_section_facts(persona, "master")
    target = next(e for e in master_facts if e.get("text") == "主人住在福冈")
    history = target.get("version_history") or []
    assert [h["text"] for h in history] == ["主人住在东京", "主人住在大阪"]
    for h in history:
        assert h["reason"] == "correction"


@pytest.mark.asyncio
async def test_replace_preserves_id_source_and_evidence_metadata(tmp_path):
    """CodeRabbit-flagged: `replace` must be in-place — id / source /
    reinforcement / recent_mentions survive the text rewrite."""
    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(
        pm, "小天", "主人住在东京",
        id="prom_ref_tokyo",
        source="reflection",
        source_id="ref_tokyo",
        reinforcement=2.5,
        disputation=0.5,
        rein_last_signal_at="2026-04-01T10:00:00",
        recent_mentions=["2026-04-20T10:00:00", "2026-04-21T11:00:00"],
        user_fact_reinforce_count=3,
    )

    await pm._aqueue_correction("小天", "主人住在东京", "主人住在大阪", "master")
    fake_llm = _make_llm_mock([{"index": 0, "action": "replace", "text": "主人住在大阪"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    persona = await pm.aensure_persona("小天")
    master_facts = pm._get_section_facts(persona, "master")
    target = next(e for e in master_facts if e.get("text") == "主人住在大阪")
    # Identity + provenance preserved.
    assert target["id"] == "prom_ref_tokyo"
    assert target["source"] == "reflection"
    assert target["source_id"] == "ref_tokyo"
    # Evidence counters preserved — a confirmed entry stays confirmed after
    # a text correction; callers rely on this for rein/disp accounting.
    assert target["reinforcement"] == pytest.approx(2.5)
    assert target["disputation"] == pytest.approx(0.5)
    assert target["rein_last_signal_at"] == "2026-04-01T10:00:00"
    assert target["user_fact_reinforce_count"] == 3
    assert target["recent_mentions"] == [
        "2026-04-20T10:00:00", "2026-04-21T11:00:00",
    ]
    # History still records the old text.
    assert target["version_history"][0]["text"] == "主人住在东京"


@pytest.mark.asyncio
async def test_replace_invalidates_token_count_cache(tmp_path):
    """text 变了 → token_count / sha / tokenizer 三元组必须重置。"""
    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(
        pm, "小天", "主人住在东京",
        token_count=7,
        token_count_text_sha256="deadbeef" * 8,
        token_count_tokenizer="tiktoken:o200k_base",
    )

    await pm._aqueue_correction("小天", "主人住在东京", "主人住在大阪", "master")
    fake_llm = _make_llm_mock([{"index": 0, "action": "replace", "text": "主人住在大阪"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    persona = await pm.aensure_persona("小天")
    master_facts = pm._get_section_facts(persona, "master")
    target = next(e for e in master_facts if e.get("text") == "主人住在大阪")
    assert target["token_count"] is None
    assert target["token_count_text_sha256"] is None
    assert target["token_count_tokenizer"] is None


@pytest.mark.asyncio
async def test_non_replace_actions_do_not_record_version_history(tmp_path):
    """keep_new / keep_old / keep_both: 不写 version_history（§2 明确范围）。"""
    pm, _ = _install(str(tmp_path))
    await _seed_master_fact(pm, "小天", "主人喜欢绿茶")

    await pm._aqueue_correction("小天", "主人喜欢绿茶", "主人喜欢红茶", "master")
    fake_llm = _make_llm_mock([{"index": 0, "action": "keep_new"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    persona = await pm.aensure_persona("小天")
    master_facts = pm._get_section_facts(persona, "master")
    new_entry = next(e for e in master_facts if e.get("text") == "主人喜欢红茶")
    assert new_entry.get("version_history") == []
