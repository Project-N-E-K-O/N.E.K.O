from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest


def _make_client():
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import SystemMessage

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._proactive_image_to_inject = None
    client._proactive_image_staged_at = 0.0
    client._proactive_image_history_len = 0
    client.vision_provider_type = None
    client.llm = SimpleNamespace(max_completion_tokens=None)
    client.base_url = "free"
    client.lanlan_name = "Test"
    client.master_name = "User"
    client._prefix_buffer_size = 0
    client._conversation_history = [SystemMessage(content="system")]
    client._pending_images = []
    client._is_responding = False
    client._recent_responses = []
    client._repetition_threshold = 0.8
    client._max_recent_responses = 3
    client.max_response_length = 9999
    client.max_response_rerolls = 0
    client.max_tool_iterations = 1
    client.enable_response_guard = False
    client.enable_long_response_summary = False
    client.vision_model = ""
    client.model = "test-model"
    client._tool_definitions = []
    client.on_text_delta = None
    client.on_input_transcript = None
    client.on_response_discarded = None
    client.on_repetition_detected = None
    return client


@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("query_public_knowledge: 初音未来", "初音未来"),
        ("/knowledge 初音未来", "初音未来"),
        ("本地知识库：初音未来", "初音未来"),
        ("请查询公共知识库：初音未来", "初音未来"),
        ("请在本地知识库中查询：初音未来", "初音未来"),
        ("请在本地知识库中查询初音未来", "初音未来"),
    ],
)
def test_explicit_local_route_requires_a_complete_command(text, query):
    import main_logic.knowledge_context as knowledge_tool

    assert knowledge_tool._extract_explicit_local_knowledge_query(text) == query


@pytest.mark.parametrize(
    "text",
    [
        "联网搜索本地知识库技术的最新新闻",
        "解释‘本地知识库’这个概念",
        "比较公共知识库和网络搜索",
        "请结合本地知识库并联网核实",
    ],
)
def test_embedded_knowledge_words_do_not_claim_the_route(text):
    import main_logic.knowledge_context as knowledge_tool

    assert knowledge_tool._extract_explicit_local_knowledge_query(text) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_type", "forwarded_type", "expected_titles"),
    [
        ("knowledge", "knowledge", "Knowledge sample"),
        ("corpus", "corpus", "Corpus sample"),
        ("auto", None, "Knowledge sample,Corpus sample"),
        ("all", None, "Knowledge sample,Corpus sample"),
    ],
)
async def test_sample_mode_respects_requested_material_type(
    tmp_path,
    monkeypatch,
    requested_type,
    forwarded_type,
    expected_titles,
):
    import main_logic.knowledge_context as knowledge_tool
    from knowledge.api import KnowledgeEntry

    entries = (
        KnowledgeEntry(
            title="Knowledge sample",
            terms={},
            tags=("source:chime",),
            summary="knowledge",
            content="knowledge",
        ),
        KnowledgeEntry(
            title="Corpus sample",
            terms={},
            tags=("source:corpora",),
            summary="corpus",
            content="corpus",
        ),
    )
    captured = {}

    class _MixedSampleService:
        def sample_entries(self, _tag, *, limit, material_type):
            captured.update(limit=limit, material_type=material_type)
            return entries

        def material_type_for_entry(self, entry):
            return (
                "corpus" if entry.source_tag == "source:corpora" else "knowledge"
            )

    monkeypatch.setattr(
        knowledge_tool,
        "get_config_manager",
        lambda: SimpleNamespace(knowledge_dir=tmp_path),
    )
    monkeypatch.setattr(
        knowledge_tool,
        "open_knowledge",
        lambda _root: _MixedSampleService(),
    )
    monkeypatch.setattr(
        knowledge_tool,
        "_render_entries",
        lambda _service, selected: ",".join(entry.title for _, entry in selected),
    )

    rendered = await knowledge_tool.handle_public_knowledge_call(
        {
            "query": "dataset:tarot-interpretations",
            "mode": "sample",
            "material_type": requested_type,
            "limit": 2,
        },
        language="zh",
    )

    assert captured == {"limit": 2, "material_type": forwarded_type}
    assert rendered == expected_titles


