"""recall_memory tool-call recall channel for QQ group memory.

The per-turn host-side recall became a model-driven tool call (the free
proxy routes keep the old synchronous recall as a fallback). These tests
pin the six load-bearing pieces of that migration:

1. the handler closure is re-pointed EVERY turn (a shared group session
   must never freeze the first speaker's subject);
2. subjects never appear in the tool schema and model-supplied arguments
   cannot influence them (omitted subjects = the admin's PRIVATE corpus
   server-side);
3. consent becomes a runtime record — what was actually read mid-stream —
   instead of "is the section still in the prompt";
4. the in-handler entry / post-fetch revocation gates;
5. tool-round dict rows never survive in the shared history (neither on
   normal turns nor through the revocation rollback);
6. pre-tool text never reaches the outbound message, and routes that
   silently drop ``tools`` fall back to the synchronous recall.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugin.plugins.qq_auto_reply.memory_bridge import (
    QQMemoryBridge,
    QQMemoryQueryResult,
)
from plugin.plugins.qq_auto_reply.memory_tool_service import (
    QQMemoryToolService,
    RECALL_TOOL_HTTP_TIMEOUT_SECONDS,
    resolve_group_recall_subjects,
)
from plugin.plugins.qq_auto_reply.reply_generation_service import (
    QQReplyGenerationService,
)

TOOL_CAPABLE_MODEL = "qwen3.7-plus"
TOOL_CAPABLE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _mock_bridge(recall_text="群规是不剧透"):
    bridge = MagicMock()
    bridge.group_subject.side_effect = QQMemoryBridge.group_subject
    bridge.group_participant_subject.side_effect = (
        QQMemoryBridge.group_participant_subject
    )
    bridge.query_relevant_memory = AsyncMock(
        return_value=QQMemoryQueryResult(text=recall_text, hit_count=1),
    )
    return bridge


def _tool_plugin(bridge=None, settings=None):
    plugin = SimpleNamespace(
        memory_bridge=bridge if bridge is not None else _mock_bridge(),
        logger=MagicMock(),
        _qq_settings=settings if settings is not None else {
            "group_memory_enabled": True,
            "group_member_memory_enabled": True,
        },
        _queue_attachment_images=AsyncMock(return_value=0),
        _wait_session_response_complete=AsyncMock(return_value=True),
        _ai_turn_timeout_seconds=5,
    )
    plugin.memory_tool_service = QQMemoryToolService(plugin)
    return plugin


def _group_context(sender_id="2046", **overrides):
    fields = dict(
        is_group=True,
        group_id="7788",
        sender_id=sender_id,
        her_name="Neko",
        attachments=None,
        prompt_message="hi",
        system_prompt="系统提示词",
        recalled_memory_text="",
        recalled_memory_used=False,
        core_memory_text="",
        cross_group_section="",
        cross_session_section="",
        used_member_subject=False,
        use_memory_context=True,
        recall_via_tool=True,
        member_memory_enabled=True,
        source_kind="",
        permission_level="user",
        consent_snapshot=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


class _RecallToolClient:
    """A stand-in for OmniOfflineClient's tool loop.

    ``stream_text`` runs the provided script, which can emit pre-tool
    deltas, invoke whatever handler is CURRENTLY installed (exactly like
    the real loop does), append the tool-round dict rows the real client
    persists, and emit the final answer.
    """

    def __init__(self, script, *, model=TOOL_CAPABLE_MODEL,
                 base_url=TOOL_CAPABLE_BASE_URL):
        self._script = script
        self.model = model
        self.base_url = base_url
        self._conversation_history: list = []
        self.tools: list = []
        self.on_tool_call = None
        self.armed_tool_names: list[list[str]] = []

    def set_tools(self, tool_definitions):
        self.tools = list(tool_definitions or [])
        self.armed_tool_names.append([t.name for t in self.tools])

    def set_tool_call_handler(self, handler):
        self.on_tool_call = handler

    async def stream_text(self, message):
        await self._script(self, message)


def _generation_service(plugin):
    service = QQReplyGenerationService.__new__(QQReplyGenerationService)
    service.plugin = plugin
    return service


def _recall_tool_call(arguments):
    return SimpleNamespace(
        name="recall_memory", arguments=arguments, call_id="call_1",
    )


def _tool_round_rows(recall_output):
    return [
        {
            "role": "assistant",
            "content": "我查一下",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "recall_memory", "arguments": "{}"},
            }],
        },
        {
            "role": "tool", "tool_call_id": "call_1", "name": "recall_memory",
            "content": recall_output,
        },
    ]


# ---------------------------------------------------------------------------
# Schema / subject isolation
# ---------------------------------------------------------------------------


def test_recall_tool_schema_exposes_only_query_and_time():
    """Subjects must be host-injected: the server reads an omitted
    subjects field as the legacy PRIVATE corpus, so a subjects (or any
    scope-shaped) parameter in the schema would hand the model a lever
    over what a group turn is allowed to read."""
    definition = _tool_plugin().memory_tool_service.build_recall_tool_definition()
    assert definition.name == "recall_memory"
    assert set(definition.parameters["properties"].keys()) == {"query", "time"}
    assert definition.parameters.get("required") == []
    serialized = json.dumps(definition.parameters, ensure_ascii=False)
    assert "subject" not in serialized
    # 分发走按轮闭包（set_tool_call_handler），不走 registry 静态 handler。
    assert definition.handler is None


@pytest.mark.asyncio
async def test_model_supplied_subjects_cannot_influence_scope():
    """Behavioural twin of the schema assert: even if the model hallucinates
    subject-shaped arguments, the HTTP call carries only the host-derived
    subjects for this turn."""
    plugin = _tool_plugin()
    context = _group_context()
    output, consumed = await plugin.memory_tool_service.execute_recall(
        context=context,
        arguments={
            "query": "群规",
            "subjects": [{"subject_kind": "legacy", "subject_id": "master"}],
            "subject_id": "master",
            "include_legacy_private": True,
        },
    )
    kwargs = plugin.memory_bridge.query_relevant_memory.await_args.kwargs
    assert kwargs["subjects"] == [
        QQMemoryBridge.group_subject("7788"),
        QQMemoryBridge.group_participant_subject("7788", "2046"),
    ]
    assert "群规是不剧透" in output
    assert consumed == {
        "group_memory_enabled": True,
        "group_member_memory_enabled": True,
    }


@pytest.mark.asyncio
async def test_execute_recall_missing_group_id_fails_closed():
    """A malformed group turn without group_id must return nothing —
    subjects=None means the legacy private corpus server-side, so falling
    through would recall the admin's private memories into a group."""
    plugin = _tool_plugin()
    context = _group_context(group_id="   ")
    output, consumed = await plugin.memory_tool_service.execute_recall(
        context=context, arguments={"query": "群规"},
    )
    plugin.memory_bridge.query_relevant_memory.assert_not_awaited()
    assert "群规是不剧透" not in output
    assert consumed == {}


