"""Unit tests for Qwen/DashScope explicit prompt caching and observability (#1066)."""

from __future__ import annotations

import atexit
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 1. DashScope registry coverage and non-DashScope isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("endpoint_url", "expected_id"),
    [
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen"),
        ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "qwen_intl"),
        ("https://dashscope-us.aliyuncs.com/compatible-mode/v1", "qwen_us"),
        ("https://coding.dashscope.aliyuncs.com/v1", "qwen"),
        ("https://coding-intl.dashscope.aliyuncs.com/v1", "qwen_coding_intl"),
        ("https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", "qwen_token_plan"),
        ("https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1", "qwen_token_plan_intl"),
    ],
)
def test_dashscope_endpoints_resolve_with_body_flag_and_headers(endpoint_url: str, expected_id: str) -> None:
    """Every DashScope Chat Completions endpoint resolves with explicit cache support."""
    from config.providers import get_cache_kwargs, resolve_cache_provider

    provider = resolve_cache_provider(endpoint_url)
    assert provider is not None
    assert provider.provider_id == expected_id
    assert provider.requires_body_flag is True
    assert provider.requires_header is True
    assert provider.memory_thinking_extra_body is not None

    cache_kw = get_cache_kwargs(endpoint_url)
    assert cache_kw["enable_cache_control"] is True
    assert cache_kw["default_headers"] == {"x-dashscope-session-cache": "enable"}


def test_non_dashscope_providers_keep_body_flag_disabled() -> None:
    """Only DashScope entries enable requires_body_flag in CACHE_PROVIDERS."""
    from config.providers import CACHE_PROVIDERS, get_cache_kwargs

    dashscope_ids = {
        "qwen",
        "qwen_intl",
        "qwen_us",
        "qwen_coding_intl",
        "qwen_token_plan",
        "qwen_token_plan_intl",
    }
    for provider_id, cfg in CACHE_PROVIDERS.items():
        kw = get_cache_kwargs(cfg.base_url)
        if provider_id in dashscope_ids:
            assert cfg.requires_body_flag is True
            assert kw["enable_cache_control"] is True
        else:
            assert cfg.requires_body_flag is False
            assert kw["enable_cache_control"] is False


# ---------------------------------------------------------------------------
# 2. create_chat_llm opt-in gating
# ---------------------------------------------------------------------------


def test_create_chat_llm_requires_caller_opt_in_for_explicit_cache() -> None:
    """One-shot DashScope calls stay on implicit cache unless callers opt in."""
    from utils.llm_client import create_chat_llm

    dashscope_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    oneshot = create_chat_llm("qwen3.8-flash", dashscope_url, "k")
    try:
        assert oneshot.enable_cache_control is False
        assert oneshot.cache_conversation is False
    finally:
        oneshot.close()

    sys_only = create_chat_llm("qwen3.8-flash", dashscope_url, "k", explicit_cache=True)
    try:
        assert sys_only.enable_cache_control is True
        assert sys_only.cache_conversation is False
    finally:
        sys_only.close()

    conv_only_flag = create_chat_llm("qwen3.8-flash", dashscope_url, "k", cache_conversation=True)
    try:
        assert conv_only_flag.enable_cache_control is True
        assert conv_only_flag.cache_conversation is True
    finally:
        conv_only_flag.close()

    dual = create_chat_llm(
        "qwen3.8-flash",
        dashscope_url,
        "k",
        explicit_cache=True,
        cache_conversation=True,
    )
    try:
        assert dual.enable_cache_control is True
        assert dual.cache_conversation is True
    finally:
        dual.close()


@pytest.mark.parametrize(
    ("model", "endpoint_url"),
    [
        ("gpt-5-nano", "https://api.openai.com/v1"),
        ("deepseek-flash", "https://api.deepseek.com/v1"),
        ("glm-5.2", "https://open.bigmodel.cn/api/paas/v4"),
        ("local-model", "http://127.0.0.1:11434/v1"),
    ],
)
def test_create_chat_llm_ignores_opt_in_on_non_body_flag_providers(model: str, endpoint_url: str) -> None:
    """Non-body-flag providers ignore explicit_cache and cache_conversation."""
    from utils.llm_client import create_chat_llm

    client = create_chat_llm(
        model,
        endpoint_url,
        "k",
        explicit_cache=True,
        cache_conversation=True,
    )
    try:
        assert client.enable_cache_control is False
        assert client.cache_conversation is False
    finally:
        client.close()


