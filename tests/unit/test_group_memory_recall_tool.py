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
6. model-authored pre-tool text remains in the outbound message, and routes that
   silently drop ``tools`` fall back to the synchronous recall.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
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
from plugin.plugins.qq_auto_reply.prompting import QQAutoReplyPromptingMixin

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
        self.on_tool_round_start = None
        self.armed_tool_names: list[list[str]] = []

    def set_tools(self, tool_definitions):
        self.tools = list(tool_definitions or [])
        self.armed_tool_names.append([t.name for t in self.tools])

    def set_tool_call_handler(self, handler):
        self.on_tool_call = handler

    def set_tool_round_start_callback(self, handler):
        self.on_tool_round_start = handler

    async def stream_text(self, message):
        await self._script(self, message)


def _generation_service(plugin):
    service = QQReplyGenerationService.__new__(QQReplyGenerationService)
    service.plugin = plugin
    return service


def test_hardcoded_recall_filler_remains_removed():
    """The retired mechanical recall phrase must not return under another hook."""
    repo_root = Path(__file__).resolve().parents[2]
    runtime_paths = [repo_root / "config/prompts/prompts_memory.py"]
    runtime_paths.extend(sorted((repo_root / "main_logic/core").glob("*.py")))
    runtime_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in runtime_paths
    )

    for retired_marker in (
        "RECALL_MEMORY_TOOL_FILLER",
        "_RECALL_FILLER_SID_SUFFIX",
        "::recall-filler",
        "让我回忆一下哦……",
    ):
        assert retired_marker not in runtime_sources


def _recall_tool_call(arguments):
    return SimpleNamespace(
        name="recall_memory", arguments=arguments, call_id="call_1",
    )