@pytest.mark.asyncio
async def test_participant_subject_gating_matches_write_side():
    """Synthetic turns / receipt-time-off member snapshots / a live member
    opt-out all drop the participant subject — same predicate set as the
    fallback recall and the write side."""
    plugin = _tool_plugin()

    for context in (
        _group_context(source_kind="rapid_fire_flush"),
        _group_context(member_memory_enabled=False),
    ):
        plugin.memory_bridge.query_relevant_memory.reset_mock()
        await plugin.memory_tool_service.execute_recall(
            context=context, arguments={"query": "群规"},
        )
        kwargs = plugin.memory_bridge.query_relevant_memory.await_args.kwargs
        assert kwargs["subjects"] == [QQMemoryBridge.group_subject("7788")]

    # Live member switch off (snapshot on): the read-point recheck drops
    # the participant scope too.
    plugin._qq_settings["group_member_memory_enabled"] = False
    plugin.memory_bridge.query_relevant_memory.reset_mock()
    output, consumed = await plugin.memory_tool_service.execute_recall(
        context=_group_context(), arguments={"query": "群规"},
    )
    kwargs = plugin.memory_bridge.query_relevant_memory.await_args.kwargs
    assert kwargs["subjects"] == [QQMemoryBridge.group_subject("7788")]
    # 只读了群域：consumed 不得把 member 开关也记成依赖。
    assert consumed == {"group_memory_enabled": True}


@pytest.mark.asyncio
async def test_private_admin_recall_uses_legacy_corpus():
    plugin = _tool_plugin()
    context = _group_context(
        is_group=False, group_id=None, permission_level="admin",
    )
    output, consumed = await plugin.memory_tool_service.execute_recall(
        context=context, arguments={"query": "上次的旅行计划", "time": "2026-05"},
    )
    kwargs = plugin.memory_bridge.query_relevant_memory.await_args.kwargs
    assert kwargs["subjects"] is None
    assert kwargs["time_spec"] == "2026-05"
    assert kwargs["timeout"] == RECALL_TOOL_HTTP_TIMEOUT_SECONDS
    # 私聊 legacy 语料不受群开关管辖：无运行时 consent 依赖要记。
    assert consumed == {}
    assert "群规是不剧透" in output


# ---------------------------------------------------------------------------
# In-handler revocation gates (连带 #4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_entry_gate_blocks_revoked_group_read():
    plugin = _tool_plugin(settings={
        "group_memory_enabled": False,
        "group_member_memory_enabled": True,
    })
    output, consumed = await plugin.memory_tool_service.execute_recall(
        context=_group_context(), arguments={"query": "群规"},
    )
    plugin.memory_bridge.query_relevant_memory.assert_not_awaited()
    assert "群规是不剧透" not in output
    assert consumed == {}


