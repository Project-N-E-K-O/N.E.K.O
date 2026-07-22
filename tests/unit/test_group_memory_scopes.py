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
        return list(self.hits)

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
async def test_stage2_partitions_mixed_subjects_before_signal_detection():
    harness = _PersistHarness()
    group_a = MemorySubject.group_chat("qq", "100")
    group_b = MemorySubject.group_chat("qq", "200")
    harness._mem = [
        {
            "id": "a",
            "text": "A 群事实",
            "importance": 7,
            "created_at": "2026-07-22T00:00:00",
            "source": "user_observation",
            "signal_processed": False,
            **group_a.as_entry_fields(),
        },
        {
            "id": "b",
            "text": "B 群事实",
            "importance": 7,
            "created_at": "2026-07-22T00:00:01",
            "source": "user_observation",
            "signal_processed": False,
            **group_b.as_entry_fields(),
        },
    ]
    harness._allm_extract_facts = AsyncMock(return_value=[])
    harness._aload_signal_targets = AsyncMock(
        return_value=[{"id": "reflection.target"}],
    )
    harness._allm_detect_signals = AsyncMock(return_value=[])

    _persisted, signals, batch_ids = (
        await harness.aextract_facts_and_detect_signals("Neko", [])
    )

    assert signals == []
    assert batch_ids == ["a", "b"]
    target_batches = [
        call.kwargs["new_facts"]
        for call in harness._aload_signal_targets.await_args_list
    ]
    signal_batches = [
        call.args[1]
        for call in harness._allm_detect_signals.await_args_list
    ]
    assert [[effective_scope(fact) for fact in batch] for batch in target_batches] == [
        [group_a.scope],
        [group_b.scope],
    ]
    assert [[effective_scope(fact) for fact in batch] for batch in signal_batches] == [
        [group_a.scope],
        [group_b.scope],
    ]


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