@pytest.mark.asyncio
async def test_ephemeral_meme_instruction_follows_user_and_is_not_persisted(
    monkeypatch,
):
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import AIMessage, HumanMessage, LLMStreamChunk

    sent_messages = []

    async def _astream(self, messages, **_overrides):
        sent_messages.extend(messages)
        yield LLMStreamChunk(content="reply")

    async def _noop(*_args, **_kwargs):
        pass

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)
    client = _make_client()
    client.on_response_done = _noop
    client.on_status_message = _noop

    await client.stream_text(
        "raw user message",
        ephemeral_response_instruction="reply to the preceding user message using this meme card",
        history_replacement_text="memory summary",
    )

    sent_humans = [
        message.content
        for message in sent_messages
        if isinstance(message, HumanMessage)
    ]
    assert sent_humans[-2:] == [
        "raw user message",
        "reply to the preceding user message using this meme card",
    ]
    assert isinstance(client._conversation_history[1], HumanMessage)
    assert client._conversation_history[1].content == "memory summary"
    assert not any(
        "meme card" in str(getattr(message, "content", ""))
        for message in client._conversation_history
    )
    assert isinstance(client._conversation_history[-1], AIMessage)
    assert client._conversation_history[-1].content == "reply"


@pytest.mark.asyncio
async def test_ephemeral_meme_instruction_is_removed_after_stream_error(monkeypatch):
    from main_logic.omni_offline_client import OmniOfflineClient

    async def _astream(self, messages, **_overrides):
        raise RuntimeError("test stream failure")
        if False:  # pragma: no cover - keeps this an async generator
            yield None

    async def _noop(*_args, **_kwargs):
        pass

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)
    client = _make_client()
    client.on_response_done = _noop
    client.on_status_message = _noop

    await client.stream_text(
        "raw user message",
        ephemeral_response_instruction="discard this temporary meme instruction",
    )

    assert not any(
        "temporary meme instruction" in str(getattr(message, "content", ""))
        for message in client._conversation_history
    )


@pytest.mark.asyncio
async def test_ephemeral_instruction_is_not_added_when_transcript_callback_fails():
    from utils.llm_client import HumanMessage

    async def _fail(_text: str):
        raise RuntimeError("test transcript failure")

    client = _make_client()

    with pytest.raises(RuntimeError, match="test transcript failure"):
        await client.stream_text(
            "raw user message",
            ephemeral_response_instruction="must never persist",
            input_transcript_callback=_fail,
        )

    human_messages = [
        message.content
        for message in client._conversation_history
        if isinstance(message, HumanMessage)
    ]
    assert human_messages == ["raw user message"]


@pytest.mark.asyncio
async def test_companion_chat_public_knowledge_tool_is_sample_only(monkeypatch):
    import main_logic.knowledge_context as knowledge_tool
    from main_logic.tool_calling import ToolRegistry

    captured = {}

    async def _handle(arguments, *, language, deadline_monotonic=None):
        del language, deadline_monotonic
        captured.update(arguments)
        return "sampled"

    monkeypatch.setattr(knowledge_tool, "handle_public_knowledge_call", _handle)
    registry = ToolRegistry()
    knowledge_tool.register_public_knowledge_tool(
        registry,
        language="zh",
        lookup_enabled=False,
    )

    tool = registry.get("query_public_knowledge")
    assert tool is not None
    assert tool.parameters["properties"]["mode"]["enum"] == ["sample"]
    assert tool.parameters["properties"]["mode"]["default"] == "sample"
    assert (
        await tool.handler({"query": "dataset:animals", "mode": "lookup"}) == "sampled"
    )
    assert captured["mode"] == "sample"


def test_realtime_public_knowledge_tool_exposes_lookup_and_sample():
    from main_logic.knowledge_context import register_public_knowledge_tool
    from main_logic.tool_calling import ToolRegistry

    registry = ToolRegistry()
    register_public_knowledge_tool(
        registry,
        language="zh",
        lookup_enabled=True,
    )

    tool = registry.get("query_public_knowledge")
    assert tool is not None
    assert tool.parameters["properties"]["mode"]["enum"] == [
        "lookup",
        "sample",
    ]
    assert tool.parameters["properties"]["mode"]["default"] == "lookup"