def test_create_chat_llm_explicit_enable_cache_control_override() -> None:
    """Explicit enable_cache_control kwargs override provider capability cleanly."""
    from utils.llm_client import create_chat_llm

    forced_on = create_chat_llm(
        "deepseek-flash",
        "https://api.deepseek.com/v1",
        "k",
        enable_cache_control=True,
        cache_conversation=True,
    )
    try:
        assert forced_on.enable_cache_control is True
        assert forced_on.cache_conversation is True
    finally:
        forced_on.close()

    forced_off = create_chat_llm(
        "qwen3.8-flash",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "k",
        explicit_cache=True,
        cache_conversation=True,
        enable_cache_control=False,
    )
    try:
        assert forced_off.enable_cache_control is False
        assert forced_off.cache_conversation is False
    finally:
        forced_off.close()


# ---------------------------------------------------------------------------
# 3. Dual breakpoints, prefix split ordering, ephemeral skipping, tool safety
# ---------------------------------------------------------------------------


def test_params_splits_static_prefix_before_placeholder_substitution() -> None:
    """cache_static_prefix_len splits before character placeholder substitution and passes leak checks."""
    from utils.llm_client import (
        AIMessage,
        CACHE_EPHEMERAL_KEY,
        CACHE_STATIC_PREFIX_LEN_KEY,
        ChatOpenAI,
        HumanMessage,
        SystemMessage,
        reset_active_character,
        set_active_character,
    )

    static_raw = "You are {LANLAN_NAME}, speaking to {MASTER_NAME}."
    dynamic_raw = "\n\n======{LANLAN_NAME}'s Inner Thoughts======\nTime is 12:00."
    raw_prompt = static_raw + dynamic_raw

    client = ChatOpenAI(
        model="qwen3.8-flash",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="k",
        enable_cache_control=True,
        cache_conversation=True,
    )
    msgs = [
        SystemMessage(content=raw_prompt, metadata={CACHE_STATIC_PREFIX_LEN_KEY: len(static_raw)}),
        HumanMessage(content="turn 1"),
        AIMessage(content="reply 1"),
        HumanMessage(content="turn 2"),
        HumanMessage(content="resident card", metadata={"turn_context": "resident"}),
        HumanMessage(content="ephemeral memory", metadata={"turn_context": "ephemeral"}),
        HumanMessage(content="proactive nudge", metadata={CACHE_EPHEMERAL_KEY: True}),
    ]
    token = set_active_character("VeryLongMasterName", "XiaoLan")
    try:
        params = client._params(msgs)
    finally:
        reset_active_character(token)
        client.close()

    wire_msgs = params["messages"]
    assert wire_msgs[0] == {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": "You are XiaoLan, speaking to VeryLongMasterName.",
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }
    assert wire_msgs[1] == {
        "role": "system",
        "content": "\n\n======XiaoLan's Inner Thoughts======\nTime is 12:00.",
    }
    # Resident block shifted from index 4 -> 5; indices 6 and 7 remain ephemeral strings.
    assert wire_msgs[5]["content"] == [
        {
            "type": "text",
            "text": "resident card",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert wire_msgs[6]["content"] == "ephemeral memory"
    assert wire_msgs[7]["content"] == "proactive nudge"
    # Input BaseMessage instances stay unmodified.
    assert msgs[0].content == raw_prompt


def test_inject_cache_control_skips_tool_and_assistant_roles_and_is_idempotent() -> None:
    """Rolling BP2 targets the last non-ephemeral user message and never mutates tool/assistant content."""
    from utils.llm_client import _inject_cache_control, _split_system_prefix_blocks

    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "check weather"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "w", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"temp": 20}'},
        {"role": "user", "content": "ephemeral note"},
    ]

    once = _inject_cache_control(msgs, cache_conversation=True, ephemeral_indices={4})
    assert once[0]["content"] == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    assert once[1]["content"] == [{"type": "text", "text": "check weather", "cache_control": {"type": "ephemeral"}}]
    assert once[2]["content"] == ""
    assert once[3]["content"] == '{"temp": 20}'
    assert once[4]["content"] == "ephemeral note"

    # Idempotent: rerunning must return the exact same object without stacking onto earlier user messages.
    twice = _inject_cache_control(once, cache_conversation=True, ephemeral_indices={4})
    assert twice is once

    # Idempotent with split_system_indices: BP1 stays on static system[0] and leaves dynamic system[1] unmarked.
    split_raw = [
        {"role": "system", "content": "static prefix||dynamic suffix"},
        {"role": "user", "content": "hello"},
        {"role": "user", "content": "ephemeral tail"},
    ]
    split_msgs, split_idx, ephem_idx = _split_system_prefix_blocks(split_raw, {0: len("static prefix")}, {2})
    assert split_idx == {0}
    assert ephem_idx == {3}
    split_once = _inject_cache_control(
        split_msgs, cache_conversation=True, split_system_indices=split_idx, ephemeral_indices=ephem_idx
    )
    assert split_once[0]["content"] == [
        {"type": "text", "text": "static prefix", "cache_control": {"type": "ephemeral"}}
    ]
    assert split_once[1] == {"role": "system", "content": "||dynamic suffix"}
    assert split_once[2]["content"] == [
        {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}
    ]
    assert split_once[3]["content"] == "ephemeral tail"
    split_twice = _inject_cache_control(
        split_once, cache_conversation=True, split_system_indices=split_idx, ephemeral_indices=ephem_idx
    )
    assert split_twice is split_once

    msgs_with_earlier_user = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "old user"},
        {"role": "assistant", "content": "old reply"},
        {
            "role": "user",
            "content": [{"type": "text", "text": "latest user", "cache_control": {"type": "ephemeral"}}],
        },
    ]
    kept = _inject_cache_control(msgs_with_earlier_user, cache_conversation=True)
    assert kept[1]["content"] == "old user"
    assert kept[3]["content"][0]["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# 4. find_static_prefix_end and OmniOfflineClient wiring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("locale", "name"),
    [
        ("zh", "小蓝"),
        ("zh-TW", "小藍"),
        ("en", "XiaoLan"),
        ("ja", "シャオラン"),
        ("ko", "샤오란"),
        ("ru", "Сяолань"),
        ("es", "XiaoLan"),
        ("pt", "XiaoLan"),
    ],
)
def test_find_static_prefix_end_matches_all_eight_locales(locale: str, name: str) -> None:
    """find_static_prefix_end locates INNER_THOUGHTS_HEADER across every supported locale."""
    from config.prompts.prompts_memory import INNER_THOUGHTS_HEADER, find_static_prefix_end

    static_prefix = f"Persona prompt for {name}\nLong-term memory block."
    dynamic_part = INNER_THOUGHTS_HEADER[locale].format(name=name) + "Recent turns..."
    full = static_prefix + dynamic_part

    pos = find_static_prefix_end(full)
    assert pos is not None
    assert full[:pos] == static_prefix
    assert full[pos:] == dynamic_part

    assert find_static_prefix_end("") is None
    assert find_static_prefix_end("No inner thoughts marker here") is None
    assert find_static_prefix_end(dynamic_part) is None


