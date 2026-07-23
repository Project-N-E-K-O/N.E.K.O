"""Group-chat memory subject/scope isolation and legacy compatibility."""

from __future__ import annotations

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
            **group_a.as_entry_fields(), "facts": [{"text": "group a"}],
        },
        group_b.persona_section_key: {
            **group_b.as_entry_fields(), "facts": [{"text": "group b"}],
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
    plugin = SimpleNamespace(memory_bridge=bridge, logger=MagicMock())
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
    service.record_group_member_turn(
        user_data,
        SimpleNamespace(is_group=True, sender_id="2046", message="我喜欢三文鱼"),
    )
    service.record_group_member_turn(
        user_data,
        SimpleNamespace(is_group=True, sender_id="4096", message="我周五有空"),
    )

    assert list(user_data["group_member_memory_messages"]) == ["2046", "4096"]
    assert user_data["group_member_memory_messages"]["2046"][0]["content"][0]["text"] == "我喜欢三文鱼"


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
