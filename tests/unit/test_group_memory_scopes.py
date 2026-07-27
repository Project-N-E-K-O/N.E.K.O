"""Group-chat memory subject/scope isolation and legacy compatibility."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.facts import FactStore
from memory.hybrid_recall import hybrid_recall
from memory.persona.rendering import RenderingMixin
from memory.persona.facts import FactsMixin
from memory.reflection.synthesis import SynthesisMixin
from memory.scopes import (
    LEGACY_PRIVATE_SCOPE,
    MemoryScopeError,
    MemorySubject,
    effective_scope,
    filter_entries_for_subjects,
)


class _PersistHarness(FactStore):
    def __init__(self, time_indexed=None):
        super().__init__(time_indexed_memory=time_indexed)
        self._mem: list[dict] = []

    async def aload_facts(self, lanlan_name):
        return self._mem

    async def asave_facts(self, lanlan_name):
        return None


class _FakeTimeIndexed:
    def __init__(self):
        self.hits: list[tuple[str, float]] = []

    async def asearch_facts(self, lanlan_name, text, limit):
        return list(self.hits)[:limit]

    async def aindex_fact(self, lanlan_name, fact_id, text):
        return None


class _PersonaHarness(FactsMixin, RenderingMixin):
    FACT_ADDED = "added"
    FACT_REJECTED_CARD = "rejected_card"
    FACT_QUEUED_CORRECTION = "queued"

    def __init__(self):
        self.persona: dict = {}

    def ensure_persona(self, name):
        return self.persona

    def save_persona(self, name, persona=None):
        return None

    def _get_entity_stop_names(self, lanlan_name=None):
        return []

    def _queue_correction(self, name, old_text, new_text, entity):
        raise AssertionError("unexpected correction")


class _ScopedSynthesisHarness(SynthesisMixin):
    def __init__(self, facts):
        self._fact_store = MagicMock()
        self._fact_store.aload_facts = AsyncMock(return_value=facts)
        self.seen: list[MemorySubject] = []

    async def synthesize_reflections(self, lanlan_name, *, subject=None):
        self.seen.append(subject)
        return [{"scope": subject.scope}]


def _fact(text: str) -> dict:
    return {"text": text, "importance": 7, "entity": "master"}


def test_subject_factories_are_platform_neutral_and_stable():
    group = MemorySubject.group_chat("qq", "7788")
    member = MemorySubject.participant("discord", "alice")
    membership = MemorySubject.group_participant("telegram", "g1", "u2")

    assert group.key == "group_chat:qq:7788"
    assert group.scope == group.key
    assert member.subject_id == "discord:alice"
    assert membership.subject_id == "telegram:g1:u2"
    assert membership.persona_section_key.startswith("@subject/")


def test_legacy_rows_default_to_private_and_never_become_global():
    legacy = {"id": "old", "text": "private"}
    group = MemorySubject.group_chat("qq", "7788")
    scoped = {"id": "group", "text": "shared", **group.as_entry_fields()}

    assert effective_scope(legacy) == LEGACY_PRIVATE_SCOPE
    assert filter_entries_for_subjects([legacy, scoped]) == [legacy]
    assert filter_entries_for_subjects([legacy, scoped], [group]) == [scoped]


def test_malformed_partial_scope_fails_closed_as_legacy_private():
    malformed = {
        "id": "broken",
        "text": "must not leak",
        "subject_kind": "group_chat",
    }
    group = MemorySubject.group_chat("qq", "7788")
    assert filter_entries_for_subjects([malformed], [group]) == []
    assert filter_entries_for_subjects([malformed]) == []
    assert effective_scope(malformed) == LEGACY_PRIVATE_SCOPE


def test_rejects_legacy_private_as_a_new_subject_scope():
    with pytest.raises(MemoryScopeError):
        MemorySubject.create("group_chat", "qq:7788", scope=LEGACY_PRIVATE_SCOPE)


@pytest.mark.asyncio
async def test_exact_dedup_is_isolated_by_subject_and_entity_is_forced():
    harness = _PersistHarness()
    group_a = MemorySubject.group_chat("qq", "100")
    group_b = MemorySubject.group_chat("qq", "200")

    first = await harness._apersist_new_facts(
        "Neko", [_fact("周五八点开黑")], subject=group_a, semantic_dedup=False,
    )
    retry = await harness._apersist_new_facts(
        "Neko", [_fact("周五八点开黑")], subject=group_a, semantic_dedup=False,
    )
    other_group = await harness._apersist_new_facts(
        "Neko", [_fact("周五八点开黑")], subject=group_b, semantic_dedup=False,
    )

    assert len(first) == 1
    assert retry == []
    assert len(other_group) == 1
    assert first[0]["entity"] == "group_chat"
    assert first[0]["scope"] == "group_chat:qq:100"
    assert first[0]["hash"] != other_group[0]["hash"]


@pytest.mark.asyncio
async def test_fts_semantic_hit_from_another_group_does_not_dedup():
    index = _FakeTimeIndexed()
    harness = _PersistHarness(index)
    group_a = MemorySubject.group_chat("qq", "100")
    group_b = MemorySubject.group_chat("qq", "200")

    first = await harness._apersist_new_facts(
        "Neko", [_fact("周五晚上八点一起玩")], subject=group_a, semantic_dedup=False,
    )
    index.hits = [(first[0]["id"], -10.0)]
    created = await harness._apersist_new_facts(
        "Neko", [_fact("周五晚八点开黑")], subject=group_b, semantic_dedup=True,
    )
    assert len(created) == 1


@pytest.mark.asyncio
async def test_unabsorbed_facts_are_partitioned_by_subject():
    harness = _PersistHarness()
    group = MemorySubject.group_chat("qq", "100")
    await harness._apersist_new_facts(
        "Neko", [_fact("群事实")], subject=group, semantic_dedup=False,
    )
    await harness._apersist_new_facts(
        "Neko", [_fact("私人事实")], semantic_dedup=False,
    )

    legacy = await harness.aget_unabsorbed_facts("Neko")
    scoped = await harness.aget_unabsorbed_facts("Neko", subject=group)
    assert [item["text"] for item in legacy] == ["私人事实"]
    assert [item["text"] for item in scoped] == ["群事实"]


@pytest.mark.asyncio
async def test_stage2_dequeues_scoped_strays_and_keeps_legacy_batch():
    """Stage-2 evidence belongs to the legacy-private pipeline only. Scoped
    facts are written with signal_processed=True and never enqueue; any
    stray row (older builds / corrupt subject metadata) must be defensively
    dequeued — otherwise high-importance, old-created_at strays would
    permanently occupy top-N batch slots and starve the private chain."""
    harness = _PersistHarness()
    group_a = MemorySubject.group_chat("qq", "100")
    harness._mem = [
        {
            "id": "stray-scoped",
            "text": "A 群事实",
            "importance": 9,
            "created_at": "2026-07-01T00:00:00",
            "source": "user_observation",
            "signal_processed": False,
            **group_a.as_entry_fields(),
        },
        {
            "id": "stray-corrupt",
            "text": "subject 元数据损坏",
            "importance": 9,
            "created_at": "2026-07-01T00:00:01",
            "source": "user_observation",
            "signal_processed": False,
            "subject_kind": "group_chat",
        },
        {
            # 没有 id 的 stray：标记不了，但绝不能混进 legacy 批次。
            "text": "无 id 的群事实",
            "importance": 9,
            "created_at": "2026-07-01T00:00:02",
            "source": "user_observation",
            "signal_processed": False,
            **group_a.as_entry_fields(),
        },
        {
            "id": "legacy",
            "text": "私聊事实",
            "importance": 5,
            "created_at": "2026-07-22T00:00:00",
            "source": "user_observation",
            "signal_processed": False,
        },
    ]
    harness._allm_extract_facts = AsyncMock(return_value=[])
    marked: list[str] = []

    async def _record_mark(name, fact_ids):
        marked.extend(fact_ids)

    harness.amark_signal_processed = _record_mark
    harness._aload_signal_targets = AsyncMock(
        return_value=[{"id": "reflection.target"}],
    )
    harness._allm_detect_signals = AsyncMock(return_value=[])

    _persisted, signals, batch_ids = (
        await harness.aextract_facts_and_detect_signals("Neko", [])
    )

    assert signals == []
    assert sorted(marked) == ["stray-corrupt", "stray-scoped"]
    assert batch_ids == ["legacy"]
    for call in harness._aload_signal_targets.await_args_list:
        assert [fact["id"] for fact in call.kwargs["new_facts"]] == ["legacy"]
    for call in harness._allm_detect_signals.await_args_list:
        assert [fact["id"] for fact in call.args[1]] == ["legacy"]


@pytest.mark.asyncio
async def test_scoped_fact_writes_skip_stage2_queue():
    """Simplified group pipeline: scoped facts persist with
    signal_processed=True; legacy user_observation stays False and enters
    Stage-2 normally."""
    harness = _PersistHarness()
    group = MemorySubject.group_chat("qq", "100")

    scoped = await harness._apersist_new_facts(
        "Neko", [_fact("群事实")], subject=group, semantic_dedup=False,
    )
    legacy = await harness._apersist_new_facts(
        "Neko", [_fact("私聊事实")], semantic_dedup=False,
    )

    assert scoped[0]["signal_processed"] is True
    assert legacy[0]["signal_processed"] is False


@pytest.mark.asyncio
async def test_scoped_sha_upgrade_does_not_reenter_stage2():
    """Monotonic ai_disclosure→user_observation upgrade on SHA hit: legacy
    resets signal_processed=False to re-enter Stage-2; scoped upgrades the
    source but keeps signal_processed=True."""
    harness = _PersistHarness()
    group = MemorySubject.group_chat("qq", "100")

    first = await harness._apersist_new_facts(
        "Neko",
        [{**_fact("群友说周五开黑"), "source": "ai_disclosure"}],
        subject=group, semantic_dedup=False,
    )
    assert first[0]["signal_processed"] is True

    upgraded = await harness._apersist_new_facts(
        "Neko",
        [{**_fact("群友说周五开黑"), "source": "user_observation"}],
        subject=group, semantic_dedup=False,
    )
    assert upgraded == []
    assert harness._mem[0]["source"] == "user_observation"
    assert harness._mem[0]["signal_processed"] is True


@pytest.mark.asyncio
async def test_hybrid_recall_filters_scope_before_rankers():
    group_a = MemorySubject.group_chat("qq", "100")
    group_b = MemorySubject.group_chat("qq", "200")
    facts = [
        {"id": "legacy", "text": "周五八点开黑", "score": 1.0},
        {"id": "a", "text": "周五八点开黑", "score": 1.0, **group_a.as_entry_fields()},
        {"id": "b", "text": "周五八点开黑", "score": 1.0, **group_b.as_entry_fields()},
    ]
    fact_store = MagicMock()
    fact_store.aload_facts = AsyncMock(return_value=facts)
    fact_store._facts_archive_path = MagicMock(return_value="missing.json")
    reflection_engine = MagicMock()
    reflection_engine.aload_reflections = AsyncMock(return_value=[])

    with patch("memory.hybrid_recall._cosine_rank", new=AsyncMock(return_value=[])), \
         patch("memory.hybrid_recall.HYBRID_RECALL_BM25_THRESHOLD", 0.0):
        result = await hybrid_recall(
            lanlan_name="Neko",
            query="周五 开黑",
            fact_store=fact_store,
            reflection_engine=reflection_engine,
            config_manager=MagicMock(),
            subjects=[group_a],
        )

    assert [item["id"] for item in result["results"]] == ["a"]
    assert result["candidates_total"] == 1
    assert result["results"][0]["scope"] == group_a.scope


def test_persona_view_only_exposes_authorized_scoped_sections():
    group_a = MemorySubject.group_chat("qq", "100")
    group_b = MemorySubject.group_chat("qq", "200")
    persona = {
        "master": {"facts": [{"text": "private"}]},
        group_a.persona_section_key: {
            # Entries carry subject stamps exactly like the real writer
            # (add_fact) produces them — authorization is per entry.
            **group_a.as_entry_fields(),
            "facts": [{"text": "group a", **group_a.as_entry_fields()}],
        },
        group_b.persona_section_key: {
            **group_b.as_entry_fields(),
            "facts": [{"text": "group b", **group_b.as_entry_fields()}],
        },
    }

    legacy_view = RenderingMixin._persona_view_for_subjects(persona)
    group_view = RenderingMixin._persona_view_for_subjects(persona, [group_a])
    assert list(legacy_view) == ["master"]
    assert list(group_view) == [group_a.persona_section_key]


def test_persona_fact_persists_scope_on_section_and_entry():
    harness = _PersonaHarness()
    group = MemorySubject.group_chat("qq", "100")
    result = harness.add_fact("Neko", "群规是不要剧透", subject=group)

    assert result == harness.FACT_ADDED
    section = harness.persona[group.persona_section_key]
    assert section["subject_kind"] == "group_chat"
    assert section["scope"] == group.scope
    assert section["facts"][0]["scope"] == group.scope
    assert "master" not in harness.persona

    replacement = harness._normalize_entry_for_section(
        harness.persona, group.persona_section_key, "群规更新为禁止剧透",
    )
    assert replacement["subject_kind"] == "group_chat"
    assert replacement["subject_id"] == "qq:100"
    assert replacement["scope"] == group.scope


@pytest.mark.asyncio
async def test_scoped_reflection_scheduler_is_bounded_and_grouped():
    group_a = MemorySubject.group_chat("qq", "100")
    group_b = MemorySubject.group_chat("qq", "200")
    facts = []
    for index in range(5):
        facts.append({
            "id": f"a{index}", "text": "a", "importance": 7,
            "created_at": f"2026-07-20T00:00:0{index}",
            **group_a.as_entry_fields(),
        })
        facts.append({
            "id": f"b{index}", "text": "b", "importance": 7,
            "created_at": f"2026-07-21T00:00:0{index}",
            **group_b.as_entry_fields(),
        })
    harness = _ScopedSynthesisHarness(facts)

    created = await harness.synthesize_scoped_reflections("Neko", max_subjects=1)
    assert len(created) == 1
    assert harness.seen == [group_a]


def test_qq_subject_mapping_uses_generic_memory_entities():
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge

    assert QQMemoryBridge.group_subject("7788") == {
        "subject_kind": "group_chat",
        "subject_id": "qq:7788",
    }
    assert QQMemoryBridge.group_participant_subject("7788", "2046") == {
        "subject_kind": "group_participant",
        "subject_id": "qq:7788:2046",
    }


@pytest.mark.asyncio
async def test_qq_group_bootstrap_never_reads_legacy_private_memory():
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    bridge = MagicMock()
    bridge.group_subject.side_effect = QQMemoryBridge.group_subject
    bridge.group_participant_subject.side_effect = (
        QQMemoryBridge.group_participant_subject
    )
    bridge.fetch_scoped_bootstrap_memory = AsyncMock(return_value="群聊长期记忆")
    bridge.fetch_bootstrap_memory = AsyncMock(return_value="私人长期记忆")
    plugin = SimpleNamespace(
        memory_bridge=bridge, logger=MagicMock(),
        _qq_settings={"group_memory_enabled": True},
    )
    service = QQSessionInstructionService(plugin)

    rendered = await service._build_core_memory_section(
        should_use_memory_context=True,
        her_name="Neko",
        master_name="Master",
        context_ready_template="{name}/{master}",
        is_group=True,
        group_id="7788",
        sender_id="2046",
    )

    assert "群聊长期记忆" in rendered
    assert "私人长期记忆" not in rendered
    bridge.fetch_bootstrap_memory.assert_not_awaited()
    bridge.fetch_scoped_bootstrap_memory.assert_awaited_once_with(
        "Neko",
        subjects=[
            QQMemoryBridge.group_subject("7788"),
            QQMemoryBridge.group_participant_subject("7788", "2046"),
        ],
    )


@pytest.mark.asyncio
async def test_qq_private_bootstrap_keeps_legacy_behavior():
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    bridge = MagicMock()
    bridge.fetch_bootstrap_memory = AsyncMock(return_value="旧私人记忆")
    bridge.fetch_scoped_bootstrap_memory = AsyncMock()
    plugin = SimpleNamespace(memory_bridge=bridge, logger=MagicMock())
    service = QQSessionInstructionService(plugin)

    rendered = await service._build_core_memory_section(
        should_use_memory_context=True,
        her_name="Neko",
        master_name="Master",
        context_ready_template="{name}/{master}",
    )

    assert "旧私人记忆" in rendered
    bridge.fetch_bootstrap_memory.assert_awaited_once_with("Neko")
    bridge.fetch_scoped_bootstrap_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_qq_group_recall_passes_group_and_member_subjects():
    from plugin.plugins.qq_auto_reply.memory_bridge import (
        QQMemoryBridge,
        QQMemoryQueryResult,
    )
    from plugin.plugins.qq_auto_reply.reply_context_node import QQReplyContextNode

    bridge = MagicMock()
    bridge.group_subject.side_effect = QQMemoryBridge.group_subject
    bridge.group_participant_subject.side_effect = (
        QQMemoryBridge.group_participant_subject
    )
    bridge.query_relevant_memory = AsyncMock(
        return_value=QQMemoryQueryResult(text="群规是不剧透", hit_count=1),
    )
    plugin = SimpleNamespace(
        memory_bridge=bridge,
        logger=MagicMock(),
        _qq_settings={"group_memory_enabled": True},
        _should_skip_direct_llm_fallback_for_images=lambda **kwargs: False,
    )

    rendered = await QQReplyContextNode(plugin)._build_recalled_memory_text(
        her_name="Neko",
        message="群规是什么？",
        should_use_memory_context=True,
        attachments=None,
        is_group=True,
        group_id="7788",
        sender_id="2046",
    )

    assert "群规是不剧透" in rendered
    bridge.query_relevant_memory.assert_awaited_once_with(
        "Neko",
        "群规是什么？",
        subjects=[
            QQMemoryBridge.group_subject("7788"),
            QQMemoryBridge.group_participant_subject("7788", "2046"),
        ],
    )


@pytest.mark.asyncio
async def test_qq_group_recall_omits_phantom_member_for_empty_sender():
    from plugin.plugins.qq_auto_reply.memory_bridge import (
        QQMemoryBridge,
        QQMemoryQueryResult,
    )
    from plugin.plugins.qq_auto_reply.reply_context_node import QQReplyContextNode

    bridge = MagicMock()
    bridge.group_subject.side_effect = QQMemoryBridge.group_subject
    bridge.group_participant_subject.side_effect = (
        QQMemoryBridge.group_participant_subject
    )
    bridge.query_relevant_memory = AsyncMock(return_value=QQMemoryQueryResult())
    plugin = SimpleNamespace(
        memory_bridge=bridge,
        logger=MagicMock(),
        _qq_settings={"group_memory_enabled": True},
        _should_skip_direct_llm_fallback_for_images=lambda **kwargs: False,
    )

    await QQReplyContextNode(plugin)._build_recalled_memory_text(
        her_name="Neko",
        message="群规是什么？",
        should_use_memory_context=True,
        attachments=None,
        is_group=True,
        group_id="7788",
        sender_id="",
    )

    bridge.query_relevant_memory.assert_awaited_once_with(
        "Neko",
        "群规是什么？",
        subjects=[QQMemoryBridge.group_subject("7788")],
    )
    bridge.group_participant_subject.assert_not_called()


@pytest.mark.asyncio
async def test_qq_recall_with_empty_subjects_never_falls_back_to_private():
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge

    bridge = QQMemoryBridge(SimpleNamespace())
    with patch(
        "plugin.plugins.qq_auto_reply.memory_bridge.httpx.AsyncClient",
    ) as client:
        result = await bridge.query_relevant_memory(
            "Neko", "不应读取私聊记忆", subjects=[],
        )

    assert result.text == ""
    assert result.raw_results == []
    client.assert_not_called()


@pytest.mark.asyncio
async def test_qq_group_session_writes_only_scoped_history():
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    history = [
        SimpleNamespace(type="human", content="记住群规是不剧透"),
        SimpleNamespace(type="ai", content="知道了"),
    ]
    session = SimpleNamespace(_conversation_history=history, close=AsyncMock())
    bridge = MagicMock()
    bridge.group_subject.side_effect = QQMemoryBridge.group_subject
    bridge.group_participant_subject.side_effect = (
        QQMemoryBridge.group_participant_subject
    )
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    bridge.post_memory_history = AsyncMock(return_value={"status": "ok"})
    user_data = {
        "memory_enabled": True,
        "is_group": True,
        "group_id": "7788",
        "her_name": "Neko",
        "session": session,
        "group_member_memory_messages": {
            "2046": [
                {"role": "user", "content": [{"type": "text", "text": "我最喜欢三文鱼"}]},
            ],
        },
    }
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _qq_settings={"group_member_memory_enabled": True},
        memory_bridge=bridge,
        logger=MagicMock(),
    )
    service = QQSessionMemoryService(plugin)

    assert await service.cache_session_delta("group:7788", user_data) == 0
    completed = await service.finalize_user_memory_session(
        "group:7788", reason="test",
    )

    assert completed is True
    bridge.post_scoped_memory_history.assert_any_await(
        "Neko",
        [
            {"role": "user", "content": [{"type": "text", "text": "记住群规是不剧透"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "知道了"}]},
        ],
        subject=QQMemoryBridge.group_subject("7788"),
        timeout=30.0,
    )
    bridge.post_scoped_memory_history.assert_any_await(
        "Neko",
        [{"role": "user", "content": [{"type": "text", "text": "我最喜欢三文鱼"}]}],
        subject=QQMemoryBridge.group_participant_subject("7788", "2046"),
        speaker_label="2046",
        timeout=30.0,
    )
    assert bridge.post_scoped_memory_history.await_count == 2
    bridge.post_memory_history.assert_not_awaited()
    assert "group:7788" not in plugin._user_sessions


@pytest.mark.asyncio
async def test_qq_member_flush_continues_and_retries_only_failed_buckets():
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    history = [SimpleNamespace(type="human", content="群消息")]
    session = SimpleNamespace(_conversation_history=history, close=AsyncMock())
    bridge = MagicMock()
    bridge.group_subject.side_effect = QQMemoryBridge.group_subject
    bridge.group_participant_subject.side_effect = (
        QQMemoryBridge.group_participant_subject
    )
    bridge.post_scoped_memory_history = AsyncMock(side_effect=[
        {"status": "ok"},
        {"status": "error", "message": "member 2046 failed"},
        {"status": "ok"},
    ])
    failed_member_messages = [
        {"role": "user", "content": [{"type": "text", "text": "A"}]},
    ]
    member_buckets = {
        "2046": failed_member_messages,
        "4096": [
            {"role": "user", "content": [{"type": "text", "text": "B"}]},
        ],
    }
    user_data = {
        "memory_enabled": True,
        "is_group": True,
        "group_id": "7788",
        "her_name": "Neko",
        "session": session,
        "group_member_memory_messages": member_buckets,
    }
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _qq_settings={"group_member_memory_enabled": True},
        memory_bridge=bridge,
        logger=MagicMock(),
    )
    service = QQSessionMemoryService(plugin)

    completed = await service.finalize_user_memory_session(
        "group:7788", reason="test",
    )

    assert completed is False
    assert bridge.post_scoped_memory_history.await_count == 3
    assert user_data["group_memory_flushed"] is True
    assert list(member_buckets) == ["2046"]
    assert "group:7788" in plugin._user_sessions
    session.close.assert_not_awaited()

    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    completed = await service.finalize_user_memory_session(
        "group:7788", reason="retry",
    )

    assert completed is True
    bridge.post_scoped_memory_history.assert_awaited_once_with(
        "Neko",
        failed_member_messages,
        subject=QQMemoryBridge.group_participant_subject("7788", "2046"),
        speaker_label="2046",
        timeout=30.0,
    )
    assert member_buckets == {}
    assert "group:7788" not in plugin._user_sessions
    session.close.assert_awaited_once()


def test_qq_group_member_turns_are_opt_in_and_actor_attributed():
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    plugin = SimpleNamespace(_qq_settings={"group_member_memory_enabled": True})
    service = QQSessionMemoryService(plugin)
    user_data: dict = {}
    # Consent is bound to the turn's build time: a turn built while member
    # memory was OFF must not be retroactively collected just because the
    # setting flipped ON before generation finished.
    service.record_group_member_turn(
        user_data,
        SimpleNamespace(
            is_group=True, sender_id="1024", message="开关打开前说的话",
            member_memory_enabled=False,
        ),
    )
    assert "group_member_memory_messages" not in user_data
    service.record_group_member_turn(
        user_data,
        SimpleNamespace(
            is_group=True, sender_id="2046", message="我喜欢三文鱼",
            member_memory_enabled=True,
        ),
    )
    service.record_group_member_turn(
        user_data,
        SimpleNamespace(
            is_group=True, sender_id="4096", message="我周五有空",
            user_nickname="Bob", member_memory_enabled=True,
        ),
    )

    assert list(user_data["group_member_memory_messages"]) == ["2046", "4096"]
    assert user_data["group_member_memory_messages"]["2046"][0]["content"][0]["text"] == "我喜欢三文鱼"
    # Speaker labels recorded for extraction attribution: nickname when
    # known, bare sender id otherwise.
    assert user_data["group_member_memory_labels"] == {
        "2046": "2046",
        "4096": "Bob(4096)",
    }
    # Synthetic / group-facing turns (proactive control prompts) are not
    # member speech and must never enter a member bucket.
    service.record_group_member_turn(
        user_data,
        SimpleNamespace(
            is_group=True, sender_id="9999",
            message="[系统] 群聊已经安静了",
            group_scene_mode="group_collective", group_facing=True,
            member_memory_enabled=True,
        ),
    )
    assert "9999" not in user_data["group_member_memory_messages"]
    # Rapid-fire control prompts resolve to shared_context but carry a
    # synthetic source_kind — also excluded.
    service.record_group_member_turn(
        user_data,
        SimpleNamespace(
            is_group=True, sender_id="8888",
            message="[系统] 合并的缓冲消息",
            group_scene_mode="shared_context", group_facing=False,
            source_kind="rapid_fire_flush",
            member_memory_enabled=True,
        ),
    )
    assert "8888" not in user_data["group_member_memory_messages"]
    # Retroactive review turns are built at review time: the consent
    # snapshot cannot see the utterance-time policy (the original message
    # may date from an opted-out era) and the text is synthetic framing.
    service.record_group_member_turn(
        user_data,
        SimpleNamespace(
            is_group=True, sender_id="7777",
            message="[回溯补回] Bob 之前说：旧消息",
            group_scene_mode="shared_context", group_facing=False,
            source_kind="retroactive_review", member_memory_enabled=True,
        ),
    )
    assert "7777" not in user_data["group_member_memory_messages"]
    # Group-join welcome prompts are fabricated control instructions, not
    # the joining member's speech.
    service.record_group_member_turn(
        user_data,
        SimpleNamespace(
            is_group=True, sender_id="6666",
            message="[系统] 新成员 6666 加入了群聊",
            group_scene_mode="shared_context", group_facing=False,
            source_kind="group_join_notice", member_memory_enabled=True,
        ),
    )
    assert "6666" not in user_data["group_member_memory_messages"]


def test_entry_missing_scope_fails_closed():
    """A stored entry carrying subject_kind/subject_id but no scope must be
    quarantined, not silently normalized into the default-scope domain — a
    custom-scope row that lost its scope would otherwise cross its isolation
    boundary."""
    from memory.scopes import is_legacy_private_entry, subject_from_entry

    partial = {"subject_kind": "group_chat", "subject_id": "qq:1"}
    assert subject_from_entry(partial) is None
    assert not is_legacy_private_entry(partial)
    group = MemorySubject.group_chat("qq", "1")
    assert filter_entries_for_subjects([partial], [group]) == []
    assert filter_entries_for_subjects([partial]) == []
    # An explicitly EMPTY scope in a request is malformed, not omitted:
    # silently normalizing it into the default domain would merge a
    # malformed caller into the default isolation boundary.
    import pytest as _pytest

    from memory.scopes import MemoryScopeError

    with _pytest.raises(MemoryScopeError):
        MemorySubject.create("group_chat", "qq:1", scope="")


@pytest.mark.asyncio
async def test_qq_group_memory_config_enables_read_and_write_on_requests():
    from plugin.plugins.qq_auto_reply.message_dispatcher import QQMessageDispatcher

    pipeline = SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(action="ignore", reply_text="")),
    )
    runtime_service = SimpleNamespace(record_pipeline_outcome=MagicMock())
    plugin = SimpleNamespace(
        _strategy_mode="neko_scene",
        _qq_settings={"group_memory_enabled": True},
        reply_pipeline=pipeline,
        runtime_service=runtime_service,
        attention_service=None,
    )
    dispatcher = QQMessageDispatcher(plugin)
    dispatcher._detect_group_interjection_suppression = AsyncMock(return_value="")

    await dispatcher.handle_group_message(
        "7788", "2046", "请记住群规", is_at_bot=True,
    )

    request = pipeline.run.await_args.args[0]
    assert request.use_memory_context is True
    assert request.persist_memory is True


def test_qq_group_memory_defaults_are_explicit_and_safe(tmp_path):
    from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore

    config = QQAutoReplyConfigStore(tmp_path).default_config()
    assert config["group_memory_enabled"] is False
    assert config["group_member_memory_enabled"] is False
    assert config["allow_cross_group_context"] is False


def test_scoped_fact_importance_is_bounded():
    from pydantic import ValidationError

    from app.memory_server.routes import ScopedFactInput

    assert ScopedFactInput(text="low", importance=1).importance == 1
    assert ScopedFactInput(text="high", importance=10).importance == 10
    with pytest.raises(ValidationError):
        ScopedFactInput(text="too low", importance=0)
    with pytest.raises(ValidationError):
        ScopedFactInput(text="too high", importance=11)


@pytest.mark.asyncio
async def test_query_memory_route_rejects_explicit_empty_subjects():
    """Server-side fail-closed: an explicit subjects=[] is a caller contract
    bug and must 422 — never collapse into None and fall back to the
    legacy-private corpus (mirrors scoped_context)."""
    from fastapi import HTTPException

    from app.memory_server import routes as memory_routes
    from app.memory_server.routes import QueryMemoryRequest

    with patch.object(memory_routes.runtime, "fact_store", MagicMock()), \
         patch.object(memory_routes.runtime, "reflection_engine", MagicMock()):
        with pytest.raises(HTTPException) as excinfo:
            await memory_routes.query_memory(
                "Neko", QueryMemoryRequest(query="hello", subjects=[]),
            )
        assert excinfo.value.status_code == 422

        too_many = [
            {"subject_kind": "group_chat", "subject_id": f"qq:{index}"}
            for index in range(9)
        ]
        with pytest.raises(HTTPException) as excinfo:
            await memory_routes.query_memory(
                "Neko", QueryMemoryRequest(query="hello", subjects=too_many),
            )
        assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_scoped_synthesis_rotates_between_subjects():
    """Rotation cursor: a dead-letter / failing bucket must not monopolize
    the single per-tick slot. Consecutive calls serve different subjects,
    and a failed attempt (empty return) still advances the cursor."""
    group_a = MemorySubject.group_chat("qq", "100")
    group_b = MemorySubject.group_chat("qq", "200")
    facts = []
    for index in range(5):
        facts.append({
            "id": f"a{index}", "text": "a", "importance": 7,
            "created_at": f"2026-07-20T00:00:0{index}",
            **group_a.as_entry_fields(),
        })
        facts.append({
            "id": f"b{index}", "text": "b", "importance": 7,
            "created_at": f"2026-07-21T00:00:0{index}",
            **group_b.as_entry_fields(),
        })
    harness = _ScopedSynthesisHarness(facts)
    # 模拟 group_a 合成失败（dead-letter：返回空）——它仍不能霸占名额。
    original = harness.synthesize_reflections

    async def _flaky(lanlan_name, *, subject=None):
        await original(lanlan_name, subject=subject)
        return []

    harness.synthesize_reflections = _flaky

    await harness.synthesize_scoped_reflections("Neko", max_subjects=1)
    await harness.synthesize_scoped_reflections("Neko", max_subjects=1)
    await harness.synthesize_scoped_reflections("Neko", max_subjects=1)
    assert harness.seen == [group_a, group_b, group_a]


@pytest.mark.asyncio
async def test_scoped_synthesis_skips_malformed_rows():
    """load_facts preserves legacy/hand-edited non-dict rows: one corrupted
    row must not raise and disable scoped synthesis for the whole character
    forever (the maintenance tick retries the same character every time)."""
    group_a = MemorySubject.group_chat("qq", "100")
    # Sorts before group_a in the rotation: if the no-id row below were
    # admitted, this subject would reach the readiness threshold and win
    # the single per-tick slot — making the guard observable.
    group_b = MemorySubject.group_chat("qq", "050")
    facts = ["corrupted-string-row"]
    facts += [
        {
            "id": f"a{index}", "text": "a", "importance": 7,
            "created_at": f"2026-07-27T00:00:0{index}",
            **group_a.as_entry_fields(),
        }
        for index in range(5)
    ]
    facts += [
        {
            "id": f"b{index}", "text": "b", "importance": 7,
            "created_at": f"2026-07-27T00:01:0{index}",
            **group_b.as_entry_fields(),
        }
        for index in range(4)
    ]
    # Valid subject fields but no stable id: synthesize_reflections sorts
    # on f['id'], so this row must be dropped at grouping — it must NOT
    # count toward group_b's readiness threshold.
    facts.append({"text": "no-id", "importance": 7, **group_b.as_entry_fields()})
    harness = _ScopedSynthesisHarness(facts)
    await harness.synthesize_scoped_reflections("Neko", max_subjects=1)
    assert harness.seen == [group_a]


@pytest.mark.asyncio
async def test_unabsorbed_getter_skips_malformed_rows():
    """Scoped synthesis re-enters FactStore.aget_unabsorbed_facts after its
    own grouping guard: the getter itself must skip non-dict rows or one
    corrupted row still raises through every caller."""
    group_a = MemorySubject.group_chat("qq", "100")
    good = {
        "id": "a0", "text": "a", "importance": 7,
        **group_a.as_entry_fields(),
    }
    fs = FactStore.__new__(FactStore)
    no_id = {"text": "b", "importance": 7, **group_a.as_entry_fields()}
    fs.aload_facts = AsyncMock(return_value=["corrupted-row", no_id, good])
    result = await fs.aget_unabsorbed_facts("Neko", subject=group_a)
    assert result == [good]


@pytest.mark.asyncio
async def test_stage2_observation_pool_respects_subject_boundary():
    """Real _aload_signal_targets (no mock): a scoped trigger batch may only
    see same-subject observation targets and a legacy batch only legacy
    ones — the safety boundary the code comments promise needs a direct
    test (removing the filter previously turned no test red)."""
    import threading

    group_a = MemorySubject.group_chat("qq", "100")
    group_b = MemorySubject.group_chat("qq", "200")

    fs = FactStore.__new__(FactStore)
    fs._config_manager = MagicMock()
    fs._time_indexed = None
    fs._facts = {}
    fs._locks = {}
    fs._locks_guard = threading.Lock()
    fs._persist_alocks = {}

    reflection_engine = SimpleNamespace(
        _aload_reflections_full=AsyncMock(return_value=[
            {"id": "r-legacy", "status": "confirmed", "text": "legacy refl",
             "entity": "master"},
            {"id": "r-a", "status": "confirmed", "text": "group a refl",
             "entity": "group_chat", **group_a.as_entry_fields()},
            {"id": "r-b", "status": "confirmed", "text": "group b refl",
             "entity": "group_chat", **group_b.as_entry_fields()},
        ]),
    )
    persona_manager = SimpleNamespace(
        aensure_persona=AsyncMock(return_value={
            "master": {"facts": [{"id": "p-legacy", "text": "legacy persona"}]},
            group_a.persona_section_key: {
                **group_a.as_entry_fields(),
                "facts": [{
                    "id": "p-a", "text": "group a persona",
                    **group_a.as_entry_fields(),
                }],
            },
        }),
    )

    scoped_batch = [{
        "id": "fa", "text": "群事实", "importance": 7,
        **group_a.as_entry_fields(),
    }]
    legacy_batch = [{"id": "fl", "text": "私聊事实", "importance": 7}]

    scoped_pool = await fs._aload_signal_targets(
        "Neko", reflection_engine=reflection_engine,
        persona_manager=persona_manager, new_facts=scoped_batch,
    )
    legacy_pool = await fs._aload_signal_targets(
        "Neko", reflection_engine=reflection_engine,
        persona_manager=persona_manager, new_facts=legacy_batch,
    )

    assert {obs["raw_id"] for obs in scoped_pool} <= {"r-a", "p-a"}
    assert {obs["raw_id"] for obs in scoped_pool} == {"r-a", "p-a"}
    assert {obs["raw_id"] for obs in legacy_pool} == {"r-legacy", "p-legacy"}


def test_persona_view_fails_closed_on_corrupt_scoped_section():
    """A persona section with the @subject/ prefix but corrupt metadata must
    fail closed both ways: never reclassified into the legacy view and
    never served to any scoped view."""
    group = MemorySubject.group_chat("qq", "100")
    corrupt_key = f"@subject/{group.key}"
    persona = {
        "master": {"facts": [{"text": "private"}]},
        corrupt_key: {
            # 缺 subject_id/scope → persona_subject_from_section 返 None
            "subject_kind": "group_chat",
            "facts": [{"text": "must not leak"}],
        },
    }

    legacy_view = RenderingMixin._persona_view_for_subjects(persona)
    scoped_view = RenderingMixin._persona_view_for_subjects(persona, [group])
    assert list(legacy_view) == ["master"]
    assert scoped_view == {}


def test_fact_vector_dedup_pairs_stay_inside_subject_boundary():
    """Vector-dedup candidate bucketing must carry the subject boundary:
    facts from different groups never pair even with identical embeddings
    (merge/replace would delete data across groups); corrupt-subject rows
    never participate at all."""
    from memory.fact_dedup import FactDedupResolver

    group_a = MemorySubject.group_chat("qq", "100")
    group_b = MemorySubject.group_chat("qq", "200")
    vec = [1.0, 0.0, 0.0]

    def _row(fact_id, extra):
        return {
            "id": fact_id, "text": f"text {fact_id}", "entity": "group_chat",
            "embedding": vec, "embedding_model_id": "m1", **extra,
        }

    cross_group = FactDedupResolver.detect_candidates([
        _row("a1", group_a.as_entry_fields()),
        _row("b1", group_b.as_entry_fields()),
    ])
    assert cross_group == []

    same_group = FactDedupResolver.detect_candidates([
        _row("a1", group_a.as_entry_fields()),
        _row("a2", group_a.as_entry_fields()),
    ])
    assert {pair["candidate_id"] for pair in same_group} == {"a1", "a2"}

    with_corrupt = FactDedupResolver.detect_candidates([
        _row("a1", group_a.as_entry_fields()),
        _row("bad", {"subject_kind": "group_chat"}),
    ])
    assert with_corrupt == []


def _build_scope_mock_cm(tmpdir: str):
    cm = MagicMock()
    cm.memory_dir = tmpdir
    cm.aget_character_data = AsyncMock(return_value=(
        "主人", "Neko", {}, {}, {"human": "主人", "system": "SYS"},
        {}, {}, {}, {},
    ))
    cm.get_character_data = MagicMock(return_value=(
        "主人", "Neko", {}, {}, {"human": "主人", "system": "SYS"},
        {}, {}, {}, {},
    ))
    cm.get_model_api_config = MagicMock(return_value={
        "model": "fake-model", "base_url": "http://fake", "api_key": "sk-fake",
    })
    return cm


@pytest.mark.asyncio
async def test_scoped_synthesis_creates_confirmed_reflection(tmp_path):
    """Simplified group pipeline: scoped reflection synthesis lands directly
    as confirmed (scoped subjects have no Stage-2 signals and no surfacing
    confirmation channel, so pending would be a permanent dead end)."""
    import json
    import os

    mock_cm = _build_scope_mock_cm(str(tmp_path))
    group = MemorySubject.group_chat("qq", "100")
    char_dir = os.path.join(str(tmp_path), "Neko")
    os.makedirs(char_dir, exist_ok=True)
    facts = [
        {
            # importance 5（ScopedFactInput 默认档）——importance 种子为 0，
            # 钉住「直出 confirmed 必须带最小正 rein，过 score>0 渲染门」。
            "id": f"g{index}", "text": f"群事实 {index}",
            "entity": "group_chat", "importance": 5, "absorbed": False,
            **group.as_entry_fields(),
        }
        for index in range(6)
    ]
    with open(os.path.join(char_dir, "facts.json"), "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False)

    with patch("memory.reflection.manager.get_config_manager", return_value=mock_cm), \
         patch("memory.facts.get_config_manager", return_value=mock_cm):
        from memory.persona import PersonaManager
        from memory.reflection import ReflectionEngine

        fs = FactStore()
        fs._config_manager = mock_cm
        pm = PersonaManager()
        pm._config_manager = mock_cm
        engine = ReflectionEngine(fs, pm)
        engine._config_manager = mock_cm

        async def _fake_ainvoke(self, prompt):
            resp = MagicMock()
            resp.content = (
                '{"reflection": "这个群固定周五晚上开黑", "entity": "group_chat"}'
            )
            return resp

        async def _fake_aclose(self):
            return None

        class _FakeLLM:
            def __init__(self, *a, **kw):
                pass
            ainvoke = _fake_ainvoke
            aclose = _fake_aclose

        with patch("utils.llm_client.create_chat_llm", _FakeLLM), \
             patch(
                 "config.prompts.prompts_memory.get_reflection_prompt",
                 lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}",
             ), \
             patch("utils.language_utils.get_global_language", return_value="zh"):
            created = await engine.synthesize_reflections("Neko", subject=group)

        confirmed_visible = await engine.aget_confirmed_reflections(
            "Neko", subjects=[group], include_legacy_private=False,
        )

    assert len(created) == 1
    assert created[0]["status"] == "confirmed"
    assert created[0]["auto_confirmed"] is True
    assert created[0]["scope"] == group.scope
    assert created[0]["subject_kind"] == "group_chat"
    # score>0 渲染门：即便源 facts 全是默认档 importance，直出 confirmed
    # 的 scoped 反思也必须立即对 /scoped_context 可见。
    assert float(created[0]["reinforcement"]) > 0.0
    assert [r["id"] for r in confirmed_visible] == [created[0]["id"]]


@pytest.mark.asyncio
async def test_scoped_reflections_use_time_driven_lifecycle(tmp_path):
    """Powerful mode: both score-driven passes skip scoped entries; the
    time-driven scoped pass at the tail of aauto_promote_stale advances
    them by age (pending→confirmed→promoted into the scoped persona) while
    legacy entries keep their score-driven behaviour."""
    import json
    import os
    from datetime import datetime, timedelta

    mock_cm = _build_scope_mock_cm(str(tmp_path))
    group = MemorySubject.group_chat("qq", "100")
    now = datetime.now()
    char_dir = os.path.join(str(tmp_path), "Neko")
    os.makedirs(char_dir, exist_ok=True)
    reflections = [
        {
            "id": "ref_legacy", "text": "主人喜欢咖啡", "entity": "master",
            "status": "pending", "created_at": now.isoformat(),
            "reinforcement": 1.5, "rein_last_signal_at": now.isoformat(),
            "source_fact_ids": ["f1"],
        },
        {
            # 历史遗留的 scoped pending（新代码合成直出 confirmed，但旧构建
            # 可能写过 pending）——高分也不许走 score-driven，只按年龄确认。
            "id": "ref_scoped_pending", "text": "这个群周五开黑",
            "entity": "group_chat", "status": "pending",
            "created_at": (now - timedelta(days=8)).isoformat(),
            "reinforcement": 5.0, "rein_last_signal_at": now.isoformat(),
            "source_fact_ids": ["g1"], **group.as_entry_fields(),
        },
        {
            # 高分也不许走 score-driven 促升（_apromote_with_merge 是 LLM
            # 路径）；只能被 time-driven Pass 2 按年龄零成本合入 persona。
            "id": "ref_scoped_confirmed", "text": "群主是老王",
            "entity": "group_chat", "status": "confirmed",
            "created_at": (now - timedelta(days=20)).isoformat(),
            "confirmed_at": (now - timedelta(days=8)).isoformat(),
            "reinforcement": 5.0, "rein_last_signal_at": now.isoformat(),
            "source_fact_ids": ["g2"], **group.as_entry_fields(),
        },
    ]
    with open(
        os.path.join(char_dir, "reflections.json"), "w", encoding="utf-8",
    ) as f:
        json.dump(reflections, f, ensure_ascii=False)

    with patch("memory.reflection.manager.get_config_manager", return_value=mock_cm), \
         patch("memory.facts.get_config_manager", return_value=mock_cm):
        from memory.persona import PersonaManager
        from memory.reflection import ReflectionEngine

        fs = FactStore()
        fs._config_manager = mock_cm
        pm = PersonaManager()
        pm._config_manager = mock_cm
        engine = ReflectionEngine(fs, pm)
        engine._config_manager = mock_cm
        engine._apromote_with_merge = AsyncMock(
            side_effect=AssertionError("scoped 不许进 score-driven merge LLM"),
        )

        await engine.aauto_promote_stale("Neko")

        engine._apromote_with_merge.assert_not_awaited()
        status_by_id = {
            r.get("id"): r for r in await engine._aload_reflections_full("Neko")
        }
        persona = await pm.aensure_persona("Neko")

    assert status_by_id["ref_legacy"]["status"] == "confirmed"
    assert not status_by_id["ref_legacy"].get("auto_confirmed")
    assert status_by_id["ref_scoped_pending"]["status"] == "confirmed"
    assert status_by_id["ref_scoped_pending"].get("auto_confirmed") is True
    assert status_by_id["ref_scoped_confirmed"]["status"] == "promoted"
    scoped_section = persona.get(group.persona_section_key)
    assert scoped_section is not None
    assert any(
        entry.get("text") == "群主是老王"
        for entry in scoped_section.get("facts", [])
    )


@pytest.mark.asyncio
async def test_corrupt_descriptor_never_promotes_in_either_mode(tmp_path):
    """A partially written subject descriptor is neither legacy nor scoped.
    Every promotion lifecycle pass must fail closed on such rows: no
    score-driven confirm/promote, no age-driven confirm/promote, and no
    persona write in either strong or weak memory mode."""
    import json
    import os
    from datetime import datetime, timedelta

    mock_cm = _build_scope_mock_cm(str(tmp_path))
    now = datetime.now()
    char_dir = os.path.join(str(tmp_path), "Neko")
    os.makedirs(char_dir, exist_ok=True)
    # subject_kind set but subject_id/scope missing: subject_from_entry()
    # returns None and is_legacy_private_entry() is False.
    corrupt_fields = {
        "subject_kind": "group_chat", "subject_id": None, "scope": None,
    }
    reflections = [
        {
            # High evidence AND old enough: would pass the score-driven
            # confirm gate and the time-driven age gate if treated as legacy.
            "id": "ref_corrupt_pending", "text": "damaged pending row",
            "entity": "group_chat", "status": "pending",
            "created_at": (now - timedelta(days=8)).isoformat(),
            "reinforcement": 5.0, "rein_last_signal_at": now.isoformat(),
            "source_fact_ids": ["g1"], **corrupt_fields,
        },
        {
            # Same for confirmed → promoted: high score + 8-day-old
            # confirmed_at would hit both promote paths if treated as legacy.
            "id": "ref_corrupt_confirmed", "text": "damaged confirmed row",
            "entity": "group_chat", "status": "confirmed",
            "created_at": (now - timedelta(days=20)).isoformat(),
            "confirmed_at": (now - timedelta(days=8)).isoformat(),
            "reinforcement": 5.0, "rein_last_signal_at": now.isoformat(),
            "source_fact_ids": ["g2"], **corrupt_fields,
        },
    ]
    with open(
        os.path.join(char_dir, "reflections.json"), "w", encoding="utf-8",
    ) as f:
        json.dump(reflections, f, ensure_ascii=False)

    with patch("memory.reflection.manager.get_config_manager", return_value=mock_cm), \
         patch("memory.facts.get_config_manager", return_value=mock_cm):
        from memory.persona import PersonaManager
        from memory.reflection import ReflectionEngine

        fs = FactStore()
        fs._config_manager = mock_cm
        pm = PersonaManager()
        pm._config_manager = mock_cm
        engine = ReflectionEngine(fs, pm)
        engine._config_manager = mock_cm
        engine._apromote_with_merge = AsyncMock(
            side_effect=AssertionError("corrupt row must not reach the merge LLM"),
        )
        engine._persona_manager.aadd_fact = AsyncMock(
            side_effect=AssertionError("corrupt row must not reach persona writes"),
        )

        # Strong mode: score-driven passes + scoped_only time-driven tail.
        await engine.aauto_promote_stale("Neko")
        # Weak mode: age-driven passes over every row.
        await engine.aauto_promote_time_driven("Neko")

        engine._apromote_with_merge.assert_not_awaited()
        engine._persona_manager.aadd_fact.assert_not_awaited()
        by_id = {
            r.get("id"): r for r in await engine._aload_reflections_full("Neko")
        }

    assert by_id["ref_corrupt_pending"]["status"] == "pending"
    assert by_id["ref_corrupt_confirmed"]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_mode_switch_reset_skips_scoped_confirmed(tmp_path):
    """The strong→weak migration resets legacy confirmed_at so old entries
    don't bulk-promote, but scoped reflections run the time-driven clock in
    BOTH modes — resetting them would let a mode toggle postpone scoped
    promotion indefinitely."""
    import json
    import os
    from datetime import datetime, timedelta

    mock_cm = _build_scope_mock_cm(str(tmp_path))
    group = MemorySubject.group_chat("qq", "100")
    now = datetime.now()
    old_confirmed_at = (now - timedelta(days=6)).isoformat()
    char_dir = os.path.join(str(tmp_path), "Neko")
    os.makedirs(char_dir, exist_ok=True)
    reflections = [
        {
            "id": "ref_legacy", "text": "legacy", "entity": "master",
            "status": "confirmed", "created_at": old_confirmed_at,
            "confirmed_at": old_confirmed_at, "source_fact_ids": ["f1"],
        },
        {
            "id": "ref_scoped", "text": "scoped", "entity": "group_chat",
            "status": "confirmed", "created_at": old_confirmed_at,
            "confirmed_at": old_confirmed_at, "source_fact_ids": ["g1"],
            **group.as_entry_fields(),
        },
        {
            # Corrupt partial descriptor: quarantined from every lifecycle
            # pass, so the migration must not touch its clock either.
            "id": "ref_corrupt", "text": "corrupt", "entity": "group_chat",
            "status": "confirmed", "created_at": old_confirmed_at,
            "confirmed_at": old_confirmed_at, "source_fact_ids": ["g2"],
            "subject_kind": "group_chat", "subject_id": None, "scope": None,
        },
    ]
    with open(
        os.path.join(char_dir, "reflections.json"), "w", encoding="utf-8",
    ) as f:
        json.dump(reflections, f, ensure_ascii=False)

    with patch("memory.reflection.manager.get_config_manager", return_value=mock_cm), \
         patch("memory.facts.get_config_manager", return_value=mock_cm):
        from memory.persona import PersonaManager
        from memory.reflection import ReflectionEngine

        fs = FactStore()
        fs._config_manager = mock_cm
        pm = PersonaManager()
        pm._config_manager = mock_cm
        engine = ReflectionEngine(fs, pm)
        engine._config_manager = mock_cm

        count = await engine.areset_confirmed_at_to_now("Neko")
        by_id = {
            r.get("id"): r for r in await engine._aload_reflections_full("Neko")
        }

    assert count == 1
    assert by_id["ref_legacy"]["confirmed_at"] != old_confirmed_at
    assert by_id["ref_scoped"]["confirmed_at"] == old_confirmed_at
    assert by_id["ref_corrupt"]["confirmed_at"] == old_confirmed_at


@pytest.mark.asyncio
async def test_fts_dedup_window_not_crowded_by_scoped_rows():
    """The legacy semantic-dedup 3-candidate window counts per subject: when
    a busy group's scoped rows fill the raw top-3, a legacy near-duplicate
    must still be deduplicated by the legacy hit sitting in 4th place."""
    index = _FakeTimeIndexed()
    harness = _PersistHarness(index)
    group = MemorySubject.group_chat("qq", "100")

    for offset in range(3):
        await harness._apersist_new_facts(
            "Neko", [_fact(f"群里聊周五开黑 {offset}")],
            subject=group, semantic_dedup=False,
        )
    legacy_first = await harness._apersist_new_facts(
        "Neko", [_fact("主人周五晚上八点想开黑")], semantic_dedup=False,
    )
    scoped_ids = [fact["id"] for fact in harness._mem[:3]]
    index.hits = [(fid, -10.0) for fid in scoped_ids] + [
        (legacy_first[0]["id"], -10.0),
    ]

    duplicate = await harness._apersist_new_facts(
        "Neko", [_fact("主人周五晚八点要开黑")], semantic_dedup=True,
    )
    assert duplicate == []


@pytest.mark.asyncio
async def test_fts_dedup_sees_archived_rows(tmp_path):
    """Archived facts stay in the FTS index but leave the active map: the
    subject check must resolve them from the archive, or an identical scoped
    fact repeated after archival re-enters the store (and legacy dedup
    regresses vs main, which never needed the lookup)."""
    import json as _json

    index = _FakeTimeIndexed()
    harness = _PersistHarness(index)
    group = MemorySubject.group_chat("qq", "100")
    archived = [{
        "id": "arch1", "text": "群规是不剧透", **group.as_entry_fields(),
    }]
    arch_path = tmp_path / "facts_archive.json"
    arch_path.write_text(
        _json.dumps(archived, ensure_ascii=False), encoding="utf-8",
    )
    index.hits = [("arch1", -10.0)]

    with patch.object(
        harness, "_facts_archive_path", return_value=str(arch_path),
    ):
        duplicate = await harness._apersist_new_facts(
            "Neko",
            [{"text": "群规是不能剧透", "importance": 7, "entity": "group_chat"}],
            subject=group, semantic_dedup=True,
        )
    assert duplicate == []


@pytest.mark.asyncio
async def test_fts_dedup_escalates_past_crowded_first_window():
    """Subject fan-out can fill the entire first FTS window (10 rows) with
    cross-subject hits; the dedup must escalate the window once so a legacy
    near-duplicate ranked 11th is still examined and caught."""
    index = _FakeTimeIndexed()
    harness = _PersistHarness(index)
    group = MemorySubject.group_chat("qq", "100")

    for offset in range(10):
        await harness._apersist_new_facts(
            "Neko", [_fact(f"群里聊周五开黑 {offset}")],
            subject=group, semantic_dedup=False,
        )
    legacy_first = await harness._apersist_new_facts(
        "Neko", [_fact("主人周五晚上八点想开黑")], semantic_dedup=False,
    )
    scoped_ids = [fact["id"] for fact in harness._mem[:10]]
    index.hits = [(fid, -10.0) for fid in scoped_ids] + [
        (legacy_first[0]["id"], -10.0),
    ]

    duplicate = await harness._apersist_new_facts(
        "Neko", [_fact("主人周五晚八点要开黑")], semantic_dedup=True,
    )
    assert duplicate == []


@pytest.mark.asyncio
async def test_scoped_history_route_fails_closed_on_extraction_failure():
    """A swallowed extraction failure lets the plugin advance its digest
    cursor and drop member buckets over a batch that was never extracted;
    the route must surface it as an HTTP error, while a genuine empty
    extraction stays a 200 no-facts success that may checkpoint."""
    import json as _json

    from fastapi import HTTPException

    from app.memory_server import routes as memory_routes
    from app.memory_server.routes import ScopedHistoryRequest
    from memory.facts import FactExtractionFailed

    history = _json.dumps([
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
    ])
    subject = {"subject_kind": "group_chat", "subject_id": "qq:100"}

    failing_store = MagicMock()
    failing_store.extract_facts = AsyncMock(
        side_effect=FactExtractionFailed("retries exhausted"),
    )
    with patch.object(memory_routes.runtime, "fact_store", failing_store):
        with pytest.raises(HTTPException) as excinfo:
            await memory_routes.process_scoped_history(
                "Neko",
                ScopedHistoryRequest(input_history=history, subject=subject),
            )
        assert excinfo.value.status_code == 502

    empty_store = MagicMock()
    empty_store.extract_facts = AsyncMock(return_value=[])
    with patch.object(memory_routes.runtime, "fact_store", empty_store):
        result = await memory_routes.process_scoped_history(
            "Neko",
            ScopedHistoryRequest(input_history=history, subject=subject),
        )
    assert result["status"] == "processed"
    assert result["created"] == 0
    assert empty_store.extract_facts.await_args.kwargs["fail_closed"] is True


@pytest.mark.asyncio
async def test_scoped_history_route_passes_speaker_label():
    """Member batches carry the speaker identity through to extraction; an
    oversized label is rejected instead of silently truncated."""
    import json as _json

    from fastapi import HTTPException

    from app.memory_server import routes as memory_routes
    from app.memory_server.routes import ScopedHistoryRequest

    history = _json.dumps([
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
    ])
    subject = {
        "subject_kind": "group_participant", "subject_id": "qq:100:12345",
    }

    store = MagicMock()
    store.extract_facts = AsyncMock(return_value=[])
    with patch.object(memory_routes.runtime, "fact_store", store):
        await memory_routes.process_scoped_history(
            "Neko",
            ScopedHistoryRequest(
                input_history=history, subject=subject,
                speaker_label="  Alice(12345)  ",
            ),
        )
    assert store.extract_facts.await_args.kwargs["speaker_label"] == "Alice(12345)"

    with patch.object(memory_routes.runtime, "fact_store", store):
        with pytest.raises(HTTPException) as excinfo:
            await memory_routes.process_scoped_history(
                "Neko",
                ScopedHistoryRequest(
                    input_history=history, subject=subject,
                    speaker_label="x" * 65,
                ),
            )
        assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_extraction_prompt_uses_speaker_label(tmp_path):
    """With speaker_label the extraction prompt frames the human speaker as
    that member instead of the configured private-chat master, so member
    statements cannot be extracted as facts about the master."""
    from types import SimpleNamespace

    mock_cm = _build_scope_mock_cm(str(tmp_path))
    fs = FactStore()
    fs._config_manager = mock_cm

    captured = {}

    async def _capture(prompt, lanlan_name, **kwargs):
        captured["prompt"] = prompt
        return []

    fs._allm_call_with_retries = _capture
    msg = SimpleNamespace(type="human", content="我对花生过敏")

    with patch("memory.facts.get_global_language", return_value="zh"):
        await fs._allm_extract_facts("Neko", [msg])
        assert "主人 | 我对花生过敏" in captured["prompt"]

        await fs._allm_extract_facts(
            "Neko", [msg], speaker_label="Alice(12345)",
        )
    assert "Alice(12345) | 我对花生过敏" in captured["prompt"]
    assert "主人 | 我对花生过敏" not in captured["prompt"]
    assert "{MASTER_NAME}" not in captured["prompt"]


@pytest.mark.asyncio
async def test_extract_facts_fail_closed_raises_on_terminal_failure(tmp_path):
    """fail_closed callers (the scoped-history route) need failure and
    genuine-empty to be distinguishable; the default swallow stays for
    legacy best-effort callers whose history is durably stored."""
    from types import SimpleNamespace

    from memory.facts import FactExtractionFailed

    mock_cm = _build_scope_mock_cm(str(tmp_path))
    fs = FactStore()
    fs._config_manager = mock_cm
    msg = SimpleNamespace(type="human", content="hi")

    async def _terminal_failure(prompt, lanlan_name, **kwargs):
        return None

    fs._allm_call_with_retries = _terminal_failure
    with patch("memory.facts.get_global_language", return_value="zh"):
        with pytest.raises(FactExtractionFailed):
            await fs.extract_facts([msg], "Neko", fail_closed=True)
        assert await fs.extract_facts([msg], "Neko") == []

        async def _malformed(prompt, lanlan_name, **kwargs):
            return {"facts": []}

        fs._allm_call_with_retries = _malformed
        with pytest.raises(FactExtractionFailed):
            await fs.extract_facts([msg], "Neko", fail_closed=True)

        # A NON-EMPTY array of malformed elements (e.g. bare strings) would
        # be silently skipped by persist and read as a genuine empty
        # extraction — fail_closed must reject it as retryable too.
        async def _malformed_items(prompt, lanlan_name, **kwargs):
            return ["Alice likes tea"]

        fs._allm_call_with_retries = _malformed_items
        with pytest.raises(FactExtractionFailed):
            await fs.extract_facts([msg], "Neko", fail_closed=True)
        assert await fs.extract_facts([msg], "Neko") == []

        # Mixed arrays fail the whole batch too: persist would silently
        # drop the malformed element and the advanced cursor would lose
        # whatever it carried; a retry re-extracts and dedup absorbs the
        # valid duplicates.
        async def _mixed(prompt, lanlan_name, **kwargs):
            return [{"text": "有效条目", "importance": 5}, "畸形"]

        fs._allm_call_with_retries = _mixed
        with pytest.raises(FactExtractionFailed):
            await fs.extract_facts([msg], "Neko", fail_closed=True)


@pytest.mark.asyncio
async def test_correction_batches_partition_by_isolation_domain(tmp_path):
    """One resolve batch must not mix isolation domains: scoped sections and
    the legacy persona would otherwise co-appear in a single correction
    prompt, letting cross-domain text bias irreversible keep/merge decisions
    (and a blended merge rewrite could leak wording across domains)."""
    import json as _json

    from memory.persona import PersonaManager

    pm = PersonaManager()
    pm._config_manager = _build_scope_mock_cm(str(tmp_path))
    name = "neko_corr_partition"
    corr_path = tmp_path / f"{name}_corrections.json"
    items = [
        {
            "old_text": "legacy old", "new_text": "legacy new",
            "entity": "master", "created_at": "2026-07-26T10:00:00",
        },
        {
            "old_text": "group A old", "new_text": "group A new",
            "entity": "@subject/group_chat:qq:100",
            "created_at": "2026-07-26T10:00:01",
        },
        {
            "old_text": "group B old", "new_text": "group B new",
            "entity": "@subject/group_chat:qq:200",
            "created_at": "2026-07-26T10:00:02",
        },
    ]
    corr_path.write_text(
        _json.dumps(items, ensure_ascii=False), encoding="utf-8",
    )

    captured = {}

    class _FakeLLM:
        async def ainvoke(self, prompt):
            captured["prompt"] = prompt
            resp = MagicMock()
            # Valid-but-empty decision list: nothing is consumed, the queue
            # survives, and the test only pins WHICH pairs entered the prompt.
            resp.content = "[]"
            return resp

        async def aclose(self):
            return None

    async def _fake_create(*args, **kwargs):
        return _FakeLLM()

    with patch.object(pm, "_corrections_path", return_value=str(corr_path)), \
         patch("utils.llm_client.create_chat_llm_async", _fake_create):
        resolved = await pm.resolve_corrections(name)

    assert resolved == 0
    prompt = captured["prompt"]
    assert "legacy old" in prompt
    assert "group A old" not in prompt
    assert "group B old" not in prompt
    remaining = _json.loads(corr_path.read_text(encoding="utf-8"))
    assert {item["entity"] for item in remaining} == {
        "master", "@subject/group_chat:qq:100", "@subject/group_chat:qq:200",
    }


@pytest.mark.asyncio
async def test_malformed_correction_entities_never_reach_prompt_or_master(tmp_path):
    """A correction whose entity is missing, empty, or not a string belongs
    to no isolation domain: it must not enter a resolve batch, and the apply
    phase must not default it into the master section (a scoped correction
    that lost its entity would otherwise cross into the legacy persona)."""
    import json as _json

    from memory.persona import PersonaManager

    pm = PersonaManager()
    pm._config_manager = _build_scope_mock_cm(str(tmp_path))
    name = "neko_corr_malformed"
    corr_path = tmp_path / f"{name}_corrections.json"
    items = [
        {
            "old_text": "legit old", "new_text": "legit new",
            "entity": "master", "created_at": "2026-07-26T11:00:00",
        },
        {
            "old_text": "no entity old", "new_text": "no entity new",
            "created_at": "2026-07-26T11:00:01",
        },
        {
            "old_text": "empty old", "new_text": "empty new",
            "entity": "  ", "created_at": "2026-07-26T11:00:02",
        },
        {
            "old_text": "weird old", "new_text": "weird new",
            "entity": 123, "created_at": "2026-07-26T11:00:03",
        },
    ]
    corr_path.write_text(
        _json.dumps(items, ensure_ascii=False), encoding="utf-8",
    )

    captured = {}

    class _FakeLLM:
        async def ainvoke(self, prompt):
            captured["prompt"] = prompt
            resp = MagicMock()
            resp.content = "[]"
            return resp

        async def aclose(self):
            return None

    async def _fake_create(*args, **kwargs):
        return _FakeLLM()

    with patch.object(pm, "_corrections_path", return_value=str(corr_path)), \
         patch("utils.llm_client.create_chat_llm_async", _fake_create):
        await pm.resolve_corrections(name)

    prompt = captured["prompt"]
    assert "legit old" in prompt
    assert "no entity old" not in prompt
    assert "empty old" not in prompt
    assert "weird old" not in prompt
    assert len(_json.loads(corr_path.read_text(encoding="utf-8"))) == 4

    # Apply-phase guard (defense in depth): even when a malformed item is
    # referenced by a valid LLM decision — e.g. replaying a stale batch —
    # it is skipped instead of being written into the master section.
    resolved = await pm._apply_correction_results(
        name, items, {1}, [{"index": 1, "action": "keep_both"}],
    )
    assert resolved == 0
    persona_text = _json.dumps(
        await pm.aensure_persona(name), ensure_ascii=False,
    )
    assert "no entity new" not in persona_text


@pytest.mark.asyncio
async def test_group_turns_always_refresh_session_prompt():
    """Group sessions are shared: the creation-time system prompt carries the
    first speaker's member persona. Group turns must swap in the current
    turn's freshly built prompt even when semantic recall is empty, and
    restore the original afterwards; private turns keep the old no-op."""
    from utils.llm_client import SystemMessage

    from plugin.plugins.qq_auto_reply.reply_generation_service import (
        QQReplyGenerationService,
    )

    service = QQReplyGenerationService(SimpleNamespace(logger=MagicMock()))
    original = SystemMessage(content="creator prompt with member A persona")
    session = SimpleNamespace(
        _conversation_history=[original],
        _instructions=original.content,
    )

    restore = service._apply_turn_memory_context(
        session, "current speaker prompt", "", always_refresh=True,
    )
    assert session._conversation_history[0].content == "current speaker prompt"
    restore()
    assert session._conversation_history[0] is original
    assert session._instructions == original.content

    # Private path unchanged: empty recall without the flag is a no-op.
    service._apply_turn_memory_context(session, "whatever", "")
    assert session._conversation_history[0] is original


@pytest.mark.asyncio
async def test_undelivered_buffer_drafts_stay_out_of_memory():
    """Rapid-fire merging delivers only the generated summary: the buffered
    draft replies already sit in the shared history but no participant ever
    saw them. Drafts are recorded on user_data at interception (a plugin-
    owned dict — no unwritable-message failure mode), the serializer skips
    recorded rows by identity, and the single-draft path unrecords ONLY its
    own delivered row: older merged-away drafts stay excluded forever."""
    from plugin.plugins.qq_auto_reply.pipeline_models import QQMessageBlock
    from plugin.plugins.qq_auto_reply.reply_buffer_service import (
        PendingReply,
        QQReplyBufferService,
    )
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    def _msg(msg_type, text):
        return SimpleNamespace(type=msg_type, content=text)

    history = [_msg("human", "问题一"), _msg("ai", "草稿一")]
    user_data = {
        "is_group": True,
        "session": SimpleNamespace(_conversation_history=history),
    }
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _emit_log=lambda *a, **k: None,
        reply_delivery_node=SimpleNamespace(deliver=AsyncMock()),
    )
    service = QQReplyBufferService.__new__(QQReplyBufferService)
    service.plugin = plugin
    service._pending = {}

    await service.schedule_reply(
        session_key="group:7788", reply_text="草稿一", raw_text="草稿一",
        blocks=[QQMessageBlock(text="草稿一")], wait_seconds=999,
        sender_id="1", is_group=True, group_id="7788",
    )
    assert user_data["undelivered_draft_rows"] == [history[1]]

    # Second draft buffered before the wait expires: recorded as well.
    history.append(_msg("human", "问题二"))
    history.append(_msg("ai", "草稿二"))
    await service.schedule_reply(
        session_key="group:7788", reply_text="草稿二", raw_text="草稿二",
        blocks=[QQMessageBlock(text="草稿二")], wait_seconds=999,
        sender_id="1", is_group=True, group_id="7788",
    )
    assert user_data["undelivered_draft_rows"] == [history[1], history[3]]
    merged_away = service._pending.pop("group:7788")
    if merged_away.task:
        merged_away.task.cancel()

    # The memory serializer skips recorded ai rows for digest and /cache
    # alike — by identity, so an identical-text delivered row still passes.
    history.append(_msg("ai", "已投递的总结"))
    memory_service = QQSessionMemoryService.__new__(QQSessionMemoryService)
    memory_service.plugin = plugin
    texts = [
        m["content"][0]["text"]
        for m in memory_service.conversation_slice_to_memory_messages(
            history, 0, user_data=user_data,
        )
    ]
    assert texts == ["问题一", "问题二", "已投递的总结"]

    # Single-draft path: the new draft IS delivered — unrecord it, and ONLY
    # it. The two merged-away drafts from the earlier burst must stay
    # excluded, or "replies that never happened" re-enter digest/cache.
    history.append(_msg("human", "问题三"))
    history.append(_msg("ai", "草稿三"))
    single = PendingReply(
        first_text="草稿三", wait_seconds=0, sender_id="1",
        is_group=True, group_id="7788",
    )
    single.first_blocks = [QQMessageBlock(text="草稿三")]
    single.wait_until = 0.0
    sentinel_ctx = object()
    single.mention_context = sentinel_ctx
    plugin.reply_generation_service = SimpleNamespace(
        record_scoped_mentions_on_delivery=AsyncMock(),
    )
    service._pending["group:7788"] = single
    service._mark_latest_draft_undelivered("group:7788", single)
    assert history[6] in user_data["undelivered_draft_rows"]
    await service._deliver_after_wait("group:7788", single)
    plugin.reply_delivery_node.deliver.assert_awaited_once()
    assert user_data["undelivered_draft_rows"] == [history[1], history[3]]
    # Mention counters bind to actual delivery: the single-draft path
    # records them now, with the turn's own context.
    plugin.reply_generation_service.record_scoped_mentions_on_delivery.assert_awaited_once_with(
        sentinel_ctx, "草稿三",
    )


@pytest.mark.asyncio
async def test_flush_prompt_excluded_but_delivered_ack_kept():
    """Two edges of the exclusion list around synthetic flush turns:
    (a) the mid-flight ack reply in the 10-16 branch IS delivered — a
    re-scan of history after the pipeline run would wrongly exclude it
    from memory forever; the draft binding must reuse the row captured
    before the run. (b) the synthetic system-instruction prompt row
    carries copies of undelivered drafts and must be excluded."""
    from plugin.plugins.qq_auto_reply.pipeline_models import QQMessageBlock
    from plugin.plugins.qq_auto_reply.reply_buffer_service import (
        PendingReply,
        QQReplyBufferService,
    )
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    def _msg(msg_type, text):
        return SimpleNamespace(type=msg_type, content=text)

    draft_new = _msg("ai", "第十条的草稿")
    history = [_msg("human", "u1"), draft_new]
    user_data = {
        "is_group": True,
        "session": SimpleNamespace(_conversation_history=history),
    }

    sys_prompt_row = _msg("human", "[系统] 对方连续发了多条消息……")
    ack_row = _msg("ai", "嗯嗯，听着呢")

    async def _run_ack(request):
        history.append(sys_prompt_row)
        history.append(ack_row)
        return SimpleNamespace(action="reply", reply_text="嗯嗯，听着呢")

    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _emit_log=lambda *a, **k: None,
        reply_pipeline=SimpleNamespace(run=AsyncMock(side_effect=_run_ack)),
        reply_delivery_node=SimpleNamespace(deliver=AsyncMock()),
    )
    memory_service = QQSessionMemoryService.__new__(QQSessionMemoryService)
    memory_service.plugin = plugin
    plugin.session_memory_service = memory_service
    service = QQReplyBufferService.__new__(QQReplyBufferService)
    service.plugin = plugin
    service._pending = {}

    waiting = PendingReply(
        first_text="旧草稿", wait_seconds=999, sender_id="1",
        is_group=True, group_id="7788",
    )
    waiting.message_count = 9
    waiting.buffered_texts = [f"旧{i}" for i in range(9)]
    waiting.task = asyncio.create_task(asyncio.sleep(999))
    service._pending["group:7788"] = waiting

    await service.schedule_reply(
        session_key="group:7788", reply_text="第十条的草稿",
        raw_text="第十条的草稿", blocks=[QQMessageBlock(text="x")],
        wait_seconds=999, sender_id="1", is_group=True, group_id="7788",
    )
    if waiting.task:
        waiting.task.cancel()
    service._pending.pop("group:7788", None)

    rows = user_data["undelivered_draft_rows"]
    assert draft_new in rows
    assert sys_prompt_row in rows
    assert not any(row is ack_row for row in rows)
    # The pending is bound to the pre-run draft only.
    assert waiting.draft_rows == [draft_new]

    # Serializer: the synthetic prompt (human) and the draft (ai) are both
    # excluded; the delivered ack row survives.
    texts = [
        m["content"][0]["text"]
        for m in memory_service.conversation_slice_to_memory_messages(
            history, 0, user_data=user_data,
        )
    ]
    assert texts == ["u1", "嗯嗯，听着呢"]


@pytest.mark.asyncio
async def test_production_model_node_runs_fallback_memory_hooks():
    """The production pipeline goes through QQReplyModelNode.generate(),
    not the legacy generate_from_context(): its successful-fallback path
    must run the same scoped memory hooks (member bucket / mention
    counters) or fallback turns silently skip memory."""
    from plugin.plugins.qq_auto_reply.pipeline_models import QQModelResult
    from plugin.plugins.qq_auto_reply.reply_model_node import QQReplyModelNode

    hooks = AsyncMock()
    generation = SimpleNamespace(
        run_primary_session_call=AsyncMock(
            return_value=QQModelResult(
                reply_text=None, source="none", allow_fallback=True,
            ),
        ),
        generate_fallback_from_context=AsyncMock(return_value="备用回复"),
        run_fallback_memory_hooks=hooks,
    )
    plugin = SimpleNamespace(
        reply_generation_service=generation,
        qq_client=SimpleNamespace(needs_attention=False),
    )
    node = QQReplyModelNode(plugin)
    context = SimpleNamespace(
        is_group=True, permission_level="normal", ephemeral_session=False,
    )
    result = await node.generate(context)
    assert result.reply_text == "备用回复"
    assert result.used_fallback is True
    hooks.assert_awaited_once_with(context, "备用回复")


@pytest.mark.asyncio
async def test_fallback_buffered_reply_does_not_mark_previous_row():
    """A direct-LLM fallback reply appends NO ai row to the shared history:
    scheduling it with history_backed=False must not record the most recent
    (already delivered) ai reply as an undelivered draft."""
    from plugin.plugins.qq_auto_reply.pipeline_models import QQMessageBlock
    from plugin.plugins.qq_auto_reply.reply_buffer_service import (
        QQReplyBufferService,
    )
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    delivered_old = SimpleNamespace(type="ai", content="上一条已投递回复")
    history = [SimpleNamespace(type="human", content="u1"), delivered_old]
    user_data = {"session": SimpleNamespace(_conversation_history=history)}
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _emit_log=lambda *a, **k: None,
        reply_delivery_node=SimpleNamespace(deliver=AsyncMock()),
    )
    memory_service = QQSessionMemoryService.__new__(QQSessionMemoryService)
    memory_service.plugin = plugin
    plugin.session_memory_service = memory_service
    service = QQReplyBufferService.__new__(QQReplyBufferService)
    service.plugin = plugin
    service._pending = {}

    await service.schedule_reply(
        session_key="group:7788", reply_text="fallback 回复",
        raw_text="fallback 回复", blocks=[QQMessageBlock(text="x")],
        wait_seconds=999, sender_id="1", is_group=True, group_id="7788",
        history_backed=False,
    )
    pending = service._pending.pop("group:7788")
    if pending.task:
        pending.task.cancel()
    assert not user_data.get("undelivered_draft_rows")
    assert pending.draft_rows == []


@pytest.mark.asyncio
async def test_used_fallback_survives_every_postprocess_path():
    """used_fallback must reach the outcome on EVERY finalize branch — the
    default/forced reply after an empty fallback also has no ai row for
    this turn in the shared history; losing the flag would let the buffer
    mark the previous delivered reply as an undelivered draft."""
    from plugin.plugins.qq_auto_reply.pipeline_models import QQModelResult
    from plugin.plugins.qq_auto_reply.reply_postprocess_node import (
        QQReplyPostprocessNode,
    )

    plugin = SimpleNamespace(
        _sanitize_generated_reply=lambda t: t,
        _strategy_mode="neko_dynamic",
        _emit_log=lambda *a, **k: None,
        i18n=SimpleNamespace(t=lambda *a, **k: "嗯嗯~"),
    )
    node = QQReplyPostprocessNode.__new__(QQReplyPostprocessNode)
    node.plugin = plugin
    empty_fallback = QQModelResult(
        reply_text=None, source="none", used_fallback=True,
    )

    # default/forced branch
    forced = SimpleNamespace(
        ephemeral_session=False, force_reply=True, permission_level="normal",
    )
    outcome = await node.finalize(forced, empty_fallback)
    assert outcome.used_default_message is True
    assert outcome.used_fallback is True

    # llm_skip branch
    skip = SimpleNamespace(
        ephemeral_session=False, force_reply=False, permission_level="normal",
    )
    outcome = await node.finalize(skip, empty_fallback)
    assert outcome.reply_text is None
    assert outcome.used_fallback is True

    # ephemeral-empty branch
    eph = SimpleNamespace(
        ephemeral_session=True, force_reply=False, permission_level="normal",
    )
    outcome = await node.finalize(eph, empty_fallback)
    assert outcome.used_fallback is True


@pytest.mark.asyncio
async def test_proactive_prompt_row_excluded_from_digest():
    """The silence-timer proactive turn appends a synthetic system-
    instruction human row to the shared history; like rapid-fire control
    prompts it must be recorded for exclusion so digests never persist it
    as a participant utterance. The delivered proactive reply row stays."""
    from plugin.plugins.qq_auto_reply.attention_gate_service import (
        QQAttentionGateService,
    )
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    def _msg(msg_type, text):
        return SimpleNamespace(type=msg_type, content=text)

    history = [_msg("human", "真实发言")]
    user_data = {
        "is_group": True,
        "session": SimpleNamespace(_conversation_history=history),
    }
    prompt_row = _msg("human", "[synthetic proactive instruction]")
    reply_row = _msg("ai", "主动说的话")

    async def _run(request):
        history.append(prompt_row)
        history.append(reply_row)
        return SimpleNamespace(action="reply", reply_text="主动说的话")

    async def _lock(session_key, fn):
        return await fn()

    plugin = SimpleNamespace(
        _user_sessions={"group:g9": user_data},
        _admin_qq="1",
        reply_pipeline=SimpleNamespace(run=AsyncMock(side_effect=_run)),
        _run_with_session_lock=_lock,
        runtime_service=SimpleNamespace(record_pipeline_outcome=lambda **k: None),
    )
    memory_service = QQSessionMemoryService.__new__(QQSessionMemoryService)
    memory_service.plugin = plugin
    plugin.session_memory_service = memory_service
    gate = QQAttentionGateService.__new__(QQAttentionGateService)
    gate.plugin = plugin
    gate._logger = MagicMock()

    await gate._trigger_proactive_speech("g9")
    rows = user_data["undelivered_draft_rows"]
    assert any(r is prompt_row for r in rows)
    assert not any(r is reply_row for r in rows)


@pytest.mark.asyncio
async def test_retro_replay_honors_receipt_time_policy():
    """Retroactive review replays a backlog message through the shared
    session: consent belongs to when it was SAID. A message received while
    group memory was OFF (or a legacy row without the field) must have its
    replayed human row excluded from scoped history; a message received
    under ON replays normally."""
    from plugin.plugins.qq_auto_reply.attention_gate_service import (
        QQAttentionGateService,
    )
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    def _msg(msg_type, text):
        return SimpleNamespace(type=msg_type, content=text)

    history = []
    user_data = {
        "is_group": True,
        "session": SimpleNamespace(_conversation_history=history),
    }

    async def _run(request):
        history.append(_msg("human", request.message_text))
        history.append(_msg("ai", "补回的回复"))
        return SimpleNamespace(action="reply", reply_text="补回的回复")

    async def _lock(session_key, fn):
        return await fn()

    plugin = SimpleNamespace(
        _user_sessions={"group:g7": user_data},
        reply_pipeline=SimpleNamespace(run=AsyncMock(side_effect=_run)),
        _run_with_session_lock=_lock,
        runtime_service=SimpleNamespace(record_pipeline_outcome=lambda **k: None),
    )
    memory_service = QQSessionMemoryService.__new__(QQSessionMemoryService)
    memory_service.plugin = plugin
    plugin.session_memory_service = memory_service
    gate = QQAttentionGateService.__new__(QQAttentionGateService)
    gate.plugin = plugin
    gate._logger = MagicMock()

    # Legacy row without the field: fails closed, row excluded.
    assert await gate._reply_to_ignored_message(
        "g7", {"message_text": "OFF 时代的话", "sender_id": "1",
               "sender_nickname": "Bob"},
    ) is True
    rows = user_data["undelivered_draft_rows"]
    assert any(getattr(r, "type", "") == "human" for r in rows)
    excluded_before = len(rows)

    # Received under ON: replays normally, nothing new excluded.
    assert await gate._reply_to_ignored_message(
        "g7", {"message_text": "ON 时代的话", "sender_id": "1",
               "sender_nickname": "Bob",
               "group_memory_enabled_at_receipt": True},
    ) is True
    assert len(user_data["undelivered_draft_rows"]) == excluded_before


@pytest.mark.asyncio
async def test_run_delivery_direct_branch_records_mentions_on_success():
    """The direct-delivery branch (no buffer service / skip_buffer) must
    record scoped mentions after a confirmed delivery — the wiring itself,
    not just the underlying recorder, needs a pin."""
    from plugin.plugins.qq_auto_reply.pipeline_models import (
        QQDeliveryPlan,
        QQDeliveryResult,
        QQMessageBlock,
        QQReplyOutcome,
    )
    from plugin.plugins.qq_auto_reply.reply_pipeline import (
        QQReplyPipelineRunner,
    )

    plugin = SimpleNamespace(
        reply_buffer_service=None,
        reply_delivery_node=SimpleNamespace(
            deliver=AsyncMock(return_value=QQDeliveryResult(
                delivered=True, target_type="group", target_id="7788",
                reply_text="回复",
            )),
        ),
        reply_generation_service=SimpleNamespace(
            record_scoped_mentions_on_delivery=AsyncMock(),
        ),
    )
    runner = QQReplyPipelineRunner(plugin)
    context = SimpleNamespace(is_group=True, group_id="7788")
    outcome = QQReplyOutcome(action="reply", reply_text="回复")
    plan = QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(text="回复")],
    )
    await runner._run_delivery(plan, None, outcome, context=context)
    plugin.reply_generation_service.record_scoped_mentions_on_delivery.assert_awaited_once_with(
        context, "回复",
    )

    # Failed delivery records nothing.
    plugin.reply_generation_service.record_scoped_mentions_on_delivery.reset_mock()
    plugin.reply_delivery_node.deliver = AsyncMock(return_value=QQDeliveryResult(
        delivered=False, target_type="group", target_id="7788", reply_text=None,
    ))
    await runner._run_delivery(plan, None, outcome, context=context)
    plugin.reply_generation_service.record_scoped_mentions_on_delivery.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_result_reflects_open_platform_send_failure():
    """The Open Platform client returns None on a swallowed send failure:
    deliver() must report delivered=False so the buffer keeps the draft
    excluded and records no mentions. NapCat sends are fire-and-forget
    (None by design) and keep reporting delivered=True; failures there
    surface as exceptions."""
    from plugin.plugins.qq_auto_reply.pipeline_models import (
        QQDeliveryPlan,
        QQMessageBlock,
    )
    from plugin.plugins.qq_auto_reply.reply_delivery_node import (
        QQReplyDeliveryNode,
    )

    def _node(needs_attention, send_result):
        plugin = SimpleNamespace(
            _get_reply_mode=lambda: "text",
            qq_client=SimpleNamespace(
                needs_attention=needs_attention,
                send_group_message=AsyncMock(return_value=send_result),
            ),
        )
        node = QQReplyDeliveryNode.__new__(QQReplyDeliveryNode)
        node.plugin = plugin
        return node

    plan = QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(text="回复")],
    )
    # Open Platform failure -> not delivered.
    result = await _node(False, None).deliver(plan)
    assert result.delivered is False
    # Open Platform success -> delivered.
    result = await _node(False, "msgid").deliver(plan)
    assert result.delivered is True
    # NapCat fire-and-forget None -> still delivered.
    result = await _node(True, None).deliver(plan)
    assert result.delivered is True

    # Multi-block partial failure: ALL attempted text blocks must confirm —
    # the exclusion list is whole-row, so a half-sent reply must not clear
    # its mark and enter extraction.
    node = _node(False, None)
    node.plugin.qq_client.send_group_message = AsyncMock(
        side_effect=["msgid", None],
    )
    with patch("asyncio.sleep", new=AsyncMock()):
        result = await node.deliver(QQDeliveryPlan(
            target_type="group", target_id="7788",
            blocks=[QQMessageBlock(text="第一块"), QQMessageBlock(text="第二块")],
        ))
    assert result.delivered is False


@pytest.mark.asyncio
async def test_buffer_keeps_draft_excluded_when_delivery_unconfirmed():
    """A failed single-draft send must keep the undelivered record and
    record no mentions — an unsent reply must never reach extraction."""
    from plugin.plugins.qq_auto_reply.pipeline_models import (
        QQDeliveryResult,
        QQMessageBlock,
    )
    from plugin.plugins.qq_auto_reply.reply_buffer_service import (
        PendingReply,
        QQReplyBufferService,
    )

    draft = SimpleNamespace(type="ai", content="草稿")
    history = [SimpleNamespace(type="human", content="u1"), draft]
    user_data = {
        "session": SimpleNamespace(_conversation_history=history),
        "undelivered_draft_rows": [draft],
    }
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _emit_log=lambda *a, **k: None,
        reply_delivery_node=SimpleNamespace(
            deliver=AsyncMock(return_value=QQDeliveryResult(
                delivered=False, target_type="group", target_id="7788",
                reply_text=None,
            )),
        ),
        reply_generation_service=SimpleNamespace(
            record_scoped_mentions_on_delivery=AsyncMock(),
        ),
    )
    service = QQReplyBufferService.__new__(QQReplyBufferService)
    service.plugin = plugin
    service._pending = {}
    single = PendingReply(
        first_text="草稿", wait_seconds=0, sender_id="1",
        is_group=True, group_id="7788",
    )
    single.first_blocks = [QQMessageBlock(text="草稿")]
    single.wait_until = 0.0
    single.draft_rows = [draft]
    single.mention_context = object()
    service._pending["group:7788"] = single

    await service._deliver_after_wait("group:7788", single)
    assert user_data["undelivered_draft_rows"] == [draft]
    plugin.reply_generation_service.record_scoped_mentions_on_delivery.assert_not_awaited()
    assert "group:7788" not in service._pending


@pytest.mark.asyncio
async def test_private_flush_prompt_not_excluded_and_cache_lags_tail_draft():
    """Two private-path edges of the exclusion machinery:
    (a) pre_buffer means the 2nd+ real private messages exist ONLY inside
    the flush prompt row — excluding it would erase them from /cache and
    /process; the synthetic-prompt recorder must skip private sessions.
    (b) /cache runs at generation time, before the buffer marks the new
    draft: the tail ai run is deferred to the next cache/finalize, when
    the exclusion list has settled."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    def _msg(msg_type, text):
        return SimpleNamespace(type=msg_type, content=text)

    history = [_msg("human", "第一条"), _msg("ai", "本轮草稿")]
    user_data = {
        "is_group": False,
        "her_name": "Neko",
        "session": SimpleNamespace(_conversation_history=history),
        "last_synced_index": 0,
    }
    plugin = SimpleNamespace(
        _user_sessions={"private:1": user_data},
        memory_bridge=SimpleNamespace(
            post_memory_history=AsyncMock(return_value={"status": "ok"}),
        ),
        logger=MagicMock(),
    )
    service = QQSessionMemoryService(plugin)

    # (a) private synthetic-prompt recording is a no-op.
    service.record_synthetic_prompt_rows("private:1", 0)
    assert "undelivered_draft_rows" not in user_data

    # (b) the tail draft is NOT cached this turn; the user row is.
    count = await service.cache_session_delta("private:1", user_data)
    assert count == 1
    sent = plugin.memory_bridge.post_memory_history.await_args.args[2]
    assert [m["content"][0]["text"] for m in sent] == ["第一条"]
    assert user_data["last_synced_index"] == 1

    # Next turn: the previous (now-settled) reply is cached with the new
    # user row; the fresh tail draft lags again.
    history.append(_msg("human", "第二条"))
    history.append(_msg("ai", "新草稿"))
    count = await service.cache_session_delta("private:1", user_data)
    assert count == 2
    sent = plugin.memory_bridge.post_memory_history.await_args.args[2]
    assert [m["content"][0]["text"] for m in sent] == ["本轮草稿", "第二条"]
    assert user_data["last_synced_index"] == 3


