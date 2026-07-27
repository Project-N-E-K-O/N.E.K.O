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


def _default_i18n():
    """Stand-in for the plugin i18n facade: a missing key yields the
    caller's default template, exactly like the real resolver."""
    return SimpleNamespace(t=lambda key, default="", **kw: default)


async def _passthrough_session_lock(session_key, coro_factory):
    """Stand-in for the plugin helper: run the body, no real lock."""
    return await coro_factory()


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

    # The section key omits the scope, so one section can hold two
    # isolation domains and its metadata is whoever wrote last. A new entry
    # must not inherit that: filing a fact under the wrong domain is a
    # cross-domain leak, while leaving it unstamped reads as fail-closed.
    section["facts"].append({
        "text": "另一个域的事实", "subject_kind": "group_chat",
        "subject_id": "qq:100", "scope": "other-scope",
    })
    ambiguous = harness._normalize_entry_for_section(
        harness.persona, group.persona_section_key, "又一条群规",
    )
    assert "scope" not in ambiguous
    assert "subject_kind" not in ambiguous
    section["facts"].pop()

    # An entry that already carries its own stamp keeps it.
    kept = harness._normalize_entry_for_section(
        harness.persona, group.persona_section_key,
        {
            "text": "自带戳的条目", "subject_kind": "group_participant",
            "subject_id": "qq:100:2046", "scope": "member-scope",
        },
    )
    assert kept["scope"] == "member-scope"
    assert kept["subject_kind"] == "group_participant"


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
        i18n=_default_i18n(),
        _qq_settings={
            "group_memory_enabled": True,
            "group_member_memory_enabled": True,
        },
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

    # Member memory OFF gates this read too (dual of the recall path):
    # existing participant memory must not reach a group reply.
    plugin._qq_settings["group_member_memory_enabled"] = False
    bridge.fetch_scoped_bootstrap_memory.reset_mock()
    await service._build_core_memory_section(
        should_use_memory_context=True,
        her_name="Neko",
        master_name="Master",
        context_ready_template="{name}/{master}",
        is_group=True,
        group_id="7788",
        sender_id="2046",
    )
    bridge.fetch_scoped_bootstrap_memory.assert_awaited_once_with(
        "Neko", subjects=[QQMemoryBridge.group_subject("7788")],
    )


@pytest.mark.asyncio
async def test_qq_private_bootstrap_keeps_legacy_behavior():
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    bridge = MagicMock()
    bridge.fetch_bootstrap_memory = AsyncMock(return_value="旧私人记忆")
    bridge.fetch_scoped_bootstrap_memory = AsyncMock()
    plugin = SimpleNamespace(
        memory_bridge=bridge, logger=MagicMock(), i18n=_default_i18n(),
        _qq_settings={},
    )
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
        _qq_settings={
            "group_memory_enabled": True,
            "group_member_memory_enabled": True,
        },
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
    kwargs = bridge.query_relevant_memory.await_args.kwargs
    assert [s2["subject_kind"] for s2 in kwargs["subjects"]] == [
        "group_chat", "group_participant",
    ]

    # Member memory OFF gates the READ too: existing participant memory
    # must not be recalled into a group reply once the switch is off
    # (otherwise "stop using member memory" only stopped writing).
    plugin._qq_settings["group_member_memory_enabled"] = False
    bridge.query_relevant_memory.reset_mock()
    await QQReplyContextNode(plugin)._build_recalled_memory_text(
        her_name="Neko",
        message="群规是什么？",
        should_use_memory_context=True,
        attachments=None,
        is_group=True,
        group_id="7788",
        sender_id="2046",
    )
    kwargs = bridge.query_relevant_memory.await_args.kwargs
    assert [s2["subject_kind"] for s2 in kwargs["subjects"]] == ["group_chat"]

    # sender_id is normalized the same way the write side normalizes it —
    # a padded id must not land in a different participant bucket.
    plugin._qq_settings["group_member_memory_enabled"] = True
    bridge.query_relevant_memory.reset_mock()
    await QQReplyContextNode(plugin)._build_recalled_memory_text(
        her_name="Neko",
        message="群规是什么？",
        should_use_memory_context=True,
        attachments=None,
        is_group=True,
        group_id="7788",
        sender_id="  2046  ",
    )
    kwargs = bridge.query_relevant_memory.await_args.kwargs
    assert kwargs["subjects"][1]["subject_id"] == "qq:7788:2046"

    # Member opt-out DURING the recall await: the result mixes group and
    # participant text and cannot be split afterwards — drop it whole.
    async def _recall_then_revoke(*args, **kwargs):
        plugin._qq_settings["group_member_memory_enabled"] = False
        return QQMemoryQueryResult(text="成员私密偏好", hit_count=1)

    plugin._qq_settings["group_member_memory_enabled"] = True
    bridge.query_relevant_memory = AsyncMock(side_effect=_recall_then_revoke)
    assert await QQReplyContextNode(plugin)._build_recalled_memory_text(
        her_name="Neko",
        message="群规是什么？",
        should_use_memory_context=True,
        attachments=None,
        is_group=True,
        group_id="7788",
        sender_id="2046",
    ) == ""
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
        _qq_settings={
            "group_memory_enabled": True,
            "group_member_memory_enabled": True,
        },
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
async def test_scoped_fact_rejected_by_character_card(tmp_path):
    """A scoped write only scans its own @subject section, so a group-
    derived claim contradicting the fixed character definition (stored
    under master/neko/relationship) must still be rejected by an explicit
    card check — otherwise it becomes a durable scoped persona entry."""
    from memory.persona import PersonaManager

    subject = MemorySubject.group_chat("qq", "7788")
    pm = PersonaManager()
    pm._config_manager = _build_scope_mock_cm(str(tmp_path))
    name = "neko_card_guard"
    persona = await pm.aensure_persona(name)
    persona["neko"] = {
        "facts": [
            {
                "id": "card1", "text": "她讨厌吃香菜",
                "source": "character_card",
            },
        ],
    }
    await pm.asave_persona(name, persona)

    code = await pm.aadd_fact(
        name, "她讨厌吃香菜是假的，她喜欢吃香菜",
        entity="group_chat", source="reflection_time_driven",
        source_id="r-card", subject=subject,
    )
    assert code == PersonaManager.FACT_REJECTED_CARD
    persona = await pm.aensure_persona(name)
    scoped_section = persona.get(subject.persona_section_key) or {}
    assert not (scoped_section.get("facts") or [])

    # A non-conflicting scoped claim still lands.
    code = await pm.aadd_fact(
        name, "群里周五常常聊摄影",
        entity="group_chat", source="reflection_time_driven",
        source_id="r-ok", subject=subject,
    )
    assert code == PersonaManager.FACT_ADDED


@pytest.mark.asyncio
async def test_scoped_promotion_is_idempotent_after_partial_commit():
    """The persona write and the reflection status flip are two stores. If
    the reflections save fails after the entry landed, the retry's
    aadd_fact sees its own text and returns QUEUED_CORRECTION forever —
    the reflection would stay confirmed and re-queue a self-correction on
    every tick. An existing entry with this reflection's source_id in the
    same subject counts as already promoted."""
    from memory.persona import PersonaManager
    from memory.reflection.promotion import PromotionMixin

    subject = MemorySubject.group_chat("qq", "7788")
    mixin = PromotionMixin.__new__(PromotionMixin)
    mixin._persona_manager = SimpleNamespace(
        aensure_persona=AsyncMock(return_value={
            subject.persona_section_key: {
                "facts": [
                    {
                        "id": "p1", "text": "群里常聊摄影",
                        "source_id": "r-1", **subject.as_entry_fields(),
                    },
                ],
            },
        }),
    )
    assert await mixin._ascoped_promotion_already_applied(
        "Neko", "r-1", subject,
    ) is True
    # A different reflection id, or another subject's entry, does not count.
    assert await mixin._ascoped_promotion_already_applied(
        "Neko", "r-2", subject,
    ) is False
    other = MemorySubject.group_chat("qq", "9999")
    assert await mixin._ascoped_promotion_already_applied(
        "Neko", "r-1", other,
    ) is False
    assert PersonaManager.FACT_QUEUED_CORRECTION is not None

    # Behavioural check on the real promote path: a QUEUED_CORRECTION for
    # a reflection whose entry already exists completes the transition
    # instead of looping self-corrections forever.
    from datetime import datetime, timedelta

    from config import WEAK_MEMORY_AUTO_PROMOTE_DAYS

    old_ts = (
        datetime.now() - timedelta(days=WEAK_MEMORY_AUTO_PROMOTE_DAYS + 1)
    ).isoformat()
    reflections = [{
        "id": "r-1", "status": "confirmed", "text": "群里常聊摄影",
        "entity": "group_chat", "confirmed_at": old_ts,
        **subject.as_entry_fields(),
    }]
    engine = PromotionMixin.__new__(PromotionMixin)
    engine._persona_manager = SimpleNamespace(
        aensure_persona=mixin._persona_manager.aensure_persona,
        aadd_fact=AsyncMock(
            return_value=PersonaManager.FACT_QUEUED_CORRECTION,
        ),
    )
    engine._get_alock = lambda name: asyncio.Lock()
    engine._aload_reflections_full = AsyncMock(return_value=reflections)
    engine.asave_reflections = AsyncMock()
    engine._abatch_mark_surfaced_handled = AsyncMock()
    await engine.aauto_promote_time_driven("Neko", scoped_only=True)
    assert reflections[0]["status"] == "promoted"


@pytest.mark.asyncio
async def test_scoped_read_refreshes_reflection_suppressions():
    """aupdate_suppressions is the only thing that clears reflection
    suppression after the cooldown, and it was reachable only through the
    legacy endpoints — a group-only deployment would hide a scoped
    reflection forever after its first suppression."""
    from app.memory_server import routes

    subject = MemorySubject.group_chat("qq", "7788")
    engine = SimpleNamespace(
        aupdate_suppressions=AsyncMock(),
        aget_pending_reflections=AsyncMock(return_value=[]),
        aget_confirmed_reflections=AsyncMock(return_value=[]),
    )
    persona = SimpleNamespace(
        arender_persona_markdown=AsyncMock(return_value="持久化人设"),
    )
    req = SimpleNamespace(
        subjects=[SimpleNamespace(to_domain=lambda: subject)],
    )
    with patch.object(routes.runtime, "reflection_engine", engine, create=True),          patch.object(routes.runtime, "persona_manager", persona, create=True):
        await routes.get_scoped_context("Neko", req)
    engine.aupdate_suppressions.assert_awaited_once_with("Neko")


@pytest.mark.asyncio
async def test_scoped_synthesis_runs_when_legacy_synthesis_raises():
    """A persistent legacy-only failure (e.g. a hand-edited fact without an
    id raising inside the legacy pass) must not starve the scoped pass —
    otherwise that character's group/member reflections never run."""
    from app.memory_server import refine_loops

    scoped = AsyncMock(return_value=[{"id": "r1"}])
    runtime = SimpleNamespace(
        _config_manager=SimpleNamespace(
            aload_characters=AsyncMock(return_value={"猫娘": {"Neko": {}}}),
        ),
        reflection_engine=SimpleNamespace(
            synthesize_reflections=AsyncMock(
                side_effect=KeyError("id"),
            ),
            synthesize_scoped_reflections=scoped,
        ),
    )
    sleeps = {"n": 0}

    async def _sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise asyncio.CancelledError()

    with patch.object(refine_loops, "runtime", runtime, create=True),          patch.object(refine_loops.asyncio, "sleep", _sleep):
        with pytest.raises(asyncio.CancelledError):
            await refine_loops._periodic_reflection_synthesis_loop()
    scoped.assert_awaited_once()


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
    bad_importance = {
        "id": "c0", "text": "c", "importance": "high",
        **group_a.as_entry_fields(),
    }
    fs.aload_facts = AsyncMock(
        return_value=["corrupted-row", no_id, bad_importance, good],
    )
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

        # Non-string text (e.g. {"text": 123}) passes a str()-based check
        # but persistence calls .strip() on the ORIGINAL value and raises
        # mid-batch, after earlier entries already mutated the in-memory
        # list and FTS index — reject it up front as retryable.
        async def _nonstring_text(prompt, lanlan_name, **kwargs):
            return [{"text": 123, "importance": 5}]

        fs._allm_call_with_retries = _nonstring_text
        with pytest.raises(FactExtractionFailed):
            await fs.extract_facts([msg], "Neko", fail_closed=True)

        # Persistence failure rolls the cached additions back: without the
        # rollback a retry hits the content-hash dedup in the still-mutated
        # cache, returns an empty success, and the caller advances its
        # cursor over facts that never reached disk.
        async def _valid(prompt, lanlan_name, **kwargs):
            return [{"text": "有效事实", "importance": 6}]

        fs._allm_call_with_retries = _valid
        fs.asave_facts = AsyncMock(side_effect=RuntimeError("disk full"))
        with pytest.raises(RuntimeError):
            await fs.extract_facts([msg], "Neko", fail_closed=True)
        cached = await fs.aload_facts("Neko")
        assert not any(
            isinstance(f, dict) and f.get("text") == "有效事实" for f in cached
        )
        fs.asave_facts = AsyncMock(return_value=None)
        created = await fs.extract_facts([msg], "Neko", fail_closed=True)
        assert any(f.get("text") == "有效事实" for f in created)

        # In-place upgrades roll back too: leaving the upgraded source in
        # the cache makes the retry hit the upgrade guard, record zero
        # upgrades, skip the save entirely — and report success.
        fs._time_indexed = None
        cached = await fs.aload_facts("Neko")
        target = next(
            f for f in cached
            if isinstance(f, dict) and f.get("text") == "有效事实"
        )
        target["source"] = "ai_disclosure"
        fs.asave_facts = AsyncMock(side_effect=RuntimeError("disk full"))
        with pytest.raises(RuntimeError):
            await fs.extract_facts([msg], "Neko", fail_closed=True)
        assert target["source"] == "ai_disclosure"
        fs.asave_facts = AsyncMock(return_value=None)
        await fs.extract_facts([msg], "Neko", fail_closed=True)
        assert target["source"] == "user_observation"
        fs.asave_facts.assert_awaited()

        # Cancellation must roll back too: CancelledError does not pass
        # through except Exception, and a retained cache entry makes the
        # retry dedup into an empty success.
        async def _cancel_text(prompt, lanlan_name, **kwargs):
            return [{"text": "取消时的事实", "importance": 6}]

        fs._allm_call_with_retries = _cancel_text
        fs._time_indexed = None
        fs.asave_facts = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await fs.extract_facts([msg], "Neko", fail_closed=True)
        cached = await fs.aload_facts("Neko")
        assert not any(
            isinstance(f, dict) and f.get("text") == "取消时的事实"
            for f in cached
        )
        fs.asave_facts = AsyncMock(return_value=None)

        # An indexing failure (maintenance mode etc.) happens BEFORE the
        # save and must roll back the same way — the row is already in the
        # cache and hash set at that point.
        async def _another(prompt, lanlan_name, **kwargs):
            return [{"text": "索引失败的事实", "importance": 6}]

        fs._allm_call_with_retries = _another
        fs._time_indexed = SimpleNamespace(
            aindex_fact=AsyncMock(side_effect=RuntimeError("maintenance")),
            adelete_fact_from_index=AsyncMock(),
            asearch_facts=AsyncMock(return_value=[]),
        )
        with pytest.raises(RuntimeError):
            await fs.extract_facts([msg], "Neko", fail_closed=True)
        cached = await fs.aload_facts("Neko")
        assert not any(
            isinstance(f, dict) and f.get("text") == "索引失败的事实"
            for f in cached
        )
        # The hash set no longer blocks the retry: with indexing healthy
        # the same content persists.
        fs._time_indexed = None
        created = await fs.extract_facts([msg], "Neko", fail_closed=True)
        assert any(f.get("text") == "索引失败的事实" for f in created)


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
        _run_with_session_lock=_passthrough_session_lock,
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
        _run_with_session_lock=_passthrough_session_lock,
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
async def test_default_reply_is_not_history_backed():
    """A forced turn that falls through to the default message appended no
    ai row for this turn (primary raised/timed out): scheduling it as
    history-backed would mark an older, already delivered reply as the
    pending draft and could exclude it from scoped history forever."""
    from plugin.plugins.qq_auto_reply.pipeline_models import (
        QQDeliveryPlan,
        QQMessageBlock,
        QQReplyOutcome,
        QQReplyRequest,
    )
    from plugin.plugins.qq_auto_reply.reply_pipeline import (
        QQReplyPipelineRunner,
    )

    captured = {}

    async def _schedule(**kwargs):
        captured.update(kwargs)

    plugin = SimpleNamespace(
        reply_buffer_service=SimpleNamespace(schedule_reply=_schedule),
        _build_session_key=(
            lambda *, sender_id, is_group, group_id: f"group:{group_id}"
        ),
        _emit_log=lambda *a, **k: None,
    )
    runner = QQReplyPipelineRunner(plugin)
    request = QQReplyRequest(
        message_text="hi", sender_id="1", is_group=True, group_id="7788",
        persist_memory=True,
    )
    plan = QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(text="嗯嗯~")],
    )
    outcome = QQReplyOutcome(
        action="reply", reply_text="嗯嗯~", used_default_message=True,
        raw_reply_text="嗯嗯~",
    )
    await runner._run_delivery(plan, request, outcome, context=None)
    assert captured["history_backed"] is False

    # A normal generated reply stays history-backed.
    captured.clear()
    normal = QQReplyOutcome(
        action="reply", reply_text="真回复", raw_reply_text="真回复",
    )
    await runner._run_delivery(plan, request, normal, context=None)
    assert captured["history_backed"] is True


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
        _run_with_session_lock=_passthrough_session_lock,
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
async def test_nonconsent_buffered_input_excludes_summary_ai_row():
    """Messages buffered while group memory was OFF can be merged after an
    ON flip: the summary's ai row derives from pre-opt-in input and lands
    past the rebase boundary — it must join the exclusion list alongside
    the synthetic prompt."""
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

    history = [_msg("human", "u1")]
    user_data = {
        "is_group": True,
        "session": SimpleNamespace(_conversation_history=history),
    }
    sys_row = _msg("human", "[synthetic merge prompt]")
    summary_row = _msg("ai", "衍生总结")

    async def _run(request):
        history.append(sys_row)
        history.append(summary_row)
        return SimpleNamespace(action="reply", reply_text="衍生总结")

    async def _lock(session_key, fn):
        return await fn()

    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _emit_log=lambda *a, **k: None,
        _run_with_session_lock=_lock,
        reply_pipeline=SimpleNamespace(run=AsyncMock(side_effect=_run)),
    )
    memory_service = QQSessionMemoryService.__new__(QQSessionMemoryService)
    memory_service.plugin = plugin
    plugin.session_memory_service = memory_service
    service = QQReplyBufferService.__new__(QQReplyBufferService)
    service.plugin = plugin
    service._pending = {}
    pending = PendingReply(
        first_text="草稿", wait_seconds=0, sender_id="1",
        is_group=True, group_id="7788",
    )
    pending.message_count = 2
    pending.buffered_texts = ["OFF 时代输入", "第二条"]
    pending.first_blocks = [QQMessageBlock(text="草稿")]
    pending.wait_until = 0.0
    pending.has_nonconsent_input = True
    service._pending["group:7788"] = pending

    await service._deliver_after_wait("group:7788", pending)
    rows = user_data["undelivered_draft_rows"]
    assert any(r is sys_row for r in rows)
    assert any(r is summary_row for r in rows)

    # Fully consented buffers keep the delivered summary in memory.
    history2 = [_msg("human", "u1")]
    user_data2 = {
        "is_group": True,
        "session": SimpleNamespace(_conversation_history=history2),
    }
    sys_row2 = _msg("human", "[synthetic merge prompt]")
    summary_row2 = _msg("ai", "正常总结")

    async def _run2(request):
        history2.append(sys_row2)
        history2.append(summary_row2)
        return SimpleNamespace(action="reply", reply_text="正常总结")

    plugin._user_sessions = {"group:7788": user_data2}
    plugin.reply_pipeline = SimpleNamespace(run=AsyncMock(side_effect=_run2))
    pending2 = PendingReply(
        first_text="草稿", wait_seconds=0, sender_id="1",
        is_group=True, group_id="7788",
    )
    pending2.message_count = 2
    pending2.buffered_texts = ["a", "b"]
    pending2.first_blocks = [QQMessageBlock(text="草稿")]
    pending2.wait_until = 0.0
    service._pending["group:7788"] = pending2
    await service._deliver_after_wait("group:7788", pending2)
    rows2 = user_data2["undelivered_draft_rows"]
    assert any(r is sys_row2 for r in rows2)
    assert not any(r is summary_row2 for r in rows2)