@pytest.mark.asyncio
async def test_omni_offline_client_wiring_and_prime_context_preserve_boundary(monkeypatch) -> None:
    """OmniOfflineClient opts in for chat/switch_model, preserves BP1 on prime_context, and skips TTS summary."""
    from config.prompts.prompts_memory import INNER_THOUGHTS_HEADER
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import CACHE_EPHEMERAL_KEY, CACHE_STATIC_PREFIX_LEN_KEY

    dashscope_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    client = OmniOfflineClient(
        base_url=dashscope_url,
        api_key="k",
        model="qwen3.8-flash",
    )
    try:
        assert client.llm.enable_cache_control is True
        assert client.llm.cache_conversation is True

        static_part = "Stable role instructions\nLong-term persona"
        dynamic_part = INNER_THOUGHTS_HEADER["en"].format(name="Neko") + "Timestamp: 10:00"
        await client.connect(static_part + dynamic_part)
        assert client._conversation_history[0].metadata[CACHE_STATIC_PREFIX_LEN_KEY] == len(static_part)

        late_text = "\nuser | late context\n"
        await client.prime_context(late_text)
        assert client._conversation_history[0].metadata[CACHE_STATIC_PREFIX_LEN_KEY] == len(static_part)

        params = client.llm._params(client._conversation_history)
        assert params["messages"] == [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": static_part,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
            {
                "role": "system",
                "content": dynamic_part + late_text,
            },
        ]

        await client.switch_model("qwen3.8-max")
        assert client.llm.enable_cache_control is True
        assert client.llm.cache_conversation is True

        # One-shot TTS tail summary must NOT opt into explicit caching.
        summary_calls: list[dict] = []

        class _FakeSummaryLLM:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def ainvoke(self, _messages):
                return SimpleNamespace(content="summary")

        async def _fake_create_chat_llm_async(*args, **kwargs):
            summary_calls.append({"args": args, "kwargs": kwargs})
            return _FakeSummaryLLM()

        fake_cfg = SimpleNamespace(
            aget_model_api_config=AsyncMock(
                return_value={
                    "model": "qwen3.7-flash",
                    "base_url": dashscope_url,
                    "api_key": "k",
                    "provider_type": "openai_compatible",
                }
            )
        )
        with monkeypatch.context() as scoped_patch:
            scoped_patch.setattr("utils.config_manager.get_config_manager", lambda: fake_cfg)
            scoped_patch.setattr(
                "main_logic.omni_offline_client._streaming.create_chat_llm_async",
                _fake_create_chat_llm_async,
            )
            out = await client._summarize_tail_for_tts("Already spoken.", "Remaining long tail sentence.")
        assert out == "summary"
        assert len(summary_calls) == 1
        assert summary_calls[0]["kwargs"].get("explicit_cache", False) is False
        assert summary_calls[0]["kwargs"].get("cache_conversation", False) is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_omni_offline_prompt_ephemeral_marks_instruction_so_bp2_skips_it() -> None:
    """prompt_ephemeral tags its instruction as ephemeral so BP2 stays on real user turns."""
    from tests.unit.test_offline_turn_cancellation_e2e import _client, _text
    from utils.llm_client import ChatOpenAI, HumanMessage

    dashscope_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    recorded_requests: list[list] = []
    ephemeral_client = _client("openai")
    real_llm = ChatOpenAI(
        model="qwen3.8-flash",
        base_url=dashscope_url,
        api_key="k",
        enable_cache_control=True,
        cache_conversation=True,
    )
    try:
        ephemeral_client._conversation_history.append(HumanMessage(content="real user message"))
        orig_astream = ephemeral_client.llm.astream

        async def _inspect_astream(messages, **overrides):
            recorded_requests.append(real_llm._params(messages)["messages"])
            async for chunk in orig_astream(messages, **overrides):
                yield chunk

        ephemeral_client.llm.astream = _inspect_astream
        ephemeral_client.script = [[_text("ok"), _text("", "stop")]]
        assert await ephemeral_client.prompt_ephemeral("======[notice]======") is True
        assert len(recorded_requests) == 1
        wire = recorded_requests[0]
        assert wire[1]["content"] == [
            {"type": "text", "text": "real user message", "cache_control": {"type": "ephemeral"}}
        ]
        assert wire[2]["content"] == "======[notice]======"
    finally:
        real_llm.close()
        await ephemeral_client.close()