def _tool_round_rows(recall_output, assistant_content="我查一下"):
    return [
        {
            "role": "assistant",
            "content": assistant_content,
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
    _, consumed = await plugin.memory_tool_service.execute_recall(
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
    # 私聊 legacy 语料不受群开关管辖：无运行时 consent 依赖要记——
    # 但"召回被消费"的标志与 consent 解耦，私聊命中也要记 used。
    assert consumed == {}
    assert "群规是不剧透" in output
    assert context.recalled_memory_used is True


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
async def test_private_participant_turn_refreshes_empty_memory_prompt():
    """A reused private participant session must not retain scoped memory
    from its creation prompt when the current turn has no memory dependency."""
    from utils.llm_client import SystemMessage

    plugin = _tool_plugin(settings={
        "private_participant_memory_enabled": True,
        "allow_cross_group_context": True,
    })
    service = _generation_service(plugin)
    seen_prompts: list[str] = []

    async def _script(client, message):
        seen_prompts.append(client._conversation_history[0].content)
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.append(
            SimpleNamespace(type="ai", content="fresh reply")
        )

    original = SystemMessage(content="cached scoped memory")
    client = _RecallToolClient(_script)
    client._conversation_history = [original]
    client._instructions = original.content
    context = _group_context(
        is_group=False,
        group_id=None,
        permission_level="trusted",
        system_prompt="fresh prompt without memory",
        recalled_memory_text="",
        core_memory_text="",
        cross_session_section="",
        cross_group_section="",
        participant_memory_enabled=True,
        private_memory_mode="participant",
        used_member_subject=False,
    )

    await service._run_session_generation(
        context=context,
        session_key="private:2046",
        user_data={
            "lock": asyncio.Lock(),
            "private_memory_mode": "participant",
            "memory_enabled": True,
        },
        user_session=client,
        reply_chunks=[],
    )

    assert seen_prompts == ["fresh prompt without memory"]
    assert client._conversation_history[0] is original
    assert client._instructions == original.content


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
    assert client.on_tool_round_start is None

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
    assert client.on_tool_round_start is None


@pytest.mark.asyncio
async def test_final_disarm_clears_handler_when_tool_cleanup_fails():
    """Final cleanup must reset each mounted slot independently."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)

    class _StickyToolClient(_RecallToolClient):
        def set_tools(self, tool_definitions):
            if tool_definitions is None:
                raise RuntimeError("tool slot refused cleanup")
            super().set_tools(tool_definitions)

    async def _quiet(client, message):
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )

    client = _StickyToolClient(_quiet)
    await service._run_session_generation(
        context=_group_context(),
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=client,
        reply_chunks=[],
    )
    assert client.on_tool_call is None
    assert client.on_tool_round_start is None


@pytest.mark.asyncio
async def test_arm_installs_the_tool_whatever_the_route():
    """Arming does not inspect the client's route at all.

    The free proxy used to be classified as tool-less, which pushed those
    turns onto a build-time recall. That fallback is gone — every turn's
    only recall channel is this tool — so a route-shaped check returning
    False here would leave the turn with no memory whatsoever."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)

    free_client = _RecallToolClient(
        AsyncMock(), model="free-model",
        base_url="https://www.lanlan.app/text/v1",
    )
    assert service._arm_recall_tool(
        context=_group_context(),
        user_session=free_client,
        consent_before={},
    ) is True
    assert free_client.armed_tool_names[-1] == ["recall_memory"]

    # 记忆政策关着：本轮压根不该读记忆，是唯一还会拦住挂载的业务条件。
    capable_client = _RecallToolClient(AsyncMock())
    assert service._arm_recall_tool(
        context=_group_context(use_memory_context=False),
        user_session=capable_client,
        consent_before={},
    ) is False
    assert capable_client.armed_tool_names == []

    # A legacy client stub without the tooling surface degrades quietly.
    assert service._arm_recall_tool(
        context=_group_context(),
        user_session=SimpleNamespace(model=TOOL_CAPABLE_MODEL,
                                     base_url=TOOL_CAPABLE_BASE_URL),
        consent_before={},
    ) is False

    assert service._arm_recall_tool(
        context=_group_context(),
        user_session=capable_client,
        consent_before={},
    ) is True
    assert capable_client.armed_tool_names[-1] == ["recall_memory"]
    assert capable_client.on_tool_call is not None


def test_failed_arm_clears_tool_slots_independently():
    """One failing cleanup action must not leave the other slot mounted."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)
    calls: list[str] = []

    class _HalfMountClient(_RecallToolClient):
        def set_tools(self, tool_definitions):
            if tool_definitions is None:
                calls.append("clear-tools")
                raise RuntimeError("tool slot refused cleanup")
            calls.append("set-tools")

        def set_tool_call_handler(self, handler):
            if handler is None:
                calls.append("clear-handler")
                return
            calls.append("set-handler")
            raise RuntimeError("handler mount failed")

    client = _HalfMountClient(AsyncMock())
    assert service._arm_recall_tool(
        context=_group_context(),
        user_session=client,
        consent_before={},
    ) is False
    assert calls == [
        "set-tools", "set-handler", "clear-tools", "clear-handler",
    ]


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
    await plugin.memory_tool_service.execute_recall(
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
# Outbound continuity (连带 #6) and timeout budget (连带 #7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_tool_text_remains_in_the_outbound_message():
    """QQ must preserve model-authored text emitted before a tool call."""
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
    user_data = {"lock": asyncio.Lock()}
    result = await service._run_session_generation(
        context=_group_context(),
        session_key="group:7788",
        user_data=user_data,
        user_session=client,
        reply_chunks=reply_chunks,
    )
    assert result == "我查一下查到了，是不剧透"
    history = client._conversation_history
    assert [getattr(row, "type", "") for row in history] == ["human", "ai"]
    assert history[-1].content == "我查一下查到了，是不剧透"
    assert user_data["current_pre_tool_text"] == "我查一下"


@pytest.mark.asyncio
async def test_primary_result_carries_the_pre_tool_boundary():
    """Postprocess receives the boundary captured from tool-round history."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)
    context = _group_context(ephemeral_session=False, group_scene_mode="")
    user_data = {
        "memory_enabled": False,
        "human_row_accepted": False,
    }
    client = SimpleNamespace(_conversation_history=[])
    reply_chunks: list[str] = []
    plugin.session_bootstrap_service = SimpleNamespace(
        ensure_generation_session=AsyncMock(return_value=user_data)
    )
    plugin.session_runtime_service = SimpleNamespace(
        build_generation_session_key=lambda _context: "group:7788",
        prime_generation_session_state=lambda *_args, **_kwargs: (
            client,
            reply_chunks,
        ),
    )

    async def _generate(**_kwargs):
        user_data["current_pre_tool_text"] = "literal <msg> prefix "
        return "literal <msg> prefix <msg><text>answer</text></msg>"

    service._run_session_generation = AsyncMock(side_effect=_generate)
    service._sync_memory_after_success = AsyncMock()

    result = await service.run_primary_session_call(context)

    assert result.pre_tool_text == "literal <msg> prefix "
    assert result.reply_text == (
        "literal <msg> prefix <msg><text>answer</text></msg>"
    )


@pytest.mark.asyncio
async def test_terminal_recovery_history_is_not_duplicated_on_tool_cleanup():
    """Callback-owned recovery already contains the complete visible turn."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)
    recovered = "我查一下。最终保留到这里。"

    async def _script(client, message):
        result = await client.on_tool_call(_recall_tool_call({"query": "群规"}))
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.extend(
            _tool_round_rows(
                result.output_as_json_string(),
                assistant_content="我查一下。",
            )
        )
        # RESPONSE_LENGTH_TRUNCATED callback owns this append.
        client._conversation_history.append(
            SimpleNamespace(type="ai", content=recovered)
        )
        client.reply_chunks_ref.append(recovered)

    client = _RecallToolClient(_script)
    reply_chunks: list[str] = []
    client.reply_chunks_ref = reply_chunks
    user_data = {"lock": asyncio.Lock()}

    result = await service._run_session_generation(
        context=_group_context(),
        session_key="group:7788",
        user_data=user_data,
        user_session=client,
        reply_chunks=reply_chunks,
    )

    assert result == recovered
    assert [getattr(row, "content", "") for row in client._conversation_history] == [
        "hi",
        recovered,
    ]
    assert user_data["current_pre_tool_text"] == "我查一下。"


@pytest.mark.asyncio
async def test_pre_tool_text_creates_history_row_without_a_final_segment():
    """A pre-tool-only reply must still have one durable assistant row."""
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
    assert result == "我查一下"
    history = client._conversation_history
    assert [getattr(row, "type", "") for row in history] == ["human", "ai"]
    assert history[-1].content == "我查一下"


@pytest.mark.asyncio
async def test_whitespace_pre_tool_text_does_not_create_a_history_row():
    """Provider-only whitespace must not become a phantom assistant turn."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)

    async def _script(client, message):
        result = await client.on_tool_call(_recall_tool_call({"query": "群规"}))
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.extend(
            _tool_round_rows(
                result.output_as_json_string(), assistant_content="\n",
            )
        )

    client = _RecallToolClient(_script)
    result = await service._run_session_generation(
        context=_group_context(),
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=client,
        reply_chunks=[],
    )
    assert result == ""
    assert [
        getattr(row, "type", "") for row in client._conversation_history
    ] == ["human"]


@pytest.mark.asyncio
async def test_repeated_pre_tool_prefix_is_preserved_in_history():
    """Equal text in separate stream segments must not be deduplicated."""
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
            SimpleNamespace(type="ai", content="我查一下，结果是不剧透")
        )
        client.reply_chunks_ref.append("我查一下，结果是不剧透")

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
    assert result == "我查一下我查一下，结果是不剧透"
    history = client._conversation_history
    assert [getattr(row, "type", "") for row in history] == ["human", "ai"]
    assert history[-1].content == "我查一下我查一下，结果是不剧透"


@pytest.mark.asyncio
async def test_exact_outbound_separator_is_preserved_in_history():
    """Provider history trimming must not join separate English segments."""
    plugin = _tool_plugin()
    plugin._sanitize_generated_reply = (
        QQAutoReplyPromptingMixin._sanitize_generated_reply
    )
    service = _generation_service(plugin)

    async def _script(client, message):
        client.reply_chunks_ref.append("Let me check. ")
        result = await client.on_tool_call(_recall_tool_call({"query": "rule"}))
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.extend(
            _tool_round_rows(
                result.output_as_json_string(),
                assistant_content="Let me check.",
            )
        )
        client._conversation_history.append(
            SimpleNamespace(type="ai", content="The answer is 42.")
        )
        client.reply_chunks_ref.append("The answer is 42.")

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
    assert result == "Let me check. The answer is 42."
    assert client._conversation_history[-1].content == result


@pytest.mark.asyncio
async def test_hidden_pre_tool_reasoning_is_not_persisted():
    """QQ-only hidden tags must be absent from delivery and shared history."""
    plugin = _tool_plugin()
    plugin._sanitize_generated_reply = (
        QQAutoReplyPromptingMixin._sanitize_generated_reply
    )
    service = _generation_service(plugin)
    hidden = "<thinking_reasoning>secret</thinking_reasoning>"

    async def _script(client, message):
        client.reply_chunks_ref.append(hidden)
        result = await client.on_tool_call(_recall_tool_call({"query": "rule"}))
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.extend(
            _tool_round_rows(
                result.output_as_json_string(), assistant_content=hidden,
            )
        )

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
    assert plugin._sanitize_generated_reply(result) == ""
    assert [
        getattr(row, "type", "") for row in client._conversation_history
    ] == ["human"]


@pytest.mark.asyncio
async def test_dangling_thinking_close_uses_full_turn_sanitizer_context():
    """A hidden prefix must not leak when only the full turn proves it hidden."""
    plugin = _tool_plugin()
    plugin._sanitize_generated_reply = (
        QQAutoReplyPromptingMixin._sanitize_generated_reply
    )
    service = _generation_service(plugin)
    hidden_prefix = "secret</thinking_reasoning> "
    final_xml = "<msg><text>answer</text></msg>"

    async def _script(client, message):
        client.reply_chunks_ref.append(hidden_prefix)
        await client.on_tool_round_start()
        result = await client.on_tool_call(
            _recall_tool_call({"query": "rule"})
        )
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.extend(
            _tool_round_rows(
                result.output_as_json_string(),
                assistant_content=hidden_prefix,
            )
        )
        client._conversation_history.append(
            SimpleNamespace(type="ai", content=final_xml)
        )
        client.reply_chunks_ref.append(final_xml)

    client = _RecallToolClient(_script)
    reply_chunks: list[str] = []
    client.reply_chunks_ref = reply_chunks
    user_data = {"lock": asyncio.Lock()}

    result = await service._run_session_generation(
        context=_group_context(),
        session_key="group:7788",
        user_data=user_data,
        user_session=client,
        reply_chunks=reply_chunks,
    )

    assert plugin._sanitize_generated_reply(result) == final_xml
    assert client._conversation_history[-1].content == final_xml
    assert user_data["current_pre_tool_text"] == ""


@pytest.mark.asyncio
async def test_sanitized_empty_terminal_recovery_is_removed_from_history():
    """A recovered turn hidden by QQ must not survive only in history."""
    plugin = _tool_plugin()
    plugin._sanitize_generated_reply = (
        QQAutoReplyPromptingMixin._sanitize_generated_reply
    )
    service = _generation_service(plugin)
    hidden = "<thinking_reasoning>secret</thinking_reasoning>"

    async def _script(client, message):
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        # Terminal RESPONSE_LENGTH_TRUNCATED callback owns both appends.
        client._conversation_history.append(
            SimpleNamespace(type="ai", content=hidden)
        )
        client.reply_chunks_ref.append(hidden)

    client = _RecallToolClient(_script)
    reply_chunks: list[str] = []
    client.reply_chunks_ref = reply_chunks
    user_data = {"lock": asyncio.Lock()}

    result = await service._run_session_generation(
        context=_group_context(),
        session_key="group:7788",
        user_data=user_data,
        user_session=client,
        reply_chunks=reply_chunks,
    )

    assert plugin._sanitize_generated_reply(result) == ""
    assert [row.type for row in client._conversation_history] == ["human"]
    assert user_data["current_turn_ai_row"] is None


@pytest.mark.asyncio
async def test_delayed_thinking_residual_completes_the_tool_boundary():
    """Persisted tool text fills a prefix emitted after round-start capture."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)
    residual = "literal <msg> example remains text"

    async def _script(client, message):
        assert callable(client.on_tool_round_start)
        await client.on_tool_round_start()
        # ThinkingStreamStripper.flush() emits this only after the sentinel.
        client.reply_chunks_ref.append(residual)
        result = await client.on_tool_call(
            _recall_tool_call({"query": "rule"})
        )
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.extend(
            _tool_round_rows(
                result.output_as_json_string(),
                assistant_content=residual,
            )
        )

    client = _RecallToolClient(_script)
    reply_chunks: list[str] = []
    client.reply_chunks_ref = reply_chunks
    user_data = {"lock": asyncio.Lock()}

    result = await service._run_session_generation(
        context=_group_context(),
        session_key="group:7788",
        user_data=user_data,
        user_session=client,
        reply_chunks=reply_chunks,
    )

    assert result == residual
    assert client._conversation_history[-1].content == residual
    assert user_data["current_pre_tool_text"] == residual


@pytest.mark.asyncio
async def test_discarded_attempt_resets_the_structural_tool_boundary():
    """A winning non-tool retry must not inherit the rejected tool boundary."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)
    winning = "literal <msg> example remains text"
    attempt_state = {"discard_epoch": 0}

    async def _script(client, message):
        # Rejected attempt entered a tool round after emitting this prefix.
        client.reply_chunks_ref.append(winning)
        await client.on_tool_round_start()
        # on_response_discarded owns both operations; the successful reroll
        # happens to start with the same text but never enters a tool round.
        client.reply_chunks_ref.clear()
        attempt_state["discard_epoch"] += 1
        client.reply_chunks_ref.append(winning)
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.append(
            SimpleNamespace(type="ai", content=winning)
        )

    client = _RecallToolClient(_script)
    reply_chunks: list[str] = []
    client.reply_chunks_ref = reply_chunks
    user_data = {
        "lock": asyncio.Lock(),
        "reply_attempt_state": attempt_state,
    }

    result = await service._run_session_generation(
        context=_group_context(),
        session_key="group:7788",
        user_data=user_data,
        user_session=client,
        reply_chunks=reply_chunks,
    )

    assert result == winning
    assert client._conversation_history[-1].content == winning
    assert user_data["current_pre_tool_text"] == ""


@pytest.mark.asyncio
async def test_terminal_recovery_retains_a_zero_execution_tool_boundary():
    """Successful truncation recovery stays in the same boundary epoch."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)
    prefix = "literal <msg> example remains text: "
    recovered = prefix + "final"
    attempt_state = {"discard_epoch": 0}

    async def _script(client, message):
        client.reply_chunks_ref.append(prefix)
        await client.on_tool_round_start()
        # Terminal recovery replaces the raw chunks but is not a reroll.
        client.reply_chunks_ref.clear()
        client.reply_chunks_ref.append(recovered)
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.append(
            SimpleNamespace(type="ai", content=recovered)
        )

    client = _RecallToolClient(_script)
    reply_chunks: list[str] = []
    client.reply_chunks_ref = reply_chunks
    user_data = {
        "lock": asyncio.Lock(),
        "reply_attempt_state": attempt_state,
    }

    result = await service._run_session_generation(
        context=_group_context(),
        session_key="group:7788",
        user_data=user_data,
        user_session=client,
        reply_chunks=reply_chunks,
    )

    assert result == recovered
    assert client._conversation_history[-1].content == recovered
    assert user_data["current_pre_tool_text"] == prefix


@pytest.mark.asyncio
async def test_pre_tool_text_remains_when_no_tool_call_executes():
    """A nameless tool fragment must not make QQ discard prior model text."""
    plugin = _tool_plugin()
    service = _generation_service(plugin)

    async def _script(client, message):
        client.reply_chunks_ref.append("literal <msg> prefix ")
        assert callable(client.on_tool_round_start)
        await client.on_tool_round_start()
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        # forced-finalize 出的最终文本。
        client._conversation_history.append(
            SimpleNamespace(type="ai", content="最终回答")
        )
        client.reply_chunks_ref.append("最终回答")

    client = _RecallToolClient(_script)
    reply_chunks: list = []
    client.reply_chunks_ref = reply_chunks
    user_data = {"lock": asyncio.Lock()}
    result = await service._run_session_generation(
        context=_group_context(),
        session_key="group:7788",
        user_data=user_data,
        user_session=client,
        reply_chunks=reply_chunks,
    )
    assert result == "literal <msg> prefix 最终回答"
    assert client._conversation_history[-1].content == result
    assert user_data["current_pre_tool_text"] == "literal <msg> prefix "
    assert client.on_tool_round_start is None
    plugin.memory_bridge.query_relevant_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_rows_stripped_even_when_generation_raises():
    """P3-2：strip 在 finally 的位置此前没有被测试钉住——把它挪成"只在
    正常返回路径 strip"，全部既有用例仍绿。这里让 stream_text 中途抛异常，
    断言 tool dict 行仍被清出共享历史：异常路径下含 scoped 记忆原文的
    tool result 永久留在群历史里，会进 digest、进后续每一轮的上下文，
    member 撤销后也无法再摘除。"""  # noqa: DOCSTRING_CJK
    plugin = _tool_plugin()
    service = _generation_service(plugin)

    async def _script(client, message):
        result = await client.on_tool_call(_recall_tool_call({"query": "群规"}))
        client._conversation_history.append(
            SimpleNamespace(type="human", content=message)
        )
        client._conversation_history.extend(
            _tool_round_rows(result.output_as_json_string())
        )
        raise RuntimeError("provider disconnected mid-stream")

    client = _RecallToolClient(_script)
    reply_chunks: list = []
    client.reply_chunks_ref = reply_chunks
    with pytest.raises(RuntimeError):
        await service._run_session_generation(
            context=_group_context(),
            session_key="group:7788",
            user_data={"lock": asyncio.Lock()},
            user_session=client,
            reply_chunks=reply_chunks,
        )
    history = client._conversation_history
    assert [getattr(row, "type", "") for row in history] == ["human"], (
        f"异常路径下 tool dict 行必须被清出共享历史，实际: {history!r}"
    )
    assert all(
        "群规是不剧透" not in json.dumps(row, ensure_ascii=False, default=str)
        for row in history
    )


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
        context=_group_context(use_memory_context=False),
        session_key="group:7788",
        user_data={"lock": asyncio.Lock()},
        user_session=_RecallToolClient(_quiet),
        reply_chunks=[],
    )
    assert captured[0] == 5


def test_plugin_session_clients_cap_tool_iterations_to_one():
    """One recall per turn: that cap bounds the armed-turn worst case the
    timeout above is sized for. The forced-finalize after the cap still
    feeds the recall result back, so the read is never wasted.

    AST 而非源码字面量计数：旧写法比较两个字符串的出现次数，任何后续
    PR 在注释/docstring 里写下 "OmniOfflineClient(" 或
    "max_tool_iterations=1" 都会无谓变红。这里找到真正的构造 Call 节点，
    逐个检查 keyword。"""  # noqa: DOCSTRING_CJK
    import ast
    import inspect

    from plugin.plugins.qq_auto_reply import session_bootstrap_service as sbs

    tree = ast.parse(inspect.getsource(sbs))
    constructions = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name)
             and node.func.id == "OmniOfflineClient")
            or (isinstance(node.func, ast.Attribute)
                and node.func.attr == "OmniOfflineClient")
        )
    ]
    assert len(constructions) >= 1, "未找到 OmniOfflineClient 构造点"
    for call in constructions:
        assert any(
            kw.arg == "max_tool_iterations"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value == 1
            for kw in call.keywords
        ), (
            f"第 {call.lineno} 行的 OmniOfflineClient 构造点必须显式 "
            "max_tool_iterations=1——漏掉的那个会话的工具轮最坏耗时是"
            "超时预算的 3 倍"
        )


# ---------------------------------------------------------------------------
# Retired: the route capability gate (free proxy forwards tools now)
# ---------------------------------------------------------------------------


def test_offline_client_grows_no_route_capability_gate():
    """No "does this route support tools" predicate may come back.

    One existed while lanlan's free proxy silently dropped ``tools``: QQ
    consulted it and pushed those turns onto a build-time recall. The
    proxy forwards tools now and that fallback is deleted, so a predicate
    answering False would no longer mean "use the other channel" — it
    would mean the turn gets no memory at all, silently.
    """
    import main_logic.omni_offline_client as ooc

    assert [
        name for name in dir(ooc)
        if "supports_tool" in name or "free_route" in name
    ] == []


@pytest.mark.asyncio
async def test_recall_header_counts_entries_not_lines():
    """A single multiline reflection must not inflate the header into
    "found N memories". Memory text is preserved verbatim and can itself
    contain lines shaped like "2. ..." — so the count comes from the
    renderer's kept-entry tally, never from re-parsing the rendered
    text."""
    from config.prompts.prompts_memory import RECALL_MEMORY_TOOL_FOUND_HEADER
    from config.prompts.prompts_sys import _loc

    plugin = _tool_plugin()
    # 对抗样例：一条 reflection 的原文自带 "2. " 开头的行。
    plugin.memory_bridge.query_relevant_memory = AsyncMock(
        return_value=QQMemoryQueryResult(
            text="1. [事实] 第一行\n2. 原文里的编号行\n第三行",
            hit_count=1, rendered_count=1,
        ),
    )
    output, _ = await plugin.memory_tool_service.execute_recall(
        context=_group_context(), arguments={"query": "群规"},
    )
    lang = plugin.memory_tool_service._short_lang()
    assert output.startswith(
        _loc(RECALL_MEMORY_TOOL_FOUND_HEADER, lang).format(n=1)
    )

    # 渲染器侧：kept 计数经 out-param 带出（预算丢弃尾部条目时与
    # hit_count 不同），多行原文只算一条。
    bridge = QQMemoryBridge(SimpleNamespace(logger=MagicMock()))
    kept_out: list = []
    rendered = bridge.render_relevant_memory(
        [
            {"text": "第一行\n2. 原文里的编号行", "tier": "fact"},
            {"text": "另一条", "tier": "fact"},
        ],
        kept_count_out=kept_out,
    )
    assert kept_out == [2]
    assert rendered.startswith("1. ")


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


def _unreadable_config_manager():
    def _boom(kind):
        raise RuntimeError("config store busy")

    return SimpleNamespace(
        get_character_data=lambda: (
            "Master", "Neko", None, {}, None, {}, None, None, None,
        ),
        get_model_api_config=_boom,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make_config_manager",
    [
        pytest.param(
            lambda: _build_config_manager(
                "free-model", "https://www.lanlan.app/text/v1",
            ),
            id="free-proxy",
        ),
        pytest.param(
            lambda: _build_config_manager(
                TOOL_CAPABLE_MODEL, TOOL_CAPABLE_BASE_URL,
            ),
            id="tool-capable",
        ),
        pytest.param(_unreadable_config_manager, id="config-unreadable"),
    ],
)
async def test_context_build_never_spends_a_retrieval(
    monkeypatch, make_config_manager,
):
    """Building a turn's context must never cost a recall round-trip.

    Recall belongs to the model, mid-generation, through the tool. Doing
    it here would spend a retrieval (HTTP + prompt tokens) on EVERY turn
    — the build cannot know whether this reply needs memory at all. The
    route the turn happens to run on is not a reason to reintroduce one,
    and neither is a config store that refuses to answer: the answer to
    "I can't tell what the route is" is still to leave it to the tool.
    """
    from plugin.plugins.qq_auto_reply import reply_context_node as rcn

    bridge = _mock_bridge()
    plugin = _build_stub_plugin(bridge)
    monkeypatch.setattr(rcn, "get_config_manager", make_config_manager)
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

    bridge.query_relevant_memory.assert_not_awaited()
    assert context.recalled_memory_text == ""
    assert context.recalled_memory_used is False


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
            self._conversation_history = []
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

    # 被 core 判废的 attempt 不得污染下一次重试；成功重试自己的 pre-tool
    # 仍由同一个 delta 回调原样保留。
    on_text_delta = built[-1].kwargs["on_text_delta"]
    on_response_discarded = built[-1].kwargs["on_response_discarded"]
    await on_text_delta("旧 attempt", True)
    built[-1]._conversation_history.extend(
        _tool_round_rows("rejected scoped output")
    )
    await on_response_discarded("retry", 1, 3, True, None)
    assert created["reply_attempt_state"]["discard_epoch"] == 1
    assert built[-1]._conversation_history == []
    await on_text_delta("我查", True)
    await on_text_delta("一下", False)
    assert created["reply_chunks"] == ["我查", "一下"]

    # reroll 耗尽后的可读截断正文不会再走 on_text_delta；terminal discard
    # callback 必须以它替换被判废的流式分片，而不是把整轮清成空回复。
    await on_response_discarded(
        "length>300",
        3,
        3,
        False,
        json.dumps({
            "code": "RESPONSE_LENGTH_TRUNCATED",
            "text": "保留到最后一个完整句子。",
        }),
    )
    assert created["reply_attempt_state"]["discard_epoch"] == 1
    assert created["reply_chunks"] == ["保留到最后一个完整句子。"]
    assert [row.content for row in built[-1]._conversation_history] == [
        "保留到最后一个完整句子。"
    ]

    await on_text_delta("故障前半句", True)
    await on_response_discarded(
        "text_gen_error",
        1,
        1,
        False,
        json.dumps({
            "code": "TEXT_GEN_ERROR_AFTER_PARTIAL",
            "text": "不可当作恢复正文",
        }),
    )
    assert created["reply_chunks"] == []

    # 线路一致的下一轮：原样复用，不再重建。
    discard.reset_mock()
    reused = await service.ensure_generation_session(context, "group:7788")
    assert reused is created
    discard.assert_not_awaited()


@pytest.mark.asyncio
async def test_bootstrap_rejects_late_stale_private_mode(monkeypatch):
    """A queued old-permission turn can create a session after permission
    invalidation has already finished; the next current turn must still
    reject that unmarked stale-mode session."""
    from plugin.plugins.qq_auto_reply import session_bootstrap_service as sbs

    route = (TOOL_CAPABLE_BASE_URL, TOOL_CAPABLE_MODEL)
    config = SimpleNamespace(
        aensure_region_resolved=AsyncMock(),
        get_model_api_config=lambda kind: {
            "base_url": route[0], "model": route[1], "api_key": "k",
        },
    )
    monkeypatch.setattr(sbs, "get_config_manager", lambda: config)
    stale_entry = {
        "login_self_id": "10000",
        "her_name": "Neko",
        "conversation_route": route,
        "private_memory_mode": "legacy",
        "permission_level": "trusted",
        "session": SimpleNamespace(),
    }
    discard = AsyncMock(return_value=False)
    plugin = SimpleNamespace(
        _user_sessions={"private:2046": stale_entry},
        logger=MagicMock(),
        session_runtime_service=SimpleNamespace(discard_session=discard),
    )
    context = SimpleNamespace(
        ephemeral_session=False,
        login_self_id="10000",
        her_name="Neko",
        is_group=False,
        private_memory_mode="participant",
        permission_level="trusted",
    )

    service = sbs.QQSessionBootstrapService(plugin)
    assert await service.ensure_generation_session(
        context, "private:2046",
    ) is None
    discard.assert_awaited_once()
    assert stale_entry["pending_identity_discard"] is True



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


@pytest.mark.asyncio
async def test_group_read_paths_share_the_subject_resolver():
    """The tool handler AND the scoped bootstrap context must authorize
    identical scopes — enforced by both calling
    resolve_group_recall_subjects. A re-inlined copy in either path is
    where the scopes would drift (the bootstrap path WAS such a copy
    until the recent-speaker expansion collapsed it).

    AST 而非源码字符串：函数体内的 import 语句 / 注释同样含这个名字，
    字符串断言在「调用被内联掉、import 还留着」的变异下照样绿（变异
    验证抓到过）。这里找真正的 Call 节点。"""  # noqa: DOCSTRING_CJK
    import ast
    import inspect
    import textwrap

    from plugin.plugins.qq_auto_reply import memory_tool_service as mts
    from plugin.plugins.qq_auto_reply import session_instruction_service as sis

    def _calls_resolver(func) -> bool:
        tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
        return any(
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name)
                 and node.func.id == "resolve_group_recall_subjects")
                or (isinstance(node.func, ast.Attribute)
                    and node.func.attr == "resolve_group_recall_subjects")
            )
            for node in ast.walk(tree)
        )

    assert _calls_resolver(
        mts.QQMemoryToolService.execute_recall
    ), "tool handler 路径没有真正调用共享 resolver"
    assert _calls_resolver(
        sis.QQSessionInstructionService._build_core_memory_section
    ), "scoped bootstrap 路径没有真正调用共享 resolver"

    # 无 backlog_store（轻量 harness）：形状退化回 [群, 当前发言人]。
    plugin = _tool_plugin()
    subjects, used_member = await resolve_group_recall_subjects(
        plugin, group_id="7788", memory_sender_id="  2046  ",
    )
    assert subjects == [
        QQMemoryBridge.group_subject("7788"),
        QQMemoryBridge.group_participant_subject("7788", "2046"),
    ]
    assert used_member is True


@pytest.mark.asyncio
async def test_resolver_appends_recent_other_speakers():
    """读侧扩容：群 + 当前发言人 + 最近说过话的另外 3 人（新→旧、去重、
    排除当前发言人），群恒在最前（subjects 顺序 = 预算分配顺序）。"""  # noqa: DOCSTRING_CJK
    plugin = _tool_plugin()
    timeline = [
        # backlog 升序（旧→新）；有重复发言与当前发言人自己的消息。
        {"sender_id": "9001", "message_id": "m1", "timestamp": 1},
        {"sender_id": "9002", "message_id": "m2", "timestamp": 2},
        {"sender_id": "9001", "message_id": "m3", "timestamp": 3},
        {"sender_id": "2046", "message_id": "m4", "timestamp": 4},
        {"sender_id": "9003", "message_id": "m5", "timestamp": 5},
        {"sender_id": "9004", "message_id": "m6", "timestamp": 6},
        # 合成事件（入群通知）：名义 sender 没有说话，最新也不占槽位。
        {"sender_id": "9005", "message_id": "welcome_7788_9005_7",
         "timestamp": 7, "synthetic_source": "group_join_notice"},
        # 升级前的旧行：无 synthetic_source 字段，靠 "welcome_" id 前缀
        # 兜底识别，同样不占槽位。
        {"sender_id": "9006", "message_id": "welcome_7788_9006_8",
         "timestamp": 8},
        # 旧行 + 普通 message_id：字段缺失不等于合成，照常算发言人——
        # 但它比 9004 还新，会顶掉最旧的 9001。
        {"sender_id": "9007", "message_id": "m9", "timestamp": 9},
    ]
    plugin.backlog_store = SimpleNamespace(
        get_recent_group_messages=AsyncMock(return_value=timeline),
    )
    subjects, used_member = await resolve_group_recall_subjects(
        plugin, group_id="7788", memory_sender_id="2046",
    )
    assert used_member is True
    assert subjects == [
        QQMemoryBridge.group_subject("7788"),
        QQMemoryBridge.group_participant_subject("7788", "2046"),
        # 新→旧：合成事件的 9005（带字段）与 9006（legacy 行按 welcome_
        # 前缀兜底）被排除，然后 9007（legacy 普通行照常算）、9004、9003
        # （跳过当前发言人 2046，9001 被更新的发言人顶出前 3）。
        QQMemoryBridge.group_participant_subject("7788", "9007"),
        QQMemoryBridge.group_participant_subject("7788", "9004"),
        QQMemoryBridge.group_participant_subject("7788", "9003"),
    ]
    assert not any(
        s["subject_id"].endswith((":9005", ":9006")) for s in subjects
    ), "合成事件的关联用户被当成了最近发言人"
    # 5 个 subject 在读端点 1..8 上限之内。
    assert len(subjects) <= 8

    # member 门控整体覆盖最近发言人：当前发言人为空（合成轮 / member
    # 快照未授权）时，最近发言人也一并不带。
    subjects, used_member = await resolve_group_recall_subjects(
        plugin, group_id="7788", memory_sender_id="",
    )
    assert subjects == [QQMemoryBridge.group_subject("7788")]
    assert used_member is False

    # member 开关关闭时同理。
    plugin_off = _tool_plugin(settings={
        "group_memory_enabled": True,
        "group_member_memory_enabled": False,
    })
    plugin_off.backlog_store = SimpleNamespace(
        get_recent_group_messages=AsyncMock(return_value=timeline),
    )
    subjects, used_member = await resolve_group_recall_subjects(
        plugin_off, group_id="7788", memory_sender_id="2046",
    )
    assert subjects == [QQMemoryBridge.group_subject("7788")]
    assert used_member is False


@pytest.mark.asyncio
async def test_recent_speaker_scan_window_seed_and_synthetic_field():
    """三条读侧不变量一起钉：扫描窗口真的用满、去重种子是当前发言人、
    合成事件按**字段**识别。

    这三条此前都只有"恰好也通过"的覆盖：
    - 扫描窗口：夹具无视 limit 直接返回整条 timeline，把
      GROUP_RECALL_RECENT_SPEAKER_SCAN_LIMIT 改成 3 照样全绿；
    - 去重种子：当前发言人在旧夹具里排在第 4 新，``seen = set()``
      还没轮到它就已经凑够 3 人；
    - 合成事件：旧夹具里带 synthetic_source 字段的两行 message_id 都是
      "welcome_" 开头，被 legacy 前缀兜底完全掩护。

    这里让当前发言人霸占最新的一大段、真正的另外三人退到窗口深处、并放
    一条**不带 welcome_ 前缀**的合成行在最新处。"""  # noqa: DOCSTRING_CJK
    from config import GROUP_RECALL_RECENT_SPEAKER_SCAN_LIMIT

    plugin = _tool_plugin()
    # backlog 升序（旧→新）。
    timeline = [
        {"sender_id": "9003", "message_id": "m1", "timestamp": 1},
        {"sender_id": "9004", "message_id": "m2", "timestamp": 2},
        {"sender_id": "9007", "message_id": "m3", "timestamp": 3},
    ]
    # 当前发言人连发，把另外三人挤到窗口深处（活跃群里最常见的形态）。
    # 整条 timeline 正好 = 扫描窗口：窗口开满才够得着那三个人，窗口被改小
    # 就只剩当前发言人自己刷屏。
    filler = GROUP_RECALL_RECENT_SPEAKER_SCAN_LIMIT - len(timeline) - 1
    assert filler > 3, "夹具得比 limit-1 更长，否则窗口大小没有可观测后果"
    timeline += [
        {"sender_id": "2046", "message_id": f"m{4 + i}", "timestamp": 4 + i}
        for i in range(filler)
    ]
    # 最新一条是合成事件（入群通知），但 message_id 不带 "welcome_" 前缀
    # ——只有读 synthetic_source 字段的分支能挡住它。
    timeline.append({
        "sender_id": "9009", "message_id": "notice_7788_9009",
        "timestamp": GROUP_RECALL_RECENT_SPEAKER_SCAN_LIMIT,
        "synthetic_source": "group_join_notice",
    })
    assert len(timeline) == GROUP_RECALL_RECENT_SPEAKER_SCAN_LIMIT

    async def _recent(group_id, *, limit):
        # 真 backlog 按 limit 只返回最近的 N 条；夹具必须同样守约，否则
        # "窗口开多大"这件事在测试里根本没有可观测后果。
        assert limit == GROUP_RECALL_RECENT_SPEAKER_SCAN_LIMIT
        return timeline[-limit:]

    plugin.backlog_store = SimpleNamespace(
        get_recent_group_messages=AsyncMock(side_effect=_recent),
    )
    subjects, used_member = await resolve_group_recall_subjects(
        plugin, group_id="7788", memory_sender_id="2046",
    )
    assert used_member is True
    assert subjects == [
        QQMemoryBridge.group_subject("7788"),
        QQMemoryBridge.group_participant_subject("7788", "2046"),
        QQMemoryBridge.group_participant_subject("7788", "9007"),
        QQMemoryBridge.group_participant_subject("7788", "9004"),
        QQMemoryBridge.group_participant_subject("7788", "9003"),
    ]
    assert [s["subject_id"] for s in subjects].count("qq:7788:2046") == 1, (
        "当前发言人没进去重种子，被最近发言人名单又带进来一次"
    )
    assert not any(s["subject_id"].endswith(":9009") for s in subjects), (
        "带 synthetic_source 字段但 message_id 不含 welcome_ 前缀的合成行"
        "占了最近发言人槽位"
    )


@pytest.mark.asyncio
async def test_record_message_persists_synthetic_source():
    """resolver 的合成事件过滤读的是 backlog 行上的 synthetic_source——
    这条测试钉住写入侧真的把 pipeline 的 _synthetic_source 落了盘，否则
    过滤读的是一个没人写的字段（形同虚设）。"""  # noqa: DOCSTRING_CJK
    from plugin.plugins.qq_auto_reply.backlog_service import QQBacklogService

    captured: list = []

    async def _append(msg, **kwargs):
        captured.append(msg)

    plugin = SimpleNamespace(
        _sanitize_message_text=lambda text, is_reply_to_bot=False: text,
        permission_mgr=SimpleNamespace(
            get_permission_level=lambda s: "normal",
            get_nickname=lambda s: None,
        ),
        group_permission_mgr=SimpleNamespace(get_group_level=lambda g: "open"),
        backlog_store=SimpleNamespace(append_message=_append),
        _build_backlog_conversation_key=(
            lambda *, sender_id, is_group, group_id: f"group:{group_id}:{sender_id}"
        ),
        _qq_settings={},
        logger=MagicMock(),
    )
    service = QQBacklogService(plugin)

    await service.record_message({
        "message_type": "group", "user_id": "9005", "group_id": "7788",
        "content": "[系统] 新成员 9005 加入了群聊",
        "message_id": "welcome_7788_9005_1", "timestamp": 1,
        "_synthetic_source": "group_join_notice",
    })
    await service.record_message({
        "message_type": "group", "user_id": "9001", "group_id": "7788",
        "content": "我真的说了话", "message_id": "m1", "timestamp": 2,
    })
    assert [m.synthetic_source for m in captured] == [
        "group_join_notice", "",
    ]


@pytest.mark.asyncio
async def test_resolver_degrades_when_backlog_read_fails():
    """backlog 读挂了只降级（[群, 当前发言人]），不把整个召回搞挂。"""  # noqa: DOCSTRING_CJK
    plugin = _tool_plugin()
    plugin.backlog_store = SimpleNamespace(
        get_recent_group_messages=AsyncMock(
            side_effect=RuntimeError("backlog io error"),
        ),
    )
    subjects, used_member = await resolve_group_recall_subjects(
        plugin, group_id="7788", memory_sender_id="2046",
    )
    assert subjects == [
        QQMemoryBridge.group_subject("7788"),
        QQMemoryBridge.group_participant_subject("7788", "2046"),
    ]
    assert used_member is True