@pytest.mark.asyncio
async def test_provisional_draft_blocks_digest_cursor_until_settled():
    """A history-backed draft is provisional during the buffer wait: the
    focus digest must stop its cursor BEFORE the draft row — advancing
    past it and then delivering (which clears the exclusion mark) would
    leave the delivered reply permanently outside scoped memory. Once the
    outcome settles (merged away), the barrier lifts and the exclusion
    list alone governs."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    def _msg(msg_type, text):
        return SimpleNamespace(type=msg_type, content=text)

    draft = _msg("ai", "在途草稿")
    tail = _msg("human", "后续消息")
    history = [_msg("human", "u1"), draft, tail]
    user_data = {
        "is_group": True,
        "undelivered_draft_rows": [draft],
        "provisional_draft_rows": [draft],
    }
    service = QQSessionMemoryService.__new__(QQSessionMemoryService)
    service.plugin = SimpleNamespace()

    messages, next_index = service._slice_group_history_batch(
        history, 0, 10, user_data=user_data, stop_at_provisional=True,
    )
    assert [m["content"][0]["text"] for m in messages] == ["u1"]
    assert next_index == 1  # cursor parked before the provisional row

    # Outcome settled (merged away): barrier lifts, exclusion list still
    # filters the dead draft, and the cursor may advance past it.
    user_data["provisional_draft_rows"] = []
    messages, next_index = service._slice_group_history_batch(
        history, next_index, 10, user_data=user_data, stop_at_provisional=True,
    )
    assert [m["content"][0]["text"] for m in messages] == ["后续消息"]
    assert next_index == 3

    # finalize/teardown path pierces the barrier (list-filtering only).
    user_data["provisional_draft_rows"] = [draft]
    messages, next_index = service._slice_group_history_batch(
        history, 0, 10, user_data=user_data,
    )
    assert [m["content"][0]["text"] for m in messages] == ["u1", "后续消息"]
    assert next_index == 3


@pytest.mark.asyncio
async def test_delete_group_prompt_survives_missing_runtime_service():
    """The discarded-check must sit INSIDE the session_runtime_service
    guard: with the service absent, `discarded` is never assigned and a
    same-level check raises NameError (the set_group_prompt twin already
    nests it correctly)."""
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    fake = SimpleNamespace(
        _qq_settings={"group_prompts": {"7788": "旧提示词"}},
        _persist_business_config=AsyncMock(return_value=True),
        session_runtime_service=None,
        _emit_log=lambda *a, **k: None,
    )
    result = await QQAutoReplyPlugin.delete_group_prompt(
        fake, group_id="7788",
    )
    assert fake._qq_settings["group_prompts"] == {}
    assert result is not None


def test_receipt_snapshot_stamped_at_task_creation():
    """process_messages must stamp the policy snapshot on the message dict
    BEFORE creating the handler task, and handle_message must forward it —
    the handler can queue on the global semaphore for seconds, so the top
    of handle_group_message is not the real receipt boundary."""
    import inspect

    from plugin.plugins.qq_auto_reply import message_dispatcher

    process_src = inspect.getsource(
        message_dispatcher.QQMessageDispatcher.process_messages
    )
    stamp_pos = process_src.find("_group_memory_at_receipt")
    task_pos = process_src.find("create_task")
    assert stamp_pos != -1 and task_pos != -1
    assert stamp_pos < task_pos
    handle_src = inspect.getsource(
        message_dispatcher.QQMessageDispatcher.handle_message
    )
    assert "_group_memory_at_receipt" in handle_src


def test_dispatcher_group_policy_snapshot_taken_before_first_await():
    """handle_group_message must read the group-memory policy before its
    first await (gate evaluate / interjection checks): a mid-processing
    OFF->ON flip must not grant persistence to an utterance received
    under OFF. Mirrors the backlog row's receipt-time field."""
    import ast
    import inspect

    from plugin.plugins.qq_auto_reply import message_dispatcher

    source = inspect.getsource(
        message_dispatcher.QQMessageDispatcher.handle_group_message
    )
    tree = ast.parse("class _W:\n" + "\n".join(
        "    " + line for line in source.splitlines()
    ))
    func = tree.body[0].body[0]
    snapshot_line = None
    first_await_line = None
    policy_reads = 0
    for node in ast.walk(func):
        if (
            snapshot_line is None
            and isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "group_memory_at_receipt"
                for t in node.targets
            )
        ):
            snapshot_line = node.lineno
        if isinstance(node, ast.Await):
            if first_await_line is None or node.lineno < first_await_line:
                first_await_line = node.lineno
        if isinstance(node, ast.Constant) and node.value == "group_memory_enabled":
            policy_reads += 1
    assert snapshot_line is not None
    assert first_await_line is not None
    assert snapshot_line < first_await_line
    # Exactly one read of the live setting — a second (late) read after the
    # awaits would reintroduce the race the snapshot exists to close.
    assert policy_reads == 1