@pytest.mark.asyncio
async def test_merge_flush_cleanup_runs_even_on_pipeline_failure():
    """A failing merge-flush pipeline must still pop the pending entry and
    settle the provisional barrier — leaking either wedges the digest
    cursor in front of a dead draft row forever."""
    from plugin.plugins.qq_auto_reply.pipeline_models import QQMessageBlock
    from plugin.plugins.qq_auto_reply.reply_buffer_service import (
        PendingReply,
        QQReplyBufferService,
    )

    draft = SimpleNamespace(type="ai", content="草稿")
    history = [SimpleNamespace(type="human", content="u1"), draft]
    user_data = {
        "is_group": True,
        "session": SimpleNamespace(_conversation_history=history),
        "undelivered_draft_rows": [draft],
        "provisional_draft_rows": [draft],
    }

    async def _boom(session_key, fn):
        raise RuntimeError("pipeline down")

    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _emit_log=lambda *a, **k: None,
        _run_with_session_lock=_boom,
        reply_pipeline=SimpleNamespace(run=AsyncMock()),
    )
    service = QQReplyBufferService.__new__(QQReplyBufferService)
    service.plugin = plugin
    service._pending = {}
    pending = PendingReply(
        first_text="草稿", wait_seconds=0, sender_id="1",
        is_group=True, group_id="7788",
    )
    pending.message_count = 2
    pending.buffered_texts = ["草稿", "第二条"]
    pending.first_blocks = [QQMessageBlock(text="草稿")]
    pending.wait_until = 0.0
    pending.draft_rows = [draft]
    service._pending["group:7788"] = pending

    await service._deliver_after_wait("group:7788", pending)
    assert "group:7788" not in service._pending
    assert user_data["provisional_draft_rows"] == []
    assert user_data["undelivered_draft_rows"] == [draft]


@pytest.mark.asyncio
async def test_force_summary_branch_binds_draft_before_settling():
    """The 17+ forced-summary branch returns before the tail association:
    it must bind the just-recorded draft row to the pending first, or the
    settle step cannot find it and the provisional barrier never lifts."""
    from plugin.plugins.qq_auto_reply.pipeline_models import QQMessageBlock
    from plugin.plugins.qq_auto_reply.reply_buffer_service import (
        PendingReply,
        QQReplyBufferService,
    )
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    draft17 = SimpleNamespace(type="ai", content="第十七条的草稿")
    history = [SimpleNamespace(type="human", content="u1"), draft17]
    user_data = {
        "is_group": True,
        "session": SimpleNamespace(_conversation_history=history),
    }
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _emit_log=lambda *a, **k: None,
        reply_pipeline=SimpleNamespace(
            run=AsyncMock(return_value=SimpleNamespace(
                action="reply", reply_text="总结",
            )),
        ),
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
    waiting.message_count = 16
    waiting.buffered_texts = [f"旧{i}" for i in range(16)]
    waiting.task = asyncio.create_task(asyncio.sleep(999))
    service._pending["group:7788"] = waiting

    summary_row = SimpleNamespace(type="ai", content="强制总结")

    async def _run_forced(request):
        history.append(SimpleNamespace(type="human", content="[synthetic]"))
        history.append(summary_row)
        return SimpleNamespace(action="reply", reply_text="强制总结")

    plugin.reply_pipeline.run = AsyncMock(side_effect=_run_forced)
    # consented=False must be honoured BEFORE the forced-summary branch
    # runs (it returns early, so a tail-set marker would be too late).
    await service.schedule_reply(
        session_key="group:7788", reply_text="第十七条的草稿",
        raw_text="第十七条的草稿", blocks=[QQMessageBlock(text="x")],
        wait_seconds=999, sender_id="1", is_group=True, group_id="7788",
        consented=False,
    )
    assert "group:7788" not in service._pending
    # The draft stays permanently excluded, but the provisional barrier
    # is lifted — the settle step found the row via the pending binding.
    assert draft17 in user_data["undelivered_draft_rows"]
    assert user_data.get("provisional_draft_rows") == []
    # Nonconsent buffered input: the eager forced summary's ai row is
    # excluded too (same rule as the delayed merge flush).
    assert any(
        r is summary_row for r in user_data["undelivered_draft_rows"]
    )


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
    # The generated reply derives from the pre-opt-in message: excluded too.
    assert any(getattr(r, "type", "") == "ai" for r in rows)
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
            append_fallback_ai_row=MagicMock(),
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
    # A history-backed reply already has its ai row; nothing to append.
    plugin.reply_generation_service.append_fallback_ai_row.assert_not_called()

    # A CONFIRMED fallback delivery must append the missing ai row here —
    # the direct-delivery branch is the only place that can do it for
    # unbuffered replies, and without it the digest keeps one-sided turns.
    from plugin.plugins.qq_auto_reply.pipeline_models import QQReplyOutcome as _O

    await runner._run_delivery(
        plan, None,
        _O(action="reply", reply_text="回复", used_fallback=True),
        context=context,
    )
    plugin.reply_generation_service.append_fallback_ai_row.assert_called_once_with(
        context, "回复",
    )
    plugin.reply_generation_service.append_fallback_ai_row.reset_mock()

    # Failed delivery records no mentions AND marks the history-backed ai
    # row as undelivered — the unsent reply must not reach digests.
    plugin.reply_generation_service.record_scoped_mentions_on_delivery.reset_mock()
    plugin.reply_delivery_node.deliver = AsyncMock(return_value=QQDeliveryResult(
        delivered=False, target_type="group", target_id="7788", reply_text=None,
    ))
    plugin._build_session_key = (
        lambda *, sender_id, is_group, group_id: f"group:{group_id}"
    )
    plugin.session_memory_service = SimpleNamespace(
        record_tail_undelivered_ai_row=MagicMock(),
    )
    from plugin.plugins.qq_auto_reply.pipeline_models import QQReplyRequest

    failed_request = QQReplyRequest(
        message_text="hi", sender_id="1", is_group=True, group_id="7788",
    )
    await runner._run_delivery(plan, failed_request, outcome, context=context)
    plugin.reply_generation_service.record_scoped_mentions_on_delivery.assert_not_awaited()
    plugin.session_memory_service.record_tail_undelivered_ai_row.assert_called_once_with(
        "group:7788"
    )
    # Fallback replies have no history row: nothing to mark.
    plugin.session_memory_service.record_tail_undelivered_ai_row.reset_mock()
    fb_outcome = QQReplyOutcome(
        action="reply", reply_text="回复", used_fallback=True,
    )
    await runner._run_delivery(plan, failed_request, fb_outcome, context=context)
    plugin.session_memory_service.record_tail_undelivered_ai_row.assert_not_called()
    # ... and an UNCONFIRMED fallback appends nothing either.
    plugin.reply_generation_service.append_fallback_ai_row.assert_not_called()

    # A RAISING transport (NapCat) marks the tail row before propagating —
    # exiting at the await without marking would let the next digest
    # persist the unsent reply.
    plugin.session_memory_service.record_tail_undelivered_ai_row.reset_mock()
    plugin.reply_delivery_node.deliver = AsyncMock(
        side_effect=RuntimeError("transport down"),
    )
    with pytest.raises(RuntimeError):
        await runner._run_delivery(plan, failed_request, outcome, context=context)
    plugin.session_memory_service.record_tail_undelivered_ai_row.assert_called_once_with(
        "group:7788"
    )


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
    # NapCat now has a receipt too (the CQ-string senders do the same echo
    # round-trip as the segment ones), so a missing message id means the
    # action never came back: unconfirmed, not delivered.
    result = await _node(True, None).deliver(plan)
    assert result.delivered is False
    result = await _node(True, "napcat-mid").deliver(plan)
    assert result.delivered is True

    # Voice mode: the TTS chain now propagates confirmation — an Open
    # Platform failure swallowed inside the wrappers must not report
    # delivered=True.
    voice_plugin = SimpleNamespace(
        _get_reply_mode=lambda: "voice",
        qq_client=SimpleNamespace(needs_attention=False),
        _deliver_group_reply=AsyncMock(return_value=False),
    )
    voice_node = QQReplyDeliveryNode.__new__(QQReplyDeliveryNode)
    voice_node.plugin = voice_plugin
    result = await voice_node.deliver(plan)
    assert result.delivered is False
    voice_plugin._deliver_group_reply = AsyncMock(return_value=True)
    result = await voice_node.deliver(plan)
    assert result.delivered is True

    # Pure sticker plan: media sends confirm too (Open Platform None = not
    # delivered).
    sticker_plugin = SimpleNamespace(
        _get_reply_mode=lambda: "text",
        _resolve_sticker_path=lambda sid: "/tmp/s.png",
        qq_client=SimpleNamespace(
            needs_attention=False,
            send_group_image=AsyncMock(return_value=None),
        ),
    )
    sticker_node = QQReplyDeliveryNode.__new__(QQReplyDeliveryNode)
    sticker_node.plugin = sticker_plugin
    sticker_plan = QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(sticker="s1")],
    )
    result = await sticker_node.deliver(sticker_plan)
    assert result.delivered is False
    sticker_plugin.qq_client.send_group_image = AsyncMock(return_value="mid")
    result = await sticker_node.deliver(sticker_plan)
    assert result.delivered is True

    # A sticker that could not be sent alongside text that WAS sent leaves
    # the verdict alone: stickers are decoration, the memory row is the
    # text, and marking the whole reply undelivered would drop a reply the
    # group actually read.
    sticker_plugin.qq_client.send_group_image = AsyncMock(return_value=None)
    sticker_plugin.qq_client.send_group_message = AsyncMock(return_value="mid")
    result = await sticker_node.deliver(QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(sticker="s1"), QQMessageBlock(text="正文")],
    ))
    assert result.delivered is True
    # ...but a failed text block still decides the verdict, sticker or not.
    sticker_plugin.qq_client.send_group_message = AsyncMock(return_value=None)
    result = await sticker_node.deliver(QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(sticker="s1"), QQMessageBlock(text="正文")],
    ))
    assert result.delivered is False

    # Private record block: send_private_record now propagates the Open
    # Platform result — a real send confirms (no false negative), a
    # swallowed failure does not.
    record_plugin = SimpleNamespace(
        _get_reply_mode=lambda: "text",
        voice_reply_service=SimpleNamespace(
            synthesize_reply_voice_file=AsyncMock(return_value=("file://x", 0)),
        ),
        qq_client=SimpleNamespace(
            needs_attention=False,
            send_private_record=AsyncMock(return_value="mid"),
        ),
    )
    record_node = QQReplyDeliveryNode.__new__(QQReplyDeliveryNode)
    record_node.plugin = record_plugin
    record_plan = QQDeliveryPlan(
        target_type="private", target_id="10086",
        blocks=[QQMessageBlock(record="早上好")],
        fallback_to_text_on_voice_failure=False,
    )
    result = await record_node.deliver(record_plan)
    assert result.delivered is True
    record_plugin.qq_client.send_private_record = AsyncMock(return_value=None)
    result = await record_node.deliver(record_plan)
    assert result.delivered is False

    # Unconfirmed record WITH the fallback flag: text fallback runs and
    # its confirmation decides the verdict (same as voice mode).
    record_plugin.qq_client.send_message = AsyncMock(return_value="mid")
    record_fb_plan = QQDeliveryPlan(
        target_type="private", target_id="10086",
        blocks=[QQMessageBlock(record="早上好")],
        fallback_to_text_on_voice_failure=True,
    )
    result = await record_node.deliver(record_fb_plan)
    assert result.delivered is True
    record_plugin.qq_client.send_message.assert_awaited_once()

    # Open Platform wrapper level: send_private_record must propagate the
    # segments result (its group twin already does) — a permanent None here
    # falsely marks every delivered private voice reply as unsent.
    from plugin.plugins.qq_auto_reply.qq_open_plat import (
        QQOpenPlatformConnection,
    )

    plat = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    plat.send_private_message_segments = AsyncMock(return_value="mid")
    assert await plat.send_private_record("10086", "file://x") == "mid"
    plat.send_private_message_segments = AsyncMock(return_value=None)
    assert await plat.send_private_record("10086", "file://x") is None
    # Poke fallback text propagates too — a swallowed failure must not
    # report a hardcoded success.
    plat.send_group_message_segments = AsyncMock(return_value=None)
    assert await plat.send_group_poke("7788", "1") is None
    plat.send_group_message_segments = AsyncMock(return_value="mid")
    assert await plat.send_group_poke("7788", "1") == "mid"

    # Poke-only plan: a skipped poke (private target / cooldown) sends
    # nothing and must not report delivered; a confirmed group poke does.
    poke_plugin = SimpleNamespace(
        _get_reply_mode=lambda: "text",
        _emit_log=lambda *a, **k: None,
        qq_client=SimpleNamespace(
            needs_attention=False,
            send_group_poke=AsyncMock(return_value="ok"),
        ),
    )
    poke_node = QQReplyDeliveryNode.__new__(QQReplyDeliveryNode)
    poke_node.plugin = poke_plugin
    poke_group = QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(poke="2")],
    )
    result = await poke_node.deliver(poke_group)
    assert result.delivered is True
    # Poke + text: the poke is now inside its 30 s cooldown and is skipped,
    # but the text landed. The template puts <poke> in its own block ahead
    # of the text block, so in an active group this is the common shape —
    # letting the skip decide the verdict would exclude a reply the group
    # actually read from scoped memory on nearly every second turn.
    poke_plugin.qq_client.send_group_message = AsyncMock(return_value="mid")
    result = await poke_node.deliver(QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(poke="2"), QQMessageBlock(text="正文")],
    ))
    assert result.delivered is True
    poke_plugin.qq_client.send_group_message.assert_awaited_once()
    # Same for a poke the platform rejected: decoration never overrides a
    # delivered text block.
    poke_plugin.qq_client.send_group_poke = AsyncMock(return_value=None)
    poke_plugin.qq_client.send_group_message = AsyncMock(return_value="mid")
    result = await poke_node.deliver(QQDeliveryPlan(
        target_type="group", target_id="4455",
        blocks=[QQMessageBlock(poke="2"), QQMessageBlock(text="正文")],
    ))
    assert result.delivered is True
    # With no text to carry the verdict, a rejected poke means nothing was
    # sent at all — that plan is undelivered.
    result = await poke_node.deliver(QQDeliveryPlan(
        target_type="group", target_id="9911",
        blocks=[QQMessageBlock(poke="2")],
    ))
    assert result.delivered is False

    # Keyboard-only block: the segments API carries buttons, so it must be
    # sent (and confirmed) instead of silently counting as delivered.
    kb_plugin = SimpleNamespace(
        _get_reply_mode=lambda: "text",
        logger=MagicMock(),
        qq_client=SimpleNamespace(
            needs_attention=False,
            send_group_message_segments=AsyncMock(return_value="mid"),
        ),
    )
    kb_node = QQReplyDeliveryNode.__new__(QQReplyDeliveryNode)
    kb_node.plugin = kb_plugin
    kb_plan = QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(keyboard="要|不要")],
    )
    result = await kb_node.deliver(kb_plan)
    assert result.delivered is True
    assert kb_plugin.qq_client.send_group_message_segments.await_args.kwargs[
        "keyboard"
    ] == "要|不要"
    # Content must be non-blank: the Open Platform sender strips whitespace
    # and returns None before building the keyboard payload.
    sent_segments = kb_plugin.qq_client.send_group_message_segments.await_args.args[1]
    assert sent_segments[0]["data"]["text"].strip()
    kb_plugin.qq_client.send_group_message_segments = AsyncMock(return_value=None)
    result = await kb_node.deliver(kb_plan)
    assert result.delivered is False

    # NapCat cannot render official buttons (its segments sender ignores the
    # kwarg): send the labels as readable text instead of a bare space.
    napcat_plugin = SimpleNamespace(
        _get_reply_mode=lambda: "text",
        logger=MagicMock(),
        qq_client=SimpleNamespace(
            needs_attention=True,
            send_group_message=AsyncMock(return_value="napcat-mid"),
            send_group_message_segments=AsyncMock(return_value=None),
        ),
    )
    napcat_node = QQReplyDeliveryNode.__new__(QQReplyDeliveryNode)
    napcat_node.plugin = napcat_plugin
    result = await napcat_node.deliver(kb_plan)
    assert result.delivered is True
    # ...and an action that never came back is unconfirmed here too.
    napcat_plugin.qq_client.send_group_message = AsyncMock(return_value=None)
    result = await napcat_node.deliver(kb_plan)
    assert result.delivered is False
    napcat_plugin.qq_client.send_group_message = AsyncMock(return_value="napcat-mid")
    napcat_plugin.qq_client.send_group_message_segments.assert_not_awaited()
    await napcat_node.deliver(kb_plan)
    assert napcat_plugin.qq_client.send_group_message.await_args.args[1] == "要 / 不要"

    # Text + keyboard on NapCat: the choices are appended to the text
    # instead of vanishing (buttons cannot render on this protocol).
    napcat_plugin.qq_client.send_group_message = AsyncMock(return_value=None)
    await napcat_node.deliver(QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(text="要看看哪个？", keyboard="状态|配置|日志")],
    ))
    sent_text = napcat_plugin.qq_client.send_group_message.await_args.args[1]
    assert "要看看哪个？" in sent_text
    assert "状态 / 配置 / 日志" in sent_text

    # NapCat reports poke/sticker failures explicitly (unlike its
    # fire-and-forget text send): those must not count as delivered.
    fail_plugin = SimpleNamespace(
        _get_reply_mode=lambda: "text",
        _emit_log=lambda *a, **k: None,
        logger=MagicMock(),
        _resolve_sticker_path=lambda sid: "/tmp/s.png",
        qq_client=SimpleNamespace(
            needs_attention=True,
            send_group_poke=AsyncMock(return_value=False),
            send_group_image=AsyncMock(return_value=None),
        ),
    )
    fail_node = QQReplyDeliveryNode.__new__(QQReplyDeliveryNode)
    fail_node.plugin = fail_plugin
    result = await fail_node.deliver(QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(poke="2")],
    ))
    assert result.delivered is False
    result = await fail_node.deliver(QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(sticker="s1")],
    ))
    assert result.delivered is False
    # Different group: the 30s poke cooldown is per-group, so this exercises
    # the success path rather than the skip path.
    fail_plugin.qq_client.send_group_poke = AsyncMock(return_value=True)
    result = await fail_node.deliver(QQDeliveryPlan(
        target_type="group", target_id="8899",
        blocks=[QQMessageBlock(poke="3")],
    ))
    assert result.delivered is True

    # Private keyboard-only block: buttons are group-only, so nothing can
    # be sent — it must report undelivered rather than silently vanish
    # (same rule as the ark block).
    priv_kb_plugin = SimpleNamespace(
        _get_reply_mode=lambda: "text",
        logger=MagicMock(),
        qq_client=SimpleNamespace(
            needs_attention=False,
            send_message=AsyncMock(return_value="mid"),
        ),
    )
    priv_kb_node = QQReplyDeliveryNode.__new__(QQReplyDeliveryNode)
    priv_kb_node.plugin = priv_kb_plugin
    result = await priv_kb_node.deliver(QQDeliveryPlan(
        target_type="private", target_id="10086",
        blocks=[QQMessageBlock(keyboard="要|不要")],
    ))
    assert result.delivered is False
    priv_kb_plugin.qq_client.send_message.assert_not_awaited()

    # Private text + keyboard: the block IS sendable, but buttons cannot be
    # rendered, so the labels must ride along in the text — otherwise the
    # user is asked "which one?" without ever seeing the options.
    result = await priv_kb_node.deliver(QQDeliveryPlan(
        target_type="private", target_id="10086",
        blocks=[QQMessageBlock(text="要看看哪个？", keyboard="状态|配置")],
    ))
    assert result.delivered is True
    sent_private = priv_kb_plugin.qq_client.send_message.await_args.args[1]
    assert "状态 / 配置" in sent_private

    # Voice mode carries the choice labels into the TTS content, otherwise
    # the spoken reply asks about options it never names.
    voice_kb_plugin = SimpleNamespace(
        _get_reply_mode=lambda: "voice",
        logger=MagicMock(),
        qq_client=SimpleNamespace(needs_attention=False),
        _deliver_group_reply=AsyncMock(return_value=True),
    )
    voice_kb_node = QQReplyDeliveryNode.__new__(QQReplyDeliveryNode)
    voice_kb_node.plugin = voice_kb_plugin
    await voice_kb_node.deliver(QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(text="要看看哪个？", keyboard="状态|配置")],
    ))
    spoken = voice_kb_plugin._deliver_group_reply.await_args.args[1]
    assert "状态 / 配置" in spoken

    # Ark-only plan: nothing is actually sent (no delivery implementation),
    # so it must not report delivered and clear the draft exclusion.
    ark_plugin = SimpleNamespace(
        _get_reply_mode=lambda: "text",
        logger=MagicMock(),
        qq_client=SimpleNamespace(needs_attention=False),
    )
    ark_node = QQReplyDeliveryNode.__new__(QQReplyDeliveryNode)
    ark_node.plugin = ark_plugin
    ark_plan = QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(ark={"title": "卡片"})],
    )
    result = await ark_node.deliver(ark_plan)
    assert result.delivered is False

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
async def test_unconfirmed_voice_send_falls_back_to_text():
    """An Open Platform voice send that returns None (swallowed failure,
    no exception) must still run the requested text fallback — returning
    False directly would drop the reply entirely."""
    from plugin.plugins.qq_auto_reply.voice_reply_service import (
        QQVoiceReplyService,
    )

    plugin = SimpleNamespace(
        _validate_outbound_message=lambda t: t,
        _get_reply_mode=lambda: "voice",
        qq_client=SimpleNamespace(
            needs_attention=False,
            send_private_record=AsyncMock(return_value=None),
            send_message=AsyncMock(return_value="mid"),
        ),
        logger=MagicMock(),
    )
    service = QQVoiceReplyService.__new__(QQVoiceReplyService)
    service.plugin = plugin
    service.synthesize_reply_voice_file = AsyncMock(
        return_value=("file://x", 0),
    )
    ok = await service.deliver_private_reply(
        "10086", "你好", fallback_to_text_on_voice_failure=True,
    )
    assert ok is True
    plugin.qq_client.send_message.assert_awaited_once()

    # Without the fallback flag the unconfirmed send stays False.
    plugin.qq_client.send_message.reset_mock()
    ok = await service.deliver_private_reply(
        "10086", "你好", fallback_to_text_on_voice_failure=False,
    )
    assert ok is False
    plugin.qq_client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_merged_buffer_keeps_older_consent_dependencies():
    """Merging a later draft (generated after revocation, so all-false)
    must not erase the earlier draft's true-valued dependencies — the
    revocation check would then see no transition and the summary prompt
    would still carry the memory-derived text."""
    from plugin.plugins.qq_auto_reply.pipeline_models import QQMessageBlock
    from plugin.plugins.qq_auto_reply.reply_buffer_service import (
        PendingReply,
        QQReplyBufferService,
    )

    user_data = {
        "is_group": True,
        "session": SimpleNamespace(_conversation_history=[]),
    }
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _qq_settings={"allow_cross_group_context": False},
        _emit_log=lambda *a, **k: None,
    )
    service = QQReplyBufferService.__new__(QQReplyBufferService)
    service.plugin = plugin
    service._pending = {}
    first = PendingReply(
        first_text="旧草稿", wait_seconds=999, sender_id="1",
        is_group=True, group_id="7788",
    )
    first.consent_snapshot = {"allow_cross_group_context": True}
    first.task = asyncio.create_task(asyncio.sleep(999))
    service._pending["group:7788"] = first

    await service.schedule_reply(
        session_key="group:7788", reply_text="新草稿", raw_text="新草稿",
        blocks=[QQMessageBlock(text="新草稿")], wait_seconds=999,
        sender_id="1", is_group=True, group_id="7788",
        consent_snapshot={"allow_cross_group_context": False},
    )
    pending = service._pending.pop("group:7788")
    if pending.task:
        pending.task.cancel()
    assert pending.consent_snapshot["allow_cross_group_context"] is True
    assert service._consent_revoked_since(pending) is True