@pytest.mark.asyncio
async def test_handler_postfetch_gate_drops_inflight_optout():
    """The opt-out lands while the recall HTTP is on the wire: data already
    read back must be discarded whole, never handed to the model."""
    plugin = _tool_plugin()

    async def _recall_then_revoke(*args, **kwargs):
        plugin._qq_settings["group_memory_enabled"] = False
        return QQMemoryQueryResult(text="群规是不剧透", hit_count=1)

    plugin.memory_bridge.query_relevant_memory = AsyncMock(
        side_effect=_recall_then_revoke,
    )
    output, consumed = await plugin.memory_tool_service.execute_recall(
        context=_group_context(), arguments={"query": "群规"},
    )
    assert "群规是不剧透" not in output
    assert consumed == {}

    # Member-only opt-out during flight: the result mixes group and
    # participant scopes and cannot be split afterwards — drop it whole.
    plugin._qq_settings["group_memory_enabled"] = True
    plugin._qq_settings["group_member_memory_enabled"] = True

    async def _recall_then_revoke_member(*args, **kwargs):
        plugin._qq_settings["group_member_memory_enabled"] = False
        return QQMemoryQueryResult(text="成员私密偏好", hit_count=1)

    plugin.memory_bridge.query_relevant_memory = AsyncMock(
        side_effect=_recall_then_revoke_member,
    )
    output, consumed = await plugin.memory_tool_service.execute_recall(
        context=_group_context(), arguments={"query": "偏好"},
    )
    assert "成员私密偏好" not in output
    assert consumed == {}


@pytest.mark.asyncio
async def test_recall_failure_returns_no_result_without_raising():
    plugin = _tool_plugin()
    plugin.memory_bridge.query_relevant_memory = AsyncMock(
        side_effect=RuntimeError("memory server down"),
    )
    output, consumed = await plugin.memory_tool_service.execute_recall(
        context=_group_context(), arguments={"query": "群规"},
    )
    assert output
    assert consumed == {}


@pytest.mark.asyncio
async def test_zero_hits_never_suggest_an_impossible_retry():
    """The core handler hints "loosen the filter and query again" on a
    query+time miss — but plugin sessions cap max_tool_iterations at 1,
    so by the time the model reads any tool result its tool budget is
    spent and the forced-finalize strips ``tools``. Echoing that hint
    here would coach the model into promising a lookup it cannot do."""
    plugin = _tool_plugin()
    plugin.memory_bridge.query_relevant_memory = AsyncMock(
        return_value=QQMemoryQueryResult(),
    )
    output, consumed = await plugin.memory_tool_service.execute_recall(
        context=_group_context(), arguments={"query": "旅行", "time": "2026-05"},
    )
    assert output
    assert "旅行" not in output  # 不回显"再查一次"式提示
    assert consumed == {}

    # Empty arguments never cost an HTTP round-trip.
    plugin.memory_bridge.query_relevant_memory.reset_mock()
    await plugin.memory_tool_service.execute_recall(
        context=_group_context(), arguments={},
    )
    plugin.memory_bridge.query_relevant_memory.assert_not_awaited()


# ---------------------------------------------------------------------------
# Per-turn handler re-pointing on the shared group session (连带 #1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_session_rebinds_recall_subjects_every_turn():
    """The group session client is shared by the whole group while the
    participant subject follows the speaker. Two consecutive speakers on
    the SAME client: the second turn's recall must carry the second
    speaker's subject — a handler frozen at session-creation time (or a
    'keep the existing handler' shortcut) would recall speaker A's
    private facts while answering speaker B."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)

    async def _script(client, message):
        handler = client.on_tool_call
        assert handler is not None, "本轮没有挂载 recall handler"
        result = await handler(_recall_tool_call({"query": "偏好"}))
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.extend(
            _tool_round_rows(result.output_as_json_string())
        )
        client._conversation_history.append(
            SimpleNamespace(type="ai", content="回复")
        )

    client = _RecallToolClient(_script)
    user_data = {"lock": asyncio.Lock()}

    for sender in ("2046", "9999"):
        reply_chunks: list = []
        await service._run_session_generation(
            context=_group_context(sender_id=sender),
            session_key="group:7788",
            user_data=user_data,
            user_session=client,
            reply_chunks=reply_chunks,
        )

    calls = plugin.memory_bridge.query_relevant_memory.await_args_list
    assert [
        call.kwargs["subjects"][1]["subject_id"] for call in calls
    ] == ["qq:7788:2046", "qq:7788:9999"]


@pytest.mark.asyncio
async def test_recall_tool_disarmed_after_every_turn():
    """The per-turn arm has a symmetric disarm: other generation paths on
    the same client (proactive prompt_ephemeral) must never inherit this
    turn's subject closure — even when the stream raises."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)

    async def _quiet(client, message):
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )

    client = _RecallToolClient(_quiet)
    await service._run_session_generation(
        context=_group_context(),
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=client,
        reply_chunks=[],
    )
    assert client.armed_tool_names[0] == ["recall_memory"]
    assert client.tools == []
    assert client.on_tool_call is None

    async def _boom(client, message):
        raise RuntimeError("stream died")

    client = _RecallToolClient(_boom)
    with pytest.raises(RuntimeError):
        await service._run_session_generation(
            context=_group_context(),
            session_key="group:7788",
            user_data={"lock": asyncio.Lock()},
            user_session=client,
            reply_chunks=[],
        )
    assert client.tools == []
    assert client.on_tool_call is None