def test_member_consent_snapshot_taken_before_first_await():
    """The consent snapshot must be assigned before build()'s first await:
    the login/bootstrap/recall calls can suspend for seconds, and an
    OFF->ON flip during them must not retroactively authorize collection
    for a turn whose utterance happened under OFF."""
    import ast
    import inspect

    from plugin.plugins.qq_auto_reply import reply_context_node

    source = inspect.getsource(reply_context_node.QQReplyContextNode.build)
    tree = ast.parse("class _W:\n" + "\n".join(
        "    " + line for line in source.splitlines()
    ))
    func = tree.body[0].body[0]
    snapshot_line = None
    first_await_line = None
    for node in ast.walk(func):
        if (
            snapshot_line is None
            and isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "member_memory_snapshot"
                for t in node.targets
            )
        ):
            snapshot_line = node.lineno
        if isinstance(node, ast.Await):
            if first_await_line is None or node.lineno < first_await_line:
                first_await_line = node.lineno
    assert snapshot_line is not None
    assert first_await_line is not None
    assert snapshot_line < first_await_line


@pytest.mark.asyncio
async def test_correction_dead_letter_redacts_scoped_text(tmp_path):
    """Dead-lettered corrections carrying subject fields hold participant-
    derived persona content: the WARN must log only domain identifiers and
    lengths, never the text itself. Legacy items keep the truncated preview
    (owner content in owner logs)."""
    import json as _json

    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.persona import PersonaManager

    subject = MemorySubject.create("group_chat", "qq:123", scope="tenant-a")
    pm = PersonaManager()
    pm._config_manager = _build_scope_mock_cm(str(tmp_path))
    name = "neko_dead_letter"
    corr_path = tmp_path / f"{name}_corrections.json"
    items = [
        {
            "old_text": "成员的私密旧观点", "new_text": "成员的私密新观点",
            "entity": subject.persona_section_key,
            "created_at": "2026-07-27T00:00:01",
            "resolve_attempts": MEMORY_LIVENESS_MAX_ATTEMPTS - 1,
            **subject.as_entry_fields(),
        },
        {
            "old_text": "主人的旧观点", "new_text": "主人的新观点",
            "entity": "master",
            "created_at": "2026-07-27T00:00:02",
            "resolve_attempts": MEMORY_LIVENESS_MAX_ATTEMPTS - 1,
        },
    ]
    corr_path.write_text(
        _json.dumps(items, ensure_ascii=False), encoding="utf-8",
    )
    with patch.object(pm, "_corrections_path", return_value=str(corr_path)), \
         patch("memory.persona.corrections.logger") as mock_logger:
        await pm._abump_correction_attempts_and_dead_letter(name, items)
    warn_text = " ".join(
        str(c.args[0]) for c in mock_logger.warning.call_args_list
    )
    assert "成员的私密旧观点" not in warn_text
    assert "成员的私密新观点" not in warn_text
    assert "qq:123" in warn_text
    assert "主人的旧观点" in warn_text
    remaining = _json.loads(corr_path.read_text(encoding="utf-8"))
    assert remaining == []