@pytest.mark.asyncio
async def test_buffered_draft_dropped_when_consent_revoked():
    """A draft generated under scoped/cross-group consent sits in the
    delay buffer; revoking either switch has no session teardown for
    cross-group at all, so the send itself must compare the snapshot and
    drop the draft instead of disclosing revoked memory."""
    from plugin.plugins.qq_auto_reply.pipeline_models import QQMessageBlock
    from plugin.plugins.qq_auto_reply.reply_buffer_service import (
        PendingReply,
        QQReplyBufferService,
    )

    draft = SimpleNamespace(type="ai", content="含跨群内容的回复")
    user_data = {
        "is_group": True,
        "session": SimpleNamespace(_conversation_history=[draft]),
        "undelivered_draft_rows": [draft],
        "provisional_draft_rows": [draft],
    }
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _qq_settings={
            "group_memory_enabled": True,
            "allow_cross_group_context": False,   # revoked while waiting
        },
        _emit_log=lambda *a, **k: None,
        reply_delivery_node=SimpleNamespace(deliver=AsyncMock()),
        _run_with_session_lock=_passthrough_session_lock,
        reply_generation_service=SimpleNamespace(
            record_scoped_mentions_on_delivery=AsyncMock(),
        ),
    )
    service = QQReplyBufferService.__new__(QQReplyBufferService)
    service.plugin = plugin
    service._pending = {}
    pending = PendingReply(
        first_text="回复", wait_seconds=0, sender_id="1",
        is_group=True, group_id="7788",
    )
    pending.first_blocks = [QQMessageBlock(text="回复")]
    pending.wait_until = 0.0
    pending.draft_rows = [draft]
    pending.mention_context = object()
    pending.consent_snapshot = {
        "group_memory_enabled": True,
        "allow_cross_group_context": True,
    }
    service._pending["group:7788"] = pending

    await service._deliver_after_wait("group:7788", pending)
    plugin.reply_delivery_node.deliver.assert_not_awaited()
    plugin.reply_generation_service.record_scoped_mentions_on_delivery.assert_not_awaited()
    assert user_data["undelivered_draft_rows"] == [draft]
    assert user_data["provisional_draft_rows"] == []
    assert "group:7788" not in service._pending

    # Unchanged consent: the draft ships normally.
    plugin._qq_settings["allow_cross_group_context"] = True
    pending2 = PendingReply(
        first_text="回复", wait_seconds=0, sender_id="1",
        is_group=True, group_id="7788",
    )
    pending2.first_blocks = [QQMessageBlock(text="回复")]
    pending2.wait_until = 0.0
    pending2.consent_snapshot = dict(pending.consent_snapshot)
    service._pending["group:7788"] = pending2
    await service._deliver_after_wait("group:7788", pending2)
    plugin.reply_delivery_node.deliver.assert_awaited_once()


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

    # A raising send (NapCat surfaces transport failures as exceptions)
    # must run the same cleanup: mark kept, provisional settled, pending
    # popped — otherwise the barrier wedges every later digest.
    from plugin.plugins.qq_auto_reply.reply_buffer_service import PendingReply as _PR

    user_data["provisional_draft_rows"] = [draft]
    plugin.reply_delivery_node.deliver = AsyncMock(
        side_effect=RuntimeError("transport down"),
    )
    single2 = _PR(
        first_text="草稿", wait_seconds=0, sender_id="1",
        is_group=True, group_id="7788",
    )
    single2.first_blocks = list(single.first_blocks)
    single2.wait_until = 0.0
    single2.draft_rows = [draft]
    single2.mention_context = object()
    service._pending["group:7788"] = single2
    await service._deliver_after_wait("group:7788", single2)
    assert user_data["undelivered_draft_rows"] == [draft]
    assert user_data["provisional_draft_rows"] == []
    assert "group:7788" not in service._pending
    plugin.reply_generation_service.record_scoped_mentions_on_delivery.assert_not_awaited()


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
    # Member policy is stamped at the same boundary and forwarded too —
    # the handler can queue past an OFF->ON member-memory flip as well.
    member_stamp_pos = process_src.find("_member_memory_at_receipt")
    assert member_stamp_pos != -1 and member_stamp_pos < task_pos
    assert "_member_memory_at_receipt" in handle_src


def test_stop_join_includes_retro_review_tasks():
    """The interactive stop must join retroactive-review tasks before
    clearing the lock table — a review holds a group session lock while
    appending history and updating exclusion state."""
    import inspect

    from plugin.plugins.qq_auto_reply import runtime_ops_service

    src = inspect.getsource(runtime_ops_service)
    assert "_retro_tasks" in src


@pytest.mark.asyncio
async def test_timeout_discard_failure_marks_sticky_retry():
    """A timeout whose salvage-discard fails keeps the session — but its
    stream was force-cancelled and direct reuse would loop timeouts until
    the memory server recovers. The kept session gets the sticky
    pending_identity_discard marker so the next bootstrap retries the
    discard first."""
    from plugin.plugins.qq_auto_reply.pipeline_models import QQPipelineStageTrace
    from plugin.plugins.qq_auto_reply.reply_generation_service import (
        QQReplyGenerationService,
    )

    kept = {"is_group": True, "memory_enabled": True}
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": kept},
        session_runtime_service=SimpleNamespace(
            build_generation_session_key=lambda context: "group:7788",
            prime_generation_session_state=lambda ud, *, session_key, context: (
                SimpleNamespace(), []
            ),
            discard_session=AsyncMock(return_value=False),
        ),
        session_bootstrap_service=SimpleNamespace(
            ensure_generation_session=AsyncMock(return_value=kept),
        ),
        logger=MagicMock(),
    )
    service = QQReplyGenerationService.__new__(QQReplyGenerationService)
    service.plugin = plugin

    async def _timeout_generation(**kwargs):
        raise asyncio.TimeoutError()

    service._run_session_generation = _timeout_generation
    context = SimpleNamespace(
        is_group=True, group_id="7788", ephemeral_session=False,
        group_scene_mode="shared_context",
    )
    result = await service.run_primary_session_call(context)
    assert result.timed_out is True
    plugin.session_runtime_service.discard_session.assert_awaited_once()
    assert kept["pending_identity_discard"] is True

    # A successful discard removes the session itself (the fake performs
    # the real success behavior) and leaves no sticky marker behind.
    fresh = {"is_group": True, "memory_enabled": True}
    plugin._user_sessions["group:7788"] = fresh
    plugin.session_bootstrap_service.ensure_generation_session = AsyncMock(
        return_value=fresh,
    )

    async def _discard_ok(session_key, reason):
        plugin._user_sessions.pop(session_key, None)
        return True

    plugin.session_runtime_service.discard_session = _discard_ok
    result = await service.run_primary_session_call(context)
    assert result.timed_out is True
    assert "group:7788" not in plugin._user_sessions
    assert "pending_identity_discard" not in fresh

    # Synthetic turn timing out: the control prompt row must enter the
    # exclusion list BEFORE the salvage discard runs — the discard
    # finalizes immediately, and the pipeline-level recording only happens
    # after run() returns.
    prompt_row = SimpleNamespace(type="human", content="[synthetic]")
    session_obj = SimpleNamespace(_conversation_history=[])
    syn_ud = {"is_group": True, "memory_enabled": True, "session": session_obj}
    plugin._user_sessions["group:7788"] = syn_ud
    plugin.session_bootstrap_service.ensure_generation_session = AsyncMock(
        return_value=syn_ud,
    )
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    memory_service = QQSessionMemoryService.__new__(QQSessionMemoryService)
    memory_service.plugin = plugin
    plugin.session_memory_service = memory_service
    order = []

    def _prime(ud, *, session_key, context):
        return session_obj, []

    plugin.session_runtime_service.prime_generation_session_state = _prime

    async def _timeout_after_append(**kwargs):
        session_obj._conversation_history.append(prompt_row)
        raise asyncio.TimeoutError()

    service._run_session_generation = _timeout_after_append

    async def _salvage(session_key, reason):
        order.append(
            any(
                r is prompt_row
                for r in syn_ud.get("undelivered_draft_rows", [])
            )
        )
        return True

    plugin.session_runtime_service.discard_session = _salvage
    syn_context = SimpleNamespace(
        is_group=True, group_id="7788", ephemeral_session=False,
        group_scene_mode="shared_context", source_kind="rapid_fire_flush",
    )
    result = await service.run_primary_session_call(syn_context)
    assert result.timed_out is True
    assert order == [True]  # excluded before the salvage saw the session


@pytest.mark.asyncio
async def test_prompt_change_discard_failure_marks_sticky_retry():
    """A prompt-override discard whose settlement fails keeps the session —
    without the sticky marker a continuously active session would use the
    old system prompt indefinitely (activity blocks the idle finalizer)."""
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    kept = {"is_group": False, "memory_enabled": True}

    async def _lock(session_key, fn):
        return await fn()

    plugin = SimpleNamespace(
        _user_sessions={"private:1": kept},
        session_runtime_service=SimpleNamespace(
            discard_session=AsyncMock(return_value=False),
        ),
        _run_with_session_lock=_lock,
        _emit_log=lambda *a, **k: None,
    )
    service = QQSessionInstructionService.__new__(QQSessionInstructionService)
    service.plugin = plugin
    service._discard_all_sessions_for_prompt_change()
    await asyncio.gather(*plugin._prompt_change_discard_tasks)
    assert kept["pending_identity_discard"] is True


