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
        self.handle_new_message = AsyncMock()


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