@pytest.mark.asyncio
async def test_arm_respects_the_session_clients_frozen_route():
    """Arming binds to the CLIENT's route, not the current config: a
    cached session can outlive a provider switch, and a route that
    silently drops ``tools`` must not count as armed."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)

    free_client = _RecallToolClient(
        AsyncMock(), model="free-model",
        base_url="https://www.lanlan.app/text/v1",
    )
    assert service._arm_recall_tool(
        context=_group_context(),
        user_session=free_client,
        reply_chunks=[],
        consent_before={},
    ) is False
    assert free_client.armed_tool_names == []

    # recall_via_tool=False（构建期决定走回落）：连 set_tools 都不碰。
    capable_client = _RecallToolClient(AsyncMock())
    assert service._arm_recall_tool(
        context=_group_context(recall_via_tool=False),
        user_session=capable_client,
        reply_chunks=[],
        consent_before={},
    ) is False
    assert capable_client.armed_tool_names == []

    # A legacy client stub without the tooling surface degrades quietly.
    assert service._arm_recall_tool(
        context=_group_context(),
        user_session=SimpleNamespace(model=TOOL_CAPABLE_MODEL,
                                     base_url=TOOL_CAPABLE_BASE_URL),
        reply_chunks=[],
        consent_before={},
    ) is False

    assert service._arm_recall_tool(
        context=_group_context(),
        user_session=capable_client,
        reply_chunks=[],
        consent_before={},
    ) is True
    assert capable_client.armed_tool_names[-1] == ["recall_memory"]
    assert capable_client.on_tool_call is not None


# ---------------------------------------------------------------------------
# Runtime consent record (连带 #3) + rollback across tool rows (连带 #5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_read_records_runtime_consent_for_all_gates():
    """The old judgement — "is the recall section still in the prompt" —
    no longer exists on the tool path. What replaces it: the handler
    records the switches the read actually relied on, into both the
    generation-scope snapshot (post-generation discard) and
    context.consent_snapshot (pre-send / buffer gates)."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)
    context = _group_context()

    async def _script(client, message):
        result = await client.on_tool_call(_recall_tool_call({"query": "群规"}))
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.extend(
            _tool_round_rows(result.output_as_json_string())
        )
        client._conversation_history.append(
            SimpleNamespace(type="ai", content="按群规是不剧透哦")
        )
        client.reply_chunks_ref.append("按群规是不剧透哦")

    client = _RecallToolClient(_script)
    reply_chunks: list = []
    client.reply_chunks_ref = reply_chunks
    result = await service._run_session_generation(
        context=context,
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=client,
        reply_chunks=reply_chunks,
    )
    assert result == "按群规是不剧透哦"
    assert context.consent_snapshot == {
        "group_memory_enabled": True,
        "group_member_memory_enabled": True,
    }
    assert context.recalled_memory_used is True
    # 工具轮 dict 行不随轮存活；最终回答行保留。
    assert [getattr(row, "type", "") for row in client._conversation_history] \
        == ["human", "ai"]