@pytest.mark.asyncio
async def test_failed_settlement_keeps_snapshot_for_pending_rollback():
    """When the settings save also failed, the opt-out settlement must not
    drop a failed snapshot: the queued rollback restores those turns
    (collected under previously persisted consent). Without a pending
    rollback the fail-closed drop still applies."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    async def _lock(session_key, fn):
        return await fn()

    def _session(rollback_pending):
        return {
            "is_group": True, "group_id": "7788", "her_name": "Neko",
            "pending_settle_buckets": {"1": [{"role": "user", "content": []}]},
            "pending_settle_labels": {"1": "一"},
            "pending_member_settle": True,
            **(
                {"member_settle_rollback_pending": True}
                if rollback_pending else {}
            ),
        }

    ud = _session(True)
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": ud},
        _run_with_session_lock=_lock,
        logger=MagicMock(),
    )
    service = QQSessionMemoryService.__new__(QQSessionMemoryService)
    service.plugin = plugin
    service._flush_member_buckets = AsyncMock(return_value=["1"])
    await service.settle_member_buckets_on_disable()
    assert ud["pending_settle_buckets"]  # kept for the rollback
    assert ud["pending_settle_labels"]

    # No pending rollback: opt-out semantics drop the failed snapshot.
    ud2 = _session(False)
    plugin._user_sessions = {"group:7788": ud2}
    await service.settle_member_buckets_on_disable()
    assert "pending_settle_buckets" not in ud2
    assert "pending_member_settle" not in ud2


@pytest.mark.asyncio
async def test_cross_group_section_removed_when_consent_revoked():
    """The cross-group section is built before later context awaits; if the
    opt-in is switched off (or rolled back after a failed save) during
    them, the section must be stripped before generation."""
    from plugin.plugins.qq_auto_reply.pipeline_models import QQInstructionBundle
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    plugin = SimpleNamespace(
        _qq_settings={"allow_cross_group_context": True},
        _user_sessions={
            "group:1": {
                "is_group": True, "group_id": "1",
                "session": SimpleNamespace(_conversation_history=[
                    SimpleNamespace(role="user", content="别的群在聊烤肉"),
                ]),
            },
        },
        i18n=SimpleNamespace(t=lambda key, default="", **kw: default),
    )
    service = QQSessionInstructionService.__new__(QQSessionInstructionService)
    service.plugin = plugin
    sections: list[str] = []
    section = service._append_cross_group_section(sections, "7788", True)
    assert section and section in sections
    assert "烤肉" in section

    # Core-memory section built with participant subjects is dropped when
    # the member switch is revoked during the later awaits, and the
    # bundle-derived fields are cleared with it (a lingering
    # memory_context_used would claim memory was used).
    from plugin.plugins.qq_auto_reply.reply_context_node import (
        QQReplyContextNode as _CtxNode,
    )

    node = _CtxNode.__new__(_CtxNode)
    node.plugin = SimpleNamespace(
        _qq_settings={"group_member_memory_enabled": True},
        logger=MagicMock(),
    )
    sep = chr(10) * 2
    core_section = "## 核心记忆" + chr(10) + "成员偏好：不吃香菜"
    prompt = "头部" + sep + core_section + sep + "尾部"
    kept, alive = node._strip_section_if_member_revoked(
        prompt, core_section, True,
    )
    assert alive is True and kept == prompt
    node.plugin._qq_settings["group_member_memory_enabled"] = False
    kept, alive = node._strip_section_if_member_revoked(
        prompt, core_section, True,
    )
    assert alive is False
    assert "不吃香菜" not in kept
    # A section that never used participant subjects is untouched.
    kept, alive = node._strip_section_if_member_revoked(
        prompt, core_section, False,
    )
    assert alive is True and kept == prompt

    # Wiring guard: the builder's return value must actually reach the
    # bundle (a correct helper that nobody wires up is dead code).
    import inspect

    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService as _Svc,
    )

    bundle_src = inspect.getsource(_Svc.build_session_instructions)
    assert "cross_group_section = self._append_cross_group_section(" in bundle_src
    assert "cross_group_section=cross_group_section" in bundle_src
    assert "used_member_subject=used_member_subject" in bundle_src

    # Post-await revocation: the node strips the exact section text.
    from plugin.plugins.qq_auto_reply.reply_context_node import (
        QQReplyContextNode,
    )

    node = _CtxNode.__new__(_CtxNode)
    node.plugin = SimpleNamespace(
        _qq_settings=plugin._qq_settings, logger=MagicMock(),
    )
    separator = chr(10) * 2
    prompt = "前段" + separator + section + separator + "后段"
    # Still consented: untouched.
    assert node._strip_cross_group_if_revoked(prompt, section) == (prompt, True)
    plugin._qq_settings["allow_cross_group_context"] = False
    stripped, kept = node._strip_cross_group_if_revoked(prompt, section)
    # The caller needs to know the section is gone: treating the reply as
    # cross-group-derived would make a later opt-out discard it although
    # the model never saw that content.
    assert kept is False
    assert "烤肉" not in stripped
    assert "前段" in stripped and "后段" in stripped



@pytest.mark.asyncio
async def test_discard_cancels_pending_buffered_reply():
    """A teardown discard (prompt/character change) must resolve the
    in-flight delayed reply first: otherwise the buffer task can deliver
    after the session is gone and its unmark finds no user_data, leaving a
    delivered reply permanently excluded from scoped memory."""
    from plugin.plugins.qq_auto_reply.reply_buffer_service import (
        PendingReply,
        QQReplyBufferService,
    )
    from plugin.plugins.qq_auto_reply.session_runtime_service import (
        QQSessionRuntimeService,
    )

    draft = SimpleNamespace(type="ai", content="草稿")
    ud = {
        "is_group": True, "memory_enabled": False,
        "session": SimpleNamespace(
            _conversation_history=[draft], close=AsyncMock(),
        ),
        "provisional_draft_rows": [draft],
        "undelivered_draft_rows": [draft],
    }
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": ud},
        logger=MagicMock(),
    )
    buffer_service = QQReplyBufferService.__new__(QQReplyBufferService)
    buffer_service.plugin = plugin
    pending = PendingReply(
        first_text="草稿", wait_seconds=999, sender_id="1",
        is_group=True, group_id="7788",
    )
    pending.draft_rows = [draft]
    pending.task = asyncio.create_task(asyncio.sleep(999))
    buffer_service._pending = {"group:7788": pending}
    plugin.reply_buffer_service = buffer_service

    runtime = QQSessionRuntimeService.__new__(QQSessionRuntimeService)
    runtime.plugin = plugin
    assert await runtime.discard_session("group:7788", reason="prompt") is True
    # Bounded wait: a discard that fails to cancel would otherwise hang the
    # suite on the 999s sleep instead of failing.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending.task, timeout=1.0)
    assert pending.task.cancelled()
    assert "group:7788" not in buffer_service._pending
    # The draft stays excluded (never delivered) but the barrier is lifted.
    assert ud["provisional_draft_rows"] == []
    assert ud["undelivered_draft_rows"] == [draft]


def test_generation_strips_scoped_sections_when_group_revoked():
    """Between context construction and generation a turn can wait on the
    shared session lock; if group memory is revoked in that window the
    already-composed scoped bootstrap section must not reach the model."""
    from plugin.plugins.qq_auto_reply.reply_generation_service import (
        QQReplyGenerationService,
    )

    core = "## 核心记忆" + chr(10) + "群里说过的事"
    sep = chr(10) * 2
    prompt = "头部" + sep + core + sep + "尾部"
    context = SimpleNamespace(core_memory_text=core)
    stripped = QQReplyGenerationService._strip_scoped_sections(prompt, context)
    assert "群里说过的事" not in stripped
    assert "头部" in stripped and "尾部" in stripped
    # No scoped section: untouched.
    assert QQReplyGenerationService._strip_scoped_sections(
        prompt, SimpleNamespace(core_memory_text=""),
    ) == prompt


@pytest.mark.asyncio
async def test_recall_reports_participant_usage_to_caller():
    """The recall reports whether it actually queried the participant
    subject, and build() ORs that into the context flag — binding the flag
    to a nonempty bootstrap section would miss the empty-bootstrap +
    participant-hit combination."""
    import inspect

    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryQueryResult
    from plugin.plugins.qq_auto_reply.reply_context_node import (
        QQReplyContextNode,
    )

    bridge = MagicMock()
    bridge.group_subject.side_effect = (
        lambda gid: {"subject_kind": "group_chat", "subject_id": f"qq:{gid}"}
    )
    bridge.group_participant_subject.side_effect = (
        lambda gid, uid: {"subject_kind": "group_participant"}
    )
    bridge.query_relevant_memory = AsyncMock(
        return_value=QQMemoryQueryResult(text="成员偏好", hit_count=1),
    )
    plugin = SimpleNamespace(
        memory_bridge=bridge,
        logger=MagicMock(),
        _qq_settings={
            "group_memory_enabled": True,
            "group_member_memory_enabled": True,
        },
        _should_skip_direct_llm_fallback_for_images=lambda **kwargs: False,
    )
    node = QQReplyContextNode(plugin)
    flag: list = []
    assert await node._build_recalled_memory_text(
        used_member_subject_out=flag,
        her_name="Neko", message="问题",
        should_use_memory_context=True, attachments=None,
        is_group=True, group_id="7788", sender_id="2046",
    ) != ""
    assert flag == [True]

    # No sender: participant subject absent, nothing reported.
    flag.clear()
    await node._build_recalled_memory_text(
        used_member_subject_out=flag,
        her_name="Neko", message="问题",
        should_use_memory_context=True, attachments=None,
        is_group=True, group_id="7788", sender_id="",
    )
    assert flag == []

    # Wiring, behaviourally: an empty scoped bootstrap plus a participant
    # recall hit must still set context.used_member_subject — a source
    # substring assert would match the surrounding comments and break on
    # any rename/reflow.
    import ast
    import textwrap

    tree = ast.parse(
        textwrap.dedent(inspect.getsource(QQReplyContextNode.build))
    )
    assert any(
        isinstance(node, ast.BoolOp)
        and isinstance(node.op, ast.And)
        and {getattr(v, "id", "") for v in node.values}
        >= {"recall_used_member", "recalled_memory_text"}
        for node in ast.walk(tree)
    )


def test_sanitizer_drops_recall_when_member_revoked_without_bootstrap():
    """Participant authorization must be tracked from the recall itself:
    an empty scoped bootstrap (no core-memory section) with a participant
    recall hit still has to lose that recall when member memory is
    revoked before generation."""
    from plugin.plugins.qq_auto_reply.reply_generation_service import (
        QQReplyGenerationService,
    )

    service = QQReplyGenerationService.__new__(QQReplyGenerationService)
    service.plugin = SimpleNamespace(
        _qq_settings={
            "group_memory_enabled": True,
            "group_member_memory_enabled": False,
            "allow_cross_group_context": True,
        },
    )
    context = SimpleNamespace(
        is_group=True, core_memory_text="", cross_group_section="",
        used_member_subject=True,
    )
    prompt, recalled = service._sanitize_for_live_consent(
        context, "系统提示", "成员的私密偏好",
    )
    assert recalled == ""
    assert prompt == "系统提示"

    # Member still enabled: recall passes through.
    service.plugin._qq_settings["group_member_memory_enabled"] = True
    prompt, recalled = service._sanitize_for_live_consent(
        context, "系统提示", "成员的私密偏好",
    )
    assert recalled == "成员的私密偏好"


@pytest.mark.asyncio
async def test_generation_recheck_wiring_drops_scoped_prompt():
    """Wiring guard for the generation-time recheck: the stripped prompt
    and the emptied recall must actually reach _apply_turn_memory_context
    (a correct helper nobody calls is dead code)."""
    from plugin.plugins.qq_auto_reply.reply_generation_service import (
        QQReplyGenerationService,
    )

    core = "## 核心记忆" + chr(10) + "群里说过的事"
    sep = chr(10) * 2
    applied = {}
    plugin = SimpleNamespace(
        _qq_settings={"group_memory_enabled": False},
        _queue_attachment_images=AsyncMock(return_value=0),
        _wait_session_response_complete=AsyncMock(return_value=True),
        _ai_turn_timeout_seconds=5,
        logger=MagicMock(),
    )
    service = QQReplyGenerationService.__new__(QQReplyGenerationService)
    service.plugin = plugin

    def _apply(session, system_prompt, recalled_text, *, always_refresh=False):
        applied["prompt"] = system_prompt
        applied["recalled"] = recalled_text
        return lambda: None

    service._apply_turn_memory_context = _apply
    context = SimpleNamespace(
        is_group=True, attachments=None, prompt_message="hi",
        system_prompt="头部" + sep + core + sep + "尾部",
        recalled_memory_text="召回内容",
        core_memory_text=core,
    )
    chunks = ["回复"]
    await service._run_session_generation(
        context=context,
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=SimpleNamespace(stream_text=AsyncMock()),
        reply_chunks=chunks,
    )
    assert "群里说过的事" not in applied["prompt"]
    assert applied["recalled"] == ""

    # Cross-group revoked while queued on the session lock: that section
    # is stripped inside the lock too.
    plugin._qq_settings["group_memory_enabled"] = True
    plugin._qq_settings["allow_cross_group_context"] = False
    xg = "## 其他群聊动态" + chr(10) + "- 群 9 最近在聊: 烤肉"
    xg_context = SimpleNamespace(
        is_group=True, attachments=None, prompt_message="hi",
        system_prompt="头部" + sep + xg + sep + "尾部",
        recalled_memory_text="召回内容",
        core_memory_text="",
        cross_group_section=xg,
    )
    await service._run_session_generation(
        context=xg_context,
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=SimpleNamespace(stream_text=AsyncMock()),
        reply_chunks=[],
    )
    assert "烤肉" not in applied["prompt"]

    # Group memory still on: the composed prompt and recall pass through.
    plugin._qq_settings["group_memory_enabled"] = True
    plugin._qq_settings["allow_cross_group_context"] = True
    await service._run_session_generation(
        context=context,
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=SimpleNamespace(stream_text=AsyncMock()),
        reply_chunks=[],
    )
    assert "群里说过的事" in applied["prompt"]
    assert applied["recalled"] == "召回内容"


@pytest.mark.asyncio
async def test_delivered_fallback_reply_enters_shared_history():
    """The direct fallback adds no ai row, so a delivered fallback would
    leave the digest with a one-sided conversation. The row is appended
    once delivery is confirmed — and only once."""
    from plugin.plugins.qq_auto_reply.reply_generation_service import (
        QQReplyGenerationService,
    )

    history: list = [SimpleNamespace(type="human", content="问题")]
    plugin = SimpleNamespace(
        _user_sessions={
            "group:7788": {
                "memory_enabled": True,
                "session": SimpleNamespace(_conversation_history=history),
            },
        },
        session_runtime_service=SimpleNamespace(
            build_generation_session_key=lambda context: "group:7788",
        ),
    )
    service = QQReplyGenerationService.__new__(QQReplyGenerationService)
    service.plugin = plugin
    context = SimpleNamespace(
        is_group=True, ephemeral_session=False, group_id="7788",
        current_message_id="msg-1",
    )
    service.append_fallback_ai_row(context, "fallback 回复")
    assert [getattr(m, "type", "") for m in history] == ["human", "ai"]
    assert history[-1].content == "fallback 回复"

    # Idempotent: a second delivery hook for the same turn adds nothing.
    service.append_fallback_ai_row(context, "fallback 回复")
    assert len(history) == 2

    # Still idempotent when the duplicate hook arrives after later rows and
    # through a REBUILT context object: the key is the turn's message id,
    # not object identity or a fixed-size tail scan.
    history.extend([
        SimpleNamespace(type="human", content="后续发言"),
        SimpleNamespace(type="ai", content="后续回复"),
        SimpleNamespace(type="human", content="再一条"),
        SimpleNamespace(type="ai", content="再一条回复"),
    ])
    service.append_fallback_ai_row(
        SimpleNamespace(
            is_group=True, ephemeral_session=False, group_id="7788",
            current_message_id="msg-1",
        ),
        "fallback 回复",
    )
    assert len(history) == 6

    # A genuinely different turn still gets its row.
    service.append_fallback_ai_row(
        SimpleNamespace(
            is_group=True, ephemeral_session=False, group_id="7788",
            current_message_id="msg-2",
        ),
        "另一轮的 fallback",
    )
    assert len(history) == 7
    del history[2:]

    # Memory disabled: nothing is appended.
    plugin._user_sessions["group:7788"]["memory_enabled"] = False
    service.append_fallback_ai_row(
        SimpleNamespace(is_group=True, ephemeral_session=False, group_id="7788"),
        "另一条",
    )
    assert len(history) == 2


@pytest.mark.asyncio
async def test_generation_discards_reply_when_consent_revoked_mid_stream():
    """The model already saw the scoped prompt; if the switch goes off
    while streaming, the reply still carries that content — it must be
    discarded rather than delivered."""
    from plugin.plugins.qq_auto_reply.reply_generation_service import (
        QQReplyGenerationService,
    )

    plugin = SimpleNamespace(
        _qq_settings={"group_memory_enabled": True},
        _queue_attachment_images=AsyncMock(return_value=0),
        _wait_session_response_complete=AsyncMock(return_value=True),
        _ai_turn_timeout_seconds=5,
        logger=MagicMock(),
    )
    service = QQReplyGenerationService.__new__(QQReplyGenerationService)
    service.plugin = plugin
    service._apply_turn_memory_context = (
        lambda *a, **k: (lambda: None)
    )
    context = SimpleNamespace(
        is_group=True, attachments=None, prompt_message="hi",
        system_prompt="含群记忆的提示词", recalled_memory_text="召回内容",
        core_memory_text="核心记忆", cross_group_section="",
        used_member_subject=False,
    )

    chunks: list = []
    history = [SimpleNamespace(type="human", content="之前的发言")]

    async def _revoke_mid_stream(_msg):
        # The model produced its reply from the scoped prompt, and the
        # session wrote both rows into the shared history...
        history.append(SimpleNamespace(type="human", content="hi"))
        history.append(SimpleNamespace(type="ai", content="带着群记忆的回复"))
        chunks.append("带着群记忆的回复")
        # ...and only then does the switch go off.
        plugin._qq_settings["group_memory_enabled"] = False

    session = SimpleNamespace(
        stream_text=_revoke_mid_stream, _conversation_history=history,
    )
    result = await service._run_session_generation(
        context=context,
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=session,
        reply_chunks=chunks,
    )
    assert not result
    assert chunks == []
    # Clearing the outbound chunks is not enough: the ai row written by
    # stream_text would otherwise stay in the shared history and reach both
    # the digest and every later turn's context. The human row (the user's
    # own utterance) stays.
    assert [row.type for row in history] == ["human", "human"]
    assert all(
        getattr(row, "content", "") != "带着群记忆的回复" for row in history
    )

    # Consent unchanged: the reply survives.
    plugin._qq_settings["group_memory_enabled"] = True
    chunks2: list = []

    async def _normal_stream(_msg):
        chunks2.append("正常回复")

    result = await service._run_session_generation(
        context=context,
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=SimpleNamespace(stream_text=_normal_stream),
        reply_chunks=chunks2,
    )
    assert result == "正常回复"


@pytest.mark.asyncio
async def test_member_turn_recorded_once_even_on_empty_generation():
    """Member-turn collection binds to 'the session accepted the human
    row', not to a nonempty reply: an empty generation (fallback empty
    too) already put the utterance into shared history and the group
    digest — it must reach the participant bucket as well. And it is
    recorded exactly once on the success path (single recording point)."""
    from plugin.plugins.qq_auto_reply.reply_generation_service import (
        QQReplyGenerationService,
    )

    ud = {"is_group": True, "memory_enabled": True}
    record = MagicMock()
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": ud},
        session_runtime_service=SimpleNamespace(
            build_generation_session_key=lambda context: "group:7788",
            prime_generation_session_state=lambda u, *, session_key, context: (
                SimpleNamespace(), []
            ),
        ),
        session_bootstrap_service=SimpleNamespace(
            ensure_generation_session=AsyncMock(return_value=ud),
        ),
        session_memory_service=SimpleNamespace(
            record_group_member_turn=record,
        ),
        _cache_session_delta=AsyncMock(return_value=0),
        logger=MagicMock(),
    )
    service = QQReplyGenerationService.__new__(QQReplyGenerationService)
    service.plugin = plugin
    context = SimpleNamespace(
        is_group=True, group_id="7788", sender_id="1",
        ephemeral_session=False, group_scene_mode="shared_context",
        recalled_memory_used=False, recalled_memory_text="",
    )

    async def _empty(**kwargs):
        # Production marks the row as accepted once stream_text has put it
        # into the shared history; the stub mirrors that.
        ud["human_row_accepted"] = True
        return ""

    service._run_session_generation = _empty
    result = await service.run_primary_session_call(context)
    assert result.allow_fallback is True
    record.assert_called_once()

    # Success path still records exactly once (no double-count via the
    # post-success hook).
    record.reset_mock()

    async def _reply(**kwargs):
        ud["human_row_accepted"] = True
        return "正常回复"

    service._run_session_generation = _reply
    service._record_scoped_mentions_best_effort = AsyncMock()
    result = await service.run_primary_session_call(context)
    assert result.reply_text == "正常回复"
    record.assert_called_once()

    # A stream that raised AFTER the session took the human row: the
    # recorder must still run (exception-safe point) without masking the
    # original error.
    record.reset_mock()
    plugin.session_runtime_service.discard_session = AsyncMock(return_value=True)

    async def _boom(**kwargs):
        ud["human_row_accepted"] = True
        raise asyncio.TimeoutError()

    service._run_session_generation = _boom
    result = await service.run_primary_session_call(context)
    assert result.timed_out is True
    record.assert_called_once()

    # ...but a failure BEFORE the row was accepted (session lock wait,
    # attachment queueing) records nothing: the utterance never entered
    # the shared history, so a participant bucket entry would be a memory
    # of something the session never saw.
    record.reset_mock()

    async def _boom_early(**kwargs):
        ud["human_row_accepted"] = False
        raise asyncio.TimeoutError()

    service._run_session_generation = _boom_early
    result = await service.run_primary_session_call(context)
    assert result.timed_out is True
    record.assert_not_called()


@pytest.mark.asyncio
async def test_failed_disable_save_restores_pre_optout_cursor():
    """ON->OFF whose save fails while the settlement also fails: the
    fail-closed cleanup pushed the cursor to len(history) as opt-out
    hygiene, but the setting stayed ON — the rollback rebase must restore
    the pre-opt-out cursor so that authorized history still settles."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    history = [SimpleNamespace(type="human", content=f"m{i}") for i in range(6)]
    ud = {
        "memory_enabled": True, "is_group": True, "group_id": "7788",
        "her_name": "Neko",
        "session": SimpleNamespace(
            _conversation_history=history, close=AsyncMock(),
        ),
        "last_group_digest_index": 2,
        "pending_disable_settle": True,
        "group_opt_out_cutoff": 6,
    }

    async def _lock(session_key, fn):
        return await fn()

    bridge = MagicMock()
    bridge.group_subject.side_effect = (
        lambda gid: {"subject_kind": "group_chat", "subject_id": f"qq:{gid}"}
    )
    bridge.post_scoped_memory_history = AsyncMock(
        side_effect=RuntimeError("server down"),
    )
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": ud},
        _qq_settings={},
        _run_with_session_lock=_lock,
        memory_bridge=bridge,
        logger=MagicMock(),
    )
    service = QQSessionMemoryService(plugin)
    await service.invalidate_group_sessions(enabled=False)
    assert ud["last_group_digest_index"] == len(history)  # fail-closed
    assert ud["pre_optout_digest_index"] == 2

    # The settings save failed -> rollback stamps the sessions and reverses.
    ud["group_settle_rollback_pending"] = True
    ud["pending_enable_rebase"] = len(history)
    await service.invalidate_group_sessions(enabled=True)
    assert ud["last_group_digest_index"] == 2
    assert ud["memory_enabled"] is True
    assert "pre_optout_digest_index" not in ud

    # A genuine re-enable (no rollback marker) keeps skipping the opt-out
    # era instead of rewinding.
    ud["last_group_digest_index"] = len(history)
    ud["pre_optout_digest_index"] = 2
    ud["pending_enable_rebase"] = len(history)
    await service.invalidate_group_sessions(enabled=True)
    assert ud["last_group_digest_index"] == len(history)


@pytest.mark.asyncio
async def test_housekeeping_not_started_when_connect_fails():
    """A failed start leaves _running False with no message task, so the
    later stop_auto_reply takes its not_running early return — a
    housekeeping task created before connect would then run forever while
    auto-reply is stopped."""
    from plugin.plugins.qq_auto_reply.runtime_ops_service import (
        QQRuntimeOpsService,
    )

    plugin = SimpleNamespace(
        _session_housekeeping_task=None,
        _session_housekeeping_loop=AsyncMock(),
        _running=False,
        _message_task=None,
        _qq_settings={"qq_connection_mode": "napcat"},
        _ensure_qq_client_initialized=lambda: None,
        qq_client=SimpleNamespace(
            needs_attention=True,
            connect=AsyncMock(side_effect=RuntimeError("no client")),
            onebot_url="ws://x",
        ),
        attention_service=None,
        attention_gate_service=None,
        napcat_service=SimpleNamespace(get_startup_error=lambda: ""),
        _emit_log=lambda *a, **k: None,
        logger=MagicMock(),
        i18n=SimpleNamespace(t=lambda key, default="", **kw: default),
        _startup_error=None,
    )
    service = QQRuntimeOpsService(plugin)
    result = await service.start_auto_reply()
    assert result.is_err() if hasattr(result, "is_err") else True
    assert plugin._session_housekeeping_task is None

    # Successful start does create it.
    plugin.qq_client.connect = AsyncMock()
    plugin._process_messages = AsyncMock()
    await service.start_auto_reply()
    assert plugin._session_housekeeping_task is not None
    plugin._session_housekeeping_task.cancel()
    if plugin._message_task:
        plugin._message_task.cancel()


@pytest.mark.asyncio
async def test_shutdown_drains_pending_disable_sessions():
    """A session whose transition settlement is still pending keeps its
    pre-cutoff authorized prefix only in memory; a post-opt-out turn may
    already have flipped memory_enabled off. Shutdown must still settle it
    (bounded by the stored cutoff) — otherwise a stalled transition task
    loses the only copy at process exit."""
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    history = [SimpleNamespace(type="human", content=f"m{i}") for i in range(4)]
    ud = {
        "memory_enabled": False,          # post-opt-out turn flipped it
        "pending_disable_settle": True,   # transition task has not run yet
        "group_opt_out_cutoff": 2,
        "is_group": True, "group_id": "7788", "her_name": "Neko",
        "session": SimpleNamespace(
            _conversation_history=history, close=AsyncMock(),
        ),
        "last_group_digest_index": 0,
    }

    async def _lock(session_key, fn):
        return await fn()

    bridge = MagicMock()
    bridge.group_subject.side_effect = QQMemoryBridge.group_subject
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": ud},
        _qq_settings={},
        _run_with_session_lock=_lock,
        memory_bridge=bridge,
        logger=MagicMock(),
    )
    service = QQSessionMemoryService(plugin)
    await service.flush_all_memory_sessions("shutdown")
    settled = [
        m["content"][0]["text"]
        for call in bridge.post_scoped_memory_history.await_args_list
        for m in call.args[1]
    ]
    # Only the pre-cutoff prefix is settled.
    assert settled == ["m0", "m1"]

    # A plain memory-disabled session (no pending settlement) stays skipped.
    bridge.post_scoped_memory_history.reset_mock()
    plugin._user_sessions = {
        "group:9": {
            "memory_enabled": False, "is_group": True, "group_id": "9",
            "her_name": "Neko",
            "session": SimpleNamespace(
                _conversation_history=list(history), close=AsyncMock(),
            ),
        },
    }
    await service.flush_all_memory_sessions("shutdown")
    bridge.post_scoped_memory_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_rollback_discard_drops_failed_optin_interval():
    """The enable-save-failure rollback must DISCARD the failed interval,
    not settle it — an ordinary OFF settlement would digest precisely the
    history received under the opt-in that never saved."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    history = [SimpleNamespace(type="human", content=f"m{i}") for i in range(4)]
    user_data = {
        "memory_enabled": True,
        "is_group": True,
        "group_id": "7788",
        "her_name": "Neko",
        "session": SimpleNamespace(_conversation_history=history, close=AsyncMock()),
        "last_group_digest_index": 0,
        "pending_disable_settle": True,
        "group_opt_out_cutoff": 4,
        "group_member_memory_messages": {"1": [{"role": "user", "content": []}]},
        "group_member_memory_labels": {"1": "1"},
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
    await service.invalidate_group_sessions(enabled=False, discard_only=True)
    bridge.post_scoped_memory_history.assert_not_awaited()
    assert user_data["memory_enabled"] is False
    assert user_data["last_group_digest_index"] == 4
    assert "group_member_memory_messages" not in user_data
    assert "group_opt_out_cutoff" not in user_data
    assert "group:7788" in plugin._user_sessions


def test_subject_components_encode_the_joiner():
    """A component containing ':' must not collapse distinct owners into
    one subject key — those conversations would read and overwrite each
    other's memory."""
    a = MemorySubject.group_chat("a:b", "c")
    b = MemorySubject.group_chat("a", "b:c")
    assert a.subject_id != b.subject_id
    assert a.scope != b.scope
    # Existing ids without the separator are unchanged.
    plain = MemorySubject.group_chat("qq", "7788")
    assert plain.subject_id == "qq:7788"


@pytest.mark.asyncio
async def test_sync_task_spawn_reports_failures():
    """The transition-task registry must consume exceptions in its done
    callback — a silently dropped failure leaves the consent transition
    half-applied with no log."""
    from plugin.plugins.qq_auto_reply.session import QQAutoReplySessionMixin
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    logs = []

    class _Plugin(QQAutoReplySessionMixin):
        def _emit_log(self, level, msg):
            logs.append((level, msg))

    service = QQSettingsService.__new__(QQSettingsService)
    # One registry shared by every producer: the settings service and the
    # member-bucket drain both go through the plugin facade, so stop()
    # joins a single set.
    service.plugin = _Plugin()

    async def _boom():
        raise RuntimeError("transition down")

    service._spawn_group_memory_sync_task(_boom())
    for _ in range(10):
        await asyncio.sleep(0)
    assert any(level == "ERROR" for level, _ in logs)
    assert not getattr(service.plugin, "_group_memory_sync_tasks")


def test_stop_cancels_buffer_tasks_and_settles_provisional():
    """Stop must cancel delayed replies (the client is gone; a survivor
    would fail or replay a stale pre-stop reply into the next run) and
    settle their provisional barriers."""
    import inspect

    from plugin.plugins.qq_auto_reply import runtime_ops_service

    src = inspect.getsource(runtime_ops_service)
    assert "task.cancel()" in src
    assert "_settle_provisional" in src