# ---------------------------------------------------------------------------
# 5. nekopower runtime model + usage_from_metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nekopower_runtime_provider_opts_in_and_parses_dashscope_cache_write() -> None:
    """LLMClientProvider opts in to explicit cache and parses prompt_tokens_details.cache_creation_input_tokens."""
    from nekopower.runtime.model import LLMClientProvider, usage_from_metadata
    from nekopower.runtime.protocol import ModelConfig

    provider = LLMClientProvider(
        ModelConfig(
            model="qwen3.8-flash",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="k",
        )
    )
    client = await provider._create_client()
    try:
        assert client.enable_cache_control is True
        assert client.cache_conversation is True
    finally:
        await client.aclose()

    usage = usage_from_metadata(
        {
            "prompt_tokens": 2000,
            "completion_tokens": 50,
            "total_tokens": 2050,
            "prompt_tokens_details": {
                "cached_tokens": 1200,
                "cache_creation_input_tokens": 300,
            },
        }
    )
    assert usage.cache_read == 1200
    assert usage.cache_write == 300
    assert usage.input == 500
    assert usage.output == 50


# ---------------------------------------------------------------------------
# 6. nekopower dsh forwarder
# ---------------------------------------------------------------------------


def test_dsh_forward_prepare_request_injects_dual_breakpoints_only_for_dashscope() -> None:
    """prepare_request injects dual cache_control markers on DashScope URLs and leaves others untouched."""
    from nekopower.dsh.forward import prepare_request
    from nekopower.runtime.protocol import ModelConfig

    payload = {
        "model": "slot-0",
        "messages": [
            {"role": "developer", "content": "System rules"},
            {"role": "user", "content": "Step 1"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "read", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "t1", "content": "file body"},
        ],
        "stream": True,
        "max_completion_tokens": 2048,
    }

    for url in (
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    ):
        _url, headers, body = prepare_request(
            payload,
            ModelConfig(model="qwen3.8-flash", base_url=url, api_key="k"),
        )
        assert headers["x-dashscope-session-cache"] == "enable"
        assert body["messages"][0] == {
            "role": "system",
            "content": [{"type": "text", "text": "System rules", "cache_control": {"type": "ephemeral"}}],
        }
        assert body["messages"][1] == {
            "role": "user",
            "content": [{"type": "text", "text": "Step 1", "cache_control": {"type": "ephemeral"}}],
        }
        assert body["messages"][2]["content"] == ""
        assert body["messages"][3]["content"] == "file body"

    for url in (
        "https://api.deepseek.com/v1",
        "https://api.openai.com/v1",
        "http://127.0.0.1:11434/v1",
    ):
        _url, _headers, body = prepare_request(
            payload,
            ModelConfig(model="m", base_url=url, api_key="k"),
        )
        assert body["messages"][0] == {"role": "system", "content": "System rules"}
        assert body["messages"][1] == {"role": "user", "content": "Step 1"}