def test_double_off_stamp_preserves_first_epoch_cutoff():
    """OFF -> ON -> OFF while the first settlement is still queued: the
    second OFF stamp must not overwrite the unconsumed cutoff. Overwriting
    skews finalize's floor exemption (floor > cutoff resets to 0): the
    first epoch's nonconsent floor then sits below the new cutoff and
    permanently skips consented backlog from before the first opt-out."""
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    history = [SimpleNamespace(type="human", content=f"m{i}") for i in range(4)]
    ud = {
        "is_group": True,
        "session": SimpleNamespace(_conversation_history=history),
    }
    plugin = SimpleNamespace(_user_sessions={"group:1": ud})
    service = QQSettingsService.__new__(QQSettingsService)
    service.plugin = plugin

    service._stamp_group_memory_transition(enabled_after=False)
    assert ud["group_opt_out_cutoff"] == 4
    assert ud["pending_disable_settle"] is True

    history.extend(
        SimpleNamespace(type="human", content=f"m{i}") for i in range(4, 6)
    )
    service._stamp_group_memory_transition(enabled_after=True)
    assert ud["pending_enable_rebase"] == 6
    assert ud["group_opt_out_cutoff"] == 4  # queued OFF settle keeps its fence

    history.extend(
        SimpleNamespace(type="human", content=f"m{i}") for i in range(6, 8)
    )
    service._stamp_group_memory_transition(enabled_after=False)
    assert ud["group_opt_out_cutoff"] == 4  # NOT overwritten to 8
    assert ud["pending_disable_settle"] is True
    assert "pending_enable_rebase" not in ud

    # Once the first settlement consumed its markers, a later OFF stamps a
    # fresh fence at the current boundary.
    ud.pop("pending_disable_settle")
    ud.pop("group_opt_out_cutoff")
    service._stamp_group_memory_transition(enabled_after=False)
    assert ud["group_opt_out_cutoff"] == 8