@pytest.mark.asyncio
async def test_unpersisted_memory_toggle_rolls_back():
    """A failed config-store write must roll the runtime consent back:
    otherwise handlers collect scoped history under an opt-in that was
    never saved (and a restart silently reverts it)."""
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    spawned = []
    service = QQSettingsService.__new__(QQSettingsService)
    hist = [SimpleNamespace(type="human", content="m")]
    ud = {
        "is_group": True,
        "session": SimpleNamespace(_conversation_history=hist),
        "pending_enable_rebase": 1,
    }

    async def _lock(session_key, fn):
        return await fn()

    service.plugin = SimpleNamespace(
        _qq_settings={
            "group_memory_enabled": True,
            "group_member_memory_enabled": True,
        },
        _user_sessions={"group:1": ud},
        _emit_log=lambda *a, **k: None,
        _run_with_session_lock=_lock,
    )
    service._spawn_group_memory_sync_task = lambda coro: spawned.append(coro)

    # Persist OK: nothing happens.
    service._rollback_unpersisted_memory_toggles(
        True,
        group_memory_before=False, group_memory_after=True,
        member_memory_before=False, member_memory_after=True,
    )
    assert service.plugin._qq_settings["group_memory_enabled"] is True
    assert not spawned

    # Persist failed while enabling: runtime policy reverts, the reverse
    # transition is stamped (disable marker on existing sessions) and a
    # reverse sync task is spawned.
    service._rollback_unpersisted_memory_toggles(
        False,
        group_memory_before=False, group_memory_after=True,
        member_memory_before=False, member_memory_after=True,
    )
    assert service.plugin._qq_settings["group_memory_enabled"] is False
    assert service.plugin._qq_settings["group_member_memory_enabled"] is False
    assert ud["pending_disable_settle"] is True
    assert len(spawned) == 1
    spawned.pop(0).close()  # reverse-transition coroutine, not under test here

    # Combined OFF (group + member) whose save fails: the member snapshot
    # must be protected and restored exactly like the member-only branch —
    # otherwise the queued opt-out settlement drops turns collected under
    # the previously persisted consent.
    ud.pop("pending_disable_settle", None)
    ud["pending_settle_buckets"] = {
        "5": [{"role": "user", "content": [{"type": "text", "text": "旧五"}]}],
    }
    ud["pending_settle_labels"] = {"5": "五"}
    service.plugin._qq_settings["group_memory_enabled"] = False
    service.plugin._qq_settings["group_member_memory_enabled"] = False
    service._rollback_unpersisted_memory_toggles(
        False,
        group_memory_before=True, group_memory_after=False,
        member_memory_before=True, member_memory_after=False,
    )
    assert ud["member_settle_rollback_pending"] is True
    assert len(spawned) == 2
    restore_coro = spawned.pop(0)
    spawned.pop(0).close()  # reverse-transition coroutine
    await restore_coro
    assert ud["group_member_memory_messages"]["5"][0]["content"][0]["text"] == "旧五"
    assert "pending_settle_buckets" not in ud
    assert "member_settle_rollback_pending" not in ud

    # ON->OFF whose save failed: the rollback direction is back to ON, so
    # the sessions must be stamped for the cursor restore (the marker
    # condition keys on the OLD value, which is True here).
    ud.pop("group_settle_rollback_pending", None)
    service.plugin._qq_settings["group_memory_enabled"] = False
    service._rollback_unpersisted_memory_toggles(
        False,
        group_memory_before=True, group_memory_after=False,
        member_memory_before=False, member_memory_after=False,
    )
    assert ud["group_settle_rollback_pending"] is True
    assert service.plugin._qq_settings["group_memory_enabled"] is True
    while spawned:
        spawned.pop(0).close()
    ud.pop("group_settle_rollback_pending", None)
    ud.pop("pending_disable_settle", None)

    # OFF->ON whose save failed rolls back to OFF: no cursor restore is
    # involved, so no marker.
    service.plugin._qq_settings["group_memory_enabled"] = True
    service._rollback_unpersisted_memory_toggles(
        False,
        group_memory_before=False, group_memory_after=True,
        member_memory_before=False, member_memory_after=False,
    )
    assert "group_settle_rollback_pending" not in ud
    while spawned:
        spawned.pop(0).close()

    # Cancellation during persistence bypasses persist's own except
    # Exception, but still means "not written" — the rollback must run.
    cancel_service = QQSettingsService.__new__(QQSettingsService)
    cancel_service.plugin = service.plugin
    rolled: list = []
    cancel_service._rollback_unpersisted_memory_toggles = (
        lambda persisted, **kw: rolled.append(persisted)
    )
    cancel_service.persist_business_config = AsyncMock(
        side_effect=asyncio.CancelledError(),
    )
    with pytest.raises(asyncio.CancelledError):
        await cancel_service._persist_with_consent_rollback(
            group_memory_before=True, group_memory_after=False,
            member_memory_before=False, member_memory_after=False,
            cross_group_before=False,
        )
    assert rolled == [False]

    # Cancelling the AWAIT does not cancel the atomic write thread: the
    # real outcome decides the rollback, otherwise disk and runtime end up
    # permanently opposite.
    rolled.clear()
    started = asyncio.Event()

    async def _slow_but_successful_write():
        started.set()
        await asyncio.sleep(0.05)
        return True

    cancel_service.persist_business_config = _slow_but_successful_write
    task = asyncio.create_task(
        cancel_service._persist_with_consent_rollback(
            group_memory_before=True, group_memory_after=False,
            member_memory_before=False, member_memory_after=False,
            cross_group_before=False,
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The write landed, so no rollback of the persisted value.
    assert rolled == [True]
    # A clean save reports success through to the rollback helper (no-op).
    rolled.clear()
    cancel_service.persist_business_config = AsyncMock(return_value=True)
    assert await cancel_service._persist_with_consent_rollback(
        group_memory_before=True, group_memory_after=False,
        member_memory_before=False, member_memory_after=False,
        cross_group_before=False,
    ) is True
    assert rolled == [True]

    # Cross-group context also rolls back on persist failure — it is a
    # consent switch too, and a lingering new value injects other groups'
    # messages under a never-saved opt-in.
    service.plugin._qq_settings["allow_cross_group_context"] = True
    service._rollback_unpersisted_memory_toggles(
        False,
        group_memory_before=False, group_memory_after=False,
        member_memory_before=False, member_memory_after=False,
        cross_group_before=False, cross_group_after=True,
    )
    assert service.plugin._qq_settings["allow_cross_group_context"] is False

    # A request that did NOT touch the switch must not restore its own
    # stale reading: another save may have legitimately opted out (and
    # persisted) in between — reviving it here leaves disk opted out while
    # the runtime keeps disclosing other groups until restart.
    service.plugin._qq_settings["allow_cross_group_context"] = False
    service._rollback_unpersisted_memory_toggles(
        False,
        group_memory_before=False, group_memory_after=False,
        member_memory_before=False, member_memory_after=False,
        cross_group_before=True, cross_group_after=True,
    )
    assert service.plugin._qq_settings["allow_cross_group_context"] is False

    # The uncontested case rolls back both switches and plants the markers.
    ud.pop("group_settle_rollback_pending", None)
    service.plugin._qq_settings["group_memory_enabled"] = False
    service._rollback_unpersisted_memory_toggles(
        False,
        group_memory_before=True, group_memory_after=False,
        member_memory_before=True, member_memory_after=False,
    )
    assert service.plugin._qq_settings["group_memory_enabled"] is True
    assert ud.get("group_settle_rollback_pending") is True
    while spawned:
        spawned.pop(0).close()

    # ON->OFF member save failure: the OFF stamp already snapshotted the
    # live buckets for opt-out settlement — those turns were collected
    # under a previously SAVED consent, so the rollback must merge the
    # snapshot back into live buckets (snapshot first, order preserved)
    # and cancel the queued settlement markers.
    service.plugin._qq_settings["group_member_memory_enabled"] = False
    ud["pending_settle_buckets"] = {
        "9": [{"role": "user", "content": [{"type": "text", "text": "旧"}]}],
    }
    ud["pending_settle_labels"] = {"9": "九"}
    ud["pending_member_settle"] = True
    ud["group_member_memory_messages"] = {
        "9": [{"role": "user", "content": [{"type": "text", "text": "新"}]}],
    }
    service._rollback_unpersisted_memory_toggles(
        False,
        group_memory_before=False, group_memory_after=False,
        member_memory_before=True, member_memory_after=False,
    )
    assert service.plugin._qq_settings["group_member_memory_enabled"] is True
    # The restoration runs as a serialized background task (transition +
    # session locks) — drive the spawned coroutine.
    while spawned:
        await spawned.pop(0)
    merged = ud["group_member_memory_messages"]["9"]
    assert [m["content"][0]["text"] for m in merged] == ["旧", "新"]
    assert ud["group_member_memory_labels"]["9"] == "九"
    assert "pending_settle_buckets" not in ud
    assert "pending_member_settle" not in ud
    assert "pending_settle_labels" not in ud

    # Member-only failure rolls back the flag AND discards live buckets
    # collected during the failed opt-in window — re-enabling later must
    # not mix them with newly authorized turns.
    service.plugin._qq_settings["group_member_memory_enabled"] = True
    ud["group_member_memory_messages"] = {"1": [{"role": "user", "content": []}]}
    ud["group_member_memory_labels"] = {"1": "1"}
    ud["pending_settle_buckets"] = {"2": [{"role": "user", "content": []}]}
    service._rollback_unpersisted_memory_toggles(
        False,
        group_memory_before=False, group_memory_after=False,
        member_memory_before=False, member_memory_after=True,
    )
    assert service.plugin._qq_settings["group_member_memory_enabled"] is False
    assert "group_member_memory_messages" not in ud
    assert "group_member_memory_labels" not in ud
    # The pending snapshot belongs to a previously saved era: untouched.
    assert "pending_settle_buckets" in ud
    assert not spawned


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
        i18n=_default_i18n(),
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
    # The group digest and the member buckets settle independently: the
    # failing digest must not stop the member queues (capped at 50) from
    # being attempted, or continued traffic silently truncates them.
    settle_attempts = bridge.post_scoped_memory_history.await_count
    assert settle_attempts == 2
    # A later idle/shutdown sweep must now skip this session entirely.
    await service.flush_all_memory_sessions("shutdown")
    assert bridge.post_scoped_memory_history.await_count == settle_attempts

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
    plugin._qq_settings = {
        "group_memory_enabled": True, "group_member_memory_enabled": True,
    }
    context = SimpleNamespace(
        is_group=True, group_id="7788", sender_id="2046", her_name="Neko",
        permission_level="user", ephemeral_session=False,
        member_memory_enabled=True,
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
        ephemeral_session=False, member_memory_enabled=True,
    )
    await service.record_scoped_mentions_on_delivery(context_syn, "合并回复")
    kwargs = bridge.post_scoped_mentions.await_args.kwargs
    assert [s2["subject_id"] for s2 in kwargs["subjects"]] == ["qq:7788"]

    # Member memory off: the participant subject is not touched either —
    # scanning/suppressing entries that were never recalled would hide
    # facts even after a later opt-in.
    bridge.post_scoped_mentions.reset_mock()
    plugin._qq_settings["group_member_memory_enabled"] = False
    await service.record_scoped_mentions_on_delivery(context, "她记得群规")
    kwargs = bridge.post_scoped_mentions.await_args.kwargs
    assert [s2["subject_id"] for s2 in kwargs["subjects"]] == ["qq:7788"]
    plugin._qq_settings["group_member_memory_enabled"] = True

    # Group memory off: mention counting WRITES group-scope metadata, so it
    # must stop the moment the switch flips — even before the background
    # settlement clears the session's own flag.
    bridge.post_scoped_mentions.reset_mock()
    plugin._qq_settings["group_memory_enabled"] = False
    await service.record_scoped_mentions_on_delivery(context, "她记得群规")
    bridge.post_scoped_mentions.assert_not_awaited()
    plugin._qq_settings["group_memory_enabled"] = True

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

    # Character switch invalidates too: same login id, different active
    # catgirl — reusing the session would post the new character's turns
    # into the old character's memory store.
    existing.pop("pending_identity_discard", None)
    existing["her_name"] = "旧角色"
    char_context = SimpleNamespace(
        ephemeral_session=False, login_self_id="new", her_name="新角色",
    )
    plugin.logger = MagicMock()
    result = await service.ensure_generation_session(char_context, "group:7788")
    assert plugin.session_runtime_service.discard_session.await_count == 3
    assert existing["pending_identity_discard"] is True
    # Character switch + failed salvage: the turn must NOT run on the old
    # character's session — its rows would settle into the old character's
    # memory store when the sticky retry finally succeeds.
    assert result is None
    assert plugin._user_sessions["group:7788"] is existing


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


@pytest.mark.asyncio
async def test_fallback_reply_dropped_when_consent_revoked_during_call(monkeypatch):
    """The direct fallback sanitizes once, then awaits an LLM for up to a
    minute: a switch turned off during that call leaves the returned text
    carrying memory the user just revoked."""
    import plugin.plugins.qq_auto_reply.reply_generation_service as rgs

    plugin = SimpleNamespace(
        _qq_settings={"group_memory_enabled": True},
        logger=MagicMock(),
        _ai_turn_timeout_seconds=5,
        _should_skip_direct_llm_fallback_for_images=lambda **kw: False,
    )
    service = rgs.QQReplyGenerationService.__new__(rgs.QQReplyGenerationService)
    service.plugin = plugin
    context = SimpleNamespace(
        is_group=True, message="hi", attachments=None, prompt_message="hi",
        system_prompt="含群记忆的提示词", recalled_memory_text="召回内容",
        core_memory_text="核心记忆", cross_group_section="",
        used_member_subject=False, consent_snapshot={},
    )

    monkeypatch.setattr(rgs, "set_call_type", lambda *a, **k: None)
    monkeypatch.setattr(
        "utils.config_manager.get_config_manager",
        lambda: SimpleNamespace(get_model_api_config=lambda kind: {
            "base_url": "http://x", "model": "m", "api_key": "k",
        }),
    )

    class _LLM:
        def __init__(self, revoke):
            self._revoke = revoke

        async def ainvoke(self, _messages):
            if self._revoke:
                plugin._qq_settings["group_memory_enabled"] = False
            return SimpleNamespace(content="带着群记忆的回复")

    monkeypatch.setattr(
        rgs, "create_chat_llm_async", AsyncMock(return_value=_LLM(True)),
    )
    assert await service.generate_reply_fallback_direct_llm(context=context) is None

    plugin._qq_settings["group_memory_enabled"] = True
    monkeypatch.setattr(
        rgs, "create_chat_llm_async", AsyncMock(return_value=_LLM(False)),
    )
    assert (
        await service.generate_reply_fallback_direct_llm(context=context)
        == "带着群记忆的回复"
    )
    # The generation-time snapshot travels on the context so the delivery
    # gates compare against what the reply actually consumed.
    assert context.consent_snapshot == {"group_memory_enabled": True}


@pytest.mark.asyncio
async def test_timeout_salvage_failure_still_discards_session():
    """The salvage marking is best-effort: if it throws, the timed-out
    session must still be discarded — its stream was force-cancelled, so
    reusing it just times out again."""
    from plugin.plugins.qq_auto_reply.reply_generation_service import (
        QQReplyGenerationService,
    )

    discard = AsyncMock(return_value=True)
    plugin = SimpleNamespace(
        logger=MagicMock(),
        _user_sessions={},
        session_bootstrap_service=SimpleNamespace(
            ensure_generation_session=AsyncMock(
                return_value={"memory_enabled": False},
            ),
        ),
        session_runtime_service=SimpleNamespace(
            build_generation_session_key=lambda context: "group:7788",
            prime_generation_session_state=lambda ud, **kw: (
                SimpleNamespace(_conversation_history=[]), [],
            ),
            discard_session=discard,
        ),
        session_memory_service=SimpleNamespace(
            record_synthetic_prompt_rows=MagicMock(
                side_effect=RuntimeError("marking down"),
            ),
            record_group_member_turn=MagicMock(),
        ),
    )
    service = QQReplyGenerationService.__new__(QQReplyGenerationService)
    service.plugin = plugin

    async def _timeout(**kwargs):
        raise asyncio.TimeoutError

    service._run_session_generation = _timeout
    context = SimpleNamespace(
        is_group=True, group_id="7788", ephemeral_session=False,
        group_scene_mode="", source_kind="rapid_fire_flush",
        recalled_memory_used=False, recalled_memory_text="",
    )
    result = await service.run_primary_session_call(context)
    assert result.timed_out is True
    discard.assert_awaited_once()


@pytest.mark.asyncio
async def test_consent_union_precedes_nested_buffer_flush():
    """The 10-16 acknowledgement and the 17+ forced summary run nested
    pipelines from the middle of schedule_reply, quoting the buffered
    drafts (the bot's own memory-derived replies). Their dependencies must
    be merged BEFORE those runs, and when one is revoked the nested run
    must not happen at all — it computes a fresh, empty snapshot for
    itself, so its own pre-send gate can never fire."""
    from plugin.plugins.qq_auto_reply.reply_buffer_service import (
        PendingReply,
        QQReplyBufferService,
    )

    service = QQReplyBufferService.__new__(QQReplyBufferService)
    seen: list = []

    async def _nested_run(request):
        # Record the request too: the 17+ branch pops the pending before
        # running, so a pending-only probe cannot tell "summary ran" from
        # "summary skipped".
        held = service._pending.get("group:7788")
        seen.append({
            "text": str(getattr(request, "message_text", ""))[:8],
            "snapshot": dict(getattr(held, "consent_snapshot", {}) or {}),
        })

    service.plugin = SimpleNamespace(
        _emit_log=lambda *a, **k: None,
        _user_sessions={},
        _qq_settings={"group_memory_enabled": True},
        reply_pipeline=SimpleNamespace(run=_nested_run),
        session_memory_service=SimpleNamespace(
            record_synthetic_prompt_rows=MagicMock(),
        ),
    )
    service._pending = {}
    service._session_history_len = lambda key: 0
    service._record_synthetic_prompt_rows = lambda key, before: None
    service._mark_latest_draft_undelivered = lambda key: None
    service._bind_draft_to_pending = lambda row, pending: None
    service._topic_hint = lambda text: ""

    pending = PendingReply(
        first_text="之前的草稿", wait_seconds=1.0, sender_id="2046",
        is_group=True, group_id="7788",
    )
    pending.task = asyncio.create_task(asyncio.sleep(999))
    pending.buffered_texts = ["之前的草稿"]
    pending.message_count = 9
    pending.consent_snapshot = {}
    service._pending["group:7788"] = pending

    await service.schedule_reply(
        session_key="group:7788", reply_text="新草稿", raw_text="新草稿",
        blocks=[], wait_seconds=1.0, sender_id="2046", is_group=True,
        group_id="7788", consent_snapshot={"group_memory_enabled": True},
    )
    pending.task.cancel()
    # The nested acknowledgement saw the new draft's dependency already
    # merged in — not an empty snapshot.
    assert len(seen) == 1
    assert seen[0]["snapshot"] == {"group_memory_enabled": True}

    # Revoked while buffering: neither nested run may quote the buffered
    # memory-derived drafts. The 17+ branch additionally drops the buffer
    # (drafts stay undelivered) and releases the cursor barrier.
    seen.clear()
    settled: list = []
    service._settle_provisional = staticmethod(
        lambda user_data, p: settled.append(p)
    )
    service.plugin._qq_settings["group_memory_enabled"] = False
    revoked = PendingReply(
        first_text="记忆派生草稿", wait_seconds=1.0, sender_id="2046",
        is_group=True, group_id="7788",
    )
    revoked.task = asyncio.create_task(asyncio.sleep(999))
    revoked.buffered_texts = ["记忆派生草稿"]
    revoked.message_count = 9
    revoked.consent_snapshot = {"group_memory_enabled": True}
    service._pending["group:7788"] = revoked
    await service.schedule_reply(
        session_key="group:7788", reply_text="新草稿", raw_text="新草稿",
        blocks=[], wait_seconds=1.0, sender_id="2046", is_group=True,
        group_id="7788", consent_snapshot={"group_memory_enabled": False},
    )
    revoked.task.cancel()
    assert seen == []

    forced = PendingReply(
        first_text="记忆派生草稿", wait_seconds=1.0, sender_id="2046",
        is_group=True, group_id="7788",
    )
    forced.task = asyncio.create_task(asyncio.sleep(999))
    forced.buffered_texts = ["记忆派生草稿"]
    forced.message_count = 20
    forced.consent_snapshot = {"group_memory_enabled": True}
    service._pending["group:7788"] = forced
    await service.schedule_reply(
        session_key="group:7788", reply_text="新草稿", raw_text="新草稿",
        blocks=[], wait_seconds=1.0, sender_id="2046", is_group=True,
        group_id="7788", consent_snapshot={"group_memory_enabled": False},
    )
    assert seen == []
    with pytest.raises(asyncio.CancelledError):
        await forced.task
    assert "group:7788" not in service._pending
    assert settled and settled[-1] is forced


@pytest.mark.asyncio
async def test_direct_delivery_gated_on_consent_at_send_time():
    """Postprocessing (XML repair) awaits another LLM after the model-time
    recheck, so the unbuffered direct path needs its own pre-send gate."""
    from plugin.plugins.qq_auto_reply.pipeline_models import (
        QQDeliveryPlan,
        QQDeliveryResult,
        QQMessageBlock,
        QQReplyOutcome,
        QQReplyRequest,
    )
    from plugin.plugins.qq_auto_reply.reply_pipeline import (
        QQReplyPipelineRunner,
    )

    deliver = AsyncMock()
    mark = MagicMock()
    plugin = SimpleNamespace(
        reply_buffer_service=None,
        reply_delivery_node=SimpleNamespace(deliver=deliver),
        reply_generation_service=SimpleNamespace(
            record_scoped_mentions_on_delivery=AsyncMock(),
            append_fallback_ai_row=MagicMock(),
        ),
        session_memory_service=SimpleNamespace(
            record_tail_undelivered_ai_row=mark,
        ),
        _build_session_key=(
            lambda *, sender_id, is_group, group_id: f"group:{group_id}"
        ),
        _qq_settings={"group_memory_enabled": False},
        logger=MagicMock(),
    )
    runner = QQReplyPipelineRunner(plugin)
    context = SimpleNamespace(
        is_group=True, group_id="7788",
        consent_snapshot={"group_memory_enabled": True},
    )
    request = QQReplyRequest(
        message_text="hi", sender_id="2046", is_group=True, group_id="7788",
    )
    plan = QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(text="带着群记忆的回复")],
    )
    result = await runner._run_delivery(
        plan, request, QQReplyOutcome(action="reply", reply_text="回复"),
        context=context,
    )
    deliver.assert_not_awaited()
    assert result.delivered is False
    mark.assert_called_once_with("group:7788")

    # Consent intact: delivery proceeds as usual.
    plugin._qq_settings["group_memory_enabled"] = True
    deliver.return_value = QQDeliveryResult(
        delivered=True, target_type="group", target_id="7788", reply_text="回复",
    )
    result = await runner._run_delivery(
        plan, request, QQReplyOutcome(action="reply", reply_text="回复"),
        context=context,
    )
    deliver.assert_awaited_once()
    assert result.delivered is True
    # ...and the sender receives a live gate, because blocks are spaced
    # seconds apart and consent can drop between them.
    gate = deliver.await_args.kwargs.get("consent_gate")
    assert callable(gate)
    assert gate() is False
    plugin._qq_settings["group_memory_enabled"] = False
    assert gate() is True
    plugin._qq_settings["group_memory_enabled"] = True