def test_public_knowledge_lookup_is_available_in_both_modes():
    """Explicit lookup is no longer realtime-only.

    It used to be gated on the session type, on the reasoning that a text turn
    had already resolved knowledge deterministically before replying. Automatic
    retrieval is threshold gated, so anything below the bar was never looked up
    and the model had no way to ask; the tool is now the fallback for exactly
    that case in both modes.
    """
    from main_logic.core.tool_calling import ToolCallingMixin
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.omni_realtime_client import OmniRealtimeClient

    text_manager = SimpleNamespace(
        session=OmniOfflineClient.__new__(OmniOfflineClient),
        pending_session=None,
    )
    assert ToolCallingMixin._public_knowledge_lookup_enabled(text_manager) is True

    voice_manager = SimpleNamespace(
        session=None,
        pending_session=OmniRealtimeClient.__new__(OmniRealtimeClient),
    )
    assert ToolCallingMixin._public_knowledge_lookup_enabled(voice_manager) is True


@pytest.mark.asyncio
async def test_every_plain_user_turn_runs_host_owned_material_selection(monkeypatch):
    import main_logic.knowledge_context as knowledge_tool
    from knowledge.service import KnowledgeTurnContext

    selector = AsyncMock(
        return_value=KnowledgeTurnContext(match_mode="automatic_miss")
    )
    fallback = AsyncMock(return_value="unexpected")
    service = SimpleNamespace(abuild_conversation_context=selector)
    monkeypatch.setattr(knowledge_tool, "open_knowledge", lambda _root: service)
    monkeypatch.setattr(knowledge_tool, "handle_public_knowledge_call", fallback)

    result = await knowledge_tool.build_public_knowledge_turn_context(
        "你好呀",
    )

    assert result.context == ""
    assert result.route_owner is None
    selector.assert_awaited_once()
    assert selector.await_args.args == ("你好呀",)
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_local_knowledge_uses_one_host_lookup(monkeypatch):
    import main_logic.knowledge_context as knowledge_tool

    fallback = AsyncMock(return_value="local result")
    monkeypatch.setattr(knowledge_tool, "handle_public_knowledge_call", fallback)

    result = await knowledge_tool.build_public_knowledge_turn_context(
        "请查询本地知识库：电车难题是什么？",
    )

    assert result.context == "local result"
    assert result.route_owner == "public_knowledge"
    fallback.assert_awaited_once_with(
        {
            "query": "电车难题是什么？",
            "mode": "lookup",
            "material_type": "auto",
            "limit": 3,
        },
        language="",
        deadline_monotonic=ANY,
    )


@pytest.mark.asyncio
async def test_failed_explicit_lookup_still_keeps_local_route_owner(monkeypatch):
    import main_logic.knowledge_context as knowledge_tool

    monkeypatch.setattr(
        knowledge_tool,
        "handle_public_knowledge_call",
        AsyncMock(side_effect=RuntimeError("offline")),
    )

    result = await knowledge_tool.build_public_knowledge_turn_context(
        "本地知识库：电车难题是什么？",
    )

    assert result.context == ""
    assert result.route_owner == "public_knowledge"