@pytest.mark.asyncio
async def test_scoped_reads_recheck_live_policy_before_fetch():
    """A group request can capture use_memory_context=True and then await
    (login fetch, first memory call) while the admin opts the group out:
    both scoped read points must recheck the live setting immediately
    before fetching — persistence is already re-gated at prime time, reads
    must not inject scoped context into a reply after opt-out."""
    from plugin.plugins.qq_auto_reply.reply_context_node import (
        QQReplyContextNode,
    )
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    bridge = MagicMock()
    bridge.query_relevant_memory = AsyncMock()
    bridge.fetch_scoped_bootstrap_memory = AsyncMock()
    plugin = SimpleNamespace(
        _qq_settings={"group_memory_enabled": False},
        memory_bridge=bridge,
        logger=MagicMock(),
        _should_skip_direct_llm_fallback_for_images=lambda **kwargs: False,
    )
    node = QQReplyContextNode.__new__(QQReplyContextNode)
    node.plugin = plugin
    assert await node._build_recalled_memory_text(
        her_name="Neko", message="hello",
        should_use_memory_context=True, attachments=None,
        is_group=True, group_id="7788", sender_id="1",
    ) == ""
    bridge.query_relevant_memory.assert_not_awaited()

    instruction = QQSessionInstructionService.__new__(QQSessionInstructionService)
    instruction.plugin = plugin
    assert await instruction._build_core_memory_section(
        should_use_memory_context=True, her_name="Neko", master_name="M",
        context_ready_template="{name}/{master}", is_group=True,
        group_id="7788", sender_id="1",
    ) == ""
    bridge.fetch_scoped_bootstrap_memory.assert_not_awaited()

    # Private paths are untouched by the group recheck.
    bridge.fetch_bootstrap_memory = AsyncMock(return_value="ctx")
    assert await instruction._build_core_memory_section(
        should_use_memory_context=True, her_name="Neko", master_name="M",
        context_ready_template="{name}/{master}", is_group=False,
    ) != ""

    # Post-await recheck: the opt-out can land while the fetch itself is on
    # the wire — data already read back must be dropped, not injected.
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryQueryResult

    plugin._qq_settings["group_memory_enabled"] = True

    async def _recall_and_flip(*args, **kwargs):
        plugin._qq_settings["group_memory_enabled"] = False
        return QQMemoryQueryResult(text="群规是不剧透", hit_count=1)

    bridge.query_relevant_memory = AsyncMock(side_effect=_recall_and_flip)
    bridge.group_subject.side_effect = (
        lambda gid: {"subject_kind": "group_chat", "subject_id": f"qq:{gid}"}
    )
    bridge.group_participant_subject.side_effect = (
        lambda gid, sid: {"subject_kind": "group_participant"}
    )
    assert await node._build_recalled_memory_text(
        her_name="Neko", message="hello",
        should_use_memory_context=True, attachments=None,
        is_group=True, group_id="7788", sender_id="1",
    ) == ""
    bridge.query_relevant_memory.assert_awaited_once()

    plugin._qq_settings["group_memory_enabled"] = True

    async def _bootstrap_and_flip(*args, **kwargs):
        plugin._qq_settings["group_memory_enabled"] = False
        return "群聊长期记忆"

    bridge.fetch_scoped_bootstrap_memory = AsyncMock(
        side_effect=_bootstrap_and_flip,
    )
    assert await instruction._build_core_memory_section(
        should_use_memory_context=True, her_name="Neko", master_name="M",
        context_ready_template="{name}/{master}", is_group=True,
        group_id="7788", sender_id="1",
    ) == ""
    bridge.fetch_scoped_bootstrap_memory.assert_awaited_once()


