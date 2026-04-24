# -*- coding: utf-8 -*-
"""Unit tests for fact version chain (RFC memory-enhancements §2).

When resolve_corrections decides `replace`, the old text must be
preserved in `version_history` on the replacing entry, chained across
multiple corrections so temporal context (e.g., 主人以前住东京 → 搬到大阪)
stays traceable."""
from __future__ import annotations

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
    """Return a fake chat-llm whose ainvoke yields `payload` as JSON."""
    import json as _json

    resp = MagicMock()
    resp.content = _json.dumps(payload)

    async def _ainvoke(*_args, **_kwargs):
        return resp

    async def _aclose():
        return None

    llm = MagicMock()
    llm.ainvoke = _ainvoke
    llm.aclose = _aclose
    return llm


@pytest.mark.asyncio
async def test_replace_records_old_text_in_version_history(tmp_path):
    """replace 动作必须把旧文本压进 version_history，新 entry 的 text 是 merged。"""
    pm, _ = _install(str(tmp_path))

    # Seed persona with an existing entry that will be replaced.
    persona = await pm._aensure_persona_locked("小天")
    section = pm._get_section_facts(persona, "master")
    section.append(pm._normalize_entry("主人住在东京"))
    await pm.asave_persona("小天", persona)

    # Queue a correction pair.
    await pm._aqueue_correction("小天", "主人住在东京", "主人住在大阪", "master")

    # Mock the correction LLM: return `replace` with merged text.
    fake_llm = _make_llm_mock([{
        "index": 0, "action": "replace", "text": "主人后来搬到了大阪",
    }])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        resolved = await pm.resolve_corrections("小天")
    assert resolved == 1

    # Verify persona state.
    persona = await pm._aensure_persona_locked("小天")
    master_facts = pm._get_section_facts(persona, "master")
    # The replaced entry should carry the merged text.
    target = next(e for e in master_facts if e.get("text") == "主人后来搬到了大阪")
    history = target.get("version_history") or []
    assert len(history) == 1
    assert history[0]["text"] == "主人住在东京"
    assert history[0]["reason"] == "correction"
    assert history[0]["replaced_at"]  # ISO timestamp populated


@pytest.mark.asyncio
async def test_replace_chains_history_across_multiple_corrections(tmp_path):
    """连续两次 replace：history 应保留全链路，不覆盖。"""
    pm, _ = _install(str(tmp_path))

    persona = await pm._aensure_persona_locked("小天")
    pm._get_section_facts(persona, "master").append(pm._normalize_entry("主人住在东京"))
    await pm.asave_persona("小天", persona)

    # First correction: 东京 → 大阪
    await pm._aqueue_correction("小天", "主人住在东京", "主人住在大阪", "master")
    fake_llm = _make_llm_mock([{"index": 0, "action": "replace", "text": "主人住在大阪"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    # Second correction: 大阪 → 福冈
    await pm._aqueue_correction("小天", "主人住在大阪", "主人住在福冈", "master")
    fake_llm = _make_llm_mock([{"index": 0, "action": "replace", "text": "主人住在福冈"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    persona = await pm._aensure_persona_locked("小天")
    master_facts = pm._get_section_facts(persona, "master")
    target = next(e for e in master_facts if e.get("text") == "主人住在福冈")
    history = target.get("version_history") or []
    assert [h["text"] for h in history] == ["主人住在东京", "主人住在大阪"]
    for h in history:
        assert h["reason"] == "correction"


@pytest.mark.asyncio
async def test_non_replace_actions_do_not_record_version_history(tmp_path):
    """keep_new / keep_old / keep_both: 不写 version_history（§2 明确范围）。"""
    pm, _ = _install(str(tmp_path))

    persona = await pm._aensure_persona_locked("小天")
    pm._get_section_facts(persona, "master").append(pm._normalize_entry("主人喜欢绿茶"))
    await pm.asave_persona("小天", persona)

    # keep_new: replaces old without going through `replace` branch.
    await pm._aqueue_correction("小天", "主人喜欢绿茶", "主人喜欢红茶", "master")
    fake_llm = _make_llm_mock([{"index": 0, "action": "keep_new"}])
    with patch("utils.llm_client.create_chat_llm", return_value=fake_llm):
        await pm.resolve_corrections("小天")

    persona = await pm._aensure_persona_locked("小天")
    master_facts = pm._get_section_facts(persona, "master")
    new_entry = next(e for e in master_facts if e.get("text") == "主人喜欢红茶")
    # Default from _normalize_entry is []; keep_new must not populate it.
    assert new_entry.get("version_history") == []