@pytest.mark.asyncio
async def test_one_turn_executes_at_most_one_recall_http():
    """max_tool_iterations=1 caps LLM/tool cycles, not calls per cycle: a
    model can emit several recall_memory calls in one assistant response
    and each would cost a sequential 5s HTTP — blowing the one-recall
    assumption the turn timeout is sized for, where the overrun discards
    the shared group session. The handler latch allows one substantive
    execution per turn; empty-argument probes (no HTTP anyway) must not
    burn the turn's only budget."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)
    outputs: list = []

    async def _script(client, message):
        # 模型在同一个回复里连发：空参试探 + 两个实质查询。
        for arguments in ({}, {"query": "群规"}, {"query": "再查一次"}):
            result = await client.on_tool_call(_recall_tool_call(arguments))
            outputs.append(result.output_as_json_string())
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.append(
            SimpleNamespace(type="ai", content="回复")
        )

    client = _RecallToolClient(_script)
    await service._run_session_generation(
        context=_group_context(),
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=client,
        reply_chunks=[],
    )
    plugin.memory_bridge.query_relevant_memory.assert_awaited_once()
    assert plugin.memory_bridge.query_relevant_memory.await_args.args[1] == "群规"
    assert "群规是不剧透" in outputs[1]
    assert "群规是不剧透" not in outputs[2]


@pytest.mark.asyncio
async def test_tool_recall_backfills_the_direct_fallback_text():
    """The direct fallback sends only context.recalled_memory_text to a
    bare LLM. When the model actually recalled something this turn, that
    content must ride along (it still originated from a tool call) — and
    used_member_subject must flip so a member opt-out strips it from the
    fallback prompt via the existing sanitizer."""
    plugin = _tool_plugin()
    context = _group_context()
    output, consumed = await plugin.memory_tool_service.execute_recall(
        context=context, arguments={"query": "群规"},
    )
    assert "群规是不剧透" in context.recalled_memory_text
    assert "长期记忆" in context.recalled_memory_text  # 走统一的包装段
    assert context.used_member_subject is True

    # 只读到群域（member 关闭）：不得虚标 participant 依赖。
    plugin = _tool_plugin(settings={
        "group_memory_enabled": True,
        "group_member_memory_enabled": False,
    })
    context = _group_context()
    await plugin.memory_tool_service.execute_recall(
        context=context, arguments={"query": "群规"},
    )
    assert "群规是不剧透" in context.recalled_memory_text
    assert context.used_member_subject is False

    # 零命中：不回填，fallback 维持无召回。
    plugin = _tool_plugin()
    plugin.memory_bridge.query_relevant_memory = AsyncMock(
        return_value=QQMemoryQueryResult(),
    )
    context = _group_context()
    await plugin.memory_tool_service.execute_recall(
        context=context, arguments={"query": "群规"},
    )
    assert context.recalled_memory_text == ""


@pytest.mark.asyncio
async def test_armed_but_uncalled_tool_creates_no_consent_dependency():
    """The dependency is the READ, not the arming: an armed turn where the
    model never calls the tool consumed nothing, so a mid-stream opt-out
    must not discard its (memory-free) reply. Recording consent at arm
    time would silently drop innocent replies on every settings flip."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)
    context = _group_context()

    async def _script(client, message):
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.append(
            SimpleNamespace(type="ai", content="不查记忆也能回")
        )
        client.reply_chunks_ref.append("不查记忆也能回")
        # 生成期间开关被关掉——但本轮没读过任何 scoped 内容。
        plugin._qq_settings["group_memory_enabled"] = False

    client = _RecallToolClient(_script)
    reply_chunks: list = []
    client.reply_chunks_ref = reply_chunks
    result = await service._run_session_generation(
        context=context,
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=client,
        reply_chunks=reply_chunks,
    )
    assert result == "不查记忆也能回"
    assert [getattr(row, "type", "") for row in client._conversation_history] \
        == ["human", "ai"]
    plugin.memory_bridge.query_relevant_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_revocation_after_tool_read_discards_reply_and_tool_rows():
    """Revoked between the tool read and the end of the stream: the reply
    was generated FROM the recalled content, so it is discarded — and the
    rollback must walk PAST the tool-round dict rows (a type=='ai'-only
    loop stops at the first dict and leaves the recalled text in the
    shared history, feeding the digest and every later turn)."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)
    context = _group_context()

    async def _script(client, message):
        result = await client.on_tool_call(_recall_tool_call({"query": "群规"}))
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.extend(
            _tool_round_rows(result.output_as_json_string())
        )
        client._conversation_history.append(
            SimpleNamespace(type="ai", content="按群规是不剧透哦")
        )
        client.reply_chunks_ref.append("按群规是不剧透哦")
        # ……生成收尾前，管理员把群记忆关掉。
        plugin._qq_settings["group_memory_enabled"] = False

    client = _RecallToolClient(_script)
    reply_chunks: list = []
    client.reply_chunks_ref = reply_chunks
    user_data = {"lock": asyncio.Lock()}
    result = await service._run_session_generation(
        context=context,
        session_key="group:7788",
        user_data=user_data,
        user_session=client,
        reply_chunks=reply_chunks,
    )
    assert not result
    assert reply_chunks == []
    # 历史回滚到 history_before + 本轮 human 行（用户自己的发言保留）；
    # 任何一行都不得再含召回原文。
    history = client._conversation_history
    assert [getattr(row, "type", "") for row in history] == ["human"]
    assert all(
        "群规是不剧透" not in str(getattr(row, "content", "") or "")
        and "群规是不剧透" not in json.dumps(row, ensure_ascii=False, default=str)
        for row in history
    )
    assert user_data["current_turn_ai_row"] is None


# ---------------------------------------------------------------------------
# Outbound hygiene (连带 #6) and timeout budget (连带 #7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_tool_text_never_reaches_the_outbound_message():
    """The model says "我查一下" before calling the tool. That segment is
    persisted by the client into the assistant tool_calls row (and swept
    with it) — the QQ message the group receives must contain only the
    post-tool answer."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)

    async def _script(client, message):
        client.reply_chunks_ref.append("我查一下")
        result = await client.on_tool_call(_recall_tool_call({"query": "群规"}))
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.extend(
            _tool_round_rows(result.output_as_json_string())
        )
        client._conversation_history.append(
            SimpleNamespace(type="ai", content="查到了，是不剧透")
        )
        client.reply_chunks_ref.append("查到了，是不剧透")

    client = _RecallToolClient(_script)
    reply_chunks: list = []
    client.reply_chunks_ref = reply_chunks
    result = await service._run_session_generation(
        context=_group_context(),
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=client,
        reply_chunks=reply_chunks,
    )
    assert result == "查到了，是不剧透"
    assert "我查一下" not in result