@pytest.mark.asyncio
async def test_enable_rebase_consumes_dead_cutoff_and_keeps_cursor_monotonic():
    """The ON rebase must (a) pop a cutoff left behind by a failed OFF
    settle — otherwise every later finalize truncates history at the dead
    boundary, the overflow clamp regresses the cursor to it, and the empty
    slice 'succeeds' into pop+close, destroying unsettled new-era rows —
    and (b) never move the digest cursor backwards: a focus-shift digest
    may already have pushed post-reenable rows and advanced past the
    boundary; overwriting would settle those rows twice."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    history = [SimpleNamespace(type="human", content=f"m{i}") for i in range(8)]
    user_data = {
        "memory_enabled": False,
        "is_group": True,
        "group_id": "7788",
        "her_name": "Neko",
        "session": SimpleNamespace(_conversation_history=history, close=AsyncMock()),
        "pending_enable_rebase": 4,
        "group_opt_out_cutoff": 2,
        "last_group_digest_index": 6,
    }

    async def _run_with_session_lock(session_key, fn):
        return await fn()

    bridge = MagicMock()
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _qq_settings={},
        _run_with_session_lock=_run_with_session_lock,
        memory_bridge=bridge,
        logger=MagicMock(),
    )
    service = QQSessionMemoryService(plugin)

    await service.invalidate_group_sessions(enabled=True)
    assert "group_opt_out_cutoff" not in user_data
    assert user_data["last_group_digest_index"] == 6
    assert user_data["memory_enabled"] is True
    bridge.post_scoped_memory_history.assert_not_awaited()

    # Normal direction still rebases forward past the opt-out era.
    user_data["pending_enable_rebase"] = 4
    user_data["last_group_digest_index"] = 1
    user_data["memory_enabled"] = False
    await service.invalidate_group_sessions(enabled=True)
    assert user_data["last_group_digest_index"] == 4


@pytest.mark.asyncio
async def test_retain_settle_pops_only_the_cutoff_it_consumed():
    """The batched retain-settle can run for minutes; a second OFF stamp
    landing mid-flight overwrites the cutoff. The retain block must not
    delete that newer, unconsumed cutoff — the queued second OFF settlement
    still needs it as its opt-out fence."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    def _mk(plugin_holder, key):
        history = [
            SimpleNamespace(type="human", content=f"m{i}") for i in range(4)
        ]
        return {
            "memory_enabled": True,
            "is_group": True,
            "group_id": key,
            "her_name": "Neko",
            "session": SimpleNamespace(
                _conversation_history=history, close=AsyncMock(),
            ),
            "last_group_digest_index": 0,
            "group_opt_out_cutoff": 2,
        }

    bridge = MagicMock()
    bridge.group_subject.side_effect = (
        lambda gid: {"subject_kind": "group_chat", "subject_id": f"qq:{gid}"}
    )
    plugin = SimpleNamespace(
        _user_sessions={},
        _qq_settings={},
        memory_bridge=bridge,
        logger=MagicMock(),
    )
    service = QQSessionMemoryService(plugin)
    ud_raced = _mk(plugin, "g1")
    ud_clean = _mk(plugin, "g2")
    plugin._user_sessions["group:g1"] = ud_raced
    plugin._user_sessions["group:g2"] = ud_clean

    async def _post_and_restamp(*args, **kwargs):
        # Simulate OFF#2 stamping a fresh cutoff while the settle is on the
        # wire.
        ud_raced["group_opt_out_cutoff"] = 3
        return {"status": "ok"}

    bridge.post_scoped_memory_history = AsyncMock(side_effect=_post_and_restamp)
    assert await service.finalize_user_memory_session(
        "group:g1", reason="test", retain_session=True,
    ) is True
    assert plugin._user_sessions.get("group:g1") is ud_raced
    assert ud_raced["group_opt_out_cutoff"] == 3

    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    assert await service.finalize_user_memory_session(
        "group:g2", reason="test", retain_session=True,
    ) is True
    assert plugin._user_sessions.get("group:g2") is ud_clean
    assert "group_opt_out_cutoff" not in ud_clean


