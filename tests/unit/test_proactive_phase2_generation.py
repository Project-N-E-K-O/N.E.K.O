"""Focused compatibility tests for proactive Phase 2 streaming generation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.proactive_chat import contracts, generation


class _FakeState:
    def __init__(self, *, preempted: bool = False) -> None:
        self.preempted = preempted

    def is_proactive_preempted(self, _speech_id: str | None = None) -> bool:
        return self.preempted


class _FakeManager:
    def __init__(self, *, preempted: bool = False) -> None:
        self.state = _FakeState(preempted=preempted)
        self.current_speech_id = "proactive-sid"
        self.handle_new_message = AsyncMock()
        self.last_user_activity_time = None
        self.last_user_engagement_time = None
        self.proactive_engagement_observation_started_at = 100.0


class _FakeStreamingLLM:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    async def __aenter__(self) -> "_FakeStreamingLLM":
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        return None

    async def astream(self, _messages: list[object]):
        for content in self._chunks:
            yield SimpleNamespace(content=content)


def _make_llm_factory(chunks: list[str]):
    async def _make_llm(**_kwargs: object) -> _FakeStreamingLLM:
        return _FakeStreamingLLM(chunks)

    return _make_llm


def _patch_runtime_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the unit boundary independent from tokenizers/provider config."""
    monkeypatch.setattr(
        generation,
        "count_tokens",
        lambda text: len(text),
        raising=False,
    )
    monkeypatch.setattr(
        generation,
        "set_call_type",
        lambda _call_type: None,
        raising=False,
    )
    monkeypatch.setattr(
        generation,
        "leaks_thinking_in_content",
        lambda _model: False,
        raising=False,
    )


async def _generate(
    mgr: _FakeManager,
    chunks: list[str],
    *,
    expects_source_tag: bool = True,
) -> generation.Phase2Generation:
    return await generation._generate_phase2_stream(
        mgr=mgr,
        proactive_sid="proactive-sid",
        lanlan_name="兰兰",
        messages=[object(), object()],
        make_llm=_make_llm_factory(chunks),
        phase2_use_vision=False,
        phase2_disable_thinking=True,
        conversation_model="fake-model",
        expects_source_tag=expects_source_tag,
        proactive_lang="zh",
        master_name="博士",
        human_text="开始生成",
        screenshot_b64=None,
    )