@pytest.mark.asyncio
async def test_tool_turn_timeout_covers_the_whole_tool_loop(monkeypatch):
    """An armed turn's worst case is two full LLM streams (initial +
    forced-finalize, max_tool_iterations=1) plus one recall HTTP. Keeping
    the single-stream budget would turn slow-but-succeeding tool turns
    into timeouts — and a timeout here discards the whole shared group
    session."""
    captured: list = []
    original_wait_for = asyncio.wait_for

    async def _capture_wait_for(awaitable, timeout=None):
        captured.append(timeout)
        return await original_wait_for(awaitable, timeout=timeout)

    # reply_generation_service 模块内引用的就是全局 asyncio 模块。
    monkeypatch.setattr(asyncio, "wait_for", _capture_wait_for)

    plugin = _tool_plugin()
    service = _generation_service(plugin)

    async def _quiet(client, message):
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )

    await service._run_session_generation(
        context=_group_context(),
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=_RecallToolClient(_quiet),
        reply_chunks=[],
    )
    assert captured[0] == 5 * 2 + RECALL_TOOL_HTTP_TIMEOUT_SECONDS

    captured.clear()
    await service._run_session_generation(
        context=_group_context(recall_via_tool=False),
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=_RecallToolClient(_quiet),
        reply_chunks=[],
    )
    assert captured[0] == 5


def test_plugin_session_clients_cap_tool_iterations_to_one():
    """One recall per turn: same budget as the old per-turn synchronous
    recall, and it bounds the armed-turn worst case the timeout above is
    sized for. The forced-finalize after the cap still feeds the recall
    result back, so the read is never wasted."""
    import inspect

    from plugin.plugins.qq_auto_reply import session_bootstrap_service as sbs

    source = inspect.getsource(sbs)
    constructions = source.count("OmniOfflineClient(")
    assert constructions == source.count("max_tool_iterations=1"), (
        "每个 OmniOfflineClient 构造点都必须显式 max_tool_iterations=1——"
        "漏掉的那个会话的工具轮最坏耗时是超时预算的 3 倍"
    )
    assert constructions >= 2


# ---------------------------------------------------------------------------
# Route capability fallback (连带 #8)
# ---------------------------------------------------------------------------


def test_route_capability_predicate_knows_the_free_proxy():
    from main_logic.omni_offline_client import route_supports_tool_calls

    assert route_supports_tool_calls(
        "free-model", "https://www.lanlan.app/text/v1",
    ) is False
    assert route_supports_tool_calls(
        "free-model", "https://lanlan.tech/text/v1",
    ) is False
    # 区域改写只动 URL：模型名单独命中也算免费路由。
    assert route_supports_tool_calls("free-model", "https://example.com/v1") is False
    assert route_supports_tool_calls(
        TOOL_CAPABLE_MODEL, TOOL_CAPABLE_BASE_URL,
    ) is True
    assert route_supports_tool_calls("", "") is True


def _build_stub_plugin(bridge):
    from tests.unit.test_group_memory_scopes import _default_i18n

    return SimpleNamespace(
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
        memory_bridge=bridge,
        _build_user_title=lambda *a, **k: "",
        _build_character_card_fields=lambda *a, **k: {},
        _should_use_memory_context=lambda *a, **k: True,
        _should_persist_memory=lambda *a, **k: True,
        _should_skip_direct_llm_fallback_for_images=lambda **kw: False,
        _fetch_login_status_payload=AsyncMock(return_value={}),
        _normalize_login_identity=lambda payload: ("online", "10000", "Neko"),
        _build_qq_session_instructions=AsyncMock(
            return_value=SimpleNamespace(
                system_prompt="系统提示词", core_memory_text="",
                cross_group_section="", cross_session_section="",
                used_member_subject=False,
                memory_context_used=False, scene_mode="group_directed",
            )
        ),
        _build_prompt_message=lambda *a, **k: "用户消息",
    )


def _build_config_manager(model, base_url):
    return SimpleNamespace(
        get_character_data=lambda: (
            "Master", "Neko", None, {}, None, {}, None, None, None,
        ),
        get_model_api_config=lambda kind: {
            "model": model, "base_url": base_url, "api_key": "k",
        },
    )