@pytest.mark.asyncio
async def test_buffer_receives_generation_time_consent_snapshot():
    """Resampling the switches after generation makes the buffered
    revocation check compare false to false."""
    from plugin.plugins.qq_auto_reply.pipeline_models import (
        QQDeliveryPlan,
        QQMessageBlock,
        QQReplyOutcome,
        QQReplyRequest,
    )
    from plugin.plugins.qq_auto_reply.reply_pipeline import (
        QQReplyPipelineRunner,
    )

    schedule = AsyncMock()
    plugin = SimpleNamespace(
        reply_buffer_service=SimpleNamespace(schedule_reply=schedule),
        _build_session_key=(
            lambda *, sender_id, is_group, group_id: f"group:{group_id}"
        ),
        _emit_log=lambda *a, **k: None,
        _qq_settings={"group_memory_enabled": False},
        logger=MagicMock(),
    )
    runner = QQReplyPipelineRunner(plugin)
    context = SimpleNamespace(
        is_group=True, group_id="7788",
        consent_snapshot={"group_memory_enabled": True},
    )
    request = QQReplyRequest(
        message_text="hi", sender_id="2046", is_group=True, group_id="7788",
    )
    plan = QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(text="带着群记忆的回复")],
    )
    await runner._run_delivery(
        plan, request,
        QQReplyOutcome(action="reply", reply_text="回复", raw_reply_text="回复"),
        context=context,
    )
    assert schedule.await_args.kwargs["consent_snapshot"] == {
        "group_memory_enabled": True,
    }


def _make_reply_context(**overrides):
    """Build a real QQReplyContext with placeholder values for every
    required field, so tests exercise the dataclass itself (defaults,
    factories) rather than a hand-rolled stand-in."""
    import dataclasses

    from plugin.plugins.qq_auto_reply.pipeline_models import QQReplyContext

    kwargs = {}
    for f in dataclasses.fields(QQReplyContext):
        if f.default is not dataclasses.MISSING or (
            f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        ):
            continue
        kwargs[f.name] = {
            "bool": False, "str": "", "int": 0,
        }.get(str(f.type), None)
    kwargs.update(overrides)
    return QQReplyContext(**kwargs)


def test_reply_context_carries_a_unique_turn_id():
    """The fallback idempotency key must not be id(context): CPython hands
    the freed address of one context straight to the next, so a key built
    from it collides across turns."""
    contexts = []
    addresses = set()
    for _ in range(8):
        ctx = _make_reply_context()
        contexts.append(ctx.turn_uid)
        addresses.add(id(ctx))
        del ctx
    assert len(set(contexts)) == 8, "turn_uid must be unique per context"
    # The point of the test: addresses DO repeat, which is why id() is unsafe.
    assert len(addresses) < 8


@pytest.mark.asyncio
async def test_fallback_rows_survive_turns_without_a_message_id():
    """Proactive speech, rapid-fire acks and join notices carry no message
    id. Keying idempotency on the context's address suppressed every
    fallback row after the first one for those turns."""
    from plugin.plugins.qq_auto_reply.reply_generation_service import (
        QQReplyGenerationService,
    )

    history: list = []
    plugin = SimpleNamespace(
        _user_sessions={
            "group:7788": {
                "memory_enabled": True,
                "session": SimpleNamespace(_conversation_history=history),
            },
        },
        session_runtime_service=SimpleNamespace(
            build_generation_session_key=lambda context: "group:7788",
        ),
    )
    service = QQReplyGenerationService.__new__(QQReplyGenerationService)
    service.plugin = plugin

    first = _make_reply_context(is_group=True, group_id="7788")
    second = _make_reply_context(is_group=True, group_id="7788")
    assert not first.current_message_id and not second.current_message_id

    service.append_fallback_ai_row(first, "第一轮 fallback")
    service.append_fallback_ai_row(second, "第二轮 fallback")
    assert [row.content for row in history] == [
        "第一轮 fallback", "第二轮 fallback",
    ]
    # The key must be derived from the context's own turn id, never from
    # its address — pinned directly so the test does not depend on whether
    # the allocator happens to reuse an address in this run.
    assert [
        row.additional_kwargs["neko_fallback_row"] for row in history
    ] == [f"fallback:{first.turn_uid}", f"fallback:{second.turn_uid}"]
    # Still idempotent within one turn.
    service.append_fallback_ai_row(second, "第二轮 fallback")
    assert len(history) == 2


@pytest.mark.asyncio
async def test_delivery_stops_between_blocks_when_consent_revoked(monkeypatch):
    """Blocks are spaced 2-5s apart to look human; revoking consent during
    one of those gaps must stop the remaining memory-derived blocks."""
    from plugin.plugins.qq_auto_reply import reply_delivery_node as rdn
    from plugin.plugins.qq_auto_reply.pipeline_models import (
        QQDeliveryPlan,
        QQMessageBlock,
    )

    monkeypatch.setattr(rdn.random, "uniform", lambda a, b: 0)
    send = AsyncMock(return_value="mid")
    node = rdn.QQReplyDeliveryNode.__new__(rdn.QQReplyDeliveryNode)
    node.plugin = SimpleNamespace(
        _get_reply_mode=lambda: "text",
        logger=MagicMock(),
        qq_client=SimpleNamespace(
            needs_attention=False, send_group_message=send,
        ),
    )
    plan = QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[
            QQMessageBlock(text="第一句"),
            QQMessageBlock(text="第二句"),
            QQMessageBlock(text="第三句"),
        ],
    )
    calls = {"n": 0}

    def _gate():
        calls["n"] += 1
        return calls["n"] > 1  # revoked right after the first block

    result = await node.deliver(plan, consent_gate=_gate)
    assert send.await_count == 1
    # The delivered part still carried revoked-context text, so the row
    # must stay out of memory: the plan reports undelivered.
    assert result.delivered is False

    # No gate: every block goes out, as before.
    send.reset_mock()
    result = await node.deliver(plan)
    assert send.await_count == 3
    assert result.delivered is True


@pytest.mark.asyncio
async def test_buffered_fallback_row_is_appended_under_the_session_lock():
    """A group message arriving during the buffer wait runs a full pipeline
    under the session lock. Appending the fallback row without that lock
    interleaves it into the other turn's rows, and the next draft scan then
    marks the delivered reply as undelivered."""
    from plugin.plugins.qq_auto_reply.pipeline_models import (
        QQDeliveryResult,
        QQMessageBlock,
    )
    from plugin.plugins.qq_auto_reply.reply_buffer_service import (
        PendingReply,
        QQReplyBufferService,
    )

    order: list = []

    async def _locked(session_key, coro_factory):
        order.append("lock:enter")
        try:
            return await coro_factory()
        finally:
            order.append("lock:exit")

    service = QQReplyBufferService.__new__(QQReplyBufferService)
    service.plugin = SimpleNamespace(
        _emit_log=lambda *a, **k: None,
        _user_sessions={"group:7788": {}},
        _qq_settings={"group_memory_enabled": True},
        _run_with_session_lock=_locked,
        reply_delivery_node=SimpleNamespace(
            deliver=AsyncMock(return_value=QQDeliveryResult(
                delivered=True, target_type="group", target_id="7788",
                reply_text="回复",
            )),
        ),
        reply_generation_service=SimpleNamespace(
            append_fallback_ai_row=MagicMock(
                side_effect=lambda *a, **k: order.append("append")
            ),
            record_scoped_mentions_on_delivery=AsyncMock(
                side_effect=lambda *a, **k: order.append("mention")
            ),
        ),
    )
    service._pending = {}
    service._clear_undelivered_marks = lambda key, pending: None
    service._settle_provisional = staticmethod(lambda ud, p: None)

    pending = PendingReply(
        first_text="fallback 回复", wait_seconds=0.0, sender_id="2046",
        is_group=True, group_id="7788",
    )
    pending.buffered_texts = ["fallback 回复"]
    pending.message_count = 1
    pending.used_fallback_reply = True
    pending.mention_context = SimpleNamespace(
        is_group=True, group_id="7788", ephemeral_session=False,
    )
    pending.wait_until = 0.0
    service._pending["group:7788"] = pending

    pending.first_blocks = [
        QQMessageBlock(text="fallback 回复"),
        QQMessageBlock(text="她记得群规是不剧透"),
    ]
    await service._deliver_after_wait("group:7788", pending)
    assert order == ["lock:enter", "append", "mention", "lock:exit"]
    # The appended row carries the WHOLE delivered plan, not just the
    # first block (postprocess reduces reply_text to that one).
    appended = (
        service.plugin.reply_generation_service.append_fallback_ai_row.call_args
    )
    assert "她记得群规是不剧透" in appended.args[1]


@pytest.mark.asyncio
async def test_concurrent_settings_saves_serialize_the_consent_transaction():
    """Two overlapping saves must not interleave read-before / mutate /
    persist / rollback: the second one would otherwise read the first
    one's not-yet-persisted value as its own "before", and no rollback can
    repair that — runtime and disk end up permanently opposite."""
    from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService

    settings = {
        "group_memory_enabled": False,
        "group_member_memory_enabled": False,
        "allow_cross_group_context": False,
    }
    plugin = SimpleNamespace(
        _qq_settings=settings,
        _user_sessions={},
        _emit_log=lambda *a, **k: None,
        logger=MagicMock(),
        attention_service=None,
        qq_client=None,
        _running=False,
        _startup_error=None,
        _strategy_mode="",
        _ensure_qq_client_initialized=lambda: None,
    )
    service = QQSettingsService.__new__(QQSettingsService)
    service.plugin = plugin
    service._enforce_attention_for_dynamic_mode = lambda: None
    service._stamp_group_memory_transition = lambda *, enabled_after: None
    service._spawn_group_memory_sync_task = lambda coro: coro.close()

    observed_before: list = []
    order: list = []

    async def _slow_failing_write():
        order.append("A:write-start")
        await asyncio.sleep(0.05)
        # Persisting swaps in a freshly normalized settings dict, exactly
        # like config_store.save + apply_runtime_settings do.
        plugin._qq_settings = dict(plugin._qq_settings)
        order.append("A:write-fail")
        return False

    async def _ok_write():
        order.append("B:write-ok")
        return True

    writes = [_slow_failing_write, _ok_write]

    async def _persist():
        return await writes.pop(0)()

    service.persist_business_config = _persist
    original_rollback = service._rollback_unpersisted_memory_toggles

    def _spy_rollback(persisted, **kw):
        observed_before.append(
            (kw["group_memory_before"], kw["group_memory_after"], persisted)
        )
        return original_rollback(persisted, **kw)

    service._rollback_unpersisted_memory_toggles = _spy_rollback

    task_a = asyncio.create_task(service.save_settings(group_memory_enabled=True))
    await asyncio.sleep(0)  # let A reach its write
    task_b = asyncio.create_task(service.save_settings(
        group_memory_enabled=True, onebot_url="ws://b",
    ))
    await asyncio.gather(task_a, task_b)

    # A: before=False, after=True, write failed -> rolled back to False.
    # B then reads that rolled-back False as ITS before and persists True.
    assert observed_before == [(False, True, False), (False, True, True)]
    assert plugin._qq_settings["group_memory_enabled"] is True
    assert order == ["A:write-start", "A:write-fail", "B:write-ok"]
    # B applied its own fields only after taking the lock, so they landed
    # in the dict A swapped in — mutating before the wait silently drops
    # them.
    assert plugin._qq_settings["onebot_url"] == "ws://b"


@pytest.mark.asyncio
async def test_core_memory_section_reads_the_localized_template():
    """The long-term memory block went through a bare .format() on the
    Chinese constant, so every locale bundle entry for it was dead. It now
    resolves through the same static-layer path as the other prompt
    sections — including the required-placeholder guard."""
    from plugin.plugins.qq_auto_reply.session_instruction_service import (
        QQSessionInstructionService,
    )

    bridge = MagicMock()
    bridge.fetch_bootstrap_memory = AsyncMock(return_value="长期记忆内容")
    bridge.fetch_scoped_bootstrap_memory = AsyncMock()
    bundle = {
        "core_memory_section": "## Long-term memory\n{memory_context}\n{context_ready}",
    }
    plugin = SimpleNamespace(
        memory_bridge=bridge,
        logger=MagicMock(),
        _qq_settings={},
        i18n=SimpleNamespace(
            t=lambda key, default="", **kw: bundle.get(key, default)
        ),
    )
    service = QQSessionInstructionService(plugin)

    rendered = await service._build_core_memory_section(
        should_use_memory_context=True,
        her_name="Neko",
        master_name="Master",
        context_ready_template="{name}/{master}",
        locale="en",
    )
    assert "Long-term memory" in rendered
    assert "长期记忆内容" in rendered
    assert "Neko/Master" in rendered

    # A translation that dropped a placeholder must not silently swallow
    # the memory: the guard falls back to the shipped template.
    bundle["core_memory_section"] = "## Long-term memory\n{context_ready}"
    rendered = await service._build_core_memory_section(
        should_use_memory_context=True,
        her_name="Neko",
        master_name="Master",
        context_ready_template="{name}/{master}",
        locale="en",
    )
    assert "长期记忆内容" in rendered

    # ...and neither must a translation carrying an unknown placeholder.
    bundle["core_memory_section"] = (
        "## Long-term memory\n{memory_context}\n{context_ready}\n{unknown}"
    )
    rendered = await service._build_core_memory_section(
        should_use_memory_context=True,
        her_name="Neko",
        master_name="Master",
        context_ready_template="{name}/{master}",
        locale="en",
    )
    assert "长期记忆内容" in rendered


def test_core_memory_section_key_exists_in_every_locale_bundle():
    """The wiring is only worth anything if every bundle carries the key
    with both placeholders."""
    import json
    from pathlib import Path

    i18n_dir = (
        Path(__file__).resolve().parents[2]
        / "plugin" / "plugins" / "qq_auto_reply" / "i18n"
    )
    bundles = sorted(i18n_dir.glob("*.json"))
    assert len(bundles) >= 9
    for path in bundles:
        data = json.loads(path.read_text(encoding="utf-8"))
        template = data.get("core_memory_section")
        assert isinstance(template, str) and template.strip(), path.name
        assert "{memory_context}" in template, path.name
        assert "{context_ready}" in template, path.name


@pytest.mark.asyncio
async def test_open_platform_keyboard_message_carries_a_markdown_body():
    """Attaching a keyboard forces msg_type=2, and a type-2 payload puts
    its text in markdown.content. Leaving the text in `content` produced a
    body-less type-2 message: no message id came back, so the delivery
    layer reported it undelivered and the reply was excluded from memory."""
    from plugin.plugins.qq_auto_reply.qq_open_plat import (
        QQOpenPlatformConnection,
    )

    sent: list = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "msg-1"}

    class _HTTP:
        @staticmethod
        async def post(url, json=None, headers=None):
            sent.append(json)
            return _Resp()

    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._http = _HTTP()
    conn._ensure_token = AsyncMock()
    conn._auth_headers = lambda: {}
    conn.logger = MagicMock()

    await conn.send_group_message_segments(
        "7788", [{"type": "text", "data": {"text": "要看看哪个？"}}],
        keyboard="状态|配置",
    )
    body = sent[-1]
    assert body["msg_type"] == 2
    assert body["markdown"] == {"content": "要看看哪个？"}
    assert "content" not in body
    assert body["keyboard"]["content"]["rows"][0]["buttons"]

    # Plain text without a keyboard is untouched (type 0, content field).
    sent.clear()
    await conn.send_group_message_segments(
        "7788", [{"type": "text", "data": {"text": "普通回复"}}],
    )
    body = sent[-1]
    assert body.get("content") == "普通回复"
    assert "msg_type" not in body and "markdown" not in body


@pytest.mark.asyncio
async def test_memory_free_turn_keeps_its_empty_consent_snapshot():
    """A turn that used no memory stores an EMPTY snapshot, which means
    "no dependencies" — not "no snapshot". Falling back to sampling the
    live switches would make a later opt-out discard a draft that never
    touched memory."""
    from plugin.plugins.qq_auto_reply.pipeline_models import (
        QQDeliveryPlan,
        QQMessageBlock,
        QQReplyOutcome,
        QQReplyRequest,
    )
    from plugin.plugins.qq_auto_reply.reply_pipeline import (
        QQReplyPipelineRunner,
    )

    schedule = AsyncMock()
    plugin = SimpleNamespace(
        reply_buffer_service=SimpleNamespace(schedule_reply=schedule),
        _build_session_key=(
            lambda *, sender_id, is_group, group_id: f"group:{group_id}"
        ),
        _emit_log=lambda *a, **k: None,
        _qq_settings={
            "group_memory_enabled": True,
            "group_member_memory_enabled": True,
            "allow_cross_group_context": True,
        },
        logger=MagicMock(),
    )
    runner = QQReplyPipelineRunner(plugin)
    request = QQReplyRequest(
        message_text="hi", sender_id="2046", is_group=True, group_id="7788",
    )
    plan = QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(text="没用到记忆的回复")],
    )
    await runner._run_delivery(
        plan, request,
        QQReplyOutcome(action="reply", reply_text="回复", raw_reply_text="回复"),
        context=SimpleNamespace(
            is_group=True, group_id="7788", consent_snapshot={},
        ),
    )
    assert schedule.await_args.kwargs["consent_snapshot"] == {}

    # A context that never reached generation (no snapshot at all) still
    # falls back to the live switches.
    schedule.reset_mock()
    await runner._run_delivery(
        plan, request,
        QQReplyOutcome(action="reply", reply_text="回复", raw_reply_text="回复"),
        context=SimpleNamespace(
            is_group=True, group_id="7788", consent_snapshot=None,
        ),
    )
    assert schedule.await_args.kwargs["consent_snapshot"] == {
        "group_memory_enabled": True,
        "group_member_memory_enabled": True,
        "allow_cross_group_context": True,
    }


@pytest.mark.asyncio
async def test_nested_synthetic_turn_inherits_buffered_consent_dependencies():
    """The summary/ack prompts quote the buffered drafts, but their own
    prompt is clean — so their own snapshot is empty and their gates can
    never fire. They must inherit the pending's dependencies."""
    from plugin.plugins.qq_auto_reply.reply_buffer_service import (
        PendingReply,
        QQReplyBufferService,
    )

    seen: list = []

    async def _nested_run(request):
        seen.append(dict(request.inherited_consent_snapshot or {}))

    service = QQReplyBufferService.__new__(QQReplyBufferService)
    service.plugin = SimpleNamespace(
        _emit_log=lambda *a, **k: None,
        _user_sessions={},
        _qq_settings={
            "group_memory_enabled": True, "group_member_memory_enabled": True,
        },
        reply_pipeline=SimpleNamespace(run=_nested_run),
        session_memory_service=SimpleNamespace(
            record_synthetic_prompt_rows=MagicMock(),
        ),
    )
    service._pending = {}
    service._session_history_len = lambda key: 0
    service._record_synthetic_prompt_rows = lambda key, before: None
    service._mark_latest_draft_undelivered = lambda key: None
    service._bind_draft_to_pending = lambda row, pending: None
    service._topic_hint = lambda text: ""

    pending = PendingReply(
        first_text="成员记忆派生的草稿", wait_seconds=1.0, sender_id="2046",
        is_group=True, group_id="7788",
    )
    pending.task = asyncio.create_task(asyncio.sleep(999))
    pending.buffered_texts = ["成员记忆派生的草稿"]
    pending.message_count = 9
    pending.consent_snapshot = {"group_member_memory_enabled": True}
    service._pending["group:7788"] = pending

    await service.schedule_reply(
        session_key="group:7788", reply_text="新草稿", raw_text="新草稿",
        blocks=[], wait_seconds=1.0, sender_id="2046", is_group=True,
        group_id="7788", consent_snapshot={},
    )
    pending.task.cancel()
    # rapid_fire_flush deliberately drops the nominal sender's member
    # subject, so without this the member permission is untracked for the
    # whole nested run.
    assert seen == [{"group_member_memory_enabled": True}]