@pytest.mark.asyncio
async def test_group_memory_toggle_syncs_existing_sessions():
    """Flipping group_memory_enabled must reach already-open group sessions:
    ON->OFF fail-closes a session whose settle fails (no later flush can
    persist opted-out data), and OFF->ON advances the digest cursor so turns
    from the opted-out period are never retroactively extracted."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    history = [SimpleNamespace(type="human", content=f"msg {i}") for i in range(6)]
    session = SimpleNamespace(_conversation_history=history, close=AsyncMock())
    bridge = MagicMock()
    bridge.group_subject.side_effect = (
        lambda gid: {"subject_kind": "group_chat", "subject_id": f"qq:{gid}"}
    )
    bridge.post_scoped_memory_history = AsyncMock(
        side_effect=RuntimeError("server down"),
    )
    user_data = {
        "memory_enabled": True,
        "is_group": True,
        "group_id": "7788",
        "her_name": "Neko",
        "session": session,
        "last_group_digest_index": 0,
        "group_member_memory_messages": {"2046": [{"role": "user", "content": []}]},
        "group_member_memory_labels": {"2046": "2046"},
    }

    async def _run_with_session_lock(session_key, fn):
        return await fn()

    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _qq_settings={"group_member_memory_enabled": True},
        _run_with_session_lock=_run_with_session_lock,
        memory_bridge=bridge,
        logger=MagicMock(),
    )
    service = QQSessionMemoryService(plugin)

    # ON->OFF with a failing settle: fail closed. Transitions act only on
    # sessions marked synchronously at the policy write.
    user_data["pending_disable_settle"] = True
    await service.invalidate_group_sessions(enabled=False)
    assert user_data["memory_enabled"] is False
    assert user_data["last_group_digest_index"] == len(history)
    assert "group_member_memory_messages" not in user_data
    # A later idle/shutdown sweep must now skip this session entirely.
    await service.flush_all_memory_sessions("shutdown")
    bridge.post_scoped_memory_history.assert_awaited_once()  # only the settle try

    # OFF->ON on a session that accumulated turns while opted out.
    history.append(SimpleNamespace(type="human", content="opted-out turn"))
    user_data["last_group_digest_index"] = 0
    user_data["pending_enable_rebase"] = True
    await service.invalidate_group_sessions(enabled=True)
    assert user_data["memory_enabled"] is True
    assert user_data["last_group_digest_index"] == len(history)

    # Enable race: a request that slipped in between the settings write and
    # this task already primed memory_enabled=True — the cursor must still
    # be rebased (the policy transition is authoritative, not the cached
    # per-request flag).
    user_data["last_group_digest_index"] = 0
    user_data["memory_enabled"] = True
    user_data["pending_enable_rebase"] = True
    await service.invalidate_group_sessions(enabled=True)
    assert user_data["last_group_digest_index"] == len(history)

    # The rebase honors the boundary stamped at the policy write: turns
    # arriving after the enable stay digestible.
    user_data["last_group_digest_index"] = 0
    user_data["pending_enable_rebase"] = 1
    await service.invalidate_group_sessions(enabled=True)
    assert user_data["last_group_digest_index"] == 1
    # Corrupt negative boundary clamps to 0 and the cursor stays monotonic:
    # never negative, never regressed below its current position.
    user_data["pending_enable_rebase"] = -5
    user_data.pop("nonconsent_history_end", None)
    await service.invalidate_group_sessions(enabled=True)
    assert user_data["last_group_digest_index"] == 1

    # A non-consented turn still in flight at the enable stamp finishes
    # AFTER the boundary: its recorded end wins (privacy over完整性).
    user_data["pending_enable_rebase"] = 1
    user_data["nonconsent_history_end"] = 3
    await service.invalidate_group_sessions(enabled=True)
    assert user_data["last_group_digest_index"] == 3

    # Unmarked session (created AFTER the transition): untouched in both
    # directions — no bogus rebase, no bogus settle.
    user_data["last_group_digest_index"] = 1
    await service.invalidate_group_sessions(enabled=True)
    assert user_data["last_group_digest_index"] == 1
    await service.invalidate_group_sessions(enabled=False)
    assert "group:7788" in plugin._user_sessions
    assert user_data["last_group_digest_index"] == 1

    # ON->OFF success path: settle succeeds, session pops, and the orphaned
    # dict's flag is still cleared so stale references cannot re-flush it.
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    user_data["last_group_digest_index"] = 0
    user_data["pending_disable_settle"] = True
    await service.invalidate_group_sessions(enabled=False)
    assert user_data["memory_enabled"] is False
    assert "group:7788" not in plugin._user_sessions

    # Rapid OFF->ON: the enable stamp must NOT erase the queued disable
    # settlement — the OFF task settles to its cutoff first, then the ON
    # task rebases to the re-enable boundary.
    hist4 = [SimpleNamespace(type="human", content=f"r{i}") for i in range(4)]
    both = {
        "memory_enabled": True, "is_group": True, "group_id": "7788",
        "her_name": "Neko", "last_group_digest_index": 0,
        "session": SimpleNamespace(_conversation_history=hist4, close=AsyncMock()),
        "pending_disable_settle": True, "group_opt_out_cutoff": 2,
        "pending_enable_rebase": 4,
    }
    plugin._user_sessions["group:7788"] = both
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    await service.invalidate_group_sessions(enabled=False)
    settled = [
        m["content"][0]["text"]
        for call in bridge.post_scoped_memory_history.await_args_list
        for m in call.args[1]
    ]
    assert settled == ["r0", "r1"]
    # Queued-ON present: the OFF settlement retains the session — the
    # post-reenable turns exist only in its history and the queued ON task
    # still has to rebase it. The consumed cutoff/marker are cleared, and
    # memory stays disabled until the rebase moves the cursor past the
    # opt-out era.
    survivor = plugin._user_sessions.get("group:7788")
    assert survivor is both
    assert survivor.get("group_opt_out_cutoff") is None
    assert survivor.get("pending_disable_settle") is None
    assert survivor["memory_enabled"] is False
    await service.invalidate_group_sessions(enabled=True)
    assert survivor["memory_enabled"] is True
    assert survivor["last_group_digest_index"] == 4
    assert "pending_enable_rebase" not in survivor
    # The rebase itself settles nothing new.
    assert bridge.post_scoped_memory_history.await_count == 1

    # Disable race: a request that slipped in after the OFF policy write
    # already primed memory_enabled=False — the transition must still settle
    # the opt-in-era buffer instead of trusting the cached flag.
    history3 = [SimpleNamespace(type="human", content="consented turn")]
    raced = {
        "memory_enabled": False, "is_group": True, "group_id": "7788",
        "her_name": "Neko", "last_group_digest_index": 0,
        "session": SimpleNamespace(
            _conversation_history=history3, close=AsyncMock(),
        ),
    }
    raced["pending_disable_settle"] = True
    plugin._user_sessions["group:7788"] = raced
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    await service.invalidate_group_sessions(enabled=False)
    bridge.post_scoped_memory_history.assert_awaited_once()
    assert "group:7788" not in plugin._user_sessions


@pytest.mark.asyncio
async def test_fact_dedup_resolve_locks_batch_to_one_domain(tmp_path):
    """The dedup queue mixes isolation domains; one resolve batch must not:
    the prompt may only contain pairs from the FIFO head's domain. Legacy
    queue items without stored domain fields are classified via their live
    fact rows, and pairs whose rows are gone are dequeued without ever
    reaching a prompt."""
    import json as _json

    from memory.fact_dedup import FactDedupResolver

    group = MemorySubject.group_chat("qq", "100")
    fact_store = MagicMock()
    fact_store._config_manager = MagicMock()
    fact_store._config_manager.get_model_api_config = MagicMock(return_value={
        "model": "fake", "base_url": "http://fake", "api_key": "sk",
    })
    # Live rows used to classify OLD queue items lacking domain fields.
    fact_store.aload_facts = AsyncMock(return_value=[
        {"id": "old_cand", "text": "legacy old cand"},
        {"id": "old_exist", "text": "legacy old exist"},
    ])
    resolver = FactDedupResolver(fact_store=fact_store)
    name = "neko_dedup_domain"
    pending_path = tmp_path / "pending.json"
    seed = [
        {
            # New-schema legacy pair (head -> locks the batch to legacy).
            "candidate_id": "c1", "existing_id": "e1",
            "candidate_text": "legacy pair text", "existing_text": "legacy sib",
            "entity": "master", "subject_key": None, "scope": None,
            "cosine": 0.9, "queued_at": "2026-07-26T10:00:00",
        },
        {
            # New-schema scoped pair: different domain, must stay queued.
            "candidate_id": "c2", "existing_id": "e2",
            "candidate_text": "group pair text", "existing_text": "group sib",
            "entity": "group_chat",
            "subject_key": group.key, "scope": group.scope,
            "cosine": 0.9, "queued_at": "2026-07-26T10:00:01",
        },
        {
            # Old-schema pair (no domain fields): classified legacy via rows.
            "candidate_id": "old_cand", "existing_id": "old_exist",
            "candidate_text": "old schema text", "existing_text": "old sib",
            "entity": "master",
            "cosine": 0.9, "queued_at": "2026-07-26T10:00:02",
        },
        {
            # Old-schema pair whose rows are gone: dequeued, never prompted.
            "candidate_id": "ghost_c", "existing_id": "ghost_e",
            "candidate_text": "ghost text", "existing_text": "ghost sib",
            "entity": "master",
            "cosine": 0.9, "queued_at": "2026-07-26T10:00:03",
        },
    ]
    pending_path.write_text(
        _json.dumps(seed, ensure_ascii=False), encoding="utf-8",
    )

    captured = {}

    class _FakeLLM:
        async def ainvoke(self, prompt):
            captured["prompt"] = prompt
            resp = MagicMock()
            resp.content = "[]"
            return resp

        async def aclose(self):
            return None

    async def _fake_create(*args, **kwargs):
        return _FakeLLM()

    def _noop_assert(*args, **kw):
        return None

    with patch.object(resolver, "_pending_path", return_value=str(pending_path)), \
         patch("memory.fact_dedup.assert_cloudsave_writable", _noop_assert), \
         patch("utils.llm_client.create_chat_llm_async", _fake_create):
        await resolver._aresolve_locked(name)

    prompt = captured["prompt"]
    assert "legacy pair text" in prompt
    assert "old schema text" in prompt
    assert "group pair text" not in prompt
    assert "ghost text" not in prompt
    remaining = _json.loads(pending_path.read_text(encoding="utf-8"))
    remaining_ids = {item["candidate_id"] for item in remaining}
    assert "c2" in remaining_ids
    assert "ghost_c" not in remaining_ids


@pytest.mark.asyncio
async def test_scoped_synthesis_prompt_never_names_private_master(tmp_path):
    """The reflection template frames its facts as being about {MASTER_NAME}.
    Scoped synthesis must substitute the subject descriptor: injecting the
    private master's name would both leak it into a scoped prompt and steer
    the model into rewriting member facts as insights about the master."""
    import json
    import os

    mock_cm = _build_scope_mock_cm(str(tmp_path))
    group = MemorySubject.group_chat("qq", "100")
    char_dir = os.path.join(str(tmp_path), "Neko")
    os.makedirs(char_dir, exist_ok=True)
    facts = [
        {
            "id": f"g{index}", "text": f"群事实 {index}",
            "entity": "group_chat", "importance": 5, "absorbed": False,
            **group.as_entry_fields(),
        }
        for index in range(6)
    ]
    with open(os.path.join(char_dir, "facts.json"), "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False)

    with patch("memory.reflection.manager.get_config_manager", return_value=mock_cm), \
         patch("memory.facts.get_config_manager", return_value=mock_cm):
        from memory.persona import PersonaManager
        from memory.reflection import ReflectionEngine

        fs = FactStore()
        fs._config_manager = mock_cm
        pm = PersonaManager()
        pm._config_manager = mock_cm
        engine = ReflectionEngine(fs, pm)
        engine._config_manager = mock_cm

        captured = {}

        async def _fake_ainvoke(self, prompt):
            captured["prompt"] = prompt
            resp = MagicMock()
            resp.content = (
                '{"reflection": "这个群固定周五晚上开黑", "entity": "group_chat"}'
            )
            return resp

        async def _fake_aclose(self):
            return None

        class _FakeLLM:
            def __init__(self, *a, **kw):
                pass
            ainvoke = _fake_ainvoke
            aclose = _fake_aclose

        with patch("utils.llm_client.create_chat_llm", _FakeLLM), \
             patch(
                 "config.prompts.prompts_memory.get_reflection_prompt",
                 lambda lang: "{FACTS}|{LANLAN_NAME}|{MASTER_NAME}",
             ), \
             patch("utils.language_utils.get_global_language", return_value="zh"):
            created = await engine.synthesize_reflections("Neko", subject=group)

    assert len(created) == 1
    assert "主人" not in captured["prompt"]
    assert group.key in captured["prompt"]


@pytest.mark.asyncio
async def test_scoped_mentions_route_records_with_subject_boundary():
    """The scoped mention endpoint bumps both recorders with the caller's
    subjects and never touches legacy-private entries; an empty subject list
    fails closed."""
    from fastapi import HTTPException

    from app.memory_server import routes as memory_routes
    from app.memory_server.routes import ScopedMentionsRequest

    subject = {"subject_kind": "group_chat", "subject_id": "qq:100"}
    pm = MagicMock()
    pm.arecord_mentions = AsyncMock()
    engine = MagicMock()
    engine.arecord_mentions = AsyncMock()
    with patch.object(memory_routes.runtime, "persona_manager", pm), \
         patch.object(memory_routes.runtime, "reflection_engine", engine):
        result = await memory_routes.record_scoped_mentions(
            "Neko",
            ScopedMentionsRequest(response_text="回复文本", subjects=[subject]),
        )
        assert result["status"] == "recorded"
        for recorder in (pm.arecord_mentions, engine.arecord_mentions):
            kwargs = recorder.await_args.kwargs
            assert kwargs["include_legacy_private"] is False
            assert len(kwargs["subjects"]) == 1

        with pytest.raises(HTTPException) as excinfo:
            await memory_routes.record_scoped_mentions(
                "Neko",
                ScopedMentionsRequest(response_text="回复文本", subjects=[]),
            )
        assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_group_reply_success_records_scoped_mentions_best_effort():
    """Scoped mention counters are bumped at DELIVERY time with the same
    subjects the reply was authorized to see — the generation-time hook
    must not bump them (buffered drafts can be merged away unseen); a
    recording failure never breaks the reply path."""
    from plugin.plugins.qq_auto_reply.reply_generation_service import (
        QQReplyGenerationService,
    )

    bridge = MagicMock()
    bridge.group_subject.side_effect = (
        lambda gid: {"subject_kind": "group_chat", "subject_id": f"qq:{gid}"}
    )
    bridge.group_participant_subject.side_effect = (
        lambda gid, uid: {
            "subject_kind": "group_participant", "subject_id": f"qq:{gid}:{uid}",
        }
    )
    bridge.post_scoped_mentions = AsyncMock()
    plugin = SimpleNamespace(
        memory_bridge=bridge,
        logger=MagicMock(),
        session_memory_service=SimpleNamespace(
            record_group_member_turn=MagicMock(),
        ),
        session_runtime_service=SimpleNamespace(
            build_generation_session_key=lambda context: "group:7788",
        ),
        _user_sessions={"group:7788": {"memory_enabled": True}},
        _cache_session_delta=AsyncMock(return_value=0),
    )
    service = QQReplyGenerationService(plugin)
    context = SimpleNamespace(
        is_group=True, group_id="7788", sender_id="2046", her_name="Neko",
        permission_level="user", ephemeral_session=False,
    )

    # Generation-time hook no longer bumps mentions: a buffered draft can
    # be merged away without anyone seeing it.
    await service._sync_memory_after_success(
        session_key="group:7788",
        user_data={"memory_enabled": True},
        context=context,
        reply_text="她记得群规是不剧透",
    )
    bridge.post_scoped_mentions.assert_not_awaited()

    await service.record_scoped_mentions_on_delivery(
        context, "她记得群规是不剧透",
    )
    kwargs = bridge.post_scoped_mentions.await_args.kwargs
    assert [s["subject_id"] for s in kwargs["subjects"]] == [
        "qq:7788", "qq:7788:2046",
    ]

    # Synthetic turns record only the group subject: the nominal sender is
    # not the real speaker.
    bridge.post_scoped_mentions.reset_mock()
    context_syn = SimpleNamespace(
        is_group=True, group_id="7788", sender_id="2046", her_name="Neko",
        permission_level="user", source_kind="rapid_fire_flush",
        ephemeral_session=False,
    )
    await service.record_scoped_mentions_on_delivery(context_syn, "合并回复")
    kwargs = bridge.post_scoped_mentions.await_args.kwargs
    assert [s2["subject_id"] for s2 in kwargs["subjects"]] == ["qq:7788"]

    # Failure is swallowed (reply already delivered).
    bridge.post_scoped_mentions = AsyncMock(side_effect=RuntimeError("down"))
    await service.record_scoped_mentions_on_delivery(context, "再次回复")


@pytest.mark.asyncio
async def test_group_digest_batches_never_skip_backlog():
    """A backlog larger than the digest window must drain oldest-first in
    multiple batches with an exact cursor — the previous newest-N slice
    permanently skipped the middle of an active group's history. A batch
    failure keeps the cursor at the last successful batch so the remainder
    is retried on the next flush."""
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    history = [SimpleNamespace(type="human", content=f"msg {i}") for i in range(8)]
    session = SimpleNamespace(_conversation_history=history, close=AsyncMock())
    bridge = MagicMock()
    bridge.group_subject.side_effect = QQMemoryBridge.group_subject
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    user_data = {
        "memory_enabled": True, "is_group": True, "group_id": "7788",
        "her_name": "Neko", "session": session,
    }
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _qq_settings={},
        memory_bridge=bridge,
        logger=MagicMock(),
    )
    service = QQSessionMemoryService(plugin)
    service.GROUP_HISTORY_MAX_MESSAGES = 3

    completed = await service.finalize_user_memory_session(
        "group:7788", reason="test",
    )

    assert completed is True
    sent = [
        [m["content"][0]["text"] for m in call.args[1]]
        for call in bridge.post_scoped_memory_history.await_args_list
    ]
    assert sent == [
        ["msg 0", "msg 1", "msg 2"],
        ["msg 3", "msg 4", "msg 5"],
        ["msg 6", "msg 7"],
    ]

    # Mid-drain failure: cursor stays at the last successful batch and the
    # session survives for the next flush to retry the remainder.
    history2 = [SimpleNamespace(type="human", content=f"m{i}") for i in range(6)]
    session2 = SimpleNamespace(_conversation_history=history2, close=AsyncMock())
    user_data2 = {
        "memory_enabled": True, "is_group": True, "group_id": "7788",
        "her_name": "Neko", "session": session2,
    }
    plugin._user_sessions["group:7788"] = user_data2
    bridge.post_scoped_memory_history = AsyncMock(side_effect=[
        {"status": "ok"}, {"status": "error", "message": "down"},
    ])
    completed = await service.finalize_user_memory_session(
        "group:7788", reason="retry",
    )
    assert completed is False
    assert user_data2["last_group_digest_index"] == 3
    assert "group:7788" in plugin._user_sessions

    # The retry after a mid-drain failure resumes from the cursor: only the
    # remaining messages are sent, the already-flushed first batch is not
    # replayed, and the session completes.
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    completed = await service.finalize_user_memory_session(
        "group:7788", reason="retry2",
    )
    assert completed is True
    retried = [
        [m["content"][0]["text"] for m in call.args[1]]
        for call in bridge.post_scoped_memory_history.await_args_list
    ]
    assert retried == [["m3", "m4", "m5"]]
    assert "group:7788" not in plugin._user_sessions

    # Batch cap: one finalize sweep sends at most 5 batches, keeps the
    # session and an exact cursor, and the next sweep resumes the rest.
    history3 = [SimpleNamespace(type="human", content=f"x{i}") for i in range(20)]
    session3 = SimpleNamespace(_conversation_history=history3, close=AsyncMock())
    user_data3 = {
        "memory_enabled": True, "is_group": True, "group_id": "7788",
        "her_name": "Neko", "session": session3,
    }
    plugin._user_sessions["group:7788"] = user_data3
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    completed = await service.finalize_user_memory_session(
        "group:7788", reason="cap",
    )
    assert completed is False
    assert bridge.post_scoped_memory_history.await_count == 5
    assert user_data3["last_group_digest_index"] == 15
    assert "group:7788" in plugin._user_sessions


@pytest.mark.asyncio
async def test_discard_session_salvages_group_buffers_first():
    """Group sessions have no per-turn /cache, and a private session whose
    /cache delta failed keeps its unsynced tail only in local history: every
    discard path (timeout, prompt change, login change) destroys the only
    copy. discard_session itself must attempt a settle first — and never for
    memory-disabled sessions."""
    from plugin.plugins.qq_auto_reply.session_runtime_service import (
        QQSessionRuntimeService,
    )

    finalize_calls = []

    async def _finalize(session_key, reason):
        finalize_calls.append(reason)
        plugin._user_sessions.pop(session_key, None)
        return True

    session = SimpleNamespace(close=AsyncMock())
    plugin = SimpleNamespace(
        _user_sessions={
            "group:7788": {
                "is_group": True, "memory_enabled": True, "session": session,
            },
        },
        session_memory_service=SimpleNamespace(
            finalize_user_memory_session=_finalize,
        ),
        logger=MagicMock(),
    )
    runtime = QQSessionRuntimeService.__new__(QQSessionRuntimeService)
    runtime.plugin = plugin

    await runtime.discard_session("group:7788", reason="generation_timeout")
    assert finalize_calls == ["discard:generation_timeout"]
    assert "group:7788" not in plugin._user_sessions

    # Private memory-enabled sessions settle too: finalize's private branch
    # posts the unsynced /cache tail (process/settle) before teardown.
    plugin._user_sessions["k"] = {
        "is_group": False, "memory_enabled": True,
        "session": SimpleNamespace(close=AsyncMock()),
    }
    await runtime.discard_session("k", reason="prompt_override_changed")
    assert finalize_calls == [
        "discard:generation_timeout", "discard:prompt_override_changed",
    ]
    assert "k" not in plugin._user_sessions

    # Memory-disabled sessions are discarded without salvage.
    plugin._user_sessions["k"] = {
        "is_group": True, "memory_enabled": False,
        "session": SimpleNamespace(close=AsyncMock()),
    }
    await runtime.discard_session("k", reason="prompt_override_changed")
    assert finalize_calls == [
        "discard:generation_timeout", "discard:prompt_override_changed",
    ]
    assert "k" not in plugin._user_sessions

    # Failed settle: the session and its buffers are KEPT — popping would
    # destroy the only copy; the next sweep/discard retries the settle.
    async def _finalize_fail(session_key, reason):
        return False

    plugin.session_memory_service = SimpleNamespace(
        finalize_user_memory_session=_finalize_fail,
    )
    kept = {"is_group": True, "memory_enabled": True, "session": session}
    plugin._user_sessions["group:9"] = kept
    await runtime.discard_session("group:9", reason="generation_timeout")
    assert plugin._user_sessions.get("group:9") is kept

    # finalize's early-exit (missing metadata) pops WITHOUT closing: the
    # discard must still close the session captured on entry — no leak.
    leak_session = SimpleNamespace(close=AsyncMock())

    async def _finalize_pop_no_close(session_key, reason):
        plugin._user_sessions.pop(session_key, None)
        return False

    plugin.session_memory_service = SimpleNamespace(
        finalize_user_memory_session=_finalize_pop_no_close,
    )
    plugin._user_sessions["group:10"] = {
        "is_group": True, "memory_enabled": True, "session": leak_session,
    }
    assert await runtime.discard_session(
        "group:10", reason="generation_timeout",
    ) is True
    leak_session.close.assert_awaited_once()

    # A queued OFF settlement (pending_disable_settle) protects the buffers
    # even when a later turn primed memory_enabled=False from the live
    # setting: the salvage path must run, and — finalize declining on the
    # False flag — keep the session for the transition task to settle.
    plugin.session_memory_service = SimpleNamespace(
        finalize_user_memory_session=_finalize_fail,
    )
    stamped = {
        "is_group": True, "memory_enabled": False,
        "pending_disable_settle": True,
        "session": SimpleNamespace(close=AsyncMock()),
    }
    plugin._user_sessions["group:11"] = stamped
    assert await runtime.discard_session(
        "group:11", reason="prompt_override_changed",
    ) is False
    assert plugin._user_sessions.get("group:11") is stamped

    # Kept sessions report False so callers (login-change bootstrap) must
    # not overwrite the key and destroy the preserved buffers.
    plugin.session_memory_service = SimpleNamespace(
        finalize_user_memory_session=_finalize_fail,
    )
    plugin._user_sessions["group:11"] = {
        "is_group": True, "memory_enabled": True, "session": session,
    }
    assert await runtime.discard_session("group:11", reason="登录身份变化") is False
    assert "group:11" in plugin._user_sessions


@pytest.mark.asyncio
async def test_login_change_bootstrap_keeps_session_when_discard_fails():
    """When the identity-change discard intentionally kept the session (settle
    failed), bootstrap must reuse it instead of overwriting the key — the
    overwrite would destroy the sole buffer copy and leak the old client."""
    from plugin.plugins.qq_auto_reply.session_bootstrap_service import (
        QQSessionBootstrapService,
    )

    existing = {"login_self_id": "old", "is_group": True, "memory_enabled": True}
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": existing},
        session_runtime_service=SimpleNamespace(
            discard_session=AsyncMock(return_value=False),
        ),
    )
    service = QQSessionBootstrapService.__new__(QQSessionBootstrapService)
    service.plugin = plugin
    context = SimpleNamespace(ephemeral_session=False, login_self_id="new")

    result = await service.ensure_generation_session(context, "group:7788")
    assert result is existing
    assert plugin._user_sessions["group:7788"] is existing
    # Sticky retry: prime overwrites login_self_id with the new value, so
    # the retry must key on the pending flag, not the id mismatch.
    assert existing["pending_identity_discard"] is True
    existing["login_self_id"] = "new"
    await service.ensure_generation_session(context, "group:7788")
    assert plugin.session_runtime_service.discard_session.await_count == 2


@pytest.mark.asyncio
async def test_memory_transitions_settle_members_before_group_invalidate():
    """Disabling both toggles at once (the UI links them) must settle member
    buckets BEFORE the group invalidation — finalize flushes buckets only
    while the member option is on, so the reverse order drops them."""
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    order = []
    plugin = SimpleNamespace(
        session_memory_service=SimpleNamespace(
            settle_member_buckets_on_disable=AsyncMock(
                side_effect=lambda: order.append("members"),
            ),
            invalidate_group_sessions=AsyncMock(
                side_effect=lambda **kw: order.append("group"),
            ),
        ),
    )
    service = QQSettingsService.__new__(QQSettingsService)
    service.plugin = plugin

    await service._sync_memory_transitions(
        settle_members=True, group_transition=True, group_enabled_after=False,
    )
    assert order == ["members", "group"]


@pytest.mark.asyncio
async def test_focus_shift_digest_batches_never_skip_backlog():
    """The focus-shift digest shares finalize's batching fix: a backlog
    beyond the window drains oldest-first with an exact cursor instead of
    pushing the newest slice and jumping the cursor past skipped messages
    (which finalize could then never recover)."""
    from plugin.plugins.qq_auto_reply.attention_gate_service import (
        QQAttentionGateService,
    )
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    history = [SimpleNamespace(type="human", content=f"msg {i}") for i in range(8)]
    session = SimpleNamespace(_conversation_history=history)
    bridge = MagicMock()
    bridge.group_subject.side_effect = QQMemoryBridge.group_subject
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    user_data = {"session": session, "her_name": "Neko"}

    async def _run_with_session_lock(session_key, fn):
        return await fn()

    plugin = SimpleNamespace(
        _qq_settings={"group_memory_enabled": True},
        _user_sessions={"group:7788": user_data},
        _run_with_session_lock=_run_with_session_lock,
        memory_bridge=bridge,
        logger=MagicMock(),
    )
    plugin.session_memory_service = QQSessionMemoryService(plugin)
    plugin.session_memory_service.GROUP_HISTORY_MAX_MESSAGES = 3
    gate = QQAttentionGateService(plugin)

    await gate._push_group_digest("7788")

    sent = [
        [m["content"][0]["text"] for m in call.args[1]]
        for call in bridge.post_scoped_memory_history.await_args_list
    ]
    assert sent == [
        ["msg 0", "msg 1", "msg 2"],
        ["msg 3", "msg 4", "msg 5"],
        ["msg 6", "msg 7"],
    ]
    assert user_data["last_group_digest_index"] == len(history)

    # Mid-drain failure: cursor stays at the last successful batch so
    # finalize (or the next digest) picks up the remainder.
    user_data["last_group_digest_index"] = 0
    bridge.post_scoped_memory_history = AsyncMock(side_effect=[
        {"status": "ok"}, RuntimeError("down"),
    ])
    await gate._push_group_digest("7788")
    assert user_data["last_group_digest_index"] == 3

    # In-lock recheck: the setting can flip off while the digest task waits
    # for the session lock — nothing may be pushed after opt-out.
    user_data["last_group_digest_index"] = 0
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})

    async def _flipping_lock(session_key, fn):
        plugin._qq_settings["group_memory_enabled"] = False
        try:
            return await fn()
        finally:
            plugin._qq_settings["group_memory_enabled"] = True

    plugin._run_with_session_lock = _flipping_lock
    await gate._push_group_digest("7788")
    bridge.post_scoped_memory_history.assert_not_awaited()

    async def _plain_lock(session_key, fn):
        return await fn()

    plugin._run_with_session_lock = _plain_lock

    # Bounded drain: one push sends at most 3 batches while holding the
    # session lock; the remainder stays for the next digest/finalize.
    history.extend(
        SimpleNamespace(type="human", content=f"msg {i}") for i in range(8, 10)
    )
    user_data["last_group_digest_index"] = 0
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    await gate._push_group_digest("7788")
    assert bridge.post_scoped_memory_history.await_count == 3
    assert user_data["last_group_digest_index"] == 9

    # Enable-rebase limbo: after a retained OFF settle the cursor still sits
    # before the opt-out gap until the queued ON task rebases it — pushing
    # here would lean on the nonconsent floor alone. Nothing is sent.
    user_data["last_group_digest_index"] = 0
    user_data["pending_enable_rebase"] = 5
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    await gate._push_group_digest("7788")
    bridge.post_scoped_memory_history.assert_not_awaited()
    assert user_data["last_group_digest_index"] == 0
    user_data.pop("pending_enable_rebase", None)

    # Stale capture: a finalizer/discard can settle and pop the session
    # while the digest waits for the lock — the closure must re-read the
    # registry and abort instead of re-sending finalized history through a
    # detached dict.
    replacement = {"session": session, "her_name": "Neko"}

    async def _swapping_lock(session_key, fn):
        plugin._user_sessions[session_key] = replacement
        try:
            return await fn()
        finally:
            plugin._user_sessions[session_key] = user_data

    plugin._run_with_session_lock = _swapping_lock
    user_data["last_group_digest_index"] = 0
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    await gate._push_group_digest("7788")
    bridge.post_scoped_memory_history.assert_not_awaited()
    assert user_data["last_group_digest_index"] == 0
    plugin._run_with_session_lock = _plain_lock


@pytest.mark.asyncio
async def test_digest_cursor_rebases_after_history_reset():
    """The repetition guard can replace _conversation_history with just the
    system message; a stale cursor beyond the new length must be clamped so
    turns appended after the reset are still digested instead of being
    treated as already settled forever."""
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    # Post-reset history: system message + two fresh human turns.
    history = [
        SimpleNamespace(type="system", content="sys"),
        SimpleNamespace(type="human", content="fresh 0"),
        SimpleNamespace(type="human", content="fresh 1"),
    ]
    session = SimpleNamespace(_conversation_history=history, close=AsyncMock())
    bridge = MagicMock()
    bridge.group_subject.side_effect = QQMemoryBridge.group_subject
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    user_data = {
        "memory_enabled": True, "is_group": True, "group_id": "7788",
        "her_name": "Neko", "session": session,
        "last_group_digest_index": 250,
    }
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _qq_settings={},
        memory_bridge=bridge,
        logger=MagicMock(),
    )
    service = QQSessionMemoryService(plugin)

    # Pre-fix, cursor 250 > len(3) made the slice empty and finalize
    # "completed" while silently skipping the fresh turns.
    # Wait: the finalize-time clamp snaps to len(history), so the fresh
    # turns present at clamp time would still be skipped — the per-turn
    # prime clamp is what rebases early. Simulate it first.
    from plugin.plugins.qq_auto_reply.session_runtime_service import (
        QQSessionRuntimeService,
    )
    runtime_plugin = SimpleNamespace()
    runtime = QQSessionRuntimeService.__new__(QQSessionRuntimeService)
    runtime.plugin = runtime_plugin
    stale = {
        "session": session, "reply_chunks": [],
        "last_group_digest_index": 250,
    }
    context = SimpleNamespace(
        sender_id="1", permission_level="user", is_group=True,
        group_id="7788", user_title="u", user_nickname="",
        persist_memory=True, memory_context_used=False,
        ephemeral_session=False, login_status="", login_self_id="",
        login_nickname="",
    )
    runtime.prime_generation_session_state(
        stale, session_key="group:7788", context=context,
    )
    assert stale["last_group_digest_index"] == len(history)

    # The cached flag is gated by the LIVE policy: a request that resolved
    # persist=True before an OFF write cannot mint an opted-in session
    # after the transition stamped existing ones.
    runtime_plugin._qq_settings = {"group_memory_enabled": False}
    runtime.prime_generation_session_state(
        stale, session_key="group:7788", context=context,
    )
    assert stale["memory_enabled"] is False
    runtime_plugin._qq_settings = {"group_memory_enabled": True}
    runtime.prime_generation_session_state(
        stale, session_key="group:7788", context=context,
    )
    assert stale["memory_enabled"] is True

    # Finalize-time clamp (defensive): oversized cursor never blocks the
    # rest of finalization and is persisted back at len(history).
    completed = await service.finalize_user_memory_session(
        "group:7788", reason="test",
    )
    assert completed is True
    assert bridge.post_scoped_memory_history.await_count == 0


@pytest.mark.asyncio
async def test_correction_domains_and_apply_respect_custom_scope(tmp_path):
    """Same kind/id under two custom scopes shares one persona_section_key:
    the resolve batch must treat each (key, scope) as its own domain, and
    the apply phase must only match/remove/stamp entries belonging to the
    correction item's own subject."""
    import json as _json

    from memory.persona import PersonaManager

    subject_a = MemorySubject.create("group_chat", "qq:123", scope="tenant-a")
    subject_b = MemorySubject.create("group_chat", "qq:123", scope="tenant-b")
    section_key = subject_a.persona_section_key

    pm = PersonaManager()
    pm._config_manager = _build_scope_mock_cm(str(tmp_path))
    name = "neko_corr_scope"
    corr_path = tmp_path / f"{name}_corrections.json"
    items = [
        {
            "old_text": "旧观点", "new_text": "A 的新观点",
            "entity": section_key, "created_at": "2026-07-26T12:00:00",
            **subject_a.as_entry_fields(),
        },
        {
            "old_text": "旧观点", "new_text": "B 的新观点",
            "entity": section_key, "created_at": "2026-07-26T12:00:01",
            **subject_b.as_entry_fields(),
        },
    ]
    corr_path.write_text(
        _json.dumps(items, ensure_ascii=False), encoding="utf-8",
    )

    captured = {}

    class _FakeLLM:
        async def ainvoke(self, prompt):
            captured["prompt"] = prompt
            resp = MagicMock()
            resp.content = "[]"
            return resp

        async def aclose(self):
            return None

    async def _fake_create(*args, **kwargs):
        return _FakeLLM()

    with patch.object(pm, "_corrections_path", return_value=str(corr_path)), \
         patch("utils.llm_client.create_chat_llm_async", _fake_create):
        await pm.resolve_corrections(name)

    # Same entity, different scopes: one batch may only contain scope A.
    assert captured["prompt"].count("旧观点") == 1
    assert "A 的新观点" in captured["prompt"]
    assert "B 的新观点" not in captured["prompt"]

    # Apply phase: keep_new for scope A removes only A's entry with that
    # text; B's identical-text entry survives, and the new entry carries
    # A's subject stamp (not the section's last-writer metadata).
    persona = await pm.aensure_persona(name)
    persona[section_key] = {
        **subject_b.as_entry_fields(),
        "facts": [
            {"text": "旧观点", **subject_a.as_entry_fields()},
            {"text": "旧观点", **subject_b.as_entry_fields()},
        ],
    }
    await pm.asave_persona(name, persona)
    resolved = await pm._apply_correction_results(
        name, items, {0}, [{"index": 0, "action": "keep_new"}],
    )
    assert resolved == 1
    persona = await pm.aensure_persona(name)
    facts = persona[section_key]["facts"]
    survivors = [
        (f["text"], f.get("scope")) for f in facts if isinstance(f, dict)
    ]
    assert ("旧观点", "tenant-b") in survivors
    assert ("旧观点", "tenant-a") not in survivors
    assert ("A 的新观点", "tenant-a") in survivors
    # Correction-created entries carry a real, domain-salted id — empty ids
    # are skipped by every ID-indexed operation and collide with each other.
    new_ids = [
        f.get("id") for f in facts
        if isinstance(f, dict) and f.get("text") == "A 的新观点"
    ]
    assert new_ids and all(new_ids)

    # Same text corrected under scope B must yield a *different* hash
    # segment: the id salt is subject.key|scope, so identical text across
    # scopes cannot collide. Compare the hash suffix, not the whole id —
    # the second-resolution timestamp segment could differ on its own.
    items_b = [
        {
            "old_text": "旧观点", "new_text": "A 的新观点",
            "entity": section_key, "created_at": "2026-07-26T12:00:02",
            **subject_b.as_entry_fields(),
        },
    ]
    resolved = await pm._apply_correction_results(
        name, items_b, {0}, [{"index": 0, "action": "keep_new"}],
    )
    assert resolved == 1
    persona = await pm.aensure_persona(name)
    facts = persona[section_key]["facts"]
    ids_by_scope = {
        f.get("scope"): f["id"]
        for f in facts
        if isinstance(f, dict) and f.get("text") == "A 的新观点"
    }
    assert set(ids_by_scope) == {"tenant-a", "tenant-b"}
    assert all(ids_by_scope.values())
    hash_a = ids_by_scope["tenant-a"].rsplit("_", 1)[-1]
    hash_b = ids_by_scope["tenant-b"].rsplit("_", 1)[-1]
    assert hash_a != hash_b