@pytest.mark.asyncio
async def test_chat_tag_returns_clean_generated_text(monkeypatch) -> None:
    _patch_runtime_guards(monkeypatch)
    mgr = _FakeManager()

    generated = await _generate(mgr, ["[CHAT]\n", "博士，今天也辛苦啦。"])

    assert generated == generation.Phase2Generation(
        result=None,
        full_text="博士，今天也辛苦啦。",
        response_text="博士，今天也辛苦啦。",
        source_tag="CHAT",
    )
    mgr.handle_new_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_label_only_chunk_defers_to_following_legal_tag(monkeypatch) -> None:
    _patch_runtime_guards(monkeypatch)
    mgr = _FakeManager()

    generated = await _generate(
        mgr,
        ["当前屏幕观察\n", "[WEB]\n", "看这个链接"],
    )

    assert generated == generation.Phase2Generation(
        result=None,
        full_text="看这个链接",
        response_text="看这个链接",
        source_tag="WEB",
    )
    mgr.handle_new_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_screen_only_chunk_defers_to_following_legal_tag(monkeypatch) -> None:
    _patch_runtime_guards(monkeypatch)
    mgr = _FakeManager()

    generated = await _generate(
        mgr,
        ["[Screen]\n", "[WEB]\n", "看这个链接"],
    )

    assert generated == generation.Phase2Generation(
        result=None,
        full_text="看这个链接",
        response_text="看这个链接",
        source_tag="WEB",
    )
    mgr.handle_new_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "body", "source"),
    [
        ("[WEB]\n看这个链接", "看这个链接", "WEB"),
        ("[Screen]\n[WEB]\n看这个链接", "看这个链接", "WEB"),
        ("当前屏幕观察\n[WEB]\n看这个链接", "看这个链接", "WEB"),
        ("\ufeff[Screen]\u200b[WEB]\n看这个链接", "看这个链接", "WEB"),
        ("chat/[WEB]\n看这个链接", "看这个链接", "WEB"),
        ("/chat /api", "/api", "CHAT"),
        ("chat/hello", "chat/hello", ""),
        ("当前屏幕观察到你正在写代码", "当前屏幕观察到你正在写代码", ""),
        ("[Screen]\n[W", "[W", "CHAT"),
        ("[Screen]\n[docs]", "[docs]", "CHAT"),
        ("主动搭话\n[WEB]\n看这个链接", "看这个链接", "WEB"),
        ("我想主动搭话\n你好", "我想主动搭话\n你好", ""),
        ("[CHAT]\n我想主动搭话\n你好", "我想主动搭话\n你好", "CHAT"),
        ("[Screen]\n我想主动搭话\n你好", "我想主动搭话\n你好", "CHAT"),
    ],
)
async def test_prefix_result_is_independent_of_chunk_boundaries(
    monkeypatch, raw, body, source,
) -> None:
    _patch_runtime_guards(monkeypatch)
    expected = generation.Phase2Generation(
        result=None, full_text=body, response_text=body, source_tag=source,
    )
    # Every two-chunk split plus single-character chunks includes cuts inside
    # labels, punctuation and whitespace, not just handpicked whole-tag chunks.
    partitions = [[raw], list(raw)] + [
        [raw[:cut], raw[cut:]] for cut in range(1, len(raw))
    ]
    for chunks in partitions:
        mgr = _FakeManager()
        generated = await _generate(mgr, chunks, expects_source_tag=bool(source))
        assert generated == expected, chunks
        mgr.handle_new_message.assert_not_awaited()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("我想主动搭话\n你好", ("我想主动搭话\n你好", "")),
        ("[CHAT]\n我想主动搭话\n你好", ("我想主动搭话\n你好", "CHAT")),
        ("  主动搭话\n[WEB]\n你好", ("你好", "WEB")),
        ("主动搭话\n我想主动搭话\n你好", ("我想主动搭话\n你好", "")),
    ],
)
def test_final_prefix_parser_only_removes_leading_heading(raw, expected) -> None:
    assert generation._parse_proactive_phase2_prefix(raw, final=True) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["[Screen]\n[PASS]", "当前屏幕观察\n[PASS]", "[CHAT]\n[PASS]"])
async def test_pass_after_prefix_is_blocked_at_every_split(monkeypatch, raw) -> None:
    _patch_runtime_guards(monkeypatch)
    for cut in range(1, len(raw)):
        mgr = _FakeManager()
        generated = await _generate(mgr, [raw[:cut], raw[cut:]])
        assert generated.result.body["reason_code"] == contracts.PROACTIVE_REASON_PASS_MODEL_PASS
        mgr.handle_new_message.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunks",
    [
        ["[Screen]\n", "a|", "b｜"],
        ["[Screen]\n", "x" * 451],
        ["[Screen]\n", "x" * 301],
    ],
)
async def test_pending_prefix_still_enforces_stream_limits(monkeypatch, chunks) -> None:
    _patch_runtime_guards(monkeypatch)
    generated = await _generate(_FakeManager(), chunks)
    assert generated.result.body["reason_code"] == contracts.PROACTIVE_REASON_PASS_GENERATION_EMPTY