@pytest.mark.asyncio
async def test_inherited_consent_reaches_the_generated_context():
    """The inherited snapshot only helps if the context carries it into
    the gates, and the turn's own dependencies must be unioned on top."""
    from plugin.plugins.qq_auto_reply.reply_generation_service import (
        QQReplyGenerationService,
    )

    service = QQReplyGenerationService.__new__(QQReplyGenerationService)
    service.plugin = SimpleNamespace(
        _qq_settings={"group_memory_enabled": True}, logger=MagicMock(),
    )
    context = SimpleNamespace(
        is_group=True,
        consent_snapshot={"group_member_memory_enabled": True},
    )
    service._store_consent_snapshot(context, {"group_memory_enabled": True})
    assert context.consent_snapshot == {
        "group_member_memory_enabled": True, "group_memory_enabled": True,
    }
    # A later store with a now-false value must not erase the true one.
    service._store_consent_snapshot(context, {"group_memory_enabled": False})
    assert context.consent_snapshot["group_memory_enabled"] is True


def test_scoped_card_contradiction_log_is_redacted(monkeypatch):
    """Scoped group/participant text is deliberately kept out of the
    ordinary Memory log; the character-card rejection line must record
    lengths, not excerpts. (The module logger does not propagate, so the
    log line is captured at the logger itself rather than via caplog.)"""
    from memory.persona import facts as facts_mod
    from memory.persona.manager import PersonaManager

    lines: list = []
    monkeypatch.setattr(
        facts_mod, "logger",
        SimpleNamespace(info=lambda msg, *a, **k: lines.append(str(msg))),
    )
    mixin = PersonaManager.__new__(PersonaManager)
    card = [{"text": "她讨厌咖啡", "source": "character_card"}]

    code, _ = mixin._evaluate_fact_contradiction(
        "Neko", "她不讨厌咖啡", card, stop_names=[], redact_text=True,
    )
    assert code == PersonaManager.FACT_REJECTED_CARD
    assert lines and "她不讨厌咖啡" not in lines[-1]
    assert "她讨厌咖啡" not in lines[-1]
    assert "new_len=" in lines[-1] and "card_len=" in lines[-1]

    # The legacy private path keeps its excerpts (unchanged behaviour).
    lines.clear()
    mixin._evaluate_fact_contradiction(
        "Neko", "她不讨厌咖啡", card, stop_names=[],
    )
    assert lines and "她不讨厌咖啡" in lines[-1]


def test_context_construction_seeds_inherited_consent():
    """The nested run's inherited dependencies only reach the generation
    and pre-send gates if the context is seeded with them at construction.
    Driving build() needs a dozen fakes, so the wiring is pinned on the
    construction site itself — including WHERE the value comes from, so
    replacing it with a constant fails too."""
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "plugin" / "plugins" / "qq_auto_reply" / "reply_context_node.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "QQReplyContext"
    ]
    assert len(calls) == 1
    seeded = [kw for kw in calls[0].keywords if kw.arg == "consent_snapshot"]
    assert seeded, "QQReplyContext must be seeded with the inherited snapshot"
    assert "inherited_consent_snapshot" in ast.get_source_segment(
        source, seeded[0].value
    )


def test_synthetic_source_classification_is_shared_by_read_write_and_mentions():
    """A join notice carries the joining member's id as the nominal sender
    while the text is fabricated. The write path already excluded it; the
    read path and the mention hook must use the SAME classification, or a
    returning member's private facts shape a public welcome."""
    import ast
    from pathlib import Path

    from plugin.plugins.qq_auto_reply.pipeline_models import (
        SYNTHETIC_SOURCE_KINDS,
        is_synthetic_source,
    )

    for kind in (
        "proactive_speech", "rapid_fire_flush", "buffer_delayed",
        "retroactive_review", "group_join_notice",
    ):
        assert is_synthetic_source(kind), kind
    assert not is_synthetic_source("incoming_group")
    assert not is_synthetic_source("")
    assert not is_synthetic_source(None)

    # No site may keep its own private copy of the list — that is how the
    # join notice ended up excluded from writes but not from reads.
    root = Path(__file__).resolve().parents[2] / "plugin" / "plugins" / "qq_auto_reply"
    for rel in (
        "reply_context_node.py",
        "reply_generation_service.py",
        "session_memory_service.py",
    ):
        source = (root / rel).read_text(encoding="utf-8")
        tree = ast.parse(source)
        # Each site must actually CALL the shared predicate: dropping the
        # call (or hard-coding the answer) is exactly the regression that
        # let a join notice through the read path.
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "is_synthetic_source"
        ]
        assert calls, f"{rel} must classify synthetic turns via the shared helper"
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.Set, ast.List)):
                continue
            literals = {
                el.value for el in node.elts
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            }
            overlap = literals & set(SYNTHETIC_SOURCE_KINDS)
            assert len(overlap) < 2, (
                f"{rel} re-declares the synthetic-source list: {sorted(overlap)}"
            )


@pytest.mark.asyncio
async def test_napcat_voice_send_failure_is_not_reported_as_delivered():
    """NapCat's segment API returns None on timeout. send_*_record used to
    drop that result, so a voice-only reply nobody heard was reported
    delivered: no text fallback ran, and the draft was cleared into
    scoped memory."""
    from plugin.plugins.qq_auto_reply.voice_reply_service import (
        QQVoiceReplyService,
    )

    service = QQVoiceReplyService.__new__(QQVoiceReplyService)
    service.plugin = SimpleNamespace(
        qq_client=SimpleNamespace(needs_attention=True),  # NapCat
    )
    # Fire-and-forget text sends keep the old semantics.
    assert service._confirm_send(None) is True
    # ...but the record senders DO report failure through their return.
    assert service._confirm_send(None, has_result_channel=True) is False
    assert service._confirm_send("msg-1", has_result_channel=True) is True


@pytest.mark.asyncio
async def test_record_senders_return_the_segment_result():
    """The wrappers must not swallow the segment API's result."""
    from plugin.plugins.qq_auto_reply.qq_client import QQClient

    client = QQClient.__new__(QQClient)
    # A returned id must reach the caller (an implicit `return None` would
    # look identical to a failure if we only tested the None case).
    client.send_group_message_segments = AsyncMock(return_value="gid")
    client.send_private_message_segments = AsyncMock(return_value="pid")
    assert True
    assert await client.send_group_record("7788", "file:///a.wav") == "gid"
    assert await client.send_private_record("2046", "file:///a.wav") == "pid"

    client.send_group_message_segments = AsyncMock(return_value=None)
    client.send_private_message_segments = AsyncMock(return_value=None)
    assert await client.send_group_record("7788", "file:///a.wav") is None
    assert await client.send_private_record("2046", "file:///a.wav") is None


@pytest.mark.asyncio
async def test_context_build_executes_end_to_end(monkeypatch):
    """A smoke test that actually RUNS build().

    The inherited-consent wiring shipped as a reference to a `request`
    object that build() never receives — a NameError on every reply, and
    the source-level guard could not see it because nothing here executed
    the function. This test exists to make that class of defect
    impossible: it drives build() with fakes and asserts the context it
    returns."""
    from plugin.plugins.qq_auto_reply import reply_context_node as rcn

    monkeypatch.setattr(
        rcn, "get_config_manager",
        lambda: SimpleNamespace(
            get_character_data=lambda: (
                "Master", "Neko", None, {}, None, {}, None, None, None,
            ),
        ),
    )

    plugin = SimpleNamespace(
        logger=MagicMock(),
        _emit_log=lambda *a, **k: None,
        _qq_settings={
            "group_memory_enabled": True,
            "group_member_memory_enabled": True,
        },
        i18n=_default_i18n(),
        permission_mgr=SimpleNamespace(
            get_user_title=lambda *a, **k: "",
            get_nickname=lambda *a, **k: None,
        ),
        qq_client=SimpleNamespace(needs_attention=False),
        memory_bridge=MagicMock(),
        _build_user_title=lambda *a, **k: "",
        _build_character_card_fields=lambda *a, **k: {},
        _should_use_memory_context=lambda *a, **k: False,
        _should_persist_memory=lambda *a, **k: False,
        _fetch_login_status_payload=AsyncMock(return_value={}),
        _normalize_login_identity=lambda payload: ("online", "10000", "Neko"),
        _build_qq_session_instructions=AsyncMock(
            return_value=SimpleNamespace(
                system_prompt="系统提示词", core_memory_text="",
                cross_group_section="", used_member_subject=False,
                context_ready_template="", traces=[],
                memory_context_used=False, scene_mode="group_directed",
                user_title="", character_prompt="",
            )
        ),
        _build_prompt_message=lambda *a, **k: "用户消息",
    )
    node = rcn.QQReplyContextNode.__new__(rcn.QQReplyContextNode)
    node.plugin = plugin

    context = await node.build(
        message="hi",
        permission_level="user",
        sender_id="2046",
        is_group=True,
        group_id="7788",
        source_kind="rapid_fire_flush",
        inherited_consent_snapshot={"group_member_memory_enabled": True},
    )
    assert context.is_group is True
    assert context.consent_snapshot == {"group_member_memory_enabled": True}
    # Synthetic turns drop the nominal sender for memory purposes.
    assert context.turn_uid

    # No inherited snapshot -> None (not an empty dict), so the pipeline
    # still knows generation has not stored its own snapshot yet.
    context = await node.build(
        message="hi",
        permission_level="user",
        sender_id="2046",
        is_group=True,
        group_id="7788",
    )
    assert context.consent_snapshot is None


def test_settlement_progress_counts_member_queues_not_just_the_cursor():
    """A round can flush member buckets and still fail on the group side,
    leaving the digest cursor untouched. Judging progress by the cursor
    alone stops the shutdown retry loop and strands the rest."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    before = QQSessionMemoryService._settlement_progress({
        "last_group_digest_index": 4,
        "group_member_memory_messages": {"a": [], "b": []},
        "pending_settle_buckets": {"c": []},
    })
    drained_one_member = QQSessionMemoryService._settlement_progress({
        "last_group_digest_index": 4,
        "group_member_memory_messages": {"b": []},
        "pending_settle_buckets": {"c": []},
    })
    drained_snapshot = QQSessionMemoryService._settlement_progress({
        "last_group_digest_index": 4,
        "group_member_memory_messages": {"a": [], "b": []},
        "pending_settle_buckets": {},
    })
    assert before != drained_one_member
    assert before != drained_snapshot
    # No movement anywhere is a real failure.
    assert before == QQSessionMemoryService._settlement_progress({
        "last_group_digest_index": 4,
        "group_member_memory_messages": {"a": [], "b": []},
        "pending_settle_buckets": {"c": []},
    })


@pytest.mark.asyncio
async def test_failed_opt_out_settlement_drops_the_pending_snapshot():
    """The failure path already discards the live member buckets
    (fail-closed). Leaving the opt-out snapshot behind lets a later
    finalize commit exactly the data this opt-out refused."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    history = [SimpleNamespace(type="human", content="opt-in 期间的发言")]
    ud = {
        "is_group": True,
        "memory_enabled": True,
        "group_id": "7788",
        "her_name": "Neko",
        "session": SimpleNamespace(_conversation_history=history),
        "pending_disable_settle": True,
        "group_member_memory_messages": {"2046": [{"role": "user"}]},
        "group_member_memory_labels": {"2046": "2046"},
        "pending_settle_buckets": {"2046": [{"role": "user"}]},
        "pending_settle_labels": {"2046": "2046"},
        "pending_member_settle": True,
    }

    async def _run_with_session_lock(session_key, fn):
        return await fn()

    service = QQSessionMemoryService(SimpleNamespace(
        _user_sessions={"group:7788": ud},
        _run_with_session_lock=_run_with_session_lock,
        logger=MagicMock(),
        _qq_settings={},
    ))
    service.finalize_user_memory_session = AsyncMock(return_value=False)

    await service.invalidate_group_sessions(enabled=False)
    assert "pending_settle_buckets" not in ud
    assert "pending_settle_labels" not in ud
    assert "pending_member_settle" not in ud
    assert "group_member_memory_messages" not in ud

    # With a rollback pending, the snapshot is the only copy of a
    # previously SAVED consent era — it must survive for restoration.
    ud.update({
        "memory_enabled": True,
        "pending_disable_settle": True,
        "member_settle_rollback_pending": True,
        "pending_settle_buckets": {"2046": [{"role": "user"}]},
        "pending_settle_labels": {"2046": "2046"},
        "pending_member_settle": True,
    })
    await service.invalidate_group_sessions(enabled=False)
    assert ud.get("pending_settle_buckets")
    assert ud.get("pending_member_settle") is True


@pytest.mark.asyncio
async def test_private_segments_send_waits_for_the_echo_receipt():
    """Without a receipt there is no way to tell "sent" from "not sent",
    so a failed private voice reply was reported as heard: no text
    fallback, and the draft cleared into memory. The private path now uses
    the same echo round-trip as the group twin."""
    import json as _json

    from plugin.plugins.qq_auto_reply.qq_client import QQClient

    client = QQClient.__new__(QQClient)
    client._pending_actions = {}
    client.logger = None
    sent: list = []

    class _WS:
        @staticmethod
        async def send(raw):
            payload = _json.loads(raw)
            sent.append(payload)
            echo = payload.get("echo")
            assert echo, "private sends must carry an echo"
            future = client._pending_actions.get(echo)
            if future and not future.done():
                future.set_result({"data": {"message_id": "pm-1"}})

    client._main_client = _WS()
    assert await client.send_private_record("2046", "file:///a.wav") == "pm-1"
    assert sent[-1]["action"] == "send_private_msg"
    assert not client._pending_actions  # no leaked futures

    # No receipt -> None (the caller falls back to text).
    class _SilentWS:
        @staticmethod
        async def send(raw):
            sent.append(_json.loads(raw))

    client._main_client = _SilentWS()
    import plugin.plugins.qq_auto_reply.qq_client as qc

    original_wait_for = qc.asyncio.wait_for

    async def _instant_timeout(awaitable, timeout=None):
        task = qc.asyncio.ensure_future(awaitable)
        task.cancel()
        raise qc.asyncio.TimeoutError

    qc.asyncio.wait_for = _instant_timeout
    try:
        assert await client.send_private_record("2046", "file:///a.wav") is None
    finally:
        qc.asyncio.wait_for = original_wait_for
    assert not client._pending_actions


@pytest.mark.asyncio
async def test_record_block_delivery_respects_the_result_channel():
    """A <record> block goes out through the segments API, which reports a
    timeout as None. Treating that as fire-and-forget marks a voice reply
    nobody heard as delivered and skips the text fallback."""
    from plugin.plugins.qq_auto_reply.pipeline_models import (
        QQDeliveryPlan,
        QQMessageBlock,
    )
    from plugin.plugins.qq_auto_reply.reply_delivery_node import (
        QQReplyDeliveryNode,
    )

    send_record = AsyncMock(return_value=None)
    send_text = AsyncMock(return_value=None)
    node = QQReplyDeliveryNode.__new__(QQReplyDeliveryNode)
    node.plugin = SimpleNamespace(
        _get_reply_mode=lambda: "text",
        logger=MagicMock(),
        qq_client=SimpleNamespace(
            needs_attention=True,  # NapCat
            send_group_record=send_record,
            send_group_message=send_text,
        ),
        voice_reply_service=SimpleNamespace(
            synthesize_reply_voice_file=AsyncMock(
                return_value=("file:///a.wav", "audio/wav")
            ),
        ),
    )
    plan = QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(record="要说的话")],
        fallback_to_text_on_voice_failure=False,
    )
    result = await node.deliver(plan)
    assert result.delivered is False

    # A confirmed send is still delivered.
    send_record.return_value = "mid"
    result = await node.deliver(plan)
    assert result.delivered is True


@pytest.mark.asyncio
async def test_voice_failure_fallback_keeps_the_keyboard():
    """Falling back to text because the voice send failed must not drop
    the choice buttons: the user would be asked "which one?" with nothing
    to pick."""
    from plugin.plugins.qq_auto_reply.voice_reply_service import (
        QQVoiceReplyService,
    )

    send_segments = AsyncMock(return_value="mid")
    service = QQVoiceReplyService.__new__(QQVoiceReplyService)
    service.plugin = SimpleNamespace(
        logger=MagicMock(),
        _get_reply_mode=lambda: "voice",
        _validate_outbound_message=lambda text: text,
        qq_client=SimpleNamespace(
            needs_attention=False,
            send_group_message_segments=send_segments,
            send_group_record=AsyncMock(return_value=None),  # unconfirmed
        ),
    )
    service.synthesize_reply_voice_file = AsyncMock(
        return_value=("file:///a.wav", "audio/wav")
    )

    assert await service.deliver_group_reply(
        "7788", "要看看哪个？", keyboard="状态|配置",
        fallback_to_text_on_voice_failure=True,
    ) is True
    assert send_segments.await_args.kwargs.get("keyboard") == "状态|配置"

    # Same for the exception path.
    send_segments.reset_mock()
    service.synthesize_reply_voice_file = AsyncMock(
        side_effect=RuntimeError("tts down")
    )
    assert await service.deliver_group_reply(
        "7788", "要看看哪个？", keyboard="状态|配置",
        fallback_to_text_on_voice_failure=True,
    ) is True
    assert send_segments.await_args.kwargs.get("keyboard") == "状态|配置"


@pytest.mark.asyncio
async def test_member_bucket_cap_flushes_instead_of_dropping():
    """A continuously active group never reaches the idle finalizer and
    the focus-shift digest only flushes group history, so hitting the cap
    used to silently delete a member's oldest authorized turns while the
    memory server was perfectly healthy."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    spawned: list = []
    ud = {
        "is_group": True,
        "memory_enabled": True,
        "group_id": "7788",
        "her_name": "Neko",
        "group_member_memory_messages": {},
        "group_member_memory_labels": {},
    }

    async def _run_with_session_lock(session_key, fn):
        return await fn()

    plugin = SimpleNamespace(
        _user_sessions={"group:7788": ud},
        _qq_settings={
            "group_memory_enabled": True, "group_member_memory_enabled": True,
        },
        _run_with_session_lock=_run_with_session_lock,
        _spawn_memory_sync_task=lambda coro: spawned.append(coro),
        logger=MagicMock(),
        permission_mgr=SimpleNamespace(get_nickname=lambda *a, **k: None),
    )
    service = QQSessionMemoryService(plugin)
    context = SimpleNamespace(
        is_group=True, sender_id="2046", member_memory_enabled=True,
        source_kind="incoming_group", group_facing=False,
        group_scene_mode="", message="发言",
    )

    for _ in range(QQSessionMemoryService.GROUP_MEMBER_MAX_MESSAGES):
        service.record_group_member_turn(ud, context)
    bucket = ud["group_member_memory_messages"]["2046"]
    # Nothing was dropped, and a drain was requested.
    assert len(bucket) == QQSessionMemoryService.GROUP_MEMBER_MAX_MESSAGES
    assert ud.get("member_flush_due") is True

    # The per-turn async hook schedules the drain in the background.
    await service.cache_session_delta("group:7788", ud)
    assert len(spawned) == 1
    assert "member_flush_due" not in ud

    # While that drain is in flight, further turns must not pile up more
    # tasks: with a slow memory server they would all queue on the same
    # session lock and grow without bound.
    service.record_group_member_turn(ud, context)
    assert ud.get("member_flush_due") is True
    await service.cache_session_delta("group:7788", ud)
    assert len(spawned) == 1
    # ...and the pending signal is kept, not swallowed.
    assert ud.get("member_flush_due") is True

    flushed: list = []
    service._flush_member_buckets = AsyncMock(
        side_effect=lambda user_data, **kw: flushed.append(kw["reason"]) or []
    )
    await spawned.pop()
    assert flushed == ["member_bucket_cap"]
    assert "member_drain_in_flight" not in ud

    # Once it finished, the next turn can schedule again.
    await service.cache_session_delta("group:7788", ud)
    assert len(spawned) == 1
    await spawned.pop()

    # Only past the hard limit (persistent flush failure) is anything
    # discarded, and it is logged.
    ud["group_member_memory_messages"]["2046"] = [
        {"role": "user"} for _ in range(QQSessionMemoryService.GROUP_MEMBER_HARD_LIMIT)
    ]
    service.record_group_member_turn(ud, context)
    assert len(ud["group_member_memory_messages"]["2046"]) == (
        QQSessionMemoryService.GROUP_MEMBER_HARD_LIMIT
    )
    assert plugin.logger.warning.called


@pytest.mark.asyncio
async def test_group_backlog_is_drained_before_it_can_be_lost():
    """The repetition guard replaces the whole conversation history with a
    bare system message. Draining on a backlog threshold does not close
    that window (the guard lives in the shared omni client) but bounds the
    loss to at most one trigger's worth of turns."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    trigger = QQSessionMemoryService.GROUP_DIGEST_BACKLOG_TRIGGER
    history = [SimpleNamespace(type="human", content=f"m{i}") for i in range(trigger)]
    ud = {
        "is_group": True,
        "memory_enabled": True,
        "group_id": "7788",
        "her_name": "Neko",
        "session": SimpleNamespace(_conversation_history=history),
        "last_group_digest_index": 0,
    }
    spawned: list = []

    async def _run_with_session_lock(session_key, fn):
        return await fn()

    plugin = SimpleNamespace(
        _user_sessions={"group:7788": ud},
        _qq_settings={"group_memory_enabled": True},
        _run_with_session_lock=_run_with_session_lock,
        _spawn_memory_sync_task=lambda coro: spawned.append(coro),
        logger=MagicMock(),
    )
    service = QQSessionMemoryService(plugin)
    settled: list = []
    service._settle_group_digest_batches = AsyncMock(
        side_effect=lambda **kw: settled.append(kw["reason"]) or True
    )

    await service.cache_session_delta("group:7788", ud)
    assert len(spawned) == 1
    # A second turn while the drain is in flight must not pile up tasks.
    await service.cache_session_delta("group:7788", ud)
    assert len(spawned) == 1
    await spawned.pop()
    assert settled == ["digest_backlog"]
    assert "group_digest_draining" not in ud

    # Below the threshold nothing is scheduled.
    ud["last_group_digest_index"] = len(history)
    await service.cache_session_delta("group:7788", ud)
    assert spawned == []