@pytest.mark.asyncio
async def test_automatic_knowledge_hit_does_not_use_explicit_lookup(monkeypatch):
    import main_logic.knowledge_context as knowledge_tool
    from knowledge.service import KnowledgeTurnContext

    service = SimpleNamespace(
        abuild_conversation_context=AsyncMock(
            return_value=KnowledgeTurnContext(
                text="automatic result",
                hit_count=1,
                match_mode="automatic_hybrid",
            )
        )
    )
    fallback = AsyncMock(return_value="unexpected")
    monkeypatch.setattr(knowledge_tool, "open_knowledge", lambda _root: service)
    monkeypatch.setattr(knowledge_tool, "handle_public_knowledge_call", fallback)

    result = await knowledge_tool.build_public_knowledge_turn_context(
        "你急了",
    )

    assert result.context == "automatic result"
    assert result.route_owner is None
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_automatic_context_timeout_fails_open_without_stacking(monkeypatch):
    import config.public_knowledge_settings as settings
    import main_logic.knowledge_context as knowledge_tool
    from knowledge.service import KnowledgeTurnContext

    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class _SlowService:
        async def abuild_conversation_context(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return KnowledgeTurnContext(match_mode="automatic_miss")

    monkeypatch.setattr(knowledge_tool, "_AUTO_CONTEXT_TASKS", {})
    monkeypatch.setattr(knowledge_tool, "open_knowledge", lambda _root: _SlowService())
    monkeypatch.setattr(
        settings,
        "PUBLIC_KNOWLEDGE_AUTO_CONTEXT_BUDGET_SECONDS",
        0.02,
    )
    started_at = asyncio.get_running_loop().time()
    try:
        result = await knowledge_tool.build_public_knowledge_turn_context("第一轮")
        assert result.context == ""
        await asyncio.wait_for(started.wait(), timeout=1.0)
        result = await knowledge_tool.build_public_knowledge_turn_context("第二轮")
        assert result.context == ""
        assert calls == 1
    finally:
        release.set()
        await asyncio.sleep(0)

    assert asyncio.get_running_loop().time() - started_at < 0.2


@pytest.mark.asyncio
async def test_slow_knowledge_open_does_not_block_the_event_loop(monkeypatch):
    import config.public_knowledge_settings as settings
    import main_logic.knowledge_context as knowledge_tool
    from knowledge.service import KnowledgeTurnContext

    heartbeat = asyncio.Event()

    class _Service:
        async def abuild_conversation_context(self, *_args, **_kwargs):
            return KnowledgeTurnContext(match_mode="automatic_miss")

    def _slow_open(_root):
        time.sleep(0.08)
        return _Service()

    async def _tick():
        await asyncio.sleep(0.005)
        heartbeat.set()

    monkeypatch.setattr(knowledge_tool, "_AUTO_CONTEXT_TASKS", {})
    monkeypatch.setattr(knowledge_tool, "open_knowledge", _slow_open)
    monkeypatch.setattr(
        settings,
        "PUBLIC_KNOWLEDGE_AUTO_CONTEXT_BUDGET_SECONDS",
        0.02,
    )
    tick = asyncio.create_task(_tick())
    result = await knowledge_tool.build_public_knowledge_turn_context("普通聊天")
    assert result.context == ""
    await asyncio.wait_for(heartbeat.wait(), timeout=0.05)
    await tick
    await asyncio.sleep(0.1)


# --- passive injection must stay occasional ----------------------------------


def _turn_context(title: str, source: str = "source:community.demo"):
    from knowledge.service import KnowledgeTurnContext

    return KnowledgeTurnContext(
        text=f"card for {title}",
        hit_count=1,
        match_mode="automatic_hybrid",
        entry_title=title,
        source_tag=source,
    )


@pytest.mark.asyncio
async def test_same_card_is_not_injected_twice_in_a_row(monkeypatch):
    """Retrieval runs every turn; delivery must not.

    The same topic keeps matching across consecutive turns, so without a
    cooldown the identical reference card is re-injected turn after turn, which
    is what makes passive injection read as a stutter.
    """
    import main_logic.knowledge_context as knowledge_tool

    knowledge_tool.reset_public_knowledge_injection_state()

    async def _context(*_args, **_kwargs):
        return _turn_context("永动机")

    monkeypatch.setattr(knowledge_tool, "open_knowledge", lambda _root: object())
    monkeypatch.setattr(
        knowledge_tool, "build_automatic_public_knowledge_context", _context
    )

    first = await knowledge_tool.build_public_knowledge_turn_context(
        "聊聊永动机", session_key="lanlan"
    )
    assert first.context, "the first match should be delivered"

    second = await knowledge_tool.build_public_knowledge_turn_context(
        "再说说永动机", session_key="lanlan"
    )
    assert second.context == "", "the same card must not be delivered again"


@pytest.mark.asyncio
async def test_cooldown_is_scoped_per_session(monkeypatch):
    """One character's delivered card must not silence another character's."""
    import main_logic.knowledge_context as knowledge_tool

    knowledge_tool.reset_public_knowledge_injection_state()

    async def _context(*_args, **_kwargs):
        return _turn_context("永动机")

    monkeypatch.setattr(knowledge_tool, "open_knowledge", lambda _root: object())
    monkeypatch.setattr(
        knowledge_tool, "build_automatic_public_knowledge_context", _context
    )

    first = await knowledge_tool.build_public_knowledge_turn_context(
        "聊聊永动机", session_key="lanlan-a"
    )
    assert first.context

    # The other half of "scoped": A must actually be on cooldown, otherwise a
    # globally broken cooldown would satisfy the B assertion just as well.
    again = await knowledge_tool.build_public_knowledge_turn_context(
        "再说说永动机", session_key="lanlan-a"
    )
    assert again.context == ""

    other = await knowledge_tool.build_public_knowledge_turn_context(
        "聊聊永动机", session_key="lanlan-b"
    )
    assert other.context, "another character must not inherit A's cooldown"


@pytest.mark.asyncio
async def test_a_different_card_still_gets_through(monkeypatch):
    """The cooldown is per card, not a blanket mute on the session."""
    import main_logic.knowledge_context as knowledge_tool

    knowledge_tool.reset_public_knowledge_injection_state()

    titles = iter(["永动机", "薛定谔的猫"])

    async def _context(*_args, **_kwargs):
        return _turn_context(next(titles))

    monkeypatch.setattr(knowledge_tool, "open_knowledge", lambda _root: object())
    monkeypatch.setattr(
        knowledge_tool, "build_automatic_public_knowledge_context", _context
    )

    first = await knowledge_tool.build_public_knowledge_turn_context(
        "聊聊永动机", session_key="lanlan"
    )
    second = await knowledge_tool.build_public_knowledge_turn_context(
        "那薛定谔的猫呢", session_key="lanlan"
    )
    assert first.context and second.context


@pytest.mark.asyncio
async def test_cooldown_expires(monkeypatch):
    import main_logic.knowledge_context as knowledge_tool

    knowledge_tool.reset_public_knowledge_injection_state()

    async def _context(*_args, **_kwargs):
        return _turn_context("永动机")

    monkeypatch.setattr(knowledge_tool, "open_knowledge", lambda _root: object())
    monkeypatch.setattr(
        knowledge_tool, "build_automatic_public_knowledge_context", _context
    )
    monkeypatch.setattr(knowledge_tool, "_INJECTION_COOLDOWN_SECONDS", 0.0)

    first = await knowledge_tool.build_public_knowledge_turn_context(
        "聊聊永动机", session_key="lanlan"
    )
    second = await knowledge_tool.build_public_knowledge_turn_context(
        "再说说永动机", session_key="lanlan"
    )
    assert first.context and second.context


@pytest.mark.asyncio
async def test_retrieval_single_flight_does_not_starve_another_character(monkeypatch):
    """The in-flight slot is per session, so characters do not block each other."""
    import main_logic.knowledge_context as knowledge_tool

    knowledge_tool.reset_public_knowledge_injection_state()
    release = asyncio.Event()

    async def _slow_context(*_args, **_kwargs):
        await release.wait()
        return _turn_context("慢词条")

    monkeypatch.setattr(knowledge_tool, "open_knowledge", lambda _root: object())
    monkeypatch.setattr(
        knowledge_tool, "build_automatic_public_knowledge_context", _slow_context
    )

    slow = asyncio.create_task(
        knowledge_tool.build_public_knowledge_turn_context(
            "慢查询", session_key="lanlan-a"
        )
    )
    await asyncio.sleep(0)

    # A different character starting now must get its own slot rather than being
    # reported busy because someone else is mid-retrieval.
    with knowledge_tool._AUTO_CONTEXT_LOCK:
        tasks = dict(knowledge_tool._AUTO_CONTEXT_TASKS)
    assert "lanlan-a" in tasks

    started = knowledge_tool._start_automatic_context_task(
        _slow_context, session_key="lanlan-b"
    )
    assert started is not None, "a second character must not be refused a slot"
    # Same character while busy: still single-flight.
    assert (
        knowledge_tool._start_automatic_context_task(
            _slow_context, session_key="lanlan-a"
        )
        is None
    )

    release.set()
    await slow
    started.cancel()
    knowledge_tool.reset_public_knowledge_injection_state()