@pytest.mark.asyncio
async def test_user_preemption_while_prefix_is_pending(monkeypatch) -> None:
    _patch_runtime_guards(monkeypatch)
    mgr = _FakeManager()

    async def preempted_stream(self, messages):
        yield SimpleNamespace(content="[Screen]\n[W")
        mgr.state.preempted = True
        yield SimpleNamespace(content="EB]\n看这个链接")

    monkeypatch.setattr(_FakeStreamingLLM, "astream", preempted_stream)
    generated = await _generate(mgr, [])
    assert generated.result.body["reason_code"] == contracts.PROACTIVE_REASON_DELIVERY_PREEMPTED
    mgr.handle_new_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_label_only_output_becomes_generation_empty(monkeypatch) -> None:
    _patch_runtime_guards(monkeypatch)
    mgr = _FakeManager()

    generated = await _generate(mgr, ["[CHAT]\n", "QQ/"])

    assert generated.result is not None
    assert (
        generated.result.body["reason_code"]
        == contracts.PROACTIVE_REASON_PASS_GENERATION_EMPTY
    )
    mgr.handle_new_message.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_bare_pass_returns_model_pass_and_clears_proactive_tts(
    monkeypatch,
) -> None:
    _patch_runtime_guards(monkeypatch)
    mgr = _FakeManager()

    generated = await _generate(mgr, ["PASS"])

    assert generated.result is not None
    assert generated.result.body["action"] == "pass"
    assert (
        generated.result.body["reason_code"]
        == contracts.PROACTIVE_REASON_PASS_MODEL_PASS
    )
    assert generated.full_text == ""
    assert generated.response_text == ""
    mgr.handle_new_message.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_pass_sentinel_split_across_chunks_is_still_blocked(
    monkeypatch,
) -> None:
    _patch_runtime_guards(monkeypatch)
    mgr = _FakeManager()

    generated = await _generate(mgr, ["[CHAT]\n", "不能说 [PA", "SS]"])

    assert generated.result is not None
    assert (
        generated.result.body["reason_code"]
        == contracts.PROACTIVE_REASON_PASS_MODEL_PASS
    )
    mgr.handle_new_message.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_tagless_text_remains_valid_when_tag_contract_is_disabled(
    monkeypatch,
) -> None:
    _patch_runtime_guards(monkeypatch)
    mgr = _FakeManager()

    generated = await _generate(
        mgr,
        ["纯文本模式也可以正常搭话。"],
        expects_source_tag=False,
    )

    assert generated.result is None
    assert generated.full_text == "纯文本模式也可以正常搭话。"
    assert generated.response_text == "纯文本模式也可以正常搭话。"
    assert generated.source_tag == ""
    mgr.handle_new_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_preemption_does_not_clear_user_reply_tts(monkeypatch) -> None:
    _patch_runtime_guards(monkeypatch)
    mgr = _FakeManager(preempted=True)

    generated = await _generate(mgr, ["[CHAT]\n", "这句不应继续投递。"])

    assert generated.result is not None
    assert generated.result.body["action"] == "pass"
    assert (
        generated.result.body["reason_code"]
        == contracts.PROACTIVE_REASON_DELIVERY_PREEMPTED
    )
    mgr.handle_new_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("replacement_sid", "expects_cleanup"),
    ((None, True), ("avatar-sid", False)),
)
async def test_initial_phase2_generation_aborts_after_ui_engagement(
    monkeypatch,
    replacement_sid,
    expects_cleanup,
) -> None:
    mgr = _FakeManager()

    async def generate_after_engagement(**_kwargs):
        mgr.last_user_engagement_time = 200.0
        if replacement_sid is not None:
            mgr.current_speech_id = replacement_sid
        return generation.Phase2Generation(
            result=None,
            full_text="这句不应继续投递。",
            response_text="这句不应继续投递。",
            source_tag="CHAT",
        )

    guard_output = AsyncMock()
    monkeypatch.setattr(generation, "_generate_phase2_stream", generate_after_engagement)
    monkeypatch.setattr(generation, "_guard_phase2_output", guard_output)
    monkeypatch.setattr(generation, "_loc", lambda *_args: "begin")

    output = await generation._run_phase2_generation(
        mgr=mgr,
        proactive_sid="proactive-sid",
        model_config=generation.ProactiveModelConfig(
            conversation_model="fake-model",
            conversation_base_url=None,
            conversation_api_key="fake-key",
            conversation_provider_type=None,
        ),
        lanlan_name="兰兰",
        proactive_lang="zh",
        master_name="博士",
        system_prompt="system",
        dynamic_context="",
        screenshot_b64=None,
        focus_thinking=False,
        expects_source_tag=True,
        active_channels=[],
        selected_music_link=None,
        selected_meme_link=None,
        music_content=None,
        meme_content=None,
        is_playing_music=False,
        music_cooldown=False,
    )

    guard_output.assert_not_awaited()
    if expects_cleanup:
        mgr.handle_new_message.assert_awaited_once_with()
    else:
        mgr.handle_new_message.assert_not_awaited()
    assert output.result is not None
    assert (
        output.result.body["reason_code"]
        == contracts.PROACTIVE_REASON_DELIVERY_PREEMPTED
    )