@pytest.mark.asyncio
async def test_free_route_falls_back_to_synchronous_recall(monkeypatch):
    """Providers that silently drop ``tools`` (the free proxy) must keep
    the pre-generation synchronous recall — flipping them to tool-call
    mode would zero group memory for those users with no error anywhere."""
    from plugin.plugins.qq_auto_reply import reply_context_node as rcn

    bridge = _mock_bridge()
    plugin = _build_stub_plugin(bridge)
    monkeypatch.setattr(
        rcn, "get_config_manager",
        lambda: _build_config_manager(
            "free-model", "https://www.lanlan.app/text/v1",
        ),
    )
    node = rcn.QQReplyContextNode.__new__(rcn.QQReplyContextNode)
    node.plugin = plugin

    context = await node.build(
        message="群规是什么？",
        permission_level="user",
        sender_id="2046",
        is_group=True,
        group_id="7788",
        use_memory_context=True,
    )
    assert context.recall_via_tool is False
    bridge.query_relevant_memory.assert_awaited_once()
    assert "群规是不剧透" in context.recalled_memory_text

    # 回落路径的 consent 复检依旧生效：构建期间已 opt-out 的群不发起召回。
    bridge.query_relevant_memory.reset_mock()
    plugin._qq_settings["group_memory_enabled"] = False
    context = await node.build(
        message="群规是什么？",
        permission_level="user",
        sender_id="2046",
        is_group=True,
        group_id="7788",
        use_memory_context=True,
    )
    bridge.query_relevant_memory.assert_not_awaited()
    assert context.recalled_memory_text == ""


@pytest.mark.asyncio
async def test_tool_capable_route_defers_recall_to_the_model(monkeypatch):
    from plugin.plugins.qq_auto_reply import reply_context_node as rcn

    bridge = _mock_bridge()
    plugin = _build_stub_plugin(bridge)
    monkeypatch.setattr(
        rcn, "get_config_manager",
        lambda: _build_config_manager(TOOL_CAPABLE_MODEL, TOOL_CAPABLE_BASE_URL),
    )
    node = rcn.QQReplyContextNode.__new__(rcn.QQReplyContextNode)
    node.plugin = plugin

    context = await node.build(
        message="群规是什么？",
        permission_level="user",
        sender_id="2046",
        is_group=True,
        group_id="7788",
        use_memory_context=True,
    )
    assert context.recall_via_tool is True
    bridge.query_relevant_memory.assert_not_awaited()
    assert context.recalled_memory_text == ""
    assert context.recalled_memory_used is False


@pytest.mark.asyncio
async def test_capability_probe_failure_degrades_to_fallback(monkeypatch):
    """When the route cannot be determined, choose the channel that is
    KNOWN to work — the synchronous recall."""
    from plugin.plugins.qq_auto_reply import reply_context_node as rcn

    bridge = _mock_bridge()
    plugin = _build_stub_plugin(bridge)

    def _boom(kind):
        raise RuntimeError("config store busy")

    config_manager = SimpleNamespace(
        get_character_data=lambda: (
            "Master", "Neko", None, {}, None, {}, None, None, None,
        ),
        get_model_api_config=_boom,
    )
    monkeypatch.setattr(rcn, "get_config_manager", lambda: config_manager)
    node = rcn.QQReplyContextNode.__new__(rcn.QQReplyContextNode)
    node.plugin = plugin

    context = await node.build(
        message="群规是什么？",
        permission_level="user",
        sender_id="2046",
        is_group=True,
        group_id="7788",
        use_memory_context=True,
    )
    assert context.recall_via_tool is False
    bridge.query_relevant_memory.assert_awaited_once()


# ---------------------------------------------------------------------------
# Stale-route sessions rebuild onto the current provider
# ---------------------------------------------------------------------------


def test_session_reuse_compares_the_stored_creation_route():
    """A cached session outliving a provider switch answers on the retired
    provider indefinitely (busy groups never idle out) — and after a
    free→tool-capable switch the turn has NO recall channel at all: the
    context skips the synchronous recall per the new config while the arm
    step refuses the old client's route. The reuse predicate must compare
    the CURRENT config route against the route stored at creation time —
    never the live client's attributes, which a vision turn legitimately
    switches mid-session."""
    from plugin.plugins.qq_auto_reply.session_bootstrap_service import (
        generation_session_is_reusable,
    )

    free_route = ("https://www.lanlan.app/text/v1", "free-model")
    new_route = (TOOL_CAPABLE_BASE_URL, TOOL_CAPABLE_MODEL)
    entry = {
        "login_self_id": "10000",
        "her_name": "Neko",
        "conversation_route": free_route,
        # 图片轮把 client 合法切到 vision 模型：现值≠创建线路。
        "session": SimpleNamespace(
            model="vision-x", base_url="https://vision.example.com/v1",
        ),
    }
    common = dict(login_self_id="10000", her_name="Neko")

    assert generation_session_is_reusable(
        entry, conversation_route=new_route, **common,
    ) is False
    assert generation_session_is_reusable(
        entry, conversation_route=free_route, **common,
    ) is True  # 指纹比对用创建线路，vision 切换过的 client 不被误重建
    # 线路未知（配置读取失败 / 旧条目没存指纹）：跳过比对，不误杀。
    assert generation_session_is_reusable(
        entry, conversation_route=None, **common,
    ) is True
    legacy_entry = {"login_self_id": "10000", "her_name": "Neko"}
    assert generation_session_is_reusable(
        legacy_entry, conversation_route=new_route, **common,
    ) is True