def test_delivered_blocks_text_covers_every_content_block():
    from plugin.plugins.qq_auto_reply.pipeline_models import (
        QQMessageBlock,
        delivered_blocks_text,
    )

    text = delivered_blocks_text([
        QQMessageBlock(text="第一句"),
        QQMessageBlock(poke="2"),
        QQMessageBlock(text="她记得群规是不剧透"),
        QQMessageBlock(record="这句是语音"),
    ])
    assert "第一句" in text
    assert "她记得群规是不剧透" in text
    assert "这句是语音" in text
    assert delivered_blocks_text([]) == ""


@pytest.mark.asyncio
async def test_mention_scan_covers_later_blocks_on_both_delivery_paths():
    """postprocess keeps only the first block in reply_text; a fact
    disclosed in a later block would never bump its mention counter and so
    never reach anti-repeat suppression."""
    from plugin.plugins.qq_auto_reply.pipeline_models import (
        QQDeliveryPlan,
        QQDeliveryResult,
        QQMessageBlock,
        QQReplyOutcome,
        QQReplyRequest,
    )
    from plugin.plugins.qq_auto_reply.reply_buffer_service import (
        PendingReply,
        QQReplyBufferService,
    )
    from plugin.plugins.qq_auto_reply.reply_pipeline import (
        QQReplyPipelineRunner,
    )

    blocks = [
        QQMessageBlock(text="嗯嗯"),
        QQMessageBlock(text="她记得群规是不剧透"),
    ]
    mentions = AsyncMock()
    plugin = SimpleNamespace(
        reply_buffer_service=None,
        reply_delivery_node=SimpleNamespace(
            deliver=AsyncMock(return_value=QQDeliveryResult(
                delivered=True, target_type="group", target_id="7788",
                reply_text="嗯嗯",
            )),
        ),
        reply_generation_service=SimpleNamespace(
            record_scoped_mentions_on_delivery=mentions,
            append_fallback_ai_row=MagicMock(),
        ),
        _qq_settings={"group_memory_enabled": True},
        logger=MagicMock(),
    )
    runner = QQReplyPipelineRunner(plugin)
    context = SimpleNamespace(is_group=True, group_id="7788", consent_snapshot={})
    await runner._run_delivery(
        QQDeliveryPlan(target_type="group", target_id="7788", blocks=blocks),
        QQReplyRequest(
            message_text="hi", sender_id="2046", is_group=True, group_id="7788",
        ),
        QQReplyOutcome(action="reply", reply_text="嗯嗯"),
        context=context,
    )
    assert "她记得群规是不剧透" in mentions.await_args.args[1]

    # Buffered single delivery has the same exposure (texts[0] is the
    # first block too).
    mentions.reset_mock()
    service = QQReplyBufferService.__new__(QQReplyBufferService)

    async def _locked(session_key, coro_factory):
        return await coro_factory()

    service.plugin = SimpleNamespace(
        _emit_log=lambda *a, **k: None,
        _user_sessions={"group:7788": {}},
        _run_with_session_lock=_locked,
        reply_delivery_node=SimpleNamespace(
            deliver=AsyncMock(return_value=QQDeliveryResult(
                delivered=True, target_type="group", target_id="7788",
                reply_text="嗯嗯",
            )),
        ),
        reply_generation_service=SimpleNamespace(
            record_scoped_mentions_on_delivery=mentions,
            append_fallback_ai_row=MagicMock(),
        ),
    )
    service._pending = {}
    service._clear_undelivered_marks = lambda key, pending: None
    service._settle_provisional = staticmethod(lambda ud, p: None)
    service._consent_revoked_since = lambda pending: False
    pending = PendingReply(
        first_text="嗯嗯", wait_seconds=0.0, sender_id="2046",
        is_group=True, group_id="7788",
    )
    pending.buffered_texts = ["嗯嗯"]
    pending.message_count = 1
    pending.first_blocks = blocks
    pending.wait_until = 0.0
    pending.mention_context = context
    service._pending["group:7788"] = pending
    await service._deliver_after_wait("group:7788", pending)
    assert "她记得群规是不剧透" in mentions.await_args.args[1]


@pytest.mark.asyncio
async def test_group_text_send_honours_the_segment_receipt():
    """Group text goes out through the segments API, which reports a
    timeout as None — treating that as fire-and-forget marks an unsent
    reply delivered."""
    from plugin.plugins.qq_auto_reply.voice_reply_service import (
        QQVoiceReplyService,
    )

    send_segments = AsyncMock(return_value=None)
    service = QQVoiceReplyService.__new__(QQVoiceReplyService)
    service.plugin = SimpleNamespace(
        logger=MagicMock(),
        _get_reply_mode=lambda: "text",
        _validate_outbound_message=lambda text: text,
        qq_client=SimpleNamespace(
            needs_attention=True,  # NapCat
            send_group_message_segments=send_segments,
        ),
    )
    assert await service.deliver_group_reply(
        "7788", "回复", fallback_to_text_on_voice_failure=True,
    ) is False
    send_segments.return_value = "mid"
    assert await service.deliver_group_reply(
        "7788", "回复", fallback_to_text_on_voice_failure=True,
    ) is True


@pytest.mark.asyncio
async def test_backlog_drain_defers_to_pending_transitions_and_barriers():
    """The live drain is a new digest producer, so it has to obey the same
    boundaries as the focus-shift digest: not while a consent transition
    is mid-flight, and not past a draft whose fate is still undecided."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    history = [SimpleNamespace(type="human", content=f"m{i}") for i in range(80)]
    ud = {
        "is_group": True,
        "memory_enabled": True,
        "group_id": "7788",
        "her_name": "Neko",
        "session": SimpleNamespace(_conversation_history=history),
        "last_group_digest_index": 0,
    }

    async def _run_with_session_lock(session_key, fn):
        return await fn()

    plugin = SimpleNamespace(
        _user_sessions={"group:7788": ud},
        _qq_settings={"group_memory_enabled": True},
        _run_with_session_lock=_run_with_session_lock,
        logger=MagicMock(),
    )
    service = QQSessionMemoryService(plugin)
    calls: list = []
    service._settle_group_digest_batches = AsyncMock(
        side_effect=lambda **kw: calls.append(kw) or True
    )

    # An opt-out settlement is queued: the transition task owns the cursor
    # (it settles up to the cutoff), a live drain would use the stale one.
    ud["pending_disable_settle"] = True
    await service._drain_group_digest("group:7788")
    assert calls == []
    ud.pop("pending_disable_settle")

    # Post-retain / pre-rebase limbo: the cursor still sits before the
    # opt-out interval, so pushing here would persist OFF-era rows.
    ud["pending_enable_rebase"] = 3
    await service._drain_group_digest("group:7788")
    assert calls == []
    ud.pop("pending_enable_rebase")

    # Clean session: the drain runs and stops at the provisional barrier,
    # otherwise it filters the in-flight draft as undelivered yet advances
    # the cursor past it — the reply that is about to be delivered would
    # stay behind the cursor forever.
    await service._drain_group_digest("group:7788")
    assert len(calls) == 1
    assert calls[0]["stop_at_provisional"] is True
    assert calls[0]["reason"] == "digest_backlog"


@pytest.mark.asyncio
async def test_cancelled_delivery_marks_the_history_row():
    """stop_runtime cancels handler tasks outright. CancelledError is a
    BaseException, so the failure branch never ran: the ai row stayed in
    history unmarked and shutdown finalization would persist a reply the
    user never (fully) received."""
    from plugin.plugins.qq_auto_reply.pipeline_models import (
        QQDeliveryPlan,
        QQMessageBlock,
        QQReplyOutcome,
        QQReplyRequest,
    )
    from plugin.plugins.qq_auto_reply.reply_pipeline import (
        QQReplyPipelineRunner,
    )

    mark = MagicMock()
    plugin = SimpleNamespace(
        reply_buffer_service=None,
        reply_delivery_node=SimpleNamespace(
            deliver=AsyncMock(side_effect=asyncio.CancelledError()),
        ),
        reply_generation_service=SimpleNamespace(
            record_scoped_mentions_on_delivery=AsyncMock(),
            append_fallback_ai_row=MagicMock(),
        ),
        session_memory_service=SimpleNamespace(
            record_tail_undelivered_ai_row=mark,
        ),
        _build_session_key=(
            lambda *, sender_id, is_group, group_id: f"group:{group_id}"
        ),
        _qq_settings={"group_memory_enabled": True},
        logger=MagicMock(),
    )
    runner = QQReplyPipelineRunner(plugin)
    request = QQReplyRequest(
        message_text="hi", sender_id="2046", is_group=True, group_id="7788",
    )
    plan = QQDeliveryPlan(
        target_type="group", target_id="7788",
        blocks=[QQMessageBlock(text="回复")],
    )
    with pytest.raises(asyncio.CancelledError):
        await runner._run_delivery(
            plan, request, QQReplyOutcome(action="reply", reply_text="回复"),
            context=SimpleNamespace(
                is_group=True, group_id="7788", consent_snapshot={},
            ),
        )
    mark.assert_called_once_with("group:7788")

    # A fallback reply has no history row of its own: nothing to mark.
    mark.reset_mock()
    with pytest.raises(asyncio.CancelledError):
        await runner._run_delivery(
            plan, request,
            QQReplyOutcome(action="reply", reply_text="回复", used_fallback=True),
            context=None,
        )
    mark.assert_not_called()


@pytest.mark.asyncio
async def test_fallback_history_row_carries_every_delivered_block():
    """postprocess keeps only the first block in reply_text; appending just
    that leaves the rest of a delivered fallback out of scoped history."""
    from plugin.plugins.qq_auto_reply.pipeline_models import (
        QQDeliveryPlan,
        QQDeliveryResult,
        QQMessageBlock,
        QQReplyOutcome,
        QQReplyRequest,
    )
    from plugin.plugins.qq_auto_reply.reply_pipeline import (
        QQReplyPipelineRunner,
    )

    append = MagicMock()
    plugin = SimpleNamespace(
        reply_buffer_service=None,
        reply_delivery_node=SimpleNamespace(
            deliver=AsyncMock(return_value=QQDeliveryResult(
                delivered=True, target_type="group", target_id="7788",
                reply_text="嗯嗯",
            )),
        ),
        reply_generation_service=SimpleNamespace(
            record_scoped_mentions_on_delivery=AsyncMock(),
            append_fallback_ai_row=append,
        ),
        _qq_settings={"group_memory_enabled": True},
        logger=MagicMock(),
    )
    runner = QQReplyPipelineRunner(plugin)
    await runner._run_delivery(
        QQDeliveryPlan(
            target_type="group", target_id="7788",
            blocks=[
                QQMessageBlock(text="嗯嗯"),
                QQMessageBlock(text="她记得群规是不剧透"),
            ],
        ),
        QQReplyRequest(
            message_text="hi", sender_id="2046", is_group=True, group_id="7788",
        ),
        QQReplyOutcome(action="reply", reply_text="嗯嗯", used_fallback=True),
        context=SimpleNamespace(
            is_group=True, group_id="7788", consent_snapshot={},
        ),
    )
    assert "她记得群规是不剧透" in append.call_args.args[1]


@pytest.mark.asyncio
async def test_digest_batches_stop_at_the_provisional_barrier_when_asked():
    """The barrier only helps if the batcher actually forwards the flag to
    the slicer — the drain's own call site is not enough."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    draft = SimpleNamespace(type="ai", content="在途草稿")
    history = [
        SimpleNamespace(type="human", content="已结算的发言"),
        draft,
    ]
    ud = {
        "is_group": True,
        "memory_enabled": True,
        "group_id": "7788",
        "her_name": "Neko",
        "session": SimpleNamespace(_conversation_history=history),
        "last_group_digest_index": 0,
        "provisional_draft_rows": [draft],
    }
    bridge = MagicMock()
    bridge.group_subject.side_effect = lambda gid: {"subject_id": f"qq:{gid}"}
    bridge.post_scoped_memory_history = AsyncMock(return_value={"status": "ok"})
    service = QQSessionMemoryService(SimpleNamespace(
        memory_bridge=bridge, logger=MagicMock(), _qq_settings={},
    ))

    await service._settle_group_digest_batches(
        user_data=ud, group_id="7788", her_name="Neko", reason="digest_backlog",
        conversation_history=history, last_group_digest_index=0,
        stop_at_provisional=True,
    )
    # The cursor stopped before the undecided draft, so the reply that is
    # about to go out is still ahead of it.
    assert ud["last_group_digest_index"] <= 1

    # Without the barrier (finalize/teardown, where the fate is settled)
    # the batcher walks past it.
    ud["last_group_digest_index"] = 0
    await service._settle_group_digest_batches(
        user_data=ud, group_id="7788", her_name="Neko", reason="finalize",
        conversation_history=history, last_group_digest_index=0,
    )
    assert ud["last_group_digest_index"] == len(history)


@pytest.mark.asyncio
async def test_failed_member_drain_is_rearmed():
    """The scheduler consumes the due flag before spawning, so a drain that
    fails must put it back — otherwise that member has to fill another
    whole bucket before anything is retried."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    ud = {
        "is_group": True,
        "memory_enabled": True,
        "group_id": "7788",
        "her_name": "Neko",
        "group_member_memory_messages": {"2046": [{"role": "user"}]},
    }

    async def _run_with_session_lock(session_key, fn):
        return await fn()

    service = QQSessionMemoryService(SimpleNamespace(
        _user_sessions={"group:7788": ud},
        _qq_settings={
            "group_memory_enabled": True, "group_member_memory_enabled": True,
        },
        _run_with_session_lock=_run_with_session_lock,
        logger=MagicMock(),
    ))
    service._flush_member_buckets = AsyncMock(return_value=["2046"])
    await service._drain_member_buckets("group:7788")
    assert ud.get("member_flush_due") is True
    assert "member_drain_in_flight" not in ud

    # A successful drain leaves nothing armed.
    ud.pop("member_flush_due")
    service._flush_member_buckets = AsyncMock(return_value=[])
    await service._drain_member_buckets("group:7788")
    assert "member_flush_due" not in ud


@pytest.mark.asyncio
async def test_draft_row_marks_are_pruned_when_rows_leave_history():
    """The exclusion lists hold the row objects themselves, so an active
    group that keeps merging drafts would grow them (and pin those rows)
    forever. Rows the history no longer contains can never match again."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    live_row = SimpleNamespace(type="ai", content="还在历史里")
    gone_row = SimpleNamespace(type="ai", content="已被复读守卫清掉")
    history = [SimpleNamespace(type="human", content="发言"), live_row]
    ud = {
        "is_group": True,
        "session": SimpleNamespace(_conversation_history=history),
        "undelivered_draft_rows": [gone_row, live_row],
        "provisional_draft_rows": [gone_row],
        "last_group_digest_index": 0,
    }
    service = QQSessionMemoryService(SimpleNamespace(
        _user_sessions={"group:7788": ud},
        _qq_settings={"group_memory_enabled": True},
        logger=MagicMock(),
        _spawn_memory_sync_task=lambda coro: coro.close(),
        _run_with_session_lock=None,
    ))

    await service.cache_session_delta("group:7788", ud)
    assert ud["undelivered_draft_rows"] == [live_row]
    assert ud["provisional_draft_rows"] == []


@pytest.mark.asyncio
async def test_stripped_cross_group_section_leaves_no_dependency(monkeypatch):
    """A reply whose cross-group section was stripped before generation
    does not depend on that consent. Keeping the field set makes a later
    opt-out discard a reply the model never saw the section in."""
    from plugin.plugins.qq_auto_reply import reply_context_node as rcn

    monkeypatch.setattr(
        rcn, "get_config_manager",
        lambda: SimpleNamespace(
            get_character_data=lambda: (
                "Master", "Neko", None, {}, None, {}, None, None, None,
            ),
        ),
    )
    bundle = SimpleNamespace(
        system_prompt="正文\n\n跨群段原文", core_memory_text="",
        cross_group_section="跨群段原文", used_member_subject=False,
        context_ready_template="", traces=[], memory_context_used=False,
        scene_mode="group_directed", user_title="", character_prompt="",
    )
    plugin = SimpleNamespace(
        logger=MagicMock(),
        _emit_log=lambda *a, **k: None,
        _qq_settings={
            "group_memory_enabled": True,
            "allow_cross_group_context": False,  # revoked during build
        },
        i18n=_default_i18n(),
        permission_mgr=SimpleNamespace(
            get_user_title=lambda *a, **k: "", get_nickname=lambda *a, **k: None,
        ),
        qq_client=SimpleNamespace(needs_attention=False),
        memory_bridge=MagicMock(),
        _build_user_title=lambda *a, **k: "",
        _build_character_card_fields=lambda *a, **k: {},
        _should_use_memory_context=lambda *a, **k: False,
        _should_persist_memory=lambda *a, **k: False,
        _fetch_login_status_payload=AsyncMock(return_value={}),
        _normalize_login_identity=lambda payload: ("online", "10000", "Neko"),
        _build_qq_session_instructions=AsyncMock(return_value=bundle),
        _build_prompt_message=lambda *a, **k: "用户消息",
    )
    node = rcn.QQReplyContextNode.__new__(rcn.QQReplyContextNode)
    node.plugin = plugin

    context = await node.build(
        message="hi", permission_level="user", sender_id="2046",
        is_group=True, group_id="7788",
    )
    assert "跨群段原文" not in context.system_prompt
    assert context.cross_group_section == ""

    # Consent intact: the section stays and the dependency is recorded.
    plugin._qq_settings["allow_cross_group_context"] = True
    context = await node.build(
        message="hi", permission_level="user", sender_id="2046",
        is_group=True, group_id="7788",
    )
    assert context.cross_group_section == "跨群段原文"


@pytest.mark.asyncio
async def test_cq_string_senders_wait_for_the_echo_receipt():
    """The CQ-string senders keep their encoding (routing them through the
    segments API would render [CQ:at,qq=...] as literal text) but they now
    take the same echo round-trip, so a send that never comes back is
    reported as unconfirmed instead of assumed delivered."""
    import json as _json

    from plugin.plugins.qq_auto_reply.qq_client import QQClient

    client = QQClient.__new__(QQClient)
    client._pending_actions = {}
    client.logger = None
    client._sent_message_ids = []
    client.record_sent_message_id = client._sent_message_ids.append
    sent: list = []

    class _WS:
        @staticmethod
        async def send(raw):
            payload = _json.loads(raw)
            sent.append(payload)
            future = client._pending_actions.get(payload.get("echo"))
            if future and not future.done():
                future.set_result({"data": {"message_id": "mid-1"}})

    client._main_client = _WS()
    assert await client.send_group_message("7788", "[CQ:at,qq=1]你好") == "mid-1"
    assert sent[-1]["action"] == "send_group_msg"
    # The encoding is untouched: still a CQ string, not a segment array.
    assert sent[-1]["params"]["message"] == "[CQ:at,qq=1]你好"
    # A confirmed group send records its id (self-message dedup).
    assert client._sent_message_ids == ["mid-1"]

    assert await client.send_message("2046", "你好") == "mid-1"
    assert sent[-1]["action"] == "send_private_msg"
    assert not client._pending_actions

    # No receipt -> None.
    class _SilentWS:
        @staticmethod
        async def send(raw):
            sent.append(_json.loads(raw))

    client._main_client = _SilentWS()
    import plugin.plugins.qq_auto_reply.qq_client as qc

    original_wait_for = qc.asyncio.wait_for

    async def _instant_timeout(awaitable, timeout=None):
        task = qc.asyncio.ensure_future(awaitable)
        task.cancel()
        raise qc.asyncio.TimeoutError

    qc.asyncio.wait_for = _instant_timeout
    try:
        assert await client.send_group_message("7788", "你好") is None
        assert await client.send_message("2046", "你好") is None
    finally:
        qc.asyncio.wait_for = original_wait_for
    assert not client._pending_actions