# ---------------------------------------------------------------------------
# 7. Theater numeric_v2_prompt_cache + numeric_v2_actor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theater_prompt_cache_marks_transient_message_and_actor_opts_in(monkeypatch) -> None:
    """cache_ordered_messages marks transient HumanMessage as ephemeral and NumericV2Actor opts in."""
    from services.theater import numeric_v2_actor as actor_mod
    from services.theater.numeric_v2_prompt_cache import cache_ordered_messages
    from services.theater.numeric_v2_prompt_mark import theater_system_message
    from utils.llm_client import CACHE_EPHEMERAL_KEY, ChatOpenAI, HumanMessage

    ordered = cache_ordered_messages(
        [
            theater_system_message(
                "Static guide\n"
                "======以下为本轮玩家动作结果投影======\n{\"action\": \"wave\"}\n======以上为本轮玩家动作结果投影======"
            ),
            HumanMessage(content="Hello there"),
        ]
    )
    assert ordered[-1].metadata.get(CACHE_EPHEMERAL_KEY) is True

    client = ChatOpenAI(
        model="qwen3.8-flash",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="k",
        enable_cache_control=True,
        cache_conversation=True,
    )
    try:
        wire = client._params(ordered)["messages"]
        assert wire[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert wire[1]["content"][0] == {
            "type": "text",
            "text": "Hello there",
            "cache_control": {"type": "ephemeral"},
        }
        assert isinstance(wire[2]["content"], str)
    finally:
        client.close()

    class _StopAfterCreate(BaseException):
        pass

    captured: list[dict] = []

    class _DummyActorClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def _fake_create_chat_llm_async(*args, **kwargs):
        captured.append(kwargs)
        return _DummyActorClient()

    async def _stop_invoke(*_args, **_kwargs):
        raise _StopAfterCreate()

    monkeypatch.setattr(actor_mod, "_model_config", AsyncMock(return_value={
        "model": "qwen3.8-flash",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "k",
        "provider_type": "openai_compatible",
    }))
    monkeypatch.setattr(actor_mod, "_ensure_actor_messages_fit", lambda msgs, **_kw: msgs)
    monkeypatch.setattr(actor_mod, "optimized_turn_enabled", lambda: False)
    monkeypatch.setattr(actor_mod, "create_chat_llm_async", _fake_create_chat_llm_async)
    monkeypatch.setattr(actor_mod, "invoke_with_usage", _stop_invoke)

    actor = actor_mod.NumericV2Actor.__new__(actor_mod.NumericV2Actor)
    actor.config_manager = object()
    actor.provider_call_count = 0
    with pytest.raises(_StopAfterCreate):
        await actor._invoke([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])

    assert len(captured) == 1
    assert captured[0]["explicit_cache"] is True
    assert captured[0]["cache_conversation"] is True


# ---------------------------------------------------------------------------
# 8. TokenTracker cache_creation_tokens observability & backward compatibility
# ---------------------------------------------------------------------------


def test_token_tracker_records_merges_and_validates_cache_creation_tokens(tmp_path, monkeypatch) -> None:
    """TokenTracker extracts, accumulates, and stays backward-compatible with legacy stats."""
    from utils import token_tracker as tt
    from utils.token_tracker._shared import _merge_day_stats
    from utils.token_tracker.reporting import _is_valid_day_stats

    # Extraction checks nested OpenAI/DashScope details and Anthropic top-level fields.
    assert tt._extract_cache_creation_tokens({
        "prompt_tokens_details": {"cached_tokens": 500, "cache_creation_input_tokens": 250}
    }) == 250
    assert tt._extract_cache_creation_tokens({"cache_creation_input_tokens": 120}) == 120
    assert tt._extract_cache_creation_tokens({"cache_creation_input_tokens": True}) == 0
    assert tt._extract_cache_creation_tokens({}) == 0

    # Hook dispatch forwards cache_creation_tokens only when > 0.
    rec_mock = MagicMock()
    with patch.object(tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec_mock)):
        tt._record_usage_from_response(
            SimpleNamespace(
                model="qwen3.8-flash",
                usage={
                    "prompt_tokens": 1500,
                    "completion_tokens": 50,
                    "total_tokens": 1550,
                    "prompt_tokens_details": {
                        "cached_tokens": 1000,
                        "cache_creation_input_tokens": 400,
                    },
                },
            ),
            "conversation",
        )
    rec_mock.assert_called_once_with(
        model="qwen3.8-flash",
        prompt_tokens=1500,
        completion_tokens=50,
        total_tokens=1550,
        cached_tokens=1000,
        call_type="conversation",
        success=True,
        cache_creation_tokens=400,
    )

    # Full TokenTracker persistence & merging.
    monkeypatch.setattr(tt, "get_config_manager", lambda: SimpleNamespace(config_dir=tmp_path))
    tracker = tt.TokenTracker()
    atexit.unregister(tracker._atexit_save)
    tracker.record(
        "qwen3.8-flash",
        1500,
        50,
        1550,
        1000,
        "conversation",
        cache_creation_tokens=400,
    )
    tracker.record(
        "qwen3.8-flash",
        1500,
        50,
        1550,
        1400,
        "conversation",
        cache_creation_tokens=100,
    )

    today_key = date.today().isoformat()
    today_stats = tracker.get_today_stats()["stats"]
    assert today_stats["cached_tokens"] == 2400
    assert today_stats["cache_creation_tokens"] == 500
    assert today_stats["by_model"]["qwen3.8-flash"]["cache_creation_tokens"] == 500
    assert today_stats["by_call_type"]["conversation"]["cache_creation_tokens"] == 500

    all_stats = tracker.get_stats(days=1)
    assert all_stats["daily_stats"][today_key]["cache_creation_tokens"] == 500
    assert [r["ccr"] for r in all_stats["recent_records"]] == [400, 100]
    assert _is_valid_day_stats(today_stats) is True

    # Backward compatibility with legacy on-disk day/bucket dicts lacking cache_creation_tokens.
    legacy_day = {
        "total_prompt_tokens": 100,
        "total_completion_tokens": 10,
        "total_tokens": 110,
        "cached_tokens": 50,
        "total_prompt_chars": 0,
        "call_count": 1,
        "error_count": 0,
        "by_model": {
            "qwen3.8-flash": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "cached_tokens": 50,
                "prompt_chars": 0,
                "call_count": 1,
            }
        },
        "by_call_type": {
            "conversation": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "cached_tokens": 50,
                "prompt_chars": 0,
                "call_count": 1,
            }
        },
    }
    _merge_day_stats(legacy_day, today_stats)
    assert legacy_day["cache_creation_tokens"] == 500
    assert legacy_day["by_model"]["qwen3.8-flash"]["cache_creation_tokens"] == 500
    assert _is_valid_day_stats(legacy_day) is True

    bad_day = dict(today_stats, cache_creation_tokens=True)
    assert _is_valid_day_stats(bad_day) is False