@pytest.mark.asyncio
async def test_member_toggle_off_settles_buckets_before_clearing():
    """Turning group_member_memory_enabled off (group memory still on) must
    settle already-collected member buckets before clearing them — finalize
    substitutes an empty mapping while the option is off, so without the
    transition hook the collected turns would be silently discarded."""
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    bridge = MagicMock()
    bridge.group_participant_subject.side_effect = (
        QQMemoryBridge.group_participant_subject
    )
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    user_data = {
        "is_group": True, "group_id": "7788", "her_name": "Neko",
        "memory_enabled": True,
        # Settings detaches OFF-era buckets into the pending snapshot
        # synchronously; the settle task consumes only the snapshot.
        "pending_settle_buckets": {
            "2046": [{"role": "user", "content": [{"type": "text", "text": "A"}]}],
        },
        "pending_settle_labels": {"2046": "Alice(2046)"},
        "pending_member_settle": True,
        # A freshly re-enabled turn writes into a NEW live bucket that the
        # late settle must not touch.
        "group_member_memory_messages": {
            "9999": [{"role": "user", "content": [{"type": "text", "text": "新授权"}]}],
        },
        "group_member_memory_labels": {"9999": "9999"},
    }

    async def _run_with_session_lock(session_key, fn):
        return await fn()

    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _qq_settings={},
        _run_with_session_lock=_run_with_session_lock,
        memory_bridge=bridge,
        logger=MagicMock(),
    )
    service = QQSessionMemoryService(plugin)

    await service.settle_member_buckets_on_disable()

    kwargs = bridge.post_scoped_memory_history.await_args.kwargs
    assert kwargs["speaker_label"] == "Alice(2046)"
    assert "pending_settle_buckets" not in user_data
    # The re-enabled live bucket survives the late settle untouched.
    assert "9999" in user_data["group_member_memory_messages"]

    # Failure path: the snapshot is still cleared (fail-closed after
    # opt-out) while live buckets remain.
    user_data["pending_settle_buckets"] = {
        "2046": [{"role": "user", "content": [{"type": "text", "text": "B"}]}],
    }
    bridge.post_scoped_memory_history = AsyncMock(side_effect=RuntimeError("down"))
    await service.settle_member_buckets_on_disable()
    assert "pending_settle_buckets" not in user_data
    assert "9999" in user_data["group_member_memory_messages"]

    # A concurrent finalizer that wins the lock before the settle task must
    # still flush marked buckets even though the global flag is already off.
    session2 = SimpleNamespace(
        _conversation_history=[], close=AsyncMock(),
    )
    marked = {
        "is_group": True, "group_id": "7788", "her_name": "Neko",
        "memory_enabled": True, "session": session2,
        "pending_member_settle": True,
        "pending_settle_buckets": {
            "2046": [{"role": "user", "content": [{"type": "text", "text": "C"}]}],
        },
        "pending_settle_labels": {"2046": "2046"},
    }
    plugin._user_sessions["group:7788"] = marked
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    completed = await service.finalize_user_memory_session(
        "group:7788", reason="idle_timeout",
    )
    assert completed is True
    kwargs = bridge.post_scoped_memory_history.await_args.kwargs
    assert kwargs["speaker_label"] == "2046"


def test_static_layer_falls_back_when_required_placeholders_missing():
    """A bundled or user override that drops the required placeholders has
    lost the template's identity-boundary constraints (e.g. the weak
    shared_session override let group members be treated as the master):
    resolution must fall back to the hardened default."""
    from plugin.plugins.qq_auto_reply.scene_prompt_templates import (
        SCENE_SHARED_GROUP,
    )
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    weak = "## 场景：群聊共享上下文\n请自然地参考正在进行的讨论。"
    i18n = SimpleNamespace(t=lambda key, default="": weak)
    plugin = SimpleNamespace(i18n=i18n, _qq_settings={}, logger=MagicMock())
    service = QQSessionInstructionService(plugin)

    rendered = service._resolve_static_layer(
        "prompts.group.shared_session", SCENE_SHARED_GROUP, "zh-CN",
        her_name="Neko", master_name="老张", group_id="7788",
    )
    assert "身份边界" in rendered
    assert "Neko" in rendered and "老张" in rendered and "7788" in rendered

    # A user override that keeps the placeholders is honored.
    plugin._qq_settings = {
        "prompt_overrides": {
            "zh-CN": {
                "prompts.group.shared_session": (
                    "自定义 {her_name}/{master_name}/{group_id} 模板"
                ),
            },
        },
    }
    rendered = service._resolve_static_layer(
        "prompts.group.shared_session", SCENE_SHARED_GROUP, "zh-CN",
        her_name="Neko", master_name="老张", group_id="7788",
    )
    assert rendered == "自定义 Neko/老张/7788 模板"


@pytest.mark.asyncio
async def test_prompt_change_discard_actually_runs():
    """_discard_all_sessions_for_prompt_change used to call the async
    discard_session without awaiting it — the coroutine was dropped and no
    session was ever discarded. It must now schedule real tasks."""
    import asyncio as _asyncio

    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    discard = AsyncMock()

    async def _run_with_session_lock(session_key, fn):
        return await fn()

    plugin = SimpleNamespace(
        _user_sessions={"group:1": {}, "private:2": {}},
        session_runtime_service=SimpleNamespace(discard_session=discard),
        i18n=SimpleNamespace(t=lambda key, default="": default),
        _qq_settings={},
        logger=MagicMock(),
        _emit_log=MagicMock(),
        _run_with_session_lock=_run_with_session_lock,
    )
    service = QQSessionInstructionService(plugin)
    service._discard_all_sessions_for_prompt_change()
    await _asyncio.sleep(0)
    await _asyncio.sleep(0)
    assert discard.await_count == 2


@pytest.mark.asyncio
async def test_finalize_honors_opt_out_cutoff():
    """Turns appended after the OFF policy write (race window while other
    groups settle) must never be extracted: finalize settles only up to the
    cutoff stamped synchronously at the policy change."""
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    history = [SimpleNamespace(type="human", content=f"msg {i}") for i in range(6)]
    session = SimpleNamespace(_conversation_history=history, close=AsyncMock())
    bridge = MagicMock()
    bridge.group_subject.side_effect = QQMemoryBridge.group_subject
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    user_data = {
        "memory_enabled": True, "is_group": True, "group_id": "7788",
        "her_name": "Neko", "session": session, "group_opt_out_cutoff": 3,
    }
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _qq_settings={},
        memory_bridge=bridge,
        logger=MagicMock(),
    )
    service = QQSessionMemoryService(plugin)

    completed = await service.finalize_user_memory_session(
        "group:7788", reason="group_memory_disabled",
    )
    assert completed is True
    sent = [
        m["content"][0]["text"]
        for call in bridge.post_scoped_memory_history.await_args_list
        for m in call.args[1]
    ]
    assert sent == ["msg 0", "msg 1", "msg 2"]

    # A failed finalize must NOT consume the cutoff: the retry stays bounded
    # by the consent-time history length.
    history2 = [SimpleNamespace(type="human", content=f"n{i}") for i in range(4)]
    session2 = SimpleNamespace(_conversation_history=history2, close=AsyncMock())
    user_data2 = {
        "memory_enabled": True, "is_group": True, "group_id": "7788",
        "her_name": "Neko", "session": session2, "group_opt_out_cutoff": 2,
    }
    plugin._user_sessions["group:7788"] = user_data2
    bridge.post_scoped_memory_history = AsyncMock(
        side_effect=RuntimeError("down"),
    )
    completed = await service.finalize_user_memory_session(
        "group:7788", reason="group_memory_disabled",
    )
    assert completed is False
    assert user_data2["group_opt_out_cutoff"] == 2
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    completed = await service.finalize_user_memory_session(
        "group:7788", reason="retry",
    )
    assert completed is True
    sent2 = [
        m["content"][0]["text"]
        for call in bridge.post_scoped_memory_history.await_args_list
        for m in call.args[1]
    ]
    assert sent2 == ["n0", "n1"]


def test_scoped_entry_ids_unique_per_domain():
    """Identical text promoted into two custom scopes of one shared section
    within the same second must not collide on entry ID — ID-addressed
    archive/delete would otherwise hit both scopes."""
    harness = _PersonaHarness()
    subject_a = MemorySubject.create("group_chat", "qq:1", scope="t-a")
    subject_b = MemorySubject.create("group_chat", "qq:1", scope="t-b")
    harness.add_fact("Neko", "同一段文本", subject=subject_a)
    harness.add_fact("Neko", "同一段文本", subject=subject_b)
    section = harness.persona[subject_a.persona_section_key]
    ids = [f["id"] for f in section["facts"]]
    assert len(ids) == 2
    assert len(set(ids)) == 2


def test_persona_view_authorizes_scoped_entries_per_entry():
    """persona_section_key omits the scope, so two subjects with the same
    kind/id but different custom scopes share one section whose metadata is
    last-writer-wins. Authorization must therefore be per entry: requesting
    scope B must never render entries stamped with scope A, and unstamped
    entries in a scoped section fail closed."""
    from memory.persona.rendering import RenderingMixin

    subject_a = MemorySubject.create("group_chat", "qq:123", scope="tenant-a")
    subject_b = MemorySubject.create("group_chat", "qq:123", scope="tenant-b")
    assert subject_a.persona_section_key == subject_b.persona_section_key

    section = {
        # Metadata is whatever the LAST writer stamped — here scope B.
        **subject_b.as_entry_fields(),
        "facts": [
            {"text": "secret of tenant A", **subject_a.as_entry_fields()},
            {"text": "note of tenant B", **subject_b.as_entry_fields()},
            {"text": "unstamped stray"},
        ],
    }
    persona = {subject_a.persona_section_key: section}

    view_b = RenderingMixin._persona_view_for_subjects(persona, [subject_b])
    facts_b = view_b[subject_a.persona_section_key]["facts"]
    assert [f["text"] for f in facts_b] == ["note of tenant B"]

    # The symmetric flip-flop: scope A must still see its own entries even
    # though the section metadata currently says scope B.
    view_a = RenderingMixin._persona_view_for_subjects(persona, [subject_a])
    facts_a = view_a[subject_a.persona_section_key]["facts"]
    assert [f["text"] for f in facts_a] == ["secret of tenant A"]

    # Mutating a returned entry must reach the underlying persona object
    # (mention recording depends on shared entry identity).
    facts_b[0]["recent_mentions"] = ["now"]
    assert section["facts"][1]["recent_mentions"] == ["now"]