@pytest.mark.asyncio
async def test_bootstrap_rebuilds_stale_route_session(monkeypatch):
    from plugin.plugins.qq_auto_reply import session_bootstrap_service as sbs

    new_route = (TOOL_CAPABLE_BASE_URL, TOOL_CAPABLE_MODEL)
    built = []

    class _StubClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.base_url = kwargs.get("base_url")
            self.model = kwargs.get("model")
            built.append(self)

        async def connect(self, instructions=""):
            return None

    monkeypatch.setattr(sbs, "OmniOfflineClient", _StubClient)
    monkeypatch.setattr(
        sbs, "get_config_manager",
        lambda: SimpleNamespace(
            aensure_region_resolved=AsyncMock(),
            get_model_api_config=lambda kind: {
                "base_url": new_route[0], "model": new_route[1], "api_key": "k",
            },
        ),
    )

    stale_entry = {
        "login_self_id": "10000",
        "her_name": "Neko",
        "conversation_route": ("https://www.lanlan.app/text/v1", "free-model"),
        "session": SimpleNamespace(model="free-model"),
    }
    discard = AsyncMock(side_effect=lambda key, reason: (
        plugin._user_sessions.pop(key, None) is not None
    ))
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": stale_entry},
        _ai_connect_timeout_seconds=5,
        logger=MagicMock(),
        session_runtime_service=SimpleNamespace(discard_session=discard),
    )
    context = SimpleNamespace(
        ephemeral_session=False,
        login_self_id="10000",
        her_name="Neko",
        system_prompt="系统提示词",
        character_card_fields={},
        persist_memory=True,
        memory_context_used=False,
        sender_id="2046",
        permission_level="user",
        is_group=True,
        group_id="7788",
        user_title="群友",
        user_nickname=None,
        login_status="online",
        login_nickname="Neko",
    )
    service = sbs.QQSessionBootstrapService(plugin)
    created = await service.ensure_generation_session(context, "group:7788")
    # 旧线路会话被结算丢弃，新会话建在当前线路上、存了新指纹。
    discard.assert_awaited_once()
    assert created is not stale_entry
    assert created["conversation_route"] == new_route
    assert built and built[-1].base_url == new_route[0]

    # 线路一致的下一轮：原样复用，不再重建。
    discard.reset_mock()
    reused = await service.ensure_generation_session(context, "group:7788")
    assert reused is created
    discard.assert_not_awaited()


# ---------------------------------------------------------------------------
# Bridge: time passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_forwards_time_spec_and_allows_time_only():
    bridge = QQMemoryBridge(SimpleNamespace(logger=MagicMock()))
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"results": [], "elapsed_ms": 1.0})
    client = MagicMock()
    client.post = AsyncMock(return_value=response)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(QQMemoryBridge, "_client", staticmethod(lambda: client))
        await bridge.query_relevant_memory(
            "Neko", "旅行", subjects=[QQMemoryBridge.group_subject("7788")],
            time_spec="2026-05",
        )
        payload = client.post.await_args.kwargs["json"]
        assert payload["time"] == "2026-05"
        assert payload["query"] == "旅行"

        # time-only（纯时间回溯）也要放行。
        client.post.reset_mock()
        await bridge.query_relevant_memory(
            "Neko", "", subjects=[QQMemoryBridge.group_subject("7788")],
            time_spec="2026-05-01",
        )
        payload = client.post.await_args.kwargs["json"]
        assert payload["time"] == "2026-05-01"

        # 空 subjects 列表照旧 fail-closed，不因带了 time 而放行。
        client.post.reset_mock()
        result = await bridge.query_relevant_memory(
            "Neko", "旅行", subjects=[], time_spec="2026-05",
        )
        client.post.assert_not_awaited()
        assert result.text == ""


# ---------------------------------------------------------------------------
# Shared subject resolver: the two read channels must agree
# ---------------------------------------------------------------------------


def test_fallback_recall_shares_the_subject_resolver():
    """The fallback recall and the tool handler must authorize identical
    scopes — enforced by both calling resolve_group_recall_subjects. A
    re-inlined copy in either path is where the scopes would drift."""
    import inspect

    from plugin.plugins.qq_auto_reply import memory_tool_service as mts
    from plugin.plugins.qq_auto_reply import reply_context_node as rcn

    assert "resolve_group_recall_subjects" in inspect.getsource(
        rcn.QQReplyContextNode._build_recalled_memory_text
    )
    assert "resolve_group_recall_subjects" in inspect.getsource(
        mts.QQMemoryToolService.execute_recall
    )

    plugin = _tool_plugin()
    subjects, used_member = resolve_group_recall_subjects(
        plugin, group_id="7788", memory_sender_id="  2046  ",
    )
    assert subjects == [
        QQMemoryBridge.group_subject("7788"),
        QQMemoryBridge.group_participant_subject("7788", "2046"),
    ]
    assert used_member is True
